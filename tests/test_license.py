# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Every file of the new code carries its licence; the licence text is verbatim."""
import fnmatch
import hashlib
import os
import re
import subprocess
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW_DIRS = ("pvj/", "bin/", "install/", "image/", "tests/", "tools/")
NEW_FILES = {"security.php", ".github/workflows/ci.yml", ".github/workflows/pvj.yml",
             ".github/workflows/image.yml", ".github/workflows/security-tests.yml"}
APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]            # whole names: the shader pack's files have spaces in theirs


def annotations():
    """[(licence, [path patterns])] of the REUSE.toml annotations, in the file's order."""
    with open(os.path.join(REPO, "REUSE.toml")) as f:
        text = f.read()
    out = []
    for block in text.split("[[annotations]]")[1:]:
        paths = re.findall(r'"([^"]+)"', re.search(r"path = \[(.*?)\]", block, re.S).group(1))
        out.append((re.search(r'SPDX-License-Identifier = "([^"]+)"', block).group(1), paths))
    return out


def covered_paths():
    """Path patterns listed in REUSE.toml annotations (of every licence)."""
    return [p for _, paths in annotations() for p in paths]


def matches(path, pattern):
    """REUSE's rule for a path pattern: * stays inside one folder, ** crosses folders."""
    rx = "".join(".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part) for part in re.split(r"(\*\*|\*)", pattern))
    return re.fullmatch(rx, path) is not None


ISF_PACK = "pvj/shaders.d/isf-files/"
ISF_LICENSE_SHA256 = "83e4dd21429a91fb7cea67a476032a9641425e5355df2e0f589a738b6ec9fd2c"      # upstream's LICENSE, as cloned


class LicenseTest(unittest.TestCase):
    def test_licence_text_is_the_official_apache_2_0(self):
        with open(os.path.join(REPO, "LICENSES", "Apache-2.0.txt"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), APACHE_SHA256)

    def test_every_new_file_is_licensed(self):
        patterns = covered_paths()
        missing = []
        for path in tracked():
            if not (path.startswith(NEW_DIRS) or path in NEW_FILES):
                continue
            if any(fnmatch.fnmatch(path, p.replace("**", "*")) for p in patterns):
                self.assertTrue(any(matches(path, p) for p in patterns), path + " is under a listed folder but not listed itself")
                continue
            with open(os.path.join(REPO, path), errors="replace") as f:
                head = f.read(600)
            if "SPDX-License-Identifier: Apache-2.0" not in head:
                missing.append(path)
        self.assertEqual(missing, [], "add an SPDX header, or list the file in REUSE.toml")

    def test_the_third_party_shader_pack_keeps_its_own_licence_and_is_declared(self):
        """Vidvox's ISF-Files generators (MIT) are somebody else's work: never labelled Apache-2.0 or as the project's,
        unchanged from upstream, with upstream's licence beside them, and every file named in the inventory."""
        files = sorted(p for p in tracked() if p.startswith(ISF_PACK))
        shaders = [p for p in files if p.endswith(".fs")]
        self.assertGreaterEqual(len(shaders), 8)
        # tools/make-release.sh hands the file list to tar split at white space: no shipped file may have any in its name
        self.assertEqual([p for p in tracked() if p.startswith(("pvj/", "bin/", "install/")) and re.search(r"\s", p)], [])
        self.assertTrue(all(re.fullmatch(r"isf-[a-z0-9-]+\.fs", os.path.basename(p)) for p in shaders), shaders)
        self.assertEqual(sorted(set(files) - set(shaders)), [ISF_PACK + "LICENSE", ISF_PACK + "SHA256SUMS"])
        by_licence = {}
        for licence, patterns in annotations():
            for p in files:
                if any(matches(p, pattern) for pattern in patterns):
                    by_licence.setdefault(p, []).append(licence)
        for p in shaders + [ISF_PACK + "LICENSE"]:
            self.assertEqual(by_licence.get(p), ["MIT"], p)
        self.assertTrue(os.path.isfile(os.path.join(REPO, "LICENSES", "MIT.txt")))
        with open(os.path.join(REPO, ISF_PACK, "LICENSE"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), ISF_LICENSE_SHA256)
        with open(os.path.join(REPO, ISF_PACK, "SHA256SUMS")) as f:
            sums = dict(reversed(line.rstrip("\n").split("  ", 1)) for line in f)
        self.assertEqual(sorted(sums), [os.path.basename(p) for p in shaders])
        with open(os.path.join(REPO, "THIRD_PARTY_LICENSES.md")) as f:
            inventory = f.read()
        self.assertIn("https://github.com/Vidvox/ISF-Files", inventory)
        self.assertRegex(inventory, r"\b[0-9a-f]{40}\b")                          # the upstream commit
        self.assertIn("Permission is hereby granted, free of charge", inventory)
        for p in shaders:
            name = os.path.basename(p)
            with open(os.path.join(REPO, p), "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), sums[name], name + " differs from upstream")
            self.assertNotIn(b"Apache-2.0", data, name)
            self.assertNotIn(b"NXLX", data, name)
            self.assertIn("`%s`" % name, inventory, name + " is not in THIRD_PARTY_LICENSES.md")

    def test_no_compiled_or_cache_files_are_tracked(self):
        junk = [p for p in tracked() if "__pycache__" in p or p.endswith((".pyc", ".DS_Store"))
                and not p.startswith("sync/")]
        self.assertEqual(junk, [], "these would also be packed into release bundles")

    def test_legacy_files_are_not_relabelled(self):
        for path in ("backend.php", "index.html", "submit_opacity.php", "LICENSE.md"):
            with open(os.path.join(REPO, path), errors="replace") as f:
                self.assertNotIn("Apache-2.0", f.read(), path + " is upstream code")


if __name__ == "__main__":
    unittest.main()
