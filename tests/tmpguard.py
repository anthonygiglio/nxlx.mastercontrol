# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A temp folder of its own for every run of the tests, removed when the run ends (D69).

The tests make several hundred temp folders a run and most never removed one, so each run left them in the
system's temp folder; on 2026-10-08 that filled the development Mac's disk. A test needs nothing new for this:
`tempfile.mkdtemp()` and friends, and every program a test starts (it inherits TMPDIR), now land in one folder,
`<temp>/pvj-test-XXXXXXXX`, and that folder goes when the process ends.

    PVJ_KEEP_TMP=1   keep the folder (to look at what a failing test left); its path is printed at the end
    PVJ_TEST_TMP     set by this module: the folder of the running suite; a Python that a test starts uses the
                     same one and leaves the removing to the process that made it

What ends a run and what happens to the folder:
    the run finishes, Ctrl-C, sys.exit      removed (atexit)
    SIGTERM, SIGHUP (kill, a closed window) removed, then the process dies of that signal as it would have
    SIGKILL, SIGABRT (CI's timeout), a crash left behind; the next run of the tests, from any checkout, removes it
                                            once it is two hours old and the process that made it is gone

Nothing is ever removed that is not provably this suite's: a real folder (not a link) directly in the temp folder,
named pvj-test-*, owned by this user, holding the marker file this module wrote. Two runs at the same time (two
worktrees) have a folder each and leave the other's alone: its process is alive.

What this cannot catch: a folder made with an explicit `dir=` outside the temp folder (remove those in the test;
tests/test_000_tmp.py checks that), and a program started with an environment that has no TMPDIR.
"""
import atexit
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import threading
import time

PREFIX = "pvj-test-"
MARKER = ".pvj-test-tmp"
MADE_BY = "nxlx.mastercontrol tests/tmpguard.py"
ENV = "PVJ_TEST_TMP"
KEEP = "PVJ_KEEP_TMP"
DEAD_AFTER = 2 * 3600       # its process is gone: longer than any run (CI stops one after 20 minutes)
ANY_AFTER = 48 * 3600       # whatever its process number says by now (numbers are used again), and kept folders


def marker(path):
    """What this suite wrote into `path` when it made it, or None if `path` is not one of its folders."""
    try:
        st = os.lstat(path)
        if not (os.path.basename(path).startswith(PREFIX) and stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()):
            return None
        mark = os.path.join(path, MARKER)
        st = os.lstat(mark)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > 4096:
            return None
        with open(mark) as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("made_by") != MADE_BY:
            return None
        data["mtime"] = st.st_mtime
        return data
    except (OSError, ValueError):
        return None


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                 # there, and not ours to signal
    return True


def is_stale(mark, now=None):
    age = (time.time() if now is None else now) - mark["mtime"]
    if age > ANY_AFTER:
        return True
    return age > DEAD_AFTER and not mark.get("keep") and not _alive(mark.get("pid"))


def _unlock(top):
    """Make every real folder under `top` (itself included) one we may empty: a test may leave one read-only.
    Links are not followed."""
    todo = [top]
    while todo:
        folder = todo.pop()
        try:
            os.chmod(folder, 0o700)
            with os.scandir(folder) as entries:
                todo.extend(e.path for e in entries if e.is_dir(follow_symlinks=False))
        except OSError:
            pass


def remove(path):
    """Remove one of this suite's folders. Returns the names in it that could not be removed (then the folder and
    its marker stay, so a later run still knows it as its own). The marker goes last for the same reason."""
    if marker(path) is None:
        return [path]
    left = []
    for attempt in range(3):        # a thread that is still writing can put a file back while we empty
        try:
            names = [n for n in os.listdir(path) if n != MARKER]
        except OSError:
            return [path]
        for name in names:
            full = os.path.join(path, name)
            try:
                if stat.S_ISDIR(os.lstat(full).st_mode):
                    _unlock(full)
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.unlink(full)
            except OSError:
                pass
        left = sorted(n for n in os.listdir(path) if n != MARKER)
        if not left:
            break
        time.sleep(0.1)
    if left:
        return left
    try:
        os.unlink(os.path.join(path, MARKER))
        os.rmdir(path)
    except OSError:
        return [path]
    return []


def sweep(base, now=None):
    """Remove what earlier runs that were killed left in `base`. Returns the folders removed."""
    gone = []
    try:
        with os.scandir(base) as entries:
            names = [e.name for e in entries if e.name.startswith(PREFIX)]
    except OSError:
        return gone
    for name in names:
        path = os.path.join(base, name)
        mark = marker(path)
        if mark is not None and is_stale(mark, now) and not remove(path):
            gone.append(path)
    return gone


def _end(root, pid, keep):
    if os.getpid() != pid:          # a forked child: the folder is its parent's
        return
    if keep:
        print("tests: temp folder kept (%s): %s" % (KEEP, root), file=sys.stderr)
        return
    left = remove(root)
    if left:
        print("tests: could not empty the temp folder %s: %s" % (root, ", ".join(left[:20])), file=sys.stderr)


def _on_signals(root, pid, keep):
    """Remove the folder on a polite kill too, then die of the signal exactly as without this. Only where nobody
    has chosen a handler yet; a test that sets its own replaces this one."""
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        _end(root, pid, keep)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        try:
            if signum is not None and signal.getsignal(signum) == signal.SIG_DFL:
                signal.signal(signum, handler)
        except (OSError, ValueError):
            pass


def activate():
    """Called when the `tests` package is imported. Returns the folder, or None where this does nothing."""
    if not hasattr(os, "getuid"):
        return None                 # not a POSIX system: no owner to check, so nothing is made and nothing removed
    root = os.environ.get(ENV)
    if not root or marker(root) is None:
        base = tempfile.gettempdir()
        sweep(base)
        keep = os.environ.get(KEEP, "") not in ("", "0")
        root = tempfile.mkdtemp(prefix=PREFIX, dir=base)        # mode 0700; the name is short: a socket's path
        with open(os.path.join(root, MARKER), "w") as f:        # in a test's folder below it has about 100 bytes
            json.dump({"made_by": MADE_BY, "pid": os.getpid(), "keep": keep, "started": int(time.time()),
                       "checkout": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "argv": " ".join(sys.argv)[:300]}, f)
        os.environ[ENV] = root
        atexit.register(_end, root, os.getpid(), keep)
        _on_signals(root, os.getpid(), keep)
    os.environ["TMPDIR"] = root     # for the programs the tests start
    tempfile.tempdir = root         # tempfile remembers its first answer
    return root
