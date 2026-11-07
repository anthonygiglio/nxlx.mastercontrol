# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The profile table (pvj/profile.py) gives every board kind the values master gave it.

tests/frozen_master_profile.json was written once by running master's own functions (before the table existed) for
every kind and every setting below; nothing in it is computed by the code under test. If a value here has to change,
that is a decision to record, not a number to refresh."""
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


class FrozenValuesTest(unittest.TestCase):
    def test_every_kind_has_the_values_master_gave_it(self):
        for kind in ("pi3", "pi4", "pi5", "x86"):
            got, want = measure(kind), FROZEN[kind]
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

    def test_unknown_kinds(self):
        for kind in ("pi-other", "arm-other"):
            got, want = measure(kind), FROZEN[kind]
            for key in want:
                self.assertEqual(got[key], want[key], (kind, key))

    def test_no_board_at_all(self):
        want = FROZEN["none"]
        self.assertEqual(E.default_detail(None), want["default_detail"])
        self.assertEqual(E.auto_lines(None, "x"), want["auto_lines"])
        self.assertEqual(list(S.heights_for(None)), want["heights"])
        self.assertEqual(S.default_height(None), want["default_height"])


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


if __name__ == "__main__":
    unittest.main()
