# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import unittest

from pvj import sync
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


def play(file="show.mp4", pos=10.0, speed=1.0, paused=False, loop=True, duration=120.0, black=False, seq=1):
    return {"seq": seq, "state": "play", "black": black, "file": file, "pos": pos, "speed": speed, "paused": paused,
            "loop": loop, "duration": duration}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class SimPlayer:
    """A player whose position runs on the shared clock at its speed; a jump lands `seek_cost` seconds late."""

    def __init__(self, clock, seek_cost=0.3):
        self.clock, self.seek_cost = clock, seek_cost
        self.loaded = None
        self.pos0, self.t0, self.speed, self.paused = 0.0, clock(), 1.0, False
        self.calls = []

    def _now_pos(self):
        if self.loaded is None:
            return None
        return self.pos0 + (0 if self.paused else (self.clock() - self.t0) * self.speed)

    def _rebase(self):
        self.pos0, self.t0 = self._now_pos() or 0.0, self.clock()

    def load(self, path, loop):
        self.calls.append(("load", path, loop))
        self.loaded, self.pos0, self.t0 = path, 0.0, self.clock()

    def playing(self):
        return self.loaded.rsplit("/", 1)[-1] if self.loaded else None

    def position(self):
        return self._now_pos()

    def set_speed(self, x):
        self._rebase()
        self.speed = x
        self.calls.append(("speed", x))

    def seek(self, seconds):
        self.calls.append(("seek", seconds))
        # the picture is back `seek_cost` seconds later, at the asked position: in effect it lands behind
        self.pos0, self.t0 = seconds - self.seek_cost * self.speed, self.clock()

    def pause(self, on):
        self._rebase()
        self.paused = on
        self.calls.append(("pause", on))

    def stop(self):
        self.calls.append(("stop",))
        self.loaded = None

    def blackout(self, on):
        self.calls.append(("blackout", on))


def follower(files=("show.mp4",), seek_cost=0.3):
    clock = Clock()
    player = SimPlayer(clock, seek_cost)
    f = sync.Follower(player, lambda name: "/media/" + name if name in files else None, clock=clock, log=lambda *_: None)
    return f, player, clock


class SettingsTest(unittest.TestCase):
    def test_good_and_bad_settings(self):
        c = sync.validate({"role": "client", "group": "stage-a", "port": 6000,
                           "wall": {"cols": 3, "rows": 2, "col": 2, "row": 1, "bezel": 4.5}}, sync.blank())
        self.assertEqual((c["role"], c["group"], c["port"], c["wall"]["col"], c["wall"]["bezel"]), ("client", "stage-a", 6000, 2, 4.5))
        for bad in ({"role": "master"}, {"group": ""}, {"group": "a b"}, {"group": "x" * 25}, {"port": 80}, {"port": True},
                    {"wall": {"cols": 9}}, {"wall": {"cols": 2, "col": 2}}, {"wall": {"bezel": 21}}, {"wall": []}, []):
            with self.assertRaises(sync.SyncError, msg=str(bad)):
                sync.validate(bad, sync.blank())


class WallTest(unittest.TestCase):
    def test_tiles_cover_the_picture_and_bezels_hide_a_slice(self):
        wall = {"cols": 2, "rows": 2, "col": 1, "row": 0, "bezel": 0.0}
        self.assertEqual(sync.wall_crop(wall, 1920, 1080), "960x540+960+0")
        self.assertEqual(sync.wall_crop(dict(wall, cols=1, rows=1, col=0), 1920, 1080), "")
        w3 = {"cols": 3, "rows": 1, "col": 2, "row": 0, "bezel": 10.0}
        crop = sync.wall_crop(w3, 1920, 1080)
        tw, rest = crop.split("x")
        th, x, y = rest.split("+")
        # three tiles and two bezels (each 10 percent of a tile) make up the width; the last tile ends at the edge
        self.assertAlmostEqual(int(tw) * 3 + 2 * int(tw) * 0.1, 1920, delta=3)
        self.assertAlmostEqual(int(x) + int(tw), 1920, delta=3)


