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
* Only play (pads), stop, pause, fade, blackout, reset, opacity, size, position, speed, volume, the clip before and
  after in the playlist, a Room scene, the shader rotation (Vibes on or off, next shader, dwell time) and the shader
  on screen (its first eight inputs, its speed, hue and brightness, the shader before and after it in the active set,
  its first eight presets) are reachable.
* A known controller gets a ready-made layout from a profile file in controllers.d (see "Controller profiles" in
  MIDI.md): matched by its ALSA card id and name, applied when it is plugged in, under the person's own mappings.
* No more than 50 commands a second reach the player, whatever the controllers send.
* The web service needs the `audio` group and read access to ALSA devices (the systemd unit has both).
"""

import glob
import json
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
    # Performing with the shader on screen (shaderlive.py). A "control" follows the shader's n-th input whatever it
    # is: from a knob or fader a number spreads over MIN to MAX, a switch is on from 64 up, a choice is picked by
    # position; from a pad or button a switch toggles, a choice steps on, an event fires, a number goes back to its
    # default. The speed is 0 (frozen) to 4 times, 1 at a quarter of the way.
    "shader_speed": ("level", 0.0, 4.0), "shader_prev": ("trigger", None, None), "shader_next": ("trigger", None, None),
}
SHADER_SLOTS = 8
for _n in range(1, SHADER_SLOTS + 1):
    ACTIONS["shader_control_%d" % _n] = ("control", _n, None)
    ACTIONS["shader_preset_%d" % _n] = ("trigger", _n, None)
# Added with the controller profiles. Each is a call the API already had: the playlist's neighbours, a fade in, the
# shader's two other common controls, a Room scene by its place in the list, and a pad of "the controllers' bank"
# (a bank the hub keeps for controllers that have one row of pad buttons; bank_prev and bank_next change it).
ACTIONS.update({
    "clip_prev": ("trigger", None, None), "clip_next": ("trigger", None, None), "fadein": ("trigger", None, None),
    "shader_hue": ("level", -180.0, 180.0), "shader_brightness": ("level", 0.0, 2.0),
    "bank_pad": ("trigger", None, None), "bank_prev": ("trigger", None, None), "bank_next": ("trigger", None, None),
    "none": ("trigger", None, None),             # "do nothing": switches one control of a standard layout off
    # Vibes with one of the two sets every box starts with, by name (the API's {"on": true, "set": name}); a box
    # where that set was renamed or removed answers "no such set" and the log says so
    "vibes_ambient": ("trigger", "Ambient", None), "vibes_show": ("trigger", "Show", None),
})
SCENE_SLOTS = 8
for _n in range(1, SCENE_SLOTS + 1):
    ACTIONS["scene_%d" % _n] = ("trigger", _n, None)
BANKS = 3
# Soft takeover ("pickup"): on a recognised controller these levels do nothing until the fader or knob reaches the
# value the box has, so a fader left at the bottom does not black the screen out when it is first touched. The
# others (a shader's own inputs, its hue, the size and position) may jump: see MIDI.md.
PICKUP = ("opacity", "volume", "speed", "shader_speed", "shader_brightness")
PICKUP_TOLERANCE = 4        # of 127: this close to the box's value counts as reached
PICKUP_IDLE = 1.0           # a control that rested this long is checked against the box's value again
GUARD_MIN, GUARD_MAX = 0.25, 1.0   # a guarded button needs a second press this long after the first
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "controllers.d")
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


CARD_NAME = re.compile(r"[\x20-\x7e]{1,80}")


def card_name(path, asound="/proc/asound"):
    """The product name ALSA shows for the card behind a device path ("Launchpad Mini"), from /proc/asound/cards,
    or None. The card id alone is the last word of that name ("Mini"), which other products share."""
    m = DEVICE_PATH.fullmatch(path)
    if not m:
        return None
    try:
        with open(os.path.join(asound, "cards")) as f:
            for line in f.read(65536).splitlines():
                row = re.match(r"\s*([0-9]{1,3})\s+\[[^\]]*\]:\s*\S+\s+-\s+(.*\S)\s*$", line)
                if row and int(row.group(1)) == int(m.group(1)) and CARD_NAME.fullmatch(row.group(2)):
                    return row.group(2)
    except OSError:
        pass
    return None


# --- controller profiles ---------------------------------------------------
# A profile is one JSON file in controllers.d: which controller it is for, a drawing of it (a grid of named controls),
# what each control sends, and what each does by default. The files are shipped with the program and read-only; they
# are checked strictly, and a file that fails the check is left out with a line in the log. See MIDI.md.
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CONTROL_ID = re.compile(r"[a-z0-9][a-z0-9_]{0,23}")
CONTROL_KINDS = ("fader", "knob", "button", "pad")
MATCH_PATTERN = re.compile(r"[A-Za-z0-9 ._()\[\]?*+|-]{1,64}")
PRINTABLE = re.compile(r"[\x20-\x7e]+")
MAX_CONTROLS, MAX_SIDE, MAX_PROFILES = 160, 16, 64


def _text(v, what, most):
    if not isinstance(v, str) or not 1 <= len(v) <= most or not PRINTABLE.fullmatch(v):
        raise MidiError("%s must be 1 to %d plain characters" % (what, most))
    return v


def _whole(v, what, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise MidiError("%s must be a whole number from %d to %d" % (what, lo, hi))
    return v


def _keys(d, what, required, optional=()):
    if not isinstance(d, dict):
        raise MidiError("%s must be an object" % what)
    missing, extra = [k for k in required if k not in d], [k for k in d if k not in required and k not in optional]
    if missing or extra:
        raise MidiError("%s: %s" % (what, "missing " + ", ".join(missing) if missing else "unknown key " + ", ".join(sorted(map(str, extra)))))


def clean_action(a, kind="note"):
    """A profile's or an override's action ({"action": "pad", "bank": 0, "index": 3}), checked by the same rules as a
    learned mapping. Returns only the action's own fields."""
    if not isinstance(a, dict):
        raise MidiError("an action must be an object")
    if any(k not in ("action", "bank", "index", "scene") for k in a):
        raise MidiError("an action has only action, bank, index and scene")
    e = validate_entry(dict(a, kind=kind, number=0, channel=0, source="*"))
    return {k: e[k] for k in ("action", "bank", "index", "scene") if k in e}


