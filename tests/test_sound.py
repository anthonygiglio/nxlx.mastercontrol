# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound reactivity (D96, pvj/SOUND.md): the analysis on generated signals, the levels file's layout, the service
idling and capturing from a stand-in arecord, its socket, OSC bytes on a loopback socket and the caps, the slow
shader input, who may do what (remote support may not switch the microphone on), the unit's sandbox, and an older
release loading the settings. No audio device is needed: every signal is made here."""
import array
import json
import math
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from pvj import netd, paths, sound, soundout
from tests.test_roles import LAN, TUNNEL, RolesBase
from tests.test_units import load_units, words

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CARDS = (" 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
         "                      bcm2835 Headphones\n"
         " 1 [Device         ]: USB-Audio - USB Audio Device\n"
         "                      C-Media Electronics Inc. USB Audio Device at usb-0000:01:00.0-1.3, full speed\n")
PCM = ("00-00: bcm2835 Headphones : bcm2835 Headphones : playback 8\n"
       "01-00: USB Audio : USB Audio : playback 1 : capture 1\n")
DEVICE = "Device,0"


def wait_for(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def sine(freq, amplitude, seconds, rate=sound.RATE, phase=0.0):
    n = int(seconds * rate)
    return array.array("h", [int(round(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate + phase))) for i in range(n)])


def clicks(bpm, seconds, rate=sound.RATE, amplitude=0.8, length=0.01):
    """A click track: a short burst of noise at every beat, silence between."""
    rnd = random.Random(7)
    out = array.array("h", bytes(2 * int(seconds * rate)))
    every = 60.0 / bpm
    t = 0.25
    starts = []
    while t < seconds - length:
        i0 = int(t * rate)
        starts.append(t)
        for i in range(int(length * rate)):
            out[i0 + i] = int(amplitude * 32767 * (rnd.random() * 2 - 1))
        t += every
    return out, starts


def run(signal, analyzer=None):
    a = analyzer or sound.Analyzer()
    out = []
    for b in range(len(signal) // sound.BLOCK):
        out.append(a.block(signal[b * sound.BLOCK:(b + 1) * sound.BLOCK]))
    return out


def proc_dir(test, cards=CARDS, pcm=PCM):
    d = tempfile.mkdtemp(prefix="pvj-asound-")
    test.addCleanup(shutil.rmtree, d, True)
    with open(os.path.join(d, "cards"), "w") as f:
        f.write(cards)
    with open(os.path.join(d, "pcm"), "w") as f:
        f.write(pcm)
    return d


# --- the analysis --------------------------------------------------------------------------------------------------------
class AnalysisTest(unittest.TestCase):
    def test_a_sine_reads_its_own_level(self):
        for peak_db in (0.0, -6.0, -20.0, -40.0):
            amp = 10 ** (peak_db / 20.0)
            v = run(sine(1000, amp, 0.5))[-1]
            want = peak_db - 3.0103                         # the RMS of a sine is its peak less 3 dB
            self.assertAlmostEqual(v["rms_db"], want, delta=0.3, msg=peak_db)
            self.assertAlmostEqual(v["level"], sound.unit(want), delta=0.01)
            self.assertAlmostEqual(v["peak_db"], peak_db, delta=0.1)
            self.assertAlmostEqual(v["envelope"], sound.unit(want), delta=0.02, msg="the envelope has risen to the level")

    def test_silence_is_the_floor_and_has_no_beat(self):
        vs = run(array.array("h", bytes(2 * sound.RATE)))
        self.assertEqual({v["rms_db"] for v in vs}, {sound.FLOOR_DB})
        self.assertFalse(any(v["beat"] for v in vs))
        self.assertEqual(vs[-1]["level"], 0.0)

    def test_each_band_takes_its_own_sine(self):
        tones = {0: 80, 1: 180, 2: 350, 3: 1000, 4: 3000, 5: 6500}
        for band, freq in tones.items():
            v = run(sine(freq, 0.5, 0.3))[-1]
            dbs = v["bands_db"]
            self.assertAlmostEqual(dbs[band], v["rms_db"], delta=1.5, msg="%d Hz reads its level in %s" % (freq, sound.BANDS[band][2]))
            for other, d in enumerate(dbs):
                if abs(other - band) >= 2:
                    self.assertLess(d, dbs[band] - 24, "%d Hz leaks into %s" % (freq, sound.BANDS[other][2]))
            self.assertEqual(max(range(6), key=lambda i: dbs[i]), band)

    def test_a_dc_offset_stays_out_of_the_lowest_band(self):
        sig = array.array("h", [v + 6000 for v in sine(1000, 0.3, 0.3)])
        v = run(sig)[-1]
        self.assertLess(v["bands_db"][0], -60, "an offset of a cheap input is not bass")

    def test_a_click_track_gives_its_beats_and_its_tempo(self):
        for bpm in (90.0, 120.0, 140.0):
            sig, starts = clicks(bpm, 8.0)
            a = sound.Analyzer()
            times = []
            for b in range(len(sig) // sound.BLOCK):
                v = a.block(sig[b * sound.BLOCK:(b + 1) * sound.BLOCK])
                if v["beat"]:
                    times.append((b + 1) * sound.BLOCK_SECONDS)
            self.assertEqual(len(times), len(starts), "one beat per click at %s BPM" % bpm)
            for t, s in zip(times, starts):
                self.assertLess(abs(t - s), 2 * sound.BLOCK_SECONDS + 0.001, "each beat within two blocks of its click")
            got, confidence = a.tempo()
            self.assertAlmostEqual(got, bpm, delta=2.0)
            self.assertGreaterEqual(confidence, 0.8)

    def test_beats_that_agree_on_nothing_give_no_tempo_and_no_fault(self):
        a = sound.Analyzer()
        for t in (0.0, 0.4, 0.9, 1.5, 2.2):                 # intervals 0.4, 0.5, 0.6, 0.7: the middle two are far apart
            a.beat_times.append(t)
        self.assertEqual(a.tempo(), (0.0, 0.0))

    def test_a_fault_in_the_analysis_is_said_and_the_service_goes_on(self):
        svc = sound.Service(os.devnull, log=lambda *_: None)
        calls = []

        def boom(want):
            calls.append(1)
            if len(calls) == 1:
                raise ZeroDivisionError("x")
            svc.stopping = True
            return True
        svc._capture = boom
        svc.want = {"on": True, "device": DEVICE, "gain_db": 0.0}
        svc.proc = proc_dir(self)
        old = sound.BACKOFF
        sound.BACKOFF = (0.05,)
        self.addCleanup(setattr, sound, "BACKOFF", old)
        svc.publish = lambda *a: None
        svc.run()
        self.assertEqual(len(calls), 2, "started again after the fault")

    def test_a_tempo_outside_the_range_is_folded_into_it(self):
        sig, _ = clicks(200.0, 6.0)
        a = sound.Analyzer()
        run(sig, a)
        self.assertAlmostEqual(a.tempo()[0], 100.0, delta=2.0)

    def test_steady_sound_and_hiss_give_no_beats(self):
        rnd = random.Random(3)
        hiss = array.array("h", [int(0.1 * 32767 * (rnd.random() * 2 - 1)) for _ in range(5 * sound.RATE)])
        self.assertLessEqual(sum(1 for v in run(hiss) if v["beat"]), 1)
        tone = sine(440, 0.5, 5.0)
        self.assertLessEqual(sum(1 for v in run(tone) if v["beat"]), 1, "only its start, if anything")

    def test_the_pulse_falls_after_a_beat(self):
        sig, _ = clicks(60.0, 3.0)
        vs = run(sig)
        at = next(i for i, v in enumerate(vs) if v["beat"])
        self.assertEqual(vs[at]["pulse"], 1.0)
        later = int(sound.PULSE_SECONDS / sound.BLOCK_SECONDS) + 1
        self.assertEqual(vs[at + later]["pulse"], 0.0)
        self.assertGreater(vs[at + 2]["pulse"], 0.0)

    def test_the_gain_trim_moves_the_levels_never_beyond_its_range(self):
        sig = sine(1000, 0.1, 0.3)
        plain = run(sig)[-1]
        up = run(sig, sound.Analyzer(6.0))[-1]
        self.assertAlmostEqual(up["rms_db"] - plain["rms_db"], 6.0, delta=0.05)
        self.assertAlmostEqual(up["bands_db"][3] - plain["bands_db"][3], 6.0, delta=0.05)
        self.assertEqual(sound.Analyzer(99).gain_db, sound.GAIN_MAX)
        self.assertEqual(sound.Analyzer(-99).gain_db, sound.GAIN_MIN)

    def test_the_envelope_rises_fast_and_falls_slowly(self):
        loud, quiet = sine(1000, 0.5, 0.5), array.array("h", bytes(2 * sound.RATE))
        vs = run(loud + quiet)
        rise = next(i for i, v in enumerate(vs) if v["envelope"] > 0.9 * vs[20]["envelope"])
        self.assertLessEqual(rise, 2, "up in a block or two")
        fall = int(0.5 / sound.BLOCK_SECONDS)
        self.assertGreater(vs[fall + 5]["envelope"], 0.5 * vs[fall - 1]["envelope"], "down over hundreds of ms")

    def test_a_clipped_block_says_so(self):
        self.assertTrue(run(sine(100, 1.0, 0.1))[-1]["clipped"])
        self.assertFalse(run(sine(100, 0.5, 0.1))[-1]["clipped"])

    def test_the_real_fft_is_the_direct_one(self):
        import cmath
        rnd = random.Random(2)
        w = array.array("h", [int(rnd.uniform(-20000, 20000)) + 3000 for _ in range(sound.FFT_N)])
        mean = sum(w) / float(len(w))
        xs = [(v - mean) * sound.HANN[i] for i, v in enumerate(w)]
        ref = [abs(sum(xs[n] * cmath.exp(-2j * math.pi * k * n / sound.FFT_N) for n in range(sound.FFT_N))) for k in range(sound.FFT_N // 2)]
        got = sound.magnitudes(w)
        self.assertLess(max(abs(a - b) for a, b in zip(ref[1:], got[1:])) / max(ref), 1e-9)

    def test_the_cost_is_bounded(self):
        """A bounded benchmark, as the decision's numbers were taken (D96): ten seconds of sound. The bound here is
        loose (a CI machine is slow and shared); the numbers in pvj/SOUND.md are from a quiet laptop."""
        sig = sine(220, 0.3, 10.0)
        a = sound.Analyzer()
        t0 = time.process_time()
        run(sig, a)
        cpu = time.process_time() - t0
        print("\n  sound analysis: %.1f ms of CPU per second of sound (%.2f%% of one core of this machine)" % (cpu * 100, cpu * 10))
        self.assertLess(cpu, 2.0, "under 20 percent of one core of the test machine")


# --- the levels file ------------------------------------------------------------------------------------------------------
class LayoutTest(unittest.TestCase):
    def values(self):
        return {"rms_db": -12.5, "level": 0.79, "envelope": 0.75, "onset": 1.25, "pulse": 0.5, "bpm": 128.0, "confidence": 0.9,
                "beats": 77, "bands": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "peak_db": -3.0, "blocks": 1234}

    def test_the_offsets_are_the_documented_ones(self):
        raw = sound.pack(self.values(), 41, 12345.5, sound.F_CAPTURING | sound.F_BEAT)
        self.assertEqual(len(raw), 128)
        self.assertEqual(raw[0:4], b"NXSD")
        self.assertEqual(struct.unpack_from("<HHII", raw, 4), (1, 128, 41, sound.F_CAPTURING | sound.F_BEAT))
        self.assertEqual(struct.unpack_from("<dII", raw, 16), (12345.5, 1234, 16000))
        rms, level, env, onset, pulse, bpm, conf = struct.unpack_from("<7f", raw, 32)
        self.assertAlmostEqual(rms, -12.5)
        self.assertAlmostEqual(level, 0.79, places=5)
        self.assertAlmostEqual(bpm, 128.0)
        self.assertEqual(struct.unpack_from("<IHH", raw, 60), (77, 6, 0))
        bands = struct.unpack_from("<8f", raw, 68)
        self.assertEqual([round(b, 5) for b in bands], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.0, 0.0])
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 100)[0], -3.0)
        self.assertEqual(raw[104:120], bytes(16), "no slow bands given: zeros")
        slow = sound.pack(dict(self.values(), slow_bands=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]), 41, 1.0, 1)
        self.assertEqual(struct.unpack_from("<6H", slow, 104), (0, 13107, 26214, 39321, 52428, 65535))
        self.assertEqual(slow[116:120], bytes(4))
        self.assertEqual([round(b, 4) for b in sound.unpack(slow)["slow_bands"]], [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        import zlib
        self.assertEqual(struct.unpack_from("<II", raw, 120), (zlib.crc32(raw[:120]), 41))

    def test_the_doc_has_the_same_table(self):
        with open(os.path.join(REPO, "pvj", "SOUND.md")) as f:
            doc = f.read()
        for row in ("| 0 | 4 bytes | magic `NXSD` |", "| 8 | u32 | sequence number", "| 16 | f64 |", "| 32 | f32 | RMS level",
                    "| 60 | u32 | beat count", "| 68 | 8 x f32 | band levels", "| 100 | f32 | peak, dB |", "| 104 | 6 x u16 | slow bands", "| 120 | u32 | CRC-32",
                    "| 124 | u32 | sequence number again |"):
            self.assertIn(row, doc)
        for name in ("sub", "bass", "low mid", "mid", "high mid", "high"):
            self.assertIn("| %s |" % name, doc)

    def test_round_trip_and_what_is_refused(self):
        raw = sound.pack(self.values(), 9, 1.0, sound.F_CAPTURING)
        v = sound.unpack(raw)
        self.assertEqual((v["seq"], v["beats"], v["capturing"], v["idle"]), (9, 77, True, False))
        self.assertEqual(len(v["bands"]), 6)
        torn = raw[:64] + sound.pack(self.values(), 10, 1.0, 1)[64:]      # half of one write, half of the next
        self.assertIsNone(sound.unpack(torn))
        bad = bytearray(raw)
        bad[40] ^= 1
        self.assertIsNone(sound.unpack(bytes(bad)), "the CRC catches a changed byte")
        self.assertIsNone(sound.unpack(raw[:127]))
        self.assertIsNone(sound.unpack(b"XXXX" + raw[4:]))
        nan = dict(self.values(), level=float("nan"), bpm=1e30)
        v = sound.unpack(sound.pack(nan, 1, 1.0, 1))
        self.assertEqual((v["level"], v["bpm"]), (0.0, 400.0), "values are held to their ranges")

    def test_the_reader_reads_only_the_services_regular_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "levels")
        with open(path, "wb") as f:
            f.write(sound.pack(self.values(), 3, 5.0, 1))
        self.assertEqual(sound.read_levels(path)["seq"], 3)
        os.symlink(path, os.path.join(d, "link"))
        self.assertIsNone(sound.read_levels(os.path.join(d, "link")), "never through a link")
        with open(os.path.join(d, "long"), "wb") as f:
            f.write(sound.pack(self.values(), 3, 5.0, 1) + b"x")
        self.assertIsNone(sound.read_levels(os.path.join(d, "long")))
        os.mkfifo(os.path.join(d, "fifo"))
        self.assertIsNone(sound.read_levels(os.path.join(d, "fifo")), "a pipe is not waited on")
        self.assertIsNone(sound.read_levels(os.path.join(d, "none")))


# --- the devices ----------------------------------------------------------------------------------------------------------
class DeviceTest(unittest.TestCase):
    def test_capture_devices_from_proc(self):
        d = proc_dir(self)
        self.assertEqual(sound.capture_devices(d), [{"id": "Device,0", "name": "USB Audio Device: USB Audio", "channels": 2}])
        self.assertEqual(sound.capture_devices("/nonexistent"), [])

    def test_the_device_is_never_a_free_alsa_string(self):
        self.assertEqual(sound.arecord_args("Device,0"), ["arecord", "-q", "-D", "plughw:CARD=Device,DEV=0", "-t", "raw", "-f", "S16_LE",
                                                         "-r", "16000", "-c", "1"])
        for bad in ("file:'/tmp/x'", "tee:hw:0", "plughw:0", "Device", "Device,0,1", "De vice,0", "Device,100", "", None, 5,
                    "default", "x" * 40 + ",0", "Device,0\n"):
            with self.assertRaises(ValueError, msg=bad):
                sound.alsa_name(bad)

    def test_a_hostile_proc_line_gives_nothing_odd(self):
        d = proc_dir(self, cards=" 2 [Ev;il  ]: x - y\n 3 [Good]: USB - \x1b[31mRed\x07 Mic\n", pcm="02-00: a : b : capture 1\n03-01: c : d : capture 1\n")
        self.assertEqual(sound.capture_devices(d), [{"id": "Good,1", "name": "[31mRed Mic: c", "channels": 2}])


# --- the service ----------------------------------------------------------------------------------------------------------
class FakeChild:
    """arecord's stand-in: raw samples on stdout, words on stderr, and an end when told or when the samples run out."""

    def __init__(self, samples=None, words=b"", forever=True, pace=sound.BLOCK_SECONDS, burst=None):
        r, self._w = os.pipe()
        er, self._ew = os.pipe()
        self.stdout, self.stderr = os.fdopen(r, "rb", buffering=0), os.fdopen(er, "rb", buffering=0)
        self.terminated = False
        self._done = threading.Event()
        self.returncode = None
        self.pace = pace
        threading.Thread(target=self._feed, args=(samples, words, forever, burst), daemon=True).start()

    def _feed(self, samples, words, forever, burst):
        try:
            if burst is not None:
                os.write(self._w, burst.tobytes())          # a backlog: this much waits at once (blocks while the pipe is full)
            data = samples.tobytes() if samples is not None else b""
            step = sound.BLOCK * 2
            while not self._done.is_set():
                for i in range(0, len(data), step):
                    if self._done.is_set():
                        break
                    os.write(self._w, data[i:i + step])
                    time.sleep(self.pace)
                if not forever:
                    break
            if words:
                os.write(self._ew, words)
        except OSError:
            pass
        finally:
            for fd in (self._w, self._ew):
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.returncode = 1

    def terminate(self):
        self.terminated = True
        self._done.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        end = time.monotonic() + (timeout or 5)
        while self.returncode is None and time.monotonic() < end:
            time.sleep(0.01)
        return self.returncode


class ServiceBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pvj-sound-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.levels = os.path.join(self.dir, "levels")
        self.proc = proc_dir(self)
        self.spawned = []
        self.children = []
        self.make = lambda: FakeChild(sine(1000, 0.25, 1.0))
        old = sound.IDLE_EVERY
        sound.IDLE_EVERY = 0.05
        self.addCleanup(setattr, sound, "IDLE_EVERY", old)
        self.svc = sound.Service(self.levels, proc=self.proc, program="arecord", spawn=self.spawn, log=lambda *_: None)
        self.thread = threading.Thread(target=self.svc.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.svc.close)

    def spawn(self, args):
        self.spawned.append(args)
        child = self.make()
        self.children.append(child)
        return child

    def state(self):
        return self.svc.status()["state"], self.svc.status()["why"]


class ServiceTest(ServiceBase):
    def test_with_no_device_it_idles_and_says_so(self):
        self.assertTrue(wait_for(lambda: sound.read_levels(self.levels) is not None))
        self.assertEqual(self.state(), ("idle", "Switched off"))
        first = sound.read_levels(self.levels)
        self.assertTrue(first["idle"])
        self.assertFalse(first["capturing"])
        self.assertTrue(wait_for(lambda: sound.read_levels(self.levels)["seq"] > first["seq"]), "the file is written while idle")
        self.svc.configure({"on": True, "device": "", "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.state() == ("idle", "No capture device chosen")))
        self.assertEqual(self.spawned, [], "nothing is started without a device")
        self.svc.configure({"on": True, "device": "Other,0", "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.state() == ("problem", "Other,0 is not plugged in now")))
        self.assertEqual(self.spawned, [])

    def test_it_captures_the_chosen_device_and_writes_the_levels(self):
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0, "gen": 4})
        self.assertTrue(wait_for(lambda: (sound.read_levels(self.levels) or {}).get("capturing") and sound.read_levels(self.levels)["blocks"] > 10))
        self.assertEqual(self.spawned[0], sound.arecord_args(DEVICE, "arecord"))
        v = sound.read_levels(self.levels)
        self.assertAlmostEqual(v["rms_db"], 20 * math.log10(0.25) - 3.01, delta=0.5)
        self.assertEqual(self.svc.status()["state"], "capturing")
        self.assertEqual(self.svc.status()["gen"], 4)
        self.assertLess(abs(time.monotonic() - v["time"]), 1.0, "the time is the monotonic clock every process shares")
        # the gain trim follows without a restart of arecord
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 12.0})
        self.assertTrue(wait_for(lambda: sound.read_levels(self.levels)["rms_db"] > v["rms_db"] + 11))
        self.assertEqual(len(self.spawned), 1)
        # switched off: arecord is ended and the file says idle
        self.svc.configure({"on": False, "device": DEVICE, "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.children[0].terminated))
        self.assertTrue(wait_for(lambda: sound.read_levels(self.levels)["idle"]))
        self.assertEqual(self.state(), ("idle", "Switched off"))

    def test_an_arecord_that_ends_is_said_with_its_words_and_started_again_later(self):
        self.make = lambda: FakeChild(sine(1000, 0.25, 0.1), words=b"arecord: main:830: audio open error: Device or resource busy\n", forever=False)
        old = sound.BACKOFF
        sound.BACKOFF = (0.2, 0.2)
        self.addCleanup(setattr, sound, "BACKOFF", old)
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.state()[0] == "problem" and "busy" in self.state()[1]))
        self.assertIn("the device stopped: arecord: main:830: audio open error: Device or resource busy", self.state()[1])
        self.assertTrue(wait_for(lambda: len(self.spawned) >= 2), "tried again after a pause")

    def test_a_silent_arecord_is_ended(self):
        self.make = lambda: FakeChild(None)                 # never a sample
        old = sound.STALL_SECONDS
        sound.STALL_SECONDS = 0.3
        self.addCleanup(setattr, sound, "STALL_SECONDS", old)
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.children and self.children[0].terminated))
        self.assertIn("gave no sound", self.state()[1] if self.state()[0] == "problem" else self.svc.why or "gave no sound")

    def test_what_the_socket_takes(self):
        for bad in ({"cmd": "configure", "on": 1, "device": "", "gain_db": 0}, {"cmd": "configure", "on": True, "device": "file:x", "gain_db": 0},
                    {"cmd": "configure", "on": True, "device": "", "gain_db": 99}, {"cmd": "configure", "on": True, "device": "", "gain_db": float("nan")},
                    {"cmd": "run"}, [], "status"):
            self.assertFalse(self.svc.handle(bad)["ok"], bad)
        self.assertTrue(self.svc.handle({"cmd": "status"})["ok"])

    def test_the_socket_answers_the_panel_only(self):
        path = os.path.join(self.dir, "s.sock")
        server = netd.NetServer(path, self.svc, lambda uid: uid == os.getuid())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = soundout.Client(path)
        reply = client.request({"cmd": "configure", "on": False, "device": DEVICE, "gain_db": 3.0, "gen": 2})
        self.assertEqual(soundout._helper_view(reply)["want"], {"on": False, "device": DEVICE, "gain_db": 3.0, "source": "device", "channel": "mix", "channels": 1})
        server.is_allowed = lambda uid: False
        self.assertEqual(soundout._helper_view(client.request({"cmd": "status"})), {"running": False})
        self.assertIsNone(soundout.Client(os.path.join(self.dir, "none.sock")).request({"cmd": "status"}))


