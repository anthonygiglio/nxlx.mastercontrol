# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Motion for text and logos (D129, slice 3): the panel's side of the GPU stage and of the stepped path on classic mpv.

Two ways of drawing, one set of pictures. Where the player has the renderer's graphics stage (`Player.graphics_stage()`)
a graphic is a slot there: loaded once, then shown, hidden, replayed, with its motion set as numbers; the renderer moves
it. On classic mpv an overlay cannot move, so a fade and a typewriter are stepped here, STEP_HZ times a second, each
motion with an end. Held here, with the stand-in helper and a fake player (nothing here has run on a Pi or a GPU; the
renderer itself is tests/test_graphics_gpu.py):

* the GPU path's commands, in order and without waste: load, set, show; a changed motion is a set and not a new picture;
  a new line on the screen is a new picture and a replay; Replay; a fade out that clears the slot only when it is done;
  Hide all and a code on the screen cut at once; the renderer's own words when it refuses a picture;
* a crawl is made as one line and refused in words when it would have to be made smaller;
* the classic path: the stepped fade (in and out) and typewriter by a clock, bounded, stopping when the graphic is hidden,
  with the files they write cleaned up; scroll and crawl refused in words and drawn still when they were chosen before;
* a node that falls back to classic mid-show keeps the graphic on, still, and plays nothing twice.
"""
import os
import threading
import time
import unittest

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import graphics, pinscreen
from pvj.player import PlayerError
from tests import test_pinscreen
from tests.test_graphics import Base, wait


class MotionBase(Base):
    """A fake player that has (or has not) the renderer's stage, and a clock the test moves."""
    GPU = True

    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        self.gfx.clock = lambda: self.now[0]
        self.stage = {"slots": 8, "side": 4096} if self.GPU else None
        self.sent = []
        self.refuse = None
        player = self.api.player
        player.graphics_stage = lambda ask=True: self.stage

        def graphic(what, slot, *args):
            self.sent.append((what, slot) + args)
            if self.refuse and what in self.refuse:
                raise PlayerError("mpv: " + self.refuse[what])
        player.graphic = graphic

    def motion(self, gid, **kw):
        st, body, _ = self.post({"id": gid, "set": {"motion": kw}})
        self.assertEqual(st, 200, body)
        return body

    def kinds(self):
        return [(s[0], s[1]) for s in self.sent]

    def advance(self, seconds):
        self.now[0] += seconds
        return self.gfx.step(self.now[0])

    def rundir_files(self):
        return sorted(n for n in os.listdir(self.api.player.rundir) if n.startswith("graphic-"))


