# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Text and logos on the screen (D129, slice 2 of design/text-and-logo-sources_2026-11-08.md).

A graphic is a text or a logo that the node draws over the picture. This slice draws them still, on every tier, the one
way that works on every board: pvj-raster (render/raster/, its own process with its own limits) makes each graphic into
one small premultiplied picture, and the player draws it with mpv's `overlay-add` (ids OVERLAY_BASE to OVERLAY_BASE + 7,
in the order of the list: a later graphic over an earlier one). Motion, keys and effects, and drawing over or as a
source, come in later slices; this one says so in words where the panel asks.

What is stored, in settings.json under "graphics" (no schema change: read with defaults everywhere, as D100 did):

    {"on": true,                                  the whole stage's switch ("Hide all")
     "items": [{"id", "slot", "kind", "name", "on", "where", "place", "text" or "logo", "motion", "key", "fx"}, ...]}

Anything read from the file is read, never trusted: unknown keys dropped, numbers clamped, a font that is not bundled
becomes Archivo, a logo that is not in the store shows as missing and is left out of the picture.

Text in a request or from OSC is cleaned before it is used (control and format characters out, Unicode normalised,
trimmed, cut to 280 characters and 6 lines) and is never interpreted: there is no markup and no tag, so a typed line
cannot become anything but letters. A worker builds the pictures, newest change wins, at most 5 a second for one
graphic and 20 a second in all; a burst keeps the last.

| lock             | guards                                                                                   |
| ---------------- | ---------------------------------------------------------------------------------------- |
| `_apply`         | graphics.apply: the check of the newest build and its draw into the player, together     |
| `_lock`          | graphics.state: the counters, the work to do and the status; never held over a call      |
"""
import collections
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata

from . import locks, raster as raster_mod

OVERLAY_BASE = 20               # ids 20 to 27; the masks keep 10, the pairing code 1 to 4 and transitions 63 (design 9)
MAX_ITEMS = 8
MAX_ON = {"lean": 4, "standard": 4, "full": 8, "unknown": 4}      # on at once, by tier (design 3.2)
MAX_TEXT = 280
MAX_TEXT_LINES = 6
MAX_SAVED_LINES = 20
MEMORY_BUDGET = 48 * 1024 * 1024        # all the pictures that are on, together (design 3.2)
SAFE = 0.05                             # an edge anchor sits this far in from the edge of the screen
PER_ITEM_INTERVAL = 0.2                 # at most 5 builds a second for one graphic
ALL_INTERVAL = 0.05                     # and 20 a second in all
SAVE_INTERVAL = 0.5                     # settings.json is written at most twice a second however fast OSC pushes lines
PREVIEWS_PER_SECOND = 5
PREVIEW_SIDE = 640                      # the preview's width at most
ANCHORS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
ALIGNS = ("left", "center", "right")
FILE_RE = r"graphic-([a-z0-9]+)-(\d+)-(\d+)\.bgra"

# What this slice can do. The panel greys out what is not here, with the reason (design 3 and 11).
MOTIONS = ("none",)
KEYS = ("alpha",)
EFFECTS = ()
WHERE = ("screen",)
LATER = "comes in a later release; for now a graphic is drawn over the screen, still"


class GraphicsError(Exception):
    """A refusal, with words for the person (status is the HTTP status)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def bad(message):
    return GraphicsError(400, message)


# ---- fonts: bundled, open licence only (design 4.2) ----------------------------------------------------------------------
def _font(fid, name, lo, hi, default, note):
    return {"id": fid, "name": name, "licence": "SIL OFL 1.1", "min": lo, "max": hi, "default": default, "note": note}


FONTS = (
    _font("archivo", "Archivo", 100, 900, 700, "The panel's own look: clean titles"),
    _font("jetbrains-mono", "JetBrains Mono", 100, 800, 500, "Monospace: good for typing out"),
    _font("inter", "Inter", 100, 900, 600, "Neutral, readable at a distance"),
    _font("bebas-neue", "Bebas Neue", 400, 400, 400, "Tall condensed capitals: the classic DJ name"),
    _font("oswald", "Oswald", 200, 700, 500, "Condensed and strong, many weights"),
    _font("anton", "Anton", 400, 400, 400, "Heavy poster capitals"),
    _font("montserrat", "Montserrat", 100, 900, 700, "Wide and geometric: good for lower thirds"),
    _font("orbitron", "Orbitron", 400, 900, 700, "A techno look"),
    _font("press-start-2p", "Press Start 2P", 400, 400, 400, "A pixel look"),
    _font("roboto-condensed", "Roboto Condensed", 100, 900, 500, "Narrow: tickers and long lines"),
    _font("noto-sans", "Noto Sans", 100, 900, 600, "Latin, Cyrillic and Greek; any letter another font lacks comes from it"),
)
FONT_IDS = tuple(f["id"] for f in FONTS)
DEFAULT_FONT = "archivo"


def font_of(fid):
    return next((f for f in FONTS if f["id"] == fid), None) or FONTS[0]


# ---- cleaning what is typed ----------------------------------------------------------------------------------------------
def clean_text(value, max_lines=MAX_TEXT_LINES, max_chars=MAX_TEXT):
    """(text, note) for a typed line. A newline is kept; every other control, format, surrogate, private-use or
    unassigned character is dropped (that takes the invisible ones that can turn a line round or hide a word);
    Unicode is normalised; the ends are trimmed; the length is cut. note says what was cut, or is ""."""
    if not isinstance(value, str):
        return "", ""
    t = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    t = "".join(c for c in t if c == "\n" or unicodedata.category(c)[0] != "C")
    t = "\n".join(line.strip(" \t ") for line in t.split("\n")).strip("\n")
    notes = []
    if len(t) > max_chars:
        t = t[:max_chars].rstrip()
        notes.append("cut to %d characters" % max_chars)
    lines = t.split("\n")
    if len(lines) > max_lines:
        t = "\n".join(lines[:max_lines])
        notes.append("cut to %d lines" % max_lines)
    return t, "; ".join(notes)


