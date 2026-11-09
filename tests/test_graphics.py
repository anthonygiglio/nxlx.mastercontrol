# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Text and logos on the screen (D129, slice 2 of design/text-and-logo-sources_2026-11-08.md): pvj/graphics.py, the
routes, who may use them, OSC, the fonts' licences and the files that build the helper.

Everything here runs without the real helper (a stand-in draws a plain block); the real program's text and logo jobs are
in tests/test_raster.py (RealGraphicsTest, with PVJ_RASTER_TEST=1). Nothing here has run on a Pi. Every wait is bounded."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import api as api_mod, graphics, osc, policy, raster
from tests.test_raster import FakeClient, png
from tests.test_roles import RolesBase
from tests.test_server import ServerBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCTET = {"Content-Type": "application/octet-stream", "X-PVJ-Request": "1"}


def wait(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakeDrawing(FakeClient):
    """Stands where raster.Client does and draws a plain block per graphic, remembering every request."""

    def __init__(self):
        super().__init__()
        self.jobs, self.fail, self.hold = [], None, None
        self.lock = threading.Lock()

    def graphic(self, params, payload=b""):
        with self.lock:
            self.jobs.append((params, payload))
        if self.hold is not None:
            self.hold.wait(10)
        if self.fail:
            raise raster.RasterError(422, self.fail)
        want_png = "out png" in params
        w, h = (300, 60) if "kind text" in params else (120, 80)
        if "kind text" in params:
            text = params.split("\nTEXT\n", 1)[1]
            w = 20 + 10 * max(len(line) for line in text.split("\n"))
        canvas = re.search(r"canvas (\d+) (\d+)", params)
        if want_png:
            data = png(int(canvas.group(1)), int(canvas.group(2))) if canvas else png(w, h)
            return {"w": w, "h": h, "missing": 0, "data": data, "png": True}
        return {"w": w, "h": h, "missing": 0, "data": b"\x10\x20\x30\xff" * (w * h), "png": False}


class Base(RolesBase):
    def setUp(self):
        super().setUp()
        self.client = FakeDrawing()
        self.api.graphics.client = self.client
        self.gfx = self.api.gfx
        self.gfx.synchronous = True
        self.addCleanup(self.gfx.stop)
        self.op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]

    def post(self, body, token="op"):
        return self.call("POST", "/api/graphics", body, token=self.op if token == "op" else token)

    def get(self, token="op"):
        return self.call("GET", "/api/graphics", token=self.op if token == "op" else token)

    def add(self, kind="text", text="NEXT UP: DJ MARA", on=False, **extra):
        st, body, _ = self.post({"add": kind})
        self.assertEqual(st, 200, body)
        gid = body["items"][-1]["id"]
        if kind == "text" and text:
            self.assertEqual(self.post({"id": gid, "text": text})[0], 200)
        if on:
            st, body, _ = self.post({"id": gid, "on": True})
            self.assertEqual(st, 200, body)
        return gid

    def overlays(self):
        return [c for c in self.api.player.calls if c[0] in ("overlay_part", "overlay_remove")]

    def parts(self):
        return [c for c in self.api.player.calls if c[0] == "overlay_part"]

    def item(self, gid):
        return next(i for i in self.get()[1]["items"] if i["id"] == gid)


# --- 1. what is typed and what is stored ------------------------------------------------------------------------------
class CleanTest(unittest.TestCase):
    def test_controls_and_invisible_marks_go_and_a_newline_stays(self):
        text, note = graphics.clean_text("  NEXT\x00 UP\x1b[31m‮  \r\nDJ‍ MARA⁦\t \n")
        self.assertEqual(text, "NEXT UP[31m\nDJ MARA")
        self.assertEqual(note, "")

    def test_unicode_is_normalised_and_the_length_and_lines_are_cut_with_the_reason(self):
        self.assertEqual(graphics.clean_text("é")[0], "é")
        text, note = graphics.clean_text("x" * 400)
        self.assertEqual((len(text), note), (280, "cut to 280 characters"))
        text, note = graphics.clean_text("\n".join("l%d" % i for i in range(9)))
        self.assertEqual((text.count("\n"), note), (5, "cut to 6 lines"))
        self.assertEqual(graphics.clean_text(5), ("", ""))

    def test_a_typed_line_is_never_interpreted(self):
        # no markup, no tag: braces and backslashes are letters (an ASS-based path would have to escape them; this one has no such path)
        text = graphics.clean_text("{\\an8}{\\fs200}<b>hi</b> %s %(x)d")[0]
        self.assertEqual(text, "{\\an8}{\\fs200}<b>hi</b> %s %(x)d")
        item = graphics.new_item("text", [])
        item = graphics.apply_set(item, {"text": {"value": "a\nfont evil\nsize 9999\nTEXT\nb"}})
        params = graphics.params_for(item, 1920, 1080)[0]
        head, _, typed = params.partition("\nTEXT\n")
        self.assertEqual(typed, "a\nfont evil\nsize 9999\nTEXT\nb")            # all of it after the one line that ends the settings
        self.assertEqual([l for l in head.split("\n") if l.startswith("font ")], ["font archivo"])
        self.assertEqual(len([l for l in head.split("\n") if l.startswith("size ")]), 1)


class StoredTest(unittest.TestCase):
    def test_a_missing_section_is_an_empty_stage_that_is_on(self):
        self.assertEqual(graphics.items_of(None), [])
        self.assertEqual(graphics.items_of({}), [])
        self.assertTrue(graphics.stage_on({}))
        self.assertFalse(graphics.stage_on({"on": False}))
        self.assertEqual(graphics.showing({}), 0)

    def test_a_damaged_section_is_read_not_trusted(self):
        good = graphics.new_item("text", [])
        junk = ["x", None, 3, {"kind": "text"}, {"kind": "video", "id": "g1111"}, {"kind": "text", "id": "../x"}, dict(good)]
        cfg = {"on": "yes", "items": junk + [dict(good, id="g%04x" % i, slot=1) for i in range(1, 12)]}
        items = graphics.items_of(cfg)
        self.assertEqual(len(items), graphics.MAX_ITEMS)
        self.assertEqual(len({i["id"] for i in items}), graphics.MAX_ITEMS)
        self.assertEqual(sorted(i["slot"] for i in items), list(range(1, 9)))      # a slot taken twice is given the next free one
        self.assertTrue(graphics.stage_on(cfg))                                    # only an explicit false switches the stage off

    def test_numbers_are_clamped_a_missing_font_is_archivo_and_later_features_read_as_none(self):
        raw = dict(graphics.new_item("text", []), on=True,
                   place={"x": 9999, "y": "no", "rotate": 1e9, "opacity": -5, "anchor": "zz", "scale": 1},
                   text={"value": "x\u0000y", "font": "comic-sans", "size": 500, "colour": "red", "weight": 12, "align": "justify",
                         "outline": {"on": "yes", "width": 99}, "lines": ["a"] * 40},
                   where={"mode": "as", "source": 2}, motion={"kind": "crawl"}, key={"kind": "chroma"}, fx=[{"id": "glow"}])
        it = graphics.items_of({"items": [raw]})[0]
        self.assertEqual((it["place"]["x"], it["place"]["y"], it["place"]["rotate"], it["place"]["opacity"], it["place"]["anchor"], it["place"]["scale"]),
                         (100, 0, 360, 0, "bc", 10))
        t = it["text"]
        self.assertEqual((t["value"], t["font"], t["size"], t["colour"], t["align"], t["weight"]), ("xy", "archivo", 60, "#ffffff", "center", 100))
        self.assertEqual((t["outline"]["on"], t["outline"]["width"], len(t["lines"])), (False, 20, graphics.MAX_SAVED_LINES))
        # (a motion is kept as it was chosen, even one this node cannot draw: it is drawn still there and says so)
        self.assertEqual((it["where"], it["motion"], it["key"], it["fx"]),
                         ({"mode": "screen"}, {"kind": "crawl", "seconds": 1.0, "out": 0.5, "speed": 12, "loop": False}, {"kind": "alpha"}, []))

    def test_a_logo_name_that_is_a_path_is_not_a_logo_file(self):
        for name in ("../x.png", "a/b.png", ".hid.png", "a.jpg", "x" * 200 + ".png", 3):
            it = graphics.items_of({"items": [dict(graphics.new_item("logo", []), logo={"file": name, "size": 30})]})[0]
            self.assertEqual(it["logo"]["file"], "", name)

    def test_what_is_stored_reads_back_the_same(self):
        items = []
        for kind in ("text", "logo", "text"):
            items.append(graphics.new_item(kind, items))
        cfg = graphics.stored(items, False)
        self.assertEqual(graphics.items_of(cfg), items)
        self.assertEqual(json.loads(json.dumps(cfg)), cfg)
        self.assertEqual([i["slot"] for i in items], [1, 2, 3])


