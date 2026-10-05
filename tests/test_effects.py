# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Effects: ISF filters over the playing picture (pvj/effects.py). The translator with hostile filter files, the
library, and the engine's whole life against the real Player class with a stand-in for mpv behind it (properties in
a dict, nothing drawn). What a real mpv draws is in tests/test_effects_gpu.py."""
import hashlib
import json
import os
import re
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
OWN = ["fx-edge-glow", "fx-grade", "fx-kaleido", "fx-mirror-quad", "fx-pixel-grid", "fx-rgb-split", "fx-slit-bands", "fx-vignette", "fx-wash"]
MOVING = ["fx-kaleido", "fx-rgb-split", "fx-slit-bands"]                # they read TIME: the flash limit applies
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
        half = E.translate(p, controls={"half": True})
        self.assertEqual(half.count("//!WIDTH HOOKED.w 2 /\n//!HEIGHT HOOKED.h 2 /"), 2)
        self.assertIn("#define RENDERSIZE (HOOKED_size * 0.5)", half)
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
            (fx(body=BODY.replace("* k", "* k * HOOKED_tex(vec2(0.5)).r")), "used by the player"),
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


class FakeMpv:
    """What the panel asks of an mpv, with nothing behind it: properties in a dict, loadfile sets the path, and the
    passes it "drew" are the shader texts it has loaded."""
    def __init__(self):
        self.pid = 1000
        self.video = {"colormatrix": "bt.709", "colorlevels": "limited", "pixelformat": "yuv420p"}
        self.fps, self.pass_ns, self.vo, self.down, self.drops = 25.0, 1500000, "gpu", False, 0
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
                if self.props["path"] is None or self.video is None:
                    raise PlayerError("mpv: property unavailable")
                return dict(self.video) if name == "video-params" else self.fps
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
                return self.drops if name == "frame-drop-count" else 0
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
            self.assertIn(s["weight"], ("light", "medium"), s["id"])                   # nothing bundled is heavy by the count
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
        for call in (lambda: self.fx.put("fx-wash.fs"), lambda: self.fx.step(1), lambda: self.fx.toggle(), lambda: self.fx.apply_preset({"id": "fx-wash.fs", "name": "x"})):
            with self.assertRaises(ApiError) as c:
                call()
            self.assertIn(c.exception.status, (409, 404))
        self.assertEqual(self.mpv.loaded, [])
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

    def test_a_generator_shader_takes_it_off_in_the_same_step_and_none_goes_on_over_a_generator(self):
        self.fx.put("fx-wash.fs")
        before = len(self.mpv.commands)
        self.gen.show("nxlx-silk.fs")
        lists = [c[2] for c in self.mpv.commands[before:] if c[:2] == ("set_property", "glsl-shaders")]
        self.assertTrue(lists and all(len(x) == 1 and os.path.basename(x[0]).startswith("shader-") for x in lists), lists)   # never both
        self.assertEqual((self.state()["on"], self.state()["last"]), (None, "a generator shader took the screen"))
        s = self.state()
        self.assertEqual((s["available"], "A generator shader has the screen" in s["unavailable"]), (False, True))
        for call in (lambda: self.fx.put("fx-wash.fs"), lambda: self.fx.step(1), lambda: self.fx.toggle()):
            with self.assertRaises(ApiError) as c:
                call()
            self.assertEqual(c.exception.status, 409)
            self.assertIn("generator", c.exception.message)
        self.assertEqual(len(self.mpv.loaded), 1)
        self.assertIsNotNone(self.gen.on_screen())                                     # the generator was never disturbed
        self.player.play(["/media/a.mp4"])                                             # a clip again: effects are back, none is on
        self.assertEqual((self.state()["available"], self.state()["on"], self.mpv.loaded), (True, None, []))

    def test_vibes_takes_it_off_and_the_module_going_off_too(self):
        self.fx.put("fx-wash.fs")
        self.api.vibes.start()
        self.api.vibes.tick()
        self.assertTrue(self.api.vibes.running)
        self.assertEqual((self.state()["on"], [n.split("-")[0] for n in self.mpv.loaded]), (None, ["shader"]))
        with self.assertRaises(ApiError):
            self.fx.put("fx-wash.fs")
        self.api.vibes.stop()
        self.player.play(["/media/a.mp4"])
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
                     "const vec2 spot = vec2(0.2, 0.9);", "pvj_native(c), 0.4)", "//!WIDTH HOOKED.w 2 /"):
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
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": False})
        self.assertEqual(self.mpv.loaded, [])
        self.assertEqual(self.fx.toggle(), {"ok": True, "on": True, "id": ids[-1]})    # the one that was on last
        self.pump()
        self.assertEqual(self.state()["on"]["id"], ids[-1])
        with self.assertRaises(ApiError):
            self.fx.step(2)

    def test_what_was_asked_for_before_the_screen_changed_hands_is_dropped(self):
        """The epoch rule: a step, an "on" or a change that waits for the worker belongs to the moment it was asked
        for. Whatever is played, stopped, put on or taken off before the worker comes round keeps the screen."""
        for name, between in (("a clip was played", lambda: self.player.play(["/media/b.mov"])),
                              ("Stop was pressed", lambda: (self.player.clear(), self.player.play(["/media/a.mp4"]))),
                              ("a generator took the screen", lambda: self.gen.show("nxlx-silk.fs")),
                              ("another effect was put on", lambda: self.fx.put("fx-wash.fs")),
                              ("the effect was taken off", lambda: self.fx.off())):
            self.player.play(["/media/a.mp4"])
            self.fx.off()
            self.fx.put("all.fs")
            self.fx.step(1)
            before = self.fx.changer.queued()
            self.assertIsNotNone(before, name)
            between()
            was = self.mpv.loaded
            if name != "the effect was taken off":
                self.fx.changer.show(before)                                           # (off forgets what waits; put it back to see it refused)
            self.fx.changer._last = -1e9
            self.fx.changer.pump()
            self.assertEqual(self.mpv.loaded, was, name)
            self.assertNotEqual((self.state()["on"] or {}).get("id"), "moves.fs", name)
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
            self.fx.toggle()                                                           # off: one request to the player
            took = time.monotonic() - began
        finally:
            go.set()
            t.join()
        self.assertLess(took, 1.0, "a controller's call waited for the engine's lock")
        self.assertEqual(self.mpv.loaded, [])


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

    def test_a_source_and_an_effect_are_never_on_together(self):
        self.p.put_effect("/e1.glsl")
        self.p.play_source("/s1.glsl", "av://lavfi:carrier")
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_ended), (["/s1.glsl"], "generator"))
        self.assertIsNone(self.p.put_effect("/e2.glsl"))
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/s1.glsl"])
        self.p.play(["/media/a.mp4"])
        self.assertEqual(self.p.put_effect("/e2.glsl"), 3)
        self.p.clear()
        self.assertEqual((self.mpv.props["glsl-shaders"], self.p.effect_ended), ([], "stop"))

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
                 ("/api/effects/preset", {"index": 1}), ("/api/effects/presets", {"action": "save", "name": "x"}), ("/api/effects/library", upload))
        for path, body in posts:
            self.assertEqual(self.call("POST", path, body)[0], 401, path)
            self.assertEqual(self.call("POST", path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=full, csrf=False)[0], 403, path)          # no cross-site requests
        # a presenter puts on, changes, steps and takes off; saving a preset and adding a file are the owner's
        st, body, _ = self.call("POST", "/api/effects", {"id": "fx-wash.fs", "values": {"strength": 0.3}, "controls": {"amount": 0.5}}, token=live)
        self.assertEqual((st, body["on"]["id"], body["on"]["controls"]["amount"]), (200, "fx-wash.fs", 0.5))
        self.assertEqual(self.call("POST", "/api/effects/values", {"controls": {"amount": 0.2}}, token=live)[1]["controls"]["amount"], 0.2)
        self.assertEqual(self.call("POST", "/api/effects/step", {"dir": 1}, token=live)[0], 200)
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

    def test_the_module_is_refused_while_off(self):
        full, _ = self.pair()
        self.api.registry.set_enabled("shaders", False)
        for path, body in (("/api/effects", {"id": "fx-wash.fs"}), ("/api/effects", {"off": True}), ("/api/effects/values", {"controls": {"amount": 1}}),
                           ("/api/effects/step", {"dir": 1}), ("/api/effects/preset", {"index": 1}), ("/api/effects/presets", {"action": "save", "name": "x"}),
                           ("/api/effects/library", {"action": "delete", "id": "x.fs"})):
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
        self.assertTrue(hub._do("/api/effects", {"toggle": True}))                    # off
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
        """Only controls that had no action got one (the profiles' own tests pin every other control): the amount on
        a spare fader, the effect's inputs on a spare row of knobs or pads, on/off and the steps on spare buttons."""
        want = {"korg-nanokontrol2": {"fader7": "effect_amount", "r5": "effect_toggle"},
                "akai-midimix": dict({"knob_c%d" % n: "effect_control_%d" % n for n in range(1, 9)}, fader7="effect_amount"),
                "novation-launchpad-mini": dict({"pad17": "effect_toggle", "pad27": "effect_prev", "pad28": "effect_next"},
                                                **{"pad%d%d" % (3 + (n - 1) // 2, 7 + (n - 1) % 2): "effect_control_%d" % n for n in range(1, 9)})}
        profiles = M.load_profiles(log=lambda *_: None)
        self.assertEqual(sorted(p["id"] for p in profiles), sorted(want))
        for p in profiles:
            got = {c["id"]: c["action"]["action"] for c in p["controls"] if c["action"] and c["action"]["action"].startswith("effect_")}
            self.assertEqual(got, want[p["id"]], p["id"])
            for c in p["controls"]:                                                    # a fader or knob follows, a button or pad presses
                if c["id"] in got:
                    kind = M.ACTIONS[got[c["id"]]][0]
                    self.assertTrue(kind != "trigger" if c["kind"] in ("fader", "knob") else kind != "level", (p["id"], c["id"]))
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
