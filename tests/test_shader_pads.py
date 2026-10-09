# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A pad that holds a generator shader instead of a clip (D73): what it stores, what a tap does from every place a
pad can be tapped from, and what it refuses."""
import copy
import os
import random
import threading
import time
import unittest

from pvj import autostart, boxcare, midi, vibes as V
from pvj.api import Api, ApiError
from pvj.settings import Settings
from tests.test_server import ServerBase
from tests.test_shaders import REFUSAL, Base, FakeTap

ONE, TWO = "nxlx-aurora.fs", "nxlx-tide.fs"


def pad_of(api, bank=0, index=0):
    return api.settings.data["pads"]["banks"][bank]["pads"][index]


class PadBase(Base):
    def setUp(self):
        super().setUp()
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=time.monotonic, sleep=lambda s: None,
                                              rng=random.Random(4), thread=False, log=lambda *_: None)
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}

    def give(self, index=0, shader=ONE, **more):
        return self.api.set_pad(dict({"bank": 0, "index": index, "shader": shader}, **more), None, "t")

    def tap(self, index=0, device=None, **more):
        return self.api.play(dict({"pad": [0, index]}, **more), device, "t")

    def on(self):
        on = self.engine.on_screen()
        return on["id"] if on else None

    def a_preset(self, sid=ONE, name="Slow"):
        """A preset of `sid`, kept from the screen as the Shaders screen keeps one."""
        self.engine.play(sid, None, {"speed": 0.5})
        self.engine.preset_save(name)
        self.api.control({"action": "stop"}, None, "t")
        self.assertIsNone(self.on())

    def wait(self, what, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if what():
                return True
            time.sleep(0.005)
        return False


class Stored(PadBase):
    """What a shader pad stores: `file` empty and two extra keys, on the schema as it is."""

    def test_a_shader_pad_keeps_its_shader_beside_an_empty_file_and_no_ending(self):
        before = self.settings.data["schema"]
        out = self.give(label="Glow", ending="hold")
        self.assertEqual(pad_of(self.api), {"label": "Glow", "file": "", "shader": ONE})
        self.assertEqual(out["banks"][0]["pads"][0], {"label": "Glow", "file": "", "shader": ONE})
        self.assertEqual(self.settings.data["schema"], before, "a shader pad must not move the schema")
        again = Settings(self.settings.path)
        again.load()
        self.assertEqual(again.data["pads"]["banks"][0]["pads"][0], {"label": "Glow", "file": "", "shader": ONE})
        self.assertEqual(Api.pad_shader(pad_of(self.api)), (ONE, None))

    def test_a_preset_is_kept_by_the_name_it_has(self):
        self.a_preset(name="Slow")
        self.give(preset="slow")                                        # typed in another letter case
        self.assertEqual(pad_of(self.api), {"label": "", "file": "", "shader": ONE, "preset": "Slow"})
        self.assertEqual(Api.pad_shader(pad_of(self.api)), (ONE, "Slow"))

    def test_what_is_refused_when_a_pad_is_given_a_shader(self):
        self.a_preset()
        for body, status in (({"shader": "../x.fs"}, 400), ({"shader": "nothing-here.fs"}, 404), ({"shader": 7}, 400),
                             ({"shader": ONE, "preset": "no such"}, 404), ({"shader": ONE, "preset": 3}, 404),
                             ({"shader": ONE, "file": "a.mp4"}, 400), ({"file": "a.mp4", "preset": "Slow"}, 400),
                             ({"preset": "Slow"}, 400), ({"shader": TWO, "preset": "Slow"}, 404),
                             ({"shader": ["a.fs"]}, 400), ({"shader": {"a": 1}}, 400), ({"shader": "a\x00.fs"}, 400),
                             ({"shader": "a\nb.fs"}, 400), ({"shader": "a\\b.fs"}, 400), ({"shader": "x" * 300 + ".fs"}, 400),
                             ({"shader": "a\u202eb.fs"}, 400), ({"shader": True}, 400), ({"shader": ONE, "preset": ["x"]}, 404),
                             ({"shader": ONE, "preset": {"a": 1}}, 404)):
            with self.assertRaises(ApiError, msg=body) as c:
                self.api.set_pad(dict({"bank": 0, "index": 0}, **body), None, "t")
            self.assertEqual(c.exception.status, status, body)
        self.assertEqual(pad_of(self.api), {"label": "", "file": ""}, "a refused request changed the pad")

    def test_a_shader_that_cannot_be_read_is_not_put_on_a_pad(self):
        os.makedirs(self.engine.dir, exist_ok=True)
        with open(os.path.join(self.engine.dir, "broken.fs"), "w") as f:
            f.write("void main() {}\n")                                 # no header: not a shader this box can show
        with self.assertRaises(ApiError) as c:
            self.give(shader="broken.fs")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("broken.fs", c.exception.message)

    def test_a_clip_takes_the_pad_back_and_a_clear_empties_it(self):
        self.give()
        self.api.set_pad({"bank": 0, "index": 0, "file": "a.mp4"}, None, "t")
        self.assertEqual(pad_of(self.api), {"label": "", "file": "a.mp4", "ending": "loop"})
        self.assertIsNone(Api.pad_shader(pad_of(self.api)))
        self.give()
        self.api.set_pad({"bank": 0, "index": 0, "file": ""}, None, "t")
        self.assertEqual(pad_of(self.api), {"label": "", "file": "", "ending": "loop"})

    def test_a_pad_with_a_clip_is_a_clip_pad_whatever_else_it_carries(self):
        self.assertIsNone(Api.pad_shader({"label": "", "file": "a.mp4", "shader": ONE}))
        for odd in ({"file": ""}, {"file": "", "shader": ""}, {"file": "", "shader": 5}, "text", None):
            self.assertIsNone(Api.pad_shader(odd), odd)
        self.assertEqual(Api.pad_shader({"file": "", "shader": ONE, "preset": 9}), (ONE, None))

    def test_only_a_full_access_device_gives_a_pad_a_shader(self):
        full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "p", "role": "live"}, token=full)[1]["token"]
        body = {"bank": 0, "index": 0, "shader": ONE}
        self.assertEqual(self.call("POST", "/api/pads", body, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/pads", body, token=full)[0], 200)

    # -- the settings file through export and import --
    def test_export_and_import_keep_a_shader_pad(self):
        self.a_preset()
        self.give(label="Glow", preset="Slow")
        self.api.set_pad({"bank": 0, "index": 1, "file": "a.mp4", "ending": "hold"}, None, "t")
        clean = boxcare.check_pads(copy.deepcopy(self.settings.data["pads"]), None)
        self.assertEqual(clean["banks"][0]["pads"][0], {"label": "Glow", "file": "", "shader": ONE, "preset": "Slow"})
        self.assertEqual(clean["banks"][0]["pads"][1], {"label": "", "file": "a.mp4", "ending": "hold"})
        self.assertEqual(clean["banks"][0]["pads"][2], {"label": "", "file": ""})

    def test_an_import_checks_a_shader_pad_as_a_name_and_does_not_look_the_file_up(self):
        def pads(pad):
            data = copy.deepcopy(self.settings.data["pads"])
            data["banks"][0]["pads"][0] = pad
            return data
        kept = boxcare.check_pads(pads({"label": "", "file": "", "shader": "comes-later.fs", "ending": "hold"}), None)
        self.assertEqual(kept["banks"][0]["pads"][0], {"label": "", "file": "", "shader": "comes-later.fs"})
        for bad in ({"file": "", "shader": "../x.fs"}, {"file": "", "shader": 3}, {"file": "a.mp4", "shader": ONE},
                    {"file": "", "shader": ONE, "preset": " x "}, {"file": "", "shader": ONE, "preset": "x" * 41},
                    {"file": "", "shader": "x.glsl"},
                    # the review of #114: with the check weakened to "ends with .fs and has no slash" nothing failed
                    {"file": "", "shader": "a\x00b.fs"}, {"file": "", "shader": "a\nb.fs"}, {"file": "", "shader": "a\\b.fs"},
                    {"file": "", "shader": "x" * 300 + ".fs"}, {"file": "", "shader": "a\u202eb.fs"}, {"file": "", "shader": ".hidden.fs"},
                    {"file": "", "shader": "a:b.fs"}, {"file": "", "shader": "caf\u00e9.fs"}, {"file": "", "shader": ".fs"},
                    {"file": "", "shader": ["a.fs"]}, {"file": "", "shader": {"a": 1}}, {"file": "", "shader": 0.5}, {"file": "", "shader": True},
                    {"file": "", "shader": ONE, "preset": "a\u202eb"}, {"file": "", "shader": ONE, "preset": ["x"]},
                    {"file": "", "shader": ONE, "preset": 7}, {"file": "", "shader": ONE, "preset": "a\nb"}):
            with self.assertRaises(ValueError, msg=bad):
                boxcare.check_pads(pads(dict({"label": ""}, **bad)), None)
        self.assertEqual(boxcare.check_pads(pads({"label": "", "file": "", "shader": "", "ending": "stop"}), None)["banks"][0]["pads"][0],
                         {"label": "", "file": "", "ending": "stop"})


class Tapped(PadBase):
    """A tap on a shader pad is the choosing of that shader by hand."""

    def by_hand(self, sid, preset=None):
        self.engine.api_play(dict({"id": sid}, **({"preset": preset} if preset else {})), None, "t")
        on = dict(self.engine.on_screen())
        calls = [c[0] for c in self.player.calls]
        self.api.control({"action": "stop"}, None, "t")
        del self.player.calls[:]
        return on, calls

    def test_a_tap_shows_the_shader_as_choosing_it_by_hand_does(self):
        hand, hand_calls = self.by_hand(ONE)
        self.give()
        self.assertEqual(self.tap(), {"playing": ONE, "shader": ONE})
        on = self.engine.on_screen()
        for key in ("id", "values", "controls", "preset", "carrier", "size", "hue"):
            self.assertEqual(on[key], hand[key], key)
        self.assertEqual([c[0] for c in self.player.calls], hand_calls, "a pad's shader took another road to the player")
        self.assertEqual(self.player.path, on["carrier"])

    def test_a_tap_with_a_preset_starts_from_it(self):
        self.a_preset(name="Slow")
        hand, _ = self.by_hand(ONE, "Slow")
        self.give(preset="Slow")
        self.tap()
        on = self.engine.on_screen()
        self.assertEqual((on["preset"], on["controls"], on["values"]), ("Slow", hand["controls"], hand["values"]))
        self.assertEqual(on["controls"]["speed"], 0.5)

    def test_a_tap_ends_vibes(self):
        self.vibes.start()
        self.assertTrue(self.vibes.running)
        self.give()
        self.tap()
        self.assertFalse(self.vibes.running)
        self.assertEqual(self.on(), ONE)

    def test_a_clip_pad_after_it_takes_the_screen(self):
        self.give()
        self.api.set_pad({"bank": 0, "index": 1, "file": "a.mp4"}, None, "t")
        self.tap()
        self.assertEqual(self.tap(1)["playing"], "a.mp4")
        self.assertEqual((self.on(), self.player.source_shader, os.path.basename(self.player.path)), (None, None, "a.mp4"))
        self.tap()
        self.assertEqual(self.on(), ONE)

    def test_an_ending_or_a_loop_sent_with_the_tap_means_nothing(self):
        self.give()
        for more in ({"ending": "hold"}, {"loop": False}, {"ending": "nonsense"}, {"shuffle": True}):
            self.assertEqual(self.tap(**more), {"playing": ONE, "shader": ONE})
            self.assertEqual(self.on(), ONE)

    def test_one_shader_pad_after_another(self):
        self.give(0, ONE)
        self.give(1, TWO)
        self.tap(0)
        self.tap(1)
        self.assertEqual(self.on(), TWO)

    def test_a_tap_is_one_newest_wish_as_a_shader_chosen_by_hand_is(self):
        # the pad's branch of Api.play comes before the play claims anything: a ticket taken there, and the
        # generator's own taking of the screen after it, would be two newest wishes for one tap
        tr = self.api.transitions
        before = tr._gen
        self.engine.api_play({"id": ONE}, None, "t")
        by_hand = tr._gen - before
        self.give(1, TWO)
        before, epoch = tr._gen, self.player.source_epoch
        self.tap(1)
        self.assertEqual((tr._gen - before, self.player.source_epoch - epoch), (by_hand, 1))

    def test_blackout_stays_dark_under_a_shader_pad(self):
        self.give()
        self.api.blackout({"on": True}, None, "t")
        self.tap()
        self.assertEqual((self.on(), self.player.level), (ONE, 0.0))

    # -- what is refused, and what it says --
    def test_the_module_off(self):
        self.give()
        self.api.registry.set_enabled("shaders", False)
        with self.assertRaises(ApiError) as c:
            self.tap()
        self.assertEqual((c.exception.status, c.exception.message), (409, "turn on the Shaders and Vibes module in System first"))
        self.assertEqual(self.player.calls, [])

    def test_the_shader_gone_since(self):
        os.makedirs(self.engine.dir, exist_ok=True)
        path = os.path.join(self.engine.dir, "mine.fs")
        with open(self.engine._path(ONE)[0]) as src, open(path, "w") as f:
            f.write(src.read())
        self.give(shader="mine.fs")
        self.tap()
        self.api.control({"action": "stop"}, None, "t")
        os.unlink(path)                                                 # deleted, or renamed, behind the pad's back
        del self.player.calls[:]
        for device in (None, {"id": "midi"}):
            with self.assertRaises(ApiError) as c:
                self.tap(device=device)
            self.assertEqual(c.exception.status, 404)
            self.assertIn("this pad's shader, mine.fs, is not on the box any more", c.exception.message)
        self.assertEqual(self.player.calls, [], "something was sent to the player for a shader that is gone")

    def test_a_pad_whose_preset_was_deleted_starts_the_shader_by_itself_and_says_so(self):
        # it used to refuse the tap, though the shader is there; Vibes does the same for a set whose preset is gone
        self.a_preset(name="Slow")
        self.give(preset="Slow")
        self.engine.preset_delete(ONE, "Slow")
        said = []
        self.api.log = said.append
        out = self.tap()
        self.assertEqual((out["playing"], self.on(), self.engine.on_screen()["preset"]), (ONE, ONE, None))
        self.assertIn("this pad's preset, Slow, is gone", out["note"])
        self.assertTrue(any("preset of %s is gone" % ONE in line for line in said), said)
        self.api.control({"action": "stop"}, None, "t")
        out = self.tap(device={"id": "midi"})
        self.assertEqual(out["pending"], True)
        self.assertIn("is gone", out["note"])
        self.assertTrue(self.wait(lambda: self.on() == ONE))
        self.give()                                                     # a pad with no preset says nothing of the kind
        self.assertNotIn("note", self.tap())

    def test_a_shader_pad_comes_up_from_black_after_a_fade_out_as_a_clip_does(self):
        # low, the fifth read of #114: it snapped to full; from the Shaders page too
        self.settings.data["mix"] = {"transition": "cut", "duration": 2.0}
        self.give()
        for how in ("pad", "hand"):
            self.api.control({"action": "stop"}, None, "t")
            self.api.fader.cancel()
            self.player.play([os.path.join(self.media, "a.mp4")])
            self.api.fadeout({"seconds": 0.1}, None, "t")
            self.assertTrue(self.wait(lambda: self.api.fader.label == "out" and self.player.level == 0.0))
            del self.player.calls[:]
            self.tap() if how == "pad" else self.engine.api_play({"id": ONE}, None, "t")
            levels = [c[1] for c in self.player.calls if c[0] == "opacity"]
            self.assertEqual(levels[0], 0, "%s: the shader did not start from black after a Fade out" % how)
            self.assertIsNone(self.api.fader.label, how)
            time.sleep(0.3)                                             # a third of the second it takes: on its way, not there
            now = [c[1] for c in self.player.calls if c[0] == "opacity"][-1]
            self.assertTrue(0 < now < 255, "%s: after 0.3 s the level is %s: it does not rise over half the Mix duration" % (how, now))
            self.assertTrue(self.wait(lambda: self.player.level == 100.0, 3), how)

    def test_a_shader_that_no_longer_reads_is_said_at_the_tap_and_vibes_goes_on(self):
        # low, the review of #114: a controller's tap answered "pending", ended Vibes and showed nothing
        os.makedirs(self.engine.dir, exist_ok=True)
        path = os.path.join(self.engine.dir, "mine.fs")
        with open(self.engine._path(ONE)[0]) as src, open(path, "w") as f:
            f.write(src.read())
        self.give(shader="mine.fs")
        time.sleep(0.02)
        with open(path, "w") as f:
            f.write("not a shader at all {{{ and more")
        self.vibes.start()
        del self.player.calls[:]
        for device in ({"id": "midi"}, None):
            with self.assertRaises(ApiError) as c:
                self.tap(device=device)
            self.assertEqual(c.exception.status, 422, device)
            self.assertIn("this pad's shader, mine.fs, cannot be shown", c.exception.message)
            self.assertTrue(self.vibes.running, "a pad whose shader no longer reads ended the rotation (%s)" % (device,))
        self.assertIsNone(self.engine.changer.queued())

    def test_a_shader_pad_takes_a_pairing_pin_off_the_screen_when_its_shader_is_on(self):
        # as a shader chosen on the Shaders screen does (Engine.show ends in Api._started_playing). A clip takes it
        # off at the tap; a shader when it is on the screen, so a shader the GPU refuses leaves the PIN where it is
        cleared = []
        self.api.pinscreen = type("Pin", (), {"clear": lambda self_: cleared.append(1), "status": lambda self_: {}})()
        self.give()
        self.tap()
        self.assertEqual(len(cleared), 1)
        self.api.control({"action": "stop"}, None, "t")
        self.tap(device={"id": "midi"})
        self.assertTrue(self.wait(lambda: len(cleared) >= 2), "a controller's shader pad left the PIN on the screen")
        self.player.vo = "gpu"
        FakeTap.lines = REFUSAL
        self.give(1, TWO)
        before = len(cleared)
        with self.assertRaises(ApiError):
            self.tap(1)
        self.assertEqual(len(cleared), before, "a shader the GPU refused took the PIN off")

    def test_a_tap_while_vibes_runs_that_is_refused_at_once_leaves_vibes_running(self):
        self.give(shader=ONE)
        pad_of(self.api)["shader"] = "gone.fs"
        self.vibes.start()
        for device in (None, {"id": "osc"}):
            with self.assertRaises(ApiError):
                self.tap(device=device)
        self.assertTrue(self.vibes.running, "a pad whose shader is gone ended the rotation")

    def test_the_gpu_refuses_it_and_the_screen_keeps_what_it_had(self):
        self.player.vo = "gpu"
        self.give(0, ONE)
        self.give(1, TWO)
        self.tap(0)
        had = self.player.source_shader
        FakeTap.lines = REFUSAL
        with self.assertRaises(ApiError) as c:
            self.tap(1)
        self.assertEqual(c.exception.status, 422)
        self.assertIn("the player refused %s" % TWO, c.exception.message)
        self.assertIn("The shader before it is back on", c.exception.message)
        self.assertEqual((self.on(), self.player.source_shader), (ONE, had))

    def test_a_guest_may_not_tap_it_and_a_presenter_may(self):
        self.give()
        full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        tokens = {role: self.call("POST", "/api/devices/invite", {"name": role, "role": role}, token=full)[1]["token"] for role in ("view", "live")}
        self.assertEqual(self.call("POST", "/api/play", {"pad": [0, 0]}, token=tokens["view"])[0], 403)
        self.assertIsNone(self.on())
        st, body, _ = self.call("POST", "/api/play", {"pad": [0, 0]}, token=tokens["live"])
        self.assertEqual((st, body), (200, {"playing": ONE, "shader": ONE}))
        self.assertEqual(self.on(), ONE)


class Queued(PadBase):
    """The review of #114: what a controller's tap, which is queued for the engine's worker, owes to the moment of
    the tap. The same holds for the controllers' own shader actions, which use the same queue: a preset of another
    shader (apply_preset) and a step to the next one."""

    def setUp(self):
        super().setUp()
        self.give(0, ONE)
        self.give(1, TWO)
        self.ids = self.engine.vibes_ids()
        self.after_first = None
        self.hold_first()

    def hold_first(self):
        """From now on the worker's next job stops between being taken and taking the screen, until `go`."""
        if getattr(self, "go", None) is not None:
            self.go.set()
        self.inside, self.go = threading.Event(), threading.Event()
        real, first = type(self.engine).play.__get__(self.engine), []
        inside, go = self.inside, self.go

        def held(*a, **k):
            mine = k.get("queued") and not first
            if mine:
                first.append(1)
                inside.set()
                go.wait(5)                          # the worker has taken its job and not yet the screen
            out = real(*a, **k)
            if mine and self.after_first:
                self.after_first()
            return out
        self.engine.play = held
        self.addCleanup(go.set)

    def taps(self, how):
        """Two wishes of the kind `how`, the second while the worker is on its way with the first. Returns what
        must be on the screen in the end."""
        midi_device = {"id": "midi"}
        if how == "pad":
            first, second, want = (lambda: self.tap(0, device=midi_device)), (lambda: self.tap(1, device=midi_device)), TWO
        elif how == "preset":
            first = lambda: self.engine.apply_preset({"id": ONE, "name": "P"})
            second, want = (lambda: self.engine.apply_preset({"id": TWO, "name": "P"})), TWO
        else:
            start = self.ids[0]
            first, second, want = (lambda: self.engine.step(1)), (lambda: self.engine.step(1)), self.ids[2]
            self.engine.play(start)
        return first, second, want

    def presets(self):
        for sid in (ONE, TWO):
            self.engine.play(sid)
            self.engine.preset_save("P")
        self.api.control({"action": "stop"}, None, "t")

    def test_of_two_quick_wishes_the_newer_one_is_on_the_screen(self):
        # M1: the second carried the epoch that the first then moved, and the player refused it: the OLDER stayed
        self.presets()
        for how in ("pad", "preset", "step"):
            self.api.control({"action": "stop"}, None, "t")
            self.assertTrue(self.wait(lambda: self.engine.changer.newest() is None))
            self.hold_first()
            first, second, want = self.taps(how)
            first()
            self.assertTrue(self.inside.wait(5), how)
            second()
            self.go.set()
            self.assertTrue(self.wait(lambda: self.on() == want), "%s: %s is on the screen, the newer wish was %s" % (how, self.on(), want))
            self.assertIsNone(self.engine.changer.queued(), how)

    def test_a_clip_played_between_the_two_still_wins(self):
        # the other side of the same rule: the second wish is let past the queue's own first job, and past nothing else
        clip = os.path.join(self.media, "a.mp4")
        self.after_first = lambda: self.player.play([clip])
        self.tap(0, device={"id": "midi"})
        self.assertTrue(self.inside.wait(5))
        self.tap(1, device={"id": "midi"})
        self.go.set()
        self.assertTrue(self.wait(lambda: self.engine.changer.queued() is None and self.engine.changer.newest() is None))
        time.sleep(0.1)
        self.assertEqual((self.on(), os.path.basename(self.player.path)), (None, "a.mp4"))

    def test_a_second_wish_after_a_first_that_the_gpu_refused(self):
        # the first leaves the screen black by its own stop, which moves the player's count once more: the second
        # is let past that too, and is not dropped without a word
        self.player.vo = "gpu"
        taps = []

        def refuse_the_first_only(path):
            FakeTap.lines = [] if taps else REFUSAL
            taps.append(1)
            return FakeTap(path)
        self.engine._tap = refuse_the_first_only
        self.tap(0, device={"id": "midi"})
        self.assertTrue(self.inside.wait(5))
        self.tap(1, device={"id": "midi"})
        self.go.set()
        self.assertTrue(self.wait(lambda: self.on() == TWO), (self.on(), self.engine.error))

    # -- the second read of #114: the windows the first repair missed --
    def hold_in_the_look(self):
        """The worker's next job stops while the GPU looks at its shader: it HAS taken the screen (the player's
        epoch has moved) and the engine does not say so yet. Up to four seconds at a shader's first showing."""
        self.go.set()
        self.engine.play = type(self.engine).play.__get__(self.engine)
        self.player.vo = "gpu"
        looking, on = threading.Event(), threading.Event()
        real, first = self.engine._watch, []

        def held(tap, desc):
            if not first and threading.current_thread() is not threading.main_thread():
                first.append(1)
                looking.set()
                on.wait(5)
            return real(tap, desc)
        self.engine._watch = held
        self.addCleanup(on.set)
        return looking, on

    def test_two_steps_go_two_on_when_the_second_comes_during_the_gpus_look(self):
        self.engine.play(self.ids[0])
        looking, on = self.hold_in_the_look()
        self.engine.step(1)
        self.assertTrue(looking.wait(5))
        self.engine.step(1)
        on.set()
        self.assertTrue(self.wait(lambda: self.engine.changer.newest() is None and self.on() == self.ids[2]),
                        "two steps from %s ended on %s" % (self.ids[0], self.on()))

    def test_a_second_tap_during_the_look_of_a_first_that_is_then_refused(self):
        taps = []

        def refuse_the_first_only(path):
            FakeTap.lines = [] if taps else REFUSAL
            taps.append(1)
            return FakeTap(path)
        self.engine._tap = refuse_the_first_only
        looking, on = self.hold_in_the_look()
        self.tap(0, device={"id": "midi"})
        self.assertTrue(looking.wait(5))
        self.tap(1, device={"id": "midi"})          # carries the epoch the first one's own showing made
        on.set()
        self.assertTrue(self.wait(lambda: self.on() == TWO), "the second tap was dropped: %s is on, %s" % (self.on(), self.engine.error))

    def test_a_second_tap_after_a_first_that_raised_once_it_had_the_screen(self):
        # the second came before the first took the screen and carries the epoch the first started from; the first
        # then takes the screen and something raises. What it made of the epoch is written down as it is made, so
        # the second is not refused for it
        self.player.vo = "gpu"

        def raises(tap, desc):
            self.engine._watch = type(self.engine)._watch.__get__(self.engine)
            raise RuntimeError("something broke after the shader was on the screen")
        self.engine._watch = raises
        self.tap(0, device={"id": "midi"})
        self.assertTrue(self.inside.wait(5))
        self.tap(1, device={"id": "midi"})
        self.go.set()
        self.assertTrue(self.wait(lambda: self.on() == TWO), (self.on(), self.engine.error))

    def test_three_wishes_in_a_row(self):
        # A in the worker's hands, B waiting, then C while B is in the worker's hands: C. (Keeping the epoch a job
        # CARRIED where the epoch it was GIVEN belongs passed every test of two.)
        self.give(2, self.ids[5])
        real, n = type(self.engine).play.__get__(self.engine), []
        gates = [(threading.Event(), threading.Event()), (threading.Event(), threading.Event())]
        self.go.set()

        def held(*a, **k):
            if k.get("queued") and len(n) < 2:
                inside, go = gates[len(n)]
                n.append(1)
                inside.set()
                go.wait(5)
            return real(*a, **k)
        self.engine.play = held
        for _, go in gates:
            self.addCleanup(go.set)
        device = {"id": "midi"}
        self.tap(0, device=device)
        self.assertTrue(gates[0][0].wait(5))
        self.tap(1, device=device)                  # B, with the epoch A starts from
        gates[0][1].set()
        self.assertTrue(gates[1][0].wait(5))        # B taken, not yet on: A is
        self.assertEqual(self.on(), ONE)
        self.tap(2, device=device)                  # C, with the epoch A left
        gates[1][1].set()
        self.assertTrue(self.wait(lambda: self.engine.changer.newest() is None and self.on() == self.ids[5]), self.on())

    def test_a_renamed_preset_and_its_pads_are_one_write(self):
        self.go.set()
        self.a_preset(name="Slow")
        self.give(0, ONE, preset="Slow")
        saves, real = [], self.settings.save

        def save():
            saves.append((self.settings.data["shaders"]["presets"][ONE][0]["name"], self.settings.data["pads"]["banks"][0]["pads"][0].get("preset")))
            return real()
        self.settings.save = save
        self.engine.preset_rename(ONE, "Slow", "Gentle")
        self.assertEqual(saves, [("Gentle", "Gentle")], "the preset's new name and the pad that names it were written apart")

    def test_a_fade_out_pressed_after_the_wish_stands_when_the_shader_comes(self):
        # M2: the level's mark was taken by the worker when it showed the shader, not at the tap
        self.presets()
        for how in ("pad", "preset", "step"):
            self.api.control({"action": "stop"}, None, "t")
            self.api.control({"action": "reset"}, None, "t")
            self.player.play([os.path.join(self.media, "a.mp4")])
            first, _, _ = self.taps(how)
            if how == "step":
                self.wait(lambda: self.on() is not None)
            with self.engine._lock:                 # the worker cannot show anything yet
                before = self.on()
                first()
                self.api.fadeout({"seconds": 0.1}, None, "t")
                self.assertTrue(self.wait(lambda: self.api.fader.label == "out" and self.player.level == 0.0), how)
            self.go.set()
            self.assertTrue(self.wait(lambda: self.on() not in (None, before) and self.engine.changer.newest() is None), how)
            time.sleep(0.1)
            self.assertEqual((self.player.level, self.api.fader.label), (0.0, "out"), "%s: the shader came up lit over a Fade out pressed after it was asked for" % how)
            self.api.fadein({"seconds": 0.1}, None, "t")
            self.wait(lambda: self.api.fader.label is None)

    def test_a_fade_out_pressed_before_the_wish_is_undone_by_it_as_by_any_play(self):
        self.api.fadeout({"seconds": 0.1}, None, "t")
        self.assertTrue(self.wait(lambda: self.player.level == 0.0))
        self.go.set()
        self.tap(0, device={"id": "midi"})
        self.assertTrue(self.wait(lambda: self.on() == ONE and self.player.level == 100.0))

    def test_a_blackout_after_the_wish_stays_dark_and_a_stop_after_it_stays_stopped(self):
        with self.engine._lock:
            self.tap(0, device={"id": "midi"})
            self.api.blackout({"on": True}, None, "t")
        self.go.set()
        self.assertTrue(self.wait(lambda: self.on() == ONE))
        time.sleep(0.1)
        self.assertEqual(self.player.level, 0.0)
        self.api.blackout({"on": False}, None, "t")
        self.api.control({"action": "stop"}, None, "t")
        with self.engine._lock:
            self.tap(1, device={"id": "midi"})
            self.api.control({"action": "stop"}, None, "t")
        self.assertTrue(self.wait(lambda: self.engine.changer.newest() is None))
        time.sleep(0.2)
        self.assertEqual((self.on(), self.player.source_shader), (None, None))


