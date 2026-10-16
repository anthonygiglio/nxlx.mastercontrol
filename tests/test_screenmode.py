# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The screen's mode (D90): which mode Automatic takes for each kind of screen, the exact --drm-mode text, the
fallbacks, the try that goes back by itself, the mapping through a change of size and of shape, and rollback.

Nothing here has a real screen: the lists are written as mpv 0.40 prints them (`mpv --drm-mode=help`), and the
player is a stand-in. What only a Pi can show is in tools/DEVICE-TESTING.md (D90's rows)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  (the lock order and a temp folder of its own, tests/__init__.py)
from pvj import mapper as M, screenmode as S
from pvj.player import Player, PlayerError
from pvj.settings import Settings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT = "11111111-2222-3333-4444-555555555555"


def help_text(*modes, connector="HDMI-A-1", card=1):
    """What mpv prints for one screen: "  Mode N: name (WxH@R.RRHz)"."""
    lines = ["Available modes for drm-connector=%d.%s" % (card, connector)]
    for i, m in enumerate(modes):
        name, hz = m if isinstance(m, tuple) else (m.split("@")[0], m.split("@")[1])
        size = name.rstrip("i")
        lines.append("  Mode %d: %s (%s@%sHz)" % (i, name, size, hz))
    return "\n".join(lines) + "\n"


# The lists, in the kernel's order (its own mode first, then larger before smaller, faster before slower).
XGA = help_text("1024x768@60.00", "1920x1080@60.00", "1920x1080@59.94", "1920x1080@50.00", ("1920x1080i", "60.00"),
                "1280x1024@60.02", "1280x720@60.00", "1280x720@50.00", "1024x768@75.03", "800x600@60.32", "640x480@59.94")
HD = help_text("1920x1080@60.00", "1920x1080@59.94", "1920x1080@50.00", "1920x1080@24.00", "1280x720@60.00",
               "1280x720@50.00", "720x576@50.00", "720x480@59.94")
WUXGA = help_text("1920x1200@59.95", "1920x1080@60.00", "1920x1080@50.00", "1600x1200@60.00", "1680x1050@59.88",
                  "1280x720@60.00")
# the owner's monitor: its own mode is 1440p at 75 Hz, with 60 and 50 below it
MONITOR = help_text("2560x1440@74.97", "2560x1440@59.95", "1920x1080@75.00", "1920x1080@60.00", "1920x1080@59.94",
                    "1920x1080@50.00", "1680x1050@59.95", "1600x900@60.00", "1280x1024@75.02", "1280x720@60.00",
                    "1280x720@50.00", "1024x768@60.00", "800x600@60.32")
ONLY_50 = help_text("1920x1080@50.00", "1280x720@50.00", "720x576@50.00")
UHD = help_text("3840x2160@60.00", "3840x2160@30.00", "2560x1440@59.95", "1920x1080@60.00", "1920x1080@50.00")
WIDE = help_text("3440x1440@59.97", "2560x1080@60.00", "1920x1080@60.00", "1280x720@60.00")
NO_60 = help_text("1920x1080@75.00", "1280x720@75.00")


def modes(text):
    return S.parse_help(text)[0]["modes"]


def connected():
    return [{"connector": "HDMI-A-1", "status": "connected", "modes": []},
            {"connector": "HDMI-A-2", "status": "disconnected", "modes": []}]


class ParseTest(unittest.TestCase):
    def test_mpvs_list_is_read_with_the_rate_as_mpv_prints_it(self):
        screens = S.parse_help("[vo/gpu/drm] noise\n" + XGA)
        self.assertEqual([(s["card"], s["connector"]) for s in screens], [(1, "HDMI-A-1")])
        m = screens[0]["modes"]
        self.assertEqual(len(m), 11)
        self.assertEqual(m[0], {"index": 0, "name": "1024x768", "width": 1024, "height": 768, "hz": 60.0,
                                "text": "1024x768@60.00", "interlaced": False})
        self.assertEqual((m[4]["text"], m[4]["interlaced"]), ("1920x1080@60.00", True))
        self.assertEqual(m[2]["text"], "1920x1080@59.94")

    def test_two_screens_and_nothing_else(self):
        both = S.parse_help(HD + help_text("1024x768@60.00", connector="HDMI-A-2"))
        self.assertEqual([s["connector"] for s in both], ["HDMI-A-1", "HDMI-A-2"])
        self.assertEqual(S.parse_help(""), [])
        self.assertEqual(S.parse_help("Mode 0: 1920x1080 (1920x1080@60.00Hz)"), [], "a mode with no screen before it is nobody's")
        self.assertEqual(S.parse_help("Available modes for drm-connector=0.HDMI-A-1\n  Mode 0: x (1920x1080@60Hz)")[0]["modes"], [],
                         "a rate that is not mpv's %.2f is not taken")

    def test_list_modes_runs_mpv_and_says_why_not(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw.get("timeout")))
            return subprocess.CompletedProcess(cmd, 0, stdout=MONITOR, stderr="")
        screens, problem = S.list_modes("mpvx", run)
        self.assertEqual((calls[0][0], calls[0][1]), (["mpvx", "--no-config", "--drm-mode=help"], S.HELP_TIMEOUT))
        self.assertEqual((len(screens), problem), (1, ""))

        def gone(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        self.assertEqual(S.list_modes("mpv", gone), ([], "mpv is not installed"))

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 8)
        self.assertEqual(S.list_modes("mpv", slow)[0], [])
        empty = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")   # noqa: E731
        self.assertEqual(S.list_modes("mpv", empty), ([], "mpv listed no screen"))


