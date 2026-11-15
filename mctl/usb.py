# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""USB drive automount by label, called from a udev-triggered systemd unit.

Replaces mountusb.sh (which mounted sda1 and sda2 on the same folder) and the
hardcoded /dev/sda mount in the old backend, which on an x86 box is the system
disk. Rules of this module:

* every USB partition gets its own folder under /media/pvj/<label>
* /media/usb points at the most recently mounted one (what old presets expect)
* mounted read-only unless PVJ_USB_RW=1, with nosuid,nodev,noexec, because a
  drive yanked mid-write at a gig is the usual way to corrupt it
* never touch a disk that also holds the running system
"""

import grp
import os
import re
import subprocess
import sys

DEVNODE = re.compile(r"^/dev/sd[a-z]{1,2}[0-9]{0,2}$")
ALLOWED_FS = {"vfat", "exfat", "ext2", "ext3", "ext4", "ntfs"}
SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/home", "/var", "/usr", "/etc")
DEFAULT_BASE = "/media/pvj"
DEFAULT_LINK = "/media/usb"


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


def mount_options(fstype, rw, gid):
    opts = ["rw" if rw else "ro", "nosuid", "nodev", "noexec", "noatime"]
    if fstype in ("vfat", "exfat", "ntfs"):
        opts += ["uid=0", "umask=0002" if gid is not None else "umask=0022"]
        if gid is not None:
            opts.append("gid=%d" % gid)
    if fstype == "vfat":
        opts.append("utf8")
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
        return grp.getgrnam("pvj").gr_gid
    except KeyError:
        return None


def _free_mountpoint(base, name, mounted_points):
    candidate, n = os.path.join(base, name), 2
    while candidate in mounted_points or (os.path.isdir(candidate) and os.listdir(candidate)):
        candidate = os.path.join(base, "%s-%d" % (name, n))
        n += 1
    return candidate


def mount(devnode, env=None, runner=subprocess.run, proc_mounts="/proc/mounts", log=print, system_disks=None):
    env = os.environ if env is None else env
    if not DEVNODE.match(devnode or ""):
        raise UsbError("refusing %r (not a USB disk device node)" % devnode)
    base = env.get("PVJ_USB_BASE", DEFAULT_BASE)
    link = env.get("PVJ_USB_LINK", DEFAULT_LINK)
    rw = env.get("PVJ_USB_RW", "0") == "1"

    mounts = read_mounts(proc_mounts)
    if any(dev == devnode for dev, _ in mounts):
        log("%s is already mounted" % devnode)
        return None
    disk = parent_disk(devnode)
    if disk in (backing_disks() if system_disks is None else system_disks):
        raise UsbError("refusing %s: %s holds the running system" % (devnode, disk))
    for dev, point in mounts:
        if parent_disk(dev) == disk and point in SYSTEM_MOUNTS:
            raise UsbError("refusing %s: %s holds the running system (%s)" % (devnode, disk, point))

    info = probe(devnode, runner)
    fstype = info.get("TYPE", "")
    if fstype not in ALLOWED_FS:
        raise UsbError("unsupported filesystem %r on %s" % (fstype, devnode))
    name = sanitize_label(info.get("LABEL"), os.path.basename(devnode))
    os.makedirs(base, mode=0o755, exist_ok=True)
    point = _free_mountpoint(base, name, {p for _, p in mounts})
    os.makedirs(point, mode=0o755, exist_ok=True)
    driver = "ntfs3" if fstype == "ntfs" else fstype
    r = runner(["mount", "-t", driver, "-o", mount_options(fstype, rw, _gid()), devnode, point],
               capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        try:
            os.rmdir(point)
        except OSError:
            pass
        raise UsbError("mount failed: %s" % (r.stderr or r.stdout).strip())
    _point_link(link, point, log)
    log("mounted %s (%s, %s) at %s" % (devnode, fstype, "rw" if rw else "ro", point))
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
    link = env.get("PVJ_USB_LINK", DEFAULT_LINK)
    base = env.get("PVJ_USB_BASE", DEFAULT_BASE)
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 2 and argv[0] == "mount":
            mount(argv[1])
        elif len(argv) == 2 and argv[0] == "unmount":
            unmount(argv[1])
        elif argv == ["list"]:
            base = os.environ.get("PVJ_USB_BASE", DEFAULT_BASE)
            for dev, point in read_mounts():
                if os.path.dirname(point.rstrip("/")) == base.rstrip("/"):
                    print(dev, point)
        else:
            print("usage: pvj-usb mount DEV | unmount DEV | list", file=sys.stderr)
            return 2
    except UsbError as e:
        print("pvj-usb: %s" % e, file=sys.stderr)
        return 1
    return 0
