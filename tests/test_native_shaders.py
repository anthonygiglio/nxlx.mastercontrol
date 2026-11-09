# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The New renderer's own shader programs (D106, phase 3 step a): generators and effects run by pvj-render itself,
with their inputs as uniforms set every frame, run for real (headless, Mesa's software GPU) like tests/test_pvj_render.

What is held here:
* the program file the panel writes (pvj/shaders.py and pvj/effects.py, translate_native) and what pvj-render's
  reader refuses;
* every bundled generator and effect drawn by pvj-render's own program against the same shader drawn by libmpv inside
  the same pvj-render (the path the New renderer had before), by the pixels, within the tolerance below;
* the level after the programs (the panel's brightness, a Blackout under an effect that adds light);
* values that move without a build, many times, with nothing growing; TIME that does not jump;
* a code that cannot take its inputs as uniforms, built with constants; refusals; the rate cap;
* the screenshots the panel takes (the crossfade's still, the preview), written by the renderer itself;
* the panel's engines (shaders.LiveEngine, effects.Effects) on the real program through the real Player.

The pixel tolerance, and why. Both paths compute the same code in the same precision on the same GPU; what differs is
where the 8-bit roundings fall (libmpv keeps 8-bit buffers between its passes and dithers its output, the renderer has
one 8-bit target) and the order of the float operations. Measured over all 47 generators and 37 effects at 320 x 180
on a smooth picture (2026-10-23): every generator within 2 levels everywhere (the 99th percentile 1), every effect
within 3 at the 99th percentile and on average under 0.5. The exception is an effect that is a step of its input (a
threshold, a quantizing, a dot's edge: isf-duotone, isf-posterize, isf-rgb-halftone): a pixel that sits on a step goes
one way or the other on the smallest difference, and isf-rgb-halftone, whose dots are all edge, had 1.9 percent of its
channels more than 3 apart. So: the 99th percentile at most 3, the mean at most 0.6 and at most 0.5 percent of the
channels more than 3 apart; for those three, the 99th percentile at most 8 and at most 3 percent apart.
"""
import glob
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import effects as E, paths, shaders as S
from pvj.player import Ipc, Player, PlayerError, is_program
from pvj.transitions import read_still
from tests.test_pvj_render import (BIN, BLACK, BLUE, GREEN, H, RED, STRICT, W, Renderer, SourcesBase, fd_count, need_program, png,
                                   thread_count)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GENERATORS = sorted(glob.glob(os.path.join(REPO, "pvj", "shaders.d", "*.fs")) + glob.glob(os.path.join(REPO, "pvj", "shaders.d", "*", "*.fs")))
EFFECTS = sorted(glob.glob(os.path.join(REPO, "pvj", "effects.d", "*.fs")) + glob.glob(os.path.join(REPO, "pvj", "effects.d", "*", "*.fs")))
P99, MEAN, OFF_SHARE = 3, 0.6, 0.005
STEPS = {"isf-duotone.fs", "isf-posterize.fs", "isf-rgb-halftone.fs"}     # effects that are a step of their input (above)

FLAT = "/*{\"INPUTS\": [{\"NAME\": \"level\", \"TYPE\": \"float\", \"DEFAULT\": 0.25},\n" \
       " {\"NAME\": \"lit\", \"TYPE\": \"bool\", \"DEFAULT\": true},\n" \
       " {\"NAME\": \"tint\", \"TYPE\": \"color\", \"DEFAULT\": [1.0, 0.5, 0.0, 1.0]}]}*/\n" \
       "void main() { gl_FragColor = vec4(level, lit ? 0.5 : 0.0, tint.b, 1.0); }\n"
# TIME as a colour: red is TIME / 100, so 1/255 of red is 0.39 s
CLOCK = "/*{}*/\nvoid main() { gl_FragColor = vec4(fract(TIME / 100.0), 0.0, 0.0, 1.0); }\n"
# an input in a constant expression: GLSL takes no uniform there, so this one is built with constants
CONST = "/*{\"INPUTS\": [{\"NAME\": \"level\", \"TYPE\": \"float\", \"DEFAULT\": 0.25}]}*/\n" \
        "const float k = level * 2.0;\nvoid main() { gl_FragColor = vec4(k, 0.0, 0.0, 1.0); }\n"
INVERT = "/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n" \
         "void main() { vec4 c = IMG_THIS_PIXEL(inputImage); gl_FragColor = vec4(1.0 - c.rgb, 1.0); }\n"


def channel_diff(a, b):
    d = sorted(abs(x - y) for x, y in zip(a[2], b[2]))
    n = len(d)
    return {"max": d[-1], "p99": d[int(n * 0.99)], "mean": sum(d) / n, "off": sum(1 for x in d if x > P99) / n}


def gradient(x, y):
    """A smooth picture with every channel moving (a clip's picture is smooth almost everywhere)."""
    return ((x * 255) // (W - 1), (y * 255) // (H - 1), ((W - 1 - x + y) * 255) // (W + H - 2))


class NativeBase(unittest.TestCase):
    ARGS = ()

    def setUp(self):
        need_program(self)
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(*self.ARGS)
        self.addCleanup(lambda: self.r.close())
        self.r("set_property", "image-display-duration", "inf")
        self.n = 0

    def write(self, text, ext=".prog"):
        self.n += 1
        path = os.path.join(self.tmp, "p%d%s" % (self.n, ext))
        with open(path, "w") as f:
            f.write(text)
        return path

    def generator(self, source, size=(W, H), **kw):
        return self.write(S.translate_native(S.parse(source, passes=True), size, **kw))

    def effect(self, source, **kw):
        return self.write(E.translate_native(S.parse(source, S.FILTER, passes=True), **kw))

    def slot(self, k):
        return self.r("get_property", "pvj-shaders")[k]

    def steady(self, before=None, seconds=8.0):
        """A capture once two in a row are the same (and differ from `before`, when given)."""
        last = self.r.capture()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.15)
            cur = self.r.capture()
            if cur[2] == last[2] and (before is None or cur[2] != before[2]):
                return cur
            last = cur
        return last

    def carrier(self):
        self.r("set_property", "fbo-format", "rgba8")          # as Player._apply_fbo sets it under a generator
        self.r("loadfile", S.carrier_url((W, H), False), "replace")
        self.r.wait(lambda: self.r.playing() is not None)

    def picture(self):
        pic = os.path.join(self.tmp, "gradient.png")
        png(pic, W, H, gradient)
        self.r.load(pic)
        return pic

    def assertNear(self, a, b, what):
        d = channel_diff(a, b)
        p99, off = (8, 0.03) if what in STEPS else (P99, OFF_SHARE)
        self.assertTrue(d["p99"] <= p99 and d["mean"] <= MEAN and d["off"] <= off, "%s: %s" % (what, d))
        return d


class ProgramFileTest(unittest.TestCase):
    """What the panel writes; no renderer needed."""

    def test_a_generator_file(self):
        text = S.translate_native(S.parse(FLAT), (320, 180), {"level": 0.75}, hue=10.0, offset=2.0, speed=0.5, gain=1.5)
        lines = text.split("\n")
        self.assertEqual(lines[:4], ["//!PVJ 1", "//!KIND generator", "//!SIZE 320 180", "//!LINE 3"])
        self.assertIn("//!INPUT float level 0.75", lines)
        self.assertIn("//!INPUT bool lit 1", lines)
        self.assertIn("//!INPUT color tint 1.0 0.5 0.0 1.0", lines)
        for c in ("//!CONTROL speed 0.5", "//!CONTROL hue 10.0", "//!CONTROL gain 1.5", "//!CONTROL offset 2.0"):
            self.assertIn(c, lines)
        code = text.split("//!CODE\n", 1)[1]
        self.assertIn("void pvj_main()", code)
        self.assertNotIn("//!", code)
        self.assertNotIn("const float level", text)            # a uniform in the renderer, never a constant here

    def test_an_effect_file(self):
        text = E.translate_native(S.parse(INVERT, S.FILTER), controls={"amount": 0.5, "speed": 2.0})
        self.assertTrue(text.startswith("//!PVJ 1\n//!KIND filter\n//!CLOCK 0\n"))
        self.assertIn("//!CONTROL amount 0.5", text)
        self.assertIn("pvj_img_this()", text)

    def test_values_by_the_name_in_the_code(self):
        parsed = S.parse(FLAT.replace("\"lit\"", "\"time\"").replace("lit ?", "time ?"))
        self.assertEqual(S.native_values(parsed, {"time": True, "level": 1}), {"pvj_in_time": True, "level": 1.0})
        sent = S.native_send(parsed, {"level": 0.5}, {})
        self.assertEqual(sent, {"level": 0.5, "pvj_in_time": True, "tint": [1.0, 0.5, 0.0, 1.0]})

    def test_a_program_is_told_from_an_mpv_shader(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        with open(a, "w") as f:
            f.write(S.BLACK_NATIVE)
        with open(b, "w") as f:
            f.write(S.BLACK)
        self.assertTrue(is_program(a) and S.is_program(a))
        self.assertFalse(is_program(b) or is_program(os.path.join(d, "none")) or is_program(None))


class ParityTest(NativeBase):
    """Every bundled shader, drawn by pvj-render's own program and by libmpv in the same process (D106's parity)."""

    def mpv_shot(self, text):
        self.r("set_property", "glsl-shaders", [])
        base = self.steady()
        self.r("set_property", "glsl-shaders", [self.write(text, ".glsl")])     # a new file each time: mpv keeps compiled files by name
        shot = self.steady(base)
        self.r("set_property", "glsl-shaders", [])
        return shot

    def test_every_generator(self):
        self.carrier()
        worst = []
        for f in GENERATORS:
            with self.subTest(shader=os.path.basename(f)):
                parsed = S.parse(open(f, encoding="utf-8").read())
                self.r("pvj-shader", "clear", 1, 0)
                a = self.mpv_shot(S.translate(parsed, (W, H), speed=0.0, offset=2.5, desc="nxlx shader %d" % self.n))
                self.r("pvj-shader", "load", 1, 0, self.write(S.translate_native(parsed, (W, H), speed=0.0, offset=2.5)))
                b = self.steady()
                self.assertEqual(self.slot(0)["state"], "on")
                worst.append((self.assertNear(a, b, os.path.basename(f))["p99"], os.path.basename(f)))
        self.assertEqual(len(worst), len(GENERATORS))
        print("\n  generators, native against libmpv: worst p99 %s" % (max(worst),))

    def test_every_effect(self):
        self.picture()
        worst = []
        for f in EFFECTS:
            with self.subTest(effect=os.path.basename(f)):
                parsed = S.parse(open(f, encoding="utf-8").read(), S.FILTER)
                self.r("pvj-shader", "clear", 1, 1)
                text = E.translate(parsed, controls={"speed": 0.0}, picture={"matrix": "rgb", "levels": "full", "fps": 30},
                                   desc="nxlx effect %d" % self.n)
                self.r("set_property", "glsl-shaders", [self.write(text, ".glsl")])
                # an effect at its defaults may leave the picture as it is (a gamma of 1): wait for the build, then for
                # two captures alike
                time.sleep(0.5)
                a = self.steady()
                self.r("set_property", "glsl-shaders", [])
                self.r("pvj-shader", "load", 1, 1, self.write(E.translate_native(parsed, controls={"speed": 0.0})))
                b = self.steady()
                self.assertEqual(self.slot(1)["state"], "on")
                worst.append((self.assertNear(a, b, os.path.basename(f))["p99"], os.path.basename(f)))
        self.assertEqual(len(worst), len(EFFECTS))
        print("\n  effects, native against libmpv: worst p99 %s" % (max(worst),))


class LevelTest(NativeBase):
    """The panel's level (brightness) after the programs (D106)."""

    def test_the_level_is_mpvs_within_a_level(self):
        self.picture()
        prog = self.effect(INVERT, controls={"amount": 0.0})         # on, and drawing nothing: only the level moves
        for b in (-100, -60, -25, -5, 0, 5, 25, 60, 100):
            with self.subTest(brightness=b):
                self.r("pvj-shader", "clear", 1, 1)
                self.r("set_property", "brightness", b)
                time.sleep(0.4)                         # libmpv redraws a still picture for a new level a moment later
                a = self.steady()
                self.r("pvj-shader", "load", 1, 1, prog)
                time.sleep(0.4)
                n = self.steady()
                self.assertLessEqual(channel_diff(a, n)["max"], 1, b)
                self.assertEqual(self.r("get_property", "brightness"), b)     # what the panel reads back

    def test_a_blackout_holds_under_an_effect_that_adds_light(self):
        self.picture()
        self.r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        self.r("set_property", "brightness", -100)
        shot = self.steady()
        self.assertEqual(max(shot[2]), 0, "an inverting effect undid the Blackout")
        self.r("set_property", "brightness", 0)
        self.r.wait(lambda: self.r.pixel(5, 5) != (0, 0, 0), 5)
        want = tuple(255 - c for c in gradient(5, 5))
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(self.r.pixel(5, 5), want)), (self.r.pixel(5, 5), want))   # inverted
        # off again: the layers have the level back, as before
        self.r("set_property", "brightness", -100)
        self.r("pvj-shader", "clear", 1, 1)
        self.assertEqual(max(self.steady()[2]), 0)
        self.assertEqual(self.r("get_property", "brightness"), -100)


class MoveEffectTest(NativeBase):
    """D74 and D111: an effect stays on through a wipe, over both sides of the edge, and a Blackout holds under it."""

    def test_an_effect_stays_on_over_both_pictures(self):
        a, b = os.path.join(self.tmp, "a.png"), os.path.join(self.tmp, "b.png")
        png(a, W, H, lambda x, y: RED)
        png(b, W, H, lambda x, y: BLUE)
        self.r.load(a)
        self.r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        self.assertTrue(self.r.wait(lambda: self.r.pixel(5, 5)[1] > 200, 5), self.r.pixel(5, 5))
        self.r("pvj-crossfade", b, 6.0, "keep-open=no,image-display-duration=inf", "wipe-from-left")
        look = lambda: self.r("get_property", "pvj-render")
        self.assertTrue(self.r.wait(lambda: look()["fade"] == "running" and 0.2 < look()["fade-progress"] < 0.7, 10, 0.02), look())
        shot = self.r.capture()
        self.assertTrue(all(abs(p - q) <= 40 for p, q in zip(self.r.pixel(4, H // 2, shot), (255, 255, 0))), "new, inverted: %s" % (self.r.pixel(4, H // 2, shot),))
        self.assertTrue(all(abs(p - q) <= 40 for p, q in zip(self.r.pixel(W - 4, H // 2, shot), (0, 255, 255))), "old, inverted: %s" % (self.r.pixel(W - 4, H // 2, shot),))
        self.r("set_property", "brightness", -100)
        self.assertEqual(max(self.steady()[2]), 0, "an inverting effect undid the Blackout during the wipe")
        self.assertEqual(look()["fade"], "running")
        self.r("set_property", "brightness", 0)


class ValuesTest(NativeBase):
    def test_values_move_without_a_build_and_nothing_grows(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        self.r.wait(lambda: self.r.pixel(10, 10)[0] in range(60, 70), 5)
        self.assertEqual(self.r.pixel(10, 10)[:2], (64, 128))
        for v in (0.0, 1.0, 0.5):
            self.r("pvj-shader", "set", 1, 0, {"level": v, "lit": v > 0.6})
            want = (round(v * 255), 128 if v > 0.6 else 0)
            self.assertTrue(self.r.wait(lambda: abs(self.r.pixel(10, 10)[0] - want[0]) <= 1 and self.r.pixel(10, 10)[1] in (want[1], want[1] - 1, want[1] + 1), 3),
                            (v, self.r.pixel(10, 10)))
        self.r("pvj-shader", "set", 1, 0, {"tint": [0.0, 0.0, 1.0, 1.0]})
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[2] >= 254, 3))
        start = (fd_count(self.r.pid), thread_count(self.r.pid), self.r("get_property", "pvj-render")["fx-gl-objects"],
                 self.r("get_property", "pvj-render")["gl-objects"])
        runs = self.slot(0)["runs"]
        for i in range(2000):
            self.r("pvj-shader", "set", 1, 0, {"level": (i % 256) / 255.0, "@hue": float(i % 360)})
        self.r("pvj-shader", "set", 1, 0, {"level": 0.2, "@hue": 0.0})
        self.assertTrue(self.r.wait(lambda: abs(self.r.pixel(10, 10)[0] - 51) <= 1, 3))
        s = self.slot(0)
        self.assertEqual((s["state"], s["rebuilds"]), ("on", 0))
        self.assertGreater(s["runs"], runs)
        after = (fd_count(self.r.pid), thread_count(self.r.pid), self.r("get_property", "pvj-render")["fx-gl-objects"],
                 self.r("get_property", "pvj-render")["gl-objects"])
        self.assertEqual((start[0], start[2]), (after[0], after[2]), (start, after))      # descriptors, the programs' GL objects
        self.assertLessEqual(abs(start[1] - after[1]), 2, (start, after))               # libmpv's threads and GL objects: a pool
        self.assertLessEqual(abs(start[3] - after[3]), 2, (start, after))

    def test_time_never_jumps_and_speed_zero_stands(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(CLOCK, offset=10.0))
        t0 = self.slot(0)["time"]
        self.assertGreaterEqual(t0, 10.0)
        last = t0
        for speed in (2.0, 0.5, 4.0, 1.0):
            self.r("pvj-shader", "set", 1, 0, {"@speed": speed})
            for _ in range(5):
                time.sleep(0.1)
                t = self.slot(0)["time"]
                self.assertGreaterEqual(t, last)
                self.assertLess(t - last, 0.1 * 4 * 3 + 0.5, (speed, last, t))     # never more than the pace allows
                last = t
        self.r("pvj-shader", "set", 1, 0, {"@speed": 0.0})
        time.sleep(0.2)
        a = self.slot(0)["time"]
        time.sleep(0.5)
        self.assertEqual(self.slot(0)["time"], a)
        self.r("pvj-shader", "set", 1, 0, {"@offset": 50.0})
        self.assertTrue(self.r.wait(lambda: self.slot(0)["time"] == 50.0, 3))
        self.assertTrue(self.r.wait(lambda: abs(self.r.pixel(5, 5)[0] - 128) <= 1, 3), self.r.pixel(5, 5))

    def test_an_effects_time_stands_while_its_clip_is_paused(self):
        self.picture()
        moving = INVERT.replace("vec4(1.0 - c.rgb, 1.0)", "vec4(fract(TIME / 100.0), c.g, 0.0, 1.0)")
        self.r("pvj-shader", "load", 1, 1, self.effect(moving))
        self.assertTrue(self.r.wait(lambda: self.slot(1)["time"] > 0.2, 3))
        self.r("set_property", "pause", True)
        time.sleep(0.3)
        a = self.slot(1)["time"]
        time.sleep(1.5)
        self.assertEqual(self.slot(1)["time"], a)
        self.r("set_property", "pause", False)
        # it goes on from where it stood: not from where the clock would have taken it meanwhile
        self.assertTrue(self.r.wait(lambda: self.slot(1)["time"] > a, 3))
        self.assertLess(self.slot(1)["time"], a + 0.6)

    def test_constants_when_the_code_needs_them(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(CONST))
        self.assertEqual(self.slot(0)["state"], "constants")
        self.assertTrue(self.r.wait(lambda: abs(self.r.pixel(5, 5)[0] - 128) <= 1, 3))
        self.r("pvj-shader", "set", 1, 0, {"level": 0.1})
        self.assertTrue(self.r.wait(lambda: abs(self.r.pixel(5, 5)[0] - 51) <= 1, 3), self.r.pixel(5, 5))
        self.assertGreaterEqual(self.slot(0)["rebuilds"], 1)

    def test_a_generator_runs_at_its_rate_and_an_effect_only_on_a_change(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(CLOCK))
        a, t = self.slot(0)["runs"], time.monotonic()
        time.sleep(2.0)
        rate = (self.slot(0)["runs"] - a) / (time.monotonic() - t)
        self.assertLess(rate, 33, rate)                 # 30 a second at most (--fx-fps), on a 60 Hz clock
        self.assertGreater(rate, 15, rate)
        self.r("pvj-shader", "clear", 1, 0)
        self.picture()
        self.r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        time.sleep(0.5)
        a = self.slot(1)["runs"]
        time.sleep(1.0)
        self.assertLessEqual(self.slot(1)["runs"] - a, 1, "an effect without a clock over a still picture ran again")
        self.r("pvj-shader", "set", 1, 1, {"@amount": 0.5})
        self.assertTrue(self.r.wait(lambda: self.slot(1)["runs"] > a, 2))


class ManyLoadsTest(NativeBase):
    def test_loads_and_clears_leave_nothing_behind(self):
        self.picture()
        gen, eff = self.generator(FLAT), self.effect(INVERT)
        counts = []
        for i in range(260):
            self.r("pvj-shader", "load", 1, i % 2, gen if i % 2 == 0 else eff)
            if i % 3 == 0:
                self.r("pvj-shader", "set", 1, i % 2, {"level": 0.5} if i % 2 == 0 else {"@amount": 0.5})
            if i % 4 == 3:
                self.r("pvj-shader", "clear", 1, 0)
                self.r("pvj-shader", "clear", 1, 1)
            if i in (59, 119, 179, 259):
                self.r("pvj-shader", "clear", 1, 0)
                self.r("pvj-shader", "clear", 1, 1)
                time.sleep(0.3)
                st = self.r("get_property", "pvj-render")
                counts.append((fd_count(self.r.pid), thread_count(self.r.pid), st["fx-gl-objects"], st["gl-objects"]))
        self.assertEqual(counts[-1][2], 0, counts)                  # every program, texture and framebuffer given back
        for c in counts[1:]:
            self.assertEqual((c[0], c[2]), (counts[0][0], counts[0][2]), counts)
            # libmpv's own threads and GL objects, untouched by this: a pool that moves by one or two (as D98 found)
            self.assertLessEqual(abs(c[1] - counts[0][1]), 2, counts)
            self.assertLessEqual(abs(c[3] - counts[0][3]), 2, counts)


class ClassicTest(unittest.TestCase):
    """Classic mpv (D105): the shader list it is sent is what it was, and it is never sent a program or a pvj-shader."""

    class Recorder:
        def __init__(self):
            self.sent = []

        def request(self, *command):
            self.sent.append(command)
            if command[:2] == ("get_property", "pid"):
                return 4242
            if command[:2] == ("get_property", "pvj-render"):
                raise PlayerError("mpv: property not found")
            if command[:2] == ("get_property", "path"):
                return "av://carrier"
            return None

    def test_classic_gets_the_list_it_always_got(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = Player(rundir=d)
        p.ipc = self.Recorder()
        src, fx, mapping = (os.path.join(d, n) for n in ("src.glsl", "fx.glsl", "map.glsl"))
        for path, text in ((src, S.BLACK), (fx, S.BLACK), (mapping, S.BLACK)):
            with open(path, "w") as f:
                f.write(text)
        p._source, p._source_pid, p._carrier = src, 4242, "av://carrier"
        p._effect, p._effect_pid = fx, 4242
        p._seen_pid = 4242
        p.set_shaders([mapping])
        lists = [c[2] for c in p.ipc.sent if c[:2] == ("set_property", "glsl-shaders")]
        self.assertEqual(lists, [[src, fx, mapping]])
        self.assertFalse(p.native_shaders())
        self.assertFalse(any(c[0] == "pvj-shader" for c in p.ipc.sent))
        # a program file that reached a classic player (a fallback in between) is never put in its list
        prog = os.path.join(d, "prog")
        with open(prog, "w") as f:
            f.write(S.BLACK_NATIVE)
        p._source = prog
        p.set_shaders([mapping])
        self.assertEqual([c[2] for c in p.ipc.sent if c[:2] == ("set_property", "glsl-shaders")][-1], [fx, mapping])
        self.assertFalse(any(c[0] == "pvj-shader" for c in p.ipc.sent))


class RefusalTest(NativeBase):
    def test_what_the_reader_and_the_socket_refuse(self):
        good = S.translate_native(S.parse(FLAT), (W, H))
        link = os.path.join(self.tmp, "link.prog")
        os.symlink(self.write(good), link)
        bad = {
            "no head": good.replace("//!PVJ 1", "//!PVJ 2"),
            "two kinds": good.replace("//!KIND generator", "//!KIND sometimes"),
            "a name": good.replace("//!INPUT float level", "//!INPUT float gl_level"),
            "a wrapper's name": good.replace("//!INPUT float level", "//!INPUT float TIME"),
            "a value": good.replace("//!INPUT float level 0.25", "//!INPUT float level nan"),
            "too many numbers": good.replace("//!INPUT float level 0.25", "//!INPUT float level 0.25 1"),
            "a directive in the code": good + "//!HOOK MAIN\n",
            "a backslash": good + "#define A \\\n",
            "no size": good.replace("//!SIZE 320 180\n", ""),
            "a size too large": good.replace("//!SIZE 320 180", "//!SIZE 9000 180"),
            "an unknown directive": good.replace("//!LINE", "//!PASS 1\n//!LINE"),
            "an empty LINE": good.replace("//!LINE 3", "//!LINE "),
            "a GLSL word as a name": good.replace("//!INPUT float level", "//!INPUT float sampler2D"),
            "another GLSL word": good.replace("//!INPUT float level", "//!INPUT float discard"),
        }
        for what, text in bad.items():
            with self.subTest(what):
                with self.assertRaises(PlayerError):
                    self.r("pvj-shader", "load", 1, 0, self.write(text))
        big = self.write(good + "//" + "x" * (70 * 1024) + "\n")
        for what, cmd in {"a link": ["load", 1, 0, link], "too large": ["load", 1, 0, big],
                          "an effect in the generator's slot": ["load", 1, 0, self.effect(INVERT)],
                          "a generator in the effect's slot": ["load", 1, 1, self.write(good)],
                          "source 2": ["load", 2, 0, self.write(good)], "slot 2": ["load", 1, 2, self.write(good)],
                          "a relative path": ["load", 1, 0, "p1.prog"], "a word": ["reload", 1, 0, "/x"]}.items():
            with self.subTest(what):
                with self.assertRaises(PlayerError):
                    self.r("pvj-shader", *cmd)
        self.assertEqual(self.slot(0)["state"], "off")
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.write(good))
        for what, values in {"an unknown name": {"nothing": 1.0}, "a word": {"level": "high"}, "a list for a float": {"level": [1.0]},
                             "two for a colour": {"tint": [1.0, 0.0]}, "2 for a bool": {"lit": 2}, "a control": {"@volume": 1.0}, "a whole number too large": {"level": 10 ** 12},
                             "half of it good": {"level": 0.5, "nothing": 1.0}}.items():
            with self.subTest(what):
                with self.assertRaises(PlayerError):
                    self.r("pvj-shader", "set", 1, 0, values)
        time.sleep(0.3)
        self.assertEqual(self.r.pixel(10, 10)[0], 64)            # all or nothing: "half of it good" moved nothing

    def test_a_refused_program_leaves_the_one_before_on(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        self.r.wait(lambda: self.r.pixel(10, 10)[0] == 64, 3)
        broken = S.translate_native(S.parse(FLAT), (W, H)).replace("vec4(level", "vec4(nonsense")
        with self.assertRaises(PlayerError) as e:
            self.r("pvj-shader", "load", 1, 0, self.write(broken))
        self.assertIn("nonsense", str(e.exception))
        self.assertIn("GPU refused", str(e.exception))
        time.sleep(0.3)
        self.assertEqual(self.slot(0)["state"], "on")
        self.assertEqual(self.r.pixel(10, 10)[0], 64)


class StillTest(NativeBase):
    """The panel's screenshots while a program is on: the renderer's own picture, not libmpv's carrier."""

    def test_the_crossfades_still_and_the_preview(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        self.r.wait(lambda: self.r.pixel(10, 10)[0] == 64, 3)
        path = os.path.join(self.tmp, "still.png")
        open(path, "w").close()                         # the panel makes the file; the player writes over it
        self.r("screenshot-to-file", path, "window")
        w, h, px = read_still(path, (W, H))             # the crossfade's own reader (pvj/transitions.py)
        self.assertEqual((w, h), (W, H))
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(px[0:3], (0, 128, 64))), tuple(px[0:3]))   # BGRA of FLAT's (64, 128, 0)
        jpg = os.path.join(self.tmp, "preview.jpg")
        self.r("screenshot-to-file", jpg, "window")
        with open(jpg, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and no ffmpeg to read the JPEG back")
            self.skipTest("no ffmpeg to read the JPEG back")
        raw = subprocess.run([ffmpeg, "-v", "error", "-i", jpg, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        self.assertEqual(len(raw), W * H * 3)
        mean = [sum(raw[c::3]) / (W * H) for c in range(3)]
        for got, want in zip(mean, (64, 128, 0)):
            self.assertLess(abs(got - want), 3, mean)
        self.r("pvj-shader", "clear", 1, 0)
        self.r("screenshot-to-file", path, "window")    # with nothing on, libmpv's own screenshot as before
        self.assertTrue(os.path.getsize(path) > 0)
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        with self.assertRaises(PlayerError):            # a file ending the renderer does not write
            self.r("screenshot-to-file", os.path.join(self.tmp, "still.bmp"), "window")


class EngineTest(NativeBase):
    """The panel's own engines on the real program, through the real Player (pvj/shaders.py, pvj/effects.py)."""

    def setUp(self):
        need_program(self)
        from tests.test_server import ServerBase
        self.server = ServerBase("run")
        self.server.setUp()
        self.addCleanup(self.server.doCleanups)
        rundir = self.server.rundir
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer()
        self.addCleanup(lambda: self.r.close())
        os.symlink(self.r.sock, os.path.join(rundir, paths.PLAYER_SOCKET))
        self.n = 0
        self.api = self.server.api
        self.player = Player(rundir=rundir)
        self.api.player = self.player
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = self.api.effects.log = lambda *_: None
        self.player.ipc.request("set_property", "image-display-duration", "inf")

    def test_a_generator_and_its_values_and_an_effect_over_it(self):
        self.assertTrue(self.player.native_shaders())
        out = self.api.shaders.play("nxlx-silk.fs")
        self.assertTrue(out.get("ok"), out)
        p = self.api.shaders.playing
        self.assertTrue(p["native"] and is_program(p["path"]) and p["anchor"] is None)
        self.assertEqual(self.player.ipc.request("get_property", "glsl-shaders"), [])     # nothing in libmpv's list
        self.assertEqual(self.slot(0)["state"], "on")
        self.assertEqual(self.player.native_verdict(p["path"]), ("ok", ""))
        before = self.steady()
        runs = self.slot(0)["runs"]
        for v in (0.1, 0.9, 0.3):
            self.api.shaders.change({"values": {"sheen": v}, "controls": {"hue": 120.0}})
        self.assertTrue(self.r.wait(lambda: not self.api.shaders.changer.pending(), 3))
        time.sleep(0.3)
        self.assertEqual(self.api.shaders.playing["path"], p["path"])      # no new text
        self.assertEqual(self.slot(0)["rebuilds"], 0)
        self.assertGreater(self.slot(0)["runs"], runs)
        self.assertNotEqual(self.r.capture()[2], before[2])
        # an effect over it, run by the renderer too, and its amount moved without a build
        put = self.api.effects.put("fx-grade.fs")
        self.assertTrue(put["ok"], put)
        on = self.api.effects.on
        self.assertTrue(on["native"])
        self.assertEqual(self.slot(1)["state"], "on")
        self.api.effects.change({"controls": {"amount": 0.0}})
        self.assertTrue(self.r.wait(lambda: self.api.effects.on["controls"]["amount"] == 0.0, 3))
        self.assertEqual(self.api.effects.on["path"], on["path"])         # no new text
        self.assertEqual(self.slot(1)["rebuilds"], 0)
        # Stop takes both off, in the renderer too
        self.player.clear()
        self.assertTrue(self.r.wait(lambda: self.slot(0)["state"] == "off" and self.slot(1)["state"] == "off", 3))

    def test_a_shader_of_several_passes_is_taken_and_played_on_the_new_renderer(self):
        # an upload with PASSES: refused on classic, taken here (the library parses with passes on the New renderer)
        self.api.shaders.upload("feedback.fs", FEEDBACK)
        rows = {r["id"]: r for r in self.api.shaders.library()}
        self.assertFalse(rows["feedback.fs"].get("error"), rows["feedback.fs"])
        out = self.api.shaders.play("feedback.fs")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.slot(0)["state"], "on")
        a = self.r.pixel(10, 10)[0]
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] > a + 20, 3))
        self.api.effects.upload("twice.fs", INVERT_TWICE)
        put = self.api.effects.put("twice.fs")
        self.assertTrue(put["ok"], put)

    def test_classic_lists_a_shader_of_several_passes_as_refused(self):
        self.api.shaders.upload("feedback2.fs", FEEDBACK)
        self.player._native_seen = (self.player._seen_pid, False)        # the player is classic now
        rows = {r["id"]: r for r in self.api.shaders.library()}
        self.assertIn("New renderer", rows["feedback2.fs"]["error"])

    def test_a_refused_generator_leaves_black_never_the_carrier(self):
        broken = "/*{}*/\nvoid main() { gl_FragColor = vec4(nonsense, 1.0); }\n"
        self.api.shaders.upload("broken.fs", broken)
        from pvj.api import ApiError
        with self.assertRaises(ApiError) as e:
            self.api.shaders.play("broken.fs")
        self.assertEqual(e.exception.status, 422)
        self.assertIn("nonsense", e.exception.message)          # the GPU's own words, from the renderer's answer
        self.assertIn("The screen is black", e.exception.message)
        # nothing to go back to: the engine stops the screen (D73), and the renderer's slot is cleared with it
        self.assertTrue(self.r.wait(lambda: self.slot(0)["state"] == "off", 3))
        self.assertEqual(max(self.steady()[2]), 0)


class ShotPaceTest(NativeBase):
    """The review of #167 (High): a screenshot is encoded on the client's thread, never on the render thread, so the
    screen does not stall while the panel's preview or a crossfade's still is taken."""
    ARGS = ("--headless=1920x1080",)

    def flips(self):
        # headless, the renderer is paced by a clock and counts no missed vblanks (out.c counts them from the
        # screen's own sequence): a stall shows as flips that did not happen
        return self.r("get_property", "pvj-render")["flips"]

    def test_screenshots_do_not_stall_the_screen(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT, size=(1920, 1080)))
        time.sleep(1.0)
        # The quiet and the busy rate are taken in turn, five times 1.5 s each, and added up. One quiet stretch and
        # one busy stretch after it (3 s each) let a runner that got busier or slower between the two read as a
        # stall (CI, 2026-11-02: 37.6 quiet, 26.0 during, which is 0.69 of it, on a loaded machine). Taken in turn,
        # both rates see the same load; a real stall still takes the busy ones down to a quarter of the quiet.
        quiet_flips = quiet_secs = busy_flips = busy_secs = 0.0
        n = 0
        for _ in range(5):
            a, t = self.flips(), time.monotonic()
            time.sleep(1.5)
            quiet_flips += self.flips() - a
            quiet_secs += time.monotonic() - t
            a, t = self.flips(), time.monotonic()
            while time.monotonic() - t < 1.5:
                for ext in ("jpg", "png"):
                    path = os.path.join(self.tmp, "shot.%s" % ext)
                    open(path, "w").close()
                    self.r("screenshot-to-file", path, "window")
                    n += 1
            busy_flips += self.flips() - a
            busy_secs += time.monotonic() - t
        quiet, busy = quiet_flips / quiet_secs, busy_flips / busy_secs
        print("\n  flips a second at 1920 x 1080: %.1f quiet, %.1f during %d screenshots" % (quiet, busy, n))
        self.assertGreater(n, 4)
        # Encoding a 1080p JPEG took 139 to 273 ms on this kind of machine: on the render thread that was 8 to 16
        # vblanks without a picture each, and the rate fell to a quarter of the quiet one (15.4 of 60 here). With
        # only the readback there it stays near the quiet rate. Judged against the quiet rate of the same run, since
        # a CI runner's software GPU draws 1080p at about 30 a second, not 60.
        # The bound was 0.7 of the quiet rate, taken from one run. Measured over 30 runs on a loaded machine with the
        # fix in place (so with no stall at all) the busy rate was 0.64 to 0.97 of the quiet one, median 0.88: the
        # screenshots' own work takes CPU from a software GPU that already uses all of it, and the load of the
        # machine moves while the test runs. A run at 0.69 failed in CI with nothing wrong. The stall it guards
        # against gives 0.26, so the bound is 0.45: above that, and well below anything a healthy render gives.
        self.assertGreater(busy, 0.45 * quiet, (quiet, busy, n))

    def test_two_screenshots_at_once_each_get_their_picture(self):
        import threading
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT, size=(320, 180)))
        paths = [os.path.join(self.tmp, "a.png"), os.path.join(self.tmp, "b.jpg")]
        errors = []

        def take(path):
            try:
                ipc = __import__("pvj.player", fromlist=["Ipc"]).Ipc(self.r.sock, timeout=10.0)
                for _ in range(5):
                    open(path, "w").close()
                    ipc.request("screenshot-to-file", path, "window")
                    with open(path, "rb") as fh:
                        head = fh.read(8)
                    if head[:2] not in (b"\x89P", b"\xff\xd8"):
                        errors.append((path, head))
            except Exception as e:
                errors.append((path, e))
        threads = [threading.Thread(target=take, args=(p,)) for p in paths]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        self.assertEqual(errors, [])

    def test_a_screenshot_into_a_fifo_is_refused_at_once(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        fifo = os.path.join(self.tmp, "pipe.png")
        os.mkfifo(fifo)
        t = time.monotonic()
        with self.assertRaises(PlayerError):
            self.r("screenshot-to-file", fifo, "window")
        self.assertLess(time.monotonic() - t, 3.0)


class SlowBuildTest(NativeBase):
    """The review of #167 (Medium): a build slower than the panel's usual wait is never taken for a refusal, and a
    load that had no word is found again by its path, so values reach the program the renderer shows."""

    def setUp(self):
        need_program(self)
        if "with test hooks" not in subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without its test hooks: make -C render TEST_HOOKS=1")
            self.skipTest("pvj-render was built without its test hooks")
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.n = 0

    def player_for(self, ms):
        self.r = Renderer(env={"PVJ_RENDER_TEST_SLOW_BUILD": str(ms)})
        self.addCleanup(lambda: self.r.close())
        self.r("set_property", "image-display-duration", "inf")
        d = tempfile.mkdtemp(prefix="pvj-native-run-")
        self.addCleanup(shutil.rmtree, d, True)
        os.symlink(self.r.sock, os.path.join(d, paths.PLAYER_SOCKET))
        return Player(rundir=d)

    def test_a_build_slower_than_two_seconds_is_taken(self):
        p = self.player_for(2500)                       # longer than the panel's usual 2 s, shorter than the renderer's 3 s
        prog = self.generator(FLAT)
        epoch = p.play_source(prog, S.carrier_url((W, H), False))
        self.assertEqual(p.native_verdict(prog), ("ok", ""))
        self.assertTrue(p.native_values(0, {"level": 1.0}, epoch=epoch))
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] >= 254, 3))

    def test_a_build_slower_than_the_renderers_wait_is_no_refusal(self):
        p = self.player_for(4000)
        prog = self.generator(FLAT)
        epoch = p.play_source(prog, S.carrier_url((W, H), False))
        verdict = p.native_verdict(prog)
        self.assertEqual(verdict[0], "unknown", verdict)            # never "refused": the GPU said nothing
        self.assertIsNone(p._native_loaded[0])
        self.assertTrue(self.r.wait(lambda: self.slot(0)["path"] == prog, 5))     # the renderer shows it after all
        t = time.monotonic()
        self.assertTrue(p.native_values(0, {"level": 1.0}, epoch=epoch))
        self.assertLess(time.monotonic() - t, 1.5, "the program was built again instead of found by its path")
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] >= 254, 3))


class CapTest(NativeBase):
    """The review of #167 (Medium): the 30-a-second cap holds while values move."""

    def rate(self, slot, send):
        a, t, i = self.slot(slot)["runs"], time.monotonic(), 0
        while time.monotonic() - t < 2.0:
            send(i)
            i += 1
            time.sleep(0.02)                            # 50 sets a second
        return (self.slot(slot)["runs"] - a) / (time.monotonic() - t), i / (time.monotonic() - t)

    def test_a_generator_and_an_effect_hold_the_cap_while_values_move(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLAT))
        runs, sets = self.rate(0, lambda i: self.r("pvj-shader", "set", 1, 0, {"level": (i % 100) / 100.0}))
        self.assertGreater(sets, 35)
        self.assertLess(runs, 33, (runs, sets))
        self.r("pvj-shader", "clear", 1, 0)
        self.picture()
        self.r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        runs, sets = self.rate(1, lambda i: self.r("pvj-shader", "set", 1, 1, {"@amount": 0.5 + (i % 50) / 100.0}))
        self.assertGreater(sets, 35)
        self.assertLess(runs, 33, (runs, sets))
        self.assertGreater(runs, 10, "the values never landed")
# Two effects whose order shows: RED_ON sets red to 1, INVERT inverts. RED_ON then INVERT: red 0; the other way: red 1.
RED_ON = INVERT.replace("vec4(1.0 - c.rgb, 1.0)", "vec4(1.0, c.g, c.b, 1.0)")


class ChainTest(SourcesBase):
    """Step b: up to three effects in order over any source (D106)."""

    def setUp(self):
        super().setUp()
        self.n = 0
        self.blue = os.path.join(self.tmp, "blue.png")
        png(self.blue, W, H, lambda x, y: BLUE)

    write = NativeBase.write
    effect = NativeBase.effect

    def slot(self, source, k):
        return [s for s in self.r("get_property", "pvj-shaders") if s["source"] == source and s["slot"] == k][0]

    def test_an_effect_on_source_2_and_a_chain_in_its_order(self):
        r = self.r
        r.load(self.green)                              # source 1: green
        self.to(2, self.blue)                           # source 2: blue
        self.show_halves(1, 2)
        self.halves_are(GREEN, BLUE)
        r("pvj-shader", "load", 2, 1, self.effect(INVERT))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 255, 0)), 3), r.pixel(3 * W // 4, H // 4))
        self.halves_are(GREEN, (255, 255, 0))           # source 1 untouched
        # a second effect after it: red set to 1 after the invert
        r("pvj-shader", "load", 2, 2, self.effect(RED_ON))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 255, 0)), 3))
        # the other order: red on first, then the invert takes it to 0
        r("pvj-shader", "load", 2, 1, self.effect(RED_ON))
        r("pvj-shader", "load", 2, 2, self.effect(INVERT))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (0, 255, 0)), 3), r.pixel(3 * W // 4, H // 4))
        # a third, and one at amount 0 in the middle passes the picture on as it is
        r("pvj-shader", "load", 2, 3, self.effect(INVERT))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 0, 255)), 3))
        r("pvj-shader", "set", 2, 2, {"@amount": 0.0})
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (0, 255, 0)), 3), r.pixel(3 * W // 4, H // 4))
        # source 1's own chain: its effect and one after it
        r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        r("pvj-shader", "load", 1, 2, self.effect(RED_ON))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(W // 4, H // 4), (255, 0, 255)), 3), r.pixel(W // 4, H // 4))
        for k in (1, 2, 3):
            r("pvj-shader", "clear", 2, k)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), BLUE), 3))

    def test_each_sources_level_is_drawn_after_its_effects(self):
        r = self.r
        r.load(self.green)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        r("pvj-shader", "load", 2, 1, self.effect(INVERT))
        r("set_property", "brightness", -100)            # Blackout: every source, under an effect that adds light
        self.assertTrue(r.wait(lambda: max(r.capture()[2]) == 0, 3), "an inverting effect on source 2 undid the Blackout")
        self.assertEqual(r("pvj-source", 2, "get_property", "brightness"), -100)
        r("set_property", "brightness", 0)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 255, 0)), 3))
        r("pvj-source", 2, "set_property", "brightness", -50)        # a source's own level, after its effect too
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (128, 128, 0), 3), 3), r.pixel(3 * W // 4, H // 4))
        self.assertEqual(r("pvj-source", 2, "get_property", "brightness"), -50)
        r("pvj-shader", "clear", 2, 1)                  # off: the layer has its level back
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (0, 0, 128), 3), 3), r.pixel(3 * W // 4, H // 4))

    def test_an_idle_source_shows_black_under_its_effect(self):
        # the review of #168: an invert over an idle source's black lit its surface white
        r = self.r
        r.load(self.green)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        r("pvj-shader", "load", 2, 1, self.effect(INVERT))
        r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 255, 0)), 3))
        r("pvj-source", 2, "stop")
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), BLACK), 3), r.pixel(3 * W // 4, H // 4))
        r("stop")                                       # source 1 with nothing on: black too, its effect still loaded
        self.assertTrue(r.wait(lambda: near_px(r.pixel(W // 4, H // 4), BLACK), 3), r.pixel(W // 4, H // 4))
        self.assertEqual(self.slot(1, 1)["state"], "on")

    def test_an_effect_runs_again_when_the_one_before_it_goes(self):
        # the only trigger then is what the effect read last: no picture is new and no value moved
        r = self.r
        r.load(self.green)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        r("pvj-shader", "load", 2, 1, self.effect(RED_ON))
        r("pvj-shader", "load", 2, 2, self.effect(INVERT))
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (0, 255, 0)), 3))
        r("pvj-shader", "clear", 2, 1)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (255, 255, 0)), 3), r.pixel(3 * W // 4, H // 4))

    def test_each_sources_level_with_a_program_on_source_1(self):
        r = self.r
        r.load(self.green)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        r("pvj-shader", "load", 1, 1, self.effect(INVERT))
        r("pvj-shader", "load", 2, 1, self.effect(INVERT))
        r("set_property", "brightness", -100)
        self.assertTrue(r.wait(lambda: max(r.capture()[2]) == 0, 3), "an invert undid the Blackout somewhere")
        r("set_property", "brightness", 0)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(W // 4, H // 4), (255, 0, 255)), 3))
        r("pvj-source", 2, "set_property", "brightness", -50)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(3 * W // 4, H // 4), (128, 128, 0), 3), 3), r.pixel(3 * W // 4, H // 4))
        self.assertTrue(near_px(r.pixel(W // 4, H // 4), (255, 0, 255)), "source 2's own level reached source 1")
        self.assertEqual(r("get_property", "brightness"), 0)

    def test_the_passes_a_second(self):
        r = self.r
        self.assertEqual(r("get_property", "pvj-render")["fx-passes-per-second"], 0)
        self.to(2, self.blue)
        r("pvj-shader", "load", 1, 0, self.write(S.translate_native(S.parse(CLOCK), (W, H))))
        time.sleep(2.5)
        rate = r("get_property", "pvj-render")["fx-passes-per-second"]
        self.assertTrue(15 <= rate <= 35, rate)         # one generator at the 30-a-second cap

    def test_what_a_chain_refuses(self):
        r = self.r
        for what, cmd in {"an effect in a source's generator slot": ["load", 2, 0, self.effect(INVERT)],
                          "source 5 of 4": ["load", 5, 1, self.effect(INVERT)], "source 0": ["load", 0, 1, self.effect(INVERT)],
                          "slot 4": ["load", 2, 4, self.effect(INVERT)], "a set on an empty slot": ["set", 3, 1, {"@amount": 0.5}]}.items():
            with self.subTest(what):
                with self.assertRaises(PlayerError):
                    r("pvj-shader", *cmd)

    def test_many_chain_loads_leave_nothing_behind(self):
        r = self.r
        self.to(2, self.blue)
        a, b = self.effect(INVERT), self.effect(RED_ON)
        counts = []
        for i in range(150):
            src, k = 2 + i % 3, 1 + i % 3
            r("pvj-shader", "load", src, k, a if i % 2 else b)
            if i % 5 == 4:
                for s in (2, 3, 4):
                    for kk in (1, 2, 3):
                        r("pvj-shader", "clear", s, kk)
                if i % 50 == 49:
                    time.sleep(0.3)
                    st = r("get_property", "pvj-render")
                    counts.append((fd_count(r.pid), st["fx-gl-objects"], thread_count(r.pid)))
        self.assertEqual([c[:2] for c in counts], [counts[0][:2]] * len(counts), counts)
        self.assertEqual(counts[-1][1], 0)
        self.assertLessEqual(max(c[2] for c in counts) - min(c[2] for c in counts), 2, counts)


def near_px(got, want, tol=3):
    return all(abs(a - b) <= tol for a, b in zip(got, want))


class ChainSlowBuildTest(unittest.TestCase):
    """The review of #168: after a load that had no word (a slow build), Stop and Off still take the effect off."""

    def setUp(self):
        need_program(self)
        if "with test hooks" not in subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without its test hooks: make -C render TEST_HOOKS=1")
            self.skipTest("pvj-render was built without its test hooks")
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer("--sources=4", env={"PVJ_RENDER_TEST_SLOW_BUILD": "4000"})
        self.addCleanup(lambda: self.r.close())
        d = tempfile.mkdtemp(prefix="pvj-native-run-")
        self.addCleanup(shutil.rmtree, d, True)
        os.symlink(self.r.sock, os.path.join(d, paths.PLAYER_SOCKET))
        self.player = Player(rundir=d)

    def slot(self, s, k):
        return [x for x in self.r("get_property", "pvj-shaders") if x["source"] == s and x["slot"] == k][0]

    def test_stop_takes_off_an_effect_whose_load_had_no_word(self):
        prog = os.path.join(self.tmp, "e.prog")
        with open(prog, "w") as fh:
            fh.write(E.translate_native(S.parse(INVERT, S.FILTER)))
        self.assertEqual(self.player.chain_put(2, 1, prog)[0], "unknown")
        self.assertTrue(self.r.wait(lambda: self.slot(2, 1)["path"] == prog, 6))       # the renderer took it after all
        self.player.stop_source(2)
        self.assertTrue(self.r.wait(lambda: self.slot(2, 1)["state"] == "off", 3), self.slot(2, 1))
        # and Off, likewise, says it was on
        self.assertEqual(self.player.chain_put(2, 2, prog)[0], "unknown")
        self.assertTrue(self.r.wait(lambda: self.slot(2, 2)["path"] == prog, 6))
        self.assertTrue(self.player.chain_clear(2, 2))
        self.assertEqual(self.slot(2, 2)["state"], "off")


class ChainApiTest(unittest.TestCase):
    """The API of the chains (pvj/fxchain.py) on the real program through the real Player, with four sources."""

    def setUp(self):
        need_program(self)
        from tests.test_server import ServerBase
        self.server = ServerBase("run")
        self.server.setUp()
        self.addCleanup(self.server.doCleanups)
        self.r = Renderer("--sources=4")
        self.addCleanup(lambda: self.r.close())
        os.symlink(self.r.sock, os.path.join(self.server.rundir, paths.PLAYER_SOCKET))
        self.api = self.server.api
        self.player = Player(rundir=self.server.rundir)
        self.api.player = self.player
        self.api.registry.set_enabled("shaders", True)
        self.api.log = lambda *_: None

    def test_put_values_off_and_a_sources_stop(self):
        chains = self.api.chains
        st = chains.state()
        self.assertTrue(st["available"])
        self.assertEqual(st["slots"][1], [2, 3])
        self.assertEqual(st["slots"][2], [0, 1, 2, 3])                  # slot 0: a shader as the source (step d)
        out = chains.api_post({"source": 2, "slot": 1, "id": "fx-grade.fs"}, None, None)
        self.assertTrue(out["ok"])
        slot = [s for s in self.r("get_property", "pvj-shaders") if s["source"] == 2 and s["slot"] == 1][0]
        self.assertEqual(slot["state"], "on")
        path = chains.on[(2, 1)]["path"]
        self.assertTrue(is_program(path))
        out = chains.api_post({"source": 2, "slot": 1, "controls": {"amount": 0.25}}, None, None)
        self.assertEqual(out["controls"]["amount"], 0.25)
        self.assertEqual(chains.on[(2, 1)]["path"], path)                  # no new file: a value, not a build
        self.assertEqual([r["id"] for r in chains.state()["chains"]], ["fx-grade.fs"])
        chains.api_post({"source": 1, "slot": 2, "id": "fx-grade.fs"}, None, None)
        from pvj.api import ApiError
        for body in ({"source": 1, "slot": 1, "id": "fx-grade.fs"}, {"source": 5, "slot": 1, "id": "fx-grade.fs"},
                     {"source": 2, "slot": 0, "id": "fx-grade.fs"}, {"source": 2, "slot": True, "id": "fx-grade.fs"},
                     {"source": 2, "slot": 1, "id": "nxlx-silk.fs"}, {"source": 2, "slot": 1, "id": "fx-grade.fs", "values": {"nope": 1}},
                     {"source": 3, "slot": 1, "controls": {"amount": 0.5}}, {"source": 2}):
            with self.subTest(body=body):
                with self.assertRaises(ApiError):
                    chains.api_post(body, None, None)
        # a source's Stop takes its chain off; source 1's Stop takes source 1's
        self.player.stop_source(2)
        self.assertEqual([r["source"] for r in chains.state()["chains"]], [1])
        self.assertTrue(self.r.wait(lambda: [s for s in self.r("get_property", "pvj-shaders") if s["source"] == 2 and s["slot"] == 1][0]["state"] == "off", 3))
        self.player.clear()
        self.assertEqual(chains.state()["chains"], [])
        out = chains.api_post({"source": 3, "slot": 2, "id": "fx-grade.fs"}, None, None)
        self.assertEqual(chains.api_post({"source": 3, "off": True}, None, None)["was_on"], True)
        self.assertEqual(chains.state()["chains"], [])
        # no program file is left behind in the panel's folder
        self.assertEqual([n for n in os.listdir(self.server.rundir) if n.startswith("nxlx-chain")], [])

    def test_classic_has_no_chain(self):
        from pvj.api import ApiError
        self.player._native_seen = (self.player._seen_pid or self.player._note_pid(self.player.ipc.request("get_property", "pid")), False)
        with self.assertRaises(ApiError) as e:
            self.api.chains.api_post({"source": 2, "slot": 1, "id": "fx-grade.fs"}, None, None)
        self.assertEqual(e.exception.status, 409)
        self.assertFalse(self.api.chains.state()["available"])


# Step c: passes and buffers (D106). FEEDBACK adds 0.02 of red to its persistent buffer at every run and shows it.
FEEDBACK = ("/*{\"INPUTS\": [{\"NAME\": \"rise\", \"TYPE\": \"float\", \"DEFAULT\": 0.02}],\n"
            " \"PASSES\": [{\"TARGET\": \"acc\", \"PERSISTENT\": true}, {}]}*/\n"
            "void main() {\n"
            "    if (PASSINDEX == 0) { vec4 a = IMG_THIS_PIXEL(acc); gl_FragColor = vec4(min(a.r + rise, 1.0), 0.0, 0.0, 1.0); }\n"
            "    else { gl_FragColor = IMG_THIS_PIXEL(acc); }\n"
            "}\n")
# HALF: its first pass draws at half the width and says the size it was given; FLOATS: a value above 1 kept by a FLOAT buffer
HALF = ("/*{\"PASSES\": [{\"TARGET\": \"small\", \"WIDTH\": \"$WIDTH/2\", \"HEIGHT\": \"$HEIGHT/2\"}, {}]}*/\n"
        "void main() {\n"
        "    if (PASSINDEX == 0) { gl_FragColor = vec4(RENDERSIZE.x / 1000.0, RENDERSIZE.y / 1000.0, 0.0, 1.0); }\n"
        "    else { vec4 h = IMG_NORM_PIXEL(small, isf_FragNormCoord); gl_FragColor = vec4(h.rg, IMG_SIZE(small).x / 1000.0, 1.0); }\n"
        "}\n")
FLOATS = ("/*{\"PASSES\": [{\"TARGET\": \"big\", \"FLOAT\": true}, {}]}*/\n"
          "void main() {\n"
          "    if (PASSINDEX == 0) { gl_FragColor = vec4(2.0, 0.0, 0.0, 1.0); }\n"
          "    else { gl_FragColor = vec4(IMG_THIS_PIXEL(big).r / 4.0, 0.0, 0.0, 1.0); }\n"
          "}\n")
INVERT_TWICE = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}], \"PASSES\": [{\"TARGET\": \"buf\"}, {}]}*/\n"
                "void main() {\n"
                "    if (PASSINDEX == 0) { gl_FragColor = vec4(1.0 - IMG_THIS_PIXEL(inputImage).rgb, 1.0); }\n"
                "    else { gl_FragColor = vec4(IMG_THIS_PIXEL(buf).rgb, 1.0); }\n"
                "}\n")


# the picture's own size seen from a pass at half its size (the review of #170: ISF's IMG_SIZE is the picture's)
SRC_SIZE = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}],\n"
            " \"PASSES\": [{\"TARGET\": \"small\", \"WIDTH\": \"$WIDTH/2\", \"HEIGHT\": \"$HEIGHT/2\"}, {}]}*/\n"
            "void main() {\n"
            "    if (PASSINDEX == 0) { gl_FragColor = vec4(IMG_SIZE(inputImage).x / 1000.0, RENDERSIZE.x / 1000.0, 0.0, 1.0); }\n"
            "    else { gl_FragColor = vec4(IMG_THIS_PIXEL(small).rg, 0.0, 1.0); }\n"
            "}\n")
