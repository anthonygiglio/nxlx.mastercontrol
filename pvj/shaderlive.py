# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Performing with shaders: everything on top of "put one shader on the screen" (shaders.py).

* Every input of the shader on screen can be changed while it plays, and so can three controls that every shader
  has: speed, a palette shift and a brightness trim. A change never restarts the shader's TIME (see "the clock").
* Changes are coalesced. mpv's GPU output has no way to hand a new number to a user shader that is running
  (`//!PARAM` and `glsl-shader-opts` belong to the other output, gpu-next; read in mpv's source at 0.37 and 0.40, and
  CI checks that this mpv refuses a `//!PARAM` line). So a change is a new shader text that the GPU compiles. A
  dragged slider or a MIDI knob sends dozens of values a second; they are collected, and one worker applies the
  newest at most five times a second. A request only checks the value and notes it down, so it answers at once and
  the thread that reads a MIDI controller is never held up by the GPU.
* Presets: named sets of values per shader. The one called "default" is what a plain Play and Vibes use.
* Rotation sets for Vibes: named lists of shaders with their own dwell time, variation and order; one is active.
* A guard for a weak GPU: frames dropped while a shader is on are counted, a shader that keeps dropping them is
  reported, and Vibes leaves it out from then on (see Guard).

The clock. A shader's TIME is `offset + speed * (frames since the anchor) / 30`, where the frame number is read from
the carrier picture itself: each carrier frame is painted with its own number, and the player reports the same number
as its playing position. So the panel knows what TIME the shader shows, and can change the speed without a jump: it
moves the anchor to "now" and the offset to the TIME reached. (mpv's own `frame` number, which the first version
used, counts every frame the player has uploaded since it started; nothing outside the shader can read it.)

