# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The one module that knows where runtime files live (pvj/paths.py)."""
import os
import tempfile
import unittest

from pvj import paths
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


class LayoutTest(unittest.TestCase):
    def test_each_service_has_its_own_folder_under_a_parent_that_is_not_a_service_folder(self):
        folders = [paths.PLAYER_DIR, paths.WEB_DIR, paths.NETD_DIR]
        self.assertEqual(len(set(folders)), 3)
        for d in folders:
            self.assertEqual(os.path.dirname(d), paths.RUN)
        for d in (paths.SYSD_DIR, paths.SUPPORTD_DIR, paths.UPDATE_DIR):
            self.assertEqual(os.path.dirname(d), "/run")
            self.assertNotEqual(d, paths.RUN)

    def test_on_a_box_every_path_comes_from_the_units_environment(self):
        env = {"PVJ_RUNTIME_DIR": paths.WEB_DIR, "PVJ_PLAYER_DIR": paths.PLAYER_DIR, "PVJ_NETD_DIR": paths.NETD_DIR}
        self.assertEqual(paths.own_path(env), "/run/pvj/web")
        self.assertEqual(paths.player_socket(env), "/run/pvj/player/player.sock")
        self.assertEqual(paths.preview_file(env), "/run/pvj/player/preview.jpg")     # mpv writes it
        self.assertEqual(paths.netd_socket(env), "/run/pvj/netd/netd.sock")
        self.assertEqual(paths.sysd_socket(env), "/run/pvj-sysd/sysd.sock")
        self.assertEqual(paths.supportd_socket(env), "/run/pvj-supportd/supportd.sock")
        self.assertEqual(paths.update_result(env), "/run/pvj-update/result.json")
        self.assertEqual(paths.pin_file(paths.own_path(env)), "/run/pvj/web/pin")
        self.assertEqual(paths.capture_fifo(paths.own_path(env)), "/run/pvj/web/capture.fifo")

    def test_the_player_and_the_helper_see_their_own_folder(self):
        self.assertEqual(paths.player_socket({"PVJ_RUNTIME_DIR": paths.PLAYER_DIR}), "/run/pvj/player/player.sock")
        self.assertEqual(paths.netd_socket({"PVJ_RUNTIME_DIR": paths.NETD_DIR}), "/run/pvj/netd/netd.sock")

    def test_without_peer_folders_everything_is_in_the_one_private_folder(self):
        env = {"PVJ_RUNTIME_DIR": "/somewhere/private"}
        self.assertEqual(paths.player_socket(env), "/somewhere/private/player.sock")
        self.assertEqual(paths.netd_socket(env), "/somewhere/private/netd.sock")
        self.assertEqual(paths.own_path({"XDG_RUNTIME_DIR": "/run/user/1000"}), "/run/user/1000/pvj")
        self.assertEqual(paths.own_path({}), "/tmp/pvj-%d" % os.getuid())

    def test_the_other_helpers_and_the_update_result_can_be_pointed_elsewhere(self):
        env = {"PVJ_SYSD_DIR": "/a", "PVJ_SUPPORTD_DIR": "/b", "PVJ_UPDATE_RESULT": "/c/r.json"}
        self.assertEqual((paths.sysd_socket(env), paths.supportd_socket(env), paths.update_result(env)),
                         ("/a/sysd.sock", "/b/supportd.sock", "/c/r.json"))


class OwnDirTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def test_a_missing_folder_is_created_private(self):
        d = os.path.join(self.base, "new")
        self.assertEqual(paths.own_dir({"PVJ_RUNTIME_DIR": d}), d)
        self.assertEqual(os.stat(d).st_mode & 0o777, 0o700)

    def test_a_folder_others_can_enter_is_refused(self):
        for mode in (0o755, 0o777, 0o771):
            os.chmod(self.base, mode)
            with self.assertRaises(paths.UnsafeDirectory):
                paths.own_dir({"PVJ_RUNTIME_DIR": self.base})
        os.chmod(self.base, 0o750)
        self.assertEqual(paths.own_dir({"PVJ_RUNTIME_DIR": self.base}), self.base)

    def test_a_peers_folder_is_never_created(self):
        missing = os.path.join(self.base, "player")
        env = {"PVJ_RUNTIME_DIR": os.path.join(self.base, "web"), "PVJ_PLAYER_DIR": missing,
               "PVJ_NETD_DIR": os.path.join(self.base, "netd")}
        paths.own_dir(env)
        paths.player_socket(env), paths.preview_file(env), paths.netd_socket(env)
        self.assertEqual(os.listdir(self.base), ["web"])


if __name__ == "__main__":
    unittest.main()
