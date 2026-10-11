# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Runs the real mpv headless (null video and audio). Skipped if mpv is missing."""
import os
import shutil
import stat
import tempfile
import time
import unittest

from pvj import player
from pvj.player import Player, PlayerError, expand_media
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

HEADLESS = ["--vo=null", "--ao=null"]
SRC = "av://lavfi:testsrc=size=160x120:rate=25"
SRC2 = "av://lavfi:smptebars=size=160x120:rate=25"


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.player = Player(extra_args=HEADLESS, rundir=self.dir)
        self.addCleanup(self.player.stop)

    def test_play_status_and_stop(self):
        self.assertFalse(self.player.status()["running"])
        self.player.play([SRC])
        st = self.player.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["path"], SRC)
        self.player.stop()
        self.assertFalse(self.player.status()["running"])
        self.assertFalse(os.path.exists(self.player.socket_path))

    def test_stopping_a_looping_clip_leaves_no_loop_behind(self):
        self.player.play([SRC], loop=True)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), "inf")
        self.player.clear()
        st = self.player.status()
        self.assertTrue(st["running"])
        self.assertIn(st["loop_file"], ("no", False))
        self.assertIn(st["loop_playlist"], ("no", False))

    def test_clip_change_keeps_same_process(self):
        self.player.play([SRC])
        pid1 = self.player.ipc.request("get_property", "pid")
        self.player.play([SRC2])
        pid2 = self.player.ipc.request("get_property", "pid")
        self.assertEqual(pid1, pid2)
        self.assertEqual(self.player.status()["path"], SRC2)

    def test_pause_speed_volume(self):
        self.player.play([SRC])
        self.assertTrue(self.player.pause())
        self.assertTrue(self.player.status()["paused"])
        self.assertFalse(self.player.pause(False))
        self.player.speed(2)
        self.assertEqual(self.player.status()["speed"], 2.0)
        self.player.speed(99)
        self.assertEqual(self.player.status()["speed"], 4.0)
        self.player.volume(50)
        self.assertEqual(self.player.status()["volume"], 50.0)

    def test_picture_controls(self):
        self.player.play([SRC])
        self.player.opacity(255)
        self.assertEqual(self.player.ipc.request("get_property", "brightness"), 0)
        self.player.opacity(0)
        self.assertEqual(self.player.ipc.request("get_property", "brightness"), -100)
        self.player.size(200)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-zoom"), 1.0)
        self.player.position(500, -250)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-pan-x"), 0.5)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-pan-y"), -0.25)

    def test_mute_rotate_loop(self):
        self.player.play([SRC])
        self.player.mute(True)
        self.assertTrue(self.player.status()["muted"])
        self.player.rotate(90)
        self.assertEqual(self.player.ipc.request("get_property", "video-rotate"), 90)
        with self.assertRaises(PlayerError):
            self.player.rotate(45)
        self.player.loop(False)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), False)
        self.player.loop(True)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), "inf")

    def test_loop_flags(self):
        self.player.play([SRC], loop=True)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), "inf")
        self.player.play([SRC], loop=False)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), False)

    def test_playlist_from_folder_and_option_like_names(self):
        media = tempfile.mkdtemp()
        for name in ("b.mp4", "a.mp4", "-evil.mp4", "notes.txt", ".hidden.mp4"):
            open(os.path.join(media, name), "w").close()
        files = [os.path.basename(f) for f in expand_media([media])]
        self.assertEqual(files, ["-evil.mp4", "a.mp4", "b.mp4"])

    def test_errors_when_not_running(self):
        with self.assertRaises(PlayerError):
            self.player.seek(5)
        with self.assertRaises(PlayerError):
            self.player.play([tempfile.mkdtemp()])  # empty folder

    def test_missing_mpv_binary(self):
        p = Player(mpv_bin="/nonexistent/mpv", rundir=self.dir)
        with self.assertRaises(PlayerError):
            p.play([SRC])

    def test_unsafe_runtime_dir_rejected(self):
        from pvj import player
        d = tempfile.mkdtemp()
        os.chmod(d, 0o777)
        os.environ["PVJ_RUNTIME_DIR"] = d
        self.addCleanup(os.environ.pop, "PVJ_RUNTIME_DIR")
        with self.assertRaises(PlayerError):
            player.runtime_dir()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        import sys
        env = dict(os.environ, PVJ_RUNTIME_DIR=self.dir)
        return subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-player"),
                               "--mpv-arg=--vo=null", "--mpv-arg=--ao=null", *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_cli_round_trip(self):
        import json
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.addCleanup(self.run_cli, "stop")
        self.assertEqual(json.loads(self.run_cli("status").stdout), {"running": False})
        r = self.run_cli("play", SRC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_cli("speed", "1.5")
        self.run_cli("pause", "on")
        st = json.loads(self.run_cli("status").stdout)
        self.assertEqual((st["speed"], st["paused"]), (1.5, True))
        self.assertEqual(self.run_cli("stop").returncode, 0)
        self.assertEqual(self.run_cli("seek", "5").returncode, 1)

    def test_info_and_selftest(self):
        import json
        import subprocess
        import sys
        self.dir = tempfile.mkdtemp()
        info = json.loads(self.run_cli("info").stdout)
        self.assertIn("board", info)
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-selftest")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(all(c["ok"] for c in json.loads(r.stdout)["checks"]))


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o770)
        self.env = dict(os.environ, PVJ_RUNTIME_DIR=self.dir)
        self.cli = [__import__("sys").executable,
                    os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-player"),
                    "--mpv-arg=--vo=null", "--mpv-arg=--ao=null"]

    def run_cli(self, *args):
        import subprocess
        return subprocess.run(self.cli + list(args), capture_output=True, text=True, env=self.env, timeout=30)

    def test_no_spawn_requires_service(self):
        r = self.run_cli("play", "--no-spawn", SRC)
        self.assertEqual(r.returncode, 1)
        self.assertIn("service is not running", r.stderr)

    def test_serve_then_control_without_spawning(self):
        import json
        import subprocess
        svc = subprocess.Popen(self.cli + ["serve"], env=self.env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        self.addCleanup(svc.wait)
        self.addCleanup(svc.kill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not json.loads(self.run_cli("status").stdout)["running"]:
            if svc.poll() is not None:
                self.fail("service exited")
            time.sleep(0.2)
        r = self.run_cli("play", "--no-spawn", SRC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.run_cli("status").stdout)["path"], SRC)
        # the service is the process we started, not a child spawned by the CLI
        self.assertIsNone(svc.poll())

    def test_group_accessible_dir_ok_world_accessible_rejected(self):
        from pvj import player
        os.environ["PVJ_RUNTIME_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "PVJ_RUNTIME_DIR")
        self.assertEqual(player.runtime_dir(), self.dir)
        os.chmod(self.dir, 0o775)
        with self.assertRaises(PlayerError):
            player.runtime_dir()


class SocketOpenerTest(unittest.TestCase):
    """mpv makes its control socket owner-only; `serve` must open it to the group (found on a real Pi 4)."""

    def test_waits_for_a_new_socket_and_ignores_a_stale_one_that_was_removed(self):
        import socket as sk
        import threading
        d = tempfile.mkdtemp()
        path = os.path.join(d, "player.sock")
        result = []

        def make_later():
            time.sleep(0.3)
            s = sk.socket(sk.AF_UNIX)
            s.bind(path)
            os.chmod(path, 0o600)
            self.addCleanup(s.close)
        t = threading.Thread(target=make_later)
        t.start()
        result.append(player.open_socket_when_ready(path, 0o660, timeout=5))
        t.join()
        self.assertEqual(result, [True])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o660)

    def test_gives_up_when_no_socket_appears(self):
        self.assertFalse(player.open_socket_when_ready(os.path.join(tempfile.mkdtemp(), "none.sock"), timeout=0.2))

    def test_serve_opens_the_socket_of_a_stand_in_mpv(self):
        """Run the real serve() (which execs mpv) with a stand-in mpv that binds a 0600 socket like the real one."""
        import subprocess
        import sys
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "fake-mpv")
        with open(fake, "w") as f:
            f.write("#!%s\nimport os, socket, sys, time\n"
                    "path = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--input-ipc-server=')][0]\n"
                    "time.sleep(0.4)\ns = socket.socket(socket.AF_UNIX); s.bind(path); os.chmod(path, 0o600)\ntime.sleep(3)\n" % sys.executable)
        os.chmod(fake, 0o755)
        rundir = os.path.join(d, "run")
        os.makedirs(rundir, mode=0o700)
        sock = os.path.join(rundir, "player.sock")
        open(sock, "w").close()                       # a stale file from the previous run must not fool it
        code = ("import sys; sys.path.insert(0, %r); from pvj.player import Player; "
                "Player(mpv_bin=%r, rundir=%r).serve()") % (os.path.join(os.path.dirname(__file__), ".."), fake, rundir)
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            deadline = time.time() + 6
            mode = None
            while time.time() < deadline:
                try:
                    if stat.S_ISSOCK(os.stat(sock).st_mode):
                        mode = stat.S_IMODE(os.stat(sock).st_mode)
                        if mode == 0o660:
                            break
                except FileNotFoundError:
                    pass
                time.sleep(0.05)
            self.assertEqual(mode, 0o660)
        finally:
            proc.kill()
            proc.wait()


