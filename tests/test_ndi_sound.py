# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound for the NDI input (D62, 2026-10-08): every figure of a block of sound is the sender's and is checked here
as hostile; what the pipe carries with sound (Matroska, written by the helper) is read back by a small reader of
this test's own; and, where mpv is installed (CI; not the dev Mac), the real mpv is given that stream through a
real pipe and what it decoded is looked at. NOBODY HAS HEARD ANY OF THIS: what a box plays, how late, and whether
lips and voice agree are device step N10."""
import array
import base64
import ctypes
import inspect
import math
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import types
import unittest
import zlib

from pvj import ndi
from pvj.player import Player, PlayerError
from tests.test_ndi import FakeClient, FakeLib, frame, wait
from tests.test_server import ServerBase


def sound(n=480, rate=48000, channels=2, value=None, planes=None, stride=None, fourcc=ndi.AUDIO_FLTP, address=1):
    """A block of sound as the seam hands it up. `planes` is one list of floats a channel; else every sample of
    channel c is `value` (default 0.5 for the first channel, 0.25 for the second, and so on)."""
    if planes is None:
        planes = [[(value if value is not None else 0.5 / (c + 1))] * max(n, 0) for c in range(max(channels, 0))]
    stride = 4 * n if stride is None else stride
    data = b"".join(array.array("f", pl).tobytes() + b"\xee" * max(0, stride - 4 * len(pl)) for pl in planes)
    asked = []

    def view(offset, count):
        asked.append((offset, count))
        return memoryview(data)[offset:offset + count]
    a = ndi.AudioFrame(rate, channels, n, fourcc, stride, address, view)
    a.token = asked
    return a


def floats(raw):
    out = array.array("f")
    out.frombytes(raw)
    return list(out)


class SoundLib(FakeLib):
    """The fake library with the two calls for sound, fed from a queue of its own."""
    has_audio = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sounds, self.freed_sound, self.inside = queue.Queue(), 0, threading.Event()
        self.hold = None                           # set to an Event to keep recv_audio inside "the library" until it is set

    def recv_audio(self, handle, timeout_ms):
        if self.hold is not None:
            self.inside.set()
            self.hold.wait(10)
            self.inside.clear()
            return None
        try:
            return self.sounds.get(timeout=0.1)
        except queue.Empty:
            return None

    def recv_free_audio(self, handle, a):
        self.freed_sound += 1


# ---- a reader of this test's own for what the helper writes ---------------------------------------------------------
def ebml_number(data, pos, keep_marker):
    first = data[pos]
    width = 8 - first.bit_length() + 1
    raw = data[pos:pos + width]
    if len(raw) < width:
        raise IndexError("cut")
    value = int.from_bytes(raw, "big")
    if not keep_marker:
        value &= (1 << (7 * width)) - 1
        if value == (1 << (7 * width)) - 1:
            value = None                           # "unknown length"
    return value, pos + width


MASTERS = {0x1A45DFA3, 0x18538067, 0x1549A966, 0x1654AE6B, 0xAE, 0xE0, 0xE1, 0x1F43B675}


def ebml_tree(data, pos=0, end=None):
    """[(id, value)] where value is bytes, or a list for a master element. Stops at a cut element."""
    out, end = [], len(data) if end is None else end
    while pos < end:
        try:
            ident, at = ebml_number(data, pos, True)
            size, at = ebml_number(data, at, False)
        except IndexError:
            break
        if ident in MASTERS:
            stop = end if size is None else at + size
            if stop > end:
                break
            out.append((ident, ebml_tree(data, at, stop)))
            pos = stop
        else:
            if at + size > end:
                break
            out.append((ident, data[at:at + size]))
            pos = at + size
    return out


def find(tree, *path):
    for ident, value in tree:
        if ident == path[0]:
            return value if len(path) == 1 else find(value, *path[1:])
    return None


def blocks(data):
    """[(track, seconds, payload)] of a stream the helper wrote, in the order written."""
    segment = find(ebml_tree(data), 0x18538067)
    out = []
    for ident, value in segment:
        if ident == 0x1F43B675:
            when = int.from_bytes(find(value, 0xE7), "big") * ndi.MKV_TIME_UNIT / 1e9
            block = find(value, 0xA3)
            assert block[1:4] == b"\x00\x00\x80", block[:4]
            out.append((block[0] & 0x7F, when, bytes(block[4:])))
    return out


class CheckAudioTest(unittest.TestCase):
    def test_a_good_block_and_what_is_read_of_it(self):
        self.assertEqual(ndi.check_audio(sound(4800)), (48000, 2, 2, 4800, 19200))        # as Test Patterns sent it on 2026-10-08
        self.assertEqual(ndi.check_audio(sound(800, channels=6)), (48000, 6, 2, 800, 3200))      # as macOS AV Output did
        self.assertEqual(ndi.check_audio(sound(441, rate=44100, channels=1)), (44100, 1, 1, 441, 1764))
        self.assertEqual(ndi.check_audio(sound(480, stride=4 * 480 + 64))[4], 1984)       # a padded plane
        for rate in ndi.AUDIO_RATES:
            ndi.check_audio(sound(100, rate=rate))

    def test_every_figure_of_a_block_is_held_to_a_bound_before_any_of_it_is_read(self):
        bad = [sound(480, rate=0), sound(480, rate=-48000), sound(480, rate=47999), sound(480, rate=192000), sound(480, rate=2 ** 31 - 1),
               sound(0), sound(-1, planes=[[], []]), sound(48001), sound(44101, rate=44100),
               sound(480, channels=0, planes=[]), sound(480, channels=-2, planes=[]), sound(480, channels=65, planes=[[0.0] * 480] * 2),
               sound(480, stride=4 * 480 - 1), sound(480, stride=0), sound(480, stride=-1920), sound(480, stride=4 * 480 + ndi.MAX_STRIDE_PAD + 1),
               sound(480, stride=2 ** 31 - 1), sound(480, fourcc=0x73313670), sound(480, fourcc=0), sound(480, address=0)]
        for a in bad:
            with self.assertRaises(ndi.NdiError):
                ndi.check_audio(a)
            self.assertEqual(a.token, [])                               # not a byte of it was asked for
        for field, value in (("rate", 48000.0), ("channels", True), ("samples", "480"), ("stride", None), ("fourcc", 1.5)):
            a = sound(480)
            setattr(a, field, value)
            with self.assertRaises(ndi.NdiError, msg=field):
                ndi.check_audio(a)
        with self.assertRaises(ndi.NdiError) as e:
            ndi.check_audio(sound(480, rate=22050))
        self.assertIn("22050 samples a second, which this input does not play", str(e.exception))

    def test_only_the_first_two_planes_are_ever_read_and_exactly_their_samples(self):
        a = sound(800, channels=6, stride=3200 + 32)
        ndi.audio_block(a, ndi.check_audio(a))
        self.assertEqual(a.token, [(0, 3200), (3232, 3200)])
        a = sound(4800, channels=64, planes=[[0.1] * 4800] * 2)        # says 64, and the memory holds two: the rest is never touched
        ndi.audio_block(a, ndi.check_audio(a))
        self.assertEqual(a.token, [(0, 19200), (19200, 19200)])


class LevelTest(unittest.TestCase):
    """NDI's 1.0 is a reference level, not full scale. One constant says how far below full scale it is played."""

    def test_the_reference_tone_comes_out_twenty_decibels_under_full_scale(self):
        self.assertEqual(ndi.AUDIO_HEADROOM_DB, 20)
        self.assertAlmostEqual(ndi.AUDIO_GAIN, 0.1, places=12)
        self.assertEqual(inspect.getsource(ndi).count("AUDIO_HEADROOM_DB = "), 1)       # in one named place
        n = 4800
        tone = [math.sin(2 * math.pi * 1000 * i / 48000.0) for i in range(n)]            # what Test Patterns sent: peak 1.0
        a = sound(n, planes=[tone, tone])
        data, peak, silenced = ndi.audio_block(a, ndi.check_audio(a))
        out = floats(data)
        self.assertFalse(silenced)
        self.assertEqual(len(out), 2 * n)
        self.assertAlmostEqual(20 * math.log10(max(abs(x) for x in out)), -20.0, places=2)
        self.assertAlmostEqual(20 * math.log10(peak), -20.0, places=2)
        rms = math.sqrt(sum(x * x for x in out) / len(out))
        self.assertAlmostEqual(20 * math.log10(rms), -23.01, places=1)

    def test_the_channels_go_out_side_by_side_sample_by_sample(self):
        a = sound(3, planes=[[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])
        data, _peak, _ = ndi.audio_block(a, ndi.check_audio(a))
        self.assertEqual([round(x, 6) for x in floats(data)], [0.1, -0.1, 0.2, -0.2, 0.3, -0.3])
        self.assertEqual(data, struct.pack("<6f", *[x * ndi.AUDIO_GAIN for x in (1.0, -1.0, 2.0, -2.0, 3.0, -3.0)]))      # little-endian floats
        a = sound(3, channels=1, planes=[[1.0, 2.0, 3.0]])
        self.assertEqual([round(x, 6) for x in floats(ndi.audio_block(a, ndi.check_audio(a))[0])], [0.1, 0.2, 0.3])
        a = sound(2, channels=6, planes=[[1.0, 1.0], [2.0, 2.0], [9.0, 9.0], [9.0, 9.0], [9.0, 9.0], [9.0, 9.0]])
        self.assertEqual([round(x, 6) for x in floats(ndi.audio_block(a, ndi.check_audio(a))[0])], [0.1, 0.2, 0.1, 0.2])    # the first two of six

    def test_what_is_louder_than_full_scale_is_held_there(self):
        a = sound(4, planes=[[25.0, -25.0, 10.0, 5.0], [0.0, 0.0, 0.0, 0.0]])
        data, peak, silenced = ndi.audio_block(a, ndi.check_audio(a))
        self.assertEqual([round(x, 6) for x in floats(data)[0::2]], [1.0, -1.0, 1.0, 0.5])
        self.assertEqual((peak, silenced), (1.0, False))

    def test_a_block_with_something_that_is_not_a_sample_is_played_as_silence_of_the_same_length(self):
        nan, inf = float("nan"), float("inf")
        for bad in ([0.1, nan, 0.1], [inf, 0.1, 0.1], [0.1, 0.1, -inf], [inf, -inf, 0.0], [nan, nan, nan],
                    [ndi.AUDIO_ABSURD * 1.01, 0.0, 0.0], [0.0, -3.0e38, 0.0], [3.0e38, 3.0e38, 3.0e38]):
            for planes in ([bad, [0.2, 0.2, 0.2]], [[0.2, 0.2, 0.2], bad]):
                a = sound(3, planes=planes)
                data, peak, silenced = ndi.audio_block(a, ndi.check_audio(a))
                self.assertEqual((data, peak, silenced), (bytes(24), 0.0, True), bad)
        a = sound(3, planes=[[ndi.AUDIO_ABSURD, 0.0, 0.0], [0.0, 0.0, 0.0]])            # at the bound: loud, and still sound
        self.assertFalse(ndi.audio_block(a, ndi.check_audio(a))[2])

    def test_a_plane_that_is_shorter_than_it_said_is_refused(self):
        a = sound(480)
        a.view = lambda offset, count: memoryview(bytes(count - 4))
        with self.assertRaises(ndi.NdiError):
            ndi.audio_block(a, ndi.check_audio(a))


class ContainerTest(unittest.TestCase):
    """The helper's Matroska, read back by this file's own reader and held against what mpv's reader needs
    (demux/demux_mkv.c, read at v0.35.0, v0.37.0 and v0.40.0 on 2026-10-08)."""

    def test_the_heading_says_what_mpv_needs_and_no_length_for_the_whole(self):
        head = ndi.mkv_header(1764, 992, 30.0, (48000, 2))
        tree = ebml_tree(head)
        ebml = find(tree, 0x1A45DFA3)
        self.assertEqual(find(ebml, 0x4282), b"matroska")
        self.assertEqual(int.from_bytes(find(ebml, 0x4285), "big"), 2)                  # mpv refuses a "read version" above 2
        self.assertEqual((int.from_bytes(find(ebml, 0x42F2), "big"), int.from_bytes(find(ebml, 0x42F3), "big")), (4, 8))
        self.assertIn(bytes.fromhex("18538067" + "01FFFFFFFFFFFFFF"), head)             # the segment: of unknown length, as a live stream is
        self.assertEqual(int.from_bytes(find(tree, 0x18538067, 0x1549A966, 0x2AD7B1), "big"), ndi.MKV_TIME_UNIT)
        tracks = [v for i, v in find(tree, 0x18538067, 0x1654AE6B) if i == 0xAE]
        self.assertEqual(len(tracks), 2)
        v, a = tracks
        self.assertEqual((find(v, 0xD7), find(v, 0x83), find(v, 0x86)), (b"\x01", b"\x01", b"V_UNCOMPRESSED"))
        self.assertEqual(find(v, 0xE0, 0x2EB524), b"UYVY")                              # four bytes: mpv reads them as the FourCC
        self.assertEqual((int.from_bytes(find(v, 0xE0, 0xB0), "big"), int.from_bytes(find(v, 0xE0, 0xBA), "big")), (1764, 992))
        self.assertEqual(int.from_bytes(find(v, 0x23E383), "big"), 33333333)
        self.assertEqual((find(a, 0xD7), find(a, 0x83), find(a, 0x86)), (b"\x02", b"\x02", b"A_PCM/FLOAT/IEEE"))
        self.assertEqual(struct.unpack(">d", find(a, 0xE1, 0xB5))[0], 48000.0)
        self.assertEqual((find(a, 0xE1, 0x9F), find(a, 0xE1, 0x6264)), (b"\x02", b"\x20"))
        self.assertEqual(len([1 for i, _v in find(ebml_tree(ndi.mkv_header(64, 16, 29.97)), 0x18538067, 0x1654AE6B) if i == 0xAE]), 1)
        self.assertEqual(int.from_bytes(find(ebml_tree(ndi.mkv_header(64, 16, 59.94)), 0x18538067, 0x1654AE6B, 0xAE, 0x23E383), "big"), 16683350)

    def test_a_frame_and_a_block_of_sound_each_carry_their_time_and_their_exact_length(self):
        for track, seconds, size in ((1, 0.0, 2048), (1, 1.5, 4147200), (2, 0.1, 38400), (1, 86400.123456, 16588800), (2, 12.0, 4), (1, 3.0, 126)):
            head = ndi.mkv_block_head(track, seconds, size)
            self.assertLess(len(head), 32)
            payload = bytes([track]) * size if size < 100000 else bytes(size)
            got = blocks(ndi.mkv_header(64, 16, 30.0, (48000, 2)) + head + payload + ndi.mkv_block_head(2, seconds, 4) + b"\x01\x02\x03\x04")
            self.assertEqual(len(got), 2, (track, size))
            self.assertEqual((got[0][0], round(got[0][1], 6), len(got[0][2])), (track, round(seconds, 6), size))
            self.assertEqual(got[1][2], b"\x01\x02\x03\x04")                             # and what follows is found where the lengths say
        self.assertEqual(blocks(ndi.mkv_header(64, 16, 30.0) + ndi.mkv_block_head(1, -5.0, 1) + b"x")[0][1], 0.0)     # never a time before the start

    def test_lengths_are_written_in_the_fewest_bytes_and_never_as_unknown(self):
        for n, raw in ((0, "80"), (126, "fe"), (127, "407f"), (16382, "7ffe"), (16383, "203fff"), (4147204, "103f4804")):
            self.assertEqual(ndi._ebml_size(n).hex(), raw, n)
        for n in (127, 16383, 2097151, 268435455):                                       # all ones in their shortest form would mean "unknown"
            self.assertNotEqual(set(bin(int.from_bytes(ndi._ebml_size(n), "big"))[3:]), {"1"}, n)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.fifo = os.path.join(self.dir, "ndi.fifo")
        os.mkfifo(self.fifo, 0o640)
        self.lib = SoundLib()
        self.source = ndi.clean_sources(self.lib.raw)[0][0]
        self.now = [100.0]
        self.got = bytearray()
        self.reader = None
        self.stall = threading.Event()

    def receiver(self, sound=True, audio_wait=0.3, **kw):
        self.r = ndi.Receiver(self.lib, self.source, self.fifo, log=lambda *_: None, clock=lambda: self.now[0], pipe_wait=1e9,
                              sound=sound, audio_wait=audio_wait, **kw)
        self.addCleanup(self.r.close)
        return self.r

    def read(self):
        def run():
            fd = os.open(self.fifo, os.O_RDONLY)
            try:
                while True:
                    while self.stall.is_set():
                        time.sleep(0.01)
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        return
                    self.got += chunk
            finally:
                os.close(fd)
        self.reader = threading.Thread(target=run, daemon=True)
        self.reader.start()

    def start_with_sound(self, n=480, **kw):
        """A source whose first picture and first sound are there before the pipe's form is decided."""
        r = self.receiver(**kw)
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.lib.sounds.put(sound(n))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "matroska")
        self.read()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        return r

    def stream(self, whole=False):
        # The reader runs in its own thread: on a slow machine a whole Segment may not have arrived yet when this is
        # asked (CI, 2026-10-11: "NoneType is not iterable" from `blocks`), so it is asked again for up to 3 s.
        # A picture that stands is written once more (ndi.STILL_AGAIN_SECONDS; see the tests of it below). Every
        # other test here counts the pictures of the SOURCE, so a picture that is the one before it again is left
        # out unless `whole` is asked for.
        end = time.monotonic() + 3
        while True:
            try:
                got, last = [], None
                for b in blocks(bytes(self.got)):
                    if b[0] == 1:
                        if not whole and last is not None and b[2] == last:
                            continue
                        last = b[2]
                    got.append(b)
                return got
            except (TypeError, ValueError):
                if time.monotonic() > end:
                    raise
                time.sleep(0.05)