class MessageTest(unittest.TestCase):
    def test_round_trip(self):
        data = sync.encode("main", 7, {"st": "play", "f": "show.mp4", "p": 12.5, "sp": 1.0, "pa": False, "lp": True, "d": 90.0, "bk": False})
        m = sync.decode(data, "main")
        self.assertEqual((m["seq"], m["file"], m["pos"], m["duration"], m["loop"]), (7, "show.mp4", 12.5, 90.0, True))
        self.assertIsNone(sync.decode(data, "other-group"))

    def test_bad_messages_are_dropped(self):
        good = {"v": 1, "g": "main", "s": 1, "st": "play", "f": "a.mp4", "p": 1.0}
        for change in ({"v": 2}, {"s": -1}, {"s": True}, {"st": "go"}, {"f": "../etc/passwd"}, {"f": "a/b.mp4"}, {"f": ""},
                       {"f": 5}, {"p": -1}, {"p": float("nan")}, {"p": 1e9}, {"sp": 10}, {"d": 0}, {"d": "x"}):
            self.assertIsNone(sync.decode(json.dumps(dict(good, **change)).encode(), "main"), change)
        for data in (b"", b"\xff\xfe", b"[]", b"x" * 2000, "not bytes"):
            self.assertIsNone(sync.decode(data, "main"))
        self.assertFalse(sync.private("8.8.8.8"))
        self.assertTrue(sync.private("192.168.0.20"))


class FollowerTest(unittest.TestCase):
    def run_for(self, f, clock, seconds, speed=1.0, start=10.0, **kw):
        """Feed server messages at 10 a second for `seconds`, the server's position running from `start`."""
        t0 = clock.t
        msg = None
        while clock.t - t0 < seconds:
            msg = play(pos=start + (clock.t - t0) * speed, speed=speed, **kw)
            f.apply(msg)
            clock.t += 0.1
        return msg

    def error(self, f, msg_pos):
        return f.player.position() - msg_pos

    def test_it_starts_jumps_once_and_then_stays_within_a_frame(self):
        f, p, clock = follower()
        msg = self.run_for(f, clock, 20.0)
        self.assertIn(("load", "/media/show.mp4", True), p.calls)
        jumps = [c for c in p.calls if c[0] == "seek"]
        self.assertLessEqual(len(jumps), 2)                      # the first jump and at most one corrected by the learned lead
        server_now = msg["pos"] + 0.1
        self.assertLess(abs(p.position() - server_now), 0.02)     # within half a frame at 25 fps
        self.assertEqual(f.status["state"], "in step")

    def test_small_drift_is_corrected_by_speed_not_by_jumping(self):
        f, p, clock = follower()
        self.run_for(f, clock, 15.0)
        p._rebase()
        p.pos0 += 0.08                                           # the client's clock ran fast: 80 ms ahead
        before = len([c for c in p.calls if c[0] == "seek"])
        msg = self.run_for(f, clock, 10.0, start=p.position() - 0.08)
        self.assertEqual(len([c for c in p.calls if c[0] == "seek"]), before)
        self.assertTrue(any(c[0] == "speed" and c[1] < 1.0 for c in p.calls))   # it slowed down for a moment
        self.assertLess(abs(p.position() - (msg["pos"] + 0.1)), 0.02)

    def test_a_server_seek_makes_it_jump(self):
        f, p, clock = follower()
        self.run_for(f, clock, 10.0)
        msg = self.run_for(f, clock, 6.0, start=70.0)              # the server jumped ahead
        self.assertLess(abs(p.position() - (msg["pos"] + 0.1)), 0.03)

    def test_looping_does_not_cause_a_jump(self):
        f, p, clock = follower()
        self.run_for(f, clock, 12.0, start=100.0)                  # 100 s to 112 s of a 120 s clip
        seeks = len([c for c in p.calls if c[0] == "seek"])
        p._rebase()
        p.pos0 -= 120.0                                            # the client looped a moment before the server
        f.apply(play(pos=119.95))
        self.assertEqual(len([c for c in p.calls if c[0] == "seek"]), seeks)

    def test_a_fast_or_slow_server_is_followed_without_endless_jumps(self):
        """Review finding: at server speed 2 the client settled at speed 1, fell behind and jumped 77 times in 2 min."""
        for speed in (2.0, 4.0, 0.25):
            f, p, clock = follower()
            msg = self.run_for(f, clock, 60.0, speed=speed)
            jumps = len([c for c in p.calls if c[0] == "seek"])
            self.assertLessEqual(jumps, 4, speed)
            self.assertLess(abs(p.position() - (msg["pos"] + 0.1 * speed)), 0.03 * max(1.0, speed), speed)   # clip seconds
            self.assertEqual(f.status["state"], "in step", speed)

    def test_a_clip_started_on_the_client_itself_is_replaced(self):
        f, p, clock = follower(files=("show.mp4", "other.mp4"))
        self.run_for(f, clock, 5.0)
        p.load("/media/other.mp4", True)                       # someone pressed a pad on the client
        f.apply(play(pos=15.0))
        self.assertEqual(p.loaded, "/media/show.mp4")

    def test_pause_stop_blackout_and_missing_files(self):
        f, p, clock = follower()
        self.run_for(f, clock, 5.0)
        f.apply(play(pos=15.0, paused=True))
        self.assertIn(("pause", True), p.calls)
        f.apply(play(pos=15.0, black=True))
        self.assertIn(("blackout", True), p.calls)
        f.apply({"seq": 9, "state": "stop", "black": False})
        self.assertIn(("stop",), p.calls)
        f.apply(play(file="other.mp4"))
        self.assertEqual(f.status, {"state": "missing file", "file": "other.mp4"})


