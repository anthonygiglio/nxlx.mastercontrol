# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The mode the screen is driven at: its size and how many times a second it is drawn (D90).

Until D90 the player took the screen's own preferred mode, whatever it was: on the owner's monitor that is 2560 x 1440
at 75 Hz, the costliest size there is for a Pi 4 and an uneven beat for 29.97 and 30 fps clips. Now:

* **Automatic** (the default): the screen's own mode if it is at most 1920 x 1200 and drawn about 60 times a second;
  otherwise the largest mode the screen lists that is at most 1920 x 1200, of the same shape as its own mode if there
  is one (else 1920 x 1080, else the largest), at about 60 Hz (50 only if it lists no 60 there). See `automatic`.
* **A mode chosen by hand** on System > Screen output, from the screen's own list. It is tried for KEEP_SECONDS and
  goes back by itself unless it is confirmed, as a network change does.

Who does what. The player's service (`pvj-player serve`, the display account) decides, just before it becomes mpv:
it asks mpv for the screen's modes (`mpv --drm-mode=help`, the only list that has the rates exactly as mpv will
compare them; /sys/class/drm has the sizes only), reads what is wanted from WANTED in the state folder (written by
the panel, which owns the settings; the player may not read settings.json), and passes one `--drm-mode` that is in
that list. What it chose and why is in REPORT, in its own folder, for the panel. The panel never opens the display
(its unit has no access to /dev/dri). It writes WANTED, restarts the player for a change, and looks that the new
player has a screen at all: one whose mode was in the list and still did not come up is started once more with no
`--drm-mode` (the screen's own mode, as before D90), and the page says so.

mpv 0.40 (video/out/drm_common.c): a `WxH@R` that matches no mode exactly is an error, not a fallback (R is compared
to 0.01 Hz: `round(R * 100)` against the mode's own rate, `clock * 1000 / htotal / vtotal`, times 100), and with
`--idle` mpv then stays up with no screen at all. So the text passed is always one of mpv's own list, R written as
mpv prints it (`%.2f`); where an interlaced mode of the same numbers comes first in the list, its index is passed.

With no screen connected nothing is passed, as before: mpv takes the first connected screen when one comes, and a
mode is chosen at the next start of the player. Forcing a mode at boot (`video=HDMI-A-1:...D`) is not built (D90).
"""

import json
import os
import re
import subprocess
import threading
import time

from . import hardware, paths

AUTO = "auto"
MAX_WIDTH, MAX_HEIGHT = 1920, 1200      # what Automatic chooses at most: 1080p and WUXGA, never 1440p or 4K
NEAR = 0.2                              # 59.94, 59.95 and 60.00 are all "60 Hz"; 49.95 and 50.00 are "50 Hz"
KEEP_SECONDS = 15                       # a mode chosen by hand goes back by itself this long after it is on the screen
START_SECONDS = 45                      # and anyway this long after the tap, if the new player never comes up with it
SCREEN_GRACE = 6.0                      # a new player has this long to open its screen before it counts as failed
HELP_TIMEOUT = 8                        # seconds for `mpv --drm-mode=help`
MAX_MODES = 64                          # kept of a screen's list (a real one has 20 to 40)
MAX_FILE = 64 * 1024                    # bytes read of WANTED or REPORT

WANTED = "screen-mode.json"             # in the state folder: the panel writes it, the player reads it
REPORT = paths.SCREEN_REPORT            # in the player's folder: the player writes it, the panel reads it

MODE_TEXT = re.compile(r"([1-9][0-9]{1,4})x([1-9][0-9]{1,4})@([0-9]{1,3}\.[0-9]{2})")
_HEAD = re.compile(r"Available modes for drm-connector=([0-9]+)\.([A-Za-z0-9_-]{1,32})")
_MODE = re.compile(r"Mode ([0-9]{1,3}): ([A-Za-z0-9_.-]{1,32}) \(([0-9]{1,5})x([0-9]{1,5})@([0-9]{1,4}\.[0-9]{2})Hz\)")
_WHY = re.compile(r"[^A-Za-z0-9 .,:;()'@x/-]")


# ---- the screen's list ---------------------------------------------------------------------------------------------
def mode_text(width, height, hz):
    """The text mpv takes and prints for a mode: 1920x1080@60.00."""
    return "%dx%d@%.2f" % (width, height, hz)


def parse_help(text):
    """[{"card", "connector", "modes": [mode, ...]}] from what `mpv --drm-mode=help` prints (mpv 0.37 and 0.40:
    "Available modes for drm-connector=1.HDMI-A-1" and then "  Mode 0: 1920x1080 (1920x1080@60.00Hz)" per mode, in
    the kernel's order, the screen's own mode first). A mode is {"index", "name", "width", "height", "hz", "text",
    "interlaced"}; `index` is its place in the list, which is what `--drm-mode=N` means."""
    out = []
    for line in (text or "").splitlines():
        head = _HEAD.search(line)
        if head:
            out.append({"card": int(head.group(1)), "connector": head.group(2), "modes": []})
            continue
        m = _MODE.search(line)
        if m and out and len(out[-1]["modes"]) < MAX_MODES:
            index, name, w, h, hz = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4)), float(m.group(5))
            if 16 <= w <= 16384 and 16 <= h <= 16384 and 0 < hz < 1000:
                out[-1]["modes"].append({"index": index, "name": name, "width": w, "height": h, "hz": hz,
                                         "text": mode_text(w, h, hz), "interlaced": name.endswith("i")})
    return out


def list_modes(mpv_bin="mpv", run=subprocess.run):
    """(screens, problem): the screens mpv sees, from `mpv --drm-mode=help`, or ([], why not)."""
    try:
        r = run([mpv_bin, "--no-config", "--drm-mode=help"], capture_output=True, text=True, timeout=HELP_TIMEOUT,
                stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return [], "mpv is not installed"
    except (OSError, subprocess.SubprocessError) as e:
        return [], "mpv did not list the modes (%s)" % type(e).__name__
    screens = parse_help((r.stdout or "") + "\n" + (r.stderr or ""))
    return [s for s in screens if s["modes"]], ("" if screens else "mpv listed no screen")


def progressive(modes):
    return [m for m in modes if not m["interlaced"]]


def rate_class(hz):
    """60 for a mode drawn about 60 times a second (59.94, 59.95, 60.00), 50 for about 50, else None."""
    for want in (60, 50):
        if abs(hz - want) <= NEAR:
            return want
    return None


def _fits(m):
    return m["width"] <= MAX_WIDTH and m["height"] <= MAX_HEIGHT


def _shape(m):
    return m["width"] / float(m["height"])


def automatic(modes):
    """(mode or None, why) that Automatic chooses from a screen's list (kernel order, its own mode first).

    1. The screen's own mode, if it is at most 1920 x 1200 and drawn at about 60 Hz.
    2. Else, among the modes at most 1920 x 1200 at about 60 Hz (at about 50 only if there is none at 60): those
       of the same shape as the screen's own mode, largest first; else 1920 x 1080; else the largest. Of one size,
       the rate the screen lists first.
    3. Else None: the screen's own mode, with no `--drm-mode` (as before D90)."""
    prog = progressive(modes)
    if not prog:
        return None, "the screen lists no mode"
    own = prog[0]
    if _fits(own) and rate_class(own["hz"]) == 60:
        return own, "the screen's own mode"
    for want in (60, 50):
        cands = [m for m in prog if _fits(m) and rate_class(m["hz"]) == want]
        if not cands:
            continue
        same = [m for m in cands if abs(_shape(m) - _shape(own)) < 0.01]
        hd = [m for m in cands if (m["width"], m["height"]) == (1920, 1080)]
        pool = same or hd or cands
        best = max(pool, key=lambda m: m["width"] * m["height"])        # max keeps the first of equals: the list's order
        why = ("the largest mode up to 1920 x 1200 at %d Hz that the screen lists" % want) if (same or not hd) else \
            "1920 x 1080 at %d Hz: the screen lists no smaller mode of its own shape" % want
        return best, "%s (its own mode is %s)" % (why, words(own))
    return None, "the screen lists no mode up to 1920 x 1200 at 60 or 50 Hz, so its own mode is used"


def drm_value(mode, modes):
    """What to pass as --drm-mode for `mode`: its text, which mpv matches against the list in order (size, and rate
    to 0.01 Hz); or its index where another mode of the same numbers (an interlaced one) comes first."""
    for m in modes:
        if (m["width"], m["height"], m["text"]) == (mode["width"], mode["height"], mode["text"]):
            return mode["text"] if m is mode or m["index"] == mode["index"] else str(mode["index"])
    return mode["text"]


def words(mode):
    """1920 x 1080 at 60 Hz, as the page and the log say it (two decimals only where they say something)."""
    if not mode:
        return "the screen's own mode"
    hz = ("%.2f" % mode["hz"]).rstrip("0").rstrip(".")
    return "%d x %d at %s Hz" % (mode["width"], mode["height"], hz)


def text_words(text):
    m = MODE_TEXT.fullmatch(text or "")
    return words({"width": int(m.group(1)), "height": int(m.group(2)), "hz": float(m.group(3))}) if m else text


def valid_mode(value):
    return value == AUTO or (isinstance(value, str) and MODE_TEXT.fullmatch(value) is not None)


def boot_id(path="/proc/sys/kernel/random/boot_id"):
    try:
        with open(path) as f:
            return f.read(64).strip()
    except OSError:
        return ""


# ---- the files between the panel and the player ----------------------------------------------------------------------
def read_json(path):
    """A small JSON object from `path`, or {} (missing, unreadable, too big, not an object)."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE + 1)
    except OSError:
        return {}
    if len(raw) > MAX_FILE:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data, mode=0o640):
    """Write a small JSON object whole (a temp file renamed over the old one), readable by the group (pvj)."""
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(data, f, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def wanted_path(env=None):
    return os.path.join(paths.state_dir(env), WANTED)


def clean_wanted(data):
    """What the player may take from WANTED: {"mode", "trial", "skip"}, every part checked."""
    out = {"mode": data.get("mode") if valid_mode(data.get("mode")) else AUTO}
    for key in ("trial", "skip"):
        part = data.get(key)
        if isinstance(part, dict) and valid_mode(part.get("mode")) and isinstance(part.get("boot"), str) and len(part["boot"]) <= 64:
            out[key] = {"mode": part["mode"], "boot": part["boot"]}
    return out


# ---- the player's side: which --drm-mode, just before it becomes mpv ------------------------------------------------
def decide(wanted, screens, problem, sysfs, boot):
    """The report: {"asked", "trial", "used", "arg", "why", "connector", "modes", "own", "automatic"}.

    `wanted` is clean_wanted(WANTED); `screens` and `problem` are list_modes(); `sysfs` is hardware.drm_connectors().
    `used` is the mode's text or None (no --drm-mode: the screen's own), `arg` the argument for mpv or None, and
    `why` says in words why `used` is not what was asked, or "" when it is."""
    trial = wanted.get("trial")
    in_trial = bool(trial and trial["boot"] == boot)
    asked = trial["mode"] if in_trial else wanted.get("mode", AUTO)
    rep = {"asked": asked, "trial": in_trial, "used": None, "arg": None, "why": "", "connector": None, "modes": [],
           "own": None, "automatic": None}
    connected = [c for c in sysfs if c.get("status") == "connected" and not c["connector"].lower().startswith("writeback")]
    if not screens:
        rep["why"] = ("no screen is connected: the player starts as it always did and takes a screen's own mode when one comes"
                      if not connected else "the screen's modes could not be read (%s), so its own mode is used" % (problem or "no answer"))
        return rep
    screen = screens[0]                       # mpv drives the first connected screen; help lists only connected ones
    modes = screen["modes"]
    rep["connector"] = screen["connector"]
    rep["modes"] = [dict(m) for m in modes]
    prog = progressive(modes)
    rep["own"] = prog[0]["text"] if prog else None
    auto, auto_why = automatic(modes)
    rep["automatic"] = auto["text"] if auto else None
    chosen, why = None, ""
    if asked != AUTO:
        chosen = next((m for m in prog if m["text"] == asked), None)
        if chosen is None:
            why = "%s is not offered by this screen now, so Automatic is used: %s" % (text_words(asked), words(auto))
    if chosen is None:
        chosen = auto
        if asked == AUTO:
            why = "" if auto else auto_why
    skip = wanted.get("skip")
    if chosen and skip and skip["boot"] == boot and skip["mode"] == chosen["text"]:
        why = "%s did not start on this screen, so the screen's own mode is used" % words(chosen)
        chosen = None
    if chosen:
        rep["used"] = chosen["text"]
        rep["arg"] = "--drm-mode=" + drm_value(chosen, modes)
        rep["auto_why"] = auto_why if asked == AUTO or why else ""
    rep["why"] = why
    return rep


def prepare(extra_args, rundir, mpv_bin="mpv", env=None, run=subprocess.run, sysfs=None, boot=None, log=None):
    """The --drm-mode argument for the player that is about to start (a list of none or one), with REPORT written.

    Only where the player draws through DRM itself (`--gpu-context=drm`, a box without a desktop) and nobody gave a
    --drm-mode by hand (`pvj-player --mpv-arg`, which wins). Never raises: a player that cannot choose a mode starts
    with the screen's own, as before."""
    if "--gpu-context=drm" not in extra_args or any(a.startswith("--drm-mode") for a in extra_args):
        return []
    log = log or (lambda line: None)
    try:
        sysfs = hardware.drm_connectors() if sysfs is None else sysfs
        boot = boot_id() if boot is None else boot
        connected = any(c.get("status") == "connected" for c in sysfs)
        screens, problem = list_modes(mpv_bin, run) if connected or not sysfs else ([], "")
        rep = decide(clean_wanted(read_json(wanted_path(env))), screens, problem, sysfs, boot)
    except Exception as e:                       # a box that cannot decide plays as it always did
        rep = {"asked": AUTO, "trial": False, "used": None, "arg": None, "why": "the mode was not chosen (%s)" % type(e).__name__,
               "connector": None, "modes": [], "own": None, "automatic": None}
    rep.update(pid=os.getpid(), boot=boot or "", at=int(time.time()))
    if rep["why"]:
        log("pvj-player: screen output: %s" % rep["why"])
    if rep["arg"]:
        log("pvj-player: screen output: %s (%s)" % (text_words(rep["used"]), rep["arg"]))
    try:
        write_json(os.path.join(rundir, REPORT), rep)
    except OSError as e:
        log("pvj-player: screen output: the report was not written (%s)" % e)
    return [rep["arg"]] if rep["arg"] else []


# ---- the panel's side ------------------------------------------------------------------------------------------------
def clean_report(data):
    """The player's report, read by the panel. The player is the account that plays untrusted media, so every field is
    checked here and nothing in it is more than a mode text from its own list, a number or a short plain sentence."""
    if not data:
        return None
    modes = []
    for m in (data.get("modes") or [])[:MAX_MODES] if isinstance(data.get("modes"), list) else []:
        if not isinstance(m, dict):
            continue
        t = m.get("text")
        hit = MODE_TEXT.fullmatch(t) if isinstance(t, str) else None
        if not hit or not isinstance(m.get("index"), int) or isinstance(m.get("index"), bool):
            continue
        modes.append({"index": m["index"], "width": int(hit.group(1)), "height": int(hit.group(2)), "hz": float(hit.group(3)),
                      "text": t, "interlaced": m.get("interlaced") is True})

    def text(v, n=300):
        return _WHY.sub("", v)[:n] if isinstance(v, str) else ""

    def mode(v):
        return v if isinstance(v, str) and MODE_TEXT.fullmatch(v) else None
    arg = data.get("arg")
    pid = data.get("pid")
    return {"asked": data.get("asked") if valid_mode(data.get("asked")) else AUTO, "trial": data.get("trial") is True,
            "used": mode(data.get("used")),
            "arg": arg if isinstance(arg, str) and re.fullmatch(r"--drm-mode=([0-9]{1,3}|%s)" % MODE_TEXT.pattern, arg) else None,
            "why": text(data.get("why")), "auto_why": text(data.get("auto_why")),
            "connector": text(data.get("connector"), 32) or None, "modes": modes,
            "own": mode(data.get("own")), "automatic": mode(data.get("automatic")),
            "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
            "boot": text(data.get("boot"), 64), "at": data.get("at") if isinstance(data.get("at"), int) else None}


class ScreenError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Screen:
    """The panel's half: the setting, the try that goes back by itself, the look that a new player has a screen,
    and what the page and the status say.

    Its lock guards a few fields and is never held while the player is asked or restarted (so it has no place in
    pvj/locks.py's order)."""

    def __init__(self, api, settings, state_dir, player_dir, log=print, clock=time.monotonic, boot=boot_id,
                 timer=None, keep=KEEP_SECONDS):
        self.api, self.settings, self.log = api, settings, log
        self.wanted_file = os.path.join(state_dir, WANTED)
        self.report_file = os.path.join(player_dir, REPORT)
        self._clock, self._boot, self.keep = clock, boot, keep
        self._timer_factory = timer or (lambda seconds, fn: threading.Timer(seconds, fn))
        self._timer = None
        self._lock = threading.Lock()
        self.pending = None          # {"mode", "previous", "since", "until" (None while the new player starts), "pid_before"}
        self.note = ""               # the last thing that happened by itself, for the page
        self._said = set()           # reasons already written to the log
        self._checked = {}           # pid -> when it was first seen with a mode (the look that it has a screen)
        self._report_cache = (None, None)

    # -- the setting ----------------------------------------------------------
    def saved(self):
        mode = (self.settings.data.get("screen") or {}).get("mode", AUTO)
        return mode if valid_mode(mode) else AUTO

    def _wanted(self):
        return clean_wanted(read_json(self.wanted_file))

    def _write(self, data):
        write_json(self.wanted_file, data)

    def report(self):
        try:
            st = os.stat(self.report_file)
            key = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        if self._report_cache[0] != key:
            self._report_cache = (key, clean_report(read_json(self.report_file)))
        return self._report_cache[1]

    def _pid(self):
        try:
            return self.api.player.ipc.request("get_property", "pid")
        except Exception:
            return None

    def _restart(self, why):
        """End the player; its service starts a new one, which reads WANTED. Never raises."""
        self.log("pvj-web: screen output: restarting the player (%s)" % why)
        try:
            self.api.player.quit()
        except Exception as e:
            self.log("pvj-web: screen output: the player did not take the restart (%s)" % e)

    def start(self):
        """At the panel's start: WANTED says what the settings say. A try that was on when the panel stopped goes back
        (a box that was restarted since never used it: the player only takes a try of this same boot)."""
        try:
            have, boot = self._wanted(), self._boot()
        except Exception:
            have, boot = {}, ""
        trial = have.get("trial")
        want = {"mode": self.saved()}
        if have.get("skip") and have["skip"]["boot"] == boot:
            want["skip"] = have["skip"]
        if have == want and os.path.exists(self.wanted_file):
            return False
        try:
            self._write(want)
        except OSError as e:
            self.log("pvj-web: screen output: %s not written (%s)" % (self.wanted_file, e))
            return False
        if trial and trial["boot"] == boot:
            self.note = "A mode that was being tried when the panel restarted went back by itself."
            self._restart("a try that was not confirmed")
            return True
        return False

    def sync(self):
        """After an import or a factory reset: WANTED follows the settings, and the player is restarted if what it
        was asked for changed."""
        self._end_pending()
        have = self._wanted()
        if have.get("mode") == self.saved() and "trial" not in have:
            return
        self._write({"mode": self.saved()})
        self._restart("the settings changed")

    # -- a change, its try and its confirm ---------------------------------------
    def choose(self, mode):
        if not valid_mode(mode):
            raise ScreenError(400, "mode must be auto or one of the screen's modes, as WIDTHxHEIGHT@RATE")
        rep = self.report()
        if mode != AUTO and not any(m["text"] == mode and not m["interlaced"] for m in (rep or {}).get("modes", [])):
            raise ScreenError(409, "this screen does not offer %s" % text_words(mode))
        pid_before = self._pid()                # asked before the lock: it is never held over a word with the player
        with self._lock:
            if self.pending is not None:
                raise ScreenError(409, "a mode is being tried: keep it or go back first")
            previous = self.saved()
            same = mode == previous
            if not same:
                self.pending = {"mode": mode, "previous": previous, "since": self._clock(), "until": None, "pid_before": pid_before}
        if same:
            return self.state()
        try:
            self._write({"mode": previous, "trial": {"mode": mode, "boot": self._boot()}})
        except OSError as e:
            with self._lock:
                self.pending = None
            raise ScreenError(500, "could not write the choice: %s" % (e.strerror or e))
        self.note = ""
        self._arm(START_SECONDS)
        self._restart("trying %s" % text_words(mode))
        return self.state()

    def _arm(self, seconds):
        if self._timer is not None:
            self._timer.cancel()
        self._timer = self._timer_factory(seconds, self.expire)
        self._timer.daemon = True
        self._timer.start()

    def expire(self):
        """The timer: a try nobody kept goes back."""
        with self._lock:
            p = self.pending
            if p is None:
                return False
            if p["until"] is not None and self._clock() < p["until"] - 0.25:
                return False                    # an older timer; the newer one is set
            if p["until"] is None and self._clock() - p["since"] < START_SECONDS - 0.25:
                return False
        self.revert("Nobody kept %s, so it went back by itself." % text_words(p["mode"]))
        return True

    def confirm(self):
        with self._lock:
            p = self.pending
            if p is None:
                raise ScreenError(409, "no mode is being tried")
            if p["until"] is None:
                raise ScreenError(409, "the player is still starting with that mode; look at the screen first")
        with self.settings.lock:
            before = self.settings.data.get("screen")
            self.settings.data["screen"] = {"mode": p["mode"]}
            try:
                self.settings.save()
            except OSError as e:
                if before is None:
                    self.settings.data.pop("screen", None)
                else:
                    self.settings.data["screen"] = before
                raise ScreenError(500, "could not save: %s" % (e.strerror or e))
        try:
            self._write({"mode": p["mode"]})
        except OSError as e:
            self.log("pvj-web: screen output: %s not written (%s)" % (self.wanted_file, e))
        self._end_pending()
        self.note = ""
        self.log("pvj-web: screen output: %s kept" % text_words(p["mode"]))
        return self.state()

    def revert(self, note=""):
        with self._lock:
            p, self.pending = self.pending, None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if p is None:
            return self.state()
        try:
            self._write({"mode": p["previous"]})
        except OSError as e:
            self.log("pvj-web: screen output: %s not written (%s)" % (self.wanted_file, e))
        self.note = note or "Went back to %s." % ("Automatic" if p["previous"] == AUTO else text_words(p["previous"]))
        self._restart("going back from %s" % text_words(p["mode"]))
        return self.state()

    def _end_pending(self):
        with self._lock:
            self.pending = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # -- the look at a new player ----------------------------------------------
    def watch(self, pid):
        """autostart's watch, every couple of seconds: the look at the player, and the words over the test pattern."""
        did = self.tick(pid)
        if pid is not None:
            self._keep_words()
        return did

    def tick(self, pid=None):
        """Called every couple of seconds (autostart's watch) and with each look at the page: a new player during a
        try starts the countdown; a player that was given a mode and has no screen after SCREEN_GRACE is started
        again without one. Returns what it did, for the tests."""
        pid = self._pid() if pid is None else pid
        rep = self.report()
        if pid is None or not rep or rep.get("pid") != pid:
            return None
        if rep.get("why") and rep["why"] not in self._said:
            self._said.add(rep["why"])
            self.log("pvj-web: screen output: %s" % rep["why"])
        did = None
        with self._lock:
            p = self.pending
            if p is not None and p["until"] is None and pid != p["pid_before"] and rep.get("trial"):
                p["until"] = self._clock() + self.keep
                did = "counting"
        if did:
            self._arm(self.keep)
        if not rep.get("arg"):
            return did
        first = self._checked.setdefault(pid, self._clock())
        if len(self._checked) > 16:
            self._checked = {pid: first}
        if first is False or self._clock() - first < SCREEN_GRACE:
            return did
        self._checked[pid] = False             # looked at once per player
        if self._has_screen():
            return did
        failed = rep.get("used")
        self.log("pvj-web: screen output: the player was given %s and has no screen" % text_words(failed))
        with self._lock:
            p = self.pending
        if p is not None and rep.get("trial"):
            self.revert("%s did not start on this screen, so it went back." % text_words(p["mode"]))
            return "reverted"
        try:
            self._write(dict(self._wanted(), skip={"mode": failed, "boot": self._boot()}))
        except OSError as e:
            self.log("pvj-web: screen output: %s not written (%s)" % (self.wanted_file, e))
            return did
        self.note = "%s did not start on this screen; the player was started with the screen's own mode." % text_words(failed)
        self._restart("%s gave no screen" % text_words(failed))
        return "own mode"

    def pattern_words(self, on):
        """The mode in words over the test pattern (on), or the words taken off (off). Not over a code or the PIN
        that someone asked to have on the display. Best effort."""
        try:
            if on:
                if self.api.access_on_screen():
                    return False
                self.api.player.ipc.request("show-text", pattern_text(self.report(), self.display()), PATTERN_WORDS_MS)
            else:
                self.api.player.ipc.request("show-text", "", 1)
            return True
        except Exception:
            return False

    def _keep_words(self):
        try:
            path = self.api.player.ipc.request("get_property", "path")
        except Exception:
            return
        if self.api.is_test_pattern(path):
            self.pattern_words(True)

    def _has_screen(self):
        try:
            vo = self.api.player.ipc.request("get_property", "current-vo")
        except Exception:
            return False
        return bool(vo)

    # -- what the page and the status say -----------------------------------------
    def display(self):
        """The size and rate mpv says it draws at now, or None."""
        try:
            ipc = self.api.player.ipc
            w, h, hz = (ipc.request("get_property", k) for k in ("display-width", "display-height", "display-fps"))
        except Exception:
            return None
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in (w, h, hz)):
            return None
        return {"width": int(w), "height": int(h), "hz": round(float(hz), 2)}

    def brief(self):
        """For GET /api/status: the mode in use, the one asked for, and why they differ ("" when they do not)."""
        rep = self.report()
        with self._lock:
            p = self.pending
        asked = p["mode"] if p else self.saved()
        if not rep:
            return {"asked": asked, "used": None, "why": "the player has not said which mode it uses"}
        return {"asked": asked, "used": rep["used"], "why": rep["why"] or ("" if rep["used"] else rep.get("auto_why") or "")}

    def state(self):
        self.tick()
        rep = self.report()
        with self._lock:
            p = dict(self.pending) if self.pending else None
        pend = None
        if p:
            left = None if p["until"] is None else max(0, int(round(p["until"] - self._clock())))
            pend = {"mode": p["mode"], "previous": p["previous"], "state": "starting" if left is None else "waiting",
                    "seconds_left": left}
        modes = []
        for m in (rep or {}).get("modes", []):
            if m["interlaced"] or any(x["mode"] == m["text"] for x in modes):
                continue
            modes.append({"mode": m["text"], "width": m["width"], "height": m["height"], "hz": m["hz"], "words": words(m)})
        live = self._sysfs_signature()
        return {"saved": self.saved(), "pending": pend, "note": self.note,
                "used": (rep or {}).get("used"), "asked": (rep or {}).get("asked", self.saved()),
                "why": (rep or {}).get("why", "" if rep else "the player has not said which mode it uses"),
                "auto_why": (rep or {}).get("auto_why", ""),
                "automatic": (rep or {}).get("automatic"), "own": (rep or {}).get("own"),
                "connector": (rep or {}).get("connector"), "modes": modes, "display": self.display(),
                "connected": live["connected"], "keep_seconds": self.keep}

    @staticmethod
    def _sysfs_signature():
        try:
            conns = [c for c in hardware.drm_connectors() if not c["connector"].lower().startswith("writeback")]
        except Exception:
            conns = []
        return {"connected": any(c["status"] == "connected" for c in conns)}