DEAF_MPV = """#!%s
# A stand-in for mpv as it was seen in CI at its first start on a machine: it answers the first question, then
# nothing for a while (the real one is making its window), then everything it was asked.
import json, os, socket, sys, threading, time
path = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--input-ipc-server=')][0]
s = socket.socket(socket.AF_UNIX)
s.bind(path)
s.listen(8)
state = {'hears_again': None}
lock = threading.Lock()


def serve(c):
    msg = json.loads(c.makefile().readline())
    with lock:
        if state['hears_again'] is None:
            state['hears_again'] = time.monotonic() + %r         # deaf from the first answer on
            wait = 0
        else:
            wait = max(0, state['hears_again'] - time.monotonic())
    time.sleep(wait)
    try:
        c.sendall(json.dumps({'request_id': msg['request_id'], 'error': 'success', 'data': os.getpid()}).encode() + b'\\n')
    except OSError:
        pass
    c.close()
    if msg['command'][0] == 'quit':
        os._exit(0)


while True:
    threading.Thread(target=serve, args=(s.accept()[0],), daemon=True).start()
"""


class DeafAtTheStartTest(unittest.TestCase):
    """"no reply from mpv" in CI's GPU jobs: a player that this process has just started answers nothing while it
    makes its window (up to 3.3 s on a fresh machine, where a request waits 2). A stand-in player that is deaf in
    the same way, with the times made short: no mpv and no GPU needed."""

    def start(self, deaf):
        import sys
        d = tempfile.mkdtemp(prefix="pvjd", dir="/tmp")              # short: a socket's path has little room
        self.addCleanup(shutil.rmtree, d, True)
        os.chmod(d, 0o700)
        fake = os.path.join(d, "fake-mpv")
        with open(fake, "w") as f:
            f.write(DEAF_MPV % (sys.executable, deaf))
        os.chmod(fake, 0o755)
        p = Player(mpv_bin=fake, rundir=d)
        self.addCleanup(p.stop)
        p.ipc.timeout = 0.3
        p._spawn()                                                  # the first question is answered: it "runs"
        return p

    def test_a_request_right_after_the_start_waits_for_a_player_that_is_still_coming_up(self):
        p = self.start(1.0)
        t = time.monotonic()
        p.ipc.request("set_property", "keep-open", "no")            # was: PlayerError("no reply from mpv") after 0.3 s
        self.assertGreater(time.monotonic() - t, 0.5)

    def test_after_the_start_a_silent_player_is_reported_as_soon_as_before(self):
        p = self.start(1.2)
        self.assertGreater(p.ipc.patient_until, time.monotonic() + player.START_GRACE - 5)
        p.ipc.patient_until = time.monotonic()                      # as if the start were long ago
        t = time.monotonic()
        with self.assertRaises(PlayerError) as e:
            p.ipc.request("set_property", "keep-open", "no")
        self.assertEqual(str(e.exception), "no reply from mpv")
        self.assertLess(time.monotonic() - t, 0.6)

    def test_a_client_that_started_nothing_waits_as_it_always_did(self):
        self.assertEqual(player.Ipc("/nonexistent").patient_until, 0.0)
        p = Player(rundir=tempfile.mkdtemp())
        self.assertEqual((p.ipc.timeout, p.ipc.patient_until), (2.0, 0.0))


class HardeningFlagsTest(unittest.TestCase):
    """A file is judged by its content: a hostile USB drive can hold an "mp4" that is really a playlist or an EDL."""

    def test_the_player_will_not_follow_references_or_load_sidecars_or_scripts(self):
        args = Player(rundir=tempfile.mkdtemp()).mpv_command()
        for flag in ("--access-references=no", "--load-unsafe-playlists=no", "--sub-auto=no", "--audio-file-auto=no",
                     "--ytdl=no", "--load-scripts=no", "--load-auto-profiles=no"):
            self.assertIn(flag, args)

