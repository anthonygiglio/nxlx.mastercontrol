# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The list of masks (D100): several PNGs combined into one overlay, chosen and ordered in the panel.

What is stored and what the release before makes of it, the route's changes and refusals, the worker (newest change
wins, an overtaken build stops, old files go, a new screen size or a replaced or deleted file builds again), the
crossfade's still beside it, and mpv's real combining: the order and the alpha."""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib

from pvj import boxcare, overlay, pinscreen, transitions
from pvj.settings import Settings
from tests.test_server import ServerBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The release before D100, by its commit (D62's lesson: never by a branch): master at 1a6484b, #151 merged.
BEFORE = "1a6484b835f0494b0e80ec7972f9b9d272478d01"


def wait(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def png(path, w, h, pixel):
    """An RGBA PNG whose pixel at (x, y) is pixel(x, y) = (r, g, b, a)."""
    rows = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(k, b):
        return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class StoredTest(unittest.TestCase):
    def test_the_one_picture_of_before_is_the_first_mask(self):
        self.assertEqual(overlay.masks_of({"file": "logo.png", "on": True}), [{"file": "logo.png", "on": True}])
        self.assertEqual(overlay.masks_of({"file": "", "on": False}), [])
        self.assertEqual(overlay.masks_of({}), [])

    def test_file_is_kept_as_the_first_mask_that_is_on(self):
        cfg = overlay.stored([{"file": "a.png", "on": False}, {"file": "b.png", "on": True}, {"file": "c.png", "on": True}], True)
        self.assertEqual(cfg["file"], "b.png")
        self.assertEqual(overlay.masks_of(cfg), cfg["masks"], "read back as written")
        self.assertEqual(overlay.stored([{"file": "a.png", "on": False}], True)["file"], "")

    def test_a_picture_the_older_release_chose_becomes_the_first_mask(self):
        cfg = overlay.stored([{"file": "a.png", "on": True}, {"file": "b.png", "on": True}], True)
        cfg["file"] = "logo.png"                     # the release before D100 changed its one picture
        self.assertEqual(overlay.masks_of(cfg), [{"file": "logo.png", "on": True}, {"file": "a.png", "on": True}, {"file": "b.png", "on": True}])
        cfg["file"] = "b.png"
        self.assertEqual(overlay.masks_of(cfg)[0], {"file": "b.png", "on": True})

    def test_a_damaged_list_is_read_not_trusted(self):
        cfg = {"on": True, "file": "", "masks": [{"file": "a.png"}, {"file": "a.png", "on": False}, "x", {"file": 3}, None]
               + [{"file": "m%d.png" % i, "on": True} for i in range(12)]}
        got = overlay.masks_of(cfg)
        self.assertEqual(len(got), overlay.MAX_MASKS)
        self.assertEqual(got[0], {"file": "a.png", "on": True})
        self.assertTrue(overlay.showing(cfg))
        self.assertFalse(overlay.showing(dict(cfg, on=False)))
        self.assertFalse(overlay.showing({"on": True, "file": "", "masks": [{"file": "a.png", "on": False}]}))

    def test_an_import_takes_the_list_and_refuses_a_bad_one(self):
        good = {"file": "", "on": True, "masks": [{"file": "a.png", "on": True}, {"file": "b.png", "on": False}]}
        self.assertEqual(boxcare.check_overlay(good, None), overlay.stored(good["masks"], True))
        self.assertEqual(boxcare.check_overlay({"file": "logo.png", "on": True}, None), {"file": "logo.png", "on": True},
                         "a file from before comes in as it was")
        for bad in ({"on": True, "masks": [{"file": "a.mp4", "on": True}]}, {"on": True, "masks": [{"file": "../a.png"}]},
                    {"masks": [{"file": "a.png"}, {"file": "a.png"}]}, {"masks": [{"file": "a.png", "on": "yes"}]},
                    {"masks": [{"file": "m%d.png" % i} for i in range(9)]}, {"masks": "a.png"}, {"on": True, "masks": []}):
            with self.assertRaises(ValueError, msg=bad):
                boxcare.check_overlay(bad, None)


class RollbackTest(ServerBase):
    """The release before D100 (its own code, read from git) loads a file with a list of masks and shows the first
    mask that is on; its own save keeps the list."""

    def test_the_older_release_reads_the_first_mask(self):
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", BEFORE], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % BEFORE)
        self.settings.data["overlay"] = overlay.stored([{"file": "a.png", "on": False}, {"file": "b.png", "on": True},
                                                        {"file": "c.png", "on": True}], True)
        self.settings.save()
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        with open(os.path.join(old, "pvj", "overlay.py")) as f:
            self.assertNotIn("MAX_MASKS", f.read(), "this is the older release")
        script = r'''
import json, sys
from pvj.settings import Settings
from pvj import boxcare
s = Settings(sys.argv[1])
s.load()
cfg = s.data["overlay"]
out = {"schema": s.data["schema"], "file": cfg["file"], "on": cfg["on"], "checked": boxcare.check_overlay(cfg, None)}
s.data["overlay"] = dict(cfg, file="logo.png")       # what its set_overlay does: a copy with its one picture changed
s.save()
print(json.dumps(out))
'''
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(out["schema"], self.settings.data["schema"], "no schema change: the older release loads the file")
        self.assertEqual((out["file"], out["on"]), ("b.png", True), "it shows the first mask that is on")
        self.assertEqual(out["checked"], {"file": "b.png", "on": True})
        # forward again: what the older release chose is the first mask, and the rest of the list is still there
        back = Settings(copy).load()["overlay"]
        self.assertEqual([m["file"] for m in overlay.masks_of(back)], ["logo.png", "a.png", "b.png", "c.png"])


class FakeCombine:
    """Stands in for overlay.combine: writes a file of the right size and remembers what it was given. With `hold`
    it waits until the build is overtaken (or `release` is set)."""

    def __init__(self):
        self.calls, self.cancelled = [], []
        self.hold = False
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self, paths, w, h, out, mpv_bin="mpv", timeout=None, cancelled=None):
        self.calls.append(([os.path.basename(p) for p in paths], w, h))
        if self.hold:
            self.hold = False
            self.started.set()
            while not self.release.is_set():
                if cancelled():
                    self.cancelled.append([os.path.basename(p) for p in paths])
                    raise overlay.Cancelled("overtaken")
                time.sleep(0.005)
        with open(out, "wb") as f:
            f.write(b"\0" * 16)
        return out


class Base(ServerBase):
    def setUp(self):
        super().setUp()
        for i in range(10):
            png(os.path.join(self.media, "m%d.png" % i), 2, 2, lambda x, y: (0, 0, 0, 255))
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.fake = self.api.masks.combine = FakeCombine()
        self.addCleanup(lambda: wait(lambda: not self.api.masks.busy()))

    def post(self, body, token=None):
        return self.call("POST", "/api/overlay", body, token=token or self.full)

    def settled(self):
        self.assertTrue(wait(lambda: not self.api.masks.busy() and self.api.masks.status["state"] != "combining"))
        return self.call("GET", "/api/overlay", token=self.full)[1]

    def shown(self):
        """The files drawn as overlay 10, in order."""
        return [c[2] for c in self.player.calls if c[0] == "overlay_file" and c[1] == overlay.OVERLAY_ID]


class RouteTest(Base):
    def test_add_switch_move_remove_and_the_whole_switch(self):
        self.assertEqual(self.post({"add": "m1.png"})[0], 200)
        self.assertEqual(self.post({"add": "m2.png"})[0], 200)
        st, body, _ = self.post({"on": True})
        self.assertEqual((st, body["status"]["state"] in ("combining", "on")), (200, True))
        body = self.settled()
        self.assertEqual(self.fake.calls[-1], (["m1.png", "m2.png"], 1920, 1080))
        self.assertEqual((body["status"]["state"], body["status"]["count"], body["file"]), ("on", 2, "m1.png"))
        self.assertEqual([m["file"] for m in body["masks"]], ["m1.png", "m2.png"])
        self.post({"mask": "m2.png", "move": "up"})
        self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m2.png", "m1.png"])
        self.post({"mask": "m2.png", "mask_on": False})
        body = self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m1.png"])
        self.assertEqual(body["file"], "m1.png", "file is the first mask that is on, for the release before")
        self.post({"mask": "m2.png", "move": "up"})                         # at the top already: nothing moves
        self.assertEqual([m["file"] for m in self.settled()["masks"]], ["m2.png", "m1.png"])
        self.post({"remove": "m1.png"})
        body = self.settled()
        self.assertEqual((body["status"]["state"], body["status"]["message"]), ("off", "no mask is switched on"))
        self.assertEqual(self.player.calls[-1], ("overlay_remove", overlay.OVERLAY_ID))
        self.post({"on": False})
        self.assertEqual(self.settled()["status"], {"state": "off", "message": "", "count": 0})
        saved = Settings(self.settings.path).load()["overlay"]
        self.assertEqual(saved, {"file": "", "on": False, "masks": [{"file": "m2.png", "on": False}]})

    def test_zero_one_and_eight_masks_and_a_ninth_refused(self):
        self.assertEqual(self.post({"on": True})[0], 400, "nothing to show")
        self.post({"add": "m0.png", "on": True})
        self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m0.png"])
        for i in range(1, 8):
            self.assertEqual(self.post({"add": "m%d.png" % i})[0], 200)
        body = self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m%d.png" % i for i in range(8)])
        self.assertEqual(body["status"]["count"], 8)
        st, body, _ = self.post({"add": "m8.png"})
        self.assertEqual((st, "up to 8" in body["error"]), (400, True))
        self.assertEqual(len(self.settings.data["overlay"]["masks"]), 8)
        for i in range(8):
            self.post({"remove": "m%d.png" % i})
        body = self.settled()
        self.assertEqual((body["on"], body["masks"]), (False, []), "the last one out switches the overlay off")
        self.assertEqual(self.player.calls[-1], ("overlay_remove", overlay.OVERLAY_ID))

    def test_bad_names_other_files_and_missing_ones_are_refused(self):
        open(os.path.join(self.media, "clip.mp4"), "w").close()
        for bad in ({"add": "../m1.png"}, {"add": "clip.mp4"}, {"add": "m1.png\n"}, {"add": 3}, {"add": ".m1.png"},
                    {"file": "a.mp4"}, {"mask": "m1.png"}, {"on": "yes"}):
            self.assertIn(self.post(bad)[0], (400, 404), bad)
        self.assertEqual(self.post({"add": "gone.png"})[0], 404)
        self.post({"add": "m1.png"})
        self.assertEqual(self.post({"add": "m1.png"})[0], 409, "named twice")
        self.assertEqual(self.post({"remove": "m2.png"})[0], 404)
        self.assertEqual(self.post({"mask": "m1.png", "move": "left"})[0], 400)
        self.assertEqual(self.post({"mask": "m1.png", "mask_on": 1})[0], 400)
        self.assertEqual([m["file"] for m in self.settings.data["overlay"]["masks"]], ["m1.png"])

    def test_the_one_picture_of_before_still_works(self):
        # what OSC's /pvj/overlay/file and a controller's overlay button send (osc.py, actions.py)
        self.post({"add": "m1.png"})
        self.post({"add": "m2.png"})
        self.post({"file": "m3.png", "on": True})
        self.assertEqual([m["file"] for m in self.settled()["masks"]], ["m3.png", "m2.png"], "the first mask is replaced")
        self.post({"file": "m2.png"})
        self.assertEqual([m["file"] for m in self.settled()["masks"]], ["m2.png", "m3.png"], "moved to the front")
        self.post({"toggle": True})
        self.assertFalse(self.settled()["on"])
        self.post({"toggle": True})
        self.assertTrue(self.settled()["on"])
        self.post({"file": ""})
        self.assertEqual([m["file"] for m in self.settled()["masks"]], ["m3.png"])

    def test_roles(self):
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        live = self.call("POST", "/api/devices/invite", {"name": "o", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/overlay", token=view)[0], 200)
        for body in ({"add": "m1.png"}, {"remove": "m1.png"}, {"on": True}, {"mask": "m1.png", "mask_on": True}):
            self.assertEqual(self.post(body, token=view)[0], 403, body)
        self.assertEqual(self.post({"add": "m1.png", "on": True}, token=live)[0], 200, "an Operator sets the masks")
        self.assertEqual(self.post({"mask": "m1.png", "move": "down"}, token=live)[0], 200)
        self.assertEqual(self.call("POST", "/api/overlay", {"on": False})[0], 401)


class WorkerTest(Base):
    def test_a_missing_file_and_a_file_that_is_not_a_png_are_left_out_without_a_loop(self):
        self.post({"add": "m1.png"})
        self.post({"add": "m2.png"})
        self.post({"add": "m3.png", "on": True})
        self.settled()
        os.unlink(os.path.join(self.media, "m2.png"))
        with open(os.path.join(self.media, "m3.png"), "w") as f:
            f.write("not a picture")
        self.assertTrue(self.api.masks.watch(), "a deleted and a replaced file build again")
        body = self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m1.png"])
        self.assertEqual(body["status"]["state"], "on")
        self.assertIn("m2.png is not in the media folder any more", body["status"]["message"])
        self.assertIn("m3.png is not a PNG picture", body["status"]["message"])
        self.assertTrue([m for m in body["masks"] if m["file"] == "m2.png"][0]["missing"])
        n = len(self.fake.calls)
        for _ in range(5):
            self.assertFalse(self.api.masks.watch(), "never an error loop")
        self.assertEqual(len(self.fake.calls), n)
        os.unlink(os.path.join(self.media, "m1.png"))
        self.assertTrue(self.api.masks.watch())
        body = self.settled()
        self.assertEqual(self.player.calls[-1], ("overlay_remove", overlay.OVERLAY_ID), "nothing left to show")
        self.assertEqual(body["status"]["state"], "on")
        self.assertIn("m1.png is not in the media folder any more", body["status"]["message"])
        self.assertFalse(self.api.masks.watch())

    def test_a_failed_combining_says_so_once_and_keeps_the_settings(self):
        def fail(*a, **kw):
            raise overlay.OverlayError("the picture could not be read")
        self.api.masks.combine = fail
        st, body, _ = self.post({"add": "m1.png", "on": True})
        self.assertEqual(st, 200)
        body = self.settled()
        self.assertEqual((body["status"]["state"], body["status"]["message"]), ("error", "the picture could not be read"))
        self.assertTrue(self.settings.data["overlay"]["on"])
        self.assertFalse(self.api.masks.watch(), "not tried again and again")

    def test_a_new_screen_size_builds_again(self):
        self.post({"add": "m1.png", "on": True})
        self.settled()
        self.assertFalse(self.api.masks.watch())
        self.player.osd_size = lambda: (1024, 768)                         # a new screen mode (D90 to D92)
        self.assertTrue(self.api.masks.watch())
        self.settled()
        self.assertEqual(self.fake.calls[-1], (["m1.png"], 1024, 768))
        self.assertFalse(self.api.masks.watch())
        self.player.osd_size = lambda: None                                 # the player is restarting: wait for it
        self.assertFalse(self.api.masks.watch())

    def test_a_replaced_file_builds_again(self):
        self.post({"add": "m1.png", "on": True})
        self.settled()
        path = os.path.join(self.media, "m1.png")
        png(path + ".new", 4, 4, lambda x, y: (255, 0, 0, 255))
        os.replace(path + ".new", path)
        self.assertTrue(self.api.masks.watch())
        self.settled()
        self.assertEqual(len(self.fake.calls), 2)

    def test_the_watch_runs_in_the_autostart_tick(self):
        from pvj import autostart
        self.post({"add": "m1.png", "on": True})
        self.settled()

        class Ipc:
            @staticmethod
            def request(*cmd):
                return 7 if cmd == ("get_property", "pid") else None
        self.player.ipc = Ipc
        a = autostart.Autostart(self.api, self.settings, log=lambda *_: None)
        a.tick()                                    # the first sight of the player: the overlay is put back
        self.settled()
        n = len(self.fake.calls)
        self.player.osd_size = lambda: (800, 600)
        a.tick()
        self.settled()
        self.assertEqual((len(self.fake.calls), self.fake.calls[-1][1:]), (n + 1, (800, 600)))

    def test_an_overtaken_build_stops_and_the_newest_wins(self):
        self.fake.hold = True
        self.post({"add": "m1.png", "on": True})
        self.assertTrue(self.fake.started.wait(5))
        self.assertEqual(self.call("GET", "/api/overlay", token=self.full)[1]["status"]["state"], "combining")
        self.post({"add": "m2.png"})                     # while the first is being combined
        body = self.settled()
        self.assertEqual(self.fake.cancelled, [["m1.png"]])
        self.assertEqual(self.fake.calls[-1][0], ["m1.png", "m2.png"])
        self.assertEqual(len(self.shown()), 1, "only the newest is drawn")
        self.assertEqual(body["status"]["count"], 2)

    def test_old_files_are_cleaned(self):
        for n in ("masks-1-1.bgra", "masks-1-2.bgra.tmp", "overlay.bgra", "keep.txt", "mapper-1-1.glsl"):
            open(os.path.join(self.rundir, n), "w").close()
        os.symlink("/etc/hostname", os.path.join(self.rundir, "masks-2-1.bgra"))
        overlay.Masks(self.api)                          # the service starts: an earlier run's files go
        left = os.listdir(self.rundir)
        for n in ("masks-1-1.bgra", "masks-1-2.bgra.tmp", "overlay.bgra"):
            self.assertNotIn(n, left)
        for n in ("keep.txt", "mapper-1-1.glsl", "masks-2-1.bgra"):
            self.assertIn(n, left, "only the masks' own plain files go; never a link")
        self.post({"add": "m1.png", "on": True})
        self.settled()
        first = self.shown()[-1]
        self.post({"add": "m2.png"})
        self.settled()
        second = self.shown()[-1]
        self.assertNotEqual(first, second, "a fresh name each time")
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))
        self.post({"on": False})
        self.settled()
        self.assertFalse([n for n in os.listdir(self.rundir) if n.startswith("masks-") and not os.path.islink(os.path.join(self.rundir, n))])


class CrossfadeSlotTest(unittest.TestCase):
    def test_the_crossfade_still_and_the_pairing_codes_never_use_the_masks_slot(self):
        self.assertEqual(overlay.OVERLAY_ID, 10)
        self.assertEqual(transitions.OVERLAY_ID, 63)
        self.assertNotIn(overlay.OVERLAY_ID, pinscreen.QR_IDS.values())

    def test_masks_from_the_list_make_the_still_one_of_the_window(self):
        class Api:
            class settings:
                data = {"overlay": {"on": True, "file": "", "masks": [{"file": "a.png", "on": True}]}}
        tr = transitions.Transitions(Api, thread=False, log=lambda *_: None)
        self.assertTrue(tr._drawn_over(), "a still of the clip alone would leave the masks out")
        Api.settings.data["overlay"] = {"on": True, "file": "", "masks": [{"file": "a.png", "on": False}]}
        self.assertFalse(tr._drawn_over())


@unittest.skipUnless(shutil.which("mpv"), "needs mpv")
class RealCombineTest(unittest.TestCase):
    """mpv really lays the masks over each other, in order, with normal alpha (runs in CI and on a Pi)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def path(self, name):
        return os.path.join(self.d, name)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def px(self, data, w, x, y):
        return tuple(data[(y * w + x) * 4:(y * w + x) * 4 + 4])

    def near(self, got, want, tol=3):
        self.assertTrue(all(abs(a - b) <= tol for a, b in zip(got, want)), (got, want))

    def test_order_and_alpha(self):
        png(self.path("a.png"), 64, 64, lambda x, y: (255, 0, 0, 255) if x < 32 else (0, 0, 0, 0))     # red, left half
        png(self.path("b.png"), 64, 64, lambda x, y: (0, 0, 255, 128))                                   # blue at half
        out = overlay.combine([self.path("a.png"), self.path("b.png")], 128, 128, self.path("o.bgra"))
        data = self.read(out)
        self.assertEqual(len(data), 128 * 128 * 4)
        self.near(self.px(data, 128, 10, 64), (128, 0, 127, 255))      # B, G, R, A: blue at half over opaque red
        self.near(self.px(data, 128, 110, 64), (128, 0, 0, 128))       # blue at half over nothing, premultiplied
        # the other order: opaque red over blue
        out = overlay.combine([self.path("b.png"), self.path("a.png")], 128, 128, self.path("o.bgra"))
        data = self.read(out)
        self.near(self.px(data, 128, 10, 64), (0, 0, 255, 255))
        self.near(self.px(data, 128, 110, 64), (128, 0, 0, 128))

    def test_eight_masks_each_in_its_place_and_fitted(self):
        colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 255, 255), (128, 64, 32)]
        files = []
        for i, c in enumerate(colours):
            files.append(self.path("m%d.png" % i))
            png(files[-1], 64, 32, lambda x, y, i=i, c=c: c + (255,) if i * 8 <= x < i * 8 + 8 else (0, 0, 0, 0))
        out = overlay.combine(files, 128, 128, self.path("o.bgra"))
        data = self.read(out)
        for i, (r, g, b) in enumerate(colours):
            self.near(self.px(data, 128, i * 16 + 8, 64), (b, g, r, 255))
        self.assertEqual(self.px(data, 128, 64, 10), (0, 0, 0, 0), "the 64 x 32 pictures are fitted and centred")

    def test_a_file_that_is_not_a_picture_and_bounds(self):
        png(self.path("a.png"), 8, 8, lambda x, y: (255, 0, 0, 255))
        with open(self.path("b.png"), "w") as f:
            f.write("#EXTM3U\nhttp://example.com/x\n")
        with self.assertRaises(overlay.OverlayError):
            overlay.combine([self.path("a.png"), self.path("b.png")], 64, 64, self.path("o.bgra"))
        for w, h in ((3841, 100), (100, 2161), (8, 8)):
            with self.assertRaises(overlay.OverlayError):
                overlay.combine([self.path("a.png")] * 2, w, h, self.path("o.bgra"))
        with self.assertRaises(overlay.OverlayError):
            overlay.combine([self.path("a.png")] * 9, 64, 64, self.path("o.bgra"))
        with self.assertRaises(overlay.OverlayError):
            overlay.combine([], 64, 64, self.path("o.bgra"))
        self.assertFalse(os.path.exists(self.path("o.bgra.tmp")))


