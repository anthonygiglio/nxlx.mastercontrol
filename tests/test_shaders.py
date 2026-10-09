# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shaders and Vibes: the ISF translator (friendly and hostile files), the engine against a fake player, the Vibes
rotation on a fake clock, and who may do what over HTTP. The real mpv and a real GPU are in test_shaders_gpu.py."""
import json
import os
import random
import re
import threading
import time
import unittest

from pvj import autostart, osc, scheduler, shaders as S, vibes as V
from pvj.api import ApiError
from pvj.settings import SCHEMA
from tests.test_server import ServerBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ISF_PACK = os.path.join(S.BUNDLED_DIR, "isf-files")
# The third-party pack (Vidvox ISF-Files, MIT): in the library, never in the Vibes rotation by itself.
# Named one by one (tests/test_license.py pins the same list and the checksums): the pack changes by decision only.
PACKED = ["isf-color-bars.fs", "isf-corner-colors.fs", "isf-linear-gradient.fs", "isf-radial-gradient.fs", "isf-ridgelines.fs",
          "isf-simplex-noise.fs", "isf-sine-warp-gradient.fs"]
HEAD ={"ISFVSN": "2", "DESCRIPTION": "test", "CREDIT": "tests", "INPUTS": [
    {"NAME": "speed", "TYPE": "float", "MIN": 0.5, "MAX": 2.0, "DEFAULT": 1.0, "LABEL": "Speed"},
    {"NAME": "flip", "TYPE": "bool", "DEFAULT": True},
    {"NAME": "mode", "TYPE": "long", "VALUES": [0, 2, 5], "LABELS": ["a", "b", "c"], "DEFAULT": 2},
    {"NAME": "tint", "TYPE": "color", "DEFAULT": [1.0, 0.5, 0.25, 1.0]},
    {"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.25, 0.75]},
    {"NAME": "bang", "TYPE": "event"}]}
BODY = """
void main() {
    vec2 uv = isf_FragNormCoord + spot + gl_FragCoord.xy / RENDERSIZE;
    gl_FragColor = vec4(tint.rgb * (0.5 + 0.5 * sin(TIME * speed + uv.x + float(mode))), flip ? 1.0 : 0.5);
}
"""


# The bundled set, named one by one: a file that is added or goes missing fails the tests until it is named here.
# The first ten and the ambient family are in the Vibes rotation from the start; the performance family is not.
FIRST_TEN = ["aurora", "drift", "ember", "horizon", "lattice", "nebula", "prism", "pulse", "silk", "tide"]
AMBIENT = ["bloom", "caustic", "contour", "dusk", "fringe", "kaleido", "lantern", "moire", "petal", "pool", "ribbon", "ridge", "stars", "tiles", "veil"]
PERFORMANCE = ["bars", "beam", "burst", "checker", "chevron", "glitch", "grid", "halftone", "mirror", "radar", "scope", "spokes", "stripes", "tunnel", "zoom"]
BUNDLED = len(FIRST_TEN) + len(AMBIENT) + len(PERFORMANCE)
# The classes a Raspberry Pi 4 measured (PI4 in pvj/shaderlive.py), named one by one: a light shader holds 30 frames a
# second at 720 lines, a medium one at 540 lines only, a heavy one drops frames at 540 lines too. All that is not
# named here is light.
# measured classes: begin
MEDIUM = ["aurora", "beam", "bloom", "lantern", "petal", "pool", "ridge", "scope", "stars", "tide"]
HEAVY = ["drift", "nebula"]                   # out of the rotation until someone puts them in
PACK_MEDIUM = ["isf-simplex-noise"]
PACK_HEAVY = ["isf-ridgelines"]
# measured classes: end
ROTATION = [n for n in FIRST_TEN + AMBIENT if n not in HEAVY]
IN_VIBES = len(ROTATION)
# Numbers from the hash and noise one-liners that are passed around everywhere: the bundled shaders build their own.
WELL_KNOWN = ("43758.5453", "12.9898", "78.233", "0.1031", ".1030", "437.585", "289.0", "6.2831 * (", "0.5 + 0.5 * cos(6.28318 * (")


def isf(head=None, body=BODY, **change):
    h = dict(HEAD if head is None else head, **change)
    return "/*" + json.dumps(h, indent=1) + "*/\n// a comment\n" + body


def refusal(test, text):
    with test.assertRaises(S.ShaderError) as c:
        S.translate(S.parse(text), (1280, 720))
    return str(c.exception)


class TranslatorTest(unittest.TestCase):
    def test_a_generator_becomes_an_mpv_hook_with_its_inputs_as_constants(self):
        p = S.parse(isf())
        self.assertEqual([i["type"] for i in p["inputs"]], ["float", "bool", "long", "color", "point2D", "event"])
        t = S.translate(p, (1280, 720), desc="nxlx shader 7")
        self.assertIn("\n// nxlx shader 7\n", t)                    # the name is in the code too, so no two texts are alike
        head = t.split("\n\n")[0].splitlines()
        self.assertEqual(head[1:], ["//!HOOK NATIVE", "//!BIND HOOKED", "//!WIDTH 1280", "//!HEIGHT 720", "//!DESC nxlx shader 7"])
        self.assertEqual(t.count("//!"), 5)                          # only the commands written here
        for line in ("const float speed = 1.0;", "const bool flip = true;", "const int mode = 2;",
                     "const vec4 tint = vec4(1.0, 0.5, 0.25, 1.0);", "const vec2 spot = vec2(0.25, 0.75);", "const bool bang = false;",
                     "#define RENDERSIZE vec2(1280.0, 720.0)", "#define TIME pvj_time", "precision highp float;",
                     "    pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / 30.0 + 0.0;",
                     "#define isf_FragNormCoord pvj_norm", "void pvj_main()", "    pvj_main();"):
            self.assertIn(line, t)
        self.assertEqual(len(re.findall(r"\bvec4 hook\(\)", t)), 1)
        for gone in ("gl_FragColor", "gl_FragCoord", "void main"):
            self.assertNotIn(gone, t)
        self.assertIn("pvj_coord.xy / RENDERSIZE", t)
        self.assertNotIn("mat3(", t)                                 # no palette shift asked for

    def test_compile_errors_point_at_the_lines_of_the_isf_file(self):
        text = isf()
        p = S.parse(text)
        self.assertEqual(text.splitlines()[p["line"] - 1], "}*/")           # the code starts on the line the header ends on
        self.assertEqual(text.splitlines()[p["line"]], "// a comment")
        # "#line n" names the next line n on OpenGL ES and from GLSL 3.30 on, and n + 1 before that (the
        # specifications of 1.40 and 3.30, section 3.3; GLSL 1.40 is a Raspberry Pi 4's): the number is picked by
        # the language, and the file's code follows it at once
        out = S.translate(p, (640, 360))
        self.assertIn("#if defined(GL_ES) || __VERSION__ >= 330\n#define PVJ_LINE %d\n#else\n#define PVJ_LINE %d\n#endif\n#line PVJ_LINE\n%s"
                      % (p["line"], p["line"] - 1, p["code"]), out)
        self.assertEqual(out.count("#line"), 1)

    def test_slider_values_are_kept_inside_min_and_max_and_only_known_numbers_are_taken(self):
        p = S.parse(isf())
        self.assertIn("const float speed = 2.0;", S.translate(p, (640, 360), {"speed": 99}))
        self.assertIn("const float speed = 0.5;", S.translate(p, (640, 360), {"speed": -1e5}))
        self.assertIn("const float speed = 1.25;", S.translate(p, (640, 360), {"speed": 1.25}))
        for bad in ({"nope": 1}, {"flip": 1}, {"speed": "1"}, {"speed": float("nan")}, {"speed": float("inf")}, {"speed": True}, [1]):
            with self.assertRaises(S.ShaderError):
                S.translate(p, (640, 360), bad)

    def test_palette_shift_and_time_offset_are_numbers_only(self):
        t = S.translate(S.parse(isf()), (640, 360), hue=90, offset=12.5)
        self.assertIn("/ 30.0 + 12.5;", t)
        self.assertRegex(t, r"c = clamp\(mat3\((-?[0-9.e-]+, ){8}-?[0-9.e-]+\) \* c, 0\.0, 1\.0\);")
        for bad in ({"hue": float("nan")}, {"hue": "9"}, {"offset": float("inf")}, {"desc": "x\n//!HOOK OUTPUT"}, {"desc": "A"}):
            with self.assertRaises(S.ShaderError):
                S.translate(S.parse(isf()), (640, 360), **bad)
        with self.assertRaises(S.ShaderError):
            S.translate(S.parse(isf()), (100000, 360))

    def test_hue_matrix_keeps_greys_and_turns_red_to_green(self):
        self.assertEqual([round(x, 6) for x in S.hue_matrix(0)], [1, 0, 0, 0, 1, 0, 0, 0, 1])
        for deg in (37, 120, -90):
            m = S.hue_matrix(deg)
            for row in range(3):
                self.assertAlmostEqual(sum(m[row * 3:row * 3 + 3]), 1.0)          # grey stays grey
        m = S.hue_matrix(120)
        self.assertEqual([round(m[r * 3], 6) for r in range(3)], [0, 1, 0])      # red lands on green

    def test_what_is_not_supported_is_refused_with_the_reason(self):
        cases = [
            (isf(PASSES=[{"TARGET": "a"}, {}]), "several passes"),
            (isf(PASSES=[{"TARGET": "buf", "PERSISTENT": True}]), "persistent buffer"),
            (isf(PASSES=[{"WIDTH": "$WIDTH/2"}]), "own drawing size"),
            (isf(PERSISTENT_BUFFERS=["a"]), "persistent buffer"),
            (isf(IMPORTED={"pic": {"PATH": "/etc/passwd"}}), "IMPORTED"),
            (isf(INPUTS=[{"NAME": "inputImage", "TYPE": "image"}]), "only generator shaders"),
            (isf(INPUTS=[{"NAME": "snd", "TYPE": "audio"}]), "sound"),
            (isf(INPUTS=[{"NAME": "fft", "TYPE": "audioFFT"}]), "sound"),
            (isf(INPUTS=[{"NAME": "c", "TYPE": "cube"}]), "only generator shaders"),
            (isf(INPUTS=[{"NAME": "x", "TYPE": "matrix"}]), "not supported"),
            (isf(INPUTS=[], body="void main() { gl_FragColor = IMG_NORM_PIXEL(a, isf_FragNormCoord); }"), "reads a picture"),
            (isf(INPUTS=[], body="void main() { gl_FragColor = IMG_THIS_PIXEL(a); }"), "reads a picture"),
        ]
        for text, reason in cases:
            self.assertIn(reason, refusal(self, text), text[:120])
        self.assertTrue(S.parse(isf(PASSES=[{}])))                   # one plain pass is the same as none

    def test_hostile_text_cannot_reach_the_player_as_a_command(self):
        """mpv reads //! lines as its own commands wherever they stand, even inside a comment: a file could add a
        hook at the OUTPUT stage (over the mapping), a texture, or bind another shader's picture."""
        ok = "void main() { gl_FragColor = vec4(1.0); }"
        for text in (isf(body=ok + "\n//!HOOK OUTPUT\n//!BIND HOOKED\nvec4 hook() { return vec4(1.0); }"),
                     isf(body="/* //!TEXTURE X */\n" + ok), isf(body=ok + " //!SAVE MAIN"), isf(DESCRIPTION="x //!HOOK OUTPUT"),
                     isf(body="/* a comment\n//!HOOK OUTPUT\n*/" + ok),
                     "//!HOOK OUTPUT\n" + isf()):
            self.assertIn("//!", refusal(self, text))
        for directive in ("#include \"/etc/passwd\"", "#version 100", "#extension GL_OES_standard_derivatives : enable", "#pragma optimize(off)",
                          "  #  include <x>", "#line 1", "#import x", "#error x", "#"):
            self.assertIn("not allowed", refusal(self, isf(body=directive + "\n" + ok)), directive)
        self.assertTrue(S.parse(isf(body="#define TWO 2.0\n#ifdef GL_ES\n#endif\n" + ok)))
        for decl in ("uniform sampler2D secret;", "varying vec2 v;", "in vec2 p;", "out vec4 o;", "layout(location = 0) out vec4 o;", "attribute vec2 a;"):
            self.assertIn("uniform, varying, in or out", refusal(self, isf(body=decl + "\n" + ok)), decl)
        # (`out_color`, spelled exactly so, is no longer refused but renamed: see the test of the two renamed names)
        for name in ("pvj_color", "HOOKED_tex", "pvj_main", "PVJ_HP", "Hooked_pos", "texture0", "texcoord0", "Out_Color", "OUT_COLOR",
                     "pvj_u_out_color", "pvj_in_color", "input_size"):
            self.assertIn("used by the player", refusal(self, isf(body="float %s = 1.0;\n%s" % (name, ok))))
        # behind a comment, and in the middle of a line: the compiler sees these, so the checks must too
        for hidden in ("/**/#extension GL_OES_standard_derivatives : enable", "/* */ #pragma optimize(off)", "/* a\nb */#version 100",
                       "/**/ # include <x>"):
            self.assertIn("not allowed", refusal(self, isf(body=hidden + "\n" + ok)), hidden)
        for decl in ("float q; uniform sampler2D tex;", "float q;\n /* x */ uniform float u;", "float f() { return 1.0; } in vec2 p;",
                     "float q; layout(location = 1) out vec4 o;"):
            self.assertIn("uniform, varying, in or out", refusal(self, isf(body=decl + "\n" + ok)), decl)
        self.assertTrue(S.parse(isf(body="void twice(in float a, out float b, inout float c) { b = a; }\n" + ok)))   # parameters are fine
        self.assertIn("used by the player", refusal(self, isf(body="void main() { gl_FragColor = texture2D(texture0, vec2(0.5)); }")))
        for line in ("#undef PVJ_HP", "#define hook x", "#define HOOK x", "#define TIME 0.0", "#undef RENDERSIZE", "#define gl_Position x",
                     "#define frame 0", "#define a__b 1"):
            self.assertIn("is not allowed", refusal(self, isf(body=line + "\n" + ok)), line)
        self.assertIn("not allowed", refusal(self, isf(body="#define TWO \\\n 2.0\n" + ok)))       # no line continuations
        # `hook` is the function the player calls: no file may define or name it (it was refused only as an input or
        # a #define, and a file with its own hook() then failed in the GPU compiler instead of here)
        for line in ("vec4 hook() { return vec4(1.0); }", "float hook = 1.0;", "vec4 Hook(void) { return vec4(0.0); }", "float x = HOOK;"):
            self.assertIn("used by the player", refusal(self, isf(body=line + "\n" + ok)), line)
        self.assertTrue(S.parse(isf(body="float hooky = 1.0; float unhook = 2.0;\n" + ok)))        # only the word itself
        self.assertIn("never closed", refusal(self, isf(body=ok + "\n/* open")))
        self.assertNotIn("secret", S.translate(S.parse(isf(body=ok + " // secret\n/* secret */")), (640, 360)))
        # (text that is not ASCII is allowed inside a comment now, since comments are never passed on: see the test of
        # comments below; in the code itself it is refused as before)
        for text in (isf(body=ok + "\nfloat café = 1.0;"), isf(body=ok + "\x00"), isf(body=ok + "\x1b[2J"), isf(body=ok + "\x0c"),
                     isf(body="float a\u2028= 1.0;\n" + ok), isf(body="/* x */\u00a0" + ok)):
            self.assertIn("ASCII", refusal(self, text))

    def test_a_comment_may_hold_any_text_because_no_comment_reaches_the_player(self):
        """Real ISF files have dashes, arrows and bullets in their comments (7 generators of Vidvox's ISF-Files were
        refused for that alone). The comments are cut out before anything else is looked at, so what the checks read
        and what the player gets is the same text, and it is plain ASCII."""
        ok = "void main() { gl_FragColor = vec4(1.0); }"
        text = isf(body="// Spectrum \u2013 hue shifts \u2192 café \u2022\n/* \u2014 secret\u2028line */\n" + ok + " // é\x0b\x0c")
        out = S.translate(S.parse(text), (640, 360))
        self.assertTrue(out.isascii())
        self.assertFalse(any(word in out for word in ("Spectrum", "secret", "line */")))
        self.assertTrue(all(" " <= ch <= "~" or ch == "\n" for ch in out), "only printable ASCII and line breaks reach the player")
        # a backslash too, inside a comment (ASCII art, a Windows path): it was refused there although the comment
        # never reaches the player; in the code it is refused as before, wherever it stands
        kept = S.translate(S.parse(isf(body="// a path C:\\shaders\\x and a slope /\\\n/* \\ */ " + ok + " // end \\")), (640, 360))
        self.assertNotIn("\\", kept)
        for body in ("#define TWO \\\n 2.0\n" + ok, "float a = 1.0; \\\n" + ok, "/* x */ \\\n" + ok, ok + "\\"):
            self.assertIn("line continuations", refusal(self, isf(body=body)), body)
        # a // comment that ends in a backslash does not swallow the next line here: that line is read as code
        self.assertIn("uniform, varying, in or out", refusal(self, isf(body="// x \\\nuniform float u;\n" + ok)))
        self.assertIn("gl_FragColor".replace("gl_FragColor", "pvj_color"), S.translate(S.parse(isf(body="// x \\\n" + ok)), (640, 360)))
        # a comment cannot be used to carry something past the checks: what follows it is still read as code
        for body, reason in (("// \u2013\nuniform float u;\n" + ok, "uniform, varying, in or out"),
                             ("/* \u2013 */ #pragma optimize(off)\n" + ok, "not allowed"),
                             ("// \u2028\n#include <x>\n" + ok, "not allowed"),
                             ("/* \u2013 */ float HOOKED_x;\n" + ok, "used by the player"),
                             ("/* \u2013\n//!HOOK OUTPUT\n*/" + ok, "//!")):
            self.assertIn(reason, refusal(self, isf(body=body)), body)

    def test_two_names_the_player_owns_are_renamed_and_never_reach_it(self):
        """`out_color` (9 generators of ISF-Files) and an input called `color` (3) were refused. Both are now written
        under a pvj_ name, which a file cannot spell itself; the player's own words stay out of the generated text."""
        body = ("vec4 tint(vec4 c) { return c * color; }\n"
                "void main() {\n    vec4 out_color = tint(vec4(isf_FragNormCoord, 0.5, 1.0));\n    gl_FragColor = out_color + Color;\n}\n")
        p = S.parse(isf(INPUTS=[{"NAME": "color", "TYPE": "color", "DEFAULT": [1, 0, 0, 1]}, {"NAME": "Color", "TYPE": "float"}], body=body))
        self.assertEqual([i["name"] for i in p["inputs"]], ["color", "Color"])     # the panel and the API keep the file's names
        out = S.translate(p, (640, 360), {"Color": 0.25})
        code = out.split("#line", 1)[1]
        self.assertNotRegex(code, r"\bout_color\b")
        self.assertNotRegex(code, r"(?i)\bcolor\b")
        self.assertIn("const vec4 pvj_in_color = vec4(1.0, 0.0, 0.0, 1.0);", out)
        self.assertIn("const float pvj_in_Color = 0.25;", out)
        self.assertIn("vec4 pvj_u_out_color = tint(", out)
        self.assertIn("return c * pvj_in_color;", out)
        self.assertEqual(S.clean_values(p, {"Color": 5}), {"Color": 1.0})
        # what stays refused: every other spelling, the names the renaming uses, and a #define of either word
        ok = "void main() { gl_FragColor = vec4(1.0); }"
        for line in ("float OUT_COLOR;", "float Out_color;", "float pvj_u_out_color;", "float pvj_in_color;"):
            self.assertIn("used by the player", refusal(self, isf(body=line + "\n" + ok)), line)
        for line in ("#define out_color gl_FragColor", "#define color vec4(1.0)", "#undef out_color"):
            self.assertIn("is not allowed", refusal(self, isf(body=line + "\n" + ok)), line)
        for name in ("out_color", "Out_Color", "colour__x", "hook", "frame"):
            with self.assertRaises(S.ShaderError, msg=name):
                S.parse(isf(INPUTS=[{"NAME": name, "TYPE": "float"}]))
        # no joining of names: a ## could build `out_color` or `HOOKED_raw` from pieces the checks never saw whole
        for line in ("#define JOIN(a, b) a##b", "#define J(a, b) a ## b", "float x = 1.0; /* */ ## y"):
            self.assertIn("##", refusal(self, isf(body=line + "\n" + ok)), line)
        self.assertTrue(S.parse(isf(body="// a ## in a comment is no code\n" + ok)))
        self.assertIn("exactly one void main", refusal(self, isf(body="float f() { return 1.0; }")))
        self.assertIn("exactly one void main", refusal(self, isf(body=ok + "\nvoid main(void) { }")))

    def test_input_names_and_numbers_are_checked_strictly(self):
        def one(spec):
            return isf(INPUTS=[spec])
        for name in ("gl_FragColor", "hook", "frame", "random", "main", "TIME", "RENDERSIZE", "sin", "float", "pvj_x", "isf_x", "HOOKED_pos",
                     "PVJ_HP", "Hook", "Frame", "RANDOM", "Main", "Gl_x", "hooked_x", "texture0", "TEXCOORD0",    # (Time, rendersize: LenientTest)
                     "a b", "a;float b", "1a", "_a", "a__b", "", "x" * 33, "speed\n", "café", None, 7, ["a"]):
            with self.assertRaises(S.ShaderError, msg=repr(name)):
                S.parse(one({"NAME": name, "TYPE": "float"}))
        self.assertIn("two inputs", refusal(self, isf(INPUTS=[{"NAME": "a", "TYPE": "float"}, {"NAME": "a", "TYPE": "bool"}])))
        self.assertIn("at most", refusal(self, isf(INPUTS=[{"NAME": "a%d" % i, "TYPE": "float"} for i in range(S.MAX_INPUTS + 1)])))
        for spec in ({"DEFAULT": "1"}, {"MIN": 2, "MAX": 1}, {"MAX": 1e9}, {"DEFAULT": True}, {"MIN": None}, {"DEFAULT": [1]}):
            with self.assertRaises(S.ShaderError, msg=spec):
                S.parse(one(dict({"NAME": "a", "TYPE": "float"}, **spec)))
        # json.loads accepts NaN and Infinity, which are not valid GLSL numbers
        for word in ("NaN", "Infinity", "-Infinity", "1e999"):
            text = '/*{"INPUTS": [{"NAME": "a", "TYPE": "float", "DEFAULT": %s}]}*/\nvoid main() { gl_FragColor = vec4(a); }' % word
            self.assertRegex(refusal(self, text), "must be a number|not a number", word)
        for spec in ({"TYPE": "long", "VALUES": [1, 2], "DEFAULT": 3}, {"TYPE": "long", "VALUES": "x"}, {"TYPE": "long", "DEFAULT": 1.5},
                     {"TYPE": "long", "VALUES": [1, "2"]}, {"TYPE": "long", "VALUES": list(range(65))}, {"TYPE": "bool", "DEFAULT": "yes"},
                     {"TYPE": "bool", "DEFAULT": 2}, {"TYPE": "color", "DEFAULT": [1, 1]}, {"TYPE": "color", "DEFAULT": "red"},
                     {"TYPE": "color", "DEFAULT": [1, 1, "1", 1]}, {"TYPE": "point2D", "DEFAULT": [1, 2, 3]}, {"TYPE": "point2D", "DEFAULT": [1, None]}):
            with self.assertRaises(S.ShaderError, msg=spec):
                S.parse(one(dict({"NAME": "a"}, **spec)))
        p = S.parse(one({"NAME": "a", "TYPE": "float", "MIN": 0, "MAX": 10, "DEFAULT": 50, "LABEL": "A\x00 \x1b[31mlabel\n"}))
        self.assertEqual((p["inputs"][0]["default"], p["inputs"][0]["label"]), (10.0, "A [31mlabel"))
        self.assertEqual(S.parse(one({"NAME": "c", "TYPE": "color", "DEFAULT": [2, -1, 0.5]}))["inputs"][0]["default"], [1.0, 0.0, 0.5, 1.0])

    def test_header_values_that_made_the_parser_raise_are_refused_plainly(self):
        """Each of these raised an uncaught exception (OverflowError, ValueError) instead of a ShaderError."""
        ok = "\nvoid main() { gl_FragColor = vec4(1.0); }"
        huge = "9" * 400
        for inputs in ('{"NAME": "a", "TYPE": "long", "DEFAULT": NaN}', '{"NAME": "a", "TYPE": "long", "DEFAULT": Infinity}',
                       '{"NAME": "a", "TYPE": "long", "DEFAULT": -Infinity}', '{"NAME": "a", "TYPE": "long", "DEFAULT": %s}' % huge,
                       '{"NAME": "a", "TYPE": "float", "MIN": %s}' % huge, '{"NAME": "a", "TYPE": "float", "MAX": -%s}' % huge,
                       '{"NAME": "a", "TYPE": "color", "DEFAULT": [1, %s, 1, 1]}' % huge, '{"NAME": "a", "TYPE": "point2D", "DEFAULT": [%s, 0]}' % huge,
                       '{"NAME": "a", "TYPE": "long", "VALUES": [1, %s]}' % huge, '{"NAME": "a", "TYPE": "bool", "DEFAULT": NaN}'):
            with self.assertRaises(S.ShaderError, msg=inputs[:60]):
                S.parse('/*{"INPUTS": [%s]}*/%s' % (inputs, ok))

    def test_a_header_key_given_twice_is_refused(self):
        """The last one won: an INPUTS list with an image input, then an empty one; PASSES twice."""
        ok = "\nvoid main() { gl_FragColor = vec4(1.0); }"
        for head in ('{"INPUTS": [{"NAME": "pic", "TYPE": "image"}], "INPUTS": []}', '{"PASSES": [{"TARGET": "a"}, {}], "PASSES": []}',
                     '{"INPUTS": [{"NAME": "a", "TYPE": "float", "MAX": 1e9, "MAX": 1}]}', '{"DESCRIPTION": "a", "DESCRIPTION": "b"}'):
            with self.assertRaises(S.ShaderError, msg=head) as c:
                S.parse("/*" + head + "*/" + ok)
            self.assertIn("twice", str(c.exception))

    def test_a_file_built_to_make_the_checks_slow_is_checked_quickly(self):
        """"void main(" followed by 31,000 spaces took 2.9 s with the first pattern."""
        import time
        ok = "void main() { gl_FragColor = vec4(1.0); }\n"
        for tail in ("void main(" + " " * 31000, "void" + " " * 31000 + "main", "#" + " " * 31000, ("void main( " * 2500), "/" * 31000,
                     ";" + " " * 31000 + "x", "\t" * 31000 + "#define", "{ " * 15000):
            started = time.thread_time()            # the work this thread did, not the time a busy runner kept it waiting
            try:
                S.translate(S.parse("/*{}*/\n" + ok + tail), (1280, 720))
            except S.ShaderError:
                pass
            self.assertLess(time.thread_time() - started, 0.5, tail[:12])

    def test_the_code_is_translated_once_per_file_not_at_every_show(self):
        p = S.parse(isf())
        self.assertIn("void pvj_main()", p["code"])
        import unittest.mock
        with unittest.mock.patch.object(S.re, "sub", side_effect=AssertionError("translate must not search the code again")):
            self.assertIn(p["code"], S.translate(p, (1280, 720), {"speed": 2}))

    def test_files_that_are_not_isf_or_too_large_are_refused(self):
        ok = "void main() { gl_FragColor = vec4(1.0); }"
        # (a first comment that does not begin with { is a plain comment since comments may stand before the header:
        # "/* not json */" and "/*[1, 2]*/" are refused as before, now for having no header; see LenientTest)
        for text, reason in ((ok, "not an ISF file"), ("/*{", "not closed"), ("/*{ not json }*/" + ok, "cannot be read"),
                             ("/* not json */" + ok, "not an ISF file"), ("/*[1, 2]*/" + ok, "not an ISF file"),
                             ("/*{\"a\": " + "[" * 5000 + "*/" + ok, "cannot be read"), ("/*" + "[" * 500 + "*/" + ok, "not an ISF file"),
                             ("/*" + "[" * 5000 + "*/" + ok, "stand before the JSON header"),
                             ("/*{\"INPUTS\": 5}*/" + ok, "INPUTS"), ("/*{}" + " " * S.MAX_HEADER + "*/" + ok, "not closed, or is larger"),
                             (isf() + "/" * S.MAX_SOURCE, "larger than"), (b"\xff\xfe\x00", "not plain text"), (5, "must be text")):
            with self.assertRaises(S.ShaderError) as c:
                S.parse(text)
            self.assertIn(reason, str(c.exception))
        self.assertTrue(S.parse("﻿  \r\n/*{}*/\r\n" + ok))       # a byte order mark and Windows line ends are fine

    def test_every_bundled_shader_translates_and_carries_its_licence_and_cost(self):
        names = sorted(n for n in os.listdir(S.BUNDLED_DIR) if n.endswith(".fs"))
        self.assertEqual(names, sorted("nxlx-%s.fs" % n for n in FIRST_TEN + AMBIENT + PERFORMANCE))
        self.assertEqual((len(names), len(set(names))), (BUNDLED, BUNDLED))
        for n in names:
            self.assertTrue(S.FILE.fullmatch(n), n)
            with open(os.path.join(S.BUNDLED_DIR, n), "rb") as f:
                data = f.read()
            text = data.decode("ascii")                               # plain ASCII: no em dashes, nothing odd
            self.assertIn("SPDX-License-Identifier: Apache-2.0", text, n)
            self.assertIn("SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors", text, n)
            p = S.parse(data)
            self.assertTrue(p["description"] and p["cost"] and p["credit"], n)
            self.assertTrue(any(i["type"] == "float" for i in p["inputs"]), n)
            for i in p["inputs"]:
                if i["type"] == "float":
                    self.assertTrue(i["min"] <= i["default"] <= i["max"] and i["min"] < i["max"], (n, i))
            self.assertNotRegex(p["body"], r"\bwhile\b", n)            # cheap by rule: short fixed loops only
            for bound in re.findall(r"for \(int i = 0; i < (\d+); i\+\+\)", p["body"]):
                self.assertLessEqual(int(bound), 6, n)
            self.assertEqual(len(re.findall(r"\bfor\s*\(", p["body"])), len(re.findall(r"for \(int i = 0; i < \d+; i\+\+\)", p["body"])), n)
            t = S.translate(p, (1280, 720), V.vary(p["inputs"], random.Random(3)), hue=45.0, offset=10.0)
            self.assertIn("//!HOOK NATIVE", t)
            self.assertEqual(t.count("//!"), 5)
            for known in WELL_KNOWN:
                self.assertNotIn(known, p["body"], n)

    def test_the_two_new_families_keep_their_rules(self):
        """The shaders after the first ten: a family, 4 to 8 inputs with plain labels, choices with a label each, a
        cost that starts with low or medium, loops of at most 4 rounds, and one sentence that says what it is."""
        for family, names in (("Ambient", AMBIENT), ("Performance", PERFORMANCE)):
            for name in names:
                with open(os.path.join(S.BUNDLED_DIR, "nxlx-%s.fs" % name), "rb") as f:
                    data = f.read()
                p = S.parse(data)
                head = json.loads(data[2:data.index(b"*/")])
                self.assertEqual(sorted(p["categories"]), sorted(["Generator", family]), name)
                self.assertEqual(S.default_in_vibes(p), family == "Ambient", name)
                self.assertEqual(head["CREDIT"], "NXLX.Systems and contributors", name)
                self.assertTrue(4 <= len(p["inputs"]) <= 8, (name, len(p["inputs"])))
                self.assertEqual(p["cost"].split(":")[0], "medium" if name in MEDIUM else "low", name)      # what the Pi 4 measured
                self.assertNotIn(name, HEAVY)                 # a heavy one would be left out of its set (Ambient or Show)
                self.assertTrue(20 <= len(head["DESCRIPTION"]) <= S.MAX_TEXT and head["DESCRIPTION"].endswith("."), name)
                for spec, i in zip(head["INPUTS"], p["inputs"]):
                    self.assertTrue(spec.get("LABEL") and i["label"] != i["name"], (name, i["name"]))
                    self.assertIn(i["type"], ("float", "bool", "long", "color", "point2D"), (name, i["name"]))
                    if i["type"] == "long":
                        self.assertEqual(len(spec["VALUES"]), len(spec["LABELS"]), (name, i["name"]))
                        self.assertTrue(len(spec["VALUES"]) >= 2 and all(isinstance(x, str) and x for x in spec["LABELS"]), (name, i["name"]))
                    if i["type"] == "point2D":
                        self.assertEqual((spec["MIN"], spec["MAX"]), ([0.0, 0.0], [1.0, 1.0]), (name, i["name"]))
                        self.assertTrue(all(0.0 <= c <= 1.0 for c in i["default"]), (name, i["name"]))
                for bound in re.findall(r"for \(int i = 0; i < (\d+); i\+\+\)", p["body"]):
                    self.assertLessEqual(int(bound), 4, name)
                self.assertNotRegex(p["body"], r"#\s*define", name)
                # Time is folded before it is used, so a picture is as smooth after a day as in its first minute: TIME
                # appears only as fract(TIME * rate), a place in a cycle, or as a count of beats that is then folded.
                folded = len(re.findall(r"\bfract\(TIME \* ", p["body"])) + len(re.findall(r"\bfloat beats = TIME \* ", p["body"]))
                self.assertEqual(len(re.findall(r"\bTIME\b", p["body"])), folded, name)
                self.assertGreater(folded, 0, name)
                if family == "Performance":
                    # The rate is capped in the code itself, not only by the slider's MAX: at most 3 a second (a
                    # sweep or a turn, which passes a point twice or lights a wide trail, at most 1.5). `rate` is
                    # never multiplied into TIME bare; it may only slow a side motion down, inside a bracket.
                    rate = next(i for i in p["inputs"] if i["name"] == "rate")
                    cap = re.findall(r"\bmin\(rate, (\d\.\d)\)", p["body"])
                    self.assertTrue(cap and all(float(c) <= 3.0 for c in cap), (name, cap))
                    self.assertEqual(float(cap[0]), rate["max"], name)
                    self.assertNotRegex(p["body"], r"TIME \* rate\b", name)
                    if any(i["name"] == "fast" for i in p["inputs"]):
                        fast = next(i for i in p["inputs"] if i["name"] == "fast")
                        self.assertEqual((fast["type"], fast["default"]), ("bool", False), name)      # off until asked for
                        self.assertIn("min(rate, 3.0) * (fast ? 2.0 : 1.0)", p["body"], name)
                        self.assertIn("not for photosensitive people", p["description"], name)
                    else:
                        self.assertNotRegex(p["body"], r"\bfast\b", name)
                else:
                    self.assertFalse(any(i["name"] in ("rate", "fast") for i in p["inputs"]), name)       # nothing to flash with
        self.assertEqual(len(set(FIRST_TEN + AMBIENT + PERFORMANCE)), BUNDLED)

    def test_the_carrier_is_built_from_whole_numbers_and_has_the_screens_shape(self):
        self.assertEqual(S.carrier_url((1920, 1080), counter=False), "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0")
        counted = S.carrier_url((1920, 1080))                         # the same picture, each frame painted with its number
        self.assertEqual(counted, "av://lavfi:color=c=black:size=64x36:rate=30,format=gbrp,geq=r=N-256*floor(N/256):"
                                  "g=floor(N/256)-256*floor(N/65536):b=floor(N/65536)-256*floor(N/16777216),format=rgb0")
        self.assertTrue(S.is_carrier(counted))
        self.assertEqual(S.carrier_url((2560, 1440)), S.carrier_url((1280, 720)))
        self.assertEqual(S.carrier_url((1920, 1200), False), "av://lavfi:color=c=black:size=64x40:rate=30,format=rgb0")
        self.assertEqual(S.carrier_url((1366, 768), False), "av://lavfi:color=c=black:size=683x384:rate=30,format=rgb0")
        self.assertEqual(S.carrier_url((1080, 1920), False), "av://lavfi:color=c=black:size=27x48:rate=30,format=rgb0")
        for good in (S.carrier_url((1920, 1080)), S.carrier_url((1366, 768)), S.carrier_url((1366, 768), False)):
            self.assertTrue(S.is_carrier(good))
        for bad in (S.carrier_url((1920, 1080)) + "\n", S.carrier_url((1920, 1080)).replace("floor(N/256)", "floor(N/255)"),
                    S.carrier_url((1920, 1080)).replace(",format=rgb0", ",movie=/etc/passwd,format=rgb0"),
                    "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0,movie=/etc/passwd",
                    "av://lavfi:smptehdbars=size=1920x1080:rate=25", "/media/a.mp4", None, 5):
            self.assertFalse(S.is_carrier(bad), bad)
        self.assertEqual(S.render_size((1920, 1080), 720), (1280, 720))
        self.assertEqual(S.render_size((2560, 1440), 1080), (1920, 1080))
        self.assertEqual(S.render_size((1280, 720), 1080), (1280, 720))        # never more lines than the screen has
        self.assertEqual(S.render_size((1920, 1200), 540), (864, 540))

    def test_the_players_error_log_becomes_one_plain_message(self):
        """The lines are what mpv 0.37 wrote in CI for a shader with two mistakes on lines 101 and 102."""
        log = [("vo/gpu/opengl", "error", "fragment shader source:"), ("vo/gpu/opengl", "error", "[  1] #version 320 es"),
               ("vo/gpu/opengl", "error", "[ 53] color = hook();"), ("vo/gpu/opengl", "error", "fragment shader compile log (status=0):"),
               ("vo/gpu/opengl", "error", "0:101(2): error: initializer of type int cannot be assigned to variable of type float"),
               ("vo/gpu/opengl", "error", "0:102(14): error: `nonsense' undeclared"), ("vo/gpu/opengl", "error", ""),
               ("vo/gpu/opengl", "error", "shader link log (status=0): error: linking with uncompiled/unspecialized shader")]
        self.assertEqual(S.shader_errors(log), "line 101: initializer of type int cannot be assigned to variable of type float; "
                                               "line 102: `nonsense' undeclared")
        self.assertEqual(S.shader_errors([("vo/gpu", "error", "Unrecognized command 'WHAT 1'!")]), "Unrecognized command 'WHAT 1'!")
        self.assertEqual(S.shader_errors([]), "")
        self.assertEqual(S.shader_errors([("vo/gpu/drm", "error", "Failed to commit atomic request")]), "")   # not about a shader
        self.assertEqual(S.shader_errors([("ipc_3", "error", "shader compile log")]), "")                     # not the video output
        self.assertEqual(S.shader_errors([("vo/gpu/opengl", "v", "fragment shader source:")]), "")            # not an error


