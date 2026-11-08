# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The output (D127): which connector the picture goes to, and the AV (composite) output on a Raspberry Pi.

Fake /sys/class/drm trees stand for a Pi 4 (two HDMI and composite), a Pi 3B (one HDMI and composite), a Pi 5 (outputs on
two cards) and a desktop; the boot files are fake folders; the helper is driven with a fake runner. Nothing here was run on a
node: tools/DEVICE-TESTING.md has the rows for composite on the real ones."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import bootcfg, hardware, latescreen, screenmode as S, sysd
from tests.test_boxcare import Base as CareBase

BOOT = "boot-1"
PI4_CONFIG = """# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on
camera_auto_detect=1
display_auto_detect=1
auto_initramfs=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overlay_prefix_check=1
[cm4]
otg_mode=1
[all]
enable_uart=1
"""
PI3_CONFIG = """dtparam=audio=on
dtoverlay=vc4-kms-v3d,cma-256 # the driver
max_framebuffers=2
[pi4]
dtoverlay=vc4-kms-v3d-pi4,noaudio
"""
CMDLINE = "console=serial0,115200 console=tty1 root=PARTUUID=abcd-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash\n"


def tree(layout, files=None):
    """A fake root: sys/class/drm/cardN-NAME/{status,modes} from (card, name, status, modes), and boot files."""
    root = tempfile.mkdtemp(prefix="pvj-output-")
    for card, name, status, modes in layout:
        d = os.path.join(root, "sys/class/drm/card%d-%s" % (card, name))
        os.makedirs(d)
        with open(os.path.join(d, "status"), "w") as f:
            f.write(status + "\n")
        with open(os.path.join(d, "modes"), "w") as f:
            f.write("\n".join(modes) + ("\n" if modes else ""))
    for path, text in (files or {}).items():
        full = os.path.join(root, path.lstrip("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
    return root


HDMI_MODES = ["1920x1080", "1280x720", "1024x768"]
PI4 = [(1, "HDMI-A-1", "connected", HDMI_MODES), (1, "HDMI-A-2", "disconnected", []), (1, "Composite-1", "connected", ["720x480i", "720x576i"]),
       (1, "Writeback-1", "unknown", [])]
PI3 = [(0, "HDMI-A-1", "connected", HDMI_MODES), (0, "Composite-1", "connected", ["720x480i"])]
PI5 = [(1, "HDMI-A-1", "connected", HDMI_MODES), (1, "HDMI-A-2", "connected", ["1280x720"]), (2, "Composite-1", "connected", ["720x576i"])]
DESKTOP = [(0, "DP-1", "disconnected", []), (0, "HDMI-A-1", "connected", HDMI_MODES), (0, "eDP-1", "connected", ["1366x768"])]


def sysfs_of(layout):
    return hardware.drm_connectors(tree(layout))


def help_text(*screens):
    """What `mpv --drm-mode=help` prints: screens are (card, connector, [(name, w, h, hz)])."""
    out = []
    for card, connector, modes in screens:
        out.append("Available modes for drm-connector=%d.%s" % (card, connector))
        for i, (name, w, h, hz) in enumerate(modes):
            out.append("  Mode %d: %s (%dx%d@%.2fHz)" % (i, name, w, h, hz))
    return "\n".join(out) + "\n"


HDMI_HELP = [("1920x1080", 1920, 1080, 60.0), ("1280x720", 1280, 720, 60.0), ("1024x768", 1024, 768, 60.0)]
COMP_HELP = [("720x480i", 720, 480, 59.94), ("720x576i", 720, 576, 50.0)]


def decide(layout, text, wanted=None):
    return S.decide(S.clean_wanted(wanted or {}), S.parse_help(text), "", sysfs_of(layout), BOOT)


def args_of(rep):
    return S.screen_args(rep)


class HardwareTest(unittest.TestCase):
    def test_connector_names_kinds_and_labels(self):
        for name, kind, label in (("HDMI-A-1", "hdmi", "HDMI 1"), ("HDMI-A-2", "hdmi", "HDMI 2"), ("Composite-1", "composite", "AV (composite)"),
                                  ("DSI-1", "dsi", "DSI 1"), ("DP-2", "dp", "DisplayPort 2"), ("eDP-1", "dp", "eDP 1"), ("VGA-1", "vga", "VGA-1"),
                                  ("Weird-3", "other", "Weird-3")):
            self.assertEqual((hardware.connector_kind(name), hardware.output_label(name)), (kind, label), name)

    def test_the_list_leaves_writeback_out_on_every_layout(self):
        for layout in (PI4, PI3, PI5, DESKTOP):
            names = [c["connector"] for c in hardware.outputs(tree(layout))]
            self.assertFalse([n for n in names if n.lower().startswith("writeback")])
        self.assertEqual([c["connector"] for c in hardware.outputs(tree(PI4))], ["Composite-1", "HDMI-A-1", "HDMI-A-2"])

    def test_which_boards_have_an_av_output(self):
        self.assertEqual([hardware.av_capable(k) for k in ("pi3", "pi4", "pi5", "pi-other", "x86", "arm-other")],
                         [True, True, True, True, False, False])


class DecideTest(unittest.TestCase):
    """The connector the player is told, on each layout: Automatic is as it was (HDMI first), a name is used when connected."""

    def test_automatic_on_a_pi_4_is_the_same_arguments_as_before_the_setting(self):
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "Composite-1", COMP_HELP))
        rep = decide(PI4, text)
        self.assertEqual(args_of(rep), ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"])
        self.assertEqual((rep["output"], rep["output_why"], rep["why"]), ("auto", "", ""))
        self.assertEqual(S.clean_wanted({"mode": "auto"}), {"mode": "auto"}, "no output key is Automatic")

    def test_a_named_hdmi_output_that_is_connected_is_used(self):
        layout = [(1, "HDMI-A-1", "connected", HDMI_MODES), (1, "HDMI-A-2", "connected", ["1280x720"])]
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "HDMI-A-2", [("1280x720", 1280, 720, 60.0)]))
        rep = decide(layout, text, {"output": "HDMI-A-2"})
        self.assertEqual(args_of(rep), ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-2", "--drm-mode=1280x720@60.00"])
        self.assertEqual((rep["connector"], rep["output"], rep["output_why"]), ("HDMI-A-2", "HDMI-A-2", ""))

    def test_a_chosen_output_that_is_not_connected_falls_back_to_automatic_and_says_so(self):
        rep = decide(PI4, help_text((0, "HDMI-A-1", HDMI_HELP)), {"output": "HDMI-A-2"})
        self.assertEqual(args_of(rep), ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"])
        self.assertIn("HDMI 2 is not connected, so Automatic is used for now", rep["output_why"])
        self.assertIn("at its next start", rep["output_why"])
        self.assertEqual(rep["connector"], "HDMI-A-1", "the room is not dark")

    def test_the_decision_is_made_at_the_players_start_only_so_it_never_flaps(self):
        before = decide(PI4, help_text((0, "HDMI-A-1", HDMI_HELP)), {"output": "HDMI-A-2"})
        layout = [(1, "HDMI-A-1", "connected", HDMI_MODES), (1, "HDMI-A-2", "connected", ["1280x720"])]
        after = decide(layout, help_text((0, "HDMI-A-1", HDMI_HELP), (0, "HDMI-A-2", [("1280x720", 1280, 720, 60.0)])), {"output": "HDMI-A-2"})
        self.assertEqual((before["connector"], after["connector"]), ("HDMI-A-1", "HDMI-A-2"))
        # the panel's watch (Screen.tick) never reads the saved output: only decide(), at a start of the player, does
        import inspect
        self.assertNotIn("saved_output", inspect.getsource(S.Screen.tick))
        self.assertNotIn("output", inspect.getsource(S.Screen.watch))

    def test_the_av_output_gets_its_own_interlaced_mode_by_the_tv_standard(self):
        rep = decide([(1, "Composite-1", "connected", ["720x480i", "720x576i"])], help_text((0, "Composite-1", COMP_HELP)))
        self.assertEqual(args_of(rep), ["--drm-device=/dev/dri/card1", "--drm-connector=Composite-1", "--drm-mode=720x480@59.94"])
        self.assertEqual((rep["used"], rep["own"], rep["automatic"], rep["why"]), ("720x480@59.94", "720x480@59.94", "720x480@59.94", ""))
        rep = decide([(1, "Composite-1", "connected", ["720x576i"])], help_text((0, "Composite-1", [COMP_HELP[1]])))
        self.assertEqual(args_of(rep)[-1], "--drm-mode=720x576@50.00")

    def test_a_mode_chosen_for_hdmi_does_not_disturb_the_av_output(self):
        rep = decide([(1, "Composite-1", "connected", ["720x480i"])], help_text((0, "Composite-1", [COMP_HELP[0]])), {"mode": "1280x720@60.00"})
        self.assertEqual((args_of(rep)[-1], rep["why"]), ("--drm-mode=720x480@59.94", ""))

    def test_choosing_the_av_output_on_each_layout(self):
        pi4 = decide(PI4, help_text((0, "HDMI-A-1", HDMI_HELP), (0, "Composite-1", COMP_HELP)), {"output": "Composite-1"})
        self.assertEqual(args_of(pi4), ["--drm-device=/dev/dri/card1", "--drm-connector=Composite-1", "--drm-mode=720x480@59.94"])
        pi3 = decide(PI3, help_text((0, "HDMI-A-1", HDMI_HELP), (0, "Composite-1", [COMP_HELP[0]])), {"output": "Composite-1"})
        self.assertEqual(args_of(pi3), ["--drm-device=/dev/dri/card0", "--drm-connector=Composite-1", "--drm-mode=720x480@59.94"])
        pi5 = decide(PI5, help_text((0, "HDMI-A-1", HDMI_HELP), (0, "HDMI-A-2", [HDMI_HELP[1]]), (1, "Composite-1", [COMP_HELP[1]])),
                     {"output": "Composite-1"})
        self.assertEqual(args_of(pi5), ["--drm-device=/dev/dri/card2", "--drm-connector=Composite-1", "--drm-mode=720x576@50.00"])
        self.assertEqual(pi5["card"], 2)

    def test_a_pi_5_with_outputs_on_two_cards_automatic_is_hdmi_on_its_own_card(self):
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "HDMI-A-2", [HDMI_HELP[1]]), (1, "Composite-1", [COMP_HELP[1]]))
        self.assertEqual(args_of(decide(PI5, text)), ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"])

    def test_a_desktop(self):
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "eDP-1", [("1366x768", 1366, 768, 60.0)]))
        self.assertEqual(args_of(decide(DESKTOP, text))[1], "--drm-connector=HDMI-A-1")
        rep = decide(DESKTOP, text, {"output": "eDP-1"})
        self.assertEqual(args_of(rep), ["--drm-device=/dev/dri/card0", "--drm-connector=eDP-1", "--drm-mode=1366x768@60.00"])
        self.assertIn("DisplayPort 1 is not connected", decide(DESKTOP, text, {"output": "DP-1"})["output_why"])

    def test_a_named_output_with_no_card_known_still_passes_its_name(self):
        layout = [{"connector": "HDMI-A-2", "status": "connected", "modes": []}, {"connector": "HDMI-A-1", "status": "connected", "modes": []}]
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "HDMI-A-2", HDMI_HELP))
        rep = S.decide(S.clean_wanted({"output": "HDMI-A-2"}), S.parse_help(text), "", layout, BOOT)
        self.assertEqual(args_of(rep), ["--drm-connector=HDMI-A-2", "--drm-mode=1920x1080@60.00"])
        self.assertTrue(S.valid_args(args_of(rep)))

    def test_what_the_wanted_file_may_hold_for_the_output(self):
        for good in ("HDMI-A-2", "Composite-1", "DSI-1", "DP-3", "eDP-1"):
            self.assertEqual(S.clean_wanted({"output": good})["output"], good)
        for bad in ("auto", "", "x;reboot", "../x", "Writeback-1", "writeback-9", 5, None, ["HDMI-A-1"], "a" * 40, "HDMI A 1"):
            self.assertNotIn("output", S.clean_wanted({"output": bad}), repr(bad))
        rep = S.clean_report(dict(decide(PI4, help_text((0, "HDMI-A-1", HDMI_HELP)), {"output": "HDMI-A-2"}), pid=1))
        self.assertEqual((rep["output"], rep["connector"]), ("HDMI-A-2", "HDMI-A-1"))
        self.assertIn("not connected", rep["output_why"])
        self.assertEqual(S.clean_report({"output": "x;y", "output_why": "<b>x" + "y" * 500})["output"], "auto")
        self.assertLessEqual(len(S.clean_report({"output_why": "y" * 500})["output_why"]), 300)