class GpuPathTest(MotionBase):
    def test_a_node_with_the_stage_is_drawn_by_it_and_the_panel_is_told_all_five_motions(self):
        st, body, _ = self.get()
        self.assertEqual((body["mode"], body["motions"], body["note"], body["stage"]), ("gpu", list(graphics.MOTIONS), "", {"slots": 8, "side": 4096}))
        self.add(on=True)
        self.assertEqual(self.parts(), [], "nothing goes through mpv's overlays")

    def test_load_set_show_in_that_order_and_the_slot_is_the_place_in_the_list(self):
        a = self.add(on=True)
        b = self.add(text="TWO", on=True)
        loads = [s for s in self.sent if s[0] == "load"]
        self.assertEqual([s[1] for s in loads], [1, 2])
        what, slot, path, w, h, lines = loads[0]
        self.assertTrue(path.startswith(self.api.player.rundir) and os.path.isfile(path))
        self.assertEqual((w, h, lines), (20 + 10 * len("NEXT UP: DJ MARA"), 60, 1))
        first = [k for k in self.kinds() if k[1] == 1]
        self.assertEqual(first[:3], [("load", 1), ("set", 1), ("show", 1)])
        self.assertEqual(self.item(a)["status"]["state"], "on")
        self.assertEqual(self.item(b)["status"]["state"], "on")
        cfg = next(s for s in self.sent if s[0] == "set" and s[1] == 1)[2]
        self.assertEqual(set(cfg), {"x", "y", "motion", "seconds", "out", "speed", "loop"})
        self.assertEqual((cfg["motion"], cfg["loop"]), ("none", False))

    def test_a_changed_motion_is_a_set_and_not_a_new_picture(self):
        gid = self.add(on=True)
        jobs, n = len(self.client.jobs), len(self.sent)
        self.motion(gid, kind="fade", seconds=2.5, out=1.0)
        new = self.sent[n:]
        self.assertEqual([s[0] for s in new], ["set"], new)
        self.assertEqual((new[0][2]["motion"], new[0][2]["seconds"], new[0][2]["out"]), ("fade", 2.5, 1.0))
        self.assertEqual(len(self.client.jobs), jobs, "no new picture for a new time")
        self.motion(gid, kind="scroll", speed=40, loop=True)
        last = self.sent[-1][2]
        self.assertEqual((last["motion"], last["speed"], last["loop"]), ("scroll", 40, True))
        self.assertEqual(len(self.client.jobs), jobs)

    def test_a_new_line_on_the_screen_is_a_new_picture_and_plays_the_motion_again(self):
        gid = self.add(on=True)
        self.motion(gid, kind="typewriter", seconds=2)
        n = len(self.sent)
        self.assertEqual(self.post({"id": gid, "text": "SECOND LINE\nAND A THIRD"})[0], 200)
        new = [s[:2] for s in self.sent[n:] if s[0] != "set"]       # (the wider line may sit somewhere else: a set between)
        self.assertEqual(new, [("load", 1), ("replay", 1)])
        self.assertEqual(self.sent[n][5], 2, "the typewriter is told it has two lines")
        n = len(self.sent)
        self.assertEqual(self.post({"id": gid, "text": "SECOND LINE\nAND A THIRD"})[0], 200)
        self.assertEqual(self.sent[n:], [], "the same words again: nothing is sent")

    def test_replay_plays_it_again_only_for_a_graphic_that_is_on(self):
        gid = self.add()
        st, body, _ = self.post({"id": gid, "replay": True})
        self.assertEqual(st, 400)
        self.assertIn("Show it first", body["error"])
        self.assertEqual(self.post({"id": gid, "on": True})[0], 200)
        jobs = len(self.client.jobs)
        n = len(self.sent)
        self.assertEqual(self.post({"id": gid, "replay": True})[0], 200)
        self.assertEqual([s[:2] for s in self.sent[n:]], [("replay", 1)])
        self.assertEqual(len(self.client.jobs), jobs, "no new picture to play again")
        self.assertEqual(self.post({"id": gid, "replay": 1})[0], 400)
        self.assertEqual(self.post({"replay": True})[0], 400, "which graphic?")
        self.post({"all": False})
        self.assertEqual(self.post({"id": gid, "replay": True})[0], 400, "the stage is off")

    def test_a_hide_without_a_fade_out_clears_the_slot_at_once_and_the_file_goes(self):
        gid = self.add(on=True)
        self.motion(gid, kind="fade", seconds=1, out=0)
        path = next(s for s in self.sent if s[0] == "load")[2]
        self.assertTrue(os.path.isfile(path))
        self.post({"id": gid, "on": False})
        self.assertEqual(self.kinds()[-1], ("clear", 1))
        self.assertFalse(os.path.exists(path))

    def test_a_fade_out_hides_now_and_clears_when_it_is_done_and_not_before(self):
        gid = self.add(on=True)
        self.motion(gid, kind="fade", seconds=1, out=2.0)
        path = next(s for s in self.sent if s[0] == "load")[2]
        n = len(self.sent)
        self.post({"id": gid, "on": False})
        self.assertEqual([s[:2] for s in self.sent[n:]], [("hide", 1)])
        self.assertTrue(os.path.isfile(path), "the picture stays while it fades")
        wait_for = self.advance(0.5)
        self.assertIsNotNone(wait_for, "a timer is waiting")
        self.assertNotIn("clear", [s[0] for s in self.sent[n:]])
        self.assertIsNone(self.advance(3.0), "and nothing is left to wait for")
        self.assertEqual([s[:2] for s in self.sent[n:]], [("hide", 1), ("clear", 1)])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.item(gid)["status"]["state"], "off")

    def test_shown_again_during_its_fade_out_is_not_cleared_by_the_timer(self):
        gid = self.add(on=True)
        self.motion(gid, kind="fade", seconds=1, out=2.0)
        self.post({"id": gid, "on": False})
        self.advance(0.5)
        n = len(self.sent)
        self.post({"id": gid, "on": True})
        self.assertIn(("show", 1), [s[:2] for s in self.sent[n:]])
        self.advance(5.0)
        self.assertNotIn("clear", [s[0] for s in self.sent[n:]])

    def test_hide_all_cuts_even_a_fade_out_and_a_removed_graphic_goes_at_once(self):
        a = self.add(on=True)
        self.motion(a, kind="fade", seconds=1, out=5.0)
        n = len(self.sent)
        self.post({"all": False})
        self.assertEqual([s[:2] for s in self.sent[n:]], [("clear", 1)])
        self.post({"id": a, "on": True})
        self.motion(a, kind="fade", seconds=1, out=5.0)
        n = len(self.sent)
        self.post({"id": a, "remove": True})
        self.assertEqual([s[:2] for s in self.sent[n:]], [("clear", 1)])

    def test_a_code_on_the_screen_clears_the_slots_and_the_pictures_come_back_without_being_made_again(self):
        gid = self.add(on=True)
        jobs = len(self.client.jobs)
        up = [True]
        self.api.access_on_screen = lambda: up[0]
        self.assertTrue(self.gfx.watch())
        self.assertEqual(self.kinds()[-1], ("clear", 1))
        self.assertEqual(self.item(gid)["status"]["state"], "held")
        up[0] = False
        n = len(self.sent)
        self.assertTrue(self.gfx.watch())
        self.assertEqual([s[:2] for s in self.sent[n:]], [("load", 1), ("set", 1), ("show", 1)])
        self.assertEqual(len(self.client.jobs), jobs)

    def test_moving_one_in_the_list_moves_the_slots(self):
        a = self.add(text="ONE", on=True)
        b = self.add(text="TWO", on=True)
        n = len(self.sent)
        self.assertEqual(self.post({"move": a, "dir": "down"})[0], 200)
        new = [s[:2] for s in self.sent[n:]]
        self.assertIn(("clear", 1), new)
        self.assertEqual([s for s in new if s[0] == "load"].count(("load", 1)), 1)
        self.assertEqual(sorted(s[1] for s in new if s[0] == "load"), [1, 2])
        self.assertEqual(self.item(a)["status"]["state"], "on")
        self.assertEqual(self.item(b)["status"]["state"], "on")

    def test_the_renderers_words_are_the_graphics_status_and_the_graphic_is_not_lost(self):
        self.refuse = {"load": "this picture is larger than this node can hold"}
        gid = self.add(on=True)
        it = self.item(gid)
        self.assertEqual(it["status"]["state"], "error")
        self.assertIn("larger than this node can hold", it["status"]["message"])
        self.refuse = None
        self.gfx.apply(restarted=True)
        self.assertEqual(self.item(gid)["status"]["state"], "on")

    def test_a_picture_bigger_than_the_chip_holds_is_refused_in_words(self):
        self.stage = {"slots": 8, "side": 100}
        gid = self.add(on=True)                      # the stand-in draws 300 x 60
        it = self.item(gid)
        self.assertEqual(it["status"]["state"], "error")
        self.assertIn("graphics chip", it["status"]["message"])
        self.assertEqual([s for s in self.sent if s[0] == "load"], [])