# Text that stands before the header in the tests below. Written for these tests: a made-up author, both kinds of
# comment, a comment of several lines, blank lines, text that is not ASCII, and things that only look like a header.
LEAD = ("// Slow Lanterns, by Ana Example (a made-up credit) – café\n"
        "// free to use; a brace { and a /*{\"DESCRIPTION\": \"not me\"}*/ in a line comment are comment text\n"
        "\n"
        "/* a block of credit\n"
        "   over three lines, with {braces} that do not come first\n"
        "*/\n"
        "\t /**/ /* two more */\n"
        "\n")
OK_MAIN = "void main() { gl_FragColor = vec4(1.0); }"


class LenientTest(unittest.TestCase):
    """Three things real files do that were refused: comments before the header, a switch whose DEFAULT is the number
    0 or 1, and an input whose name is a reserved word in another letter case (`time`). With what must stay refused."""

    # -- comments before the header --
    def test_comments_and_blank_space_may_stand_before_the_header_and_never_reach_the_player(self):
        plain, text = S.parse(isf()), LEAD + isf()
        p = S.parse(text)
        for key in ("description", "credit", "inputs", "body", "code", "kind"):
            self.assertEqual(p[key], plain[key], key)                # the same shader, whatever stood before it
        out = S.translate(p, (640, 360))
        for gone in ("Ana", "Lanterns", "credit", "braces", "not me", "two more"):
            self.assertNotIn(gone, out)
        self.assertEqual(out.replace("PVJ_LINE %d" % p["line"], "L").replace("PVJ_LINE %d" % (p["line"] - 1), "M"),
                         S.translate(plain, (640, 360)).replace("PVJ_LINE %d" % plain["line"], "L").replace("PVJ_LINE %d" % (plain["line"] - 1), "M"))
        # Windows line ends, a byte order mark, and bytes instead of text
        for same in (text.replace("\n", "\r\n"), "﻿" + text, text.encode("utf-8"), ("﻿" + text.replace("\n", "\r\n")).encode("utf-8")):
            self.assertEqual(S.parse(same)["code"], plain["code"])
        # each kind alone, and a header that has blank space before its {
        for lead in ("// one line\n", "//\n", "/* one block */", "/**/", "/***/", "/*/ a slash first */", "\n\n\t ", "//a\n//b\n/*c*/\n//d\n"):
            self.assertEqual(S.parse(lead + isf())["code"], plain["code"], lead)
        self.assertEqual(S.parse("// x\n/* \n\t {\"DESCRIPTION\": \"late brace\"} */\n" + OK_MAIN)["description"], "late brace")
        # the same for a filter of the playing picture (it is the same parser)
        fx = "/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\nvoid main() { gl_FragColor = IMG_THIS_PIXEL(inputImage); }\n"
        self.assertEqual(S.parse(LEAD + fx, S.FILTER)["code"], S.parse(fx, S.FILTER)["code"])

    def test_line_numbers_stay_those_of_the_file_with_comments_before_the_header(self):
        """The compiler is told the file's own line numbers (#line): the lines before the header are counted, so a
        mistake is still reported on the line it has in the file."""
        body = "\nfloat one() { return 1.0; }\n/* a comment\n   of two lines */\nvoid main() { gl_FragColor = vec4(MARK); }\n"
        for lead in ("", LEAD, "// a\n", "/* a\n\n\nb */", LEAD * 3):
            text = lead + isf(body=body)
            p = S.parse(text)
            lines = text.split("\n")
            self.assertEqual(lines[p["line"] - 1], "}*/", lead)             # the code starts on the line the header ends on
            code = p["code"].split("\n")
            at = [n for n, line in enumerate(lines) if "MARK" in line]
            self.assertEqual(len(at), 1)
            self.assertIn("MARK", code[at[0] - (p["line"] - 1)], lead)      # the line of the file is the line of the code
            self.assertEqual(p["line"], S.parse(isf(body=body))["line"] + lead.count("\n"))
            out = S.translate(p, (640, 360))
            self.assertIn("#define PVJ_LINE %d\n#else\n#define PVJ_LINE %d\n#endif\n#line PVJ_LINE\n%s" % (p["line"], p["line"] - 1, p["code"]), out)
            self.assertEqual(out.count("#line"), 1)

    def test_only_comments_and_blank_space_may_stand_before_the_header(self):
        head = "/*{}*/\n" + OK_MAIN
        for lead in ("float x = 1.0;\n", "x", "/", "#version 100\n", "#define A 1\n", "#\n", "uniform float u;\n", "*/", "{}", "\\\n",
                     "// a\nvoid f() {}\n", " ", " ", "\x00", "\x1b", "﻿﻿​"):
            self.assertIn("not an ISF file: it must start with", refusal(self, lead + head), repr(lead))
        # code after a plain comment: the message says that the comment was not taken for the header
        for lead in ("/**/uniform float u;\n", "/* credit */ #version 100\n", "/* a */ float x;", "/* a *//", "/* [1, 2] */ x"):
            self.assertIn("read as a plain comment", refusal(self, lead + head), repr(lead))
        # nothing but comments, or nothing at all
        for text in ("", "   \n", "// only a line", "// a line\n", "// /*{}*/ " + OK_MAIN):
            self.assertIn("not an ISF file: it must start with", refusal(self, text), repr(text))
        for text in ("/* a */", "/* a */\n// b\n", "/* not json */" + OK_MAIN, "/*[1, 2]*/" + OK_MAIN, "/**/" + OK_MAIN):
            self.assertIn("read as a plain comment", refusal(self, text), repr(text))
        # a comment that is never closed
        for text in ("/* credit", "/* credit\n" + OK_MAIN, "// a\n/*", "/**/ /* x * /" + head.replace("*/", "* /"), "/*/"):
            self.assertIn("before the JSON header is never closed", refusal(self, text), repr(text))
        self.assertIn("not closed", refusal(self, "// a\n/*{\"INPUTS\": []}\n" + OK_MAIN))          # the header itself
        # mpv's command marker is refused wherever it stands, before the header too
        for lead in ("//!HOOK OUTPUT\n", "// credit //!DESC x\n", "/* //!BIND X */\n", "/*\n//!HOOK OUTPUT\n*/", "// a\n\n//!SAVE MAIN\n"):
            self.assertIn("//!", refusal(self, lead + head), repr(lead))
        # a // comment ends at its line break here whatever its last character is: what follows is not comment
        self.assertTrue(S.parse("// credit \\\n" + head))
        self.assertIn("it must start with", refusal(self, "// credit \\\nuniform float u;\n" + head))

    def test_the_first_comment_that_begins_with_a_brace_is_the_header_and_no_other_is_tried(self):
        ok = "\n" + OK_MAIN
        # it looks like JSON and begins with {: it IS the header, and its mistakes are reported, not passed over
        for text, reason in (("/* {\"INPUTS\": 5} */" + ok, "INPUTS"),
                             ("/*{ credit: me }*/\n/*{}*/" + ok, "cannot be read"),
                             ("// a\n/*{broken*/\n/*{\"DESCRIPTION\": \"second\"}*/" + ok, "cannot be read"),
                             ("/*\n{ }x*/\n/*{}*/" + ok, "cannot be read"),
                             ("/*{\"PASSES\": [{}, {}]}*/\n/*{}*/" + ok, "several passes"),
                             ("/* c */\n/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n/*{}*/" + ok, "only generator shaders"),
                             ("/*{\"A\": 1, \"A\": 2}*/" + ok, "twice")):
            self.assertIn(reason, refusal(self, text), text[:50])
        # a second header after the first is a comment in the code, as it always was: it changes nothing
        two = "// a\n/*{\"DESCRIPTION\": \"first\"}*/\n/*{\"DESCRIPTION\": \"second\", \"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/" + ok
        p = S.parse(two)
        self.assertEqual((p["description"], p["inputs"]), ("first", []))
        self.assertNotIn("second", S.translate(p, (640, 360)))
        # a header cannot hide inside a comment before it. Comments do not nest: the plain comment ends at the first
        # */, which is the hidden header's own, and what follows is code with no header before it
        self.assertIn("read as a plain comment", refusal(self, "/* credit\n/*{\"DESCRIPTION\": \"hidden\"}*/" + ok))
        self.assertIn("cannot be read", refusal(self, "/* credit\n/*{\"DESCRIPTION\": \"hidden\"}*/\n/*{ \"x\" */\n/*{}*/" + ok))
        # in a // line it is comment text, and the header is the one that follows
        p = S.parse("// /*{\"DESCRIPTION\": \"in a line\", \"INPUTS\": [{\"NAME\": \"a\", \"TYPE\": \"float\"}]}*/\n/*{\"DESCRIPTION\": \"real\"}*/" + ok)
        self.assertEqual((p["description"], p["inputs"]), ("real", []))
        # a comment that holds braces but does not begin with one is a plain comment
        self.assertEqual(S.parse("/* by me {2026} */\n/*{\"DESCRIPTION\": \"real\"}*/" + ok)["description"], "real")

    def test_what_stands_before_the_header_is_bounded(self):
        head = "/*{}*/\n" + OK_MAIN
        fits = "//" + "x" * (S.MAX_LEADING - 3) + "\n"
        self.assertEqual(len(fits), S.MAX_LEADING)
        self.assertTrue(S.parse(fits + head))
        for lead in (fits + " ", fits + "\n", "//" + "x" * S.MAX_LEADING + "\n", " " * (S.MAX_LEADING + 1), "\n" * (2 * S.MAX_LEADING),
                     "/*" + "x" * S.MAX_LEADING + "*/", "/**/" * (S.MAX_LEADING // 4) + " ", "// a\n" * S.MAX_LEADING,
                     "//" + "é" * (S.MAX_LEADING // 2) + "\n",            # counted in bytes, as the file's size is
                     "/* a */" + " " * S.MAX_LEADING):
            self.assertIn("stand before the JSON header", refusal(self, lead + head), lead[:20])
        self.assertIn("stand before the JSON header", refusal(self, " " * (S.MAX_LEADING + 1) + "float x;" + head))
        # the header's own limit is counted from where the header starts, and the file's limit is the file's
        self.assertTrue(S.parse(fits + "/*{}" + " " * (S.MAX_HEADER - 8) + "*/\n" + OK_MAIN))
        self.assertIn("not closed, or is larger", refusal(self, fits + "/*{}" + " " * S.MAX_HEADER + "*/\n" + OK_MAIN))
        self.assertIn("larger than", refusal(self, fits + head + "/" * S.MAX_SOURCE))
        self.assertGreaterEqual(S.MAX_SOURCE - S.MAX_HEADER - S.MAX_LEADING, 20 * 1024)        # room that is left for the code
        # plain blank space counts like comments: 4096 spaces fit, 4097 do not
        self.assertTrue(S.parse(" " * S.MAX_LEADING + head))
        self.assertIn("stand before the JSON header", refusal(self, " " * (S.MAX_LEADING + 1) + head))
        # the limit is counted after the byte order mark is dropped and Windows line ends are made \n (as the header's
        # is): 900 lines of "//x" are 4,500 bytes with \r\n and 3,600 as they are counted
        self.assertTrue(S.parse(("﻿" + "//x\r\n" * 900 + head).encode("utf-8")))
        self.assertIn("stand before the JSON header", refusal(self, "//x\r\n" * 1025 + head))

    def test_the_headers_limit_is_counted_in_bytes(self):
        """It was counted in characters (on master too): a header of 8,100 euro signs is 24 KB and was taken, so less
        than the promised room was left for the code."""
        def with_description(text):
            return "/*{\"DESCRIPTION\": \"%s\"}*/\n%s" % (text, OK_MAIN)
        self.assertTrue(S.parse(with_description("d" * 8000)))                         # 8,000 bytes
        self.assertTrue(S.parse(with_description("€" * 2600)))                    # 7,800 bytes
        for text in ("€" * 2800, "€" * 8100, "d" * S.MAX_HEADER, "é" * 4100):
            self.assertIn("not closed, or is larger", refusal(self, with_description(text)), text[:3])
            self.assertIn("not closed, or is larger", refusal(self, LEAD + with_description(text)), text[:3])
        edge = "/*{}" + " " * (S.MAX_HEADER - 4) + "*/\n" + OK_MAIN                    # exactly at the limit, and one over
        self.assertTrue(S.parse(edge))
        self.assertIn("not closed, or is larger", refusal(self, edge.replace("{} ", "{}  ", 1)))

    def test_which_comment_is_the_header_is_decided_by_its_first_character_after_plain_blank_space(self):
        """Blank space here is space, tab, line break, form feed and vertical tab. A comment whose first character
        after those is not { is a plain comment, however much it looks like a header, and the first comment after it
        that does begin with { is the header."""
        image = "{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}"
        rest = "*/\n/*{\"DESCRIPTION\": \"second\"}*/\n" + OK_MAIN
        for start in ("/*", "/* ", "/*\t\n ", "/*\r\n", "/*\n\n\n"):                   # the header: its picture input refuses the file
            self.assertIn("only generator shaders", refusal(self, start + image + rest), repr(start))
        for start in ("/**", "/* ", "/*/", "/*-", "/* ", "/*　", "/*\x00", "/*﻿", "/*x", "/*[", "/*\"", "/* * "):
            self.assertEqual(S.parse(start + image + rest)["description"], "second", repr(start))    # a plain comment
        # a form feed and a vertical tab are blank space to the search and not to JSON: the comment is the header,
        # and it cannot be read
        for start in ("/*\x0c", "/*\x0b", "/* \x0c "):
            self.assertIn("cannot be read", refusal(self, start + image + rest), repr(start))
        # before the header, blank space is the same five characters: everything else Unicode calls blank is refused
        for blank in (" ", "\x85", " ", " ", "　", "\x1c", "\x1f", "​", " "):
            self.assertIn("it must start with", refusal(self, blank + "/*{}*/\n" + OK_MAIN), repr(blank))
            self.assertIn("it must start with", refusal(self, "// a\n" + blank + "/*{}*/\n" + OK_MAIN), repr(blank))
        for blank in (" ", "\t", "\n", "\r\n", "\r", "\x0c", "\x0b"):
            self.assertTrue(S.parse(blank + "// a\n" + blank + "/*{}*/\n" + OK_MAIN), repr(blank))

    def test_nothing_that_was_refused_gets_through_behind_comments_before_the_header(self):
        cases = [(isf(PASSES=[{"TARGET": "a"}, {}]), "several passes"), (isf(PERSISTENT_BUFFERS=["a"]), "persistent buffer"),
                 (isf(IMPORTED={"pic": {"PATH": "/etc/passwd"}}), "IMPORTED"), (isf(INPUTS=[{"NAME": "inputImage", "TYPE": "image"}]), "only generator shaders"),
                 (isf(INPUTS=[{"NAME": "snd", "TYPE": "audio"}]), "sound"),
                 (isf(INPUTS=[{"NAME": "a%d" % i, "TYPE": "float"} for i in range(S.MAX_INPUTS + 1)]), "at most"),
                 (isf(INPUTS=[{"NAME": "hook", "TYPE": "float"}]), "is taken"),
                 (isf(body="#version 100\n" + OK_MAIN), "not allowed"), (isf(body="#include <x>\n" + OK_MAIN), "not allowed"),
                 (isf(body="#line 1\n" + OK_MAIN), "not allowed"), (isf(body="#define hook x\n" + OK_MAIN), "is not allowed"),
                 (isf(body="uniform sampler2D secret;\n" + OK_MAIN), "uniform, varying, in or out"),
                 (isf(body="float pvj_time;\n" + OK_MAIN), "used by the player"), (isf(body="vec4 hook() { return vec4(1.0); }\n" + OK_MAIN), "used by the player"),
                 (isf(body=OK_MAIN + "\nvoid main() {}"), "exactly one void main"), (isf(body="float a = 1.0; \\\n" + OK_MAIN), "line continuations"),
                 (isf(body="float café;\n" + OK_MAIN), "ASCII"), (isf(body=OK_MAIN + "/" * S.MAX_SOURCE), "larger than")]
        for text, reason in cases:
            self.assertIn(reason, refusal(self, text), text[:80])
            for lead in (LEAD, "// a\n", "/* a */"):
                self.assertIn(reason, refusal(self, lead + text), text[:80])
        fx = "/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}, {\"NAME\": \"other\", \"TYPE\": \"image\"}]}*/\n" + OK_MAIN
        with self.assertRaises(S.ShaderError) as c:
            S.parse(LEAD + fx, S.FILTER)
        self.assertIn("second picture", str(c.exception))

    def test_a_file_built_to_make_the_search_for_the_header_slow_is_read_quickly(self):
        """The search is one pass that stops at MAX_LEADING; these are the worst texts for it, as large as an upload
        may be (32 KB), and each is read 20 times."""
        import time
        n = S.MAX_SOURCE - 64
        tail = "/*{}*/\n" + OK_MAIN
        worst = ["/**/" * (n // 4), "//\n" * (n // 3), " " * n, "\n" * n, "/*" + " " * n, "/*" * (n // 2), "/* " * (n // 3), "/" * n,
                 "//" + "/" * n, "/*" + " " * (n - 4) + "*/", ("/*" + " " * 30 + "x*/") * (n // 35), "/*" + "{" * n, "/*" + " {" * (n // 2),
                 "/*" + "*" * n, ("/*" + "\t" * 2000) * (n // 2002), "// \\\n" * (n // 5), "/*{" + "/*{" * (n // 3 - 1), "/* */ " * (n // 6)]
        for text in worst + [w[:S.MAX_LEADING - 8] + "\n" + tail for w in worst] + [w + tail for w in worst]:
            self.assertLessEqual(len(text), S.MAX_SOURCE)
            started = time.thread_time()
            for _ in range(20):
                try:
                    S.translate(S.parse(text), (1280, 720))
                except S.ShaderError:
                    pass
            self.assertLess(time.thread_time() - started, 1.0, text[:12])

    # -- a switch whose DEFAULT is a number --
    def test_a_switch_takes_true_false_and_the_numbers_0_and_1_as_its_default_and_nothing_else(self):
        def default(word):
            text = '/*{"INPUTS": [{"NAME": "lit", "TYPE": "bool", "DEFAULT": %s}]}*/\nvoid main() { gl_FragColor = vec4(lit ? 1.0 : 0.0); }' % word
            p = S.parse(text)
            self.assertIn("const bool lit = %s;" % ("true" if p["inputs"][0]["default"] else "false"), S.translate(p, (640, 360)))
            return p["inputs"][0]["default"]
        for word, want in (("true", True), ("false", False), ("1", True), ("0", False), ("1.0", True), ("0.0", False), ("-0.0", False),
                           ("-0", False), ("1e0", True), ("0e5", False), ("1.000", True),
                           ("1.0000000000000001", True)):            # JSON reads this as the number 1.0: it IS 1 by then
            got = default(word)
            self.assertIs(got, want, word)                           # a real true or false, never the number
        for word in ('"yes"', '""', "2", "-1", "0.5", "1.0000001", "0.9999999", "1e-9", "255", "[1]", "[true]",
                     "[]", "{}", "null", "NaN", "Infinity", "1e999", "9" * 400):
            with self.assertRaises(S.ShaderError, msg=word) as c:
                default(word)
            self.assertRegex(str(c.exception), "DEFAULT of lit must be true or false|not a number", word)
        # a value that is set (the panel, a preset, MIDI) stays exactly true or false: this is about the file's header only
        p = S.parse('/*{"INPUTS": [{"NAME": "lit", "TYPE": "bool", "DEFAULT": 1}]}*/\n' + OK_MAIN)
        for bad in (1, 0, 1.0, "1", None):
            with self.assertRaises(S.ShaderError, msg=repr(bad)):
                S.clean_values(p, {"lit": bad})
        self.assertEqual(S.clean_values(p, {"lit": False}), {"lit": False})
        # the other switch in a header: an event has no DEFAULT of its own and is never on by itself
        self.assertIs(S.parse('/*{"INPUTS": [{"NAME": "go", "TYPE": "event", "DEFAULT": 1}]}*/\n' + OK_MAIN)["inputs"][0]["default"], False)

    def test_a_switch_also_takes_four_texts_in_quotes_as_its_default_and_no_other_text(self):
        """The owner's choice of 2026-10-08 (D66): "0", "1", "true" and "false" in quotes, letter for letter, as files
        written for other ISF hosts have them. The list is closed, so a typo is still an error, and nothing of the
        text reaches the shader: the value is true or false from the parser on."""
        def parsed(word):
            return S.parse('/*{"INPUTS": [{"NAME": "lit", "TYPE": "bool", "DEFAULT": %s}]}*/\nvoid main() { gl_FragColor = vec4(lit ? 1.0 : 0.0); }' % word)
        for word, want in (('"1"', True), ('"0"', False), ('"true"', True), ('"false"', False)):
            p = parsed(word)
            self.assertIs(p["inputs"][0]["default"], want, word)                 # a real true or false, never the text
            out = S.translate(p, (640, 360))
            self.assertIn("const bool lit = %s;" % ("true" if want else "false"), out, word)
            self.assertEqual(out.count("const bool lit"), 1)
            self.assertNotIn('"', out)                                           # nothing of the text is in the shader
            self.assertEqual(S.input_lines(p, {"lit": not want}), ["const bool lit = %s;" % ("false" if want else "true")])
        self.assertEqual(S._BOOL_TEXT, {"0": False, "1": True, "false": False, "true": True})
        for word in ('"TRUE"', '"True"', '"FALSE"', '"False"', '"yes"', '"no"', '"on"', '"off"', '" 1"', '"1 "', '"1\\n"', '"\\t0"', '"1.0"', '"0.0"',
                     '"01"', '"00"', '"+1"', '"-0"', '"2"', '""', '" "', '"t"', '"f"', '"y"', '"null"', '"true;"', '"true\\u0000"', '"\\uff11"',
                     '"1\\u200b"', '"tru\\u0435"', '["1"]', '["true"]', '{"1": true}', '"true); float x = (1.0"'):
            with self.assertRaises(S.ShaderError, msg=word) as c:
                parsed(word)
            self.assertIn("DEFAULT of lit must be true or false (also taken: the numbers 0 and 1, and \"0\", \"1\", \"true\", \"false\" in quotes",
                          str(c.exception), word)
        # only a switch's DEFAULT in the header: a value that is set stays exactly true or false,
        p = parsed('"1"')
        for bad in ("1", "0", "true", "false", 1, 0):
            with self.assertRaises(S.ShaderError, msg=repr(bad)):
                S.clean_values(p, {"lit": bad})
            with self.assertRaises(S.ShaderError, msg=repr(bad)):
                S.translate(p, (640, 360), {"lit": bad})
        # an event has no DEFAULT (whatever is written there is never read),
        self.assertIs(S.parse('/*{"INPUTS": [{"NAME": "go", "TYPE": "event", "DEFAULT": "true"}]}*/\n' + OK_MAIN)["inputs"][0]["default"], False)
        # and no other type takes text for a number
        for spec in ('"TYPE": "float", "DEFAULT": "1"', '"TYPE": "long", "DEFAULT": "1"', '"TYPE": "float", "MIN": "0"',
                     '"TYPE": "color", "DEFAULT": ["1", "0", "0", "1"]', '"TYPE": "point2D", "DEFAULT": ["0", "1"]', '"TYPE": "long", "VALUES": ["0", "1"]'):
            with self.assertRaises(S.ShaderError, msg=spec):
                S.parse('/*{"INPUTS": [{"NAME": "a", %s}]}*/\n%s' % (spec, OK_MAIN))
        # the switches a header is refused for are read as before: text there refuses the file, "0" and "false" too
        for head in ('{"PERSISTENT_BUFFERS": "0"}', '{"PASSES": [{"PERSISTENT": "false"}]}', '{"PASSES": [{"FLOAT": "0"}]}', '{"IMPORTED": "false"}'):
            with self.assertRaises(S.ShaderError, msg=head):
                S.parse("/*" + head + "*/\n" + OK_MAIN)

    # -- an input whose name is a reserved word in another letter case --
    def test_an_input_called_time_is_renamed_and_the_clock_stays_the_clock(self):
        body = ("float wave(float time_scale) { return sin(TIME * time + time_scale + TIMEDELTA); }\n"
                "void main() {\n    float t = time;\n    gl_FragColor = vec4(wave(t), Date, my_time.x, 1.0);\n}\n")
        inputs = [{"NAME": "time", "TYPE": "float", "MIN": 0, "MAX": 2, "DEFAULT": 0.5, "LABEL": "Time"}, {"NAME": "Date", "TYPE": "float"},
                  {"NAME": "my_time", "TYPE": "point2D"}]
        p = S.parse(isf(INPUTS=inputs, body=body))
        self.assertEqual([(i["name"], i["label"]) for i in p["inputs"]], [("time", "Time"), ("Date", "Date"), ("my_time", "my_time")])
        out = S.translate(p, (640, 360), {"time": 1.5})
        self.assertIn("const float pvj_in_time = 1.5;", out)
        self.assertIn("const float pvj_in_Date = 0.5;", out)
        self.assertIn("const vec2 my_time = vec2(0.0, 0.0);", out)                 # a name that only holds the word is left alone
        self.assertIn("#define TIME pvj_time", out)
        self.assertIn("return sin(TIME * pvj_in_time + time_scale + TIMEDELTA);", out)
        self.assertIn("float t = pvj_in_time;", out)
        self.assertIn("vec4(wave(t), pvj_in_Date, my_time.x, 1.0)", out)
        self.assertNotRegex(out, r"\btime\b")
        self.assertNotRegex(out.split("#line", 1)[1], r"\bDate\b")
        # everything outside the shader text keeps the file's own name: values by name, and their limits
        self.assertEqual(S.clean_values(p, {"time": 5, "Date": -1}), {"time": 2.0, "Date": 0.0})
        with self.assertRaises(S.ShaderError):
            S.clean_values(p, {"pvj_in_time": 1})
        self.assertEqual(S.shape_of(p, {"time": 1.0}), "[]")
        # both spellings at once are two inputs
        p = S.parse(isf(INPUTS=[{"NAME": "time", "TYPE": "float"}, {"NAME": "Time", "TYPE": "bool"}], body="void main() { gl_FragColor = vec4(Time ? time : 0.0); }"))
        out = S.translate(p, (640, 360))
        self.assertIn("const float pvj_in_time = 0.5;\nconst bool pvj_in_Time = false;", out)
        self.assertIn("vec4(pvj_in_Time ? pvj_in_time : 0.0)", out)
        # in a filter of the playing picture too
        fx = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}, {\"NAME\": \"time\", \"TYPE\": \"float\"}]}*/\n"
              "void main() { gl_FragColor = IMG_THIS_PIXEL(inputImage) * time * fract(TIME); }\n")
        f = S.parse(fx, S.FILTER)
        self.assertEqual(([i["name"] for i in f["inputs"]], f["clock"]), (["time"], True))
        self.assertIn("pvj_img_this() * pvj_in_time * fract(TIME)", f["code"])
        self.assertEqual(S.input_lines(f, {}), ["const float pvj_in_time = 0.5;"])

    def test_one_rule_says_which_reserved_names_are_renamed_and_which_stay_refused(self):
        def one(name):
            return S.parse(isf(INPUTS=[{"NAME": name, "TYPE": "float"}], body=OK_MAIN))["inputs"][0]["name"]
        # the language's and ISF's words in another letter case: taken, under a name no file can write
        for name in ("time", "Time", "tIME", "date", "Date", "timedelta", "frameindex", "passindex", "rendersize", "RenderSize", "Float", "FLOAT", "Mix",
                     "SIN", "Step", "Length", "Filter", "Input", "Sample", "Sampler2d", "TEXTURE", "img_pixel", "vv_fragnormcoord", "Color", "COLOR", "color"):
            self.assertEqual(one(name), name)
            self.assertTrue(S.renamed(name), name)
            self.assertEqual(S.ident(name), "pvj_in_" + name)
        # a reserved word letter for letter: refused, since renaming it would rename the code's own uses of the word
        for name in ("TIME", "TIMEDELTA", "DATE", "FRAMEINDEX", "PASSINDEX", "RENDERSIZE", "IMG_PIXEL", "float", "mix", "sin", "step", "length", "filter",
                     "input", "sample", "sampler2D", "texture", "texture2D", "vec4", "main", "frame", "random", "hook", "input_size", "pixel_size"):
            self.assertIn("is taken", refusal(self, isf(INPUTS=[{"NAME": name, "TYPE": "float"}])), name)
            self.assertFalse(S.renamed(name), name)
        # the player's and the translator's own words, their patterns and their prefixes: refused in any letter case
        for name in ("Frame", "FRAME", "Random", "RANDOM", "Main", "MAIN", "Hook", "HOOK", "Input_Size", "TARGET_SIZE", "Tex_Offset", "Pixel_Size",
                     "Out_Color", "out_color", "OUT_COLOR", "TEXCOORD0", "Texture0", "Hooked_pos", "HOOKED", "Gl_x", "GL_FragColor", "Pvj_x", "PVJ_HP",
                     "pvj_in_time", "Isf_x", "ISF_FragNormCoord", "isf_fragnormcoord", "ti__me", "time__"):
            self.assertIn("is taken", refusal(self, isf(INPUTS=[{"NAME": name, "TYPE": "float"}])), name)
        # every reserved word, in four spellings: it is refused or it is renamed, and never an input under its own name
        taken = 0
        for word in sorted(S.RESERVED):
            for name in {word, word.lower(), word.upper(), word.capitalize()}:
                try:
                    one(name)
                except S.ShaderError:
                    self.assertTrue(name in S.RESERVED or name.lower() in S._PLAYER_WORDS or name.lower().startswith("isf_"), name)
                    self.assertNotIn(name.lower(), S._INPUT_RENAMED)
                    continue
                taken += 1
                self.assertTrue(S.ident(name).startswith("pvj_in_"), name)
                self.assertTrue(name not in S.RESERVED or name == "color", name)
        self.assertGreater(taken, 250)
        self.assertTrue(S._PLAYER_WORDS <= S.RESERVED)
        # an ordinary name is neither
        for name in ("speed", "timer", "my_time", "time2", "colour", "frames"):
            self.assertEqual((one(name), S.ident(name)), (name, name))
        # the renamed names cannot be defined, undefined or written by the code itself
        for line in ("#define time 1.0", "#undef time", "#define Time x", "#define Date 1.0", "#define TIME 0.0", "#undef TIME"):
            self.assertIn("is not allowed", refusal(self, isf(INPUTS=[{"NAME": "time", "TYPE": "float"}], body=line + "\n" + OK_MAIN)), line)
        for line in ("float pvj_in_time = 1.0;", "float PVJ_IN_TIME;", "float pvj_time;"):
            self.assertIn("used by the player", refusal(self, isf(INPUTS=[{"NAME": "time", "TYPE": "float"}], body=line + "\n" + OK_MAIN)), line)
        # in a filter the player's clock names stay refused as inputs, letter for letter and in any letter case
        for name in ("frame", "random", "Frame", "RANDOM"):
            with self.assertRaises(S.ShaderError, msg=name):
                S.parse("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}, {\"NAME\": \"%s\", \"TYPE\": \"float\"}]}*/\n"
                        "void main() { gl_FragColor = IMG_THIS_PIXEL(inputImage); }" % name, S.FILTER)


class FakeIpc:
    def __init__(self, player):
        self.p = player

    def request(self, *command):
        if command[:2] == ("get_property", "path"):
            return self.p.path
        if command[:2] == ("get_property", "current-vo"):
            return self.p.vo
        if command[:2] == ("get_property", "vo-passes"):
            return {"fresh": [{"desc": "user shader: %s (rgb)" % d, "avg": self.p.pass_ns, "last": self.p.pass_ns} for d in self.p.drawn], "redraw": []}
        return None


class SourcePlayer:
    """A player with the shader-source calls of pvj.player.Player and nothing behind them."""
    TEST_PATTERN = "av://lavfi:smptehdbars=size=1920x1080:rate=25"
    TEST_TONES = {}

    def __init__(self, rundir):
        self.rundir, self.socket_path = rundir, os.path.join(rundir, "player.sock")
        self.source_epoch, self.source_shader, self.path, self.vo = 0, None, None, "null"
        self.calls, self.drawn, self.down, self.pass_ns = [], [], False, 1500000
        self.level, self.carrier = 100.0, None
        self.ipc = FakeIpc(self)
        # as pvj.player.Player: its own lock around every change of what plays, at the same place in the order of
        # the locks (pvj/locks.py), so that the engines are tested against the order as against the real player
        from pvj import locks
        self._lock = locks.make("player", reentrant=True)

    def osd_size(self):
        return (1920, 1080)

    def status(self):
        return {"running": True, "path": self.path}

    def play_source(self, shader, carrier, epoch=None, spawn=False):
        from pvj.player import PlayerError
        with self._lock:
            if self.down:
                raise PlayerError("player service is not running")
            if epoch is not None and epoch != self.source_epoch:
                return None
            with open(shader) as f:
                text = f.read()
            self.drawn = [re.search(r"//!DESC (.*)", text).group(1)]
            self.source_shader, self.path, self.carrier = shader, carrier, carrier
            self.source_epoch += 1
            self.calls.append(("play_source", shader, carrier))
            return self.source_epoch

    def claim_screen(self):
        with self._lock:
            self.source_epoch += 1

    def opacity(self, value):
        self.level = value / 2.55
        self.calls.append(("opacity", value))

    def source_opacity(self, value, epoch):
        with self._lock:
            if epoch != self.source_epoch:
                return False
            self.opacity(value)
            return True

    def opacity_now(self):
        return self.level

    def clear_source(self, epoch):
        with self._lock:
            if epoch != self.source_epoch or self.carrier is None:
                return False
            self.clear()
            return True

    def swap_source(self, shader, epoch):
        with self._lock:
            if epoch != self.source_epoch:
                return False
            self.source_shader = shader
            self.calls.append(("swap_source", shader))
            return True

    def play(self, paths, *a, **k):
        with self._lock:
            self.source_epoch += 1
            self.source_shader, self.path, self.carrier = None, paths[0], None
            self.calls.append(("play", paths))

    def clear(self):
        with self._lock:
            self.source_epoch += 1
            self.source_shader, self.path, self.carrier = None, None, None
            self.calls.append(("clear",))

    def __getattr__(self, name):
        if name in ("set_shaders", "set_mapping_mode", "overlay_remove", "pause", "size", "position", "speed", "rotate", "flip"):
            return lambda *a: self.calls.append((name,) + a)
        raise AttributeError(name)


class FakeTap:
    lines = []

    def __init__(self, path, level="error"):
        self.sent = False

    def drain(self, seconds):
        out, self.sent = ([] if self.sent else list(FakeTap.lines)), True
        return out

    def close(self):
        pass


REFUSAL = [("vo/gpu/opengl", "error", "fragment shader compile log (status=0):"), ("vo/gpu/opengl", "error", "0:12(3): error: `oops' undeclared")]
GOOD = "/*{\"DESCRIPTION\": \"mine\", \"INPUTS\": [{\"NAME\": \"k\", \"TYPE\": \"float\", \"MIN\": 0, \"MAX\": 4, \"DEFAULT\": 1}]}*/\nvoid main() { gl_FragColor = vec4(k); }\n"


class Base(ServerBase):
    def setUp(self):
        super().setUp()
        self.player = self.api.player = SourcePlayer(self.rundir)
        self.engine = self.api.shaders
        self.engine.log = lambda *_: None
        self.engine._tap = FakeTap
        FakeTap.lines = []
        self.api.registry.set_enabled("shaders", True)

    def generated(self):
        return sorted(n for n in os.listdir(self.rundir) if n.startswith("shader-"))


class EngineTest(Base):
    def test_shader_texts_nobody_uses_go_at_start_and_on_stop(self):
        """Seen on the Pi: the text an earlier panel process generated stayed in the panel's runtime folder until the
        first play, and the last one stayed after Stop."""
        loaded = []
        real = self.player.ipc.request

        def request(*command):
            if command[:2] == ("get_property", "glsl-shaders"):
                if loaded == ["cannot say"]:
                    raise RuntimeError("no answer")
                return list(loaded)
            return real(*command)
        self.player.ipc.request = request
        self.player.is_running = lambda: True

        def leave(*names):
            for n in names:
                with open(os.path.join(self.rundir, n), "w") as f:
                    f.write("// old")
        other = ("shader-1-7.glsl", "shader-1-8.glsl.tmp", "shader-99999-1.glsl")
        leave(*other)
        leave("overlay-1.bgra", "notes.glsl")
        # at start: what the player still has loaded stays, the rest goes, and nothing else in the folder is touched
        loaded[:] = [os.path.join(self.rundir, "shader-1-7.glsl"), "/somewhere/else/mapping.glsl"]
        self.engine.tidy()
        self.assertEqual(self.generated(), ["shader-1-7.glsl"])
        self.assertEqual(sorted(n for n in os.listdir(self.rundir) if not n.startswith("shader-") and n != "player.sock"), ["notes.glsl", "overlay-1.bgra"])
        # a player that runs and cannot say what it has loaded: nothing is removed
        leave(*other)
        loaded[:] = ["cannot say"]
        self.engine.tidy()
        self.assertEqual(self.generated(), sorted(other))
        # a player that is down has nothing loaded
        self.player.is_running = lambda: False
        self.engine.tidy()
        self.assertEqual(self.generated(), [])
        self.player.is_running = lambda: True
        # the build of the panel does it (pvj/server.py), so an old text does not wait for the first play
        import inspect
        from pvj import server
        self.assertIn("api.shaders.tidy()", inspect.getsource(server.build))
        # a shader that is on keeps its text; Stop removes it
        loaded[:] = []
        self.engine.show("nxlx-aurora.fs")
        (mine,) = self.generated()
        leave("shader-1-7.glsl")
        self.engine.tidy()
        self.assertEqual(self.generated(), [mine])
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual((self.player.source_shader, self.generated()), (None, []))
        # Stop never waits for the engine (its lock is held while the GPU looks at a shader): the tidy steps aside
        leave("shader-1-7.glsl")
        held, done = threading.Event(), threading.Event()

        def hold():
            with self.engine._lock:
                held.set()
                done.wait(5)
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        self.assertTrue(held.wait(5))
        began = time.monotonic()
        self.api.control({"action": "stop"}, None, "t")
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertEqual(self.generated(), ["shader-1-7.glsl"])          # left for the next look
        done.set()
        t.join(5)
        self.engine.tidy()
        self.assertEqual(self.generated(), [])

    def test_nothing_is_offered_until_the_module_is_on_and_the_pi_3_never_gets_it(self):
        self.api.registry.set_enabled("shaders", False)
        self.assertEqual((self.engine.state()["enabled"], self.engine.state()["shaders"]), (False, []))
        for call in (lambda: self.engine.show("nxlx-aurora.fs"), lambda: self.engine.api_play({"id": "nxlx-aurora.fs"}, None, "t"),
                     lambda: self.engine.api_set({"action": "config", "dwell": 60}, None, "t"), self.api.vibes.start):
            with self.assertRaises(ApiError) as c:
                call()
            self.assertEqual(c.exception.status, 409)
        from pvj.modules import Registry
        rows = {m["id"]: m for m in Registry(self.settings, "pi3").list()}
        self.assertEqual((rows["shaders"]["supported"], rows["shaders"]["enabled"], rows["shaders"]["channel"]), (False, False, "beta"))
        for board in ("pi4", "pi5", "x86"):
            self.assertTrue({m["id"]: m for m in Registry(self.settings, board).list()}["shaders"]["supported"])

    def test_a_shader_is_written_for_the_player_and_put_over_the_carrier(self):
        r = self.engine.show("nxlx-aurora.fs", {"speed": 1.5})
        self.assertEqual((r["ok"], r["epoch"]), (True, 1))
        (name,) = self.generated()
        self.assertRegex(name, r"^shader-%d-1\.glsl$" % os.getpid())
        path = os.path.join(self.rundir, name)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o640")
        self.assertEqual(self.player.calls[-2], ("play_source", path, S.carrier_url((1920, 1080))))
        with open(path) as f:
            text = f.read()
        self.assertIn("//!WIDTH 1280\n//!HEIGHT 720\n", text)       # 720 lines by default, in the screen's shape
        self.assertIn("const float speed = 1.5;", text)
        st = self.engine.state()
        self.assertEqual((st["playing"]["id"], st["playing"]["values"]["speed"], st["playing"]["checked"]), ("nxlx-aurora.fs", 1.5, None))
        self.assertEqual(self.api.status({}, None, "t")["player"]["shader"], "nxlx-aurora")
        self.assertIsNone(self.api.status({}, None, "t")["player"]["path"])           # the carrier is not shown as a clip
        self.engine.show("nxlx-tide.fs")
        self.assertEqual(self.generated(), ["shader-%d-2.glsl" % os.getpid()])         # the old file is removed

    def test_files_left_by_an_earlier_run_are_removed_and_other_files_are_not(self):
        for n in ("shader-1-9.glsl", "shader-1-10.glsl.tmp", "mapper-1-1.glsl", "shader-notes.txt"):
            open(os.path.join(self.rundir, n), "w").close()
        self.engine.show("nxlx-aurora.fs")
        self.assertEqual(sorted(os.listdir(self.rundir)), sorted(["mapper-1-1.glsl", "shader-notes.txt", "shader-%d-1.glsl" % os.getpid()]))

    def test_a_shader_the_gpu_refuses_gives_way_to_the_one_before_it(self):
        self.player.vo = "gpu"
        good = self.engine.show("nxlx-aurora.fs")
        self.assertEqual(self.engine.state()["playing"]["checked"], True)              # the player drew a frame with it
        good_file = self.player.source_shader
        FakeTap.lines = REFUSAL
        r = self.engine.show("nxlx-tide.fs")
        self.assertEqual((r["ok"], r["error"], r["showing"]), (False, "line 12: `oops' undeclared", "nxlx-aurora.fs"))
        self.assertEqual(self.player.source_shader, good_file)                         # back on the screen
        self.assertEqual(self.generated(), [os.path.basename(good_file)])              # the refused file is gone
        st = self.engine.state()
        self.assertEqual((st["playing"]["id"], st["error"]["id"], st["error"]["message"]), ("nxlx-aurora.fs", "nxlx-tide.fs", "line 12: `oops' undeclared"))
        self.assertGreater(r["epoch"], good["epoch"])

    def test_a_refused_shader_with_nothing_before_it_leaves_black_and_says_so(self):
        self.player.vo = "gpu"
        FakeTap.lines = REFUSAL
        with self.assertRaises(ApiError) as c:
            self.engine.api_play({"id": "nxlx-tide.fs"}, None, "t")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("`oops' undeclared", c.exception.message)
        self.assertIn("The screen is black", c.exception.message)
        self.assertEqual((self.player.source_shader, self.generated(), self.engine.state()["playing"]), (None, [], None))
        self.assertEqual((self.player.calls[-2][0], self.player.calls[-1]), ("swap_source", ("clear",)))            # black at once, then stopped
        self.assertRegex(os.path.basename(self.player.calls[-2][1]), r"^shader-\d+-\d+\.glsl$")       # a shader that draws black, not the bare carrier
        self.assertIsNone(self.player.path)
        FakeTap.lines = []
        self.engine.api_play({"id": "nxlx-tide.fs"}, None, "t")                        # a later good try clears the error
        self.assertIsNone(self.engine.state()["error"])

    def test_a_pass_that_is_listed_but_took_no_time_is_not_counted_as_drawn(self):
        """mpv lists a refused shader's pass too, with a time of 0; its error lines may come late."""
        self.player.vo, self.player.pass_ns = "gpu", 0
        self.engine.show("nxlx-aurora.fs")
        self.assertIsNone(self.engine.state()["playing"]["checked"])
        self.assertEqual(self.engine._checked, set())                 # so it is watched again next time

    def test_a_shader_already_taken_by_the_gpu_is_not_waited_for_again(self):
        self.player.vo = "gpu"
        taps = []
        self.engine._tap = lambda path: taps.append(path) or FakeTap(path)
        self.engine.show("nxlx-aurora.fs")
        self.engine.show("nxlx-aurora.fs", {"speed": 0.5})
        self.assertEqual(len(taps), 1)
        self.assertEqual(self.engine.state()["playing"]["checked"], True)

    def test_show_does_nothing_when_something_else_was_played_meanwhile(self):
        first = self.engine.show("nxlx-aurora.fs")
        self.player.play(["/media/clip.mp4"])
        self.assertIsNone(self.engine.show("nxlx-tide.fs", epoch=first["epoch"]))
        self.assertEqual((self.player.path, self.player.source_shader), ("/media/clip.mp4", None))
        self.assertIsNone(self.engine.state()["playing"])
        self.assertNotIn("shader", self.api.status({}, None, "t")["player"])

    def test_a_player_that_is_down_is_reported_and_leaves_no_file(self):
        self.player.down = True
        with self.assertRaises(ApiError) as c:
            self.engine.show("nxlx-aurora.fs")
        self.assertEqual((c.exception.status, self.generated()), (503, []))

    def test_switching_the_module_off_takes_the_shader_off_the_screen(self):
        self.engine.show("nxlx-aurora.fs")
        self.api.set_module("shaders", {"enabled": False}, None, "t")
        self.assertEqual((self.player.calls[-1], self.generated()), (("clear",), []))

    def test_upload_checks_the_file_before_storing_it(self):
        r = self.engine.api_set({"action": "upload", "name": "My shader 1.fs", "source": GOOD}, None, "t")
        mine = [s for s in r["shaders"] if s["source"] == "uploaded"]
        self.assertEqual([(s["id"], s["description"], s["vibes"]) for s in mine], [("My shader 1.fs", "mine", False)])    # in the library only
        stored = os.path.join(self.tmp, "shaders", "My shader 1.fs")
        with open(stored) as f:
            self.assertEqual(f.read(), GOOD)
        self.assertEqual(self.engine.show("My shader 1.fs", {"k": 3})["ok"], True)
        bad = [("evil.fs", GOOD + "//!HOOK OUTPUT\n", 422), ("multi.fs", isf(PASSES=[{}, {}]), 422), ("big.fs", GOOD + " " * S.MAX_SOURCE, 422),
               ("notisf.fs", "void main() {}", 422), ("x.fs", 5, 400), ("x.fs", None, 400), ("../x.fs", GOOD, 400), ("a/b.fs", GOOD, 400),
               (".hidden.fs", GOOD, 400), ("x.fs\n", GOOD, 400), ("x.glsl", GOOD, 400), ("x.fs.exe", GOOD, 400), ("café.fs", GOOD, 400),
               ("x" * 80 + ".fs", GOOD, 400), (None, GOOD, 400), ("nxlx-aurora.fs", GOOD, 409), ("My shader 1.fs", GOOD, 409)]
        for name, source, status in bad:
            with self.assertRaises(ApiError, msg=repr(name)) as c:
                self.engine.api_set({"action": "upload", "name": name, "source": source}, None, "t")
            self.assertEqual(c.exception.status, status, (name, c.exception.message))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "shaders")), ["My shader 1.fs"])     # nothing else was stored, no temp files
        self.engine.api_set({"action": "upload", "name": "My shader 1.fs", "source": GOOD.replace("mine", "newer"), "replace": True}, None, "t")
        self.assertEqual([s["description"] for s in self.engine.library() if s["source"] == "uploaded"], ["newer"])
        with self.assertRaises(ApiError):
            self.engine.api_set({"action": "upload", "name": "y.fs", "source": GOOD, "replace": "yes"}, None, "t")

    def test_upload_never_writes_through_a_link_and_is_capped(self):
        folder = os.path.join(self.tmp, "shaders")
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, folder)
        with self.assertRaises(ApiError) as c:
            self.engine.upload("x.fs", GOOD)
        self.assertEqual((c.exception.status, os.listdir(outside)), (500, []))
        self.assertEqual([s for s in self.engine.library() if s["source"] == "uploaded"], [])
        os.unlink(folder)
        os.makedirs(folder)
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, os.path.join(folder, "x.fs"))
        for replace in (False, True):
            with self.assertRaises(ApiError):
                self.engine.upload("x.fs", GOOD, replace)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        with self.assertRaises(ApiError):
            self.engine.show("x.fs")                                  # a link in the folder is never read either
        os.unlink(os.path.join(folder, "x.fs"))
        for i in range(S.MAX_UPLOADS):
            self.engine.upload("s%d.fs" % i, GOOD)
        with self.assertRaises(ApiError) as c:
            self.engine.upload("one-more.fs", GOOD)
        self.assertIn("at most", c.exception.message)

    def test_delete_only_uploads_and_only_by_name(self):
        self.engine.upload("mine.fs", GOOD)
        self.engine.api_set({"action": "vibes", "id": "mine.fs", "on": False}, None, "t")
        for sid, status in (("nxlx-aurora.fs", 409), ("../settings.json", 400), ("nope.fs", 404), (None, 400), ("mine.fs\n", 400)):
            with self.assertRaises(ApiError) as c:
                self.engine.api_set({"action": "delete", "id": sid}, None, "t")
            self.assertEqual(c.exception.status, status, sid)
        self.engine.api_set({"action": "delete", "id": "mine.fs"}, None, "t")
        self.assertEqual(os.listdir(os.path.join(self.tmp, "shaders")), [])
        self.assertEqual(self.settings.data["shaders"]["disabled"], [])
        self.assertTrue(os.path.exists(os.path.join(S.BUNDLED_DIR, "nxlx-aurora.fs")))

    # -- packs: third-party shaders beside the project's own --
    def test_the_third_party_pack_is_in_the_library_with_its_credit_but_not_in_vibes(self):
        rows = self.engine.library()
        self.assertEqual(len({s["id"] for s in rows}), len(rows))                 # ids are unique across packs
        own = [s for s in rows if s["pack"] == "nxlx"]
        pack = [s for s in rows if s["pack"] == "isf-files"]
        self.assertEqual((len(own), [s["id"] for s in pack]), (BUNDLED, sorted(PACKED, key=str.lower)))
        self.assertEqual(sorted(n for n in os.listdir(ISF_PACK) if n.endswith(".fs")), PACKED)
        self.assertEqual([s["id"] for s in rows[:BUNDLED]], [s["id"] for s in own])    # the project's own come first
        for s in pack:
            self.assertEqual((s["source"], s["error"], s["vibes"]), ("bundled", None, False), s["id"])
            self.assertEqual(s["name"], s["id"][:-3])
        heavy = {"nxlx-%s.fs" % x for x in HEAVY}
        self.assertTrue(all(s["vibes"] == ("Performance" not in s["categories"] and s["id"] not in heavy) for s in own))    # not the ones to perform with, not the heavy two
        self.assertEqual(len(self.engine.vibes_ids()), IN_VIBES)                  # the pack adds nothing to the rotation
        self.assertTrue(any("VIDVOX" in s["credit"].upper() for s in pack))
        self.assertEqual(self.engine.packs()[0], ("nxlx", S.BUNDLED_DIR))
        self.assertEqual([p for p, _ in self.engine.packs()], ["nxlx", "isf-files"])
        self.engine.upload("mine.fs", GOOD)
        mine = [s for s in self.engine.library() if s["id"] == "mine.fs"][0]
        self.assertEqual((mine["pack"], mine["source"], mine["vibes"]), ("uploads", "uploaded", False))      # in the library only

    def test_a_pack_shader_joins_vibes_only_when_the_owner_puts_it_in(self):
        sid = PACKED[0]
        own = [x["id"] for x in self.engine.library() if x["pack"] == "nxlx" and x["vibes"]]
        st = self.engine.api_set({"action": "vibes", "id": sid, "on": True}, None, "t")
        self.assertTrue([x for x in st["shaders"] if x["id"] == sid][0]["vibes"])

        def in_set():       # the rotation is a set since the shader engine (shaderlive.py): the switch writes the active set
            return [r["id"] for r in self.settings.data["shaders"]["sets"][0]["shaders"]]
        self.assertEqual(in_set(), own + [sid])
        self.assertEqual(sorted(self.engine.vibes_ids()), sorted(own + [sid]))
        self.engine.api_set({"action": "vibes", "id": sid, "on": True}, None, "t")                # twice is once
        self.assertEqual(in_set().count(sid), 1)
        self.engine.api_set({"action": "vibes", "id": "nxlx-tide.fs", "on": False}, None, "t")    # the project's own: the same switch
        self.assertEqual((sid in in_set(), "nxlx-tide.fs" in in_set()), (True, False))
        self.engine.api_set({"action": "config", "dwell": 60}, None, "t")                         # another change keeps the list
        self.assertIn(sid, in_set())
        self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        self.assertNotIn(sid, in_set())
        self.assertNotIn(sid, self.engine.vibes_ids())
        # settings saved by the pack's first version: its "included" list is read for the first set while no set is saved
        self.settings.data["shaders"] = {"included": ["../x", 5, sid, "gone.fs"]}                 # edited by hand
        self.assertEqual(self.engine.config()["included"], [sid, "gone.fs"])
        self.assertEqual(len(self.engine.vibes_ids()), IN_VIBES + 1)              # a name that is no file adds nothing
        self.engine.api_set({"action": "config", "dwell": 61}, None, "t")         # the first save writes the list as the set Ambient
        self.assertIn(sid, in_set())
        # the old settings, saved before packs existed, read as they did
        self.settings.data["shaders"] = {"dwell": 45, "vary": False, "height": 540, "disabled": []}
        self.assertEqual(self.engine.config(), {"dwell": 45, "vary": False, "height": 540, "disabled": []})

    def test_a_pack_shader_plays_and_can_neither_be_deleted_nor_have_its_name_taken(self):
        sid = PACKED[0]
        r = self.engine.show(sid)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.engine.state()["playing"]["id"], sid)
        with self.assertRaises(ApiError) as c:
            self.engine.api_set({"action": "delete", "id": sid}, None, "t")
        self.assertEqual(c.exception.status, 409)
        self.assertTrue(os.path.exists(os.path.join(ISF_PACK, sid)))
        for replace in (False, True):
            with self.assertRaises(ApiError) as c:
                self.engine.upload(sid, GOOD, replace)
            self.assertEqual((c.exception.status, "bundled shader" in c.exception.message), (409, True))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "shaders", sid)))
        # a file of that name that was uploaded before the pack came: the pack's file is the one listed and played
        os.makedirs(os.path.join(self.tmp, "shaders"), exist_ok=True)
        with open(os.path.join(self.tmp, "shaders", sid), "w") as f:
            f.write(GOOD)
        rows = [s for s in self.engine.library() if s["id"] == sid]
        self.assertEqual([(s["pack"], s["source"], s.get("hides_upload")) for s in rows], [("isf-files", "bundled", True)])
        self.assertEqual(self.engine._path(sid), (os.path.join(ISF_PACK, sid), "bundled"))
        self.assertNotIn("hides_upload", [s for s in self.engine.library() if s["id"] == PACKED[1]][0])

    def test_an_upload_hidden_behind_a_pack_file_takes_no_slot_and_can_be_removed(self):
        """An upload made before the pack arrived, under a name the pack now has: it was hidden, could not be
        deleted or replaced (409), still took one of the upload slots, and its old "not in Vibes" entry waited to
        come back with it."""
        sid, folder = PACKED[0], os.path.join(self.tmp, "shaders")
        os.makedirs(folder)
        with open(os.path.join(folder, sid), "w") as f:
            f.write(GOOD)
        self.settings.data["shaders"] = {"disabled": [sid, "nxlx-tide.fs"]}        # switched out when it was an upload
        for i in range(S.MAX_UPLOADS):                                             # all 64 slots are still free
            self.engine.upload("s%d.fs" % i, GOOD)
        with self.assertRaises(ApiError) as c:
            self.engine.upload("one-more.fs", GOOD)
        self.assertIn("at most", c.exception.message)
        self.assertEqual(len([s for s in self.engine.library() if s["pack"] == "uploads"]), S.MAX_UPLOADS)
        self.engine.api_set({"action": "delete", "id": sid}, None, "t")            # removes the hidden upload
        self.assertFalse(os.path.exists(os.path.join(folder, sid)))
        self.assertTrue(os.path.exists(os.path.join(ISF_PACK, sid)))               # never the pack's file
        self.assertEqual(self.settings.data["shaders"]["disabled"], ["nxlx-tide.fs"])      # the stale entry went with it
        row = [s for s in self.engine.library() if s["id"] == sid][0]
        self.assertEqual((row["pack"], row.get("hides_upload")), ("isf-files", None))
        with self.assertRaises(ApiError) as c:                                     # nothing hidden any more: as before
            self.engine.api_set({"action": "delete", "id": sid}, None, "t")
        self.assertEqual(c.exception.status, 409)
        # a file put by hand under the name of one of the project's own: removing it leaves that shader's switch alone
        with open(os.path.join(folder, "nxlx-tide.fs"), "w") as f:
            f.write(GOOD)
        self.engine.api_set({"action": "delete", "id": "nxlx-tide.fs"}, None, "t")
        self.assertFalse(os.path.exists(os.path.join(folder, "nxlx-tide.fs")))
        self.assertTrue(os.path.exists(os.path.join(S.BUNDLED_DIR, "nxlx-tide.fs")))
        self.assertEqual(self.settings.data["shaders"]["disabled"], ["nxlx-tide.fs"])
        os.symlink(os.path.join(ISF_PACK, sid), os.path.join(folder, sid))         # a link is never followed or removed
        with self.assertRaises(ApiError) as c:
            self.engine.api_set({"action": "delete", "id": sid}, None, "t")
        self.assertEqual(c.exception.status, 409)
        self.assertTrue(os.path.lexists(os.path.join(folder, sid)))

    def test_a_pack_is_found_however_many_of_the_projects_own_files_sort_before_it(self):
        """The folder listing was cut at 64 names before the folders were picked out: with 64 files sorting before
        `isf-files` the pack vanished, and an upload under one of its names was then stored."""
        root = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(root, "the-pack"))
        for i in range(70):
            with open(os.path.join(root, "a-own-%02d.fs" % i), "w") as f:
                f.write(GOOD)
        with open(os.path.join(root, "the-pack", "theirs.fs"), "w") as f:
            f.write(GOOD)
        for i in range(S.MAX_PACKS + 3):
            os.makedirs(os.path.join(root, "z-pack-%02d" % i))
        self.engine.bundled_dir = root
        packs = [p for p, _ in self.engine.packs()]
        self.assertEqual(packs[:2], ["nxlx", "the-pack"])
        self.assertEqual(len(packs), 1 + S.MAX_PACKS)                              # the cap counts packs, not files
        rows = {s["id"]: s for s in self.engine.library()}
        self.assertEqual((len(rows), rows["theirs.fs"]["pack"]), (71, "the-pack"))
        with self.assertRaises(ApiError) as c:
            self.engine.upload("theirs.fs", GOOD)
        self.assertEqual(c.exception.status, 409)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "shaders", "theirs.fs")))

    def test_real_isf_files_from_another_program_go_through_the_upload_route(self):
        """The way in for everything that is not bundled: a full-access device uploads the .fs file. The pack's files
        are real files from Vidvox's collection, unchanged, so they stand for it here: each one uploads under a name
        of its own, within the size and input limits, and is listed without an error."""
        texts = {}
        for n, name in enumerate(PACKED):
            with open(os.path.join(ISF_PACK, name), "rb") as f:
                data = f.read()
            texts[name] = data.decode("utf-8")
            self.assertLessEqual(len(data), S.MAX_SOURCE, name)
            out = self.engine.api_set({"action": "upload", "name": "mine-%d.fs" % n, "source": data.decode("utf-8")}, None, "t")
            row = [s for s in out["shaders"] if s["id"] == "mine-%d.fs" % n][0]
            self.assertEqual((row["error"], row["pack"], row["vibes"]), (None, "uploads", False), name)
            self.assertLessEqual(len(row["inputs"]), S.MAX_INPUTS)
        # one of the three things real files do that the first translator refused is present in the files that stand
        # in: text that is not ASCII in a comment (`out_color` and an input called `color` are in upstream files that
        # are not bundled; each has its own test above)
        self.assertTrue(any(not t.isascii() for t in texts.values()))

    def test_only_plain_folders_with_a_plain_name_are_packs_and_none_can_stand_in_for_the_projects_own(self):
        root = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(root, "a-pack"))
        os.makedirs(os.path.join(root, "Bad Name"))
        os.makedirs(os.path.join(root, "uploads"))
        os.makedirs(os.path.join(root, "nxlx"))
        os.makedirs(os.path.join(self.tmp, "elsewhere"))
        os.symlink(os.path.join(self.tmp, "elsewhere"), os.path.join(root, "linked"))
        for folder, name in ((root, "own.fs"), ("a-pack", "own.fs"), ("a-pack", "theirs.fs"), ("Bad Name", "x.fs"), ("uploads", "y.fs"),
                             ("nxlx", "z.fs"), ("linked", "w.fs")):
            with open(os.path.join(root, folder, name), "w") as f:
                f.write(GOOD.replace("0.5", "0.25") if folder == "a-pack" else GOOD)
        os.symlink(os.path.join(root, "own.fs"), os.path.join(root, "a-pack", "link.fs"))
        self.engine.bundled_dir = root
        self.assertEqual(self.engine.packs(), [("nxlx", root), ("a-pack", os.path.join(root, "a-pack"))])
        rows = {s["id"]: s for s in self.engine.library()}
        self.assertEqual(sorted(rows), ["own.fs", "theirs.fs"])
        self.assertEqual((rows["own.fs"]["pack"], rows["theirs.fs"]["pack"]), ("nxlx", "a-pack"))
        self.assertEqual(self.engine._path("own.fs"), (os.path.join(root, "own.fs"), "bundled"))
        self.assertEqual((rows["own.fs"]["vibes"], rows["theirs.fs"]["vibes"]), (True, False))
        for sid in ("x.fs", "y.fs", "z.fs", "w.fs", "link.fs", "a-pack/theirs.fs", "../own.fs"):
            with self.assertRaises(ApiError, msg=sid):
                self.engine._path(sid)

    def test_a_broken_file_in_the_folder_is_listed_with_its_reason_and_left_out_of_vibes(self):
        os.makedirs(os.path.join(self.tmp, "shaders"))
        with open(os.path.join(self.tmp, "shaders", "broken.fs"), "w") as f:
            f.write("not a shader")
        row = [s for s in self.engine.library() if s["id"] == "broken.fs"][0]
        self.assertEqual((row["vibes"], "not an ISF file" in row["error"]), (False, True))
        self.assertNotIn("broken.fs", self.engine.vibes_ids())
        with self.assertRaises(ApiError) as c:
            self.engine.show("broken.fs")
        self.assertEqual(c.exception.status, 422)

    def test_a_file_put_in_the_folder_by_hand_cannot_take_the_list_down(self):
        """GET /api/shaders answered 500 and Vibes could not start for anyone."""
        folder = os.path.join(self.tmp, "shaders")
        os.makedirs(folder)
        ok = "\nvoid main() { gl_FragColor = vec4(1.0); }"
        for n, head in (("nan.fs", '{"INPUTS": [{"NAME": "a", "TYPE": "long", "DEFAULT": NaN}]}'),
                        ("huge.fs", '{"INPUTS": [{"NAME": "a", "TYPE": "float", "MIN": %s}]}' % ("9" * 400)),
                        ("digits.fs", '{"INPUTS": [{"NAME": "a", "TYPE": "float", "MIN": %s}]}' % ("9" * 6000))):
            with open(os.path.join(folder, n), "w") as f:
                f.write("/*" + head + "*/" + ok)
        real = S.parse

        def trips(data):
            if b"digits" in data or len(data) > 6000:
                raise RuntimeError("something unforeseen")
            return real(data)
        S.parse = trips
        self.addCleanup(setattr, S, "parse", real)
        rows = {s["id"]: s for s in self.engine.library()}
        self.assertEqual(len(rows), BUNDLED + 3 + len(PACKED))      # the project's own, the third-party pack, these three
        for n in ("nan.fs", "huge.fs", "digits.fs"):
            self.assertTrue(rows[n]["error"] and not rows[n]["vibes"], rows[n])
        self.assertEqual(len(self.engine.vibes_ids()), IN_VIBES)
        self.assertEqual(self.api.handle("GET", "/api/shaders", {}, {"id": "t", "role": "view"}, "t")[0], 200)
        self.api.vibes._use_thread = False
        self.assertTrue(self.api.vibes.start()["running"])
        st, body = self.api.handle("POST", "/api/shaders", {"action": "upload", "name": "up.fs", "source": '/*{"INPUTS": [{"NAME": "a", "TYPE": "long", "DEFAULT": NaN}]}*/' + ok},
                                   {"id": "t", "role": "full"}, "t")
        self.assertEqual(st, 422, body)

    def test_reading_what_is_on_screen_never_changes_it(self):
        """Every status poll called on_screen without the lock, and it could write "nothing is playing" just after a
        show had set it, which ended Vibes."""
        self.engine.show("nxlx-aurora.fs")
        was = self.engine.playing
        self.player.path = "/media/other.mp4"
        self.assertIsNone(self.engine.on_screen())
        self.assertIs(self.engine.playing, was)
        self.player.path = was["carrier"]
        self.assertIs(self.engine.on_screen(), was)

    def test_off_never_stops_a_clip_that_was_started_meanwhile(self):
        r = self.engine.show("nxlx-aurora.fs")
        self.player.play(["/media/clip.mp4"])
        self.engine.off(r["epoch"])
        self.assertEqual((self.player.path, self.player.calls[-1][0]), ("/media/clip.mp4", "play"))
        r = self.engine.show("nxlx-aurora.fs")
        other = self.engine.show("nxlx-tide.fs")
        self.engine.off(r["epoch"])                                   # an old epoch: the newer shader and its file stay
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-tide.fs")
        self.assertTrue(os.path.exists(self.player.source_shader))
        self.engine.off(other["epoch"])
        self.assertEqual((self.player.path, self.generated()), (None, []))

    def test_settings_need_no_migration_and_are_checked(self):
        from pvj.settings import MIGRATIONS
        self.assertFalse([m for m in MIGRATIONS.values() if "shader" in (m.__doc__ or "").lower()])     # no settings migration for shaders
        self.assertNotIn("shaders", self.settings.data)               # nothing is written until something changes
        self.assertEqual(self.engine.state()["config"], {"dwell": 180, "vary": True, "height": 720, "guard": True, "clock": "carrier", "faster": False})
        r = self.engine.api_set({"action": "config", "dwell": 45, "vary": False, "height": 540}, None, "t")
        self.assertEqual(r["config"], {"dwell": 45, "vary": False, "height": 540, "guard": True, "clock": "carrier", "faster": False})
        self.assertEqual((r["render"]["width"], r["render"]["height"]), (960, 540))
        self.assertEqual(self.settings.data["shaders"], {"dwell": 45, "vary": False, "height": 540, "disabled": [], "v": 2})
        for bad in ({"dwell": 5}, {"dwell": 99999}, {"dwell": "60"}, {"dwell": True}, {"dwell": float("nan")}, {"vary": 1}, {"height": 721}, {"height": True}):
            with self.assertRaises(ApiError, msg=bad):
                self.engine.api_set(dict({"action": "config"}, **bad), None, "t")
        with self.assertRaises(ApiError):
            self.engine.api_set({"action": "format-disk"}, None, "t")
        self.settings.data["shaders"] = {"dwell": "soon", "vary": None, "height": 9, "disabled": ["../x", 5, "ok.fs"]}   # edited by hand
        self.assertEqual(self.engine.config(), {"dwell": 180, "vary": True, "height": 720, "disabled": ["ok.fs"]})
        self.settings.data["shaders"] = "nonsense"
        self.assertEqual(self.engine.config(), dict(S.default_config(), height=720))      # the test box is an x86


