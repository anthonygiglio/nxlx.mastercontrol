# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import sys
import tempfile
import unittest

from pvj import autostart
from pvj.api import ApiError
from pvj.player import PlayerError
from pvj.settings import Settings
from tests.test_server import ServerBase

ROOT = os.path.join(os.path.dirname(__file__), "..")


class FakeApi:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.pid = None
        outer = self

        class Ipc:
            def request(self, *cmd):
                if outer.pid is None:
                    raise PlayerError("no player")
                return outer.pid
        self.player = type("P", (), {"ipc": Ipc()})()

    def play(self, body, device, client):
        if self.fail:
            raise self.fail
        self.calls.append(body)


class ImportTest(unittest.TestCase):
    def test_every_module_imports_first_on_its_own(self):
        # a circular import only shows when a module is the first one loaded
        for mod in ("api", "autostart", "scheduler", "dmx", "midi", "streams", "osc", "server", "settings"):
            r = subprocess.run([sys.executable, "-c", "import pvj.%s" % mod], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (mod, r.stderr[-300:]))


class ValidateTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(autostart.validate({"mode": "file", "file": "a.mp4", "loop": False, "delay": 10})["file"], "a.mp4")
        self.assertEqual(autostart.validate({"mode": "all"})["mode"], "all")
        self.assertEqual(autostart.validate({"mode": "preset", "preset": "startlessonce05"})["preset"], "startlessonce05")
        self.assertEqual(autostart.validate({"mode": "off"})["mode"], "off")

    def test_bad(self):
        bad = [[], {"mode": "reboot"}, {"mode": "file"}, {"mode": "file", "file": "../x.mp4"}, {"mode": "file", "file": "a.txt"},
               {"mode": "preset"}, {"mode": "preset", "preset": "startless\n"}, {"mode": "preset", "preset": "rm -rf"},
               {"mode": "preset", "preset": "startwifi01"}, {"mode": "all", "loop": "yes"}, {"mode": "all", "delay": -1},
               {"mode": "all", "delay": 121}, {"mode": "all", "delay": True}, {"mode": "all", "delay": float("nan")}]
        for body in bad:
            with self.assertRaises(autostart.AutostartError, msg=str(body)):
                autostart.validate(body)


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        self.settings.load()
        self.api = FakeApi()
        self.slept = []
        self.a = autostart.Autostart(self.api, self.settings, log=lambda *_: None, sleep=self.slept.append)

    def cfg(self, **kw):
        self.settings.data["autostart"] = autostart.validate(kw, self.settings.data["autostart"])

    def test_the_level_is_offered_to_a_restarted_player_until_it_lands(self):
        # the seventh review: one try, at the first sight of the new player, and a failure nobody heard of
        said, answers, asked = [], [False, False, True], []
        self.a.log = said.append
        self.api.restore_level = lambda: (asked.append(1), answers.pop(0))[1]
        self.api.pid = 100
        for _ in range(6):
            self.a.tick()
        self.assertEqual(len(asked), 3, "the level was not offered again until the player took it, or went on being offered")
        self.assertEqual([s for s in said if "level" in s], [])

    def test_a_level_that_never_lands_is_given_up_and_logged_once(self):
        said, asked = [], []
        self.a.log = said.append

        def never():
            asked.append(1)
            if len(asked) % 2:
                return False
            raise RuntimeError("the player is away")
        self.api.restore_level = never
        self.api.pid = 100
        for _ in range(autostart.LEVEL_TRIES + 4):
            self.a.tick()
        self.assertEqual(len(asked), autostart.LEVEL_TRIES)
        self.assertEqual(len([s for s in said if "could not put the picture's level back" in s]), 1, said)
        self.api.pid = 101                                  # the next player gets its own tries
        self.a.tick()
        self.assertEqual(len(asked), autostart.LEVEL_TRIES + 1)

    def test_a_level_owed_to_a_player_that_is_gone_waits_for_the_next_one(self):
        asked = []
        self.api.restore_level = lambda: (asked.append(self.api.pid), False)[1]
        self.api.pid = 100
        self.a.tick()
        self.api.pid = None
        self.a.tick(), self.a.tick()
        self.assertEqual(asked, [100])
        self.api.pid = 101
        self.a.tick()
        self.assertEqual(asked, [100, 101])

    def test_off_by_default_does_nothing(self):
        self.api.pid = 100
        self.assertFalse(self.a.tick())
        self.assertEqual(self.api.calls, [])

    def test_waits_for_the_player_then_plays_once(self):
        self.cfg(mode="file", file="a.mp4")
        self.assertFalse(self.a.tick())          # no player yet
        self.api.pid = 100
        self.assertTrue(self.a.tick())
        self.assertFalse(self.a.tick())          # same player: never again
        self.assertEqual(self.api.calls, [{"file": "a.mp4", "loop": True}])
        self.assertTrue(self.a.last["ok"])

    def test_a_stop_from_the_panel_stays_stopped(self):
        self.cfg(mode="file", file="a.mp4")
        self.api.pid = 100
        self.a.tick()
        for _ in range(20):
            self.a.tick()                        # the player idles for a long time: still nothing new
        self.assertEqual(len(self.api.calls), 1)

    def test_a_restarted_player_is_started_again(self):
        self.cfg(mode="all")
        self.api.pid = 100
        self.a.tick()
        self.api.pid = None                      # crashed
        self.assertFalse(self.a.tick())
        self.api.pid = 222                       # systemd brought it back
        self.assertTrue(self.a.tick())
        self.assertEqual(self.api.calls, [{"preset": "startless"}, {"preset": "startless"}])

    def test_modes(self):
        self.api.pid = 1
        self.cfg(mode="all", loop=False)
        self.a.tick()
        self.api.pid = 2
        self.cfg(mode="preset", preset="startlessonce05")
        self.a.tick()
        self.assertEqual(self.api.calls, [{"preset": "startlessonce"}, {"preset": "startlessonce05"}])

    def test_delay_is_full_at_boot_and_short_after_a_crash(self):
        self.cfg(mode="all", delay=30)
        self.api.pid = 1
        self.a.tick()
        self.api.pid = 2
        self.a.tick()
        self.assertEqual(self.slept, [30, 5])

    def test_failure_is_recorded_and_not_retried_in_a_loop(self):
        self.cfg(mode="file", file="gone.mp4")
        self.api.fail = ApiError(404, "file not found")
        self.api.pid = 1
        self.assertTrue(self.a.tick())
        self.assertEqual((self.a.last["ok"], self.a.last["message"]), (False, "file not found"))
        self.api.fail = RuntimeError("boom")
        self.api.pid = 2
        self.a.tick()
        self.assertIn("boom", self.a.last["message"])
        self.assertFalse(self.a.tick())

    def test_thread_starts_and_stops(self):
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None, interval=0.01)
        a.start(), a.start()
        a.stop()
        self.assertIsNone(a._thread)