class Steps(PadBase):
    """Next and Previous from a controller (the third read of #114). A step is queued as a move and turned into a
    shader by the worker when it comes to it; the steps that wait add up. However many are pressed, and whatever
    the worker is doing when each comes, the screen ends that many on from where it was."""

    def setUp(self):
        super().setUp()
        self.ids = self.engine.vibes_ids()
        self.assertGreaterEqual(len(self.ids), 6)
        self.player.vo = "gpu"                      # so the GPU looks at a shader it has not seen: the long window
        FakeTap.lines = []
        ch = self.engine.changer
        self.gate = threading.Event()               # closed: the worker has not taken its job yet
        self.before, self.go_on, self.looking, self.look_over = (threading.Event() for _ in range(4))
        self.busy = []
        real_pump, real_play, real_watch = ch.pump, type(self.engine).play.__get__(self.engine), self.engine._watch
        held = {"play": False, "watch": False}

        def pump():
            self.gate.wait(10)
            self.busy.append(1)
            try:
                return real_pump()
            finally:
                self.busy.pop()

        def play(*a, **k):
            if k.get("queued") and not held["play"]:
                held["play"] = True
                self.before.set()
                self.go_on.wait(10)                 # taken by the worker, the screen not yet
            return real_play(*a, **k)

        def watch(tap, desc):
            if not held["watch"] and threading.current_thread() is not threading.main_thread():
                held["watch"] = True
                self.looking.set()
                self.look_over.wait(10)             # on the screen, the GPU looking: the engine does not say so yet
            return real_watch(tap, desc)
        ch.pump, self.engine.play, self.engine._watch = pump, play, watch
        for e in (self.gate, self.go_on, self.look_over):
            self.addCleanup(e.set)

    def idle(self):
        ch = self.engine.changer
        return self.wait(lambda: ch.newest() is None and not self.busy and ch.newest() is None, 10)

    def press(self, moves):
        for d in moves:
            self.assertTrue(self.engine.step(d)["ok"])

    def run_windows(self, start, w0, w1=(), w2=(), w3=()):
        """Start on ids[start]; press w0 before the worker takes anything, w1 once it has taken the first job and
        not yet the screen, w2 while the GPU looks at that job's shader, w3 after it is on. Returns what is on."""
        self.engine.play(self.ids[start])
        self.press(w0)
        self.gate.set()
        if w1 or w2 or w3:
            self.assertTrue(self.before.wait(5), "the first job never ran: nothing is tested")
        self.press(w1)
        self.go_on.set()
        if w2 or w3:
            self.assertTrue(self.looking.wait(5), "the GPU never looked: nothing is tested")
        self.press(w2)
        self.look_over.set()
        if w3:
            self.assertTrue(self.wait(lambda: self.on() == self.ids[(start + sum(w0[:1]) ) % len(self.ids)] or self.on() is not None))
            self.assertTrue(self.idle())
        self.press(w3)
        self.assertTrue(self.idle())
        time.sleep(0.05)
        self.assertTrue(self.idle())
        return self.on()

    def check(self, start, *windows):
        got = self.run_windows(start, *windows)
        want = self.ids[(start + sum(sum(w) for w in windows)) % len(self.ids)]
        self.assertEqual(got, want, "from %s (place %d) with %r: on %s (place %s), expected place %d" % (
            self.ids[start], start, windows, got, self.ids.index(got) if got in self.ids else None, (start + sum(sum(w) for w in windows)) % len(self.ids)))


