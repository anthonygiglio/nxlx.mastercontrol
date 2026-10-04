# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shaders and Vibes: the ISF translator (friendly and hostile files), the engine against a fake player, the Vibes
rotation on a fake clock, and who may do what over HTTP. The real mpv and a real GPU are in test_shaders_gpu.py."""
import json
import os
import random
import re
import unittest

from pvj import autostart, osc, scheduler, shaders as S, vibes as V
from pvj.api import ApiError
from pvj.settings import SCHEMA
from tests.test_server import ServerBase

HEAD = {"ISFVSN": "2", "DESCRIPTION": "test", "CREDIT": "tests", "INPUTS": [
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
        self.assertIn("#line %d\n" % p["line"], S.translate(p, (640, 360)))

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
        for name in ("pvj_color", "HOOKED_tex", "pvj_main", "PVJ_HP", "Hooked_pos", "texture0", "texcoord0", "out_color", "input_size"):
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
        self.assertIn("never closed", refusal(self, isf(body=ok + "\n/* open")))
        self.assertNotIn("secret", S.translate(S.parse(isf(body=ok + " // secret\n/* secret */")), (640, 360)))
        for text in (isf(body=ok + "\n// café"), isf(body=ok + "\x00"), isf(body=ok + "\x1b[2J"), isf(body=ok + "\x0c")):
            self.assertIn("ASCII", refusal(self, text))
        self.assertIn("exactly one void main", refusal(self, isf(body="float f() { return 1.0; }")))
        self.assertIn("exactly one void main", refusal(self, isf(body=ok + "\nvoid main(void) { }")))

    def test_input_names_and_numbers_are_checked_strictly(self):
        def one(spec):
            return isf(INPUTS=[spec])
        for name in ("gl_FragColor", "hook", "frame", "random", "main", "TIME", "RENDERSIZE", "sin", "float", "pvj_x", "isf_x", "HOOKED_pos",
                     "PVJ_HP", "Hook", "Time", "rendersize", "Gl_x", "hooked_x", "texture0", "TEXCOORD0",
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
        for text, reason in ((ok, "not an ISF file"), ("/*{", "not closed"), ("/* not json */" + ok, "cannot be read"),
                             ("/*[1, 2]*/" + ok, "must be an object"), ("/*" + "[" * 5000 + "*/" + ok, "cannot be read"),
                             ("/*{\"INPUTS\": 5}*/" + ok, "INPUTS"), ("/*{}" + " " * S.MAX_HEADER + "*/" + ok, "not closed, or is larger"),
                             (isf() + "/" * S.MAX_SOURCE, "larger than"), (b"\xff\xfe\x00", "not plain text"), (5, "must be text")):
            with self.assertRaises(S.ShaderError) as c:
                S.parse(text)
            self.assertIn(reason, str(c.exception))
        self.assertTrue(S.parse("﻿  \r\n/*{}*/\r\n" + ok))       # a byte order mark and Windows line ends are fine

    def test_every_bundled_shader_translates_and_carries_its_licence_and_cost(self):
        names = sorted(n for n in os.listdir(S.BUNDLED_DIR) if n.endswith(".fs"))
        self.assertTrue(8 <= len(names) <= 12, names)
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

    def test_the_carrier_is_built_from_whole_numbers_and_has_the_screens_shape(self):
        self.assertEqual(S.carrier_url((1920, 1080)), "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0")
        self.assertEqual(S.carrier_url((2560, 1440)), S.carrier_url((1280, 720)))
        self.assertEqual(S.carrier_url((1920, 1200)), "av://lavfi:color=c=black:size=64x40:rate=30,format=rgb0")
        self.assertEqual(S.carrier_url((1366, 768)), "av://lavfi:color=c=black:size=683x384:rate=30,format=rgb0")
        self.assertEqual(S.carrier_url((1080, 1920)), "av://lavfi:color=c=black:size=27x48:rate=30,format=rgb0")
        for good in (S.carrier_url((1920, 1080)), S.carrier_url((1366, 768))):
            self.assertTrue(S.is_carrier(good))
        for bad in (S.carrier_url((1920, 1080)) + "\n", "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0,movie=/etc/passwd",
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

    def osd_size(self):
        return (1920, 1080)

    def status(self):
        return {"running": True, "path": self.path}

    def play_source(self, shader, carrier, epoch=None, spawn=False):
        from pvj.player import PlayerError
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
        self.source_epoch += 1

    def opacity(self, value):
        self.level = value / 2.55
        self.calls.append(("opacity", value))

    def source_opacity(self, value, epoch):
        if epoch != self.source_epoch:
            return False
        self.opacity(value)
        return True

    def opacity_now(self):
        return self.level

    def clear_source(self, epoch):
        if epoch != self.source_epoch or self.carrier is None:
            return False
        self.clear()
        return True

    def swap_source(self, shader, epoch):
        if epoch != self.source_epoch:
            return False
        self.source_shader = shader
        self.calls.append(("swap_source", shader))
        return True

    def play(self, paths, *a, **k):
        self.source_epoch += 1
        self.source_shader, self.path, self.carrier = None, paths[0], None
        self.calls.append(("play", paths))

    def clear(self):
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
        self.assertEqual(self.player.calls[-2], ("play_source", path, "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0"))
        with open(path) as f:
            text = f.read()
        self.assertIn("//!WIDTH 1280\n//!HEIGHT 720\n", text)       # 720 lines by default, in the screen's shape
        self.assertIn("const float speed = 1.5;", text)
        st = self.engine.state()
        self.assertEqual((st["playing"]["id"], st["playing"]["values"], st["playing"]["checked"]), ("nxlx-aurora.fs", {"speed": 1.5}, None))
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
        self.assertEqual((self.player.calls[-2][:2], self.player.calls[-1]), (("swap_source", None), ("clear",)))   # black at once, then stopped
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
        self.assertEqual([(s["id"], s["description"], s["vibes"]) for s in mine], [("My shader 1.fs", "mine", True)])
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
        self.assertEqual(len(rows), 13)
        for n in ("nan.fs", "huge.fs", "digits.fs"):
            self.assertTrue(rows[n]["error"] and not rows[n]["vibes"], rows[n])
        self.assertEqual(len(self.engine.vibes_ids()), 10)
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
        self.assertEqual(SCHEMA, 13)
        self.assertNotIn("shaders", self.settings.data)               # nothing is written until something changes
        self.assertEqual(self.engine.state()["config"], {"dwell": 180, "vary": True, "height": 720})
        r = self.engine.api_set({"action": "config", "dwell": 45, "vary": False, "height": 540}, None, "t")
        self.assertEqual(r["config"], {"dwell": 45, "vary": False, "height": 540})
        self.assertEqual((r["render"]["width"], r["render"]["height"]), (960, 540))
        self.assertEqual(self.settings.data["shaders"], {"dwell": 45, "vary": False, "height": 540, "disabled": []})
        for bad in ({"dwell": 5}, {"dwell": 99999}, {"dwell": "60"}, {"dwell": True}, {"dwell": float("nan")}, {"vary": 1}, {"height": 721}, {"height": True}):
            with self.assertRaises(ApiError, msg=bad):
                self.engine.api_set(dict({"action": "config"}, **bad), None, "t")
        with self.assertRaises(ApiError):
            self.engine.api_set({"action": "format-disk"}, None, "t")
        self.settings.data["shaders"] = {"dwell": "soon", "vary": None, "height": 9, "disabled": ["../x", 5, "ok.fs"]}   # edited by hand
        self.assertEqual(self.engine.config(), {"dwell": 180, "vary": True, "height": 720, "disabled": ["ok.fs"]})
        self.settings.data["shaders"] = "nonsense"
        self.assertEqual(self.engine.config(), S.default_config())


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
        self.assertEqual(len(enabled), 10)
        seen = self.run_rounds(30)
        for k in range(0, 30, 10):
            self.assertEqual(sorted(seen[k:k + 10]), sorted(enabled))
        self.assertNotEqual(seen[:10], sorted(enabled))                # shuffled, not in name order
        self.assertNotEqual(seen[:10], seen[10:20])                    # and shuffled again each time round
        for a, b in zip(seen, seen[1:]):
            self.assertNotEqual(a, b)                                  # never the same one twice in a row

    def test_only_shaders_switched_on_for_vibes_are_picked(self):
        for sid in self.engine.vibes_ids()[2:]:
            self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        self.engine.upload("mine.fs", GOOD)
        allowed = {"nxlx-aurora.fs", "nxlx-drift.fs", "mine.fs"}
        self.assertEqual(set(self.engine.vibes_ids()), allowed)
        self.assertEqual(set(self.run_rounds(9)), allowed)
        for sid in allowed:
            self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        with self.assertRaises(ApiError) as c:
            self.vibes.start()
        self.assertEqual(c.exception.status, 409)

    def test_each_round_varies_the_numbers_inside_min_and_max_and_shifts_the_palette(self):
        for sid in self.engine.vibes_ids():
            if sid != "nxlx-aurora.fs":
                self.engine.api_set({"action": "vibes", "id": sid, "on": False}, None, "t")
        texts = []
        self.vibes.start()
        for _ in range(25):
            self.assertTrue(self.vibes.tick())
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
        self.assertEqual((self.vibes.running, len(self.vibes.refused), self.player.path, self.player.source_shader), (False, 10, None, None))
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
        self.assertEqual(answer, [{"playing": "a.mp4"}])
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
        self.assertEqual(self.api.play({"file": "a.mp4"}, None, "t"), {"playing": "a.mp4"})    # its dip has begun
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
            self.assertEqual((st, len(body["shaders"])), (200, 10))
        for path, body in (("/api/shaders/play", {"id": "nxlx-aurora.fs"}), ("/api/vibes", {"on": True}), ("/api/shaders", upload)):
            self.assertEqual(self.call("POST", path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=full, csrf=False)[0], 403, path)      # no cross-site requests
        self.assertEqual(self.call("POST", "/api/shaders", upload, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/shaders", {"action": "config", "dwell": 60}, token=live)[0], 403)
        st, body, _ = self.call("POST", "/api/shaders/play", {"id": "nxlx-aurora.fs", "values": {"speed": 2}}, token=live)
        self.assertEqual((st, body["playing"]["id"], body["playing"]["values"]), (200, "nxlx-aurora.fs", {"speed": 2.0}))
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
