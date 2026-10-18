# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound reactivity (D96, pvj/SOUND.md): the service `pvj-sound` and what it shares with the panel.

The service runs `arecord` on the capture device the Owner chose, analyses each block of 20 ms in pure Python
(level, envelope, onsets and beats with a rough tempo, six bands) and writes the result into one file of 128 bytes,
`/run/pvj-sound/levels`, whose layout is fixed below (LEVELS). It has no network at all (its unit), keeps no settings
(the panel tells it what the Owner chose over its socket, which answers the panel's account only) and stores no
sound: the samples of one block live in memory for that block.

It analyses levels only. No audio is stored, recorded, played or sent anywhere, ever.
"""

import array
import cmath
import collections
import math
import operator
import os
import re
import select
import stat
import struct
import subprocess
import sys
import threading
import time
import zlib

from . import paths

# --- the analysis -----------------------------------------------------------------------------------------------------
RATE = 16000                    # samples a second, one channel: ALSA's plug layer mixes down and converts in arecord (D96)
BLOCK = 320                     # samples of one block: 20 ms, so 50 blocks a second
BLOCK_SECONDS = BLOCK / float(RATE)
FFT_N = 256                     # points of the FFT, over the last FFT_N samples (16 ms of each 20 ms block: a kick lasts longer)
FULL = 32768.0                  # full scale of a 16-bit sample
FLOOR_DB = -90.0                # the lowest level said (a 16-bit silence is about -96 dB)
LOW_DB = -60.0                  # 0 on the 0 to 1 scales; 0 dB is 1
GAIN_MIN, GAIN_MAX = -24.0, 24.0
ATTACK, RELEASE = 0.010, 0.300  # the envelope's time constants, seconds
BEAT_GAP = 0.18                 # a beat at most this often (333 BPM): the rest of an onset is not a second beat
SILENT_DB = -60.0               # no beat while the level is below this
PULSE_SECONDS = 0.15            # the beat pulse falls from 1 to 0 in this long
FLUX_BLOCKS = 50                # the onset threshold is the mean flux of the last second ...
FLUX_TIMES = 1.5                # ... times this ...
FLUX_FLOOR = 1.0                # ... plus this, so that steady sound and hiss give no onsets
FLUX_TOP_HZ = 5000              # the flux counts the bins up to here (kick, snare, the body of most onsets)
COMPRESS = 100.0                # magnitudes are compressed as log(1 + COMPRESS * m), m = 1 for a full-scale sine's bin
BEATS_KEPT = 16                 # the last beats kept for the tempo
TEMPO_MIN_INTERVALS = 4
BPM_LOW, BPM_HIGH = 75.0, 150.0  # a tempo is folded into this range
TEMPO_TOLERANCE = 0.06
# The six bands: (lowest Hz, highest Hz, name). The FFT's bins are RATE / FFT_N = 62.5 Hz apart; a bin belongs to the
# band its centre is in. The top is the Nyquist frequency of RATE.
BANDS = ((40, 120, "sub"), (120, 250, "bass"), (250, 500, "low mid"), (500, 2000, "mid"), (2000, 5000, "high mid"),
         (5000, 8000, "high"))
MAX_BANDS = 8                   # places for bands in the file


def _bins(low, high):
    width = RATE / float(FFT_N)
    return [k for k in range(1, FFT_N // 2) if low <= k * width < high]


BAND_BINS = [(_bins(lo, hi)[0], _bins(lo, hi)[-1] + 1) for lo, hi, _name in BANDS]
FLUX_BINS = (1, _bins(0, FLUX_TOP_HZ)[-1] + 1)
HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / FFT_N) for i in range(FFT_N)]
_HANN_POWER = sum(w * w for w in HANN)
# The FFT's tables. The FFT_N real samples are taken as FFT_N / 2 complex numbers (even ones real, odd ones
# imaginary), transformed at half the size, and the two halves are told apart afterwards: half the work.
_M = FFT_N // 2
_LEVELS = _M.bit_length() - 1
_REV = [int(format(i, "0%db" % _LEVELS)[::-1], 2) for i in range(_M)]
_TWIDDLE = [cmath.exp(-2j * math.pi * k / _M) for k in range(_M // 2)]
_STAGES = []
_size = 2
while _size <= _M:
    _STAGES.append((_size, _size // 2, _TWIDDLE[::_M // _size][:_size // 2]))
    _size *= 2
_SPLIT = [-1j * cmath.exp(-2j * math.pi * k / FFT_N) for k in range(_M)]      # e^(-2 pi i k / N) / i
_HANN_EVEN, _HANN_ODD = HANN[0::2], HANN[1::2]


def fft(values):
    """The discrete Fourier transform of len(values) == FFT_N / 2 complex numbers (radix 2, on a new list). Each
    stage is worked a slice at a time, along whichever of its two directions is shorter, so the loops in Python are
    short and the arithmetic is the interpreter's own (map over complex numbers)."""
    y = list(map(values.__getitem__, _REV))
    add, sub, mul = operator.add, operator.sub, operator.mul
    for size, half, tw in _STAGES:
        if _M // size >= half:      # few places in a group, many groups: one stride slice per place
            for o in range(half):
                w = tw[o]
                a, b = y[o::size], y[o + half::size]
                t = list(map(w.__mul__, b))
                y[o::size] = list(map(add, a, t))
                y[o + half::size] = list(map(sub, a, t))
        else:                       # few groups, many places: one run per group
            for s in range(0, _M, size):
                a, b = y[s:s + half], y[s + half:s + size]
                t = list(map(mul, tw, b))
                y[s:s + half] = map(add, a, t)
                y[s + half:s + size] = map(sub, a, t)
    return y


def magnitudes(window):
    """|X[k]| for k = 0 to FFT_N / 2 - 1 of FFT_N real samples under the Hann window, the mean taken out. With the
    Hann window the mean shows only in bins 0 and 1 (the window's own transform is N/2, -N/4, -N/4 and nothing
    else), so it is taken out there instead of from every sample."""
    z = list(map(complex, map(operator.mul, window[0::2], _HANN_EVEN), map(operator.mul, window[1::2], _HANN_ODD)))
    Z = fft(z)
    Zc = list(map(complex.conjugate, [Z[0]] + Z[:0:-1]))           # conj(Z[(M - k) % M])
    even = map(operator.add, Z, Zc)                                 # 2 x the even samples' transform
    odd = map(operator.sub, Z, Zc)                                  # 2i x the odd samples' transform
    X = list(map(operator.add, even, map(operator.mul, _SPLIT, odd)))  # 2 x X[k]
    mean = sum(window) / float(FFT_N)
    X[0] = 0j
    X[1] += 2.0 * mean * FFT_N / 4.0
    return [m * 0.5 for m in map(abs, X)]


def db(rms):
    """dB of full scale for an RMS (0 to 1), never below FLOOR_DB."""
    return max(FLOOR_DB, 20.0 * math.log10(rms)) if rms > 0 else FLOOR_DB


def unit(level_db):
    """A level in dB on the 0 to 1 scale: LOW_DB is 0, 0 dB is 1."""
    return min(1.0, max(0.0, (level_db - LOW_DB) / -LOW_DB))


class Analyzer:
    """One block in, the numbers out. Holds only what the next block needs: the last FFT_N samples, the last
    magnitudes, a second of flux, the last beats."""

    def __init__(self, gain_db=0.0):
        self.window = array.array("h", bytes(2 * FFT_N))
        self.prev = None                                    # the last compressed magnitudes
        self.flux = collections.deque(maxlen=FLUX_BLOCKS)
        self.beat_times = collections.deque(maxlen=BEATS_KEPT)
        self.env = 0.0
        self.blocks = 0
        self.beats = 0
        self.t = 0.0                                        # the time of the stream, by its own samples
        self.set_gain(gain_db)
        self._up = math.exp(-BLOCK_SECONDS / ATTACK)
        self._down = math.exp(-BLOCK_SECONDS / RELEASE)

    def set_gain(self, gain_db):
        self.gain_db = min(GAIN_MAX, max(GAIN_MIN, float(gain_db)))
        self.gain = 10.0 ** (self.gain_db / 20.0)

    def block(self, samples):
        """`samples`: an array('h') of BLOCK samples. Returns a dict of the values (see pack)."""
        n = len(samples)
        self.blocks += 1
        self.t += n / float(RATE)
        if n >= FFT_N:
            self.window = samples[-FFT_N:]
        else:
            self.window = self.window[n:] + samples
        g = self.gain
        energy = sum(map(operator.mul, samples, samples))
        rms = math.sqrt(energy / float(n)) / FULL * g if n else 0.0
        peak = max(max(samples), -min(samples)) if n else 0
        clipped = peak >= 32767
        a = self._up if rms > self.env else self._down
        self.env = a * self.env + (1.0 - a) * rms
        # The spectrum of the window, without its mean (a DC offset of a cheap input would sit in the lowest band).
        mags = magnitudes(self.window)
        scale = 2.0 / (FFT_N * _HANN_POWER) * (g / FULL) ** 2         # power in a bin -> mean square, of full scale
        power = list(map(operator.mul, mags, mags))
        bands = []
        for lo, hi in BAND_BINS:
            bands.append(db(math.sqrt(sum(power[lo:hi]) * scale)))
        # Onsets: what rose since the last block, in compressed magnitudes, against the last second.
        c = COMPRESS * g / (FULL * FFT_N / 4.0)
        lo, hi = FLUX_BINS
        comp = list(map(math.log1p, map(c.__mul__, mags[lo:hi])))
        if self.prev is None:
            flux = 0.0
        else:
            flux = sum(map(max, map(operator.sub, comp, self.prev), [0.0] * len(comp)))
        self.prev = comp
        threshold = FLUX_TIMES * (sum(self.flux) / len(self.flux) if self.flux else 0.0) + FLUX_FLOOR
        self.flux.append(flux)
        onset_strength = flux / threshold
        level_db = db(rms)
        onset = onset_strength >= 1.0 and level_db > SILENT_DB
        beat = False
        last = self.beat_times[-1] if self.beat_times else None
        if onset and (last is None or self.t - last >= BEAT_GAP):
            beat = True
            self.beats += 1
            self.beat_times.append(self.t)
            last = self.t
        pulse = max(0.0, 1.0 - (self.t - last) / PULSE_SECONDS) if last is not None else 0.0
        bpm, confidence = self.tempo()
        return {"rms_db": level_db, "level": unit(level_db), "envelope": unit(db(self.env)), "onset": onset_strength,
                "pulse": pulse, "bpm": bpm, "confidence": confidence, "beats": self.beats, "bands": [unit(b) for b in bands],
                "bands_db": bands, "peak_db": db(peak / FULL * g), "beat": beat, "onset_flag": onset, "clipped": clipped,
                "blocks": self.blocks}

    def tempo(self):
        """(BPM, confidence) from the last beats, folded into BPM_LOW to BPM_HIGH; (0, 0) until there are enough."""
        times = list(self.beat_times)
        intervals = []
        for a, b in zip(times, times[1:]):
            iv = b - a
            if not 0.2 <= iv <= 2.0:
                continue
            while iv < 60.0 / BPM_HIGH:
                iv *= 2.0
            while iv > 60.0 / BPM_LOW:
                iv /= 2.0
            intervals.append(iv)
        if len(intervals) < TEMPO_MIN_INTERVALS:
            return 0.0, 0.0
        s = sorted(intervals)
        mid = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0
        near = [iv for iv in intervals if abs(iv - mid) <= TEMPO_TOLERANCE * mid]
        # The mean of those near the median: a beat is seen at a block's end, so one interval is a block short and
        # the next a block long, and their mean is the tempo where the median is one of the two.
        return 60.0 * len(near) / sum(near), len(near) / float(len(intervals))


# --- the levels file (pvj/SOUND.md has the table) ----------------------------------------------------------------------
MAGIC = b"NXSD"
VERSION = 1
SIZE = 128
F_CAPTURING, F_BEAT, F_ONSET, F_CLIPPED, F_IDLE = 1, 2, 4, 8, 16
_HEAD = struct.Struct("<4sHHIIdIIfffffffIHH8ff16x")        # bytes 0 to 119
_TAIL = struct.Struct("<II")                                # the CRC-32 and the sequence number again
assert _HEAD.size == 120 and _HEAD.size + _TAIL.size == SIZE
STALE_SECONDS = 0.5             # values older than this mean the service is not delivering


def _f32(v, low, high):
    v = float(v)
    return low if v != v else min(high, max(low, v))


def pack(values, seq, now, flags):
    """The 128 bytes of the file for these values (a dict as Analyzer.block gives, or IDLE)."""
    bands = [_f32(b, 0.0, 1.0) for b in list(values.get("bands", []))[:MAX_BANDS]]
    head = _HEAD.pack(MAGIC, VERSION, SIZE, seq & 0xFFFFFFFF, flags, float(now), values.get("blocks", 0) & 0xFFFFFFFF, RATE,
                      _f32(values.get("rms_db", FLOOR_DB), FLOOR_DB, GAIN_MAX), _f32(values.get("level", 0.0), 0.0, 1.0),
                      _f32(values.get("envelope", 0.0), 0.0, 1.0), _f32(values.get("onset", 0.0), 0.0, 1.0e6),
                      _f32(values.get("pulse", 0.0), 0.0, 1.0), _f32(values.get("bpm", 0.0), 0.0, 400.0),
                      _f32(values.get("confidence", 0.0), 0.0, 1.0), values.get("beats", 0) & 0xFFFFFFFF, len(bands), 0,
                      *(bands + [0.0] * (MAX_BANDS - len(bands))),
                      _f32(values.get("peak_db", FLOOR_DB), FLOOR_DB, GAIN_MAX))
    return head + _TAIL.pack(zlib.crc32(head) & 0xFFFFFFFF, seq & 0xFFFFFFFF)


IDLE = {}


def unpack(data):
    """The values in 128 bytes of the file, or None if they are not a whole record of this version."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != SIZE:
        return None
    f = _HEAD.unpack_from(data)
    crc, seq2 = _TAIL.unpack_from(data, _HEAD.size)
    if f[0] != MAGIC or f[1] != VERSION or f[2] != SIZE or f[3] != seq2 or zlib.crc32(bytes(data[:_HEAD.size])) & 0xFFFFFFFF != crc:
        return None
    n = min(f[16], MAX_BANDS)
    return {"seq": f[3], "flags": f[4], "time": f[5], "blocks": f[6], "rate": f[7], "rms_db": f[8], "level": f[9],
            "envelope": f[10], "onset": f[11], "pulse": f[12], "bpm": f[13], "confidence": f[14], "beats": f[15],
            "bands": list(f[18:18 + n]), "peak_db": f[26],
            "capturing": bool(f[4] & F_CAPTURING), "beat": bool(f[4] & F_BEAT), "onset_flag": bool(f[4] & F_ONSET),
            "clipped": bool(f[4] & F_CLIPPED), "idle": bool(f[4] & F_IDLE)}


def read_levels(path, tries=2):
    """The last whole record in the file, or None. The file is the service's: opened without following a link and
    without waiting, and only a regular file of exactly SIZE bytes is read. A record caught half written is read
    again once."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != SIZE:
            return None
        for _ in range(tries):
            got = unpack(os.pread(fd, SIZE, 0))
            if got is not None:
                return got
        return None
    except OSError:
        return None
    finally:
        os.close(fd)


# --- the capture devices ------------------------------------------------------------------------------------------------
PROC = "/proc/asound"
MAX_PROC = 65536                # bytes read of one /proc/asound file
MAX_DEVICES = 32
DEVICE_FORM = re.compile(r"([A-Za-z0-9_]{1,31}),([0-9]{1,2})")
_CARD = re.compile(r"^\s*(\d+)\s+\[([A-Za-z0-9_]{1,31})\s*\]:\s*(.*?)\s+-\s+(.*?)\s*$")
_PCM = re.compile(r"^(\d{1,2})-(\d{1,2}):\s*(.*?)\s*:\s*(.*?)\s*:\s*(.*)$")
_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read(MAX_PROC).decode("utf-8", "replace")
    except OSError:
        return ""


def _clean(text, limit=80):
    return _PRINTABLE.sub("", text)[:limit].strip()


def capture_devices(proc=PROC):
    """The capture devices ALSA has now: [{"id": "<card id>,<device>", "name"}], from /proc/asound. Never a free ALSA
    string: an id is a card's own short id and a device number, and nothing else."""
    cards = {}
    for line in _read(os.path.join(proc, "cards")).splitlines():
        m = _CARD.match(line)
        if m:
            cards[int(m.group(1))] = (m.group(2), _clean(m.group(4)))
    out = []
    for line in _read(os.path.join(proc, "pcm")).splitlines():
        m = _PCM.match(line)
        if not m or not re.search(r"\bcapture\s+[1-9]", m.group(5)):
            continue
        card = cards.get(int(m.group(1)))
        if not card:
            continue
        dev = int(m.group(2))
        out.append({"id": "%s,%d" % (card[0], dev), "name": (card[1] + (": " + _clean(m.group(3)) if _clean(m.group(3)) else ""))[:120]})
        if len(out) >= MAX_DEVICES:
            break
    return out


def device_form(text):
    """The device as it is kept ("<card id>,<n>"), or ValueError."""
    if not isinstance(text, str) or not DEVICE_FORM.fullmatch(text):
        raise ValueError("a capture device is chosen from the list")
    card, dev = text.split(",")
    return "%s,%d" % (card, int(dev))


def alsa_name(device):
    """The ALSA name arecord is given: always the plug layer on one card by its id and one device by its number."""
    card, dev = device_form(device).split(",")
    return "plughw:CARD=%s,DEV=%s" % (card, dev)


def arecord_args(device, program="arecord"):
    return [program, "-q", "-D", alsa_name(device), "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]


# --- the service ----------------------------------------------------------------------------------------------------------
STDERR_KEEP = 4096              # bytes of arecord's words kept: the last ones
BACKOFF = (2, 4, 8, 16, 30)     # seconds before arecord is started again after it ended
STALL_SECONDS = 2.0             # no sample from arecord in this long: it is ended and started again
IDLE_EVERY = 1.0                # the file is written this often while idle
MAX_LINE_WORDS = 200


class Service:
    """What the socket's commands reach, and the capture thread. `spawn(args)` starts arecord (a Popen with stdout
    and stderr pipes); the tests give a stand-in."""

    def __init__(self, levels_path, proc=PROC, program="arecord", spawn=None, clock=time.monotonic, log=print):
        self.levels_path, self.proc, self.program = levels_path, proc, program
        self._spawn = spawn or self._popen
        self._clock, self.log = clock, log
        self.lock = threading.Lock()            # guards want, state, why, child; never held over a read or a write
        self.want = {"on": False, "device": "", "gain_db": 0.0}
        self.gen = 0                            # what the panel last configured, so it can tell a restart of this
        self.state, self.why = "idle", "Switched off"
        self.child = None
        self.wake = threading.Event()
        self.stopping = False
        self.seq = 0
        self.fd = None
        self.tail = collections.deque(maxlen=STDERR_KEEP)
        self.fails = 0
        self.started = self._clock()
        self.counts = {"blocks": 0, "beats": 0, "starts": 0}
        self.cpu = None                         # (wall, cpu) at the start of the last capture, for the status

    # -- the socket's commands --
    def handle(self, message):
        if not isinstance(message, dict):
            return {"ok": False, "error": "a request is a JSON object"}
        cmd = message.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd == "configure":
            return self.configure(message)
        return {"ok": False, "error": "unknown command"}

    def configure(self, m):
        on, device, gain, gen = m.get("on"), m.get("device", ""), m.get("gain_db", 0.0), m.get("gen", 0)
        if not isinstance(on, bool):
            return {"ok": False, "error": "on must be true or false"}
        if device != "":
            try:
                device = device_form(device)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or gain != gain or not GAIN_MIN <= gain <= GAIN_MAX:
            return {"ok": False, "error": "the gain trim is %d to %d dB" % (GAIN_MIN, GAIN_MAX)}
        if isinstance(gen, bool) or not isinstance(gen, int) or not 0 <= gen < 2 ** 53:
            return {"ok": False, "error": "gen must be a whole number"}
        with self.lock:
            changed = (self.want["on"], self.want["device"]) != (on, device)
            self.want = {"on": on, "device": device, "gain_db": float(gain)}
            self.gen = gen
            if changed:
                self.fails = 0
        self.wake.set()
        return self.status()

    def status(self):
        with self.lock:
            out = {"ok": True, "want": dict(self.want), "gen": self.gen, "state": self.state, "why": self.why,
                   "counts": dict(self.counts), "uptime": round(self._clock() - self.started, 1)}
        try:
            t = os.times()
            out["cpu_seconds"] = round(t.user + t.system + t.children_user + t.children_system, 2)
        except OSError:
            pass
        return out

    def _set(self, state, why):
        with self.lock:
            if (self.state, self.why) != (state, why):
                self.log("pvj-sound: %s (%s)" % (state, why))
            self.state, self.why = state, why

    # -- the file --
    def _open(self):
        if self.fd is None:
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(self.levels_path, flags, 0o640)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                os.close(fd)
                raise OSError("the levels file is not this service's own")
            os.ftruncate(fd, SIZE)
            self.fd = fd
        return self.fd

    def publish(self, values, flags):
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        try:
            os.pwrite(self._open(), pack(values, self.seq, self._clock(), flags), 0)
        except OSError as e:
            self._set("problem", "the levels file cannot be written: %s" % (e.strerror or e))

    # -- capture --
    def _popen(self, args):
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, start_new_session=True)

    def _drain(self, pipe):
        """arecord's words, the last STDERR_KEEP bytes of them; read on a thread so a talkative arecord never
        blocks on a full pipe."""
        try:
            while True:
                chunk = pipe.read1(512) if hasattr(pipe, "read1") else pipe.read(512)
                if not chunk:
                    return
                self.tail.extend(chunk)
        except (OSError, ValueError):
            return

    def _words(self):
        text = bytes(self.tail).decode("utf-8", "replace")
        lines = [_clean(ln, MAX_LINE_WORDS) for ln in text.splitlines() if _clean(ln, MAX_LINE_WORDS)]
        return lines[-1] if lines else ""

    def _stop_child(self):
        with self.lock:
            child, self.child = self.child, None
        if child is None:
            return
        try:
            child.terminate()
            try:
                child.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def run(self):
        """The capture thread: idle until there is something to capture, then capture until that changes."""
        while not self.stopping:
            with self.lock:
                want = dict(self.want)
            if not want["on"]:
                self._set("idle", "Switched off")
            elif not want["device"]:
                self._set("idle", "No capture device chosen")
            elif os.sep in self.program and not os.path.exists(self.program):
                self._set("problem", "arecord is not installed (the package alsa-utils)")
            elif want["device"] not in {d["id"] for d in capture_devices(self.proc)}:
                self._set("problem", "%s is not plugged in now" % want["device"])
            else:
                if self._capture(want):
                    continue                     # it ended because the wish changed: look again at once
                wait = BACKOFF[min(self.fails, len(BACKOFF) - 1)]
                self.fails += 1
                self.publish(IDLE, F_IDLE)
                if self.wake.wait(wait):
                    self.wake.clear()
                continue
            self.publish(IDLE, F_IDLE)
            if self.wake.wait(IDLE_EVERY):
                self.wake.clear()

    def _capture(self, want):
        """Run arecord for `want` until the wish changes (True) or arecord ends or stalls (False)."""
        self.tail.clear()
        try:
            child = self._spawn(arecord_args(want["device"], self.program))
        except (OSError, ValueError) as e:
            self._set("problem", "arecord could not be started: %s" % (getattr(e, "strerror", None) or e))
            return False
        with self.lock:
            self.child = child
            self.counts["starts"] += 1
        threading.Thread(target=self._drain, args=(child.stderr,), name="sound-words", daemon=True).start()
        self._set("capturing", "Listening to %s" % want["device"])
        analyzer = Analyzer(want["gain_db"])
        fd = child.stdout.fileno()
        need = BLOCK * 2
        buf = bytearray()
        last = self._clock()
        try:
            while not self.stopping:
                if self.wake.is_set():
                    self.wake.clear()
                    with self.lock:
                        now_want = dict(self.want)
                    if (now_want["on"], now_want["device"]) != (want["on"], want["device"]):
                        return True
                    analyzer.set_gain(now_want["gain_db"])
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    if self._clock() - last > STALL_SECONDS:
                        self._set("problem", "%s gave no sound for %d seconds; starting it again" % (want["device"], STALL_SECONDS))
                        return False
                    continue
                chunk = os.read(fd, need - len(buf))
                if not chunk:
                    words = ""
                    try:
                        child.wait(timeout=1.0)
                        time.sleep(0.05)
                        words = self._words()
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    self._set("problem", "the device stopped%s" % (": " + words if words else ""))
                    return False
                buf.extend(chunk)
                if len(buf) < need:
                    continue
                last = self._clock()
                samples = array.array("h")
                samples.frombytes(bytes(buf))
                buf.clear()
                if sys.byteorder != "little":
                    samples.byteswap()
                v = analyzer.block(samples)
                self.fails = 0
                flags = F_CAPTURING | (F_BEAT if v["beat"] else 0) | (F_ONSET if v["onset_flag"] else 0) | (F_CLIPPED if v["clipped"] else 0)
                self.publish(v, flags)
                with self.lock:
                    self.counts["blocks"] += 1
                    self.counts["beats"] += 1 if v["beat"] else 0
            return True
        finally:
            del buf
            self._stop_child()

    def close(self):
        self.stopping = True
        self.wake.set()
        self._stop_child()


def main(argv=None):
    import pwd
    from .netd import NetServer
    rundir = paths.sound_dir()
    os.makedirs(rundir, mode=0o750, exist_ok=True)
    allowed = set()
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        allowed.add(os.getuid())                   # on a desk: whoever runs it
    service = Service(os.path.join(rundir, paths.SOUND_LEVELS), program=os.environ.get("PVJ_ARECORD") or "/usr/bin/arecord",
                      log=lambda m: print(m, flush=True))
    worker = threading.Thread(target=service.run, name="sound-capture", daemon=True)
    worker.start()
    server = NetServer(os.path.join(rundir, paths.SOUND_SOCKET), service, lambda uid: uid in allowed)
    print("pvj-sound: ready; it analyses levels only and keeps no sound", flush=True)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
    return 0