class ReceiverSoundTest(Base):
    def test_picture_and_sound_go_into_one_stream_each_with_its_time(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        for k in range(1, 4):
            self.now[0] += 0.033
            self.lib.sounds.put(sound(480, value=0.5))                                   # 10 ms of sound
            self.lib.frames.put(frame(64, 16, fill=1 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 1 + k and len([b for b in self.stream() if b[0] == 2]) == 1 + k), k)
        got = self.stream()
        tree = ebml_tree(bytes(self.got))
        self.assertEqual(find(tree, 0x18538067, 0x1654AE6B, 0xAE, 0xE0, 0x2EB524), b"UYVY")
        video = [b for b in got if b[0] == 1]
        audio = [b for b in got if b[0] == 2]
        # the sound track begins where the picture's does: silence from 0 up to the first block (23 ms)
        self.assertEqual((round(audio[0][1], 3), audio[0][2]), (0.0, bytes(int(round(0.023 * 48000)) * 8)))
        audio = audio[1:]
        self.assertEqual([b[2] for b in video], [bytes([n]) * 2048 for n in (1, 2, 3, 4)])      # whole frames, in order, untouched
        self.assertEqual([round(b[1], 3) for b in video], [0.0, 0.033, 0.066, 0.099])           # where each arrived on the helper's clock
        self.assertEqual([len(b[2]) for b in audio], [480 * 2 * 4] * 3)
        self.assertAlmostEqual(floats(audio[0][2])[0], 0.05, places=6)                          # 0.5 brought down by 20 dB
        # the first block is placed one block's length before it came; the next follow by count, not by the clock
        self.assertEqual([round(b[1], 3) for b in audio], [0.023, 0.033, 0.043])
        st = r.status()
        self.assertEqual((st["container"], st["audio"]["rate"], st["audio"]["channels"], st["audio"]["played"]), ("matroska", 48000, 2, 2))
        self.assertEqual((st["audio"]["counts"]["received"], st["audio"]["counts"]["written"], st["audio"]["counts"]["dropped"]), (3, 3, 0))
        self.assertTrue(st["audio"]["arriving"])
        self.assertAlmostEqual(st["audio"]["level_db"], -26.0, places=1)
        self.assertFalse(st["audio"]["silent"])
        self.assertEqual(self.lib.freed_sound, 4)                                        # every block went back to the library
        self.assertEqual(st["counts"]["shown"], 4)

    def test_a_frame_is_never_copied_to_be_joined_to_its_heading(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        written = []
        real = r._write
        r._write = lambda fd, buf: (written.append((type(buf).__name__, len(buf))), real(fd, buf))[1]
        self.now[0] += 0.033
        self.lib.frames.put(frame(64, 16, fill=9))
        self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 2))
        self.assertEqual(written[-1], ("bytearray", 2048))                               # the capture thread's own buffer, as it is
        self.assertEqual(written[-2][0], "bytes")
        self.assertLess(written[-2][1], 32)
        self.assertNotIn("+ buf", inspect.getsource(ndi.Receiver._write_frame))

    def test_a_source_without_sound_is_bare_frames_exactly_as_before(self):
        r = self.receiver(audio_wait=0.2)
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r.first.wait(3))
        t = time.monotonic()
        self.assertEqual(r.decide(), "raw")
        self.assertGreaterEqual(time.monotonic() - t, 0.15)                              # it waited for sound, and not for long
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.lib.frames.put(frame(64, 16, fill=2))
        self.assertTrue(wait(lambda: len(self.got) == 4096))
        self.assertEqual(bytes(self.got), b"\x01" * 2048 + b"\x02" * 2048)               # not one byte of a container
        st = r.status()
        self.assertEqual((st["container"], st["audio"]["rate"], st["audio"]["arriving"], st["audio"]["silent"]), ("raw", 0, False, True))

    def test_with_sound_switched_off_nothing_asks_the_library_for_sound(self):
        r = self.receiver(sound=False)
        self.assertEqual(r.container, "raw")
        r.start()
        self.assertEqual(sorted(t.name for t in r._threads), ["ndi-capture", "ndi-writer"])
        self.lib.frames.put(frame(64, 16, fill=1))
        self.lib.sounds.put(sound(480))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.assertEqual((self.lib.freed_sound, self.lib.sounds.qsize()), (0, 1))        # never taken
        self.assertNotIn("audio", r.status())
        lib = FakeLib()                                                                   # a library without the calls for sound: picture only
        r2 = ndi.Receiver(lib, self.source, self.fifo, log=lambda *_: None, sound=True)
        self.assertEqual((r2.sound, r2.container), (False, "raw"))

    def test_sound_that_starts_late_or_changes_ends_the_stream_as_changed_so_that_it_is_opened_again(self):
        r = self.receiver(audio_wait=0.1)
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "raw")
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: r.status()["state"] == "changed"))
        self.assertEqual(r.status()["message"], "the source started to send sound")
        self.assertEqual(bytes(self.got), b"\x01" * 2048)                                # nothing of the sound went into the bare frames
        for change, words in ((sound(441, rate=44100), "2 channels at 44100 samples a second"), (sound(480, channels=1), "1 channels at 48000"),
                              (sound(480, channels=6), "6 channels at 48000")):
            self.setUp()
            r = self.start_with_sound()
            self.lib.sounds.put(change)
            self.assertTrue(wait(lambda: r.status()["state"] == "changed"), words)
            self.assertIn(words, r.status()["message"])
            r.close()

    def test_a_block_that_cannot_be_read_is_counted_and_the_picture_goes_on(self):
        r = self.start_with_sound()
        for bad in (sound(480, rate=12345), sound(0), sound(480, fourcc=0x1234), sound(480, stride=7)):
            self.lib.sounds.put(bad)
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["refused"] == 4))
        self.assertIn("could not be read", r.status()["audio"]["problem"])
        self.now[0] += 0.01                                                              # a block that lies after the stream's beginning
        nan = sound(480, planes=[[float("nan")] * 480, [0.5] * 480])
        self.lib.sounds.put(nan)
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["silenced"] == 1))
        self.now[0] += 0.033
        self.lib.frames.put(frame(64, 16, fill=2))
        self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 2))
        st = r.status()
        self.assertEqual(st["state"], "playing")
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertEqual(audio[-1][2], bytes(480 * 8))                                   # silence of the same length, so time goes on
        self.assertEqual(self.lib.freed_sound, 6)                                        # refused blocks went back to the library too
        self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: r.status()["audio"]["problem"] == ""))              # a good block clears the words

    def test_when_the_player_stops_reading_sound_is_dropped_and_never_piles_up(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.stall.set()
        for k in range(400):                                                             # 40 seconds of sound, 100 ms a block, as fast as it comes
            self.now[0] += 0.1
            self.lib.sounds.put(sound(4800, value=0.5))
            self.lib.frames.put(frame(64, 16, fill=3))
        self.assertTrue(wait(lambda: self.lib.sounds.qsize() == 0 and r.status()["audio"]["counts"]["received"] >= 390, 20))
        with r._cond:
            queued, held = r._queued, sum(len(b[1]) for b in r._queue)
        self.assertLessEqual(queued, ndi.AUDIO_QUEUE_SECONDS + 0.1001)
        self.assertLessEqual(held, int((ndi.AUDIO_QUEUE_SECONDS + 0.1001) * 48000) * 8)
        st = r.status()
        self.assertGreater(st["audio"]["counts"]["dropped"], 300)
        self.assertEqual(st["state"], "playing")
        self.stall.clear()
        # The player reads again, and sound that comes now is written. (This line used to wait for one more block
        # of the flood to be written, which only happens if the pipe was full when the flood ended: true on the dev
        # Mac, whose pipes hold 64 kB, and a matter of luck on Linux, whose pipes hold 1 MiB. It failed in one of the
        # two unit test jobs of each CI run on 2026-10-08.)
        self.assertTrue(wait(lambda: self.lib.sounds.qsize() == 0 and not r._queue, 10))
        before = r.status()["audio"]["counts"]["written"]
        self.now[0] += 5.0
        self.lib.sounds.put(sound(4800, value=0.5))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] >= 1 + before, 10))
        self.assertNotIn(r.status()["state"], ndi.ENDED)

    def test_a_flood_of_tiny_blocks_is_bounded_by_their_number_too(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.stall.set()
        for _ in range(40):                                                              # fill the pipe so that the writer stands still
            self.lib.frames.put(frame(64, 16, fill=3))
        self.now[0] += 0.05
        for k in range(3000):                                                            # 3000 blocks of one sample: 62 ms of sound
            self.lib.sounds.put(sound(1))
        self.assertTrue(wait(lambda: self.lib.sounds.qsize() == 0, 20))
        time.sleep(0.2)
        with r._cond:
            self.assertLessEqual(len(r._queue), ndi.AUDIO_QUEUE_BLOCKS)
            self.assertLessEqual(len(r._peaks), ndi.AUDIO_QUEUE_BLOCKS)
        self.stall.clear()

    def test_sound_that_runs_ahead_of_the_clock_loses_a_block_and_sound_after_a_gap_starts_again_at_the_clock(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        for _ in range(8):                                                               # 800 ms of sound in no time at all: a sender's clock gone wild
            self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["received"] == 8))
        self.assertGreaterEqual(r.status()["audio"]["counts"]["dropped"], 3)
        self.assertLessEqual(r._apts, ndi.AUDIO_WINDOW + 0.2001)                          # never more than the window ahead of the clock
        self.now[0] += 5.0                                                               # then nothing for five seconds
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["received"] == 9))
        # At the clock again, not where the count stood (0.5): the block that came at 5.0 began at 4.9. Two orders
        # are both right and the test's clock cannot choose between them: the block is placed first (silence up
        # to 4.9, the block at 4.9), or the writer looks first, sees five seconds without sound and writes its
        # silence up to a block's length and a tenth before the clock (4.8), and the block follows it there.
        # (On a loaded runner the second happened and this asked for 4.9 alone.)
        def last():
            real = [b for b in self.stream() if b[0] == 2 and any(b[2])]
            return round(real[-1][1], 3) if real else None
        self.assertTrue(wait(lambda: last() is not None and last() > 4.0), last())
        self.assertIn(last(), (4.8, 4.9))
        audio = [b for b in self.stream() if b[0] == 2]
        for a, b in zip(audio, audio[1:]):
            self.assertGreaterEqual(b[1] + 1e-6, a[1] + len(a[2]) / 8 / 48000.0)         # and never over sound already written

    def test_while_sound_is_missing_silence_is_written_so_the_player_does_not_wait_for_it(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        for k in range(1, 4):                                                            # two seconds of picture and not a block of sound
            self.now[0] += 0.7
            self.lib.frames.put(frame(64, 16, fill=1 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 1 + k))
        st = r.status()
        self.assertGreaterEqual(st["audio"]["counts"]["filled"], 2)
        self.assertTrue(st["audio"]["silent"])
        self.assertFalse(st["audio"]["arriving"])
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertTrue(all(b[2] == bytes(len(b[2])) for b in audio[1:]))
        ends = [b[1] + len(b[2]) / 8 / 48000.0 for b in audio]
        self.assertGreater(max(ends), 1.9)                                               # up to a tenth of a second before the last picture
        self.assertLess(max(ends), 2.1 + 0.001)
        for b in audio:
            self.assertLessEqual(len(b[2]), int(ndi.AUDIO_QUEUE_SECONDS * 48000) * 8)    # never more than a second in one go
        for a, b in zip(audio, audio[1:]):
            self.assertGreaterEqual(b[1] + 1e-6, a[1] + len(a[2]) / 8 / 48000.0)         # and never over sound already written
        # sound that returns goes after the silence
        self.lib.sounds.put(sound(480))
        self.now[0] += 0.033
        self.lib.frames.put(frame(64, 16, fill=7))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == 1))       # the first real block since the pipe was taken
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertGreaterEqual(audio[-1][1] + 1e-6, audio[-2][1] + len(audio[-2][2]) / 8 / 48000.0)

    def test_a_picture_that_stands_is_written_once_more_so_that_the_player_has_one_after_it(self):
        """mpv shows a picture only when it has the next one, and mpv 0.40 does not finish opening a stream whose
        picture track has had one block (Test Patterns with sound on, the Pi, 2026-10-12). So a picture that no other
        has followed for half a second is written again, once, at the time it is then."""
        r = self.start_with_sound()
        # the stream's first picture: again at once (until then mpv 0.40 is still opening the stream)
        self.assertTrue(wait(lambda: len([b for b in self.stream(whole=True) if b[0] == 1]) == 2))
        for k in range(1, 21):                                                           # then two seconds of only sound
            self.now[0] += 0.1
            self.lib.sounds.put(sound(4800))
            self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == k), k)
        video = [b for b in self.stream(whole=True) if b[0] == 1]
        self.assertEqual(len(video), 2)                                                  # once more, and never a third time
        self.assertEqual(video[0][2], video[1][2])
        self.assertEqual(round(video[0][1], 3), 0.0)
        self.assertAlmostEqual(video[1][1], 0.001, places=6)                             # ANOTHER time: what mpv's probe looks for
        self.assertEqual(r.status()["counts"]["shown"], 1)                               # one picture of the source
        # a picture that moves on is not written again; the last one, when the source stands again, is
        for k in (5, 6, 7):
            self.now[0] += 0.033
            self.lib.frames.put(frame(64, 16, fill=k))
            self.assertTrue(wait(lambda: self.stream(whole=True)[-1][2] == bytes([k]) * 2048 or [b for b in self.stream(whole=True) if b[0] == 1][-1][2] == bytes([k]) * 2048), k)
        self.assertEqual([b[2][:1] for b in self.stream(whole=True) if b[0] == 1], [b"\x01", b"\x01", b"\x05", b"\x06", b"\x07"])
        time.sleep(0.2)
        self.assertEqual(len([b for b in self.stream(whole=True) if b[0] == 1]), 5)      # not before its time
        self.now[0] += ndi.STILL_AGAIN_SECONDS
        self.assertTrue(wait(lambda: len([b for b in self.stream(whole=True) if b[0] == 1]) == 6))
        video = [b for b in self.stream(whole=True) if b[0] == 1]
        self.assertEqual((video[-1][2], r.status()["counts"]["shown"]), (bytes([7]) * 2048, 4))
        self.assertGreater(video[-1][1], video[-2][1])
        self.assertEqual(len(r._free) + (r._pending is not None), 3)                     # every buffer is back but the one being filled

    def test_the_first_picture_which_came_before_the_pipe_was_taken_and_its_copy_have_different_times(self):
        """As it is on a box: the first picture arrives, then sound is waited for and the player opens the pipe, so
        the picture's time is before the stream's beginning. Both it and its copy were written at time 0 (found on
        the Pi: mpv 0.40 went on waiting for a second picture)."""
        r = self.receiver()
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.lib.sounds.put(sound(480))
        self.assertTrue(r.first.wait(3))
        self.now[0] += 0.8                                                               # the picture is that old when the pipe is taken
        self.assertEqual(r.decide(), "matroska")
        self.read()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        self.assertTrue(wait(lambda: len([b for b in self.stream(whole=True) if b[0] == 1]) == 2))
        video = [b for b in self.stream(whole=True) if b[0] == 1]
        self.assertEqual((video[0][2], round(video[0][1], 6), round(video[1][1], 6)), (video[1][2], 0.0, 0.001))

    def test_bare_frames_are_never_written_again(self):
        r = self.receiver(audio_wait=0.05)
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "raw")
        self.read()
        self.assertTrue(wait(lambda: len(self.got) >= 2048))
        self.now[0] += 3.0
        time.sleep(0.4)
        self.assertEqual(bytes(self.got), bytes([1]) * 2048)

    # ---- picture and sound in step: what the SENDER made at one moment has one time in the stream ---------------
    def sender(self, n, seconds, jitter=(), w=64, h=16, begin=0.0, stalled=0.0):
        """Play a sender into the helper by the test's clock: picture k is made at k/30 s and comes at once; block j
        of sound holds the samples from j*n/48000 on and comes when its last sample exists (plus `jitter`, in turn).
        Each says in its content when the sender made it: the picture's bytes are k, the sound's samples are j.
        With `stalled`, the reader takes nothing for that long after it opened the pipe.
        Returns [(picture's time in the stream minus the sender's)], [(the same for each block of sound)]."""
        length = n / 48000.0
        r = self.receiver()
        r.start()
        base = self.now[0]
        self.lib.frames.put(frame(w, h, fill=255))                   # the picture from before the pipe: not one of the counted
        self.lib.sounds.put(sound(n, planes=[[0.0] * n] * 2))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "matroska")
        if stalled:
            self.stall.set()
        self.read()
        if not stalled:
            self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        events = sorted([(k / 30.0, 0, k) for k in range(1, int(seconds * 30))] +
                        [((j + 1) * length + (jitter[j % len(jitter)] if jitter else 0.0), 1, j) for j in range(int(seconds / length))])
        for t, kind, i in events:
            if t < begin:
                continue
            if stalled and t >= stalled and self.stall.is_set():
                self.stall.clear()
                self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
            self.now[0] = base + t
            if kind == 0:
                self.lib.frames.put(frame(w, h, fill=1 + i % 250))
                wait(lambda: self.lib.frames.qsize() == 0 and (r._pending is None or self.stall.is_set()), 2)
            else:
                self.lib.sounds.put(sound(n, planes=[[i / 1000.0] * n] * 2))
                wait(lambda: self.lib.sounds.qsize() == 0, 2)
            time.sleep(0.004)
        self.assertTrue(wait(lambda: r._pending is None and not r._queue, 5))
        time.sleep(0.1)
        got = self.stream(whole=True)
        # The stream's first picture is whatever was newest when the player began to read: it has the time 0 by
        # definition and is older than that (it is shown until the next one comes). It is not one of the counted.
        pictures, sounds, seen = [], [], {next(data[0] for track, _, data in got if track == 1)}
        for track, when, data in got:
            if track == 1 and data[0] != 255 and data[0] not in seen:                    # a picture written again is the same picture
                seen.add(data[0])
                k = next(i for t, kind, i in events if kind == 0 and 1 + i % 250 == data[0] and abs(i / 30.0 - when) < 4.0 + stalled)
                pictures.append((when - k / 30.0, when, k))
            elif track == 2:
                j = int(round(floats(data)[0] * 1000.0 / ndi.AUDIO_GAIN))
                if any(floats(data)[:1]) or j:
                    cut = n - len(data) // 8                         # samples taken off its front: it begins that much later
                    sounds.append((when - j * length - cut / 48000.0, when, j))
        return pictures, sounds, got

    def in_step(self, pictures, sounds, bound):
        """Every picture and every block of sound stands as far from the sender's time as the others, within `bound`."""
        self.assertGreater(len(pictures), 20)
        self.assertGreater(len(sounds), 5)
        middle = sorted(x[0] for x in sounds)[len(sounds) // 2]
        worst_picture = max(pictures, key=lambda x: abs(x[0] - middle))
        worst_sound = max(sounds, key=lambda x: abs(x[0] - middle))
        self.assertLessEqual(abs(worst_picture[0] - middle), bound, ("picture", worst_picture, middle))
        self.assertLessEqual(abs(worst_sound[0] - middle), bound + ndi.AUDIO_SYNC, ("sound", worst_sound, middle))

    def test_what_the_sender_made_at_one_moment_has_one_time_in_the_stream_whatever_the_blocks_length(self):
        """Proof of the stream itself, which mpv's own `avsync` cannot give: that number says how well mpv keeps to
        the times in the stream, not whether the helper gave picture and sound of one moment the same time."""
        for n in (1024, 1600, 4800, 12000):
            with self.subTest(samples=n):
                pictures, sounds, _ = self.sender(n, 3.0)
                self.in_step(pictures, sounds, 0.002)
            self.r.close()
            self.setUp()

    def test_sound_that_comes_unevenly_stays_in_step_with_the_picture(self):
        pictures, sounds, _ = self.sender(1600, 4.0, jitter=(0.0, 0.012, -0.008, 0.02, 0.003, -0.012))
        self.in_step(pictures, sounds, 0.025)                        # as far as the unevenness itself, never more and more

    def test_sound_that_begins_a_little_after_the_picture_is_led_up_to_by_silence_from_the_streams_beginning(self):
        """mpv 0.40 given a picture at 0 and its first sound at 0.211 began both at once: `avsync` 0.206, over 40 ms
        for 1.8 seconds (the Pi, 2026-10-12). The sound track begins at 0, with silence."""
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.now[0] += 0.311                                                             # the first block: 100 ms that began at 0.211
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == 1))
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertEqual([round(b[1], 3) for b in audio], [0.0, 0.211])
        self.assertEqual(audio[0][2], bytes(int(round(0.211 * 48000)) * 8))              # silence, exactly up to the sound
        self.assertTrue(any(audio[1][2]))
        self.assertEqual(r.status()["audio"]["counts"]["received"], 1)                   # the silence is not a block of the sender's

    def test_under_a_picture_that_stands_sound_that_stops_for_seconds_leaves_no_jump_in_the_sounds_times(self):
        """One picture, a tenth of a second of sound, nothing for six seconds, then sound (CI's stand-in "after a
        long gap", and what a sender does whose sound is switched on late). The stream had one second of silence
        and a jump of five; mpv 0.40 starts both tracks again at a jump of 5 s and then waits for a picture."""
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.now[0] += 0.1
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == 1))
        def due():
            with r._cond:
                return r._fill_due(self.now[0] - r._t0)
        for _ in range(24):                                                              # six seconds with nothing from the sender
            # The writer looks at the clock when it wakes (every quarter second of REAL time), and this clock is
            # the test's: so it is woken at each step and waited for. Without that, a loaded machine let more
            # than a second of this clock pass between two looks, and the writer then rightly wrote its one
            # bounded jump (that is test_a_writer_that_was_away..., below), which is not what is asked here.
            self.now[0] += 0.25
            with r._cond:
                r._cond.notify_all()
            self.assertTrue(wait(lambda: not due(), 5))
        for k in range(2, 6):
            self.now[0] += 0.1
            self.lib.sounds.put(sound(4800))
            self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == k), k)
        audio = [b for b in self.stream() if b[0] == 2]
        ends = [b[1] + len(b[2]) / 8 / 48000.0 for b in audio]
        jumps = [round(b[1] - e, 3) for e, b in zip(ends, audio[1:]) if abs(b[1] - e) > 0.001]
        self.assertEqual(jumps, [], [round(b[1], 3) for b in audio])                     # each block begins where the one before ended
        self.assertAlmostEqual(audio[0][1], 0.0, places=3)
        self.assertGreater(ends[-1], 6.2)
        self.assertGreater(r.status()["audio"]["counts"]["filled"], 5)                   # as time passed, not in one piece at the end
        self.assertEqual(len([b for b in self.stream(whole=True) if b[0] == 1]), 2)      # and no picture was written for it

    def test_a_writer_that_was_away_for_seconds_writes_one_second_of_silence_and_one_jump_never_all_of_it(self):
        """The bound: silence is for a hole of up to a second (AUDIO_QUEUE_SECONDS). A writer that could not look
        for five seconds does not write five seconds of silence in one piece; the times jump once."""
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.now[0] += 0.1
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == 1))
        self.now[0] += 5.0                                                               # one step: nothing looked meanwhile
        with r._cond:
            r._cond.notify_all()
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["filled"] >= 1))
        time.sleep(0.4)
        audio = [b for b in self.stream() if b[0] == 2]
        ends = [b[1] + len(b[2]) / 8 / 48000.0 for b in audio]
        jumps = [round(b[1] - e, 2) for e, b in zip(ends, audio[1:]) if abs(b[1] - e) > 0.001]
        self.assertEqual(jumps, [4.0], [round(b[1], 3) for b in audio])
        self.assertLessEqual(sum(len(b[2]) for b in audio[1:]) / 8 / 48000.0, ndi.AUDIO_QUEUE_SECONDS)
        self.assertEqual(r.status()["audio"]["counts"]["filled"], 1)                     # and not again and again after it

    def test_a_player_that_reads_nothing_for_seconds_after_opening_the_pipe_gets_a_stream_that_begins_whole(self):
        """The stream's clock began when the pipe was opened: a player that then read nothing for 4.7 seconds was
        given a first picture at time 0, and the next picture and the first sound seconds after it."""
        pictures, sounds, got = self.sender(4800, 6.5, w=640, h=360, stalled=4.7)
        first_picture = next(when for track, when, _ in got if track == 1)
        first_sound = next(when for track, when, _ in got if track == 2)
        self.assertEqual(first_picture, 0.0)
        self.assertLess(first_sound, 0.3, "the first sound is stamped seconds after the first picture")
        self.assertLess(min(when for _, when, _ in pictures), 0.3, "the first new picture is stamped seconds after the first")
        self.in_step(pictures, sounds, 0.002)

    def test_a_still_picture_with_sound_is_one_frame_and_the_sound_goes_on(self):
        """What Test Patterns is: one picture, then only its tone."""
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        for k in range(1, 31):                                                           # three seconds of tone, no new picture
            self.now[0] += 0.1
            self.lib.sounds.put(sound(4800))
            self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == k), k)
        got = self.stream()
        self.assertEqual(len([b for b in got if b[0] == 1]), 1)                          # the frame once, never again
        audio = [b for b in got if b[0] == 2]
        self.assertEqual(len(audio), 30)
        for a, b in zip(audio, audio[1:]):
            self.assertAlmostEqual(b[1], a[1] + 0.1, places=5)                           # each block follows the last, by count
        st = r.status()
        self.assertEqual((st["state"], st["counts"]["shown"], st["audio"]["counts"]["filled"], st["audio"]["counts"]["dropped"]), ("still", 1, 0, 0))

    def test_the_connection_is_given_back_only_when_the_pictures_and_the_sounds_threads_are_both_out(self):
        """The picture's thread ends first here (the picture changes size) while the sound's thread is still inside
        the library. Giving the connection back at that moment would free it under that thread."""
        self.lib.hold = threading.Event()
        r = self.receiver()
        r.start()
        self.assertTrue(self.lib.inside.wait(3))
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r.first.wait(3))
        self.lib.frames.put(frame(128, 16, fill=2))
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertTrue(wait(lambda: not [t for t in r._threads if t.name == "ndi-capture" and t.is_alive()]))
        time.sleep(0.2)
        self.assertEqual(self.lib.closed, 0)                                             # not yet: the sound's thread is in the library
        self.lib.hold.set()
        self.assertTrue(wait(lambda: self.lib.closed == 1))
        r.close()
        self.assertEqual(self.lib.closed, 1)                                             # once

    def test_close_leaves_the_connection_open_under_a_sound_thread_that_will_not_come_out(self):
        logged = []
        self.lib.hold = threading.Event()
        r = ndi.Receiver(self.lib, self.source, self.fifo, log=logged.append, join_wait=0.3, sound=True)
        r.start()
        self.assertTrue(self.lib.inside.wait(3))
        r.close()
        self.assertEqual(self.lib.closed, 0)
        self.assertIn("left open, not freed under it", " ".join(logged))
        self.lib.hold.set()
        self.assertTrue(wait(lambda: self.lib.closed == 1))