Settings live under "shaders" in the settings file, beside the first version's keys and without a schema change:
"presets", "sets", "active", "heavy", "guard", "clock", and "v" (2 once this version has saved the section).
"""

import glob
import os
import re
import threading
import time
import unicodedata

from . import locks, shaders as S
from .api import ApiError
from .shaders import ShaderError

MAX_PRESETS = 16                # per shader
MAX_PRESET_SHADERS = 128
MAX_SETS = 16
MAX_SET_ENTRIES = 128
SET_ID = re.compile(r"[0-9a-f]{8}")
DEFAULT_PRESET = "default"
ADOPTED = 8                     # epochs kept that Vibes moved on its way out (see LiveEngine.adopt)
STEP_ENTRIES = 8                # waiting steps are kept as at most this many sums (see Changer.step)


def name_ok(name):
    """A name for a preset or a set: 1 to 40 characters, no space at either end, and none of the characters that are
    not seen but change what is seen (controls, direction overrides, zero-width marks, line separators)."""
    return (isinstance(name, str) and 1 <= len(name) <= 40 and name == name.strip()
            and not any(unicodedata.category(ch) in S.HIDDEN for ch in name))


def name_key(name):
    """What two names are compared by: the same letters however they were typed (composed or not, any letter case)."""
    return unicodedata.normalize("NFC", name).casefold()


FIRST_SET = "00000000"
SHOW_SET = "00000001"
ORDERS = ("shuffle", "listed")
APPLY_GAP = 0.2                 # seconds between two compiles: at most five a second
REANCHOR = 2 * 86400.0          # seconds after which a shader that nobody touched gets a new anchor (see LiveEngine.time_of)
EVENT_HOLD = 0.25               # how long a pressed event stays true
MAX_CONTROLS = 8
V3D_STATS = "/sys/devices/platform/v3dbus/*/gpu_stats"

# Measured on a real board: a Raspberry Pi 4 (mpv 0.40, desktop OpenGL 3.1 on V3D, Mesa 26.2, a 2560 x 1440 screen at
# 75 Hz), every bundled shader with its defaults through this engine on 2026-10-05, for 20 seconds at each drawing
# height (60 where the first look was near the line). Per shader: the class, then (the shader's own pass in
# milliseconds, frames dropped a second) at 540 lines and at 720 lines. The method and what else was seen are in
# project-log/JOURNAL.md ("every bundled shader on the Pi 4").
#   light   holds 30 frames a second at 720 lines
#   medium  holds at 540 lines only
#   heavy   drops frames at 540 lines
# "Holds" is fewer than HOLDS dropped frames a second, the line under which the guard says "ok" (Guard.TIGHT).
HOLDS = 0.5
PI4 = {
    "nxlx-aurora.fs": ("medium", (12.9, 0.0), (22.8, 4.87)),
    "nxlx-drift.fs": ("heavy", (20.1, 2.01), (35.7, 10.55)),
    "nxlx-ember.fs": ("light", (6.5, 0.0), (11.4, 0.39)),
    "nxlx-horizon.fs": ("light", (5.5, 0.0), (9.4, 0.0)),
    "nxlx-lattice.fs": ("light", (4.9, 0.0), (8.3, 0.0)),
    "nxlx-nebula.fs": ("heavy", (19.6, 1.48), (34.8, 11.55)),
    "nxlx-prism.fs": ("light", (5.4, 0.0), (9.4, 0.0)),
    "nxlx-pulse.fs": ("light", (6.2, 0.0), (10.9, 0.27)),
    "nxlx-silk.fs": ("light", (4.5, 0.0), (7.5, 0.0)),
    "nxlx-tide.fs": ("medium", (8.7, 0.0), (15.4, 1.17)),
    "nxlx-bloom.fs": ("medium", (8.2, 0.0), (14.8, 1.17)),
    "nxlx-caustic.fs": ("light", (5.8, 0.0), (10.4, 0.0)),
    "nxlx-contour.fs": ("light", (4.6, 0.0), (8.2, 0.0)),
    "nxlx-dusk.fs": ("light", (5.3, 0.0), (9.9, 0.0)),
    "nxlx-fringe.fs": ("light", (6.0, 0.0), (11.4, 0.2)),
    "nxlx-kaleido.fs": ("light", (5.9, 0.0), (10.5, 0.0)),
    "nxlx-lantern.fs": ("medium", (11.5, 0.0), (20.5, 3.84)),
    "nxlx-moire.fs": ("light", (4.7, 0.0), (7.5, 0.0)),
    "nxlx-petal.fs": ("medium", (7.9, 0.0), (13.8, 0.81)),
    "nxlx-pool.fs": ("medium", (7.2, 0.0), (13.5, 1.1)),
    "nxlx-ribbon.fs": ("light", (4.8, 0.0), (8.2, 0.0)),
    "nxlx-ridge.fs": ("medium", (7.1, 0.0), (13.2, 0.79)),
    "nxlx-stars.fs": ("medium", (7.2, 0.0), (13.3, 0.77)),
    "nxlx-tiles.fs": ("light", (4.5, 0.0), (7.8, 0.0)),
    "nxlx-veil.fs": ("light", (4.5, 0.0), (8.3, 0.0)),
    "nxlx-bars.fs": ("light", (4.0, 0.0), (7.2, 0.0)),
    "nxlx-beam.fs": ("medium", (6.9, 0.0), (12.5, 1.0)),
    "nxlx-burst.fs": ("light", (6.3, 0.0), (11.1, 0.15)),
    "nxlx-checker.fs": ("light", (4.1, 0.0), (7.4, 0.0)),
    "nxlx-chevron.fs": ("light", (4.6, 0.0), (7.8, 0.0)),
    "nxlx-glitch.fs": ("light", (5.7, 0.0), (10.0, 0.0)),
    "nxlx-grid.fs": ("light", (4.9, 0.0), (8.4, 0.0)),
    "nxlx-halftone.fs": ("light", (4.3, 0.0), (7.5, 0.0)),
    "nxlx-mirror.fs": ("light", (4.3, 0.0), (7.8, 0.0)),
    "nxlx-radar.fs": ("light", (6.8, 0.0), (12.6, 0.46)),
    "nxlx-scope.fs": ("medium", (6.6, 0.0), (11.8, 0.56)),
    "nxlx-spokes.fs": ("light", (4.9, 0.0), (8.7, 0.0)),
    "nxlx-stripes.fs": ("light", (4.2, 0.0), (7.4, 0.0)),
    "nxlx-tunnel.fs": ("light", (5.1, 0.0), (9.1, 0.0)),
    "nxlx-zoom.fs": ("light", (4.6, 0.0), (8.0, 0.0)),
    "isf-color-bars.fs": ("light", (2.0, 0.0), (3.5, 0.0)),
    "isf-corner-colors.fs": ("light", (4.6, 0.0), (8.3, 0.0)),
    "isf-linear-gradient.fs": ("light", (1.7, 0.0), (2.7, 0.0)),
    "isf-radial-gradient.fs": ("light", (2.2, 0.0), (3.7, 0.0)),
    "isf-ridgelines.fs": ("heavy", (16.8, 1.11), (29.8, 7.76)),
    "isf-simplex-noise.fs": ("medium", (15.7, 0.45), (27.9, 7.18)),
    "isf-sine-warp-gradient.fs": ("light", (3.6, 0.0), (6.5, 0.0)),
}
RETUNED = ()                    # shaders whose look was changed after they were measured: their numbers are marked stale


def weigh(low, high):
    """The class that two dropped-frame rates (at 540 and at 720 lines) make."""
    return "light" if high < HOLDS else ("medium" if low < HOLDS else "heavy")


def performance(categories):
    return any(isinstance(c, str) and c.lower() == S.PERFORMANCE for c in (categories or ()))


def weight_of(sid, cost):
    """light, medium or heavy: measured for the bundled shaders, otherwise read from the first word of the file's own
    COST note, otherwise "" (unknown)."""
    if sid in PI4:
        return PI4[sid][0]
    word = (cost or "").strip().lower()
    for prefix, weight in (("medium to high", "heavy"), ("low", "light"), ("light", "light"), ("medium", "medium"), ("high", "heavy"), ("heavy", "heavy")):
        if word.startswith(prefix):
            return weight
    return ""


def measured(sid):
    """What was measured for a bundled shader on a Pi 4, or None: the pass time at 720 lines as "pass_ms" (the number
    the library shows), both heights in "pass_ms_by_lines", and the frames dropped a second by drawing height."""
    if sid not in PI4:
        return None
    _, low, high = PI4[sid]
    return {"board": "pi4", "lines": 720, "pass_ms": high[0], "pass_ms_by_lines": {"540": low[0], "720": high[0]},
            "drops_per_second": {"540": low[1], "720": high[1]}, "stale": sid in RETUNED}


_BRANCH = re.compile(r"\b(?:for|while|if)\s*\(")


def work_inputs(parsed):
    """The inputs whose value can change how much the GPU has to do: those named in the head of a loop or in a
    condition. Vibes does not vary them (a round must not be heavier than the shader was when it was tried)."""
    if "_work" in parsed:
        return parsed["_work"]
    code, found = parsed["body"], set()
    names = [i["name"] for i in parsed["inputs"]]
    for m in _BRANCH.finditer(code):
        depth, j = 1, m.end()
        while j < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[j], 0)
            j += 1
        head = code[m.end():j]
        found.update(n for n in names if re.search(r"\b%s\b" % re.escape(n), head))
    parsed["_work"] = found                 # kept with the parsed file, which is remembered until the file changes
    return found


# ---- settings: presets and sets ------------------------------------------------------------------------------------------
def _value_ok(v):
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return v == v and abs(v) <= 1e6
    return isinstance(v, list) and 2 <= len(v) <= 4 and all(not isinstance(c, bool) and isinstance(c, (int, float)) and c == c and abs(c) <= 1e6 for c in v)


def clean_fx_controls(controls, base=None):
    """The controls every effect has (effects.py), from untrusted input: {"amount": 0 to 1, the mix between the
    picture as it is and the filtered one (while the effect works at a lower size than the clip, the mix is made at
    that size: at exactly 0 the picture is the clip itself, above 0 all of it is the smaller, softer picture; an
    amount too small to be written into a text counts as 0); "speed": 0 to 4, for a filter that moves by itself; "half": superseded by
    the box's Effect detail and kept so that old presets and callers go on working: true makes this one effect work
    at 540 lines at most, false (what every preset holds) follows the box's setting}. Keys left out keep the value
    in `base` (or the neutral one); an
    unknown key or a value of the wrong kind is refused, a number outside its range is kept inside it."""
    out = dict({"amount": 1.0, "speed": 1.0, "half": False} if base is None else base)
    if controls is None:
        return out
    if not isinstance(controls, dict):
        raise ShaderError("controls must be an object of amount, speed and half")
    for name, v in controls.items():
        if name == "amount":
            out[name] = min(1.0, max(0.0, S._num(v, "amount")))
            if S._f(out[name]) == S._f(0.0):        # too small to be written into a text (1e-40): it is 0, and is treated as 0
                out[name] = 0.0
        elif name == "speed":
            out[name] = min(S.SPEED_MAX, max(S.SPEED_MIN, S._num(v, "speed")))
        elif name == "half":
            if not isinstance(v, bool):
                raise ShaderError("half must be true or false")
            out[name] = v
        else:
            raise ShaderError("no control called %s (there are amount, speed and half)" % S._text(str(name), 32))
    return out


def check_preset(p, controls=S.clean_controls):
    """One stored preset, checked by its form (the values are checked against the shader when they are used): raises
    ValueError. `controls` checks the common controls: a generator's, or an effect's."""
    if not isinstance(p, dict) or not name_ok(p.get("name")):
        raise ValueError("a preset needs a name of 1 to 40 characters")
    values = p.get("values", {})
    if (not isinstance(values, dict) or len(values) > S.MAX_INPUTS
            or not all(isinstance(n, str) and S.INPUT_NAME.fullmatch(n) and _value_ok(v) for n, v in values.items())):
        raise ValueError("the values of preset %s are not input names and values" % p["name"])
    try:
        controls = controls(p.get("controls"))
    except ShaderError as e:
        raise ValueError(str(e))
    return {"name": p["name"], "values": dict(values), "controls": controls}


def check_presets(v, controls=S.clean_controls):
    """{shader file: [preset]} from a settings file; raises ValueError."""
    if not isinstance(v, dict) or len(v) > MAX_PRESET_SHADERS:
        raise ValueError("presets must be an object of shader file and its presets, at most %d shaders" % MAX_PRESET_SHADERS)
    out = {}
    for sid, rows in v.items():
        if not isinstance(sid, str) or not S.FILE.fullmatch(sid) or not isinstance(rows, list) or len(rows) > MAX_PRESETS:
            raise ValueError("presets are kept per shader file, at most %d each" % MAX_PRESETS)
        clean = [check_preset(p, controls) for p in rows]
        if len({name_key(p["name"]) for p in clean}) != len(clean):
            raise ValueError("two presets of %s have the same name" % sid)
        if clean:
            out[sid] = clean
    return out


def check_set(e):
    """One rotation set; raises ValueError."""
    if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not SET_ID.fullmatch(e["id"]):
        raise ValueError("a set needs an id of 8 hex digits")
    name = e.get("name")
    if not name_ok(name) or SET_ID.fullmatch(name):      # a name that reads like an id would be taken for one
        raise ValueError("a set needs a name of 1 to 40 characters that is not 8 hex digits")
    rows = e.get("shaders", [])
    if not isinstance(rows, list) or len(rows) > MAX_SET_ENTRIES:
        raise ValueError("a set holds at most %d shaders" % MAX_SET_ENTRIES)
    entries, seen = [], set()
    for r in rows:
        r = {"id": r} if isinstance(r, str) else r
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not S.FILE.fullmatch(r["id"]) or r["id"] in seen:
            raise ValueError("the shaders of a set are file names, each once")
        seen.add(r["id"])
        row = {"id": r["id"]}
        if r.get("preset") is not None:
            if not name_ok(r["preset"]):
                raise ValueError("the preset of %s is not a preset name" % r["id"])
            row["preset"] = r["preset"]
        entries.append(row)
    d = e.get("dwell", S.DWELL_DEFAULT)
    if isinstance(d, bool) or not isinstance(d, (int, float)) or d != d or not S.DWELL_MIN <= d <= S.DWELL_MAX:
        raise ValueError("each shader stays %d to %d seconds" % (S.DWELL_MIN, S.DWELL_MAX))
    vary, order = e.get("vary", True), e.get("order", ORDERS[0])
    if not isinstance(vary, bool) or order not in ORDERS:
        raise ValueError("vary must be true or false, and order shuffle or listed")
    return {"id": e["id"], "name": name, "shaders": entries, "dwell": int(d), "vary": vary, "order": order}


def check_sets(v):
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_SETS:
        raise ValueError("sets must be a list of 1 to %d rotation sets" % MAX_SETS)
    out = [check_set(e) for e in v]
    if len({e["id"] for e in out}) != len(out) or len({name_key(e["name"]) for e in out}) != len(out):
        raise ValueError("two sets have the same id or name")
    return out


def check_heavy(v):
    if not isinstance(v, dict) or len(v) > S.MAX_UPLOADS + 64:
        raise ValueError("heavy must be an object of shader file and what was seen")
    out = {}
    for sid, note in v.items():
        if not isinstance(sid, str) or not S.FILE.fullmatch(sid) or not isinstance(note, dict):
            raise ValueError("heavy must be an object of shader file and what was seen")
        row = {"at": S._text(note.get("at"), 20)}
        for key in ("drops", "height"):
            n = note.get(key)
            n = float(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and n == n and 0 <= n <= 1e5 else 0.0
            row[key] = int(round(n)) if key == "height" else round(n, 1)      # lines are whole numbers (720, never 720.0)
        if note.get("board") is not None:               # the board it was seen on; a mark without one is this box's own
            if not isinstance(note["board"], str) or not re.fullmatch(r"[a-z0-9-]{1,20}", note["board"]):
                raise ValueError("the board of a heavy mark is not a board name")
            row["board"] = note["board"]
        out[sid] = row
    return out


# ---- reading settings a person may have damaged: bad rows are dropped one by one ------------------------------------------
def read_presets(v, controls=S.clean_controls):
    """{shader: [preset]} with every row that does not pass left out; None if it is not that kind of thing at all."""
    if not isinstance(v, dict):
        return None
    out = {}
    for sid, rows in list(v.items())[:MAX_PRESET_SHADERS]:
        if not isinstance(sid, str) or not S.FILE.fullmatch(sid) or not isinstance(rows, list):
            continue
        clean, names = [], set()
        for p in rows[:MAX_PRESETS]:
            try:
                p = check_preset(p, controls)
            except (ValueError, TypeError):
                continue
            if name_key(p["name"]) not in names:
                names.add(name_key(p["name"]))
                clean.append(p)
        if clean:
            out[sid] = clean
    return out


def read_sets(v):
    if not isinstance(v, list):
        return None
    out, ids, names = [], set(), set()
    for e in v:
        try:
            e = check_set(e)
        except (ValueError, TypeError):
            continue
        if e["id"] not in ids and name_key(e["name"]) not in names and len(out) < MAX_SETS:
            ids.add(e["id"])
            names.add(name_key(e["name"]))
            out.append(e)
    return out


def read_heavy(v):
    if not isinstance(v, dict):
        return None
    out = {}
    for sid, note in list(v.items())[:S.MAX_UPLOADS + 64]:
        try:
            out.update(check_heavy({sid: note}))
        except (ValueError, TypeError):
            continue
    return out


def check_fx_presets(v):
    """The presets of the effects (filters over the playing picture, effects.py): the same form, an effect's controls."""
    return check_presets(v, clean_fx_controls)


def read_fx_presets(v):
    return read_presets(v, clean_fx_controls)


FX_PRESETS = "fx_presets"       # the effects' presets, kept beside the generators' in the same settings section
FX_DETAIL = "fx_detail"         # the effects' working size for the box (effects.py, DETAILS); absent: the board's own default
FX_DETAILS = ("auto", 540, 720, "full")


def detail_ok(v):
    return isinstance(v, (str, int)) and not isinstance(v, bool) and v in FX_DETAILS
READ = (("presets", read_presets), ("sets", read_sets), ("heavy", read_heavy), (FX_PRESETS, read_fx_presets))


# The keys this version adds to the "shaders" settings, each with its check (also used by settings import, boxcare.py).
EXTRA = (("presets", check_presets), ("sets", check_sets), ("heavy", check_heavy), (FX_PRESETS, check_fx_presets))


def check_extra(v):
    """The keys of this file in a "shaders" settings section, checked; raises ValueError. Keys that are absent stay
    absent."""
    out = {}
    for key, check in EXTRA:
        if key in v:
            out[key] = check(v[key])
    if "active" in v:
        if not isinstance(v["active"], str) or not SET_ID.fullmatch(v["active"]):
            raise ValueError("active must be the id of a set")
        out["active"] = v["active"]
    for key in ("guard", "faster"):
        if key in v:
            if not isinstance(v[key], bool):
                raise ValueError("%s must be true or false" % key)
            out[key] = v[key]
    if FX_DETAIL in v:
        if not detail_ok(v[FX_DETAIL]):
            raise ValueError("%s must be one of %s" % (FX_DETAIL, ", ".join(str(d) for d in FX_DETAILS)))
        out[FX_DETAIL] = v[FX_DETAIL]
    if "clock" in v:
        if v["clock"] not in S.CLOCKS:
            raise ValueError("clock must be carrier or frame")
        out["clock"] = v["clock"]
    if "v" in v:
        if v["v"] != 2:
            raise ValueError("unknown version of the shaders settings")
        out["v"] = 2
    return out


# ---- the guard -----------------------------------------------------------------------------------------------------------
def v3d_stats(pattern=V3D_STATS):
    """{queue: (timestamp ns, jobs, runtime ns)} from the kernel's figures for a Raspberry Pi's GPU, or None where
    there are none. The file is a small table (queue, timestamp, jobs, runtime); anything else in it is skipped."""
    for path in sorted(glob.glob(pattern))[:1]:
        try:
            with open(path) as f:
                text = f.read(4096)
        except OSError:
            return None
        out = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 4 and all(x.isdigit() for x in parts[1:]):
                out[parts[0]] = tuple(int(x) for x in parts[1:])
        return out or None
    return None


class Guard:
    """Watches the frames the player drops while a shader is on. LIMIT a second or more on average over the last
    WINDOW seconds is "heavy": too much for this box at this drawing size. It is the average over the whole window
    that counts, not every look: a shader that drops its frames in bursts (nxlx-lantern at 720 lines on the Pi 4, up
    to 8 a second, 3.8 on average) had a quiet look in every six seconds and was never marked, and how often the
    guard was asked decided the answer. One look counts for at most PEAK a second towards "heavy", so a single
    hitch, however many frames it costs, is not a heavy shader. The first SETTLE seconds after a shader comes on or is
    changed are not counted (compiling it costs a few frames). The player's counts start again whenever a clip loops:
    the rises are added up across that, each count by itself (see _rise), so the frames of every loop of a short
    clip count. That holds for the generators too, whose guard this also is and whose heavy marks Vibes keeps: their
    carrier does not loop, so for them it changes nothing today, and a fall of a count that is not a start from 0 is
    never taken for dropped frames. Reading it changes nothing; Vibes acts on it."""
    LIMIT, TIGHT, WINDOW, SETTLE, EVERY, PEAK = 2.0, 0.5, 6.0, 3.0, 0.9, 8.0

    def __init__(self, engine, clock=time.monotonic, stats=v3d_stats):
        self.engine, self._clock, self._stats = engine, clock, stats
        self._lock = locks.make("shaderlive.queue")
        self._desc = None           # the shader text the numbers below belong to
        self._since = 0.0
        self._last = None           # (time, drop count)
        self._seen = []             # the looks of the last WINDOW seconds: (time, frames dropped so far, the same with each look capped)
        self._gpu_last = None
        self.verdict = {"state": None, "drops_per_second": None, "gpu": None}

    def _drops(self):
        """The player's two counts of dropped frames, each by itself (None for one it does not say), or None."""
        try:
            ipc = self.engine.api.player.ipc
            counts = [ipc.request("get_property", name) for name in ("frame-drop-count", "decoder-frame-drop-count")]
        except Exception:
            return None
        counts = tuple(c if (isinstance(c, int) and not isinstance(c, bool)) else None for c in counts)
        return counts if any(c is not None for c in counts) else None

    def _rise(self, before, after, span):
        """How many frames were dropped between two looks `span` seconds apart, or None when that cannot be said.
        Each count is followed by itself: the two are read one after the other, and one may start again alone. A
        count that rose rose by that much. A count that fell has started again (the player does that when a clip
        loops and at a new clip), and what it says now is what was dropped since; but only a small number can be
        that. A count that fell to more than PEAK a second could not have got there from 0 in the time: it was not
        a start from 0 (a seek back, a player that counts otherwise), and nothing is known."""
        total = 0
        for was, now in zip(before, after):
            if was is None or now is None:
                continue
            if now >= was:
                total += now - was
            elif now <= self.PEAK * span:
                total += now
            else:
                return None
        return total

    def _gpu(self, now):
        """How busy the Pi's GPU was since the last look: percent of the time, and render jobs a second."""
        try:
            stats = self._stats()
        except Exception:
            stats = None
        row = (stats or {}).get("render")
        last, self._gpu_last = self._gpu_last, (row if row else None)
        if not row or not last or row[0] <= last[0]:
            return None
        span = row[0] - last[0]
        return {"busy_percent": round(min(100.0, max(0.0, 100.0 * (row[2] - last[2]) / span)), 1),
                "render_jobs_per_second": round(max(0.0, (row[1] - last[1]) * 1e9 / span), 1)}

    def sample(self, playing):
        """Look once (it keeps its own pace: a second look within EVERY seconds returns the last answer). `playing`
        is the engine's record of what is on, or None."""
        now = self._clock()
        with self._lock:
            if playing is None:
                self._desc, self._last, self._seen, self._gpu_last = None, None, [], None
                self.verdict = {"state": None, "drops_per_second": None, "gpu": None}
                return self.verdict
            if playing["desc"] != self._desc:                       # a new text: compiling it costs frames; start over
                self._desc, self._since, self._last, self._seen = playing["desc"], now, None, []
                self.verdict = dict(self.verdict, state=None, drops_per_second=None)
            if self._last is not None and now - self._last[0] < self.EVERY:
                return self.verdict
            drops = self._drops()
            gpu = self._gpu(now)
            if drops is None or now - self._since < self.SETTLE:
                self._last, self._seen = (None if drops is None else (now, drops)), []
                self.verdict = {"state": None, "drops_per_second": None, "gpu": gpu}
                return self.verdict
            last, self._last = self._last, (now, drops)
            if last is None or now <= last[0]:
                self._seen = []
                return self.verdict
            # The player's counts start again at 0 each time a clip loops, and at every new clip. What one says
            # after that is what was dropped since, so the rises are added up across it and the window goes on: a
            # clip shorter than the window would otherwise never be seen as heavy (found while measuring on the
            # Pi 4, where the same mistake made a window say minus 1.9 frames a second). See _rise for what a fall
            # is not taken for.
            span = now - last[0]
            step = self._rise(last[1], drops, span)
            if step is None:
                self._seen = []                                         # nothing is known of this stretch: the window starts again
                self.verdict = dict(self.verdict, state=None, drops_per_second=None)
                return self.verdict
            if not self._seen:
                self._seen = [(last[0], 0.0, 0.0)]
            self._seen.append((now, self._seen[-1][1] + step, self._seen[-1][2] + min(step, self.PEAK * span)))
            while len(self._seen) > 2 and self._seen[1][0] <= now - self.WINDOW:     # keep one look at or before the window's start
                self._seen.pop(0)
            first, newest = self._seen[0], self._seen[-1]
            whole = now - first[0]
            rate = (newest[1] - first[1]) / whole                       # what is shown: the average over the window so far
            heavy = whole >= self.WINDOW and (newest[2] - first[2]) / whole >= self.LIMIT
            self.verdict = {"state": "heavy" if heavy else ("tight" if rate >= self.TIGHT else "ok"), "drops_per_second": round(rate, 1), "gpu": gpu}
            return self.verdict


# ---- changes, coalesced ----------------------------------------------------------------------------------------------------
class Changer:
    """One worker that applies changes to the shader on screen. `submit` only notes the newest wish and returns;
    the worker compiles at most once every APPLY_GAP seconds, so however fast values arrive the GPU compiles a few
    times a second and the last value always lands, at the latest APPLY_GAP after it came (plus the compile)."""

    def __init__(self, engine, clock=time.monotonic, thread=True, refresh=REANCHOR):
        self.engine, self._clock, self._use_thread = engine, clock, thread
        self.refresh = refresh                      # seconds after keep() until the engine is asked to look again
        self._cond = threading.Condition()          # guards the fields below; never held across a call to the player
        self._adjust = None                         # {"epoch", "id", "values", "controls", "held"}: the newest wish
        self._show = None                           # {"id", "values", "controls", "preset"}: a whole shader to put on
        self._steps = None                          # {"steps": [[net, epoch, mark], ...], "presses"}: steps that wait, BEHIND _show
        self._doing = None                          # the whole shader the worker is putting on right now, if any
        self._last = -1e9                           # when the last change was applied
        self._release = None                        # when a pressed event is to be let go
        self._refresh = None                        # when the shader on screen is due for a new anchor
        self._thread = None
        self.applied = 0                            # how many times the GPU was given a new text (for tests and curiosity)

    def pending(self):
        with self._cond:
            return self._adjust

    def submit(self, target, values=None, controls=None, held=None):
        """Note a change for the shader `target` (the engine's record of what is on). Later wishes for the same
        shader are merged, value by value; a wish for another shader replaces it."""
        with self._cond:
            a = self._adjust
            if a is None or (a["epoch"], a["id"]) != (target["epoch"], target["id"]):
                a = self._adjust = {"epoch": target["epoch"], "id": target["id"], "values": {}, "controls": {}, "held": {}}
            a["values"].update(values or {})
            a["controls"].update(controls or {})
            a["held"].update(held or {})
            self._wake()

    def show(self, job):
        """Put a whole shader on (the newest wish wins), away from the caller's thread. The job carries the player's
        epoch of the moment it was asked for: whatever is played or stopped before the worker comes round keeps the
        screen. What waited before it is superseded, a whole shader and steps alike: the wishes keep their order,
        and a shader that is named does not depend on where the steps before it would have ended."""
        with self._cond:
            self._show, self._adjust, self._steps = job, None, None
            self._wake()

    def step(self, direction, epoch, mark):
        """Note a step to the next shader (+1) or the one before (-1), away from the caller's thread. A step is kept
        as what it is, a move from wherever the screen is when the worker comes to it, with the epoch and the
        level's mark of its own moment. Nothing is worked out here from what is on the screen now.

        The wishes keep their order. Steps wait BEHIND a whole shader that waits (a pad's tap, a preset of another
        shader): that shader is shown first and the steps count from it; and a whole shader asked for after them
        supersedes them (Changer.show). So what waits is at most one whole shader and one batch of steps after it.

        The batch does not grow with the presses (a knob can send hundreds): presses made under the same epoch of
        the player are one entry, [their sum, the epoch, the level's mark of the newest of them]. A new entry
        begins only when something else has taken the screen in between, and the oldest go beyond STEP_ENTRIES
        (they are the ones the worker would drop anyway). Returns how many presses wait."""
        with self._cond:
            if self._steps is None:
                self._steps = {"steps": [], "presses": 0}
            rows = self._steps["steps"]
            if rows and rows[-1][1] == epoch:
                rows[-1][0] += direction
                rows[-1][2] = mark
            else:
                rows.append([direction, epoch, mark])
                del rows[:-STEP_ENTRIES]
            self._steps["presses"] += 1
            self._adjust = None
            self._wake()
            return self._steps["presses"]

    def queued(self):
        """The wish that the worker takes next: the whole shader that waits, else the steps that wait."""
        with self._cond:
            return self._show or self._steps

    def newest(self):
        """What was asked for and is not on yet: what waits, else what the worker is putting on right now."""
        with self._cond:
            return self._show or self._steps or self._doing

    def clear(self):
        """Forget what is waiting (the module went off, Vibes was started, the box was reset)."""
        with self._cond:
            self._show = self._steps = self._adjust = self._release = self._refresh = None

    def keep(self):
        """A shader has just come on or been changed: come back in two days to give it a new anchor, if nobody has
        touched it by then."""
        with self._cond:
            self._refresh = self._clock() + self.refresh
            self._wake()

    def _wake(self):
        if self._use_thread and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="shader-changes", daemon=True)
            self._thread.start()
        self._cond.notify_all()

    def _take(self):
        """The job that is due now, or (None, seconds to wait). A whole shader may have to wait (the engine's
        `show_wait`: the effects keep a gap between two switches); what else is due is done meanwhile."""
        now = self._clock()
        hold = None
        if self._show is not None:
            hold = getattr(self.engine, "show_wait", lambda job: 0.0)(self._show)
            if hold <= 0:
                job, self._show = self._show, None
                return ("show", job), 0.0
        elif self._steps is not None:               # behind the whole shader, never before it
            job, self._steps = self._steps, None
            return ("show", job), 0.0

        def sooner(wait):
            return None, (wait if hold is None else min(wait, hold))
        if self._adjust is not None:
            wait = self._last + APPLY_GAP - now
            if wait > 0:
                return sooner(wait)
            job, self._adjust = self._adjust, None
            return ("adjust", job), 0.0
        if self._release is not None:
            wait = self._release - now
            if wait > 0:
                return sooner(wait)
            self._release = None
            return ("release", None), 0.0
        if self._refresh is not None:
            wait = self._refresh - now
            if wait > 0:
                return sooner(wait)
            self._refresh = None
            return ("refresh", "anchor"), 0.0
        return None, hold

    def pump(self):
        """Apply what is due (the worker's one step; tests call it with a fake clock). True if something was done."""
        with self._cond:
            job, _ = self._take()
        if job is None:
            return False
        kind, body = job
        try:
            if kind == "show":
                with self._cond:
                    self._doing = body
                try:
                    self.engine.play_job(body)
                finally:
                    with self._cond:
                        self._doing = None
            else:
                held = self.engine.adjust(body)
                self.applied += 1
                with self._cond:
                    self._last = self._clock()
                    self._release = self._last + EVENT_HOLD if held else None
        except Exception as e:                      # a refusal is already recorded by the engine; nothing may end the worker
            self.engine.log("pvj-web: a shader change failed: %s" % (getattr(e, "message", None) or e))
        return True

    def _loop(self):
        idle = 0.0
        while True:
            with self._cond:
                job, wait = self._take()
                if job is None:
                    if wait is None:
                        if idle >= 5.0:             # nothing to do for a while: the thread ends, the next wish starts one
                            self._thread = None
                            return
                        self._cond.wait(1.0)
                        idle += 1.0
                    else:
                        self._cond.wait(min(wait, 30.0))
                    continue
                # put it back for pump(), which takes it again outside the lock
                if job[0] == "show" and "steps" in job[1]:
                    self._steps = job[1]
                elif job[0] == "show":
                    self._show = job[1]
                elif job[0] == "adjust":
                    self._adjust = job[1]
                elif job[0] == "refresh":
                    self._refresh = self._clock()
                else:
                    self._release = self._clock()
            idle = 0.0
            self.pump()


