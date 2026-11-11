# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Show stats on output (D133): a small debugging overlay in a corner of the node's video output.

It shows, in a few lines of monospace text: the node's name and address; the output frame rate and the frames dropped;
what is playing (decoder, size, pixel format, the clip's file name); CPU load, memory and temperature; and whether the
firmware has throttled the CPU. It is for the Owner at a soak or a fault, never for a show.

How it is drawn: mpv's `osd-overlay` command with ASS text, in an id of its own (OVERLAY_ID). Classic mpv draws it over
the clip, user shaders and graphics; pvj-render (0.12.0 on) forwards the same command to its text layer, which is drawn
last, over the mapping. It does NOT use `show-text`, which is the one OSD slot the PIN and the recovery codes use, and not
mpv's stats.lua, which cannot show the system fields, costs more, and on pvj-render would be warped by the mapping.

Show safety (the owner's rules; D133). All of these are tested:

* the state lives only in this object, in memory. It is never written to settings, an export, a preset, a scene, the
  schedule, autostart, the Room or the fleet, and nothing but the route handler (`Api.set_stats_overlay`) turns it on;
* at every start of pvj-web it sends `osd-overlay ID none` (`reset_player`), because the player outlives a restart of this
  program and may still be drawing the last text;
* it goes off by itself when the player is another process (a crash, a screen mode, a renderer change or a fall back),
  after an auto-off time (15 minutes by default, 1 to 240 asked for), and when the player does not answer for a while;
* while a PIN, a join code or a QR code is on the screen, and during a blackout, it is taken off the screen and comes back
  after, if still on;
* an event line says who turned it on and off, and why when it went off by itself.

The sampler is a thread that exists only while the overlay is on. It looks once a second: one connection to the player
for all the questions (`Ipc.batch`), /proc/stat for the CPU, /proc/meminfo, the thermal zones, and every five seconds the
firmware's throttling word from the root helper (`pvj-sysd`, command `throttled`). Where the helper is missing or older, the
overlay says "throttling: unknown" and carries on.
"""

import os
import re
import threading
import time

from . import hardware, health
from .player import PlayerError

OVERLAY_ID = 61                     # mpv's osd-overlay id: its own, the PIN and recovery codes use show-text (a different slot)
DEFAULT_MINUTES, MIN_MINUTES, MAX_MINUTES = 15, 1, 240
CORNERS = ("tl", "tr", "bl", "br")
DEFAULT_CORNER = "tl"
PLAY_RES = (1280, 720)              # the ASS script's own resolution: the text is the same size on every screen
FONT_SIZE = 20
MARGIN = 12
SAMPLE_SECONDS = 1.0
THROTTLE_SECONDS = 5.0              # the helper is asked this often (it starts vcgencmd)
ADDRESS_SECONDS = 30.0              # `ip` is run at most this often
FAILS_TO_STOP = 5                   # this many seconds in a row of a player that does not answer, and the overlay goes off
FILE_NAME_MAX = 40
LINE_MAX = 64
TEXT_MAX = 1500                     # far under the renderer's 4096
WORD_JOINER = "⁠"

# the alignment (ASS numpad) and the point the text is anchored at, in PLAY_RES units
ANCHOR = {"tl": (7, MARGIN, MARGIN - 2), "tr": (9, PLAY_RES[0] - MARGIN, MARGIN - 2),
          "bl": (1, MARGIN, PLAY_RES[1] - MARGIN + 2), "br": (3, PLAY_RES[0] - MARGIN, PLAY_RES[1] - MARGIN + 2)}
CORNER_WORDS = {"tl": "top left", "tr": "top right", "bl": "bottom left", "br": "bottom right"}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class StatsError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def ass_escape(text):
    """Text made safe to put in an ASS line: a `{` would open an override block (so `{\\fs500}` in a node's name or a file
    name would resize the whole overlay), a backslash starts a tag, and a new line starts a new event. Braces get a
    backslash in front (libass draws them), a backslash gets a word joiner after it (mpv's own way: the backslash is then not
    followed by anything that is special), control characters become a space. Always a plain str."""
    out = []
    for ch in _CONTROL.sub(" ", str(text if text is not None else "")):
        if ch == "\\":
            out.append("\\" + WORD_JOINER)
        elif ch in "{}":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def shorten(text, limit):
    """A long name cut in the middle (the end of a file name says the most): `very-long-na...clip.mp4`."""
    text = _CONTROL.sub(" ", str(text if text is not None else ""))
    if len(text) <= limit:
        return text
    keep = max(limit - 3, 2)
    head = keep // 2
    return text[:head] + "..." + text[len(text) - (keep - head):]


def ass_text(lines, corner=DEFAULT_CORNER):
    """The `data` of an `osd-overlay ID ass-events`: one event, the lines joined by \\N, in the corner, white with a black
    outline. The lines are plain text; this escapes them."""
    align, x, y = ANCHOR[corner if corner in ANCHOR else DEFAULT_CORNER]
    tags = "{\\an%d\\pos(%d,%d)\\fnmonospace\\fs%d\\bord3\\shad0\\1c&HFFFFFF&\\3c&H000000&\\3a&H30&}" % (align, x, y, FONT_SIZE)
    body = "\\N".join(ass_escape(shorten(l, LINE_MAX)) for l in lines)
    return (tags + body)[:TEXT_MAX]


def overlay_command(lines, corner):
    return ("osd-overlay", OVERLAY_ID, "ass-events", ass_text(lines, corner), PLAY_RES[0], PLAY_RES[1])


def remove_command():
    return ("osd-overlay", OVERLAY_ID, "none", "")


# -- the throttling word, in plain words --------------------------------------------------------------------------------
def throttle_words(bits):
    """(now, since): lists of plain phrases from the firmware's get_throttled word. now: bit 0 under-voltage, bit 1 the ARM
    frequency capped, bit 2 throttled, bit 3 the soft temperature limit; since the node started: the same at bits 16 to 19."""
    now, since = [], []
    if bits & 0x1:
        now.append("under-voltage now")
    if bits & 0x2 or bits & 0x8:
        now.append("capped now")
    if bits & 0x4:
        now.append("throttled now")
    if bits & 0xF0000:
        what = []
        if bits & 0x10000:
            what.append("under-voltage")
        if bits & 0xA0000:
            what.append("capped")
        if bits & 0x40000:
            what.append("throttled")
        since.append("has throttled since boot (%s)" % ", ".join(what) if what else "has throttled since boot")
    return now, since


def cpufreq_capped(cur, top, ceiling, busy):
    """True if the CPU is held under its top speed. `ceiling` (scaling_max_freq) under `top` (cpuinfo_max_freq) is a cap set
    by the kernel. A current speed under the top is only a cap when the CPU is busy: the ondemand and schedutil governors
    keep an idle Pi at its lowest speed, which is normal and not throttling. None where the files are not there."""
    if not all(isinstance(v, int) and v > 0 for v in (cur, top)):
        return None
    if isinstance(ceiling, int) and 0 < ceiling < top * 0.98:
        return True
    return bool(busy is not None and busy >= 70 and cur < top * 0.85)


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def decoder_words(hwdec, params):
    """`drm-copy 1920x1080 yuv420p`, or `software 1280x720 yuv420p`; `no clip` where nothing is decoded."""
    p = params if isinstance(params, dict) else {}
    w, h = p.get("w"), p.get("h")
    size = "%sx%s" % (w, h) if isinstance(w, int) and isinstance(h, int) else ""
    fmt = p.get("pixelformat") if isinstance(p.get("pixelformat"), str) else ""
    if not size and not fmt and not hwdec:
        return "decoder: none yet"
    mode = hwdec if isinstance(hwdec, str) and hwdec not in ("", "no") else "software"
    return " ".join(x for x in (mode, size, fmt) if x)


def format_lines(s):
    """The overlay's lines (plain text) from one sample, a dict made by StatsOverlay.sample. Pure."""
    def f1(v, unit=""):
        return ("%.1f%s" % (v, unit)) if isinstance(v, (int, float)) else "--"
    head = "%s  %s" % (s.get("name") or "node", s.get("ip") or "no address")
    if s.get("mode") == "render":
        head += "  new renderer"
    elif s.get("mode") == "classic":
        head += "  classic mpv"
    if s.get("left") is not None:
        head += "  off in %dm" % max(1, round(s["left"] / 60.0))
    lines = [head]
    if s.get("mode") == "render":
        hz = ("%g" % round(s["out_hz"], 2)) if s.get("out_hz") else "--"
        lines.append("out %s of %s fps  missed %s/s" % (f1(s.get("out_fps")), hz, f1(s.get("missed_rate"))))
        lines.append("video %s fps  dropped %s/s  late %s" % (f1(s.get("video_fps")), f1(s.get("drop_rate")),
                                                              s.get("late") if s.get("late") is not None else "--"))
    else:
        lines.append("video %s fps  dropped %s/s" % (f1(s.get("video_fps")), f1(s.get("drop_rate"))))
    lines.append(s.get("decoder") or "decoder: none yet")
    lines.append(shorten(s.get("file") or "no clip", FILE_NAME_MAX))
    mem = ("mem %d%%" % s["mem"]) if s.get("mem") is not None else "mem --"
    cpu = ("cpu %d%%" % s["cpu"]) if s.get("cpu") is not None else "cpu --"
    temp = ("%.1f C" % s["temp"]) if s.get("temp") is not None else "-- C"
    lines.append("%s  %s  %s" % (cpu, mem, temp))
    lines.extend((s.get("throttle") or "throttling: unknown").split("; "))       # "now" and "since boot" on lines of their own
    return lines


class StatsOverlay:
    """The overlay's state and sampler. `api` supplies the player (`api.player.ipc`), `access_on_screen()`, `mix["blackout"]`,
    `event()` and `_ip_json()`; everything is looked up at the moment of use so a test can hand it small fakes."""

    def __init__(self, api, clock=time.monotonic, proc="/proc", sysfs="/sys", log=print, threaded=True):
        self.api, self._clock, self.proc, self.sysfs, self.log = api, clock, proc, sysfs, log
        self._threaded = threaded
        self._link = None               # the connection the overlay is drawn through (see _held)
        self.lock = threading.Lock()
        self._draw = threading.Lock()           # held while a frame is checked and drawn, and while the overlay is taken off
        self._stop = threading.Event()
        self._thread = None
        self._reset()
        self.throttle_reader = self._ask_helper  # () -> bits or None; a test replaces it
        self.addresses_reader = self._addresses

    # -- the state (memory only) --
    def _reset(self):
        self.on = False
        self.corner = DEFAULT_CORNER
        self.minutes = DEFAULT_MINUTES
        self.mode = None                # "classic" or "render", as found when it was turned on
        self._deadline = None
        self._pid = None
        self._drawn = False             # is the text on the player's screen now
        self._hidden = ""               # why it is taken off while it is on: "", "a PIN or code is on the screen", "blackout"
        self._fails = 0
        self._last = None               # the last sample
        self._prev = None               # (time, drops, flips, missed) for rates
        self._cpu_prev = None
        self._throttle = (None, None)   # (time asked, text)
        self._addr = (None, "")

    def state(self):
        """What the route says: no secrets in it, and the last numbers so the panel can show them too."""
        with self.lock:
            left = max(0, int(self._deadline - self._clock())) if self.on and self._deadline is not None else None
            return {"on": self.on, "corner": self.corner, "minutes": self.minutes, "seconds_left": left,
                    "renderer": {"render": "new", "classic": "classic"}.get(self.mode), "hidden": self._hidden or None,
                    "lines": format_lines(self._last) if self._last else [], "default_minutes": DEFAULT_MINUTES,
                    "max_minutes": MAX_MINUTES,
                    "note": "Off by default. It turns itself off after %d minutes, whenever the player restarts, and when "
                            "this panel restarts." % (self.minutes if self.on else DEFAULT_MINUTES)}

    # -- turning on and off (the route handler is the only caller of `turn_on`) --
    def turn_on(self, corner=None, minutes=None, who=None):
        """On, or the corner and the time changed while on; a new call re-arms the timer. Raises StatsError."""
        corner = DEFAULT_CORNER if corner is None else corner
        if not isinstance(corner, str) or corner not in CORNERS:
            raise StatsError(400, "corner must be tl, tr, bl or br")
        minutes = DEFAULT_MINUTES if minutes is None else minutes
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise StatsError(400, "minutes must be a whole number from %d to %d" % (MIN_MINUTES, MAX_MINUTES))
        mode, pid = self._probe()
        with self.lock:
            was_on = self.on
            self.on, self.corner, self.minutes, self.mode = True, corner, minutes, mode
            self._deadline = self._clock() + minutes * 60
            if not was_on or self._pid != pid:
                self._pid = pid
                self._prev = self._cpu_prev = None
                self._last = None
            self._fails, self._hidden = 0, ""
        try:
            self.tick(raise_errors=True)        # the first frame now, so a refusal reaches the person who asked
        except StatsError:
            self.turn_off(quiet=True)
            raise
        if not was_on:
            self.api.event("player", "Stats on the output were turned on (%s, off by itself in %d minutes)." % (CORNER_WORDS[corner], minutes), who=who, urgent=False)
            self.log("pvj-web: stats on the output on (%s, %d minutes)" % (corner, minutes))
        self._start_thread()
        return self.state()

    def turn_off(self, why=None, who=None, quiet=False):
        """Off. `why` (given for an automatic off) goes in the event line with `who` (for a person's off)."""
        with self.lock:
            was_on, mode = self.on, self.mode
            self._reset()
            self._stop.set()                    # under the lock: a turn_on that follows always starts a thread of its own
        with self._draw:                        # a frame being drawn finishes first, then the text is taken off
            self._remove(mode, close=True)
        if was_on and not quiet:
            if why:
                self.api.event("player", "Stats on the output went off by themselves: %s." % why, urgent=False)
            else:
                self.api.event("player", "Stats on the output were turned off.", who=who, urgent=False)
            self.log("pvj-web: stats on the output off%s" % (" (%s)" % why if why else ""))
        return was_on

    def stop_if_on(self, why):
        """For a change that makes a new player (the renderer, the screen mode): off, and said why."""
        if self.on:
            self.turn_off(why)

    # -- the player --
    def _ipc(self):
        return self.api.player.ipc

    def _probe(self):
        """("classic" or "render", pid) of the player now, or StatsError. A renderer from before osd-overlay is refused in
        plain words (the capability is `osd-overlay` in its `pvj-render` property)."""
        ipc = self._ipc()
        try:
            pid = ipc.request("get_property", "pid")
        except PlayerError as e:
            raise StatsError(409, "The player is not running, so there is no screen to draw on (%s)." % e)
        try:
            state = ipc.request("get_property", "pvj-render")
        except PlayerError as e:
            if str(e).startswith("mpv: "):
                return "classic", pid               # mpv's own refusal: classic mpv has no such property
            raise StatsError(409, "The player did not answer (%s)." % e)
        if isinstance(state, dict) and state.get("osd-overlay") is True:
            return "render", pid
        raise StatsError(409, "The new renderer on this node is too old to draw the stats overlay (it is version %s; the overlay "
                              "needs 0.12.0). Update the node's pvj-render." % (state.get("version") if isinstance(state, dict) else "unknown"))

    def _held(self):
        """The connection the overlay is drawn through, kept open. mpv ties an osd-overlay to the client that made it and
        removes it when that connection closes, and every request of pvj.player.Ipc is a connection of its own: drawn through
        those, the text vanished at once on classic mpv (found on node-02, 2026-11-11; pvj-render keeps its overlays itself). So
        the overlay is sent here, and ending the connection ends the overlay on classic mpv too."""
        if self._link is None:
            self._link = self._ipc().hold()
        return self._link

    def _drop_link(self):
        link, self._link = self._link, None
        if link is not None:
            link.close()

    def _command(self, *command):
        """One command through the held connection. A connection that has gone bad (the player restarted, the socket broke) is
        ended and replaced once, at once; a fresh one that fails too is the player's answer."""
        fresh = self._link is None
        try:
            return self._held().request(*command)
        except PlayerError as e:
            if str(e).startswith("mpv: "):
                raise
            self._drop_link()
            if fresh:
                raise
        try:
            return self._held().request(*command)
        except PlayerError as e:
            if not str(e).startswith("mpv: "):
                self._drop_link()
            raise

    def _remove(self, mode=None, close=False):
        """Take the text off. Sent through the held connection (or, on pvj-render with none, directly: that renderer keeps the
        text itself until it is told). With `close` the connection is ended as well, which on classic mpv takes the text off
        by itself. `_drawn` stays True only when pvj-render did not take it, so the next look tries again; a connection that
        failed is ended, and that takes the text off on classic mpv. True when it is off."""
        mode = mode or self.mode
        ok = True
        try:
            if self._link is not None:
                self._command(*remove_command())
            elif mode == "render":
                self._ipc().request(*remove_command())
        except PlayerError as e:
            if not str(e).startswith("mpv: "):       # mpv's or the renderer's own refusal means nothing is drawn
                ok = False
        except Exception:
            ok = False
        if close or not ok:
            self._drop_link()
        self._drawn = False if (ok or mode != "render") else True
        return ok

    def reset_player(self, tries=None, delay=1.0, sleep=time.sleep, cap=30.0):
        """At every start of pvj-web: the player outlives this program, so what it was told to draw last is taken off. Tries
        again with a back-off (delay, then twice that, and so on up to `cap` seconds) until the player takes it or says it does
        not know the command (a renderer before 0.12.0 has nothing to take off); `tries` bounds it for a test. It also ends when
        the overlay has been turned on since, which needed a player that answers. True when cleared."""
        n = 0
        while tries is None or n < tries:
            n += 1
            try:
                self._ipc().request(*remove_command())
                return True
            except PlayerError as e:
                if str(e).startswith("mpv: "):
                    return True                     # mpv's or the renderer's own refusal: nothing is drawn
            except Exception:
                pass
            if self.on:
                return True
            sleep(min(delay * 2 ** (n - 1), cap))
        return False

    # -- the sampler thread --
    def _start_thread(self):
        if not self._threaded:
            return
        with self.lock:
            if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
                return
            self._stop = threading.Event()
            stop = self._stop
            self._thread = threading.Thread(target=self._run, args=(stop,), name="stats-overlay", daemon=True)
            self._thread.start()

    def _run(self, stop):
        while not stop.wait(SAMPLE_SECONDS):
            try:
                self.tick()
            except Exception as e:                  # never takes the panel down
                self.log("pvj-web: stats overlay: %s" % e)
            if not self.on:
                return

    def tick(self, raise_errors=False):
        """One look and, if it is to be seen, one drawing. Called once a second by the thread. A player that refuses the
        drawing ends the overlay (and raises StatsError for the first frame, where `raise_errors` is set)."""
        with self.lock:
            if not self.on:
                return
            deadline = self._deadline
        if deadline is not None and self._clock() >= deadline:
            self.turn_off("the %d-minute timer ran out" % self.minutes)
            return
        reason = ""
        try:
            if self.api.access_on_screen():
                reason = "a PIN or code is on the screen"
            elif self.api.mix.get("blackout"):
                reason = "blackout"
        except Exception:
            reason = "a PIN or code is on the screen"       # when unsure, stay off the screen (as the snapshot does)
        if reason:
            with self.lock:
                self._hidden = reason
            with self._draw:
                self._remove()                  # every look while hidden, until the player has taken it off
            return
        with self.lock:
            self._hidden = ""
        sample = self.sample()
        if sample is None:
            return
        with self._draw:
            with self.lock:
                if not self.on:
                    return
                corner, left = self.corner, max(0, deadline - self._clock()) if deadline is not None else None
            sample["left"] = left
            self._last = sample
            try:
                self._command(*overlay_command(format_lines(sample), corner))
            except PlayerError as e:
                self._fails += 1
                refused = str(e).startswith("mpv: ")
                gone = "" if (not refused and self._fails < FAILS_TO_STOP) else (
                    "the player refused it (%s)" % e if refused else "the player did not answer")
                failure = e
            else:
                self._fails = 0
                self._drawn = True
                return
        if not gone:
            return
        if raise_errors:
            raise StatsError(409, "The player refused the overlay (%s)." % failure)
        self.turn_off(gone)

    # -- the numbers --
    def sample(self):
        """One dict of everything the overlay shows, or None when the overlay went off while sampling (another process)."""
        ipc, now = self._ipc(), self._clock()
        asks = [("get_property", "pid"), ("get_property", "path"), ("get_property", "hwdec-current"),
                ("get_property", "video-params"), ("get_property", "estimated-vf-fps"),
                ("get_property", "frame-drop-count"), ("get_property", "decoder-frame-drop-count")]
        if self.mode == "render":
            asks += [("get_property", "pvj-render"), ("get_property", "pvj-shaders")]
        try:
            got = ipc.batch(asks)
        except PlayerError:
            got = None
        if got is None:
            with self.lock:
                self._fails += 1
                fails = self._fails
            if fails >= FAILS_TO_STOP:
                self.turn_off("the player did not answer")
            return None
        val = [d if ok else None for ok, d in got]
        pid = val[0]
        if pid is not None and self._pid is not None and pid != self._pid:
            self.turn_off("the player restarted")
            return None
        path, hwdec, params, vfps = val[1], val[2], val[3], _num(val[4])
        drops = sum(v for v in (_num(val[5]), _num(val[6])) if v is not None) if _num(val[5]) is not None or _num(val[6]) is not None else None
        try:
            tr = getattr(ipc, "translate", None)
            view = tr.lists.view(path) if tr is not None and isinstance(path, str) else None
            if view is not None:
                path = view.clip
        except Exception:
            pass
        flips = missed = out_hz = late = None
        if self.mode == "render":
            st, shaders = val[7], val[8]
            if isinstance(st, dict):
                flips, missed, out_hz = _num(st.get("flips")), _num(st.get("missed-vblanks")), _num(st.get("output-fps"))
            if isinstance(shaders, list):
                late = sum(r.get("drops", 0) for r in shaders if isinstance(r, dict) and isinstance(r.get("drops"), int))
        out_fps = missed_rate = drop_rate = None
        with self.lock:
            prev, self._prev = self._prev, (now, drops, flips, missed)
        if prev and now > prev[0]:
            dt = now - prev[0]
            if drops is not None and prev[1] is not None and drops >= prev[1]:
                drop_rate = (drops - prev[1]) / dt
            if flips is not None and prev[2] is not None and flips >= prev[2]:
                out_fps = (flips - prev[2]) / dt
            if missed is not None and prev[3] is not None and missed >= prev[3]:
                missed_rate = (missed - prev[3]) / dt
        cpu = self._cpu()
        used, _total = health.memory(self.proc)
        try:
            temps = hardware.temperatures(self._root())
            temp = max((t["celsius"] for t in temps), default=None)
        except Exception:
            temp = None
        return {"name": self._name(), "ip": self._ip(now), "mode": self.mode, "out_fps": out_fps, "out_hz": out_hz,
                "missed_rate": missed_rate, "video_fps": vfps, "drop_rate": drop_rate, "late": late,
                "decoder": decoder_words(hwdec, params), "file": os.path.basename(path) if isinstance(path, str) and path else None,
                "cpu": cpu, "mem": used, "temp": temp, "throttle": self._throttle_text(now, cpu)}

    def _root(self):
        return os.path.dirname(self.sysfs.rstrip("/")) or "/"

    def _name(self):
        try:
            import socket
            from . import fleet
            return fleet.clean_hostname(socket.gethostname())
        except Exception:
            return "node"

    def _addresses(self):
        out = []
        try:
            for entry in self.api._ip_json():
                if entry.get("ifname") in ("lo", "wg-pvj"):
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("local"):
                        out.append(a["local"])
        except Exception:
            pass
        return out

    def _ip(self, now):
        at, text = self._addr
        if at is None or now - at >= ADDRESS_SECONDS or not text:
            found = self.addresses_reader()
            text = found[0] if found else ""
            self._addr = (now, text)
        return text

    def _cpu(self):
        """Busy percent of all cores since the last look, from /proc/stat. None for the first look."""
        try:
            with open(os.path.join(self.proc, "stat")) as f:
                parts = f.readline().split()
            nums = [int(x) for x in parts[1:9]]
        except (OSError, ValueError, IndexError):
            return None
        total, idle = sum(nums), nums[3] + (nums[4] if len(nums) > 4 else 0)
        prev, self._cpu_prev = self._cpu_prev, (total, idle)
        if not prev or total <= prev[0]:
            return None
        return max(0, min(100, round(100.0 * (1 - (idle - prev[1]) / (total - prev[0])))))

    def _ask_helper(self):
        """The firmware's throttling word from pvj-sysd, or None (helper missing, too old, or not a Pi). A short timeout: a
        helper that hangs must not stall the sampler."""
        client = getattr(self.api, "sysd", None)
        path = getattr(client, "path", None)
        if client is None:
            return None
        try:
            from .sysd import SysdClient
            reply = SysdClient(path, 2).request({"cmd": "throttled"}) if path else client.request({"cmd": "throttled"})
        except Exception:
            return None
        bits = reply.get("bits") if isinstance(reply, dict) and reply.get("ok") else None
        return bits if isinstance(bits, int) and not isinstance(bits, bool) else None

    def _cpufreq(self):
        base = os.path.join(self.sysfs, "devices/system/cpu/cpu0/cpufreq")

        def read(name):
            try:
                with open(os.path.join(base, name)) as f:
                    return int(f.read().strip())
            except (OSError, ValueError):
                return None
        return read("scaling_cur_freq"), read("cpuinfo_max_freq"), read("scaling_max_freq")

    def _throttle_text(self, now, busy):
        at, text = self._throttle
        if at is None or now - at >= THROTTLE_SECONDS:
            bits = self.throttle_reader()
            capped = cpufreq_capped(*self._cpufreq(), busy)
            if bits is None:
                text = "throttling: unknown" + (", capped now (CPU speed)" if capped else "")
            else:
                cur, since = throttle_words(bits)
                if capped and "capped now" not in cur:
                    cur.append("capped now")
                text = "throttling: " + (", ".join(cur) if cur else "none now") + ("; " + "; ".join(since) if since else "")
            self._throttle = (now, text)
        return text