class NewModesTest(unittest.TestCase):
    """From the manual deep dive: installations want a slideshow, shuffle, a pad, and whatever is on a USB stick."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        self.settings.load()
        self.api = FakeApi()
        self.api.drives = []
        self.api.usb_drives = lambda: self.api.drives
        self.now = [1000.0]
        self.a = autostart.Autostart(self.api, self.settings, log=lambda *_: None, sleep=lambda *_: None, clock=lambda: self.now[0])

    def cfg(self, **kw):
        self.settings.data["autostart"] = autostart.validate(kw, self.settings.data["autostart"])

    def test_slideshow_shuffle_and_pad(self):
        self.cfg(mode="slideshow", seconds=8, shuffle=True)
        self.a.run_now()
        self.assertEqual(self.api.calls[-1], {"slideshow": {"source": "media", "seconds": 8, "shuffle": True, "ending": "loop"}})
        self.cfg(mode="all", shuffle=True)
        self.a.run_now()
        self.assertEqual(self.api.calls[-1], {"preset": "startless", "shuffle": True})
        self.cfg(mode="pad", pad=[1, 4])
        self.a.run_now()
        self.assertEqual(self.api.calls[-1], {"pad": [1, 4]})
        for bad in ({"seconds": 0}, {"seconds": True}, {"pad": [0]}, {"pad": [0, 12]}, {"pad": [16, 0]}, {"shuffle": "yes"}, {"mode": "dance"}):
            with self.assertRaises(autostart.AutostartError, msg=str(bad)):
                autostart.validate(bad, self.settings.data["autostart"])

    def test_a_usb_stick_plugged_in_plays_and_one_already_there_plays_once(self):
        self.cfg(mode="usb")
        self.api.pid = 100
        self.a.tick()                                            # start: no drive yet
        self.assertEqual(self.a.last["ok"], False)
        self.tick()
        self.assertEqual(self.api.calls, [])
        self.api.drives = [{"drive": "SHOW", "files": [{"name": "a.mp4", "size": 1}]}]
        self.assertTrue(self.tick())                             # plugged in: that drive plays
        self.assertEqual(self.api.calls, [{"usb_drive": "SHOW", "shuffle": False}])
        self.tick()
        self.tick()
        self.assertEqual(len(self.api.calls), 1)                 # still the same stick: not again
        self.api.drives = []
        self.tick()
        self.api.drives = [{"drive": "OTHER", "files": [{"name": "b.mp4", "size": 1}]}]
        self.tick()
        self.assertEqual(self.api.calls[-1], {"usb_drive": "OTHER", "shuffle": False})   # swapped: the new one
        self.api.drives = [{"drive": "EMPTY", "files": []}] + self.api.drives
        self.tick()
        self.assertEqual(len(self.api.calls), 2)                 # a drive without clips is ignored

    def tick(self, seconds=11):
        self.now[0] += seconds
        return self.a.tick()

    def test_a_stick_mounted_during_boot_is_not_missed(self):
        """Review finding: mounted between the first run and the first look, it was taken as already seen."""
        self.cfg(mode="usb")
        self.api.pid = 100
        self.api.drives = [{"drive": "SHOW", "files": [{"name": "a.mp4", "size": 1}]}]
        self.a.tick()                                            # the box starts with the stick in
        self.assertEqual(self.api.calls, [{"usb_drive": "SHOW", "shuffle": False}])
        self.tick()
        self.assertEqual(len(self.api.calls), 1)                 # and plays it once

    def test_switching_back_to_usb_mode_does_not_interrupt_the_show(self):
        self.cfg(mode="usb")
        self.api.pid = 100
        self.a.tick()
        self.cfg(mode="all")
        self.tick()
        self.api.drives = [{"drive": "SHOW", "files": [{"name": "a.mp4", "size": 1}]}]
        self.tick()
        self.cfg(mode="usb")
        self.tick()
        self.tick()
        self.assertEqual(self.api.calls, [])

    def test_a_flapping_drive_does_not_restart_playback(self):
        self.cfg(mode="usb")
        self.api.pid = 100
        self.a.tick()
        stick = [{"drive": "SHOW", "files": [{"name": "a.mp4", "size": 1}]}]
        self.api.drives = stick
        self.tick()
        for _ in range(5):                                       # a bad contact: gone and back every 2 s
            self.api.drives = []
            self.tick(2)
            self.api.drives = stick
            self.tick(2)
        self.assertEqual(len(self.api.calls), 1)

    def test_settings_saved_before_the_new_keys_still_work(self):
        self.settings.data["autostart"] = {"mode": "all", "file": "", "preset": "", "loop": True, "delay": 0}
        self.a.run_now()
        self.assertEqual(self.api.calls[-1], {"preset": "startless"})
        self.assertEqual(autostart.validate({"mode": "slideshow"}, self.settings.data["autostart"])["seconds"], 10)


class AutostartApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.api.autostart = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def invite(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "g", "role": role}, token=self.full)[1]["token"]

    def test_roundtrip_persist_and_bad_input(self):
        st, body, _ = self.call("GET", "/api/autostart", token=self.full)
        self.assertEqual((st, body["config"]["mode"]), (200, "off"))
        st, body, _ = self.call("POST", "/api/autostart", {"mode": "file", "file": "a.mp4", "delay": 5}, token=self.full)
        self.assertEqual((st, body["config"]["file"], body["config"]["delay"]), (200, "a.mp4", 5))
        self.assertEqual(Settings(self.settings.path).load()["autostart"]["mode"], "file")
        for bad in ({"mode": "file", "file": "../a.mp4"}, {"mode": "shutdown"}, {"mode": "all", "delay": 999}):
            self.assertEqual(self.call("POST", "/api/autostart", bad, token=self.full)[0], 400)
        self.assertEqual(self.settings.data["autostart"]["file"], "a.mp4")     # unchanged by the bad requests

    def test_test_button_plays_now_and_reports(self):
        self.call("POST", "/api/autostart", {"mode": "file", "file": "a.mp4"}, token=self.full)
        st, body, _ = self.call("POST", "/api/autostart/test", {}, token=self.invite("live"))
        self.assertEqual((st, body["message"]), (200, "started"))
        self.assertIn(("play", [os.path.join(os.path.realpath(self.media), "a.mp4")], True, False), self.player.calls)

    def test_roles(self):
        view, live = self.invite("view"), self.invite("live")
        self.assertEqual(self.call("GET", "/api/autostart", token=view)[0], 200)
        for token in (view, live):
            self.assertEqual(self.call("POST", "/api/autostart", {"mode": "off"}, token=token)[0], 403)
        self.assertEqual(self.call("POST", "/api/autostart/test", {}, token=view)[0], 403)
        self.assertEqual(self.call("GET", "/api/autostart")[0], 401)


if __name__ == "__main__":
    unittest.main()
