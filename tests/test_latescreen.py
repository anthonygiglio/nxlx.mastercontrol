# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A screen that comes after the player has started (pvj/latescreen.py): the box that fell back to classic mpv for want
of a screen goes back to pvj-render once when one is connected, never in a loop, and a crash fallback stays on classic.

A fake /sys tree and a fake clock; nothing here forks, kills or touches the real /sys."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import hardware, latescreen, renderer, screenmode
from pvj.player import Player
from tests.test_renderer import Base as RendererBase


class Fake:
    """A /sys/class/drm tree, a clock that only moves when the watcher sleeps, and a player that can be ended."""

    def __init__(self, test):
        self.root = tempfile.mkdtemp(prefix="pvj-latescreen-")
        test.addCleanup(shutil.rmtree, self.root, True)
        self.rundir = os.path.join(self.root, "run")
        os.makedirs(self.rundir)
        self.now = 1000.0
        self.ended = []
        self.lines = []

    def screen(self, connector, connected, card=1):
        folder = os.path.join(self.root, "sys/class/drm/card%d-%s" % (card, connector))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "status"), "w") as f:
            f.write("connected\n" if connected else "disconnected\n")

    def sysfs(self):
        return hardware.drm_connectors(self.root)

    def sleep(self, seconds):
        self.now += seconds

    def watcher(self, **kw):
        kw.setdefault("boot", "boot-1")
        return latescreen.Watcher(self.rundir, self.lines.append, sysfs=self.sysfs, clock=lambda: self.now, sleep=self.sleep,
                                  end_player=lambda: self.ended.append(self.now), **kw)

    def run_until(self, watcher, seconds, change=None):
        """step() once a second for `seconds`, with change(t) called first at each second; True when it said switch."""
        for t in range(seconds):
            if change:
                change(t)
            if watcher.step():
                return True
            self.sleep(1.0)
        return False


