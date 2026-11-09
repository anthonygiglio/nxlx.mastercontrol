# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The graphics stage of pvj-render (D129, slice 3; render/src/gfx.c), run for real, headless, like tests/test_pvj_render.

What is held here:
* a picture loaded from a file is drawn where it was put, to the pixel, premultiplied, over the final picture and under
  the text layer (an overlay of the masks' kind is on top of it), and later slots over earlier ones;
* the fade (in and out, from the level it had when it was hidden), the typewriter (line after line, along the ink), the
  scroll and the crawl (once, or looped) move by the clock, with nothing sent while they run;
* what is refused, in words or as "invalid parameter": a bad slot, a relative path, a link, a short file, a picture
  larger than the node's texture limit (a test hook makes it small), all together more than 48 MB, bad numbers, a name
  that is not on the list;
* a clear gives the memory back (the GL objects and the byte count go to zero);
* several clients at once, each speaking on its own socket, change nothing for the others and leave nothing behind;
* the output rate with four graphics on (a crawl among them).

Without the program these are skipped, except where PVJ_RENDER_TEST=1 (the CI job pvj-render).
"""
import os
import random
import shutil
import tempfile
import threading
import time
import unittest

import tests        # the run's own temp folder (tests/__init__.py)
from pvj.player import Ipc, Player, PlayerError
from tests.test_pvj_render import BIN, H, STRICT, W, Renderer, fd_count, need_program, thread_count

import subprocess

WHITE_BGRA = bytes((255, 255, 255, 255))
RED_BGRA = bytes((0, 0, 255, 255))          # B, G, R, A


def hooks_built():
    return "with test hooks" in subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10).stdout


def bgra(path, w, h, pixel):
    """A w x h file of B G R A; pixel(x, y) gives a 4-byte pixel."""
    rows = [b"".join(pixel(x, y) for x in range(w)) for y in range(h)]
    with open(path, "wb") as f:
        f.write(b"".join(rows))


def solid(path, w, h, px=RED_BGRA):
    with open(path, "wb") as f:
        f.write(px * (w * h))


class Base(unittest.TestCase):
    ARGS = ()
    ENV = None

    def setUp(self):
        need_program(self)
        self.tmp = tempfile.mkdtemp(prefix="pvj-gfx-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(*self.ARGS, env=self.ENV)
        self.addCleanup(lambda: self.r.close())
        self.n = 0

    def make(self, w, h, px=RED_BGRA):
        self.n += 1
        path = os.path.join(self.tmp, "g%d.bgra" % self.n)
        solid(path, w, h, px)
        return path

    def g(self, *command):
        return self.r("pvj-graphic", *command)

    def slots(self):
        return self.r("get_property", "pvj-graphics")

    def slot(self, n):
        return self.slots()["list"][n - 1]

    def loaded(self, n, seconds=5.0):
        ok = self.r.wait(lambda: self.slot(n)["loaded"], seconds)
        self.assertTrue(ok, "slot %d was not uploaded\n%s" % (n, self.r.stderr()))

    def put(self, n, w, h, x, y, px=RED_BGRA, lines=1, **motion):
        self.g("load", n, self.make(w, h, px), w, h, lines)
        if motion or x is not None:
            settings = dict(motion)
            if x is not None:
                settings["x"], settings["y"] = x, y
            self.g("set", n, settings)
        self.loaded(n)

    def drawn(self, n, more=2):
        """Wait until slot n has been drawn `more` more frames (a picture is on the screen)."""
        d0 = self.slot(n)["draws"]
        self.assertTrue(self.r.wait(lambda: self.slot(n)["draws"] >= d0 + more, 5), self.r.stderr())

    def shot(self):
        return self.r.capture()

    def lit(self, shot, pred):
        """The (x, y) of every pixel of a capture for which pred(r, g, b) holds."""
        w, h, px = shot
        out = []
        for y in range(h):
            for x in range(w):
                o = (y * w + x) * 3
                if pred(px[o], px[o + 1], px[o + 2]):
                    out.append((x, y))
        return out


class StillTest(Base):
    def test_it_says_what_it_is(self):
        st = self.r("get_property", "pvj-render")
        self.assertEqual(st["version"], "0.11.0")
        self.assertEqual(st["graphics"], 8)
        self.assertGreaterEqual(st["graphics-side"], 1024)
        s = self.slots()
        self.assertEqual((s["slots"], s["bytes"], s["bytes-max"], s["gl-objects"]), (8, 0, 48 * 1024 * 1024, 0))
        self.assertEqual(len(s["list"]), 8)
        self.assertTrue(all(not x["loaded"] and not x["on"] for x in s["list"]))

    def test_a_picture_is_drawn_where_it_was_put_to_the_pixel(self):
        self.put(1, 100, 40, 20, 30)
        self.g("show", 1)
        self.drawn(1)
        shot = self.shot()
        self.assertEqual(self.r.pixel(20, 30, shot), (255, 0, 0))
        self.assertEqual(self.r.pixel(119, 69, shot), (255, 0, 0))
        for x, y in ((19, 30), (20, 29), (120, 69), (119, 70), (5, 5), (W - 1, H - 1)):
            self.assertEqual(self.r.pixel(x, y, shot), (0, 0, 0), (x, y))
        self.assertEqual(len(self.lit(shot, lambda r, g, b: r > 128)), 100 * 40)

    def test_the_picture_is_premultiplied_and_blue_green_red_is_read_as_such(self):
        self.put(1, 50, 20, 10, 10, bytes((0, 0, 128, 128)))     # half red, premultiplied: R = 128 over black
        self.g("show", 1)
        self.drawn(1)
        r, g, b = self.r.pixel(20, 15)
        self.assertTrue(abs(r - 128) <= 2 and g == 0 and b == 0, (r, g, b))
        self.put(2, 20, 20, 100, 100, bytes((255, 0, 0, 255)))   # B G R A = blue
        self.g("show", 2)
        self.drawn(2)
        self.assertEqual(self.r.pixel(105, 105), (0, 0, 255))

    def test_a_hidden_graphic_is_not_drawn_and_costs_nothing_and_a_shown_one_comes_back(self):
        self.put(1, 40, 40, 50, 50)
        time.sleep(0.4)
        self.assertEqual(self.slot(1)["draws"], 0, "loaded but never shown: not drawn")
        self.assertEqual(self.r.pixel(60, 60), (0, 0, 0))
        self.g("show", 1)
        self.drawn(1)
        self.assertEqual(self.r.pixel(60, 60), (255, 0, 0))
        self.g("hide", 1)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(60, 60) == (0, 0, 0)), "hidden: gone at once (no fade set)")
        d = self.slot(1)["draws"]
        time.sleep(0.4)
        self.assertEqual(self.slot(1)["draws"], d, "a hidden graphic is not drawn")
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(60, 60) == (255, 0, 0)))

    def test_a_later_slot_is_over_an_earlier_one_and_the_text_layer_is_over_both(self):
        self.put(1, 80, 80, 40, 40, RED_BGRA)
        self.put(2, 40, 40, 60, 60, bytes((0, 255, 0, 255)))             # green
        self.g("show", 2)
        self.g("show", 1)
        self.drawn(1)
        self.drawn(2)
        self.assertEqual(self.r.pixel(70, 70), (0, 255, 0), "slot 2 over slot 1")
        self.assertEqual(self.r.pixel(45, 45), (255, 0, 0))
        mag = os.path.join(self.tmp, "m.bgra")
        solid(mag, 30, 30, bytes((255, 0, 255, 255)))
        self.r("overlay-add", 30, 55, 55, mag, 0, "bgra", 30, 30, 120)    # a mask's id: drawn by the text layer
        self.assertTrue(self.r.wait(lambda: self.r.pixel(70, 70) == (255, 0, 255)), "the text layer is over the graphics")
        self.assertEqual(self.r.pixel(45, 45), (255, 0, 0))
        self.r("overlay-remove", 30)

    def test_a_new_picture_replaces_the_old_one_and_a_clear_gives_the_memory_back(self):
        self.put(1, 40, 40, 10, 10)
        self.g("show", 1)
        self.drawn(1)
        self.assertEqual(self.slots()["bytes"], 40 * 40 * 4)
        self.assertEqual(self.slots()["gl-objects"], 1)
        self.put(1, 60, 30, 10, 10, bytes((255, 0, 0, 255)))
        self.assertTrue(self.r.wait(lambda: self.slot(1)["width"] == 60), self.r.stderr())
        self.assertTrue(self.r.wait(lambda: self.r.pixel(30, 20) == (0, 0, 255)))
        self.assertEqual((self.slots()["bytes"], self.slots()["gl-objects"]), (60 * 30 * 4, 1))
        self.g("clear", 1)
        self.assertTrue(self.r.wait(lambda: self.slots()["gl-objects"] == 0 and not self.slot(1)["loaded"]), self.r.stderr())
        self.assertEqual(self.slots()["bytes"], 0)
        self.assertEqual(self.r.pixel(30, 20), (0, 0, 0))
        self.assertFalse(self.slot(1)["on"])
        self.assertEqual(self.slot(1)["motion"], "none", "a clear puts the slot back to what it was")

    def test_the_motion_names_and_the_settings_are_kept_and_all_or_nothing(self):
        self.g("set", 3, {"motion": "crawl", "speed": 40, "seconds": 2.5, "out": 0.5, "loop": True, "opacity": 0.5, "x": 3, "y": 4})
        self.assertEqual(self.slot(3)["motion"], "crawl")
        for bad in ({"motion": "spin"}, {"speed": 0}, {"speed": 301}, {"seconds": -1}, {"seconds": 61}, {"opacity": 1.5}, {"x": 99999},
                    {"loop": 1}, {"colour": "red"}, {"motion": "fade", "speed": "fast"}, {}):
            with self.assertRaises(PlayerError, msg=bad):
                self.g("set", 3, bad)
        self.assertEqual(self.slot(3)["motion"], "crawl", "a refused set changed nothing, not even the valid half of it")
        for nan in ("NaN", "Infinity"):
            self.assertTrue(self.send_raw('{"command": ["pvj-graphic", "set", 3, {"x": %s}], "request_id": 1}' % nan).find("success") < 0)

    def send_raw(self, line):
        import socket
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(self.r.sock)
        s.sendall(line.encode() + b"\n")
        out = s.recv(4096).decode()
        s.close()
        return out


class RefusalTest(Base):
    def test_what_is_not_understood_is_refused_and_nothing_is_drawn(self):
        good = self.make(10, 10)
        for cmd in (("load", 0, good, 10, 10, 1), ("load", 9, good, 10, 10, 1), ("load", "1", good, 10, 10, 1),
                    ("load", 1, "g.bgra", 10, 10, 1), ("load", 1, good, 0, 10, 1), ("load", 1, good, 10, 10, 0),
                    ("load", 1, good, 10, 10, 7), ("load", 1, good, 10.5, 10, 1), ("load", 1, good, 10, 10),
                    ("load", 1, "/does/not/exist.bgra", 10, 10, 1), ("load", 1, os.path.dirname(good), 10, 10, 1),
                    ("show", 0), ("show", 9), ("show",), ("hide", 1, 2), ("spin", 1), ("set", 1), ("set", 1, 5), ("set", 1, [1])):
            with self.assertRaises(PlayerError, msg=cmd):
                self.g(*cmd)
        self.assertEqual(self.slots()["bytes"], 0)
        self.assertTrue(all(not x["loaded"] for x in self.slots()["list"]))

    def test_a_link_and_a_short_file_and_a_fifo_are_refused(self):
        good = self.make(10, 10)
        link = os.path.join(self.tmp, "link.bgra")
        os.symlink(good, link)
        with self.assertRaises(PlayerError):
            self.g("load", 1, link, 10, 10, 1)
        short = os.path.join(self.tmp, "short.bgra")
        with open(short, "wb") as f:
            f.write(b"\0" * (10 * 10 * 4 - 1))
        with self.assertRaises(PlayerError):
            self.g("load", 1, short, 10, 10, 1)
        fifo = os.path.join(self.tmp, "fifo.bgra")
        os.mkfifo(fifo)
        t0 = time.monotonic()
        with self.assertRaises(PlayerError):
            self.g("load", 1, fifo, 10, 10, 1)
        self.assertLess(time.monotonic() - t0, 3, "a FIFO is refused, not waited on")
        self.assertEqual(self.slots()["bytes"], 0)
        # a file longer than the picture is fine: the rest is not read
        long_ = os.path.join(self.tmp, "long.bgra")
        with open(long_, "wb") as f:
            f.write(RED_BGRA * 200)
        self.g("load", 1, long_, 10, 10, 1)

    def test_all_the_graphics_together_may_use_48_megabytes_and_no_more(self):
        self.g("load", 1, self.make(4096, 2048), 4096, 2048, 1)      # 32 MB
        self.g("load", 2, self.make(4096, 1024), 4096, 1024, 1)      # 16 MB: exactly the limit
        s = self.slots()
        self.assertEqual(s["bytes"], 48 * 1024 * 1024)
        with self.assertRaises(PlayerError) as e:
            self.g("load", 3, self.make(64, 64), 64, 64, 1)
        self.assertIn("memory", str(e.exception))
        with self.assertRaises(PlayerError):
            self.g("load", 1, self.make(4096, 4096), 4096, 4096, 1)   # 64 MB by itself
        self.assertEqual(self.slots()["bytes"], 48 * 1024 * 1024, "a refused load changed nothing")
        self.g("load", 1, self.make(100, 100), 100, 100, 1)           # a smaller picture in slot 1 makes room
        self.g("load", 3, self.make(64, 64), 64, 64, 1)
        self.assertTrue(self.r.wait(lambda: all(self.slot(n)["loaded"] for n in (1, 2, 3)), 20), self.r.stderr())
        self.assertEqual(self.slots()["bytes"], 100 * 100 * 4 + 4096 * 1024 * 4 + 64 * 64 * 4)
        for n in (1, 2, 3):
            self.g("clear", n)
        self.assertTrue(self.r.wait(lambda: self.slots()["gl-objects"] == 0 and self.slots()["bytes"] == 0, 10))


class SmallTextureTest(Base):
    ENV = {"PVJ_RENDER_TEST_MAX_TEXTURE": "256"}

    def setUp(self):
        need_program(self)
        if not hooks_built():
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without TEST_HOOKS=1")
            self.skipTest("pvj-render was built without TEST_HOOKS=1")
        super().setUp()

    def test_a_picture_wider_than_the_node_can_hold_is_refused_in_words(self):
        self.assertEqual(self.r("get_property", "pvj-render")["graphics-side"], 256)
        self.assertEqual(self.slots()["side"], 256)
        self.g("load", 1, self.make(256, 40), 256, 40, 1)
        with self.assertRaises(PlayerError) as e:
            self.g("load", 2, self.make(257, 40), 257, 40, 1)
        self.assertIn("larger than this node can hold", str(e.exception))
        with self.assertRaises(PlayerError):
            self.g("load", 2, self.make(40, 257), 40, 257, 1)
        self.assertFalse(self.slot(2)["loaded"] or self.slot(2)["waiting"])


class FadeTest(Base):
    def alphas(self, n, seconds, step=0.05):
        out, t0 = [], time.monotonic()
        while time.monotonic() - t0 < seconds:
            out.append((time.monotonic() - t0, self.slot(n)["alpha"]))
            time.sleep(step)
        return out

    def test_a_fade_in_rises_by_the_clock_to_full_and_the_pixels_follow(self):
        self.put(1, 100, 60, 50, 50, WHITE_BGRA, motion="fade", seconds=1.5)
        self.g("show", 1)
        got = self.alphas(1, 2.2)
        values = [a for _, a in got]
        self.assertEqual(values[-1], 1.0)
        mid = [a for t, a in got if 0.2 < t < 1.2]
        self.assertTrue(any(0.05 < a < 0.95 for a in mid), "it rose, not jumped: %s" % got)
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(values, values[1:])), "never down: %s" % values)
        r, g, b = self.r.pixel(80, 70)
        self.assertGreater(r, 250)

    def test_a_fade_out_keeps_the_picture_on_and_then_takes_it_off_and_stops_drawing_it(self):
        self.put(1, 100, 60, 50, 50, WHITE_BGRA, motion="fade", seconds=0.2, out=1.5)
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: self.slot(1)["alpha"] == 1.0))
        self.g("hide", 1)
        t0 = time.monotonic()
        got = self.alphas(1, 2.3)
        values = [a for _, a in got]
        self.assertTrue(any(0.05 < a < 0.95 for a in values), values)
        self.assertTrue(all(b <= a + 1e-6 for a, b in zip(values, values[1:])), "never up: %s" % values)
        self.assertEqual(values[-1], 0.0)
        self.assertEqual(self.r.pixel(80, 70), (0, 0, 0))
        d = self.slot(1)["draws"]
        time.sleep(0.4)
        self.assertEqual(self.slot(1)["draws"], d, "no longer drawn once the fade is out")
        self.assertTrue(self.slot(1)["loaded"], "the picture is still held until it is cleared")

    def test_hidden_halfway_through_a_fade_in_goes_down_from_where_it_was(self):
        self.put(1, 100, 60, 50, 50, WHITE_BGRA, motion="fade", seconds=4.0, out=4.0)
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: 0.2 < self.slot(1)["alpha"] < 0.5, 5))
        self.g("hide", 1)
        a0 = self.slot(1)["alpha"]
        time.sleep(0.3)
        a1 = self.slot(1)["alpha"]
        self.assertLessEqual(a1, a0 + 0.01)
        self.assertGreater(a1, 0.0)
        self.assertLess(a1, 0.5, "not back up to where the fade in was going")

    def test_a_replay_starts_the_fade_again_and_a_show_of_a_shown_one_does_not(self):
        self.put(1, 100, 60, 50, 50, WHITE_BGRA, motion="fade", seconds=0.3)
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: self.slot(1)["alpha"] == 1.0))
        self.g("show", 1)
        time.sleep(0.1)
        self.assertEqual(self.slot(1)["alpha"], 1.0, "show does not restart a graphic that is on")
        self.g("set", 1, {"seconds": 4.0})
        self.g("replay", 1)
        self.assertTrue(self.r.wait(lambda: self.slot(1)["alpha"] < 0.5), "replay: from the start")

    def test_opacity_multiplies_everything(self):
        self.put(1, 100, 60, 50, 50, WHITE_BGRA, opacity=0.5)
        self.g("show", 1)
        self.drawn(1)
        self.assertAlmostEqual(self.slot(1)["alpha"], 0.5, places=2)
        r, g, b = self.r.pixel(80, 70)
        self.assertTrue(abs(r - 128) <= 3, r)


class TypewriterTest(Base):
    def two_lines(self, w=200, h=60):
        """Line 1 (the top half) is white across the whole width; line 2 across the left half."""
        path = os.path.join(self.tmp, "type.bgra")
        bgra(path, w, h, lambda x, y: WHITE_BGRA if (y < h // 2 and x >= 0) or (y >= h // 2 and x < w // 2) else bytes(4))
        return path

    def test_it_reveals_line_after_line_along_the_ink_and_never_takes_anything_back(self):
        path = self.two_lines()
        self.g("load", 1, path, 200, 60, 2)
        self.g("set", 1, {"x": 40, "y": 60, "motion": "typewriter", "seconds": 3.0})
        self.loaded(1)
        self.g("show", 1)
        seen_partial_first, last, full_at = False, 0, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 6:
            shot = self.shot()
            w, h, px = shot
            row_top = [px[((60 + 5) * w + 40 + x) * 3] > 128 for x in range(200)]
            row_bot = [px[((60 + 45) * w + 40 + x) * 3] > 128 for x in range(100)]
            n_top, n_bot = sum(row_top), sum(row_bot)
            self.assertGreaterEqual(n_top + n_bot, last, "nothing is taken back")
            last = n_top + n_bot
            if 0 < n_top < 195:
                seen_partial_first = True
                self.assertEqual(n_bot, 0, "the second line waits for the first")
                # a wipe from the left: the lit part is a run that starts at the left edge (up to the soft edge)
                self.assertTrue(row_top[0] and not row_top[-1])
            if n_top >= 199 and 0 < n_bot < 99:
                self.assertTrue(row_bot[0])
            if n_top + n_bot >= 299:
                full_at = time.monotonic() - t0
                break
            time.sleep(0.05)
        self.assertTrue(seen_partial_first, "the first line was seen part way")
        self.assertIsNotNone(full_at, "it was all there at the end")
        self.assertGreater(full_at, 1.5, "paced by its 3 seconds")
        self.assertLess(full_at, 5.5)
        time.sleep(0.3)
        self.assertEqual(self.r.pixel(40 + 199, 60 + 5), (255, 255, 255), "and stays")

    def test_a_replay_types_it_again_from_nothing(self):
        path = self.two_lines()
        self.g("load", 1, path, 200, 60, 2)
        self.g("set", 1, {"x": 40, "y": 60, "motion": "typewriter", "seconds": 0.4})
        self.loaded(1)
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(40 + 199, 60 + 5) == (255, 255, 255), 5))
        self.g("set", 1, {"seconds": 6.0})
        self.g("replay", 1)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(40 + 199, 60 + 5) == (0, 0, 0), 3), "the end of the line is not there")
        self.assertTrue(self.r.wait(lambda: self.r.pixel(40 + 2, 60 + 5) == (255, 255, 255), 5), "the start is")

    def test_a_picture_with_one_line_and_a_blank_band_do_not_divide_by_zero(self):
        self.put(1, 100, 30, 10, 10, bytes(4), motion="typewriter", seconds=1.0)         # nothing but transparent pixels
        self.g("show", 1)
        time.sleep(0.5)
        self.assertEqual(self.r("get_property", "pid"), self.r.proc.pid)
        path = os.path.join(self.tmp, "three.bgra")
        bgra(path, 90, 90, lambda x, y: WHITE_BGRA if y < 30 or y >= 60 else bytes(4))      # the middle line is blank
        self.g("load", 2, path, 90, 90, 3)
        self.g("set", 2, {"x": 100, "y": 10, "motion": "typewriter", "seconds": 0.5})
        self.loaded(2)
        self.g("show", 2)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(100 + 80, 10 + 80) == (255, 255, 255), 5), "the last line arrives")


class MoveTest(Base):
    def rows(self, shot):
        return [y for _, y in self.lit(shot, lambda r, g, b: r > 128)]

    def cols(self, shot):
        return [x for x, _ in self.lit(shot, lambda r, g, b: r > 128)]

    def test_a_scroll_goes_up_the_screen_from_below_to_above_and_is_done(self):
        # speed 100: a screen's height a second; the picture 20 high, so 200 pixels in 200 / 180 s
        self.put(1, 40, 20, 100, 0, motion="scroll", speed=100)
        self.g("show", 1)
        tops, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 2.2:
            rows = self.rows(self.shot())
            tops.append(min(rows) if rows else None)
            time.sleep(0.03)
        seen = [t for t in tops if t is not None]
        self.assertGreaterEqual(len(seen), 3, tops)
        self.assertTrue(all(b <= a for a, b in zip(seen, seen[1:])), "only up: %s" % seen)
        self.assertGreater(seen[0] + 20, H - 80, "it came in from below")
        self.assertIsNone(tops[-1], "it has run off the top and is not drawn again")
        self.assertTrue(all(c in range(100, 140) for c in self.cols(self.shot())) )
        self.assertFalse(self.slot(1)["draws"] == 0)

    def test_a_crawl_goes_left_and_a_loop_comes_round_again(self):
        self.put(1, 60, 20, 0, 80, motion="crawl", speed=100, loop=True)        # a screen's width a second
        self.g("show", 1)
        xs, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 3.5:
            cols = self.cols(self.shot())
            xs.append(min(cols) if cols else None)
            time.sleep(0.03)
        seen = [x for x in xs if x is not None]
        self.assertGreaterEqual(len(seen), 4, xs)
        falling = sum(1 for a, b in zip(seen, seen[1:]) if b <= a)
        self.assertGreaterEqual(falling, 0.8 * (len(seen) - 1), "mostly left: %s" % seen)
        # a pass takes (320 + 60) / 320 = 1.2 s; a jump back to the right edge is the next pass
        jumps = sum(1 for a, b in zip(seen, seen[1:]) if b > a + 150)
        self.assertGreaterEqual(jumps, 1, "looped: %s" % xs)
        # the row is the one it was put on
        self.assertTrue(all(80 <= y < 100 for y in self.rows(self.shot())))

    def test_a_crawl_that_is_not_looped_goes_once(self):
        self.put(1, 60, 20, 0, 80, motion="crawl", speed=200)
        self.g("show", 1)
        self.assertTrue(self.r.wait(lambda: self.cols(self.shot()), 3), "seen")
        self.assertTrue(self.r.wait(lambda: not self.cols(self.shot()), 4), "gone")
        time.sleep(1.5)
        self.assertEqual(self.cols(self.shot()), [], "and not back")
        self.g("replay", 1)
        self.assertTrue(self.r.wait(lambda: self.cols(self.shot()), 3), "a replay sends it across again")

    def test_a_graphic_off_the_screen_is_not_drawn(self):
        self.put(1, 40, 20, 5000, 10)
        self.g("show", 1)
        time.sleep(0.4)
        self.assertEqual(self.slot(1)["draws"], 0)
        self.g("set", 1, {"x": -100, "y": 10})
        time.sleep(0.4)
        self.assertEqual(self.slot(1)["draws"], 0)
        self.g("set", 1, {"x": -20, "y": 10})
        self.drawn(1)
        self.assertEqual(self.r.pixel(5, 15), (255, 0, 0))


class PaceTest(Base):
    def rate(self, seconds=2.0):
        a, t0 = self.r("get_property", "pvj-render"), time.monotonic()
        time.sleep(seconds)
        b, t1 = self.r("get_property", "pvj-render"), time.monotonic()
        return (b["flips"] - a["flips"]) / (t1 - t0)

    def test_four_graphics_one_of_them_a_crawl_keep_the_output_rate(self):
        self.put(1, 120, 30, 10, 10, WHITE_BGRA, motion="fade", seconds=1.0)
        self.put(2, 80, 80, 200, 50, RED_BGRA)
        self.put(3, 300, 20, 0, 150, WHITE_BGRA, motion="crawl", speed=40, loop=True)
        self.put(4, 100, 40, 100, 100, WHITE_BGRA, lines=2, motion="typewriter", seconds=1.5)
        for n in (1, 2, 3, 4):
            self.g("show", n)
        flips = self.rate(3.0)
        self.assertGreater(flips, 0.85 * 60, "flips a second with four graphics: %.1f\n%s" % (flips, self.r.stderr()))
        self.assertLessEqual(flips, 62)
        self.assertEqual(self.r("get_property", "pvj-render")["missed-vblanks"] < 30, True)


class ManyClientsTest(Base):
    """Four clients at once, each on its own socket, change the same slots as fast as they can for a few seconds: no
    answer is wrong, the renderer stays up, and when they have all cleared what they made nothing is left."""
    SECONDS = 4.0

    def test_clients_at_once_leave_nothing_behind(self):
        picture = self.make(120, 40)
        other = self.make(300, 50, WHITE_BGRA)
        before = (fd_count(self.r.proc.pid), thread_count(self.r.proc.pid))
        stop = time.monotonic() + self.SECONDS
        errors, counts = [], []

        def worker(seed):
            rng = random.Random(seed)
            ipc = Ipc(self.r.sock, timeout=10.0)
            n = 0
            try:
                while time.monotonic() < stop:
                    slot = rng.randint(1, 3)
                    op = rng.choice(("load", "load", "set", "show", "hide", "replay", "clear", "read", "bad"))
                    try:
                        if op == "load":
                            p, w, h = rng.choice(((picture, 120, 40), (other, 300, 50)))
                            ipc.request("pvj-graphic", "load", slot, p, w, h, rng.randint(1, 2))
                        elif op == "set":
                            ipc.request("pvj-graphic", "set", slot, {"motion": rng.choice(("none", "fade", "typewriter", "scroll", "crawl")),
                                                                     "x": rng.randint(-50, 300), "y": rng.randint(-20, 200),
                                                                     "seconds": rng.random() * 2, "out": rng.random(), "loop": rng.random() < 0.5})
                        elif op == "read":
                            s = ipc.request("get_property", "pvj-graphics")
                            if s["bytes"] > s["bytes-max"]:
                                errors.append("bytes over the limit: %s" % s["bytes"])
                        elif op == "bad":
                            try:
                                ipc.request("pvj-graphic", "load", slot, "/nope", 10, 10, 1)
                                errors.append("a bad load was taken")
                            except PlayerError:
                                pass
                        else:
                            ipc.request("pvj-graphic", op, slot)
                    except PlayerError as e:
                        errors.append("%s: %s" % (op, e))
                    n += 1
            except Exception as e:      # noqa: BLE001
                errors.append(repr(e))
            counts.append(n)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(self.SECONDS + 20)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertGreater(sum(counts), 100, counts)
        self.assertIsNone(self.r.proc.poll(), "still up\n" + self.r.stderr())
        for n in range(1, 9):
            self.g("clear", n)
        self.assertTrue(self.r.wait(lambda: self.slots()["gl-objects"] == 0 and self.slots()["bytes"] == 0, 10), self.slots())
        self.assertFalse(any(x["waiting"] or x["loaded"] or x["on"] for x in self.slots()["list"]))
        time.sleep(0.5)
        after = (fd_count(self.r.proc.pid), thread_count(self.r.proc.pid))
        self.assertLessEqual(after[0], before[0] + 1, (before, after))
        self.assertLessEqual(after[1], before[1] + 1, (before, after))
        self.assertEqual(self.r.pixel(10, 10), (0, 0, 0))

    def test_two_loads_of_one_slot_at_the_same_moment_leave_one_picture_and_the_right_count(self):
        a = self.make(100, 100)
        b = self.make(200, 50)
        ipcs = [Ipc(self.r.sock, timeout=10.0) for _ in range(2)]
        for round_ in range(40):
            ts = [threading.Thread(target=lambda i=i, p=p, w=w, h=h: ipcs[i].request("pvj-graphic", "load", 1, p, w, h, 1))
                  for i, (p, w, h) in enumerate(((a, 100, 100), (b, 200, 50)))]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
            self.assertTrue(self.r.wait(lambda: not self.slot(1)["waiting"] and self.slot(1)["loaded"], 5))
            s = self.slot(1)
            self.assertIn((s["width"], s["height"]), ((100, 100), (200, 50)))
            self.assertEqual(self.slots()["bytes"], s["width"] * s["height"] * 4)
        self.assertEqual(self.slots()["gl-objects"], 1)


class PlayerSideTest(Base):
    """pvj/player.py's side: how the panel finds the stage and speaks to it (graphics_stage and graphic)."""

    def player(self):
        p = Player(rundir=self.tmp)
        p.socket_path = self.r.sock
        p.ipc = Ipc(self.r.sock, timeout=10.0)
        return p

    def test_the_player_finds_the_stage_once_and_speaks_to_it(self):
        p = self.player()
        self.assertIsNone(p.graphics_stage(ask=False), "nothing known yet, nothing asked")
        st = p.graphics_stage()
        self.assertEqual(st["slots"], 8)
        self.assertGreaterEqual(st["side"], 1024)
        self.assertEqual(p.graphics_stage(ask=False), st)
        p.graphic("load", 1, self.make(30, 30), 30, 30, 1)
        p.graphic("set", 1, {"x": 5, "y": 5, "motion": "fade", "seconds": 0.2})
        p.graphic("show", 1)
        self.assertTrue(self.r.wait(lambda: self.slot(1)["draws"] > 2, 5), self.r.stderr())
        with self.assertRaises(PlayerError) as e:
            p.graphic("load", 9, self.make(30, 30), 30, 30, 1)
        self.assertIn("invalid parameter", str(e.exception))
        p.graphic("clear", 1)

    def fake(self, answers):
        calls = []

        class FakeIpc:
            def request(self, *command):
                calls.append(command)
                a = answers(command)
                if isinstance(a, Exception):
                    raise a
                return a
        p = Player(rundir=self.tmp)
        p.ipc = FakeIpc()
        return p, calls

    def test_classic_mpv_and_a_renderer_from_before_have_no_stage_and_are_asked_once(self):
        classic, calls = self.fake(lambda c: 7 if c[1] == "pid" else PlayerError("mpv: property not found"))
        self.assertIsNone(classic.graphics_stage())
        self.assertIsNone(classic.graphics_stage())
        self.assertEqual(len([c for c in calls if c[1] == "pvj-render"]), 1, "asked once for this process")
        old, calls = self.fake(lambda c: 7 if c[1] == "pid" else {"version": "0.10.0", "layers": 2, "program-fades": 0})
        self.assertIsNone(old.graphics_stage(), "a 0.10.0 renderer has no stage")
        self.assertIsNone(old.graphics_stage())
        self.assertEqual(len([c for c in calls if c[1] == "pvj-render"]), 1)
        odd, _ = self.fake(lambda c: 7 if c[1] == "pid" else {"graphics": True, "graphics-side": "big"})
        self.assertIsNone(odd.graphics_stage(), "words that are not numbers are not a stage")

    def test_a_player_that_does_not_answer_is_asked_again_and_a_new_player_is_asked_afresh(self):
        down = [True]
        pid = [7]

        def answers(c):
            if down[0]:
                return PlayerError("player is not running")
            return pid[0] if c[1] == "pid" else {"graphics": 8, "graphics-side": 4096}
        p, calls = self.fake(answers)
        self.assertIsNone(p.graphics_stage())
        self.assertIsNone(p.graphics_stage())
        self.assertEqual(len(calls), 2, "asked again, not remembered as no")
        down[0] = False
        self.assertEqual(p.graphics_stage(), {"slots": 8, "side": 4096})
        pid[0] = 8
        p._note_pid(8)
        down[0] = True
        self.assertIsNone(p.graphics_stage(), "another mpv: what the last said does not hold")


if __name__ == "__main__":
    unittest.main()
