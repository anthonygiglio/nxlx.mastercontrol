# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The stand-in for NDI's library (tests/ndi_standin/standin.c), built with this machine's C compiler and driven by
the helper's own code: the real CtypesLibrary, the real Service and Receiver, a real pipe. It shows that the
stand-in and pvj/ndi.py agree on every structure and call, which is what tests/real_install_test.sh then leans on
when it runs the same file under systemd. Skipped where there is no C compiler. It says nothing about NDI's own
library: no test in this repository can."""
import ctypes
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import tests  # noqa: F401  (the package: its lock checker and its temp folder, also when this file is started alone)
from pvj import ndi

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "tests", "ndi_standin", "standin.c")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
WIDTH, HEIGHT, FPS = 320, 180, 30.0
FRAME = 2 * WIDTH * HEIGHT
MOVING, SOUND, STILL = "PVJ-STANDIN (moving)", "PVJ-STANDIN (moving with sound)", "PVJ-STANDIN (one still then sound)"
AT_ONCE_A, AT_ONCE_B, CHANGES = "PVJ-STANDIN (at once A)", "PVJ-STANDIN (at once B)", "PVJ-STANDIN (changes size)"
BIG_SOUND, BIG_CHANGES_SOUND = "PVJ-STANDIN (big moving with sound)", "PVJ-STANDIN (big changes size with sound)"
BIG = (BIG_SOUND, "PVJ-STANDIN (big one still then sound)", "PVJ-STANDIN (big at once A)", "PVJ-STANDIN (big at once B)",
       "PVJ-STANDIN (big at once A with sound)", "PVJ-STANDIN (big at once B with sound)", "PVJ-STANDIN (big changes size)", BIG_CHANGES_SOUND)
THEN_STILL = "PVJ-STANDIN (moving then still with sound)"
STILL_SHORT_GAP, STILL_LONG_GAP = "PVJ-STANDIN (one still then sound after a short gap)", "PVJ-STANDIN (one still then sound after a long gap)"
THEN_STILL_GAP = "PVJ-STANDIN (moving then still with sound after a short gap)"
PAUSES = "PVJ-STANDIN (moving with sound pauses 20 s)"
LATE = tuple("PVJ-STANDIN (moving with sound late by %s)" % t for t in ("0.2 s", "0.8 s", "5 s"))
# sound from another sender (D87): a sender with sound and no picture, and the ways it can come and go
SOUND_ONLY, SOUND_ONLY_STOPS = "PVJ-STANDIN (sound only)", "PVJ-STANDIN (sound only stops after 12 s)"
SOUND_ONLY_APPEARS = "PVJ-STANDIN (sound only appears 5 s after the first picture)"
FLASHES, SOUND_ONLY_CLICKS = "PVJ-STANDIN (flashes)", "PVJ-STANDIN (sound only clicks 200 ms late)"
NOT_ALWAYS_THERE = (SOUND_ONLY_APPEARS,)       # the finder does not report it from the start (standin.c says when)
ONLY_HERE = ((AT_ONCE_A, AT_ONCE_B, THEN_STILL, STILL_SHORT_GAP, STILL_LONG_GAP, THEN_STILL_GAP, PAUSES) + LATE      # for this file and tests/test_ndi_mpv.py
             + (SOUND_ONLY_STOPS, SOUND_ONLY_APPEARS, FLASHES, SOUND_ONLY_CLICKS))
ALL = (MOVING, SOUND, STILL, AT_ONCE_A, AT_ONCE_B, CHANGES, SOUND_ONLY) + ONLY_HERE[2:] + BIG
HOST = "10.255.255.1"                          # where the stand-in says its sources are


def picture(n):
    """Picture n as the stand-in's own text says it is, without the padding of its lines."""
    return bytes((b + 3 * y + 5 * n) & 255 for y in range(HEIGHT) for b in range(2 * WIDTH))


class Reader(threading.Thread):
    """What the player is to the pipe: opens it for reading and goes on reading until told to stop or the pipe ends
    (a reader that leaves is, rightly, the end of the source). The first `want` bytes are kept; `full` says they came."""

    def __init__(self, path, want):
        super().__init__(daemon=True)
        self.path, self.want, self.data = path, want, bytearray()
        self.stop, self.full = threading.Event(), threading.Event()

    def run(self):
        fd = os.open(self.path, os.O_RDONLY)
        try:
            while not self.stop.is_set():
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    return
                if len(self.data) < self.want:
                    self.data += chunk[:self.want - len(self.data)]
                if len(self.data) >= self.want:
                    self.full.set()
        finally:
            os.close(fd)


