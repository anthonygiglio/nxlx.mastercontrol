# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Play after Stop (the owner, 2026-11-11: "Make the freeze and stop buttons into play/pause and stop buttons", and Play
after a Stop restarts the last clip): POST /api/control {"action": "replay"}, and the play-and-pause toggle with nothing
loaded. The last play is kept in memory only."""
import json
import os

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from tests.test_server import ServerBase


class Replay(ServerBase):
    def setUp(self):
        super().setUp()
        self.owner, _ = self.pair("owner")

    def control(self, body, token=None):
        return self.call("POST", "/api/control", body, token=token or self.owner)

    def played(self):
        return [p["paths"] for p in self.player.plays]

    def test_nothing_to_play_again(self):
        st, body = self.control({"action": "replay"})[:2]
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "nothing to play again: start a pad or a clip first")
        self.assertEqual(self.played(), [])

    def test_replay_after_stop_plays_the_same_clip_again(self):
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.owner)[0], 200)
        self.assertEqual(self.control({"action": "stop"})[0], 200)
        st, _ = self.control({"action": "replay"})[:2]
        self.assertEqual(st, 200)
        self.assertEqual(len(self.played()), 2)
        self.assertEqual(self.played()[0], self.played()[1], "the same clip, from its start")

    def test_the_last_play_is_the_one_replayed(self):
        for n in ("a.mp4", "b.mov"):        # (the two clips ServerBase puts in the media folder)
            self.assertEqual(self.call("POST", "/api/play", {"file": n}, token=self.owner)[0], 200)
        self.control({"action": "stop"})
        self.control({"action": "replay"})
        self.assertEqual(self.played()[-1], self.played()[1], "the second play, not the first")

    def test_the_toggle_with_nothing_loaded_plays_again(self):
        # the fake player reports no path: nothing loaded, as after a Stop
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.owner)
        st, body = self.control({"action": "pause"})[:2]
        self.assertEqual(st, 200)
        self.assertEqual(body, {"paused": False, "replayed": True})
        self.assertEqual(len(self.played()), 2)
        # a pause or a play asked for by value never starts anything
        for v in (True, False):
            st, body = self.control({"action": "pause", "value": v})[:2]
            self.assertEqual(st, 200)
            self.assertNotIn("replayed", body)
        self.assertEqual(len(self.played()), 2)

    def test_the_toggle_with_nothing_loaded_and_nothing_remembered_says_so(self):
        st, body = self.control({"action": "pause"})[:2]
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "nothing to play again: start a pad or a clip first")
        self.assertEqual(self.played(), [])

    def test_a_list_replays_from_its_first_clip(self):
        for n in ("one.jpg", "two.jpg"):
            open(os.path.join(self.media, n), "w").close()
        st, _ = self.call("POST", "/api/play", {"slideshow": {"source": "media", "seconds": 5}}, token=self.owner)[:2]
        self.assertEqual(st, 200)
        first = self.played()[0]
        self.player.calls.append(("playlist_step", True))           # (as if Next was pressed: the list is on its second clip)
        self.control({"action": "stop"})
        self.control({"action": "replay"})
        self.assertEqual(self.played()[-1], first, "the whole list again, from its first clip")

    def test_the_status_says_what_is_loaded(self):
        st = self.call("GET", "/api/status", token=self.owner)[1]["player"]
        self.assertIs(st["loaded"], False)
        self.assertIs(st["replay"], False)
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.owner)
        self.assertIs(self.call("GET", "/api/status", token=self.owner)[1]["player"]["replay"], True)

    def test_only_the_action(self):
        st, _ = self.control({"action": "replay", "value": 1})[:2]
        self.assertEqual(st, 400)

    def test_a_guest_does_not_replay(self):
        # Stop is open to a guest with open controls; replay is not: it could start again what an Owner played (a stream,
        # a capture), which a guest could not start itself, past the gate's check of what a guest may play
        guest = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.owner)[1]["token"]
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.owner)
        self.assertEqual(self.control({"action": "replay"}, token=guest)[0], 403)
        self.assertEqual(len(self.played()), 1)

    def test_never_saved(self):
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=self.owner)
        self.settings.save()
        with open(self.settings.path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("a.mp4", json.dumps(json.loads(text).get("player", {})))
        self.assertNotIn("last_play", text)
        self.assertNotIn("replay", text)
