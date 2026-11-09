# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Text and logos on the screen (D129, slices 2 and 3 of design/text-and-logo-sources_2026-11-08.md).

A graphic is a text or a logo that the node draws over the picture. pvj-raster (render/raster/, its own process with its
own limits) makes each graphic into one small premultiplied picture; what draws it depends on the player:

* the New renderer (pvj-render 0.11.0 on, `Player.graphics_stage()`; Pi 4, Pi 5, desktop): its GPU stage, one slot a
  graphic in the order of the list (a later one over an earlier). The picture is loaded once; the fade, the typewriter,
  the scroll and the crawl are worked out by the renderer every frame, so nothing is sent while they run;
* classic mpv (a Pi 3, or a node that fell back): `overlay-add` (ids OVERLAY_BASE to OVERLAY_BASE + 7), still. mpv's
  osd-overlay cannot animate (D129 addendum, slice 2's spike), so the fade and the typewriter are stepped here, about
  STEP_HZ times a second, by a thread that has a finite job: it stops when a motion is done or the graphic is hidden.
  Scroll and crawl need the GPU stage and are refused in words on this path.

A node that falls back to classic mid-show keeps showing the graphics, still (the pictures are the same files).
Keys and effects, and drawing over or as a source, come in later slices; this one says so in words where the panel asks.

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

The stepping thread takes `_apply` for each step (it draws into the player) and `_lock` inside it, as a build does.
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

OVERLAY_BASE = 20               # ids 20 to 27; the masks are 30 and the pairing codes 40 to 43, above these, and transitions 63 (design 3.1)
MAX_ITEMS = 8
MAX_ON = {"lean": 4, "standard": 4, "full": 8, "unknown": 4}      # on at once, by tier (design 3.2)
MAX_TEXT = 280
MAX_TEXT_LINES = 6
MAX_SAVED_LINES = 20
MEMORY_BUDGET = 48 * 1024 * 1024        # all the pictures that are on, together (design 3.2)
SAFE = 0.05                             # an edge anchor sits this far in from the edge of the screen
STEP_HZ = 12                            # a stepped fade or typewriter on the classic path (mpv cannot animate an overlay)
STEP_MAX_BYTES = 6 * 1024 * 1024        # a picture larger than this is shown at once, not stepped (each step writes the whole picture)
STEP_TOTAL_BYTES = 12 * 1024 * 1024     # and the pictures being stepped at once, together
MOTION_SECONDS_MAX = 30
PER_ITEM_INTERVAL = 0.2                 # at most 5 builds a second for one graphic
ALL_INTERVAL = 0.05                     # and 20 a second in all
SAVE_INTERVAL = 0.5                     # settings.json is written at most twice a second however fast OSC pushes lines
PREVIEWS_PER_SECOND = 5
PREVIEW_SIDE = 640                      # the preview's width at most
ANCHORS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
ALIGNS = ("left", "center", "right")
FILE_RE = r"graphic-([a-z0-9]+)-(\d+)-(\d+)\.bgra"

# What this slice can do. The panel greys out what is not here, with the reason (design 3 and 11).
MOTION_KINDS = ("none", "fade", "typewriter", "scroll", "crawl")
MOTIONS_OVERLAY = ("none", "fade", "typewriter")        # classic mpv: still, and stepped
MOTIONS = MOTION_KINDS                                  # the New renderer's GPU stage
KEYS = ("alpha",)
EFFECTS = ()
WHERE = ("screen",)
LATER = "comes in a later release; for now a graphic is drawn over the screen"
OVERLAY_NOTE = "This node draws graphics without the New renderer: only None, Fade and Typewriter, with steps."
NEEDS_RENDERER = "Scroll and crawl need the New renderer. " + OVERLAY_NOTE


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


def default_motion():
    return {"kind": "none", "seconds": 1.0, "out": 0.5, "speed": 12, "loop": False}


def motion_of(raw):
    """A motion section as stored: every key present, every value in range. seconds is the fade in, or the whole typing;
    out is the fade out (the fade only); speed is how much of the screen's height (scroll) or width (crawl) goes by in a
    second, in percent; loop sends a scroll or a crawl round again."""
    raw = raw if isinstance(raw, dict) else {}
    d = default_motion()
    return {"kind": raw.get("kind") if raw.get("kind") in MOTION_KINDS else "none",
            "seconds": round(_num(raw.get("seconds"), 0.1, MOTION_SECONDS_MAX, d["seconds"]), 2),
            "out": round(_num(raw.get("out"), 0, MOTION_SECONDS_MAX, d["out"]), 2),
            "speed": _int(raw.get("speed"), 2, 100, d["speed"]), "loop": _bool(raw.get("loop"), False)}


def motion_for(item, mode):
    """The motion a graphic is drawn with on a node whose drawing is `mode` ("gpu" or "overlay"): what was chosen if this
    node can do it, else none (the graphic is still there, still)."""
    kind = item["motion"]["kind"]
    return kind if kind in (MOTIONS if mode == "gpu" else MOTIONS_OVERLAY) else "none"


def lines_of(item):
    """Lines of text in a picture (the typewriter goes line after line); a logo is one."""
    if item["kind"] != "text":
        return 1
    return max(1, min(MAX_TEXT_LINES, item["text"]["value"].count("\n") + 1))


def place_of(raw, kind):
    raw = raw if isinstance(raw, dict) else {}
    d = default_place(kind)
    return {"x": round(_num(raw.get("x"), -100, 100, 0), 1), "y": round(_num(raw.get("y"), -100, 100, 0), 1),
            "scale": _int(raw.get("scale"), 10, 400, 100), "rotate": round(_num(raw.get("rotate"), -360, 360, 0), 1),
            "opacity": _int(raw.get("opacity"), 0, 100, 100), "mirror": _bool(raw.get("mirror"), False),
            "anchor": raw.get("anchor") if raw.get("anchor") in ANCHORS else d["anchor"]}


def item_of(raw):
    """One item as stored, or None for something that is not one. This release draws everything over the screen and
    with the alpha the picture has: what a later release wrote for another place or key is read as that. A motion is kept
    as chosen, even one this node cannot draw (it is drawn still there, and says so)."""
    if not isinstance(raw, dict) or raw.get("kind") not in ("text", "logo"):
        return None
    kind = raw["kind"]
    gid = raw.get("id") if isinstance(raw.get("id"), str) and re.fullmatch(r"g[0-9a-f]{4,12}", raw["id"]) else None
    if gid is None:
        return None
    out = {"id": gid, "slot": _int(raw.get("slot"), 1, MAX_ITEMS, 0), "kind": kind,
           "name": _name(raw.get("name"), "Text" if kind == "text" else "Logo"), "on": raw.get("on") is True,
           "where": {"mode": "screen"}, "place": place_of(raw.get("place"), kind),
           "motion": motion_of(raw.get("motion")), "key": {"kind": "alpha"}, "fx": []}
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
          "where": {"mode": "screen"}, "place": default_place(kind), "motion": default_motion(), "key": {"kind": "alpha"}, "fx": []}
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