# ---- the numbers and colours people may send -------------------------------------------------------------------------------
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _num(v, lo, hi, default):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, v))


def _int(v, lo, hi, default):
    v = _num(v, lo, hi, default)
    return int(round(v))


def _colour(v, default):
    return v.lower() if isinstance(v, str) and HEX.fullmatch(v) else default


def _bool(v, default):
    return v if isinstance(v, bool) else default


def _name(v, default):
    if not isinstance(v, str):
        return default
    v = "".join(c for c in unicodedata.normalize("NFC", v) if unicodedata.category(c)[0] != "C").strip()
    return v[:40] or default


# ---- the items -------------------------------------------------------------------------------------------------------------
def default_text():
    return {"value": "", "font": DEFAULT_FONT, "weight": font_of(DEFAULT_FONT)["default"], "size": 9, "colour": "#ffffff",
            "align": "center", "leading": 110,
            "outline": {"on": False, "colour": "#000000", "width": 4},
            "shadow": {"on": True, "colour": "#000000", "soft": 40},
            "plate": {"on": False, "colour": "#000000", "opacity": 60, "pad": 25},
            "lines": [], "line": -1}


def default_place(kind):
    return {"x": 0, "y": 0, "scale": 100, "rotate": 0, "opacity": 100, "mirror": False, "anchor": "bc" if kind == "text" else "tr"}


def text_of(raw):
    """A text section as stored: every key present, every value in range."""
    raw = raw if isinstance(raw, dict) else {}
    d = default_text()
    font = raw.get("font") if raw.get("font") in FONT_IDS else DEFAULT_FONT
    f = font_of(font)
    sub = lambda k: raw.get(k) if isinstance(raw.get(k), dict) else {}      # noqa: E731
    o, s, p = sub("outline"), sub("shadow"), sub("plate")
    lines = []
    for x in raw.get("lines") if isinstance(raw.get("lines"), list) else []:
        t, _ = clean_text(x)
        if t and len(lines) < MAX_SAVED_LINES:
            lines.append(t)
    return {"value": clean_text(raw.get("value"))[0], "font": font,
            "weight": _int(raw.get("weight"), f["min"], f["max"], f["default"]),
            "size": round(_num(raw.get("size"), 1, 60, d["size"]), 2), "colour": _colour(raw.get("colour"), d["colour"]),
            "align": raw.get("align") if raw.get("align") in ALIGNS else "center",
            "leading": _int(raw.get("leading"), 80, 250, d["leading"]),
            "outline": {"on": _bool(o.get("on"), False), "colour": _colour(o.get("colour"), "#000000"), "width": round(_num(o.get("width"), 1, 20, 4), 1)},
            "shadow": {"on": _bool(s.get("on"), True), "colour": _colour(s.get("colour"), "#000000"), "soft": _int(s.get("soft"), 0, 100, 40)},
            "plate": {"on": _bool(p.get("on"), False), "colour": _colour(p.get("colour"), "#000000"), "opacity": _int(p.get("opacity"), 0, 100, 60),
                      "pad": _int(p.get("pad"), 0, 100, 25)},
            "lines": lines, "line": _int(raw.get("line"), -1, max(-1, len(lines) - 1), -1) if lines else -1}


def place_of(raw, kind):
    raw = raw if isinstance(raw, dict) else {}
    d = default_place(kind)
    return {"x": round(_num(raw.get("x"), -100, 100, 0), 1), "y": round(_num(raw.get("y"), -100, 100, 0), 1),
            "scale": _int(raw.get("scale"), 10, 400, 100), "rotate": round(_num(raw.get("rotate"), -360, 360, 0), 1),
            "opacity": _int(raw.get("opacity"), 0, 100, 100), "mirror": _bool(raw.get("mirror"), False),
            "anchor": raw.get("anchor") if raw.get("anchor") in ANCHORS else d["anchor"]}


def item_of(raw):
    """One item as stored, or None for something that is not one. Slice 2 draws everything over the screen, still: what
    a later release wrote for another place or another motion is read as that (and is kept when the file is saved only
    if it was never touched here: the panel writes what this slice knows)."""
    if not isinstance(raw, dict) or raw.get("kind") not in ("text", "logo"):
        return None
    kind = raw["kind"]
    gid = raw.get("id") if isinstance(raw.get("id"), str) and re.fullmatch(r"g[0-9a-f]{4,12}", raw["id"]) else None
    if gid is None:
        return None
    out = {"id": gid, "slot": _int(raw.get("slot"), 1, MAX_ITEMS, 0), "kind": kind,
           "name": _name(raw.get("name"), "Text" if kind == "text" else "Logo"), "on": raw.get("on") is True,
           "where": {"mode": "screen"}, "place": place_of(raw.get("place"), kind),
           "motion": {"kind": "none"}, "key": {"kind": "alpha"}, "fx": []}
    if kind == "text":
        out["text"] = text_of(raw.get("text"))
    else:
        logo = raw.get("logo") if isinstance(raw.get("logo"), dict) else {}
        f = logo.get("file")
        out["logo"] = {"file": f if isinstance(f, str) and f.endswith(".png") and len(f) <= 120 and "/" not in f and "\\" not in f and not f.startswith(".") else "",
                       "size": _int(logo.get("size"), 1, 100, 30)}
    return out


