# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""An effect over a generator shader (D74) on a real mpv that really draws: the rig of tests/test_effects_gpu.py.

It began as a spike: before anything in pvj/ was changed, a generator's text and an effect's text were handed to the
player together through its socket, to see what it does with the pair (the first commit of the branch has that
version; its answers are in D74 and in pvj/SHADERS.md). What it found is held here, now through the engines: the
pair is the effect of the generator's picture, the order of the player's list decides that, the effect meets the
generator's drawing size and is capped by Effect detail only below it, both clocks run, Blackout and the opacity
darken the pair, a refused pair leaves the generator on, and a refusal of one of the two is not taken for the other's.

CI's three effects-gpu jobs run these: tests/test_effects_gpu.py, the module the workflow names, loads this one when
it is run. Run alone:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m tests.test_pair_gpu
A software GPU: nothing here says what the pair costs a real board.
"""
import os
import shutil
import sys
import time
import unittest

from pvj import effects as E, shaders as S
from pvj.api import ApiError
from tests.test_effects_gpu import BROKEN, CLIP, GPU, H, INVERT, ONLY, SAME, SWAP_HALVES, W, FxCase, differ, grid
from tests.test_server import ServerBase
from tests.test_shaders_gpu import BROKEN as BROKEN_GENERATOR

GL = "GLSL %s" % os.environ.get("MESA_GLSL_VERSION_OVERRIDE", "of the driver")
# generators: a gradient (a flat colour could not show a flip), a checkerboard of single pixels, a clock
GRADIENT = "/*{}*/\nvoid main() { gl_FragColor = vec4(isf_FragNormCoord, 0.25, 1.0); }\n"
CHECKER = ("/*{}*/\nvoid main() { float c = mod(floor(gl_FragCoord.x) + floor(gl_FragCoord.y), 2.0);\n"
           "    gl_FragColor = vec4(0.2 + 0.6 * c, 0.5, 0.8 - 0.6 * c, 1.0); }\n")
GEN_CLOCK = "/*{}*/\nvoid main() { gl_FragColor = vec4(fract(TIME / 8.0), fract(TIME / 64.0), 0.25, 1.0); }\n"
# a filter whose left half is the picture under it and whose right half is its own clock
FX_CLOCK = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
            "void main() { vec4 c = IMG_THIS_PIXEL(inputImage);\n"
            "    if (isf_FragNormCoord.x > 0.5) { c = vec4(fract(TIME / 8.0), fract(TIME / 64.0), 0.5, 1.0); }\n"
            "    gl_FragColor = c; }\n")
# a filter that writes the size it is drawn at into the colour (a reading good to 4 pixels: 1024 in 255 steps)
FX_SIZE = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
           "void main() { gl_FragColor = vec4(RENDERSIZE.x / 1024.0, RENDERSIZE.y / 1024.0, IMG_SIZE(inputImage).x / 1024.0, 1.0); }\n")


def negative(rows):
    return [[tuple(255 - c for c in p) for p in row] for row in rows]


class PairCase(FxCase):
    def say(self, what, *args):
        print("pair, ES %s, %s: %s" % (self.ES, GL, what % args))

    def generator(self, name, source, size=None):
        """Show a generator through the engine; with `size`, its text is then exchanged for one drawn at that size
        (the window is 320 x 180, and the engine never draws more lines than the screen has)."""
        self.engine.upload(name, source, replace=True)
        r = self.engine.show(name)
        self.assertTrue(r["ok"], r)
        if size is not None:
            p = self.engine.playing
            text = S.translate(S.parse(source), size, desc="nxlx shader 1 %d" % (900 + len(os.listdir(self.rundir))), anchor=p["anchor"])
            path = os.path.join(self.rundir, "big-%d.glsl" % len(os.listdir(self.rundir)))
            with open(path, "w") as f:
                f.write(text)
            self.assertTrue(self.real.swap_source(path, p["epoch"]))
            p["size"] = size                 # what the engine's record would say had it drawn at this size itself
        self.settle()

    def kinds(self):
        return [n.split("-")[0] for n in self.loaded()]

    def look(self):
        """The worker's look at the picture under the effect, as it comes once a second."""
        self.fx.adjust("anchor")
        self.settle()

    # -- the picture --
    def test_an_effect_over_a_generator_is_the_effect_of_the_generators_picture(self):
        self.fx.upload("invert.fs", INVERT)
        self.fx.upload("swap.fs", SWAP_HALVES)
        self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        self.put("invert.fs")
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.assertIsNotNone(self.engine.on_screen())
        over = self.still()
        d = differ(over, negative(plain))
        self.say("an invert over a gradient generator, against 255 minus the generator: max %d mean %.2f; a quarter in, a quarter down %s over %s",
                 d[0], d[1], over[H // 4][W // 4], plain[H // 4][W // 4])
        self.assertLessEqual(d[0], 3, "an invert over a generator is not the negative of the generator's picture")
        on = self.fx.state()["on"]
        self.assertEqual((on["picture"], on["working"]["under"], on["working"]["clip"]),
                         ({"matrix": "rgb", "levels": "full", "fps": 30.0}, "shader", {"width": W, "height": H, "lines": H}))
        st = self.api.status({}, None, "t")["player"]
        self.assertEqual((st["shader"], st["effect"]), ("gradient", "invert"))
        # the amount: half way, then 0, where the player leaves the effect's pass out
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.near(self.still(flat=True)[H // 4][W // 4], (128, 128, 128), 4)
        self.fx.change({"controls": {"amount": 0.0}})
        self.pump()
        self.assertLessEqual(differ(self.still(), plain)[0], 3)
        self.assertEqual(self.fx._fresh(self.fx.on["desc"]), [], "the player drew a pass of the effect at amount 0")
        # which way up: a filter that draws the top half in the bottom half gives the generator upside down
        self.put("swap.fs")
        d = differ(self.still(), list(reversed(plain)))
        self.assertLessEqual(d[0], 3, "the effect meets the generator's picture the wrong way up: %s" % (d,))
        self.fx.off()
        self.assertEqual(self.kinds(), ["shader"])
        self.assertLessEqual(differ(self.still(), plain)[0], 3)

    def test_the_order_of_the_players_list_is_the_order_the_two_run_in(self):
        """Why Player._push_shaders lists the source first. Both hook NATIVE; the other way round the generator
        draws over what the effect made, and the screen shows the generator alone."""
        self.fx.upload("invert.fs", INVERT)
        self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        self.put("invert.fs")
        ours = self.real.ipc.request("get_property", "glsl-shaders")
        self.assertLessEqual(differ(self.still(), negative(plain))[0], 3)
        self.real.ipc.request("set_property", "glsl-shaders", list(reversed(ours)))
        self.settle(0.3)
        back = self.still()
        d, n = differ(back, plain), differ(back, negative(plain))
        self.say("the list the other way round (effect, then generator): against the generator mean %.2f, against its negative mean %.2f", d[1], n[1])
        self.assertLessEqual(d[0], 3, "with the effect first in the list the screen is not the generator alone: the order no longer decides")
        self.real.ipc.request("set_property", "glsl-shaders", ours)

    def test_a_generator_chosen_while_an_effect_is_on_comes_up_filtered_and_a_clip_after_it_too(self):
        self.fx.upload("invert.fs", INVERT)
        self.put("invert.fs")                                   # over the test clip (a video: the effect's hook for LUMA runs)
        self.generator("gradient.fs", GRADIENT)
        self.assertEqual((self.kinds(), self.fx.state()["on"]["id"]), (["shader", "effect"], "invert.fs"))
        first = self.still()                                    # before the worker has looked: the text made for the clip
        self.look()                                             # the look: a text for the RGB picture, seen by the GPU
        self.assertIs(self.fx.on["checked"], True)
        self.assertIsNone(self.fx.error)
        second = self.still()
        self.fx.off()
        plain = self.still()
        for name, rows in (("before the worker's look", first), ("after it", second)):
            d = differ(rows, negative(plain))
            self.say("a generator chosen under an invert that was on, %s: against the generator's negative max %d mean %.2f", name, d[0], d[1])
            self.assertLessEqual(d[0], 3, name)
        # and a clip chosen while the pair is on: the generator goes, the effect stays
        self.put("invert.fs")
        self.play(CLIP)
        self.look()
        self.assertEqual((self.kinds(), self.fx.state()["on"]["working"]["under"]), (["effect"], "clip"))
        over = self.still()
        self.fx.off()
        self.assertLess(differ(over, negative(self.still()))[1], 3.0)

    def test_a_shader_pad_tapped_with_an_effect_on_comes_up_filtered_from_the_panel_and_from_a_controller(self):
        """The two features together (D73 and D74), on the picture: a pad that holds a generator, tapped while an
        effect is on over a clip. From a controller the tap is carried out by the generators' worker."""
        self.fx.upload("invert.fs", INVERT)
        self.engine.upload("gradient.fs", GRADIENT, replace=True)
        self.assertTrue(self.engine.show("gradient.fs")["ok"])
        plain = self.still()                                    # the generator alone, to judge by
        self.api.set_pad({"bank": 0, "index": 0, "shader": "gradient.fs"}, None, "t")
        for name, device in (("the panel", None), ("a controller", {"id": "midi"})):
            self.play(CLIP)
            self.put("invert.fs")
            out = self.api.play({"pad": [0, 0]}, device, "t")
            self.assertEqual(out["shader"], "gradient.fs", name)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (self.engine.on_screen() or {}).get("id") == "gradient.fs":
                time.sleep(0.05)
            self.assertEqual((self.engine.on_screen() or {}).get("id"), "gradient.fs", name)
            self.look()
            self.assertEqual((self.kinds(), self.fx.state()["on"]["id"], self.fx.error), (["shader", "effect"], "invert.fs", None), name)
            d = differ(self.still(), negative(plain))
            self.say("a shader pad tapped from %s under an invert: against the generator's negative max %d mean %.2f", name, d[0], d[1])
            self.assertLessEqual(d[0], 3, name)
            st = self.api.status({}, None, "t")["player"]
            self.assertEqual((st["shader"], st["effect"], st.get("shader_refused")), ("gradient", "invert", None), name)
            self.fx.off()

    # -- sizes --
    def test_effect_detail_caps_the_effect_only_below_the_generators_lines(self):
        """The effect meets the generator's drawing (not the carrier's few pixels). At or above the generator's lines
        the cap changes nothing and every pixel comes through; below them the effect draws at the cap and reads the
        generator with the GPU's smoothing: softer, as a capped effect over a clip is."""
        self.fx.upload("same.fs", SAME)
        self.fx.upload("size.fs", FX_SIZE)
        self.generator("checker.fs", CHECKER)
        plain = self.still(flat=True)
        self.assertGreater(max(abs(a - b) for a, b in zip(plain[H // 2][W // 2], plain[H // 2][W // 2 + 1])), 100, "no checkerboard of single pixels")
        for lines, soft in ((None, False), (H, False), (240, False), (90, True)):
            self.cap(lines)
            self.put("same.fs")
            rows = self.still(flat=True)
            d = differ(rows, plain)
            w = self.fx.state()["on"]["working"]
            self.say("a checkerboard generator under an effect that changes nothing, cap %s: max %d mean %.2f; the panel is told %s x %s, scaled %s",
                     lines, d[0], d[1], w["width"], w["height"], w["scaled"])
            if soft:
                self.assertGreater(d[1], 40, "a cap below the generator's lines changed nothing")
                self.assertEqual((w["width"], w["height"], w["scaled"]), (160, 90, True))
            else:
                self.assertEqual(d[0], 0, "an effect that changes nothing changed the generator at cap %s" % lines)
                self.assertEqual((w["width"], w["height"], w["scaled"]), (W, H, False))
            self.fx.off()
        # the size the filter itself is told, for a generator that draws more than the window shows
        for size, lines, want in (((640, 360), None, (640, 360)), ((640, 360), 240, (427, 240)), ((640, 360), 90, (160, 90)), ((320, 180), 240, (320, 180))):
            self.generator("gradient.fs", GRADIENT, size)
            self.cap(lines)
            self.put("size.fs")
            r, g, b = self.still(flat=True)[H // 2][W // 2]
            got = (r / 255.0 * 1024, g / 255.0 * 1024, b / 255.0 * 1024)
            w = self.fx.state()["on"]["working"]
            self.say("a generator at %s under a cap of %s: the filter's RENDERSIZE about %.0f x %.0f, IMG_SIZE.x about %.0f; the panel is told %s x %s",
                     size, lines, got[0], got[1], got[2], w["width"], w["height"])
            self.assertTrue(abs(got[0] - want[0]) <= 5 and abs(got[1] - want[1]) <= 5 and abs(got[2] - want[0]) <= 5, (size, lines, got, want))
            self.assertEqual((w["width"], w["height"]), want)
            self.assertEqual(E.work_size(size[0], size[1], lines), want)
            self.fx.off()

    # -- the two clocks --
    def test_both_clocks_run_and_the_effects_goes_on_when_another_generator_comes(self):
        self.fx.upload("fxclock.fs", FX_CLOCK)
        self.generator("genclock.fs", GEN_CLOCK)
        self.put("fxclock.fs")

        def now():
            rows, at = self.shot(), time.monotonic()
            out = []
            for x in (W // 4, 3 * W // 4):
                r, g, _ = rows[H // 2][x]
                fine, coarse = r / 255.0 * 8.0, g / 255.0 * 64.0
                out.append((round((coarse - fine) / 8.0) * 8.0 + fine) % 64.0)
            return out[0], out[1], at
        time.sleep(0.5)
        g1, f1, t1 = now()
        time.sleep(2.0)
        g2, f2, t2 = now()
        gen, fx = ((g2 - g1) % 64.0) / (t2 - t1), ((f2 - f1) % 64.0) / (t2 - t1)
        self.say("over two seconds the generator's TIME ran at %.2f times the clock and the effect's at %.2f", gen, fx)
        self.assertTrue(0.9 <= gen <= 1.1 and 0.9 <= fx <= 1.1, (gen, fx))
        self.generator("gradient.fs", GRADIENT)                 # another generator takes the screen, as a Vibes step does
        self.assertEqual(self.kinds(), ["shader", "effect"])
        _, f3, t3 = now()
        went = ((f3 - f2) % 64.0) / (t3 - t2)
        self.say("across another generator taking the screen (%.2f s) the effect's TIME went on at %.2f times the clock", t3 - t2, went)
        # (the player draws no frame while the GPU takes the new generator, so a little under the clock is right)
        self.assertTrue(0.6 <= went <= 1.25, "the effect's TIME jumped or stood when another generator came: %.2f" % went)

    # -- Vibes --
    def test_the_effect_stays_through_two_steps_of_a_vibes_rotation(self):
        self.fx.upload("invert.fs", INVERT)
        self.put("invert.fs")
        self.settings.data["mix"]["duration"] = 0.4
        vibes = self.api.vibes
        vibes._use_thread = False
        vibes.log = lambda *_: None
        vibes.start()
        self.addCleanup(vibes.stop)
        self.assertTrue(vibes.tick())
        shown = []
        for step in range(2):
            self.look()
            self.assertEqual((self.kinds(), self.fx.state()["on"]["id"]), (["shader", "effect"], "invert.fs"), step)
            self.assertIsNone(self.fx.error)
            time.sleep(0.5)
            self.assertTrue(self.fx._fresh(self.fx.on["desc"]), "the player draws no pass of the effect over %s" % vibes.current)
            self.assertTrue(self.engine._fresh(self.engine.playing["desc"]), "the player draws no pass of %s under the effect" % vibes.current)
            rows = self.shot()
            self.assertGreater(len(set(grid(rows, 9))), 12, "a flat picture: %s under the effect" % vibes.current)
            shown.append(vibes.current)
            vibes.skip()
            vibes.tick()
        self.say("the effect stayed over %s through the rotation's dips", shown)
        vibes.stop()
        self.assertEqual((self.loaded(), self.fx.state()["on"], self.fx.state()["last"]), ([], None, "the shader under it was taken off the screen"))

    # -- refusals --
    def test_a_refused_effect_leaves_the_generator_on_and_is_not_sent_again(self):
        self.fx.upload("broken.fs", BROKEN)
        self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        with self.assertRaises(ApiError) as c:
            self.fx.put("broken.fs")
        self.assertEqual(c.exception.status, 422)
        for words in ("nonsense", "line 4:", "over the shader", "No effect is on.", "The shader stays on the screen."):
            self.assertIn(words, c.exception.message)
        self.assertEqual(self.kinds(), ["shader"])
        self.assertLessEqual(differ(self.still(), plain)[0], 3, "the generator is not on the screen as it was")
        self.assertIsNotNone(self.engine.on_screen())
        sent = len([n for n in os.listdir(self.rundir) if n.startswith("effect-")])
        serial = self.real.effect_serial
        with self.assertRaises(ApiError) as c:
            self.fx.put("broken.fs")
        self.assertIn("not sent again", c.exception.message)
        self.assertEqual((self.real.effect_serial, self.kinds()), (serial, ["shader"]))    # the player heard nothing of it
        self.assertEqual(len([n for n in os.listdir(self.rundir) if n.startswith("effect-")]), sent)
        self.assertLessEqual(differ(self.still(), plain)[0], 3)

    def test_a_refusal_of_one_of_the_two_is_not_taken_for_the_others(self):
        """Both engines listen to the player's one log. A real refusal is read here by the other engine's ears: the
        player prints the refused text, with its name in it, before the compiler's words (shaders.about)."""
        self.fx.upload("invert.fs", INVERT)
        self.fx.upload("broken.fs", BROKEN)
        self.engine.upload("broken-generator.fs", BROKEN_GENERATOR)
        self.generator("gradient.fs", GRADIENT)
        self.put("invert.fs")
        over = self.still()
        # a generator the GPU refuses, while the effect's engine listens
        tap = self.fx._tap(self.real.socket_path)
        r = self.engine.show("broken-generator.fs")
        self.assertFalse(r["ok"], r)
        self.assertIn("nonsense", r["error"])
        lines = tap.drain(0.5)
        tap.close()
        self.assertIn("nonsense", S.shader_errors(lines), "the listener heard nothing: this test would pass for no reason")
        self.assertEqual(S.shader_errors(S.about(lines, self.fx.on["desc"])), "", "a refused generator was taken for a refusal of the effect")
        self.assertEqual((self.kinds(), self.fx.state()["on"]["id"]), (["shader", "effect"], "invert.fs"))
        self.assertLessEqual(differ(self.still(), over)[0], 3, "the generator before the refused one is not back under the effect")
        # an effect the GPU refuses, while the generators' engine listens
        tap = self.engine._tap(self.real.socket_path)
        with self.assertRaises(ApiError):
            self.fx.put("broken.fs")
        lines = tap.drain(0.5)
        tap.close()
        self.assertIn("nonsense", S.shader_errors(lines))
        self.assertEqual(S.shader_errors(S.about(lines, self.engine.playing["desc"])), "", "a refused effect was taken for a refusal of the generator")
        self.assertEqual(self.fx.state()["on"]["id"], "invert.fs")                     # the effect before it is back on

    # -- the brightness --
    def test_blackout_and_the_opacity_darken_the_pair(self):
        self.fx.upload("invert.fs", INVERT)
        self.generator("gradient.fs", GRADIENT)
        self.put("invert.fs")
        over = self.still()
        self.api.blackout({"on": True}, None, "t")
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.assertLessEqual(max(max(p) for p in grid(self.still(flat=True))), 3, "Blackout does not darken an invert over a generator")
        self.api.blackout({"on": False}, None, "t")
        self.api.control({"action": "opacity", "value": 50}, None, "t")
        d = differ(self.still(), [[tuple(max(0, c - 128) for c in p) for p in row] for row in over])
        self.assertLess(d[1], 3.0, "opacity 50 under the pair: %s" % (d,))
        self.api.control({"action": "opacity", "value": 100}, None, "t")
        self.assertLessEqual(differ(self.still(), over)[0], 3)


class PlainCase(PairCase):
    """The same pair with the player's default scalers and on a box that is not a Pi 4: there an effect over a clip
    leaves the buffers' format alone (Player._apply_fbo says why), and under a generator they are 8-bit all the
    same, for the generator. One test: the pair is right there too, from the first look."""
    def setUp(self):
        super(FxCase, self).setUp()                             # the generators' rig: no --profile=fast, the box's own kind
        self.fx = self.api.effects
        self.fx.log = lambda *_: None
        self.fx.changer._use_thread = False
        self.real.ipc.request("set_property", "screenshot-png-filter", 0)
        self.real.ipc.request("set_property", "screenshot-png-compression", 1)

    def test_the_pair_is_right_with_the_players_default_scalers(self):
        self.assertFalse(self.fx.eight_bit())
        self.fx.upload("invert.fs", INVERT)
        self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")
        self.fx.put("invert.fs")
        first = self.shot()                                     # at once: no dark picture while it goes on
        self.assertGreater(len(set(grid(first, 9))), 12, "a flat picture right after the effect went on over a generator")
        d = differ(self.still(), negative(plain))
        self.say("default scalers: an invert over a gradient generator, against the negative: max %d mean %.2f", d[0], d[1])
        self.assertLessEqual(d[0], 3)
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")
        self.fx.off()
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")      # the generator's, still
        self.assertLessEqual(differ(self.still(), plain)[0], 3)


for _name in dir(FxCase):                     # the effects' own tests run from their module, not here again
    if _name.startswith("test_") and _name not in PairCase.__dict__:
        setattr(PairCase, _name, None)
for _name in PairCase.__dict__:               # and the pair's tests once, on their own rig
    if _name.startswith("test_"):
        setattr(PlainCase, _name, None)


if ONLY != "desktop":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesPairTest(PairCase, ServerBase):
        ES = "yes"

    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesPlainPairTest(PlainCase, ServerBase):
        ES = "yes"


if ONLY != "gles":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class DesktopGlPairTest(PairCase, ServerBase):
        ES = "no"

    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class DesktopGlPlainPairTest(PlainCase, ServerBase):
        ES = "no"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    ran = result.testsRun - len(result.skipped)
    print("pair GPU tests: %d run, %d skipped" % (ran, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() and ran > 0 and not result.skipped else 1)