def validate_profile(p, stem=None):
    """A clean profile from a parsed file. Raises MidiError with the reason."""
    _keys(p, "profile", ("id", "name", "match", "description", "sources", "layout", "controls"), ("note",))
    if not isinstance(p["id"], str) or not PROFILE_ID.fullmatch(p["id"]) or (stem is not None and p["id"] != stem):
        raise MidiError("id must be the file's name: small letters, digits and dashes")
    out = {"id": p["id"], "name": _text(p["name"], "name", 60), "description": _text(p["description"], "description", 1200),
           "note": _text(p["note"], "note", 1200) if "note" in p else ""}
    if not isinstance(p["sources"], list) or not 1 <= len(p["sources"]) <= 8:
        raise MidiError("sources must list one to eight documents")
    out["sources"] = [_text(s, "a source", 400) for s in p["sources"]]
    _keys(p["match"], "match", ("card_ids", "card_names"))
    out["match"] = {}
    for key, least in (("card_ids", 1), ("card_names", 0)):
        pats = p["match"][key]
        if not isinstance(pats, list) or not least <= len(pats) <= 8:
            raise MidiError("match.%s must be a list of %d to 8 patterns" % (key, least))
        for pat in pats:
            if not isinstance(pat, str) or not MATCH_PATTERN.fullmatch(pat):
                raise MidiError("match.%s: a pattern is 1 to 64 letters, digits, spaces and . _ - ( ) [ ] ? * + |" % key)
            try:
                re.compile(pat)
            except re.error:
                raise MidiError("match.%s: %r is not a pattern" % (key, pat))
        out["match"][key] = list(pats)
    _keys(p["layout"], "layout", ("rows", "cols"))
    rows, cols = _whole(p["layout"]["rows"], "layout.rows", 1, MAX_SIDE), _whole(p["layout"]["cols"], "layout.cols", 1, MAX_SIDE)
    out["layout"] = {"rows": rows, "cols": cols}
    if not isinstance(p["controls"], list) or not 1 <= len(p["controls"]) <= MAX_CONTROLS:
        raise MidiError("controls must list 1 to %d controls" % MAX_CONTROLS)
    ids, cells, sends, controls = set(), set(), set(), []
    for c in p["controls"]:
        _keys(c, "a control", ("id", "name", "row", "col", "kind", "send", "action"), ("guard", "unverified"))
        if not isinstance(c["id"], str) or not CONTROL_ID.fullmatch(c["id"]) or c["id"] in ids:
            raise MidiError("a control's id must be unique: small letters, digits and _ (%r)" % (c["id"],))
        what = "control %s" % c["id"]
        ids.add(c["id"])
        cell = (_whole(c["row"], what + " row", 0, rows - 1), _whole(c["col"], what + " col", 0, cols - 1))
        if cell in cells:
            raise MidiError("%s: two controls in one place" % what)
        cells.add(cell)
        if c["kind"] not in CONTROL_KINDS:
            raise MidiError("%s: kind must be fader, knob, button or pad" % what)
        _keys(c["send"], what + " send", ("type", "channel", "number"))
        if c["send"]["type"] not in ("note", "cc"):
            raise MidiError("%s: send.type must be note or cc" % what)
        send = {"type": c["send"]["type"], "channel": _whole(c["send"]["channel"], what + " channel", 0, 16),
                "number": _whole(c["send"]["number"], what + " number", 0, 127)}
        if (send["type"], send["number"]) in sends:
            raise MidiError("%s: two controls send the same message" % what)
        sends.add((send["type"], send["number"]))
        if c["kind"] in ("fader", "knob") and send["type"] != "cc":
            raise MidiError("%s: a fader or knob sends a cc" % what)
        action = None
        if c["action"] is not None:
            try:
                action = clean_action(c["action"], send["type"])
            except MidiError as e:
                raise MidiError("%s: %s" % (what, e))
            if "scene" in action or action["action"] == "none":
                raise MidiError("%s: a profile cannot name a scene id or the action none" % what)
            level = ACTIONS[action["action"]][0] in ("level", "control")
            if c["kind"] in ("fader", "knob") and not level:
                raise MidiError("%s: a fader or knob needs an action that follows it" % what)
            if c["kind"] in ("button", "pad") and ACTIONS[action["action"]][0] == "level":
                raise MidiError("%s: a button or pad needs an action that is pressed" % what)
        for flag in ("guard", "unverified"):
            if flag in c and not isinstance(c[flag], bool):
                raise MidiError("%s: %s must be true or false" % (what, flag))
        if c.get("guard") and (action is None or c["kind"] not in ("button", "pad")):
            raise MidiError("%s: only a button or pad with an action can be guarded" % what)
        controls.append({"id": c["id"], "name": _text(c["name"], what + " name", 24), "row": cell[0], "col": cell[1], "kind": c["kind"],
                         "send": send, "action": action, "guard": bool(c.get("guard")), "unverified": bool(c.get("unverified"))})
    out["controls"] = controls
    return out


