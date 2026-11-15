# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import unittest

from mctl.player import Player
from tests.test_server import ServerBase


class EndingsShuffleSlideshowTest(ServerBase):
    def setUp(self):
        super().setUp()
        for n in ("01_a.mp4", "02_b.mp4", "p1.jpg", "p2.png", "song.mp3", "Z.gif"):
            open(os.path.join(self.media, n), "wb").close()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_endings_for_a_single_clip(self):
        for ending, expect in (("loop", "loop"), ("stop", "stop"), ("hold", "hold"), ("next", "stop")):
            self.assertEqual(self.post("/api/play", {"file": "a.mp4", "ending": ending})[0], 200)
            self.assertEqual(self.player.plays[-1]["ending"], expect, ending)
        self.post("/api/play", {"file": "a.mp4", "loop": False})
        self.assertEqual(self.player.plays[-1]["ending"], "stop")                  # the old loop flag still works
        self.assertEqual(self.post("/api/play", {"file": "a.mp4", "ending": "explode"})[0], 400)

    def test_audio_files_play_but_play_all_leaves_them_out(self):
        self.assertEqual(self.post("/api/play", {"file": "song.mp3"})[0], 200)
        st, body, _ = self.post("/api/play", {"preset": "startless"})
        names = [os.path.basename(p) for p in self.player.plays[-1]["paths"]]
        self.assertNotIn("song.mp3", names)
        self.assertIn("01_a.mp4", names)

    def test_play_all_can_shuffle_and_can_hold_at_the_end(self):
        self.post("/api/play", {"preset": "startlessonce", "shuffle": True, "ending": "hold"})
        p = self.player.plays[-1]
        self.assertEqual(p["ending"], "hold")
        self.assertEqual(sorted(p["paths"]), sorted(self.player.plays[-1]["paths"]))
        orders = set()
        for _ in range(12):
            self.post("/api/play", {"preset": "startless", "shuffle": True})
            orders.add(tuple(self.player.plays[-1]["paths"]))
        self.assertGreater(len(orders), 1)                                         # really random
        self.assertEqual(self.post("/api/play", {"preset": "startless", "shuffle": "yes"})[0], 400)

    def test_slideshow_of_the_media_folder(self):
        st, body, _ = self.post("/api/play", {"slideshow": {"source": "media", "seconds": 5}})
        self.assertEqual((st, body["images"], body["seconds"]), (200, 3, 5.0))
        p = self.player.plays[-1]
        self.assertEqual([os.path.basename(x) for x in p["paths"]], ["p1.jpg", "p2.png", "Z.gif"])   # images only, name order
        self.assertEqual((p["image_seconds"], p["ending"]), (5.0, "loop"))
        for bad in ({"seconds": 0}, {"seconds": 4000}, {"seconds": 5, "source": "../x"}, {"seconds": 5, "ending": "x"}, "x"):
            self.assertEqual(self.post("/api/play", {"slideshow": bad})[0], 400, bad)

    def test_slideshow_from_a_usb_drive_and_a_missing_one(self):
        root = os.path.join(self.tmp, "usbroot")
        os.makedirs(os.path.join(root, "STICK"))
        for n in ("s1.jpg", "s2.jpg", "movie.mp4"):
            open(os.path.join(root, "STICK", n), "wb").close()
        self.api.usb_root = root
        st, body, _ = self.post("/api/play", {"slideshow": {"source": "STICK", "seconds": 0.5, "ending": "stop"}})
        self.assertEqual((st, body["images"]), (200, 2))
        self.assertEqual(self.post("/api/play", {"slideshow": {"source": "GONE", "seconds": 1}})[0], 404)

    def test_a_folder_with_no_images(self):
        for n in ("p1.jpg", "p2.png", "Z.gif"):
            os.unlink(os.path.join(self.media, n))
        self.assertEqual(self.post("/api/play", {"slideshow": {"seconds": 1}})[0], 404)

    def test_pads_remember_their_ending(self):
        self.post("/api/pads", {"bank": 0, "index": 2, "label": "Sting", "file": "a.mp4", "ending": "hold"})
        self.post("/api/play", {"pad": [0, 2]})
        self.assertEqual(self.player.plays[-1]["ending"], "hold")
        self.post("/api/play", {"pad": [0, 2], "ending": "loop"})
        self.assertEqual(self.player.plays[-1]["ending"], "loop")                  # asked for explicitly
        self.post("/api/pads", {"bank": 0, "index": 3, "label": "Old", "file": "a.mp4"})
        self.post("/api/play", {"pad": [0, 3]})
        self.assertEqual(self.player.plays[-1]["ending"], "loop")                  # the default
        self.assertEqual(self.post("/api/pads", {"bank": 0, "index": 4, "file": "a.mp4", "ending": "next"})[0], 400)

    def test_shuffle_control_and_roles(self):
        self.assertEqual(self.post("/api/control", {"action": "shuffle"})[0], 200)
        self.assertIn(("shuffle",), self.player.calls)
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        self.assertEqual(self.post("/api/play", {"slideshow": {"seconds": 1}}, token=view)[0], 403)


class PlayerEndingTest(unittest.TestCase):
    def run_play(self, paths, **kw):
        p = Player.__new__(Player)
        sent = []

        class Ipc:
            def request(self, *cmd):
                sent.append(cmd)
                if cmd[:2] == ("get_property", "path"):
                    return paths[0]
                return True if cmd[:2] == ("get_property", "pid") else None
        p.ipc = Ipc()
        p.play(paths, spawn=False, **kw)
        return {c[1]: c[2] for c in sent if c[0] == "set_property"}

    def test_hold_keeps_the_last_frame_and_a_list_of_images_gets_its_slide_time(self):
        props = self.run_play(["/m/a.mp4"], ending="hold")
        self.assertEqual((props["keep-open"], props["loop-file"]), ("yes", "no"))
        props = self.run_play(["/m/1.jpg", "/m/2.jpg"], ending="loop", image_seconds=7)
        self.assertEqual((props["image-display-duration"], props["loop-playlist"], props["keep-open"]), (7.0, "inf", "no"))
        props = self.run_play(["/m/1.jpg"], loop=True)
        self.assertEqual(props["image-display-duration"], "inf")                   # a single image stays up
        props = self.run_play(["/m/a.mp4", "/m/b.mp4"], ending="next")
        self.assertEqual((props["loop-playlist"], props["loop-file"]), ("no", "no"))


if __name__ == "__main__":
    unittest.main()
