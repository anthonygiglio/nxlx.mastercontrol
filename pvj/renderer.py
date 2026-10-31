# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Which program draws the screen: classic mpv, or pvj-render, the box's own renderer (render/, D95).

pvj-render is a separate program (GPL-3.0-or-later, in render/) that speaks the same JSON socket as mpv, so nothing
that talks to the player changes: the switch is which program the player's service starts.

Who does what, as for the screen's mode (pvj/screenmode.py, D90):

* The panel owns the setting (`renderer` in the settings: {"use": "classic"|"new", "detail": "auto"|540|720|"output"},
  since D101 an optional "sources": 1 to 8, and since D108 an optional "final": "full"|720, read with defaults, no
  schema change; no key is this board's own number and Automatic). The player may not read settings.json, so the
  panel writes the wish alone to
  WANTED in the state folder, at its start and on a change, and restarts the player for a change (`Renderer`).
* The player's service (`pvj-player serve`, the display's account) reads WANTED just before it would become mpv. With
  "new" it stays a small supervisor instead (`serve`): it starts pvj-render with the screen's arguments that
  screenmode chose for mpv, waits for its socket to answer, and watches it. It falls back to classic mpv by itself
  (the same exec as before) when pvj-render is missing, does not start, or ends twice in a row, and writes what it
  did and why to REPORT in its own folder, which the panel reads as data from a less trusted account.
* While pvj-render draws, the supervisor also reads WANTED every half second. When it no longer says what this run
  was started with (the Owner chose classic, or another detail), the supervisor ends pvj-render itself: it asks it
  to quit on its socket, then sends SIGTERM after QUIT_SECONDS, then SIGKILL after TERM_SECONDS more, writes how it
  ended to REPORT (the page shows it), and ends the service, which systemd starts again with the new wish. The
  panel's own quit on the socket is only the polite half: on the first Pi run a jammed pvj-render never heard it,
  and the box stayed on the new renderer until the service was restarted by hand (2026-10-18).
* The final pass may draw at 720 lines and let the display's scaler bring it to the output (D108). `final` is the
  setting: "full" is pvj-render as it always was; 720 is always on; "auto" allows it on a Pi 3 or 4, and the
  supervisor, which looks at pvj-render's counters at every WATCH_SECONDS anyway, applies `final_rule`.
