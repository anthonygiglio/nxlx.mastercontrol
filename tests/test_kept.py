# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The kept settings folder (J-050, D148): mctl/kept.py and its place in nxlx-rootfs enable.

Everything runs in a temp folder standing in for the node's root, with a fake mount that makes a link where a real
bind mount would go. No real mount, disk or reboot is involved: what the real tools do on a node is still to be
tried at the box (the pull-the-plug test)."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from mctl import kept, paths, rootfs
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

UUID = "1234abcd-0000-4000-8000-0123456789ab"


class FakeSystem:
    """A node's root in a temp folder and the commands kept.py runs, acted out on it."""

    def __init__(self, test):
        self.root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.root, True)
        self.calls = []
        self.mount_rc = 0
        self.mount_fail_first = False
        self.blkid = {"TYPE": "ext4", "UUID": UUID, "FSSIZE": str(512 * 2**20)}
        self.mkfs_rc = 0
        os.makedirs(self.p("proc"))
        self.write(self.p("proc/mounts"), "overlayroot / overlay rw 0 0\n")
        os.makedirs(self.p("dev/disk/by-uuid"))
        self.disk = self.p("dev/sda3")
        open(self.disk, "w").close()

    def p(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def add_disk_link(self):
        os.symlink(self.disk, self.p("dev/disk/by-uuid/" + UUID))

    def prepare_volume_files(self, marker=UUID):
        """What `prepare` leaves on the disk: the marker. (The disk itself is the folder where it is 'mounted'.)"""
        os.makedirs(self.p("var/lib/nxlx-kept/.nxlx-kept"), exist_ok=True)
        if marker is not None:
            self.write(self.p("var/lib/nxlx-kept/.nxlx-kept/volume"), marker + "\n")

    def configure(self):
        self.write(self.p("etc/nxlx/kept.json"), json.dumps({"uuid": UUID}))

    def node_folders(self):
        """The folders a normal node has before the first kept boot."""
        self.write(self.p("var/lib/nxlx/settings.json"), '{"pin": "1234"}')
        self.write(self.p("var/lib/nxlx/scenes/one.json"), "[1]")
        os.makedirs(self.p("var/lib/nxlx-player/late-screen"))
        os.makedirs(self.p("var/lib/nxlx-update"), mode=0o700)
        os.chmod(self.p("var/lib/nxlx-update"), 0o700)
        self.write(self.p("var/lib/nxlx-update/settings-1-2.json"), "{}")
        os.makedirs(self.p("etc/NetworkManager/system-connections"))
        self.write(self.p("etc/NetworkManager/system-connections/venue.nmconnection"), "[wifi]\nssid=Venue\n")
        os.chmod(self.p("etc/NetworkManager/system-connections/venue.nmconnection"), 0o600)

    def mounts(self):
        with open(self.p("proc/mounts")) as f:
            return f.read()

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        name = cmd[0]
        out, rc = "", 0
        if name == "mount" and cmd[1] == "--bind":
            src, dst = cmd[2], cmd[3]
            shutil.rmtree(dst)
            os.symlink(src, dst)              # a write through the covered place lands in the kept folder, as with a bind mount
            with open(self.p("proc/mounts"), "a") as f:
                f.write("/dev/sda3 %s ext4 rw 0 0\n" % dst)
        elif name == "mount":
            real_fail = self.mount_rc or (self.mount_fail_first and not any(c[0] == "e2fsck" for c in self.calls))
            if real_fail:
                rc, out = 32, "wrong fs type"
            else:
                with open(self.p("proc/mounts"), "a") as f:
                    f.write("%s %s ext4 rw 0 0\n" % (cmd[-2], cmd[-1]))
        elif name == "umount":
            lines = [l for l in self.mounts().splitlines(True) if cmd[1] not in l.split()]
            self.write(self.p("proc/mounts"), "".join(lines))
        elif name == "blkid":
            tag = cmd[cmd.index("-s") + 1]
            out = self.blkid.get(tag, "")
            rc = 0 if out else 2
        elif name == "mkfs.ext4":
            rc = self.mkfs_rc
        return subprocess.CompletedProcess(cmd, rc, out + ("\n" if out else ""), "boom" if rc else "")

    def kept(self, **kw):
        kw.setdefault("wait_seconds", 3)
        kw.setdefault("sleep", lambda s: self.calls.append(["sleep", s]))
        self.logs = []
        return kept.Kept(root=self.root, runner=self, proc_mounts=self.p("proc/mounts"), log=self.logs.append, **kw)

    def reboot(self):
        """The RAM layer goes: every covered folder is what the protected disk holds, which is what it held before."""
        self.write(self.p("proc/mounts"), "overlayroot / overlay rw 0 0\n")
        for _name, place, _c in kept.ITEMS:
            path = self.p(place)
            if os.path.islink(path):
                os.unlink(path)
        self.write_status_gone()

    def write_status_gone(self):
        try:
            os.unlink(self.p("run/nxlx-kept.json"))
        except FileNotFoundError:
            pass

    def status(self):
        with open(self.p("run/nxlx-kept.json")) as f:
            return json.load(f)


class CopyTreeTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_copies_files_modes_and_nested_folders(self):
        src = os.path.join(self.d, "src")
        os.makedirs(os.path.join(src, "a", "b"))
        with open(os.path.join(src, "a", "b", "f.txt"), "w") as f:
            f.write("hello")
        os.chmod(os.path.join(src, "a", "b", "f.txt"), 0o640)
        os.chmod(os.path.join(src, "a"), 0o2750)
        skipped = kept.copy_tree(src, os.path.join(self.d, "dst"))
        self.assertEqual(skipped, [])
        with open(os.path.join(self.d, "dst", "a", "b", "f.txt")) as f:
            self.assertEqual(f.read(), "hello")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "dst", "a", "b", "f.txt")).st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "dst", "a")).st_mode), 0o2750)

    def test_a_link_is_never_followed_or_copied(self):
        secret = os.path.join(self.d, "secret.txt")
        with open(secret, "w") as f:
            f.write("not the panel's")
        outside = os.path.join(self.d, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "x"), "w") as f:
            f.write("x")
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        os.symlink(secret, os.path.join(src, "settings.json"))
        os.symlink(outside, os.path.join(src, "video"))
        os.symlink("/etc/shadow", os.path.join(src, "dangling-ish"))
        os.mkfifo(os.path.join(src, "pipe"))
        with open(os.path.join(src, "real.json"), "w") as f:
            f.write("{}")
        dst = os.path.join(self.d, "dst")
        skipped = kept.copy_tree(src, dst)
        self.assertEqual(sorted(os.listdir(dst)), ["real.json"])
        self.assertEqual(sorted(skipped), ["dangling-ish", "pipe", "settings.json", "video"])

    def test_the_folder_itself_may_not_be_a_link(self):
        real = os.path.join(self.d, "real")
        os.makedirs(real)
        os.symlink(real, os.path.join(self.d, "link"))
        with self.assertRaises(OSError):
            kept.copy_tree(os.path.join(self.d, "link"), os.path.join(self.d, "dst"))

    def test_set_id_bits_on_files_are_not_kept(self):
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        f = os.path.join(src, "tool")
        with open(f, "w") as fh:
            fh.write("x")
        os.chmod(f, 0o4755)
        kept.copy_tree(src, os.path.join(self.d, "dst"))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "dst", "tool")).st_mode) & 0o6000, 0)


