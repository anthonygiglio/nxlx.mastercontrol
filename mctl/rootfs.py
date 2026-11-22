# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""nxlx-rootfs: protect the system disk from power loss with a read-only root.

With an overlay root, everything written since boot lives in RAM and vanishes
at reboot, so a pulled plug cannot corrupt the SD card or SSD. This module does
not invent that mechanism; it drives the tools distributions already ship:

* Raspberry Pi OS: `raspi-config nonint enable_overlayfs|disable_overlayfs`
* Debian, Ubuntu and other x86: the `overlayroot` package

Changes take effect after a reboot, which this tool never triggers itself.

With the read-only disk on, nothing written after boot would survive a restart: not the PIN, the scenes, the devices,
the uploaded graphics, the settings or the Wi-Fi. So `enable` also needs the kept settings disk (mctl/kept.py, D148),
which holds those few folders outside the read-only root; `enable --without-kept` turns it on without, as before.
"""

import os
import shutil
import subprocess
import sys


class RootfsError(Exception):
    pass


# What the updater and the panel say while the read-only root is on (an update or a rollback would be lost at the next restart).
UPDATE_REFUSAL = ("The system disk is read-only (power-loss protection is on), so an update would be lost at the next restart. "
                  "Turn it off with: sudo nxlx-rootfs disable, restart, update, then sudo nxlx-rootfs enable and restart.")


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


def status(board_kind, media_dir, proc_mounts="/proc/mounts", which=shutil.which, kept=None):
    out = {"root": current_state(proc_mounts), "backend": detect_backend(board_kind, which),
           "media_dir": media_dir, "media_on_root": media_on_root(media_dir)}
    if kept is not None:
        out["kept"] = kept.status()
    return out


def change(action, board_kind, media_dir, force=False, runner=subprocess.run,
           proc_mounts="/proc/mounts", which=shutil.which, log=print, kept=None):
    """`kept` is a mctl.kept.Kept: with it, `enable` is refused unless the kept settings disk is ready (and a media
    folder inside the kept folders is not counted as on the system disk). Without it (None) nothing is checked."""
    state = current_state(proc_mounts)
    backend = detect_backend(board_kind, which)
    media_kept = False
    if action == "enable":
        if state == "overlay":
            log("read-only root is already active")
            return
        if kept is not None:
            from . import kept as keptmod
            try:
                media_kept = kept.check_for_enable(media_dir)
            except keptmod.KeptError as e:
                raise RootfsError(str(e))
        if media_on_root(media_dir) and not force and not media_kept:
            raise RootfsError("%s is on the system disk; files added while the overlay is active would be lost at "
                              "reboot. Put media on a separate disk or USB drive, or use --force." % media_dir)
    cmds = commands(backend, action, state)
    for cmd in cmds:
        r = runner(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RootfsError("%s failed: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout).strip()))
    log("%s requested. Reboot to apply. Settings in /etc/nxlx can only be changed with the root writable." % action)
    if kept is not None and action == "enable":
        log("The first restart copies the settings to the kept settings disk; after that they survive every restart.")
    if kept is not None and action == "disable" and kept.configured():
        log("The kept settings disk stays in use: the settings keep living on it, so nothing is lost or copied back.")


def main(argv=None):
    import argparse
    import json

    from . import hardware
    ap = argparse.ArgumentParser(prog="nxlx-rootfs", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["status", "enable", "disable"])
    ap.add_argument("--force", action="store_true", help="enable even if media is on the system disk")
    ap.add_argument("--without-kept", action="store_true",
                    help="enable without the kept settings disk: every setting is lost at each restart")
    args = ap.parse_args(argv)
    media = os.environ.get("NXLX_MEDIA_DIR", "/var/lib/nxlx/video")
    kind = hardware.detect_board()["kind"]
    from . import kept as keptmod
    keep = None if (args.without_kept and args.action == "enable") else keptmod.Kept()
    try:
        if args.action == "status":
            print(json.dumps(status(kind, media, kept=keep), indent=2))
        else:
            if os.geteuid() != 0:
                raise RootfsError("run as root (sudo)")
            if keep is None:
                print("nxlx-rootfs: WARNING: no kept settings disk: the PIN, scenes, devices, graphics, settings and Wi-Fi "
                      "will be lost at every restart.", file=sys.stderr)
            change(args.action, kind, media, force=args.force, kept=keep)
    except RootfsError as e:
        print("nxlx-rootfs: %s" % e, file=sys.stderr)
        return 1
    return 0
