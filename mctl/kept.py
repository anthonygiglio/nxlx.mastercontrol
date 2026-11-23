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
  disk is writable. A stick that only carries the same label is never mounted, and neither is one with the right UUID
  but no secret: `prepare` makes a random secret, keeps it in /etc/nxlx/kept.secret (root only) and on the disk, and a
  disk is accepted at boot only if its copy matches (a constant-time compare; the file is opened without following a
  link and read with a size cap, so a link to /dev/zero cannot hang the boot).
* The copying is not done at boot. `nxlx-kept prepare` (an admin, with the node up) stops the nxlx services, copies each
  folder to the disk (seeding), flushes the disk once, empties the system disk's copy of what grants access (below),
  binds the folders and starts the services again. `nxlx-kept seed` does the same for a folder the disk does not hold
  yet. The copy is made as root and never follows a link (the panel's account owns /var/lib/nxlx and could plant
  one), skips anything that is not a plain file or folder, keeps owners and modes, and is renamed into place only
  when it is complete. A hard link is copied as a separate file. A link whose target is relative and has no ".."
  stays a link; any other link is left out and said.
* The boot unit only mounts and binds, and never copies. A folder is bound only if the kept copy is a real folder
  and the place it covers is a real folder reached by no link. A folder the disk does not hold is left on the system
  disk and the reason is written to /run/nxlx-kept.json. The boot step never raises: whatever goes wrong with one
  folder is written down and the next one is tried.
