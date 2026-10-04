# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Performing with shaders on a real mpv that really draws (see test_shaders_gpu.py for the rig): every input type
changes the picture, TIME goes on across a change of a value and of the speed, a preset applies, the picture is never
black during a change, and this mpv has no way to take a new value without a new shader text.

Run:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m tests.test_shaderlive_gpu
A software GPU: the times printed here say what a change costs in CI, not on a real board.
"""
import os
import shutil
import sys
import threading
import time
import unittest

from pvj import shaders as S
from tests.test_server import ServerBase
from tests.test_shaders_gpu import GPU, GpuCase, H, W

TYPES = """/*{"INPUTS": [
 {"NAME": "level", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.25},
 {"NAME": "lit", "TYPE": "bool", "DEFAULT": false},
 {"NAME": "mode", "TYPE": "long", "VALUES": [0, 1, 2], "LABELS": ["none", "half", "all"], "DEFAULT": 0},
 {"NAME": "tint", "TYPE": "color", "DEFAULT": [0.0, 0.0, 0.0, 1.0]},
 {"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.25, 0.25], "MIN": [0.0, 0.0], "MAX": [1.0, 1.0]},
 {"NAME": "bang", "TYPE": "event"}]}*/
void main() {
    vec2 uv = isf_FragNormCoord;
    vec3 c = vec3(level, lit ? 1.0 : 0.0, float(mode) * 0.5);
    if (uv.x > 0.5) { c = tint.rgb; }
    if (distance(uv, spot) < 0.08) { c = vec3(1.0); }
    if (bang) { c = vec3(1.0, 0.0, 1.0); }
    gl_FragColor = vec4(c, 1.0);
}
"""
# TIME in two channels: red turns once in 8 seconds (fine), green once in 64 (coarse); blue shows the input.
CLOCK = """/*{"INPUTS": [{"NAME": "k", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.5}]}*/
void main() { gl_FragColor = vec4(fract(TIME / 8.0), fract(TIME / 64.0), k, 1.0); }
"""
BRIGHT = """/*{"INPUTS": [{"NAME": "k", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.5}]}*/
void main() { gl_FragColor = vec4(0.6 + 0.2 * k, 0.8, 0.7, 1.0); }
"""
PARAM = "//!PARAM pvj_level\n//!TYPE float\n0.5\n\n//!HOOK MAIN\n//!BIND HOOKED\n//!DESC param probe\nvec4 hook() { return vec4(pvj_level); }\n"


class LiveCase(GpuCase):
    def setUp(self):
        super().setUp()
        self.engine.changer._use_thread = False

    def apply(self, wait=3.0):
        """Let the worker's step run as soon as it is due; returns the seconds the change itself took."""
        end = time.monotonic() + wait
        while time.monotonic() < end:
            t0 = time.monotonic()
            if self.engine.changer.pump():
                took = time.monotonic() - t0
                time.sleep(0.25)                                     # a few frames with the new text
                return took
            time.sleep(0.02)
        self.fail("no change was due")

    def left(self):
        return self.shot()[H // 8][W // 8]

    def test_every_input_type_changes_the_picture(self):
        self.engine.upload("types.fs", TYPES)
        self.show("types.fs")
        row = next(s for s in self.engine.state()["shaders"] if s["id"] == "types.fs")
        self.assertEqual([(i["type"], i["value"]) for i in row["inputs"]],
                         [("float", 0.25), ("bool", False), ("long", 0), ("color", [0.0, 0.0, 0.0, 1.0]), ("point2D", [0.25, 0.25]), ("event", False)])
        self.near(self.left(), (64, 0, 0))
        costs = []
        for values, want in (({"level": 0.75}, (191, 0, 0)), ({"lit": True}, (191, 255, 0)), ({"mode": 2}, (191, 255, 255)), ({"mode": 1}, (191, 255, 128))):
            self.engine.change({"values": values})
            costs.append(self.apply())
            self.near(self.left(), want)
        self.assertIs(self.engine.playing["checked"], True, "a switch or a choice is a new shape of the text: the GPU was asked again")
        self.engine.change({"values": {"tint": [0.2, 0.4, 0.6]}})
        costs.append(self.apply())
        rows = self.shot()
        self.near(rows[H // 8][W - W // 8], (51, 102, 153))
        self.near(rows[H - H // 4][W // 4], (255, 255, 255))         # the dot at (0.25, 0.25), counted from the bottom left
        self.engine.change({"values": {"spot": [0.25, 0.75]}})
        costs.append(self.apply())
        rows = self.shot()
        self.near(rows[H // 4][W // 4], (255, 255, 255))
        self.near(rows[H - H // 4][W // 4], (191, 255, 128))
        # an event is true for a moment and lets go by itself
        self.engine.change({"values": {"bang": True}})
        end = time.monotonic() + 3
        while not self.engine.changer.pump() and time.monotonic() < end:
            time.sleep(0.01)
        time.sleep(0.12)
        self.near(self.left(), (255, 0, 255))
        self.apply()                                                 # the release, a quarter of a second later
        self.near(self.left(), (191, 255, 128))
        self.assertEqual(self.engine.state()["playing"]["values"]["lit"], True)
        self.assertIsNone(self.engine.error)
        print("a change of one value took %s ms (write, exchange, GPU check where the shape was new)" % ", ".join("%.0f" % (c * 1000) for c in costs))

    def time_now(self):
        """(TIME in seconds, modulo 64, as the picture shows it; the wall clock; blue)."""
        r, g, b = self.shot()[H // 2][W // 2]
        fine, coarse = r / 255.0 * 8.0, g / 255.0 * 64.0
        return (round((coarse - fine) / 8.0) * 8.0 + fine) % 64.0, time.monotonic(), b

    def test_time_goes_on_across_a_change_of_a_value_and_of_the_speed(self):
        self.engine.upload("clock.fs", CLOCK)
        self.show("clock.fs", offset=20.0)
        time.sleep(1.0)
        a, wall_a, blue = self.time_now()
        self.assertTrue(20.0 <= a <= 27.0, "TIME starts at the offset when the shader comes on: %.2f" % a)
        self.assertTrue(abs(blue - 128) <= 8)
        self.engine.change({"values": {"k": 1.0}})
        self.apply()
        b, wall_b, blue = self.time_now()
        self.assertTrue(blue >= 247, "the new value is on")
        self.assertTrue(abs((b - a) - (wall_b - wall_a)) < 0.6, "TIME went on through the change: %.2f s in %.2f s" % (b - a, wall_b - wall_a))
        # three times as fast: no jump at the change, then three seconds of TIME a second
        self.engine.change({"controls": {"speed": 3.0}})
        self.apply()
        c, wall_c, _ = self.time_now()
        gone = wall_c - wall_b
        self.assertTrue(gone * 0.5 - 0.3 <= c - b <= gone * 3.0 + 0.6, "no jump when the speed changed: %.2f s of TIME in %.2f s" % (c - b, gone))
        time.sleep(1.5)
        d, wall_d, _ = self.time_now()
        rate = (d - c) / (wall_d - wall_c)
        self.assertTrue(2.2 <= rate <= 3.8, "TIME runs at %.2f times the clock, 3 was asked for" % rate)
        # 0 freezes it where it is
        self.engine.change({"controls": {"speed": 0.0}})
        self.apply()
        e, wall_e, _ = self.time_now()
        self.assertTrue(-0.2 <= e - d <= (wall_e - wall_d) * 3.0 + 0.6, "no jump when it froze: %.2f" % (e - d))
        time.sleep(1.0)
        f, _, _ = self.time_now()
        self.assertTrue(abs(f - e) < 0.05, "frozen: %.3f then %.3f" % (e, f))
        self.engine.change({"controls": {"speed": 1.0}})
        self.apply()
        time.sleep(1.0)
        g, _, _ = self.time_now()
        self.assertTrue(0.3 <= g - f <= 2.5, "and it goes on from there: %.2f" % (g - f))

    def test_the_first_versions_clock_still_draws(self):
        """"clock": "frame" is the way back to counting mpv's own frames, should a GPU not read the carrier's colour."""
        self.engine.api_set({"action": "config", "clock": "frame"}, None, "t")
        self.engine.upload("clock.fs", CLOCK)
        self.show("clock.fs")
        self.assertNotIn("geq", self.real.ipc.request("get_property", "path"))
        a, wall_a, _ = self.time_now()
        time.sleep(1.5)
        b, wall_b, _ = self.time_now()
        self.assertTrue(0.5 * (wall_b - wall_a) <= (b - a) % 64.0 <= 1.5 * (wall_b - wall_a) + 0.2)

    def test_a_preset_brings_its_values_back(self):
        self.engine.upload("types.fs", TYPES)
        self.show("types.fs")
        self.engine.change({"values": {"level": 1.0, "lit": True, "mode": 2}})
        self.apply()
        self.near(self.left(), (255, 255, 255))
        self.engine.api_presets({"action": "save", "name": "white"}, None, "t")
        self.engine.change({"values": {"level": 0.0, "lit": False, "mode": 0}})
        self.apply()
        self.near(self.left(), (0, 0, 0))
        self.engine.apply_preset({"name": "white"})
        self.apply()
        self.near(self.left(), (255, 255, 255))
        self.assertEqual(self.engine.state()["playing"]["preset"], "white")
        self.engine.api_presets({"action": "save", "name": "default"}, None, "t")
        self.real.play(["av://lavfi:testsrc=size=160x90:rate=25"], windowed=True)
        self.engine.api_play({"id": "types.fs"}, None, "t")          # a plain Play uses the preset called default
        time.sleep(0.3)
        self.near(self.left(), (255, 255, 255))

    def test_the_picture_is_never_black_during_changes_and_they_are_coalesced(self):
        self.engine.upload("bright.fs", BRIGHT)
        self.show("bright.fs")
        self.engine.changer._use_thread = True
        drops = self.real.ipc.request("get_property", "frame-drop-count") or 0
        before = self.engine.changer.applied
        stop = threading.Event()

        def knob():                                                  # a knob turned for two seconds, 50 messages a second
            n = 0
            while not stop.is_set() and n < 100:
                self.engine.change({"values": {"k": (n % 50) / 49.0}})
                n += 1
                time.sleep(0.02)
            self.engine.change({"values": {"k": 1.0}})
        t = threading.Thread(target=knob)
        t.start()
        shots = 0
        try:
            while t.is_alive():
                r, g, b = self.shot()[H // 2][W // 2]
                self.assertTrue(r >= 140 and g >= 190, "a dark picture during a change: %s" % ((r, g, b),))
                shots += 1
        finally:
            stop.set()
            t.join()
        time.sleep(0.8)
        applied = self.engine.changer.applied - before
        self.assertGreater(shots, 3)
        self.assertTrue(2 <= applied <= 14, "100 values in two seconds were %d compiles" % applied)
        self.near(self.shot()[H // 2][W // 2], (204, 204, 178))     # the last value landed
        self.assertEqual(self.engine.state()["playing"]["values"]["k"], 1.0)
        self.assertEqual(len(self.shaders_in_player()), 1)
        print("100 values in 2 s: %d compiles, %d screenshots all lit, %d frames dropped meanwhile"
              % (applied, shots, (self.real.ipc.request("get_property", "frame-drop-count") or 0) - drops))

    def test_this_mpv_has_no_live_parameters_for_a_user_shader(self):
        """`//!PARAM` (and `glsl-shader-opts`, which sets one) belong to mpv's other output, gpu-next. With --vo=gpu a
        shader that declares one is refused, so a value can only change with a new shader text. If this test fails,
        mpv has learned it, and the coalescing worker can hand numbers over without a compile."""
        path = os.path.join(self.rundir, "param.glsl")
        with open(path, "w") as f:
            f.write(PARAM)
        tap = S.LogTap(self.real.socket_path)
        try:
            self.real.ipc.request("set_property", "glsl-shaders", [path])
            lines = tap.drain(2.0)
        finally:
            tap.close()
            self.real.ipc.request("set_property", "glsl-shaders", [])
        said = " ".join(t for _, _, t in lines)
        self.assertRegex(said, r"Unrecognized command|PARAM|undeclared|pvj_level")
        print("mpv %s on a //!PARAM line: %s" % (self.real.ipc.request("get_property", "mpv-version"), S.shader_errors(lines) or said[:200]))


ONLY = os.environ.get("PVJ_GPU_ONLY")          # "desktop": the run that makes Mesa a GL 3.1 driver has no use for GLES

if ONLY != "desktop":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesLiveTest(LiveCase, ServerBase):
        ES = "yes"


@unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
class DesktopGlLiveTest(LiveCase, ServerBase):
    ES = "no"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    ran = result.testsRun - len(result.skipped)
    print("live shader GPU tests: %d run, %d skipped" % (ran, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() and ran > 0 and not result.skipped else 1)
