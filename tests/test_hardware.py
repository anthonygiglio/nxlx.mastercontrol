# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from pvj import hardware
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


def make_tree(files):
    root = tempfile.mkdtemp()
    for path, text in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(text.encode() if isinstance(text, str) else text)
    return root


class HardwareTest(unittest.TestCase):
    def test_pi_models(self):
        for model, kind in (("Raspberry Pi 3 Model B Plus Rev 1.3", "pi3"),
                            ("Raspberry Pi 4 Model B Rev 1.5", "pi4"),
                            ("Raspberry Pi 5 Model B Rev 1.0", "pi5"),
                            ("Raspberry Pi Zero 2 W Rev 1.0", "pi-other")):
            root = make_tree({"proc/device-tree/model": model.encode() + b"\0"})
            self.assertEqual(hardware.detect_board(root)["kind"], kind, model)

    def test_x86_from_cpuinfo(self):
        root = make_tree({"proc/cpuinfo": "vendor_id\t: GenuineIntel\nmodel name\t: Intel N100\n"})
        board = hardware.detect_board(root)
        self.assertEqual(board["kind"], "x86")
        self.assertEqual(board["model"], "Intel N100")

    def test_unknown_arm(self):
        self.assertEqual(hardware.detect_board(make_tree({"x": ""}))["kind"], "arm-other")

    def test_os_release(self):
        root = make_tree({"etc/os-release": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_CODENAME=bookworm\n'})
        self.assertEqual(hardware.os_release(root)["version_codename"], "bookworm")

    def test_temperatures_sysfs(self):
        root = make_tree({"sys/class/thermal/thermal_zone0/temp": "48312\n",
                          "sys/class/thermal/thermal_zone0/type": "cpu-thermal\n",
                          "sys/class/thermal/thermal_zone1/temp": "garbage"})
        temps = hardware.temperatures(root)
        self.assertEqual(temps, [{"zone": "thermal_zone0", "type": "cpu-thermal", "celsius": 48.3}])

    def test_drm_connectors(self):
        root = make_tree({"sys/class/drm/card1-HDMI-A-1/status": "connected\n",
                          "sys/class/drm/card1-HDMI-A-1/modes": "1920x1080\n1280x720\n",
                          "sys/class/drm/card1-HDMI-A-2/status": "disconnected\n"})
        conns = hardware.drm_connectors(root)
        self.assertEqual(conns[0], {"connector": "HDMI-A-1", "status": "connected", "modes": ["1920x1080", "1280x720"]})
        self.assertEqual(conns[1]["status"], "disconnected")

    def test_profile_headless_uses_drm(self):
        p = hardware.playback_profile({"kind": "pi4"}, has_desktop=False)
        self.assertIn("--gpu-context=drm", p["mpv_args"])
        p = hardware.playback_profile({"kind": "pi5"}, has_desktop=True)
        self.assertNotIn("--gpu-context=drm", p["mpv_args"])
        self.assertTrue(p["notes"])


if __name__ == "__main__":
    unittest.main()


class CheapRenderingTest(unittest.TestCase):
    """--profile=fast: 1080p H.264 dropped 15 frames a second on a Pi 4 at 2560x1440 without it and none with it."""

    def test_pis_that_struggle_get_the_fast_profile_others_do_not(self):
        for kind, expected in (("pi3", True), ("pi4", True), ("pi5", False), ("x86", False), ("arm-other", False)):
            args = hardware.playback_profile({"kind": kind}, has_desktop=False)["mpv_args"]
            self.assertEqual("--profile=fast" in args, expected, kind)

