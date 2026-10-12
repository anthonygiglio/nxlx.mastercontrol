# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI input above its seam, against a fake library and fake frames (D62). The real library was never loaded:
nothing here says that NDI works, only that what this project wrote does what it says with what it is given."""
import ctypes
import io
import json
import os
import queue
import shutil
import stat
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

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

    def find_open(self, extra, groups=""):
        self.finders.append(extra)
        self.groups = getattr(self, "groups", []) + [groups]
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
            return self.frames.get(timeout=0.1)
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

    def test_a_still_picture_is_written_once_and_never_again(self):
        """A frame that nothing follows used to be written a second time, on a guess about mpv that the Pi 4 then
        showed to be wrong (2026-10-08: a still source's one frame was on the monitor without it). Taken out again."""
        self.r.start()
        self.lib.frames.put(frame(64, 16, fill=7))
        self.read()
        self.assertTrue(wait(lambda: len(self.got) == 2048))
        self.now[0] += 3600
        time.sleep(0.5)
        self.assertEqual(bytes(self.got), b"\x07" * 2048)
        st = self.r.status()
        self.assertEqual((st["state"], st["counts"]["shown"]), ("still", 1))
        self.assertNotIn("repeated", st["counts"])
        self.assertFalse(hasattr(ndi, "REPEAT_AFTER"))

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

    def test_roots_pipe_is_written_by_a_helper_that_is_not_root(self):
        """Found on the owner's Pi, 2026-10-12: on a box the pipe is root's (0660 root:pvj-ndi) and the helper runs
        as pvj-ndi, and the helper took only a pipe of its own, so every source ended "the input pipe was replaced"."""
        real = os.fstat

        class Owned:                                                     # the same pipe, said to be somebody's
            def __init__(self, st, uid):
                self.st_mode, self.st_uid = st.st_mode, uid
        helper = 996 if os.getuid() != 996 else 997
        for owner, taken in ((0, True), (helper, True), (helper + 1, False)):
            with mock.patch.object(ndi.os, "getuid", lambda: helper), \
                    mock.patch.object(ndi.os, "fstat", lambda fd, owner=owner: Owned(real(fd), owner)):
                r = ndi.Receiver(self.lib, self.source, self.fifo, log=lambda *_: None, pipe_wait=5)
                r.start()
                self.lib.frames.put(frame(64, 16))
                self.got = bytearray()
                self.read()
                if taken:
                    self.assertTrue(wait(lambda: len(self.got) == 2048), "a pipe owned by %d was not written" % owner)
                else:
                    self.assertTrue(wait(lambda: r.status()["state"] == "stopped"))
                    self.assertIn("pipe was replaced", r.status()["message"])
                r.close()
                self.reader.join(2)

    def test_what_the_helper_may_write_is_a_pipe_of_roots_or_its_own(self):
        st = os.stat(self.fifo)
        self.assertTrue(ndi.pipe_to_write(st))
        plain = os.path.join(self.dir, "plain")
        open(plain, "wb").close()
        self.assertFalse(ndi.pipe_to_write(os.stat(plain)))
        with mock.patch.object(ndi.os, "getuid", lambda: st.st_uid + 1):
            self.assertEqual(ndi.pipe_to_write(st), st.st_uid == 0)      # somebody else's: only root's passes

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
        self.assertTrue(os.path.exists(self.s.fifo))                     # the pipe stays: on a box it is root's (M3)
        self.assertEqual(self.lib.closed, 1)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])

    def test_a_source_that_sends_no_picture_is_said_and_let_go(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        reply = self.s.handle({"cmd": "open", "id": self.rid})
        self.assertEqual(reply["ok"], False)
        self.assertIn("sent no picture", reply["error"])
        self.assertEqual((self.lib.closed, self.s.receiver, os.path.exists(self.s.fifo)), (1, None, True))      # let go, the pipe left in place

    def test_a_source_that_vanished_from_the_network_cannot_be_opened(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.assertEqual(len(self.s.handle({"cmd": "status"})["sources"]), 2)
        self.lib.raw = [self.lib.raw[2]]                               # Resolume quit
        reply = self.s.handle({"cmd": "open", "id": self.rid})         # within the second the list is kept for
        self.assertEqual((reply["ok"], reply["error"]), (False, "that source is not on the network now"))
        self.assertEqual(self.lib.opened, [])

    def test_a_pipe_made_beforehand_is_used_as_it_is_and_never_unlinked(self):
        """On a box root makes the pipe once (tmpfiles); the helper opens it, writes it, and leaves it there."""
        before = os.stat(self.s.fifo).st_ino if os.path.exists(self.s.fifo) else None
        if before is None:
            os.mkfifo(self.s.fifo, 0o660)
            before = os.stat(self.s.fifo).st_ino
        self.s.handle({"cmd": "configure", "on": True, "addresses": []})
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.rid})["ok"])
        self.assertEqual(os.stat(self.s.fifo).st_ino, before, "the pipe was replaced")
        self.s.handle({"cmd": "close"})
        self.assertTrue(stat.S_ISFIFO(os.stat(self.s.fifo).st_mode), "the pipe was removed")
        self.assertEqual(os.stat(self.s.fifo).st_ino, before)
        self.s.handle({"cmd": "configure", "on": False, "addresses": []})
        self.assertTrue(os.path.exists(self.s.fifo))

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
        # The second source's first frame must come after the first receiver has been let go and the second one opened,
        # not after a tenth of a second (a timer lost that race on a loaded machine, 3 runs in 5 for the reviewer).
        def second_frame():
            if wait(lambda: len(self.lib.opened) == 2, 5):
                self.lib.frames.put(frame(64, 16))
        threading.Thread(target=second_frame, daemon=True).start()
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
        self.assertEqual((st["playing"]["state"], self.lib.closed, os.path.exists(self.s.fifo)), ("changed", 1, True))


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
                                         "counts": {"dropped_by_runtime": 4}, "container": "raw", "audio": None})
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
        self.assertFalse(os.path.lexists(dest))            # review M1: not even the folder it made is left by a refused file
        for source in (self.put("script.sh", b"#!/bin/sh\n" + b" " * 40), os.path.join(self.dir, "missing"), self.dir):
            with self.assertRaises(ndi.NdiError, msg=source):
                ndi.install_runtime(source, dest, machine=183, chown=False)
        self.assertFalse(os.path.lexists(dest))
        # a folder that was there before stays, with what was in it; so does a library put there earlier
        os.makedirs(dest)
        with self.assertRaises(ndi.NdiError):
            ndi.install_runtime(wrong, dest, machine=183, chown=False)
        self.assertEqual(os.listdir(dest), [])
        good = self.put("good/libndi.so.6", elf(183) + b"arm64")
        ndi.install_runtime(good, dest, machine=183, chown=False)
        with self.assertRaises(ndi.NdiError):
            ndi.install_runtime(wrong, dest, machine=183, chown=False)
        self.assertEqual(os.listdir(dest), [ndi.LIB_NAME])
        import shutil as _shutil
        _shutil.rmtree(dest)
        self.assertEqual(ndi.install_runtime(good, dest, machine=183, chown=False), os.path.join(dest, ndi.LIB_NAME))     # and a good file still makes it
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
        self.assertEqual(self.call("GET", "/api/ndi", token=self.invite("view"))[0], 403)     # a Guest has no page for it (D80)
        st, body, _ = self.call("GET", "/api/ndi", token=self.invite("live"))
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
        self.assertEqual((st, body), (200, {"playing": "ndi", "name": "RESOLUME (Output)", "width": 1920, "height": 1080, "fps": 59.94, "sound": False}))
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
        del data["ndi"]                                                  # a file without the key (an older release wrote it; no migration adds it)
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

    def test_a_link_given_as_the_file_is_followed_only_to_a_plain_name_beside_it(self):
        """The first run on a Pi 4: the SDK's own `libndi.so.6` is a link to `libndi.so.6.3.2` beside it, and the
        name NDI's pages give was refused as "a link". Followed now, and no further than that."""
        sdk = os.path.join(self.dir, "sdk", "lib", "aarch64-rpi4-linux-gnueabi")
        other = os.path.join(self.dir, "elsewhere")
        os.makedirs(sdk)
        os.makedirs(other)
        real = os.path.join(sdk, "libndi.so.6.3.2")
        with open(real, "wb") as f:
            f.write(elf(183) + b"the real one")
        os.symlink("libndi.so.6.3.2", os.path.join(sdk, "libndi.so.6"))          # as the SDK ships it
        os.symlink("libndi.so.6", os.path.join(sdk, "libndi.so"))                # and a link to that link
        for name in ("libndi.so.6", "libndi.so"):
            path = ndi.install_runtime(os.path.join(sdk, name), self.dest, machine=183, chown=False)
            with open(path, "rb") as f:
                self.assertTrue(f.read().endswith(b"the real one"), name)
            self.assertFalse(os.path.islink(path))                               # a copy, never a link
            os.unlink(path)
        # the wrong processor is still told apart through the link (what device step N2 asks for)
        x86 = os.path.join(self.dir, "sdk", "lib", "x86_64-linux-gnu")
        os.makedirs(x86)
        with open(os.path.join(x86, "libndi.so.6.3.2"), "wb") as f:
            f.write(elf(62))
        os.symlink("libndi.so.6.3.2", os.path.join(x86, "libndi.so.6"))
        with self.assertRaises(ndi.NdiError) as e:
            ndi.install_runtime(os.path.join(x86, "libndi.so.6"), self.dest, machine=183, chown=False)
        self.assertIn("is for x86_64, this box is aarch64", str(e.exception))
        # everything else that is a link stays refused, and nothing is put in place
        with open(os.path.join(other, "lib.so"), "wb") as f:
            f.write(elf(183) + b"from another folder")
        os.mkdir(os.path.join(sdk, "sub"))
        with open(os.path.join(sdk, "sub", "lib.so"), "wb") as f:
            f.write(elf(183))
        refused = {"abs": self.good, "abs-beside": real, "up": "../../../elsewhere/lib.so", "down": "sub/lib.so", "dot": "./libndi.so.6.3.2",
                   "dangling": "not-there.so", "itself": "itself", "three": "two", "two": "one", "one": "libndi.so.6.3.2",
                   "loop-a": "loop-b", "loop-b": "loop-a"}
        for name, target in refused.items():
            os.symlink(target, os.path.join(sdk, name))
        shutil.rmtree(self.dest)
        for name, words in (("abs", "not to a plain name beside it"), ("abs-beside", "not to a plain name beside it"),
                            ("up", "not to a plain name beside it"), ("down", "not to a plain name beside it"),
                            ("dot", "not to a plain name beside it"),
                            ("dangling", "a file that is not there"), ("itself", "a link to a link to a link"),
                            ("three", "a link to a link to a link"), ("loop-a", "a link to a link to a link")):
            with self.assertRaises(ndi.NdiError, msg=name) as e:
                ndi.install_runtime(os.path.join(sdk, name), self.dest, machine=183, chown=False)
            self.assertIn(words, str(e.exception), name)
            self.assertFalse(os.path.lexists(self.dest), name)
        ndi.install_runtime(os.path.join(sdk, "two"), self.dest, machine=183, chown=False)       # two links: the bound, and allowed
        self.assertEqual(ndi.LINK_HOPS, 2)

    def test_a_name_swapped_for_a_link_after_the_look_is_refused_at_the_open(self):
        sdk = os.path.join(self.dir, "sdk")
        os.makedirs(sdk)
        real = os.path.join(sdk, "libndi.so.6.3.2")
        with open(real, "wb") as f:
            f.write(elf(183))
        os.symlink("libndi.so.6.3.2", os.path.join(sdk, "libndi.so.6"))
        looked = ndi.same_folder_target

        def swap(path):
            out = looked(path)
            os.unlink(real)
            os.symlink(self.good, real)                                 # between the look and the open
            return out
        from unittest import mock
        with mock.patch.object(ndi, "same_folder_target", swap):
            with self.assertRaises(ndi.NdiError) as e:
                ndi.install_runtime(os.path.join(sdk, "libndi.so.6"), self.dest, machine=183, chown=False)
        self.assertIn("cannot be read as a file (it is a link)", str(e.exception))
        self.assertFalse(os.path.lexists(self.dest))
        self.assertIn('getattr(os, "O_NOFOLLOW", 0)', __import__("inspect").getsource(ndi.install_runtime))

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
        self.assertFalse(os.path.exists(self.dest) and os.listdir(self.dest))

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

        def play(player, paths, *a, **kw):
            order.append("clip loaded")
            return real_play(paths, *a, **kw)
        import types
        self.player.play_pipe, self.player.play = play_pipe, types.MethodType(play, self.player)
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
        import types

        def clear(player):
            return None

        def play_pipe(player, *args):
            return None
        for fn, args in ((self.player.play, ([os.path.join(self.media, "a.mp4")],)), (types.MethodType(clear, self.player), ()),
                         (types.MethodType(play_pipe, self.player), ("/x", 16, 16, 30))):
            t = self.api.ndi.ticket()
            self.api._player_call(fn, *args)
            self.assertFalse(self.api.ndi.still(t), fn.__name__)
        t = self.api.ndi.ticket()
        self.api._player_call(clear, None)                               # something else that happens to be called "clear"
        self.assertTrue(self.api.ndi.still(t))
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

    def test_switching_the_module_off_while_the_pipe_is_on_the_screen_clears_it_and_says_why(self):
        """Review of b46410c, medium 2: _stop_ndi leaves a source alone exactly when its pipe plays, so the switch-off
        skipped the clear, kept the note and the speed hold, and the helper went anyway: a black screen with no word."""
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.player.pipe_is = lambda path: path == self.service.fifo        # the real player after the pipe is loaded
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.assertTrue(self.api._speed_held)
        clears = len([c for c in self.player.calls if c == ("clear",)])
        self.assertEqual(self.enable(False)[0], 200)
        self.assertEqual(len([c for c in self.player.calls if c == ("clear",)]), clears + 1, "the screen was not cleared")
        self.assertIsNone(self.api.ndi.current)
        self.assertFalse(self.api._speed_held, "the Mix speed was not given back")
        self.assertEqual(self.api.ndi.ended, {"name": "RESOLUME (Output)", "message": ndi.SWITCHED_OFF})
        self.assertTrue(wait(lambda: self.service.receiver is None))
        self.enable()
        self.assertEqual(self.call("GET", "/api/ndi", token=self.full)[1]["ended"], {"name": "RESOLUME (Output)", "message": ndi.SWITCHED_OFF})

    def test_the_capture_helper_is_stopped_when_an_ndi_source_plays_and_the_other_way_round(self):
        """Player.pipe_is (2026-10-11): each live input has a pipe of its own. "A pipe plays" alone (pipe_playing) left
        the capture helper writing into its pipe while the NDI pipe was what played, and the other way round."""
        from pvj import locks as locks_mod
        self.enable()

        class Capture:
            lock = locks_mod.make("capture", reentrant=True)
            fifo = os.path.join(tempfile.gettempdir(), "capture-never-made.fifo")
            stopped = 0

            def stop(self):
                Capture.stopped += 1

            def status(self, devices=True):
                return {"current": None}
        cap = self.api.capture = Capture()
        self.player.pipe_playing = True
        self.player.pipe_is = lambda path: path == self.service.fifo        # what the real player says once the NDI pipe is loaded
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.assertEqual(cap.stopped, 1, "the capture helper was left alone because 'a pipe plays'")
        self.assertEqual(self.api.ndi.current["id"], self.rid)
        self.assertFalse(self.api._stop_ndi())                               # its own pipe is what plays: left alone
        self.assertEqual(self.api.ndi.current["id"], self.rid)
        self.player.pipe_is = lambda path: path == cap.fifo                  # now the capture input's pipe was loaded
        self.api._stop_capture()
        self.assertEqual(cap.stopped, 1, "the capture helper is what plays and was stopped all the same")
        self.assertTrue(self.api._stop_ndi())                                # and the NDI source is let go
        self.assertIsNone(self.api.ndi.current)

    def test_a_live_source_is_cut_to_a_transition_on_its_way_is_ended_and_the_load_is_under_the_players_lock(self):
        """D71 with D62: an NDI source is never crossfaded in. The play goes through _as_newest, which ends a transition
        on its way and loads under the player's lock; the ticket is looked at under the same lock (`ready`)."""
        self.enable()
        ended, under = [], []
        real = self.api.transitions.end

        def end(why="ended", newer=False):
            ended.append((why, newer))
            return real(why, newer)
        self.api.transitions.end = end
        lock = self.player._lock

        def play_pipe(*args):
            under.append(lock._is_owned() if hasattr(lock, "_is_owned") else True)
            self.player.calls.append(("play_pipe",) + args)
        self.player.play_pipe = play_pipe
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.assertIn(("another play", True), ended)
        self.assertEqual(under, [True], "the pipe was loaded outside the player's lock")
        self.assertEqual(self.api.ndi.current["id"], self.rid)


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
        self.s._receiver = lambda lib, source, fifo, log, sound=False: ndi.Receiver(lib, source, fifo, log=log, clock=lambda: clock[0], sound=sound)
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
            pass                                                         # a file in the pipe's place (root's folder: the helper cannot remove it)
        import pwd
        web = mock.Mock(pw_uid=4242)
        with mock.patch.dict(os.environ, {"PVJ_NDI_DIR": d, "PVJ_NDI_PIPE_DIR": d}), mock.patch("pvj.netd.NetServer", Server), \
                mock.patch.object(pwd, "getpwnam", lambda name: web if name == "pvj-web" else (_ for _ in ()).throw(KeyError(name))), \
                mock.patch("builtins.print"):
            self.assertEqual(ndi.main([]), 0)
        self.assertEqual(made["path"], os.path.join(d, paths.NDI_SOCKET))
        self.assertEqual([made["is_allowed"](uid) for uid in (4242, 0, os.getuid(), 4243, 65534)],
                         [True, False, os.getuid() == 4242, False, False])
        self.assertTrue(os.path.exists(os.path.join(d, paths.NDI_FIFO)), "the helper is not the pipe's owner and never unlinks it")
        self.assertEqual(made["service"].fifo, os.path.join(d, paths.NDI_FIFO))
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

    def test_a_pipe_in_a_folder_others_may_change_is_not_loaded(self):
        """Review of 2026-10-11, M3: the look at the pipe and the player's opening of it are two moments, so what
        holds is the folder: root's, and nobody else may add, rename or remove a name in it."""
        self.enable()
        folder = os.path.dirname(self.service.fifo)
        mode = os.stat(folder).st_mode & 0o777
        self.addCleanup(os.chmod, folder, mode)
        self.swap(lambda: os.chmod(folder, 0o770))
        self.refused("the NDI helper's pipe is in a folder that is not root's alone; not loading it")
        os.chmod(folder, 0o707)
        self.refused("the NDI helper's pipe is in a folder that is not root's alone; not loading it")
        os.chmod(folder, mode)
        self.api.ndi._pipe_owner = lambda: os.getuid() + 1                   # the folder is not the pipe owner's either
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
        # root makes the pipe (the tmpfiles lines pvj-ndi-runtime writes), so root is its owner; the helper only writes it
        self.assertIn("pipe_owner=ndi_mod.pipe_owner", inspect.getsource(server.build))
        self.assertEqual(ndi.pipe_owner(), 0)
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
        # Closed again while the library still has the thread (the Service does this: at every status of an ended
        # receiver, and at the next open). The second close found no thread on its list and freed the connection
        # under the one still inside (review, 2026-10-08).
        r.close()
        r.close()
        self.assertEqual(lib.closed, 0, "a second close freed the connection under the thread still in the library")
        self.assertEqual(len([m for m in logged if "left open, not freed under it" in m]), 1)       # and it is said once
        let_go.set()                                                     # it comes back after all: now it is given back, once
        self.assertTrue(wait(lambda: lib.closed == 1))
        r.close()
        self.assertEqual(lib.closed, 1)

    def test_a_library_that_answers_at_once_with_nothing_is_not_asked_flat_out(self):
        """The fake used to wait 20 ms whenever it had nothing, which hid this: a library that comes back at once
        (the real one does while a connection is down) was asked again without a pause, a core's worth."""
        class Eager(FakeLib):
            calls = 0

            def recv_capture(self, handle, timeout_ms):
                self.calls += 1
                return None
        lib = Eager()
        fifo = os.path.join(self.dir, "f2")
        os.mkfifo(fifo)
        r = ndi.Receiver(lib, ndi.clean_sources(lib.raw)[0][0], fifo, log=lambda *_: None)
        r.start()
        time.sleep(0.6)
        calls = lib.calls
        started = time.monotonic()
        r.close()
        self.assertLess(time.monotonic() - started, 1.0)                 # and a stop is still seen at once
        self.assertLessEqual(calls, 8, "the library was asked %d times in 0.6 s" % calls)
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
        self.assertTrue(os.path.exists(s.fifo))                          # and the close left it too: the helper never unlinks the pipe (M3)

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


