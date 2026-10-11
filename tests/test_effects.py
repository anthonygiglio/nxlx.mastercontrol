# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Effects: ISF filters over the playing picture (pvj/effects.py). The translator with hostile filter files, the
library, and the engine's whole life against the real Player class with a stand-in for mpv behind it (properties in
a dict, nothing drawn). What a real mpv draws is in tests/test_effects_gpu.py."""
import hashlib
import json
import math
import os
import random
import re
import struct
import threading
import time
import unittest

from pvj import effects as E, midi as M, shaderlive as L, shaders as S
from pvj.api import ApiError
from pvj.player import Player, PlayerError
from tests.test_server import ServerBase
from tests.test_shaders import FakeTap, REFUSAL, WELL_KNOWN

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACK_DIR = os.path.join(E.EFFECTS_DIR, "isf-files")
OWN = ["fx-edge-glow", "fx-grade", "fx-kaleido", "fx-mirror-quad", "fx-pixel-grid", "fx-rgb-split", "fx-ring", "fx-ripple", "fx-slit-bands", "fx-twirl",
       "fx-vignette", "fx-wash"]
MOVING = ["fx-kaleido", "fx-rgb-split", "fx-ring", "fx-ripple", "fx-slit-bands"]                # they read TIME: the flash limit applies
PICTURE = {"NAME": "inputImage", "TYPE": "image"}
BODY = "\nvoid main() {\n    gl_FragColor = IMG_THIS_PIXEL(inputImage) * k;\n}\n"
K = {"NAME": "k", "TYPE": "float", "MIN": 0, "MAX": 2, "DEFAULT": 1}


def fx(inputs=None, body=BODY, **head):
    return "/*" + json.dumps(dict({"INPUTS": [PICTURE, K] if inputs is None else inputs}, **head)) + "*/" + body


GOOD = fx()
ALL = fx([PICTURE, K, {"NAME": "lit", "TYPE": "bool", "DEFAULT": False}, {"NAME": "mode", "TYPE": "long", "VALUES": [0, 1, 2], "DEFAULT": 0},
          {"NAME": "tint", "TYPE": "color", "DEFAULT": [0, 0, 0, 1]}, {"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.5, 0.5]},
          {"NAME": "bang", "TYPE": "event"}],
         "\nvoid main() {\n    vec4 c = IMG_NORM_PIXEL(inputImage, spot) * k + tint;\n    if (lit || bang || mode == 2) { c = vec4(1.0); }\n    gl_FragColor = c;\n}\n")
MOVES = fx(body="\nvoid main() {\n    gl_FragColor = IMG_THIS_PIXEL(inputImage) * k * fract(TIME);\n}\n")
STROBE = fx(DESCRIPTION="A strobe you play by hand", CATEGORIES=["Stylize"])


def refusal(test, text, kind=S.FILTER):
    with test.assertRaises(S.ShaderError) as c:
        S.parse(text, kind)
    return str(c.exception)


class TranslatorTest(unittest.TestCase):
    def test_a_filter_reads_the_playing_picture_in_five_ways_and_nothing_of_the_picture_is_named_in_the_text(self):
        body = ("\nvoid main() {\n    vec4 a = IMG_THIS_PIXEL(inputImage) + IMG_THIS_NORM_PIXEL( inputImage );\n"
                "    vec4 b = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord) + IMG_PIXEL (inputImage , gl_FragCoord.xy);\n"
                "    gl_FragColor = (a + b) * k / IMG_SIZE(inputImage).x;\n}\n")
        p = S.parse(fx(body=body), S.FILTER)
        self.assertEqual((p["kind"], [i["name"] for i in p["inputs"]]), ("filter", ["k"]))        # the picture is not a control
        text = E.translate(p)
        self.assertNotIn("inputImage", text)
        self.assertNotIn("IMG_", text)
        self.assertEqual(text.count("pvj_img_this() + pvj_img_this()"), 2)                      # once per hook
        self.assertIn("pvj_img_norm( pvj_norm)", text.replace("isf_FragNormCoord", "pvj_norm"))
        self.assertIn("pvj_img_px( pvj_coord.xy)", text)
        self.assertIn("pvj_img_size().x", text)

    def test_the_text_is_two_hooks_at_the_native_stage_one_for_a_yuv_picture_and_one_for_rgb(self):
        p = S.parse(GOOD, S.FILTER)
        text = E.translate(p, {"k": 1.5}, {"amount": 0.25}, {"matrix": "bt.601", "levels": "full", "fps": 50.0}, "nxlx effect 7 3")
        self.assertEqual(re.findall(r"^//!(.*)$", text, re.M), ["HOOK NATIVE", "BIND HOOKED", "BIND LUMA", "DESC nxlx effect 7 3",
                                                                "HOOK NATIVE", "BIND HOOKED", "BIND RGB", "DESC nxlx effect 7 3"])
        first, second = text.split("//!HOOK NATIVE")[1:]
        self.assertIn("mat3(1.0, 1.0, 1.0, 0.0,", first)                               # the full-range BT.601 matrix
        self.assertIn("vec3 pvj_native(vec3 c) { return c; }", second)                 # nothing to convert for RGB
        for block in (first, second):
            self.assertIn("const float k = 1.5;", block)
            self.assertIn("#define RENDERSIZE HOOKED_size\n", block)
            self.assertIn("#define PVJ_LINE 1\n#else\n#define PVJ_LINE 0\n#endif\n#line PVJ_LINE\n", block)
            self.assertIn("return vec4(mix(pvj_src.rgb, pvj_native(c), 0.25), pvj_src.a);", block)
            self.assertIn("pvj_time = 0.0;", block)                                      # it does not read the clock
            self.assertIn("// nxlx effect 7 3\n", block)                                 # the name inside the code: a text is never alike another
        self.assertNotIn("//!WIDTH", text)                                             # no cap: the hook draws at the clip's own size
        self.assertNotIn("//!WHEN", text)
        half = E.translate(p, controls={"half": True})                                 # superseded, and still taken: 540 lines at most
        self.assertEqual(half.count("\n".join(E.size_lines(540))), 2)
        self.assertEqual(half.count("#define RENDERSIZE pvj_work\n"), 2)
        # an RGB picture: the YUV hook is still there, for the video that may come next
        self.assertIn("BIND LUMA", E.translate(p, picture={"matrix": "rgb", "levels": "full", "fps": 25.0}))

    def test_time_is_counted_from_the_players_frames_and_the_clips_rate(self):
        p = S.parse(MOVES, S.FILTER)
        self.assertEqual((p["clock"], p["flashes"]), (True, False))
        text = E.translate(p, picture={"matrix": "bt.709", "levels": "limited", "fps": 60.0})
        self.assertIn("pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / 60.0;", text)
        self.assertIn("#define TIMEDELTA 0.01666667", text)
        self.assertIn("/ 25.0 * 0.5;", E.translate(p, controls={"speed": 0.5}, picture={"fps": 25.0}))
        self.assertIn("/ 30.0;", E.translate(p, picture={"fps": float("nan")}))          # a rate the player cannot say: 30
        for word in ("TIMEDELTA", "FRAMEINDEX", "DATE"):
            self.assertTrue(S.parse(fx(body=BODY.replace("* k", "* k * float(%s)" % word)), S.FILTER)["clock"], word)
        self.assertFalse(S.parse(GOOD, S.FILTER)["clock"])
        self.assertFalse(S.parse(fx(body="// TIME in a comment only" + BODY), S.FILTER)["clock"])

    def test_a_filter_that_says_strobe_or_flash_is_marked(self):
        for head in ({"DESCRIPTION": "A strobe"}, {"CATEGORIES": ["Glitch", "Flash"]}, {"DESCRIPTION": "Flickers like an old lamp"}):
            self.assertTrue(S.parse(fx(**head), S.FILTER)["flashes"], head)
        self.assertFalse(S.parse(fx(DESCRIPTION="Inverts the picture"), S.FILTER)["flashes"])

    def test_values_and_controls_are_checked(self):
        p = S.parse(GOOD, S.FILTER)
        for bad in ({"values": {"nope": 1}}, {"values": {"k": "2"}}, {"controls": {"amount": "1"}}, {"controls": {"hue": 3}},
                    {"controls": {"half": 1}}, {"desc": "nxlx effect\n//!HOOK OUTPUT"}):
            with self.assertRaises(S.ShaderError, msg=bad):
                E.translate(p, **bad)
        self.assertEqual(L.clean_fx_controls({"amount": 7, "speed": -1}), {"amount": 1.0, "speed": 0.0, "half": False})
        self.assertIn("pvj_native(c), 0.0)", E.translate(p, controls={"amount": -3}))
        with self.assertRaises(S.ShaderError):
            E.translate(S.parse("/*{}*/\nvoid main() { gl_FragColor = vec4(1.0); }\n"))      # a generator is not put on as an effect
        with self.assertRaises(S.ShaderError):
            S.translate(p, (320, 180))                                                    # and a filter never takes the screen

    def test_what_is_refused_and_why(self):
        other = {"NAME": "mask", "TYPE": "image"}
        for text, why in (
            (fx([PICTURE, other]), "second picture"),
            (fx([{"NAME": "startImage", "TYPE": "image"}, {"NAME": "endImage", "TYPE": "image"}]), "second picture"),
            (fx([K]), "no picture input called inputImage"),
            (fx([]), "no picture input called inputImage"),
            (fx([{"NAME": "inputImage", "TYPE": "float"}]), "must be of the TYPE image"),
            (fx([PICTURE, PICTURE]), "two inputs are called inputImage"),
            (fx([PICTURE, {"NAME": "sky", "TYPE": "cube"}]), "cube map"),
            (fx([PICTURE, {"NAME": "sound", "TYPE": "audio"}]), "wants sound"),
            (fx([PICTURE, {"NAME": "spectrum", "TYPE": "audioFFT"}]), "wants sound"),
            (fx(IMPORTED={"logo": {"PATH": "logo.png"}}), "IMPORTED"),
            (fx(PASSES=[{"TARGET": "buf"}, {}]), "several passes"),
            (fx(PASSES=[{"TARGET": "last", "PERSISTENT": True}]), "persistent buffer"),
            (fx(PASSES=[{"WIDTH": "$WIDTH/4"}]), "its own drawing size"),
            (fx(PERSISTENT_BUFFERS=["last"]), "persistent buffer"),
            (fx(body=BODY.replace("IMG_THIS_PIXEL(inputImage)", "IMG_THIS_PIXEL(mask)")), "other than the one that is playing"),
            (fx(body=BODY.replace("IMG_THIS_PIXEL(inputImage)", "IMG_NORM_PIXEL(last, vec2(0.5))")), "other than the one that is playing"),
            (fx(body=BODY.replace("IMG_THIS_PIXEL(inputImage)", "IMG_PIXEL /* x */ (other, vec2(1.0))")), "other than the one that is playing"),
            (fx(body=BODY.replace("IMG_THIS_PIXEL(inputImage)", "texture2D(inputImage, vec2(0.5))")), "may only be read with"),
            (fx(body="\n#define src inputImage" + BODY.replace("(inputImage)", "(src)")), "other than the one that is playing"),
            (fx(body="\n#define src inputImage" + BODY), "may only be read with"),
            (fx(body="\n#define inputImage other" + BODY), "may only be read with"),
            (fx(body="\nvec4 read(sampler2D s) { return vec4(0.0); }" + BODY.replace("IMG_THIS_PIXEL(inputImage)", "read(inputImage)")), "may only be read with"),
            (fx(body=BODY.replace("* k", "* k * _inputImage_imgRect.x")), "ISF version 1"),
            (fx(body=BODY.replace("* k", "* k * LUMA_tex(vec2(0.5)).r")), "belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * RGB_size.x")), "belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * HOOKED_tex(vec2(0.5)).r")), "belongs to the player"),
            # names that only the GPU refused before (a 422 at upload now, with the name)
            (fx(body=BODY.replace("* k", "* k * LUMA_gather(vec2(0.5), 0).r")), "LUMA_gather belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * PREV_tex(vec2(0.5)).r")), "PREV_tex belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * OUTPUT_tex(vec2(0.5)).r")), "OUTPUT_tex belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * MAINPRESUB_tex(vec2(0.5)).r")), "MAINPRESUB_tex belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * CHROMA_texOff(vec2(1.0)).r")), "CHROMA_texOff belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * NATIVE_pt.x")), "NATIVE_pt belongs to the player"),
            (fx([PICTURE, K, {"NAME": "LUMA_pos", "TYPE": "float", "DEFAULT": 0.5}]), "an input is called LUMA_pos"),
            (fx([PICTURE, K, {"NAME": "RGB_size", "TYPE": "float", "DEFAULT": 0.5}]), "an input is called RGB_size"),
            # the player's own clocks: a filter that read them would move outside TIME, where the flash limit cannot see it
            (fx(body=BODY.replace("* k", "* k * mod(float(frame), 2.0)")), "the name frame belongs to the player"),
            (fx(body=BODY.replace("* k", "* k * step(0.5, random)")), "the name random belongs to the player"),
            (fx(body="\nfloat random(vec2 p) { return fract(p.x * 7.0); }" + BODY), "the name random belongs to the player"),
            (fx(body=BODY.replace("IMG_THIS_PIXEL(inputImage)", "pvj_at(vec2(2.0))")), "used by the player"),
            (fx(body="\n//!HOOK OUTPUT" + BODY), "//!"),
            (fx(DESCRIPTION="//!TEXTURE x"), "//!"),
            (fx(body="\n#include <x>" + BODY), "#include"),
            (fx(body="\n#version 300 es" + BODY), "#version"),
            (fx(body="\n#extension GL_OES_EGL_image_external : require" + BODY), "#extension"),
            (fx(body="\n#pragma optimize(off)" + BODY), "#pragma"),
            (fx(body="\n#define IMG_PIXEL(a, b) texture2D(a, b)" + BODY), "#define IMG_PIXEL"),
            (fx(body="\nuniform sampler2D other;" + BODY), "uniform"),
            (fx(body="\nvarying vec2 left_coord;" + BODY), "uniform, varying"),
            (fx(body="\n#define A IMG_##PIXEL" + BODY), "##"),
            (fx(body=BODY.replace("* k", "* k \\\n")), "\\"),
            (fx(body=BODY.replace("* k", "* k /* é */ * é")), "plain ASCII"),
            (fx(body=BODY + "\nvoid main() {}\n"), "exactly one void main()"),
            ("/*" + '{"INPUTS": [], "INPUTS": [%s]}' % json.dumps(PICTURE) + "*/" + BODY, "twice"),
            ("/*" + '{"INPUTS": [%s, {"NAME": "k", "TYPE": "float", "DEFAULT": NaN}]}' % json.dumps(PICTURE) + "*/" + BODY, "not a number"),
            ("void main() { gl_FragColor = vec4(1.0); }", "must start with"),
        ):
            self.assertIn(why, refusal(self, text), text[:120])
        # a comment may hold anything: it is cut out before the checks and never passed on
        calm = fx(body="\n// IMG_PIXEL(mask, p) and texture2D(inputImage, p) and → and a backslash \\\n/* #include <x> */" + BODY)
        self.assertNotIn("mask", E.translate(S.parse(calm, S.FILTER)))
        # the generator's reader still takes no picture, and says where a filter goes
        self.assertIn("added under Effects", refusal(self, GOOD, S.GENERATOR))
        self.assertIn("reads a picture", refusal(self, fx([K]), S.GENERATOR))

    def test_a_call_over_several_lines_keeps_the_files_line_numbers(self):
        body = "\nvoid main() {\n    gl_FragColor = IMG_NORM_PIXEL(\n        inputImage,\n        isf_FragNormCoord);\n    nonsense;\n}\n"
        p = S.parse(fx(body=body), S.FILTER)
        self.assertEqual(p["code"].count("\n"), body.rstrip().count("\n"))
        self.assertEqual(p["code"].split("\n")[5].strip(), "nonsense;")

    def test_the_colour_arithmetic_goes_there_and_back(self):
        def mul(m, v):
            return [sum(m[r][c] * v[c] for c in range(3)) for r in range(3)]
        for matrix in sorted(E.MATRICES) + ["rgb", "something new"]:
            for levels in ("limited", "full"):
                to_rgb, off, to_native = E.native_maps(matrix, levels)
                for rgb in ([0, 0, 0], [1, 1, 1], [1, 0, 0], [0.2, 0.7, 0.4]):
                    native = [a + b for a, b in zip(mul(to_native, rgb), off)]
                    back = mul(to_rgb, [a - b for a, b in zip(native, off)])
                    self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(back, rgb)), (matrix, levels, rgb, back))
        to_rgb, off, to_native = E.native_maps("bt.709", "limited")
        white = [round((a + b) * 255) for a, b in zip(mul(to_native, [1, 1, 1]), off)]
        red = [round((a + b) * 255) for a, b in zip(mul(to_native, [1, 0, 0]), off)]
        self.assertEqual((white, red), ([235, 128, 128], [63, 102, 240]))                 # the well-known BT.709 values
        self.assertEqual(E.clean_picture("bt.2020-ncl", "full", 59.94), {"matrix": "bt.2020-ncl", "levels": "full", "fps": 59.94})
        self.assertEqual(E.clean_picture(None, 7, True), {"matrix": "bt.709", "levels": "limited", "fps": 30.0})
        self.assertEqual(E.clean_picture("rgb", "full", 1000), {"matrix": "rgb", "levels": "full", "fps": 30.0})

    def test_an_honest_name_that_ends_like_one_of_the_players_is_allowed(self):
        """Only the player's own texture names are refused with those endings. `cell_size`, `ring_pos` and the like
        are what people call things (the review asked for every name ending so to be refused; that would have
        refused them too)."""
        for name in ("cell_size", "ring_pos", "tile_off", "wave_mul", "grid_pt", "glow_tex", "luma_size", "frames", "randomness", "frame_no"):
            p = S.parse(fx(body=BODY.replace("* k", "* k * %s" % name).replace("void main() {", "void main() {\n    float %s = 1.0;" % name)), S.FILTER)
            E.translate(p)
            self.assertFalse(p["clock"], name)

    def test_a_filter_that_counts_frames_moves_and_its_frames_follow_the_speed(self):
        p = S.parse(fx(body=BODY.replace("* k", "* k * mod(float(FRAMEINDEX), 2.0)")), S.FILTER)
        self.assertTrue(p["clock"])
        text = E.translate(p, {}, {"amount": 1.0, "speed": 0.5, "half": False}, E.clean_picture("bt.709", "limited", 25.0))
        self.assertIn("#define FRAMEINDEX int(pvj_time * 25.0 + 0.5)", text)
        self.assertNotIn("#define FRAMEINDEX frame", text)
        self.assertIn("/ 25.0 * 0.5;", text)                                           # TIME, and with it the frame number, at half speed

    def test_an_upload_whose_work_cannot_be_counted_is_refused(self):
        """The limits for an upload (64 reads, 256 rounds a pixel) bound something only if every loop says how often
        it runs. The review's table: each of these counted as little or nothing before and was taken."""
        loop = "\nvoid main() {\n    vec4 c = vec4(0.0);\n    for (int i = 0; i < 9; ++i) { c += IMG_NORM_PIXEL(inputImage, vec2(float(i) / 9.0)); }\n    gl_FragColor = c / 9.0;\n}\n"
        read = "IMG_NORM_PIXEL(inputImage, vec2(0.5))"
        taps = [PICTURE, {"NAME": "taps", "TYPE": "float", "MIN": 1, "MAX": 20, "DEFAULT": 4}]

        def count(body, inputs=None):
            return E.estimate(S.parse(fx(inputs, body), S.FILTER))

        def refused(body, why, inputs=None):
            e = count(body, inputs)
            self.assertEqual((e["sure"], e["weight"]), (False, "heavy"), body[:100])
            self.assertIn(why, e["why"], body[:100])
            with self.assertRaises(S.ShaderError) as c:
                E.Effects.check_upload(self.engine(), fx(inputs, body).encode())
            self.assertIn(why, str(c.exception))
        for body, why in (
            (loop.replace("for (int i = 0; i < 9; ++i)", "int i = 0; while (i++ < 9)"), "`while` loop"),
            (loop.replace("for (int i = 0; i < 9; ++i)", "while (true)"), "`while` loop"),
            (loop.replace("for (int i = 0; i < 9; ++i) {", "int i = 0; do {").replace("9.0)); }", "9.0)); } while (i++ < 9);"), "loop"),
            (loop.replace("int i = 0; i < 9; ++i", ";;"), "does not say how often it runs"),
            (loop.replace("i < 9", "i != 9"), "does not say how often it runs"),
            (loop.replace("i < 9", "i < n"), "does not say how often it runs"),                          # a name that is no constant
            (loop.replace("i < 9", "i < 9 * 4000"), "does not say how often it runs"),                    # arithmetic in the limit
            (loop.replace("i < 9", "i < 2, i < 100000"), "does not say how often it runs"),               # the last number used to win
            (loop.replace("i < 9", "i < 100000 || i < 2"), "does not say how often it runs"),
            (loop.replace("++i", "i += 0"), "does not say how often it runs"),
            (loop.replace("++i", "i--"), "does not say how often it runs"),                               # counts away from its limit
            (loop.replace("++i", "i *= 1"), "does not say how often it runs"),
            (loop.replace("++i", ""), "does not say how often it runs"),
            (loop.replace("int i = 0", "int i = k"), "does not say how often it runs"),
            (loop.replace("{ c +=", "{ i -= 1; c +=", 1), "counter (i) is changed inside the loop"),      # a loop that never ends
            (loop.replace("{ c +=", "{ i = 0; c +=", 1), "counter (i) is changed inside the loop"),
            (loop.replace("{ c +=", "{ --i; c +=", 1), "counter (i) is changed inside the loop"),
            ("\nvoid back(inout int n) { n -= 1; }" + loop.replace("{ c +=", "{ back(i); c +=", 1), "handed to back"),
            ("\n#define N 9\n#undef N\n#define N k" + loop.replace("i < 9", "i < N"), "does not say how often it runs"),
            ("\n#define LOOP for (int i = 0; i < 100000; ++i)" + loop.replace("for (int i = 0; i < 9; ++i)", "LOOP"), "a #define holds a loop"),
            ("\n#define W while" + BODY, "a #define holds a loop"),
            ("\n#define TAP(p) IMG_NORM_PIXEL(inputImage, p)" + BODY, "a #define reads the picture"),
            ("\n#define BACK(n) n -= 1" + loop.replace("{ c +=", "{ BACK(i); c +=", 1), "handed to BACK"),
            ("\n#define BACK i -= 1" + loop.replace("{ c +=", "{ BACK; c +=", 1), "handed to BACK"),
            ("\n#define BACK(n) n -= 1\n#define AGAIN(n) BACK(n)" + loop.replace("{ c +=", "{ AGAIN(i); c +=", 1), "handed to AGAIN"),
            (loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int n = 9; n = 100000;").replace("i < 9", "i < n"), "does not say how often it runs"),
            (loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int n = 9; n *= 9000;").replace("i < 9", "i < n"), "does not say how often it runs"),
            ("\nvoid more(inout int n) { n = 100000; }" + loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int n = 9; more(n);").replace("i < 9", "i < n"),
             "does not say how often it runs"),
            (loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int n = int(k * 9000.0);").replace("i < 9", "i < n"), "does not say how often it runs"),
            ("\nfloat a(float x) { return a(x); }" + BODY.replace("* k", "* a(k)"), "calls itself"),
            # a name that means two things is no number: a constant, and a function's argument of the same name
            ("\nconst int N = 2;\nvec4 blur(int N) { vec4 s = vec4(0.0); for (int j = 0; j < N; j++) s += IMG_NORM_PIXEL(inputImage, vec2(0.5)); return s; }"
             + BODY.replace("* k", "* k + blur(100000)"), "does not say how often it runs"),
            ("\nvec4 blur(float k) { vec4 s = vec4(0.0); for (float j = 0.0; j < k; j++) s += IMG_NORM_PIXEL(inputImage, vec2(0.5)); return s; }"
             + BODY.replace("* k", "* k + blur(100000.0)"), "does not say how often it runs"),
            ("\nfloat a(float x);\nfloat b(float x) { return a(x); }\nfloat a(float x) { return b(x); }" + BODY.replace("* k", "* a(k)"), "call each other in a circle"),
            (loop.replace("{ c +=", "{ for (int a = 0; a < 2; a++) for (int b = 0; b < 2; b++) for (int d = 0; d < 2; d++) for (int e = 0; e < 2; e++) c +=", 1), "more than 4 deep"),
            ("\n" + "".join("float f%d(float x) { return x; }\n" % n for n in range(70)) + BODY, "more than 64 functions"),
            (BODY.replace("}", "}}"), "brackets do not match"),
        ):
            refused(body, why)
        refused(loop.replace("i < 9", "i < int(speed)"), "does not say how often it runs", taps)       # no such input
        refused("\n#define taps 100000" + loop.replace("i < 9", "i < int(taps)"), "does not say how often it runs", taps)     # a second meaning for an input
        # what is counted, and how: loops in a row add up, loops in loops multiply, a function counts wherever its
        # name appears (through a #define too), an input counts at its largest, the dearest of two meanings counts
        for body, inputs, want in (
            (loop, None, (9, 9)),
            (loop.replace("    gl_FragColor", "    for (int j = 0; j < 9; ++j) { c += " + read + "; }\n    gl_FragColor"), None, (18, 18)),
            (loop.replace("    gl_FragColor", "    for (int j = 0; j < 200; ++j) { c.r += 0.001; }\n    gl_FragColor"), None, (9, 209)),
            (loop.replace("{ c +=", "{ for (int j = 8; j >= 0; j -= 2) c +=", 1), None, (45, 45)),
            (loop.replace("i < 9", "i < int(taps)"), taps, (20, 20)),
            (loop.replace("i < 9", "i <= taps").replace("int i = 0", "float i = 0.0").replace("++i", "i += 0.5").replace("float(i)", "i"), taps, (41, 41)),
            ("\nconst int N = 12;" + loop.replace("i < 9", "i < N"), None, (12, 12)),
            # forms honest files use (each is in a file of ISF-Files): a counter declared before its loop, a local
            # name for an input, two functions of one name where one calls the other, a #define that assigns
            (loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int i;").replace("int i = 0; i < 9", "i = 0; i < 9"), None, (9, 9)),
            (loop.replace("vec4 c = vec4(0.0);", "vec4 c = vec4(0.0); int n = int(taps);").replace("i < 9", "i < n"), taps, (20, 20)),
            ("\nfloat lum(vec3 c) { return c.r + " + read + ".r; }\nfloat lum(vec4 c) { return lum(c.rgb); }" + BODY.replace("* k", "* lum(vec4(k))"), None, (2, 1)),
            ("\n#define SORT(a, b) t = a; a = min(a, b); b = max(t, b);" + loop.replace("{ c +=", "{ vec4 t; vec4 u = c; SORT(c, u) c +=", 1), None, (9, 9)),
            ("\n#define N 12\n#undef N\n#define N 40" + loop.replace("i < 9", "i < N"), None, (40, 40)),
            ("\nvec4 tap(vec2 p) { return " + read + "; }\n#define T(p) tap(p)" + loop.replace("IMG_NORM_PIXEL(inputImage, vec2(float(i) / 9.0))", "T(vec2(0.5))"), None, (9, 9)),
            ("\nvec4 tap(vec2 p) { vec4 s = vec4(0.0); for (int j = 0; j < 5; j++) s += " + read + "; return s; }" + loop.replace("IMG_NORM_PIXEL(inputImage, vec2(float(i) / 9.0))", "tap(vec2(0.5)) + tap(vec2(0.1))"), None, (90, 90)),
            ("\nvoid main() {\n    vec4 c = vec4(0.0);\n    for (int i = 0; i < 4; i++) if (k > 1.0) c += " + read + "; else c += " + read + " + " + read + ";\n    gl_FragColor = c;\n}\n", None, (12, 4)),
            ("\nvoid main() {\n    vec4 c = vec4(0.0);\n    for (int i = 0; float(i) < 0.0; i++) c += " + read + ";\n    gl_FragColor = c + " + read + ";\n}\n", None, None),
        ):
            e = count(body, inputs)
            if want is None:
                self.assertFalse(e["sure"], body[:80])
            else:
                self.assertEqual((e["reads"], e["rounds"], e["sure"]), want + (True,), body[:120])
        # over the limits: counted, and refused for being too much
        for body, why in ((loop.replace("i < 9", "i < 65"), "about 65 times"), (loop.replace("i < 9", "i < 100000"), "about 100000 times"),
                          (loop.replace("    gl_FragColor", "    for (int j = 0; j < 300; ++j) { c.r += 0.001; }\n    gl_FragColor"), "about 309 rounds")):
            with self.assertRaises(S.ShaderError) as c:
                E.Effects.check_upload(self.engine(), fx(None, body).encode())
            self.assertIn(why, str(c.exception))

    def engine(self):
        class Stub:
            read = staticmethod(lambda data: S.parse(data, S.FILTER))
        return Stub()

    def test_counting_a_hostile_text_is_quick_and_never_an_error_of_the_box(self):
        """The count runs on the panel's own thread and a Python regular expression holds the whole interpreter. Each
        of these took seconds or raised RecursionError (a 500 where a 422 belongs)."""
        loop = "for (int i = 0; i < 2; i++) { "
        calls = "".join("float f%d(float x) { return %s; }\n" % (n, " + ".join("f%d(x)" % m for m in range(58) if m != n)) for n in range(58))
        for name, body in (("1,500 loops inside each other", "\nvoid main() {\n" + "for(;;){" * 1500 + "}" * 1500 + "\n gl_FragColor = vec4(1.0);\n}\n"),
                           ("900 counted loops inside each other", "\nvoid main() {\n" + loop * 900 + "}" * 900 + "\n gl_FragColor = vec4(1.0);\n}\n"),
                           ("3,000 brackets", "\nvoid main() {\n gl_FragColor = vec4(" + "(" * 3000 + "1.0" + ")" * 3000 + ");\n}\n"),
                           ("a #define and 30,000 spaces", "\n#define a" + " " * 30000 + "\n" + BODY),
                           ("a declaration and 30,000 spaces", BODY.replace("void main() {", "void main() {\n    float a" + " " * 30000 + "= k;")),
                           ("58 functions that all call each other", "\n" + calls + BODY),
                           ("60 functions in a chain, each called twice", "\nfloat g0(float x) { return x; }\n" + "".join(
                               "float g%d(float x) { return g%d(x) + g%d(x); }\n" % (n, n - 1, n - 1) for n in range(1, 60)) + BODY.replace("* k", "* g59(k)")),
                           ("2,000 small functions", "\n" + "".join("void e%d(){}\n" % n for n in range(2000)) + BODY),
                           ("3,000 ifs in a row", "\nvoid main() {\n" + "if(k>1.)" * 3000 + ";\n gl_FragColor = vec4(1.0);\n}\n"),
                           ("else upon else", "\nvoid main() {\n for (int i = 0; i < 2; i++) " + "if(k>1.);else " * 2000 + ";\n gl_FragColor = vec4(1.0);\n}\n")):
            text = fx(None, body)
            self.assertLessEqual(len(text), S.MAX_SOURCE, name)
            began = time.monotonic()
            try:
                parsed = S.parse(text, S.FILTER)
            except S.ShaderError:
                parsed = None                           # refused before the count: as good
            if parsed is not None:
                e = E.estimate(parsed)
                self.assertIs(E.estimate(parsed), e)    # made once for a file's content
                self.assertIn(e["sure"], (True, False))
                try:
                    E.Effects.check_upload(self.engine(), text.encode())
                except S.ShaderError:
                    pass
            self.assertLess(time.monotonic() - began, 1.0, name)

    def test_the_work_of_a_filter_is_counted_from_its_text(self):
        def count(body, inputs=None):
            e = E.estimate(S.parse(fx(inputs, body), S.FILTER))
            return e["reads"], e["rounds"], e["weight"], e["sure"]
        self.assertEqual(count(BODY), (1, 1, "light", True))
        loop = "\nvoid main() {\n    vec4 c = vec4(0.0);\n    for (int i = 0; i < 9; ++i) { c += IMG_NORM_PIXEL(inputImage, vec2(float(i) / 9.0)); }\n    gl_FragColor = c / 9.0;\n}\n"
        self.assertEqual(count(loop), (9, 9, "medium", True))
        self.assertEqual(count(loop.replace("i < 9", "i <= 9")), (10, 10, "medium", True))
        self.assertEqual(count(loop.replace("int i = 0; i < 9", "int i = -4; i <= 4")), (9, 9, "medium", True))
        self.assertEqual(count(loop.replace("++i", "i += 3")), (3, 3, "medium", True))
        nested = loop.replace("{ c +=", "{ for (int j = 0; j < 9; ++j) c +=").replace("9.0)); }", "9.0)); }")
        self.assertEqual(count(nested), (81, 81, "heavy", True))
        self.assertEqual(count("\n#define N 30" + loop.replace("i < 9", "i < N")), (30, 30, "heavy", True))
        by_input = [PICTURE, {"NAME": "taps", "TYPE": "float", "MIN": 1, "MAX": 20, "DEFAULT": 4}]
        self.assertEqual(count(loop.replace("i < 9", "i < int(taps)"), by_input), (20, 20, "heavy", True))    # an input counts at its MAX
        self.assertEqual(count(loop.replace("i < 9", "i < n"))[2:], ("heavy", False))                         # a length the text does not say
        self.assertEqual(count(loop.replace("for (int i = 0; i < 9; ++i)", "int i = 0; while (i++ < 9)"))[2:], ("heavy", False))
        helper = "\nfloat light(vec2 p) { return IMG_NORM_PIXEL(inputImage, p).r; }\nvoid main() {\n    gl_FragColor = vec4(light(vec2(0.1)) + light(vec2(0.2)) + light(vec2(0.3)));\n}\n"
        self.assertEqual(count(helper), (3, 1, "medium", True))
        self.assertEqual(count("\nvoid main() {\n    gl_FragColor = IMG_THIS_PIXEL(inputImage) + IMG_NORM_PIXEL(inputImage, vec2(0.5)) + IMG_PIXEL(inputImage, vec2(1.0));\n}\n")[:3],
                         (3, 1, "medium"))


def f32(x):
    """A number as the player and the GPU hold it: 32 bits."""
    return struct.unpack("f", struct.pack("f", x))[0]


def player_size(line, w, h):
    """What mpv makes of a //!WIDTH or //!HEIGHT line for a picture of w x h: its own arithmetic (each value before
    the sign that uses it, in 32-bit numbers, a comparison gives 1 or 0), then rounded to a whole number."""
    words = line.split()[1:]
    assert len(words) <= 32, "mpv reads at most 32 words of a size"
    stack = []
    for word in words:
        if word in ("HOOKED.w", "HOOKED.h"):
            stack.append(f32(w if word.endswith("w") else h))
        elif word == "!":
            stack.append(0.0 if stack.pop() else 1.0)
        elif word in "+-*/<>":
            b, a = stack.pop(), stack.pop()
            stack.append(f32({"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0.0, ">": float(a > b), "<": float(a < b)}[word]))
        else:
            stack.append(f32(float(word)))
    assert len(stack) == 1, line
    return int(math.floor(stack[0] + 0.5))


def shader_size(text, w, h):
    """What the generated code takes for RENDERSIZE in a picture of w x h, from its own lines (32-bit numbers)."""
    assert "    PVJ_HP float pvj_short = min(HOOKED_size.x, HOOKED_size.y);\n" in text
    m = re.search(r"if \(pvj_short > ([0-9.]+)\) pvj_work = floor\(HOOKED_size - HOOKED_size \* \(pvj_short - \1\) / pvj_short \+ 0\.5\);", text)
    side, cap = min(w, h), float(m.group(1))
    if not side > cap:
        return w, h
    return tuple(int(math.floor(f32(f32(n - f32(f32(n * f32(side - cap)) / side)) + 0.5))) for n in (w, h))


SIZES = [(1920, 1080), (1280, 720), (960, 540), (720, 576), (720, 480), (640, 360), (320, 180), (3840, 2160), (4096, 2160), (1440, 1080), (1998, 1080),
         (2560, 1080), (1080, 1080), (1206, 2622), (2622, 1206), (1080, 1920), (720, 1280), (1921, 1081), (853, 480), (1366, 768), (721, 1281), (2, 2), (1, 1),
         (541, 961), (16384, 16384)]


class WorkingSizeTest(unittest.TestCase):
    """Effect detail: the arithmetic of the cap, in the three places it is done (this file's numbers for the panel,
    the player's size lines, the code's RENDERSIZE), which have to agree for every clip."""
    def test_the_shorter_side_is_capped_the_shape_is_kept_and_a_small_clip_is_not_touched(self):
        want = {((1920, 1080), 720): (1280, 720), ((1920, 1080), 540): (960, 540), ((1920, 1080), 1080): (1920, 1080), ((1920, 1080), None): (1920, 1080),
                ((1280, 720), 720): (1280, 720), ((1280, 720), 540): (960, 540), ((960, 540), 540): (960, 540), ((720, 576), 540): (675, 540),
                ((3840, 2160), 720): (1280, 720), ((3840, 2160), 1080): (1920, 1080), ((1440, 1080), 720): (960, 720), ((2560, 1080), 720): (1707, 720),
                ((1080, 1920), 720): (720, 1280), ((1206, 2622), 720): (720, 1565), ((2622, 1206), 720): (1565, 720), ((1206, 2622), 540): (540, 1174),
                ((1080, 1080), 720): (720, 720), ((1921, 1081), 540): (960, 540), ((640, 360), 540): (640, 360), ((1, 1), 540): (1, 1)}
        for (size, lines), out in want.items():
            self.assertEqual(E.work_size(size[0], size[1], lines), out, (size, lines))
        rng = random.Random(7)
        for w, h in SIZES + [(rng.randint(2, 4096), rng.randint(2, 4096)) for _ in range(400)]:
            for lines in (None, 1, 90, 100, 360, 540, 720, 1080, 2160):
                ow, oh = E.work_size(w, h, lines)
                what = (w, h, lines)
                if lines is None or min(w, h) <= lines:
                    self.assertEqual((ow, oh), (w, h), what)                           # at or below the cap: not scaled at all
                    continue
                self.assertEqual(min(ow, oh), lines, what)                             # the shorter side is the cap, exactly
                self.assertTrue(ow <= w and oh <= h and ow >= 1 and oh >= 1, what)     # never larger than the clip
                self.assertLessEqual(abs(max(ow, oh) - max(w, h) * lines / min(w, h)), 0.5 + 1e-9, what)    # the shape, to the nearest pixel
                self.assertTrue(ow == oh or (ow > oh) == (w > h), what)               # what lies stays lying
                self.assertEqual(E.work_size(h, w, lines), (oh, ow), what)             # turned by a quarter: the same picture, turned

    def test_a_turned_clip_is_capped_like_the_same_clip_stored_upright(self):
        # The player turns a clip that is to be shown turned (rotate 90 or 270: a phone's upright video, or the
        # panel's Rotate) before the stage an effect hooks, so the hook meets it standing. Stored lying and turned,
        # or stored standing: the same number of pixels, and one text for both, which does not ask which it is.
        width, height = E.size_lines(720)
        for lines in (540, 720):
            lying, standing = E.work_size(1920, 1080, lines), E.work_size(1080, 1920, lines)
            self.assertEqual(lying[0] * lying[1], standing[0] * standing[1])
            self.assertEqual(sorted(lying), sorted(standing))
        for w, h in ((1920, 1080), (2622, 1206), (1280, 720), (720, 576), (1000, 1000)):
            for turn in (0, 90, 180, 270):
                met = (h, w) if turn % 180 else (w, h)                                  # what the hook meets
                got = (player_size(width, *met), player_size(height, *met))
                self.assertEqual(got, E.work_size(*met, 720), (w, h, turn))
                self.assertEqual(sorted(got), sorted(E.work_size(w, h, 720)), (w, h, turn))        # the same picture, turned

    def test_the_player_the_code_and_the_panel_come_to_the_same_size_for_every_clip(self):
        p = S.parse(GOOD, S.FILTER)
        rng = random.Random(11)
        sizes = SIZES + [(rng.randint(2, 4096), rng.randint(2, 4096)) for _ in range(300)]
        for lines in (1, 90, 100, 360, 540, 720, 1080):
            width, height = E.size_lines(lines)
            self.assertTrue(width.startswith("//!WIDTH ") and height.startswith("//!HEIGHT "))
            self.assertEqual((len(width.split()) - 1, len(height.split()) - 1), (30, 30))       # mpv reads at most 32 words of a size
            text = E.translate(p, lines=lines)
            self.assertEqual((text.count(width + "\n" + height + "\n"), text.count("PVJ_HP vec2 pvj_work;")), (2, 2))
            for w, h in sizes:                          # lying, standing and square alike: one text for every shape
                want = E.work_size(w, h, lines)
                self.assertEqual((player_size(width, w, h), player_size(height, w, h)), want, (w, h, lines))
                if max(w, h) <= 4096:               # beyond that 32 bits do not hold the products exactly; a pixel either way there
                    self.assertEqual(shader_size(text, w, h), want, (w, h, lines))

    def test_what_the_text_holds_for_a_cap_for_amount_0_and_for_the_old_half(self):
        p = S.parse(GOOD, S.FILTER)
        plain = E.translate(p, desc="nxlx effect 1 1")
        self.assertEqual(E.translate(p, lines=None, desc="nxlx effect 1 1"), plain)                 # no cap: nothing of it in the text
        capped = E.translate(p, lines=720, desc="nxlx effect 1 1")
        self.assertEqual(capped.count(
            "//!WIDTH HOOKED.w HOOKED.w HOOKED.h > HOOKED.h 720 > * HOOKED.w * HOOKED.h 720 - * HOOKED.h / - HOOKED.w HOOKED.h > ! HOOKED.w 720 > * HOOKED.w 720 - * -\n"
            "//!HEIGHT HOOKED.h HOOKED.w HOOKED.h > ! HOOKED.w 720 > * HOOKED.h * HOOKED.w 720 - * HOOKED.w / - HOOKED.w HOOKED.h > HOOKED.h 720 > * HOOKED.h 720 - * -\n"), 2)
        self.assertEqual(capped.count("    if (pvj_short > 720.0) pvj_work = floor("), 2)
        self.assertIn("vec4 pvj_src = HOOKED_tex(HOOKED_pos);", capped)                # the mix is with the clip itself, read where this pixel lies
        # the old control: true is 540 lines at most, whatever the cap; false follows the cap
        self.assertEqual(E.translate(p, controls={"half": True}, lines=720), E.translate(p, controls={"half": True}, lines=540))
        self.assertEqual(E.translate(p, controls={"half": True}), E.translate(p, controls={"half": True}, lines=540))
        self.assertIn(E.size_lines(360)[1], E.translate(p, controls={"half": True}, lines=360))     # a lower cap stays
        self.assertEqual(E.translate(p, controls={"half": False}, lines=720, desc="nxlx effect 1 1"), capped)
        self.assertEqual(E.HALF_LINES, 540)
        # amount 0: the player is told to leave both hooks out, so the picture is the clip itself, whatever the cap
        for lines in (None, 540):
            zero = E.translate(p, controls={"amount": 0}, lines=lines)
            self.assertEqual(zero.count("//!WHEN 0\n//!DESC nxlx effect\n"), 2, lines)
            self.assertNotIn("//!WHEN", E.translate(p, controls={"amount": 0.01}, lines=lines))
        for bad in (0, -1, 8193, 720.0, True, "720"):
            with self.assertRaises(S.ShaderError, msg=bad):
                E.translate(p, lines=bad)
        # TIME does not know of the cap: every line of the clock is the same with and without one, at any speed
        moves = S.parse(MOVES, S.FILTER)
        clock = lambda text: [l for l in text.split("\n") if "pvj_time" in l or "TIMEDELTA" in l or "FRAMEINDEX" in l or "pvj_hi" in l or "pvj_lo" in l]
        for speed in (1.0, 0.5):
            picture = E.clean_picture("bt.709", "limited", 29.97)
            free = clock(E.translate(moves, controls={"speed": speed}, picture=picture))
            self.assertIn("    pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / 29.97%s;" % ("" if speed == 1.0 else " * 0.5"), free)
            for lines in (540, 720):
                self.assertEqual(clock(E.translate(moves, controls={"speed": speed}, picture=picture, lines=lines)), free, (speed, lines))
            self.assertEqual(clock(E.translate(moves, controls={"speed": speed, "half": True}, picture=picture)), free)

    def test_what_automatic_gives_each_board_and_what_a_box_has_when_nobody_chose(self):
        self.assertEqual(E.DETAILS, ("auto", 540, 720, "full"))
        self.assertEqual({b: E.default_detail(b) for b in ("pi3", "pi4", "pi5", "x86", "pi-other", "arm-other", None)},
                         {"pi3": "auto", "pi4": "auto", "pi5": "full", "x86": "full", "pi-other": "auto", "arm-other": "auto", None: "full"})
        for board in ("pi5", "x86", None, "nope"):                                     # not measured: Automatic scales nothing there
            self.assertIsNone(E.auto_lines(board, "fx-wash.fs"))
            self.assertIsNone(E.cap_lines("auto", board, "fx-wash.fs"))
        for board, row in E.AUTO.items():
            self.assertIn(row["lines"], (540, 720), board)
            self.assertIn(row["other"], (540, 720), board)
            self.assertLessEqual(row["other"], row["lines"], board)                    # an upload nobody measured never gets more than a bundled filter
            for sid, lines in row["lower"].items():
                self.assertIn(sid, E.PI4, sid)
                self.assertTrue(lines in (540, 720) and lines < row["lines"], sid)
            self.assertEqual(E.auto_lines(board, "fx-wash.fs", bundled=False), row["other"])
            self.assertEqual(E.auto_lines(board), row["other"])
        self.assertFalse(E.AUTO["pi3"]["measured"])                                    # the careful value, should effects ever run there
        # The Pi 4's row is made from what the board measured, by the rule: the largest cap at which the filter held a
        # 1080 line clip (fewer than half a dropped frame a second), the heavier ones one step down. Two that held at
        # 720 lines with the GPU nearly full are stepped down too, by the owner's choice of 2026-10-08: PI4_HEADROOM.
        self.assertTrue(E.AUTO["pi4"]["measured"])
        self.assertEqual(sorted(E.PI4_720), sorted(E.PI4))
        for sid, (ms, dropped) in E.PI4_720.items():
            full, half = E.PI4[sid][1]
            self.assertTrue(half[0] < ms < full[0] and half[1] <= dropped <= full[1], sid)     # between the 540 line and the full size numbers
            want = 720 if dropped < E.HOLDS and sid not in E.PI4_HEADROOM else 540
            self.assertEqual(E.auto_lines("pi4", sid), want, sid)
            self.assertLess((E.PI4_720[sid][1] if want == 720 else half[1]), E.HOLDS, sid)    # and at what Automatic gives it, it held
        self.assertEqual(E.PI4_HEADROOM, {"fx-edge-glow.fs", "isf-corner-color-tint.fs"})
        for sid in sorted(E.PI4_HEADROOM):
            self.assertIn(sid, E.PI4_720, sid)                                         # only a filter that was measured,
            self.assertLess(E.PI4_720[sid][1], E.HOLDS, sid)                           # and that held at 720: one that did not is the rule's
            # what the owner chose for the box is not overridden, and the choice is a Pi 4's only
            self.assertEqual((E.cap_lines("auto", "pi4", sid), E.cap_lines(540, "pi4", sid), E.cap_lines(720, "pi4", sid), E.cap_lines("full", "pi4", sid)),
                             (540, 540, 720, None), sid)
            self.assertEqual((E.auto_lines("pi3", sid), E.auto_lines("pi5", sid)), (540, None), sid)
        self.assertEqual((E.PI4_720["fx-edge-glow.fs"], E.PI4_720["isf-corner-color-tint.fs"]), ((9.9, 0.0), (9.2, 0.0)))     # the measured numbers stay
        self.assertEqual([E.auto_lines("pi4", sid) for sid in ("fx-edge-glow.fs", "isf-corner-color-tint.fs", "isf-edge-blowout.fs", "fx-grade.fs")],
                         [540, 540, 540, 720])
        self.assertEqual(len([sid for sid in E.PI4_720 if E.auto_lines("pi4", sid) == 720]), 34)
        self.assertEqual(E.AUTO["pi4"], {"lines": 720, "lower": {"fx-edge-glow.fs": 540, "isf-corner-color-tint.fs": 540, "isf-edge-blowout.fs": 540},
                                         "other": 540, "measured": True})
        self.assertLessEqual(E.AUTO["pi3"]["lines"], E.AUTO["pi4"]["lines"])
        self.assertEqual(E.AUTO["pi3"]["lines"], 540)
        for detail, want in (("full", None), (540, 540), (720, 720)):
            for board in ("pi4", "x86"):
                self.assertEqual(E.cap_lines(detail, board, "fx-wash.fs"), want)
                self.assertEqual(E.cap_lines(detail, board, "fx-wash.fs", half=True), 540)       # the old half: 540 at most
        self.assertEqual(E.cap_lines("auto", "pi4", "fx-wash.fs"), E.AUTO["pi4"]["lower"].get("fx-wash.fs", E.AUTO["pi4"]["lines"]))
        for bad in (True, False, None, 540.0, 720.0, "720", "Auto", 1080, 360, [], {}):
            with self.assertRaises(ValueError, msg=bad):
                E.clean_detail(bad)
        self.assertEqual([E.clean_detail(d) for d in E.DETAILS], list(E.DETAILS))


class FakeMpv:
    """What the panel asks of an mpv, with nothing behind it: properties in a dict, loadfile sets the path, and the
    passes it "drew" are the shader texts it has loaded."""
    def __init__(self):
        self.pid = 1000
        self.video = {"colormatrix": "bt.709", "colorlevels": "limited", "pixelformat": "yuv420p"}
        self.fps, self.pass_ns, self.vo, self.down, self.drops = 25.0, 1500000, "gpu", False, 0
        self.decoder_drops = 0      # the player's second count of dropped frames (the decoder's own)
        self.container = True       # False: a stream or a live input, with no frame rate of its own, only the player's estimate
        self.commands = []
        self.props = {}
        self.restart(same=True)

    def restart(self, same=False):
        """A new mpv: another process, nothing loaded, nothing playing."""
        self.pid += 0 if same else 1
        self.props = {"path": None, "glsl-shaders": [], "fbo-format": "auto", "pause": False, "time-pos": 1.0, "brightness": 0, "keepaspect": True}

    def request(self, *command):
        if self.down:
            raise PlayerError("player is not running")
        self.commands.append(command)
        if command[0] == "get_property":
            name = command[1]
            if name == "pid":
                return self.pid
            if name == "current-vo":
                return self.vo
            if name in ("video-params", "container-fps", "estimated-vf-fps"):
                if self.props["path"] is None or self.video is None or (name == "container-fps" and not self.container):
                    raise PlayerError("mpv: property unavailable")
                return dict(self.video) if name == "video-params" else (self.fps() if callable(self.fps) else self.fps)
            if name == "vo-passes":
                fresh = []
                for path in self.props["glsl-shaders"]:
                    try:
                        with open(path) as f:
                            desc = re.search(r"//!DESC (.*)", f.read()).group(1)
                    except (OSError, AttributeError):
                        continue
                    fresh.append({"desc": "user shader: %s (native)" % desc, "avg": self.pass_ns, "last": self.pass_ns})
                return {"fresh": fresh, "redraw": []}
            if name in ("frame-drop-count", "decoder-frame-drop-count"):
                return self.drops if name == "frame-drop-count" else self.decoder_drops
            return self.props.get(name)
        if command[0] == "set_property":
            self.props[command[1]] = command[2]
        elif command[0] == "loadfile":
            self.props["path"] = command[1]
        elif command[0] == "stop":
            self.props["path"] = None
        return None

    @property
    def loaded(self):
        return [os.path.basename(p) for p in self.props["glsl-shaders"]]


class Base(ServerBase):
    def setUp(self):
        super().setUp()
        self.mpv = FakeMpv()
        self.player = self.api.player = Player(rundir=self.rundir)
        self.player.ipc = self.mpv
        self.api.registry.set_enabled("shaders", True)
        self.fx, self.gen = self.api.effects, self.api.shaders
        for engine in (self.fx, self.gen):
            engine.log = lambda *_: None
            engine._tap = FakeTap
            engine.changer._use_thread = False
        self.api.vibes._use_thread = False
        self.api.vibes.log = lambda *_: None
        FakeTap.lines = []
        self.player.play(["/media/a.mp4"])

    def texts(self):
        return sorted(n for n in os.listdir(self.rundir) if n.startswith("effect-"))

    def text(self):
        with open(self.mpv.props["glsl-shaders"][0]) as f:
            return f.read()

    def pump(self):
        self.fx.changer._last = -1e9                    # as if the fifth of a second between two texts had passed
        self.fx._switched = -1e9                        # and the gap between two switches (tested by itself below)
        self.assertTrue(self.fx.changer.pump(), "nothing was due")

    def state(self):
        return self.fx.state()


class LibraryTest(Base):
    def test_the_bundled_filters_are_the_projects_own_and_the_pack(self):
        rows = self.state()["effects"]
        own = [s for s in rows if s["pack"] == "nxlx"]
        pack = [s for s in rows if s["pack"] == "isf-files"]
        self.assertEqual([s["name"] for s in own], OWN)
        self.assertEqual(len(own) + len(pack), len(rows))
        self.assertTrue(20 <= len(pack) <= 32, len(pack))
        for s in rows:
            self.assertIsNone(s["error"], s["id"])
            self.assertEqual(s["source"], "bundled")
            self.assertIn(s["estimate"]["weight"], ("light", "medium"), s["id"])       # nothing bundled is heavy by the count
            self.assertTrue(s["estimate"]["sure"] and s["estimate"]["reads"] <= 12 and s["estimate"]["rounds"] <= 16, s["id"])
            self.assertNotIn("vibes", s)                                               # an effect is never in a rotation
            self.assertTrue(all("value" in i for i in s["inputs"]), s["id"])
            self.assertLessEqual(len(s["inputs"]), S.MAX_INPUTS)
        self.assertEqual(sorted(s["name"] for s in rows if s["moves"]), MOVING)
        self.assertEqual({s["speed_max"] for s in rows if s["moves"]}, {1.0})
        self.assertEqual({s["speed_max"] for s in rows if not s["moves"] and not s["flashes"]}, {None})
        self.assertEqual([s["name"] for s in pack if s["credit"] == ""], [])           # every pack file names its author
        # none of it is a generator, and no generator is an effect
        self.assertEqual([s["id"] for s in self.gen.library() if s["id"].startswith("fx-")], [])
        self.assertEqual(self.gen.state()["shaders"][0]["id"][:5], "nxlx-")

    def test_every_bundled_filter_says_what_a_pi_4_measured_and_an_upload_keeps_its_count(self):
        """All 37, each with the class and the numbers a Pi 4 measured (2026-10-05): over a 1080 line clip and a 720
        line clip, at full size and with Half resolution. The class is what the two full-size drop rates make."""
        rows = {s["id"]: s for s in self.state()["effects"]}
        self.assertEqual(sorted(E.PI4), sorted(rows))                                  # every bundled file, and nothing else
        for sid, (weight, large, small) in E.PI4.items():
            self.assertEqual(E.weigh(small[0][1], large[0][1]), weight, sid)
            for full, half in (large, small):                                          # a quarter of the pixels never costs more
                self.assertTrue(0 < half[0] < full[0] < 100 and 0 <= half[1] <= full[1] < 30, sid)
            self.assertTrue(small[0][0] < large[0][0] and small[0][1] <= large[0][1], sid)         # nor does a smaller clip
            row = rows[sid]
            self.assertEqual(row["weight"], weight, sid)
            self.assertEqual(row["measured"], {"board": "pi4",
                                               "pass_ms": {"1080": large[0][0], "1080_half": large[1][0], "720": small[0][0], "720_half": small[1][0],
                                                           "1080_at_720": E.PI4_720[sid][0]},
                                               "drops_per_second": {"1080": large[0][1], "1080_half": large[1][1], "720": small[0][1], "720_half": small[1][1],
                                                                    "1080_at_720": E.PI4_720[sid][1]},
                                               "holds": 1080 if weight == "light" else (720 if weight == "medium" else None),
                                               "holds_half": 1080 if large[1][1] < E.HOLDS else (720 if small[1][1] < E.HOLDS else None),
                                               "works_at": E.auto_lines("pi4", sid)}, sid)
            self.assertEqual(row["estimate"]["weight"], E.estimate(self.fx._parsed(self.fx._path(sid)[0])[0])["weight"], sid)       # the count is still told
        self.assertEqual((E.weigh(0, 0), E.weigh(0, 0.49), E.weigh(0, 0.5), E.weigh(0.49, 9), E.weigh(0.5, 9)), ("light", "light", "medium", "medium", "heavy"))
        self.assertEqual((E.HOLDS, E.HOLDS), (L.HOLDS, L.Guard.TIGHT))                 # "holds" is where the guard says "ok", as for a generator
        # What the Pi 4 said, named: one filter held a 1080 line clip at full size, none dropped frames over a 720
        # line clip, and every one held the 1080 line clip with Half resolution.
        self.assertEqual({w: sorted(sid for sid, row in E.PI4.items() if row[0] == w) for w in ("light", "heavy")}, {"light": ["isf-duotone.fs"], "heavy": []})
        self.assertEqual({rows[sid]["measured"]["holds_half"] for sid in E.PI4}, {1080})
        # the count from the text and the measurement disagree, which is why the table exists: the heaviest and the
        # third heaviest pass at 1080 lines belong to filters the count calls medium and light
        heaviest = sorted(E.PI4, key=lambda sid: -E.PI4[sid][1][0][0])[:3]
        self.assertEqual([(sid, rows[sid]["estimate"]["weight"]) for sid in heaviest],
                         [("isf-edge-blowout.fs", "medium"), ("fx-edge-glow.fs", "medium"), ("isf-corner-color-tint.fs", "light")])
        self.assertIsNone(E.measured("mine.fs"))
        # an upload was never measured: it keeps the count, also when it is a copy of a bundled file
        with open(os.path.join(E.EFFECTS_DIR, "fx-edge-glow.fs")) as f:
            self.fx.upload("mine.fs", f.read())
        row = next(s for s in self.state()["effects"] if s["id"] == "mine.fs")
        self.assertEqual((row["source"], row["weight"], row["measured"], row["estimate"]["weight"]), ("uploaded", "medium", None, "medium"))

    def test_an_upload_that_cannot_be_counted_is_a_422_with_its_reason_never_a_500(self):
        for body, why in (("\nvoid main() {\n" + "for(;;){" * 1500 + "}" * 1500 + "\n gl_FragColor = vec4(1.0);\n}\n", "does not say how often it runs"),
                          (BODY.replace("void main() {", "void main() {\n    while (true) {}"), "`while` loop"),
                          (BODY.replace("* k", "* k * float(frame)"), "the name frame belongs to the player"),
                          (BODY.replace("* k", "* k * LUMA_gather(vec2(0.5), 0).r"), "LUMA_gather belongs to the player")):
            with self.assertRaises(ApiError) as c:
                self.fx.upload("hostile.fs", fx(None, body))
            self.assertEqual((c.exception.status, why in c.exception.message), (422, True), c.exception.message)
        self.assertNotIn("hostile.fs", [s["id"] for s in self.state()["effects"]])
        # a file that is already on the box and cannot be counted (written there by hand, or by an older version)
        # is listed as heavy with the reason; the list is never an error
        os.makedirs(self.fx.dir, exist_ok=True)
        with open(os.path.join(self.fx.dir, "old.fs"), "w") as f:
            f.write(fx(None, BODY.replace("void main() {", "void main() {\n    while (false) {}")))
        row = next(s for s in self.state()["effects"] if s["id"] == "old.fs")
        self.assertEqual((row["weight"], row["estimate"]["sure"], "`while` loop" in row["estimate"]["why"]), ("heavy", False, True))

    def test_the_projects_own_filters_are_its_own_construction(self):
        for name in OWN:
            with open(os.path.join(E.EFFECTS_DIR, name + ".fs")) as f:
                text = f.read()
            self.assertIn("SPDX-License-Identifier: Apache-2.0", text, name)
            self.assertIn('"CREDIT": "NXLX.Systems and contributors"', text, name)
            for snippet in WELL_KNOWN + ("rgb2hsv", "hsv2rgb", "vec4(0.0, -1.0 / 3.0", "shadertoy", "http"):
                self.assertNotIn(snippet, text, "%s holds %r" % (name, snippet))
            p = S.parse(text, S.FILTER)
            self.assertTrue(4 <= len(p["inputs"]) <= 8, name)
            self.assertTrue(all(i["label"] != i["name"] for i in p["inputs"]), name)   # every control has a plain label
            self.assertTrue(p["description"], name)
            if p["clock"]:
                # TIME is folded into a place in a cycle before it is used, and its rate is capped in the code
                self.assertEqual(re.findall(r"TIME[^;]*", p["body"]), re.findall(r"TIME \* min\([a-z]+, [0-9.]+\)[^;]*", p["body"]), name)
                self.assertIn("fract(TIME * min(", p["body"], name)

    def test_uploads_are_checked_as_filters_and_kept_apart_from_the_generators(self):
        self.fx.upload("mine.fs", GOOD)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "effects", "mine.fs")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "shaders", "mine.fs")))
        row = next(s for s in self.state()["effects"] if s["id"] == "mine.fs")
        self.assertEqual((row["source"], row["pack"], row["weight"]), ("uploaded", "uploads", "light"))
        self.assertEqual([s["id"] for s in self.gen.library() if s["id"] == "mine.fs"], [])
        heavy = fx(body="\nvoid main() {\n    vec4 c = vec4(0.0);\n    for (int i = 0; i < 200; ++i) { c += IMG_NORM_PIXEL(inputImage, vec2(float(i) / 200.0)); }\n    gl_FragColor = c;\n}\n")
        for name, text, status, why in (("gen.fs", "/*{}*/\nvoid main() { gl_FragColor = vec4(1.0); }\n", 422, "added under Shaders"),
                                        ("heavy.fs", heavy, 422, "200 times for every pixel"),
                                        ("spin.fs", heavy.replace("c += IMG_NORM_PIXEL(inputImage, vec2(float(i) / 200.0));", "for (int j = 0; j < 9; ++j) { c.r += 0.1; }"), 422, "1800 rounds"),
                                        ("fx-wash.fs", GOOD, 409, "bundled"), ("isf-mirror.fs", GOOD, 409, "bundled"),
                                        ("../x.fs", GOOD, 400, "named with"), ("mine.fs", GOOD, 409, "already exists")):
            with self.assertRaises(ApiError, msg=name) as c:
                self.fx.upload(name, text)
            self.assertEqual(c.exception.status, status, name)
            self.assertIn(why, c.exception.message, name)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "effects")), ["mine.fs"])
        with self.assertRaises(ApiError) as c:                                         # a filter is not taken as a generator either
            self.gen.upload("mine.fs", GOOD)
        self.assertIn("added under Effects", c.exception.message)
        with self.assertRaises(ApiError) as c:
            self.fx.delete("fx-wash.fs")
        self.assertEqual(c.exception.status, 409)
        self.fx.put("mine.fs")
        self.fx.preset_save("keep")
        self.fx.delete("mine.fs")                                                      # it comes off, and its presets go with it
        self.assertEqual((self.mpv.loaded, self.state()["on"], self.fx.presets()), ([], None, {}))
        self.assertNotIn("shaders", {k for k, v in self.settings.data.items() if k == "shaders" and not v.get("v")})


