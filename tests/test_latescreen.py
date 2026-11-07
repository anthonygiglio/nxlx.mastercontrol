# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A screen that comes after the player has started (pvj/latescreen.py): the box that fell back to classic mpv for want
of a screen goes back to pvj-render once when one is connected, never in a loop, and a crash fallback stays on classic.

A fake /sys tree and a fake clock; nothing here forks, kills or touches the real /sys."""
import os
import shutil
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

    def screen(self, connector, connected, card=1, modes=True):
        folder = os.path.join(self.root, "sys/class/drm/card%d-%s" % (card, connector))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "status"), "w") as f:
            f.write("connected\n" if connected else "disconnected\n")
        with open(os.path.join(folder, "modes"), "w") as f:          # the kernel lists none until it has read the EDID
            f.write("1920x1080\n1024x768\n" if modes else "")

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
        self.assertFalse(f.run_until(w, 40))
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
            self.assertFalse(f.run_until(w, 40), connector)
            f.screen(connector, True, card)
            self.assertTrue(f.run_until(w, 10), connector)

    def test_a_screen_that_says_connected_before_it_has_modes_does_not_switch_until_it_has_some(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True, modes=False)
        w = f.watcher()
        self.assertFalse(f.run_until(w, 60))
        self.assertEqual(f.ended, [])
        f.screen("HDMI-A-1", True)
        plugged = f.now
        self.assertTrue(f.run_until(w, 30))
        self.assertLessEqual(f.ended[0] - plugged, latescreen.SETTLE_SECONDS + 2)

    def test_a_screen_that_goes_before_it_has_settled_does_not_switch(self):
        f = Fake(self)
        f.screen("HDMI-A-1", False)
        w = f.watcher()
        # connected for two seconds in every five: never the three in a row that a screen needs
        self.assertFalse(f.run_until(w, 60, lambda t: f.screen("HDMI-A-1", t % 5 < 2)))
        self.assertEqual(f.ended, [])

    def restarts(self, f, seconds, watch_for=600):
        """A flapping cable for `seconds`: after every switch the player starts again (a new watcher over the same folder),
        the screen is gone for a moment and comes back two seconds later. Returns when each switch happened."""
        begin = f.now
        while f.now - begin < seconds:
            f.screen("HDMI-A-1", False)
            w = f.watcher()
            f.sleep(2.0)
            f.screen("HDMI-A-1", True)
            f.run_until(w, watch_for)
        return f.ended

    def test_a_flapping_cable_for_an_hour_waits_30_60_120_then_300_and_never_stops(self):
        f = Fake(self)
        ended = self.restarts(f, 3600)
        waits = [b - a for a, b in zip(ended, ended[1:])]
        want = [30, 60, 120] + [300] * (len(waits) - 3)
        self.assertGreaterEqual(len(ended), 10)
        for got, wait in zip(waits, want):
            self.assertGreaterEqual(got, wait)          # never more than one switch per wait
            self.assertLessEqual(got, wait + 6)         # and no longer than the wait, the poll and the settle
        self.assertEqual(waits[:4], sorted(waits[:4]))
        self.assertTrue(all("the next switch waits at least" in line for line in f.lines if "restarting" in line))
        self.assertFalse([line for line in f.lines if "stays" in line])     # there is no limit that ends it

    def test_the_wait_starts_again_once_pvj_render_has_drawn_for_ten_minutes(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        self.restarts(f, 1, 100)
        self.restarts(f, 1, 100)
        self.restarts(f, 1, 100)                        # three switches in a run: the next would wait 120 s
        f.sleep(latescreen.RESET_SECONDS + 60)           # pvj-render drew all that time
        f.screen("HDMI-A-1", False)
        w = f.watcher()
        f.sleep(2.0)
        f.screen("HDMI-A-1", True)
        before = f.now
        self.assertTrue(f.run_until(w, 100))
        self.assertLessEqual(f.ended[-1] - before, latescreen.SETTLE_SECONDS + 2)

    def test_the_first_switch_of_a_boot_comes_within_a_few_seconds_of_the_screen_settling(self):
        f = Fake(self)
        f.screen("HDMI-A-1", False)
        w = f.watcher()
        self.assertFalse(f.run_until(w, 5))              # a watcher that has just begun
        f.screen("HDMI-A-1", True)
        plugged = f.now
        self.assertTrue(f.run_until(w, 30))
        self.assertLessEqual(f.ended[0] - plugged, latescreen.SETTLE_SECONDS + 2)
        self.assertFalse([line for line in f.lines if "cannot be kept" in line])

    def test_a_count_that_cannot_be_written_gives_the_floor_and_says_so_once(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        gone = os.path.join(f.root, "no-such-folder", "state")      # nothing can be written there
        w = latescreen.Watcher(gone, f.lines.append, sysfs=f.sysfs, clock=lambda: f.now, sleep=f.sleep, boot="boot-1",
                               end_player=lambda: f.ended.append(f.now))
        began = f.now
        self.assertTrue(f.run_until(w, 100))
        self.assertGreaterEqual(f.ended[0] - began, latescreen.GAP_SECONDS)
        self.assertEqual(len([line for line in f.lines if "cannot be kept" in line]), 1)

    def test_a_readable_count_from_this_boot_does_not_lift_the_floor_when_nothing_can_be_written(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        screenmode.write_json(os.path.join(f.rundir, latescreen.STATE), {"boot": "boot-1", "switches": [f.now - 100]})
        with mock.patch.object(screenmode, "write_json", side_effect=OSError(30, "Read-only file system")):
            w = f.watcher()
            began = f.now
            self.assertTrue(f.run_until(w, 100))
        self.assertGreaterEqual(f.ended[0] - began, latescreen.GAP_SECONDS)
        self.assertEqual(len([line for line in f.lines if "cannot be kept" in line]), 1)
        self.assertTrue(any("sooner than 30 s" in line for line in f.lines), f.lines)

    def test_the_count_belongs_to_one_boot(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        self.assertTrue(f.run_until(f.watcher(boot="boot-1"), 100))
        f.sleep(1.0)
        first = f.watcher(boot="boot-1")
        self.assertTrue(f.run_until(first, 100))                        # waited its 30 s
        f.sleep(1.0)
        second = f.watcher(boot="boot-2")                               # a new boot: no run of switches, no wait
        began = f.now
        self.assertTrue(f.run_until(second, 100))
        self.assertLessEqual(f.ended[-1] - began, latescreen.SETTLE_SECONDS + 2)

    def test_a_damaged_count_is_nothing(self):
        f = Fake(self)
        f.screen("HDMI-A-1", True)
        with open(os.path.join(f.rundir, latescreen.STATE), "w") as g:
            g.write('{"boot": "boot-1", "switches": "many"}')
        self.assertTrue(f.run_until(f.watcher(), 60))


class FallbackKindTest(unittest.TestCase):
    NONE = [{"connector": "HDMI-A-1", "status": "disconnected", "modes": [], "card": 1},
            {"connector": "Writeback-1", "status": "connected", "modes": [], "card": 1}]
    NO_MODES = [{"connector": "HDMI-A-1", "status": "connected", "modes": [], "card": 1}]
    ONE = [{"connector": "HDMI-A-1", "status": "connected", "modes": ["1920x1080"], "card": 1}]

    def test_pvj_render_that_did_not_start_with_no_screen_is_the_one_a_screen_undoes(self):
        self.assertTrue(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", self.NONE))

    def test_a_screen_that_is_connected_but_lists_no_modes_is_not_one(self):
        self.assertTrue(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", self.NO_MODES))

    def test_a_screen_that_went_away_while_pvj_render_drew_is_a_no_screen_fallback(self):
        # out_present fails, the supervisor starts it once more, it exits 2: "ended twice in a row (exit 2)"
        self.assertTrue(latescreen.no_screen_fallback("pvj-render ended twice in a row (exit 2)", self.NONE))
        self.assertFalse(latescreen.no_screen_fallback("pvj-render ended twice in a row (exit 2)", self.ONE))

    def test_only_exit_2_counts(self):
        for why in ("pvj-render did not start (exit 1)", "pvj-render did not start (exit -11)", "pvj-render did not start (exit 22)",
                    "pvj-render ended twice in a row (exit 1)", "pvj-render ended twice in a row (exit 3)",
                    "pvj-render ended twice in a row (signal 11)", "pvj-render ended twice in a row (exit 20)"):
            self.assertFalse(latescreen.no_screen_fallback(why, self.NONE), why)

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


class KillTest(unittest.TestCase):
    """The code that ends a process: the start-time guard, the double fork and what the watcher is handed."""

    def test_a_process_with_the_same_start_time_is_ended(self):
        with mock.patch.object(latescreen, "start_time", lambda pid: "777"), mock.patch("os.kill") as kill:
            self.assertTrue(latescreen.end_process(4242, "777"))
        kill.assert_called_once_with(4242, latescreen.signal.SIGTERM)

    def test_a_pid_with_another_start_time_or_none_is_never_signalled(self):
        for now in ("778", None):
            with mock.patch.object(latescreen, "start_time", lambda pid, now=now: now), mock.patch("os.kill") as kill:
                self.assertFalse(latescreen.end_process(4242, "777"))
            kill.assert_not_called()

    def test_the_watcher_stops_when_the_pid_is_gone_or_reused(self):
        for now, alive in (("777", True), (None, False), ("778", False)):
            with mock.patch.object(latescreen, "start_time", lambda pid, now=now: now):
                self.assertEqual(latescreen.still_running(4242, "777"), alive, now)

    def test_start_time_of_this_process_is_there_and_of_a_missing_one_is_none(self):
        self.assertTrue(latescreen.start_time(os.getpid()))
        self.assertIsNone(latescreen.start_time(2 ** 22 + 12345))

    def test_start_forks_twice_in_the_child_and_hands_the_watcher_the_guarded_kill(self):
        class Out(Exception):
            pass

        def leave(code):
            raise Out()
        made = {}

        class FakeWatcher:
            def __init__(self, rundir, log, **kw):
                made.update(kw)

            def watch(self):
                made["watched"] = True
        lines = []
        with mock.patch.object(latescreen, "start_time", lambda pid: "777"), mock.patch("os.fork", side_effect=[0, 0]), \
                mock.patch("os.setsid") as setsid, mock.patch("os._exit", leave), mock.patch.object(latescreen, "Watcher", FakeWatcher):
            with self.assertRaises(Out):
                latescreen.start("/nowhere", lines.append, env={"INVOCATION_ID": "x"})
        setsid.assert_called_once_with()
        self.assertTrue(made["watched"])
        self.assertTrue(any("watching for one" in line for line in lines), lines)
        with mock.patch.object(latescreen, "start_time", lambda pid: "777"), mock.patch("os.kill") as kill:
            self.assertTrue(made["alive"]())
            made["end_player"]()
        kill.assert_called_once_with(os.getpid(), latescreen.signal.SIGTERM)
        with mock.patch.object(latescreen, "start_time", lambda pid: "778"), mock.patch("os.kill") as kill:
            self.assertFalse(made["alive"]())
            made["end_player"]()
        kill.assert_not_called()

    def test_the_parent_waits_for_the_first_child_and_a_failed_fork_starts_nothing(self):
        with mock.patch.object(latescreen, "start_time", lambda pid: "777"), mock.patch("os.fork", return_value=555), \
                mock.patch("os.waitpid") as wait:
            self.assertTrue(latescreen.start("/nowhere", lambda line: None, env={"INVOCATION_ID": "x"}))
        wait.assert_called_once_with(555, 0)
        with mock.patch.object(latescreen, "start_time", lambda pid: "777"), mock.patch("os.fork", side_effect=OSError("no")):
            self.assertFalse(latescreen.start("/nowhere", lambda line: None, env={"INVOCATION_ID": "x"}))


class StartTest(unittest.TestCase):
    def test_a_player_not_run_by_systemd_is_never_watched(self):
        self.assertFalse(latescreen.start(tempfile.gettempdir(), lambda line: None, env={}))

    def test_the_unit_has_a_state_folder_of_its_own_for_the_count(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "install", "pvj-player.service")) as f:
            lines = f.read().split("\n")
        self.assertNotIn("RuntimeDirectoryPreserve=restart", lines)
        self.assertIn("StateDirectory=pvj-player/late-screen", lines)
        self.assertEqual(latescreen.paths.PLAYER_STATE_DIR, "/var/lib/pvj-player/late-screen")


if __name__ == "__main__":
    unittest.main()
