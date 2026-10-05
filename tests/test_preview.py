# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import time
import unittest

from pvj import api as api_mod
from pvj.player import PlayerError
from tests.test_server import ServerBase

JPEG = b"\xff\xd8\xff\xe0" + b"fake jpeg body" * 20 + b"\xff\xd9"


class PreviewTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.shots = []
        self.modes = []

        def screenshot(path, quality=60, with_text=True):
            self.shots.append(path)
            self.modes.append(with_text)
            with open(path, "wb") as f:
                f.write(self.next_bytes)
        self.next_bytes = JPEG
        self.player.screenshot = screenshot
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def invite(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "g", "role": role}, token=self.full)[1]["token"]

    def test_a_view_only_device_gets_the_picture_as_a_jpeg(self):
        st, body, resp = self.call("GET", "/api/preview.jpg", token=self.invite("view"))
        self.assertEqual((st, body), (200, JPEG))
        self.assertEqual(resp.getheader("Content-Type"), "image/jpeg")
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(os.path.basename(self.shots[0]), "preview.jpg")
        self.assertEqual(os.path.dirname(self.shots[0]), self.rundir)

    def test_unpaired_devices_get_nothing(self):
        st, body, _ = self.call("GET", "/api/preview.jpg")
        self.assertEqual(st, 401)
        self.assertEqual(self.shots, [])

    def test_the_minimum_interval_is_slow_enough_to_protect_playback(self):
        # measured on a Pi 4: a snapshot stalls playback for about a quarter of a second
        self.assertGreaterEqual(api_mod.PREVIEW_MIN_INTERVAL, 2.0)

    def test_viewers_within_the_interval_share_one_frame(self):
        token = self.invite("view")
        for _ in range(5):
            self.assertEqual(self.call("GET", "/api/preview.jpg", token=token)[0], 200)
        self.assertEqual(len(self.shots), 1)
        self.api._preview = (time.monotonic() - api_mod.PREVIEW_MIN_INTERVAL - 0.1, JPEG, True)   # the frame is now old
        self.call("GET", "/api/preview.jpg", token=token)
        self.assertEqual(len(self.shots), 2)

    def test_idle_or_missing_player_is_a_503_not_a_crash(self):
        def broken(path, quality=60, with_text=True):
            raise PlayerError("player service is not running")
        self.player.screenshot = broken
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertEqual(st, 503)
        self.assertIn("not running", body["error"])

    def test_a_player_that_plays_nothing_is_a_409_in_plain_words(self):
        # seen on the Pi 4: with nothing playing, mpv refuses the screenshot with "error running command"
        def nothing(path, quality=60, with_text=True):
            raise PlayerError("mpv: error running command")
        self.player.screenshot = nothing
        self.player.running = True                      # the player is there, and status() says no clip
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertEqual((st, body["error"]), (409, "nothing is on the screen right now"))
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.invite("view"))      # remembered, like any failure
        self.assertEqual(st, 409)

    def test_a_failed_snapshot_while_something_plays_stays_a_503(self):
        def broken(path, quality=60, with_text=True):
            raise PlayerError("mpv: error running command")
        self.player.screenshot = broken
        self.player.running = True
        self.player.status = lambda: {"running": True, "path": "/media/intro.mp4"}
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertEqual(st, 503)
        self.assertIn("error running command", body["error"])

    def test_a_file_that_is_not_a_jpeg_is_refused(self):
        self.next_bytes = b"<html>not a picture</html>"
        self.assertEqual(self.call("GET", "/api/preview.jpg", token=self.full)[0], 503)
        self.next_bytes = b"\xff\xd8" + b"x" * (api_mod.PREVIEW_MAX_BYTES + 10)
        self.assertEqual(self.call("GET", "/api/preview.jpg", token=self.full)[0], 503)

    def test_a_link_planted_before_the_snapshot_is_removed_and_never_written_through(self):
        target = os.path.join(self.tmp, "victim.txt")
        with open(target, "w") as f:
            f.write("do not overwrite")
        os.symlink(target, os.path.join(self.rundir, "preview.jpg"))       # planted by someone in group pvj
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertEqual((st, body), (200, JPEG))
        with open(target) as f:
            self.assertEqual(f.read(), "do not overwrite")
        self.assertFalse(os.path.islink(os.path.join(self.rundir, "preview.jpg")))

    def test_a_link_created_during_the_snapshot_is_not_followed(self):
        target = os.path.join(self.tmp, "secret.jpg")
        with open(target, "wb") as f:
            f.write(JPEG)
        link = os.path.join(self.rundir, "preview.jpg")

        def screenshot(path, quality=60, with_text=True):
            os.symlink(target, link)
        self.player.screenshot = screenshot
        st, body, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertNotEqual(st, 200)
        self.assertNotEqual(body, JPEG)

    def test_a_fifo_at_the_path_cannot_hang_the_reader(self):
        link = os.path.join(self.rundir, "preview.jpg")

        def screenshot(path, quality=60, with_text=True):
            os.mkfifo(link)
        self.player.screenshot = screenshot
        started = time.monotonic()
        st, _, _ = self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertNotEqual(st, 200)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_failure_is_remembered_so_requests_do_not_queue_behind_a_slow_player(self):
        calls = []

        def slow_fail(path, quality=60, with_text=True):
            calls.append(1)
            raise PlayerError("nothing playing")
        self.player.screenshot = slow_fail
        for _ in range(5):
            self.assertEqual(self.call("GET", "/api/preview.jpg", token=self.full)[0], 503)
        self.assertEqual(len(calls), 1)

    def test_query_string_is_ignored_and_other_methods_do_not_reach_it(self):
        token = self.invite("view")
        self.assertEqual(self.call("GET", "/api/preview.jpg?t=123", token=token)[0], 200)
        self.assertEqual(self.call("POST", "/api/preview.jpg", {}, token=token)[0], 404)


