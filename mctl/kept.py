# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""nxlx-kept: the kept settings folder (J-050, D148).

With the read-only system disk on (mctl/rootfs.py) everything written after boot lives in RAM. That would lose the
PIN, the scenes, the Owner and staff devices, the uploaded graphics, the node's settings and the Wi-Fi at every
restart. The kept settings folder fixes that without giving up the protection: ONE small ext4 filesystem on its own
partition (or its own USB disk), outside the read-only root, holds a copy of the few folders that change at run time,
and each of those is bind-mounted over its normal place at boot, before any service starts:

    on the kept disk   shows up at                           what it is
    state              /var/lib/nxlx                         the panel: settings, scenes, devices, graphics, media
    player             /var/lib/nxlx-player                  the player's own state
    netd               /var/lib/nxlx-netd                    the network helper's state
    update             /var/lib/nxlx-update                  root's update backups and the clip stick's claim
    support            /var/lib/nxlx-support                 the remote support key
    wifi               /etc/NetworkManager/system-connections  the saved Wi-Fi networks (NetworkManager)

Nothing else is kept: /opt/nxlx, the units and /etc/nxlx stay on the protected disk, so a pulled plug can only ever
damage the small kept filesystem (ext4 with a journal, which replays at the next mount), never the system.

Rules (D148):

* The disk is named by its filesystem UUID in /etc/nxlx/kept.json, written by `nxlx-kept prepare` while the system
  disk is writable. A stick that only carries the same label is never mounted.
* The first boot with the disk copies each folder to it (seeding), before the services start, so nothing is writing
  meanwhile. The copy is made as root and never follows a link (the panel's account owns /var/lib/nxlx and could
  plant one), skips anything that is not a plain file or folder, keeps owners and modes, and is renamed into place
  only when it is complete and on the disk. A folder that is on the kept disk is never copied over again.
* A folder is bound only if the kept copy is a real folder and the place it covers is a real folder reached by no
  link. Anything else leaves that folder as it is, and the reason is written to /run/nxlx-kept.json.
* If the disk is missing at boot the node still starts, on the protected disk, and says so in that file. Nothing is
  made up and nothing is deleted.

Changes to the kept disk take effect at the next boot. This module never reboots.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

CONFIG = "/etc/nxlx/kept.json"
MOUNT = "/var/lib/nxlx-kept"
STATUS = "/run/nxlx-kept.json"
META = ".nxlx-kept"            # on the disk, root 0700: "volume" holds the filesystem UUID the disk was prepared with
LABEL = "nxlx-kept"
MOUNT_OPTIONS = "noatime,nodev,nosuid,errors=remount-ro"
SPARE_BYTES = 32 * 1024 * 1024  # room left over after the copy, so a full kept disk does not stop the first write
UUID_RE = re.compile(r"^[0-9A-Fa-f][0-9A-Fa-f-]{6,62}$")
DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9][A-Za-z0-9/_.-]*$")


class KeptError(Exception):
    pass


# (name on the kept disk, place it covers, make it root 0700 if it is missing)
ITEMS = (
    ("state", "/var/lib/nxlx", False),
    ("player", "/var/lib/nxlx-player", False),
    ("netd", "/var/lib/nxlx-netd", True),
    ("update", "/var/lib/nxlx-update", False),
    ("support", "/var/lib/nxlx-support", True),
    ("wifi", "/etc/NetworkManager/system-connections", False),
)

SETUP_HELP = ("Set up the kept settings disk first: make an ext4 partition (or use a USB disk) of at least 1 GB that holds "
              "nothing you need, then run: sudo nxlx-kept prepare /dev/<that partition> --yes. "
              "To turn the read-only disk on without keeping anything (every setting is lost at each restart), "
              "use: sudo nxlx-rootfs enable --without-kept.")


def _join(root, path):
    return os.path.join(root, path.lstrip("/")) if root else path


