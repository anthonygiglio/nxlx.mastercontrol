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
120 s, then 300 s). There is no total: a loose cable never leaves the box on classic for the rest of the boot, it only
makes it wait longer. The wait starts again at 30 s once the last switch is more than RESET_SECONDS old, i.e. pvj-render
has drawn that long since. The first switch of a boot has no wait.

The switch times are kept in STATE in the player's state folder (`paths.player_state_dir`, a StateDirectory= of the
unit: owned by the account that runs the player, kept across any restart of the service and of the box), keyed by the
boot's id. The watcher tries to write the file when it starts; if it cannot, it logs that once and keeps a floor of
GAP_SECONDS after its own start for every switch, so that a count that cannot be kept never allows a quick loop.
A crash fallback is never watched: the screen is not what failed there.
"""
import os
import signal
import sys
import time

from . import hardware, paths, screenmode

STATE = "late-screen.json"      # in the player's state folder: {"boot": id, "switches": [monotonic seconds, ...]}
POLL_SECONDS = 1.0
SETTLE_SECONDS = 3.0            # a screen must stay connected this long (its modes are read after the kernel has them)
GAP_SECONDS = 30.0              # the floor of a watcher that cannot keep a count
WAITS = (30.0, 60.0, 120.0, 300.0)      # the wait after the 1st, 2nd, 3rd and later switch of a run of switches
RESET_SECONDS = 600.0           # a switch this old (pvj-render has drawn since) no longer counts: the waits start again
KEEP = 8                        # switches kept in the file

# renderer.Supervisor.run's words for the two ends that exit 2 gives: pvj-render ends with 2 when it has no screen with a
# mode (render/src/out.c), before it answers ("did not start") or after it has drawn, when the screen went away and
# presenting failed ("ended twice in a row"). Any other exit code is a crash, and stays on classic.
NO_SCREEN_WHY = ("pvj-render did not start (exit 2)", "pvj-render ended twice in a row (exit 2)")


def connected_screens(sysfs):
    """The names of the screens pvj-render could draw on in hardware.drm_connectors()' list: status connected AND at
    least one mode (a projector says "connected" before its EDID is read, and pvj-render takes only a connector with
    modes: out.c, pick_connector). A writeback connector is not a screen."""
    return sorted(c["connector"] for c in sysfs
                  if c.get("status") == "connected" and c.get("modes") and not str(c.get("connector", "")).lower().startswith("writeback"))


def no_screen_fallback(why, sysfs):
    """True when pvj-render ended with exit 2 (NO_SCREEN_WHY) AND no screen with a mode is connected now: the one kind of
    fallback that a screen can undo. A board whose /sys lists no connector at all cannot say; any other end is a crash."""
    return bool(sysfs) and why in NO_SCREEN_WHY and not connected_screens(sysfs)


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
        self.floor = False          # the count cannot be written: every switch waits GAP_SECONDS after this watcher began
        self._probe()
        self.since = None           # when the screens now connected were first seen so
        self.spent = False

    def _probe(self):
        """Write the file as it is (or empty) now, to know that it can be kept; if not, say so once and use the floor."""
        try:
            screenmode.write_json(self.path, {"boot": self.boot, "switches": self._switches()})
        except OSError as e:
            self.floor = True
            self.log("pvj-player: renderer: the count of switches cannot be kept (%s); every switch waits %d s after "
                     "the player starts" % (e.strerror or e, self.gap))

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
        elif self.floor and now - self.began < self.gap:
            return False            # the count cannot be kept: never sooner than the floor after this watcher began
        switches = switches[-KEEP:]
        wait_next = self.waits[min(run + 1, len(self.waits)) - 1]
        self.spent = True
        switches.append(now)
        try:
            screenmode.write_json(self.path, {"boot": self.boot, "switches": switches})
        except OSError:
            pass                    # said at the start if the file cannot be written; the next watcher then has the floor
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


def still_running(pid, began):
    """True while `pid` is the process whose start time was `began` (not gone, and not a pid reused by another)."""
    return start_time(pid) == began


def end_process(pid, began):
    """SIGTERM to `pid` only if it is still the process that began at `began`; True when it was sent."""
    if not still_running(pid, began):
        return False
    os.kill(pid, signal.SIGTERM)        # mpv quits; Restart=always starts the service again
    return True


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
        log("pvj-player: renderer: no screen with a mode is connected; watching for one, to start pvj-render when it comes")
        child = os.fork()
    except Exception:
        return False
    if child == 0:
        try:
            if os.fork() == 0:
                os.setsid()
                Watcher(rundir, log, boot=boot, end_player=lambda: end_process(pid, began),
                        alive=lambda: still_running(pid, began)).watch()
                os._exit(0)
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.waitpid(child, 0)
    return True
