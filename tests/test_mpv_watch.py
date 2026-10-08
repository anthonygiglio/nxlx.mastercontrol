# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The watch on the player's control socket (tests/mpv_watch.py), against a stand-in for mpv that answers late, never,
or between events. No mpv and no GPU: this runs everywhere. What is checked: a request that got no reply still fails
exactly as it did, the report says when the answer did come, and the product's client is as it was afterwards."""
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from pvj import player as player_module
from pvj.player import Ipc, PlayerError
from tests import mpv_watch as M


class FakeMpv:
    """A unix socket that speaks mpv's line protocol. `script(command)` says what a connection does with a request:
    a list of (seconds to wait, a message to send or None to close). Without a script a request is answered at once."""

    def __init__(self, path):
        self.script = lambda command: None
        self.seen = []
        self._s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._s.bind(path)
        self._s.listen(8)
        self._open = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self._open:
            try:
                c, _ = self._s.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            buf = b""
            while b"\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            msg = json.loads(buf.split(b"\n", 1)[0])
            self.seen.append(msg["command"])
            steps = self.script(msg["command"])
            if steps is None:
                steps = [(0, {"request_id": msg["request_id"], "error": "success", "data": 7})]
            for wait, out in steps:
                time.sleep(wait)
                if out is None:
                    return
                if out == "answer":
                    out = {"request_id": msg["request_id"], "error": "success", "data": 7}
                c.sendall(json.dumps(out).encode() + b"\n")
            time.sleep(0.5)
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self._open = False
        self._s.close()


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pvjw", dir="/tmp")         # short: a socket's path has little room
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "s")
        self.mpv = FakeMpv(self.path)
        self.addCleanup(self.mpv.close)
        M.install()
        self.addCleanup(M.uninstall)
        self.addCleanup(M.STATS.clear)
        self.addCleanup(M.SLOWEST.clear)
        self.addCleanup(M.REPORTS.clear)
        self.out = io.StringIO()
        self.ipc = M.WatchedIpc(self.path, timeout=0.3, where=self.id, out=self.out, late_wait=3.0, probe_wait=2.0, retry_wait=3.0)

    def slow_screenshot(self, seconds, first_only=True):
        calls = []

        def script(command):
            if command[0] != "screenshot-to-file":
                return None
            calls.append(command)
            if first_only and len(calls) > 1:
                return None
            return [(0, {"event": "playback-restart"}), (0, {"event": "property-change", "name": "x"}), (seconds, "answer")]
        self.mpv.script = script
        return calls

    def test_an_answer_that_comes_late_still_fails_the_request_and_the_report_says_when_it_came(self):
        calls = self.slow_screenshot(0.8)
        self.ipc.request("set_property", "glsl-shaders", ["a"])
        t = time.monotonic()
        with self.assertRaises(PlayerError) as e:
            self.ipc.request("screenshot-to-file", os.path.join(self.dir, "shot.png"), "window")
        self.assertEqual(str(e.exception), "no reply from mpv")                 # as the product says it, nothing added
        said = self.out.getvalue()
        self.assertIn("THE ANSWER CAME, 0.", said)
        came = float(said.split("THE ANSWER CAME, ")[1].split(" s")[0])
        self.assertTrue(0.7 <= came <= 1.6, came)
        self.assertIn("event playback-restart x1", said)
        self.assertIn("event property-change:x x1", said)
        self.assertIn("number 2; waited 0.3", said)
        self.assertIn("set_property glsl-shaders", said)                        # what was asked before it
        self.assertIn("pid ", said)                                             # the cheap questions were answered
        self.assertIn("the same request once more", said)
        self.assertEqual([os.path.basename(c[1]) for c in calls], ["shot.png", "shot.png.again.png", "shot.png.again.png"])
        self.assertIn(self.id(), said)
        self.assertLess(time.monotonic() - t, 6.0)
        row = M.STATS["screenshot-to-file (the first after a change of shaders)"]
        self.assertEqual(row[:2], [1, 1])

    def test_no_answer_at_all_is_said_so_and_a_request_that_changes_something_is_not_asked_again(self):
        self.mpv.script = lambda command: [(5, None)] if command[0] == "loadfile" else None
        self.ipc.late_wait = 0.6
        with self.assertRaises(PlayerError) as e:
            self.ipc.request("loadfile", "clip.mp4", "replace")
        self.assertEqual(str(e.exception), "no reply from mpv")
        said = self.out.getvalue()
        self.assertIn("NO ANSWER on the original connection", said)
        self.assertIn("nothing at all", said)
        self.assertIn("not asked again", said)
        self.assertEqual([c[0] for c in self.mpv.seen].count("loadfile"), 1)

    def test_events_and_another_requests_answer_before_the_right_one_are_passed_over(self):
        self.mpv.script = lambda command: [(0, {"event": "seek"}), (0, {"request_id": 9999, "error": "success", "data": "old"}), (0.05, "answer")]
        self.assertEqual(self.ipc.request("get_property", "pid"), 7)
        self.assertEqual(self.out.getvalue(), "")
        self.assertEqual(M.STATS["get_property pid"][:2], [1, 0])

    def test_an_error_from_the_player_is_not_a_missing_reply(self):
        self.mpv.script = lambda command: [(0, {"request_id": 1, "error": "property unavailable"})]
        with self.assertRaises(PlayerError) as e:
            self.ipc.request("get_property", "time-pos")
        self.assertEqual(str(e.exception), "mpv: property unavailable")
        self.assertEqual(self.out.getvalue(), "")

    def test_the_products_client_is_as_it_was_once_the_watch_is_off(self):
        self.assertIsNot(player_module.socket, socket)
        M.uninstall()
        self.addCleanup(M.install)                      # keep setUp's count even
        self.assertIs(player_module.socket, socket)
        self.assertEqual(Ipc(self.path, timeout=0.3).request("get_property", "pid"), 7)

    def test_a_plain_client_beside_a_watched_one_is_not_held_open(self):
        plain = Ipc(self.path, timeout=0.3)
        self.assertEqual(plain.request("get_property", "pid"), 7)
        self.assertEqual(M.STATS, {})

    def test_the_summary_names_the_slowest(self):
        self.slow_screenshot(0.15, first_only=False)
        self.ipc.request("screenshot-to-file", "a.png", "window")
        text = "\n".join(M.summary())
        self.assertIn("screenshot-to-file", text)
        self.assertIn("the slowest: 0.1", text)


if __name__ == "__main__":
    unittest.main()
