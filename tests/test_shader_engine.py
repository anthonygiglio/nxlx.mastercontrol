# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The shader engine made fit to perform with: what the first measurements on a real Pi 4 found, every input type
adjustable live, changes that are coalesced, the common controls, presets, rotation sets, and the guard for a weak
GPU. All against the fake player of test_shaders.py and fake clocks; the real mpv is in test_shaders_gpu.py."""
import datetime
import json
import os
import random
import threading
import time

from pvj import boxcare, midi, osc, scheduler, shaderlive as L, shaders as S, vibes as V
from pvj.api import ApiError
from pvj.midi import MidiMapper
from pvj.settings import SCHEMA
from tests.test_shaders import GOOD, REFUSAL, Base, FakeTap

ALL = """/*{"INPUTS": [
 {"NAME": "level", "TYPE": "float", "MIN": 0.0, "MAX": 2.0, "DEFAULT": 0.5, "LABEL": "Level"},
 {"NAME": "lit", "TYPE": "bool", "DEFAULT": false},
 {"NAME": "mode", "TYPE": "long", "VALUES": [0, 2, 5], "LABELS": ["none", "two"], "DEFAULT": 2},
 {"NAME": "count", "TYPE": "long", "MIN": 1, "MAX": 6, "DEFAULT": 3},
 {"NAME": "tint", "TYPE": "color", "DEFAULT": [1.0, 0.5, 0.25, 1.0]},
 {"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.5, 0.5], "MIN": [0.0, 0.0], "MAX": [1.0, 1.0]},
 {"NAME": "bang", "TYPE": "event"},
 {"NAME": "steps", "TYPE": "float", "MIN": 1.0, "MAX": 6.0, "DEFAULT": 3.0}]}*/
void main() {
    float v = 0.0;
    for (int i = 0; i < 6; i++) { if (float(i) < steps) { v += level; } }
    gl_FragColor = vec4(tint.rgb * v + vec3(spot, float(mode + count)), (lit || bang) ? 1.0 : 0.5);
}
"""


def passes(player, fresh, redraw=()):
    """Make the fake player report these pass descriptions (name, nanoseconds) as mpv's vo-passes does."""
    def request(*command):
        if command[:2] == ("get_property", "vo-passes"):
            return {"fresh": [{"desc": "user shader: %s (rgb)" % d, "avg": ns, "last": ns} for d, ns in fresh],
                    "redraw": [{"desc": "user shader: %s (rgb)" % d, "avg": ns, "last": ns} for d, ns in redraw]}
        return type(player.ipc).request(player.ipc, *command)
    player.ipc.request = request


class MeasuredFixesTest(Base):
    """Fixes d, e and f: what the first run on the Pi 4 showed to be wrong."""

    def test_pass_ms_is_the_playing_shaders_own_time_not_the_one_before_it(self):
        self.engine.show("nxlx-aurora.fs")
        old = self.engine.playing["desc"]
        self.engine.show("nxlx-tide.fs")
        new = self.engine.playing["desc"]
        self.assertNotEqual(old, new)
        # after a change mpv still lists the shader before this one for a redraw, and it came last
        passes(self.player, [(new, 15100000)], redraw=[(old, 20600000)])
        self.assertEqual(self.engine.state()["playing"]["pass_ms"], 15.1)
        passes(self.player, [(old, 20600000)])                      # only the old one has been timed so far: say nothing
        self.assertNotIn("pass_ms", self.engine.state()["playing"])
        passes(self.player, [(new + "7", 9000000)])                 # "shader 5 2" is not "shader 5 27"
        self.assertNotIn("pass_ms", self.engine.state()["playing"])
        passes(self.player, [(new, True)])                          # a flag is not a time
        self.assertNotIn("pass_ms", self.engine.state()["playing"])

    def test_the_gpu_check_does_not_take_another_shaders_pass_for_its_own(self):
        self.player.vo = "gpu"
        real = self.player.play_source

        def play(shader, carrier, epoch=None, spawn=False):
            new = real(shader, carrier, epoch, spawn)
            passes(self.player, [(self.player.drawn[0] + "3", 5000000)])       # a pass with a longer number: not ours
            return new
        self.player.play_source = play
        self.engine.show("nxlx-aurora.fs")
        self.assertIsNone(self.engine.state()["playing"]["checked"])

    def test_a_refused_shader_with_none_before_it_leaves_black_not_the_carriers_own_colour(self):
        """The carrier's colour is its frame number now: the bare carrier would show a dark red that gets brighter."""
        self.player.vo = "gpu"
        FakeTap.lines = REFUSAL
        r = self.engine.show("nxlx-tide.fs")
        self.assertEqual((r["ok"], r["showing"]), (False, None))
        with open(self.player.source_shader) as f:
            text = f.read()
        self.assertEqual(text, S.BLACK)
        self.assertIn("return vec4(0.0, 0.0, 0.0, 1.0);", text)
        self.assertEqual(self.generated(), [os.path.basename(self.player.source_shader)])      # and the refused text is gone
        FakeTap.lines = []
        self.engine.show("nxlx-tide.fs")
        self.assertEqual(len(self.generated()), 1)                    # the black one is removed like any other

    def test_deleting_a_shader_forgets_its_refusal(self):
        self.player.vo = "gpu"
        self.engine.upload("mine.fs", GOOD)
        self.engine.upload("other.fs", GOOD.replace("mine", "other"))
        FakeTap.lines = REFUSAL
        self.assertFalse(self.engine.show("mine.fs")["ok"])
        self.assertEqual(self.engine.state()["error"]["id"], "mine.fs")
        self.engine.delete("other.fs")                              # another file: the note stays
        self.assertEqual(self.engine.state()["error"]["id"], "mine.fs")
        self.engine.delete("mine.fs")
        self.assertIsNone(self.engine.state()["error"])

    def test_compiler_warnings_about_the_translators_own_names_are_not_passed_on(self):
        head = [("vo/gpu/opengl", "error", "fragment shader source:"), ("vo/gpu/opengl", "error", "[  1] #version 140"),
                ("vo/gpu/opengl", "error", "[ 31] PVJ_HP float pvj_time;"), ("vo/gpu/opengl", "error", "fragment shader compile log (status=0):")]
        log = head + [("vo/gpu/opengl", "error", "0:31(1): warning: `pvj_k' used uninitialized"),
                      ("vo/gpu/opengl", "error", "0:44(9): warning: unused variable `PVJ_HP'"),
                      ("vo/gpu/opengl", "error", "0:102(14): error: `nonsense' undeclared"),
                      ("vo/gpu/opengl", "error", "0:103(2): warning: `mine' set but not used")]
        self.assertEqual(S.shader_errors(log), "line 102: `nonsense' undeclared")      # errors only; no warning, no pvj_ name
        # the other spelling of a compiler line (ERROR: 0:6: ...), and a warning of the author's own when nothing else is said
        self.assertEqual(S.shader_errors(head + [("vo/gpu/opengl", "error", "WARNING: 0:3: 'pvj_norm' : unused"),
                                                 ("vo/gpu/opengl", "error", "ERROR: 0:6: 'nonsense' : undeclared identifier")]),
                         "line 6: 'nonsense' : undeclared identifier")
        self.assertEqual(S.shader_errors(head + [("vo/gpu/opengl", "error", "0:9(2): warning: `mine' set but not used")]),
                         "line 9: `mine' set but not used")
        self.assertEqual(S.shader_errors(head + [("vo/gpu/opengl", "error", "0:31(1): warning: `pvj_k' used uninitialized")]),
                         "the GPU refused the shader")

    def test_the_generated_shader_text_never_reaches_the_message(self):
        """When no line of the compiler's log is understood, what is passed on must still not be the shader itself."""
        text = S.translate(S.parse(GOOD), (640, 360))
        log = [("vo/gpu/opengl", "error", "fragment shader source:")]
        log += [("vo/gpu/opengl", "error", "[%3d] %s" % (n + 1, line)) for n, line in enumerate(text.splitlines())]
        log += [("vo/gpu/opengl", "error", line) for line in text.splitlines()]            # and once more without numbers
        log += [("vo/gpu/opengl", "error", "shader compile log (status=0):"), ("vo/gpu/opengl", "error", "the driver gave up")]
        said = S.shader_errors(log)
        for internal in ("pvj_", "PVJ_", "//!", "HOOKED", "#define", "precision highp", "vec4 hook"):
            self.assertNotIn(internal, said)
        self.assertIn("the driver gave up", said)
        self.assertLessEqual(len(said), 500)


class Live(Base):
    """The engine with the worker's thread off: the test runs the worker's step itself, on a fake clock."""

    def setUp(self):
        super().setUp()
        self.now = [500.0]
        self.engine._clock = self.engine.changer._clock = self.engine.guard._clock = lambda: self.now[0]
        self.engine.changer._use_thread = False
        self.player.time_pos, self.player.drops = 0.0, 0
        real = self.player.ipc.request

        def request(*command):
            if command[:2] == ("get_property", "time-pos"):
                return self.player.time_pos
            if command[:2] == ("get_property", "frame-drop-count"):
                return self.player.drops
            return real(*command)
        self.player.ipc.request = request
        self.api.vibes._use_thread = False

    def text(self):
        with open(self.player.source_shader) as f:
            return f.read()

    def pump(self, after=1.0):
        self.now[0] += after
        return self.engine.changer.pump()

    def row(self, sid):
        return next(s for s in self.engine.state()["shaders"] if s["id"] == sid)


