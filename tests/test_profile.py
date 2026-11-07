# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The profile table (pvj/profile.py) gives every board kind the values master gave it.

tests/frozen_master_profile.json was written once by running master's own functions (before the table existed) for
every kind and every setting below; nothing in it is computed by the code under test. If a value here has to change,
that is a decision to record, not a number to refresh. The two unknown kinds are the one change (D122): they take the lean
renderer values (below), and master's values for everything else."""
import itertools
import json
import os
import unittest

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import effects as E, hardware, probe as P, profile, renderer as R, shaders as S

KINDS = ("pi3", "pi4", "pi5", "x86", "pi-other", "arm-other")
SCREEN = ["--vo=gpu", "--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"]
ENV = {"PVJ_RENDER_BIN": "BIN"}
ROWS = [{"source": 1, "height": 1080, "idle": False}, {"source": 2, "height": 720, "idle": False}, {"source": 3, "height": 720, "idle": False}]
INFOS = {"h264_4k30": {"codec": "h264", "width": 3840, "height": 2160, "fps": 30}, "h264_1080p60": {"codec": "h264", "width": 1920, "height": 1080, "fps": 60},
         "h264_1080p24": {"codec": "h264", "width": 1920, "height": 1080, "fps": 24}, "h264_720p": {"codec": "h264", "width": 1280, "height": 720, "fps": 30},
         "hevc_4k": {"codec": "hevc", "width": 3840, "height": 2160, "fps": 30}, "prores": {"codec": "prores", "width": 1920, "height": 1080, "fps": 24}}

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "frozen_master_profile.json")) as f:
    FROZEN = json.load(f)


def measure(k):
    """The same questions the frozen file answers, asked of the code as it is now."""
    o = {}
    o["sources"] = {str(c): R.sources_for(k, c) for c in (None, 1, 2, 3, 4, 5, 8)}
    o["cap"] = R.source_cap(k)
    o["detail_arg"] = {str(d): R.detail_arg(d, k) for d in ("auto", 540, 720, "output")}
    o["final_arg"] = {str(fin): R.final_arg(fin, k) for fin in ("auto", "full", 720, None)}
    cmds = {}
    for det, src, fin, deb in itertools.product(("auto", 720, "output"), (None, 3), ("auto", "full", 720), (False, True)):
        cmds["%s|%s|%s|%s" % (det, src, fin, deb)] = R.command("/run/s", SCREEN, det, k, ENV, src, fin, deb)
    o["command"] = cmds
    o["warning"] = [R.source_warning(ROWS, 1, k), R.source_warning(ROWS[1:], 2, k), R.source_warning([dict(ROWS[0], height=720)] + ROWS[1:], 1, k)]
    o["default_detail"] = E.default_detail(k)
    o["auto_lines"] = {"%s|%s" % (sid, b): E.auto_lines(k, sid, b) for sid in (None, "fx-wash.fs", "fx-edge-glow.fs", "isf-edge-blowout.fs") for b in (True, False)}
    o["heights"] = list(S.heights_for(k))
    o["default_height"] = S.default_height(k)
    o["playback"] = hardware.playback_profile({"kind": k}, False)
    o["advice"] = {n: P.advice(i, k) for n, i in INFOS.items()}
    return json.loads(json.dumps(o))


def with_fx(argv, rate):
    """Master's argv with --fx-fps=N after --sources=N, where the full tier now puts it."""
    i = next(i for i, a in enumerate(argv) if a.startswith("--sources="))
    return argv[:i + 1] + ["--fx-fps=%d" % rate] + argv[i + 1:]


class FrozenValuesTest(unittest.TestCase):
    def test_every_kind_has_the_values_master_gave_it(self):
        for kind in ("pi3", "pi4", "pi5", "x86"):
            got, want = measure(kind), FROZEN[kind]
            if kind in ("pi5", "x86"):      # the one addition: the full tier passes the screen's rate (60 in the 1920x1080@60.00 these use)
                want = dict(want, command={k: with_fx(v, 60) for k, v in want["command"].items()})
            for key in want:
                self.assertEqual(got[key], want[key], (kind, key))

    def test_the_frozen_file_is_master_not_the_new_code(self):
        # a few values by hand, from master's source, so a regenerated fixture cannot hide a change
        self.assertEqual(FROZEN["pi4"]["command"]["auto|None|auto|False"],
                         ["BIN", "--socket=/run/s", "--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00",
                          "--detail=540", "--fast", "--skip-deblock", "--sources=4", "--final=auto"])
        self.assertEqual((FROZEN["pi3"]["cap"], FROZEN["pi4"]["cap"], FROZEN["pi5"]["cap"], FROZEN["x86"]["cap"]), (2, 4, 8, 8))
        self.assertEqual((FROZEN["pi5"]["default_height"], FROZEN["pi4"]["default_height"]), (720, 540))
        self.assertEqual(FROZEN["pi5"]["heights"], [360, 540, 720, 1080])
        self.assertEqual(FROZEN["x86"]["command"]["auto|None|auto|False"][5:], ["--detail=output", "--sources=4"])

    LEAN_KEYS = ("sources", "cap", "detail_arg", "final_arg", "command", "warning")      # D122: what unknown boards now take from pi3

    def test_unknown_kinds_take_the_lean_values_for_the_renderer_and_master_s_for_the_rest(self):
        for kind in ("pi-other", "arm-other"):
            got = measure(kind)
            for key in self.LEAN_KEYS:
                self.assertEqual(got[key], FROZEN["pi3"][key], (kind, key))
            for key in set(FROZEN[kind]) - set(self.LEAN_KEYS):
                self.assertEqual(got[key], FROZEN[kind][key], (kind, key))

    def test_unknown_boards_by_hand(self):
        for kind in ("pi-other", "arm-other"):
            self.assertEqual(R.command("/run/s", SCREEN, "auto", kind, ENV),
                             ["BIN", "--socket=/run/s", "--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00",
                              "--detail=540", "--fast", "--skip-deblock", "--sources=2"])
            self.assertEqual((R.sources_for(kind), R.source_cap(kind), R.final_arg("auto", kind)), (2, 2, "--final=auto"))
            self.assertEqual(R.source_warning(ROWS, 1, kind), R.WARN_TEXT)
            self.assertEqual(R.sources_for(kind, 8), 2, "never over the cap")

    def test_no_board_at_all(self):
        want = FROZEN["none"]
        self.assertEqual(E.default_detail(None), want["default_detail"])
        self.assertEqual(E.auto_lines(None, "x"), want["auto_lines"])
        self.assertEqual(list(S.heights_for(None)), want["heights"])
        self.assertEqual(S.default_height(None), want["default_height"])