def _step_case(start, *windows):
    def test(self):
        self.check(start, *windows)
    return test


N, P = 1, -1
LAST = -1           # the last shader of the set: `ids[-1]`
_CASES = {
    # two, three, four and five quick steps, landing in each window
    "two_before_the_worker_takes_any": (0, (N, N)),
    "two_the_second_before_the_screen": (0, (N,), (N,)),
    "two_the_second_during_the_look": (0, (N,), (), (N,)),
    "two_the_second_after_it_is_on": (0, (N,), (), (), (N,)),
    "three_one_in_each_window": (0, (N,), (N,), (N,)),                         # the third read's probe: it landed on the first
    "three_before_the_screen": (0, (N,), (N, N)),
    "three_during_the_look": (0, (N,), (), (N, N)),
    "four_spread": (0, (N,), (N,), (N,), (N,)),
    "four_two_and_two": (0, (N, N), (), (N, N)),
    "five_spread": (0, (N, N), (N,), (N,), (N,)),
    "five_all_while_the_gpu_looks": (0, (N,), (), (N, N, N, N)),
    # the other way, and round both ends of the list
    "previous_three_one_in_each_window": (4, (P,), (P,), (P,)),
    "previous_round_the_start": (1, (P,), (P,), (P,)),
    "previous_five_round_the_start": (2, (P,), (P, P), (P, P)),
    "next_round_the_end": (LAST, (N,), (N,), (N,)),
    "next_five_round_the_end": (LAST - 2, (N,), (N, N), (N, N)),
    # Next and Previous mixed
    "next_then_previous_during_the_look_goes_back": (2, (N,), (), (P,)),
    "next_next_previous": (2, (N,), (N,), (P,)),
    "previous_then_two_next": (2, (P,), (N,), (N,)),
    "two_next_and_two_previous_while_the_first_is_on_its_way": (2, (N,), (N, P), (P, N)),
    "next_and_previous_that_wait_together_make_none": (2, (N,), (N, P)),
}
for _name, _case in _CASES.items():
    setattr(Steps, "test_" + _name, _step_case(_case[0] % 1000 if _case[0] >= 0 else _case[0], *_case[1:]))


