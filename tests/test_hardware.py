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
    MODELS = (("Raspberry Pi 5 Model B Rev 1.0", "pi5", "full"),
              ("Raspberry Pi 500 Rev 1.0", "pi5", "full"),
              ("Raspberry Pi Compute Module 5 Rev 1.0", "pi5", "full"),
              ("Raspberry Pi 4 Model B Rev 1.4", "pi4", "standard"),
              ("Raspberry Pi 4 Model B Rev 1.5", "pi4", "standard"),
              ("Raspberry Pi 400 Rev 1.0", "pi4", "standard"),
              ("Raspberry Pi Compute Module 4 Rev 1.0", "pi4", "standard"),
              ("Raspberry Pi Compute Module 4S Rev 1.0", "pi4", "standard"),
              ("Raspberry Pi 3 Model B Plus Rev 1.3", "pi3", "lean"),
              ("Raspberry Pi 3 Model B Rev 1.2", "pi3", "lean"),
              ("Raspberry Pi 3 Model A Plus Rev 1.0", "pi3", "lean"),
              ("Raspberry Pi Compute Module 3 Rev 1.0", "pi3", "lean"),
              ("Raspberry Pi 2 Model B Rev 1.1", "pi3", "lean"),
              ("Raspberry Pi Zero 2 W Rev 1.0", "pi3", "lean"),
              ("Raspberry Pi Zero W Rev 1.1", "pi-other", "unknown"),
              ("Raspberry Pi Model B Rev 2", "pi-other", "unknown"),
              ("Raspberry Pi Model B Plus Rev 1.2", "pi-other", "unknown"),
              ("Raspberry Pi Compute Module Rev 1.0", "pi-other", "unknown"))

    def test_pi_models(self):
        for model, kind, tier in self.MODELS:
            root = make_tree({"proc/device-tree/model": model.encode() + b"\0"})
            board = hardware.detect_board(root)
            self.assertEqual(board["kind"], kind, model)
            self.assertEqual(board["model"], model, "the exact model stays visible")
            self.assertEqual(hardware.tier(board["kind"]), tier, model)

    def test_model_from_the_sys_path_too(self):
        root = make_tree({"sys/firmware/devicetree/base/model": b"Raspberry Pi 400 Rev 1.0\0"})
        self.assertEqual(hardware.detect_board(root)["kind"], "pi4")

    def test_tiers(self):
        for kind, tier in (("pi3", "lean"), ("pi4", "standard"), ("pi5", "full"), ("x86", "full"),
                           ("pi-other", "unknown"), ("arm-other", "unknown"), ("", "unknown"), ("nonsense", "unknown")):
            self.assertEqual(hardware.tier(kind), tier, kind)

    def test_a_non_pi_model_is_not_a_pi(self):
        root = make_tree({"proc/device-tree/model": b"Orange Pi 5 Plus\0"})
        self.assertEqual(hardware.detect_board(root)["kind"], "arm-other")

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
        self.assertEqual(conns[0], {"connector": "HDMI-A-1", "status": "connected", "modes": ["1920x1080", "1280x720"], "card": 1})
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



class VaDecodingTest(unittest.TestCase):
    """Hardware decoding on a PC node (J-152): pvj-selftest reports it as information, from the graphics, the VA-API
    driver files and the render node."""
    LIB = "usr/lib/x86_64-linux-gnu/dri/"

    def tree(self, files):
        root = make_tree(files)
        self.addCleanup(__import__("shutil").rmtree, root, True)
        return root

    def pci(self, cls, vendor, n=2):
        d = "sys/bus/pci/devices/0000:00:%02x.0/" % n
        return {d + "class": cls + "\n", d + "vendor": vendor + "\n"}

    def test_an_intel_node_with_its_driver_and_render_node_has_it(self):
        root = self.tree(dict(self.pci("0x030000", "0x8086"), **self.pci("0x060000", "0x8086", 0),
                              **{self.LIB + "iHD_drv_video.so": "", "dev/dri/renderD128": ""}))
        out = hardware.va_decoding(root, "x86_64")
        self.assertIs(out["available"], True)
        self.assertEqual((out["gpu_vendors"], out["va_drivers"], out["render_nodes"]), (["0x8086"], ["iHD"], ["renderD128"]))
        self.assertIn("iHD", out["text"])

    def test_an_intel_node_without_its_driver_is_told_the_package(self):
        root = self.tree(dict(self.pci("0x030000", "0x8086"), **{"dev/dri/renderD128": ""}))
        out = hardware.va_decoding(root, "x86_64")
        self.assertIs(out["available"], False)
        self.assertEqual(out["missing_package"], "intel-media-va-driver")
        self.assertIn("intel-media-va-driver", out["text"])

    def test_an_amd_node_needs_radeonsi_not_an_intel_driver(self):
        root = self.tree(dict(self.pci("0x030000", "0x1002"), **{self.LIB + "iHD_drv_video.so": "", "dev/dri/renderD128": ""}))
        out = hardware.va_decoding(root, "x86_64")
        self.assertEqual((out["available"], out["missing_package"]), (False, "mesa-va-drivers"))
        root = self.tree(dict(self.pci("0x030000", "0x1002"), **{self.LIB + "radeonsi_drv_video.so": "", "dev/dri/renderD128": ""}))
        self.assertIs(hardware.va_decoding(root, "x86_64")["available"], True)

    def test_no_render_node_means_no_hardware_decoding(self):
        root = self.tree(dict(self.pci("0x030000", "0x8086"), **{self.LIB + "iHD_drv_video.so": ""}))
        out = hardware.va_decoding(root, "x86_64")
        self.assertIs(out["available"], False)
        self.assertIn("renderD", out["text"])

    def test_a_pi_is_not_checked(self):
        out = hardware.va_decoding(self.tree({"dev/dri/renderD128": ""}), "aarch64")
        self.assertIsNone(out["available"])
        self.assertIn("Not checked", out["text"])

    def test_the_selftest_reports_it_and_never_fails_on_it(self):
        from pvj import selftest
        out = selftest.decoding(self.tree(self.pci("0x030000", "0x8086")), "x86_64")
        self.assertIs(out["available"], False)
        broken = hardware.va_decoding
        hardware.va_decoding = lambda *a: 1 / 0
        try:
            out = selftest.decoding()
        finally:
            hardware.va_decoding = broken
        self.assertIsNone(out["available"])
        self.assertIn("ZeroDivisionError", out["text"])