# a buffer that is not persistent, read before it is drawn: black every run, so it never grows
FRESH = ("/*{\"PASSES\": [{\"TARGET\": \"t\"}, {}]}*/\n"
         "void main() {\n"
         "    if (PASSINDEX == 0) { gl_FragColor = vec4(IMG_THIS_PIXEL(t).r + 0.1, 0.0, 0.0, 1.0); }\n"
         "    else { gl_FragColor = IMG_THIS_PIXEL(t); }\n"
         "}\n")
# a buffer of the picture's size in half floats: about 33 MB at 1920 x 1080 (two textures)
BIG_FX = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}], \"PASSES\": [{\"TARGET\": \"b\", \"FLOAT\": true}, {}]}*/\n"
          "void main() { if (PASSINDEX == 0) { gl_FragColor = IMG_THIS_PIXEL(inputImage); } else { gl_FragColor = IMG_THIS_PIXEL(b); } }\n")


class PassParseTest(unittest.TestCase):
    """What the panel takes of PASSES on the New renderer, and what classic still refuses."""

    def test_the_weight_of_an_effect_counts_every_pass_and_the_limit_does_not(self):
        """The reviews of #170 and #172: a filter of two passes draws twice, so its weight takes the count times the
        passes; the count itself, which already sums both PASSINDEX branches, is what the upload's limit judges."""
        from pvj import effects as E
        two_reads = INVERT.replace("vec4 c = IMG_THIS_PIXEL(inputImage);", "vec4 c = 0.5 * (IMG_THIS_PIXEL(inputImage) + IMG_NORM_PIXEL(inputImage, isf_FragNormCoord));")
        one = E.estimate(S.parse(two_reads, S.FILTER))
        two = E.estimate(S.parse(two_reads.replace('"image"}]}*/', '"image"}], "PASSES": [{"TARGET": "buf"}, {}]}*/'), S.FILTER, passes=True))
        self.assertEqual((one["reads"], one["weight"]), (2, "light"))
        self.assertEqual((two["reads"], two["weight"]), (2, "medium"))
        # a separable blur of 17 taps in two passes (each reads the picture: a buffer's reads are not counted yet,
        # D106): 34 reads, under the limit, heavy by its weight
        blur = ('/*{"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}], "PASSES": [{"TARGET": "h"}, {}]}*/\n'
                "void main() {\n    vec4 s = vec4(0.0);\n    if (PASSINDEX == 0) {\n"
                "        for (int i = -8; i <= 8; i++) { s += IMG_NORM_PIXEL(inputImage, isf_FragNormCoord + vec2(float(i) / RENDERSIZE.x, 0.0)); }\n"
                "    } else {\n"
                "        for (int i = -8; i <= 8; i++) { s += IMG_NORM_PIXEL(inputImage, isf_FragNormCoord + vec2(0.0, float(i) / RENDERSIZE.y)); }\n"
                "    }\n    gl_FragColor = s / 17.0;\n}\n")
        e = E.estimate(S.parse(blur, S.FILTER, passes=True))
        self.assertEqual((e["sure"], e["reads"], e["weight"]), (True, 34, "heavy"))
        self.assertLessEqual(e["reads"], E.MAX_READS)

    def test_the_program_file_and_classic(self):
        p = S.parse(FEEDBACK, passes=True)
        self.assertEqual(p["targets"], [{"name": "acc", "persistent": True, "float": False, "w": "w1", "h": "h1"}])
        self.assertEqual(p["plan"], [0, None])
        self.assertIn("pvj_t0_this()", p["code"])
        text = S.translate_native(p, (W, H))
        self.assertIn("//!TARGET acc 1 0 w1 h1\n//!PASS 0\n//!PASS -\n", text)
        self.assertEqual(S.parse(HALF, passes=True)["targets"][0]["w"], "w0.5")
        with self.assertRaises(S.ShaderError) as e:
            S.parse(FEEDBACK)
        self.assertIn("New renderer", str(e.exception))
        self.assertEqual(S.parse(FLAT, passes=True)["plan"], [None])        # one pass: nothing more in the file
        self.assertNotIn("//!PASS", S.translate_native(S.parse(FLAT, passes=True), (W, H)))
        fx = S.parse(INVERT_TWICE, S.FILTER, passes=True)
        self.assertIn("//!PASS 0", E.translate_native(fx))

    def test_what_is_refused(self):
        bad = {
            "the last pass into a buffer": FEEDBACK.replace(', {}]', ', {"TARGET": "x"}]'),
            "a pass before the last without one": FEEDBACK.replace('[{"TARGET": "acc", "PERSISTENT": true}, {}]', '[{}, {}]'),
            "five passes": FEEDBACK.replace(', {}]', ', {"TARGET": "b"}, {"TARGET": "c"}, {"TARGET": "d"}, {}]'),
            "a size of words": HALF.replace("$WIDTH/2", "$WIDTH/2 + 1"),
            "a size too small": HALF.replace("$WIDTH/2", "$WIDTH/64"),
            "a buffer called as an input": FEEDBACK.replace('"TARGET": "acc"', '"TARGET": "rise"'),
            "a buffer named as the language": FEEDBACK.replace('"TARGET": "acc"', '"TARGET": "float"').replace("(acc)", "(float)"),
            "a buffer read as a texture": FEEDBACK.replace("IMG_THIS_PIXEL(acc); gl_FragColor", "texture(acc, vec2(0.0)); gl_FragColor"),
            "an unknown key": FEEDBACK.replace('"PERSISTENT": true', '"PERSISTENT": true, "BLEND": 1'),
            "another picture": FEEDBACK.replace("IMG_THIS_PIXEL(acc); gl_FragColor", "IMG_THIS_PIXEL(other); gl_FragColor"),
            "a size on the last pass": FEEDBACK.replace(', {}]', ', {"WIDTH": 100}]'),
            "FLOAT on the last pass": FEEDBACK.replace(', {}]', ', {"FLOAT": true}]'),
            "one buffer at two sizes": FEEDBACK.replace('[{"TARGET": "acc", "PERSISTENT": true}, {}]',
                                                        '[{"TARGET": "acc", "WIDTH": "$WIDTH/2"}, {"TARGET": "acc", "WIDTH": "$WIDTH"}, {}]'),
            "a run of more than 4 pictures": HALF.replace('"WIDTH": "$WIDTH/2", "HEIGHT": "$HEIGHT/2"', '"WIDTH": "$WIDTH*2", "HEIGHT": "$HEIGHT*2"'),
            "a buffer named as GLSL reserves": FEEDBACK.replace('"TARGET": "acc"', '"TARGET": "dvec2"').replace("(acc)", "(dvec2)"),
            "an input named as GLSL reserves": FEEDBACK.replace('\"NAME\": \"rise\"', '\"NAME\": \"image2D\"').replace("+ rise", "+ image2D"),
        }
        for what, text in bad.items():
            with self.subTest(what):
                with self.assertRaises(S.ShaderError):
                    S.parse(text, passes=True)


