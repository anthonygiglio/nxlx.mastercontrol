# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""NDI input: receive a sender's picture and hand it to the one mpv as raw frames in a pipe (D62, pvj/NDI.md).

NDI(R) is a registered trademark of Vizrt NDI AB. Nothing of NDI's is in this file or this repository: the runtime
library (libndi.so.6) is proprietary and is put on the box by its owner (`pvj-ndi-runtime`, below). It is loaded
with ctypes; the structures further down were written from the layout the SDK's public headers describe.

Three parts, in one file so the bounds live in one place:

* `pvj-ndi` (Service, Receiver, CtypesLibrary): a small daemon under its own account and sandbox. It is the only
  process that loads the library, so a library taken over by a bad packet has that account's reach and no more: no
  player socket, no PIN, no settings, no internet (pvj/systemd/pvj-ndi.service).
* the panel's side (Client, Input): asks the daemon over a Unix socket and tells the player to read the pipe.
* `pvj-ndi-runtime` (runtime_main): root's command that sets NDI up on a box: it copies the library into place after
  checking what it is, then installs the helper (pvj/ndisetup.py). Until it is run a box has no helper at all.

Everything that comes from the network, through a closed-source library, is hostile until checked: source names
(clean_name), where a source is (clean_sources), and every number of a frame (check_frame), each against a fixed
bound and with fullmatch. The library itself is behind a seam of a few methods (CtypesLibrary); the tests drive
everything above the seam with a fake. Run once against the real library, a real sender and mpv on a Pi 4
(2026-10-08, one short run by SSH, nobody at the monitor: pvj/NDI.md); what was changed after it has not been.
"""

import array
import collections
import ctypes
import errno
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import select
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
import unicodedata

from . import paths

LIB_DIR = "/opt/pvj-ndi"                  # the application's own folder, root's: NDI asks that the library stay off the system path
LIB_NAME = "libndi.so.6"
LIB_MAX_BYTES = 96 * 1024 * 1024
MAX_SOURCES = 64
SCAN_ROWS = 2048                          # rows of the library's list that are looked at, whatever it says it has
MAX_NAME = 128
MAX_MARKS = 2                             # combining marks in a row that a name may have, after NFC
MAX_NAME_WIRE = 768                       # and as the helper's answer writes it (6 bytes for an accented letter, 12 for an emoji)
MAX_WHERE = 100                           # a host and a port
REPLY_LIMIT = 65536                       # what a helper's client reads of one answer (netd.exchange)
MAX_ADDRESSES = 16
MIN_SIDE, MAX_WIDTH, MAX_HEIGHT = 16, 3840, 2160
MAX_STRIDE_PAD = 256                      # a line may be padded; never by more than this
FOURCC_UYVY = 0x59565955                  # "UYVY", read as a little-endian number
FOURCC_UYVA = 0x41565955                  # "UYVA": a UYVY plane and then an alpha plane, which is never read
PROGRESSIVE, INTERLACED = 1, (0, 2, 3)      # the frame format values: 0 two fields in one frame, 2 and 3 single fields
FIRST_FRAME_SECONDS = 6.0
PIPE_OPEN_SECONDS = 10.0
QUIET_SECONDS = 2.0                       # no frame for this long: "still" (or "waiting", if the connection dropped)
# A frame that nothing follows is written to the pipe once more after this long. Why: on the first run on a Pi 4
# (mpv 0.40) a still source's one frame was in the pipe and mpv reported frame 0, position 0.0 and "idle", and could
# not make a snapshot. mpv, as far as its source is remembered here (player/video.c; NOT read again for this, and
# NOT tried), shows a frame only once it also holds the one after it, to know how long the first lasts. If that is
# right the repeat puts a still picture on the screen; if it is wrong the repeat costs one frame. Device step N12.
REPEAT_AFTER = 0.3

# ---- sound (D62, 2026-10-08). Every figure of a block of sound is the sender's and is held to these.
AUDIO_FLTP = 0x70544C46                   # "FLTp": 32-bit floats, one plane a channel; the only form the library hands out
AUDIO_RATES = (32000, 44100, 48000, 88200, 96000)
MAX_AUDIO_CHANNELS = 64                   # what a sender may say it has; only the first AUDIO_PLANES are ever read
AUDIO_PLANES = 2                          # the first two channels are played (one, for a mono source); NDI says no layout
MAX_AUDIO_SAMPLES = 48000                 # in one block, for each channel, and never more than one second of it
# NDI's sound is floating point in which 1.0 is a professional reference level (+4 dBu), not full scale: a sender
# may go well above 1.0. NDI's own helpers for 16-bit sound are told a "reference level" in dB for this, and its
# header advises 20 for receiving: full scale is then 20 dB above reference. The same here, in one place. Measured
# on 2026-10-08 against NDI Tools' Test Patterns (a 1 kHz tone at SMPTE alignment level): the floats peak at
# exactly 1.0, so what the box plays peaks at -20 dBFS, which is where that tone belongs.
AUDIO_HEADROOM_DB = 20
AUDIO_GAIN = 10 ** (-AUDIO_HEADROOM_DB / 20.0)
AUDIO_ABSURD = 1000.0                     # a sample 60 dB over reference is not sound: the block is played as silence
AUDIO_WAIT = 0.5                          # after the first picture: how long sound may take to show that there is some
AUDIO_QUEUE_SECONDS = 1.0                 # sound waiting for the player; beyond it the oldest is dropped, never kept
AUDIO_WINDOW = 0.3                        # how far the sound's place in time may stray from the helper's clock
AUDIO_SILENT_DB = -70.0                   # a second whose loudest sample is under this is "silent" on the page
CONTAINERS = ("raw", "matroska")          # what the pipe carries: bare frames (no sound), or frames and sound with times
PLAYER_LEFT = "the player stopped reading the input"      # something else was played: an end, not a fault
SOURCES_CACHE = 1.0
PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
# "still": on the screen and connected, but no new frame for a while. That is normal (a test pattern, a paused
# output, a slide): the real library sent ONE frame of NDI Tools' Test Patterns and then none (measured 2026-10-07).
# "waiting" is only said when the library itself reports the connection dropped.
STATES = ("connecting", "ready", "playing", "still", "waiting", "changed", "refused", "stopped")
ENDED = ("changed", "refused", "stopped")
# The one command, run by root at the box, that sets NDI up (the library and the helper; pvj/ndisetup.py has the same words).
SETUP_COMMAND = 'sudo pvj-ndi-runtime install "/path/to/NDI SDK for Linux"'
NOT_SET_UP = "NDI is not set up on this box; someone with a keyboard or SSH on the box runs: " + SETUP_COMMAND

_ID = re.compile(r"[0-9a-f]{12}")
_WHERE = re.compile(r"[A-Za-z0-9._:\[\]-]{1,%d}" % MAX_WHERE)
_IPV4 = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")
_BAD_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")     # control, format (bidi, zero width), surrogate, private, unassigned, line breaks

ELF_MACHINES = {3: "32-bit x86", 40: "32-bit ARM", 62: "x86_64", 183: "aarch64"}
SDK_FOLDERS = {62: ("x86_64-linux-gnu",), 3: ("i686-linux-gnu",),
               183: ("aarch64-rpi4-linux-gnueabi", "aarch64-rpi3-linux-gnueabi", "aarch64-linux-gnu"),
               40: ("arm-rpi4-linux-gnueabihf", "arm-rpi3-linux-gnueabihf", "arm-rpi2-linux-gnueabihf")}


class NdiError(Exception):
    pass


# ---- what comes from the network ---------------------------------------------------------------------------------
def clean_name(raw):
    """A source's name as it may be shown and listed, or None for one that is dropped. Bytes must be UTF-8."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or len(raw) > MAX_NAME * 4:
        return None
    # Two names that look alike must not be two rows. So: one way of writing an accented letter (NFC), every kind
    # of space (no-break, thin, ideographic) shown and compared as a plain one and runs of them as one, and no
    # pile of accents on one letter (more than MAX_MARKS in a row is refused: no real name has it).
    raw = unicodedata.normalize("NFC", raw)
    raw = re.sub(" +", " ", "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in raw))
    if not 1 <= len(raw) <= MAX_NAME or raw != raw.strip():
        return None
    if any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in raw):
        return None
    marks = 0
    for ch in raw:
        marks = marks + 1 if unicodedata.category(ch) in ("Mn", "Mc", "Me") else 0
        if marks > MAX_MARKS:
            return None
    # The helper's whole answer must fit what its client reads (REPLY_LIMIT), with MAX_SOURCES names in it. An
    # answer that does not fit makes the helper look dead, so a name is also bounded as it is written there.
    if len(json.dumps(raw)) - 2 > MAX_NAME_WIRE:
        return None
    return raw


def host_of(where):
    """The machine in a source's address, without its port: "192.168.0.20:5961" gives "192.168.0.20"."""
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::[0-9]{1,5})?|([^:]+)(?::[0-9]{1,5})?", where)
    return (m.group(1) or m.group(2)).lower() if m else where.lower()


def source_id(name, host):
    """What the panel plays by: made here from the checked name AND the machine it is announced from, so a second
    machine announcing the same name is another source and can never stand in for the one that was chosen. The
    same after a restart. The port is left out (it changes when the sender restarts). A sender whose address
    changes (DHCP) becomes a new row and has to be chosen again: staying with the machine was preferred to
    following the name."""
    return hashlib.sha256(b"ndi:" + name.encode("utf-8") + b"\0" + host.encode("utf-8")).hexdigest()[:12]


def clean_sources(raw, prefer_hosts=(), keep_id=None):
    """([{"id", "name", "from", "raw": (name bytes, address bytes)}], was the list cut) from the library's (name,
    address) pairs. Only sources whose name and address pass; at most SCAN_ROWS rows are looked at and MAX_SOURCES
    kept. Which ones are kept does not depend on the order the library gives them in (anyone on the network can
    announce senders): the source being shown (`keep_id`) first, then senders at an address the owner added
    (`prefer_hosts`), then the rest, each group by name and address. So neither can be pushed out of the list."""
    prefer = {str(h).lower() for h in prefer_hosts}
    found = {}
    for n, pair in enumerate(raw):
        if n >= SCAN_ROWS:
            break
        if not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(x, bytes) for x in pair):
            continue
        name = clean_name(pair[0])
        try:
            where = pair[1].decode("ascii")
        except UnicodeDecodeError:
            continue
        if name is None or not _WHERE.fullmatch(where):
            continue
        host = host_of(where)
        s = {"id": source_id(name, host), "name": name, "from": where, "raw": pair}
        old = found.get(s["id"])
        if old is None or (where, pair) < (old["from"], old["raw"]):      # one name twice on one machine: one row, always the same one
            found[s["id"]] = s
    rows = sorted(found.values(), key=lambda s: (s["id"] != keep_id, host_of(s["from"]) not in prefer, s["name"].lower(), s["name"], s["from"]))
    kept = sorted(rows[:MAX_SOURCES], key=lambda s: (s["name"].lower(), s["name"], s["from"]))
    return kept, len(rows) > MAX_SOURCES or len(raw) > SCAN_ROWS


