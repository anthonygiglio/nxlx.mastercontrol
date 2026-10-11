# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import shutil
import os
import unittest
from unittest import mock

from tests.test_server import ServerBase


class UsbMediaTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.usb = os.path.join(self.tmp, "usbroot")
        drive = os.path.join(self.usb, "NXLX-USB")
        os.makedirs(os.path.join(drive, "System Volume Information"))
        os.makedirs(os.path.join(self.usb, "OTHER"))
        for n, size in (("Film One.mp4", 5000), ("b.mkv", 10), (".hidden.mp4", 1), ("notes.txt", 3)):
            with open(os.path.join(drive, n), "wb") as f:
                f.write(b"x" * size)
        with open(os.path.join(drive, "System Volume Information", "in.mp4"), "wb") as f:
            f.write(b"x")
        outside = os.path.join(self.tmp, "secret.mp4")
        with open(outside, "wb") as f:
            f.write(b"secret")
        os.symlink(outside, os.path.join(drive, "link.mp4"))
        os.symlink(drive, os.path.join(self.usb, "LINKED"))
        self.api.usb_root = self.usb
        self.api.usb_link = os.path.join(self.tmp, "usb")
        os.symlink(drive, self.api.usb_link)
        self.token = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def play(self, **body):
        return self.call("POST", "/api/play", body, token=self.token)

    def test_lists_only_real_media_at_the_top_of_real_drives(self):
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual([d["drive"] for d in body["usb"]], ["NXLX-USB", "OTHER"])   # the LINKED alias is skipped
        first = body["usb"][0]["files"]
        self.assertEqual([(f["name"], f["size"]) for f in first], [("b.mkv", 10), ("Film One.mp4", 5000)])   # no hidden, txt, link or subfolder files
        self.assertEqual(body["usb"][1]["files"], [])

    def test_plays_a_file_straight_from_the_drive(self):
        st, body, _ = self.play(usb="NXLX-USB/Film One.mp4")
        self.assertEqual((st, body), (200, {"playing": "NXLX-USB/Film One.mp4"}))
        real = os.path.join(os.path.realpath(self.usb), "NXLX-USB", "Film One.mp4")
        self.assertIn(("play", [real], True, False), self.player.calls)

    def test_nothing_outside_the_drive_can_be_played(self):
        for ref in ("../secret.mp4", "NXLX-USB/../../secret.mp4", "NXLX-USB/link.mp4", "LINKED/b.mkv", "NXLX-USB/System Volume Information/in.mp4",
                    "NXLX-USB/notes.txt", "NXLX-USB/.hidden.mp4", "NXLX-USB/nope.mp4", "/etc/passwd", "NXLX-USB", "NXLX-USB/", "", "a/b/c.mp4",
                    "NXLX-USB\\b.mkv", "NXLX-USB/b.mkv\n", "..", ".", None, 5, ["NXLX-USB/b.mkv"], "NXLX-USB/../NXLX-USB/b.mkv"):
            before = len(self.player.calls)
            st, body, _ = self.play(usb=ref)
            self.assertIn(st, (400, 404), ref)
            self.assertEqual(len(self.player.calls), before, ref)

    def test_roles(self):
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.token)[1]["token"]
        self.assertEqual(self.call("GET", "/api/media", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"usb": "NXLX-USB/b.mkv"}, token=view)[0], 403)

    def test_old_usb_presets_now_play_from_the_drive(self):
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual((st, body["files"]), (200, 2))                      # b.mkv and Film One.mp4; not the link or the txt
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual(st, 200)

    def test_the_usb_link_must_point_at_a_mounted_drive(self):
        os.unlink(self.api.usb_link)
        os.symlink(self.tmp, self.api.usb_link)                              # a link to somewhere else entirely
        before = len(self.player.calls)
        st, _, _ = self.play(preset="startmasterusb")
        self.assertEqual(st, 404)
        self.assertEqual(len(self.player.calls), before)

    def test_hostile_names_are_not_listed_and_not_played_by_a_preset(self):
        drive = os.path.join(self.usb, "NXLX-USB")
        for name in ("bad\nname.mp4", "bidi\u202egpm.mp4", "ctrl\x07.mp4", "x" * 300 + ".mp4"):
            try:
                open(os.path.join(drive, name), "wb").write(b"x")
            except OSError:
                pass
        self.api._usb_cache = (0.0, [])
        names = [f["name"] for f in self.call("GET", "/api/media", token=self.token)[1]["usb"][0]["files"]]
        self.assertEqual(names, ["b.mkv", "Film One.mp4"])
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual((st, body["files"]), (200, 2))                     # the preset applies the same name rules

    def test_a_huge_directory_is_scanned_only_up_to_a_limit_and_cached(self):
        from pvj import api as api_mod
        drive = os.path.join(self.usb, "OTHER")
        for i in range(60):
            open(os.path.join(drive, "f%03d.mp4" % i), "wb").write(b"x")
        with mock.patch.object(api_mod, "USB_SCAN_LIMIT", 25):
            self.api._usb_cache = (0.0, [])
            other = [d for d in self.call("GET", "/api/media", token=self.token)[1]["usb"] if d["drive"] == "OTHER"][0]
            self.assertLessEqual(len(other["files"]), 25)
            self.assertTrue(other["truncated"])
        real = os.scandir
        calls = []

        def counting(path):
            calls.append(path)
            return real(path)
        with mock.patch("pvj.api.os.scandir", counting):
            for _ in range(5):
                self.call("GET", "/api/media", token=self.token)
        self.assertEqual(len(calls), 0)                                      # served from the cache within a couple of seconds

    def test_a_preset_queues_at_most_a_bounded_number_of_files(self):
        from pvj import api as api_mod
        drive = os.path.join(self.usb, "NXLX-USB")
        for i in range(30):
            open(os.path.join(drive, "m%02d.mp4" % i), "wb").write(b"x")
        with mock.patch.object(api_mod, "PRESET_MAX_FILES", 10):
            st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual((st, body["files"]), (200, 10))
        self.assertEqual(len(self.player.calls[-2][1]) if self.player.calls[-1][0] != "play" else len(self.player.calls[-1][1]), 10)

    def test_dot_labels_and_hidden_drive_folders_are_refused(self):
        os.makedirs(os.path.join(self.usb, ".hiddendrive"))
        open(os.path.join(self.usb, ".hiddendrive", "a.mp4"), "wb").write(b"x")
        self.api._usb_cache = (0.0, [])
        self.assertNotIn(".hiddendrive", [d["drive"] for d in self.call("GET", "/api/media", token=self.token)[1]["usb"]])
        for ref in (".hiddendrive/a.mp4", "../usbroot/NXLX-USB/b.mkv", "./b.mkv", "../b.mkv"):
            self.assertIn(self.play(usb=ref)[0], (400, 404), ref)

    def test_an_old_image_with_a_real_folder_at_media_usb_still_works(self):
        os.unlink(self.api.usb_link)
        os.makedirs(self.api.usb_link)
        open(os.path.join(self.api.usb_link, "old.mp4"), "wb").write(b"x")
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual((st, body["files"]), (200, 1))

    def test_no_drive_is_an_empty_list_not_an_error(self):
        self.api.usb_root = os.path.join(self.tmp, "nowhere")
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual((st, body["usb"]), (200, []))
        self.assertEqual(self.play(usb="NXLX-USB/b.mkv")[0], 404)