def load_profiles(folder=PROFILE_DIR, log=print):
    """Every profile file that passes the check, in the order of their file names."""
    found = []
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))[:MAX_PROFILES]
    except OSError:
        return found
    for n in names:
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                found.append(validate_profile(json.loads(f.read(262144)), n[:-5]))
        except (OSError, ValueError, MidiError) as e:
            log("midi: controller profile %s left out: %s" % (n, e))
    return found


def match_profile(profiles, card_id, name=None):
    """The first profile whose card id pattern matches as a whole and, when the card's product name is known and the
    profile lists names, whose name pattern matches too. None if there is none."""
    for p in profiles:
        if not any(re.fullmatch(pat, card_id) for pat in p["match"]["card_ids"]):
            continue
        if name is not None and p["match"]["card_names"] and not any(re.fullmatch(pat, name) for pat in p["match"]["card_names"]):
            continue
        return p
    return None


def profile_entries(profile, source):
    """A profile's default actions as map entries for the controller called `source`."""
    out = []
    for c in profile["controls"]:
        if c["action"] is None:
            continue
        e = dict(c["action"], id="p:" + c["id"], source=source, kind=c["send"]["type"], channel=c["send"]["channel"],
                 number=c["send"]["number"], profile=True, guard=c["guard"])
        e["pickup"] = e["action"] in PICKUP
        out.append(e)
    return out


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
    if action == "bank_pad":
        v = e.get("index")
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 11:
            raise MidiError("pad index must be 0 to 11")
        out["index"] = v
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
        self.profiled = set()               # controllers whose standard layout is on: the built-in map is not for them
        self.target = lambda action: None   # the value the box has now, for pickup; the hub sets it. Must never wait
        self.bank = 0                       # the controllers' bank, for bank_pad; not saved
        self._pick = {}                     # (source, kind, number) -> {"caught", "prev", "sent", "at"}
        self._armed = {}                    # trigger key -> time of the first press of a guarded button

    def matching(self, source, kind, channel, number):
        """Entries for this control, in the order of precedence: the person's own mapping (it replaces the others
        instead of firing next to them), else the controller's standard layout, else the built-in map; and the
        built-in map is never used for a controller whose standard layout is on."""
        found = [e for e in self.entries if e["kind"] == kind and e["number"] == number
                 and e["source"] in ("*", source) and e["channel"] in (0, channel + 1)]
        mine = [e for e in found if not e.get("builtin") and not e.get("profile")]
        if mine:                                # a mapping for this controller comes before one made for any controller
            return [e for e in mine if e["source"] == source] or mine
        standard = [e for e in found if e.get("profile")]
        if standard or source in self.profiled:
            return standard
        return found

    def forget(self, source):
        """A controller went: its pickup and guard state go with it, so it starts clean when it comes back."""
        for store in (self._pick, self._armed, self._pressed, self.pending):
            for key in [k for k in store if source in k[:2]]:
                del store[key]

    def waiting(self, source, kind, number):
        """True while a fader or knob has been moved but has not reached the box's value yet."""
        st = self._pick.get((source, kind, number))
        return bool(st and not st["caught"])

    def _picked_up(self, e, key, value, now):
        """Soft takeover. False while the control has not reached the value the box has."""
        _, lo, hi = ACTIONS[e["action"]]
        have = self.target(e["action"])
        if have is None:
            return True
        at = (min(max(have, lo), hi) - lo) * 127.0 / (hi - lo)
        st = self._pick.get(key)
        if st and st["caught"] and (now - st["at"] < PICKUP_IDLE or abs(st["sent"] - at) <= PICKUP_TOLERANCE + 2):
            st.update(prev=value, sent=value, at=now)
            return True
        prev = st["prev"] if st else None
        caught = abs(value - at) <= PICKUP_TOLERANCE or (prev is not None and (prev - at) * (value - at) <= 0)
        self._pick[key] = {"caught": caught, "prev": value, "sent": value, "at": now}
        return caught

    def _trigger_calls(self, e):
        a = e["action"]
        if a == "pad":
            return [("/api/play", {"pad": [e["bank"], e["index"]]})]
        if a == "bank_pad":
            return [("/api/play", {"pad": [self.bank, e["index"]]})]
        if a in ("bank_next", "bank_prev"):
            self.bank = (self.bank + (1 if a == "bank_next" else -1)) % BANKS
            return []
        if a in ("clip_next", "clip_prev"):
            return [("/api/control", {"action": "next" if a == "clip_next" else "prev"})]
        if a == "fadein":
            return [("/api/fadein", {"seconds": 2})]
        if a.startswith("scene_"):
            return [("/api/room/scene", {"number": ACTIONS[a][1]})]
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
        if a in ("vibes_ambient", "vibes_show"):
            return [("/api/vibes", {"on": True, "set": ACTIONS[a][1]})]
        if a in ("shader_next", "shader_prev"):
            return [("/api/shaders/step", {"dir": 1 if a == "shader_next" else -1})]
        if a.startswith("shader_preset_"):
            return [("/api/shaders/preset", {"index": ACTIONS[a][1]})]
        if a.startswith("shader_control_"):
            return [("/api/shaders/values", {"control": ACTIONS[a][1], "press": True})]
        return []

    @staticmethod
    def _level_calls(e, value):
        a = e["action"]
        if a == "blackout_hold":
            return [("/api/blackout", {"on": value >= 64})]
        if a == "vibes_dwell":
            return [("/api/vibes", {"dwell": VIBES_DWELLS[min(len(VIBES_DWELLS) - 1, value * len(VIBES_DWELLS) // 128)]})]
        if a.startswith("shader_control_"):
            return [("/api/shaders/values", {"control": ACTIONS[a][1], "level": value})]
        _, lo, hi = ACTIONS[a]
        if a in ("shader_speed", "shader_hue", "shader_brightness"):
            return [("/api/shaders/values", {"controls": {a[7:]: round(lo + (hi - lo) * value / 127.0, 2)}})]
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
            if kind_of == "control":                    # a knob or fader is followed; a pad or button is a press
                kind_of = "level" if kind == "cc" else "trigger"
            if kind_of == "trigger":
                down = d2 >= 64 if kind == "cc" else (d2 > 0 or kind == "program")
                was = self._pressed.get(key, False)
                self._pressed[key] = down if kind != "program" else False
                if down and not was and now - self._fired.get(key, -1e9) >= TRIGGER_GAP:
                    self._fired[key] = now
                    if e.get("guard"):                  # the same press twice: the first only arms it
                        first = self._armed.pop(key, None)
                        if first is None or not GUARD_MIN <= now - first <= GUARD_MAX:
                            self._armed[key] = now
                            continue
                    calls.extend(self._trigger_calls(e))
            else:
                if e.get("pickup") and not self._picked_up(e, (source, kind, d1), d2, now):
                    self.pending.pop(key, None)
                    continue
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
                 clock=time.monotonic, scan_interval=2.0, profiles=None, describer=card_name):
        self.api, self.settings, self.log = api, settings, log
        self._open_fn, self._lister, self._namer, self._clock = open_fn, lister, namer, clock
        self.profiles = load_profiles(log=log) if profiles is None else profiles
        self._describer = describer
        self._matched = {}        # device path -> (source, profile or None), kept while the device is there
        self._standard = {}       # (profile id, source) -> its entries, made once
        self.activity = {}        # (source, kind, number) -> (time, value) of the last message, for the drawn layout
        self._sent = {}           # action -> the last level a controller set (for pickup, where the box keeps no value)
        self.scan_interval = scan_interval
        self._lock = threading.RLock()
        self.inputs = {}          # path -> MidiInput
        self._scanner = None
        self._stop = threading.Event()
        self.calls = RateLimiter(clock, rate=MAX_CALLS_PER_SECOND, burst=MAX_CALLS_PER_SECOND)   # a faulty pad cannot flood the player
        self.mapper = MidiMapper(self._do, [], api.mix, clock)
        self.mapper.target = self._target
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
        if path == "/api/vibes" or path.startswith("/api/shaders/"):   # only with the Shaders and Vibes module on; said once
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

    # --- profiles ---------------------------------------------------------
    def profile_for(self, source, path=None):
        """The profile of the controller called `source` (its ALSA card id), or None. With the device path the card's
        product name is checked too, and the answer is kept while the device is there."""
        if path is not None and self._matched.get(path, (None,))[0] == source:
            return self._matched[path][1]
        name = None
        if path is not None:
            try:
                name = self._describer(path)
            except Exception:
                name = None
        found = match_profile(self.profiles, source, name)
        if path is not None:
            self._matched[path] = (source, found)
        return found

    def profile_of(self, source):
        """The profile a connected controller's device was matched with (by card id and product name), or None.
        Messages and the API's set and reset use this one, so both mean the same layout."""
        for src, found in list(self._matched.values()):
            if src == source:
                return found
        return None

    def standard_on(self, source):
        """Is the standard layout switched on for this controller. On unless someone switched it off."""
        entry = self.cfg().get("controllers", {})
        entry = entry.get(source) if isinstance(entry, dict) else None
        return not (isinstance(entry, dict) and entry.get("standard") is False)

    def _target(self, action):
        """The value the box has for a level, for pickup. Reads only what is in memory: it runs on the thread that
        reads the controller and must never wait for the player or for the shader engine's lock."""
        try:
            if action == "opacity":
                return float(self.api.mix["opacity"])
            if action in ("shader_speed", "shader_brightness"):
                playing = getattr(getattr(self.api, "shaders", None), "playing", None)
                return float(playing["controls"][action[7:]]) if playing else None
        except (KeyError, TypeError, ValueError):
            return None
        return self._sent.get(action, {"volume": 100.0, "speed": 1.0}.get(action))

    def entries(self, source=None):
        """The user's mappings (each re-checked: settings are a file a person may have edited), then the standard
        layout of the controller the message came from (if it has one and it is on), then the built-in map."""
        c = self.cfg()
        profile = self.profile_of(source) if source is not None else None
        if profile is not None and not self.standard_on(source):
            profile = None
        mine = []
        for e in c["map"]:
            try:
                e = validate_entry(e, keep_id=True)
            except MidiError:
                continue
            if profile is not None and e["source"] == source:
                e["pickup"] = e["action"] in PICKUP          # the same soft takeover as the layout it replaces
            mine.append(e)
        standard = []
        if profile is not None:
            key = (profile["id"], source)
            if key not in self._standard:
                self._standard[key] = profile_entries(profile, source)
            standard = self._standard[key]
        self.mapper.profiled = {source} if profile is not None else set()
        return mine + standard + (builtin_cached() if c["builtin"] else [])

    # --- messages ---------------------------------------------------------
    def _run_calls(self, calls):
        """Make the calls a message planned. Called WITHOUT the hub lock: a play is many round trips to the player."""
        for path, body in calls:
            if path == "/api/blackout" and body.get("on") is None:
                body = {"on": not self.api.mix.get("blackout", False)}
            if path == "/api/vibes" and "on" in body and body["on"] is None:
                body = {"on": not self.api.vibes.running}
            if self._do(path, body) and path == "/api/control" and body.get("action") in ("volume", "speed"):
                self._sent[body["action"]] = body["value"]

    def on_message(self, source, msg):
        if msg is None:
            with self._lock:
                calls = self.mapper.flush_calls()
            self._run_calls(calls)
            return
        with self._lock:
            kind, channel, d1, d2 = msg
            now = self._clock()
            if len(self.activity) > 1024:                                   # a faulty device cannot grow this without end
                self.activity.clear()
            self.activity[(source, "note" if kind in ("on", "off") else kind, d1)] = (now, 0 if kind == "off" else d2)
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
            self.mapper.entries = self.entries(source)
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
                    gone = self.inputs.pop(path)
                    gone.stop()
                    self._matched.pop(path, None)       # unplugged: its layout goes with it
                    if not any(i.source == gone.source for i in self.inputs.values()):
                        self.mapper.forget(gone.source)
                        for key in [k for k in self.activity if k[0] == gone.source]:
                            del self.activity[key]
            for path in sorted(paths - set(self.inputs)):
                source = self._namer(path)
                inp = MidiInput(path, source, self.on_message, log=self.log, open_fn=self._open_fn)
                self.inputs[path] = inp
                found = self.profile_for(source, path)
                if found is not None:
                    self.log("midi: %s is a %s: its standard layout is %s" % (source, found["name"], "on" if self.standard_on(source) else "switched off"))
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
            self._matched.clear()
            self.activity.clear()
            self.learn_until, self.captured = 0.0, None
        if scanner:
            scanner.join(timeout=3)
        for inp in inputs:
            inp.stop()

    def stop(self):
        self._stop_all()

    def _controller(self, device, c, now):
        """One connected controller for the panel: its profile, what each control does now and where that comes from
        ("yours", "standard" or nothing), and what it last sent."""
        source = device["name"]
        profile = self.profile_for(source, device["path"]) if device["path"] in self.inputs else None
        out = {"name": source, "path": device["path"], "connected": device["connected"], "messages": device["messages"],
               "profile": None, "standard": self.standard_on(source), "controls": []}
        if profile is None:
            return out
        out["profile"] = {k: profile[k] for k in ("id", "name", "description", "note", "sources")}
        out["profile"].update(profile["layout"])
        mine = {}
        for e in c["map"]:
            try:
                e = validate_entry(e, keep_id=True)
            except MidiError:
                continue
            if e["source"] in (source, "*"):                  # the controller's own mapping first, then one for any controller
                key = (e["kind"], e["number"])
                if key not in mine or (mine[key]["source"] == "*" and e["source"] == source):
                    mine[key] = e
        for ctl in profile["controls"]:
            send = ctl["send"]
            key = (send["type"], send["number"])
            item = {k: ctl[k] for k in ("id", "name", "row", "col", "kind", "send", "unverified")}
            if key in mine:
                e = mine[key]
                item.update(action={k: e[k] for k in ("action", "bank", "index", "scene") if k in e}, guard=False,
                            origin="yours" if e["source"] == source else "any",      # "any": made for every controller; removed in the list
                            pickup=out["standard"] and e["source"] == source and e["action"] in PICKUP)
            elif out["standard"] and ctl["action"] is not None:
                item.update(action=dict(ctl["action"]), origin="standard", guard=ctl["guard"], pickup=ctl["action"]["action"] in PICKUP)
            else:
                item.update(action=None, origin=None, guard=False, pickup=False)
            item["standard"] = dict(ctl["action"]) if ctl["action"] is not None else None
            seen = self.activity.get((source,) + key)
            item["value"] = seen[1] if seen else None
            item["ago"] = round(now - seen[0], 2) if seen else None
            item["waiting"] = self.mapper.waiting(source, *key)
            out["controls"].append(item)
        return out

    def status(self):
        with self._lock:
            c = self.cfg()
            now = self._clock()
            seen = {p: i for p, i in self.inputs.items()}
            devices = [{"path": p, "name": self._namer(p), "connected": p in seen and seen[p].connected,
                        "messages": seen[p].messages if p in seen else 0} for p in self._lister()]
            learning = bool(self.learn_until and now < self.learn_until)
            return {"enabled": c["enabled"], "builtin": c["builtin"], "devices": devices, "map": list(c["map"]),
                    "last": self.mapper.last_message, "bank": self.mapper.bank,
                    "controllers": [self._controller(d, c, now) for d in devices] if c["enabled"] else [],
                    "profiles": [{"id": p["id"], "name": p["name"]} for p in self.profiles],
                    "learn": {"active": learning, "captured": self.captured, "seconds_left": max(0, round(self.learn_until - now)) if learning else 0}}


def validate(body, current):
    """New switches (enabled, builtin) from untrusted input, based on `current`. The map has its own calls."""
    new = dict(current)
    for key in ("enabled", "builtin"):
        if key in body:
            if not isinstance(body[key], bool):
                raise MidiError("%s must be true or false" % key)
            new[key] = body[key]
    if "controller" in body or "standard" in body:      # the standard layout of one controller, on or off
        name = body.get("controller")
        if not isinstance(name, str) or not SOURCE.fullmatch(name):
            raise MidiError("bad controller name")
        if not isinstance(body.get("standard"), bool):
            raise MidiError("standard must be true or false")
        switches = validate_controllers(current.get("controllers", {}))
        if body["standard"]:
            switches.pop(name, None)                     # on is the default: nothing is kept for it
        else:
            switches[name] = {"standard": False}
        new["controllers"] = validate_controllers(switches)
    return new


MAX_CONTROLLERS = 32


def validate_controllers(v):
    """The per-controller switches as stored: {"<controller name>": {"standard": false}}. Raises MidiError."""
    if not isinstance(v, dict) or len(v) > MAX_CONTROLLERS:
        raise MidiError("controllers must be an object with at most %d controllers" % MAX_CONTROLLERS)
    out = {}
    for name, entry in v.items():
        if not isinstance(name, str) or not SOURCE.fullmatch(name):
            raise MidiError("bad controller name")
        if not isinstance(entry, dict) or list(entry) != ["standard"] or not isinstance(entry["standard"], bool):
            raise MidiError("a controller has one switch: standard, true or false")
        out[name] = {"standard": entry["standard"]}
    return out


def override_entry(profile, source, control_id, action):
    """The map entry that makes one control of a recognised controller do `action` instead of the standard."""
    ctl = next((c for c in profile["controls"] if c["id"] == control_id), None)
    if ctl is None:
        raise MidiError("no such control")
    clean = clean_action(action, ctl["send"]["type"])
    level = ACTIONS[clean["action"]][0]
    if ctl["kind"] in ("fader", "knob") and level not in ("level", "control") and clean["action"] != "none":
        raise MidiError("a fader or knob needs an action that follows it")
    if ctl["kind"] in ("button", "pad") and level == "level":
        raise MidiError("a button or pad needs an action that is pressed")
    return dict(clean, source=source, kind=ctl["send"]["type"], channel=ctl["send"]["channel"], number=ctl["send"]["number"])


def reset_entries(current_map, profile, source, control_id=None):
    """The map without the person's own mappings for the controls of this controller's layout (or for one of them).
    Mappings for any controller, and this controller's mappings on numbers that are not in its layout, are kept."""
    if control_id is None:
        drawn = {(c["send"]["type"], c["send"]["number"]) for c in profile["controls"]}
        return [e for e in current_map if not (e.get("source") == source and (e.get("kind"), e.get("number")) in drawn)]
    ctl = next((c for c in profile["controls"] if c["id"] == control_id), None)
    if ctl is None:
        raise MidiError("no such control")
    return [e for e in current_map if not (e.get("source") == source and e.get("kind") == ctl["send"]["type"]
                                           and e.get("number") == ctl["send"]["number"])]


def add_entry(current_map, entry):
    if len(current_map) >= MAX_MAP:
        raise MidiError("at most %d mappings" % MAX_MAP)
    clean = validate_entry(entry)
    # one control does one thing: a new mapping replaces an older one for the same control
    kept = [e for e in current_map if not (e["kind"] == clean["kind"] and e["number"] == clean["number"]
                                           and e["source"] == clean["source"] and e["channel"] == clean["channel"])]
    return kept + [clean]