class LifeTest(Base):
    def test_the_status_names_the_effect_over_the_built_in_test_pattern_too(self):
        # On the Pi 4 an effect over the colour bars was on in GET /api/effects and on the screen, and the status,
        # which the Live screen's "now playing" reads, did not name it.
        self.player.play([self.player.TEST_PATTERN])
        self.fx.put("fx-wash.fs")
        st = self.api.status({}, None, "t")["player"]
        self.assertEqual((st["test_pattern"], st["path"], st["effect"]), (True, None, "fx-wash"))
        self.fx.off()
        self.assertNotIn("effect", self.api.status({}, None, "t")["player"])

    def test_an_effect_goes_on_over_what_plays_and_never_takes_the_screen(self):
        epoch = self.player.source_epoch
        self.assertEqual((self.state()["available"], self.state()["on"]), (True, None))
        r = self.fx.put("fx-wash.fs", {"strength": 0.5})
        self.assertEqual(r, {"ok": True, "id": "fx-wash.fs"})
        self.assertEqual(self.mpv.props["path"], "/media/a.mp4")                       # the clip plays on
        self.assertEqual(self.player.source_epoch, epoch)                              # and the screen has not changed hands
        self.assertEqual(self.mpv.loaded, self.texts())
        self.assertEqual(self.mpv.props["fbo-format"], "auto")                         # on this board (x86) the buffers are left alone
        on = self.state()["on"]
        self.assertEqual((on["id"], on["name"], on["values"]["strength"], on["controls"]), ("fx-wash.fs", "fx-wash", 0.5, {"amount": 1.0, "speed": 1.0, "half": False}))
        self.assertEqual((on["checked"], on["pass_ms"], on["picture"]), (True, 1.5, {"matrix": "bt.709", "levels": "limited", "fps": 25.0}))
        self.assertIn("const float strength = 0.5;", self.text())
        self.assertEqual(self.api.status({}, None, "t")["player"]["effect"], "fx-wash")
        # another one takes its place: one effect at a time
        self.fx.put("fx-vignette.fs")
        self.assertEqual((len(self.mpv.loaded), self.state()["on"]["id"], len(self.texts())), (1, "fx-vignette.fs", 1))
        self.assertEqual(self.state()["limits"]["at_once"], 1)
        self.assertEqual(self.fx.off(), {"ok": True})
        self.assertEqual((self.mpv.loaded, self.mpv.props["fbo-format"], self.texts(), self.state()["on"], self.state()["last"]), ([], "auto", [], None, None))
        self.assertEqual([c for c in self.mpv.commands if c[:2] == ("set_property", "fbo-format")], [], "on this board an effect never sets the buffers' format")

    def test_on_a_pi_4_the_player_draws_in_8_bit_buffers_while_an_effect_is_on(self):
        """As for the mapping there (16-bit buffers cost it frames on the Pi 4). Set once when the effect goes on and
        once when it comes off; a change of a value, which is a new text of the same effect, leaves them."""
        self.api.board = dict(self.api.board, kind="pi4")
        self.assertTrue(self.fx.eight_bit())
        sets = lambda: [c[2] for c in self.mpv.commands if c[:2] == ("set_property", "fbo-format")]
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.mpv.props["fbo-format"], sets()), ("rgba8", ["rgba8"]))
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.fx.put("fx-vignette.fs")                                                  # another effect in its place
        self.assertEqual(sets(), ["rgba8"])
        self.fx.off()
        self.assertEqual((self.mpv.props["fbo-format"], sets()), ("auto", ["rgba8", "auto"]))
        self.player.set_mapping_mode(True)                                             # with a mapping on they are 8-bit already, and stay so
        self.fx.put("fx-wash.fs")
        self.fx.off()
        self.assertEqual(self.mpv.props["fbo-format"], "rgba8")
        self.player.set_mapping_mode(False)
        self.fx.put("fx-wash.fs")
        self.api.control({"action": "stop"}, None, "t")                                # Stop takes the effect and its buffers
        self.assertEqual(self.mpv.props["fbo-format"], "auto")
        for kind, want in (("pi3", True), ("pi5", False), ("x86", False), (None, False)):
            self.api.board = dict(self.api.board, kind=kind)
            self.assertEqual(self.fx.eight_bit(), want, kind)
        self.assertNotIn("effect", self.api.status({}, None, "t")["player"])

    def test_it_needs_a_picture_to_be_put_on(self):
        self.player.clear()
        self.assertEqual((self.state()["available"], "Nothing with a picture is playing" in self.state()["unavailable"]), (False, True))
        for call in (lambda: self.fx.put("fx-wash.fs"), lambda: self.fx.apply_preset({"id": "fx-wash.fs", "name": "x"})):
            with self.assertRaises(ApiError) as c:
                call()
            self.assertIn(c.exception.status, (409, 404))
        self.assertEqual(self.mpv.loaded, [])
        # Next and the one button answer at once and ask the player nothing (a controller's thread makes these calls):
        # the worker finds out that there is no picture, puts nothing on and says why
        for call in (lambda: self.fx.step(1), lambda: self.fx.toggle()):
            self.fx.error = None
            self.assertTrue(call()["ok"])
            self.pump()
            self.assertEqual((self.mpv.loaded, self.state()["on"]), ([], None))
            self.assertIn("Nothing with a picture is playing", self.state()["error"]["message"])
            self.fx.off()
        self.player.play(["/media/song.mp3"])                                          # sound only: no picture either
        self.mpv.video = None
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertEqual(c.exception.status, 409)
        self.api.registry.set_enabled("shaders", False)
        self.assertEqual((self.state()["enabled"], self.state()["effects"], self.state()["available"]), (False, [], False))
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertIn("turn on the Shaders and Vibes module", c.exception.message)

    def test_it_stays_on_when_the_clip_changes(self):
        self.fx.put("fx-wash.fs")
        text = self.mpv.loaded
        self.player.play(["/media/b.mov"])
        self.assertEqual((self.mpv.loaded, self.state()["on"]["id"]), (text, "fx-wash.fs"))
        self.api.play({"file": "a.mp4"}, None, "t")                                    # through the API, as a pad does
        self.assertEqual((self.mpv.loaded, self.state()["on"]["id"]), (text, "fx-wash.fs"))
        # the worker's look, once a second: the same kind of picture means no new text
        self.assertTrue(self.fx.changer._refresh is not None)
        self.fx.adjust("anchor")
        self.assertEqual(self.mpv.loaded, text)
        # another kind of picture under it (full range, BT.601, 50 a second): a new text of the same effect
        self.mpv.video, self.mpv.fps = {"colormatrix": "bt.601", "colorlevels": "full"}, 50.0
        serial = self.player.effect_serial
        self.fx.adjust("anchor")
        self.assertNotEqual(self.mpv.loaded, text)
        self.assertEqual((len(self.mpv.loaded), self.player.effect_serial), (1, serial))
        self.assertEqual(self.state()["on"]["picture"], {"matrix": "bt.601", "levels": "full", "fps": 50.0})
        self.assertIn("mat3(1.0, 1.0, 1.0, 0.0,", self.text())
        self.assertEqual(self.texts(), self.mpv.loaded)                                # the text before it is removed

    def test_stop_takes_it_off(self):
        self.fx.put("fx-wash.fs")
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual((self.mpv.loaded, self.mpv.props["fbo-format"], self.texts()), ([], "auto", []))
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "Stop was pressed"))
        self.player.play(["/media/a.mp4"])                                             # and the next clip is clean
        self.assertEqual(self.mpv.loaded, [])
        self.fx.put("fx-wash.fs")
        self.assertIsNone(self.state()["last"])

    def test_the_module_going_off_takes_it_off(self):
        self.fx.put("fx-wash.fs")
        self.api.set_module("shaders", {"enabled": False}, None, "t")
        self.assertEqual((self.mpv.loaded, self.texts()), ([], []))
        self.api.registry.set_enabled("shaders", True)
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "the module was switched off"))

    def test_a_restart_of_the_player_leaves_it_cleanly_off(self):
        self.fx.put("fx-wash.fs")
        self.player.set_shaders(["/run/mapper-1-1.glsl"])
        self.assertEqual([n.split("-")[0] for n in self.mpv.loaded], ["effect", "mapper"])       # the mapping stays behind the effect
        self.mpv.restart()                                                             # a new mpv: nothing loaded, nothing playing
        self.player.set_shaders(["/run/mapper-1-1.glsl"])                              # what autostart does for the mapping
        self.assertEqual(self.mpv.loaded, ["mapper-1-1.glsl"])                         # the effect is not put back by the way
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "the player was restarted"))
        self.assertNotIn("effect", self.api.status({}, None, "t")["player"])
        self.player.play(["/media/a.mp4"])
        self.assertEqual(self.mpv.loaded, ["mapper-1-1.glsl"])
        self.fx.put("fx-wash.fs")                                                      # and it goes on again like the first time
        self.assertEqual([n.split("-")[0] for n in self.mpv.loaded], ["effect", "mapper"])
        # the same when nobody asked in between: a change for the old effect finds nothing to change
        self.fx.change({"values": {"strength": 0.1}})
        self.mpv.restart()
        self.player.play(["/media/a.mp4"])
        self.pump()
        self.assertEqual((self.mpv.loaded, self.state()["on"]), ([], None))
        # a player that is down: asking is not an error, and nothing is on
        self.fx.put("fx-wash.fs")
        self.mpv.down = True
        self.assertIsNone(self.fx.current())
        self.mpv.down = False

    def test_an_effect_left_by_an_earlier_panel_process_is_taken_off_at_start(self):
        self.fx.put("fx-wash.fs")
        left = list(self.mpv.props["glsl-shaders"]) + ["/run/mapper-9-9.glsl"]
        self.mpv.props["glsl-shaders"] = left
        fresh = E.Effects(self.api, log=lambda *_: None, thread=False)                 # the panel after a restart: it knows of no effect
        with open(os.path.join(self.rundir, "effect-1-1.glsl.tmp"), "w") as f:
            f.write("// half written")
        fresh.tidy()
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/mapper-9-9.glsl"])
        self.assertEqual(self.texts(), [])
        self.assertEqual((fresh.state()["on"], fresh.state()["last"]), (None, "the panel was restarted"))
        import inspect
        from pvj import server
        self.assertIn("api.effects.tidy()", inspect.getsource(server.build))

    def test_the_gpu_refusing_a_filter_leaves_the_picture_as_it_was(self):
        self.fx.upload("bad.fs", GOOD.replace("* k", "* oops"))
        FakeTap.lines = list(REFUSAL)
        with self.assertRaises(ApiError) as c:
            self.fx.put("bad.fs")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("`oops' undeclared", c.exception.message)
        self.assertIn("No effect is on", c.exception.message)
        self.assertEqual((self.mpv.loaded, self.texts(), self.mpv.props["fbo-format"]), ([], [], "auto"))
        s = self.state()
        self.assertEqual((s["on"], s["error"]["id"]), (None, "bad.fs"))
        self.assertIn("undeclared", next(x for x in s["effects"] if x["id"] == "bad.fs")["refused"])
        self.assertNotIn("bad.fs", self.fx.order())                                    # Previous and Next pass it by
        FakeTap.lines = []
        self.fx.put("fx-wash.fs")
        was = self.mpv.loaded
        FakeTap.lines = list(REFUSAL)
        with self.assertRaises(ApiError) as c:
            self.fx.put("bad.fs")
        self.assertIn("The effect before it is back on", c.exception.message)
        self.assertEqual((self.mpv.loaded, self.state()["on"]["id"]), (was, "fx-wash.fs"))
        self.assertIsNotNone(self.fx.current())
        FakeTap.lines = []
        self.fx.upload("bad.fs", GOOD, replace=True)                                   # the file changed: it is tried again
        self.fx.put("bad.fs")
        self.assertIsNone(self.state()["error"])
        self.assertIsNone(next(x for x in self.state()["effects"] if x["id"] == "bad.fs")["refused"])

    def test_a_player_that_cannot_judge_a_shader_says_so(self):
        self.mpv.vo = "null"
        self.fx.put("fx-wash.fs")
        self.assertIsNone(self.state()["on"]["checked"])