def items_of(cfg):
    """The list of items of the "graphics" section, in drawing order: at most MAX_ITEMS, no id twice, a slot each
    (a slot that is missing or taken is given the lowest free one)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("items") if isinstance(cfg.get("items"), list) else []
    out, ids, slots = [], set(), set()
    for r in raw:
        it = item_of(r)
        if it is None or it["id"] in ids or len(out) >= MAX_ITEMS:
            continue
        ids.add(it["id"])
        out.append(it)
    for it in out:
        if it["slot"] and it["slot"] not in slots:
            slots.add(it["slot"])
        else:
            it["slot"] = 0
    for it in out:
        if not it["slot"]:
            it["slot"] = next(n for n in range(1, MAX_ITEMS + 1) if n not in slots)
            slots.add(it["slot"])
    return out


def stage_on(cfg):
    return not (isinstance(cfg, dict) and cfg.get("on") is False)


def stored(items, on=True):
    return {"on": bool(on), "items": [json.loads(json.dumps(i)) for i in items]}


def showing(cfg):
    """How many graphics are on the screen by the settings: the stage on and an item on, up to what one node shows."""
    try:
        return sum(1 for i in items_of(cfg) if i["on"]) if stage_on(cfg) else 0
    except Exception:
        return 0


def new_item(kind, items):
    ids = {i["id"] for i in items}
    gid = "g" + os.urandom(2).hex()
    while gid in ids:
        gid = "g" + os.urandom(2).hex()
    slots = {i["slot"] for i in items}
    slot = next(n for n in range(1, MAX_ITEMS + 1) if n not in slots)
    it = {"id": gid, "slot": slot, "kind": kind, "name": ("Text %d" if kind == "text" else "Logo %d") % slot, "on": False,
          "where": {"mode": "screen"}, "place": default_place(kind), "motion": {"kind": "none"}, "key": {"kind": "alpha"}, "fx": []}
    if kind == "text":
        it["text"] = default_text()
    else:
        it["logo"] = {"file": "", "size": 30}
    return it


# ---- changing one item (the form's Set) ------------------------------------------------------------------------------------
_PLACE_KEYS = ("x", "y", "scale", "rotate", "opacity", "mirror", "anchor")
_TEXT_KEYS = ("value", "font", "weight", "size", "colour", "align", "leading", "outline", "shadow", "plate", "lines")


def _only(patch, keys, where):
    if not isinstance(patch, dict):
        raise bad("%s must be an object" % where)
    for k in patch:
        if k not in keys:
            raise bad("%s has no setting called %r" % (where, k))


def _check_enum(v, allowed, what):
    if v not in allowed:
        raise bad("%s must be one of: %s" % (what, ", ".join(map(str, allowed))))


def _check_num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise bad("%s must be a number from %s to %s" % (what, lo, hi))


def apply_set(item, patch):
    """A copy of `item` with `patch` applied, or a GraphicsError saying what is wrong. Strict, where reading is lenient:
    a name or a value that is not understood is refused, not dropped."""
    _only(patch, ("name", "place", "text", "logo", "where", "motion", "key", "fx"), "set")
    out = json.loads(json.dumps(item))
    if "name" in patch:
        if not isinstance(patch["name"], str) or not patch["name"].strip():
            raise bad("name must be some words")
        out["name"] = _name(patch["name"], out["name"])
    if "place" in patch:
        p = patch["place"]
        _only(p, _PLACE_KEYS, "place")
        for k, (lo, hi) in {"x": (-100, 100), "y": (-100, 100), "scale": (10, 400), "rotate": (-360, 360), "opacity": (0, 100)}.items():
            if k in p:
                _check_num(p[k], lo, hi, "place." + k)
        if "mirror" in p and not isinstance(p["mirror"], bool):
            raise bad("place.mirror must be true or false")
        if "anchor" in p:
            _check_enum(p["anchor"], ANCHORS, "place.anchor")
        out["place"] = place_of(dict(out["place"], **p), out["kind"])
    if "where" in patch:
        w = patch["where"]
        if not isinstance(w, dict) or w.get("mode") not in WHERE:
            raise bad("Showing over a source, or as a source, %s" % LATER)
    if "motion" in patch:
        m = patch["motion"]
        if not isinstance(m, dict) or m.get("kind") not in MOTIONS:
            raise bad("Motion (fade, typewriter, scroll, crawl) %s" % LATER)
    if "key" in patch:
        k = patch["key"]
        if not isinstance(k, dict) or k.get("kind") not in KEYS:
            raise bad("Luma and chroma keys %s" % LATER)
    if "fx" in patch:
        if patch["fx"] not in ([], None):
            raise bad("Effects on a graphic %s" % LATER)
    if "text" in patch:
        if out["kind"] != "text":
            raise bad("this graphic is a logo, not a text")
        t = patch["text"]
        _only(t, _TEXT_KEYS, "text")
        merged = json.loads(json.dumps(out["text"]))
        if "value" in t:
            if not isinstance(t["value"], str):
                raise bad("text.value must be some words")
            merged["value"] = clean_text(t["value"])[0]
        if "font" in t:
            _check_enum(t["font"], FONT_IDS, "text.font")
            merged["font"] = t["font"]
            f = font_of(t["font"])
            if "weight" not in t:
                merged["weight"] = max(f["min"], min(f["max"], merged["weight"])) if merged["weight"] else f["default"]
        if "weight" in t:
            f = font_of(merged["font"])
            _check_num(t["weight"], f["min"], f["max"], "text.weight (for %s)" % f["name"])
            merged["weight"] = t["weight"]
        for k, (lo, hi) in {"size": (1, 60), "leading": (80, 250)}.items():
            if k in t:
                _check_num(t[k], lo, hi, "text." + k)
                merged[k] = t[k]
        if "colour" in t:
            if not isinstance(t["colour"], str) or not HEX.fullmatch(t["colour"]):
                raise bad("text.colour must be a colour like #ffcc00")
            merged["colour"] = t["colour"]
        if "align" in t:
            _check_enum(t["align"], ALIGNS, "text.align")
            merged["align"] = t["align"]
        for sect, keys in (("outline", {"on": bool, "colour": str, "width": (1, 20)}), ("shadow", {"on": bool, "colour": str, "soft": (0, 100)}),
                           ("plate", {"on": bool, "colour": str, "opacity": (0, 100), "pad": (0, 100)})):
            if sect in t:
                s = t[sect]
                _only(s, keys, "text." + sect)
                for k, v in s.items():
                    kind = keys[k]
                    if kind is bool and not isinstance(v, bool):
                        raise bad("text.%s.%s must be true or false" % (sect, k))
                    if kind is str and not (isinstance(v, str) and HEX.fullmatch(v)):
                        raise bad("text.%s.%s must be a colour like #ffcc00" % (sect, k))
                    if isinstance(kind, tuple):
                        _check_num(v, kind[0], kind[1], "text.%s.%s" % (sect, k))
                    merged[sect][k] = v
        if "lines" in t:
            if not isinstance(t["lines"], list) or len(t["lines"]) > MAX_SAVED_LINES or not all(isinstance(x, str) for x in t["lines"]):
                raise bad("text.lines must be a list of up to %d lines of words" % MAX_SAVED_LINES)
            merged["lines"] = [clean_text(x)[0] for x in t["lines"]]
            merged["lines"] = [x for x in merged["lines"] if x]
            merged["line"] = min(merged["line"], len(merged["lines"]) - 1)
        out["text"] = text_of(merged)
    if "logo" in patch:
        if out["kind"] != "logo":
            raise bad("this graphic is a text, not a logo")
        lg = patch["logo"]
        _only(lg, ("file", "size"), "logo")
        if "file" in lg:
            f = lg["file"]
            if not isinstance(f, str) or (f and (not f.endswith(".png") or "/" in f or "\\" in f or f.startswith("."))):
                raise bad("choose a picture from the graphics store")
            out["logo"]["file"] = f
        if "size" in lg:
            _check_num(lg["size"], 1, 100, "logo.size")
            out["logo"]["size"] = int(round(lg["size"]))
    return out


# ---- where and how big (the same maths draws the screen and the preview) --------------------------------------------------
def position(anchor, nudge_x, nudge_y, w, h, sw, sh):
    """(x, y) of a picture w x h on a screen sw x sh: the anchor's point of the picture on the anchor's point of the
    screen (an edge sits SAFE in from the edge), then the nudges (percent of the screen's width and height, y down),
    and the whole picture kept on the screen."""
    col, row = "lcr".index(anchor[1]) if anchor[1] in "lcr" else 1, "tmb".index(anchor[0])
    mx, my = sw * SAFE, sh * SAFE
    x = mx if col == 0 else (sw - mx - w if col == 2 else (sw - w) / 2)
    y = my if row == 0 else (sh - my - h if row == 2 else (sh - h) / 2)
    x += nudge_x / 100.0 * sw
    y += nudge_y / 100.0 * sh
    x = max(0, min(sw - w, x)) if w <= sw else 0
    y = max(0, min(sh - h, y)) if h <= sh else 0
    return int(round(x)), int(round(y))


def _hex(c):
    return c.lstrip("#")


def params_for(item, sw, sh, png=False, canvas=None):
    """The request for pvj-raster for `item` on a screen sw x sh: (parameter text, ok). The size of everything follows
    the screen's height (text) or width (logo), so the preview at a third of the size is the same picture."""
    place = item["place"]
    lines = ["kind %s" % item["kind"], "out %s" % ("png" if png else "raw"), "opacity %d" % place["opacity"],
             "limit %d %d" % (int(sw * (1 - 2 * SAFE)), int(sh * (1 - 2 * SAFE)))]
    if canvas:
        lines.append("canvas %d %d %d %d" % canvas)
    if item["kind"] == "text":
        t = item["text"]
        px = max(1.0, t["size"] / 100.0 * sh * place["scale"] / 100.0)
        lines += ["font %s" % t["font"], "weight %d" % t["weight"], "size %.3f" % px, "colour %s" % _hex(t["colour"]),
                  "align %s" % t["align"], "leading %d" % t["leading"]]
        if t["outline"]["on"]:
            lines.append("outline %.4f %s" % (t["outline"]["width"] / 100.0, _hex(t["outline"]["colour"])))
        if t["shadow"]["on"]:
            lines.append("shadow 0.04 0.04 %.4f %s 75" % (t["shadow"]["soft"] / 100.0 * 0.12, _hex(t["shadow"]["colour"])))
        if t["plate"]["on"]:
            lines.append("plate %.4f %s %d" % (t["plate"]["pad"] / 100.0, _hex(t["plate"]["colour"]), t["plate"]["opacity"]))
        lines += ["TEXT", t["value"]]
        return "\n".join(lines), None
    lg = item["logo"]
    lines += ["width %.2f" % max(1.0, lg["size"] / 100.0 * sw * place["scale"] / 100.0), "rotate %.1f" % place["rotate"],
              "mirror %d" % (1 if place["mirror"] else 0)]
    return "\n".join(lines), None


def render_key(item, sw, sh, logo_sig=None):
    """What a built picture was made from: the same key means the same picture. Not the on-switch or the name."""
    body = {"kind": item["kind"], "place": item["place"], "text": item.get("text"), "logo": item.get("logo"), "sig": logo_sig, "screen": [sw, sh]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---- the engine --------------------------------------------------------------------------------------------------------------
class Graphics:
    """Holds the graphics and puts them on the screen. `change(body)` is the one way in from the panel and from OSC;
    the pictures are made in a worker thread of this object, and `status` per graphic is what the panel shows:
    {"state": "off" | "building" | "on" | "error", "message", "missing"}."""

    def __init__(self, api, log=print, clock=time.monotonic):
        self.api = api
        self.log = log
        self.clock = clock
        self._lock = locks.make("graphics.state")
        self._apply = locks.make("graphics.apply")
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._serial = 0
        self._gen = {}              # id -> counts every change that needs a new picture (an overtaken build is dropped)
        self._work = set()          # ids to look at
        self._last = {}             # id -> time its last build began
        self._last_any = 0.0
        self._built = {}            # id -> {"key", "path", "w", "h", "x", "y", "bytes", "missing"}
        self._shown = {}            # overlay id -> (id, path, x, y): what the player was told
        self._status = {}           # id -> status dict
        self._size = None           # the screen's size at the last look
        self._previews = collections.deque()
        self._saved_at = -1e9       # when settings.json was last written by this engine, and whether a write is owed
        self._save_owed = False
        self._need_size = False     # a graphic waits for the player to say how big the screen is
        self._failed = {}           # id -> the key of a build that failed: not tried again until something changes
        self._pid = os.getpid()
        self.synchronous = False    # a test builds in the caller's thread
        self._cleanup(keep=())

    # -- what the settings say --
    def _cfg(self):
        with self.api.settings.lock:
            return json.loads(json.dumps(self.api.settings.data.get("graphics") or {}))

    def _tier(self):
        try:
            from . import hardware
            return hardware.tier(self.api.board["kind"])
        except Exception:
            return "unknown"

    def max_on(self):
        return MAX_ON.get(self._tier(), 4)

    def _player(self):
        return self.api.player

    def _screen(self):
        try:
            size = self._player().osd_size()
        except Exception:
            return None
        return tuple(size) if size else None

    def _logo_sig(self, item):
        if item["kind"] != "logo" or not item["logo"]["file"]:
            return None
        try:
            st = os.stat(os.path.join(self.api.graphics.folder, item["logo"]["file"]))
            return [st.st_ino, st.st_size, st.st_mtime_ns]
        except (OSError, ValueError):
            return None

    # -- the changes the panel and OSC make --
    def change(self, body):
        """Do what `body` says (design 8.2) and return the state. Raises GraphicsError. Several keys may be sent
        together; they are done in this order: add, set, text, line, on, move, remove, all."""
        if not isinstance(body, dict):
            raise bad("send an object")
        known = ("add", "id", "slot", "set", "remove", "on", "text", "line", "move", "dir", "all", "toggle")
        for k in body:
            if k not in known:
                raise bad("no such request: %r" % k)
        touched = set()
        with self.api.settings.lock:
            cfg = self.api.settings.data.get("graphics") or {}
            items = items_of(cfg)
            on = stage_on(cfg)
            limit = self.max_on()

            def find(key="id"):
                gid = body.get(key)
                if "slot" in body and key == "id" and gid is None:
                    n = body["slot"]
                    it = next((i for i in items if i["slot"] == n), None) if isinstance(n, int) and not isinstance(n, bool) else None
                    if it is None:
                        raise GraphicsError(404, "There is no graphic in slot %s." % n)
                    return it
                it = next((i for i in items if i["id"] == gid), None) if isinstance(gid, str) else None
                if it is None:
                    raise GraphicsError(404, "No such graphic.")
                return it
            if "add" in body:
                if body["add"] not in ("text", "logo"):
                    raise bad("add must be text or logo")
                if len(items) >= MAX_ITEMS:
                    raise bad("Up to %d graphics; remove one first." % MAX_ITEMS)
                items.append(new_item(body["add"], items))
                touched.add(items[-1]["id"])
            target_keys = [k for k in ("set", "text", "line", "on", "remove") if k in body]
            if "move" in body:
                it = find("move")
                i = items.index(it)
                j = {"up": i - 1, "down": i + 1}.get(body.get("dir"))
                if j is None:
                    raise bad("dir must be up or down")
                if 0 <= j < len(items):
                    items[i], items[j] = items[j], items[i]
                    touched.update(x["id"] for x in items)
            if target_keys:
                it = find() if ("id" in body or "slot" in body) else (items[-1] if "add" in body else None)
                if it is None:
                    raise bad("say which graphic: id or slot")
                idx = items.index(it)
                if "set" in body:
                    it = apply_set(it, body["set"])
                    items[idx] = it
                    touched.add(it["id"])
                if "text" in body:
                    if it["kind"] != "text":
                        raise bad("this graphic is a logo, not a text")
                    value = body["text"]
                    if not isinstance(value, str):
                        raise bad("text must be some words")
                    clean, note = clean_text(value)
                    it = json.loads(json.dumps(it))
                    it["text"]["value"] = clean
                    it["text"]["line"] = -1
                    items[idx] = it
                    touched.add(it["id"])
                if "line" in body:
                    if it["kind"] != "text":
                        raise bad("this graphic is a logo, not a text")
                    lines = it["text"]["lines"]
                    if not lines:
                        raise bad("This graphic has no saved lines yet.")
                    cur, v = it["text"]["line"], body["line"]
                    if v == "next":
                        n = (cur + 1) % len(lines)
                    elif v == "prev":
                        n = (cur - 1) % len(lines) if cur >= 0 else len(lines) - 1
                    elif isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(lines):
                        n = v
                    else:
                        raise bad("line must be a number from 0 to %d, next or prev" % (len(lines) - 1))
                    it = json.loads(json.dumps(it))
                    it["text"]["value"], it["text"]["line"] = lines[n], n
                    items[idx] = it
                    touched.add(it["id"])
                    if "on" not in body:
                        body = dict(body, on=True)          # tapping a line sends it to the screen at once
                if body.get("toggle") is True and "on" not in body:
                    body = dict(body, on="toggle")
                if "on" in body:
                    v = body["on"]
                    if v == "toggle":
                        v = not it["on"]
                    if not isinstance(v, bool):
                        raise bad("on must be true, false or toggle")
                    if v:
                        if it["kind"] == "text" and not it["text"]["value"]:
                            raise bad("Type a line first.")
                        if it["kind"] == "logo" and not it["logo"]["file"]:
                            raise bad("Choose a logo file first.")
                        if not it["on"] and sum(1 for i in items if i["on"] and i["id"] != it["id"]) >= limit:
                            raise bad("This node shows %d graphics at a time; hide one first." % limit)
                    if it["on"] != v:
                        it = dict(it, on=v)
                        items[idx] = it
                    touched.add(it["id"])
                    if v:
                        on = True                            # showing one brings the stage back after Hide all
                if "remove" in body:
                    if body["remove"] is not True:
                        raise bad("remove must be true")
                    items.remove(it)
                    touched.add(it["id"])
            elif "on" in body and "all" not in body and "toggle" not in body:
                raise bad("say which graphic: id or slot")
            if "all" in body:
                if not isinstance(body["all"], bool):
                    raise bad("all must be true or false")
                on = body["all"]
            self.api.settings.data["graphics"] = stored(items, on)
            self._persist()
        self._after(touched)
        return self.state()

    def _persist(self):
        """Write the settings now, or soon: a burst of pushes from OSC is one write, so the card is not worn by a ticker.
        Call with the settings' lock held. What is owed is written by the worker within SAVE_INTERVAL, and by stop()."""
        now = self.clock()
        if now - self._saved_at >= SAVE_INTERVAL:
            try:
                self.api.settings.save()
            except OSError as e:
                raise GraphicsError(500, "Could not save: %s" % (e.strerror or e))
            self._saved_at, self._save_owed = now, False
        else:
            self._save_owed = True

    def flush(self):
        """Write what is owed. Returns True if a write was owed."""
        if not self._save_owed:
            return False
        with self.api.settings.lock:
            if not self._save_owed:
                return False
            try:
                self.api.settings.save()
            except OSError as e:
                self.log("pvj-web: graphics: could not save: %s" % e)
                return True
            self._saved_at, self._save_owed = self.clock(), False
        return True

    def _after(self, touched):
        with self._lock:
            for gid in touched:
                self._gen[gid] = self._gen.get(gid, 0) + 1
            self._work.update(touched)
            self._work.add("*")            # the overlay list is looked at again: ids follow the order
        self._kick()

    def apply(self, restarted=False):
        """Make the screen match the settings (they were replaced wholesale, or the player restarted and lost what was
        drawn: restarted=True, and every overlay is drawn again from the pictures already built). Never raises."""
        if restarted:
            with self._apply:
                self._shown.clear()
        self._failed.clear()                # what failed is tried again
        with self._lock:
            self._work.add("*")
        self._kick()
        return self.state()

    def hide_all(self):
        return self.change({"all": False})

    def _kick(self):
        if self.synchronous:
            for _ in range(64):
                if self.process(final=True) is None:
                    break
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="graphics-build", daemon=True)
                self._thread.start()
        self._wake.set()

    def stop(self, wait=5.0):
        self._stop.set()
        self._wake.set()
        self.flush()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(wait)
            return not t.is_alive()
        return True

    def busy(self):
        with self._lock:
            return bool(self._work)

    def watch(self):
        """Called regularly (pvj/autostart.py's tick): build again when the screen's size changed (D90 to D92) or a
        logo's file was replaced or deleted."""
        cfg = self._cfg()
        if not showing(cfg):
            return False
        size = self._screen()
        if size is None:
            return False
        want = False
        if size != self._size or self._need_size:
            want = True
        else:
            for it in items_of(cfg):
                b = self._built.get(it["id"])
                if it["on"] and b is not None and it["kind"] == "logo" and render_key(it, size[0], size[1], self._logo_sig(it)) != b["key"]:
                    want = True
        if want:
            with self._lock:
                self._work.add("*")
                self._work.update(i["id"] for i in items_of(cfg))
            self._kick()
        return want

    # -- the worker --
    def _loop(self):
        while not self._stop.is_set():
            try:
                delay = self.process()
            except Exception as e:              # never leave the worker dead: say so and look again in a second
                self.log("pvj-web: graphics: %s" % e)
                delay = 1.0
            if self._save_owed:
                delay = SAVE_INTERVAL if delay is None else min(delay, SAVE_INTERVAL)
                if self.clock() - self._saved_at >= SAVE_INTERVAL:
                    self.flush()
            if delay is None:
                self._wake.wait(1.0)
            else:
                self._wake.wait(max(0.01, delay))
            self._wake.clear()

    def process(self, final=False):
        """Do one piece of work: build the next picture that is due and draw what is ready. Returns None when there is
        nothing to do, else the seconds to wait before the next try (0 for more now)."""
        with self._lock:
            work = set(self._work)
        if not work:
            return None

        def finished():
            with self._lock:
                self._work -= work           # not clear(): a change that came in meanwhile is for the next round

        cfg = self._cfg()
        items = items_of(cfg)
        known = {i["id"] for i in items}
        # what is gone leaves the screen
        for gid in [g for g in list(self._built) if g not in known]:
            self._drop(gid)
        for gid in [g for g in list(self._status) if g not in known]:
            self._status.pop(gid, None)
        on = stage_on(cfg)
        limit = self.max_on()
        wanted, count = [], 0
        for idx, it in enumerate(items):
            if on and it["on"] and count < limit:
                wanted.append((OVERLAY_BASE + idx, it))
                count += 1
            else:
                if it["id"] in self._built:
                    self._drop(it["id"])
                self._status[it["id"]] = {"state": "off", "message": "", "missing": 0}
        size = self._screen()
        self._need_size = bool(wanted) and size is None
        if wanted and size is None:
            for _, it in wanted:
                self._status[it["id"]] = {"state": "error", "message": "The player has no screen size yet; this is drawn when it has one.", "missing": 0}
            finished()
            self._draw(items, wanted)
            return None
        if size is not None:
            self._size = size           # (a picture's key holds the screen's size: a new size makes every key stale)
        # the first picture that needs building
        delay = None
        now = self.clock()
        for _, it in wanted:
            key = render_key(it, size[0], size[1], self._logo_sig(it))
            b = self._built.get(it["id"])
            if b is not None and b["key"] == key:
                continue
            if self._failed.get(it["id"]) == key:
                continue                # this exact picture failed: said in the status, not tried again until something changes
            due = max(self._last.get(it["id"], -1e9) + PER_ITEM_INTERVAL, self._last_any + ALL_INTERVAL)
            if due > now and not final:
                delay = due - now if delay is None else min(delay, due - now)
                self._status.setdefault(it["id"], {"state": "building", "message": "", "missing": 0})
                continue
            self._last[it["id"]] = now
            self._last_any = now
            self._build(it, key, size, wanted)
            return 0.0
        if delay is not None:
            return delay
        finished()
        not_drawn = self._draw(items, wanted)
        for _, it in wanted:
            b = self._built.get(it["id"])
            if b is not None and it["id"] not in not_drawn and it["id"] not in self._failed:
                self._status[it["id"]] = {"state": "on", "message": "", "missing": b.get("missing", 0)}
        return None

    def _build(self, it, key, size, wanted):
        gid = it["id"]
        with self._lock:
            gen = self._gen.get(gid, 0)
        self._status[gid] = {"state": "building", "message": "", "missing": 0}
        try:
            payload = b""
            if it["kind"] == "logo":
                name = it["logo"]["file"]
                if not name or self._logo_sig(it) is None:
                    raise GraphicsError(404, "%s is not in the graphics store any more." % (name or "The logo"))
                with open(os.path.join(self.api.graphics.folder, name), "rb") as f:
                    payload = f.read(raster_mod.MAX_PICTURE_BYTES + 1)
            else:
                if not it["text"]["value"]:
                    raise GraphicsError(400, "Type a line first.")
            params, _ = params_for(it, size[0], size[1])
            done = self.api.graphics.client.graphic(params, payload)
        except GraphicsError as e:
            self._status[gid] = {"state": "error", "message": e.message, "missing": 0}
            self._drop(gid)
            self._failed[gid] = key
            return
        except raster_mod.RasterError as e:
            self._status[gid] = {"state": "error", "message": e.message, "missing": 0}
            self._drop(gid)
            self._failed[gid] = key
            return
        except Exception as e:                      # never leave the worker dead
            self._status[gid] = {"state": "error", "message": "Could not draw this: %s" % e, "missing": 0}
            self.log("pvj-web: graphics: %s" % e)
            self._failed[gid] = key
            return
        with self._lock:
            if gen != self._gen.get(gid, 0):
                return                              # a newer change overtook this one: it is built next
        used = sum(b["bytes"] for g, b in self._built.items() if g != gid)
        need = done["w"] * done["h"] * 4
        if used + need > MEMORY_BUDGET:
            self._status[gid] = {"state": "error", "missing": 0,
                                 "message": "All the graphics together would use more than %d MB (this one needs %.1f MB); hide one or make this smaller."
                                            % (MEMORY_BUDGET // (1024 * 1024), need / 1048576.0)}
            self._drop(gid)
            self._failed[gid] = key
            return
        rundir = getattr(self._player(), "rundir", None)
        if not rundir:
            self._status[gid] = {"state": "error", "message": "The player has no folder to draw from.", "missing": 0}
            return
        with self._lock:
            self._serial += 1
            serial = self._serial
        path = os.path.join(rundir, "graphic-%s-%d-%d.bgra" % (gid, self._pid, serial))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(done["data"])
        except OSError as e:
            _unlink(path)
            self._status[gid] = {"state": "error", "message": "Could not write the picture: %s" % (e.strerror or e), "missing": 0}
            return
        x, y = position(it["place"]["anchor"], it["place"]["x"], it["place"]["y"], done["w"], done["h"], size[0], size[1])
        with self._apply:
            with self._lock:
                if gen != self._gen.get(gid, 0):
                    _unlink(path)
                    return
            old = self._built.get(gid)
            self._built[gid] = {"key": key, "path": path, "w": done["w"], "h": done["h"], "x": x, "y": y, "bytes": need,
                                "missing": done.get("missing", 0), "drawn": False}
            if old is not None:
                self._retire(old["path"])
        self._status[gid] = {"state": "on", "message": "", "missing": done.get("missing", 0)}

    def _retire(self, path):
        """A picture's file goes once nothing draws it: after the overlay that used it has been replaced."""
        if path and not any(s[1] == path for s in self._shown.values()) and not any(b["path"] == path for b in self._built.values()):
            _unlink(path)

    def _drop(self, gid):
        with self._apply:
            b = self._built.pop(gid, None)
            for oid, s in list(self._shown.items()):
                if s[0] == gid:
                    try:
                        self._player().overlay_remove(oid)
                    except Exception:
                        pass
                    self._shown.pop(oid, None)
            if b is not None:
                _unlink(b["path"])

    def _draw(self, items, wanted):
        """Tell the player what to show: each wanted graphic whose picture is ready at its id, nothing at the other ids."""
        not_drawn = set()
        with self._apply:
            want_ids = {}
            for oid, it in wanted:
                b = self._built.get(it["id"])
                if b is not None:
                    want_ids[oid] = (it["id"], b)
            for oid in list(self._shown):
                s = self._shown[oid]
                w = want_ids.get(oid)
                if w is None or w[0] != s[0]:
                    try:
                        self._player().overlay_remove(oid)
                    except Exception:
                        pass
                    self._shown.pop(oid, None)
            for oid, (gid, b) in sorted(want_ids.items()):
                s = (gid, b["path"], b["x"], b["y"])
                if self._shown.get(oid) == s:
                    continue
                try:
                    self._player().overlay_part(oid, b["path"], b["x"], b["y"], 0, b["w"], b["h"], b["w"] * 4)
                except Exception as e:      # the player is down or restarting: it gets them when it comes back (apply)
                    self._status[gid] = {"state": "error", "message": "The player did not take this (%s); it is drawn when the player restarts." % e, "missing": 0}
                    not_drawn.add(gid)
                    continue
                old = self._shown.get(oid)
                self._shown[oid] = s
                if old is not None:
                    self._retire(old[1])
            self._cleanup(keep={b["path"] for b in self._built.values()})
        return not_drawn

    def _cleanup(self, keep):
        """Remove the pictures this process wrote that nothing uses, and every one an earlier run left."""
        try:
            rundir = getattr(self._player(), "rundir", None)
        except Exception:
            return
        if not rundir:
            return
        try:
            names = os.listdir(rundir)
        except OSError:
            return
        shown = {s[1] for s in self._shown.values()}
        with self._lock:
            newest = self._serial
        for n in names:
            m = re.fullmatch(FILE_RE, n)
            if not m:
                continue
            full = os.path.join(rundir, n)
            if full in keep or full in shown:
                continue
            if int(m.group(2)) == self._pid and int(m.group(3)) > newest:
                continue                    # being written by a build that began after this look
            try:
                if os.path.isfile(full) and not os.path.islink(full):
                    os.unlink(full)
            except OSError:
                pass

    # -- what the panel is told --
    def state(self):
        cfg = self._cfg()
        items = items_of(cfg)
        store = self.api.graphics
        have = {f["name"] for f in store.files()}
        out = []
        for it in items:
            it = json.loads(json.dumps(it))
            st = dict(self._status.get(it["id"]) or {"state": "off", "message": "", "missing": 0})
            shown = it["on"] and stage_on(cfg)
            if shown and st["state"] == "off":
                st = {"state": "building", "message": "", "missing": 0}         # switched on, and the worker has not got to it yet
            elif not shown:
                st = {"state": "off", "message": "", "missing": 0}
            if it["kind"] == "logo" and it["logo"]["file"] and it["logo"]["file"] not in have:
                it["logo"]["missing"] = True
                if it["on"]:
                    st = {"state": "error", "message": "%s is not in the graphics store." % it["logo"]["file"], "missing": 0}
            it["status"] = st
            out.append(it)
        size = self._screen() or self._size
        used = sum(b["bytes"] for b in self._built.values())
        return {"on": stage_on(cfg), "items": out, "showing": showing(cfg), "max_items": MAX_ITEMS, "max_on": self.max_on(), "tier": self._tier(),
                "motions": list(MOTIONS), "keys": list(KEYS), "effects": list(EFFECTS), "where": list(WHERE), "later": LATER,
                "fonts": [dict(f) for f in FONTS], "files": store.files(), "available": store.client.available(),
                "screen": {"width": size[0], "height": size[1]} if size else None,
                "limits": {"text": MAX_TEXT, "lines": MAX_TEXT_LINES, "saved_lines": MAX_SAVED_LINES, "memory": MEMORY_BUDGET, "used": used},
                "anchors": list(ANCHORS)}

    def preview(self, ident, text=None):
        """The PNG of one graphic as it will look, on a screen of the same shape at most PREVIEW_SIDE wide, made by the
        same helper and the same maths as the screen. At most PREVIEWS_PER_SECOND a second. `text` (a text graphic
        only) is a line as typed and not yet sent: it is cleaned like any other and drawn instead of the saved one,
        and kept nowhere."""
        now = self.clock()
        with self._lock:
            while self._previews and now - self._previews[0] > 1.0:
                self._previews.popleft()
            if len(self._previews) >= PREVIEWS_PER_SECOND:
                raise GraphicsError(429, "Previews are limited to %d a second; wait a moment." % PREVIEWS_PER_SECOND)
            self._previews.append(now)
        items = items_of(self._cfg())
        it = next((i for i in items if i["id"] == ident), None)
        if it is None:
            raise GraphicsError(404, "No such graphic.")
        if text is not None and it["kind"] == "text":
            it = json.loads(json.dumps(it))
            it["text"]["value"] = clean_text(text)[0]
        sw, sh = self._screen() or self._size or (1920, 1080)
        k = min(1.0, PREVIEW_SIDE / float(sw))
        pw, ph = max(16, int(round(sw * k))), max(16, int(round(sh * k)))
        payload = b""
        if it["kind"] == "text":
            if not it["text"]["value"]:
                raise GraphicsError(409, "Type a line to see it.")
        else:
            name = it["logo"]["file"]
            if not name or self._logo_sig(it) is None:
                raise GraphicsError(404, "Choose a logo file to see it.")
            with open(os.path.join(self.api.graphics.folder, name), "rb") as f:
                payload = f.read(raster_mod.MAX_PICTURE_BYTES + 1)
        client = self.api.graphics.client
        try:
            first = client.graphic(params_for(it, pw, ph)[0], payload)
            x, y = position(it["place"]["anchor"], it["place"]["x"], it["place"]["y"], first["w"], first["h"], pw, ph)
            return client.graphic(params_for(it, pw, ph, png=True, canvas=(pw, ph, x, y))[0], payload)["data"]
        except raster_mod.RasterError as e:
            raise GraphicsError(e.status, e.message)
