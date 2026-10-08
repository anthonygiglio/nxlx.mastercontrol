# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import stat
import tempfile
import threading
import time
import unittest

from pvj import capture
from tests.test_server import ServerBase


def fake_sysfs(entries, usb=True):
    d = tempfile.mkdtemp()
    bus = os.path.join(d, "devices", "platform", "usb1" if usb else "soc", "dev")
    os.makedirs(bus)
    for node, name, index in entries:
        os.makedirs(os.path.join(d, node))
        on_usb = usb and not name.startswith(("bcm2835", "rpi-"))
        os.symlink(bus if on_usb else os.path.join(d, "devices"), os.path.join(d, node, "device"))
        with open(os.path.join(d, node, "name"), "w") as f:
            f.write(name + "\n")
        with open(os.path.join(d, node, "index"), "w") as f:
            f.write(index + "\n")
    return d


class ListTest(unittest.TestCase):
    def test_only_real_inputs_first_node_only(self):
        d = fake_sysfs([("video0", "USB3.0 UHD: USB3.0 UHD", "0"), ("video1", "USB3.0 UHD: USB3.0 UHD", "1"),
                        ("video10", "bcm2835-codec-decode", "0"), ("video13", "bcm2835-isp-output0", "0"),
                        ("video19", "rpi-hevc-dec", "0"), ("video2", "HD Webcam <x>", "0"), ("junk", "x", "0")])
        self.assertEqual(capture.list_devices(d), [{"id": "video0", "name": "USB3.0 UHD: USB3.0 UHD"}, {"id": "video2", "name": "HD Webcam x"}])
        self.assertEqual(capture.list_devices("/nonexistent"), [])

    def test_a_device_that_is_not_on_usb_is_not_an_input(self):
        d = fake_sysfs([("video0", "some codec", "0")], usb=False)
        self.assertEqual(capture.list_devices(d), [])


class FakeProc:
    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        self.signals = []
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def send_signal(self, s):
        self.signals.append(s)
        self.alive = False

    def wait(self, timeout=None):
        import subprocess
        if self.alive:
            raise subprocess.TimeoutExpired("mpv", timeout)
        return 0

    def kill(self):
        self.alive = False


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.procs = []

        def spawn(args, **kw):
            p = FakeProc(args, **kw)
            self.procs.append(p)
            return p
        self.c = capture.Capture(self.dir, lister=lambda: [{"id": "video0", "name": "stick"}], spawn=spawn, log=lambda *_: None)

    def reader(self):
        """Stand in for the player: open the pipe for reading."""
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("fd", os.open(self.c.fifo, os.O_RDONLY)))
        t.start()
        return t, box

    def test_start_runs_the_helper_on_the_pipe_and_stop_ends_it(self):
        self.assertEqual(self.c.prepare("video0", "1080p30"), (1920, 1080, 30))
        self.assertTrue(stat.S_ISFIFO(os.stat(self.c.fifo).st_mode))
        t, box = self.reader()
        self.c.start("video0", "1080p30")
        t.join(2)
        p = self.procs[0]
        self.assertEqual(p.args[-1], "av://v4l2:/dev/video0")
        self.assertIn("--no-config", p.args)
        self.assertIn("--demuxer-lavf-o=input_format=yuyv422,video_size=1920x1080,framerate=30", p.args)
        self.assertIn("--vf=scale=1920:1080,format=yuyv422", p.args)          # always the layout the player expects
        self.assertEqual(self.c.status()["current"], {"device": "video0", "mode": "1080p30"})
        self.assertTrue(self.c.stop())
        self.assertEqual(len(p.signals), 1)
        self.assertFalse(os.path.exists(self.c.fifo))
        self.assertFalse(self.c.stop())
        os.close(box["fd"])

    def test_only_listed_devices_and_known_modes(self):
        for dev, mode in (("video1", "720p30"), ("../../dev/sda", "720p30"), ("video0", "1080p60"), ("video0", "720p60"), (5, "720p30"),
                          ("video0\n", "720p30"), ("video0", ["720p30"]), ("video0", {"a": 1})):
            with self.assertRaises(capture.CaptureError, msg=(dev, mode)):
                self.c.prepare(dev, mode)

    def test_a_player_that_never_opens_the_pipe_is_an_error_not_a_hang(self):
        self.c.prepare("video0", "720p30")
        started = time.monotonic()
        with self.assertRaises(capture.CaptureError):
            self.c.start("video0", "720p30", timeout=0.3)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.procs, [])

    def test_a_new_input_stops_the_old_helper_first(self):
        self.c.prepare("video0", "720p30")
        t, box = self.reader()
        self.c.start("video0", "720p30")
        t.join(2)
        first = self.procs[0]
        self.c.prepare("video0", "1080p30")
        self.assertFalse(first.alive)
        os.close(box["fd"])