class ValuesTest(Base):
    def setUp(self):
        super().setUp()
        self.fx.upload("all.fs", ALL)
        self.fx.upload("moves.fs", MOVES)
        self.fx.upload("strobe.fs", STROBE)

    def test_values_change_while_it_is_on_and_many_changes_are_one_text(self):
        self.fx.put("all.fs")
        first, serial = self.mpv.loaded, self.player.effect_serial
        for n in range(50):                                                            # a knob turned
            r = self.fx.change({"values": {"k": n / 25.0}})
        self.assertEqual((r["ok"], r["id"], r["values"]["k"]), (True, "all.fs", 1.96))
        self.assertEqual(self.mpv.loaded, first)                                       # a request only notes the value down
        self.assertTrue(self.state()["on"]["pending"])
        self.pump()
        self.assertNotEqual(self.mpv.loaded, first)
        self.assertEqual((len(self.mpv.loaded), self.player.effect_serial, self.texts()), (1, serial, self.mpv.loaded))
        self.assertIn("const float k = 1.96;", self.text())
        self.assertEqual((self.state()["on"]["values"]["k"], self.state()["on"]["pending"]), (1.96, False))
        self.fx.change({"values": {"lit": True, "mode": 1, "tint": [0.1, 0.2, 0.3], "spot": [0.2, 0.9]}, "controls": {"amount": 0.4, "half": True}, "id": "all.fs"})
        self.pump()
        text = self.text()
        for line in ("const bool lit = true;", "const int mode = 1;", "const vec4 pvj_in_tint" if False else "const vec4 tint = vec4(0.1, 0.2, 0.3, 1.0);",
                     "const vec2 spot = vec2(0.2, 0.9);", "pvj_native(c), 0.4)", E.size_lines(540)[0]):
            self.assertIn(line, text)
        self.assertEqual(self.state()["on"]["controls"], {"amount": 0.4, "speed": 1.0, "half": True})

    def test_what_is_refused_at_once(self):
        with self.assertRaises(ApiError) as c:
            self.fx.change({"values": {"k": 1}})
        self.assertEqual((c.exception.status, c.exception.message), (409, "no effect is on"))
        self.fx.put("all.fs")
        for body, status, why in (({"values": {"nope": 1}}, 400, "no input called nope"), ({"values": {"k": "x"}}, 400, "k must be a number"),
                                  ({"values": {"mode": 7}}, 400, "must be one of"), ({"controls": {"hue": 1}}, 400, "no control called hue"),
                                  ({"controls": {"amount": True}}, 400, "amount must be a number"), ({"controls": {"half": "yes"}}, 400, "half must be true or false"),
                                  ({"values": {"k": 1}, "id": "fx-wash.fs"}, 409, "not the effect that is on"),
                                  ({"control": 9, "level": 1}, 400, "control must be 1 to 8"), ({"control": 1}, 400, "send level"),
                                  ({"control": 1, "level": 200}, 400, "send level"), ({"control": 6, "level": 1}, 404, "has no control 6")):
            with self.assertRaises(ApiError, msg=body) as c:
                self.fx.change(body)
            self.assertEqual(c.exception.status, status, body)
            self.assertIn(why, c.exception.message, body)
        self.assertEqual(self.fx.change({"values": {"k": 99}, "controls": {"amount": 9}})["values"]["k"], 2.0)       # a number is kept inside its range
        self.assertIsNone(self.fx.changer.pending() and None)

    def test_a_controllers_knobs_and_buttons_drive_the_inputs_in_the_files_order(self):
        self.fx.put("all.fs")
        self.assertEqual(self.fx.change({"control": 1, "level": 127})["values"]["k"], 2.0)
        self.assertEqual(self.fx.change({"control": 2, "press": True})["values"]["lit"], True)
        self.assertEqual(self.fx.change({"control": 2, "press": True})["values"]["lit"], False)            # a toggle of the wish that waits
        self.assertEqual(self.fx.change({"control": 3, "level": 127})["values"]["mode"], 2)
        self.fx.change({"control": 4, "press": True})                                  # the event (colours and points have no knob)
        self.pump()
        self.assertIn("const bool bang = true;", self.text())
        self.assertNotIn("bang", self.state()["on"]["values"])
        self.fx.changer._release = 0                                                   # a quarter of a second later
        self.pump()
        self.assertIn("const bool bang = false;", self.text())

    def test_a_filter_that_moves_or_says_it_flashes_keeps_its_own_pace_unless_faster_is_switched_on(self):
        rows = {s["id"]: s for s in self.state()["effects"]}
        self.assertEqual([(rows[n]["moves"], rows[n]["flashes"], rows[n]["speed_max"]) for n in ("all.fs", "moves.fs", "strobe.fs")],
                         [(False, False, None), (True, False, 1.0), (False, True, 1.0)])
        self.fx.put("moves.fs", controls={"speed": 4})
        self.assertEqual(self.state()["on"]["controls"]["speed"], 1.0)
        self.assertEqual(self.fx.change({"controls": {"speed": 3}})["controls"]["speed"], 1.0)
        self.assertEqual(self.fx.change({"controls": {"speed": 0.25}})["controls"]["speed"], 0.25)          # slower is always allowed
        self.pump()
        self.assertIn("/ 25.0 * 0.25;", self.text())
        self.gen.api_set({"action": "config", "faster": True}, None, "t")              # the owner's opt-in, the same one as for the generators
        self.assertEqual((self.state()["faster"], next(s for s in self.state()["effects"] if s["id"] == "moves.fs")["speed_max"]), (True, 4.0))
        self.fx.change({"controls": {"speed": 3}})
        self.pump()
        self.assertIn("/ 25.0 * 3.0;", self.text())
        self.gen.api_set({"action": "config", "faster": False}, None, "t")             # off again: the worker's next look limits what is on
        self.fx.adjust("anchor")
        self.assertEqual(self.state()["on"]["controls"]["speed"], 1.0)
        self.assertIn("/ 25.0;", self.text())
        self.fx.put("all.fs", controls={"speed": 3})                                   # a filter that does not move has no speed to set
        self.assertEqual(self.state()["on"]["controls"]["speed"], 1.0)

    def test_values_the_gpu_refused_are_not_sent_again(self):
        self.fx.put("all.fs")
        was = self.mpv.loaded
        FakeTap.lines = list(REFUSAL)
        self.fx.change({"values": {"mode": 2}})
        self.pump()
        self.assertEqual(self.mpv.loaded, was)                                         # the text before it is back
        self.assertEqual((self.state()["on"]["values"]["mode"], self.state()["error"]["id"]), (0, "all.fs"))
        FakeTap.lines = []
        with self.assertRaises(ApiError) as c:
            self.fx.change({"values": {"mode": 2}})
        self.assertEqual(c.exception.status, 422)
        self.assertIn("refused these values before", c.exception.message)
        self.assertEqual(self.fx.change({"values": {"mode": 1}})["values"]["mode"], 1)

    def test_a_frame_rate_that_wobbles_does_not_make_a_new_text_every_second(self):
        """A stream or a live input has no frame rate of its own, and the player's estimate of it moves all the time.
        The look, once a second, used to take each new number for another kind of picture: a new text (a compile)
        every second for as long as the effect was on, and a guard that never got to settle."""
        wobble = iter([29.2, 30.4, 29.71, 30.02, 29.5, 30.3, 29.97, 30.1, 29.4, 30.25, 29.8] * 4)
        self.mpv.container, self.mpv.fps = False, lambda: next(wobble)
        for sid, moves in (("fx-wash.fs", False), ("moves.fs", True)):
            self.fx.off()
            self.fx.put(sid)
            self.assertEqual(self.fx.library()[[s["id"] for s in self.fx.library()].index(sid)]["moves"], moves)
            texts, serial, made = [self.mpv.loaded], self.player.effect_serial, self.fx._serial
            for _ in range(10):
                self.fx.adjust("anchor")
                texts.append(self.mpv.loaded)
            self.assertEqual(len({tuple(t) for t in texts}), 1, "%s: ten looks, %d texts" % (sid, len({tuple(t) for t in texts})))
            self.assertEqual((self.fx._serial, self.player.effect_serial), (made, serial), sid)
        # the estimate is taken for the common rate it is close to; a real change of rate is still followed
        self.assertEqual([E.near_common(x) for x in (29.9, 30.2, 24.1, 14.95, 59.6, 27.0, 8.3)], [29.97, 30.0, 24.0, 15.0, 59.94, 27.0, 8.3])
        self.assertEqual(self.fx.on["picture"]["fps"], 29.97)
        self.mpv.fps = 50.0
        self.fx.adjust("anchor")
        self.assertEqual(self.fx.on["picture"]["fps"], 50.0)                            # moves.fs reads the clock: its text follows
        self.assertNotEqual(self.mpv.loaded, texts[-1])
        # a file's own rate is exact and is followed exactly, also by a little (24 and 25 pictures a second)
        self.mpv.container, self.mpv.fps = True, 24.0
        self.fx.adjust("anchor")
        self.mpv.fps = 25.0
        self.fx.adjust("anchor")
        self.assertEqual(self.fx.on["picture"]["fps"], 25.0)
        # a filter that never reads the clock keeps its text whatever the rate does, and takes the new rate along
        # when another kind of picture makes a new text anyway
        self.fx.put("fx-wash.fs")
        text = self.mpv.loaded
        self.mpv.fps = 25.0
        self.fx.adjust("anchor")
        self.assertEqual(self.mpv.loaded, text)
        self.assertEqual(E.steady_picture({"matrix": "bt.709", "levels": "limited", "fps": 60.0}, {"matrix": "bt.709", "levels": "limited", "fps": 25.0}, False)["fps"], 25.0)
        self.assertEqual(E.steady_picture({"matrix": "bt.709", "levels": "limited", "fps": 25.4}, {"matrix": "bt.709", "levels": "limited", "fps": 25.0}, True, True)["fps"], 25.0)
        self.assertEqual(E.steady_picture({"matrix": "bt.709", "levels": "limited", "fps": 27.0}, {"matrix": "bt.709", "levels": "limited", "fps": 25.0}, True, True)["fps"], 27.0)
        self.assertEqual(E.steady_picture({"matrix": "bt.709", "levels": "limited", "fps": 25.4}, {"matrix": "bt.709", "levels": "limited", "fps": 25.0}, True)["fps"], 25.4)

    def test_a_picture_whose_format_the_hooks_cannot_take_gets_no_effect(self):
        """An effect's text hooks the LUMA plane or the RGB plane. A picture in XYZ has neither and one without
        colour was never drawn through them: the effect would be "on" with nothing drawn. The box says so instead,
        and an effect that is on when such a picture starts comes off with the reason."""
        for video in ({"pixelformat": "xyz12le", "colormatrix": "xyz", "colorlevels": "full"}, {"pixelformat": "gray", "colormatrix": "bt.601", "colorlevels": "full"},
                      {"pixelformat": "gray10le", "colormatrix": "bt.709", "colorlevels": "limited"}, {"pixelformat": "ya8", "colormatrix": "bt.601", "colorlevels": "full"},
                      {"pixelformat": "pal8", "colormatrix": "rgb", "colorlevels": "full"}, {"pixelformat": "monow", "colormatrix": "bt.601", "colorlevels": "full"},
                      {"pixelformat": "drm_prime", "hw-pixelformat": "gray", "colormatrix": "bt.601", "colorlevels": "limited"}):
            self.mpv.video = video
            state = self.state()
            self.assertEqual((state["available"], state["on"]), (False, None), video)
            self.assertIn("This picture's format cannot take an effect", state["unavailable"])
            with self.assertRaises(ApiError) as c:
                self.fx.put("fx-wash.fs")
            self.assertEqual((c.exception.status, "format cannot take an effect" in c.exception.message, self.mpv.loaded), (409, True, []))
        # what is not known to be unfit is let through: above all a decoder's own format, with the real layout beside it
        for video in ({"pixelformat": "drm_prime", "hw-pixelformat": "nv12", "colormatrix": "bt.709", "colorlevels": "limited"},
                      {"pixelformat": "vaapi", "hw-pixelformat": "yuv420p", "colormatrix": "bt.709", "colorlevels": "limited"},
                      {"pixelformat": "videotoolbox", "colormatrix": "bt.709", "colorlevels": "limited"}, {"pixelformat": "yuv444p10", "colormatrix": "bt.2020-ncl"},
                      {"pixelformat": "gbrp", "colormatrix": "rgb", "colorlevels": "full"}, {"pixelformat": "rgb24", "colormatrix": "rgb"},
                      {"pixelformat": "yuyv422", "colormatrix": "bt.601"}, {"pixelformat": "some-new-format"}, {}):
            self.mpv.video = video
            self.fx.put("fx-wash.fs")
            self.assertEqual((len(self.mpv.loaded), self.state()["on"]["id"]), (1, "fx-wash.fs"), video)
            self.fx.off()
        # a clip of such a format starts under an effect that is on: at the next look it comes off, with the reason
        self.fx.put("fx-wash.fs")
        self.mpv.video = {"pixelformat": "xyz12le", "colormatrix": "xyz", "colorlevels": "full"}
        self.fx.adjust("anchor")
        state = self.state()
        self.assertEqual((self.mpv.loaded, state["on"], state["last"]), ([], None, "a picture came that cannot take an effect"))
        self.assertEqual(self.texts(), [])

    def test_previous_and_next_step_through_the_filters_and_one_button_switches_on_and_off(self):
        ids = self.fx.order()
        self.assertEqual((ids[0], ids[-3:]), ("fx-edge-glow.fs", ["all.fs", "moves.fs", "strobe.fs"]))
        self.assertEqual(self.fx.step(1), {"ok": True, "id": ids[0]})                  # none is on: the first
        self.assertEqual(self.mpv.loaded, [])                                          # it answers at once; the worker puts it on
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[0])
        self.fx.step(1)
        self.assertEqual(self.fx.step(1)["id"], ids[2])                                # two presses before the worker came: two steps
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[2])
        self.fx.step(-1)
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[1])
        self.fx.put(ids[0])
        self.fx.step(-1)
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[-1])                            # round the end
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": False})                  # noted; the worker takes it off
        self.pump()
        self.assertEqual(self.mpv.loaded, [])
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": True, "id": ids[-1]})    # the one that was on last
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[-1])
        for bad in (2, 0, 1.0, -1.0, True, "1", None):
            with self.assertRaises(ApiError) as c:
                self.fx.step(bad)
            self.assertEqual(c.exception.status, 400, bad)
        for body in ({"off": 1}, {"off": "yes"}, {"toggle": 1}, {"toggle": False}, {"off": False, "id": ids[0]}):
            with self.assertRaises(ApiError) as c:
                self.fx.api_put(body, None, "t")
            self.assertEqual((c.exception.status, "must be true" in c.exception.message), (400, True), body)
        self.assertEqual(self.state()["on"]["id"], ids[-1])

    def test_an_off_ends_what_was_asked_for_before_it_also_when_nothing_is_on(self):
        """The review's case. Nothing is on; Next (or the one button) notes an effect for the worker; the worker has
        taken the job; Off arrives. There was nothing for Off to take off, and the job went on afterwards. Now an
        Off always moves the player's effect serial on, so the job is no longer the newest thing asked for."""
        for ask in (lambda: self.fx.step(1), lambda: self.fx.toggle(), lambda: self.fx.apply_preset({"id": "all.fs", "index": 1})):
            self.fx.put("all.fs")
            self.fx.preset_save("One")
            self.fx.off()
            self.assertEqual((self.mpv.loaded, self.fx.on), ([], None))
            serial = self.player.effect_serial
            ask()
            with self.fx.changer._cond:                                                # the worker takes the job ...
                job, self.fx.changer._show = self.fx.changer._show, None
            self.assertEqual((job["gen"], job["clears"]), (self.fx._gen, self.player.clears))
            self.fx.off()                                                              # ... and Off arrives before it reaches the player
            self.assertGreater(self.player.effect_serial, serial)
            self.fx.play_job(job)
            self.assertEqual((self.mpv.loaded, self.fx.on, self.state()["on"], self.fx.error), ([], None, None, None))
            # the player's own rule, without the engine's count: a job from before a clearing of the screen is
            # refused by the count of clearings alone
            self.player.clear()
            self.player.play(["/media/a.mp4"])
            self.assertIsNone(self.fx.put("all.fs", queued=True, gen=self.fx._gen, clears=job["clears"]))
            self.assertEqual(self.mpv.loaded, [])
        # and through the player directly
        serial = self.player.effect_serial
        self.assertFalse(self.player.clear_effect())                                   # nothing was on
        self.assertEqual(self.player.effect_serial, serial + 1)
        self.assertFalse(self.player.clear_effect(serial + 1, "refused"))              # "only that one": no Off, the serial stays
        self.assertEqual(self.player.effect_serial, serial + 1)

    def test_an_off_from_the_one_button_ends_a_job_the_worker_is_in_the_middle_of(self):
        """The controller's Off is noted for the worker (its thread asks the player nothing). The worker may be in
        the middle of putting the effect on: it does not stay on."""
        self.fx.toggle()
        with self.fx.changer._cond:
            job, self.fx.changer._show = self.fx.changer._show, None
        real = self.player.put_effect

        def put_effect(*args):                          # the button is pressed again while the player is taking the text
            self.fx.toggle()
            return real(*args)
        self.player.put_effect = put_effect
        self.fx.play_job(job)
        self.player.put_effect = real
        self.assertEqual((self.mpv.loaded, self.fx.on), ([], None))
        self.pump()                                     # the Off that was noted: nothing left to do, and nothing goes on
        self.assertEqual((self.mpv.loaded, self.state()["on"]), ([], None))
        self.assertFalse(self.fx.changer.pump())

    def test_the_one_button_acts_on_what_was_asked_for_last(self):
        """Two quick presses are on and off, whether or not the worker came round in between; they used to note
        "on" twice."""
        ids = self.fx.order()
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": True, "id": ids[0]})
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": False})
        self.pump()
        self.assertEqual((self.mpv.loaded, self.state()["on"]), ([], None))
        self.assertEqual([self.fx.toggle()["on"] for _ in range(5)], [True, False, True, False, True])
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[0])
        self.assertEqual([self.fx.toggle()["on"] for _ in range(3)], [False, True, False])
        self.pump()
        self.assertEqual(self.mpv.loaded, [])
        # put on from the panel in between: the button then acts on what the player has
        self.fx.toggle()
        self.fx.put(ids[1])
        self.assertEqual(self.fx.toggle()["on"], False)
        self.pump()
        self.assertEqual(self.mpv.loaded, [])
        # Next after an Off that the worker has not carried out yet starts from "none on"
        self.fx.put(ids[3])
        self.fx.toggle()
        self.assertEqual(self.fx.step(1)["id"], ids[0])
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[0])

    def test_effects_switch_at_most_about_three_times_a_second_unless_faster_is_on(self):
        """The flash limit for switching: an effect goes on at the earliest 0.35 seconds after the last switch, so a
        sequencer on the one button or on Next cannot flash a filter. Off never waits. The last wish always lands."""
        t = [100.0]
        self.fx._clock = self.fx.changer._clock = lambda: t[0]
        self.assertEqual(E.SWITCH_GAP, 0.35)

        def burst(press, presses, seconds):
            before, shown, end = self.player.effect_serial, 0, t[0] + seconds
            for n in range(presses):
                press()
                while self.fx.changer.pump():
                    pass
                shown, before = shown + (1 if self.fx.on is not None and self.player.effect_serial != before and self.mpv.loaded else 0), self.player.effect_serial
                t[0] += seconds / presses
            return shown
        went_on = burst(self.fx.toggle, 20, 2.0)                                       # a button at ten presses a second
        self.assertTrue(1 <= went_on <= 6, went_on)                                    # at most 3 a second
        t[0] += 1.0
        while self.fx.changer.pump():
            pass
        self.assertEqual(self.mpv.loaded, [])                                          # twenty presses: off at the end
        self.fx.toggle()
        t[0] += 1.0
        self.fx.changer.pump()
        self.assertEqual(len(self.mpv.loaded), 1)                                      # twenty-one: on
        went_on = burst(lambda: self.fx.step(1), 20, 2.0)                              # Next at ten a second
        self.assertTrue(1 <= went_on <= 6, went_on)
        # a wish that has to wait lands when the gap is over, and Off does not wait
        self.fx.off()
        t[0] += 1.0
        self.fx.put("fx-wash.fs")
        self.fx.step(1)
        self.assertFalse(self.fx.changer.pump())
        self.assertEqual(self.state()["on"]["id"], "fx-wash.fs")
        with self.fx.changer._cond:
            self.assertAlmostEqual(self.fx.changer._take()[1], 0.35, 2)                # the worker sleeps that long
        t[0] += 0.2
        self.fx.toggle()                                                               # off: at once
        self.assertTrue(self.fx.changer.pump())
        self.assertEqual(self.mpv.loaded, [])
        self.fx.toggle()
        self.assertFalse(self.fx.changer.pump())
        t[0] += 0.36
        self.assertTrue(self.fx.changer.pump())
        self.assertEqual(len(self.mpv.loaded), 1)
        # values of the effect that is on are not held up by a switch that waits
        self.fx.step(1)
        self.fx.change({"controls": {"amount": 0.5}})
        self.fx.changer._last = -1e9
        self.assertTrue(self.fx.changer.pump())
        self.assertEqual(self.fx.on["controls"]["amount"], 0.5)
        # the owner's "faster" opt-in lifts the gap
        cfg = self.gen.config()
        cfg["faster"] = True
        self.gen._save(cfg)
        self.fx.step(1)
        self.assertTrue(self.fx.changer.pump())

    def test_a_change_of_a_value_meant_for_an_effect_that_has_gone_is_dropped(self):
        # a change of a value meant for an effect that has gone
        self.player.play(["/media/a.mp4"])
        self.fx.put("all.fs")
        self.fx.change({"values": {"k": 2}})
        wish = self.fx.changer.pending()
        self.fx.put("all.fs")                                                          # the same file, put on anew: another effect
        was = self.mpv.loaded
        self.fx.changer._adjust, self.fx.changer._last = wish, -1e9
        self.fx.changer.pump()
        self.assertEqual(self.mpv.loaded, was)
        self.assertEqual(self.state()["on"]["values"]["k"], 1.0)

    def test_presets_per_filter(self):
        self.fx.put("all.fs", {"k": 1.5, "lit": True}, {"amount": 0.6})
        self.fx.api_presets({"action": "save", "name": "Warm"}, None, "t")
        self.assertEqual(self.settings.data["shaders"]["fx_presets"]["all.fs"],
                         [{"name": "Warm", "values": {"k": 1.5, "lit": True}, "controls": {"amount": 0.6, "speed": 1.0, "half": False}}])
        self.assertNotIn("all.fs", self.settings.data["shaders"].get("presets", {}))      # the generators' presets are another list
        self.assertEqual((self.state()["on"]["preset"], next(s for s in self.state()["effects"] if s["id"] == "all.fs")["presets"]), ("Warm", ["Warm"]))
        self.fx.change({"values": {"k": 0.2, "lit": False, "mode": 2}, "controls": {"amount": 1.0}})
        self.pump()
        self.assertIsNone(self.state()["on"]["preset"])
        self.assertEqual(self.fx.apply_preset({"name": "warm"}), {"ok": True, "id": "all.fs", "preset": "Warm"})
        self.pump()
        on = self.state()["on"]
        self.assertEqual((on["values"]["k"], on["values"]["lit"], on["values"]["mode"], on["controls"]["amount"], on["preset"]), (1.5, True, 0, 0.6, "Warm"))
        self.assertEqual(self.fx.apply_preset({"index": 1})["preset"], "Warm")
        for body, status in (({"index": 2}, 404), ({"index": 0}, 400), ({"name": "nope"}, 404), ({}, 400), ({"id": "../x.fs", "name": "Warm"}, 400)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.fx.apply_preset(body)
            self.assertEqual(c.exception.status, status, body)
        # the preset called default is what a plain "put on" uses; values given go on top
        self.fx.api_presets({"action": "save", "name": "default"}, None, "t")
        self.fx.off()
        self.fx.put("all.fs")
        self.assertEqual((self.state()["on"]["values"]["k"], self.state()["on"]["controls"]["amount"], self.state()["on"]["preset"]), (1.5, 0.6, "default"))
        self.fx.put("all.fs", {"k": 0.5})
        self.assertEqual((self.state()["on"]["values"]["k"], self.state()["on"]["values"]["lit"], self.state()["on"]["preset"]), (0.5, True, None))
        # a preset of another filter puts that filter on (through the worker)
        self.fx.put("fx-wash.fs")
        self.fx.apply_preset({"id": "all.fs", "name": "Warm"})
        self.pump()
        self.assertEqual((self.state()["on"]["id"], self.state()["on"]["preset"]), ("all.fs", "Warm"))
        self.fx.api_presets({"action": "rename", "id": "all.fs", "name": "Warm", "to": "Hot"}, None, "t")
        self.fx.api_presets({"action": "delete", "id": "all.fs", "name": "default"}, None, "t")
        self.assertEqual([p["name"] for p in self.fx.presets()["all.fs"]], ["Hot"])
        for body, status in (({"action": "save", "name": " x"}, 400), ({"action": "save", "name": "x", "id": "fx-wash.fs"}, 409),
                             ({"action": "rename", "id": "all.fs", "name": "nope", "to": "y"}, 404), ({"action": "delete", "id": "all.fs", "name": "nope"}, 404),
                             ({"action": "shuffle"}, 400)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.fx.api_presets(body, None, "t")
            self.assertEqual(c.exception.status, status, body)
        for n in range(L.MAX_PRESETS - 1):
            self.fx.preset_save("p%d" % n)
        with self.assertRaises(ApiError) as c:
            self.fx.preset_save("one too many")
        self.assertEqual(c.exception.status, 409)

    def test_presets_go_round_with_the_settings_and_a_damaged_one_is_only_itself(self):
        from pvj import boxcare
        self.fx.put("all.fs", {"k": 1.5})
        self.fx.preset_save("Warm")
        section = boxcare.check_shaders(self.settings.data["shaders"], None)
        self.assertEqual(section["fx_presets"], self.settings.data["shaders"]["fx_presets"])
        for bad in ({"all.fs": [{"name": "x", "controls": {"hue": 3}}]}, {"../x.fs": []}, {"all.fs": [{"name": "x"}, {"name": "X"}]}, "no"):
            with self.assertRaises(ValueError, msg=bad):
                boxcare.check_shaders({"fx_presets": bad}, None)
        self.settings.data["shaders"]["fx_presets"]["all.fs"].append({"name": "broken", "values": "no"})     # edited by hand
        self.assertEqual([p["name"] for p in self.fx.presets()["all.fs"]], ["Warm"])

    def test_the_guard_reports_a_filter_that_is_too_heavy_with_this_clip_and_marks_nothing(self):
        now = [100.0]
        self.fx.guard = L.Guard(self.fx, clock=lambda: now[0])
        self.fx.put("all.fs")
        loads = []
        for _ in range(12):
            now[0] += 1.0
            self.mpv.drops += 5
            loads.append(self.state()["on"]["load"])
        self.assertEqual((loads[0], loads[-1]), (None, "heavy"))
        self.assertGreaterEqual(self.state()["on"]["drops_per_second"], 2.0)
        self.assertNotIn("heavy", self.settings.data.get("shaders", {}))               # no mark: the generators' rotation is not touched
        self.assertEqual(self.gen.heavy_here(), {})
        self.assertEqual(self.state()["on"]["id"], "all.fs")                           # and it stays on: the person decides
        for _ in range(10):
            now[0] += 1.0
            load = self.state()["on"]["load"]
        self.assertEqual(load, "ok")

    def test_nothing_a_controller_sends_waits_for_the_gpu(self):
        """While the GPU looks at a new filter the engine's lock is held, for up to four seconds. A change, a step,
        on/off and the status must answer meanwhile: they run on the thread that reads the MIDI controller."""
        self.fx.put("all.fs")
        held, go = threading.Event(), threading.Event()

        def gpu():
            with self.fx._lock:
                held.set()
                go.wait(5)
        t = threading.Thread(target=gpu)
        t.start()
        self.assertTrue(held.wait(2))
        try:
            began = time.monotonic()
            self.fx.change({"control": 1, "level": 64})
            self.fx.change({"controls": {"amount": 0.5}})
            self.fx.step(1)
            self.fx.apply_preset({"id": "fx-wash.fs", "index": 1}) if self.fx.presets().get("fx-wash.fs") else None
            self.api.status({}, None, "t")
            self.fx.current()
            self.fx.sweep()
            self.fx.toggle()                                                           # off: noted for the worker
            took = time.monotonic() - began
        finally:
            go.set()
            t.join()
        self.assertLess(took, 1.0, "a controller's call waited for the engine's lock")
        self.pump()
        self.assertEqual(self.mpv.loaded, [])

    def test_nothing_a_controller_sends_asks_the_player_or_waits_for_its_lock(self):
        """Everything a controller can send for an effect (a value, the amount, a control, a press, Next, Previous,
        the one button, a preset) is noted and answered from what this process remembers. The player's lock may be
        held (a clip is being started, the player is being restarted), the player may not answer at all, and the
        engine's lock may be held by the GPU's look: the thread that reads the controller goes on."""
        self.fx.put("all.fs")
        self.fx.preset_save("One")
        hub = M.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [])
        self.addCleanup(hub.stop)
        hub.calls = M.RateLimiter(time.monotonic, rate=1e9, burst=1e9)
        held, go = threading.Event(), threading.Event()

        def busy():
            with self.fx._lock, self.player._lock:      # in the order of the locks (pvj/locks.py): the engine's, then the player's
                held.set()
                go.wait(10)
        t = threading.Thread(target=busy)
        t.start()
        self.assertTrue(held.wait(2))
        asked = len(self.mpv.commands)
        answers = []

        def controller():
            for path, body in (("/api/effects/values", {"controls": {"amount": 0.4}}), ("/api/effects/values", {"control": 1, "level": 90}),
                               ("/api/effects/values", {"control": 3, "press": True}), ("/api/effects/preset", {"index": 1}),
                               ("/api/effects/step", {"dir": 1}), ("/api/effects/step", {"dir": -1}), ("/api/effects", {"toggle": True}),
                               ("/api/effects", {"toggle": True}), ("/api/effects/preset", {"id": "fx-wash.fs", "name": "nope"})):
                answers.append(hub._do(path, body))
            answers.append(hub._target("effect_amount"))
        c = threading.Thread(target=controller, daemon=True)
        began = time.monotonic()
        c.start()
        c.join(3)
        took, alive = time.monotonic() - began, c.is_alive()
        go.set()
        t.join()
        c.join(5)
        self.assertFalse(alive, "a controller's call waited for the player's lock")
        self.assertLess(took, 1.0)
        self.assertEqual(len(answers), 10)
        self.assertEqual(self.mpv.commands[asked:], [], "a controller's call asked the player something")


class DetailTest(Base):
    """Effect detail: the box's setting, what Automatic makes of it on each board, and what the panel is told."""
    def setUp(self):
        super().setUp()
        self.fx.upload("mine.fs", GOOD)
        self.mpv.video = dict(self.mpv.video, w=1920, h=1080)

    def pi4(self):
        self.api.board = dict(self.api.board, kind="pi4")

    def lines_of(self, sid, bundled=True):
        return E.auto_lines("pi4", sid, bundled)

    def test_on_a_pi_4_an_effect_works_at_what_automatic_chose_and_the_panel_is_told(self):
        self.pi4()
        s = self.state()
        row = E.AUTO["pi4"]
        self.assertEqual(s["detail"], {"value": "auto", "default": "auto", "choices": ["auto", 540, 720, "full"], "board": "pi4",
                                       "auto": {"lines": row["lines"], "other": row["other"], "lower": row["lower"], "measured": row["measured"]}})
        self.assertNotIn("fx_detail", self.settings.data.get("shaders", {}))            # nobody chose: nothing is kept
        self.fx.put("fx-wash.fs")
        lines = self.lines_of("fx-wash.fs")
        w, h = E.work_size(1920, 1080, lines)
        self.assertEqual(self.text().count("\n".join(E.size_lines(lines)) + "\n"), 2)
        on = self.state()["on"]
        self.assertEqual(on["working"], {"lines": lines, "auto": True, "clip": {"width": 1920, "height": 1080, "lines": 1080}, "width": w, "height": h,
                                         "scaled": True, "lower": 540 if lines > 540 else None, "under": "clip"})
        self.assertEqual(on["controls"], {"amount": 1.0, "speed": 1.0, "half": False})  # the controls are what they were
        # an upload nobody measured works at the careful value
        self.fx.put("mine.fs")
        self.assertEqual(self.state()["on"]["working"]["lines"], row["other"])
        self.assertIn(E.size_lines(row["other"])[1], self.text())
        # a filter the table steps down works at its own value
        for sid, lower in row["lower"].items():
            self.fx.put(sid)
            self.assertEqual((self.state()["on"]["working"]["lines"], self.state()["on"]["working"]["auto"]), (lower, True), sid)

    def test_a_clip_at_or_below_the_cap_is_not_scaled_and_the_panel_says_so(self):
        self.pi4()
        self.mpv.video = dict(self.mpv.video, w=1280, h=720)
        self.fx.set_detail(720)
        self.fx.put("fx-wash.fs")
        on = self.state()["on"]["working"]
        self.assertEqual((on["lines"], on["auto"], on["clip"]["lines"], on["width"], on["height"], on["scaled"], on["lower"]), (720, False, 720, 1280, 720, False, 540))
        self.assertEqual((player_size(E.size_lines(720)[0], 1280, 720), player_size(E.size_lines(720)[1], 1280, 720)), (1280, 720))
        self.fx.set_detail(540)
        self.pump()
        on = self.state()["on"]["working"]
        self.assertEqual((on["lines"], on["width"], on["height"], on["scaled"], on["lower"]), (540, 960, 540, True, None))    # nothing lower to offer

    def test_one_text_fits_every_clip_and_a_new_size_is_told_without_a_new_text(self):
        self.pi4()
        self.fx.set_detail(720)
        self.fx.put("fx-wash.fs")
        text, made = self.mpv.loaded, self.fx._serial
        self.mpv.video = dict(self.mpv.video, w=1280, h=720)                            # the next clip is smaller
        self.fx.adjust("anchor")
        self.assertEqual((self.mpv.loaded, self.fx._serial), (text, made))             # the same text: the player's own arithmetic fits it
        on = self.state()["on"]["working"]
        self.assertEqual((on["clip"], on["width"], on["height"], on["scaled"]), ({"width": 1280, "height": 720, "lines": 720}, 1280, 720, False))
        self.mpv.video = dict(self.mpv.video, w=1080, h=1920)                           # a clip that stands: its width is the shorter side
        self.fx.adjust("anchor")
        self.assertEqual((self.mpv.loaded, self.fx._serial), (text, made))             # still the same text: it does not ask which way a clip lies
        on = self.state()["on"]["working"]
        self.assertEqual((on["clip"]["lines"], on["width"], on["height"], on["scaled"]), (1080, 720, 1280, True))
        self.mpv.video = {k: v for k, v in self.mpv.video.items() if k not in ("w", "h")}    # a player that does not say the size
        self.fx.put("fx-wash.fs")
        on = self.state()["on"]["working"]
        self.assertEqual((on["lines"], on["clip"], on["width"], on["scaled"], on["lower"]), (720, None, None, False, None))
        self.assertIn(E.size_lines(720)[0], self.text())                               # the cap is in the text all the same

    def test_on_a_board_nobody_measured_nothing_is_scaled_until_someone_chooses(self):
        self.assertEqual(self.api.board["kind"] in E.AUTO, False)                      # the tests' own board
        s = self.state()["detail"]
        self.assertEqual((s["value"], s["default"], s["auto"]), ("full", "full", None))
        self.fx.put("fx-wash.fs")
        self.assertNotIn("//!WIDTH", self.text())
        on = self.state()["on"]["working"]
        self.assertEqual((on["lines"], on["auto"], on["width"], on["height"], on["scaled"], on["lower"]), (None, False, 1920, 1080, False, 720))
        self.fx.set_detail("auto")                                                     # Automatic there: still nothing, and it says so
        self.pump()
        self.assertNotIn("//!WIDTH", self.text())
        self.assertEqual((self.state()["on"]["working"]["lines"], self.state()["on"]["working"]["auto"]), (None, True))
        self.assertEqual(self.settings.data["shaders"]["fx_detail"], "auto")
        self.fx.set_detail(540)
        self.pump()
        self.assertIn(E.size_lines(540)[0], self.text())
        for kind in ("pi5", "x86"):
            self.api.board = dict(self.api.board, kind=kind)
            self.assertEqual(self.state()["detail"]["default"], "full", kind)

    def test_the_setting_applies_on_tap_is_kept_and_only_the_owner_may_change_it(self):
        self.pi4()
        full, _ = self.pair()
        view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=full)[1]["token"]
        self.assertEqual(self.call("POST", "/api/effects/config", {"detail": 540})[0], 401)
        for token in (view, live):
            self.assertEqual(self.call("POST", "/api/effects/config", {"detail": 540}, token=token)[0], 403)
        self.assertEqual(self.call("POST", "/api/effects/config", {"detail": 540}, token=full, csrf=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/effects", token=view)[1]["detail"]["value"], "auto")         # everyone may see it
        self.fx.put("fx-wash.fs")
        first = self.mpv.loaded
        for detail in ("full", 540, 720):
            st, body, _ = self.call("POST", "/api/effects/config", {"detail": detail}, token=full)
            self.assertEqual((st, body["detail"]["value"], self.settings.data["shaders"]["fx_detail"]), (200, detail, detail))
            self.assertTrue(body["on"]["pending"])                                     # it goes to the effect that is on, through the worker
            self.pump()
            on = self.state()["on"]["working"]
            self.assertEqual((on["lines"], on["auto"]), (None if detail == "full" else detail, False))
            self.assertEqual("//!WIDTH" in self.text(), detail != "full")
            with open(self.settings.path) as f:
                self.assertEqual(json.load(f)["shaders"]["fx_detail"], detail)        # on disk
        self.assertNotEqual(self.mpv.loaded, first)
        # the board's own default is kept by keeping nothing, and writing it twice writes nothing
        st, body, _ = self.call("POST", "/api/effects/config", {"detail": "auto"}, token=full)
        self.assertEqual((st, body["detail"]["value"]), (200, "auto"))
        self.assertNotIn("fx_detail", self.settings.data["shaders"])
        before = os.stat(self.settings.path).st_mtime_ns
        self.assertEqual(self.call("POST", "/api/effects/config", {"detail": "auto"}, token=full)[0], 200)
        self.assertEqual(os.stat(self.settings.path).st_mtime_ns, before)
        for bad in ({"detail": 1080}, {"detail": 360}, {"detail": "720"}, {"detail": 720.0}, {"detail": True}, {"detail": None}, {"detail": [540]}, {},
                    {"detail": 540, "half": True}, {"height": 540}):
            st, body, _ = self.call("POST", "/api/effects/config", bad, token=full)
            self.assertEqual(st, 400, bad)
            self.assertIn("auto, 540, 720, full", body["error"])
        self.assertEqual(self.state()["detail"]["value"], "auto")
        self.api.registry.set_enabled("shaders", False)
        self.assertEqual(self.call("POST", "/api/effects/config", {"detail": 540}, token=full)[0], 409)

    def test_the_old_half_still_works_for_one_effect_and_a_preset_that_holds_false_follows_the_box(self):
        self.pi4()
        self.fx.set_detail(720)
        self.fx.put("fx-wash.fs", controls={"half": True})
        on = self.state()["on"]
        self.assertEqual((on["controls"]["half"], on["working"]["lines"], on["working"]["auto"], on["working"]["width"]), (True, 540, False, 960))
        self.assertIn(E.size_lines(540)[0], self.text())
        self.fx.change({"controls": {"half": False}})
        self.pump()
        self.assertEqual((self.state()["on"]["controls"]["half"], self.state()["on"]["working"]["lines"]), (False, 720))
        self.assertEqual(self.state()["controls"]["half"], {"default": False, "superseded": "detail"})
        # every preset saved before this holds "half": false: it must follow the box, never mean full size
        self.fx.preset_save("Kept")
        self.assertIs(self.settings.data["shaders"]["fx_presets"]["fx-wash.fs"][0]["controls"]["half"], False)
        self.fx.set_detail("auto")
        self.fx.off()
        self.fx.put("fx-wash.fs", preset="Kept")
        self.assertEqual((self.state()["on"]["working"]["lines"], self.state()["on"]["working"]["auto"]), (self.lines_of("fx-wash.fs"), True))
        self.fx.set_detail("full")                                                     # and at full size a preset with true still halves its own effect
        self.fx.put("fx-wash.fs", controls={"half": True})
        self.assertEqual(self.state()["on"]["working"]["lines"], 540)

    def test_at_amount_0_the_player_leaves_the_effect_out_and_nothing_waits_for_a_frame(self):
        self.pi4()
        began = time.monotonic()
        self.fx.put("fx-wash.fs", controls={"amount": 0})
        self.assertLess(time.monotonic() - began, 2.0)                                  # no wait for a pass that will not be drawn
        self.assertEqual(self.text().count("//!WHEN 0\n"), 2)
        on = self.state()["on"]
        self.assertEqual((on["id"], on["controls"]["amount"], on["checked"]), ("fx-wash.fs", 0.0, None))     # on, and the GPU has not looked yet
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.assertNotIn("//!WHEN", self.text())
        self.assertIs(self.state()["on"]["checked"], True)                              # now it has
        self.fx.change({"controls": {"amount": 0}})
        self.pump()
        self.assertEqual(self.text().count("//!WHEN 0\n"), 2)
        # a filter the GPU refuses is found out when the amount leaves 0, and the text with amount 0 stays
        self.fx.upload("bad.fs", GOOD.replace("* k", "* oops"))
        self.fx.put("bad.fs", controls={"amount": 0})
        kept = self.mpv.loaded
        FakeTap.lines = list(REFUSAL)
        self.fx.change({"controls": {"amount": 1}})
        self.pump()
        FakeTap.lines = []
        self.assertEqual(self.mpv.loaded, kept)
        s = self.state()
        self.assertEqual((s["on"]["controls"]["amount"], s["error"]["id"]), (0.0, "bad.fs"))
        self.assertIn("undeclared", s["error"]["message"])

    def test_the_setting_goes_round_through_a_settings_file_and_a_factory_reset_clears_it(self):
        from pvj import boxcare
        from pvj.settings import default_settings
        self.pi4()
        full, _ = self.pair()
        self.fx.set_detail(540)
        st, out, _ = self.call("POST", "/api/system/settings/export", {}, token=full)
        self.assertEqual((st, out["file"]["settings"]["shaders"]["fx_detail"]), (200, 540))
        self.fx.set_detail("full")
        self.assertEqual(self.state()["detail"]["value"], "full")
        st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(out["file"]).encode(), token=full)
        self.assertEqual((st, "shaders" in body["imported"]), (200, True), body)
        self.assertEqual((self.settings.data["shaders"]["fx_detail"], self.state()["detail"]["value"]), (540, 540))
        for detail in E.DETAILS:
            self.assertEqual(boxcare.check_shaders({"fx_detail": detail}, None)["fx_detail"], detail)
            self.assertEqual(L.check_extra({"fx_detail": detail}), {"fx_detail": detail})
        self.assertNotIn("fx_detail", boxcare.check_shaders({"dwell": 60}, None))       # a file without it: the board's default
        for bad in (1080, 360, "720", 720.0, True, None, [540], "Full"):
            with self.assertRaises(ValueError, msg=bad):
                boxcare.check_shaders({"fx_detail": bad}, None)
        self.settings.data["shaders"]["fx_detail"] = "ultra"                            # edited by hand: the board's default, not an error
        self.assertEqual(self.state()["detail"]["value"], "auto")
        self.settings.data["shaders"]["fx_detail"] = True
        self.assertEqual(self.state()["detail"]["value"], "auto")
        self.assertNotIn("shaders", default_settings())                                 # a factory reset leaves no section, so no setting
        # the factory reset itself, as the panel asks for it (boxcare: _wipe_access puts the defaults in place)
        self.fx.set_detail(540)
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.settings.data["shaders"]["fx_detail"], self.state()["on"]["working"]["lines"]), (540, 540))
        st, body, _ = self.call("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, token=full)
        self.assertEqual(st, 200, body)
        self.assertNotIn("fx_detail", self.settings.data.get("shaders", {}))
        with open(self.settings.path) as f:
            self.assertNotIn("fx_detail", json.load(f).get("shaders", {}))                # on disk too
        self.api.registry.set_enabled("shaders", True)                                  # the reset switched the module off
        self.assertEqual((self.state()["detail"]["value"], self.state()["on"]), ("auto", None))
        self.mpv.video = dict(self.mpv.video, w=1920, h=1080)
        self.player.play(["/media/a.mp4"])
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.state()["on"]["working"]["lines"], self.state()["on"]["working"]["auto"]), (self.lines_of("fx-wash.fs"), True))