class SetTest(unittest.TestCase):
    def setUp(self):
        self.text = graphics.new_item("text", [])
        self.logo = graphics.new_item("logo", [])

    def refused(self, item, patch, words):
        with self.assertRaises(graphics.GraphicsError) as cm:
            graphics.apply_set(item, patch)
        self.assertEqual(cm.exception.status, 400)
        self.assertIn(words, cm.exception.message)

    def test_good_changes_are_applied_to_a_copy(self):
        out = graphics.apply_set(self.text, {"name": "Next up", "place": {"anchor": "tl", "x": 5}, "text": {
            "font": "bebas-neue", "size": 12.5, "colour": "#FFCC00", "outline": {"on": True, "width": 3}, "lines": ["one", " ", "two"]}})
        self.assertEqual(self.text["name"], "Text 1", "the item it was given is not changed")
        self.assertEqual((out["name"], out["place"]["anchor"], out["place"]["x"]), ("Next up", "tl", 5))
        t = out["text"]
        self.assertEqual((t["font"], t["weight"], t["size"], t["colour"], t["outline"]["on"], t["lines"]), ("bebas-neue", 400, 12.5, "#ffcc00", True, ["one", "two"]))

    def test_a_weight_follows_the_font_it_belongs_to(self):
        out = graphics.apply_set(self.text, {"text": {"font": "oswald", "weight": 300}})
        self.assertEqual(out["text"]["weight"], 300)
        self.refused(self.text, {"text": {"font": "bebas-neue", "weight": 700}}, "text.weight")
        self.assertEqual(graphics.apply_set(out, {"text": {"font": "anton"}})["text"]["weight"], 400, "a font with one weight")

    def test_refusals_say_what_is_wrong(self):
        self.refused(self.text, {"zzz": 1}, "no setting called 'zzz'")
        self.refused(self.text, {"text": {"font": "papyrus"}}, "text.font must be one of")
        self.refused(self.text, {"text": {"size": 90}}, "text.size")
        self.refused(self.text, {"text": {"colour": "red"}}, "colour like #ffcc00")
        self.refused(self.text, {"text": {"align": "justify"}}, "text.align")
        self.refused(self.text, {"text": {"outline": {"on": 1}}}, "true or false")
        self.refused(self.text, {"text": {"shadow": {"soft": 101}}}, "text.shadow.soft")
        self.refused(self.text, {"text": {"lines": ["a"] * 21}}, "up to 20")
        self.refused(self.text, {"place": {"x": "left"}}, "place.x")
        self.refused(self.text, {"place": {"anchor": "tc "}}, "place.anchor")
        self.refused(self.text, {"logo": {"file": "a.png"}}, "this graphic is a text")
        self.refused(self.logo, {"text": {"value": "hi"}}, "this graphic is a logo")
        self.refused(self.logo, {"logo": {"file": "../a.png"}}, "choose a picture")
        self.refused(self.logo, {"logo": {"size": 0}}, "logo.size")

    def test_what_comes_in_later_slices_is_refused_in_words_not_ignored(self):
        with self.assertRaises(graphics.GraphicsError) as cm:
            graphics.apply_set(self.text, {"motion": {"kind": "crawl"}}, graphics.MOTIONS_OVERLAY)
        self.assertIn("need the New renderer", cm.exception.message)
        self.assertEqual(graphics.apply_set(self.text, {"motion": {"kind": "crawl"}}, graphics.MOTIONS)["motion"]["kind"], "crawl")
        self.refused(self.text, {"where": {"mode": "over", "source": 2}}, "over a source")
        self.refused(self.logo, {"key": {"kind": "chroma"}}, "keys")
        self.refused(self.logo, {"fx": [{"id": "glow"}]}, "Effects")
        graphics.apply_set(self.text, {"motion": {"kind": "none"}, "where": {"mode": "screen"}, "key": {"kind": "alpha"}, "fx": []})


class PlaceTest(unittest.TestCase):
    def test_each_anchor_sits_on_its_point_with_a_margin_at_an_edge(self):
        w, h, sw, sh = 200, 100, 1920, 1080
        mx, my = 96, 54
        want = {"tl": (mx, my), "tc": (860, my), "tr": (1920 - mx - w, my), "ml": (mx, 490), "mc": (860, 490), "mr": (1920 - mx - w, 490),
                "bl": (mx, 1080 - my - h), "bc": (860, 1080 - my - h), "br": (1920 - mx - w, 1080 - my - h)}
        for anchor, xy in want.items():
            self.assertEqual(graphics.position(anchor, 0, 0, w, h, sw, sh), xy, anchor)

    def test_nudges_move_by_a_share_of_the_screen_and_never_off_it(self):
        self.assertEqual(graphics.position("mc", 10, -10, 200, 100, 1920, 1080), (860 + 192, 490 - 108))
        self.assertEqual(graphics.position("tl", -100, -100, 200, 100, 1920, 1080), (0, 0))
        self.assertEqual(graphics.position("br", 100, 100, 200, 100, 1920, 1080), (1720, 980))
        self.assertEqual(graphics.position("mc", 0, 0, 3000, 2000, 1920, 1080), (0, 0), "bigger than the screen: at the corner")

    def test_the_size_follows_the_screens_height_for_text_and_its_width_for_a_logo(self):
        text = graphics.apply_set(graphics.new_item("text", []), {"text": {"value": "hi", "size": 10}})
        logo = graphics.apply_set(graphics.new_item("logo", []), {"logo": {"file": "a.png", "size": 25}, "place": {"rotate": 15, "mirror": True}})
        self.assertIn("size 108.000", graphics.params_for(text, 1920, 1080)[0])
        self.assertIn("size 36.000", graphics.params_for(text, 640, 360)[0], "the preview is the same picture, smaller")
        p = graphics.params_for(logo, 1920, 1080)[0]
        self.assertIn("width 480.00", p)
        self.assertIn("rotate 15.0", p)
        self.assertIn("mirror 1", p)
        self.assertIn("limit 1728 972", p)

    def test_a_built_picture_has_a_key_that_changes_with_what_it_is_made_from_and_not_with_the_switch(self):
        it = graphics.apply_set(graphics.new_item("text", []), {"text": {"value": "hi"}})
        k = graphics.render_key(it, 1920, 1080)
        self.assertEqual(graphics.render_key(dict(it, on=True, name="other"), 1920, 1080), k)
        self.assertNotEqual(graphics.render_key(it, 1280, 720), k)
        self.assertNotEqual(graphics.render_key(graphics.apply_set(it, {"text": {"value": "ho"}}), 1920, 1080), k)
        self.assertNotEqual(graphics.render_key(graphics.apply_set(it, {"place": {"opacity": 50}}), 1920, 1080), k)