class StepsBesides(Steps):
    """What a step owes to everything else: it cancels with its opposite, it is dropped by what was played after
    it, it steps over a shader the GPU refused, and it reads the set when it runs."""

    def test_a_next_and_a_previous_before_the_worker_comes_show_nothing_new(self):
        self.engine.play(self.ids[2])
        shown = len([c for c in self.player.calls if c[0] == "play_source"])
        self.press((N, P))
        self.gate.set()
        self.assertTrue(self.idle())
        self.assertEqual((self.on(), len([c for c in self.player.calls if c[0] == "play_source"])), (self.ids[2], shown))

    def test_a_clip_played_after_the_steps_keeps_the_screen(self):
        clip = os.path.join(self.media, "a.mp4")
        for stop in (False, True):
            self.engine.play(self.ids[2])
            self.gate.clear()
            self.press((N, N))
            self.api.control({"action": "stop"}, None, "t") if stop else self.player.play([clip])
            self.gate.set()
            self.assertTrue(self.idle())
            time.sleep(0.1)
            self.assertEqual((self.on(), self.player.source_shader), (None, None), "after a Stop" if stop else "after a clip")

    def test_a_clip_played_while_the_first_step_is_on_its_way_drops_the_ones_behind_it(self):
        # pressed before the clip: the first step shows its shader, the clip takes the screen, and the second step,
        # which was pressed before the clip, does not come over it
        clip = os.path.join(self.media, "a.mp4")
        self.engine.play(self.ids[0])
        self.press((N,))
        self.gate.set()
        self.assertTrue(self.before.wait(5))
        self.press((N,))
        self.go_on.set()
        self.assertTrue(self.looking.wait(5))
        self.player.play([clip])
        self.look_over.set()
        self.assertTrue(self.idle())
        time.sleep(0.1)
        self.assertEqual((self.on(), os.path.basename(self.player.path)), (None, "a.mp4"))
        # and a Next pressed after the clip is a new wish: with no shader on, it starts at the first of the set
        self.press((N,))
        self.assertTrue(self.wait(lambda: self.on() == self.ids[0]), self.on())
        self.engine.play(self.ids[3])
        self.player.play([clip])
        self.press((P,))
        self.assertTrue(self.wait(lambda: self.on() == self.ids[-1]), self.on())

    def test_a_step_goes_over_a_shader_the_gpu_refused(self):
        # before, Next after a refused shader counted from the one that came back and tried the refused one again
        taps = []

        def refuse_the_first_only(path):
            FakeTap.lines = [] if taps else REFUSAL
            taps.append(1)
            return FakeTap(path)
        self.engine.play(self.ids[0])
        self.engine._checked.clear()
        self.engine._tap = refuse_the_first_only
        self.gate.set()
        self.go_on.set()
        self.look_over.set()
        self.press((N,))
        self.assertTrue(self.wait(lambda: (self.engine.error or {}).get("id") == self.ids[1]), "the first was not refused: nothing is tested")
        self.assertEqual(self.on(), self.ids[0])
        self.press((N,))
        self.assertTrue(self.wait(lambda: self.on() == self.ids[2]), self.on())

    def test_three_steps_with_a_refusal_in_the_middle(self):
        taps = []

        def refuse_the_second(path):
            taps.append(1)
            FakeTap.lines = REFUSAL if len(taps) == 2 else []
            return FakeTap(path)
        self.engine.play(self.ids[0])
        self.engine._checked.clear()
        self.engine._tap = refuse_the_second
        self.press((N,))
        self.gate.set()
        self.assertTrue(self.before.wait(5))
        self.go_on.set()
        self.assertTrue(self.looking.wait(5))
        self.look_over.set()
        self.assertTrue(self.wait(lambda: self.on() == self.ids[1]))
        self.assertTrue(self.idle())
        self.press((N,))                            # refused: ids[1] comes back
        self.assertTrue(self.wait(lambda: (self.engine.error or {}).get("id") == self.ids[2]))
        self.press((N,))
        self.assertTrue(self.wait(lambda: self.on() == self.ids[3]), self.on())

    def test_the_set_is_read_when_the_step_runs(self):
        ids = list(self.ids)
        other = [ids[4], ids[0], ids[2]]
        self.engine.play(ids[0])
        self.press((N,))
        self.gate.set()
        self.assertTrue(self.before.wait(5))
        self.press((N,))                            # waits; the set changes before the worker comes to it
        self.engine.vibes_ids = lambda: list(other)
        self.go_on.set()
        self.look_over.set()
        self.assertTrue(self.idle())
        # the first went to ids[1] in the old set; the second counts from ids[1], which the new set does not hold:
        # a Next then starts at the new set's first
        self.assertTrue(self.wait(lambda: self.on() == other[0]), self.on())
        self.press((N,))
        self.assertTrue(self.wait(lambda: self.on() == other[1]), self.on())

    def test_a_fade_out_after_the_last_step_stands(self):
        self.engine.play(self.ids[0])
        self.press((N, N))
        self.api.fadeout({"seconds": 0.1}, None, "t")
        self.assertTrue(self.wait(lambda: self.api.fader.label == "out" and self.player.level == 0.0))
        self.gate.set()
        self.go_on.set()
        self.look_over.set()
        self.assertTrue(self.wait(lambda: self.on() == self.ids[2]))
        time.sleep(0.1)
        self.assertEqual((self.player.level, self.api.fader.label), (0.0, "out"))

    # -- the fourth read of #114: the order of the wishes, the newest mark, the place after a black refusal, no growth --
    def open_all(self):
        self.gate.set()
        self.go_on.set()
        self.look_over.set()

    def test_a_pad_between_steps_is_shown_and_the_steps_after_it_count_from_it(self):
        # it was dropped without a word: the step after it replaced it, though its tap had already ended Vibes
        midi_device = {"id": "midi"}
        self.give(0, self.ids[7])
        self.engine.play(self.ids[0])
        shown, real = [], self.engine.show
        self.engine.show = lambda sid, *a, **k: (shown.append(sid), real(sid, *a, **k))[1]
        self.press((N,))
        self.gate.set()
        self.assertTrue(self.before.wait(5))        # the first step is in the worker's hands
        self.press((N,))                            # waits
        self.tap(0, device=midi_device)             # the pad: supersedes the step that waits
        self.press((N,))                            # behind the pad
        self.go_on.set()
        self.look_over.set()
        self.assertTrue(self.idle())
        self.assertTrue(self.wait(lambda: self.on() == self.ids[8]), "on %s; the pad's shader is %s" % (self.on(), self.ids[7]))
        self.assertEqual(shown, [self.ids[1], self.ids[7], self.ids[8]], "the pad's shader was never on the screen")

    def test_a_preset_of_another_shader_between_steps(self):
        self.engine.play(self.ids[7])
        self.engine.preset_save("P")
        self.engine.play(self.ids[0])
        self.press((N,))
        self.gate.set()
        self.assertTrue(self.before.wait(5))
        self.engine.apply_preset({"id": self.ids[7], "name": "P"})
        self.press((N,))
        self.go_on.set()
        self.look_over.set()
        self.assertTrue(self.idle())
        self.assertTrue(self.wait(lambda: self.on() == self.ids[8]), self.on())

    def test_steps_after_a_pad_that_waits_count_from_the_pad(self):
        self.give(0, self.ids[7])
        self.engine.play(self.ids[0])
        self.tap(0, device={"id": "midi"})
        self.press((N, N))
        self.assertEqual(self.engine.changer.queued().get("id"), self.ids[7], "the steps went before the pad that was tapped first")
        self.open_all()
        self.assertTrue(self.idle())
        self.assertTrue(self.wait(lambda: self.on() == self.ids[9]), self.on())

    def test_a_pad_after_steps_that_wait_is_what_is_shown(self):
        self.give(0, self.ids[7])
        self.engine.play(self.ids[0])
        self.press((N, N))
        self.tap(0, device={"id": "midi"})
        self.open_all()
        self.assertTrue(self.idle())
        self.assertTrue(self.wait(lambda: self.on() == self.ids[7]), self.on())

    def test_the_summed_steps_carry_the_level_mark_of_the_newest_press(self):
        # the code was right and nothing held it: with the OLDEST press's mark every test passed
        self.engine.play(self.ids[0])
        self.press((N, N))
        self.api.fadeout({"seconds": 0.1}, None, "t")
        self.assertTrue(self.wait(lambda: self.api.fader.label == "out" and self.player.level == 0.0))
        self.press((P,))                            # pressed after the Fade out: like any play after it, it brings the picture back
        self.open_all()
        self.assertTrue(self.wait(lambda: self.on() == self.ids[1]), self.on())
        self.assertTrue(self.wait(lambda: self.player.level == 100.0 and self.api.fader.label is None),
                        "a step pressed after the Fade out came up dark (level %s, label %s)" % (self.player.level, self.api.fader.label))

    def test_a_fade_out_with_steps_that_add_up_to_none_stays_dark(self):
        self.engine.play(self.ids[0])
        self.press((N,))
        self.api.fadeout({"seconds": 0.1}, None, "t")
        self.assertTrue(self.wait(lambda: self.api.fader.label == "out" and self.player.level == 0.0))
        self.press((P,))
        self.open_all()
        self.assertTrue(self.idle())
        time.sleep(0.1)
        self.assertEqual((self.on(), self.player.level, self.api.fader.label), (self.ids[0], 0.0, "out"))

    def refuse_once(self):
        taps = []

        def refuse_the_first_only(path):
            FakeTap.lines = [] if taps else REFUSAL
            taps.append(1)
            return FakeTap(path)
        self.engine._checked.clear()
        self.engine._tap = refuse_the_first_only

    def test_after_a_refusal_into_black_the_next_step_goes_on_from_that_place(self):
        # with nothing to go back to the screen is black and no shader is on: the place was lost and Next began
        # again at the first of the set
        for move, where in ((N, 4), (P, 2)):
            self.api.control({"action": "stop"}, None, "t")
            self.assertTrue(self.idle())
            ids = list(self.engine.vibes_ids())
            self.engine.error = None
            self.refuse_once()
            self.gate.clear()
            self.press((N, N, N, N))                # from nothing on: the fourth of the set, which the GPU refuses
            self.open_all()
            self.assertTrue(self.wait(lambda: (self.engine.error or {}).get("id") == ids[3]), "the fourth was not refused: nothing is tested")
            self.assertTrue(self.idle())
            self.assertEqual((self.on(), self.player.source_shader), (None, None))
            self.press((move,))
            self.assertTrue(self.wait(lambda: self.on() == ids[where]), "after %+d: on %s, expected %s" % (move, self.on(), ids[where]))
            self.engine.unmark([ids[3]])            # back into the set for the second turn
            self.engine._refusals.clear()

    def test_something_else_played_after_a_black_refusal_forgets_the_place(self):
        ids = list(self.ids)
        self.api.control({"action": "stop"}, None, "t")
        self.refuse_once()
        self.press((N, N, N, N))
        self.open_all()
        self.assertTrue(self.wait(lambda: (self.engine.error or {}).get("id") == ids[3]))
        self.assertTrue(self.idle())
        self.player.play([os.path.join(self.media, "a.mp4")])
        self.press((N,))
        self.assertTrue(self.wait(lambda: self.on() == self.engine.vibes_ids()[0]), self.on())

    def test_the_steps_that_wait_do_not_grow_with_the_presses(self):
        ch = self.engine.changer
        self.engine.play(self.ids[0])
        for _ in range(300):
            self.engine.step(1)
        waiting = ch.queued()
        self.assertEqual((len(waiting["steps"]), waiting["presses"], waiting["steps"][0][0]), (1, 300, 300))
        self.open_all()
        self.assertTrue(self.idle())
        self.assertTrue(self.wait(lambda: self.on() == self.ids[300 % len(self.ids)]), self.on())
        from pvj import shaderlive
        self.gate.clear()
        for epoch in range(1000, 1040):             # something else took the screen between every two presses
            ch.step(1, epoch, None)
        self.assertEqual(len(ch.queued()["steps"]), shaderlive.STEP_ENTRIES)
        self.assertEqual(ch.queued()["steps"][-1][1], 1039, "the newest presses are the ones kept")
        before = self.on()
        self.gate.set()
        self.assertTrue(self.idle())
        self.assertEqual(self.on(), before, "presses made before something else took the screen were shown")

    def test_a_set_emptied_before_the_worker_comes_is_said_in_the_log_and_names_no_shader(self):
        said = []
        self.engine.log = lambda *a: said.append(" ".join(str(x) for x in a))
        self.engine.play(self.ids[0])
        self.engine.error = None
        self.press((N,))
        self.engine.vibes_ids = lambda: []
        self.open_all()
        self.assertTrue(self.idle())
        self.assertIsNone(self.engine.error)
        self.assertEqual((self.on(), [x for x in said if "found none in the active set" in x] != []), (self.ids[0], True))

    def test_a_rename_keeps_a_part_of_the_shaders_settings_that_cannot_be_read(self):
        # a regression of the round before: the one write went past LiveEngine._save and its rule that a key which
        # cannot be read is left in the file as it is
        self.gate.set()
        self.a_preset(name="Slow")
        self.give(0, ONE, preset="Slow")
        self.settings.data["shaders"]["sets"] = "damaged by hand"
        saves, real = [], self.settings.save
        self.settings.save = lambda: (saves.append(1), real())[1]
        self.engine.preset_rename(ONE, "Slow", "Gentle")
        self.assertEqual(self.settings.data["shaders"].get("sets"), "damaged by hand")
        self.assertEqual((self.settings.data["shaders"].get("v"), len(saves), pad_of(self.api).get("preset")), (2, 1, "Gentle"))
        self.engine.preset_delete(ONE, "Gentle")    # the usual road, for comparison
        self.assertEqual(self.settings.data["shaders"].get("sets"), "damaged by hand")