class TickerTest(MotionBase):
    def test_a_crawl_is_one_wide_line_that_may_not_be_made_smaller(self):
        gid = self.add(text="ONE\nTWO\nTHREE", on=True)
        self.client.jobs.clear()
        self.motion(gid, kind="crawl", speed=30)
        params, _ = self.client.jobs[-1]
        head, _, typed = params.partition("\nTEXT\n")
        self.assertEqual(typed, "ONE  TWO  THREE", "one line")
        self.assertIn("nofit 1", head.split("\n"))
        self.assertIn("limit 4096 ", head)
        loads = [s for s in self.sent if s[0] == "load"]
        self.assertEqual(loads[-1][5], 1, "one line for the typewriter's bands")
        self.assertEqual(len(self.client.jobs), 1, "one new picture: the ticker")

    def test_a_scroll_keeps_its_lines_and_the_ordinary_limits(self):
        gid = self.add(text="ONE\nTWO", on=True)
        self.client.jobs.clear()
        self.motion(gid, kind="scroll")
        self.assertEqual(self.client.jobs, [], "the same picture: only the motion differs")

    def test_a_ticker_that_is_too_long_says_so_in_the_helpers_words_and_is_not_asked_again(self):
        gid = self.add(on=True)
        self.client.fail = "Too long for this node's ticker at this size: shorten it or lower the size."
        self.motion(gid, kind="crawl")
        it = self.item(gid)
        self.assertEqual(it["status"]["state"], "error")
        self.assertIn("Too long for this node's ticker", it["status"]["message"])
        n = len(self.client.jobs)
        self.gfx.watch()
        self.gfx.process(final=True)
        self.assertEqual(len(self.client.jobs), n, "the same picture is not tried again")
        self.client.fail = None
        self.motion(gid, kind="fade")
        self.assertEqual(self.item(gid)["status"]["state"], "on", "a motion that needs no ticker draws it again")

    def test_a_logo_crawls_like_a_picture_it_is_not_made_into_a_line(self):
        gid = self.add("logo")
        self.client.jobs.clear()
        # (a logo needs a stored file; the request is what matters here)
        params = graphics.params_for(dict(graphics.new_item("logo", []), logo={"file": "x.png", "size": 30}), 1920, 1080, ticker=4096)[0]
        self.assertNotIn("nofit", params)
        self.assertIn("limit 4096 ", params)
        self.assertEqual(gid, self.get()[1]["items"][0]["id"])


