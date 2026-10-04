# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A runtime folder per service (D45): what the code does with a folder that is its own and one that is a peer's.

The ownership itself (systemd handing a folder to the unit that starts) cannot be shown here; the units are checked
in tests/test_units.py and the real box with the checklist in tools/DEVICE-TESTING.md."""
import errno
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from pvj import paths, server
from pvj.netd import NetdClient, NetError
from pvj.player import Player, PlayerError
from pvj.update import Updater

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def box(base):
    """The environment pvj-web has on a box, under a temporary folder. Only the panel's folder exists."""
    env = {"PVJ_RUNTIME_DIR": os.path.join(base, "web"), "PVJ_PLAYER_DIR": os.path.join(base, "player"),
           "PVJ_NETD_DIR": os.path.join(base, "netd")}
    os.mkdir(env["PVJ_RUNTIME_DIR"], 0o750)
    return env


class PanelSideTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.env = box(self.base)
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_panel_writes_in_its_own_folder_and_looks_for_the_socket_in_the_players(self):
        p = Player()
        self.assertEqual(p.rundir, self.env["PVJ_RUNTIME_DIR"])
        self.assertEqual(p.socket_path, os.path.join(self.base, "player", "player.sock"))
        self.assertEqual(p.preview_path, os.path.join(self.base, "player", "preview.jpg"))
        self.assertEqual(p.ipc.path, p.socket_path)

    def test_a_player_that_never_started_is_not_running_and_its_folder_is_not_made(self):
        # On a box the parent belongs to root: trying to create the player's folder would be refused, and a panel
        # that insisted would never start. It must come up in any order.
        os.chmod(self.base, 0o555)
        self.addCleanup(os.chmod, self.base, 0o755)
        p = Player()
        self.assertFalse(p.is_running())
        with self.assertRaises(PlayerError) as e:
            p.ipc.request("get_property", "pid")
        self.assertIn("not running", str(e.exception))
        self.assertEqual(os.listdir(self.base), ["web"])

    def test_a_missing_network_helper_is_not_running(self):
        with self.assertRaises(NetError) as e:
            NetdClient(paths.netd_socket()).request({"cmd": "status"})
        self.assertIn("not running", str(e.exception))
        self.assertEqual(os.listdir(self.base), ["web"])

    def test_files_handed_to_the_player_are_read_only_for_its_group(self):
        p = Player()
        p.ipc = mock.Mock()
        p.overlay(3, 0, 0, 1, 1, b"\0\0\0\0")
        path = os.path.join(self.env["PVJ_RUNTIME_DIR"], "overlay-3.bgra")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(p.ipc.request.call_args[0][4], path)

    def test_the_pin_is_in_the_panels_folder_and_for_its_owner_only(self):
        old = os.umask(0)                      # the mode must not depend on the unit's UMask
        self.addCleanup(os.umask, old)
        server.write_pin_file(self.env["PVJ_RUNTIME_DIR"], "1234")
        path = os.path.join(self.base, "web", "pin")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertEqual(f.read(), "1234\n")
        server.write_pin_file(self.env["PVJ_RUNTIME_DIR"], "5678")       # rotated: same file, same mode
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_the_pin_is_not_written_through_a_link(self):
        target = os.path.join(self.base, "elsewhere")
        os.symlink(target, os.path.join(self.base, "web", "pin"))
        with self.assertRaises(OSError):
            server.write_pin_file(self.env["PVJ_RUNTIME_DIR"], "1234")
        self.assertFalse(os.path.exists(target))

    def test_pvj_pin_reads_the_same_file(self):
        server.write_pin_file(self.env["PVJ_RUNTIME_DIR"], "4321")
        r = subprocess.run(["sh", os.path.join(REPO, "bin", "pvj-pin")], capture_output=True, text=True, timeout=20,
                           env={"PATH": os.environ["PATH"], "PVJ_RUNTIME_DIR": self.env["PVJ_RUNTIME_DIR"]})
        self.assertEqual((r.returncode, r.stdout), (0, "4321\n"))

    def run_pin(self):
        return subprocess.run(["sh", os.path.join(REPO, "bin", "pvj-pin")], capture_output=True, text=True, timeout=20,
                              env={"PATH": os.environ["PATH"], "PVJ_RUNTIME_DIR": self.env["PVJ_RUNTIME_DIR"]})

    def test_pvj_pin_shows_no_other_file_through_a_link_and_does_not_hang_on_a_pipe(self):
        # it runs as root (sudo) on a name the panel's account controls
        secret = os.path.join(self.base, "shadow")
        with open(secret, "w") as f:
            f.write("root:secret\n")
        pin = os.path.join(self.base, "web", "pin")
        os.symlink(secret, pin)
        r = self.run_pin()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        os.unlink(pin)
        os.mkfifo(pin)
        r = self.run_pin()                                   # the 20 s limit would end a hang
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        os.unlink(pin)
        with open(pin, "w") as f:
            f.write("x" * 5000)
        self.assertEqual(len(self.run_pin().stdout), 32)