class OneSetOfNames(unittest.TestCase):
    """The three sources are named in the stand-in and asked for by name in the real-install test. No compiler needed."""

    def test_the_real_install_test_asks_for_the_sources_the_stand_in_reports_and_for_each_road(self):
        with open(SOURCE) as f:
            c = f.read()
        with open(os.path.join(REPO, "tests", "real_install_test.sh")) as f:
            sh = f.read()
        for name in ALL:
            self.assertIn('{"%s", "10.255.255.1:' % name, c)
            if name not in ONLY_HERE:
                self.assertIn('="%s"\n' % name, sh)
        for want in ('pvj-ndi-runtime install "$NDI_STANDIN"', 'box play "$NDI_MOVING" raw', "systemctl restart pvj-ndi.service\n",
                     'box back "$NDI_MOVING" 20', 'box play "$NDI_SOUND" matroska', 'case still "$NDI_STILL"', 'case twice "$NDI_STILL"',
                     'case stuck "$NDI_BIG_STILL"', 'case follow "$NDI_BIG_A" "$NDI_BIG_B"', 'case follow "$NDI_BIG_A_SOUND" "$NDI_BIG_B_SOUND"',
                     'case sound "$NDI_SOUND"', 'case sound "$NDI_BIG_SOUND"', 'case soundon "$NDI_BIG_SOUND"', 'case size "$NDI_BIG_CHANGES"',
                     'case size "$NDI_BIG_CHANGES_SOUND"', 'case size "$NDI_CHANGES"', 'case restart "$NDI_MOVING"', "case chain\n",
                     'case soundfrom "$NDI_MOVING" "$NDI_SOUND_ONLY" 12', 'case soundfromsize "$NDI_BIG_CHANGES" "$NDI_SOUND_ONLY"',
                     'case soundonlyplay "$NDI_SOUND_ONLY"', 'sound_connections_of "$HELPER_PID"', "box soundown ",
                     "pvj-ndi-runtime remove"):
            self.assertIn(want, sh[sh.index('step "an NDI picture all the way'):], want)

    def test_the_stand_in_has_no_line_of_ndis_headers_and_says_so(self):
        with open(SOURCE) as f:
            c = f.read()
        self.assertIn("It is NOT NDI and holds nothing of NDI's", c)
        self.assertNotIn("#include <Processing.NDI", c)
        self.assertNotIn("NDIlib_source_t", c)       # the structures have names of our own; only the entry points' names are NDI's