def apply_set(item, patch, motions=None):
    """A copy of `item` with `patch` applied, or a GraphicsError saying what is wrong. Strict, where reading is lenient:
    a name or a value that is not understood is refused, not dropped. `motions`: the kinds of motion this node can draw
    (the GPU stage's, or the overlay path's three); None: any that exists."""
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
        _only(m, ("kind", "seconds", "out", "speed", "loop"), "motion")
        if "kind" in m:
            _check_enum(m["kind"], MOTION_KINDS, "motion.kind")
            if motions is not None and m["kind"] not in motions:
                raise bad(NEEDS_RENDERER)
        for k, (lo, hi) in {"seconds": (0.1, MOTION_SECONDS_MAX), "out": (0, MOTION_SECONDS_MAX), "speed": (2, 100)}.items():
            if k in m:
                _check_num(m[k], lo, hi, "motion." + k)
        if "loop" in m and not isinstance(m["loop"], bool):
            raise bad("motion.loop must be true or false")
        out["motion"] = motion_of(dict(out["motion"], **m))
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


def params_for(item, sw, sh, png=False, canvas=None, ticker=0):
    """The request for pvj-raster for `item` on a screen sw x sh: (parameter text, ok). The size of everything follows
    the screen's height (text) or width (logo), so the preview at a third of the size is the same picture. `ticker`: the
    widest picture the GPU can hold, for a text that crawls: one line, as wide as it needs to be, and a text that would
    have to be made smaller to fit is refused (in words) instead."""
    place = item["place"]
    lines = ["kind %s" % item["kind"], "out %s" % ("png" if png else "raw"), "opacity %d" % place["opacity"],
             "limit %d %d" % (ticker or int(sw * (1 - 2 * SAFE)), int(sh * (1 - 2 * SAFE)))]
    if ticker and item["kind"] == "text":
        lines.append("nofit 1")
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
        value = t["value"]
        if ticker:
            value = "  ".join(x for x in value.split("\n") if x)
        lines += ["TEXT", value]
        return "\n".join(lines), None
    lg = item["logo"]
    lines += ["width %.2f" % max(1.0, lg["size"] / 100.0 * sw * place["scale"] / 100.0), "rotate %.1f" % place["rotate"],
              "mirror %d" % (1 if place["mirror"] else 0)]
    return "\n".join(lines), None


