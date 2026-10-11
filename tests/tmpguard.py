# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A temp folder of its own for every run of the tests, removed when the run ends (D69).

The tests make several hundred temp folders a run and most never removed one, so each run left them in the
system's temp folder; on 2026-10-08 that filled the development Mac's disk. A test needs nothing new for this:
`tempfile.mkdtemp()` and friends, and every program a test starts (it inherits TMPDIR), now land in one folder,
`<temp>/pvj-t-XXXXXX`, and that folder goes when the process ends.

    PVJ_KEEP_TMP=1   keep the folder (to look at what a failing test left); its path is printed at the end
    PVJ_TEST_TMP     set by this module: the folder of the running suite; a Python that a test starts uses the
                     same one, as long as the process that made it is alive, and leaves the removing to it

What ends a run and what happens to the folder:
    the run finishes, Ctrl-C, sys.exit      removed (atexit)
    SIGTERM, SIGHUP (kill, a closed window) removed, then the process dies of that signal as it would have
    SIGKILL, SIGABRT (CI's timeout), a crash left behind; the next run of the tests, from any checkout, removes it
                                            once it is two hours old and the process that made it is gone

Nothing is ever removed that is not provably this suite's: a real folder (not a link) directly in the temp folder,
named pvj-t-*, owned by this user, holding the marker file this module wrote. The marker is the proof; the name
only says where to look, and is short because a socket's path in a test's folder has about 100 bytes in all. Two
runs at the same time (two worktrees) have a folder each and leave the other's alone: its process is alive. A
folder whose process is alive is left for fourteen days, because a process number alone cannot tell a run that
was paused from a number given out again, and the start time of a process cannot be read the same way on every
system; after fourteen days it is taken to be the latter.

Nothing here may stop a run: the sweep and the removal catch what goes wrong and carry on.

What this cannot catch: a folder made with an explicit `dir=` outside the temp folder (remove those in the test;
tests/test_000_tmp.py checks that), and a program started with an environment that has no TMPDIR.
"""
import atexit
import binascii
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import threading
import time

PREFIX = "pvj-t-"
MARKER = ".pvj-test-tmp"
MADE_BY = "nxlx.mastercontrol tests/tmpguard.py"
ENV = "PVJ_TEST_TMP"
KEEP = "PVJ_KEEP_TMP"
DEAD_AFTER = 2 * 3600           # its process is gone: longer than any run (CI stops one after 20 minutes)
KEPT_AFTER = 48 * 3600          # its process is gone and it was kept on purpose
LIVE_AFTER = 14 * 24 * 3600     # a process has its number: a paused run for two weeks, or the number was given out again
# The longest path the tests put a socket at, counted from the run's folder: /tmpXXXXXXXX/run/player.sock
# (tests/test_player.py, tests/test_server.py, tests/ui/harness.py). A socket's path may be 103 bytes on macOS and
# 107 on Linux.
SOCKET_TAIL = 28
SOCKET_MAX = 103 if sys.platform == "darwin" else 107

_mine = None                    # (folder, process number) once this process has made its folder


def marker(path):
    """What this suite wrote into `path` when it made it, or None if `path` is not one of its folders."""
    try:
        st = os.lstat(path)
        if not (os.path.basename(path).startswith(PREFIX) and stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()):
            return None
        fd = os.open(os.path.join(path, MARKER), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd) as f:
            st = os.fstat(f.fileno())               # of the file that is read, not of a name that can change
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > 4096:
                return None
            data = json.loads(f.read(4097))
        if not isinstance(data, dict) or data.get("made_by") != MADE_BY:
            return None
        data["mtime"] = st.st_mtime
        return data
    except (OSError, ValueError):
        return None


def _alive(pid):
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True                 # there, and not ours to signal
    except (OSError, OverflowError, ValueError, TypeError):
        return False                # gone, or not a process number at all (a damaged marker)
    return True


def is_stale(mark, now=None):
    age = (time.time() if now is None else now) - mark["mtime"]
    if _alive(mark.get("pid")):
        return age > LIVE_AFTER
    return age > (KEPT_AFTER if mark.get("keep") else DEAD_AFTER)


def _open_up(folder):
    """Give ourselves a folder we own back (a test may leave one read-only), without ever following a link: the
    mode of whatever a link points to is not ours to change."""
    if os.chmod in os.supports_follow_symlinks:             # macOS, the BSDs
        os.chmod(folder, 0o700, follow_symlinks=False)
        return
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except PermissionError:
        if not hasattr(os, "O_PATH"):
            return
        fd = os.open(folder, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)       # Linux: a handle without reading
        try:
            os.chmod("/proc/self/fd/%d" % fd, 0o700)
        finally:
            os.close(fd)
        return
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _unlock(top):
    """Make every real folder under `top` (itself included) one we may empty. Links are not followed."""
    todo = [top]
    while todo:
        folder = todo.pop()
        try:
            _open_up(folder)
            with os.scandir(folder) as entries:
                todo.extend(e.path for e in entries if e.is_dir(follow_symlinks=False))
        except OSError:
            pass


def remove(path):
    """Remove one of this suite's folders. Returns the names in it that could not be removed (then the folder and
    its marker stay, so a later run still knows it as its own). The marker goes last for the same reason. Never
    raises for what the file system does: another run may be removing the same folder at the same moment."""
    if marker(path) is None:
        return [path]
    left = []
    try:
        device = os.lstat(path).st_dev
        for attempt in range(3):        # a thread that is still writing can put a file back while we empty
            for name in os.listdir(path):
                full = os.path.join(path, name)
                try:
                    st = os.lstat(full)
                    if name == MARKER or st.st_dev != device:       # something mounted here is not ours to empty
                        continue
                    if stat.S_ISDIR(st.st_mode):
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
        os.unlink(os.path.join(path, MARKER))
        os.rmdir(path)
    except OSError:
        return [path]
    return []


def sweep(base, now=None):
    """Remove what earlier runs that were killed left in `base`. Returns the folders removed. Whatever goes wrong
    with one folder, the others are still looked at and the run goes on."""
    gone = []
    try:
        with os.scandir(base) as entries:
            names = [e.name for e in entries if e.name.startswith(PREFIX)]
    except OSError:
        return gone
    for name in names:
        try:
            path = os.path.join(base, name)
            mark = marker(path)
            if mark is not None and is_stale(mark, now) and not remove(path):
                gone.append(path)
        except Exception:
            continue
    return gone


def _say(text):
    try:
        print(text, file=sys.stderr)
    except Exception:                # nobody is reading any more: that must not stop the removal or the exit
        pass


def _end(root, pid, keep):
    if os.getpid() != pid:          # a forked child: the folder is its parent's
        return
    if keep:
        _say("tests: temp folder kept (%s): %s" % (KEEP, root))
        return
    left = remove(root)
    if left:
        _say("tests: could not empty the temp folder %s: %s" % (root, ", ".join(left[:20])))


def _on_signals(root, pid, keep):
    """Remove the folder on a polite kill too, then die of the signal exactly as without this, whatever the
    removing did. Only where nobody has chosen a handler yet; a test that sets its own replaces this one."""
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        try:
            _end(root, pid, keep)
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        try:
            if signum is not None and signal.getsignal(signum) == signal.SIG_DFL:
                signal.signal(signum, handler)
        except (OSError, ValueError):
            pass


def _parents(root):
    """The folder a Python started by a test may share: one of ours whose maker is another process, and alive."""
    if not root or not os.path.isabs(root):
        return False
    mark = marker(root)
    return mark is not None and mark.get("pid") != os.getpid() and _alive(mark.get("pid"))


def _make(base):
    for attempt in range(100):
        root = os.path.join(base, PREFIX + binascii.hexlify(os.urandom(3)).decode())
        try:
            os.mkdir(root, 0o700)
            return root
        except FileExistsError:
            continue
    raise FileExistsError("no free name for the tests' temp folder in %s" % base)


def activate():
    """Called when the `tests` package is imported. Returns the folder, or None where this does nothing."""
    global _mine
    if not hasattr(os, "getuid"):
        return None                 # not a POSIX system: no owner to check, so nothing is made and nothing removed
    if _mine and _mine[1] == os.getpid():
        root = _mine[0]
    elif _parents(os.environ.get(ENV)):
        root = os.environ[ENV]
    else:
        base = os.path.abspath(tempfile.gettempdir())   # not realpath: on macOS that is 8 bytes longer
        sweep(base)
        keep = os.environ.get(KEEP, "") not in ("", "0")
        root = _make(base)
        with open(os.path.join(root, MARKER), "w") as f:
            json.dump({"made_by": MADE_BY, "pid": os.getpid(), "keep": keep, "started": int(time.time()),
                       "checkout": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "argv": " ".join(sys.argv)[:300]}, f)
        _mine = (root, os.getpid())
        os.environ[ENV] = root
        atexit.register(_end, root, os.getpid(), keep)
        _on_signals(root, os.getpid(), keep)
    os.environ["TMPDIR"] = root     # for the programs the tests start
    tempfile.tempdir = root         # tempfile remembers its first answer
    return root


def room_for_sockets(root):
    """How many bytes are to spare for the longest socket path the tests make below `root` (negative: too few)."""
    return SOCKET_MAX - (len(os.fsencode(root)) + SOCKET_TAIL)