class BootTest(unittest.TestCase):
    def setUp(self):
        self.s = FakeSystem(self)
        self.s.node_folders()

    def ready(self):
        self.s.configure()
        self.s.add_disk_link()
        self.s.prepare_volume_files()

    def test_the_panel_state_and_root_state_are_among_the_kept_folders(self):
        # J-178: root's own update folder stays outside the panel's folder, and both are kept, each on its own.
        places = {place for _n, place, _c in kept.ITEMS}
        self.assertIn(paths.STATE_DIR, places)
        self.assertIn(paths.ROOT_STATE_DIR, places)
        self.assertIn("/etc/NetworkManager/system-connections", places)
        for _n, place, _c in kept.ITEMS:
            self.assertFalse(place.startswith(paths.STATE_DIR + "/"), "%s is inside the panel's folder" % place)

    def test_nothing_is_set_up_means_nothing_happens(self):
        k = self.s.kept()
        self.assertEqual(k.boot(), 0)
        self.assertEqual(self.s.status()["state"], "off")
        self.assertEqual([c for c in self.s.calls if c[0] == "mount"], [])
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))

    def test_first_boot_copies_the_folders_then_covers_them(self):
        self.ready()
        k = self.s.kept()
        self.assertEqual(k.boot(), 0, self.s.logs)
        st = self.s.status()
        self.assertEqual(st["state"], "active")
        self.assertEqual(st["items"]["state"], "seeded and bound")
        self.assertEqual(st["items"]["wifi"], "seeded and bound")
        self.assertEqual(st["items"]["netd"], "seeded and bound")        # made empty, root only, because it is missing
        self.assertEqual(st["items"]["support"], "seeded and bound")
        vol = self.s.p("var/lib/nxlx-kept")
        with open(os.path.join(vol, "state", "settings.json")) as f:
            self.assertEqual(f.read(), '{"pin": "1234"}')
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(vol, "update")).st_mode), 0o700)      # root's folder stays root's
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(vol, "wifi", "venue.nmconnection")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(vol, "netd")).st_mode), 0o700)
        # the volume was mounted with the options that keep it safe, before anything was bound
        mount = [c for c in self.s.calls if c[0] == "mount" and c[1] == "-t"][0]
        self.assertIn("errors=remount-ro", mount[4])
        self.assertIn("nosuid", mount[4])
        order = [c[1] for c in self.s.calls if c[0] == "mount"]
        self.assertEqual(order[0], "-t")

    def test_a_write_after_boot_survives_a_restart(self):
        self.ready()
        self.assertEqual(self.s.kept().boot(), 0)
        # the panel changes the PIN and uploads a graphic, through the normal paths
        self.s.write(self.s.p("var/lib/nxlx/settings.json"), '{"pin": "9876"}')
        self.s.write(self.s.p("var/lib/nxlx/graphics/logo.png"), "PNG")
        self.s.write(self.s.p("etc/NetworkManager/system-connections/new.nmconnection"), "[wifi]\nssid=New\n")
        # power goes: the RAM layer is gone, the protected disk still holds the first copy of everything
        self.s.reboot()
        self.s.node_folders()
        self.assertEqual(self.s.kept().boot(), 0)
        for path, text in (("var/lib/nxlx/settings.json", '{"pin": "9876"}'), ("var/lib/nxlx/graphics/logo.png", "PNG"),
                           ("etc/NetworkManager/system-connections/new.nmconnection", "[wifi]\nssid=New\n")):
            with open(self.s.p(path)) as f:
                self.assertEqual(f.read(), text, path)
        st = self.s.status()
        self.assertEqual(st["items"]["state"], "bound")                   # not copied again

    def test_a_kept_folder_is_never_copied_over_again(self):
        self.ready()
        self.s.kept().boot()
        self.s.write(self.s.p("var/lib/nxlx/settings.json"), "kept version")
        self.s.reboot()
        self.s.node_folders()                                             # the protected disk's older copy is back
        self.s.kept().boot()
        with open(self.s.p("var/lib/nxlx-kept/state/settings.json")) as f:
            self.assertEqual(f.read(), "kept version")

    def test_a_missing_disk_leaves_everything_as_it_is_and_says_so(self):
        self.s.configure()                                                # no disk link: the disk is not there
        k = self.s.kept(wait_seconds=3)
        self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "missing")
        self.assertIn("not there", st["message"])
        self.assertEqual([c for c in self.s.calls if c[0] == "mount"], [])
        self.assertEqual(len([c for c in self.s.calls if c[0] == "sleep"]), 3)       # waited, and no longer than asked
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))
        self.assertFalse(os.path.islink(self.s.p("var/lib/nxlx")))

    def test_a_disk_without_the_right_marker_is_refused(self):
        self.s.configure()
        self.s.add_disk_link()
        self.s.prepare_volume_files(marker="ffffffff-0000-0000-0000-000000000000")
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual(self.s.status()["state"], "missing")
        self.assertEqual([c for c in self.s.calls if c[:2] == ["mount", "--bind"]], [])
        self.s.logs = []
        self.s.calls = []
        # no marker at all
        shutil.rmtree(self.s.p("var/lib/nxlx-kept"))
        self.s.write_status_gone()
        self.s.write(self.s.p("proc/mounts"), "overlayroot / overlay rw 0 0\n")
        self.s.prepare_volume_files(marker=None)
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual([c for c in self.s.calls if c[:2] == ["mount", "--bind"]], [])

    def test_a_bad_config_is_refused_not_ignored(self):
        self.s.write(self.s.p("etc/nxlx/kept.json"), '{"uuid": "../../etc"}')
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual(self.s.status()["state"], "refused")
        self.s.write(self.s.p("etc/nxlx/kept.json"), "not json")
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual(self.s.status()["state"], "refused")

    def test_the_mount_point_may_not_be_a_link(self):
        self.ready()
        target = self.s.p("var/lib/nxlx-kept")
        shutil.rmtree(target)
        os.symlink(self.s.p("tmp"), target)
        os.makedirs(self.s.p("tmp"))
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual([c for c in self.s.calls if c[0] == "mount"], [])

    def test_a_covered_folder_that_is_a_link_is_left_alone_and_not_copied(self):
        self.ready()
        shutil.rmtree(self.s.p("var/lib/nxlx"))
        os.makedirs(self.s.p("elsewhere"))
        os.symlink(self.s.p("elsewhere"), self.s.p("var/lib/nxlx"))
        k = self.s.kept()
        self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "partial")
        self.assertTrue(st["items"]["state"].startswith("left as it is"))
        self.assertFalse(os.path.lexists(self.s.p("var/lib/nxlx-kept/state")))
        self.assertEqual(st["items"]["wifi"], "seeded and bound")          # the others are still kept

    def test_a_kept_copy_that_is_a_link_is_not_used(self):
        self.ready()
        os.makedirs(self.s.p("etc/evil"))
        os.symlink(self.s.p("etc/evil"), self.s.p("var/lib/nxlx-kept/state"))
        k = self.s.kept()
        self.assertEqual(k.boot(), 1)
        self.assertTrue(self.s.status()["items"]["state"].startswith("left as it is"))
        self.assertNotIn(["mount", "--bind", self.s.p("var/lib/nxlx-kept/state"), self.s.p("var/lib/nxlx")], self.s.calls)

    def test_a_full_kept_disk_keeps_nothing_half_copied(self):
        self.ready()
        fake = os.statvfs_result((4096, 4096, 100, 1, 1, 0, 0, 0, 0, 255))     # a few KB free
        with mock.patch("mctl.kept.os.statvfs", return_value=fake):
            self.assertEqual(self.s.kept().boot(), 1)
        st = self.s.status()
        self.assertTrue(all(v.startswith("left as it is") for v in st["items"].values()), st)
        self.assertIn("not enough room", st["items"]["state"])
        self.assertEqual(sorted(os.listdir(self.s.p("var/lib/nxlx-kept"))), [".nxlx-kept"])      # not even a .new
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))

    def test_a_copy_cut_short_leaves_nothing_that_is_taken_for_a_kept_folder(self):
        self.ready()
        with mock.patch("mctl.kept.copy_tree", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.s.kept().boot(), 1)
        self.assertFalse(os.path.lexists(self.s.p("var/lib/nxlx-kept/state")))
        # a leftover .new from a power cut is removed and made again, never used
        os.makedirs(self.s.p("var/lib/nxlx-kept/state.new"))
        self.s.write(self.s.p("var/lib/nxlx-kept/state.new/half"), "x")
        self.s.write(self.s.p("proc/mounts"), "overlayroot / overlay rw 0 0\n")
        self.assertEqual(self.s.kept().boot(), 0)
        self.assertFalse(os.path.exists(self.s.p("var/lib/nxlx-kept/state.new")))
        self.assertFalse(os.path.exists(self.s.p("var/lib/nxlx-kept/state/half")))

    def test_the_filesystem_is_checked_once_if_the_first_mount_fails(self):
        self.ready()
        self.s.mount_fail_first = True
        self.assertEqual(self.s.kept().boot(), 0, self.s.logs)
        names = [c[0] for c in self.s.calls]
        self.assertEqual(names.count("e2fsck"), 1)
        self.s.reboot()
        self.s.node_folders()
        self.s.calls = []
        self.s.mount_rc = 32
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual([c[0] for c in self.s.calls].count("e2fsck"), 1)
        self.assertEqual(self.s.status()["state"], "missing")
        self.assertFalse(os.path.islink(self.s.p("var/lib/nxlx")))

    def test_running_it_twice_changes_nothing(self):
        self.ready()
        self.s.kept().boot()
        self.s.calls = []
        self.assertEqual(self.s.kept().boot(), 0)
        self.assertEqual([c for c in self.s.calls if c[0] == "mount"], [])
        self.assertEqual(set(self.s.status()["items"].values()), {"bound"})

    def test_a_node_without_the_panel_folder_has_nothing_to_bind(self):
        self.ready()
        shutil.rmtree(self.s.p("var/lib/nxlx-player"))
        self.assertEqual(self.s.kept().boot(), 0)
        self.assertEqual(self.s.status()["items"]["player"], "not on this node")