# --- OSC out ---------------------------------------------------------------------------------------------------------------
def parse_bundle(data):
    """[(address, tags, [values])] of an OSC bundle or message (floats and ints only, which is all this sends)."""
    def string(b, i):
        end = b.index(b"\0", i)
        s = b[i:end].decode()
        return s, (end + 4) & ~3

    def message(b):
        addr, i = string(b, 0)
        tags, i = string(b, i)
        vals = []
        for t in tags[1:]:
            vals.append(struct.unpack_from(">f" if t == "f" else ">i", b, i)[0])
            i += 4
        return addr, tags, vals
    if data.startswith(b"#bundle\0"):
        out, i = [], 16
        while i < len(data):
            (n,) = struct.unpack_from(">i", data, i)
            out.append(message(data[i + 4:i + 4 + n]))
            i += 4 + n
        return out
    return [message(data)]


class Settings:
    def __init__(self, data):
        self.data, self.lock = data, threading.RLock()

    def save(self):
        pass


class FakeApi:
    def __init__(self, data):
        self.settings = Settings(data)
        self.osc = None
        self.render = None
        self.shaders = None


class Stub:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def record(**over):
    v = {"rms_db": -10.0, "level": 0.83, "envelope": 0.8, "onset": 0.2, "pulse": 0.25, "bpm": 124.0, "confidence": 0.75,
         "beats": 5, "bands": [0.9, 0.8, 0.5, 0.4, 0.3, 0.2], "slow_bands": [0.15, 0.25, 0.35, 0.45, 0.55, 0.65], "peak_db": -2.0, "blocks": 9}
    v.update(over)
    return v


