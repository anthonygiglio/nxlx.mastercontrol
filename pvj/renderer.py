# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Which program draws the screen: classic mpv, or pvj-render, the box's own renderer (render/, D95).

pvj-render is a separate program (GPL-3.0-or-later, in render/) that speaks the same JSON socket as mpv, so nothing
that talks to the player changes: the switch is which program the player's service starts.

Who does what, as for the screen's mode (pvj/screenmode.py, D90):

* The panel owns the setting (`renderer` in the settings: {"use": "classic"|"new", "detail": "auto"|540|720|"output"},
  read with defaults, no schema change). The player may not read settings.json, so the panel writes the wish alone to
  WANTED in the state folder, at its start and on a change, and restarts the player for a change (`Renderer`).
* The player's service (`pvj-player serve`, the display's account) reads WANTED just before it would become mpv. With
  "new" it stays a small supervisor instead (`serve`): it starts pvj-render with the screen's arguments that
  screenmode chose for mpv, waits for its socket to answer, and watches it. It falls back to classic mpv by itself
  (the same exec as before) when pvj-render is missing, does not start, or ends twice in a row, and writes what it
  did and why to REPORT in its own folder, which the panel reads as data from a less trusted account.
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
SMALL_BOARDS = ("pi3", "pi4")           # where Automatic draws 540 lines inside (the spike of 2026-10-16, D95)


def binary(env=None):
    """Where the program is built: render/pvj-render beside pvj/ in this release (PVJ_RENDER_BIN overrides, tests)."""
    env = os.environ if env is None else env
    return env.get("PVJ_RENDER_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render", "pvj-render")


def available(env=None):
    path = binary(env)
    return os.path.isfile(path) and os.access(path, os.X_OK)


def clean(data):
    """{"use", "detail"} from a settings section or the wanted file; anything else is the default."""
    data = data if isinstance(data, dict) else {}
    use = data.get("use") if data.get("use") in USES else DEFAULT["use"]
    detail = data.get("detail")
    if isinstance(detail, bool) or detail not in DETAILS:
        detail = DEFAULT["detail"]
    return {"use": use, "detail": detail}


def wanted_path(env=None):
    return os.path.join(paths.state_dir(env), WANTED)


def detail_arg(detail, board_kind):
    """--detail for pvj-render: lines inside, or the output's own size. Automatic: 540 on a Pi 3 or 4, else the output."""
    if detail == "auto":
        detail = 540 if board_kind in SMALL_BOARDS else "output"
    return "--detail=%s" % detail


def command(socket_path, screen_args, detail, board_kind, env=None):
    """pvj-render's argv: the socket, the screen's arguments as screenmode chose them for mpv, the detail, and mpv's
    cheap scalers on a Pi (as classic's --profile=fast)."""
    args = [binary(env), "--socket=" + socket_path]
    args += [a for a in screen_args if a.startswith(("--drm-device=", "--drm-connector=", "--drm-mode="))]
    args.append(detail_arg(detail, board_kind))
    if board_kind in SMALL_BOARDS:
        args.append("--fast")
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
                 ready=None, start_seconds=START_SECONDS, healthy_seconds=HEALTHY_SECONDS):
        self.cmd, self.socket_path, self.rundir, self.detail = list(cmd), socket_path, rundir, detail
        self.log, self.clock, self.sleep = log, clock, sleep
        self.spawn = spawn or (lambda argv: subprocess.Popen(argv, stdin=subprocess.DEVNULL, close_fds=True))
        self.ready = ready or self._ready
        self.start_seconds, self.healthy_seconds = start_seconds, healthy_seconds
        self.proc = None
        self.stopping = False

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
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

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
        write_report(self.rundir, {"chosen": "new", "drawing": drawing, "why": why, "detail": self.detail}, self.log)

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
            self._report("new", "")
            self.log("pvj-player: renderer: pvj-render is drawing (pid %d)" % self.proc.pid)
            while True:
                try:
                    code = self.proc.wait(0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
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
    sup = Supervisor(command(player.socket_path, screen_args, detail, board, env), player.socket_path, player.rundir, detail, log=log)
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
            "detail": clean({"detail": data.get("detail")})["detail"],
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

    def state(self):
        saved = self.saved()
        drawing, why = self.drawing()
        out = {"use": saved["use"], "detail": saved["detail"], "drawing": drawing, "available": available(self.env),
               "uses": list(USES), "details": list(DETAILS)}
        if saved["use"] == "new" and drawing == "classic":
            out["why"] = why or "the player has not started pvj-render yet"
        return out

    def brief(self):
        s = self.state()
        return {"use": s["use"], "drawing": s["drawing"], "why": s.get("why", "")}

    def choose(self, use, detail=None):
        if use not in USES:
            raise RendererError(400, "use must be classic or new")
        detail = self.saved()["detail"] if detail is None else detail
        if isinstance(detail, bool) or detail not in DETAILS:
            raise RendererError(400, "detail must be auto, 540, 720 or output")
        if use == "new" and not available(self.env):
            raise RendererError(409, "pvj-render is not built on this box")
        before = self.saved()
        with self.settings.lock:
            self.settings.data["renderer"] = {"use": use, "detail": detail}
            self.settings.save()
        try:
            self._write()
        except OSError as e:
            raise RendererError(500, "the wish for the player was not written (%s)" % e)
        if self.saved() != before:
            self._restart("the renderer is now %s" % ("pvj-render" if use == "new" else "classic mpv"))
        return self.state()