class PrepareTest(unittest.TestCase):
    def test_the_report_and_the_log_say_why_when_the_chosen_output_is_not_connected(self):
        state, run = tempfile.mkdtemp(), tempfile.mkdtemp()
        S.write_json(os.path.join(state, S.WANTED), {"mode": "auto", "output": "HDMI-A-2"})
        lines = []
        out = S.prepare(["--vo=gpu", "--gpu-context=drm"], run, "mpv", env={"PVJ_STATE_DIR": state},
                        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=help_text((0, "HDMI-A-1", HDMI_HELP)), stderr=""),
                        sysfs=sysfs_of(PI4), boot=BOOT, log=lines.append)
        self.assertEqual(out[1], "--drm-connector=HDMI-A-1")
        self.assertTrue(any("HDMI 2 is not connected" in l for l in lines), lines)
        with open(os.path.join(run, S.REPORT)) as f:
            self.assertEqual(json.load(f)["output"], "HDMI-A-2")


class LateScreenAgreementTest(unittest.TestCase):
    """Composite, the late-screen watcher (#201) and pvj-render's exit-2 words: one notion of a screen."""

    def test_the_watcher_counts_the_av_output_as_a_screen_exactly_when_the_decision_does(self):
        sysfs = sysfs_of(PI4)
        self.assertEqual(latescreen.connected_screens(sysfs), ["Composite-1", "HDMI-A-1"], "writeback and the unplugged HDMI are not")
        self.assertFalse(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", sysfs), "a screen is there: not a late screen")
        only_av = sysfs_of([(1, "HDMI-A-1", "disconnected", []), (1, "Composite-1", "connected", ["720x480i"])])
        self.assertEqual(latescreen.connected_screens(only_av), ["Composite-1"])
        self.assertFalse(latescreen.no_screen_fallback("pvj-render ended twice in a row (exit 2)", only_av))
        none = sysfs_of([(1, "HDMI-A-1", "disconnected", []), (1, "Composite-1", "disconnected", [])])
        self.assertTrue(latescreen.no_screen_fallback("pvj-render did not start (exit 2)", none))
        self.assertFalse(latescreen.no_screen_fallback("pvj-render did not start (exit 3)", none), "any other end is a crash")
        # a projector that says connected before it has modes is not a screen yet, for the watcher and for the decision alike
        waking = sysfs_of([(1, "HDMI-A-1", "connected", []), (1, "Composite-1", "disconnected", [])])
        self.assertEqual(latescreen.connected_screens(waking), [])

    def test_the_decision_and_pvj_renders_own_pick_agree_on_a_named_connector_and_an_exact_interlaced_mode(self):
        # pvj-render reads the same arguments (render/src/out.c: pick_connector takes the connector by its whole name, pick_mode
        # takes WxH@R to 0.01 Hz with the rate doubled for an interlaced mode): the text passed is mpv's own, 59.94 for 720x480i
        rep = decide([(1, "Composite-1", "connected", ["720x480i"])], help_text((0, "Composite-1", [COMP_HELP[0]])))
        self.assertIsNotNone(S.MODE_ARG.fullmatch(S.screen_args(rep)[-1]))
        self.assertEqual(S.screen_args(rep)[-1], "--drm-mode=720x480@59.94")
        with open(os.path.join(os.path.dirname(S.__file__), "..", "render", "src", "out.c")) as f:
            src = f.read()
        self.assertIn("if (m->flags & DRM_MODE_FLAG_INTERLACE) r *= 2.0;", src)
        self.assertIn("pick_connector(drm_fd, res, cfg->drm_connector)", src)

    def test_a_failed_named_output_is_barred_with_its_own_arguments_only(self):
        text = help_text((0, "HDMI-A-1", HDMI_HELP), (0, "Composite-1", COMP_HELP))
        rep = decide(PI4, text, {"output": "Composite-1"})
        skip = S.with_skip({}, BOOT, rep["used"], S.screen_args(rep))
        wanted = S.clean_wanted({"output": "Composite-1", "skip": skip})
        self.assertEqual(len(wanted["skip"]), 1)
        again = S.decide(wanted, S.parse_help(text), "", sysfs_of(PI4), BOOT)
        self.assertEqual(S.screen_args(again), [], "the barred arguments are not given again: the player takes its own screen")
        self.assertIn("did not start on this screen", again["why"])
        hdmi = S.decide(S.clean_wanted({"skip": skip}), S.parse_help(help_text((0, "HDMI-A-1", HDMI_HELP))), "", sysfs_of(PI4), BOOT)
        self.assertEqual(S.screen_args(hdmi)[1], "--drm-connector=HDMI-A-1")


class ScreenSettingTest(unittest.TestCase):
    """Screen.set_output and what the other steps keep of it."""

    def make(self, layout=PI4):
        from pvj.settings import Settings
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(os.path.join(self.dir, "settings.json"))
        self.settings.load()
        self.restarts = []

        class Player:
            ipc = None
            quit = staticmethod(lambda: self.restarts.append(1))
        api = mock.Mock(player=Player)
        self.root = tree(layout)
        self.screen = S.Screen(api, self.settings, self.dir, self.dir, log=lambda *_: None, boot=lambda: BOOT,
                               connectors=lambda: hardware.drm_connectors(self.root))
        return self.screen

    def test_automatic_is_the_default_and_a_name_is_saved_and_written_for_the_player(self):
        s = self.make()
        self.assertEqual(s.saved_output(), "auto")
        out = s.set_output("HDMI-A-2")
        self.assertEqual(s.saved_output(), "HDMI-A-2")
        self.assertEqual(self.settings.data["screen"], {"mode": "auto", "output": "HDMI-A-2"})
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "auto", "output": "HDMI-A-2"})
        self.assertEqual(self.restarts, [1], "the player restarts once to read it")
        self.assertEqual([r["connector"] for r in out["outputs"] if r["saved"]], ["HDMI-A-2"])
        s.set_output("HDMI-A-2")
        self.assertEqual(self.restarts, [1], "the same again restarts nothing")
        s.set_output("auto")
        self.assertEqual(self.settings.data["screen"], {"mode": "auto"})
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "auto"})
        self.assertEqual(len(self.restarts), 2)

    def test_what_is_refused(self):
        s = self.make()
        for bad in ("x;reboot", "", 5, None, "Writeback-1", "a" * 40):
            with self.assertRaises(S.ScreenError) as e:
                s.set_output(bad)
            self.assertEqual(e.exception.status, 400, repr(bad))
        with self.assertRaises(S.ScreenError) as e:
            s.set_output("HDMI-A-9")
        self.assertEqual(e.exception.status, 409)
        s = self.make(PI3[:1])
        with self.assertRaises(S.ScreenError) as e:
            s.set_output("Composite-1")
        self.assertEqual(e.exception.status, 409)
        self.assertIn("AV output is off on this node", e.exception.message)
        self.assertEqual(self.restarts, [])
        self.assertNotIn("output", self.settings.data["screen"])

    def test_an_output_that_is_listed_but_not_connected_may_be_saved(self):
        s = self.make()
        s.set_output("HDMI-A-2")                      # a projector not switched on yet
        self.assertEqual(s.saved_output(), "HDMI-A-2")

    def test_a_try_in_progress_holds_the_output_back(self):
        s = self.make()
        s.pending = {"mode": "1280x720@60.00", "previous": "auto", "since": 0, "until": None, "pid_before": None}
        with self.assertRaises(S.ScreenError) as e:
            s.set_output("HDMI-A-2")
        self.assertEqual(e.exception.status, 409)

    def test_the_output_survives_a_mode_try_keep_and_going_back(self):
        s = self.make()
        s.set_output("HDMI-A-2")
        with mock.patch.object(s, "report", return_value={"modes": [{"text": "1280x720@60.00", "width": 1280, "height": 720, "hz": 60.0, "index": 1, "interlaced": False}]}):
            s.choose("1280x720@60.00")
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "auto", "output": "HDMI-A-2", "trial": {"mode": "1280x720@60.00", "boot": BOOT}})
        s.pending["until"] = 1e9
        s.confirm()
        self.assertEqual(self.settings.data["screen"], {"mode": "1280x720@60.00", "output": "HDMI-A-2"})
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "1280x720@60.00", "output": "HDMI-A-2"})
        with mock.patch.object(s, "report", return_value={"modes": [{"text": "1024x768@60.00", "width": 1024, "height": 768, "hz": 60.0, "index": 2, "interlaced": False}]}):
            s.choose("1024x768@60.00")
        s.revert()
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "1280x720@60.00", "output": "HDMI-A-2"})

    def test_start_and_sync_keep_wanted_in_step_with_the_settings(self):
        s = self.make()
        s.set_output("HDMI-A-2")
        os.unlink(s.wanted_file)
        s.start()
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "auto", "output": "HDMI-A-2"})
        n = len(self.restarts)
        self.settings.data["screen"] = {"mode": "auto"}           # a factory reset puts the settings back
        s.sync()
        self.assertEqual(S.read_json(s.wanted_file), {"mode": "auto"})
        self.assertEqual(len(self.restarts), n + 1)

    def test_the_list_on_each_layout(self):
        for layout, names, connected in ((PI4, ["Composite-1", "HDMI-A-1", "HDMI-A-2"], ["Composite-1", "HDMI-A-1"]),
                                         (PI3, ["Composite-1", "HDMI-A-1"], ["Composite-1", "HDMI-A-1"]),
                                         (PI5, ["HDMI-A-1", "HDMI-A-2", "Composite-1"], ["HDMI-A-1", "HDMI-A-2", "Composite-1"]),
                                         (DESKTOP, ["DP-1", "HDMI-A-1", "eDP-1"], ["HDMI-A-1", "eDP-1"])):
            out = self.make(layout).outputs()
            self.assertEqual(sorted(r["connector"] for r in out["outputs"]), sorted(names))
            self.assertEqual(sorted(r["connector"] for r in out["outputs"] if r["connected"]), sorted(connected))
            for r in out["outputs"]:
                self.assertEqual(set(r), {"connector", "label", "kind", "connected", "modes", "card", "saved", "using"})
        row = {r["connector"]: r for r in self.make(PI4).outputs()["outputs"]}
        self.assertEqual((row["Composite-1"]["kind"], row["Composite-1"]["label"], row["Composite-1"]["modes"]),
                         ("composite", "AV (composite)", ["720x480i", "720x576i"]))
        self.assertEqual(row["HDMI-A-1"]["card"], 1)

    def test_a_saved_output_that_the_board_does_not_list_is_said(self):
        s = self.make(PI3[:1])
        self.settings.data["screen"] = {"mode": "auto", "output": "Composite-1"}
        out = s.outputs()
        self.assertEqual(out["saved"], "Composite-1")
        self.assertIn("is not listed by this node now", out["why"])