# --- 2. the routes ------------------------------------------------------------------------------------------------------
class RoutesTest(Base):
    def test_the_owner_and_the_operator_use_it_and_a_guest_and_a_stranger_do_not(self):
        for token in (self.full, self.op):
            st, body, _ = self.get(token)
            self.assertEqual(st, 200, body)
            self.assertEqual((body["on"], body["items"], body["max_items"], body["motions"], body["effects"], body["where"]), (True, [], 8, ["none", "fade", "typewriter"], [], ["screen"]))
            self.assertEqual(self.call("GET", "/api/graphics/fonts", token=token)[1]["fonts"][0]["id"], "archivo")
        gid = self.add()
        for token, want in ((self.guest_token, 403), (None, 401)):
            self.assertEqual(self.get(token)[0], want)
            self.assertEqual(self.post({"add": "text"}, token)[0], want)
            self.assertEqual(self.post({"id": gid, "text": "from a guest"}, token)[0], want)
            self.assertEqual(self.post({"all": False}, token)[0], want)
            self.assertEqual(self.call("GET", "/api/graphics/fonts", token=token)[0], want)
            self.assertEqual(self.call("GET", "/api/graphics/preview.png?id=" + gid, token=token)[0], want)
        self.lock(False)           # guest controls open: still none of it, the Guest list has no row for it
        self.assertEqual(self.post({"id": gid, "text": "from a guest"}, self.guest_token)[0], 403)
        self.assertEqual(self.item(gid)["text"]["value"], "NEXT UP: DJ MARA")
        self.assertNotIn(("POST", "/api/graphics"), policy.GUEST)
        self.assertEqual([c for c in self.client.jobs if "from a guest" in c[0]], [])

    def test_a_guest_is_told_only_how_many_are_on(self):
        self.add(on=True)
        st, body, _ = self.call("GET", "/api/status", token=self.guest_token)
        self.assertEqual((st, body["graphics_on"]), (200, 1))
        self.assertEqual(self.post({"all": False})[0], 200)
        self.assertEqual(self.call("GET", "/api/status", token=self.guest_token)[1]["graphics_on"], 0)
        self.assertNotIn("items", json.dumps(self.call("GET", "/api/status", token=self.guest_token)[1]))

    def test_add_set_show_and_hide_draw_over_the_screen_with_the_players_overlay(self):
        gid = self.add()
        self.assertEqual(self.parts(), [], "off: nothing is drawn or built")
        self.assertEqual(self.client.jobs, [])
        st, body, _ = self.post({"id": gid, "on": True})
        self.assertEqual(st, 200)
        self.assertEqual(body["items"][0]["status"]["state"], "on")
        (call,) = self.parts()
        oid, path, x, y, offset, w, h, stride = call[1:]
        self.assertEqual((oid, offset, stride), (20, 0, w * 4))
        self.assertEqual((w, h), (20 + 10 * len("NEXT UP: DJ MARA"), 60))
        self.assertEqual((x, y), graphics.position("bc", 0, 0, w, h, 1920, 1080), "bottom centre, by default")
        self.assertTrue(os.path.dirname(path) == self.rundir and os.path.getsize(path) == w * h * 4)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o640")
        self.assertEqual(self.post({"id": gid, "on": False})[0], 200)
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))
        self.assertFalse(os.path.exists(path), "a graphic that is off keeps no picture")
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("graphic-")], [])

    def test_a_change_to_a_visible_text_is_built_again_and_the_old_picture_goes(self):
        gid = self.add(on=True)
        first = self.parts()[-1][2]
        self.assertEqual(self.post({"id": gid, "text": "THANK YOU, SASKATOON"})[0], 200)
        second = self.parts()[-1][2]
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.exists(first))
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("graphic-")], [os.path.basename(second)])
        self.assertEqual(self.post({"id": gid, "set": {"place": {"anchor": "tl"}}})[0], 200)
        last = self.parts()[-1]
        self.assertEqual(last[3:5], graphics.position("tl", 0, 0, last[6], last[7], 1920, 1080))
        jobs = len(self.client.jobs)
        self.assertEqual(self.post({"id": gid, "set": {"name": "Renamed"}})[0], 200)
        self.assertEqual(len(self.client.jobs), jobs, "a name changes no picture")

    def test_the_order_of_the_list_is_the_order_of_the_overlays_and_move_re_draws_them(self):
        a = self.add(text="AAA", on=True)
        b = self.add(text="BBBBBB", on=True)
        ids = {c[1]: c[6] for c in self.parts()}
        self.assertEqual(ids, {20: 20 + 10 * 3, 21: 20 + 10 * 6})
        self.assertEqual(self.post({"move": a, "dir": "down"})[0], 200)
        by_oid = {}
        for c in self.parts():
            by_oid[c[1]] = c[6]
        self.assertEqual(by_oid, {20: 20 + 10 * 6, 21: 20 + 10 * 3}, "the first of the list is drawn at 20 and the later one over it")
        st, body, _ = self.get()
        self.assertEqual([i["id"] for i in body["items"]], [b, a])
        self.assertEqual([i["slot"] for i in body["items"]], [2, 1], "a graphic keeps its slot when it moves")
        self.assertEqual(self.post({"move": a, "dir": "sideways"})[0], 400)

    def test_remove_takes_it_off_the_screen_and_frees_its_slot(self):
        a = self.add(on=True)
        self.assertEqual(self.post({"id": a, "remove": True})[0], 200)
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))
        self.assertEqual(self.get()[1]["items"], [])
        self.assertEqual(self.post({"id": a, "remove": True})[0], 404)
        again = self.add()
        self.assertEqual(self.item(again)["slot"], 1)

    def test_hide_all_hides_every_graphic_and_showing_one_brings_the_stage_back(self):
        a, b = self.add(on=True), self.add(on=True)
        self.assertEqual(self.post({"all": False})[0], 200)
        self.assertEqual({c[1] for c in self.overlays() if c[0] == "overlay_remove"}, {20, 21})
        st, body, _ = self.get()
        self.assertEqual((body["on"], body["showing"]), (False, 0))
        self.assertTrue(all(i["on"] for i in body["items"]), "each keeps its own switch")
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[1]["graphics_on"], 0)
        self.assertEqual(self.post({"all": True})[0], 200)
        self.assertEqual(len([c for c in self.parts() if c[1] in (20, 21)]) >= 4, True)
        self.assertEqual(self.post({"all": False})[0], 200)
        self.assertEqual(self.post({"id": a, "on": "toggle"})[0], 200)       # off: it was on
        self.assertEqual(self.post({"id": a, "on": "toggle"})[0], 200)       # on again, and the stage with it
        self.assertEqual(self.get()[1]["on"], True)
        self.assertEqual(self.post({"all": "no"})[0], 400)

    def test_how_many_may_be_on_at_once_follows_the_tier(self):
        self.api.board = {"kind": "pi3", "model": "x", "arch": "armv7l"}
        ids = [self.add(text="t%d" % i) for i in range(5)]
        for gid in ids[:4]:
            self.assertEqual(self.post({"id": gid, "on": True})[0], 200)
        st, body, _ = self.post({"id": ids[4], "on": True})
        self.assertEqual(st, 400)
        self.assertIn("4 graphics at a time", body["error"])
        self.assertEqual(self.get()[1]["max_on"], 4)
        self.api.board = {"kind": "x86", "model": "x", "arch": "x86_64"}
        self.assertEqual(self.post({"id": ids[4], "on": True})[0], 200)
        self.assertEqual(self.get()[1]["max_on"], 8)

    def test_eight_graphics_at_most(self):
        for _ in range(8):
            self.add(text=None)
        st, body, _ = self.post({"add": "text"})
        self.assertEqual((st, "Up to 8" in body["error"]), (400, True))

    def test_a_text_graphic_needs_a_line_and_a_logo_needs_a_file_before_it_can_show(self):
        t = self.add(text=None)
        st, body, _ = self.post({"id": t, "on": True})
        self.assertEqual((st, body["error"]), (400, "Type a line first."))
        lg = self.add("logo")
        st, body, _ = self.post({"id": lg, "on": True})
        self.assertEqual((st, body["error"]), (400, "Choose a logo file first."))
        self.assertEqual(self.client.jobs, [])

    def test_lines_are_saved_and_a_tap_sends_one_at_once(self):
        gid = self.add(text="first")
        self.assertEqual(self.post({"id": gid, "set": {"text": {"lines": ["NEXT UP: DJ MARA", "THANK YOU", "SEE YOU AT 9"]}}})[0], 200)
        st, body, _ = self.post({"id": gid, "line": 1})
        self.assertEqual(st, 200, body)
        it = body["items"][0]
        self.assertEqual((it["on"], it["text"]["value"], it["text"]["line"]), (True, "THANK YOU", 1))
        self.assertEqual(self.post({"id": gid, "line": "next"})[1]["items"][0]["text"]["value"], "SEE YOU AT 9")
        self.assertEqual(self.post({"id": gid, "line": "next"})[1]["items"][0]["text"]["value"], "NEXT UP: DJ MARA", "round again")
        self.assertEqual(self.post({"id": gid, "line": "prev"})[1]["items"][0]["text"]["value"], "SEE YOU AT 9")
        for bad in (3, -1, "later", True):
            self.assertEqual(self.post({"id": gid, "line": bad})[0], 400, bad)
        other = self.add(text="x")
        self.assertEqual(self.post({"id": other, "line": 0})[1]["error"], "This graphic has no saved lines yet.")
        self.assertEqual(self.post({"id": gid, "text": "typed"})[1]["items"][0]["text"]["line"], -1, "a typed line is no saved line")

    def test_a_typed_text_is_cleaned_before_it_is_kept_or_drawn(self):
        gid = self.add(text=None)
        st, body, _ = self.post({"id": gid, "text": "  HELLO‮\x00 WORLD  "})
        self.assertEqual(body["items"][0]["text"]["value"], "HELLO WORLD")
        for bad in (5, None, ["a"], {"a": 1}):
            self.assertEqual(self.post({"id": gid, "text": bad})[0], 400, bad)
        st, body, _ = self.post({"id": gid, "text": "z" * 5000})
        self.assertEqual(len(body["items"][0]["text"]["value"]), 280)

    def test_requests_that_are_not_understood_are_refused_without_changing_anything(self):
        gid = self.add()
        before = json.dumps(self.settings.data.get("graphics"), sort_keys=True)
        for body in ({"frobnicate": 1}, {"add": "video"}, {"id": "nope", "on": True}, {"slot": 7, "on": True}, {"on": True}, {"set": {"name": "x"}},
                     {"id": gid, "on": "maybe"}, {"id": gid, "remove": False}, {"id": gid, "set": {"text": {"size": "big"}}}, "text", ["add"]):
            st, out, _ = self.post(body)
            self.assertIn(st, (400, 404), body)
            self.assertIn("error", out)
        self.assertEqual(json.dumps(self.settings.data.get("graphics"), sort_keys=True), before)

    def test_what_comes_later_is_said_not_silently_dropped(self):
        gid = self.add()
        for patch, words in (({"motion": {"kind": "crawl"}}, "New renderer"), ({"where": {"mode": "as", "source": 2}}, "a source")):
            st, body, _ = self.post({"id": gid, "set": patch})
            self.assertEqual(st, 400)
            self.assertIn(words, body["error"])

    def test_a_logo_is_drawn_from_the_store_scaled_and_placed(self):
        self.api.graphics.store("mara.png", png(8, 4))
        self.api.graphics.client = self.client        # the store's client is the stand-in for both jobs
        lg = self.add("logo")
        self.assertEqual(self.post({"id": lg, "set": {"logo": {"file": "mara.png", "size": 20}, "place": {"opacity": 80, "rotate": 10, "mirror": True}}})[0], 200)
        st, body, _ = self.post({"id": lg, "on": True})
        self.assertEqual(st, 200, body)
        params, payload = self.client.jobs[-1]
        self.assertEqual(payload, open(os.path.join(self.api.graphics.folder, "mara.png"), "rb").read())
        for want in ("kind logo", "width 384.00", "rotate 10.0", "mirror 1", "opacity 80"):
            self.assertIn(want, params)
        self.assertEqual(self.parts()[-1][1], 20)
        self.assertEqual(self.parts()[-1][3:5], graphics.position("tr", 0, 0, 120, 80, 1920, 1080))
        self.assertEqual(self.post({"id": lg, "set": {"logo": {"file": "../etc/passwd"}}})[0], 400)

    def test_a_logo_that_leaves_the_store_is_said_to_be_missing_and_is_taken_off(self):
        self.api.graphics.store("mara.png", png(8, 4))
        lg = self.add("logo")
        self.post({"id": lg, "set": {"logo": {"file": "mara.png"}}})
        self.assertEqual(self.post({"id": lg, "on": True})[0], 200)
        self.assertEqual(self.api.graphics.delete("mara.png"), {"deleted": "mara.png"})
        it = self.item(lg)
        self.assertTrue(it["logo"]["missing"])
        self.assertEqual(it["status"]["state"], "error")
        self.assertIn("not in the graphics store", it["status"]["message"])
        self.gfx.apply()                    # the settings are looked at again: the logo's file is not there, so the picture is taken off
        self.assertEqual(self.item(lg)["status"]["state"], "error")
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))

    def test_a_helper_that_refuses_is_said_in_its_words_and_is_not_asked_again_for_the_same_picture(self):
        gid = self.add()
        self.client.fail = "there is too much text to fit the screen at a size that can be read; use fewer words"
        st, body, _ = self.post({"id": gid, "on": True})
        self.assertEqual(st, 200)
        self.assertEqual(body["items"][0]["status"], {"state": "error", "message": self.client.fail, "missing": 0})
        self.assertEqual(self.parts(), [])
        jobs = len(self.client.jobs)
        self.gfx.watch()
        self.gfx.process(final=True)
        self.assertEqual(len(self.client.jobs), jobs, "the same picture is not tried over and over")
        self.client.fail = None
        st, body, _ = self.post({"id": gid, "text": "SHORT"})
        self.assertEqual(body["items"][0]["status"]["state"], "on")

    def test_the_pictures_of_all_that_is_on_together_stay_inside_the_memory_budget(self):
        big = FakeDrawing()          # each picture is said to be 18 MB (the stand-in's data is empty: only the sizes count)
        big.graphic = lambda params, payload=b"": {"w": 3000, "h": 1500, "missing": 0, "data": b"", "png": False}
        self.api.graphics.client = big
        gids = [self.add() for _ in range(4)]
        for gid in gids:
            self.assertEqual(self.post({"id": gid, "on": True})[0], 200)
        states = [self.item(g)["status"] for g in gids]
        self.assertEqual([s["state"] for s in states], ["on", "on", "error", "error"], "two fit in 48 MB, the third does not")
        self.assertIn("more than 48 MB (this one needs 17.2 MB)", states[2]["message"])
        self.assertEqual(self.post({"id": gids[0], "on": False})[0], 200)
        self.gfx.apply()
        self.assertEqual(self.item(gids[2])["status"]["state"], "on", "room again: it is drawn")

    def test_the_screen_having_no_size_yet_is_said(self):
        gid = self.add()
        self.api.player.osd_size = lambda: None
        self.post({"id": gid, "on": True})
        st = self.item(gid)["status"]
        self.assertEqual(st["state"], "error")
        self.assertIn("no screen size yet", st["message"])
        self.assertEqual(self.client.jobs, [])
        self.api.player.osd_size = lambda: (1920, 1080)
        self.assertTrue(self.gfx.watch())
        self.assertEqual(self.item(gid)["status"]["state"], "on")

    def test_a_new_screen_size_builds_again_and_a_replaced_logo_does_too(self):
        gid = self.add(on=True)
        n = len(self.client.jobs)
        self.assertFalse(self.gfx.watch())
        self.api.player.osd_size = lambda: (1280, 720)
        self.assertTrue(self.gfx.watch())
        self.assertEqual(len(self.client.jobs), n + 1)
        self.assertIn("size 64.800", self.client.jobs[-1][0])
        self.assertFalse(self.gfx.watch())
        self.api.graphics.store("m.png", png(4, 4))
        lg = self.add("logo")
        self.post({"id": lg, "set": {"logo": {"file": "m.png"}}})
        self.post({"id": lg, "on": True})
        n = len(self.client.jobs)
        self.api.graphics.store("m.png", png(6, 6), replace=True)
        self.assertTrue(self.gfx.watch())
        self.assertEqual(len(self.client.jobs), n + 1)

    def test_a_restarted_player_gets_what_was_on_back_and_a_restarted_node_too(self):
        a = self.add(text="ONE", on=True)
        b = self.add(text="TWO", on=True)
        self.api.player.calls.clear()
        self.gfx.apply(restarted=True)
        self.assertEqual(sorted(c[1] for c in self.parts()), [20, 21], "drawn again from the pictures already built")
        jobs = len(self.client.jobs)
        self.assertEqual(jobs, 2)
        # a new node: nothing built, the settings say what was on
        fresh = graphics.Graphics(self.api)
        fresh.synchronous = True
        self.addCleanup(fresh.stop)
        self.api.player.calls.clear()
        fresh.apply(restarted=True)
        self.assertEqual(sorted(c[1] for c in self.parts()), [20, 21])
        self.assertEqual(len(self.client.jobs), jobs + 2)
        self.assertEqual({i["id"] for i in fresh.state()["items"]}, {a, b})

    def test_a_player_that_does_not_answer_is_said_and_the_graphic_comes_back_with_apply(self):
        gid = self.add()
        real = self.api.player.overlay_part
        self.api.player.overlay_part = lambda *a: (_ for _ in ()).throw(RuntimeError("mpv is not running"))
        self.post({"id": gid, "on": True})
        st = self.item(gid)["status"]
        self.assertEqual(st["state"], "error")
        self.assertIn("mpv is not running", st["message"])
        self.api.player.overlay_part = real
        self.gfx.apply(restarted=True)
        self.assertEqual(self.item(gid)["status"]["state"], "on")

    def test_the_files_of_an_earlier_run_are_cleaned_and_only_ours_are_kept(self):
        for name in ("graphic-gaaaa-1-1.bgra", "graphic-gbbbb-99999-7.bgra", "keep.bgra"):
            with open(os.path.join(self.rundir, name), "wb") as f:
                f.write(b"x")
        gid = self.add(on=True)
        left = sorted(n for n in os.listdir(self.rundir) if n.endswith(".bgra"))
        self.assertEqual(len(left), 2)
        self.assertIn("keep.bgra", left)
        self.assertTrue(any(n.startswith("graphic-%s-" % gid) for n in left))

    def test_settings_are_written_at_most_twice_a_second_and_the_last_text_is_what_is_kept(self):
        gid = self.add(text="a")
        saved = self.saves()
        t = [100.0]
        self.gfx.clock = lambda: t[0]
        self.gfx._saved_at = -1e9
        for i in range(30):
            t[0] += 0.01
            self.assertEqual(self.post({"id": gid, "text": "line %d" % i})[0], 200)
        self.assertLess(len(saved), 3, "a burst of pushes is not a burst of writes")
        self.assertTrue(self.gfx.flush())
        self.assertEqual(self.settings.data["graphics"]["items"][0]["text"]["value"], "line 29")
        self.assertFalse(self.gfx.flush())

    def test_a_save_that_fails_is_said_and_changes_nothing_on_screen(self):
        gid = self.add()
        real = self.settings.save
        self.settings.save = lambda: (_ for _ in ()).throw(OSError(28, "No space left on device"))
        self.addCleanup(setattr, self.settings, "save", real)
        self.gfx._saved_at = -1e9
        st, body, _ = self.post({"id": gid, "text": "never kept"})
        self.assertEqual(st, 500)
        self.assertIn("Could not save", body["error"])


