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
SECRET = "a" * 64


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
        self.blkid_rc = {}                      # tag -> exit code, for a blkid that fails (exit 2 with no output is "nothing found")
        self.inactive = set()                   # units `systemctl is-active` says are not running
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

    def prepare_volume_files(self, marker=UUID, disk_secret=SECRET, node_secret=SECRET):
        """What `prepare` leaves on the disk (the marker and the secret) and on the node (the same secret, root only).
        (The disk itself is the folder where it is 'mounted'.)"""
        os.makedirs(self.p("var/lib/nxlx-kept/.nxlx-kept"), exist_ok=True)
        if marker is not None:
            self.write(self.p("var/lib/nxlx-kept/.nxlx-kept/volume"), marker + "\n")
            self.write(self.p("var/lib/nxlx-kept/.nxlx-kept/secret"), disk_secret + "\n")
        self.write(self.p("etc/nxlx/kept.secret"), node_secret + "\n")
        os.chmod(self.p("etc/nxlx/kept.secret"), 0o600)

    def seed_copies(self):
        """What `prepare` leaves on the disk besides that: a copy of each folder of the node."""
        for name, place, _create in kept.ITEMS:
            src, dst = self.p(place), self.p("var/lib/nxlx-kept/" + name)
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                os.makedirs(dst, mode=0o700)

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
            os.rename(dst, dst + ".under")    # what a bind mount hides stays below it: the system disk's own copy
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
            rc = self.blkid_rc.get(tag, 0 if out else 2)
        elif name == "systemctl" and cmd[1] == "is-active":
            rc = 3 if cmd[-1] in self.inactive else 0
        elif name == "mkfs.ext4":
            rc = self.mkfs_rc
            if not rc and not os.path.lexists(self.p("dev/disk/by-uuid/" + UUID)):
                self.add_disk_link()          # udev makes the by-uuid link of the new filesystem
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
            if os.path.exists(path + ".under"):
                os.rename(path + ".under", path)
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

    def test_a_link_out_of_the_folder_is_never_followed_or_copied(self):
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
        os.symlink("../outside", os.path.join(src, "up"))
        os.symlink("sub/../../outside", os.path.join(src, "up-deep"))
        os.mkfifo(os.path.join(src, "pipe"))
        with open(os.path.join(src, "real.json"), "w") as f:
            f.write("{}")
        dst = os.path.join(self.d, "dst")
        skipped = kept.copy_tree(src, dst)
        self.assertEqual(sorted(os.listdir(dst)), ["real.json"])
        self.assertEqual(sorted(skipped), ["dangling-ish", "pipe", "settings.json", "up", "up-deep", "video"])

    def test_a_relative_link_that_stays_in_its_folder_is_made_again(self):
        src = os.path.join(self.d, "src")
        os.makedirs(os.path.join(src, "loops"))
        with open(os.path.join(src, "loops", "a.mp4"), "w") as f:
            f.write("clip")
        os.symlink("a.mp4", os.path.join(src, "loops", "b.mp4"))
        os.symlink("loops/a.mp4", os.path.join(src, "top"))
        os.symlink("not-there-yet", os.path.join(src, "dangling"))        # its target need not exist
        skipped = kept.copy_tree(src, os.path.join(self.d, "dst"))
        self.assertEqual(skipped, [])
        self.assertEqual(os.readlink(os.path.join(self.d, "dst", "loops", "b.mp4")), "a.mp4")
        self.assertEqual(os.readlink(os.path.join(self.d, "dst", "top")), "loops/a.mp4")
        self.assertEqual(os.readlink(os.path.join(self.d, "dst", "dangling")), "not-there-yet")
        for bad in ("", "/abs", "..", "a/../b", "a//b", "a/", "../x", "./x/.."):
            self.assertFalse(kept.keepable_link(bad), bad)
        self.assertTrue(kept.keepable_link("x"))
        self.assertTrue(kept.keepable_link("a/b/c.mp4"))

    def test_hard_links_become_separate_files(self):
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        with open(os.path.join(src, "one"), "w") as f:
            f.write("same bytes")
        os.link(os.path.join(src, "one"), os.path.join(src, "two"))
        dst = os.path.join(self.d, "dst")
        self.assertEqual(kept.copy_tree(src, dst), [])
        a, b = os.stat(os.path.join(dst, "one")), os.stat(os.path.join(dst, "two"))
        self.assertNotEqual(a.st_ino, b.st_ino)
        self.assertEqual((a.st_nlink, b.st_nlink), (1, 1))
        for n in ("one", "two"):
            with open(os.path.join(dst, n)) as f:
                self.assertEqual(f.read(), "same bytes")

    def test_pipes_sockets_and_devices_are_left_out(self):
        import socket
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        os.mkfifo(os.path.join(src, "fifo"))
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(os.path.join(src, "sock"))
        with open(os.path.join(src, "plain"), "w") as f:
            f.write("x")
        dst = os.path.join(self.d, "dst")
        skipped = kept.copy_tree(src, dst)
        self.assertEqual(sorted(os.listdir(dst)), ["plain"])
        self.assertEqual(sorted(skipped), ["fifo", "sock"])
        # a character or block device is in the same branch (neither a file, a folder nor a link); making one needs root
        real_stat = os.stat

        def as_device(path, *a, **kw):
            st = real_stat(path, *a, **kw)
            if path == "plain":
                values = list(st)
                values[0] = stat.S_IFCHR | 0o600
                return os.stat_result(values)
            return st
        with mock.patch("mctl.kept.os.stat", side_effect=as_device):
            self.assertEqual(kept.copy_tree(src, os.path.join(self.d, "dst2")).count("plain"), 1)

    def test_nothing_is_flushed_file_by_file(self):
        src = os.path.join(self.d, "src")
        os.makedirs(os.path.join(src, "sub"))
        for i in range(30):
            with open(os.path.join(src, "sub", "f%d" % i), "w") as f:
                f.write("x")
        with mock.patch("mctl.kept.os.fsync") as fsync:
            kept.copy_tree(src, os.path.join(self.d, "dst"))
        self.assertEqual(fsync.call_count, 0)

    def test_a_name_that_is_not_utf8_is_copied(self):
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        raw = os.path.join(os.fsencode(src), b"caf\xe9.json")
        with open(raw, "wb") as f:
            f.write(b"{}")
        dst = os.path.join(self.d, "dst")
        self.assertEqual(kept.copy_tree(src, dst), [])
        self.assertTrue(os.path.exists(os.path.join(os.fsencode(dst), b"caf\xe9.json")))

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

    def ready(self, seeded=True):
        """A prepared disk: the config, the disk link, the marker and secret, and (by default) the folders on it."""
        self.s.configure()
        self.s.add_disk_link()
        self.s.prepare_volume_files()
        if seeded:
            self.s.seed_copies()

    def no_copy(self):
        return mock.patch("mctl.kept.copy_tree", side_effect=AssertionError("the boot must never copy"))

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

    def test_the_boot_mounts_and_binds_what_the_disk_holds_and_copies_nothing(self):
        self.ready()
        with self.no_copy():
            self.assertEqual(self.s.kept().boot(), 0, self.s.logs)
        st = self.s.status()
        self.assertEqual(st["state"], "active")
        self.assertEqual(st["items"]["state"], "bound")
        self.assertEqual(st["items"]["wifi"], "bound")
        self.assertEqual(st["items"]["netd"], "bound")
        self.assertEqual(st["items"]["support"], "bound")
        # the volume was mounted with the options that keep it safe, before anything was bound
        mount = [c for c in self.s.calls if c[0] == "mount" and c[1] == "-t"][0]
        self.assertIn("errors=remount-ro", mount[4])
        self.assertIn("nosuid", mount[4])
        self.assertEqual([c[1] for c in self.s.calls if c[0] == "mount"][0], "-t")
        self.assertEqual(len([c for c in self.s.calls if c[:2] == ["mount", "--bind"]]), 6)

    def test_a_folder_the_disk_does_not_hold_is_left_on_the_system_disk_and_said(self):
        self.ready()
        shutil.rmtree(self.s.p("var/lib/nxlx-kept/wifi"))
        with self.no_copy():
            self.assertEqual(self.s.kept().boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "partial")
        self.assertIn("left as it is", st["items"]["wifi"])
        self.assertIn("nxlx-kept seed", st["items"]["wifi"])
        self.assertEqual(st["items"]["state"], "bound")
        self.assertTrue(os.path.isfile(self.s.p("etc/NetworkManager/system-connections/venue.nmconnection")))
        self.assertFalse(os.path.islink(self.s.p("etc/NetworkManager/system-connections")))

    def test_a_write_after_boot_survives_a_restart(self):
        self.ready()
        self.assertEqual(self.s.kept().boot(), 0)
        # the panel changes the PIN and uploads a graphic, through the normal paths
        self.s.write(self.s.p("var/lib/nxlx/settings.json"), '{"pin": "9876"}')
        self.s.write(self.s.p("var/lib/nxlx/graphics/logo.png"), "PNG")
        self.s.write(self.s.p("etc/NetworkManager/system-connections/new.nmconnection"), "[wifi]\nssid=New\n")
        # power goes: the RAM layer is gone, the protected disk still holds what it held
        self.s.reboot()
        self.assertEqual(self.s.kept().boot(), 0)
        for path, text in (("var/lib/nxlx/settings.json", '{"pin": "9876"}'), ("var/lib/nxlx/graphics/logo.png", "PNG"),
                           ("etc/NetworkManager/system-connections/new.nmconnection", "[wifi]\nssid=New\n")):
            with open(self.s.p(path)) as f:
                self.assertEqual(f.read(), text, path)
        self.assertEqual(self.s.status()["items"]["state"], "bound")

    def test_a_missing_disk_leaves_everything_as_it_is_and_says_so_loudly(self):
        self.s.configure()                                                # no disk link: the disk is not there
        k = self.s.kept(wait_seconds=3)
        self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "missing")
        self.assertIn("not there", st["message"])
        self.assertIn("pair again with the PIN", st["message"])
        self.assertIn("Owner recovery codes", st["message"])
        self.assertEqual([c for c in self.s.calls if c[0] == "mount"], [])
        self.assertEqual(len([c for c in self.s.calls if c[0] == "sleep"]), 3)       # waited, and no longer than asked
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))
        self.assertFalse(os.path.islink(self.s.p("var/lib/nxlx")))

    def refused(self, why):
        self.assertEqual(self.s.kept().boot(), 1)
        self.assertEqual(self.s.status()["state"], "missing")
        self.assertIn(why, self.s.status()["message"])
        self.assertEqual([c for c in self.s.calls if c[:2] == ["mount", "--bind"]], [])
        self.s.calls = []
        self.s.write(self.s.p("proc/mounts"), "overlayroot / overlay rw 0 0\n")

    def test_a_disk_without_the_right_marker_or_secret_is_refused(self):
        self.s.configure()
        self.s.add_disk_link()
        self.s.prepare_volume_files(marker="ffffffff-0000-0000-0000-000000000000")
        self.s.seed_copies()
        self.refused("marker does not match")
        # no marker at all
        shutil.rmtree(self.s.p("var/lib/nxlx-kept/.nxlx-kept"))
        self.refused("no marker")
        # the right UUID on the disk but another secret (a stick prepared by someone else, or a copy of the marker)
        self.s.prepare_volume_files(disk_secret="b" * 64)
        self.refused("secret does not match")
        # this node has no secret of its own
        self.s.prepare_volume_files()
        os.unlink(self.s.p("etc/nxlx/kept.secret"))
        self.refused("kept.secret")
        # an empty secret on both sides is no secret
        self.s.prepare_volume_files(disk_secret="", node_secret="")
        self.refused("secret does not match")
        # the node's secret open to everyone is not trusted
        self.s.prepare_volume_files()
        os.chmod(self.s.p("etc/nxlx/kept.secret"), 0o644)
        self.refused("not private")
        os.chmod(self.s.p("etc/nxlx/kept.secret"), 0o600)
        self.assertEqual(self.s.kept().boot(), 0)             # and the right pair is accepted

    def test_a_marker_that_is_a_link_a_pipe_or_huge_cannot_hang_or_fool_the_boot(self):
        self.ready()
        meta = self.s.p("var/lib/nxlx-kept/.nxlx-kept")
        os.unlink(os.path.join(meta, "secret"))
        os.symlink("/dev/zero", os.path.join(meta, "secret"))           # an endless file behind a link
        self.refused("no marker")
        os.unlink(os.path.join(meta, "secret"))
        os.mkfifo(os.path.join(meta, "secret"))                          # a pipe nobody writes to
        self.refused("not a plain file")
        os.unlink(os.path.join(meta, "secret"))
        with open(os.path.join(meta, "secret"), "w") as f:
            f.write("a" * 5000)
        self.refused("too long")
        os.unlink(os.path.join(meta, "secret"))
        shutil.rmtree(meta)
        os.symlink(self.s.p("tmp"), meta)                                 # the marker folder itself a link
        os.makedirs(self.s.p("tmp"))
        self.refused("no marker")

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

    def test_a_covered_folder_that_is_a_link_is_left_alone(self):
        self.ready()
        shutil.rmtree(self.s.p("var/lib/nxlx"))
        os.makedirs(self.s.p("elsewhere"))
        os.symlink(self.s.p("elsewhere"), self.s.p("var/lib/nxlx"))
        k = self.s.kept()
        self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "partial")
        self.assertTrue(st["items"]["state"].startswith("left as it is"))
        self.assertEqual(st["items"]["wifi"], "bound")          # the others are still kept

    def test_a_kept_copy_that_is_a_link_is_not_used(self):
        self.ready()
        shutil.rmtree(self.s.p("var/lib/nxlx-kept/state"))
        os.makedirs(self.s.p("etc/evil"))
        os.symlink(self.s.p("etc/evil"), self.s.p("var/lib/nxlx-kept/state"))
        k = self.s.kept()
        self.assertEqual(k.boot(), 1)
        self.assertTrue(self.s.status()["items"]["state"].startswith("left as it is"))
        self.assertNotIn(["mount", "--bind", self.s.p("var/lib/nxlx-kept/state"), self.s.p("var/lib/nxlx")], self.s.calls)

    def test_the_filesystem_is_checked_once_if_the_first_mount_fails(self):
        self.ready()
        self.s.mount_fail_first = True
        self.assertEqual(self.s.kept().boot(), 0, self.s.logs)
        names = [c[0] for c in self.s.calls]
        self.assertEqual(names.count("e2fsck"), 1)
        self.s.reboot()
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
        shutil.rmtree(self.s.p("var/lib/nxlx-kept/player"))
        self.assertEqual(self.s.kept().boot(), 0)
        self.assertEqual(self.s.status()["items"]["player"], "not on this node")

    def test_one_folders_failure_of_any_kind_never_stops_the_next_and_the_boot_never_raises(self):
        self.ready()
        k = self.s.kept()
        real = k._bind

        def sometimes(name, place, create):
            if name == "state":
                raise RuntimeError("a surprise")
            if name == "player":
                raise UnicodeEncodeError("ascii", "x", 0, 1, "odd")
            return real(name, place, create)
        k._bind = sometimes
        self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "partial")
        self.assertIn("a surprise", st["items"]["state"])
        self.assertTrue(st["items"]["player"].startswith("left as it is"))
        self.assertEqual(st["items"]["wifi"], "bound")
        self.assertEqual(st["items"]["support"], "bound")

    def test_even_a_failure_outside_the_folders_writes_the_status_and_returns(self):
        self.ready()
        k = self.s.kept()
        with mock.patch.object(k, "_mount_volume", side_effect=RuntimeError("boom")):
            self.assertEqual(k.boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "refused")
        self.assertIn("boom", st["message"])

    def test_a_name_that_is_not_utf8_cannot_stop_the_boot_or_the_logging(self):
        self.ready()
        raw = os.path.join(os.fsencode(self.s.p("var/lib/nxlx-kept/state")), b"caf\xe9.json")
        with open(raw, "wb") as f:
            f.write(b"{}")
        k = self.s.kept()
        real = k._bind
        bad = [n for n in os.listdir(self.s.p("var/lib/nxlx-kept/state")) if "caf" in n][0]      # holds a lone surrogate

        def failing(name, place, create):
            if name == "state":
                raise kept.KeptError("trouble with " + bad)
            return real(name, place, create)
        k._bind = failing
        self.assertEqual(k.boot(), 1)
        for m in self.s.logs:
            m.encode("utf-8")                # a lone surrogate would raise here
        json.dumps(self.s.status())          # and the status file holds it as an escape
        self.assertIn("caf", self.s.status()["items"]["state"])
        self.assertEqual(self.s.status()["items"]["wifi"], "bound")
        self.assertEqual(kept._safe("caf\udce9"), "caf\\udce9")


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

    def test_a_media_folder_that_is_a_link_is_not_covered(self):
        self.s.configure()
        self.s.add_disk_link()
        elsewhere = self.s.p("mnt/big/video")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, self.s.p("var/lib/nxlx/video"))                    # looks like it is in the kept folder, is not
        k = self.s.kept()
        self.assertFalse(k.check_for_enable("/var/lib/nxlx/video"))
        os.unlink(self.s.p("var/lib/nxlx/video"))
        os.makedirs(self.s.p("var/lib/nxlx/real/deep"))
        os.symlink(self.s.p("var/lib/nxlx/real"), self.s.p("var/lib/nxlx/shortcut"))
        self.assertFalse(k.check_for_enable("/var/lib/nxlx/shortcut/deep"))      # reached through a link higher up
        self.assertTrue(k.check_for_enable("/var/lib/nxlx/real/deep"))

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
        self.s.node_folders()
        self.s.write(self.s.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n")
        self.s.blkid["TYPE"] = ""           # a blank partition

    def prep(self, device="/dev/sda3", **kw):
        kw.setdefault("yes", True)
        return self.s.kept().prepare(device, **kw)

    def nothing_written(self):
        self.assertEqual([c for c in self.s.calls if c[0] in ("mkfs.ext4", "mount", "umount", "systemctl")], [])
        self.assertFalse(os.path.lexists(self.s.p("etc/nxlx/kept.json")))
        self.assertFalse(os.path.lexists(self.s.p("etc/nxlx/kept.secret")))
        self.assertFalse(os.path.lexists(self.s.p("var/lib/nxlx-kept")))

    def test_it_asks_for_a_yes_and_refuses_bad_paths_and_a_read_only_disk(self):
        for kw in ({"yes": False}, {"overlay_active": True}):
            with self.assertRaises(kept.KeptError):
                self.prep(**kw)
        for bad in ("sda3", "/dev/../etc/passwd", "/etc/passwd", "/dev/sda3; reboot"):
            with self.assertRaises(kept.KeptError):
                self.prep(bad)
        self.nothing_written()

    def test_a_device_that_is_in_use_is_never_formatted(self):
        self.s.write(self.s.p("proc/mounts"), "%s /media/x ext4 rw 0 0\n" % self.s.disk)
        with self.assertRaises(kept.KeptError) as cm:
            self.prep("/dev/sda3")
        self.assertIn("in use", str(cm.exception))
        self.nothing_written()

    def test_the_disk_that_holds_the_system_is_refused_even_with_wipe(self):
        sysno = 8 * 256 + 1
        with mock.patch.object(kept.Kept, "_system_devnos", return_value={sysno}), \
                mock.patch.object(kept.Kept, "_devnos_of", return_value={sysno, 8 * 256 + 2}):
            for kw in ({}, {"wipe": True}):
                with self.assertRaises(kept.KeptError) as cm:
                    self.prep("/dev/sda3", **kw)
                self.assertIn("holds the system", str(cm.exception))
        self.nothing_written()

    def test_an_existing_filesystem_needs_the_wipe_word(self):
        self.s.blkid["TYPE"] = "ext4"
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("--wipe", str(cm.exception))
        self.nothing_written()
        self.prep(wipe=True)
        self.assertEqual(len([c for c in self.s.calls if c[0] == "mkfs.ext4"]), 1)

    def test_a_whole_disk_with_a_partition_table_needs_the_wipe_word(self):
        self.s.blkid["PTTYPE"] = "dos"            # blkid prints no TYPE for such a disk, and exits 0 with only PTTYPE
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("partition table", str(cm.exception))
        self.nothing_written()
        self.prep(wipe=True)
        self.assertEqual(len([c for c in self.s.calls if c[0] == "mkfs.ext4"]), 1)

    def test_a_whole_disk_with_partitions_in_sysfs_needs_the_wipe_word(self):
        self.s.write(self.s.p("sys/class/block/sda3/sda3p1/partition"), "1\n")
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("sda3p1", str(cm.exception))
        self.nothing_written()

    def test_a_blkid_that_fails_means_not_blank(self):
        self.s.blkid_rc["TYPE"] = 1                # an error, not "nothing found"
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("not known to be blank", str(cm.exception))
        self.nothing_written()
        self.s.blkid_rc.clear()
        self.s.blkid_rc["PTTYPE"] = 4
        with self.assertRaises(kept.KeptError):
            self.prep(wipe=True)
        self.nothing_written()
        self.s.blkid_rc.clear()
        self.prep()                                # exit 2 with no output is a blank device

    def test_every_check_is_made_before_anything_is_written(self):
        # already a mount point
        self.s.write(self.s.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n/dev/sdb1 %s ext4 rw 0 0\n" % self.s.p("var/lib/nxlx-kept"))
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("already a mount point", str(cm.exception))
        self.assertEqual([c for c in self.s.calls if c[0] in ("mkfs.ext4", "mount", "systemctl")], [])
        self.s.write(self.s.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n")
        # a config is already there
        self.s.configure()
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("already set up", str(cm.exception))
        self.assertEqual([c for c in self.s.calls if c[0] in ("mkfs.ext4", "mount", "systemctl")], [])
        os.unlink(self.s.p("etc/nxlx/kept.json"))
        # the read-only disk is on
        with self.assertRaises(kept.KeptError):
            self.prep(overlay_active=True)
        # a covered folder that is a link
        shutil.rmtree(self.s.p("var/lib/nxlx-player"))
        os.symlink(self.s.p("tmp"), self.s.p("var/lib/nxlx-player"))
        with self.assertRaises(kept.KeptError) as cm:
            self.prep()
        self.assertIn("link", str(cm.exception))
        self.nothing_written()

    def test_it_makes_the_disk_writes_the_marker_and_secret_and_saves_the_uuid(self):
        self.assertEqual(self.prep(), UUID)
        mkfs = [c for c in self.s.calls if c[0] == "mkfs.ext4"][0]
        self.assertIn("nxlx-kept", mkfs)
        with open(self.s.p("etc/nxlx/kept.json")) as f:
            self.assertEqual(json.load(f)["uuid"], UUID)
        with open(self.s.p("var/lib/nxlx-kept/.nxlx-kept/volume")) as f:
            self.assertEqual(f.read().strip(), UUID)
        with open(self.s.p("var/lib/nxlx-kept/.nxlx-kept/secret")) as f:
            secret = f.read().strip()
        self.assertRegex(secret, r"^[0-9a-f]{64}$")
        with open(self.s.p("etc/nxlx/kept.secret")) as f:
            self.assertEqual(f.read().strip(), secret)
        self.assertEqual(stat.S_IMODE(os.stat(self.s.p("etc/nxlx/kept.secret")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.s.p("var/lib/nxlx-kept/.nxlx-kept/secret")).st_mode), 0o600)
        # a second disk gets another secret
        s2 = FakeSystem(self)
        s2.node_folders()
        s2.write(s2.p("proc/mounts"), "/dev/mmcblk0p2 / ext4 rw 0 0\n")
        s2.blkid["TYPE"] = ""
        s2.kept().prepare("/dev/sda3", yes=True)
        with open(s2.p("etc/nxlx/kept.secret")) as f:
            self.assertNotEqual(f.read().strip(), secret)

    def test_the_services_are_stopped_for_the_copy_and_started_after_it_with_one_flush(self):
        syncs = []
        with mock.patch.object(kept.Kept, "_syncfs", lambda self_, path: syncs.append(path)):
            self.prep()
        stops = [i for i, c in enumerate(self.s.calls) if c[:2] == ["systemctl", "stop"]]
        starts = [i for i, c in enumerate(self.s.calls) if c[:2] == ["systemctl", "start"]]
        binds = [i for i, c in enumerate(self.s.calls) if c[:2] == ["mount", "--bind"]]
        self.assertEqual(len(stops), len(kept.SERVICES))
        self.assertEqual(len(starts), len(kept.SERVICES))
        self.assertEqual([self.s.calls[i][-1] for i in stops], list(reversed(kept.SERVICES)))
        self.assertEqual([self.s.calls[i][-1] for i in starts], list(kept.SERVICES))
        self.assertLess(max(stops), min(binds))
        self.assertLess(max(binds), min(starts))
        self.assertEqual(len(syncs), 1, syncs)             # one flush of the kept disk for all the copying
        self.assertEqual(len(binds), 6)
        with open(self.s.p("var/lib/nxlx-kept/state/settings.json")) as f:
            self.assertEqual(f.read(), '{"pin": "1234"}')
        self.assertEqual(stat.S_IMODE(os.stat(self.s.p("var/lib/nxlx-kept/update")).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.s.p("var/lib/nxlx-kept/netd")).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.s.p("var/lib/nxlx-kept/wifi/venue.nmconnection")).st_mode), 0o600)
        self.assertEqual(os.path.islink(self.s.p("var/lib/nxlx")), True)       # (the fake bind is a link) the node runs on it now

    def test_a_service_that_was_not_running_is_not_started(self):
        self.s.inactive = {"nxlx-player.service", "nxlx-supportd.service"}
        self.prep()
        started = [c[-1] for c in self.s.calls if c[:2] == ["systemctl", "start"]]
        self.assertEqual(started, ["nxlx-netd.service", "nxlx-sysd.service", "nxlx-web.service"])

    def test_what_grants_access_is_taken_out_of_the_system_disks_old_copy(self):
        settings = {"schema": 13, "auth": {"pin_hash": "aa", "pin_salt": "bb"}, "scenes": [{"name": "Show"}],
                    "devices": [{"id": "d1", "name": "Owner phone", "token_hash": "cc"}], "recovery": {"codes": ["x"]}}
        self.s.write(self.s.p("var/lib/nxlx/settings.json"), json.dumps(settings))
        for extra in ("settings.json.bak", "settings.json.rolled-back-5", "settings.json.before-import-7", "pairings.json",
                      ".settings-ab12cd", "scenes/one.json"):
            self.s.write(self.s.p("var/lib/nxlx/" + extra), json.dumps(settings))
        self.s.write(self.s.p("var/lib/nxlx-update/settings-1.2.3-9.json"), json.dumps(settings))
        self.s.write(self.s.p("var/lib/nxlx-update/clip-stick.json"), "{}")
        self.s.write(self.s.p("var/lib/nxlx-support/private.key"), "KEY\n")
        self.s.write(self.s.p("var/lib/nxlx-support/private.key.tmp"), "KEY\n")
        self.prep()
        under = lambda p: self.s.p(p + ".under")          # the system disk's copy, hidden below the bind  # noqa: E731
        with open(under("var/lib/nxlx") + "/settings.json") as f:
            old = json.load(f)
        self.assertEqual(old["devices"], [])
        self.assertNotIn("recovery", old)
        self.assertEqual(old["auth"], settings["auth"])                 # the PIN stays: pairing needs it
        self.assertEqual(old["scenes"], settings["scenes"])             # and the room still runs
        for gone in ("settings.json.bak", "settings.json.rolled-back-5", "settings.json.before-import-7", "pairings.json", ".settings-ab12cd"):
            self.assertFalse(os.path.lexists(under("var/lib/nxlx") + "/" + gone), gone)
        self.assertTrue(os.path.exists(under("var/lib/nxlx") + "/scenes/one.json"))
        self.assertFalse(os.path.lexists(under("var/lib/nxlx-update") + "/settings-1.2.3-9.json"))
        self.assertTrue(os.path.exists(under("var/lib/nxlx-update") + "/clip-stick.json"))
        self.assertFalse(os.path.lexists(under("var/lib/nxlx-support") + "/private.key"))
        self.assertFalse(os.path.lexists(under("var/lib/nxlx-support") + "/private.key.tmp"))
        # the kept copy has all of it
        with open(self.s.p("var/lib/nxlx-kept/state/settings.json")) as f:
            self.assertEqual(json.load(f)["devices"], settings["devices"])
        self.assertTrue(os.path.exists(self.s.p("var/lib/nxlx-kept/state/settings.json.bak")))
        self.assertTrue(os.path.exists(self.s.p("var/lib/nxlx-kept/support/private.key")))
        # a restart with the kept disk gone: the node runs on the emptied copy, says missing, and holds no device
        self.s.reboot()
        os.unlink(self.s.p("dev/disk/by-uuid/" + UUID))
        self.assertEqual(self.s.kept().boot(), 1)
        st = self.s.status()
        self.assertEqual(st["state"], "missing")
        self.assertIn("pair again with the PIN", st["message"])
        with open(self.s.p("var/lib/nxlx/settings.json")) as f:
            self.assertEqual(json.load(f)["devices"], [])
        self.assertFalse(os.path.islink(self.s.p("var/lib/nxlx")))

    def test_a_settings_file_that_is_a_link_or_unreadable_is_not_followed_and_is_removed_from_the_old_copy(self):
        secret = self.s.p("outside.json")
        self.s.write(secret, '{"devices": [1]}')
        os.unlink(self.s.p("var/lib/nxlx/settings.json"))
        os.symlink(secret, self.s.p("var/lib/nxlx/settings.json"))
        self.prep()
        self.assertFalse(os.path.lexists(self.s.p("var/lib/nxlx.under/settings.json")))
        with open(secret) as f:
            self.assertEqual(f.read(), '{"devices": [1]}')               # never rewritten through the link

    def test_a_failed_copy_leaves_the_node_as_it_was(self):
        real = kept.copy_tree
        calls = []

        def second_fails(src, dst, chown=None):
            calls.append(src)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            return real(src, dst, chown)
        with mock.patch("mctl.kept.copy_tree", side_effect=second_fails):
            with self.assertRaises(OSError):
                self.prep()
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.json")))
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.secret")))
        self.assertEqual(sorted(os.listdir(self.s.p("var/lib/nxlx-kept"))), [".nxlx-kept"])      # the copies made are removed again
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))
        with open(self.s.p("var/lib/nxlx/settings.json")) as f:
            self.assertEqual(f.read(), '{"pin": "1234"}')                 # nothing of the old copy was taken out
        self.assertFalse(os.path.islink(self.s.p("var/lib/nxlx")))
        self.assertEqual(len([c for c in self.s.calls if c[:2] == ["systemctl", "start"]]), len(kept.SERVICES))   # services are back
        self.assertIn("umount", [c[0] for c in self.s.calls])              # the new filesystem is not left mounted

    def test_a_full_kept_disk_keeps_nothing_half_copied(self):
        fake = os.statvfs_result((4096, 4096, 100, 1, 1, 0, 0, 0, 0, 255))     # a few KB free
        with mock.patch("mctl.kept.os.statvfs", return_value=fake):
            with self.assertRaises(kept.KeptError) as cm:
                self.prep()
        self.assertIn("not enough room", str(cm.exception))
        self.assertEqual(sorted(os.listdir(self.s.p("var/lib/nxlx-kept"))), [".nxlx-kept"])      # not even a .new
        self.assertTrue(os.path.isfile(self.s.p("var/lib/nxlx/settings.json")))
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.json")))

    def test_a_name_that_is_not_utf8_in_the_panels_folder_is_copied_and_logged_safely(self):
        raw = os.path.join(os.fsencode(self.s.p("var/lib/nxlx")), b"caf\xe9.json")
        with open(raw, "wb") as f:
            f.write(b"{}")
        os.symlink("/etc/shadow", os.path.join(os.fsencode(self.s.p("var/lib/nxlx")), b"bad\xffname"))
        self.prep()
        self.assertTrue(os.path.exists(os.path.join(os.fsencode(self.s.p("var/lib/nxlx-kept/state")), b"caf\xe9.json")))
        for m in self.s.logs:
            m.encode("utf-8")
        self.assertTrue(any("bad\\udcffname" in m for m in self.s.logs), self.s.logs)
        self.assertEqual(self.s.kept().boot(), 0)

    def test_links_in_the_state_folder_are_copied_only_if_relative_and_inside(self):
        os.makedirs(self.s.p("var/lib/nxlx/loops"))
        self.s.write(self.s.p("var/lib/nxlx/video/a.mp4"), "clip")
        os.symlink(self.s.p("var/lib/nxlx/video/a.mp4"), self.s.p("var/lib/nxlx/loops/abc.mp4"))   # what the loop lists make: absolute
        os.symlink("a.mp4", self.s.p("var/lib/nxlx/video/b.mp4"))
        self.prep()
        self.assertEqual(os.readlink(self.s.p("var/lib/nxlx-kept/state/video/b.mp4")), "a.mp4")
        self.assertFalse(os.path.lexists(self.s.p("var/lib/nxlx-kept/state/loops/abc.mp4")))
        self.assertTrue(any("loops/abc.mp4" in m for m in self.s.logs))

    def test_seed_copies_a_folder_the_disk_does_not_hold_yet(self):
        self.prep()
        shutil.rmtree(self.s.p("var/lib/nxlx-kept/wifi"))
        self.s.reboot()
        self.s.write(self.s.p("etc/NetworkManager/system-connections/new.nmconnection"), "[wifi]\nssid=New\n")
        self.s.write(self.s.p("var/lib/nxlx-kept/state/settings.json"), "newer than the old copy")
        self.s.calls = []
        result = self.s.kept().seed()
        self.assertEqual(result["wifi"], "seeded")
        self.assertEqual(result["state"], "already on the kept disk")
        with open(self.s.p("var/lib/nxlx-kept/wifi/new.nmconnection")) as f:
            self.assertIn("ssid=New", f.read())
        with open(self.s.p("var/lib/nxlx-kept/state/settings.json")) as f:
            self.assertEqual(f.read(), "newer than the old copy")
        self.assertEqual(len([c for c in self.s.calls if c[:2] == ["systemctl", "stop"]]), len(kept.SERVICES))
        with self.assertRaises(kept.KeptError):
            self.s.kept().seed(overlay_active=True)

    def test_a_failed_format_saves_nothing(self):
        self.s.mkfs_rc = 1
        with self.assertRaises(kept.KeptError):
            self.prep()
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.json")))
        self.assertFalse(os.path.exists(self.s.p("etc/nxlx/kept.secret")))


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