class BootConfigTest(unittest.TestCase):
    def test_on_a_pi_4_config_the_overlay_gets_composite_and_a_marked_block(self):
        new = bootcfg.edit_config(PI4_CONFIG, True)
        self.assertIn("\ndtoverlay=vc4-kms-v3d,composite\n", new)
        self.assertTrue(new.rstrip().endswith(bootcfg.END))
        self.assertIn("[all]\nenable_tvout=1", new)
        self.assertTrue(bootcfg.config_enabled(new) and bootcfg.tvout_on(new) and bootcfg.has_kms(new))
        self.assertEqual(new.count("composite"), 1)
        self.assertEqual(bootcfg.edit_config(new, True), new, "again changes nothing")
        self.assertEqual(bootcfg.edit_config(new, False), PI4_CONFIG, "off puts the file back exactly")

    def test_other_parameters_and_comments_and_sections_are_kept(self):
        new = bootcfg.edit_config(PI3_CONFIG, True)
        self.assertIn("dtoverlay=vc4-kms-v3d,cma-256,composite # the driver", new)
        self.assertIn("dtoverlay=vc4-kms-v3d-pi4,noaudio,composite", new)
        self.assertEqual(bootcfg.edit_config(new, False), PI3_CONFIG)
        mine = "dtoverlay=vc4-kms-v3d,composite=1,noaudio\n"
        self.assertEqual(bootcfg.edit_config(mine, True).splitlines()[0], "dtoverlay=vc4-kms-v3d,noaudio,composite")
        self.assertEqual(bootcfg.edit_config(mine, False), "dtoverlay=vc4-kms-v3d,noaudio\n")

    def test_a_commented_line_and_the_legacy_driver_are_not_touched_and_a_node_without_kms_is_refused(self):
        text = "#dtoverlay=vc4-kms-v3d\ndtoverlay=vc4-fkms-v3d\n"
        with self.assertRaises(bootcfg.BootError) as e:
            bootcfg.edit_config(text, True)
        self.assertIn("full KMS driver", str(e.exception))
        self.assertFalse(bootcfg.has_kms(text))
        self.assertEqual(bootcfg.edit_config(text + "dtoverlay=vc4-kms-v3d\n", False).splitlines()[:2], text.splitlines())

    def test_a_half_block_is_refused(self):
        with self.assertRaises(bootcfg.BootError):
            bootcfg.edit_config("dtoverlay=vc4-kms-v3d\n" + bootcfg.BEGIN + "\n[all]\n", True)

    def test_cmdline(self):
        new = bootcfg.edit_cmdline(CMDLINE, "PAL")
        self.assertEqual(new, CMDLINE.rstrip("\n") + " vc4.tv_norm=PAL\n")
        self.assertEqual(bootcfg.cmdline_norm(new), "PAL")
        self.assertEqual(bootcfg.edit_cmdline(new, "NTSC").count("vc4.tv_norm"), 1)
        self.assertEqual(bootcfg.cmdline_norm(bootcfg.edit_cmdline(new, "NTSC")), "NTSC")
        self.assertEqual(bootcfg.edit_cmdline(new, None), CMDLINE)
        for bad in ("a\nb\n", "\n", ""):
            with self.assertRaises(bootcfg.BootError):
                bootcfg.edit_cmdline(bad, "PAL")

    def files(self, config=PI4_CONFIG, cmdline=CMDLINE):
        return tree([], {"/boot/firmware/config.txt": config, "/boot/firmware/cmdline.txt": cmdline})

    def read(self, root, name):
        with open(os.path.join(root, "boot/firmware", name)) as f:
            return f.read()

    def test_apply_on_and_off_with_backups(self):
        root = self.files()
        clock = iter(range(1000, 2000))
        res = bootcfg.apply(True, "NTSC", root=root, now=lambda: next(clock))
        self.assertTrue(res["changed"])
        self.assertEqual(sorted(res["backups"]), ["cmdline.txt.nxlx-1001", "config.txt.nxlx-1000"])
        self.assertTrue(res["state"]["composite"] and res["state"]["tvout"] and res["state"]["kms"])
        self.assertEqual(res["state"]["norm"], "NTSC")
        self.assertEqual(self.read(root, "config.txt.nxlx-original"), PI4_CONFIG)
        self.assertEqual(self.read(root, "cmdline.txt.nxlx-original"), CMDLINE)
        self.assertEqual(self.read(root, "config.txt.nxlx-1000"), PI4_CONFIG)
        again = bootcfg.apply(True, "NTSC", root=root, now=lambda: next(clock))
        self.assertEqual((again["changed"], again["backups"]), (False, []), "nothing to change: nothing written, no backup")
        pal = bootcfg.apply(True, "PAL", root=root, now=lambda: next(clock))
        self.assertEqual(pal["state"]["norm"], "PAL")
        self.assertEqual(len(pal["backups"]), 1, "only the cmdline changed")
        off = bootcfg.apply(False, root=root, now=lambda: next(clock))
        self.assertEqual((self.read(root, "config.txt"), self.read(root, "cmdline.txt")), (PI4_CONFIG, CMDLINE))
        self.assertFalse(off["state"]["composite"])
        self.assertEqual(self.read(root, "config.txt.nxlx-original"), PI4_CONFIG, "the first original is never replaced")

    def test_only_the_newest_backups_are_kept_and_the_original_stays(self):
        root = self.files()
        t = [1000]

        def now():
            t[0] += 1
            return t[0]
        for i in range(6):
            bootcfg.apply(True, "PAL" if i % 2 else "NTSC", root=root, now=now)
        names = sorted(n for n in os.listdir(os.path.join(root, "boot/firmware")) if n.startswith("cmdline.txt.nxlx-"))
        self.assertEqual(len(names), bootcfg.KEEP + 1, names)
        self.assertIn("cmdline.txt.nxlx-original", names)

    def test_nothing_is_written_when_the_config_is_refused(self):
        root = self.files(config="dtoverlay=vc4-fkms-v3d\n")
        before = sorted(os.listdir(os.path.join(root, "boot/firmware")))
        with self.assertRaises(bootcfg.BootError):
            bootcfg.apply(True, "NTSC", root=root)
        self.assertEqual(sorted(os.listdir(os.path.join(root, "boot/firmware"))), before)
        self.assertEqual(self.read(root, "cmdline.txt"), CMDLINE)

    def test_a_second_file_that_cannot_be_written_puts_the_first_back(self):
        root = self.files()
        real = bootcfg._replace

        def flaky(path, text):
            if path.endswith("cmdline.txt"):
                raise OSError(30, "Read-only file system")
            return real(path, text)
        with mock.patch.object(bootcfg, "_replace", flaky), self.assertRaises(bootcfg.BootError) as e:
            bootcfg.apply(True, "NTSC", root=root)
        self.assertIn("nothing was left changed", str(e.exception))
        self.assertEqual(self.read(root, "config.txt"), PI4_CONFIG)

    def test_no_boot_files_and_a_bad_standard(self):
        with self.assertRaises(bootcfg.BootError):
            bootcfg.apply(True, "NTSC", root=tree([]))
        with self.assertRaises(bootcfg.BootError):
            bootcfg.apply(True, "NTSC-X", root=self.files())
        self.assertEqual(bootcfg.state(tree([]))["found"], False)
        self.assertEqual(bootcfg.state(self.files())["composite"], False)

    def test_the_older_boot_folder_is_found_too(self):
        root = tree([], {"/boot/config.txt": PI4_CONFIG, "/boot/cmdline.txt": CMDLINE})
        self.assertTrue(bootcfg.apply(True, "NTSC", root=root)["changed"])
        with open(os.path.join(root, "boot/config.txt")) as f:
            self.assertIn("composite", f.read())

    def test_the_norms_are_the_kernels_list(self):
        self.assertEqual(bootcfg.NORMS, ("NTSC", "NTSC-J", "NTSC-443", "PAL", "PAL-M", "PAL-N", "PAL60", "SECAM"))
        self.assertEqual(bootcfg.DEFAULT_NORM, "NTSC")