class ManagerTest(unittest.TestCase):
    def manager(self, props, **api_attrs):
        class Ipc:
            def request(self, cmd, prop, *a):
                if cmd == "set_property":
                    props["set:" + prop] = a[0]
                    return None
                return props.get(prop)

        class Api:
            mix = {"blackout": False}
            registry = type("R", (), {"enabled": lambda self, m: True})()
            player = type("P", (), {"ipc": Ipc()})()
        api = Api()
        for k, v in api_attrs.items():
            setattr(api, k, v)

        class Settings:
            data = {"sync": sync.blank()}
        return sync.SyncManager(api, Settings(), log=lambda *_: None), Settings

    def test_only_media_and_usb_files_are_synced(self):
        props = {"path": "/srv/video/show.mp4", "time-pos": 1.0}
        m, _ = self.manager(props, media_dir="/srv/video", usb_root="/media/pvj")
        self.assertEqual(m.state()["f"], "show.mp4")
        for path in ("/run/pvj/capture.fifo", "srt://10.0.0.5:9000", "/etc/passwd", "av://lavfi:smptehdbars"):
            props["path"] = path
            self.assertEqual(m.state()["st"], "stop", path)          # clients stop instead of showing "missing file"

    def test_the_wall_crop_waits_for_the_picture_size(self):
        """Review finding: a clip whose size was not known yet got the whole picture for good."""
        props = {"path": "/srv/video/show.mp4"}
        m, S = self.manager(props)
        S.data["sync"]["wall"] = {"cols": 2, "rows": 1, "col": 1, "row": 0, "bezel": 0.0}
        m.apply_wall()
        self.assertNotIn("set:video-crop", props)
        props["video-params"] = {"w": 1920, "h": 1080}
        m.apply_wall()
        self.assertEqual(props["set:video-crop"], "960x1080+960+0")

    def test_a_restarted_server_is_followed_at_once(self):
        """Review finding: a client ignored a restarted server (seq back to 0) until the new seq passed the old."""
        old = sync.decode(sync.encode("main", 500, {"st": "stop", "r": "aaaa"}), "main")
        new = sync.decode(sync.encode("main", 0, {"st": "stop", "r": "bbbb"}), "main")
        self.assertNotEqual(old["run"], new["run"])
        self.assertIsNone(sync.decode(sync.encode("main", 1, {"st": "stop", "r": "NOT HEX"}), "main"))

    def test_an_old_thread_never_runs_with_new_settings(self):
        """Review finding: a thread still busy when the role changed picked up the next run's stop signal."""
        import threading
        import time as t
        m, S = self.manager({})
        S.data["sync"]["role"] = "server"
        m._targets = ["127.0.0.1"]
        slow = threading.Event()
        real_state = m.state

        def stuck_state():
            slow.wait(2)                         # a player that answers slowly
            return real_state()
        m.state = stuck_state
        m.apply()
        first = m._thread
        S.data["sync"]["role"] = "off"
        m.apply()
        slow.set()
        first.join(5)
        self.assertFalse(first.is_alive())

    def test_server_state_from_the_player(self):
        props = {"path": "/var/lib/pvj/video/show.mp4", "time-pos": 12.3456, "speed": 1.0, "pause": False,
                 "loop-file": "inf", "duration": 90.0}

        class Ipc:
            def request(self, cmd, prop, *a):
                return props.get(prop)

        class Api:
            mix = {"blackout": True}
            player = type("P", (), {"ipc": Ipc()})()
        Api.media_dir = "/var/lib/pvj/video"
        m = sync.SyncManager(Api(), None, log=lambda *_: None)
        st = m.state()
        self.assertEqual((st["st"], st["f"], st["p"], st["lp"], st["d"], st["bk"]), ("play", "show.mp4", 12.346, True, 90.0, True))
        props["path"] = "av://lavfi:smptehdbars"
        self.assertEqual(m.state()["st"], "stop")                 # the test pattern is not synced