class BoardTest(Live):
    """Fixes a, b, c and g."""

    def test_the_default_detail_and_the_choices_depend_on_the_board(self):
        for board, default, heights in (("pi4", 540, [360, 540, 720]), ("pi5", 720, [360, 540, 720, 1080]), ("x86", 720, [360, 540, 720, 1080]),
                                        ("arm-other", 540, [360, 540, 720]), ("pi-other", 540, [360, 540, 720]), (None, 540, [360, 540, 720])):
            self.api.board = {"kind": board, "model": "t"}
            st = self.engine.state()
            self.assertEqual((st["config"]["height"], st["render"]["heights"], st["render"]["default"], st["render"]["measured"]),
                             (default, heights, default, board == "pi4"), board)
        self.api.board = {"kind": "pi4", "model": "t"}
        with self.assertRaises(ApiError) as c:                        # never 1080 on a Pi 4
            self.engine.api_set({"action": "config", "height": 1080}, None, "t")
        self.assertEqual(c.exception.status, 400)
        self.settings.data["shaders"] = {"height": 1080}              # a settings file from a Pi 5: read as this board's default
        self.assertEqual(self.engine.config()["height"], 540)
        self.assertEqual(self.engine.api_set({"action": "config", "height": 720}, None, "t")["render"]["height"], 720)
        self.assertEqual(S.default_config()["height"], 540)           # what a box of an unknown kind gets
        self.assertEqual(boxcare.check_shaders({"height": 1080}, None)["height"], 1080)       # a file may carry any known height
        self.assertNotIn("height", boxcare.check_shaders({"dwell": 60}, None))                # and none is made up for it

    def test_every_bundled_shader_says_how_heavy_it_is_and_what_was_measured(self):
        rows = {s["id"]: s for s in self.engine.state()["shaders"]}
        self.assertEqual({w: sorted(n[5:-3] for n, s in rows.items() if s["weight"] == w) for w in ("light", "medium", "heavy")},
                         {"light": ["ember", "horizon", "lattice", "prism", "pulse", "silk"], "medium": ["aurora", "tide"], "heavy": ["drift", "nebula"]})
        tide = rows["nxlx-tide.fs"]
        self.assertEqual(tide["measured"], {"board": "pi4", "lines": 720, "pass_ms": 15.1, "drops_per_second": {"540": 0, "720": 3.3}, "stale": False})
        self.assertEqual((rows["nxlx-silk.fs"]["measured"]["pass_ms"], rows["nxlx-silk.fs"]["measured"]["pass_ms_range"]), (None, [7.5, 11.2]))
        self.assertTrue(rows["nxlx-drift.fs"]["measured"]["stale"])   # its look was changed after the measurement
        for sid, s in rows.items():                                   # the file's own note starts with the same class
            self.assertEqual(L.weight_of("other.fs", s["cost"]), s["weight"], sid)
        self.engine.upload("mine.fs", GOOD)
        mine = self.row("mine.fs")
        self.assertEqual((mine["weight"], mine["measured"], mine["heavy"], mine["refused"]), ("", None, None, None))

    def test_the_heavy_ones_and_uploads_are_out_of_the_default_rotation(self):
        self.engine.upload("mine.fs", GOOD)
        ids = self.engine.vibes_ids()
        self.assertEqual(sorted(ids), sorted("nxlx-%s.fs" % n for n in ("aurora", "ember", "horizon", "lattice", "prism", "pulse", "silk", "tide")))
        self.assertNotIn("shaders", self.settings.data)               # still nothing saved: it is the default on read
        self.assertEqual([s["id"] for s in self.engine.state()["shaders"] if s["vibes"]], ids)
        self.engine.api_set({"action": "vibes", "id": "nxlx-nebula.fs", "on": True}, None, "t")       # anyone may put one in
        self.assertIn("nxlx-nebula.fs", self.engine.vibes_ids())

    def test_a_box_set_up_by_the_first_version_keeps_its_list(self):
        """Its uploads were in the rotation and stay; what was switched off stays off; the heavy two go."""
        self.engine.upload("mine.fs", GOOD)
        self.settings.data["shaders"] = {"dwell": 45, "vary": False, "height": 540, "disabled": ["nxlx-silk.fs"]}
        ids = self.engine.vibes_ids()
        self.assertIn("mine.fs", ids)
        self.assertEqual({"nxlx-silk.fs", "nxlx-nebula.fs", "nxlx-drift.fs"} & set(ids), set())
        self.engine.api_set({"action": "config", "dwell": 50}, None, "t")          # the first save makes the list a real set
        saved = self.settings.data["shaders"]
        self.assertEqual((saved["v"], [e["name"] for e in saved["sets"]], saved["sets"][0]["dwell"], saved["sets"][0]["vary"]), (2, ["Ambient"], 50, False))
        self.assertEqual([r["id"] for r in saved["sets"][0]["shaders"]], ids)
        self.assertEqual(self.engine.vibes_ids(), ids)
        self.assertEqual(SCHEMA, 13)                                  # no settings migration

    def test_a_pack_shader_has_everything_the_projects_own_have(self):
        """Weight, presets, sets, the guard's note and live values work for a third-party pack's shader too; it stays
        out of every rotation until someone puts it in."""
        pack = [s for s in self.engine.state()["shaders"] if s.get("pack") not in ("nxlx", "uploads")]
        self.assertTrue(pack)
        self.assertFalse(any(s["vibes"] for s in pack))
        self.assertTrue(all(s["weight"] in ("light", "medium", "heavy", "") and s["measured"] is None for s in pack))
        s = next(x for x in pack if any(i["type"] != "float" for i in x["inputs"]) and any(i["type"] == "float" and i["max"] > i["min"] for i in x["inputs"]))
        sid = s["id"]
        self.assertTrue(all("value" in i and "varies" in i for i in s["inputs"]))
        self.engine.play(sid)
        f = next(i for i in s["inputs"] if i["type"] == "float" and i["max"] > i["min"])
        self.assertEqual(self.engine.change({"values": {f["name"]: f["max"]}})["values"][f["name"]], f["max"])
        self.pump()
        self.engine.api_presets({"action": "save", "name": "default"}, None, "t")
        show = self.engine.api_set({"action": "set", "op": "add", "name": "Pack", "shaders": [{"id": sid, "preset": "default"}]}, None, "t")["sets"][-1]
        self.assertEqual(self.engine.vibes_ids(show["id"]), [sid])
        self.assertNotIn(sid, self.engine.vibes_ids())                # the active set does not have it
        self.engine.api_set({"action": "heavy", "id": sid, "on": True}, None, "t")
        self.assertEqual(self.engine.vibes_ids(show["id"]), [])       # the guard's note keeps it out of every set
        st = self.engine.api_set({"action": "vibes", "id": sid, "on": True}, None, "t")          # put in by hand: the note goes
        row = next(x for x in st["shaders"] if x["id"] == sid)
        self.assertEqual((row["vibes"], row["heavy"], row["presets"]), (True, None, ["default"]))
        self.assertIn(sid, self.engine.vibes_ids())

    def test_a_refusal_is_remembered_until_the_file_changes(self):
        self.player.vo = "gpu"
        self.engine.upload("mine.fs", GOOD)
        self.engine.api_set({"action": "vibes", "id": "mine.fs", "on": True}, None, "t")
        FakeTap.lines = REFUSAL
        self.assertFalse(self.engine.show("mine.fs")["ok"])
        FakeTap.lines = []
        self.assertNotIn("mine.fs", self.engine.vibes_ids())          # no Vibes run tries it again
        self.assertIn("oops", self.row("mine.fs")["refused"])
        self.api.vibes.start()
        self.api.vibes.stop()
        self.assertNotIn("mine.fs", self.engine.vibes_ids())          # also after another run
        self.engine.upload("mine.fs", GOOD.replace("vec4(k)", "vec4(k * 0.5)"), replace=True)
        os.utime(os.path.join(self.tmp, "shaders", "mine.fs"), (1, 1))
        self.assertIn("mine.fs", self.engine.vibes_ids())             # another file under the same name: it gets its chance
        self.assertIsNone(self.row("mine.fs")["refused"])


class FadeTest(Live):
    def test_the_dip_takes_the_mix_duration_whatever_the_player_costs(self):
        """Fix h: every step used to sleep its full length and then pay the round trip to the player on top."""
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            self.now[0] += seconds
        vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=sleep, rng=random.Random(5), thread=False, log=lambda *_: None)
        real = self.player.source_opacity

        def slow(value, epoch):                                       # each call to the player costs 15 ms
            self.now[0] += 0.015
            return real(value, epoch)
        self.player.source_opacity = slow
        vibes.start()
        vibes.tick()
        self.now[0] += 180
        began = self.now[0]
        self.assertTrue(vibes.tick())
        self.assertAlmostEqual(self.now[0] - began, 1.0 + 0.015, places=2)       # the old way: 1.0 + 20 * 0.015 = 1.3
        self.assertIn(len(slept), (19, 20))                           # the way up has what the way down left of the second
        self.assertTrue(all(0 < s <= 0.06 for s in slept), slept)
        # a change that takes long in the dark (the GPU's first look at a shader) shortens the way up, to half at most
        show = self.engine.show

        def late(*a, **k):
            self.now[0] += 2.0
            return show(*a, **k)
        self.engine.show = late
        del slept[:]
        self.now[0] += 180
        began = self.now[0]
        self.assertTrue(vibes.tick())
        self.assertAlmostEqual(self.now[0] - began, 0.5 + 0.015 + 2.0 + 0.25 + 0.015, places=2)


