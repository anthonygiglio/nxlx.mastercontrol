# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import math
import os
import re
import struct
import shutil
import tempfile
import threading
import time
import unittest

from pvj import autostart, mapper as M
from pvj.settings import Settings
from tests.test_server import ServerBase

W, H = 1920, 1080


def quad(verts, tex=None, **kw):
    return dict({"type": "quad", "vertices": verts, "tex": tex or [[0, 0], [1, 0], [1, 1], [0, 1]]}, **kw)


def grid(cols, rows, x0=100, y0=100, dx=200, dy=150, bend=0):
    verts = [[x0 + dx * c + (bend if (r, c) == (rows // 2, cols // 2) else 0), y0 + dy * r] for r in range(rows + 1) for c in range(cols + 1)]
    return {"type": "grid", "cols": cols, "rows": rows, "vertices": verts, "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}


def exact(surfaces, p):
    """The picture point at screen point p by the direct method (the first surface that holds p), or None."""
    for s in surfaces:
        if not s["on"]:
            continue
        for scr, cm in M.surface_maps(s):
            uv, inside = M.cell_uv(cm, p)
            if inside:
                return uv
    return None


def lookup(table, p):
    """Emulate the show shader: bilinear between table cell centres, as the GPU filter does. (u, v, dA)."""
    lw, lh, vals = table
    tw, th = W / lw, H / lh
    fx, fy = p[0] / tw - 0.5, p[1] / th - 0.5
    i, j = math.floor(fx), math.floor(fy)
    wx, wy = fx - i, fy - j

    def at(a, b):
        a, b = min(max(a, 0), lw - 1), min(max(b, 0), lh - 1)
        return vals[6 * (b * lw + a):6 * (b * lw + a) + 3]
    A, B, C, D = at(i, j), at(i + 1, j), at(i, j + 1), at(i + 1, j + 1)
    return [(A[c] * (1 - wx) + B[c] * wx) * (1 - wy) + (C[c] * (1 - wx) + D[c] * wx) * wy for c in range(3)]


class GeometryTest(unittest.TestCase):
    def test_homography_maps_the_corners(self):
        src = [(10, 20), (900, 60), (850, 700), (40, 650)]
        h = M.homography(src, M.UNIT_QUAD)
        for p, q in zip(src, M.UNIT_QUAD):
            self.assertAlmostEqual(M.apply(h, p)[0], q[0], places=9)
            self.assertAlmostEqual(M.apply(h, p)[1], q[1], places=9)

    def test_affine_maps_the_corners(self):
        src = [(0, 100), (50, 0), (100, 100)]
        a = M.affine(src, M.UNIT_TRI)
        for p, q in zip(src, M.UNIT_TRI):
            self.assertAlmostEqual(M.apply(a, p)[0], q[0], places=9)

    def test_inverse_bilinear_round_trips(self):
        q = [(100, 100), (500, 130), (560, 420), (90, 380)]
        for u in (0.0, 0.25, 0.5, 0.9, 1.0):
            for v in (0.0, 0.3, 0.77, 1.0):
                p = M._bilerp(q, u, v)
                got = M.inv_bilinear(p, q)
                self.assertAlmostEqual(got[0], u, places=7)
                self.assertAlmostEqual(got[1], v, places=7)
        par = [(0, 0), (100, 0), (100, 50), (0, 50)]           # a parallelogram takes the linear path
        self.assertAlmostEqual(M.inv_bilinear((25, 10), par)[0], 0.25)

    def test_grid_cells_agree_along_shared_edges(self):
        """Per-cell perspective broke the picture at every inner grid line on the Pi; bilinear cells cannot."""
        g = M.validate_mapping({"surfaces": [grid(2, 2, bend=180)]})[0]
        sm = M.surface_maps(g)
        a, b = g["vertices"][3], g["vertices"][4]                 # the inner edge between the left cells
        for k in range(1, 20):
            p = (a[0] + (b[0] - a[0]) * k / 20, a[1] + (b[1] - a[1]) * k / 20)
            u1, _ = M.cell_uv(sm[0][1], p)
            u2, _ = M.cell_uv(sm[2][1], p)
            self.assertLess(math.hypot(u1[0] - u2[0], u1[1] - u2[1]), 1e-9)


class ValidationTest(unittest.TestCase):
    def test_a_good_mapping_is_cleaned(self):
        out = M.validate_mapping({"surfaces": [quad([[0, 0], [100, 0], [100, 100], [0, 100]], name=" Wall "), grid(2, 2),
                                               {"type": "triangle", "vertices": [[0, 100], [50, 0], [100, 100]], "tex": [[0, 1], [0.5, 0], [1, 1]]}]})
        self.assertEqual([s["type"] for s in out], ["quad", "grid", "triangle"])
        self.assertEqual(out[0]["name"], "Wall")
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{8}", s["id"]) for s in out))

    def test_bad_mappings_are_refused(self):
        sq = [[0, 0], [100, 0], [100, 100], [0, 100]]
        bad = [
            {"surfaces": "x"},
            {"surfaces": [quad(sq)] * (M.MAX_SURFACES + 1)},
            {"surfaces": [{"type": "hexagon", "vertices": sq, "tex": sq}]},
            {"surfaces": [quad(sq[:3])]},
            {"surfaces": [quad([[0, 0], [100, 0], [0, 100], [100, 100]])]},                 # crossed: folds over itself
            {"surfaces": [quad([[0, 0], [100, 0], [50, 10], [0, 100]])]},                   # bent inwards
            {"surfaces": [quad(sq, tex=[[0, 0], [2, 0], [1, 1], [0, 1]])]},                 # picture outside 0..1
            {"surfaces": [quad([[0, 0], [1e9, 0], [100, 100], [0, 100]])]},
            {"surfaces": [quad([[0, 0], [float("nan"), 0], [100, 100], [0, 100]])]},
            {"surfaces": [quad([[0, 0], [True, 0], [100, 100], [0, 100]])]},
            {"surfaces": [quad(sq, name="a{\\b}")]},
            {"surfaces": [quad(sq, name="x" * 41)]},
            {"surfaces": [quad(sq, id="ABCDEFGH")]},
            {"surfaces": [quad(sq, id="aaaaaaaa"), quad(sq, id="aaaaaaaa")]},
            {"surfaces": [dict(grid(2, 2), cols=9)]},
            {"surfaces": [grid(8, 8), grid(1, 1)]},                                         # 65 cells
            {"surfaces": [dict(quad(sq), on="yes")]},
            {"surfaces": [dict(grid(2, 1), vertices=[[0, 0], [100, 0], [50, 0], [0, 100], [100, 100], [50, 100]])]},  # folded
        ]
        for b in bad:
            with self.assertRaises(M.MapperError, msg=str(b)[:80]):
                M.validate_mapping(b)
        for x in (float("inf"), float("nan")):
            with self.assertRaises(M.MapperError):
                M._f(x)

class TableTest(unittest.TestCase):
    """The warp table, checked against the direct method by emulating what the GPU does with it."""

    def check(self, surfaces, samples=4000, tolerance=0.25):
        clean = M.validate_mapping({"surfaces": surfaces})
        table = M.warp_table(clean, W, H)
        inner, edge = [], 0
        for k in range(samples):
            p = ((k * 7919) % W + 0.5, (k * 104729 // W * 13) % H + 0.5)
            want = exact(clean, p)
            u, v, d = lookup(table, p)
            if d >= M.EDGE - 1e-6:                     # well inside a surface: the picture point must match
                self.assertIsNotNone(want, p)
                inner.append(math.hypot((u - want[0]) * W, (v - want[1]) * H))
            elif d <= -M.EDGE + 1e-6:                  # well outside every surface: nothing there
                self.assertIsNone(want, p)
            else:
                edge += 1
        self.assertGreater(len(inner), 100)
        self.assertLess(max(inner), tolerance)
        return table

    def test_perspective_quad(self):
        self.check([quad([[300, 120], [1600, 200], [1500, 950], [200, 1000]])])

    def test_triangle_and_bent_grid_over_a_quad(self):
        # 1 pixel: across the crease at a strongly bent grid point the table's interpolation is off by up to 0.6
        self.check([grid(3, 2, x0=500, y0=300, dx=250, dy=200, bend=150),
                    {"type": "triangle", "vertices": [[100, 900], [300, 500], [500, 900]], "tex": [[0, 1], [0.5, 0], [1, 1]]},
                    quad([[50, 50], [1800, 80], [1850, 1000], [60, 1050]])], tolerance=1.0)

    def test_hidden_surfaces_are_not_drawn_and_nothing_is_black(self):
        lw, lh, vals = M.warp_table(M.validate_mapping({"surfaces": [dict(quad([[0, 0], [W, 0], [W, H], [0, H]]), on=False)]}), W, H)
        self.assertTrue(all(vals[6 * k + 2] <= -M.EDGE for k in range(lw * lh)))
        lw, lh, vals = M.warp_table([], W, H)
        self.assertEqual((lw, lh, len(vals)), (480, 270, 480 * 270 * 6))

    def test_the_edge_distance_is_exact_near_an_edge(self):
        """The distance, interpolated by the GPU, keeps a straight edge sharp although the table is coarse."""
        s = M.validate_mapping({"surfaces": [quad([[400, 300], [1400, 330], [1380, 900], [420, 880]])]})
        table = M.warp_table(s, W, H)
        for x in range(600, 1200, 37):
            y_edge = 300 + 30 * (x - 400) / 1000.0       # the top edge
            for off in (-3.0, -1.0, 1.0, 3.0):
                d = lookup(table, (x, y_edge + off))[2]
                self.assertAlmostEqual(d, off, delta=0.2)

    def test_two_layers_where_one_surface_lies_over_another(self):
        """At the top surface's edge over a lower one, layer B carries the lower surface, so the join is smooth."""
        low = quad([[100, 100], [1800, 100], [1800, 1000], [100, 1000]], name="low")
        top = quad([[600, 400], [1200, 400], [1200, 800], [600, 800]], name="top", tex=[[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]])
        clean = M.validate_mapping({"surfaces": [top, low]})
        lw, lh, vals = M.warp_table(clean, W, H)
        k = (400 // 4) * lw + (1200 // 4)          # on the top surface's right edge
        ua, va, da, db, ub, vb = vals[6 * k:6 * k + 6]
        self.assertLess(abs(da), M.EDGE)
        self.assertGreaterEqual(db, M.EDGE - 1e-6)                    # the lower surface is fully there
        want = exact([clean[1]], ((1200 // 4 + 0.5) * 4, (400 // 4 + 0.5) * 4))
        self.assertAlmostEqual(ub, want[0], places=6)

    def test_a_64_cell_grid_builds_in_reasonable_time(self):
        g = [grid(8, 8, x0=80, y0=50, dx=220, dy=122, bend=40)]
        start = time.monotonic()
        self.check(g, samples=500, tolerance=1.0)
        self.assertLess(time.monotonic() - start, 20)


class ShaderTest(unittest.TestCase):
    def test_direct_shader_holds_only_numbers_and_draws_outlines_when_editing(self):
        s = M.validate_mapping({"surfaces": [quad([[0, 0], [100, 0], [100, 100], [0, 100]], name="Front wall"), grid(2, 2)]})
        text = M.shader(s, {"selected": s[1]["id"], "corner": 4})
        self.assertIn("//!HOOK OUTPUT", text)
        self.assertNotIn("Front wall", text)                       # names never go into the shader
        self.assertEqual(text.count("ibl(p / 1024.0"), 4)          # the grid's cells are bilinear
        self.assertIn("vec4(1.0, 0.16, 0.47, 1.0)", text)            # the chosen corner
        self.assertNotIn("seg(p", M.shader(s))                      # no outlines outside editing
        self.assertNotIn("nan", text.lower())

    def test_show_shader_carries_the_table_as_32_bit_floats(self):
        """mpv refused half floats: it wants 16 bytes a cell for an rgba16f texture."""
        lw, lh, vals = M.warp_table([], 64, 36, step=4)
        text = M.warp_shader(lw, lh, vals)
        hexes = [l for l in text.splitlines() if re.fullmatch(r"[0-9a-f]+", l)]
        self.assertEqual([len(h) // 2 for h in hexes], [16 * 16 * 9, 16 * 16 * 9])
        a = struct.unpack("<%df" % (16 * 9 * 4), bytes.fromhex(hexes[0]))
        self.assertEqual(a[2], -M.EDGE)
        self.assertIn("//!BIND PVJWARPA", text)
        self.assertNotIn("if (ka", text)                          # the branch was measured slower


class EngineTest(unittest.TestCase):
    """The engine against a small fake player."""

    class Player:
        def __init__(self, rundir):
            self.rundir = rundir
            self.shaders = []
            self.mode = False
            self.fail = False

        def osd_size(self):
            return (W, H)

        def set_mapping_mode(self, on):
            if self.fail:
                raise OSError("player down")
            self.mode = on

        def set_shaders(self, paths):
            if self.fail:
                raise OSError("player down")
            for p in paths:
                with open(p) as f:
                    f.read(10)                 # the player must be able to read it when it is set
            self.shaders = list(paths)

    class Registry:
        on = True

        def enabled(self, mid):
            return self.on

    def setUp(self):
        self.rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rundir, True)
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        self.addCleanup(shutil.rmtree, os.path.dirname(self.settings.path), True)
        self.settings.load()

        class Api:
            pass
        self.api = Api()
        self.api.settings, self.api.player, self.api.registry = self.settings, self.Player(self.rundir), self.Registry()
        self.e = M.Engine(self.api, log=lambda *_: None)

    def wait_state(self, state):
        end = time.monotonic() + 20
        while self.e.status["state"] != state and time.monotonic() < end:
            time.sleep(0.02)
        self.assertEqual(self.e.status["state"], state, self.e.status)

    def files(self):
        return sorted(n for n in os.listdir(self.rundir) if n.startswith("mapper-"))

    def test_add_edit_show_and_off(self):
        st = self.e.handle({"action": "add", "type": "quad"})
        sid = st["surfaces"][0]["id"]
        self.assertEqual(st["edit"]["selected"], sid)
        self.assertEqual(st["status"]["state"], "off")                     # mapping not switched on yet
        st = self.e.handle({"action": "edit", "on": True})
        self.assertEqual(st["status"]["state"], "editing")
        with open(self.api.player.shaders[0]) as f:
            self.assertIn("nxlx mapper (editing)", f.read())
        self.e.handle({"action": "move", "id": sid, "corner": 0, "dx": -50, "dy": 10})
        self.e.handle({"action": "edit", "on": False})
        self.e.handle({"action": "on", "on": True})
        self.wait_state("on")
        with open(self.api.player.shaders[0]) as f:
            self.assertIn("//!TEXTURE PVJWARPA", f.read())
        self.assertEqual(len(self.files()), 1)                               # old files are removed
        self.assertTrue(self.api.player.mode)                                # stretched, 8-bit buffers
        self.assertEqual(oct(os.stat(self.api.player.shaders[0]).st_mode & 0o777), "0o640")
        self.e.handle({"action": "on", "on": False})
        self.assertEqual((self.api.player.shaders, self.files(), self.api.player.mode), ([], [], False))

    def test_changes_are_saved_and_survive_a_reload(self):
        sid = self.e.handle({"action": "add", "type": "grid"})["surfaces"][0]["id"]
        self.e.handle({"action": "grid", "id": sid, "cols": 4, "rows": 3})
        self.e.handle({"action": "rename", "id": sid, "name": "Curved screen"})
        again = Settings(self.settings.path).load()
        s = again["mapper"]["surfaces"][0]
        self.assertEqual((s["cols"], s["rows"], len(s["vertices"]), s["name"]), (4, 3, 20, "Curved screen"))
        self.assertEqual(again["mapper"]["screen"], [W, H])

    def test_moves_place_order_show_and_remove(self):
        a = self.e.handle({"action": "add", "type": "quad"})["surfaces"][0]["id"]
        b = self.e.handle({"action": "add", "type": "triangle"})["surfaces"][0]["id"]
        self.assertEqual([s["id"] for s in self.e.state()["surfaces"]], [b, a])       # a new surface goes on top
        self.e.handle({"action": "order", "id": b, "dir": "down"})
        self.assertEqual([s["id"] for s in self.e.state()["surfaces"]], [a, b])
        before = self.e.state()["surfaces"][0]["vertices"]
        st = self.e.handle({"action": "move", "id": a, "corner": -1, "dx": 10, "dy": -5})        # the whole surface
        self.assertEqual(st["surfaces"][0]["vertices"], [[x + 10, y - 5] for x, y in before])
        st = self.e.handle({"action": "place", "id": a, "target": "picture", "corner": 1, "x": 0.75, "y": 0})
        self.assertEqual(st["surfaces"][0]["tex"][1], [0.75, 0.0])
        st = self.e.handle({"action": "move", "id": a, "target": "picture", "corner": 1, "dx": 9999 - 5999, "dy": 0})
        self.assertEqual(st["surfaces"][0]["tex"][1], [1.0, 0.0])                               # kept inside the picture
        self.assertFalse(self.e.handle({"action": "show", "id": a, "on": False})["surfaces"][0]["on"])
        self.assertEqual([s["id"] for s in self.e.handle({"action": "remove", "id": a})["surfaces"]], [b])

    def test_a_move_that_folds_a_surface_is_refused_and_nothing_changes(self):
        sid = self.e.handle({"action": "add", "type": "quad"})["surfaces"][0]["id"]
        before = self.e.state()["surfaces"]
        with self.assertRaises(M.MapperError):
            self.e.handle({"action": "move", "id": sid, "corner": 0, "dx": 2000, "dy": 2000})
        self.assertEqual(self.e.state()["surfaces"], before)

    def test_bad_requests(self):
        sid = self.e.handle({"action": "add", "type": "quad"})["surfaces"][0]["id"]
        for body in ({"action": "nope"}, [], {"action": "add", "type": "hexagon"}, {"action": "remove", "id": "ffffffff"},
                     {"action": "move", "id": sid, "corner": 9, "dx": 1}, {"action": "move", "id": sid, "dx": float("inf")},
                     {"action": "move", "id": sid, "dx": 5000}, {"action": "move", "id": sid, "target": "sky"},
                     {"action": "place", "id": sid, "corner": -1, "x": 1, "y": 1}, {"action": "order", "id": sid, "dir": "left"},
                     {"action": "grid", "id": sid, "cols": 2, "rows": 2}, {"action": "edit", "corner": 7},
                     {"action": "on", "on": 1}, {"action": "save", "name": "../x"}, {"action": "load", "name": "missing"},
                     {"action": "rename", "id": sid, "name": "<b>"}):
            with self.assertRaises(M.MapperError, msg=str(body)):
                self.e.handle(body)

    def test_saved_mappings_follow_a_different_screen_size(self):
        self.e.handle({"action": "add", "type": "quad"})
        self.e.handle({"action": "save", "name": "Show A"})
        v = self.e.state()["surfaces"][0]["vertices"]
        self.e.handle({"action": "remove", "id": self.e.state()["surfaces"][0]["id"]})
        self.api.player.osd_size = lambda: (1280, 720)                                    # a smaller projector
        st = self.e.handle({"action": "load", "name": "Show A"})
        self.assertEqual(st["surfaces"][0]["vertices"][0], [round(v[0][0] * 1280 / W, 4), round(v[0][1] * 720 / H, 4)])
        self.assertEqual(st["sets"], ["Show A"])
        for i in range(M.MAX_SETS - 1):
            self.e.handle({"action": "save", "name": "s%d" % i})
        with self.assertRaises(M.MapperError):
            self.e.handle({"action": "save", "name": "one too many"})
        self.e.handle({"action": "save", "name": "Show A"})                               # overwriting is fine
        self.assertNotIn("Show A", self.e.handle({"action": "delete", "name": "Show A"})["sets"])

    def test_a_player_that_is_down_is_a_status_not_a_crash(self):
        self.e.handle({"action": "add", "type": "quad"})
        self.api.player.fail = True
        st = self.e.handle({"action": "edit", "on": True})
        self.assertEqual(st["status"]["state"], "error")

    def test_an_older_build_never_lands_after_a_newer_change(self):
        self.e.handle({"action": "add", "type": "grid"})
        self.e.handle({"action": "grid", "id": self.e.state()["surfaces"][0]["id"], "cols": 8, "rows": 8})
        self.e.handle({"action": "on", "on": True})                   # a slow build starts
        self.e.handle({"action": "on", "on": False})                  # and is overtaken at once
        time.sleep(0.2)
        end = time.monotonic() + 20
        while any(t.name == "mapper-build" for t in threading.enumerate()) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual((self.api.player.shaders, self.files()), ([], []))

    def test_a_drag_costs_one_build_at_a_time(self):
        """Review finding: every change while the mapping was on started its own build thread (25 at once)."""
        sid = self.e.handle({"action": "add", "type": "grid"})["surfaces"][0]["id"]
        self.e.handle({"action": "grid", "id": sid, "cols": 8, "rows": 8})
        self.e.handle({"action": "on", "on": True})
        peak = 0
        for k in range(40):
            self.e.handle({"action": "move", "id": sid, "corner": 0, "dx": 1 if k % 2 else -1, "dy": 0})
            peak = max(peak, sum(1 for t in threading.enumerate() if t.name == "mapper-build"))
        self.assertLessEqual(peak, 1)
        self.wait_state("on")
        with open(self.api.player.shaders[0]) as f:
            text = f.read()
        final = M.validate_mapping({"surfaces": self.e.current()})
        self.assertEqual(text, M.warp_shader(*M.warp_table(final, W, H)))        # the last change is what is shown

    def test_an_old_build_stops_early(self):
        calls = []

        def stop():
            calls.append(1)
            return len(calls) > 3
        self.assertIsNone(M.warp_table(M.validate_mapping({"surfaces": [grid(8, 8)]}), W, H, stop=stop))
        self.assertEqual(len(calls), 4)

    def test_cleanup_never_removes_a_newer_file(self):
        """Review finding: an older switch's cleanup removed the file a newer change was about to use."""
        self.e.handle({"action": "add", "type": "quad"})
        self.e.handle({"action": "edit", "on": True})
        written = []
        real = self.api.player.set_mapping_mode

        def meanwhile(on):                              # another request writes its file during this switch
            if not written:
                written.append(self.e._write("// newer"))
            real(on)
        self.api.player.set_mapping_mode = meanwhile
        path = self.e._write("// this one")
        self.assertTrue(self.e._commit(self.e._gen, path, "editing"))
        self.assertTrue(os.path.exists(written[0]))
        self.assertEqual(sorted(self.files()), sorted([os.path.basename(path), os.path.basename(written[0])]))
        # and a switch for an older change gives up without touching anything
        self.assertFalse(self.e._commit(self.e._gen - 1, None, "off"))
        self.assertTrue(os.path.exists(written[0]))

    def test_status_of_an_older_change_never_overwrites_a_newer_one(self):
        self.e._gen = 5
        self.e._set_status("editing", "", 5)
        self.e._set_status("building", "", 4)
        self.assertEqual(self.e.status["state"], "editing")

    def test_a_screen_too_large_to_scale_to_does_not_lock_the_mapping(self):
        """Review finding: scaled past the coordinate limit, every later change was refused."""
        sid = self.e.handle({"action": "add", "type": "quad"})["surfaces"][0]["id"]
        self.e.handle({"action": "move", "id": sid, "corner": -1, "dx": 1500, "dy": 0})
        before = self.e.state()["surfaces"][0]["vertices"]
        self.api.player.osd_size = lambda: (W * 20, H * 20)
        st = self.e.handle({"action": "move", "id": sid, "corner": 0, "dx": -10, "dy": 0})    # still accepted
        self.assertEqual(st["surfaces"][0]["vertices"][0], [before[0][0] - 10, before[0][1]])   # left where it was
        self.assertEqual(self.e.handle({"action": "remove", "id": sid})["surfaces"], [])

    def test_files_from_an_earlier_run_are_removed(self):
        stale = os.path.join(self.rundir, "mapper-1-1.glsl")
        open(stale, "w").close()
        other = os.path.join(self.rundir, "overlay.bgra")
        open(other, "w").close()
        self.e.handle({"action": "add", "type": "quad"})
        self.e.handle({"action": "edit", "on": True})
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(other))

    def test_module_off_takes_the_mapping_away(self):
        self.e.handle({"action": "add", "type": "quad"})
        self.e.handle({"action": "edit", "on": True})
        self.api.registry.on = False
        self.e.apply()
        self.assertEqual(self.api.player.shaders, [])
        self.assertEqual(self.e.state()["status"]["message"], "the module is off")


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, body, token=None):
        return self.call("POST", "/api/mapper", body, token=token or self.full)

    def test_module_gate_roles_and_a_round_trip(self):
        self.assertEqual(self.post({"action": "add", "type": "quad"})[0], 409)
        self.call("POST", "/api/modules/mapper", {"enabled": True}, token=self.full)
        live = self.call("POST", "/api/devices/invite", {"name": "p", "role": "live"}, token=self.full)[1]["token"]
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        self.assertEqual(self.post({"action": "add", "type": "quad"}, token=live)[0], 403)
        st, body, _ = self.post({"action": "add", "type": "quad"})
        self.assertEqual(st, 200)
        self.assertEqual(body["screen"], [1920, 1080])
        self.assertEqual(self.call("GET", "/api/mapper", token=view)[1]["surfaces"], body["surfaces"])
        self.assertEqual(self.post({"action": "add", "type": "circle"})[0], 400)
        self.post({"action": "edit", "on": True})
        self.assertTrue(any(c[0] == "set_shaders" and c[1] for c in self.player.calls))
        self.call("POST", "/api/modules/mapper", {"enabled": False}, token=self.full)
        self.assertEqual([c for c in self.player.calls if c[0] == "set_shaders"][-1], ("set_shaders", []))


class AutostartHookTest(unittest.TestCase):
    def test_a_restarted_player_gets_the_mapping_back(self):
        settings = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        settings.load()
        calls = []

        class Api:
            pid = None

            def __init__(self):
                outer = self

                class Ipc:
                    def request(self, *cmd):
                        return outer.pid
                self.player = type("P", (), {"ipc": Ipc()})()

            def apply_mapper(self):
                calls.append("mapper")
        api = Api()
        a = autostart.Autostart(api, settings, log=lambda *_: None, sleep=lambda *_: None)
        api.pid = 10
        a.tick()
        self.assertEqual(calls, [])                                   # nothing mapped: nothing to do
        settings.data["mapper"]["surfaces"] = [{"id": "aaaaaaaa"}]
        api.pid = 11
        a.tick()
        self.assertEqual(calls, ["mapper"])


if __name__ == "__main__":
    unittest.main()
