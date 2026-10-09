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
from tests.test_effects_gpu import CLIP, GPU, H, INVERT, ONLY, W, FxCase, differ, grid
from tests.test_server import ServerBase
from tests.test_transitions import png

FLAT = "av://lavfi:color=c=0x2060C0:size=320x180:rate=25"
BLUE = (28, 95, 195)            # what the player draws for it, give or take the conversion
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
            t = self.real.ipc.request("get_property", "time-pos")
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
        self.assertTrue(self.tr.hold("crossfade"), self.tr.last)
        self.assertIs(self.real.ipc.request("get_property", "pause"), True, "the outgoing clip is frozen for the still")
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the still is not the screen: %s" % (d,))
        self.under()
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the new clip shows through a still that is whole: %s" % (d,))
        self.tr.run(1.5)
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], BLUE, "a clip to a clip")
        after = self.still(flat=True)
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(after)), "the new clip is not alone on the screen at the end")
        self.assertEqual(self.tr.given_up, "")
        self.nothing_left()

    def test_blackout_takes_the_still_away_at_once(self):
        self.assertTrue(self.tr.hold("crossfade"))
        self.under()
        self.tr.run(3.0)
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
        self.assertTrue(self.tr.hold("crossfade"))
        d = differ(self.shot(), mirrored)
        self.assertLessEqual(d[0], 3, "the still was drawn through the mapping's shader a second time: %s" % (d,))
        self.under()
        self.tr.run(0.6)
        self.watch()
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(self.still(flat=True))))
        self.real.set_shaders([])

    def test_an_effect_is_in_the_still_and_stays_on_under_it(self):
        self.fx.upload("invert.fs", INVERT)
        self.put("invert.fs")
        old = self.still()
        self.assertTrue(self.tr.hold("crossfade"))
        d = differ(self.shot(), old)
        self.assertLessEqual(d[0], 3, "the still is not the filtered picture: %s" % (d,))
        self.under()
        self.tr.run(0.8)
        seen = self.watch()
        inverted = tuple(255 - c for c in BLUE)
        self.through(seen, old[H // 2][W // 2], inverted, "under an effect")
        self.assertTrue(self.loaded() and self.loaded()[0].startswith("effect-"), self.loaded())
        self.assertGreater(far(self.still(flat=True)[H // 2][W // 2], BLUE), 150, "the effect is not on the new clip")
        self.fx.off()

    def test_a_play_from_the_panel_to_a_still_picture(self):
        """The whole way, through the panel's own play: the Mix setting, a file from the media folder, and a picture
        as what comes. A picture is one frame: the player draws nothing more by itself, and every step must show."""
        with open(os.path.join(self.media, "red.png"), "wb") as f:
            f.write(png(320, 180, (200, 40, 30)))
        self.settings.data["mix"] = T.stored("crossfade", 1.5)
        old = self.still()
        self.assertEqual(self.api.play({"file": "red.png"}, None, "t"), {"playing": "red.png"})
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], (200, 40, 30), "the panel's play, to a still picture")
        self.assertLessEqual(far(self.still(flat=True)[H // 2][W // 2], (200, 40, 30)), 24)
        self.assertEqual(self.api.status({}, None, "t")["mix"]["transition"], "crossfade")
        self.assertNotIn("fallback", self.api.status({}, None, "t")["mix"])
        self.nothing_left()

    def test_a_frozen_clip_is_blended_from(self):
        with open(os.path.join(self.media, "red.png"), "wb") as f:
            f.write(png(320, 180, (200, 40, 30)))
        self.settings.data["mix"] = T.stored("crossfade", 0.8)
        self.real.pause(True)
        old = self.still()
        self.api.play({"file": "red.png"}, None, "t")
        seen = self.watch()
        self.through(seen, old[H // 2][W // 2], (200, 40, 30), "from a frozen clip")
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
                    os.write(fd, bytes([150, 60, 150, 190]) * (320 * 180 // 2))
                    time.sleep(0.04)
            except OSError:
                pass
            finally:
                os.close(fd)

        thread = threading.Thread(target=writer, daemon=True)
        self.addCleanup(thread.join, 3)
        self.addCleanup(stop.set)
        self.assertTrue(self.tr.hold("crossfade"))
        thread.start()
        self.real.play_pipe(fifo, 320, 180, 25)
        self.tr.run(0.8)
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
        self.assertTrue(self.tr.hold("crossfade"), self.tr.last)
        held = self.shot()
        self.assertLessEqual(differ(self.shot(), held)[0], 3, "the still of a generator moves")
        self.assertGreater(len(set(grid(held, 9))), 12, "the still of a generator is no picture")
        self.under()
        self.assertIsNone(self.real.source_shader)
        self.tr.run(0.6)
        self.watch()
        self.assertTrue(all(far(p, BLUE) <= 30 for p in grid(self.still(flat=True))))

    def test_a_player_that_dies_during_it_leaves_nothing_behind(self):
        self.assertTrue(self.tr.hold("crossfade"))
        self.under()
        self.tr.run(5.0)
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
