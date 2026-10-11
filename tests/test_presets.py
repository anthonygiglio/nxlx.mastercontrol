# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Checks the preset table against every real legacy script in sync/."""
import glob
import os
import re
import tempfile
import unittest

from pvj import presets
from pvj.player import PlayerError
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

SYNC = os.path.join(os.path.dirname(__file__), "..", "sync")


def legacy_command(path):
    """The omxplayer line of a legacy script, with a trailing redirect removed."""
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                continue
            m = re.match(r"^/usr/bin/(omxplayer(?:-sync)?)\s+(.*?)(\s*>\s*/dev/null.*)?$", line)
            if m:
                return m.group(1), m.group(2).split(" &")[0].strip()
    return None, None


# Upstream defects, deliberately not reproduced:
#   startless is a wrapper that loops every file via startlesshelper (no omxplayer line);
#   startmaster95 plays 65* (copy-paste error); the preset plays 95*.
KNOWN_DEFECTS = {"startless": "no omxplayer line",
                 "startmaster95": "target /media/internal/video/65* vs /media/internal/video/95*"}


class PresetTest(unittest.TestCase):
    def test_every_legacy_script_matches_the_table(self):
        checked = 0
        mismatches = []
        for path in sorted(glob.glob(os.path.join(SYNC, "start*"))):
            name = os.path.basename(path)
            try:
                preset = presets.parse_legacy_name(name)
            except PlayerError:
                continue
            binary, args = legacy_command(path)
            if binary is None:
                mismatches.append((name, "no omxplayer line"))
                continue
            loop = "--loop" in args.split() or bool(re.search(r"(^|\s)-mu(\s|$)", args))
            audio = re.search(r"-o\s+(\w+)", args)
            audio = audio.group(1) if audio else None
            target = args.split()[-1]
            want_dir = presets.DEFAULT_USB_DIR if preset["usb"] else presets.DEFAULT_MEDIA_DIR
            want_target = want_dir + "/" + ((preset["index"] or "") + "*")
            problems = []
            if loop != preset["loop"]:
                problems.append("loop %s vs %s" % (loop, preset["loop"]))
            if preset["audio"] and audio != preset["audio"]:
                problems.append("audio %s vs %s" % (audio, preset["audio"]))
            if preset["sync_master"] != (binary == "omxplayer-sync"):
                problems.append("sync mismatch")
            if target != want_target:
                problems.append("target %s vs %s" % (target, want_target))
            if problems:
                mismatches.append((name, "; ".join(problems)))
            checked += 1
        self.assertGreater(checked, 100, "expected to check the ~290 legacy scripts")
        self.assertEqual([m for m in mismatches if KNOWN_DEFECTS.get(m[0]) != m[1]], [])
        self.assertEqual(len(mismatches), len(KNOWN_DEFECTS), "a known defect went away; update KNOWN_DEFECTS")

    def test_unported_names_rejected(self):
        for name in ("startslave", "startmasterwifi", "startmasterstream", "startlesshelper", "reboot", "../x"):
            with self.assertRaises(PlayerError, msg=name):
                presets.parse_legacy_name(name)

    def test_resolve_files(self):
        d = tempfile.mkdtemp()
        for n in ("01_a.mp4", "05_b.mp4", "05_c.mov", ".hidden"):
            open(os.path.join(d, n), "w").close()
        p = presets.parse_legacy_name("startlessonce05")
        self.assertEqual([os.path.basename(f) for f in presets.resolve_files(p, d)], ["05_b.mp4", "05_c.mov"])
        self.assertEqual(len(presets.resolve_files(presets.parse_legacy_name("startmaster"), d)), 3)
        with self.assertRaises(PlayerError):
            presets.resolve_files(presets.parse_legacy_name("startless09"), d)


if __name__ == "__main__":
    unittest.main()