class OscTest(unittest.TestCase):
    def listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.settimeout(2.0)
        self.addCleanup(s.close)
        return s

    def test_the_bundle_carries_the_documented_addresses(self):
        got = parse_bundle(soundout.values_bundle(record()))
        self.assertEqual([a for a, _t, _v in got], ["/nxlx/sound/level", "/nxlx/sound/db", "/nxlx/sound/envelope", "/nxlx/sound/pulse",
                                                    "/nxlx/sound/bpm"], "no band in the bundle of 20 a second (review M1)")
        got += parse_bundle(soundout.bands_bundle(record()))
        self.assertEqual([a for a, _t, _v in got][5:], ["/nxlx/sound/band/%d" % n for n in range(1, 7)] + ["/nxlx/sound/bands"])
        by = {a: (t, v) for a, t, v in got}
        self.assertEqual(by["/nxlx/sound/bpm"][0], ",ff")
        self.assertAlmostEqual(by["/nxlx/sound/bpm"][1][0], 124.0)
        self.assertEqual(by["/nxlx/sound/bands"][0], ",ffffff")
        self.assertAlmostEqual(by["/nxlx/sound/band/1"][1][0], 0.15, places=5, msg="the slow bands, never the fast ones")
        self.assertEqual(parse_bundle(soundout.beat_message(42)), [("/nxlx/sound/beat", ",i", [42])])
        self.assertLess(len(soundout.values_bundle(record()) + soundout.bands_bundle(record())), 600)
        with open(os.path.join(REPO, "pvj", "SOUND.md")) as f:
            doc = f.read()
        for a in ("level", "db", "envelope", "pulse", "bpm", "band/<n>", "bands", "beat"):
            self.assertIn("`/nxlx/sound/%s`" % a, doc)

    def test_the_bytes_on_a_loopback_socket_and_the_caps(self):
        rx = self.listener()
        port = rx.getsockname()[1]
        clock = Clock()
        out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=clock, log=lambda *_: None)
        data = soundout.values_bundle(record())
        tx = soundout._udp()
        self.addCleanup(tx.close)
        self.assertEqual(tx.getsockopt(socket.IPPROTO_IP, socket.IP_TTL), 1, "never across a router")
        target = [("render", "Max", "127.0.0.1", port)]
        out.send(tx, data, target, clock.t)
        self.assertEqual(rx.recv(2048), data, "exactly the bundle")
        # in one moment no more than the burst; over a second no more than 25
        got = 1
        for _ in range(60):
            out.send(tx, data, target, clock.t)
        rx.settimeout(0.3)
        try:
            while True:
                rx.recv(2048)
                got += 1
        except socket.timeout:
            pass
        self.assertEqual(got, int(soundout.TARGET_BURST), "the burst, and nothing queued")
        out2 = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=clock, log=lambda *_: None)
        sent = []

        class Count:
            def sendto(self, d, where):
                sent.append(where)
        for i in range(200):
            out2.send(Count(), data, target, 200.0 + i * 0.01)            # two seconds, a try every 10 ms
        self.assertLessEqual(len(sent), int(soundout.TARGET_BURST + 2 * soundout.TARGET_RATE))
        self.assertGreaterEqual(len(sent), int(2 * soundout.TARGET_RATE) - 1)
        big = b"x" * 1300
        n = len(sent)
        out2.send(Count(), big, target, 300.0)
        self.assertEqual(len(sent), n, "nothing over one datagram's size")
        # small datagrams (the beats) meet the count cap before the byte cap: 5 at once, 25 in a second
        out3 = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=clock, log=lambda *_: None)
        sent.clear()
        beat = soundout.beat_message(1)
        for _ in range(60):
            out3.send(Count(), beat, target, 400.0)
        self.assertEqual(len(sent), int(soundout.TARGET_BURST))
        for i in range(100):
            out3.send(Count(), beat, target, 400.0 + (i + 1) * 0.01)
        self.assertLessEqual(len(sent), int(soundout.TARGET_BURST + soundout.TARGET_RATE) + 1)
        # and every target together: 150 datagrams in any second, whatever the number of targets
        out4 = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=clock, log=lambda *_: None)
        sent.clear()
        many = [("state", "t%d" % i, "127.0.0.%d" % (i + 1), 9000) for i in range(12)]
        for i in range(100):
            out4.send(Count(), beat, many, 500.0 + i * 0.01)
        self.assertLessEqual(len(sent), int(soundout.ALL_BURST + soundout.ALL_RATE))
        self.assertGreaterEqual(len(sent), int(soundout.ALL_RATE) - 12)

    def test_where_it_may_go(self):
        data = {"osc": {"feedback_on": True, "feedback": [{"name": "Tablet", "address": "192.168.0.40", "port": 9000},
                                                          {"name": "Far", "address": "192.168.7.40", "port": 9000},
                                                          {"name": "Locked", "address": "192.168.0.41", "port": 9000}]},
                "render": {"nodes": [{"id": "0a0b0c0d", "name": "Max", "host": "192.168.0.30", "port": 7400, "ndi": "", "profile": "generic", "still": 10},
                                     {"id": "0a0b0c0e", "name": "Name", "host": "render-mac.local", "port": 7401, "ndi": "", "profile": "generic", "still": 10}]}}
        api = FakeApi(data)
        api.osc = Stub(feedback=object(), _may_be_sent=lambda a: a != "192.168.0.41")
        far = lambda a: None if str(a).startswith("192.168.0.") else "not on this network now, nothing is sent to it"

        class Bridge:
            lock = threading.Lock()

            def why_not(self, a, nets=None):
                return far(a)

            def _slot(self, nid, now):
                return {}

            def _look_up(self, node, st, wait):
                from pvj.render import RenderError
                raise RenderError("not yet", 409)
        api.render = Bridge()
        out = soundout.Sound(api, socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None)
        cfg = dict(soundout.DEFAULT, on=True, osc_state=True, osc_render=True)
        got = out.targets(cfg)
        self.assertEqual(got, [("state", "Tablet", "192.168.0.40", 9000), ("render", "Max", "192.168.0.30", 7400)])
        why = {(r["kind"], r["name"]): r["why"] for r in out.osc_status()["left_out"]}
        self.assertEqual(why[("state", "Far")], "not on this network now, nothing is sent to it")
        self.assertEqual(why[("state", "Locked")], "not let in by the locks that are on, nothing is sent to it")
        self.assertIn("not been found", why[("render", "Name")])
        self.assertEqual(out.targets(dict(cfg, osc_state=False, osc_render=False)), [], "both switches off: nowhere")
        api.osc.feedback = None
        self.assertEqual(out.targets(dict(cfg, osc_render=False)), [], "OSC or Send state back off: no state target")
        api.osc = None
        self.assertEqual(out.targets(dict(cfg, osc_render=False)), [])

    def test_from_the_file_to_the_wire_with_a_beat_at_once(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        levels = os.path.join(d, "levels")
        rx = self.listener()
        port = rx.getsockname()[1]
        data = {"sound": dict(soundout.DEFAULT, on=True, device=DEVICE, osc_render=True),
                "render": {"nodes": [{"id": "0a0b0c0d", "name": "Max", "host": "192.168.0.30", "port": 7400, "ndi": "", "profile": "generic", "still": 10}]}}
        api = FakeApi(data)
        api.render = Stub(why_not=lambda a, nets=None: None, lock=threading.Lock())

        class Loop:
            def __init__(self):
                self.where = []

            def __call__(self):
                outer = self

                class S:
                    def sendto(self, b, where):
                        outer.where.append(where)
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.sendto(b, ("127.0.0.1", port))

                    def close(self):
                        pass
                return S()
        udp = Loop()
        out = soundout.Sound(api, socket_path="/nonexistent", levels_path=levels, log=lambda *_: None, udp=udp)
        svc = sound.Service(levels, log=lambda *_: None)
        svc.publish(record(beats=5), sound.F_CAPTURING)
        out.ensure()
        self.addCleanup(out.stop)
        first = parse_bundle(rx.recv(2048))
        self.assertEqual(first[0][0], "/nxlx/sound/level")
        self.assertEqual(udp.where[0], ("192.168.0.30", 7400))
        svc.publish(record(beats=6), sound.F_CAPTURING | sound.F_BEAT)
        seen = []
        end = time.monotonic() + 2
        while time.monotonic() < end and ("/nxlx/sound/beat", ",i", [6]) not in seen:
            seen += parse_bundle(rx.recv(2048))
        self.assertIn(("/nxlx/sound/beat", ",i", [6]), seen)
        # idle or stale: nothing more leaves
        svc.publish({}, sound.F_IDLE)
        time.sleep(0.2)
        rx.settimeout(0.3)
        try:
            while True:
                rx.recv(2048)
        except socket.timeout:
            pass
        with self.assertRaises(socket.timeout):
            rx.recv(2048)
        out.stop()

    def test_the_thread_runs_only_while_needed(self):
        api = FakeApi({"sound": dict(soundout.DEFAULT)})
        out = soundout.Sound(api, socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None)
        out.ensure()
        self.assertIsNone(out._thread)
        api.settings.data["sound"] = dict(soundout.DEFAULT, on=True, osc_state=True)
        out.ensure()
        self.assertTrue(out._thread.is_alive())
        api.settings.data["sound"] = dict(soundout.DEFAULT, on=False, osc_state=True)
        t = out._thread
        out.ensure()
        t.join(3)
        self.assertFalse(t.is_alive(), "capture off: nothing goes out and the thread ends")


# --- the slow shader input ---------------------------------------------------------------------------------------------------
class ShaderMapTest(unittest.TestCase):
    def rig(self):
        changes = []
        self.now = [0.0]
        on = {"id": "nxlx-silk", "epoch": 3}
        parsed = {"inputs": [{"name": "zoom", "label": "Zoom", "type": "float", "min": 0.0, "max": 2.0, "default": 1.0},
                             {"name": "flip", "type": "bool", "min": 0, "max": 1}]}
        eng = Stub(enabled=lambda: True, on_screen=lambda: on, _path=lambda sid: ("p",), _parsed=lambda p: (parsed,),
                   change=lambda body: changes.append(dict(body, at=self.now[0])))
        api = FakeApi({})
        api.shaders = eng
        return soundout.Sound(api, socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None), changes, on

    def test_a_slow_value_at_most_twice_a_second_and_only_when_it_moves(self):
        out, changes, _on = self.rig()
        m = soundout.map_form({"on": True, "input": "zoom", "source": "level", "rest": 0.25, "depth": 0.5, "smooth": 0.5})
        t = 0.0
        while t < 5.0:
            self.now[0] = t
            out.map_step(m, sound.unpack(sound.pack(record(envelope=1.0), 1, 1.0, 1)), t)
            t += 0.05
        self.assertLessEqual(len(changes), 11, "at most twice a second over five seconds")
        gaps = [b["at"] - a["at"] for a, b in zip(changes, changes[1:])]
        self.assertGreaterEqual(min(gaps), soundout.MAP_EVERY - 1e-9, "never two values within half a second")
        values = [c["values"]["zoom"] for c in changes]
        self.assertEqual(values, sorted(values), "it rises towards the level")
        self.assertAlmostEqual(values[-1], 2.0 * 0.75, delta=0.05, msg="rest + depth x the source, in the input's range")
        self.assertEqual(changes[0]["id"], "nxlx-silk")
        n = len(changes)
        for i in range(40):
            out.map_step(m, sound.unpack(sound.pack(record(envelope=1.0), 1, 1.0, 1)), 5.0 + i * 0.05)
        self.assertEqual(len(changes), n, "a value that stands is not sent again (each send is a compile)")
        self.assertEqual(out.map_state["what"], "Moving zoom of nxlx-silk")

    def test_on_the_new_renderer_a_value_can_land_on_each_beat(self):
        out, changes, _on = self.rig()
        out.api.shaders.playing = {"native": True}
        m = soundout.map_form({"on": True, "input": "zoom", "source": "beat", "rest": 0.0, "depth": 1.0, "smooth": 0.05})
        t = 0.0
        while t < 3.0:
            self.now[0] = t
            pulse = 1.0 if t % 0.6 < 0.15 else 0.0            # 100 BPM
            out.map_step(m, sound.unpack(sound.pack(record(pulse=pulse), 1, 1.0, 1)), t)
            t += 0.05
        gaps = [b["at"] - a["at"] for a, b in zip(changes, changes[1:])]
        self.assertGreaterEqual(min(gaps), soundout.MAP_EVERY_NATIVE - 1e-9)
        values = [c["values"]["zoom"] for c in changes]
        turns = sum(1 for a, b, c in zip(values, values[1:], values[2:]) if a < b > c)
        self.assertGreaterEqual(turns, 4, "one rise a beat, not a slow alias of the beat: %s" % values)

    def test_the_beat_and_a_band_and_what_is_said_when_it_cannot(self):
        out, changes, on = self.rig()
        v = sound.unpack(sound.pack(record(pulse=1.0, bands=[0.0, 0.0, 0.0, 0.0, 0.0, 1.0]), 1, 1.0, 1))
        self.assertEqual(out.source_value({"source": "beat"}, v), 1.0)
        self.assertEqual(out.source_value({"source": "band6"}, v), 1.0)
        self.assertEqual(out.source_value({"source": "band1"}, v), 0.0)
        self.assertEqual(out.source_value({"source": "level"}, None), 0.0, "no sound: the source rests")
        m = soundout.map_form({"on": True, "input": "flip"})
        out.map_step(m, v, 0.0)
        self.assertEqual(out.map_state["what"], "nxlx-silk has no number input called flip")
        on.clear()
        out.map_step(soundout.map_form({"on": True, "input": "zoom"}), v, 1.0)
        self.assertEqual(out.map_state["what"], "No shader is on the screen")
        self.assertEqual(changes, [])
        for bad in ({"source": "band9"}, {"depth": 2}, {"smooth": 0.01}, {"rest": -1}, {"input": "a\nb"}, {"on": 1}, {"x": 1}):
            with self.assertRaises(soundout.SoundError, msg=bad):
                soundout.map_form(bad)


# --- the setting, the routes, remote support -----------------------------------------------------------------------------------
class SettingTest(unittest.TestCase):
    def test_stored_fails_closed(self):
        self.assertEqual(soundout.stored(None), soundout.DEFAULT)
        for bad in ({"on": "yes"}, {"device": "file:x"}, {"gain_db": 50}, {"extra": 1}, [], "on", {"map": {"source": "x"}}):
            self.assertFalse(soundout.stored(bad)["on"], bad)
        self.assertEqual(soundout.stored({"on": True, "device": DEVICE})["device"], DEVICE)

    def test_the_default_settings_have_it_off(self):
        from pvj.settings import default_settings
        self.assertEqual(soundout.stored(default_settings()["sound"]), soundout.DEFAULT)
        self.assertFalse(default_settings()["sound"]["on"])


class RoutesTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.sdir = tempfile.mkdtemp(prefix="pvj-sound-api-")
        self.addCleanup(shutil.rmtree, self.sdir, True)
        self.proc = proc_dir(self)
        self.api.sound = soundout.Sound(self.api, socket_path=os.path.join(self.sdir, "none.sock"),
                                        levels_path=os.path.join(self.sdir, "levels"), proc=self.proc, log=lambda *_: None)
        self.addCleanup(self.api.sound.stop)

    def session(self):
        from tests.test_support import CFG
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.owner)[0], 200)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        self.assertEqual(st, 200, body)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        return self.api.support.authenticate(login["token"])

    def test_the_page_says_what_it_does_and_is_off_by_default(self):
        st, d = self.h("GET", "/api/sound", {}, self.operator)
        self.assertEqual(st, 200, d)
        self.assertEqual((d["config"]["on"], d["config"]["device"], d["helper"]["running"]), (False, "", False))
        self.assertEqual(d["devices"], [{"id": DEVICE, "name": "USB Audio Device: USB Audio", "channels": 2}])
        self.assertIn("No sound is stored, recorded or sent anywhere, ever", d["privacy"])
        self.assertIn("up to 20 times a second", d["slow"])
        self.assertIsNone(d["levels"])
        self.assertEqual(self.h("GET", "/api/sound", {}, self.guest)[0], 403)
        self.assertEqual(self.h("GET", "/api/sound/levels", {}, self.guest)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound", {"on": True}, self.operator)[0], 403, "the device and the switch are the Owner's")

    def test_the_owner_chooses_a_listed_device_and_switches_it_on(self):
        self.assertEqual(self.h("POST", "/api/sound", {"device": "Other,0"}, self.owner)[0], 409)
        self.assertEqual(self.h("POST", "/api/sound", {"device": "file:'/tmp/rec'"}, self.owner)[0], 400)
        self.assertEqual(self.h("POST", "/api/sound", {"gain_db": 30}, self.owner)[0], 400)
        self.assertEqual(self.h("POST", "/api/sound", {"bogus": 1}, self.owner)[0], 400)
        st, d = self.h("POST", "/api/sound", {"device": DEVICE, "on": True, "gain_db": -6}, self.owner)
        self.assertEqual(st, 200, d)
        self.assertEqual(self.settings.data["sound"]["device"], DEVICE)
        self.assertTrue(self.settings.data["sound"]["on"])
        self.assertTrue(any("sound reactivity changed" in ln for ln in self.lines))

    def test_the_meter_reads_the_file(self):
        svc = sound.Service(os.path.join(self.sdir, "levels"), log=lambda *_: None)
        svc.publish(record(), sound.F_CAPTURING)
        st, d = self.h("GET", "/api/sound/levels", {}, self.operator)
        self.assertEqual(st, 200)
        self.assertEqual((d["levels"]["capturing"], d["levels"]["db"], len(d["levels"]["bands"])), (True, -10.0, 6))

    def test_the_operator_maps_a_shader_input(self):
        st, d = self.h("POST", "/api/sound/map", {"on": True, "input": "zoom", "source": "band1", "depth": 0.3}, self.operator)
        self.assertEqual(st, 200, d)
        self.assertEqual(self.settings.data["sound"]["map"]["source"], "band1")
        self.assertEqual(self.h("POST", "/api/sound/map", {"source": "voice"}, self.operator)[0], 400)
        self.assertEqual(self.h("POST", "/api/sound/map", {"on": True}, self.guest)[0], 403)

    def test_a_helper_never_starts_listening_to_the_room(self):
        dev = self.session()
        st, body = self.h("POST", "/api/sound", {"device": DEVICE}, dev, TUNNEL)
        self.assertEqual(st, 200, "a device may be chosen while it is off")
        st, body = self.h("POST", "/api/sound", {"on": True}, dev, TUNNEL)
        self.assertEqual(st, 403)
        self.assertIn("a helper does not start listening to the room", body["error"])
        self.assertFalse(self.settings.data["sound"]["on"])
        self.assertEqual(self.h("POST", "/api/sound", {"on": True}, self.owner)[0], 200, "the owner in the room may")
        self.assertEqual(self.h("POST", "/api/sound", {"device": "Device,1"}, dev, TUNNEL)[0], 403, "never another device while it is on")
        self.assertEqual(self.h("POST", "/api/sound", {"osc_state": True}, dev, TUNNEL)[0], 403, "never sending on")
        self.assertEqual(self.h("POST", "/api/sound", {"osc_render": True}, dev, TUNNEL)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound", {"gain_db": 3}, dev, TUNNEL)[0], 200, "the gain may change")
        self.assertEqual(self.h("POST", "/api/sound/map", {"on": True, "input": "zoom"}, dev, TUNNEL)[0], 200)
        self.assertEqual(self.h("POST", "/api/sound", {"on": False}, dev, TUNNEL)[0], 200, "and it may be switched off")
        self.assertFalse(self.settings.data["sound"]["on"])
        self.assertEqual(self.h("POST", "/api/sound", {"on": False}, dict(dev), LAN)[0], 403, "a support login from the studio's network is no login")
        did = json.dumps(self.settings.data["support_log"][0])
        self.assertIn("/api/sound", did)

    def test_it_is_the_boxs_own_and_in_no_settings_file(self):
        from pvj import boxcare
        self.assertIn("sound", boxcare.BOX_OWN)
        self.assertEqual(self.h("POST", "/api/sound", {"device": DEVICE, "on": True}, self.owner)[0], 200)
        st, out = self.h("POST", "/api/system/settings/export", {}, self.owner)
        self.assertEqual(st, 200, out)
        self.assertNotIn("sound", out["file"]["settings"], "no settings file carries a microphone")


class RollbackTest(unittest.TestCase):
    def test_an_older_release_loads_the_file_and_keeps_the_key(self):
        master = "9ca9cbf71bce6258813c18b3d4c87fd8cda46397"     # the release before D96, by its commit (D62's lesson)
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", master], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % master)
        from pvj.settings import Settings as RealSettings
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "settings.json")
        s = RealSettings(path)
        s.load()
        s.data["sound"] = dict(soundout.DEFAULT, on=True, device=DEVICE, osc_render=True)
        s.save()
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", master, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "sound.py")), "the older release has no sound service")
        script = r'''
import json, sys
from pvj.settings import Settings
s = Settings(sys.argv[1])
s.load()
s.data["mix"] = {"transition": "dip", "duration": 2.0}
s.save()
print(json.dumps({"schema": s.data["schema"]}))
'''
        r = subprocess.run([sys.executable, "-c", script, path], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path) as f:
            kept = json.load(f)
        self.assertEqual(kept["sound"], s.data["sound"], "the older release's save keeps what it does not know")
        self.assertEqual(soundout.stored(kept["sound"])["on"], True, "and this release reads it back")


# --- the unit -----------------------------------------------------------------------------------------------------------------
class UnitTest(unittest.TestCase):
    def setUp(self):
        self.u = load_units()["pvj-sound.service"]

    def test_the_sandbox_is_exactly_this(self):
        u = self.u
        self.assertEqual((u["User"], u["Group"]), (["pvj-sound"], ["audio"]))
        self.assertNotIn("SupplementaryGroups", u, "never group pvj")
        self.assertEqual(u["CapabilityBoundingSet"], [""])
        self.assertEqual(words(u, "RestrictAddressFamilies"), ["AF_UNIX"])
        for key, value in (("PrivateNetwork", "yes"), ("IPAddressDeny", "any"), ("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"),
                           ("ProtectHome", "yes"), ("PrivateTmp", "yes"), ("DevicePolicy", "closed"), ("RuntimeDirectory", "pvj-sound"),
                           ("RuntimeDirectoryMode", "0750"), ("MemoryMax", "64M"), ("CPUQuota", "25%"), ("TasksMax", "16"),
                           ("MemoryDenyWriteExecute", "yes"), ("SystemCallFilter", "@system-service"), ("UMask", "0027")):
            self.assertEqual(u[key], [value], key)
        self.assertEqual(u["DeviceAllow"], ["/dev/null rw", "/dev/urandom r", "char-alsa rw"])
        self.assertNotIn("ReadWritePaths", u)
        self.assertNotIn("PrivateUsers", u)            # it would hide the caller's uid from the peer check
        self.assertEqual(u["ConditionPathExists"], ["@PVJ_DIR@/bin/pvj-sound"], "an older release without the program skips it")
        self.assertEqual(dict(w.split("=", 1) for w in words(u, "Environment"))["PVJ_SOUND_DIR"], paths.SOUND_DIR)

    def test_the_installer_makes_the_account_before_the_unit_and_enables_it_and_the_image_too(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            sh = f.read()
        self.assertIn("--gid audio pvj-sound", sh)
        self.assertLess(sh.index("--gid audio pvj-sound"), sh.index('install/pvj-sound.service" > "$SOUND_UNIT"'))
        self.assertIn("systemctl enable pvj-sound.service", sh)
        self.assertIn("alsa-utils", sh)
        self.assertIn('"$SOUND_UNIT"', sh[sh.index("uninstall() {"):sh.index("\n}\n", sh.index("uninstall() {"))])
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            self.assertIn("pvj-sound.service", f.read())
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "00-packages-nr")) as f:
            self.assertIn("alsa-utils", f.read().split())
        self.assertTrue(os.access(os.path.join(REPO, "bin", "pvj-sound"), os.X_OK))