class WatcherTest(unittest.TestCase):
    def test_a_screen_that_comes_late_ends_the_player_once_within_a_few_seconds(self):
        f = Fake(self)
        f.screen("HDMI-A-1", False)
        w = f.watcher()
        started = f.now
        self.assertFalse(f.run_until(w, 20))
        self.assertEqual(f.ended, [])
        f.screen("HDMI-A-1", True)
        plugged = f.now
        self.assertTrue(f.run_until(w, 20))
        self.assertEqual(len(f.ended), 1)
        self.assertLessEqual(f.ended[0] - plugged, latescreen.SETTLE_SECONDS + 1)
        self.assertGreater(plugged, started)
        # once: a watcher that has switched says nothing more, even with the screen still there
        self.assertFalse(f.run_until(w, 60))
        self.assertEqual(len(f.ended), 1)
        self.assertTrue(any("restarting the player once" in line and "HDMI-A-1" in line for line in f.lines), f.lines)

    def test_no_connector_name_or_card_number_is_assumed(self):
        # a Pi 5's HDMI-A-2 on card0, a desktop's DP-3 on card2: whatever the kernel lists counts
        for connector, card in (("HDMI-A-2", 0), ("DP-3", 2), ("HDMI-A-1", 1)):
            f = Fake(self)
            f.screen(connector, False, card)
            f.screen("Writeback-1", True, card)         # never a screen
            w = f.watcher()
            self.assertFalse(f.run_until(w, 10), connector)
            f.screen(connector, True, card)
            self.assertTrue(f.run_until(w, 10), connector)

    def test_a_screen_that_goes_before_it_has_settled_does_not_switch(self):
        f = Fake(self)
        f.screen("HDMI-A-1", False)
        w = f.watcher()
        # connected for two seconds in every five: never the three in a row that a screen needs
        self.assertFalse(f.run_until(w, 60, lambda t: f.screen("HDMI-A-1", t % 5 < 2)))
        self.assertEqual(f.ended, [])

    def test_a_flapping_screen_is_bounded_by_the_gap_and_the_total(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        switches = []
        # each pass is one start of the player after a switch: a new watcher over the same folder, as systemd restarts it
        for _ in range(12):
            f.screen("HDMI-A-1", False)
            w = f.watcher()
            f.sleep(2.0)
            f.screen("HDMI-A-1", True)
            if f.run_until(w, 100):
                switches.append(f.now)
            f.sleep(1.0)
        self.assertEqual(len(switches), latescreen.MAX_SWITCHES)
        self.assertEqual(len(f.ended), latescreen.MAX_SWITCHES)
        for a, b in zip(f.ended, f.ended[1:]):
            self.assertGreaterEqual(b - a, latescreen.GAP_SECONDS)
        said = [line for line in f.lines if "already switched" in line]
        self.assertGreaterEqual(len(said), 1)
        self.assertIn("classic mpv stays", said[0])

    def test_a_watcher_waits_out_the_gap_after_a_switch(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        self.assertTrue(f.run_until(f.watcher(), 20))
        again = f.watcher()
        f.sleep(2.0)
        self.assertTrue(f.run_until(again, 100))
        self.assertGreaterEqual(f.ended[1] - f.ended[0], latescreen.GAP_SECONDS)

    def test_the_count_belongs_to_one_boot(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        for n in range(latescreen.MAX_SWITCHES):
            self.assertTrue(f.run_until(f.watcher(boot="boot-1"), 100), n)
            f.sleep(1.0)
        self.assertFalse(f.run_until(f.watcher(boot="boot-1"), 100))
        self.assertTrue(f.run_until(f.watcher(boot="boot-2"), 100))     # a new boot starts again

    def test_a_damaged_count_is_nothing(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        with open(os.path.join(f.rundir, latescreen.STATE), "w") as g:
            g.write('{"boot": "boot-1", "switches": "many"}')
        self.assertTrue(f.run_until(f.watcher(), 20))


class FallbackKindTest(unittest.TestCase):
    NONE = [{"connector": "HDMI-A-1", "status": "disconnected", "modes": [], "card": 1},
            {"connector": "Writeback-1", "status": "connected", "modes": [], "card": 1}]
    ONE = [{"connector": "HDMI-A-1", "status": "connected", "modes": ["1920x1080"], "card": 1}]

    def test_pvj_render_that_did_not_start_with_no_screen_is_the_one_a_screen_undoes(self):
        self.assertTrue(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", self.NONE))

    def test_a_crash_stays_on_classic(self):
        for why in ("pvj-render ended twice in a row (exit 3)", "pvj-render ended twice in a row (signal 11)",
                    "pvj-render did not answer within 10 seconds", "pvj-render is not built on this box",
                    "pvj-render could not be started (Permission denied)", ""):
            self.assertFalse(latescreen.no_screen_fallback(why, self.NONE), why)

    def test_a_screen_that_is_there_changes_nothing(self):
        self.assertFalse(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", self.ONE))

    def test_a_board_that_lists_no_connector_cannot_say(self):
        self.assertFalse(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", []))


class ServeTest(RendererBase):
    """Player.serve: when the watcher is started, and that classic mpv is exec'd in every case."""

    def serve(self, sysfs, wish=None):
        os.environ["STANDIN_MODE"], os.environ["STANDIN_LOG"] = "exit:2", self.calls
        self.addCleanup(os.environ.pop, "STANDIN_MODE", None)
        self.addCleanup(os.environ.pop, "STANDIN_LOG", None)
        os.environ["PVJ_RENDER_BIN"] = self.standin
        self.addCleanup(os.environ.pop, "PVJ_RENDER_BIN", None)
        state = os.path.join(self.dir, "state")
        os.makedirs(state, exist_ok=True)
        screenmode.write_json(os.path.join(state, renderer.WANTED), wish or {"use": "new"})
        p = Player(extra_args=["--vo=gpu", "--gpu-context=drm"], rundir=self.dir)

        class Exec(Exception):
            pass
        seen = {"watch": []}

        def execvp(path, args):
            seen["args"] = args
            raise Exec()
        with mock.patch.object(screenmode, "prepare", lambda *a, **k: []), \
                mock.patch.object(renderer.paths, "state_dir", lambda e=None: state), \
                mock.patch("pvj.player.detach_socket_opener", lambda path: None), \
                mock.patch("os.execvp", execvp), \
                mock.patch.object(latescreen, "start", lambda *a, **k: seen["watch"].append(a)), \
                mock.patch.object(hardware, "drm_connectors", lambda root="/": sysfs), \
                mock.patch.object(renderer, "START_SECONDS", 3.0), \
                mock.patch.object(renderer.hardware, "detect_board", lambda: {"kind": "pi4"}):
            try:
                p.serve()
            except Exec:
                pass
        return seen

    def test_no_screen_at_boot_starts_the_watcher_and_classic_mpv_draws(self):
        seen = self.serve(FallbackKindTest.NONE)
        self.assertEqual(seen["args"][0], "mpv")
        self.assertEqual(len(seen["watch"]), 1)
        self.assertEqual(self.report()["drawing"], "classic")

    def test_a_screen_present_at_start_changes_nothing(self):
        seen = self.serve(FallbackKindTest.ONE)          # pvj-render fails for another reason: classic, and no watching
        self.assertEqual(seen["args"][0], "mpv")
        self.assertEqual(seen["watch"], [])

    def test_classic_chosen_is_never_watched(self):
        seen = self.serve(FallbackKindTest.NONE, {"use": "classic"})
        self.assertEqual(seen["args"][0], "mpv")
        self.assertEqual(seen["watch"], [])


class StartTest(unittest.TestCase):
    def test_a_player_not_run_by_systemd_is_never_watched(self):
        self.assertFalse(latescreen.start(tempfile.gettempdir(), lambda line: None, env={}))

    def test_the_unit_keeps_its_folder_across_its_own_restart(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "install", "pvj-player.service")) as f:
            self.assertIn("RuntimeDirectoryPreserve=restart", f.read().split("\n"))


if __name__ == "__main__":
    unittest.main()