class FoundOnThePiTest(Base):
    """Three things the measuring run on the Pi 4 found (project-log/JOURNAL.md, "effects on the Pi 4", items 5 and 6)."""
    def test_a_clip_shorter_than_the_guards_window_can_be_called_heavy(self):
        """mpv sets its count of dropped frames back to 0 each time a clip loops. The guard started its window again
        at every such step, so over a clip shorter than its six seconds no effect could ever be "heavy"."""
        now = [100.0]
        self.fx.guard = L.Guard(self.fx, clock=lambda: now[0])
        self.fx.upload("all.fs", ALL)
        self.fx.put("all.fs")
        loads, rates = [], []
        for second in range(1, 25):
            now[0] += 1.0
            self.mpv.drops = 0 if second % 4 == 0 else self.mpv.drops + 5              # a clip of four seconds: the count starts again
            seen = self.state()["on"]
            loads.append(seen["load"])
            rates.append(seen["drops_per_second"])
        self.assertEqual(loads[0], None)                                               # the first seconds are not counted
        self.assertIn("heavy", loads)
        self.assertEqual(loads[-1], "heavy")
        self.assertTrue(all(r is None or r >= 0 for r in rates), rates)                # a count that fell is never a negative rate
        self.assertTrue(3.0 <= rates[-1] <= 5.0, rates[-1])                            # three seconds in four drop five frames
        # the count after it started again is what was dropped since: a clip that loops and drops nothing is quiet
        self.fx.guard = L.Guard(self.fx, clock=lambda: now[0])
        self.mpv.drops = 40
        self.fx.put("fx-wash.fs")
        for second in range(1, 16):
            now[0] += 1.0
            self.mpv.drops = 0 if second % 4 == 0 else self.mpv.drops
            load = self.state()["on"]["load"]
        self.assertEqual((load, self.state()["on"]["drops_per_second"]), ("ok", 0.0))
        # and the generators' guard is the same one
        self.assertIs(type(self.gen.guard), L.Guard)

    def test_the_rate_the_panel_shows_follows_the_clip_also_under_a_filter_that_never_reads_the_clock(self):
        """Such a filter's text is not written anew for a new rate alone (no line that matters has the rate in it),
        and what `on.picture.fps` said was the rate of the clip the effect went on over."""
        self.fx.upload("moves.fs", MOVES)
        self.fx.put("fx-wash.fs")
        text, made, applied = self.mpv.loaded, self.fx._serial, self.fx.changer.applied
        self.assertEqual(self.state()["on"]["picture"]["fps"], 25.0)
        for fps in (50.0, 30.0, 23.976):
            self.mpv.fps = fps
            self.fx.adjust("anchor")
            self.assertEqual(self.state()["on"]["picture"], {"matrix": "bt.709", "levels": "limited", "fps": fps})
            self.assertEqual((self.mpv.loaded, self.fx._serial, self.fx.changer.applied), (text, made, applied), "a new text for a rate no line uses")
        # the next text, made for another reason, has the rate that is shown
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.assertIn("#define TIMEDELTA %s\n" % S._f(1.0 / 23.976), self.text())
        self.assertEqual(self.state()["on"]["picture"]["fps"], 23.976)
        # a stream's estimate that only wobbles is not shown wobbling
        wobble = iter([29.5, 30.4, 29.7, 30.3, 29.4] * 3)
        self.mpv.container, self.mpv.fps = False, lambda: next(wobble)
        self.fx.put("fx-wash.fs")
        shown = self.state()["on"]["picture"]["fps"]
        for _ in range(8):
            self.fx.adjust("anchor")
            self.assertEqual(self.state()["on"]["picture"]["fps"], shown)

    def test_an_error_is_gone_once_it_no_longer_applies(self):
        """`error` in GET /api/effects stayed until that same effect went on again: a refusal of one filter was still
        shown under another that was on and fine, and "nothing is playing" was still shown over a playing clip."""
        self.fx.upload("bad.fs", GOOD.replace("* k", "* oops"))
        FakeTap.lines = list(REFUSAL)
        with self.assertRaises(ApiError):
            self.fx.put("bad.fs")
        FakeTap.lines = []
        self.assertEqual(self.state()["error"]["id"], "bad.fs")
        self.assertEqual(self.state()["error"]["id"], "bad.fs")                        # reading it does not clear it
        self.fx.put("fx-wash.fs")                                                      # another effect is on and fine
        s = self.state()
        self.assertEqual((s["error"], s["on"]["id"]), (None, "fx-wash.fs"))
        self.assertIn("undeclared", next(x for x in s["effects"] if x["id"] == "bad.fs")["refused"])       # the file's own note stays
        # Off: nothing is on and nothing is wanted
        FakeTap.lines = list(REFUSAL)
        with self.assertRaises(ApiError):
            self.fx.put("bad.fs")
        FakeTap.lines = []
        self.assertEqual((self.state()["error"]["id"], self.state()["on"]["id"]), ("bad.fs", "fx-wash.fs"))  # the one before is back on, and the refusal is said
        self.fx.off()
        self.assertIsNone(self.state()["error"])
        # values the GPU refused: said, and gone once a change is taken
        self.fx.upload("all.fs", ALL)
        self.fx.put("all.fs")
        FakeTap.lines = list(REFUSAL)
        self.fx.change({"values": {"mode": 2}})
        self.pump()
        FakeTap.lines = []
        self.assertEqual(self.state()["error"]["id"], "all.fs")
        self.fx.change({"values": {"k": 0.3}})
        self.pump()
        self.assertIsNone(self.state()["error"])
        self.fx.off()
        # a controller's Next with nothing playing: said, and gone as soon as there is a picture
        self.player.clear()
        self.assertTrue(self.fx.step(1)["ok"])
        self.pump()
        self.assertIn("Nothing with a picture is playing", self.state()["error"]["message"])
        self.assertIn("Nothing with a picture is playing", self.state()["error"]["message"])
        self.player.play(["/media/a.mp4"])
        s = self.state()
        self.assertEqual((s["error"], s["available"]), (None, True))