class PreviewTest(Base):
    def test_a_preview_is_a_png_of_the_screens_shape_made_by_the_helper(self):
        gid = self.add()
        st, body, resp = self.call("GET", "/api/graphics/preview.png?id=" + gid, token=self.op)
        self.assertEqual(st, 200)
        self.assertEqual((resp.getheader("Content-Type"), resp.getheader("X-Content-Type-Options")), ("image/png", "nosniff"))
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        first, second = self.client.jobs[-2:]
        self.assertIn("out raw", first[0])
        self.assertIn("out png", second[0])
        self.assertIn("canvas 640 360 ", second[0])
        self.assertIn("size 32.400", second[0], "9 percent of a 360 line screen")
        self.assertEqual(self.parts(), [], "a preview draws nothing on the screen")

    def test_a_line_as_typed_is_drawn_in_the_preview_and_kept_nowhere(self):
        gid = self.add(text="saved line")
        st, body, _ = self.call("POST", "/api/graphics/preview.png", {"id": gid, "text": "typed \u202e line"}, token=self.op)
        self.assertEqual(st, 200)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(self.client.jobs[-1][0].endswith("\nTEXT\ntyped  line"), self.client.jobs[-1][0][-30:])
        self.assertEqual(self.item(gid)["text"]["value"], "saved line")
        self.assertEqual(self.settings.data["graphics"]["items"][0]["text"]["value"], "saved line")
        st, body, _ = self.call("POST", "/api/graphics/preview.png", {"id": gid, "text": "  "}, token=self.op)
        self.assertEqual((st, body["error"]), (409, "Type a line to see it."))
        self.call("POST", "/api/graphics/preview.png", {"id": gid, "text": "x" * 9000}, token=self.op)
        self.assertLessEqual(len(self.client.jobs[-1][0].split("\nTEXT\n", 1)[1]), 280)

    def test_the_typed_line_is_not_taken_from_the_address(self):
        gid = self.add(text="saved line")
        self.call("GET", "/api/graphics/preview.png?id=%s&text=in%%20the%%20address" % gid, token=self.op)
        self.assertTrue(self.client.jobs[-1][0].endswith("\nTEXT\nsaved line"), "an address cannot carry a line any more")
        for token, want in ((self.guest_token, 403), (None, 401)):
            self.assertEqual(self.call("POST", "/api/graphics/preview.png", {"id": gid, "text": "x"}, token=token)[0], want)
        self.assertEqual(self.call("POST", "/api/graphics/preview.png", {"id": 5}, token=self.op)[0], 404)
        self.assertEqual(self.call("POST", "/api/graphics/preview.png", {}, token=self.op)[0], 404)

    def test_a_node_without_the_helper_says_so_for_text_and_logos(self):
        c = raster.Client(socket_path=os.path.join(self.tmp, "no-such.sock"), env={})
        with self.assertRaises(raster.RasterError) as cm:
            c.graphic("kind text\nTEXT\nx")
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("Text and logos cannot be drawn", cm.exception.message)

    def test_previews_are_limited_to_five_a_second_and_say_so(self):
        gid = self.add()
        t = [50.0]
        self.gfx.clock = lambda: t[0]
        got = [self.call("GET", "/api/graphics/preview.png?id=" + gid, token=self.op)[0] for _ in range(7)]
        self.assertEqual(got, [200] * 5 + [429] * 2)
        t[0] += 1.1
        self.assertEqual(self.call("GET", "/api/graphics/preview.png?id=" + gid, token=self.op)[0], 200)

    def test_a_preview_of_nothing_says_why(self):
        gid = self.add(text=None)
        st, body, _ = self.call("GET", "/api/graphics/preview.png?id=" + gid, token=self.op)
        self.assertEqual((st, body["error"]), (409, "Type a line to see it."))
        self.assertEqual(self.call("GET", "/api/graphics/preview.png?id=nope", token=self.op)[0], 404)
        self.assertEqual(self.call("GET", "/api/graphics/preview.png", token=self.op)[0], 404)
        lg = self.add("logo")
        self.assertEqual(self.call("GET", "/api/graphics/preview.png?id=" + lg, token=self.op)[0], 404)