class EnableCheckTest(unittest.TestCase):
    def setUp(self):
        self.s = FakeSystem(self)
        self.s.node_folders()

    def test_no_kept_disk_set_up_is_refused_with_the_way_forward(self):
        with self.assertRaises(kept.KeptError) as cm:
            self.s.kept().check_for_enable("/var/lib/nxlx/video")
        self.assertIn("nxlx-kept prepare", str(cm.exception))
        self.assertIn("--without-kept", str(cm.exception))

    def test_a_disk_that_is_not_plugged_in_a_disk_of_the_wrong_kind_and_a_small_disk_are_refused(self):
        self.s.configure()
        with self.assertRaises(kept.KeptError) as cm:
            self.s.kept().check_for_enable()
        self.assertIn("not connected", str(cm.exception))
        self.s.add_disk_link()
        self.s.blkid["TYPE"] = "vfat"
        with self.assertRaises(kept.KeptError) as cm:
            self.s.kept().check_for_enable()
        self.assertIn("not ext4", str(cm.exception))
        self.s.blkid["TYPE"] = "ext4"
        self.s.blkid["FSSIZE"] = str(1024)
        with self.assertRaises(kept.KeptError) as cm:
            self.s.kept().check_for_enable()
        self.assertIn("too small", str(cm.exception))

    def test_a_damaged_config_is_refused(self):
        self.s.write(self.s.p("etc/nxlx/kept.json"), "{")
        with self.assertRaises(kept.KeptError):
            self.s.kept().check_for_enable()

    def test_a_ready_disk_passes_and_covers_the_media_folder(self):
        self.s.configure()
        self.s.add_disk_link()
        k = self.s.kept()
        self.assertTrue(k.check_for_enable("/var/lib/nxlx/video"))
        self.assertFalse(k.check_for_enable("/media/big-disk/video"))
        self.assertFalse(k.check_for_enable("/var/lib/nxlx-other/video"))         # a name that only starts the same
        self.assertEqual([c for c in self.s.calls if c[0] in ("mount", "mkfs.ext4")], [])        # a check only

    def test_the_folders_to_keep_must_fit(self):
        self.s.configure()
        self.s.add_disk_link()
        self.s.blkid["FSSIZE"] = str(kept.SPARE_BYTES + 100)
        self.s.write(self.s.p("var/lib/nxlx/video/clip.mp4"), "x" * 5000)
        with self.assertRaises(kept.KeptError) as cm:
            self.s.kept().check_for_enable("/var/lib/nxlx/video")
        self.assertIn("too small", str(cm.exception))


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.s = FakeSystem(self)
        self.s.write(self.s.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n")
        self.s.blkid["TYPE"] = ""           # a blank partition

    def prep(self, device="/dev/sda3", **kw):
        kw.setdefault("yes", True)
        return self.s.kept().prepare(device, **kw)

    def test_it_asks_for_a_yes_and_refuses_bad_paths_and_a_read_only_disk(self):
        for kw in ({"yes": False}, {"overlay_active": True}):
            with self.assertRaises(kept.KeptError):
                self.prep(**kw)
        for bad in ("sda3", "/dev/../etc/passwd", "/etc/passwd", "/dev/sda3; reboot"):
            with self.assertRaises(kept.KeptError):
                self.prep(bad)
        self.assertEqual([c for c in self.s.calls if c[0] == "mkfs.ext4"], [])

    def test_a_device_that_is_in_use_is_never_formatted(self):
        self.s.write(self.s.p("proc/mounts"), "%s /media/x ext4 rw 0 0\n" % self.s.disk)
        with self.assertRaises(kept.KeptError) as cm:
            self.prep("/dev/sda3")
        self.assertIn("in use", str(cm.exception))
        self.assertEqual([c for c in self.s.calls if c[0] == "mkfs.ext4"], [])

    def test_an_existing_filesystem_needs_the_wipe_word(self):
        self.s.blkid["TYPE"] = "ext4"
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("--wipe", str(cm.exception))
        self.assertEqual([c for c in self.s.calls if c[0] == "mkfs.ext4"], [])
        self.prep(wipe=True)
        self.assertEqual(len([c for c in self.s.calls if c[0] == "mkfs.ext4"]), 1)

    def test_it_makes_the_disk_writes_the_marker_and_saves_the_uuid(self):
        self.assertEqual(self.prep(), UUID)
        mkfs = [c for c in self.s.calls if c[0] == "mkfs.ext4"][0]
        self.assertIn("nxlx-kept", mkfs)
        with open(self.s.p("etc/nxlx/kept.json")) as f:
            self.assertEqual(json.load(f)["uuid"], UUID)
        with open(self.s.p("var/lib/nxlx-kept/.nxlx-kept/volume")) as f:
            self.assertEqual(f.read().strip(), UUID)
        self.assertEqual([c[0] for c in self.s.calls if c[0] in ("umount", "sync")][-1], "umount")     # not left mounted
        self.assertNotIn("nxlx-kept", self.s.mounts())

    def test_a_failed_format_saves_nothing(self):
        self.s.mkfs_rc = 1
        with self.assertRaises(kept.KeptError):
            self.prep()
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.json")))


class RootfsEnableTest(unittest.TestCase):
    """nxlx-rootfs enable with and without the kept disk (the existing rootfs tests stay in tests/test_rootfs.py)."""

    def setUp(self):
        self.s = FakeSystem(self)
        self.s.node_folders()
        self.s.write(self.s.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n")
        self.logs = []
        self.calls = []

    def runner(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def which(self, n):
        return "/usr/bin/" + n if n == "raspi-config" else None

    def change(self, action, keep, media="/var/lib/nxlx/video", **kw):
        rootfs.change(action, "pi4", media, runner=self.runner, proc_mounts=self.s.p("proc/mounts"), which=self.which,
                      log=self.logs.append, kept=keep, **kw)

    def test_enable_is_refused_until_the_kept_disk_is_ready_and_runs_nothing(self):
        with self.assertRaises(rootfs.RootfsError) as cm:
            self.change("enable", self.s.kept(), force=True)
        self.assertIn("nxlx-kept prepare", str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_enable_with_a_ready_disk_goes_ahead_and_the_media_folder_in_the_kept_state_is_fine(self):
        self.s.configure()
        self.s.add_disk_link()
        # the media folder is on "/" by device number here (a temp folder shares one filesystem): without the kept
        # disk this would be refused, with it the folder is covered
        with mock.patch("mctl.rootfs.media_on_root", return_value=True):
            self.change("enable", self.s.kept())
            self.assertEqual(self.calls, [["raspi-config", "nonint", "enable_overlayfs"]])
            self.assertIn("kept settings disk", self.logs[-1])
            self.calls.clear()
            with self.assertRaises(rootfs.RootfsError) as cm:
                self.change("enable", self.s.kept(), media="/home/pi/video")
            self.assertIn("system disk", str(cm.exception))

    def test_without_the_kept_object_it_behaves_as_before(self):
        self.change("enable", None, force=True)
        self.assertEqual(self.calls, [["raspi-config", "nonint", "enable_overlayfs"]])

    def test_disable_says_the_kept_disk_stays_in_use(self):
        self.s.configure()
        self.change("disable", self.s.kept())
        self.assertEqual(self.calls, [["raspi-config", "nonint", "disable_overlayfs"]])
        self.assertIn("stays in use", self.logs[-1])

    def test_status_shows_the_kept_disk(self):
        self.s.configure()
        self.s.add_disk_link()
        st = rootfs.status("pi4", "/var/lib/nxlx/video", proc_mounts=self.s.p("proc/mounts"), which=self.which, kept=self.s.kept())
        self.assertTrue(st["kept"]["configured"])
        self.assertTrue(st["kept"]["disk_present"])
        self.assertEqual(st["kept"]["uuid"], UUID)


class InstalledFilesTest(unittest.TestCase):
    def test_the_unit_only_acts_where_a_kept_disk_was_set_up_and_comes_before_the_services(self):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(repo, "install", "nxlx-kept.service")) as f:
            unit = f.read()
        self.assertIn("ConditionPathExists=/etc/nxlx/kept.json", unit)
        self.assertIn("ExecStart=@NXLX_DIR@/bin/nxlx-kept mount", unit)
        self.assertIn("ExecStop=@NXLX_DIR@/bin/nxlx-kept sync", unit)
        for before in ("sysinit.target", "NetworkManager.service", "nxlx-web.service", "nxlx-player.service", "nxlx-netd.service"):
            self.assertRegex(unit, r"(?m)^Before=.*\b%s\b" % before.replace(".", r"\."))
        self.assertTrue(os.access(os.path.join(repo, "bin", "nxlx-kept"), os.X_OK))


if __name__ == "__main__":
    unittest.main()