def clean_address(text):
    """An address to ask for sources besides mDNS: an IPv4 literal in a private or link-local range, as text."""
    if not isinstance(text, str) or not _IPV4.fullmatch(text):
        raise NdiError("enter an address like 192.168.1.20")
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        raise NdiError("enter an address like 192.168.1.20")
    if not any(ip in net for net in PRIVATE_NETS):
        raise NdiError("only addresses on a private network are taken (10.x, 172.16 to 172.31, 192.168.x, 169.254.x)")
    return str(ip)


def validate_saved(cfg):
    """The stored part of the settings (a user-editable file, and a settings import): {"addresses": [...]} and,
    since sound, "sound": true or false (left out of what is returned when it was left out: it then means true)."""
    if not isinstance(cfg, dict) or set(cfg) - {"addresses", "sound"}:
        raise NdiError("ndi must be an object with addresses and sound")
    if "sound" in cfg and not isinstance(cfg["sound"], bool):
        raise NdiError("ndi.sound must be true or false")
    items = cfg.get("addresses", [])
    if not isinstance(items, list) or len(items) > MAX_ADDRESSES:
        raise NdiError("at most %d NDI addresses" % MAX_ADDRESSES)
    out = []
    for a in items:
        if clean_address(a) != a or a in out:
            raise NdiError("bad or repeated NDI address")
        out.append(a)
    return dict({"addresses": out}, **({"sound": cfg["sound"]} if "sound" in cfg else {}))


SOUND_DEFAULT = True                      # a source's sound is played with its picture unless the owner says no (D62)


def saved_sound(section):
    """Whether sound is wanted, from a stored `ndi` section a person may have edited: anything but a plain true
    or false is the default."""
    value = section.get("sound") if isinstance(section, dict) else None
    return value if isinstance(value, bool) else SOUND_DEFAULT


def saved_addresses(section):
    """(the addresses of a stored `ndi` section that can be used, what was wrong with it in words or ""). The
    settings are a file a person may have edited: a section of the wrong kind gives no addresses, and a bad entry
    in a good list is left out, never an error that reaches the caller."""
    if section is None:
        return [], ""
    items = section.get("addresses", []) if isinstance(section, dict) else None
    if not isinstance(items, list):
        return [], "the saved NDI addresses could not be read and are not used"
    good = []
    for a in items[:MAX_ADDRESSES * 4]:
        try:
            if clean_address(a) == a and a not in good and len(good) < MAX_ADDRESSES:
                good.append(a)
        except NdiError:
            pass
    return good, ("" if len(good) == len(items) else "%d saved NDI address%s could not be used and %s left out"
                  % (len(items) - len(good), "" if len(items) - len(good) == 1 else "es", "was" if len(items) - len(good) == 1 else "were"))


class Wanted:
    """What the panel wants of the helper, read from the settings each time: (module on, addresses). Never raises;
    what is wrong with the stored section is kept in `problem` for the page and logged once when it changes."""

    def __init__(self, enabled, data, log=print):
        self._enabled, self._data, self.log, self.problem = enabled, data, log, ""
        self.sound = SOUND_DEFAULT                 # read with the rest at each call

    def __call__(self):
        try:
            data = self._data()
            section = data.get("ndi") if isinstance(data, dict) else None
            addresses, problem = saved_addresses(section)
            self.sound = saved_sound(section)
            on = bool(self._enabled())
        except Exception as e:
            on, addresses, problem = False, [], "the NDI settings could not be read (%s)" % type(e).__name__
            self.sound = SOUND_DEFAULT
        if problem != self.problem:
            self.problem = problem
            if problem:
                self.log("pvj-web: %s" % problem)
        return on, addresses


def _fourcc_text(n):
    raw = struct.pack("<I", n & 0xFFFFFFFF)
    return raw.decode("ascii") if all(0x20 < b < 0x7f for b in raw) else "0x%08x" % (n & 0xFFFFFFFF)


def frame_rate(n, d):
    """Frames a second from the sender's fraction; 30 for anything that is not a sensible rate."""
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (n, d)) or n <= 0 or d <= 0:
        return 30.0
    fps = n / d
    return round(fps, 3) if 1.0 <= fps <= 120.0 else 30.0


def check_frame(f):
    """((width, height, frames a second), line stride) of a video frame, or NdiError in words for the page. Nothing
    is read from the frame's memory before this has passed, and the size read comes from these numbers only."""
    w, h, stride, fourcc, fields = f.width, f.height, f.stride, f.fourcc, f.fields
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (w, h, stride, fourcc, fields)):
        raise NdiError("the source sent a frame that could not be read")
    # 1 is a progressive frame. 0 is a frame of two fields woven together, 2 and 3 are single fields: all three are
    # interlaced video, and all three are refused alike. The library is asked for its "fastest" format, in which
    # (its documentation says) fields are always allowed, so it never de-interlaces for us; see pvj/NDI.md.
    if fields in INTERLACED:
        raise NdiError("the source sends interlaced video, which this input does not show; set the sender to progressive")
    if fields != PROGRESSIVE:
        raise NdiError("the source sent a frame that could not be read")
    if fourcc not in (FOURCC_UYVY, FOURCC_UYVA):
        raise NdiError("the source sends a picture format this input does not read (%s)" % _fourcc_text(fourcc))
    if not (MIN_SIDE <= w <= MAX_WIDTH and MIN_SIDE <= h <= MAX_HEIGHT) or w % 2:
        raise NdiError("the source's picture size is not one this input shows (%d x %d; up to %d x %d, an even width)"
                       % (w, h, MAX_WIDTH, MAX_HEIGHT))
    if not 2 * w <= stride <= 2 * w + MAX_STRIDE_PAD or not f.address:
        raise NdiError("the source sent a frame that could not be read")
    return (w, h, frame_rate(f.fps_n, f.fps_d)), stride


def copy_frame(f, fmt, stride, out):
    """Copy the picture (never the alpha plane, never a line's padding) into `out`, which holds exactly one frame."""
    w, h = fmt[0], fmt[1]
    row = 2 * w
    src = f.view(stride * (h - 1) + row)
    if len(src) != stride * (h - 1) + row or len(out) != row * h:
        raise NdiError("the source sent a frame that could not be read")
    if stride == row:
        out[:] = src
        return
    dst = memoryview(out)
    for y in range(h):
        dst[y * row:(y + 1) * row] = src[y * stride:y * stride + row]


def check_audio(a):
    """(rate, the sender's channels, the channels read, samples a channel, bytes from one plane to the next) of a
    block of sound, or NdiError in words. Nothing of the block's memory is touched before this has passed, and
    what is read afterwards is computed from these checked numbers only."""
    rate, channels, n, stride = a.rate, a.channels, a.samples, a.stride
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (rate, channels, n, stride, a.fourcc)):
        raise NdiError("the source sent sound that could not be read")
    if a.fourcc != AUDIO_FLTP:
        raise NdiError("the source sends a sound format this input does not read (%s)" % _fourcc_text(a.fourcc))
    if rate not in AUDIO_RATES:
        raise NdiError("the source's sound is at %d samples a second, which this input does not play" % max(-1, min(rate, 10 ** 9)))
    if not 1 <= channels <= MAX_AUDIO_CHANNELS:
        raise NdiError("the source sent sound that could not be read")
    if not 1 <= n <= min(MAX_AUDIO_SAMPLES, rate):
        raise NdiError("the source sent sound that could not be read")
    if not 4 * n <= stride <= 4 * n + MAX_STRIDE_PAD or not a.address:
        raise NdiError("the source sent sound that could not be read")
    return rate, channels, min(channels, AUDIO_PLANES), n, stride


def audio_block(a, checked):
    """(the block as the pipe carries it: 32-bit floats, little-endian, channel after channel for each sample,
    brought down by AUDIO_GAIN and held to full scale; its loudest sample after the gain, 0.0 to 1.0; whether it
    was replaced by silence). A block with a sample that is not a number, is infinite, or is absurdly large is
    not played: it becomes silence of the same length, so that time goes on.

    The checks run at the speed of C (sum, max, min over an array); only the gain is a Python loop, 96000
    multiplications a second for stereo at 48 kHz."""
    _rate, _channels, planes, n, stride = checked
    cols, peak = [], 0.0
    for c in range(planes):
        col = array.array("f")
        col.frombytes(a.view(c * stride, 4 * n))
        if len(col) != n:
            raise NdiError("the source sent sound that could not be read")
        if sys.byteorder != "little":
            col.byteswap()                        # the library's floats are the machine's own; said for completeness
        if not math.isfinite(sum(col)):           # one NaN or infinity anywhere makes the sum one
            return bytes(4 * n * planes), 0.0, True
        top = max(max(col), -min(col))
        if top > AUDIO_ABSURD:
            return bytes(4 * n * planes), 0.0, True
        peak = max(peak, top)
        cols.append(col)
    g = AUDIO_GAIN
    if peak * g > 1.0:                            # louder than full scale after the gain: held there, here, not by whatever plays it
        cols = [array.array("f", [1.0 if x * g > 1.0 else -1.0 if x * g < -1.0 else x * g for x in col]) for col in cols]
    else:
        cols = [array.array("f", [x * g for x in col]) for col in cols]
    if planes == 1:
        out = cols[0]
    else:
        out = array.array("f", bytes(4 * n * planes))
        for c, col in enumerate(cols):
            out[c::planes] = col
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes(), min(1.0, peak * g), False


# ---- the container: Matroska, written here ----------------------------------------------------------------------------
# With sound the pipe cannot carry bare frames: a player that counts raw frames and raw samples to tell the time
# is put out of step for good by every frame this helper drops or writes twice. So the helper says when each frame
# and each block of sound belongs, in the smallest container that mpv reads itself from a pipe without seeking:
# Matroska with uncompressed video ("V_UNCOMPRESSED" and the FourCC UYVY) and float PCM ("A_PCM/FLOAT/IEEE").
# mpv's own reader (demux/demux_mkv.c, read for this at v0.35.0, v0.37.0 and v0.40.0) takes both, and takes a
# segment and clusters of unknown length "for streaming". Only what that reader needs is written.
MKV_TIME_UNIT = 1000                      # nanoseconds in one unit of time in the stream (so: microseconds)