for _name in list(_CASES):
    setattr(StepsBesides, "test_" + _name, None)    # the table's cases are Steps' own: not run a second time here


class WhileVibesChanges(PadBase):
    """The fifth read of #114, its one high finding: a controller's shader pad tapped while Vibes was in the middle
    of a change left the screen black and said nothing. Vibes' own thread runs here, as on the box, and is held in
    each place of a change in turn while the wish comes."""

    def setUp(self):
        super().setUp()
        self.player.vo = "gpu"
        self.settings.data["mix"] = {"transition": "cut", "duration": 0.2}
        self.a_preset(TWO, name="P")
        self.give(0, ONE)
        self._refusals_clear()
        said = self.said = []
        self.engine.log = lambda *a: said.append(" ".join(str(x) for x in a))
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, rng=random.Random(4), log=lambda *_: None)
        self.addCleanup(self.vibes.stop)
        self.where, self.reached, self.go = None, threading.Event(), threading.Event()
        self.addCleanup(self.go.set)
        engine, vibes = self.engine, self.vibes
        playable, watch, show, off, fade = engine.playable, engine._watch, engine.show, engine.off, vibes._fade

        def hold(name):
            if self.where == name and threading.current_thread().name == "vibes" and not self.reached.is_set():
                self.reached.set()
                self.go.wait(10)

        def held_playable(*a, **k):
            hold("before its compose")
            return playable(*a, **k)

        def held_watch(tap, desc):
            hold("during the GPU's look")
            return watch(tap, desc)

        def held_show(*a, **k):
            out = show(*a, **k)
            hold("after its play_source")
            return out

        def held_off(*a, **k):
            hold("during its settle after a Stop")
            return off(*a, **k)

        def held_fade(*a, **k):
            hold("during its own dip")
            return fade(*a, **k)
        engine.playable, engine._watch, engine.show, engine.off, vibes._fade = held_playable, held_watch, held_show, held_off, held_fade
        # The worker waits until the rotation has done all it does on its way out: the worst order for the wish,
        # and the one the box has when the GPU's look takes seconds. (Left to race, the worker usually came first
        # and a rotation that still took the screen after it was ended went unseen.)
        self.gate = threading.Event()
        self.gate.set()
        self.addCleanup(self.gate.set)
        pump = engine.changer.pump
        engine.changer.pump = lambda: (self.gate.wait(10), pump())[1]

    def after_the_rotation(self):
        """Let the rotation go on, wait until its change is over (and its settle), then let the worker work."""
        self.go.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.vibes._work.acquire(blocking=False):
                self.vibes._work.release()
                if self.vibes._thread is None or not self.vibes._thread.is_alive():
                    break
            time.sleep(0.01)
        time.sleep(0.05)
        self.gate.set()

    def _refusals_clear(self):
        self.engine._checked.clear()
        FakeTap.lines = []

    def vibes_shows(self):
        return [c for c in self.player.calls if c[0] == "play_source"]

    WINDOWS = ("before its compose", "during the GPU's look", "after its play_source", "during its own dip")

    def wish(self, kind):
        if kind == "pad":
            self.assertEqual(self.tap(0, device={"id": "midi"})["pending"], True)
            return ONE
        self.assertTrue(self.engine.apply_preset({"id": TWO, "name": "P"})["ok"])
        return TWO

    def one(self, window, kind):
        if window == "during its own dip":
            self.player.play([os.path.join(self.media, "a.mp4")])      # a picture is on: the rotation dips before its shader
        self.where = window
        self.vibes.start()
        self.assertTrue(self.reached.wait(10), "Vibes never came to %r: nothing is tested" % window)
        self.assertTrue(self.vibes.running)
        shown_before = len(self.vibes_shows())
        self.gate.clear()
        want = self.wish(kind)
        self.assertFalse(self.vibes.running)
        calls = len(self.player.calls)
        self.after_the_rotation()
        ok = self.wait(lambda: self.on() == want and self.engine.changer.newest() is None, 8)
        self.assertTrue(ok, "%s, a controller's %s: on the screen is %s (the player's shader %s), expected %s; the journal: %s"
                        % (window, kind, self.on(), self.player.source_shader and "one", want, self.said[-2:]))
        time.sleep(0.3)                                                 # and it stays: the rotation's thread has settled
        self.assertEqual((self.on(), self.vibes.running), (want, False), window)
        self.assertIsNotNone(self.player.source_shader, "the screen is black")
        self.assertNotIn(("clear",), self.player.calls[calls:], "%s: the rotation cleared the screen to black on its way out, "
                         "though a shader had been chosen" % window)
        return shown_before


