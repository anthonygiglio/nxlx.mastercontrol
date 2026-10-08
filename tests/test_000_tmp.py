# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The tests leave nothing behind in the temp folder (D69).

This module is named to be the first one `python3 -m unittest discover -s tests` loads: that command does not import
the `tests` package by itself, and the import below is what gives the run its own temp folder (tests/tmpguard.py)
before any test makes a file.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tests import tmpguard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INNER = "PVJ_TMP_INNER_RUN"                     # set for the runs this module starts, which load this module too
# Small, quick modules that between them make folders, files, sockets in a temp folder and start programs.
SUBSET = ["tests.test_settings", "tests.test_auth", "tests.test_paths", "tests.test_presets", "tests.test_sysd",
          "tests.test_run_folders"]


def run(args, tmp, **env):
    """Run Python in the repository with TMPDIR=tmp and no trace of the suite this test is part of."""
    e = {k: v for k, v in os.environ.items() if k not in (tmpguard.ENV, tmpguard.KEEP)}
    e.update(TMPDIR=tmp, **{INNER: "1"})
    e.update(env)
    return subprocess.run([sys.executable] + args, cwd=REPO, env=e, capture_output=True, text=True, timeout=300)


def listing(folder):
    """Everything in `folder`, two levels deep, so a failure names what was left and what is in it."""
    out = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        inside = sorted(os.listdir(full))[:8] if os.path.isdir(full) and not os.path.islink(full) else []
        out.append("%s: %s" % (name, ", ".join(inside)) if inside else name)
    return out


