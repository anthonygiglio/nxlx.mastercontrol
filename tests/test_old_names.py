# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The rename of pocketvj / pvj to NXLX and Mastercontrol (D135): the table, the apply script and the guard.

The guard (OldNamesGuard) enforces only the kinds whose rename has landed (LANDED in tools/rename-apply.py): it
fails when a file holds more names of those kinds than tools/rename-allow.txt allows it, or when a file that is not
listed holds any, in its text or its file name, so a landed rename cannot slide back. A kind that has not landed
is reported, not enforced, so unrelated pull requests that still use the old names are not blocked. Each rename
pull request adds its kind to LANDED and recounts the list with `python3 tools/rename-apply.py --write-allow`."""
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location("rename_apply", os.path.join(REPO, "tools", "rename-apply.py"))
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)
ROWS = ra.load(os.path.join(REPO, "tools", "rename-map.txt"))
MATCH = ra.Matcher(ROWS)


def apply(text, *kinds):
    return MATCH.apply(text, kinds or ra.KINDS)[0]


def tree(files):
    """A folder (not a git checkout) holding the files {relative path: text or bytes}."""
    root = tempfile.mkdtemp()
    for path, data in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


def snapshot(root):
    """{relative path: sha256} of everything in a folder."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            with open(os.path.join(d, n), "rb") as f:
                out[os.path.relpath(os.path.join(d, n), root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def run(*args):
    """(exit code, what it printed) of the script's main."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ra.main(list(args))
    return code, out.getvalue()


class OldNamesGuard(unittest.TestCase):
    def test_no_file_holds_more_old_names_than_the_allow_list_says(self):
        counts, allowed = ra.allow_counts(REPO), ra.read_allow(REPO)
        over = {p: "%d, allowed %d" % (c, allowed.get(p, 0)) for p, c in counts.items() if c > allowed.get(p, 0)}
        self.assertEqual(over, {}, "new old names of a kind whose rename has landed (%s). Use the new name from "
                         "tools/rename-map.txt; a new use of an old name is a choice for review: raise the file's "
                         "number in tools/rename-allow.txt and say why" % ", ".join(ra.LANDED))
        waiting = [k for k in ra.KINDS if k not in ra.LANDED]
        if waiting:
            old = ra.old_counts(REPO)
            sys.stderr.write("\nold-name guard: %d old-style names in %d files, kinds not landed yet (%s): reported, "
                             "not enforced\n" % (sum(old.values()), len(old), ", ".join(waiting)))

    def test_the_allow_list_is_well_formed(self):
        allowed = ra.read_allow(REPO)
        self.assertTrue(set(ra.LANDED) <= set(ra.KINDS), "LANDED names kinds of the table")
        if not ra.LANDED:
            self.assertEqual(allowed, {}, "nothing is enforced, so nothing is allowed yet")
        for own in ra.OWN:
            self.assertNotIn(own, allowed, "the rename's own files are not counted")
        with open(os.path.join(REPO, ra.ALLOW), encoding="utf-8") as f:
            listed = [line.split("\t", 1)[1].rstrip("\n") for line in f if line[:1].isdigit()]
        self.assertEqual(listed, sorted(listed), "kept sorted, so a recount shows as a small diff")

    def test_a_new_old_name_passes_until_its_kind_lands_and_fails_after(self):
        root = tree({"a.py": "x = 1\n", "b.md": "the pvj-web service\n", "pvj-web.txt": "nothing\n"})
        os.makedirs(os.path.join(root, "tools"))

        def over():
            counts, allowed = ra.allow_counts(root), ra.read_allow(root)
            return {p for p, c in counts.items() if c > allowed.get(p, 0)}

        with mock.patch.object(ra, "LANDED", ()):
            ra.write_allow(root)
            self.assertEqual(ra.read_allow(root), {})
            with open(os.path.join(root, "a.py"), "a") as f:
                f.write("PVJ_DIR = 1\nsee pvj-sound\n")
            with open(os.path.join(root, "pvj-new.py"), "w") as f:
                f.write("import pvj\n")
            self.assertEqual(over(), set(), "no kind has landed: reported, not enforced")
        with mock.patch.object(ra, "LANDED", ("system",)):
            self.assertEqual(over(), {"a.py", "b.md", "pvj-web.txt"}, "in the text and in the file name")
        with mock.patch.object(ra, "LANDED", ("env",)):
            self.assertEqual(over(), {"a.py"})
        with mock.patch.object(ra, "LANDED", ("system",)):
            ra.write_allow(root)
            self.assertEqual(ra.read_allow(root), {"a.py": 1, "b.md": 1, "pvj-web.txt": 1})
            self.assertEqual(over(), set())
            with open(os.path.join(root, "b.md"), "a") as f:
                f.write("and pvj-link\n")
            self.assertEqual(over(), {"b.md"}, "a landed kind cannot slide back")


class Table(unittest.TestCase):
    def test_every_row_turns_an_old_name_into_a_new_one(self):
        self.assertGreater(len(ROWS), 200)
        for row in ROWS:
            self.assertTrue(ra.OLD.search(row.old), row.old)
            if row.kind != ra.KEEP:
                self.assertIsNone(ra.OLD.search(row.new), "%s -> %s still holds an old name" % (row.old, row.new))
                self.assertTrue(ra.NEW.search(row.new), "%s -> %s is neither nxlx nor mctl" % (row.old, row.new))
        self.assertEqual({r.kind for r in ROWS}, set(ra.KINDS) | {ra.KEEP}, "every kind has rows")

    def test_the_owners_decisions(self):
        new = {r.old: (r.new, r.kind) for r in ROWS}
        self.assertEqual(new["pvj-dev"], ("nxlx-dev", "system"), "the login account is part of the rename")
        self.assertEqual(new["pvj-web"], ("nxlx-web", "system"), "nxlx for what an admin sees")
        self.assertEqual(new["/pvj"], ("/mctl", "protocol"), "mctl for the protocols")
        self.assertEqual(new["X-PVJ-Request"], ("X-MCTL-Request", "protocol"))
        self.assertEqual(new["pvj_token"], ("mctl_token", "protocol"))
        self.assertEqual(new["pvj/"], ("mctl/", "package"), "the code package is mctl")
        self.assertEqual(new["pvj-render"], ("mctl-render", "render"), "nxlx-render-node is the Max patch")
        self.assertEqual(new["PVJ_*"], ("NXLX_*", "env"))
        self.assertEqual(new["PocketVJ CP v3"], ("PocketVJ CP v3", "keep"), "the credit stays")
        # The shared group is nxlx-svc: node-01 already has a login user and group called nxlx.
        groups = [r for r in ROWS if re.search(r"group|Group=|gid|-aG|getgrnam|for g in|^:pvj$|--system pvj", r.old)]
        self.assertGreaterEqual(len(groups), 8)
        for r in groups:
            self.assertIn("nxlx-svc", r.new, "%s would not name the shared group nxlx-svc" % r.old)
        self.assertEqual(apply("chown pvj-web:pvj /var/lib/pvj"), "chown nxlx-web:nxlx-svc /var/lib/nxlx")

    def test_a_broken_table_is_refused_with_its_line(self):
        for text, said in [("a\tb\ttext\n", "four fields"), ("pvj-a\tnxlx-a\tnope\tn\n", "unknown kind"),
                           ("pvj-a\tnxlx-a\tsystem\tn\npvj-a\tnxlx-b\tsystem\tn\n", "line 2"),
                           ("pvj_*\tmctl_\trender\tn\n", "family"), ("pvj-a\tpvj-a\tsystem\tn\n", "keep row"),
                           ("PocketVJ\tMastercontrol\tkeep\tn\n", "keep row")]:
            root = tree({"map.txt": text})
            with self.assertRaises(ValueError) as e:
                ra.load(os.path.join(root, "map.txt"))
            self.assertIn(said, str(e.exception))


class Matching(unittest.TestCase):
    def test_word_boundaries(self):
        self.assertEqual(apply("systemctl restart pvj-ndisc"), "systemctl restart nxlx-ndisc")
        self.assertEqual(apply("pvj-ndi.service pvj-ndi-discovery"), "nxlx-ndi.service nxlx-ndi-discovery")
        self.assertEqual(apply('"xpvj-9.9.9.tar.gz", oldpvj'), '"xpvj-9.9.9.tar.gz", oldpvj', "inside a word: left")
        self.assertEqual(apply("root:pvj 0750 and pvj-ndi:pvj-ndi"), "root:nxlx-svc 0750 and nxlx-ndi:nxlx-ndi")
        self.assertEqual(apply("/opt/pvj/current/render/pvj-render"), "/opt/nxlx/current/render/mctl-render")

    def test_an_osc_address_is_not_a_path(self):
        self.assertEqual(apply('"/pvj/stop", "/pvj/state/clip"'), '"/mctl/stop", "/mctl/state/clip"')
        self.assertEqual(apply("see [OSC](../pvj/OSC.md)", "protocol"), "see [OSC](../pvj/OSC.md)", "a link is the package's")
        self.assertEqual(apply("see [OSC](../pvj/OSC.md)", "package"), "see [OSC](../mctl/OSC.md)")
        self.assertEqual(apply("--fonts @PVJ_DIR@/pvj/fonts", "protocol"), "--fonts @PVJ_DIR@/pvj/fonts")
        self.assertEqual(apply('self.etc_dir + "/pvj.env"'), 'self.etc_dir + "/nxlx.env"')
        self.assertEqual(apply("RuntimeDirectory=pvj/web"), "RuntimeDirectory=nxlx/web")

    def test_families_and_longest_first(self):
        self.assertEqual(apply("PVJ_RUNTIME_DIR=/run/pvj PVJ_"), "NXLX_RUNTIME_DIR=/run/nxlx NXLX_")
        self.assertEqual(apply("vec4 pvj_at(vec2 p); pvj_token"), "vec4 mctl_at(vec2 p); mctl_token")
        self.assertEqual(MATCH.apply("pvj_token", ("render",))[0], "pvj_token", "the cookie is protocol, not a shader name")

    def test_a_row_of_another_kind_holds_its_text(self):
        job = "  pvj-render:\n    name: pvj-render (Ubuntu)\n    run: make pvj-render\n"
        self.assertEqual(apply(job, "render"), "  pvj-render:\n    name: pvj-render (Ubuntu)\n    run: make mctl-render\n",
                         "the CI job names are required checks: they change last, with the ruleset")
        self.assertEqual(apply(job, "ci"), "  mctl-render:\n    name: mctl-render (Ubuntu)\n    run: make pvj-render\n")

    def test_the_credit_is_kept(self):
        line = "a fork of PocketVJ CP v3 (github.com/magdesign/PocketVJ-CP-v3); PocketVJ was the name"
        self.assertEqual(apply(line), "a fork of PocketVJ CP v3 (github.com/magdesign/PocketVJ-CP-v3); Mastercontrol was the name")

    def test_a_bare_pvj_is_reported_not_guessed(self):
        text = 'os.path.join(REPO, "pvj") and pvj-web'
        self.assertEqual(apply(text), 'os.path.join(REPO, "pvj") and nxlx-web')
        self.assertEqual(MATCH.uncovered(text), ["pvj"])

    def test_the_whole_text_and_the_quick_scan_agree(self):
        """The matcher only looks near each old-style name; a plain scan of the whole text must find the same."""
        text = "".join("%s %s(%s) systemctl x%s_y\n" % (r.lit, r.old, r.lit, r.lit) for r in ROWS)
        quick = [(s, e, r.old) for s, e, r in MATCH.matches(text)]
        slow = [(m.start(), m.end(), MATCH.rows[m.lastindex - 1].old) for m in MATCH.rx.finditer(text)]
        self.assertEqual(quick, slow)
        self.assertGreater(len(quick), 2 * len(ROWS))


class ApplyScript(unittest.TestCase):
    FILES = {"pvj/server.py": "from pvj import api\nCOOKIE = \"pvj_token\"\nos.environ.get(\"PVJ_PORT\")\n",
             "install/pvj-web.service": "User=pvj-web\nGroup=pvj\nExecStart=/opt/pvj/current/bin/pvj-web\n",
             "LICENSE.md": "PocketVJ by magdesign\n",
             "sync/old.sh": "pvj stuff\n",
             "font.woff2": b"\0pvj\0binary",
             "tools/systemctl.sh": "systemctl restart x\n"}

    def test_a_dry_run_writes_nothing(self):
        root = tree(self.FILES)
        before = snapshot(root)
        code, said = run("--root", root, "--map", os.path.join(REPO, ra.MAP))
        self.assertEqual(code, 0)
        self.assertEqual(snapshot(root), before)
        self.assertIn("dry run (nothing written)", said)
        self.assertIn("Would change 7 places", said)
        self.assertIn("rename 2 files", said)

    def test_write_applies_the_chosen_kinds_and_moves_the_files(self):
        root = tree(self.FILES)
        run("--root", root, "--map", os.path.join(REPO, ra.MAP), "--kind", "package", "--write")
        with open(os.path.join(root, "mctl", "server.py")) as f:
            self.assertEqual(f.read(), "from mctl import api\nCOOKIE = \"pvj_token\"\nos.environ.get(\"PVJ_PORT\")\n")
        self.assertFalse(os.path.exists(os.path.join(root, "pvj", "server.py")))
        self.assertTrue(os.path.exists(os.path.join(root, "install", "pvj-web.service")), "system is another kind")
        run("--root", root, "--map", os.path.join(REPO, ra.MAP), "--write")
        with open(os.path.join(root, "install", "nxlx-web.service")) as f:
            self.assertEqual(f.read(), "User=nxlx-web\nGroup=nxlx-svc\nExecStart=/opt/nxlx/current/bin/nxlx-web\n")
        with open(os.path.join(root, "LICENSE.md")) as f:
            self.assertEqual(f.read(), "PocketVJ by magdesign\n", "the credit is never rewritten")
        with open(os.path.join(root, "sync", "old.sh")) as f:
            self.assertEqual(f.read(), "pvj stuff\n", "the upstream original is never rewritten")
        with open(os.path.join(root, "font.woff2"), "rb") as f:
            self.assertEqual(f.read(), b"\0pvj\0binary", "a binary file is not rewritten")
        with open(os.path.join(root, "tools", "systemctl.sh")) as f:
            self.assertEqual(f.read(), "systemctl restart x\n")
        again = ra.plan(root, ROWS, ra.KINDS)
        self.assertEqual((again["files"], again["renames"]), ({}, {}), "a second run changes nothing")

    def test_a_rename_onto_an_existing_file_writes_nothing(self):
        root = tree({"pvj/a.py": "import pvj\n", "mctl/a.py": "x\n"})
        before = snapshot(root)
        with self.assertRaises(SystemExit):
            run("--root", root, "--map", os.path.join(REPO, ra.MAP), "--write")
        self.assertEqual(snapshot(root), before)

    def test_a_link_is_never_followed_rewritten_or_renamed(self):
        outside = tree({"secret.service": "User=pvj-web\n"})
        root = tree({"a.service": "User=pvj-web\n"})
        os.symlink(os.path.join(outside, "secret.service"), os.path.join(root, "b.service"))
        os.symlink(os.path.join(outside, "secret.service"), os.path.join(root, "pvj-web.service"))
        os.symlink(outside, os.path.join(root, "pvj"))
        code, said = run("--root", root, "--map", os.path.join(REPO, ra.MAP), "--write")
        self.assertEqual(code, 0)
        with open(os.path.join(outside, "secret.service")) as f:
            self.assertEqual(f.read(), "User=pvj-web\n", "a file outside the tree is never written")
        with open(os.path.join(root, "a.service")) as f:
            self.assertEqual(f.read(), "User=nxlx-web\n")
        for name in ("b.service", "pvj-web.service", "pvj"):
            self.assertTrue(os.path.islink(os.path.join(root, name)), "%s is left as it is" % name)
        self.assertFalse(os.path.lexists(os.path.join(root, "nxlx-web.service")))
        self.assertIn("Links, never followed, rewritten or renamed: 2", said)
        with mock.patch.object(ra, "LANDED", ("system",)):
            self.assertEqual(ra.allow_counts(root), {}, "the guard does not read through a link either")

    def test_a_new_name_already_in_the_tree_is_reported(self):
        root = tree({"a.sh": "id nxlx-web; systemctl status x\n", "b.service": "User=pvj-web\n"})
        report = ra.plan(root, ROWS, ("system",))
        self.assertEqual(report["clashes"], {"nxlx-web": 1}, "systemctl is not a use of mctl")


if __name__ == "__main__":
    unittest.main()
