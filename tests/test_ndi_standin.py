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
ALL = (MOVING, SOUND, STILL, AT_ONCE_A, AT_ONCE_B, CHANGES) + BIG


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
            if name not in (AT_ONCE_A, AT_ONCE_B):         # the small "at once" pair is the unit test's only
                self.assertIn('="%s"\n' % name, sh)
        for want in ('pvj-ndi-runtime install "$NDI_STANDIN"', 'box play "$NDI_MOVING" raw', "systemctl restart pvj-ndi.service\n",
                     'box back "$NDI_MOVING" 20', 'box play "$NDI_SOUND" matroska', 'case still "$NDI_STILL"', 'case twice "$NDI_STILL"',
                     'case stuck "$NDI_BIG_STILL"', 'case follow "$NDI_BIG_A" "$NDI_BIG_B"', 'case follow "$NDI_BIG_A_SOUND" "$NDI_BIG_B_SOUND"',
                     'case sound "$NDI_SOUND"', 'case sound "$NDI_BIG_SOUND"', 'case soundon "$NDI_BIG_SOUND"', 'case size "$NDI_BIG_CHANGES"',
                     'case size "$NDI_BIG_CHANGES_SOUND"', 'case size "$NDI_CHANGES"', 'case restart "$NDI_MOVING"', "case chain\n",
                     "pvj-ndi-runtime remove"):
            self.assertIn(want, sh[sh.index('step "an NDI picture all the way'):], want)

    def test_the_stand_in_has_no_line_of_ndis_headers_and_says_so(self):
        with open(SOURCE) as f:
            c = f.read()
        self.assertIn("It is NOT NDI and holds nothing of NDI's", c)
        self.assertNotIn("#include <Processing.NDI", c)
        self.assertNotIn("NDIlib_source_t", c)       # the structures have names of our own; only the entry points' names are NDI's


@unittest.skipUnless(CC, "no C compiler here: the stand-in for NDI's library cannot be built")
class StandIn(unittest.TestCase):
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

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pvj-standin-run-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.said = []
        self.s = ndi.Service(self.dir, self.path, loader=ndi.CtypesLibrary, problem=lambda p: None, log=self.said.append)
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

    def on(self, sound):
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": sound})
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["runtime"]["loaded"], True, st)
        return st

    def play(self, name, sound, want):
        """Open the source called `name`, then read the pipe; returns (the helper's answer, the reader)."""
        st = self.on(sound)
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
        sys.path.insert(0, os.path.join(REPO, "tests", "ndi_standin"))
        try:
            import box_check
        finally:
            sys.path.pop(0)
        self.assertLessEqual(set(box_check.CHAIN), set(ALL))
        self.assertEqual((box_check.size_of(BIG_SOUND), box_check.size_of(BIG_SOUND, True)), ((1280, 720), (1264, 704)))

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
        self.assertEqual(sorted(s["name"] for s in st["sources"]), sorted(ALL))
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


if __name__ == "__main__":
    unittest.main()