# --- the review of #145 at 7ffce1c ------------------------------------------------------------------------------------------------
class ReviewTest(unittest.TestCase):
    def test_m1_the_slow_bands_are_the_mean_of_200_ms_and_change_at_most_five_times_a_second(self):
        rnd = random.Random(5)
        sig = array.array("h", [int(0.3 * 32767 * (rnd.random() * 2 - 1)) for _ in range(2 * sound.RATE)])
        a = sound.Analyzer()
        vs = run(sig, a)
        n = int(round(sound.SLOW_SECONDS / sound.BLOCK_SECONDS))
        changes = [i for i in range(1, len(vs)) if vs[i]["slow_bands"] != vs[i - 1]["slow_bands"]]
        self.assertLessEqual(len(changes), int(2 / sound.SLOW_SECONDS) + 1)
        for i in changes:
            want = [sum(vs[j]["bands"][k] for j in range(i - n + 1, i + 1)) / n for k in range(6)]
            self.assertEqual([round(x, 9) for x in vs[i]["slow_bands"]], [round(x, 9) for x in want])
        self.assertTrue(all(b - a >= n for a, b in zip(changes, changes[1:])), "never two changes within 200 ms")

    def test_m1_osc_sends_the_bands_slowly_and_the_level_fast(self):
        out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None)
        kinds = {"values": [], "bands": [], "beat": []}
        t, beats = 0.0, 0
        while t < 3.0:
            if t - beats * 0.25 >= 0.25:
                beats += 1
            for d in out.plan(record(beats=beats), t):
                first = parse_bundle(d)[0][0]
                kinds["beat" if first.endswith("/beat") else "bands" if "/band" in first else "values"].append(t)
            t += soundout.LOOK_EVERY
        self.assertLessEqual(len(kinds["bands"]), int(3 / sound.SLOW_SECONDS) + 1, "bands at most 5 times a second")
        self.assertGreaterEqual(len(kinds["values"]), 55, "the level about 20 times a second")
        self.assertEqual(len(kinds["beat"]), beats, "every beat, at once")

    def test_l3_a_target_is_sent_everything_without_a_drop_and_any_second_stays_under_the_cap(self):
        clock = Clock()
        out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=clock, log=lambda *_: None)
        sent = []

        class Count:
            def sendto(self, d, where):
                sent.append(t)
        target = [("render", "Max", "127.0.0.1", 9)]
        t, beats = 100.0, 0
        while t < 106.0:
            if (t - 100.0) / sound.BEAT_GAP >= beats:
                beats += 1                              # beats as fast as the analysis may give them
            for d in out.plan(record(beats=beats), t):
                out.send(Count(), d, target, t)
            t += soundout.LOOK_EVERY
        self.assertEqual(out.stats["held"], 0, "nothing dropped in normal work")
        for start in [100.0 + i * 0.1 for i in range(50)]:
            self.assertLessEqual(sum(1 for x in sent if start <= x < start + 1.0), int(soundout.TARGET_RATE + soundout.TARGET_BURST))

    def test_l4_where_it_may_go_is_worked_out_once_a_second_or_at_once_after_a_change(self):
        out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None)
        asked = []
        out.targets = lambda cfg: asked.append(1) or []
        cfg = dict(soundout.DEFAULT, on=True, osc_render=True)
        t = 0.0
        while t < 3.0:
            out.current_targets(cfg, t)
            t += soundout.LOOK_EVERY
        self.assertLessEqual(len(asked), 4)
        out.current_targets(cfg, 10.0)
        out.current_targets(cfg, 10.01)
        n = len(asked)
        out.gen += 1
        out.current_targets(cfg, 10.02)
        self.assertEqual(len(asked), n + 1, "a change of the setting is taken at once")

    def test_m2_no_core_dump_for_the_service_or_its_child(self):
        code = "import json, resource; from pvj import sound; r = sound.no_core_dumps(); print(json.dumps([list(r['core']), r['dumpable']]))"
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        core, dumpable = json.loads(r.stdout)
        self.assertEqual(core, [0, 0])
        self.assertIn(dumpable, (0, None))
        if sys.platform.startswith("linux"):
            self.assertEqual(dumpable, 0)
        self.assertEqual(load_units()["pvj-sound.service"]["LimitCORE"], ["0"])
        with open(os.path.join(REPO, "pvj", "sound.py")) as f:
            main = f.read().split("def main(")[1]
        self.assertLess(main.index("no_core_dumps()"), main.index("Service("), "before anything is captured")

    def test_m1_the_page_says_what_is_true(self):
        with open(os.path.join(REPO, "pvj", "web", "app.js")) as f:
            js = f.read()
        self.assertNotIn("They leave the box only to the devices you list here", js)
        self.assertIn("id: 'soundbandwarn', text: d.bands_warning", js)
        self.assertLess(js.index("'soundbandwarn'"), js.index("toggle('soundoscstate'"), "the warning stands before the switch")