class FallbackTest(MotionBase):
    def test_a_node_that_falls_back_to_classic_mid_show_keeps_the_graphic_still_and_plays_nothing_twice(self):
        gid = self.add(on=True)
        self.motion(gid, kind="fade", seconds=2, out=1)
        self.assertEqual(self.parts(), [])
        self.stage = None                       # the renderer went; classic mpv answered
        self.assertTrue(self.gfx.watch(), "the tick notices")
        parts = self.parts()
        self.assertEqual(len(parts), 1, "drawn through the overlay")
        self.assertEqual(parts[0][1], 20)
        self.assertTrue(parts[0][2].startswith(self.api.player.rundir))
        self.assertEqual(self.gfx.state()["mode"], "overlay")
        self.assertEqual(self.item(gid)["status"]["state"], "on")
        self.assertIsNone(self.advance(5.0), "no fade in is played again: it is simply there")
        self.assertEqual(len(self.parts()), 1)
        self.assertEqual(self.gfx._tasks, {})
        stills = [p for p in self.parts() if os.path.basename(p[2]).startswith("graphic-%s-" % gid)]
        self.assertTrue(stills)
        # and back
        self.stage = {"slots": 8, "side": 4096}
        n = len(self.sent)
        self.assertTrue(self.gfx.watch())
        self.assertEqual([s[:2] for s in self.sent[n:]][:1], [("load", 1)])
        self.assertIn(("show", 1), [s[:2] for s in self.sent[n:]])

    def test_the_motion_chosen_is_kept_when_the_node_cannot_draw_it_and_it_says_so(self):
        gid = self.add(on=True)
        self.motion(gid, kind="crawl", speed=20)
        self.stage = None
        self.gfx.watch()
        self.gfx.apply()
        it = self.item(gid)
        self.assertEqual(it["motion"]["kind"], "crawl", "still chosen")
        self.assertEqual(it["status"]["state"], "on")
        self.assertIn("Drawn still", it["status"]["message"])
        self.assertEqual(len(self.parts()), 1)
        self.assertEqual(self.client.jobs[-1][0].count("nofit"), 0, "and the picture drawn still is the ordinary one, not a ticker")