class AutomaticTest(unittest.TestCase):
    def pick(self, text):
        m, why = S.automatic(modes(text))
        return m["text"] if m else None, why

    def test_an_xga_projector_gets_its_own_1024x768_at_60(self):
        self.assertEqual(self.pick(XGA), ("1024x768@60.00", "the screen's own mode"))

    def test_a_1080p_projector_gets_1080p60(self):
        self.assertEqual(self.pick(HD)[0], "1920x1080@60.00")

    def test_a_wuxga_screen_gets_1920x1200_at_its_own_59_95(self):
        self.assertEqual(self.pick(WUXGA)[0], "1920x1200@59.95")

    def test_the_1440p_monitor_at_75_gets_1080p_at_60_not_75_or_50(self):
        text, why = self.pick(MONITOR)
        self.assertEqual(text, "1920x1080@60.00")
        self.assertIn("2560 x 1440 at 74.97 Hz", why)

    def test_a_screen_with_only_50_gets_1080p50(self):
        self.assertEqual(self.pick(ONLY_50)[0], "1920x1080@50.00")

    def test_a_4k_tv_and_an_ultrawide_get_1080p60(self):
        self.assertEqual(self.pick(UHD)[0], "1920x1080@60.00")
        text, why = self.pick(WIDE)
        self.assertEqual(text, "1920x1080@60.00")
        self.assertIn("no smaller mode of its own shape", why)

    def test_the_own_shape_comes_before_1080p(self):
        wide = help_text("2560x1600@59.97", "1920x1200@59.95", "1920x1080@60.00", "1680x1050@59.95")
        self.assertEqual(self.pick(wide)[0], "1920x1200@59.95")
        no1200 = help_text("2560x1600@59.97", "1920x1080@60.00", "1680x1050@59.95")
        self.assertEqual(self.pick(no1200)[0], "1680x1050@59.95", "16:10 kept before 1080p, as decided")

    def test_nothing_at_60_or_50_and_an_empty_list_give_no_mode(self):
        self.assertEqual(self.pick(NO_60)[0], None)
        self.assertEqual(S.automatic([]), (None, "the screen lists no mode"))
        self.assertEqual(S.automatic(modes(help_text(("1920x1080i", "60.00"))))[0], None, "an interlaced mode is never chosen")

    def test_the_rate_classes(self):
        self.assertEqual([S.rate_class(h) for h in (59.94, 59.95, 60.0, 60.02, 59.88, 50.0, 49.95, 75.0, 30.0, 59.7)],
                         [60, 60, 60, 60, 60, 50, 50, None, None, None])


class DrmValueTest(unittest.TestCase):
    def test_the_text_is_mpvs_own_with_two_decimals(self):
        m = modes(MONITOR)
        auto = S.automatic(m)[0]
        self.assertEqual(S.drm_value(auto, m), "1920x1080@60.00")
        self.assertEqual(S.drm_value(m[4], m), "1920x1080@59.94")
        self.assertEqual(S.mode_text(1024, 768, 60.0039), "1024x768@60.00")

    def test_an_interlaced_mode_with_the_same_numbers_first_makes_it_the_index(self):
        m = modes(help_text(("1920x1080i", "60.00"), "1920x1080@60.00", "1280x720@60.00"))
        self.assertEqual(S.drm_value(m[1], m), "1")
        self.assertEqual(S.drm_value(m[2], m), "1280x720@60.00")

    def test_mpv_matches_the_text_as_its_own_code_does(self):
        """drm_common.c: (int)round(refresh*100) == (int)round(mode_refresh*100), the mode's rate from its timings."""
        timings = {"1920x1080@60.00": (148500, 2200, 1125), "1920x1080@59.94": (148352, 2200, 1125),
                   "1024x768@60.00": (65000, 1344, 806), "1920x1200@59.95": (154000, 2080, 1235)}
        for text, (clock, htotal, vtotal) in timings.items():
            rate = clock * 1000.0 / htotal / vtotal
            self.assertEqual(S.mode_text(1, 1, rate).split("@")[1], text.split("@")[1])
            self.assertEqual(int(round(float(text.split("@")[1]) * 100)), int(round(rate * 100)), text)