class HelperCommandTest(unittest.TestCase):
    def make(self):
        root = tree([], {"/boot/firmware/config.txt": PI4_CONFIG, "/boot/firmware/cmdline.txt": CMDLINE})
        lines = []
        s = sysd.SysService(runner=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""), log=lines.append, now=lambda: 5000)
        s.boot_root = root
        return s, root, lines

    def test_on_and_off_through_the_command(self):
        s, root, lines = self.make()
        r = s.handle({"cmd": "av_output", "enable": True, "norm": "PAL"})
        self.assertEqual((r["ok"], r["changed"], r["restart_needed"], r["enabled"], r["norm"]), (True, True, True, True, "PAL"))
        self.assertTrue(r["backups"])
        self.assertTrue(any("AV output on (PAL)" in l for l in lines), lines)
        r = s.handle({"cmd": "av_output", "enable": False})
        self.assertEqual((r["ok"], r["enabled"]), (True, False))

    def test_a_default_standard_is_ntsc(self):
        s, root, _ = self.make()
        self.assertEqual(s.handle({"cmd": "av_output", "enable": True})["norm"], "NTSC")

    def test_what_is_refused(self):
        s, root, _ = self.make()
        for msg in ({"cmd": "av_output"}, {"cmd": "av_output", "enable": "yes"}, {"cmd": "av_output", "enable": 1},
                    {"cmd": "av_output", "enable": True, "norm": "NTSC;reboot"}, {"cmd": "av_output", "enable": True, "norm": 5}):
            self.assertFalse(s.handle(msg)["ok"], msg)
        with open(os.path.join(root, "boot/firmware/config.txt")) as f:
            self.assertEqual(f.read(), PI4_CONFIG)
        broken = sysd.SysService(runner=lambda *a, **k: None, log=lambda *_: None)
        broken.boot_root = tree([])
        r = broken.handle({"cmd": "av_output", "enable": True})
        self.assertFalse(r["ok"])
        self.assertIn("no boot configuration", r["error"])

    def test_the_unit_may_write_the_boot_folder_and_nothing_more_of_the_system(self):
        with open(os.path.join(os.path.dirname(sysd.__file__), "..", "install", "pvj-sysd.service")) as f:
            unit = f.read()
        self.assertIn("ReadWritePaths=/run/pvj-sysd -/boot/firmware -/boot", unit)
        self.assertIn("ProtectSystem=strict", unit)