# ---- the engine ----------------------------------------------------------------------------------------------------------
class LiveEngine(S.Engine):
    def __init__(self, api, log=print, clock=time.monotonic, tap=S.LogTap, thread=True):
        super().__init__(api, log, clock, tap)
        self.changer = Changer(self, clock, thread)
        # (every epoch of the player that the worker's last job itself made, the one it left): see play_job. Read
        # and written with no lock, because ONLY THE WORKER calls play_job (Changer.pump): do not call it from
        # anywhere else.
        self._chain = None
        self._place = None                          # (epoch, place in the set, shader) of a step whose shader did not stay: the worker's too
        self._job = threading.local()               # .doing: the queued job this thread is carrying out (the worker only)
        # epoch -> the epoch Vibes made of it while it was being ended: see adopt. Capped there and never cleared:
        # an entry is only ever looked up by a job that carries its epoch, the player's epoch only goes up, so an
        # old entry can match nothing new, and eight numbers are all it ever holds.
        self._adopted = {}
        self.guard = Guard(self, clock)
        self._refusals = {}                         # source hash -> what the GPU said; a changed file has another hash
        # Settings are edited under this lock, never under the engine's own: that one is held while the GPU looks at a
        # shader (up to four seconds), and a dwell knob, a preset or a set must not wait for it.
        self._cfg = locks.make("shaderlive.cfg", reentrant=True)
        self._bad = {}                              # (source hash, shape of the values) -> what the GPU said about it

    def board(self):
        return (getattr(self.api, "board", None) or {}).get("kind")

    # -- settings --
    def _saved(self):
        saved = self.api.settings.data.get("shaders")
        return saved if isinstance(saved, dict) else None

    def config(self):
        cfg = super().config()
        saved, board = self._saved() or {}, self.board()
        if saved.get("height") not in S.heights_for(board) or isinstance(saved.get("height"), bool):
            cfg["height"] = S.default_height(board)                 # also a height this board is not offered (1080 on a Pi 4)
        for key, read in READ:
            if key in saved:
                rows = read(saved[key])         # damaged by hand: the rows that still pass are kept, one bad row is only itself
                if rows:
                    cfg[key] = rows
        if isinstance(saved.get("active"), str) and SET_ID.fullmatch(saved["active"]):
            cfg["active"] = saved["active"]
        for key in ("guard", "faster"):
            if isinstance(saved.get(key), bool):
                cfg[key] = saved[key]
        if detail_ok(saved.get(FX_DETAIL)):
            cfg[FX_DETAIL] = saved[FX_DETAIL]
        if saved.get("clock") in S.CLOCKS:
            cfg["clock"] = saved["clock"]
        if saved.get("v") == 2:
            cfg["v"] = 2
        return cfg

    def _save(self, cfg):
        """Save the section. The first save by this version of a section the first version wrote turns that box's
        list into the set Ambient: everything that was not switched off, uploads included, without the two heavy
        shaders (see _first_set). A box with no saved section has nothing to turn: its first set stays computed
        until someone edits a set. A key that could not be read at all is left in the file as it is."""
        super()._save(self._prepared(cfg))

    def _prepared(self, cfg):
        """`cfg` as it is to be written (see _save): for a caller that writes the section together with something
        else in one save (preset_rename, with the pads)."""
        saved = self._saved()
        if cfg.get("v") != 2:
            if saved is not None and "sets" not in cfg:
                cfg["sets"] = self.sets(cfg)
            cfg["v"] = 2
        for key, read in READ:                  # a key that could not be read at all is left as it is, never written over
            if saved and key in saved and key not in cfg and read(saved[key]) is None:
                cfg[key] = saved[key]
        return cfg

    # -- the library --
    def refused(self, sid, digest, message):
        if len(self._refusals) > 4 * S.MAX_UPLOADS:
            self._refusals.clear()
        self._refusals[digest] = message

    def _digest(self, sid):
        try:
            return self._parsed(self._path(sid)[0])[1]
        except (ShaderError, ApiError):
            return None

    def library(self, cfg=None):
        """The first version's list, with what a panel needs to perform: per shader "weight" (light, medium, heavy or
        ""), "measured" (the Pi 4's numbers, for the bundled ones), "heavy" (what the guard saw on this box, or null),
        "refused" (what this box's GPU said, or null), "presets" (names), and per input "value" (what is on now, or
        what Play would use) and "varies" (whether Vibes may vary it). "vibes" is membership of the active set."""
        cfg = cfg or self.config()
        rows = super().library()
        members = {e["id"] for e in self.rotation(None, cfg, rows)["shaders"]}
        heavy = self.heavy_here(cfg)
        on = self.on_screen() if self.enabled() else None
        pads, with_preset = {}, {}
        try:
            for bank in self.api.settings.data["pads"]["banks"]:
                for pad in bank["pads"]:
                    if not pad.get("file") and isinstance(pad.get("shader"), str):
                        pads[pad["shader"]] = pads.get(pad["shader"], 0) + 1
                        if isinstance(pad.get("preset"), str) and pad["preset"]:
                            key = (pad["shader"], pad["preset"])
                            with_preset[key] = with_preset.get(key, 0) + 1
        except (AttributeError, KeyError, TypeError):
            pads, with_preset = {}, {}
        for s in rows:
            sid = s["id"]
            s["weight"], s["measured"] = weight_of(sid, s["cost"]), measured(sid)
            s["heavy"] = heavy.get(sid)
            s["presets"] = [p["name"] for p in cfg.get("presets", {}).get(sid, [])]
            s["pads"] = pads.get(sid, 0)            # how many pads hold it (D73): a Remove says so before it is done
            s["preset_pads"] = {name: n for (shader, name), n in with_preset.items() if shader == sid}     # and per preset, for its Delete
            s["refused"] = None
            if not s["error"]:
                try:
                    parsed, digest = self._parsed(self._path(sid)[0])
                except (ShaderError, ApiError):
                    parsed, digest = None, None
                s["refused"] = self._refusals.get(digest)
                s["speed_max"] = 1.0 if (performance(s.get("categories")) and not cfg.get("faster", False)) else S.SPEED_MAX
                work = work_inputs(parsed) if parsed else set()
                now = {} if not parsed else (self.current_values(parsed, on) if (on and on["id"] == sid) else self.start_values(parsed, cfg, sid)[0])
                for i in s["inputs"]:
                    i["value"] = now.get(i["name"], i["default"])
                    i["varies"] = i["type"] == "float" and i["name"] not in work
            s["vibes"] = sid in members and not s["error"]
        return rows

    def vibes_ids(self, set_id=None):
        """The shaders Vibes may pick from now: the set's own (the active set, or the one named), without those that
        are broken, that this box's GPU refused, or that the guard found too heavy here."""
        return [e["id"] for e in self.playable(set_id)["shaders"]]

    # -- presets --
    def start_values(self, parsed, cfg, sid, preset=None):
        """(values, controls, preset name) a shader starts with: the named preset, else the one called "default",
        else the file's own defaults. A stored value the file no longer takes is left out, not an error."""
        rows = cfg.get("presets", {}).get(sid, [])
        want = name_key(preset or DEFAULT_PRESET) if isinstance(preset or DEFAULT_PRESET, str) else None
        hit = next((p for p in rows if name_key(p["name"]) == want), None)
        if hit is None:
            if preset is not None:
                raise ApiError(404, "%s has no preset called %s" % (sid, S._text(str(preset), 40)))
            return {}, None, None
        inputs = {i["name"]: i for i in parsed["inputs"]}
        values = {}
        for name, v in hit["values"].items():
            if name in inputs and inputs[name]["type"] != "event":
                try:
                    values[name] = S.clean_value(inputs[name], v)
                except ShaderError:
                    pass
        return values, dict(hit["controls"]), hit["name"]

    def entry_values(self, sid, preset=None):
        """(values, controls, preset name) for a shader that a rotation is about to show."""
        try:
            parsed = self._parsed(self._path(sid)[0])[0]
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        return self.start_values(parsed, self.config(), sid, preset)

    def current_values(self, parsed, playing):
        """Every input's value as it is on the screen (an event is never held in a record)."""
        out = {i["name"]: i["default"] for i in parsed["inputs"]}
        out.update(playing["values"])
        return out

    def _presets_of(self, sid):
        cfg = self.config()
        return cfg, list(cfg.get("presets", {}).get(sid, []))

    def preset_save(self, name, sid=None):
        """Keep the values and controls that are on the screen as a preset of that shader (a new one, or over the one
        of that name)."""
        if not name_ok(name):
            raise ApiError(400, "a preset needs a name of 1 to 40 characters")
        on = self.on_screen()
        if on is None or (sid is not None and sid != on["id"]):
            raise ApiError(409, "play the shader first: a preset keeps the values that are on the screen")
        with self._cfg:
            cfg, rows = self._presets_of(on["id"])
            wish = self.changer.pending() or {}
            wish = wish if (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"]) else {}
            entry = {"name": name, "values": dict(on["values"], **wish.get("values", {})),
                     "controls": dict(on["controls"], **wish.get("controls", {}))}
            at = next((n for n, p in enumerate(rows) if name_key(p["name"]) == name_key(name)), None)
            if at is None:
                if len(rows) >= MAX_PRESETS:
                    raise ApiError(409, "at most %d presets for one shader; delete one first" % MAX_PRESETS)
                if on["id"] not in cfg.get("presets", {}) and len(cfg.get("presets", {})) >= MAX_PRESET_SHADERS:
                    raise ApiError(409, "presets are kept for at most %d shaders" % MAX_PRESET_SHADERS)
                rows.append(entry)
            else:
                rows[at] = entry
            cfg.setdefault("presets", {})[on["id"]] = rows
            self._save(cfg)
            on["preset"] = name

    def preset_rename(self, sid, name, to):
        self._path(sid)
        if not name_ok(to):
            raise ApiError(400, "a preset needs a name of 1 to 40 characters")
        with self._cfg:
            cfg, rows = self._presets_of(sid)
            hit = next((p for p in rows if isinstance(name, str) and name_key(p["name"]) == name_key(name)), None)
            if hit is None:
                raise ApiError(404, "no such preset")
            if any(p is not hit and name_key(p["name"]) == name_key(to) for p in rows):
                raise ApiError(409, "a preset with that name already exists")
            for e in cfg.get("sets", []):                           # a set that names it follows the new name
                for row in e["shaders"]:
                    if row["id"] == sid and name_key(row.get("preset", "")) == name_key(hit["name"]):
                        row["preset"] = to
            was = hit["name"]
            hit["name"] = to
            cfg["presets"][sid] = rows
            # The presets and the pads are in the one settings file: the new name and the pads that start this
            # shader with it (D73) are changed in one step and written once, so no tap finds a pad naming a preset
            # that is gone, and a write that fails leaves the file as it was.
            follow = getattr(self.api, "pads_follow_preset", None)
            with self.api.settings.lock:
                self.api.settings.data["shaders"] = self._prepared(cfg)     # with _save's own rules, and still one write
                if follow is not None:
                    follow(sid, was, to)
                self.api.settings.save()

    def preset_delete(self, sid, name):
        self._path(sid)
        with self._cfg:
            cfg, rows = self._presets_of(sid)
            keep = [p for p in rows if not (isinstance(name, str) and name_key(p["name"]) == name_key(name))]
            if len(keep) == len(rows):
                raise ApiError(404, "no such preset")
            cfg["presets"][sid] = keep
            if not keep:
                del cfg["presets"][sid]
            self._save(cfg)

    # -- rotation sets --
    def _first_set(self, cfg, rows=None):
        """The set a box has before anyone made one: the project's own shaders without the heavy ones; a third-party
        pack's shaders only if they were put in; uploads only on a box whose settings the first version saved (there
        they were in the rotation, and they stay). Shaders that were switched off stay out."""
        rows = super().library() if rows is None else rows
        old = self._saved() is not None and cfg.get("v") != 2
        out = []
        for s in rows:
            sid = s["id"]
            if sid in cfg["disabled"]:
                continue
            if s["source"] == "uploaded":
                keep = old
            elif s.get("pack", "nxlx") != "nxlx" or performance(s.get("categories")):
                keep = sid in cfg.get("included", ())       # a pack's shader, or one of ours made to perform with: only if put in
            else:
                keep = weight_of(sid, s["cost"]) != "heavy"
            if keep:
                out.append({"id": sid})
        first = {"id": FIRST_SET, "name": "Ambient", "shaders": out, "dwell": cfg["dwell"], "vary": cfg["vary"], "order": ORDERS[0]}
        # The second set a box has from the start: the project's shaders made to perform with. It is not the active one.
        show = [{"id": s["id"]} for s in rows if s["source"] == "bundled" and s.get("pack", "nxlx") == "nxlx" and performance(s.get("categories"))
                and not s["error"] and s["id"] not in cfg["disabled"] and weight_of(s["id"], s["cost"]) != "heavy"]
        return [first] + ([{"id": SHOW_SET, "name": "Show", "shaders": show, "dwell": S.DWELL_DEFAULT, "vary": True, "order": ORDERS[0]}] if show else [])

    def sets(self, cfg=None, rows=None):
        cfg = cfg or self.config()
        return [dict(e, shaders=[dict(r) for r in e["shaders"]]) for e in cfg["sets"]] if cfg.get("sets") else self._first_set(cfg, rows)

    def limit(self, parsed, controls):
        """The flash limit. Every shader of the category "Performance" caps its own flashing at 3 a second (6 with its
        Fast switch) by its TIME. The speed control multiplies TIME, so it would multiply the cap: for these shaders
        the speed stays at 1 or below, unless a full-access device has switched "faster" on for the box."""
        if controls["speed"] > 1.0 and performance(parsed.get("categories")) and not self.config().get("faster", False):
            return dict(controls, speed=1.0)
        return controls

    def rotation(self, set_id=None, cfg=None, rows=None):
        """One set, by its id or its name (any letter case); with nothing named, the active one."""
        cfg = cfg or self.config()
        every = self.sets(cfg, rows)
        if set_id is None:
            return next((e for e in every if e["id"] == cfg.get("active")), every[0])
        if isinstance(set_id, str):
            for match in (lambda e: e["id"] == set_id, lambda e: name_key(e["name"]) == name_key(set_id.strip())):     # an id first
                for e in every:
                    if match(e):
                        return e
        raise ApiError(404, "that set is not there (it may have been deleted)")

    def playable(self, set_id=None):
        """The set with only the shaders Vibes can show now, in the set's order."""
        cfg = self.config()
        rows = {s["id"]: s for s in S.Engine.library(self)}
        e = self.rotation(set_id, cfg, list(rows.values()))
        heavy = self.heavy_here(cfg)
        keep = [r for r in e["shaders"] if r["id"] in rows and not rows[r["id"]]["error"] and r["id"] not in heavy
                and self._digest(r["id"]) not in self._refusals]
        return dict(e, shaders=keep)

    def _edit_sets(self, change):
        """Change the sets under the lock: `change(cfg, sets)` edits the list (made real first if it was only the
        first set's default) and may return the id to make active."""
        with self._cfg:
            cfg = self.config()
            every = self.sets(cfg)
            active = change(cfg, every)
            try:
                cfg["sets"] = check_sets(every)
            except ValueError as e:
                raise ApiError(400, str(e))
            ids = [e["id"] for e in cfg["sets"]]
            cfg["active"] = active if active in ids else (cfg.get("active") if cfg.get("active") in ids else ids[0])
            now = next(e for e in cfg["sets"] if e["id"] == cfg["active"])
            cfg["dwell"], cfg["vary"] = now["dwell"], now["vary"]      # the first version's keys follow the active set
            self._save(cfg)

    def _set_action(self, body):
        op = body.get("op")
        if op == "add":
            import uuid

            def add(cfg, every):
                if len(every) >= MAX_SETS:
                    raise ApiError(409, "at most %d sets; delete one first" % MAX_SETS)
                every.append({"id": uuid.uuid4().hex[:8], "name": body.get("name"), "shaders": body.get("shaders", []),
                              "dwell": body.get("dwell", S.DWELL_DEFAULT), "vary": body.get("vary", True), "order": body.get("order", ORDERS[0])})
            self._edit_sets(add)
        elif op == "update":
            def update(cfg, every):
                e = next((x for x in every if x["id"] == body.get("id")), None)
                if e is None:
                    raise ApiError(404, "no such set")
                for key in ("name", "shaders", "dwell", "vary", "order"):
                    if key in body:
                        e[key] = body[key]
            self._edit_sets(update)
        elif op == "delete":
            def delete(cfg, every):
                if not any(x["id"] == body.get("id") for x in every):
                    raise ApiError(404, "no such set")
                if len(every) == 1:
                    raise ApiError(409, "the last set cannot be deleted")
                every[:] = [x for x in every if x["id"] != body.get("id")]
            self._edit_sets(delete)
        elif op == "activate":
            def activate(cfg, every):
                if not any(x["id"] == body.get("id") for x in every):
                    raise ApiError(404, "no such set")
                return body.get("id")
            self._edit_sets(activate)
        else:
            raise ApiError(400, "op must be add, update, delete or activate")

    def tune_set(self, set_id=None, dwell=None, vary=None):
        """Change how long each shader stays, or the variation, of one set (the active one if none is named)."""
        with self._cfg:
            cfg = self.config()
            target = self.rotation(set_id, cfg)
            active = target["id"] == self.rotation(None, cfg)["id"]
            for e in cfg.get("sets", []) + [cfg] if active else cfg.get("sets", []):
                if e is cfg or e["id"] == target["id"]:
                    if dwell is not None:
                        e["dwell"] = int(dwell)
                    if vary is not None:
                        e["vary"] = vary
            self._save(cfg)

    # -- the guard --
    def heavy_here(self, cfg=None):
        """The heavy marks that count on this box now: made on this board (a mark with no board is this box's own),
        at the drawing height that is set now or a lower one. A mark made at a greater height says nothing about a
        lower one, so lowering the picture detail gives every marked shader another chance; raising it keeps them."""
        cfg = cfg or self.config()
        board = self.board()
        return {sid: m for sid, m in cfg.get("heavy", {}).items() if m.get("board") in (None, board) and m.get("height", 0) <= cfg["height"]}

    def note_heavy(self, sid, verdict=None):
        """The guard found this shader too heavy in a rotation (or someone marked it by hand): remember it, with the
        drawing height and the board, so no rotation shows it until someone puts it back."""
        drops = (verdict or {}).get("drops_per_second") or 0
        with self._cfg:
            cfg = self.config()
            mark = {"at": time.strftime("%Y-%m-%d %H:%M"), "drops": drops, "height": int(cfg["height"])}
            if self.board():
                mark["board"] = self.board()
            cfg.setdefault("heavy", {})[sid] = mark
            self._save(cfg)
        if verdict:
            self.log("pvj-web: shader %s drops %s frames a second at %d lines: too heavy on this box, left out of rotations"
                     % (sid, drops, cfg["height"]))

    def unmark(self, ids):
        """Take heavy marks back."""
        with self._cfg:
            cfg = self.config()
            if any(sid in cfg.get("heavy", {}) for sid in ids):
                for sid in ids:
                    cfg["heavy"].pop(sid, None)
                self._save(cfg)

    def watch(self):
        """What the guard sees for the shader on screen, or None while it is switched off or nothing is on."""
        if not self.config().get("guard", True):
            return None
        on = self.on_screen()
        return self.guard.sample(on) if on else self.guard.sample(None)

    # -- changing what is on --
    def frames(self, carrier):
        """How many frames the carrier has played, not wrapped into three bytes (0 if it is not what plays)."""
        try:
            ipc = self.api.player.ipc
            if ipc.request("get_property", "path") != carrier:
                return 0
            t = ipc.request("get_property", "time-pos")
        except Exception:
            return 0
        return int(round(t * S.CARRIER_FPS)) if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t < 1e12 else 0

    def show(self, *args, **kwargs):
        result = super().show(*args, **kwargs)
        p = self.playing
        if result and result.get("ok") and p and p["anchor"] is not None and p["epoch"] == result["epoch"]:
            now = self.frames(p["carrier"])
            p["since"] = now - ((now - p["anchor"]) % S.FRAME_WRAP)       # the anchor as a whole count of frames
            self.changer.keep()
        return result

    def time_of(self, p):
        """(the TIME the shader on screen has reached, the carrier frame that is so in three bytes, the same frame as
        a whole count): where a new text has to go on from. The frames since the anchor are counted in whole numbers
        here, so the answer is right however long the shader has been on (the three bytes the shader reads start
        again after 6.4 days, which is why the worker gives an untouched shader a new anchor every two days). With
        the first version's clock the offset simply stays (mpv's frame number goes on counting)."""
        if p["anchor"] is None:
            return p["offset"], None, None
        now = self.frames(p["carrier"])
        gone = max(0, now - p["since"]) if p.get("since") is not None else (now - p["anchor"]) % S.FRAME_WRAP
        return p["offset"] + p["controls"]["speed"] * gone / S.CARRIER_FPS, now % S.FRAME_WRAP, now

    def adjust(self, job):
        """Give the shader on screen new values or controls (the worker's call; see Changer). The carrier, the epoch
        and TIME stay as they are: only the shader text is exchanged, and only while that shader still has the
        screen. Returns True if an event is now held down (to be let go a moment later)."""
        with self._lock:
            p = self.on_screen()
            if p is None:
                return False
            if job is None:                                         # let go of the events
                if not p.get("held"):
                    return False
                job = {"epoch": p["epoch"], "id": p["id"], "values": {}, "controls": {}, "held": {}}
            elif job == "anchor":                                   # two days untouched: the same picture from a new anchor
                if p["anchor"] is None:
                    return False
                job = {"epoch": p["epoch"], "id": p["id"], "values": {}, "controls": {}, "held": dict(p.get("held") or {}), "anchor": True}
            if (job["epoch"], job["id"]) != (p["epoch"], p["id"]):
                return False                                        # that shader has gone: the wish goes with it
            try:
                parsed, digest = self._parsed(self._path(p["id"])[0])
                state = {"values": dict(p["values"], **job["values"]), "held": dict(job["held"]), "hue": p["hue"],
                         "controls": self.limit(parsed, dict(p["controls"], **job["controls"])), "offset": p["offset"], "anchor": p["anchor"]}
                since = p.get("since")
                if state["anchor"] is not None and (state["controls"]["speed"] != p["controls"]["speed"] or job.get("anchor")):
                    state["offset"], state["anchor"], since = self.time_of(p)       # TIME goes on from where it is, at the new pace
                desc = "nxlx shader %d %d" % (os.getpid(), self._serial + 1)
                text = self.compose(parsed, p["size"], state, desc)
                key = (digest, S.shape_of(parsed, dict(state["values"], **state["held"])))
            except (ShaderError, ApiError) as e:
                self.error = {"id": p["id"], "message": str(getattr(e, "message", e)), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                return False
            if key in self._bad:                    # the GPU has refused exactly this before: it is not asked again
                self.error = {"id": p["id"], "message": self._bad[key], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                return False
            player = self.api.player
            try:
                out = self._write(text)
            except OSError as e:
                raise ApiError(500, "could not write the shader: %s" % (e.strerror or e))
            tap = None
            if key not in self._checked and self._gpu_output():
                try:
                    tap = self._tap(player.socket_path)
                except OSError:
                    tap = None
            try:
                try:
                    done = player.swap_source(out, p["epoch"])
                except Exception as e:
                    self._cleanup({p["path"]})
                    raise ApiError(503, str(e))
                if not done:
                    self._cleanup({p["path"]})
                    return False
                verdict, message = self._watch(tap, desc) if tap else ("unknown", "")
            finally:
                if tap:
                    tap.close()
            if verdict == "refused":                                # back to the text that was on: the same shader, as it was
                try:
                    player.swap_source(p["path"], p["epoch"])
                except Exception:
                    pass
                self._cleanup({p["path"]})
                self.error = {"id": p["id"], "message": message, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                if len(self._bad) >= 256:
                    self._bad.clear()
                self._bad[key] = message
                self.log("pvj-web: shader %s refused with new values: %s" % (p["id"], message))
                return False
            if verdict == "ok":
                self._checked.add(key)
            preset = p.get("preset") if not job["values"] and not job["controls"] else job.get("preset")
            self.playing = dict(p, values=state["values"], held=state["held"], controls=state["controls"], offset=state["offset"],
                                anchor=state["anchor"], since=since, path=out, desc=desc, digest=digest, preset=preset,
                                checked=True if (verdict == "ok" or key in self._checked) else p["checked"])
            self._cleanup({out})
            if state["anchor"] is not None:
                self.changer.keep()
            return bool(state["held"])

    def play_job(self, job):
        """Put a whole shader on from the worker (a step to the next or the one before, a preset of another shader),
        only if nothing was played or stopped since it was asked for."""
        if not self.enabled():
            return                                  # the module went off meanwhile: nothing to show, nothing to report
        # "Nothing was played since" must not count the queue's own job before this one. A wish that comes while
        # the worker is busy with the one before it carries an epoch that job itself makes or has made: the one it
        # started from (the wish came before it took the screen), the one its taking of the screen made (the wish
        # came while the GPU looked at it, which can be seconds), or the one after its own stop (the GPU refused it
        # and the screen went black). The player refused such a wish and the OLDER one stayed on, or nothing. So
        # every epoch a job itself makes is written into the job as it is made (`made`, by Engine._made, at once and
        # also if something raises afterwards), and the worker keeps them with the last of them: a job that carries
        # any of them is given the last. Anything else that took the screen in between (a clip, a Stop, a shader
        # from the panel) moved the epoch to one the job did not make, and refuses the next job as before.
        if "steps" in job:
            if not self._resolve_steps(job):
                return
        epoch = self._alias(job["epoch"])
        job["made"] = [epoch]                       # what it starts from, then what it makes
        self._job.doing = job
        try:
            for _ in range(3):
                shown = self.play(job["id"], job.get("values"), job.get("controls"), job.get("preset"), epoch=epoch, queued=True,
                                  mark=job["mark"] if "mark" in job else S.Engine.NOW)
                if shown is not None:
                    break
                # Refused: the epoch moved after it was read here. If the rotation moved it on its way out, that is
                # noted in the same step as the move (adopt), under the player's lock: looked at again under that
                # lock, the epoch this job carries now stands for the new one, and the job is tried with it.
                player = self.api.player
                with (getattr(player, "_lock", None) or locks.make("player")):
                    again = self._alias(job["epoch"])
                if again == epoch:
                    break
                epoch = again
                job["made"] = [epoch]
            if shown is None:                       # never silent: a wish that was dropped says so in the journal
                self.log("pvj-web: shader %s, asked for from a controller, was not shown: something else was played or "
                         "stopped after it was asked for" % job["id"])
        except ApiError as e:
            self.error = {"id": job["id"], "message": e.message, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "epoch": self.api.player.source_epoch}       # see play(): news while the screen stays as it is
        finally:
            self._job.doing = None
            if len(job["made"]) > 1:                # it took the screen, whatever came of it afterwards
                # If it started from where the job before it left, the epochs that job made stand for this one's
                # last too: a wish that waited through both (a step behind a pad's tap, pressed while the worker
                # was busy with the job before the tap) is still only behind the queue's own jobs. Only the job
                # before: the set does not grow with the jobs.
                made = frozenset(job["made"])
                before = self._chain[2] if self._chain is not None and self._chain[1] == epoch else frozenset()
                self._chain = (before | made, job["made"][-1], made)
            if "steps" in job:
                # Where the next step counts from if this one's shader did not stay (the GPU refused it): its place
                # in the set, good while nothing else takes the screen. A shown shader needs none: it is on.
                on = self.on_screen()
                stays = on is not None and on["id"] == job["id"]
                self._place = None if stays else (job["made"][-1], job["place"], job["id"])

    def _alias(self, epoch):
        """`epoch` as the queue reads it: one that the worker's last job itself made stands for the one it left, and
        one that the rotation moved on its way out (adopt) for where it moved it to."""
        for _ in range(ADOPTED + 2):
            if self._chain is not None and epoch in self._chain[0] and epoch != self._chain[1]:
                epoch = self._chain[1]
            elif epoch in self._adopted:
                epoch = self._adopted[epoch]
            else:
                break
        return epoch

    def adopt(self, before, after):
        """Vibes, while it was being ended, moved the player's epoch from `before` to `after`: its next shader had
        already taken the screen when the wish that ended it came, or its stop cleared the screen. A wish queued
        for the worker carries the epoch of its own moment, which may be `before`; it is the wish that ended the
        rotation (or came right after its Stop), so the rotation's own last moves must not refuse it. Without
        this a controller's pad tapped while Vibes changed shaders left the screen black and said nothing (the
        fifth read of #114). Called from Vibes' thread; a plain dict, a few entries."""
        if before is None or after is None or before == after:
            return
        self._adopted[before] = after
        while len(self._adopted) > ADOPTED:
            self._adopted.pop(next(iter(self._adopted)), None)

    def _resolve_steps(self, job):
        """Turn the steps that waited into one shader to show, now, in the worker: the only place that knows what
        the job before this one did. A step is still good if nothing but the queue's own jobs took the screen since
        it was pressed (its epoch, read through _alias, is the player's now); a step pressed before a clip, a Stop
        or a shader from the panel is dropped, as a queued shader is refused. The good ones add up, so two Next make
        two and a Next and a Previous make none. They count from the shader on the screen, which is what the
        step before them showed (a shader the GPU refused has left the set and the one before it is back on, so
        the next step goes past it); with none on, or one the set does not hold, a Next starts at the first of the
        set and a Previous at the last. The set is read here too, so a set changed between two steps is the one stepped through.
        Fills in the job's id, epoch and mark; False if there is nothing to show."""
        now = self.api.player.source_epoch
        good = [s for s in job["steps"] if self._alias(s[1]) == now]
        n = sum(s[0] for s in good)
        if not n:
            return False
        ids = self.vibes_ids()
        if not ids:                                 # emptied since the press, which had one: said in the log, as it
            self.log("pvj-web: a step to the next shader found none in the active set that can be shown")
            return False                            # is nobody's shader that failed (`error` names a shader)
        on = self.on_screen()                       # the worker does one job at a time: the job before this one is
        at = on["id"] if on else None               # over, and what it showed (or what came back when the GPU
        place = self._place if self._place is not None and self._place[0] == now else None
        if at in ids:                               # refused it) is what is on the screen
            at = ids.index(at) + n
        elif place is not None and place[2] in ids:
            at = ids.index(place[2]) + n
        elif place is not None:
            # The step before this one went to a shader the GPU refused, with nothing to go back to: the screen is
            # black and that shader has left the set. Its place is kept: the one that stood after it stands there
            # now, so a Next goes on from where it was and a Previous back from there.
            at = place[1] + (n - 1 if n > 0 else n)
        else:
            at = n - 1 if n > 0 else n              # no shader on: a Next starts at the first, a Previous at the last
        job["place"] = at % len(ids)
        job["id"] = ids[job["place"]]
        job["epoch"] = now
        if good[-1][2] is not S.Engine.NOW:
            job["mark"] = good[-1][2]               # the level's mark of the newest of them
        return True

    def _made(self, epoch):
        job = getattr(self._job, "doing", None)
        if job is not None:
            job["made"].append(epoch)

    def moment(self):
        """(the player's epoch, the level's mark) of now: what a wish that is queued for the worker carries with it.
        The two are read one after the other under no common lock, on purpose: the player's lock is held over a
        load, and a controller's tap must not wait for that. It is safe because each is only ever compared with its
        own later value: something played between the two readings moves the epoch past the one read (the job is
        refused, as it should be for a play that came with the tap), and a wish for the level between them is
        older than the mark (the shader then sets the level, as it does for a wish made just before the tap)."""
        marker = getattr(self.api, "_level_mark", None)
        epoch = self.api.player.source_epoch
        return epoch, (marker() if marker is not None else S.Engine.NOW)

    def drop_waiting(self, why):
        """Forget what waits for the worker (Vibes was started: it wins), and say in the journal what was dropped: a
        controller's tap that was answered "pending" must not vanish without a word."""
        waiting = self.changer.queued()
        self.changer.clear()
        if waiting is None:
            return
        if "steps" in waiting:
            self.log("pvj-web: %d step(s) to the next shader, asked for from a controller, were not carried out: %s" % (waiting.get("presses", 0), why))
        else:
            self.log("pvj-web: shader %s, asked for from a controller, was not shown: %s" % (waiting.get("id"), why))

    def queue_show(self, sid, preset=None, moment=None):
        """Put a whole shader on from the worker, for a caller that must not wait for the GPU (a controller's pad, a
        preset of another shader). What belongs to the moment of asking is taken here and carried with the
        job: the player's epoch (what is played or stopped after this keeps the screen) and the level's mark (a
        Fade out, a Blackout or the slider moved after this stands when the shader comes). `moment` is for a
        caller that has read it already. (A step is not queued here: it has no shader yet, see Changer.step.)"""
        epoch, mark = moment or self.moment()
        job = {"id": sid, "epoch": epoch}
        if preset is not None:
            job["preset"] = preset
        if mark is not S.Engine.NOW:
            job["mark"] = mark
        self.changer.show(job)

    def off(self, epoch=None, adopt=False):
        if epoch is None:                           # the module was switched off: what was waiting goes too
            self.changer.clear()
        return super().off(epoch, adopt)

    def play(self, sid, values=None, controls=None, preset=None, epoch=None, queued=False, mark=S.Engine.NOW):
        """Show one shader by hand: with the values given, else its named preset, else its "default" preset, else the
        file's defaults. The rotation ends. Raises 422 if the player refuses it. With `epoch`, only if nothing else
        was played or stopped since (None is returned then); `queued` is the worker's call, whose request already
        ended the rotation when it was made."""
        self._need()
        vibes = getattr(self.api, "vibes", None)
        if vibes and not queued:
            vibes.yield_screen()                    # the operator chose a shader: the rotation ends
        path, _ = self._path(sid)
        try:
            parsed = self._parsed(path)[0]
            start, stored, name = self.start_values(parsed, self.config(), sid, preset)
            if values is not None:
                start = dict(start, **S.clean_values(parsed, values))
                name = None if values else name
            # The shader that is already on, played again (the panel's sliders do this): it goes on from the TIME it
            # has reached, with the palette turn and the controls it has, instead of starting over.
            on = self.on_screen()
            same = on is not None and on["id"] == sid
            if same and stored is None:
                stored = on["controls"]
            result = self.show(sid, start, hue=on["hue"] if same else 0.0, offset=self.time_of(on)[0] if same else 0.0,
                               controls=S.clean_controls(controls, stored), preset=name, epoch=epoch, mark=mark)
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        if result is None:
            return None                             # the screen went to something else: it keeps it
        if not result["ok"]:
            if not result["showing"]:
                self.off(result["epoch"])           # nothing to go back to: stop, which leaves the screen black
            # For the Live page (Api.status, `shader_refused`): the refusal is news while the screen is as the
            # refusal left it. The player's epoch of now is kept with it; a clip, another shader, a Stop or Vibes
            # moves the epoch, and the line under the pads goes. (The Shaders page keeps `error` as before.)
            if self.error and self.error.get("id") == sid:
                self.error = dict(self.error, epoch=self.api.player.source_epoch)
            raise ApiError(422, "the player refused %s: %s. %s" % (
                result["id"], result["error"], "The shader before it is back on." if result["showing"] else "The screen is black."))
        self._refusals.pop(self.playing["digest"] if self.playing else None, None)
        return result

    def nth_control(self, parsed, n):
        """The n-th input a controller can drive (1 to 8): numbers, switches, choices and events, in the file's order."""
        able = [i for i in parsed["inputs"] if i["type"] in ("bool", "long", "event") or (i["type"] == "float" and i["max"] > i["min"])]
        return able[n - 1] if 1 <= n <= min(len(able), MAX_CONTROLS) else None

    def _from_controller(self, i, now, level, press):
        """The value a controller's knob (`level`, 0 to 127) or button (`press`) gives input `i`, whose value is `now`."""
        kind = i["type"]
        if kind == "event":
            return True if (press or (level is not None and level >= 64)) else None
        if level is not None:
            if kind == "float":
                return i["min"] + (i["max"] - i["min"]) * level / 127.0
            if kind == "bool":
                return level >= 64
            if "values" in i:
                return i["values"][min(len(i["values"]) - 1, level * len(i["values"]) // 128)]
            return int(round(i["min"] + (i["max"] - i["min"]) * level / 127.0))
        if kind == "float":
            return i["default"]                     # a button on a number: back to the file's own value
        if kind == "bool":
            return not now
        if "values" in i:
            return i["values"][(i["values"].index(now) + 1) % len(i["values"])] if now in i["values"] else i["values"][0]
        return i["min"] if now >= i["max"] else now + 1

    def change(self, body):
        """Note a change to the shader on screen and return at once: {"values"?: {input: value}, "controls"?: {speed,
        hue, brightness}, "id"?: the shader it is meant for} or, from a controller, {"control": 1 to 8, "level": 0 to
        127} or {"control": n, "press": true}. Everything is checked here; the GPU gets it from the worker."""
        self._need()
        on = self.on_screen()
        if on is None:
            raise ApiError(409, "no shader is on the screen")
        if body.get("id") is not None and body["id"] != on["id"]:
            raise ApiError(409, "%s is not the shader on the screen" % S._text(str(body["id"]), 60))
        try:
            parsed = self._parsed(self._path(on["id"])[0])[0]
            values = S.clean_values(parsed, body.get("values"))
            controls = S.clean_controls(body.get("controls"), {}) if body.get("controls") is not None else {}
            if "control" in body:
                n, level, press = body["control"], body.get("level"), body.get("press")
                if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_CONTROLS:
                    raise ApiError(400, "control must be 1 to %d" % MAX_CONTROLS)
                if (level is None) == (press is None) or (press is not None and press is not True) or (
                        level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 127)):
                    raise ApiError(400, "send level (0 to 127) or press (true)")
                i = self.nth_control(parsed, n)
                if i is None:
                    raise ApiError(404, "%s has no control %d" % (on["id"], n))
                wish = self.changer.pending() or {}
                now = dict(self.current_values(parsed, on), **(wish.get("values", {}) if wish.get("epoch") == on["epoch"] else {}))
                v = self._from_controller(i, now.get(i["name"], i["default"]), level, press)
                if v is not None:
                    values[i["name"]] = S.clean_value(i, v)
        except ShaderError as e:
            raise ApiError(400, str(e))
        events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
        held = {n: True for n, v in values.items() if n in events and v}
        values = {n: v for n, v in values.items() if n not in events}
        if self._bad and (values or held):          # a switch or choice the GPU refused before is refused here, at once
            wish = self.changer.pending() or {}
            after = dict(on["values"], **(wish.get("values", {}) if wish.get("epoch") == on["epoch"] else {}))
            said = self._bad.get((self._digest(on["id"]), S.shape_of(parsed, dict(after, **dict(values, **held)))))
            if said:
                raise ApiError(422, "the GPU refused these values before (%s); they were not sent again" % said)
        if values or controls or held:
            self.changer.submit(on, values, controls, held)
        wish = self.changer.pending() or {}
        return {"ok": True, "id": on["id"], "values": dict(self.current_values(parsed, on), **wish.get("values", {})),
                "controls": self.limit(parsed, dict(on["controls"], **wish.get("controls", {})))}

    def apply_preset(self, body):
        """{"name": preset} or {"index": 1 to 8} for the shader on screen, or with "id" for another shader, which is
        then put on. Answers at once; the worker does it."""
        self._need()
        on = self.on_screen()
        sid = body.get("id") if body.get("id") is not None else (on["id"] if on else None)
        if sid is None:
            raise ApiError(409, "no shader is on the screen")
        path, _ = self._path(sid)
        cfg = self.config()
        rows = cfg.get("presets", {}).get(sid, [])
        name = body.get("name")
        if "index" in body:
            n = body["index"]
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PRESETS:
                raise ApiError(400, "index must be 1 to %d" % MAX_PRESETS)
            if n > len(rows):
                raise ApiError(404, "%s has no preset %d" % (sid, n))
            name = rows[n - 1]["name"]
        if not isinstance(name, str):
            raise ApiError(400, "name the preset")
        try:
            parsed = self._parsed(path)[0]
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        values, controls, name = self.start_values(parsed, cfg, sid, name)
        if on and on["id"] == sid:
            full = {i["name"]: i["default"] for i in parsed["inputs"] if i["type"] != "event"}       # a preset sets every input
            self.changer.submit(on, dict(full, **values), controls)
            with self.changer._cond:
                if self.changer._adjust:
                    self.changer._adjust["preset"] = name
        else:
            vibes = getattr(self.api, "vibes", None)
            if vibes:
                vibes.yield_screen()                # now, at the request: the worker never ends a rotation
            self.queue_show(sid, name)
        return {"ok": True, "id": sid, "preset": name}

    def step(self, direction):
        """The next shader of the active set, or the one before. While Vibes runs it is Vibes that steps; otherwise
        the neighbour of the shader on screen in the set's own order is put on."""
        self._need()
        if direction not in (1, -1):
            raise ApiError(400, "dir must be 1 (next) or -1 (the one before)")
        vibes = getattr(self.api, "vibes", None)
        if vibes and vibes.running:
            return vibes.skip(direction)
        ids = self.vibes_ids()
        if not ids:
            raise ApiError(409, "the active set has no shader that can be shown")
        # Queued as what it is, a move, and turned into a shader by the worker when it comes to it
        # (_resolve_steps). Which shader that is cannot be known here: it depends on what the jobs before it will
        # have shown, so the answer says how many steps wait and names none.
        epoch, mark = self.moment()
        return {"ok": True, "steps": self.changer.step(direction, epoch, mark)}

    # -- delete also clears what was kept for the file --
    def delete(self, sid):
        super().delete(sid)
        with self._cfg:
            cfg = self.config()
            had = sid in cfg.get("presets", {}) or sid in cfg.get("heavy", {}) or any(r["id"] == sid for e in cfg.get("sets", []) for r in e["shaders"])
            if had:
                cfg.get("presets", {}).pop(sid, None)
                cfg.get("heavy", {}).pop(sid, None)
                for e in cfg.get("sets", []):
                    e["shaders"] = [r for r in e["shaders"] if r["id"] != sid]
                self._save(cfg)

    # -- requests --
    def state(self):
        cfg = self.config()
        board = self.board()
        on = self.on_screen() if self.enabled() else None
        base = super().state()
        if base["playing"] and on:
            wish = self.changer.pending() or {}
            mine = (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"])
            row = next((s for s in base["shaders"] if s["id"] == on["id"]), None)
            if row:
                base["playing"]["values"] = {i["name"]: i["value"] for i in row["inputs"] if i["type"] != "event"}
            base["playing"].update(controls=dict(on["controls"]), preset=on.get("preset"), pending=bool(mine))
            seen = self.watch()
            if seen:
                base["playing"].update(drops_per_second=seen["drops_per_second"], load=seen["state"])
        seen = self.guard.verdict if on else {}
        active = self.rotation(None, cfg, base["shaders"]) if self.enabled() else None
        base["config"].update(guard=cfg.get("guard", True), clock=cfg.get("clock", S.CLOCKS[0]), faster=cfg.get("faster", False))
        if active:
            base["config"].update(dwell=active["dwell"], vary=active["vary"])
        base["render"].update(heights=list(S.heights_for(board)), default=S.default_height(board), measured=board == "pi4", board=board)
        base["sets"] = self.sets(cfg, base["shaders"]) if self.enabled() else []
        base["active"] = active["id"] if active else None
        base["controls"] = {"speed": {"min": S.SPEED_MIN, "max": S.SPEED_MAX, "default": 1.0}, "hue": {"min": -180.0, "max": 180.0, "default": 0.0},
                            "brightness": {"min": S.GAIN_MIN, "max": S.GAIN_MAX, "default": 1.0}}
        base["gpu"] = seen.get("gpu")
        base["limits"].update(presets=MAX_PRESETS, sets=MAX_SETS, set_shaders=MAX_SET_ENTRIES, name=40, controls=MAX_CONTROLS)
        return base

    def api_play(self, body, device, client):
        """{"id": "name.fs", "values"?: {input: value}, "controls"?: {speed, hue, brightness}, "preset"?: name}: show
        one shader until something else is played."""
        self.play(body.get("id"), body.get("values"), body.get("controls"), body.get("preset"))
        return self.state()

    def api_values(self, body, device, client):
        return self.change(body)

    def api_step(self, body, device, client):
        return self.step(body.get("dir", 1))

    def api_preset(self, body, device, client):
        return self.apply_preset(body)

    def api_presets(self, body, device, client):
        """Full access: {"action": "save", "name", "id"?}, {"action": "rename", "id", "name", "to"},
        {"action": "delete", "id", "name"}."""
        self._need()
        action = body.get("action")
        if action == "save":
            self.preset_save(body.get("name"), body.get("id"))
        elif action == "rename":
            self.preset_rename(body.get("id"), body.get("name"), body.get("to"))
        elif action == "delete":
            self.preset_delete(body.get("id"), body.get("name"))
        else:
            raise ApiError(400, "action must be save, rename or delete")
        return self.state()

    def api_set(self, body, device, client):
        """The first version's actions (upload, delete, vibes, config) and: {"action": "set", "op": "add" | "update" |
        "delete" | "activate", ...}, {"action": "heavy", "id", "on": bool} (false puts a shader the guard took out
        back into the rotations), and in "config" also "guard" (bool) and "clock" ("carrier" or "frame")."""
        self._need()
        action = body.get("action")
        if action == "set":
            self._set_action(body)
        elif action == "heavy":
            sid, on = body.get("id"), body.get("on")
            self._path(sid)
            if not isinstance(on, bool):
                raise ApiError(400, "on must be true or false")
            if on:
                self.note_heavy(sid)
            else:
                self.unmark([sid])
        elif action == "vibes":                     # in or out of the active set
            sid, on = body.get("id"), body.get("on")
            self._path(sid)
            if not isinstance(on, bool):
                raise ApiError(400, "on must be true or false")

            def toggle(cfg, every):
                e = next((x for x in every if x["id"] == cfg.get("active")), every[0])
                e["shaders"] = [r for r in e["shaders"] if r["id"] != sid] + ([{"id": sid}] if on else [])
                if on:
                    cfg.get("heavy", {}).pop(sid, None)             # put back by hand: the guard's note goes
            self._edit_sets(toggle)
        elif action == "config":
            if "height" in body and (isinstance(body["height"], bool) or body["height"] not in S.heights_for(self.board())):
                raise ApiError(400, "height must be one of %s on this board" % ", ".join(str(h) for h in S.heights_for(self.board())))
            for key, ok in (("guard", lambda v: isinstance(v, bool)), ("faster", lambda v: isinstance(v, bool)), ("clock", lambda v: v in S.CLOCKS)):
                if key in body and not ok(body[key]):
                    raise ApiError(400, "guard and faster must be true or false, and clock carrier or frame")
            # Read, change and write with the settings lock held, once. This used to be two writes (the first
            # version's keys, written without the lock, then this engine's): a change that arrived between the read
            # and the write of an earlier one's second step was overwritten by it, and the later choice was lost.
            with self._cfg:
                cfg = self.config()
                self._config_keys(cfg, body)
                for key in ("guard", "clock", "faster"):
                    if key in body:
                        cfg[key] = body[key]
                self._save(cfg)
                self.tune_set(None, cfg["dwell"], cfg["vary"])      # dwell and variation belong to the active set
            on = self.on_screen()
            if "faster" in body and on:             # the limit applies to what is on now, not only to the next shader
                self.changer.submit(on, controls={"speed": on["controls"]["speed"]})
        else:
            return super().api_set(body, device, client)
        return self.state()