class PreviewInThePlayersFolderTest(unittest.TestCase):
    """mpv writes the preview, so it is in the player's folder, which the panel can read and not change."""

    def setUp(self):
        from pvj.api import Api
        self.base = tempfile.mkdtemp()
        self.env = box(self.base)
        os.mkdir(self.env["PVJ_PLAYER_DIR"], 0o750)
        self.shot = os.path.join(self.env["PVJ_PLAYER_DIR"], "preview.jpg")
        self.api = Api.__new__(Api)
        self.api._preview_lock, self.api._preview = threading.RLock(), None
        self.api.access_on_screen = lambda: False
        self.api._player_call = lambda fn, *a: fn(*a)
        self.api.player = mock.Mock(rundir=self.env["PVJ_RUNTIME_DIR"], preview_path=self.shot)
        self.api.player.screenshot.side_effect = self.screenshot
        self.addCleanup(os.chmod, self.env["PVJ_PLAYER_DIR"], 0o750)

    def screenshot(self, path, quality, with_text):
        with open(path, "wb") as f:                          # mpv, in its own folder
            f.write(JPEG)

    def old(self, data=JPEG):
        with open(self.shot, "wb") as f:
            f.write(data)
        os.utime(self.shot, ns=(1, 1))                       # written some time ago

    def test_the_panel_never_tries_to_remove_a_picture_in_the_players_folder(self):
        # On a box the panel's sandbox (ProtectSystem=strict) mounts the player's folder read-only, and unlink
        # then fails with EROFS, not EACCES: the first version caught the wrong one and every preview was a 503
        # (found by the independent review). The panel must not call unlink there at all.
        self.old(b"old")
        calls = []

        def unlink(path, *a, **kw):
            calls.append(path)
            raise OSError(errno.EROFS, "Read-only file system")
        with mock.patch("os.unlink", unlink):
            self.assertEqual(self.api.preview_jpeg(None), JPEG)
        self.assertEqual(calls, [])
        self.assertEqual(self.api.player.screenshot.call_args[0][0], self.shot)
        self.assertEqual(os.listdir(self.env["PVJ_RUNTIME_DIR"]), [])

    def test_a_picture_from_an_earlier_request_is_never_served_as_new(self):
        from pvj.api import ApiError
        self.old()                                           # a good JPEG, but from before
        self.api.player.screenshot.side_effect = lambda *a: None       # mpv said yes and wrote nothing
        with self.assertRaises(ApiError) as e:
            self.api.preview_jpeg(None)
        self.assertEqual(e.exception.status, 503)
        self.assertIn("did not produce a picture", e.exception.message)
        # and the next real one is served, whether mpv writes over the file or puts a new one in its place
        self.api._preview = None
        self.api.player.screenshot.side_effect = self.screenshot
        self.assertEqual(self.api.preview_jpeg(None), JPEG)
        self.api._preview = None

        def replace(path, quality, with_text):
            os.chmod(self.env["PVJ_PLAYER_DIR"], 0o750)
            with open(path + ".tmp", "wb") as f:
                f.write(JPEG + b"2")
            os.replace(path + ".tmp", path)
        self.api.player.screenshot.side_effect = replace
        self.assertEqual(self.api.preview_jpeg(None), JPEG + b"2")

    def test_a_first_picture_with_no_file_there_before_is_served(self):
        self.assertFalse(os.path.exists(self.shot))
        self.assertEqual(self.api.preview_jpeg(None), JPEG)

    def test_a_link_in_place_of_the_picture_is_not_followed(self):
        from pvj.api import ApiError
        secret = os.path.join(self.base, "secret")
        with open(secret, "wb") as f:
            f.write(JPEG)
        os.symlink(secret, self.shot)
        self.api.player.screenshot.side_effect = lambda *a: None
        with self.assertRaises(ApiError):
            self.api.preview_jpeg(None)

    def test_a_player_without_the_attribute_keeps_the_picture_in_its_one_folder(self):
        self.api.player = mock.Mock(spec=["rundir", "screenshot"], rundir=self.env["PVJ_PLAYER_DIR"])
        self.api.player.screenshot.side_effect = self.screenshot
        self.old(b"old")
        self.assertEqual(self.api.preview_jpeg(None), JPEG)       # its own folder: removed first, as before