* If the disk is missing at boot the node still starts, on the system disk's copies, and says "missing" in that file.
  Those copies are old and could bring back a device that was removed since, so seeding empties them of what grants
  access: the paired devices and the Owner recovery codes in settings.json (and its backups), the switch that
  accepts devices from other nodes (fleet.accept), the signed fleet list and the acceptances by fingerprint
  (fleet.json, fleet.sig, pairings.json), the settings copies in the update folder and the remote support key. Only
  deny.json stays: it can only refuse. This happens after the kept disk is committed (its secret and config are
  written), never before, and before the folders are bound (a bind hides the system disk's copy). A fallback boot
  therefore needs fresh pairing with the PIN; the rest (scenes, graphics, settings, Wi-Fi) is kept so the room runs.
  Nothing else is made up and nothing is deleted.
* `prepare` and `seed` stop the running (or starting) nxlx services and always start them again, also on SIGTERM,
  SIGHUP and SIGINT; they refuse while an update or a USB copy runs and while a conversion's undo is armed. The boot
  step is installed as a copy in /usr/local/lib/nxlx (bin/nxlx-kept-boot and this file), not run from a release, so a
  rollback cannot take it away.

Changes to the kept disk take effect at the next boot. This module never reboots.
"""

import contextlib
import ctypes
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

CONFIG = "/etc/nxlx/kept.json"
SECRET = "/etc/nxlx/kept.secret"   # root 0600: the same secret as on the disk; a disk without it is not this node's
MOUNT = "/var/lib/nxlx-kept"
STATUS = "/run/nxlx-kept.json"
META = ".nxlx-kept"            # on the disk, root 0700: "volume" holds the filesystem UUID, "secret" the secret of prepare
SECRET_LIMIT = 256             # bytes read of either secret file
SETTINGS_LIMIT = 16 * 1024 * 1024
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware")
LABEL = "nxlx-kept"
MOUNT_OPTIONS = "noatime,nodev,nosuid,errors=remount-ro"
SPARE_BYTES = 32 * 1024 * 1024  # room left over after the copy, so a full kept disk does not stop the first write
UUID_RE = re.compile(r"^[0-9A-Fa-f][0-9A-Fa-f-]{6,62}$")
DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9][A-Za-z0-9/_.-]*$")
# The nxlx services that write the kept folders, in start order: prepare and seed stop the running ones while a folder is
# copied (nothing may write meanwhile) and start them again.
SERVICES = ("nxlx-netd.service", "nxlx-sysd.service", "nxlx-supportd.service", "nxlx-player.service", "nxlx-web.service")
# Units that write /var/lib/nxlx-update or the media: prepare does not stop them, it refuses while one runs.
BUSY_UNITS = ("nxlx-update.service", "nxlx-update-usb@*.service", "nxlx-update-inbox@*.service", "nxlx-usb@*.service")
# Files in the panel's folder that let another node, or the Owners it carries, in (mctl/handover.py): the signed fleet
# list and its signature, and the acceptances by fingerprint. deny.json is kept: it only restricts.
GRANT_FILES = ("fleet.json", "fleet.sig", "pairings.json")
UNDO_TIMER = "/etc/systemd/system/nxlx-convert-undo.timer"
SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")


# Said in /run/nxlx-kept.json whenever the node falls back to the system disk's copies of the folders.
FALLBACK = (". The node runs on the protected disk's older copies, which hold no paired devices, Owner recovery codes or "
            "remote support key (they were taken out when the folders were copied to the kept disk): pair again with the "
            "PIN. Settings changed since this boot are lost at the next restart")


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
              "nothing you need, then run: sudo nxlx-kept prepare /dev/<that partition> --yes "
              "(it stops the panel and the player for a short while and copies the settings, graphics and media to it). "
              "To turn the read-only disk on without keeping anything (every setting is lost at each restart), "
              "use: sudo nxlx-rootfs enable --without-kept.")


def _safe(text):
    """Text that any log or terminal can take: a name that is not UTF-8 (the panel's account can make one) shows its
    bytes as backslash escapes instead of raising."""
    return str(text).encode("utf-8", "backslashreplace").decode("utf-8")


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


def keepable_link(target):
    """True for a link target that may be made again on the kept copy: relative and made only of plain names (no
    "..", so it cannot leave its folder whatever the other links are)."""
    if not isinstance(target, str) or not target or target.startswith("/") or "\0" in target:
        return False
    return all(part not in ("", "..") for part in target.split("/"))


def reached_by_link(root, place, path):
    """True if `path` (inside `place`) is, or is reached through, a link: a media folder that is a link is not on the
    kept disk, whatever its name says."""
    base = _join(root, place).rstrip("/")
    full = _join(root, os.path.abspath(path))
    if not (full + "/").startswith(base + "/"):
        return True
    cur = base
    for part in [p for p in full[len(base):].split("/") if p]:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return os.path.islink(base)


def copy_tree(src, dst, chown=None):
    """Copy the folder `src` to the new folder `dst` as root, safely.

    `src` may belong to an account that is not trusted (the panel's), so nothing in it is followed: every name is
    opened relative to its folder with O_NOFOLLOW, and only plain files and folders are copied. A link is made again
    only if its target is relative and has no ".." (see keepable_link); every other link, a pipe, a socket or a device
    is left out (its path is in the returned list). A hard link becomes a separate file for each name. Owners and
    modes are kept (a set-user-ID or set-group-ID bit on a file is not). Nothing is flushed here: the caller flushes
    the whole disk once (syncfs) when everything is copied. `dst` must not exist. Returns the list of what was left out."""
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
                finally:
                    os.close(ofd)
            finally:
                os.close(ifd)
        elif stat.S_ISLNK(st.st_mode):
            try:
                target = os.readlink(name, dir_fd=sfd)
                if not keepable_link(target):
                    skipped.append(shown)
                    continue
                os.symlink(target, name, dir_fd=dfd)
                if chown:
                    os.chown(name, st.st_uid, st.st_gid, dir_fd=dfd, follow_symlinks=False)
            except OSError:
                skipped.append(shown)
        else:
            skipped.append(shown)


def _fsync_path(fd):
    try:
        os.fsync(fd)
    except OSError:
        pass


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


def read_small(name, dir_fd=None, limit=SECRET_LIMIT, private=False):
    """The bytes (stripped) of a small plain file, opened without following a link and without waiting for a pipe, so a
    link to /dev/zero or a FIFO planted on the disk cannot hang the boot. Anything that is not a regular file, or is
    longer than `limit`, raises KeptError. `private`: the file must be the caller's own and closed to everyone else."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise KeptError("%s is not a plain file" % name)
        if private and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            raise KeptError("%s is not private to root" % name)
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise KeptError("%s is too long" % name)
    return data.strip()


class Kept:
    """The kept settings folder of one node. `root` puts every path under a folder (the tests); `runner` runs mount,
    mkfs and the like; `proc_mounts` is the table of mounts."""

    def __init__(self, root="", runner=subprocess.run, proc_mounts=None, sleep=time.sleep, log=print,
                 wait_seconds=30, items=ITEMS):
        self.root = root
        self.runner = runner
        self.proc_mounts = proc_mounts or _join(root, "/proc/mounts")
        self.sleep = sleep
        self.log = lambda message: log(_safe(message))
        self.wait_seconds = wait_seconds
        self.items = items
        self.config_path = _join(root, CONFIG)
        self.secret_path = _join(root, SECRET)
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
        """The value of a tag, or "" if there is none or blkid failed. For looking only (enable, the UUID after mkfs);
        `_probe` is the one `prepare` trusts before it writes anything."""
        r = self._run(["blkid", "-p", "-s", tag, "-o", "value", dev])
        return (r.stdout or "").strip() if r.returncode == 0 else ""

    def _probe(self, dev, tag):
        """The value of a tag of what is on `dev`, "" if blkid found none (exit 2). Any other failure is an error: a
        device that cannot be read is not a blank device."""
        try:
            r = self._run(["blkid", "-p", "-s", tag, "-o", "value", dev])
        except OSError as e:
            raise KeptError("blkid could not look at %s (%s), so it is not known to be blank. Nothing was changed." % (dev, e))
        if r.returncode == 0:
            return (r.stdout or "").strip()
        if r.returncode == 2 and not (r.stdout or "").strip():
            return ""
        raise KeptError("blkid could not look at %s (exit %s: %s), so it is not known to be blank. Nothing was changed."
                        % (dev, r.returncode, (r.stderr or "").strip()))

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
        """The disk is this node's only if its marker names the UUID and its secret equals the one in /etc/nxlx. The disk
        is whatever was plugged in, so its files are opened without following a link and read with a size cap."""
        wrong = "the kept disk was not prepared by this node's nxlx-kept prepare (%s)"
        try:
            mfd = os.open(os.path.join(self.mount_dir, META), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise KeptError(wrong % "no marker")
        try:
            try:
                volume = read_small("volume", mfd)
                theirs = read_small("secret", mfd)
            except OSError:
                raise KeptError(wrong % "no marker")
        finally:
            os.close(mfd)
        if volume != uuid.encode():
            raise KeptError(wrong % "its marker does not match")
        try:
            mine = read_small(self.secret_path, private=True)
        except OSError as e:
            raise KeptError("%s cannot be read (%s), so no kept disk can be trusted" % (SECRET, e))
        if not mine or not hmac.compare_digest(theirs, mine):
            raise KeptError(wrong % "its secret does not match")

    # ---- boot ----------------------------------------------------------------------------------------------

    def boot(self):
        """What the boot unit runs: mount the disk and bind every folder the disk holds. It copies nothing. Never raises
        and always writes /run/nxlx-kept.json; returns 0 if every folder is where it should be (or nothing is set up),
        1 if anything was left as it was."""
        try:
            return self._boot()
        except Exception as e:      # noqa: BLE001 - the boot must go on whatever happened here
            try:
                self._status("refused", "the kept settings step failed: %s" % e, {})
                self.log("nxlx-kept: it failed: %r" % (e,))
            except Exception:       # noqa: BLE001
                pass
            return 1

    def _boot(self):
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
                raise KeptError("the kept disk (UUID %s) is not there" % cfg["uuid"])
            self._mount_volume(dev)
            self._check_marker(cfg["uuid"])
        except (KeptError, OSError) as e:
            what = str(e) if isinstance(e, KeptError) else "the kept disk could not be used: %s" % e
            message = what + FALLBACK
            self._status("missing", message, items)
            self.log("nxlx-kept: %s" % message)
            return 1
        for name, place, create in self.items:
            try:
                items[name] = self._bind(name, place, create)
            except Exception as e:      # noqa: BLE001 - one folder's trouble never stops the next
                items[name] = "left as it is: %s" % (e,)
                self.log("nxlx-kept: %s: %s" % (name, e))
        bad = [n for n, v in items.items() if v.startswith("left as it is")]
        state = "partial" if bad else "active"
        self._status(state, "", items, device=dev)
        return 1 if bad else 0

    def _check_place(self, place):
        """What covers `place` now: its lstat, or None if it is not there. Raises KeptError if it is not a real folder
        or is reached through a link."""
        target = _join(self.root, place)
        try:
            tst = os.lstat(target)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(tst.st_mode):
            raise KeptError("%s is a link or a file, not a folder" % place)
        if os.path.realpath(target) != target:
            raise KeptError("%s is reached through a link" % place)
        return tst

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
        tst = self._check_place(place)
        if not have_kept:
            if tst is None:
                return "not on this node"
            # Never copied here: that is for `nxlx-kept prepare` / `seed`, with the services stopped. The folder stays
            # where it is, and the node says so.
            raise KeptError("the kept disk holds no copy of it, so it stays on the system disk (run: sudo nxlx-kept seed)")
        if tst is None:
            # A folder that systemd or a service makes at its first start: made here, root only, so it can be kept from
            # the first write on (the service changes its owner when it starts).
            if os.path.realpath(os.path.dirname(target)) != os.path.dirname(target):
                raise KeptError("the folder above %s is reached through a link" % place)
            os.mkdir(target, 0o700)
        r = self._run(["mount", "--bind", kept, target])
        if r.returncode != 0:
            raise KeptError("bind mount failed: %s" % (r.stderr or r.stdout or "").strip())
        return "bound"

    # ---- seeding (prepare and seed, never boot) ---------------------------------------------------------------

    def _syncfs(self, path):
        """Flush the whole filesystem under `path` once (syncfs); sync() everything if that is not available."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                if ctypes.CDLL(None, use_errno=True).syncfs(fd) == 0:
                    return
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            pass
        os.sync()

    def _stop_services(self, active):
        """Stop the running nxlx services (also one that is still starting). `active` is filled as it goes, so a signal in
        the middle still leaves the caller knowing what to start again."""
        running = []
        for unit in SERVICES:
            r = self._run(["systemctl", "is-active", unit])
            if r.returncode == 0 or (r.stdout or "").strip() in ("activating", "reloading"):
                running.append(unit)
        for unit in reversed(running):
            active.insert(0, unit)
            self._run(["systemctl", "stop", unit])
        return active

    def _start_services(self, active):
        self._ignore_signals()          # nothing may cut the clean-up short
        for unit in active:
            self._run(["systemctl", "start", unit])

    def _busy_units(self):
        """Update and USB units that are running (or starting) now: they write the folders being copied."""
        r = self._run(["systemctl", "list-units", "--plain", "--no-legend", "--state=active,activating,reloading"] + list(BUSY_UNITS))
        if r.returncode != 0:
            return []
        return [line.split()[0] for line in (r.stdout or "").splitlines() if line.split()]

    @staticmethod
    def _ignore_signals():
        for name in SIGNALS:
            try:
                signal.signal(getattr(signal, name), signal.SIG_IGN)
            except (ValueError, OSError):
                pass

    @contextlib.contextmanager
    def _interruptible(self):
        """While `prepare` or `seed` runs, a SIGTERM, SIGHUP (the ssh session ends) or SIGINT becomes a KeptError, so the
        clean-up runs and the services that were stopped are started again. Over ssh, run it under systemd-run --scope or
        nohup anyway."""
        old = {}

        def handler(signum, _frame):
            self._ignore_signals()
            raise KeptError("interrupted by %s; the services are being started again" % signal.Signals(signum).name)
        try:
            for name in SIGNALS:
                old[name] = signal.signal(getattr(signal, name), handler)
        except ValueError:              # not the main thread: no handlers, the caller's own rules apply
            old = {}
        try:
            yield
        finally:
            for name, previous in old.items():
                signal.signal(getattr(signal, name), previous)

    def _release_old(self):
        """--wipe over a kept disk that is in use: unbind the six places and unmount the old disk, then say plainly what that
        means. The settings on the old disk are not copied; the node goes back to the system disk's older copies."""
        for _name, place, _create in reversed(self.items):
            target = _join(self.root, place)
            if is_mount_point(target, self.proc_mounts):
                r = self._run(["umount", target])
                if r.returncode != 0:
                    raise KeptError("could not unmount %s (%s): something still uses it. Nothing was formatted." % (place, (r.stderr or r.stdout or "").strip()))
        if is_mount_point(self.mount_dir, self.proc_mounts):
            r = self._run(["umount", self.mount_dir])
            if r.returncode != 0:
                raise KeptError("could not unmount the old kept disk (%s). Nothing was formatted." % (r.stderr or r.stdout or "").strip())
        self.log("nxlx-kept: the old kept disk was unbound and unmounted. What was on it is NOT copied to the new one: the node "
                 "goes back to the system disk's older copies, which hold no paired devices or Owner recovery codes, so pair "
                 "again with the PIN.")

    def _seed_folder(self, name, target):
        """Copy `target` to the kept disk under `name`: into a temporary folder first, renamed into place when it is
        complete: copy to .new, flush the disk, rename, flush again, so a power cut leaves either nothing or all of it,
        and a folder under its final name is always whole (a later seed skips a folder that exists)."""
        want = tree_size(target) + SPARE_BYTES
        try:
            vfs = os.statvfs(self.mount_dir)
            free = vfs.f_bavail * vfs.f_frsize
        except OSError:
            free = None
        if free is not None and free < want:
            raise KeptError("not enough room on the kept disk to keep %s (%d MB free, %d MB needed)" % (name, free // 2**20, want // 2**20))
        final = os.path.join(self.mount_dir, name)
        tmp = final + ".new"
        if os.path.lexists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            skipped = copy_tree(target, tmp)
            self._syncfs(self.mount_dir)
            os.rename(tmp, final)
            self._syncfs(self.mount_dir)
        except (OSError, KeptError):
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        for s in skipped:
            self.log("nxlx-kept: %s: left out %s (a link that is not relative to its own folder, or not a plain file or folder)" % (name, s))
        self.log("nxlx-kept: %s: copied to the kept disk" % name)

    def _seed_missing(self):
        """Copy every folder the disk does not hold yet (each flushed to the disk before and after its rename, so a power cut
        never leaves a partial folder under its final name). The caller has the services stopped and the volume mounted.
        Returns ({name: what was done}, [(name, target) to scrub later]). If any folder fails, the ones copied in this call
        are removed again and nothing on the system disk was touched."""
        done, scrub, result = [], [], {}
        try:
            for name, place, create in self.items:
                kept = os.path.join(self.mount_dir, name)
                if os.path.lexists(kept):
                    result[name] = "already on the kept disk"
                    continue
                target = _join(self.root, place)
                tst = self._check_place(place)
                if tst is None:
                    if not create:
                        result[name] = "not on this node"
                        continue
                    if os.path.realpath(os.path.dirname(target)) != os.path.dirname(target):
                        raise KeptError("the folder above %s is reached through a link" % place)
                    os.mkdir(target, 0o700)
                self._seed_folder(name, target)
                done.append(name)
                scrub.append((name, target))
                result[name] = "seeded"
        except BaseException:
            for name in done:
                shutil.rmtree(os.path.join(self.mount_dir, name), ignore_errors=True)
            raise
        return result, scrub

    def _scrub_all(self, result, scrub):
        """Empty the system disk's copies of what grants access. Only after the kept disk is committed (its config and
        secret written), so a failure before that leaves the system disk as the live, complete copy."""
        for name, target in scrub:
            try:
                self._scrub(name, target)
            except Exception as e:      # noqa: BLE001 - said, and the rest is still done
                self.log("nxlx-kept: %s: could not empty the system disk's copy of what grants access: %s" % (name, e))
                result[name] += " (the system disk's old copy was NOT emptied: %s)" % e

    # What a fallback boot must not bring back (D148): see _scrub.
    _SETTINGS_FILES = re.compile(r"^(settings\.json\..*|\.settings-.*)$")
    _UPDATE_COPIES = re.compile(r"^settings-.*\.json$")

    def _scrub(self, name, target):
        """Empty the system disk's copy of `target` of what grants access, so a boot that falls back to it (the kept disk is
        missing) cannot bring back a device that was removed since the copy: in the panel's folder the devices and the
        Owner recovery codes inside settings.json, the copies of it (.bak, rolled-back, before-import), pairings.json; in
        the update folder the settings copies; in the support folder the private key. Everything else stays."""
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        try:
            for entry in sorted(os.listdir(fd)):
                if name == "state":
                    if entry == "settings.json":
                        self._scrub_settings(fd)
                    elif entry in GRANT_FILES or self._SETTINGS_FILES.match(entry):
                        self._unlink_in(fd, entry, name)
                elif name == "update" and self._UPDATE_COPIES.match(entry):
                    self._unlink_in(fd, entry, name)
                elif name == "support" and (entry.startswith("private.key") or entry.endswith(".pub")):
                    self._unlink_in(fd, entry, name)
        finally:
            os.close(fd)

    def _unlink_in(self, dfd, entry, name):
        try:
            os.unlink(entry, dir_fd=dfd)
        except IsADirectoryError:
            self.log("nxlx-kept: %s: %s is a folder and stays" % (name, entry))

    def _scrub_settings(self, dfd):
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            data = json.loads(read_small("settings.json", dfd, limit=SETTINGS_LIMIT).decode("utf-8"))
        except (OSError, ValueError, KeptError):
            data = None
        if not isinstance(data, dict):
            os.unlink("settings.json", dir_fd=dfd)        # not readable as settings: nothing of it is worth bringing back
            return
        st = os.stat("settings.json", dir_fd=dfd, follow_symlinks=False)
        data["devices"] = []
        data.pop("recovery", None)
        fleet = data.get("fleet")
        if isinstance(fleet, dict):
            fleet["accept"] = False          # the switch that lets another node's carried devices in (handover.py)
        tmp = ".settings-scrub-%d" % os.getpid()
        try:
            os.unlink(tmp, dir_fd=dfd)
        except FileNotFoundError:
            pass
        ofd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=dfd)
        try:
            os.write(ofd, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
            if os.geteuid() == 0:
                os.fchown(ofd, st.st_uid, st.st_gid)
            os.fchmod(ofd, stat.S_IMODE(st.st_mode))
        finally:
            os.close(ofd)
        os.rename(tmp, "settings.json", src_dir_fd=dfd, dst_dir_fd=dfd)

    def _status(self, state, message, items, device=None):
        data = {"state": state, "message": message, "items": items, "device": device}
        try:
            os.makedirs(os.path.dirname(self.status_path), exist_ok=True)
            _write_atomic(self.status_path, json.dumps(data, sort_keys=True) + "\n")
        except Exception:           # noqa: BLE001 - the status is for people; it never stops the boot
            pass

    def sync(self):
        """The boot unit's stop: flush the kept disk before the machine goes down."""
        self._run(["sync"])
        return 0

    # ---- what `nxlx-rootfs enable` asks ---------------------------------------------------------------------

    def check_for_enable(self, media_dir=None):
        """Raise KeptError (with the words to show) unless turning the read-only disk on now would keep the node's
        settings. Returns True if `media_dir` will be on the kept disk too (so it does not count as on the system disk):
        it must lie inside a kept folder and be reached by no link. Checks only: nothing is copied or mounted."""
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
            if media_dir and (os.path.abspath(media_dir) + "/").startswith(place.rstrip("/") + "/") \
                    and not reached_by_link(self.root, place, media_dir):
                covered = True
        free = self._free_bytes(dev)
        if free is not None and free < need:
            raise KeptError("The kept settings disk is too small: %d MB are free and the folders to keep take %d MB "
                            "(media included when it lives in /var/lib/nxlx)." % (free // 2**20, need // 2**20))
        return covered

    def _free_bytes(self, dev):
        """Free bytes on the kept disk, from where it is mounted, or from the filesystem's size. None if unknown."""
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

    # ---- prepare and seed --------------------------------------------------------------------------------------

    def _sys_block(self, real):
        return _join(self.root, "/sys/class/block/" + os.path.basename(real))

    def _partitions_of(self, real):
        """Names of the partitions of a whole disk, from sysfs; [] if it has none, is a partition itself or is unknown."""
        base = self._sys_block(real)
        try:
            return sorted(n for n in os.listdir(base) if os.path.exists(os.path.join(base, n, "partition")))
        except OSError:
            return []

    def _system_devnos(self):
        """The device numbers of the file systems that hold / and /boot (what must never be formatted)."""
        out = set()
        if self.root:
            return out
        for mp in SYSTEM_MOUNTS:
            try:
                out.add(os.stat(mp).st_dev)
            except OSError:
                pass
        return out

    def _devnos_of(self, real):
        """The device numbers of `real` and of its partitions."""
        out = set()
        try:
            out.add(os.stat(real).st_rdev)
        except OSError:
            pass
        base = self._sys_block(real)
        for part in self._partitions_of(real):
            try:
                with open(os.path.join(base, part, "dev")) as f:
                    major, minor = f.read().strip().split(":")
                out.add(os.makedev(int(major), int(minor)))
            except (OSError, ValueError):
                pass
        return out

    def _preflight(self, device, yes, wipe, overlay_active, need_config):
        """Every check `prepare` and `seed` make before anything is written. Returns the real device path (None for seed)."""
        if overlay_active:
            raise KeptError("The system disk is read-only right now, so the set-up would be lost at the next restart. "
                            "Turn it off first: sudo nxlx-rootfs disable, restart, then run this again.")
        if os.path.lexists(_join(self.root, UNDO_TIMER)):
            raise KeptError("A conversion of this node waits for its confirm (its undo is armed): confirm the conversion first "
                            "(sudo nxlx-convert --confirm), then run this again. Nothing was changed.")
        busy = self._busy_units()
        if busy:
            raise KeptError("An update or a USB copy is running (%s). Wait until it is done, then run this again. Nothing was changed."
                            % ", ".join(busy))
        for _name, place, _create in self.items:
            self._check_place(place)
        if not need_config:
            return None, False
        if not DEVICE_RE.match(device) or ".." in device:
            raise KeptError("%r is not a device path such as /dev/sda3" % device)
        if not yes:
            raise KeptError("This erases everything on %s. Run it again with --yes if that is the right device." % device)
        real = os.path.realpath(_join(self.root, device))
        try:
            if not stat.S_ISBLK(os.stat(real).st_mode) and not self.root:
                raise KeptError("%s is not a block device" % device)
        except OSError:
            raise KeptError("%s does not exist" % device)
        table = mount_table(self.proc_mounts)
        # --wipe over a kept disk that is in use replaces it: its own mounts do not count as "in use" then
        replacing = wipe and os.path.lexists(self.config_path) and is_mount_point(self.mount_dir, self.proc_mounts)
        ours = ({self.mount_dir} | {_join(self.root, place) for _n, place, _c in self.items}) if replacing else set()
        for dev, mp, _t in table:
            if mp in ours:
                continue
            # also a whole disk whose partition is mounted, and any name that only starts the same: refusing too much is safe
            if dev.startswith("/") and os.path.realpath(dev).startswith(real):
                raise KeptError("%s (or a part of it) is in use, mounted at %s. Nothing was changed." % (device, mp))
        if self._devnos_of(real) & self._system_devnos():
            raise KeptError("%s is the disk or the partition that holds the system (/ or /boot). It is never formatted. Nothing was changed." % device)
        try:
            st = os.lstat(self.mount_dir)
            if not stat.S_ISDIR(st.st_mode):
                raise KeptError("%s is a link or a file, not a folder. Nothing was changed." % MOUNT)
        except FileNotFoundError:
            pass
        if os.path.lexists(self.config_path) and not wipe:
            raise KeptError("A kept settings disk is already set up (%s). To replace it, add --wipe: the old disk is then unbound "
                            "and unmounted and its settings are NOT copied to the new one (the node goes back to the system "
                            "disk's older copies and its devices must be paired again). Nothing was changed." % CONFIG)
        if is_mount_point(self.mount_dir, self.proc_mounts) and not replacing:
            raise KeptError("%s is already a mount point (a kept disk is in use); restart and try again. Nothing was changed." % MOUNT)
        existing = self._probe(real, "TYPE")
        if existing and not wipe:
            raise KeptError("%s already holds a %s filesystem. If it really is to be erased, add --wipe. Nothing was changed." % (device, existing))
        table_type = self._probe(real, "PTTYPE")
        parts = self._partitions_of(real)
        if (table_type or parts) and not wipe:
            raise KeptError("%s is a whole disk with a partition table (%s). Use one of its partitions (/dev/%s), or add --wipe "
                            "if the whole disk really is to be erased. Nothing was changed."
                            % (device, table_type or "partitions: " + ", ".join(parts), parts[0] if parts else "<partition>"))
        return real, replacing

    def prepare(self, device, yes=False, wipe=False, overlay_active=False):
        """Make `device` the kept settings disk: an ext4 filesystem labelled nxlx-kept, a marker and a random secret on it
        (the secret also in /etc/nxlx/kept.secret), the node's folders copied to it, the UUID saved in
        /etc/nxlx/kept.json. DESTROYS what is on the device. The nxlx services are stopped while the folders are copied
        and started again, with the folders now on the kept disk."""
        real, replacing = self._preflight(device, yes, wipe, overlay_active, True)
        secret = secrets.token_hex(32)
        active = []
        mounted = committed = False
        with self._interruptible():
            try:
                self._stop_services(active)
                if replacing:
                    self._release_old()
                r = self._run(["mkfs.ext4", "-q", "-F", "-m", "1", "-L", LABEL, real])
                if r.returncode != 0:
                    raise KeptError("mkfs.ext4 failed: %s" % (r.stderr or r.stdout or "").strip())
                uuid = self._blkid(real, "UUID")
                if not UUID_RE.match(uuid or ""):
                    raise KeptError("could not read the new filesystem's UUID")
                self._ensure_mount_dir()
                r = self._run(["mount", "-t", "ext4", "-o", MOUNT_OPTIONS, real, self.mount_dir])
                if r.returncode != 0:
                    raise KeptError("could not mount the new filesystem: %s" % (r.stderr or r.stdout or "").strip())
                mounted = True
                meta = os.path.join(self.mount_dir, META)
                os.makedirs(meta, mode=0o700, exist_ok=True)
                os.chmod(self.mount_dir, 0o700)
                _write_atomic(os.path.join(meta, "volume"), uuid + "\n", 0o600)
                _write_atomic(os.path.join(meta, "secret"), secret + "\n", 0o600)
                result, scrub = self._seed_missing()
                # The commit: from here the kept disk is the node's. Only after it is written is anything taken out of the
                # system disk's copies, so a failure before it leaves the system disk the complete, live copy.
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
                _write_atomic(self.secret_path, secret + "\n", 0o600)
                _write_atomic(self.config_path, json.dumps({"uuid": uuid, "label": LABEL}) + "\n", 0o644)
                committed = True
                self._scrub_all(result, scrub)      # before the bind: a bind hides the system disk's copy from here on
                self._bind_all()
            except BaseException:
                if mounted and not committed:
                    self._run(["umount", self.mount_dir])
                raise
            finally:
                self._start_services(active)
        self.log("The kept settings disk is ready (UUID %s) and in use now: %s. Turn the read-only disk on with: "
                 "sudo nxlx-rootfs enable." % (uuid, ", ".join("%s %s" % (n, v) for n, v in result.items())))
        return uuid

    def seed(self, overlay_active=False):
        """Copy the folders the kept disk does not hold yet (a folder a later release keeps, or one that was not there when
        the disk was prepared), as `prepare` does, and bind them. Needs the disk to be set up and present."""
        self._preflight(None, True, False, overlay_active, False)
        cfg = self.read_config()
        if cfg is None:
            raise KeptError("No kept settings disk is set up. " + SETUP_HELP)
        dev = self.device_for(cfg["uuid"])
        if dev is None:
            raise KeptError("The kept settings disk (UUID %s) is not connected." % cfg["uuid"])
        self._mount_volume(dev)
        self._check_marker(cfg["uuid"])
        active = []
        with self._interruptible():
            try:
                self._stop_services(active)
                result, scrub = self._seed_missing()
                self._scrub_all(result, scrub)      # the disk is already committed (its config and secret exist)
                self._bind_all()
            finally:
                self._start_services(active)
        for n, v in result.items():
            self.log("nxlx-kept: %s: %s" % (n, v))
        return result

    def _bind_all(self):
        """Bind every folder the disk holds that is not bound yet. Each is tried; the failures are raised together."""
        failed = []
        for name, place, create in self.items:
            try:
                self._bind(name, place, create)
            except Exception as e:      # noqa: BLE001 - every folder is tried; each failure is reported
                failed.append("%s: %s" % (name, e))
                self.log("nxlx-kept: %s: %s" % (name, e))
        if failed:
            raise KeptError("not every folder could be bound (%s). The settings are on the kept disk; restart to bind them, "
                            "or look at the cause first." % "; ".join(failed))

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
    ap.add_argument("action", choices=["status", "prepare", "seed", "mount", "sync"])
    ap.add_argument("device", nargs="?")
    ap.add_argument("--yes", action="store_true", help="prepare: yes, erase the device")
    ap.add_argument("--wipe", action="store_true", help="prepare: the device holds a filesystem, a partition table or an earlier kept disk and it may be erased")
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
        overlay = rootfs.current_state() == "overlay"
        if args.action == "seed":
            k.seed(overlay_active=overlay)
            return 0
        if not args.device:
            raise KeptError("say which device: sudo nxlx-kept prepare /dev/<partition> --yes")
        k.prepare(args.device, yes=args.yes, wipe=args.wipe, overlay_active=overlay)
    except KeptError as e:
        print("nxlx-kept: %s" % _safe(e), file=sys.stderr)
        return 1
    except OSError as e:
        print("nxlx-kept: %s" % _safe(e), file=sys.stderr)
        return 1
    return 0