class SourceIsPlainTextTest(unittest.TestCase):
    def test_the_ndi_sources_hold_no_character_a_reviewer_cannot_see(self):
        # Escapes for a bidirectional override and a zero-width space once landed in this file as the characters.
        here = os.path.dirname(os.path.abspath(__file__))
        for path in (os.path.join(here, "test_ndi.py"), os.path.join(here, "..", "pvj", "ndi.py"),
                     os.path.join(here, "..", "pvj", "systemd", "pvj-ndi.service"), os.path.join(here, "..", "bin", "pvj-ndi-runtime"),
                     os.path.join(here, "..", "pvj", "ndisetup.py"), os.path.join(here, "test_ndi_setup.py")):
            with open(path, encoding="utf-8") as f:
                odd = sorted({"U+%04X" % ord(ch) for ch in f.read() if ord(ch) > 126 or (ord(ch) < 32 and ch not in "\n\t")})
            self.assertEqual(odd, [], path)


class BracketsInNamesTest(unittest.TestCase):
    """Found by a second read of review fix 1: the bound on nesting counted brackets inside names, so one sender
    named with brackets made every answer of the helper a "bad answer" and the input look dead."""

    def test_a_real_status_with_brackets_in_every_name_goes_through_the_real_client(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        names = ["[" * 128, "{" * 128, "]" * 100 + "[" * 28, 'quote " [[[[', "back\\slash [[[["] + ["Cam [%02d" % n for n in range(59)]
        lib = FakeLib([(n.encode(), b"192.168.0.%d:5961" % (i + 1)) for i, n in enumerate(names)])
        s = ndi.Service(d, "x", loader=lambda path: lib, problem=lambda path: None, log=lambda *_: None)
        self.addCleanup(s.close)
        c = ndi.Client("unused")
        c._exchange = lambda message, timeout: json.dumps(s.handle(message)).encode() + b"\n"
        i = ndi.Input(c, s.fifo, lambda: (True, []), log=lambda *_: None)
        st = i.status()
        self.assertEqual((st["helper"], len(st["sources"])), (True, 64))
        self.assertEqual(sorted(x["name"] for x in st["sources"]), sorted(names))

    def test_depth_is_counted_outside_strings_only(self):
        for raw, want in ((b'{"a": [1, {"b": []}]}', 4), (b'{"name": "[[[[[[[[{{{{"}', 1), (b'{"n": "a\\"[[[", "m": [[]]}', 3),
                          (b'{"n": "a\\\\", "m": [[[]]]}', 4), (b"[" * 60000, 60000), (b'"' + b"[" * 60000, 0), (b"", 0), (b"]]]][", 1)):
            self.assertEqual(ndi.json_depth(raw), want, raw[:30])
        c = ndi.Client("unused")
        for raw in (b"[" * 60000 + b"\n", b'{"a":' * 65 + b"1" + b"}" * 65):                  # deep outside any string: still refused
            c._exchange = lambda message, timeout, raw=raw: raw
            with self.assertRaises(ndi.NdiError):
                c.request({"cmd": "status"})


class OldPlayer:
    """A reader of the pipe that behaves as a player may: it reads (or, `reads` False, has stopped reading with
    bytes still in the pipe), and at the end of the stream it goes at once (`stays` False) or holds its end of the
    pipe open until leave() (a player with frames left to show, or one that has not yet acted on the end). What it
    read before the first end and what it read after it are kept apart: the second is what a later source wrote
    into this reader's stream."""

    def __init__(self, fifo, stays=True, reads=True):
        self.fifo, self.stays, self.reads = fifo, stays, reads
        self.got, self.late, self.ended, self.opened, self.gone = bytearray(), bytearray(), threading.Event(), threading.Event(), threading.Event()
        self._leave = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        fd = os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK)      # as mpv opens it: without waiting for a writer
        self.opened.set()
        try:
            while not self._leave.is_set():
                if not self.reads:
                    time.sleep(0.005)
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:                           # a writer is there and has written nothing yet
                    time.sleep(0.002)
                    continue
                if chunk:
                    (self.late if self.ended.is_set() else self.got).extend(chunk)
                elif self.got or self.ended.is_set() or self.wake:
                    self.ended.set()
                    if not self.stays:
                        return
                    time.sleep(0.002)
                else:
                    time.sleep(0.002)                             # no writer yet: a player waits here
        finally:
            os.close(fd)
            self.gone.set()

    wake = False                                                  # True: a reader that takes "no writer" as the end even before a first byte

    def leave(self):
        self._leave.set()
        self.thread.join(3)