"""

import os
import signal
import subprocess
import sys
import time

from . import hardware, paths, screenmode

USES = ("classic", "new")
DETAILS = ("auto", 540, 720, "output")
DEFAULT = {"use": "classic", "detail": "auto"}
WANTED = "renderer.json"                # in the state folder: the panel writes it, the player reads it
REPORT = paths.RENDERER_REPORT          # in the player's folder: the player writes it, the panel reads it
START_SECONDS = 10.0                    # pvj-render must answer on its socket this soon after it is started
HEALTHY_SECONDS = 600.0                 # an end after this long a run is not "in a row" with the one before
STOP_SECONDS = 5.0                      # systemd's stop: this long for pvj-render to end before it is killed
QUIT_SECONDS = 2.0                      # a switch: this long for pvj-render to end when asked on its socket, then SIGTERM
TERM_SECONDS = 3.0                      # ... and this long after SIGTERM, then SIGKILL
WATCH_SECONDS = 0.5                     # how often the supervisor reads WANTED while pvj-render draws
SMALL_BOARDS = ("pi3", "pi4")           # where Automatic draws 540 lines inside (the spike of 2026-10-16, D95)
# Sources (D101): clip layers beside the player's own picture, which the mapping shows per surface. A board takes at
# most its cap, and without a choice gets its default. Measured on the owner's Pi 4 (2026-10-20), software H.264
# decode alone: 1080p 83 fps for one stream, 44 each for two, 22 each for four; 720p 161, 88, 44; 540p 229, 137, 67,
# and 46 each for six. With room left for drawing, a Pi 4 carries about 2 at 1080p, 4 at 720p, 6 at 540p. So a Pi 4
# has 4: that is what it carries at 720p, an idle source costs nothing, and the panel warns when a 1080p clip goes
# to a source while two others play. A Pi 3 (not measured, about half a Pi 4) has 2. Other boards: 4 by default, 8
# at most (the renderer's own bound).
SOURCES_MAX = 8
FINALS = ("auto", "full", 720)          # the final pass (D108): Automatic, always the output's size, always 720 lines
SOURCE_CAP = {"pi3": 2, "pi4": 4}
SOURCE_DEFAULT = {"pi3": 2, "pi4": 4}
SOURCES_OTHER = 4


# Automatic for the final pass (D108): 720 lines when the box is busy, the output's size while it has room, because
# the scaler costs something on the screen (mapped edges and renderer text a little softer, thin lines in a squeezed
# mapped area can break into dashes). Busy is more than BUSY_SHARE of the vblanks missed over BUSY_SECONDS. It goes back
# only when the load has become lighter than it was at the switch and has stayed so for LIGHTER_SECONDS, and at least
# HOLD_SECONDS after the switch down: so it never needs a trial at full size to know, and a load that stays as it
# was stays at 720 for good, which is what stops it flapping.
BUSY_SHARE = 0.05
BUSY_SECONDS = 2.0
LIGHTER_SECONDS = 5.0
HOLD_SECONDS = 30.0


def final_rule(state, now, missed, load, hz):
    """One look at the box: what the final pass should draw at. `state` is what the last look returned, {"final":
    "full"} at the start; `now` seconds on any clock, `missed` pvj-render's vblanks missed so far, `load` the number of
    things it draws now (sources that play, their programs, the mapping), `hz` the output's rate. Returns the next
    state, whose "final" is "full" or 720 and whose "why" says why in words. Asks nothing and changes nothing, so
    a test can drive it with made-up numbers."""
    state = dict(state)
    if state.get("t0") is None or missed < state["m0"]:
        state.update(t0=now, m0=missed)
    if state["final"] == "full":
        seconds = now - state["t0"]
        if seconds < BUSY_SECONDS:
            return state
        share = (missed - state["m0"]) / (seconds * hz) if hz > 0 else 0.0
        state.update(t0=now, m0=missed)
        # With nothing drawn there is nothing to lighten: a stall of the box is no reason, and "lighter than nothing"
        # could never bring it back.
        if share > BUSY_SHARE and load > 0:
            state.update(final=720, since=now, load=load, lighter=None,
                         why="the box was busy: %d in 100 frames came late with a load of %d" % (round(share * 100), load))
        return state
    if load >= state["load"]:
        state["lighter"] = None
        return state
    if state.get("lighter") is None:
        state["lighter"] = now
    if now - state["lighter"] >= LIGHTER_SECONDS and now - state["since"] >= HOLD_SECONDS:
        state.update(final="full", t0=now, m0=missed, why="")
    return state


def source_cap(board_kind):
    return SOURCE_CAP.get(board_kind, SOURCES_MAX)


def sources_for(board_kind, chosen=None):
    """The sources pvj-render is started with on this board: the Owner's number, or the board's default, never over
    its cap."""
    n = chosen if isinstance(chosen, int) and not isinstance(chosen, bool) and 1 <= chosen <= SOURCES_MAX else \
        SOURCE_DEFAULT.get(board_kind, SOURCES_OTHER)
    return min(n, source_cap(board_kind))


def binary(env=None):
    """Where the program is built: render/pvj-render beside pvj/ in this release (PVJ_RENDER_BIN overrides, tests)."""
    env = os.environ if env is None else env
    return env.get("PVJ_RENDER_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render", "pvj-render")


def available(env=None):
    path = binary(env)
    return os.path.isfile(path) and os.access(path, os.X_OK)


HEAVY_LINES = 720                       # a clip taller than this is "1080p" for the warning below
WARN_TEXT = "this may drop frames on this box; 720p clips are lighter"


def source_warning(rows, target, board_kind):
    """The panel's warning (D101) when a clip of more than 720 lines plays on source `target` while two or more other
    sources play, on a board whose numbers say that is past what it decodes (a Pi 3 or 4); else None. `rows`:
    Player.sources_state()."""
    if board_kind not in SMALL_BOARDS:
        return None
    mine = [r for r in rows if r.get("source") == target]
    others = [r for r in rows if r.get("source") != target and not r.get("idle")]
    height = mine[0].get("height") if mine else None
    if isinstance(height, int) and height > HEAVY_LINES and len(others) >= 2:
        return WARN_TEXT
    return None


def clean(data):
    """{"use", "detail"} from a settings section or the wanted file; anything else is the default. "sources" (D101)
    only when it is a number from 1 to SOURCES_MAX: no key is the board's own number, so a file without it is as
    before. "final" (D108) the same way, only when it is "full" or 720."""
    data = data if isinstance(data, dict) else {}
    use = data.get("use") if data.get("use") in USES else DEFAULT["use"]
    detail = data.get("detail")
    if isinstance(detail, bool) or detail not in DETAILS:
        detail = DEFAULT["detail"]
    out = {"use": use, "detail": detail}
    n = data.get("sources")
    if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= SOURCES_MAX:
        out["sources"] = n
    final = data.get("final")
    if final == "full" or (final == 720 and isinstance(final, int) and not isinstance(final, bool)):
        out["final"] = final                # no key is Automatic: a file without it is as before (D108)
    return out


def wanted_path(env=None):
    return os.path.join(paths.state_dir(env), WANTED)


def detail_arg(detail, board_kind):
    """--detail for pvj-render: lines inside, or the output's own size. Automatic: 540 on a Pi 3 or 4, else the output."""
    if detail == "auto":
        detail = 540 if board_kind in SMALL_BOARDS else "output"
    return "--detail=%s" % detail