import os
import shutil
import statistics as _stats
import subprocess
import tempfile
import time as _time


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class TwoRealPlayersTest(unittest.TestCase):
    """A real server and a real client, two headless mpv players, over UDP on this machine."""

    def setUp(self):
        from pvj.player import Player
        self.dir = tempfile.mkdtemp()
        self.clip = os.path.join(self.dir, "show.mkv")
        subprocess.run(["mpv", "av://lavfi:testsrc=size=160x120:rate=25", "--length=30", "--o=" + self.clip, "--no-terminal"],
                       capture_output=True, timeout=120)
        if not os.path.isfile(self.clip):
            self.skipTest("this mpv cannot make a test clip")
        self.players = []
        for name in ("server", "client"):
            run = os.path.join(self.dir, name)
            os.makedirs(run, mode=0o700)
            p = Player(extra_args=["--vo=null", "--ao=null"], rundir=run)
            self.addCleanup(p.stop)
            self.players.append(p)

    def manager(self, player, role, port):
        clip = self.clip

        class Settings:
            data = {"sync": dict(sync.blank(), role=role, port=port)}

        class Api:
            mix = {"blackout": False}
            registry = type("R", (), {"enabled": lambda self, m: True})()
            spawn = True
            media_dir = os.path.dirname(clip)        # only files from the media folder (or USB) are synced
            usb_root = None

            def __init__(self):
                self.player = player

            def _ip_json(self):
                return []

            def resolve_media(self, name):
                if name != os.path.basename(clip):
                    raise ValueError(name)
                return clip

            def usb_drives(self):
                return []

            def blackout(self, *a):
                pass
        m = sync.SyncManager(Api(), Settings(), log=lambda *_: None, targets=["127.0.0.1"])
        self.addCleanup(m.stop)
        return m

    def test_the_client_follows_the_server_within_a_few_frames(self):
        server, client = self.players
        server.play([self.clip], loop=True)
        port = 20000 + os.getpid() % 20000
        self.manager(client, "client", port).apply()
        self.manager(server, "server", port).apply()
        _time.sleep(12)                                       # start, one jump, settle
        diffs = []
        for _ in range(20):
            a = server.ipc.request("get_property", "time-pos")
            b = client.ipc.request("get_property", "time-pos")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                d = b - a
                if abs(d) > 15:                               # one of them looped in between the two readings
                    d -= 30 if d > 0 else -30
                diffs.append(d)
            _time.sleep(0.25)
        self.assertGreater(len(diffs), 10)
        med = _stats.median(abs(d) for d in diffs)
        print("\nsync, two headless players: median difference %.1f ms, worst %.1f ms" % (med * 1000, max(abs(d) for d in diffs) * 1000))
        self.assertLess(med, 0.1)


if __name__ == "__main__":
    unittest.main()
