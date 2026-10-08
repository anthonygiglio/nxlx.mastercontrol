# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""NDI input: receive a sender's picture and hand it to the one mpv as raw frames in a pipe (D61, pvj/NDI.md).

NDI(R) is a registered trademark of Vizrt NDI AB. Nothing of NDI's is in this file or this repository: the runtime
library (libndi.so.6) is proprietary and is put on the box by its owner (`pvj-ndi-runtime`, below). It is loaded
with ctypes; the structures further down were written from the layout the SDK's public headers describe.

Three parts, in one file so the bounds live in one place:

* `pvj-ndi` (Service, Receiver, CtypesLibrary): a small daemon under its own account and sandbox. It is the only
  process that loads the library, so a library taken over by a bad packet has that account's reach and no more: no
  player socket, no PIN, no settings, no internet (install/pvj-ndi.service).
* the panel's side (Client, Input): asks the daemon over a Unix socket and tells the player to read the pipe.
* `pvj-ndi-runtime` (runtime_main): root's command that copies the library into place after checking what it is.

Everything that comes from the network, through a closed-source library, is hostile until checked: source names
(clean_name), where a source is (clean_sources), and every number of a frame (check_frame), each against a fixed
bound and with fullmatch. The library itself is behind a seam of a few methods (CtypesLibrary); the tests drive
everything above the seam with a fake. NEVER RUN against the real library, a real sender or mpv.
"""

import ctypes
import errno
import fcntl
import hashlib
import ipaddress
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
MAX_NAME = 128
MAX_ADDRESSES = 16
MIN_SIDE, MAX_WIDTH, MAX_HEIGHT = 16, 3840, 2160
MAX_STRIDE_PAD = 256                      # a line may be padded; never by more than this
FOURCC_UYVY = 0x59565955                  # "UYVY", read as a little-endian number
FOURCC_UYVA = 0x41565955                  # "UYVA": a UYVY plane and then an alpha plane, which is never read
FIRST_FRAME_SECONDS = 6.0
PIPE_OPEN_SECONDS = 10.0
QUIET_SECONDS = 2.0                       # no frame for this long: "waiting for the source"
SOURCES_CACHE = 1.0
PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))
STATES = ("connecting", "ready", "playing", "waiting", "changed", "refused", "stopped")
ENDED = ("changed", "refused", "stopped")

_ID = re.compile(r"[0-9a-f]{12}")
_WHERE = re.compile(r"[A-Za-z0-9._:\[\]-]{1,255}")
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
    if not isinstance(raw, str) or not 1 <= len(raw) <= MAX_NAME or raw != raw.strip():
        return None
    if any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in raw):
        return None
    return raw


def source_id(name):
    """What the panel plays by: made here from the checked name, the same after a restart."""
    return hashlib.sha256(b"ndi:" + name.encode("utf-8")).hexdigest()[:12]


def clean_sources(raw):
    """[{"id", "name", "from", "raw": (name bytes, address bytes)}] from the library's (name, address) pairs: only
    sources whose name and address pass, the first of two with one name, at most MAX_SOURCES, by name."""
    out, seen = [], set()
    for n, pair in enumerate(raw):
        if n >= MAX_SOURCES * 4 or len(out) >= MAX_SOURCES:
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
        sid = source_id(name)
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "name": name, "from": where, "raw": pair})
    return sorted(out, key=lambda s: s["name"].lower())


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
    """The stored part of the settings (a user-editable file, and a settings import): {"addresses": [...]}."""
    if not isinstance(cfg, dict) or set(cfg) - {"addresses"}:
        raise NdiError("ndi must be an object with addresses")
    items = cfg.get("addresses", [])
    if not isinstance(items, list) or len(items) > MAX_ADDRESSES:
        raise NdiError("at most %d NDI addresses" % MAX_ADDRESSES)
    out = []
    for a in items:
        if clean_address(a) != a or a in out:
            raise NdiError("bad or repeated NDI address")
        out.append(a)
    return {"addresses": out}


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
    if fields in (2, 3):
        raise NdiError("the source sends interlaced fields, which this input does not show; set the sender to progressive")
    if fields not in (0, 1):
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


class Frame:
    """One captured frame as the seam hands it up: plain numbers, and `view(n)` for n bytes of its memory."""
    __slots__ = ("kind", "width", "height", "fourcc", "stride", "fps_n", "fps_d", "fields", "address", "view", "token")

    def __init__(self, kind, width=0, height=0, fourcc=0, stride=0, fps_n=0, fps_d=0, fields=1, address=0, view=None, token=None):
        self.kind, self.width, self.height, self.fourcc, self.stride = kind, width, height, fourcc, stride
        self.fps_n, self.fps_d, self.fields, self.address, self.view, self.token = fps_n, fps_d, fields, address, view, token


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


class _Perf(ctypes.Structure):
    _fields_ = [("video", ctypes.c_int64), ("audio", ctypes.c_int64), ("metadata", ctypes.c_int64)]


COLOR_FASTEST, BANDWIDTH_HIGHEST = 100, 100
FRAME_VIDEO, FRAME_ERROR = 1, 4
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
        for n in range(min(count.value, MAX_SOURCES * 4)):
            name, url = _cstr(rows[n].name), _cstr(rows[n].url)
            if name is not None and url is not None:
                out.append((name, url))
        return out

    def find_close(self, handle):
        self.lib.NDIlib_find_destroy(handle)

    def recv_open(self, raw):
        """`raw` is the (name, address) pair the library itself gave for a source that passed clean_sources."""
        keep = [ctypes.create_string_buffer(raw[0]), ctypes.create_string_buffer(raw[1]), ctypes.create_string_buffer(b"nxlx.mastercontrol")]
        spec = _RecvCreate(_Source(ctypes.addressof(keep[0]), ctypes.addressof(keep[1])), COLOR_FASTEST, BANDWIDTH_HIGHEST, True,
                           ctypes.cast(keep[2], ctypes.c_char_p))
        handle = self.lib.NDIlib_recv_create_v3(ctypes.byref(spec))
        if not handle:
            raise NdiError("the NDI runtime could not open the source")
        return (handle, keep)

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
                     address, view, video)

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
    pipe. Three buffers go round between them, so neither waits for the other and a frame is never written in part."""

    def __init__(self, lib, source, fifo, log=print, clock=time.monotonic, pipe_wait=PIPE_OPEN_SECONDS):
        self.lib, self.source, self.fifo, self.log, self._clock, self._pipe_wait = lib, source, fifo, log, clock, pipe_wait
        self.state, self.message, self.format = "connecting", "", None
        self.counts = {"received": 0, "shown": 0, "dropped": 0}
        self.first = threading.Event()             # a first good frame, or the end
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._pending, self._free = None, []
        self._last = clock()
        self._handle = None
        self._handle_lock = threading.Lock()       # the connection is used by the capture thread and asked about by status
        self._threads = []

    def _release(self):
        """Give the connection back to the library, once. The capture thread does this itself the moment it ends,
        so a source nobody watches any more is let go without waiting for anyone to ask."""
        with self._handle_lock:
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    self.lib.recv_close(handle)
                except Exception as e:
                    self.log("pvj-ndi: closing the source: %s" % e)

    def start(self):
        self._handle = self.lib.recv_open(self.source["raw"])
        self._threads = [threading.Thread(target=self._capture, name="ndi-capture", daemon=True),
                         threading.Thread(target=self._writer, name="ndi-writer", daemon=True)]
        for t in self._threads:
            t.start()

    def _end(self, state, message):
        with self._cond:
            if self.state not in ENDED:
                self.state, self.message = state, message
            self._stop.set()
            self._cond.notify_all()
        self.first.set()

    def _capture(self):
        spare = None
        try:
            while not self._stop.is_set():
                f = self.lib.recv_capture(self._handle, 250)
                if f is None or f.kind != "video":
                    continue
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
                    self._last = self._clock()
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
            self._release()

    def _open_pipe(self):
        """The pipe, opened for writing once the player reads it; None when stopped or nobody came."""
        deadline = None
        while not self._stop.is_set():
            if self.format is None:
                self.first.wait(0.25)
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
                fcntl.fcntl(fd, getattr(fcntl, "F_SETPIPE_SZ", 1031), 8 * 1024 * 1024)     # room for a 1080p frame
            except OSError:
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
                self._end("stopped", "the player stopped reading the input")
                return False
        return True

    def _writer(self):
        fd = None
        try:
            fd = self._open_pipe()
            if fd is None:
                return
            with self._cond:
                if self.state == "ready":
                    self.state = "playing"
            while not self._stop.is_set():
                with self._cond:
                    while self._pending is None and not self._stop.is_set():
                        self._cond.wait(0.25)
                    buf, self._pending = self._pending, None
                if buf is None:
                    break
                ok = self._write(fd, buf)
                with self._cond:
                    self._free.append(buf)
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
            if state == "playing" and self._clock() - self._last > QUIET_SECONDS:
                state = "waiting"
            out = {"id": self.source["id"], "name": self.source["name"], "state": state, "message": self.message,
                   "counts": dict(self.counts)}
            if self.format:
                out.update(width=self.format[0], height=self.format[1], fps=self.format[2])
        with self._handle_lock:
            if self._handle is not None and state in ("playing", "waiting"):
                try:
                    out["counts"]["dropped_by_runtime"] = self.lib.recv_dropped(self._handle)
                except Exception:
                    pass
        return out

    def close(self):
        """Stop both threads, give the connection back and take the pipe away. Never called from those threads, and
        never with a lock they need (LESSONS: do not join a thread while holding its lock)."""
        self._end("stopped", "")
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []
        self._release()
        try:
            os.unlink(self.fifo)
        except OSError:
            pass


