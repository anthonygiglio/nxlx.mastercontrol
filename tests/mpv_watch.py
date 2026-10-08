# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A watch on the control socket of a real mpv, for the tests that draw (tests/test_*_gpu.py). Tests only.

Why: "no reply from mpv" came now and then in CI's GPU jobs and was rerun each time. The player's client
(pvj/player.py, `Ipc.request`) opens a connection for each request, waits two seconds and closes it, so a failure
said nothing but that: not whether the answer came a moment later or never, not what the player was doing.

What it does, with the product's client left exactly as it is (the request still goes through `Ipc.request`, and a
request that failed still fails with the same error):

- it times every request and, when the tests end, prints how long each kind took (how near the two seconds the
  ordinary ones come is half the answer);
- when a request gets no reply it prints a report: the request and its number, how long was waited, what the player
  sent on that connection meanwhile, how long the player's process and each of its threads were busy, the machine's
  load, whether the player answers a cheap question on a new connection and how long that takes, WHEN THE ANSWER TO
  THE ORIGINAL REQUEST CAME on its own connection (which is kept open and read on for a while), how long the same
  request takes when asked once more with a long wait, and the end of the player's own log if it writes one
  (PVJ_MPV_LOG=1).

How it sees the connection without a change to the product: while a watch is on, the `socket` that pvj.player
uses is a stand-in whose sockets note what they receive and can be kept open after the client closed them.
"""
import atexit
import json
import os
import socket
import sys
import threading
import time

from pvj import player as player_module
from pvj.player import Ipc, PlayerError

NO_REPLY = "no reply from mpv"
LATE_WAIT = 20.0            # how long the original connection is read on for the answer that did not come in time
PROBE_WAIT = 20.0           # how long a cheap question may take before the player counts as not answering
RETRY_WAIT = 30.0           # the same request once more, with this much patience (as information only)
CHEAP = ("pid", "idle-active", "core-idle", "time-pos", "estimated-vf-fps", "frame-drop-count", "vo-delayed-frame-count")
SAFE_AGAIN = ("get_property", "set_property", "screenshot-to-file")     # asking these twice changes nothing
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0)                        # seconds; the last column is "2 s and more"

_local = threading.local()
_installed = 0
_lock = threading.Lock()
_real_socket = socket.socket
STATS = {}                  # kind of request: [count, failures, the longest, a count per bucket]
SLOWEST = []                # (seconds, kind, where, seconds since the player first answered, error or "")
REPORTS = []                # the reports made, as text (the tests of this file read them)


class _Socket(socket.socket):
    """A socket that notes what it receives and when, and that its watcher can keep open after close()."""

    def recv(self, *a):
        chunk = _real_socket.recv(self, *a)
        notes = getattr(self, "pvj_notes", None)
        if notes is not None:
            notes.append((time.monotonic(), chunk))
        return chunk

    def close(self):
        if getattr(self, "pvj_hold", False):
            self.pvj_closed = time.monotonic()      # the client has given up; the watcher reads on and closes it
            return
        _real_socket.close(self)


def _make(*a, **k):
    s = _Socket(*a, **k)
    rec = getattr(_local, "rec", None)
    if rec is not None and rec.get("sock") is None:
        s.pvj_notes, s.pvj_hold = rec["chunks"], True
        rec["sock"] = s
    return s


class _SocketModule:
    """The `socket` module as pvj.player sees it while a watch is on: only `socket.socket` differs."""
    socket = staticmethod(_make)

    def __getattr__(self, name):
        return getattr(socket, name)


def install():
    global _installed
    with _lock:
        if _installed == 0:
            player_module.socket = _SocketModule()
        _installed += 1


def uninstall():
    global _installed
    with _lock:
        _installed = max(0, _installed - 1)
        if _installed == 0:
            player_module.socket = socket


def kind_of(command, after_change=False):
    name = str(command[0]) if command else "?"
    if name in ("get_property", "set_property") and len(command) > 1:
        name += " " + str(command[1])
    if name == "screenshot-to-file" and after_change:
        name += " (the first after a change of shaders)"
    return name


def note(kind, took, error, where="", age=None):
    with _lock:
        row = STATS.setdefault(kind, [0, 0, 0.0, [0] * (len(BUCKETS) + 1)])
        row[0] += 1
        row[1] += 1 if error and (error == NO_REPLY or error.startswith("ipc error")) else 0      # a refusal is an answer
        row[2] = max(row[2], took)
        row[3][next((i for i, edge in enumerate(BUCKETS) if took < edge), len(BUCKETS))] += 1
        SLOWEST.append((round(took, 3), kind, where, None if age is None else round(age, 2), error or ""))
        SLOWEST.sort(key=lambda r: -r[0])
        del SLOWEST[12:]


def messages(chunks):
    """[(when, message)] of the whole lines in what a connection received."""
    out, buf = [], b""
    for when, chunk in chunks:
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                out.append((when, msg))
    return out


def told(msgs, start, rid):
    """What a connection received, in words: events by name with a count, answers with their number and time."""
    events, lines = {}, []
    for when, msg in msgs:
        if "event" in msg:
            name = str(msg["event"]) + (":" + str(msg.get("name")) if msg.get("name") else "")
            first = events.setdefault(name, [0, when - start, when - start])
            first[0] += 1
            first[2] = when - start
        else:
            lines.append("an answer to request %s (%s) after %.3f s: %s" % (
                msg.get("request_id"), "the one asked" if msg.get("request_id") == rid else "NOT the one asked",
                when - start, str(msg.get("error"))))
    for name, (n, first, last) in sorted(events.items(), key=lambda kv: kv[1][1]):
        lines.append("event %s x%d (%.3f to %.3f s)" % (name, n, first, last))
    return lines or ["nothing at all"]


def threads_of(pid):
    """{thread id: (name, state, clock ticks of processor time)} of a process, or {} where /proc has no such thing."""
    out = {}
    try:
        for tid in os.listdir("/proc/%d/task" % pid):
            with open("/proc/%d/task/%s/stat" % (pid, tid)) as f:
                s = f.read()
            rest = s[s.rindex(")") + 2:].split()
            out[tid] = (s[s.index("(") + 1:s.rindex(")")], rest[0], int(rest[11]) + int(rest[12]))
    except (OSError, ValueError, IndexError):
        pass
    return out


def busy(pid, seconds=0.5):
    """What each thread of a process did in `seconds`: its state, and the share of one processor it used."""
    a = threads_of(pid)
    if not a:
        return ["no /proc here, or the process is gone"]
    time.sleep(seconds)
    b = threads_of(pid)
    tick = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
    rows = []
    for tid, (name, state, used) in sorted(b.items(), key=lambda kv: -(kv[1][2] - a.get(kv[0], kv[1])[2])):
        share = (used - a[tid][2]) / tick / seconds if tid in a else 0.0
        rows.append("%s %s %3.0f%%" % (name, state, share * 100))
    return ["%d threads: " % len(b) + "; ".join(rows[:14])]


def pid_named(name):
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    with open("/proc/%s/comm" % entry) as f:
                        if f.read().strip() == name:
                            return int(entry)
                except OSError:
                    continue
    except OSError:
        pass
    return None


def load():
    try:
        return "%.2f %.2f %.2f on %s processors" % (os.getloadavg() + (os.cpu_count(),))
    except (OSError, AttributeError):
        return "unknown"


def tail(path, lines=70):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 24000))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


class WatchedIpc(Ipc):
    """The player's client, timed, and with a report when a request gets no reply. See the top of this file."""

    def __init__(self, path, timeout=2.0, where=lambda: "", pid=lambda: None, log_path=None, out=None,
                 late_wait=LATE_WAIT, probe_wait=PROBE_WAIT, retry_wait=RETRY_WAIT):
        super().__init__(path, timeout)
        self.where, self.pid, self.log_path = where, pid, log_path
        self.out = out
        self.late_wait, self.probe_wait, self.retry_wait = late_wait, probe_wait, retry_wait
        self.born = None            # when this player first answered
        self.recent = []            # the last requests: (kind, seconds, error or "", when)
        self.changed = False        # the shaders were changed since the last screenshot

    def request(self, *command):
        rec = {"chunks": [], "sock": None}
        _local.rec = rec
        start = time.monotonic()
        kind = kind_of(command, self.changed)
        error = None
        try:
            return Ipc.request(self, *command)
        except PlayerError as e:
            error = str(e)
            if error == NO_REPLY or error.startswith("ipc error"):
                try:
                    self.report(command, self._id, start, rec, error)
                except Exception as x:                  # the report must never change how the request ended
                    self.say(["the report itself failed: %r" % (x,)])
            raise
        finally:
            _local.rec = None
            took = time.monotonic() - start
            sock = rec["sock"]
            if sock is not None:
                sock.pvj_hold = False
                sock.close()
            if error is None and self.born is None:
                self.born = time.monotonic()
            if command and command[0] == "set_property" and len(command) > 1 and command[1] in ("glsl-shaders", "fbo-format"):
                self.changed = True
            elif command and command[0] == "screenshot-to-file":
                self.changed = False
            age = None if self.born is None else time.monotonic() - self.born
            note(kind, took, error, self.where(), age)
            self.recent.append((kind, took, error or "", time.monotonic()))
            del self.recent[:-10]

    def say(self, lines):
        text = "\n".join("MPV-WATCH  " + line for line in lines)
        REPORTS.append(text)
        out = self.out or sys.stdout
        out.write(text + "\n")
        out.flush()

    def ask(self, wait, *command):
        """(answer or the error, seconds) of a request on a connection of its own, unwatched, with its own patience."""
        t = time.monotonic()
        try:
            got = Ipc(self.path, timeout=wait).request(*command)
        except PlayerError as e:
            got = "FAILED: %s" % e
        return got, time.monotonic() - t

    def report(self, command, rid, start, rec, error=NO_REPLY):
        waited = time.monotonic() - start
        now = time.monotonic()
        pid, sock = self.pid(), rec["sock"]
        lines = ["=" * 100,
                 "%s: %s" % (error, self.where()),
                 "the request: %s, number %d; waited %.3f s (the client waits %.1f s)" % (json.dumps(list(command))[:300], rid, waited, self.timeout),
                 "this player first answered %s ago; load %s" % ("%.2f s" % (now - self.born) if self.born else "never", load()),
                 "the requests before it, oldest first: " + ("; ".join("%s %.0f ms%s, %.2f s ago" % (k, t * 1000, " (%s)" % e if e else "", now - at)
                                                               for k, t, e, at in self.recent) or "none"),
                 "received on its connection before the client gave up:"]
        seen = len(rec["chunks"])
        lines += ["    " + x for x in told(messages(rec["chunks"]), start, rid)]
        alive = pid is not None and bool(threads_of(pid) or _alive(pid))
        lines.append("the player's process %s: %s" % (pid, "there" if alive else "GONE"))
        self.say(lines)
        lines = []
        if pid is not None:
            lines += ["the player's threads over half a second (name, state, share of a processor):"] + ["    " + x for x in busy(pid)]
            x = pid_named("Xvfb")
            if x:
                lines += ["the display server's:"] + ["    " + r for r in busy(x, 0.3)]
        # the original connection is read on, by a thread of its own, while cheap questions go to new connections
        late = {}
        reader = threading.Thread(target=self._read_on, args=(sock, rec, rid, late), daemon=True)
        reader.start()
        lines.append("cheap questions on new connections while the answer is still awaited:")
        for prop in CHEAP:
            got, took = self.ask(self.probe_wait, "get_property", prop)
            lines.append("    %-24s %-28s after %.3f s (%.3f s after the request was sent)" % (prop, str(got)[:28], took, time.monotonic() - start))
        self.say(lines)
        reader.join(self.late_wait + 2)
        lines = []
        if "at" in late:
            lines.append("THE ANSWER CAME, %.3f s after the request was sent (%.3f s after the client gave up): %s"
                         % (late["at"] - start, late["at"] - start - waited, late["error"]))
        else:
            lines.append("NO ANSWER on the original connection within %.1f s of the request (%s)" % (time.monotonic() - start, late.get("end", "still waiting")))
        lines.append("received on it after the client gave up:")
        lines += ["    " + x for x in told(messages(rec["chunks"])[len(messages(rec["chunks"][:seen])):], start, rid)]
        if pid is not None:
            lines += ["the player's threads now:"] + ["    " + x for x in busy(pid)]
        lines.append("load now %s" % load())
        if command and command[0] in SAFE_AGAIN:
            again = list(command)
            if again[0] == "screenshot-to-file":
                again[1] = str(again[1]) + ".again.png"
            got, took = self.ask(self.retry_wait, *again)
            lines.append("the same request once more, with %.0f s of patience: %.3f s%s (information only: the test has failed)"
                         % (self.retry_wait, took, ", %s" % got if isinstance(got, str) and got.startswith("FAILED") else ""))
            if again[0] == "screenshot-to-file":
                got, took = self.ask(self.retry_wait, *again)
                lines.append("and a third time: %.3f s" % took)
                try:
                    os.unlink(again[1])
                except OSError:
                    pass
        else:
            lines.append("not asked again: %s is not a request that can be repeated without changing something" % command[0])
        if self.log_path:
            log = tail(self.log_path)
            lines += ["the end of the player's own log (%s):" % self.log_path] + ["    " + x for x in log] if log else ["the player's log %s could not be read" % self.log_path]
        else:
            lines.append("the player writes no log of its own (PVJ_MPV_LOG=1 makes it)")
        lines.append("=" * 100)
        self.say(lines)

    def _read_on(self, sock, rec, rid, late):
        """Keep reading the connection the client closed, until the answer to request `rid` or `late_wait`."""
        if sock is None:
            late["end"] = "there was no connection"
            return
        end = time.monotonic() + self.late_wait
        seen = 0
        try:
            while "at" not in late:
                msgs = messages(rec["chunks"])
                for when, msg in msgs[seen:]:
                    if msg.get("request_id") == rid:
                        late["at"], late["error"] = when, str(msg.get("error"))
                seen = len(msgs)
                left = end - time.monotonic()
                if "at" in late or left <= 0:
                    break
                sock.settimeout(left)
                if not sock.recv(65536):
                    late["end"] = "the player closed the connection"
                    break
            if "at" not in late and "end" not in late:
                late["end"] = "gave up waiting"
        except socket.timeout:
            late["end"] = "gave up waiting"
        except OSError as e:
            late["end"] = "the connection failed: %s" % e


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log_args(folder):
    """(extra arguments for mpv, the log's path or None): with PVJ_MPV_LOG=1 the player writes its own log."""
    if os.environ.get("PVJ_MPV_LOG") != "1":
        return [], None
    path = os.path.join(folder, "mpv.log")
    return ["--log-file=" + path], path


