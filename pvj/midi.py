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
* Nothing is written to a device, with one exception: the lights of a controller that matched a profile with a
  "lights" section (see "lights" below). Only the fixed messages of that section are written, on a second,
  write-only handle, by a rate-limited writer on its own thread. A controller without a profile is never opened
  for writing.
* Only play (pads), stop, pause, fade, blackout, reset, opacity, size, position, speed, volume, the clip before and
  after in the playlist, a Room scene, the shader rotation (Vibes on or off, next shader, dwell time) and the shader
  on screen (its first eight inputs, its speed, hue and brightness, the shader before and after it in the active set,
  its first eight presets) are reachable.
* One more thing can be asked for, and it is not a call into the API: a one-time pairing code on the box's own
  display (the actions code_join and code_owner; D61, controllercode.py). It needs a hold of HOLD_MIN to HOLD_MAX
  seconds that ends with letting go, a box setting that is off unless a full-access device switched it on, and a
  controller that is on USB. Nothing about the code is ever written to a controller.
* A known controller gets a ready-made layout from a profile file in controllers.d (see "Controller profiles" in
  MIDI.md): matched by its ALSA card id and name, applied when it is plugged in, under the person's own mappings.
* No more than 50 commands a second reach the player, whatever the controllers send.
* The web service needs the `audio` group and access to ALSA devices: read for the controls, write for the lights
  (the systemd unit has `DeviceAllow=char-alsa rw` since D53; under an older unit the lights say so and stay off).
"""

import fcntl
import glob
import json
import os
import queue
import re
import select
import stat
import struct
import threading
import time
import uuid

from . import actions
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
# Effects: a filter over whatever plays (effects.py). The amount is the mix between the picture as it is and the
# filtered one; a "control" follows the n-th input of the effect that is on, as a shader control does; one button
# puts the last effect back on or takes it off; two more step through the filters.
ACTIONS.update({"effect_amount": ("level", 0.0, 1.0), "effect_toggle": ("trigger", None, None),
                "effect_prev": ("trigger", None, None), "effect_next": ("trigger", None, None)})
for _n in range(1, SHADER_SLOTS + 1):
    ACTIONS["effect_control_%d" % _n] = ("control", _n, None)
# A pairing code on the box's display, for someone at the box with no paired device (D61). Kind "hold": nothing
# happens on a press; the control must be held for HOLD_MIN to HOLD_MAX seconds and then let go. So a tap, a stuck
# note (it never ends) and something left lying on a button (it ends too late or never) all do nothing. The second
# field is the kind of code asked for (controllercode.KINDS). These two are not API calls: see MidiHub._local.
ACTIONS.update({"code_join": ("hold", "join", None), "code_owner": ("hold", "owner", None)})
HOLD_MIN, HOLD_MAX = 3.0, 10.0
# Added with the editable layouts (D75), after the first report from the real controllers ("odd, or not helpful, or
# incomplete. especially, the zoom and x/y position"). Each is something the panel could already do and a controller
# could not: the picture's other axis, its quarter turns and flips, the overlay, sound off, the loop, a step of ten
# seconds, the test pattern, and ONE fade button (out, and at the next press in; the API decides which, from what the
# screen is doing, so it stays right when a fade was started somewhere else).
ACTIONS.update({
    "position_y": ("level", -100, 100),
    "fade": ("trigger", None, None), "rotate": ("trigger", None, None),
    "flip_h": ("trigger", None, None), "flip_v": ("trigger", None, None), "mute": ("trigger", None, None),
    "loop": ("trigger", None, None), "overlay": ("trigger", None, None), "test_pattern": ("trigger", None, None),
    "seek_back": ("trigger", -10, None), "seek_forward": ("trigger", 10, None),
})
# Mapping mode (D75; pvj/mapper.py, "from a controller"). One button enters and leaves it (press twice, as for
# Blackout: it changes what other controls do). The others do something only while it is on; outside it the box
# refuses them. "delta" is a knob that is followed by how far it is TURNED, not by where it stands: the chosen
# corner moves a step for each step of the knob, and the first touch moves nothing.
ACTIONS.update({a: ("trigger", None, None) for a in (
    "mapping_mode", "map_surface_next", "map_surface_prev", "map_corner_next", "map_corner_prev",
    "map_left", "map_right", "map_up", "map_down", "map_step", "map_undo")})
ACTIONS.update({"map_x": ("delta", "x", None), "map_y": ("delta", "y", None)})
# A LAYER on a controller: a state in which a few of its controls do something else, shown by a flashing light, and
# only one at a time. A profile gives a control its other self per layer ("layers": {"geometry": {...}}); everything
# without one works as always. Two layers exist:
# * "mapping": mapping mode. It belongs to the box (the mapper keeps it, behind the owner's switch), is entered and
#   left by the action mapping_mode, and applies to every controller that has such controls.
# * "geometry": the owner, 2026-10-10, for the nanoKONTROL2: "all eight knobs for shaders ... use a button to
#   switch to the geometry controls and flash the button when in that state". It belongs to the one controller, is
#   entered and left by the action layer_geometry (a plain press: it changes nothing on the screen by itself), and
#   ends by itself LAYER_SECONDS after the last touch of one of its controls, so that forgotten, it does not leave
#   the shader knobs dead. A control whose other self is null does nothing in the layer.
LAYERS = ("mapping", "geometry")
LAYER_SECONDS = {"geometry": 120.0}
ACTIONS["layer_geometry"] = ("trigger", "geometry", None)
# How a level follows a knob or fader: action -> (low, high, centre, curve). The second and third fields of ACTIONS
# stay the WIDEST range a mapping may ask for with its own "min" and "max"; these are what a control gets when it
# asks for nothing.
# * A level with a centre has a place where it is "as it was made": 100 percent size, no shift, the clip's own
#   speed. The middle of the control IS that value, exactly, over CENTRE_HALF steps to either side (a knob has no
#   notch, and 0 to 127 has no middle step: 63.5), so the picture sits centred when the knob looks centred.
# * A curve above 1 gives the steps next to the centre less to do and the steps at the ends more: fine control
#   where a picture is lined up, the whole range still within reach.
# * Size: 25 to 200. Under 25 percent a picture is a speck; a mapping that wants the panel's whole range says "min": 1.
SHAPES = {
    "opacity": (0, 100, None, 1.0), "volume": (0, 100, None, 1.0), "effect_amount": (0.0, 1.0, None, 1.0),
    "size": (25, 200, 100, 1.6), "position": (-100, 100, 0, 1.6), "position_y": (-100, 100, 0, 1.6),
    "speed": (0.25, 2.0, 1.0, 1.0), "shader_speed": (0.0, 4.0, 1.0, 1.0),
    "shader_hue": (-180.0, 180.0, 0.0, 1.0), "shader_brightness": (0.0, 2.0, 1.0, 1.0),
}
CENTRE_HALF = 4             # of 127: this close to the middle of a control is the centre of a level that has one
TAKEOVERS = ("pickup", "jump")
OPTION_KEYS = ("min", "max", "invert", "takeover")      # what a mapping may say about how its level follows the control
SLOW_CALLS = 0.5            # calls that took longer than this, for a message with no read time: what is held is forgotten
LOCAL_CODE = "code"         # a planned call with this in the place of a path goes to the hub's own _local, never to the API
BANKS = 3
# Soft takeover ("pickup"): on a recognised controller these levels do nothing until the fader or knob reaches the
# value the box has, so a fader left at the bottom does not black the screen out when it is first touched, and a knob
# that is not where the picture is does not throw the picture across the screen. The others (a shader's own inputs
# and its hue) may jump: see MIDI.md. A mapping can say otherwise for itself ("takeover": "pickup" or "jump").
PICKUP = ("opacity", "volume", "speed", "shader_speed", "shader_brightness", "effect_amount", "size", "position", "position_y")
PICKUP_TOLERANCE = 4        # of 127: this close to the box's value counts as reached
PICKUP_END = 8              # of 127: this close to the top or the bottom is the top or the bottom
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
USB_ID = re.compile(r"[0-9a-f]{4}:[0-9a-f]{4}")


def card_info(path, asound="/proc/asound"):
    """What ALSA says about the card behind a device path: {"name": its product name ("Launchpad Mini") or None,
    "usbid": "1235:0036" or None, "readable": whether the card list could be read at all}. The card id alone is the
    last word of the product name ("Mini"), which other products share, so a profile is matched on these too.
    The list is read as bytes (a controller can name itself anything) and only the row that carries this card's own
    number AND id counts; a name that is not plain printable text is "unusable" (None with readable True), which is
    not the same as "could not look"."""
    out = {"name": None, "usbid": None, "readable": False}
    m = DEVICE_PATH.fullmatch(path)
    if not m:
        return out
    number = int(m.group(1))
    try:
        with open(os.path.join(asound, "card%d" % number, "usbid"), "rb") as f:
            usbid = f.read(64).decode("ascii", "replace").strip().lower()
        if USB_ID.fullmatch(usbid):
            out["usbid"] = usbid
    except OSError:
        pass
    try:
        with open(os.path.join(asound, "card%d" % number, "id"), "rb") as f:
            card_id = f.read(64).decode("ascii", "replace").strip()
        with open(os.path.join(asound, "cards"), "rb") as f:
            text = f.read(65536).decode("utf-8", "replace")
    except OSError:
        return out
    out["readable"] = True
    for line in text.split("\n"):
        row = re.match(r" {0,2}([0-9]{1,3}) \[(.{15})\]: (\S+) - (.*)$", line)
        if row and int(row.group(1)) == number and row.group(2).rstrip() == card_id:
            name = row.group(4).rstrip()
            if CARD_NAME.fullmatch(name):
                out["name"] = name
            break
    return out


def card_name(path, asound="/proc/asound"):
    """The product name alone, or None (see card_info)."""
    return card_info(path, asound)["name"]


# --- controller profiles ---------------------------------------------------
# A profile is one JSON file in controllers.d: which controller it is for, a drawing of it (a grid of named controls),
# what each control sends, and what each does by default. The files are shipped with the program and read-only; they
# are checked strictly, and a file that fails the check is left out with a line in the log. See MIDI.md.
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CONTROL_ID = re.compile(r"[a-z0-9][a-z0-9_]{0,23}")
CONTROL_KINDS = ("fader", "knob", "button", "pad")
MATCH_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")       # exact names only: no patterns, so nothing a file says can make matching slow
PRINTABLE = re.compile(r"[\x20-\x7e]+")
MAX_CONTROLS, MAX_SIDE, MAX_PROFILES = 160, 16, 64
# What a control is for, so the drawing can tint the parts of a controller that belong together and name them once.
# A fixed list: a file chooses among these words and can put no word of its own on the page.
ZONES = ("pads", "clips", "screen", "picture", "sound", "shaders", "effects", "room", "access")


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


def level_shape(action, opts=None):
    """(low, high, centre or None, curve) for a level as one mapping has it: the action's own shape, with the
    mapping's "min" and "max" in place of the ends. A centre that the chosen range does not hold is no centre: the
    control then spreads evenly over the range."""
    lo, hi, centre, curve = SHAPES[action]
    if opts:
        lo, hi = opts.get("min", lo), opts.get("max", hi)
    if centre is not None and not lo < centre < hi:
        centre, curve = None, 1.0
    return lo, hi, centre, curve


def level_value(action, v, opts=None):
    """The level a control at `v` (0 to 127) asks for. `opts` is the mapping (its min, max and invert are read)."""
    lo, hi, centre, curve = level_shape(action, opts)
    if opts and opts.get("invert"):
        v = 127 - v
    if centre is None:
        return lo + (hi - lo) * v / 127.0
    if abs(v - 64) <= CENTRE_HALF:
        return float(centre)
    if v < 64:
        t = (64 - CENTRE_HALF - v) / float(64 - CENTRE_HALF)
        return centre + (lo - centre) * t ** curve
    t = (v - 64 - CENTRE_HALF) / float(127 - 64 - CENTRE_HALF)
    return centre + (hi - centre) * t ** curve


_TABLES = {}


def level_position(action, have, opts=None):
    """Where a control must stand (0 to 127) for the box's value `have`: the exact reverse of level_value, read from
    its own table, so pickup catches where the control really gives that value, whatever the curve, the range and
    the direction. A value several steps give (the centre) is the middle one of them; a value outside the range is
    the end nearest to it."""
    key = (action, (opts or {}).get("min"), (opts or {}).get("max"), bool((opts or {}).get("invert")))
    table = _TABLES.get(key)
    if table is None:
        if len(_TABLES) > 512:                  # a mapping's range is free: nothing may grow without end
            _TABLES.clear()
        table = _TABLES[key] = [level_value(action, v, opts) for v in range(128)]
    best = min(abs(x - have) for x in table)
    hits = [v for v, x in enumerate(table) if abs(x - have) <= best + 1e-9]
    return hits[len(hits) // 2] if len(hits) > 1 else hits[0]


def clean_options(e, action):
    """A mapping's own way of following its control, from untrusted input: {"min", "max", "invert", "takeover"},
    each optional. Only a level that has a shape takes them. Raises MidiError."""
    found = {k: e[k] for k in OPTION_KEYS if k in e}
    if not found:
        return {}
    if action not in SHAPES:
        raise MidiError("only a level (opacity, size, position and the like) has a range, a direction and a takeover")
    wide_lo, wide_hi = ACTIONS[action][1], ACTIONS[action][2]
    for key in ("min", "max"):
        if key in found:
            v = found[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not wide_lo <= v <= wide_hi:
                raise MidiError("%s must be a number from %s to %s" % (key, wide_lo, wide_hi))
            found[key] = round(float(v), 3)
    lo, hi = found.get("min", SHAPES[action][0]), found.get("max", SHAPES[action][1])
    if not lo < hi:
        raise MidiError("min must be less than max")
    if "invert" in found and not isinstance(found["invert"], bool):
        raise MidiError("invert must be true or false")
    if "takeover" in found and found["takeover"] not in TAKEOVERS:
        raise MidiError("takeover must be pickup or jump")
    return found


def takes_over(e, layout):
    """Does this mapping wait for pickup: what it says itself, else the action's own rule on a controller that has a
    layout (`layout`), and never on one that has none, as before."""
    if e.get("takeover") in TAKEOVERS:
        return e["takeover"] == "pickup"
    return bool(layout) and e["action"] in PICKUP


ACTION_KEYS = ("action", "bank", "index", "scene", "guard") + OPTION_KEYS


def clean_action(a, kind="note"):
    """A profile's or an override's action ({"action": "pad", "bank": 0, "index": 3}), checked by the same rules as a
    learned mapping. Returns only the action's own fields."""
    if not isinstance(a, dict):
        raise MidiError("an action must be an object")
    if any(k not in ACTION_KEYS for k in a):
        raise MidiError("an action has only action, bank, index, scene, guard, min, max, invert and takeover")
    e = validate_entry(dict(a, kind=kind, number=0, channel=0, source="*"))
    return {k: e[k] for k in ACTION_KEYS if k in e}