def header_only(path, w, h):
    """A file that is only a PNG's signature and header saying w x h: a few bytes that claim a huge picture."""
    body = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(overlay.PNG_MAGIC + struct.pack(">I", len(body)) + b"IHDR" + body
                + struct.pack(">I", zlib.crc32(b"IHDR" + body) & 0xFFFFFFFF))


def fake_mpv(folder, script):
    """An "mpv" that runs `script` (sh), for what is asked of the child; $OUT is the file mpv was told to write."""
    path = os.path.join(folder, "mpv")
    with open(path, "w") as f:
        f.write("#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --o=*) OUT=\"${a#--o=}\";; esac; done\n" + script + "\n")
    os.chmod(path, 0o755)
    return path


class CancelTest(unittest.TestCase):
    def test_a_cancelled_build_kills_mpv_and_leaves_nothing(self):
        d, pics = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.addCleanup(shutil.rmtree, pics, True)
        a, b = os.path.join(pics, "a.png"), os.path.join(pics, "b.png")
        png(a, 2, 2, lambda x, y: (0, 0, 0, 255))
        png(b, 2, 2, lambda x, y: (0, 0, 0, 255))
        slow = fake_mpv(d, ': > "$OUT"\nexec sleep 30')
        go = time.monotonic()
        with self.assertRaises(overlay.Cancelled):
            overlay.combine([a, b], 64, 64, os.path.join(d, "o.bgra"), slow, cancelled=lambda: time.monotonic() - go > 0.2)
        self.assertLess(time.monotonic() - go, 5)
        self.assertEqual(sorted(os.listdir(d)), ["mpv"], "the half-written file is gone")
        with self.assertRaises(overlay.OverlayError):
            overlay.combine([a], 64, 64, os.path.join(d, "o.bgra"), slow, timeout=0.3)


