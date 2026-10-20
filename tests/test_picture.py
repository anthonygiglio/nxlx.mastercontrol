# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import shutil
import struct
import tempfile
import time
import unittest
import zlib
from unittest import mock

from pvj import autostart, overlay
from pvj.settings import Settings
from tests.test_server import ServerBase


def rgba_png(path, w, h, pixel):
    """A PNG with every pixel `pixel` (r, g, b, a) except a transparent left half."""
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            rows += bytes(pixel) if x >= w // 2 else bytes((0, 0, 0, 0))

    def chunk(k, b):
        return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


class FlipAndPositionTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, body):
        return self.call("POST", "/api/control", body, token=self.full)

    def test_vertical_position_keeps_the_horizontal_one(self):
        self.post({"action": "position", "value": 20})
        self.post({"action": "position_y", "value": -30})
        self.assertEqual(self.player.calls[-1], ("position", 200, -300))
        self.assertEqual(self.post({"action": "position_y", "value": 101})[0], 400)

    def test_flip_on_off_and_reset_undoes_it(self):
        self.assertEqual(self.post({"action": "flip_h", "value": True})[0], 200)
        self.assertIn(("flip", True, True), self.player.calls)
        self.post({"action": "flip_v", "value": True})
        self.assertEqual(self.post({"action": "flip_h", "value": "yes"})[0], 400)
        self.post({"action": "reset"})
        self.assertIn(("flip", True, False), self.player.calls)
        self.assertIn(("flip", False, False), self.player.calls)
        st = self.call("GET", "/api/status", token=self.full)[1]["mix"]
        self.assertEqual((st["flip_h"], st["flip_v"], st["position_y"]), (False, False, 0))


class OverlayApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        open(os.path.join(self.media, "logo.png"), "wb").close()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.converted = []

        rgba_png(os.path.join(self.media, "logo.png"), 2, 2, (0, 0, 0, 255))      # a real one: its header is read (D100)

        def fake_convert(src, w, h, out, mpv_bin="mpv", timeout=30, cancelled=None):
            self.converted.append((os.path.basename(src), w, h))
            open(out, "wb").close()
            return out
        p = mock.patch("pvj.overlay.convert", fake_convert)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.settled)

    def post(self, body, token=None):
        return self.call("POST", "/api/overlay", body, token=token or self.full)

    def settled(self):
        """The masks are combined in a worker (D100): wait until it is done."""
        end = time.monotonic() + 10
        while time.monotonic() < end and (self.api.masks.busy() or self.api.masks.status["state"] == "combining"):
            time.sleep(0.01)
        return self.api.masks.status

    def test_on_converts_at_the_screen_size_and_draws_it_off_removes_it(self):
        st, body, _ = self.post({"file": "logo.png", "on": True})
        self.assertEqual((st, body["on"], body["choices"]), (200, True, ["logo.png"]))
        self.assertEqual(self.settled()["state"], "on")
        self.assertEqual(self.converted, [("logo.png", 1920, 1080)])
        drawn = [c for c in self.player.calls if c[0] == "overlay_file"]
        self.assertEqual([(c[1], os.path.dirname(c[2]), c[3], c[4]) for c in drawn], [(overlay.OVERLAY_ID, self.rundir, 1920, 1080)])
        self.assertTrue(Settings(self.settings.path).load()["overlay"]["on"])
        st, body, _ = self.post({"on": False})
        self.assertEqual(self.player.calls[-1], ("overlay_remove", overlay.OVERLAY_ID))

    def test_only_png_files_from_the_media_folder(self):
        for bad in ({"file": "../etc/x.png", "on": True}, {"file": "a.mp4", "on": True}, {"file": "logo.png\n"}, {"on": True}, {"on": "yes"}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        self.assertEqual(self.post({"file": "missing.png", "on": True})[0], 404)
        self.assertFalse(self.settings.data["overlay"]["on"])                 # a failed attempt leaves it as it was

    def test_a_failed_conversion_says_so_in_the_status(self):
        # D100: the picture is made in a worker, so the request no longer waits for it; the status says what failed
        with mock.patch("pvj.overlay.convert", side_effect=overlay.OverlayError("the picture could not be read")):
            st, body, _ = self.post({"file": "logo.png", "on": True})
            self.assertEqual(st, 200)
            status = self.settled()
        self.assertEqual((status["state"], "could not be read" in status["message"]), ("error", True))
        self.assertNotIn("overlay_file", [c[0] for c in self.player.calls])

    def test_roles(self):
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/overlay", token=view)[0], 200)
        self.assertEqual(self.post({"on": False}, token=view)[0], 403)

    def test_a_restarted_player_gets_the_overlay_back(self):
        self.post({"file": "logo.png", "on": True})
        self.settled()                      # the post's own build is done before counting (it runs in a worker)
        self.converted.clear()
        pid = [1]
        real_ipc = self.player.ipc

        class Ipc:
            @staticmethod
            def request(*cmd):
                return pid[0] if cmd == ("get_property", "pid") else None
        self.player.ipc = Ipc
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        a.tick()
        self.settled()
        pid[0] = 2
        a.tick()
        self.settled()
        self.assertEqual(len(self.converted), 2)
        self.player.ipc = real_ipc


@unittest.skipUnless(shutil.which("mpv"), "needs mpv")
class RealConversionTest(unittest.TestCase):
    """mpv really turns a transparent PNG into premultiplied BGRA at the screen size (runs in CI and on a Pi)."""

    def test_fitted_centred_premultiplied(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "logo.png")
        rgba_png(src, 64, 32, (255, 0, 0, 128))
        out = overlay.convert(src, 128, 128, os.path.join(d, "o.bgra"))
        data = open(out, "rb").read()
        self.assertEqual(len(data), 128 * 128 * 4)
        px = lambda x, y: tuple(data[(y * 128 + x) * 4:(y * 128 + x) * 4 + 4])
        self.assertEqual(px(64, 5), (0, 0, 0, 0))                              # padding above the fitted picture
        self.assertEqual(px(10, 64), (0, 0, 0, 0))                             # the transparent half
        b, g, r, a = px(110, 64)
        self.assertTrue(abs(a - 128) <= 3 and abs(r - 128) <= 3 and b < 4 and g < 4, (b, g, r, a))   # red at half alpha, premultiplied

    def test_a_file_that_is_not_a_picture_is_refused(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "fake.png")
        with open(src, "w") as f:
            f.write("#EXTM3U\nhttp://example.com/x\n")
        with self.assertRaises(overlay.OverlayError):
            overlay.convert(src, 64, 64, os.path.join(d, "o.bgra"))

    def test_bad_sizes(self):
        for w, h in ((0, 10), (5000, 10), (10, 10), ("64", 64)):
            with self.assertRaises(overlay.OverlayError):
                overlay.convert("/nonexistent.png", w, h, "/tmp/never")


if __name__ == "__main__":
    unittest.main()