class ValuesTest(Live):
    """Steps 1 to 3: every input type, the common controls, coalesced changes."""

    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")

    def test_every_input_is_described_for_a_panel(self):
        ins = {i["name"]: i for i in self.row("all.fs")["inputs"]}
        self.assertEqual(ins["level"], {"name": "level", "type": "float", "label": "Level", "min": 0.0, "max": 2.0, "default": 0.5, "value": 0.5, "varies": True})
        self.assertEqual(ins["lit"], {"name": "lit", "type": "bool", "label": "lit", "default": False, "value": False, "varies": False})
        self.assertEqual((ins["mode"]["values"], ins["mode"]["labels"], ins["mode"]["default"]), ([0, 2, 5], ["none", "two", "5"], 2))
        self.assertEqual((ins["count"]["min"], ins["count"]["max"], ins["count"]["value"]), (1, 6, 3))
        self.assertEqual((ins["tint"]["value"], ins["spot"]["min"], ins["spot"]["max"]), ([1.0, 0.5, 0.25, 1.0], [0.0, 0.0], [1.0, 1.0]))
        self.assertEqual((ins["bang"]["type"], ins["bang"]["value"]), ("event", False))
        self.assertFalse(ins["steps"]["varies"])                      # it sits in a condition inside a loop: it changes the work
        st = self.engine.state()
        self.assertEqual(st["controls"]["speed"], {"min": 0.0, "max": 4.0, "default": 1.0})
        self.assertEqual(st["playing"]["controls"], {"speed": 1.0, "hue": 0.0, "brightness": 1.0})
        self.assertEqual(sorted(st["playing"]["values"]), ["count", "level", "lit", "mode", "spot", "steps", "tint"])    # never the event

    def test_each_type_is_written_into_the_shader_and_checked_strictly(self):
        r = self.engine.change({"values": {"level": 9, "lit": True, "mode": 5, "count": 99, "tint": [0.1, 0.2, 0.3], "spot": [2, -1]}})
        self.assertEqual((r["values"]["level"], r["values"]["count"], r["values"]["tint"], r["values"]["spot"]), (2.0, 6, [0.1, 0.2, 0.3, 1.0], [1.0, 0.0]))
        self.assertTrue(self.pump())
        t = self.text()
        for line in ("const float level = 2.0;", "const bool lit = true;", "const int mode = 5;", "const int count = 6;",
                     "const vec4 tint = vec4(0.1, 0.2, 0.3, 1.0);", "const vec2 spot = vec2(1.0, 0.0);", "const bool bang = false;"):
            self.assertIn(line, t)
        self.assertEqual(self.engine.state()["playing"]["values"]["mode"], 5)
        for bad in ({"lit": 1}, {"lit": "true"}, {"mode": 1}, {"mode": 2.5}, {"mode": True}, {"mode": "2"}, {"count": 1.5}, {"count": 10 ** 400},
                    {"tint": [1, 2]}, {"tint": "red"}, {"tint": [0, 0, "0"]}, {"tint": [0, 0, float("nan")]}, {"spot": [1]}, {"spot": [True, 0]},
                    {"spot": {"x": 1}}, {"bang": 1}, {"level": "1"}, {"level": None}, {"nope": 1}, {"level; //!HOOK OUTPUT": 1}, [1], "x"):
            with self.assertRaises(ApiError, msg=bad) as c:
                self.engine.change({"values": bad})
            self.assertEqual(c.exception.status, 400)
        for bad in ({"speed": "2"}, {"speed": True}, {"warp": 1}, {"hue": float("inf")}, [1]):
            with self.assertRaises(ApiError, msg=bad):
                self.engine.change({"controls": bad})
        self.assertIsNone(self.engine.changer.pending())              # nothing of a refused request is kept
        with self.assertRaises(ApiError) as c:
            self.engine.change({"id": "nxlx-tide.fs", "values": {"level": 1}})
        self.assertEqual(c.exception.status, 409)                     # a slider of a shader that is no longer on
        self.assertEqual(t.count("//!"), 5)

    def test_an_event_is_true_for_a_moment_and_is_never_kept(self):
        self.engine.change({"values": {"bang": True}})
        self.assertTrue(self.pump())
        self.assertIn("const bool bang = true;", self.text())
        self.assertNotIn("bang", self.engine.state()["playing"]["values"])
        self.assertFalse(self.pump(0.1))                              # held for a quarter of a second
        self.assertTrue(self.pump(0.2))
        self.assertIn("const bool bang = false;", self.text())
        self.assertFalse(self.pump(5))

    def test_a_dragged_slider_compiles_a_few_times_a_second_and_the_last_value_wins(self):
        before = self.engine.changer.applied
        for n in range(100):                                          # two seconds at 50 values a second
            self.engine.change({"values": {"level": n / 100.0}})
            self.pump(0.02)
        self.engine.change({"values": {"level": 1.75}})
        self.assertLessEqual(self.engine.changer.applied - before, 11)
        self.assertGreaterEqual(self.engine.changer.applied - before, 9)
        self.assertFalse(self.pump(0.0) and self.pump(0.0))
        self.assertTrue(self.pump(L.APPLY_GAP))                       # at the latest one gap after the last value
        self.assertIn("const float level = 1.75;", self.text())
        self.assertEqual(len(self.generated()), 1)                    # every text before it has been removed

    def test_a_change_keeps_the_carrier_the_epoch_and_time(self):
        self.engine.show("all.fs", {"level": 1.0}, hue=40.0, offset=123.0)
        was = dict(self.engine.playing)
        calls = len(self.player.calls)
        self.player.time_pos = 10.0                                   # 300 carrier frames later
        self.engine.change({"values": {"lit": True}})
        self.pump()
        now = self.engine.playing
        self.assertEqual((now["epoch"], now["carrier"], now["anchor"], now["offset"], now["hue"]), (was["epoch"], was["carrier"], 0, 123.0, 40.0))
        self.assertEqual([c[0] for c in self.player.calls[calls:]], ["swap_source"])       # one exchange: no play, no opacity, no restart
        self.assertIn("/ 30.0 + 123.0;", self.text())
        self.assertIn("mat3(", self.text())                           # the palette turn of the round stays

    def test_speed_changes_without_a_jump_and_zero_freezes(self):
        self.engine.show("all.fs", offset=100.0)
        self.player.time_pos = 20.0                                   # frame 600: TIME is 120
        self.engine.change({"controls": {"speed": 3.0}})
        self.pump()
        p = self.engine.playing
        self.assertEqual((p["anchor"], p["offset"], p["controls"]["speed"]), (600, 120.0, 3.0))
        self.assertIn("- %d;" % (600 % 512), self.text())
        self.assertIn("/ 30.0 * 3.0 + 120.0;", self.text())
        self.player.time_pos = 30.0                                   # ten seconds at three times: TIME is 150
        self.engine.change({"controls": {"speed": 0.0}})
        self.pump()
        p = self.engine.playing
        self.assertEqual((p["anchor"], p["offset"]), (900, 150.0))
        self.assertIn("/ 30.0 * 0.0 + 150.0;", self.text())
        self.player.time_pos = 500.0
        self.engine.change({"controls": {"speed": 1.0, "hue": 400, "brightness": -3}})      # kept inside their ranges
        self.pump()
        p = self.engine.playing
        self.assertEqual((p["offset"], p["controls"]), (150.0, {"speed": 1.0, "hue": 180.0, "brightness": 0.0}))
        self.assertIn("c = clamp(c * 0.0, 0.0, 1.0);", self.text())
        # the three bytes of the frame number start again after 6.4 days: the frames since the anchor stay right
        self.engine.playing["anchor"] = S.FRAME_WRAP - 30
        self.player.time_pos = (S.FRAME_WRAP + 60) / 30.0
        self.engine.change({"controls": {"speed": 2.0}})
        self.pump()
        self.assertEqual((self.engine.playing["anchor"], self.engine.playing["offset"]), (60, 153.0))

    def test_playing_the_shader_that_is_on_again_goes_on_from_its_time(self):
        """The panel's sliders send a whole Play for every change. With the carrier's clock that started TIME over."""
        self.engine.show("all.fs", {"level": 1.0}, hue=40.0, offset=100.0)
        self.engine.change({"controls": {"speed": 2.0}})
        self.pump()
        self.player.time_pos = 10.0                                   # 300 frames at twice the pace: TIME is 120
        self.engine.api_play({"id": "all.fs", "values": {"level": 0.25}}, None, "t")
        p = self.engine.playing
        self.assertEqual((p["anchor"], p["offset"], p["hue"], p["controls"]["speed"], p["values"]["level"]), (300, 120.0, 40.0, 2.0, 0.25))
        self.assertIn("/ 30.0 * 2.0 + 120.0;", self.text())
        self.engine.api_play({"id": "all.fs", "controls": {"speed": 1.0}}, None, "t")       # controls sent with it go on top
        self.assertEqual((self.engine.playing["offset"], self.engine.playing["controls"]["speed"]), (120.0, 1.0))
        self.engine.api_play({"id": "nxlx-tide.fs"}, None, "t")      # another shader starts at its own beginning
        p = self.engine.playing
        self.assertEqual((p["anchor"], p["offset"], p["hue"], p["controls"]["speed"]), (300, 0.0, 0.0, 1.0))
        self.engine.api_set({"action": "config", "clock": "frame"}, None, "t")
        self.engine.show("all.fs", offset=7.0)                        # the first version's clock: the offset just stays
        self.player.time_pos = 50.0
        self.engine.api_play({"id": "all.fs", "values": {"level": 0.5}}, None, "t")
        self.assertEqual((self.engine.playing["anchor"], self.engine.playing["offset"]), (None, 7.0))

    def test_a_late_value_never_lands_on_a_clip_or_on_another_shader(self):
        self.engine.change({"values": {"level": 1.0}})
        self.player.play(["/media/clip.mp4"])                        # someone played a clip before the worker came round
        self.assertTrue(self.pump())
        self.assertEqual((self.player.path, self.player.source_shader), ("/media/clip.mp4", None))
        self.engine.play("all.fs")
        self.engine.change({"values": {"level": 1.0}})
        self.engine.play("nxlx-tide.fs")                              # another shader took the screen
        self.pump()
        self.assertNotIn("const float level", self.text())
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-tide.fs")
        with self.assertRaises(ApiError) as c:
            self.engine.change({"values": {"level": 1.0}})
        self.assertEqual(c.exception.status, 400)                     # tide has no such input

    def test_values_the_gpu_refuses_go_back_to_the_ones_before(self):
        self.player.vo = "gpu"
        self.engine.play("all.fs")
        good = self.player.source_shader
        FakeTap.lines = REFUSAL
        self.engine.change({"values": {"mode": 5}})                   # a choice is a new shape of the text: the GPU is asked
        self.pump()
        self.assertEqual((self.player.source_shader, self.generated()), (good, [os.path.basename(good)]))
        st = self.engine.state()
        self.assertEqual((st["playing"]["values"]["mode"], st["error"]["id"]), (2, "all.fs"))
        FakeTap.lines = []
        taps = []
        self.engine._tap = lambda path: taps.append(path) or FakeTap(path)
        for level in (0.1, 0.2, 0.3):                                 # plain numbers are not watched again
            self.engine.change({"values": {"level": level}})
            self.pump()
        self.assertEqual(taps, [])

    def test_a_choice_the_gpu_refused_is_not_sent_to_it_again(self):
        """mode=5 was refused six times out of six, each a trip to the GPU with the lock held and a black flash; a
        MIDI pad that toggles it would repeat that for ever."""
        self.player.vo = "gpu"
        self.engine.play("all.fs")
        taps, test = [], self

        class Tap(FakeTap):                                           # listening takes time on the fake clock too
            def drain(self, seconds):
                test.now[0] += seconds
                return FakeTap.drain(self, seconds)
        FakeTap.lines = REFUSAL
        self.engine._tap = lambda path: taps.append(path) or Tap(path)
        self.engine.change({"values": {"mode": 5}})
        self.pump()
        self.assertEqual(len(taps), 1)
        swaps = len([c for c in self.player.calls if c[0] == "swap_source"])
        for _ in range(5):
            with self.assertRaises(ApiError) as c:                    # the same wish again: answered from memory
                self.engine.change({"values": {"mode": 5}})
            self.assertEqual(c.exception.status, 422)
            self.assertIn("oops", c.exception.message)
            self.pump()
        self.engine.changer.submit(self.engine.playing, {"mode": 5})   # and one that got past the request (two wishes merged)
        self.pump()
        self.assertEqual((len(taps), len([c for c in self.player.calls if c[0] == "swap_source"])), (1, swaps))
        self.assertEqual(self.engine.state()["error"]["message"], "line 12: `oops' undeclared")
        FakeTap.lines = []
        self.engine.change({"values": {"mode": 0}})                   # another choice is tried as before
        self.pump()
        self.assertEqual((len(taps), self.engine.state()["playing"]["values"]["mode"]), (2, 0))
        self.engine.change({"values": {"level": 0.3}})               # and a plain number never was the trouble
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["values"]["level"], 0.3)

    def test_a_change_during_vibes_does_not_end_the_rotation(self):
        vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(1), thread=False, log=lambda *_: None)
        vibes.start()
        vibes.tick()
        sid = vibes.current
        hue = self.engine.playing["hue"]
        self.engine.change({"controls": {"speed": 2.0}, "values": {"speed": 1.5}})
        self.pump()
        self.assertFalse(vibes.tick())
        self.assertEqual((vibes.running, vibes.current, self.engine.playing["hue"], self.engine.playing["controls"]["speed"]), (True, sid, hue, 2.0))

    def test_requests_answer_while_the_gpu_is_busy_with_a_change(self):
        """Nothing a presenter, a controller, OSC, DMX or the schedule sends waits for the engine's lock (the GPU's
        look at a shader can take four seconds). The dwell knob did: 3.99 s were measured."""
        self.engine.api_presets({"action": "save", "name": "kept"}, None, "t")
        self.engine.api_set({"action": "set", "op": "add", "name": "Show", "shaders": ["nxlx-silk.fs"]}, None, "t")
        vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(1), thread=True, log=lambda *_: None)
        self.addCleanup(vibes.stop)
        live = {"id": "midi", "role": "live"}
        calls = [("/api/shaders/values", {"values": {"level": 1.0}}), ("/api/shaders/values", {"control": 1, "level": 64}),
                 ("/api/shaders/values", {"controls": {"speed": 2.0}}), ("/api/shaders/preset", {"name": "kept"}), ("/api/shaders/preset", {"index": 1}),
                 ("/api/shaders/step", {"dir": 1}), ("/api/shaders/step", {"dir": -1}),
                 ("/api/vibes", {"on": True}), ("/api/vibes", {"dwell": 45}), ("/api/vibes", {"next": True}), ("/api/vibes", {"previous": True}),
                 ("/api/vibes", {"on": False}), ("/api/vibes", {"on": True, "set": "Show"}), ("/api/vibes", {"dwell": 60}), ("/api/vibes", {"on": False}),
                 ("/api/blackout", {"on": True}), ("/api/blackout", {"on": False})]
        took, answers = [], []
        with self.engine._lock:                                       # as if the GPU were looking at a shader
            def run():
                for path, body in calls:
                    t0 = time.monotonic()
                    answers.append((path, body, self.api.handle("POST", path, body, live, "midi")[0]))
                    took.append(time.monotonic() - t0)
                t0 = time.monotonic()
                self.api.handle("GET", "/api/shaders", {}, live, "t")
                self.api.handle("GET", "/api/status", {}, live, "t")
                took.append(time.monotonic() - t0)
            t = threading.Thread(target=run)
            t.start()
            t.join(20)
            self.assertFalse(t.is_alive(), "a request waited for the engine's lock: %s" % (answers[-1:],))
        self.assertEqual([a for a in answers if a[2] != 200], [])
        self.assertLess(max(took), 2.0)
        self.assertEqual(self.engine.rotation("Show")["dwell"], 60)   # the dwell knob wrote the running set's time meanwhile

    def test_a_queued_step_or_preset_never_takes_the_screen_back(self):
        """A step (while Vibes is off) and a preset of another shader are put on by the worker a moment later. They
        used to carry no epoch: the shader replaced a clip played meanwhile, came back after a Stop, and ended a
        Vibes run that had started in between."""
        self.engine.api_presets({"action": "save", "name": "kept"}, None, "t")
        self.engine.play("nxlx-silk.fs")
        wishes = (lambda: self.engine.step(1), lambda: self.engine.step(-1), lambda: self.engine.apply_preset({"id": "all.fs", "name": "kept"}))
        for wish in wishes:                                           # a clip played before the worker came round keeps the screen
            self.engine.play("nxlx-silk.fs")
            wish()
            self.player.play(["/media/clip.mp4"])
            self.assertTrue(self.pump())
            self.assertEqual((self.player.path, self.player.source_shader, self.engine.state()["playing"]), ("/media/clip.mp4", None, None))
        for wish in wishes:                                           # after a Stop the screen stays empty
            self.engine.play("nxlx-silk.fs")
            wish()
            self.player.clear()
            self.pump()
            self.assertEqual((self.player.path, self.player.source_shader), (None, None))
        self.assertIsNone(self.engine.error)
        vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(1), thread=False, log=lambda *_: None)
        for tick_first in (True, False):                              # Vibes started in between runs on, whoever comes first
            self.engine.play("nxlx-silk.fs")
            self.engine.step(1)
            vibes.start()
            if tick_first:
                vibes.tick()
            self.pump()
            vibes.tick()
            self.assertEqual((vibes.running, vibes.status()["last"]), (True, None))
            self.assertEqual(self.engine.state()["playing"]["id"], vibes.current)
            vibes.stop()
        # a preset of another shader ends the rotation when it is asked for, not when the worker comes round
        vibes.start()
        vibes.tick()
        self.engine.apply_preset({"id": "all.fs", "name": "kept"})
        self.assertEqual((vibes.running, vibes.status()["last"]["message"]), (False, "ended: a shader was chosen by hand"))
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["id"], "all.fs")
        # the module switched off with a step waiting: nothing is shown and no error is noted
        self.engine.step(1)
        self.api.set_module("shaders", {"enabled": False}, None, "t")
        self.assertFalse(self.pump())
        self.assertEqual((self.engine.error, self.player.source_shader), (None, None))
        self.api.registry.set_enabled("shaders", True)
        self.engine.play("nxlx-silk.fs")
        self.engine.changer.show({"id": "all.fs", "epoch": self.player.source_epoch})         # and one that slipped past the switch
        self.api.registry.set_enabled("shaders", False)
        self.pump()
        self.assertIsNone(self.engine.error)
        # two steps before the worker comes round are still two steps
        self.api.registry.set_enabled("shaders", True)
        ids = self.engine.vibes_ids()
        self.engine.play(ids[0])
        self.engine.step(1)
        self.engine.step(1)
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["id"], ids[2])