class NetdFolderTest(unittest.TestCase):
    @unittest.skipUnless(hasattr(socket, "SO_PEERCRED"), "needs SO_PEERCRED (Linux)")
    def test_the_helper_listens_in_the_folder_it_is_given_and_the_panel_finds_it(self):
        from pvj import netd
        base = tempfile.mkdtemp()
        env = box(base)
        made = []

        class Stop(Exception):
            pass

        def serve(self_):
            made.append(self_.server_address)
            raise Stop()
        with mock.patch.dict(os.environ, {"PVJ_RUNTIME_DIR": env["PVJ_NETD_DIR"], "STATE_DIRECTORY": os.path.join(base, "state")}), \
                mock.patch.object(netd.NetServer, "serve_forever", serve), \
                mock.patch.object(netd.NetService, "recover", lambda self_: False), \
                mock.patch.object(netd.os, "chown", lambda *a, **k: None):
            with self.assertRaises(Stop):
                netd.main([])
        self.assertEqual(made, [paths.netd_socket(env)])
        self.assertEqual(stat.S_IMODE(os.stat(env["PVJ_NETD_DIR"]).st_mode) & 0o027, 0)       # never group-writable
        self.assertEqual(stat.S_IMODE(os.stat(made[0]).st_mode), 0o660)


class UpdateRestartTest(unittest.TestCase):
    def test_an_update_moves_a_running_network_helper_too_and_leaves_the_other_helpers_alone(self):
        calls = []
        up = Updater(root="/", run=lambda argv, **kw: calls.append(argv))
        with mock.patch("os.path.isdir", lambda p: True):
            up._systemd_restart()
        self.assertEqual(calls, [["systemctl", "try-restart", "pvj-netd.service"],
                                 ["systemctl", "restart", "pvj-player.service", "pvj-web.service"]])
        flat = " ".join(sum(calls, []))
        self.assertNotIn("pvj-sysd", flat)           # it started this update
        self.assertNotIn("pvj-supportd", flat)       # a support session may be the one updating


class OlderCodeUnderTheNewUnitsTest(unittest.TestCase):
    """After a rollback an older release runs under these units. It knows one name, PVJ_RUNTIME_DIR, and uses that
    one folder for everything. Each unit gives it the service's own folder, and the panel's folder holds links to
    both sockets (install/pvj-tmpfiles.conf), so the older panel still reaches the player and the helper."""

    def test_through_the_links_an_older_panel_reaches_a_socket_in_the_peers_folder(self):
        base = tempfile.mkdtemp(dir="/tmp")          # a short path: a unix socket name is limited to about 100 bytes
        env = box(base)
        os.mkdir(env["PVJ_PLAYER_DIR"], 0o750)
        real = os.path.join(env["PVJ_PLAYER_DIR"], "player.sock")
        os.symlink(real, os.path.join(env["PVJ_RUNTIME_DIR"], "player.sock"))       # what the L+ line makes
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(real)
        listener.listen(1)
        self.addCleanup(listener.close)
        old_style = os.path.join(env["PVJ_RUNTIME_DIR"], "player.sock")            # an older panel: rundir + name
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(c.close)
        c.connect(old_style)
        # and the new panel is not confused by the links in its own folder
        with mock.patch.dict(os.environ, env):
            self.assertEqual(Player().socket_path, real)


if __name__ == "__main__":
    sys.exit(unittest.main())