class ReviewTest(Base):
    """What an independent review of the Effect detail pull request found; each test is the review's own scenario."""
    def setUp(self):
        super().setUp()
        self.fx.upload("all.fs", ALL)
        self.mpv.video = dict(self.mpv.video, w=1920, h=1080)

    def test_a_refusal_is_not_wiped_by_an_older_nothing_is_playing(self):
        """A controller's Next with nothing playing left a note that "the error is the no-picture one". A clip, an
        effect and a refused change later, with nobody having asked for the state in between, the first look took
        the refusal for that old error and cleared it."""
        self.player.clear()
        self.assertTrue(self.fx.step(1)["ok"])
        self.pump()                                                                    # the worker: nothing to put it on
        self.assertIn("Nothing with a picture is playing", self.fx.error["message"])
        self.player.play(["/media/a.mp4"])
        self.fx.put("all.fs")                                                          # error None; no state() was asked for
        FakeTap.lines = list(REFUSAL)
        self.fx.change({"values": {"mode": 2}})
        self.pump()                                                                    # the GPU refuses the new values
        FakeTap.lines = []
        s = self.state()
        self.assertEqual((s["available"], s["error"] and s["error"]["id"]), (True, "all.fs"))
        self.assertIn("undeclared", s["error"]["message"])
        self.assertIn("undeclared", self.state()["error"]["message"])                  # and it stays over the next look
        # only the no-picture answer is ever cleared by a picture: another 409 from the worker stays
        self.fx.off()
        self.assertTrue(self.fx.step(1)["ok"])
        real, other = self.fx.available, "Another reason why none can go on now."
        self.fx.available = lambda: (False, other)
        self.pump()
        self.fx.available = real
        s = self.state()
        self.assertEqual((s["available"], s["error"]["message"]), (True, other))
        # and the no-picture answer still goes when a picture comes
        self.fx.off()
        self.player.clear()
        self.fx.step(1)
        self.pump()
        self.assertIn(E.NO_PICTURE, self.state()["error"]["message"])
        self.player.play(["/media/a.mp4"])
        self.assertIsNone(self.state()["error"])

    def guard(self):
        now = [100.0]
        self.fx.guard = L.Guard(self.fx, clock=lambda: now[0])
        return now

    def looks(self, now, counts):
        """One look a second, the two counts as given; ([load], [dropped a second]) as the panel was told."""
        loads, rates = [], []
        for frame, decoder in counts:
            now[0] += 1.0
            self.mpv.drops, self.mpv.decoder_drops = frame, decoder
            on = self.state()["on"]
            loads.append(on["load"])
            rates.append(on["drops_per_second"])
        return loads, rates

    def test_a_count_that_falls_but_not_to_a_fresh_start_is_not_dropped_frames(self):
        """The guard took any fall of the player's count for a start from 0 and counted the whole new number as
        frames dropped since: 5000 to 4990 read as 1663 a second, and two such falls as a heavy effect."""
        now = self.guard()
        self.mpv.drops = 5000
        self.fx.put("all.fs")
        loads, rates = self.looks(now, [(5000, 0)] * 5 + [(4990, 0)] + [(4990, 0)] * 3 + [(4980, 0)] + [(4980, 0)] * 8)
        self.assertNotIn("heavy", loads)
        self.assertNotIn("tight", loads)
        self.assertTrue(all(r is None or r == 0.0 for r in rates), rates)
        self.assertEqual((loads[-1], rates[-1]), ("ok", 0.0))                          # and it is watched again afterwards
        # a real start from 0 with a few frames dropped since is still counted (a short clip that loops)
        g = L.Guard(self.fx)
        self.assertEqual((g._rise((40, 0), (3, 0), 1.0), g._rise((40, 7), (43, 7), 1.0), g._rise((40, 7), (0, 0), 1.0)), (3, 3, 0))
        self.assertEqual(g._rise((40, 0), (8, 0), 1.0), 8)                             # at most PEAK a second can be new
        self.assertIsNone(g._rise((40, 0), (9, 0), 1.0))
        self.assertIsNone(g._rise((5000, 0), (4990, 0), 1.0))
        self.assertEqual(g._rise((5, None), (9, None), 1.0), 4)                        # a player that has only one of the counts
        self.assertEqual(g._rise((5, None), (9, 3), 1.0), 4)                           # a count that has only now appeared says nothing yet

    def test_one_count_starting_again_alone_is_not_the_other_counts_frames(self):
        """The two counts are read in two requests and were added up: when one started again by itself the sum fell,
        and the whole of the other count was taken for frames dropped in that second."""
        now = self.guard()
        self.mpv.drops, self.mpv.decoder_drops = 100, 50
        self.fx.put("all.fs")
        loads, rates = self.looks(now, [(100, 50)] * 5 + [(100, 0)] + [(100, 0)] * 3 + [(0, 0)] + [(0, 0)] * 8)
        self.assertNotIn("heavy", loads)
        self.assertNotIn("tight", loads)
        self.assertTrue(all(r is None or r == 0.0 for r in rates), rates)
        # and frames really dropped are still seen, by either count, across a start from 0
        now = self.guard()
        self.mpv.drops = self.mpv.decoder_drops = 0
        self.fx.put("fx-wash.fs")
        counts, f, d = [], 0, 0
        for second in range(1, 21):
            f = 0 if second % 4 == 0 else f + 2
            d = 0 if second % 5 == 0 else d + 2
            counts.append((f, d))
        loads, rates = self.looks(now, counts)
        self.assertEqual(loads[-1], "heavy")
        self.assertTrue(2.0 <= rates[-1] <= 4.0, rates)

    def test_choosing_a_detail_keeps_an_amount_that_is_on_its_way_and_the_presets_name(self):
        self.fx.put("all.fs")
        self.fx.change({"controls": {"amount": 0.25}})                                 # noted, not yet in the player
        self.fx.set_detail(720)                                                        # before the worker came round
        self.pump()
        on = self.state()["on"]
        self.assertEqual((on["controls"]["amount"], on["working"]["lines"]), (0.25, 720))
        self.assertIn("pvj_native(c), 0.25)", self.text())
        self.assertIn(E.size_lines(720)[0], self.text())
        # with a preset on, a new detail is not a change of the effect's values: the preset is still what is on
        self.fx.preset_save("Kept")
        self.assertEqual(self.state()["on"]["preset"], "Kept")
        text = self.mpv.loaded
        self.fx.set_detail(540)
        self.assertTrue(self.state()["on"]["pending"])
        self.pump()
        on = self.state()["on"]
        self.assertEqual((on["preset"], on["working"]["lines"], on["controls"]["amount"]), ("Kept", 540, 0.25))
        self.assertNotEqual(self.mpv.loaded, text)
        self.assertIn(E.size_lines(540)[0], self.text())

    def test_an_amount_too_small_to_be_written_is_amount_0(self):
        """1e-40 is written into a text as 0.0. It was still taken for "above 0": the whole capped pass was drawn to
        show the unfiltered clip, at the working size."""
        p = S.parse(GOOD, S.FILTER)
        for tiny in (1e-40, 1e-31, 4.9e-324, -0.0, 0):
            self.assertEqual(L.clean_fx_controls({"amount": tiny})["amount"], 0.0, tiny)
            self.assertEqual(E.translate(p, controls={"amount": tiny}, lines=720).count("//!WHEN 0\n"), 2, tiny)
        for small in (1e-29, 1e-6, 0.001):                                             # what is written as a number is one
            self.assertEqual(L.clean_fx_controls({"amount": small})["amount"], small)
            text = E.translate(p, controls={"amount": small}, lines=720)
            self.assertNotIn("//!WHEN", text)
            self.assertNotIn("pvj_native(c), 0.0)", text)
        self.fx.set_detail(720)
        self.fx.put("all.fs", controls={"amount": 1e-40})
        self.assertEqual(self.text().count("//!WHEN 0\n"), 2)
        self.assertEqual((self.state()["on"]["controls"]["amount"], self.state()["on"]["checked"]), (0.0, None))
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.fx.change({"controls": {"amount": 1e-40}})
        self.pump()
        self.assertEqual(self.text().count("//!WHEN 0\n"), 2)