class PresetTest(Live):
    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")

    def save(self, name, **values):
        self.engine.change({"values": values})
        self.pump()
        return self.engine.api_presets({"action": "save", "name": name}, None, "t")

    def test_save_list_apply_rename_delete(self):
        self.engine.change({"controls": {"speed": 2.0}})
        st = self.save("Bright", level=1.5, lit=True)
        self.assertEqual((self.row("all.fs")["presets"], st["playing"]["preset"]), (["Bright"], "Bright"))
        stored = self.settings.data["shaders"]["presets"]["all.fs"][0]
        self.assertEqual((stored["values"]["level"], stored["values"]["lit"], stored["controls"]["speed"]), (1.5, True, 2.0))
        self.assertNotIn("bang", stored["values"])
        self.save("Dim", level=0.1, lit=False)
        self.engine.change({"values": {"level": 0.7}})
        self.pump()
        self.assertIsNone(self.engine.state()["playing"]["preset"])   # changed by hand since
        self.assertEqual(self.engine.apply_preset({"name": "bright"})["preset"], "Bright")      # any letter case
        self.pump()
        p = self.engine.state()["playing"]
        self.assertEqual((p["values"]["level"], p["values"]["lit"], p["controls"]["speed"], p["preset"]), (1.5, True, 2.0, "Bright"))
        self.assertEqual(self.engine.apply_preset({"index": 2})["preset"], "Dim")
        self.engine.api_presets({"action": "rename", "id": "all.fs", "name": "Dim", "to": "Low"}, None, "t")
        self.engine.api_presets({"action": "delete", "id": "all.fs", "name": "Bright"}, None, "t")
        self.assertEqual(self.row("all.fs")["presets"], ["Low"])
        self.engine.api_presets({"action": "save", "name": "low"}, None, "t")      # the same name again writes over it
        self.assertEqual(self.row("all.fs")["presets"], ["low"])
        self.engine.api_presets({"action": "delete", "id": "all.fs", "name": "low"}, None, "t")
        self.assertNotIn("presets", {k: v for k, v in self.settings.data["shaders"].items() if v})

    def test_limits_and_refusals(self):
        for n in range(L.MAX_PRESETS):
            self.engine.api_presets({"action": "save", "name": "p%d" % n}, None, "t")
        for body, status in (({"action": "save", "name": "one more"}, 409), ({"action": "save", "name": ""}, 400), ({"action": "save", "name": "x" * 41}, 400),
                             ({"action": "save", "name": "a\nb"}, 400), ({"action": "save", "name": 5}, 400), ({"action": "save", "name": " x"}, 400),
                             ({"action": "save", "name": "x", "id": "nxlx-tide.fs"}, 409),
                             ({"action": "rename", "id": "all.fs", "name": "p0", "to": "p1"}, 409), ({"action": "rename", "id": "all.fs", "name": "nope", "to": "x"}, 404),
                             ({"action": "rename", "id": "../x", "name": "p0", "to": "x"}, 400), ({"action": "delete", "id": "all.fs", "name": "nope"}, 404),
                             ({"action": "delete", "id": "nope.fs", "name": "p0"}, 404), ({"action": "wipe"}, 400)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.engine.api_presets(body, None, "t")
            self.assertEqual(c.exception.status, status, body)
        for body, status in (({"name": "nope"}, 404), ({"index": 0}, 400), ({"index": True}, 400), ({"index": 17}, 400), ({}, 400), ({"id": "nope.fs", "name": "p0"}, 404)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.engine.apply_preset(body)
            self.assertEqual(c.exception.status, status, body)
        self.player.clear()
        with self.assertRaises(ApiError) as c:                        # nothing on the screen: nothing to keep
            self.engine.api_presets({"action": "save", "name": "x"}, None, "t")
        self.assertEqual(c.exception.status, 409)

    def test_the_default_preset_is_what_play_and_vibes_use(self):
        self.save("default", level=1.25, mode=5)
        self.engine.play("nxlx-tide.fs")
        self.engine.play("all.fs")
        self.assertIn("const float level = 1.25;", self.text())
        self.assertEqual(self.engine.state()["playing"]["preset"], "default")
        self.assertEqual(next(i["value"] for i in self.row("all.fs")["inputs"] if i["name"] == "mode"), 5)
        self.engine.play("all.fs", {"level": 0.3})                    # values sent with Play go on top
        self.assertIn("const float level = 0.3;", self.text())
        self.assertIn("const int mode = 5;", self.text())
        self.save("other", level=2.0)
        self.engine.api_set({"action": "set", "op": "add", "name": "Show", "vary": False, "shaders": [{"id": "all.fs", "preset": "other"}]}, None, "t")
        vibes = self.api.vibes
        vibes._clock = lambda: self.now[0]
        vibes.start("Show")
        vibes.tick()
        self.assertIn("const float level = 2.0;", self.text())
        self.engine.api_presets({"action": "delete", "id": "all.fs", "name": "other"}, None, "t")
        vibes.skip()
        vibes.tick()                                                  # the preset the set names is gone: the default one
        self.assertIn("const float level = 1.25;", self.text())

    def test_a_stored_value_the_file_no_longer_takes_is_left_out(self):
        self.settings.data["shaders"] = {"v": 2, "presets": {"all.fs": [{"name": "default", "values": {"level": 99, "gone": 1, "mode": 3, "lit": True, "bang": True}}]}}
        self.engine.play("all.fs")
        p = self.engine.state()["playing"]
        self.assertEqual((p["values"]["level"], p["values"]["mode"], p["values"]["lit"]), (2.0, 2, True))
        self.assertIn("const bool bang = false;", self.text())
        self.settings.data["shaders"]["presets"] = {"all.fs": [{"name": "x", "values": {"level; //!HOOK": 1}}]}      # edited by hand: as if not there
        self.assertNotIn("presets", self.engine.config())
        self.engine.play("all.fs")

    def test_one_damaged_row_does_not_cost_the_rest(self):
        """One bad row dropped the whole key on read, and the next save wrote the section without it: every preset
        and every set was gone."""
        self.save("good", level=1.0)
        self.save("also good", level=0.2)
        self.engine.api_set({"action": "set", "op": "add", "name": "Show", "shaders": ["all.fs"]}, None, "t")
        self.engine.api_set({"action": "heavy", "id": "nxlx-nebula.fs", "on": True}, None, "t")
        data = self.settings.data["shaders"]
        data["presets"]["all.fs"].insert(1, {"name": "", "values": {}})                       # damaged by hand
        data["presets"]["all.fs"].append({"name": "GOOD", "values": {}})                      # the same name twice
        data["presets"]["../x"] = [{"name": "a"}]
        data["presets"]["nxlx-tide.fs"] = "not a list"
        data["sets"].insert(0, {"id": "zz", "name": "broken"})
        data["sets"].append({"id": data["sets"][1]["id"], "name": "same id"})
        data["heavy"]["bad.fs"] = "nonsense"
        cfg = self.engine.config()
        self.assertEqual([p["name"] for p in cfg["presets"]["all.fs"]], ["good", "also good"])
        self.assertEqual((sorted(cfg["presets"]), [e["name"] for e in cfg["sets"]], sorted(cfg["heavy"])), (["all.fs"], ["Ambient", "Show"], ["nxlx-nebula.fs"]))
        self.engine.api_set({"action": "config", "dwell": 33}, None, "t")                     # any save
        saved = self.settings.data["shaders"]
        self.assertEqual(([p["name"] for p in saved["presets"]["all.fs"]], [e["name"] for e in saved["sets"]], sorted(saved["heavy"])),
                         (["good", "also good"], ["Ambient", "Show"], ["nxlx-nebula.fs"]))
        # a key that cannot be read at all is left as it is, not written over with nothing
        for key, junk in (("presets", "all of them"), ("sets", {"not": "a list"}), ("heavy", ["a.fs"])):
            self.settings.data["shaders"][key] = junk
        self.assertEqual([k for k in ("presets", "heavy") if k in self.engine.config()], [])
        self.engine.api_set({"action": "config", "dwell": 34}, None, "t")
        self.assertEqual((self.settings.data["shaders"]["presets"], self.settings.data["shaders"]["heavy"]), ("all of them", ["a.fs"]))
        # more marks than the cap: the first ones are read, none is an error
        self.settings.data["shaders"]["heavy"] = {"s%d.fs" % n: {"at": "x", "drops": 3, "height": 720} for n in range(S.MAX_UPLOADS + 80)}
        self.assertEqual(len(self.engine.config()["heavy"]), S.MAX_UPLOADS + 64)

    def test_deleting_a_shader_takes_its_presets_and_its_place_in_the_sets(self):
        self.save("mine", level=1.0)
        self.engine.api_set({"action": "vibes", "id": "all.fs", "on": True}, None, "t")
        self.engine.delete("all.fs")
        saved = self.settings.data["shaders"]
        self.assertEqual((saved["presets"], [r for e in saved["sets"] for r in e["shaders"] if r["id"] == "all.fs"]), ({}, []))


class SetsTest(Live):
    def setUp(self):
        super().setUp()
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(4), thread=False, log=lambda *_: None)

    def add(self, name, shaders, **kw):
        st = self.engine.api_set(dict({"action": "set", "op": "add", "name": name, "shaders": shaders}, **kw), None, "t")
        return next(e for e in st["sets"] if e["name"] == name)

    def rounds(self, n):
        out = []
        for _ in range(n):
            self.assertTrue(self.vibes.tick())
            out.append(self.vibes.current)
            self.now[0] += 4000
        return out

    def test_the_first_set_is_there_without_anything_saved(self):
        st = self.engine.state()
        self.assertEqual([(e["id"], e["name"], e["dwell"], e["vary"], e["order"], len(e["shaders"])) for e in st["sets"]], [("00000000", "Ambient", 180, True, "shuffle", 8)])
        self.assertEqual(st["active"], "00000000")
        self.assertNotIn("shaders", self.settings.data)

    def test_add_update_activate_delete(self):
        show = self.add("Show", ["nxlx-prism.fs", {"id": "nxlx-silk.fs"}, "nxlx-nebula.fs"], dwell=30, vary=False, order="listed")
        self.assertEqual((show["dwell"], show["vary"], show["order"], [r["id"] for r in show["shaders"]]), (30, False, "listed", ["nxlx-prism.fs", "nxlx-silk.fs", "nxlx-nebula.fs"]))
        st = self.engine.state()
        self.assertEqual((st["active"], st["config"]["dwell"], len(st["sets"])), ("00000000", 180, 2))       # adding one does not make it active
        st = self.engine.api_set({"action": "set", "op": "activate", "id": show["id"]}, None, "t")
        self.assertEqual((st["active"], st["config"]["dwell"], st["config"]["vary"]), (show["id"], 30, False))
        self.assertEqual([s["id"] for s in st["shaders"] if s["vibes"]], ["nxlx-nebula.fs", "nxlx-prism.fs", "nxlx-silk.fs"])
        st = self.engine.api_set({"action": "config", "dwell": 40}, None, "t")     # the panel's dwell and variation are the active set's
        self.assertEqual(next(e for e in st["sets"] if e["id"] == show["id"])["dwell"], 40)
        self.assertEqual(st["sets"][0]["dwell"], 180)
        st = self.engine.api_set({"action": "vibes", "id": "nxlx-silk.fs", "on": False}, None, "t")          # and so is the switch per shader
        self.assertEqual([r["id"] for r in next(e for e in st["sets"] if e["id"] == show["id"])["shaders"]], ["nxlx-prism.fs", "nxlx-nebula.fs"])
        st = self.engine.api_set({"action": "set", "op": "update", "id": show["id"], "name": "Late show", "order": "shuffle"}, None, "t")
        self.assertEqual([e["name"] for e in st["sets"]], ["Ambient", "Late show"])
        st = self.engine.api_set({"action": "set", "op": "delete", "id": show["id"]}, None, "t")
        self.assertEqual((st["active"], [e["name"] for e in st["sets"]], st["config"]["dwell"]), ("00000000", ["Ambient"], 180))

    def test_sets_are_checked(self):
        show = self.add("Show", ["nxlx-prism.fs"])
        for body, status in (({"op": "add", "name": "show"}, 400), ({"op": "add", "name": ""}, 400), ({"op": "add", "name": "x" * 41}, 400),
                             ({"op": "add", "name": "A", "shaders": ["../x.fs"]}, 400), ({"op": "add", "name": "A", "shaders": ["a.fs", "a.fs"]}, 400),
                             ({"op": "add", "name": "A", "shaders": "a.fs"}, 400), ({"op": "add", "name": "A", "shaders": [{"id": "a.fs", "preset": 5}]}, 400),
                             ({"op": "add", "name": "A", "dwell": 5}, 400), ({"op": "add", "name": "A", "vary": 1}, 400), ({"op": "add", "name": "A", "order": "random"}, 400),
                             ({"op": "add", "name": "A", "shaders": ["a.fs"] * 0 + ["s%d.fs" % n for n in range(L.MAX_SET_ENTRIES + 1)]}, 400),
                             ({"op": "update", "id": "ffffffff", "name": "B"}, 404), ({"op": "update", "id": show["id"], "name": "ambient"}, 400),
                             ({"op": "delete", "id": "ffffffff"}, 404), ({"op": "activate", "id": "nope"}, 404), ({"op": "format"}, 400)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.engine.api_set(dict({"action": "set"}, **body), None, "t")
            self.assertEqual(c.exception.status, status, body)
        self.assertEqual(len(self.engine.state()["sets"]), 2)         # nothing of a refused change was kept
        for n in range(L.MAX_SETS - 2):
            self.add("set %d" % n, [])
        with self.assertRaises(ApiError) as c:
            self.add("one more", [])
        self.assertEqual(c.exception.status, 409)
        for e in self.engine.state()["sets"][1:]:
            self.engine.api_set({"action": "set", "op": "delete", "id": e["id"]}, None, "t")
        with self.assertRaises(ApiError) as c:                        # one always remains
            self.engine.api_set({"action": "set", "op": "delete", "id": "00000000"}, None, "t")
        self.assertEqual(c.exception.status, 409)

    def test_vibes_runs_the_active_set_or_the_one_named(self):
        show = self.add("Show", ["nxlx-prism.fs", "nxlx-silk.fs", "nxlx-ember.fs"], dwell=30, vary=False, order="listed")
        st = self.vibes.api_vibes({"on": True}, None, "t")
        self.assertEqual(st["set"], {"id": "00000000", "name": "Ambient"})
        self.assertNotIn(self.rounds(1)[0], ())
        self.vibes.stop()
        st = self.vibes.api_vibes({"on": True, "set": "show"}, None, "t")      # by name, any letter case; or by id
        self.assertEqual(st["set"], {"id": show["id"], "name": "Show"})
        self.assertEqual(self.rounds(5), ["nxlx-prism.fs", "nxlx-silk.fs", "nxlx-ember.fs", "nxlx-prism.fs", "nxlx-silk.fs"])    # in the set's own order
        self.assertEqual(self.vibes.status()["next_in"] is not None and self.engine.playing["hue"], 0.0)     # no variation in this set
        self.assertEqual(self.engine.state()["active"], "00000000")   # naming a set for one run does not change the active one
        self.vibes.tick()
        self.assertEqual(self.vibes.status()["next_in"], 30)          # the set's own dwell
        self.vibes.set_dwell(60)                                      # a controller's dwell knob turns the running set's
        self.assertEqual(self.engine.rotation(show["id"])["dwell"], 60)
        self.assertEqual(self.engine.rotation()["dwell"], 180)
        self.vibes.stop()
        for bad, status in (("nope", 404), (5, 400)):
            with self.assertRaises(ApiError) as c:
                self.vibes.api_vibes({"on": True, "set": bad}, None, "t")
            self.assertEqual(c.exception.status, status)
        empty = self.add("Empty", [])
        with self.assertRaises(ApiError) as c:
            self.vibes.start(empty["id"])
        self.assertEqual(c.exception.status, 409)

    def test_the_one_before_and_the_next_in_vibes_and_without_it(self):
        show = self.add("Show", ["nxlx-prism.fs", "nxlx-silk.fs", "nxlx-ember.fs"], order="listed")
        self.engine.api_set({"action": "set", "op": "activate", "id": show["id"]}, None, "t")
        self.vibes.start()
        self.assertEqual(self.rounds(2), ["nxlx-prism.fs", "nxlx-silk.fs"])
        self.vibes.api_vibes({"previous": True}, None, "t")
        self.vibes.tick()
        self.assertEqual(self.vibes.current, "nxlx-prism.fs")
        self.assertEqual(self.engine.step(1)["running"], True)        # while Vibes runs, a step is Vibes' own
        self.vibes.tick()
        self.assertEqual(self.vibes.current, "nxlx-silk.fs")
        self.vibes.stop()
        # without Vibes: the neighbour in the active set, put on by the worker (the request does not wait for the GPU)
        self.engine.play("nxlx-silk.fs")
        self.assertEqual(self.engine.step(1), {"ok": True, "id": "nxlx-ember.fs"})
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-silk.fs")
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-ember.fs")
        self.engine.step(1)
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-prism.fs")       # round the end
        self.engine.step(-1)
        self.engine.step(-1)                                          # two presses before the worker came round: two steps
        self.pump()
        self.assertEqual(self.engine.state()["playing"]["id"], "nxlx-silk.fs")
        with self.assertRaises(ApiError):
            self.engine.step(2)

    def test_schedule_osc_and_autostart_can_name_a_set_or_use_the_active_one(self):
        show = self.add("Show", ["nxlx-prism.fs"])
        self.assertEqual(osc.translate("/pvj/vibes/set", ["Show"]), ("/api/vibes", {"on": True, "set": "Show"}))
        self.assertIsNone(osc.translate("/pvj/vibes/set", [5]))
        self.assertEqual(osc.translate("/pvj/vibes/previous", [1]), ("/api/vibes", {"previous": True}))
        self.assertEqual(osc.translate("/pvj/vibes", []), ("/api/vibes", {"on": True}))
        entry = {"time": "08:00", "days": [0], "action": "vibes", "set": show["id"]}
        clean = scheduler.validate({"enabled": True, "entries": [entry]})["entries"][0]
        self.assertEqual(clean["set"], show["id"])
        self.assertNotIn("set", scheduler.validate({"enabled": True, "entries": [dict(entry, set=None)]})["entries"][0])
        with self.assertRaises(scheduler.ScheduleError):
            scheduler.validate({"enabled": True, "entries": [dict(entry, set="Show; rm")]})
        sched = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        sched._execute(clean, datetime.datetime.now())
        self.assertEqual(self.vibes.status()["set"]["name"], "Show")
        self.vibes.stop()
        sched._execute(dict(clean, set=None), datetime.datetime.now())
        self.assertEqual(self.vibes.status()["set"]["name"], "Ambient")


class GuardTest(Live):
    def setUp(self):
        super().setUp()
        self.notes = []
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(4), thread=False,
                                              log=self.notes.append)

    def second(self, drops):
        self.now[0] += 1
        self.player.drops += drops

    def test_a_shader_chosen_by_hand_is_only_reported(self):
        self.engine.play("nxlx-nebula.fs")
        for _ in range(2):                                            # the first seconds after it comes on are not counted
            self.second(9)
            self.assertIsNone(self.engine.state()["playing"]["load"])
        self.second(0)
        self.engine.state()
        for _ in range(10):
            self.second(4)
            st = self.engine.state()
        self.assertEqual((st["playing"]["load"], st["playing"]["drops_per_second"]), ("heavy", 4.0))
        self.assertEqual((st["playing"]["id"], self.row("nxlx-nebula.fs")["heavy"]), ("nxlx-nebula.fs", None))     # nothing is taken off or noted
        self.second(1)
        self.assertEqual(self.engine.state()["playing"]["load"], "tight")
        self.second(0)
        self.assertEqual(self.engine.state()["playing"]["load"], "ok")

    def test_vibes_moves_on_from_a_shader_that_keeps_dropping_frames_and_leaves_it_out(self):
        self.vibes.start()
        self.vibes.tick()
        bad = self.vibes.current
        for _ in range(4):
            self.second(0)
            self.assertFalse(self.vibes.tick())
        for n in range(12):
            self.second(3)
            if self.vibes.tick():
                break
        self.assertLess(n, 9)                                         # after about six seconds over the limit, not after the dwell time
        self.assertNotEqual(self.vibes.current, bad)
        row = self.row(bad)
        self.assertEqual((row["vibes"], row["heavy"]["drops"], row["heavy"]["height"]), (True, 3.0, 720))
        self.assertNotIn(bad, self.engine.vibes_ids())
        self.assertTrue(any("left out" in n and bad in n for n in self.notes))
        self.assertEqual(self.settings.data["shaders"]["heavy"][bad]["drops"], 3.0)       # kept across a restart
        self.vibes.stop()
        self.vibes.start()
        seen = set()
        for _ in range(16):
            self.now[0] += 200
            self.vibes.tick()
            seen.add(self.vibes.current)
        self.assertNotIn(bad, seen)
        st = self.engine.api_set({"action": "heavy", "id": bad, "on": False}, None, "t")        # until someone puts it back
        self.assertIn(bad, self.engine.vibes_ids())
        self.assertIsNone(next(s for s in st["shaders"] if s["id"] == bad)["heavy"])

    def test_a_box_that_drops_frames_whatever_plays_does_not_mark_every_shader(self):
        """Three dropped frames a second from any cause marked all eight shaders in 73 seconds, for good, and Vibes
        ended saying no shader was switched on."""
        self.vibes.start()
        self.vibes.tick()
        for _ in range(200):
            self.second(3)
            self.vibes.tick()
            if not self.vibes.running:
                break
        self.assertFalse(self.vibes.running)
        self.assertEqual(self.vibes.status()["last"]["message"], "ended: the box is dropping frames whatever plays: check the picture detail")
        self.assertEqual((self.engine.config().get("heavy"), len(self.engine.vibes_ids())), (None, 8))     # the two marks were taken back
        self.assertIsNotNone(self.engine.state()["playing"])          # the shader that was on stays on
        self.vibes.start()                                            # and it starts again
        self.assertTrue(self.vibes.tick())
        # one heavy shader between healthy ones is still marked
        bad = self.vibes.current
        for _ in range(12):
            self.second(3)
            if self.vibes.tick():
                break
        for _ in range(12):
            self.second(0)
            self.vibes.tick()
        self.assertEqual(sorted(self.engine.config()["heavy"]), [bad])
        self.vibes.stop()

    def test_a_heavy_mark_belongs_to_its_height_and_board(self):
        self.engine.note_heavy("nxlx-tide.fs", {"drops_per_second": 3.3})
        mark = self.settings.data["shaders"]["heavy"]["nxlx-tide.fs"]
        self.assertEqual((mark["height"], mark["board"], mark["drops"]), (720, "x86", 3.3))
        self.assertNotIn("nxlx-tide.fs", self.engine.vibes_ids())
        self.engine.api_set({"action": "config", "height": 540}, None, "t")        # fewer lines: it gets another chance
        self.assertIn("nxlx-tide.fs", self.engine.vibes_ids())
        self.assertIsNone(self.row("nxlx-tide.fs")["heavy"])
        self.engine.api_set({"action": "config", "height": 1080}, None, "t")       # more lines: the mark holds
        self.assertNotIn("nxlx-tide.fs", self.engine.vibes_ids())
        self.assertEqual(self.row("nxlx-tide.fs")["heavy"]["height"], 720)
        self.api.board = {"kind": "pi5", "model": "t"}                # the settings on another kind of board: not its mark
        self.assertIn("nxlx-tide.fs", self.engine.vibes_ids())
        saved = json.loads(json.dumps(self.settings.data["shaders"]))

        class Care:
            api = self.api
        self.assertEqual(boxcare.check_shaders(saved, Care)["heavy"], {})          # and an import does not bring it along
        self.api.board = {"kind": "x86", "model": "t"}
        self.assertEqual(sorted(boxcare.check_shaders(saved, Care)["heavy"]), ["nxlx-tide.fs"])
        self.assertEqual(sorted(boxcare.check_shaders(dict(saved, heavy={"a.fs": {"at": "x"}}), Care)["heavy"]), ["a.fs"])     # a mark of the first kind, with no board
        with self.assertRaises(ValueError):
            boxcare.check_shaders(dict(saved, heavy={"a.fs": {"board": "pi4; rm"}}), Care)
        # every shader of a set left out: the start says so, not that nothing is switched on
        for sid in self.engine.vibes_ids():
            self.engine.note_heavy(sid)
        with self.assertRaises(ApiError) as c:
            self.vibes.start()
        self.assertEqual(c.exception.status, 409)
        self.assertIn("left out on this box", c.exception.message)

    def test_a_short_burst_or_a_change_is_not_taken_for_a_heavy_shader(self):
        self.vibes.start()
        self.vibes.tick()
        first = self.vibes.current
        for _ in range(4):
            self.second(0)
            self.vibes.tick()
        for pattern in ((5, 5, 5, 0), (9, 0, 0, 0)) * 4:              # never six seconds in a row
            for d in pattern:
                self.second(d)
                self.assertFalse(self.vibes.tick())
        for _ in range(5):
            self.second(3)
            self.vibes.tick()
        self.engine.change({"values": {"speed": 1.0}})               # a change is a new text: the count starts over
        self.engine.changer.pump()
        for _ in range(4):
            self.second(3)
            self.assertFalse(self.vibes.tick())
        self.assertEqual((self.vibes.current, self.engine.config().get("heavy")), (first, None))

    def test_the_guard_can_be_switched_off(self):
        self.engine.api_set({"action": "config", "guard": False}, None, "t")
        self.vibes.start()
        self.vibes.tick()
        first = self.vibes.current
        for _ in range(30):
            self.second(9)
            self.assertFalse(self.vibes.tick())
        self.assertEqual(self.vibes.current, first)
        self.assertNotIn("load", self.engine.state()["playing"])
        for bad in ({"guard": 1}, {"clock": "sundial"}):
            with self.assertRaises(ApiError):
                self.engine.api_set(dict({"action": "config"}, **bad), None, "t")

    def test_variation_leaves_alone_what_changes_the_work_and_drops_the_palette_turn_when_frames_drop(self):
        self.engine.upload("all.fs", ALL)
        show = self.engine.api_set({"action": "set", "op": "add", "name": "One", "shaders": ["all.fs"]}, None, "t")["sets"][-1]
        self.vibes.start(show["id"])
        steps, levels, hues = set(), set(), []
        for n in range(12):
            self.assertTrue(self.vibes.tick())
            steps.add(self.engine.playing["values"].get("steps", 3.0))
            levels.add(self.engine.playing["values"]["level"])
            hues.append(self.engine.playing["hue"])
            if n == 5:                                                # a few dropped frames, well under the limit
                for _ in range(8):
                    self.second(1)
                    self.assertFalse(self.vibes.tick())
            self.now[0] += 200
        self.assertEqual(steps, {3.0})                                # the loop's own input is never varied
        self.assertGreater(len(levels), 6)
        self.assertTrue(all(hues[:6]) and not any(hues[6:]), hues)    # no palette turn for it once it was seen dropping frames
        self.assertEqual(L.work_inputs(S.parse(ALL)), {"steps"})

    def test_the_pis_gpu_figures_are_read_when_they_are_there(self):
        table = ["queue\ttimestamp\tjobs\truntime\nbin\t1000000000\t10\t100\nrender\t1000000000\t30\t200000000\nnonsense line\n",
                 "queue\ttimestamp\tjobs\truntime\nrender\t2000000000\t60\t950000000\n"]
        path = os.path.join(self.tmp, "gpu_stats")
        with open(path, "w") as f:
            f.write(table[0])
        self.assertEqual(L.v3d_stats(path)["render"], (1000000000, 30, 200000000))
        self.assertIsNone(L.v3d_stats(os.path.join(self.tmp, "nothing-here")))
        self.engine.guard._stats = lambda: L.v3d_stats(path)
        self.engine.play("nxlx-silk.fs")
        self.second(0)
        self.assertIsNone(self.engine.state()["gpu"])                 # one look is not a rate yet
        with open(path, "w") as f:
            f.write(table[1])
        self.second(0)
        self.assertEqual(self.engine.state()["gpu"], {"busy_percent": 75.0, "render_jobs_per_second": 30.0})
        self.second(0)
        with open(path, "w") as f:
            f.write("garbage")
        self.second(0)
        self.assertIsNone(self.engine.state()["gpu"])                 # a file in another form is not an error


class ControllerTest(Live):
    """Step 6: performing from a MIDI controller, through the same API calls as the panel."""

    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        self.t = [50.0]
        self.calls = []

        def do(path, body):
            st, payload = self.api.handle("POST", path, body, midi.MIDI_DEVICE, "midi")
            self.calls.append((path, body, st))
            return st == 200
        names = ["shader_control_%d" % n for n in range(1, 9)] + ["shader_speed", "shader_prev", "shader_next"] + ["shader_preset_%d" % n for n in range(1, 9)]
        entries = [midi.validate_entry({"source": "*", "kind": "cc", "channel": 0, "number": n, "action": a}) for n, a in enumerate(names[:9])]
        entries += [midi.validate_entry({"source": "*", "kind": "note", "channel": 0, "number": n, "action": a}) for n, a in enumerate(names)]
        self.m = MidiMapper(do, entries, {"blackout": False}, clock=lambda: self.t[0])

    def cc(self, number, value):
        self.t[0] += 1
        self.m.message("nano", ("cc", 0, number, value))
        self.pump()

    def note(self, number):
        self.t[0] += 1
        self.m.message("pads", ("on", 0, number, 100))
        self.m.message("pads", ("off", 0, number, 0))
        self.pump()

    def values(self):
        return self.engine.state()["playing"]

    def test_a_knob_follows_the_nth_input_whatever_its_type(self):
        self.cc(0, 127)
        self.assertEqual(self.values()["values"]["level"], 2.0)       # a number spreads over MIN to MAX
        self.cc(0, 0)
        self.assertEqual(self.values()["values"]["level"], 0.0)
        self.cc(1, 100)
        self.assertEqual(self.values()["values"]["lit"], True)        # a switch is on from 64 up
        self.cc(1, 10)
        self.assertEqual(self.values()["values"]["lit"], False)
        for value, want in ((0, 0), (60, 2), (127, 5)):               # a choice by position
            self.cc(2, value)
            self.assertEqual(self.values()["values"]["mode"], want)
        self.cc(3, 127)
        self.assertEqual(self.values()["values"]["count"], 6)
        self.cc(4, 127)                                               # the fifth is the event (colours and points have no knob)
        self.assertIn("const bool bang = true;", self.text())
        self.cc(5, 64)
        self.assertEqual(round(self.values()["values"]["steps"], 2), 3.52)
        self.cc(6, 64)                                                # this shader has six: the seventh does nothing
        self.assertEqual(self.calls[-1][2], 404)
        self.cc(8, 127)
        self.assertEqual(self.values()["controls"]["speed"], 4.0)
        self.cc(8, 32)
        self.assertEqual(self.values()["controls"]["speed"], 1.01)
        self.assertTrue(all(path.startswith("/api/shaders/") for path, _, _ in self.calls))

    def test_a_pad_toggles_steps_fires_or_resets(self):
        self.note(1)
        self.assertEqual(self.values()["values"]["lit"], True)        # a switch toggles
        self.note(1)
        self.assertEqual(self.values()["values"]["lit"], False)
        seen = []
        for _ in range(4):                                            # a choice steps on, round the end
            self.note(2)
            seen.append(self.values()["values"]["mode"])
        self.assertEqual(seen, [5, 0, 2, 5])
        self.cc(0, 127)
        self.note(0)
        self.assertEqual(self.values()["values"]["level"], 0.5)       # a number goes back to the file's own value
        self.note(4)
        self.assertIn("const bool bang = true;", self.text())

    def test_presets_and_the_shader_before_and_after(self):
        self.engine.api_presets({"action": "save", "name": "one"}, None, "t")
        self.cc(0, 127)
        self.engine.api_presets({"action": "save", "name": "two"}, None, "t")
        self.note(11)                                                 # preset 1
        self.assertEqual((self.values()["values"]["level"], self.values()["preset"]), (0.5, "one"))
        self.note(12)
        self.assertEqual((self.values()["values"]["level"], self.values()["preset"]), (2.0, "two"))
        self.note(13)
        self.assertEqual(self.calls[-1][2], 404)                      # there is no third
        self.note(10)                                                 # the next shader of the active set
        first = self.values()["id"]
        self.assertIn(first, self.engine.vibes_ids())
        self.note(10)
        self.note(9)                                                  # and back
        self.assertEqual(self.values()["id"], first)

    def test_a_controller_is_a_presenter_and_cannot_save_or_edit(self):
        for path, body in (("/api/shaders/presets", {"action": "save", "name": "x"}), ("/api/shaders", {"action": "set", "op": "add", "name": "x"}),
                           ("/api/shaders", {"action": "heavy", "id": "all.fs", "on": False})):
            self.assertEqual(self.api.handle("POST", path, body, midi.MIDI_DEVICE, "midi")[0], 403)
        for kind in ("note", "cc", "program"):
            midi.validate_entry({"source": "*", "kind": kind, "channel": 0, "number": 1, "action": "shader_control_8"})
        with self.assertRaises(midi.MidiError):
            midi.validate_entry({"source": "*", "kind": "cc", "channel": 0, "number": 1, "action": "shader_control_9"})
        with self.assertRaises(midi.MidiError):
            midi.validate_entry({"source": "*", "kind": "program", "channel": 0, "number": 1, "action": "shader_speed"})

    def test_a_knob_sweep_never_waits_for_the_gpu(self):
        """50 messages a second for two seconds: each is answered at once, the GPU compiles a few times a second."""
        show = self.engine.adjust
        slow = []

        def adjust(job):
            slow.append(job)
            return show(job)
        self.engine.adjust = adjust
        before = self.engine.changer.applied
        for n in range(100):
            self.t[0] += 0.02
            self.now[0] += 0.02
            self.m.message("nano", ("cc", 0, 0, n))
            for path, body in self.m.flush_calls():
                self.m.do(path, body)
            self.engine.changer.pump()
        self.assertLessEqual(self.engine.changer.applied - before, 11)
        self.assertLessEqual(len(self.calls), 45)                     # the mapper thins a sweep to 20 a second
        self.pump()
        self.assertEqual(round(self.values()["values"]["level"], 3), round(2.0 * 99 / 127, 3))


class RolesAndSettingsTest(Live):
    """Step 8: who may do what, and that every new key goes round through a settings file."""

    def test_who_may_do_what(self):
        full, _ = self.pair()
        view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=full)[1]["token"]
        self.engine.upload("all.fs", ALL)
        presenter = (("/api/shaders/play", {"id": "all.fs", "values": {"lit": True}, "controls": {"speed": 2}}), ("/api/shaders/values", {"values": {"level": 1.0}}),
                     ("/api/shaders/values", {"controls": {"hue": 30}}), ("/api/shaders/step", {"dir": 1}), ("/api/vibes", {"previous": True}))
        for path, body in presenter:
            self.assertEqual(self.call("POST", path, body)[0], 401, path)
            self.assertEqual(self.call("POST", path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=live, csrf=False)[0], 403, path)
        self.assertEqual(self.call("POST", presenter[0][0], presenter[0][1], token=live)[0], 200)
        st, body, _ = self.call("POST", "/api/shaders/values", {"values": {"level": 1.0}}, token=live)
        self.assertEqual((st, body["values"]["level"], body["controls"]["speed"]), (200, 1.0, 2.0))
        owner = (("/api/shaders/presets", {"action": "save", "name": "mine"}), ("/api/shaders", {"action": "set", "op": "add", "name": "Show", "shaders": ["all.fs"]}),
                 ("/api/shaders", {"action": "heavy", "id": "nxlx-nebula.fs", "on": True}), ("/api/shaders", {"action": "config", "guard": False}))
        for path, body in owner:
            for token in (view, live):
                self.assertEqual(self.call("POST", path, body, token=token)[0], 403, path)
            self.assertEqual(self.call("POST", path, body, token=full)[0], 200, path)
        self.assertEqual(self.call("POST", "/api/shaders/preset", {"name": "mine"}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/shaders/preset", {"name": "mine"}, token=live)[0], 200)     # a presenter may apply one
        for path, body in (("/api/shaders/presets", {"action": "rename", "id": "all.fs", "name": "mine", "to": "x"}),
                           ("/api/shaders/presets", {"action": "delete", "id": "all.fs", "name": "mine"})):
            self.assertEqual(self.call("POST", path, body, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/vibes", {"on": True, "set": "Show"}, token=live)[0], 200)   # and start a set by name
        self.api.vibes.stop()
        self.api.registry.set_enabled("shaders", False)
        for path, body in presenter[1:4] + (("/api/shaders/preset", {"name": "mine"}), ("/api/shaders/presets", {"action": "save", "name": "x"})):
            self.assertEqual(self.call("POST", path, body, token=full)[0], 409, path)

    def test_nothing_from_a_request_reaches_the_shader_text_but_checked_values(self):
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        hostile = "x\n//!HOOK OUTPUT\n#include <x>"
        for body in ({"values": {hostile: 1}}, {"values": {"level": hostile}}, {"controls": {hostile: 1}}, {"controls": {"speed": hostile}},
                     {"values": {"tint": [hostile, 0, 0]}}, {"values": {"mode": hostile}}, {"id": hostile, "values": {"level": 1}}):
            with self.assertRaises(ApiError, msg=body):
                self.engine.change(body)
        self.engine.api_presets({"action": "save", "name": "a //!HOOK OUTPUT #include"}, None, "t")          # a preset's name is only a name
        self.engine.api_set({"action": "set", "op": "add", "name": "#version 300 //!DESC x", "shaders": ["all.fs"]}, None, "t")
        self.engine.apply_preset({"name": "a //!HOOK OUTPUT #include"})
        self.pump()
        t = self.text()
        self.assertEqual((t.count("//!"), t.count("#include"), t.count("#version")), (5, 0, 0))
        self.assertEqual(len(self.generated()), 1)

    def test_every_new_key_goes_round_through_a_settings_file_and_a_reset_clears_it(self):
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        self.engine.api_presets({"action": "save", "name": "default"}, None, "t")
        self.engine.api_set({"action": "set", "op": "add", "name": "Show", "dwell": 20, "vary": False, "order": "listed",
                             "shaders": [{"id": "all.fs", "preset": "default"}, "nxlx-silk.fs"]}, None, "t")
        self.engine.api_set({"action": "heavy", "id": "nxlx-nebula.fs", "on": True}, None, "t")
        self.engine.api_set({"action": "config", "guard": False, "clock": "frame"}, None, "t")
        saved = json.loads(json.dumps(self.settings.data["shaders"]))
        self.assertEqual(sorted(saved), ["active", "clock", "disabled", "dwell", "guard", "heavy", "height", "presets", "sets", "v", "vary"])
        clean = boxcare.check_shaders(saved, None)
        self.assertEqual(clean, saved)                                # what the box wrote is what an import takes
        self.settings.data["shaders"] = clean
        self.assertEqual(self.engine.config(), saved)                 # and the module reads back every value
        for key, bad in (("presets", {"all.fs": [{"name": ""}]}), ("presets", {"../x": []}), ("presets", {"all.fs": [{"name": "a", "values": {"x y": 1}}]}),
                         ("presets", {"all.fs": [{"name": "a", "values": {"k": "1"}}]}), ("presets", {"all.fs": [{"name": "a"}, {"name": "A"}]}),
                         ("presets", {"all.fs": [{"name": "a", "controls": {"speed": "fast"}}]}), ("presets", []),
                         ("sets", []), ("sets", [{"id": "zz", "name": "x"}]), ("sets", [{"id": "00000000", "name": "x", "shaders": ["/etc/passwd"]}]),
                         ("sets", [{"id": "00000000", "name": "x", "dwell": 1}]), ("sets", [{"id": "00000000", "name": "x"}, {"id": "00000000", "name": "y"}]),
                         ("heavy", ["a.fs"]), ("heavy", {"../x": {}}), ("active", "first"), ("guard", "yes"), ("clock", "sundial"), ("v", 3)):
            with self.assertRaises(ValueError, msg=(key, bad)):
                boxcare.check_shaders(dict(saved, **{key: bad}), None)
        self.assertEqual(L.check_extra({}), {})                       # a file of the first version: nothing is made up
        from pvj.settings import default_settings
        self.assertNotIn("shaders", default_settings())               # which is why a factory reset leaves none of it behind
