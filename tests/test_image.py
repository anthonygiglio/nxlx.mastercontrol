# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Static checks of the pi-gen stage. It cannot be built here: see image/README.md."""
import os
import subprocess
import unittest
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

IMAGE = os.path.join(os.path.dirname(__file__), "..", "image")


def config():
    out = {}
    with open(os.path.join(IMAGE, "config")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip("'\"")
    return out


class ImageStageTest(unittest.TestCase):
    def test_config_targets_trixie_lite_with_our_stage(self):
        c = config()
        self.assertEqual(c["RELEASE"], "trixie")
        self.assertEqual(c["STAGE_LIST"].split(), ["stage0", "stage1", "stage2", "stage-pvj"])
        self.assertEqual(c["ENABLE_SSH"], "0")
        self.assertEqual(c["DISABLE_FIRST_BOOT_USER_RENAME"], "0")
        self.assertNotIn("FIRST_USER_PASS", c, "never bake a default password into the image")

    def test_stage_files(self):
        stage = os.path.join(IMAGE, "stage-pvj")
        self.assertTrue(os.path.isfile(os.path.join(stage, "EXPORT_IMAGE")))
        for script in ("prerun.sh", "00-install-pvj/01-run.sh"):
            path = os.path.join(stage, script)
            self.assertTrue(os.access(path, os.X_OK), script + " must be executable")
            self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0, script)
        with open(os.path.join(stage, "00-install-pvj", "00-packages-nr")) as f:
            pkgs = f.read().split()
        for p in ("mpv", "python3"):
            self.assertIn(p, pkgs)

    def test_run_script_uses_the_offline_installer_and_enables_the_service(self):
        with open(os.path.join(IMAGE, "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            text = f.read()
        self.assertIn("install.sh --offline --no-start", text)
        self.assertIn("systemctl enable pvj-player.service pvj-web.service pvj-netd.service", text)

    def test_workflow_copies_what_the_installer_needs(self):
        wf = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "image.yml")
        with open(wf) as f:
            text = f.read()
        self.assertIn("cp -a pocketvj/pvj pocketvj/bin pocketvj/install", text)
        self.assertIn("SKIP_IMAGES", text)


if __name__ == "__main__":
    unittest.main()