class WishTap:
    """A listener during whose listening (the GPU's look at a new text) the wishes in `during` are made."""
    during = []

    def __init__(self, path, level="error"):
        pass

    def drain(self, seconds):
        while WishTap.during:
            WishTap.during.pop(0)()
        return []

    def close(self):
        pass


WINDOWS = ("before the worker takes any", "while the worker holds the one before", "at its call to the player", "during the GPU's look", "after it")


class QueueTest(Base):
    """Wishes that wait for the worker (a controller's Next, Previous, On, a preset): of two, three or five quick
    ones the older never wins and none is lost, wherever the worker is with the one before. The windows are made
    here on one thread: the wishes after the first are made at the named moment of the first one's way."""
    def setUp(self):
        super().setUp()
        self.fx.upload("all.fs", ALL)
        self.fx._tap = WishTap
        WishTap.during = []
        self.at_player, self.at_start = [], []
        job = self.fx.play_job                          # the worker has taken the job and has done nothing with it yet

        def play_job(body):
            while self.at_start:
                self.at_start.pop(0)()
            return job(body)
        self.fx.play_job = play_job
        real = self.player.put_effect                   # the real one, lock and all, with the wishes made just before it

        def put_effect(*args, **kwargs):
            while self.at_player:
                self.at_player.pop(0)()
            return real(*args, **kwargs)
        self.player.put_effect = put_effect
        self.ids = self.fx.order()
        self.assertGreater(len(self.ids), 6)

    def drain(self):
        for _ in range(20):
            if self.fx.changer.queued() is None and self.fx.changer.pending() is None:
                return
            self.pump()
        self.fail("the queue never emptied")

    def make(self, window, wishes):
        """Make the wishes, the ones after the first in `window` of the first one's way, and let the worker finish."""
        first, rest = wishes[0], list(wishes[1:])
        self.fx._checked.clear()                        # so that the GPU "looks" (and the listener is there) each time
        if window == WINDOWS[0]:
            for w in wishes:
                w()
        elif window == WINDOWS[1]:
            first()
            self.at_start = rest
            self.pump()
        elif window == WINDOWS[2]:
            first()
            self.at_player = rest
            self.pump()
        elif window == WINDOWS[3]:
            first()
            WishTap.during = rest
            self.pump()
        else:
            for w in wishes:
                w()
                self.drain()
        self.assertEqual((self.at_start, self.at_player, WishTap.during), ([], [], []), "the wishes were not made in the window: %s" % window)
        self.drain()

    def on(self):
        return (self.state()["on"] or {}).get("id")

    def test_quick_steps_add_up_in_every_window(self):
        nxt, prev = (lambda: self.fx.step(1)), (lambda: self.fx.step(-1))
        for window in WINDOWS:
            for wishes, moved in (([nxt] * 2, 2), ([nxt] * 3, 3), ([nxt] * 5, 5), ([prev] * 3, -3), ([nxt, nxt, prev], 1), ([nxt, prev, prev, prev, nxt], -1)):
                self.fx.put(self.ids[2])
                self.make(window, wishes)
                self.assertEqual(self.on(), self.ids[(2 + moved) % len(self.ids)], "%d wishes %s: %+d" % (len(wishes), window, moved))
                self.assertIsNone(self.fx.error, window)
                self.assertIsNone(self.fx._intent, window)
            # from nothing on: the first step forward is the first effect, and the others count on from it
            self.fx.off()
            self.make(window, [nxt] * 3)
            self.assertEqual(self.on(), self.ids[2], window)
            self.fx.off()
            self.make(window, [prev] * 2)
            self.assertEqual(self.on(), self.ids[-2], window)

    def test_the_answer_names_the_effect_the_steps_lead_to(self):
        self.fx.put(self.ids[2])
        self.assertEqual([self.fx.step(1)["id"] for _ in range(3)], self.ids[3:6])
        self.assertEqual(self.fx.step(-1)["id"], self.ids[4])
        self.drain()
        self.assertEqual(self.on(), self.ids[4])

    def test_on_then_a_preset_and_a_step_then_a_preset_in_every_window(self):
        self.fx.put("all.fs", {"k": 1.5})
        self.fx.preset_save("One")
        for window in WINDOWS:
            # the one button, then a preset by its place: the preset is meant for the effect that is coming
            self.fx.put("all.fs")
            self.fx.off()                               # all.fs was on last: it is what the one button puts back
            self.make(window, [lambda: self.fx.toggle(), lambda: self.fx.apply_preset({"index": 1})])
            on = self.state()["on"]
            self.assertEqual((on["id"], on["preset"], on["values"]["k"]), ("all.fs", "One", 1.5), window)
            # a step, then a preset of a named effect: the newer wish names where to go
            self.fx.put(self.ids[2])
            self.make(window, [lambda: self.fx.step(1), lambda: self.fx.apply_preset({"id": "all.fs", "name": "One"})])
            on = self.state()["on"]
            self.assertEqual((on["id"], on["preset"]), ("all.fs", "One"), window)
            # a preset of a named effect, then a step: one on from that effect, without its preset
            self.fx.put(self.ids[2])
            self.make(window, [lambda: self.fx._queue(self.ids[4]), lambda: self.fx.step(1)])
            self.assertEqual(self.on(), self.ids[5], window)
            self.assertIsNone(self.fx.error, window)

    def test_off_and_an_effect_by_hand_are_newer_than_what_waits_in_every_window(self):
        nxt = lambda: self.fx.step(1)
        for window in WINDOWS[:4]:
            self.fx.put(self.ids[2])
            self.make(window, [nxt, nxt, lambda: self.fx.off()])
            self.assertEqual((self.on(), self.mpv.loaded, self.fx.error), (None, [], None), window)
            self.fx.put(self.ids[2])
            self.make(window, [nxt, nxt, lambda: self.fx.toggle()])                    # a controller's Off
            self.assertEqual((self.on(), self.mpv.loaded, self.fx.error), (None, [], None), window)
        for window in WINDOWS[:2]:                                                     # (by hand waits for the engine's lock: not from inside the worker)
            self.fx.put(self.ids[2])
            self.make(window, [nxt, nxt, lambda: self.fx.put("all.fs")])
            self.assertEqual((self.on(), len(self.mpv.loaded), self.fx.error), ("all.fs", 1, None), window)

    def test_a_clip_a_generator_and_a_vibes_step_in_between_do_not_drop_a_wish(self):
        """An effect stays through a change of what plays (D74), so a wish for one does too. Before, the wish was
        stamped with the player's epoch, a rotation's step moved it, and the controller's Next came to nothing
        without a word."""
        for name, between in (("a clip", lambda: self.player.play(["/media/b.mov"])),
                              ("a generator", lambda: self.gen.show("nxlx-silk.fs")),
                              ("a rotation's step", lambda: self.gen.show("nxlx-ember.fs", epoch=self.player.source_epoch, cut=False))):
            self.fx.put(self.ids[2])
            self.fx.step(1)
            self.fx.step(1)
            between()
            path = self.mpv.props["path"]
            self.drain()
            self.assertEqual((self.on(), self.fx.error), (self.ids[4], None), name)
            self.assertEqual(self.mpv.props["path"], path, name)                       # and what was played keeps the screen
            self.player.play(["/media/a.mp4"])

    def test_a_stop_in_between_keeps_the_screen_clear_and_the_wish_says_so(self):
        for wish in (lambda: self.fx.step(1), lambda: (self.fx.off(), self.fx.toggle()), lambda: (self.fx.off(), self.fx.apply_preset({"id": "all.fs", "index": 1}))):
            self.player.play(["/media/a.mp4"])
            self.fx.put("all.fs")
            self.fx.preset_save("One")
            self.fx.error = None
            wish()
            self.api.control({"action": "stop"}, None, "t")
            self.player.play(["/media/a.mp4"])                                         # and a clip after the Stop: it starts clean
            self.drain()
            s = self.state()
            self.assertEqual((s["on"], self.mpv.loaded), (None, []))
            self.assertEqual((s["error"]["message"], s["error"]["kind"]), ("Stop was pressed after it was asked for, so it was not put on. Ask again.", "wish"))
            self.assertIsNone(self.fx._intent)
            # with nothing playing after the Stop it is the other answer, also said
            self.fx.error = None
            wish()
            self.api.control({"action": "stop"}, None, "t")
            self.drain()
            self.assertIn("Nothing with a picture is playing", self.state()["error"]["message"])
            self.assertEqual(self.state()["error"]["kind"], "wish")
        # the player's side by itself: only a clearing and the panel's restart move the count
        clears = self.player.clears
        self.player.play(["/media/b.mov"])
        self.gen.show("nxlx-silk.fs")
        self.fx.put("all.fs")
        self.fx.off()
        self.assertEqual(self.player.clears, clears)
        self.player.clear()
        self.assertEqual(self.player.clears, clears + 1)
        self.player.quit()
        self.assertEqual(self.player.clears, clears + 2)

    def test_five_quick_steps_against_the_real_worker_thread(self):
        """The same on two real threads: the worker runs by itself and the steps come as fast as a held button
        sends them, with small pauses drawn from a seed, so that they fall into every window by chance."""
        cfg = self.gen.config()
        cfg["faster"] = True                            # no gap between two switches: the worker is as quick as it can be
        self.gen._save(cfg)
        class SlowTap(WishTap):                         # the GPU's look takes a moment, so steps pile up behind it
            def drain(self, seconds):
                time.sleep(0.03)
                return []
        self.fx._tap = SlowTap
        self.fx.changer._use_thread = True
        rng = random.Random(7)
        for trial in range(12):
            self.fx._checked.clear()
            self.fx.put(self.ids[2])
            count = rng.choice((2, 3, 5))
            for _ in range(count):
                self.fx.step(1)
                time.sleep(rng.choice((0.0, 0.0, 0.001, 0.004, 0.012)))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and (self.fx.changer.queued() is not None or self.fx.changer.working is not None or self.fx._intent):
                time.sleep(0.005)
            self.assertEqual(self.on(), self.ids[(2 + count) % len(self.ids)], "trial %d: %d steps" % (trial, count))
            self.assertIsNone(self.fx.error)
        self.fx.changer._use_thread = False
        self.fx.off()

    def test_a_wish_says_what_cleared_the_screen_and_a_player_that_came_back_by_itself_is_a_clearing(self):
        # Vibes stopped (a shader taken off the screen) is not "Stop"
        self.gen.show("nxlx-silk.fs")
        self.fx.step(1)
        self.gen.off()
        self.player.play(["/media/a.mp4"])
        self.drain()
        self.assertEqual((self.on(), self.mpv.loaded), (None, []))
        self.assertEqual(self.state()["error"]["message"], "The shader was taken off the screen (Vibes stopped or ended) after it was asked for, so it was not put on. Ask again.")
        # the player crashed and its service started a new one, which nobody here asked for; something plays on
        # the new one before the worker comes round. The wish was for the old one's screen.
        for noticed_by in ("the next play", "the worker itself"):
            self.fx.error = None
            self.player.play(["/media/a.mp4"])
            clears = self.player.clears
            self.fx.step(1)
            self.mpv.restart()
            if noticed_by == "the next play":
                self.player.play(["/media/b.mov"])
                self.assertEqual(self.player.clears, clears + 1)
            else:
                self.mpv.props["path"] = "/media/b.mov"                                # (what its autostart put there)
            self.drain()
            self.assertEqual((self.on(), self.mpv.loaded), (None, []), noticed_by)
            self.assertEqual(self.state()["error"]["message"], "The player was restarted after it was asked for, so it was not put on. Ask again.", noticed_by)
            self.assertEqual(self.player.clears, clears + 1, noticed_by)
        self.fx.step(1)                                                                # and a wish made after it goes on
        self.drain()
        self.assertEqual(self.on(), self.ids[0])

    def test_an_off_from_a_controller_holds_when_the_next_behind_it_comes_to_nothing(self):
        """Off, then Next, quickly: the Next takes the Off's place in the queue, and the Off had not reached the
        player. The Off comes first all the same, so the old effect is off whatever becomes of the Next."""
        self.fx.upload("bad.fs", GOOD.replace("* k", "* oops"))
        ids = self.fx.order()
        self.assertEqual(ids[-1], "bad.fs")                                            # an upload: the last, so Previous from none goes to it
        self.fx._tap = FakeTap
        for name, lines, want in (("a Next that goes on", [], ids[0]), ("a Previous the GPU refuses", list(REFUSAL), None)):
            self.fx.put("all.fs")
            self.fx._refusals.clear()
            self.assertEqual(self.fx.toggle(), {"ok": True, "on": False})
            self.assertEqual(len(self.mpv.loaded), 1)                                  # noted only: the player has heard nothing yet
            FakeTap.lines = lines
            self.fx.step(1 if want else -1)
            self.assertTrue(self.fx.changer.queued()["off_first"], name)
            self.drain()
            FakeTap.lines = []
            self.assertEqual(self.on(), want, name)
            self.assertEqual(len(self.mpv.loaded), 1 if want else 0, name)             # never the old effect left on
            self.assertEqual((self.state()["error"] or {}).get("id"), None if want else "bad.fs", name)
        # and with no picture to put the Next on: the Off still held
        self.fx.put("all.fs")
        self.fx.toggle()
        self.fx.step(1)
        self.mpv.video = None
        self.drain()
        self.assertEqual((self.on(), self.mpv.loaded), (None, []))
        self.assertIn("Nothing with a picture is playing", self.state()["error"]["message"])

    def test_an_effect_put_on_by_hand_after_an_off_and_a_next_is_not_taken_off_by_that_off(self):
        """A on; a controller's Off; a controller's Next (which carries the Off before it); and within the gap
        between two switches somebody puts B on by hand. B is the newest wish: the Off the Next carries is older."""
        self.fx.put(self.ids[2])
        self.fx.toggle()
        self.fx.step(1)
        self.assertTrue(self.fx.changer.queued()["off_first"])
        self.fx.put("all.fs")                                                          # by hand, while the two wait
        self.drain()
        self.assertEqual((self.on(), len(self.mpv.loaded), self.fx.error), ("all.fs", 1, None))

    def test_a_restart_is_counted_once_and_a_wish_made_after_the_panels_own_restart_is_for_the_new_player(self):
        # status polls from several threads meet a new mpv: one clearing, not one for each
        self.player.is_running()
        for trial in range(30):
            clears = self.player.clears
            self.mpv.restart()
            gate = threading.Barrier(6)

            def poll():
                gate.wait()
                self.player.is_running()
            threads = [threading.Thread(target=poll) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
            self.assertEqual(self.player.clears, clears + 1, trial)
        # the panel ends the player (counted then); a wish made before anyone has heard of the new one is for the
        # new one, and is not dropped as "the player was restarted"
        self.player.play(["/media/a.mp4"])
        clears = self.player.clears
        self.player.quit()
        self.assertEqual((self.player.clears, self.player.cleared_by), (clears + 1, "restart"))
        self.fx.error = None
        self.fx.step(1)                                                                # after the quit, before the new mpv is heard
        self.mpv.restart()
        self.player.play(["/media/b.mov"])                                             # the new one plays
        self.assertEqual(self.player.clears, clears + 1)
        self.drain()
        self.assertEqual((self.on(), self.fx.error), (self.ids[0], None))
        self.mpv.restart()                                                             # and a restart nobody asked for, later, counts again
        self.player.is_running()
        self.assertEqual(self.player.clears, clears + 2)
        # a quit the player did not take (it does not answer): the same mpv lives on, and the restart that comes
        # later by itself is not taken for the panel's own
        clears = self.player.clears
        self.mpv.down = True
        with self.assertRaises(PlayerError):
            self.player.quit()
        self.mpv.down = False
        self.assertFalse(self.player._new_expected)
        self.player.play(["/media/a.mp4"])
        self.fx.error = None
        self.fx.step(1)                                                                # a wish, and then the crash
        self.mpv.restart()
        self.player.play(["/media/b.mov"])
        self.assertEqual(self.player.clears, clears + 2)                               # the quit's own count, and the crash
        self.drain()
        self.assertEqual(self.on(), None)
        self.assertIn("The player was restarted after it was asked for", self.state()["error"]["message"])

    def test_a_wish_that_cannot_be_carried_out_says_why(self):
        self.fx.put(self.ids[2])
        self.fx.step(1)
        with self.assertRaises(ApiError) as c:                                         # a preset the coming effect does not have: said at once
            self.fx.apply_preset({"name": "nobody saved this"})
        self.assertEqual(c.exception.status, 404)
        self.assertIn(self.ids[3], c.exception.message)
        self.drain()
        self.assertEqual(self.on(), self.ids[3])
        self.fx.upload("bad.fs", GOOD.replace("* k", "* oops"))                        # a step onto a file the GPU refuses: said, the one before stays
        ids = self.fx.order()
        self.fx.put(ids[ids.index("bad.fs") - 1])
        self.fx._tap = FakeTap
        FakeTap.lines = list(REFUSAL)
        self.fx.step(1)
        self.drain()
        FakeTap.lines = []
        s = self.state()
        self.assertEqual((s["on"]["id"], s["error"]["id"], s["error"]["kind"]), (ids[ids.index("bad.fs") - 1], "bad.fs", "gpu"))
        self.fx.step(1)                                                                # and the next Next passes it by
        self.drain()
        self.assertEqual(self.on(), ids[(ids.index("bad.fs") + 1) % len(ids)])


CARRIER_VIDEO = {"colormatrix": "rgb", "colorlevels": "full", "pixelformat": "rgb0", "w": 64, "h": 36}     # what a real mpv said in CI (the spike)
DUMP = [("vo/gpu/opengl", "error", "fragment shader source:"), ("vo/gpu/opengl", "error", "[  1] #version 140"),
        ("vo/gpu/opengl", "error", "[ 12] // %s"), ("vo/gpu/opengl", "error", "[ 40] gl_FragColor = oops;"),
        ("vo/gpu/opengl", "error", "fragment shader compile log (status=0):"), ("vo/gpu/opengl", "error", "0:40(16): error: `oops' undeclared")]


class OwnTap:
    """A tap whose lines are its class's own: FakeTap's are shared by both engines, and here one engine's shader is
    refused while the other's is taken."""
    lines = []

    def __init__(self, path, level="error"):
        self.sent = False

    def drain(self, seconds):
        out, self.sent = ([] if self.sent else list(type(self).lines)), True
        return out

    def close(self):
        pass


class GenTap(OwnTap):
    lines = []


class FxTap(OwnTap):
    lines = []


class OverShaderTest(Base):
    """An effect over a generator shader (D74). What the player really draws for the pair is in tests/test_pair_gpu.py;
    here is the engines' side, against the stand-in for mpv, which says of a carrier what the real one said in CI."""
    def setUp(self):
        super().setUp()
        self.gen._tap, self.fx._tap = GenTap, FxTap
        GenTap.lines, FxTap.lines = [], []
        self.settings.data["mix"]["duration"] = 0.1
        self.api.vibes._sleep = lambda s: None

    def show(self, sid="nxlx-silk.fs"):
        """A generator takes the screen, and the stand-in speaks of its carrier from then on."""
        r = self.gen.show(sid)
        self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0
        return r

    def clip(self, path="/media/a.mp4"):
        self.player.play([path])
        self.mpv.video, self.mpv.fps = {"colormatrix": "bt.709", "colorlevels": "limited", "pixelformat": "yuv420p", "w": 1920, "h": 1080}, 25.0

    def kinds(self):
        return [n.split("-")[0] for n in self.mpv.loaded]

    def refuse(self):
        """The GPU refuses the next text of the effect, as a real player says it: the text with its name in it, then
        the compiler's words. (Over a generator a complaint that names no shader is nobody's: shaders.about.)"""
        FxTap.lines = [(p, level, t % ("nxlx effect %d %d" % (os.getpid(), self.fx._serial + 1)) if "%s" in t else t) for p, level, t in DUMP]

    def lists(self, since):
        return [[os.path.basename(x).split("-")[0] for x in c[2]] for c in self.mpv.commands[since:] if c[:2] == ("set_property", "glsl-shaders")]

    def test_an_effect_goes_on_over_a_generator_and_the_generator_comes_first_in_the_list(self):
        self.show()
        s = self.state()
        self.assertEqual((s["available"], s["unavailable"], s["on"]), (True, None, None))
        epoch = self.player.source_epoch
        self.assertEqual(self.fx.put("fx-wash.fs"), {"ok": True, "id": "fx-wash.fs"})
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.assertEqual(self.player.source_epoch, epoch)                              # the screen has not changed hands
        self.assertEqual(self.gen.on_screen()["id"], "nxlx-silk.fs")                   # the generator was never disturbed
        on = self.state()["on"]
        self.assertEqual((on["id"], on["checked"], on["picture"]), ("fx-wash.fs", True, {"matrix": "rgb", "levels": "full", "fps": 30.0}))
        st = self.api.status({}, None, "t")["player"]
        self.assertEqual((st["shader"], st["effect"], st["path"]), ("nxlx-silk", "fx-wash", None))
        # Next, Previous and the one button work over a generator too (they were refused with 409)
        self.assertTrue(self.fx.step(1, ask=True)["ok"])
        self.pump()
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.assertNotEqual(self.state()["on"]["id"], "fx-wash.fs")
        self.assertEqual(self.fx.toggle(ask=True), {"ok": True, "on": False})
        self.pump()
        self.assertEqual((self.kinds(), self.state()["on"]), (["shader"], None))
        self.assertTrue(self.fx.toggle(ask=True)["on"])
        self.pump()
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.fx.off()
        self.assertEqual((self.kinds(), self.texts(), self.state()["last"]), (["shader"], [], None))
        self.assertIsNotNone(self.gen.on_screen())

    def test_a_generator_chosen_while_an_effect_is_on_leaves_it_on(self):
        """As it stays when a clip changes. The effect is in the list with the generator from the generator's first
        command on: no moment without it."""
        self.fx.put("fx-wash.fs", {"strength": 0.5})
        serial, text = self.player.effect_serial, self.texts()
        before = len(self.mpv.commands)
        self.show()
        self.assertEqual(self.lists(before), [["shader", "effect"]])
        self.assertEqual((self.player.effect_serial, self.texts(), self.state()["last"]), (serial, text, None))
        on = self.state()["on"]
        self.assertEqual((on["id"], on["values"]["strength"]), ("fx-wash.fs", 0.5))
        self.assertTrue(self.fx.changer._refresh is not None)                          # the worker goes on looking at the picture under it
        # the worker's look: another kind of picture came under it (RGB, 30 a second), so a new text of the same effect
        self.fx.adjust("anchor")
        self.assertEqual((self.kinds(), self.player.effect_serial), (["shader", "effect"], serial))
        self.assertEqual(self.state()["on"]["picture"], {"matrix": "rgb", "levels": "full", "fps": 30.0})
        self.assertEqual(self.state()["on"]["working"]["under"], "shader")
        was = self.mpv.loaded
        self.show("nxlx-ember.fs")                                                     # another generator: the same picture for the effect
        self.fx.adjust("anchor")
        self.assertEqual(self.mpv.loaded[1], was[1])
        self.assertEqual((self.kinds(), self.state()["on"]["id"]), (["shader", "effect"], "fx-wash.fs"))
        # a value of the effect and a value of the generator change, each its own text, neither touches the other
        self.fx.change({"values": {"strength": 0.9}})
        self.pump()
        self.assertEqual((self.kinds(), self.state()["on"]["values"]["strength"]), (["shader", "effect"], 0.9))
        self.clip()                                                                    # a clip again: the generator goes, the effect stays
        self.assertEqual((self.kinds(), self.state()["on"]["id"]), (["effect"], "fx-wash.fs"))
        self.fx.adjust("anchor")
        self.assertEqual((self.state()["on"]["picture"]["matrix"], self.state()["on"]["working"]["under"]), ("bt.709", "clip"))

    def test_the_size_the_panel_is_told_is_the_generators_drawing_not_the_carrier(self):
        """The player speaks of the carrier (64 x 36 in CI); the effect meets the generator's drawing (seen there by
        pixel). Effect detail caps the effect only where the generator draws more lines than the cap."""
        self.api.board = dict(self.api.board, kind="pi4")
        screen = self.gen.screen
        self.gen.screen = lambda: (1920, 1080)
        self.addCleanup(setattr, self.gen, "screen", screen)
        for height, sid, want in ((540, "fx-wash.fs", (720, 960, 540, False, None)),         # Automatic's 720 is above the generator's 540 lines
                                  (720, "fx-wash.fs", (720, 1280, 720, False, 540)),
                                  (720, "isf-edge-blowout.fs", (540, 960, 540, True, None))):     # the generator draws more: the effect works at its cap
            cfg = self.gen.config()
            cfg["height"] = height
            self.gen._save(cfg)
            self.show()
            self.fx.put(sid)
            w = self.state()["on"]["working"]
            self.assertEqual((w["under"], w["clip"]["lines"], w["auto"]), ("shader", height, True), (height, sid))
            self.assertEqual((w["lines"], w["width"], w["height"], w["scaled"], w["lower"]), want, (height, sid))
            self.assertEqual("//!WIDTH" in self.text_of("effect"), True)                # the cap is in the text either way: the player decides
            self.fx.off()
        # black after a refused generator: a carrier with no record of a drawing, so no size is said
        self.show()
        self.gen.playing = None
        self.fx.put("fx-wash.fs")
        w = self.state()["on"]["working"]
        self.assertEqual((w["under"], w["clip"], w["scaled"]), ("shader", None, False))

    def text_of(self, kind):
        path = next(p for p in self.mpv.props["glsl-shaders"] if os.path.basename(p).startswith(kind))
        with open(path) as f:
            return f.read()

    def test_it_stays_through_a_vibes_rotation_and_its_dip(self):
        self.fx.put("fx-wash.fs")
        serial = self.player.effect_serial
        vibes = self.api.vibes
        vibes.start()
        before = len(self.mpv.commands)
        self.assertTrue(vibes.tick())                                                  # the first shader, after a dip from the clip
        self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0
        self.assertTrue(vibes.running)
        self.assertEqual((self.kinds(), self.state()["on"]["id"], self.player.effect_serial), (["shader", "effect"], "fx-wash.fs", serial))
        first = self.mpv.loaded[0]
        for _ in range(2):                                                             # two more steps of the rotation
            vibes.skip()
            vibes.tick()
            self.fx.adjust("anchor")
            self.assertEqual((self.kinds(), self.state()["on"]["id"]), (["shader", "effect"], "fx-wash.fs"))
        self.assertNotEqual(self.mpv.loaded[0], first)
        # at no moment of it was the effect out of the list, and the rotation's own dip (the brightness) went on
        # with the effect in it: it is the filtered picture that dips and comes back
        seen = self.mpv.commands[before:]
        self.assertTrue(all("effect" in kinds for kinds in self.lists(before)), self.lists(before))
        self.assertGreaterEqual(len(self.lists(before)), 3)
        self.assertTrue(any(c[:2] == ("set_property", "brightness") and c[2] < 0 for c in seen), "the rotation made no dip in this test")
        self.assertEqual(self.player.effect_serial, serial)                            # the same effect all along: never off and on again
        # stopping Vibes clears the screen, and the effect goes with the picture it was over
        vibes.stop()
        self.assertEqual((self.mpv.loaded, self.mpv.props["path"], self.texts()), ([], None, []))
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "the shader under it was taken off the screen"))

    def test_stop_a_restart_and_the_module_going_off_take_the_pair_off(self):
        self.show()
        self.fx.put("fx-wash.fs")
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual((self.mpv.loaded, self.texts(), self.mpv.props["fbo-format"]), ([], [], "auto"))
        self.assertEqual((self.state()["on"], self.state()["last"], self.gen.on_screen()), (None, "Stop was pressed", None))
        self.show()                                                                    # the next shader starts clean
        self.assertEqual(self.kinds(), ["shader"])
        self.fx.put("fx-wash.fs")
        self.mpv.restart()                                                             # a new mpv: it has neither
        self.assertEqual((self.state()["on"], self.state()["last"], self.gen.on_screen()), (None, "the player was restarted", None))
        self.clip()
        self.assertEqual(self.mpv.loaded, [])
        self.show()
        self.fx.put("fx-wash.fs")
        self.api.set_module("shaders", {"enabled": False}, None, "t")
        self.assertEqual((self.mpv.loaded, self.mpv.props["path"], self.texts()), ([], None, []))
        self.api.registry.set_enabled("shaders", True)
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "the module was switched off"))

    def test_a_pair_the_gpu_refuses_leaves_the_generator_on_and_is_not_sent_again(self):
        self.show()
        gen = self.mpv.loaded
        self.refuse()
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertEqual(c.exception.status, 422)
        for words in ("over the shader", "undeclared", "No effect is on.", "The shader stays on the screen."):
            self.assertIn(words, c.exception.message)
        self.assertEqual((self.mpv.loaded, self.texts(), self.state()["on"]), (gen, [], None))
        self.assertEqual(self.gen.on_screen()["id"], "nxlx-silk.fs")
        self.assertEqual(self.state()["error"]["id"], "fx-wash.fs")
        # again, by hand and through the worker: the GPU is not shown it a second time (no flash)
        FxTap.lines = []
        before = len(self.mpv.commands)
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("refused fx-wash.fs over this shader before", c.exception.message)
        self.assertIn("not sent again", c.exception.message)
        self.fx._queue("fx-wash.fs")
        self.pump()
        self.assertIn("not sent again", self.state()["error"]["message"])
        self.assertEqual((self.lists(before), self.mpv.loaded, self.texts()), ([], gen, []))
        # the memory is of the pair: another effect over this generator is tried, and so is this effect over
        # another generator (one refusal does not refuse it over every shader), and over a clip (another hook runs)
        self.fx.put("fx-vignette.fs")
        self.assertEqual(self.kinds(), ["shader", "effect"])
        self.fx.off()
        self.show("nxlx-ember.fs")
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.kinds(), self.state()["on"]["id"]), (["shader", "effect"], "fx-wash.fs"))
        self.fx.off()
        self.show()                                                                    # the first generator again: still not sent
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertIn("not sent again", c.exception.message)
        self.clip()
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.kinds(), self.state()["on"]["id"], self.state()["error"]), (["effect"], "fx-wash.fs", None))

    def test_a_pairs_refusal_stays_with_the_pair_in_the_library_and_for_next(self):
        """Not by hand: through the library's rows, `order()` and Next. A refusal over one generator said the file
        was refused, for good and everywhere: the row said so over a clip, and Next passed it by over every picture."""
        ids = self.fx.order()
        at = ids.index("fx-wash.fs")
        before, after = ids[at - 1], ids[(at + 1) % len(ids)]
        row = lambda: next(r for r in self.state()["effects"] if r["id"] == "fx-wash.fs")
        step = lambda: (self.fx.step(1), self.pump())
        self.show()
        self.fx.put(before)
        self.refuse()
        step()                                                                         # Next onto it over silk: the GPU refuses the pair
        FxTap.lines = []
        s = self.state()
        self.assertEqual((s["on"]["id"], s["error"]["id"], s["error"]["kind"]), (before, "fx-wash.fs", "gpu"))
        self.assertEqual((row()["refused"], "undeclared" in row()["refused_pair"]), (None, True))    # said of the pair, while silk is under it
        self.assertIn("fx-wash.fs", self.fx.order())
        self.assertNotIn("fx-wash.fs", self.fx.order(self.fx.pair))
        sent = len(self.mpv.commands)
        step()                                                                         # Next again over silk: past it, and the GPU is not shown it
        self.assertEqual((self.state()["on"]["id"], self.kinds()), (after, ["shader", "effect"]))
        self.assertFalse(any("effect" in kinds and len(kinds) != 2 for kinds in self.lists(sent)))
        # over a clip: the row says nothing (at the first look, not one poll later), and Next goes onto it
        self.clip()
        first = row()                                                                  # one request, the first after the clip
        self.assertEqual((first["refused"], first["refused_pair"]), (None, None))
        self.fx.put(before)
        self.assertEqual((row()["refused"], row()["refused_pair"]), (None, None))
        step()
        self.assertEqual((self.state()["on"]["id"], self.state()["error"]), ("fx-wash.fs", None))
        # over another generator: the same
        self.show("nxlx-ember.fs")
        self.fx.put(before)
        self.assertEqual((row()["refused"], row()["refused_pair"]), (None, None))
        step()
        self.assertEqual((self.state()["on"]["id"], self.state()["error"]), ("fx-wash.fs", None))
        # and back over silk it is still that pair's
        self.show()
        self.fx.put(before)
        self.assertIn("undeclared", row()["refused_pair"])
        step()
        self.assertEqual(self.state()["on"]["id"], after)
        # a file the GPU refuses over a clip is the file's, as before
        self.clip()
        self.fx.put(before)
        self.fx._checked.clear()                                                       # (the GPU looks again: it has taken this text before)
        self.refuse()
        step()
        self.assertEqual((row()["refused_pair"], "undeclared" in row()["refused"], "fx-wash.fs" in self.fx.order()), (None, True, False))

    def test_an_effect_refused_over_a_generator_gives_way_to_the_effect_before_it(self):
        self.show()
        self.fx.put("fx-vignette.fs")
        was = self.mpv.loaded
        self.refuse()
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertIn("The effect before it is back on. The shader stays on the screen.", c.exception.message)
        self.assertEqual((self.mpv.loaded, self.state()["on"]["id"]), (was, "fx-vignette.fs"))

    def test_a_generator_that_comes_under_an_effect_the_gpu_refuses_over_it_takes_the_effect_off(self):
        """The effect was taken over a clip (its hook for video ran). Over a generator the text's other hook runs,
        and the GPU has not seen it: the worker's first look listens. A refusal there takes the effect off (there
        is no text to go back to that works over a shader), leaves the generator, and is remembered."""
        self.clip()
        self.fx.put("fx-wash.fs")
        self.show()
        gen = self.mpv.loaded[:1]
        self.refuse()
        self.fx.adjust("anchor")
        self.assertEqual((self.mpv.loaded, self.texts()), (gen, []))
        s = self.state()
        self.assertEqual((s["on"], s["last"], s["error"]["id"]), (None, "the GPU refused it over the shader that came on", "fx-wash.fs"))
        self.assertIn("undeclared", s["error"]["message"])
        self.assertEqual(self.gen.on_screen()["id"], "nxlx-silk.fs")
        FxTap.lines = []
        with self.assertRaises(ApiError) as c:
            self.fx.put("fx-wash.fs")
        self.assertIn("not sent again", c.exception.message)
        # the same meeting once more, with the refusal known: off at the first look, and nothing is sent to the GPU
        self.clip()
        self.fx.put("fx-wash.fs")
        self.show()
        before, effect = len(self.mpv.commands), self.mpv.loaded[1]
        self.fx.adjust("anchor")
        self.assertEqual((self.lists(before), self.state()["on"]), ([["shader"]], None))
        self.assertNotIn(effect, self.mpv.loaded)

    def test_a_refusal_of_the_other_shader_is_not_taken_for_ones_own(self):
        """Both engines listen to the player's one log, each under its own lock. The player prints a refused
        shader's text before the compiler's words, and every text carries its name."""
        named = lambda desc: [(p, level, t % desc if "%s" in t else t) for p, level, t in DUMP]
        other = named("nxlx shader 7 12")
        self.assertEqual(S.shader_errors(other), "line 40: `oops' undeclared")
        self.assertEqual(S.about(other, "nxlx effect 7 3"), [])
        self.assertEqual(S.about(other, "nxlx shader 7 1"), [])                        # "shader 7 12" is not "shader 7 1"
        self.assertEqual(S.about(other, "nxlx shader 7 12"), other)
        both = other + named("nxlx effect 7 3")
        self.assertEqual(S.about(both, "nxlx effect 7 3"), both[len(other):])
        self.assertEqual(S.about(both, "nxlx shader 7 12"), other)
        self.assertEqual(S.about(REFUSAL, "nxlx effect 7 3"), REFUSAL)                 # a log that names no shader: kept, as before
        unnamed = [row for row in other if "// nxlx" not in row[2]]
        self.assertEqual(S.about(unnamed + other, "nxlx effect 7 3"), unnamed)
        # the engines: a generator's refusal in the log while an effect goes on, and the other way round
        self.show()
        FxTap.lines = named("nxlx shader %d 99" % os.getpid())
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.kinds(), self.state()["on"]["checked"], self.state()["error"]), (["shader", "effect"], True, None))
        GenTap.lines = named(self.fx.on["desc"])
        self.assertTrue(self.gen.show("nxlx-ember.fs")["ok"])
        self.assertIsNone(self.gen.error)
        GenTap.lines = []

    def test_vibes_marks_no_shader_heavy_while_an_effect_is_on_and_the_effects_card_says_it(self):
        """The real guards of both engines, on a clock of this test's and the stand-in's count of dropped frames:
        five a second for twelve seconds is "heavy" by the rule (2 a second over 6, after 3 to settle)."""
        now = [100.0]
        for engine in (self.gen, self.fx):
            engine.guard._clock = lambda: now[0]
        vibes = self.api.vibes
        vibes.start()
        vibes.tick()
        self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0
        current = vibes.current

        def seconds(n, judge=True):
            """`n` seconds in which the player drops five frames each; True if Vibes found the shader too heavy."""
            for _ in range(n):
                now[0] += 1.0
                self.mpv.drops += 5
                self.fx.state()                                                        # the effects' card is asked, as a panel asks
                if judge and vibes._guard():
                    return True
            return False
        self.fx.put("fx-wash.fs")
        self.assertIs(seconds(14), False)                                              # the frames are the pair's: no mark, no skip
        seen = self.gen.watch()
        self.assertEqual((seen["state"], seen["drops_per_second"], seen["effect"]), ("heavy", 5.0, "fx-wash.fs"))
        self.assertEqual(self.gen.state()["playing"]["effect"], "fx-wash.fs")
        self.assertEqual((self.settings.data.get("shaders", {}).get("heavy", {}), vibes._marked, vibes._tight, vibes.current), ({}, [], set(), current))
        # the effect's own guard is the one that speaks: its card says the pair is too heavy
        on = self.state()["on"]
        self.assertEqual((on["load"], on["drops_per_second"], on["working"]["under"]), ("heavy", 5.0, "shader"))
        # and it starts over when another shader comes under the effect (the GPU taking it costs frames)
        desc = self.fx.guard._desc
        self.assertEqual(desc, "%s over %s" % (self.fx.on["desc"], self.gen.playing["desc"]))
        vibes.skip()
        vibes.tick()
        self.assertEqual(self.state()["on"]["load"], None)
        self.assertNotEqual(self.fx.guard._desc, desc)
        self.assertIn(self.fx.on["desc"], self.fx.guard._desc)
        # the effect off: Vibes judges the shader by itself again, with a window of its own
        self.fx.off()
        current = vibes.current
        self.assertIs(seconds(14), True)
        self.assertNotIn("effect", self.gen.watch())
        self.assertIsNone(self.gen.state()["playing"]["effect"])
        self.assertIn(current, self.settings.data["shaders"]["heavy"])

    def heavy_seconds(self, now, n, rate=5):
        """`n` seconds in which the player drops `rate` frames each and the worker looks once; True once the box has
        taken the effect off."""
        for _ in range(n):
            now[0] += 1.0
            self.mpv.drops += rate
            self.gen.watch()                            # the generators' guard counts too, as it does while Vibes ticks or a panel is open
            self.fx.adjust("anchor")
            if self.fx.on is None:
                return True
        return False

    def floor_clock(self):
        now = [500.0]
        for guard in (self.fx.guard, self.fx.pair_guard, self.gen.guard):
            guard._clock = lambda: now[0]
        return now

    def test_a_pair_that_stays_too_heavy_loses_its_effect_and_the_shader_plays_on(self):
        """The floor under the suspended guard: nobody need be watching (the worker's look does it)."""
        self.assertEqual((E.PAIR_LIMIT, E.PAIR_SECONDS, E.PairGuard.SETTLE), (L.Guard.LIMIT, 20.0, L.Guard.SETTLE))
        now = self.floor_clock()
        self.show()
        self.fx.put("fx-wash.fs")
        gen = self.mpv.loaded[:1]
        self.assertIs(self.heavy_seconds(now, 22), False)                              # 3 seconds to settle and not yet 20 of dropping
        self.assertEqual(self.state()["on"]["load"], "heavy")                          # the card has said so for a while
        self.assertIs(self.heavy_seconds(now, 3), True)
        s = self.state()
        self.assertEqual((s["on"], self.mpv.loaded, self.texts()), (None, gen, []))
        said = "the picture was dropping frames with it on over the shader for 20 seconds, so the box took the effect off to lighten the load. The shader plays on"
        self.assertEqual(s["last"], said)                                              # what is known, and no more: not "it was too heavy"
        self.assertEqual(self.gen.on_screen()["id"], "nxlx-silk.fs")                   # the shader was never touched,
        self.assertEqual(self.settings.data.get("shaders", {}).get("heavy", {}), {})   # and nothing is marked against it
        self.assertEqual(self.player.effect_ended, "heavy")
        # In the second of the shed the generators' guard still holds the pair's window, and says heavy: that is no
        # word about the shader alone, and nothing is added to the sentence from it
        self.assertEqual(self.gen.guard.verdict["state"], "heavy")
        self.assertEqual(self.state()["last"], said)
        self.gen.watch()                                                               # its next look: the effect has left, and it starts over
        self.assertEqual((self.gen.guard.verdict["state"], self.state()["last"]), (None, said))
        # it does not come back by itself
        self.assertIs(self.heavy_seconds(now, 30), True)
        self.assertEqual(self.mpv.loaded, gen)
        # the shader alone, judged by the generators' own guard from then on: while it holds, nothing is added; when
        # it goes on dropping frames without the effect, the card says that too, once
        for _ in range(12):
            now[0] += 1.0
            self.gen.watch()
        self.assertEqual((self.gen.guard.verdict["state"], self.state()["last"]), ("ok", said))
        for _ in range(12):
            now[0] += 1.0
            self.mpv.drops += 5
            self.gen.watch()
        self.assertEqual(self.gen.guard.verdict["state"], "heavy")
        self.assertEqual(self.state()["last"], said + ". Frames are still dropping without it, so the effect may not have been the cause")
        self.assertEqual(self.state()["last"].count("still dropping"), 1)
        # by hand it goes on again, with a count of its own
        self.fx.put("fx-wash.fs")
        self.assertIs(self.heavy_seconds(now, 10), False)
        self.assertIsNone(self.state()["last"])

    def test_no_word_about_a_shader_alone_is_made_from_frames_dropped_under_a_pair(self):
        """The floor takes the effect off; Vibes' guard is no longer suspended; and its window of six seconds was
        full of the pair's dropped frames: in that same second it marked the SHADER heavy, for good, and skipped it.
        The same after a plain Off and after a refusal. The guard starts over whenever an effect leaves a shader."""
        now = self.floor_clock()
        vibes = self.api.vibes
        vibes.start()
        vibes.tick()
        self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0
        marks = lambda: self.settings.data.get("shaders", {}).get("heavy", {})

        def second(rate):
            now[0] += 1.0
            self.mpv.drops += rate
            self.fx.adjust("anchor")
            return vibes._guard()

        def refused():
            with self.fx._lock:
                self.fx._pair_refused(self.fx.on)
        for name, leave in (("the floor", None), ("Off", lambda: self.fx.off()), ("a refusal over the shader", refused),
                            ("a controller's Off", lambda: (self.fx.toggle(), self.pump()))):
            current = vibes.current
            self.fx.put("fx-wash.fs")
            for n in range(40):                                                        # a heavy pair
                self.assertIs(second(5), False, name)
                if self.fx.on is None or (leave and n == 11):
                    break
            if leave:
                self.assertEqual(self.gen.guard.verdict["state"], "heavy", name)       # the pair's window, full
                leave()
            self.assertIsNone(self.fx.on, name)
            self.assertIs(vibes._guard(), False, "%s: the shader was judged in the second the effect left" % name)
            self.assertEqual(self.gen.guard.verdict["state"], None, name)              # a window of its own has begun
            for _ in range(8):                                                         # the shader alone holds: never marked, never skipped
                self.assertIs(second(0), False, name)
            self.assertEqual((marks(), vibes._marked, vibes.current, self.gen.guard.verdict["state"]), ({}, [], current, "ok"), name)
        # and a shader that really is heavy by itself is marked, after a whole window of its own and not sooner
        self.gen.guard.sample(None)
        took = next(n for n in range(1, 20) if second(5))
        self.assertGreaterEqual(took, 9)                                               # three seconds to settle, six to judge
        self.assertEqual(list(marks()), [current])
        # an effect that comes over a shader starts the count over too (what the pair drops is not carried in)
        vibes.skip()
        vibes.tick()
        for _ in range(8):
            second(0)
        self.assertEqual(self.gen.guard.verdict["state"], "ok")
        self.fx.put("fx-wash.fs")
        self.gen.watch()
        self.assertEqual((self.gen.guard.verdict["state"], " under effect " in self.gen.guard._desc), (None, True))

    def test_the_floor_counts_across_the_steps_of_a_rotation_and_leaves_a_light_pair_alone(self):
        now = self.floor_clock()
        vibes = self.api.vibes
        vibes.start()
        vibes.tick()
        self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0
        self.fx.put("fx-wash.fs")
        # a light pair through four steps, each with the hitch of a new shader (twelve frames in its first second)
        for _ in range(4):
            self.assertIs(self.heavy_seconds(now, 1, rate=12), False)
            self.assertIs(self.heavy_seconds(now, 9, rate=0), False)
            vibes.skip()
            vibes.tick()
        self.assertEqual(self.state()["on"]["id"], "fx-wash.fs")
        # a heavy pair under a short dwell: the effect's own guard starts over at each shader and never says
        # heavy (three seconds to settle, six to judge, and the next shader is there), but the floor's count goes on
        self.fx.put("fx-wash.fs")
        said, gone = set(), False
        for _ in range(6):
            for _ in range(5):
                gone = gone or self.heavy_seconds(now, 1)
                said.add((self.fx.guard.verdict or {}).get("state"))
            if gone:
                break
            vibes.skip()
            vibes.tick()
        self.assertTrue(gone, "a heavy pair kept its effect through a rotation of short dwells")
        self.assertNotIn("heavy", said)
        self.assertTrue(vibes.running)
        self.assertEqual((self.kinds(), self.settings.data.get("shaders", {}).get("heavy", {}), vibes._marked), (["shader"], {}, []))
        self.assertIn("was dropping frames with it on over the shader", self.state()["last"])

    def test_the_floor_is_for_a_pair_only_and_goes_with_the_guards_switch(self):
        now = self.floor_clock()
        self.clip()
        self.fx.put("fx-wash.fs")
        self.assertIs(self.heavy_seconds(now, 40), False)                              # over a clip the card says so and a person decides, as before
        self.assertEqual(self.state()["on"]["load"], "heavy")
        self.show()
        cfg = self.gen.config()
        cfg["guard"] = False                                                           # "watch the load" switched off: nothing is taken off
        self.gen._save(cfg)
        self.assertIs(self.heavy_seconds(now, 40), False)
        cfg["guard"] = True
        self.gen._save(cfg)
        self.assertIs(self.heavy_seconds(now, 22), False)                              # and its count began when the switch came back
        self.assertIs(self.heavy_seconds(now, 3), True)

    def test_the_size_is_said_while_the_gpu_still_looks_at_a_generator_that_has_just_come(self):
        """The generators' record is then still the one before (its epoch is old): the size is what the box draws
        generators at. Only with no record at all (black) is no size said."""
        self.show()
        self.fx.put("fx-wash.fs")
        size = self.state()["on"]["working"]["clip"]
        self.assertIsNotNone(size)
        self.fx.off()
        self.player.play_source(self.player.source_shader, self.gen.playing["carrier"])    # the player has the next one; the record follows after the look
        self.assertNotEqual(self.gen.playing["epoch"], self.player.source_epoch)
        self.fx.put("fx-wash.fs")                                                      # an effect put on in that moment
        self.assertEqual(self.state()["on"]["working"]["clip"], size)
        self.fx.adjust("anchor")
        self.assertEqual(self.state()["on"]["working"]["clip"], size)

    def test_a_complaint_with_no_name_is_nobodys_while_both_are_in_the_player(self):
        """A listener that began in the middle of the other shader's text hears numbered lines and a complaint, and
        no name. Alone in the player that is one's own, as before; with both there it is not claimed."""
        tail = [row for row in DUMP if "// %s" not in row[2] and "shader source" not in row[2]]
        self.assertEqual(S.shader_errors(tail), "line 40: `oops' undeclared")
        self.assertEqual(S.about(tail, "nxlx effect 7 3"), tail)
        self.assertEqual(S.about(tail, "nxlx effect 7 3", True), [])
        self.assertEqual(S.about(REFUSAL, "nxlx effect 7 3", True), [])
        named = [(p, level, t % "nxlx effect 7 3" if "%s" in t else t) for p, level, t in DUMP]
        self.assertEqual(S.about(tail + named, "nxlx effect 7 3", True), named)
        # the engines: over a generator an unnamed complaint refuses neither
        self.show()
        FxTap.lines = list(tail)
        self.fx.put("fx-wash.fs")
        self.assertEqual((self.kinds(), self.state()["error"]), (["shader", "effect"], None))
        GenTap.lines = list(tail)
        self.assertTrue(self.gen.show("nxlx-ember.fs")["ok"])
        GenTap.lines = []
        self.fx.off()
        FxTap.lines = []
        self.clip()                                                                    # alone in the player it is heard as before
        FxTap.lines = list(REFUSAL)
        with self.assertRaises(ApiError):
            self.fx.put("fx-vignette.fs")

    def test_a_generator_that_comes_makes_the_effects_worker_look_at_once(self):
        """Not at its next round up to a second away: the GPU's word about the effect over the new picture is
        listened for from the generator's arrival."""
        self.clip()
        self.fx.put("fx-wash.fs")
        self.fx.changer._clock = lambda: 1000.0
        self.fx.changer.keep()
        self.assertEqual(self.fx.changer._refresh, 1000.0 + E.WATCH)
        self.show()
        self.assertEqual(self.fx.changer._refresh, 1000.0)
        self.assertTrue(self.fx.changer.pump())                                        # due now: the look, with a text for the new picture
        self.assertEqual(self.state()["on"]["working"]["under"], "shader")

    def test_values_the_gpu_refused_over_a_generator_are_not_sent_again(self):
        """The memory of refused values has the generator in its key, and the request that notes a change looks under
        the same key as the worker that learned of the refusal."""
        self.api.board = dict(self.api.board, kind="pi4")                              # with a cap in the key too (Automatic)
        self.fx.upload("all.fs", ALL)
        self.show()
        self.fx.put("all.fs")
        self.refuse()
        self.fx.change({"values": {"mode": 2}})
        self.pump()
        self.assertIn("undeclared", self.state()["error"]["message"])
        FxTap.lines = []
        before = len(self.mpv.commands)
        with self.assertRaises(ApiError) as c:
            self.fx.change({"values": {"mode": 2}})
        self.assertEqual(c.exception.status, 422)
        self.assertIn("refused these values before", c.exception.message)
        self.assertEqual(self.lists(before), [])
        self.clip()                                                                    # over a clip they are another text, and are tried
        self.fx.adjust("anchor")
        self.assertTrue(self.fx.change({"values": {"mode": 2}})["ok"])
        self.pump()
        self.assertEqual((self.state()["on"]["values"]["mode"], self.state()["error"]), (2, None))

    def test_a_rotation_and_a_performer_on_the_effect_at_once(self):
        """Two threads, eight rounds each: one shows generator after generator (a rotation's steps), the other puts an
        effect on, changes it, looks and takes it off. The two engines hold different locks and meet in the
        player's. Nothing may hang, nothing may raise, and no list the player was given has the effect before the
        generator. (The order of the locks is checked at every taking by tests/lockrank.py, here as everywhere.)"""
        self.show()
        errors = []

        rounds = 8                                      # each thread does this many and ends: the machine's speed decides nothing

        def rotate():
            for n in range(1, rounds + 1):
                try:
                    if not self.gen.show(("nxlx-silk.fs", "nxlx-ember.fs")[n % 2])["ok"]:
                        errors.append("a generator was refused: %s" % (self.gen.error,))
                except Exception as e:
                    errors.append("show: %r" % (e,))

        def perform():
            for n in range(1, rounds + 1):
                try:
                    self.fx.put(("fx-wash.fs", "fx-vignette.fs")[n % 2])
                    self.fx.change({"controls": {"amount": 0.25 + 0.5 * (n % 2)}})
                    self.fx.changer._last = -1e9
                    self.fx.changer.pump()
                    self.fx.adjust("anchor")
                    self.fx.state()
                    self.gen.state()
                    if n % 3 == 0:
                        self.fx.off()
                except Exception as e:
                    errors.append("effect: %r" % (e,))
        threads = [threading.Thread(target=rotate, daemon=True), threading.Thread(target=perform, daemon=True)]
        before = len(self.mpv.commands)
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual([t.is_alive() for t in threads], [False, False], "a thread never ended (a deadlock)")
        self.assertEqual(errors, [])
        lists = self.lists(before)
        self.assertGreaterEqual(len(lists), 2 * rounds)             # a list for every generator shown and for every effect put on
        self.assertEqual(sorted(set(tuple(x) for x in lists) - {("shader",), ("shader", "effect")}), [], "a list with no generator, or the effect first")
        self.assertIsNotNone(self.gen.on_screen())
        self.assertEqual(self.fx.error, None)

    def test_a_controllers_lights_say_an_effect_can_go_on_over_a_generator(self):
        self.show()
        self.api.registry.set_enabled("control-midi", True)
        self.settings.data["control"]["midi"]["enabled"] = True
        hub = M.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], describer=lambda p: None)
        self.addCleanup(hub.stop)
        snap = hub._snapshot(1.0, fresh=True)
        self.assertEqual((snap["effect"], snap["effect_ready"], snap["running"], snap["shader"]), (None, True, True, "nxlx-silk.fs"))
        self.assertEqual([M.light_state({"action": a}, snap) for a in ("effect_toggle", "effect_next")], ["on", "on"])
        self.fx.put("fx-wash.fs")
        snap = hub._snapshot(2.0, fresh=True)
        self.assertEqual((snap["effect"], M.light_state({"action": "effect_toggle"}, snap)), ("fx-wash.fs", "active"))

    def test_nothing_of_it_is_stored(self):
        """No settings key and no schema change: the pair lives in the player and in memory, as an effect did."""
        before = json.dumps(self.settings.data, sort_keys=True)
        self.show()
        self.fx.put("fx-wash.fs")
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        self.show("nxlx-ember.fs")
        self.fx.off()
        self.assertEqual(json.dumps(self.settings.data, sort_keys=True), before)


