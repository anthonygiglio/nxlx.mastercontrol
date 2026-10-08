# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI input above its seam, against a fake library and fake frames (D61). The real library was never loaded:
nothing here says that NDI works, only that what this project wrote does what it says with what it is given."""
import ctypes
import io
import json
import os
import queue
import shutil
import struct
import tempfile
import threading
import time
import unittest

from pvj import ndi, paths
from tests.test_server import ServerBase


def frame(w=64, h=16, fill=1, stride=None, fourcc=ndi.FOURCC_UYVY, fps=(30000, 1001), fields=1, data=None, address=1):
    stride = 2 * w if stride is None else stride
    if data is None:
        data = (bytes([fill]) * (2 * w) + b"\xee" * max(0, stride - 2 * w)) * max(h, 0)
        if fourcc == ndi.FOURCC_UYVA:
            data += b"\xaa" * (w * h)                 # the alpha plane: must never be read
    asked = []

    def view(n):
        asked.append(n)
        return memoryview(data)[:n]
    f = ndi.Frame("video", w, h, fourcc, stride, fps[0], fps[1], fields, address, view)
    f.token = asked
    return f


class FakeLib:
    """What CtypesLibrary is to the code above it, with frames from a queue."""

    def __init__(self, sources=((b"RESOLUME (Output)", b"192.168.0.20:5961"),)):
        self.raw, self.frames = list(sources), queue.Queue()
        self.finders, self.closed_finders, self.opened, self.closed, self.freed = [], 0, [], 0, 0

    def version(self):
        return "fake 6.0"

    def find_open(self, extra):
        self.finders.append(extra)
        return ("finder", len(self.finders))

    def find_sources(self, handle):
        return list(self.raw)

    def find_close(self, handle):
        self.closed_finders += 1

    def recv_open(self, raw):
        self.opened.append(raw)
        return ("recv", len(self.opened))

    def recv_capture(self, handle, timeout_ms):
        try:
            return self.frames.get(timeout=0.02)
        except queue.Empty:
            return None

    def recv_free(self, handle, f):
        self.freed += 1

    def recv_dropped(self, handle):
        return 7

    def recv_close(self, handle):
        self.closed += 1


def wait(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class NamesTest(unittest.TestCase):
    def test_a_plain_name_passes_and_gets_the_same_id_every_time(self):
        self.assertEqual(ndi.clean_name(b"RESOLUME (Output)"), "RESOLUME (Output)")
        self.assertEqual(ndi.clean_name("Caf\u00e9 MadMapper (NDI 1)"), "Caf\u00e9 MadMapper (NDI 1)")
        self.assertRegex(ndi.source_id("RESOLUME (Output)", "192.168.0.20"), r"^[0-9a-f]{12}\Z")
        self.assertEqual(ndi.source_id("a", "192.168.0.20"), ndi.source_id("a", "192.168.0.20"))
        self.assertNotEqual(ndi.source_id("a", "192.168.0.20"), ndi.source_id("b", "192.168.0.20"))

    def test_hostile_names_from_the_network_are_dropped(self):
        for bad in (b"", b" ", b"name\n", b"\nname", b"na\x00me", b"na\x1bme", b"tab\there", "evil\u202egnp.exe".encode(),
                    "zero\u200bwidth".encode(), "line\u2028sep".encode(), b" padded", b"padded ", b"\xff\xfe", b"x" * 129,
                    "\ud800", None, 5, ["a"], "\U000e0001tag"):
            self.assertIsNone(ndi.clean_name(bad), repr(bad))
        self.assertEqual(len(ndi.clean_name(b"x" * 128)), 128)

    def test_bad_names_and_addresses_drop_the_source_and_the_list_is_sorted(self):
        raw = [(b"b cam", b"10.0.0.2:5961"), (b"A cam", b"10.0.0.1:5961"),
               (b"newline", b"10.0.0.3:5961\n"), (b"space", b"10.0.0.3 5961"), (b"utf", "10.0.0.\u0663:1".encode()),
               (b"empty", b""), (b"bad\x07name", b"10.0.0.4:1"), ("text", "10.0.0.5:1"), b"junk", (b"one",), None]
        out, cut = ndi.clean_sources(raw)
        self.assertEqual(([(s["name"], s["from"]) for s in out], cut), ([("A cam", "10.0.0.1:5961"), ("b cam", "10.0.0.2:5961")], False))
        self.assertEqual(out[1]["raw"], (b"b cam", b"10.0.0.2:5961"))
        self.assertEqual(ndi.clean_sources([(b"\x00", b"x")] * 5000 + [(b"late", b"10.0.0.1:1")]), ([], True))   # the scan is bounded too

    def test_the_same_name_from_two_machines_is_two_sources_and_from_one_machine_one(self):
        # Review finding 4: an announcer of "RESOLUME (Output)" on another machine must not stand in for the real one.
        real, fake = (b"RESOLUME (Output)", b"192.168.0.20:5961"), (b"RESOLUME (Output)", b"192.168.0.66:5961")
        for order in ([real, fake], [fake, real]):
            out, _ = ndi.clean_sources(order)
            self.assertEqual([s["from"] for s in out], ["192.168.0.20:5961", "192.168.0.66:5961"])
            self.assertEqual(len({s["id"] for s in out}), 2)
            self.assertEqual(out[0]["id"], ndi.source_id("RESOLUME (Output)", "192.168.0.20"))
        again = (b"RESOLUME (Output)", b"192.168.0.20:5999")              # the sender restarted on another port: the same source
        self.assertEqual(ndi.clean_sources([again])[0][0]["id"], ndi.clean_sources([real])[0][0]["id"])
        for order in ([real, again], [again, real]):                     # both at once: one row, and always the same one
            self.assertEqual([s["from"] for s in ndi.clean_sources(order)[0]], ["192.168.0.20:5961"])
        self.assertEqual([ndi.host_of(w) for w in ("192.168.0.20:5961", "192.168.0.20", "CAM.local:5961", "[fe80::1]:5961", "[fe80::1]")],
                         ["192.168.0.20", "192.168.0.20", "cam.local", "fe80::1", "fe80::1"])

    def test_a_flood_of_announcers_cannot_push_out_the_chosen_source_or_an_address_the_owner_added(self):
        flood = [(b"!!! cam %04d" % n, b"10.9.%d.%d:5961" % (n // 250, n % 250 + 1)) for n in range(1000)]     # names that sort first
        mine, added = (b"RESOLUME (Output)", b"192.168.0.20:5961"), (b"zz MadMapper (Out)", b"192.168.0.21:5961")
        chosen = ndi.source_id("RESOLUME (Output)", "192.168.0.20")
        for raw in (flood + [mine, added], [mine, added] + flood, flood[:500] + [added, mine] + flood[500:]):
            out, cut = ndi.clean_sources(raw, prefer_hosts=["192.168.0.21"], keep_id=chosen)
            names = [s["name"] for s in out]
            self.assertEqual((len(out), cut), (ndi.MAX_SOURCES, True))
            self.assertIn("RESOLUME (Output)", names)                    # the one on the screen
            self.assertIn("zz MadMapper (Out)", names)                   # the address the owner typed
            self.assertEqual(names[:62], ["!!! cam %04d" % n for n in range(62)])     # the rest by name, whatever the order they came in
        out, cut = ndi.clean_sources(flood + [mine, added])
        self.assertNotIn("RESOLUME (Output)", [s["name"] for s in out])  # without either, a flood does hide it: the page says "cut"
        self.assertTrue(cut)
        self.assertEqual(ndi.clean_sources(flood[:64]), (ndi.clean_sources(list(reversed(flood[:64])))[0], False))


class AddressTest(unittest.TestCase):
    def test_private_ipv4_literals_only(self):
        for good in ("192.168.1.20", "10.0.0.1", "172.16.0.1", "172.31.255.254", "169.254.10.10"):
            self.assertEqual(ndi.clean_address(good), good)
        for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "172.32.0.1", "192.168.1.20\n", "192.168.1.20,8.8.8.8", "192.168.1.20 ",
                    "host.local", "::1", "192.168.1.999", "192.168.1", "192.168.1.20:5960", "", None, 5, "224.0.0.251", "100.64.0.1"):
            with self.assertRaises(ndi.NdiError, msg=repr(bad)):
                ndi.clean_address(bad)

    def test_the_saved_part_is_checked_whole(self):
        self.assertEqual(ndi.validate_saved({"addresses": ["10.0.0.1", "10.0.0.2"]}), {"addresses": ["10.0.0.1", "10.0.0.2"]})
        self.assertEqual(ndi.validate_saved({}), {"addresses": []})
        for bad in (None, [], {"addresses": "10.0.0.1"}, {"addresses": ["10.0.0.1", "10.0.0.1"]}, {"addresses": ["8.8.8.8"]},
                    {"addresses": ["10.0.0.%d" % n for n in range(17)]}, {"addresses": [], "lib": "/tmp/x.so"}, {"addresses": [None]}):
            with self.assertRaises(ndi.NdiError, msg=repr(bad)):
                ndi.validate_saved(bad)


class FrameTest(unittest.TestCase):
    def test_a_good_frame_gives_its_size_rate_and_stride(self):
        self.assertEqual(ndi.check_frame(frame(1920, 1080)), ((1920, 1080, 29.97), 3840))
        self.assertEqual(ndi.check_frame(frame(1280, 720, stride=2560 + 64, fps=(60, 1), fields=1)), ((1280, 720, 60.0), 2624))
        self.assertEqual(ndi.check_frame(frame(64, 16, fourcc=ndi.FOURCC_UYVA))[0], (64, 16, 29.97))

    def test_hostile_numbers_are_refused_before_anything_is_read(self):
        cases = [dict(w=-64), dict(w=0), dict(w=8), dict(w=3842), dict(w=65536), dict(w=65), dict(h=0), dict(h=-1), dict(h=2161),
                 dict(h=2 ** 31 - 1), dict(stride=127), dict(stride=0), dict(stride=-128), dict(stride=128 + 257), dict(stride=2 ** 31 - 1),
                 dict(address=0), dict(address=None), dict(fourcc=0x41524742), dict(fourcc=0), dict(fields=2), dict(fields=3),
                 dict(fields=9), dict(w=True), dict(w=64.0), dict(h="4")]
        for case in cases:
            f = frame(**dict(dict(data=b""), **case))
            with self.assertRaises(ndi.NdiError, msg=case):
                ndi.check_frame(f)
            self.assertEqual(f.token, [], case)                       # its memory was never asked for
        with self.assertRaises(ndi.NdiError) as e:
            ndi.check_frame(frame(fourcc=0x41524742))
        self.assertIn("BGRA", str(e.exception))
        with self.assertRaises(ndi.NdiError) as e:
            ndi.check_frame(frame(fields=2))
        self.assertIn("interlaced", str(e.exception))
        with self.assertRaises(ndi.NdiError) as e:
            ndi.check_frame(frame(fourcc=0x0a0d1b00))
        self.assertNotRegex(str(e.exception), r"[\x00-\x1f]")          # a format that is not text is shown as a number

    def test_a_rate_that_makes_no_sense_becomes_30(self):
        for n, d, want in ((30000, 1001, 29.97), (60000, 1001, 59.94), (25, 1, 25.0), (0, 0, 30.0), (1, 0, 30.0), (-30, 1, 30.0),
                           (1000, 1, 30.0), (1, 1000, 30.0), (True, 1, 30.0), (30.0, 1, 30.0), (2 ** 31 - 1, 1, 30.0)):
            self.assertEqual(ndi.frame_rate(n, d), want, (n, d))

    def test_the_copy_takes_the_picture_and_never_the_padding_or_the_alpha_plane(self):
        f = frame(64, 16, fill=5)
        fmt, stride = ndi.check_frame(f)
        out = bytearray(2 * 64 * 16)
        ndi.copy_frame(f, fmt, stride, out)
        self.assertEqual(bytes(out), b"\x05" * 2048)
        padded = frame(64, 16, fill=6, stride=160, fourcc=ndi.FOURCC_UYVA)
        fmt, stride = ndi.check_frame(padded)
        ndi.copy_frame(padded, fmt, stride, out)
        self.assertEqual(bytes(out), b"\x06" * 2048)
        self.assertEqual(padded.token, [160 * 15 + 128])                # not one byte past the last line's pixels

    def test_a_buffer_shorter_than_the_frame_says_is_refused(self):
        f = frame(64, 16, data=b"\x01" * 100)
        fmt, stride = ndi.check_frame(f)
        with self.assertRaises(ndi.NdiError):
            ndi.copy_frame(f, fmt, stride, bytearray(2048))
        with self.assertRaises(ndi.NdiError):
            ndi.copy_frame(frame(64, 16), fmt, stride, bytearray(2047))


class SeamTest(unittest.TestCase):
    """The few things about the ctypes layer that can be checked without the library."""

    @unittest.skipUnless(struct.calcsize("P") == 8, "the layout below is the 64-bit one")
    def test_the_structures_have_the_layout_the_public_headers_describe(self):
        self.assertEqual(ctypes.sizeof(ndi._Source), 16)
        self.assertEqual((ctypes.sizeof(ndi._FindCreate), ndi._FindCreate.groups.offset, ndi._FindCreate.extra_ips.offset), (24, 8, 16))
        r = ndi._RecvCreate
        self.assertEqual((ctypes.sizeof(r), r.color.offset, r.bandwidth.offset, r.fields.offset, r.name.offset), (40, 16, 20, 24, 32))
        v = ndi._Video
        self.assertEqual([getattr(v, n).offset for n, _t in v._fields_], [0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64])
        self.assertEqual(ctypes.sizeof(v), 72)
        self.assertEqual(ctypes.sizeof(ndi._Perf), 24)
        self.assertEqual(struct.pack("<I", ndi.FOURCC_UYVY), b"UYVY")
        self.assertEqual(struct.pack("<I", ndi.FOURCC_UYVA), b"UYVA")

    def test_memory_is_read_as_plain_bytes_so_a_padded_frame_copies(self):
        w, h, stride = 16, 16, 40
        raw = ctypes.create_string_buffer((b"\x09" * 32 + b"\xee" * 8) * h, stride * h)
        address = ctypes.addressof(raw)
        f = ndi.Frame("video", w, h, ndi.FOURCC_UYVY, stride, 30, 1, 1, address, lambda n: ndi._memory(address, n))
        fmt, got = ndi.check_frame(f)
        out = bytearray(2 * w * h)
        ndi.copy_frame(f, fmt, got, out)
        self.assertEqual(bytes(out), b"\x09" * (2 * w * h))

    def test_a_string_from_the_library_is_read_to_a_bound(self):
        short = ctypes.create_string_buffer(b"cam")
        self.assertEqual(ndi._cstr(ctypes.addressof(short)), b"cam")
        self.assertEqual(ndi._cstr(0), b"")
        long = ctypes.create_string_buffer(b"x" * 600)
        self.assertIsNone(ndi._cstr(ctypes.addressof(long)))           # not trusted to end: dropped, never cut

    def test_a_file_that_is_no_library_is_refused_in_words(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"not a library")
            f.flush()
            with self.assertRaises(ndi.NdiError):
                ndi.load_library(f.name)
        with self.assertRaises(ndi.NdiError) as e:
            ndi.load_library("/nonexistent/libndi.so.6")
        self.assertIn("not on this box yet", str(e.exception))


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.fifo = os.path.join(self.dir, "ndi.fifo")
        os.mkfifo(self.fifo, 0o640)
        self.lib = FakeLib()
        self.source = ndi.clean_sources(self.lib.raw)[0][0]
        self.now = [0.0]
        self.r = ndi.Receiver(self.lib, self.source, self.fifo, log=lambda *_: None, clock=lambda: self.now[0], pipe_wait=1e9)
        self.addCleanup(self.r.close)
        self.got = bytearray()
        self.reader = None

    def read(self, slow=0.0):
        def run():
            fd = os.open(self.fifo, os.O_RDONLY)
            try:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        return
                    self.got += chunk
                    if slow:
                        time.sleep(slow)
            finally:
                os.close(fd)
        self.reader = threading.Thread(target=run, daemon=True)
        self.reader.start()

    def test_frames_reach_the_pipe_whole_and_in_order(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertTrue(self.r.first.wait(3))
        self.assertEqual((self.r.format, self.r.status()["state"]), ((64, 16, 29.97), "ready"))
        self.read()
        self.assertTrue(wait(lambda: self.r.status()["state"] == "playing"))
        for n in (2, 3):
            self.lib.frames.put(frame(64, 16, fill=n))
            self.assertTrue(wait(lambda: len(self.got) >= 2048 * n))
        self.assertEqual(bytes(self.got), b"\x01" * 2048 + b"\x02" * 2048 + b"\x03" * 2048)
        st = self.r.status()
        self.assertEqual((st["width"], st["height"], st["fps"], st["name"]), (64, 16, 29.97, "RESOLUME (Output)"))
        self.assertEqual(st["counts"]["dropped_by_runtime"], 7)
        self.assertEqual(self.lib.freed, 3)                             # every frame went back to the library
        self.assertEqual(self.lib.opened, [(b"RESOLUME (Output)", b"192.168.0.20:5961")])

    def test_a_source_that_changes_size_ends_the_pipe_on_a_whole_frame(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.lib.frames.put(frame(128, 16, fill=2))
        self.assertTrue(wait(lambda: self.r.status()["state"] == "changed"))
        self.assertIn("128 x 16", self.r.status()["message"])
        self.reader.join(3)
        self.assertFalse(self.reader.is_alive())                        # the player sees the end of the pipe
        self.assertEqual(bytes(self.got), b"\x01" * 2048)                # nothing of the other size was written
        self.assertEqual(self.lib.freed, 2)

    def test_a_change_of_rate_alone_is_a_change_too(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.lib.frames.put(frame(64, 16, fps=(60, 1)))
        self.assertTrue(wait(lambda: self.r.status()["state"] == "changed"))

    def test_a_source_that_goes_away_is_waited_for_and_carries_on_when_it_returns(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.assertEqual(self.r.status()["state"], "playing")
        self.now[0] += ndi.QUIET_SECONDS + 0.5
        self.assertEqual(self.r.status()["state"], "still")             # no new frame is not a lost sender
        self.lib.frames.put(ndi.Frame("lost"))                          # the library says the connection dropped
        self.assertTrue(wait(lambda: self.r.status()["state"] == "waiting"))
        self.lib.frames.put(frame(64, 16, fill=2))
        self.assertTrue(wait(lambda: len(self.got) == 4096))
        self.assertEqual(self.r.status()["state"], "playing")
        self.assertEqual(self.lib.closed, 0)                            # the connection was kept all along

    def test_a_slow_screen_gets_the_newest_frame_and_never_part_of_one(self):
        w, h = 256, 128                                                 # 64 KiB a frame: more than the pipe takes at once
        self.r.start()
        self.lib.frames.put(frame(w, h, fill=1))
        self.assertTrue(self.r.first.wait(3))
        self.read(slow=0.002)
        self.assertTrue(wait(lambda: self.r.status()["state"] == "playing"))
        for n in range(2, 60):
            self.lib.frames.put(frame(w, h, fill=n))
        self.assertTrue(wait(lambda: self.r.status()["counts"]["received"] == 58, 10))
        self.assertTrue(wait(lambda: len(self.got) % (2 * w * h) == 0 and bytes(self.got[-1:]) == bytes([59]), 10))
        size = 2 * w * h
        fills = []
        for off in range(0, len(self.got), size):
            piece = bytes(self.got[off:off + size])
            self.assertEqual(piece, piece[:1] * size, "a frame was written in part at %d" % off)
            fills.append(piece[0])
        self.assertEqual(fills, sorted(fills))
        self.assertEqual(fills[-1], 59)                                 # the newest always gets through
        c = self.r.status()["counts"]
        self.assertEqual(c["shown"], len(fills))
        self.assertEqual(c["received"] + 1, c["shown"] + c["dropped"])     # every frame was shown or counted as dropped
        self.assertGreater(c["dropped"], 0)
        self.assertEqual(self.lib.freed, 59)

    def test_a_frame_that_cannot_be_shown_ends_it_with_the_reason(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16, fourcc=0x41524742))
        self.assertTrue(self.r.first.wait(3))
        st = self.r.status()
        self.assertEqual(st["state"], "refused")
        self.assertIn("BGRA", st["message"])
        self.assertEqual(self.lib.freed, 1)

    def test_a_hostile_second_frame_is_refused_and_not_read(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        bad = frame(64, 16, data=b"\x01" * 8)                            # says 2048 bytes, has 8
        self.lib.frames.put(bad)
        self.assertTrue(wait(lambda: self.r.status()["state"] == "refused"))
        self.assertEqual(len(self.got), 2048)

    def test_nobody_reading_the_pipe_is_said_and_does_not_hang(self):
        r = ndi.Receiver(self.lib, self.source, self.fifo, log=lambda *_: None, pipe_wait=0.2)
        self.addCleanup(r.close)
        r.start()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(wait(lambda: r.status()["state"] == "stopped"))
        self.assertIn("did not open", r.status()["message"])

    def test_a_pipe_swapped_for_a_file_is_not_written(self):
        os.unlink(self.fifo)
        with open(self.fifo, "wb"):
            pass
        self.r.start()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(wait(lambda: self.r.status()["state"] == "stopped"))
        self.assertIn("replaced", self.r.status()["message"])
        self.assertEqual(os.path.getsize(self.fifo), 0)

    def test_the_player_leaving_ends_it_and_close_leaves_no_thread_and_no_pipe(self):
        self.r.start()
        self.lib.frames.put(frame(64, 16))
        fd = os.open(self.fifo, os.O_RDONLY)
        self.assertTrue(wait(lambda: self.r.status()["state"] == "playing"))
        os.close(fd)                                                    # the player played something else
        self.lib.frames.put(frame(64, 16, fill=2))
        self.lib.frames.put(frame(64, 16, fill=3))
        self.assertTrue(wait(lambda: self.r.status()["state"] == "stopped"))
        self.assertTrue(wait(lambda: self.lib.closed == 1))             # the source is let go at once, with nobody asking
        started = time.monotonic()
        self.r.close()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])
        self.assertTrue(os.path.exists(self.fifo))                      # the pipe is the Service's to remove, not the receiver's
        self.assertEqual(self.lib.closed, 1)
        self.r.close()                                                  # twice is safe
        self.assertEqual(self.lib.closed, 1)

    def test_close_while_the_player_is_not_reading_does_not_hang(self):
        w, h = 256, 128
        self.r.start()
        fd = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK)            # open, and never read
        self.addCleanup(os.close, fd)
        self.lib.frames.put(frame(w, h, fill=9))
        self.assertTrue(wait(lambda: self.r.status()["state"] == "playing"))
        for n in range(1, 6):
            self.lib.frames.put(frame(w, h, fill=n))
        self.assertTrue(wait(lambda: self.r.status()["counts"]["dropped"] > 0))
        started = time.monotonic()
        self.r.close()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])


class FakeClient:
    """The panel's client, straight into a Service (the socket itself is netd's, tested there)."""

    def __init__(self, service):
        self.service, self.sent, self.down, self.answer = service, [], False, None

    def request(self, message, timeout=None):
        self.sent.append(message)
        if self.down:
            raise ndi.NdiError("the NDI helper (pvj-ndi) is not running")
        if self.answer is not None:
            return self.answer
        return json.loads(json.dumps(self.service.handle(json.loads(json.dumps(message)))))

    def status(self):
        try:
            return self.request({"cmd": "status"})
        except ndi.NdiError:
            return {"ok": False}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FakeLib([(b"RESOLUME (Output)", b"192.168.0.20:5961"), (b"evil\nname", b"10.0.0.9:1"), (b"MAD (Out)", b"192.168.0.21:5961")])
        self.missing = False

        def loader(path):
            if self.missing:
                raise ndi.NdiError("the NDI runtime is not on this box yet")
            return self.lib
        self.s = ndi.Service(self.dir, "/opt/pvj-ndi/libndi.so.6", loader=loader, log=lambda *_: None, first_frame=0.3,
                             problem=lambda path: "the NDI runtime is not on this box yet" if self.missing else None)
        self.addCleanup(self.s.close)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def test_it_does_nothing_until_it_is_told_the_module_is_on(self):
        st = self.s.handle({"cmd": "status"})
        self.assertEqual((st["ok"], st["configured"], st["on"], st["sources"], st["playing"]), (True, False, False, [], None))
        self.assertEqual(self.lib.finders, [])
        self.assertEqual(self.s.handle({"cmd": "open", "id": self.rid})["ok"], False)

    def test_without_the_runtime_it_says_so_and_lists_nothing(self):
        self.missing = True
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.assertEqual(st["runtime"], {"present": False, "loaded": False, "version": "", "problem": "the NDI runtime is not on this box yet"})
        self.assertEqual(st["sources"], [])
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual((reply["ok"], reply["error"]), (False, "the NDI runtime is not on this box yet"))
        self.missing = False                                           # the owner installed it; the panel says "on" again
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.assertEqual((st["runtime"]["loaded"], st["runtime"]["version"], len(st["sources"])), (True, "fake 6.0", 2))

    def test_sources_are_listed_checked_and_the_addresses_reach_the_finder(self):
        st = self.s.handle({"cmd": "configure", "on": True, "addresses": ["192.168.0.20", "10.0.0.5"]})
        self.assertEqual(self.lib.finders, ["192.168.0.20,10.0.0.5"])
        self.assertEqual([s["name"] for s in st["sources"]], ["MAD (Out)", "RESOLUME (Output)"])
        self.assertEqual(st["sources"][1], {"id": self.rid, "name": "RESOLUME (Output)", "from": "192.168.0.20:5961"})
        self.s.handle({"cmd": "configure", "on": True, "addresses": ["192.168.0.20", "10.0.0.5"]})
        self.assertEqual(len(self.lib.finders), 1)                     # nothing changed: the finder is kept
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.assertEqual((self.lib.finders[-1], self.lib.closed_finders), ("", 1))

    def test_bad_requests_are_refused(self):
        for message in (None, [], {"cmd": "run"}, {"cmd": "configure", "on": "yes", "addresses": []},
                        {"cmd": "configure", "on": True, "addresses": ["8.8.8.8"]}, {"cmd": "configure", "on": True, "addresses": ["10.0.0.1,10.0.0.2"]},
                        {"cmd": "configure", "on": True}):
            self.assertEqual(self.s.handle(message)["ok"], False, message)
        self.assertEqual(self.lib.finders, [])
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        for sid in (self.rid + "\n", self.rid.upper(), "RESOLUME (Output)", "../x", None, 5, "0" * 12):
            reply = self.s.handle({"cmd": "open", "id": sid})
            self.assertEqual(reply["ok"], False, sid)
        self.assertEqual(self.lib.opened, [])

    def test_open_answers_with_the_first_frames_size_and_makes_the_pipe(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.frames.put(frame(1280, 720, fps=(60, 1)))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertTrue(reply["ok"], reply)
        p = reply["playing"]
        self.assertEqual((p["id"], p["name"], p["width"], p["height"], p["fps"], p["state"]), (self.rid, "RESOLUME (Output)", 1280, 720, 60.0, "ready"))
        import stat
        st = os.stat(self.s.fifo)
        self.assertTrue(stat.S_ISFIFO(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode) & 0o137, 0)          # never writable by the group, nothing for others
        self.assertEqual(self.s.fifo, os.path.join(self.dir, paths.NDI_FIFO))
        self.assertEqual(self.s.handle({"cmd": "status"})["playing"]["id"], self.rid)
        self.assertEqual(self.s.handle({"cmd": "close"}), {"ok": True})
        self.assertFalse(os.path.exists(self.s.fifo))
        self.assertEqual(self.lib.closed, 1)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])

    def test_a_source_that_sends_no_picture_is_said_and_let_go(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply["ok"], False)
        self.assertIn("sent no picture", reply["error"])
        self.assertEqual((self.lib.closed, self.s.receiver, os.path.exists(self.s.fifo)), (1, None, False))

    def test_a_source_that_vanished_from_the_network_cannot_be_opened(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.assertEqual(len(self.s.handle({"cmd": "status"})["sources"]), 2)
        self.lib.raw = [self.lib.raw[2]]                               # Resolume quit
        reply = self.s.handle({"cmd": "open", "id": self.rid})         # within the second the list is kept for
        self.assertEqual((reply["ok"], reply["error"]), (False, "that source is not on the network now"))
        self.assertEqual(self.lib.opened, [])

    def test_a_refused_first_frame_gives_the_reason(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.frames.put(frame(64, 16, fields=2))
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply["ok"], False)
        self.assertIn("interlaced", reply["error"])

    def test_switching_off_lets_go_of_everything_and_a_second_open_replaces_the_first(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        threading.Timer(0.1, self.lib.frames.put, [frame(64, 16)]).start()      # after the first receiver has been let go
        self.assertTrue(self.s.handle({"cmd": "open", "id": ndi.source_id("MAD (Out)", "192.168.0.21")})["ok"])
        self.assertEqual((len(self.lib.opened), self.lib.closed), (2, 1))
        st = self.s.handle({"cmd": "configure", "on": False, "addresses": []})
        self.assertEqual((st["on"], st["sources"], st["playing"], self.lib.closed, self.lib.closed_finders), (False, [], None, 2, 1))
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])

    def test_an_ended_receiver_is_let_go_at_the_next_status_and_its_reason_stays(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(wait(lambda: self.s.receiver.state == "changed"))
        self.assertTrue(wait(lambda: self.lib.closed == 1))
        st = self.s.handle({"cmd": "status"})
        self.assertEqual((st["playing"]["state"], self.lib.closed, os.path.exists(self.s.fifo)), ("changed", 1, False))


class InputTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FakeLib()
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(self.s.close)
        self.client = FakeClient(self.s)
        self.wanted = [True, ["10.0.0.5"]]
        self.now = [100.0]
        self.i = ndi.Input(self.client, self.s.fifo, lambda: (self.wanted[0], list(self.wanted[1])), log=lambda *_: None, clock=lambda: self.now[0])
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def test_the_first_status_tells_the_helper_what_is_wanted_and_a_change_is_sent_on(self):
        st = self.i.status()
        self.assertEqual([m["cmd"] for m in self.client.sent], ["status", "configure"])
        self.assertEqual(self.lib.finders, ["10.0.0.5"])
        self.assertEqual((st["helper"], st["runtime"]["loaded"], st["addresses"], [s["name"] for s in st["sources"]]),
                         (True, True, ["10.0.0.5"], ["RESOLUME (Output)"]))
        self.assertEqual(st["install"]["get"], "https://ndi.video/")
        self.assertIn("pvj-ndi-runtime install", st["install"]["command"])
        self.i.status()
        self.assertEqual(len(self.lib.finders), 1)
        self.wanted[1] = []
        self.i.status()
        self.assertEqual(self.lib.finders[-1], "")
        self.wanted[0] = False
        self.assertEqual(self.i.status()["sources"], [])
        self.assertEqual(self.s.on, False)

    def test_a_helper_that_is_not_running_is_a_plain_no(self):
        self.client.down = True
        st = self.i.status()
        self.assertEqual((st["helper"], st["sources"], st["runtime"]["present"], st["playing"]), (False, [], False, None))
        with self.assertRaises(ndi.NdiError):
            self.i.open(self.rid)
        self.assertFalse(self.i.tick(lambda sid: self.fail("no replay")))

    def test_what_a_taken_over_helper_says_is_checked_again(self):
        self.i.current = {"id": self.rid, "name": "x"}
        self.client.answer = {"ok": True, "configured": True, "on": True, "addresses": ["10.0.0.5"],
                              "runtime": {"present": "yes", "loaded": 1, "version": "v" * 500, "problem": ["x"]},
                              "cut": "yes",
                              "sources": [{"id": self.rid, "name": "RESOLUME (Output)", "from": "a b"},
                                          {"id": self.rid, "name": "Other name", "from": "10.0.0.1:1"},        # the id is not this name's
                                          {"id": ndi.source_id("bad\u202ename", "192.168.0.20"), "name": "bad\u202ename", "from": "x"},
                                          {"id": "../../etc", "name": "x", "from": "x"}, "junk", None] + [{"id": self.rid, "name": "RESOLUME (Output)", "from": "192.168.0.66:5961"}] * 500 + [{"id": self.rid, "name": "RESOLUME (Output)", "from": "192.168.0.20:5961"}],
                              "playing": {"id": self.rid, "name": "evil\nname", "state": "playing", "message": "m" * 5000, "width": 10 ** 9,
                                          "height": True, "fps": 1e9, "counts": {"received": 2 ** 80, "shown": -1, "dropped": "3", "x": 1, "dropped_by_runtime": 4}}}
        st = self.i.status()
        self.assertEqual(st["runtime"], {"present": False, "loaded": False, "version": "v" * 80, "problem": ""})
        self.assertEqual(st["sources"], [])                               # rows past the bound are not looked at, and an id that
        self.assertIs(st["cut"], False)                                  # is not its name and machine's own is not shown
        self.client.answer["sources"] = self.client.answer["sources"][-3:]
        self.assertEqual(self.i.status()["sources"], [{"id": self.rid, "name": "RESOLUME (Output)", "from": "192.168.0.20:5961"}])
        self.assertEqual(st["playing"], {"id": self.rid, "name": "NDI source", "state": "playing", "message": "m" * 200,
                                         "counts": {"dropped_by_runtime": 4}})
        for answer in ({"ok": True, "playing": {"id": self.rid, "state": "ready", "width": 64, "height": 16}},          # no rate
                       {"ok": True, "playing": {"id": ndi.source_id("b", "192.168.0.20"), "state": "ready", "width": 64, "height": 16, "fps": 30}},
                       {"ok": True, "playing": "x"}, {"ok": True}, {"ok": "yes"}):
            self.client.answer = answer
            with self.assertRaises(ndi.NdiError, msg=answer):
                self.i.open(self.rid)
        self.client.answer = {"ok": False, "error": "e" * 999}
        with self.assertRaises(ndi.NdiError) as e:
            self.i.open(self.rid)
        self.assertEqual(len(str(e.exception)), 200)

    def test_open_gives_the_size_the_player_needs_and_refuses_what_is_not_an_id(self):
        self.i.status()
        self.lib.frames.put(frame(1920, 1080))
        p = self.i.open(self.rid)
        self.assertEqual((p["width"], p["height"], p["fps"], p["name"]), (1920, 1080, 29.97, "RESOLUME (Output)"))
        sent = len(self.client.sent)
        for bad in ("RESOLUME (Output)", self.rid + "\n", None, 5, ""):
            with self.assertRaises(ndi.NdiError):
                self.i.open(bad)
        self.assertEqual(len(self.client.sent), sent)                  # nothing but an id is ever sent on

    def test_a_changed_source_is_shown_again_once_and_then_only_after_a_pause(self):
        self.i.status()
        self.lib.frames.put(frame(64, 16))
        self.i.open(self.rid)
        self.i.current = {"id": self.rid, "name": "RESOLUME (Output)"}
        replays = []
        self.assertFalse(self.i.tick(replays.append))                  # ready or playing: leave it alone
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(wait(lambda: self.s.receiver.state == "changed"))
        self.assertTrue(self.i.tick(replays.append))
        self.assertFalse(self.i.tick(replays.append))                  # not again within the pause
        self.now[0] += ndi.Input.RETRY_SECONDS + 0.1
        self.assertTrue(self.i.tick(replays.append))
        self.assertEqual(replays, [self.rid, self.rid])

    def test_a_replay_that_fails_is_logged_and_tried_again_later(self):
        self.i.status()
        self.i.current = {"id": self.rid, "name": "x"}                 # the helper has nothing open: it started again

        def boom(sid):
            raise ndi.NdiError("that source is not on the network now")
        self.assertTrue(self.i.tick(boom))
        self.assertEqual(self.i.current["id"], self.rid)               # still wanted

    def test_a_helper_that_started_again_is_told_the_settings_before_the_replay(self):
        self.i.current = {"id": self.rid, "name": "x"}
        seen = []
        self.assertTrue(self.i.tick(lambda sid: seen.append(self.s.on)))
        self.assertEqual(seen, [True])

    def test_a_player_that_went_on_to_something_else_by_itself_ends_it_quietly(self):
        # Vibes or a schedule entry may take the screen without telling the NDI input: the pipe just loses its reader.
        self.i.status()
        self.lib.frames.put(frame(64, 16))
        self.i.open(self.rid)
        self.i.current = {"id": self.rid, "name": "x"}
        fd = os.open(self.s.fifo, os.O_RDONLY)
        self.assertTrue(wait(lambda: self.s.receiver.status()["state"] == "playing"))
        os.close(fd)
        self.lib.frames.put(frame(64, 16))
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(wait(lambda: self.s.receiver.status()["message"] == ndi.PLAYER_LEFT))
        self.assertFalse(self.i.tick(lambda sid: self.fail("no replay")))
        self.assertIsNone(self.i.current)
        self.assertIsNone(self.i.status()["playing"])
        self.assertEqual(self.lib.closed, 1)                           # and the source was let go

    def test_refused_and_stopped_are_not_retried_and_nothing_wanted_means_no_questions(self):
        self.i.status()
        self.lib.frames.put(frame(64, 16))
        self.i.open(self.rid)
        self.i.current = {"id": self.rid, "name": "x"}
        self.lib.frames.put(frame(64, 16, fields=3))
        self.assertTrue(wait(lambda: self.s.receiver.state == "refused"))
        self.assertFalse(self.i.tick(lambda sid: self.fail("no replay")))
        st = self.i.status()                                           # it is no longer what plays, and the page keeps the reason
        self.assertEqual((st["playing"], st["ended"]["name"]), (None, "x"))
        self.assertIn("interlaced", st["ended"]["message"])
        self.assertIsNone(self.i.current)
        self.assertFalse(self.i.stop())
        sent = len(self.client.sent)
        self.assertFalse(self.i.tick(lambda sid: self.fail("no replay")))
        self.assertFalse(self.i.stop())
        self.assertEqual(len(self.client.sent), sent)
        self.assertIsNone(self.i.status()["playing"])


def elf(machine, bits=64, little=True):
    return b"\x7fELF" + bytes([bits // 32, 1 if little else 2]) + b"\x01" + b"\0" * 9 + struct.pack("<H" if little else ">H", 3) + \
        struct.pack("<H" if little else ">H", machine) + b"\0" * 64


class RuntimeFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.me = (os.getuid(),)

    def put(self, name, data, mode=0o644):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, mode)
        return path

    def test_the_header_says_which_processor_a_file_is_for(self):
        self.assertEqual(ndi.elf_machine(self.put("a", elf(183))), (183, 64))
        self.assertEqual(ndi.elf_machine(self.put("b", elf(40, 32))), (40, 32))
        self.assertEqual(ndi.elf_machine(self.put("c", elf(62, little=False))), (62, 64))
        for bad in (b"", b"\x7fELF", b"MZ" + b"\0" * 64, b"\x7fELF\x03\x01" + b"\0" * 64, b"#!/bin/sh\nrm -rf /\n" + b" " * 20):
            self.assertIsNone(ndi.elf_machine(self.put("d", bad)), bad)
        self.assertIsNone(ndi.elf_machine(os.path.join(self.dir, "missing")))
        self.assertEqual([ndi.box_machine(m, p) for m, p in (("aarch64", 8), ("aarch64", 4), ("x86_64", 8), ("armv7l", 4), ("i686", 4), ("riscv64", 8))],
                         [183, 40, 62, 40, 3, None])

    def test_a_file_for_another_processor_is_named_in_plain_words(self):
        path = self.put("libndi.so.6", elf(62))
        self.assertIsNone(ndi.runtime_problem(path, self.me, machine=62))
        self.assertEqual(ndi.runtime_problem(path, self.me, machine=183), "the NDI runtime file is for x86_64, this box is aarch64")
        self.assertEqual(ndi.runtime_problem(os.path.join(self.dir, "nope"), self.me, machine=62), "the NDI runtime is not on this box yet")
        self.assertIn("not a program library", ndi.runtime_problem(self.put("text", b"hello" * 10), self.me, machine=62))

    def test_a_file_someone_else_could_change_is_not_loaded(self):
        good = self.put("libndi.so.6", elf(62))
        self.assertIn("only root can change", ndi.runtime_problem(good, (0,), machine=62) if os.getuid() else "only root can change")
        self.assertIn("only root can change", ndi.runtime_problem(self.put("w", elf(62), 0o664), self.me, machine=62))
        self.assertIn("only root can change", ndi.runtime_problem(self.put("o", elf(62), 0o646), self.me, machine=62))
        link = os.path.join(self.dir, "link")
        os.symlink(good, link)
        self.assertIn("only root can change", ndi.runtime_problem(link, self.me, machine=62))
        self.assertIn("only root can change", ndi.runtime_problem(self.dir, self.me, machine=62))

    def test_install_takes_the_right_file_from_an_sdk_folder_and_checks_the_copy(self):
        sdk = os.path.join(self.dir, "NDI SDK for Linux")
        self.put("NDI SDK for Linux/lib/aarch64-rpi4-linux-gnueabi/libndi.so.6.1.1", elf(183) + b"arm64")
        os.symlink("libndi.so.6.1.1", os.path.join(sdk, "lib/aarch64-rpi4-linux-gnueabi/libndi.so.6"))
        self.put("NDI SDK for Linux/lib/x86_64-linux-gnu/libndi.so.6.1.1", elf(62) + b"x86")
        dest = os.path.join(self.dir, "opt")
        path = ndi.install_runtime(sdk, dest, machine=183, chown=False)
        self.assertEqual(path, os.path.join(dest, ndi.LIB_NAME))
        with open(path, "rb") as f:
            self.assertTrue(f.read().endswith(b"arm64"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(os.listdir(dest), [ndi.LIB_NAME])
        ndi.install_runtime(sdk, dest, machine=62, chown=False)        # again, over the first
        with open(path, "rb") as f:
            self.assertTrue(f.read().endswith(b"x86"))

    def test_install_refuses_the_wrong_file_and_leaves_nothing_behind(self):
        dest = os.path.join(self.dir, "opt")
        wrong = self.put("libndi.so.6", elf(62))
        with self.assertRaises(ndi.NdiError) as e:
            ndi.install_runtime(wrong, dest, machine=183, chown=False)
        self.assertIn("is for x86_64, this box is aarch64", str(e.exception))
        self.assertEqual(os.listdir(dest), [])
        for source in (self.put("script.sh", b"#!/bin/sh\n" + b" " * 40), os.path.join(self.dir, "missing"), self.dir):
            with self.assertRaises(ndi.NdiError, msg=source):
                ndi.install_runtime(source, dest, machine=183, chown=False)
        self.assertEqual(os.listdir(dest), [])
        # a link in the SDK folder that points outside it is not followed
        self.put("sdk2/lib/aarch64-rpi4-linux-gnueabi/readme", b"x")
        os.symlink(wrong, os.path.join(self.dir, "sdk2/lib/aarch64-rpi4-linux-gnueabi/libndi.so.6"))
        self.assertIsNone(ndi.find_in_sdk(os.path.join(self.dir, "sdk2"), 183))

    def test_the_command_explains_itself_and_changes_nothing_without_root(self):
        out = io.StringIO()
        self.assertEqual(ndi.runtime_main([], out), 2)
        self.assertIn("https://ndi.video/", out.getvalue())
        self.assertIn("registered trademark of Vizrt NDI AB", out.getvalue())
        if os.geteuid() != 0:
            out = io.StringIO()
            self.assertEqual(ndi.runtime_main(["install", "/tmp/x"], out), 1)
            self.assertIn("sudo", out.getvalue())
            self.assertEqual(ndi.runtime_main(["remove"], io.StringIO()), 1)
        self.assertEqual(ndi.runtime_main(["status"], io.StringIO()), 0)


class NdiApiTest(ServerBase):
    """The routes, with the real Service behind a client that calls it directly, and the fake library behind that."""

    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.lib = FakeLib([(b"RESOLUME (Output)", b"192.168.0.20:5961"), (b"MAD (Out)", b"192.168.0.21:5961")])
        self.missing = False

        def loader(path):
            if self.missing:
                raise ndi.NdiError("the NDI runtime is not on this box yet")
            return self.lib
        self.ndidir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ndidir, True)
        self.service = ndi.Service(self.ndidir, "x", loader=loader, log=lambda *_: None, first_frame=0.3,
                                   problem=lambda path: "the NDI runtime is not on this box yet" if self.missing else None)
        self.addCleanup(self.service.close)
        self.client = FakeClient(self.service)
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(self.client, self.service.fifo, lambda: (reg.enabled("inputs-ndi"), list(st.data["ndi"]["addresses"])),
                                 log=lambda *_: None)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def invite(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "g", "role": role}, token=self.full)[1]["token"]

    def enable(self, on=True):
        return self.call("POST", "/api/modules/inputs-ndi", {"enabled": on}, token=self.full)

    def test_the_module_is_off_by_default_and_everything_is_refused_until_it_is_on(self):
        self.assertEqual(self.call("GET", "/api/ndi", token=self.full)[0], 409)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "10.0.0.5"}, token=self.full)[0], 409)
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 409)
        self.assertEqual(self.lib.finders, [])                         # the helper has not touched the network
        self.assertEqual(self.enable()[0], 200)
        self.assertEqual(self.lib.finders, [""])                       # switching on tells the helper at once

    def test_the_page_gets_sources_the_runtime_and_how_to_get_it(self):
        self.enable()
        st, body, _ = self.call("GET", "/api/ndi", token=self.invite("view"))
        self.assertEqual(st, 200)
        self.assertEqual([s["name"] for s in body["sources"]], ["MAD (Out)", "RESOLUME (Output)"])
        self.assertEqual((body["helper"], body["runtime"]["loaded"], body["playing"], body["addresses"]), (True, True, None, []))
        self.assertEqual(body["install"]["get"], "https://ndi.video/")

    def test_without_the_runtime_the_page_is_told_in_plain_words(self):
        self.missing = True
        self.enable()
        body = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((body["helper"], body["runtime"]["present"], body["runtime"]["problem"], body["sources"]),
                         (True, False, "the NDI runtime is not on this box yet", []))
        st, out, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, out["error"]), (409, "the NDI runtime is not on this box yet"))
        self.assertFalse([c for c in self.player.calls if c[0] == "play_pipe"])

    def test_without_the_helper_the_page_still_answers(self):
        self.enable()
        self.client.down = True
        st, body, _ = self.call("GET", "/api/ndi", token=self.full)
        self.assertEqual((st, body["helper"], body["sources"]), (200, False, []))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 409)
        self.assertEqual(self.enable(False)[0], 200)                   # it can still be switched off

    def test_play_loads_the_pipe_with_the_first_frames_size_in_uyvy(self):
        self.enable()
        self.lib.frames.put(frame(1920, 1080, fps=(60000, 1001)))
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.invite("live"))
        self.assertEqual((st, body), (200, {"playing": "ndi", "name": "RESOLUME (Output)", "width": 1920, "height": 1080, "fps": 59.94}))
        self.assertIn(("play_pipe", self.service.fifo, 1920, 1080, 59.94, "uyvy422"), self.player.calls)
        self.assertEqual(self.api.ndi.current, {"id": self.rid, "name": "RESOLUME (Output)"})
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((page["playing"]["name"], page["playing"]["width"]), ("RESOLUME (Output)", 1920))
        self.player.status = lambda: {"running": True, "path": self.service.fifo}
        pl = self.call("GET", "/api/status", token=self.full)[1]["player"]
        self.assertEqual((pl["path"], pl["ndi"]), (None, "RESOLUME (Output)"))     # the pipe's path is never shown

    def test_play_takes_an_id_and_nothing_else_and_view_may_not(self):
        self.enable()
        for bad in ("RESOLUME (Output)", "192.168.0.20:5961", self.rid + "\n", "../../etc/passwd", None, 5, {"name": "x"}, [self.rid], "f" * 12):
            self.assertEqual(self.call("POST", "/api/play", {"ndi": bad}, token=self.full)[0], 409, bad)
        self.assertEqual(self.lib.opened, [])
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.invite("view"))[0], 403)
        self.assertFalse([c for c in self.player.calls if c[0] == "play_pipe"])

    def test_playing_something_else_stopping_and_switching_off_let_go_of_the_source(self):
        self.enable()
        for leave in (lambda: self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.full),
                      lambda: self.call("POST", "/api/control", {"action": "stop"}, token=self.full),
                      lambda: self.enable(False)):
            self.enable()
            self.lib.frames.put(frame(64, 16))
            self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
            closed = self.lib.closed
            self.assertEqual(leave()[0], 200)
            self.assertEqual((self.lib.closed, self.api.ndi.current, self.service.receiver), (closed + 1, None, None))
        self.assertEqual((self.service.on, self.lib.closed_finders), (False, 1))
        self.assertIn(("clear",), self.player.calls)                   # switched off while showing: the last frame goes

    def test_a_source_that_changes_size_is_loaded_again_by_the_watch(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertFalse(self.api.ndi_tick())
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(wait(lambda: self.service.receiver.state == "changed"))
        threading.Timer(0.1, self.lib.frames.put, [frame(128, 16)]).start()
        self.assertTrue(self.api.ndi_tick())
        self.assertIn(("play_pipe", self.service.fifo, 128, 16, 29.97, "uyvy422"), self.player.calls)
        self.assertEqual(self.api.ndi.current["id"], self.rid)

    def test_a_reload_that_lost_the_race_with_a_clip_does_not_take_the_screen_back(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        was = self.api.ndi.current
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.full)          # the operator moved on
        pipes = len([c for c in self.player.calls if c[0] == "play_pipe"])
        self.assertEqual(self.api.play_ndi({"ndi": self.rid}, again=was), {"playing": None})
        self.assertEqual(len([c for c in self.player.calls if c[0] == "play_pipe"]), pipes)
        self.assertEqual(len(self.lib.opened), 1)                      # the source was not even opened again
        self.assertFalse(self.api.ndi_tick())

    def test_what_is_played_or_stopped_while_a_source_is_still_connecting_keeps_the_screen(self):
        # Connecting takes seconds. The later choice stands: the pipe is never loaded over it and the source is let go.
        self.enable()
        real = self.client.request
        for later in ({"path": "/api/play", "body": {"file": "a.mp4"}}, {"path": "/api/control", "body": {"action": "stop"}}):
            gate, entered = threading.Event(), threading.Event()

            def slow(message, timeout=None):
                if message.get("cmd") != "open":
                    return real(message, timeout)
                self.lib.frames.put(frame(64, 16))
                reply = real(message, timeout)             # the helper has the source open and a first frame
                entered.set()
                gate.wait(5)
                return reply
            self.client.request = slow
            out, pipes, closed = [], len([c for c in self.player.calls if c[0] == "play_pipe"]), self.lib.closed
            t = threading.Thread(target=lambda: out.append(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)))
            t.start()
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.call("POST", later["path"], later["body"], token=self.full)[0], 200)
            gate.set()
            t.join(5)
            self.client.request = real
            self.assertEqual((out[0][0], out[0][1]["error"]), (409, "something else was played while the source was connecting"), later)
            self.assertEqual(len([c for c in self.player.calls if c[0] == "play_pipe"]), pipes, later)
            self.assertEqual((self.api.ndi.current, self.service.receiver, self.lib.closed), (None, None, closed + 1), later)

    def test_a_close_waits_longer_than_the_helper_may_be_busy_with_a_first_frame(self):
        self.enable()
        self.api.ndi.client_close()
        self.assertEqual(self.client.sent[-1], {"cmd": "close"})
        seen = []
        self.client.request = lambda message, timeout=None: seen.append(timeout) or {"ok": True}
        self.api.ndi.client_close()
        self.assertGreater(seen[0], ndi.FIRST_FRAME_SECONDS)

    def test_addresses_are_added_checked_saved_and_sent_to_the_helper(self):
        self.enable()
        st, body, _ = self.call("POST", "/api/ndi", {"action": "add_address", "address": "192.168.1.20"}, token=self.full)
        self.assertEqual((st, body["addresses"]), (200, ["192.168.1.20"]))
        self.assertEqual(self.lib.finders[-1], "192.168.1.20")
        from pvj.settings import Settings
        self.assertEqual(Settings(self.settings.path).load()["ndi"], {"addresses": ["192.168.1.20"]})
        for bad in ("8.8.8.8", "192.168.1.20\n", "192.168.1.21,8.8.8.8", "cam.local", "", None, 7, "127.0.0.1"):
            self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": bad}, token=self.full)[0], 400, bad)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "192.168.1.20"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "remove_address", "address": "10.9.9.9"}, token=self.full)[0], 404)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "run", "address": "10.0.0.1"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "10.0.0.1"}, token=self.invite("live"))[0], 403)
        self.assertEqual(self.settings.data["ndi"], {"addresses": ["192.168.1.20"]})
        for n in range(15):
            self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "10.0.0.%d" % (n + 1)}, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "10.0.1.1"}, token=self.full)[0], 400)
        st, body, _ = self.call("POST", "/api/ndi", {"action": "remove_address", "address": "192.168.1.20"}, token=self.full)
        self.assertEqual((st, len(body["addresses"])), (200, 15))

    def test_a_settings_file_carries_the_addresses_and_a_bad_one_is_refused(self):
        self.enable()
        self.call("POST", "/api/ndi", {"action": "add_address", "address": "192.168.1.20"}, token=self.full)
        st, out, _ = self.call("POST", "/api/system/settings/export", {}, token=self.full)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["file"]["settings"]["ndi"], {"addresses": ["192.168.1.20"]})
        from pvj import boxcare
        self.assertEqual(boxcare.check_ndi({"addresses": ["10.0.0.1"]}, None), {"addresses": ["10.0.0.1"]})
        for bad in ({"addresses": ["8.8.8.8"]}, {"addresses": ["10.0.0.1\n"]}, {"lib": "/tmp/evil.so"}, "x"):
            with self.assertRaises(boxcare.CHECK_ERRORS, msg=bad):
                boxcare.check_ndi(bad, None)


class PlayerPipeFormatTest(unittest.TestCase):
    def test_the_pipe_is_loaded_in_the_format_asked_for_and_an_unknown_one_is_refused(self):
        from pvj.player import Player, PlayerError
        sent = []

        class Ipc:
            def request(self, *a):
                sent.append(a)
        p = Player.__new__(Player)
        p.ipc, p._lock = Ipc(), threading.RLock()
        p.is_running, p._end_source = (lambda: True), (lambda: None)
        p.play_pipe("/run/pvj-ndi/ndi.fifo", 1920, 1080, 59.94, "uyvy422")
        load = [a for a in sent if a[0] == "loadfile"][0]
        self.assertIn("demuxer-rawvideo-mp-format=uyvy422", load[4])
        self.assertIn("demuxer-rawvideo-fps=59.94", load[4])
        self.assertIn("demuxer-rawvideo-w=1920", load[4])
        sent.clear()
        p.play_pipe("/x", 1280, 720, 30)
        load = [a for a in sent if a[0] == "loadfile"][0]
        self.assertIn("demuxer-rawvideo-mp-format=yuyv422", load[4])   # the capture input is as it was
        self.assertIn("demuxer-rawvideo-fps=30,", load[4])
        for bad in ("bgra", "uyvy422,cache=yes", "", None):
            with self.assertRaises(PlayerError):
                p.play_pipe("/x", 16, 16, 30, bad)


class HelperCannotHoldThePanelTest(unittest.TestCase):
    """Review finding 1: the helper is the part that could be taken over, and the panel asks it while it starts."""

    def serve(self, behave):
        import socket
        d = tempfile.mkdtemp(dir="/tmp")                # a short path: a Unix socket name is limited
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "s")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        self.addCleanup(srv.close)

        def run():
            try:
                conn, _ = srv.accept()
                with conn:
                    behave(conn)
            except OSError:
                pass
        threading.Thread(target=run, daemon=True).start()
        return path

    def test_a_reply_that_trickles_in_cannot_outlast_the_callers_timeout(self):
        import socket
        from pvj.netd import exchange

        def trickle(conn):
            conn.recv(65536)
            for _ in range(200):                        # one byte at a time, each well inside the timeout
                conn.sendall(b" ")
                time.sleep(0.05)
        path = self.serve(trickle)
        started = time.monotonic()
        with self.assertRaises(socket.timeout):
            exchange(path, {"cmd": "status"}, 0.4)
        self.assertLess(time.monotonic() - started, 1.2)                # it was 0.4 s times the number of bytes
        c = ndi.Client(self.serve(trickle), timeout=0.4)
        started = time.monotonic()
        self.assertEqual(c.status(), {"ok": False})
        self.assertLess(time.monotonic() - started, 1.2)

    def test_the_other_helpers_clients_still_get_a_whole_answer_and_a_refusal(self):
        from pvj.netd import exchange

        def answer(conn):
            conn.recv(65536)
            conn.sendall(b'{"ok": true, ')
            time.sleep(0.1)
            conn.sendall(b'"n": 1}\n')
        self.assertEqual(json.loads(exchange(self.serve(answer), {"cmd": "status"}, 2)), {"ok": True, "n": 1})
        refuse = lambda conn: conn.sendall(b'{"ok": false, "error": "not allowed"}\n')      # answers and closes, unread
        self.assertEqual(json.loads(exchange(self.serve(refuse), {"cmd": "status"}, 2))["error"], "not allowed")
        with self.assertRaises(OSError):
            exchange(os.path.join(tempfile.gettempdir(), "no-such-socket"), {"cmd": "status"}, 1)

    def test_a_reply_nested_thousands_deep_is_a_bad_answer_and_never_an_error_of_another_kind(self):
        from unittest import mock
        c = ndi.Client("unused")
        c._exchange = lambda message, timeout: b"[" * 60000 + b"\n"
        # Python 3.9 to 3.12 raise RecursionError reading this; a newer one may not. Either way it must never be
        # read: the reader is made to fail the way the old ones do, and must not even be reached.
        with mock.patch("json.loads", side_effect=RecursionError("maximum recursion depth exceeded")) as loads:
            with self.assertRaises(ndi.NdiError):
                c.request({"cmd": "status"})
            self.assertEqual(loads.call_count, 0)
            self.assertEqual(c.status(), {"ok": False})
            c._exchange = lambda message, timeout: b'{"ok": true}\n'      # and should the reader fail on a plain reply
            with self.assertRaises(ndi.NdiError):
                c.request({"cmd": "status"})
        for raw in (b'{"a": ' * 65 + b"1" + b"}" * 65, b"]" * 9 + b"[" * 70, b"", b"\xff\xfe", b"[]", b'"x"', b"nul"):
            c._exchange = lambda message, timeout, raw=raw: raw
            with self.assertRaises(ndi.NdiError, msg=raw[:20]):
                c.request({"cmd": "status"})
        c._exchange = lambda message, timeout: json.dumps({"ok": True, "a": {"b": {"c": [[1]]}}}).encode()
        self.assertEqual(c.request({"cmd": "status"})["a"], {"b": {"c": [[1]]}})

    def test_no_answer_and_no_fault_stops_the_panel_starting(self):
        import inspect
        from pvj import server

        class Bad:
            def request(self, message, timeout=None):
                raise RecursionError("from a reply")

            def status(self):
                return {"ok": False}
        logged = []
        i = ndi.Input(Bad(), "x", lambda: (True, []), log=logged.append)
        self.assertEqual(i.sync(), {"ok": False})
        self.assertEqual(len(logged), 1)
        i = ndi.Input(Bad(), "x", lambda: [].get("ndi"), log=logged.append)          # the settings themselves are odd
        self.assertEqual(i.sync(), {"ok": False})
        src = inspect.getsource(server.build)
        self.assertRegex(src, r"try:[^\n]*\n\s+api\.ndi\.sync\(\)\n\s+except Exception")


class InterlacedTest(unittest.TestCase):
    """Review finding 2: a frame of two woven fields was let through and would have shown combed."""

    def test_only_a_progressive_frame_is_shown_and_each_interlaced_kind_is_refused_in_the_same_words(self):
        self.assertEqual((ndi.PROGRESSIVE, ndi.INTERLACED), (1, (0, 2, 3)))
        self.assertEqual(ndi.check_frame(frame(64, 16, fields=1))[0], (64, 16, 29.97))
        said = set()
        for value in (0, 2, 3):
            f = frame(64, 16, fields=value)
            with self.assertRaises(ndi.NdiError, msg=value) as e:
                ndi.check_frame(f)
            said.add(str(e.exception))
            self.assertEqual(f.token, [], value)                         # refused before its memory is read
        self.assertEqual(said, {"the source sends interlaced video, which this input does not show; set the sender to progressive"})
        for value in (4, -1, 100, 0x7fffffff):
            with self.assertRaises(ndi.NdiError) as e:
                ndi.check_frame(frame(64, 16, fields=value))
            self.assertNotIn("interlaced", str(e.exception))

    def test_a_woven_frame_ends_the_receiver_with_that_reason(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        lib = FakeLib()
        s = ndi.Service(d, "x", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        lib.frames.put(frame(1920, 1080, fields=0))
        reply = s.handle({"cmd": "open", "id": s.handle({"cmd": "status"})["sources"][0]["id"]})
        self.assertEqual(reply["ok"], False)
        self.assertIn("interlaced video", reply["error"])

    def test_what_the_library_is_asked_for_is_fastest_full_bandwidth_with_fields_allowed(self):
        spec, keep = ndi.CtypesLibrary.recv_spec((b"RESOLUME (Output)", b"192.168.0.20:5961"))
        self.assertEqual((spec.color, spec.bandwidth, spec.fields), (100, 100, True))
        self.assertIs(ndi.ALLOW_FIELDS, True)                            # "fastest" implies it; a False here would be a lie
        self.assertEqual(ctypes.string_at(spec.source.name), b"RESOLUME (Output)")
        self.assertEqual(ctypes.string_at(spec.source.url), b"192.168.0.20:5961")
        self.assertEqual(spec.name, b"nxlx.mastercontrol")
        self.assertEqual(len(keep), 3)                                   # the strings outlive the call that made them


class SavedSettingsTest(unittest.TestCase):
    """Review finding 3: a settings file edited by hand must not stop the panel, and a refusal is not "not running"."""

    def test_a_section_of_the_wrong_kind_gives_no_addresses_and_one_log_line(self):
        logged = []
        data = {"ndi": []}
        w = ndi.Wanted(lambda: True, lambda: data, log=logged.append)
        for bad in ([], "10.0.0.1", 5, {"addresses": "10.0.0.1"}, {"addresses": None}, True):
            data["ndi"] = bad
            self.assertEqual(w(), (True, []), bad)
            self.assertEqual(w.problem, "the saved NDI addresses could not be read and are not used")
        self.assertEqual(len(logged), 1)                                 # said once, not at every poll
        del data["ndi"]                                                  # a file from before schema 14, mid-migration
        self.assertEqual((w(), w.problem), ((True, []), ""))
        w = ndi.Wanted(lambda: 1 / 0, lambda: data, log=logged.append)
        self.assertEqual(w(), (False, []))
        w = ndi.Wanted(lambda: True, lambda: None, log=logged.append)
        self.assertEqual(w(), (True, []))

    def test_a_bad_entry_in_a_good_list_is_left_out_and_said(self):
        self.assertEqual(ndi.saved_addresses({"addresses": ["10.0.0.1", "8.8.8.8", "10.0.0.1", "10.0.0.2\n", 7, "192.168.1.20"]}),
                         (["10.0.0.1", "192.168.1.20"], "4 saved NDI addresses could not be used and were left out"))
        self.assertEqual(ndi.saved_addresses({"addresses": ["10.0.0.1", "x"]})[1], "1 saved NDI address could not be used and was left out")
        self.assertEqual(ndi.saved_addresses({"addresses": ["10.0.0.%d" % n for n in range(1, 30)]})[0], ["10.0.0.%d" % n for n in range(1, 17)])
        self.assertEqual(ndi.saved_addresses({"addresses": ["10.0.0.1"]}), (["10.0.0.1"], ""))

    def page(self, section, lib=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        lib = lib or FakeLib()
        s = ndi.Service(d, "x", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None)
        self.addCleanup(s.close)
        client = FakeClient(s)
        i = ndi.Input(client, s.fifo, ndi.Wanted(lambda: True, lambda: {"ndi": section}, log=lambda *_: None), log=lambda *_: None)
        return i, client, lib

    def test_the_page_works_with_a_wrong_section_and_says_what_was_left_out(self):
        i, client, lib = self.page("oops")
        st = i.status()
        self.assertEqual((st["helper"], st["addresses"], st["notice"]), (True, [], "the saved NDI addresses could not be read and are not used"))
        i, client, lib = self.page({"addresses": ["192.168.1.20", "8.8.8.8"]})
        st = i.status()
        self.assertEqual((st["helper"], st["addresses"], lib.finders), (True, ["192.168.1.20"], ["192.168.1.20"]))
        self.assertIn("1 saved NDI address could not be used", st["notice"])
        self.assertEqual(len(st["sources"]), 1)                          # and the sources are still found

    def test_a_helper_that_refuses_the_settings_is_running_and_its_reason_is_on_the_page(self):
        i, client, lib = self.page({"addresses": []})
        client.answer = {"ok": False, "error": "bad or repeated NDI address"}
        st = i.status()                                                  # status says not ok; so does the configure
        self.assertEqual(i.sync(), {"ok": False, "answered": True, "error": "bad or repeated NDI address"})
        client.answer = None
        real = client.request

        def refuse(message, timeout=None):
            return {"ok": False, "error": "bad or repeated NDI address"} if message["cmd"] == "configure" else real(message, timeout)
        client.request = refuse
        st = i.status()
        self.assertEqual((st["helper"], st["notice"]), (True, "bad or repeated NDI address"))
        client.down = True
        client.request = real
        self.assertEqual((i.status()["helper"], i.status()["notice"]), (False, ""))


class SourceListServiceTest(unittest.TestCase):
    """Review finding 4, through the helper: the list says when it was cut, and a play stays with its machine."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FakeLib([(b"RESOLUME (Output)", b"192.168.0.20:5961")])
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(self.s.close)
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")

    def test_the_status_says_when_the_list_was_cut_and_keeps_the_added_address_and_the_playing_source(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": ["192.168.0.21"]})
        self.assertIs(self.s.handle({"cmd": "status"})["cut"], False)
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        self.lib.raw = [(b"!!! %04d" % n, b"10.9.9.%d:1" % (n % 250 + 1)) for n in range(300)] + self.lib.raw + [(b"zz Mad", b"192.168.0.21:5961")]
        self.s._sources_at = None
        st = self.s.handle({"cmd": "status"})
        names = [x["name"] for x in st["sources"]]
        self.assertEqual((len(names), st["cut"]), (ndi.MAX_SOURCES, True))
        self.assertIn("RESOLUME (Output)", names)
        self.assertIn("zz Mad", names)

    def test_another_machine_announcing_the_chosen_name_is_not_what_gets_opened(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.raw = [(b"RESOLUME (Output)", b"192.168.0.66:5961")] + self.lib.raw      # the impostor is listed first
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        self.assertEqual(self.lib.opened, [(b"RESOLUME (Output)", b"192.168.0.20:5961")])
        self.lib.raw = [(b"RESOLUME (Output)", b"192.168.0.66:5961")]                     # the real one quit; the impostor stays
        reply = self.s.handle({"cmd": "open", "id": self.rid})                            # a replay, or a saved choice
        self.assertEqual((reply["ok"], reply["error"]), (False, "that source is not on the network now"))
        self.assertEqual(len(self.lib.opened), 1)


class RuntimeInstallHardeningTest(unittest.TestCase):
    """Review finding 5: root's copy of the library, from a name somebody else may control."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.dest = os.path.join(self.dir, "opt")
        self.good = os.path.join(self.dir, "real.so")
        with open(self.good, "wb") as f:
            f.write(elf(183) + b"payload")

    def test_a_link_given_as_the_file_is_not_followed(self):
        link = os.path.join(self.dir, "libndi.so.6")
        os.symlink(self.good, link)
        with self.assertRaises(ndi.NdiError) as e:
            ndi.install_runtime(link, self.dest, machine=183, chown=False)
        self.assertIn("cannot be read as a file", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.dest, ndi.LIB_NAME)))

    def test_a_pipe_given_as_the_file_is_refused_at_once(self):
        pipe = os.path.join(self.dir, "pipe")
        os.mkfifo(pipe)
        done = []
        t = threading.Thread(target=lambda: done.append(self.assertRaises(ndi.NdiError, ndi.install_runtime, pipe, self.dest, 183, False)), daemon=True)
        t.start()
        t.join(3)
        self.assertFalse(t.is_alive(), "root would have waited for ever for somebody to write to the pipe")
        self.assertEqual(len(done), 1)
        self.assertFalse(os.path.exists(self.dest) and os.listdir(self.dest))

    def test_a_file_that_grows_while_it_is_read_is_cut_off_at_the_bound(self):
        from unittest import mock
        real = os.read
        fed = []

        def endless(fd, n):
            chunk = real(fd, n)
            if not chunk and sum(fed) < 3 * 1024:                        # somebody appends as fast as root reads
                chunk = b"x" * min(n, 512)
            fed.append(len(chunk))
            return chunk
        with mock.patch.object(ndi, "LIB_MAX_BYTES", 1024), mock.patch("os.read", endless):
            with self.assertRaises(ndi.NdiError) as e:
                ndi.install_runtime(self.good, self.dest, machine=183, chown=False)
        self.assertIn("too large", str(e.exception))
        self.assertLessEqual(sum(fed), 1024 + 1)                         # never more than the bound and one byte is read
        self.assertEqual(os.listdir(self.dest), [])

    def test_the_copy_is_private_until_every_check_has_passed(self):
        from unittest import mock
        seen = []
        real = ndi.runtime_problem

        def spy(path, *a, **kw):
            seen.append(os.stat(path).st_mode & 0o777)
            return real(path, *a, **kw)
        with mock.patch.object(ndi, "runtime_problem", spy):
            path = ndi.install_runtime(self.good, self.dest, machine=183, chown=False)
        self.assertEqual(seen, [0o600])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        with open(path, "rb") as f:
            self.assertTrue(f.read().endswith(b"payload"))


class ReplySizeTest(unittest.TestCase):
    """Review finding 6: 64 sources with long names made an answer of 96,799 bytes, the client reads 65,536, and the
    helper looked dead."""

    def test_the_largest_status_there_can_be_fits_what_the_client_reads(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        worst = []
        for kind, char, count in (("emoji", "\U0001f600", 64), ("accent", "\u00e9", 128), ("ascii", "\\", 128)):
            for n in range(200):
                name = ("%03d" % n) + char * (count - 3)
                self.assertIsNotNone(ndi.clean_name(name), kind)
                worst.append((name.encode(), ("h" * 85 + "%03d.local:65535" % n).encode()[-ndi.MAX_WHERE:]))
        lib = FakeLib(worst)
        s = ndi.Service(d, "/opt/pvj-ndi/libndi.so.6", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "addresses": ["192.168.100.%d" % n for n in range(100, 116)]})
        st = s.handle({"cmd": "status"})
        self.assertEqual((len(st["sources"]), st["cut"]), (ndi.MAX_SOURCES, True))
        lib.frames.put(frame(3840, 2160))
        self.assertTrue(s.handle({"cmd": "open", "id": st["sources"][0]["id"]})["ok"])
        s.receiver.message = "m" * 400
        s.lib_error = "e" * 400
        sizes = [len(json.dumps(s.handle({"cmd": "status"})).encode()) + 1]
        for char in ("\U0001f600", "\u00e9"):                            # every row of the worst kind at once
            lib.raw = [(("%03d" % n + char * (64 if char > "\uffff" else 128))[:64 if char > "\uffff" else 128].encode(),
                        ("h" * 85 + "%03d.local:65535" % n).encode()[-ndi.MAX_WHERE:]) for n in range(200)]
            s._sources_at = None
            sizes.append(len(json.dumps(s.handle({"cmd": "status"})).encode()) + 1)
        self.assertLess(max(sizes), ndi.REPLY_LIMIT, sizes)
        self.assertGreater(max(sizes), 40000)                            # and the test did build a large one
        from pvj import netd
        import inspect
        self.assertIn("len(data) < %d" % ndi.REPLY_LIMIT, inspect.getsource(netd.exchange))      # the limit this is measured against

    def test_a_name_longer_than_that_on_the_wire_is_dropped_like_any_other_bad_name(self):
        self.assertIsNone(ndi.clean_name("\U0001f600" * 65))
        self.assertIsNotNone(ndi.clean_name("\U0001f600" * 64))
        self.assertIsNotNone(ndi.clean_name("\u00e9" * 128))
        self.assertIsNone(ndi.clean_name('"' * 128 + "x"))
        out, _ = ndi.clean_sources([(b"cam", b"h" * 101), (b"cam", b"h" * 100)])
        self.assertEqual([len(x["from"]) for x in out], [100])


class LostConnectionTest(unittest.TestCase):
    """Review finding 7: a library that answers "lost" at once, again and again, made the capture thread spin."""

    def test_lost_for_ever_costs_a_few_calls_a_second_and_one_log_line_and_coming_back_is_one_more(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        calls, back = [], threading.Event()

        class Lost(FakeLib):
            def recv_capture(self, handle, timeout_ms):
                calls.append(1)
                if back.is_set():
                    time.sleep(0.02)
                    return frame(64, 16)
                return ndi.Frame("lost")                                 # at once, never waiting
        lib, logged = Lost(), []
        r = ndi.Receiver(lib, ndi.clean_sources(lib.raw)[0][0], os.path.join(d, "f"), log=logged.append, pipe_wait=1e9)
        self.addCleanup(r.close)
        r.start()
        time.sleep(1.0)
        self.assertLessEqual(len(calls), 8, "the capture thread span")
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(logged, ["pvj-ndi: the connection to RESOLUME (Output) dropped; waiting for it"])
        back.set()
        self.assertTrue(r.first.wait(3))
        self.assertTrue(wait(lambda: len(logged) == 2))
        self.assertEqual(logged[1], "pvj-ndi: RESOLUME (Output) is back")
        started = time.monotonic()
        r.close()
        self.assertLess(time.monotonic() - started, 1.5)


class LaterChoiceWinsTest(NdiApiTest):
    """Review finding 8: between "nothing else was played" and loading the pipe there was a gap a clip could fall
    into. The order is forced here: the pipe is being loaded when the clip is played."""

    def test_a_clip_played_while_the_pipe_is_being_loaded_waits_and_then_has_the_screen(self):
        self.enable()
        order, loading, go = [], threading.Event(), threading.Event()

        def play_pipe(*args):
            order.append("pipe starts")
            loading.set()
            go.wait(5)
            order.append("pipe loaded")
        real_play = self.player.play

        def play(paths, *a, **kw):
            order.append("clip loaded")
            return real_play(paths, *a, **kw)
        play.__name__ = "play"
        self.player.play_pipe, self.player.play = play_pipe, play
        self.lib.frames.put(frame(64, 16))
        out = []
        ndi_play = threading.Thread(target=lambda: out.append(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)))
        ndi_play.start()
        self.assertTrue(loading.wait(5))
        clip = threading.Thread(target=lambda: out.append(self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.full)))
        clip.start()
        time.sleep(0.3)                                                  # the clip's request is in; it must be waiting
        self.assertEqual(order, ["pipe starts"])
        go.set()
        ndi_play.join(5)
        clip.join(5)
        self.assertEqual(order, ["pipe starts", "pipe loaded", "clip loaded"])       # one after the other, the later one last
        self.assertEqual(sorted(o[0] for o in out), [200, 200])
        self.assertTrue(wait(lambda: self.api.ndi.current is None and self.service.receiver is None))
        self.assertEqual(self.lib.closed, 1)                             # and the source was let go

    def test_everything_that_loads_or_clears_the_player_cancels_a_source_still_connecting(self):
        self.enable()
        def clear():
            return None

        def play_pipe(*args):
            return None
        for fn, args in ((self.player.play, ([os.path.join(self.media, "a.mp4")],)), (clear, ()), (play_pipe, ("/x", 16, 16, 30))):
            t = self.api.ndi.ticket()
            self.api._player_call(fn, *args)
            self.assertFalse(self.api.ndi.still(t), fn.__name__)
        from pvj.player import Player
        for name in self.api.PLAYER_LOADS:                               # the names are the real player's
            self.assertEqual(getattr(Player, name).__name__, name)
        t = self.api.ndi.ticket()
        self.api._player_call(self.player.status)                        # a question is not a load
        self.assertTrue(self.api.ndi.still(t))
        self.assertEqual(self.api.PLAYER_LOADS, ("play", "play_pipe", "clear"))
        t = self.api.ndi.ticket()
        self.api.ndi.stop()                                              # and so does a stop, with nothing playing
        self.assertFalse(self.api.ndi.still(t))


for _name in [n for n in dir(NdiApiTest) if n.startswith("test_") and n not in LaterChoiceWinsTest.__dict__]:
    setattr(LaterChoiceWinsTest, _name, None)                            # the set-up is shared, the tests are not run twice


class WatchTest(unittest.TestCase):
    """Review finding 9, and the still source the real library showed: what the once-a-second watch does."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FakeLib()
        self.now = [1000.0]
        self.s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(self.s.close)
        self.logged = []
        self.i = ndi.Input(FakeClient(self.s), self.s.fifo, lambda: (True, []), log=self.logged.append, clock=lambda: self.now[0])
        self.rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")
        self.i.status()

    def test_a_source_that_sends_one_frame_and_then_nothing_for_an_hour_is_left_alone(self):
        # NDI Tools' Test Patterns, against the real library: one picture, then only its tone.
        clock = [0.0]
        self.s._receiver = lambda lib, source, fifo, log: ndi.Receiver(lib, source, fifo, log=log, clock=lambda: clock[0])
        self.lib.frames.put(frame(1920, 1080))
        self.i.open(self.rid)
        self.i.current = {"id": self.rid, "name": "RESOLUME (Output)"}
        fd = os.open(self.s.fifo, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.assertTrue(wait(lambda: self.s.receiver.status()["state"] == "playing"))
        opened = len(self.lib.opened)
        for seconds in (1, 3, 60, 3600):
            clock[0] = seconds
            self.now[0] += seconds
            self.assertFalse(self.i.tick(lambda sid: self.fail("a still picture was loaded again")), seconds)
            st = self.i.status()["playing"]
            self.assertEqual(st["state"], "playing" if seconds < ndi.QUIET_SECONDS else "still", seconds)
        self.assertEqual((len(self.lib.opened), self.lib.closed, self.i.current["id"]), (opened, 0, self.rid))
        self.assertIsNone(self.i.status()["ended"])
        self.assertEqual(self.logged, [])
        self.assertIn("still", ndi.STATES)

    def test_a_source_that_is_gone_is_tried_less_and_less_often_and_logged_once(self):
        self.i.current = {"id": self.rid, "name": "RESOLUME (Output)"}     # the helper has nothing open and the sender is off
        tries = []

        def replay(sid):
            tries.append(self.now[0])
            raise ndi.NdiError("that source is not on the network now")
        start = self.now[0]
        for _ in range(24 * 3600):                                       # a day of ticks, one a second
            self.now[0] += 1.0
            self.i.tick(replay)
        gaps = [round(b - a) for a, b in zip(tries, tries[1:])]
        self.assertEqual(gaps[:6], [3, 6, 12, 24, 48, 60])
        self.assertEqual(set(gaps[5:]), {60})
        self.assertLess(len(tries), 1500)                                # it was 28,800 a day
        self.assertEqual(self.logged, ["pvj-web: NDI source RESOLUME (Output) not shown again yet: that source is not on the network now"])
        self.assertEqual(self.i.current["id"], self.rid)                 # still wanted: it comes back when the sender does

        def other(sid):
            raise ndi.NdiError("the NDI helper (pvj-ndi) is not running")
        self.now[0] += 61
        self.i.tick(other)
        self.assertEqual(len(self.logged), 2)                            # a new reason is a new line
        self.assertGreater(self.now[0] - start, 0)

    def test_after_it_is_back_the_next_trouble_starts_from_three_seconds_again(self):
        self.i.current = {"id": self.rid, "name": "RESOLUME (Output)"}
        fails = [3]

        def replay(sid):
            if fails[0]:
                fails[0] -= 1
                raise ndi.NdiError("that source is not on the network now")
            self.lib.frames.put(frame(64, 16))
            self.i.open(sid)
        for _ in range(40):
            self.now[0] += 1.0
            self.i.tick(replay)
        self.assertEqual((self.i._retry_wait, self.i._retry_said), (ndi.Input.RETRY_SECONDS, ""))
        self.assertEqual(self.s.receiver.status()["state"], "ready")

    def test_a_source_that_ended_by_itself_is_no_longer_what_plays_and_the_page_keeps_why(self):
        self.lib.frames.put(frame(64, 16))
        self.i.open(self.rid)
        self.i.current = {"id": self.rid, "name": "RESOLUME (Output)"}
        self.lib.frames.put(frame(64, 16, fourcc=0x41524742))
        self.assertTrue(wait(lambda: self.s.receiver.state == "refused"))
        self.now[0] += 5
        self.assertFalse(self.i.tick(lambda sid: self.fail("no replay")))
        st = self.i.status()
        self.assertEqual((self.i.current, st["playing"], st["ended"]["name"]), (None, None, "RESOLUME (Output)"))
        self.assertIn("BGRA", st["ended"]["message"])
        self.lib.frames.put(frame(64, 16))
        self.i.open(self.rid)                                            # played again: the old reason goes
        self.assertIsNone(self.i.ended)


class OffMeansNoNdiCodeTest(unittest.TestCase):
    """Review finding 10: switched off after being on, the helper ends so that the library is no longer loaded."""

    def test_switching_off_with_the_library_loaded_asks_for_a_clean_start_and_only_then(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        lib, asked, logged = FakeLib(), [], []
        s = ndi.Service(d, "x", loader=lambda path: lib, problem=lambda path: None, log=logged.append)
        self.addCleanup(s.close)
        s.on_unload = lambda: asked.append(1)
        s.handle({"cmd": "configure", "on": False, "addresses": []})     # never on: nothing is loaded, nothing to end
        self.assertEqual(asked, [])
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        s.handle({"cmd": "configure", "on": True, "addresses": ["10.0.0.1"]})
        self.assertEqual(asked, [])
        reply = s.handle({"cmd": "configure", "on": False, "addresses": []})
        self.assertEqual((asked, reply["ok"], reply["on"], lib.closed_finders), ([1], True, False, 2))     # the answer still goes out
        self.assertIn("pvj-ndi: the NDI input was switched off; starting again without the NDI runtime", logged)

    def test_the_helper_as_started_ends_itself_and_answers_only_the_panels_account(self):
        import inspect
        src = inspect.getsource(ndi.main)
        self.assertIn("service.on_unload = lambda: threading.Timer(0.5, os._exit, [0]).start()", src)
        # who may ask: the panel's account, and nobody else (not root, not the player, which is in the folder's group)
        self.assertIn('allowed.add(pwd.getpwnam("pvj-web").pw_uid)', src)
        self.assertIn("lambda uid: uid in allowed", src)
        self.assertNotIn("allowed = {0}", src)
        from unittest import mock
        made = {}

        class Server:
            def __init__(self, path, service, is_allowed):
                made.update(path=path, service=service, is_allowed=is_allowed)

            def serve_forever(self):
                raise KeyboardInterrupt
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, paths.NDI_FIFO), "w"):
            pass                                                         # a pipe left by a run that was killed
        import pwd
        web = mock.Mock(pw_uid=4242)
        with mock.patch.dict(os.environ, {"PVJ_NDI_DIR": d}), mock.patch("pvj.netd.NetServer", Server), \
                mock.patch.object(pwd, "getpwnam", lambda name: web if name == "pvj-web" else (_ for _ in ()).throw(KeyError(name))), \
                mock.patch("builtins.print"):
            self.assertEqual(ndi.main([]), 0)
        self.assertEqual(made["path"], os.path.join(d, paths.NDI_SOCKET))
        self.assertEqual([made["is_allowed"](uid) for uid in (4242, 0, os.getuid(), 4243, 65534)],
                         [True, False, os.getuid() == 4242, False, False])
        self.assertFalse(os.path.exists(os.path.join(d, paths.NDI_FIFO)))
        self.assertIsNotNone(made["service"].on_unload)


class PipeCheckTest(NdiApiTest):
    """Review finding 11: the player is only told to read a pipe that is a pipe and the helper's own."""

    def swap(self, how):
        real = self.client.request

        def request(message, timeout=None):
            reply = real(message, timeout)
            if message.get("cmd") == "open" and reply.get("ok"):
                how()
            return reply
        self.client.request = request

    def refused(self, why):
        self.lib.frames.put(frame(64, 16))
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body["error"]), (409, why))
        self.assertFalse([c for c in self.player.calls if c[0] == "play_pipe"])
        self.assertEqual((self.api.ndi.current, self.service.receiver), (None, None))

    def test_a_file_in_the_pipes_place_is_not_loaded(self):
        self.enable()

        def to_file():
            os.unlink(self.service.fifo)
            with open(self.service.fifo, "w") as f:
                f.write("not video")
        self.swap(to_file)
        self.refused("the NDI helper's pipe is not what it should be; not loading it")

    def test_a_link_in_the_pipes_place_is_not_loaded_even_to_another_pipe(self):
        self.enable()
        other = os.path.join(self.ndidir, "other.fifo")
        os.mkfifo(other)

        def to_link():
            os.unlink(self.service.fifo)
            os.symlink(other, self.service.fifo)
        self.swap(to_link)
        self.refused("the NDI helper's pipe is not what it should be; not loading it")

    def test_a_pipe_that_is_somebody_elses_or_gone_is_not_loaded(self):
        self.enable()
        self.api.ndi._pipe_owner = lambda: os.getuid() + 1
        self.refused("the NDI helper's pipe is not what it should be; not loading it")
        self.api.ndi._pipe_owner = os.getuid
        self.swap(lambda: os.unlink(self.service.fifo))
        self.refused("the NDI helper's pipe is not there; not loading it")

    def test_the_panel_as_started_expects_the_helpers_account(self):
        import inspect
        from unittest import mock
        import pwd
        from pvj import server
        self.assertIn("pipe_owner=ndi_mod.helper_uid", inspect.getsource(server.build))
        with mock.patch.object(pwd, "getpwnam", lambda name: mock.Mock(pw_uid=777) if name == "pvj-ndi" else None):
            self.assertEqual(ndi.helper_uid(), 777)

        def none(name):
            raise KeyError(name)
        with mock.patch.object(pwd, "getpwnam", none):
            self.assertEqual(ndi.helper_uid(), os.getuid())