def _ebml_size(n):
    """A length as EBML writes it, in the fewest bytes (never the all-ones pattern, which means "unknown")."""
    for width in range(1, 9):
        if n < (1 << (7 * width)) - 1:
            return ((1 << (7 * width)) | n).to_bytes(width, "big")
    raise NdiError("too large for the stream")


def _ebml(ident, payload):
    return bytes.fromhex(ident) + _ebml_size(len(payload)) + payload


def _ebml_uint(ident, n):
    return _ebml(ident, n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big"))


def mkv_header(width, height, fps, audio=None):
    """The start of the stream: what it is, and its tracks. Track 1 is the picture (UYVY, as the frames come);
    track 2, if `audio` is (rate, channels), the sound. The segment has no length: it ends when the pipe does."""
    head = _ebml("1A45DFA3", _ebml_uint("4286", 1) + _ebml_uint("42F7", 1) + _ebml_uint("42F2", 4) + _ebml_uint("42F3", 8)
                 + _ebml("4282", b"matroska") + _ebml_uint("4287", 2) + _ebml_uint("4285", 2))
    info = _ebml("1549A966", _ebml_uint("2AD7B1", MKV_TIME_UNIT) + _ebml("4D80", b"nxlx.mastercontrol") + _ebml("5741", b"nxlx.mastercontrol"))
    video = _ebml("AE", _ebml_uint("D7", 1) + _ebml_uint("73C5", 1) + _ebml_uint("83", 1) + _ebml_uint("9C", 0)
                  + _ebml("86", b"V_UNCOMPRESSED") + _ebml_uint("23E383", int(round(1e9 / fps)))
                  + _ebml("E0", _ebml_uint("B0", width) + _ebml_uint("BA", height) + _ebml("2EB524", b"UYVY")))
    tracks = video
    if audio is not None:
        rate, channels = audio
        tracks += _ebml("AE", _ebml_uint("D7", 2) + _ebml_uint("73C5", 2) + _ebml_uint("83", 2) + _ebml_uint("9C", 0)
                        + _ebml("86", b"A_PCM/FLOAT/IEEE")
                        + _ebml("E1", _ebml("B5", struct.pack(">d", float(rate))) + _ebml_uint("9F", channels) + _ebml_uint("6264", 32)))
    return head + bytes.fromhex("18538067" + "01FFFFFFFFFFFFFF") + info + _ebml("1654AE6B", tracks)


def mkv_block_head(track, seconds, size):
    """What goes in front of `size` bytes of one frame, or one block of sound, that belongs at `seconds`: a cluster
    of its own with its time and one block. The bytes themselves follow, written from where they are (a frame is
    never copied to be joined to this)."""
    block = bytes((0x80 | track, 0, 0, 0x80))     # the track, a time of 0 inside its cluster, "a whole picture by itself"
    when = _ebml_uint("E7", max(0, int(round(seconds * 1e9 / MKV_TIME_UNIT))))
    inner = when + bytes.fromhex("A3") + _ebml_size(len(block) + size) + block
    return bytes.fromhex("1F43B675") + _ebml_size(len(inner) + size) + inner


class Frame:
    """One captured frame as the seam hands it up: plain numbers, and `view(n)` for n bytes of its memory.
    `timestamp` is the sender's own clock in units of 100 ns, or None where the library gave none."""
    __slots__ = ("kind", "width", "height", "fourcc", "stride", "fps_n", "fps_d", "fields", "address", "view", "token", "timestamp")

    def __init__(self, kind, width=0, height=0, fourcc=0, stride=0, fps_n=0, fps_d=0, fields=1, address=0, view=None, token=None,
                 timestamp=None):
        self.kind, self.width, self.height, self.fourcc, self.stride = kind, width, height, fourcc, stride
        self.fps_n, self.fps_d, self.fields, self.address, self.view, self.token = fps_n, fps_d, fields, address, view, token
        self.timestamp = timestamp


class AudioFrame:
    """One captured block of sound as the seam hands it up: plain numbers, and `view(offset, n)` for n bytes of its
    memory from `offset`. Every number is the sender's and is checked (check_audio) before a byte is read."""
    __slots__ = ("rate", "channels", "samples", "fourcc", "stride", "address", "view", "token", "timestamp")

    def __init__(self, rate=0, channels=0, samples=0, fourcc=0, stride=0, address=0, view=None, token=None, timestamp=None):
        self.rate, self.channels, self.samples, self.fourcc, self.stride = rate, channels, samples, fourcc, stride
        self.address, self.view, self.token, self.timestamp = address, view, token, timestamp


# ---- the library, behind a seam ------------------------------------------------------------------------------------
class _Source(ctypes.Structure):
    _fields_ = [("name", ctypes.c_void_p), ("url", ctypes.c_void_p)]


class _FindCreate(ctypes.Structure):
    _fields_ = [("show_local", ctypes.c_bool), ("groups", ctypes.c_char_p), ("extra_ips", ctypes.c_char_p)]


class _RecvCreate(ctypes.Structure):
    _fields_ = [("source", _Source), ("color", ctypes.c_int), ("bandwidth", ctypes.c_int), ("fields", ctypes.c_bool),
                ("name", ctypes.c_char_p)]


class _Video(ctypes.Structure):
    _fields_ = [("xres", ctypes.c_int), ("yres", ctypes.c_int), ("fourcc", ctypes.c_uint32), ("fps_n", ctypes.c_int),
                ("fps_d", ctypes.c_int), ("aspect", ctypes.c_float), ("format", ctypes.c_int), ("timecode", ctypes.c_int64),
                ("data", ctypes.c_void_p), ("stride", ctypes.c_int), ("metadata", ctypes.c_void_p), ("timestamp", ctypes.c_int64)]


class _Audio(ctypes.Structure):
    _fields_ = [("rate", ctypes.c_int), ("channels", ctypes.c_int), ("samples", ctypes.c_int), ("timecode", ctypes.c_int64),
                ("fourcc", ctypes.c_uint32), ("data", ctypes.c_void_p), ("stride", ctypes.c_int), ("metadata", ctypes.c_void_p),
                ("timestamp", ctypes.c_int64)]


class _Perf(ctypes.Structure):
    _fields_ = [("video", ctypes.c_int64), ("audio", ctypes.c_int64), ("metadata", ctypes.c_int64)]


COLOR_FASTEST, BANDWIDTH_HIGHEST = 100, 100
ALLOW_FIELDS = True                       # what "fastest" implies whatever is asked; said outright, and pinned by a test
FRAME_VIDEO, FRAME_AUDIO, FRAME_ERROR = 1, 2, 4
TIMESTAMP_UNDEFINED = 2 ** 63 - 1           # what the library writes where it has no time for a frame
_CSTR_MAX = 512


def _cstr(address, limit=_CSTR_MAX):
    """The bytes of a C string, reading at most `limit`; None for one that is longer (it is not trusted to end)."""
    if not address:
        return b""
    p = ctypes.cast(address, ctypes.POINTER(ctypes.c_char))
    out = bytearray()
    for i in range(limit):
        c = p[i]
        if c == b"\0":
            return bytes(out)
        out += c
    return None


def _memory(address, n):
    """n bytes at an address as a plain byte view, without copying them. The caller has checked n."""
    view = memoryview((ctypes.c_ubyte * n).from_address(address))
    try:
        return view.cast("B")              # ctypes calls the format "<B"; a row copy needs plain "B"
    except TypeError:
        make = ctypes.pythonapi.PyMemoryView_FromMemory
        make.restype, make.argtypes = ctypes.py_object, [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        return make(address, n, 0x100)     # PyBUF_READ


def elf_machine(path):
    """(machine number, 32 or 64) from a file's ELF header, or None for a file that is not one."""
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"\x7fELF" or head[4] not in (1, 2) or head[5] not in (1, 2):
        return None
    return struct.unpack("<H" if head[5] == 1 else ">H", head[18:20])[0], 32 * head[4]


def box_machine(machine=None, pointer_bytes=None):
    """The ELF machine number this Python process can load."""
    machine = machine or os.uname().machine
    bits = 8 * (pointer_bytes or struct.calcsize("P"))
    if machine in ("aarch64", "arm64"):
        return 183 if bits == 64 else 40          # a 64-bit kernel under a 32-bit system
    if machine in ("x86_64", "amd64"):
        return 62 if bits == 64 else 3
    if machine.startswith("arm"):
        return 40
    if re.fullmatch(r"i[3-6]86", machine):
        return 3
    return None


def runtime_problem(path, owner_uids=(0,), machine=None):
    """None when the library file is there and safe to load, else the reason in words. Not a link, a plain file,
    its owner root, not writable by anyone else, and built for this processor."""
    try:
        st = os.lstat(path)
    except OSError:
        return "the NDI runtime is not on this box yet"
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in owner_uids or st.st_mode & 0o022 or st.st_size > LIB_MAX_BYTES:
        return "the NDI runtime file is not a plain file that only root can change; install it again with pvj-ndi-runtime"
    found, want = elf_machine(path), machine if machine is not None else box_machine()
    if found is None:
        return "the NDI runtime file is not a program library"
    if found[0] != want:
        return "the NDI runtime file is for %s, this box is %s" % (ELF_MACHINES.get(found[0], "another processor"),
                                                                   ELF_MACHINES.get(want, "another kind"))
    return None


class CtypesLibrary:
    """The only code that calls the NDI library. Every method is a few lines, checks nothing about content (that is
    done above, where it is tested), and never lets a pointer or a length from the library out as anything but a
    number or a bounded read."""

    def __init__(self, path):
        try:
            lib = ctypes.CDLL(path)
        except OSError as e:
            raise NdiError("the NDI runtime could not be loaded: %s" % str(e)[-160:])
        try:
            self._declare(lib)
        except AttributeError:
            raise NdiError("the NDI runtime is not a version this box knows (it needs NDI 5 or 6)")
        if not lib.NDIlib_initialize():
            raise NdiError("the NDI runtime does not run on this processor")
        self.lib = lib

    @staticmethod
    def _declare(lib):
        p, u32, i = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        for name, res, args in (
                ("NDIlib_initialize", ctypes.c_bool, []),
                ("NDIlib_version", p, []),
                ("NDIlib_find_create_v2", p, [ctypes.POINTER(_FindCreate)]),
                ("NDIlib_find_destroy", None, [p]),
                ("NDIlib_find_get_current_sources", p, [p, ctypes.POINTER(u32)]),
                ("NDIlib_recv_create_v3", p, [ctypes.POINTER(_RecvCreate)]),
                ("NDIlib_recv_destroy", None, [p]),
                ("NDIlib_recv_capture_v2", i, [p, ctypes.POINTER(_Video), p, p, u32]),
                ("NDIlib_recv_free_video_v2", None, [p, ctypes.POINTER(_Video)]),
                ("NDIlib_recv_get_performance", None, [p, ctypes.POINTER(_Perf), ctypes.POINTER(_Perf)])):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = res, args
        # Sound: two more calls. A library without them still gives the picture (has_audio says which).
        try:
            for name, res, args in (
                    ("NDIlib_recv_capture_v3", i, [p, ctypes.POINTER(_Video), ctypes.POINTER(_Audio), p, u32]),
                    ("NDIlib_recv_free_audio_v3", None, [p, ctypes.POINTER(_Audio)])):
                fn = getattr(lib, name)
                fn.restype, fn.argtypes = res, args
            lib._pvj_audio = True
        except AttributeError:
            lib._pvj_audio = False

    def version(self):
        raw = _cstr(self.lib.NDIlib_version(), 80) or b""
        return "".join(ch for ch in raw.decode("ascii", "replace") if " " <= ch <= "~")[:80]

    def find_open(self, extra_ips):
        """`extra_ips` is text made of checked IPv4 addresses and commas, nothing else."""
        self._ips = extra_ips.encode("ascii")             # kept: the library may read it later
        handle = self.lib.NDIlib_find_create_v2(ctypes.byref(_FindCreate(True, None, self._ips or None)))
        if not handle:
            raise NdiError("the NDI runtime could not start looking for sources (is avahi-daemon running?)")
        return handle

    def find_sources(self, handle):
        count = ctypes.c_uint32(0)
        first = self.lib.NDIlib_find_get_current_sources(handle, ctypes.byref(count))
        if not first:
            return []
        rows = ctypes.cast(first, ctypes.POINTER(_Source))
        out = []
        for n in range(min(count.value, SCAN_ROWS + 1)):      # one more than is used, so the caller knows it was cut
            name, url = _cstr(rows[n].name), _cstr(rows[n].url)
            if name is not None and url is not None:
                out.append((name, url))
        return out

    def find_close(self, handle):
        self.lib.NDIlib_find_destroy(handle)

    def recv_open(self, raw):
        """`raw` is the (name, address) pair the library itself gave for a source that passed clean_sources."""
        spec, keep = self.recv_spec(raw)
        handle = self.lib.NDIlib_recv_create_v3(ctypes.byref(spec))
        if not handle:
            raise NdiError("the NDI runtime could not open the source")
        return (handle, keep)

    @staticmethod
    def recv_spec(raw):
        """(what the library is asked for when a source is opened, the strings it points at). No library needed,
        so a test can read it: the fastest format, full bandwidth, fields allowed."""
        keep = [ctypes.create_string_buffer(raw[0]), ctypes.create_string_buffer(raw[1]), ctypes.create_string_buffer(b"nxlx.mastercontrol")]
        spec = _RecvCreate(_Source(ctypes.addressof(keep[0]), ctypes.addressof(keep[1])), COLOR_FASTEST, BANDWIDTH_HIGHEST, ALLOW_FIELDS,
                           ctypes.cast(keep[2], ctypes.c_char_p))
        return spec, keep

    def recv_capture(self, handle, timeout_ms):
        """None when nothing came in time; a Frame of kind "video" (to be given back with recv_free), or "lost"."""
        video = _Video()
        kind = self.lib.NDIlib_recv_capture_v2(handle[0], ctypes.byref(video), None, None, int(timeout_ms))
        if kind == FRAME_ERROR:
            return Frame("lost")
        if kind != FRAME_VIDEO:
            return None
        address = video.data or 0

        def view(n):
            return _memory(address, n)
        return Frame("video", video.xres, video.yres, video.fourcc, video.stride, video.fps_n, video.fps_d, video.format,
                     address, view, video, None if video.timestamp == TIMESTAMP_UNDEFINED else video.timestamp)

    @property
    def has_audio(self):
        return bool(getattr(self.lib, "_pvj_audio", False))

    def recv_audio(self, handle, timeout_ms):
        """None when no sound came in time (or the connection is down: the picture's thread is the one that says
        so); else an AudioFrame, to be given back with recv_free_audio. The library documents that the picture and
        the sound of one connection may be taken on two threads at once; this is the sound's call."""
        audio = _Audio()
        kind = self.lib.NDIlib_recv_capture_v3(handle[0], None, ctypes.byref(audio), None, int(timeout_ms))
        if kind != FRAME_AUDIO:
            return None
        address = audio.data or 0

        def view(offset, n):
            return _memory(address + offset, n)
        return AudioFrame(audio.rate, audio.channels, audio.samples, audio.fourcc, audio.stride, address, view, audio,
                          None if audio.timestamp == TIMESTAMP_UNDEFINED else audio.timestamp)

    def recv_free_audio(self, handle, frame):
        if frame.token is not None:
            self.lib.NDIlib_recv_free_audio_v3(handle[0], ctypes.byref(frame.token))

    def recv_free(self, handle, frame):
        if frame.token is not None:
            self.lib.NDIlib_recv_free_video_v2(handle[0], ctypes.byref(frame.token))

    def recv_dropped(self, handle):
        total, dropped = _Perf(), _Perf()
        self.lib.NDIlib_recv_get_performance(handle[0], ctypes.byref(total), ctypes.byref(dropped))
        return max(0, int(dropped.video))

    def recv_close(self, handle):
        self.lib.NDIlib_recv_destroy(handle[0])


def load_library(path):
    problem = runtime_problem(path)
    if problem:
        raise NdiError(problem)
    return CtypesLibrary(path)


# ---- one connection to one source ------------------------------------------------------------------------------------
class Receiver:
    """Receives one source and writes whole frames into the pipe. Two threads: one takes frames from the library
    into a buffer of its own and gives the library's frame straight back; one writes the newest whole frame to the
    pipe. Three buffers go round between them, so neither waits for the other and a frame is never written in part.

    With `sound` a third thread takes the source's sound from the library (the library documents that picture and
    sound of one connection may be taken on two threads at once), checks and converts each block, and queues it for
    the same writer. What the pipe then carries is decided once, after the first picture (decide): if sound came
    within AUDIO_WAIT it is Matroska, with a time on every frame and block; if none came it is bare frames, exactly
    as without sound. Sound that turns up later, or changes its rate or channels, ends this receiver as "changed",
    and the panel opens the source again, as it does when a picture changes size.

    Time in the stream is this helper's own clock, never the sender's. Measured against real senders (2026-10-08):
    the library's time on a block of sound is when it was sent, jittering by tens of milliseconds around blocks of
    exactly 100, and a still picture carried a time more than two hours older than its sound. So a frame belongs
    where it arrived, and sound is placed by counting its samples, held within AUDIO_WINDOW of the clock: when it
    falls behind (a gap) the count starts again at the clock, and when it runs ahead a block is dropped."""

    def __init__(self, lib, source, fifo, log=print, clock=time.monotonic, pipe_wait=PIPE_OPEN_SECONDS, join_wait=3.0,
                 sound=False, audio_wait=AUDIO_WAIT):
        self.lib, self.source, self.fifo, self.log, self._clock, self._pipe_wait = lib, source, fifo, log, clock, pipe_wait
        self._join_wait = join_wait
        self.sound = bool(sound) and bool(getattr(lib, "has_audio", False))
        self._audio_wait = audio_wait
        # "raw" or "matroska". Without sound it is bare frames from the start. With sound it is decided once, after
        # the first picture (decide), and the writer waits for that.
        self.container = None if self.sound else "raw"
        self.audio = None                          # {"rate", "channels" (the sender's), "played"} once a good block came
        self.audio_counts = {"received": 0, "written": 0, "dropped": 0, "refused": 0, "silenced": 0, "filled": 0}
        self._audio_seen = threading.Event()
        self._audio_problem = ""                   # why the last block was refused, for the page
        self._queue = collections.deque()          # (seconds, bytes, length in seconds) waiting for the writer
        self._queued = 0.0
        self._peaks = collections.deque()          # (clock, loudest sample) of the blocks of the last second
        self._t0 = None                            # the clock when the player took the pipe: the stream's time 0
        self._at = 0.0                             # the clock when the pending frame arrived
        self._apts = None                          # where the next block of sound belongs, by count
        self._users = 0                            # threads that may be inside the library with the connection
        self.state, self.message, self.format = "connecting", "", None
        self.counts = {"received": 0, "shown": 0, "dropped": 0, "repeated": 0}
        self.first = threading.Event()             # a first good frame, or the end
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._pending, self._free = None, []
        self._last = clock()
        self._lost = False                         # the library said the connection dropped, and no frame has come since
        self._handle = None
        self._handle_lock = threading.Lock()       # the connection is used by the capture thread and asked about by status
        self._threads = []

    def _release(self):
        """Give the connection back to the library, once."""
        with self._handle_lock:
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    self.lib.recv_close(handle)
                except Exception as e:
                    self.log("pvj-ndi: closing the source: %s" % e)

    def _leave(self):
        """A thread that used the connection is out of the library for good. The last one out gives the connection
        back at once, so a source nobody watches any more is let go without waiting for anyone to ask; never an
        earlier one, which would free it under the thread still inside (the picture's and the sound's threads end
        at different moments)."""
        with self._cond:
            self._users -= 1
            last = self._users <= 0
        if last:
            self._release()

    def start(self):
        self._handle = self.lib.recv_open(self.source["raw"])
        self._threads = [threading.Thread(target=self._capture, name="ndi-capture", daemon=True),
                         threading.Thread(target=self._writer, name="ndi-writer", daemon=True)]
        if self.sound:
            self._threads.append(threading.Thread(target=self._capture_audio, name="ndi-audio", daemon=True))
        self._users = len(self._threads) - 1       # every thread but the writer, which never touches the library
        for t in self._threads:
            t.start()

    def decide(self):
        """Called once after the first picture: wait a moment for sound, then fix what the pipe carries. Returns it."""
        if self.sound and self.format is not None and not self._stop.is_set():
            self._audio_seen.wait(self._audio_wait)
        with self._cond:
            if self.container is None:
                self.container = "matroska" if self.audio is not None else "raw"
            self._cond.notify_all()
            return self.container

    def _end(self, state, message):
        with self._cond:
            if self.state not in ENDED:
                self.state, self.message = state, message
            self._stop.set()
            self._cond.notify_all()
        self.first.set()
        self._audio_seen.set()

    def _capture(self):
        spare, lost = None, False
        try:
            while not self._stop.is_set():
                f = self.lib.recv_capture(self._handle, 250)
                if f is not None and f.kind == "lost":
                    # The library says the connection dropped, and says so again at once for as long as it is down:
                    # wait as long as a capture would have, and write it down once, not at every turn.
                    if not lost:
                        lost = self._lost = True
                        self.log("pvj-ndi: the connection to %s dropped; waiting for it" % self.source["name"])
                    self._stop.wait(0.25)
                    continue
                if f is None:
                    continue
                if lost:
                    lost = self._lost = False
                    self.log("pvj-ndi: %s is back" % self.source["name"])
                try:
                    fmt, stride = check_frame(f)
                    if self.format is None:
                        self._free = [bytearray(2 * fmt[0] * fmt[1]) for _ in range(3)]
                        spare = self._free.pop()
                        self.format = fmt
                    elif fmt != self.format:
                        return self._end("changed", "the source changed to %d x %d at %s frames a second" % fmt)
                    copy_frame(f, fmt, stride, spare)
                except NdiError as e:
                    return self._end("refused", str(e))
                finally:
                    self.lib.recv_free(self._handle, f)
                with self._cond:
                    counting = self.state == "playing"     # frames from before the screen took the pipe are not counted
                    self.counts["received"] += counting
                    self._last = self._at = self._clock()
                    if self._pending is not None:          # the screen has not taken the last one: the newest wins
                        self.counts["dropped"] += counting
                        spare, self._pending = self._pending, spare
                    else:
                        self._pending, spare = spare, self._free.pop()
                    if self.state == "connecting":
                        self.state = "ready"
                    self._cond.notify_all()
                self.first.set()
        except Exception as e:                             # a fault here must end as a state, never as a silent thread
            self._end("stopped", "the NDI input stopped: %s" % str(e)[-160:])
        finally:
            self._leave()

    def _capture_audio(self):
        """The sound's thread. A block that cannot be read is counted and left out; it never ends the picture."""
        try:
            while not self._stop.is_set():
                a = self.lib.recv_audio(self._handle, 250)
                if a is None:
                    continue
                try:
                    try:
                        checked = check_audio(a)
                        data, peak, silenced = audio_block(a, checked)
                    except NdiError as e:
                        with self._cond:
                            self.audio_counts["refused"] += 1
                            self._audio_problem = str(e)
                        continue
                finally:
                    self.lib.recv_free_audio(self._handle, a)
                rate, channels, planes, n = checked[0], checked[1], checked[2], checked[3]
                with self._cond:
                    if self.audio is None:
                        if self.container is not None:     # the pipe already carries bare frames: start again, with sound
                            return self._end("changed", "the source started to send sound")
                        self.audio = {"rate": rate, "channels": channels, "played": planes}
                        self._audio_seen.set()
                    elif (rate, channels) != (self.audio["rate"], self.audio["channels"]):
                        return self._end("changed", "the source's sound changed to %d channels at %d samples a second" % (channels, rate))
                    self._audio_problem = ""
                    if self.state != "playing" or self._t0 is None or self.container != "matroska":
                        continue                           # sound from before the screen took the pipe is not kept
                    now, length = self._clock(), n / float(rate)
                    self.audio_counts["received"] += 1
                    self.audio_counts["silenced"] += silenced
                    self._peaks.append((now, peak))
                    while self._peaks and now - self._peaks[0][0] > 1.0:
                        self._peaks.popleft()
                    # A block is sent when its last sample exists, so it belongs one block's length before it came.
                    here = max(0.0, now - self._t0 - length)
                    if self._apts is None or here - self._apts > AUDIO_WINDOW:
                        self._apts = max(here, self._apts or 0.0)       # the first block, or after a gap: from the clock
                    elif self._apts - here > AUDIO_WINDOW:
                        self.audio_counts["dropped"] += 1               # the count ran ahead of the clock: this one is left out
                        continue
                    self._queue.append((self._apts, data, length))
                    self._apts += length
                    self._queued += length
                    while self._queued > AUDIO_QUEUE_SECONDS and self._queue:      # the player is not taking it: the oldest goes
                        self._queued -= self._queue.popleft()[2]
                        self.audio_counts["dropped"] += 1
                    self._cond.notify_all()
        except Exception as e:
            self._end("stopped", "the NDI input stopped: %s" % str(e)[-160:])
        finally:
            self._leave()

    def _open_pipe(self):
        """The pipe, opened for writing once the player reads it; None when stopped or nobody came."""
        deadline = None
        while not self._stop.is_set():
            if self.format is None or self.container is None:
                self.first.wait(0.05 if self.format is not None else 0.25)
                continue
            deadline = deadline or self._clock() + self._pipe_wait
            try:
                fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            except OSError as e:
                if e.errno != errno.ENXIO or self._clock() > deadline:
                    self._end("stopped", "the player did not open the input")
                    return None
                self._stop.wait(0.05)
                continue
            st = os.fstat(fd)
            if not stat.S_ISFIFO(st.st_mode) or st.st_uid != os.getuid():
                os.close(fd)
                self._end("stopped", "the input pipe was replaced; not writing to it")
                return None
            try:
                # Fewer wake-ups for a 4 MB frame. An account without privileges gets at most
                # /proc/sys/fs/pipe-max-size (1 MiB unless the box raises it); asked for from large to small.
                for size in (8, 4, 1):
                    try:
                        fcntl.fcntl(fd, getattr(fcntl, "F_SETPIPE_SZ", 1031), size * 1024 * 1024)
                        break
                    except OSError:
                        continue
            except Exception:
                pass
            return fd
        return None

    def _write(self, fd, buf):
        """All of one frame, or False when stopped or the reader has gone. The pipe stays non-blocking so a stop is
        seen within a quarter of a second even while the player is not reading."""
        view, done = memoryview(buf), 0
        while done < len(view):
            if self._stop.is_set():
                return False
            try:
                _, ready, _ = select.select([], [fd], [], 0.25)
                if ready:
                    done += os.write(fd, view[done:])
            except BlockingIOError:
                continue
            except OSError:
                self._end("stopped", PLAYER_LEFT)
                return False
        return True

    def _write_frame(self, fd, buf, seconds):
        """One whole picture. In the container its few bytes of heading go first and the frame follows from the
        buffer it is in: a frame is never copied to be joined to its heading."""
        if self.container == "matroska":
            return self._write(fd, mkv_block_head(1, seconds, len(buf))) and self._write(fd, buf)
        return self._write(fd, buf)

    def _write_sound(self, fd, blocks, upto):
        """The queued blocks of sound, then silence for any stretch the sound has left open before the picture at
        `upto`: a player that waits for sound it will not get would hold the picture back with it. Returns False
        when the pipe has gone."""
        rate, planes = self.audio["rate"], self.audio["played"]
        for seconds, data, length in blocks:
            if not self._write(fd, mkv_block_head(2, seconds, len(data)) + data):
                return False
            self._sound_end = max(self._sound_end, seconds + length)
            with self._cond:
                self.audio_counts["written"] += 1
        if upto - self._sound_end > AUDIO_WINDOW:
            start = max(self._sound_end, upto - AUDIO_QUEUE_SECONDS)
            n = int((upto - 0.1 - start) * rate)
            if n > 0:
                if not self._write(fd, mkv_block_head(2, start, 4 * n * planes) + bytes(4 * n * planes)):
                    return False
                self._sound_end = start + n / float(rate)
                with self._cond:
                    self.audio_counts["filled"] += 1
                    if self._apts is None or self._apts < self._sound_end:
                        self._apts = self._sound_end       # real sound, when it comes, goes after the silence
        return True

    def _writer(self):
        fd = None
        try:
            fd = self._open_pipe()
            if fd is None:
                return
            mkv = self.container == "matroska"
            if mkv and not self._write(fd, mkv_header(self.format[0], self.format[1], self.format[2],
                                                      (self.audio["rate"], self.audio["played"]))):
                return
            with self._cond:
                self._t0 = self._clock()
                if self.state == "ready":
                    self.state = "playing"
            self._sound_end = 0.0
            # `last` is the frame written most recently. It is kept (not given back) until the next one is taken, so
            # that it can be written once more if nothing follows it (REPEAT_AFTER). The writer so never holds more
            # than one buffer at a time, which is what the capture thread's three buffers allow for.
            last, wrote_at, repeated, shown_at = None, 0.0, True, -1.0
            while not self._stop.is_set():
                again, blocks, buf, at = False, (), None, 0.0
                with self._cond:
                    while self._pending is None and not self._queue and not self._stop.is_set():
                        if last is not None and not repeated and self._clock() - wrote_at >= REPEAT_AFTER:
                            again = True
                            break
                        self._cond.wait(0.05 if last is not None and not repeated else 0.25)
                    if self._queue:
                        blocks, self._queued = tuple(self._queue), 0.0
                        self._queue.clear()
                    if not again and self._pending is not None:
                        buf, self._pending, at = self._pending, None, self._at
                        if last is not None:
                            self._free.append(last)
                            last = None
                if self._stop.is_set() and buf is None and not blocks:
                    break
                now = self._clock() - self._t0
                if mkv and (blocks or buf is not None or again) and not self._write_sound(fd, blocks, now):
                    break
                if again:
                    repeated = True                        # once: a still picture is not written over and over
                    shown_at = max(shown_at + 0.001, now)
                    if self._write_frame(fd, last, shown_at):
                        with self._cond:
                            self.counts["repeated"] += 1
                    continue
                if buf is None:
                    continue
                shown_at = max(shown_at + 0.001, at - self._t0)        # where it arrived; never before the one before it
                ok = self._write_frame(fd, buf, shown_at)
                last, wrote_at, repeated = buf, self._clock(), not ok
                with self._cond:
                    if ok:
                        self.counts["shown"] += 1
        except Exception as e:
            self._end("stopped", "the NDI input stopped: %s" % str(e)[-160:])
        finally:
            if fd is not None:
                os.close(fd)

    def status(self):
        with self._cond:
            state = self.state
            if state == "playing" and self._lost:
                state = "waiting"
            elif state == "playing" and self._clock() - self._last > QUIET_SECONDS:
                state = "still"
            out = {"id": self.source["id"], "name": self.source["name"], "state": state, "message": self.message,
                   "counts": dict(self.counts), "container": self.container or "raw"}
            if self.format:
                out.update(width=self.format[0], height=self.format[1], fps=self.format[2])
            if self.sound:
                now = self._clock()
                peaks = [pk for when, pk in self._peaks if now - when <= 1.0]
                top = max(peaks) if peaks else 0.0
                level = round(20 * math.log10(top), 1) if top > 0 else None
                out["audio"] = {"rate": self.audio["rate"] if self.audio else 0, "channels": self.audio["channels"] if self.audio else 0,
                                "played": self.audio["played"] if self.audio else 0,
                                "arriving": bool(peaks), "level_db": level,
                                "silent": level is None or level < AUDIO_SILENT_DB,
                                "problem": self._audio_problem, "counts": dict(self.audio_counts)}
        with self._handle_lock:
            if self._handle is not None and state in ("playing", "still", "waiting"):
                try:
                    out["counts"]["dropped_by_runtime"] = self.lib.recv_dropped(self._handle)
                except Exception:
                    pass
        return out

    def close(self):
        """Stop the threads and give the connection back. Never called from those threads, and never with a lock
        they need (LESSONS: do not join a thread while holding its lock). The pipe is not this object's to remove:
        the Service made it and the Service takes it away.

        The connection is never given back while a thread may still be inside the library with it (the picture's or
        the sound's). The last of them out gives it back itself; if one will not come out, the connection is left
        open (and said in the log) rather than freed under the library's feet. The helper is restarted by systemd
        if that ever matters."""
        self._end("stopped", "")
        threads, self._threads = self._threads, []
        for t in threads:
            t.join(timeout=self._join_wait)
        stuck = [t.name for t in threads if t.is_alive()]
        if "ndi-capture" in stuck or "ndi-audio" in stuck:
            self.log("pvj-ndi: the NDI runtime did not come back from %s; its connection is left open, not freed under it" % self.source["name"])
        else:
            self._release()


# ---- the daemon ------------------------------------------------------------------------------------------------------
class Service:
    """What pvj-ndi answers, one JSON line each: status, configure, open, close. It does nothing on the network until
    the panel has said the module is on (configure), and it knows no settings but what the panel sends."""

    def __init__(self, rundir, lib_path, loader=load_library, problem=runtime_problem, log=print, clock=time.monotonic,
                 first_frame=FIRST_FRAME_SECONDS, receiver=Receiver):
        self.rundir, self.lib_path, self._loader, self._problem, self.log, self._clock = rundir, lib_path, loader, problem, log, clock
        self._first_frame, self._receiver = first_frame, receiver
        self.on_unload = None                      # called when the module is switched off with the library loaded (main: exit)
        self.fifo = os.path.join(rundir, paths.NDI_FIFO)
        self.lock = threading.RLock()              # configure, open and close, one at a time
        self._recv_lock = threading.Lock()         # self.receiver: set and read under it, never held while anything waits
        self._find_lock = threading.Lock()         # the finder: asked by status, replaced by configure
        self.lib, self.lib_error, self.version = None, "", ""
        self.configured, self.on, self.addresses = False, False, []
        self.sound = True                          # play a source's sound with its picture (the panel says; D62)
        self._finder, self._sources, self._sources_at, self._cut = None, [], None, False
        self.receiver = None

    def _load(self):
        if self.lib is None:
            try:
                self.lib = self._loader(self.lib_path)
                self.version, self.lib_error = self.lib.version(), ""
                self.log("pvj-ndi: loaded the NDI runtime (%s)" % self.version)
            except NdiError as e:
                self.lib_error = str(e)
        return self.lib

    def _close_finder(self):
        with self._find_lock:
            finder, self._finder, self._sources, self._sources_at, self._cut = self._finder, None, [], None, False
            if finder is not None:
                self.lib.find_close(finder)

    def configure(self, message):
        on, addresses, sound = message.get("on"), message.get("addresses"), message.get("sound", True)
        if not isinstance(on, bool) or not isinstance(sound, bool):
            return {"ok": False, "error": "on and sound must be true or false"}
        try:
            addresses = validate_saved({"addresses": addresses})["addresses"]
        except NdiError as e:
            return {"ok": False, "error": str(e)}
        with self.lock:
            if not on:
                self._close_receiver()
                self._close_finder()
                # "Off" means no NDI code running. A library cannot be unloaded from a process, so the process ends
                # (after this answer has gone) and systemd starts a new one, which loads nothing until told "on".
                if self.lib is not None and self.on_unload is not None:
                    self.log("pvj-ndi: the NDI input was switched off; starting again without the NDI runtime")
                    self.on_unload()
            elif self._load() is not None and (self._finder is None or addresses != self.addresses):
                self._close_finder()
                try:
                    with self._find_lock:
                        self._finder = self.lib.find_open(",".join(addresses))
                except NdiError as e:
                    self.lib_error = str(e)
            if on and sound != self.sound:         # a source that is open goes on in its old form until it is opened again
                with self._recv_lock:
                    r = self.receiver
                if r is not None and r.state not in ENDED:
                    r._end("changed", "sound was switched %s" % ("on" if sound else "off"))
            self.configured, self.on, self.addresses, self.sound = True, on, addresses, sound
        return self.status()

    def sources(self):
        with self._find_lock:
            if self._finder is None:
                return []
            now = self._clock()
            if self._sources_at is None or now - self._sources_at >= SOURCES_CACHE:
                with self._recv_lock:
                    r = self.receiver
                try:
                    self._sources, self._cut = clean_sources(self.lib.find_sources(self._finder), self.addresses,
                                                             r.source["id"] if r is not None else None)
                except Exception as e:
                    self.log("pvj-ndi: reading the sources: %s" % e)
                    self._sources, self._cut = [], False
                self._sources_at = now
            return list(self._sources)

    def runtime(self):
        if self.lib is not None:
            return {"present": True, "loaded": True, "version": self.version, "problem": self.lib_error}
        problem = self._problem(self.lib_path)
        return {"present": problem is None, "loaded": False, "version": "", "problem": problem or self.lib_error}

    def _remove_pipe(self):
        """Only here is the pipe taken away. A reader still waiting on it (an old player blocked in opening it,
        a player waiting for a first byte) is woken first: a writer that opens and closes gives it the end."""
        try:
            wake = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            os.close(wake)
        except OSError:
            pass
        try:
            os.unlink(self.fifo)
        except OSError:
            pass

    def status(self):
        with self._recv_lock:
            r = self.receiver
        if r is not None and r.state in ENDED and self.lock.acquire(blocking=False):
            try:                                   # an ended receiver lets go of its source; what it said stays
                with self._recv_lock:
                    mine = self.receiver is r
                if mine:
                    r.close()
                    self._remove_pipe()
            finally:
                self.lock.release()
        return {"ok": True, "configured": self.configured, "on": self.on, "addresses": list(self.addresses), "sound": self.sound,
                "runtime": self.runtime(),
                "sources": [{"id": s["id"], "name": s["name"], "from": s["from"]} for s in self.sources()], "cut": self._cut,
                "playing": r.status() if r is not None else None}

    def _close_receiver(self):
        with self._recv_lock:
            r, self.receiver = self.receiver, None
        if r is not None:
            r.close()
        self._remove_pipe()

    def open(self, message):
        sid = message.get("id")
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            return {"ok": False, "error": "no such source"}
        with self.lock:
            if not self.on or self.lib is None or self._finder is None:
                return {"ok": False, "error": self.lib_error or "the NDI input is switched off"}
            with self._find_lock:
                self._sources_at = None            # ask the library again: the list on the page may be a second old
            match = [s for s in self.sources() if s["id"] == sid]
            if not match:
                return {"ok": False, "error": "that source is not on the network now"}
            self._close_receiver()
            try:
                os.mkfifo(self.fifo, 0o640)        # the helper writes; the player reads through the helper's group
            except OSError as e:
                self.log("pvj-ndi: could not make the pipe %s: %s" % (self.fifo, e))
                return {"ok": False, "error": "the NDI helper could not make its pipe (%s)" % (e.strerror or "error")}
            r = self._receiver(self.lib, match[0], self.fifo, log=self.log, sound=self.sound)
            try:
                r.start()
            except NdiError as e:
                r.close()
                self._remove_pipe()
                return {"ok": False, "error": str(e)}
            with self._recv_lock:
                self.receiver = r
            r.first.wait(self._first_frame)
            if r.format:
                r.decide()                         # a moment for sound to show itself; then the pipe's form is fixed
            st = r.status()
            if st["state"] in ENDED or not r.format:
                self._close_receiver()
                return {"ok": False, "error": st["message"] or "%s sent no picture in %d seconds" % (match[0]["name"], self._first_frame)}
            self.log("pvj-ndi: %s is %d x %d at %s" % ((match[0]["name"],) + r.format))
            return {"ok": True, "playing": st}

    def close(self):
        with self.lock:
            self._close_receiver()
        return {"ok": True}

    def handle(self, message):
        if not isinstance(message, dict):
            return {"ok": False, "error": "bad request"}
        cmd = message.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd == "configure":
            return self.configure(message)
        if cmd == "open":
            return self.open(message)
        if cmd == "close":
            return self.close()
        return {"ok": False, "error": "unknown command"}


def main(argv=None):
    import pwd
    from .netd import NetServer
    rundir = paths.ndi_dir()
    os.makedirs(rundir, mode=0o750, exist_ok=True)
    allowed = set()
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        allowed.add(os.getuid())                   # on a desk: whoever runs it
    service = Service(rundir, os.environ.get("PVJ_NDI_LIB") or os.path.join(LIB_DIR, LIB_NAME), log=lambda m: print(m, flush=True))
    try:
        os.unlink(service.fifo)                    # a pipe left by a run that was killed
    except OSError:
        pass
    service.on_unload = lambda: threading.Timer(0.5, os._exit, [0]).start()      # Restart=always brings up a clean one
    server = NetServer(os.path.join(rundir, paths.NDI_SOCKET), service, lambda uid: uid in allowed)
    print("pvj-ndi: ready (%s)" % (service.runtime()["problem"] or "the NDI runtime is in place"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
    return 0


# ---- the panel's side --------------------------------------------------------------------------------------------------
def json_depth(raw):
    """How deep the brackets of a JSON text nest, not counting brackets inside its strings (a source may be named
    "Cam [2]", and sixty-four of those are not a deep answer)."""
    depth = deepest = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5c:             # a backslash: the next byte is not the end of the string
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in b"[{":
            depth += 1
            deepest = max(deepest, depth)
        elif byte in b"]}":
            depth = max(0, depth - 1)      # closing brackets that open nothing do not buy depth for later
    return deepest


def helper_uid():
    """The uid of the helper's account, whose pipe the player may be told to read; our own where there is none (a desk)."""
    import pwd
    try:
        return pwd.getpwnam("pvj-ndi").pw_uid
    except KeyError:
        return os.getuid()


class Client:
    """One request, one reply. A status question never holds the panel up: a short wait, and a reply kept a second."""

    def __init__(self, path, timeout=2.0, clock=time.monotonic):
        self.path, self.timeout, self._clock = path, timeout, clock
        self._status = None

    MAX_NESTING = 64                       # a real answer nests five deep

    def _exchange(self, message, timeout):
        from .netd import exchange
        return exchange(self.path, message, timeout)

    def request(self, message, timeout=None):
        """The helper's answer as a dict, or NdiError: nothing else ever comes out of here, whatever the helper
        sends (it is the part that could be taken over). A reply of brackets nested thousands deep makes the JSON
        reader of Python 3.9 to 3.12 raise RecursionError, which is not a ValueError; such a reply is refused
        before it is read, and the error is caught as well."""
        import json
        try:
            raw = self._exchange(message, timeout or self.timeout)
        except (OSError, ValueError):
            raise NdiError("the NDI helper (pvj-ndi) is not running")
        try:
            if json_depth(raw) > self.MAX_NESTING:
                raise ValueError("nested too deep")
            reply = json.loads(raw)
        except (ValueError, RecursionError, MemoryError):
            raise NdiError("the NDI helper (pvj-ndi) gave a bad answer")
        if not isinstance(reply, dict):
            raise NdiError("the NDI helper (pvj-ndi) gave a bad answer")
        self._status = None
        return reply

    def status(self):
        now = self._clock()
        if self._status and now - self._status[0] < 1.0:
            return self._status[1]
        try:
            reply = self.request({"cmd": "status"})
        except NdiError:
            reply = {"ok": False}
        self._status = (now, reply)
        return reply


def _text(v, limit=200):
    """Words from the helper for the page: nothing that could change how the line reads (control characters, the
    marks that turn text round, line breaks), and then cut to a length. Scrubbed first, cut after."""
    if not isinstance(v, str):
        return ""
    return "".join(ch for ch in v[:limit * 8] if unicodedata.category(ch) not in _BAD_CATEGORIES)[:limit]


def _playing(p):
    """The helper's account of what plays, re-checked: the helper is the part that could be taken over."""
    if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not _ID.fullmatch(p["id"]) or p.get("state") not in STATES:
        return None
    out = {"id": p["id"], "name": clean_name(p.get("name")) or "NDI source", "state": p["state"], "message": _text(p.get("message"))}
    for key, low, high in (("width", MIN_SIDE, MAX_WIDTH), ("height", MIN_SIDE, MAX_HEIGHT)):
        v = p.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high:
            out[key] = v
    fps = p.get("fps")
    if isinstance(fps, (int, float)) and not isinstance(fps, bool) and 1 <= fps <= 120:
        out["fps"] = round(float(fps), 3)
    counts = p.get("counts") if isinstance(p.get("counts"), dict) else {}
    out["counts"] = {k: counts[k] for k in ("received", "shown", "dropped", "dropped_by_runtime", "repeated")
                     if isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool) and 0 <= counts[k] < 2 ** 53}
    out["container"] = p["container"] if p.get("container") in CONTAINERS else "raw"
    out["audio"] = _audio(p.get("audio"))
    if out["container"] == "matroska" and (out["audio"] is None or not out["audio"]["rate"]):
        out["container"] = "raw"           # a stream with sound must say what its sound is; otherwise it is read as bare frames
    return out


def _audio(a):
    """The helper's account of a source's sound, re-checked field by field; None when it gave none (sound is off)."""
    if not isinstance(a, dict):
        return None

    def whole(v, low, high):
        return v if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high else 0
    rate = a.get("rate") if a.get("rate") in AUDIO_RATES else 0
    level = a.get("level_db")
    level = round(float(level), 1) if isinstance(level, (int, float)) and not isinstance(level, bool) and math.isfinite(level) and -200 <= level <= 0 else None
    counts = a.get("counts") if isinstance(a.get("counts"), dict) else {}
    return {"rate": rate, "channels": whole(a.get("channels"), 1, MAX_AUDIO_CHANNELS) if rate else 0,
            "played": whole(a.get("played"), 1, AUDIO_PLANES) if rate else 0,
            "arriving": a.get("arriving") is True, "level_db": level, "silent": a.get("silent") is not False,
            "problem": _text(a.get("problem")),
            "counts": {k: counts[k] for k in ("received", "written", "dropped", "refused", "silenced", "filled")
                       if isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool) and 0 <= counts[k] < 2 ** 53}}


class Input:
    """The panel's handle on the NDI helper: keeps it told of the switch and the saved addresses, opens a source for
    the player, and notices when the helper says the picture changed size."""

    RETRY_SECONDS, RETRY_MAX = 3.0, 60.0   # between tries to show a source again: 3 s, then twice as long each time, to a minute

    def __init__(self, client, fifo, wanted, log=print, clock=time.monotonic, pipe_owner=os.getuid, setup=True):
        self.client, self.fifo, self._wanted, self.log, self._clock = client, fifo, wanted, log, clock
        # False on a box where nobody ran the opt-in command (server.py): there is no helper and never was, so its
        # socket is not even tried, and the page says how to set NDI up in place of "the helper is not running".
        self.setup = setup
        self._pipe_owner = pipe_owner      # the uid the helper's pipe must belong to (server.py: the pvj-ndi account)
        self.current = None                # {"id", "name"} while the player reads the pipe
        self.lock = threading.RLock()      # open, load in the player and note, as one step (like the capture input)
        self._retry_at, self._retry_wait, self._retry_said = 0.0, self.RETRY_SECONDS, ""
        self.ended = None                  # {"name", "message"}: why the last source ended by itself, for the page
        self._ticket = 0
        # Held by whoever is deciding what the screen shows next, for as long as the deciding and the loading take:
        # the NDI play holds it from "is my ticket still good" to the end of loading the pipe, and everything else
        # that loads or clears the player goes through cancel() first, which waits for it. So the two can only
        # happen one after the other, and the later one is the one that stays on the screen.
        self.screen = threading.RLock()

    def ticket(self):
        """Taken before a source is opened (which can take seconds). `still(ticket)` afterwards says whether
        nothing else was played or stopped meanwhile: cancel() makes every ticket taken before it worthless."""
        with self.screen:
            self._ticket += 1
            return self._ticket

    def still(self, ticket):
        with self.screen:
            return self._ticket == ticket

    def cancel(self):
        """Something else is about to be loaded or the screen cleared: a source still connecting is not wanted."""
        self.ticket()

    def sound_wanted(self):
        """Whether the owner wants a source's sound played (read by the last call of what is wanted; else the default)."""
        v = getattr(self._wanted, "sound", SOUND_DEFAULT)
        return v if isinstance(v, bool) else SOUND_DEFAULT

    def sync(self):
        """Tell the helper whether the module is on, which addresses to ask, and whether sound is wanted. Returns its
        status, or {"ok": False}."""
        if not self.setup:
            return {"ok": False}
        try:
            on, addresses = self._wanted()
            reply = self.client.request({"cmd": "configure", "on": on, "addresses": addresses, "sound": self.sound_wanted()}, timeout=10)
        except Exception as e:             # never out of here: this runs while the panel starts
            if not isinstance(e, NdiError):
                self.log("pvj-web: the NDI helper was not told the settings: %s" % type(e).__name__)
            return {"ok": False}
        if reply.get("ok") is not True:    # it answered, and refused: that is not "not running", and the page says which
            return {"ok": False, "answered": True, "error": _text(reply.get("error")) or "the NDI helper refused the settings"}
        return reply

    def status(self):
        """What the page shows. Every string and number from the helper is checked again here."""
        on, addresses = self._wanted()
        st = self.client.status() if self.setup else {"ok": False}
        sound = self.sound_wanted()
        if st.get("ok") and (st.get("configured") is not True or st.get("on") is not on or st.get("addresses") != list(addresses)
                             or ("sound" in st and st.get("sound") is not sound)):
            st = self.sync()
        helper = st.get("ok") is True or st.get("answered") is True
        notice = [x for x in (getattr(self._wanted, "problem", ""), _text(st.get("error")) if st.get("answered") is True else "") if x]
        rt = st.get("runtime") if isinstance(st.get("runtime"), dict) else {}
        sources = []
        for s in (st.get("sources") if isinstance(st.get("sources"), list) else [])[:MAX_SOURCES]:
            if isinstance(s, dict) and isinstance(s.get("id"), str) and _ID.fullmatch(s["id"]):
                name, where = clean_name(s.get("name")), s.get("from")
                if name and isinstance(where, str) and _WHERE.fullmatch(where) and source_id(name, host_of(where)) == s["id"]:
                    sources.append({"id": s["id"], "name": name, "from": where})
        return {"setup": self.setup, "helper": helper, "notice": "; ".join(notice), "sound": sound, "headroom_db": AUDIO_HEADROOM_DB,
                "runtime": {"present": rt.get("present") is True, "loaded": rt.get("loaded") is True,
                            "version": _text(rt.get("version"), 80), "problem": _text(rt.get("problem"))},
                "install": {"command": SETUP_COMMAND, "folder": LIB_DIR,
                            "get": "https://ndi.video/"},
                "sources": sources, "cut": st.get("cut") is True, "max_sources": MAX_SOURCES,
                "addresses": list(addresses), "max_addresses": MAX_ADDRESSES,
                "playing": _playing(st.get("playing")) if self.current else None,
                "ended": dict(self.ended) if self.ended and not self.current else None}

    def open(self, sid):
        """Ask the helper for a source's first frame. Returns {"id", "name", "width", "height", "fps", "container",
        "audio"} or NdiError. "container" says what the pipe will carry: "raw" frames, or "matroska" with sound."""
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise NdiError("no such source")
        if not self.setup:
            raise NdiError(NOT_SET_UP)
        reply = self.client.request({"cmd": "open", "id": sid}, timeout=FIRST_FRAME_SECONDS + 8)
        if reply.get("ok") is not True:
            raise NdiError(_text(reply.get("error")) or "the source could not be opened")
        p = _playing(reply.get("playing"))
        if p is None or p["id"] != sid or not all(k in p for k in ("width", "height", "fps")):
            raise NdiError("the NDI helper (pvj-ndi) gave a bad answer")
        self.ended = None
        return p

    def check_pipe(self):
        """Before the player is told to read it: the path is a pipe, not a link or a file, and the helper's own.
        The helper could swap it a moment after this look (the player opens it later, by name), so this is a
        tripwire for a mistake or a crude swap, not a guarantee; the guarantee is that the player reads whatever
        is there as raw video only (the demuxer is forced) and that the helper cannot write anywhere else."""
        try:
            st = os.lstat(self.fifo)
        except OSError:
            raise NdiError("the NDI helper's pipe is not there; not loading it")
        if not stat.S_ISFIFO(st.st_mode) or st.st_uid != self._pipe_owner():
            raise NdiError("the NDI helper's pipe is not what it should be; not loading it")

    def client_close(self):
        """Longer than the helper may hold its lock while it waits for a first frame: a close must not be lost."""
        try:
            self.client.request({"cmd": "close"}, timeout=FIRST_FRAME_SECONDS + 4)
        except NdiError:
            pass

    def stop(self):
        """Nothing of NDI is on the screen any more: have the helper let go of the source. `current` is cleared
        first and without the lock, so a source being shown again (tick) sees at once that it is no longer wanted."""
        self.cancel()                      # a source still connecting for an earlier play is no longer wanted either
        had, self.current = self.current, None
        if had:
            self.client_close()
        return bool(had)

    def tick(self, replay):
        """Called about once a second. When the helper says the source changed size or rate, or the helper came
        back after a fall, show the source again (`replay(id)`), at most once every RETRY_SECONDS."""
        cur = self.current
        if not cur or self._clock() < self._retry_at:
            return False
        st = self.client.status()
        if st.get("ok") is not True:
            return False
        if st.get("configured") is not True:       # the helper started again and knows nothing yet
            self.sync()
        p = _playing(st.get("playing"))
        if p is not None and p["id"] == cur["id"] and p["state"] in ("refused", "stopped"):
            # It ended for a reason no retry cures (a format this input does not show), or the player went on to
            # something else without a word to us. Either way it is no longer what plays; the reason stays for the page.
            if self.current is cur:
                self.current = None
                self.ended = None if p["message"] in ("", PLAYER_LEFT) else {"name": cur["name"], "message": p["message"]}
            self._retry_wait, self._retry_said = self.RETRY_SECONDS, ""
            return False
        if p is not None and (p["id"] != cur["id"] or p["state"] != "changed"):
            self._retry_wait, self._retry_said = self.RETRY_SECONDS, ""
            return False                           # on the screen: playing, a still picture, or waiting for the sender
        # Try again later and later (a sender switched off for the night must not be asked every 3 seconds for
        # ever), and write the reason down when it changes, not at every try: the journal is kept on the card.
        self._retry_at = self._clock() + self._retry_wait
        self._retry_wait = min(self._retry_wait * 2, self.RETRY_MAX)
        try:
            replay(cur["id"])
            self._retry_at, self._retry_wait, self._retry_said = self._clock() + self.RETRY_SECONDS, self.RETRY_SECONDS, ""
        except Exception as e:                     # the source may be gone for now; a later tick tries again
            said = str(e)[:200]
            if said != self._retry_said:
                self._retry_said = said
                self.log("pvj-web: NDI source %s not shown again yet: %s" % (cur["name"], said))
        return True


# ---- root's command: put the library in place ----------------------------------------------------------------------
def find_in_sdk(folder, machine):
    """The library for this processor inside an unpacked SDK folder, or None."""
    for arch in SDK_FOLDERS.get(machine, ()):
        base = os.path.join(folder, "lib", arch)
        try:
            names = sorted(n for n in os.listdir(base) if re.fullmatch(r"libndi\.so\.[0-9]+(\.[0-9]+){0,3}", n))
        except OSError:
            continue
        for n in sorted(names, key=len, reverse=True):       # the real file has the longest name; the others are links
            path = os.path.realpath(os.path.join(base, n))
            if os.path.dirname(path) == os.path.realpath(base) and os.path.isfile(path):
                return path
    return None


LINK_HOPS = 2                             # libndi.so -> libndi.so.6 -> libndi.so.6.3.2 is the longest the SDK has


def same_folder_target(path):
    """The file a library name stands for. The SDK ships `libndi.so.6` as a link to `libndi.so.6.3.2` beside it
    (found on the first run on a Pi: the name NDI's own pages give was refused as "a link"). A link is followed
    only to a plain name in the SAME folder as the link, at most LINK_HOPS times; a link that leaves its folder,
    points nowhere or goes on and on is refused. What is returned is opened without following a link, so a name
    swapped for a link after this look is refused there. Not a link: returned as it is."""
    if not os.path.islink(path):
        return path
    folder = os.path.dirname(os.path.abspath(path))
    current = os.path.join(folder, os.path.basename(path))
    for _ in range(LINK_HOPS):
        try:
            target = os.readlink(current)
        except OSError:
            break
        # only a bare name: no folder in it at all, so not "../x", not "/abs/x", not "sub/x", not "./x"
        if not target or "/" in target or target in (".", ".."):
            raise NdiError("%s is a link, and not to a plain name beside it (it points to %s); give the file itself"
                           % (path, _text(target, 120) or "nothing"))
        current = os.path.join(folder, target)
        if not os.path.islink(current):
            if not os.path.lexists(current):
                raise NdiError("%s is a link to a file that is not there (%s)" % (path, _text(target, 120)))
            return current
    raise NdiError("%s is a link to a link to a link; give the file itself" % path)


def install_runtime(source, dest_dir=LIB_DIR, machine=None, chown=True):
    """Copy the library from `source` (the file, or an unpacked SDK folder) to dest_dir/LIB_NAME. Returns the path.
    Raises NdiError in words. The copy is checked, not the source: what was checked is what is put in place."""
    machine = machine if machine is not None else box_machine()
    path = find_in_sdk(source, machine) if os.path.isdir(source) else same_folder_target(source)
    if path is None:
        raise NdiError("no libndi.so for this box (%s) under %s/lib" % (ELF_MACHINES.get(machine, "unknown processor"), source))
    # Root reads a name somebody else may control (a USB stick, a download folder). So: never through a link in
    # the last part of the name, never waiting on a pipe, and what is judged is the open file itself, not the name.
    try:
        src = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as e:
        raise NdiError("%s cannot be read as a file (%s)" % (path, "it is a link" if e.errno in (errno.ELOOP, errno.EMLINK) else e.strerror or "error"))
    tmp, made, placed = None, False, False
    try:
        st = os.fstat(src)
        if not stat.S_ISREG(st.st_mode):
            raise NdiError("%s is not a file" % path)
        if st.st_size > LIB_MAX_BYTES:
            raise NdiError("%s is too large to be the NDI runtime" % path)
        made = not os.path.lexists(dest_dir)                             # a refused file leaves no folder behind either
        os.makedirs(dest_dir, mode=0o755, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".libndi-")       # 0600: nobody loads it before every check has passed
        with os.fdopen(fd, "wb") as out:
            copied = 0
            while True:
                chunk = os.read(src, min(1024 * 1024, LIB_MAX_BYTES + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > LIB_MAX_BYTES:                               # it grew while it was read
                    raise NdiError("%s is too large to be the NDI runtime" % path)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if chown:
            os.chown(tmp, 0, 0)
            os.chown(dest_dir, 0, 0)
            os.chmod(dest_dir, 0o755)
        problem = runtime_problem(tmp, owner_uids=(0,) if chown else (os.getuid(),), machine=machine)
        if problem:
            raise NdiError(problem.replace("install it again with pvj-ndi-runtime", "not installed"))
        os.chmod(tmp, 0o644)                                             # readable by the helper only now
        dest = os.path.join(dest_dir, LIB_NAME)
        os.replace(tmp, dest)
        placed = True
        return dest
    finally:
        os.close(src)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if made and not placed:            # the folder this call made, and only if nothing is in it
            try:
                os.rmdir(dest_dir)
            except OSError:
                pass


def runtime_main(argv=None, out=sys.stdout, setup=None):
    """`pvj-ndi-runtime`: root's one command that opts a box in to NDI (the library, then the helper: pvj/ndisetup.py),
    and the one that opts it out again."""
    if setup is None:
        from . import ndisetup as setup
    argv = sys.argv[1:] if argv is None else argv
    dest = os.path.join(LIB_DIR, LIB_NAME)
    usage = ("usage: pvj-ndi-runtime install <the unpacked NDI SDK folder, or libndi.so.6> | status | remove\n"
             "install: sets NDI up on this box. It copies NDI's library into place, then installs and starts the NDI\n"
             "  helper (the service pvj-ndi with an account of its own; avahi-daemon if it is missing) and restarts the\n"
             "  panel and the player. Until it has been run, a box has nothing of NDI on it and nothing of it runs.\n"
             "remove: takes the helper and the library off again.\n"
             "The NDI runtime is proprietary. Get the NDI SDK for Linux from https://ndi.video/ and read its licence\n"
             "BEFORE installing: NDI's free licence may not cover a box like this one (it names embedded devices\n"
             "running Linux among what it does not cover). The owner of the box decides. This is not legal advice.\n"
             "NDI(R) is a registered trademark of Vizrt NDI AB.")
    if argv and argv[0] == "refresh":      # the installer's call for a box that opted in; never sets a box up
        return setup.refresh_main(argv[1:], out)
    if len(argv) == 1 and argv[0] == "status":
        print(runtime_problem(dest) or "the NDI runtime is in place: %s" % dest, file=out)
        print("NDI is set up on this box (the helper pvj-ndi is installed)" if setup.opted_in()
              else "NDI is not set up on this box; to set it up: %s" % setup.COMMAND, file=out)
        return 0
    if os.geteuid() != 0 and argv and argv[0] in ("install", "remove"):
        print("pvj-ndi-runtime: run it with sudo", file=out)
        return 1
    say = lambda m: print("pvj-ndi-runtime: %s" % m, file=out)      # noqa: E731
    if len(argv) == 2 and argv[0] == "install":
        try:
            setup.check()                  # whatever can refuse the setup, before anything on the box is changed
            # then the library: a file that is refused leaves the box as it was
            print("installed %s" % install_runtime(argv[1]), file=out)
            return 1 if setup.enable(say=say) is False else 0      # False: set up, and the helper did not stay up
        except (NdiError, setup.SetupError, OSError) as e:
            print("pvj-ndi-runtime: %s" % e, file=out)
            return 1
    if len(argv) == 1 and argv[0] == "remove":
        code = 0
        try:
            setup.disable(say=say)
        except (setup.SetupError, OSError) as e:      # said, and the library still goes
            print("pvj-ndi-runtime: %s" % e, file=out)
            code = 1
        try:
            try:
                os.unlink(dest)
            except FileNotFoundError:
                pass
            try:
                os.rmdir(LIB_DIR)          # only when nothing else is in it
            except OSError:
                pass
            print("removed NDI's library (%s)" % dest, file=out)
        except OSError as e:
            print("pvj-ndi-runtime: %s could not be removed (%s)" % (dest, e.strerror or "error"), file=out)
            code = 1
        return code
    print(usage, file=out)
    return 2