def sent_all(test):
    """The fake library has one queue of frames for every source: a frame of the source before that is still on
    its way would be the first frame of the next one, in the wrong size. Wait until the last sender is done and
    its frames were taken."""
    sender = getattr(test, "sender", None)
    if sender is not None:
        sender.join(5)
    wait(test.lib.frames.empty)
    time.sleep(0.15)                                                 # and the one a receiver was just handed is through


class PipeReuseTest(unittest.TestCase):
    """Since the review of 2026-10-11 (M3) the pipe is one pipe for every source: root's, made once, never unlinked.
    Until then every source had a pipe of its own, and that alone kept the reader of the last source and the writer
    of the next apart. These tests hold the order that now has to be made: the pipe is seen without a reader, then
    the helper answers, then the panel loads, then the writer opens."""

    A, B = (64, 16, 0xA1), (128, 16, 0xB2)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = FakeLib([(b"A (Out)", b"192.168.0.20:5961"), (b"B (Out)", b"192.168.0.21:5961")])
        self.said = []
        self.s = self.service()
        self.ida, self.idb = ndi.source_id("A (Out)", "192.168.0.20"), ndi.source_id("B (Out)", "192.168.0.21")
        self.players = []
        self.addCleanup(lambda: [p.leave() for p in self.players])

    def service(self, **kw):
        s = ndi.Service(self.dir, "x", loader=lambda path: self.lib, problem=lambda path: None, log=self.said.append, first_frame=2.0, **kw)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "addresses": []})
        return s

    def player(self, **kw):
        p = OldPlayer(self.s.fifo, **kw)
        self.players.append(p)
        self.assertTrue(p.opened.wait(3))
        return p

    def open(self, sid, shape, frames=2):
        """Open `sid`; its frames come once the helper has connected to it, as a sender's do."""
        sent_all(self)                                               # the source before has had all of its frames
        n = len(self.lib.opened)

        def send():
            if wait(lambda: len(self.lib.opened) == n + 1, 5):
                for _ in range(frames):
                    self.lib.frames.put(frame(shape[0], shape[1], fill=shape[2]))
                    time.sleep(0.03)
        self.sender = threading.Thread(target=send, daemon=True)
        self.sender.start()
        began = time.monotonic()
        reply = self.s.handle({"cmd": "open", "id": sid})
        self.assertTrue(reply["ok"], reply)
        return time.monotonic() - began

    def size(self, shape):
        return shape[0] * shape[1] * 2

    def play_a(self, **kw):
        took = self.open(self.ida, self.A)
        old = self.player(**kw)                                      # the panel loads the pipe after the answer
        return old, took

    def test_the_next_source_is_not_written_into_the_stream_of_the_player_still_on_the_one_before(self):
        old, took = self.play_a()
        self.assertLess(took, 1.0, "an open with nobody on the pipe waited for something")
        self.assertTrue(wait(lambda: len(old.got) >= self.size(self.A)))
        threading.Timer(0.4, old.leave).start()                      # the player lets go of A's stream a moment after its end
        self.open(self.idb, self.B, frames=3)
        self.assertTrue(old.gone.is_set(), "the helper answered while the player of the source before still held the pipe")
        self.assertEqual(bytes(old.late), b"", "the next source's frames went into the stream of the one before")
        self.assertEqual(self.s.receiver.status()["state"], "ready")
        new = self.player()                                          # the panel's load for B
        self.assertTrue(wait(lambda: len(new.got) >= self.size(self.B)))
        self.assertEqual(self.s.receiver.status()["state"], "playing")
        self.assertEqual(set(new.got), {self.B[2]})                  # B's picture from its first byte
        self.assertFalse([m for m in self.said if "still held" in m], self.said)

    def test_what_the_last_source_left_in_the_pipe_is_not_the_start_of_the_next(self):
        """The mirror: a player that has stopped reading with a part of A's frame still in the pipe. Raw frames have
        no heading, so bytes of A at the start of B's stream would shift every frame of B for good."""
        big = (1920, 1080, 0xA1)                                     # 4 MB: more than any pipe holds
        self.open(self.ida, big)
        old = self.player(reads=False)
        self.assertTrue(wait(lambda: self.s.receiver.status()["state"] == "playing"))
        time.sleep(0.2)                                              # A's writer is now in the middle of a frame, the pipe full
        threading.Timer(0.3, old.leave).start()
        self.open(self.idb, self.B, frames=3)
        self.assertTrue(old.gone.is_set())
        new = self.player()
        self.assertTrue(wait(lambda: len(new.got) >= self.size(self.B)))
        time.sleep(0.2)
        self.assertEqual(set(new.got), {self.B[2]}, "bytes of the source before were read as the start of this one")
        self.assertEqual(len(new.got) % self.size(self.B), 0)
        self.assertEqual(self.s.receiver.status()["state"], "playing")

    def test_a_player_that_opens_late_for_the_load_before_is_given_the_end_and_gets_none_of_the_next_source(self):
        """A pad pressed twice: the player may open the pipe for the first press's load when the helper is already
        on the second. That reader has the first load's size; it must be gone before the second's writer opens."""
        self.open(self.ida, self.A)                                  # answered; its player has not opened the pipe yet
        late = []

        def send():
            if wait(lambda: len(self.lib.opened) == 2, 5):
                late.append(self.player(stays=False))                # now it does, after the helper went on to B
                late[0].wake = True
                for _ in range(3):
                    self.lib.frames.put(frame(self.B[0], self.B[1], fill=self.B[2]))
                    time.sleep(0.03)
        threading.Thread(target=send, daemon=True).start()
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.idb})["ok"])
        self.assertTrue(late and late[0].gone.is_set(), "the helper answered with the late reader of the load before on the pipe")
        self.assertEqual(bytes(late[0].got + late[0].late), b"")
        new = self.player()
        self.assertTrue(wait(lambda: len(new.got) >= self.size(self.B)))
        self.assertEqual(set(new.got), {self.B[2]})

    def test_over_a_player_that_will_not_let_go_nothing_is_written_and_the_open_says_why(self):
        self.s.close()
        self.s = self.service(pipe_clear=0.3)
        old, _ = self.play_a()
        self.assertTrue(wait(lambda: len(old.got) >= self.size(self.A)))
        sent_all(self)
        threading.Thread(target=lambda: wait(lambda: len(self.lib.opened) == 2, 5) and self.lib.frames.put(frame(128, 16, fill=0xB2)), daemon=True).start()
        began = time.monotonic()
        reply = self.s.handle({"cmd": "open", "id": self.idb})
        self.assertEqual((reply["ok"], reply["error"]), (False, ndi.PIPE_HELD))
        self.assertLess(time.monotonic() - began, 1.5)               # bounded
        self.assertEqual(len([m for m in self.said if "still held the pipe" in m]), 1, self.said)
        time.sleep(0.2)
        self.assertEqual((bytes(old.late), self.s.receiver, self.lib.closed), (b"", None, 2))      # not a byte, and the source let go
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])
        old.leave()                                                  # the player lets go at last: the next try is as any other
        self.open(self.idb, self.B, frames=3)
        new = self.player()
        self.assertTrue(wait(lambda: len(new.got) >= self.size(self.B)))
        self.assertEqual(set(new.got), {self.B[2]})

    def test_the_writer_waits_for_the_word_and_a_close_meanwhile_ends_it(self):
        fifo = os.path.join(self.dir, "own.fifo")
        os.mkfifo(fifo, 0o640)
        r = ndi.Receiver(self.lib, ndi.clean_sources(self.lib.raw)[0][0], fifo, log=lambda *_: None)
        r.hold()
        r.start()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(r.first.wait(3))
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        time.sleep(0.2)
        self.assertEqual(r.status()["state"], "ready")               # a reader is there; the writer has not opened
        r.go()
        self.assertTrue(wait(lambda: r.status()["state"] == "playing"))
        r.close()
        held = ndi.Receiver(self.lib, ndi.clean_sources(self.lib.raw)[0][0], fifo, log=lambda *_: None)
        held.hold()
        held.start()
        began = time.monotonic()
        held.close()
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ndi-")])