def validate_profile(p, stem=None):
    """A clean profile from a parsed file. Raises MidiError with the reason."""
    _keys(p, "profile", ("id", "name", "match", "description", "sources", "layout", "controls"), ("note", "lights"))
    if not isinstance(p["id"], str) or not PROFILE_ID.fullmatch(p["id"]) or (stem is not None and p["id"] != stem):
        raise MidiError("id must be the file's name: small letters, digits and dashes")
    out = {"id": p["id"], "name": _text(p["name"], "name", 60), "description": _text(p["description"], "description", 1200),
           "note": _text(p["note"], "note", 1200) if "note" in p else ""}
    if not isinstance(p["sources"], list) or not 1 <= len(p["sources"]) <= 8:
        raise MidiError("sources must list one to eight documents")
    out["sources"] = [_text(s, "a source", 400) for s in p["sources"]]
    _keys(p["match"], "match", ("card_ids", "card_names"), ("usb_ids",))
    out["match"] = {"usb_ids": []}
    for key, least, shape, say in (("card_ids", 1, MATCH_TEXT, "an exact name: letters, digits, spaces and . _ -"),
                                   ("card_names", 0, MATCH_TEXT, "an exact name: letters, digits, spaces and . _ -"),
                                   ("usb_ids", 0, USB_ID, "a USB id like 1235:0036")):
        values = p["match"].get(key, [])
        if not isinstance(values, list) or not least <= len(values) <= 8:
            raise MidiError("match.%s must be a list of %d to 8 entries" % (key, least))
        for v in values:
            if not isinstance(v, str) or not shape.fullmatch(v):
                raise MidiError("match.%s: each entry is %s" % (key, say))
        out["match"][key] = list(values)
    _keys(p["layout"], "layout", ("rows", "cols"))
    rows, cols = _whole(p["layout"]["rows"], "layout.rows", 1, MAX_SIDE), _whole(p["layout"]["cols"], "layout.cols", 1, MAX_SIDE)
    out["layout"] = {"rows": rows, "cols": cols}
    if not isinstance(p["controls"], list) or not 1 <= len(p["controls"]) <= MAX_CONTROLS:
        raise MidiError("controls must list 1 to %d controls" % MAX_CONTROLS)
    ids, cells, sends, controls = set(), set(), set(), []
    for c in p["controls"]:
        _keys(c, "a control", ("id", "name", "row", "col", "kind", "send", "action"), ("guard", "unverified", "zone", "layers"))
        if "zone" in c and c["zone"] not in ZONES:
            raise MidiError("a control's zone is one of: %s" % ", ".join(ZONES))
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
            if "scene" in action or "guard" in action or action["action"] == "none":
                raise MidiError("%s: a profile cannot name a scene id or the action none, and its guard is the control's" % what)
            level = ACTIONS[action["action"]][0] in ("level", "control", "delta")
            if c["kind"] in ("fader", "knob") and not level:
                raise MidiError("%s: a fader or knob needs an action that follows it" % what)
            if c["kind"] in ("button", "pad") and ACTIONS[action["action"]][0] in ("level", "delta"):
                raise MidiError("%s: a button or pad needs an action that is pressed" % what)
        for flag in ("guard", "unverified"):
            if flag in c and not isinstance(c[flag], bool):
                raise MidiError("%s: %s must be true or false" % (what, flag))
        if c.get("guard") and (action is None or c["kind"] not in ("button", "pad")):
            raise MidiError("%s: only a button or pad with an action can be guarded" % what)
        others = {}                                 # what the control does instead while a layer is on: {layer: action or None}
        if "layers" in c:
            if not isinstance(c["layers"], dict) or not c["layers"] or any(k not in LAYERS for k in c["layers"]):
                raise MidiError("%s: layers names %s" % (what, " or ".join(LAYERS)))
            for name, raw in c["layers"].items():
                if raw is None:                     # nothing at all in that layer
                    if name == "mapping":
                        raise MidiError("%s: in mapping mode a control has an action or is left as it is" % what)
                    others[name] = None
                    continue
                try:
                    other = clean_action(raw, send["type"])
                except MidiError as e:
                    raise MidiError("%s in the layer %s: %s" % (what, name, e))
                kind_of = ACTIONS[other["action"]][0]
                follows = kind_of in ("level", "control", "delta")
                if "scene" in other or "guard" in other or other["action"] in ("none", "mapping_mode", "layer_geometry") or kind_of == "hold":
                    raise MidiError("%s in the layer %s: that action cannot be a control's other self" % (what, name))
                if (c["kind"] in ("fader", "knob")) != follows and not (kind_of == "control" and c["kind"] in ("button", "pad")):
                    raise MidiError("%s in the layer %s: a fader or knob needs an action that follows it, a button or pad one that is pressed" % (what, name))
                if name == "mapping" and (not other["action"].startswith("map_") or len(other) != 1):
                    raise MidiError("%s: in mapping mode a button chooses, nudges or undoes, and a knob nudges" % what)
                others[name] = other
        controls.append({"id": c["id"], "name": _text(c["name"], what + " name", 24), "row": cell[0], "col": cell[1], "kind": c["kind"],
                         "send": send, "action": action, "guard": bool(c.get("guard")), "unverified": bool(c.get("unverified")),
                         "zone": c.get("zone"), "layers": others})
    out["controls"] = controls
    out["lights"] = validate_lights(p["lights"], controls) if "lights" in p else None      # see "lights" below
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
        except Exception as e:                       # whatever a file holds, it costs only itself
            log("midi: controller profile %s left out: %s" % (n, e))
    return found


def match_profile(profiles, card_id, info=None):
    """The profile for a controller, or None. `info` is card_info()'s answer, a plain product name, or None when
    nothing could be looked up. In order: a USB id the profile lists is the surest key and decides alone; else the
    card id must be one the profile lists (ALSA adds _1, _2 for a second unit) and, if the profile lists product
    names, the name must be one of them. A name that could not be looked up at all (no card list: a test, another
    system) lets the card id decide; a card list that was read but gave no usable name does not."""
    if isinstance(info, str):
        info = {"name": info, "usbid": None, "readable": True}
    info = info or {"name": None, "usbid": None, "readable": False}
    base = re.sub(r"_[0-9]{1,3}$", "", card_id) if isinstance(card_id, str) else ""
    for p in profiles:
        if info.get("usbid") and info["usbid"] in p["match"]["usb_ids"]:
            return p
    for p in profiles:
        if base not in p["match"]["card_ids"]:
            continue
        if p["match"]["card_names"] and (info.get("readable") or info.get("name") is not None) and info.get("name") not in p["match"]["card_names"]:
            continue
        return p
    return None


def profile_entries(profile, source):
    """A profile's default actions as map entries for the controller called `source`."""
    out = []
    for c in profile["controls"]:
        for name, other in (c.get("layers") or {}).items():    # its other self per layer, used only while that layer is on (MidiMapper.matching)
            e = dict(other or {"action": "none"}, id="l:%s:%s" % (name, c["id"]), source=source, kind=c["send"]["type"],
                     channel=c["send"]["channel"], number=c["send"]["number"], profile=True, layer=name, guard=False)
            e["pickup"] = bool(other) and takes_over(e, True)
            out.append(e)
        if c["action"] is None:
            continue
        e = dict(c["action"], id="p:" + c["id"], source=source, kind=c["send"]["type"], channel=c["send"]["channel"],
                 number=c["send"]["number"], profile=True, guard=c["guard"])
        e["pickup"] = takes_over(e, True)
        out.append(e)
    return out


# --- lights: what a profile may say about a controller's lights --------------
# The optional "lights" section of a profile file. It is the ONLY source of bytes that are ever written to a
# controller: for each control with a light, a note-on or a control change on the section's channel with the control's
# own number, and as its value one of the numbers written out in a style; plus the few fixed set-up and clear messages
# the maker's reference gives. Nothing from a request, a clip name or a shader name can reach these bytes. See MIDI.md.
LIGHT_STATES = ("off", "on", "active", "busy")
# A fifth state, "flash": the light goes on and off about twice a second (the one fade button while the picture goes
# down and while it is black). It is not one of the four a style must give: a style MAY give "flash", the value the
# controller itself flashes at (only in a section that says "flash": "device", where the maker's document gives such
# values); every other light is flashed by its writer, which alternates the style's "active" value with the
# section's "off" on its own clock.
LIGHT_FLASH = "flash"
FLASH_HALF = 0.25           # seconds a writer-flashed light is on, then off: two flashes a second
LIGHT_LEVELS = ("low", "medium", "high")
# what a light can be about; which one a control shows follows from what the control does (light_meaning)
LIGHT_MEANINGS = ("clip", "preset", "control", "vibes", "set", "step", "play", "stop", "blackout", "fadeout", "fadein", "room", "bank", "effect",
                  "fade", "clip_b", "clip_c", "mapping", "layer", "spare")
# "clip_b" and "clip_c" are a pad of bank B and of bank C, for a controller that can show the three banks in three
# colours; a section without them shows every pad as "clip".
FALLBACK_STYLE = {"clip_b": "clip", "clip_c": "clip", "fade": "fadeout", "mapping": "vibes", "layer": "vibes"}
MAX_FIXED = 8               # set-up or clear messages in a profile
_MEANING = {"vibes": "vibes", "vibes_ambient": "set", "vibes_show": "set", "vibes_next": "step", "shader_prev": "step",
            "shader_next": "step", "clip_prev": "step", "clip_next": "step", "pause": "play", "stop": "stop",
            "blackout": "blackout", "fadeout": "fadeout", "fadein": "fadein", "bank_prev": "bank", "bank_next": "bank",
            "effect_toggle": "effect", "effect_prev": "step", "effect_next": "step", "fade": "fade", "mapping_mode": "mapping", "layer_geometry": "layer",
            "seek_back": "step", "seek_forward": "step"}


def light_meaning(action):
    """What the light of a control that does `action` (a clean action, or None) is about. None for an action
    that has nothing to show (a level, or an action added after this was written): its light stays dark."""
    a = action["action"] if action else None
    if a is None or a == "none":
        return "spare"
    if a == "pad":
        return ("clip", "clip_b", "clip_c")[action.get("bank", 0)] if action.get("bank", 0) in (0, 1, 2) else "clip"
    if a == "bank_pad":
        return "clip"
    if a.startswith("shader_preset_"):
        return "preset"
    if a.startswith("shader_control_") or a.startswith("effect_control_"):
        return "control"
    if a == "scene" or a.startswith("scene_"):
        return "room"
    return _MEANING.get(a)


def sure_match(profile, info):
    """Is this controller certainly the profile's: its USB id is one the profile lists, or the card list was read
    and gave a product name the profile lists. A match by the card id alone ("Mini", "Mix": the last word of many
    product names) is good enough to read a controller with, and never good enough to WRITE to one: a synthesiser
    whose card id happens to be Mini must not be sent eighty note-ons because /proc/asound could not be read."""
    if isinstance(info, str):
        info = {"name": info, "usbid": None, "readable": True}
    if not isinstance(info, dict) or profile is None:
        return False
    if info.get("usbid") and info["usbid"] in profile["match"]["usb_ids"]:
        return True
    return bool(info.get("name")) and info["name"] in profile["match"]["card_names"]