def render_key(item, sw, sh, logo_sig=None, ticker=0):
    """What a built picture was made from: the same key means the same picture. Not the on-switch or the name, and not
    the motion (the speed and the times are the player's), except that a crawling text is made as one wide line."""
    body = {"kind": item["kind"], "place": item["place"], "text": item.get("text"), "logo": item.get("logo"), "sig": logo_sig, "screen": [sw, sh]}
    if ticker:
        body["ticker"] = ticker
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---- the stepped motions' pictures (the classic path; plain bytes work, no imaging library on the node) ------------------------------------
_INK = bytes(0 if v < 12 else 1 for v in range(256))


def _faded(base, level):
    """The picture (premultiplied B G R A) at `level` of its strength: every byte scaled, which keeps it premultiplied."""
    level = max(0.0, min(1.0, level))
    return base.translate(bytes(min(255, int(v * level + 0.5)) for v in range(256)))


def _ink_extents(base, w, h, lines):
    """(first, last + 1) column with ink, for each of `lines` equal bands of rows (a band with none: 0, 0)."""
    stride, out = w * 4, []
    for li in range(lines):
        lo, hi = w, -1
        for y in range(h * li // lines, h * (li + 1) // lines):
            a = base[y * stride + 3:(y + 1) * stride:4].translate(_INK)
            first = a.find(1)
            if first >= 0:
                lo, hi = min(lo, first), max(hi, a.rfind(1))
        out.append((lo, hi + 1) if hi >= lo else (0, 0))
    return out


def _typed(base, w, h, lines, ext, p):
    """The picture with only the first `p` of its ink showing: line after line, each from its left edge, at one pace
    along the ink (so lines share the time by their widths). What is not shown is transparent."""
    stride = w * 4
    out = bytearray(base)
    left = p * sum(max(0, e1 - e0) for e0, e1 in ext)
    for li in range(lines):
        x0, x1 = ext[li]
        if x1 <= x0:
            continue
        if left >= x1 - x0:
            left -= x1 - x0
            continue
        y0, y1 = h * li // lines, h * (li + 1) // lines
        if left <= 0:
            out[y0 * stride:y1 * stride] = bytes((y1 - y0) * stride)
        else:
            edge = x0 + int(left)
            zero = bytes(stride - edge * 4)
            for y in range(y0, y1):
                o = y * stride
                out[o + edge * 4:o + stride] = zero
            left = 0
    return out


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
        self._held = False          # a pairing code or the PIN is on the screen: the graphics are held back (design 3.1)
        self._mode_drawn = None     # "gpu" or "overlay": what the screen was last drawn with
        self._stage_last = None     # what the player last said of its graphics stage (a lost answer keeps it)
        self._hold_keep = set()     # ids that were on the screen when a code came up: they come back still
        self._mode_now = "overlay"  # what the worker found at the top of its last pass
        self._gpu = {}              # slot -> {"gid", "token", "cfg", "on"}: what the renderer's stage holds
        self._fading = {}           # slot -> (gid, due): a fade out the renderer is running; the picture is cleared when it is done
        self._drawn = {}            # overlay id -> (id, token, x, y): the picture the classic path was last told to show
        self._once = set()          # ids drawn and not taken off since: a draw of one that is not here is a show (its motion plays)
        self._replay = set()        # ids whose motion plays again at their next draw (a new line, or the Replay button)
        self._tasks = {}            # id -> the stepped motion that is running on the classic path
        self._timers = []           # (due, kind, args): in _lock
        self._ticker = None         # the thread that steps the motions and runs the timers
        self._tick_wake = threading.Event()
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

    def _stage(self):
        """{"slots", "side"} when the player has the renderer's graphics stage, else None (classic mpv, a renderer before
        0.11.0). A player that does not answer gives what it last said."""
        ask = getattr(self._player(), "graphics_stage", None)
        if ask is None:
            return None
        try:
            stage = ask()
        except Exception:
            return self._stage_last         # no reply (a busy or restarting player) is not a fall back: what it said last stands
        self._stage_last = stage
        return stage

    def mode(self):
        """"gpu" where the renderer's stage draws the graphics, "overlay" where mpv's overlays do."""
        return "gpu" if self._stage() else "overlay"

    def motions(self):
        return list(MOTIONS if self.mode() == "gpu" else MOTIONS_OVERLAY)

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
        known = ("add", "id", "slot", "set", "remove", "on", "text", "line", "move", "dir", "all", "toggle", "replay")
        for k in body:
            if k not in known:
                raise bad("no such request: %r" % k)
        touched, soft, replay = set(), set(), set()
        allowed = self.motions() if isinstance(body.get("set"), dict) and "motion" in body["set"] else None      # (asks the player: not under the settings' lock)
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
            target_keys = [k for k in ("set", "text", "line", "on", "remove", "replay") if k in body]
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
                    it = apply_set(it, body["set"], allowed)
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
                    changed = it["text"]["value"] != clean
                    it["text"]["value"] = clean
                    it["text"]["line"] = -1
                    items[idx] = it
                    touched.add(it["id"])
                    if changed and it["on"]:
                        replay.add(it["id"])            # a new line on the screen plays its motion again
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
                    changed = it["text"]["value"] != lines[n]
                    it["text"]["value"], it["text"]["line"] = lines[n], n
                    items[idx] = it
                    touched.add(it["id"])
                    if changed and it["on"]:
                        replay.add(it["id"])
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
                if "replay" in body:
                    if body["replay"] is not True:
                        raise bad("replay must be true")
                    if not it["on"] or not on:
                        raise bad("Show it first; then Replay plays its motion again.")
                    replay.add(it["id"])
                    soft.add(it["id"])
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
        with self._lock:
            self._replay |= replay
        self._after(touched, soft)
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

    def _after(self, touched, soft=()):
        with self._lock:
            for gid in touched:
                self._gen[gid] = self._gen.get(gid, 0) + 1
            self._work.update(touched)
            self._work.update(soft)         # (looked at again, but no new picture is owed)
            self._work.add("*")            # the overlay list is looked at again: ids follow the order
        self._kick()

    def apply(self, restarted=False):
        """Make the screen match the settings (they were replaced wholesale, or the player restarted and lost what was
        drawn: restarted=True, and every overlay is drawn again from the pictures already built). Never raises."""
        if restarted:
            with self._apply:
                self._shown.clear()         # a new player has nothing: the pictures are drawn again (on classic, still; on the GPU path a
                                            # graphic's entrance plays, as the screen came from nothing)
                self._drawn.clear()
                self._gpu.clear()
                self._fading.clear()
                self._tasks.clear()
        self._failed.clear()                # what failed is tried again
        with self._lock:
            self._work.add("*")
        self._kick()
        return self.state()

    def _access_up(self):
        """True while the PIN or a pairing code is on the display. The PIN text is drawn by the OSD before any overlay, so
        the only way to keep it readable is to take the graphics off for that time (design 3.1: codes stay on top)."""
        try:
            return bool(self.api.access_on_screen())
        except Exception:
            return True                 # when unsure, hold back: a code that cannot be read is worse than a late logo

    def controllers_may_push(self):
        """The Owner's switch "Controllers may push text": OSC (and any other controller) may set a line of text only
        while it is on. Off unless switched on. The panel's own pushes, by an Operator or the Owner, never ask it."""
        control = self.api.settings.data.get("control")
        return isinstance(control, dict) and control.get("graphics") is True

    def set_controllers(self, allow):
        if not isinstance(allow, bool):
            raise bad("allow must be true or false")
        settings = self.api.settings
        with settings.lock:
            control = settings.data.setdefault("control", {})
            if allow:
                control["graphics"] = True
            else:
                control.pop("graphics", None)
            settings.save()
        return {"allow": allow}

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
        self._tick_wake.set()
        self.flush()
        done = True
        for t in (self._thread, self._ticker):
            if t is not None and t is not threading.current_thread():
                t.join(wait)
                done = done and not t.is_alive()
        return done

    def busy(self):
        with self._lock:
            return bool(self._work)

    def watch(self):
        """Called regularly (pvj/autostart.py's tick): build again when the screen's size changed (D90 to D92) or a
        logo's file was replaced or deleted."""
        cfg = self._cfg()
        held = self._access_up()
        if held != self._held:                  # a code came on the screen or went: draw again (hide or bring back)
            with self._lock:
                self._work.add("*")
            self._kick()
            return True
        if not showing(cfg):
            return False
        size = self._screen()
        if size is None:
            return False
        want = False
        if size != self._size or self._need_size:
            want = True
        elif self._mode_drawn is not None and self.mode() != self._mode_drawn:
            want = True                                 # the renderer went (or came): draw again with what the player has now
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
        held = self._held = self._access_up()
        limit = self.max_on()
        stage = self._stage()
        mode = self._mode_now = "gpu" if stage else "overlay"
        wanted, count = [], 0
        for idx, it in enumerate(items):
            if on and it["on"] and count < limit:
                wanted.append((OVERLAY_BASE + idx, it))
                count += 1
            else:
                if it["id"] in self._built:
                    self._drop(it["id"], it if on else None)       # (Hide all cuts; a graphic switched off fades out if it has chosen to)
                self._status[it["id"]] = {"state": "off", "message": "", "missing": 0}
        if held and wanted:
            # a code is on the screen: nothing of ours is drawn (the pictures built stay, ready to come back)
            for _, it in wanted:
                self._status[it["id"]] = {"state": "held", "message": "Held back while a pairing code is on the screen.", "missing": 0}
            finished()
            self._draw(items, [], held=True)
            return None
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
            ticker = stage["side"] if stage and it["kind"] == "text" and motion_for(it, mode) == "crawl" else 0
            key = render_key(it, size[0], size[1], self._logo_sig(it), ticker)
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
            self._build(it, key, size, wanted, ticker)
            return 0.0
        if delay is not None:
            return delay
        finished()
        not_drawn = self._draw(items, wanted)
        for _, it in wanted:
            b = self._built.get(it["id"])
            if b is not None and it["id"] not in not_drawn and it["id"] not in self._failed:
                still = it["motion"]["kind"] in ("scroll", "crawl") and mode != "gpu"
                self._status[it["id"]] = {"state": "on", "message": "Drawn still: scroll and crawl need the New renderer." if still else "", "missing": b.get("missing", 0)}
        return None

    def _build(self, it, key, size, wanted, ticker=0):
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
            params, _ = params_for(it, size[0], size[1], ticker=ticker)
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
        stage = self._stage() if self._mode_now == "gpu" else None
        if stage and (done["w"] > stage["side"] or done["h"] > stage["side"]):
            self._status[gid] = {"state": "error", "missing": 0,
                                 "message": "Too big for this node's graphics chip (%d pixels on a side at most): make it smaller." % stage["side"]}
            self._drop(gid)
            self._failed[gid] = key
            return
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
            self._failed[gid] = key                 # not tried again until something changes (a restarted player clears this: apply)
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
            self._failed[gid] = key
            return
        x, y = position(it["place"]["anchor"], it["place"]["x"], it["place"]["y"], done["w"], done["h"], size[0], size[1])
        with self._apply:
            with self._lock:
                if gen != self._gen.get(gid, 0):
                    _unlink(path)
                    return
            old = self._built.get(gid)
            self._built[gid] = {"key": key, "path": path, "w": done["w"], "h": done["h"], "x": x, "y": y, "bytes": need,
                                "missing": done.get("missing", 0), "drawn": False, "lines": 1 if ticker else lines_of(it), "token": serial}
            if old is not None:
                self._retire(old["path"])
        self._status[gid] = {"state": "on", "message": "", "missing": done.get("missing", 0)}

    def _retire(self, path):
        """A picture's file goes once nothing draws it: after the overlay that used it has been replaced."""
        if path and not any(s[1] == path for s in self._shown.values()) and not any(b["path"] == path for b in self._built.values()):
            _unlink(path)

    def _slot_of(self, gid):
        return next((n for n, st in self._gpu.items() if st["gid"] == gid), None)

    def _is_fading(self, gid):
        t = self._tasks.get(gid)
        return (t is not None and t["kind"] == "out") or any(f[0] == gid for f in self._fading.values())

    def _drop(self, gid, item=None):
        """Take a graphic off the screen and forget its picture. With `item` (it was switched off, not removed, and the
        stage is on) a fade out that it has chosen plays first, and the picture goes when that is done."""
        with self._apply:
            b = self._built.get(gid)
            if item is not None and b is not None and self._fade_out(gid, item, b):
                return
            self._built.pop(gid, None)
            self._drop_drawn(gid)
            if b is not None:
                _unlink(b["path"])

    def _drop_drawn(self, gid):
        """(under _apply) Off the screen at once: its overlay or its slot, its stepping, its replay."""
        self._tasks.pop(gid, None)
        self._once.discard(gid)
        self._hold_keep.discard(gid)
        self._replay.discard(gid)
        for oid, s in list(self._shown.items()):
            if s[0] == gid:
                try:
                    self._player().overlay_remove(oid)
                except Exception:
                    pass
                self._shown.pop(oid, None)
                self._drawn.pop(oid, None)
                self._retire(s[1])
        for n, st in list(self._gpu.items()):
            if st["gid"] == gid:
                self._gpu_clear(n)

    def _gpu_clear(self, n):
        self._gpu.pop(n, None)
        self._fading.pop(n, None)
        try:
            self._player().graphic("clear", n)
        except Exception:
            pass                            # a player that is down has lost it

    def _fade_out(self, gid, item, b):
        """(under _apply) Begin the fade out `item` has chosen, if there is one and the graphic is on the screen. True
        when the picture has to stay for it; asking again while it runs also says True."""
        if self._is_fading(gid):
            return True
        m = item["motion"]
        if self._mode_drawn is None or motion_for(item, self._mode_drawn) != "fade" or m["out"] <= 0:
            return False
        n = self._slot_of(gid)
        if n is not None:
            if not self._gpu[n]["on"]:
                return False
            try:
                self._player().graphic("hide", n)
            except Exception:
                return False
            self._gpu[n]["on"] = False
            due = self.clock() + m["out"] + 0.25
            self._fading[n] = (gid, due)
            self._timer(due, "slot", n, gid)
            return True
        oid = next((o for o, s in self._shown.items() if s[0] == gid), None)
        return oid is not None and self._start_task(gid, "out", oid, b, m["out"])

    # -- drawing: what the player is told ------------------------------------------------------------------------------
    def _draw(self, items, wanted, held=False):
        """Tell the player what to show: each wanted graphic whose picture is ready, nothing at the other places. Where the
        player has the renderer's stage that is a slot each; on classic mpv an overlay each."""
        mode = self._mode_now
        with self._apply:
            if held:
                self._hold_keep |= self._once       # what was on the screen comes back as it was: no motion plays again
            if mode != self._mode_drawn:
                self._switch(mode)
            not_drawn = self._draw_gpu(wanted) if mode == "gpu" else self._draw_overlay(wanted)
            if not held:
                self._hold_keep.clear()
            self._cleanup(keep={b["path"] for b in self._built.values()})
        return not_drawn

    def _switch(self, mode):
        """(under _apply) The player draws another way now (the renderer went, or came back): what the old way showed is
        forgotten and the new way draws everything again from the same picture files. _once keeps what had been shown, so
        a node that falls back to classic mid-show shows the graphics still, not fading in again; when the renderer comes
        back a graphic's motion starts as it does when it is shown."""
        if self._mode_drawn == "overlay":
            for oid in list(self._shown):
                try:
                    self._player().overlay_remove(oid)
                except Exception:
                    pass
        for n in list(self._gpu):
            self._gpu_clear(n)              # (best effort: if the renderer is still there it must not go on drawing under the overlays)
        self._shown.clear()
        self._drawn.clear()
        self._tasks.clear()
        self._gpu.clear()
        self._fading.clear()
        with self._lock:
            self._timers = []
        self._mode_drawn = mode

    def _refused(self, gid, e):
        words = str(e)[5:] if str(e).startswith("mpv: ") else str(e)
        if words in ("invalid parameter", ""):
            words = "the renderer did not take the picture"
        self._status[gid] = {"state": "error", "message": "The player did not take this (%s); it is drawn when the player restarts." % words, "missing": 0}

    def _draw_gpu(self, wanted):
        not_drawn = set()
        player = self._player()
        now = self.clock()
        want = {}
        for oid, it in wanted:
            b = self._built.get(it["id"])
            if b is not None:
                want[oid - OVERLAY_BASE + 1] = (it, b)
        for n in list(self._gpu):
            st, w = self._gpu[n], want.get(n)
            if w is not None and w[0]["id"] == st["gid"]:
                continue
            fd = self._fading.get(n)
            if w is None and fd is not None and fd[0] == st["gid"] and fd[1] > now:
                continue                    # a fade out is running there: the timer clears it
            self._gpu_clear(n)
        for n, (it, b) in sorted(want.items()):
            gid = it["id"]
            old = self._gpu.get(n)
            try:
                if old is None or old["token"] != b["token"]:
                    player.graphic("load", n, b["path"], b["w"], b["h"], b["lines"])
                    same = old is not None and old["gid"] == gid
                    st = self._gpu[n] = {"gid": gid, "token": b["token"], "cfg": old["cfg"] if same else None, "on": old["on"] if same else False}
                else:
                    st = old
                m = it["motion"]
                cfg = {"x": b["x"], "y": b["y"], "motion": motion_for(it, "gpu"), "seconds": m["seconds"], "out": m["out"],
                       "speed": m["speed"], "loop": m["loop"]}
                if cfg != st["cfg"]:
                    player.graphic("set", n, cfg)
                    st["cfg"] = cfg
                self._fading.pop(n, None)       # shown again while it faded out: it fades in from nothing (the renderer's show)
                if not st["on"]:
                    if gid in self._hold_keep and cfg["motion"] in ("fade", "typewriter"):
                        player.graphic("show", n, "still")      # back from a hold: it is simply there
                    else:
                        player.graphic("show", n)
                    st["on"] = True
                elif gid in self._replay:
                    player.graphic("replay", n)
                self._replay.discard(gid)
                self._once.add(gid)
            except Exception as e:          # the player is down or restarting: it gets them when it comes back (apply)
                self._refused(gid, e)
                self._gpu.pop(n, None)
                not_drawn.add(gid)
        return not_drawn

    def _put(self, oid, gid, path, b):
        """(under _apply) Show `path` (the picture, or a step of it) as overlay `oid`; the file it replaces goes."""
        self._player().overlay_part(oid, path, b["x"], b["y"], 0, b["w"], b["h"], b["w"] * 4)
        old = self._shown.get(oid)
        self._shown[oid] = (gid, path, b["x"], b["y"])
        if old is not None and old[1] != path:
            self._retire(old[1])

    def _draw_overlay(self, wanted):
        not_drawn = set()
        want_ids = {}
        for oid, it in wanted:
            b = self._built.get(it["id"])
            if b is not None:
                want_ids[oid] = (it, b)
        for oid in list(self._shown):
            s = self._shown[oid]
            w = want_ids.get(oid)
            if w is None or w[0]["id"] != s[0]:
                t = self._tasks.get(s[0])
                if w is None and t is not None and t["kind"] == "out":
                    continue                # it is fading out, a step at a time: the last step takes it off
                try:
                    self._player().overlay_remove(oid)
                except Exception:
                    pass
                self._shown.pop(oid, None)
                self._drawn.pop(oid, None)
                self._tasks.pop(s[0], None)
                self._once.discard(s[0])
                self._retire(s[1])
        for oid, (it, b) in sorted(want_ids.items()):
            gid = it["id"]
            sig = (gid, b["token"], b["x"], b["y"])
            again = gid in self._replay
            fading = self._tasks.get(gid)
            fading = fading is not None and fading["kind"] == "out"
            if self._drawn.get(oid) == sig and not again and not fading:
                continue
            task = self._tasks.pop(gid, None)
            level0 = task["level"] if task is not None and task["kind"] == "out" else 0.0
            kind = motion_for(it, "overlay")
            new = (gid not in self._once and gid not in self._hold_keep) or again or level0 > 0.0
            started = False
            if new and kind in ("fade", "typewriter"):
                started = self._start_task(gid, "in" if kind == "fade" else "type", oid, b, it["motion"]["seconds"], level0)
            if not started:
                try:
                    self._put(oid, gid, b["path"], b)
                except Exception as e:      # the player is down or restarting: it gets them when it comes back (apply)
                    self._refused(gid, e)
                    not_drawn.add(gid)
                    continue
            self._drawn[oid] = sig
            self._once.add(gid)
            self._replay.discard(gid)
        return not_drawn

    # -- the stepped motions of the classic path ------------------------------------------------------------------------
    def _start_task(self, gid, kind, oid, b, seconds, level0=0.0):
        """(under _apply) Begin a fade in ("in"), a fade out ("out") or a typing ("type") of the picture `b` as overlay
        `oid`, drawing its first step now. False (and nothing begun) when the picture is too large to step, the steps
        already running would be too much, or the first step did not go: the caller draws the picture still."""
        if seconds <= 0 or b["bytes"] > STEP_MAX_BYTES:
            return False
        if sum(t["b"]["bytes"] for t in self._tasks.values()) + b["bytes"] > STEP_TOTAL_BYTES:
            return False
        if kind == "out":
            t = self._tasks.get(gid)
            level0 = t["level"] if t is not None else 1.0
        try:
            with open(b["path"], "rb") as f:
                base = f.read(b["bytes"] + 1)
        except OSError:
            return False
        if len(base) != b["w"] * b["h"] * 4:
            return False
        task = {"gid": gid, "kind": kind, "oid": oid, "b": b, "base": base, "t0": self.clock(), "dur": float(seconds),
                "level0": level0, "level": level0, "ext": _ink_extents(base, b["w"], b["h"], b["lines"]) if kind == "type" else None}
        self._tasks[gid] = task
        if not self._step_task(task, task["t0"]):
            self._tasks.pop(gid, None)
            return False
        self._kick_ticker()
        return True

    def _step_task(self, task, now):
        """(under _apply) One step. True if it is on the screen (or the motion ended); False if the player did not take it."""
        gid, oid, b, kind = task["gid"], task["oid"], task["b"], task["kind"]
        p = min(1.0, max(0.0, (now - task["t0"]) / task["dur"]))
        try:
            if kind == "out":
                task["level"] = task["level0"] * (1.0 - p)
                if p >= 1.0:
                    self._tasks.pop(gid, None)
                    self._drop_drawn(gid)
                    done = self._built.pop(gid, None)
                    if done is not None:
                        _unlink(done["path"])
                    return True
                data = _faded(task["base"], task["level"])
            elif p >= 1.0:
                task["level"] = 1.0
                self._tasks.pop(gid, None)
                self._put(oid, gid, b["path"], b)       # the picture itself, from its own file
                return True
            elif kind == "in":
                task["level"] = task["level0"] + (1.0 - task["level0"]) * p
                data = _faded(task["base"], task["level"])
            else:
                task["level"] = p
                data = _typed(task["base"], b["w"], b["h"], b["lines"], task["ext"], p)
            with self._lock:
                self._serial += 1
                serial = self._serial
            rundir = getattr(self._player(), "rundir", None)
            path = os.path.join(rundir, "graphic-%s-%d-%d.bgra" % (gid, self._pid, serial))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(data)
            try:
                self._put(oid, gid, path, b)
            except Exception:
                _unlink(path)
                raise
            return True
        except Exception as e:
            self.log("pvj-web: graphics: a step of %s did not go: %s" % (kind, e))
            return False

    def _timer(self, due, kind, *args):
        with self._lock:
            self._timers.append((due, kind) + args)
        self._kick_ticker()

    def _kick_ticker(self):
        if self.synchronous:
            return                          # a test steps by hand: step()
        with self._lock:
            if self._ticker is None or not self._ticker.is_alive():
                self._ticker = threading.Thread(target=self._tick_loop, name="graphics-motion", daemon=True)
                self._ticker.start()
        self._tick_wake.set()

    def _tick_loop(self):
        while not self._stop.is_set():
            began = time.monotonic()
            try:
                wait = self.step()
            except Exception as e:          # never leave the stepper dead
                self.log("pvj-web: graphics: %s" % e)
                wait = 1.0
            wait = 5.0 if wait is None else max(0.005, wait - (time.monotonic() - began))
            self._tick_wake.wait(wait)
            self._tick_wake.clear()

    def step(self, now=None):
        """Run the timers that are due and one step of every running motion (the stepping thread calls this about STEP_HZ
        times a second while one runs; a test calls it by hand). Returns the seconds until it is next wanted, None when
        nothing is running. A motion has an end, so the thread idles by itself."""
        now = self.clock() if now is None else now
        with self._lock:
            due = [t for t in self._timers if t[0] <= now]
            self._timers = [t for t in self._timers if t[0] > now]
        with self._apply:
            for t in due:
                if t[1] == "slot":
                    n, gid = t[2], t[3]
                    fd = self._fading.get(n)
                    if fd is not None and fd[0] == gid:     # nobody showed it again: the picture goes
                        self._gpu_clear(n)
                        self._once.discard(gid)
                        done = self._built.pop(gid, None)
                        if done is not None:
                            _unlink(done["path"])
            for gid, task in list(self._tasks.items()):
                if self._tasks.get(gid) is not task:
                    continue
                if not self._step_task(task, now):
                    self._tasks.pop(gid, None)
                    self._status[gid] = {"state": "error", "message": "The player did not take a step of this; it is drawn when the player restarts.", "missing": 0}
            running = bool(self._tasks)
        with self._lock:
            nxt = min((t[0] for t in self._timers), default=None)
        waits = [1.0 / STEP_HZ] if running else []
        if nxt is not None:
            waits.append(max(0.0, nxt - now))
        return min(waits) if waits else None

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
            if shown and self._held:
                st = {"state": "held", "message": "Held back while a pairing code is on the screen.", "missing": 0}
            elif shown and st["state"] == "off":
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
        stage = self._stage()
        return {"on": stage_on(cfg), "items": out, "showing": showing(cfg), "max_items": MAX_ITEMS, "max_on": self.max_on(), "tier": self._tier(),
                "mode": "gpu" if stage else "overlay", "stage": stage, "note": "" if stage else OVERLAY_NOTE, "step_hz": STEP_HZ,
                "motions": list(MOTIONS if stage else MOTIONS_OVERLAY), "keys": list(KEYS), "effects": list(EFFECTS), "where": list(WHERE), "later": LATER,
                "fonts": [dict(f) for f in FONTS], "files": store.files(), "available": store.client.available(),
                "screen": {"width": size[0], "height": size[1]} if size else None,
                "limits": {"text": MAX_TEXT, "lines": MAX_TEXT_LINES, "saved_lines": MAX_SAVED_LINES, "memory": MEMORY_BUDGET, "used": used},
                "anchors": list(ANCHORS), "controllers_text": self.controllers_may_push(), "held": self._held}

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
