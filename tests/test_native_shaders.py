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
import time
import unittest

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import effects as E, paths, shaders as S
from pvj.player import Player, PlayerError, is_program
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
        a, t = self.flips(), time.monotonic()
        time.sleep(3.0)
        quiet = (self.flips() - a) / (time.monotonic() - t)
        a, t, n = self.flips(), time.monotonic(), 0
        while time.monotonic() - t < 3.0:
            for ext in ("jpg", "png"):
                path = os.path.join(self.tmp, "shot.%s" % ext)
                open(path, "w").close()
                self.r("screenshot-to-file", path, "window")
                n += 1
        busy = (self.flips() - a) / (time.monotonic() - t)
        print("\n  flips a second at 1920 x 1080: %.1f quiet, %.1f during %d screenshots" % (quiet, busy, n))
        self.assertGreater(n, 4)
        # Encoding a 1080p JPEG took 139 to 273 ms on this kind of machine: on the render thread that was 8 to 16
        # vblanks without a picture each, and the rate fell to a quarter of the quiet one (15.4 of 60 here). With
        # only the readback there it stays near the quiet rate. Judged against the quiet rate of the same run, since
        # a CI runner's software GPU draws 1080p at about 30 a second, not 60.
        self.assertGreater(busy, 0.7 * quiet, (quiet, busy, n))

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
        for what, cmd in {"a generator on source 2": ["load", 2, 0, self.write(S.translate_native(S.parse(FLAT), (W, H)))],
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
        self.assertEqual(st["slots"][2], [1, 2, 3])
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


class PassParseTest(unittest.TestCase):
    """What the panel takes of PASSES on the New renderer, and what classic still refuses."""

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
        }
        for what, text in bad.items():
            with self.subTest(what):
                with self.assertRaises(S.ShaderError):
                    S.parse(text, passes=True)


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


if __name__ == "__main__":
    unittest.main()