class FakeSysd:
    def __init__(self, ok=True, changed=True):
        self.asked, self.ok, self.changed = [], ok, changed

    def request(self, message):
        self.asked.append(message)
        if not self.ok:
            return {"ok": False, "error": "the boot files could not be written"}
        return {"ok": True, "changed": self.changed, "backups": ["config.txt.nxlx-1"], "restart_needed": self.changed}


class ApiTest(CareBase):
    def setUp(self):
        super().setUp()
        self.root = tree(PI4)
        self.api.screen._connectors = lambda: hardware.drm_connectors(self.root)
        self.api.board = dict(self.api.board, kind="pi4", model="Raspberry Pi 4 Model B Rev 1.4")
        self.boot = {"found": True, "kms": True, "composite": False, "tvout": False, "norm": None}
        patches = [mock.patch("pvj.hardware.outputs", lambda root="/": [c for c in hardware.drm_connectors(self.root)
                                                                           if not c["connector"].lower().startswith("writeback")]),
                   mock.patch("pvj.bootcfg.state", lambda root="/": dict(self.boot))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.restarted = []
        self.api.player.quit = lambda: self.restarted.append(1)

    def get(self, dev=None):
        st, out = self.h("GET", "/api/output", {}, dev or self.full_dev)
        self.assertEqual(st, 200, out)
        return out

    def test_only_the_owner_reads_or_sets_it(self):
        for dev in (self.view_dev, self.live_dev):
            self.assertEqual(self.h("GET", "/api/output", {}, dev)[0], 403)
            self.assertEqual(self.h("POST", "/api/output", {"output": "auto"}, dev)[0], 403)
            self.assertEqual(self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/output", {}, None)[0], 401)
        self.assertEqual(self.call("GET", "/api/output", token=self.full)[0], 200)

    def test_never_through_the_support_tunnel(self):
        from pvj import support
        for route in (("POST", "/api/output"), ("POST", "/api/output/av")):
            self.assertIn(route, support.REMOTE_DENY)
        self.assertNotIn(("GET", "/api/output"), support.REMOTE_DENY, "a helper may read the list")

    def test_the_shape_of_the_read(self):
        out = self.get()
        self.assertEqual(set(out), {"saved", "using", "why", "note", "outputs", "av"})
        self.assertEqual(out["saved"], "auto")
        self.assertEqual(sorted(r["connector"] for r in out["outputs"]), ["Composite-1", "HDMI-A-1", "HDMI-A-2"])
        av = out["av"]
        self.assertEqual(set(av), {"supported", "why", "enabled", "active", "restart_needed", "norm", "norms", "default_norm", "hdmi_off", "costs", "helper"})
        self.assertEqual((av["supported"], av["enabled"], av["active"], av["restart_needed"], av["norm"], av["default_norm"]),
                         (True, False, True, True, "NTSC", "NTSC"), "listed by the kernel but not in the boot files: a restart is pending")
        self.assertTrue(av["hdmi_off"])
        self.assertIn("HDMI outputs off", av["costs"][0])
        self.assertEqual(av["norms"], list(bootcfg.NORMS))

    def test_choosing_an_output(self):
        st, out = self.h("POST", "/api/output", {"output": "HDMI-A-2"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["saved"], "HDMI-A-2")
        self.assertEqual(self.restarted, [1])
        self.assertEqual(self.h("POST", "/api/output", {"output": "HDMI-A-9"}, self.full_dev)[0], 409)
        self.assertEqual(self.h("POST", "/api/output", {"output": "x;y"}, self.full_dev)[0], 400)
        self.assertEqual(self.h("POST", "/api/output", {}, self.full_dev)[0], 400)
        self.assertEqual(self.h("POST", "/api/output", {"output": "auto", "extra": 1}, self.full_dev)[0], 400)
        self.assertEqual(self.h("POST", "/api/output", {"output": "auto"}, self.full_dev)[1]["saved"], "auto")

    def test_the_av_switch_needs_the_confirm_and_a_known_standard(self):
        helper = FakeSysd()
        self.api.sysd = helper
        for body in ({"enable": True}, {"enable": "yes", "confirm": "av"}, {"confirm": "av"}, {"enable": True, "confirm": "yes"},
                     {"enable": True, "confirm": "av", "norm": "NTSC;x"}, {"enable": True, "confirm": "av", "extra": 1}):
            self.assertEqual(self.h("POST", "/api/output/av", body, self.full_dev)[0], 400, body)
        self.assertEqual(helper.asked, [])
        st, out = self.h("POST", "/api/output/av", {"enable": True, "norm": "PAL", "confirm": "av"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(helper.asked, [{"cmd": "av_output", "enable": True, "norm": "PAL"}])
        self.assertTrue(out["changed"] and out["backups"])
        self.assertIn("restart of the node is needed", out["note"])
        self.assertTrue(out["av"]["restart_needed"])

    def test_the_default_standard_is_ntsc_and_on_again_keeps_the_one_that_is_set(self):
        helper = FakeSysd()
        self.api.sysd = helper
        self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)
        self.assertEqual(helper.asked[0]["norm"], "NTSC")
        self.boot.update(composite=True, norm="PAL")
        self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)
        self.assertEqual(helper.asked[1]["norm"], "PAL")

    def test_where_the_av_output_cannot_be_switched_it_says_why_and_asks_the_helper_nothing(self):
        helper = FakeSysd()
        self.api.sysd = helper
        for kind, found, kms, word in (("x86", True, True, "not a Raspberry Pi"), ("pi4", False, False, "was not found"),
                                       ("pi4", True, False, "full KMS driver")):
            self.api.board = dict(self.api.board, kind=kind)
            self.boot.update(found=found, kms=kms)
            av = self.get()["av"]
            self.assertFalse(av["supported"])
            self.assertIn(word, av["why"])
            self.assertEqual(self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)[0], 409)
        self.assertEqual(helper.asked, [])

    def test_a_helper_that_refuses_or_is_missing(self):
        self.api.sysd = FakeSysd(ok=False)
        st, out = self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)
        self.assertEqual(st, 409)
        self.assertIn("could not be written", out["error"])
        self.api.sysd = None
        self.assertEqual(self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)[0], 503)

    def test_the_costs_by_board(self):
        for kind, hdmi_off, pad in (("pi3", False, False), ("pi4", True, False), ("pi5", True, True)):
            self.api.board = dict(self.api.board, kind=kind)
            av = self.get()["av"]
            self.assertEqual(av["hdmi_off"], hdmi_off, kind)
            self.assertEqual(any("soldered" in c for c in av["costs"]), pad, kind)
            self.assertEqual(any("HDMI stays on" in c for c in av["costs"]), not hdmi_off, kind)
            self.assertTrue(any("restart of the node" in c for c in av["costs"]))

    def test_after_the_change_a_restart_is_pending_until_the_node_has_restarted(self):
        self.api.sysd = FakeSysd()
        self.root = tree([(1, "HDMI-A-1", "connected", HDMI_MODES)])          # composite not listed
        self.assertFalse(self.get()["av"]["restart_needed"])
        self.h("POST", "/api/output/av", {"enable": True, "confirm": "av"}, self.full_dev)
        self.boot.update(composite=True)
        self.assertTrue(self.get()["av"]["restart_needed"], "in the boot files, not yet listed by the kernel")
        self.root = tree(PI4)                                                  # the node restarted: the kernel lists it
        self.api._av_boot = "an earlier boot"
        self.assertFalse(self.get()["av"]["restart_needed"])

    def test_the_screen_reply_carries_the_output_too(self):
        self.h("POST", "/api/output", {"output": "HDMI-A-2"}, self.full_dev)
        st = self.h("GET", "/api/screen", {}, self.full_dev)[1]
        self.assertEqual(st["output"]["saved"], "HDMI-A-2")


if __name__ == "__main__":
    unittest.main()
