# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A pad that holds a generator shader instead of a clip (D73): what it stores, what a tap does from every place a
pad can be tapped from, and what it refuses."""
import copy
import os
import random
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
                             ({"preset": "Slow"}, 400), ({"shader": TWO, "preset": "Slow"}, 404)):
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
                    {"file": "", "shader": "x.glsl"}):
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
        self.assertEqual(midi.light_state(action, {"pads": [[""] * 12], "shader": None, "running": False, "playing": None}), "off")

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


if __name__ == "__main__":
    unittest.main()
