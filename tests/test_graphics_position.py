# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Where a graphic lands (D129): every place the selector offers, on both drawing paths, after every motion.

Anthony found on 2026-11-09 that the place selector "doesn't align properly" after a crawl. No stale offset was found in
the renderer, the engine or the panel (these tests hold that: with the engine and the real renderer, headless, every one
of the nine places lands exactly where `graphics.position` says, fresh and after a crawl, a scroll, a fade and a typewriter,
while a crawl or a scroll runs, and after Replay). What was found is that a scroll or a crawl that has run off the screen
shows nothing when the place is changed, and that a crawl ignores the grid's column (and the nudge across) and a scroll its
row (and the nudge down) with nothing said: a change of place now sends them across again, and the panel says it
(tests/ui/panel.test.js).

The pictures are plain blocks from the stand-in helper, so the lit rectangle is exactly the picture. The renderer is run at
two sizes (the second has the proportions of 1080p and 720p outputs; `--final=720` is tested in tests/test_pvj_render.py).
"""
import time

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import graphics
from pvj.player import Ipc
from tests.test_graphics import Base
from tests.test_graphics_motion import MotionBase
from tests.test_pvj_render import Renderer, need_program


def lit_box(shot):
    """(x0, y0, x1, y1) of the pixels the picture lit (the stand-in's blocks are dark red, 0x30), or None."""
    w, h, px = shot
    x0, x1, y0, y1 = w, 0, h, 0
    for y in range(h):
        row = px[y * w * 3:(y + 1) * w * 3:3]
        if max(row) > 20:
            y0, y1 = min(y0, y), max(y1, y + 1)
            x0 = min(x0, next(i for i, v in enumerate(row) if v > 20))
            x1 = max(x1, w - next(i for i, v in enumerate(reversed(row)) if v > 20))
    return (x0, y0, x1, y1) if y1 else None


class GpuPlaceTest(Base):
    SIZES = ("320x180", "1280x720")

    def start(self, size):
        need_program(self)
        self.r = Renderer("--headless=" + size)
        self.addCleanup(self.r.close)
        self.size = tuple(int(v) for v in size.split("x"))
        ipc = Ipc(self.r.sock, timeout=10.0)
        p = self.api.player
        p.osd_size = lambda: (self.r("get_property", "osd-width"), self.r("get_property", "osd-height"))
        p.graphics_stage = lambda ask=True: {"slots": 8, "side": 4096}
        p.graphic = lambda what, slot, *a: ipc.request("pvj-graphic", what, slot, *a)

    def want(self, gid, anchor):
        it = next(i for i in graphics.items_of(self.api.settings.data["graphics"]) if i["id"] == gid)
        b = self.gfx._built[gid]
        x, y = graphics.position(anchor, it["place"]["x"], it["place"]["y"], b["w"], b["h"], *self.size)
        return x, y, x + b["w"], y + b["h"]

    def settle(self, gid, anchor):
        """The picture for this place is built and loaded in the renderer."""
        end = time.monotonic() + 20            # (generous: a slow runner builds at five a second at most)
        while time.monotonic() < end:
            b = self.gfx._built.get(gid)
            if b and (b["x"], b["y"]) == self.want(gid, anchor)[:2]:
                slots = self.r("get_property", "pvj-graphics")["list"]
                if slots[0]["loaded"] and not slots[0]["waiting"] and slots[0]["width"] == b["w"] and slots[0]["height"] == b["h"]:
                    time.sleep(0.15)
                    return
            time.sleep(0.05)
        self.fail("the picture did not get to the renderer: built %s, renderer %s, status %s; wanted %s at %s; work %s, failed %s, worker alive %s" % (
            self.gfx._built.get(gid), self.r("get_property", "pvj-graphics")["list"][0], self.gfx._status.get(gid), self.want(gid, anchor),
            anchor, self.gfx._work, self.gfx._failed, self.gfx._thread is not None and self.gfx._thread.is_alive()))

    def sweep(self, gid, tag):
        for a in graphics.ANCHORS:
            self.post({"id": gid, "set": {"place": {"anchor": a}}})
            self.settle(gid, a)
            self.assertEqual(lit_box(self.r.capture()), self.want(gid, a), "%s: place %s at %s" % (tag, a, self.size))

    def test_every_place_lands_where_it_says_fresh_and_after_each_motion(self):
        self.gfx.synchronous = False            # the real worker: this is what the node runs
        self.addCleanup(self.gfx.stop)
        for size in self.SIZES[:1]:
            self.start(size)
            gid = self.add(text="THANKS", on=True)
            self.sweep(gid, "fresh")
            for kind in ("crawl", "scroll", "fade", "typewriter", "crawl"):
                self.post({"id": gid, "set": {"motion": {"kind": kind, "speed": 100, "loop": False, "seconds": 0.3}}})
                time.sleep(1.4)                 # a scroll or a crawl at 100 percent has run across and off by now
                self.post({"id": gid, "set": {"motion": {"kind": "none"}}})
                self.sweep(gid, "after " + kind)
            self.post({"id": gid, "set": {"place": {"anchor": "tl", "x": 10, "y": 10}}})
            self.settle(gid, "tl")
            self.assertEqual(lit_box(self.r.capture()), self.want(gid, "tl"), "with a nudge")

    def test_at_the_proportions_of_720p_too(self):
        self.gfx.synchronous = False
        self.addCleanup(self.gfx.stop)
        self.start(self.SIZES[1])
        gid = self.add(text="THANKS", on=True)
        self.sweep(gid, "fresh")
        self.post({"id": gid, "set": {"motion": {"kind": "crawl", "speed": 100}}})
        time.sleep(1.8)
        self.post({"id": gid, "set": {"motion": {"kind": "none"}}})
        self.sweep(gid, "after a crawl")

    def test_while_a_crawl_runs_the_row_is_the_place_and_while_a_scroll_runs_the_column(self):
        self.gfx.synchronous = False
        self.addCleanup(self.gfx.stop)
        self.start(self.SIZES[0])
        gid = self.add(text="THANKS", on=True)
        for kind, axis in (("crawl", "rows"), ("scroll", "columns")):
            self.post({"id": gid, "set": {"motion": {"kind": kind, "speed": 40, "loop": True}}})
            for a in graphics.ANCHORS:
                self.post({"id": gid, "set": {"place": {"anchor": a}}})
                self.settle(gid, a)
                want = self.want(gid, a)
                seen = [lit_box(self.r.capture()) for _ in range(6)]
                seen = [s for s in seen if s]
                self.assertTrue(seen, "%s at %s was on the screen" % (kind, a))
                for s in seen:
                    if axis == "rows":
                        self.assertEqual((s[1], s[3]), (want[1], want[3]), "a crawl at %s keeps its row" % a)
                    else:
                        self.assertEqual((s[0], s[2]), (want[0], want[2]), "a scroll at %s keeps its column" % a)
            self.post({"id": gid, "replay": True})
            time.sleep(0.3)
            self.post({"id": gid, "set": {"motion": {"kind": "none"}}})
            self.sweep(gid, "after Replay of a " + kind)


class LostRequestTest(MotionBase):
    """The cause found on the way (2026-11-09): a change that came while the worker was finishing a pass for the same graphic
    was forgotten with that pass's work, so the picture stayed at its old place until the next change."""

    def test_a_change_made_during_a_pass_is_looked_at_by_the_next_pass(self):
        gid = self.add(on=True)
        self.gfx.synchronous = False
        self.gfx._kick = lambda: None                 # the test is the worker
        self.gfx._stop.set()
        real = self.gfx._cfg
        state = {"first": True}

        def cfg():
            out = real()
            if state["first"]:
                state["first"] = False                # a tap on a place arrives after the pass has read the settings
                with self.api.settings.lock:
                    self.api.settings.data["graphics"]["items"][0]["place"]["anchor"] = "tl"
                self.gfx._after({gid})
            return out
        self.gfx._cfg = cfg
        self.gfx._after({gid})
        self.gfx.process(final=True)
        self.assertTrue(self.gfx.busy(), "the request made during the pass is still owed")
        for _ in range(6):
            if self.gfx.process(final=True) is None:
                break
        b = self.gfx._built[gid]
        self.assertEqual((b["x"], b["y"]), graphics.position("tl", 0, 0, b["w"], b["h"], 1920, 1080), "and the next pass draws the picture at its new place")
        self.assertFalse(self.gfx.busy())


class MotionChosenTest(Base):
    """Choosing a crawl, or a scroll that is not looped, for a graphic that is already on the screen shows it crossing once
    (the first version drew nothing: the new motion was measured from the old one's clock, long past its end)."""

    def start(self, size="320x180"):
        need_program(self)
        self.r = Renderer("--headless=" + size)
        self.addCleanup(self.r.close)
        ipc = Ipc(self.r.sock, timeout=10.0)
        p = self.api.player
        p.osd_size = lambda: (self.r("get_property", "osd-width"), self.r("get_property", "osd-height"))
        p.graphics_stage = lambda ask=True: {"slots": 8, "side": 4096}
        p.graphic = lambda what, slot, *a: ipc.request("pvj-graphic", what, slot, *a)
        self.gfx.synchronous = False
        self.addCleanup(self.gfx.stop)

    def frames_with_ink(self, seconds):
        seen, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            seen.append(lit_box(self.r.capture()))
        return seen

    def test_a_crawl_chosen_on_a_shown_graphic_crosses_the_screen_once(self):
        self.start()
        gid = self.add(text="THANKS", on=True)
        time.sleep(1.5)                             # well past any pass of a motion
        self.assertIsNotNone(lit_box(self.r.capture()), "it is there, still")
        self.post({"id": gid, "set": {"motion": {"kind": "crawl", "speed": 100, "loop": False}}})
        seen = self.frames_with_ink(2.2)
        inked = [s for s in seen if s]
        self.assertGreater(len(inked), 3, "it crawled across (frames with ink: %d of %d)" % (len(inked), len(seen)))
        lefts = [s[0] for s in inked]
        start = next((i for i, x in enumerate(lefts) if x > 250), None)           # (the first frames may still show the graphic where it sat)
        self.assertIsNotNone(start, "it came in from the right edge: %s" % lefts)
        lefts = lefts[start:]
        self.assertTrue(all(b <= a + 1 for a, b in zip(lefts, lefts[1:])), "and went left: %s" % lefts)
        self.assertIsNone(seen[-1], "and is gone once it has crossed (not looped)")

    def test_a_scroll_chosen_on_a_shown_graphic_rises_once(self):
        self.start()
        gid = self.add(text="THANKS", on=True)
        time.sleep(1.5)
        self.post({"id": gid, "set": {"motion": {"kind": "scroll", "speed": 100, "loop": False}}})
        seen = self.frames_with_ink(2.2)
        inked = [s for s in seen if s]
        self.assertGreater(len(inked), 3, "it rose (frames with ink: %d of %d)" % (len(inked), len(seen)))
        tops = [s[1] for s in inked]
        self.assertTrue(all(b <= a + 1 for a, b in zip(tops, tops[1:])), "and went up: %s" % tops)

    def test_a_looped_crawl_keeps_going_when_the_place_changes_and_does_not_jump_back(self):
        self.start()
        gid = self.add(text="THANKS", on=True)
        self.post({"id": gid, "set": {"motion": {"kind": "crawl", "speed": 30, "loop": True}}})
        time.sleep(1.0)
        for a in ("tl", "tc", "tr"):
            self.post({"id": gid, "set": {"place": {"anchor": a}}})
        time.sleep(1.0)
        lefts = [s[0] for s in self.frames_with_ink(1.0) if s]
        jumps = sum(1 for a, b in zip(lefts, lefts[1:]) if b > a + 30)
        self.assertLessEqual(jumps, 1, "only the loop's own turn at the edge, not one a tap: %s" % lefts)


class PlaceReplaysTest(MotionBase):
    def test_a_new_place_sends_a_moving_graphic_across_again_and_leaves_a_still_one_alone(self):
        gid = self.add(on=True)
        self.motion(gid, kind="crawl", speed=50)
        n = len(self.sent)
        self.assertEqual(self.post({"id": gid, "set": {"place": {"anchor": "tl"}}})[0], 200)
        self.assertIn(("replay", 1), [s[:2] for s in self.sent[n:]], "a crawl that had run off is shown at its new row")
        self.motion(gid, kind="scroll")
        n = len(self.sent)
        self.post({"id": gid, "set": {"place": {"anchor": "mc"}}})
        self.assertIn(("replay", 1), [s[:2] for s in self.sent[n:]])
        self.motion(gid, kind="fade")
        n = len(self.sent)
        self.post({"id": gid, "set": {"place": {"anchor": "tr"}}})
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]], "a fade is not played again for a move")
        self.motion(gid, kind="crawl")
        self.post({"id": gid, "on": False})
        n = len(self.sent)
        self.post({"id": gid, "set": {"place": {"anchor": "bl"}}})
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]], "nothing is replayed for a graphic that is off")

    def test_a_looped_one_is_not_sent_back_by_a_tap_or_a_slider(self):
        gid = self.add(on=True)
        self.motion(gid, kind="crawl", speed=50, loop=True)
        for place in ({"anchor": "tl"}, {"y": 5}, {"y": 6}, {"y": 7}):
            n = len(self.sent)
            self.post({"id": gid, "set": {"place": place}})
            self.assertNotIn("replay", [s[0] for s in self.sent[n:]], place)

    def test_the_nudge_a_motion_ignores_neither_rebuilds_nor_replays(self):
        gid = self.add(on=True)
        self.motion(gid, kind="crawl", speed=50)
        jobs, n = len(self.client.jobs), len(self.sent)
        self.post({"id": gid, "set": {"place": {"x": 20}}})
        self.assertEqual(len(self.client.jobs), jobs, "a crawl crosses the whole screen: across is not in the picture")
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]])
        self.post({"id": gid, "set": {"place": {"y": 20}}})
        self.assertGreater(len(self.client.jobs), jobs, "the height is")
        self.assertIn("replay", [s[0] for s in self.sent[n:]])
        self.motion(gid, kind="scroll")
        jobs, n = len(self.client.jobs), len(self.sent)
        self.post({"id": gid, "set": {"place": {"y": 30}}})
        self.assertEqual(len(self.client.jobs), jobs, "and a scroll's height is not")
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]])
        self.post({"id": gid, "set": {"place": {"x": 30}}})
        self.assertIn("replay", [s[0] for s in self.sent[n:]])
        self.motion(gid, kind="none")
        jobs = len(self.client.jobs)
        self.post({"id": gid, "set": {"place": {"y": 40}}})
        self.assertGreater(len(self.client.jobs), jobs, "with no motion both nudges are in the picture again")

    def test_choosing_a_scroll_or_a_crawl_on_a_graphic_that_is_on_starts_it(self):
        gid = self.add(on=True)
        n = len(self.sent)
        self.motion(gid, kind="crawl")
        self.assertIn(("replay", 1), [s[:2] for s in self.sent[n:]])
        n = len(self.sent)
        self.motion(gid, kind="crawl", speed=40)
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]], "the same motion with another speed does not restart it")
        self.motion(gid, kind="none")
        self.post({"id": gid, "on": False})
        n = len(self.sent)
        self.motion(gid, kind="scroll")
        self.assertNotIn("replay", [s[0] for s in self.sent[n:]], "a graphic that is off starts with its show")

    def test_the_preview_of_a_crawl_is_one_line_unfitted_on_its_row(self):
        gid = self.add(text="ONE\nTWO", on=True)
        self.motion(gid, kind="crawl")
        self.client.jobs.clear()
        self.gfx.preview(gid)
        first, second = [j[0] for j in self.client.jobs[-2:]]
        for params in (first, second):
            head, _, typed = params.partition("\nTEXT\n")
            self.assertEqual(typed, "ONE  TWO", "one line")
            self.assertNotIn("nofit 1", head.split("\n"), "a preview shows the line, it does not refuse it")
            self.assertIn("limit 4096 ", head)
        import re
        m = re.search(r"canvas (\d+) (\d+) (-?\d+) (-?\d+)", second)
        self.assertEqual(m.group(3), "0", "it starts at the edge of its run, not at the grid's column")
        self.motion(gid, kind="none")
        self.client.jobs.clear()
        self.gfx.preview(gid)
        self.assertIn("ONE\nTWO", self.client.jobs[-1][0], "any other motion previews the fitted picture")


class OverlayPlaceTest(MotionBase):
    """The classic path puts the picture at the same place, after a motion chosen on another node and after a stepped one."""
    GPU = False

    def test_every_place_is_the_same_x_and_y_as_the_gpu_path_after_a_chosen_crawl_and_a_fade(self):
        gid = self.add(on=True)
        for kind in ("fade", "typewriter"):
            self.motion(gid, kind=kind, seconds=0.5)
            for a in graphics.ANCHORS:
                self.assertEqual(self.post({"id": gid, "set": {"place": {"anchor": a}}})[0], 200)
                self.advance(2.0)
                b = self.gfx._built[gid]
                want = graphics.position(a, 0, 0, b["w"], b["h"], 1920, 1080)
                last = self.parts()[-1]
                self.assertEqual((last[3], last[4]), want, "%s at %s" % (kind, a))
                self.assertEqual((last[6], last[7]), (b["w"], b["h"]))
