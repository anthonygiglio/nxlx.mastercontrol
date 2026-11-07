# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The project log in one new file per pull request (D107, tools/project-log.py): every refusal of the check fires,
on a git repository made here, and the log reads as one document, with nothing of the old files lost."""
import datetime
import importlib.util
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import tests                                    # noqa: F401  the temp folder and the lock checker (tests/__init__.py)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("project_log", os.path.join(REPO, "tools", "project-log.py"))
plog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plog)
TODAY = datetime.date(2026, 10, 24)
HEAD = plog.SPDX % 2026
POINTER = plog.START + "\nNew entries go in their own files (D107).\n" + plog.END + "\n\n"
OLD = {
    "JOURNAL.md": "# Journal\n\nNewest entry first.\n\n## 2026-10-02, the second\n\n- two\n\n## 2026-10-01, the first\n\n- one\n",
    "LESSONS.md": "# Lessons learned\n\nAppend new lessons at the bottom.\n\n- **An old lesson.** Do better.\n",
    "DECISIONS.md": "# Decisions\n\nNewest at the bottom.\n\n## D1. The first (2026-01-01)\nWhy: one.\n\n"
                    "## D2. The second (2026-01-02)\nWhy: two.\n",
}


def with_pointer(text):
    """The old file with the pointer after its first paragraph, as this pull request added it to the real three."""
    lines = text.split("\n")
    return "\n".join(lines[:6]) + "\n" + POINTER + "\n".join(lines[6:])


def git(root, *args):
    return subprocess.run(("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "commit.gpgsign=false", "-c", "init.defaultBranch=master") + args, cwd=root, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60).stdout


class Repo(unittest.TestCase):
    """A repository whose master has the three old files (with the pointer unless told otherwise) and one journal
    file, and a branch "work" checked out from it."""
    POINTERS = True

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q")
        for name, text in OLD.items():
            self.write(name, with_pointer(HEAD + text) if self.POINTERS else HEAD + text, head=False)
        if self.POINTERS:
            self.write("journal/2026-10-23-earlier.md", "## 2026-10-23, an earlier pull request\n\n- done\n")
            self.write("decisions/D3-earlier.md", "## D3. An earlier decision (2026-10-23)\nWhy: three.\n")
        self.commit("master")
        git(self.root, "checkout", "-q", "-b", "work")

    def path(self, rel):
        return os.path.join(self.root, "project-log", rel)

    def write(self, rel, text, head=True):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8", newline="\n") as f:
            f.write((HEAD if head else "") + text)

    def text(self, rel):
        with open(self.path(rel), encoding="utf-8") as f:
            return f.read()

    def commit(self, message="work"):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)

    def journal(self):
        self.write("journal/2026-10-24-work.md", "## 2026-10-24, this pull request\n\n- built\n")

    def problems(self, no_journal_ok=False, base="master"):
        return plog.tree_problems(self.root, TODAY) + plog.diff_problems(self.root, base, no_journal_ok)

    def assertRefused(self, words, **kw):
        bad = self.problems(**kw)
        self.assertTrue(any(words in b for b in bad), "no refusal with %r in %r" % (words, bad))
        return bad

    def assertGood(self, **kw):
        self.assertEqual(self.problems(**kw), [])


class GoodPullRequest(Repo):
    def test_a_pull_request_of_new_files_passes_committed_or_not(self):
        self.journal()
        self.write("lessons/2026-10-24-work.md", "## Something learned (2026-10-24)\n\nWhat we do now.\n")
        self.write("decisions/D4-work.md", "## D4. A new decision (2026-10-24)\nWhy: four.\n\n### Details\n")
        self.write("decisions/D1-addendum-2026-10-24-work.md", "## Addendum to D1 (2026-10-24): more\n\nText.\n")
        self.write("decisions/D3-addendum-2026-10-24-work.md", "## Addendum to D3 (2026-10-24): more\n\nText.\n")
        self.assertGood()                       # not committed yet: what a writer checks before committing
        self.commit()
        self.assertGood()

    def test_a_journal_file_with_a_review_entry_above_the_first(self):
        self.write("journal/2026-10-24-work.md", "## 2026-10-26, the review, fixed\n\n- fixed\n\n"
                                                 "## 2026-10-24, the work\n\n- built\n")
        self.assertGood()

    def test_the_command_says_so_and_exits_0(self):
        self.journal()
        out = io.StringIO()
        self.assertEqual(plog.main(["p", "check", "--base", "master"], root=self.root, today=TODAY, out=out), 0)
        self.assertIn("in order", out.getvalue())


class FrozenFiles(Repo):
    def test_an_edit_to_any_of_the_three_old_files_is_refused(self):
        for name in OLD:
            with self.subTest(name):
                self.journal()
                before = self.text(name)
                self.write(name, before.rstrip("\n") + " (edited)\n", head=False)
                self.assertRefused("%s was edited. Since D107 it is history" % name)
                self.write(name, before + "\n- one more line at the bottom\n", head=False)
                self.assertRefused("%s was edited." % name)
                self.write(name, before, head=False)
                self.assertGood()

    def test_deleting_one_is_refused(self):
        self.journal()
        os.remove(self.path("LESSONS.md"))
        self.assertRefused("project-log/LESSONS.md was deleted")

    def test_the_pointer_cannot_be_taken_out_or_changed(self):
        self.journal()
        text = self.text("JOURNAL.md")
        self.write("JOURNAL.md", text.replace(POINTER, ""), head=False)
        self.assertRefused("JOURNAL.md was edited")
        self.write("JOURNAL.md", text.replace("New entries go", "Entries go"), head=False)
        self.assertRefused("JOURNAL.md was edited")


class ThePointerOnce(Repo):
    POINTERS = False                            # master as it was before D107

    def test_adding_the_pointer_and_nothing_else_passes(self):
        self.journal()
        for name, text in OLD.items():
            self.write(name, with_pointer(HEAD + text), head=False)
        self.assertGood()

    def test_the_pointer_with_another_change_is_refused(self):
        self.journal()
        self.write("JOURNAL.md", with_pointer(HEAD + OLD["JOURNAL.md"]).replace("- two", "- 2"), head=False)
        self.assertRefused("Only the pointer at its top may be added to it, once")

    def test_two_pointers_are_refused(self):
        self.journal()
        self.write("DECISIONS.md", with_pointer(with_pointer(HEAD + OLD["DECISIONS.md"])), head=False)
        self.assertRefused("DECISIONS.md was edited. Only the pointer")

    def test_a_plain_entry_on_top_as_before_is_refused(self):
        self.journal()
        self.write("JOURNAL.md", HEAD + OLD["JOURNAL.md"].replace("## 2026-10-02", "## 2026-10-24, new\n\n- n\n\n"
                                                                  "## 2026-10-02"), head=False)
        self.assertRefused("JOURNAL.md was edited. Only the pointer")


class EarlierFiles(Repo):
    def test_an_earlier_pull_requests_file_is_not_edited(self):
        self.journal()
        self.write("journal/2026-10-23-earlier.md", "## 2026-10-23, an earlier pull request\n\n- changed\n")
        self.assertRefused("project-log/journal/2026-10-23-earlier.md was edited. It was written by an earlier")
        self.write("decisions/D3-earlier.md", "## D3. An earlier decision, changed (2026-10-23)\nWhy.\n")
        self.assertRefused("project-log/decisions/D3-earlier.md was edited")

    def test_or_deleted(self):
        self.journal()
        os.remove(self.path("journal/2026-10-23-earlier.md"))
        self.assertRefused("project-log/journal/2026-10-23-earlier.md was deleted")


class Names(Repo):
    def test_bad_names_in_journal_and_lessons_are_refused(self):
        self.journal()
        for folder in ("journal", "lessons"):
            for name in ("notes.md", "2026-10-24_work.md", "2026-10-24-Work.md", "2026-10-24-work.txt",
                         "24-10-2026-work.md", "2026-10-24-.md", "2026-10-24-work--two.md"):
                with self.subTest(folder=folder, name=name):
                    self.write("%s/%s" % (folder, name), "## 2026-10-24, x\n")
                    self.assertRefused("%s/%s: a bad name" % (folder, name))
                    os.remove(self.path("%s/%s" % (folder, name)))
        self.assertGood()

    def test_a_date_that_is_not_real_or_in_the_future_is_refused(self):
        self.journal()
        self.write("lessons/2026-02-30-work.md", "## A lesson (2026)\n")
        self.assertRefused("2026-02-30 is not a real date")
        os.remove(self.path("lessons/2026-02-30-work.md"))
        self.write("lessons/2026-10-27-work.md", "## A lesson (2026)\n")
        self.assertRefused("2026-10-27 is in the future")
        os.remove(self.path("lessons/2026-10-27-work.md"))
        self.write("lessons/2026-10-25-work.md", "## A lesson (2026)\n")     # a day ahead of UTC is allowed
        self.assertGood()

    def test_bad_decision_names_are_refused(self):
        self.journal()
        for name in ("D4.md", "d4-work.md", "D04-work.md", "D4_work.md", "D-work.md", "D4-addendum-work.md",
                     "D1-addendum-2026-10-24.md", "D1-addendum-24-10-2026-work.md", "decision-4.md"):
            with self.subTest(name):
                self.write("decisions/" + name, "## D4. x\n")
                self.assertRefused("decisions/%s: a bad name" % name)
                os.remove(self.path("decisions/" + name))
        self.write("decisions/D1-addendum-2026-13-01-work.md", "## Addendum to D1 (x)\n")
        self.assertRefused("2026-13-01 is not a real date")

    def test_a_folder_inside_is_refused(self):
        self.journal()
        os.makedirs(self.path("journal/more"))
        self.write("journal/more/2026-10-24-x.md", "## 2026-10-24, x\n")
        self.assertRefused("only files go in project-log/journal/")


class Contents(Repo):
    def test_no_spdx_header_is_refused_with_the_two_lines_to_add(self):
        self.write("journal/2026-10-24-work.md", "## 2026-10-24, this pull request\n", head=False)
        bad = self.assertRefused("journal/2026-10-24-work.md: it has no SPDX header")
        self.assertTrue(any(HEAD in b for b in bad))

    def test_the_templates_placeholder_left_in_is_refused(self):
        self.write("journal/2026-10-24-work.md", "## 2026-10-24, %s\n" % plog.TEMPLATE)
        self.assertRefused("it still has WRITE-HERE from the template")

    def test_journal_headings_must_be_dated_entries_newest_first_and_one_of_the_names_date(self):
        self.write("journal/2026-10-24-work.md", "Some text and no heading.\n")
        self.assertRefused("it has no entry")
        self.write("journal/2026-10-24-work.md", "## Work on the twenty-fourth\n")
        self.assertRefused("the heading `## Work on the twenty-fourth` is not a journal entry's")
        self.write("journal/2026-10-24-work.md", "## 2026-10-24: work\n")
        self.assertRefused("is not a journal entry's")
        self.write("journal/2026-10-24-work.md", "## 2026-10-22, work\n")
        self.assertRefused("no entry in it is dated 2026-10-24")
        self.write("journal/2026-10-24-work.md", "## 2026-10-24, first\n\n## 2026-10-25, then\n")
        self.assertRefused("its entries are not newest first")

    def test_a_lesson_needs_a_heading(self):
        self.journal()
        self.write("lessons/2026-10-24-work.md", "- **A bullet as in the old file.**\n")
        self.assertRefused("lessons/2026-10-24-work.md: it has no entry")

    def test_a_decisions_heading_carries_the_number_of_its_name(self):
        self.journal()
        for text in ("## D5. A decision\n", "## The fourth\n", "Why: no heading.\n", "## D4 without the dot\n"):
            with self.subTest(text):
                self.write("decisions/D4-work.md", text)
                self.assertRefused("its first heading must be `## D4. What was decided (date)`")
        self.write("decisions/D4-work.md", "## D4. One (2026-10-24)\n\n## D5. Another (2026-10-24)\n")
        self.assertRefused("one decision a file. `## D5. Another (2026-10-24)` goes in a file of its own")

    def test_an_addendums_heading_names_its_decision_and_the_decision_exists(self):
        self.journal()
        self.write("decisions/D1-addendum-2026-10-24-work.md", "## Addendum to D2 (2026-10-24): x\n")
        self.assertRefused("its first heading must be `## Addendum to D1 (date): what`")
        os.remove(self.path("decisions/D1-addendum-2026-10-24-work.md"))
        self.write("decisions/D9-addendum-2026-10-24-work.md", "## Addendum to D9 (2026-10-24): x\n")
        self.assertRefused("it is an addendum to D9, and there is no D9")


class Numbers(Repo):
    def test_a_number_in_decisions_md_is_taken(self):
        self.journal()
        self.write("decisions/D2-again.md", "## D2. Again (2026-10-24)\n")
        bad = self.assertRefused("D2 is already taken on the base branch (project-log/DECISIONS.md). Take the next free "
                                 "number, D4")
        self.assertTrue(any("D2 is used more than once" in b for b in bad))

    def test_a_number_master_took_after_this_branch_left_it_is_refused(self):
        # Another pull request merged D4 while this branch was open, and this branch took D4 too and has not merged
        # master: its own tree has one D4, and only master tells.
        git(self.root, "checkout", "-q", "master")
        self.write("decisions/D4-theirs.md", "## D4. Theirs (2026-10-24)\n")
        self.commit("theirs")
        git(self.root, "checkout", "-q", "work")
        self.journal()
        self.write("decisions/D4-mine.md", "## D4. Mine (2026-10-24)\n")
        self.assertEqual(plog.tree_problems(self.root, TODAY), [])
        bad = plog.diff_problems(self.root, "master")
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("project-log/decisions/D4-mine.md: D4 is already taken on the base branch "
                      "(project-log/decisions/D4-theirs.md). Take the next free number, D5", bad[0])

    def test_two_files_of_one_number_are_refused_on_the_tree_alone(self):
        self.write("decisions/D4-one.md", "## D4. One (2026-10-24)\n")
        self.write("decisions/D4-two.md", "## D4. Two (2026-10-24)\n")
        bad = plog.tree_problems(self.root, TODAY)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("D4 is used more than once: project-log/decisions/D4-one.md and project-log/decisions/"
                      "D4-two.md", bad[0])
        self.assertIn("the next free number, D5", bad[0])

    def test_next_counts_the_tree_and_the_base(self):
        self.assertEqual(plog.next_number(self.root), 4)
        git(self.root, "checkout", "-q", "master")
        self.write("decisions/D9-theirs.md", "## D9. Theirs (2026-10-24)\n")
        self.commit("theirs")
        git(self.root, "checkout", "-q", "work")
        self.assertEqual(plog.next_number(self.root), 4)
        self.assertEqual(plog.next_number(self.root, "master"), 10)
        self.assertEqual(plog.next_number(self.root, "no-such-ref"), 4)


class JournalRequired(Repo):
    def test_no_journal_file_is_refused_unless_the_label_says_so(self):
        self.write("lessons/2026-10-24-work.md", "## A lesson (2026-10-24)\n")
        self.assertRefused("This pull request adds no journal entry")
        self.assertGood(no_journal_ok=True)

    def test_a_base_git_does_not_know_is_said_in_words(self):
        self.journal()
        self.assertRefused("git does not know that commit here", base="origin/nowhere")

    def test_ci_gets_annotations_and_exit_1(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            code = plog.main(["p", "check", "--base", "master"], root=self.root, today=TODAY, out=out)
        self.assertEqual(code, 1)
        self.assertRegex(out.getvalue(), r"(?m)^::error::This pull request adds no journal entry")
        out = io.StringIO()
        self.assertEqual(plog.main(["p", "check", "--base", "master", "--no-journal-ok"], root=self.root,
                                   today=TODAY, out=out), 0)


class Reading(Repo):
    def test_the_journal_newest_first_the_files_then_the_old_file(self):
        self.write("journal/2026-10-20-a.md", "## 2026-10-21, a's review\n\n- r\n\n## 2026-10-20, a\n\n- a\n")
        self.commit("a")
        self.write("journal/2026-10-22-z.md", "## 2026-10-22, z, committed first that day\n\n- z\n")
        self.commit("z")
        self.write("journal/2026-10-22-b.md", "## 2026-10-22, b, not committed yet\n\n- b\n")
        out = io.StringIO()
        self.assertEqual(plog.main(["p", "journal"], root=self.root, out=out), 0)
        heads = re.findall(r"(?m)^## (.*)$", out.getvalue())
        self.assertEqual(heads, ["2026-10-23, an earlier pull request", "2026-10-22, b, not committed yet",
                                 "2026-10-22, z, committed first that day", "2026-10-21, a's review", "2026-10-20, a",
                                 "2026-10-02, the second", "2026-10-01, the first"])
        self.assertNotIn("SPDX", out.getvalue())
        self.assertNotIn(plog.START, out.getvalue())

    def test_the_lessons_old_file_first_then_the_files_oldest_first(self):
        self.write("lessons/2026-10-24-b.md", "## B (2026-10-24)\n")
        self.write("lessons/2026-10-20-a.md", "## A (2026-10-20)\n")
        out = io.StringIO()
        plog.main(["p", "lessons"], root=self.root, out=out)
        text = out.getvalue()
        self.assertLess(text.index("An old lesson"), text.index("## A (2026-10-20)"))
        self.assertLess(text.index("## A (2026-10-20)"), text.index("## B (2026-10-24)"))

    def test_decisions_with_their_addenda_joined(self):
        self.write("decisions/D1-addendum-2026-10-25-later.md", "## Addendum to D1 (2026-10-25): later\n")
        self.write("decisions/D1-addendum-2026-10-24-sooner.md", "## Addendum to D1 (2026-10-24): sooner\n")
        self.write("decisions/D3-addendum-2026-10-24-x.md", "## Addendum to D3 (2026-10-24): on a new one\n")
        out = io.StringIO()
        self.assertEqual(plog.main(["p", "decisions"], root=self.root, out=out), 0)
        heads = re.findall(r"(?m)^## (.*)$", out.getvalue())
        self.assertEqual(heads, ["D1. The first (2026-01-01)", "Addendum to D1 (2026-10-24): sooner",
                                 "Addendum to D1 (2026-10-25): later", "D2. The second (2026-01-02)",
                                 "D3. An earlier decision (2026-10-23)", "Addendum to D3 (2026-10-24): on a new one"])
        self.assertIn("Newest at the bottom.", out.getvalue())
        out = io.StringIO()
        self.assertEqual(plog.main(["p", "decisions", "D3"], root=self.root, out=out), 0)
        self.assertEqual(re.findall(r"(?m)^## (.*)$", out.getvalue()),
                         ["D3. An earlier decision (2026-10-23)", "Addendum to D3 (2026-10-24): on a new one"])
        err = io.StringIO()
        self.assertEqual(plog.main(["p", "decisions", "D3", "D8"], root=self.root, out=io.StringIO(), err=err), 1)
        self.assertIn("no such decision: D8", err.getvalue())


class Starting(Repo):
    def test_new_makes_each_kind_and_the_check_wants_it_written(self):
        out = io.StringIO()
        for args in (["journal"], ["lesson", "a-lesson"], ["decision", "a-choice"], ["addendum", "D2"]):
            self.assertEqual(plog.main(["p", "new"] + args, root=self.root, today=TODAY, out=out), 0, args)
        made = out.getvalue().split()
        for rel in ("project-log/journal/2026-10-24-work.md", "project-log/lessons/2026-10-24-a-lesson.md",
                    "project-log/decisions/D4-a-choice.md", "project-log/decisions/D2-addendum-2026-10-24-work.md"):
            self.assertIn(rel, made)
        bad = self.problems()
        self.assertEqual(sorted(b.split(":")[0] for b in bad),
                         sorted(r for r in made if r.startswith("project-log/")), bad)
        self.assertTrue(all("WRITE-HERE from the template" in b for b in bad), bad)
        for rel in (r for r in made if r.startswith("project-log/")):
            with open(os.path.join(self.root, rel), encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as f:
                f.write(text.replace(plog.TEMPLATE, "written"))
        self.assertGood()

    def test_new_refuses_a_bad_name_an_unknown_decision_and_a_file_that_is_there(self):
        for args, words in ((["journal", "Bad Name"], "a name is small letters"),
                            (["decision", "addendum-x"], "does not start with addendum"),
                            (["decision"], "usage: new decision NAME"),
                            (["addendum", "D9"], "there is no D9"),
                            (["addendum", "4"], "usage: new addendum DN"),
                            (["poem"], "new: journal, lesson, decision or addendum")):
            err = io.StringIO()
            self.assertEqual(plog.main(["p", "new"] + args, root=self.root, today=TODAY, out=io.StringIO(),
                                       err=err), 2, args)
            self.assertIn(words, err.getvalue())
        plog.main(["p", "new", "journal"], root=self.root, today=TODAY, out=io.StringIO())
        err = io.StringIO()
        self.assertEqual(plog.main(["p", "new", "journal"], root=self.root, today=TODAY, out=io.StringIO(), err=err), 2)
        self.assertIn("is there already: this branch started it, so add to it", err.getvalue())


class Renumbering(Repo):
    """Two pull requests took D4 and both merged: master has D4 twice, and the fix moves one to D5."""

    def setUp(self):
        super().setUp()
        git(self.root, "checkout", "-q", "master")
        self.write("decisions/D4-one.md", "## D4. One (2026-10-24)\n")
        self.write("decisions/D4-two.md", "## D4. Two (2026-10-24)\n")
        self.commit("both merged")
        git(self.root, "checkout", "-q", "-B", "work")
        self.journal()

    def test_moving_one_to_the_next_number_under_the_same_name_passes(self):
        self.assertRefused("D4 is used more than once")
        git(self.root, "mv", "project-log/decisions/D4-two.md", "project-log/decisions/D5-two.md")
        self.write("decisions/D5-two.md", "## D5. Two (2026-10-24)\n")
        self.assertGood()
        self.commit()
        self.assertGood()

    def test_deleting_it_alone_or_under_another_name_is_refused(self):
        os.remove(self.path("decisions/D4-two.md"))
        self.assertRefused("project-log/decisions/D4-two.md was deleted. It was written by an earlier")
        self.write("decisions/D5-other.md", "## D5. Two (2026-10-24)\n")
        self.assertRefused("project-log/decisions/D4-two.md was deleted. It was written by an earlier")

    def test_a_number_the_base_has_once_is_not_moved(self):
        os.remove(self.path("decisions/D3-earlier.md"))
        self.write("decisions/D6-earlier.md", "## D6. An earlier decision (2026-10-23)\nWhy: three.\n")
        self.assertRefused("project-log/decisions/D3-earlier.md was deleted. It was written by an earlier")


class BranchFromBeforeD107(Repo):
    """A branch left master before the pointers came; master then took another old-style entry and the pointers."""
    POINTERS = False

    def setUp(self):
        super().setUp()
        git(self.root, "checkout", "-q", "master")
        self.write("JOURNAL.md", HEAD + OLD["JOURNAL.md"].replace("## 2026-10-02", "## 2026-10-03, another old "
                                                                  "pull request\n\n- three\n\n## 2026-10-02"), head=False)
        self.commit("an old-style entry")
        for name in OLD:
            self.write(name, with_pointer(self.text(name)), head=False)
        self.commit("D107")
        git(self.root, "checkout", "-q", "work")

    def test_its_own_entry_in_the_old_file_is_refused_with_the_words_for_moving_it(self):
        self.journal()
        self.write("JOURNAL.md", HEAD + OLD["JOURNAL.md"].replace("## 2026-10-02", "## 2026-10-24, mine\n\n- m\n\n"
                                                                  "## 2026-10-02"), head=False)
        bad = self.assertRefused("project-log/JOURNAL.md was edited. Since D107 it is history")
        self.assertTrue(any('"An open pull request from before D107" in project-log/README.md' in b for b in bad))
        self.assertFalse(any("LESSONS.md" in b or "DECISIONS.md" in b for b in bad), bad)   # untouched: not flagged
        self.assertFalse(any("Only the pointer" in b for b in bad), bad)

    def test_in_the_middle_of_merging_master_the_base_branchs_files_pass(self):
        self.journal()
        self.commit()
        git(self.root, "merge", "-q", "--no-commit", "--no-ff", "master")
        self.assertGood()
        git(self.root, "commit", "-q", "-m", "merged")
        self.assertGood()

    def test_after_merging_master_and_moving_the_entry_it_passes(self):
        self.write("JOURNAL.md", HEAD + OLD["JOURNAL.md"].replace("## 2026-10-02", "## 2026-10-24, mine\n\n- m\n\n"
                                                                  "## 2026-10-02"), head=False)
        self.commit("old style")
        subprocess.run(("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "merge", "-q", "master"),
                       cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)  # it conflicts
        git(self.root, "checkout", "master", "--", "project-log/JOURNAL.md", "project-log/LESSONS.md",
            "project-log/DECISIONS.md")
        self.journal()
        self.assertGood()


class EarlierName(Repo):
    def test_new_says_an_earlier_pull_request_wrote_the_file_and_gives_a_name(self):
        git(self.root, "checkout", "-q", "master")
        self.write("journal/2026-10-24-work.md", "## 2026-10-24, an earlier work branch\n\n- done\n")
        self.commit("earlier")
        git(self.root, "update-ref", "refs/remotes/origin/master", "master")
        git(self.root, "checkout", "-q", "work")
        for merged in (False, True):
            if merged:
                git(self.root, "merge", "-q", "master")
            err = io.StringIO()
            self.assertEqual(plog.main(["p", "new", "journal"], root=self.root, today=TODAY, out=io.StringIO(),
                                       err=err), 2)
            self.assertIn("an earlier pull request wrote it. Give a NAME of your own, e.g. "
                          "`python3 tools/project-log.py new journal work-2`", err.getvalue())
        self.assertEqual(plog.main(["p", "new", "journal", "work-2"], root=self.root, today=TODAY,
                                   out=io.StringIO()), 0)


class Hostile(Repo):
    @unittest.skipIf(os.name == "nt", "a carriage return in a file name")
    def test_a_name_with_a_carriage_return_cannot_forge_an_annotation(self):
        self.journal()
        name = "2026-10-24-x\r::error::forged.md"
        self.write("journal/" + name, "## 2026-10-24, x\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.assertEqual(plog.main(["p", "check", "--base", "master"], root=self.root, today=TODAY, out=out), 1)
        text = out.getvalue()
        self.assertNotIn("\r", text)
        self.assertIn(repr("project-log/journal/" + name), text)
        lines = text.splitlines()
        self.assertFalse([l for l in lines if l.startswith("::error::forged")], lines)
        self.assertEqual(len([l for l in lines if l.startswith("::error::")]), len(lines) - 1, lines)

    def test_a_control_character_in_any_problem_is_never_printed(self):
        out = io.StringIO()
        with mock.patch.object(plog, "tree_problems", return_value=["a\rb\x1b[2Jc\nd"]), \
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            plog.main(["p", "check"], root=self.root, today=TODAY, out=out)
        self.assertEqual(out.getvalue().splitlines()[0], "::error::a b [2Jc d")

    def test_a_file_that_is_not_utf8_is_said_in_words(self):
        self.journal()
        os.makedirs(self.path("lessons"))
        with open(self.path("lessons/2026-10-24-work.md"), "wb") as f:
            f.write(HEAD.encode() + b"## A lesson (2026-10-24)\n\nCaf\xe9, in Latin-1.\n")
        bad = self.assertRefused("project-log/lessons/2026-10-24-work.md: it is not UTF-8 text")
        self.assertEqual(len(bad), 1, bad)
        for what in ("lessons", "journal", "decisions"):
            self.assertEqual(plog.main(["p", what], root=self.root, out=io.StringIO()), 0, what)


class TheRealLog(unittest.TestCase):
    """The repository's own log, as every run of the unit tests sees it (so a number taken twice on master shows)."""

    def test_its_files_are_in_order(self):
        self.assertEqual(plog.tree_problems(REPO), [])

    def test_each_old_file_has_its_pointer_once(self):
        for name in plog.FROZEN:
            with open(os.path.join(REPO, "project-log", name), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count(plog.START), 1, name)
            self.assertEqual(len(plog.POINTER.findall(text)), 1, name)
            self.assertIn("D107", plog.POINTER.search(text).group(0), name)

    def test_the_printed_journal_and_decisions_hold_every_old_entry_byte_for_byte(self):
        for name, printed in (("JOURNAL.md", plog.print_journal(REPO)),
                              ("DECISIONS.md", plog.print_decisions(REPO)[0])):
            with open(os.path.join(REPO, "project-log", name), encoding="utf-8") as f:
                raw = f.read()
            old = plog.sections(plog.body(raw))[1]
            joined = plog.joined(printed)
            self.assertGreater(len(old), 100, name)
            for head, text in old:
                self.assertIn(text, joined, "%s: %s" % (name, head))
            if name == "JOURNAL.md":                # the old entries all together: the file's own bytes
                self.assertIn(raw[raw.index("\n## ") + 1:].rstrip("\n"), joined, name)

    def test_d107_is_a_file_and_the_number_greps(self):
        found = subprocess.run(["grep", "-rl", "D107", os.path.join(REPO, "project-log")], stdout=subprocess.PIPE,
                               universal_newlines=True, timeout=30).stdout
        self.assertIn("decisions/D107-log-fragments.md", found)


class Workflow(unittest.TestCase):
    def test_the_check_runs_on_every_pull_request_and_on_its_labels(self):
        with open(os.path.join(REPO, ".github", "workflows", "project-log.yml"), encoding="utf-8") as f:
            y = f.read()
        self.assertIn("types: [opened, synchronize, reopened, labeled, unlabeled]", y)
        self.assertIn("  push:\n    branches: [master]\n", y)  # a number taken twice shows red on master at once
        self.assertIn("if: github.event_name == 'push'\n        run: python3 tools/project-log.py check\n", y)
        self.assertIn("if: github.event_name == 'pull_request'", y)
        self.assertIn("\n    name: project-log\n", y)           # the check's name, for the ruleset
        self.assertIn("fetch-depth: 2", y)                       # HEAD^1, master's tip under GitHub's test merge
        self.assertIn("python3 tools/project-log.py check --base HEAD^1 $NO_JOURNAL", y)
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'no-journal') && '--no-journal-ok'", y)
        self.assertRegex(y, r"timeout-minutes: \d+")


if __name__ == "__main__":
    unittest.main()
