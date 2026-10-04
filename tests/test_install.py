# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Runs install/install.sh in --stage mode (no users, apt or systemctl)."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def copy_source(version):
    src = tempfile.mkdtemp()
    for d in ("pvj", "bin", "install"):
        shutil.copytree(os.path.join(REPO, d), os.path.join(src, d), ignore=shutil.ignore_patterns("__pycache__"))
    init = os.path.join(src, "pvj", "__init__.py")
    with open(init, "w") as f:
        f.write('__version__ = "%s"\n' % version)
    return src


def install(src, stage, *extra):
    return subprocess.run([os.path.join(src, "install", "install.sh"), "--stage", stage, "--user", "gigbox", *extra],
                          capture_output=True, text=True, timeout=60)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.stage = tempfile.mkdtemp()
        self.src = copy_source("9.9.1")

    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()

    def p(self, *parts):
        return os.path.join(self.stage, *parts)

    def test_fresh_install_layout(self):
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.1")
        self.assertTrue(os.path.isfile(self.p("opt/pvj/releases/9.9.1/pvj/player.py")))
        self.assertFalse(os.path.exists(self.p("opt/pvj/releases/9.9.1.new")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-player")), "/opt/pvj/current/bin/pvj-player")
        unit = self.read(self.p("etc/systemd/system/pvj-player.service"))
        self.assertIn("User=gigbox", unit)
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-player serve", unit)
        self.assertNotIn("@PVJ", unit)
        web = self.read(self.p("etc/systemd/system/pvj-web.service"))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-web", web)
        self.assertIn("User=pvj-web", web)
        # the MIDI module needs to read /dev/snd/midi*: audio group, a device policy instead of PrivateDevices
        self.assertIn("SupplementaryGroups=audio", web)
        self.assertIn("DeviceAllow=char-alsa r", web)
        self.assertIn("DevicePolicy=closed", web)
        self.assertNotIn("DeviceAllow=char-alsa rw", web)
        self.assertNotRegex(web, r"(?m)^PrivateDevices=yes")
        self.assertIn("NoNewPrivileges=yes", web)
        self.assertNotIn("@PVJ", web)
        net = self.read(self.p("etc/systemd/system/pvj-netd.service"))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-netd", net)
        self.assertIn("ConditionPathExists=/usr/bin/nmcli", net)
        self.assertNotIn("@PVJ", net)
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-pin")), "/opt/pvj/current/bin/pvj-pin")
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-update")), "/opt/pvj/current/bin/pvj-update")
        self.assertIn("pvj-release", self.read(self.p("etc/pvj/allowed_signers")))
        self.assertIn("PVJ_MEDIA_DIR=/var/lib/pvj/video", self.read(self.p("etc/pvj/pvj.env")))
        self.assertIn("PVJ_USB_RW=0", self.read(self.p("etc/pvj/pvj.env")))
        usb_unit = self.read(self.p("etc/systemd/system/pvj-usb@.service"))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-usb mount /dev/%I", usb_unit)
        self.assertNotIn("@PVJ", usb_unit)
        self.assertIn("pvj-usb@%k.service", self.read(self.p("etc/udev/rules.d/99-pvj-usb.rules")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-usb")), "/opt/pvj/current/bin/pvj-usb")
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-rootfs")), "/opt/pvj/current/bin/pvj-rootfs")
        self.assertEqual(json.loads(self.read(self.p("etc/pvj/install.json")))["version"], "9.9.1")
        self.assertTrue(os.path.isdir(self.p("var/lib/pvj/video")))
        # the system log survives restarts (Raspberry Pi OS keeps it in memory); our drop-in sorts after theirs
        log = self.read(self.p("etc/systemd/journald.conf.d/50-pvj-persistent-log.conf"))
        self.assertIn("Storage=persistent", log)
        self.assertIn("SystemMaxUse=64M", log)
        self.assertGreater("50-pvj-persistent-log.conf", "40-rpi-volatile-storage.conf")

    def test_each_service_gets_its_own_runtime_folder_and_tmpfiles_keeps_the_parent_with_root(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        conf = self.read(self.p("etc/tmpfiles.d/pvj.conf"))
        self.assertIn("d /run/pvj 0755 root root -", conf)
        for unit, folder in (("pvj-player", "pvj/player"), ("pvj-web", "pvj/web"), ("pvj-netd", "pvj/netd")):
            text = self.read(self.p("etc/systemd/system/%s.service" % unit))
            self.assertIn("RuntimeDirectory=%s\n" % folder, text)
            self.assertIn("Environment=PVJ_RUNTIME_DIR=/run/%s\n" % folder, text)
            self.assertNotIn("RuntimeDirectory=pvj\n", text)
        self.assertFalse(os.path.exists(self.p("run")))          # a fresh install makes nothing under /run itself

    def old_run_folder(self, mode=0o770):
        """What a running version from before D45 leaves in /run/pvj: one shared folder (here DIR/run/pvj)."""
        run = self.p("run/pvj")
        os.makedirs(run)
        for name in ("pin", "player.pid", "preview.jpg", "overlay.bgra", "overlay-3.bgra", "mapper-7-1.glsl", "shader-7-2.glsl"):
            with open(os.path.join(run, name), "w") as f:
                f.write("old")
        with open(os.path.join(run, "undervoltage-seen"), "w") as f:
            f.write("1759570000")
        for name in ("player.sock", "netd.sock"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            cwd = os.getcwd()
            os.chdir(run)                     # a unix socket name is short; the stage folder's path may not be
            try:
                s.bind(name)
            finally:
                os.chdir(cwd)
            s.close()
        os.mkfifo(os.path.join(run, "capture.fifo"))
        # and what an account that owned the folder could have left under the new names
        os.mkdir(os.path.join(run, "netd"))
        with open(os.path.join(run, "netd", "netd.sock"), "w") as f:
            f.write("planted")
        os.symlink("/etc", os.path.join(run, "player"))
        os.symlink("/etc/passwd", os.path.join(run, "web"))
        os.chmod(run, mode)
        return run

    def test_upgrade_over_a_running_older_install_empties_the_shared_folder(self):
        run = self.old_run_folder()
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("a folder per service", r.stdout)
        self.assertEqual(os.stat(run).st_mode & 0o7777, 0o755)                  # on a box also chown root:root
        self.assertEqual(os.listdir(run), [".d45"])            # no stale file, link or planted folder; only the marker
        self.assertTrue(os.path.isdir("/etc") and os.path.isfile("/etc/passwd"))   # the links were removed, not followed

    def test_nothing_is_carried_over_so_a_planted_link_in_the_panels_folder_leads_nowhere(self):
        # The first version copied the undervoltage note into the panel's folder as root. That folder belongs to
        # pvj-web: a dangling link planted there made root create its target anywhere (found by the review).
        run = self.old_run_folder()
        outside = os.path.join(tempfile.mkdtemp(), "made-by-root")
        os.chmod(run, 0o770)
        os.unlink(os.path.join(run, "web"))
        os.mkdir(os.path.join(run, "web"))
        os.symlink(outside, os.path.join(run, "web", "undervoltage-seen"))
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.assertFalse(os.path.lexists(outside))
        self.assertEqual(os.listdir(run), [".d45"])
        # and with the new layout in place: a link planted by the account that owns the folder is left alone
        os.mkdir(os.path.join(run, "web"), 0o750)
        os.symlink(outside, os.path.join(run, "web", "undervoltage-seen"))
        with open(os.path.join(run, "undervoltage-seen"), "w") as f:
            f.write("1759570000")
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.assertFalse(os.path.lexists(outside))
        self.assertEqual(sorted(os.listdir(run)), [".d45", "web"])
        self.assertEqual(os.readlink(os.path.join(run, "web", "undervoltage-seen")), outside)

    def test_an_emptying_that_was_cut_short_is_finished_by_the_next_run(self):
        # after the chown and chmod of an interrupted run the folder already looks right from outside: root's, 0755
        run = self.old_run_folder(mode=0o755)
        self.assertFalse(os.path.exists(os.path.join(run, ".d45")))
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("a folder per service", r.stdout)
        self.assertEqual(os.listdir(run), [".d45"])            # the planted netd/ with its fake socket is gone

    def test_a_marker_that_is_a_link_or_a_folder_nobody_should_have_made_means_not_done(self):
        for plant in ("link", "folder"):
            stage = tempfile.mkdtemp()
            self.stage = stage
            run = self.p("run/pvj")
            os.makedirs(os.path.join(run, "netd"))
            if plant == "link":
                os.symlink("/etc/hostname", os.path.join(run, ".d45"))
            else:
                with open(os.path.join(run, ".d45"), "w"):
                    pass
                os.mkdir(os.path.join(run, "netd2"))
            os.chmod(run, 0o755)
            r = install(self.src, stage)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("a folder per service", r.stdout, plant)
            self.assertEqual(os.listdir(run), [".d45"], plant)
            self.assertFalse(os.path.islink(os.path.join(run, ".d45")))

    def test_reinstall_over_the_new_layout_leaves_the_services_folders_alone(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        run = self.p("run/pvj")
        for d in ("player", "web", "netd"):
            os.makedirs(os.path.join(run, d))
            with open(os.path.join(run, d, "live"), "w") as f:
                f.write("in use")
        with open(os.path.join(run, "stray"), "w") as f:
            f.write("x")
        with open(os.path.join(run, ".d45"), "w"):
            pass
        os.chmod(run, 0o755)
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("a folder per service", r.stdout)
        self.assertEqual(sorted(os.listdir(run)), [".d45", "netd", "player", "web"])     # the stray file went
        for d in ("player", "web", "netd"):
            self.assertEqual(self.read(os.path.join(run, d, "live")), "in use")

    def test_installed_copy_runs(self):
        install(self.src, self.stage)
        r = subprocess.run([self.p("opt/pvj/releases/9.9.1/bin/pvj-player"), "info"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("board", json.loads(r.stdout))

    def test_rerun_is_idempotent_and_keeps_edited_settings(self):
        install(self.src, self.stage)
        with open(self.p("etc/pvj/pvj.env"), "a") as f:
            f.write("PVJ_MEDIA_DIR=/mnt/mine\n")
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("keeping existing", r.stdout)
        self.assertIn("/mnt/mine", self.read(self.p("etc/pvj/pvj.env")))
        self.assertEqual(os.listdir(self.p("opt/pvj/releases")), ["9.9.1"])
        self.assertFalse(os.path.exists(self.p("opt/pvj/previous")))

    def test_upgrade_records_previous_release_for_rollback(self):
        install(self.src, self.stage)
        newer = copy_source("9.9.2")
        self.assertEqual(install(newer, self.stage).returncode, 0)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.2")
        self.assertEqual(self.read(self.p("opt/pvj/previous")).strip(), "/opt/pvj/releases/9.9.1")
        self.assertTrue(os.path.isdir(self.p("opt/pvj/releases/9.9.1")))

    def test_same_version_reinstall_replaces_files_without_leftovers(self):
        install(self.src, self.stage)
        with open(os.path.join(self.src, "pvj", "marker.py"), "w") as f:
            f.write("X = 1\n")
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        rel = self.p("opt/pvj/releases")
        self.assertEqual(os.listdir(rel), ["9.9.1"])  # no .old or .new left behind
        self.assertTrue(os.path.isfile(os.path.join(rel, "9.9.1", "pvj", "marker.py")))
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.1")

    def test_account_chosen_at_first_install_is_kept_when_user_is_omitted(self):
        install(self.src, self.stage)  # helper passes --user gigbox
        r = subprocess.run([os.path.join(self.src, "install", "install.sh"), "--stage", self.stage],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("User=gigbox", self.read(self.p("etc/systemd/system/pvj-player.service")))

    def test_dotdot_in_paths_is_refused(self):
        for bad in (["--prefix", "/opt/x/../../etc"], ["--prefix", "/opt/pvj/.."], ["--media", "/var/lib/../../etc"]):
            self.assertNotEqual(install(self.src, self.stage, *bad).returncode, 0, bad)
        self.assertEqual(os.listdir(self.stage), [])

    def test_other_accounts_are_not_added_to_the_pvj_group_by_default(self):
        text = self.read(os.path.join(self.src, "install", "install.sh"))
        self.assertNotIn("WEB_USER=www-data", text)  # the legacy PHP user must never reach the PIN file

    def test_dry_run_changes_nothing(self):
        r = install(self.src, self.stage, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.stage), [])

    def test_uninstall_keeps_settings_unless_purged(self):
        install(self.src, self.stage)
        self.assertEqual(install(self.src, self.stage, "--uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.p("opt/pvj")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-player")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-rootfs")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-update")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-player.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-web.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-netd.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-usb@.service")))
        self.assertFalse(os.path.exists(self.p("etc/udev/rules.d/99-pvj-usb.rules")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/journald.conf.d/50-pvj-persistent-log.conf")))
        self.assertFalse(os.path.exists(self.p("etc/tmpfiles.d/pvj.conf")))
        self.assertTrue(os.path.exists(self.p("etc/pvj/pvj.env")))
        install(self.src, self.stage, "--uninstall", "--purge")
        self.assertFalse(os.path.exists(self.p("etc/pvj")))

    def test_rejects_bad_input(self):
        for bad in (["--prefix", "relative"], ["--media", "/tmp/a b"], ["--prefix", "/opt/x;reboot"],
                    ["--web-user", "Bad User"], ["--bogus"],
                    ["--prefix", "/opt"], ["--prefix", "/"], ["--prefix", "/usr/local"]):
            r = install(self.src, self.stage, *bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(os.listdir(self.stage), [])

    def test_requires_root_without_stage_or_dry_run(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run([os.path.join(self.src, "install", "install.sh")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
