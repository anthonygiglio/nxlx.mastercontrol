# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound for the NDI input (D62, 2026-10-08): every figure of a block of sound is the sender's and is checked here
as hostile; what the pipe carries with sound (Matroska, written by the helper) is read back by a small reader of
this test's own; and, where mpv is installed (CI; not the dev Mac), the real mpv is given that stream through a
real pipe and what it decoded is looked at. NOBODY HAS HEARD ANY OF THIS: what a box plays, how late, and whether
lips and voice agree are device step N10."""
import array
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
import unittest
import zlib

from pvj import ndi
from pvj.player import Player
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
            return self.sounds.get(timeout=0.02)
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
        self.assertTrue(wait(lambda: r.status()["audio"]["counts"]["written"] >= 1 + st["audio"]["counts"]["written"], 10))

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

    def test_a_still_picture_with_sound_is_written_once_more_with_a_later_time(self):
        r = self.start_with_sound()
        self.assertTrue(wait(lambda: len(self.stream()) >= 1))
        self.now[0] += ndi.REPEAT_AFTER + 0.05
        self.assertTrue(wait(lambda: len([b for b in self.stream() if b[0] == 1]) == 2))
        video = [b for b in self.stream() if b[0] == 1]
        self.assertEqual(video[0][2], video[1][2])
        self.assertAlmostEqual(video[1][1], ndi.REPEAT_AFTER + 0.05, places=3)
        self.assertEqual(r.status()["counts"]["repeated"], 1)

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


class ServiceSoundTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = SoundLib()
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.5, problem=lambda path: None)
        self.addCleanup(self.s.close)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def test_sound_is_on_unless_the_panel_says_no_and_the_answer_says_what_the_pipe_carries(self):
        self.assertTrue(ndi.SOUND_DEFAULT)
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": []})
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
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
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
                "counts": {"received": 10, "written": 9, "dropped": 1, "refused": 0, "silenced": 0, "filled": 2}}
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
    def test_the_stream_with_sound_is_loaded_with_mpvs_own_reader_named_and_no_cache(self):
        self.assertIn("matroska", Player.PIPE_FORMATS)
        src = inspect.getsource(Player._play_pipe)
        self.assertIn('opts = {"demuxer": "mkv", "cache": "no", "demuxer-readahead-secs": 0, "demuxer-max-bytes": "32MiB"}', src)
        sent = []

        class Ipc:
            def request(self, *a):
                sent.append(a)
                return None
        p = Player.__new__(Player)
        p.ipc, p._lock, p._pipe_globals = Ipc(), threading.RLock(), None
        p.is_running = lambda: True
        p._end_source = lambda: None
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 1920, 1080, 29.97, "matroska")
        load = [a for a in sent if a[0] == "loadfile"][0]
        self.assertEqual(load, ("loadfile", "/run/pvj-ndi/ndi.fifo", "replace", -1, "demuxer=mkv,cache=no,demuxer-readahead-secs=0,demuxer-max-bytes=32MiB"))
        self.assertNotIn("rawvideo", load[4])
        for word in ("aid", "mute", "volume", "audio-device", "af="):                     # sound is left to what the player already does
            self.assertNotIn(word, load[4])


# ---- the real mpv, where there is one --------------------------------------------------------------------------------
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
        opts = ["--%s=%s" % kv for kv in (("demuxer", "mkv"), ("cache", "no"), ("demuxer-readahead-secs", "0"), ("demuxer-max-bytes", "32MiB"))]
        self.assertIn('opts = {"demuxer": "mkv", "cache": "no", "demuxer-readahead-secs": 0, "demuxer-max-bytes": "32MiB"}',
                      inspect.getsource(Player._play_pipe))                              # the same four the player gives
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
            written = st["counts"]["shown"] + st["counts"]["repeated"]
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
