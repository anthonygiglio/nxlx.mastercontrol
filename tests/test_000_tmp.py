# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The tests leave nothing behind in the temp folder (D69).

This module is named to be the first one `python3 -m unittest discover -s tests` loads: that command does not import
the `tests` package by itself, and the import below is what gives the run its own temp folder (tests/tmpguard.py)
before any test makes a file.
"""
import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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
        self.assertTrue(os.path.basename(r.stdout.strip()).startswith(tmpguard.PREFIX))
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


@unittest.skipIf(os.environ.get(INNER), "this is a run started by this module")
class Hardening(Outer):
    """What the review of 2026-10-08 found: each of these failed before its repair."""

    def dead_folder(self, age):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        path = os.path.join(self.tmp, tmpguard.PREFIX + "dead00")
        os.mkdir(path)
        open(os.path.join(path, "in-use"), "w").close()
        m = os.path.join(path, tmpguard.MARKER)
        with open(m, "w") as f:
            json.dump({"made_by": tmpguard.MADE_BY, "pid": p.pid, "keep": False}, f)
        os.utime(m, (time.time() - age, time.time() - age))
        return path

    def test_a_terminated_run_dies_of_the_signal_even_when_its_last_words_cannot_be_written(self):
        code = "import time, tests\nprint('ready', flush=True)\ntime.sleep(60)\n"
        e = {k: v for k, v in os.environ.items() if k != tmpguard.ENV}
        e.update({"TMPDIR": self.tmp, tmpguard.KEEP: "1"})
        p = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env=e, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "ready")
            p.stderr.close()                            # whoever was reading has gone: "kept: ..." has nowhere to go
            p.terminate()
            self.assertEqual(p.wait(20), -signal.SIGTERM)
        finally:
            p.kill()
            p.stdout.close()
            p.wait()

    def test_a_folder_named_in_the_environment_is_shared_only_while_its_maker_lives(self):
        # A variable left over in a shell, or the child of a run that was killed: the folder is nobody's now, and a
        # later run would sweep it from under whoever moved in.
        dead = self.dead_folder(tmpguard.DEAD_AFTER - 600)
        r = run(["-c", "import tempfile, tests; print(tempfile.gettempdir())"], self.tmp, **{tmpguard.ENV: dead})
        self.assertEqual(r.returncode, 0, r.stderr)
        used = r.stdout.strip()
        self.assertNotEqual(used, dead)
        self.assertEqual(os.path.dirname(used), self.tmp)
        self.assertEqual(sorted(os.listdir(dead)), [tmpguard.MARKER, "in-use"])
        self.assertEqual(os.listdir(self.tmp), [os.path.basename(dead)], "the new run's own folder is gone again")

    def test_a_relative_temp_folder_is_still_removed_after_the_run_went_elsewhere(self):
        os.mkdir(os.path.join(self.tmp, "rel"))
        code = ("import os, sys, tempfile\n"
                "tempfile.tempdir = 'rel'\n"            # what Python 3.9 makes of TMPDIR=rel; later ones make it whole
                "sys.path.insert(0, %r)\n"
                "import tests\n"
                "assert os.path.isabs(tempfile.gettempdir()) and os.path.isabs(os.environ['TMPDIR'])\n"
                "tempfile.mkdtemp()\n"
                "os.chdir('/')\n" % REPO)
        e = {k: v for k, v in os.environ.items() if k not in (tmpguard.ENV, tmpguard.KEEP)}
        r = subprocess.run([sys.executable, "-c", code], cwd=self.tmp, env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(listing(os.path.join(self.tmp, "rel")), [])


class Sweep(Outer):
    """What the sweep at the start of a run may remove, and above all what it may not."""

    def folder(self, name=None, pid=None, age=0.0, keep=False, made_by=tmpguard.MADE_BY, mark=True):
        path = os.path.join(self.tmp, name or tmpguard.PREFIX + "abc123")
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
        for name, mode in (("locked", 0o500), ("shut", 0o000)):         # a test left folders it may not write or read
            os.mkdir(os.path.join(path, name))
            open(os.path.join(path, name, "f"), "w").close()
            os.chmod(os.path.join(path, name), mode)
        self.assertEqual(tmpguard.sweep(self.tmp), [path])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_recent_folder_stays_whatever_its_process(self):
        self.folder(age=tmpguard.DEAD_AFTER - 60)       # a process in another container looks dead from here
        self.assertEqual(tmpguard.sweep(self.tmp), [])

    def test_a_folder_of_a_running_process_stays_for_two_weeks(self):
        # A run in another worktree; or one that was paused for days. Only after two weeks is the number taken to
        # belong to some other process by now.
        path = self.folder(pid=os.getpid(), age=tmpguard.KEPT_AFTER + 3600)
        self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(tmpguard.sweep(self.tmp, now=time.time() + tmpguard.LIVE_AFTER - tmpguard.KEPT_AFTER - 7200), [])
        self.assertEqual(tmpguard.sweep(self.tmp, now=time.time() + tmpguard.LIVE_AFTER), [path])

    def test_a_kept_folder_stays_for_two_days(self):
        path = self.folder(keep=True, age=tmpguard.DEAD_AFTER + 60)
        self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(tmpguard.sweep(self.tmp, now=time.time() + tmpguard.KEPT_AFTER), [path])

    def test_a_marker_with_no_usable_process_number_is_a_dead_run_and_never_an_error(self):
        for i, pid in enumerate((10 ** 30, -5, 0, "12", None, 1.5, True, [1])):
            self.assertFalse(tmpguard._alive(pid), repr(pid))
            path = self.folder(tmpguard.PREFIX + "odd%03d" % i, pid=pid, age=tmpguard.DEAD_AFTER + 60)
            self.assertEqual(tmpguard.sweep(self.tmp), [path], repr(pid))

    def test_a_folder_that_another_run_removes_at_the_same_moment_does_not_stop_this_one(self):
        # Two runs start together and both find the same old folder. The one that comes second finds it gone in
        # the middle of emptying it; that used to end `import tests` with FileNotFoundError.
        path = self.folder(age=tmpguard.DEAD_AFTER + 60)
        real, calls = os.listdir, []

        def listdir(p):
            if p == path:
                calls.append(p)
                if len(calls) == 2:
                    shutil.rmtree(path)                 # the other run got there
            return real(p)

        with mock.patch("os.listdir", listdir):
            self.assertEqual(tmpguard.remove(path), [path])     # at exit and in the signal handler it is called bare
        self.assertEqual(len(calls), 2)
        path = self.folder(age=tmpguard.DEAD_AFTER + 60)
        del calls[:]
        with mock.patch("os.listdir", listdir):
            self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_whatever_goes_wrong_with_one_folder_the_next_is_still_swept(self):
        first = self.folder(tmpguard.PREFIX + "aaaaaa", age=tmpguard.DEAD_AFTER + 60)
        second = self.folder(tmpguard.PREFIX + "bbbbbb", age=tmpguard.DEAD_AFTER + 60)
        real = tmpguard.remove

        def remove(path):
            if path == first:
                raise RuntimeError("anything at all")
            return real(path)

        with mock.patch.object(tmpguard, "remove", remove):
            self.assertEqual(tmpguard.sweep(self.tmp), [second])
        self.assertEqual(os.listdir(self.tmp), [os.path.basename(first)])

    def test_nothing_that_is_not_provably_ours_is_touched(self):
        age = tmpguard.LIVE_AFTER + 60
        P = tmpguard.PREFIX
        outside = tempfile.mkdtemp(prefix="o")
        self.addCleanup(shutil.rmtree, outside, True)
        target = self.folder(P + "target", age=age)
        shutil.move(target, os.path.join(outside, P + "target"))                    # ours, but not in this folder
        os.symlink(os.path.join(outside, P + "target"), os.path.join(self.tmp, P + "link00"))
        self.folder("tmpabcd1234", age=age)                                         # another name, even with a marker
        self.folder("pvj-test-abcd1234", age=age)
        self.folder(P + "nomark", mark=False)
        self.folder(P + "strngr", age=age, made_by="someone else")
        open(os.path.join(self.tmp, P + "afile0"), "w").close()
        os.mkdir(os.path.join(self.folder(P + "mrkdir", mark=False), tmpguard.MARKER))
        os.symlink(os.path.join(outside, P + "target", tmpguard.MARKER),            # a marker that is a link to a real one
                   os.path.join(self.folder(P + "mrklnk", mark=False), tmpguard.MARKER))
        os.mkfifo(os.path.join(self.folder(P + "mrkpip", mark=False), tmpguard.MARKER))
        before = listing(self.tmp)
        self.assertEqual(tmpguard.sweep(self.tmp), [])
        self.assertEqual(listing(self.tmp), before)
        self.assertEqual(sorted(os.listdir(os.path.join(outside, P + "target"))), [tmpguard.MARKER, "data"])
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
        self.assertEqual(mode_of(outside), 0o500)

    def test_unlocking_never_changes_what_a_link_points_to(self):
        # The walk only hands real folders on, but between looking and changing a folder can be swapped for a link.
        outside = tempfile.mkdtemp(prefix="o")
        self.addCleanup(shutil.rmtree, outside, True)
        self.addCleanup(os.chmod, outside, 0o700)
        secret = os.path.join(outside, "secret")
        open(secret, "w").close()
        os.chmod(secret, 0o600)
        os.chmod(outside, 0o500)
        path = self.folder()
        for name, target in (("to-file", secret), ("to-folder", outside)):
            os.symlink(target, os.path.join(path, name))
            tmpguard._unlock(os.path.join(path, name))
        self.assertEqual(mode_of(secret), 0o600)
        self.assertEqual(mode_of(outside), 0o500)


def mode_of(path):
    return os.stat(path).st_mode & 0o777


class Sockets(unittest.TestCase):
    def test_the_temp_folder_leaves_room_for_the_sockets_the_tests_make(self):
        """Tests put sockets in temp folders, and a socket's path is short (103 bytes on macOS, 107 on Linux). If
        this fails, the socket tests would fail with "AF_UNIX path too long" or a player that never answers: set
        TMPDIR to something shorter (an agent's scratch folder is too long; /tmp/<a few letters> is fine)."""
        root = tempfile.gettempdir()
        room = tmpguard.room_for_sockets(root)
        self.assertGreaterEqual(room, 0, "the temp folder %s is %d bytes too long for the sockets the tests make "
                                "(%d bytes allowed for a socket's path here): use a shorter TMPDIR"
                                % (root, -room, tmpguard.SOCKET_MAX))

    def test_the_count_is_of_a_real_path(self):
        root = "/var/folders/xf/ypzddf7d2hv7twms6f8c6rrc0000gn/T/" + tmpguard.PREFIX + "0a1b2c"     # a Mac's own
        longest = root + "/tmp" + "x" * 8 + "/run/player.sock"
        self.assertEqual(len(longest), len(root) + tmpguard.SOCKET_TAIL)
        self.assertEqual(tmpguard.room_for_sockets(root), tmpguard.SOCKET_MAX - len(longest))
        self.assertLess(tmpguard.room_for_sockets("/" + "x" * 80), 0)


CLEANUPS = ("addCleanup", "addClassCleanup", "addModuleCleanup")


def unremoved(source):
    """Line numbers where a temp file or folder is made with a `dir` of its own (so outside the run's temp folder,
    as far as anyone reading can tell) and the same function does not, at or after that line, register a cleanup
    that names what was made (`d = mkdtemp(dir=...)` wants `self.addCleanup(..., d, ...)`; where the result is not
    given a name, any cleanup after it will do). `dir=None` is the run's folder. TemporaryDirectory removes itself;
    NamedTemporaryFile does unless it is told `delete=False`."""
    def called(node):
        f = node.func
        return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""

    def own(scope):                     # the nodes of a function without those of the functions defined in it
        todo = list(ast.iter_child_nodes(scope))
        while todo:
            node = todo.pop()
            yield node
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                todo.extend(ast.iter_child_nodes(node))

    def elsewhere(call):
        name = called(call)
        given = {k.arg: k.value for k in call.keywords if k.arg}
        if name in ("mkdtemp", "mkstemp"):
            where = given.get("dir", call.args[2] if len(call.args) > 2 else None)
        elif name == "NamedTemporaryFile":
            delete = given.get("delete")
            if not (isinstance(delete, ast.Constant) and delete.value is False):
                return False
            where = given.get("dir")
        else:
            return False
        return where is not None and not (isinstance(where, ast.Constant) and where.value is None)

    tree = ast.parse(source)
    bad = []
    for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        nodes = list(own(scope))
        names = {}                      # a call -> what its result is called
        for node in nodes:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                targets = [e for t in node.targets for e in (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t])]
                names[node.value] = {ast.unparse(t) for t in targets}
        cleanups = [(c.lineno, {ast.unparse(n) for a in c.args + [k.value for k in c.keywords] for n in ast.walk(a)
                                if isinstance(n, (ast.Name, ast.Attribute))})
                    for c in nodes if isinstance(c, ast.Call) and called(c) in CLEANUPS]
        for call in nodes:
            if isinstance(call, ast.Call) and elsewhere(call):
                made = names.get(call)
                if not any(line >= call.lineno and (made is None or made & said) for line, said in cleanups):
                    bad.append(call.lineno)
    return sorted(bad)