PATTERN_PREFIX = "av://lavfi:smptehdbars=size="       # every test pattern, the old fixed one and the sized one
PATTERN_WORDS_MS = 5000                                # the words over it, drawn again every couple of seconds while it is on


def pattern_url(size):
    """The test pattern at the screen's own size: the colour bars, a grid of 12 rows of squares and a border one
    pixel wide at the very edge (red), so the projector's own scaling and overscan show. Whole numbers only."""
    w, h = size
    if not (isinstance(w, int) and isinstance(h, int) and 16 <= w <= 16384 and 16 <= h <= 16384):
        raise ValueError("a screen size is two whole numbers")
    cell = max(8, h // 12)
    return ("av://lavfi:smptehdbars=size=%dx%d:rate=25,drawgrid=w=%d:h=%d:t=1:c=white@0.8,"
            "drawbox=x=0:y=0:w=%d:h=%d:t=1:c=red" % (w, h, cell, cell, w, h))


def pattern_text(rep, display=None):
    """What the test pattern says on the screen: the mode in words and whether it is Automatic."""
    if display:
        size = words({"width": display["width"], "height": display["height"], "hz": display["hz"]})
    elif rep and rep.get("used"):
        size = text_words(rep["used"])
    else:
        size = "the screen's own mode"
    how = "Automatic" if not rep or rep.get("asked") == AUTO else ("being tried" if rep.get("trial") else "chosen by hand")
    return "Screen output: %s (%s)" % (size, how)
