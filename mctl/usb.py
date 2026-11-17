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
  exFAT partition labelled Nxx-CLIPS whose marker names this node. It is checked
  with fsck.exfat -p and then mounted writable; if the check fails it is mounted
  read-only like any other stick
* never touch a disk that also holds the running system
"""

import grp
import os
import re
import subprocess
import sys

from . import clipstick, paths

DEVNODE = re.compile(r"^/dev/sd[a-z]{1,2}[0-9]{0,2}$")
ALLOWED_FS = {"vfat", "exfat", "ext2", "ext3", "ext4", "ntfs"}
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/home", "/var", "/usr", "/etc")
DEFAULT_BASE = "/media/nxlx"
DEFAULT_LINK = "/media/usb"
DEFAULT_STATE = "/var/lib/nxlx"
PEEK_DIR = os.path.join(paths.USB_DIR, "peek")


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
    r = runner(["blkid", "-o", "export", devnode], capture_output=True, text=True, timeout=15)
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


def _peek_marker(devnode, info, env, runner, log):
    """Mount the partition read-only at a private folder, read its marker, unmount. None when it cannot be read.
    Read-only, so the stick is not written before fsck has looked at it."""
    peek = os.path.join(env.get("NXLX_USB_PEEK", PEEK_DIR), os.path.basename(devnode))
    try:
        os.makedirs(peek, mode=0o700, exist_ok=True)
    except OSError as e:
        log("could not make %s: %s" % (peek, e))
        return None
    r = runner(["mount", "-t", "exfat", "-o", mount_options("exfat", False, None), devnode, peek],
               capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log("could not look for a clip stick marker on %s: %s" % (devnode, (r.stderr or r.stdout).strip()))
        try:
            os.rmdir(peek)
        except OSError:
            pass
        return None
    try:
        return clipstick.read_marker(peek)
    finally:
        runner(["umount", peek], capture_output=True, text=True, timeout=30)
        try:
            os.rmdir(peek)
        except OSError:
            pass


def _mount(runner, driver, opts, devnode, point):
    return runner(["mount", "-t", driver, "-o", opts, devnode, point], capture_output=True, text=True, timeout=60)


def mount(devnode, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print, system_disks=None):
    env = os.environ if env is None else env
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r (not a USB disk device node)" % devnode)
    base = env.get("NXLX_USB_BASE", DEFAULT_BASE)
    link = env.get("NXLX_USB_LINK", DEFAULT_LINK)
    rw = env.get("NXLX_USB_RW", "0") == "1"

    mounts = read_mounts(proc_mounts)
    if any(dev == devnode for dev, _ in mounts):
        log("%s is already mounted" % devnode)
        return None
    _check_device(devnode, mounts, system_disks)

    info = probe(devnode, runner)
    fstype = info.get("TYPE", "")
    if fstype not in ALLOWED_FS:
        raise UsbError("unsupported filesystem %r on %s" % (fstype, devnode))

    # The node's own clip stick? Only an exFAT Nxx-CLIPS partition whose marker names this node (never the serial).
    own, why_ro = False, ""
    if clipstick.candidate(info):
        node_id = clipstick.own_node_id(env.get("NXLX_STATE_DIR", DEFAULT_STATE))
        marker = _peek_marker(devnode, info, env, runner, log) if node_id else None
        if clipstick.marker_matches(marker, node_id, info.get("LABEL")):
            own = True
            ok, why_ro = clipstick.fsck(devnode, runner, log)       # before the writable mount, never after
            if not ok:
                log("clip stick %s: %s; mounting it read-only" % (info.get("LABEL"), why_ro))
        elif marker is not None:
            log("%s is a clip stick of another node; mounting it read-only" % info.get("LABEL"))
    writable = own and not why_ro

    name = sanitize_label(info.get("LABEL"), os.path.basename(devnode))
    os.makedirs(base, mode=0o755, exist_ok=True)
    point = _free_mountpoint(base, name, {p for _, p in mounts})
    os.makedirs(point, mode=0o755, exist_ok=True)
    driver = "ntfs3" if fstype == "ntfs" else fstype
    gid = _gid()
    if writable:
        r = _mount(runner, driver, mount_options(fstype, True, gid, errors_ro=True), devnode, point)
        if r.returncode != 0:
            log("the exfat driver refused errors=remount-ro (%s); mounting the clip stick without it"
                % (r.stderr or r.stdout).strip())
            r = _mount(runner, driver, mount_options(fstype, True, gid), devnode, point)
    else:
        r = _mount(runner, driver, mount_options(fstype, rw and not own, gid), devnode, point)
    if r.returncode != 0:
        try:
            os.rmdir(point)
        except OSError:
            pass
        raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
    _point_link(link, point, log)
    if own:
        status = {"device": devnode, "point": point, "label": info.get("LABEL"), "writable": writable, "why": why_ro}
        if writable:
            try:
                clipstick.ensure_clip_folder(point)
            except (OSError, clipstick.ClipStickError) as e:
                log("clip stick %s: no clips folder (%s)" % (info.get("LABEL"), e))
                status.update(writable=False, why="no clips folder")
        try:
            clipstick.write_status(env.get("NXLX_CLIP_STATUS", clipstick.DEFAULT_STATUS), status)
        except OSError as e:
            log("could not write the clip stick status: %s" % e)
    log("mounted %s (%s, %s%s) at %s" % (devnode, fstype, "rw" if (writable or (rw and not own)) else "ro",
                                         ", the node's clip stick" if own else "", point))
    return point


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
    clipstick.clear_status(env.get("NXLX_CLIP_STATUS", clipstick.DEFAULT_STATUS), devnode)   # the library goes back to the SD first
    points = [p for dev, p in read_mounts(proc_mounts) if dev == devnode]
    for point in points:
        if os.path.dirname(point.rstrip("/")) != base.rstrip("/"):
            log("not unmounting %s: outside %s" % (point, base))
            continue
        runner(["umount", "-l", point], capture_output=True, text=True, timeout=30)  # lazy: the device may be gone
        try:
            os.rmdir(point)
        except OSError:
            pass
        if os.path.islink(link) and os.readlink(link) == point:
            os.unlink(link)
        log("unmounted %s from %s" % (devnode, point))
    return points


def claim(devnode, force=False, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print,
          system_disks=None, node_name=None):
    """Make the stick at `devnode` this node's clip stick (root only, from a terminal: `sudo nxlx-usb claim DEV`).

    The partition must be exFAT and labelled Nxx-CLIPS. It is unmounted if mounted, checked with fsck.exfat -p (which
    must pass), mounted writable at a private folder, given the marker and a clips folder, unmounted, and then mounted
    the normal way. A stick whose marker names another node is refused unless `force`. The node keeps a record that it
    has a clip stick, so the panel can say when it is missing."""
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
    if any(dev == devnode for dev, _ in read_mounts(proc_mounts)):
        unmount(devnode, env=env, runner=runner, proc_mounts=proc_mounts, log=log)
        if any(dev == devnode for dev, _ in read_mounts(proc_mounts)):
            raise UsbError("%s is still mounted; unmount it first" % devnode)
    ok, why = clipstick.fsck(devnode, runner, log)
    if not ok:
        raise UsbError("the stick did not pass its check (%s); repair it on a computer first" % why)
    peek = os.path.join(env.get("NXLX_USB_PEEK", PEEK_DIR), os.path.basename(devnode))
    os.makedirs(peek, mode=0o700, exist_ok=True)
    r = _mount(runner, "exfat", mount_options("exfat", True, _gid()), devnode, peek)
    if r.returncode != 0:
        try:
            os.rmdir(peek)
        except OSError:
            pass
        raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
    try:
        old = clipstick.read_marker(peek)
        if (old is not None and isinstance(old.get("node_id"), str) and old.get("node_id") != node_id and not force):
            raise UsbError("this stick is claimed by another node (%s); add --force to take it over"
                           % (old.get("node_name") or old.get("node_id")))
        if node_name is None:
            import socket
            node_name = socket.gethostname().split(".")[0]
        clipstick.write_marker(peek, clipstick.make_marker(node_id, node_name, label))
        clipstick.ensure_clip_folder(peek)
    except clipstick.ClipStickError as e:
        raise UsbError(str(e))
    finally:
        runner(["umount", peek], capture_output=True, text=True, timeout=120)
        try:
            os.rmdir(peek)
        except OSError:
            pass
    clipstick.write_claim(state, label)
    log("claimed %s (%s) as this node's clip stick" % (devnode, label))
    return mount(devnode, env=env, runner=runner, proc_mounts=proc_mounts, log=log, system_disks=system_disks)


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
            gone = clipstick.forget_claim(os.environ.get("NXLX_STATE_DIR", DEFAULT_STATE))
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