class UsbImportTest(ServerBase):
    """Copy a clip from a USB drive into the media folder (the old "Loading from USB to internal")."""

    def setUp(self):
        super().setUp()
        import tempfile as _t
        self.usb = _t.mkdtemp()
        self.addCleanup(shutil.rmtree, self.usb, True)
        os.makedirs(os.path.join(self.usb, "SHOW"))
        self.data = os.urandom(3 * 1024 * 1024 + 17)
        with open(os.path.join(self.usb, "SHOW", "film.mp4"), "wb") as f:
            f.write(self.data)
        self.api.usb_root = self.usb
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def wait(self):
        import time as _time
        end = _time.monotonic() + 20
        while _time.monotonic() < end:
            st = self.call("GET", "/api/media/import", token=self.full)[1]
            if not st.get("active"):
                return st
            _time.sleep(0.05)
        self.fail("the copy did not finish")

    def test_copy_then_refuse_a_second_copy_unless_replacing(self):
        st, body, _ = self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4"}, token=self.full)
        self.assertEqual(st, 200, body)
        done = self.wait()
        self.assertEqual(done["result"], {"name": "film.mp4", "size": len(self.data)})
        with open(os.path.join(self.media, "film.mp4"), "rb") as f:
            self.assertEqual(f.read(), self.data)
        self.assertFalse([n for n in os.listdir(self.media) if n.startswith(".upload-")])      # no temporary file left
        self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4"}, token=self.full)
        self.assertIn("already exists", self.wait()["error"])
        self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4", "replace": True}, token=self.full)
        self.assertIn("result", self.wait())

    def test_bad_references_and_roles(self):
        for ref in ("../SHOW/film.mp4", "SHOW/../film.mp4", "SHOW", "SHOW/missing.mp4", "SHOW/notes.txt", 5):
            self.assertIn(self.call("POST", "/api/media/import", {"usb": ref}, token=self.full)[0], (400, 404), ref)
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]     # an Operator copies since D80
        self.assertEqual(self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4"}, token=view)[0], 403)

    def test_an_empty_file_and_a_stick_that_stops_answering(self):
        open(os.path.join(self.usb, "SHOW", "empty.mp4"), "w").close()
        st, body, _ = self.call("POST", "/api/media/import", {"usb": "SHOW/empty.mp4"}, token=self.full)
        self.assertEqual((st, body["error"]), (400, "that file is empty"))
        import io
        from unittest import mock
        real_fdopen = os.fdopen

        class Failing(io.RawIOBase):            # a stick that stops answering half-way
            def __init__(self, fd):
                self.inner = real_fdopen(fd, "rb")
                self.n = 0

            def fileno(self):
                return self.inner.fileno()

            def read(self, n=-1):
                self.n += 1
                if self.n > 1:
                    raise OSError(5, "Input/output error")
                return self.inner.read(n)

            def close(self):
                self.inner.close()
        with mock.patch("pvj.api.os.fdopen", lambda fd, mode="r", *a, **k: Failing(fd) if mode == "rb" else real_fdopen(fd, mode, *a, **k)):
            self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4"}, token=self.full)
            self.assertIn("stopped answering", self.wait()["error"])
        self.assertFalse([n for n in os.listdir(self.media) if n.startswith(".upload-")])      # the half file is gone

    def test_cancel(self):
        import threading
        gate = threading.Event()
        real = self.api.upload

        def slow_upload(name, length, read, replace=False, check=None):
            def slow_read(n):
                gate.wait(5)
                return read(min(n, 4096))
            return real(name, length, slow_read, replace=replace, check=check)
        self.api.upload = slow_upload
        self.call("POST", "/api/media/import", {"usb": "SHOW/film.mp4"}, token=self.full)
        self.call("POST", "/api/media/import/cancel", {}, token=self.full)
        gate.set()
        self.assertIn("cancelled", self.wait()["error"])
        self.assertFalse(os.path.exists(os.path.join(self.media, "film.mp4")))

class UsbDriveAndAutostartPadTest(UsbMediaTest):
    def test_play_every_clip_of_one_drive(self):
        st, body, _ = self.call("POST", "/api/play", {"usb_drive": "NXLX-USB"}, token=self.token)
        self.assertEqual((st, body["playing"], body["files"]), (200, "NXLX-USB", 2))    # Film One.mp4 and b.mkv; not the link
        self.assertEqual(self.call("POST", "/api/play", {"usb_drive": "NOPE"}, token=self.token)[0], 404)
        self.assertEqual(self.call("POST", "/api/play", {"usb_drive": "LINKED"}, token=self.token)[0], 404)

    def test_autostart_refuses_an_empty_pad_and_media_says_usb_autostart_is_on(self):
        from pvj import autostart
        self.api.autostart = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        st, body, _ = self.call("POST", "/api/autostart", {"mode": "pad", "pad": [2, 11]}, token=self.token)
        self.assertEqual((st, body["error"]), (400, "choose a pad that has a clip or a shader"))
        self.call("POST", "/api/autostart", {"mode": "usb"}, token=self.token)
        self.assertTrue(self.call("GET", "/api/media", token=self.token)[1]["autostart_usb"])

if __name__ == "__main__":
    unittest.main()