def unescape_mount(text):
    """/proc/mounts writes a space, tab, newline and backslash in a path as an octal escape."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def mount_table(proc_mounts="/proc/mounts"):
    """[(device, mount point, type)] in the order of the file; empty if it cannot be read."""
    rows = []
    try:
        with open(proc_mounts) as f:
            for line in f:
                p = line.split()
                if len(p) >= 3:
                    rows.append((unescape_mount(p[0]), unescape_mount(p[1]), p[2]))
    except OSError:
        pass
    return rows


def is_mount_point(path, proc_mounts="/proc/mounts"):
    return any(mp == path for _, mp, _ in mount_table(proc_mounts))


def tree_size(path):
    """Bytes of the plain files below `path`, links not followed (what the copy will make). 0 if it is not there."""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        total += tree_size(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _fsync_path(fd):
    try:
        os.fsync(fd)
    except OSError:
        pass


def copy_tree(src, dst, chown=None):
    """Copy the folder `src` to the new folder `dst` as root, safely.

    `src` may belong to an account that is not trusted (the panel's), so nothing in it is followed: every name is
    opened relative to its folder with O_NOFOLLOW, and only plain files and folders are copied. A link, a pipe, a
    socket or a device is left out (its path is in the returned list). Owners and modes are kept (a set-user-ID or
    set-group-ID bit on a file is not), file contents and the folders are flushed to the disk before this returns.
    `dst` must not exist. Returns the list of what was left out."""
    if chown is None:
        chown = os.geteuid() == 0
    skipped = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    sfd = os.open(src, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    try:
        st = os.fstat(sfd)
        os.mkdir(dst, 0o700)
        dfd = os.open(dst, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        try:
            _copy_dir(sfd, dfd, "", skipped, chown, nofollow)
            _finish_dir(dfd, st, chown)
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)
    return skipped


def _finish_dir(dfd, st, chown):
    if chown:
        os.fchown(dfd, st.st_uid, st.st_gid)
    os.fchmod(dfd, stat.S_IMODE(st.st_mode))
    try:
        os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
    except (OSError, NotImplementedError):
        pass
    _fsync_path(dfd)


def _copy_dir(sfd, dfd, rel, skipped, chown, nofollow):
    for name in sorted(os.listdir(sfd)):
        shown = rel + name
        try:
            st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
        except OSError:
            skipped.append(shown)
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                nsfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=sfd)
            except OSError:
                skipped.append(shown)
                continue
            try:
                os.mkdir(name, 0o700, dir_fd=dfd)
                ndfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dfd)
                try:
                    _copy_dir(nsfd, ndfd, shown + "/", skipped, chown, nofollow)
                    _finish_dir(ndfd, os.fstat(nsfd), chown)
                finally:
                    os.close(ndfd)
            finally:
                os.close(nsfd)
        elif stat.S_ISREG(st.st_mode):
            try:
                ifd = os.open(name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=sfd)
            except OSError:
                skipped.append(shown)
                continue
            try:
                ist = os.fstat(ifd)
                if not stat.S_ISREG(ist.st_mode):         # swapped for something else between the look and the open
                    skipped.append(shown)
                    continue
                ofd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=dfd)
                try:
                    while True:
                        chunk = os.read(ifd, 1 << 20)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(ofd, view):]
                    if chown:
                        os.fchown(ofd, ist.st_uid, ist.st_gid)
                    os.fchmod(ofd, stat.S_IMODE(ist.st_mode) & 0o1777)
                    try:
                        os.utime(ofd, ns=(ist.st_atime_ns, ist.st_mtime_ns))
                    except (OSError, NotImplementedError):
                        pass
                    os.fsync(ofd)
                finally:
                    os.close(ofd)
            finally:
                os.close(ifd)
        else:
            skipped.append(shown)


def _write_atomic(path, data, mode=0o644):
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL, mode)
    try:
        os.write(fd, data.encode())
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _fsync_path(dfd)
    finally:
        os.close(dfd)


class Kept:
    """The kept settings folder of one node. `root` puts every path under a folder (the tests); `runner` runs mount,
    mkfs and the like; `proc_mounts` is the table of mounts."""

    def __init__(self, root="", runner=subprocess.run, proc_mounts=None, sleep=time.sleep, log=print,
                 wait_seconds=30, items=ITEMS):
        self.root = root
        self.runner = runner
        self.proc_mounts = proc_mounts or _join(root, "/proc/mounts")
        self.sleep = sleep
        self.log = log
        self.wait_seconds = wait_seconds
        self.items = items
        self.config_path = _join(root, CONFIG)
        self.mount_dir = _join(root, MOUNT)
        self.status_path = _join(root, STATUS)

    # ---- configuration ----------------------------------------------------------------------------------------

    def read_config(self):
        """The saved set-up as a dict ({"uuid": ...}), or None if there is none. Raises KeptError if it is unusable."""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            raise KeptError("%s cannot be read (%s)" % (CONFIG, e))
        uuid = cfg.get("uuid") if isinstance(cfg, dict) else None
        if not isinstance(uuid, str) or not UUID_RE.match(uuid):
            raise KeptError("%s does not hold a valid disk UUID" % CONFIG)
        return {"uuid": uuid}

    def configured(self):
        try:
            return self.read_config() is not None
        except KeptError:
            return True          # something is there, and it is wrong: the caller must not treat that as "no kept disk"

    def _run(self, cmd):
        return self.runner(cmd, capture_output=True, text=True)

    def device_for(self, uuid, wait=0):
        """The device node of the filesystem with this UUID, waiting up to `wait` seconds for a slow USB disk."""
        link = _join(self.root, "/dev/disk/by-uuid/" + uuid)
        waited = 0
        while True:
            if os.path.exists(link):
                return os.path.realpath(link)
            if waited >= wait:
                return None
            self.sleep(1)
            waited += 1

    def _blkid(self, dev, tag):
        r = self._run(["blkid", "-p", "-s", tag, "-o", "value", dev])
        return (r.stdout or "").strip() if r.returncode == 0 else ""

    # ---- the volume -----------------------------------------------------------------------------------------

    def _mount_volume(self, dev):
        """Mount the kept disk at MOUNT (nothing to do if it is). One check and repair of the filesystem if the first
        mount fails, as after a pulled plug that the journal could not settle."""
        if is_mount_point(self.mount_dir, self.proc_mounts):
            return
        self._ensure_mount_dir()
        cmd = ["mount", "-t", "ext4", "-o", MOUNT_OPTIONS, dev, self.mount_dir]
        r = self._run(cmd)
        if r.returncode != 0:
            self.log("nxlx-kept: the first mount failed (%s); checking the filesystem" % (r.stderr or r.stdout or "").strip())
            self._run(["e2fsck", "-p", dev])
            r = self._run(cmd)
            if r.returncode != 0:
                raise KeptError("the kept disk %s could not be mounted: %s" % (dev, (r.stderr or r.stdout or "").strip()))

    def _ensure_mount_dir(self):
        try:
            st = os.lstat(self.mount_dir)
        except FileNotFoundError:
            os.makedirs(self.mount_dir, mode=0o700)
            return
        if not stat.S_ISDIR(st.st_mode):
            raise KeptError("%s is a link or a file, not a folder" % MOUNT)

    def _check_marker(self, uuid):
        marker = os.path.join(self.mount_dir, META, "volume")
        try:
            with open(marker) as f:
                if f.read().strip() != uuid:
                    raise KeptError("the kept disk was not prepared by nxlx-kept prepare (its marker does not match)")
        except OSError:
            raise KeptError("the kept disk was not prepared by nxlx-kept prepare (no marker)")

    # ---- boot ----------------------------------------------------------------------------------------------

    def boot(self):
        """What the boot unit runs: mount the disk, seed what is not on it yet, bind every folder. Never raises;
        returns 0 if every folder is where it should be (or nothing is set up), 1 if anything was left as it was."""
        try:
            cfg = self.read_config()
        except KeptError as e:
            self._status("refused", str(e), {})
            self.log("nxlx-kept: %s" % e)
            return 1
        if cfg is None:
            self._status("off", "no kept settings disk is set up", {})
            return 0
        items = {}
        try:
            dev = self.device_for(cfg["uuid"], self.wait_seconds)
            if dev is None:
                raise KeptError("the kept disk (UUID %s) is not there; the node runs on the protected disk and "
                                "settings changed since this boot are lost at the next restart" % cfg["uuid"])
            self._mount_volume(dev)
            self._check_marker(cfg["uuid"])
        except KeptError as e:
            self._status("missing", str(e), items)
            self.log("nxlx-kept: %s" % e)
            return 1
        except OSError as e:
            self._status("missing", "the kept disk could not be used: %s" % e, items)
            self.log("nxlx-kept: the kept disk could not be used: %s" % e)
            return 1
        for name, place, create in self.items:
            try:
                items[name] = self._bind(name, place, create)
            except KeptError as e:
                items[name] = "left as it is: %s" % e
                self.log("nxlx-kept: %s: %s" % (name, e))
            except OSError as e:
                items[name] = "left as it is: %s" % e
                self.log("nxlx-kept: %s: %s" % (name, e))
        bad = [n for n, v in items.items() if v.startswith("left as it is")]
        state = "partial" if bad else "active"
        self._status(state, "", items, device=dev)
        return 1 if bad else 0

    def _bind(self, name, place, create):
        target = _join(self.root, place)
        kept = os.path.join(self.mount_dir, name)
        if is_mount_point(target, self.proc_mounts):
            return "bound"
        have_kept = os.path.lexists(kept)
        if have_kept:
            st = os.lstat(kept)
            if not stat.S_ISDIR(st.st_mode):
                raise KeptError("the kept copy %s is a link or a file" % kept)
        try:
            tst = os.lstat(target)
        except FileNotFoundError:
            tst = None
        if tst is None:
            if not (create or have_kept):
                return "not on this node"
            # A folder that systemd or a service makes at its first start: made here, root only, so it can be kept from
            # the first write on (the service changes its owner when it starts).
            if os.path.realpath(os.path.dirname(target)) != os.path.dirname(target):
                raise KeptError("the folder above %s is reached through a link" % place)
            os.mkdir(target, 0o700)
            tst = os.lstat(target)
        if not stat.S_ISDIR(tst.st_mode):
            raise KeptError("%s is a link or a file, not a folder" % place)
        if os.path.realpath(target) != target:
            raise KeptError("%s is reached through a link" % place)
        seeded = False
        if not have_kept:
            self._seed(name, target)
            seeded = True
        r = self._run(["mount", "--bind", kept, target])
        if r.returncode != 0:
            raise KeptError("bind mount failed: %s" % (r.stderr or r.stdout or "").strip())
        return "seeded and bound" if seeded else "bound"

    def _seed(self, name, target):
        """Copy `target` to the kept disk under `name`: into a temporary folder first, renamed into place only when it
        is complete and flushed, so a pulled plug leaves either nothing or all of it."""
        want = tree_size(target) + SPARE_BYTES
        try:
            vfs = os.statvfs(self.mount_dir)
            free = vfs.f_bavail * vfs.f_frsize
        except OSError:
            free = None
        if free is not None and free < want:
            raise KeptError("not enough room on the kept disk to keep it (%d MB free, %d MB needed)" % (free // 2**20, want // 2**20))
        final = os.path.join(self.mount_dir, name)
        tmp = final + ".new"
        if os.path.lexists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            skipped = copy_tree(target, tmp)
            dfd = os.open(self.mount_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.rename(tmp, final)
                _fsync_path(dfd)
            finally:
                os.close(dfd)
        except (OSError, KeptError):
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        for s in skipped:
            self.log("nxlx-kept: %s: left out %s (not a plain file or folder)" % (name, s))
        self.log("nxlx-kept: %s: copied to the kept disk" % name)

    def _status(self, state, message, items, device=None):
        data = {"state": state, "message": message, "items": items, "device": device}
        try:
            os.makedirs(os.path.dirname(self.status_path), exist_ok=True)
            _write_atomic(self.status_path, json.dumps(data, sort_keys=True) + "\n")
        except OSError:
            pass

    def sync(self):
        """The boot unit's stop: flush the kept disk before the machine goes down."""
        self._run(["sync"])
        return 0

    # ---- what `nxlx-rootfs enable` asks ---------------------------------------------------------------------

    def check_for_enable(self, media_dir=None):
        """Raise KeptError (with the words to show) unless turning the read-only disk on now would keep the node's
        settings. Returns True if `media_dir` will be on the kept disk too (so it does not count as on the system disk).
        Checks only: nothing is copied or mounted, the copy happens at the next boot, before any service starts."""
        try:
            cfg = self.read_config()
        except KeptError as e:
            raise KeptError("%s %s" % (e, SETUP_HELP))
        if cfg is None:
            raise KeptError("No kept settings disk is set up, so the PIN, scenes, devices, graphics, settings and Wi-Fi "
                            "would be lost at every restart. " + SETUP_HELP)
        dev = self.device_for(cfg["uuid"])
        if dev is None:
            raise KeptError("The kept settings disk (UUID %s) is not connected. Plug it in, or run nxlx-kept prepare again." % cfg["uuid"])
        fstype = self._blkid(dev, "TYPE")
        if fstype != "ext4":
            raise KeptError("The kept settings disk %s is not ext4 (it says %r)." % (dev, fstype))
        if not os.path.isdir(_join(self.root, "/var/lib/nxlx")):
            raise KeptError("/var/lib/nxlx is missing: install the node first (a node on the old layout converts when it is updated).")
        need = SPARE_BYTES
        covered = False
        for _, place, _create in self.items:
            p = _join(self.root, place)
            if os.path.isdir(p) and not os.path.islink(p):
                need += tree_size(p)
            if media_dir and (os.path.abspath(media_dir) + "/").startswith(place.rstrip("/") + "/"):
                covered = True
        free = self._free_bytes(dev)
        if free is not None and free < need:
            raise KeptError("The kept settings disk is too small: %d MB are free and the folders to keep take %d MB "
                            "(media included when it lives in /var/lib/nxlx)." % (free // 2**20, need // 2**20))
        return covered

    def _free_bytes(self, dev):
        """Free bytes on the kept disk, from where it is mounted, or from a short look-only mount. None if unknown."""
        if is_mount_point(self.mount_dir, self.proc_mounts):
            try:
                vfs = os.statvfs(self.mount_dir)
                return vfs.f_bavail * vfs.f_frsize
            except OSError:
                return None
        r = self._run(["blkid", "-p", "-s", "FSSIZE", "-o", "value", dev])
        # Before the disk is first mounted the size is the size of the filesystem; the free part is the same but for the
        # few MB of the journal and tables, so it is a fair, slightly generous answer.
        try:
            return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
        except ValueError:
            return None

    # ---- prepare ---------------------------------------------------------------------------------------------

    def prepare(self, device, yes=False, wipe=False, overlay_active=False):
        """Make `device` the kept settings disk: an ext4 filesystem labelled nxlx-kept, a marker on it, and the UUID
        saved in /etc/nxlx/kept.json. DESTROYS what is on the device."""
        if not DEVICE_RE.match(device) or ".." in device:
            raise KeptError("%r is not a device path such as /dev/sda3" % device)
        if overlay_active:
            raise KeptError("The system disk is read-only right now, so the set-up would be lost at the next restart. "
                            "Turn it off first: sudo nxlx-rootfs disable, restart, then run this again.")
        if not yes:
            raise KeptError("This erases everything on %s. Run it again with --yes if that is the right device." % device)
        real = os.path.realpath(_join(self.root, device))
        try:
            if not stat.S_ISBLK(os.stat(real).st_mode) and not self.root:
                raise KeptError("%s is not a block device" % device)
        except OSError:
            raise KeptError("%s does not exist" % device)
        for dev, mp, _t in mount_table(self.proc_mounts):
            # also a whole disk whose partition is mounted, and any name that only starts the same: refusing too much is safe
            if dev.startswith("/") and os.path.realpath(dev).startswith(real):
                raise KeptError("%s (or a part of it) is in use, mounted at %s. Nothing was changed." % (device, mp))
        existing = self._blkid(real, "TYPE")
        if existing and not wipe:
            raise KeptError("%s already holds a %s filesystem. If it really is to be erased, add --wipe. Nothing was changed." % (device, existing))
        r = self._run(["mkfs.ext4", "-q", "-F", "-m", "1", "-L", LABEL, real])
        if r.returncode != 0:
            raise KeptError("mkfs.ext4 failed: %s" % (r.stderr or r.stdout or "").strip())
        uuid = self._blkid(real, "UUID")
        if not UUID_RE.match(uuid or ""):
            raise KeptError("could not read the new filesystem's UUID")
        self._ensure_mount_dir()
        if is_mount_point(self.mount_dir, self.proc_mounts):
            raise KeptError("%s is already a mount point; restart and try again" % MOUNT)
        r = self._run(["mount", "-t", "ext4", "-o", MOUNT_OPTIONS, real, self.mount_dir])
        if r.returncode != 0:
            raise KeptError("could not mount the new filesystem: %s" % (r.stderr or r.stdout or "").strip())
        try:
            meta = os.path.join(self.mount_dir, META)
            os.makedirs(meta, mode=0o700, exist_ok=True)
            os.chmod(self.mount_dir, 0o700)
            _write_atomic(os.path.join(meta, "volume"), uuid + "\n", 0o600)
            self._run(["sync"])
        finally:
            self._run(["umount", self.mount_dir])
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        _write_atomic(self.config_path, json.dumps({"uuid": uuid, "label": LABEL}) + "\n", 0o644)
        self.log("The kept settings disk is ready (UUID %s). Turn the read-only disk on with: sudo nxlx-rootfs enable. "
                 "The first restart after that copies the settings to it." % uuid)
        return uuid

    # ---- status ------------------------------------------------------------------------------------------------

    def status(self):
        """What is set up and what the last boot did, for `nxlx-kept status` and `nxlx-rootfs status`."""
        out = {"configured": False, "uuid": None, "disk_present": None, "boot": None}
        try:
            cfg = self.read_config()
        except KeptError as e:
            out["configured"] = True
            out["error"] = str(e)
            return out
        if cfg is None:
            return out
        out.update(configured=True, uuid=cfg["uuid"], disk_present=self.device_for(cfg["uuid"]) is not None)
        try:
            with open(self.status_path) as f:
                out["boot"] = json.load(f)
        except (OSError, ValueError):
            pass
        return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="nxlx-kept", description="The kept settings folder: keeps the node's settings across restarts when the system disk is read-only.")
    ap.add_argument("action", choices=["status", "prepare", "mount", "sync"])
    ap.add_argument("device", nargs="?")
    ap.add_argument("--yes", action="store_true", help="prepare: yes, erase the device")
    ap.add_argument("--wipe", action="store_true", help="prepare: the device holds a filesystem and it may be erased")
    args = ap.parse_args(argv)
    from . import rootfs
    k = Kept()
    try:
        if args.action == "status":
            print(json.dumps(k.status(), indent=2))
            return 0
        if os.geteuid() != 0:
            raise KeptError("run as root (sudo)")
        if args.action == "mount":
            return k.boot()
        if args.action == "sync":
            return k.sync()
        if not args.device:
            raise KeptError("say which device: sudo nxlx-kept prepare /dev/<partition> --yes")
        k.prepare(args.device, yes=args.yes, wipe=args.wipe, overlay_active=rootfs.current_state() == "overlay")
    except KeptError as e:
        print("nxlx-kept: %s" % e, file=sys.stderr)
        return 1
    return 0
