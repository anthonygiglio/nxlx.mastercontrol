# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The crossfade (pvj/transitions.py, D71) on a real mpv that really draws: the rig of tests/test_effects_gpu.py (mpv's
GPU output in a window on a virtual display, Mesa's software GPU), judged on screenshots of the window.

CI's three effects-gpu jobs run these: tests/test_effects_gpu.py, the module the workflow names, loads this one when
it is run. Run alone:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m tests.test_transitions_gpu

What is held here: the still is the screen (with an effect, with a shader at the mapping's stage, over a generator),
it stays whole until the new clip's first frame (a pipe that opens late), the picture then goes from the old to the
new one through mixes of the two, over a clip and over a still picture, Blackout takes it away, and a player that
dies leaves nothing behind. A software GPU and a 320 x 180 window: nothing here says how many steps a real board
makes, or what a still of a full screen costs it.
"""
import os
import shutil
import sys
import threading
import time
import unittest

from pvj import transitions as T
from pvj.player import PlayerError
from tests.test_effects_gpu import CLIP, GPU, H, INVERT, ONLY, W, FxCase, differ, grid
from tests.test_server import ServerBase
from tests.test_transitions import png

FLAT = "av://lavfi:color=c=0x2060C0:size=320x180:rate=25"
BLUE = (28, 95, 195)            # what the player draws for it, give or take the conversion
PICTURE = (20, 200, 220)       # the still picture that comes: far from the middle of the test clip, which is about (187, 121, 75)
YELLOW_CLIP = "av://lavfi:color=c=0xF0E010:size=320x180:rate=25"
YELLOW = (240, 224, 16)         # for the wipes and slides: unlike every part of the test clip (its right half is near the blue)
MIRROR = "//!HOOK OUTPUT\n//!BIND HOOKED\n//!DESC mirror\nvec4 hook() { return HOOKED_tex(vec2(1.0 - HOOKED_pos.x, HOOKED_pos.y)); }\n"


def far(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


class CrossCase(FxCase):
    def setUp(self):
        super().setUp()
        self.tr = self.api.transitions
        self.tr.log = lambda line: print("transition, ES %s: %s" % (self.ES, line))

    def middle(self):
        return self.shot()[H // 2][W // 2]

    def under(self, url=FLAT):
        """Start `url` under the still, as the panel's play does, and wait until it plays."""
        self.real.play([url], windowed=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                t = self.real.ipc.request("get_property", "time-pos")
            except PlayerError as e:        # right after a load the player has no position yet: not started, ask again
                if "unavailable" not in str(e):
                    raise
                t = None
            if isinstance(t, (int, float)) and t > 0.2:
                return
            time.sleep(0.05)

    def watch(self, wait=12.0):
        """The middle of the screen, again and again, until the transition is over."""
        seen, deadline = [], time.monotonic() + wait
        while self.tr.running is not None and time.monotonic() < deadline:
            seen.append(self.middle())
        self.assertIsNone(self.tr.running, "the transition never ended: %s" % self.tr.last)
        return seen

    def nothing_left(self):
        left = [n for n in os.listdir(self.rundir) if n.startswith("transition-") or n == "overlay-%d.bgra" % T.OVERLAY_ID]
        self.assertEqual(left, [])

    def through(self, seen, old, new, what):
        """The looks go from the old middle to the new one and never back, and at least one is a mix of the two."""
        whole = far(old, new)
        gone = [far(p, old) for p in seen]
        print("crossfade, ES %s: %s: %d looks, still %d ms, %d steps in %.2f s, %d dropped; distance from the old picture %s"
              % (self.ES, what, len(seen), self.tr.last.get("still_ms", -1), self.tr.last.get("steps", -1), self.tr.last.get("seconds", -1),
                 self.tr.last.get("dropped", -1), gone))
        self.assertGreater(whole, 150, "the two pictures are too alike to judge")
        self.assertTrue(all(b >= a - 12 for a, b in zip(gone, gone[1:])), "the picture went back towards the old one: %s" % gone)
        self.assertTrue(any(0.12 * whole < d < 0.88 * whole for d in gone), "no look was a mix of the two pictures: %s of %d" % (gone, whole))
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertGreaterEqual(self.tr.last["steps"], 4)

    def test_the_still_holds_the_old_picture_and_the_new_clip_comes_through_it(self):
        old = self.still()
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        self.assertIs(self.real.ipc.request("get_property", "pause"), True, "the outgoing clip is frozen for the still")
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the still is not the screen: %s" % (d,))
        self.under()
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the new clip shows through a still that is whole: %s" % (d,))
        self.tr.run(self.token, 1.5)
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], BLUE, "a clip to a clip")
        after = self.still(flat=True)
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(after)), "the new clip is not alone on the screen at the end")
        self.assertEqual(self.tr.given_up, "")
        self.nothing_left()

    def test_blackout_takes_the_still_away_at_once(self):
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        self.under()
        self.tr.run(self.token, 3.0)
        time.sleep(0.4)
        self.api.blackout({"on": True}, None, "t")
        dark = self.still(flat=True)
        self.assertLessEqual(max(max(p) for p in grid(dark)), 8, "a Blackout left light on the screen")
        self.assertIsNone(self.tr.running)
        time.sleep(0.3)                                             # and no step comes after it
        self.assertLessEqual(max(max(p) for p in grid(self.shot())), 8)
        self.api.blackout({"on": False}, None, "t")
        self.nothing_left()

    def test_a_shader_at_the_mappings_stage_is_in_the_still_once(self):
        path = os.path.join(self.rundir, "mirror.glsl")
        with open(path, "w") as f:
            f.write(MIRROR)
        plain = self.still()
        self.real.set_shaders([path])                   # the mapping's layer of the player's shader list
        mirrored = self.still()
        self.assertGreater(differ(mirrored, plain)[1], 20, "the mirror did nothing: the test would prove nothing")
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        d = differ(self.shot(), mirrored)
        self.assertLessEqual(d[0], 3, "the still was drawn through the mapping's shader a second time: %s" % (d,))
        self.under()
        self.tr.run(self.token, 0.6)
        self.watch()
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(self.still(flat=True))))
        self.real.set_shaders([])

    def test_an_effect_is_in_the_still_and_stays_on_under_it(self):
        self.fx.upload("invert.fs", INVERT)
        self.put("invert.fs")
        old = self.still()
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the still is not the filtered picture: %s" % (d,))
        self.under()
        self.tr.run(self.token, 0.8)
        seen = self.watch()
        inverted = tuple(255 - c for c in BLUE)
        self.through(seen, old[H // 2][W // 2], inverted, "under an effect")
        self.assertTrue(self.loaded() and self.loaded()[0].startswith("effect-"), self.loaded())
        self.assertGreater(far(self.still(flat=True)[H // 2][W // 2], BLUE), 150, "the effect is not on the new clip")
        self.fx.off()

    def test_a_play_from_the_panel_to_a_still_picture(self):
        """The whole way, through the panel's own play: the Mix setting, a file from the media folder, and a picture
        as what comes. A picture is one frame: the player draws nothing more by itself, and every step must show."""
        with open(os.path.join(self.media, "cyan.png"), "wb") as f:
            f.write(png(320, 180, PICTURE))
        self.settings.data["mix"] = T.stored("crossfade", 1.5)
        old = self.still()
        self.assertEqual(self.api.play({"file": "cyan.png"}, None, "t"), {"playing": "cyan.png"})
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], PICTURE, "the panel's play, to a still picture")
        self.assertLessEqual(far(self.still(flat=True)[H // 2][W // 2], PICTURE), 24)
        self.assertEqual(self.api.status({}, None, "t")["mix"]["transition"], "crossfade")
        self.assertNotIn("fallback", self.api.status({}, None, "t")["mix"])
        self.nothing_left()

    def test_after_stop_the_clip_just_starts(self):
        """On a box the player always runs; after Stop it has nothing loaded, and there is nothing to take a still of."""
        with open(os.path.join(self.media, "cyan.png"), "wb") as f:
            f.write(png(320, 180, PICTURE))
        self.settings.data["mix"] = T.stored("crossfade", 0.8)
        self.api.control({"action": "stop"}, None, "t")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.real.ipc.request("get_property", "idle-active") is not True:
            time.sleep(0.05)
        self.assertTrue(self.api.player.status().get("running"), "the player runs with nothing loaded: the case this test is for")
        lines = []
        self.tr.log = lines.append
        self.api.play({"file": "cyan.png"}, None, "t")
        self.assertIsNone(self.tr.running)
        self.assertEqual(lines, [], "the first play after a Stop wrote a line about a still that failed")
        self.assertIs(self.real.ipc.request("get_property", "pause"), False)
        self.assertLessEqual(far(self.still(flat=True)[H // 2][W // 2], PICTURE), 24)

    def test_through_the_mapper_itself(self):
        """The real projection mapping, switched on through its engine, and the panel's play: the still shows the
        surface where it is, and the surface then shows the new picture."""
        with open(os.path.join(self.media, "cyan.png"), "wb") as f:
            f.write(png(320, 180, PICTURE))
        self.settings.data["mix"] = T.stored("crossfade", 1.2)
        self.api.registry.set_enabled("mapper", True)
        self.api.mapper.handle({"action": "add", "type": "quad"})
        self.api.mapper.handle({"action": "on", "on": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.api.mapper.state()["status"]["state"] != "on":
            time.sleep(0.1)
        self.assertEqual(self.api.mapper.state()["status"]["state"], "on")
        old = self.still()
        self.assertEqual(old[4][4], (0, 0, 0), "outside the surface")
        self.api.play({"file": "cyan.png"}, None, "t")
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], PICTURE, "under the projection mapping")
        after = self.still(flat=True)
        self.assertEqual(after[4][4], (0, 0, 0), "the still left something outside the surface")
        self.assertLessEqual(far(after[H // 2][W // 2], PICTURE), 24)
        self.api.mapper.handle({"action": "on", "on": False})

    def test_an_effect_put_on_during_it_changes_the_new_clip_and_the_still_goes_on(self):
        self.fx.upload("invert.fs", INVERT)
        old = self.still()
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        self.under()
        self.tr.run(self.token, 3.0)
        time.sleep(0.3)
        self.put("invert.fs")                           # the player sets its renderer up anew under the still
        self.assertEqual(self.tr.running, "crossfade", "putting an effect on ended the transition: %s" % self.tr.last)
        mid = self.middle()
        self.assertLess(far(mid, old[H // 2][W // 2]), 0.8 * far(old[H // 2][W // 2], tuple(255 - c for c in BLUE)), "the still was gone after the effect went on")
        self.watch()
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertGreater(far(self.still(flat=True)[H // 2][W // 2], BLUE), 150, "the effect is not on the new clip")
        self.fx.off()

    def test_a_frozen_clip_is_blended_from(self):
        with open(os.path.join(self.media, "cyan.png"), "wb") as f:
            f.write(png(320, 180, PICTURE))
        self.settings.data["mix"] = T.stored("crossfade", 0.8)
        self.real.pause(True)
        old = self.still()
        self.api.play({"file": "cyan.png"}, None, "t")
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], PICTURE, "from a frozen clip")
        self.assertIs(self.real.ipc.request("get_property", "pause"), False)

    def test_the_still_stays_whole_until_a_slow_sources_first_frame(self):
        """A pipe nobody writes into for a second and a half, as a stream that takes its time: the steps must not
        begin when the play was asked for."""
        old = self.still()
        fifo = os.path.join(self.tmp, "in.fifo")
        os.mkfifo(fifo)
        wrote, stop = [], threading.Event()

        def writer():
            time.sleep(1.5)
            fd, deadline = None, time.monotonic() + 10
            while fd is None and time.monotonic() < deadline:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    time.sleep(0.05)
            if fd is None:
                return
            os.set_blocking(fd, True)
            wrote.append(time.monotonic())
            try:
                while not stop.is_set():
                    os.write(fd, bytes([40, 128, 40, 128]) * (320 * 180 // 2))        # a dark grey: far from the old picture
                    time.sleep(0.04)
            except OSError:
                pass
            finally:
                os.close(fd)

        thread = threading.Thread(target=writer, daemon=True)
        self.addCleanup(thread.join, 3)
        self.addCleanup(stop.set)
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        thread.start()
        self.real.play_pipe(fifo, 320, 180, 25)
        self.tr.run(self.token, 0.8)
        early = []
        while not wrote and len(early) < 200:
            before = time.monotonic()
            rows = self.shot()
            if not wrote:
                early.append((before, differ(rows, old)[0]))
        self.assertGreaterEqual(len(early), 5, "the pipe was written before anything could be looked at")
        self.assertLessEqual(max(d for _, d in early), 3, "the still changed before the new picture's first frame: %s" % early)
        seen = self.watch()
        new = self.middle()
        print("crossfade, ES %s: a pipe that opens late: %d looks while it was waited for, all the old picture; then %s" % (self.ES, len(early), seen[-6:]))
        self.assertGreater(far(new, old[H // 2][W // 2]), 150, "the pipe's picture never came")
        self.assertEqual(self.tr.last["ended"], "done")

    def test_from_a_generator_shader_to_a_clip(self):
        sid = next(s["id"] for s in self.engine.library() if s["pack"] == "nxlx")
        self.assertTrue(self.engine.show(sid)["ok"])
        self.settle(0.4)
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        held = self.shot()
        self.assertLessEqual(differ(self.shot(), held)[0], 3, "the still of a generator moves")
        self.assertGreater(len(set(grid(held, 9))), 12, "the still of a generator is no picture")
        self.under()
        self.assertIsNone(self.real.source_shader)
        self.tr.run(self.token, 0.6)
        self.watch()
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(self.still(flat=True))))

    def half_way(self, name, new, old_at, old_from):
        """Lay the still for `name`, start the flat clip under it and draw the transition at its half: the point
        `new` (of "left", "right", "top", "bottom": the middle of that half of the screen) must show the new clip and
        the point `old_at` what the old picture had at `old_from`. Then let it run to its end."""
        at = {"left": (W // 4, H // 2), "right": (3 * W // 4, H // 2), "top": (W // 2, H // 4), "bottom": (W // 2, 3 * H // 4)}
        self.play(CLIP)
        old = self.still()
        token = self.tr.hold(name)
        self.assertTrue(token, "%s: %s %s" % (name, self.tr.last, self.tr.given_up))
        whole = differ(self.shot(), old)
        self.assertLessEqual(whole[0], 3, "%s: the still is not the screen: %s" % (name, whole))
        self.under(YELLOW_CLIP)
        self.assertEqual(self.tr._draw(token, 0.5), "drawn", name)
        time.sleep(0.15)
        rows = self.shot()

        def px(where, of=rows):
            x, y = at[where]
            return of[y][x]
        print("%s, ES %s: half way: %s is %s (the new clip is about %s), %s is %s (the old picture had %s at %s)"
              % (name, self.ES, new, px(new), YELLOW, old_at, px(old_at), px(old_from, old), old_from))
        self.assertLessEqual(far(px(new), YELLOW), 30, "%s: at its half the %s of the screen is not the new clip" % (name, new))
        self.assertLessEqual(far(px(old_at), px(old_from, old)), 18, "%s: at its half the %s of the screen is not the old picture's %s" % (name, old_at, old_from))
        self.assertGreater(far(px(old_at), YELLOW), 60)
        self.tr.run(token, 0.5)
        self.watch()
        self.assertEqual((self.tr.last["ended"], self.tr.given_up), ("done", ""), name)
        self.assertGreaterEqual(self.tr.last["steps"], 4, name)
        print("%s, ES %s: %d steps in %.2f s, %d dropped" % (name, self.ES, self.tr.last["steps"], self.tr.last["seconds"], self.tr.last["dropped"]))
        self.assertTrue(all(far(p, YELLOW) <= 36 for p in grid(self.still(flat=True))), "%s: the new clip is not alone at the end" % name)
        self.nothing_left()

    def test_a_wipe_at_its_half_shows_the_new_clip_on_the_side_it_comes_from(self):
        # the still stays where it is and is cut away: the other half still shows what the old picture had there
        for name, new, old in (("wipe-from-left", "left", "right"), ("wipe-from-right", "right", "left"),
                               ("wipe-from-top", "top", "bottom"), ("wipe-from-bottom", "bottom", "top")):
            self.half_way(name, new, old, old)

    def test_a_slide_at_its_half_shows_the_old_picture_moved_by_half_a_screen(self):
        # the still moves off: the half it still covers shows what the old picture had in its other half
        for name, new, old_at, old_from in (("slide-left", "right", "left", "right"), ("slide-right", "left", "right", "left"),
                                            ("slide-up", "bottom", "top", "bottom"), ("slide-down", "top", "bottom", "top")):
            self.half_way(name, new, old_at, old_from)

    def test_a_wipe_through_the_panels_play_and_blackout_in_the_middle(self):
        with open(os.path.join(self.media, "cyan.png"), "wb") as f:
            f.write(png(320, 180, PICTURE))
        self.settings.data["mix"] = T.stored("wipe-from-left", 3.0)
        old = self.still()
        self.api.play({"file": "cyan.png"}, None, "t")
        time.sleep(1.5)
        rows = self.shot()
        self.assertLessEqual(far(rows[H // 2][8], PICTURE), 24, "the left edge is not the new picture half way through a wipe from the left")
        self.assertLessEqual(far(rows[H // 2][W - 8], old[H // 2][W - 8]), 18, "the right edge is not the old picture")
        self.api.blackout({"on": True}, None, "t")
        self.assertLessEqual(max(max(p) for p in grid(self.still(flat=True))), 8, "a Blackout left light on the screen")
        self.assertIsNone(self.tr.running)
        self.api.blackout({"on": False}, None, "t")
        self.nothing_left()

    def test_a_player_that_dies_during_it_leaves_nothing_behind(self):
        self.token = self.tr.hold("crossfade")
        self.assertTrue(self.token, self.tr.last)
        self.under()
        self.tr.run(self.token, 5.0)
        time.sleep(0.5)
        self.real.stop()
        deadline = time.monotonic() + 5
        while self.tr.running is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.tr.running, "the transition went on without a player")
        self.assertIn("the player", self.tr.last["ended"])
        self.nothing_left()


for _name in dir(FxCase):                     # the effects' own tests run from their module, not here again
    if _name.startswith("test_"):
        setattr(CrossCase, _name, None)

if ONLY != "desktop":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesCrossfadeTest(CrossCase, ServerBase):
        ES = "yes"


if ONLY != "gles":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class DesktopGlCrossfadeTest(CrossCase, ServerBase):
        ES = "no"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    ran = result.testsRun - len(result.skipped)
    print("crossfade GPU tests: %d run, %d skipped" % (ran, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() and ran > 0 and not result.skipped else 1)