class Built(unittest.TestCase):
    """The stand-in built once for a class, and for each test the helper's real Service with it and a pipe."""

    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.mkdtemp(prefix="pvj-standin-")
        cls.path = os.path.join(cls.build, ndi.LIB_NAME)
        done = subprocess.run([CC, "-shared", "-fPIC", "-pthread", "-O2", "-Wall", "-Wextra", "-o", cls.path, SOURCE],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if done.returncode != 0:
            shutil.rmtree(cls.build, ignore_errors=True)
            raise AssertionError("the stand-in did not build:\n" + done.stdout)
        cls.warnings = done.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.build, ignore_errors=True)

    def library(self):
        """The file this test's helper loads: the class's one build (what it remembers passes from test to test)."""
        return self.path

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pvj-standin-run-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.said = []
        self.loaded = self.library()
        self.s = ndi.Service(self.dir, self.loaded, loader=ndi.CtypesLibrary, problem=lambda p: None, log=self.said.append)
        self.addCleanup(self.s.close)
        os.mkfifo(self.s.fifo, 0o600)          # as on a box, the pipe is there before a source is opened
        self.readers = []
        self.addCleanup(self.end_readers)

    def end_readers(self):
        for r in self.readers:
            r.stop.set()
        self.s.close()                         # the writer closes: a reader still waiting gets the end
        for r in self.readers:
            r.join(3)

    def on(self, sound, **more):
        st = self.s.handle(dict({"cmd": "configure", "on": True, "addresses": [], "sound": sound}, **more))
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["runtime"]["loaded"], True, st)
        return st

    def play(self, name, sound, want, **more):
        """Open the source called `name`, then read the pipe; returns (the helper's answer, the reader)."""
        st = self.on(sound, **more)
        sid = [s["id"] for s in st["sources"] if s["name"] == name]
        self.assertEqual(len(sid), 1, st["sources"])
        out = self.s.handle({"cmd": "open", "id": sid[0]})
        self.assertTrue(out["ok"], out)
        # The reader comes after the helper's answer, as the player does: before it opens a source the helper wakes
        # whoever still waits on the pipe (Service._remove_pipe), and a reader from before would be given the end.
        reader = Reader(self.s.fifo, want)
        self.readers.append(reader)
        reader.start()
        return out, reader

    def playing(self):
        return self.s.handle({"cmd": "status"})["playing"]

    def wait(self, what, seconds=6.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if what():
                return True
            time.sleep(0.02)
        return bool(what())


@unittest.skipUnless(CC, "no C compiler here: the stand-in for NDI's library cannot be built")
class StandIn(Built):
    def test_it_built_without_a_warning(self):
        self.assertEqual(self.warnings.strip(), "")

    def test_every_structure_has_the_size_and_the_places_ctypes_gives_it(self):
        lib = ctypes.CDLL(self.path)
        lib.pvj_standin_layout.restype, lib.pvj_standin_layout.argtypes = ctypes.c_int, [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        want = []
        for s in (ndi._Source, ndi._FindCreate, ndi._RecvCreate, ndi._Video, ndi._Audio, ndi._Perf):
            want += [ctypes.sizeof(s)] + [getattr(s, name).offset for name, _ in s._fields_]
        got = (ctypes.c_int * 64)()
        n = lib.pvj_standin_layout(got, 64)
        self.assertEqual(list(got[:n]), want)

    def test_it_has_every_call_the_helper_declares_and_says_what_it_is(self):
        lib = ndi.CtypesLibrary(self.path)
        self.assertTrue(lib.has_audio)
        self.assertIn("STAND-IN", lib.version())
        self.assertIn("not NDI", lib.version())

    @unittest.skipUnless(sys.platform.startswith("linux"), "the check of the file is for a Linux library")
    def test_the_helpers_check_of_the_file_passes_it_and_nothing_tells_it_from_ndis(self):
        # runtime_problem asks: a plain file, its owner, nobody else may write it, its size, this processor. Any
        # library built here passes; the sha256 that pvj-ndi-runtime prints is the owner's only check of whose it is.
        os.chmod(self.path, 0o644)
        self.assertIsNone(ndi.runtime_problem(self.path, owner_uids=(os.getuid(),)))

    def test_the_chain_in_the_boxs_questions_names_only_sources_the_stand_in_has(self):
        from tests.ndi_standin import mpvword
        self.assertLessEqual(set(mpvword.CHAIN), set(ALL))
        self.assertEqual((mpvword.size_of(BIG_SOUND), mpvword.size_of(BIG_SOUND, True)), ((1280, 720), (1264, 704)))

    def test_a_big_source_with_sound_is_1280_by_720_in_matroska_and_changes_to_1264_by_704(self):
        out, reader = self.play(BIG_CHANGES_SOUND, True, 0)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"], out["playing"]["container"]), (1280, 720, "matroska"))
        self.assertTrue(self.wait(lambda: self.playing()["counts"]["shown"] >= 10), self.playing())
        self.assertTrue(self.wait(lambda: self.playing()["state"] == "changed", 7.0), self.playing())
        self.assertEqual(self.playing()["message"], "the source changed to 1264 x 704 at 30.0 frames a second")
        out, reader = self.play(BIG_CHANGES_SOUND, True, 0)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"], out["playing"]["container"]), (1264, 704, "matroska"))

    def test_the_finder_reports_its_sources_at_a_private_address(self):
        st = self.on(False)
        names = [s["name"] for s in st["sources"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(set(names), set(ALL))
        self.assertEqual(set(ALL) - set(names) - set(NOT_ALWAYS_THERE), set())
        self.assertEqual({ndi.host_of(s["from"]) for s in st["sources"]}, {"10.255.255.1"})
        self.assertFalse(st["cut"])
        self.assertIn("STAND-IN", st["runtime"]["version"])

    def test_sound_off_whole_pictures_reach_the_pipe_byte_for_byte_and_the_helper_says_playing(self):
        out, reader = self.play(MOVING, False, 6 * FRAME)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"], out["playing"]["fps"]), (WIDTH, HEIGHT, FPS))
        self.assertEqual(out["playing"]["container"], "raw")
        self.assertTrue(reader.full.wait(6), "six whole pictures in the time given: %d bytes came" % len(reader.data))
        numbers = []
        for k in range(6):
            got = bytes(reader.data[k * FRAME:(k + 1) * FRAME])
            n = (got[0] * 205) & 255           # byte 0 of line 0 is 5 * n; 205 is 1/5 modulo 256
            self.assertEqual(got, picture(n), "picture %d in the pipe is not one the stand-in made" % k)
            numbers.append(n)
        steps = [(b - a) & 255 for a, b in zip(numbers, numbers[1:])]
        self.assertTrue(all(1 <= d <= 30 for d in steps), "the pictures go forward: %r" % numbers)
        p = self.playing()
        self.assertEqual(p["state"], "playing", p)
        self.assertGreaterEqual(p["counts"]["shown"], 6)
        self.assertNotIn("audio", p)

    def test_the_source_without_sound_stays_bare_frames_when_sound_is_wanted(self):
        out, reader = self.play(MOVING, True, FRAME)
        self.assertEqual(out["playing"]["container"], "raw")
        self.assertTrue(reader.full.wait(6), "a whole picture in the time given: %d bytes came" % len(reader.data))

    def test_sound_on_the_pipe_carries_matroska_with_the_picture_and_the_sound(self):
        head = ndi.mkv_header(WIDTH, HEIGHT, FPS, (48000, 2))
        out, reader = self.play(SOUND, True, len(head) + 12 * FRAME)
        self.assertEqual(out["playing"]["container"], "matroska")
        self.assertEqual(out["playing"]["audio"]["rate"], 48000)
        self.assertEqual(out["playing"]["audio"]["channels"], 2)
        self.assertTrue(self.wait(lambda: self.playing()["audio"]["counts"]["written"] >= 5), self.playing())
        self.assertTrue(reader.full.wait(6), "twelve pictures' worth in the time given: %d bytes came" % len(reader.data))
        data = bytes(reader.data)
        self.assertEqual(data[:len(head)], head)
        self.assertIn(ndi.mkv_block_head(1, 0.0, FRAME)[:4], data[len(head):])      # a cluster follows
        # the square wave after the helper's headroom: the two channels begin low together, and each sample is the
        # left then the right
        tenth = struct.unpack("<f", struct.pack("<f", 0.1))[0] * ndi.AUDIO_GAIN
        low, high = struct.pack("<f", -tenth), struct.pack("<f", tenth)
        self.assertTrue(low + low in data or high + high in data, "none of the stand-in's sound is in the pipe")
        self.assertTrue(low + high in data or high + low in data, "the right channel is not half the left's pitch")
        p = self.playing()
        self.assertEqual(p["state"], "playing", p)
        self.assertGreaterEqual(p["counts"]["shown"], 2)
        self.assertEqual(p["audio"]["problem"], "")
        self.assertEqual(p["audio"]["counts"]["refused"], 0)

    def test_an_at_once_source_has_its_picture_without_the_quiet_first_second(self):
        st = self.on(False)
        began = time.monotonic()
        out = self.s.handle({"cmd": "open", "id": [s["id"] for s in st["sources"] if s["name"] == AT_ONCE_B][0]})
        self.assertTrue(out["ok"], out)
        self.assertLess(time.monotonic() - began, 0.5, "a first picture at once")
        began = time.monotonic()
        out = self.s.handle({"cmd": "open", "id": [s["id"] for s in st["sources"] if s["name"] == MOVING][0]})
        self.assertGreater(time.monotonic() - began, 0.9, "and the others after their quiet second")

    def test_the_source_that_changes_size_ends_as_changed_and_is_the_new_size_from_then_on(self):
        out, reader = self.play(CHANGES, False, 0)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"]), (WIDTH, HEIGHT))
        self.assertTrue(self.wait(lambda: self.playing()["state"] == "changed", 7.0), self.playing())
        self.assertEqual(self.playing()["message"], "the source changed to 256 x 144 at 30.0 frames a second")
        out, reader = self.play(CHANGES, False, 2 * 256 * 144)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"]), (256, 144))
        self.assertTrue(reader.full.wait(6))
        got = bytes(reader.data)
        n = (got[0] * 205) & 255
        self.assertEqual(got, bytes((b + 3 * y + 5 * n) & 255 for y in range(144) for b in range(512)))

    def test_the_source_that_goes_still_stops_its_picture_after_five_seconds_and_its_sound_goes_on(self):
        out, reader = self.play(THEN_STILL, True, 0)
        self.assertEqual(out["playing"]["container"], "matroska")
        time.sleep(6.3)
        first = self.playing()
        time.sleep(1.0)
        p = self.playing()
        self.assertEqual(p["counts"]["shown"], first["counts"]["shown"], "no picture after the fifth second")
        self.assertGreater(first["counts"]["shown"], 100)
        self.assertGreaterEqual(p["audio"]["counts"]["written"] - first["audio"]["counts"]["written"], 15)
        self.assertEqual(p["message"], "")

    def test_sound_after_a_gap_shows_itself_then_is_silent_until_its_time_and_then_goes_on(self):
        st = self.on(True)
        lib = self.s.lib
        row = [s for s in self.s.sources() if s["name"] == STILL_SHORT_GAP][0]
        handle = lib.recv_open(row["raw"])
        self.addCleanup(lib.recv_close, handle)
        began, times = time.monotonic(), []
        while time.monotonic() - began < 2.6:
            a = lib.recv_audio(handle, 100)
            if a is not None and a is not ndi.AUDIO_LOST:
                times.append(time.monotonic() - began)
                lib.recv_free_audio(handle, a)
        # the receiver's first second is quiet; then 0.1 s of sound, none until 0.8 s, then thirty blocks a second
        early = [t for t in times if t < 1.5]
        self.assertEqual(len(early), 3, times[:8])
        self.assertGreater(min(t for t in times if t >= 1.5), 1.75)
        self.assertGreater(len([t for t in times if t >= 1.8]), 15)
        self.assertTrue(st["ok"])

    def test_a_moving_source_whose_sound_is_late_by_a_fifth_of_a_second_has_pictures_first(self):
        self.on(True)
        lib = self.s.lib
        row = [s for s in self.s.sources() if s["name"] == LATE[0]][0]
        handle = lib.recv_open(row["raw"])
        self.addCleanup(lib.recv_close, handle)
        began, first_sound, first_picture = time.monotonic(), None, None
        while time.monotonic() - began < 1.6 and (first_sound is None or first_picture is None):
            f = lib.recv_capture(handle, 20)
            if f is not None and f.kind == "video":
                first_picture = first_picture or time.monotonic()
                lib.recv_free(handle, f)
            a = lib.recv_audio(handle, 20)
            if a is not None and a is not ndi.AUDIO_LOST:
                first_sound = first_sound or time.monotonic()
                lib.recv_free_audio(handle, a)
        self.assertIsNotNone(first_picture)
        self.assertIsNotNone(first_sound)
        self.assertAlmostEqual(first_sound - first_picture, 0.23, delta=0.08)

    def test_the_bounds_for_sound_against_picture_name_each_breach(self):
        from tests.ndi_standin import mpvword

        def sample(n, avsync, total, drops=0):
            return (n * 0.5, {"time-pos": n * 0.5, "audio-pts": n * 0.5 + 0.01, "avsync": avsync, "total-avsync-change": total,
                              "frame-drop-count": drops, "decoder-frame-drop-count": "(property unavailable)", "estimated-frame-number": n * 15})
        good = mpvword.sync_numbers([sample(n, 0.004, 0.001) for n in range(21)])
        self.assertEqual(mpvword.sync_breaches(good, 16), [])
        self.assertEqual((good["moving"], good["frames"], good["drops"]), (20, 300, 0))
        old = mpvword.sync_numbers([sample(n, 0.206 if n < 4 else 0.0, 0.0 if n < 4 else 0.206) for n in range(21)])
        said = " ".join(mpvword.sync_breaches(old, 16))
        self.assertIn("avsync was as far as 0.206", said)
        self.assertIn("total-avsync-change grew by 0.206", said)
        dropped = mpvword.sync_numbers([sample(n, 0.0, 0.0, drops=n) for n in range(21)])
        self.assertIn("20 of 300 frames were dropped", " ".join(mpvword.sync_breaches(dropped, 16)))
        stood = mpvword.sync_numbers([(n * 0.5, dict(sample(n, 0.5, 0.0)[1], **{"time-pos": 1.0})) for n in range(21)])
        self.assertEqual(stood["moving"], 0, "samples where the picture's clock stands are not sync samples")
        none = mpvword.sync_numbers([(n * 0.5, dict(sample(n, "(property unavailable)", 0.0)[1])) for n in range(21)])
        self.assertIn("mpv gave no avsync in 20 of the 20", " ".join(mpvword.sync_breaches(none, 16)))
        self.assertIn("worst |avsync| 0.004", mpvword.sync_line("x", good))

    def test_the_sounds_clock_is_judged_at_real_time_and_from_its_last_start(self):
        from tests.ndi_standin import mpvword

        def looks(pts):
            return [(float(n), {"audio-pts": v}) for n, v in enumerate(pts)]
        self.assertEqual(mpvword.sound_rate(looks([None, 0.5, 1.5, 2.5, 3.5])), (3.0, 3.0, 1.0))
        played, took, since = mpvword.sound_rate(looks([5.0, 6.0, 0.2, 1.2, 2.2]))       # opened again: only the last stretch counts
        self.assertEqual((played, took, since), (2.0, 2.0, 2.0))
        self.assertEqual(mpvword.sound_rate(looks([1.0, 2.0, "(property unavailable)"])), (0.0, 0.0, None))
        played, took, _ = mpvword.sound_rate(looks([0.0, 0.3, 0.6, 0.9]))               # it moves, at a third of real time
        self.assertLess(played, 0.9 * took - 0.5)                                       # the rule in Rig.watch
        self.assertTrue(mpvword.trying({"playing": None, "ended": {"message": "the NDI helper has no source open; trying again"}}))
        self.assertFalse(mpvword.trying({"playing": None, "ended": {"message": "the source sends interlaced video"}}))

    def test_one_still_then_only_sound_one_picture_is_written_and_the_sound_goes_on(self):
        out, reader = self.play(STILL, True, 0)
        self.assertEqual(out["playing"]["container"], "matroska")
        self.assertTrue(self.wait(lambda: self.playing()["counts"]["shown"] == 1), self.playing())
        before = self.playing()["audio"]["counts"]["written"]
        time.sleep(1.0)
        p = self.playing()
        self.assertEqual(p["counts"]["shown"], 1, "the stand-in sent a second picture")
        self.assertGreaterEqual(p["audio"]["counts"]["written"] - before, 15, "about thirty blocks a second")
        self.assertIn(p["state"], ("playing", "still"), p)
        self.assertEqual(p["message"], "")


def level(data, tenths):
    """Whether the stand-in's square wave at `tenths` tenths of full scale is in what the pipe carried (after the
    helper's headroom): both channels low or both high together, sample by sample."""
    v = struct.unpack("<f", struct.pack("<f", tenths / 10.0))[0] * ndi.AUDIO_GAIN
    low, high = struct.pack("<f", -v), struct.pack("<f", v)
    return low + low in data or high + high in data


@unittest.skipUnless(CC, "no C compiler here: the stand-in for NDI's library cannot be built")
class SoundFromAnotherSender(Built):
    """One sound source for the whole box (D87), through the helper's real code: the picture from one stand-in source,
    the sound from another on a connection asked for sound alone. Each test has a copy of the library of its own,
    because the stand-in counts every receiver that was made in it, and that count is what is read here."""

    def library(self):
        own = os.path.join(tempfile.mkdtemp(prefix="pvj-standin-own-", dir=self.dir), ndi.LIB_NAME)
        shutil.copy(self.path, own)
        return own

    def made(self, name, bandwidth, open_now=False):
        from tests import ndi_standin
        return ndi_standin.receivers(self.loaded, name, bandwidth, open_now)

    def from_source(self, name):
        """The configure message's part that makes `name` the box's sound source (its id is the helper's own hash)."""
        sid = ndi.source_id(name, HOST)
        return {"sound_from": sid, "allowed": [sid]}

    def status(self):
        return self.s.handle({"cmd": "status"})

    def test_the_two_bandwidths_are_the_helpers(self):
        from tests import ndi_standin
        self.assertEqual((ndi_standin.SOUND_ALONE, ndi_standin.PICTURE), (ndi.BANDWIDTH_AUDIO_ONLY, ndi.BANDWIDTH_HIGHEST))
        with open(SOURCE) as f:
            c = f.read()
        self.assertIn("#define BANDWIDTH_SOUND_ALONE %d " % ndi.BANDWIDTH_AUDIO_ONLY, c)
        self.assertIn("#define KIND_ERROR %d " % ndi.FRAME_ERROR, c)

    def test_a_receiver_for_sound_alone_gets_no_picture_from_any_source_and_the_sound_only_source_never_sends_one(self):
        self.on(True)
        lib = self.s.lib
        rows = {s["name"]: s for s in self.s.sources()}
        asked = {"sound alone, a source with sound": lib.recv_open(rows[SOUND]["raw"], audio_only=True),
                 "sound alone, a source without sound": lib.recv_open(rows[MOVING]["raw"], audio_only=True),
                 "sound alone, one still then sound": lib.recv_open(rows[STILL]["raw"], audio_only=True),
                 "a picture's receiver, the sound-only source": lib.recv_open(rows[SOUND_ONLY]["raw"]),
                 "sound alone, the sound-only source": lib.recv_open(rows[SOUND_ONLY]["raw"], audio_only=True)}
        pictures, blocks, loudest = dict.fromkeys(asked, 0), dict.fromkeys(asked, 0), dict.fromkeys(asked, 0.0)
        began = time.monotonic()
        while time.monotonic() - began < 2.0:
            for what, handle in asked.items():
                f = lib.recv_capture(handle, 5)
                if f is not None and f.kind == "video":
                    pictures[what] += 1
                    lib.recv_free(handle, f)
                a = lib.recv_audio(handle, 5)
                if a is not None and a is not ndi.AUDIO_LOST:
                    blocks[what] += 1
                    loudest[what] = max(loudest[what], abs(struct.unpack("<f", bytes(a.view(0, 4)))[0]))
                    lib.recv_free_audio(handle, a)
        self.assertEqual(set(pictures.values()), {0}, pictures)
        self.assertEqual(blocks["sound alone, a source without sound"], 0, blocks)
        for what in set(asked) - {"sound alone, a source without sound"}:
            self.assertGreater(blocks[what], 10, blocks)
        self.assertAlmostEqual(loudest["sound alone, a source with sound"], 0.1, places=5)
        self.assertAlmostEqual(loudest["sound alone, the sound-only source"], 0.05, places=5)
        self.assertAlmostEqual(loudest["a picture's receiver, the sound-only source"], 0.05, places=5)
        # and each of them is on the stand-in's own record, by its source and by how it was asked for
        self.assertEqual((self.made(SOUND, 10), self.made(MOVING, 10), self.made(STILL, 10)), (1, 1, 1))
        self.assertEqual((self.made(SOUND_ONLY, 10), self.made(SOUND_ONLY, 100), self.made(SOUND, 100)), (1, 1, 0))
        self.assertEqual(self.made(SOUND_ONLY, 10, open_now=True), 1)
        for handle in asked.values():
            lib.recv_close(handle)
        self.assertEqual((self.made(SOUND_ONLY, 10, open_now=True), self.made(SOUND_ONLY, 10)), (0, 1))
        self.assertEqual(self.made("no such source", 10), 0)

    def test_played_by_itself_the_sound_only_source_is_refused_in_words_and_marked_sound_only(self):
        sid = ndi.source_id(SOUND_ONLY, HOST)
        st = self.on(True, **self.from_source(SOUND_ONLY))
        self.assertNotIn("sound_only", [s for s in st["sources"] if s["id"] == sid][0])       # not known before it was pressed
        self.s._first_frame = 2.0                  # the helper's own wait for a first picture, shortened for this test
        out = self.s.handle({"cmd": "open", "id": sid})
        self.assertEqual(out, {"ok": False, "sound_only": True,
                               "error": "%s sends sound and no picture; choose it under 'Where sound comes from' on the NDI page" % SOUND_ONLY})
        self.assertIs([s for s in self.status()["sources"] if s["id"] == sid][0].get("sound_only"), True)
        # it was asked for as a picture is (it is the picture here), never on a second connection for its sound
        self.assertEqual((self.made(SOUND_ONLY, 100), self.made(SOUND_ONLY, 10)), (1, 0))

    def test_the_picture_of_one_source_and_the_sound_of_the_sound_only_source_arrive_in_one_matroska_stream(self):
        from tests.test_ndi_sound import blocks
        head = ndi.mkv_header(WIDTH, HEIGHT, FPS, (48000, 2))
        out, reader = self.play(MOVING, True, len(head) + 45 * FRAME, **self.from_source(SOUND_ONLY))
        self.assertEqual((out["playing"]["name"], out["playing"]["container"]), (MOVING, "matroska"))
        self.assertEqual((out["playing"]["audio"]["rate"], out["playing"]["audio"]["channels"]), (48000, 2))
        self.assertTrue(reader.full.wait(8), "forty-five pictures' worth in the time given: %d bytes came" % len(reader.data))
        data = bytes(reader.data)
        self.assertEqual(data[:len(head)], head)
        got = blocks(data)
        pictures, sounds = [b for t, _, b in got if t == 1], b"".join(b for t, _, b in got if t == 2)
        self.assertGreater(len(pictures), 20)
        for one in pictures[:3]:
            self.assertEqual(one, picture((one[0] * 205) & 255), "a picture in the stream is not one the picture's source made")
        self.assertGreater(len(sounds), 48000 * 8 // 2, "half a second of sound at the least")
        self.assertTrue(level(sounds, 0.5), "the sound-only source's sound (a twentieth of full scale) is not in the stream")
        self.assertFalse(level(sounds, 1.0), "a picture source's sound (a tenth of full scale) is in the stream")
        st = self.status()
        self.assertEqual(st["sound_source"], {"id": ndi.source_id(SOUND_ONLY, HOST), "state": "playing", "arriving": True})
        self.assertEqual((st["playing"]["state"], st["playing"]["audio"]["problem"]), ("playing", ""))
        # how the stand-in was asked: the picture's source as a picture is, the sound's source for sound alone, once each
        self.assertEqual((self.made(MOVING, 100), self.made(MOVING, 10)), (1, 0))
        self.assertEqual((self.made(SOUND_ONLY, 10), self.made(SOUND_ONLY, 100)), (1, 0))

    def test_the_sound_connection_is_opened_once_and_stays_through_a_picture_re_open_at_another_size(self):
        choice = self.from_source(SOUND_ONLY)
        out, reader = self.play(CHANGES, True, 0, **choice)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"], out["playing"]["container"]), (WIDTH, HEIGHT, "matroska"))
        feed = self.s.feed
        self.assertTrue(self.wait(lambda: self.playing()["state"] == "changed", 7.0), self.playing())
        self.assertEqual(self.playing()["message"], "the source changed to 256 x 144 at 30.0 frames a second")
        self.assertEqual(self.made(SOUND_ONLY, 10, open_now=True), 1, "the sound connection is still open while no picture is")
        head = ndi.mkv_header(256, 144, FPS, (48000, 2))
        out, reader = self.play(CHANGES, True, len(head) + 40 * 2 * 256 * 144, **choice)
        self.assertEqual((out["playing"]["width"], out["playing"]["height"], out["playing"]["container"]), (256, 144, "matroska"))
        self.assertTrue(reader.full.wait(8), "forty pictures' worth of the new stream: %d bytes came" % len(reader.data))
        self.assertEqual(bytes(reader.data[:len(head)]), head)
        self.assertTrue(level(bytes(reader.data), 0.5), "the sound-only source's sound is not in the stream after the re-open")
        self.assertIs(self.s.feed, feed)
        self.assertTrue(feed.alive())
        self.assertEqual((self.made(SOUND_ONLY, 10), self.made(SOUND_ONLY, 10, open_now=True)), (1, 1), "one sound connection in the whole run")
        self.assertEqual((self.made(CHANGES, 100), self.made(CHANGES, 100, open_now=True)), (2, 1), "the picture was opened twice")
        self.assertEqual(len([line for line in self.said if "sound comes from" in line]), 1, self.said)

    def test_a_sound_source_that_appears_five_seconds_after_the_press_ends_the_bare_picture_as_changed_and_then_plays(self):
        choice = self.from_source(SOUND_ONLY_APPEARS)
        st = self.on(True, **choice)
        self.assertNotIn(SOUND_ONLY_APPEARS, [s["name"] for s in st["sources"]])
        self.assertEqual(st["sound_source"], {"id": choice["sound_from"], "state": "away", "arriving": False})
        began = time.monotonic()
        out, reader = self.play(MOVING, True, 0, **choice)
        self.assertEqual(out["playing"]["container"], "raw", "with its sound source away a picture plays as bare frames")
        seen = {}

        def changed():                             # each look is also the helper's look for the sound source (Service.status)
            st = self.status()
            if SOUND_ONLY_APPEARS in [s["name"] for s in st["sources"]]:
                seen.setdefault("listed", time.monotonic() - began)
            return st["playing"]["state"] == "changed"
        self.assertTrue(self.wait(changed, 12.0), self.status())
        took = time.monotonic() - began
        self.assertEqual(self.playing()["message"], "the sound source started to send sound")
        self.assertAlmostEqual(seen["listed"], 5.0, delta=1.3)       # five seconds after the picture's receiver; the helper's list is a second old
        # then: the helper's next look (every 2 s), the stand-in's quiet first second, the first block
        self.assertLess(took - seen["listed"], ndi.FEED_RETRY_SECONDS + 2.5, "from there to the first sound")
        out, reader = self.play(MOVING, True, 0, **choice)
        self.assertEqual(out["playing"]["container"], "matroska")
        self.assertEqual(self.status()["sound_source"]["state"], "playing")
        self.assertEqual((self.made(SOUND_ONLY_APPEARS, 10), self.made(MOVING, 100)), (1, 2))

    def test_a_sound_source_that_stops_is_away_and_the_picture_goes_on(self):
        choice = self.from_source(SOUND_ONLY_STOPS)
        out, reader = self.play(MOVING, True, 0, **choice)
        self.assertEqual(out["playing"]["container"], "matroska")
        began = time.monotonic()
        self.assertEqual(self.status()["sound_source"]["state"], "playing")
        self.assertTrue(self.wait(lambda: self.status()["sound_source"]["state"] == "away", 16.0), self.status())
        self.assertGreater(time.monotonic() - began, 8.0, "it stops twelve seconds after its receiver was made")
        first = self.status()
        time.sleep(2.0)
        st = self.status()
        self.assertEqual(st["sound_source"], {"id": choice["sound_from"], "state": "away", "arriving": False})
        self.assertNotIn(SOUND_ONLY_STOPS, [s["name"] for s in st["sources"]])
        self.assertEqual((st["playing"]["state"], st["playing"]["message"]), ("playing", ""), st["playing"])
        self.assertGreaterEqual(st["playing"]["counts"]["shown"] - first["playing"]["counts"]["shown"], 40, "the picture goes on")
        self.assertGreater(st["playing"]["audio"]["counts"]["filled"], first["playing"]["audio"]["counts"]["filled"],
                           "silence is written where the sound source's sound was")
        self.assertEqual(self.made(SOUND_ONLY_STOPS, 10), 1)

    def test_the_known_offset_of_two_senders_is_in_the_stream_as_the_stand_in_made_it(self):
        # The picture source flashes on each whole second of the stand-in's clock; the sound source clicks 200 ms
        # after each. The helper gives each its time as it ARRIVES, so the stream must hold the click 0.2 s after
        # the flash: this is the offset that ndi.sound_delay_ms is there to take out, by ear, on a box.
        import array
        from tests.test_ndi_sound import blocks
        head = ndi.mkv_header(WIDTH, HEIGHT, FPS, (48000, 2))
        out, reader = self.play(FLASHES, True, len(head) + 170 * FRAME, **self.from_source(SOUND_ONLY_CLICKS))
        self.assertEqual(out["playing"]["container"], "matroska")
        self.assertTrue(reader.full.wait(12), "five and a half seconds' worth in the time given: %d bytes came" % len(reader.data))
        got = blocks(bytes(reader.data))
        flashes = [when for t, when, b in got if t == 1 and b[:64] == b"\xff" * 64]
        clicks, quiet = [], True
        for t, when, b in got:
            if t != 2:
                continue
            samples = array.array("f", b[:len(b) - len(b) % 8])
            if sys.byteorder != "little":
                samples.byteswap()
            loud = [i for i, v in enumerate(samples) if v != 0.0][:1]
            if loud and quiet:
                clicks.append(when + (loud[0] // 2) / 48000.0)
            quiet = not samples or samples[-1] == 0.0
        self.assertGreaterEqual(len(flashes), 4, flashes)
        self.assertGreaterEqual(len(clicks), 4, clicks)
        for a, b in zip(flashes, flashes[1:]):
            self.assertAlmostEqual(b - a, 1.0, delta=0.05, msg="one flash a second: %r" % flashes)
        late = [c - max(f for f in flashes if f <= c) for c in clicks if c >= flashes[0]]
        self.assertGreaterEqual(len(late), 3, (flashes, clicks))
        for d in late:
            self.assertAlmostEqual(d, 0.2, delta=0.06, msg="each click 200 ms after its flash: flashes %r, clicks %r" % (flashes, clicks))
        self.assertTrue(level(b"".join(b for t, _, b in got if t == 2), 0.5))


if __name__ == "__main__":
    unittest.main()