class LenientUploadTest(Base):
    def test_an_upload_with_credits_before_its_header_is_stored_byte_for_byte(self):
        """The credit lines are the author's: the stored file is the upload's own bytes, with its comments, its
        Windows line ends and its text that is not ASCII. Only the player's text leaves them out."""
        head = {"DESCRIPTION": "with credits", "CREDIT": "Ana Example", "INPUTS": [
            {"NAME": "time", "TYPE": "float", "MIN": 0, "MAX": 2, "DEFAULT": 0.5}, {"NAME": "lit", "TYPE": "bool", "DEFAULT": 1}]}
        source = (LEAD + "/*" + json.dumps(head, indent=1) + "*/\n// more\nvoid main() { gl_FragColor = vec4(lit ? time : fract(TIME)); }\n").replace("\n", "\r\n")
        out = self.engine.api_set({"action": "upload", "name": "credits.fs", "source": source}, None, "t")
        with open(os.path.join(self.tmp, "shaders", "credits.fs"), "rb") as f:
            stored = f.read()
        self.assertEqual(stored, source.encode("utf-8"))
        self.assertTrue(stored.startswith(b"// Slow Lanterns, by Ana Example (a made-up credit) \xe2\x80\x93 caf\xc3\xa9\r\n"))
        row = [s for s in out["shaders"] if s["id"] == "credits.fs"][0]
        self.assertEqual((row["error"], row["description"], row["credit"]), (None, "with credits", "Ana Example"))
        # the panel, and so every mapping and preset, sees the inputs under the file's own names
        self.assertEqual([(i["name"], i["type"], i["default"]) for i in row["inputs"]], [("time", "float", 0.5), ("lit", "bool", True)])
        r = self.engine.show("credits.fs", {"time": 1.25, "lit": False})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.engine.playing["values"], {"time": 1.25, "lit": False})
        with open(os.path.join(self.rundir, self.generated()[-1])) as f:
            text = f.read()
        self.assertIn("const float pvj_in_time = 1.25;\nconst bool lit = false;", text)
        self.assertIn("vec4(lit ? pvj_in_time : fract(TIME))", text)
        for gone in ("Ana", "Lanterns", "not me", "\r", "more"):
            self.assertNotIn(gone, text)
        self.assertTrue(text.isascii())
        # what is refused is not stored, with comments before the header as without
        for name, text in (("a.fs", LEAD + "uniform float u;\n" + GOOD), ("b.fs", "/* credit */\n" + GOOD + "//!HOOK OUTPUT\n"),
                           ("c.fs", "// " + "x" * S.MAX_LEADING + "\n" + GOOD), ("d.fs", "/* never closed\n" + GOOD.replace("*/", "")),
                           ("e.fs", LEAD + GOOD.replace("void main", "#version 100\nvoid main"))):
            with self.assertRaises(ApiError, msg=name) as c:
                self.engine.api_set({"action": "upload", "name": name, "source": text}, None, "t")
            self.assertEqual(c.exception.status, 422, name)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "shaders")), ["credits.fs"])