# --- 3. OSC and the controllers ----------------------------------------------------------------------------------------
class OverlayOrderTest(Base):
    """Design 3.1: the pairing codes and the masks stay on top of the graphics."""

    def test_graphics_are_under_the_masks_and_the_masks_under_the_pairing_codes(self):
        from pvj import overlay, pinscreen, transitions
        top_graphic = graphics.OVERLAY_BASE + graphics.MAX_ITEMS - 1
        self.assertEqual((graphics.OVERLAY_BASE, top_graphic), (20, 27))
        self.assertEqual(overlay.OVERLAY_ID, 30)
        self.assertEqual(sorted(pinscreen.QR_IDS.values()), [40, 41, 42, 43])
        self.assertLess(top_graphic, overlay.OVERLAY_ID)
        self.assertLess(overlay.OVERLAY_ID, min(pinscreen.QR_IDS.values()))
        self.assertLess(max(pinscreen.QR_IDS.values()), transitions.OVERLAY_ID)
        used = list(range(graphics.OVERLAY_BASE, top_graphic + 1)) + [overlay.OVERLAY_ID, transitions.OVERLAY_ID] + list(pinscreen.QR_IDS.values())
        self.assertEqual(len(used), len(set(used)), "no two draw at the same id")
        self.assertTrue(all(0 <= i < 64 for i in used), "the player takes ids 0 to 63")

    def test_graphics_are_held_back_while_a_code_is_on_the_screen_and_come_back_without_a_new_picture(self):
        gid = self.add(on=True)
        self.assertEqual(len(self.parts()), 1)
        jobs = len(self.client.jobs)
        up = [True]
        self.api.access_on_screen = lambda: up[0]
        self.assertTrue(self.gfx.watch(), "the tick notices the code")
        self.assertEqual(self.overlays()[-1][:2], ("overlay_remove", 20))
        it = self.item(gid)
        self.assertEqual((it["on"], it["status"]["state"]), (True, "held"), "still on in the settings, held back on the screen")
        self.assertTrue(self.get()[1]["held"])
        parts = len(self.parts())
        self.assertFalse(self.gfx.watch(), "nothing changed: nothing to do")
        self.assertEqual(len(self.parts()), parts)
        up[0] = False
        self.assertTrue(self.gfx.watch())
        self.assertEqual(self.parts()[-1][1], 20, "back at its own id")
        self.assertEqual(self.item(gid)["status"]["state"], "on")
        self.assertEqual(len(self.client.jobs), jobs, "the picture built before is drawn again, not made again")
        self.assertFalse(self.get()[1]["held"])

    def test_a_graphic_switched_on_while_a_code_is_up_waits_for_it_to_go(self):
        self.api.access_on_screen = lambda: True
        self.add(on=True)
        self.assertEqual(self.parts(), [])
        self.api.access_on_screen = lambda: False
        self.assertTrue(self.gfx.watch())
        self.assertEqual(self.parts()[-1][1], 20)