def _vibes_case(window, kind):
    def test(self):
        self.one(window, kind)
    return test


for _window in WhileVibesChanges.WINDOWS:
    for _kind in ("pad", "preset"):
        setattr(WhileVibesChanges, "test_a_controllers_%s_%s" % (_kind, _window.replace("'", "").replace(" ", "_")), _vibes_case(_window, _kind))


class WhileVibesEnds(WhileVibesChanges):
    """The same for a rotation that is being stopped, and what a rotation that was ended may still do."""

    def test_a_rotation_that_was_ended_puts_nothing_on_afterwards(self):
        # held before it composes its next shader, ended by the tap: it used to put that shader on all the same,
        # for a moment, over the screen the tapped shader was about to take
        self.where = "before its compose"
        self.vibes.start()
        self.assertTrue(self.reached.wait(10))
        self.gate.clear()
        self.tap(0, device={"id": "midi"})
        shown = []
        real = self.player.play_source

        def play_source(*a, **k):
            shown.append(threading.current_thread().name)
            return real(*a, **k)
        self.player.play_source = play_source
        self.after_the_rotation()
        self.assertTrue(self.wait(lambda: self.on() == ONE and self.engine.changer.newest() is None, 8))
        time.sleep(0.3)
        self.assertNotIn("vibes", shown, "the rotation put a shader on after it was ended")

    def stopped_then(self, kind):
        self.where = None
        self.vibes.start()
        self.assertTrue(self.wait(lambda: self.vibes.started and self.vibes.current is not None, 10), "Vibes never showed a shader")
        self.where = "during its settle after a Stop"
        self.vibes.stop()                                               # as a Room scene's stop: it returns at once
        self.assertTrue(self.reached.wait(10), "the rotation's thread never came to clear the screen: nothing is tested")
        self.gate.clear()
        want = self.wish(kind)                                          # straight after the Stop, before its clear
        self.after_the_rotation()
        self.assertTrue(self.wait(lambda: self.on() == want and self.engine.changer.newest() is None, 8),
                        "a %s right after a Stop of Vibes: on the screen is %s, expected %s; the journal: %s" % (kind, self.on(), want, self.said[-2:]))
        time.sleep(0.3)
        self.assertEqual(self.on(), want)

    def test_a_pad_right_after_a_stop_of_vibes_is_shown(self):
        self.stopped_then("pad")

    def test_a_preset_of_another_shader_right_after_a_stop_of_vibes_is_shown(self):
        self.stopped_then("preset")

    def test_a_stop_of_vibes_during_the_gpus_look_and_a_pad_right_after_it(self):
        self.where = "during the GPU's look"
        self.vibes.start()
        self.assertTrue(self.reached.wait(10))
        self.vibes.stop()
        self.gate.clear()
        self.tap(0, device={"id": "midi"})
        self.after_the_rotation()
        self.assertTrue(self.wait(lambda: self.on() == ONE and self.engine.changer.newest() is None, 8), (self.on(), self.said[-2:]))

    def test_the_worker_looks_again_when_the_rotations_clear_came_after_it_read_the_epoch(self):
        # the other order: the worker has read what the job's epoch stands for, and only then does the rotation's
        # clear move it. The player refuses the job; the move was noted in the same step, and the worker, looking
        # again under the player's lock, tries with the new epoch.
        self.where = None
        self.engine.show(TWO)                                           # as the rotation's shader, on the screen
        was = self.player.source_epoch
        real, first = type(self.engine).play.__get__(self.engine), []

        def play(*a, **k):
            if k.get("queued") and not first:
                first.append(1)
                self.assertIsNotNone(self.engine.off(was, adopt=True))  # the rotation's clear on its way out, right now
            return real(*a, **k)
        self.engine.play = play
        self.tap(0, device={"id": "midi"})
        self.assertTrue(self.wait(lambda: self.on() == ONE and self.engine.changer.newest() is None, 8), (self.on(), self.said[-2:]))
        self.assertEqual(first, [1])

    def test_a_stop_of_vibes_alone_still_clears_the_screen(self):
        self.where = None
        self.vibes.start()
        self.assertTrue(self.wait(lambda: self.vibes.started and self.vibes.current is not None, 10))
        self.vibes.stop()
        self.assertTrue(self.wait(lambda: self.player.source_shader is None and self.on() is None, 8), "a Stop of Vibes left its shader on")

    def test_a_queued_wish_that_is_dropped_says_so_in_the_journal(self):
        self.where = None
        with self.engine._lock:
            self.tap(0, device={"id": "midi"})
            self.player.play([os.path.join(self.media, "a.mp4")])
        self.assertTrue(self.wait(lambda: any("was not shown" in line for line in self.said), 8), self.said)
        self.assertIn(ONE, next(line for line in self.said if "was not shown" in line))