# ---- the daemon ------------------------------------------------------------------------------------------------------
class Service:
    """What pvj-ndi answers, one JSON line each: status, configure, open, close. It does nothing on the network until
    the panel has said the module is on (configure), and it knows no settings but what the panel sends."""

    def __init__(self, rundir, lib_path, loader=load_library, problem=runtime_problem, log=print, clock=time.monotonic,
                 first_frame=FIRST_FRAME_SECONDS, receiver=Receiver):
        self.rundir, self.lib_path, self._loader, self._problem, self.log, self._clock = rundir, lib_path, loader, problem, log, clock
        self._first_frame, self._receiver = first_frame, receiver
        self.fifo = os.path.join(rundir, paths.NDI_FIFO)
        self.lock = threading.RLock()              # configure, open and close, one at a time
        self._find_lock = threading.Lock()         # the finder: asked by status, replaced by configure
        self.lib, self.lib_error, self.version = None, "", ""
        self.configured, self.on, self.addresses = False, False, []
        self._finder, self._sources, self._sources_at = None, [], None
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
            finder, self._finder, self._sources, self._sources_at = self._finder, None, [], None
            if finder is not None:
                self.lib.find_close(finder)

    def configure(self, message):
        on, addresses = message.get("on"), message.get("addresses")
        if not isinstance(on, bool):
            return {"ok": False, "error": "on must be true or false"}
        try:
            addresses = validate_saved({"addresses": addresses})["addresses"]
        except NdiError as e:
            return {"ok": False, "error": str(e)}
        with self.lock:
            if not on:
                self._close_receiver()
                self._close_finder()
            elif self._load() is not None and (self._finder is None or addresses != self.addresses):
                self._close_finder()
                try:
                    with self._find_lock:
                        self._finder = self.lib.find_open(",".join(addresses))
                except NdiError as e:
                    self.lib_error = str(e)
            self.configured, self.on, self.addresses = True, on, addresses
        return self.status()

    def sources(self):
        with self._find_lock:
            if self._finder is None:
                return []
            now = self._clock()
            if self._sources_at is None or now - self._sources_at >= SOURCES_CACHE:
                try:
                    self._sources = clean_sources(self.lib.find_sources(self._finder))
                except Exception as e:
                    self.log("pvj-ndi: reading the sources: %s" % e)
                    self._sources = []
                self._sources_at = now
            return list(self._sources)

    def runtime(self):
        if self.lib is not None:
            return {"present": True, "loaded": True, "version": self.version, "problem": self.lib_error}
        problem = self._problem(self.lib_path)
        return {"present": problem is None, "loaded": False, "version": "", "problem": problem or self.lib_error}

    def status(self):
        r = self.receiver
        if r is not None and r.state in ENDED and self.lock.acquire(blocking=False):
            try:                                   # an ended receiver lets go of its source; what it said stays
                r.close()
            finally:
                self.lock.release()
        return {"ok": True, "configured": self.configured, "on": self.on, "addresses": list(self.addresses), "runtime": self.runtime(),
                "sources": [{"id": s["id"], "name": s["name"], "from": s["from"]} for s in self.sources()],
                "playing": r.status() if r is not None else None}

    def _close_receiver(self):
        r, self.receiver = self.receiver, None
        if r is not None:
            r.close()

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
                os.unlink(self.fifo)
            except OSError:
                pass
            os.mkfifo(self.fifo, 0o640)            # the helper writes; the player reads through the helper's group
            r = self._receiver(self.lib, match[0], self.fifo, log=self.log)
            try:
                r.start()
            except NdiError as e:
                r.close()
                return {"ok": False, "error": str(e)}
            self.receiver = r
            r.first.wait(self._first_frame)
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
class Client:
    """One request, one reply. A status question never holds the panel up: a short wait, and a reply kept a second."""

    def __init__(self, path, timeout=2.0, clock=time.monotonic):
        self.path, self.timeout, self._clock = path, timeout, clock
        self._status = None

    def request(self, message, timeout=None):
        import json
        from .netd import exchange
        try:
            reply = json.loads(exchange(self.path, message, timeout or self.timeout))
        except (OSError, ValueError):
            raise NdiError("the NDI helper (pvj-ndi) is not running")
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
    return v[:limit] if isinstance(v, str) else ""


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
    out["counts"] = {k: counts[k] for k in ("received", "shown", "dropped", "dropped_by_runtime")
                     if isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool) and 0 <= counts[k] < 2 ** 53}
    return out