class FailedBuildTest(Base):
    """A picture that cannot be written, or a player with nowhere to draw from, is not tried again until something changes."""

    def test_no_run_folder_and_a_failed_write_are_tried_once_and_not_five_times_a_second(self):
        player = self.api.player
        was = getattr(player, "rundir", None)
        self.addCleanup(setattr, player, "rundir", was)
        for label, rundir, words in (("no folder", None, "no folder"), ("write fails", os.path.join(self.tmp, "not-there", "deeper"), "Could not write")):
            player.rundir = rundir
            gid = self.add(text="line for " + label, on=True)
            jobs = [c for c in self.client.jobs if "line for " + label in c[0]]
            self.assertEqual(len(jobs), 1, label)
            self.assertIn(words, self.item(gid)["status"]["message"])
            self.assertEqual(self.item(gid)["status"]["state"], "error")
            self.assertIn(gid, self.gfx._failed)
            for _ in range(5):
                self.gfx.watch()
                self.gfx.process(final=True)
            self.assertEqual(len([c for c in self.client.jobs if "line for " + label in c[0]]), 1, "%s: not built again" % label)
            self.assertEqual(self.item(gid)["status"]["state"], "error", label)
            # a restarted player clears it, and the picture is made again
            player.rundir = was
            self.gfx.apply(restarted=True)
            self.assertEqual(self.item(gid)["status"]["state"], "on", label)
            self.assertEqual(self.post({"id": gid, "remove": True})[0], 200)