class ReviewTest(Base):
    """What the independent review of 2026-10-08 found in the sound's thread and its timing, each as the case that
    showed it."""

    # ---- H2 ----
    def eager(self, answer):
        class Eager(SoundLib):
            calls = 0

            def recv_audio(self, handle, timeout_ms):
                self.calls += 1
                return answer
        self.lib = Eager()
        r = self.receiver()
        r.start()
        time.sleep(0.6)
        calls = self.lib.calls
        started = time.monotonic()
        r.close()
        self.assertLess(time.monotonic() - started, 1.0)                                 # and a stop is still seen at once
        return calls

    def test_while_the_connection_is_down_the_sounds_thread_waits_and_does_not_spin(self):
        """Measured by the reviewer on the code before: 3.1 million calls in half a second."""
        calls = self.eager(ndi.AUDIO_LOST)
        self.assertLessEqual(calls, 5, "asked %d times in 0.6 s while the connection was down" % calls)

    def test_a_library_that_answers_at_once_with_nothing_is_not_asked_flat_out(self):
        calls = self.eager(None)
        self.assertLessEqual(calls, 8, "asked %d times in 0.6 s" % calls)

    def test_the_library_saying_the_connection_is_down_is_told_apart_from_no_sound_in_time(self):
        for kind, want in ((ndi.FRAME_ERROR, ndi.AUDIO_LOST), (ndi.FRAME_VIDEO, None), (0, None)):
            c = ndi.CtypesLibrary.__new__(ndi.CtypesLibrary)
            c.lib = types.SimpleNamespace(NDIlib_recv_capture_v3=lambda *a, kind=kind: kind)
            self.assertIs(c.recv_audio((ctypes.c_void_p(1),), 250), want, kind)

    # ---- M3 ----
    def test_a_second_close_does_not_free_the_connection_under_a_sound_thread_still_in_the_library(self):
        logged = []
        self.lib.hold = threading.Event()
        r = ndi.Receiver(self.lib, self.source, self.fifo, log=logged.append, join_wait=0.3, sound=True)
        r.start()
        self.assertTrue(self.lib.inside.wait(3))
        r.close()
        self.assertEqual(self.lib.closed, 0)
        r.close()                                                                        # the Service: at the next status, at the next open
        r.close()
        self.assertEqual(self.lib.closed, 0, "a second close freed the connection under the thread still in the library")
        self.assertEqual(len([m for m in logged if "left open, not freed under it" in m]), 1)
        self.lib.hold.set()
        self.assertTrue(wait(lambda: self.lib.closed == 1))
        r.close()
        self.assertEqual(self.lib.closed, 1)                                             # once

    # ---- the timing: a clock stepped a thirtieth of a second at a time, a picture at every step ----
    def play(self, block):
        r = self.start_with_sound(n=int(round(block * 48000)))
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.frames_sent, self.blocks_sent, self.tick, self.held, self.late = 0, 0, 0, [], 0.0
        return r

    def settle(self, r):
        """Every picture and every block of sound given so far has been taken and written (or left out)."""
        def done():
            c = r.audio_counts
            return (r.counts["received"] >= self.frames_sent and r._pending is None and not r._queue
                    and c["received"] + c["refused"] >= self.blocks_sent and c["written"] + c["dropped"] >= c["received"])
        self.assertTrue(wait(done, 5), (r.counts, r.audio_counts, self.frames_sent, self.blocks_sent))

    def give(self, r, n):
        self.lib.sounds.put(sound(n, value=0.5))
        self.blocks_sent += 1
        self.settle(r)

    def run_for(self, r, seconds, block, stalled=False):
        """`seconds` of a sender with pictures at 30 a second and sound in blocks of `block` seconds, each block
        given when its last sample exists (at the first step of the clock that is not before it). While `stalled`
        the blocks are held back; they are all given at once at the first step that is not stalled."""
        n = int(round(block * 48000))
        for _ in range(int(round(seconds * 30))):
            self.tick += 1
            t = self.tick / 30.0
            self.now[0] = r._t0 + t
            while (len(self.held) + self.blocks_sent + 1) * block + self.late <= t + 1e-9 and stalled:
                self.held.append(n)
            if not stalled:
                for held in self.held:
                    self.give(r, held)
                self.held = []
                while (self.blocks_sent + 1) * block + self.late <= t + 1e-9:
                    self.give(r, n)
            self.lib.frames.put(frame(64, 16, fill=1 + self.tick % 200))
            self.frames_sent += 1
            self.settle(r)

    def sound_written(self):
        return [(b[1], b[1] + len(b[2]) / 8 / 48000.0, b[2]) for b in self.stream() if b[0] == 2]

    def lead(self, r):
        """How far the end of the sound in the pipe stands ahead of the helper's clock, in seconds."""
        return self.sound_written()[-1][1] - (self.now[0] - r._t0)

    def in_order(self):
        written = self.sound_written()
        for a, b in zip(written, written[1:]):
            self.assertGreaterEqual(b[0] + 1e-4, a[1], "sound in the pipe steps back in time: %.4f after %.4f" % (b[0], a[1]))
        return written

    def test_after_a_hiccup_the_sound_comes_back_to_the_clock(self):
        """M1. A stall of 0.4 s, then the held blocks at once: the code before left the sound 0.2 s late for good
        (the silence written for the stall, and the late sound on top of it)."""
        r = self.play(0.1)
        self.run_for(r, 2.0, 0.1)
        self.assertLess(abs(self.lead(r)), 0.05)
        self.run_for(r, 0.4, 0.1, stalled=True)
        self.run_for(r, 3.0, 0.1)                                                        # "within a few seconds"
        self.assertLess(abs(self.lead(r)), 0.05, r.audio_counts)
        self.run_for(r, 6.0, 0.1)
        self.assertLess(abs(self.lead(r)), 0.05, r.audio_counts)
        self.in_order()
        self.assertGreaterEqual(r.audio_counts["adjusted"], 1)                           # it was brought back, and that was counted

    def test_sound_a_little_behind_the_clock_for_a_whole_second_gets_that_much_silence_and_one_late_block_does_not(self):
        r = self.play(0.1)
        self.run_for(r, 2.0, 0.1)
        base = dict(r.audio_counts)                                                      # two blocks late by 0.2 s, the next on time again
        self.run_for(r, 0.2, 0.1, stalled=True)
        self.run_for(r, 2.0, 0.1)
        self.in_order()
        self.assertLess(abs(self.lead(r)), 0.05, r.audio_counts)
        self.assertEqual(r.audio_counts["adjusted"], base["adjusted"])                   # two late blocks changed nothing
        self.late = 0.1                                                                  # from here on every block comes a tenth of a second later
        self.run_for(r, 0.6, 0.1)
        self.assertEqual(r.audio_counts["adjusted"], base["adjusted"], "silence was put in on the word of half a second")
        self.run_for(r, 2.0, 0.1)
        self.assertEqual(r.audio_counts["adjusted"], base["adjusted"] + 1, r.audio_counts)
        written = self.in_order()
        self.assertEqual(len([w for w in written if w[2] == bytes(len(w[2]))]), 1)       # one stretch of silence
        self.assertLess(abs(self.lead(r)), 0.05, r.audio_counts)                         # and the count is at the clock again
        self.assertEqual(r.audio_counts["written"], r.audio_counts["received"])          # silence is not counted as a block written

    def test_blocks_of_every_length_a_sender_may_use_are_played_and_longer_ones_are_refused_in_words(self):
        """M2. The reviewer's table on the code before, 12 s of picture at 30 a second: blocks of 0.1 s, 120 of 120
        written; 0.4 s, 30 written and 0.2 s late; 0.6 s, 0 of 20 and 59 fills; 1.0 s, 0 of 12. Blocks of 0.6 s and
        more were played as silence for ever without a word. Now: up to MAX_AUDIO_BLOCK every block is written and
        stays at the clock; a longer block is refused, counted, and the page says why."""
        for block in (0.0167, 0.1, 0.2, ndi.MAX_AUDIO_BLOCK):
            with self.subTest(block=block):
                self.setUp()
                r = self.play(block)
                self.run_for(r, 12.0, block)
                c = r.audio_counts
                n = int(round(block * 48000))
                self.assertEqual(c["received"], self.blocks_sent, c)
                self.assertGreater(c["written"], 0)
                self.assertGreaterEqual(c["written"], self.blocks_sent - 1, c)
                self.assertEqual((c["refused"], c["filled"]), (0, 0), c)
                self.assertLessEqual(c["adjusted"] + c["dropped"], 1, c)
                real = [w for w in self.in_order() if w[2] != bytes(len(w[2]))]
                self.assertGreaterEqual(sum(len(w[2]) for w in real), (self.blocks_sent - 1) * n * 8)
                lead = self.lead(r)
                self.assertLess(lead, 0.05, (block, c))
                self.assertGreater(lead, -(n / 48000.0) - 0.05, (block, c))              # never further back than the block that is on its way
                r.close()
        for block in (0.4, 0.6, 1.0):
            with self.subTest(block=block):
                self.setUp()
                n = int(round(block * 48000))
                r = self.receiver()
                r.start()
                self.lib.frames.put(frame(64, 16, fill=1))
                self.lib.sounds.put(sound(n))
                self.assertTrue(r.first.wait(3))
                self.assertEqual(r.decide(), "raw")                                      # the pipe carries the picture alone
                st = r.status()["audio"]
                self.assertEqual(st["counts"]["refused"], 1)
                self.assertIn("blocks of %.2f seconds" % block, st["problem"])
                self.assertIn("longer than 0.25 are not played", st["problem"])
                self.read()
                self.assertTrue(wait(lambda: r.status()["state"] == "playing"))          # and the picture goes on
                r.close()
        with self.assertRaises(ndi.NdiError):
            ndi.check_audio(sound(12001))                                                # one sample over a quarter of a second at 48 kHz
        ndi.check_audio(sound(12000))

    def test_silence_for_missing_sound_never_lies_over_a_block_queued_while_it_was_being_written(self):
        """L7. The silence's place was worked out after the queue had been taken and the count was moved after the
        silence was written, so a block queued in between was given a time inside the silence."""
        r = self.play(0.1)
        self.run_for(r, 0.1, 0.1)                                                        # one block: the count stands at 0.1
        self.assertEqual(r.audio_counts["written"], 1)
        real, injected = r._write, []

        def write(fd, buf):
            if not injected and len(buf) > 2000 and bytes(buf[-1000:]) == bytes(1000):   # the silence, about to be written
                injected.append(True)
                self.lib.sounds.put(sound(4800, value=0.5))
                self.blocks_sent += 1
                self.assertTrue(wait(lambda: r.audio_counts["received"] >= self.blocks_sent and (r._queue or r.audio_counts["dropped"])))
            return real(fd, buf)
        r._write = write
        self.now[0] = r._t0 + 0.45                                                       # 0.35 s without sound, then a picture
        self.lib.frames.put(frame(64, 16, fill=9))
        self.frames_sent += 1
        self.settle(r)
        self.assertTrue(injected, "no silence was written")
        self.assertGreaterEqual(r.audio_counts["filled"], 1)
        written = self.in_order()
        self.assertEqual(len([w for w in written if w[2] != bytes(len(w[2]))]), 2, r.audio_counts)       # both real blocks are there

    # ---- L6 ----
    def test_a_block_that_will_not_be_written_is_not_converted(self):
        converted, real = [], ndi.audio_block

        def counting(a, checked):
            converted.append(checked[3])
            return real(a, checked)
        ndi.audio_block = counting
        self.addCleanup(setattr, ndi, "audio_block", real)
        r = self.receiver()
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.lib.sounds.put(sound(4800))                                                 # before the screen has the pipe: looked at, not kept
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "matroska")
        self.assertEqual(converted, [])
        self.read()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        self.now[0] += 1.0
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.audio_counts["written"] == 1))
        self.assertEqual(converted, [4800])
        with r._cond:
            r._apts += 0.5                                                               # the count far ahead of the clock: the next block is all in the past
        self.lib.sounds.put(sound(4800))
        self.assertTrue(wait(lambda: r.audio_counts["dropped"] == 1))
        self.assertEqual(converted, [4800])                                              # dropped unread
        self.assertEqual(self.lib.freed_sound, 3)                                        # and every one given back to the library


class ServiceSoundTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = SoundLib()
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.5, problem=lambda path: None)
        self.addCleanup(self.s.close)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def test_the_helper_plays_sound_only_when_the_panel_asks_for_it_in_so_many_words(self):
        """The owner's default is OFF (SOUND_DEFAULT, kept by the panel, which sends the word every time) until the
        sound has been heard on a box (the coordinator's call, 2026-10-11: the one path never run on hardware, and
        the one that hands mpv's Matroska reader to whoever controls the helper). The helper itself assumes nothing:
        a message without the word means no sound."""
        self.assertFalse(ndi.SOUND_DEFAULT)
        self.assertIs(self.s.handle({"cmd": "configure", "on": True, "addresses": []})["sound"], False)
        self.lib.frames.put(frame(64, 16))
        self.lib.sounds.put(sound(4800))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply["playing"]["container"], "raw")
        self.assertNotIn("audio", reply["playing"])
        self.s.close()

    def test_sound_is_on_when_the_panel_says_so_and_the_answer_says_what_the_pipe_carries(self):
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        self.assertIs(st["sound"], True)
        self.lib.frames.put(frame(64, 16))
        self.lib.sounds.put(sound(4800))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "matroska"))
        self.assertEqual((reply["playing"]["audio"]["rate"], reply["playing"]["audio"]["channels"], reply["playing"]["audio"]["played"]), (48000, 2, 2))
        self.s.close()
        self.assertIs(self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": False})["sound"], False)
        self.lib.frames.put(frame(64, 16))
        self.lib.sounds.put(sound(4800))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply["playing"]["container"], "raw")
        self.assertNotIn("audio", reply["playing"])
        self.assertEqual(self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": "yes"})["ok"], False)

    def test_the_switch_while_a_source_is_open_ends_it_as_changed_so_that_it_is_opened_again(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        self.lib.frames.put(frame(64, 16))
        self.lib.sounds.put(sound(4800))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        r = self.s.receiver
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})          # said again: nothing happens
        self.assertNotIn(r.state, ndi.ENDED)
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": False})
        self.assertEqual((r.state, r.message), ("changed", "sound was switched off"))

    def test_a_source_that_changes_size_while_it_is_being_opened_is_opened_again_and_answers_with_the_new_size(self):
        """On the Pi (2026-10-12) a first press on Scan Converter was answered 409, "the source changed to 1540 x 866
        at 30.0 frames a second": the clip changed while the source was connecting. That is not a failure."""
        logged = []
        self.s.log = logged.append
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        for w in (64, 128, 128):            # the first picture, the change (seen while the helper waits for sound), and the new size again
            self.lib.frames.put(frame(w, 16))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual((reply["ok"], reply["playing"]["width"], reply["playing"]["state"]), (True, 128, "ready"), reply)
        self.assertNotIn("again", reply)
        self.assertEqual((len(self.lib.opened), self.lib.closed), (2, 1))
        self.assertIn("changed while it was being opened", " ".join(logged))
        self.s.close()
        # a source that keeps changing is tried OPEN_TRIES times and then gets the plain reason
        self.assertEqual(ndi.OPEN_TRIES, 3)
        del self.lib.opened[:]
        for w in (64, 128, 64, 128, 64, 128):
            self.lib.frames.put(frame(w, 16))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply, {"ok": False, "error": "the source changed to 128 x 16 at 29.97 frames a second"})
        self.assertEqual(len(self.lib.opened), 3)
        # The budget, said outright (a Low of the review of #132): the whole open is over before the panel stops
        # waiting, the first try fits it by itself, and a later try is started only if its least still fits.
        self.assertLess(ndi.OPEN_BUDGET_EXTRA, ndi.OPEN_WAIT_EXTRA)
        self.assertLessEqual(ndi.AUDIO_WAIT + ndi.PIPE_CLEAR_SECONDS, ndi.OPEN_BUDGET_EXTRA)       # the first try alone
        self.assertIn("timeout=FIRST_FRAME_SECONDS + OPEN_WAIT_EXTRA", inspect.getsource(ndi.Input.open))
        # measured: a source whose every try changes at the last moment of a long wait for sound
        slow = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=3.0, problem=lambda path: None,
                           pipe_clear=0.3)
        self.addCleanup(slow.close)
        slow.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        waits, real = [], slow._open_once

        def once(sid, row, wait):
            waits.append(wait)
            began = time.monotonic()
            self.lib.frames.put(frame(64, 16))
            reply = real(sid, row, wait)
            time.sleep(max(0.0, 4.0 - (time.monotonic() - began)))                      # a try that took 4 seconds
            return {"ok": False, "error": "the source changed to 128 x 16 at 29.97 frames a second", "again": True}
        slow._open_once = once
        began = time.monotonic()
        reply = slow.handle({"cmd": "open", "id": self.rid})
        took = time.monotonic() - began
        self.assertEqual(reply, {"ok": False, "error": "the source changed to 128 x 16 at 29.97 frames a second"})
        self.assertEqual(slow.open_budget(), 3.0 + ndi.OPEN_BUDGET_EXTRA)
        self.assertEqual((len(waits), waits[:2]), (3, [3.0, 3.0]), waits)
        self.assertTrue(ndi.TRY_LEAST <= waits[2] <= 10.0 - 8.0 - ndi.AUDIO_WAIT - 0.3 + 0.01, waits)      # the third waits only for what is left: 1.2 s
        self.assertLess(took, 8.0 + waits[2] + ndi.AUDIO_WAIT + 0.3 + 3.0)
        slow._open_once = real
        slow.close()
        # and one whose tries are so slow that a third no longer fits is not given one
        late = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.4, problem=lambda path: None, pipe_clear=0.3)
        self.addCleanup(late.close)
        late.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        tries = []

        def long_try(sid, row, wait):
            tries.append(wait)
            time.sleep(3.4)
            return {"ok": False, "error": "the source changed to 128 x 16 at 29.97 frames a second", "again": True}
        late._open_once = long_try
        began = time.monotonic()
        late.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(len(tries), 2)                                                  # 6.8 s gone of 7.4: the least of a third (0.5 + 0.5 + 0.3) does not fit
        self.assertLess(time.monotonic() - began, late.open_budget())
        # an end that no new try cures is answered at once, as before
        del self.lib.opened[:]
        self.lib.frames.put(frame(64, 16, fields=0))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertIn("interlaced", reply["error"])
        self.assertEqual(len(self.lib.opened), 1)

    def test_a_source_without_sound_is_opened_after_a_short_wait_and_no_longer(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        self.lib.frames.put(frame(64, 16))
        t = time.monotonic()
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        took = time.monotonic() - t
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.assertGreaterEqual(took, ndi.AUDIO_WAIT - 0.1)
        self.assertLess(took, ndi.AUDIO_WAIT + 1.0)


class SettingsSoundTest(unittest.TestCase):
    def test_the_stored_section_takes_sound_and_nothing_else_new(self):
        self.assertEqual(ndi.validate_saved({"addresses": []}), {"addresses": []})       # an older file: nothing is added to it
        self.assertEqual(ndi.validate_saved({"addresses": ["10.0.0.1"], "sound": False}), {"addresses": ["10.0.0.1"], "sound": False})
        self.assertEqual(ndi.validate_saved({"addresses": [], "sound": True}), {"addresses": [], "sound": True})
        for bad in ({"addresses": [], "sound": "no"}, {"addresses": [], "sound": 0}, {"addresses": [], "sound": None},
                    {"addresses": [], "sound": True, "gain": 30}, {"addresses": [], "library": "/tmp/evil.so"}, {"addresses": [], "headroom_db": 0}):
            with self.assertRaises(ndi.NdiError, msg=bad):
                ndi.validate_saved(bad)

    def test_what_is_read_from_a_file_a_person_may_have_edited(self):
        # anything but a real True means off: the default is off until the sound has been heard on a box (2026-10-11)
        for section, want in (({"addresses": []}, False), (None, False), ({"sound": False}, False), ({"sound": True}, True), ({"sound": "yes"}, False),
                              ({"sound": 1}, False), ("nonsense", False), ([], False)):
            self.assertIs(ndi.saved_sound(section), want, section)
        w = ndi.Wanted(lambda: True, lambda: {"ndi": {"addresses": ["10.0.0.1"], "sound": True}}, log=lambda *_: None)
        self.assertEqual((w(), w.sound), ((True, ["10.0.0.1"]), True))
        w = ndi.Wanted(lambda: True, lambda: {"ndi": {"addresses": []}}, log=lambda *_: None)
        self.assertEqual((w(), w.sound), ((True, []), False))
        w = ndi.Wanted(lambda: True, lambda: [].nothing, log=lambda *_: None)             # the settings themselves cannot be read
        w()
        self.assertIs(w.sound, False)

    def test_no_new_schema_was_needed(self):
        from pvj import settings
        self.assertEqual(settings.SCHEMA, 14)                                # and since 2026-10-11 the NDI key itself needs none either
        self.assertEqual(settings.default_settings()["ndi"], {"addresses": []})


class PanelSoundTest(unittest.TestCase):
    def test_what_a_taken_over_helper_says_about_sound_is_checked_again(self):
        good = {"rate": 48000, "channels": 6, "played": 2, "arriving": True, "level_db": -20.04, "silent": False, "problem": "",
                "counts": {"received": 10, "written": 9, "dropped": 1, "refused": 0, "silenced": 0, "filled": 2, "adjusted": 1}}
        self.assertEqual(ndi._audio(good), dict(good, level_db=-20.0))
        self.assertIsNone(ndi._audio(None))
        self.assertIsNone(ndi._audio("loud"))
        bad = ndi._audio({"rate": 47999, "channels": 9999, "played": 64, "arriving": "yes", "level_db": float("nan"), "silent": 0,
                          "problem": "x‮" + "y" * 900, "counts": {"received": -1, "written": 2 ** 80, "dropped": "3", "evil": 1, "filled": True}})
        self.assertEqual(bad, {"rate": 0, "channels": 0, "played": 0, "arriving": False, "level_db": None, "silent": True,
                               "problem": "x" + "y" * 199, "counts": {}})
        for level in (5.0, 1e9, -1e9, float("inf"), float("-inf"), "loud", True, None):
            self.assertIsNone(ndi._audio(dict(good, level_db=level))["level_db"], level)
        self.assertEqual(ndi._audio(dict(good, channels=0))["channels"], 0)
        self.assertEqual(ndi._audio(dict(good, played=3))["played"], 0)

    def test_a_stream_is_read_as_matroska_only_when_the_helper_also_says_what_its_sound_is(self):
        rid = "0123456789ab"
        base = {"id": rid, "name": "A", "state": "ready", "message": "", "width": 64, "height": 16, "fps": 30.0, "counts": {}}
        audio = {"rate": 48000, "channels": 2, "played": 2, "arriving": True, "level_db": -20.0, "silent": False, "problem": "", "counts": {}}
        self.assertEqual(ndi._playing(dict(base, container="matroska", audio=audio))["container"], "matroska")
        for p in (dict(base, container="matroska"), dict(base, container="matroska", audio=dict(audio, rate=1)), dict(base, container="mkv", audio=audio),
                  dict(base, container=["matroska"], audio=audio), dict(base, audio=audio), dict(base, container="../../etc/passwd", audio=audio)):
            self.assertEqual(ndi._playing(p)["container"], "raw", p)

    def test_with_sound_switched_off_the_pipe_is_read_as_bare_frames_whatever_the_helper_answers(self):
        """H1. The helper decided, by its answer alone, whether the player would read its bytes as Matroska."""
        rid = "0123456789ab"
        answer = {"ok": True, "playing": {"id": rid, "name": "A", "state": "ready", "message": "", "width": 64, "height": 16, "fps": 30.0,
                                          "counts": {}, "container": "matroska",
                                          "audio": {"rate": 48000, "channels": 2, "played": 2, "arriving": True, "level_db": -20.0,
                                                    "silent": False, "problem": "", "counts": {}}}}

        class Lying:
            def request(self, message, timeout=None):
                return answer if message["cmd"] == "open" else {"ok": True}

            def status(self):
                return {"ok": False}
        data = {"ndi": {"addresses": [], "sound": False}}
        i = ndi.Input(Lying(), "/nonexistent/ndi.fifo", ndi.Wanted(lambda: True, lambda: data, log=lambda *_: None), log=lambda *_: None)
        self.assertEqual(i.open(rid)["container"], "raw")
        data["ndi"]["sound"] = True
        self.assertEqual(i.open(rid)["container"], "matroska")

    def test_the_helper_is_told_whether_sound_is_wanted_and_told_again_when_that_changes(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        lib = SoundLib()
        s = ndi.Service(d, "x", loader=lambda path: lib, log=lambda *_: None, first_frame=0.3, problem=lambda path: None)
        self.addCleanup(s.close)
        data = {"ndi": {"addresses": [], "sound": False}}
        client = FakeClient(s)
        i = ndi.Input(client, s.fifo, ndi.Wanted(lambda: True, lambda: data, log=lambda *_: None), log=lambda *_: None)
        st = i.status()
        self.assertEqual((st["sound"], st["headroom_db"]), (False, 20))
        self.assertEqual([m.get("sound") for m in client.sent if m["cmd"] == "configure"], [False])
        self.assertIs(s.sound, False)
        data["ndi"]["sound"] = True
        self.assertIs(i.status()["sound"], True)
        self.assertIs(s.sound, True)
        n = len([m for m in client.sent if m["cmd"] == "configure"])
        i.status()
        self.assertEqual(len([m for m in client.sent if m["cmd"] == "configure"]), n)     # and not again while nothing changed
        # a handle made with a plain function (the older tests) asks for the default
        self.assertIs(ndi.Input(client, s.fifo, lambda: (True, []), log=lambda *_: None).sound_wanted(), False)


class ApiSoundTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.lib = SoundLib([(b"RESOLUME (Output)", b"192.168.0.20:5961")])
        self.ndidir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ndidir, True)
        self.service = ndi.Service(self.ndidir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.5, problem=lambda path: None)
        self.addCleanup(self.service.close)
        self.client = FakeClient(self.service)
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(self.client, self.service.fifo, ndi.Wanted(lambda: reg.enabled("inputs-ndi"), lambda: st.data, log=lambda *_: None),
                                 log=lambda *_: None)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": True}, token=self.full)[0], 200)

    def test_a_source_with_sound_is_loaded_as_matroska_and_one_without_as_bare_frames(self):
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "sound", "on": True}, token=self.full)[0], 200)    # off by default
        self.lib.frames.put(frame(1920, 1080, fps=(60000, 1001)))
        self.lib.sounds.put(sound(4800))
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, True))
        self.assertIn(("play_pipe", self.service.fifo, 1920, 1080, 59.94, "matroska"), self.player.calls)
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((page["sound"], page["headroom_db"], page["playing"]["container"], page["playing"]["audio"]["rate"]), (True, 20, "matroska", 48000))
        self.call("POST", "/api/control", {"action": "stop"}, token=self.full)
        self.lib.frames.put(frame(1920, 1080, fps=(60000, 1001)))
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, False))
        self.assertIn(("play_pipe", self.service.fifo, 1920, 1080, 59.94, "uyvy422"), self.player.calls)

    def test_a_helper_that_answers_matroska_while_sound_is_off_is_loaded_as_bare_frames(self):
        """H1, at the last step before the player: even if the panel's handle let "matroska" through."""
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "sound", "on": False}, token=self.full)[0], 200)
        self.lib.frames.put(frame(64, 16))
        real = self.api.ndi.open

        def lying(sid, **kw):
            return dict(real(sid, **kw), container="matroska")
        self.api.ndi.open = lying
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual(st, 200, body)
        pipes = [c for c in self.player.calls if c[0] == "play_pipe"]
        self.assertEqual([c[-1] for c in pipes], ["uyvy422"])

    def test_an_ndi_source_is_played_at_speed_one_and_the_mix_speed_comes_back_with_the_next_clip(self):
        """The real play, not a look at the source text (review, 2026-10-08)."""
        self.call("POST", "/api/control", {"action": "speed", "value": 1.86}, token=self.full)
        self.player.calls.clear()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.assertEqual([c[1] for c in self.player.calls if c[0] == "speed"], [1])
        self.assertIs(self.api._speed_held, True)
        self.api._started_playing()
        self.assertEqual([c[1] for c in self.player.calls if c[0] == "speed"], [1, 1.86])

    def test_the_switch_is_saved_sent_to_the_helper_and_needs_full_access(self):
        self.assertNotIn("sound", self.settings.data["ndi"])                              # nothing is written until someone chooses
        st, body, _ = self.call("POST", "/api/ndi", {"action": "sound", "on": False}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, False))
        self.assertEqual(self.settings.data["ndi"], {"addresses": [], "sound": False})
        self.assertIs(self.service.sound, False)
        self.lib.frames.put(frame(64, 16))
        self.lib.sounds.put(sound(4800))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[1]["sound"], False)
        self.assertIn(("play_pipe", self.service.fifo, 64, 16, 29.97, "uyvy422"), self.player.calls)
        # adding an address keeps the choice; before a choice was made it adds nothing
        self.call("POST", "/api/ndi", {"action": "add_address", "address": "192.168.1.20"}, token=self.full)
        self.assertEqual(self.settings.data["ndi"], {"addresses": ["192.168.1.20"], "sound": False})
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "sound", "on": True}, token=self.full)[1]["sound"], True)
        self.assertEqual(self.settings.data["ndi"], {"addresses": ["192.168.1.20"], "sound": True})
        for bad in ({"action": "sound"}, {"action": "sound", "on": "yes"}, {"action": "sound", "on": 1}, {"action": "volume", "on": True}):
            self.assertEqual(self.call("POST", "/api/ndi", bad, token=self.full)[0], 400, bad)
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "sound", "on": False}, token=live)[0], 403)

    def test_a_settings_file_carries_the_switch_and_a_bad_one_is_refused(self):
        from pvj import boxcare
        self.assertEqual(boxcare.check_ndi({"addresses": [], "sound": False}, None), {"addresses": [], "sound": False})
        with self.assertRaises(ndi.NdiError):
            boxcare.check_ndi({"addresses": [], "sound": "off"}, None)


