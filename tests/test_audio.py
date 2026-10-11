# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest import mock

from pvj import autostart
from pvj.player import PlayerError
from pvj.settings import Settings
from tests.test_server import ServerBase

DEVICES = [{"name": "auto", "description": "Autoselect device"},
           {"name": "alsa/plughw:CARD=Headphones,DEV=0", "description": "bcm2835 Headphones"},
           {"name": "alsa/sysdefault:CARD=vc4hdmi0", "description": "vc4-hdmi-0"},
           {"name": "alsa/sysdefault:CARD=vc4hdmi1", "description": "vc4-hdmi-1"},
           {"name": "alsa/default:CARD=Mix", "description": "Default Audio Device"}]


class FakeIpc:
    def __init__(self):
        self.calls = []
        self.down = False

    def request(self, *cmd):
        if self.down:
            raise PlayerError("no player")
        self.calls.append(cmd)
        if cmd[:2] == ("get_property", "audio-device-list"):
            return list(DEVICES)
        return None


def connectors(*names):
    return [{"connector": n, "status": st, "modes": []} for n, st in names]


class AudioTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.ipc = FakeIpc()
        self.player.ipc = self.ipc
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        p = mock.patch("pvj.api.hardware.drm_connectors", return_value=connectors(("HDMI-A-1", "connected"), ("HDMI-A-2", "disconnected")))
        p.start()
        self.addCleanup(p.stop)

    def sets(self):
        return [c[2] for c in self.ipc.calls if c[:2] == ("set_property", "audio-device")]

    def test_automatic_means_hdmi_on_the_connected_port_not_the_headphone_jack(self):
        st, body, _ = self.call("GET", "/api/audio", token=self.full)
        self.assertEqual((st, body["device"], body["automatic_is"]), (200, "auto", "alsa/sysdefault:CARD=vc4hdmi0"))
        self.assertEqual(len(body["devices"]), 5)
        self.assertEqual(self.api.apply_audio(), "alsa/sysdefault:CARD=vc4hdmi0")
        self.assertEqual(self.sets(), ["alsa/sysdefault:CARD=vc4hdmi0"])

    def test_the_second_hdmi_port_and_no_screen(self):
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=connectors(("HDMI-A-1", "disconnected"), ("HDMI-A-2", "connected"))):
            self.assertEqual(self.api.apply_audio(), "alsa/sysdefault:CARD=vc4hdmi1")
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=connectors(("HDMI-A-1", "disconnected"))):
            self.assertEqual(self.api.apply_audio(), "auto")                 # nothing to prefer: mpv decides
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=[]):
            self.assertEqual(self.api.apply_audio(), "auto")                 # a PC with no Pi connectors

    def test_choosing_an_output_is_saved_applied_and_survives_a_restart_of_the_program(self):
        st, body, _ = self.call("POST", "/api/audio", {"device": "alsa/default:CARD=Mix"}, token=self.full)
        self.assertEqual((st, body["in_use"]), (200, "alsa/default:CARD=Mix"))
        self.assertEqual(self.sets()[-1], "alsa/default:CARD=Mix")
        self.assertEqual(Settings(self.settings.path).load()["audio"]["device"], "alsa/default:CARD=Mix")

    def test_only_listed_outputs_are_accepted(self):
        for bad in ("hw:0,0", "alsa/../../x", "", None, 5, ["auto"], "alsa/sysdefault:CARD=vc4hdmi9", "auto\n"):
            st, _, _ = self.call("POST", "/api/audio", {"device": bad}, token=self.full)
            self.assertEqual(st, 400, repr(bad))
        self.assertEqual(self.settings.data["audio"]["device"], "auto")
        self.assertEqual(self.sets(), [])

    def test_a_saved_output_that_has_gone_falls_back_to_automatic(self):
        self.settings.data["audio"]["device"] = "alsa/default:CARD=Unplugged"
        self.assertEqual(self.api.apply_audio(), "alsa/sysdefault:CARD=vc4hdmi0")

    def test_no_player_is_a_503_not_a_crash(self):
        self.ipc.down = True
        self.assertEqual(self.call("GET", "/api/audio", token=self.full)[0], 503)
        self.assertEqual(self.call("POST", "/api/audio", {"device": "auto"}, token=self.full)[0], 503)

    def test_roles(self):
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/audio", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/audio", {"device": "auto"}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/audio", {"device": "auto"}, token=live)[0], 200)      # the Operator's since D80

    def test_a_screen_switched_on_after_boot_moves_the_sound_to_hdmi(self):
        current = ["alsa/plughw:CARD=Headphones,DEV=0"]

        class Ipc(FakeIpc):
            def request(inner, *cmd):
                if cmd == ("get_property", "audio-device"):
                    return current[0]
                if cmd[:2] == ("set_property", "audio-device"):
                    current[0] = cmd[2]
                return FakeIpc.request(inner, *cmd)
        self.player.ipc = Ipc()
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=connectors(("HDMI-A-1", "disconnected"))):
            self.assertEqual(self.api.ensure_audio(), "auto")                # no screen yet: mpv's own choice
        current[0] = "alsa/plughw:CARD=Headphones,DEV=0"
        self.assertEqual(self.api.ensure_audio(), "alsa/sysdefault:CARD=vc4hdmi0")     # the screen came on
        self.assertEqual(current[0], "alsa/sysdefault:CARD=vc4hdmi0")
        before = len(self.player.ipc.calls)
        self.api.ensure_audio()
        self.assertFalse([c for c in self.player.ipc.calls[before:] if c[:2] == ("set_property", "audio-device")])   # already right: no change

    def test_ensure_audio_never_raises_when_the_player_is_gone(self):
        self.ipc.down = True
        self.assertIsNone(self.api.ensure_audio())

    def test_autostart_rechecks_the_output_every_few_ticks(self):
        pid = [100]
        calls = []

        class Ipc(FakeIpc):
            def request(inner, *cmd):
                return pid[0] if cmd == ("get_property", "pid") else FakeIpc.request(inner, *cmd)
        self.player.ipc = Ipc()
        self.api.ensure_audio = lambda: calls.append(1)
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        for _ in range(autostart.AUDIO_CHECK_EVERY * 2 + 1):
            a.tick()
        self.assertGreaterEqual(len(calls), 2)

    def test_a_restarted_player_gets_the_output_again_before_autostart_plays(self):
        pid = [100]

        class Ipc(FakeIpc):
            def request(inner, *cmd):
                if cmd == ("get_property", "pid"):
                    return pid[0]
                return FakeIpc.request(inner, *cmd)
        self.player.ipc = Ipc()
        self.settings.data["autostart"] = autostart.validate({"mode": "all"})
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        plays = []
        self.api.play = lambda body, d, c: plays.append(len(self.player.ipc.calls))
        self.assertTrue(a.tick())
        self.assertFalse(a.tick())
        pid[0] = 200                                             # crashed and restarted
        self.assertTrue(a.tick())
        sets = [i for i, c in enumerate(self.player.ipc.calls) if c[:2] == ("set_property", "audio-device")]
        self.assertEqual(len(sets), 2)
        self.assertTrue(all(s < p for s, p in zip(sets, plays)), "the sound output must be set before the clip starts")


if __name__ == "__main__":
    unittest.main()