class FakeFader:
    def __init__(self, log):
        self.log = log

    def ramp(self, start, end, seconds, then=None):
        self.log.append(("ramp", start, end, seconds))

    def cancel(self):
        self.log.append(("cancel",))


class VibesTest(Base):
    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        self.events = []
        self.during_dip = None
        self.api.fader = FakeFader(self.events)
        self.settings.data["mix"] = {"transition": "cut", "duration": 2.0}
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=self.sleep,
                                              rng=random.Random(5), thread=False, log=lambda *_: None)

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))
        self.now[0] += seconds
        if self.during_dip:
            self.during_dip()

    def shown(self):
        return [os.path.basename(c[1]) for c in self.player.calls if c[0] == "play_source"]

    def ids(self):
        out = []
        for c in self.player.calls:
            if c[0] == "play_source":
                out.append(self.engine_ids[c[1]])
        return out

    def run_rounds(self, n):
        """Start and let `n` shaders come up; returns their ids in order."""
        seen = []
        self.vibes.start()
        for _ in range(n):
            self.assertTrue(self.vibes.tick())
            seen.append(self.vibes.current)
            self.now[0] += self.engine.config()["dwell"]
        return seen

    def test_start_returns_at_once_and_the_first_tick_puts_a_shader_on(self):
        st = self.vibes.api_vibes({"on": True}, None, "t")
        self.assertEqual((st["running"], st["current"], self.shown()), (True, None, []))
        self.assertTrue(self.vibes.tick())
        self.assertEqual(len(self.shown()), 1)
        self.assertIn(self.vibes.current, self.engine.vibes_ids())
        status = self.api.status({}, None, "t")["player"]
        self.assertEqual((status["vibes"], status["shader"]), (True, self.vibes.current[:-3]))
        self.assertEqual(self.vibes.status()["next_in"], 180)
        self.assertEqual([e[0] for e in self.events if e[0] in ("ramp", "sleep")], [])      # nothing was playing: no dip

    def test_each_shader_stays_for_the_dwell_time_then_dips_to_black_for_the_next(self):
        self.vibes.start()
        self.vibes.tick()
        first = self.vibes.current
        del self.events[:]
        for _ in range(179):
            self.now[0] += 1
            self.assertFalse(self.vibes.tick())
        self.assertEqual((len(self.shown()), self.events), (1, []))
        self.now[0] += 1
        self.assertTrue(self.vibes.tick())
        self.assertNotEqual(self.vibes.current, first)
        # down over half the Mix duration, the change in the dark, up again
        del self.player.calls[:self.player.calls.index(("opacity", 242))]
        levels = [c[1] for c in self.player.calls if c[0] == "opacity"]
        self.assertEqual(len(levels), 40)                              # 20 steps down over half the Mix duration, 20 up
        self.assertEqual(levels[:20], sorted(levels[:20], reverse=True))
        self.assertEqual(levels[20:], sorted(levels[20:]))
        self.assertEqual((levels[0], levels[19], levels[20], levels[39]), (242, 0, 13, 255))
        self.assertEqual([e for e in self.events if e[0] != "sleep"], [])  # the panel's Fader is never touched (see the review)
        self.assertAlmostEqual(sum(e[1] for e in self.events), 2.0)
        dark = [c[0] for c in self.player.calls].index("play_source")
        self.assertEqual(self.player.calls[dark - 1], ("opacity", 0))  # the change happens in the dark
        self.assertEqual(self.vibes.status()["rounds"], 2)

    def test_the_rotation_is_shuffled_and_shows_every_enabled_shader_before_any_again(self):
        enabled = self.engine.vibes_ids()
        n = IN_VIBES
        self.assertEqual(len(enabled), n)
        self.assertEqual(sorted(enabled), sorted("nxlx-%s.fs" % x for x in ROTATION))      # no performance shader, not the heavy two
        seen = self.run_rounds(3 * n)
        for k in range(0, 3 * n, n):
            self.assertEqual(sorted(seen[k:k + n]), sorted(enabled))
        self.assertNotEqual(seen[:n], sorted(enabled))                 # shuffled, not in name order
        self.assertNotEqual(seen[:n], seen[n:2 * n])                   # and shuffled again each time round
        for a, b in zip(seen, seen[1:]):
            self.assertNotEqual(a, b)                                  # never the same one twice in a row

    def test_only_shaders_switched_on_for_vibes_are_picked(self):
        for sid in self.engine.vibes_ids():
            if sid not in ("nxlx-aurora.fs", "nxlx-ember.fs"):
                self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        self.engine.upload("mine.fs", GOOD)
        self.assertNotIn("mine.fs", self.engine.vibes_ids())           # an upload starts in the library only
        self.engine.api_set({"action": "vibes", "id": "mine.fs", "on": True}, None, "t")
        allowed = {"nxlx-aurora.fs", "nxlx-ember.fs", "mine.fs"}
        self.assertEqual(set(self.engine.vibes_ids()), allowed)
        self.assertEqual(set(self.run_rounds(9)), allowed)
        for sid in allowed:
            self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        with self.assertRaises(ApiError) as c:
            self.vibes.start()
        self.assertEqual(c.exception.status, 409)

    def test_a_performance_shader_is_out_of_the_rotation_until_it_is_put_in(self):
        """The strong, rhythmic ones are for playing by hand: a room's one-tap ambience never shows one unless somebody
        chose it. They are kept in "included", apart from "disabled", so a settings file from before stays valid."""
        rows = {s["id"]: s for s in self.engine.library()}
        for name in PERFORMANCE:
            self.assertEqual((rows["nxlx-%s.fs" % name]["vibes"], "Performance" in rows["nxlx-%s.fs" % name]["categories"]), (False, True), name)
        for name in FIRST_TEN + AMBIENT:
            self.assertEqual(rows["nxlx-%s.fs" % name]["vibes"], name not in HEAVY, name)       # all but the two heavy ones (measured)
        self.assertNotIn("included", self.engine.config())
        sid = "nxlx-%s.fs" % PERFORMANCE[0]
        self.engine.show(sid)                                          # playing one by hand needs no switch
        self.assertEqual(self.engine.state()["playing"]["id"], sid)
        # since the shader engine the rotation is a set: the box has two from the start, Ambient (active) and Show
        sets = self.engine.state()["sets"]
        self.assertEqual([(e["name"], sorted(r["id"] for r in e["shaders"])) for e in sets],
                         [("Ambient", sorted("nxlx-%s.fs" % n for n in ROTATION)), ("Show", sorted("nxlx-%s.fs" % n for n in PERFORMANCE))])
        self.assertEqual(self.engine.state()["active"], sets[0]["id"])
        self.engine.api_set({"action": "vibes", "id": sid, "on": True}, None, "t")
        self.engine.api_set({"action": "vibes", "id": sid, "on": True}, None, "t")
        ambient = self.settings.data["shaders"]["sets"][0]
        self.assertEqual([r["id"] for r in ambient["shaders"]].count(sid), 1)
        self.assertIn(sid, self.engine.vibes_ids())
        self.assertEqual(len(self.engine.vibes_ids()), IN_VIBES + 1)
        self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        self.assertNotIn(sid, self.engine.vibes_ids())
        self.engine.api_set({"action": "vibes", "id": "nxlx-dusk.fs", "on": False}, None, "t")     # an ambient one, the same switch
        self.assertNotIn("nxlx-dusk.fs", self.engine.vibes_ids())
        # settings from before the sets: "included" still puts a performance shader into the first set
        self.settings.data["shaders"] = {"included": ["../x", 5, sid, "nxlx-aurora.fs"]}            # edited by hand
        self.assertEqual(self.engine.config()["included"], [sid, "nxlx-aurora.fs"])
        self.assertIn(sid, self.engine.vibes_ids())
        self.assertEqual(len(self.engine.vibes_ids()), IN_VIBES + 1)                               # naming an ambient one changes nothing
        # the rule is for the bundled set: an upload starts in the library only, whatever it calls itself
        self.settings.data.pop("shaders")
        self.engine.upload("loud.fs", "/*{\"CATEGORIES\": [\"Performance\"]}*/\nvoid main() { gl_FragColor = vec4(1.0); }\n")
        self.assertNotIn("loud.fs", self.engine.vibes_ids())
        self.engine.api_set({"action": "vibes", "id": "loud.fs", "on": True}, None, "t")
        self.assertIn("loud.fs", self.engine.vibes_ids())

    def test_each_round_varies_the_numbers_inside_min_and_max_and_shifts_the_palette(self):
        # Two shaders stay in, and only aurora's rounds are read: a rotation of one no longer loads its shader again
        # each round (it stays on without a dip; tests/test_shader_engine.py), so the second one is here on purpose.
        for sid in self.engine.vibes_ids():
            if sid not in ("nxlx-aurora.fs", "nxlx-silk.fs"):
                self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        texts = []
        self.vibes.start()
        while len(texts) < 25:
            self.assertTrue(self.vibes.tick())
            if self.vibes.current == "nxlx-aurora.fs":
                with open(self.player.source_shader) as f:
                    texts.append(f.read())
            self.now[0] += 180
        speeds = [float(re.search(r"const float speed = ([0-9.e-]+);", t).group(1)) for t in texts]
        self.assertTrue(all(0.2 <= v <= 2.0 for v in speeds), speeds)
        self.assertGreater(len(set(speeds)), 20)
        self.assertGreater(len({re.search(r"mat3\(([^)]*)\)", t).group(1) for t in texts}), 20)      # the palette shift
        self.assertGreater(len({re.search(r"/ 30\.0 \+ ([0-9.]+);", t).group(1) for t in texts}), 20)   # where time starts
        self.engine.api_set({"action": "config", "vary": False}, None, "t")
        self.assertTrue(self.vibes.tick())
        if self.vibes.current != "nxlx-aurora.fs":
            self.now[0] += 180
            self.assertTrue(self.vibes.tick())
        self.assertEqual(self.vibes.current, "nxlx-aurora.fs")
        with open(self.player.source_shader) as f:
            plain = f.read()
        self.assertIn("const float speed = 1.0;", plain)
        self.assertNotIn("mat3(", plain)

    def test_vary_keeps_every_value_between_min_and_max(self):
        inputs = [{"name": "a", "type": "float", "min": -3.0, "max": 7.0, "default": 7.0}, {"name": "b", "type": "float", "min": 2.0, "max": 2.0, "default": 2.0},
                  {"name": "c", "type": "bool", "default": True}]
        rng = random.Random(1)
        for _ in range(500):
            v = V.vary(inputs, rng)
            self.assertEqual(list(v), ["a"])
            self.assertTrue(-3.0 <= v["a"] <= 7.0)

    def test_playing_anything_else_ends_it_and_it_never_takes_the_screen_back(self):
        self.vibes.start()
        self.vibes.tick()
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertFalse(self.vibes.tick())
        self.assertFalse(self.vibes.running)
        self.assertIn("something else", self.vibes.status()["last"]["message"])
        for _ in range(5):
            self.now[0] += 500
            self.assertFalse(self.vibes.tick())
        self.assertEqual(len(self.shown()), 1)
        self.assertTrue(self.player.path.endswith("a.mp4"))
        self.assertNotIn("vibes", self.api.status({}, None, "t")["player"])

    def test_the_stop_button_ends_it(self):
        self.vibes.start()
        self.vibes.tick()
        self.api.control({"action": "stop"}, None, "t")
        self.now[0] += 500
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.running, len(self.shown()), self.player.path), (False, 1, None))

    def test_a_clip_started_during_the_dip_keeps_the_screen(self):
        self.vibes.start()
        self.vibes.tick()
        self.now[0] += 180
        self.during_dip = lambda: self.player.play(["/media/clip.mp4"])
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.running, len(self.shown()), self.player.path), (False, 1, "/media/clip.mp4"))

    def test_a_clip_started_between_start_and_the_first_shader_keeps_the_screen(self):
        self.vibes.start()
        self.player.play(["/media/clip.mp4"])
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.running, self.shown(), self.player.path), (False, [], "/media/clip.mp4"))

    def test_starting_over_a_clip_dips_first(self):
        self.player.play(["/media/clip.mp4"])
        self.vibes.start()
        self.assertTrue(self.vibes.tick())
        levels = [c[1] for c in self.player.calls if c[0] == "opacity"]
        self.assertEqual(len(levels), 40)                              # 20 steps down over half the Mix duration, 20 up
        self.assertEqual(levels[:20], sorted(levels[:20], reverse=True))
        self.assertEqual(levels[20:], sorted(levels[20:]))
        self.assertEqual((levels[0], levels[19], levels[20], levels[39]), (242, 0, 13, 255))
        self.assertEqual([e for e in self.events if e[0] != "sleep"], [])  # the panel's Fader is never touched (see the review)
        self.assertAlmostEqual(sum(e[1] for e in self.events), 2.0)

    def test_an_operators_fade_out_is_not_undone_by_the_next_change(self):
        """The change dipped from 100 and faded back up: a picture the operator had faded out flashed up from black."""
        self.vibes.start()
        self.vibes.tick()
        self.player.opacity(0)                                         # what Fade out leaves: the mix value is still 100
        del self.player.calls[:]
        self.now[0] += 180
        self.assertTrue(self.vibes.tick())                             # the shader changes, in the dark
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"], [])
        self.assertEqual(self.player.level, 0)
        self.player.opacity(255)                                       # Fade in: the next change dips as usual
        self.now[0] += 180
        self.assertTrue(self.vibes.tick())
        self.assertEqual(self.player.level, 100)

    def test_stop_during_the_dip_does_not_leave_the_screen_dark(self):
        """Brightness stayed at 0 after a Stop (or a Vibes stop) that landed inside the dip."""
        for how in ("panel stop", "vibes stop", "error"):
            self.vibes.start()
            self.vibes.tick()
            self.now[0] += 180
            steps = [0]

            def during():
                steps[0] += 1
                if steps[0] == 12:                                     # more than half way down
                    if how == "panel stop":
                        self.api.control({"action": "stop"}, None, "t")
                    elif how == "vibes stop":
                        self.vibes.stop()
                    else:
                        self.player.down = True
                        self.player.source_epoch += 1
                        self.player.path = None
            self.during_dip = during
            self.assertFalse(self.vibes.tick(), how)
            self.during_dip = None
            self.player.down = False
            self.assertEqual((self.vibes.running, self.player.path, self.player.level), (False, None, 100), how)

    def test_ending_because_the_module_went_off_takes_the_shader_off(self):
        self.vibes.start()
        self.vibes.tick()
        self.api.registry.set_enabled("shaders", False)                # not through the API, which stops Vibes itself
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.running, self.player.path, self.player.source_shader), (False, None, None))
        self.assertIn("module was switched off", self.vibes.status()["last"]["message"])

    def test_start_stop_and_next_never_wait_for_a_change_in_progress(self):
        """They took the lock that a change holds through its dip and the wait for the GPU: a second /pvj/vibes/next
        stalled every later OSC cue, Blackout and Stop included."""
        import threading
        import time
        # The change is held in its dip for a minute, far longer than the calls take together. The proof is that each
        # call returns while the change is still held (`left` is not set): a call that waited for the change could
        # only return after it. The time limit is a second check, for a wait that gives up by itself: 2 seconds is
        # 30 times below the hold and 10 times above the 0.19 s that one call took on a busy CI runner (the old limit
        # was 0.1 s; nothing in these calls waits, set_dwell writes the settings file and that can be slow).
        hold, limit = 60, 2.0
        inside, go, left = threading.Event(), threading.Event(), threading.Event()
        self.vibes.start()
        self.vibes.tick()
        self.now[0] += 180
        self.during_dip = lambda: left.is_set() or (inside.set(), go.wait(hold), left.set())     # held once, at the first step
        worker = threading.Thread(target=self.vibes.tick, daemon=True)
        worker.start()
        self.assertTrue(inside.wait(5))
        try:
            for name, call in (("skip", self.vibes.skip), ("status", self.vibes.status), ("set_dwell", lambda: self.vibes.set_dwell(60)),
                               ("tick", self.vibes.tick),
                               ("api next", lambda: self.api.handle("POST", "/api/vibes", {"next": True}, {"id": "osc", "role": "live"}, "t")),
                               ("stop", self.vibes.stop), ("yield_screen", self.vibes.yield_screen), ("start", self.vibes.start),
                               ("stop again", self.vibes.stop)):
                started = time.perf_counter()
                call()
                took = time.perf_counter() - started
                self.assertFalse(left.is_set(), "%s returned only after the change (%.2f s)" % (name, took))
                self.assertTrue(worker.is_alive(), name)
                self.assertLess(took, limit, name)
        finally:
            go.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual((self.vibes.running, self.player.path, self.player.level), (False, None, 100))   # the stop was carried out

    def test_stop_clears_the_screen_only_while_vibes_has_it(self):
        self.vibes.start()
        self.vibes.tick()
        self.vibes.api_vibes({"on": False}, None, "t")
        self.assertEqual((self.vibes.running, self.player.calls[-1]), (False, ("clear",)))
        self.vibes.start()
        self.vibes.tick()
        self.player.play(["/media/clip.mp4"])
        self.vibes.api_vibes({"on": False}, None, "t")
        self.assertEqual(self.player.path, "/media/clip.mp4")          # someone else's clip is left alone

    def test_choosing_one_shader_by_hand_ends_the_rotation_and_keeps_that_shader(self):
        self.vibes.start()
        self.vibes.tick()
        self.engine.api_play({"id": "nxlx-tide.fs"}, None, "t")
        self.assertFalse(self.vibes.running)
        self.now[0] += 500
        self.assertFalse(self.vibes.tick())
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-tide.fs")

    def test_a_refused_shader_is_left_out_and_the_next_one_is_shown(self):
        self.player.vo = "gpu"
        real = self.engine._tap
        refuse = {"n": 1}

        def tap(path):
            FakeTap.lines = REFUSAL if refuse["n"] > 0 else []
            refuse["n"] -= 1
            return real(path)
        self.engine._tap = tap
        self.vibes.start()
        self.assertTrue(self.vibes.tick())
        self.assertEqual(len(self.vibes.refused), 1)
        self.assertNotIn(self.vibes.current, self.vibes.refused)
        self.assertEqual(self.engine.state()["playing"]["id"], self.vibes.current)
        bad = next(iter(self.vibes.refused))
        self.assertIn("%s left out" % bad, self.vibes.status()["last"]["message"])
        for _ in range(12):                                            # a full round and more: it is not tried again
            self.now[0] += 180
            self.assertTrue(self.vibes.tick())
            self.assertNotEqual(self.vibes.current, bad)

    def test_when_the_gpu_refuses_every_shader_it_ends_with_a_clear_screen(self):
        self.player.vo = "gpu"
        FakeTap.lines = REFUSAL
        self.engine._tap = lambda path: FakeTap(path)
        self.vibes.start()
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.running, len(self.vibes.refused), self.player.path, self.player.source_shader), (False, IN_VIBES, None, None))
        self.assertIn("refused every shader", self.vibes.status()["last"]["message"])

    def test_blackout_stays_black_through_a_change(self):
        self.vibes.start()
        self.vibes.tick()
        self.api.blackout({"on": True}, None, "t")
        del self.events[:]
        del self.player.calls[:]
        self.now[0] += 180
        self.assertTrue(self.vibes.tick())
        self.assertEqual(self.events, [])                              # no fade up behind a blackout
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"], [])

    def test_next_goes_on_at_once_and_only_while_running(self):
        with self.assertRaises(ApiError):
            self.vibes.api_vibes({"next": True}, None, "t")
        self.vibes.start()
        self.vibes.tick()
        first = self.vibes.current
        self.vibes.api_vibes({"next": True}, None, "t")
        self.assertTrue(self.vibes.tick())
        self.assertNotEqual(self.vibes.current, first)
        for bad in ({}, {"on": "yes"}, {"on": 1}, {"next": 1}):
            with self.assertRaises(ApiError):
                self.vibes.api_vibes(bad, None, "t")

    def test_a_player_that_went_down_ends_it(self):
        self.vibes.start()
        self.vibes.tick()
        self.player.path = None                                        # mpv was restarted: the carrier is gone
        self.assertFalse(self.vibes.tick())
        self.assertFalse(self.vibes.running)
        self.vibes.start()
        self.player.down = True
        self.assertFalse(self.vibes.tick())
        self.assertIn("not running", self.vibes.status()["last"]["message"])

    def test_the_thread_runs_only_while_vibes_is_on(self):
        import threading
        import time
        v = self.api.vibes = V.Vibes(self.api, self.engine, log=lambda *_: None)
        v.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and v.current is None:
            time.sleep(0.02)
        self.assertIsNotNone(v.current)
        v.stop()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(t.name == "vibes" for t in threading.enumerate()):
            time.sleep(0.02)
        self.assertFalse(any(t.name == "vibes" for t in threading.enumerate()))