class PlayerSoundTest(unittest.TestCase):
    def player(self, refuse=()):
        sent = []

        class Ipc:
            def request(self, *a):
                sent.append(a)
                if a[0] == "set_property" and a[1] in refuse:
                    raise PlayerError("mpv: property not found")
                if a[0] == "loadfile" and len(a) > 3 and "old" in refuse:
                    raise PlayerError("mpv: invalid parameter")
                return None
        p = Player.__new__(Player)
        p.ipc, p._lock, p._pipe_globals = Ipc(), threading.RLock(), None
        p.is_running = lambda: True
        p._end_source = lambda: None
        return p, sent

    def player_with_pid(self, pid):
        """As `player`, with a real is_running (the first word of a panel with its player asks for the pid)."""
        p, sent = self.player()
        del p.is_running
        p.ipc.request = (lambda req: lambda *a: (sent.append(a), pid[0])[1] if a[:2] == ("get_property", "pid") else req(*a))(p.ipc.request)
        return p, sent

    def test_a_new_panel_takes_the_narrowing_back_from_a_player_it_did_not_narrow(self):
        """Review of b46410c, medium 1. The narrowing (vd, ad, sid, ...) is a property of the whole mpv, taken back from
        an in-memory list. A panel that restarts while a source with sound is on the screen has no list, mpv keeps the
        narrowing, and every clip after it fails to find a decoder. So a Player sends the defaults for every MKV_ONLY
        key at its first word with a player and again whenever the player is another process."""
        pid = [100]
        old, sent = self.player_with_pid(pid)
        old.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")               # the panel before the restart narrowed mpv
        self.assertIn(("set_property", "vd", "rawvideo,-"), sent)
        new, sent2 = self.player_with_pid(pid)                                         # the new panel's Player: no list
        self.assertIsNone(new._pipe_globals)
        new.is_running()
        widened = {a[1]: a[2] for a in sent2 if a[0] == "set_property" and a[1] in Player.MKV_ONLY}
        self.assertEqual(widened, {k: Player.PIPE_DEFAULTS[k] for k in Player.MKV_ONLY})
        del sent2[:]
        new.is_running()
        self.assertEqual([a for a in sent2 if a[0] == "set_property"], [], "sent again for the same player")
        pid[0] = 101                                                                   # the player is another process
        new.is_running()
        self.assertEqual({a[1] for a in sent2 if a[0] == "set_property"}, set(Player.MKV_ONLY) | {"audio-delay"})
        # a player this side has just started is a fresh process: nothing to widen, and nothing to wait for while it
        # makes its window (_spawn marks it; tests/test_player.py DeafAtTheStartTest counts on the start staying quiet)
        del sent2[:]
        pid[0] = 103
        new._own_start = True
        new.is_running()
        self.assertEqual([a for a in sent2 if a[0] == "set_property"], [], "a player this side started was widened")
        # and not in the middle of a pipe load, which narrows on purpose
        del sent2[:]
        pid[0] = 102
        new.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        sets = [a for a in sent2 if a[0] == "set_property" and a[1] == "vd"]
        self.assertEqual(sets[-1], ("set_property", "vd", "rawvideo,-"), "the load's own narrowing must be the last word")

    def test_the_stream_with_sound_is_loaded_with_mpvs_own_reader_named_and_no_cache(self):
        self.assertIn("matroska", Player.PIPE_FORMATS)
        p, sent = self.player()
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 1920, 1080, 29.97, "matroska")
        load = [a for a in sent if a[0] == "loadfile"][0]
        self.assertEqual(load, ("loadfile", "/run/pvj-ndi/ndi.fifo", "replace", -1,
                                "demuxer=mkv,cache=no,cache-pause=no,initial-audio-sync=no,demuxer-readahead-secs=0,demuxer-max-bytes=" + Player.pipe_queue(1920, 1080, 29.97)
                                + ",audio-delay=0.0"))
        self.assertNotIn("rawvideo", load[4])
        for word in ("aid", "mute", "volume", "audio-device", "af="):                     # sound is left to what the player already does
            self.assertNotIn(word, load[4])

    def test_for_the_helpers_stream_the_player_decodes_uncompressed_video_and_float_pcm_and_nothing_else(self):
        """H1 of the review of 2026-10-08. The helper is the part that could be taken over, and with sound the
        player reads its bytes as Matroska, which can name any codec. So that one load is narrowed first."""
        only = Player.MKV_ONLY
        self.assertEqual((only["vd"], only["ad"]), ("rawvideo,-", "pcm_f32le,-"))          # a list that ends in "-": no other decoder
        self.assertEqual((only["sid"], only["ordered-chapters"], only["cover-art-auto"], only["embeddedfonts"]), ("no", "no", "no", "no"))
        p, sent = self.player()
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        load = [i for i, a in enumerate(sent) if a[0] == "loadfile"][0]
        before = [a[1:] for a in sent[:load] if a[0] == "set_property"]
        for k, v in only.items():
            self.assertIn((k, v), before, k)                                             # every one of them BEFORE the load
        self.assertEqual(set(p._pipe_globals), set(only))                             # (the sound's delay goes with the load, not here)
        # bare frames are loaded as before: nothing of this is set for them
        p2, sent2 = self.player()
        p2.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "uyvy422")
        self.assertFalse([a for a in sent2 if a[0] == "set_property" and a[1] in only])
        self.assertIsNone(p2._pipe_globals)

    def test_the_narrowing_is_taken_back_before_the_next_thing_is_loaded(self):
        p, sent = self.player()
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        sent.clear()
        p.play_pipe("/run/pvj/capture.fifo", 1280, 720, 30)                               # the next thing: bare frames from the capture input
        load = [i for i, a in enumerate(sent) if a[0] == "loadfile"][0]
        undone = dict(a[1:] for a in sent[:load] if a[0] == "set_property")
        for k in Player.MKV_ONLY:
            self.assertEqual(undone.get(k), Player.PIPE_DEFAULTS[k], k)
        self.assertEqual((Player.PIPE_DEFAULTS["vd"], Player.PIPE_DEFAULTS["ad"], Player.PIPE_DEFAULTS["sid"]), ("", "", "auto"))
        self.assertIsNone(p._pipe_globals)
        self.assertIn("self._undo_pipe_globals()", inspect.getsource(Player._play))        # and a clip does the same

    def test_one_that_could_not_be_taken_back_is_tried_again(self):
        p, sent = self.player()
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        real = p.ipc.request

        def flaky(*a):
            if a[:2] == ("set_property", "vd"):
                raise PlayerError("mpv: timed out")
            return real(*a)
        p.ipc.request = flaky
        p._undo_pipe_globals()
        self.assertEqual(p._pipe_globals, ["vd"])                                        # not forgotten
        p.ipc.request = real
        sent.clear()
        p._undo_pipe_globals()
        self.assertEqual([a for a in sent], [("set_property", "vd", "")])
        self.assertIsNone(p._pipe_globals)

    def test_a_player_that_will_not_be_narrowed_is_not_given_the_stream(self):
        p, sent = self.player(refuse=("ad",))
        with self.assertRaises(PlayerError):
            p.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        self.assertFalse([a for a in sent if a[0] == "loadfile"])                         # nothing was loaded
        self.assertIn(("set_property", "vd", ""), sent)                                  # and what was already set was taken back

    def test_on_an_old_mpv_the_pipes_options_and_the_narrowing_are_both_taken_back(self):
        p, sent = self.player(refuse=("old",))
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 64, 16, 30, "matroska")
        self.assertEqual(set(p._pipe_globals), set(Player.MKV_ONLY) | {"audio-delay", "demuxer", "cache", "cache-pause", "initial-audio-sync", "demuxer-readahead-secs", "demuxer-max-bytes"})
        self.assertEqual([a for a in sent if a[0] == "loadfile"][-1], ("loadfile", "/run/pvj-ndi/ndi.fifo", "replace"))   # then pause off, then the pid the pipe was loaded into (pipe_is)

    def delays(self, sent):
        """The delay as mpv was given it, in order: a number set as a property, or ("load", n) given with a load."""
        out = []
        for a in sent:
            if a[:2] == ("set_property", "audio-delay"):
                out.append(a[2])
            elif a[0] == "loadfile" and len(a) > 4 and "audio-delay=" in a[4]:
                out.append(("load", float(dict(kv.split("=", 1) for kv in a[4].split(","))["audio-delay"])))
        return out

    def test_the_sounds_delay_goes_with_the_load_so_the_clip_before_it_is_never_shifted(self):
        """D87, and the review of #132. Two senders share no clock, so the owner moves the sound by ear: mpv's
        audio-delay. Where mpv takes options with the load it is that file's alone: nothing is set on the player
        before the load (the clip still playing is not shifted), and mpv takes it back when the file ends."""
        fifo = "/run/pvj-ndi-pipe/ndi.fifo"
        p, sent = self.player()
        p.play_pipe(fifo, 64, 16, 30, "matroska", 0.12)
        self.assertEqual(self.delays(sent), [("load", 0.12)])                             # with the load, and no property before it
        self.assertNotIn("audio-delay", p._pipe_globals)
        del sent[:]
        p.play_pipe(fifo, 64, 16, 30, "matroska")                                         # each load says its own, also "none"
        p.play_pipe(fifo, 64, 16, 30, "matroska", -0.04)
        self.assertEqual(self.delays(sent), [("load", 0.0), ("load", -0.04)])
        del sent[:]
        p.play_pipe("/run/pvj/capture.fifo", 1280, 720, 30)                               # bare frames: no sound, no delay
        self.assertEqual(self.delays(sent), [])
        self.assertEqual(Player.PIPE_DEFAULTS["audio-delay"], 0)

    def test_on_the_older_road_and_after_a_change_while_it_plays_the_delay_is_taken_back_like_the_narrowing(self):
        fifo = "/run/pvj-ndi-pipe/ndi.fifo"
        # mpv 0.35 takes no options with a load: there it is the whole player's, set a moment before the load (the
        # outgoing clip is shifted for that moment: said in player.py), and taken back before the next thing
        p, sent = self.player(refuse=("old",))
        p.play_pipe(fifo, 64, 16, 30, "matroska", 0.2)
        self.assertEqual(self.delays(sent), [("load", 0.2), 0.2])                         # the load with options, which 0.35 refuses; then the property
        self.assertIn("audio-delay", p._pipe_globals)
        del sent[:]
        p.play_pipe("/run/pvj/capture.fifo", 1280, 720, 30)
        load = [i for i, a in enumerate(sent) if a[0] == "loadfile"][0]
        self.assertEqual(self.delays(sent[:load]), [0])                                   # before the next load
        # changed while the source plays, on a new mpv: a property, and then on the list of what is taken back
        p, sent = self.player()
        p.play_pipe(fifo, 64, 16, 30, "matroska", 0.1)
        self.assertIs(p.pipe_audio_delay(fifo, 0.25), True)
        self.assertIs(p.pipe_audio_delay(fifo, 7), True)
        self.assertEqual(self.delays(sent), [("load", 0.1), 0.25, 0.5])
        self.assertEqual(p._pipe_globals.count("audio-delay"), 1)
        for road in (Player._play, Player._play_pipe):                                    # a clip and a pipe both undo first
            self.assertIn("self._undo_pipe_globals()", inspect.getsource(road))
        real = p.ipc.request

        def flaky(*a):                     # one that could not be taken back is kept and tried again
            if a[:2] == ("set_property", "audio-delay"):
                raise PlayerError("mpv: timed out")
            return real(*a)
        p.ipc.request = flaky
        p._undo_pipe_globals()
        self.assertEqual(p._pipe_globals, ["audio-delay"])
        p.ipc.request = real
        del sent[:]
        p._undo_pipe_globals()
        self.assertEqual(sent, [("set_property", "audio-delay", 0)])
        self.assertIsNone(p._pipe_globals)
        # a panel that started again under a player it did not set takes it back (the narrowing's own road)
        pid = [100]
        old, _ = self.player_with_pid(pid)
        old.play_pipe(fifo, 64, 16, 30, "matroska", 0.2)
        old.pipe_audio_delay(fifo, 0.3)
        new, sent2 = self.player_with_pid(pid)
        new.is_running()
        self.assertEqual(self.delays(sent2), [0])

    def test_the_delay_is_held_to_its_bounds_and_only_the_stream_with_sound_is_ever_moved(self):
        self.assertEqual([Player.pipe_delay(v) for v in (0.12, -0.04, 9, -9, 0.1234567, True, "0.2", None, float("nan"))],
                         [0.12, -0.04, 0.5, -0.25, 0.123, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(Player.PIPE_DELAY_SECONDS, tuple(ms / 1000.0 for ms in ndi.SOUND_DELAY_MS))
        fifo = "/run/pvj-ndi-pipe/ndi.fifo"
        p, sent = self.player()
        self.assertIs(p.pipe_audio_delay(fifo, 0.1), False)                               # nothing plays
        p.play_pipe(fifo, 64, 16, 30, "uyvy422", 0.3)                                     # sound off, or a source without sound
        self.assertIs(p.pipe_audio_delay(fifo, 0.1), False)
        p.play_pipe("/run/pvj/capture.fifo", 64, 16, 30)
        self.assertIs(p.pipe_audio_delay(fifo, 0.1), False)                               # another pipe
        self.assertEqual(self.delays(sent), [])
        self.assertFalse(p._pipe_globals)
        p.play_pipe(fifo, 64, 16, 30, "matroska")
        p.play_pipe(fifo, 64, 16, 30, "uyvy422")                                          # the same pipe again as bare frames
        self.assertIs(p.pipe_audio_delay(fifo, 0.1), False)

    def test_the_players_queue_is_sized_from_the_picture_and_the_longest_block_of_sound(self):
        """M4. mpv's limit is one total for picture and sound; the sound trails the picture by a block's length."""
        mib = lambda w, h, fps: int(Player.pipe_queue(w, h, fps)[:-3])
        self.assertTrue(Player.pipe_queue(1920, 1080, 30).endswith("MiB"))
        for w, h, fps in ((1280, 720, 30), (1920, 1080, 29.97), (1920, 1080, 60), (1764, 992, 30)):
            frame_bytes = 2 * w * h
            held = mib(w, h, fps) * 1024 * 1024 / float(frame_bytes) / fps               # seconds of picture the queue takes
            if mib(w, h, fps) < 256:
                self.assertGreaterEqual(held, 1.0, (w, h, fps))
            self.assertGreaterEqual(held, 2 * ndi.MAX_AUDIO_BLOCK + 0.2 if mib(w, h, fps) < 256 else 1.0, (w, h, fps))
        self.assertEqual(mib(64, 16, 30), 32)                                            # never under what bare frames get
        self.assertEqual(mib(3840, 2160, 60), 256)                                       # never over this
        self.assertEqual(mib(1920, 1080, 30), 119)
        self.assertEqual(mib(1920, 1080, 60), 238)
        self.assertLessEqual(ndi.MAX_AUDIO_BLOCK, 0.25)


# ---- one sound source for the whole box (D87, 2026-10-12) -----------------------------------------------------------
PICTURE, VOICE = (b"SCAN (Converter)", b"192.168.0.20:5961"), (b"VLC (Audio)", b"192.168.0.20:5962")
PID, VID = ndi.source_id("SCAN (Converter)", "192.168.0.20"), ndi.source_id("VLC (Audio)", "192.168.0.20")
VOICE_ROW = {"id": VID, "name": "VLC (Audio)", "from": "192.168.0.20:5962"}
PICTURE_ROW = {"id": PID, "name": "SCAN (Converter)", "from": "192.168.0.20:5961"}
# The release before the one sound source (master at 6e0145b, #129). An older release is named by a commit.
BEFORE_SOUND_FROM = "6e0145b6ffe8a9df68804f149055d1acca949470"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FeedLib(SoundLib):
    """The fake library with two senders and a record of every connection: what it was opened with (audio only or
    not), which were asked for sound, which were closed. Sound for a connection that was opened for sound alone
    comes from `sounds`; sound of a picture's own connection from `own`; a sound-only connection never has a picture."""

    def __init__(self, sources=(PICTURE, VOICE)):
        super().__init__(sources)
        self.opens, self.kinds, self.asked, self.closed_handles = [], {}, set(), []
        self.own, self.down = queue.Queue(), False

    def recv_open(self, raw, audio_only=False):
        handle = ("recv", len(self.opens) + 1)
        self.opens.append((raw, audio_only))
        self.kinds[handle] = audio_only
        return handle

    def recv_capture(self, handle, timeout_ms):
        if self.kinds[handle]:
            time.sleep(0.05)
            return None
        return super().recv_capture(handle, timeout_ms)

    def recv_audio(self, handle, timeout_ms):
        self.asked.add(handle)
        if self.hold is not None:
            return super().recv_audio(handle, timeout_ms)
        if self.kinds[handle] and self.down:
            return ndi.AUDIO_LOST
        try:
            return (self.sounds if self.kinds[handle] else self.own).get(timeout=0.1)
        except queue.Empty:
            return None

    def recv_close(self, handle):
        self.closed_handles.append(handle)
        self.closed += 1

    def sound_opens(self):
        return [raw for raw, audio_only in self.opens if audio_only]


class LibraryAskTest(unittest.TestCase):
    def test_the_sound_source_is_asked_for_its_sound_alone_and_a_picture_for_everything_as_before(self):
        spec, _keep = ndi.CtypesLibrary.recv_spec(PICTURE)
        self.assertEqual((spec.bandwidth, spec.color, spec.fields), (100, 100, True))
        spec, _keep = ndi.CtypesLibrary.recv_spec(VOICE, audio_only=True)
        self.assertEqual((spec.bandwidth, spec.color, spec.fields), (ndi.BANDWIDTH_AUDIO_ONLY, 100, True))
        self.assertEqual(ndi.BANDWIDTH_AUDIO_ONLY, 10)          # NDIlib_recv_bandwidth_audio_only, read from the owner's SDK 6.3.2
        self.assertEqual(ctypes.string_at(spec.source.name), VOICE[0])


class FeedBase(Base):
    def setUp(self):
        super().setUp()
        self.lib = FeedLib()
        rows = {r["id"]: r for r in ndi.clean_sources(self.lib.raw)[0]}
        self.source, self.voice, self.logged = rows[PID], rows[VID], []

    def feed(self, **kw):
        f = ndi.SoundFeed(self.lib, self.voice, log=self.logged.append, clock=lambda: self.now[0], **kw)
        self.addCleanup(f.close)
        f.start()
        return f

    def play_with(self, feed, block=None):
        r = self.receiver(feed=feed)
        r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.lib.sounds.put(block or sound(480))
        self.assertTrue(r.first.wait(3))
        self.assertTrue(wait(lambda: r.audio is not None))
        self.assertEqual(r.decide(), "matroska")
        self.read()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        return r


class SoundFeedTest(FeedBase):
    def test_another_senders_sound_goes_into_the_pictures_stream_and_the_picture_is_never_asked_for_sound(self):
        feed = self.feed()
        self.lib.own.put(sound(480, value=0.9))                                          # the picture's own sound: never taken
        r = self.play_with(feed)
        self.assertEqual(sorted(t.name for t in r._threads), ["ndi-capture", "ndi-writer"])      # no sound thread of its own
        self.assertEqual(self.lib.opens, [(VOICE, True), (PICTURE, False)])
        for _ in range(4):
            self.now[0] += 0.01
            self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] >= 3))
        self.assertEqual(self.lib.asked, {("recv", 1)})                                  # only the sound connection
        self.assertEqual(self.lib.own.qsize(), 1)
        got = self.stream()
        self.assertEqual(find(ebml_tree(bytes(self.got)), 0x18538067, 0x1654AE6B)[1][0], 0xAE)       # the same writer: a picture track and a sound track
        audio = [b for b in got if b[0] == 2]
        self.assertEqual(audio[0][1], 0.0)                                               # the sound track begins at 0, as with a picture's own sound
        for a, b in zip(audio, audio[1:]):
            self.assertAlmostEqual(b[1], a[1] + len(a[2]) / 8 / 48000.0, places=5)       # one block after the other, by count
        self.assertAlmostEqual(max(floats(audio[-1][2])), 0.5 * ndi.AUDIO_GAIN, places=6)
        st = r.status()["audio"]
        self.assertEqual((st["rate"], st["channels"], st["played"], st["arriving"]), (48000, 2, 2, True))

    def test_every_block_of_the_sound_source_is_held_to_the_same_bounds_before_any_of_it_is_read(self):
        feed = self.feed()
        r = self.play_with(feed)
        for bad in (sound(480, rate=22050), sound(480, fourcc=0x20203631), sound(480, stride=100), sound(24000), sound(480, channels=0)):
            self.lib.sounds.put(bad)
            self.assertTrue(wait(lambda: not self.lib.sounds.qsize() and self.lib.freed_sound >= 2))
            time.sleep(0.05)
            self.assertEqual(bad.token, [], "not a byte of it was read")
        self.assertEqual(r.status()["audio"]["counts"]["refused"], 5)
        self.assertEqual(r.state, "playing")
        six = sound(480, channels=6)                                                     # a change of channels is a new stream, as ever
        self.lib.sounds.put(six)
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertEqual(six.token, [])

    def test_with_nobody_attached_a_block_is_given_back_unread(self):
        self.feed()
        a = sound(480)
        self.lib.sounds.put(a)
        self.assertTrue(wait(lambda: self.lib.freed_sound == 1))
        self.assertEqual(a.token, [])

    def test_when_the_sound_source_drops_silence_is_written_and_the_picture_goes_on(self):
        feed = self.feed()
        r = self.play_with(feed)
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.lib.down = True
        for k in range(1, 4):
            self.now[0] += 0.7
            self.lib.frames.put(frame(64, 16, fill=1 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 1 + k))
        self.assertTrue(wait(lambda: feed.lost))
        st = r.status()
        self.assertEqual(st["state"], "playing")
        self.assertGreaterEqual(st["audio"]["counts"]["filled"], 2)
        self.assertFalse(st["audio"]["arriving"])
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertGreater(max(b[1] + len(b[2]) / 8 / 48000.0 for b in audio), 1.9)
        for a, b in zip(audio, audio[1:]):
            self.assertGreaterEqual(b[1] + 1e-6, a[1] + len(a[2]) / 8 / 48000.0)         # the sound's times never step back or leave a hole
        self.assertIn("dropped; waiting for it", " ".join(self.logged))
        written = st["audio"]["counts"]["written"]
        self.lib.down = False                                                            # and it is back: real sound after the silence
        self.lib.sounds.put(sound(480))
        self.now[0] += 0.033
        self.lib.frames.put(frame(64, 16, fill=9))
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] == written + 1))
        self.assertFalse(feed.lost)
        self.assertEqual(self.lib.closed_handles, [])

    def test_a_change_of_rate_ends_the_picture_as_changed_and_the_sound_connection_stays_for_the_next(self):
        feed = self.feed()
        r = self.play_with(feed)
        self.lib.sounds.put(sound(441, rate=44100))
        self.assertTrue(wait(lambda: r.status()["state"] == "changed"))
        self.assertIn("2 channels at 44100 samples a second", r.status()["message"])
        r.close()
        self.assertTrue(feed.alive())
        self.assertEqual(self.lib.closed_handles, [("recv", 2)])                         # the picture's, and only it
        self.stall.clear()
        self.reader.join(3)
        del self.got[:]
        r2 = self.play_with(feed, block=sound(441, rate=44100))
        self.assertEqual((r2.audio["rate"], len(self.lib.sound_opens())), (44100, 1))
        self.assertIn(struct.pack(">d", 44100.0), bytes(self.got[:400]))

    def test_a_picture_opened_while_the_sound_source_is_quiet_has_its_sound_track_from_the_start(self):
        """The owner's rig: the picture sender changes size at every clip (so the picture is opened again), and the
        player that sends the sound sends none between two clips. The sound connection remembers what its sound
        was, so the new stream has a sound track at once, silence as time passes, and the sound when it comes:
        the picture is not opened a second time for it."""
        feed = self.feed()
        r = self.play_with(feed)
        r.close()
        self.stall.clear()
        self.reader.join(3)
        del self.got[:]
        self.assertEqual(feed.format, {"rate": 48000, "channels": 2, "played": 2})
        r2 = self.receiver(feed=feed, audio_wait=30)                                     # not a block of sound from here on
        r2.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r2.first.wait(3))
        began = time.monotonic()
        self.assertEqual(r2.decide(), "matroska")
        self.assertLess(time.monotonic() - began, 1.0)                                   # and no wait for it
        self.read()
        self.assertTrue(wait(lambda: r2.status()["state"] == "playing"))
        for k in range(1, 4):
            self.now[0] += 0.7
            self.lib.frames.put(frame(64, 16, fill=1 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 1 + k))
        audio = [b for b in self.stream() if b[0] == 2]
        self.assertTrue(audio and all(b[2] == bytes(len(b[2])) for b in audio))          # silence, as time passes
        self.assertEqual(audio[0][1], 0.0)
        self.assertFalse(r2.status()["audio"]["arriving"])
        self.lib.sounds.put(sound(480))                                                  # the next clip's sound
        self.now[0] += 0.033
        self.lib.frames.put(frame(64, 16, fill=9))
        self.assertTrue(wait(lambda: r2.status()["audio"]["counts"]["written"] == 1))
        self.assertEqual(r2.state, "playing")                                            # not "changed"
        audio = [b for b in self.stream() if b[0] == 2]
        for a, b in zip(audio, audio[1:]):
            self.assertGreaterEqual(b[1] + 1e-6, a[1] + len(a[2]) / 8 / 48000.0)
        self.lib.sounds.put(sound(441, rate=44100))                                      # and sound in another form is a new stream, as ever
        self.assertTrue(wait(lambda: r2.state == "changed"))
        self.assertEqual(feed.format["rate"], 44100)

    def test_what_the_sound_source_had_last_is_put_right_by_a_block_that_comes_before_the_pipe_is_decided(self):
        """The remembered form is a guess until a block of this stream shows it. A block in another form that comes
        while the picture is still connecting must not end it (the press would be refused for a change nobody saw)."""
        feed = self.feed()
        feed.format = {"rate": 44100, "channels": 2, "played": 2}
        r = self.receiver(feed=feed)
        r.start()
        self.assertEqual(r.audio["rate"], 44100)
        self.lib.sounds.put(sound(480, channels=1))
        self.assertTrue(wait(lambda: r.audio == {"rate": 48000, "channels": 1, "played": 1}))
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(r.first.wait(3))
        self.assertEqual((r.decide(), r.state), ("matroska", "ready"))
        self.read()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        self.assertIn(struct.pack(">d", 48000.0), bytes(self.got[:400]))

    def again(self, feed, block):
        """The picture is opened again on the same sound connection (as the panel does after "changed")."""
        self.r.close()
        self.stall.clear()
        self.reader.join(3)
        del self.got[:]
        return self.play_with(feed, block=block)

    def test_a_sound_source_that_keeps_changing_its_sound_cannot_take_the_picture_off_the_screen_in_a_loop(self):
        """The review of #132, M4. Every change of rate or channels in the SOUND source ended the picture as
        "changed", and the panel opened it again each time: a second sender could blink the picture at will. A
        first change still ends the stream once (the new track needs it). After an ending the next is let through
        no sooner than a pause later, 3 s, then 6, 12 ... 60, and the picture plays on without the sound meanwhile."""
        other = lambda: sound(441, rate=44100)                                           # noqa: E731
        feed = self.feed()
        r = self.play_with(feed)
        t0 = self.now[0]
        self.lib.sounds.put(other())                                                     # the first change: handed on
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertFalse(feed.status()["restless"])
        r = self.again(feed, other())
        self.assertEqual(r.audio["rate"], 44100)
        self.now[0] += 1.0
        back = sound(480)
        self.lib.sounds.put(back)                                                        # and back again a second later: held
        self.assertTrue(wait(lambda: feed.status()["restless"]))
        self.assertAlmostEqual(feed.pace.quiet_until - t0, 3.0, places=3)                # until 3 s after the ending before
        self.assertEqual(back.token, [])                                                 # given back unread
        self.assertIsNone(feed.known())
        self.assertIn("keeps changing its sound; pictures play without it for 2 seconds", " ".join(self.logged))
        for k in range(2):                                                               # the picture plays on, silence is written
            self.now[0] += 0.7
            self.lib.sounds.put(sound(480))
            self.lib.frames.put(frame(64, 16, fill=2 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 2 + k))
        self.assertEqual(r.state, "playing")
        self.assertGreaterEqual(r.status()["audio"]["counts"]["filled"], 1)
        self.assertTrue(feed.status()["restless"])
        self.now[0] += 0.7                                                               # the pause is over: its sound, as it is now, ends the stream ONCE
        t1 = self.now[0]
        self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertFalse(feed.status()["restless"])
        r = self.again(feed, sound(480))
        self.lib.sounds.put(other())                                                     # it changes again: the pause is twice as long
        self.assertTrue(wait(lambda: feed.status()["restless"]))
        self.assertAlmostEqual(feed.pace.quiet_until - t1, 6.0, places=3)
        self.now[0] += 5.0
        self.lib.sounds.put(sound(480))
        self.lib.sounds.put(other())                                                     # and again while it is held: never handed
        self.assertTrue(wait(lambda: not self.lib.sounds.qsize()))
        time.sleep(0.2)
        self.assertEqual(r.state, "playing")
        self.now[0] += 1.5                                                               # the pause is over: one ending, and the next pause is 12 s
        t2 = self.now[0]
        self.lib.sounds.put(other())
        self.assertTrue(wait(lambda: r.state == "changed"))
        r = self.again(feed, other())
        self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: feed.status()["restless"]))
        self.assertAlmostEqual(feed.pace.quiet_until - t2, 12.0, places=3)
        self.assertEqual(r.state, "playing")
        self.assertEqual((ndi.Input.RETRY_SECONDS, ndi.Input.RETRY_MAX), (ndi.FEED_QUIET_SECONDS, ndi.FEED_QUIET_MAX))   # the pauses of Input.tick
        # THE HONEST RIG: its sound changes form now and then. It waits the pause and has its sound back; and after
        # ten minutes without an ending the pauses begin at the shortest again.
        self.now[0] += 300                                                               # five minutes later: let through at once
        self.lib.sounds.put(sound(480))
        self.assertTrue(wait(lambda: r.state == "changed"))
        r = self.again(feed, sound(480))
        self.assertEqual(r.audio["rate"], 48000)
        self.assertFalse(feed.status()["restless"])
        self.assertEqual(feed.pace.gap, 24.0)                                            # (counted on: the pause after it is longer, not shorter)
        self.now[0] += ndi.FEED_RESET_SECONDS + 1
        self.lib.sounds.put(other())
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertEqual((feed.status()["restless"], feed.pace.gap), (False, ndi.FEED_QUIET_SECONDS))
        self.assertEqual(len(self.lib.sound_opens()), 1)

    def test_a_greedy_sound_source_gets_one_ending_a_minute_in_the_long_run_whatever_it_does(self):
        """The re-check of #132: with a "calm" of half a minute a sender could end the stream at 0, 3 and 9 seconds,
        rest until 39 and begin again: three re-opens in 39 seconds for ever. The pause now grows with every
        ending and goes back only after ten minutes without one. Asked of the pace itself, a tenth of a second at
        a time, for an hour, for a sender that takes every ending it can and for ones that rest between bursts."""
        def run(wants, seconds=3600.0):
            pace, ends, t = ndi.SoundPace(), [], 0.0
            while t < seconds:
                if wants(t, ends) and pace.may_end(t)[0]:
                    ends.append(round(t, 1))
                t = round(t + 0.1, 1)
            return ends
        greedy = run(lambda t, ends: True)
        self.assertEqual(greedy[:7], [0.0, 3.0, 9.0, 21.0, 45.0, 93.0, 153.0])           # 3, 6, 12, 24, 48 and then a minute
        self.assertEqual({round(b - a, 1) for a, b in zip(greedy[5:], greedy[6:])}, {60.0})
        self.assertLessEqual(len(greedy), 5 + 60)
        for rest in (10, 30, 39, 61, 120, 300, 599):                                     # three endings, a rest, and again
            def bursts(t, ends, rest=rest):
                return len(ends) % 3 != 0 or not ends or t - ends[-1] >= rest
            ends = run(bursts)
            late = [b - a for a, b in zip(ends[5:], ends[6:])]
            self.assertTrue(all(g >= 60.0 - 0.01 for g in late), (rest, ends[:12]))
            self.assertLessEqual(len(ends), 5 + 60, rest)
            for start in range(0, 3540, 60):                                             # once the pause has grown (five endings): never two in a minute
                if len(ends) > 5 and start >= ends[4]:
                    self.assertLessEqual(len([e for e in ends if start <= e < start + 60]), 1, (rest, start))
            self.assertLessEqual(len([e for e in ends if e < 120.0]), 6, (rest, ends[:8]))   # and the first two minutes hold six at the most
        # only ten minutes without any ending bring the short pause back
        calm = run(lambda t, ends: not ends or t - ends[-1] >= ndi.FEED_RESET_SECONDS + 1, seconds=2000.0)
        self.assertEqual(calm, [0.0, 601.0, 1202.0, 1803.0])
        pace = ndi.SoundPace()
        self.assertEqual([pace.may_end(t)[0] for t in (0.0, 601.0, 602.0, 604.0)], [True, True, False, True])

    def test_a_feed_whose_thread_cannot_be_started_gives_its_connection_back_and_close_waits_for_nobody(self):
        """A Low of the review: under the unit's bound on tasks a thread may not start. close() then joined a thread
        that never started (RuntimeError) and the connection was never given back."""
        f = ndi.SoundFeed(self.lib, self.voice, log=self.logged.append, clock=lambda: self.now[0])
        real = threading.Thread.start

        def no_thread(thread):
            if thread.name == "ndi-sound-feed":
                raise RuntimeError("can't start new thread")
            return real(thread)
        threading.Thread.start = no_thread
        try:
            with self.assertRaises(ndi.NdiError):
                f.start()
        finally:
            threading.Thread.start = real
        self.assertEqual(self.lib.closed_handles, [("recv", 1)])
        self.assertFalse(f.alive())
        f.close()                                                                        # and no error, twice
        f.close()
        self.assertEqual(self.lib.closed_handles, [("recv", 1)])

    def test_close_leaves_the_connection_open_under_a_thread_that_will_not_come_out(self):
        self.lib.hold = threading.Event()
        feed = self.feed(join_wait=0.3)
        self.assertTrue(self.lib.inside.wait(3))
        feed.close()
        self.assertEqual(self.lib.closed_handles, [])
        self.assertIn("left open, not freed under it", " ".join(self.logged))
        self.lib.hold.set()
        self.assertTrue(wait(lambda: self.lib.closed_handles == [("recv", 1)]))
        feed.close()
        self.assertEqual(self.lib.closed_handles, [("recv", 1)])                         # once

    def test_a_sound_connection_that_no_picture_uses_is_given_back_by_itself(self):
        feed = self.feed()
        r = self.play_with(feed)
        self.now[0] += ndi.FEED_IDLE_SECONDS + 5
        time.sleep(0.4)
        self.assertTrue(feed.alive())                                                    # in use: it stays, however long
        r.close()
        # From some turn of its thread on, nobody uses it; which turn is the thread's own business. So the clock is
        # moved in small steps: it must still be there after less than the limit in all, and gone some steps later.
        added = 0.0
        while added < ndi.FEED_IDLE_SECONDS - 1:
            self.now[0] += 1.0
            added += 1.0
            time.sleep(0.01)
        self.assertTrue(feed.alive())

        def later():
            self.now[0] += 1.0
            return not feed.alive() and ("recv", 1) in self.lib.closed_handles
        self.assertTrue(wait(later))
        self.assertIn("given back", " ".join(self.logged))


class SoundFromServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib, self.now, self.logged = FeedLib(), [1000.0], []
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=self.logged.append, first_frame=0.5,
                             problem=lambda path: None, clock=lambda: self.now[0])
        self.addCleanup(self.s.close)

    def conf(self, **kw):
        message = dict({"cmd": "configure", "on": True, "addresses": [], "sound": True, "sound_from": VID, "allowed": [PID, VID]}, **kw)
        rows = {VID: VOICE_ROW, PID: PICTURE_ROW}
        message.setdefault("sound_row", rows.get(message["sound_from"]) if isinstance(message["sound_from"], str) else None)
        return self.s.handle(message)

    def pump(self, make=lambda: sound(480)):
        """Sound from the sound sender, for as long as the test runs."""
        stop = threading.Event()

        def run():
            while not stop.wait(0.02):
                if self.lib.sounds.qsize() < 3:
                    self.lib.sounds.put(make())
        threading.Thread(target=run, daemon=True).start()
        self.addCleanup(stop.set)

    def status(self, later=0.0):
        self.now[0] += later
        return self.s.handle({"cmd": "status"})

    def test_the_sound_connection_stays_while_the_picture_changes_size_and_is_opened_again(self):
        st = self.conf()
        self.assertEqual((st["ok"], st["sound_from"], st["sound_source"]), (True, VID, {"id": VID, "state": "ready", "arriving": False}))
        self.assertEqual(self.lib.opens, [])                                             # nothing is connected to before a picture is asked for
        self.pump()
        self.lib.frames.put(frame(64, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"], reply["playing"]["audio"]["rate"]), (True, "matroska", 48000))
        self.assertEqual(self.lib.opens, [(VOICE, True), (PICTURE, False)])              # the sound first, so that it has the picture's time to connect
        r = self.s.receiver
        self.assertEqual(sorted(t.name for t in r._threads), ["ndi-capture", "ndi-writer"])
        self.assertTrue(wait(lambda: self.status()["sound_source"] == {"id": VID, "state": "playing", "arriving": True}))
        self.lib.frames.put(frame(128, 16))                                              # the next clip is another size
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.status()
        self.lib.frames.put(frame(128, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"], reply["playing"]["width"]), (True, "matroska", 128))
        self.assertEqual(self.lib.opens, [(VOICE, True), (PICTURE, False), (PICTURE, False)])        # ONE open of the sound source
        self.assertEqual(self.lib.closed_handles, [("recv", 2)])                         # and no close of it: the old picture's only
        self.assertEqual(self.lib.asked, {("recv", 1)})                                  # no picture connection was ever asked for sound
        self.assertTrue(self.s.handle({"cmd": "close"})["ok"])                           # nothing of NDI on the screen: both are given back
        self.assertEqual(sorted(self.lib.closed_handles), [("recv", 1), ("recv", 2), ("recv", 3)])
        self.assertEqual(self.status()["sound_source"]["state"], "ready")

    def test_the_page_is_told_when_the_sound_is_held_back_for_changing_again_and_again(self):
        self.conf()
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])
        self.s.feed.pace.quiet_until = self.now[0] + 10
        self.assertEqual(self.status()["sound_source"]["state"], "restless")
        self.assertIn("restless", ndi.SOUND_STATES)
        self.assertEqual(self.status(11)["sound_source"]["state"], "playing")

    def test_a_sound_source_that_is_away_gives_bare_frames_and_its_first_sound_ends_the_picture_as_changed(self):
        self.lib.raw.remove(VOICE)
        self.assertEqual(self.conf()["sound_source"], {"id": VID, "state": "away", "arriving": False})
        self.lib.frames.put(frame(64, 16))
        began = time.monotonic()
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.assertLess(time.monotonic() - began, ndi.AUDIO_WAIT)                       # and no wait for sound that cannot come
        r = self.s.receiver
        self.assertEqual((self.lib.opens, self.lib.asked), ([(PICTURE, False)], set()))
        self.assertEqual(sorted(t.name for t in r._threads), ["ndi-capture", "ndi-writer"])
        self.assertEqual(self.status(5)["sound_source"]["state"], "away")
        self.assertEqual(self.lib.opens, [(PICTURE, False)])
        self.lib.raw.append(VOICE)                                                       # the sender is started
        self.pump()
        self.assertEqual(self.status(2.1)["sound_source"]["state"], "playing")           # found at the next look, and connected to
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.assertEqual(r.message, "the sound source started to send sound")
        self.assertEqual(self.lib.opens, [(PICTURE, False), (VOICE, True)])
        self.status()                                                                    # the ended picture is let go of (its threads are out)
        self.lib.frames.put(frame(64, 16))                                               # the panel opens it again, now with the sound
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "matroska"))
        self.assertEqual(len(self.lib.sound_opens()), 1)
        self.assertEqual(self.lib.asked, {("recv", 2)})

    def test_it_is_looked_for_again_no_more_often_than_every_two_seconds(self):
        self.lib.raw.remove(VOICE)
        self.conf()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])
        looked = []
        want = self.s._want_feed
        self.s._want_feed = lambda: (looked.append(self.now[0]), want())[1]
        for _ in range(10):
            self.status(0.5)
        self.assertIn(len(looked), (2, 3), looked)

    def test_two_senders_under_the_sound_sources_id_are_not_played(self):
        self.lib.raw.append((VOICE[0], b"192.168.0.20:5999"))
        self.assertEqual(self.conf()["sound_source"], {"id": VID, "state": "twins", "arriving": False})
        self.pump()
        self.lib.frames.put(frame(64, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.status(5)
        self.assertEqual((self.lib.sound_opens(), self.lib.asked), ([], set()))          # the box does not pick one

    def test_twins_that_appear_while_the_sound_plays_end_its_connection_and_the_picture_goes_on(self):
        """The review of #132, M2: twins were looked at only when the feed was opened, so a second sender that
        started under the id later was played on while the page said "it plays neither"."""
        self.conf()
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.s.handle({"cmd": "open", "id": PID})["playing"]["container"], "matroska")
        r = self.s.receiver
        twin = (VOICE[0], b"192.168.0.20:5999")
        self.lib.raw.append(twin)
        self.assertEqual(self.status(1.1)["sound_source"], {"id": VID, "state": "twins", "arriving": False})
        self.assertEqual(self.lib.closed_handles, [("recv", 1)])                         # the sound's connection, and only it
        self.assertIsNone(self.s.feed)
        self.assertNotIn(r.state, ndi.ENDED)                                             # the picture goes on (silence is written)
        self.status(5)
        self.assertEqual(len(self.lib.sound_opens()), 1)                                 # and it is not opened while there are two
        # through a re-open of the picture (the next clip is another size): still neither
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(wait(lambda: r.state == "changed"))
        self.status()
        self.lib.frames.put(frame(128, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"], len(self.lib.sound_opens())), (True, "raw", 1))
        r2 = self.s.receiver
        self.lib.raw.remove(twin)                                                        # one sender is left: it is the sound source again
        self.assertEqual(self.status(2.1)["sound_source"]["state"], "playing")
        self.assertEqual(len(self.lib.sound_opens()), 2)
        self.assertTrue(wait(lambda: r2.state == "changed"))                             # and the picture is opened again with it
        self.assertEqual(r2.message, "the sound source started to send sound")

    def test_only_the_name_at_the_machine_the_owner_allowed_is_the_sound_source_whatever_answers_to_the_id(self):
        """The review of #132, M1: the sound road went by the 48-bit id alone, where a pad compares the live row's
        name and machine with the allowed row. A sender of another name behind the id is never opened, a
        connection to it is not kept, and the page does not show the owner's name as playing."""
        for bad in (None, {"id": VID}, dict(VOICE_ROW, name="EVIL (Audio)"), dict(PICTURE_ROW), dict(VOICE_ROW, **{"from": "10.9.9.9:1"}), "VLC (Audio)"):
            self.assertIs(self.conf(sound_row=bad)["ok"], False, bad)
        self.conf()
        self.assertEqual(self.s.sound_row, VOICE_ROW)
        real = self.s.sources
        other = [False]

        def sources():                     # the id answered by another name (a collision of the hash, made here by hand)
            return [dict(s, name="EVIL (Audio)") if (other[0] and s["id"] == VID) else s for s in real()]
        self.s.sources = sources
        other[0] = True
        self.assertEqual(self.status()["sound_source"], {"id": VID, "state": "away", "arriving": False})
        self.pump()
        self.lib.frames.put(frame(64, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.status(5)
        self.assertEqual((self.lib.sound_opens(), self.lib.asked), ([], set()))          # never opened
        other[0] = False
        self.assertEqual(self.status(2.1)["sound_source"]["state"], "playing")           # the real one is: opened
        self.assertEqual(len(self.lib.sound_opens()), 1)
        other[0] = True
        self.assertEqual(self.status(1.1)["sound_source"]["state"], "away")              # and not KEPT when another answers
        self.assertIn(("recv", 2), self.lib.closed_handles)
        self.assertIsNone(self.s.feed)
        # the panel's side: the owner's name is never shown as playing for another sender, whatever the helper says
        data = {"ndi": {"addresses": [], "sound": True, "sources": [PICTURE_ROW, VOICE_ROW], "sound_from": VID}}
        client = FakeClient(self.s)
        base = {"ok": True, "configured": True, "on": True, "addresses": [], "sound": True, "groups": [], "allowed": [PID, VID], "sound_from": VID,
                "sound_delay_ms": 0, "runtime": {"present": True, "loaded": True, "version": "x", "problem": ""}, "playing": None}
        i = ndi.Input(client, self.s.fifo, ndi.Wanted(lambda: True, lambda: data, log=lambda *_: None), log=lambda *_: None)
        elsewhere = {"id": ndi.source_id("VLC (Audio)", "192.168.0.99"), "name": "VLC (Audio)", "from": "192.168.0.99:5962"}
        for rows, want in (([VOICE_ROW], "playing"), ([], "away"), ([elsewhere], "away"), ([dict(VOICE_ROW, twins=True)], "twins"), ([VOICE_ROW, VOICE_ROW], "twins")):
            client.answer = dict(base, sources=rows, sound_source={"id": VID, "state": "playing", "arriving": True})
            got = i.status()["sound_source"]
            self.assertEqual((got["name"], got["state"], got["arriving"]), ("VLC (Audio)", want, want == "playing"), rows)
        client.answer = None
        i.sync()
        told = [m for m in client.sent if m["cmd"] == "configure"][-1]
        self.assertEqual((told["sound_from"], told["sound_row"]), (VID, VOICE_ROW))       # the helper is told the row the owner allowed

    def test_closing_and_opening_the_sounds_connection_does_not_wipe_the_pace_a_twin_cannot_be_used_to_blink_the_picture(self):
        """The re-check of #132: the back-off lived on the connection. A hostile sound sender announced a second
        sender of its name (the connection is closed: "plays neither"), withdrew it (a fresh connection, its count
        at nothing), then changed rate: six re-opens of the picture in 19 seconds, never "restless". The pace is
        the SOURCE's now, kept by the Service; and what is counted is every ending of a picture's stream by that
        source's sound, also sound that "starts" under a picture which was opened while the twin was there."""
        twin = (VOICE[0], b"192.168.0.20:5999")
        form = [sound]
        self.conf()
        self.pump(lambda: form[0](480) if form[0] is sound else sound(441, rate=44100))
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])
        ended, began = [], self.now[0]

        def reopen():
            """What the panel does when the helper says "changed": the picture is opened again."""
            r = self.s.receiver
            if r is not None and r.state == "changed":
                ended.append((round(self.now[0] - began, 1), r.message))
                self.status()
                self.lib.frames.put(frame(64, 16))
                self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])

        def step(seconds=0.5):
            self.now[0] += seconds
            self.status()
            time.sleep(0.12)
            reopen()
        states = set()
        for cycle in range(12):                                                          # 12 cycles of the attack, 5 s each: a minute
            self.lib.raw.append(twin)                                                    # a second sender of its name: the connection is closed
            for _ in range(3):
                step()
            self.lib.raw.remove(twin)                                                    # withdrawn: a fresh connection
            form[0] = None if form[0] is sound else sound                                # and another rate
            for _ in range(7):
                step()
                states.add((self.s.handle({"cmd": "status"})["sound_source"] or {}).get("state"))
        self.assertIsNotNone(self.s._pace[1])
        self.assertGreaterEqual(len(self.lib.sound_opens()), 8)                          # the connection really was opened again and again
        self.assertIn("restless", states)                                                # and the page was told why there is no sound
        # at the pace of the back-off: one ending that is free, then one after each pause of 3, 6, 12, 24 ... seconds
        self.assertLessEqual(len(ended), 5, ended)
        gaps = [b[0] - a[0] for a, b in zip(ended, ended[1:])]
        for n, gap in enumerate(gaps):
            self.assertGreaterEqual(gap, min(ndi.FEED_QUIET_SECONDS * 2 ** n, ndi.FEED_QUIET_MAX) - 0.01, (n, ended))
        self.assertNotIn(self.s.receiver.state, ("refused", "stopped"))                  # and the picture is there throughout

    def test_with_sound_off_no_sound_source_is_connected_to_and_the_pipe_carries_bare_frames(self):
        self.assertIsNone(self.conf(sound=False)["sound_source"])
        self.pump()
        self.lib.frames.put(frame(64, 16))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.assertNotIn("audio", reply["playing"])
        self.status(5)
        self.assertEqual((self.lib.opens, self.lib.asked), ([(PICTURE, False)], set()))

    def test_a_picture_that_is_the_sound_source_plays_its_own_sound_on_its_one_connection(self):
        self.conf(sound_from=PID)
        self.lib.frames.put(frame(64, 16))
        self.lib.own.put(sound(480))
        reply = self.s.handle({"cmd": "open", "id": PID})
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "matroska"))
        self.assertEqual(self.lib.opens, [(PICTURE, False)])
        self.assertIn("ndi-audio", [t.name for t in self.s.receiver._threads])
        self.assertEqual(self.status()["sound_source"]["state"], "playing")

    def test_what_the_panel_may_say_and_what_a_change_does_to_a_picture_that_plays(self):
        for bad in ({"sound_from": "FIRESPRAY (VLC)"}, {"sound_from": "192.168.0.20"}, {"sound_from": 7}, {"sound_from": None},
                    {"sound_from": "0123456789ab"},                                      # an id, and not one of the allowed
                    {"sound_delay_ms": 501}, {"sound_delay_ms": -251}, {"sound_delay_ms": True}, {"sound_delay_ms": 1.5}):
            self.assertIs(self.conf(**bad)["ok"], False, bad)
        self.assertEqual(self.s.sound_from, "")                                          # nothing of a refused message is kept
        st = self.conf(sound_delay_ms=120)
        self.assertEqual((st["sound_from"], st["sound_delay_ms"]), (VID, 120))           # the delay is said back and used by nothing here
        self.assertNotIn("sound_delay_ms", inspect.getsource(ndi.Receiver) + inspect.getsource(ndi.SoundFeed))
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": []})            # a message without the words: no sound source
        self.assertEqual((st["sound_from"], st["sound_delay_ms"], st["sound_source"]), ("", 0, None))
        self.conf()
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])
        r = self.s.receiver
        self.conf()                                                                      # said again: nothing happens
        self.conf(sound_delay_ms=40)                                                     # the delay is the player's: the picture plays on
        self.assertNotIn(r.state, ndi.ENDED)
        self.assertEqual(self.lib.closed_handles, [])
        self.conf(sound_from="")                                                         # back to each picture's own sound
        self.assertEqual((r.state, r.message), ("changed", "where the sound comes from was changed"))
        self.assertIn(("recv", 1), self.lib.closed_handles)                              # the sound connection is given back
        self.assertIsNone(self.s.feed)

    def test_a_sender_with_sound_and_no_picture_is_told_apart_from_one_that_sends_nothing(self):
        """The owner's sound sender, pressed as if it were a picture. Before: "sent no picture in 6 seconds", for
        this one as for a sender that is switched off. Sound is off here, as on a new box: the connection is
        asked for one block, whose figures are looked at and not a byte of it read."""
        self.conf(sound=False, sound_from="")
        block = sound(480)
        self.lib.own.put(block)
        reply = self.s.handle({"cmd": "open", "id": VID})
        self.assertEqual(reply, {"ok": False, "sound_only": True, "error": "VLC (Audio) sends sound and no picture; to hear it under NDI pictures, choose it under 'Where sound comes from' on the NDI page"})
        self.assertEqual(block.token, [])
        self.assertEqual(self.lib.freed_sound, 1)
        rows = {r["id"]: r for r in self.status(2)["sources"]}
        self.assertIs(rows[VID].get("sound_only"), True)
        self.assertNotIn("sound_only", rows[PID])
        reply = self.s.handle({"cmd": "open", "id": PID})                                # nothing at all: the old words
        self.assertEqual(reply, {"ok": False, "error": "SCAN (Converter) sent no picture in 0 seconds, and no sound either (a sender of sound alone "
                                                       "sends nothing while nothing plays into it)"})
        self.assertNotIn("sound_only", {r["id"]: r for r in self.status(2)["sources"]}[PID])
        for bad in (sound(480, rate=22050), sound(24000)):                              # sound the box would not play is not "sound"
            self.lib.own.put(bad)
            self.assertNotIn("sound_only", self.s.handle({"cmd": "open", "id": PID}))
            self.assertEqual(bad.token, [])
        # with each picture's own sound on, the sound's own thread has seen it: the same answer
        self.conf(sound=True, sound_from="")
        self.s.sound_only.clear()
        self.lib.own.put(sound(480))
        self.assertIs(self.s.handle({"cmd": "open", "id": VID}).get("sound_only"), True)
        # the label goes by itself: after ten minutes, and when the sender leaves the list
        self.assertEqual(ndi.SOUND_ONLY_SECONDS, 600)
        label = lambda later: {r["id"]: r.get("sound_only") for r in self.status(later)["sources"]}      # noqa: E731
        self.assertEqual(label(598)[VID], True)
        self.assertEqual(label(4)[VID], None)
        self.assertEqual(self.s.sound_only, {})
        self.lib.own.put(sound(480))
        self.assertIs(self.s.handle({"cmd": "open", "id": VID}).get("sound_only"), True)
        self.lib.raw.remove(VOICE)
        self.assertNotIn(VID, label(2))
        self.lib.raw.append(VOICE)
        self.assertEqual(label(2)[VID], None)                                            # back on the list: not labelled
        # and the label goes the moment the sender does send a picture
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": VID})["ok"])
        self.assertNotIn("sound_only", {r["id"]: r for r in self.status(2)["sources"]}[VID])
        # With one sound source for the box, the sound that the picture's receiver knows of is the SOUND SOURCE's.
        # A picture sender whose first picture is late must not be called "sound only" for it.
        self.conf()
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])                 # a picture under the feed: the feed has had sound now
        self.assertTrue(wait(lambda: self.s.feed is not None and self.s.feed.format is not None))
        reply = self.s.handle({"cmd": "open", "id": PID})                                # the next clip: no picture in time
        self.assertEqual((reply["ok"], reply.get("sound_only")), (False, None), reply)
        self.assertIn("sent no picture", reply["error"])
        rows = {r["id"]: r for r in self.status(2)["sources"]}
        self.assertNotIn("sound_only", rows[PID])
        # the panel passes the label on only as a plain true
        rows = ndi.Input._rows({"sources": [dict(VOICE_ROW, sound_only=True), dict(PICTURE_ROW, sound_only="yes")]})
        self.assertEqual(rows, [dict(VOICE_ROW, sound_only=True), PICTURE_ROW])

    def test_switching_the_input_off_gives_the_sound_connection_back(self):
        self.conf()
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": PID})["ok"])
        self.conf(on=False)
        self.assertEqual(sorted(self.lib.closed_handles), [("recv", 1), ("recv", 2)])
        self.assertIsNone(self.s.feed)