class BacklogTest(ServiceBase):
    def test_l5_a_block_read_late_carries_its_own_time_and_looks_stale(self):
        self.make = lambda: FakeChild(sine(1000, 0.25, 1.0), burst=sine(1000, 0.25, 1.5))
        got = []
        real = self.svc.publish

        def publish(values, flags, at=None):
            got.append((time.monotonic(), at))
            real(values, flags, at)
        self.svc.publish = publish
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: len([a for _n, a in got if a is not None]) > 120, 8))
        late = [n - a for n, a in got if a is not None]
        self.assertGreater(late[0], 1.0, "the first block of a 1.5 s backlog is more than a second old")
        self.assertGreater(late[0], sound.STALE_SECONDS, "so every reader takes it as stale")
        self.assertLess(late[-1], 0.2, "and once caught up, fresh again")

    def test_l5_a_backlog_beyond_two_seconds_is_thrown_away(self):
        self.make = lambda: FakeChild(sine(1000, 0.25, 1.0), burst=sine(1000, 0.25, 3.0))
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0})
        self.assertTrue(wait_for(lambda: self.svc.status()["counts"].get("dropped", 0) >= 1, 8))


class TunnelTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.sdir = tempfile.mkdtemp(prefix="pvj-sound-tun-")
        self.addCleanup(shutil.rmtree, self.sdir, True)
        self.api.sound = soundout.Sound(self.api, socket_path=os.path.join(self.sdir, "none.sock"),
                                        levels_path=os.path.join(self.sdir, "levels"), proc=proc_dir(self), log=lambda *_: None)
        sound.Service(os.path.join(self.sdir, "levels"), log=lambda *_: None).publish(record(), sound.F_CAPTURING)

    def test_m1_off_the_box_the_bands_are_the_slow_ones_and_through_the_tunnel_none(self):
        for path in ("/api/sound/levels", "/api/sound"):
            st, d = self.h("GET", path, {}, self.operator)
            self.assertEqual(st, 200, d)
            self.assertEqual(d["levels"]["bands"], [0.15, 0.25, 0.35, 0.45, 0.55, 0.65], "the slow bands, never the 50 a second")
        from tests.test_support import CFG
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.owner)[0], 200)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        dev = self.api.support.authenticate(login["token"])
        for path in ("/api/sound/levels", "/api/sound"):
            st, d = self.h("GET", path, {}, dev, TUNNEL)
            self.assertEqual(st, 200, d)
            self.assertEqual((d["levels"]["bands"], d["levels"]["bands_hidden"]), ([], True), "no band through the tunnel")
            self.assertAlmostEqual(d["levels"]["level"], 0.83, places=2, msg="the level and the beat stay")
        self.assertIn("averaged over 200 ms, at most 5 times a second", self.h("GET", "/api/sound", {}, self.owner)[1]["bands_warning"])


if __name__ == "__main__":
    unittest.main()