class DecideTest(unittest.TestCase):
    def decide(self, wanted, text=MONITOR, sysfs=None, boot=BOOT):
        screens = S.parse_help(text)
        return S.decide(S.clean_wanted(wanted), screens, "", connected() if sysfs is None else sysfs, boot)

    def test_automatic(self):
        rep = self.decide({})
        self.assertEqual((rep["asked"], rep["used"], rep["arg"], rep["why"]), ("auto", "1920x1080@60.00", "--drm-mode=1920x1080@60.00", ""))
        self.assertEqual((rep["connector"], rep["own"], rep["automatic"]), ("HDMI-A-1", "2560x1440@74.97", "1920x1080@60.00"))

    def test_l5_two_cards_the_hdmi_screen_and_its_card_are_passed(self):
        """A Pi 5 (the review of #139, L5): composite on one card, HDMI on another. The mode is the HDMI screen's,
        and mpv is told which connector on which card, so the mode is never set on the other one."""
        text = help_text("720x576@50.00", "720x480@59.94", connector="Composite-1", card=0) + help_text(
            "1024x768@60.00", "1920x1080@60.00", connector="HDMI-A-2", card=2)
        rep = self.decide({}, text=text)
        self.assertEqual((rep["connector"], rep["card"], rep["used"]), ("HDMI-A-2", 2, "1024x768@60.00"))
        self.assertEqual(rep["connector_arg"], "--drm-connector=2.HDMI-A-2")
        rep = self.decide({}, text=help_text("720x576@50.00", connector="Composite-1", card=0) + help_text("800x480@60.00", connector="DSI-1", card=1))
        self.assertEqual((rep["connector"], rep["connector_arg"]), ("Composite-1", "--drm-connector=0.Composite-1"), "no HDMI: the first one listed")
        self.assertEqual(S.clean_report(dict(rep, pid=1))["connector_arg"], "--drm-connector=0.Composite-1")
        self.assertIsNone(S.clean_report(dict(rep, connector_arg="--drm-connector=0.x;reboot"))["connector_arg"])

    def test_a_mode_chosen_by_hand(self):
        rep = self.decide({"mode": "1920x1080@50.00"})
        self.assertEqual((rep["used"], rep["arg"], rep["why"]), ("1920x1080@50.00", "--drm-mode=1920x1080@50.00", ""))

    def test_a_chosen_mode_the_screen_does_not_offer_now_is_automatic_and_says_so(self):
        rep = self.decide({"mode": "1920x1080@50.00"}, text=XGA.replace("1920x1080@50.00", "1920x1080@48.00"))
        self.assertEqual(rep["used"], "1024x768@60.00")
        self.assertEqual(rep["why"], "1920 x 1080 at 50 Hz is not offered by this screen now, so Automatic is used: 1024 x 768 at 60 Hz")
        rep = self.decide({"mode": "1920x1080@60.00"}, text=help_text(("1920x1080i", "60.00"), "1280x720@60.00"))
        self.assertEqual(rep["used"], "1280x720@60.00", "an interlaced mode is not a chosen mode's stand-in")

    def test_a_try_is_taken_only_in_the_boot_it_was_made(self):
        wanted = {"mode": "auto", "trial": {"mode": "1920x1080@50.00", "boot": BOOT}}
        rep = self.decide(wanted)
        self.assertEqual((rep["asked"], rep["trial"], rep["used"]), ("1920x1080@50.00", True, "1920x1080@50.00"))
        rep = self.decide(wanted, boot="another-boot")
        self.assertEqual((rep["asked"], rep["trial"], rep["used"]), ("auto", False, "1920x1080@60.00"))

    def test_a_mode_that_did_not_start_is_not_given_again_in_this_boot(self):
        wanted = {"mode": "auto", "skip": {"mode": "1920x1080@60.00", "boot": BOOT}}
        rep = self.decide(wanted)
        self.assertEqual((rep["used"], rep["arg"]), (None, None))
        self.assertEqual(rep["why"], "1920 x 1080 at 60 Hz did not start on this screen, so the screen's own mode is used")
        self.assertEqual(self.decide(wanted, boot="next")["used"], "1920x1080@60.00", "after a restart of the box it is tried again")

    def test_no_screen_connected_is_as_before(self):
        rep = S.decide({"mode": "auto"}, [], "", [{"connector": "HDMI-A-1", "status": "disconnected", "modes": []}], BOOT)
        self.assertEqual((rep["used"], rep["arg"]), (None, None))
        self.assertIn("no screen is connected", rep["why"])
        rep = S.decide({"mode": "auto"}, [], "mpv did not list the modes (TimeoutExpired)", connected(), BOOT)
        self.assertEqual(rep["arg"], None)
        self.assertIn("could not be read (mpv did not list the modes (TimeoutExpired))", rep["why"])

    def test_nothing_at_60_or_50_is_the_screens_own_and_says_so(self):
        rep = self.decide({}, text=NO_60)
        self.assertEqual(rep["arg"], None)
        self.assertIn("no mode up to 1920 x 1200 at 60 or 50 Hz", rep["why"])

    def test_what_the_player_takes_from_the_file_is_checked(self):
        self.assertEqual(S.clean_wanted({"mode": "1920x1080@60"}), {"mode": "auto"})
        self.assertEqual(S.clean_wanted({"mode": "$(reboot)"}), {"mode": "auto"})
        self.assertEqual(S.clean_wanted({"mode": "auto", "trial": {"mode": "1x1@1.00", "boot": BOOT}}), {"mode": "auto"})
        self.assertEqual(S.clean_wanted({"mode": "auto", "trial": {"mode": "10x10@1.00", "boot": 5}}), {"mode": "auto"})


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        self.env = {"PVJ_STATE_DIR": self.state}
        self.asked = []

    def run_mpv(self, text):
        def run(cmd, **kw):
            self.asked.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
        return run

    def prepare(self, args=("--vo=gpu", "--gpu-context=drm"), text=MONITOR, sysfs=None):
        lines = []
        out = S.prepare(list(args), self.run_dir, "mpv", env=self.env, run=self.run_mpv(text),
                        sysfs=connected() if sysfs is None else sysfs, boot=BOOT, log=lines.append)
        return out, lines

    def report(self):
        with open(os.path.join(self.run_dir, S.REPORT)) as f:
            return json.load(f)

    def test_the_argument_and_the_report(self):
        out, lines = self.prepare()
        self.assertEqual(out, ["--drm-connector=1.HDMI-A-1", "--drm-mode=1920x1080@60.00"])
        rep = self.report()
        self.assertEqual((rep["used"], rep["pid"], rep["boot"]), ("1920x1080@60.00", os.getpid(), BOOT))
        self.assertEqual(oct(os.stat(os.path.join(self.run_dir, S.REPORT)).st_mode & 0o777), "0o640")
        self.assertEqual(lines, ["pvj-player: screen output: 1920 x 1080 at 60 Hz on HDMI-A-1 (--drm-connector=1.HDMI-A-1 --drm-mode=1920x1080@60.00)"])

    def test_the_wanted_file_is_read(self):
        S.write_json(os.path.join(self.state, S.WANTED), {"mode": "1280x720@50.00"})
        self.assertEqual(self.prepare()[0], ["--drm-connector=1.HDMI-A-1", "--drm-mode=1280x720@50.00"])
        S.write_json(os.path.join(self.state, S.WANTED), {"mode": "1280x720@51.00"})
        out, lines = self.prepare()
        self.assertEqual(out, ["--drm-connector=1.HDMI-A-1", "--drm-mode=1920x1080@60.00"])
        self.assertIn("is not offered by this screen now, so Automatic is used", lines[0])
        with open(os.path.join(self.state, S.WANTED), "w") as f:
            f.write("not json")
        self.assertEqual(self.prepare()[0], ["--drm-connector=1.HDMI-A-1", "--drm-mode=1920x1080@60.00"], "a damaged file is Automatic")

    def test_nothing_where_mpv_does_not_draw_through_drm_or_a_mode_was_given_by_hand(self):
        self.assertEqual(self.prepare(args=("--vo=gpu",))[0], [])
        self.assertEqual(self.prepare(args=("--gpu-context=drm", "--drm-mode=1024x768"))[0], [])
        self.assertEqual(self.asked, [], "mpv was not asked")
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, S.REPORT)))

    def test_no_screen_connected_asks_mpv_nothing_and_passes_nothing(self):
        out, lines = self.prepare(sysfs=[{"connector": "HDMI-A-1", "status": "disconnected", "modes": []}])
        self.assertEqual((out, self.asked), ([], []))
        self.assertIn("no screen is connected", self.report()["why"])

    def test_a_failure_anywhere_starts_the_player_as_before(self):
        def boom(cmd, **kw):
            raise RuntimeError("no")
        out = S.prepare(["--gpu-context=drm"], self.run_dir, "mpv", env=self.env, run=boom, sysfs=connected(), boot=BOOT)
        self.assertEqual(out, [])
        self.assertEqual(self.report()["why"], "the mode was not chosen (RuntimeError)")
        out = S.prepare(["--gpu-context=drm"], os.path.join(self.run_dir, "missing"), "mpv", env=self.env,
                        run=self.run_mpv(MONITOR), sysfs=connected(), boot=BOOT)
        self.assertEqual(out, ["--drm-connector=1.HDMI-A-1", "--drm-mode=1920x1080@60.00"], "a report that cannot be written does not stop the mode")

    def test_serve_passes_it_to_mpv(self):
        p = Player(mpv_bin="mpv", extra_args=["--vo=gpu", "--gpu-context=drm"], rundir=self.run_dir)
        seen = []
        with mock.patch("pvj.player.detach_socket_opener"), \
                mock.patch("pvj.player.os.execvp", side_effect=lambda f, a: seen.append(a)), \
                mock.patch("pvj.screenmode.list_modes", return_value=(S.parse_help(XGA), "")), \
                mock.patch("pvj.screenmode.hardware.drm_connectors", return_value=connected()), \
                mock.patch.dict(os.environ, self.env):
            p.serve()
        self.assertEqual(seen[0][-1], "--drm-mode=1024x768@60.00")
        self.assertIn("--gpu-context=drm", seen[0])


