# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shaders on a real mpv that really draws: mpv's GPU output in a window on a virtual display (xvfb), with Mesa's
software GPU. The other tests run mpv with --vo=null, which never compiles a shader, so "no error in the log" would
pass there whatever the shader held. Here every claim is checked on the picture itself (a screenshot of the window),
once on OpenGL ES (what a Raspberry Pi uses; the stricter shader language) and once on desktop OpenGL.

Run:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m unittest tests.test_shaders_gpu -v
Without PVJ_GPU_TEST it is skipped (CI sets it, so there a missing display or GPU is a failure, not a skip).
This is a software GPU: it says nothing about speed, and nothing about the GPU driver of a real board.
"""
import os
import random
import shutil
import struct
import time
import unittest
import zlib

from pvj import shaders as S, vibes as V
from pvj.api import ApiError
from pvj.player import Player
from tests.test_server import ServerBase

GPU = os.environ.get("PVJ_GPU_TEST") == "1"
W, H = 320, 180
CLIP = "av://lavfi:testsrc=size=160x90:rate=25"
PROBE = "/*{}*/\nvoid main() { gl_FragColor = vec4(isf_FragNormCoord, 0.25, 1.0); }\n"
PROBE_COORD = "/*{}*/\nvoid main() { gl_FragColor = vec4(gl_FragCoord.xy / RENDERSIZE, 0.25, 1.0); }\n"
PROBE_TIME = ("/*{}*/\nvoid main() {\n    float inside = (TIME > 100000.0 && TIME < 100600.0) ? 1.0 : 0.0;\n"
              "    gl_FragColor = vec4(inside, fract(TIME / 8.0), 0.0, 1.0);\n}\n")
BROKEN = "/*{\n \"DESCRIPTION\": \"a mistake on line 6\"\n}*/\n\nvoid main() {\n    gl_FragColor = vec4(nonsense, 1.0);\n}\n"
SWAP = "//!HOOK OUTPUT\n//!BIND HOOKED\n//!DESC swap\nvec4 hook() { return vec4(HOOKED_tex(HOOKED_pos).bgr, 1.0); }\n"


def png_rows(path):
    """(width, height, rows of (r, g, b)) of an 8-bit PNG, with the standard library only."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w, h, bpp = 8, b"", 0, 0, 3
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), (depth, ctype)
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += body
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)])
        prev = line
    return w, h, rows


