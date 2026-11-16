# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""install/install.sh over a node with the old names (rename PR 7, D136), in --stage mode: the node is installed by
the installer of the release before the rename, then this installer converts it with bin/nxlx-convert first and
carries on under the new names. The real run, with accounts, sudoers and systemd, is tests/real_install_test.sh."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BEFORE = "0ff0ddbeb662efb405186a0e774839cb1f61b2de"     # master with release 0.1.1, before rename PR 7


def version():
    with open(os.path.join(REPO, "mctl", "__init__.py")) as f:
        return next(line.split('"')[1] for line in f if line.startswith("__version__"))


def snapshot(root, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        if any(rel == s or rel.startswith(s + "/") for s in skip):
            dirs[:] = []
            continue
        for n in dirs + files:
            p = os.path.join(d, n)
            r = os.path.normpath(os.path.join(rel, n))
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[r] = ("link", os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                out[r] = ("dir", stat.S_IMODE(st.st_mode))
            else:
                with open(p, "rb") as f:
                    out[r] = ("file", hashlib.sha256(f.read()).hexdigest(), stat.S_IMODE(st.st_mode))
    return out


class InstallConvertsAnOldNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = tempfile.mkdtemp()
        try:
            tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE, "mctl", "bin", "install"],
                                 check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError):
            shutil.rmtree(cls.old, True)
            raise unittest.SkipTest("no %s here to install the old node from" % BEFORE)
        subprocess.run(["tar", "-x", "-C", cls.old], input=tar, check=True)
        with open(os.path.join(cls.old, "mctl", "__init__.py")) as f:
            cls.old_version = next(line.split('"')[1] for line in f if line.startswith("__version__"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.old, True)

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        r = subprocess.run([os.path.join(self.old, "install", "install.sh"), "--stage", self.root, "--user", "pvj-player"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isdir(self.p("opt/pvj/releases", self.old_version)), "the old node")

    def p(self, *rel):
        return os.path.join(self.root, *rel)

    def read(self, *rel):
        with open(self.p(*rel)) as f:
            return f.read()

    def install(self, *args):
        return subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", self.root, *args],
                              capture_output=True, text=True, timeout=120)

    def test_the_installer_converts_the_node_then_installs_under_the_new_names(self):
        old_release = "/opt/pvj/releases/" + self.old_version
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("putting version %s into /opt/pvj/releases, then converting the node" % version(), r.stdout)
        self.assertIn("Converted:", r.stdout)
        self.assertIn("CONVERTED from the old names", r.stdout)
        for gone in ("opt/pvj", "etc/pvj", "var/lib/pvj", "etc/systemd/system/pvj-web.service", "etc/tmpfiles.d/pvj.conf",
                     "etc/udev/rules.d/99-pvj-usb.rules", "usr/local/bin/pvj-player"):
            self.assertFalse(os.path.lexists(self.p(gone)), gone)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/" + version())
        self.assertTrue(os.path.isdir(self.p("opt/nxlx/releases", self.old_version, "bin")), "the old release stays")
        self.assertFalse(os.path.exists(self.p("opt/nxlx/releases", version() + ".new")))
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/%s\n" % self.old_version)
        for name in os.listdir(self.p("etc/systemd/system")):
            self.assertNotIn("pvj", name)
        web = self.read("etc/systemd/system/nxlx-web.service")
        for line in ("User=nxlx-web", "Group=nxlx-svc", "ExecStart=/opt/nxlx/current/bin/nxlx-web",
                     "EnvironmentFile=-/etc/nxlx/nxlx.env", "Environment=PVJ_RUNTIME_DIR=/run/nxlx/web"):
            self.assertIn(line, web)
        self.assertIn("User=nxlx-player", self.read("etc/systemd/system/nxlx-player.service"), "the old account's new name")
        inst = json.loads(self.read("etc/nxlx/install.json"))
        self.assertEqual((inst["prefix"], inst["user"], inst["version"]), ("/opt/nxlx", "nxlx-player", version()))
        self.assertIn("PVJ_MEDIA_DIR=/var/lib/nxlx/video", self.read("etc/nxlx/nxlx.env"))
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-convert")), "/opt/nxlx/current/bin/nxlx-convert")
        self.assertTrue(os.path.isfile(self.p("etc/systemd/system/nxlx-convert-undo.timer")), "the undo is armed")
        # A second run is a plain install: nothing is converted again, and the old release is still "previous".
        again = self.install()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("CONVERTED", again.stdout)
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/%s\n" % self.old_version)
        # The undo goes back to the old names and the release the node ran before.
        u = subprocess.run(["python3", os.path.join(REPO, "bin", "nxlx-convert"), "--root", self.root, "--undo"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), old_release)
        self.assertTrue(os.path.isfile(self.p("etc/systemd/system/pvj-web.service")))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-web", self.read("etc/systemd/system/pvj-web.service"))
        self.assertFalse(os.path.exists(self.p("opt/nxlx")))

    def test_a_refused_conversion_stops_the_installer_and_changes_nothing_but_the_waiting_release(self):
        os.makedirs(self.p("proc"))
        with open(self.p("proc/mounts"), "w") as f:
            f.write("overlayroot / overlay rw,relatime 0 0\n")            # a read-only root (nxlx-rootfs on)
        before = snapshot(self.root)
        r = self.install()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("the root file system is read-only", r.stdout)
        self.assertIn("the conversion was refused and nothing on the node was changed", r.stderr)
        after = snapshot(self.root)
        waiting = "opt/pvj/releases/" + version()
        self.assertTrue(os.path.isdir(self.p(waiting)))
        self.assertEqual({k: v for k, v in after.items() if k != waiting and not k.startswith(waiting + "/")}, before)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/" + self.old_version)

    def test_a_dry_run_on_an_old_node_says_what_it_would_do_and_changes_nothing(self):
        before = snapshot(self.root)
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would convert the node: python3 /opt/pvj/releases/%s/bin/nxlx-convert --run --release %s"
                      % (version(), version()), r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_another_prefix_or_an_uninstall_on_an_old_node_is_refused(self):
        before = snapshot(self.root)
        for args, said in ((("--prefix", "/srv/box/nxlx"), "run the installer once without --prefix"),
                           (("--uninstall",), "run the installer once without --uninstall")):
            r = self.install(*args)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn(said, r.stderr)
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