class OscTest(Base):
    def allow_text(self, on=True):
        st, body, _ = self.call("POST", "/api/graphics/remote", {"allow": on}, token=self.full)
        self.assertEqual(st, 200, body)

    def test_text_send_and_hide_map_to_the_graphics_route(self):
        self.assertEqual(osc.translate("/pvj/graphic/2/text", ["NEXT UP"]), ("/api/graphics", {"slot": 2, "text": "NEXT UP"}))
        self.assertEqual(osc.translate("/pvj/graphic/1/send", ["THANK YOU"]), ("/api/graphics", {"slot": 1, "text": "THANK YOU", "on": True}))
        self.assertEqual(osc.translate("/pvj/graphic/hide", []), ("/api/graphics", {"all": False}))
        self.assertEqual(osc.translate("/pvj/graphic/hide", [0]), None, "a button's release does nothing")
        self.assertEqual(osc.translate("/pvj/graphic/3", []), ("/api/graphics", {"slot": 3, "on": "toggle"}))
        self.assertEqual(osc.translate("/pvj/graphic/3", [1]), ("/api/graphics", {"slot": 3, "on": True}))
        self.assertEqual(osc.translate("/pvj/graphic/3", [0.0]), ("/api/graphics", {"slot": 3, "on": False}))
        for address, args in (("/pvj/graphic/9/text", ["x"]), ("/pvj/graphic/0/send", ["x"]), ("/pvj/graphic/1/text", [5]), ("/pvj/graphic/1/send", []),
                              ("/pvj/graphic/1/frob", ["x"]), ("/pvj/graphic/x", [])):
            self.assertIsNone(osc.translate(address, args), address)
        long = osc.translate("/pvj/graphic/1/text", ["z" * 50000])[1]["text"]
        self.assertEqual(len(long), 2000)

    def test_an_osc_line_reaches_the_screen_and_a_burst_keeps_the_last(self):
        self.allow_text()
        gid = self.add(text="old")
        slot = self.item(gid)["slot"]
        mapped = osc.translate("/pvj/graphic/%d/send" % slot, ["  PUSHED ‮ FROM TOUCHOSC "])
        st, body = self.h("POST", mapped[0], mapped[1], osc.OSC_DEVICE, "osc")
        self.assertEqual(st, 200, body)
        it = self.item(gid)
        self.assertEqual((it["on"], it["text"]["value"], it["status"]["state"]), (True, "PUSHED  FROM TOUCHOSC", "on"))
        self.assertEqual(self.parts()[-1][1], 20)
        # a burst of pushes with the worker running: far fewer pictures than messages, the last one wins
        self.gfx.synchronous = False
        self.client.jobs.clear()
        for i in range(60):
            m = osc.translate("/pvj/graphic/%d/text" % slot, ["burst %d" % i])
            self.assertEqual(self.h("POST", m[0], m[1], osc.OSC_DEVICE, "osc")[0], 200)
        self.assertTrue(wait(lambda: not self.gfx.busy()))
        self.assertTrue(wait(lambda: self.gfx.state()["items"][0]["status"]["state"] == "on"))
        self.assertLess(len(self.client.jobs), 30)
        self.assertIn("burst 59", self.client.jobs[-1][0])
        self.assertTrue(self.gfx.stop())
        self.assertEqual(self.settings.data["graphics"]["items"][0]["text"]["value"], "burst 59", "stop writes what was owed")

    def test_osc_text_is_refused_until_the_owner_switches_it_on_and_showing_needs_no_switch(self):
        gid = self.add(text="kept")
        slot = self.item(gid)["slot"]
        for address in ("text", "send"):
            m = osc.translate("/pvj/graphic/%d/%s" % (slot, address), ["pushed by osc"])
            st, body = self.h("POST", m[0], m[1], osc.OSC_DEVICE, "osc")
            self.assertEqual(st, 403, address)
            self.assertIn("may not push text", body["error"])
        self.assertEqual(self.item(gid)["text"]["value"], "kept")
        self.assertEqual([c for c in self.client.jobs if "pushed by osc" in c[0]], [])
        self.assertFalse(self.item(gid)["on"])
        self.assertFalse(self.get()[1]["controllers_text"], "off by default")
        # showing, hiding and Hide all are not text: they go through
        for body in ({"slot": slot, "on": True}, {"slot": slot, "on": False}, {"all": False}):
            self.assertEqual(self.h("POST", "/api/graphics", body, osc.OSC_DEVICE, "osc")[0], 200, body)
        # the panel's own pushes, by an Operator, are not asked
        self.assertEqual(self.post({"id": gid, "text": "from the panel"})[0], 200)
        self.assertEqual(self.item(gid)["text"]["value"], "from the panel")
        # the Owner switches it on
        self.allow_text()
        self.assertTrue(self.get()[1]["controllers_text"])
        m = osc.translate("/pvj/graphic/%d/send" % slot, ["pushed by osc"])
        self.assertEqual(self.h("POST", m[0], m[1], osc.OSC_DEVICE, "osc")[0], 200)
        self.assertEqual((self.item(gid)["text"]["value"], self.item(gid)["on"]), ("pushed by osc", True))
        # and off again
        self.allow_text(False)
        m = osc.translate("/pvj/graphic/%d/text" % slot, ["after off"])
        self.assertEqual(self.h("POST", m[0], m[1], osc.OSC_DEVICE, "osc")[0], 403)
        self.assertEqual(self.item(gid)["text"]["value"], "pushed by osc")
        self.assertNotIn("graphics", self.settings.data.get("control", {}), "off is stored as nothing")

    def test_the_text_switch_is_the_owners_only(self):
        for who, token in (("operator", self.op), ("guest", self.guest_token), ("none", None)):
            st = self.call("POST", "/api/graphics/remote", {"allow": True}, token=token)[0]
            self.assertIn(st, (401, 403), who)
        for device, name in ((osc.OSC_DEVICE, "osc"), ({"id": "midi", "name": "MIDI", "role": "live"}, "midi")):
            self.assertEqual(self.h("POST", "/api/graphics/remote", {"allow": True}, device, name)[0], 403, name)
        self.assertFalse(self.get()[1]["controllers_text"])
        self.assertEqual(self.call("POST", "/api/graphics/remote", {"allow": "yes"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/graphics/remote", {"allow": True, "x": 1}, token=self.full)[0], 400)
        self.allow_text()
        self.assertTrue(self.api.settings.data["control"]["graphics"])
        self.assertNotIn(("POST", "/api/graphics/remote"), policy.LEGACY_LIVE)

    def test_a_controller_may_push_a_line_but_a_guest_never_and_a_slot_with_nothing_is_a_plain_404(self):
        self.allow_text()
        self.add(text="x")
        for who in (osc.OSC_DEVICE,):
            self.assertEqual(self.h("POST", "/api/graphics", {"slot": 1, "text": "ok"}, who, "osc")[0], 200)
            self.assertEqual(self.h("POST", "/api/graphics", {"slot": 5, "text": "ok"}, who, "osc")[0], 404)
            self.assertEqual(self.h("GET", "/api/graphics", {}, who, "osc")[0], 403, "a controller reads nothing back from here")
        self.assertEqual(self.h("POST", "/api/graphics", {"slot": 1, "text": "ok"}, self.guest, "10.0.0.5")[0], 403)
        self.assertIn(("POST", "/api/graphics"), policy.LEGACY_LIVE)
        self.assertNotIn(("GET", "/api/graphics"), policy.LEGACY_LIVE)

    def test_the_documented_addresses_are_the_ones_that_work(self):
        text = open(os.path.join(HERE, "pvj", "OSC.md"), encoding="utf-8").read()
        for address in ("/pvj/graphic/N", "/pvj/graphic/N/text", "/pvj/graphic/N/send", "/pvj/graphic/hide"):
            self.assertIn(address, text)


# --- 4. the fonts and what builds the helper ------------------------------------------------------------------------------
class FontsTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
            return f.read()

    def test_every_font_is_a_bundled_file_with_its_own_licence_text_and_is_listed(self):
        reuse, third = self.read("REUSE.toml"), self.read("THIRD_PARTY_LICENSES.md")
        folder = os.path.join(HERE, "pvj", "fonts")
        self.assertEqual(len(graphics.FONTS), 11)
        for f in graphics.FONTS:
            ttf = os.path.join(folder, f["id"] + ".ttf")
            self.assertTrue(os.path.isfile(ttf), f["id"])
            data = open(ttf, "rb").read()
            self.assertIn(data[:4], (b"\x00\x01\x00\x00", b"true"), "a TrueType file")
            sha = hashlib.sha256(data).hexdigest()
            self.assertIn(sha, third, "%s: its checksum is in THIRD_PARTY_LICENSES.md" % f["id"])
            self.assertIn("pvj/fonts/%s.ttf" % f["id"], reuse, "%s is in REUSE.toml" % f["id"])
            self.assertEqual(f["licence"], "SIL OFL 1.1")
            self.assertIn(f["name"], third)
            self.assertTrue(f["min"] <= f["default"] <= f["max"])
        licences = sorted(n for n in os.listdir(folder) if n.startswith("OFL-"))
        self.assertEqual(len(licences), 11, "one licence text beside each font")
        for n in licences:
            text = self.read("pvj", "fonts", n)
            self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text, n)
            self.assertRegex(text, r"^Copyright", n)
            self.assertIn("pvj/fonts/" + n, reuse)

    def test_the_font_files_are_the_releases_unchanged(self):
        # the checksums of the two that were already bundled as web fonts are the ones recorded for them in October
        third = self.read("THIRD_PARTY_LICENSES.md")
        for sha in ("0e094a7d3c7c4c25cf1310c4b30014f1dae9332220b1c2c88f4fa996f0b05053", "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda"):
            self.assertIn(sha, third)
            self.assertIn(sha, {hashlib.sha256(open(os.path.join(HERE, "pvj", "fonts", n), "rb").read()).hexdigest() for n in ("archivo.ttf", "jetbrains-mono.ttf")})

    def test_a_font_that_is_not_bundled_is_archivo_and_no_browser_is_sent_a_font(self):
        self.assertEqual(graphics.font_of("papyrus")["id"], "archivo")
        self.assertEqual(sorted(graphics.FONT_IDS), sorted(set(graphics.FONT_IDS)))
        api = self.read("pvj", "api.py") + self.read("pvj", "server.py")
        self.assertNotIn("fonts_dir", api, "nothing serves the folder")

    def test_the_helper_is_built_with_freetype_and_told_where_the_fonts_are(self):
        self.assertIn("freetype2", self.read("render", "Makefile"))
        self.assertIn("gfx.inc.c", self.read("render", "Makefile"))
        unit = self.read("install", "pvj-raster@.service")
        self.assertRegex(unit, r"ExecStart=@PVJ_DIR@/render/pvj-raster --fonts @PVJ_DIR@/pvj/fonts\n")
        sh = self.read("install", "install.sh")
        self.assertIn("libfreetype-dev", sh)
        self.assertIn("pkg-config --exists libpng libjpeg freetype2", sh)
        self.assertIn("libfreetype-dev", self.read("image", "stage-pvj", "00-install-pvj", "00-packages-nr").split())
        self.assertIn("libfreetype-dev", self.read(".github", "workflows", "pvj.yml"))
        self.assertIn("InaccessiblePaths=-/var/lib/pvj", unit)
        self.assertNotIn("ReadWritePaths", unit)

    def test_the_paths_of_the_font_folder(self):
        from pvj import paths
        self.assertEqual(paths.fonts_dir({}), os.path.join(HERE, "pvj", "fonts"))
        self.assertEqual(paths.fonts_dir({"PVJ_FONTS_DIR": "/x"}), "/x")


class RealPlayerTest(ServerBase):
    """The real pvj.player.Player over a stand-in for mpv on its socket (tests/fakempv.py, which checks that an overlay's
    file is long enough for what it is told): what the engine really sends, with the real player's own calls."""

    def setUp(self):
        super().setUp()
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"), size=(1280, 720))
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.api.graphics.client = FakeDrawing()
        self.gfx = self.api.gfx
        self.gfx.synchronous = True
        self.addCleanup(self.gfx.stop)

    def test_the_player_takes_each_picture_at_its_place_and_its_id_and_lets_it_go(self):
        self.gfx.change({"add": "text"})
        self.gfx.change({"add": "text"})
        a, b = [i["id"] for i in self.gfx.state()["items"]]
        self.gfx.change({"id": a, "text": "AAAA", "on": True})
        self.gfx.change({"id": b, "text": "BBBBBBBB", "on": True})
        self.gfx.change({"id": b, "set": {"place": {"anchor": "tl"}}})
        self.assertEqual(sorted(self.mpv.overlays), [20, 21])
        x, y, offset, w, h, alpha, shown = self.mpv.overlays[20]
        self.assertEqual((offset, w, h, alpha, shown), (0, 20 + 10 * 4, 60, 0xff, None))
        self.assertEqual((x, y), graphics.position("bc", 0, 0, w, h, 1280, 720), "the screen's own size is the real player's")
        x, y, offset, w, h, alpha, shown = self.mpv.overlays[21]
        self.assertEqual((x, y), graphics.position("tl", 0, 0, w, h, 1280, 720))
        self.gfx.change({"id": a, "on": False})
        self.assertEqual(sorted(self.mpv.overlays), [21])
        self.gfx.change({"all": False})
        self.assertEqual(self.mpv.overlays, {})
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("graphic-")], [])
        self.gfx.change({"all": True})
        self.assertEqual(sorted(self.mpv.overlays), [21], "the stage back: what was on is drawn again")


