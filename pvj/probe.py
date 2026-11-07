# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What is in a clip (codec, size, frame rate, length, sound), without playing it on the screen.

Replaces the old panel's "Movie Codec" and "Movie Resolution" buttons (mediainfo). mpv, which is already on the box,
opens the file with no video or audio output, decodes one frame and prints its properties; about half a second for a
clip on a Pi 4. Results are kept per file (path, size, modification time), so asking again is free.
"""

import math
import os
import re
import subprocess
import threading

MARK = "PVJINFO|"
FIELDS = ("codec", "width", "height", "fps", "duration", "audio", "container")
_TEMPLATE = MARK + "|".join(("${video-format}", "${width}", "${height}", "${=container-fps}", "${=duration}",
                              "${audio-codec-name}", "${file-format}"))
_cache = {}
_lock = threading.Lock()


class ProbeError(Exception):
    pass


def _number(text, kind):
    try:
        v = kind(text)
    except (TypeError, ValueError):
        return None
    return v if v == v and v >= 0 else None


def parse(line):
    """The fields of one PVJINFO line; missing values become None."""
    parts = line[len(MARK):].split("|")
    if len(parts) != len(FIELDS):
        raise ProbeError("unexpected answer from the player")
    raw = dict(zip(FIELDS, [None if p in ("", "(unavailable)") else p for p in parts]))
    fps = _number(raw["fps"], float)
    return {"codec": raw["codec"], "width": _number(raw["width"], int), "height": _number(raw["height"], int),
            "fps": round(fps, 3) if fps else None, "duration": round(_number(raw["duration"], float) or 0, 2) or None,
            "audio": raw["audio"], "container": raw["container"]}


def probe(path, mpv_bin="mpv", timeout=20):
    """A dict of FIELDS for the file at `path`. Raises ProbeError."""
    try:
        st = os.stat(path)
    except OSError:
        raise ProbeError("file not found")
    key = (path, st.st_size, int(st.st_mtime))
    with _lock:
        if key in _cache:
            return dict(_cache[key])
    args = [mpv_bin, "--no-config", "--vo=null", "--ao=null", "--frames=1", "--load-unsafe-playlists=no",
            "--access-references=no", "--ytdl=no", "--load-scripts=no", "--term-playing-msg=" + _TEMPLATE, "--", path]
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, HOME="/nonexistent"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError("could not read the file: %s" % e)
    lines = [l for l in r.stdout.splitlines() if l.startswith(MARK)]
    if not lines:
        raise ProbeError("the player could not open this file")
    info = parse(lines[0])
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = info
    return dict(info)


LENGTH = "PVJLEN|"


def exact(path, mpv_bin="mpv", timeout=20):
    """(seconds, the container as mpv calls it, the video's codec) of the file at `path`: the length as mpv gives it, to
    the microsecond, for a loop list's `duration` lines (D118; `probe` rounds to 2 places, which is what a person reads).
    The same bounded look as `probe`, and only ever on a thread of its own: it starts a program. Raises ProbeError."""
    args = [mpv_bin, "--no-config", "--vo=null", "--ao=null", "--frames=1", "--load-unsafe-playlists=no",
            "--access-references=no", "--ytdl=no", "--load-scripts=no",
            "--term-playing-msg=" + LENGTH + "${=duration}|${file-format}|${video-format}", "--", path]
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, HOME="/nonexistent"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError("could not read the file: %s" % e)
    lines = [l for l in r.stdout.splitlines() if l.startswith(LENGTH)]
    if not lines:
        raise ProbeError("the player could not open this file")
    parts = lines[0][len(LENGTH):].split("|")
    if len(parts) != 3:
        raise ProbeError("unexpected answer from the player")
    return _number(parts[0], float), parts[1] or None, parts[2] or None


HEAVY = ("prores", "dnxhd", "dnxhr", "cfhd", "rawvideo", "v210", "qtrle", "ffv1", "huffyuv", "utvideo")


# By exact kind, not tier: what is said here was measured on a Pi 4, not tested on a Pi 3, or is a fact of the Pi 5 chip
# (pvj/profile.py keeps the tier questions).
_NTSC = 1000 / 1001                      # 23.976, 29.97, 59.94 are 24, 30 and 60 times this
SLIP_QUIET = 600                         # a drift slower than one frame in this many seconds is not worth a line
_MODE_HZ = re.compile(r"@([0-9]{1,3}\.[0-9]{2})$")


def mode_hz(text):
    """The refresh rate in a mode's text ("1920x1080@60.00"), or None."""
    m = _MODE_HZ.search(text) if isinstance(text, str) else None
    return float(m.group(1)) if m else None


def _snap(rate):
    """The exact NTSC rate for a rate that is one of them to the 2 or 3 places a container or a mode text gives."""
    for n in (24, 30, 60, 120):
        if abs(rate - n * _NTSC) < 0.006:
            return n * _NTSC
    return rate


def pacing_advice(fps, hz):
    """One plain line on how a clip's frame rate sits on the screen's refresh rate, or []. No timing code runs for it (D119):
    this only says what to expect. `hz` is the output's real rate; with none known there is no advice."""
    if not fps or not hz or not math.isfinite(fps) or not math.isfinite(hz) or fps <= 0 or hz <= 0:
        return []
    shown = "%.2f Hz" % hz
    if not (abs(hz - 60) <= 0.2 or abs(hz - 50) <= 0.2):
        return ["The screen runs at %s, which is not a 60 or 50 Hz mode, so most clips (24, 25, 30 or 60 fps) will not divide "
                "into it evenly. Choose a 60.00 or 50.00 Hz mode for the screen if you can." % shown]
    f, h = _snap(fps), _snap(hz)
    ratio = h / f
    if ratio < 0.99:
        return ["At %s a %s fps clip has more frames than the screen shows, so some are skipped." % (shown, _fmt(fps))]
    k = max(1, round(ratio))
    drift = abs(h - k * f)
    if drift / h < 0.01:
        if drift < 1.0 / SLIP_QUIET:
            return []
        wish = ""
        if abs(h - 60) < 0.006 and any(abs(f - n * _NTSC) < 0.006 for n in (24, 30, 60)):
            wish = " A 59.94 Hz mode would make it even."
        elif abs(h - 60 * _NTSC) < 0.006:
            wish = " A 60.00 Hz mode would make it even."
        return ["At %s a %s fps clip slips one frame about every %d s (a small hitch).%s" % (shown, _fmt(fps), round(1 / drift), wish)]
    if ratio == int(ratio):
        return []
    lo = int(ratio)
    if abs(f - 24) < 0.1 and abs(h - 60) <= 0.2:
        pattern = "shown for 3 and 2 refreshes in turn (3:2)"
    else:
        pattern = "shown for %d or %d refreshes, unevenly" % (lo, lo + 1)
    return ["At %s a %s fps clip judders in slow movement: its frames are %s. Matching the clip to the screen's rate (for example 30 or 60 fps "
            "on 60 Hz, 25 or 50 fps on 50 Hz) avoids it." % (shown, _fmt(fps), pattern)]


def _fmt(rate):
    return ("%.3f" % rate).rstrip("0").rstrip(".")


def advice(info, board, hz=None):
    """Plain-word warnings for a clip that may not play smoothly on this board, or []. Only what was measured on a
    Pi 4 (1080p H.264 at 24 fps in software: about 109 percent of one core, no dropped frames) or is plainly far
    beyond it; anything untested says so. With the screen's refresh rate (`hz`, in Hz) a line on pacing is added
    (pacing_advice); without it the advice is as it always was."""
    codec = (info.get("codec") or "").lower()
    w, h, fps = info.get("width") or 0, info.get("height") or 0, info.get("fps") or 0
    if not isinstance(fps, (int, float)) or not math.isfinite(fps):
        fps = 0                 # not a rate (inf or nan from a damaged file): unknown
    if not codec:
        return []
    out = []
    pixels = w * h
    if any(k in codec for k in HEAVY):
        out.append("%s is an editing format: far too heavy for playback on this node. Export H.264 or HEVC (see Prepare "
                   "your clips in the manual)." % codec.upper())
        return out
    if board in ("pi3", "pi4") and "h264" in codec and pixels > 1920 * 1088:
        out.append("H.264 larger than 1080p is decoded in software here and will very likely stutter. Use 1080p, or "
                   "HEVC for 4K (4K not tested on this node yet).")
    elif board == "pi4" and "h264" in codec and pixels > 1280 * 720 and fps > 31:
        out.append("1080p at %s fps in H.264 is decoded in software here; 1080p at 24 fps was measured smooth on a Pi 4, "
                   "faster was not tested. If it stutters, use 25 or 30 fps or HEVC." % round(fps))
    elif board == "pi3" and pixels > 1280 * 720:
        out.append("On a Pi 3, clips larger than 720p were not tested and may stutter. 720p H.264 is the safe choice.")
    if board == "pi4" and "h264" not in codec and pixels > 1920 * 1088:
        out.append("Larger than 1080p: not tested on this node yet. Check it plays smoothly before the show.")
    if board == "pi5" and "h264" in codec and pixels > 1920 * 1088:
        out.append("A Pi 5 has no hardware H.264 decoding: above 1080p use HEVC (not tested on a Pi 5 yet).")
    if fps and fps > 61:
        out.append("%s fps is more than a screen shows; use 25, 30, 50 or 60." % round(fps))
    if codec in ("mjpeg", "png", "bmp", "tiff", "webp") and pixels > 24000000:
        out.append("A very large picture (%dx%d): it may be slow to show. 1920x1080 is plenty for a screen." % (w, h))
    elif fps and codec not in ("mjpeg", "png", "bmp", "tiff", "webp") and 1 < fps <= 61:       # above 61 the line above is the one
        out += pacing_advice(fps, hz)
    return out