class PlayerLayerTest(unittest.TestCase):
    """The player's three shader layers, on the real class."""
    def setUp(self):
        import tempfile
        self.mpv = FakeMpv()
        self.p = Player(rundir=tempfile.mkdtemp())
        self.p.ipc = self.mpv
        self.p.play(["/media/a.mp4"])

    def test_the_order_is_source_effect_mapping_and_each_layer_leaves_the_others(self):
        self.p.set_shaders(["/m.glsl"])
        s = self.p.put_effect("/e1.glsl")
        self.assertEqual((s, self.mpv.props["glsl-shaders"], self.p.effect_shader), (1, ["/e1.glsl", "/m.glsl"], "/e1.glsl"))
        self.assertTrue(self.p.swap_effect("/e2.glsl", s))
        self.assertFalse(self.p.swap_effect("/e3.glsl", s + 1))
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/e2.glsl", "/m.glsl"])
        self.p.set_shaders([])                                                         # the mapper replaces its own layer only
        self.assertEqual((self.mpv.props["glsl-shaders"], self.mpv.props["fbo-format"]), (["/e2.glsl"], "auto"))
        self.p.set_mapping_mode(True)                                                  # the mapping's 8-bit buffers are the mapping's affair
        self.assertEqual(self.mpv.props["fbo-format"], "rgba8")
        self.p.set_mapping_mode(False)
        self.assertIsNone(self.p.put_effect("/e4.glsl", serial=s + 5))                 # an old serial
        self.assertIsNone(self.p.put_effect("/e4.glsl", epoch=self.p.source_epoch - 1))    # something was played since
        self.assertEqual(self.p.put_effect("/e4.glsl", serial=s, epoch=self.p.source_epoch), s + 1)
        self.assertFalse(self.p.clear_effect(s))
        self.assertTrue(self.p.clear_effect(s + 1))
        self.assertEqual((self.mpv.props["glsl-shaders"], self.mpv.props["fbo-format"], self.p.effect_ended, self.p.effect_serial), ([], "auto", "off", s + 2))
        self.assertFalse(self.p.clear_effect())

    def test_a_source_and_an_effect_are_on_together_the_source_first(self):
        """D74. The order of the list is the order the two run in (both hook the same stage), so the source comes
        first: the effect then filters what the source drew."""
        s = self.p.put_effect("/e1.glsl")
        self.p.set_shaders(["/m.glsl"])
        epoch = self.p.play_source("/s1.glsl", "av://lavfi:carrier")
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_ended, self.p.effect_serial), (["/s1.glsl", "/e1.glsl", "/m.glsl"], "", s))
        self.assertEqual(self.p.effect_on(), "/e1.glsl")
        self.assertEqual(self.p.put_effect("/e2.glsl"), s + 1)                         # and one goes on over a source
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/s1.glsl", "/e2.glsl", "/m.glsl"])
        self.p.play_source("/s2.glsl", "av://lavfi:carrier", epoch)                    # the next source (a Vibes step): the effect stays
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_serial), (["/s2.glsl", "/e2.glsl", "/m.glsl"], s + 1))
        self.assertTrue(self.p.swap_source("/s3.glsl", self.p.source_epoch))           # another text of the source: the same
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/s3.glsl", "/e2.glsl", "/m.glsl"])
        self.p.play(["/media/a.mp4"])                                                  # a clip takes the screen: the source goes, the effect stays
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_on()), (["/e2.glsl", "/m.glsl"], "/e2.glsl"))
        self.p.clear()
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_ended), (["/m.glsl"], "stop"))

    def test_a_source_taken_off_the_screen_takes_the_effect_with_it_and_says_so(self):
        epoch = self.p.play_source("/s1.glsl", "av://lavfi:carrier")
        self.p.put_effect("/e1.glsl")
        self.assertFalse(self.p.clear_source(epoch - 1))                               # not this source: nothing happens
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/s1.glsl", "/e1.glsl"])
        self.assertTrue(self.p.clear_source(epoch))                                    # Vibes was stopped, the module went off
        self.assertEqual((self.mpv.props["glsl-shaders"], self.mpv.props["path"], self.p.effect_ended), ([], None, "cleared"))

    def test_the_buffers_format_is_not_set_again_for_an_effect_over_a_source(self):
        """Setting it makes the player set its renderer up anew (a hitch). Under a source the buffers are 8-bit
        already, so an effect that comes or goes there leaves the setting alone; when the source goes, the effect
        keeps them 8-bit on the boards where it wants them (effect_8bit) and gives them back elsewhere."""
        sets = lambda: [c[2] for c in self.mpv.commands if c[:2] == ("set_property", "fbo-format")]
        for eight in (True, False):
            self.p.effect_8bit = eight
            self.p.play_source("/s1.glsl", "av://lavfi:carrier")
            before = sets()
            self.assertEqual(self.mpv.props["fbo-format"], "rgba8")
            s = self.p.put_effect("/e1.glsl")
            self.p.swap_effect("/e2.glsl", s)
            self.p.clear_effect(s)
            self.p.put_effect("/e3.glsl")
            self.assertEqual(sets(), before, eight)
            self.p.play(["/media/a.mp4"])                                              # the source goes, the effect stays
            self.assertEqual(self.mpv.props["fbo-format"], "rgba8" if eight else "auto", eight)
            self.p.clear()
            self.assertEqual(self.mpv.props["fbo-format"], "auto", eight)

    def test_a_restarted_mpv_has_lost_the_effect_and_is_not_given_it_again(self):
        s = self.p.put_effect("/e1.glsl")
        self.mpv.restart()
        self.assertIsNone(self.p.effect_on())
        self.assertEqual((self.p.effect_ended, self.p.effect_serial), ("restart", s + 1))
        self.p.put_effect("/e1.glsl")
        self.mpv.restart()
        self.p.set_shaders(["/m.glsl"])
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/m.glsl"])
        self.p.put_effect("/e1.glsl")
        self.mpv.down = True
        self.assertIsNone(self.p.effect_on())
        self.assertFalse(self.p.clear_effect())
        with self.assertRaises(PlayerError):                                           # Stop with the player down says so, as before,
            self.p.clear()
        self.assertEqual((self.p.effect_shader, self.p.effect_ended), (None, "restart"))    # and the effect is forgotten all the same
        self.mpv.down = False