class ClientTest(unittest.TestCase):
    def test_the_helpers_answers_are_checked_before_they_are_believed(self):
        def answer(out):
            c = raster.Client(exec_bin="/nonexistent", env={})
            c._run = lambda request, payload: out
            return c.graphic("kind text\nTEXT\nx")
        ok = answer(b"OKRAW 2 1 0 8\n" + b"\x00" * 8)
        self.assertEqual((ok["w"], ok["h"], ok["missing"], len(ok["data"]), ok["png"]), (2, 1, 0, 8, False))
        for out, status in ((b"", 413), (b"ERR there is too much text\n", 422), (b"nonsense\n", 502), (b"OKRAW 2 1 0 9\n" + b"\x00" * 9, 502),
                            (b"OKRAW 2 1 0 4\n" + b"\x00" * 4, 502), (b"OKRAW 0 1 0 0\n", 502), (b"OKRAW 5000 5000 0 100000000\n" + b"\x00" * 100000000, 502),
                            (b"OKPNG 2 2 0 8\n" + b"\x00" * 8, 502)):
            with self.assertRaises(raster.RasterError) as cm:
                answer(out)
            self.assertEqual(cm.exception.status, status, out[:20])
        good = png(2, 2)
        self.assertTrue(answer(b"OKPNG 2 2 0 %d\n" % len(good) + good)["png"])
        with self.assertRaises(raster.RasterError):
            answer(b"OKPNG 3 3 0 %d\n" % len(good) + good)           # a PNG of another size than it says
        with self.assertRaises(raster.RasterError) as cm:
            answer(b"ERR " + b"x" * 5000 + b"\n")
        self.assertLessEqual(len(cm.exception.message), 300)

    def test_a_direct_run_is_given_the_font_folder_and_the_limits(self):
        seen = []
        import subprocess as sp
        real = sp.run

        def run(cmd, **kw):
            seen.append((cmd, kw["input"][:30]))
            return type("R", (), {"stdout": b"ERR no\n"})()
        sp.run = run
        self.addCleanup(setattr, sp, "run", real)
        c = raster.Client(exec_bin="/bin/true", env={"PVJ_FONTS_DIR": "/some/fonts"})
        with self.assertRaises(raster.RasterError):
            c.graphic("kind text\nTEXT\nx")
        self.assertEqual(seen[0][0], ["/bin/true", "--self-limit", "--fonts", "/some/fonts"])
        self.assertTrue(seen[0][1].startswith(b"RASTER1 GFX "))


if __name__ == "__main__":
    unittest.main()
