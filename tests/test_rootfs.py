# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import tempfile
import unittest

from pvj import rootfs
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


def mounts(root_fs):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mounts")
    with open(p, "w") as f:
        f.write("%s / %s rw 0 0\nproc /proc proc rw 0 0\n" % ("overlayroot" if root_fs == "overlay" else "/dev/mmcblk0p2", root_fs))
    return p


class Runner:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", "boom" if self.rc else "")


def which_only(*names):
    return lambda n: "/usr/bin/" + n if n in names else None


class RootfsTest(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.media = tempfile.mkdtemp()

    def test_state_detection(self):
        self.assertEqual(rootfs.current_state(mounts("ext4")), "normal")
        self.assertEqual(rootfs.current_state(mounts("overlay")), "overlay")
        self.assertEqual(rootfs.current_state("/nonexistent"), "normal")

    def test_backend_choice(self):
        self.assertEqual(rootfs.detect_backend("pi4", which_only("raspi-config")), "raspi-config")
        self.assertEqual(rootfs.detect_backend("x86", which_only("overlayroot-chroot")), "overlayroot")
        self.assertEqual(rootfs.detect_backend("x86", which_only("raspi-config")), None)
        self.assertEqual(rootfs.detect_backend("pi5", which_only()), None)

    def test_enable_on_pi_uses_raspi_config(self):
        r = Runner()
        rootfs.change("enable", "pi4", self.media, force=True, runner=r, proc_mounts=mounts("ext4"),
                      which=which_only("raspi-config"), log=self.logs.append)
        self.assertEqual(r.calls, [["raspi-config", "nonint", "enable_overlayfs"]])
        self.assertIn("Reboot", self.logs[-1])

    def test_disable_overlayroot_edits_the_lower_filesystem_when_active(self):
        r = Runner()
        rootfs.change("disable", "x86", self.media, runner=r, proc_mounts=mounts("overlay"),
                      which=which_only("overlayroot-chroot"), log=self.logs.append)
        self.assertEqual(r.calls[0][0], "overlayroot-chroot")
        r = Runner()
        rootfs.change("disable", "x86", self.media, runner=r, proc_mounts=mounts("ext4"),
                      which=which_only("overlayroot-chroot"), log=self.logs.append)
        self.assertEqual(r.calls[0][0], "sh")

    def test_enable_refused_when_media_lives_on_root(self):
        with self.assertRaises(rootfs.RootfsError):
            rootfs.change("enable", "x86", self.media, runner=Runner(), proc_mounts=mounts("ext4"),
                          which=which_only("overlayroot-chroot"), log=self.logs.append)

    def test_enable_when_already_active_is_a_no_op(self):
        r = Runner()
        rootfs.change("enable", "x86", self.media, runner=r, proc_mounts=mounts("overlay"),
                      which=which_only("overlayroot-chroot"), log=self.logs.append)
        self.assertEqual(r.calls, [])

    def test_no_tool_and_tool_failure_are_errors(self):
        with self.assertRaises(rootfs.RootfsError):
            rootfs.change("enable", "x86", self.media, force=True, runner=Runner(), proc_mounts=mounts("ext4"),
                          which=which_only(), log=self.logs.append)
        with self.assertRaises(rootfs.RootfsError):
            rootfs.change("enable", "pi4", self.media, force=True, runner=Runner(rc=1), proc_mounts=mounts("ext4"),
                          which=which_only("raspi-config"), log=self.logs.append)

    def test_status_shape(self):
        s = rootfs.status("x86", self.media, mounts("ext4"), which_only())
        self.assertEqual(s["root"], "normal")
        self.assertIsNone(s["backend"])
        self.assertIn("media_on_root", s)


if __name__ == "__main__":
    unittest.main()