class FakeTimer:
    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.cancelled, self.started = seconds, fn, False, False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class ScreenTest(unittest.TestCase):
    """The panel's half against a stand-in player: a quit makes a new pid, whose report the test writes as the
    player's service would."""

    class Player:
        def __init__(self, test):
            self.test, self.pid, self.quits, self.vo, self.texts = test, 100, 0, "gpu", []
            outer = self

            class Ipc:
                def request(self, *a):
                    if a[0] == "show-text":
                        outer.texts.append(a[1:])
                        return None
                    key = a[1]
                    if key == "pid":
                        return outer.pid
                    if key == "current-vo":
                        if isinstance(outer.vo, Exception):
                            raise outer.vo                          # a word with the player that failed (L3)
                        if not outer.vo:
                            raise PlayerError("mpv: property unavailable")      # what Ipc.request says for mpv's answer
                        return outer.vo
                    if key == "path":
                        return getattr(outer, "path", None)
                    return {"display-width": 1920, "display-height": 1080, "display-fps": 60.0}.get(key)
            self.ipc = Ipc()

        def quit(self):
            self.quits += 1
            self.pid += 1
            self.test.player_started()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state, self.pdir = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "player")
        os.makedirs(self.state)
        os.makedirs(self.pdir)
        self.settings = Settings(os.path.join(self.state, "settings.json"))
        self.settings.load()
        self.now = [1000.0]
        self.timers = []
        self.text = MONITOR
        self.boot = BOOT
        self.lines = []

        class Api:
            pass
        self.api = Api()
        self.api.player = self.Player(self)
        self.api.access_on_screen = lambda: False
        self.api.is_test_pattern = lambda path: isinstance(path, str) and path.startswith(S.PATTERN_PREFIX)
        self.screen = S.Screen(self.api, self.settings, self.state, self.pdir, log=self.lines.append,
                               clock=lambda: self.now[0], boot=lambda: self.boot, timer=self.timer)
        self.player_started()

    def timer(self, seconds, fn):
        t = FakeTimer(seconds, fn)
        self.timers.append(t)
        return t

    def player_started(self):
        """What `pvj-player serve` does at each start: decide, and write the report with the new player's pid."""
        rep = S.decide(S.clean_wanted(S.read_json(os.path.join(self.state, S.WANTED))), S.parse_help(self.text), "",
                       connected(), self.boot)
        rep.update(pid=self.api.player.pid, boot=self.boot, at=0)
        S.write_json(os.path.join(self.pdir, S.REPORT), rep)

    def wanted(self):
        return S.read_json(os.path.join(self.state, S.WANTED))

    def later(self, seconds):
        self.now[0] += seconds

    def test_the_page_lists_the_screens_modes_automatic_first_and_says_what_is_used(self):
        st = self.screen.state()
        self.assertEqual((st["saved"], st["used"], st["automatic"], st["own"], st["why"]),
                         ("auto", "1920x1080@60.00", "1920x1080@60.00", "2560x1440@74.97", ""))
        self.assertEqual(st["modes"][0], {"mode": "2560x1440@74.97", "width": 2560, "height": 1440, "hz": 74.97, "words": "2560 x 1440 at 74.97 Hz"})
        self.assertEqual(len(st["modes"]), 13)
        self.assertEqual(st["display"], {"width": 1920, "height": 1080, "hz": 60.0})
        self.assertEqual(self.screen.brief(), {"asked": "auto", "used": "1920x1080@60.00", "why": ""})

    def test_a_change_is_tried_counts_down_once_the_player_is_back_and_goes_back_by_itself(self):
        st = self.screen.choose("1920x1080@50.00")
        self.assertEqual(self.api.player.quits, 1)
        self.assertEqual(self.wanted(), {"mode": "auto", "trial": {"mode": "1920x1080@50.00", "boot": BOOT}})
        self.assertEqual(self.settings.data["screen"], {"mode": "auto"}, "nothing is saved while it is tried")
        self.assertEqual(self.timers[0].seconds, S.START_SECONDS)
        self.assertTrue(self.timers[0].cancelled, "the count from the tap gave way to the count from the new player")
        self.assertEqual(st["pending"]["state"], "waiting", "the stand-in player is back at once, so the count runs")
        self.assertEqual(st["pending"]["seconds_left"], 15)
        self.assertEqual(st["used"], "1920x1080@50.00")
        self.assertEqual(self.timers[-1].seconds, 15)
        self.later(14)
        self.assertFalse(self.timers[-1].fn(), "not yet")
        self.assertEqual(self.screen.state()["pending"]["seconds_left"], 1)
        self.later(1)
        self.assertTrue(self.timers[-1].fn())
        self.assertIsNone(self.screen.pending)
        self.assertEqual(self.wanted(), {"mode": "auto"})
        self.assertEqual(self.api.player.quits, 2, "the player was started again with the old mode")
        self.assertEqual(self.screen.report()["used"], "1920x1080@60.00")
        self.assertEqual(self.screen.note, "Nobody kept 1920 x 1080 at 50 Hz, so it went back by itself.")

    def test_the_15_seconds_begin_when_the_new_player_is_up_not_at_the_tap(self):
        player = self.api.player

        def slow_quit():                          # the service needs a few seconds to start the new one
            player.quits += 1
            player.pid += 1
        player.quit = slow_quit
        self.screen.choose("1920x1080@50.00")
        self.assertEqual(self.screen.state()["pending"]["state"], "starting")
        self.later(6)
        self.player_started()
        self.assertEqual(self.screen.tick(), "counting")
        st = self.screen.state()["pending"]
        self.assertEqual((st["state"], st["seconds_left"]), ("waiting", 15))
        self.later(10)
        self.assertFalse(self.timers[-1].fn(), "16 seconds after the tap, 10 after the picture: not yet")
        self.later(5)
        self.assertTrue(self.timers[-1].fn())

    def test_kept_it_is_saved_and_stays(self):
        self.screen.choose("1920x1080@50.00")
        st = self.screen.confirm()
        self.assertIsNone(st["pending"])
        self.assertEqual(self.settings.data["screen"], {"mode": "1920x1080@50.00"})
        self.assertEqual(Settings(self.settings.path).load()["screen"], {"mode": "1920x1080@50.00"})
        self.assertEqual(self.wanted(), {"mode": "1920x1080@50.00"})
        self.assertTrue(self.timers[-1].cancelled)
        self.assertEqual(self.api.player.quits, 1, "keeping it restarts nothing")
        self.later(60)
        self.assertFalse(self.timers[-1].fn())
        self.assertEqual(self.settings.data["screen"], {"mode": "1920x1080@50.00"})

    def test_go_back_now(self):
        self.screen.choose("1280x720@60.00")
        self.screen.revert()
        self.assertEqual((self.wanted(), self.api.player.quits), ({"mode": "auto"}, 2))
        self.assertEqual(self.screen.revert()["pending"], None, "twice is nothing")
        self.assertEqual(self.api.player.quits, 2)

    def test_a_player_that_does_not_come_back_goes_back_after_START_SECONDS(self):
        self.api.player.quit = lambda: setattr(self.api.player, "quits", self.api.player.quits + 1)    # no new player
        self.screen.choose("1280x720@60.00")
        self.assertEqual(self.screen.state()["pending"]["state"], "starting")
        with self.assertRaises(S.ScreenError) as e:
            self.screen.confirm()
        self.assertEqual(e.exception.status, 409, "nobody can keep what nobody has seen")
        self.later(S.START_SECONDS - 5)
        self.assertFalse(self.timers[-1].fn())
        self.later(5)
        self.assertTrue(self.timers[-1].fn())
        self.assertEqual(self.wanted(), {"mode": "auto"})

    # -- the review of #139 ------------------------------------------------------------------------------------------
    def test_m1_a_pipe_a_link_a_folder_or_a_huge_file_is_no_report_and_never_waits(self):
        import threading
        import time as _time
        rpath = os.path.join(self.pdir, S.REPORT)
        good = S.read_json(rpath)
        self.assertTrue(good)
        for make in ("fifo", "link", "folder", "huge"):
            os.unlink(rpath) if not os.path.isdir(rpath) or os.path.islink(rpath) else os.rmdir(rpath)
            if make == "fifo":
                os.mkfifo(rpath)
            elif make == "link":
                other = os.path.join(self.tmp, "elsewhere.json")
                S.write_json(other, dict(good))
                os.symlink(other, rpath)
            elif make == "folder":
                os.mkdir(rpath)
            else:
                with open(rpath, "w") as f:
                    f.write('{"why": "' + "x" * (S.MAX_FILE + 10) + '"}')
            self.screen._report_cache = (None, None)
            out = {}
            t = threading.Thread(target=lambda: out.update(rep=self.screen.report(), brief=self.screen.brief(),
                                                            raw=S.read_json(rpath)), daemon=True)
            started = _time.monotonic()
            t.start()
            t.join(2.0)
            self.assertFalse(t.is_alive(), "%s: the read waited (a stuck thread for every status poll)" % make)
            self.assertLess(_time.monotonic() - started, 2.0)
            self.assertEqual((out["rep"], out["raw"]), (None, {}), make)
            self.assertEqual(out["brief"]["used"], None, make)
        if os.path.isdir(rpath):
            os.rmdir(rpath)
        else:
            os.unlink(rpath)

    def test_l1_keep_and_the_timer_are_one_step_in_either_order(self):
        import threading
        self.screen.choose("1920x1080@50.00")
        self.later(20)                                  # the 15 seconds are over: the timer may fire at any moment
        timer = self.timers[-1].fn
        real_save, fired = self.settings.save, []

        def save_while_the_timer_fires():               # the timer's thread comes in the middle of Keep's save
            t = threading.Thread(target=lambda: fired.append(timer()), daemon=True)
            t.start()
            t.join(0.3)
            real_save()
            self.later(0)
            return None
        with mock.patch.object(self.settings, "save", side_effect=save_while_the_timer_fires):
            st = self.screen.confirm()
        for _ in range(50):
            if fired:
                break
            import time as _time
            _time.sleep(0.02)
        self.assertEqual(fired, [False], "the timer found nothing to take back once Keep was done")
        self.assertIsNone(st["pending"])
        self.assertEqual((self.settings.data["screen"], self.wanted()), ({"mode": "1920x1080@50.00"}, {"mode": "1920x1080@50.00"}))
        self.assertEqual(self.api.player.quits, 1, "kept: the player was not sent back")
        # the other order: the timer first, then Keep is refused and nothing is saved
        self.screen.choose("1280x720@60.00")
        self.later(20)
        self.assertTrue(self.timers[-1].fn())
        with self.assertRaises(S.ScreenError) as e:
            self.screen.confirm()
        self.assertEqual(e.exception.status, 409)
        self.assertEqual((self.settings.data["screen"], self.wanted()), ({"mode": "1920x1080@50.00"}, {"mode": "1920x1080@50.00"}))

    def test_l2_two_threads_looking_at_a_player_restart_it_once(self):
        import threading
        self.api.player.vo = ""
        pid = self.api.player.pid
        self.screen.tick(pid)
        self.later(S.SCREEN_GRACE)
        gate = threading.Barrier(2, timeout=1.0)
        clock = self.screen._clock

        def meeting_clock():                            # both threads reach the look before either has claimed it
            if threading.current_thread().name.startswith("look"):
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
            return clock()
        self.screen._clock = meeting_clock
        quits = []
        self.api.player.quit = lambda: quits.append(1)
        threads = [threading.Thread(target=self.screen.tick, args=(pid,), name="look%d" % i, daemon=True) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(len(quits), 1, "one restart, not two")
        self.assertEqual(sum("has no screen" in line for line in self.lines), 1, "and one look")
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp")], [], "no temp file left")

    def test_two_threads_writing_the_wanted_file_at_once_both_succeed(self):
        import threading
        errors, path = [], os.path.join(self.state, S.WANTED)

        def write(n):
            for i in range(200):
                try:
                    S.write_json(path, {"mode": "auto", "n": n, "i": i})
                except OSError as e:
                    errors.append(e)
        threads = [threading.Thread(target=write, args=(n,), daemon=True) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp")], [])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o640")

    def test_l3_a_word_with_the_player_that_failed_is_not_known_yet(self):
        self.api.player.vo = PlayerError("ipc error: [Errno 104] Connection reset by peer")
        self.screen.tick()
        self.later(S.SCREEN_GRACE)
        self.assertIsNone(self.screen.tick())
        self.assertEqual((self.api.player.quits, self.wanted().get("skip")), (0, None), "no skip for a hiccup")
        self.api.player.vo = PlayerError("no reply from mpv")
        self.later(2)
        self.assertIsNone(self.screen.tick())
        self.api.player.vo = "gpu"
        self.later(2)
        self.assertIsNone(self.screen.tick(), "asked again and answered")
        self.assertEqual(self.api.player.quits, 0)
        self.assertEqual(self.screen._has_screen(), True)
        self.api.player.vo = ""
        self.assertIs(self.screen._has_screen(), False, "mpv's own answer: no output")

    def test_l4_a_setting_of_the_wrong_kind_is_automatic_and_the_status_answers(self):
        for value in ("1280x720@60.00", ["x"], 7, None, {"mode": 5}):
            self.settings.data["screen"] = value
            self.assertEqual(self.screen.saved(), "auto", value)
            self.assertEqual(self.screen.brief()["asked"], "auto", value)

    def test_what_may_be_chosen(self):
        for bad, status in (("1920x1080@60", 400), ("1920x1080", 400), ("", 400), (None, 400), ("1920x1080@61.00", 409)):
            with self.assertRaises(S.ScreenError) as e:
                self.screen.choose(bad)
            self.assertEqual(e.exception.status, status, bad)
        self.assertIsNone(self.screen.choose("auto")["pending"], "what is saved already is no change")
        self.screen.choose("1280x720@50.00")
        with self.assertRaises(S.ScreenError):
            self.screen.choose("1280x720@60.00")
        self.assertEqual(self.api.player.quits, 1)

    def test_a_mode_that_gives_no_screen_is_started_again_with_the_screens_own(self):
        self.api.player.vo = ""
        self.assertIsNone(self.screen.tick())                   # first sight
        self.later(S.SCREEN_GRACE - 1)
        self.assertIsNone(self.screen.tick(), "it is given time")
        self.later(1)
        self.assertEqual(self.screen.tick(), "own mode")
        self.assertEqual(self.wanted(), {"mode": "auto", "skip": {"mode": "1920x1080@60.00", "boot": BOOT}})
        self.assertEqual(self.api.player.quits, 1)
        rep = self.screen.report()
        self.assertEqual((rep["used"], rep["arg"]), (None, None))
        self.assertIn("did not start on this screen", rep["why"])
        self.assertIn("did not start on this screen; the player was started with the screen's own mode", self.screen.note)
        self.later(60)
        self.assertIsNone(self.screen.tick(), "a player with no --drm-mode is not restarted")
        self.assertEqual(self.api.player.quits, 1, "no loop")

    def test_a_tried_mode_that_gives_no_screen_goes_back(self):
        self.screen.choose("1280x720@60.00")
        self.api.player.vo = ""
        self.later(S.SCREEN_GRACE)
        self.assertEqual(self.screen.tick(), "reverted")
        self.assertEqual(self.wanted(), {"mode": "auto"})
        self.assertIn("did not start on this screen, so it went back", self.screen.note)

    def test_a_player_with_a_screen_is_looked_at_once(self):
        self.screen.tick()
        self.later(S.SCREEN_GRACE)
        self.assertIsNone(self.screen.tick())
        self.api.player.vo = ""
        self.later(60)
        self.assertIsNone(self.screen.tick())
        self.assertEqual(self.api.player.quits, 0)

    def test_the_panels_start_writes_the_setting_and_takes_back_a_try_of_this_boot(self):
        self.settings.data["screen"] = {"mode": "1280x720@60.00"}
        self.assertFalse(self.screen.start())
        self.assertEqual(self.wanted(), {"mode": "1280x720@60.00"})
        S.write_json(os.path.join(self.state, S.WANTED), {"mode": "1280x720@60.00", "trial": {"mode": "1920x1080@50.00", "boot": BOOT}})
        self.assertTrue(self.screen.start())
        self.assertEqual((self.wanted(), self.api.player.quits), ({"mode": "1280x720@60.00"}, 1))
        S.write_json(os.path.join(self.state, S.WANTED), {"mode": "1280x720@60.00", "trial": {"mode": "1920x1080@50.00", "boot": "before"}})
        self.assertFalse(self.screen.start(), "after a restart of the box the player never took it: nothing to restart")
        self.assertEqual((self.wanted(), self.api.player.quits), ({"mode": "1280x720@60.00"}, 1))
        self.settings.data["screen"] = {"mode": "nonsense"}
        self.screen.start()
        self.assertEqual(self.wanted(), {"mode": "auto"}, "a damaged setting is Automatic")

    def test_after_an_import_or_a_reset_the_player_follows_the_settings(self):
        self.screen.sync()
        self.assertEqual(self.api.player.quits, 0)
        self.settings.data["screen"] = {"mode": "1280x720@60.00"}
        self.screen.sync()
        self.assertEqual((self.wanted(), self.api.player.quits), ({"mode": "1280x720@60.00"}, 1))

    def test_the_report_is_read_as_coming_from_a_less_trusted_account(self):
        S.write_json(os.path.join(self.pdir, S.REPORT), {
            "used": "<b>", "arg": "--drm-mode=1x1;reboot", "why": "<script>x</script> ${pid}", "pid": True,
            "modes": [{"text": "1920x1080@60.00", "index": 0}, {"text": "evil", "index": 1}, "x", {"text": "1x1@1.00", "index": "0"}] + [{"text": "1920x1080@60.00", "index": 0}] * 100})
        rep = self.screen.report()
        self.assertEqual((rep["used"], rep["arg"], rep["pid"]), (None, None, None))
        self.assertEqual(rep["why"], "scriptx/script pid")
        self.assertEqual(len(rep["modes"]), S.MAX_MODES - 3, "of the first MAX_MODES, the three that are not modes are dropped")
        with open(os.path.join(self.pdir, S.REPORT), "w") as f:
            f.write("[" * 100000)
        self.assertIsNone(self.screen.report())

    def test_the_words_over_the_test_pattern(self):
        self.api.player.path = S.pattern_url((1920, 1080))
        self.screen.watch(self.api.player.pid)
        self.assertEqual(self.api.player.texts[-1], ("Screen output: 1920 x 1080 at 60 Hz (Automatic)", S.PATTERN_WORDS_MS))
        self.api.access_on_screen = lambda: True
        n = len(self.api.player.texts)
        self.screen.watch(self.api.player.pid)
        self.assertEqual(len(self.api.player.texts), n, "never over a code someone asked to have on the display")
        self.api.player.path = "/media/a.mp4"
        self.api.access_on_screen = lambda: False
        self.screen.watch(self.api.player.pid)
        self.assertEqual(len(self.api.player.texts), n)


class PatternTest(unittest.TestCase):
    def test_the_pattern_is_at_the_screens_size_with_a_grid_and_a_border(self):
        self.assertEqual(S.pattern_url((1024, 768)),
                         "av://lavfi:smptehdbars=size=1024x768:rate=25,drawgrid=w=64:h=64:t=1:c=white@0.8,drawbox=x=0:y=0:w=1024:h=768:t=1:c=red")
        self.assertTrue(S.pattern_url((1920, 1080)).startswith(S.PATTERN_PREFIX))
        for bad in ((0, 0), ("1920", 1080), (1920.5, 1080), (99999, 10)):
            with self.assertRaises(ValueError):
                S.pattern_url(bad)

    def test_the_words(self):
        self.assertEqual(S.pattern_text({"used": "1024x768@60.00", "asked": "auto"}), "Screen output: 1024 x 768 at 60 Hz (Automatic)")
        self.assertEqual(S.pattern_text({"used": "1920x1080@50.00", "asked": "1920x1080@50.00", "trial": True}),
                         "Screen output: 1920 x 1080 at 50 Hz (being tried)")
        self.assertEqual(S.pattern_text({"used": None, "asked": "1280x720@60.00"}, {"width": 2560, "height": 1440, "hz": 74.97}),
                         "Screen output: 2560 x 1440 at 74.97 Hz (chosen by hand)")


@unittest.skipUnless(shutil.which("mpv"), "no mpv here (CI has it)")
class RealMpvTest(unittest.TestCase):
    """The texts against a real mpv (CI: Ubuntu's 0.37; the ndi-mpv job's whole suite: Debian's 0.40, the Pi's)."""

    def mpv(self, *args):
        return subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--frames=2", "--msg-level=all=error"] + list(args),
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_mpv_takes_the_drm_mode_text_and_refuses_a_wrong_one(self):
        ok = self.mpv("--drm-mode=1920x1080@60.00", "av://lavfi:color=c=black:size=64x64:rate=25")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.mpv("--drm-mode=1", "av://lavfi:color=c=black:size=64x64:rate=25").returncode, 0)
        bad = self.mpv("--drm-mode=1920x1080@sixty", "av://lavfi:color=c=black:size=64x64:rate=25")
        self.assertNotEqual(bad.returncode, 0, "mpv checks the text: the test above means something")
        both = self.mpv("--drm-connector=1.HDMI-A-1", "--drm-mode=1920x1080@60.00", "av://lavfi:color=c=black:size=64x64:rate=25")
        self.assertEqual(both.returncode, 0, both.stderr)          # the connector with its card (L5) is an option mpv takes

    def test_mpv_opens_the_test_pattern_graph(self):
        r = self.mpv(S.pattern_url((1024, 768)))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_help_list_parses_or_is_empty(self):
        """No screen in CI: mpv lists nothing (or says it cannot open a card). It must not hang or crash the parser."""
        screens, problem = S.list_modes()
        self.assertIsInstance(screens, list)
        self.assertIsInstance(problem, str)


