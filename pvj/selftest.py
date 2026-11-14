# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-selftest: run on each device and send the JSON report back.

    bin/pvj-selftest            checks that need no display
    bin/pvj-selftest --play     also plays a 6 second test pattern on the real screen
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__, hardware
from .player import Player, PlayerError

PATTERN = "av://lavfi:testsrc2=size=1280x720:rate=30"


def check(name, fn):
    started = time.monotonic()
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": detail, "seconds": round(time.monotonic() - started, 2)}
    except Exception as e:  # report every failure instead of stopping
        return {"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e),
                "seconds": round(time.monotonic() - started, 2)}


def mpv_version():
    mpv = shutil.which("mpv")
    if not mpv:
        raise RuntimeError("mpv not installed (sudo apt install mpv)")
    out = subprocess.run([mpv, "--version"], capture_output=True, text=True, timeout=10).stdout
    return out.splitlines()[0]


def headless_roundtrip():
    """Start mpv with no video/audio output and control it over IPC."""
    rundir = tempfile.mkdtemp()
    os.chmod(rundir, 0o700)
    p = Player(extra_args=["--vo=null", "--ao=null"], rundir=rundir)
    try:
        p.play([PATTERN])
        p.speed(2)
        p.pause(True)
        st = p.status()
        if st.get("speed") != 2.0 or st.get("paused") is not True:
            raise RuntimeError("unexpected status %r" % st)
        return "ipc ok"
    finally:
        p.stop()
        shutil.rmtree(rundir, ignore_errors=True)


def display_play(seconds=6):
    rundir = tempfile.mkdtemp()
    os.chmod(rundir, 0o700)
    profile = hardware.playback_profile(hardware.detect_board(), hardware.has_desktop())
    p = Player(extra_args=profile["mpv_args"], rundir=rundir)
    try:
        p.play([PATTERN])
        time.sleep(seconds)
        st = p.status()
        drops = {}
        for prop in ("frame-drop-count", "decoder-frame-drop-count", "hwdec-current", "video-codec"):
            try:
                drops[prop] = p.ipc.request("get_property", prop)
            except PlayerError:
                drops[prop] = None
        return {"status": st, "mpv_args": profile["mpv_args"], "counters": drops,
                "note": "Did you see the test pattern on the screen, smoothly? Tell the developer."}
    finally:
        p.stop()
        shutil.rmtree(rundir, ignore_errors=True)


def decoding(root="/", machine=None):
    """Whether hardware decoding is there (VA-API on a PC node), as information: it is not a check, so a node without it
    still passes. A PC node without its VA-API driver plays in software, which drops frames on heavy clips (J-152)."""
    try:
        return hardware.va_decoding(root, machine)
    except Exception as e:  # information only: it must never stop the report
        return {"available": None, "text": "Could not tell: %s: %s" % (type(e).__name__, e)}


def _timedatectl_synchronized():
    """timedatectl's NTPSynchronized ("yes" or "no"), or None when it cannot say (no timedatectl, no systemd)."""
    exe = shutil.which("timedatectl")
    if not exe:
        return None
    r = subprocess.run([exe, "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=5)
    value = r.stdout.strip().lower()
    return value if r.returncode == 0 and value in ("yes", "no") else None


def clock_sync(root="/", ask=None):
    """Whether the clock is kept right by time sync, as information: it is not a check, so a node that is not
    synchronised still passes. A PC node without any time sync ran 5 hours slow (J-154). timedatectl is asked first;
    without an answer from it, the flag file systemd-timesyncd keeps while synchronised
    (/run/systemd/timesync/synchronized) says yes, and its absence cannot tell. `synchronized` is True, False or None."""
    try:
        value = (ask or _timedatectl_synchronized)()
        if value == "yes":
            return {"synchronized": True, "source": "timedatectl", "text": "The clock is synchronised (time sync is on)."}
        if value == "no":
            return {"synchronized": False, "source": "timedatectl",
                    "text": "The clock is not synchronised: no time sync service is running, or it has not reached a "
                            "time server yet. The installer installs systemd-timesyncd when none is there."}
        if os.path.exists(os.path.join(root, "run/systemd/timesync/synchronized")):
            return {"synchronized": True, "source": "systemd-timesyncd",
                    "text": "The clock is synchronised (systemd-timesyncd)."}
        return {"synchronized": None, "source": None, "text": "Could not tell whether the clock is synchronised."}
    except Exception as e:  # information only: it must never stop the report
        return {"synchronized": None, "source": None, "text": "Could not tell: %s: %s" % (type(e).__name__, e)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pvj-selftest")
    ap.add_argument("--play", action="store_true", help="also play a test pattern on the real display")
    ap.add_argument("--json", metavar="FILE", help="write the report to FILE as well")
    args = ap.parse_args(argv)

    report = {"pvj_version": __version__, "python": platform.python_version(),
              "hardware": hardware.report(), "hardware_decoding": decoding(),
              "clock": clock_sync(), "checks": []}
    report["checks"].append(check("mpv installed", mpv_version))
    report["checks"].append(check("headless ipc roundtrip", headless_roundtrip))
    if args.play:
        report["checks"].append(check("display playback", display_play))
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text + "\n")
    return 0 if all(c["ok"] for c in report["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