for _name in [n for n in dir(NdiApiTest) if n.startswith("test_") and n not in PipeCheckTest.__dict__]:
    setattr(PipeCheckTest, _name, None)


class LifetimeTest(unittest.TestCase):
    """Review finding 12: who removes the pipe, and never freeing a connection the library is still inside."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_capture_that_will_not_come_back_leaves_its_connection_open_and_says_so(self):
        inside, let_go = threading.Event(), threading.Event()

        class Stuck(FakeLib):
            def recv_capture(self, handle, timeout_ms):
                inside.set()
                let_go.wait(10)                                          # the library, not returning
                return None
        lib, logged = Stuck(), []
        fifo = os.path.join(self.dir, "f")
        os.mkfifo(fifo)
        r = ndi.Receiver(lib, ndi.clean_sources(lib.raw)[0][0], fifo, log=logged.append, join_wait=0.3)
        r.start()
        self.assertTrue(inside.wait(3))
        started = time.monotonic()
        r.close()
        self.assertLess(time.monotonic() - started, 2.0)                 # it does not wait for ever either
        self.assertEqual(lib.closed, 0, "the connection was freed while the library was still using it")
        self.assertEqual(len([m for m in logged if "left open, not freed under it" in m]), 1)
        self.assertEqual(r.status()["state"], "stopped")
        let_go.set()                                                     # it comes back after all: now it is given back, once
        self.assertTrue(wait(lambda: lib.closed == 1))
        r.close()
        self.assertEqual(lib.closed, 1)

    def test_only_the_service_removes_the_pipe_and_it_wakes_a_reader_that_was_waiting(self):
        lib = FakeLib()
        s = ndi.Service(self.dir, "x", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None, first_frame=0.3)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        lib.frames.put(frame(64, 16))
        self.assertTrue(s.handle({"cmd": "open", "id": s.handle({"cmd": "status"})["sources"][0]["id"]})["ok"])
        got = []

        def reader():                                                    # a player that opened the pipe and waits for a first byte
            fd = os.open(s.fifo, os.O_RDONLY)
            try:
                got.append(os.read(fd, 16))
            finally:
                os.close(fd)
        s.receiver._end("stopped", "test")                               # the receiver ended before it wrote anything
        self.assertTrue(wait(lambda: lib.closed == 1))
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertTrue(os.path.exists(s.fifo))                          # the receiver ending did not remove it
        self.assertEqual(s.handle({"cmd": "close"}), {"ok": True})
        t.join(3)
        self.assertFalse(t.is_alive(), "the reader was left waiting on a pipe nobody will write")
        self.assertFalse(os.path.exists(s.fifo))

    def test_a_pipe_that_cannot_be_made_is_an_answer_with_the_reason(self):
        lib, logged = FakeLib(), []
        s = ndi.Service(os.path.join(self.dir, "gone"), "x", loader=lambda path: lib, problem=lambda path: None, log=logged.append)
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        reply = s.handle({"cmd": "open", "id": s.handle({"cmd": "status"})["sources"][0]["id"]})
        self.assertEqual(reply["ok"], False)
        self.assertIn("could not make its pipe", reply["error"])
        self.assertEqual((lib.opened, s.receiver), ([], None))
        self.assertEqual(len([m for m in logged if "could not make the pipe" in m]), 1)
        os.makedirs(os.path.join(self.dir, "gone"))
        os.mkfifo(s.fifo)                                                # one is in the way: also an answer, never an exception
        with open(os.path.join(self.dir, "gone", "x"), "w"):
            pass
        os.unlink(s.fifo)
        os.mkdir(s.fifo)
        self.assertEqual(s.handle({"cmd": "open", "id": s.handle({"cmd": "status"})["sources"][0]["id"]})["ok"], False)

    def test_status_reads_the_receiver_under_its_lock_and_never_waits_behind_an_open(self):
        import inspect
        src = inspect.getsource(ndi.Service.status)
        self.assertIn("with self._recv_lock:\n            r = self.receiver", src)
        lib = FakeLib()
        s = ndi.Service(self.dir, "x", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None, first_frame=1.5)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        sid = s.handle({"cmd": "status"})["sources"][0]["id"]
        t = threading.Thread(target=s.handle, args=({"cmd": "open", "id": sid},), daemon=True)     # no frame comes: it waits 1.5 s
        t.start()
        time.sleep(0.2)
        started = time.monotonic()
        for _ in range(20):
            self.assertEqual(s.handle({"cmd": "status"})["ok"], True)
        self.assertLess(time.monotonic() - started, 0.5)
        t.join(5)


class LookAlikeNamesTest(unittest.TestCase):
    """Review finding 13: two rows must not look alike, and the helper's words cannot change how a line reads."""

    def test_one_way_of_writing_a_name_so_two_spellings_are_one_source(self):
        self.assertEqual(ndi.clean_name("Cafe\u0301 (Out)"), "Caf\u00e9 (Out)")              # e + accent, and the one letter
        self.assertEqual(ndi.clean_name("Caf\u00e9 (Out)"), "Caf\u00e9 (Out)")
        for spaced in ("RESOLUME\u00a0(Output)", "RESOLUME\u2009(Output)", "RESOLUME\u3000(Output)", "RESOLUME  (Output)", "RESOLUME \u00a0 (Output)"):
            self.assertEqual(ndi.clean_name(spaced), "RESOLUME (Output)", repr(spaced))
        rows = [("RESOLUME (Output)".encode(), b"192.168.0.20:5961"), ("RESOLUME\u00a0(Output)".encode(), b"192.168.0.20:5962"),
                ("Cafe\u0301".encode(), b"192.168.0.21:1"), ("Caf\u00e9".encode(), b"192.168.0.21:2")]
        out, _ = ndi.clean_sources(rows)
        self.assertEqual([x["name"] for x in out], ["Caf\u00e9", "RESOLUME (Output)"])         # one row each, not two that look the same
        self.assertEqual(ndi.clean_name(ndi.clean_name("Cafe\u0301  x\u00a0y")), ndi.clean_name("Cafe\u0301  x\u00a0y"))    # and doing it twice changes nothing

    def test_names_made_to_mislead_are_dropped(self):
        self.assertIsNone(ndi.clean_name("a" + "\u0301" * 5))
        self.assertIsNone(ndi.clean_name("x\u0300\u0301\u0302"))
        self.assertIsNone(ndi.clean_name("\u00a0cam"))
        self.assertIsNone(ndi.clean_name("cam\u2003"))
        self.assertIsNone(ndi.clean_name("a" * 600))
        self.assertIsNotNone(ndi.clean_name("x\u0323\u0301"))                                 # two marks on a letter is ordinary (Vietnamese)
        self.assertEqual(len(ndi.clean_name("e\u0301" * 128)), 128)                           # 256 written, 128 letters

    def test_words_from_the_helper_are_scrubbed_and_then_cut(self):
        self.assertEqual(ndi._text("the source\n\x1b[31m sent\u202e a frame\u200b\u2028 that"), "the source[31m sent a frame that")
        self.assertEqual(ndi._text("\u202e" * 300 + "x" * 300), "x" * 200)                    # scrubbed first: the cut cannot hide what follows
        self.assertEqual(ndi._text(None), "")
        self.assertEqual(ndi._text(5), "")
        self.assertEqual(ndi._text("v" * 500, 80), "v" * 80)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        s = ndi.Service(d, "x", loader=lambda path: FakeLib(), problem=lambda path: None, log=lambda *_: None)
        client = FakeClient(s)
        rid = ndi.source_id("RESOLUME (Output)", "192.168.0.20")
        client.answer = {"ok": True, "configured": True, "on": True, "addresses": [],
                         "runtime": {"present": True, "loaded": True, "version": "6.3\u202e.2\n", "problem": "bad\x07\u2066 thing"},
                         "sources": [], "playing": {"id": rid, "name": "x", "state": "playing", "message": "line\r\none\u202etwo"}}
        i = ndi.Input(client, s.fifo, lambda: (True, []), log=lambda *_: None)
        i.current = {"id": rid, "name": "x"}
        st = i.status()
        self.assertEqual((st["runtime"]["version"], st["runtime"]["problem"], st["playing"]["message"]), ("6.3.2", "bad thing", "lineonetwo"))


if __name__ == "__main__":
    unittest.main()
