# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""An effect over a generator shader on a real mpv that really draws: the rig of tests/test_effects_gpu.py.

THE SPIKE (first commit of the branch): what the player does when a generator's text and an effect's text are in its
shader list together. Nothing in pvj/ is changed for it: the texts are made by the two translators and handed to the
player through its socket, because the engines still refuse the pair. Every probe prints what it saw on a line that
starts with "pair,", so that one run answers every question even where an answer is a surprise.

CI's three effects-gpu jobs run these: tests/test_effects_gpu.py, the module the workflow names, loads this one when
it is run. Run alone:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m tests.test_pair_gpu
A software GPU: nothing here says what the pair costs a real board.
"""
import os
import shutil
import sys
import time
import unittest

from pvj import effects as E, shaderlive as L, shaders as S
from tests.test_effects_gpu import GPU, H, INVERT, ONLY, SAME, SWAP_HALVES, W, FxCase, differ, grid, quick_rows
from tests.test_server import ServerBase

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
# a filter that writes the size it is drawn at and the size of its picture into the colour
FX_SIZE = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
           "void main() { gl_FragColor = vec4(RENDERSIZE.x / 1024.0, RENDERSIZE.y / 1024.0, IMG_SIZE(inputImage).x / 1024.0, 1.0); }\n")
# hand-written hooks: the size of the picture the hook meets; and a filter in two passes
RAW_SIZE = ("//!HOOK NATIVE\n//!BIND HOOKED\n%s//!DESC nxlx raw size\n\n"
            "vec4 hook() { return vec4(HOOKED_size.x / 1024.0, HOOKED_size.y / 1024.0, gl_FragCoord.x / 1024.0, 1.0); }\n")
TWO_PASS = ("//!HOOK NATIVE\n//!BIND HOOKED\n%s//!SAVE PVJA\n//!DESC nxlx pass one\n\n"
            "vec4 hook() { return vec4(vec3(1.0) - HOOKED_tex(HOOKED_pos).rgb, 1.0); }\n\n"
            "//!HOOK NATIVE\n//!BIND HOOKED\n//!BIND PVJA\n//!DESC nxlx pass two\n\n"
            "vec4 hook() { vec2 p = HOOKED_pos;\n"
            "    return vec4(mix(HOOKED_tex(p).rgb, PVJA_tex(vec2(1.0 - p.x, p.y)).rgb, step(0.5, p.x)), 1.0); }\n")
RGB30 = {"matrix": "rgb", "levels": "full", "fps": 30.0}


def negative(rows):
    return [[tuple(255 - c for c in p) for p in row] for row in rows]


class PairSpike(FxCase):
    def say(self, what, *args):
        print("pair, ES %s, %s: %s" % (self.ES, GL, what % args))

    def write(self, text, stem="pair"):
        self.n = getattr(self, "n", 0) + 1
        path = os.path.join(self.rundir, "%s-%d.glsl" % (stem, self.n))
        with open(path, "w") as f:
            f.write(text)
        return path

    def fx_text(self, source, lines=None, controls=None, planes=("LUMA", "RGB")):
        """An effect's text as the translator makes it for a picture in RGB at 30 a second; with `planes`, only those
        of its two hooks."""
        self.n = getattr(self, "n", 0) + 1
        parsed, desc = S.parse(source, S.FILTER), "nxlx effect 1 %d" % self.n
        if planes == ("LUMA", "RGB"):
            return E.translate(parsed, None, controls, RGB30, desc, lines=lines), desc
        out = []
        for plane in planes:
            out += E._block(parsed, S.clean_values(parsed, None), L.clean_fx_controls(controls), E.clean_picture(**RGB30), desc, plane,
                            time.localtime(), lines)
        return "\n".join(out), desc

    def generator(self, name, source, size=None):
        """Show a generator through the engine; with `size`, its text is then exchanged for one drawn at that size
        (the window is 320 x 180, and the engine never draws more lines than the screen has)."""
        self.engine.upload(name, source, replace=True)
        r = self.engine.show(name)
        self.assertTrue(r["ok"], r)
        if size is not None:
            p = self.engine.playing
            text = S.translate(S.parse(source), size, desc="nxlx shader 1 %d" % (900 + getattr(self, "n", 0)), anchor=p["anchor"])
            self.assertTrue(self.real.swap_source(self.write(text, "big"), p["epoch"]))
        self.settle()
        return self.real.source_shader

    def together(self, *paths):
        """The player's shader list, set through its socket: nothing in pvj/ knows of it."""
        self.real.ipc.request("set_property", "glsl-shaders", list(paths))
        self.settle(0.3)

    def passes(self):
        p = self.real.ipc.request("get_property", "vo-passes") or {}
        return [(str(x.get("desc")), x.get("avg")) for x in (p.get("fresh") or []) if isinstance(x, dict)]

    # -- a: is it the effect of the generator's picture, and which way up --
    def test_a_an_invert_over_a_generator_is_the_negative_of_the_generators_picture(self):
        gen = self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        self.say("a. the generator alone at a quarter in, a quarter down: %s (the gradient says about (64, 191, 64))", plain[H // 4][W // 4])
        text, desc = self.fx_text(INVERT)
        fx = self.write(text)
        self.together(gen, fx)
        over = self.still()
        d = differ(over, negative(plain))
        self.say("a. generator then invert: the same place %s; against 255 minus the generator max %d mean %.2f; passes %s",
                 over[H // 4][W // 4], d[0], d[1], self.passes())
        # the order of the list, the other way round
        self.together(fx, gen)
        back = self.still()
        d2, d3 = differ(back, plain), differ(back, negative(plain))
        self.say("a. invert then generator (the list the other way round): against the generator mean %.2f, against its negative mean %.2f; passes %s",
                 d2[1], d3[1], self.passes())
        # which way up: the top half in the bottom half
        text, _ = self.fx_text(SWAP_HALVES)
        self.together(gen, self.write(text))
        swapped = self.still()
        d4 = differ(swapped, list(reversed(plain)))
        self.say("a. generator then swap-halves: against the generator upside down max %d mean %.2f", d4[0], d4[1])
        # the amount: half way, and 0 (the hook left out)
        text, _ = self.fx_text(INVERT, controls={"amount": 0.5})
        self.together(gen, self.write(text))
        self.say("a. invert at amount 0.5: %s (half way is about (128, 128, 128))", self.still(flat=True)[H // 4][W // 4])
        text, _ = self.fx_text(INVERT, controls={"amount": 0.0})
        self.together(gen, self.write(text))
        d5 = differ(self.still(), plain)
        self.say("a. invert at amount 0: against the generator max %d mean %.2f; passes %s", d5[0], d5[1], self.passes())
        self.assertLess(d[1], 3.0, "an invert over a generator is not the negative of the generator's picture")

    # -- b: which of the effect's two hooks runs over the carrier, and the player's own word about the picture --
    def test_b_which_hook_of_the_effect_runs_and_what_the_player_says_of_the_picture(self):
        gen = self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        for planes in (("RGB",), ("LUMA",), ("LUMA", "RGB")):
            text, desc = self.fx_text(INVERT, planes=planes)
            self.together(gen, self.write(text))
            d = differ(self.still(), negative(plain))
            mine = [p for p in self.passes() if desc in p[0]]
            self.say("b. the effect's hook for %s: against the negative mean %.2f; its passes %s", " and ".join(planes), d[1], mine)
        for name in ("video-params", "video-out-params", "container-fps", "estimated-vf-fps", "path", "fbo-format"):
            try:
                v = self.real.ipc.request("get_property", name)
            except Exception as e:
                v = "(%s)" % e
            self.say("b. the player's %s over a generator: %s", name, v)
        self.say("b. Effects.picture() over a generator: %s, size %s, unfit %s, estimated %s", self.fx.picture(), self.fx.size, self.fx.unfit,
                 self.fx.estimated)

    # -- c: sizes --
    def test_c_the_size_the_effect_meets_and_draws_at(self):
        for size in ((320, 180), (640, 360)):
            gen = self.generator("gradient.fs", GRADIENT, size)
            for lines in (None, 90, 240):
                cap = "".join(l + "\n" for l in E.size_lines(lines)) if lines else ""
                self.together(gen, self.write(RAW_SIZE % cap))
                r, g, b = self.still(flat=True)[H // 2][W - 2]
                self.say("c. generator at %s, a hand-written hook with %s: HOOKED_size %.0f x %.0f, the hook's own width about %.0f",
                         size, "a cap of %d lines" % lines if lines else "no cap", r / 255.0 * 1024, g / 255.0 * 1024, b / 255.0 * 1024)
                text, _ = self.fx_text(FX_SIZE, lines=lines)
                self.together(gen, self.write(text))
                r, g, b = self.still(flat=True)[H // 2][W // 2]
                self.say("c. generator at %s, the translator's effect with %s: RENDERSIZE %.0f x %.0f, IMG_SIZE.x %.0f; E.work_size says %s",
                         size, "a cap of %d lines" % lines if lines else "no cap", r / 255.0 * 1024, g / 255.0 * 1024, b / 255.0 * 1024,
                         E.work_size(size[0], size[1], lines))

    def test_c_is_the_generator_blurred_by_an_effect_that_changes_nothing(self):
        gen = self.generator("checker.fs", CHECKER)
        plain = self.still(flat=True)
        self.say("c. a checkerboard of single pixels, the generator alone: two neighbours %s %s", plain[H // 2][W // 2], plain[H // 2][W // 2 + 1])
        for lines in (None, 90):
            text, _ = self.fx_text(SAME, lines=lines)
            self.together(gen, self.write(text))
            rows = self.still(flat=True)
            d = differ(rows, plain)
            self.say("c. the checkerboard under an effect that changes nothing, %s: against the generator alone max %d mean %.2f; two neighbours %s %s",
                     "a cap of 90 lines" if lines else "no cap", d[0], d[1], rows[H // 2][W // 2], rows[H // 2][W // 2 + 1])

    # -- d: the two clocks --
    def test_d_both_clocks_run(self):
        gen = self.generator("genclock.fs", GEN_CLOCK)
        text, _ = self.fx_text(FX_CLOCK)
        fx = self.write(text)
        self.together(gen, fx)

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
        self.say("d. over two seconds the generator's TIME ran at %.2f times the clock and the effect's at %.2f; the two read %.2f and %.2f",
                 ((g2 - g1) % 64.0) / (t2 - t1), ((f2 - f1) % 64.0) / (t2 - t1), g2, f2)
        # another generator takes the screen (as a Vibes step does): what the effect's clock does across it
        self.engine.upload("gradient.fs", GRADIENT, replace=True)
        self.engine.show("gradient.fs")
        self.together(self.real.source_shader, fx)
        g3, f3, t3 = now()
        self.say("d. after another generator took the screen, %.2f s later: the effect's TIME went on by %.2f s (the generator's half now reads the gradient)",
                 t3 - t2, (f3 - f2) % 64.0)
        self.real.ipc.request("set_property", "pause", True)
        time.sleep(0.4)
        _, f4, t4 = now()
        time.sleep(1.0)
        _, f5, t5 = now()
        self.real.ipc.request("set_property", "pause", False)
        self.say("d. with the player frozen for %.2f s the effect's TIME moved by %.2f s", t5 - t4, (f5 - f4) % 64.0)

    # -- e: a filter in two passes --
    def test_e_a_filter_in_two_passes_over_a_generator(self):
        gen = self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        for name, size in (("at the picture's size", ""), ("its first pass at half the size", "//!WIDTH HOOKED.w 2 /\n//!HEIGHT HOOKED.h 2 /\n")):
            self.together(gen, self.write(TWO_PASS % size))
            rows = self.still()
            left, right = rows[H // 4][W // 4], rows[H // 4][3 * W // 4]
            want = tuple(255 - c for c in plain[H // 4][W // 4])
            self.say("e. two passes, %s: the left half %s (the generator has %s there), the right half %s (the negative of the mirrored place is %s); passes %s",
                     name, left, plain[H // 4][W // 4], right, want, self.passes())

    # -- f: the brightness still comes after both --
    def test_f_blackout_and_opacity_darken_the_pair(self):
        gen = self.generator("gradient.fs", GRADIENT)
        text, _ = self.fx_text(INVERT)
        self.together(gen, self.write(text))
        over = self.still()
        self.api.blackout({"on": True}, None, "t")
        loaded = len(self.real.ipc.request("get_property", "glsl-shaders"))
        dark = max(max(p) for p in grid(self.still(flat=True)))
        self.say("f. Blackout under generator and invert: the brightest point %d (with %d shaders in the list)", dark, loaded)
        self.api.blackout({"on": False}, None, "t")
        self.together(gen, self.write(text))
        self.api.control({"action": "opacity", "value": 50}, None, "t")
        loaded = len(self.real.ipc.request("get_property", "glsl-shaders"))
        half = self.still()
        d = differ(half, [[tuple(max(0, c - 128) for c in p) for p in row] for row in over])
        self.say("f. opacity 50 under the pair: against the pair less a half max %d mean %.2f (with %d shaders in the list)", d[0], d[1], loaded)
        self.api.control({"action": "opacity", "value": 100}, None, "t")
        # the still a crossfade takes is a screenshot of the window: it holds the pair
        self.together(gen, self.write(text))
        path = os.path.join(self.tmp, "still.png")
        self.real.still(path)
        w, h, rows = quick_rows(path)
        d = differ(rows, over)
        self.say("f. Player.still() of the pair: %d x %d, against the pair on screen max %d mean %.2f", w, h, d[0], d[1])

    # -- g: through the player's own list (source, effect, mapping), not the socket --
    def test_g_the_players_own_order_of_the_list(self):
        gen = self.generator("gradient.fs", GRADIENT)
        plain = self.still()
        text, _ = self.fx_text(INVERT)
        with self.real._lock:
            self.real._effect, self.real._effect_pid = self.write(text, "effect"), self.real.ipc.request("get_property", "pid")
            self.real._push_shaders()
        self.settle(0.3)
        d = differ(self.still(), negative(plain))
        self.say("g. Player._push_shaders with a source and an effect: the list %s; against the negative mean %.2f", self.loaded(), d[1])
        with self.real._lock:
            self.real._effect = None
            self.real._push_shaders()


for _name in dir(FxCase):                     # the effects' own tests run from their module, not here again
    if _name.startswith("test_") and _name not in PairSpike.__dict__:
        setattr(PairSpike, _name, None)


if ONLY != "desktop":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesPairTest(PairSpike, ServerBase):
        ES = "yes"


if ONLY != "gles":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class DesktopGlPairTest(PairSpike, ServerBase):
        ES = "no"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    ran = result.testsRun - len(result.skipped)
    print("pair GPU tests: %d run, %d skipped" % (ran, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() and ran > 0 and not result.skipped else 1)
