# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from mctl import update
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "tools", "make-release.sh")


def version():
    with open(os.path.join(REPO, "mctl", "__init__.py")) as f:
        return re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)


def build(*args, cwd=REPO):
    # the script works on the checkout it lives in, so run the copy inside `cwd`
    script = os.path.join(cwd, "tools", "make-release.sh")
    return subprocess.run([script, *args], cwd=cwd, capture_output=True, text=True)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.out = os.path.join(REPO, "dist", "nxlx-%s.tar.gz" % version())
        self.addCleanup(shutil.rmtree, os.path.join(REPO, "dist"), True)

    def test_bundle_is_valid_reproducible_and_installable_by_the_updater(self):
        r = build(version(), "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        first = update.sha256_file(self.out)
        build(version(), "--allow-dirty")
        self.assertEqual(update.sha256_file(self.out), first, "same commit must give identical bytes")
        with open(self.out + ".sha256") as f:
            update.verify_sha256(self.out, f.read())
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        update.safe_extract(self.out, work)
        self.assertEqual(update.inspect_bundle(work)["version"], version())
        self.assertFalse(os.path.exists(os.path.join(work, "backend.php")), "legacy code must not ship")
        self.assertFalse(os.path.exists(os.path.join(work, "sync")))
        # the Signal look's fonts and their licence texts ship, byte for byte: a box at a venue has no internet
        fonts = os.path.join(work, "mctl", "web", "fonts")
        self.assertEqual(sorted(os.listdir(fonts)), ["OFL-Archivo.txt", "OFL-JetBrainsMono.txt", "archivo-latin.06fa7831.woff2", "jetbrains-mono-500-latin.6c95bc2f.woff2"])
        for name in os.listdir(fonts):
            with open(os.path.join(fonts, name), "rb") as shipped, open(os.path.join(REPO, "mctl", "web", "fonts", name), "rb") as ours:
                self.assertEqual(shipped.read(), ours.read(), name)

    def test_the_script_writes_the_new_names_only(self):
        r = build(version(), "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(REPO, "dist"))),
                         ["nxlx-%s.tar.gz" % version(), "nxlx-%s.tar.gz.sha256" % version()])
        with open(self.out + ".sha256") as f:
            self.assertEqual(f.read().split()[1], "nxlx-%s.tar.gz" % version())
        tops = subprocess.run(["tar", "-tzf", self.out], capture_output=True, text=True, check=True).stdout.split("\n")
        self.assertTrue(all(t.startswith("nxlx-%s/" % version()) for t in tops if t), "one folder inside, named like the bundle")

    def test_the_updater_installed_before_the_system_rename_refuses_the_bundle(self):
        """A node with the old names runs the updater of its own release, which looks for bin/pvj-player and (before
        the package rename) pvj/__init__.py, and refuses a bundle without them. That is wanted from rename PR 7 on:
        the panel's Update button on such a node refuses this release, and the first install is install.sh over ssh,
        which converts the node. The updaters of the releases before are run here from pinned commits of master."""
        befores = {"1057087dcc9939af9044f68d03ef3ea72a322116": "pvj",    # release 0.1.1 before the package rename
                   "0ff0ddbeb662efb405186a0e774839cb1f61b2de": "mctl"}   # release 0.1.1 after it (PR 6), before PR 7
        r = build(version(), "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        update.safe_extract(self.out, work)
        self.assertFalse(os.path.exists(os.path.join(work, "pvj")), "no stubs for the old updater any more")
        self.assertFalse(os.path.exists(os.path.join(work, "bin", "pvj-player")))
        self.assertEqual(update.inspect_bundle(work)["version"], version(), "this release's updater takes it")
        ran = 0
        for before, pkg in befores.items():
            try:
                tar = subprocess.run(["git", "-C", REPO, "archive", before, pkg], check=True, capture_output=True).stdout
            except (OSError, subprocess.CalledProcessError):
                continue
            old = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, old, True)
            subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
            script = ("import sys\nfrom %s import update\ntry:\n    update.inspect_bundle(sys.argv[1])\n"
                      "except update.UpdateError as e:\n    print('refused:', e)\nelse:\n    print('taken')\n" % pkg)
            run = subprocess.run([sys.executable, "-c", script, work], cwd=old, capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONPATH=old))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("refused: bundle is missing", run.stdout, before)
            ran += 1
        if not ran:
            self.skipTest("none of %s here to read the releases before from" % ", ".join(befores))

    def test_wrong_or_missing_version_refused(self):
        self.assertNotEqual(build("9.9.9").returncode, 0)
        self.assertNotEqual(build("nonsense").returncode, 0)
        self.assertNotEqual(build().returncode, 0)

    def test_dirty_tree_is_refused_and_a_clean_one_builds(self):
        clone = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, clone, True)
        # --no-hardlinks: the temp folder may be on another filesystem than the checkout (tmpfs /tmp), where a hard link fails
        subprocess.run(["git", "clone", "-q", "--local", "--no-hardlinks", REPO, clone], check=True)
        with open(os.path.join(clone, "mctl", "README.md"), "a") as f:
            f.write("\nuncommitted edit\n")
        with open(os.path.join(clone, "mctl", "__init__.py")) as f:
            ver = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)
        r = build(ver, cwd=clone)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted", r.stderr)
        subprocess.run(["git", "checkout", "-q", "--", "mctl/README.md"], cwd=clone, check=True)
        self.assertEqual(build(ver, cwd=clone).returncode, 0)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
    def test_signing_round_trip(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        allowed = os.path.join(d, "allowed")
        with open(allowed, "w") as f:
            f.write('nxlx-release namespaces="nxlx-release" %s %s\n' % (fields[0], fields[1]))
        r = build(version(), "--key", key, "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        update.verify_signature(self.out, self.out + ".sig", allowed)
        self.assertEqual(sorted(os.listdir(os.path.join(REPO, "dist"))), ["nxlx-%s.tar.gz" % version(),
                         "nxlx-%s.tar.gz.sha256" % version(), "nxlx-%s.tar.gz.sig" % version()])
        with open(self.out + ".sig") as f:
            self.assertIn("BEGIN SSH SIGNATURE", f.read())
        # the namespace is nxlx-release: the same signature does not verify under the old one
        run = subprocess.run(["ssh-keygen", "-Y", "check-novalidate", "-n", "pvj-release", "-s", self.out + ".sig"],
                             stdin=open(self.out, "rb"), capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        run = subprocess.run(["ssh-keygen", "-Y", "check-novalidate", "-n", "nxlx-release", "-s", self.out + ".sig"],
                             stdin=open(self.out, "rb"), capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