class EffectRateTest(unittest.TestCase):
    def test_only_the_full_tier_passes_the_screens_rate_capped_at_60(self):
        for kind in ("pi3", "pi4", "pi-other", "arm-other"):
            for mode in ("1920x1080@60.00", "1920x1080@144.00"):
                self.assertFalse([a for a in R.command("/run/s", ["--drm-mode=" + mode], "auto", kind, ENV) if a.startswith("--fx-fps")], (kind, mode))
        for mode, want in (("1920x1080@60.00", 60), ("1920x1080@59.94", 60), ("1920x1080@50.00", 50), ("2560x1440@144.00", 60),
                           ("1280x720@24.00", 24), ("1920x1080@30.00", 30)):
            for kind in ("pi5", "x86"):
                cmd = R.command("/run/s", ["--vo=gpu", "--drm-mode=" + mode], "auto", kind, ENV)
                self.assertEqual([a for a in cmd if a.startswith("--fx-fps")], ["--fx-fps=%d" % want], (kind, mode))
        for screen in ([], ["--vo=gpu"], ["--drm-mode=3"], ["--drm-mode=1920x1080"], ["--drm-mode=1920x1080@x"]):
            self.assertFalse([a for a in R.command("/run/s", screen, "auto", "x86", ENV) if a.startswith("--fx-fps")], screen)

    def test_the_row_says_30_and_the_reason(self):
        for kind, text in (("pi4", "30 a second"), ("pi3", "30 a second"), ("pi-other", "30 a second"), ("x86", "up to the screen's rate, at most 60 a second")):
            row = {v["name"]: v for v in profile.describe(kind, "m")["values"]}["Effect rate (new renderer)"]
            self.assertEqual(row["value"], text, kind)
            self.assertIn("30 a second on a Pi 3 or 4; up to the screen's rate, at most 60, on a Pi 5 or a desktop, not yet measured there", row["why"])


class TableTest(unittest.TestCase):
    def test_every_kind_is_in_a_tier_and_every_tier_has_a_row(self):
        self.assertEqual(sorted(profile.KINDS), sorted(KINDS))
        for kind in profile.KINDS:
            self.assertIn(hardware.tier(kind), profile.PROFILES, kind)
        self.assertEqual(set(profile.PROFILES), {"lean", "standard", "full", "unknown"})

    def test_shader_default_matches_the_shaders_constant(self):
        self.assertEqual(profile.PROFILES["lean"]["shader_default"], S.DEFAULT_HEIGHT)

    def test_describe_names_the_tier_the_model_and_a_reason_for_each_value(self):
        for kind, tier in (("pi3", "lean"), ("pi4", "standard"), ("pi5", "full"), ("x86", "full"), ("pi-other", "unknown")):
            d = profile.describe(kind, "Raspberry Pi 3 Model B Rev 1.2")
            self.assertEqual((d["tier"], d["kind"], d["model"]), (tier, kind, "Raspberry Pi 3 Model B Rev 1.2"))
            self.assertGreaterEqual(len(d["values"]), 8)
            for v in d["values"]:
                self.assertTrue(v["name"] and v["value"] and v["why"], (kind, v))
                self.assertNotIn("\u2014", json.dumps(v))
            json.dumps(d)

    def test_the_rows_say_what_is_in_effect(self):
        def rows(kind, saved):
            return {v["name"]: v for v in profile.describe(kind, "m", saved)["values"]}
        auto = rows("pi4", {"use": "new", "detail": "auto"})
        self.assertEqual(auto["Video deblocking skipped (new renderer)"]["value"], "on")
        r = rows("pi4", {"use": "new", "detail": 720, "final": 720, "sources": 3, "deblock": True})
        self.assertEqual(r["Video deblocking (new renderer)"]["value"], "off")
        self.assertIn("D120", r["Video deblocking (new renderer)"]["why"])
        self.assertEqual((r["Picture detail inside"]["value"], r["Final picture (new renderer)"]["value"], r["Sources in use"]["value"]),
                         ("720 lines", "720 lines", "3"))
        self.assertEqual(rows("pi4", {"detail": "output"})["Picture detail inside"]["value"], "the screen's own size")
        clamped = rows("pi-other", {"sources": 6})["Sources in use"]
        self.assertEqual(clamped["value"], "2")
        self.assertIn("You chose 6", clamped["why"])
        for bad in (None, "x", 3, {"detail": True, "sources": True, "final": 5, "deblock": "yes"}):
            self.assertEqual(profile.describe("pi4", "m", bad)["values"], profile.describe("pi4", "m")["values"], bad)


if __name__ == "__main__":
    unittest.main()
