# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""USB drive automount by label, called from a udev-triggered systemd unit.

Replaces mountusb.sh (which mounted sda1 and sda2 on the same folder) and the
hardcoded /dev/sda mount in the old backend, which on an x86 box is the system
disk. Rules of this module:

* every USB partition gets its own folder under /media/nxlx/<label>
* /media/usb points at the most recently mounted one (what old presets expect)
* mounted read-only unless NXLX_USB_RW=1, with nosuid,nodev,noexec, because a
  drive yanked mid-write at a gig is the usual way to corrupt it
* the one exception is the node's own clip stick (mctl/clipstick.py, D143): an
  exFAT partition labelled Nxx-CLIPS that root claimed on this node (the marker on
  the stick carries the secret of the node's root-only claim record). It is checked
  with fsck.exfat -p, mounted in a folder only root can reach, and only its clips
  folder is bound at /media/nxlx-clips for the panel; if the check fails, that
  binding is read-only too
* never touch a disk that also holds the running system
"""

import fcntl
import grp
import os
import re
import subprocess
import sys
import tempfile
import time

from . import clipstick, paths

DEVNODE = re.compile(r"^/dev/sd[a-z]{1,2}[0-9]{0,2}$")
ALLOWED_FS = {"vfat", "exfat", "ext2", "ext3", "ext4", "ntfs"}
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/home", "/var", "/usr", "/etc")
DEFAULT_BASE = "/media/nxlx"
DEFAULT_LINK = "/media/usb"
DEFAULT_STATE = "/var/lib/nxlx"
LOCK_SECONDS = 900          # a claim from a terminal waits this long for the lock (another stick's check is at most 600 s)
# A clip stick being mounted by its unit waits at most this long, so that this wait, the check (clipstick.FSCK_SECONDS)
# and the mounts stay well inside the unit's TimeoutStartSec (15 min); a test holds that sum.
MOUNT_LOCK_SECONDS = 60
_ismount = os.path.ismount


class UsbError(Exception):
    pass


def sanitize_label(label, fallback):
    """Safe folder name: a-z 0-9 . _ - only, no leading dot, at most 32 chars."""
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", label or "")[:32].lstrip(".")
    return clean or fallback


def parent_disk(devnode):
    return re.sub(r"[0-9]+$", "", devnode)


def backing_disks(paths=SYSTEM_MOUNTS, sysfs="/sys/dev/block", stat=os.stat):
    """Disks that hold the running system, found from device numbers, so a root shown as
    /dev/root (or an overlay) in /proc/mounts cannot hide the system disk."""
    disks = set()
    for path in paths:
        try:
            dev = stat(path).st_dev
        except OSError:
            continue
        name = os.path.basename(os.path.realpath("%s/%d:%d" % (sysfs, os.major(dev), os.minor(dev))))
        if re.match(r"^sd[a-z]+[0-9]*$", name):
            disks.add(parent_disk("/dev/" + name))
    return disks


def read_mounts(path="/proc/mounts"):
    mounts = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                # /proc/mounts escapes spaces as \040
                mounts.append((parts[0], parts[1].replace("\\040", " ")))
    return mounts


def mount_options(fstype, rw, gid, errors_ro=False):
    opts = ["rw" if rw else "ro", "nosuid", "nodev", "noexec", "noatime"]
    if fstype in ("vfat", "exfat", "ntfs"):
        opts += ["uid=0", "umask=0002" if gid is not None else "umask=0022"]
        if gid is not None:
            opts.append("gid=%d" % gid)
    if fstype == "vfat":
        opts.append("utf8")
    if errors_ro:
        # The kernel's exfat driver takes errors=continue|panic|remount-ro (fs/exfat/super.c). A driver that does not
        # is caught by the mount failing, and the mount is tried once more without it (see _mount_clip_stick).
        opts.append("errors=remount-ro")
    return ",".join(opts)


def probe(devnode, runner=subprocess.run):
    """Filesystem type, label and uuid from blkid."""
    try:
        r = runner(["blkid", "-o", "export", devnode], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise UsbError("blkid did not answer for %s within 15 s" % devnode)
    if r.returncode != 0:
        raise UsbError("no filesystem found on %s" % devnode)
    info = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v
    return info


def _gid():
    try:
        return grp.getgrnam("nxlx-svc").gr_gid
    except KeyError:
        return None


def _free_mountpoint(base, name, mounted_points):
    candidate, n = os.path.join(base, name), 2
    while candidate in mounted_points or (os.path.isdir(candidate) and os.listdir(candidate)):
        candidate = os.path.join(base, "%s-%d" % (name, n))
        n += 1
    return candidate


def _check_device(devnode, mounts, system_disks):
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r (not a USB disk device node)" % devnode)
    disk = parent_disk(devnode)
    if disk in (backing_disks() if system_disks is None else system_disks):
        raise UsbError("refusing %s: %s holds the running system" % (devnode, disk))
    for dev, point in mounts:
        if parent_disk(dev) == disk and point in SYSTEM_MOUNTS:
            raise UsbError("refusing %s: %s holds the running system (%s)" % (devnode, disk, point))


# --- where the clip stick's private things live -------------------------------------------------------------------------
def _run_dir(env):
    return env.get("NXLX_USB_RUN", paths.USB_DIR)


def _status_path(env):
    return env.get("NXLX_CLIP_STATUS", os.path.join(_run_dir(env), "clip-stick.json"))


def _clip_dir(env):
    return env.get("NXLX_CLIP_DIR", clipstick.DEFAULT_CLIP_DIR)


def _private(env, name):
    """A folder under the run folder that only root can enter (peek/, mnt/)."""
    path = os.path.join(_run_dir(env), name)
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


class _Lock:
    """flock on <run>/clip.lock: one claim or clip stick mount at a time, with a deadline."""

    def __init__(self, env, seconds=None):
        self.path = os.path.join(_run_dir(env), "clip.lock")
        self.seconds = float(env.get("NXLX_CLIP_LOCK_SECONDS", LOCK_SECONDS)) if seconds is None else seconds
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), mode=0o755, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        end = time.monotonic() + self.seconds
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= end:
                    os.close(self.fd)
                    self.fd = None
                    raise UsbError("another clip stick mount or claim is still running")
                time.sleep(0.2)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)          # closing releases the lock
            self.fd = None


def _run(runner, cmd, timeout, log):
    """A mount or umount; a time-out is a failure (returncode None), never an exception."""
    try:
        return runner(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("%s did not finish in %d s" % (" ".join(cmd[:1] + cmd[-1:]), timeout))
        return subprocess.CompletedProcess(cmd, None, "", "timed out")


def _ok(r):
    return r.returncode == 0


def _umount(runner, point, log, lazy=False):
    """Unmount; True only for a clean unmount. A plain unmount that fails is detached lazily (so the folder can go),
    and still counts as a failure: something held the stick."""
    r = _run(runner, ["umount"] + (["-l"] if lazy else []) + [point], 60, log)
    if not _ok(r) and not lazy:
        log("could not unmount %s (%s); detaching it" % (point, (r.stderr or r.stdout).strip()))
        _run(runner, ["umount", "-l", point], 30, log)
    return _ok(r)


def _peek_marker(devnode, env, runner, log):
    """Mount the partition read-only at a folder of this call's own, read its marker, unmount. Returns (marker, ok):
    ok is False when the look could not be finished cleanly, and then the stick is mounted as any other stick."""
    try:
        peek = tempfile.mkdtemp(prefix=os.path.basename(devnode) + ".", dir=_private(env, "peek"))
    except OSError as e:
        log("could not make a folder to look at %s: %s" % (devnode, e))
        return None, False
    r = _run(runner, ["mount", "-t", "exfat", "-o", mount_options("exfat", False, None), devnode, peek], 60, log)
    if not _ok(r):
        log("could not look for a clip stick marker on %s: %s" % (devnode, (r.stderr or r.stdout).strip()))
        _umount(runner, peek, log, lazy=True)            # a mount that timed out may still have happened
        _rmdir(peek)
        return None, False
    try:
        marker = clipstick.read_marker(peek)
    finally:
        done = _umount(runner, peek, log)
        _rmdir(peek)
    if not done:
        log("the look at %s could not be unmounted cleanly; treating it as any other stick" % devnode)
        return None, False
    return marker, True


def _rmdir(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


def _mount(runner, driver, opts, devnode, point, log=print):
    return _run(runner, ["mount", "-t", driver, "-o", opts, devnode, point], 60, log)


def _bind(runner, src, dst, writable, log):
    """Bind `src` at `dst` with its own nosuid,nodev,noexec (and ro unless `writable`)."""
    r = _run(runner, ["mount", "--bind", src, dst], 60, log)
    if not _ok(r):
        return r
    flags = "remount,bind,%s,nosuid,nodev,noexec" % ("rw" if writable else "ro")
    r2 = _run(runner, ["mount", "-o", flags, dst], 60, log)
    if not _ok(r2):
        _umount(runner, dst, log, lazy=True)
    return r2


def mount(devnode, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print, system_disks=None):
    env = os.environ if env is None else env
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r (not a USB disk device node)" % devnode)
    mounts = read_mounts(proc_mounts)
    if any(dev == devnode for dev, _ in mounts):
        log("%s is already mounted" % devnode)
        return None
    _check_device(devnode, mounts, system_disks)
    info = probe(devnode, runner)
    if info.get("TYPE", "") not in ALLOWED_FS:
        raise UsbError("unsupported filesystem %r on %s" % (info.get("TYPE", ""), devnode))
    if _claimed_here(info, env):
        # The label and volume of this node's claim: its marker holds the claim's secret, so this stick is never
        # mounted where others can read it. It is the clip stick, or it is not mounted at all.
        with _Lock(env, float(env.get("NXLX_CLIP_LOCK_SECONDS", MOUNT_LOCK_SECONDS))):
            point = _mount_clip_stick(devnode, info, env, runner, log)
        if point is None:
            raise UsbError("%s has the label and volume of this node's clip stick but did not pass as it; it is not "
                           "mounted (look at the lines above; plug it in again, or claim it again)" % info.get("LABEL"))
        return point
    return _mount_plain(devnode, info, env, runner, proc_mounts, log)


def _claimed_here(info, env):
    """True for an exFAT partition with the label and volume serial of this node's claim record."""
    if not clipstick.candidate(info):
        return False
    state = env.get("NXLX_STATE_DIR", DEFAULT_STATE)
    return clipstick.record_matches(clipstick.read_record(state), info.get("LABEL"), info.get("UUID", ""))


def _mount_plain(devnode, info, env, runner, proc_mounts, log, force_ro=False):
    """Every stick but the node's own clip stick, as before: /media/nxlx/<label>, read-only unless NXLX_USB_RW=1."""
    base = env.get("NXLX_USB_BASE", DEFAULT_BASE)
    link = env.get("NXLX_USB_LINK", DEFAULT_LINK)
    rw = env.get("NXLX_USB_RW", "0") == "1" and not force_ro
    fstype = info.get("TYPE", "")
    name = sanitize_label(info.get("LABEL"), os.path.basename(devnode))
    os.makedirs(base, mode=0o755, exist_ok=True)
    point = _free_mountpoint(base, name, {p for _, p in read_mounts(proc_mounts)})
    os.makedirs(point, mode=0o755, exist_ok=True)
    driver = "ntfs3" if fstype == "ntfs" else fstype
    r = _mount(runner, driver, mount_options(fstype, rw, _gid()), devnode, point, log)
    if not _ok(r):
        _rmdir(point)
        raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
    _point_link(link, point, log)
    log("mounted %s (%s, %s) at %s" % (devnode, fstype, "rw" if rw else "ro", point))
    return point


def _is_own(devnode, info, env, runner, log):
    """(record, marker_ok): the node's claim record for this partition and whether the stick's marker carries its
    secret. No record, a record for another label or volume, or a marker without the secret: not the node's."""
    state = env.get("NXLX_STATE_DIR", DEFAULT_STATE)
    node_id = clipstick.own_node_id(state)
    record = clipstick.read_record(state)
    label, uuid = info.get("LABEL"), info.get("UUID", "")
    if node_id is None or not clipstick.record_matches(record, label, uuid):
        return None, False
    marker, done = _peek_marker(devnode, env, runner, log)
    if not done:
        return None, False
    if not clipstick.marker_matches(marker, node_id, label, record, uuid):
        log("%s has no marker with this node's claim on it" % label)
        return None, False
    return record, True


def _mount_clip_stick(devnode, info, env, runner, log):
    """The node's own clip stick, under the lock. Returns the clips folder (or the private folder of a stick without
    one), or None when it did not pass as the clip stick; then nothing of it is left mounted."""
    clip_dir = _clip_dir(env)
    if _ismount(clip_dir):
        log("another clip stick is already at %s; %s is not mounted" % (clip_dir, devnode))
        return None
    record, own = _is_own(devnode, info, env, runner, log)
    if not own:
        return None
    state = env.get("NXLX_STATE_DIR", DEFAULT_STATE)
    node_id, label, uuid = clipstick.own_node_id(state), info.get("LABEL"), info.get("UUID", "")
    status_path = _status_path(env)
    status = {"device": devnode, "label": label, "state": "checking", "why": ""}
    priv = os.path.join(_private(env, "mnt"), os.path.basename(devnode))
    mounted = bound = done = False
    try:
        clipstick.write_status(status_path, status)              # the panel says "checking" while fsck runs
        ok, why = clipstick.fsck(devnode, runner, log)           # before the writable mount, never after
        if not ok:
            log("clip stick %s: %s; mounting it read-only" % (label, why))
        os.makedirs(priv, mode=0o700, exist_ok=True)
        gid = _gid()
        if ok:
            r = _mount(runner, "exfat", mount_options("exfat", True, gid, errors_ro=True), devnode, priv, log)
            if not _ok(r):
                log("the exfat driver refused errors=remount-ro (%s); mounting the clip stick without it"
                    % (r.stderr or r.stdout).strip())
                r = _mount(runner, "exfat", mount_options("exfat", True, gid), devnode, priv, log)
        else:
            r = _mount(runner, "exfat", mount_options("exfat", False, gid), devnode, priv, log)
        if not _ok(r):
            _umount(runner, priv, log, lazy=True)
            raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
        mounted = True
        # The stick could have been swapped between the look and this mount: the marker is read again here.
        if not clipstick.marker_matches(clipstick.read_marker(priv), node_id, label, record, uuid):
            log("%s changed between the check and the mount" % label)
            return None
        writable = ok
        if writable:
            try:
                clipstick.ensure_clip_folder(priv)
            except (OSError, clipstick.ClipStickError) as e:
                log("clip stick %s: could not make the clips folder (%s)" % (label, e))
                writable, why = False, "no clips folder"
        if not clipstick.has_clip_folder(priv):
            status.update(state="no-clips", why=why or "no clips folder")
            clipstick.write_status(status_path, status)
            log("mounted %s (%s) without a clips folder; the library stays on the SD card" % (devnode, label))
            done = True
            return priv
        os.makedirs(clip_dir, mode=0o755, exist_ok=True)
        r = _bind(runner, os.path.join(priv, clipstick.CLIP_FOLDER), clip_dir, writable, log)
        if not _ok(r):
            raise UsbError("could not bind the clips folder: %s" % (r.stderr or r.stdout).strip())
        bound = True
        status.update(state="in" if writable else "read-only", why=why)
        clipstick.write_status(status_path, status)
        log("mounted %s (%s, the node's clip stick, %s) with its clips folder at %s"
            % (devnode, label, "rw" if writable else "ro", clip_dir))
        done = True
        return clip_dir
    finally:
        if not done:                                             # whatever stopped it, nothing of it stays up
            if bound:
                _run(runner, ["umount", "-l", clip_dir], 30, log)
            if mounted:
                _umount(runner, priv, log)
            _rmdir(priv)
            clipstick.clear_status(status_path, devnode)


def _point_link(link, point, log):
    if os.path.lexists(link) and not os.path.islink(link):
        log("%s is a real folder; not replacing it with a link" % link)
        return
    tmp = link + ".tmp"
    try:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(point, tmp)
        os.replace(tmp, link)
    except OSError as e:
        log("could not update %s: %s" % (link, e))


def unmount(devnode, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print):
    env = os.environ if env is None else env
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r" % devnode)
    link = env.get("NXLX_USB_LINK", DEFAULT_LINK)
    base = env.get("NXLX_USB_BASE", DEFAULT_BASE)
    clip_dir = _clip_dir(env).rstrip("/")
    mnt = os.path.join(_run_dir(env), "mnt")
    clipstick.clear_status(_status_path(env), devnode)          # the library goes back to the SD first
    points = [p for dev, p in read_mounts(proc_mounts) if dev == devnode]
    # The clips binding first (it sits on the private mount), then the private mount, then the ordinary folders.
    order = sorted(points, key=lambda p: 0 if p.rstrip("/") == clip_dir else 1 if os.path.dirname(p.rstrip("/")) == mnt else 2)
    for point in order:
        parent = os.path.dirname(point.rstrip("/"))
        if point.rstrip("/") != clip_dir and parent not in (base.rstrip("/"), mnt):
            log("not unmounting %s: outside %s" % (point, base))
            continue
        _run(runner, ["umount", "-l", point], 30, log)            # lazy: the device may be gone
        if point.rstrip("/") != clip_dir:
            _rmdir(point)
        if os.path.islink(link) and os.readlink(link) == point:
            os.unlink(link)
        log("unmounted %s from %s" % (devnode, point))
    return points


def forget_clip_stick(env=None, proc_mounts="/proc/mounts"):
    """Remove this node's claim. Refused while the clip stick is mounted: unplug it first (its unit then unmounts it)."""
    env = os.environ if env is None else env
    mnt = os.path.join(_run_dir(env), "mnt")
    busy = _ismount(_clip_dir(env)) or clipstick.read_status(_status_path(env)) is not None or any(
        os.path.dirname(p.rstrip("/")) == mnt for _, p in read_mounts(proc_mounts))
    if busy:
        raise UsbError("the clip stick is still in; unplug it, then run this again")
    return clipstick.forget_claim(env.get("NXLX_STATE_DIR", DEFAULT_STATE))


def claim(devnode, force=False, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print,
          system_disks=None, node_name=None):
    """Make the stick at `devnode` this node's clip stick (root only, from a terminal: `sudo nxlx-usb claim DEV`).

    The partition must be exFAT and labelled Nxx-CLIPS. It is unmounted if mounted, checked with fsck.exfat -p (which
    must pass), mounted writable at a private folder, given the marker with a new secret and a clips folder, unmounted,
    and then mounted the normal way. The same secret goes into the node's root-only claim record. A stick whose marker
    names another node is refused unless `force`."""
    env = os.environ if env is None else env
    state = env.get("NXLX_STATE_DIR", DEFAULT_STATE)
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r (not a USB disk device node)" % devnode)
    _check_device(devnode, read_mounts(proc_mounts), system_disks)
    info = probe(devnode, runner)
    if not clipstick.candidate(info):
        raise UsbError("%s is %s labelled %r; a clip stick is exFAT labelled like N01-CLIPS"
                       % (devnode, info.get("TYPE") or "unformatted", info.get("LABEL", "")))
    label = info["LABEL"]
    node_id = clipstick.own_node_id(state)
    if node_id is None:
        raise UsbError("this node has no id in %s yet; start the panel once (nxlx-web), then claim the stick" % state)
    with _Lock(env):
        if any(dev == devnode for dev, _ in read_mounts(proc_mounts)):
            unmount(devnode, env=env, runner=runner, proc_mounts=proc_mounts, log=log)
            if any(dev == devnode for dev, _ in read_mounts(proc_mounts)):
                raise UsbError("%s is still mounted; unmount it first" % devnode)
        ok, why = clipstick.fsck(devnode, runner, log)
        if not ok:
            raise UsbError("the stick did not pass its check (%s); repair it on a computer first" % why)
        peek = tempfile.mkdtemp(prefix=os.path.basename(devnode) + ".", dir=_private(env, "peek"))
        r = _mount(runner, "exfat", mount_options("exfat", True, _gid()), devnode, peek, log)
        if not _ok(r):
            _umount(runner, peek, log, lazy=True)
            _rmdir(peek)
            raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
        token = clipstick.new_token()
        try:
            old = clipstick.read_marker(peek)
            if old is not None and isinstance(old.get("node_id"), str) and old.get("node_id") != node_id and not force:
                raise UsbError("this stick is claimed by another node (%s); add --force to take it over"
                               % (old.get("node_name") or old.get("node_id")))
            if node_name is None:
                import socket
                node_name = socket.gethostname().split(".")[0]
            clipstick.write_marker(peek, clipstick.make_marker(node_id, node_name, label, token))
            clipstick.ensure_clip_folder(peek)
        except clipstick.ClipStickError as e:
            raise UsbError(str(e))
        finally:
            done = _umount(runner, peek, log)
            _rmdir(peek)
        if not done:
            raise UsbError("the stick could not be unmounted after the claim; pull it, plug it in and claim it again")
        clipstick.write_claim(state, label, info.get("UUID", ""), token)
        log("claimed %s (%s) as this node's clip stick" % (devnode, label))
        point = _mount_clip_stick(devnode, info, env, runner, log)
    if point is None:
        raise UsbError("the claimed stick did not mount as the clip stick; look at the log")
    return point


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 2 and argv[0] == "mount":
            mount(argv[1])
        elif len(argv) == 2 and argv[0] == "unmount":
            unmount(argv[1])
        elif len(argv) in (2, 3) and argv[0] == "claim" and (len(argv) == 2 or argv[2] == "--force"):
            claim(argv[1], force=len(argv) == 3)
        elif argv == ["forget-clip-stick"]:
            gone = forget_clip_stick()
            print("this node no longer expects a clip stick" if gone else "this node had no clip stick")
        elif argv == ["list"]:
            base = os.environ.get("NXLX_USB_BASE", DEFAULT_BASE)
            for dev, point in read_mounts():
                if os.path.dirname(point.rstrip("/")) == base.rstrip("/"):
                    print(dev, point)
        else:
            print("usage: nxlx-usb mount DEV | unmount DEV | list | claim DEV [--force] | forget-clip-stick", file=sys.stderr)
            return 2
    except (UsbError, OSError) as e:
        print("nxlx-usb: %s" % e, file=sys.stderr)
        return 1
    return 0