class OverlayPathTest(MotionBase):
    GPU = False

    def stepped(self, gid):
        """The overlay_part calls so far for graphic gid: (id, path, x, y, w, h)."""
        return [(c[1], c[2], c[3], c[4], c[6], c[7]) for c in self.parts()]

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_the_note_and_the_three_motions_of_this_path(self):
        st, body, _ = self.get()
        self.assertEqual((body["mode"], body["motions"], body["stage"]), ("overlay", ["none", "fade", "typewriter"], None))
        self.assertEqual(body["note"], "This node draws graphics without the New renderer: only None, Fade and Typewriter, with steps.")
        self.assertEqual(body["step_hz"], 12)

    def test_scroll_and_crawl_are_refused_in_words_here_and_nothing_changes(self):
        gid = self.add()
        for kind in ("scroll", "crawl"):
            st, body, _ = self.post({"id": gid, "set": {"motion": {"kind": kind}}})
            self.assertEqual(st, 400)
            self.assertIn("need the New renderer", body["error"])
            self.assertIn("only None, Fade and Typewriter, with steps", body["error"])
        self.assertEqual(self.item(gid)["motion"]["kind"], "none")
        self.assertEqual(self.post({"id": gid, "set": {"motion": {"kind": "typewriter", "seconds": 3}}})[0], 200)

    def test_a_fade_in_is_steps_that_rise_and_end_in_the_picture_itself(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=1.0)
        self.post({"id": gid, "on": True})
        first = self.parts()
        self.assertEqual(len(first), 1, "the first step is drawn at once")
        alphas = []
        for _ in range(12):
            self.advance(1.0 / 12)
            alphas.append(self.read(self.parts()[-1][2])[3])
        self.assertTrue(all(b >= a for a, b in zip(alphas, alphas[1:])), alphas)
        self.assertTrue(0 < alphas[3] < 255, alphas)
        self.assertEqual(alphas[-1], 255)
        files = self.rundir_files()
        self.assertEqual(len(files), 1, "the step files are gone: %s" % files)
        self.assertEqual(self.parts()[-1][2], os.path.join(self.api.player.rundir, files[0]), "the last step is the picture's own file")
        self.assertIsNone(self.advance(1.0), "and nothing is left running")
        self.assertEqual(self.gfx._tasks, {})
        self.assertLessEqual(len(self.parts()), 1 + 12 + 1, "about twelve steps a second, no more")

    def test_a_typewriter_shows_more_of_the_picture_each_step_from_nothing_to_all_of_it(self):
        gid = self.add(text="ONE\nTWO")
        self.motion(gid, kind="typewriter", seconds=2.0)
        self.post({"id": gid, "on": True})
        part = self.parts()[-1]
        w, h = part[6], part[7]
        stride = w * 4

        def counts():
            data = self.read(self.parts()[-1][2])
            return [sum(1 for x in range(w) if data[y * stride + x * 4 + 3]) for y in range(h)]
        first = counts()
        self.assertEqual(sum(first), 0, "nothing at first")
        seen = [0]
        for _ in range(24):
            self.advance(1.0 / 12)
            c = counts()
            self.assertGreaterEqual(sum(c), seen[-1], "never takes anything back")
            seen.append(sum(c))
        self.assertEqual(seen[-1], w * h, "all of it at the end")
        mid = seen[len(seen) // 2]
        self.assertTrue(0 < mid < w * h, seen)
        self.assertEqual(self.gfx._tasks, {})

    def test_the_lines_come_one_after_the_other(self):
        gid = self.add(text="ONE\nTWO")
        self.motion(gid, kind="typewriter", seconds=2.0)
        self.post({"id": gid, "on": True})
        w, h = self.parts()[-1][6:8]
        stride = w * 4
        self.advance(0.5)                                   # a quarter of the time: half of the first line
        data = self.read(self.parts()[-1][2])
        top = sum(1 for x in range(w) if data[2 * stride + x * 4 + 3])
        bottom = sum(1 for x in range(w) if data[(h - 2) * stride + x * 4 + 3])
        self.assertTrue(0 < top < w, top)
        self.assertEqual(bottom, 0, "the second line waits for the first")

    def test_hiding_during_the_motion_stops_it_and_takes_the_picture_off(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=4.0, out=0)
        self.post({"id": gid, "on": True})
        self.advance(0.5)
        self.assertTrue(self.gfx._tasks)
        self.post({"id": gid, "on": False})
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))
        self.assertEqual(self.gfx._tasks, {})
        self.assertIsNone(self.advance(1.0))
        self.assertEqual(self.rundir_files(), [], "no step file is left")

    def test_a_fade_out_is_steps_down_and_then_the_overlay_goes(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=0.5, out=1.0)
        self.post({"id": gid, "on": True})
        self.advance(1.0)
        self.assertEqual(self.gfx._tasks, {})
        self.post({"id": gid, "on": False})
        self.assertNotEqual(self.overlays()[-1][0], "overlay_remove", "still there: it is fading")
        alphas = []
        for _ in range(12):
            self.advance(1.0 / 12)
            if self.overlays()[-1][0] == "overlay_remove":
                break
            alphas.append(self.read(self.parts()[-1][2])[3])
        self.assertTrue(all(b <= a for a, b in zip(alphas, alphas[1:])), alphas)
        self.assertGreater(alphas[0], alphas[-1])
        self.advance(1.0)
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))
        self.assertEqual(self.rundir_files(), [])
        self.assertIsNone(self.advance(1.0))
        self.assertEqual(self.item(gid)["status"]["state"], "off")

    def test_shown_again_during_a_fade_out_comes_back_up_from_where_it_was(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=1.0, out=2.0)
        self.post({"id": gid, "on": True})
        self.advance(2.0)
        self.post({"id": gid, "on": False})
        self.advance(1.0)                                   # half way out
        low = self.read(self.parts()[-1][2])[3]
        self.post({"id": gid, "on": True})
        self.assertNotEqual(self.overlays()[-1][0], "overlay_remove")
        first = self.read(self.parts()[-1][2])[3]
        self.assertLessEqual(abs(first - low), 8, "from the level it had, not from nothing")
        self.advance(2.0)
        self.assertEqual(self.read(self.parts()[-1][2])[3], 255)

    def test_a_new_line_types_again_and_a_changed_size_does_not_play_the_motion_again(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=1.0)
        self.post({"id": gid, "on": True})
        self.advance(2.0)
        n = len(self.parts())
        self.post({"id": gid, "text": "A NEW LINE"})
        self.assertGreater(len(self.parts()), n)
        self.assertTrue(self.gfx._tasks, "a new line fades in again")
        self.advance(2.0)
        n = len(self.parts())
        self.assertEqual(self.post({"id": gid, "set": {"text": {"size": 20}}})[0], 200)
        self.assertEqual(len(self.parts()), n + 1)
        self.assertEqual(self.gfx._tasks, {}, "a different size is drawn at once, still")
        self.assertEqual(self.read(self.parts()[-1][2])[3], 255)

    def test_a_picture_too_large_to_step_is_shown_at_once(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=1.0)
        old = graphics.STEP_MAX_BYTES
        graphics.STEP_MAX_BYTES = 100
        try:
            self.post({"id": gid, "on": True})
        finally:
            graphics.STEP_MAX_BYTES = old
        self.assertEqual(self.gfx._tasks, {})
        self.assertEqual(len(self.parts()), 1)
        self.assertEqual(self.read(self.parts()[0][2])[3], 255)

    def test_the_pictures_being_stepped_together_are_bounded(self):
        old = graphics.STEP_TOTAL_BYTES
        graphics.STEP_TOTAL_BYTES = 2 * (80 * 60 * 4) + 10            # (the stand-in draws "LINE 0" at 80 x 60)
        ids = []
        try:
            for i in range(3):
                gid = self.add(text="LINE %d" % i)
                self.motion(gid, kind="fade", seconds=5.0)
                ids.append(gid)
            for gid in ids:
                self.post({"id": gid, "on": True})
        finally:
            graphics.STEP_TOTAL_BYTES = old
        self.assertEqual(len(self.gfx._tasks), 2, "the third is shown at once")

    def test_a_code_on_the_screen_stops_the_steps_and_takes_the_overlay_off(self):
        gid = self.add()
        self.motion(gid, kind="typewriter", seconds=4.0)
        self.post({"id": gid, "on": True})
        self.assertTrue(self.gfx._tasks)
        up = [True]
        self.api.access_on_screen = lambda: up[0]
        self.gfx.watch()
        self.assertEqual(self.overlays()[-1][0], "overlay_remove")
        self.assertEqual(self.gfx._tasks, {})
        self.assertIsNone(self.advance(1.0))
        n = len(self.parts())
        self.advance(1.0)
        self.assertEqual(len(self.parts()), n, "nothing is drawn while the code is up")
        up[0] = False
        self.gfx.watch()
        self.assertEqual(len(self.parts()), n + 1, "and it comes back, still (it was typing: it does not start again)")

    def test_a_player_that_stops_answering_during_a_motion_ends_the_motion_and_says_so(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=4.0)
        self.post({"id": gid, "on": True})
        player = self.api.player

        def broken(*a, **k):
            raise PlayerError("player is not running")
        player.overlay_part = broken
        self.advance(0.5)
        self.assertEqual(self.gfx._tasks, {})
        self.assertIn("did not take a step", self.item(gid)["status"]["message"])
        self.assertIsNone(self.advance(1.0))


