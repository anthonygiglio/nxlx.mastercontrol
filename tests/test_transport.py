# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import threading
import unittest

from pvj.api import Fader
from pvj.player import Player, PlayerError
from tests.test_server import ServerBase


class FaderTest(unittest.TestCase):
    def ramp(self, seconds, cost):
        """Run one fade from 0 to 100 on a clock of our own, where each step costs the player `cost` seconds.
        Returns (the levels set, when the fade was over)."""
        now, levels, over, done = [100.0], [], [], threading.Event()

        def apply(level):
            levels.append(level)
            now[0] += cost

        def sleep(wait):
            now[0] += wait

        def then():
            over.append(now[0] - 100.0)
            done.set()
        Fader(apply, clock=lambda: now[0], sleep=sleep).ramp(0, 100, seconds, then=then)
        self.assertTrue(done.wait(5))
        return levels, over[0]

    def test_a_fade_takes_the_seconds_asked_for_whatever_a_step_costs(self):
        # It slept a full step after each one on top of what the step took: 2 seconds became 2.6 on a Pi 4.
        levels, took = self.ramp(2.0, 0.015)
        self.assertEqual((len(levels), levels[0], levels[-1]), (40, 2.5, 100.0))
        self.assertAlmostEqual(took, 2.0, places=6)

    def test_a_fade_whose_steps_cost_more_than_their_share_never_sleeps_and_sets_every_level(self):
        levels, took = self.ramp(1.0, 0.08)
        self.assertEqual((len(levels), levels[-1]), (20, 100.0))
        self.assertAlmostEqual(took, 20 * 0.08, places=6)      # as fast as the player allows, and no slower


class TransportApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_seek_to_next_and_prev(self):
        self.assertEqual(self.post("/api/control", {"action": "seek_to", "value": 42.5})[0], 200)
        self.assertIn(("seek_to", 42.5), self.player.calls)
        for bad in (-1, 24 * 3600 + 1, "10", True, None):
            self.assertEqual(self.post("/api/control", {"action": "seek_to", "value": bad})[0], 400, bad)
        self.assertEqual(self.post("/api/control", {"action": "next"})[0], 200)
        self.assertEqual(self.post("/api/control", {"action": "prev"})[0], 200)
        self.assertIn(("playlist_step", True), self.player.calls)
        self.assertIn(("playlist_step", False), self.player.calls)

    def test_next_with_nothing_after_is_a_clear_409(self):
        self.player.playlist_step = lambda forward: False
        st, body, _ = self.post("/api/control", {"action": "next"})
        self.assertEqual((st, "no next clip" in body["error"]), (409, True))

    def test_fade_in_leaves_blackout_and_ramps_up(self):
        self.post("/api/blackout", {"on": True})
        self.assertTrue(self.api.mix["blackout"])
        self.assertEqual(self.post("/api/fadein", {"seconds": 0.2})[0], 200)
        self.assertFalse(self.api.mix["blackout"])
        import time
        time.sleep(0.5)
        opacities = [c[1] for c in self.player.calls if c[0] == "opacity"]
        self.assertEqual(opacities[-1], 255)                                # it ended at full opacity
        self.assertIn(0, opacities)                                          # and started from black
        for bad in (0, 31, "2", None):
            self.assertEqual(self.post("/api/fadein", {"seconds": bad})[0], 400, bad)

    def test_test_pattern_on_off_and_status(self):
        st, body, _ = self.post("/api/testpattern", {"on": True})
        self.assertEqual((st, body), (200, {"test_pattern": True}))
        self.assertEqual(self.player.calls[[c[0] for c in self.player.calls].index("play")][1], [self.player.TEST_PATTERN])
        self.player.status = lambda: {"running": True, "path": self.player.TEST_PATTERN}
        p = self.call("GET", "/api/status", token=self.full)[1]["player"]
        self.assertEqual((p["path"], p["test_pattern"]), (None, True))           # never shows the internal source name
        self.assertEqual(self.post("/api/testpattern", {"on": False})[1], {"test_pattern": False})
        self.assertIn(("clear",), self.player.calls)
        self.assertEqual(self.post("/api/testpattern", {"on": "yes"})[0], 400)

    def test_roles(self):
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        for path, body in (("/api/fadein", {"seconds": 1}), ("/api/testpattern", {"on": True}), ("/api/control", {"action": "next"}),
                           ("/api/control", {"action": "seek_to", "value": 1})):
            self.assertEqual(self.post(path, body, token=view)[0], 403, path)


class PlaylistStepTest(unittest.TestCase):
    """Player.playlist_step against a fake mpv."""

    def make(self, count, pos, loop):
        p = Player.__new__(Player)
        sent = []

        class Ipc:
            def request(self, *cmd):
                sent.append(cmd)
                return {"playlist-count": count, "playlist-pos": pos, "loop-playlist": loop}.get(cmd[1]) if cmd[0] == "get_property" else None
        p.ipc = Ipc()
        return p, sent

    def test_steps_and_edges(self):
        p, sent = self.make(3, 1, "no")
        self.assertTrue(p.playlist_step(True))
        self.assertEqual(sent[-1], ("playlist-next", "force"))
        self.assertTrue(p.playlist_step(False))
        self.assertEqual(sent[-1], ("playlist-prev", "force"))
        p, _ = self.make(3, 2, "no")
        self.assertFalse(p.playlist_step(True))                              # last clip, no loop
        p, sent = self.make(3, 2, "inf")
        self.assertTrue(p.playlist_step(True))                               # looping: wraps
        p, _ = self.make(3, 0, "no")
        self.assertFalse(p.playlist_step(False))
        p, _ = self.make(1, 0, "inf")
        self.assertFalse(p.playlist_step(True))                              # one clip: nothing to step to


if __name__ == "__main__":
    unittest.main()