class BufferBudgetTest(NativeBase):
    """The review of #170: every program's buffers together on the box, at most 192 MB."""
    ARGS = ("--headless=1920x1080", "--sources=4")

    def test_the_box_wide_cap_and_a_slot_given_back(self):
        prog = self.effect(BIG_FX)          # about 33 MB at 1920 x 1080
        placed = []
        refused = None
        for s, k in ((1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1)):
            try:
                self.r("pvj-shader", "load", s, k, prog)
                placed.append((s, k))
            except PlayerError as e:
                refused = (s, k, str(e))
                break
        self.assertEqual(len(placed), 6, (placed, refused))          # 6 of 31.6 MiB fit in 192, a seventh does not
        self.assertIn("every program on the box", refused[2])
        # a slot that already holds one takes another in its place (its own buffers count as given back)
        self.r("pvj-shader", "load", 1, 1, prog)
        self.r("pvj-shader", "clear", *placed[0])
        self.r("pvj-shader", "load", refused[0], refused[1], prog)       # room again
        for s, k in placed[1:] + [refused[:2]]:
            self.r("pvj-shader", "clear", s, k)
        self.assertEqual(self.r("get_property", "pvj-render")["fx-gl-objects"], 0)


class PassTest(NativeBase):
    """Persistent and feedback passes in pvj-render (D106 step c)."""

    def red(self):
        return self.r.pixel(10, 10)[0]

    def test_a_feedback_buffer_grows_keeps_through_values_and_starts_over_on_a_load(self):
        self.carrier()
        prog = self.generator(FEEDBACK)
        self.r("pvj-shader", "load", 1, 0, prog)
        a = self.red()
        self.assertTrue(self.r.wait(lambda: self.red() > a + 20, 3), (a, self.red()))
        b = self.red()
        self.assertTrue(self.r.wait(lambda: self.red() > b, 3))            # it goes on growing, frame by frame
        self.r("pvj-shader", "set", 1, 0, {"rise": 0.001})               # a value: the buffer keeps what it holds
        time.sleep(0.2)
        c = self.red()
        self.assertGreaterEqual(c, b)
        self.r("pvj-shader", "set", 1, 0, {"rise": 0.02})
        self.assertTrue(self.r.wait(lambda: self.red() == 255, 5))
        self.r("pvj-shader", "load", 1, 0, prog)                         # a load: the buffer starts black again
        self.assertLess(self.red(), 128)
        self.assertEqual(self.slot(0)["state"], "on")

    def test_a_pass_draws_at_its_own_size(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(HALF))
        time.sleep(0.3)
        got = self.r.pixel(10, 10)
        want = (round(W / 2 / 1000 * 255), round(H / 2 / 1000 * 255), round(W / 2 / 1000 * 255))
        self.assertTrue(all(abs(g - w) <= 2 for g, w in zip(got, want)), (got, want))

    def test_a_float_buffer_keeps_values_above_one(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FLOATS))
        time.sleep(0.3)
        state = self.slot(0)["state"]
        want = 64 if "8-bit" in state else 128                   # 2 / 4 kept, or 1 / 4 where the GPU has no float buffers
        self.assertLessEqual(abs(self.red() - want), 2, (self.red(), state))
        print("\n  a FLOAT buffer here: %s" % ("8-bit (no half floats)" if "8-bit" in state else "half float"))

    def test_an_effect_of_two_passes(self):
        self.picture()
        self.r("pvj-shader", "load", 1, 1, self.effect(INVERT_TWICE))
        time.sleep(0.3)
        got = self.r.pixel(5, 5)
        want = tuple(255 - c for c in gradient(5, 5))
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(got, want)), (got, want))

    def test_a_buffer_that_is_not_persistent_starts_each_run_black(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FRESH))
        time.sleep(0.5)
        a = self.red()
        time.sleep(1.0)
        self.assertLessEqual(abs(self.red() - a), 1)
        self.assertLessEqual(abs(a - 26), 2, a)          # 0.1 over black, every run

    def test_a_filter_sees_its_pictures_own_size_from_a_smaller_pass(self):
        self.picture()
        self.r("pvj-shader", "load", 1, 1, self.effect(SRC_SIZE))
        time.sleep(0.3)
        got = self.r.pixel(10, 10)
        want = (round(W / 1000 * 255), round(W / 2 / 1000 * 255))     # IMG_SIZE: the picture's; RENDERSIZE: the pass's
        self.assertTrue(abs(got[0] - want[0]) <= 2 and abs(got[1] - want[1]) <= 2, (got, want))

    def test_every_pass_is_counted(self):
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(FEEDBACK))
        time.sleep(2.5)
        rate = self.r("get_property", "pvj-render")["fx-passes-per-second"]
        self.assertTrue(30 <= rate <= 70, rate)          # two passes a run, about 30 runs a second

    def test_buffers_too_large_are_refused_and_loads_leave_nothing(self):
        self.carrier()
        huge = FEEDBACK.replace('"PERSISTENT": true', '"PERSISTENT": true, "WIDTH": 4096, "HEIGHT": 4096')
        with self.assertRaises(PlayerError) as e:
            self.r("pvj-shader", "load", 1, 0, self.generator(huge))
        self.assertIn("MB", str(e.exception))
        progs = [self.generator(FEEDBACK), self.generator(HALF), self.generator(FLOATS)]
        for i in range(60):
            self.r("pvj-shader", "load", 1, 0, progs[i % 3])
        self.r("pvj-shader", "clear", 1, 0)
        self.assertEqual(self.r("get_property", "pvj-render")["fx-gl-objects"], 0)