def final_arg(final, board_kind):
    """--final for pvj-render (D108), or None: without it pvj-render flips as it always has. Automatic is allowed on
    a Pi 3 or 4 only; elsewhere the GPU has the room, so it is off."""
    if final == 720:
        return "--final=720"
    return "--final=auto" if final == "auto" and board_kind in SMALL_BOARDS else None


def command(socket_path, screen_args, detail, board_kind, env=None, sources=None, final=None):
    """pvj-render's argv: the socket, the screen's arguments as screenmode chose them for mpv, the detail, mpv's
    cheap scalers on a Pi (as classic's --profile=fast), the number of sources (D101) and the final pass (D108)."""
    args = [binary(env), "--socket=" + socket_path]
    args += [a for a in screen_args if a.startswith(("--drm-device=", "--drm-connector=", "--drm-mode="))]
    args.append(detail_arg(detail, board_kind))
    if board_kind in SMALL_BOARDS:
        args.append("--fast")
    args.append("--sources=%d" % sources_for(board_kind, sources))
    extra = final_arg(final, board_kind)
    if extra:
        args.append(extra)
    return args


def chosen(rundir, extra_args, windowed, env=None):
    """(use, detail, why) for the start that is about to happen. "new" only where pvj-render can draw in phase 1: on
    a box without a desktop, where the player draws through DRM itself (`--gpu-context=drm`)."""
    want = clean(screenmode.read_json(wanted_path(env)))
    if want["use"] != "new":
        return "classic", want["detail"], ""
    if windowed or "--gpu-context=drm" not in extra_args:
        return "classic", want["detail"], "pvj-render draws only on a box without a desktop (phase 1)"
    if not available(env):
        return "classic", want["detail"], "pvj-render is not built on this box"
    return "new", want["detail"], ""


def write_report(rundir, data, log=None):
    data = dict(data, pid=os.getpid(), at=int(time.time()), boot=screenmode.boot_id())
    try:
        screenmode.write_json(os.path.join(rundir, REPORT), data)
    except OSError as e:
        if log:
            log("pvj-player: renderer: the report was not written (%s)" % e)


