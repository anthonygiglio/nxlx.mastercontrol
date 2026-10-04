# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The shader engine made fit to perform with: what the first measurements on a real Pi 4 found, every input type
adjustable live, changes that are coalesced, the common controls, presets, rotation sets, and the guard for a weak
GPU. All against the fake player of test_shaders.py and fake clocks; the real mpv is in test_shaders_gpu.py."""
from pvj import shaders as S
from tests.test_shaders import GOOD, REFUSAL, Base, FakeTap


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