class ReviewFindingsTest(CaptureTest):
    def test_stop_removes_the_pipe_even_with_no_helper(self):
        self.c.prepare("video0", "720p30")
        self.assertTrue(os.path.exists(self.c.fifo))
        self.c.stop()
        self.assertFalse(os.path.exists(self.c.fifo))

    def test_a_helper_that_dies_at_once_is_an_error_and_is_cleaned_up(self):
        def dying(args, **kw):
            p = FakeProc(args, **kw)
            p.alive = False
            self.procs.append(p)
            return p
        self.c._spawn = dying
        self.c.prepare("video0", "720p30")
        t, box = self.reader()
        with self.assertRaises(capture.CaptureError):
            self.c.start("video0", "720p30")
        t.join(2)
        self.assertIsNone(self.c.proc)
        self.assertFalse(os.path.exists(self.c.fifo))
        os.close(box["fd"])

    def test_a_pipe_swapped_for_a_regular_file_is_never_written(self):
        self.c.prepare("video0", "720p30")
        os.unlink(self.c.fifo)
        with open(self.c.fifo, "w") as f:
            f.write("do not fill me")
        with self.assertRaises(capture.CaptureError):
            self.c.start("video0", "720p30", timeout=0.3)
        self.assertEqual(self.procs, [])

    def test_status_never_waits_for_the_lock(self):
        with self.c.lock:
            done = []
            t = threading.Thread(target=lambda: done.append(self.c.status()))
            t.start()
            t.join(1)
            self.assertTrue(done)

    def test_two_requests_at_once_leave_exactly_one_helper(self):
        results = []

        def one():
            with self.c.lock:
                self.c.prepare("video0", "720p30")
                t, box = self.reader()
                self.c.start("video0", "720p30")
                t.join(2)
                os.close(box["fd"])
            results.append(1)
        threads = [threading.Thread(target=one) for _ in range(4)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(len(results), 4)
        self.assertEqual(sum(p.alive for p in self.procs), 1)                     # every earlier helper was stopped


class OldMpvTest(unittest.TestCase):
    def test_play_pipe_falls_back_to_player_wide_options_and_undoes_them(self):
        from pvj.player import Player, PlayerError
        p = Player.__new__(Player)
        sent = []

        class Ipc:
            def request(self, *cmd):
                sent.append(cmd)
                if cmd[0] == "loadfile" and len(cmd) > 3:
                    raise PlayerError("invalid parameter")                   # mpv 0.35 has no index argument
                if cmd[:2] == ("get_property", "pid"):
                    return 1
                if cmd[:2] == ("get_property", "path"):
                    return "/m/a.mp4"
                return None
        p.ipc = Ipc()
        p.play_pipe("/run/pvj/capture.fifo", 1280, 720, 30)
        self.assertIn(("set_property", "demuxer", "rawvideo"), sent)
        self.assertIn(("loadfile", "/run/pvj/capture.fifo", "replace"), sent)
        sent.clear()
        p.play(["/m/a.mp4"], spawn=False)
        self.assertIn(("set_property", "demuxer", ""), sent)                   # a normal file is not read as raw video


class CaptureApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        events = []

        class Cap:
            fifo = os.path.join(self.rundir, "capture.fifo")
            current = None
            lock = threading.RLock()

            def prepare(inner, device, mode):
                if device != "video0" or mode not in capture.MODES:
                    raise capture.CaptureError("no such input")
                events.append(("prepare", device, mode))
                return capture.MODES[mode]

            def start(inner, device, mode):
                events.append(("start", device, mode))
                inner.current = {"device": device, "mode": mode}

            def stop(inner):
                events.append(("stop",))
                inner.current = None

            def status(inner, devices=True):
                return {"running": bool(inner.current), "current": inner.current, "devices": [{"id": "video0", "name": "stick"}], "modes": list(capture.MODES)}
        self.events = events
        self.api.capture = Cap()

    def post(self, path, body):
        return self.call("POST", path, body, token=self.full)

    def test_play_input_then_a_file_stops_the_helper(self):
        st, body, _ = self.post("/api/play", {"capture": {"device": "video0", "mode": "720p30"}})
        self.assertEqual((st, body["mode"]), (200, "720p30"))
        self.assertIn(("play_pipe", os.path.join(self.rundir, "capture.fifo"), 1280, 720, 30), self.player.calls)
        self.assertEqual([e[0] for e in self.events], ["prepare", "start"])       # not stopped by its own start
        self.post("/api/play", {"file": "a.mp4"})
        self.assertEqual(self.events[-1], ("stop",))

    def test_the_capture_input_is_played_at_speed_one_and_the_mix_speed_comes_back_with_the_next_clip(self):
        """A live source cannot be played faster than it comes (found with NDI on the Pi 4, 2026-10-08; the capture
        input had the same fault unnoticed). The real play, not a look at the source text."""
        self.post("/api/control", {"action": "speed", "value": 1.86})
        self.player.calls.clear()
        self.assertEqual(self.post("/api/play", {"capture": {"device": "video0", "mode": "720p30"}})[0], 200)
        self.assertEqual([c[1] for c in self.player.calls if c[0] == "speed"], [1])
        self.assertIs(self.api._speed_held, True)
        self.post("/api/play", {"file": "a.mp4"})
        self.assertEqual([c[1] for c in self.player.calls if c[0] == "speed"], [1, 1.86])
        self.assertIs(self.api._speed_held, False)

    def test_restart_player_and_test_pattern_off_also_stop_the_helper(self):
        self.post("/api/play", {"capture": {"device": "video0", "mode": "720p30"}})
        self.post("/api/player/restart", {})
        self.assertEqual(self.events[-1], ("stop",))
        self.post("/api/play", {"capture": {"device": "video0", "mode": "720p30"}})
        self.post("/api/testpattern", {"on": False})
        self.assertEqual(self.events[-1], ("stop",))

    def test_stop_also_stops_the_helper_and_bad_input_is_400(self):
        self.post("/api/play", {"capture": {"device": "video0", "mode": "720p30"}})
        self.post("/api/control", {"action": "stop"})
        self.assertEqual(self.events[-1], ("stop",))
        self.assertEqual(self.post("/api/play", {"capture": {"device": "video9"}})[0], 400)
        self.assertEqual(self.post("/api/play", {"capture": "video0"})[0], 400)

    def test_inputs_list_and_roles(self):
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        st, body, _ = self.call("GET", "/api/inputs", token=view)
        self.assertEqual((st, body["devices"][0]["id"], body["modes"]), (200, "video0", ["720p30", "1080p30"]))
        self.assertEqual(self.call("POST", "/api/play", {"capture": {"device": "video0", "mode": "720p30"}}, token=view)[0], 403)


if __name__ == "__main__":
    unittest.main()
