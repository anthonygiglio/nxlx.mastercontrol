# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from pvj import update

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "tools", "make-release.sh")


def version():
    with open(os.path.join(REPO, "pvj", "__init__.py")) as f:
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
        fonts = os.path.join(work, "pvj", "web", "fonts")
        self.assertEqual(sorted(os.listdir(fonts)), ["OFL-Archivo.txt", "OFL-JetBrainsMono.txt", "archivo-latin.06fa7831.woff2", "jetbrains-mono-500-latin.6c95bc2f.woff2"])
        for name in os.listdir(fonts):
            with open(os.path.join(fonts, name), "rb") as shipped, open(os.path.join(REPO, "pvj", "web", "fonts", name), "rb") as ours:
                self.assertEqual(shipped.read(), ours.read(), name)

    def test_wrong_or_missing_version_refused(self):
        self.assertNotEqual(build("9.9.9").returncode, 0)
        self.assertNotEqual(build("nonsense").returncode, 0)
        self.assertNotEqual(build().returncode, 0)

    def test_dirty_tree_is_refused_and_a_clean_one_builds(self):
        clone = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, clone, True)
        subprocess.run(["git", "clone", "-q", "--local", REPO, clone], check=True)
        with open(os.path.join(clone, "pvj", "README.md"), "a") as f:
            f.write("\nuncommitted edit\n")
        with open(os.path.join(clone, "pvj", "__init__.py")) as f:
            ver = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)
        r = build(ver, cwd=clone)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted", r.stderr)
        subprocess.run(["git", "checkout", "-q", "--", "pvj/README.md"], cwd=clone, check=True)
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