class Input:
    """The panel's handle on the NDI helper: keeps it told of the switch and the saved addresses, opens a source for
    the player, and notices when the helper says the picture changed size."""

    RETRY_SECONDS = 3.0

    def __init__(self, client, fifo, wanted, log=print, clock=time.monotonic):
        self.client, self.fifo, self._wanted, self.log, self._clock = client, fifo, wanted, log, clock
        self.current = None                # {"id", "name"} while the player reads the pipe
        self.lock = threading.RLock()      # open, load in the player and note, as one step (like the capture input)
        self._retry_at = 0.0

    def sync(self):
        """Tell the helper whether the module is on and which addresses to ask. Returns its status, or {"ok": False}."""
        on, addresses = self._wanted()
        try:
            return self.client.request({"cmd": "configure", "on": on, "addresses": addresses}, timeout=10)
        except NdiError:
            return {"ok": False}

    def status(self):
        """What the page shows. Every string and number from the helper is checked again here."""
        on, addresses = self._wanted()
        st = self.client.status()
        if st.get("ok") and (st.get("configured") is not True or st.get("on") is not on or st.get("addresses") != list(addresses)):
            st = self.sync()
        helper = st.get("ok") is True
        rt = st.get("runtime") if isinstance(st.get("runtime"), dict) else {}
        sources = []
        for s in (st.get("sources") if isinstance(st.get("sources"), list) else [])[:MAX_SOURCES]:
            if isinstance(s, dict) and isinstance(s.get("id"), str) and _ID.fullmatch(s["id"]):
                name, where = clean_name(s.get("name")), s.get("from")
                if name and source_id(name) == s["id"]:
                    sources.append({"id": s["id"], "name": name, "from": where if isinstance(where, str) and _WHERE.fullmatch(where) else ""})
        return {"helper": helper,
                "runtime": {"present": rt.get("present") is True, "loaded": rt.get("loaded") is True,
                            "version": _text(rt.get("version"), 80), "problem": _text(rt.get("problem"))},
                "install": {"command": "sudo pvj-ndi-runtime install \"/path/to/NDI SDK for Linux\"", "folder": LIB_DIR,
                            "get": "https://ndi.video/"},
                "sources": sources, "addresses": list(addresses), "max_addresses": MAX_ADDRESSES,
                "playing": _playing(st.get("playing")) if self.current else None}

    def open(self, sid):
        """Ask the helper for a source's first frame. Returns {"id", "name", "width", "height", "fps"} or NdiError."""
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise NdiError("no such source")
        reply = self.client.request({"cmd": "open", "id": sid}, timeout=FIRST_FRAME_SECONDS + 8)
        if reply.get("ok") is not True:
            raise NdiError(_text(reply.get("error")) or "the source could not be opened")
        p = _playing(reply.get("playing"))
        if p is None or p["id"] != sid or not all(k in p for k in ("width", "height", "fps")):
            raise NdiError("the NDI helper (pvj-ndi) gave a bad answer")
        return p

    def client_close(self):
        try:
            self.client.request({"cmd": "close"})
        except NdiError:
            pass

    def stop(self):
        """Nothing of NDI is on the screen any more: have the helper let go of the source. `current` is cleared
        first and without the lock, so a source being shown again (tick) sees at once that it is no longer wanted."""
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
        if p is not None and (p["id"] != cur["id"] or p["state"] != "changed"):
            return False                           # playing, waiting, or ended for a reason a retry would not cure
        self._retry_at = self._clock() + self.RETRY_SECONDS
        try:
            replay(cur["id"])
        except Exception as e:                     # the source may be gone for now; the next tick tries again
            self.log("pvj-web: NDI source not shown again yet: %s" % e)
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