class Outer(unittest.TestCase):
    def setUp(self):
        # Not in this run's own folder: a run started here makes its folder in this one, and a test's socket two
        # levels further down still has to fit in about 100 bytes.
        self.tmp = tempfile.mkdtemp(prefix="pvjo", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.addCleanup(shutil.rmtree, self.tmp, True)


@unittest.skipIf(os.environ.get(INNER), "this is a run started by this module")
class NothingIsLeft(Outer):
    def test_this_run_has_a_folder_of_its_own(self):
        root = os.environ.get(tmpguard.ENV)
        self.assertTrue(root and tmpguard.marker(root), "the tests package did not set up a temp folder")
        self.assertEqual(tempfile.gettempdir(), root)
        self.assertEqual(os.environ["TMPDIR"], root)
        made = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, made)
        self.assertEqual(os.path.dirname(made), root)

    def test_named_modules_leave_nothing(self):
        r = run(["-m", "unittest"] + SUBSET, self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertRegex(r.stderr, r"Ran \d\d+ tests")
        self.assertEqual(listing(self.tmp), [], "left in the temp folder after the run")

    def test_discover_as_the_handoff_says_leaves_nothing(self):
        # No -t: unittest then imports test_x, not tests.test_x, and never the package by itself.
        r = run(["-m", "unittest", "discover", "-s", "tests", "-p", "test_[0q]*.py"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertRegex(r.stderr, r"Ran \d\d+ tests")
        self.assertEqual(listing(self.tmp), [], "left in the temp folder after the run")

    def test_discover_as_ci_runs_it_leaves_nothing(self):
        r = run(["-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_[0q]*.py"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertEqual(listing(self.tmp), [], "left in the temp folder after the run")

    def test_a_module_run_as_a_program_leaves_nothing(self):
        # As CI runs the GPU tests: python -m tests.test_shaders_gpu. Here a module that needs no GPU.
        code = ("import os, sys, tempfile, tests\n"
                "d = tempfile.mkdtemp(); open(os.path.join(d, 'x'), 'w').close()\n"
                "os.chmod(tempfile.mkdtemp(), 0o500)\n"
                "import subprocess; subprocess.run(['sh', '-c', 'mktemp >/dev/null; mktemp -d >/dev/null'], check=True)\n"
                "print(os.path.dirname(d))\n")
        r = run(["-c", code], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.path.dirname(r.stdout.strip()), self.tmp)
        self.assertTrue(os.path.basename(r.stdout.strip()).startswith("pvj-test-"))
        self.assertEqual(listing(self.tmp), [], "a folder, a read-only folder and what a shell made")

    def test_a_python_started_by_a_test_uses_the_same_folder_and_leaves_it_to_its_parent(self):
        code = ("import os, subprocess, sys, tempfile, tests\n"
                "mine = tempfile.gettempdir()\n"
                "child = subprocess.run([sys.executable, '-c', 'import tempfile, tests; print(tempfile.mkdtemp())'],\n"
                "                       capture_output=True, text=True).stdout.strip()\n"
                "assert os.path.dirname(child) == mine, (child, mine)\n"
                "assert os.path.isdir(child), 'the child removed the folder of its parent'\n")
        r = run(["-c", code], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(listing(self.tmp), [])

    def test_keep_keeps_the_folder_and_says_where(self):
        r = run(["-c", "import tempfile, tests; open(tempfile.mkdtemp() + '/evidence', 'w').close()"], self.tmp,
                **{tmpguard.KEEP: "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        kept = os.listdir(self.tmp)
        self.assertEqual(len(kept), 1)
        self.assertIn(os.path.join(self.tmp, kept[0]), r.stderr)
        self.assertIn("PVJ_KEEP_TMP", r.stderr)
        inside = [n for n in os.listdir(os.path.join(self.tmp, kept[0])) if n != tmpguard.MARKER]
        self.assertTrue(os.path.exists(os.path.join(self.tmp, kept[0], inside[0], "evidence")))

    def test_a_terminated_run_removes_its_folder_and_still_dies_of_the_signal(self):
        code = ("import sys, tempfile, time, tests\n"
                "open(tempfile.mkdtemp() + '/x', 'w').close()\n"
                "print('ready', flush=True)\n"
                "time.sleep(60)\n")
        e = {k: v for k, v in os.environ.items() if k not in (tmpguard.ENV, tmpguard.KEEP)}
        e.update(TMPDIR=self.tmp)
        p = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env=e, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "ready")
            self.assertEqual(len(os.listdir(self.tmp)), 1)
            p.terminate()
            self.assertEqual(p.wait(30), -signal.SIGTERM)
        finally:
            p.kill()
            p.stdout.close()
            p.wait()
        self.assertEqual(listing(self.tmp), [])

    def test_a_killed_run_is_cleared_by_a_later_one_once_it_is_old(self):
        code = ("import os, signal, tempfile, tests\n"
                "open(tempfile.mkdtemp() + '/x', 'w').close()\n"
                "os.kill(os.getpid(), signal.SIGKILL)\n")
        r = run(["-c", code], self.tmp)
        self.assertEqual(r.returncode, -signal.SIGKILL)             # CI's `timeout -s ABRT` ends a run the same way
        left = os.listdir(self.tmp)
        self.assertEqual(len(left), 1, "a run that is killed cannot clean up")
        r = run(["-c", "import tests"], self.tmp)
        self.assertEqual(os.listdir(self.tmp), left, "minutes old: it could be a run that is still going")
        old = time.time() - tmpguard.DEAD_AFTER - 60
        os.utime(os.path.join(self.tmp, left[0], tmpguard.MARKER), (old, old))
        r = run(["-c", "import tests"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(listing(self.tmp), [])


class Sweep(Outer):
    """What the sweep at the start of a run may remove, and above all what it may not."""

    def folder(self, name="pvj-test-abcd1234", pid=None, age=0.0, keep=False, made_by=tmpguard.MADE_BY, mark=True):
        path = os.path.join(self.tmp, name)
        os.mkdir(path)
        with open(os.path.join(path, "data"), "w") as f:
            f.write("x")
        if mark:
            m = os.path.join(path, tmpguard.MARKER)
            with open(m, "w") as f:
                json.dump({"made_by": made_by, "pid": self.dead if pid is None else pid, "keep": keep}, f)
            then = time.time() - age
            os.utime(m, (then, then))
        return path

    def setUp(self):
        super().setUp()
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.dead = p.pid                               # a process number nothing has now

    def test_an_old_folder_of_a_dead_run_goes(self):
        path = self.folder(age=tmpguard.DEAD_AFTER + 60)
        os.mkdir(os.path.join(path, "locked"))
        open(os.path.join(path, "locked", "f"), "w").close()
        os.chmod(os.path.join(path, "locked"), 0o500)   # a test left a read-only folder
        self.assertEqual(tmpguard.sweep(self.tmp), [path])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_recent_folder_stays_whatever_its_process(self):
        self.folder(age=tmpguard.DEAD_AFTER - 60)       # a process in another container looks dead from here
        self.assertEqual(tmpguard.sweep(self.tmp), [])

    def test_a_folder_of_a_running_process_stays(self):
        self.folder(pid=os.getpid(), age=tmpguard.DEAD_AFTER + 60)      # a run in another worktree
        self.assertEqual(tmpguard.sweep(self.tmp), [])

    def test_a_kept_folder_stays_for_two_days(self):
        path = self.folder(keep=True, age=tmpguard.DEAD_AFTER + 60)
        self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(tmpguard.sweep(self.tmp, now=time.time() + tmpguard.ANY_AFTER), [path])

    def test_after_two_days_a_process_number_proves_nothing(self):
        path = self.folder(pid=os.getpid(), age=tmpguard.ANY_AFTER + 60)
        self.assertEqual(tmpguard.sweep(self.tmp), [path])

    def test_nothing_that_is_not_provably_ours_is_touched(self):
        age = tmpguard.ANY_AFTER + 60
        outside = tempfile.mkdtemp(prefix="o")
        self.addCleanup(shutil.rmtree, outside, True)
        target = self.folder("pvj-test-target00", age=age)
        shutil.move(target, os.path.join(outside, "pvj-test-target00"))             # ours, but not in this folder
        os.symlink(os.path.join(outside, "pvj-test-target00"), os.path.join(self.tmp, "pvj-test-link0000"))
        self.folder("tmpabcd1234", age=age)                                         # another name, even with a marker
        self.folder("pvj-test-nomarker", mark=False)
        self.folder("pvj-test-stranger", age=age, made_by="someone else")
        open(os.path.join(self.tmp, "pvj-test-afile000"), "w").close()
        os.mkdir(os.path.join(self.folder("pvj-test-markdir0", mark=False), tmpguard.MARKER))
        before = listing(self.tmp)
        self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(listing(self.tmp), before)
        self.assertEqual(sorted(os.listdir(os.path.join(outside, "pvj-test-target00"))), [tmpguard.MARKER, "data"])
        for name in os.listdir(self.tmp):
            self.assertEqual(tmpguard.remove(os.path.join(self.tmp, name)), [os.path.join(self.tmp, name)], name)
        self.assertEqual(listing(self.tmp), before)

    def test_a_link_inside_is_removed_and_what_it_points_to_is_not(self):
        outside = tempfile.mkdtemp(prefix="o")
        self.addCleanup(shutil.rmtree, outside, True)
        open(os.path.join(outside, "precious"), "w").close()
        os.chmod(outside, 0o500)
        self.addCleanup(os.chmod, outside, 0o700)
        path = self.folder(age=tmpguard.DEAD_AFTER + 60)
        os.symlink(outside, os.path.join(path, "link"))
        os.mkdir(os.path.join(path, "sub"))
        os.symlink(outside, os.path.join(path, "sub", "link"))
        self.assertEqual(tmpguard.sweep(self.tmp), [path])
        self.assertEqual(os.listdir(outside), ["precious"])
        self.assertEqual(stat_mode(outside), 0o500)


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


class Rule(unittest.TestCase):
    def test_a_temp_folder_made_outside_the_temp_folder_is_removed_by_its_test(self):
        """`dir=` takes a folder out of the run's own temp folder (a socket's path is the usual reason: `dir="/tmp"`),
        so nothing removes it unless the test does: the next line must register that."""
        bad = []
        for folder in (os.path.join(REPO, "tests"), os.path.join(REPO, "tests", "ui")):
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".py") or name in (os.path.basename(__file__), "tmpguard.py"):
                    continue
                with open(os.path.join(folder, name)) as f:
                    lines = f.read().splitlines()
                for i, line in enumerate(lines):
                    if re.search(r"\bmk[ds]temp\(.*\bdir=", line) and "addCleanup" not in " ".join(lines[i + 1:i + 3]):
                        bad.append("%s:%d" % (name, i + 1))
        self.assertEqual(bad, [], "mkdtemp(dir=...) without self.addCleanup(shutil.rmtree, ...) right after it")


if __name__ == "__main__":
    unittest.main()
