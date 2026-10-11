# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import unittest

from pvj import pinscreen
from pvj.player import PlayerError
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


class Player:
    def __init__(self):
        self.shown = []
        self.st = {"running": True, "path": None}
        self.down = False
        outer = self

        class Ipc:
            def request(self, *cmd):
                if outer.down:
                    raise PlayerError("gone")
                outer.shown.append(cmd)
        self.ipc = Ipc()

    def status(self):
        if self.down:
            raise PlayerError("gone")
        return self.st


class Api:
    def __init__(self):
        self.player = Player()

    def _ip_json(self):
        return [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
                {"ifname": "eth0", "addr_info": [{"family": "inet6", "local": "fe80::1"}, {"family": "inet", "local": "192.168.0.169"}]}]


class Auth:
    def __init__(self):
        self.devices = []
        self.current_pin = "6196"

    def list_devices(self):
        return list(self.devices)


class PinScreenTest(unittest.TestCase):
    def setUp(self):
        self.api, self.auth = Api(), Auth()
        self.p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="nxlx-mastercontrol")

    def test_shows_the_pin_and_the_addresses_on_an_idle_unpaired_box(self):
        self.assertTrue(self.p.tick())
        cmd = self.api.player.shown[0]
        self.assertEqual((cmd[0], cmd[2]), ("show-text", pinscreen.SHOW_MS))
        self.assertEqual(cmd[1].split("\n"), ["nxlx.mastercontrol", "Open on your phone:", "http://nxlx-mastercontrol.local/",
                                              "http://192.168.0.169/", "PIN  6196"])      # no loopback, no IPv6

    def test_never_again_once_any_device_has_paired(self):
        self.auth.devices = [{"id": "x"}]
        self.assertFalse(self.p.tick())
        self.assertEqual(self.api.player.shown, [])

    def test_never_over_a_playing_clip(self):
        self.api.player.st = {"running": True, "path": "/var/lib/pvj/video/a.mp4"}
        self.assertFalse(self.p.tick())
        self.assertEqual(self.api.player.shown, [])

    def test_nothing_when_the_player_is_not_running_or_fails(self):
        self.api.player.st = {"running": False}
        self.assertFalse(self.p.tick())
        self.api.player.st = {"running": True, "path": None}
        self.api.player.down = True
        self.assertFalse(self.p.tick())

    def test_text_is_limited_to_safe_characters_because_mpv_expands_dollar_braces(self):
        self.p.hostname = "evil${path}\n%x;\\"
        self.auth.current_pin = "1${x}2"
        self.p.tick()
        text = self.api.player.shown[0][1]
        for bad in ("$", "{", "}", "%", ";", "\\"):
            self.assertNotIn(bad, text)
        self.assertEqual(pinscreen.clean(r"a b.c:/d_e-f${}\;"), "a b.c:/d_e-f")

    def test_it_comes_back_if_every_device_is_removed_and_a_starting_clip_clears_it(self):
        self.auth.devices = [{"id": "x"}]
        self.assertFalse(self.p.wanted())
        self.auth.devices = []                        # the way back in for a box nobody can reach
        self.assertTrue(self.p.wanted())
        self.p.clear()
        self.assertEqual(self.api.player.shown[-1][:2], ("show-text", ""))

    def test_the_text_lasts_a_little_longer_than_the_tick_so_it_is_steady(self):
        self.assertGreater(pinscreen.SHOW_MS / 1000.0, 3.0)
        self.assertLess(pinscreen.SHOW_MS / 1000.0, 5.0)

    def test_a_missing_address_list_still_shows_the_pin(self):
        self.api._ip_json = lambda: (_ for _ in ()).throw(OSError("no ip"))
        self.assertTrue(self.p.tick())
        self.assertIn("PIN  6196", self.api.player.shown[0][1])

    def test_thread_starts_and_stops(self):
        p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, interval=0.01, hostname="h")
        p.start(), p.start()
        p.stop()
        self.assertIsNone(p._thread)


if __name__ == "__main__":
    unittest.main()