def install_runtime(source, dest_dir=LIB_DIR, machine=None, chown=True):
    """Copy the library from `source` (the file, or an unpacked SDK folder) to dest_dir/LIB_NAME. Returns the path.
    Raises NdiError in words. The copy is checked, not the source: what was checked is what is put in place."""
    machine = machine if machine is not None else box_machine()
    path = find_in_sdk(source, machine) if os.path.isdir(source) else source
    if path is None:
        raise NdiError("no libndi.so for this box (%s) under %s/lib" % (ELF_MACHINES.get(machine, "unknown processor"), source))
    if not os.path.isfile(path):
        raise NdiError("%s is not a file" % path)
    if os.path.getsize(path) > LIB_MAX_BYTES:
        raise NdiError("%s is too large to be the NDI runtime" % path)
    os.makedirs(dest_dir, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".libndi-")
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        if chown:
            os.chown(tmp, 0, 0)
            os.chown(dest_dir, 0, 0)
            os.chmod(dest_dir, 0o755)
        problem = runtime_problem(tmp, owner_uids=(0,) if chown else (os.getuid(),), machine=machine)
        if problem:
            raise NdiError(problem.replace("install it again with pvj-ndi-runtime", "not installed"))
        dest = os.path.join(dest_dir, LIB_NAME)
        os.replace(tmp, dest)
        return dest
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def runtime_main(argv=None, out=sys.stdout):
    argv = sys.argv[1:] if argv is None else argv
    dest = os.path.join(LIB_DIR, LIB_NAME)
    usage = ("usage: pvj-ndi-runtime install <the unpacked NDI SDK folder, or libndi.so.6> | status | remove\n"
             "The NDI runtime is proprietary. Get the NDI SDK for Linux from https://ndi.video/ and read its licence.\n"
             "NDI(R) is a registered trademark of Vizrt NDI AB.")
    if len(argv) == 1 and argv[0] == "status":
        print(runtime_problem(dest) or "the NDI runtime is in place: %s" % dest, file=out)
        return 0
    if os.geteuid() != 0 and argv and argv[0] in ("install", "remove"):
        print("pvj-ndi-runtime: run it with sudo", file=out)
        return 1
    if len(argv) == 2 and argv[0] == "install":
        try:
            print("installed %s; the NDI helper picks it up when it next starts (sudo systemctl restart pvj-ndi)"
                  % install_runtime(argv[1]), file=out)
            return 0
        except (NdiError, OSError) as e:
            print("pvj-ndi-runtime: %s" % e, file=out)
            return 1
    if len(argv) == 1 and argv[0] == "remove":
        try:
            os.unlink(dest)
        except FileNotFoundError:
            pass
        print("removed; restart the helper to let go of a copy it has loaded (sudo systemctl restart pvj-ndi)", file=out)
        return 0
    print(usage, file=out)
    return 2