class LostPlayerTest(NdiApiTest):
    """A source whose writer lost its reader ended with "the player stopped reading the input", which the watch took
    for "something else was played": the screen stayed black, the pad went dark and nothing was said or tried (seen
    on the Pi on 2026-10-12). The panel knows better: the pipe is still what it loaded last."""

    def setUp(self):
        super().setUp()
        self.players = []
        self.addCleanup(lambda: [p.leave() for p in self.players])

    def reader(self, **kw):
        p = OldPlayer(self.service.fifo, **kw)
        self.players.append(p)
        self.assertTrue(p.opened.wait(3))
        return p

    def play(self, sid, w, fill, frames=3):
        sent_all(self)
        n = len(self.lib.opened)

        def send():
            if wait(lambda: len(self.lib.opened) == n + 1, 5):
                for _ in range(frames):
                    self.lib.frames.put(frame(w, 16, fill=fill))
                    time.sleep(0.03)
        self.sender = threading.Thread(target=send, daemon=True)
        self.sender.start()
        return self.call("POST", "/api/play", {"ndi": sid}, token=self.full)

    def lose_the_player(self):
        """A source plays, and the player lets go of the pipe without the panel having loaded anything else."""
        self.enable()
        self.assertEqual(self.play(self.rid, 128, 0xB2)[0], 200)
        old = self.reader()
        self.assertTrue(wait(lambda: len(old.got) >= 128 * 16 * 2))
        sent_all(self)
        old.leave()
        self.lib.frames.put(frame(128, 16, fill=0xB2))
        self.lib.frames.put(frame(128, 16, fill=0xB2))
        self.assertTrue(wait(lambda: self.service.receiver.status()["message"] == ndi.PLAYER_LEFT))
        self.api.ndi._retry_at = 0.0

    def test_a_source_that_lost_the_player_while_its_pipe_is_what_plays_is_shown_again(self):
        self.player.pipe_is = lambda path: path == self.service.fifo       # the panel's own record: the pipe was loaded last
        self.lose_the_player()
        loads = len([c for c in self.player.calls if c[0] == "play_pipe"])
        threading.Timer(0.1, lambda: [self.lib.frames.put(frame(128, 16, fill=0xB2)) for _ in range(3)]).start()
        self.assertTrue(self.api.ndi_tick())
        self.assertEqual(self.api.ndi.current, {"id": self.rid, "name": "RESOLUME (Output)"})
        self.assertEqual(len([c for c in self.player.calls if c[0] == "play_pipe"]), loads + 1)
        new = self.reader()
        self.assertTrue(wait(lambda: len(new.got) >= 128 * 16 * 2))
        self.assertEqual(set(new.got), {0xB2})
        self.assertEqual(self.service.receiver.status()["state"], "playing")
        self.assertFalse(self.api.ndi_tick())                        # not again within the pause

    def test_when_something_else_took_the_player_it_is_over_and_the_page_keeps_why(self):
        self.player.pipe_is = lambda path: False                     # the player is another process, or was loaded past the panel
        self.lose_the_player()
        self.assertFalse(self.api.ndi_tick())
        self.assertEqual((self.api.ndi.current, self.api.ndi.ended), (None, {"name": "RESOLUME (Output)", "message": ndi.PLAYER_LEFT}))
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((page["playing"], page["ended"]["message"]), (None, ndi.PLAYER_LEFT))      # never nothing and no reason

    def test_a_reload_that_the_panel_itself_refuses_keeps_its_reason_for_the_page(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(wait(lambda: self.service.receiver.state == "changed"))
        self.api.ndi._pipe_owner = lambda: 54321                     # the pipe is not what it should be when the watch loads it again
        threading.Timer(0.1, self.lib.frames.put, [frame(128, 16)]).start()
        self.assertTrue(self.api.ndi_tick())
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertIsNone(page["playing"])
        self.assertEqual(page["ended"], {"name": "RESOLUME (Output)", "message": "the NDI helper's pipe is not what it should be; not loading it"})

    def test_a_player_that_loses_the_pipe_every_time_is_loaded_less_and_less_often(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        now, replays, logged = [50.0], [], []

        class Left:
            def status(self):
                return {"ok": True, "configured": True, "playing": {"id": "ab12cd34ef56", "name": "x", "state": "stopped", "message": ndi.PLAYER_LEFT,
                                                                    "counts": {}, "width": 64, "height": 16, "fps": 30.0}}
        i = ndi.Input(Left(), os.path.join(d, "f"), lambda: (True, []), log=logged.append, clock=lambda: now[0])
        i.current = {"id": "ab12cd34ef56", "name": "x"}
        at = []
        for _ in range(400):
            if i.tick(replays.append, loaded=lambda: True):
                at.append(now[0])
            now[0] += 1.0
        self.assertEqual([round(b - a) for a, b in zip(at, at[1:])][:6], [3, 6, 12, 24, 48, 60])
        self.assertEqual(i.current["id"], "ab12cd34ef56")


class HelperRestartTest(NdiApiTest):
    """The helper ends under a source (a crash in the library, or its own exit when the discovery servers change:
    Service.on_unload) and systemd starts a new one, which knows nothing. Who brings the picture back, and when not."""

    def setUp(self):
        super().setUp()
        self.players = []
        self.addCleanup(lambda: [p.leave() for p in self.players])

    def reader(self):
        p = OldPlayer(self.service.fifo, stays=False)                # a player goes idle at the end of the stream: black
        self.players.append(p)
        self.assertTrue(p.opened.wait(3))
        return p

    def die(self):
        self.service.close()                                         # every thread of the process gone, its end of the pipe closed
        self.client.down = True

    def back(self, sources=True):
        """A new process: nothing configured, nothing loaded, and the senders not found yet unless `sources`."""
        self.lib = FakeLib([(b"RESOLUME (Output)", b"192.168.0.20:5961")] if sources else [])
        self.service = ndi.Service(self.ndidir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.3, problem=lambda path: None)
        self.addCleanup(self.service.close)
        self.client.service, self.client.down = self.service, False
        self.api.ndi._retry_at = 0.0

    def lit(self, on_screen):
        """What a pad holding the source goes by: the status names the source's id only while the pipe is what the player plays."""
        self.player.status = lambda: {"running": True, "path": self.service.fifo if on_screen else None}
        return self.call("GET", "/api/status", token=self.full)[1]["player"].get("ndi_id")

    def test_a_source_that_was_playing_comes_back_by_itself_and_its_pad_says_what_is_true_meanwhile(self):
        self.enable()
        for turn in range(3):                                        # every time, not once
            if turn == 0:
                self.lib.frames.put(frame(64, 16))
                self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
            old = self.reader()
            self.lib.frames.put(frame(64, 16, fill=7))
            self.assertTrue(wait(lambda: len(old.got) >= 64 * 16 * 2))
            self.assertEqual(self.lit(True), self.rid)
            self.die()
            self.assertTrue(old.gone.wait(3), "the player was not given the end when the helper went")
            self.assertFalse(self.api.ndi_tick())                    # no helper: nothing to ask, nothing forgotten
            self.assertEqual(self.api.ndi.current["id"], self.rid)
            page = self.call("GET", "/api/ndi", token=self.full)[1]  # and the page says why the screen is black
            self.assertEqual((page["playing"], page["ended"]), (None, {"name": "RESOLUME (Output)", "message": "the NDI helper (pvj-ndi) is not running; trying again"}))
            self.assertIsNone(self.lit(False))                       # black: the pad is not lit while nothing of it is on the screen
            self.back(sources=False)
            self.assertTrue(self.api.ndi_tick())                     # the new helper is told the settings; the sender is not found yet
            self.assertEqual((self.service.on, self.api.ndi.current["id"]), (True, self.rid))
            page = self.call("GET", "/api/ndi", token=self.full)[1]
            self.assertEqual((page["playing"], page["ended"]["message"]), (None, "that source is not on the network now; trying again"))
            self.lib.raw = [(b"RESOLUME (Output)", b"192.168.0.20:5961")]
            self.assertFalse(self.api.ndi_tick())                    # not before the pause is over
            self.api.ndi._retry_at = 0.0
            self.lib.frames.put(frame(64, 16, fill=9))
            loads = len([c for c in self.player.calls if c[0] == "play_pipe"])
            self.assertTrue(self.api.ndi_tick())
            self.assertEqual(len([c for c in self.player.calls if c[0] == "play_pipe"]), loads + 1)
            self.assertEqual(self.api.ndi.current, {"id": self.rid, "name": "RESOLUME (Output)"})
            self.assertEqual(self.lit(True), self.rid)
            page = self.call("GET", "/api/ndi", token=self.full)[1]
            self.assertEqual((page["playing"]["id"], page["ended"]), (self.rid, None))
        new = self.reader()
        self.lib.frames.put(frame(64, 16, fill=9))
        self.assertTrue(wait(lambda: len(new.got) >= 64 * 16 * 2))
        self.assertEqual(set(new.got), {9})

    def dies_while_connecting(self):
        """The helper goes while it is asked for a source: the question gets no answer."""
        real = self.client.request

        def request(message, timeout=None):
            if message.get("cmd") == "open":
                self.die()
            return real(message, timeout)
        self.client.request = request
        return lambda: setattr(self.client, "request", real)

    def test_a_restart_while_the_watch_is_bringing_the_source_back_is_tried_again(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.die()
        self.back()
        undo = self.dies_while_connecting()                          # and it goes again, in the middle of the watch's own open
        self.assertTrue(self.api.ndi_tick())
        self.assertEqual(self.api.ndi.current["id"], self.rid)       # still wanted
        undo()
        self.back()
        self.lib.frames.put(frame(64, 16))
        loads = len([c for c in self.player.calls if c[0] == "play_pipe"])
        self.assertTrue(self.api.ndi_tick())
        self.assertEqual(len([c for c in self.player.calls if c[0] == "play_pipe"]), loads + 1)
        self.assertEqual(self.api.ndi.current["id"], self.rid)

    def test_a_restart_while_a_first_press_is_still_connecting_is_answered_and_not_brought_back(self):
        """Not what one would wish, and written down as it is: a press whose source never reached the screen is
        not "what plays", so nothing remembers it. The press is answered with the reason and has to be made again."""
        self.enable()
        undo = self.dies_while_connecting()
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body["error"]), (409, "the NDI helper (pvj-ndi) is not running"))
        self.assertIsNone(self.api.ndi.current)
        undo()
        self.back()
        self.assertFalse(self.api.ndi_tick())
        self.assertFalse([c for c in self.player.calls if c[0] == "play_pipe"])

    def test_a_restart_while_another_source_is_pressed_brings_back_the_one_that_was_playing(self):
        self.enable()
        self.lib.raw.append((b"MAD (Out)", b"192.168.0.21:5961"))
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        undo = self.dies_while_connecting()
        self.assertEqual(self.call("POST", "/api/play", {"ndi": ndi.source_id("MAD (Out)", "192.168.0.21")}, token=self.full)[0], 409)
        undo()
        self.back()
        self.lib.frames.put(frame(64, 16))
        self.assertTrue(self.api.ndi_tick())
        self.assertEqual(self.api.ndi.current, {"id": self.rid, "name": "RESOLUME (Output)"})


class PlayerLetsGoTest(NdiApiTest):
    """What the Pi showed on 2026-10-12: Scan Converter's picture changed size with every clip, each change was
    opened again on the same pipe, and twice out of twice the new source ended at once with "the player stopped
    reading the input" and was gone from the screen without a word. The player there did not let go of the pipe by
    itself after the writer had closed: so here the player's stand-in holds its end until it is TOLD to let go."""

    def holding(self):
        """Not in setUp: the tests this class inherits run against the plain fake player."""
        self.said = []
        self.service.log = self.said.append
        self.readers, self.released = [], []

        def release_pipe(path):
            self.released.append(path)
            for r in self.readers:
                r.leave()
            return True
        self.player.release_pipe = release_pipe
        self.player.pipe_is = lambda path: path == self.service.fifo
        self.addCleanup(lambda: [r.leave() for r in self.readers])
        self.enable()

    def load(self):
        """The player opens the pipe for the load it was just given, and keeps it until it is told otherwise."""
        r = OldPlayer(self.service.fifo)
        self.readers.append(r)
        self.assertTrue(r.opened.wait(3))
        return r

    def send(self, w, fill, n=3):
        for _ in range(n):
            self.lib.frames.put(frame(w, 16, fill=fill))
            time.sleep(0.03)

    def test_a_source_that_changes_size_again_and_again_comes_back_every_time(self):
        self.holding()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        reader, width = self.load(), 64
        for turn, w in enumerate((128, 96, 160, 64), 2):
            self.send(width, turn - 1)
            self.assertTrue(wait(lambda: len(reader.got) >= width * 16 * 2))
            wait(self.lib.frames.empty)
            time.sleep(0.15)
            self.lib.frames.put(frame(w, 16, fill=turn))             # the next clip: another size
            self.assertTrue(wait(lambda: self.service.receiver.state == "changed"))
            self.api.ndi._retry_at = 0.0
            threading.Timer(0.1, self.send, [w, turn]).start()
            self.assertTrue(self.api.ndi_tick())
            self.assertEqual(self.api.ndi.current["id"], self.rid)
            self.assertIn(("play_pipe", self.service.fifo, w, 16, 29.97, "uyvy422"), self.player.calls)
            self.assertEqual(bytes(reader.late), b"", "the new size's frames went into the stream of the old size")
            reader, width = self.load(), w
            self.send(w, turn)
            self.assertTrue(wait(lambda: len(reader.got) >= w * 16 * 2), "turn %d: no picture" % turn)
            self.assertEqual(set(reader.got), {turn})
            self.assertEqual(self.service.receiver.status()["state"], "playing")
            self.assertFalse(self.api.ndi_tick())                    # and it is left alone
        self.assertEqual(len(self.released), 5)                      # before every open
        self.assertFalse([m for m in self.said if "still held" in m], self.said)

    def test_a_pad_switch_from_one_source_to_another_does_not_wait_for_a_player_that_holds_on(self):
        self.holding()
        self.lib.frames.put(frame(64, 16, fill=1))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        old = self.load()
        self.send(64, 1)
        self.assertTrue(wait(lambda: len(old.got) >= 64 * 16 * 2))
        wait(self.lib.frames.empty)
        time.sleep(0.15)
        threading.Timer(0.1, self.send, [128, 2]).start()
        began = time.monotonic()
        self.assertEqual(self.call("POST", "/api/play", {"ndi": ndi.source_id("MAD (Out)", "192.168.0.21")}, token=self.full)[0], 200)
        self.assertLess(time.monotonic() - began, 1.5)               # not the two seconds a player that will not let go costs
        self.assertEqual((self.released, bytes(old.late)), ([self.service.fifo] * 2, b""))      # asked before every open; the player knows whether it holds it
        new = self.load()
        self.send(128, 2)
        self.assertTrue(wait(lambda: len(new.got) >= 128 * 16 * 2))
        self.assertEqual(set(new.got), {2})

    def test_the_player_is_told_to_let_go_only_of_the_pipe_it_was_given_last_and_asked_until_it_is_idle(self):
        from pvj.player import Player, PlayerError
        sent, idle = [], [False, False, True]

        class Ipc:
            down = False

            def request(self, *a):
                sent.append(a)
                if self.down:
                    raise PlayerError("down")
                if a == ("get_property", "idle-active"):
                    return idle.pop(0) if len(idle) > 1 else idle[0]
                if a == ("get_property", "path"):
                    return p.path
                return 41
        p = Player.__new__(Player)
        p.ipc, p._lock, p.path = Ipc(), threading.RLock(), None
        self.assertIsNone(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"))         # nothing was loaded
        p._pipe, p._pipe_pid, p._pipe_path = True, 41, "/run/pvj-capture/capture.fifo"
        self.assertIsNone(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"))         # the capture input's pipe plays: not ours to end
        self.assertNotIn(("stop",), sent)
        del sent[:]
        p._pipe_path = "/run/pvj-ndi-pipe/ndi.fifo"
        self.assertIs(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"), True)
        self.assertEqual(sent, [("stop",)] + [("get_property", "idle-active")] * 3)       # asked until mpv itself says it holds nothing
        self.assertTrue(p.pipe_is("/run/pvj-ndi-pipe/ndi.fifo"))                # still what the panel loaded last (the watch goes by it)
        idle[:] = [False]
        p.RELEASE_SECONDS = 0.2
        began = time.monotonic()
        self.assertIs(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"), False)      # a player that does not let go: said, and bounded
        self.assertLess(time.monotonic() - began, 1.0)
        # a panel that started again remembers no load, and its mpv still has the pipe: mpv's own word counts
        p._pipe, p.path, idle[:] = False, "/run/pvj-ndi-pipe/ndi.fifo", [True]
        del sent[:]
        self.assertIs(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"), True)
        self.assertEqual(sent, [("get_property", "path"), ("stop",), ("get_property", "idle-active")])
        p.path = "/media/a.mp4"
        self.assertIsNone(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"))         # mpv plays a clip: nothing is stopped
        self.assertEqual(sent[-1], ("get_property", "path"))
        p._pipe, p.ipc.down = True, True
        self.assertIs(p.release_pipe("/run/pvj-ndi-pipe/ndi.fifo"), True)       # a player that is down holds nothing

    def test_a_player_that_does_not_confirm_it_let_go_is_not_written_over_and_the_page_says_so(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        opened = len(self.lib.opened)
        asked = []
        self.player.release_pipe = lambda path: asked.append(self.service.receiver is not None) or False
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body["error"]), (409, self.api.NDI_HELD))
        self.assertEqual(len(self.lib.opened), opened)               # no new source was opened over it
        # The player is not left wedged with the old writer feeding it (the review, and the Pi's stuck Test
        # Patterns): the helper was told to close before the player was asked the second time.
        self.assertEqual(asked, [True, False])
        self.assertIsNone(self.service.receiver)
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((self.api.ndi.current, page["playing"], page["ended"]),
                         (None, None, {"name": "RESOLUME (Output)", "message": self.api.NDI_HELD}))
        # and the next press is as any other once the player has let go
        self.player.release_pipe = lambda path: True
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)

    def test_a_player_that_lets_go_only_once_the_writer_has_ended_is_played_to_again_in_the_same_press(self):
        """A player stuck on the stream (opening it, or reading on) ignores the first `stop`, and lets go when the
        stream ends: the press closes the helper's source and goes on."""
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.player.release_pipe = lambda path: self.service.receiver is None
        opened = len(self.lib.opened)                                # the new source's picture, once it is opened
        threading.Thread(target=lambda: wait(lambda: len(self.lib.opened) > opened, 5) and self.lib.frames.put(frame(64, 16)), daemon=True).start()
        st, body, _ = self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)
        self.assertEqual((st, body.get("playing")), (200, "ndi"), body)
        self.assertEqual(self.api.ndi.current["id"], self.rid)

    def test_when_the_watch_asked_a_player_that_will_not_let_go_keeps_the_source_wanted(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        self.player.release_pipe = lambda path: False
        self.lib.frames.put(frame(128, 16))                          # a change of size: the watch asks
        self.assertTrue(wait(lambda: self.service.receiver.state == "changed"))
        self.api.ndi._retry_at = 0.0
        self.assertTrue(self.api.ndi_tick())
        self.assertEqual(self.api.ndi.current["id"], self.rid)       # still wanted, tried again later
        page = self.call("GET", "/api/ndi", token=self.full)[1]
        self.assertEqual((page["playing"], page["ended"]["message"]), (None, self.api.NDI_HELD + "; trying again"))
        self.player.release_pipe = lambda path: True
        self.api.ndi._retry_at = 0.0
        self.lib.frames.put(frame(128, 16))
        self.assertTrue(self.api.ndi_tick())
        self.assertTrue(wait(lambda: (self.call("GET", "/api/ndi", token=self.full)[1]["playing"] or {}).get("width") == 128))

    def test_a_press_on_a_source_that_is_gone_from_the_network_does_not_black_what_plays(self):
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        released = []
        self.player.release_pipe = lambda path: released.append(path) or True
        gone = ndi.source_id("GONE (Out)", "192.168.0.99")
        st, body, _ = self.call("POST", "/api/play", {"ndi": gone}, token=self.full)
        self.assertEqual((st, body["error"]), (409, self.api.NDI_GONE))
        self.assertEqual(released, [])                               # the player was not told to let go
        self.assertEqual(self.api.ndi.current["id"], self.rid)
        self.assertEqual(self.service.receiver.status()["id"], self.rid)       # and the helper still has what plays


if __name__ == "__main__":
    unittest.main()