class GpuCase:
    ES = "yes"

    def setUp(self):
        super().setUp()
        self.real = Player(extra_args=["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + self.ES, "--ao=null",
                                       "--geometry=%dx%d" % (W, H), "--no-border"], rundir=self.rundir)
        self.addCleanup(self.real.stop)
        self.api.player = self.real
        self.real.play([CLIP], windowed=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.real.osd_size() != (W, H):
            time.sleep(0.05)
        self.assertEqual(self.real.osd_size(), (W, H), "the player has no window: is there a display (xvfb-run)?")
        self.assertEqual(self.real.ipc.request("get_property", "current-vo"), "gpu")
        self.real.ipc.request("set_property", "screenshot-format", "png")
        self.real.ipc.request("set_property", "screenshot-high-bit-depth", False)
        self.api.registry.set_enabled("shaders", True)
        self.engine = self.api.shaders
        self.engine.log = lambda *_: None

    def shot(self):
        path = os.path.join(self.tmp, "shot.png")
        if os.path.exists(path):
            os.unlink(path)
        self.real.ipc.request("screenshot-to-file", path, "window")
        w, h, rows = png_rows(path)
        self.assertEqual((w, h), (W, H))
        return rows

    def near(self, got, want, slack=8):
        self.assertTrue(all(abs(a - b) <= slack for a, b in zip(got, want)), "%s is not near %s" % (got, want))

    def shaders_in_player(self):
        return [os.path.basename(p) for p in self.real.ipc.request("get_property", "glsl-shaders")]

    def show(self, sid, **kw):
        r = self.engine.show(sid, **kw)
        self.assertTrue(r["ok"], r)
        self.assertIs(self.engine.playing["checked"], True, "the player never drew a frame with the shader")
        return r

    # -- the bundled set --
    def test_every_bundled_shader_compiles_and_draws_a_picture(self):
        ids = [s["id"] for s in self.engine.library() if s["source"] == "bundled"]
        self.assertEqual(len(ids), 10)
        rng = random.Random(11)
        for sid in ids:
            for varied in (False, True):
                self.engine._checked.clear()                       # watch the player's log every time
                inputs = next(s["inputs"] for s in self.engine.library() if s["id"] == sid)
                kw = {"values": V.vary(inputs, rng), "hue": 77.0, "offset": 321.5} if varied else {}
                self.show(sid, **kw)
                self.assertIsNone(self.engine.error, sid)
                rows = self.shot()
                colours = {rows[y][x] for y in range(2, H, 5) for x in range(2, W, 5)}
                self.assertGreater(len(colours), 40, "%s drew a flat picture (%d colours)" % (sid, len(colours)))
                self.assertGreater(max(max(c) for c in colours), 60, "%s drew a dark picture" % sid)
        self.assertEqual(self.shaders_in_player(), ["shader-%d-20.glsl" % os.getpid()])

    # -- where things are, and what TIME does --
    def test_coordinates_follow_isf_with_the_origin_at_the_bottom_left(self):
        for name, text in (("probe.fs", PROBE), ("coord.fs", PROBE_COORD)):
            self.engine.upload(name, text)
            self.show(name)
            rows = self.shot()
            self.near(rows[H - 3][2], (2, 3, 64))                  # bottom left: (0, 0)
            self.near(rows[2][W - 3], (253, 252, 64))              # top right: (1, 1)
            self.near(rows[2][2], (2, 252, 64))                    # top left: (0, 1)
            self.near(rows[H // 2][W // 2], (128, 128, 64))

    def test_time_runs_in_seconds_with_high_precision(self):
        """TIME is far above what a 16-bit float holds here (the offset is 100000 s): with medium precision the
        picture was black in CI. Its speed is checked against the wall clock, loosely (the runner may be busy)."""
        self.engine.upload("time.fs", PROBE_TIME)
        self.show("time.fs", offset=100000.0)
        a, t0 = self.shot()[H // 2][W // 2], time.monotonic()
        self.assertGreater(a[0], 247, "TIME is not between 100000 and 100600: %s" % (a,))
        time.sleep(2.0)
        b, t1 = self.shot()[H // 2][W // 2], time.monotonic()
        self.assertGreater(b[0], 247)
        moved = ((b[1] - a[1]) % 256) / 255.0 * 8.0                 # seconds of TIME, from the green channel (8 s a turn)
        self.assertTrue(0.5 * (t1 - t0) <= moved <= 1.5 * (t1 - t0), "TIME moved %.2f s in %.2f s" % (moved, t1 - t0))

    # -- the panel's opacity, fades and Blackout --
    def test_brightness_still_darkens_the_shader(self):
        """Opacity, the fades and Blackout are mpv's brightness. A shader at the MAIN stage ignored it (seen in CI);
        at the NATIVE stage over an RGB carrier it is applied after the shader."""
        self.engine.upload("probe.fs", PROBE)
        self.show("probe.fs")
        self.api.blackout({"on": True}, None, "t")
        time.sleep(0.3)
        rows = self.shot()
        self.assertEqual(max(max(rows[y][x]) for y in range(0, H, 9) for x in range(0, W, 9)), 0)
        self.api.blackout({"on": False}, None, "t")
        time.sleep(0.3)
        self.near(self.shot()[2][W - 3], (253, 252, 64))
        self.api.control({"action": "opacity", "value": 50}, None, "t")
        time.sleep(0.3)
        self.near(self.shot()[2][W - 3], (126, 125, 0), 10)        # brightness takes half away, as it does from a clip

    # -- a shader the GPU refuses --
    def test_a_refused_shader_is_reported_with_its_line_and_the_one_before_stays(self):
        self.engine.upload("probe.fs", PROBE)
        self.engine.upload("broken.fs", BROKEN)
        self.show("probe.fs")
        r = self.engine.show("broken.fs")
        self.assertEqual((r["ok"], r["showing"]), (False, "probe.fs"))
        self.assertIn("nonsense", r["error"])
        self.assertIn("line 6:", r["error"])                        # the line of the ISF file, not of mpv's own text
        time.sleep(0.3)
        self.near(self.shot()[2][W - 3], (253, 252, 64))           # the shader before it is on the screen
        self.assertEqual(self.engine.state()["playing"]["id"], "probe.fs")
        self.assertEqual(self.engine.state()["error"]["id"], "broken.fs")
        self.assertEqual(len(self.shaders_in_player()), 1)

    def test_a_refused_shader_with_nothing_before_it_ends_in_black_not_in_a_broken_picture(self):
        self.engine.upload("broken.fs", BROKEN)
        r = self.engine.show("broken.fs")
        self.assertEqual((r["ok"], r["showing"]), (False, None))
        time.sleep(0.3)
        rows = self.shot()                                           # the bare carrier
        self.assertEqual(max(max(rows[y][x]) for y in range(0, H, 9) for x in range(0, W, 9)), 0)
        with self.assertRaises(ApiError) as c:
            self.engine.api_play({"id": "broken.fs"}, None, "t")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("The screen is black", c.exception.message)
        self.assertEqual(self.shaders_in_player(), [])
        for _ in range(3):                                           # and every time again: mpv keeps quiet about a text it
            r = self.engine.show("broken.fs")                        # has refused before, so each try must be a new text
            self.assertEqual((r["ok"], "nonsense" in r["error"]), (False, True))
        self.engine.upload("probe.fs", PROBE)
        self.show("probe.fs")                                        # and a good one works straight after
        self.near(self.shot()[2][W - 3], (253, 252, 64))

    # -- together with the projection mapping --
    def test_a_mapping_stays_on_over_a_shader_source(self):
        self.engine.upload("probe.fs", PROBE)
        self.show("probe.fs")
        swap = os.path.join(self.rundir, "swap.glsl")
        with open(swap, "w") as f:
            f.write(SWAP)
        self.real.set_shaders([swap])                               # what the mapper calls; it knows nothing of the source
        time.sleep(0.4)
        self.assertEqual(self.shaders_in_player(), [os.path.basename(self.real.source_shader), "swap.glsl"])
        self.near(self.shot()[2][W - 3], (64, 252, 253))           # the shader's picture, with red and blue exchanged
        self.real.set_shaders([])
        # the real mapper: a quad in the middle of the screen shows the whole shader picture, the rest is black
        self.api.registry.set_enabled("mapper", True)
        self.api.mapper.handle({"action": "add", "type": "quad"})
        self.api.mapper.handle({"action": "on", "on": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.api.mapper.state()["status"]["state"] != "on":
            time.sleep(0.1)
        self.assertEqual(self.api.mapper.state()["status"]["state"], "on")
        time.sleep(0.4)
        names = self.shaders_in_player()
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("shader-") and names[1].startswith("mapper-"), names)
        rows = self.shot()
        self.assertEqual(rows[4][4], (0, 0, 0))                     # outside the surface
        self.near(rows[H // 2][W // 2], (128, 128, 64), 12)        # the middle of the shader, in the middle of the quad
        self.near(rows[H // 4 + 4][W // 4 + 4], (6, 245, 64), 20)  # the quad's top left corner shows the picture's
        self.engine.show("probe.fs", hue=0.0)                       # a new shader file: the mapping must still be there
        self.assertTrue(self.shaders_in_player()[1].startswith("mapper-"))
        self.real.play([CLIP], windowed=True)                       # a clip takes over: the source goes, the mapping stays
        names = self.shaders_in_player()
        self.assertTrue(len(names) == 1 and names[0].startswith("mapper-"), names)
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")
        self.api.mapper.handle({"action": "on", "on": False})
        self.assertEqual(self.shaders_in_player(), [])
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "auto")

    # -- handing the screen over --
    def clip_colours(self, wait):
        """How many colours the clip's test picture shows; polls up to `wait` seconds for more than 20."""
        deadline, seen = time.monotonic() + wait, []
        while True:
            seen.append(len({c for row in self.shot()[::9] for c in row[::9]}))
            if seen[-1] > 20 or time.monotonic() > deadline:
                return seen
            time.sleep(0.25)

    def test_playing_a_clip_takes_the_shader_off_before_the_clip_starts(self):
        self.engine.upload("probe.fs", PROBE)
        r = self.show("probe.fs")
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")
        self.assertEqual(self.api.status({}, None, "t")["player"]["shader"], "probe")
        self.real.play([CLIP], windowed=True)
        self.assertEqual(self.shaders_in_player(), [])
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "auto")
        self.assertIsNone(self.engine.state()["playing"])
        self.assertIsNone(self.engine.show("probe.fs", epoch=r["epoch"]))      # an old epoch can no longer take the screen
        self.assertEqual((self.shaders_in_player(), self.real.ipc.request("get_property", "path")), ([], CLIP))
        for _ in range(4):                                           # the probe's picture is gone from the screen
            rows = self.shot()
            probe = all(abs(a - b) <= 8 for got, want in ((rows[2][W - 3], (253, 252, 64)), (rows[H - 3][2], (2, 3, 64))) for a, b in zip(got, want))
            self.assertFalse(probe, "the shader is still drawn over the clip")
            time.sleep(0.2)
        # The clip's own picture is not checked: on this rig a screenshot of the test clip was at times one flat
        # colour even before any shader had been on (cause not found), so it proves nothing either way. Printed only.
        print("clip colours after the shader: %s, time-pos %s" % (self.clip_colours(2), self.real.ipc.request("get_property", "time-pos")))

    def test_vibes_changes_shaders_on_the_same_carrier(self):
        now = [0.0]
        self.settings.data["mix"] = {"transition": "dip", "duration": 0.4}
        v = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: now[0], rng=random.Random(2), thread=False, log=lambda *_: None)
        v.start()
        self.assertTrue(v.tick())
        first, carrier = v.current, self.real.ipc.request("get_property", "path")
        self.assertTrue(S.is_carrier(carrier))
        self.assertIs(self.engine.playing["checked"], True)
        time.sleep(0.5)
        before = self.real.ipc.request("get_property", "time-pos")
        now[0] += 180
        self.assertTrue(v.tick())
        self.assertNotEqual(v.current, first)
        self.assertIs(self.engine.playing["checked"], True)
        self.assertEqual(self.real.ipc.request("get_property", "path"), carrier)
        self.assertGreater(self.real.ipc.request("get_property", "time-pos"), before)       # the carrier was not restarted
        time.sleep(0.6)                                              # the fade back up
        self.assertEqual(self.real.ipc.request("get_property", "brightness"), 0)
        self.real.play([CLIP], windowed=True)
        self.assertFalse(v.tick())
        self.assertFalse(v.running)


@unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
class GlesShaderTest(GpuCase, ServerBase):
    ES = "yes"


@unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
class DesktopGlShaderTest(GpuCase, ServerBase):
    ES = "no"


if __name__ == "__main__":
    unittest.main()