for _name in list(vars(WhileVibesChanges)):
    if _name.startswith("test_a_controllers_"):
        setattr(WhileVibesEnds, _name, None)        # the table is WhileVibesChanges' own: not run a second time here


class FromEverywhere(PadBase):
    """Every way a pad can be played. A controller does not wait for the GPU: its tap is queued for the engine's
    worker, as its own shader actions are."""

    def shown(self, sid=ONE):
        return self.wait(lambda: self.on() == sid)

    def test_a_controllers_tap_answers_at_once_and_the_worker_shows_it(self):
        self.give()
        for who in ("midi", "osc", "dmx", "room"):
            self.api.control({"action": "stop"}, None, "t")
            self.assertEqual(self.tap(device={"id": who, "role": "live"}), {"playing": ONE, "shader": ONE, "pending": True}, who)
            self.assertTrue(self.shown(), who)

    def test_a_controllers_tap_ends_vibes_at_the_tap(self):
        self.vibes.start()
        self.give()
        self.tap(device={"id": "midi"})
        self.assertFalse(self.vibes.running)
        self.assertTrue(self.shown())

    def test_a_refusal_by_the_gpu_after_a_controllers_tap_is_kept_for_the_shaders_screen(self):
        self.player.vo = "gpu"
        self.give(0, ONE)
        self.give(1, TWO)
        self.tap(0)
        FakeTap.lines = REFUSAL
        self.assertEqual(self.tap(1, device={"id": "midi"})["pending"], True)
        self.assertTrue(self.wait(lambda: (self.engine.error or {}).get("id") == TWO), "the refusal was lost")
        self.assertEqual(self.on(), ONE)

    def test_a_clip_tapped_after_a_controllers_shader_pad_wins(self):
        # the queued shader carries the epoch of its tap: what is played after the tap is newer, and the worker's
        # turn is then refused by the player
        self.give()
        self.api.set_pad({"bank": 0, "index": 1, "file": "a.mp4"}, None, "t")
        with self.engine._lock:                                         # the worker cannot show anything yet
            self.tap(0, device={"id": "midi"})
            self.player.play([os.path.join(self.media, "a.mp4")])
        time.sleep(0.3)
        self.assertEqual((self.on(), os.path.basename(self.player.path)), (None, "a.mp4"))

    def test_a_midi_note_plays_a_shader_pad(self):
        logs = []
        hub = midi.MidiHub(self.api, self.settings, log=logs.append, lister=lambda: [], clock=time.monotonic)
        self.settings.data["control"]["midi"].update(enabled=True, builtin=True, map=[])
        self.give(2)
        hub.on_message("Mini", ("on", 0, 38, 100))                       # the built-in map: note 36 is pad 1
        hub.on_message("Mini", ("off", 0, 38, 0))
        self.assertTrue(self.shown(), logs)

    def test_the_light_of_a_shader_pad(self):
        self.give(2)
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], clock=time.monotonic)
        action = {"action": "pad", "bank": 0, "index": 2}
        snap = hub._snapshot(time.monotonic(), True)
        self.assertEqual(snap["pad_shaders"][0][2], ONE)
        self.assertEqual(midi.light_state(action, snap), "on")
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 3}, snap), "off")
        self.assertEqual(midi.light_state({"action": "bank_pad", "index": 2}, snap, 0), "on")
        self.tap(2)
        self.assertEqual(midi.light_state(action, hub._snapshot(time.monotonic(), True)), "active")
        self.give(3, TWO)
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 3}, hub._snapshot(time.monotonic(), True)), "on")
        # a clip takes the screen: the engine still remembers the shader it showed last, and the pad must not go on
        # saying it is the one that plays
        self.player.play([os.path.join(self.media, "a.mp4")])
        self.assertEqual(midi.light_state(action, hub._snapshot(time.monotonic(), True)), "on", "a shader pad stays lit as playing under a clip")
        self.tap(2)
        self.assertEqual(midi.light_state(action, hub._snapshot(time.monotonic(), True)), "active")
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual(midi.light_state(action, hub._snapshot(time.monotonic(), True)), "on")
        self.assertEqual(midi.light_state(action, {"pads": [[""] * 12], "shader": None, "running": False, "playing": None}), "off")

    def test_only_the_pad_that_started_what_is_on_is_the_one_playing(self):
        # low, the review of #114: every pad that held the shader on the screen was lit as playing, whatever its
        # preset, and also while Vibes showed that shader. The rule: the shader is on, with the preset this pad
        # starts it with (a pad without one: the preset called default, or none), and Vibes is not running.
        self.a_preset(name="Slow")
        self.give(0, ONE)
        self.give(1, ONE, preset="Slow")
        self.give(2, TWO)
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], clock=time.monotonic)

        def lights():
            snap = hub._snapshot(time.monotonic(), True)
            return [midi.light_state({"action": "pad", "bank": 0, "index": i}, snap) for i in range(3)]
        self.tap(0)
        self.assertEqual(lights(), ["active", "on", "on"])
        self.assertEqual(self.api.status({}, None, "t")["player"]["shader_preset"], "")
        self.tap(1)
        self.assertEqual(lights(), ["on", "active", "on"])
        self.assertEqual(self.api.status({}, None, "t")["player"]["shader_preset"], "Slow")
        self.vibes.start()
        self.assertTrue(self.vibes.tick())
        self.assertEqual(lights(), ["on", "on", "on"], "a pad is lit as playing while Vibes shows a shader")
        # and the pad that holds the very shader Vibes shows (the fifth read: Vibes showed one that was on no pad
        # here, so the rule "not while Vibes shows it" was held by nothing)
        shown = self.vibes.current
        self.give(4, shown)
        hub2 = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], clock=time.monotonic)
        snap = hub2._snapshot(time.monotonic(), True)
        self.assertEqual((snap["shader"], snap["vibes"]), (shown, True))
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 4}, snap), "on", "the pad of the shader Vibes shows is lit as playing")
        self.vibes.stop()
        self.tap(4)
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 4}, hub2._snapshot(time.monotonic(), True)), "active")

    def test_the_preset_lights_are_off_under_a_clip(self):
        # from the first round's own list: the engine remembers the shader it showed last, and the lights of its
        # presets stayed on under a clip played after it
        self.a_preset(name="Slow")
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], clock=time.monotonic)
        action = {"action": "shader_preset_1"}
        self.engine.play(ONE, preset="Slow")
        self.assertEqual(midi.light_state(action, hub._snapshot(time.monotonic(), True)), "active")
        self.player.play([os.path.join(self.media, "a.mp4")])
        snap = hub._snapshot(time.monotonic(), True)
        self.assertEqual((midi.light_state(action, snap), snap["shader"], snap["presets"]), ("off", None, []))

    def test_a_renamed_preset_carries_the_pads_that_start_with_it(self):
        self.a_preset(name="Slow")
        self.a_preset(TWO, name="Slow")
        self.give(0, ONE, preset="Slow")
        self.give(1, TWO, preset="Slow")
        self.give(2, ONE)
        self.engine.preset_rename(ONE, "slow", "Gentle")
        pads = self.settings.data["pads"]["banks"][0]["pads"]
        self.assertEqual((pads[0].get("preset"), pads[1].get("preset"), pads[2].get("preset")), ("Gentle", "Slow", None))
        again = Settings(self.settings.path)
        again.load()
        self.assertEqual(again.data["pads"]["banks"][0]["pads"][0]["preset"], "Gentle")
        self.tap(0)
        self.assertEqual(self.engine.on_screen()["preset"], "Gentle")

    def test_the_library_says_how_many_pads_start_a_shader_with_each_preset(self):
        self.a_preset(name="Slow")
        self.give(0, ONE, preset="Slow")
        self.give(1, ONE, preset="Slow")
        self.give(2, ONE)
        row = next(s for s in self.engine.state()["shaders"] if s["id"] == ONE)
        self.assertEqual((row["pads"], row["preset_pads"]), (3, {"Slow": 2}))
        self.assertEqual(next(s for s in self.engine.state()["shaders"] if s["id"] == TWO)["preset_pads"], {})

    def test_the_status_carries_the_last_refusal_for_the_live_page(self):
        # medium, the fifth read: a controller's tap the GPU refused was said on the Shaders page only
        self.player.vo = "gpu"
        self.give(0, ONE)
        self.assertNotIn("shader_refused", self.api.status({}, None, "t")["player"])
        FakeTap.lines = REFUSAL
        self.assertEqual(self.tap(0, device={"id": "midi"})["pending"], True)
        self.assertTrue(self.wait(lambda: "shader_refused" in self.api.status({}, None, "t")["player"]))
        said = self.api.status({}, None, "t")["player"]["shader_refused"]
        self.assertEqual(said["id"], ONE)
        self.assertIn("The screen is black", said["message"])           # nothing was on before it: not "keeps what it had"
        self.assertEqual((self.on(), self.player.source_shader), (None, None))
        FakeTap.lines = []
        self.tap(0)                                                     # shown: the refusal is over
        self.assertNotIn("shader_refused", self.api.status({}, None, "t")["player"])

    def test_the_queues_record_of_its_own_epochs_does_not_grow_with_the_jobs(self):
        self.give(0, ONE)
        self.give(1, TWO)
        for n in range(30):
            self.tap(n % 2, device={"id": "midi"})
            self.assertTrue(self.wait(lambda: self.on() == (ONE, TWO)[n % 2] and self.engine.changer.newest() is None))
        self.assertLessEqual(len(self.engine._chain[0]), 6, self.engine._chain)
        for n in range(40):
            self.engine.adopt(1000 + n, 2000 + n)
        from pvj import shaderlive
        self.assertLessEqual(len(self.engine._adopted), shaderlive.ADOPTED)

    def test_the_library_says_how_many_pads_hold_a_shader(self):
        self.give(0, ONE)
        self.give(5, ONE)
        self.api.set_pad({"bank": 1, "index": 0, "file": "a.mp4"}, None, "t")
        rows = {s["id"]: s["pads"] for s in self.engine.state()["shaders"]}
        self.assertEqual((rows[ONE], rows[TWO]), (2, 0))

    def test_osc_dmx_and_a_room_scene_name_a_pad_by_its_place_only(self):
        # none of them reads what the pad holds: each sends {"pad": [bank, index]} to the same play
        from pvj import dmx, osc, room
        self.assertEqual(osc.translate("/pvj/play/pad", [1, 3]), ("/api/play", {"pad": [0, 2]}))
        self.assertEqual(dmx.pad_of(dmx.PAD_STEP * 3), 2)
        self.assertEqual(room.BOX["pad"][1]({"pad": [0, 2]}), [("/api/play", {"pad": [0, 2]})])

    def test_autostart_may_start_a_shader_pad(self):
        self.api.autostart = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        with self.assertRaises(ApiError) as c:
            self.api.set_autostart({"mode": "pad", "pad": [0, 4]}, None, "t")
        self.assertEqual(c.exception.message, "choose a pad that has a clip or a shader")
        self.give(4)
        self.api.set_autostart({"mode": "pad", "pad": [0, 4]}, None, "t")
        self.assertEqual(self.api.autostart.run_now(), "started")
        self.assertEqual(self.on(), ONE, "the start of the box does not wait for a worker: the shader is on when it says started")

    def test_a_sync_server_sends_stop_for_a_shader_pad_as_for_any_generator(self):
        # a client cannot have the server's generator: the server says "stop", with its Blackout, as it does for a
        # shader chosen by hand, the test pattern and a live input
        self.give()
        self.tap()
        self.player.ipc.request = lambda *c: self.player.path if c[:2] == ("get_property", "path") else None
        self.assertEqual(self.api.sync.state(), {"st": "stop", "bk": False})


