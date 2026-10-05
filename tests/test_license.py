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


def annotations(holders=False):
    """[(licence, [path patterns])] of the REUSE.toml annotations, in the file's order; with `holders`, the text of
    each annotation's copyright lines as a third item."""
    with open(os.path.join(REPO, "REUSE.toml")) as f:
        text = f.read()
    out = []
    for block in text.split("[[annotations]]")[1:]:
        paths = re.findall(r'"([^"]+)"', re.search(r"path = \[(.*?)\]", block, re.S).group(1))
        licence = re.search(r'SPDX-License-Identifier = "([^"]+)"', block).group(1)
        who = re.search(r"SPDX-FileCopyrightText = (\[.*?\]|\"[^\n]*\")", block, re.S).group(1)
        out.append((licence, paths, who) if holders else (licence, paths))
    return out


def covered_paths():
    """Path patterns listed in REUSE.toml annotations (of every licence)."""
    return [p for _, paths in annotations() for p in paths]


def matches(path, pattern):
    """REUSE's rule for a path pattern: * stays inside one folder, ** crosses folders."""
    rx = "".join(".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part) for part in re.split(r"(\*\*|\*)", pattern))
    return re.fullmatch(rx, path) is not None


FONTS = "pvj/web/fonts/"
# The two fonts as built from github.com/google/fonts at commit 9710da1e (THIRD_PARTY_LICENSES.md says how), and each
# family's OFL.txt as fetched there on 2026-10-04.
FONT_SUMS = {"archivo-latin.woff2": "06fa7831060c673ef6e553b846635fb1e7eaf558e717ddfeb7c0a24fd9280529",
             "jetbrains-mono-500-latin.woff2": "6c95bc2faff7653603df02e7dca2fef5341d7ba49ebe8c952907f8ded2c0eb20",
             "OFL-Archivo.txt": "108b4e57c9c796d3d38d0428ca7ee39de47ad93187302718d9b2d8864b9b716b",
             "OFL-JetBrainsMono.txt": "b2fe5e8987594e9ffd1d2ca52a2f5d73eb8335243893c5d6254b5ad69269591d"}
OFL_SHA256 = "8eea8287e5876b539670cadb82e99f9a7afddec6f6730811be1daf25d2e9bcfd"      # SPDX's text of OFL-1.1 (license-list-data)

ISF_PACK = "pvj/shaders.d/isf-files/"
ISF_LICENSE_SHA256 = "83e4dd21429a91fb7cea67a476032a9641425e5355df2e0f589a738b6ec9fd2c"      # upstream's LICENSE, as cloned
# github.com/ashima/webgl-noise LICENSE, as fetched on 2026-10-04: the notice for the simplex noise in two of the files
WEBGL_NOISE_LICENSE_SHA256 = "bdafce1bb01517c9ae6c4f3620c01340790b5e9d039ae9e356347d1174250916"
# The checksum list, made from the upstream clone at commit 395072d4 and checked against it then. Pinned here, so the
# files and the list cannot be changed together unnoticed.
ISF_SUMS_SHA256 = "38066ce8878457610d44950a371fca5f2a78bac89132c16b94169a1535d00a4e"
ISF_FILES = ["isf-color-bars.fs", "isf-corner-colors.fs", "isf-linear-gradient.fs", "isf-radial-gradient.fs", "isf-ridgelines.fs",
             "isf-simplex-noise.fs", "isf-sine-warp-gradient.fs"]


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
        self.assertEqual([os.path.basename(p) for p in shaders], ISF_FILES)       # the pack, by name: a change is a decision
        # tools/make-release.sh hands the file list to tar split at white space: no shipped file may have any in its name
        self.assertEqual([p for p in tracked() if p.startswith(("pvj/", "bin/", "install/")) and re.search(r"\s", p)], [])
        self.assertTrue(all(re.fullmatch(r"isf-[a-z0-9-]+\.fs", os.path.basename(p)) for p in shaders), shaders)
        # The notices ship with the files: a release holds only pvj/, bin/ and install/, so a notice that is only in
        # THIRD_PARTY_LICENSES.md at the top of the repository never reaches a box.
        notices = [ISF_PACK + "LICENSE", ISF_PACK + "LICENSE.webgl-noise"]
        self.assertEqual(sorted(set(files) - set(shaders)), notices + [ISF_PACK + "SHA256SUMS"])
        by_licence = {}
        for licence, patterns, holders in annotations(holders=True):
            for p in files:
                if any(matches(p, pattern) for pattern in patterns):
                    by_licence.setdefault(p, []).append((licence, holders))
        noise = [ISF_PACK + "isf-ridgelines.fs", ISF_PACK + "isf-simplex-noise.fs", ISF_PACK + "LICENSE.webgl-noise"]
        for p in shaders + notices:
            self.assertEqual({licence for licence, _ in by_licence.get(p, [])}, {"MIT"}, p)
            last = by_licence[p][-1][1]                                   # the last annotation that matches is the one that counts
            self.assertIn("ISF-Files", last, p)
            self.assertEqual("Ashima Arts" in last and "Stefan Gustavson" in last, p in noise, p)
        self.assertTrue(os.path.isfile(os.path.join(REPO, "LICENSES", "MIT.txt")))
        for name, digest in (("LICENSE", ISF_LICENSE_SHA256), ("LICENSE.webgl-noise", WEBGL_NOISE_LICENSE_SHA256),
                             ("SHA256SUMS", ISF_SUMS_SHA256)):
            with open(os.path.join(REPO, ISF_PACK, name), "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
            if name != "SHA256SUMS":
                self.assertIn(b"Permission is hereby granted, free of charge", data, name)
        with open(os.path.join(REPO, ISF_PACK, "LICENSE.webgl-noise")) as f:
            self.assertIn("Copyright (C) 2011 by Ashima Arts", f.read())
        for p in noise[:2]:                                                 # the files that need that notice are these two
            with open(os.path.join(REPO, p), encoding="utf-8") as f:
                self.assertIn("Ashima", f.read(), p)
        with open(os.path.join(REPO, ISF_PACK, "SHA256SUMS")) as f:
            sums = dict(reversed(line.rstrip("\n").split("  ", 1)) for line in f)
        self.assertEqual(sorted(sums), [os.path.basename(p) for p in shaders])
        with open(os.path.join(REPO, "THIRD_PARTY_LICENSES.md")) as f:
            inventory = f.read()
        self.assertIn("https://github.com/Vidvox/ISF-Files", inventory)
        self.assertRegex(inventory, r"\b[0-9a-f]{40}\b")                          # the upstream commit
        self.assertIn("Permission is hereby granted, free of charge", inventory)
        self.assertIn("Copyright (C) 2011 by Ashima Arts (Simplex noise)", inventory)
        for p in shaders:
            name = os.path.basename(p)
            with open(os.path.join(REPO, p), "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), sums[name], name + " differs from upstream")
            self.assertNotIn(b"Apache-2.0", data, name)
            self.assertNotIn(b"NXLX", data, name)
            self.assertIn("`%s`" % name, inventory, name + " is not in THIRD_PARTY_LICENSES.md")

    def test_the_fonts_keep_their_own_licence_and_are_declared(self):
        """Archivo and JetBrains Mono (SIL OFL 1.1) are somebody else's work: never labelled Apache-2.0, each with its
        own OFL.txt beside it (a release holds only pvj/, bin/ and install/), pinned by checksum and in the inventory."""
        files = sorted(p for p in tracked() if p.startswith(FONTS))
        self.assertEqual(files, sorted(FONTS + n for n in FONT_SUMS))
        with open(os.path.join(REPO, "THIRD_PARTY_LICENSES.md")) as f:
            inventory = f.read()
        for name, digest in FONT_SUMS.items():
            with open(os.path.join(REPO, FONTS, name), "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
            self.assertIn("`%s`" % name, inventory, name)
            self.assertIn(digest, inventory, name)
            licences = [(licence, who) for licence, patterns, who in annotations(holders=True) if any(matches(FONTS + name, p) for p in patterns)]
            self.assertEqual([licence for licence, _ in licences], ["OFL-1.1"], name)
            self.assertIn("JetBrains Mono Project Authors" if "jetbrains" in name.lower() else "Archivo Project Authors", licences[0][1], name)
            if name.endswith(".txt"):
                self.assertIn(b"SIL OPEN FONT LICENSE Version 1.1", data, name)
                self.assertNotIn(b"with Reserved Font Name", data, name)      # a subset may keep the family's name
            else:
                self.assertEqual(data[:4], b"wOF2", name)
        self.assertIn("https://github.com/google/fonts", inventory)
        self.assertIn("9710da1eacb3be272583c3224dcb70f9da6eadbb", inventory)
        self.assertIn("Modified Versions", inventory)
        with open(os.path.join(REPO, "LICENSES", "OFL-1.1.txt"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), OFL_SHA256)

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
