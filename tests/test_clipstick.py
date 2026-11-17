# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's own clip stick (D143): the claim's secret on the stick and in the root-only record, fsck before the
writable mount, only the clips folder writable, every other stick read-only, the media folder falling back to the SD
card when the stick is missing, uploads refused then, and writes on a filesystem without hard links (exFAT)."""
import errno
import fcntl
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from mctl import clipstick, usb
import tests        # the run's own temp folder and the locks' checker (tests/__init__.py)

NODE = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
SECRET = "00112233445566778899aabbccddeeff"
WRONG = "ffeeddccbbaa99887766554433221100"
UUID = "1234-ABCD"


def marker(node_id=NODE, label="N01-CLIPS", token=SECRET, fmt=1):
    m = {"nxlx_clip_stick": fmt, "node_id": node_id, "node_name": "nxlx-node-01", "label": label, "claimed": "2026-11-17T12:00:00Z"}
    if token is not None:
        m["token"] = token
    return m


def marker_bytes(**kw):
    return json.dumps(marker(**kw)).encode()


RECORD = {"label": "N01-CLIPS", "uuid": UUID, "token": SECRET}


class Stick:
    """A stand-in for blkid, mount, umount and fsck.exfat. What is "on the stick" (top-level files and folders) is put
    into a folder when it is mounted there and read back and cleared when it is unmounted, so the code under test reads
    and writes real files. Bind mounts and remounts are only recorded."""

    def __init__(self, blkid, files=None, dirs=(), fsck_rc=0, fsck_missing=False, fsck_timeout=False, refuse_errors_opt=False,
                 peek_umount_fails=False, peek_mount_times_out=False, on_fsck=None, swap_on_mount=None):
        self.blkid, self.files, self.dirs = blkid, dict(files or {}), set(dirs)
        self.fsck_rc, self.fsck_missing, self.fsck_timeout = fsck_rc, fsck_missing, fsck_timeout
        self.refuse_errors_opt, self.peek_umount_fails, self.peek_mount_times_out = refuse_errors_opt, peek_umount_fails, peek_mount_times_out
        self.on_fsck, self.swap_on_mount = on_fsck, swap_on_mount
        self.on_systemctl = None
        self.calls, self.mounted, self.binds, self.mounts_done = [], {}, [], 0

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        name = cmd[0]
        if name == "blkid":
            out = self.blkid.get(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0 if out else 2, out or "", "")
        if name == "systemctl":
            if self.on_systemctl:
                self.on_systemctl()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if name == "fsck.exfat":
            if self.on_fsck:
                self.on_fsck()
            if self.fsck_missing:
                raise FileNotFoundError("fsck.exfat")
            if self.fsck_timeout:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, self.fsck_rc, "", "exfatprogs: ERROR: bad cluster" if self.fsck_rc > 1 else "")
        if name == "mount" and cmd[1] == "--bind":
            self.binds.append((cmd[2], cmd[3]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if name == "mount" and cmd[1] == "-o":
            self.binds.append((cmd[2], cmd[3]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if name == "mount":
            opts, target = cmd[4], cmd[-1]
            if self.peek_mount_times_out and "/peek/" in target:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            if self.refuse_errors_opt and "errors=" in opts:
                return subprocess.CompletedProcess(cmd, 32, "", "mount: bad option")
            self.mounts_done += 1
            if self.swap_on_mount and self.mounts_done == self.swap_on_mount[0]:
                self.files = dict(self.swap_on_mount[1])
            for n, data in self.files.items():
                with open(os.path.join(target, n), "wb") as f:
                    f.write(data)
            for d in self.dirs:
                os.makedirs(os.path.join(target, d), exist_ok=True)
            self.mounted[target] = opts
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if name == "umount":
            target, lazy = cmd[-1], "-l" in cmd
            if self.peek_umount_fails and "/peek/" in target and not lazy:
                return subprocess.CompletedProcess(cmd, 32, "", "umount: target is busy")
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
        return [c[0] if c[0] != "mount" or c[1] not in ("--bind", "-o") else "bind" for c in self.calls]


EXFAT_N01 = {"/dev/sdb2": "TYPE=exfat\nLABEL=N01-CLIPS\nUUID=%s\n" % UUID}


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

    def test_the_marker_must_carry_the_claims_secret(self):
        ok = lambda m, record=RECORD, uuid=UUID, node=NODE, label="N01-CLIPS": clipstick.marker_matches(m, node, label, record, uuid)
        self.assertTrue(ok(marker()))
        self.assertFalse(ok(marker(token=None)), "the public node id alone is a forgery")
        self.assertFalse(ok(marker(token=WRONG)))
        self.assertFalse(ok(marker(token=SECRET.upper())))
        self.assertFalse(ok(marker(token=5)))
        self.assertFalse(ok(marker(), record=None), "no record, no clip stick")
        self.assertFalse(ok(marker(), uuid="9999-0000"), "another volume with the same label and a copied marker")
        self.assertFalse(ok(marker(), uuid=""), "the claim saw a volume serial; one must be there")
        self.assertTrue(ok(marker(), record=dict(RECORD, uuid=""), uuid="9999-0000"), "a claim without a serial compares none")
        self.assertFalse(ok(marker(node_id=OTHER)))
        self.assertFalse(ok(marker(), label="N03-CLIPS"))
        self.assertFalse(ok(marker(), record=dict(RECORD, label="N03-CLIPS")))
        self.assertFalse(ok(dict(marker(), nxlx_clip_stick=True)))
        self.assertFalse(ok(marker(fmt=2)))
        self.assertFalse(ok(marker(), node=None))
        self.assertFalse(ok(None))
        self.assertFalse(ok(["x"]))

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
        self.assertIsNone(clipstick.read_marker(d))
        with open(path, "wb") as f:
            f.write(b"not json")
        self.assertIsNone(clipstick.read_marker(d))
        os.unlink(path)
        real = os.path.join(d, "real.json")
        with open(real, "wb") as f:
            f.write(marker_bytes())
        os.symlink(real, path)
        self.assertIsNone(clipstick.read_marker(d))
        os.unlink(path)
        os.mkdir(path)
        self.assertIsNone(clipstick.read_marker(d))

    def test_the_record_is_private_and_the_label_file_is_not(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(clipstick.read_record(d))
        self.assertIsNone(clipstick.claimed_label(d))
        clipstick.write_claim(d, "N01-CLIPS", UUID, SECRET)
        rec = os.path.join(d, clipstick.CLAIM_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(rec).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, clipstick.LABEL_FILE)).st_mode), 0o644)
        self.assertEqual(clipstick.read_record(d)["token"], SECRET)
        with open(os.path.join(d, clipstick.LABEL_FILE)) as f:
            self.assertNotIn(SECRET, f.read())
        self.assertEqual(clipstick.claimed_label(d), "N01-CLIPS")
        os.chmod(rec, 0o640)
        self.assertIsNone(clipstick.read_record(d), "a record others can read is not trusted")
        os.chmod(rec, 0o600)
        os.rename(rec, rec + ".real")
        os.symlink(rec + ".real", rec)
        self.assertIsNone(clipstick.read_record(d))
        self.assertEqual(sorted(n for n in os.listdir(d) if n.startswith(".")), [])
        self.assertTrue(clipstick.forget_claim(d))
        os.unlink(rec + ".real")
        self.assertIsNone(clipstick.claimed_label(d))

    def test_the_status_never_holds_the_secret(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "s.json")
        clipstick.write_status(path, {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "in", "why": "", "token": SECRET})
        with open(path) as f:
            self.assertNotIn(SECRET, f.read())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)

    def test_writing_the_marker_leaves_no_temporary_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        clipstick.write_marker(d, clipstick.make_marker(NODE, "nxlx-node-01", "N01-CLIPS", SECRET, now=0))
        self.assertEqual(os.listdir(d), [clipstick.MARKER])
        self.assertTrue(clipstick.marker_matches(clipstick.read_marker(d), NODE, "N01-CLIPS", RECORD, UUID))
        for args in ((None, "x", "N01-CLIPS", SECRET), (NODE, "x", "SHOW", SECRET), (NODE, "x", "N01-CLIPS", "short")):
            with self.assertRaises(clipstick.ClipStickError):
                clipstick.make_marker(*args)


class MountBase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.join(self.tmp, "nxlx")
        self.state = os.path.join(self.tmp, "state")
        self.run = os.path.join(self.tmp, "run")
        self.clips = os.path.join(self.tmp, "nxlx-clips")
        os.makedirs(self.state)
        with open(os.path.join(self.state, "node-id"), "w") as f:
            f.write(NODE + "\n")
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        self.status = os.path.join(self.run, "clip-stick.json")
        self.env = {"NXLX_USB_BASE": self.base, "NXLX_USB_LINK": os.path.join(self.tmp, "usb"), "NXLX_STATE_DIR": self.state,
                    "NXLX_USB_RUN": self.run, "NXLX_CLIP_DIR": self.clips}
        self.mounts = os.path.join(self.tmp, "mounts")
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n")
        self.logs = []

    def set_mounts(self, text):
        with open(self.mounts, "w") as f:
            f.write(text)

    def mount(self, stick, env=None):
        return usb.mount("/dev/sdb2", env=env or self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append,
                         system_disks=set())

    def mounts_of(self, stick):
        return [c for c in stick.calls if c[0] == "mount" and c[1] == "-t"]

    def status_file(self):
        return clipstick.read_status(self.status)

    def own(self, **kw):
        return Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()}, dirs={"clips"}, **kw)

    def refused(self, stick, env=None):
        """The stick has the label and volume of the claim but did not pass: it is not mounted anywhere, so the secret
        in its marker is never readable outside root's own folders."""
        with self.assertRaises(usb.UsbError):
            self.mount(stick, env)
        self.assertEqual(stick.mounted, {}, "nothing of it is left mounted")
        self.assertFalse(os.path.exists(self.base) and os.listdir(self.base), "never under the drive folder")
        self.assertIsNone(self.status_file())

    def not_accepted(self, stick):
        """The claim's label and volume, but the marker is not the claim's: not mounted anywhere, and the panel says
        it was not accepted (not "not plugged in")."""
        self.assertIsNone(self.mount(stick))
        self.assertEqual(stick.mounted, {}, "nothing of it is left mounted")
        self.assertFalse(os.path.exists(self.base) and os.listdir(self.base), "never under the drive folder")
        st = self.status_file()
        self.assertEqual(st["state"], "not-accepted")
        c = clipstick.Chooser(os.path.join(self.tmp, "sd"), self.clips, self.state, self.status, ttl=0, exists=lambda p: True)
        self.assertEqual(c.state()["state"], "not-accepted")
        self.assertIn("was not accepted", c.state()["text"])
        self.assertIn("sudo nxlx-usb claim /dev/sdb2", c.state()["text"])
        self.assertNotIn(SECRET, json.dumps(st))
        clipstick.clear_status(self.status)

    def assert_plain_read_only(self, stick):
        last = self.mounts_of(stick)[-1]
        self.assertTrue(last[-1].startswith(self.base + os.sep), last)
        self.assertTrue(last[4].startswith("ro,"), last)
        self.assertNotIn("fsck.exfat", stick.kinds())
        self.assertEqual(stick.binds, [])
        self.assertIsNone(self.status_file())


