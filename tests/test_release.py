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
        self.out = os.path.join(REPO, "dist", "pvj-%s.tar.gz" % version())
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

    def test_the_bundle_passes_the_updater_installed_before_the_rename_and_the_stubs_are_never_installed(self):
        """For one release the bundle holds pvj/__init__.py and pvj/settings.py (two stub lines), so that the updater
        already on a node, which reads only those, takes it. That updater is run here from a pinned commit of master."""
        before = "1057087dcc9939af9044f68d03ef3ea72a322116"       # master with release 0.1.1, before the package was renamed
        r = build(version(), "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        update.safe_extract(self.out, work)
        with open(os.path.join(work, "pvj", "__init__.py")) as f:
            self.assertIn('__version__ = "%s"' % version(), f.read())
        with open(os.path.join(work, "pvj", "settings.py")) as f:
            stub = f.read()
        with open(os.path.join(REPO, "mctl", "settings.py")) as f:
            self.assertIn(re.search(r"^SCHEMA = \d+$", f.read(), re.M).group(0), stub)
        self.assertEqual(sorted(os.listdir(os.path.join(work, "pvj"))), ["__init__.py", "settings.py"], "only the two stubs")
        info = update.inspect_bundle(work)                         # the new updater reads mctl/ and ignores the stubs
        self.assertEqual(info["version"], version())
        # the installer copies mctl, bin and render into the release: the stubs are not installed
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        done = subprocess.run([os.path.join(work, "install", "install.sh"), "--stage", root, "--user", "someone"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        release = os.path.join(root, "opt", "pvj", "releases", version())
        self.assertTrue(os.path.isfile(os.path.join(release, "mctl", "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(release, "pvj")), "a stub was installed")
        # the updater of the release before: master's own inspect_bundle, on this bundle
        try:
            tar = subprocess.run(["git", "-C", REPO, "archive", before, "pvj"], check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the release before from" % before)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        script = "import json, sys\nfrom pvj import update\nprint(json.dumps(update.inspect_bundle(sys.argv[1])))\n"
        run = subprocess.run([sys.executable, "-c", script, work], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout.strip().splitlines()[-1])["version"], version())

    def test_wrong_or_missing_version_refused(self):
        self.assertNotEqual(build("9.9.9").returncode, 0)
        self.assertNotEqual(build("nonsense").returncode, 0)
        self.assertNotEqual(build().returncode, 0)

    def test_dirty_tree_is_refused_and_a_clean_one_builds(self):
        clone = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, clone, True)
        subprocess.run(["git", "clone", "-q", "--local", REPO, clone], check=True)
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
            f.write('pvj-release namespaces="pvj-release" %s %s\n' % (fields[0], fields[1]))
        r = build(version(), "--key", key, "--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        update.verify_signature(self.out, self.out + ".sig", allowed)


if __name__ == "__main__":
    unittest.main()
