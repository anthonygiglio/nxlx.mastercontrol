# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""D75: OSC reaches what the controllers reach, from the table of actions it shares with MIDI (pvj/actions.py).

Every message here goes through the real parser; the last class sends over a real UDP socket on loopback. Nothing
here has met a TouchOSC layout or a real sender."""
import socket
import time
import unittest

from pvj import actions, midi, osc
from pvj.osc import OscServer
from tests.test_osc import ServerLogicTest, msg
from tests.test_shader_engine import ALL, Live


def tr(address, *args, mix=None):
    """What a message means, through the parser."""
    (a, parsed), = osc.parse_packet(msg(address, *args))
    return osc.translate(a, parsed, mix)


class OneTableTest(unittest.TestCase):
    def test_midi_and_osc_make_the_same_call_from_the_same_entry(self):
        both = {"/pvj/fade": "fade", "/pvj/clip/next": "clip_next", "/pvj/clip/prev": "clip_prev", "/pvj/effect/next": "effect_next",
                "/pvj/effect/prev": "effect_prev", "/pvj/shader/next": "shader_next", "/pvj/shader/prev": "shader_prev",
                "/pvj/shader/preset/3": "shader_preset_3", "/pvj/shader/preset/8": "shader_preset_8"}
        for n, (address, action) in enumerate(sorted(both.items())):
            calls = []
            m = midi.MidiMapper(lambda p, b: calls.append((p, b)) or True, [midi.validate_entry({"kind": "note", "number": n, "action": action})], {})
            m.message("x", ("on", 0, n, 127))
            self.assertEqual([tr(address, 1.0)], calls, address)
            self.assertEqual(tr(address, 1.0), actions.press(action))
            self.assertIsNone(tr(address, 0.0), address)                # the release of a button does nothing
            self.assertEqual(tr(address), actions.press(action))        # no argument is a press
        self.assertEqual(tr("/pvj/effect"), actions.press("effect_toggle"))
        self.assertEqual(tr("/pvj/overlay"), actions.press("overlay"))
        # every action MIDI knows that is a plain press or a level with a shape is in the table, and nothing else is
        for name, (kind, _, _) in midi.ACTIONS.items():
            plain = kind == "trigger" and name not in ("pad", "bank_pad", "bank_prev", "bank_next", "scene", "none")
            self.assertEqual(actions.press(name) is not None, plain or kind == "control", name)
            self.assertEqual(actions.level(name, 1.0) is not None, name in midi.SHAPES, name)
        self.assertIsNone(actions.press("shutdown"))
        a, b = actions.press("fade"), actions.press("fade")
        a[1]["seconds"] = 99                                            # a caller cannot change the table
        self.assertEqual(b, ("/api/fade", {"seconds": 2}))

    def test_levels_are_real_units_with_no_dead_zone(self):
        self.assertEqual(tr("/pvj/position/y", -37.5), ("/api/control", {"action": "position_y", "value": -37.5}))
        self.assertEqual(tr("/pvj/position/x", 12), ("/api/control", {"action": "position", "value": 12}))
        self.assertEqual(tr("/pvj/position", 12.0), ("/api/control", {"action": "position", "value": 12.0}))     # the old name stays
        self.assertEqual(tr("/pvj/position/y", 0.5)[1]["value"], 0.5)   # half a step off centre stays half a step: no centre that sits
        self.assertEqual(tr("/pvj/size", 101.0)[1]["value"], 101.0)
        self.assertEqual(tr("/pvj/shader/speed", 1.25), ("/api/shaders/values", {"controls": {"speed": 1.25}}))
        self.assertEqual(tr("/pvj/shader/hue", -90), ("/api/shaders/values", {"controls": {"hue": -90}}))
        self.assertEqual(tr("/pvj/shader/brightness", 0.5), ("/api/shaders/values", {"controls": {"brightness": 0.5}}))
        self.assertEqual(tr("/pvj/effect/amount", 50), ("/api/effects/values", {"controls": {"amount": 0.5}}))     # percent, as the opacity
        for address in ("/pvj/position/y", "/pvj/shader/speed", "/pvj/effect/amount", "/pvj/vibes/dwell", "/pvj/transition/duration"):
            self.assertIsNone(tr(address), address)                     # a level needs its number
            self.assertIsNone(tr(address, "x"), address)

    def test_the_other_addresses(self):
        self.assertEqual(tr("/pvj/fadein"), ("/api/fadein", {"seconds": 2.0}))
        self.assertEqual(tr("/pvj/fadein", 5), ("/api/fadein", {"seconds": 5}))
        self.assertEqual([tr("/pvj/flip/h", 1), tr("/pvj/flip/h", 0.0), tr("/pvj/flip/v", True)],
                         [("/api/control", {"action": "flip_h", "value": True}), ("/api/control", {"action": "flip_h", "value": False}),
                          ("/api/control", {"action": "flip_v", "value": True})])
        self.assertIsNone(tr("/pvj/flip/h"))                            # 0 or 1 is required; the old /fliph still switches over
        self.assertEqual(tr("/fliph", 1.0, mix={"flip_h": True}), ("/api/control", {"action": "flip_h", "value": False}))
        self.assertEqual([tr("/pvj/effect", 1), tr("/pvj/effect", 0)], [("/api/effects", {"on": True}), ("/api/effects", {"on": False})])
        self.assertEqual([tr("/pvj/overlay", 1), tr("/pvj/overlay", 0.0)], [("/api/overlay", {"on": True}), ("/api/overlay", {"on": False})])
        self.assertEqual(tr("/pvj/overlay/file", "logo.png"), ("/api/overlay", {"file": "logo.png"}))
        self.assertIsNone(tr("/pvj/overlay/file", 1))
        self.assertEqual(tr("/pvj/transition", "crossfade"), ("/api/mix", {"transition": "crossfade"}))
        self.assertEqual(tr("/pvj/transition/duration", 2.5), ("/api/mix", {"duration": 2.5}))
        self.assertEqual(tr("/pvj/vibes/dwell", 60), ("/api/vibes", {"dwell": 60}))
        self.assertEqual(tr("/pvj/shader/control/2", 1.0), ("/api/shaders/values", {"control": 2, "level": 127}))
        self.assertEqual(tr("/pvj/effect/control/8", 0.5), ("/api/effects/values", {"control": 8, "level": 64}))
        for bad in (("/pvj/shader/control/2", 1.5), ("/pvj/shader/control/2", -0.1), ("/pvj/shader/control/9", 0.5), ("/pvj/shader/control/0", 0.5),
                    ("/pvj/effect/preset/1", 1.0), ("/pvj/shader/preset/9", 1.0), ("/pvj/shader/control/2",)):
            self.assertIsNone(tr(*bad), bad)
        self.assertEqual(tr("/pvj/shader", "silk"), ("/api/shaders/play", {"id": "silk.fs"}))
        self.assertEqual(tr("/pvj/shader", "silk.fs"), ("/api/shaders/play", {"id": "silk.fs"}))
        for bad in ("", "../x", "a/b", "x" * 90, ".hidden"):
            self.assertIsNone(tr("/pvj/shader", bad), bad)
        self.assertIsNone(tr("/pvj/shader", 3))
        self.assertIsNone(tr("/pvj/freeze", 1))                         # Freeze is the panel's word for pause: /pvj/pause


class ThroughTheApiTest(ServerLogicTest):
    """The same validation and the same path as the panel: a real Api, a fake player."""

    def setUp(self):
        super().setUp()
        self.player.running = True
        self.player.status = lambda: {"running": True, "path": "/x/a.mp4"}

    def wait(self, cond):
        end = time.time() + 4
        while time.time() < end and not cond():
            time.sleep(0.01)
        self.assertTrue(cond())

    def test_the_picture(self):
        self.assertEqual(self.send(msg("/pvj/position/y", 40.0)), 1)
        self.assertEqual(self.send(msg("/pvj/position/x", -20)), 1)
        self.assertEqual((self.api.mix["position"], self.api.mix["position_y"]), (-20.0, 40.0))
        self.assertIn(("position", -200.0, 400.0), self.player.calls)
        self.assertEqual(self.send(msg("/pvj/position/y", 100.5)), 0)   # out of the panel's range: refused by the API, nothing applied
        self.assertEqual(self.api.mix["position_y"], 40.0)
        self.assertEqual(self.send(msg("/pvj/flip/h", 1)), 1)
        self.assertEqual(self.send(msg("/pvj/flip/h", 1)), 1)           # said outright: on twice is on
        self.assertTrue(self.api.mix["flip_h"])
        self.assertEqual(self.send(msg("/pvj/flip/h", 0)), 1)
        self.assertFalse(self.api.mix["flip_h"])

    def test_the_fades(self):
        self.assertEqual(self.send(msg("/pvj/fade", 1.0)), 1)           # a button's press: out
        self.assertEqual(self.send(msg("/pvj/fade", 0.0)), 0)           # its release: nothing
        self.assertEqual(self.api.fader.label, "out")
        self.assertEqual(self.send(msg("/pvj/fade", 1.0)), 1)           # the next press: in
        self.assertEqual(self.api.fader.label, "in")
        self.assertEqual(self.send(msg("/pvj/fadeout", 0.1)), 1)
        self.wait(lambda: ("opacity", 0) in self.player.calls)
        self.assertEqual(self.send(msg("/pvj/fadein", 0.1)), 1)
        self.wait(lambda: self.api.fader.label is None)
        self.assertEqual(self.player.calls[-1], ("opacity", 255))
        self.assertEqual(self.send(msg("/pvj/fadein", 0.0)), 0)         # a button's release sent as seconds: refused, as for /pvj/fadeout

    def test_the_clip_the_transition_and_the_overlay(self):
        self.assertEqual(self.send(msg("/pvj/clip/next", 1.0)), 1)
        self.assertEqual(self.send(msg("/pvj/clip/prev")), 1)
        self.assertEqual([c for c in self.player.calls if c[0] == "playlist_step"], [("playlist_step", True), ("playlist_step", False)])
        self.assertEqual(self.send(msg("/pvj/transition", "crossfade")), 1)
        self.assertEqual(self.api._mix_settings()["transition"], "crossfade")
        self.assertEqual(self.api._mix_settings()["duration"], 1.0)     # the duration stayed
        self.assertEqual(self.send(msg("/pvj/transition/duration", 3.5)), 1)
        self.assertEqual((self.api._mix_settings()["transition"], self.api._mix_settings()["duration"]), ("crossfade", 3.5))
        for bad in (msg("/pvj/transition", "explode"), msg("/pvj/transition/duration", 60.0), msg("/pvj/transition/duration", 0.0)):
            self.assertEqual(self.send(bad), 0)
        self.assertEqual((self.api._mix_settings()["transition"], self.api._mix_settings()["duration"]), ("crossfade", 3.5))
        self.assertEqual(self.api.handle("POST", "/api/mix", {}, osc.OSC_DEVICE, "t")[0], 400)
        self.assertEqual(self.send(msg("/pvj/overlay", 1)), 0)          # no picture chosen: refused, as on the panel
        for bad in ("../secret.png", "a.mp4", "nothere.txt"):
            self.assertEqual(self.send(msg("/pvj/overlay/file", bad)), 0, bad)
        self.assertEqual(self.api.settings.data["overlay"]["file"], "")

    def test_shaders_and_effects_need_their_module_like_the_panel(self):
        for packet in (msg("/pvj/shader/next", 1.0), msg("/pvj/shader/speed", 1.0), msg("/pvj/effect", 1), msg("/pvj/effect/amount", 50),
                       msg("/pvj/shader", "silk"), msg("/pvj/vibes/dwell", 60)):
            self.assertEqual(self.send(packet), 0)
        self.assertTrue(any("409" in line for line in self.logs))

    def test_a_real_udp_socket_on_loopback_and_no_reply(self):
        server = OscServer(self.api, port=0, host="127.0.0.1", log=self.logs.append)
        server.start()
        self.addCleanup(server.stop)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        for packet in (msg("/pvj/position/y", 25.0), msg("/pvj/fade", 1.0), msg("/pvj/flip/v", 1), msg("/pvj/transition", "dip"), msg("/pvj/clip/next", 1.0)):
            sock.sendto(packet, ("127.0.0.1", server.port))
        self.wait(lambda: ("playlist_step", True) in self.player.calls)
        self.assertEqual((self.api.mix["position_y"], self.api.mix["flip_v"], self.api.fader.label, self.api._mix_settings()["transition"]),
                         (25.0, True, "out", "dip"))
        sock.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(1024)                                         # still: the receiver never replies


class ShadersOverOscTest(Live):
    """With the Shaders and Vibes module on and a shader on the screen: the engine's own fake player and clock."""

    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.upload("two.fs", ALL)
        self.engine.play("all.fs")
        self.logs = []
        self.server = OscServer(self.api, log=self.logs.append)

    def send(self, packet):
        done = self.server.handle_packet(packet, "192.168.1.20")
        self.pump()
        return done

    def test_the_shader_on_screen(self):
        self.assertEqual(self.send(msg("/pvj/shader/speed", 2.5)), 1)
        self.assertEqual(self.engine.state()["playing"]["controls"]["speed"], 2.5)
        self.assertEqual(self.send(msg("/pvj/shader/hue", -90.0)), 1)
        self.assertEqual(self.send(msg("/pvj/shader/brightness", 1.5)), 1)
        self.assertEqual((self.engine.playing["controls"]["hue"], self.engine.playing["controls"]["brightness"]), (-90.0, 1.5))
        self.send(msg("/pvj/shader/speed", 9.0))                       # beyond the panel's range: the engine's own rule holds
        self.assertLessEqual(self.engine.state()["playing"]["controls"]["speed"], 4.0)
        self.assertEqual(self.send(msg("/pvj/shader/control/1", 1.0)), 1)       # the first input: a number, MIN 0 to MAX 2
        self.assertEqual(self.engine.state()["playing"]["values"]["level"], 2.0)
        self.assertEqual(self.send(msg("/pvj/shader/control/2", 1.0)), 1)       # the second: a switch, on from the middle up
        self.assertEqual(self.engine.state()["playing"]["values"]["lit"], True)
        self.assertEqual(self.send(msg("/pvj/shader/control/7", 0.5)), 0)       # this shader has six
        self.assertEqual(self.send(msg("/pvj/shader/preset/1", 1.0)), 0)        # and no preset

    def test_a_shader_by_its_name_is_queued_and_never_waits_for_the_gpu(self):
        with self.engine._lock:                                         # as if the GPU were looking at a shader
            t0 = time.monotonic()
            done = self.server.handle_packet(msg("/pvj/shader", "two"), "192.168.1.20")
            self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(done, 1)
        self.pump()
        self.assertEqual(self.engine.playing["id"], "two.fs")
        self.assertEqual(self.send(msg("/pvj/shader", "nothere")), 0)   # said at once: no such shader
        self.assertEqual(self.engine.playing["id"], "two.fs")
        # a panel's Play is the engine's own call, as it was
        self.assertEqual(self.api.handle("POST", "/api/shaders/play", {"id": "all.fs"}, {"id": "phone", "role": "live"}, "t")[0], 200)
        self.assertEqual(self.engine.playing["id"], "all.fs")

    def test_how_long_each_shader_stays(self):
        self.assertEqual(self.send(msg("/pvj/vibes/dwell", 45)), 1)
        self.assertEqual(self.engine.rotation(None)["dwell"], 45)
        self.assertEqual(self.send(msg("/pvj/vibes/dwell", 5)), 0)      # under the panel's fifteen seconds
        self.assertEqual(self.engine.rotation(None)["dwell"], 45)

    def test_the_effect_said_outright(self):
        fx = self.api.effects
        self.assertEqual(self.send(msg("/pvj/effect", 0)), 1)           # off while none is on: nothing to do, and no error
        self.assertFalse(fx._intent)
        self.assertEqual(self.send(msg("/pvj/effect", 1)), 1)
        self.assertTrue(fx._intent)
        self.assertEqual(self.send(msg("/pvj/effect", 1)), 1)           # on twice is on (the one button would have switched it off)
        self.assertTrue(fx._intent)
        self.assertEqual(self.send(msg("/pvj/effect", 0)), 1)
        self.assertFalse(fx._intent)
        self.assertEqual(self.send(msg("/pvj/effect")), 1)              # no argument: the one button
        self.assertTrue(fx._intent)
        self.assertEqual(self.send(msg("/pvj/effect/next", 1.0)), 1)
        self.assertEqual(self.api.handle("POST", "/api/effects", {"on": "yes"}, osc.OSC_DEVICE, "t")[0], 400)


if __name__ == "__main__":
    unittest.main()