class RealFaderTest(Base):
    """The review's high finding, with the panel's real Fader and the Mix transition set to Dip. A play that waits for
    its dip is kept by the Fader as a callback; Vibes used the same Fader, which dropped the callback: the API
    answered {"playing": "a.mp4"} and the clip never played."""

    def setUp(self):
        super().setUp()
        self.settings.data["mix"] = {"transition": "dip", "duration": 0.4}
        self.now = [1000.0]
        self.hook = None
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=self.sleep,
                                              rng=random.Random(5), thread=False, log=lambda *_: None)

    def sleep(self, seconds):
        self.now[0] += seconds
        if self.hook:
            hook, self.hook = self.hook, None
            hook()

    def wait_for_clip(self, name):
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (self.player.path or "").endswith(name):
            time.sleep(0.02)
        self.assertTrue((self.player.path or "").endswith(name), "the clip was accepted and never played: %s" % self.player.path)

    def test_a_clip_tapped_during_the_vibes_dip_plays_and_vibes_ends(self):
        self.vibes.start()
        self.vibes.tick()
        self.now[0] += 180
        answer = []
        self.hook = lambda: answer.append(self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertFalse(self.vibes.tick())
        self.assertEqual(answer, [{"playing": "a.mp4", "pending": True}])      # a dip answers before its clip has loaded (D71)
        self.wait_for_clip("a.mp4")
        self.assertFalse(self.vibes.running)
        import time
        time.sleep(0.4)                                                # the clip's own fade up is left alone
        self.assertEqual((self.player.level, self.player.source_shader), (100, None))
        for _ in range(3):
            self.now[0] += 500
            self.assertFalse(self.vibes.tick())
        self.assertTrue(self.player.path.endswith("a.mp4"))

    def test_a_clip_tapped_just_before_a_change_comes_due_plays(self):
        self.vibes.start()
        self.vibes.tick()
        self.now[0] += 180
        self.assertEqual(self.api.play({"file": "a.mp4"}, None, "t"), {"playing": "a.mp4", "pending": True})    # its dip has begun
        self.assertFalse(self.vibes.tick())                            # the change is due, but the screen is taken
        self.assertFalse(self.vibes.running)
        self.wait_for_clip("a.mp4")

    def test_the_other_ways_to_play_are_not_cancelled_either(self):
        import os as _os
        for name in ("pic1.png", "pic2.png"):
            open(_os.path.join(self.media, name), "w").close()
        self.settings.data["pads"]["banks"][0]["pads"][0] = {"label": "", "file": "b.mov", "ending": "loop"}
        for body, want in (({"pad": [0, 0]}, "b.mov"), ({"preset": "startless"}, ".mp4"), ({"slideshow": {"source": "media", "seconds": 5}}, "pic1.png")):
            self.vibes.start()
            self.vibes.tick()
            self.now[0] += 180
            self.hook = lambda: self.api.play(body, None, "t")
            self.assertFalse(self.vibes.tick(), body)
            self.wait_for_clip(want)
            self.assertFalse(self.vibes.running)
        self.vibes.start()
        self.vibes.tick()
        self.now[0] += 180
        self.hook = lambda: self.api.test_pattern({"on": True}, None, "t")
        self.assertFalse(self.vibes.tick())
        self.assertEqual((self.player.path, self.vibes.running), (self.player.TEST_PATTERN, False))

    def test_vibes_never_calls_the_panels_fader(self):
        used = []
        self.api.fader.ramp = lambda *a, **k: used.append("ramp")
        self.api.fader.cancel = lambda: used.append("cancel")
        self.player.play(["/media/clip.mp4"])
        self.vibes.start()
        for _ in range(3):
            self.assertTrue(self.vibes.tick())
            self.now[0] += 180
        self.vibes.stop()
        self.assertEqual(used, [])


class GpuStepTest(unittest.TestCase):
    def test_the_ci_step_fails_when_its_tests_are_skipped(self):
        """"A skip here is a failure" was not true: unittest exits 0 when every test was skipped."""
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k != "PVJ_GPU_TEST"}
        r = subprocess.run([sys.executable, "-m", "tests.test_shaders_gpu"], capture_output=True, text=True, env=env, timeout=120,
                           cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        self.assertEqual(r.returncode, 1, r.stdout[-300:] + r.stderr[-300:])
        self.assertIn("0 run", r.stdout)


class OtherWaysInTest(Base):
    def test_autostart_schedule_and_osc_start_vibes_through_the_same_call(self):
        calls = []
        self.api.vibes.api_vibes = lambda body, device, client: calls.append((body, client))
        cfg = autostart.validate({"mode": "vibes", "delay": 3})
        self.assertEqual((cfg["mode"], cfg["delay"]), ("vibes", 3))
        self.settings.data["autostart"] = cfg
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        self.assertEqual(a.run_now(), "started")
        clean = scheduler.validate({"enabled": True, "entries": [{"time": "18:00", "days": [4], "action": "vibes"}]})
        self.assertEqual(clean["entries"][0]["action"], "vibes")
        import datetime
        s = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        s._execute(clean["entries"][0], datetime.datetime(2026, 10, 2, 18, 0))
        self.assertEqual(s.last[clean["entries"][0]["id"]]["ok"], True)
        self.assertEqual(osc.translate("/pvj/vibes", [1.0]), ("/api/vibes", {"on": True}))
        self.assertEqual(osc.translate("/pvj/vibes", []), ("/api/vibes", {"on": True}))
        self.assertIsNone(osc.translate("/pvj/vibes", [0.0]))          # a button's release does nothing
        self.assertEqual(osc.translate("/pvj/vibes/next", [1]), ("/api/vibes", {"next": True}))
        self.assertEqual(calls, [({"on": True}, "autostart"), ({"on": True}, "schedule")])

    def test_autostart_reports_a_module_that_is_off_and_does_not_crash(self):
        self.api.registry.set_enabled("shaders", False)
        self.settings.data["autostart"] = autostart.validate({"mode": "vibes"})
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        self.assertIn("Shaders and Vibes", a.run_now())
        self.assertEqual(a.last["ok"], False)


class RolesTest(Base):
    def test_who_may_do_what(self):
        full, _ = self.pair()
        view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=full)[1]["token"]
        upload = {"action": "upload", "name": "mine.fs", "source": GOOD}
        self.assertEqual(self.call("GET", "/api/shaders")[0], 401)
        self.assertEqual(self.call("POST", "/api/vibes", {"on": True})[0], 401)
        for token in (view, live, full):
            st, body, _ = self.call("GET", "/api/shaders", token=token)
            self.assertEqual((st, len(body["shaders"])), (200, BUNDLED + len(PACKED)))
            self.assertEqual(len([s for s in body["shaders"] if s["pack"] == "nxlx"]), BUNDLED)
        for path, body in (("/api/shaders/play", {"id": "nxlx-aurora.fs"}), ("/api/vibes", {"on": True}), ("/api/shaders", upload)):
            self.assertEqual(self.call("POST", path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=full, csrf=False)[0], 403, path)      # no cross-site requests
        self.assertEqual(self.call("POST", "/api/shaders", upload, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/shaders", {"action": "config", "dwell": 60}, token=live)[0], 403)
        st, body, _ = self.call("POST", "/api/shaders/play", {"id": "nxlx-aurora.fs", "values": {"speed": 2}}, token=live)
        self.assertEqual((st, body["playing"]["id"], body["playing"]["values"]["speed"]), (200, "nxlx-aurora.fs", 2.0))
        self.assertEqual(sorted(body["playing"]["values"]), ["height", "speed", "tint"])     # every input's value, as it is on the screen
        self.assertEqual(self.call("POST", "/api/shaders/play", {"id": "../../etc/passwd"}, token=live)[0], 400)
        self.assertEqual(self.call("POST", "/api/shaders/play", {"id": "nope.fs"}, token=live)[0], 404)
        self.assertEqual(self.call("POST", "/api/shaders/play", {"id": "nxlx-aurora.fs", "values": {"speed": "fast"}}, token=live)[0], 422)
        self.api.vibes._use_thread = False
        st, body, _ = self.call("POST", "/api/vibes", {"on": True}, token=live)
        self.assertEqual((st, body["running"]), (200, True))
        self.assertEqual(self.call("POST", "/api/vibes", {"on": False}, token=live)[1]["running"], False)
        st, body, _ = self.call("POST", "/api/shaders", upload, token=full)
        self.assertEqual((st, [s["id"] for s in body["shaders"] if s["source"] == "uploaded"]), (200, ["mine.fs"]))
        st, body, _ = self.call("POST", "/api/shaders", dict(upload, name="bad.fs", source=GOOD + "#include <x>\n"), token=full)
        self.assertEqual((st, "not allowed" in body["error"]), (422, True))
        self.assertEqual(self.call("POST", "/api/shaders", {"action": "delete", "id": "mine.fs"}, token=full)[0], 200)

    def test_osc_reaches_vibes_as_a_presenter_and_nothing_more(self):
        self.api.vibes._use_thread = False
        self.assertEqual(self.api.handle("POST", "/api/vibes", {"on": True}, osc.OSC_DEVICE, "10.0.0.5")[0], 200)
        self.assertEqual(self.api.handle("POST", "/api/shaders", {"action": "upload", "name": "x.fs", "source": GOOD}, osc.OSC_DEVICE, "10.0.0.5")[0], 403)

    def test_the_module_is_refused_while_off(self):
        full, _ = self.pair()
        self.api.registry.set_enabled("shaders", False)
        for path, body in (("/api/shaders/play", {"id": "nxlx-aurora.fs"}), ("/api/vibes", {"on": True}), ("/api/shaders", {"action": "config", "dwell": 60})):
            self.assertEqual(self.call("POST", path, body, token=full)[0], 409, path)


if __name__ == "__main__":
    unittest.main()