class RolesTest(Base):
    def test_who_may_do_what(self):
        full, _ = self.pair()
        view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/effects")[0], 401)
        for token in (view, live, full):
            st, body, _ = self.call("GET", "/api/effects", token=token)
            self.assertEqual((st, body["enabled"], len(body["effects"]) >= 29), (200, True, True))
        upload = {"action": "upload", "name": "mine.fs", "source": GOOD}
        posts = (("/api/effects", {"id": "fx-wash.fs"}), ("/api/effects/values", {"controls": {"amount": 0.5}}), ("/api/effects/step", {"dir": 1}),
                 ("/api/effects/preset", {"index": 1}), ("/api/effects/presets", {"action": "save", "name": "x"}), ("/api/effects/library", upload),
                 ("/api/effects/config", {"detail": 540}))
        for path, body in posts:
            self.assertEqual(self.call("POST", path, body)[0], 401, path)
            self.assertEqual(self.call("POST", path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=full, csrf=False)[0], 403, path)          # no cross-site requests
        # a presenter puts on, changes, steps and takes off; saving a preset and adding a file are the owner's
        st, body, _ = self.call("POST", "/api/effects", {"id": "fx-wash.fs", "values": {"strength": 0.3}, "controls": {"amount": 0.5}}, token=live)
        self.assertEqual((st, body["on"]["id"], body["on"]["controls"]["amount"]), (200, "fx-wash.fs", 0.5))
        self.assertEqual(self.call("POST", "/api/effects/values", {"controls": {"amount": 0.2}}, token=live)[1]["controls"]["amount"], 0.2)
        self.assertEqual(self.call("POST", "/api/effects/step", {"dir": 1}, token=live)[0], 200)
        self.pump()                                                                    # the worker carries the step out (a preset asked for while
        #                                                                                one waits is meant for the effect that is coming)
        self.assertEqual(self.call("POST", "/api/effects/presets", {"action": "save", "name": "x"}, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/effects/library", upload, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/effects/presets", {"action": "save", "name": "x"}, token=full)[0], 200)
        self.assertEqual(self.call("POST", "/api/effects/preset", {"name": "x"}, token=live)[1]["preset"], "x")
        st, body, _ = self.call("POST", "/api/effects", {"off": True}, token=live)
        self.assertEqual((st, body["on"]), (200, None))
        self.assertEqual(self.call("POST", "/api/effects", {"toggle": True}, token=live)[1]["on"], True)
        st, body, _ = self.call("POST", "/api/effects/library", upload, token=full)
        self.assertEqual((st, [s["id"] for s in body["effects"] if s["source"] == "uploaded"]), (200, ["mine.fs"]))
        st, body, _ = self.call("POST", "/api/effects/library", dict(upload, name="gen.fs", source="/*{}*/\nvoid main() { gl_FragColor = vec4(1.0); }\n"), token=full)
        self.assertEqual((st, "added under Shaders" in body["error"]), (422, True))
        self.assertEqual(self.call("POST", "/api/effects/library", {"action": "delete", "id": "mine.fs"}, token=full)[0], 200)
        for body, status in (({"id": "../../etc/passwd"}, 400), ({"id": "nope.fs"}, 404), ({"id": "fx-wash.fs", "values": {"strength": "a lot"}}, 422), ({}, 400)):
            self.assertEqual(self.call("POST", "/api/effects", body, token=live)[0], status, body)

    def test_with_nothing_playing_a_panel_is_told_so_and_a_controller_still_asks_the_player_nothing(self):
        """Next, Previous, the one button and a preset of another effect answered "ok" with nothing playing and then
        came to nothing. That was the price of a controller's calls never asking the player. A panel's request may
        ask: it gets the 409 and the reason that putting an effect on always gave. A controller's does not."""
        full, _ = self.pair()
        self.fx.put("fx-wash.fs")
        self.fx.preset_save("One")
        self.fx.off()
        self.player.clear()
        wishes = (("/api/effects/step", {"dir": 1}), ("/api/effects/step", {"dir": -1}), ("/api/effects", {"toggle": True}),
                  ("/api/effects/preset", {"id": "fx-wash.fs", "name": "One"}))
        for path, body in wishes:
            st, answer, _ = self.call("POST", path, body, token=full)
            self.assertEqual((st, E.NO_PICTURE in answer.get("error", "")), (409, True), (path, answer))
            self.assertFalse(self.fx.changer.pump(), path)                             # and nothing was noted for the worker
        self.assertEqual((self.fx.error, self.mpv.loaded), (None, []))
        for device in (M.MIDI_DEVICE, {"id": "osc", "name": "OSC", "role": "live"}, {"id": "dmx", "name": "DMX", "role": "live"},
                       {"id": "room", "name": "Room", "role": "live"}):
            for path, body in wishes:
                asked = len(self.mpv.commands)
                self.assertEqual(self.api.handle("POST", path, body, device, "t")[0], 200, (device["id"], path))
                self.assertEqual(self.mpv.commands[asked:], [], (device["id"], path))  # answered from memory, as before
            self.fx.off()
        self.assertEqual(self.api.handle("POST", "/api/effects/step", {"dir": 1}, M.MIDI_DEVICE, "midi")[0], 200)
        self.pump()                                                                    # the worker finds out and says why
        self.assertIn(E.NO_PICTURE, self.state()["error"]["message"])
        self.assertEqual(self.mpv.loaded, [])
        # with a picture a panel's wishes are noted as they were, and Off from the one button never asks
        self.player.play(["/media/a.mp4"])
        st, answer, _ = self.call("POST", "/api/effects/step", {"dir": 1}, token=full)
        self.assertEqual((st, answer["ok"]), (200, True))
        self.pump()
        self.assertEqual(self.state()["on"]["id"], answer["id"])
        self.player.clear()                                                            # Stop takes it off in the player; this
        self.fx._intent = True                                                         # process has not looked yet
        asked = len(self.mpv.commands)
        self.assertEqual(self.call("POST", "/api/effects", {"toggle": True}, token=full)[1], {"ok": True, "on": False})
        self.assertEqual([c for c in self.mpv.commands[asked:] if c[:2] == ("get_property", "video-params")], [])

    def test_the_module_is_refused_while_off(self):
        full, _ = self.pair()
        self.api.registry.set_enabled("shaders", False)
        for path, body in (("/api/effects", {"id": "fx-wash.fs"}), ("/api/effects", {"off": True}), ("/api/effects/values", {"controls": {"amount": 1}}),
                           ("/api/effects/step", {"dir": 1}), ("/api/effects/preset", {"index": 1}), ("/api/effects/presets", {"action": "save", "name": "x"}),
                           ("/api/effects/library", {"action": "delete", "id": "x.fs"}), ("/api/effects/config", {"detail": 540})):
            self.assertEqual(self.call("POST", path, body, token=full)[0], 409, path)
        self.assertEqual(self.call("GET", "/api/effects", token=full)[1]["effects"], [])


class MidiTest(Base):
    """The effect actions of a MIDI controller: what each sends, through the same call as the panel, as a presenter."""
    def mapper(self, *entries, **have):
        self.calls, self.t = [], [100.0]
        m = M.MidiMapper(lambda path, body: self.calls.append((path, body)) or True, [M.validate_entry(dict(e, source="*")) for e in entries],
                         {"blackout": False}, clock=lambda: self.t[0])
        m.target = lambda action: have.get(action)
        return m

    def send(self, m, kind, number, value):
        self.t[0] += 1.0
        m.message("any", (kind, 0, number, value))

    def test_the_actions_are_in_the_list_and_send_what_the_panel_sends(self):
        self.assertEqual(M.ACTIONS["effect_amount"], ("level", 0.0, 1.0))
        self.assertEqual([M.ACTIONS[a][0] for a in ("effect_toggle", "effect_prev", "effect_next")], ["trigger"] * 3)
        self.assertEqual([M.ACTIONS["effect_control_%d" % n] for n in (1, 8)], [("control", 1, None), ("control", 8, None)])
        self.assertNotIn("effect_control_9", M.ACTIONS)
        self.assertIn("effect_amount", M.PICKUP)
        m = self.mapper({"kind": "cc", "number": 1, "action": "effect_amount"}, {"kind": "cc", "number": 2, "action": "effect_control_3"},
                        {"kind": "note", "number": 3, "action": "effect_control_2"}, {"kind": "note", "number": 4, "action": "effect_toggle"},
                        {"kind": "note", "number": 5, "action": "effect_prev"}, {"kind": "note", "number": 6, "action": "effect_next"})
        self.send(m, "cc", 1, 127)
        self.send(m, "cc", 1, 64)
        self.send(m, "cc", 2, 100)
        for note in (3, 4, 5, 6):
            self.send(m, "on", note, 127)
            self.send(m, "off", note, 0)
        self.assertEqual(self.calls, [("/api/effects/values", {"controls": {"amount": 1.0}}), ("/api/effects/values", {"controls": {"amount": 0.504}}),
                                      ("/api/effects/values", {"control": 3, "level": 100}), ("/api/effects/values", {"control": 2, "press": True}),
                                      ("/api/effects", {"toggle": True}), ("/api/effects/step", {"dir": -1}), ("/api/effects/step", {"dir": 1})])
        with self.assertRaises(M.MidiError):                                           # a fader's action is not for a program change
            M.validate_entry({"kind": "program", "number": 1, "action": "effect_amount"})

    def test_a_controller_plays_an_effect_as_a_presenter_and_only_with_the_module_on(self):
        hub = M.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [])
        self.addCleanup(hub.stop)
        hub.calls = M.RateLimiter(time.monotonic, rate=1e9, burst=1e9)
        self.fx.upload("all.fs", ALL)
        self.assertTrue(hub._do("/api/effects", {"toggle": True}))                    # on: the first of the library, through the worker
        self.pump()
        self.assertEqual(self.state()["on"]["id"], "fx-edge-glow.fs")
        self.fx.put("all.fs")
        self.assertTrue(hub._do("/api/effects/values", {"controls": {"amount": 0.25}}))
        self.assertTrue(hub._do("/api/effects/values", {"control": 1, "level": 127}))
        self.pump()
        self.assertEqual((self.state()["on"]["controls"]["amount"], self.state()["on"]["values"]["k"]), (0.25, 2.0))
        self.assertEqual(hub._target("effect_amount"), 0.25)                           # what a fader has to reach before it takes over
        self.assertTrue(hub._do("/api/effects/step", {"dir": 1}))
        self.assertTrue(hub._do("/api/effects", {"toggle": True}))                    # off: it was on, and another was on its way
        self.pump()
        self.assertEqual((self.mpv.loaded, hub._target("effect_amount")), ([], None))
        # a controller is a presenter: it saves no preset and adds no file
        self.assertEqual(self.api.handle("POST", "/api/effects/presets", {"action": "save", "name": "x"}, M.MIDI_DEVICE, "midi")[0], 403)
        self.assertEqual(self.api.handle("POST", "/api/effects/library", {"action": "upload", "name": "x.fs", "source": GOOD}, M.MIDI_DEVICE, "midi")[0], 403)
        self.api.registry.set_enabled("shaders", False)
        before = len(self.mpv.commands)
        self.assertFalse(hub._do("/api/effects", {"toggle": True}))
        self.assertFalse(hub._do("/api/effects/values", {"controls": {"amount": 1.0}}))
        self.assertEqual(len(self.mpv.commands), before)                               # the player was not asked anything

    def test_the_three_shipped_layouts_have_effect_controls_on_what_was_spare(self):
        """Where the effect actions sit since the layouts were drawn afresh in zones (D75): the amount on fader 7 of
        the two fader boxes, the effect's inputs on the MIDI Mix's bottom row of knobs, and one button (or one row of
        three on the Launchpad) for on/off and the steps."""
        want = {"korg-nanokontrol2": {"fader7": "effect_amount", "r5": "effect_toggle"},
                "akai-midimix": dict({"knob_c%d" % n: "effect_control_%d" % n for n in range(1, 9)}, fader7="effect_amount", solo4="effect_toggle"),
                "novation-launchpad-mini": {"pad75": "effect_toggle", "pad76": "effect_prev", "pad77": "effect_next"}}
        profiles = M.load_profiles(log=lambda *_: None)
        self.assertEqual(sorted(p["id"] for p in profiles), sorted(want))
        for p in profiles:
            got = {c["id"]: c["action"]["action"] for c in p["controls"] if c["action"] and c["action"]["action"].startswith("effect_")}
            self.assertEqual(got, want[p["id"]], p["id"])
            for c in p["controls"]:                                                    # a fader or knob follows, a button or pad presses
                if c["id"] in got:
                    kind = M.ACTIONS[got[c["id"]]][0]
                    self.assertTrue(kind != "trigger" if c["kind"] in ("fader", "knob") else kind != "level", (p["id"], c["id"]))
            if p["id"] == "novation-launchpad-mini":                                   # the third round button along the top shows a pairing code (D61, moved off pad 6.8)
                self.assertEqual([c["id"] for c in p["controls"] if c["action"] and c["action"]["action"] == "code_join"], ["top3"])
            else:
                self.assertTrue(any(c["action"] is None for c in p["controls"]), p["id"])  # and something is still spare


class PackTest(unittest.TestCase):
    def test_the_pack_is_upstream_byte_for_byte(self):
        with open(os.path.join(PACK_DIR, "SHA256SUMS")) as f:
            sums = dict(reversed(line.rstrip("\n").split("  ", 1)) for line in f)
        files = sorted(n for n in os.listdir(PACK_DIR) if n.endswith(".fs"))
        self.assertEqual(sorted(sums), files)
        for n in files:
            with open(os.path.join(PACK_DIR, n), "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), sums[n], n + " differs from upstream")
            self.assertTrue(re.fullmatch(r"isf-[a-z0-9-]+\.fs", n), n)
            p = S.parse(data, S.FILTER)
            self.assertTrue(p["credit"], n)
            # nothing in the pack credits a source outside its own repository, or holds the snippets that are passed around
            text = data.decode("utf-8")
            for word in ("http", "shadertoy", "adapted", "Adapted", "ported", "rgb2hsv", "hsv2rgb", "43758.5453", "12.9898"):
                self.assertNotIn(word, text, "%s holds %r" % (n, word))


if __name__ == "__main__":
    unittest.main()