class SteppingThreadTest(MotionBase):
    """The real thread, on a real clock, for a short fade: it steps, ends by itself, and stop() ends it."""
    GPU = False

    def setUp(self):
        super().setUp()
        self.gfx.synchronous = False
        self.gfx.clock = time.monotonic

    def test_the_thread_steps_a_short_fade_to_its_end_and_idles(self):
        gid = self.add()
        self.motion(gid, kind="fade", seconds=0.6)
        self.post({"id": gid, "on": True})
        self.assertTrue(wait(lambda: self.gfx._ticker is not None and self.gfx._ticker.is_alive(), 5))
        self.assertTrue(wait(lambda: not self.gfx._tasks and len(self.parts()) >= 4, 10), (len(self.parts()), self.gfx._tasks))
        count = len(self.parts())
        self.assertLessEqual(count, 12 * 0.6 + 6, "about twelve steps a second: %d" % count)
        time.sleep(0.5)
        self.assertEqual(len(self.parts()), count, "it stopped by itself")
        self.assertEqual(self.read_alpha(), 255)
        self.assertTrue(self.gfx.stop(5))
        self.assertFalse(self.gfx._ticker.is_alive())

    def read_alpha(self):
        with open(self.parts()[-1][2], "rb") as f:
            return f.read(4)[3]

    def test_no_thread_is_started_by_a_graphic_that_has_no_motion(self):
        self.add(on=True)
        wait(lambda: not self.gfx.busy(), 5)
        self.assertIsNone(self.gfx._ticker)
        self.assertTrue(self.gfx.stop(5))
        self.assertEqual([t.name for t in threading.enumerate() if t.name == "graphics-motion"], [])