class Rule(unittest.TestCase):
    def test_a_temp_folder_made_outside_the_temp_folder_is_removed_by_its_test(self):
        """`dir=` takes a folder out of the run's own temp folder (a socket's path is the usual reason: `dir="/tmp"`),
        so nothing removes it unless the test does: the function that makes it must register a cleanup."""
        bad = []
        for folder in (os.path.join(REPO, "tests"), os.path.join(REPO, "tests", "ui")):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".py") and name not in (os.path.basename(__file__), "tmpguard.py"):
                    with open(os.path.join(folder, name)) as f:
                        bad += ["%s:%d" % (name, line) for line in unremoved(f.read())]
        self.assertEqual(bad, [], "a temp file or folder made with dir=... in a function with no self.addCleanup(...)")

    def test_what_the_rule_sees(self):
        def lines(body):
            return unremoved("import tempfile, shutil\nclass T:\n    def test(self):\n" +
                             "".join("        %s\n" % x for x in body))
        self.assertEqual(lines(['d = tempfile.mkdtemp(dir="/tmp")']), [4])
        self.assertEqual(lines(['d = tempfile.mkdtemp(prefix="x",', '                     dir="/tmp")']), [4])
        self.assertEqual(lines(['d = tempfile.mkdtemp("", "x", "/tmp")']), [4])
        self.assertEqual(lines(['fd, p = tempfile.mkstemp(dir=self.where)']), [4])
        self.assertEqual(lines(['f = tempfile.NamedTemporaryFile(dir="/tmp", delete=False)']), [4])
        self.assertEqual(lines(['d = mkdtemp(dir="/tmp")', 'def later():', '    self.addCleanup(shutil.rmtree, d)']), [4])
        self.assertEqual(lines(['self.addCleanup(self.stop)', 'd = tempfile.mkdtemp(dir="/tmp")']), [5])
        self.assertEqual(lines(['d = tempfile.mkdtemp(dir="/tmp")', 'self.addCleanup(self.server.close)']), [4])
        for fine in (['d = tempfile.mkdtemp()'],
                     ['d = tempfile.mkdtemp(dir=None)'],
                     ['f = tempfile.NamedTemporaryFile(dir="/tmp")'],
                     ['d = tempfile.TemporaryDirectory(dir="/tmp")'],
                     ['d = tempfile.mkdtemp(dir="/tmp")', 'self.addCleanup(shutil.rmtree, d, True)'],
                     ['d = tempfile.mkdtemp(dir="/tmp")', 'x = 1', 'y = 2', 'self.addCleanup(shutil.rmtree, d, True)'],
                     ['d = tempfile.mkdtemp(dir="/tmp")', 'self.addClassCleanup(shutil.rmtree, d, True)'],
                     ['self.d = tempfile.mkdtemp(dir="/tmp")', 'self.addCleanup(shutil.rmtree, self.d, True)'],
                     ['fd, p = tempfile.mkstemp(dir="/tmp")', 'self.addCleanup(os.unlink, p)'],
                     ['d = tempfile.mkdtemp(dir="/tmp")', 'self.addCleanup(lambda: shutil.rmtree(d))'],
                     ['use(tempfile.mkdtemp(dir=self.tmp))', 'self.addCleanup(shutil.rmtree, self.tmp, True)'],
                     ['d = tempfile.mkdtemp(dir="/tmp")', 'unittest.addModuleCleanup(shutil.rmtree, d, True)']):
            self.assertEqual(lines(fine), [], fine)
        self.assertEqual(unremoved('import tempfile\nd = tempfile.mkdtemp(dir="/tmp")\n'), [2])    # a helper script


if __name__ == "__main__":
    unittest.main()