class MountTest(MountBase):
    def test_own_stick_is_checked_then_only_its_clips_folder_is_bound_writable(self):
        seen = []
        stick = self.own(on_fsck=lambda: seen.append(self.status_file()))
        point = self.mount(stick)
        self.assertEqual(point, self.clips)
        self.assertEqual(stick.kinds(), ["blkid", "mount", "umount", "fsck.exfat", "mount", "bind", "bind"])
        peek, final = self.mounts_of(stick)
        self.assertTrue(peek[4].startswith("ro,"), "the look for the marker is read-only")
        self.assertEqual(stick.calls[3], ["fsck.exfat", "-p", "/dev/sdb2"])
        self.assertEqual(seen[0]["state"], "checking", "the panel says checking while fsck runs")
        opts = final[4].split(",")
        self.assertEqual(opts[0], "rw")
        for o in ("nosuid", "nodev", "noexec", "noatime", "errors=remount-ro"):
            self.assertIn(o, opts)
        priv = final[-1]
        self.assertEqual(os.path.dirname(priv), os.path.join(self.run, "mnt"))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(priv)).st_mode), 0o700, "only root reaches the whole stick")
        self.assertEqual(stick.binds[0], (os.path.join(priv, "clips"), self.clips))
        self.assertEqual(stick.binds[1], ("remount,bind,rw,nosuid,nodev,noexec", self.clips))
        self.assertFalse(os.path.exists(self.base), "the top of the stick is under no drive folder (the recovery stick scan never sees it)")
        st = self.status_file()
        self.assertIsInstance(st.pop("since"), int)
        self.assertEqual(st, {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "in", "why": ""})
        self.assertFalse(any(SECRET in m for m in self.logs))
        self.assertEqual(os.listdir(os.path.join(self.run, "peek")), [], "the look's own folder is gone")

    def test_fsck_that_corrected_errors_still_mounts_writable(self):
        stick = self.own(fsck_rc=1)
        self.mount(stick)
        self.assertTrue(self.mounts_of(stick)[-1][4].startswith("rw,"))

    def test_a_failed_missing_or_endless_fsck_binds_the_clips_read_only(self):
        for kw, words in (({"fsck_rc": 4}, "exit 4"), ({"fsck_missing": True}, "not installed"), ({"fsck_timeout": True}, "did not finish")):
            with self.subTest(**kw):
                self.logs.clear()
                stick = self.own(**kw)
                self.mount(stick, dict(self.env, NXLX_USB_RW="1"))        # not even with the old switch
                self.assertTrue(self.mounts_of(stick)[-1][4].startswith("ro,"))
                self.assertEqual(stick.binds[1][0], "remount,bind,ro,nosuid,nodev,noexec")
                st = self.status_file()
                self.assertEqual(st["state"], "read-only")
                self.assertIn(words, st["why"])
                if kw.get("fsck_timeout"):
                    self.assertTrue(any("did not finish" in m for m in self.logs))
                clipstick.clear_status(self.status)
                shutil.rmtree(os.path.join(self.run, "mnt"))

    def test_a_forged_marker_with_the_public_node_id_is_not_mounted_at_all(self):
        for files in ({clipstick.MARKER: marker_bytes(token=None)}, {clipstick.MARKER: marker_bytes(token=WRONG)},
                      {clipstick.MARKER: marker_bytes(node_id=OTHER)}, {}):
            with self.subTest(files=sorted(files)):
                stick = Stick(EXFAT_N01, files, dirs={"clips"})
                self.not_accepted(stick)
                self.assertNotIn("fsck.exfat", stick.kinds())

    def test_a_forged_marker_on_another_volume_is_any_other_stick(self):
        stick = Stick({"/dev/sdb2": "TYPE=exfat\nLABEL=N01-CLIPS\nUUID=9999-0000\n"}, {clipstick.MARKER: marker_bytes(token=WRONG)})
        self.mount(stick)
        self.assert_plain_read_only(stick)

    def test_no_record_or_a_record_for_another_volume_means_no_clip_stick(self):
        cases = (("no record", lambda: clipstick.forget_claim(self.state)),
                 ("readable record", lambda: os.chmod(os.path.join(self.state, clipstick.CLAIM_FILE), 0o644)),
                 ("other volume", lambda: clipstick.write_claim(self.state, "N01-CLIPS", "9999-0000", SECRET)))
        for why, change in cases:
            with self.subTest(why):
                clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
                change()
                stick = self.own()
                self.mount(stick)
                self.assert_plain_read_only(stick)
                self.assertEqual(stick.kinds()[:2], ["blkid", "mount"], "the stick is not even looked at")
                shutil.rmtree(self.base)

    def test_the_marker_alone_is_not_enough(self):
        for blkid, files in (({"/dev/sdb2": "TYPE=exfat\nLABEL=SHOW\nUUID=%s\n" % UUID}, {clipstick.MARKER: marker_bytes()}),
                             ({"/dev/sdb2": "TYPE=vfat\nLABEL=N01-CLIPS\nUUID=%s\n" % UUID}, {clipstick.MARKER: marker_bytes()})):
            with self.subTest(blkid=blkid):
                stick = Stick(blkid, files)
                self.mount(stick)
                self.assert_plain_read_only(stick)
                shutil.rmtree(self.base)

    def test_a_node_without_an_id_mounts_no_clip_stick(self):
        os.unlink(os.path.join(self.state, "node-id"))
        stick = self.own()
        self.refused(stick)
        self.assertEqual(stick.kinds(), ["blkid"])
        other = Stick({"/dev/sdb2": "TYPE=vfat\nLABEL=SHOW\n"})
        self.mount(other)
        self.assert_plain_read_only(other)

    def test_a_look_that_cannot_be_unmounted_leaves_the_stick_unmounted(self):
        stick = self.own(peek_umount_fails=True)
        self.refused(stick)
        self.assertIn(["umount", "-l", self.mounts_of(stick)[0][-1]], stick.calls, "the look is detached")
        self.assertEqual(len(self.mounts_of(stick)), 1, "no other mount follows")
        self.assertEqual(os.listdir(os.path.join(self.run, "peek")), [])

    def test_a_look_that_times_out_leaves_the_stick_unmounted(self):
        stick = self.own(peek_mount_times_out=True)
        self.refused(stick)
        self.assertEqual(os.listdir(os.path.join(self.run, "peek")), [])

    def test_a_blkid_that_hangs_is_an_error_not_a_crash(self):
        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        with self.assertRaises(usb.UsbError):
            self.mount(hang)

    def test_a_stick_swapped_between_the_look_and_the_mount_is_taken_down(self):
        stick = self.own(swap_on_mount=(2, {clipstick.MARKER: marker_bytes(token=WRONG)}))
        self.not_accepted(stick)
        priv = self.mounts_of(stick)[1][-1]
        self.assertIn(["umount", priv], stick.calls)
        self.assertEqual(stick.binds, [])

    def test_a_failure_after_the_mount_takes_the_mount_and_the_binding_down(self):
        real = clipstick.write_status

        def failing(path, status):
            if status.get("state") == "in":
                raise OSError(28, "No space left on device")
            return real(path, status)
        stick = self.own()
        with mock.patch.object(clipstick, "write_status", failing):
            with self.assertRaises(OSError):
                self.mount(stick)
        self.assertEqual(stick.mounted, {})
        self.assertIn(["umount", "-l", self.clips], stick.calls)
        self.assertIsNone(self.status_file())
        self.assertEqual(os.listdir(os.path.join(self.run, "mnt")), [])

    def test_a_second_stick_with_the_same_claim_is_not_stacked(self):
        stick = self.own()
        with mock.patch.object(usb, "_ismount", lambda p: p == self.clips):
            self.refused(stick)
        self.assertEqual(stick.kinds(), ["blkid"])
        self.assertTrue(any("already" in m for m in self.logs))

    def test_the_wait_for_the_lock_and_the_check_fit_in_the_units_time_limit(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install", "nxlx-usb@.service")) as f:
            unit = f.read()
        limit = [l for l in unit.splitlines() if l.startswith("TimeoutStartSec=")]
        self.assertEqual(limit, ["TimeoutStartSec=15min"])
        # the wait, the longest check and three minutes for the look and the mounts, a minute inside the limit
        self.assertLessEqual(usb.MOUNT_LOCK_SECONDS + clipstick.FSCK_SECONDS + 3 * 60, 15 * 60 - 60)
        self.assertIn("ExecStopPost=-@NXLX_DIR@/bin/nxlx-usb unmount /dev/%I", unit)

    def test_a_binding_left_by_a_pulled_stick_is_taken_down_before_the_next_one(self):
        gone_priv = os.path.join(self.run, "mnt", "sdz9")
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/sdz9 %s exfat rw 0 0\n/dev/sdz9 %s exfat rw 0 0\n" % (gone_priv, self.clips))
        stick = self.own()
        bound = lambda p: p == self.clips and ["umount", "-l", self.clips] not in stick.calls
        with mock.patch.object(usb, "_ismount", bound), mock.patch.object(usb, "_exists", lambda p: p != "/dev/sdz9"):
            self.assertEqual(self.mount(stick), self.clips)
        first_mount = stick.kinds().index("mount")
        self.assertLess(stick.calls.index(["umount", "-l", self.clips]), first_mount)
        self.assertLess(stick.calls.index(["umount", "-l", gone_priv]), first_mount)
        self.assertEqual(self.status_file()["state"], "in")

    def test_a_binding_whose_stick_is_still_there_is_not_touched(self):
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/sdz9 %s exfat rw 0 0\n" % self.clips)
        stick = self.own()
        with mock.patch.object(usb, "_ismount", lambda p: p == self.clips), mock.patch.object(usb, "_exists", lambda p: True):
            with self.assertRaises(usb.UsbError):
                self.mount(stick)
        self.assertNotIn(["umount", "-l", self.clips], stick.calls)

    def test_unmount_also_takes_down_a_look_left_over(self):
        peek = os.path.join(self.run, "peek", "sdb2.left")
        os.makedirs(peek)
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/sdb2 %s exfat ro 0 0\n" % peek)
        stick = Stick({})
        usb.unmount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
        self.assertEqual(stick.calls, [["umount", "-l", peek]])
        self.assertFalse(os.path.exists(peek))

    def test_unmount_after_a_killed_check_clears_the_status_and_twice_is_harmless(self):
        clipstick.write_status(self.status, {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "checking", "why": ""})
        stick = Stick({})
        for _ in range(2):
            usb.unmount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
            self.assertIsNone(self.status_file())
        self.assertEqual(stick.calls, [])

    def test_forgetting_the_claim_is_refused_while_the_stick_is_in(self):
        stick = self.own()
        self.mount(stick)
        with self.assertRaises(usb.UsbError):
            usb.forget_clip_stick(self.env, proc_mounts=self.mounts)
        self.assertEqual(clipstick.claimed_label(self.state), "N01-CLIPS")
        clipstick.clear_status(self.status)
        priv = self.mounts_of(stick)[-1][-1]
        self.set_mounts("/dev/sdb2 %s exfat rw 0 0\n" % priv)
        with self.assertRaises(usb.UsbError):
            usb.forget_clip_stick(self.env, proc_mounts=self.mounts)
        self.set_mounts("/dev/nvme0n1p2 / ext4 rw 0 0\n")
        self.assertTrue(usb.forget_clip_stick(self.env, proc_mounts=self.mounts))
        self.assertIsNone(clipstick.claimed_label(self.state))

    def test_a_read_only_own_stick_without_a_clips_folder_says_so(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes()}, fsck_rc=4)
        self.mount(stick)
        self.assertEqual(stick.binds, [])
        self.assertEqual(self.status_file()["state"], "no-clips")
        c = clipstick.Chooser(os.path.join(self.tmp, "sd"), self.clips, self.state, self.status, ttl=0, exists=lambda p: True)
        self.assertEqual(c.state()["state"], "no-clips")
        self.assertIn("no clips folder", c.state()["text"])

    def test_a_driver_without_errors_remount_ro_is_mounted_without_it_and_says_so(self):
        stick = self.own(refuse_errors_opt=True)
        self.mount(stick)
        opts = self.mounts_of(stick)[-1][4]
        self.assertTrue(opts.startswith("rw,"))
        self.assertNotIn("errors=", opts)
        self.assertTrue(any("errors=remount-ro" in m for m in self.logs))

    def test_other_sticks_follow_the_old_switch_as_before(self):
        stick = Stick({"/dev/sdb2": "TYPE=exfat\nLABEL=SHOW\n"})
        self.mount(stick, dict(self.env, NXLX_USB_RW="1"))
        self.assertTrue(stick.calls[-1][4].startswith("rw,"))
        self.assertNotIn("errors=", stick.calls[-1][4])

    def test_each_look_has_its_own_folder(self):
        a, b = self.own(), self.own()
        self.mount(a)
        clipstick.clear_status(self.status)
        shutil.rmtree(os.path.join(self.run, "mnt"))
        self.mount(b)
        self.assertNotEqual(self.mounts_of(a)[0][-1], self.mounts_of(b)[0][-1])

    def test_a_held_lock_stops_a_clip_stick_mount_but_not_other_sticks(self):
        os.makedirs(self.run, exist_ok=True)
        fd = os.open(os.path.join(self.run, "clip.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        env = dict(self.env, NXLX_CLIP_LOCK_SECONDS="0.3")
        stick = self.own()
        with self.assertRaises(usb.UsbError):
            self.mount(stick, env)
        self.assertEqual(stick.kinds(), ["blkid"])
        other = Stick({"/dev/sdb2": "TYPE=vfat\nLABEL=SHOW\n"})
        self.assertIsNotNone(self.mount(other, env))
        with self.assertRaises(usb.UsbError):
            usb.claim("/dev/sdb2", env=env, runner=self.own(), proc_mounts=self.mounts, log=self.logs.append, system_disks=set())

    def test_unmount_takes_the_binding_off_first_and_the_library_back_to_the_sd(self):
        stick = self.own()
        self.mount(stick)
        priv = self.mounts_of(stick)[-1][-1]
        self.set_mounts("/dev/sdb2 %s exfat rw 0 0\n/dev/sdb2 %s exfat rw 0 0\n" % (priv, self.clips))
        usb.unmount("/dev/sdc1", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
        self.assertIsNotNone(self.status_file(), "another drive's unmount leaves the status alone")
        n = len(stick.calls)
        usb.unmount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append)
        self.assertEqual(stick.calls[n:], [["umount", "-l", self.clips], ["umount", "-l", priv]])
        self.assertIsNone(self.status_file())


class ClaimTest(MountBase):
    def setUp(self):
        super().setUp()
        clipstick.forget_claim(self.state)

    def claim(self, stick, force=False):
        stick.on_systemctl = lambda: usb.mount("/dev/sdb2", env=self.env, runner=stick, proc_mounts=self.mounts,
                                               log=self.logs.append, system_disks=set())
        return usb.claim("/dev/sdb2", force=force, env=self.env, runner=stick, proc_mounts=self.mounts, log=self.logs.append,
                         system_disks=set(), node_name="nxlx-node-01")

    def test_claim_puts_one_new_secret_on_the_stick_and_in_the_record(self):
        stick = Stick(EXFAT_N01)
        self.assertEqual(self.claim(stick), "in")
        restart = stick.calls.index(["systemctl", "restart", "nxlx-usb@sdb2.service"])
        before = [c for c in stick.calls[:restart] if c[0] == "mount"]
        self.assertTrue(all("/peek/" in c[-1] for c in before), "claim mounts nothing of its own but its work folder")
        self.assertEqual([c for c in stick.calls[:restart] if c[0] == "mount" and c[1] == "--bind"], [])
        rec = clipstick.read_record(self.state)
        self.assertEqual((rec["label"], rec["uuid"]), ("N01-CLIPS", UUID))
        on_stick = json.loads(stick.files.get(clipstick.MARKER) or open(os.path.join(self.mounts_of(stick)[-1][-1], clipstick.MARKER), "rb").read())
        self.assertEqual(on_stick["token"], rec["token"])
        self.assertTrue(clipstick.TOKEN.fullmatch(rec["token"]))
        self.assertTrue(clipstick.marker_matches(on_stick, NODE, "N01-CLIPS", rec, UUID))
        self.assertLess(stick.kinds().index("fsck.exfat"), stick.kinds().index("mount"))
        self.assertEqual(self.status_file()["state"], "in")
        self.assertFalse(any(rec["token"] in m for m in self.logs), "the secret is never logged")
        stick2 = Stick(EXFAT_N01)
        self.claim(stick2)
        self.assertNotEqual(clipstick.read_record(self.state)["token"], rec["token"], "every claim makes a new secret")

    def test_claim_never_leaves_a_recovery_file_or_a_temporary_file(self):
        stick = Stick(EXFAT_N01)
        self.claim(stick)
        priv = self.mounts_of(stick)[-1][-1]
        names = set(os.listdir(priv)) | set(stick.files) | stick.dirs
        self.assertFalse([n for n in names if "recover" in n.lower()])
        self.assertFalse([n for n in names if n.startswith(".")])
        self.assertLessEqual(names, {clipstick.MARKER, clipstick.CLIP_FOLDER})

    def test_claim_refuses_what_is_not_a_clip_stick(self):
        for blkid in ({"/dev/sdb2": "TYPE=vfat\nLABEL=N01-CLIPS\n"}, {"/dev/sdb2": "TYPE=exfat\nLABEL=N01-KIT\n"}):
            stick = Stick(blkid)
            with self.assertRaises(usb.UsbError):
                self.claim(stick)
            self.assertEqual(stick.kinds(), ["blkid"])
        self.assertIsNone(clipstick.claimed_label(self.state))

    def test_claim_refuses_a_stick_that_fails_its_check(self):
        stick = Stick(EXFAT_N01, fsck_rc=4)
        with self.assertRaises(usb.UsbError):
            self.claim(stick)
        self.assertNotIn("mount", stick.kinds())
        self.assertIsNone(clipstick.claimed_label(self.state))

    def test_claim_refuses_another_nodes_stick_unless_forced(self):
        stick = Stick(EXFAT_N01, {clipstick.MARKER: marker_bytes(node_id=OTHER)})
        with self.assertRaises(usb.UsbError) as e:
            self.claim(stick)
        self.assertIn("another node", str(e.exception))
        self.assertEqual(json.loads(stick.files[clipstick.MARKER])["node_id"], OTHER)
        self.assertIsNone(clipstick.claimed_label(self.state))
        self.claim(stick, force=True)
        self.assertEqual(clipstick.claimed_label(self.state), "N01-CLIPS")

    def test_claim_that_cannot_unmount_its_work_keeps_no_record(self):
        stick = Stick(EXFAT_N01, peek_umount_fails=True)
        with self.assertRaises(usb.UsbError):
            self.claim(stick)
        self.assertIsNone(clipstick.claimed_label(self.state))

    def test_claim_needs_the_node_id(self):
        os.unlink(os.path.join(self.state, "node-id"))
        with self.assertRaises(usb.UsbError):
            self.claim(Stick(EXFAT_N01))


class ChooserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sd = os.path.join(self.tmp, "video")
        self.clips = os.path.join(self.tmp, "nxlx-clips")
        self.state = os.path.join(self.tmp, "state")
        for d in (self.sd, self.clips, self.state):
            os.makedirs(d)
        self.status = os.path.join(self.tmp, "clip-stick.json")
        self.mounted = {self.clips}

    def chooser(self, clips=None, now=None, exists=lambda p: True):
        import time as _time
        return clipstick.Chooser(self.sd, clips or self.clips, self.state, self.status, ttl=0, ismount=lambda p: p in self.mounted,
                                 now=now or _time.time, exists=exists)

    def put_status(self, **kw):
        st = {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "in", "why": ""}
        st.update(kw)
        clipstick.write_status(self.status, st)

    def test_no_stick_and_no_claim_is_the_sd_folder_and_nothing_to_say(self):
        c = self.chooser()
        self.assertEqual(c.media_dir(), self.sd)
        self.assertEqual(c.state(), {"state": "none", "label": None, "text": ""})
        self.assertFalse(c.claimed())

    def test_a_claimed_stick_that_is_missing_says_so_and_falls_back_to_the_sd(self):
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        c = self.chooser()
        self.assertEqual(c.media_dir(), self.sd)
        st = c.state()
        self.assertEqual(st["state"], "missing")
        self.assertIn("not plugged in", st["text"])
        self.assertIn("test clips", st["text"])
        self.assertTrue(c.claimed())

    def test_the_panel_knows_of_a_claim_it_cannot_read(self):
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        os.unlink(os.path.join(self.state, clipstick.LABEL_FILE))
        self.assertEqual(self.chooser().state()["state"], "missing")

    def test_checking_then_in_then_pulled(self):
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        self.put_status(state="checking")
        c = self.chooser()
        self.assertEqual((c.media_dir(), c.state()["state"]), (self.sd, "checking"))
        self.assertIn("Checking", c.state()["text"])
        self.put_status()
        self.assertEqual((c.media_dir(), c.state()["state"]), (self.clips, "in"))
        self.mounted.clear()                                  # pulled: the status may be stale, the mount check is not
        self.assertEqual((c.media_dir(), c.state()["state"]), (self.sd, "missing"))

    def test_a_checking_status_left_by_a_killed_unit_is_missing(self):
        import time as _time
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        self.put_status(state="checking")
        self.assertEqual(self.chooser().state()["state"], "checking")
        later = _time.time() + clipstick.FSCK_SECONDS + 301
        self.assertEqual(self.chooser(now=lambda: later).state()["state"], "missing", "older than the longest check")
        self.assertEqual(self.chooser(exists=lambda p: False).state()["state"], "missing", "the device is gone")
        self.put_status(state="in")
        self.assertEqual(self.chooser(exists=lambda p: False).state()["state"], "missing")
        self.put_status(state="in", device="/dev/../etc")
        self.assertEqual(self.chooser().state()["state"], "missing")

    def test_a_read_only_stick_says_why(self):
        self.put_status(state="read-only", why="fsck.exfat exit 4: bad cluster")
        st = self.chooser().state()
        self.assertEqual(st["state"], "read-only")
        self.assertIn("exit 4", st["text"])

    def test_the_clips_folder_must_be_a_real_mount_point(self):
        self.put_status()
        self.mounted.clear()
        self.assertEqual(self.chooser().media_dir(), self.sd)
        link = os.path.join(self.tmp, "link-clips")
        os.symlink(self.clips, link)
        self.mounted.add(link)
        self.assertEqual(self.chooser(clips=link).media_dir(), self.sd)


from tests.test_server import ServerBase   # noqa: E402


class ApiClipStickTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.token = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)
        self.clips = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.clips, True)
        self.status = os.path.join(self.state, "status.json")

    def use_chooser(self, mounted=True):
        self.api.clip_chooser = clipstick.Chooser(self.media, self.clips, self.state, self.status, ttl=0,
                                                  ismount=lambda p: mounted and p == self.clips, exists=lambda p: True)

    def test_while_the_stick_is_missing_the_sd_clips_are_listed_and_uploads_refused(self):
        with open(os.path.join(self.media, "test-grid.mp4"), "wb") as f:
            f.write(b"x")
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        self.use_chooser()
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual(st, 200)
        self.assertIn("test-grid.mp4", body["files"])
        self.assertEqual(body["clip_stick"]["state"], "missing")
        self.assertIn("not plugged in", body["clip_stick"]["text"])
        self.assertNotIn(SECRET, json.dumps(body))
        from mctl.api import ApiError
        before = sorted(os.listdir(self.media))
        with self.assertRaises(ApiError) as e:
            self.api.upload("new.mp4", 3, io.BytesIO(b"new").read)
        self.assertEqual(e.exception.status, 409)
        self.assertIn("not plugged in", e.exception.message)
        self.assertEqual(sorted(os.listdir(self.media)), before, "nothing went to the SD card instead")

    def test_with_the_stick_in_list_upload_and_delete_use_its_clips_folder(self):
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        clipstick.write_status(self.status, {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "in", "why": ""})
        with open(os.path.join(self.clips, "on-stick.mp4"), "wb") as f:
            f.write(b"s")
        self.use_chooser()
        body = self.call("GET", "/api/media", token=self.token)[1]
        self.assertEqual((body["files"], body["clip_stick"]["state"]), (["on-stick.mp4"], "in"))
        sd_before = sorted(os.listdir(self.media))
        self.assertEqual(self.api.upload("added.mp4", 5, io.BytesIO(b"hello").read)["name"], "added.mp4")
        self.assertEqual(sorted(os.listdir(self.clips)), ["added.mp4", "on-stick.mp4"])
        st, body, _ = self.call("POST", "/api/media/delete", {"name": "on-stick.mp4"}, token=self.token)
        self.assertEqual(st, 200, body)
        self.assertEqual(sorted(os.listdir(self.clips)), ["added.mp4"])
        self.assertEqual(sorted(os.listdir(self.media)), sd_before, "the SD folder was not touched")

    def test_a_delete_reads_the_media_folder_once(self):
        """The folder the name was checked in is the folder the file is removed from, even if the media folder changes
        while the delete runs (a stick going in)."""
        sd, stick = os.path.realpath(self.media), self.clips
        for d in (sd, stick):
            with open(os.path.join(d, "twice.mp4"), "wb") as f:
                f.write(b"x")
        calls = []

        class Flip:
            clip_dir = stick

            def choose(self):
                calls.append(1)
                return (sd if len(calls) == 1 else stick), {"state": "none", "label": None, "text": ""}

            def media_dir(self):
                return self.choose()[0]

            def state(self):
                return self.choose()[1]

            def claimed(self):
                return False
        self.api.clip_chooser = Flip()
        self.api.delete_media({"name": "twice.mp4"}, None, None)
        self.assertNotIn("twice.mp4", os.listdir(sd))
        self.assertIn("twice.mp4", os.listdir(stick))

    def test_a_stick_that_was_not_accepted_takes_no_upload(self):
        clipstick.write_claim(self.state, "N01-CLIPS", UUID, SECRET)
        clipstick.write_status(self.status, {"device": "/dev/sdb2", "label": "N01-CLIPS", "state": "not-accepted", "why": ""})
        self.use_chooser()
        body = self.call("GET", "/api/media", token=self.token)[1]
        self.assertEqual(body["clip_stick"]["state"], "not-accepted")
        self.assertIn("sudo nxlx-usb claim /dev/sdb2", body["clip_stick"]["text"])
        from mctl.api import ApiError
        with self.assertRaises(ApiError) as e:
            self.api.upload("new.mp4", 3, io.BytesIO(b"new").read)
        self.assertEqual(e.exception.status, 409)
        self.assertIn("not accepted", e.exception.message)

    def test_without_a_chooser_nothing_changes(self):
        body = self.call("GET", "/api/media", token=self.token)[1]
        self.assertEqual(body["clip_stick"], {"state": "none", "label": None, "text": ""})
        self.assertEqual(self.api.media_dir, self.media)

    def exfat_like(self):
        """os is one module for the whole process: only the media folder behaves like exFAT."""
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
        return mock.patch("mctl.api.os.link", no_links), mock.patch("mctl.api.os.chmod", no_modes)

    def test_writes_on_a_filesystem_without_hard_links_rename_and_never_overwrite(self):
        a, b = self.exfat_like()
        with a, b:
            self.assertEqual(self.api.upload("up1.mp4", 5, io.BytesIO(b"hello").read), {"name": "up1.mp4", "size": 5})
            with open(os.path.join(self.media, "up1.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"hello")
            from mctl.api import ApiError
            with self.assertRaises(ApiError) as e:
                self.api.upload("up1.mp4", 3, io.BytesIO(b"new").read)
            self.assertEqual(e.exception.status, 409)
            self.assertFalse([n for n in os.listdir(self.media) if n.startswith(".upload-")])
            st, body, _ = self.call("POST", "/api/media/rename", {"name": "up1.mp4", "new": "up2.mp4"}, token=self.token)
            self.assertEqual(st, 200, body)
            self.assertEqual(sorted(n for n in os.listdir(self.media) if n.startswith("up")), ["up2.mp4"])
            with open(os.path.join(self.media, "up3.mp4"), "wb") as f:
                f.write(b"c")
            st, body, _ = self.call("POST", "/api/media/rename", {"name": "up2.mp4", "new": "up3.mp4"}, token=self.token)
            self.assertEqual(st, 409, body)
            with open(os.path.join(self.media, "up3.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"c")

    def test_a_name_that_appears_during_an_upload_is_not_overwritten_without_hard_links(self):
        """The check under the media lock in the rename fallback: the name was free when the upload began and taken
        by the time it ended. Without that check os.rename would replace the other file."""
        final = os.path.join(self.media, "race.mp4")
        data = io.BytesIO(b"mine!")

        def read(n):
            if not os.path.exists(final):
                with open(final, "wb") as f:
                    f.write(b"theirs")
            return data.read(n)
        a, b = self.exfat_like()
        from mctl.api import ApiError
        with a, b:
            with self.assertRaises(ApiError) as e:
                self.api.upload("race.mp4", 5, read)
        self.assertEqual(e.exception.status, 409)
        with open(final, "rb") as f:
            self.assertEqual(f.read(), b"theirs")
        self.assertFalse([n for n in os.listdir(self.media) if n.startswith(".upload-")])


if __name__ == "__main__":
    unittest.main()