class PinScreenTellsTest(unittest.TestCase):
    """The pin screen tells the graphics the moment a code goes up or comes down, with none of its own locks held (the
    graphics ask it back), instead of leaving them to the 2 second tick."""

    def test_show_and_hide_call_watch_at_once_outside_the_lock(self):
        api, auth = test_pinscreen.Api(), test_pinscreen.Auth()
        auth.list_joins = lambda: []
        p = pinscreen.PinScreen(api, auth, log=lambda *_: None, hostname="h")
        seen = []

        class Spy:
            def watch(self):
                free = []
                t = threading.Thread(target=lambda: free.append(p._lock.acquire(timeout=2) and (p._lock.release() or True)))
                t.start()
                t.join(5)
                seen.append(bool(free and free[0]))
        api.gfx = Spy()
        p.show(["pin"], seconds=30)
        p.hide()
        self.assertEqual(seen, [True, True])

    def test_a_graphics_engine_that_fails_does_not_fail_the_screen(self):
        api, auth = test_pinscreen.Api(), test_pinscreen.Auth()
        auth.list_joins = lambda: []
        logged = []
        p = pinscreen.PinScreen(api, auth, log=logged.append, hostname="h")

        class Broken:
            def watch(self):
                raise RuntimeError("boom")
        api.gfx = Broken()
        p.show(["pin"], seconds=30)
        self.assertTrue(any("boom" in line for line in logged))
