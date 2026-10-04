# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""MIDI input from USB controllers, using the raw ALSA device files (no libraries).

Every class-compliant USB MIDI controller (pad grid, keyboard, fader box) shows up as
`/dev/snd/midiC<card>D<device>`; reading that file gives the plain MIDI byte stream. All controllers that are
plugged in are read at once, and a controller unplugged and replugged is picked up again. Each control can be
assigned to an action with "learn": choose the action, move the control, done. A built-in map (notes 36 to 71 play
pads, and so on) can stay on underneath. See MIDI.md.

Limits:
* Off until switched on. Only paths of the form /dev/snd/midiC<n>D<n> are ever opened (never an arbitrary file),
  and only if they are character devices.
* Receive only; nothing is written to a device.
* Only play (pads), stop, pause, fade, blackout, reset, opacity, size, position, speed, volume and the shader
  rotation (Vibes on or off, next shader, dwell time) are reachable.
* No more than 50 commands a second reach the player, whatever the controllers send.
* The web service needs the `audio` group and read access to ALSA devices (the systemd unit has both).
"""

import glob
import os
import re
import select
import stat
import threading
import time
import uuid

from .osc import RateLimiter

MIDI_DEVICE = {"id": "midi", "name": "MIDI", "role": "live"}
DEVICE_PATH = re.compile(r"/dev/snd/midiC([0-9]{1,3})D[0-9]{1,3}")
SOURCE = re.compile(r"[A-Za-z0-9 ._-]{1,32}")
MIN_INTERVAL = 0.05
MAX_CALLS_PER_SECOND = 50.0
TRIGGER_GAP = 0.25          # a pad or button cannot fire again within this many seconds, even if it bounces
LEARN_QUIET = 0.6           # after a control is learned, its own next messages are ignored for this long
MAX_MAP = 200
LEARN_SECONDS = 20

# action -> (kind, low, high). "trigger" fires on a press; "level" follows the control.
ACTIONS = {
    "pad": ("trigger", None, None), "stop": ("trigger", None, None), "pause": ("trigger", None, None),
    "scene": ("trigger", None, None),            # a Room scene, by its id in "scene" (room.py)
    "blackout": ("trigger", None, None), "fadeout": ("trigger", None, None), "reset": ("trigger", None, None),
    "opacity": ("level", 0, 100), "size": ("level", 1, 200), "position": ("level", -100, 100),
    "speed": ("level", 0.25, 2.0), "volume": ("level", 0, 100),
    "blackout_hold": ("level", None, None),      # on while the control is up at 64 or more, off below
    # The shader rotation (the Shaders and Vibes module): a press switches it on or off, another goes to the next
    # shader, and a knob or fader chooses how long each shader stays from VIBES_DWELLS.
    "vibes": ("trigger", None, None), "vibes_next": ("trigger", None, None), "vibes_dwell": ("level", None, None),
}
VIBES_DWELLS = (15, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 3600)      # seconds, bottom to top
KINDS = ("note", "cc", "program")


class MidiError(Exception):
    pass


def list_devices(pattern="/dev/snd/midiC*D*"):
    return sorted(p for p in glob.glob(pattern) if DEVICE_PATH.fullmatch(p))


def source_name(path, asound="/proc/asound"):
    """A stable name for the controller behind a device path: ALSA's card id (nanoKONTROL2, Mix, Mini ...),
    which stays the same when card numbers change between boots. Falls back to the path."""
    m = DEVICE_PATH.fullmatch(path)
    if m:
        try:
            with open(os.path.join(asound, "card%s" % m.group(1), "id")) as f:
                name = f.read().strip()
            if SOURCE.fullmatch(name):
                return name
        except OSError:
            pass
    return os.path.basename(path)


class MidiParser:
    """Turns a MIDI byte stream into (kind, channel, data1, data2) tuples, kinds 'on', 'off', 'cc',
    'program'. Handles running status; skips system and real-time bytes and system-exclusive data."""

    def __init__(self):
        self.status = None
        self.data = []
        self.in_sysex = False

    def feed(self, chunk):
        out = []
        for b in chunk:
            if b >= 0xF8:                 # real-time: may appear anywhere, changes nothing
                continue
            if b & 0x80:
                if b == 0xF0:
                    self.in_sysex, self.status, self.data = True, None, []
                elif b == 0xF7:
                    self.in_sysex = False
                elif b >= 0xF0:           # other system common: cancels running status
                    self.in_sysex, self.status, self.data = False, None, []
                else:
                    self.in_sysex, self.status, self.data = False, b, []
                continue
            if self.in_sysex or self.status is None:
                continue
            self.data.append(b)
            kind = self.status & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if len(self.data) < need:
                continue
            channel = self.status & 0x0F
            d = self.data
            self.data = []
            if kind == 0x90:
                out.append(("on" if d[1] > 0 else "off", channel, d[0], d[1]))
            elif kind == 0x80:
                out.append(("off", channel, d[0], d[1]))
            elif kind == 0xB0:
                out.append(("cc", channel, d[0], d[1]))
            elif kind == 0xC0:
                out.append(("program", channel, d[0], 0))
        return out


def builtin_map():
    """The map that is on unless switched off: notes 36 to 71 (and program changes 0 to 35) play pads 1 to 36,
    notes 72 to 76 are stop, pause, blackout, fade out and reset, and CC 20 to 25 are the levels."""
    entries = []
    for n in range(36):
        entries.append({"kind": "note", "channel": 0, "number": 36 + n, "action": "pad", "bank": n // 12, "index": n % 12})
        entries.append({"kind": "program", "channel": 0, "number": n, "action": "pad", "bank": n // 12, "index": n % 12})
    for n, action in ((72, "stop"), (73, "pause"), (74, "blackout"), (75, "fadeout"), (76, "reset")):
        entries.append({"kind": "note", "channel": 0, "number": n, "action": action})
    for n, action in ((20, "opacity"), (21, "size"), (22, "position"), (23, "speed"), (24, "volume"), (25, "blackout_hold")):
        entries.append({"kind": "cc", "channel": 0, "number": n, "action": action})
    for e in entries:
        e["source"] = "*"
        e["builtin"] = True
    return entries


_BUILTIN = None


def builtin_cached():
    global _BUILTIN
    if _BUILTIN is None:
        _BUILTIN = builtin_map()
    return _BUILTIN


def validate_entry(e, keep_id=False):
    """A clean map entry from untrusted input. Raises MidiError. A client never chooses an id (keep_id is only for
    re-checking an entry read back from our own settings file)."""
    if not isinstance(e, dict):
        raise MidiError("a mapping must be an object")
    kind, action = e.get("kind"), e.get("action")
    if kind not in KINDS:
        raise MidiError("kind must be note, cc or program")
    if action not in ACTIONS:
        raise MidiError("unknown action")
    for key, lo, hi in (("number", 0, 127), ("channel", 0, 16)):
        v = e.get(key, 0 if key == "channel" else None)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise MidiError("%s must be a whole number from %d to %d" % (key, lo, hi))
    source = e.get("source", "*")
    if source != "*" and (not isinstance(source, str) or not SOURCE.fullmatch(source)):
        raise MidiError("bad controller name")
    if ACTIONS[action][0] == "level" and kind == "program":
        raise MidiError("a program change cannot drive a level")
    stored = keep_id and isinstance(e.get("id"), str) and re.fullmatch(r"[0-9a-f]{8}", e["id"])
    out = {"id": e["id"] if stored else uuid.uuid4().hex[:8],
           "source": source, "kind": kind, "channel": e.get("channel", 0), "number": e["number"], "action": action}
    if action == "pad":
        for key, hi in (("bank", 2), ("index", 11)):
            v = e.get(key)
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi:
                raise MidiError("pad %s must be 0 to %d" % (key, hi))
            out[key] = v
    if action == "scene":
        if not isinstance(e.get("scene"), str) or not re.fullmatch(r"[0-9a-f]{8}", e["scene"]):
            raise MidiError("choose a scene")
        out["scene"] = e["scene"]
    return out


class MidiMapper:
    """Turns (source, message) into API calls using a list of map entries. `do(path, body)` makes one call."""

    def __init__(self, do, entries, mix=None, clock=time.monotonic):
        self.do, self.entries, self.mix, self._clock = do, entries, mix or {}, clock
        self._fired = {}        # (id, source, kind, number) -> time a trigger last fired
        self._last = {}         # (source, kind, number) -> time of the last applied level
        self.pending = {}       # same key -> newest level not yet applied
        self._pressed = {}      # same key -> was it "down" last time (so a held or repeated value fires once)
        self.last_message = None
        self.vibes_on = lambda: False       # is the shader rotation running (for the on/off toggle); the hub sets it

    def matching(self, source, kind, channel, number):
        """Entries for this control. If the user has mapped it, only their entries apply: a learned mapping replaces
        the built-in one for that control instead of firing next to it."""
        found = [e for e in self.entries if e["kind"] == kind and e["number"] == number
                 and e["source"] in ("*", source) and e["channel"] in (0, channel + 1)]
        mine = [e for e in found if not e.get("builtin")]
        return mine or found

    @staticmethod
    def _trigger_calls(e):
        a = e["action"]
        if a == "pad":
            return [("/api/play", {"pad": [e["bank"], e["index"]]})]
        if a == "scene":
            return [("/api/room/scene", {"scene": e["scene"]})]
        if a in ("pause", "stop", "reset"):
            return [("/api/control", {"action": a})]
        if a == "fadeout":
            return [("/api/fadeout", {"seconds": 2})]
        if a == "blackout":
            return [("/api/blackout", {"on": None})]           # None: toggle, decided by the caller from the live state
        if a == "vibes":
            return [("/api/vibes", {"on": None})]              # a toggle too
        if a == "vibes_next":
            return [("/api/vibes", {"next": True})]
        return []

    @staticmethod
    def _level_calls(e, value):
        a = e["action"]
        if a == "blackout_hold":
            return [("/api/blackout", {"on": value >= 64})]
        if a == "vibes_dwell":
            return [("/api/vibes", {"dwell": VIBES_DWELLS[min(len(VIBES_DWELLS) - 1, value * len(VIBES_DWELLS) // 128)]})]
        _, lo, hi = ACTIONS[a]
        return [("/api/control", {"action": a, "value": round(lo + (hi - lo) * value / 127.0, 2)})]

    def plan(self, source, msg):
        """What a message should do: a list of (path, body) calls. Nothing is called here, so a caller can release its
        lock before making the (slow) calls into the player."""
        kind, channel, d1, d2 = msg
        if kind == "off":
            kind, d2 = "note", 0                       # a note-off is the release of the same control
        elif kind == "on":
            kind = "note"
        self.last_message = "%s %s %d %d" % (source, kind, d1, d2)
        calls = []
        now = self._clock()
        for e in self.matching(source, kind, channel, d1):
            key = (e["id"] if "id" in e else e["action"], source, kind, d1)
            kind_of, _, _ = ACTIONS[e["action"]]
            if kind_of == "trigger":
                down = d2 >= 64 if kind == "cc" else (d2 > 0 or kind == "program")
                was = self._pressed.get(key, False)
                self._pressed[key] = down if kind != "program" else False
                if down and not was and now - self._fired.get(key, -1e9) >= TRIGGER_GAP:
                    self._fired[key] = now
                    calls.extend(self._trigger_calls(e))
            else:
                if now - self._last.get(key, 0.0) < MIN_INTERVAL:
                    self.pending[key] = (e, d2)         # a fader sweep: keep only the newest value
                    continue
                self.pending.pop(key, None)
                self._last[key] = now
                calls.extend(self._level_calls(e, d2))
        return calls

    def message(self, source, msg):
        """Handle one parsed message. Returns the number of calls made."""
        done = 0
        for path, body in self.plan(source, msg):
            if path == "/api/blackout" and body.get("on") is None:
                body = {"on": not self.mix.get("blackout", False)}
            if path == "/api/vibes" and "on" in body and body["on"] is None:
                body = {"on": not self.vibes_on()}
            done += int(bool(self.do(path, body)))
        return done

    def flush_calls(self):
        """The held-back fader values whose wait is over, as calls (so the last position is never lost)."""
        now, calls = self._clock(), []
        for key in list(self.pending):
            if now - self._last.get(key, 0.0) >= MIN_INTERVAL:
                e, value = self.pending.pop(key)
                self._last[key] = now
                calls.extend(self._level_calls(e, value))
        return calls

    def flush(self):
        return sum(int(bool(self.do(path, body))) for path, body in self.flush_calls())


class MidiInput:
    """Reads one device file on its own thread and hands parsed messages to `on_message(source, msg)`."""

    def __init__(self, path, source, on_message, log=print, open_fn=None):
        self.path, self.source, self.on_message, self.log = path, source, on_message, log
        self._open = open_fn or self._open_device
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.messages = 0

    @staticmethod
    def _open_device(path):
        """Open a MIDI device file, but only a character device that really is one of /dev/snd/midi*."""
        if not isinstance(path, str) or not DEVICE_PATH.fullmatch(path):
            raise OSError("not a MIDI device path")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("not a character device")
        return fd

    def _run(self):
        try:
            fd = self._open(self.path)
        except Exception:
            return                                      # the hub tries again at its next scan
        self.connected = True
        parser = MidiParser()
        poller = select.poll()
        poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
        try:
            while not self._stop.is_set():
                if poller.poll(100):
                    chunk = os.read(fd, 256)
                    if not chunk:
                        break                           # end of stream: the device is gone
                    for msg in parser.feed(chunk):
                        self.messages += 1
                        self.on_message(self.source, msg)
                self.on_message(self.source, None)      # a tick: lets the hub flush held fader values
        except OSError:
            pass                                        # unplugged mid-read
        except Exception as e:
            self.log("midi: %s: internal error: %r" % (self.path, e))
        finally:
            self.connected = False
            try:
                os.close(fd)
            except OSError:
                pass

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="midi")
        self._thread.start()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None


class MidiHub:
    """Owns the settings-driven set of controllers, the map, and learn mode."""

    def __init__(self, api, settings, log=print, open_fn=None, lister=list_devices, namer=source_name,
                 clock=time.monotonic, scan_interval=2.0):
        self.api, self.settings, self.log = api, settings, log
        self._open_fn, self._lister, self._namer, self._clock = open_fn, lister, namer, clock
        self.scan_interval = scan_interval
        self._lock = threading.RLock()
        self.inputs = {}          # path -> MidiInput
        self._scanner = None
        self._stop = threading.Event()
        self.calls = RateLimiter(clock, rate=MAX_CALLS_PER_SECOND, burst=MAX_CALLS_PER_SECOND)   # a faulty pad cannot flood the player
        self.mapper = MidiMapper(self._do, [], api.mix, clock)
        self.learn_until = 0.0
        self.captured = None
        self._quiet = None
        self._quiet_until = 0.0
        self._vibes_off_said = False

    # --- calls into the player ------------------------------------------
    def _note(self, text):
        now = self._clock()
        if self._quiet_until <= now:
            self._quiet_until = now + 10
            self.log("midi: " + text)

    def _do(self, path, body):
        if not self.calls.allow("all"):
            self._note("too many commands a second; some were dropped")
            return False
        if path == "/api/vibes":                           # only with the Shaders and Vibes module on; said once
            if not self.api.registry.enabled("shaders"):
                if not self._vibes_off_said:
                    self._vibes_off_said = True
                    self.log("midi: a Vibes control was used, but the Shaders and Vibes module is off; nothing was done")
                return False
            self._vibes_off_said = False
        status, payload = self.api.handle("POST", path, body, MIDI_DEVICE, "midi")
        if status != 200:
            self._note("%s -> %s %s" % (path, status, payload.get("error", "")))
            return False
        return True

    def cfg(self):
        return self.settings.data["control"]["midi"]

    def entries(self):
        """The user's mappings (each re-checked: settings are a file a person may have edited) then the built-in map."""
        c = self.cfg()
        mine = []
        for e in c["map"]:
            try:
                mine.append(validate_entry(e, keep_id=True))
            except MidiError:
                continue
        return mine + (builtin_cached() if c["builtin"] else [])

    # --- messages ---------------------------------------------------------
    def _run_calls(self, calls):
        """Make the calls a message planned. Called WITHOUT the hub lock: a play is many round trips to the player."""
        for path, body in calls:
            if path == "/api/blackout" and body.get("on") is None:
                body = {"on": not self.api.mix.get("blackout", False)}
            if path == "/api/vibes" and "on" in body and body["on"] is None:
                body = {"on": not self.api.vibes.running}
            self._do(path, body)

    def on_message(self, source, msg):
        if msg is None:
            with self._lock:
                calls = self.mapper.flush_calls()
            self._run_calls(calls)
            return
        with self._lock:
            kind, channel, d1, d2 = msg
            now = self._clock()
            if self.learn_until and now < self.learn_until and self.captured is None:
                if kind == "cc" or (kind == "on" and d2 > 0) or kind == "program":
                    self.captured = {"source": source, "kind": "note" if kind == "on" else kind, "channel": channel + 1, "number": d1}
                    self._quiet = (source, "note" if kind in ("on", "off") else kind, d1, now + LEARN_QUIET)
                    self.learn_until = 0.0
                return                                                     # nothing is executed while learning
            if self.learn_until and now >= self.learn_until:
                self.learn_until = 0.0
            q = self._quiet                                                # the control just learned is still moving: let it settle
            if q and now < q[3] and q[:3] == (source, "note" if kind in ("on", "off") else kind, d1):
                return
            self.mapper.entries = self.entries()
            self.mapper.mix = self.api.mix
            calls = self.mapper.plan(source, msg)
        self._run_calls(calls)

    # --- learn ----------------------------------------------------------------
    def start_learn(self):
        with self._lock:
            self.captured = None
            self.learn_until = self._clock() + LEARN_SECONDS

    def cancel_learn(self):
        with self._lock:
            self.learn_until, self.captured = 0.0, None

    # --- devices -------------------------------------------------------------
    def scan(self):
        """Start a reader for each controller that appeared and forget those that went. Cheap; called every 2 s.
        It gives up quietly if the hub is stopping: stop() holds the lock while it waits for this thread, and a
        scan that waited for the lock would start a new reader after everything had been shut down."""
        if self._stop.is_set() or not self._lock.acquire(timeout=0.2):
            return
        try:
            if self._stop.is_set():
                return
            paths = set(self._lister())
            for path in list(self.inputs):
                if path not in paths or not self.inputs[path].alive:
                    self.inputs.pop(path).stop()
            for path in sorted(paths - set(self.inputs)):
                inp = MidiInput(path, self._namer(path), self.on_message, log=self.log, open_fn=self._open_fn)
                self.inputs[path] = inp
                inp.start()
        finally:
            self._lock.release()

    def apply(self):
        """Make reality match the settings and the module switch."""
        enabled = self.cfg()["enabled"] and self.api.registry.enabled("control-midi")
        if not enabled:
            self._stop_all()
            return
        with self._lock:
            self._stop.clear()
            self.scan()
            if self._scanner is None:
                def loop():
                    while not self._stop.wait(self.scan_interval):
                        try:
                            self.scan()
                        except Exception as e:
                            self.log("midi: scan error: %r" % (e,))
                self._scanner = threading.Thread(target=loop, daemon=True, name="midi-scan")
                self._scanner.start()

    def _stop_all(self):
        """Stop the scanner and every reader. The threads call back into this hub (messages, scans), so they are
        joined with the lock RELEASED; joining while holding it stalls until the join times out."""
        with self._lock:
            self._stop.set()
            scanner, self._scanner = self._scanner, None
            inputs = list(self.inputs.values())
            self.inputs.clear()
            self.learn_until, self.captured = 0.0, None
        if scanner:
            scanner.join(timeout=3)
        for inp in inputs:
            inp.stop()

    def stop(self):
        self._stop_all()

    def status(self):
        with self._lock:
            c = self.cfg()
            now = self._clock()
            seen = {p: i for p, i in self.inputs.items()}
            devices = [{"path": p, "name": self._namer(p), "connected": p in seen and seen[p].connected,
                        "messages": seen[p].messages if p in seen else 0} for p in self._lister()]
            learning = bool(self.learn_until and now < self.learn_until)
            return {"enabled": c["enabled"], "builtin": c["builtin"], "devices": devices, "map": list(c["map"]),
                    "last": self.mapper.last_message,
                    "learn": {"active": learning, "captured": self.captured, "seconds_left": max(0, round(self.learn_until - now)) if learning else 0}}


def validate(body, current):
    """New switches (enabled, builtin) from untrusted input, based on `current`. The map has its own calls."""
    new = dict(current)
    for key in ("enabled", "builtin"):
        if key in body:
            if not isinstance(body[key], bool):
                raise MidiError("%s must be true or false" % key)
            new[key] = body[key]
    return new


def add_entry(current_map, entry):
    if len(current_map) >= MAX_MAP:
        raise MidiError("at most %d mappings" % MAX_MAP)
    clean = validate_entry(entry)
    # one control does one thing: a new mapping replaces an older one for the same control
    kept = [e for e in current_map if not (e["kind"] == clean["kind"] and e["number"] == clean["number"]
                                           and e["source"] == clean["source"] and e["channel"] == clean["channel"])]
    return kept + [clean]
