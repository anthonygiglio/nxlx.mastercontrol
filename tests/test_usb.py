# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import tempfile
import unittest

from pvj import usb
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


class FakeRunner:
    def __init__(self, blkid=None, mount_rc=0):
        self.blkid = blkid or {}
        self.mount_rc = mount_rc
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "blkid":
            out = self.blkid.get(cmd[-1])
            if out is None:
                return subprocess.CompletedProcess(cmd, 2, "", "")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[0] == "mount":
            return subprocess.CompletedProcess(cmd, self.mount_rc, "", "mount: bad superblock" if self.mount_rc else "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class UsbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, "pvj")
        self.link = os.path.join(self.tmp, "usb")
        self.env = {"PVJ_USB_BASE": self.base, "PVJ_USB_LINK": self.link}
        self.mounts = os.path.join(self.tmp, "mounts")
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n")
        self.logs = []

    def set_mounts(self, text):
        with open(self.mounts, "w") as f:
            f.write(text)

    def mount(self, dev, runner, env=None):
        return usb.mount(dev, env=env or self.env, runner=runner, proc_mounts=self.mounts, log=self.logs.append,
                         system_disks=set())

    def test_sanitize_label(self):
        s = usb.sanitize_label
        self.assertEqual(s("MY GIG/USB", "x"), "MY_GIG_USB")
        self.assertEqual(s("../../etc", "x"), "_.._etc")
        self.assertEqual(s(".hidden", "x"), "hidden")
        self.assertEqual(s("a\nb; rm -rf /", "x"), "a_b__rm_-rf__")
        self.assertEqual(s("", "sdb1"), "sdb1")
        self.assertEqual(len(s("a" * 100, "x")), 32)

    def test_mount_options(self):
        o = usb.mount_options("vfat", False, 995)
        self.assertTrue(o.startswith("ro,nosuid,nodev,noexec"))
        self.assertIn("gid=995", o)
        self.assertIn("utf8", o)
        self.assertTrue(usb.mount_options("ext4", True, None).startswith("rw,nosuid,nodev,noexec"))
        self.assertNotIn("uid=", usb.mount_options("ext4", False, None))

    def test_mounts_by_label_read_only_and_links(self):
        r = FakeRunner({"/dev/sdb1": "TYPE=exfat\nLABEL=NXLX SHOW\nUUID=1\n"})
        point = self.mount("/dev/sdb1", r)
        self.assertEqual(point, os.path.join(self.base, "NXLX_SHOW"))
        cmd = r.calls[-1]
        self.assertEqual(cmd[:3], ["mount", "-t", "exfat"])
        self.assertIn("ro,nosuid,nodev,noexec", cmd[4])
        self.assertEqual(cmd[-2:], ["/dev/sdb1", point])
        self.assertEqual(os.readlink(self.link), point)

    def test_rw_only_when_asked(self):
        r = FakeRunner({"/dev/sdb1": "TYPE=ext4\nLABEL=X\n"})
        self.mount("/dev/sdb1", r, dict(self.env, PVJ_USB_RW="1"))
        self.assertTrue(r.calls[-1][4].startswith("rw,"))

    def test_same_label_twice_gets_distinct_folders(self):
        r = FakeRunner({"/dev/sdb1": "TYPE=vfat\nLABEL=SHOW\n", "/dev/sdc1": "TYPE=vfat\nLABEL=SHOW\n"})
        p1 = self.mount("/dev/sdb1", r)
        self.set_mounts("/dev/sdb1 %s vfat ro 0 0\n" % p1)
        p2 = self.mount("/dev/sdc1", r)
        self.assertNotEqual(p1, p2)
        self.assertEqual(p2, p1 + "-2")

    def test_refuses_bad_device_nodes(self):
        for dev in ("/dev/sda1; reboot", "/dev/../etc/passwd", "sda1", "/dev/nvme0n1p1", "", None):
            with self.assertRaises(usb.UsbError, msg=repr(dev)):
                self.mount(dev, FakeRunner())

    def test_backing_disks_found_from_device_numbers_even_when_root_is_dev_root(self):
        import types
        sysfs = os.path.join(self.tmp, "sysblock")
        os.makedirs(os.path.join(self.tmp, "devices", "block", "sda", "sda2"))
        os.makedirs(sysfs)
        os.symlink(os.path.join(self.tmp, "devices", "block", "sda", "sda2"), os.path.join(sysfs, "8:2"))
        fake = lambda path: types.SimpleNamespace(st_dev=os.makedev(8, 2)) if path == "/" else (_ for _ in ()).throw(OSError())
        self.assertEqual(usb.backing_disks(("/", "/boot"), sysfs, fake), {"/dev/sda"})
        # tmpfs/overlay device numbers that are not a sd disk are ignored
        fake_overlay = lambda path: types.SimpleNamespace(st_dev=os.makedev(0, 23))
        self.assertEqual(usb.backing_disks(("/",), sysfs, fake_overlay), set())

    def test_refuses_the_system_disk_even_if_proc_mounts_hides_it(self):
        self.set_mounts("/dev/root / ext4 rw 0 0\n")  # no parent disk visible
        r = FakeRunner({"/dev/sda1": "TYPE=vfat\nLABEL=EFI\n"})
        with self.assertRaises(usb.UsbError):
            usb.mount("/dev/sda1", env=self.env, runner=r, proc_mounts=self.mounts, log=self.logs.append,
                      system_disks={"/dev/sda"})
        self.assertEqual(r.calls, [])

    def test_refuses_disk_holding_running_system(self):
        # USB-booted x86 box: root lives on sda2, so sda1 (same disk) must be left alone
        self.set_mounts("/dev/sda2 / ext4 rw 0 0\n")
        r = FakeRunner({"/dev/sda1": "TYPE=vfat\nLABEL=EFI\n"})
        with self.assertRaises(usb.UsbError):
            self.mount("/dev/sda1", r)
        self.assertEqual(r.calls, [])

    def test_already_mounted_is_a_no_op(self):
        self.set_mounts("/dev/sdb1 /somewhere vfat ro 0 0\n")
        r = FakeRunner()
        self.assertIsNone(self.mount("/dev/sdb1", r))
        self.assertEqual(r.calls, [])

    def test_unsupported_and_failed_mounts_leave_nothing_behind(self):
        with self.assertRaises(usb.UsbError):
            self.mount("/dev/sdb1", FakeRunner({"/dev/sdb1": "TYPE=crypto_LUKS\n"}))
        with self.assertRaises(usb.UsbError):
            self.mount("/dev/sdb1", FakeRunner({"/dev/sdb1": "TYPE=vfat\nLABEL=A\n"}, mount_rc=32))
        self.assertEqual(os.listdir(self.base), [])
        with self.assertRaises(usb.UsbError):
            self.mount("/dev/sdb1", FakeRunner())  # blkid finds nothing

    def test_real_folder_at_link_path_is_not_replaced(self):
        os.mkdir(self.link)
        r = FakeRunner({"/dev/sdb1": "TYPE=vfat\nLABEL=A\n"})
        self.mount("/dev/sdb1", r)
        self.assertFalse(os.path.islink(self.link))
        self.assertTrue(any("real folder" in m for m in self.logs))

    def test_unmount_removes_folder_and_link(self):
        r = FakeRunner({"/dev/sdb1": "TYPE=vfat\nLABEL=SHOW\n"})
        point = self.mount("/dev/sdb1", r)
        self.set_mounts("/dev/sdb1 %s vfat ro 0 0\n" % point)
        usb.unmount("/dev/sdb1", env=self.env, runner=r, proc_mounts=self.mounts, log=self.logs.append)
        self.assertEqual(r.calls[-1], ["umount", "-l", point])
        self.assertFalse(os.path.exists(point))
        self.assertFalse(os.path.lexists(self.link))

    def test_unmount_ignores_mounts_outside_our_folder(self):
        self.set_mounts("/dev/sdb1 /mnt/other vfat ro 0 0\n")
        r = FakeRunner()
        usb.unmount("/dev/sdb1", env=self.env, runner=r, proc_mounts=self.mounts, log=self.logs.append)
        self.assertEqual(r.calls, [])


if __name__ == "__main__":
    unittest.main()
