# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A screen that comes after the player has started (D95, D90).

The order at a show is the Pi boots, then the projector warms up. pvj-render cannot start without a screen
(`render/src/out.c`: "no screen is connected", exit 2), so the player's service falls back to classic mpv, and classic
mpv takes a screen when one comes. Nothing then brought the box back to pvj-render, though the owner chose it.

When the fallback was only for want of a screen, the player starts a small watcher before it becomes mpv. The watcher
reads the connector status files under /sys/class/drm (any connector on any card, none hard-coded: the same
`hardware.drm_connectors` that screenmode reads) once a second. When a screen has stayed connected for SETTLE_SECONDS,
it ends the player's process, and systemd (`Restart=always`) starts the service again by its normal path: the screen's
mode is chosen as at any start and pvj-render is tried, with the same gate as ever (a pvj-render that fails to start
or ends twice stays on classic). It is not a restart loop: after a switch the next one waits WAITS in turn (30 s, 60 s,
120 s, then 300 s), counted in a file of the player's folder, which the unit keeps across its own restarts. There is no
total: a loose cable never leaves the box on classic for the rest of the boot, it only makes it wait longer. The wait
starts again at 30 s once the last switch is more than RESET_SECONDS old, i.e. pvj-render has drawn that long since. With
no count (the file gone), a watcher still waits GAP_SECONDS after its own start, so the wait is never shorter than 30 s.
A crash fallback is never watched: the screen is not what failed there.
"""
import os
import signal
import sys
import time

from . import hardware, screenmode

STATE = "late-screen.json"      # in the player's folder: {"boot": id, "switches": [monotonic seconds, ...]}
POLL_SECONDS = 1.0
SETTLE_SECONDS = 3.0            # a screen must stay connected this long (its modes are read after the kernel has them)
GAP_SECONDS = 30.0              # the shortest wait, and the wait of a watcher that has no count
WAITS = (30.0, 60.0, 120.0, 300.0)      # the wait after the 1st, 2nd, 3rd and later switch of a run of switches
RESET_SECONDS = 600.0           # a switch this old (pvj-render has drawn since) no longer counts: the waits start again
KEEP = 8                        # switches kept in the file

NO_SCREEN_START = "pvj-render did not start (exit"      # renderer.Supervisor.run's words for an end before it answered


def connected_screens(sysfs):
    """The names of the connected screens in hardware.drm_connectors()' list (a writeback connector is not one)."""
    return sorted(c["connector"] for c in sysfs
                  if c.get("status") == "connected" and not str(c.get("connector", "")).lower().startswith("writeback"))


def no_screen_fallback(why, sysfs):
    """True when pvj-render did not start AND no screen is connected now: the one fallback that a screen can undo. A
    board whose /sys lists no connector at all cannot say, and a crash after pvj-render drew ("ended twice") is not it."""
    return bool(sysfs) and isinstance(why, str) and why.startswith(NO_SCREEN_START) and not connected_screens(sysfs)


def start_time(pid):
    """Field 22 of /proc/PID/stat (clock ticks after boot), which tells one process of a pid from the next one, or None."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class Watcher:
    """`step()` once a second until it says the time has come. The clock, the sysfs reading, the file and the way the
    player is ended are all handed in, so a test runs it on a fake /sys tree and a fake clock."""

    def __init__(self, rundir, log, sysfs=hardware.drm_connectors, clock=time.monotonic, sleep=time.sleep,
                 boot=None, end_player=None, alive=None, settle=SETTLE_SECONDS, gap=GAP_SECONDS, waits=WAITS, reset=RESET_SECONDS):
        self.path = os.path.join(rundir, STATE)
        self.log, self.sysfs, self.clock, self.sleep = log, sysfs, clock, sleep
        self.boot = screenmode.boot_id() if boot is None else boot
        self.end_player = end_player or (lambda: None)
        self.alive = alive or (lambda: True)
        self.settle, self.gap, self.waits, self.reset = settle, gap, waits, reset
        self.began = clock()
        self.since = None           # when the screens now connected were first seen so
        self.spent = False

    def _switches(self):
        data = screenmode.read_json(self.path)
        if data.get("boot") != self.boot or not isinstance(data.get("switches"), list):
            return []
        return [t for t in data["switches"] if isinstance(t, (int, float)) and not isinstance(t, bool)]

    def step(self):
        """True once, when the player should be ended for a screen that has come; False before, and for ever after."""
        if self.spent:
            return False
        try:
            names = connected_screens(self.sysfs())
        except Exception:
            names = []
        now = self.clock()
        if not names:
            self.since = None       # a screen that goes again starts the wait over
            return False
        if self.since is None:
            self.since = now
        if now - self.since < self.settle:
            return False
        switches = self._switches()
        run = 0                     # how many switches in a row, each less than `reset` after the one before (or before now)
        edge = now
        for t in reversed(switches):
            if edge - t >= self.reset:
                break
            run, edge = run + 1, t
        if run:
            wait = self.waits[min(run, len(self.waits)) - 1]
            if now - switches[-1] < wait:
                return False        # the screen may be flapping: wait out the wait, then look again
        elif now - self.began < self.gap:
            return False            # no count of switches: never sooner than the shortest wait after this watcher began
        switches = switches[-KEEP:]
        wait_next = self.waits[min(run + 1, len(self.waits)) - 1]
        self.spent = True
        switches.append(now)
        try:
            screenmode.write_json(self.path, {"boot": self.boot, "switches": switches})
        except OSError as e:
            self.log("pvj-player: renderer: the count of switches was not written (%s)" % e)
        self.log("pvj-player: renderer: a screen is connected now (%s) and pvj-render did not start only because there "
                 "was none; restarting the player once so that pvj-render draws (the next switch waits at least %d s)"
                 % (", ".join(names), wait_next))
        self.end_player()
        return True

    def watch(self):
        while self.alive():
            if self.step():
                return True
            self.sleep(POLL_SECONDS)
        return False


def start(rundir, log, env=None, boot=None):
    """Called by the player just before it becomes mpv, after a fallback that a screen can undo (no_screen_fallback).
    Starts the watcher in a detached grandchild (as detach_socket_opener does: no zombie, and the exec of mpv leaves
    it running) when this is a systemd service (INVOCATION_ID: a player started by hand is not restarted by
    anyone). Never raises. Returns True when a watcher was started."""
    env = os.environ if env is None else env
    try:
        if not env.get("INVOCATION_ID"):
            return False
        pid = os.getpid()
        began = start_time(pid)
        if began is None:
            return False

        def end_player():
            if start_time(pid) == began:        # still this process, not a pid that something else has now
                os.kill(pid, signal.SIGTERM)    # mpv quits; Restart=always starts the service again
        child = os.fork()
    except Exception:
        return False
    if child == 0:
        try:
            if os.fork() == 0:
                os.setsid()
                Watcher(rundir, log, boot=boot, end_player=end_player, alive=lambda: start_time(pid) == began).watch()
                os._exit(0)
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.waitpid(child, 0)
    return True
