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
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def copy_source(version):
    src = tempfile.mkdtemp()
    for d in ("mctl", "bin", "install"):
        shutil.copytree(os.path.join(REPO, d), os.path.join(src, d), ignore=shutil.ignore_patterns("__pycache__"))
    init = os.path.join(src, "mctl", "__init__.py")
    with open(init, "w") as f:
        f.write('__version__ = "%s"\n' % version)
    return src


def install(src, stage, *extra):
    return subprocess.run([os.path.join(src, "install", "install.sh"), "--stage", stage, "--user", "gigbox", *extra],
                          capture_output=True, text=True, timeout=60)


class ReadOnlyRootTest(unittest.TestCase):
    """J-174: under the read-only root the installer would write to RAM and report success."""

    def run_with(self, mounts_text, *extra):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        table = os.path.join(d, "mounts")
        with open(table, "w") as f:
            f.write(mounts_text)
        env = dict(os.environ, NXLX_PROC_MOUNTS=table)
        return subprocess.run([os.path.join(REPO, "install", "install.sh"), *extra], capture_output=True, text=True, timeout=60, env=env)

    def test_refused_under_either_overlay_form(self):
        for line in ("overlay / overlay rw 0 0\n", "overlayroot / overlay rw 0 0\n"):
            r = self.run_with(line + "proc /proc proc rw 0 0\n")
            self.assertEqual(r.returncode, 1, line)
            self.assertIn("The system disk is read-only", r.stderr)
            self.assertIn("sudo nxlx-rootfs disable", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "as root the installer would run for real")
    def test_a_normal_root_and_a_docker_overlay_elsewhere_get_past_it(self):
        # No --dry-run: the read-only check runs and passes, and the next refusal (not root) is what ends the run. With
        # --dry-run the check is skipped altogether, so that form could not fail. The overlay check comes before the root
        # check in install.sh, so "run as root" does not hide it: a wrongly refused overlay would show the read-only message.
        r = self.run_with("/dev/sda2 / ext4 rw 0 0\noverlay /var/lib/docker/overlay2/x/merged overlay rw 0 0\n")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("The system disk is read-only", r.stderr)
        self.assertIn("run as root", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "as root the installer would run for real")
    def test_stage_and_dry_run_are_not_refused(self):
        # An overlay root with --dry-run alone: skipping the check is the only thing that keeps it from refusing.
        r = self.run_with("overlay / overlay rw 0 0\n", "--dry-run")
        self.assertNotIn("The system disk is read-only", r.stderr)
        # An overlay root with --stage alone (no --dry-run, not root): the stage path skips both the check and the root refusal.
        stage = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, stage, True)
        r = self.run_with("overlay / overlay rw 0 0\n", "--stage", stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)        # it ran through, not died for another reason
        self.assertNotIn("The system disk is read-only", r.stderr)
        self.assertNotIn("run as root", r.stderr)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.stage = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.stage, True)
        self.src = copy_source("9.9.1")
        self.addCleanup(shutil.rmtree, self.src, True)       # a copy of the source, 3 MB a test (D69)

    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()

    def p(self, *parts):
        return os.path.join(self.stage, *parts)

    def test_fresh_install_layout(self):
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.1")
        self.assertTrue(os.path.isfile(self.p("opt/nxlx/releases/9.9.1/mctl/player.py")))
        self.assertFalse(os.path.exists(self.p("opt/nxlx/releases/9.9.1.new")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-player")), "/opt/nxlx/current/bin/nxlx-player")
        unit = self.read(self.p("etc/systemd/system/nxlx-player.service"))
        self.assertIn("User=gigbox", unit)
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-player serve", unit)
        self.assertRegex(unit, r"(?m)^LimitRTPRIO=1$")       # the render thread's SCHED_RR 1, without a capability
        self.assertNotIn("@NXLX", unit)
        web = self.read(self.p("etc/systemd/system/nxlx-web.service"))
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-web", web)
        self.assertIn("User=nxlx-web", web)
        # the MIDI module reads /dev/snd/midi* and writes a known controller's lights (D53): audio group, a device
        # policy instead of PrivateDevices. The installer puts the unit with "rw" in place
        self.assertIn("SupplementaryGroups=audio", web)
        self.assertRegex(web, r"(?m)^DeviceAllow=char-alsa rw$")
        self.assertIn("DevicePolicy=closed", web)
        self.assertNotRegex(web, r"(?m)^DeviceAllow=char-alsa r$")
        self.assertNotRegex(web, r"(?m)^PrivateDevices=yes")
        self.assertIn("NoNewPrivileges=yes", web)
        self.assertNotIn("@NXLX", web)
        net = self.read(self.p("etc/systemd/system/nxlx-netd.service"))
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-netd", net)
        self.assertIn("ConditionPathExists=/usr/bin/nmcli", net)
        self.assertNotIn("@NXLX", net)
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-pin")), "/opt/nxlx/current/bin/nxlx-pin")
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-update")), "/opt/nxlx/current/bin/nxlx-update")
        self.assertIn("nxlx-release", self.read(self.p("etc/nxlx/allowed_signers")))
        self.assertIn("NXLX_MEDIA_DIR=/var/lib/nxlx/video", self.read(self.p("etc/nxlx/nxlx.env")))
        self.assertIn("NXLX_USB_RW=0", self.read(self.p("etc/nxlx/nxlx.env")))
        usb_unit = self.read(self.p("etc/systemd/system/nxlx-usb@.service"))
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-usb mount /dev/%I", usb_unit)
        self.assertNotIn("@NXLX", usb_unit)
        self.assertIn("nxlx-usb@%k.service", self.read(self.p("etc/udev/rules.d/99-nxlx-usb.rules")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-usb")), "/opt/nxlx/current/bin/nxlx-usb")
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-rootfs")), "/opt/nxlx/current/bin/nxlx-rootfs")
        self.assertEqual(json.loads(self.read(self.p("etc/nxlx/install.json")))["version"], "9.9.1")
        self.assertTrue(os.path.isdir(self.p("var/lib/nxlx/video")))
        # the system log survives restarts (Raspberry Pi OS keeps it in memory); our drop-in sorts after theirs
        log = self.read(self.p("etc/systemd/journald.conf.d/50-nxlx-persistent-log.conf"))
        self.assertIn("Storage=persistent", log)
        self.assertIn("SystemMaxUse=64M", log)
        self.assertGreater("50-nxlx-persistent-log.conf", "40-rpi-volatile-storage.conf")

    def test_each_service_gets_its_own_runtime_folder_and_tmpfiles_keeps_the_parent_with_root(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        conf = self.read(self.p("etc/tmpfiles.d/nxlx.conf"))
        self.assertIn("d /run/nxlx 0755 root root -", conf)
        for unit, folder in (("nxlx-player", "nxlx/player"), ("nxlx-web", "nxlx/web"), ("nxlx-netd", "nxlx/netd")):
            text = self.read(self.p("etc/systemd/system/%s.service" % unit))
            self.assertIn("RuntimeDirectory=%s\n" % folder, text)
            self.assertIn("Environment=NXLX_RUNTIME_DIR=/run/%s\n" % folder, text)
            self.assertNotIn("RuntimeDirectory=nxlx\n", text)
        self.assertFalse(os.path.exists(self.p("run")))          # a fresh install makes nothing under /run itself

    def old_run_folder(self, mode=0o770):
        """What a running version from before D45 leaves in /run/nxlx: one shared folder (here DIR/run/nxlx)."""
        run = self.p("run/nxlx")
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
        # nxlx-web: a dangling link planted there made root create its target anywhere (found by the review).
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
            self.addCleanup(shutil.rmtree, stage, True)
            self.stage = stage
            run = self.p("run/nxlx")
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
        run = self.p("run/nxlx")
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
        r = subprocess.run([self.p("opt/nxlx/releases/9.9.1/bin/nxlx-player"), "info"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("board", json.loads(r.stdout))

    def test_rerun_is_idempotent_and_keeps_edited_settings(self):
        install(self.src, self.stage)
        with open(self.p("etc/nxlx/nxlx.env"), "a") as f:
            f.write("NXLX_MEDIA_DIR=/mnt/mine\n")
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("keeping existing", r.stdout)
        self.assertIn("/mnt/mine", self.read(self.p("etc/nxlx/nxlx.env")))
        self.assertEqual(os.listdir(self.p("opt/nxlx/releases")), ["9.9.1"])
        self.assertFalse(os.path.exists(self.p("opt/nxlx/previous")))

    def test_upgrade_records_previous_release_for_rollback(self):
        install(self.src, self.stage)
        newer = copy_source("9.9.2")
        self.addCleanup(shutil.rmtree, newer, True)
        self.assertEqual(install(newer, self.stage).returncode, 0)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")
        self.assertEqual(self.read(self.p("opt/nxlx/previous")).strip(), "/opt/nxlx/releases/9.9.1")
        self.assertTrue(os.path.isdir(self.p("opt/nxlx/releases/9.9.1")))

    def test_same_version_reinstall_replaces_files_without_leftovers(self):
        install(self.src, self.stage)
        with open(os.path.join(self.src, "mctl", "marker.py"), "w") as f:
            f.write("X = 1\n")
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        rel = self.p("opt/nxlx/releases")
        self.assertEqual(os.listdir(rel), ["9.9.1"])  # no .old or .new left behind
        self.assertTrue(os.path.isfile(os.path.join(rel, "9.9.1", "mctl", "marker.py")))
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.1")

    def test_account_chosen_at_first_install_is_kept_when_user_is_omitted(self):
        install(self.src, self.stage)  # helper passes --user gigbox
        r = subprocess.run([os.path.join(self.src, "install", "install.sh"), "--stage", self.stage],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("User=gigbox", self.read(self.p("etc/systemd/system/nxlx-player.service")))

    def test_dotdot_in_paths_is_refused(self):
        for bad in (["--prefix", "/opt/x/../../etc"], ["--prefix", "/opt/nxlx/.."], ["--media", "/var/lib/../../etc"]):
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
        self.assertFalse(os.path.exists(self.p("opt/nxlx")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/nxlx-player")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/nxlx-rootfs")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/nxlx-update")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-player.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-web.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-netd.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-usb@.service")))
        self.assertFalse(os.path.exists(self.p("etc/udev/rules.d/99-nxlx-usb.rules")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/journald.conf.d/50-nxlx-persistent-log.conf")))
        self.assertFalse(os.path.exists(self.p("etc/tmpfiles.d/nxlx.conf")))
        self.assertTrue(os.path.exists(self.p("etc/nxlx/nxlx.env")))
        install(self.src, self.stage, "--uninstall", "--purge")
        self.assertFalse(os.path.exists(self.p("etc/nxlx")))

    # ---- the NDI input is opt-in per box (D62, the owner's answer of 2026-10-08) ----
    NDI_FILES = ("etc/systemd/system/nxlx-ndi.service", "etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf",
                 "etc/systemd/system/nxlx-player.service.d/50-nxlx-ndi.conf", "etc/tmpfiles.d/nxlx-ndi.conf")

    def ndi_names(self):
        """Every name under the staged root that has to do with NDI, apart from the program's own files."""
        found = []
        for d, dirs, names in os.walk(self.stage):
            for n in dirs + names:
                path = os.path.relpath(os.path.join(d, n), self.stage)
                if "ndi" in n.lower() and not path.startswith("opt/nxlx/releases/"):
                    found.append(path)
        return sorted(found)

    def opt_in(self):
        """What root's `nxlx-ndi-runtime install` leaves in /etc, written by the same code (mctl/ndisetup.py)."""
        import sys
        sys.path.insert(0, REPO)
        from mctl import ndisetup
        ndisetup.write_units(self.stage, "/opt/nxlx")

    def test_a_plain_install_leaves_nothing_of_ndi_but_the_program_and_its_command(self):
        for extra in ((), ("--offline",), ("--no-start",)):
            stage = self.stage = tempfile.mkdtemp()
            r = install(self.src, stage, *extra)
            if extra == ("--offline",) and "offline mode and missing" in r.stderr:
                continue                                                  # a machine without mpv: --offline stops before anything is written
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # the one command a person runs to opt in is there; nothing else is
            self.assertEqual(self.ndi_names(), ["usr/local/bin/nxlx-ndi-runtime"], extra)
            self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-ndi-runtime")), "/opt/nxlx/current/bin/nxlx-ndi-runtime")
            for path in self.NDI_FILES:
                self.assertFalse(os.path.lexists(self.p(path)), path)
            for name in ("nxlx-web.service", "nxlx-player.service"):
                unit = self.read(self.p("etc/systemd/system/" + name))
                self.assertNotIn("nxlx-ndi", unit, name)                   # a group that does not exist would keep the unit from starting
                self.assertNotIn("NXLX_NDI", unit, name)
            self.assertFalse(os.path.exists(self.p("opt/nxlx-ndi")))
            self.assertNotRegex(r.stdout + r.stderr, r"(?i)avahi|\bndi\b", extra)      # it asks for no package and says nothing of NDI
        # a second run over it: still nothing
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.assertEqual(self.ndi_names(), ["usr/local/bin/nxlx-ndi-runtime"])

    def test_the_units_of_the_panel_and_the_player_are_what_they_are_without_ndi(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.assertRegex(self.read(self.p("etc/systemd/system/nxlx-web.service")), r"(?m)^SupplementaryGroups=audio video$")
        self.assertRegex(self.read(self.p("etc/systemd/system/nxlx-player.service")), r"(?m)^SupplementaryGroups=video render audio input$")

    def test_a_box_that_opted_in_stays_opted_in_and_gets_the_unit_of_the_new_version(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.opt_in()
        with open(self.p(self.NDI_FILES[0]), "w") as f:
            f.write("[Service]\nExecStart=/opt/nxlx/current/bin/nxlx-ndi --from-an-older-version\n")
        os.unlink(self.p(self.NDI_FILES[2]))                              # and one of its files went missing
        r = install(copy_source("9.9.2"), self.stage, "--no-start")       # an update, as the panel's update runs it
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("this box has NDI set up", r.stdout)
        self.assertNotIn("could not write the NDI helper's unit", r.stdout)
        unit = self.read(self.p(self.NDI_FILES[0]))
        self.assertNotIn("--from-an-older-version", unit)
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-ndi\n", unit)
        self.assertNotIn("@NXLX", unit)
        self.assertIn("User=nxlx-ndi\n", unit)
        self.assertIn("IPAddressDeny=any\n", unit)
        with open(os.path.join(REPO, "mctl", "systemd", "nxlx-ndi.service")) as f:
            self.assertEqual(unit, f.read().replace("@NXLX_DIR@", "/opt/nxlx/current"))
        self.assertIn("SupplementaryGroups=nxlx-ndi\n", self.read(self.p(self.NDI_FILES[1])))
        self.assertIn("Environment=NXLX_NDI_DIR=/run/nxlx-ndi\n", self.read(self.p(self.NDI_FILES[1])))
        self.assertIn("SupplementaryGroups=nxlx-ndi\n", self.read(self.p(self.NDI_FILES[2])))
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")
        self.assertFalse([n for n in os.listdir(self.p("opt/nxlx/releases/9.9.2/mctl")) if n == "__pycache__"])     # root's run left no compiled files
        # the installer's own units still do not name the group: only the drop-ins do
        self.assertNotIn("nxlx-ndi", self.read(self.p("etc/systemd/system/nxlx-web.service")))

    def test_another_install_folder_reaches_the_helpers_unit_too(self):
        self.assertEqual(install(self.src, self.stage, "--prefix", "/srv/box/nxlx").returncode, 0)
        self.opt_in()
        self.assertEqual(install(self.src, self.stage, "--prefix", "/srv/box/nxlx").returncode, 0)
        self.assertIn("ExecStart=/srv/box/nxlx/current/bin/nxlx-ndi\n", self.read(self.p(self.NDI_FILES[0])))

    def test_a_slash_at_the_end_of_the_install_folder_is_dropped_and_a_doubled_one_refused(self):
        # Review L1: the folder went into install.json as it was typed, and the NDI setup takes it only one way.
        import sys
        sys.path.insert(0, REPO)
        from mctl import ndisetup
        r = install(self.src, self.stage, "--prefix", "/srv/box/nxlx///")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(self.read(self.p("etc/nxlx/install.json")))["prefix"], "/srv/box/nxlx")
        self.assertEqual(ndisetup.read_prefix(self.stage), "/srv/box/nxlx")
        self.assertEqual(os.readlink(self.p("srv/box/nxlx/current")), "/srv/box/nxlx/releases/9.9.1")
        self.assertIn("ExecStart=/srv/box/nxlx/current/bin/nxlx-web", self.read(self.p("etc/systemd/system/nxlx-web.service")))
        ndisetup.check(self.stage, account=lambda: None)   # the setup would take this box
        stage2 = tempfile.mkdtemp()
        for bad in ("/opt//mctl", "//opt/nxlx", "/opt/nxlx//x", "/opt/nxlx/./", "/opt/./pvj", "/opt/nxlx/."):      # the last three: second review, N4
            self.assertNotEqual(install(self.src, stage2, "--prefix", bad).returncode, 0, bad)
        self.assertEqual(os.listdir(stage2), [])

    def test_a_helper_without_the_mark_is_removed_by_the_next_install(self):
        # Review L4: a setup that was cut short, or a box that ran this branch while the helper went on every box.
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.opt_in()
        os.unlink(self.p(self.NDI_FILES[1]))                              # the mark is gone; the unit and the player's drop-in are not
        os.rmdir(os.path.dirname(self.p(self.NDI_FILES[1])))
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("removing an NDI helper that nobody set up on this box", r.stdout)
        self.assertNotIn("this box has NDI set up", r.stdout)
        self.assertEqual(self.ndi_names(), ["usr/local/bin/nxlx-ndi-runtime"])
        r = install(self.src, self.stage)                                 # and the run after that says nothing of NDI
        self.assertNotRegex(r.stdout + r.stderr, r"(?i)avahi|\bndi\b")

    def test_a_mark_that_is_a_link_is_not_an_opt_in(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        os.makedirs(self.p("etc/systemd/system/nxlx-web.service.d"))
        os.symlink("/etc/passwd", self.p(self.NDI_FILES[1]))
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("this box has NDI set up", r.stdout)
        self.assertFalse(os.path.exists(self.p(self.NDI_FILES[0])))
        # Second review, N2: systemd reads a drop-in through a link. The link is removed (rm takes the link, not what
        # it points to) and that is said loudly.
        self.assertFalse(os.path.lexists(self.p(self.NDI_FILES[1])))
        self.assertTrue(os.path.exists("/etc/passwd"))
        self.assertIn("WARNING:", r.stdout)
        self.assertIn("is a link, which the NDI setup never makes", r.stdout)
        self.assertIn("This box is NOT set up for NDI.", r.stdout)

    def test_a_link_at_the_mark_with_a_unit_present_leaves_nothing_systemd_would_read(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.opt_in()
        target = os.path.join(tempfile.mkdtemp(), "kept.conf")
        with open(target, "w") as f:
            f.write("[Service]\nSupplementaryGroups=nxlx-ndi\nEnvironment=NXLX_NDI_DIR=/run/nxlx-ndi\n")
        os.unlink(self.p(self.NDI_FILES[1]))
        os.symlink(target, self.p(self.NDI_FILES[1]))                     # the mark is now a link to a file with the same words
        wants = self.p("etc/systemd/system/multi-user.target.wants")
        os.makedirs(wants)
        os.symlink("/etc/systemd/system/nxlx-ndi.service", os.path.join(wants, "nxlx-ndi.service"))     # what "systemctl enable" left
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("is a link, which the NDI setup never makes", r.stdout)
        self.assertIn("removing an NDI helper that nobody set up on this box", r.stdout)
        self.assertEqual(self.ndi_names(), ["usr/local/bin/nxlx-ndi-runtime"])      # no unit, no drop-in, no link, and (N3) no link to start it
        self.assertFalse(os.path.lexists(os.path.join(wants, "nxlx-ndi.service")))
        with open(target) as f:
            self.assertIn("NXLX_NDI_DIR", f.read())                        # what the link pointed to was not touched

    def test_a_folder_in_the_place_of_the_unit_does_not_stop_the_install(self):
        # Second review, L1: "rm -f" on a folder fails, and under "set -e" that ended every install and update here.
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        os.makedirs(self.p(self.NDI_FILES[0] + "/inside"))
        r = install(copy_source("9.9.2"), self.stage, "--no-start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING: could not remove everything of that NDI helper", r.stdout)
        self.assertIn("remove it by hand", r.stdout)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")      # the install went on to its end
        self.assertIn("installed. Check the device", r.stdout)
        # the same with a folder where a drop-in goes, in the uninstall
        os.makedirs(self.p(self.NDI_FILES[2] + "/inside"))
        r = install(self.src, self.stage, "--uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING: could not remove everything of the NDI helper", r.stdout)
        self.assertFalse(os.path.exists(self.p("opt/nxlx")))

    def test_uninstall_takes_the_helper_and_leaves_the_owners_library_unless_purged(self):
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.opt_in()
        os.makedirs(self.p("opt/nxlx-ndi"))
        with open(self.p("opt/nxlx-ndi/libndi.so.6"), "w") as f:
            f.write("the owner's copy")
        other = self.p("etc/systemd/system/nxlx-web.service.d/90-owner.conf")
        with open(other, "w") as f:
            f.write("[Service]\n")
        r = install(self.src, self.stage, "--uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for path in self.NDI_FILES + ("usr/local/bin/nxlx-ndi-runtime", "etc/systemd/system/nxlx-player.service.d"):
            self.assertFalse(os.path.lexists(self.p(path)), path)
        self.assertTrue(os.path.exists(other))                            # somebody else's drop-in and its folder stay
        self.assertTrue(os.path.exists(self.p("opt/nxlx-ndi/libndi.so.6")))
        # installed again afterwards, the box has not opted in
        os.unlink(other)
        os.rmdir(os.path.dirname(other))
        self.assertEqual(install(self.src, self.stage).returncode, 0)
        self.assertEqual(self.ndi_names(), ["opt/nxlx-ndi", "opt/nxlx-ndi/libndi.so.6", "usr/local/bin/nxlx-ndi-runtime"])
        self.assertEqual(install(self.src, self.stage, "--uninstall", "--purge").returncode, 0)
        self.assertFalse(os.path.exists(self.p("opt/nxlx-ndi")))

    # Hardware video decoding on a PC node (J-152): the VA-API driver for the graphics found in sysfs, best effort.
    def _pci(self, *devices):
        pci = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pci, True)
        for i, (cls, vendor) in enumerate(devices):
            d = os.path.join(pci, "0000:00:%02x.0" % i)
            os.makedirs(d)
            for name, value in (("class", cls), ("vendor", vendor)):
                with open(os.path.join(d, name), "w") as f:
                    f.write(value + "\n")
        return pci

    def _va_install(self, arch, pci, *extra):
        env = dict(os.environ, NXLX_INSTALL_ARCH=arch, NXLX_INSTALL_PCI=pci)
        return subprocess.run([os.path.join(self.src, "install", "install.sh"), "--stage", self.stage, "--user", "gigbox", *extra],
                              capture_output=True, text=True, timeout=60, env=env)

    def test_an_intel_pc_node_is_told_its_va_driver(self):
        pci = self._pci(("0x060000", "0x8086"), ("0x030000", "0x8086"), ("0x0c0330", "0x8086"))
        r = self._va_install("x86_64", pci)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("graphics: Intel; hardware video decoding needs intel-media-va-driver\n", r.stdout)
        self.assertNotIn("mesa-va-drivers", r.stdout)
        # in stage mode nothing is installed, and what is missing is said (unless this machine has the packages already)
        self.assertTrue("until these are installed: " in r.stdout or "the VA-API driver is installed" in r.stdout, r.stdout)
        self.assertNotIn("installing intel-media-va-driver", r.stdout)

    def test_an_amd_pc_node_gets_mesa_and_both_get_both(self):
        r = self._va_install("x86_64", self._pci(("0x030000", "0x1002")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("graphics: AMD; hardware video decoding needs mesa-va-drivers\n", r.stdout)
        r = self._va_install("x86_64", self._pci(("0x030000", "0x8086"), ("0x038000", "0x1002"), ("0x030000", "0x8086")))
        self.assertIn("graphics: Intel AMD; hardware video decoding needs intel-media-va-driver mesa-va-drivers\n", r.stdout)

    def test_other_graphics_and_a_pi_get_no_va_driver(self):
        r = self._va_install("x86_64", self._pci(("0x030000", "0x10de"), ("0x020000", "0x8086")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("graphics: no Intel or AMD graphics found", r.stdout)
        self.assertNotIn("va-driver", r.stdout)
        r = self._va_install("aarch64", self._pci(("0x030000", "0x8086")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("graphics:", r.stdout)
        self.assertNotIn("va-driver", r.stdout)

    def test_va_driver_offline_and_dry_run_install_nothing(self):
        pci = self._pci(("0x030000", "0x8086"))
        r = self._va_install("x86_64", pci, "--offline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("installing intel-media-va-driver", r.stdout)
        shutil.rmtree(self.stage)                   # the dry run starts from nothing, and leaves nothing
        os.mkdir(self.stage)
        r = self._va_install("x86_64", pci, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("graphics: Intel; hardware video decoding needs intel-media-va-driver\n", r.stdout)
        self.assertNotIn("installing intel-media-va-driver", r.stdout)
        self.assertEqual(os.listdir(self.stage), [])

    def test_a_failed_va_driver_install_does_not_fail_the_install(self):
        # The apt branch only runs on a real install; its line must end in "|| log", as the other optional packages do.
        text = self.read(os.path.join(self.src, "install", "install.sh"))
        line = [l for l in text.splitlines() if "run apt-get install" in l and "va_need" in l]
        self.assertEqual(len(line), 1, line)
        self.assertIn('|| log "could not install ${va_need[*]}; the install goes on', line[0])
        # the stand-ins for the machine and sysfs are for stage mode only: a real install reads the real ones
        self.assertIn('if [ "$REAL" = 0 ]; then va_arch="${NXLX_INSTALL_ARCH:-$va_arch}"', text)

    # Time sync (J-154): a system without any gets systemd-timesyncd, best effort; one that has one is left alone.
    def test_a_system_without_time_sync_is_told_and_stage_mode_installs_nothing(self):
        for extra in ((), ("--offline",)):
            r = install(self.src, self.stage, *extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("time sync: none installed; the clock is not kept right until systemd-timesyncd", r.stdout)
            self.assertNotIn("installing systemd-timesyncd", r.stdout)

    def test_a_system_with_time_sync_is_left_as_it_is(self):
        for path in ("usr/lib/systemd/systemd-timesyncd", "usr/sbin/chronyd", "usr/sbin/ntpd"):
            stage = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, stage, True)
            os.makedirs(os.path.join(stage, os.path.dirname(path)))
            open(os.path.join(stage, path), "w").close()       # what a Pi image (systemd-timesyncd) or chrony brings
            r = install(self.src, stage)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("time sync: %s is installed\n" % os.path.basename(path), r.stdout)
            self.assertNotIn("time sync: none installed", r.stdout)

    def test_time_sync_dry_run_writes_nothing_and_a_failed_install_does_not_fail_the_install(self):
        r = install(self.src, self.stage, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("time sync: none installed", r.stdout)
        self.assertEqual(os.listdir(self.stage), [])
        # the apt branch only runs on a real install: a failed apt, start or set-ntp is said and the install goes on
        text = self.read(os.path.join(self.src, "install", "install.sh"))
        line = [l for l in text.splitlines() if "apt-get install" in l and "systemd-timesyncd" in l]
        self.assertEqual(len(line), 1, line)
        self.assertIn("if run apt-get install -y --no-install-recommends systemd-timesyncd; then", line[0])
        self.assertIn("run systemctl enable --now systemd-timesyncd.service || log", text)
        self.assertIn("run timedatectl set-ntp true || log", text)

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


class StateFolderLinksTest(unittest.TestCase):
    """J-175: root's chown and chmod in /var/lib/nxlx must not follow a link the panel account planted there.

    The functions are cut out of install.sh and run on a temp folder. chown and chmod are replaced by recorders (a test
    cannot chown to the panel account); nxlx_fd_own, which does the real work for backups and the media folder, is also
    run for real against real links, as the current user (it can fchown to its own account)."""

    def funcs(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            text = f.read()
        out = []
        for name in ("nxlx_fd_own", "nxlx_state_fix", "nxlx_media_fix"):
            start = text.index(name + "() {")
            body = text[start:text.index("\n}\n", start) + 3]
            if name == "nxlx_media_fix":
                body = body.replace("nxlx-svc", self.mygroup)      # the test box has no such group
            out.append(body)
        return out

    def run_fix(self, call, fake_fd_own=False):
        funcs = self.funcs()
        bindir = os.path.join(self.work, "bin")
        if not os.path.isdir(bindir):
            os.mkdir(bindir)
        for cmd in ("chown", "chmod"):
            with open(os.path.join(bindir, cmd), "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> "%s"\n' % (cmd, os.path.join(self.work, "calls")))
            os.chmod(os.path.join(bindir, cmd), 0o755)
        fake = 'nxlx_fd_own() { echo "fd_own $*" >> "%s"; mkdir -p "$1"; }\n' % os.path.join(self.work, "calls")
        script = ('set -euo pipefail\nlog() { echo "$*"; }\ndie() { echo "DIE $*" >&2; exit 1; }\nNXLX_USER=%s\n' % self.me
                  + "".join(funcs) + (fake if fake_fd_own else "") + call + "\n")
        env = dict(os.environ, PATH=bindir + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
        calls = os.path.join(self.work, "calls")
        lines = []
        if os.path.exists(calls):
            with open(calls) as f:
                lines = f.read().splitlines()
        return r, lines

    def setUp(self):
        import grp
        import pwd
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.me = pwd.getpwuid(os.getuid()).pw_name
        self.mygroup = grp.getgrgid(os.getgid()).gr_name
        self.sd = os.path.join(self.work, "state")
        os.mkdir(self.sd)
        self.victim = os.path.join(self.work, "etc-file")
        with open(self.victim, "w") as f:
            f.write("x")
        os.chmod(self.victim, 0o640)
        self.victim_dir = os.path.join(self.work, "etc-dir")
        os.mkdir(self.victim_dir, 0o755)
        self.before = {p: os.stat(p) for p in (self.victim, self.victim_dir)}

    def untouched(self, calls=()):
        for line in calls:
            self.assertNotIn(self.victim, line)
            self.assertNotIn(self.victim_dir, line)
        for p, b in self.before.items():
            a = os.stat(p)
            self.assertEqual((a.st_uid, a.st_gid, a.st_mode), (b.st_uid, b.st_gid, b.st_mode), p)

    def test_links_in_the_settings_names_and_at_backups_are_not_followed(self):
        with open(os.path.join(self.sd, "settings.json"), "w") as f:
            f.write("{}")
        os.symlink(self.victim, os.path.join(self.sd, "settings.json.bak"))
        os.symlink(self.victim, os.path.join(self.sd, "settings.json.tmp"))
        os.link(self.victim, os.path.join(self.sd, "settings.json.hard"))      # a second name of a file: skipped too
        os.symlink(self.victim_dir, os.path.join(self.sd, "backups"))
        r, calls = self.run_fix('nxlx_state_fix "%s"' % self.sd, fake_fd_own=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.untouched(calls)
        self.assertIn("chown -h nxlx-web:nxlx-svc " + os.path.join(self.sd, "settings.json"), calls)
        self.assertFalse(any(os.path.join(self.sd, "settings.json.") in c for c in calls), calls)
        self.assertTrue(os.path.islink(os.path.join(self.sd, "settings.json.bak")))   # skipped, left as planted
        b = os.path.join(self.sd, "backups")
        self.assertTrue(os.path.isdir(b) and not os.path.islink(b))     # the link was removed, a folder is made
        self.assertIn("fd_own %s %s root root 0700" % (b, self.sd), calls)
        self.assertFalse(any(c.startswith("chmod") and b in c for c in calls), calls)   # never chmod by path

    def test_every_chown_by_path_in_the_state_folder_uses_h(self):
        r, calls = self.run_fix('nxlx_state_fix "%s"' % self.sd, fake_fd_own=True)
        chowns = [c for c in calls if c.startswith("chown")]
        self.assertTrue(chowns)
        for c in chowns:
            self.assertTrue(c.startswith("chown -h "), c)

    def test_the_helper_refuses_a_link_at_any_name_and_changes_nothing_through_it(self):
        helper = 'nxlx_fd_own "%s" "%s" %s %s 2775'
        # a link at the final name
        link = os.path.join(self.sd, "video")
        os.symlink(self.victim_dir, link)
        r, _ = self.run_fix(helper % (link, self.sd, self.me, self.mygroup))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing was changed through it", r.stderr)
        self.untouched()
        # a link in the middle of a deeper path
        os.symlink(self.victim_dir, os.path.join(self.sd, "mid"))
        r, _ = self.run_fix(helper % (os.path.join(self.sd, "mid", "video"), self.sd, self.me, self.mygroup))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.victim_dir, "video")))
        self.untouched()

    def test_the_helper_makes_and_sets_real_folders_and_clears_a_setgid_bit_it_is_not_given(self):
        deep = os.path.join(self.sd, "a", "video")
        r, _ = self.run_fix('nxlx_fd_own "%s" "%s" %s %s 2775' % (deep, self.sd, self.me, self.mygroup))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(deep).st_mode & 0o7777, 0o2775)
        r, _ = self.run_fix('nxlx_fd_own "%s" "%s" %s %s 0700' % (deep, self.sd, self.me, self.mygroup))
        self.assertEqual(os.stat(deep).st_mode & 0o7777, 0o700)

    def test_a_link_at_the_media_folder_in_the_state_folder_is_removed_and_a_folder_made(self):
        link = os.path.join(self.sd, "video")
        os.symlink(self.victim_dir, link)
        r, calls = self.run_fix('nxlx_media_fix "%s" "%s"' % (link, self.sd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(link) and not os.path.islink(link))
        self.assertEqual(os.stat(link).st_mode & 0o7777, 0o2775)
        self.untouched(calls)
        self.assertIn("was a link (to %s) and has been removed" % self.victim_dir, r.stdout)
        self.assertIn("--media", r.stdout)

    def test_media_cannot_be_the_state_folder_itself(self):
        src = copy_source("9.9.1")
        self.addCleanup(shutil.rmtree, src, True)
        for spelling in ("/var/lib/nxlx", "/var/lib/nxlx/", "/var/lib//nxlx//"):
            r = install(src, self.work, "--media", spelling)
            self.assertNotEqual(r.returncode, 0, spelling)
            self.assertIn("--media cannot be /var/lib/nxlx", r.stderr)

    def test_a_trailing_slash_or_double_slash_spelling_is_one_way_of_writing_the_media_folder(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            text = f.read()
        start = text.index('while [ "${#MEDIA}" -gt 1 ]')
        snippet = text[start:text.index("\nfor name in", start)]
        for given, want in (("/var/lib/nxlx/video/", "/var/lib/nxlx/video"), ("/var/lib/nxlx//video", "/var/lib/nxlx/video"),
                            ("/var/lib/nxlx///video//", "/var/lib/nxlx/video"), ("/media/x", "/media/x")):
            r = subprocess.run(["bash", "-c", 'MEDIA="$1"\n' + snippet + '\necho "$MEDIA"', "x", given], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), want, given)

    def test_a_link_at_a_media_folder_outside_the_state_folder_is_followed_as_before(self):
        elsewhere = os.path.join(self.work, "media-link")
        os.symlink(self.victim_dir, elsewhere)
        r, calls = self.run_fix('nxlx_media_fix "%s" "%s"' % (elsewhere, self.sd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(calls), 2, calls)


if __name__ == "__main__":
    unittest.main()