W, H = 2560, 1440


class MappingTest(unittest.TestCase):
    """D28 and D58 meet D90: a mapping made on the 1440p monitor, after the mode changes to 1080p (the same shape)
    and to 1024 x 768 (another shape)."""

    def setUp(self):
        from tests.test_mapper import EngineTest
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.settings = Settings(os.path.join(tmp, "settings.json"))
        self.settings.load()
        self.size = [W, H]
        outer = self

        class Player(EngineTest.Player):
            def osd_size(self):
                return tuple(outer.size)

        class Api:
            pass
        self.api = Api()
        self.api.settings, self.api.player, self.api.registry = self.settings, Player(tmp), EngineTest.Registry()
        self.e = M.Engine(self.api, log=lambda *_: None)
        self.e.handle({"action": "add", "type": "quad"})
        sid = self.e.state()["surfaces"][0]["id"]
        verts = [[100, 100], [2460, 100], [2460, 1340], [100, 1340]]
        for i, (x, y) in enumerate(verts):
            self.e.handle({"action": "place", "id": sid, "corner": i, "target": "screen", "x": x, "y": y})
        self.assertEqual(self.settings.data["mapper"]["screen"], [W, H])

    def test_to_1080p_the_points_scale_and_nothing_is_said(self):
        self.size = [1920, 1080]
        st = self.e.state()
        self.assertEqual(st["surfaces"][0]["vertices"], [[75, 75], [1845, 75], [1845, 1005], [75, 1005]])
        self.assertEqual((st["screen"], st["made_at"], st["reshaped"]), ([1920, 1080], [W, H], False))

    def test_to_1024x768_each_axis_on_its_own_and_the_page_asks_for_a_look(self):
        self.size = [1024, 768]
        st = self.e.state()
        self.assertEqual(st["surfaces"][0]["vertices"], [[40, 53.3333], [984, 53.3333], [984, 714.6667], [40, 714.6667]])
        self.assertTrue(st["reshaped"])
        self.size = [W, H]
        self.assertEqual(self.e.state()["surfaces"][0]["vertices"][1], [2460, 100], "and back, where it was")
        self.size = [1024, 768]
        sid = st["surfaces"][0]["id"]
        st = self.e.handle({"action": "move", "id": sid, "corner": 0, "dx": 1, "dy": 0})
        self.assertEqual((st["made_at"], st["reshaped"]), ([1024, 768], False), "a change made on this screen is made at this size")

    def test_the_shape_rule(self):
        self.assertFalse(M.shape_changed((2560, 1440), (1920, 1080)))
        self.assertFalse(M.shape_changed((1920, 1200), (1680, 1050)))
        self.assertTrue(M.shape_changed((1920, 1080), (1024, 768)))
        self.assertTrue(M.shape_changed((1920, 1080), (1920, 1200)))
        self.assertFalse(M.shape_changed(None, (1920, 1080)))