def _fixed(v, what, channel):
    """Fixed three-byte messages of a profile: note-off, note-on or control change only, on the section's own
    channel, and never a channel mode message (controllers 120 to 127: all sound off, reset all controllers, local
    control, all notes off, omni, mono, poly). No system messages, so no SysEx can be written whatever a file says."""
    if not isinstance(v, list) or len(v) > MAX_FIXED:
        raise MidiError("lights.%s must list at most %d messages" % (what, MAX_FIXED))
    out = []
    for m in v:
        if not isinstance(m, list) or len(m) != 3:
            raise MidiError("lights.%s: a message is three numbers" % what)
        status = _whole(m[0], "lights.%s status" % what, 0x80, 0xBF)
        if status & 0xF0 not in (0x80, 0x90, 0xB0):
            raise MidiError("lights.%s: only note-off, note-on and control change may be written" % what)
        if status & 0x0F != channel - 1:
            raise MidiError("lights.%s: a message must be on the section's channel (%d)" % (what, channel))
        if status & 0xF0 == 0xB0 and isinstance(m[1], int) and 120 <= m[1] <= 127:
            raise MidiError("lights.%s: controllers 120 to 127 are channel mode messages and may not be written" % what)
        out.append(bytes((status, _whole(m[1], "lights.%s data" % what, 0, 127), _whole(m[2], "lights.%s data" % what, 0, 127))))
    return out


def _style(v, what, device_flash=False):
    _keys(v, what, LIGHT_STATES, ("pulse", "flash") if device_flash else ("pulse",))
    out = {k: _whole(v[k], "%s %s" % (what, k), 0, 127) for k in LIGHT_STATES}
    if "flash" in v:                                # the value at which the controller flashes the light by itself
        out["flash"] = _whole(v["flash"], "%s flash" % what, 0, 127)
    pulse = v.get("pulse", {})
    if not isinstance(pulse, dict) or any(k not in LIGHT_STATES[1:] for k in pulse):
        raise MidiError("%s pulse: on, active or busy, each with the value it alternates with" % what)
    out["pulse"] = {k: _whole(pulse[k], "%s pulse %s" % (what, k), 0, 127) for k in pulse}
    return out


def validate_lights(v, controls):
    """A clean lights section, checked as strictly as the rest of the file. `controls` are the profile's clean
    controls: a light belongs to a button or pad of the layout. The section does not say what a light shows: that
    follows from what its control does (light_meaning), so giving a spare control an action, or adding an action,
    never makes a lights section wrong."""
    _keys(v, "lights", ("default", "unverified", "note", "sources", "channel", "brightness", "off", "styles", "controls"), ("setup", "clear", "flash"))
    if v.get("flash", "timer") not in ("timer", "device"):
        raise MidiError("lights.flash must be timer (the box switches the light on and off) or device (the controller flashes it)")
    device_flash = v.get("flash") == "device"
    for flag in ("default", "unverified", "brightness"):
        if not isinstance(v[flag], bool):
            raise MidiError("lights.%s must be true or false" % flag)
    out = {"default": v["default"], "unverified": v["unverified"], "brightness": v["brightness"], "note": _text(v["note"], "lights.note", 600),
           "channel": _whole(v["channel"], "lights.channel", 1, 16), "off": _whole(v["off"], "lights.off", 0, 127),
           "flash": "device" if device_flash else "timer"}
    if not isinstance(v["sources"], list) or not 1 <= len(v["sources"]) <= 8:
        raise MidiError("lights.sources must list one to eight documents")
    out["sources"] = [_text(s, "a lights source", 400) for s in v["sources"]]
    out["setup"], out["clear"] = _fixed(v.get("setup", []), "setup", out["channel"]), _fixed(v.get("clear", []), "clear", out["channel"])
    if not isinstance(v["styles"], dict) or not v["styles"]:
        raise MidiError("lights.styles must be an object")
    styles = {}
    for name, style in v["styles"].items():
        if name not in LIGHT_MEANINGS or name == "spare":
            raise MidiError("lights.styles: %r is not something a light can show" % (name,))
        what = "lights.styles.%s" % name
        if out["brightness"]:
            _keys(style, what, LIGHT_LEVELS)
            styles[name] = {level: _style(style[level], "%s.%s" % (what, level), device_flash) for level in LIGHT_LEVELS}
        else:
            one = _style(style, what, device_flash)
            styles[name] = {level: one for level in LIGHT_LEVELS}
    out["styles"] = styles
    if not isinstance(v["controls"], list) or not 1 <= len(v["controls"]) <= MAX_CONTROLS:
        raise MidiError("lights.controls must list 1 to %d controls" % MAX_CONTROLS)
    by_id = {c["id"]: c for c in controls}
    lit = []
    for cid in v["controls"]:
        ctl = by_id.get(cid) if isinstance(cid, str) else None
        if ctl is None:
            raise MidiError("lights.controls: no control called %r" % (cid,))
        if ctl["kind"] not in ("button", "pad"):
            raise MidiError("lights.controls: %s is a fader or knob; only a button or pad has a light" % cid)
        if cid in lit:
            raise MidiError("lights.controls: %s is listed twice" % cid)
        lit.append(cid)
    out["controls"] = lit
    return out


def light_message(lights, ctl, value):
    """The three bytes that set one light: a note-on or a control change on the section's channel, the control's own
    number, a value that came out of a style. Every part is a checked number from the profile file."""
    status = (0x90 if ctl["send"]["type"] == "note" else 0xB0) | (lights["channel"] - 1)
    return bytes((status, ctl["send"]["number"] & 0x7F, value & 0x7F))


def light_state(action, snap, bank=0, layer=None):
    """What the light of a control that does `action` should say now: "off", "on" (there is something here), "active"
    (it is the one on now) or "busy". `snap` is the hub's picture of the box (MidiHub._snapshot); only plain values
    are read here, so this cannot wait for anything."""
    a = action["action"] if action else None
    if a is None or a == "none":
        return "off"
    if a in ("pad", "bank_pad"):
        b = action["bank"] if a == "pad" else bank
        try:
            name = snap["pads"][b][action["index"]]
        except (IndexError, KeyError, TypeError):
            name = ""
        if not name:
            try:
                held = snap["pad_shaders"][b][action["index"]]      # a pad that holds a shader (D73)
            except (IndexError, KeyError, TypeError):
                held = ""
            if not held:
                return "off"
            # the one on now: its shader is on the screen, started with this pad's preset (a pad without one
            # starts the preset called default, or none), and not by Vibes, whose shader is nobody's pad
            try:
                want = snap["pad_presets"][b][action["index"]]
            except (IndexError, KeyError, TypeError):
                want = ""
            got = snap.get("preset") or ""
            same = got.casefold() == want.casefold() if want else got.casefold() in ("", "default")
            return "active" if snap["shader"] == held and same and not snap["vibes"] else "on"
        return "active" if snap["running"] and snap["playing"] == name else "on"
    if a.startswith("shader_preset_"):
        n = ACTIONS[a][1]
        if not snap["shader"] or n > len(snap["presets"]):
            return "off"
        return "active" if snap["preset"] is not None and snap["presets"][n - 1] == snap["preset"] else "on"
    if a.startswith("shader_control_"):
        return "on" if snap["shader"] else "off"
    # Effects (a filter over what plays): the one button is lit while the player runs (whether something with a
    # picture plays is not asked here; pvj/MIDI.md says so) and is "on now" while one is on; the two that step are lit then too; a
    # control of the effect is lit while an effect is on (not checked per input, as for a shader's).
    if a == "effect_toggle":
        return "active" if snap.get("effect") else ("on" if snap.get("effect_ready") and snap["running"] else "off")
    if a in ("effect_prev", "effect_next"):
        return "on" if snap.get("effect") or (snap.get("effect_ready") and snap["running"]) else "off"
    if a.startswith("effect_control_"):
        return "on" if snap.get("effect") else "off"
    if a == "scene" or a.startswith("scene_"):
        if a == "scene":
            sid = action.get("scene") if action.get("scene") in snap["scenes"] else None
        else:
            sid = snap["scenes"][ACTIONS[a][1] - 1] if ACTIONS[a][1] <= len(snap["scenes"]) else None
        if sid is None:
            return "off"
        return "busy" if snap["applying"] == sid else "on"
    if a == "vibes":
        return "active" if snap["vibes"] else ("on" if snap["vibes_ready"] else "off")
    if a in ("vibes_ambient", "vibes_show"):
        sid = snap["sets"].get(ACTIONS[a][1])
        if sid is None or not snap["vibes_ready"]:
            return "off"
        return "active" if snap["vibes"] and snap["set"] == sid else "on"
    if a == "vibes_next":
        return "on" if snap["vibes"] else "off"
    if a in ("shader_prev", "shader_next"):
        return "on" if snap["vibes"] or snap["shader"] else "off"
    if a in ("clip_prev", "clip_next"):
        return "on" if snap["running"] and snap["playlist"] else "off"
    if a == "pause":
        return "off" if not snap["running"] else ("busy" if snap["paused"] else "active")
    if a == "stop":
        return "on" if snap["running"] else "active"
    if a == "layer_geometry":                           # the controller's own layer: flashing for as long as it is on
        return LIGHT_FLASH if layer == ACTIONS[a][1] else "on"
    if a == "mapping_mode":                             # lit where the owner allows it, flashing while the mode is on
        return LIGHT_FLASH if snap.get("mapping") else ("on" if snap.get("mapping_ready") else "off")
    if a == "blackout":
        return LIGHT_FLASH if snap["blackout"] else "on"      # flashing for as long as Blackout is on (the owner, 2026-10-10)
    if a == "fade":                                     # the one fade button: flashing while the picture goes down and
        return LIGHT_FLASH if snap["fade"] == "out" else ("busy" if snap["fade"] == "in" else "on")     # while it is black
    if a in ("seek_back", "seek_forward"):
        return "on" if snap["running"] else "off"
    if a == "fadeout":
        return "active" if snap["fade"] == "out" else "on"
    if a == "fadein":
        return "busy" if snap["fade"] == "in" else "on"
    if a in ("bank_prev", "bank_next"):                 # a place mark: the left one on A, both on B, the right one on C
        return "active" if bank == 1 or bank == (0 if a == "bank_prev" else 2) else "on"
    return "off"


def light_value(lights, meaning, state, level="low", phase=0):
    """The number a light is sent for a state, from the profile's style for what it shows (the section's own "off"
    when it has no style for that); a pulsing state alternates with its second value on the hub's slow beat."""
    style = lights["styles"].get(meaning) or lights["styles"].get(FALLBACK_STYLE.get(meaning))
    if style is None or (state not in LIGHT_STATES and state != LIGHT_FLASH):
        return lights["off"]
    style = style[level if level in LIGHT_LEVELS else "low"]
    if state == LIGHT_FLASH:                            # the controller's own flashing value, else what its writer
        return style.get("flash", style["active"])      # alternates with "off" (light_flashes says which)
    if phase and state in style["pulse"]:
        return style["pulse"][state]
    return style[state]


def light_flashes(lights, meaning, state, level="low"):
    """Must the writer flash this light itself: it is in the state "flash" and its style has no value at which the
    controller does it."""
    if state != LIGHT_FLASH:
        return False
    style = lights["styles"].get(meaning) or lights["styles"].get(FALLBACK_STYLE.get(meaning))
    return style is not None and "flash" not in style[level if level in LIGHT_LEVELS else "low"]


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
    if not isinstance(action, str) or action not in ACTIONS:
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
    if ACTIONS[action][0] == "delta" and kind != "cc":
        raise MidiError("only a knob or fader can nudge by being turned")
    if ACTIONS[action][0] == "hold" and kind == "program":
        raise MidiError("a program change cannot be held; use a pad or a button")
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
    if "guard" in e:                              # "press twice", for what darkens the screen or changes the room
        if not isinstance(e["guard"], bool) or not guardable(action):
            raise MidiError("guard is true or false, and only for blackout and Room scenes")
        out["guard"] = e["guard"]
    out.update(clean_options(e, action))            # its own range, direction and takeover (a level only)
    return out


