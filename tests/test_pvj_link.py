# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The real helper pvj-link (render/link/, D117) beside a real stand-in peer, on this machine's own network.

Skipped unless PVJ_LINK_TEST=1 and both programs are built: most machines have no ableton-link-dev, and those must stay
green. CI's job pvj-link builds them on Debian 13 and treats a skip as a failure. Bounded: every wait here has an end.
Nothing here has met Ableton Live, a phone or a Pi."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

import tests  # noqa: F401  (test_lockrank: the package's temp folder and lock checks, also when started alone)
from pvj import link

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(HERE, "render", "pvj-link")
PEER = os.path.join(HERE, "render", "pvj-link-peer")


def has_multicast_network():
    return any(n != "lo" for n in os.listdir("/sys/class/net"))


def wait_for(look, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = look()
        if got:
            return got
        time.sleep(0.02)
    return look()


@unittest.skipUnless(os.environ.get("PVJ_LINK_TEST") == "1", "PVJ_LINK_TEST=1 runs the real helper")
class RealHelperTest(unittest.TestCase):
    def setUp(self):
        if not (os.access(HELPER, os.X_OK) and os.access(PEER, os.X_OK)):
            self.skipTest("make -C render pvj-link pvj-link-peer first")
        if not has_multicast_network():
            self.skipTest("no network but the loopback")
        self.dir = tempfile.mkdtemp(prefix="pvj-link-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.helper = subprocess.Popen([HELPER], env=dict(os.environ, PVJ_LINK_DIR=self.dir), stdout=subprocess.DEVNULL)
        self.addCleanup(self.stop, self.helper)
        self.state = os.path.join(self.dir, "state")
        self.sock = os.path.join(self.dir, "link.sock")
        self.assertTrue(wait_for(lambda: link.read_state(self.state), 3.0), "no record from the helper")

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def ask(self, cmd):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2.0)
        try:
            s.connect(self.sock)
            s.sendall(json.dumps({"cmd": cmd}).encode() + b"\n")
            return json.loads(s.makefile().readline())
        finally:
            s.close()

    def peer(self, *args):
        p = subprocess.Popen([PEER] + [str(a) for a in args], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, p)
        self.addCleanup(p.stdout.close)
        return p

    def test_off_says_nothing_and_a_record_is_a_whole_one(self):
        st = self.ask("status")
        self.assertEqual((st["on"], st["stage"], st["peers"]), (False, "off", 0))
        r = link.read_state(self.state)
        self.assertEqual((r["on"], r["enabled"], r["peers"], r["bpm"], r["phase"]), (False, False, 0, 0.0, 0.0))
        self.assertLess(abs(time.monotonic() - r["time"]), 2.0, "stamped on the box's own clock")
        self.assertEqual(oct(os_mode(self.state)), "0o640")

    def test_off_a_peer_on_the_network_does_not_wake_it(self):
        p = self.peer(133, 2)
        time.sleep(1.5)
        self.assertEqual(self.ask("status")["stage"], "off")
        self.assertFalse(link.read_state(self.state)["enabled"], "the Link object stays disabled")
        p.wait(timeout=5)

    def test_on_and_alone_it_keeps_link_disabled_and_gives_no_tempo(self):
        self.assertTrue(self.ask("on")["on"])
        time.sleep(2.2)
        st = self.ask("status")
        self.assertEqual((st["stage"], st["peers"], st["bpm"]), ("listening", 0, 0.0))
        r = link.read_state(self.state)
        self.assertEqual((r["on"], r["enabled"], r["peers"], r["bpm"]), (True, False, 0, 0.0))

    def test_it_follows_tempo_and_phase_never_sets_them_and_lets_go(self):
        self.ask("on")
        time.sleep(1.2)                         # alone first: this is what would have made it the older session
        p = self.peer(133, 9, 141)
        got = wait_for(lambda: (lambda r: r if r and r["peers"] >= 1 else None)(link.read_state(self.state)), 4.0)
        self.assertTrue(got, "it did not join within 4 s")
        self.assertAlmostEqual(got["bpm"], 133.0, delta=0.05, msg="the peer's tempo, never Link's default")
        self.assertEqual(self.ask("status")["stage"], "following")
        lines = []
        for _ in range(60):                     # the peer's lines, 5 a second, until the tempo has changed
            line = p.stdout.readline()
            if not line:
                break
            lines.append(json.loads(line))
            if len(lines) > 12 and lines[-1]["peers"] >= 1:
                break
        mine = link.read_state(self.state)
        theirs = lines[-1]
        want = (theirs["phase"] + (mine["time"] - theirs["time"]) * theirs["bpm"] / 60.0) % 4.0
        diff = abs((mine["phase"] - want + 2.0) % 4.0 - 2.0)
        self.assertLess(diff, 0.05, "the same place in the bar on the same clock (0.05 beats is 22 ms at 133)")
        self.assertAlmostEqual(lines[-1]["bpm"], 133.0, delta=0.01, msg="the box did not move the session's tempo")
        changed = wait_for(lambda: (lambda r: r if r and abs(r["bpm"] - 141.0) < 0.05 else None)(link.read_state(self.state)), 6.0)
        self.assertTrue(changed, "it did not follow the change of tempo")
        p.stdout.read()
        p.wait(timeout=10)
        self.assertTrue(wait_for(lambda: link.read_state(self.state)["peers"] == 0, 8.0), "the peer left")
        self.assertTrue(wait_for(lambda: self.ask("status")["stage"] == "listening", 4.0), "disabled again when nobody is left")
        self.assertTrue(wait_for(lambda: not link.read_state(self.state)["enabled"], 2.0), "the record says so within its second")
        self.assertEqual(link.read_state(self.state)["bpm"], 0.0)

    def test_after_a_long_time_alone_a_new_peer_keeps_its_own_tempo(self):
        # Without the wait for someone, a helper alone for this long would have the older session and the peer would
        # take its default of 120.
        self.ask("on")
        time.sleep(3.0)
        p = self.peer(97, 5)
        lines = [json.loads(line) for line in p.stdout]
        p.wait(timeout=5)
        self.assertTrue(any(x["peers"] >= 1 for x in lines), "they never met")
        self.assertTrue(all(abs(x["bpm"] - 97.0) < 0.01 for x in lines), sorted({x["bpm"] for x in lines}))

    def test_a_stranger_and_an_unknown_command_are_refused_and_it_answers_after(self):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2.0)
        s.connect(self.sock)
        s.sendall(b'{"cmd": "setTempo", "bpm": 90}\n')
        self.assertFalse(json.loads(s.makefile().readline())["ok"])
        s.close()
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2.0)
        s.connect(self.sock)
        s.sendall(b"x" * 4096 + b"\n")
        s.close()
        self.assertTrue(self.ask("status")["ok"])

    def test_a_term_signal_ends_it_cleanly(self):
        self.ask("on")
        self.helper.terminate()
        self.assertEqual(self.helper.wait(timeout=5), 0)


def os_mode(path):
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