class Mixed(ServerBase):
    """The Mix transition around a shader pad, on the real player over the stand-in for mpv: it is whatever it is
    around a shader chosen by hand. Into a generator is a cut (no still is taken: the generator takes the screen and
    ends a transition that runs). Out of one, the clip blends from a still of the generator, or dips."""

    def setUp(self):
        super().setUp()
        from pvj import transitions as T
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.T = T
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"))
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.api.log = self.api.transitions.log = lambda line: None
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        self.api.set_pad({"bank": 0, "index": 0, "shader": ONE}, None, "t")
        self.api.set_pad({"bank": 0, "index": 1, "file": "a.mp4"}, None, "t")

    def choose(self, how):
        if how == "pad":
            self.api.play({"pad": [0, 0]}, None, "t")
        else:
            self.api.shaders.api_play({"id": ONE}, None, "t")

    def settle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (self.api.transitions.running is not None or self.mpv.overlays):
            time.sleep(0.01)
        self.assertEqual((self.api.transitions.running, self.mpv.overlays), (None, {}))

    def test_into_a_shader_is_a_cut_by_pad_as_by_hand(self):
        for style in ("crossfade", "dip", "wipe-from-left"):
            for how in ("hand", "pad"):
                self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
                self.api.play({"pad": [0, 1]}, None, "t")
                self.settings.data["mix"] = self.T.stored(style, 0.2)
                del self.mpv.commands[:], self.mpv.levels[:]
                self.choose(how)
                what = "%s, by %s" % (style, how)
                self.assertNotIn("screenshot-to-file", self.mpv.commands, what)
                self.assertNotIn("overlay-add", self.mpv.commands, what)
                self.assertEqual([v for v in self.mpv.levels if v != 0], [], "the picture was dipped on the way into a shader (%s)" % what)
                self.assertEqual((self.mpv.log[-1][1], self.api.shaders.on_screen()["id"]), ("carrier", ONE), what)

    def test_out_of_a_shader_a_clip_blends_from_a_still_of_it_by_pad_as_by_hand(self):
        seen = {}
        for how in ("hand", "pad"):
            self.settings.data["mix"] = self.T.stored("crossfade", 0.2)
            self.choose(how)
            del self.mpv.commands[:]
            self.assertEqual(self.api.play({"pad": [0, 1]}, None, "t")["playing"], "a.mp4")
            self.settle()
            seen[how] = [c for c in self.mpv.commands if c in ("screenshot-to-file", "loadfile", "overlay-remove")]
            self.assertEqual(seen[how][:2], ["screenshot-to-file", "loadfile"], how)
            self.assertIn("overlay-add", self.mpv.commands, how)
            self.assertEqual((os.path.basename(self.mpv.path), self.player.source_shader, self.api.transitions.given_up), ("a.mp4", None, ""), how)
        self.assertEqual(seen["pad"], seen["hand"])

    def test_out_of_a_shader_a_clip_dips_by_pad_as_by_hand(self):
        for how in ("hand", "pad"):
            self.settings.data["mix"] = {"transition": "dip", "duration": 0.2}
            self.choose(how)
            del self.mpv.levels[:], self.mpv.log[:]
            self.api.play({"pad": [0, 1]}, None, "t")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not (self.mpv.log and self.mpv.levels and self.mpv.levels[-1] == 0 and self.api.fader.label is None):
                time.sleep(0.01)
            self.assertEqual(os.path.basename(self.mpv.path), "a.mp4", how)
            self.assertIn(-100, self.mpv.levels, "the generator was not dipped to black before the clip (%s)" % how)
            self.assertEqual(self.mpv.levels[-1], 0, how)


if __name__ == "__main__":
    unittest.main()