class SoundFromSettingsTest(unittest.TestCase):
    def test_the_stored_section_takes_the_two_keys_and_holds_the_id_to_the_allowed_list(self):
        good = {"addresses": [], "sound": True, "sources": [VOICE_ROW], "sound_from": VID, "sound_delay_ms": -40}
        self.assertEqual(ndi.validate_saved(good), good)
        self.assertEqual(ndi.validate_saved({"addresses": [], "sound_delay_ms": 0}), {"addresses": [], "sound_delay_ms": 0})
        self.assertEqual(ndi.validate_saved({"addresses": [], "sound_from": ""}), {"addresses": []})
        for bad in ({"sound_from": VID},                                                 # an id with no allowed source behind it
                    {"sound_from": PID, "sources": [VOICE_ROW]}, {"sound_from": "VLC (Audio)", "sources": [VOICE_ROW]},
                    {"sound_from": "192.168.0.20:5962", "sources": [VOICE_ROW]}, {"sound_from": 5}, {"sound_from": None},
                    {"sound_delay_ms": 501}, {"sound_delay_ms": -251}, {"sound_delay_ms": True}, {"sound_delay_ms": "10"}, {"sound_delay_ms": 0.5}):
            with self.assertRaises(ndi.NdiError, msg=bad):
                ndi.validate_saved(dict({"addresses": []}, **bad))

    def test_what_is_read_from_a_file_a_person_may_have_edited(self):
        self.assertEqual(ndi.saved_sound_from({"sources": [VOICE_ROW], "sound_from": VID}), VID)
        for section in (None, [], {}, {"sound_from": VID}, {"sources": [VOICE_ROW], "sound_from": PID}, {"sources": [VOICE_ROW], "sound_from": [VID]},
                        {"sources": "x", "sound_from": VID}):
            self.assertEqual(ndi.saved_sound_from(section), "", section)
        self.assertEqual([ndi.saved_sound_delay({"sound_delay_ms": v}) for v in (120, -250, 500, 501, -251, True, "10", 1.5, None)],
                         [120, -250, 500, 0, 0, 0, 0, 0, 0])
        self.assertEqual(ndi.saved_sound_delay(None), 0)
        data = {"ndi": {"addresses": [], "sources": [VOICE_ROW], "sound_from": VID, "sound_delay_ms": 80}}
        w = ndi.Wanted(lambda: True, lambda: data, log=lambda *_: None)
        w()
        self.assertEqual((w.sound_from, w.sound_delay_ms), (VID, 80))
        data["ndi"] = "broken"
        w()
        self.assertEqual((w.sound_from, w.sound_delay_ms), ("", 0))

    def test_the_release_before_loads_the_file_and_keeps_both_keys_through_a_save(self):
        """No new schema. The release before this one (named by its commit) reads the file, plays as it did, and
        its save keeps the two keys it does not know. One edge is pinned here because NDI.md says it: that
        release does not import a settings export that carries the keys. (The other, read from its code and not
        run: its own writers of the section drop them, so the sound source is chosen again after going back.)"""
        from pvj.settings import Settings
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", BEFORE_SOUND_FROM + "^{commit}"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % BEFORE_SOUND_FROM)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE_SOUND_FROM, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        now = Settings(os.path.join(old, "settings.json"))
        now.load()
        section = {"addresses": [], "sound": True, "sources": [VOICE_ROW], "sound_from": VID, "sound_delay_ms": 120}
        now.data["ndi"] = dict(section)
        now.save()
        script = r"""
import json, sys
from pvj.settings import Settings
from pvj import boxcare, ndi
assert not hasattr(ndi, "SoundFeed"), "this is the older release"
s = Settings(sys.argv[1])
s.load()
out = {"schema": s.data["schema"], "sound": ndi.saved_sound(s.data["ndi"]), "sources": ndi.saved_sources(s.data["ndi"])}
try:
    boxcare.check_ndi(s.data["ndi"], None)
    out["imports"] = True
except ndi.NdiError:
    out["imports"] = False
s.data["mix"] = {"transition": "dip", "duration": 2.0}
s.save()
print(json.dumps(out))
"""
        import json
        import sys
        run = subprocess.run([sys.executable, "-c", script, now.path], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual((out["schema"], out["sound"], out["sources"]), (now.data["schema"], True, [VOICE_ROW]))
        self.assertIs(out["imports"], False)
        with open(now.path) as f:
            self.assertEqual(json.load(f)["ndi"], section)


class SoundFromPanelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FeedLib()
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.3, problem=lambda path: None)
        self.addCleanup(self.s.close)
        self.data = {"ndi": {"addresses": [], "sound": True, "sources": [PICTURE_ROW, VOICE_ROW], "sound_from": VID, "sound_delay_ms": 120}}
        self.client = FakeClient(self.s)
        self.i = ndi.Input(self.client, self.s.fifo, ndi.Wanted(lambda: True, lambda: self.data, log=lambda *_: None), log=lambda *_: None)

    def told(self):
        return [(m.get("sound"), m.get("sound_from"), m.get("sound_delay_ms")) for m in self.client.sent if m["cmd"] == "configure"]

    def test_the_helper_is_told_the_sound_source_and_told_again_when_it_changes(self):
        st = self.i.status()
        self.assertEqual(self.told(), [(True, VID, 120)])
        self.assertEqual((self.s.sound_from, self.s.sound_delay_ms), (VID, 120))
        self.assertEqual((st["sound"], st["sound_from"], st["sound_delay_ms"], st["sound_delay_range"]), (True, VID, 120, [-250, 500]))
        self.assertEqual(st["sound_source"], dict(VOICE_ROW, state="ready", arriving=False))
        self.i.status()
        self.assertEqual(len(self.told()), 1)                                            # not again while nothing changed
        self.data["ndi"]["sound_delay_ms"] = 60
        self.i.status()
        self.data["ndi"].pop("sound_from")
        st = self.i.status()
        self.assertEqual(self.told()[1:], [(True, VID, 60), (True, "", 60)])
        self.assertEqual((st["sound_from"], st["sound_source"], self.s.sound_from), ("", None, ""))
        self.data["ndi"].update(sound=False, sound_from=VID)                             # the choice is kept while sound is off
        st = self.i.status()
        self.assertEqual((st["sound"], st["sound_source"]["state"]), (False, "off"))
        self.assertIsNone(self.s.handle({"cmd": "status"})["sound_source"])

    def test_a_helper_from_before_is_not_told_again_and_again_and_a_lying_one_is_not_believed(self):
        base = {"ok": True, "configured": True, "on": True, "addresses": [], "sound": True, "groups": [], "allowed": [PID, VID],
                "runtime": {"present": True, "loaded": True, "version": "x", "problem": ""}, "sources": [VOICE_ROW], "playing": None}
        self.client.answer = dict(base)                                                  # it says nothing of a sound source
        st = self.i.status()
        self.assertEqual(self.told(), [])
        self.assertEqual(st["sound_source"], dict(VOICE_ROW, state="away", arriving=False))
        for lie in ({"id": PID, "state": "playing", "arriving": True}, {"id": VID, "state": "<b>loud</b>", "arriving": True},
                    {"id": VID, "state": "playing", "arriving": "yes", "name": "EVIL", "from": "8.8.8.8:1"}, "playing", [VID]):
            self.client.answer = dict(base, sound_from=VID, sound_delay_ms=120, sound_source=lie)
            st = self.i.status()["sound_source"]
            self.assertEqual((st["id"], st["name"], st["from"], st["arriving"]), (VID, "VLC (Audio)", "192.168.0.20:5962", False), lie)
            self.assertIn(st["state"], ("away", "playing"), lie)
        self.assertEqual(self.told(), [])


class ApiSoundFromTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.lib = FeedLib()
        self.ndidir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ndidir, True)
        self.service = ndi.Service(self.ndidir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.5, problem=lambda path: None)
        self.addCleanup(self.service.close)
        self.client = FakeClient(self.service)
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(self.client, self.service.fifo, ndi.Wanted(lambda: reg.enabled("inputs-ndi"), lambda: st.data, log=lambda *_: None),
                                 log=lambda *_: None)
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": True}, token=self.full)[0], 200)

    def post(self, body, token=None):
        st, out, _ = self.call("POST", "/api/ndi", body, token=token or self.full)
        return st, out

    def test_the_owner_chooses_a_listed_source_and_it_is_allowed_saved_and_sent_to_the_helper(self):
        st, body = self.post({"action": "sound_from", "id": VID})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.settings.data["ndi"], {"addresses": [], "sound": True, "sources": [VOICE_ROW], "sound_from": VID})
        self.assertEqual((body["sound"], body["sound_from"], body["sound_source"]), (True, VID, dict(VOICE_ROW, state="ready", arriving=False)))
        self.assertEqual((self.service.sound, self.service.sound_from), (True, VID))
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)         # again: not allowed twice
        self.assertEqual(self.settings.data["ndi"]["sources"], [VOICE_ROW])
        # "no sound" keeps the choice; the old switch back on plays that source again
        self.assertEqual(self.post({"action": "sound", "on": False})[1]["sound_source"]["state"], "off")
        self.assertEqual(self.settings.data["ndi"], {"addresses": [], "sound": False, "sources": [VOICE_ROW], "sound_from": VID})
        self.assertEqual(self.post({"action": "sound", "on": True})[1]["sound_from"], VID)
        # each picture's own sound: the id goes, sound stays on, the source stays allowed
        st, body = self.post({"action": "sound_from", "id": ""})
        self.assertEqual((st, body["sound"], body["sound_from"], body["sound_source"]), (200, True, "", None))
        self.assertEqual(self.settings.data["ndi"], {"addresses": [], "sound": True, "sources": [VOICE_ROW]})
        self.assertEqual(self.service.sound_from, "")

    def test_only_an_id_on_the_list_now_and_only_the_owner(self):
        for bad in ({"action": "sound_from"}, {"action": "sound_from", "id": "VLC (Audio)"}, {"action": "sound_from", "id": "192.168.0.20:5962"},
                    {"action": "sound_from", "id": 5}, {"action": "sound_from", "id": VID.upper()}, {"action": "sound_from", "name": "VLC (Audio)"}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        st, body = self.post({"action": "sound_from", "id": "0123456789ab"})
        self.assertEqual(st, 404)
        self.assertIn("not on the network now", body["error"])
        self.lib.raw.remove(VOICE)                                                       # allowed before, away now: still not taken
        self.settings.data["ndi"] = {"addresses": [], "sources": [VOICE_ROW]}
        self.service._sources_at = None
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 404)
        self.lib.raw.extend([VOICE, (VOICE[0], b"192.168.0.20:5999")])                  # two senders under the id
        self.service._sources_at = None
        st, body = self.post({"action": "sound_from", "id": VID})
        self.assertEqual(st, 409)
        self.assertIn("cannot tell them apart", body["error"])
        self.assertNotIn("sound_from", self.settings.data["ndi"])
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        for body in ({"action": "sound_from", "id": PID}, {"action": "sound_from", "id": ""}, {"action": "sound_delay", "ms": 10}):
            self.assertEqual(self.post(body, token=live)[0], 403, body)
        self.assertEqual(self.api.routes()[("POST", "/api/ndi")][0], "full")

    def test_the_list_of_allowed_sources_is_bounded_for_the_sound_source_too(self):
        rows = []
        for n in range(ndi.MAX_ALLOWED):
            name = "CAM %d" % n
            rows.append({"id": ndi.source_id(name, "192.168.0.30"), "name": name, "from": "192.168.0.30:%d" % (6000 + n)})
        self.settings.data["ndi"] = {"addresses": [], "sources": rows}
        st, body = self.post({"action": "sound_from", "id": VID})
        self.assertEqual(st, 400)
        self.assertIn("at most %d" % ndi.MAX_ALLOWED, body["error"])

    def test_the_delay_is_a_whole_number_within_its_bounds(self):
        for bad in (501, -251, True, 1.5, "10", None):
            self.assertEqual(self.post({"action": "sound_delay", "ms": bad})[0], 400, bad)
        self.assertEqual(self.post({"action": "sound_delay"})[0], 400)
        st, body = self.post({"action": "sound_delay", "ms": -250})
        self.assertEqual((st, body["sound_delay_ms"], self.settings.data["ndi"]), (200, -250, {"addresses": [], "sound_delay_ms": -250}))
        self.assertEqual(self.post({"action": "sound_delay", "ms": 500})[1]["sound_delay_ms"], 500)
        self.assertEqual(self.post({"action": "sound_delay", "ms": 0})[1]["sound_delay_ms"], 0)
        self.assertEqual(self.settings.data["ndi"], {"addresses": []})                   # nothing is kept for "no delay"

    def test_every_other_writer_of_the_section_keeps_both_keys(self):
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)
        self.assertEqual(self.post({"action": "sound_delay", "ms": 120})[0], 200)
        keys = lambda: (self.settings.data["ndi"].get("sound_from"), self.settings.data["ndi"].get("sound_delay_ms"))      # noqa: E731
        for body in ({"action": "add_address", "address": "192.168.1.20"}, {"action": "add_group", "group": "Leyline"},
                     {"action": "add_discovery", "server": "192.168.1.5"}, {"action": "own_discovery", "on": True},
                     {"action": "sound", "on": False}, {"action": "sound", "on": True}, {"action": "remove_address", "address": "192.168.1.20"},
                     {"action": "remove_group", "group": "Leyline"}, {"action": "remove_discovery", "server": "192.168.1.5"}):
            self.assertEqual(self.post(body)[0], 200, body)
            self.assertEqual(keys(), (VID, 120), body)
        st, _, _ = self.call("POST", "/api/pads", {"bank": 0, "index": 0, "ndi": PID}, token=self.full)       # a pad allows another source
        self.assertEqual(st, 200)
        self.assertEqual([r["id"] for r in self.settings.data["ndi"]["sources"]], [VID, PID])
        self.assertEqual(keys(), (VID, 120))
        self.assertEqual(self.post({"action": "forget", "id": PID})[0], 200)             # and forgetting that other one
        self.assertEqual(keys(), (VID, 120))
        self.assertEqual(self.api._ndi_write(), None)
        self.assertEqual(keys(), (VID, 120))
        # a hand-edited file whose id has no allowed source behind it: the id is not written again
        self.settings.data["ndi"]["sound_from"] = PID
        self.api._ndi_write()
        self.assertNotIn("sound_from", self.settings.data["ndi"])

    def test_the_sound_source_cannot_be_forgotten_while_it_is_the_sound_source(self):
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)
        st, body = self.post({"action": "forget", "id": VID})
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "'VLC (Audio)' is where this box's NDI sound comes from; choose another sound source first, then remove it")
        self.assertEqual(self.settings.data["ndi"]["sources"], [VOICE_ROW])
        self.assertEqual(self.post({"action": "sound", "on": False})[0], 200)            # also while sound is off: the choice is kept
        self.assertEqual(self.post({"action": "forget", "id": VID})[0], 409)
        self.assertEqual(self.post({"action": "sound_from", "id": ""})[0], 200)
        self.assertEqual(self.post({"action": "forget", "id": VID})[0], 200)
        self.assertNotIn("sources", self.settings.data["ndi"])

    def pump(self):
        stop = threading.Event()

        def run():
            while not stop.wait(0.02):
                if self.lib.sounds.qsize() < 3:
                    self.lib.sounds.put(sound(480))
        threading.Thread(target=run, daemon=True).start()
        self.addCleanup(stop.set)

    def test_a_picture_is_played_with_the_sound_sources_sound_and_as_bare_frames_when_sound_is_off(self):
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)
        self.pump()
        self.lib.frames.put(frame(64, 16))
        st, body, _ = self.call("POST", "/api/play", {"ndi": PID}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, True), body)
        self.assertIn(("play_pipe", self.service.fifo, 64, 16, 29.97, "matroska"), self.player.calls)
        self.assertEqual(self.lib.opens, [(VOICE, True), (PICTURE, False)])
        self.assertEqual(self.lib.asked, {("recv", 1)})
        # sound off: bare frames, whatever the helper says and whatever sound source is kept
        self.assertEqual(self.post({"action": "sound", "on": False})[0], 200)
        self.assertEqual(self.settings.data["ndi"]["sound_from"], VID)
        self.lib.frames.put(frame(64, 16))
        st, body, _ = self.call("POST", "/api/play", {"ndi": PID}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, False), body)
        self.assertEqual([c for c in self.player.calls if c[0] == "play_pipe"][-1][5], "uyvy422")
        self.assertEqual(len(self.lib.sound_opens()), 1)                                 # none was opened for it
        lying = dict(self.service.handle({"cmd": "status"}), playing=None)
        answer = {"ok": True, "playing": {"id": PID, "name": "SCAN (Converter)", "state": "ready", "message": "", "width": 64, "height": 16, "fps": 30.0,
                                          "counts": {}, "container": "matroska",
                                          "audio": {"rate": 48000, "channels": 2, "played": 2, "arriving": True, "level_db": -20.0,
                                                    "silent": False, "problem": "", "counts": {}}}}
        self.client.request = lambda message, timeout=None: answer if message["cmd"] == "open" else lying
        st, body, _ = self.call("POST", "/api/play", {"ndi": PID}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, False), body)
        self.assertEqual([c for c in self.player.calls if c[0] == "play_pipe"][-1][5], "uyvy422")

    def test_a_press_on_the_sound_source_or_on_a_sound_only_sender_is_refused_before_anything_is_touched(self):
        """Read from the code by the test builder (2026-10-12): the press let the player go of the pipe and had the
        helper close the picture and the sound's connection, and only then found that there was no picture."""
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)
        self.pump()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": PID}, token=self.full)[0], 200)
        released = []
        self.player.release_pipe = lambda path: released.append(path) or True
        before = (list(self.lib.opens), list(self.lib.closed_handles), len(self.player.calls), self.service.receiver, self.service.feed, dict(self.api.ndi.current))
        asked = len([m for m in self.client.sent if m["cmd"] in ("open", "close")])
        st, body, _ = self.call("POST", "/api/play", {"ndi": VID}, token=self.full)
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "VLC (Audio) is where this box's NDI sound comes from, so it is not shown as a picture (to show it, choose another sound source first); "
                                        "what plays was not touched")
        after = (list(self.lib.opens), list(self.lib.closed_handles), len(self.player.calls), self.service.receiver, self.service.feed, dict(self.api.ndi.current))
        self.assertEqual(after, before)                                                  # the picture, its sound and the player are as they were
        self.assertEqual((released, len([m for m in self.client.sent if m["cmd"] in ("open", "close")])), ([], asked))
        self.assertNotIn(self.service.receiver.state, ndi.ENDED)
        self.assertTrue(self.service.feed.alive())

    def test_the_sound_only_label_is_advice_and_never_locks_a_sender_out(self):
        """The review of #132, H1. A picture sender that was slow once (no picture in time, and sound) was labelled,
        the panel then refused every press on it before asking the helper, the page had no Play for it, and only a
        press that reached the helper could have cleared the label: locked out until the helper started again.
        Through the panel's road, as a person presses: refused in words while it sends no picture, labelled, asked
        AGAIN at the next press, played when its picture is there, and the label gone."""
        self.assertEqual(self.post({"action": "sound", "on": True})[0], 200)             # each picture's own sound

        def label():
            return {r["id"]: r.get("sound_only") for r in self.call("GET", "/api/ndi", token=self.full)[1]["sources"]}
        opens = lambda: len([m for m in self.client.sent if m["cmd"] == "open"])          # noqa: E731
        self.lib.own.put(sound(480))
        st, body, _ = self.call("POST", "/api/play", {"ndi": VID}, token=self.full)
        self.assertEqual((st, body["error"]), (409, "VLC (Audio) sends sound and no picture; to hear it under NDI pictures, choose it under "
                                                    "'Where sound comes from' on the NDI page"))
        self.service._sources_at = None
        self.assertEqual(label(), {PID: None, VID: True})
        asked = opens()
        self.lib.own.put(sound(480))
        st, body, _ = self.call("POST", "/api/play", {"ndi": VID}, token=self.full)      # pressed again: the helper is asked again
        self.assertEqual((st, opens()), (409, asked + 1))
        self.assertIn("sends sound and no picture", body["error"])
        self.lib.frames.put(frame(64, 16))                                               # it was a picture sender, and slow
        self.lib.own.put(sound(480))
        st, body, _ = self.call("POST", "/api/play", {"ndi": VID}, token=self.full)
        self.assertEqual((st, body.get("sound")), (200, True), body)
        self.service._sources_at = None
        self.assertEqual(label(), {PID: None, VID: None})                                # the label went with its first picture
        # a pad that holds a labelled source plays it too
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/pads", {"bank": 0, "index": 0, "ndi": VID}, token=self.full)[0], 200)
        self.service.sound_only[VID] = self.service._clock()
        self.lib.frames.put(frame(64, 16))
        st, body, _ = self.call("POST", "/api/play", {"pad": [0, 0]}, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertNotIn(VID, self.service.sound_only)

    def test_the_delay_goes_to_the_player_with_the_stream_with_sound_and_never_with_bare_frames(self):
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)
        self.pump()
        loads = lambda: [c for c in self.player.calls if c[0] == "play_pipe"]             # noqa: E731
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": PID}, token=self.full)[0], 200)
        self.assertEqual(loads()[-1], ("play_pipe", self.service.fifo, 64, 16, 29.97, "matroska"))     # no delay set: as it always was
        moved = []
        self.player.pipe_audio_delay = lambda path, seconds: moved.append((path, seconds)) or True
        self.assertEqual(self.post({"action": "sound_delay", "ms": 120})[0], 200)
        self.assertEqual(moved, [(self.service.fifo, 0.12)])                             # heard at once under the source that plays
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": PID}, token=self.full)[0], 200)
        self.assertEqual(loads()[-1], ("play_pipe", self.service.fifo, 64, 16, 29.97, "matroska", 0.12))
        # The delay belongs to the two-sender mode alone (the review of #132, M3): a picture that plays its OWN
        # sender's sound has one clock for both and is never moved, at the load or under the slider; the number is kept.
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.assertEqual(self.post({"action": "sound_from", "id": ""})[0], 200)
        self.assertEqual(self.settings.data["ndi"]["sound_delay_ms"], 120)
        self.lib.frames.put(frame(64, 16))
        self.lib.own.put(sound(480))
        st, body, _ = self.call("POST", "/api/play", {"ndi": PID}, token=self.full)
        self.assertEqual((st, body["sound"]), (200, True), body)
        self.assertEqual(loads()[-1], ("play_pipe", self.service.fifo, 64, 16, 29.97, "matroska"))
        del moved[:]
        self.assertEqual(self.post({"action": "sound_delay", "ms": 200})[0], 200)
        self.assertEqual((moved, self.settings.data["ndi"]["sound_delay_ms"]), ([], 200))
        self.assertEqual(self.post({"action": "sound_delay", "ms": 120})[0], 200)
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.assertEqual(self.post({"action": "sound_from", "id": VID})[0], 200)         # back to one sound source: the number is there again
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": PID}, token=self.full)[0], 200)
        self.assertEqual(loads()[-1], ("play_pipe", self.service.fifo, 64, 16, 29.97, "matroska", 0.12))
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.assertEqual(self.post({"action": "sound", "on": False})[0], 200)            # sound off: bare frames, and no delay goes with them
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": PID}, token=self.full)[0], 200)
        self.assertEqual(loads()[-1], ("play_pipe", self.service.fifo, 64, 16, 29.97, "uyvy422"))
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        del moved[:]
        self.assertEqual(self.post({"action": "sound_delay", "ms": 60})[0], 200)         # nothing of NDI plays: the player is not touched
        self.assertEqual(moved, [])

    def test_a_settings_file_carries_both_keys_and_one_whose_sound_source_is_not_allowed_is_refused(self):
        from pvj import boxcare
        good = {"addresses": [], "sound": True, "sources": [VOICE_ROW], "sound_from": VID, "sound_delay_ms": 120}
        self.assertEqual(boxcare.check_ndi(good, None), good)
        for bad in (dict(good, sound_from=PID), dict(good, sources=[]), dict(good, sound_delay_ms=9000), dict(good, sound_from="VLC (Audio)")):
            with self.assertRaises(ndi.NdiError, msg=bad):
                boxcare.check_ndi(bad, None)



