# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-rootfs: protect the system disk from power loss with a read-only root.

With an overlay root, everything written since boot lives in RAM and vanishes
at reboot, so a pulled plug cannot corrupt the SD card or SSD. This module does
not invent that mechanism; it drives the tools distributions already ship:

* Raspberry Pi OS: `raspi-config nonint enable_overlayfs|disable_overlayfs`
* Debian, Ubuntu and other x86: the `overlayroot` package

Changes take effect after a reboot, which this tool never triggers itself.
"""

import os
import shutil
import subprocess
import sys


class RootfsError(Exception):
    pass


def current_state(proc_mounts="/proc/mounts"):
    """'overlay' if / is an overlay filesystem right now, else 'normal'."""
    try:
        with open(proc_mounts) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/":
                    return "overlay" if parts[2] == "overlay" or parts[0] == "overlayroot" else "normal"
    except OSError:
        pass
    return "normal"


def detect_backend(board_kind, which=shutil.which):
    if board_kind.startswith("pi") and which("raspi-config"):
        return "raspi-config"
    if which("overlayroot-chroot") or which("overlayroot"):
        return "overlayroot"
    return None


def commands(backend, action, state):
    """Command lists to run for enable/disable. Raises if the request makes no sense."""
    if backend == "raspi-config":
        return [["raspi-config", "nonint", "enable_overlayfs" if action == "enable" else "disable_overlayfs"]]
    if backend == "overlayroot":
        if action == "enable":
            return [["sh", "-c", "printf 'overlayroot=\"tmpfs:recurse=0\"\\n' > /etc/overlayroot.local.conf"]]
        inner = "printf 'overlayroot=\"\"\\n' > /etc/overlayroot.local.conf"
        if state == "overlay":
            # Editing the config while the overlay is active would only change RAM.
            return [["overlayroot-chroot", "sh", "-c", inner]]
        return [["sh", "-c", inner]]
    raise RootfsError("no supported tool found: on Raspberry Pi OS use raspi-config; "
                      "elsewhere install overlayroot (sudo apt install overlayroot)")


def media_on_root(media_dir):
    """True if the media folder lives on the same filesystem as / (it would be lost at reboot)."""
    try:
        return os.stat(media_dir).st_dev == os.stat("/").st_dev
    except OSError:
        return True


def status(board_kind, media_dir, proc_mounts="/proc/mounts", which=shutil.which):
    return {"root": current_state(proc_mounts), "backend": detect_backend(board_kind, which),
            "media_dir": media_dir, "media_on_root": media_on_root(media_dir)}


def change(action, board_kind, media_dir, force=False, runner=subprocess.run,
           proc_mounts="/proc/mounts", which=shutil.which, log=print):
    state = current_state(proc_mounts)
    backend = detect_backend(board_kind, which)
    if action == "enable":
        if state == "overlay":
            log("read-only root is already active")
            return
        if media_on_root(media_dir) and not force:
            raise RootfsError("%s is on the system disk; files added while the overlay is active would be lost at "
                              "reboot. Put media on a separate disk or USB drive, or use --force." % media_dir)
    cmds = commands(backend, action, state)
    for cmd in cmds:
        r = runner(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RootfsError("%s failed: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout).strip()))
    log("%s requested. Reboot to apply. Settings in /etc/pvj can only be changed with the root writable." % action)


def main(argv=None):
    import argparse
    import json

    from . import hardware
    ap = argparse.ArgumentParser(prog="pvj-rootfs", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["status", "enable", "disable"])
    ap.add_argument("--force", action="store_true", help="enable even if media is on the system disk")
    args = ap.parse_args(argv)
    media = os.environ.get("PVJ_MEDIA_DIR", "/var/lib/pvj/video")
    kind = hardware.detect_board()["kind"]
    try:
        if args.action == "status":
            print(json.dumps(status(kind, media), indent=2))
        else:
            if os.geteuid() != 0:
                raise RootfsError("run as root (sudo)")
            change(args.action, kind, media, force=args.force)
    except RootfsError as e:
        print("pvj-rootfs: %s" % e, file=sys.stderr)
        return 1
    return 0