def watch(case, real, log_path=None):
    """Put a watch on the player `real` of the test `case`, until the test ends."""
    install()
    case.addCleanup(uninstall)
    real.ipc = WatchedIpc(real.socket_path, real.ipc.timeout, where=case.id, pid=lambda: getattr(getattr(real, "_proc", None), "pid", None),
                          log_path=log_path)
    return real


def summary():
    """What every kind of request took, as lines."""
    with _lock:
        rows = sorted(STATS.items(), key=lambda kv: -kv[1][2])
        slow = list(SLOWEST)
    if not rows:
        return []
    head = "%-62s %6s %5s %8s  %s" % ("request", "count", "fail", "longest", "  ".join("<%g" % b for b in BUCKETS) + "  2+ s")
    lines = ["what the player's answers took (tests/mpv_watch.py); the client waits 2 s", head]
    for kind, (n, bad, longest, buckets) in rows:
        if longest >= 0.1 or bad or kind.startswith("screenshot"):
            lines.append("%-62s %6d %5d %7.3fs  %s" % (kind[:62], n, bad, longest, "  ".join("%d" % c for c in buckets)))
    lines.append("the slowest: " + "; ".join("%.3f s %s%s%s in %s" % (t, k, " (%s)" % e if e else "", "" if age is None else ", player %.1f s old" % age,
                                                                      w.split(".")[-1][:48]) for t, k, w, age, e in slow[:8]))
    return lines


def _at_exit():
    lines = summary()
    if lines:
        print("\n".join("MPV-WATCH  " + line for line in lines))
    path = os.environ.get("PVJ_MPV_WATCH_OUT")
    if path and STATS:
        with open(path, "a") as f:
            f.write(json.dumps({"stats": STATS, "slowest": SLOWEST, "reports": len(REPORTS)}) + "\n")


atexit.register(_at_exit)