def png_pixel(path, x, y):
    """(r, g, b) of one pixel of an 8-bit RGB or RGBA PNG that is not interlaced, with nothing but zlib."""
    with open(path, "rb") as f:
        raw = f.read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, head = 8, b"", None
    while pos < len(raw):
        size, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + size]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + size
    width, height, depth, colour, _c, _f, interlace = head
    assert depth == 8 and colour in (2, 6) and interlace == 0, head
    bpp = 3 if colour == 2 else 4
    data, stride = zlib.decompress(idat), width * bpp
    prev, row = bytearray(stride), None
    for line in range(y + 1):
        start = line * (stride + 1)
        kind, row = data[start], bytearray(data[start + 1:start + 1 + stride])
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up, corner = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                row[i] = (row[i] + left) & 255
            elif kind == 2:
                row[i] = (row[i] + up) & 255
            elif kind == 3:
                row[i] = (row[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                row[i] = (row[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        prev = row
    return tuple(row[x * bpp:x * bpp + 3])


def wav_samples(path):
    """(rate, channels, [floats of the first channel]) of a WAV that mpv wrote, whatever sample form it chose."""
    with open(path, "rb") as f:
        raw = f.read()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE", raw[:12]
    pos, fmt, data = 12, None, b""
    while pos + 8 <= len(raw):
        kind, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size] if kind != b"data" else raw[pos + 8:]       # mpv cannot go back to fill in the length of a pipe-fed file
        if kind == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
            if fmt[0] == 0xFFFE:
                fmt = (struct.unpack("<H", body[24:26])[0],) + fmt[1:]
        elif kind == b"data":
            data = body
            break
        pos += 8 + size + (size & 1)
    tag, channels, rate, _b, _a, bits = fmt
    step = bits // 8 * channels
    data = data[:len(data) - len(data) % step]
    if tag == 3 and bits == 32:
        values = array.array("f")
        values.frombytes(data)
        values = list(values)
    elif tag == 1 and bits in (16, 32):
        values = array.array("h" if bits == 16 else "i")
        values.frombytes(data)
        values = [v / float(2 ** (bits - 1)) for v in values]
    else:
        raise AssertionError("a WAV of a kind this test does not read: tag %d, %d bits" % (tag, bits))
    return rate, channels, values[0::channels]


MPV = shutil.which("mpv")
RED = (81, 90, 240)                                # Y, U, V of pure red in the range video uses


def red_frame(w, h):
    return frame(w, h, data=bytes((RED[1], RED[0], RED[2], RED[0])) * (w * h // 2))      # U Y V Y: the order "UYVY" means


def mkv_only():
    return ["--%s=%s" % kv for kv in Player.MKV_ONLY.items()]


# A 64 x 16 JPEG, all red (made with ffmpeg; 230 bytes).
RED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMQD/2wBDAAgQEBMQExYWFhYWFhoYGhsbGxoaGhobGxsdHR0iIiIdHR0bGx0dICAiIiUmJSMjIiMmJigoKDAwLi44ODpF"
    "RVP/xABNAAEBAAAAAAAAAAAAAAAAAAAABgEBAQEAAAAAAAAAAAAAAAAAAAYHEAEAAAAAAAAAAAAAAAAAAAAAEQEAAAAAAAAAAAAAAAAAAAAA/8AAEQgAEABAAwEiAAIRAAMRAP/a"
    "AAwDAQACEQMRAD8AiwEm38AAAAAB/9k=")


def other_codecs_stream(frames=20):
    """What a helper that was taken over could write in place of its own stream: Matroska of the same shape whose
    picture track says Motion JPEG (and carries real JPEGs) and whose sound track says 32-bit integer PCM. Both are
    things mpv decodes gladly when nothing stops it."""
    e, u = ndi._ebml, ndi._ebml_uint
    head = e("1A45DFA3", u("4286", 1) + u("42F7", 1) + u("42F2", 4) + u("42F3", 8) + e("4282", b"matroska") + u("4287", 2) + u("4285", 2))
    info = e("1549A966", u("2AD7B1", ndi.MKV_TIME_UNIT) + e("4D80", b"x") + e("5741", b"x"))
    video = e("AE", u("D7", 1) + u("73C5", 1) + u("83", 1) + u("9C", 0) + e("86", b"V_MJPEG") + u("23E383", int(1e9 / 30))
              + e("E0", u("B0", 64) + u("BA", 16)))
    audio = e("AE", u("D7", 2) + u("73C5", 2) + u("83", 2) + u("9C", 0) + e("86", b"A_PCM/INT/LIT")
              + e("E1", e("B5", struct.pack(">d", 48000.0)) + u("9F", 2) + u("6264", 32)))
    out = head + bytes.fromhex("18538067" + "01FFFFFFFFFFFFFF") + info + e("1654AE6B", video + audio)
    pcm = struct.pack("<2i", 2 ** 28, -2 ** 28) * 1600                                   # a thirtieth of a second, loud enough to find
    for k in range(frames):
        out += ndi.mkv_block_head(1, k / 30.0, len(RED_JPEG)) + RED_JPEG
        out += ndi.mkv_block_head(2, k / 30.0, len(pcm)) + pcm
    return out


@unittest.skipUnless(MPV, "mpv is not installed here (it is in CI); what mpv makes of the stream is not shown by this run")
class RealMpvTest(Base):
    """The stream the helper writes, through a real pipe into the real mpv, with the options the player gives it.
    Shown where this runs: mpv takes the stream from a pipe (which cannot seek), finds a picture of the right size
    whose colour is the one that was sent (so the byte order is right), and sound of the right rate, channels,
    length and level. NOT shown: anything a person would hear, the delay, or whether picture and sound are in step
    on a screen with loudspeakers; mpv is run here without a display or a sound device and as fast as it can go."""

    def test_mpv_reads_the_stream_from_a_pipe_and_finds_the_picture_and_the_tone(self):
        w, h, n, seconds = 64, 16, 4800, 2.0
        tone = [math.sin(2 * math.pi * 1000 * i / 48000.0) for i in range(n)]            # 1 kHz at NDI's reference level, 100 ms a block
        out = os.path.join(self.dir, "out")
        os.makedirs(out)
        wav, log = os.path.join(self.dir, "sound.wav"), os.path.join(self.dir, "mpv.log")
        r = self.receiver(audio_wait=1.0)
        r._clock = time.monotonic                                                        # the real clock: this is a run in real time
        r.start()
        self.lib.frames.put(red_frame(w, h))
        self.lib.sounds.put(sound(n, planes=[tone, tone]))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "matroska")
        # the same the player gives: the four for the pipe, and the narrowing to the two decoders the stream may need
        # (MKV_ONLY). mpv refuses an option it does not know or cannot read, so this run also shows it takes them.
        opts = ["--%s=%s" % kv for kv in (("demuxer", "mkv"), ("cache", "no"), ("cache-pause", "no"), ("initial-audio-sync", "no"), ("demuxer-readahead-secs", "0"),
                                           ("demuxer-max-bytes", Player.pipe_queue(w, h, 30)))] + mkv_only()
        cmd = [MPV, "--no-config", "--no-terminal", "--idle=no", "--framedrop=no", "--untimed", "--log-file=" + log, "--msg-level=all=v",
               "--vo=image", "--vo-image-format=png", "--vo-image-outdir=" + out, "--ao=pcm", "--ao-pcm-file=" + wav] + opts + [self.fifo]
        mpv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: mpv.poll() is None and mpv.kill())
        try:
            self.assertTrue(wait(lambda: r.status()["state"] == "playing", 15), "mpv did not open the pipe")
            end = time.monotonic() + seconds
            sent_frames, sent_blocks, next_sound = 1, 1, time.monotonic() + 0.1
            while time.monotonic() < end:
                time.sleep(1 / 30.0)
                self.lib.frames.put(red_frame(w, h))
                sent_frames += 1
                if time.monotonic() >= next_sound:
                    next_sound += 0.1
                    self.lib.sounds.put(sound(n, planes=[tone, tone]))
                    sent_blocks += 1
            self.assertTrue(wait(lambda: self.lib.frames.qsize() == 0 and self.lib.sounds.qsize() == 0 and r._pending is None and not r._queue, 10))
            time.sleep(0.2)
            st = r.status()
            r.close()                                                                    # the pipe ends: mpv plays out and leaves
            try:
                code = mpv.wait(30)
            except subprocess.TimeoutExpired:
                self.fail("mpv did not end when the pipe did")
            said = open(log, errors="replace").read() if os.path.exists(log) else ""
            tail = "\n".join(said.splitlines()[-60:])
            self.assertEqual(code, 0, tail)
            self.assertEqual(st["state"], "playing", st)
            # the picture: mpv's own reader, uncompressed UYVY of the size that was said
            self.assertIn("mkv", said.lower())
            self.assertRegex(said, r"(?i)rawvideo", tail)
            self.assertRegex(said, r"(?i)uyvy422", tail)
            self.assertRegex(said, r"%dx%d" % (w, h), tail)
            self.assertNotRegex(said, r"(?i)cannot seek|seek failed|unsupported codec|could not open codec", tail)
            pictures = sorted(os.listdir(out))
            written = st["counts"]["shown"]
            self.assertGreaterEqual(len(pictures), written - 5, tail)                    # a frame or two may be left out where the stream starts or ends
            self.assertLessEqual(len(pictures), written + 2, tail)                       # and the first picture is in the stream twice, and a picture that stood once more
            self.assertGreater(len(pictures), 20, tail)
            for name in (pictures[0], pictures[len(pictures) // 2], pictures[-1]):
                red, green, blue = png_pixel(os.path.join(out, name), w // 2, h // 2)
                self.assertGreater(red, 200, (name, red, green, blue))                   # red: with the bytes in another order it is not
                self.assertLess(green, 60, (name, red, green, blue))
                self.assertLess(blue, 60, (name, red, green, blue))
            # the sound: float PCM at the rate and channels that were said, as long as what was written, the tone at -20 dBFS
            self.assertRegex(said, r"(?i)pcm_f32le", tail)
            rate, channels, left = wav_samples(wav)
            self.assertEqual((rate, channels), (48000, 2), tail)
            counts = st["audio"]["counts"]
            self.assertEqual(counts["received"], sent_blocks - 1, counts)                # all but the one from before the pipe was taken
            wrote = counts["written"] * n
            self.assertGreater(wrote, 0.6 * seconds * 48000, counts)
            self.assertGreaterEqual(len(left), wrote - 24000, (len(left), counts))       # half a second of slack where the stream starts and ends
            self.assertLessEqual(len(left), wrote + counts["filled"] * 48000 + 24000, (len(left), counts))
            loud = [abs(x) for x in left if abs(x) > 0.001]
            self.assertGreater(len(loud), 0.5 * len(left))
            self.assertAlmostEqual(20 * math.log10(max(loud)), -20.0, delta=0.3)         # NDI's reference level, 20 dB under full scale
            crossings = sum(1 for a, b in zip(left, left[1:]) if (a < 0) != (b < 0) and (abs(a) > 0.001 or abs(b) > 0.001))
            self.assertAlmostEqual(crossings / 2.0 / (len(loud) / 48000.0), 1000.0, delta=90.0)       # and still the 1 kHz that went in
        finally:
            if mpv.poll() is None:
                mpv.kill()

    def run_mpv_on_file(self, path, name, extra):
        out = os.path.join(self.dir, name)
        os.makedirs(out)
        wav, log = os.path.join(self.dir, name + ".wav"), os.path.join(self.dir, name + ".log")
        cmd = [MPV, "--no-config", "--no-terminal", "--idle=no", "--framedrop=no", "--untimed", "--log-file=" + log, "--msg-level=all=v",
               "--vo=image", "--vo-image-format=png", "--vo-image-outdir=" + out, "--ao=pcm", "--ao-pcm-file=" + wav,
               "--demuxer=mkv", "--cache=no"] + extra + [path]
        mpv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: mpv.poll() is None and mpv.kill())
        try:
            code = mpv.wait(60)
        except subprocess.TimeoutExpired:
            mpv.kill()
            self.fail("mpv did not end")
        said = open(log, errors="replace").read() if os.path.exists(log) else ""
        sound_bytes = os.path.getsize(wav) if os.path.exists(wav) else 0
        return code, sorted(os.listdir(out)), sound_bytes, said, out

    def test_a_stream_that_names_other_codecs_is_not_decoded_once_the_player_is_narrowed(self):
        """H1. The same file twice. Without the narrowing mpv decodes both tracks (so the file is one mpv can play,
        and the second half of this test means something); with the options the player sets (MKV_ONLY) it decodes
        neither."""
        path = os.path.join(self.dir, "other.mkv")
        with open(path, "wb") as f:
            f.write(other_codecs_stream())
        code, pictures, sound_bytes, said, out = self.run_mpv_on_file(path, "open", [])
        tail = "\n".join(said.splitlines()[-60:])
        self.assertGreater(len(pictures), 10, tail)                                      # Motion JPEG, decoded
        red, green, blue = png_pixel(os.path.join(out, pictures[len(pictures) // 2]), 32, 8)
        self.assertTrue(red > 180 and green < 80 and blue < 80, (red, green, blue))
        self.assertRegex(said, r"(?i)mjpeg", tail)
        self.assertGreater(sound_bytes, 10000, tail)                                     # integer PCM, decoded
        self.assertNotIn("Failed to initialize a decoder", said)

        code, pictures, sound_bytes, said, out = self.run_mpv_on_file(path, "narrow", mkv_only())
        tail = "\n".join(said.splitlines()[-60:])
        self.assertEqual(pictures, [], tail)                                             # not one picture
        self.assertLess(sound_bytes, 200, tail)                                          # and no sound (a heading at most)
        self.assertRegex(said, r"Failed to initialize a decoder for codec 'mjpeg'", tail)
        self.assertRegex(said, r"Failed to initialize a decoder for codec 'pcm_s32le'", tail)

    def paced(self, limit, seconds=4.0, w=1280, h=720, block=0.25):
        """The helper's stream at a real size and in real time (a picture every thirtieth of a second, sound in the
        longest blocks the helper takes) into mpv, which plays it at its own pace without a screen or a sound
        device. Returns mpv's log and the helper's last status."""
        n = int(block * 48000)
        log = os.path.join(self.dir, "paced-%s.log" % limit)
        picture = frame(w, h, fill=90)
        r = self.receiver(audio_wait=1.0)
        r._clock = time.monotonic
        r.start()
        self.lib.frames.put(picture)
        self.lib.sounds.put(sound(n))
        self.assertTrue(r.first.wait(3))
        self.assertEqual(r.decide(), "matroska")
        cmd = [MPV, "--no-config", "--no-terminal", "--idle=no", "--log-file=" + log, "--msg-level=all=v", "--vo=null", "--ao=null",
               "--demuxer=mkv", "--cache=no", "--cache-pause=no", "--initial-audio-sync=no", "--demuxer-readahead-secs=0", "--demuxer-max-bytes=" + limit] + mkv_only() + [self.fifo]
        mpv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: mpv.poll() is None and mpv.kill())
        try:
            self.assertTrue(wait(lambda: r.status()["state"] == "playing", 15), "mpv did not open the pipe")
            start = time.monotonic()
            frames = blocks_sent = 0
            while time.monotonic() - start < seconds:
                t = time.monotonic() - start
                if t >= (frames + 1) / 30.0:
                    self.lib.frames.put(picture)
                    frames += 1
                if t >= (blocks_sent + 1) * block:
                    self.lib.sounds.put(sound(n))
                    blocks_sent += 1
                time.sleep(0.004)
            wait(lambda: self.lib.frames.qsize() == 0 and self.lib.sounds.qsize() == 0 and r._pending is None and not r._queue, 10)
            time.sleep(0.3)
            st = r.status()
            r.close()
            try:
                mpv.wait(30)
            except subprocess.TimeoutExpired:
                self.fail("mpv did not end when the pipe did")
        finally:
            if mpv.poll() is None:
                mpv.kill()
        return (open(log, errors="replace").read() if os.path.exists(log) else ""), st

    def test_at_a_real_size_and_in_real_time_the_players_queue_takes_picture_and_sound_together(self):
        """M4. 1280 x 720 at 30 a second is 55 MB a second of picture, and the sound comes a quarter of a second
        behind it. With the queue the player is given (pipe_queue) mpv must not say "Too many packets in the demuxer
        packet queues". With a queue of 2 MiB (one picture) it must say so: that is what shows this run can see the
        line at all, and that the limit is what was read in mpv's source (one total for picture and sound).
        NOT shown: a Pi 4, 1080p, a real sound device, or anything heard."""
        limit = Player.pipe_queue(1280, 720, 30)
        said, st = self.paced(limit)
        tail = "\n".join(said.splitlines()[-40:])
        self.assertEqual(st["state"], "playing", st)
        self.assertGreater(st["counts"]["shown"], 60, st)
        self.assertGreater(st["audio"]["counts"]["written"], 8, st)
        self.assertNotIn("Too many packets in the demuxer packet queues", said, tail)
        self.assertNotIn("Failed to initialize a decoder", said, tail)
        self.assertRegex(said, r"(?i)pcm_f32le", tail)
        self.setUp()
        said, st = self.paced("2MiB")
        self.assertIn("Too many packets in the demuxer packet queues", said, "\n".join(said.splitlines()[-40:]))

    def test_mpv_plays_the_bare_frames_of_a_source_without_sound_as_before(self):
        """The same run without sound: the pipe carries bare UYVY frames and mpv is given the raw video options."""
        w, h = 64, 16
        out = os.path.join(self.dir, "out")
        os.makedirs(out)
        log = os.path.join(self.dir, "mpv.log")
        r = self.receiver(sound=False)
        r._clock = time.monotonic
        r.start()
        self.lib.frames.put(red_frame(w, h))
        self.assertTrue(r.first.wait(3))
        cmd = [MPV, "--no-config", "--no-terminal", "--idle=no", "--framedrop=no", "--untimed", "--log-file=" + log, "--msg-level=all=v",
               "--vo=image", "--vo-image-format=png", "--vo-image-outdir=" + out, "--ao=null",
               "--demuxer=rawvideo", "--demuxer-rawvideo-w=%d" % w, "--demuxer-rawvideo-h=%d" % h, "--demuxer-rawvideo-mp-format=uyvy422",
               "--demuxer-rawvideo-fps=30", "--cache=no", self.fifo]
        mpv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: mpv.poll() is None and mpv.kill())
        self.assertTrue(wait(lambda: r.status()["state"] == "playing", 15), "mpv did not open the pipe")
        for _ in range(30):
            time.sleep(1 / 30.0)
            self.lib.frames.put(red_frame(w, h))
        self.assertTrue(wait(lambda: self.lib.frames.qsize() == 0 and r._pending is None, 10))
        time.sleep(0.2)
        r.close()
        code = mpv.wait(30)
        said = open(log, errors="replace").read() if os.path.exists(log) else ""
        self.assertEqual(code, 0, "\n".join(said.splitlines()[-40:]))
        pictures = sorted(os.listdir(out))
        self.assertGreater(len(pictures), 20)
        red, green, blue = png_pixel(os.path.join(out, pictures[len(pictures) // 2]), w // 2, h // 2)
        self.assertTrue(red > 200 and green < 60 and blue < 60, (red, green, blue))


if __name__ == "__main__":
    unittest.main()