def guardable(action):
    return action in ("blackout", "scene", "mapping_mode") or (isinstance(action, str) and action.startswith("scene_"))


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
        self._held = {}                     # trigger key -> when a control with a "hold" action went down
        self.local = lambda source, body: False     # what is not an API call (a code on the display); the hub sets it
        self.mapping_mode = lambda: False           # is mapping mode on (the mapper's memory); the hub sets it. Must never wait
        self._turn = {}                             # (source, kind, number) -> [where a "delta" knob stood last, steps not yet sent]
        self.layers = {}                            # source -> (layer, until): a controller's own layer (geometry), see LAYERS
        self._stood = {}                            # (source, kind, number) -> where a knob stood when it last set a shader's or an effect's control
        self._parked = {}                           # the same key -> where the knob was last seen, while it waits to come back to that place
        self._seen = {}                             # source -> the layer that was on at its last message (matching)

    def matching(self, source, kind, channel, number):
        """Entries for this control, in the order of precedence: the person's own mapping (it replaces the others
        instead of firing next to them), else the controller's standard layout, else the built-in map; and the
        built-in map is never used for a controller whose standard layout is on."""
        # The layer that is on now, and whether it is another than at this controller's last message. Mapping mode
        # comes and goes in the mapper's memory (another controller, OSC, the panel's switch, its three minutes
        # running out), so nobody tells us: the change is met here, before anything is looked up, whatever control
        # this message is for (a Learned one too).
        layer = self.active_layer(source)
        if source not in self._seen:
            self._seen[source] = layer
        elif self._seen[source] != layer:
            self._seen[source] = layer
            self._afresh(source)
        found = [e for e in self.entries if e["kind"] == kind and e["number"] == number
                 and e["source"] in ("*", source) and e["channel"] in (0, channel + 1)]
        mine = [e for e in found if not e.get("builtin") and not e.get("profile")]
        if mine:                                # this controller's mapping before one for any controller; its channel's before any channel's
            mine = [e for e in mine if e["source"] == source] or mine
            return [e for e in mine if e["channel"]] or mine
        standard = [e for e in found if e.get("profile")]
        # a control's other self in mapping mode takes its place while the mode is on, and does not exist outside it
        other = [e for e in standard if layer is not None and e.get("layer") == layer]
        standard = other or [e for e in standard if not e.get("layer")]
        if other and layer in LAYER_SECONDS:            # a touch of one of the layer's controls keeps the layer on
            self.layers[source] = (layer, self._clock() + LAYER_SECONDS[layer])
        if standard or source in self.profiled:
            return standard
        return found

    def active_layer(self, source):
        """The layer that is on for this controller, or None: mapping mode (the box's) before the controller's own."""
        if self.mapping_mode():
            return "mapping"
        own = self.layers.get(source)
        if own is None:
            return None
        if self._clock() >= own[1]:                     # it ended by itself
            self._switch(source, None)
            return None
        return own[0]

    def _switch(self, source, layer):
        """Put a controller's own layer on, or off (None)."""
        if layer is None:
            self.layers.pop(source, None)
        else:
            self.layers[source] = (layer, self._clock() + LAYER_SECONDS.get(layer, 0.0))
        if not self.mapping_mode():                     # under mapping mode the layer that counts did not change
            self._seen[source] = layer
            self._afresh(source)

    def _afresh(self, source):
        """A controller's layer changed, by whatever road (its own button, its time running out, mapping mode coming
        or going). Nothing may jump across the change: a level starts its pickup afresh (its knob was somewhere else
        meanwhile), and a knob that sets a shader's or an effect's control waits until it is back where it stood
        when it last set one."""
        for key in [k for k in self._pick if k[0] == source]:
            del self._pick[key]
        for key in [k for k in self._stood if k[0] == source]:
            self._parked[key] = None
        # and a knob that nudges forgets where it stood and what it had not sent yet: outside the mode it is another
        # control (a shader's knob), turned to anywhere, so its next first touch in the mode moves nothing again
        for key in [k for k in self._turn if k[0] == source]:
            del self._turn[key]

    def toggle_layer(self, source, layer):
        """The layer's own button. While mapping mode is on it does nothing: one layer at a time."""
        if self.mapping_mode():
            return
        self._switch(source, None if self.layers.get(source, (None,))[0] == layer else layer)

    def _back_in_place(self, key, value):
        """A knob that sets a control of the shader or the effect, after a layer: False until it is back where it
        stood (within the pickup's tolerance, or passing it)."""
        if key not in self._parked:
            return True
        stood, prev = self._stood.get(key), self._parked[key]
        if stood is None or abs(value - stood) <= PICKUP_TOLERANCE or (prev is not None and (prev - stood) * (value - stood) <= 0):
            del self._parked[key]
            return True
        self._parked[key] = value
        return False

    def forget(self, source=None):
        """A controller went: its pickup and guard state go with it, so it starts clean when it comes back. With no
        source, every controller's (MIDI was switched off: what is let go meanwhile is never heard, so a button
        that was down then would otherwise count as held for ever, and its next press would do nothing)."""
        for store in (self.layers, self._seen):
            for name in [n for n in store if source is None or n == source]:
                del store[name]
        for store in (self._pick, self._armed, self._pressed, self.pending, self._held, self._turn, self._stood, self._parked):
            for key in [k for k in store if source is None or source in k[:2]]:
                del store[key]

    def waiting(self, source, kind, number):
        """True while a fader or knob has been moved but has not reached the box's value yet."""
        st = self._pick.get((source, kind, number))
        return bool(st and not st["caught"])

    def _picked_up(self, e, key, value, now):
        """Soft takeover. False while the control has not reached the value the box has."""
        have = self.target(e["action"])
        if have is None:
            return True
        at = level_position(e["action"], have, e)      # where this control gives that value: its own curve, backwards
        st = self._pick.get(key)
        if st and st["caught"] and (now - st["at"] < PICKUP_IDLE or abs(st["sent"] - at) <= PICKUP_TOLERANCE + 2):
            st.update(prev=value, sent=value, at=now)
            return True
        prev = st["prev"] if st else None
        # a fader that tops out at 122 must still reach "100 percent": the last few steps at each end count as the end
        ends = (at >= 127 - PICKUP_END and value >= 127 - PICKUP_END) or (at <= PICKUP_END and value <= PICKUP_END)
        caught = ends or abs(value - at) <= PICKUP_TOLERANCE or (prev is not None and (prev - at) * (value - at) <= 0)
        self._pick[key] = {"caught": caught, "prev": value, "sent": value, "at": now}
        return caught

    def _trigger_calls(self, e):
        """The calls of a press. What needs more than the action's name is here (a pad, the controllers' bank, a
        Room scene by its id); everything else is the one table MIDI shares with OSC (pvj/actions.py)."""
        a = e["action"]
        if a == "pad":
            return [("/api/play", {"pad": [e["bank"], e["index"]]})]
        if a == "bank_pad":
            return [("/api/play", {"pad": [self.bank, e["index"]]})]
        if a in ("bank_next", "bank_prev"):
            self.bank = (self.bank + (1 if a == "bank_next" else -1)) % BANKS
            return []
        if a == "scene":
            return [("/api/room/scene", {"scene": e["scene"]})]
        call = actions.press(a)
        return [call] if call else []

    @staticmethod
    def _level_calls(e, value):
        a = e["action"]
        if a == "blackout_hold":
            return [("/api/blackout", {"on": value >= 64})]
        if a == "vibes_dwell":
            return [("/api/vibes", {"dwell": VIBES_DWELLS[min(len(VIBES_DWELLS) - 1, value * len(VIBES_DWELLS) // 128)]})]
        if a.startswith("shader_control_"):
            return [actions.control("shader", ACTIONS[a][1], value)]
        if a.startswith("effect_control_"):
            return [actions.control("effect", ACTIONS[a][1], value)]
        # the action's shape, with this mapping's own range and direction, then the same call OSC makes for the level
        return [actions.level(a, level_value(a, value, e))]

    def forget_held(self, source=None, pressed=False):
        """Forget every hold that is under way (of one controller, or of all). For when a release may have been
        missed: Learn started or ended, the map or a layout changed, messages were lost. The control still counts as
        down until its release is seen, so the press that is under way cannot start a hold again.
        `pressed` (messages were LOST): which controls are down is not known any more either, so that is forgotten
        too, for every control of the controller. Otherwise a press whose release was among the lost messages would
        stay "down", the next press would not count as one, and its release would be timed from nothing or, worse,
        from an older press."""
        for key in [k for k in self._held if source is None or k[1] == source]:
            del self._held[key]
        if pressed:
            # Only what sends notes (pads and buttons): a lost note-off leaves one "down" for good. A controller
            # number is left as it is: a knob or fader mapped to a trigger rests at a value, and forgetting that it
            # is up would fire the trigger again at its next step. (A cc button whose release was lost is still
            # safe: its hold is forgotten above, so its next release asks for nothing.)
            for key in [k for k in self._pressed if (source is None or k[1] == source) and k[2] == "note"]:
                del self._pressed[key]

    def plan(self, source, msg, at=None):
        """What a message should do: a list of (path, body) calls. Nothing is called here, so a caller can release its
        lock before making the (slow) calls into the player. `at`: when the message was READ from the device, on
        this mapper's clock. A hold is timed by that, never by when the message was handled: the thread that
        handles messages also waits for the player, so a release that was read a tenth of a second after its press
        can be handled seconds after it."""
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
            if kind_of == "delta":
                # A knob that nudges: followed by how far it is turned. Where it stands is always noted, so the first
                # step after the mode came on moves one step and not the whole way the knob was turned before.
                if kind != "cc":
                    continue
                st = self._turn.setdefault((source, kind, d1), [d2, 0])
                moved, st[0] = d2 - st[0], d2
                if not self.mapping_mode():
                    st[1] = 0
                    continue
                st[1] = max(-127, min(127, st[1] + moved))
                if st[1] and now - self._last.get(key, 0.0) >= MIN_INTERVAL:
                    self._last[key] = now
                    calls.append(actions.nudge(ACTIONS[e["action"]][1], st[1]))
                    st[1] = 0
                continue
            if kind_of == "hold":
                # Held, then let go: the press only notes the time (and takes a code that is showing off the
                # display, which needs no hold); the release asks, if the hold was neither too short nor too long.
                # A control that never comes up asks for nothing, and one that is down already cannot go down again.
                if kind == "program":
                    continue
                read = now if at is None else at
                was = self._pressed.get(key, False)
                if kind == "cc":
                    # A button sends 127 when it goes down and 0 when it comes up, and nothing between. A fader or
                    # a knob mapped by hand passes through other values: any of those ends the hold with no request.
                    if was and d2 not in (0, 127):
                        self._pressed[key] = False
                        self._held.pop(key, None)
                        continue
                    down = d2 == 127
                else:
                    down = d2 > 0
                self._pressed[key] = down
                if down and not was:
                    self._held[key] = read
                    calls.append((LOCAL_CODE, {"press": read}))
                elif was and not down:
                    since = self._held.pop(key, None)
                    if since is not None and HOLD_MIN <= read - since <= HOLD_MAX:
                        calls.append((LOCAL_CODE, {"kind": ACTIONS[e["action"]][1], "since": since}))
                continue
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
                    if e["action"] == "layer_geometry":     # the controller's own layer: nothing is asked of the box
                        self.toggle_layer(source, ACTIONS[e["action"]][1])
                        continue
                    calls.extend(self._trigger_calls(e))
            else:
                if ACTIONS[e["action"]][0] == "control":        # a knob on a shader's or an effect's control
                    if not self._back_in_place((source, kind, d1), d2):
                        self.pending.pop(key, None)
                        continue
                    self._stood[(source, kind, d1)] = d2
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
            if path == LOCAL_CODE:
                done += int(bool(self.local(source, body)))
                continue
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
        if self._turn and not self.mapping_mode():      # the mode is over: steps it never sent are not kept for the next one
            for st in self._turn.values():
                st[1] = 0
        elif self._turn:                                # what a nudging knob was turned since its last call
            for (source, kind, number), st in list(self._turn.items()):
                if not st[1]:
                    continue
                for e in self.matching(source, kind, 0, number):
                    if self._turn.get((source, kind, number)) is not st:    # the layer had changed: matching forgot it
                        break
                    if ACTIONS[e["action"]][0] == "delta":
                        key = (e["id"] if "id" in e else e["action"], source, kind, number)
                        if now - self._last.get(key, 0.0) >= MIN_INTERVAL:
                            self._last[key] = now
                            calls.append(actions.nudge(ACTIONS[e["action"]][1], st[1]))
                            st[1] = 0
        return calls

    def flush(self):
        return sum(int(bool(self.do(path, body))) for path, body in self.flush_calls())


LOST = "lost"               # handed to a stamped on_message in the place of a message: some were dropped (the queue was full)
QUEUE_MAX = 2048
STOP_WAIT = 3.0             # stopping waits this long in all for the threads of the inputs being stopped


class MidiInput:
    """Reads one device file on its own thread and hands parsed messages to `on_message(source, msg)`.

    With a `clock` (the hub gives one) there are two threads: one only reads, stamps each message with the clock
    at the moment it was read and queues it; the other hands them on as `on_message(source, msg, at)`. The thread
    that hands on can wait seconds for the player; the one that reads never waits, so `at` is when the control
    was really touched (D61: a hold is timed by it)."""

    def __init__(self, path, source, on_message, log=print, open_fn=None, clock=None, queue_max=QUEUE_MAX):
        self.path, self.source, self.on_message, self.log = path, source, on_message, log
        self._open = open_fn or self._open_device
        self._clock = clock
        self._queue = queue.Queue(queue_max) if clock is not None else None
        self._lost = False
        self._said_at = 0.0
        self._deadline = None
        self._worker = None
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
                    at = self._clock() if self._queue is not None else None
                    for msg in parser.feed(chunk):
                        if self._stop.is_set():         # told to stop: what was still in the pipe is not acted on
                            break
                        if self._queue is not None:     # stamped and queued; the other thread hands it on
                            # Messages that did not fit are dropped, and a marker says so IN the queue, at the
                            # place where they are missing: before the next message that does fit. (A flag the
                            # worker looked at by itself would be acted on before the older messages still queued.)
                            try:
                                if self._lost:
                                    self._queue.put_nowait((LOST, at))
                                    self._lost = False
                                self._queue.put_nowait((msg, at))
                            except queue.Full:
                                self._lost = True
                            continue
                        self.messages += 1
                        self.on_message(self.source, msg)
                elif self._lost and self._queue is not None:
                    # Dropped, and nothing came after that could carry the marker: put it as soon as there is
                    # room. Only this thread sets, reads and clears the flag, so a newer loss is never wiped out.
                    try:
                        self._queue.put_nowait((LOST, self._clock()))
                        self._lost = False
                    except queue.Full:
                        pass
                if self._stop.is_set():
                    break
                if self._queue is None:
                    self.on_message(self.source, None)  # a tick: lets the hub flush held fader values
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

    def _work(self):
        """Hand the queued messages on, in order, each with the time it was read; a tick whenever nothing waits."""
        reader = self._thread
        while not self._stop.is_set():
            try:
                msg, at = self._queue.get(timeout=0.1)
            except queue.Empty:
                if not reader.is_alive() and self._queue.empty():
                    break                               # the device is gone and everything read from it was handed on
                self._hand(None, self._clock())         # a tick: lets the hub flush held fader values
                continue
            if msg != LOST:
                self.messages += 1
            self._hand(msg, at)
            if self._queue.empty():
                self._hand(None, self._clock())

    def _hand(self, msg, at):
        """One message to the handler. Whatever it raises (a save that failed somewhere under the API, a bug) ends
        with that message: this thread goes on to the next one, or the controller would be deaf from then on. Said
        in the log with the kind of error, at most once in ten seconds.
        A real message that failed counts as a lost one: it may have been a release, half handled or not at all, so
        the handler is told "lost" right after it (in a try of its own) and forgets what is held and down."""
        try:
            self.on_message(self.source, msg, at)
        except Exception as e:
            now = time.monotonic()
            if now >= self._said_at:
                self._said_at = now + 10.0
                self.log("midi: %s: a message could not be handled (%s: %s); carrying on" % (self.path, type(e).__name__, e))
            if msg is not None and msg != LOST:
                try:
                    self.on_message(self.source, LOST, at)
                except Exception:
                    pass

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="midi")
        self._thread.start()
        if self._queue is not None:
            self._worker = threading.Thread(target=self._work, daemon=True, name="midi-work")
            self._worker.start()

    @property
    def alive(self):
        """Both threads are there. An input with only one of them left delivers nothing, so the hub's scan must see
        it as gone and put a new one in its place."""
        reading = bool(self._thread and self._thread.is_alive())
        return reading and (self._queue is None or bool(self._worker and self._worker.is_alive()))

    def halt(self):
        """Tell the reader to stop, without waiting for it (stop() waits)."""
        self._stop.set()

    def stop(self):
        """Stop and wait for both threads: until the deadline stop_inputs() set for several inputs together, else
        STOP_WAIT from now in all."""
        self._stop.set()
        deadline, self._deadline = self._deadline, None
        deadline = time.monotonic() + STOP_WAIT if deadline is None else deadline
        for name in ("_thread", "_worker"):
            thread = getattr(self, name)
            if thread:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
                setattr(self, name, None)


def stop_inputs(inputs, timeout=None):
    """Stop several inputs: all are told first, then all are waited for against ONE deadline. A handler stuck in a
    call to the player holds its thread for seconds; waiting for each in turn made switching MIDI off take that long
    per thread."""
    inputs = list(inputs)
    for inp in inputs:
        inp.halt()
    deadline = time.monotonic() + (STOP_WAIT if timeout is None else timeout)
    for inp in inputs:
        inp._deadline = deadline
        inp.stop()


# --- lights: the writer ------------------------------------------------------
LIGHT_RATE = 180.0          # messages a second to one controller, with LIGHT_BURST on top: never more than 200 in a second
LIGHT_BURST = 20
LIGHT_TICK = 0.3            # how often the hub looks at the box's state for the lights (and half the pulse)
LIGHT_PLAYER_EVERY = 0.6    # how often, at most, the player is asked what is playing (one status call for all lights)
LIGHT_RETRY = 10.0          # a writer that could not open its device for another reason than permission tries again after this
LIGHT_SWEEP_STEP = 0.03     # Test lights: one light after another
LIGHT_SWEEP_HOLD = 1.0      # and all of them stay on this long
ALSA_MAJOR = 116            # every /dev/snd node is a character device with this major number
# include/uapi/sound/asound.h: #define SNDRV_RAWMIDI_IOCTL_DROP _IOW('W', 0x30, int), the argument a pointer to the
# stream (SNDRV_RAWMIDI_STREAM_OUTPUT is 0). _IOW is (1 << 30) | (size 4 << 16) | ('W' 0x57 << 8) | 0x30 on ARM and
# x86 (the Pi and the test machines). It throws away the bytes a rawmidi output has not sent yet. A fixed number, sent
# only to a handle this module opened; nothing from outside reaches it.
SNDRV_RAWMIDI_IOCTL_DROP = 0x40045730
SNDRV_RAWMIDI_STREAM_OUTPUT = 0


class LightWriter:
    """Writes one controller's lights, on its own thread and its own write-only handle (never the reader's).

    There is no queue to grow: `show()` replaces a table of the value each light should have, the thread sends what
    differs from what the device took, a few at a time under the rate limit. A device that takes nothing (a full
    buffer) is not waited for and nothing piles up: the table simply keeps being replaced, and what differs then is
    sent when the device takes messages again. A fresh device (plugged in again) starts from nothing known, so it
    gets the whole table. `show()` and `test()` only swap a table under a lock held for moments; nothing here is
    ever called while waiting for the device."""

    def __init__(self, path, source, lights, keys, open_fn=None, clock=time.monotonic, log=print):
        self.path, self.source, self.lights, self.log, self._clock = path, source, lights, log, clock
        self.keys = list(keys)          # every light of the layout as its two leading bytes, in the drawn order
        self._open = open_fn or self._open_device
        self._lock = threading.Lock()
        self._want = {}                 # key -> value the light should have
        self._flash = set()             # keys this writer switches on and off itself, FLASH_HALF each (see show)
        self._over = {}                 # key -> value while Test lights runs
        self._sweep = None              # (steps, value per key, started) while Test lights runs
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._clear = True
        self._thread = None
        self.state = "opening"          # opening, on, installer (no permission), busy, failed, gone, stopped
        self.error = ""
        self.sent = 0                   # messages written, for the page and the tests
        self.since = clock()            # when it was made, then when it ended
        self.reader = None              # the MidiInput it belongs to: a controller plugged in again gets a new writer
        self.asked = 0                  # the hub's count of settings changes when it was made
        self.setup = True               # send the profile's set-up messages first (once per plug-in: the hub says)
        self.setup_sent = False         # they went out whole
        self._stuck = False             # the last write was cut short or refused
        self.dropped = False            # the waiting bytes were dropped before the close

    @staticmethod
    def _open_device(path):
        """Open a controller for writing: only /dev/snd/midiC<n>D<n>, no link, a character device of ALSA's."""
        if not isinstance(path, str) or not DEVICE_PATH.fullmatch(path):
            raise OSError("not a MIDI device path")
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            good = stat.S_ISCHR(st.st_mode) and os.major(st.st_rdev) == ALSA_MAJOR
        except OSError:
            good = False
        if not good:
            os.close(fd)                                # nothing was written to it, and the handle does not stay open
            raise OSError("not an ALSA character device")
        return fd

    def _close(self, fd):
        """Close the handle. The kernel waits for a rawmidi output to drain when it is closed, up to about ten
        seconds, holding the device's open lock, so a controller that takes no bytes would hold up the reader's
        close and a re-open for that long. So when bytes may still be waiting (the last write was cut short or
        refused), they are dropped first. Without that, or if the drop fails, the close is bounded by the kernel's
        ten seconds and happens on this writer's own thread; stop() does not wait for it beyond its 2.5 seconds."""
        if self._stuck:
            try:
                fcntl.ioctl(fd, SNDRV_RAWMIDI_IOCTL_DROP, struct.pack("i", SNDRV_RAWMIDI_STREAM_OUTPUT))
                self.dropped = True
            except Exception:                           # a pipe in a test, another kernel: the close is as slow as it is
                pass
        try:
            os.close(fd)
        except OSError:
            pass

    def show(self, table, flash=()):
        """The value every light should have now ({key: value}). Never waits. `flash`: the keys of the lights this
        writer flashes itself: each alternates its value with the section's "off", FLASH_HALF seconds each, on this
        thread's own clock, for as long as the hub keeps naming it. The flashing costs two messages a light a second
        and goes through the same limit as everything else this writer sends; a controller's reader never waits for
        it, and it ends with the first table that does not name the light."""
        flash = set(flash)
        with self._lock:
            if table != self._want or flash != self._flash:
                self._want, self._flash = dict(table), flash
                self._wake.set()

    def test(self, values):
        """Test lights: all off, then one light after another in the drawn order to `values[key]`, a pause, and back
        to what they were showing. False while a sweep is running already."""
        with self._lock:
            if self._sweep is not None or self.state != "on":
                return False
            self._over = {k: self.lights["off"] for k in self.keys}
            self._sweep = ([k for k in self.keys if k in values], dict(values), self._clock())
        self._wake.set()
        return True

    @property
    def testing(self):
        return self._sweep is not None

    def _advance(self, now):
        """With the lock held: where the sweep is. Returns the seconds until its next step, or None when none runs."""
        if self._sweep is None:
            return None
        steps, values, started = self._sweep
        done = int((now - started) / LIGHT_SWEEP_STEP)
        for k in steps[:done]:
            self._over[k] = values[k]
        if done >= len(steps):
            if now - started >= len(steps) * LIGHT_SWEEP_STEP + LIGHT_SWEEP_HOLD:
                self._sweep, self._over = None, {}
                return 0.0
            return LIGHT_SWEEP_STEP * 4
        return LIGHT_SWEEP_STEP

    def _pending(self, have, now=None):
        """With the lock held: the messages that would make the device match the table, in the drawn order."""
        want = self._want
        if self._flash and int((self._clock() if now is None else now) / FLASH_HALF) % 2:      # the dark half of a flash
            want = dict(want)
            want.update({k: self.lights["off"] for k in self._flash if k in want})
        if self._over:
            want = {**want, **self._over}
        return [(k, want[k]) for k in self.keys if k in want and have.get(k) != want[k]]

    def _write(self, fd, data):
        """Write whole messages or nothing more: returns how many bytes went. A full buffer is not waited for."""
        try:
            n = os.write(fd, data)
        except BlockingIOError:
            n = 0
        self._stuck = n < len(data)                     # bytes may be waiting in the device's buffer (see _close)
        return n

    def _run(self):
        try:
            self._work()
        finally:
            self.since = self._clock()

    def _work(self):
        try:
            fd = self._open(self.path)
        except PermissionError as e:       # the service file is older than this program (or the account is not in "audio")
            self.state, self.error = "installer", str(e)
            return
        except OSError as e:
            self.state, self.error = ("busy" if getattr(e, "errno", None) in (11, 16) else "failed"), str(e)
            return
        except Exception as e:
            self.state, self.error = "failed", repr(e)
            return
        self.state = "on"
        have, tail, tokens, last = {}, (b"".join(self.lights["setup"]) if self.setup else b""), float(LIGHT_BURST), self._clock()
        first = bool(tail)                              # the set-up messages are the first thing in `tail`
        self.setup_sent = not self.setup or not first
        try:
            while not self._stop.is_set():
                now = self._clock()
                tokens = min(float(LIGHT_BURST), tokens + (now - last) * LIGHT_RATE)
                last = now
                with self._lock:
                    wait = self._advance(now)
                    todo = self._pending(have, now)
                    if self._flash:                     # wake for the next half of the flash, not a second from now
                        beat = FLASH_HALF - now % FLASH_HALF + 0.005
                        wait = beat if wait is None else min(wait, beat)
                if tail:                                # what a full buffer cut off goes first, whole, before anything new
                    n = self._write(fd, tail)
                    tail = tail[n:]
                    if tail:
                        self._stop.wait(0.05)
                        continue
                    if first:
                        first, self.setup_sent = False, True
                room = int(tokens)
                batch = todo[:room]
                if batch:
                    data = b"".join(k + bytes((v,)) for k, v in batch)
                    n = self._write(fd, data)
                    whole = n // 3
                    for k, v in batch[:whole]:
                        have[k] = v
                    tokens -= whole
                    self.sent += whole
                    if n < len(data):                   # the device is not taking it: nothing is queued for later. The one
                        if n % 3:                       # message that was cut is finished first; the rest stays a difference
                            k, v = batch[whole]         # between the tables and is worked out again from what is wanted then
                            tail, have[k] = data[n:whole * 3 + 3], v
                            tokens -= 1
                            self.sent += 1
                        self._stop.wait(0.05)
                        continue
                if len(todo) > len(batch):
                    self._stop.wait(max(0.005, (1 - (tokens - int(tokens))) / LIGHT_RATE))
                    continue
                self._wake.clear()
                with self._lock:                        # a table that came in between is not slept through
                    again = bool(self._pending(have))
                if not again and not self._stop.is_set():
                    self._wake.wait(wait if wait is not None else 1.0)
            if self._clear:
                self._off(fd, tail)
            self.state = "stopped"
        except OSError as e:                            # unplugged, or the device refuses: this writer ends, the others go on
            self.state, self.error = "gone", str(e)
        except Exception as e:
            self.state, self.error = "failed", repr(e)
            self.log("midi: lights of %s: internal error: %r" % (self.source, e))
        finally:
            self._close(fd)

    def _off(self, fd, tail=b""):
        """Every light off, within a second: the profile's own clear message if it has one, else each light's off."""
        data = tail + (b"".join(self.lights["clear"]) or b"".join(k + bytes((self.lights["off"],)) for k in self.keys))
        end = time.monotonic() + 1.0                    # the real clock: a deadline must pass even where the hub's clock is a test's
        while data and time.monotonic() < end:
            n = self._write(fd, data[:LIGHT_BURST * 3])
            data = data[n:]
            self.sent += n // 3
            time.sleep(LIGHT_BURST / LIGHT_RATE if n else 0.02)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="midi-lights")
        self._thread.start()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def halt(self, clear=True):
        """Tell the writer to switch everything off and end, without waiting for it."""
        self._clear = clear
        self._stop.set()
        self._wake.set()

    def stop(self, clear=True):
        self.halt(clear)
        if self._thread:
            self._thread.join(timeout=2.5)
            self._thread = None


class MidiHub:
    """Owns the settings-driven set of controllers, the map, and learn mode."""

    def __init__(self, api, settings, log=print, open_fn=None, lister=list_devices, namer=source_name,
                 clock=time.monotonic, scan_interval=2.0, profiles=None, describer=card_info, light_open_fn=None):
        self.api, self.settings, self.log = api, settings, log
        self._light_open_fn = light_open_fn       # opens a controller for WRITING (tests: a pipe); None is the real device file
        self.lights = {}          # device path -> LightWriter, only for a recognised controller whose lights are on
        self._ending = []         # writers that were told to switch off and end, until they have
        self._lit = {}            # device path -> {control id: the state its light shows}, for the drawn layout
        self._light_thread = None
        self._light_wake = threading.Event()
        self._player_seen = None  # (time, what the player last said), for the lights
        self._sure = {}           # device path -> (source, is it certainly the profile's controller, when that was looked at)
        self._light_stop = None   # the stop signal of the lights thread that is the current one (each has its own)
        self._light_asked = 0     # goes up with every apply(): a writer that had no permission is tried once more then
        self._open_fn, self._lister, self._namer, self._clock = open_fn, lister, namer, clock
        self.profiles = load_profiles(log=log) if profiles is None else profiles
        self._describer = describer
        self._matched = {}        # device path -> (source, profile or None), kept while the device is there
        self._standard = {}       # (profile id, source) -> its entries, made once
        self.activity = {}        # (source, kind, number) -> (time, value) of the last message, for the drawn layout
        self._retired = set()     # controllers that went: a late message from one is dropped, not given to the built-in map
        self._seen = set()        # controllers plugged in since the panel started (a switch is only kept for one of these)
        self.scan_interval = scan_interval
        self._lock = threading.RLock()
        self.inputs = {}          # path -> MidiInput
        self._scanner = None
        self._stop = threading.Event()
        self.calls = RateLimiter(clock, rate=MAX_CALLS_PER_SECOND, burst=MAX_CALLS_PER_SECOND)   # a faulty pad cannot flood the player
        self.mapper = MidiMapper(self._do, [], api.mix, clock)
        self.mapper.target = self._target
        self.mapper.local = self._local
        self.mapper.mapping_mode = self._mapping_mode
        self.queue_max = QUEUE_MAX          # messages read from one controller and not yet handled (a test makes it small)
        self.learn_until = 0.0
        self.captured = None
        self._quiet = None
        self._quiet_until = 0.0
        self._vibes_off_said = False
        self._bad_said = set()    # stored mappings that failed the check and were said in the log, once each

    def _check_map(self):
        """Say in the log, once for each, which stored mapping fails the check and why. Such a mapping is skipped
        whenever a message comes (a settings file a person edited, or one written by a newer version of this
        program with an action this one does not know), and nothing said so before: the control just did nothing."""
        for e in self.cfg().get("map", []):
            try:
                validate_entry(e, keep_id=True)
            except MidiError as why:
                key = json.dumps(e, sort_keys=True, default=str)[:400]
                if key not in self._bad_said:
                    if len(self._bad_said) > MAX_MAP:
                        self._bad_said.clear()
                    self._bad_said.add(key)
                    what = e if not isinstance(e, dict) else "%s %s %s on %s, action %r" % (
                        e.get("id", "?"), e.get("kind", "?"), e.get("number", "?"), e.get("source", "?"), e.get("action"))
                    self.log("midi: a stored mapping is left out (%s): %s" % (str(what)[:160], why))

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
        if path == "/api/vibes" or path.startswith(("/api/shaders/", "/api/effects")):   # only with the Shaders and Vibes module on; said once
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

    def _mapping_mode(self):
        """Is mapping mode on: the mapper's own memory, no lock and no question to anybody."""
        try:
            return bool(self.api.mapper.remote_on())
        except Exception:
            return False

    def cfg(self):
        return self.settings.data["control"]["midi"]

    # --- lights -------------------------------------------------------------
    # One block: which controllers get a writer, the picture of the box the lights are made from, and the tables.
    # A light is only ever written to a device whose card matched a profile that has a lights section.
    def light_choice(self, source, profile):
        """(on, brightness) for a controller: what the person chose, else the profile's default and "low"."""
        saved = self.cfg().get("lights")
        entry = saved.get(source) if isinstance(saved, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        on = entry["on"] if isinstance(entry.get("on"), bool) else bool(profile["lights"] and profile["lights"]["default"])
        level = entry.get("brightness") if entry.get("brightness") in LIGHT_LEVELS else "low"
        return on, level

    def _doing(self, source, profile, c):
        """{control id: the action it has now, or None}: the person's own mapping, else the standard (if it is on)."""
        mine = {}
        for e in c["map"]:
            try:
                e = validate_entry(e, keep_id=True)
            except MidiError:
                continue
            if e["source"] in (source, "*"):
                key = (e["kind"], e["number"])
                if key not in mine or (mine[key]["source"] == "*" and e["source"] == source):
                    mine[key] = e
        out = {}
        for ctl in profile["controls"]:
            e = mine.get((ctl["send"]["type"], ctl["send"]["number"]))
            out[ctl["id"]] = {k: e[k] for k in ("action", "bank", "index", "scene") if k in e} if e else ctl["action"]
        return out

    def _snapshot(self, now, fresh=False):
        """The box as the lights need it, from memory, plus ONE status call to the player at most every
        LIGHT_PLAYER_EVERY seconds for all lights together. Runs on the lights thread only, never under the hub's lock,
        and never takes the shader engine's lock. A part that cannot be read keeps its quiet default."""
        api = self.api
        snap = {"pads": [], "pad_shaders": [], "pad_presets": [], "shader_on": False, "playing": None, "running": False, "paused": False, "playlist": False, "blackout": False, "fade": None,
                "vibes": False, "vibes_ready": False, "sets": {}, "set": None, "shader": None, "presets": [], "preset": None,
                "scenes": [], "applying": None, "effect": None, "effect_ready": False}
        try:
            snap["pads"] = [[str(p.get("file") or "") for p in b["pads"]] for b in api.settings.data["pads"]["banks"]]
            snap["pad_shaders"] = [[("" if p.get("file") else str(p.get("shader") or "")) for p in b["pads"]]
                                   for b in api.settings.data["pads"]["banks"]]
            snap["pad_presets"] = [[("" if p.get("file") else str(p.get("preset") or "")) for p in b["pads"]]
                                   for b in api.settings.data["pads"]["banks"]]
        except Exception:
            pass
        if fresh or self._player_seen is None or now - self._player_seen[0] >= LIGHT_PLAYER_EVERY:
            seen = {"running": False, "paused": False, "playing": None, "playlist": False}
            try:
                st = api.player.status()
                path = st.get("path")
                seen = {"running": bool(st.get("running")) and isinstance(path, str) and bool(path), "paused": bool(st.get("paused")),
                        "playing": os.path.basename(path) if isinstance(path, str) else None,
                        "playlist": isinstance(st.get("playlist_count"), int) and st["playlist_count"] > 1}
            except Exception:
                pass
            self._player_seen = (now, seen)
        snap.update(self._player_seen[1])
        try:
            snap["blackout"] = bool(api.mix.get("blackout"))
            snap["fade"] = getattr(getattr(api, "fader", None), "label", None)
        except Exception:
            pass
        try:
            if api.registry.enabled("shaders"):
                from . import shaderlive
                snap["vibes_ready"], snap["vibes"] = True, bool(api.vibes.running)
                cfg = api.shaders.config()
                rows = cfg.get("sets") or [{"id": shaderlive.FIRST_SET, "name": "Ambient"}, {"id": shaderlive.SHOW_SET, "name": "Show"}]
                snap["sets"] = {e["name"]: e["id"] for e in rows}
                snap["set"] = api.vibes.set_id or cfg.get("active") or rows[0]["id"]
                on = api.shaders.playing
                # What the engine showed last is remembered after a clip has taken the screen: a shader is "on"
                # only while the screen is still its own. The player's count of what took the screen is compared, a
                # plain number, so the player is not asked and no lock is taken. (Before, the preset lights and
                # the shader pads went on showing a shader under a clip.)
                epoch = getattr(api.player, "source_epoch", None)
                if on and (epoch is None or epoch == on.get("epoch")):
                    snap["shader_on"] = True
                    snap["shader"], snap["preset"] = on["id"], on.get("preset")
                    snap["presets"] = [p["name"] for p in cfg.get("presets", {}).get(on["id"], [])]
                fx = getattr(api, "effects", None)          # from what the engine remembers: the player is not asked
                if fx is not None:
                    seen = fx._seen()
                    # "ready" was "no generator has the screen" until an effect could go on over one (D74)
                    snap["effect"], snap["effect_ready"] = (seen["id"] if seen else None), True
        except Exception:
            pass
        try:
            snap["mapping"] = self._mapping_mode()
            snap["mapping_ready"] = bool(api.registry.enabled("mapper") and api.mapper.remote_allowed())
        except Exception:
            pass
        try:
            room = api.room
            if room.enabled():
                snap["scenes"] = [s["id"] for s in room.config()["scenes"]]
                with room.lock:                     # held for moments only (room.py: never while talking to a projector)
                    job = room._scene_job
                    if job is not None and "scene" in job and room._report(job)["running"]:
                        snap["applying"] = job["scene"]
        except Exception:
            pass
        return snap

    def _light_table(self, source, profile, level, snap, bank, phase, c):
        """({the two leading bytes of a light's message: its value}, {control id: its state}) for one controller."""
        lights, doing = profile["lights"], self._doing(source, profile, c)
        table, states, flash = {}, {}, set()
        layer = (self.mapper.layers.get(source) or (None,))[0]     # the controller's own layer, for its button's light (a plain read)
        for ctl in profile["controls"]:
            if ctl["id"] not in lights["controls"]:
                continue
            action = doing[ctl["id"]]
            state, meaning = light_state(action, snap, bank, layer), light_meaning(action)
            key = light_message(lights, ctl, 0)[:2]
            table[key] = light_value(lights, meaning, state, level, phase)
            if light_flashes(lights, meaning, state, level):
                flash.add(key)
            states[ctl["id"]] = state
        return table, states, flash

    @staticmethod
    def _light_keys(profile):
        lit = [c for c in profile["controls"] if c["id"] in profile["lights"]["controls"]]
        return [light_message(profile["lights"], c, 0)[:2] for c in sorted(lit, key=lambda c: (c["row"], c["col"]))]

    def _sure_of(self, path, source, profile, now):
        """May this controller be written to: only if it is certainly the profile's (sure_match). The layout itself
        may rest on the card id alone when the card list cannot be read (D49); a writer never does. An unsure answer
        is not kept as good: it is looked at again, at most every LIGHT_RETRY seconds, so a card list that could not
        be read in the moment of plugging in costs a few seconds of dark lights and nothing else."""
        seen = self._sure.get(path)
        if seen and seen[0] == source and (seen[1] or now - seen[2] < LIGHT_RETRY):
            return seen[1]
        try:
            info = self._describer(path)
        except Exception:
            info = None
        sure = sure_match(profile, info)
        self._sure[path] = (source, sure, now)
        return sure

    def _lights_tick(self, fresh=False, stop=None):
        """Start a writer for each recognised controller whose lights are on, end the others, and hand every writer
        its table. The hub's lock is held only to look at the lists; the player is asked and the tables are made
        without it, and nothing here waits for a device. `stop` is the calling loop's own stop signal: a loop that
        was replaced while it waited for the player hands out nothing more."""
        stop = stop or self._stop
        if stop.is_set() or not self._lock.acquire(timeout=0.2):
            return
        try:
            if stop.is_set():
                return
            now, want = self._clock(), {}
            for path, inp in self.inputs.items():
                src, profile = self._matched.get(path, (None, None))
                if profile is not None and profile["lights"] and src == inp.source and self.standard_on(src):
                    on, level = self.light_choice(src, profile)
                    if on and self._sure_of(path, src, profile, now):
                        want[path] = (src, profile, level)
            self._ending = [w for w in self._ending if w.alive]
            for path in list(self.lights):
                w = self.lights[path]
                if w.setup_sent and w.reader is not None:
                    w.reader.light_setup = True         # this plug-in has had its set-up messages: a retry sends none
                if path not in want or w.reader is not self.inputs[path] or w.lights is not want[path][1]["lights"]:
                    w.halt()
                    self._ending.append(self.lights.pop(path))
                    self._lit.pop(path, None)
                elif not w.alive and w.state in ("busy", "failed", "gone") and now - w.since >= LIGHT_RETRY:
                    del self.lights[path]               # tried again below
                elif not w.alive and w.state == "installer" and w.asked != self._light_asked:
                    del self.lights[path]               # no permission: not tried again by itself, only when it is plugged in
                                                        # again or a MIDI setting is changed (once per change, so never a loop)
            for path, (src, profile, level) in want.items():
                if path not in self.lights and not any(w.path == path for w in self._ending):
                    w = LightWriter(path, src, profile["lights"], self._light_keys(profile), open_fn=self._light_open_fn, clock=self._clock, log=self.log)
                    w.reader, w.asked = self.inputs[path], self._light_asked
                    w.setup = not getattr(w.reader, "light_setup", False)      # once per plug-in, not once per try
                    self.lights[path] = w
                    w.start()
            live = [(path, self.lights[path]) + want[path] for path in self.lights if self.lights[path].alive]
            c, bank = self.cfg(), self.mapper.bank
        finally:
            self._lock.release()
        if not live:
            return
        snap = self._snapshot(now, fresh)
        if stop.is_set():                               # the player took its time and this loop is no longer the one
            return
        phase = int(now / (2 * LIGHT_TICK)) % 2
        for path, w, src, profile, level in live:
            table, states, flash = self._light_table(src, profile, level, snap, bank, phase, c)
            w.show(table, flash)
            self._lit[path] = states

    def _lights_loop(self, stop):
        """The lights thread. `stop` is this loop's own signal, not the hub's: when MIDI is switched off while this
        loop waits for a slow player and on again before it comes back, the hub has a new loop and this one must end
        when it returns instead of carrying on beside it."""
        while not stop.is_set():
            fresh = self._light_wake.wait(LIGHT_TICK)
            if stop.is_set():
                return
            self._light_wake.clear()
            if fresh:
                stop.wait(0.05)                         # a press was acted on: give the player a moment, then look at once
            try:
                self._lights_tick(fresh, stop)
            except Exception as e:
                self._note("lights: %r" % (e,))

    def _lights_off(self):
        """Every writer switches its lights off and ends (within about a second each). Called without the hub's lock."""
        with self._lock:
            writers = list(self.lights.values()) + list(self._ending)
            self.lights.clear()
            self._ending = []
            self._lit.clear()
            self._player_seen = None
        for w in writers:
            w.halt()
        for w in writers:
            w.stop()

    def test_lights(self, source):
        """Test lights for one controller: a sweep over every light, in the drawn order. Raises MidiError with the
        reason when it cannot run. The values are the profile's own (the brightest of each light's style)."""
        with self._lock:
            found = next(((p, w) for p, w in self.lights.items() if w.source == source), None)
            profile = self._matched.get(found[0], (None, None))[1] if found else None
            if found is None or profile is None or not found[1].alive:
                raise MidiError("the lights of that controller are not on")
            lights, level = profile["lights"], self.light_choice(source, profile)[1]
            first, doing = next(iter(lights["styles"])), self._doing(source, profile, self.cfg())
            values = {}
            for ctl in profile["controls"]:
                if ctl["id"] not in lights["controls"]:
                    continue
                style = lights["styles"].get(light_meaning(doing[ctl["id"]]), lights["styles"][first])[level]
                values[light_message(lights, ctl, 0)[:2]] = next((style[k] for k in ("active", "on", "busy") if style[k] != lights["off"]), style["active"])
            writer = found[1]
        if not writer.test(values):
            raise MidiError("the lights are being tested already")

    def _light_status(self, path, source, profile):
        """What the controller's card says about its lights, or None when its profile has none."""
        lights = profile["lights"]
        if not lights:
            return None
        on, level = self.light_choice(source, profile)
        w = self.lights.get(path)
        if not on:
            state, line = "off", "Lights off."
        elif not self.standard_on(source):
            state, line = "off", "Lights are off while the standard layout is off."
        elif path in self._sure and not self._sure[path][1]:
            state, line = "unsure", "Lights are off: the box could not make sure which controller this is, so it sends it nothing."
        elif w is None or w.state == "opening":
            state, line = "opening", "Lights are starting."
        elif w.state == "installer":
            state, line = "installer", "Lights need the box's installer to run once."
        elif w.state == "on":
            state, line = "on", "Lights on."
        else:
            state, line = "failed", "Lights could not be opened (another program may be using the controller). Trying again."
        return {"on": on, "default": lights["default"], "brightness": level, "levels": lights["brightness"], "state": state, "line": line,
                "note": lights["note"], "unverified": lights["unverified"], "sources": list(lights["sources"]),
                "testing": bool(w and w.testing), "sent": w.sent if w else 0}

    # --- profiles ---------------------------------------------------------
    def profile_for(self, source, path=None):
        """The profile of the controller called `source` (its ALSA card id), or None. With the device path the card's
        product name is checked too, and the answer is kept while the device is there."""
        if path is not None and self._matched.get(path, (None,))[0] == source:
            return self._matched[path][1]
        info = None
        if path is not None:
            try:
                info = self._describer(path)
            except Exception:
                info = None
        found = match_profile(self.profiles, source, info)
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

    def known_sources(self):
        """The controllers that are plugged in or were since the panel started."""
        with self._lock:
            return set(self._seen) | {i.source for i in self.inputs.values()}

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
            if action in ("size", "position", "position_y"):    # the mix in memory, as for the opacity
                return float(self.api.mix[action])
            if action in ("shader_speed", "shader_brightness"):
                playing = getattr(getattr(self.api, "shaders", None), "playing", None)
                return float(playing["controls"][action[7:]]) if playing else None
            if action == "effect_amount":               # the record in memory; whether the player still has it is not asked here
                on = getattr(getattr(self.api, "effects", None), "on", None)
                return float(on["controls"]["amount"]) if on else None
        except (KeyError, TypeError, ValueError):
            return None
        if action in ("volume", "speed"):       # what the API last set, from anywhere (Api.control keeps it in memory)
            have = getattr(self.api, "levels", {}).get(action)
            return float(have) if have is not None else None
        return None

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
            # the same soft takeover as the layout it replaces, unless the mapping says "pickup" or "jump" itself
            e["pickup"] = takes_over(e, profile is not None and e["source"] == source)
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
    def _local(self, source, body):
        """A pairing code on the box's display (D61): the one thing a controller can ask for that is not an API
        call, so nothing that reaches the box through the API (OSC, DMX, the schedule, a Room scene, a device
        through the support tunnel) can ask for it. Only this hub calls it, for a message it read from a MIDI
        device file. A finished hold asks for a code, and only from a controller that is on USB: its card has a
        USB id. A device the box cannot place (no id: a virtual or a network MIDI port, or the card list
        unreadable) is refused.
        A PRESS is handled before both checks, on purpose: it only takes a showing code off the display and ends
        it, from any MIDI device the hub reads and whatever the setting says. Ending a code is the safe
        direction, and with the setting off there is no code to end, so the press does nothing.
        The USB id says the port is a USB MIDI interface. It does not say a person is at it: a DIN to USB
        interface, a wireless MIDI dongle, or a computer that presents itself as a USB MIDI device all have one."""
        codes = getattr(self.api, "controller_codes", None)
        if codes is None or not isinstance(source, str):
            return False
        if "press" in body:
            return codes.press(body["press"])
        with self._lock:
            paths = [p for p, i in self.inputs.items() if i.source == source]
        try:
            on_usb = bool(paths) and all(self._describer(p).get("usbid") for p in paths)
        except Exception:
            on_usb = False
        if not on_usb:
            self._note("%s asked for a code on the display, but it is not a USB controller the box can place; nothing was done" % source)
            return False
        return codes.request(body.get("kind"), body.get("since"), source)

    def _run_calls(self, calls, source=None, stamped=False):
        """Make the calls a message planned. Called WITHOUT the hub lock: a play is many round trips to the player.
        `stamped`: the message came with the time it was read, so however long these calls take, a hold is still
        timed rightly. Without that (a caller that reads and handles on one thread), calls that took longer than
        SLOW_CALLS mean the next message's time cannot be trusted, and what is held for this controller is forgotten."""
        began = self._clock()
        try:
            self._make_calls(calls, source)
        finally:
            if calls and not stamped and self._clock() - began > SLOW_CALLS:
                with self._lock:
                    self.mapper.forget_held(source)

    def _make_calls(self, calls, source):
        for path, body in calls:
            if path == LOCAL_CODE:
                self._local(source, body)
                continue
            if path == "/api/blackout" and body.get("on") is None:
                body = {"on": not self.api.mix.get("blackout", False)}
            if path == "/api/vibes" and "on" in body and body["on"] is None:
                body = {"on": not self.api.vibes.running}
            self._do(path, body)
        if calls and self.lights:
            self._light_wake.set()                  # the lights follow a press at once (on their own thread)

    def on_message(self, source, msg, at=None):
        # `at`: when the message was read from the device (MidiInput stamps it); None from a caller that has no such time.
        # MIDI switched off, or this controller unplugged: a message still on its way is dropped. Without this a late
        # CC 20 from a nanoKONTROL2 (knob 5) would be read by the built-in map as opacity.
        if self._stop.is_set() or source in self._retired:
            return
        if msg == LOST:                             # the reader's queue was full: a release may be among what was dropped
            with self._lock:                        # (the marker comes in the queue, where the messages are missing)
                self.mapper.forget_held(source, pressed=True)
            return
        if msg is None:
            with self._lock:
                calls = [] if self._stop.is_set() else self.mapper.flush_calls()
            self._run_calls(calls, source, stamped=at is not None)      # the tick of an input that stamps: its holds are timed rightly
            return
        with self._lock:
            if self._stop.is_set():                 # switched off while this message waited for the lock
                return
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
                    self.mapper.forget_held()                              # a release may have been swallowed meanwhile
                return                                                     # nothing is executed while learning
            if self.learn_until and now >= self.learn_until:
                self.learn_until = 0.0
                self.mapper.forget_held()
            q = self._quiet                                                # the control just learned is still moving: let it settle
            if q and now < q[3] and q[:3] == (source, "note" if kind in ("on", "off") else kind, d1):
                return
            self.mapper.entries = self.entries(source)
            self.mapper.mix = self.api.mix
            calls = self.mapper.plan(source, msg, at)
        self._run_calls(calls, source, stamped=at is not None)

    # --- learn ----------------------------------------------------------------
    # While Learn listens nothing is executed, so a release can go unseen; and a map that changes under a held
    # control changes what that control is. In each case what is held is forgotten (D61): a hold must be seen whole.
    def start_learn(self):
        with self._lock:
            self.captured = None
            self.learn_until = self._clock() + LEARN_SECONDS
            self.mapper.forget_held()

    def cancel_learn(self):
        """Also called after every change of the map (api.midi_map)."""
        with self._lock:
            self.learn_until, self.captured = 0.0, None
            self.mapper.forget_held()

    # --- devices -------------------------------------------------------------
    def scan(self):
        """Start a reader for each controller that appeared and forget those that went. Cheap; called every 2 s.
        It gives up quietly if the hub is stopping: stop() holds the lock while it waits for this thread, and a
        scan that waited for the lock would start a new reader after everything had been shut down."""
        if self._stop.is_set() or not self._lock.acquire(timeout=0.2):
            return
        gone = []
        try:
            if self._stop.is_set():
                return
            paths = set(self._lister())
            for path in list(self.inputs):
                if path not in paths or not self.inputs[path].alive:
                    inp = self.inputs.pop(path)
                    inp.halt()                          # it delivers nothing more; it is joined below, without the lock
                    gone.append((path, inp))
                    if not any(i.source == inp.source for i in self.inputs.values()):
                        self._retired.add(inp.source)
            # A path whose input was halted just now is not given a new one in this pass: the old reader still has
            # the device open until it is joined below. The next scan starts the new one.
            for path in sorted(paths - set(self.inputs) - {p for p, _ in gone}):
                source = self._namer(path)
                inp = MidiInput(path, source, self.on_message, log=self.log, open_fn=self._open_fn, clock=self._clock, queue_max=self.queue_max)
                if not any(i.source == source for i in self.inputs.values()):
                    self.mapper.forget(source)          # nothing of it was heard until now: no button of it is held
                self.inputs[path] = inp
                self._retired.discard(source)
                self._seen.add(source)
                if len(self._seen) > 256:
                    self._seen = {i.source for i in self.inputs.values()}
                self._matched.pop(path, None)           # looked up afresh: another controller may sit on this path now
                self._sure.pop(path, None)
                found = self.profile_for(source, path)
                if found is not None:
                    self.log("midi: %s is a %s: its standard layout is %s" % (source, found["name"], "on" if self.standard_on(source) else "switched off"))
                inp.start()
        finally:
            self._lock.release()
        stop_inputs([inp for path, inp in gone])        # joining a reader can take seconds: never under the lock
        if gone:
            with self._lock:
                for path, inp in gone:
                    if path not in self.inputs:
                        self._matched.pop(path, None)   # its layout goes only once its reader has ended
                    if not any(i.source == inp.source for i in self.inputs.values()):
                        self.mapper.forget(inp.source)
                        for key in [k for k in self.activity if k[0] == inp.source]:
                            del self.activity[key]

    def apply(self):
        """Make reality match the settings and the module switch."""
        enabled = self.cfg()["enabled"] and self.api.registry.enabled("control-midi")
        if not enabled:
            self._stop_all()
            return
        self._check_map()
        with self._lock:
            self._stop.clear()
            self.mapper.forget_held()                   # a switch or a layout choice changed: no hold runs across that
        self.scan()                                     # not under the lock: it may have to wait for a reader to end
        with self._lock:
            if self._stop.is_set():
                return
            if self._scanner is None:
                def loop():
                    while not self._stop.wait(self.scan_interval):
                        try:
                            self.scan()
                        except Exception as e:
                            self.log("midi: scan error: %r" % (e,))
                self._scanner = threading.Thread(target=loop, daemon=True, name="midi-scan")
                self._scanner.start()
            if self._light_thread is None or not self._light_thread.is_alive():
                self._light_stop = threading.Event()
                self._light_thread = threading.Thread(target=self._lights_loop, args=(self._light_stop,), daemon=True, name="midi-lights-state")
                self._light_thread.start()
        self._light_asked += 1
        self._light_wake.set()                          # a switch or a brightness that changed shows at once

    def _stop_all(self):
        """Stop the scanner and every reader. The threads call back into this hub (messages, scans), so they are
        joined with the lock RELEASED; joining while holding it stalls until the join times out."""
        with self._lock:
            self._stop.set()
            scanner, self._scanner = self._scanner, None
            lighter, self._light_thread = self._light_thread, None
            if self._light_stop is not None:
                self._light_stop.set()                  # its own signal: it ends even if a new loop is started meanwhile
            inputs = list(self.inputs.values())
            self.inputs.clear()
            for inp in inputs:
                inp.halt()
            self.learn_until, self.captured = 0.0, None
            # From here on a release is dropped (on_message), so no button may stay "pressed": one held at this
            # moment would never fire again. The same for a fader's pickup and a guarded button's first press.
            self.mapper.forget()
        self._light_wake.set()
        if scanner:
            scanner.join(timeout=3)
        if lighter:
            lighter.join(timeout=3)
        self._lights_off()                              # every light off, each writer on its own, before the readers end
        stop_inputs(inputs)                             # all told first, then one wait for all of them
        with self._lock:                                # only now, with every reader ended, do the layouts go
            if self._stop.is_set():
                self._matched.clear()
                self._sure.clear()
                self.activity.clear()
                self._retired.clear()

    def stop(self):
        self._stop_all()

    def _controller(self, device, c, now):
        """One connected controller for the panel: its profile, what each control does now and where that comes from
        ("yours", "standard" or nothing), and what it last sent."""
        source = device["name"]
        profile = self.profile_for(source, device["path"]) if device["path"] in self.inputs else None
        out = {"name": source, "path": device["path"], "connected": device["connected"], "messages": device["messages"],
               "profile": None, "standard": self.standard_on(source), "controls": [], "lights": None,
               "layer": self.mapper.active_layer(source)}      # the layer that is on for it now (LAYERS), or None
        if profile is None:
            return out
        out["lights"] = self._light_status(device["path"], source, profile)
        lit, has = self._lit.get(device["path"], {}), (profile["lights"] or {}).get("controls", ())
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
            item = {k: ctl[k] for k in ("id", "name", "row", "col", "kind", "send", "unverified", "zone", "layers")}
            if key in mine:
                e = mine[key]
                item.update(action={k: e[k] for k in ("action", "bank", "index", "scene") + OPTION_KEYS if k in e}, guard=bool(e.get("guard")),
                            origin="yours" if e["source"] == source else "any",      # "any": made for every controller; removed in the list
                            pickup=takes_over(e, out["standard"] and e["source"] == source))
            elif out["standard"] and out["layer"] in ctl["layers"]:       # its other self, while that layer is on
                other = ctl["layers"][out["layer"]]
                item.update(action=dict(other) if other else None, origin="layer" if other else None, guard=False,
                            pickup=bool(other) and takes_over(other, True))
            elif out["standard"] and ctl["action"] is not None:
                item.update(action=dict(ctl["action"]), origin="standard", guard=ctl["guard"], pickup=takes_over(ctl["action"], True))
            else:
                item.update(action=None, origin=None, guard=False, pickup=False)
            item["standard"] = dict(ctl["action"]) if ctl["action"] is not None else None
            seen = self.activity.get((source,) + key)
            item["value"] = seen[1] if seen else None
            item["ago"] = round(now - seen[0], 2) if seen else None
            item["waiting"] = self.mapper.waiting(source, *key)
            item["light"] = ctl["id"] in has                                 # this control has a light the box can set
            item["lit"] = lit.get(ctl["id"]) if out["lights"] and out["lights"]["state"] == "on" else None
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


def validate(body, current, known=None):
    """New switches (enabled, builtin, and one controller's standard layout) from untrusted input, based on `current`.
    The map has its own calls. `known` is the set of controllers seen plugged in: a switch is only kept for one of
    those or for one that has a switch already, so the list cannot be filled with made-up names."""
    new = dict(current)
    for key in ("enabled", "builtin"):
        if key in body:
            if not isinstance(body[key], bool):
                raise MidiError("%s must be true or false" % key)
            new[key] = body[key]
    if "controller" not in body and (isinstance(body.get("lights"), bool) or "brightness" in body):
        raise MidiError("name the controller")           # (a stored "lights" object, as in an import, is not this)
    if "controller" in body and "standard" not in body and ("lights" in body or "brightness" in body):
        return validate_light_choice(body, new, known)   # one controller's lights, on or off, and their brightness
    if "controller" in body or "standard" in body:      # the standard layout of one controller, on or off
        name = body.get("controller")
        if not isinstance(name, str) or not SOURCE.fullmatch(name):
            raise MidiError("bad controller name")
        if not isinstance(body.get("standard"), bool):
            raise MidiError("standard must be true or false")
        if "lights" in body or "brightness" in body:
            raise MidiError("change the standard layout and the lights one at a time")
        try:
            switches = validate_controllers(current.get("controllers", {}))
        except MidiError:                                # junk in the settings file must not lock the switch for good
            switches = {}
        if known is not None and name not in known and name not in switches:
            raise MidiError("that controller is not plugged in")
        if body["standard"]:
            switches.pop(name, None)                     # on is the default: nothing is kept for it
        else:
            switches[name] = {"standard": False}
            if len(switches) > MAX_CONTROLLERS and known is not None:       # full: the ones not seen this run go first
                for old in [n for n in switches if n not in known and n != name][:len(switches) - MAX_CONTROLLERS]:
                    del switches[old]
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


# --- lights: the person's choice per controller --------------------------------
def validate_lights_choice(v):
    """The per-controller light choices as stored: {"<controller name>": {"on"?: bool, "brightness"?: low, medium or
    high}}. Kept beside "controllers" (not in it): switching a standard layout back on removes that entry, and an
    older version of this program reads "controllers" strictly. Raises MidiError."""
    if not isinstance(v, dict) or len(v) > MAX_CONTROLLERS:
        raise MidiError("lights must be an object with at most %d controllers" % MAX_CONTROLLERS)
    out = {}
    for name, entry in v.items():
        if not isinstance(name, str) or not SOURCE.fullmatch(name):
            raise MidiError("bad controller name")
        if not isinstance(entry, dict) or not entry or any(k not in ("on", "brightness") for k in entry):
            raise MidiError("a controller's lights have on (true or false) and brightness (low, medium or high)")
        if "on" in entry and not isinstance(entry["on"], bool):
            raise MidiError("lights must be true or false")
        if "brightness" in entry and (not isinstance(entry["brightness"], str) or entry["brightness"] not in LIGHT_LEVELS):
            raise MidiError("brightness must be low, medium or high")
        out[name] = {k: entry[k] for k in ("on", "brightness") if k in entry}
    return out


def validate_light_choice(body, new, known=None):
    """{"controller": name, "lights"?: bool, "brightness"?: level} from untrusted input, into a copy of the settings.
    These are the only things a request can say about lights: a switch and one of three words."""
    name = body.get("controller")
    if not isinstance(name, str) or not SOURCE.fullmatch(name):
        raise MidiError("bad controller name")
    if any(k not in ("controller", "lights", "brightness") for k in body):
        raise MidiError("send controller with lights, brightness or both, and nothing else")
    if "lights" in body and not isinstance(body["lights"], bool):
        raise MidiError("lights must be true or false")
    if "brightness" in body and (not isinstance(body["brightness"], str) or body["brightness"] not in LIGHT_LEVELS):
        raise MidiError("brightness must be low, medium or high")
    try:
        choices = validate_lights_choice(new.get("lights", {}))
    except MidiError:                                # junk in the settings file must not lock the switch for good
        choices = {}
    if known is not None and name not in known and name not in choices:
        raise MidiError("that controller is not plugged in")
    entry = dict(choices.get(name, {}))
    if "lights" in body:
        entry["on"] = body["lights"]
    if "brightness" in body:
        entry["brightness"] = body["brightness"]
    choices[name] = entry
    if len(choices) > MAX_CONTROLLERS and known is not None:        # full: the ones not seen this run go first
        for old in [n for n in choices if n not in known and n != name][:len(choices) - MAX_CONTROLLERS]:
            del choices[old]
    new["lights"] = validate_lights_choice(choices)
    return new


def override_entry(profile, source, control_id, action):
    """The map entry that makes one control of a recognised controller do `action` instead of the standard, or None
    when `action` IS the standard (same action, same press-twice): then nothing is stored, so pressing Save without
    changing anything cannot quietly take the guard off blackout. For blackout and Room scenes the entry is guarded
    unless the person switched "press twice" off themselves."""
    ctl = next((c for c in profile["controls"] if c["id"] == control_id), None)
    if ctl is None:
        raise MidiError("no such control")
    clean = clean_action(action, ctl["send"]["type"])
    level = ACTIONS[clean["action"]][0]
    if ctl["kind"] in ("fader", "knob") and level not in ("level", "control") and clean["action"] != "none":
        raise MidiError("a fader or knob needs an action that follows it")
    if ctl["kind"] in ("button", "pad") and level == "level":
        raise MidiError("a button or pad needs an action that is pressed")
    if guardable(clean["action"]):
        clean.setdefault("guard", True)
    plain = {k: v for k, v in clean.items() if k != "guard"}
    if ctl["action"] is not None and plain == ctl["action"] and bool(clean.get("guard")) == ctl["guard"]:
        return None
    return dict(clean, source=source, kind=ctl["send"]["type"], channel=ctl["send"]["channel"], number=ctl["send"]["number"])


def set_override(current_map, profile, source, control_id, action):
    """The map after "this control does `action`": every own mapping of this controller for that control goes (on any
    channel, so two can never fire together), and the new one is added unless it is the standard."""
    entry = override_entry(profile, source, control_id, action)
    kept = reset_entries(current_map, profile, source, control_id)
    return kept if entry is None else add_entry(kept, entry)


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
