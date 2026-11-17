# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's own clip stick (D143): label and marker matching, fsck before the writable mount, every other stick
read-only, the media folder falling back to the SD card when the stick is missing, and writes on a filesystem without
hard links (exFAT)."""
import errno
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from mctl import clipstick, usb
import tests        # the run's own temp folder and the locks' checker (tests/__init__.py)

NODE = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"


def marker_bytes(node_id=NODE, label="N01-CLIPS", fmt=1):
    return json.dumps({"nxlx_clip_stick": fmt, "node_id": node_id, "node_name": "nxlx-node-01", "label": label,
                       "claimed": "2026-11-17T12:00:00Z"}).encode()


class Stick:
    """A stand-in for blkid, mount, umount and fsck.exfat. What is "on the stick" (top-level files and folders) is put
    into a folder when it is mounted there, and read back and cleared when it is unmounted, so the code under test
    reads and writes real files."""

    def __init__(self, blkid, files=None, fsck_rc=0, fsck_missing=False, refuse_errors_opt=False):
        self.blkid, self.files, self.dirs = blkid, dict(files or {}), set()
        self.fsck_rc, self.fsck_missing, self.refuse_errors_opt = fsck_rc, fsck_missing, refuse_errors_opt
        self.calls, self.mounted = [], {}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        name = cmd[0]
        if name == "blkid":
            out = self.blkid.get(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0 if out else 2, out or "", "")
        if name == "fsck.exfat":
            if self.fsck_missing:
                raise FileNotFoundError("fsck.exfat")
            return subprocess.CompletedProcess(cmd, self.fsck_rc, "", "exfatprogs: ERROR: bad cluster" if self.fsck_rc > 1 else "")
        if name == "mount":
            opts, target = cmd[4], cmd[-1]
            if self.refuse_errors_opt and "errors=" in opts:
                return subprocess.CompletedProcess(cmd, 32, "", "mount: bad option")
            for n, data in self.files.items():
                with open(os.path.join(target, n), "wb") as f:
                    f.write(data)
            for d in self.dirs:
                os.makedirs(os.path.join(target, d), exist_ok=True)
            self.mounted[target] = opts
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if name == "umount":
            target = cmd[-1]
            if target in self.mounted:
                del self.mounted[target]
                self.files, self.dirs = {}, set()
                for e in os.scandir(target):
                    if e.is_dir(follow_symlinks=False):
                        self.dirs.add(e.name)
                        shutil.rmtree(e.path)
                    else:
                        with open(e.path, "rb") as f:
                            self.files[e.name] = f.read()
                        os.unlink(e.path)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def kinds(self):
        return [c[0] for c in self.calls]


EXFAT_N01 = {"/dev/sdb2": "TYPE=exfat\nLABEL=N01-CLIPS\nUUID=1\n"}


class MarkerTest(unittest.TestCase):
    def test_label_must_be_nxx_clips(self):
        for good in ("N01-CLIPS", "N03-CLIPS", "N99-CLIPS"):
            self.assertTrue(clipstick.label_ok(good), good)
        for bad in ("N1-CLIPS", "n01-clips", "N01-KIT", "N01-CLIPS ", "NXLX-USB", "", None, "N001-CLIPS"):
            self.assertFalse(clipstick.label_ok(bad), bad)

    def test_a_candidate_is_exfat_with_the_label(self):
        self.assertTrue(clipstick.candidate({"TYPE": "exfat", "LABEL": "N01-CLIPS"}))
        self.assertFalse(clipstick.candidate({"TYPE": "vfat", "LABEL": "N01-CLIPS"}))
        self.assertFalse(clipstick.candidate({"TYPE": "exfat", "LABEL": "N01-KIT"}))

    def test_the_marker_matches_only_this_node_and_this_label(self):
        m = json.loads(marker_bytes())
        self.assertTrue(clipstick.marker_matches(m, NODE, "N01-CLIPS"))
        self.assertFalse(clipstick.marker_matches(m, OTHER, "N01-CLIPS"))          # another node's stick
        self.assertFalse(clipstick.marker_matches(m, NODE, "N03-CLIPS"))           # the label was changed since
        self.assertFalse(clipstick.marker_matches(dict(m, nxlx_clip_stick=2), NODE, "N01-CLIPS"))
        self.assertFalse(clipstick.marker_matches(dict(m, nxlx_clip_stick=True), NODE, "N01-CLIPS"))
        self.assertFalse(clipstick.marker_matches(m, None, "N01-CLIPS"))           # a node without an id matches nothing
        self.assertFalse(clipstick.marker_matches(dict(m, node_id=""), "", "N01-CLIPS"))
        self.assertFalse(clipstick.marker_matches(None, NODE, "N01-CLIPS"))
        self.assertFalse(clipstick.marker_matches(["x"], NODE, "N01-CLIPS"))

    def test_the_marker_is_read_only_from_a_small_regular_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(clipstick.read_marker(d))
        path = os.path.join(d, clipstick.MARKER)
        with open(path, "wb") as f:
            f.write(marker_bytes())
        self.assertEqual(clipstick.read_marker(d)["node_id"], NODE)
        with open(path, "wb") as f:
            f.write(b"{" + b" " * clipstick.MARKER_MAX + b"}")
        self.assertIsNone(clipstick.read_marker(d))                                # too big
        with open(path, "wb") as f:
            f.write(b"not json")
        self.assertIsNone(clipstick.read_marker(d))
        os.unlink(path)
        real = os.path.join(d, "real.json")
        with open(real, "wb") as f:
            f.write(marker_bytes())
        os.symlink(real, path)
        self.assertIsNone(clipstick.read_marker(d))                                # never through a link
        os.unlink(path)
        os.mkdir(path)
        self.assertIsNone(clipstick.read_marker(d))                                # a folder is not a marker

    def test_writing_the_marker_leaves_no_temporary_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        clipstick.write_marker(d, clipstick.make_marker(NODE, "nxlx-node-01", "N01-CLIPS", now=0))
        self.assertEqual(os.listdir(d), [clipstick.MARKER])
        self.assertTrue(clipstick.marker_matches(clipstick.read_marker(d), NODE, "N01-CLIPS"))
        with self.assertRaises(clipstick.ClipStickError):
            clipstick.make_marker(None, "x", "N01-CLIPS")
        with self.assertRaises(clipstick.ClipStickError):
            clipstick.make_marker(NODE, "x", "SHOW")


class MountBase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.join(self.tmp, "nxlx")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        with open(os.path.join(self.state, "node-id"), "w") as f:
            f.write(NODE + "\n")
        self.status = os.path.join(self.tmp, "run", "clip-stick.json")
        self.env = {"NXLX_USB_BASE": self.base, "NXLX_USB_LINK": os.path.join(self.tmp, "usb"), "NXLX_STATE_DIR": self.state,
                    "NXLX_USB_PEEK": os.path.join(self.tmp, "peek"), "NXLX_CLIP_STATUS": self.status}
        self.mounts = os.path.join(self.tmp, "mounts")
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n")
        self.logs = []

    def set_mounts(self, text):
        with open(self.mounts, "w") as f:
            f.write(text)

    def mount(self, stick, env=None):
        return usb.mount("/dev/sdb2", env=env or self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append,
                         system_disks=set())

    def final_mount(self, stick):
        """The mount at the drive's folder (not the private look for the marker)."""
        found = [i for i, c in enumerate(stick.calls) if c[0] == "mount" and c[-1].startswith(self.base + os.sep)]
        self.assertTrue(found, stick.calls)
        return found[-1], stick.calls[found[-1]]

    def status_file(self):
        try:
            with open(self.status) as f:
                return json.load(f)
        except FileNotFoundError:
            return None