# The release before D90, by its commit (master at 33e7eb4, the merge of #137). An older release is named by a commit,
# never by a branch (see tests/test_ndi_pads.py).
BEFORE = "33e7eb419eb1b26b08618dd54b3e1481d4b60f0e"


class RollbackTest(unittest.TestCase):
    def test_the_release_before_loads_the_file_keeps_the_setting_and_maps_as_it_did(self):
        try:
            full = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", BEFORE + "^{commit}"], check=True,
                                  capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % BEFORE)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", full, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "screenmode.py")), "the release before D90")
        s = Settings(os.path.join(old, "settings.json"))
        s.load()
        s.data["screen"] = {"mode": "1024x768@60.00"}
        s.data["mapper"] = {"on": True, "screen": [2560, 1440], "sets": {},
                            "surfaces": M.validate_mapping({"surfaces": [M.default_surface("quad", 2560, 1440)]})}
        s.save()
        script = r'''
import json, sys
from pvj.settings import Settings
from pvj import mapper
s = Settings(sys.argv[1])
s.load()
moved = mapper.scaled(s.data["mapper"]["surfaces"], s.data["mapper"]["screen"], (1024, 768))
s.data["mix"] = {"transition": "dip", "duration": 2.0}
s.save()
print(json.dumps({"schema": s.data["schema"], "moved": moved[0]["vertices"]}))
'''
        run = subprocess.run([sys.executable, "-c", script, s.path], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(out["schema"], s.data["schema"])
        self.assertEqual(out["moved"], M.scaled(s.data["mapper"]["surfaces"], [2560, 1440], (1024, 768))[0]["vertices"])
        with open(s.path) as f:
            kept = json.load(f)
        self.assertEqual(kept["screen"], {"mode": "1024x768@60.00"}, "the older release kept what it does not know")
        again = Settings(s.path)
        again.load()
        self.assertEqual(again.data["screen"], {"mode": "1024x768@60.00"})


if __name__ == "__main__":
    unittest.main()
