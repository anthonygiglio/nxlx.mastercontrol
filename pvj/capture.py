# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Live input from a USB capture device (an HDMI capture stick, a webcam): the old panel's camera livefeed.

Why a separate process: on a Raspberry Pi 4 with mpv 0.40, closing a V4L2 capture inside the player crashed the player
(SIGBUS or SIGSEGV) in 4 of 5 switches back to a file, in libavdevice's buffer teardown. So the player never opens the
device. A helper mpv, run by the web service, reads the device and writes raw frames to a pipe in its own runtime folder (/run/pvj/web); the player
plays the pipe with the raw video demuxer. Leaving the input stops the helper; if its teardown crashes, only the helper
dies (0 player crashes in 5 switches, against 4 in 5 before). Measured on a Pi 4 with a MACROSILICON USB3 HDMI capture
stick (YUYV only), through the pipe: 720p30 and 1080p30 dropped no frames and kept real time; 720p60 dropped 9 frames a
second and 1080p60 would not open reliably, so neither is offered.
"""

import errno
import fcntl
import grp
import os
import stat
import re
import signal
import subprocess
import threading
import time

from . import paths

MODES = {"720p30": (1280, 720, 30), "1080p30": (1920, 1080, 30)}
DEVICE = re.compile(r"video([0-9]{1,3})")
SKIP_NAMES = ("bcm2835", "rpi-", "unicam", "rp1-cfe")      # the Pi's own codec, ISP and camera blocks, not inputs


class CaptureError(Exception):
    pass


def list_devices(sysfs="/sys/class/video4linux"):
    """Capture inputs: [{"id": "video0", "name": "USB3.0 UHD"}]. Only the first node of each device (index 0)."""
    out = []
    try:
        names = sorted(os.listdir(sysfs), key=lambda n: int(DEVICE.fullmatch(n).group(1)) if DEVICE.fullmatch(n) else 1e9)
    except OSError:
        return out
    for n in names:
        if not DEVICE.fullmatch(n):
            continue
        try:
            with open(os.path.join(sysfs, n, "name")) as f:
                label = f.read().strip()
            with open(os.path.join(sysfs, n, "index")) as f:
                index = f.read().strip()
        except OSError:
            continue
        if index != "0" or label.lower().startswith(SKIP_NAMES):
            continue
        try:                                   # only devices on a USB bus: the Pi's own codec, ISP and camera blocks are not inputs
            if "/usb" not in os.path.realpath(os.path.join(sysfs, n, "device")):
                continue
        except OSError:
            continue
        out.append({"id": n, "name": re.sub(r"[^\w .:()-]", "", label)[:60]})
    return out


class Capture:
    def __init__(self, rundir, mpv_bin="mpv", lister=list_devices, spawn=subprocess.Popen, log=print):
        self.rundir, self.mpv_bin, self.lister, self._spawn, self.log = rundir, mpv_bin, lister, spawn, log
        self.fifo = paths.capture_fifo(rundir)
        self.proc = None
        self.current = None           # {"device", "mode"}
        self._lock = threading.RLock()

    def helper_args(self, device, mode):
        """The helper always writes exactly W x H YUYV, whatever the device sends (a webcam may give MJPEG at 1080p or
        fall back to another size), because the player is told that layout."""
        w, h, fps = MODES[mode]
        return [self.mpv_bin, "--no-config", "--really-quiet", "--no-audio", "--no-input-terminal",
                "--demuxer-lavf-o=input_format=yuyv422,video_size=%dx%d,framerate=%d" % (w, h, fps),
                "--vf=scale=%d:%d,format=yuyv422" % (w, h), "--of=rawvideo", "--ovc=rawvideo",
                "--o=-", "--", "av://v4l2:/dev/%s" % device]

    def prepare(self, device, mode):
        """Check the request and make a fresh pipe. Returns (width, height, fps). Raises CaptureError. The caller holds
        self.lock across prepare, loading the pipe in the player and start, so two requests cannot interleave."""
        if not isinstance(mode, str) or mode not in MODES:
            raise CaptureError("mode must be one of " + ", ".join(MODES))
        if not isinstance(device, str) or not DEVICE.fullmatch(device) or device not in [d["id"] for d in self.lister()]:
            raise CaptureError("no such input")
        self.stop()
        os.mkfifo(self.fifo, 0o640)       # the panel writes, the player (group pvj) only reads
        return MODES[mode]

    def start(self, device, mode, timeout=5.0):
        """Start the helper writing into the pipe the player is opening. Raises CaptureError, and cleans up."""
        deadline = time.monotonic() + timeout
        fd = None
        while fd is None:
            try:
                fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            except OSError as e:
                if e.errno != errno.ENXIO or time.monotonic() > deadline:
                    self.stop()
                    raise CaptureError("the player did not open the input")
                time.sleep(0.05)
        try:
            st = os.fstat(fd)
            if not stat.S_ISFIFO(st.st_mode) or st.st_uid != os.getuid():   # someone swapped the pipe for something else
                raise CaptureError("the input pipe was replaced; not writing to it")
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            try:
                fcntl.fcntl(fd, getattr(fcntl, "F_SETPIPE_SZ", 1031), 4 * 1024 * 1024)   # fewer wake-ups at ~124 MB/s
            except OSError:
                pass
            try:
                proc = self._spawn(self.helper_args(device, mode), stdin=subprocess.DEVNULL, stdout=fd,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                raise CaptureError("could not start the capture: %s" % e)
        except CaptureError:
            os.close(fd)
            self.stop()
            raise
        os.close(fd)
        self.proc, self.current = proc, {"device": device, "mode": mode}
        try:                                    # a busy, unplugged or unhappy device ends the helper at once: say so
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self.log("pvj-web: live input %s %s started" % (device, mode))
            return
        self.stop()
        raise CaptureError("the input could not be opened (in use by something else, unplugged, or that size is not offered)")

    def stop(self):
        """Stop the helper (its teardown may crash; it is on its own) and remove the pipe, waking any reader still waiting
        on it. Safe to call when nothing runs."""
        p, self.proc, self.current = self.proc, None, None
        if p is not None:
            try:
                p.send_signal(signal.SIGTERM)
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            except (OSError, ProcessLookupError):
                pass
        try:
            wake = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            os.close(wake)                      # a reader blocked in open() gets end of file instead of waiting for ever
        except OSError:
            pass
        try:
            os.unlink(self.fifo)
        except FileNotFoundError:
            pass
        return p is not None

    @property
    def lock(self):
        return self._lock

    def status(self, devices=True):
        """No lock: a status poll must never wait behind a capture that is starting or stopping."""
        p, cur = self.proc, self.current
        running = bool(p and p.poll() is None)
        out = {"running": running, "current": cur if running else None, "modes": list(MODES)}
        if devices:
            out["devices"] = self.lister()
        return out