class SizeTest(unittest.TestCase):
    """The review of #152: a PNG of 1 MB can say 16000 x 16000, and mpv then needs gigabytes. The header is read
    first, the masks together are bounded, and mpv runs with its memory capped."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ran = os.path.join(self.d, "ran")
        self.mpv = fake_mpv(self.d, ': > "%s"\nexit 1' % self.ran)

    def path(self, name):
        return os.path.join(self.d, name)

    def test_the_header_says_the_size(self):
        header_only(self.path("huge.png"), 16000, 16000)
        png(self.path("ok.png"), 3, 2, lambda x, y: (0, 0, 0, 255))
        self.assertEqual(overlay.png_size(self.path("huge.png")), (16000, 16000))
        self.assertEqual(overlay.png_size(self.path("ok.png")), (3, 2))
        self.assertIsNone(overlay.png_problem(self.path("ok.png")))
        self.assertIn("is 16000 x 16000, larger than a mask may be", overlay.png_problem(self.path("huge.png")))
        for w, h in ((8193, 10), (10, 8193), (8000, 5001), (0, 10)):
            header_only(self.path("x.png"), w, h)
            self.assertIsNotNone(overlay.png_problem(self.path("x.png")), (w, h))
        header_only(self.path("x.png"), 8192, 4882)             # 39.99 megapixels: allowed
        self.assertIsNone(overlay.png_problem(self.path("x.png")))
        with open(self.path("short.png"), "wb") as f:
            f.write(overlay.PNG_MAGIC + b"\0\0\0\rIHD")
        self.assertIn("is not a PNG picture", overlay.png_problem(self.path("short.png")))

    def test_one_picture_and_several_are_checked_before_mpv_runs(self):
        header_only(self.path("huge.png"), 16000, 16000)
        png(self.path("ok.png"), 4, 4, lambda x, y: (0, 0, 0, 255))
        for paths in ([self.path("huge.png")], [self.path("ok.png"), self.path("huge.png")]):
            with self.assertRaises(overlay.OverlayError) as e:
                overlay.combine(paths, 1920, 1080, self.path("o.bgra"), self.mpv)
            self.assertIn("16000 x 16000", str(e.exception))
        with self.assertRaises(overlay.OverlayError):
            overlay.convert(self.path("huge.png"), 1920, 1080, self.path("o.bgra"), self.mpv)
        self.assertFalse(os.path.exists(self.ran), "mpv was never started")

    def test_the_masks_together_are_bounded(self):
        for i in range(5):
            header_only(self.path("k%d.png" % i), 3840, 2160)
        four = [self.path("k%d.png" % i) for i in range(4)]
        self.assertIsNone(overlay.budget_problem([(3840, 2160)] * 4, 3840, 2160))
        self.assertIn("at most 4 masks", overlay.budget_problem([(3840, 2160)] * 5, 3840, 2160))
        self.assertIsNone(overlay.budget_problem([(1920, 1080)] * 8, 1920, 1080))
        self.assertIn("more than 70 megapixels", overlay.budget_problem([(8000, 5000), (6000, 6000)], 1920, 1080))
        with self.assertRaises(overlay.OverlayError) as e:
            overlay.combine(four + [self.path("k4.png")], 3840, 2160, self.path("o.bgra"), self.mpv)
        self.assertIn("at most 4 masks", str(e.exception))
        self.assertFalse(os.path.exists(self.ran))

    def test_mpv_runs_with_its_memory_capped(self):
        png(self.path("a.png"), 2, 2, lambda x, y: (0, 0, 0, 255))
        said = self.path("limits")
        mpv = fake_mpv(self.d, 'echo "$(ulimit -v) $(ulimit -s) $MALLOC_ARENA_MAX" > "%s"\nexit 1' % said)
        with self.assertRaises(overlay.OverlayError):
            overlay.combine([self.path("a.png")] * 2, 64, 64, self.path("o.bgra"), mpv)
        with open(said) as f:
            got = f.read().split()
        self.assertEqual(got, [str(overlay.MPV_MEMORY // 1024), str(overlay.MPV_STACK // 1024), "2"])

    @unittest.skipUnless(shutil.which("mpv"), "needs mpv")
    def test_the_real_mpv_works_under_the_cap(self):
        # the biggest work the bounds allow, on this machine's mpv: four masks of the screen's size at 3840 x 2160
        for i in range(4):
            png(self.path("k%d.png" % i), 64, 36, lambda x, y, i=i: (0, 0, 0, 255) if x == i else (0, 0, 0, 0))
        out = overlay.combine([self.path("k%d.png" % i) for i in range(4)], 3840, 2160, self.path("o.bgra"))
        self.assertEqual(os.path.getsize(out), 3840 * 2160 * 4)


class BudgetWorkerTest(Base):
    def test_a_huge_mask_and_masks_over_the_bounds_are_left_out(self):
        header_only(os.path.join(self.media, "huge.png"), 16000, 16000)
        self.post({"add": "m1.png"})
        self.post({"add": "huge.png", "on": True})
        body = self.settled()
        self.assertEqual(self.fake.calls[-1][0], ["m1.png"])
        self.assertIn("huge.png is 16000 x 16000, larger than a mask may be", body["status"]["message"])
        self.assertFalse(self.api.masks.watch(), "never an error loop")
        self.player.osd_size = lambda: (3840, 2160)
        for i in range(2, 6):
            self.post({"add": "m%d.png" % i})
        body = self.settled()
        self.assertEqual(self.fake.calls[-1], (["m1.png", "m2.png", "m3.png", "m4.png"], 3840, 2160))
        self.assertIn("m5.png: at 3840 x 2160 at most 4 masks are combined", body["status"]["message"])


class ConcurrentAddTest(Base):
    def test_two_adds_at_once_both_land(self):
        # the first Add is held inside its look at the media folder; the second comes meanwhile. With the read and
        # the write under one take of the settings' lock, the second waits and both are in the list.
        real, held = self.api.resolve_media, threading.Event()
        first = []

        def slow(name):
            if not first:
                first.append(name)
                held.set()
                time.sleep(0.5)
            return real(name)
        self.api.resolve_media = slow
        out = []
        a = threading.Thread(target=lambda: out.append(self.api.set_overlay({"add": "m1.png"}, None, "t")))
        a.start()
        self.assertTrue(held.wait(5))
        b = threading.Thread(target=lambda: out.append(self.api.set_overlay({"add": "m2.png"}, None, "t")))
        b.start()
        a.join(10)
        b.join(10)
        self.assertEqual(len(out), 2)
        self.assertEqual(sorted(m["file"] for m in self.settings.data["overlay"]["masks"]), ["m1.png", "m2.png"])
        self.assertEqual(sorted(m["file"] for m in Settings(self.settings.path).load()["overlay"]["masks"]), ["m1.png", "m2.png"])


if __name__ == "__main__":
    unittest.main()