class AccessOnScreenSnapshotTest(PreviewTest):
    """While the PIN or join codes are on the display, a snapshot must not show them to a device without full access."""

    def show(self, showing):
        class Ps:
            def status(inner):
                return {"showing": showing, "items": ["pin"], "seconds_left": 30}

            def auto_wanted(inner):
                return False
        self.api.pinscreen = Ps()

    def test_a_guest_gets_the_video_only_while_codes_are_shown_the_owner_gets_everything(self):
        view = self.invite("view")
        live = self.invite("live")
        self.show(True)
        self.call("GET", "/api/preview.jpg", token=view)
        self.assertEqual(self.modes[-1], False)                              # video only: no text, no QR codes
        self.call("GET", "/api/preview.jpg", token=live)
        self.assertEqual(self.modes[-1], False)
        self.call("GET", "/api/preview.jpg", token=self.full)
        self.assertEqual(self.modes[-1], True)                               # the owner sees what is on the screen
        self.show(False)
        self.api._preview = None
        self.call("GET", "/api/preview.jpg", token=view)
        self.assertEqual(self.modes[-1], True)                               # nothing sensitive shown: the whole window

    def test_a_cached_full_frame_is_never_handed_to_a_guest(self):
        self.show(True)
        self.call("GET", "/api/preview.jpg", token=self.full)               # a full frame (with the PIN) is now cached
        n = len(self.modes)
        self.call("GET", "/api/preview.jpg", token=self.invite("view"))
        self.assertEqual(self.modes[n:], [False])                            # a fresh video-only grab, not the cached frame

    def test_the_first_run_screen_counts_too_and_an_error_counts_as_shown(self):
        class Ps:
            def status(inner):
                return {"showing": False}

            def auto_wanted(inner):
                return True
        self.api.pinscreen = Ps()
        self.assertTrue(self.api.access_on_screen())

        class Broken:
            def status(inner):
                raise RuntimeError("x")
        self.api.pinscreen = Broken()
        self.assertTrue(self.api.access_on_screen())


if __name__ == "__main__":
    unittest.main()
