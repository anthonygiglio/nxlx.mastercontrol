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

    def test_the_preset_gone_since(self):
        self.a_preset(name="Slow")
        self.give(preset="Slow")
        self.engine.preset_delete(ONE, "Slow")
        del self.player.calls[:]
        with self.assertRaises(ApiError) as c:
            self.tap()
        self.assertEqual(c.exception.status, 404)
        self.assertIn("this pad's preset of %s is gone" % ONE, c.exception.message)
        self.assertEqual(self.player.calls, [])

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
        self.vibes.stop()

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
