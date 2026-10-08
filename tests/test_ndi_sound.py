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

    def stream(self):
        return blocks(bytes(self.got))


class ReceiverSoundTest(Base):
    def test_picture_and_sound_go_into_one_stream_each_with_its_time(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        for k in range(1, 4):
            self.now[0] += 0.033
            self.lib.sounds.put(sound(480, value=0.5))                                   # 10 ms of sound
            self.lib.frames.put(frame(64, 16, fill=1 + k))
            self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 1 + k and len([b for b in self.stream() if b[0] == 2]) == k), k)
        got = self.stream()
        tree = ebml_tree(bytes(self.got))
        self.assertEqual(find(tree, 0x18538067, 0x1654AE6B, 0xAE, 0xE0, 0x2EB524), b"UYVY")
        video = [b for b in got if b[0] == 1]
        audio = [b for b in got if b[0] == 2]
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
        self.assertTrue(wait(lambda: any(round(b[1], 1) == 4.9 for b in self.stream() if b[0] == 2)))      # at the clock, one block's length back

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
        """The owner's default is on (SOUND_DEFAULT, kept by the panel, which sends the word every time). The helper
        itself assumes nothing: a message without the word means no sound."""
        self.assertTrue(ndi.SOUND_DEFAULT)
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
        for section, want in (({"addresses": []}, True), (None, True), ({"sound": False}, False), ({"sound": True}, True), ({"sound": "no"}, True),
                              ({"sound": 0}, True), ("nonsense", True), ([], True)):
            self.assertIs(ndi.saved_sound(section), want, section)
        w = ndi.Wanted(lambda: True, lambda: {"ndi": {"addresses": ["10.0.0.1"], "sound": False}}, log=lambda *_: None)
        self.assertEqual((w(), w.sound), ((True, ["10.0.0.1"]), False))
        w = ndi.Wanted(lambda: True, lambda: {"ndi": {"addresses": []}}, log=lambda *_: None)
        self.assertEqual((w(), w.sound), ((True, []), True))
        w = ndi.Wanted(lambda: True, lambda: [].nothing, log=lambda *_: None)             # the settings themselves cannot be read
        w()
        self.assertIs(w.sound, True)

    def test_no_new_schema_was_needed(self):
        from pvj import settings
        self.assertEqual(settings.SCHEMA, 15)
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
        self.assertIs(ndi.Input(client, s.fifo, lambda: (True, []), log=lambda *_: None).sound_wanted(), True)


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

        def lying(sid):
            return dict(real(sid), container="matroska")
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

    def test_the_stream_with_sound_is_loaded_with_mpvs_own_reader_named_and_no_cache(self):
        self.assertIn("matroska", Player.PIPE_FORMATS)
        p, sent = self.player()
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 1920, 1080, 29.97, "matroska")
        load = [a for a in sent if a[0] == "loadfile"][0]
        self.assertEqual(load, ("loadfile", "/run/pvj-ndi/ndi.fifo", "replace", -1,
                                "demuxer=mkv,cache=no,demuxer-readahead-secs=0,demuxer-max-bytes=" + Player.pipe_queue(1920, 1080, 29.97)))
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
        self.assertEqual(set(p._pipe_globals), set(only))
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
        self.assertEqual(set(p._pipe_globals), set(Player.MKV_ONLY) | {"demuxer", "cache", "demuxer-readahead-secs", "demuxer-max-bytes"})
        self.assertEqual(sent[-2], ("loadfile", "/run/pvj-ndi/ndi.fifo", "replace"))

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
        opts = ["--%s=%s" % kv for kv in (("demuxer", "mkv"), ("cache", "no"), ("demuxer-readahead-secs", "0"),
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
            self.assertLessEqual(len(pictures), written, tail)
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
               "--demuxer=mkv", "--cache=no", "--demuxer-readahead-secs=0", "--demuxer-max-bytes=" + limit] + mkv_only() + [self.fifo]
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