class GeneratorSourceTest(SourcesBase):
    """Step d: a generator as the whole picture of a source 2 to N (D106)."""

    def setUp(self):
        super().setUp()
        self.n = 0

    write = NativeBase.write
    effect = NativeBase.effect

    def slot(self, source, k):
        return [s for s in self.r("get_property", "pvj-shaders") if s["source"] == source and s["slot"] == k][0]

    def test_a_generator_is_a_sources_picture_with_its_effects_and_level(self):
        r = self.r
        r.load(self.green)
        self.show_halves(1, 2)
        right = (3 * W // 4, H // 4)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(*right), BLACK), 3))         # source 2 idle: black
        gen = self.write(S.translate_native(S.parse(FLAT, passes=True), (W, H)))
        r("pvj-shader", "load", 2, 0, gen)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(*right), (64, 128, 0)), 3), r.pixel(*right))
        self.assertTrue(near_px(r.pixel(W // 4, H // 4), GREEN), "source 1 changed")
        self.assertTrue(self.sources()[1]["idle"], "the clip layer under the generator plays nothing")
        r("pvj-shader", "set", 2, 0, {"level": 1.0})
        self.assertTrue(r.wait(lambda: near_px(r.pixel(*right), (255, 128, 0)), 3))
        r("pvj-shader", "load", 2, 1, self.effect(INVERT))                          # an effect over it
        self.assertTrue(r.wait(lambda: near_px(r.pixel(*right), (0, 127, 255)), 3), r.pixel(*right))
        r("set_property", "brightness", -100)                                       # the Blackout reaches it
        self.assertTrue(r.wait(lambda: max(r.capture()[2]) == 0, 3))
        r("set_property", "brightness", 0)
        r("pvj-shader", "clear", 2, 0)
        r("pvj-shader", "clear", 2, 1)
        self.assertTrue(r.wait(lambda: near_px(r.pixel(*right), BLACK), 3))


class GeneratorSourceApiTest(unittest.TestCase):
    """A shader pad, or a shader by name, played to Source 2 (the API, pvj/fxchain.py), on the real program."""

    def setUp(self):
        need_program(self)
        from tests.test_server import ServerBase
        self.server = ServerBase("run")
        self.server.setUp()
        self.addCleanup(self.server.doCleanups)
        self.r = Renderer("--sources=4")
        self.addCleanup(lambda: self.r.close())
        os.symlink(self.r.sock, os.path.join(self.server.rundir, paths.PLAYER_SOCKET))
        self.api = self.server.api
        self.player = Player(rundir=self.server.rundir)
        self.api.player = self.player
        self.api.registry.set_enabled("shaders", True)
        self.api.log = lambda *_: None
        self.player.ipc.request("set_property", "image-display-duration", "inf")

    def slot(self, s, k):
        return [x for x in self.r("get_property", "pvj-shaders") if x["source"] == s and x["slot"] == k][0]

    def test_a_shader_to_source_2_its_values_a_clip_in_its_place_and_stop(self):
        dev = {"id": "t", "role": "full"}
        out = self.api.play({"shader": "nxlx-silk.fs", "source": 2}, dev, "t")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.slot(2, 0)["state"], "on")
        self.assertEqual(self.slot(2, 0)["kind"], "generator")
        rows = {r["source"]: r for r in self.api.get_sources({}, dev, "t")["sources"]}
        self.assertEqual((rows[2]["shader"], rows[2]["name"], rows[2]["idle"]), ("nxlx-silk.fs", "nxlx-silk.fs", False))
        self.assertNotIn("shader", rows[3])                                 # a row with no shader is as it was
        path = self.api.chains.on[(2, 0)]["path"]
        out = self.api.chains.api_post({"source": 2, "slot": 0, "values": {"sheen": 0.9}, "controls": {"hue": 90.0}}, dev, "t")
        self.assertEqual(out["values"]["sheen"], 0.9)
        self.assertEqual(self.api.chains.on[(2, 0)]["path"], path)          # a value, not a build
        self.assertEqual(self.slot(2, 0)["rebuilds"], 0)
        # a clip played to source 2 takes the shader's place
        clip = os.path.join(self.server.media, "blue.png")
        png(clip, W, H, lambda x, y: BLUE)
        self.api.play({"file": "blue.png", "source": 2}, dev, "t")
        self.assertTrue(self.r.wait(lambda: self.slot(2, 0)["state"] == "off", 3))
        self.assertIsNone(self.api.chains.generator_of(2))
        # a shader pad to source 2, then its Stop
        banks = self.api.settings.data["pads"]["banks"]
        banks[0]["pads"][0] = {"label": "silk", "file": "", "shader": "nxlx-silk.fs"}
        out = self.api.play({"pad": [0, 0], "source": 2}, dev, "t")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.slot(2, 0)["state"], "on")
        self.api.set_sources({"source": 2, "action": "stop"}, dev, "t")
        self.assertTrue(self.r.wait(lambda: self.slot(2, 0)["state"] == "off", 3))
        self.assertEqual(self.api.chains.state()["chains"], [])
        # source 1's generator stays the shader engine's: slot 0 of source 1 is not the chain's
        from pvj.api import ApiError
        with self.assertRaises(ApiError):
            self.api.chains.api_post({"source": 1, "slot": 0, "values": {"sheen": 0.5}}, dev, "t")

    def source2(self):
        return [r for r in self.player.sources_state() if r["source"] == 2][0]

    def test_a_refused_shader_leaves_the_clip_playing_and_a_taken_one_stops_it(self):
        """The review of #171: the clip stops only once the shader is taken; a refusal never leaves the source black."""
        from pvj.api import ApiError
        dev = {"id": "t", "role": "full"}
        png(os.path.join(self.server.media, "blue.png"), W, H, lambda x, y: BLUE)
        self.api.play({"file": "blue.png", "source": 2}, dev, "t")
        self.assertTrue(self.r.wait(lambda: not self.source2()["idle"], 3))
        self.api.shaders.upload("broken.fs", "/*{}*/\nvoid main() { gl_FragColor = vec4(nonsense, 1.0); }\n")
        with self.assertRaises(ApiError) as e:
            self.api.play({"shader": "broken.fs", "source": 2}, dev, "t")
        self.assertEqual(e.exception.status, 422)
        self.assertIn("stays on", e.exception.message)
        time.sleep(0.3)
        row = self.source2()
        self.assertFalse(row["idle"], row)                                  # the clip plays on
        self.assertTrue(row["path"].endswith("blue.png"), row)
        self.assertEqual({r["source"]: r for r in self.api.get_sources({}, dev, "t")["sources"]}[2]["name"], "blue.png")
        out = self.api.play({"shader": "nxlx-silk.fs", "source": 2}, dev, "t")
        self.assertTrue(out["ok"], out)
        self.assertTrue(self.r.wait(lambda: self.source2()["idle"], 3))        # taken: the clip under it stopped
        self.assertEqual(self.slot(2, 0)["state"], "on")

    def test_a_shader_pad_whose_preset_is_gone_plays_with_a_note(self):
        dev = {"id": "t", "role": "full"}
        banks = self.api.settings.data["pads"]["banks"]
        banks[0]["pads"][1] = {"label": "silk", "file": "", "shader": "nxlx-silk.fs", "preset": "long gone"}
        out = self.api.play({"pad": [0, 1], "source": 2}, dev, "t")
        self.assertTrue(out["ok"], out)
        self.assertIn("long gone", out["note"])
        self.assertEqual(self.slot(2, 0)["state"], "on")

    def test_a_replaced_upload_and_a_change_of_picture_detail_build_source_2_again(self):
        """The review of #171, built in step e: Sources 2 to N follow their shader's file and Picture detail."""
        dev = {"id": "t", "role": "full"}
        self.api.shaders.upload("mine.fs", FLAT)
        self.api.play({"shader": "mine.fs", "source": 2}, dev, "t")
        self.api.chains.api_post({"source": 2, "slot": 0, "values": {"level": 0.75}}, dev, "t")
        first = self.api.chains.on[(2, 0)]["path"]
        self.api.shaders.api_set({"action": "upload", "name": "mine.fs", "source": FLAT.replace("0.25", "0.5"), "replace": True}, dev, "t")
        again = self.api.chains.on[(2, 0)]
        self.assertNotEqual(again["path"], first)                           # built again from the new file
        self.assertEqual(again["values"]["level"], 0.75)                    # with the value it had
        self.assertEqual((self.slot(2, 0)["state"], self.slot(2, 0)["path"]), ("on", again["path"]))
        heights = [h for h in S.heights_for(self.api.shaders.board()) if h != self.api.shaders.height_for("mine.fs")]
        self.api.shaders.api_set({"action": "config", "height": heights[0]}, dev, "t")
        third = self.api.chains.on[(2, 0)]["path"]
        self.assertNotEqual(third, again["path"])                            # and again at the new detail
        self.assertEqual(self.slot(2, 0)["path"], third)
        # another shader's upload leaves it as it is
        self.api.shaders.upload("other.fs", FLAT)
        self.api.shaders.api_set({"action": "upload", "name": "other.fs", "source": FLAT, "replace": True}, dev, "t")
        self.assertEqual(self.api.chains.on[(2, 0)]["path"], third)

    def race(self, other):
        """mine.fs on source 2, then a rebuild with `other` landing between its look at the sources and its build."""
        self.api.play({"shader": "mine.fs", "source": 2}, {"id": "t", "role": "full"}, "t")
        eng, fired = self.api.shaders, []
        real = eng._parsed

        def hook(path, native=None):
            if not fired and path.endswith("mine.fs"):
                fired.append(1)
                other()
            return real(path, native)
        eng._parsed = hook
        try:
            out = self.api.chains.rebuild_generators()
        finally:
            eng._parsed = real
        time.sleep(0.3)
        return out, [r for r in self.player.sources_state() if r["source"] == 2][0]

    def test_a_rebuild_leaves_what_came_to_the_source_meanwhile(self):
        """The review of #172: a Stop, a clip or another shader that lands while a rebuild runs is never undone."""
        dev = {"id": "t", "role": "full"}
        self.api.shaders.upload("mine.fs", FLAT)
        png(os.path.join(self.server.media, "blue.png"), W, H, lambda x, y: BLUE)
        with self.subTest("a Stop"):
            out, row = self.race(lambda: self.api.set_sources({"source": 2, "action": "stop"}, dev, "t"))
            self.assertEqual((out["rebuilt"], self.api.chains.generator_of(2), self.slot(2, 0)["state"], row["idle"]), ([], None, "off", True))
        with self.subTest("a clip"):
            out, row = self.race(lambda: self.api.play({"file": "blue.png", "source": 2}, dev, "t"))
            self.assertEqual((out["rebuilt"], self.api.chains.generator_of(2), row["idle"]), ([], None, False))
            self.assertTrue(row["path"].endswith("blue.png"), row)
        with self.subTest("another shader"):
            out, row = self.race(lambda: self.api.play({"shader": "nxlx-silk.fs", "source": 2}, dev, "t"))
            self.assertEqual((out["rebuilt"], self.api.chains.generator_of(2), self.slot(2, 0)["state"]), ([], "nxlx-silk.fs", "on"))

    def test_a_rebuild_drops_a_value_the_new_file_has_no_input_for_and_says_what_it_could_not_build(self):
        dev = {"id": "t", "role": "full"}
        self.api.shaders.upload("mine.fs", FLAT)
        self.api.play({"shader": "mine.fs", "source": 2}, dev, "t")
        self.api.chains.api_post({"source": 2, "slot": 0, "values": {"lit": False, "level": 0.75}}, dev, "t")
        first = self.api.chains.on[(2, 0)]["path"]
        no_lit = FLAT.replace(" {\"NAME\": \"lit\", \"TYPE\": \"bool\", \"DEFAULT\": true},\n", "").replace("lit ? 0.5 : 0.0", "0.5")
        out = self.api.shaders.api_set({"action": "upload", "name": "mine.fs", "source": no_lit, "replace": True}, dev, "t")
        again = self.api.chains.on[(2, 0)]
        self.assertNotEqual(again["path"], first)
        self.assertNotIn("lit", again["values"])
        self.assertEqual(again["values"]["level"], 0.75)
        self.assertNotIn("sources_not_rebuilt", out)
        # a new file the GPU refuses: the one before stays on, and the answer says so
        broken = FLAT.replace("vec4(level,", "vec4(no_such_fn(level),")
        out = self.api.shaders.api_set({"action": "upload", "name": "mine.fs", "source": broken, "replace": True}, dev, "t")
        self.assertEqual([(x["source"], x["id"]) for x in out["sources_not_rebuilt"]], [(2, "mine.fs")])
        self.assertIn("no_such_fn", out["sources_not_rebuilt"][0]["why"])
        self.assertEqual(self.api.chains.on[(2, 0)]["path"], again["path"])
        self.assertEqual((self.slot(2, 0)["state"], self.slot(2, 0)["path"]), ("on", again["path"]))

    def test_the_same_picture_detail_again_builds_nothing(self):
        dev = {"id": "t", "role": "full"}
        self.api.play({"shader": "nxlx-silk.fs", "source": 3}, dev, "t")
        heights = [h for h in S.heights_for(self.api.shaders.board()) if h != self.api.shaders.height_for("nxlx-silk.fs")]
        self.api.shaders.api_set({"action": "config", "height": heights[0]}, dev, "t")
        built = self.api.chains.on[(3, 0)]["path"]
        self.api.shaders.api_set({"action": "config", "height": heights[0]}, dev, "t")
        self.assertEqual(self.api.chains.on[(3, 0)]["path"], built)

    def test_an_effect_put_on_asks_once_whether_the_player_runs_programs(self):
        """The review of #172: the effects' put, as the shaders' play (reverting it was caught by nothing)."""
        png(os.path.join(self.server.media, "blue.png"), W, H, lambda x, y: BLUE)
        self.api.play({"file": "blue.png"}, {"id": "t", "role": "full"}, "t")
        time.sleep(0.5)
        fx, calls = self.api.effects, []
        fx.upload("inv.fs", INVERT)
        real = fx.native
        fx.native = lambda: calls.append(1) or real()
        self.addCleanup(lambda: setattr(fx, "native", real))
        fx._cache.clear()
        out = fx.put("inv.fs")
        self.assertTrue(out and out.get("ok", True), out)
        self.assertEqual(len(calls), 1, calls)

    def test_a_play_asks_once_whether_the_player_runs_programs(self):
        """The review of #170, built in step e: the parse and the text of one play from one answer."""
        eng, calls = self.api.shaders, []
        real = eng.native
        eng.native = lambda: calls.append(1) or real()
        self.addCleanup(lambda: setattr(eng, "native", real))
        eng._cache.clear()
        eng.play("nxlx-silk.fs")
        self.assertEqual(len(calls), 1, calls)

    def test_on_classic_a_shader_to_a_source_is_refused_as_before(self):
        from pvj.api import ApiError
        self.player._native_seen = (self.player._seen_pid or self.player._note_pid(self.player.ipc.request("get_property", "pid")), False)
        with self.assertRaises(ApiError) as e:
            self.api.play({"shader": "nxlx-silk.fs", "source": 2}, {"id": "t", "role": "full"}, "t")
        self.assertEqual(e.exception.status, 409)


def solid(r, g, b):
    """A generator that is one colour (each 0 to 1)."""
    return "/*{}*/\nvoid main() { gl_FragColor = vec4(%s, %s, %s, 1.0); }\n" % (r, g, b)


# the blue is an input, so a value set on the socket shows which layer's generator it reached
LEVEL = "/*{\"INPUTS\": [{\"NAME\": \"level\", \"TYPE\": \"float\", \"DEFAULT\": 0.2}]}*/\n" \
        "void main() { gl_FragColor = vec4(0.0, 0.0, level, 1.0); }\n"
FULL = 204          # 0.8 of 255, the colour of the solids below


class LayerGeneratorTest(NativeBase):
    """D130, slice 1: each of source 1's two video layers can hold a generator of its own. The layer's picture is its
    generator's, and a crossfade or a wipe mixes the two pictures, both moving. Slot 0 of source 1 on the socket is the
    front layer's generator (the incoming one from the moment a fade starts); the outgoing layer's generator runs on to
    the end of the fade and is freed with it, on the render thread. Nothing here is the panel: that is slice 3."""

    SECONDS = 8.0

    def setUp(self):
        super().setUp()
        self.blue = os.path.join(self.tmp, "blue.png")
        png(self.blue, W, H, lambda x, y: BLUE)

    def st(self):
        return self.r("get_property", "pvj-render")

    def objects(self):
        return self.st()["fx-gl-objects"]

    def red_on_the_screen(self):
        """A clip playing in the front layer (the panel's carrier) and a red generator on it: the state before a fade."""
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, self.generator(solid(0.8, 0, 0)))
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] > FULL - 8, 5), self.r.pixel(10, 10))

    def fade(self, move=None, seconds=None):
        args = ["pvj-crossfade", self.blue, seconds or self.SECONDS, "keep-open=no,image-display-duration=inf"]
        self.r(*(args + ([move] if move else [])))

    def running(self, low=0.25, high=0.7):
        look = lambda: self.st()
        self.assertTrue(self.r.wait(lambda: look()["fade"] == "running" and low < look()["fade-progress"] < high, 15, 0.02), look())

    def settled(self):
        self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", self.SECONDS + 10, 0.05), self.st())

    def both(self, wait_running=True):
        """Red on the outgoing layer, a fade begun, and a second generator loaded for the incoming one (slot 0 now means
        the front layer, which is the incoming one)."""
        self.red_on_the_screen()
        one = self.objects()
        self.fade("wipe-from-left" if self._move else None)
        self.r("pvj-shader", "load", 1, 0, self.generator(LEVEL if self._level else solid(0, 0, 0.8)))
        return one

    _move = False
    _level = False

    def test_the_two_generators_are_mixed_by_the_share_and_the_outgoing_one_is_freed_at_the_end(self):
        one = self.both()
        self.running()
        p0 = self.st()["fade-progress"]
        shot = self.r.capture()
        p1 = self.st()["fade-progress"]
        r, g, b = self.r.pixel(W // 2, H // 2, shot)
        p = 1 - r / FULL                  # red goes from FULL to 0 as the share goes from 0 to 1
        self.assertTrue(p0 - 0.05 <= p <= p1 + 0.05, "the share by the red %.3f, by the clock %.3f to %.3f: %s" % (p, p0, p1, (r, g, b)))
        self.assertLessEqual(abs(b - FULL * p), 8, (r, g, b))
        self.assertLessEqual(g, 4, (r, g, b))
        self.assertEqual(self.objects(), 2 * one, "two programs alive for the length of the fade")
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), "the outgoing program was not freed: %s" % self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6 and px[1] < 6, px)
        slot = self.r("get_property", "pvj-shaders")[0]
        self.assertEqual((slot["kind"], slot["state"]), ("generator", "on"))     # the incoming one, now the only one
        st = self.st()
        self.assertEqual((st["fades-done"], st["fades-cut"], st["layer-generators"]), (1, 0, True))

    def test_a_wipe_has_a_generator_on_each_side_of_its_edge(self):
        self._move = True
        try:
            self.both()
        finally:
            self._move = False
        self.running(0.3, 0.6)
        shot = self.r.capture()
        left, right = self.r.pixel(4, H // 2, shot), self.r.pixel(W - 4, H // 2, shot)
        self.assertTrue(left[2] > FULL - 8 and left[0] < 6, "the incoming generator on the left: %s" % (left,))
        self.assertTrue(right[0] > FULL - 8 and right[2] < 6, "the outgoing generator on the right: %s" % (right,))
        self.settled()

    def test_a_value_goes_to_the_incoming_generator_and_the_outgoing_one_is_left_alone(self):
        self._move = True
        self._level = True
        try:
            self.both()
        finally:
            self._move = self._level = False
        self.running(0.3, 0.6)
        self.r("pvj-shader", "set", 1, 0, {"level": 0.9})
        self.assertTrue(self.r.wait(lambda: self.r.pixel(4, H // 2)[2] > 220, 5), self.r.pixel(4, H // 2))
        right = self.r.pixel(W - 4, H // 2)
        self.assertTrue(right[0] > FULL - 8 and right[2] < 6, "the outgoing generator (right of the edge) moved: %s" % (right,))
        self.assertEqual(self.r("get_property", "pvj-shaders")[0]["inputs"], 1)
        self.settled()

    def test_both_generators_keep_drawing_through_the_fade(self):
        self.red_on_the_screen()

        def passes(seconds):
            before, t0 = self.st()["fx-passes"], time.monotonic()
            time.sleep(seconds)
            return (self.st()["fx-passes"] - before) / (time.monotonic() - t0)
        alone = passes(1.5)
        self.assertGreater(alone, 20, alone)                # --fx-fps 30 by default
        self.fade()
        self.r("pvj-shader", "load", 1, 0, self.generator(solid(0, 0, 0.8)))
        self.running(0.1, 0.5)
        both = passes(1.5)
        self.assertGreater(both, 1.6 * alone, "the outgoing generator stopped drawing: %.1f a second, one alone drew %.1f" % (both, alone))
        self.settled()

    def test_a_generator_out_to_a_clip_runs_to_the_end_of_the_fade_and_is_freed(self):
        self.red_on_the_screen()
        self.fade()
        self.running()
        shot = self.r.capture()
        r, g, b = self.r.pixel(W // 2, H // 2, shot)
        self.assertTrue(r > 20 and b > 20 and g < 6, "a mix of the red generator and the blue clip: %s" % ((r, g, b),))
        self.assertEqual(self.objects() > 0, True)
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())
        slot = self.r("get_property", "pvj-shaders")[0]
        self.assertEqual(slot["state"], "off")
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > 240 and px[0] < 6, px)

    def test_a_load_or_a_new_crossfade_in_the_middle_of_a_fade_takes_the_outgoing_generator_off(self):
        one = self.both()
        self.running()
        self.assertEqual(self.objects(), 2 * one)
        self.r("loadfile", self.blue, "replace")                 # a load ends the fade on its incoming side (D98)
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        st = self.st()
        self.assertEqual((st["fade"], st["fades-cut"]), ("off", 1))
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6, "the incoming generator is the picture: %s" % (px,))
        # and a new crossfade does the same: red out, blue in, then asked again while that runs
        self.r("pvj-shader", "load", 1, 0, self.generator(solid(0.8, 0, 0)))
        self.fade()
        self.r("pvj-shader", "load", 1, 0, self.generator(solid(0, 0, 0.8)))
        self.running(0.05, 0.5)
        self.assertEqual(self.objects(), 2 * one)
        self.fade(seconds=3.0)
        # the red went with the fade it was cut from; the blue is the outgoing picture of this one, and goes at its end
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        self.assertEqual(self.r("get_property", "pvj-shaders")[0]["state"], "off")      # slot 0 is the (new) front layer's: none yet
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())

    def test_a_refused_program_in_a_fade_leaves_both_pictures_as_they_were(self):
        one = self.both()
        self.running()
        with self.assertRaises(PlayerError):
            self.r("pvj-shader", "load", 1, 0, self.write("//!PVJ 1\n//!KIND generator\n//!SIZE 320 180\n//!CODE\nvoid pvj_main() { not glsl }\n"))
        self.assertEqual(self.objects(), 2 * one)
        self.assertEqual(self.st()["fade"], "running")
        self.settled()

    BAD = "//!PVJ 1\n//!KIND generator\n//!SIZE 320 180\n//!CODE\nvoid pvj_main() { not glsl }\n"
    TRIES = int(os.environ.get("PVJ_LOST_CLEAR_TRIES", "6"))

    def test_a_cut_then_a_refused_load_at_once_still_takes_the_outgoing_generator_off(self):
        """The review of #239 (High): a crossfade that cuts a fade between two generators asks the outgoing layer's
        generator off (end_fade), and a load that lands before the render thread's next turn used to overwrite that
        clear; if the load was then refused, the old generator stayed as the new front picture and was never freed.
        Timing dependent, so it is tried TRIES times (PVJ_LOST_CLEAR_TRIES)."""
        green = os.path.join(self.tmp, "green.png")
        png(green, W, H, lambda x, y: GREEN)
        self.carrier()
        for i in range(self.TRIES):
            self.r("pvj-shader", "load", 1, 0, self.generator(solid(0.8, 0, 0)))
            self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] > FULL - 8, 5), self.r.pixel(10, 10))
            one = self.objects()
            self.fade(seconds=8.0)
            self.r("pvj-shader", "load", 1, 0, self.generator(solid(0, 0, 0.8)))
            self.running(0.1, 0.8)
            self.assertEqual(self.objects(), 2 * one)
            # the cut and the refused load, back to back
            args = ["pvj-crossfade", green, 2.0, "keep-open=no,image-display-duration=inf"]
            self.r(*args)
            with self.assertRaises(PlayerError):
                self.r("pvj-shader", "load", 1, 0, self.write(self.BAD))
            self.settled()
            self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), "try %d: objects %d" % (i, self.objects()))
            self.assertEqual(self.slot(0)["state"], "off", "try %d" % i)
            px = self.r.pixel(W // 2, H // 2)
            self.assertTrue(px[1] > FULL - 8 and px[0] < 20 and px[2] < 20, "try %d: the clip is the picture: %s" % (i, px))
            self.r("stop")
            self.assertTrue(self.r.wait(lambda: self.objects() == 0 and self.st()["fade"] == "off", 5), self.st())
            self.carrier()

    def test_a_fade_on_nothing_but_clips_is_what_it_was(self):
        """No generator in either layer: the pictures are the layers' own, as before this step."""
        a = os.path.join(self.tmp, "red.png")
        png(a, W, H, lambda x, y: RED)
        self.r.load(a)
        self.fade()
        self.running(0.3, 0.7)
        r, g, b = self.r.pixel(W // 2, H // 2)
        self.assertTrue(r > 50 and b > 50 and g < 40, (r, g, b))
        self.settled()
        self.assertEqual(self.objects(), 0)
        self.assertFalse(self.r("get_property", "pvj-shaders")[0]["state"] != "off")


class LayerLoadAnswerTest(NativeBase):
    """The review of #239 (Medium): a load that waits for the render thread while its layer is cut (the layer goes out and
    its generator is taken off before the load was built) is not answered "success" with the slot off; it is told the
    layer was ended first. Two clients, and a slow build (a test hook) to keep the render thread busy for the window."""

    def setUp(self):
        need_program(self)
        if "with test hooks" not in subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without its test hooks: make -C render TEST_HOOKS=1")
            self.skipTest("pvj-render was built without its test hooks")
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(env={"PVJ_RENDER_TEST_SLOW_BUILD": "3500"})
        self.addCleanup(lambda: self.r.close())
        self.r("set_property", "image-display-duration", "inf")
        self.n = 0

    def test_a_load_cut_with_its_layer_while_it_waits_is_not_answered_success(self):
        green = os.path.join(self.tmp, "green.png")
        png(green, W, H, lambda x, y: GREEN)
        self.carrier()
        said = {}

        def load(name, path):
            ipc = Ipc(self.r.sock, timeout=12.0)
            try:
                ipc.request("pvj-shader", "load", 1, 0, path, wait=6.0)
                said[name] = "success"
            except PlayerError as e:
                said[name] = str(e)

        a = threading.Thread(target=load, args=("a", self.generator(solid(0.8, 0, 0))))
        a.start()                                   # client 1: its build holds the render thread for 3.5 s; it times out at 3 s
        time.sleep(3.1)
        b = threading.Thread(target=load, args=("b", self.generator(solid(0, 0, 0.8))))
        b.start()                                   # client 2: waits behind that build, on the same layer
        time.sleep(0.1)
        keep = "keep-open=no,image-display-duration=inf"
        self.r("pvj-crossfade", green, 20.0, keep)          # the load's layer goes out ...
        self.r("pvj-crossfade", green, 0.2, keep)           # ... and a new crossfade cuts that fade: end_fade drops the waiting load
        a.join(15)
        b.join(15)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertTrue(self.r.wait(lambda: self.r("get_property", "pvj-render")["fade"] == "off", 20, 0.1))
        self.assertNotEqual(said.get("b"), "success", said)
        self.assertIn("ended", said.get("b", ""), said)
        self.assertTrue(self.r.wait(lambda: self.r("get_property", "pvj-render")["fx-gl-objects"] == 0, 8), self.r("get_property", "pvj-render"))
        self.assertEqual(self.slot(0)["state"], "off")


class ManyLayerFadesTest(NativeBase):
    """A show fades between shaders all night: fades with a generator on each side, some cut short by the next, leave no
    program, texture, descriptor or thread behind. Bounded by a deadline."""

    FADES = int(os.environ.get("PVJ_LAYER_FADES", "160"))     # 160 in CI (about 80 s); the design's 2,000 by hand
    LIMIT = max(420.0, FADES * 0.7)

    def test_fades_between_generators_leave_nothing_behind(self):
        deadline = time.monotonic() + self.LIMIT
        pics = [os.path.join(self.tmp, "p%d.png" % i) for i in range(2)]
        png(pics[0], W, H, lambda x, y: RED)
        png(pics[1], W, H, lambda x, y: BLUE)
        gens = [self.generator(solid(0.8, 0, 0)), self.generator(solid(0, 0, 0.8))]
        st = lambda: self.r("get_property", "pvj-render")
        self.carrier()
        self.r("pvj-shader", "load", 1, 0, gens[0])
        one = st()["fx-gl-objects"]
        self.assertGreater(one, 0)
        marks = []
        for i in range(self.FADES):
            self.assertLess(time.monotonic(), deadline, "the bounded run of fades took too long (%d done)" % i)
            long_one = i % 3 == 1                               # this one is cut short by the next
            before = st()["fades"]
            self.r("pvj-crossfade", pics[i % 2], 30.0 if long_one else 0.03, "keep-open=no,image-display-duration=inf")
            self.r("pvj-shader", "load", 1, 0, gens[(i + 1) % 2])
            if not long_one:
                self.assertTrue(self.r.wait(lambda: st()["fade"] == "off", 15, 0.01), "fade %d: %s\n%s" % (i, st(), self.r.stderr()))
            self.assertEqual(st()["fades"], before + 1)
            if i in (self.FADES // 4 - 1, self.FADES // 2 - 1, self.FADES * 3 // 4 - 1, self.FADES - 1):
                # at rest: one fade in the next is ended by its successor, so end the last one by hand and look
                if long_one:
                    self.r("loadfile", pics[0], "replace")
                self.assertTrue(self.r.wait(lambda: st()["fade"] == "off" and st()["fx-gl-objects"] == one, 10), st())
                time.sleep(0.6)
                marks.append((fd_count(self.r.pid), thread_count(self.r.pid), st()["fx-gl-objects"], st()["gl-objects"]))
        self.assertEqual({m[2] for m in marks}, {one}, marks)
        for m in marks[1:]:
            self.assertEqual(m[0], marks[0][0], marks)
            self.assertLessEqual(abs(m[1] - marks[0][1]), 2, marks)
            self.assertLessEqual(abs(m[3] - marks[0][3]), 6, marks)       # libmpv's pools move by a few with fades (D98)
        self.assertIsNone(self.r.proc.poll())
        self.assertNotIn("Export failed", self.r.stderr())
        self.r("pvj-shader", "clear", 1, 0)
        self.assertEqual(st()["fx-gl-objects"], 0)
        print("\n%d fades with a generator on each side: fds, threads, program objects, libmpv objects at rest %s" % (self.FADES, marks))


# ---- D130, slice 2: pvj-fade-to-program, and clip to shader and shader to clip as fades --------------------------------

# a clip whose red moves (a sine over its frame number) and a generator whose green moves (a sine over TIME): while both
# are in a fade, the mix has both moving. No comma outside a function: the address is a lavfi graph.
MOVING = "av://lavfi:color=c=black:size=%dx%d:rate=30,format=gbrp,geq=r=140+110*sin(N/5):g=0:b=0,format=rgb0" % (W, H)
MOVER = "/*{}*/\nvoid main() { gl_FragColor = vec4(0.0, 0.5 + 0.45 * sin(TIME * 6.0), 0.0, 1.0); }\n"
BAD_PROGRAM = "//!PVJ 1\n//!KIND generator\n//!SIZE 320 180\n//!CODE\nvoid pvj_main() { not glsl }\n"
MOVES = ("wipe-from-left", "wipe-from-right", "wipe-from-top", "wipe-from-bottom",
         "slide-left", "slide-right", "slide-up", "slide-down")
KEEP = "keep-open=no,image-display-duration=inf"


class ProgramFadeBase(NativeBase):
    SECONDS = 8.0

    def st(self):
        return self.r("get_property", "pvj-render")

    def objects(self):
        return self.st()["fx-gl-objects"]

    def pic(self, name, colour):
        p = os.path.join(self.tmp, name + ".png")
        png(p, W, H, lambda x, y: colour)
        return p

    def moving_clip(self):
        self.r("loadfile", MOVING, "replace")
        self.assertTrue(self.r.wait(lambda: self.r.playing() is not None))
        time.sleep(0.3)

    def to_program(self, program, seconds=None, move=None):
        args = ["pvj-fade-to-program", program, self.SECONDS if seconds is None else seconds]
        return self.r(*(args + ([move] if move else [])))

    def to_clip(self, url, seconds=None, move=None):
        args = ["pvj-crossfade", url, self.SECONDS if seconds is None else seconds, KEEP]
        return self.r(*(args + ([move] if move else [])))

    def running(self, low=0.25, high=0.7, step=0.02):
        look = lambda: self.st()
        self.assertTrue(self.r.wait(lambda: look()["fade"] == "running" and low < look()["fade-progress"] < high, 15, step), look())

    def settled(self, seconds=None):
        self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", (seconds or self.SECONDS) + 10, 0.05), self.st())

    def samples(self, n=6, gap=0.12):
        """n pictures from inside a fade: (red, green, blue of the middle, the fade's progress before and after)."""
        out = []
        for _ in range(n):
            p0 = self.st()["fade-progress"]
            shot = self.r.capture()
            p1 = self.st()["fade-progress"]
            out.append((self.r.pixel(W // 2, H // 2, shot), p0, p1))
            time.sleep(gap)
        return out


class ProgramFadeTest(ProgramFadeBase):
    """D130, slice 2. pvj-fade-to-program builds a generator into the idle layer and fades to it, from a clip or from
    another generator; a pvj-crossfade out of a layer that holds a generator is a fade whether or not a clip plays under
    it. Both sides move through the fade; the outgoing layer's picture is freed at its end; a program the GPU refuses
    changes nothing on the screen."""

    def test_a_clip_fades_into_a_program_by_the_share_and_the_clip_layer_is_left_idle(self):
        self.r.load(self.pic("red", RED))
        self.assertEqual(self.objects(), 0)
        self.to_program(self.generator(solid(0, 0, 0.8)))
        self.running()
        p0 = self.st()["fade-progress"]
        shot = self.r.capture()
        p1 = self.st()["fade-progress"]
        r, g, b = self.r.pixel(W // 2, H // 2, shot)
        p = b / FULL                                    # blue goes from 0 to FULL as the share goes from 0 to 1
        self.assertTrue(p0 - 0.05 <= p <= p1 + 0.05, "the share by the blue %.3f, by the clock %.3f to %.3f: %s" % (p, p0, p1, (r, g, b)))
        self.assertLessEqual(abs(r - 255 * (1 - p)), 8, (r, g, b))
        self.assertLessEqual(g, 4, (r, g, b))
        alive = self.objects()
        self.assertGreater(alive, 0)                    # one program: the clip has none
        self.settled()
        self.assertEqual(self.objects(), alive)
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6 and px[1] < 6, px)
        st = self.st()
        self.assertEqual((st["fades"], st["fades-done"], st["fades-cut"], st["program-fades"], st["fades-from-nothing"]), (1, 1, 0, 1, 0))
        self.assertTrue(st["layer-generators"])
        self.assertEqual(self.slot(0)["state"], "on")

    def test_both_sides_move_while_a_clip_fades_into_a_program(self):
        self.moving_clip()
        self.to_program(self.generator(MOVER))
        self.running(0.1, 0.4)
        shots = self.samples()
        self.assertTrue(all(0.05 < s[1] and s[2] < 0.9 for s in shots), shots)
        reds, greens = [s[0][0] for s in shots], [s[0][1] for s in shots]
        self.assertGreater(max(reds) - min(reds), 20, "the clip stands still under the fade: %s" % reds)
        self.assertGreater(max(greens) - min(greens), 20, "the program stands still in the fade: %s" % greens)
        self.assertEqual(self.st()["fade"], "running")
        self.settled()

    def test_a_program_fades_out_to_a_moving_clip_with_no_clip_under_it_and_both_move(self):
        """The case slice 3 makes the usual one: no blank clip carries the shader, so the front layer's clip is idle and
        only its generator is the picture. The first program is a cut from black; the clip then fades in over it."""
        self.to_program(self.generator(MOVER), 1.0)
        st = self.st()
        self.assertEqual((st["fade"], st["fades"], st["fades-from-nothing"], st["program-fades"]), ("off", 0, 1, 1))
        self.assertTrue(self.r.wait(lambda: self.r.pixel(W // 2, H // 2)[1] > 20, 5), self.r.pixel(W // 2, H // 2))
        one = self.objects()
        self.to_clip(MOVING)
        self.running(0.1, 0.4)
        st = self.st()
        self.assertEqual((st["fades"], st["fades-from-nothing"]), (1, 1), "a fade, not a cut: %s" % st)
        shots = self.samples()
        reds, greens = [s[0][0] for s in shots], [s[0][1] for s in shots]
        self.assertGreater(max(reds) - min(reds), 20, "the clip stands still: %s" % reds)
        self.assertGreater(max(greens) - min(greens), 20, "the outgoing program stands still: %s" % greens)
        self.assertEqual(self.objects(), one)
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())
        self.assertEqual(self.slot(0)["state"], "off")
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[0] > 20 and px[1] < 6, "the clip alone: %s" % (px,))

    def test_a_program_fades_into_a_program_and_the_outgoing_one_is_freed(self):
        self.to_program(self.generator(solid(0.8, 0, 0)), 1.0)
        self.assertTrue(self.r.wait(lambda: self.r.pixel(10, 10)[0] > FULL - 8, 5), self.r.pixel(10, 10))
        one = self.objects()
        self.to_program(self.generator(solid(0, 0, 0.8)))
        self.running()
        shot = self.r.capture()
        r, g, b = self.r.pixel(W // 2, H // 2, shot)
        self.assertTrue(r > 20 and b > 20 and g < 6, (r, g, b))
        self.assertEqual(self.objects(), 2 * one, "two programs alive for the length of the fade")
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6, px)

    def test_every_wipe_and_slide_has_a_program_on_one_side_of_its_edge_both_ways(self):
        """Eight wipes and slides, a clip into a program and the program back out to the clip: the four sides of the picture
        are each the incoming or the outgoing picture whole, never a mix, and both are there."""
        red = self.pic("red", RED)
        self.r.load(red)
        prog = self.generator(solid(0, 0, 0.8))
        spots = [(12, H // 2), (W - 13, H // 2), (W // 2, 8), (W // 2, H - 9)]
        for move in MOVES:
            for into in (True, False):
                if into:
                    self.to_program(prog, 3.0, move)
                else:
                    self.to_clip(red, 3.0, move)
                self.running(0.25, 0.55)
                p0 = self.st()["fade-progress"]
                shot = self.r.capture()
                self.assertLess(self.st()["fade-progress"], 0.85, "%s: the picture was taken late" % move)
                look = [self.r.pixel(x, y, shot) for x, y in spots]
                pure = []
                for px in look:
                    clip = px[0] > 230 and px[1] < 20 and px[2] < 20
                    program = px[2] > FULL - 12 and px[0] < 20 and px[1] < 20
                    self.assertTrue(clip or program, "%s %s: a mixed pixel %s of %s (progress %.2f)" % (move, "in" if into else "out", px, look, p0))
                    pure.append(clip)
                self.assertTrue(any(pure) and not all(pure), "%s: one picture only: %s" % (move, look))
                self.settled(3.0)
                self.assertEqual(self.st()["fades-cut"], 0)
                if not into:
                    self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())
        st = self.st()
        self.assertEqual((st["fades"], st["fades-done"], st["program-fades"]), (16, 16, 8))

    def test_a_refused_program_changes_nothing_and_a_refused_one_in_a_fade_ends_that_fade_on_its_incoming_side(self):
        self.r.load(self.pic("red", RED))
        with self.assertRaises(PlayerError) as e:
            self.to_program(self.write(BAD_PROGRAM))
        self.assertNotIn("success", str(e.exception))
        st = self.st()
        self.assertEqual((st["fade"], st["fades"], st["program-fades"], st["front"]), ("off", 0, 0, 0))
        self.assertEqual(self.objects(), 0)
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[0] > 240 and px[2] < 6, "the clip is still the picture: %s" % (px,))
        # a good fade, then a refused one in the middle of it: the running fade ends at once on its incoming side
        self.to_program(self.generator(solid(0, 0, 0.8)))
        self.running()
        one = self.objects()
        with self.assertRaises(PlayerError):
            self.to_program(self.write(BAD_PROGRAM))
        st = self.st()
        self.assertEqual((st["fade"], st["fades-cut"], st["program-fades"]), ("off", 1, 1), st)
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6, "the incoming program is the picture, not black: %s" % (px,))
        self.assertEqual(self.slot(0)["state"], "on")
        # and the renderer goes on taking fades
        self.to_program(self.generator(solid(0, 0.8, 0)), 0.2)
        self.settled(1)
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[1] > FULL - 8 and px[2] < 6, px)
        self.assertEqual(self.st()["program-fades"], 2)
        self.assertIsNone(self.r.proc.poll())

    def test_what_is_not_a_program_to_fade_to_is_an_invalid_parameter_and_changes_nothing(self):
        self.r.load(self.pic("red", RED))
        good = self.generator(solid(0, 0, 0.8))
        for args in ([os.path.basename(good), 1.0], ["/does/not/exist.prog", 1.0], [good, -1], [good, 61], [good, 1.0, "wobble"],
                     [self.effect(INVERT), 1.0], [5, 1.0], [good, "x"]):
            with self.assertRaises(PlayerError) as e:
                self.r("pvj-fade-to-program", *args)
            self.assertIn("invalid parameter", str(e.exception), args)
        st = self.st()
        self.assertEqual((st["fade"], st["fades"], st["program-fades"]), ("off", 0, 0))
        self.assertEqual(self.objects(), 0)

    def test_a_tap_in_the_middle_of_a_fade_finishes_it_and_fades_to_the_new_picture(self):
        self.r.load(self.pic("red", RED))
        self.to_program(self.generator(solid(0, 0, 0.8)))
        self.running()
        one = self.objects()
        self.to_program(self.generator(solid(0, 0.8, 0)), 3.0)
        self.assertEqual(self.st()["fades-cut"], 1)
        shot = self.r.capture()
        r, g, b = self.r.pixel(W // 2, H // 2, shot)
        self.assertTrue(b > 100 and r < 20, "the first program is the outgoing picture of the new fade: %s" % ((r, g, b),))
        self.assertEqual(self.objects(), 2 * one)
        self.settled(3.0)
        self.assertTrue(self.r.wait(lambda: self.objects() == one, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[1] > FULL - 8 and px[2] < 6, px)

    def test_a_clip_asked_in_the_middle_of_a_fade_into_a_program_fades_out_of_that_program(self):
        red, green = self.pic("red", RED), self.pic("green", GREEN)
        self.r.load(red)
        self.to_program(self.generator(solid(0, 0, 0.8)))
        self.running()
        one = self.objects()
        self.to_clip(green, 3.0)
        self.running(0.2, 0.7, 0.01)
        st = self.st()
        self.assertEqual((st["fades"], st["fades-cut"], st["fades-from-nothing"]), (2, 1, 0), "a fade out of the program, not a cut: %s" % st)
        r, g, b = self.r.pixel(W // 2, H // 2)
        self.assertTrue(b > 20 and g > 20 and r < 20, ((r, g, b), "blue program and green clip"))
        self.assertEqual(self.objects(), one)
        self.settled(3.0)
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[1] > 240 and px[2] < 6, px)

    def test_a_value_set_to_slot_0_during_a_program_out_to_a_clip_is_refused_and_the_fade_is_left_alone(self):
        self.to_program(self.generator(LEVEL), 1.0)
        self.to_clip(self.pic("green", GREEN))
        self.running(0.1, 0.8)
        one = self.objects()
        self.assertGreater(one, 0)
        with self.assertRaises(PlayerError) as e:
            self.r("pvj-shader", "set", 1, 0, {"level": 0.9})
        self.assertIn("invalid parameter", str(e.exception))
        self.assertEqual(self.st()["fade"], "running")
        self.assertEqual(self.objects(), one)
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())

    def test_a_program_asked_with_nothing_on_the_screen_is_a_cut_from_black(self):
        self.to_program(self.generator(solid(0, 0.8, 0)), 2.0)
        st = self.st()
        self.assertEqual((st["fade"], st["fades"], st["fades-from-nothing"], st["program-fades"]), ("off", 0, 1, 1))
        self.assertTrue(self.r.wait(lambda: self.r.pixel(W // 2, H // 2)[1] > FULL - 8, 5), self.r.pixel(W // 2, H // 2))
        self.assertEqual(self.slot(0)["state"], "on")

    def test_the_fade_waits_for_the_programs_first_picture_and_the_outgoing_clip_goes_on_under_it(self):
        """The old picture keeps moving until the new one is drawn, and the fade's clock starts only then: a fade of 6 s
        asked as the property says is at its start (not 1 s in) when the answer is hardly home."""
        self.moving_clip()
        t0 = time.monotonic()
        self.to_program(self.generator(solid(0, 0, 0.8)), 6.0)
        took = time.monotonic() - t0
        st = self.st()
        elapsed = time.monotonic() - t0
        self.assertLessEqual(st["fade-progress"] * 6.0, elapsed - 0.0 + 0.05, (st["fade-progress"], elapsed))
        self.assertLess(took, 3.0)
        self.settled(6.0)


class ProgramFadeSlowBuildTest(ProgramFadeBase):
    """With a slow compiler (a test hook, make TEST_HOOKS=1): the outgoing picture plays on while the program builds,
    the fade starts after it, and a build that outlasts the renderer's wait is answered so and leaves nothing behind."""

    def start(self, ms):
        need_program(self)
        if "with test hooks" not in subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without its test hooks: make -C render TEST_HOOKS=1")
            self.skipTest("pvj-render was built without its test hooks")
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(env={"PVJ_RENDER_TEST_SLOW_BUILD": str(ms)})
        self.addCleanup(lambda: self.r.close())
        self.r("set_property", "image-display-duration", "inf")
        self.n = 0

    def setUp(self):
        pass

    def test_the_outgoing_clip_moves_while_the_program_builds_and_the_clock_starts_after(self):
        self.start(900)
        self.moving_clip()
        said = {}

        def ask():
            said["t0"] = time.monotonic()
            Ipc(self.r.sock, timeout=12.0).request("pvj-fade-to-program", self.generator(solid(0, 0, 0.8)), 8.0, wait=6.0)
            said["t1"] = time.monotonic()
        th = threading.Thread(target=ask)
        th.start()
        time.sleep(0.1)
        self.assertEqual(self.st()["fade"], "off", "the fade must not begin before the program is built")
        looks = []
        while th.is_alive() and len(looks) < 12:
            try:
                looks.append((self.r.pixel(W // 2, H // 2), self.st()["fade"]))
            except PlayerError:
                pass
            time.sleep(0.05)
        th.join(15)
        self.assertFalse(th.is_alive())
        self.assertGreaterEqual(said["t1"] - said["t0"], 0.8)
        self.assertTrue(looks, "no look at the screen while it built")
        for px, fade in looks[:-1]:
            self.assertTrue(px[2] < 6 and px[0] > 20, "the outgoing clip alone while the program builds: %s" % (px,))
        elapsed = time.monotonic() - said["t1"]
        st = self.st()
        self.assertLessEqual(st["fade-progress"] * 8.0, elapsed + 0.15, "the clock began before the program was drawn: %s" % st)
        self.running(0.05, 0.5)
        self.settled()

    def test_a_build_that_outlasts_the_wait_is_answered_so_and_the_program_never_appears(self):
        self.start(3500)
        self.moving_clip()
        with self.assertRaises(PlayerError) as e:
            self.to_program(self.generator(solid(0, 0, 0.8)))
        self.assertIn("in time", str(e.exception))
        # the build finishes after the answer; the program it made is cleared, and the screen never changed
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 10), self.objects())
        st = self.st()
        self.assertEqual((st["fade"], st["fades"], st["program-fades"]), ("off", 0, 0))
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] < 6 and px[0] > 20, "the clip is still the picture: %s" % (px,))


class ManyProgramFadesTest(ProgramFadeBase):
    """A show fades between clips and shaders all night: fades into and out of programs, some cut short by the next, leave
    no program, texture, descriptor or thread behind. Bounded by a deadline."""

    FADES = int(os.environ.get("PVJ_PROGRAM_FADES", "120"))
    LIMIT = max(420.0, FADES * 0.8)

    def test_fades_into_and_out_of_programs_leave_nothing_behind(self):
        deadline = time.monotonic() + self.LIMIT
        clips = [self.pic("a", RED), self.pic("b", GREEN)]
        gens = [self.generator(solid(0.8, 0, 0)), self.generator(solid(0, 0, 0.8))]
        st = lambda: self.r("get_property", "pvj-render")
        self.r.load(clips[0])
        marks = []
        one = None
        for i in range(self.FADES):
            self.assertLess(time.monotonic(), deadline, "the bounded run of fades took too long (%d done)" % i)
            step = i % 4
            before = st()["fades"]
            if step == 0:
                self.to_program(gens[(i // 4) % 2], 0.03)
            elif step == 1:
                self.to_clip(clips[(i // 4) % 2], 0.03)
            elif step == 2:
                self.to_program(gens[0], 30.0)              # cut short by the next
            else:
                self.to_program(gens[1], 0.03)              # ... which is a program fading out of a program
            self.assertEqual(st()["fades"], before + 1, "fade %d" % i)
            if step != 2:
                self.assertTrue(self.r.wait(lambda: st()["fade"] == "off", 15, 0.01), "fade %d: %s\n%s" % (i, st(), self.r.stderr()))
            if step == 3 and one is None:
                one = st()["fx-gl-objects"]
                self.assertGreater(one, 0)
            if step == 1 and i // 4 in (self.FADES // 16, self.FADES // 8, self.FADES * 3 // 16, self.FADES // 4 - 1):
                self.assertTrue(self.r.wait(lambda: st()["fade"] == "off" and st()["fx-gl-objects"] == 0, 10), st())
                time.sleep(0.6)
                marks.append((fd_count(self.r.pid), thread_count(self.r.pid), st()["fx-gl-objects"], st()["gl-objects"]))
        self.assertGreaterEqual(len(marks), 3, marks)
        self.assertEqual({m[2] for m in marks}, {0}, marks)
        for m in marks[1:]:
            self.assertEqual(m[0], marks[0][0], marks)
            self.assertLessEqual(abs(m[1] - marks[0][1]), 2, marks)
            self.assertLessEqual(abs(m[3] - marks[0][3]), 6, marks)
        self.assertIsNone(self.r.proc.poll())
        self.assertNotIn("Export failed", self.r.stderr())
        print("\n%d fades into and out of programs: fds, threads, program objects, libmpv objects at rest %s" % (self.FADES, marks))


class PlayerProgramFadeTest(ProgramFadeBase):
    """The panel's player (pvj/player.py) over the real renderer: fade_to_program, and play() out of a program. Not asked by
    the panel before its slice 3, so these are the only callers."""

    def setUp(self):
        super().setUp()
        d = tempfile.mkdtemp(prefix="pvj-native-run-")
        self.addCleanup(shutil.rmtree, d, True)
        os.symlink(self.r.sock, os.path.join(d, paths.PLAYER_SOCKET))
        self.player = Player(rundir=d)

    def test_a_clip_into_a_program_and_the_program_out_to_a_clip(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        self.assertTrue(p.fades_native())
        self.assertTrue(p.layer_generators())
        prog = self.generator(solid(0, 0, 0.8))
        before = p.source_epoch
        epoch = p.fade_to_program(prog, 3.0)
        self.assertEqual(epoch, before + 1)
        self.assertEqual((p.source_shader, p._carrier), (prog, None))
        self.assertFalse(p.fades_from_clip())
        self.assertEqual(p.native_verdict(prog), ("ok", ""))
        self.running(0.1, 0.8)
        self.settled(3.0)
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6, px)
        # out of the program to a clip: a fade, the program is the renderer's to free and this side lets go of it
        green = self.pic("green", GREEN)
        epoch = p.source_epoch
        self.assertIsNone(p.play([green], fade=3.0))
        self.assertEqual(p.fade_refused, "")
        self.assertIsNone(p.source_shader)
        self.assertGreater(p.source_epoch, epoch)
        st = self.st()
        self.assertEqual((st["fades"], st["fades-from-nothing"]), (2, 0), st)
        self.running(0.1, 0.9, 0.01)
        r, g, b = self.r.pixel(W // 2, H // 2)
        self.assertTrue(b > 10 and g > 10, "both the program and the clip are in it: %s" % ((r, g, b),))
        self.settled(3.0)
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 5), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[1] > 240 and px[2] < 6, px)

    def test_a_wipe_style_reaches_the_renderer(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        p.fade_to_program(self.generator(solid(0, 0, 0.8)), 3.0, "wipe-from-left")
        self.assertEqual(self.st()["fade-kind"], "wipe-from-left")
        self.settled(3.0)

    def test_a_refused_program_changes_nothing_here_either(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        bad = self.write(BAD_PROGRAM)
        epoch = p.source_epoch
        with self.assertRaises(PlayerError):
            p.fade_to_program(bad, 2.0)
        self.assertEqual(p.native_verdict(bad)[0], "refused")
        self.assertEqual((p.source_epoch, p.source_shader), (epoch, None))
        self.assertTrue(p.fades_from_clip())
        self.assertEqual(self.st()["program-fades"], 0)

    def test_an_epoch_that_has_gone_by_asks_the_renderer_nothing(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        self.assertIsNone(p.fade_to_program(self.generator(solid(0, 0, 0.8)), 1.0, epoch=p.source_epoch - 1))
        self.assertEqual(self.st()["program-fades"], 0)

    def test_a_renderer_without_the_command_is_not_asked_for_it(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        p.crossfades()
        p._gens_seen = (p._seen_pid, False)                 # a renderer of slice 1: the property says layer-generators only
        self.assertFalse(p.fades_native())
        with self.assertRaises(PlayerError):
            p.fade_to_program(self.generator(solid(0, 0, 0.8)), 1.0)
        self.assertEqual(self.st()["program-fades"], 0)

    def test_a_live_inputs_pipe_keeps_the_stills_hand_over(self):
        p = self.player
        p.crossfades()
        self.assertTrue(p.fades_native())
        p._pipe = True
        self.assertFalse(p.fades_native())
        p._pipe = False


ENDED = "the layer was ended before the load took effect"


class ProgramBuildDoesNotHoldTheLayersTest(ProgramFadeBase):
    """The review of #240: pvj-fade-to-program used to hold layer_lock across the build (up to 3 s), so a Stop, a load, a
    level or a crossfade from the panel waited behind it past the player's 2 s timeout and raised there while still running
    in the renderer. Now the lock is let go while the program builds; a command that ends a fade ends the build too, and the
    fade command answers that the layer was ended."""

    def start(self, ms):
        need_program(self)
        if "with test hooks" not in subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and pvj-render was built without its test hooks: make -C render TEST_HOOKS=1")
            self.skipTest("pvj-render was built without its test hooks")
        self.tmp = tempfile.mkdtemp(prefix="pvj-native-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(env={"PVJ_RENDER_TEST_SLOW_BUILD": str(ms)})
        self.addCleanup(lambda: self.r.close())
        self.r("set_property", "image-display-duration", "inf")
        self.n = 0

    def setUp(self):
        pass

    def ask_in_background(self, program, seconds=4.0):
        said = {}

        def ask():
            t0 = time.monotonic()
            try:
                Ipc(self.r.sock, timeout=12.0).request("pvj-fade-to-program", program, seconds, wait=8.0)
                said["answer"] = "success"
            except PlayerError as e:
                said["answer"] = str(e)
            said["seconds"] = time.monotonic() - t0
        th = threading.Thread(target=ask)
        th.start()
        time.sleep(0.3)                     # the build is under way (the hook makes it 2.5 s)
        return th, said

    def test_a_stop_from_a_second_client_is_answered_at_once_and_ends_the_build(self):
        self.start(2500)
        self.moving_clip()
        th, said = self.ask_in_background(self.generator(solid(0, 0, 0.8)))
        t0 = time.monotonic()
        Ipc(self.r.sock, timeout=2.0).request("stop")      # the player's own timeout: it must not be reached
        took = time.monotonic() - t0
        self.assertLess(took, 1.0, "the Stop waited %.2f s behind the build" % took)
        th.join(15)
        self.assertFalse(th.is_alive())
        self.assertIn(ENDED, said["answer"])
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 10), self.objects())
        st = self.st()
        self.assertEqual((st["fade"], st["fades"]), ("off", 0), st)
        self.assertEqual(self.r("get_property", "pvj-shaders")[0]["state"], "off")
        self.assertIsNone(self.r.proc.poll())

    def test_a_load_a_level_and_a_crossfade_are_served_while_the_program_builds(self):
        self.start(2500)
        self.moving_clip()
        red = self.pic("red", RED)
        th, said = self.ask_in_background(self.generator(solid(0, 0, 0.8)))
        ipc = Ipc(self.r.sock, timeout=2.0)
        t0 = time.monotonic()
        ipc.request("set_property", "brightness", -10)       # a level does not end the build
        ipc.request("get_property", "pvj-render")
        self.assertLess(time.monotonic() - t0, 1.0)
        t0 = time.monotonic()
        ipc.request("pvj-crossfade", red, 0.2, KEEP)         # a transition does: the program is asked off
        self.assertLess(time.monotonic() - t0, 1.0)
        th.join(15)
        self.assertIn(ENDED, said["answer"])
        self.settled(2.0)
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 10), self.objects())
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[0] > 200 and px[2] < 20, "the clip asked meanwhile is the picture, not the late program: %s" % (px,))

    def test_a_level_alone_leaves_the_build_to_finish_into_its_fade(self):
        self.start(1200)
        self.moving_clip()
        th, said = self.ask_in_background(self.generator(solid(0, 0, 0.8)), 3.0)
        Ipc(self.r.sock, timeout=2.0).request("set_property", "brightness", -5)
        th.join(15)
        self.assertEqual(said["answer"], "success", said)
        self.assertEqual(self.st()["program-fades"], 1)
        self.running(0.0, 0.9)
        self.settled(3.0)
        px = self.r.pixel(W // 2, H // 2)
        self.assertGreater(px[2], 100, px)

    def test_a_fade_asked_after_the_cut_build_is_not_mistaken_for_it(self):
        self.start(900)
        self.moving_clip()
        prog = self.generator(solid(0, 0, 0.8))
        th, said = self.ask_in_background(prog, 0.2)
        Ipc(self.r.sock, timeout=2.0).request("stop")
        th.join(15)
        self.assertIn(ENDED, said["answer"])
        self.moving_clip()
        self.to_program(prog, 0.2)                          # asked again: the first one's end does not spoil this one
        self.settled(1.0)
        self.assertEqual(self.st()["program-fades"], 1)
        px = self.r.pixel(W // 2, H // 2)
        self.assertGreater(px[2], 150, px)


class TwoClientStressTest(ProgramFadeBase):
    """The reviewer's stress of #240 (review240/stress.py), bounded: two clients mix fades into programs, crossfades, loads,
    levels, sets, clears and stops. The renderer must live, every answer must be success or a refusal in the renderer's words,
    no program or object may be left after a stop and a clear, and descriptors and threads must be flat."""

    SECONDS = float(os.environ.get("PVJ_STRESS_SECONDS", "10"))

    def test_the_back_to_back_fade_the_instant_the_last_one_ends_is_never_refused(self):
        red = self.pic("red", RED)
        gens = [self.generator(solid(0, 0, 0.8)), self.generator(solid(0, 0.8, 0))]
        self.r.load(red)
        refused = []
        for i in range(int(os.environ.get("PVJ_ENDED_TRIES", "25"))):
            self.to_program(gens[i % 2], 0.03)
            self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", 20, 0.002), self.st())
            try:
                self.to_program(gens[(i + 1) % 2], 0.03)
            except PlayerError as e:
                refused.append(str(e))
            self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", 20, 0.002), self.st())
        self.assertEqual(refused, [])
        self.assertIsNone(self.r.proc.poll())

    def test_two_clients_mix_everything_and_nothing_is_left_behind(self):
        import random
        red, blue, green = self.pic("red", RED), self.pic("blue", BLUE), self.pic("green", GREEN)
        gens = [self.generator(solid(0.8, 0, 0)), self.generator(solid(0, 0, 0.8)), self.generator(LEVEL)]
        bad = self.write(BAD_PROGRAM)
        self.r.load(red)
        start = (fd_count(self.r.pid), thread_count(self.r.pid))
        errors, slow, counts = [], [], {"a": 0, "b": 0}
        stop_at = time.monotonic() + self.SECONDS
        allowed = ("invalid parameter", "GPU refused", ENDED, "did not build")

        def client(name, ops):
            ipc = Ipc(self.r.sock, timeout=12.0)
            rnd = random.Random(name)
            while time.monotonic() < stop_at:
                op = rnd.choice(ops)
                t0 = time.monotonic()
                try:
                    op(ipc, rnd)
                    counts[name] += 1
                except PlayerError as e:
                    if not any(w in str(e) for w in allowed):
                        errors.append("%s %s: %s" % (name, op.__name__, e))
                except Exception as e:                      # noqa: BLE001
                    errors.append("%s %s: %r" % (name, op.__name__, e))
                    return
                if time.monotonic() - t0 > 1.5:
                    slow.append((name, op.__name__, round(time.monotonic() - t0, 2)))
                time.sleep(rnd.uniform(0, 0.02))

        def a_fade_prog(ipc, rnd):
            args = ["pvj-fade-to-program", rnd.choice(gens + [bad]), rnd.choice([0.03, 0.1, 0.5, 2.0])]
            if rnd.random() < 0.4:
                args.append(rnd.choice(["wipe-from-left", "slide-from-top", "wipe-from-bottom"]))
            ipc.request(*args, wait=8.0)

        def a_load(ipc, rnd):
            ipc.request("pvj-shader", "load", 1, 0, rnd.choice(gens + [bad]), wait=8.0)

        def a_set(ipc, rnd):
            ipc.request("pvj-shader", "set", 1, 0, {"level": rnd.random()})

        def a_clear(ipc, rnd):
            ipc.request("pvj-shader", "clear", 1, 0, wait=8.0)

        def b_fade(ipc, rnd):
            args = ["pvj-crossfade", rnd.choice([red, blue, green]), rnd.choice([0.03, 0.1, 0.5, 2.0]), KEEP]
            if rnd.random() < 0.4:
                args.append(rnd.choice(["wipe-from-left", "slide-from-top"]))
            ipc.request(*args)

        def b_fade_prog(ipc, rnd):
            ipc.request("pvj-fade-to-program", rnd.choice(gens), rnd.choice([0.03, 0.3, 1.0]), wait=8.0)

        def b_load(ipc, rnd):
            ipc.request("loadfile", rnd.choice([red, blue, green]), "replace")

        def b_stop(ipc, rnd):
            ipc.request("stop")

        def b_set(ipc, rnd):
            ipc.request("set_property", "brightness", rnd.choice([0, -20, 10]))

        def b_look(ipc, rnd):
            ipc.request("get_property", "pvj-shaders")
            ipc.request("get_property", "pvj-render")

        ta = threading.Thread(target=client, args=("a", [a_fade_prog, a_fade_prog, a_fade_prog, a_load, a_set, a_set, a_clear]))
        tb = threading.Thread(target=client, args=("b", [b_fade, b_fade, b_fade_prog, b_load, b_stop, b_set, b_look]))
        ta.start()
        tb.start()
        ta.join(self.SECONDS + 60)
        tb.join(self.SECONDS + 60)
        self.assertFalse(ta.is_alive() or tb.is_alive(), "a client thread did not end")
        self.assertIsNone(self.r.proc.poll(), "the renderer died: " + self.r.stderr())
        self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", 20), self.st())
        self.assertEqual(errors, [], errors[:10])
        self.assertEqual(slow, [], "answers that took over 1.5 s (a lock held across a build): %s" % slow[:8])
        self.assertGreater(counts["a"] + counts["b"], 20)
        self.r("stop")
        self.r("pvj-shader", "clear", 1, 0)
        self.assertTrue(self.r.wait(lambda: self.objects() == 0, 10), "programs left alive after stop and clear: %d" % self.objects())
        time.sleep(0.5)
        self.assertLessEqual(fd_count(self.r.pid), start[0])
        self.assertLessEqual(thread_count(self.r.pid), start[1] + 1)
        self.assertNotIn("Export failed", self.r.stderr())


class PlayerShaderLayerTest(ProgramFadeBase):
    """D130, slice 3, the player (pvj/player.py): a shader on the New renderer is the front layer's picture with no blank
    clip under it, the stand-in name keeps the panel's checks working, the lists are touched, and a fade whose answer is lost
    is reconciled with what the renderer holds."""

    def setUp(self):
        super().setUp()
        d = tempfile.mkdtemp(prefix="pvj-native-run-")
        self.addCleanup(shutil.rmtree, d, True)
        os.symlink(self.r.sock, os.path.join(d, paths.PLAYER_SOCKET))
        self.player = Player(rundir=d)
        self.carrier_name = S.carrier_url((W, H), False)

    def test_a_cut_into_a_shader_loads_no_carrier_and_the_clip_is_stopped(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        prog = self.generator(solid(0, 0, 0.8))
        epoch = p.play_source(prog, self.carrier_name)
        self.assertIsNotNone(epoch)
        self.assertEqual((p.source_shader, p._carrier), (prog, None))
        self.assertTrue(self.r.wait(lambda: self.r.playing() is None, 5), "the clip under the shader was left playing")
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[2] > FULL - 8 and px[0] < 6, px)
        self.assertEqual(p.status()["path"], self.carrier_name)       # the stand-in for the panel's "is a shader playing" checks
        self.assertEqual(p.path_now(), self.carrier_name)
        self.assertTrue(p.clear_source(epoch))
        self.assertTrue(self.r.wait(lambda: self.slot(0)["state"] == "off", 5))

    def test_the_stand_in_goes_with_the_shader(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        p.fade_to_program(self.generator(solid(0, 0, 0.8)), 0.2, standin=self.carrier_name)
        self.settled(0.2)
        self.assertEqual(p.path_now(), self.carrier_name)
        green = self.pic("green", GREEN)
        p.play([green], fade=0.2)
        self.settled(0.2)
        self.assertIsNone(p.source_shader)
        self.assertEqual(p.status()["path"], green)

    def test_a_fade_touches_the_lists_as_a_crossfade_does(self):
        p = self.player
        touched = []

        class Lists:
            def any(self):
                return True

            def touch(self, path):
                touched.append(path)
        p.lists = Lists()
        clip = self.pic("red", RED)
        self.r.load(clip)
        p.fade_to_program(self.generator(solid(0, 0, 0.8)), 0.2)
        self.assertEqual(touched, [clip])

    def test_a_lost_answer_is_reconciled_with_what_the_renderer_holds(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        prog = self.generator(solid(0, 0, 0.8))
        real = p.ipc.request

        def lost(*command, **kw):
            out = real(*command, **kw)
            if command and command[0] == "pvj-fade-to-program":
                raise PlayerError("no answer from the player")
            return out
        p.ipc.request = lost
        epoch = p.fade_to_program(prog, 0.3, standin=self.carrier_name)
        p.ipc.request = real
        self.assertIsNotNone(epoch)
        self.assertEqual((p.source_shader, p._native_loaded[0][0]), (prog, prog))     # the renderer took it: this side knows

    def test_a_fade_that_never_reached_the_renderer_leaves_its_record_to_be_reloaded(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        first = self.generator(solid(0.8, 0, 0))
        p.fade_to_program(first, 0.1)
        self.settled(0.1)
        self.assertEqual(p._native_loaded[0][0], first)
        real = p.ipc.request

        def lost(*command, **kw):
            if command and command[0] == "pvj-fade-to-program":
                raise PlayerError("no answer from the player")
            return real(*command, **kw)
        p.ipc.request = lost
        with self.assertRaises(PlayerError):
            p.fade_to_program(self.generator(solid(0, 0, 0.8)), 0.3)
        p.ipc.request = real
        self.assertEqual(p.source_shader, first)
        self.assertIsNone(p._native_loaded[0])                       # unknown: the next push loads this side's source again

    def test_a_value_refused_during_a_shader_to_clip_fade_is_not_an_error(self):
        p = self.player
        self.r.load(self.pic("red", RED))
        prog = self.generator(LEVEL)
        p.fade_to_program(prog, 0.2)
        self.settled(0.2)
        epoch = p.source_epoch
        self.assertTrue(p.native_values(0, {"level": 0.5}, epoch=epoch))
        p.play([self.pic("green", GREEN)], fade=4.0)
        # this side lets go of the program at the play; the value worker's epoch is gone and it says False, not an error
        self.assertFalse(p.native_values(0, {"level": 0.9}, epoch=epoch))
        # and if a set does reach the renderer while the outgoing generator draws, the renderer refuses it: not raised
        p._source, p._source_pid, p._native_loaded[0] = prog, p._seen_pid, (prog, p._seen_pid)
        self.assertFalse(p.native_values(0, {"level": 0.9}, epoch=p.source_epoch))


class EngineFadeTest(NativeBase):
    """D130, slice 3, the panel's engine on the real renderer: a shader chosen by hand or by a pad takes the Mix transition
    into and out of the shader, with no carrier, and a refusal says why without changing the screen."""

    def setUp(self):
        EngineTest.setUp(self)
        self.red = os.path.join(self.tmp, "red.png")
        png(self.red, W, H, lambda x, y: RED)
        self.green = os.path.join(self.tmp, "green.png")
        png(self.green, W, H, lambda x, y: GREEN)
        for n, p in (("red.png", self.red), ("green.png", self.green)):
            shutil.copy(p, os.path.join(self.api.media_dir, n))
        self.T = __import__("pvj.transitions", fromlist=["stored"])
        self.api.settings.data["mix"] = self.T.stored("crossfade", 3.0)

    def st(self):
        return self.r("get_property", "pvj-render")

    def settled(self):
        self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "off", 15, 0.05), self.st())

    def test_a_clip_to_a_shader_to_a_shader_to_a_clip_with_the_chosen_mix(self):
        self.api.play({"file": "red.png"}, None, "t")
        self.assertTrue(self.r.wait(lambda: self.r.playing() is not None, 5))
        out = self.api.shaders.play("nxlx-bloom.fs")
        self.assertTrue(out["ok"], out)
        st = self.st()
        self.assertEqual((st["program-fades"], st["fades"]), (1, 1), st)
        self.assertIn(st["fade"], ("waiting", "running"))
        self.assertEqual(self.player._carrier, None)
        self.assertEqual(self.api.shaders.on_screen()["id"], "nxlx-bloom.fs")
        self.assertEqual(self.player.status()["path"], self.api.shaders.playing["carrier"])
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.r.playing() is None, 5), "the clip is still decoding under the shader")
        # shader to shader
        out = self.api.shaders.play("nxlx-aurora.fs")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.st()["program-fades"], 2)
        self.assertEqual(self.api.shaders.on_screen()["id"], "nxlx-aurora.fs")
        self.settled()
        self.assertEqual(self.st()["fades-cut"], 0)
        # shader to clip
        self.api.play({"file": "green.png"}, None, "t")
        self.assertEqual(self.st()["fades"], 3)
        self.assertIsNone(self.player.source_shader)
        self.assertEqual(self.player.fade_refused, "")
        self.settled()
        self.assertTrue(self.r.wait(lambda: self.slot(0)["state"] == "off", 5))
        px = self.r.pixel(W // 2, H // 2)
        self.assertTrue(px[1] > 240 and px[0] < 6, px)

    def test_a_wipe_is_the_chosen_style_and_a_cut_stays_a_cut(self):
        self.api.settings.data["mix"] = self.T.stored("wipe-from-left", 3.0)
        self.api.play({"file": "red.png"}, None, "t")
        self.api.shaders.play("nxlx-bloom.fs")
        self.assertEqual(self.st()["fade-kind"], "wipe-from-left")
        self.settled()
        self.api.settings.data["mix"] = self.T.stored("cut", 1.0)
        fades = self.st()["fades"]
        self.api.shaders.play("nxlx-aurora.fs")
        self.assertEqual(self.st()["fades"], fades)               # a cut: no fade
        self.assertEqual(self.api.shaders.on_screen()["id"], "nxlx-aurora.fs")

    def test_the_same_shader_shown_again_is_exchanged_in_place(self):
        self.api.play({"file": "red.png"}, None, "t")
        self.api.shaders.play("nxlx-bloom.fs")
        self.settled()
        fades = self.st()["fades"]
        self.api.shaders.play("nxlx-bloom.fs", values={})
        self.assertEqual(self.st()["fades"], fades)

    def test_a_tap_in_the_middle_of_a_fade_finishes_it_and_fades_to_the_new_pad(self):
        self.api.settings.data["mix"] = self.T.stored("crossfade", 5.0)
        self.api.play({"file": "red.png"}, None, "t")
        self.api.shaders.play("nxlx-bloom.fs")
        self.assertTrue(self.r.wait(lambda: self.st()["fade"] == "running" and self.st()["fade-progress"] > 0.1, 10, 0.02), self.st())
        self.api.shaders.play("nxlx-aurora.fs")
        st = self.st()
        self.assertEqual((st["fades"], st["fades-cut"], st["program-fades"]), (2, 1, 2), st)
        self.assertEqual(self.api.shaders.on_screen()["id"], "nxlx-aurora.fs")

    def test_a_shader_the_gpu_refuses_leaves_the_screen_and_says_why(self):
        from pvj.api import ApiError
        self.api.play({"file": "red.png"}, None, "t")
        self.api.shaders.play("nxlx-bloom.fs")
        self.settled()
        broken = "/*{}*/\nvoid main() { gl_FragColor = vec4(nonsense, 1.0); }\n"
        self.api.shaders.upload("broken.fs", broken)
        with self.assertRaises(ApiError) as e:
            self.api.shaders.play("broken.fs")
        self.assertEqual(e.exception.status, 422)
        self.assertIn("nonsense", e.exception.message)
        self.assertIn("The shader before it stays on", e.exception.message)
        self.assertEqual(self.api.shaders.on_screen()["id"], "nxlx-bloom.fs")
        self.assertEqual(self.slot(0)["state"], "on")
        self.assertEqual(self.st()["fades"], 1)
        # from a clip: the clip stays
        self.player.clear()
        self.api.play({"file": "red.png"}, None, "t")
        with self.assertRaises(ApiError) as e:
            self.api.shaders.play("broken.fs")
        self.assertIn("The picture before it stays on", e.exception.message)
        self.assertTrue(self.r.pixel(W // 2, H // 2)[0] > 240)

    def test_a_node_that_cuts_says_so_on_the_mix_card_and_cuts(self):
        self.api.play({"file": "red.png"}, None, "t")
        self.assertIsNone(self.api._mix_settings().get("shader_cut"))
        self.player._gens_seen = (self.player._seen_pid, False)     # a renderer from before layer generators, or classic
        self.assertIn("cut", self.api._mix_settings()["shader_cut"])
        self.assertIsNone(self.api._shader_fade())


if __name__ == "__main__":
    unittest.main()