class MountTest(MountBase):
    def test_own_stick_is_checked_with_fsck_before_it_is_mounted_writable(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()})
        point = self.mount(stick)
        self.assertEqual(point, os.path.join(self.base, "N01-CLIPS"))
        kinds = stick.kinds()
        self.assertEqual(kinds, ["blkid", "mount", "umount", "fsck.exfat", "mount"])
        self.assertTrue(stick.calls[1][4].startswith("ro,"), "the look for the marker is read-only")
        self.assertEqual(stick.calls[3], ["fsck.exfat", "-p", "/dev/sdb2"])
        at, cmd = self.final_mount(stick)
        self.assertLess(kinds.index("fsck.exfat"), at)
        opts = cmd[4].split(",")
        self.assertEqual(opts[0], "rw")
        for o in ("nosuid", "nodev", "noexec", "noatime", "errors=remount-ro"):
            self.assertIn(o, opts)
        self.assertTrue(os.path.isdir(os.path.join(point, clipstick.CLIP_FOLDER)))
        self.assertEqual(self.status_file(), {"device": "/dev/sdb2", "point": point, "label": "N01-CLIPS", "writable": True, "why": ""})

    def test_fsck_that_corrected_errors_still_mounts_writable(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()}, fsck_rc=1)
        self.mount(stick)
        self.assertTrue(self.final_mount(stick)[1][4].startswith("rw,"))

    def test_a_failed_or_missing_fsck_mounts_the_own_stick_read_only(self):
        for kw, words in (({"fsck_rc": 4}, "exit 4"), ({"fsck_missing": True}, "not installed")):
            with self.subTest(**kw):
                stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()}, **kw)
                point = self.mount(stick, dict(self.env, NXLX_USB_RW="1"))      # not even with the old switch
                self.assertTrue(self.final_mount(stick)[1][4].startswith("ro,"))
                st = self.status_file()
                self.assertFalse(st["writable"])
                self.assertIn(words, st["why"])
                usb.unmount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
                self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n")
                if os.path.isdir(point):
                    shutil.rmtree(point)

    def test_another_nodes_stick_stays_read_only_and_is_not_checked(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes(node_id=OTHER)})
        self.mount(stick)
        self.assertNotIn("fsck.exfat", stick.kinds())
        self.assertTrue(self.final_mount(stick)[1][4].startswith("ro,"))
        self.assertNotIn("errors=remount-ro", self.final_mount(stick)[1][4])
        self.assertIsNone(self.status_file())
        self.assertTrue(any("another node" in m for m in self.logs))

    def test_the_label_alone_is_not_enough(self):
        stick = Stick(EXFAT_N01)                                   # Nxx-CLIPS but no marker
        self.mount(stick)
        self.assertNotIn("fsck.exfat", stick.kinds())
        self.assertTrue(self.final_mount(stick)[1][4].startswith("ro,"))
        self.assertIsNone(self.status_file())

    def test_the_marker_alone_is_not_enough(self):
        for blkid in ({"/dev/sdb2": "TYPE=exfat\nLABEL=SHOW\n"}, {"/dev/sdb2": "TYPE=vfat\nLABEL=N01-CLIPS\n"}):
            with self.subTest(blkid=blkid):
                stick = Stick(blkid, {clipstick.MARKER: marker_bytes()})
                self.mount(stick)
                self.assertEqual(stick.kinds(), ["blkid", "mount"])     # no look, no check, read-only as before
                self.assertTrue(stick.calls[-1][4].startswith("ro,"))
                self.assertIsNone(self.status_file())
                shutil.rmtree(self.base)

    def test_a_node_without_an_id_mounts_every_stick_read_only(self):
        os.unlink(os.path.join(self.state, "node-id"))
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()})
        self.mount(stick)
        self.assertEqual(stick.kinds(), ["blkid", "mount"])
        self.assertTrue(stick.calls[-1][4].startswith("ro,"))

    def test_a_driver_without_errors_remount_ro_is_mounted_without_it_and_says_so(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()}, refuse_errors_opt=True)
        point = self.mount(stick)
        opts = self.final_mount(stick)[1][4]
        self.assertTrue(opts.startswith("rw,"))
        self.assertNotIn("errors=", opts)
        self.assertTrue(any("errors=remount-ro" in m for m in self.logs))
        self.assertEqual(point, os.path.join(self.base, "N01-CLIPS"))

    def test_other_sticks_follow_the_old_switch_as_before(self):
        stick = Stick({"/dev/sdb2": "TYPE=exfat\nLABEL=SHOW\n"})
        self.mount(stick, dict(self.env, NXLX_USB_RW="1"))
        self.assertTrue(stick.calls[-1][4].startswith("rw,"))
        self.assertNotIn("errors=", stick.calls[-1][4])

    def test_unmount_takes_the_library_back_to_the_sd_card(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()})
        point = self.mount(stick)
        self.assertIsNotNone(self.status_file())
        self.set_mounts("/dev/sdb2 %s exfat rw 0 0\n" % point)
        usb.unmount("/dev/sdc1", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
        self.assertIsNotNone(self.status_file(), "another drive's unmount leaves the status alone")
        usb.unmount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
        self.assertIsNone(self.status_file())


class ClaimTest(MountBase):
    def claim(self, stick, force=False):
        return usb.claim("/dev/sdb2", force=force, env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append,
                         system_disks=set(), node_name="nxlx-node-01")

    def test_claim_writes_the_marker_and_the_record_and_then_mounts_it_writable(self):
        stick = Stick(EXFAT_N01)
        point = self.claim(stick)
        marker = json.loads(stick.files.get(clipstick.MARKER) or open(os.path.join(point, clipstick.MARKER), "rb").read())
        self.assertTrue(clipstick.marker_matches(marker, NODE, "N01-CLIPS"))
        self.assertEqual(marker["node_name"], "nxlx-node-01")
        self.assertEqual(clipstick.read_claim(self.state)["label"], "N01-CLIPS")
        self.assertLess(stick.kinds().index("fsck.exfat"), [i for i, c in enumerate(stick.calls) if c[0] == "mount"][0])
        self.assertTrue(self.final_mount(stick)[1][4].startswith("rw,"))
        self.assertTrue(self.status_file()["writable"])

    def test_claim_never_leaves_a_recovery_file_or_a_temporary_file(self):
        stick = Stick(EXFAT_N01)
        point = self.claim(stick)
        names = set(os.listdir(point)) | set(stick.files)
        self.assertFalse([n for n in names if "recover" in n.lower()])
        self.assertFalse([n for n in names if n.startswith(".")])
        self.assertLessEqual(names, {clipstick.MARKER, clipstick.CLIP_FOLDER})

    def test_claim_refuses_what_is_not_a_clip_stick(self):
        for blkid in ({"/dev/sdb2": "TYPE=vfat\nLABEL=N01-CLIPS\n"}, {"/dev/sdb2": "TYPE=exfat\nLABEL=N01-KIT\n"}):
            stick = Stick(blkid)
            with self.assertRaises(usb.UsbError):
                self.claim(stick)
            self.assertEqual(stick.kinds(), ["blkid"])
        self.assertIsNone(clipstick.read_claim(self.state))

    def test_claim_refuses_a_stick_that_fails_its_check(self):
        stick = Stick(EXFAT_N01, fsck_rc=4)
        with self.assertRaises(usb.UsbError):
            self.claim(stick)
        self.assertNotIn("mount", stick.kinds())
        self.assertIsNone(clipstick.read_claim(self.state))

    def test_claim_refuses_another_nodes_stick_unless_forced(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes(node_id=OTHER)})
        with self.assertRaises(usb.UsbError) as e:
            self.claim(stick)
        self.assertIn("another node", str(e.exception))
        self.assertEqual(json.loads(stick.files[clipstick.MARKER])["node_id"], OTHER)    # left as it was
        self.assertIsNone(clipstick.read_claim(self.state))
        self.claim(stick, force=True)
        self.assertEqual(clipstick.read_claim(self.state)["label"], "N01-CLIPS")

    def test_claim_needs_the_node_id(self):
        os.unlink(os.path.join(self.state, "node-id"))
        with self.assertRaises(usb.UsbError):
            self.claim(Stick(EXFAT_N01))


class ChooserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sd = os.path.join(self.tmp, "video")
        self.root = os.path.join(self.tmp, "nxlx")
        self.point = os.path.join(self.root, "N01-CLIPS")
        self.state = os.path.join(self.tmp, "state")
        for d in (self.sd, os.path.join(self.point, "clips"), self.state):
            os.makedirs(d)
        self.status = os.path.join(self.tmp, "clip-stick.json")
        self.mounted = {self.point}

    def chooser(self):
        return clipstick.Chooser(self.sd, self.root, self.state, self.status, ttl=0, ismount=lambda p: p in self.mounted)

    def put_status(self, **kw):
        st = {"device": "/dev/sdb2", "point": self.point, "label": "N01-CLIPS", "writable": True, "why": ""}
        st.update(kw)
        clipstick.write_status(self.status, st)

    def test_no_stick_and_no_claim_is_the_sd_folder_and_nothing_to_say(self):
        c = self.chooser()
        self.assertEqual(c.media_dir(), self.sd)
        self.assertEqual(c.state(), {"state": "none", "label": None, "text": ""})

    def test_a_claimed_stick_that_is_missing_says_so_and_falls_back_to_the_sd(self):
        clipstick.write_claim(self.state, "N01-CLIPS")
        c = self.chooser()
        self.assertEqual(c.media_dir(), self.sd)
        st = c.state()
        self.assertEqual(st["state"], "missing")
        self.assertIn("not plugged in", st["text"])
        self.assertIn("test clips", st["text"])

    def test_the_mounted_stick_is_the_media_folder(self):
        clipstick.write_claim(self.state, "N01-CLIPS")
        self.put_status()
        c = self.chooser()
        self.assertEqual(c.media_dir(), os.path.join(self.point, "clips"))
        self.assertEqual(c.state()["state"], "in")
        self.mounted.clear()                                  # pulled: the status may be stale, the mount check is not
        self.assertEqual(c.media_dir(), self.sd)
        self.assertEqual(c.state()["state"], "missing")

    def test_a_read_only_stick_says_why(self):
        self.put_status(writable=False, why="fsck.exfat exit 4: bad cluster")
        st = self.chooser().state()
        self.assertEqual(st["state"], "read-only")
        self.assertIn("exit 4", st["text"])

    def test_a_status_pointing_anywhere_else_is_ignored(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(os.path.join(outside, "clips"))
        self.mounted.add(outside)
        for point in (outside, self.root, os.path.join(self.root, "..", "elsewhere")):
            self.put_status(point=point)
            self.assertEqual(self.chooser().media_dir(), self.sd, point)
        os.symlink(self.point, os.path.join(self.root, "LINK"))
        self.mounted.add(os.path.join(self.root, "LINK"))
        self.put_status(point=os.path.join(self.root, "LINK"))
        self.assertEqual(self.chooser().media_dir(), self.sd)
        shutil.rmtree(os.path.join(self.point, "clips"))
        os.symlink(self.sd, os.path.join(self.point, "clips"))   # a clips link on the stick is not followed
        self.put_status()
        self.assertEqual(self.chooser().media_dir(), self.sd)


from tests.test_server import ServerBase   # noqa: E402


class ApiClipStickTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.token = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def test_the_library_says_when_the_stick_is_missing_and_lists_the_sd_clips(self):
        with open(os.path.join(self.media, "test-grid.mp4"), "wb") as f:
            f.write(b"x")
        state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state, True)
        clipstick.write_claim(state, "N01-CLIPS")
        self.api.clip_chooser = clipstick.Chooser(self.media, os.path.join(state, "usb"), state,
                                                  os.path.join(state, "none.json"), ttl=0)
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual(st, 200)
        self.assertIn("test-grid.mp4", body["files"])           # the SD folder's clips
        self.assertEqual(body["clip_stick"]["state"], "missing")
        self.assertIn("not plugged in", body["clip_stick"]["text"])

    def test_without_a_chooser_nothing_changes(self):
        body = self.call("GET", "/api/media", token=self.token)[1]
        self.assertEqual(body["clip_stick"], {"state": "none", "label": None, "text": ""})
        self.assertEqual(self.api.media_dir, self.media)

    def test_writes_on_a_filesystem_without_hard_links_rename_and_never_overwrite(self):
        # os is one module for the whole process: only the media folder behaves like exFAT, the settings file does not.
        real_link, real_chmod = os.link, os.chmod
        media = os.path.realpath(self.media)

        def no_links(src, dst, *a, **kw):
            if os.path.realpath(os.path.dirname(dst)) == media:
                raise OSError(errno.EPERM, "Operation not permitted")
            return real_link(src, dst, *a, **kw)

        def no_modes(path, mode, *a, **kw):
            if isinstance(path, str) and os.path.realpath(os.path.dirname(path)) == media:
                raise PermissionError(errno.EPERM, "Operation not permitted")
            return real_chmod(path, mode, *a, **kw)
        with mock.patch("mctl.api.os.link", no_links), mock.patch("mctl.api.os.chmod", no_modes):
            self.assertEqual(self.api.upload("up1.mp4", 5, io.BytesIO(b"hello").read), {"name": "up1.mp4", "size": 5})
            with open(os.path.join(self.media, "up1.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"hello")
            from mctl.api import ApiError
            with self.assertRaises(ApiError) as e:
                self.api.upload("up1.mp4", 3, io.BytesIO(b"new").read)
            self.assertEqual(e.exception.status, 409)
            with open(os.path.join(self.media, "up1.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"hello")                     # not overwritten
            self.assertFalse([n for n in os.listdir(self.media) if n.startswith(".upload-")])     # no temporary file left
            st, body, _ = self.call("POST", "/api/media/rename", {"name": "up1.mp4", "new": "up2.mp4"}, token=self.token)
            self.assertEqual(st, 200, body)
            self.assertEqual(sorted(n for n in os.listdir(self.media) if n.startswith("up")), ["up2.mp4"])
            with open(os.path.join(self.media, "up3.mp4"), "wb") as f:
                f.write(b"c")
            st, body, _ = self.call("POST", "/api/media/rename", {"name": "up2.mp4", "new": "up3.mp4"}, token=self.token)
            self.assertEqual(st, 409, body)
            with open(os.path.join(self.media, "up3.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"c")


if __name__ == "__main__":
    unittest.main()