# ---- the player's side: the supervisor ---------------------------------------------------------------------------
class Supervisor:
    """Runs pvj-render and says when to fall back. `run()` returns the reason to fall back to classic mpv, or ends
    the process (SystemExit) when pvj-render was asked to quit or systemd stops the service."""

    def __init__(self, cmd, socket_path, rundir, detail, log=print, spawn=None, clock=time.monotonic, sleep=time.sleep,
                 ready=None, start_seconds=START_SECONDS, healthy_seconds=HEALTHY_SECONDS, wanted=None, sources=None,
                 final=None):
        self.cmd, self.socket_path, self.rundir, self.detail = list(cmd), socket_path, rundir, detail
        self.sources = sources              # the Owner's number of sources this run was started with (None: the board's)
        self.final = final                  # the final pass this run was started with: "full" or 720 (None: Automatic)
        self.final_rule = None              # final_rule's state, while it decides for this run
        self.final_watch = False            # pvj-render was started with --final and has not said it cannot
        self.final_now = ("full", "")       # what the report says: what the final pass draws at, and why
        self.final_ipc = None
        self.log, self.clock, self.sleep = log, clock, sleep
        self.spawn = spawn or (lambda argv: subprocess.Popen(argv, stdin=subprocess.DEVNULL, close_fds=True))
        self.ready = ready or self._ready
        self.start_seconds, self.healthy_seconds = start_seconds, healthy_seconds
        self.wanted = wanted                # WANTED's path: a change of the wish ends this run (None: not watched)
        self.proc = None
        self.stopping = False
        self.stop_at = None                 # when systemd's stop came (the clock), for the kill after STOP_SECONDS

    def _ready(self, proc):
        """True once pvj-render answers on its socket with its own pid."""
        from .player import Ipc, PlayerError
        ipc = Ipc(self.socket_path, timeout=1.0)
        try:
            return ipc.request("get_property", "pid") == proc.pid
        except PlayerError:
            return False

    def _stop(self, signum=None, frame=None):
        """systemd's stop (SIGTERM) or Ctrl-C: passed on to pvj-render, which ends cleanly."""
        self.stopping = True
        if self.stop_at is None:
            self.stop_at = self.clock()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def _wish_changed(self):
        """The wish in WANTED, if it is no longer this run's ({"use": "new", "detail": self.detail}); else None. A
        missing or unreadable file is no change (the panel writes it whole, by a rename)."""
        if not self.wanted:
            return None
        data = screenmode.read_json(self.wanted)
        if not data:
            return None
        wish = clean(data)
        mine = {"use": "new", "detail": self.detail}
        if self.sources is not None:
            mine["sources"] = self.sources
        if self.final is not None:
            mine["final"] = self.final
        return wish if wish != mine else None

    def _ask_quit(self):
        from .player import Ipc, PlayerError
        try:
            Ipc(self.socket_path, timeout=1.0).request("quit")
            return True
        except PlayerError:
            return False

    def _switch(self, wish):
        """End pvj-render for a new wish whatever state it is in: asked on its socket, then SIGTERM, then SIGKILL.
        Returns how it ended, in words for the page."""
        self.log("pvj-player: renderer: the wish is now %s (%s); ending pvj-render" % (wish["use"], wish["detail"]))
        heard = self._ask_quit()
        how = None
        for sig, seconds, words in ((None, QUIT_SECONDS, "it quit when asked"),
                                    (signal.SIGTERM, TERM_SECONDS, "it did not quit within %d s when asked%s, so it was sent SIGTERM"
                                     % (QUIT_SECONDS, "" if heard else " (its socket did not answer)")),
                                    (signal.SIGKILL, 5.0, "it did not quit within %d s when asked%s nor %d s after SIGTERM, so it was killed (SIGKILL)"
                                     % (QUIT_SECONDS, "" if heard else " (its socket did not answer)", TERM_SECONDS))):
            if sig is not None and self.proc.poll() is None:
                try:
                    self.proc.send_signal(sig)
                except OSError:
                    pass
            try:
                self.proc.wait(seconds)
                how = words
                break
            except subprocess.TimeoutExpired:
                continue
        how = how or "it did not end even after SIGKILL"
        self.log("pvj-player: renderer: switched from pvj-render: %s" % how)
        write_report(self.rundir, {"chosen": wish["use"], "drawing": "classic" if wish["use"] == "classic" else "new",
                                   "why": "", "detail": wish["detail"], "switched": how}, self.log)
        return how

    def _end(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                self.proc.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
            except OSError:
                pass

    def _report(self, drawing, why):
        write_report(self.rundir, {"chosen": "new", "drawing": drawing, "why": why, "detail": self.detail,
                                   "final": self.final_now[0], "final_why": self.final_now[1]}, self.log)

    def _final_begin(self):
        """pvj-render is up: the final pass starts at the output's size, whatever the last run came to."""
        from .player import Ipc
        self.final_now = ("full", "")
        self.final_watch = any(a.startswith("--final=") for a in self.cmd)
        self.final_rule = {"final": "full"} if "--final=auto" in self.cmd else None
        self.final_ipc = Ipc(self.socket_path, timeout=1.0)

    def _final_say(self, final, why):
        if (final, why) != self.final_now:
            self.final_now = (final, why)
            self._report("new", "")

    def _final_tick(self):
        """A look at pvj-render's counters while it draws (D108): under Automatic the rule's word goes to pvj-render,
        and the report says what the final pass draws at and why. A pvj-render that does not answer is looked at
        again next time: it is not this watch's business."""
        if not self.final_watch:
            return
        from .player import PlayerError
        try:
            state = self.final_ipc.request("get_property", "pvj-render")
        except PlayerError:
            return
        if not isinstance(state, dict):
            return
        refused = state.get("final-why")
        if refused:
            self.final_watch = False            # said once by pvj-render, and it stays at the output's size
            self._final_say("full", refused if isinstance(refused, str) else "")
            return
        words = ""
        if self.final_rule is not None:
            before = self.final_rule["final"]
            self.final_rule = final_rule(self.final_rule, self.clock(), state.get("missed-vblanks") or 0, state.get("load") or 0,
                                         state.get("output-fps") or 60.0)
            words = self.final_rule.get("why", "")
            if self.final_rule["final"] != before:
                self.log("pvj-player: renderer: the final pass goes to %s (%s)" % (self.final_rule["final"], words or "the load is lighter"))
                try:
                    self.final_ipc.request("pvj-final", self.final_rule["final"])
                except PlayerError as e:
                    self.log("pvj-player: renderer: the final pass stays as it is (%s)" % e)
                    self.final_rule, self.final_watch = None, False
                    self._final_say("full", "pvj-render did not take the change")
                    return
        self._final_say(720 if state.get("final-lines") == 720 else "full", words)

    def run(self):
        ends = 0
        while True:
            try:
                os.unlink(self.socket_path)          # an old socket must not be taken for the new one's answer
            except OSError:
                pass
            started = self.clock()
            try:
                self.proc = self.spawn(self.cmd)
            except OSError as e:
                return "pvj-render could not be started (%s)" % (e.strerror or e)
            up = False
            while self.clock() - started < self.start_seconds and not self.stopping:
                if self.proc.poll() is not None:
                    break
                if self.ready(self.proc):
                    up = True
                    break
                self.sleep(0.1)
            if self.stopping:
                self._end()
                raise SystemExit(0)
            if not up:
                code = self.proc.poll()
                self._end()
                return ("pvj-render did not start (exit %s)" % code) if code is not None else \
                    "pvj-render did not answer within %d seconds" % self.start_seconds
            self._final_begin()
            self._report("new", "")
            self.log("pvj-player: renderer: pvj-render is drawing (pid %d)" % self.proc.pid)
            while True:
                try:
                    code = self.proc.wait(WATCH_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if self.stopping:
                    if self.clock() - self.stop_at >= STOP_SECONDS:
                        try:
                            self.proc.kill()    # SIGTERM was not enough: killed, before systemd's own timeout
                        except OSError:
                            pass
                    continue
                wish = self._wish_changed()
                if wish is not None:
                    self._switch(wish)
                    raise SystemExit(0)         # systemd starts the service again, which reads the new wish
                self._final_tick()
            if self.stopping:
                raise SystemExit(0)
            if code == 0:
                # asked to quit (the panel restarts the player): the service starts again and reads the wish again
                self.log("pvj-player: renderer: pvj-render quit")
                raise SystemExit(0)
            ends = 1 if self.clock() - started >= self.healthy_seconds else ends + 1
            what = ("exit %d" % code) if code > 0 else "signal %d" % -code
            if ends >= 2:
                return "pvj-render ended twice in a row (%s)" % what
            self.log("pvj-player: renderer: pvj-render ended (%s); starting it once more" % what)


def serve(player, screen_args, detail, log, env=None):
    """Run pvj-render under a Supervisor in this process. Returns the reason to fall back to classic mpv; never
    returns while pvj-render draws."""
    board = hardware.detect_board().get("kind", "")
    wish = clean(screenmode.read_json(wanted_path(env)))
    sources, final = wish.get("sources"), wish.get("final")
    sup = Supervisor(command(player.socket_path, screen_args, detail, board, env, sources, final or "auto"), player.socket_path, player.rundir,
                     detail, log=log, wanted=wanted_path(env), sources=sources, final=final)
    signal.signal(signal.SIGTERM, sup._stop)
    signal.signal(signal.SIGINT, sup._stop)
    try:
        why = sup.run()
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    return why


def fallback(rundir, detail, why, log):
    log("pvj-player: renderer: classic mpv draws: %s" % why)
    write_report(rundir, {"chosen": "new", "drawing": "classic", "why": why, "detail": detail}, log)


# ---- the panel's side --------------------------------------------------------------------------------------------
REPORT_WORDS = 200


def clean_report(data):
    """The player's report, read by the panel: written by the account that plays untrusted media, so every field is
    checked and nothing in it is more than a word from a list, a number or a short plain sentence."""
    if not data:
        return None
    pid = data.get("pid")
    return {"chosen": data.get("chosen") if data.get("chosen") in USES else None,
            "drawing": data.get("drawing") if data.get("drawing") in USES else None,
            "why": screenmode._WHY.sub("", data["why"])[:REPORT_WORDS] if isinstance(data.get("why"), str) else "",
            "switched": screenmode._WHY.sub("", data["switched"])[:REPORT_WORDS] if isinstance(data.get("switched"), str) else "",
            "detail": clean({"detail": data.get("detail")})["detail"],
            "final": clean({"final": data.get("final")}).get("final"),
            "final_why": screenmode._WHY.sub("", data["final_why"])[:REPORT_WORDS] if isinstance(data.get("final_why"), str) else "",
            "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
            "boot": screenmode._WHY.sub("", data["boot"])[:64] if isinstance(data.get("boot"), str) else "",
            "at": data.get("at") if isinstance(data.get("at"), int) and not isinstance(data.get("at"), bool) else None}


class RendererError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Renderer:
    """The panel's half: the setting, the wish for the player, a restart for a change, and what the page says."""

    def __init__(self, api, settings, state_dir, player_dir, log=print, env=None):
        self.api, self.settings, self.log, self.env = api, settings, log, env
        self.wanted_file = os.path.join(state_dir, WANTED)
        self.report_file = os.path.join(player_dir, REPORT)

    def saved(self):
        return clean(self.settings.data.get("renderer"))

    def _write(self):
        screenmode.write_json(self.wanted_file, self.saved())

    def _restart(self, why):
        self.log("pvj-web: renderer: restarting the player (%s)" % why)
        try:
            self.api.player.quit()
        except Exception as e:
            self.log("pvj-web: renderer: the player did not take the restart (%s)" % e)

    def start(self):
        """At the panel's start: WANTED says what the settings say (nothing is restarted: the player reads it at its
        next start, and the one that runs was started with what the last panel wrote)."""
        if screenmode.read_json(self.wanted_file) == self.saved():
            return False
        self._write()
        return True

    def sync(self):
        """After a factory reset (or an import, which never carries it): WANTED follows the settings, and the player is
        restarted if that changed what it should run."""
        before = clean(screenmode.read_json(self.wanted_file))
        self._write()
        if before != self.saved():
            self._restart("the settings changed")

    def report(self):
        try:
            if not os.path.isfile(self.report_file) or os.path.islink(self.report_file):
                return None
        except OSError:
            return None
        return clean_report(screenmode.read_json(self.report_file))

    def drawing(self):
        """Which program draws now, and why it is not the one chosen: from the report of the player that runs (its
        pid), else classic (a player started with classic writes no report)."""
        rep = self.report()
        try:
            pid = self.api.player.ipc.request("get_property", "pid")
        except Exception:
            pid = None
        if rep and rep["drawing"] and (pid is None or rep["pid"] is None or self._same_run(rep, pid)):
            return rep["drawing"], rep["why"]
        return "classic", ""

    def _same_run(self, rep, pid):
        # the supervisor writes its own pid; pvj-render is its child and classic mpv is the supervisor itself (exec)
        if rep["drawing"] == "classic":
            return rep["pid"] == pid
        try:
            with open("/proc/%d/stat" % int(pid)) as f:
                return int(f.read().rsplit(") ", 1)[1].split()[1]) == rep["pid"]
        except (OSError, ValueError, IndexError):
            return True

    def board(self):
        board = getattr(self.api, "board", None)
        return board.get("kind", "") if isinstance(board, dict) else hardware.detect_board().get("kind", "")

    def sources(self):
        """What the page says of sources (D101): the Owner's number or None (the board's own), this board's default
        and cap, and the number pvj-render is started with."""
        kind = self.board()
        chosen = self.saved().get("sources")
        return {"chosen": chosen, "default": sources_for(kind), "cap": source_cap(kind), "starts": sources_for(kind, chosen)}

    def state(self):
        saved = self.saved()
        drawing, why = self.drawing()
        out = {"use": saved["use"], "detail": saved["detail"], "drawing": drawing, "available": available(self.env),
               "uses": list(USES), "details": list(DETAILS), "sources": self.sources(),
               "final": saved.get("final", "auto"), "finals": list(FINALS)}
        if saved["use"] == "new" and drawing == "classic":
            out["why"] = why or "the player has not started pvj-render yet"
        rep = self.report()
        if rep and drawing == "new" and rep["final"]:
            out["final_now"] = {"final": rep["final"], "why": rep["final_why"]}      # what the final pass draws at now (D108)
        if rep and rep["switched"]:
            # how the player ended pvj-render at the last change of the wish (Supervisor._switch)
            out["switched"], out["switched_at"] = rep["switched"], rep["at"]
        return out

    def brief(self):
        s = self.state()
        return {"use": s["use"], "drawing": s["drawing"], "why": s.get("why", "")}

    def choose(self, use, detail=None, sources=None, final=None):
        """`sources` (D101): None keeps the number as it is, "auto" is the board's own, 1 to the board's cap a
        number of the Owner's. `final` (D108): None keeps it as it is, else one of FINALS."""
        if use not in USES:
            raise RendererError(400, "use must be classic or new")
        detail = self.saved()["detail"] if detail is None else detail
        if isinstance(detail, bool) or detail not in DETAILS:
            raise RendererError(400, "detail must be auto, 540, 720 or output")
        if final is None:
            final = self.saved().get("final", "auto")
        if isinstance(final, bool) or final not in FINALS or isinstance(final, float):
            raise RendererError(400, "final must be auto, full or 720")
        cap = source_cap(self.board())
        if sources is None:
            sources = self.saved().get("sources")
        elif sources == "auto":
            sources = None
        elif isinstance(sources, bool) or not isinstance(sources, int) or not 1 <= sources <= cap:
            raise RendererError(400, "sources must be auto or 1 to %d on this box" % cap)
        if use == "new" and not available(self.env):
            raise RendererError(409, "pvj-render is not built on this box")
        before = self.saved()
        with self.settings.lock:
            self.settings.data["renderer"] = {"use": use, "detail": detail}
            if sources is not None:
                self.settings.data["renderer"]["sources"] = sources
            if final != "auto":
                self.settings.data["renderer"]["final"] = final
            self.settings.save()
        try:
            self._write()
        except OSError as e:
            raise RendererError(500, "the wish for the player was not written (%s)" % e)
        if self.saved() != before:
            self._restart("the renderer is now %s" % ("pvj-render" if use == "new" else "classic mpv"))
        elif use == "new":
            # New chosen again while the player fell back to classic (pvj-render ended twice in a row, or did not
            # start): a restart of the player is what tries pvj-render again (D95), so choosing New again does it,
            # instead of leaving the box on classic until something else restarts the player (the 2-hour soak, D106)
            drawing, why = self.drawing()
            if drawing == "classic" and why:
                self._restart("New chosen again after classic took over (%s)" % why)
        return self.state()
