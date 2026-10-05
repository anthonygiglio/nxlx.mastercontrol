# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shader sources: ISF generator shaders drawn by the player's GPU in place of a clip.

An ISF file (Interactive Shader Format) is a GLSL fragment shader with a JSON comment at the top that names its
inputs. Here a generator (a shader that needs no picture) is translated into an mpv user shader and drawn over a
"carrier": a tiny black picture that the player makes itself, 30 times a second, so that there are frames to draw on.

How it sits in the player (all of it checked against a real mpv in CI, see tests/test_shaders_gpu.py):
* the shader hooks NATIVE, the first stage, and the carrier is RGB, so everything mpv does afterwards still applies:
  the brightness that the panel's opacity, fades and Blackout use (a shader at the MAIN stage ignored it), scaling,
  and the projection mapping, which stays where it was at the OUTPUT stage;
* the shader's own size is set with WIDTH and HEIGHT, so the carrier can stay tiny whatever the drawing size;
* TIME is counted from mpv's `frame` number and the carrier's frame rate (see SHADERS.md for how exact that is). It is
  put together in high precision from small whole numbers: with Mesa's software GPU on OpenGL ES in CI, a shader
  that computed mod(float(frame), 1048576.0) drew a black picture with no error (most likely medium precision);
* input values are written into the shader as constants, like the mapper's numbers: nothing but checked numbers and
  the shader's own code reach the GPU. The code itself is untrusted text from a full-access device; it is bounded in
  size, may not carry mpv's own `//!` commands or preprocessor includes, and what the GPU refuses is taken off again.

Sources live in two places: the bundled set in pvj/shaders.d and uploads in <state>/shaders. The generated GLSL goes
to the player's runtime folder under fresh names, as the mapper's does (the player cannot read the state folder).
"""

import hashlib
import json
import math
import os
import re
import socket
import threading
import time
import unicodedata

from .api import ApiError, valid_name
from .player import PlayerError

MAX_SOURCE = 32 * 1024        # bytes of one ISF file (it must also fit a JSON request)
MAX_HEADER = 8 * 1024
MAX_INPUTS = 24
MAX_UPLOADS = 64
MAX_TEXT = 200                # description, credit
CARRIER_FPS = 30
HEIGHTS = (360, 540, 720, 1080)         # every drawing height any board may use (a settings file may hold any of them)
DEFAULT_HEIGHT = 540                    # for a Pi 4 and for a board nobody has measured; see default_height()
FRAME_WRAP = 1 << 24                    # the carrier's frame number is carried in three bytes (6.4 days at 30 a second)
SPEED_MIN, SPEED_MAX = 0.0, 4.0         # the speed control: 1 is the shader's own pace, 0 freezes it
GAIN_MIN, GAIN_MAX = 0.0, 2.0           # the brightness trim: 1 leaves the shader as it is
CLOCKS = ("carrier", "frame")
DWELL_MIN, DWELL_MAX, DWELL_DEFAULT = 10, 3600, 180
VERIFY_SECONDS = 4.0
BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "shaders.d")
# Packs: the project's own shaders lie in BUNDLED_DIR itself; each folder inside it is a pack of somebody else's
# shaders under its own licence (see THIRD_PARTY_LICENSES.md). Uploads are listed as the pack "uploads".
PACK_OWN, PACK_UPLOADS = "nxlx", "uploads"
PACK = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
MAX_PACKS = 16
FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}\.fs")
INPUT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
# The carrier with a clock: each frame's three colour bytes are its own number (red the lowest), so a shader can read
# how many frames the carrier has played, and the panel can read the same number from the player (time-pos).
_COUNTER = "format=gbrp,geq=r=N-256*floor(N/256):g=floor(N/256)-256*floor(N/65536):b=floor(N/65536)-256*floor(N/16777216),"
CARRIER = re.compile(r"av://lavfi:color=c=black:size=[0-9]{1,4}x[0-9]{1,4}:rate=%d,(?:%s)?format=rgb0" % (CARRIER_FPS, re.escape(_COUNTER)))
TYPES = ("float", "bool", "long", "color", "point2D", "event")
# What stands in when a shader is refused and there is none to go back to: the carrier itself is not black any more
# (its colour is its frame number), so black is drawn over it.
BLACK = "//!HOOK NATIVE\n//!BIND HOOKED\n//!DESC nxlx black\n\nvec4 hook() {\n    return vec4(0.0, 0.0, 0.0, 1.0);\n}\n"
# Names an input may not have: GLSL's own words, what mpv and this translator define, and ISF's built-ins.
RESERVED = frozenset("""
attribute const uniform varying layout centroid flat smooth noperspective patch sample break continue do for while
switch case default if else subroutine in out inout float double int void bool true false invariant precise discard
return mat2 mat3 mat4 vec2 vec3 vec4 ivec2 ivec3 ivec4 bvec2 bvec3 bvec4 uint uvec2 uvec3 uvec4 lowp mediump highp
precision sampler2D sampler3D samplerCube struct common partition active asm class union enum typedef template this
goto inline noinline volatile public static extern external interface long short half fixed unsigned superp input
output filter sizeof cast namespace using main hook frame random input_size target_size tex_offset pixel_size color
texture texture2D radians degrees sin cos tan asin acos atan pow exp log exp2 log2 sqrt inversesqrt abs sign floor
ceil fract mod min max clamp mix step smoothstep length distance dot cross normalize reflect refract
TIME TIMEDELTA DATE FRAMEINDEX PASSINDEX RENDERSIZE isf_FragNormCoord vv_FragNormCoord
IMG_PIXEL IMG_NORM_PIXEL IMG_THIS_PIXEL IMG_THIS_NORM_PIXEL IMG_SIZE
""".split())
RESERVED_LOWER = frozenset(w.lower() for w in RESERVED)
# No nested optional white space: "(\s*(?:void)?\s*)" took seconds on "void main(" followed by 31,000 spaces.
_MAIN = re.compile(r"\bvoid\s+main\s*\(\s*(?:void\s*)?\)")
_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(\w*)(?:[ \t]+(\w+))?", re.M)
_ALLOWED_DIRECTIVES = ("define", "undef", "if", "ifdef", "ifndef", "else", "elif", "endif")
_GLOBAL_IO = re.compile(r"\b(?:uniform|varying|attribute|layout)\b|(?:^|[;{}])\s*(?:in|out)\s")
_IMG = re.compile(r"\bIMG_(?:PIXEL|NORM_PIXEL|THIS_PIXEL|THIS_NORM_PIXEL|SIZE)\b")
# Names the player and this translator own, in any letter case: the hook, mpv's textures and their companions.
_OWN = re.compile(r"\b(?:pvj_\w*|hook|hooked\w*|texture\d+|texcoord\d+|texture_(?:size|rot|off)\d+|pixel_size\d+|texmap\d+"
                  r"|out_color|input_size|target_size|tex_offset)\b", re.I)
# Two names that many ISF files use and the player owns. They are not refused: every use is renamed to a pvj_ name
# (which a file cannot write itself), so the player's own `out_color` and `color` are never touched by a file.
_RENAMED = "out_color"                  # in the code, spelled exactly so; any other letter case stays refused
_INPUT_RENAMED = ("color",)             # as an input's name, in any letter case


HIDDEN = ("Cc", "Cf", "Zl", "Zp", "Cs")      # Unicode categories no name or label may hold
# A bundled shader with this category is made to be performed with (strong, rhythmic): it is in the library but not in
# the Vibes rotation until someone puts it there. Everything else is in until it is taken out.
PERFORMANCE = "performance"


class ShaderError(ValueError):
    """An ISF file this box will not take; the message says why in plain words."""


# ---- what a board can draw -----------------------------------------------------------------------------------------------
def heights_for(board):
    """The drawing heights offered on a board. Measured on a Pi 4 (2560 x 1440 at 75 Hz): at 1080 lines even the
    lightest shaders dropped 7 to 8 frames a second, so 1080 is never offered there. A Pi 5 and x86 keep every
    choice; nothing is measured on them."""
    return HEIGHTS if board in ("pi5", "x86") else HEIGHTS[:3]


def default_height(board):
    """540 lines on a Pi 4 (every bundled shader but the two heavy ones kept up there) and on a board that is unknown
    or weaker; 720 on a Pi 5 and x86, which is a guess until someone measures them."""
    return 720 if board in ("pi5", "x86") else DEFAULT_HEIGHT


# ---- numbers ---------------------------------------------------------------------------------------------------------
def _num(v, what, limit=1e6):
    try:            # a whole number of 400 digits makes isfinite raise instead of answering
        ok = not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= limit
    except (OverflowError, ValueError):
        ok = False
    if not ok:
        raise ShaderError("%s must be a number" % what)
    return float(v)


def _f(x, digits=7):
    """A GLSL float literal (finite numbers only). A magnitude below 1e-30 is written as 0: a 32-bit number cannot
    hold 1e-40, and what a compiler makes of such a literal is its own business."""
    v = _num(x, "a value", 1e9)
    s = "%.*g" % (digits, 0.0 if abs(v) < 1e-30 else v)
    return s if ("." in s or "e" in s) else s + ".0"


def _text(v, limit=MAX_TEXT):
    """A short line of plain text for the panel. Dropped: control characters, and the characters that are not seen
    but change what is seen (text direction overrides, zero-width marks, line and paragraph separators)."""
    if not isinstance(v, str):
        return ""
    return "".join(ch for ch in v if unicodedata.category(ch) not in HIDDEN)[:limit].strip()


# ---- the ISF header ----------------------------------------------------------------------------------------------------
def _input(spec, seen):
    if not isinstance(spec, dict):
        raise ShaderError("each input must be an object")
    name, kind = spec.get("NAME"), spec.get("TYPE")
    if not isinstance(name, str) or not INPUT_NAME.fullmatch(name):
        raise ShaderError("an input name must be 1 to 32 letters, digits or _ and start with a letter")
    if name.lower() in _INPUT_RENAMED:
        pass                                # written into the shader under another name, see ident()
    elif (name.lower() in RESERVED_LOWER or name.lower().startswith(("gl_", "pvj_", "isf_", "hooked")) or "__" in name
            or _OWN.fullmatch(name)):
        raise ShaderError("the input name %s is taken by the shader language or the player" % name)
    if name in seen:
        raise ShaderError("two inputs are called %s" % name)
    seen.add(name)
    if kind in ("image", "cube"):
        raise ShaderError("input %s is a picture: only generator shaders are supported, not filters that need an image" % name)
    if kind in ("audio", "audioFFT"):
        raise ShaderError("input %s wants sound (audio or FFT), which is not supported" % name)
    if kind not in TYPES:
        raise ShaderError("input %s has the type %s, which is not supported" % (name, _text(str(kind), 20) or "?"))
    out = {"name": name, "type": kind, "label": _text(spec.get("LABEL"), 60) or name}
    if kind == "float":
        lo, hi = _num(spec.get("MIN", 0.0), "MIN of " + name), _num(spec.get("MAX", 1.0), "MAX of " + name)
        if lo > hi:
            raise ShaderError("input %s has MIN above MAX" % name)
        out.update(min=lo, max=hi, default=min(hi, max(lo, _num(spec.get("DEFAULT", (lo + hi) / 2.0), "DEFAULT of " + name))))
    elif kind == "bool":
        d = spec.get("DEFAULT", False)
        if not isinstance(d, bool) and d not in (0, 1):
            raise ShaderError("DEFAULT of %s must be true or false" % name)
        out["default"] = bool(d)
    elif kind == "long":
        values = spec.get("VALUES")
        d = spec.get("DEFAULT", 0)
        if values is not None:
            if (not isinstance(values, list) or not 1 <= len(values) <= 64
                    or not all(isinstance(v, int) and not isinstance(v, bool) and abs(v) <= 100000 for v in values)):
                raise ShaderError("VALUES of %s must be 1 to 64 whole numbers" % name)
            out["values"] = list(values)
            labels = spec.get("LABELS")
            labels = labels if isinstance(labels, list) else []
            out["labels"] = [(_text(labels[n], 40) if n < len(labels) else "") or str(v) for n, v in enumerate(values)]
            if "DEFAULT" not in spec:
                d = values[0]
        else:
            lo = spec.get("MIN", 0) if "MIN" in spec or "MAX" in spec else -100000
            hi = spec.get("MAX", 100000)
            lo, hi = _num(lo, "MIN of " + name, 100000), _num(hi, "MAX of " + name, 100000)
            if lo != int(lo) or hi != int(hi) or lo > hi:
                raise ShaderError("MIN and MAX of %s must be whole numbers, MIN not above MAX" % name)
            out.update(min=int(lo), max=int(hi))
            if "DEFAULT" not in spec:
                d = min(int(hi), max(int(lo), 0))
        try:
            d = _num(d, "DEFAULT of " + name, 100000)
        except ShaderError:
            raise ShaderError("DEFAULT of %s must be a whole number" % name)
        if d != int(d):
            raise ShaderError("DEFAULT of %s must be a whole number" % name)
        if values is not None and int(d) not in values:
            raise ShaderError("DEFAULT of %s is not one of its VALUES" % name)
        out["default"] = int(d) if values is not None else min(out["max"], max(out["min"], int(d)))
    elif kind == "color":
        d = spec.get("DEFAULT", [1.0, 1.0, 1.0, 1.0])
        if not isinstance(d, list) or len(d) not in (3, 4):
            raise ShaderError("DEFAULT of %s must be [r, g, b, a]" % name)
        rgba = [min(1.0, max(0.0, _num(c, "DEFAULT of " + name))) for c in d]
        out["default"] = rgba + [1.0] * (4 - len(rgba))
    elif kind == "point2D":
        d = spec.get("DEFAULT", [0.0, 0.0])
        if not isinstance(d, list) or len(d) != 2:
            raise ShaderError("DEFAULT of %s must be [x, y]" % name)
        out["default"] = [_num(c, "DEFAULT of " + name) for c in d]
        if "MIN" in spec or "MAX" in spec:
            lo, hi = spec.get("MIN", [0.0, 0.0]), spec.get("MAX", [1.0, 1.0])
            if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) == 2 and len(hi) == 2):
                raise ShaderError("MIN and MAX of %s must be [x, y]" % name)
            lo, hi = [_num(c, "MIN of " + name) for c in lo], [_num(c, "MAX of " + name) for c in hi]
            if lo[0] > hi[0] or lo[1] > hi[1]:
                raise ShaderError("input %s has MIN above MAX" % name)
            out.update(min=lo, max=hi)
            out["default"] = [min(hi[n], max(lo[n], out["default"][n])) for n in (0, 1)]
    else:                                   # an event: a button in ISF; it is never pressed here
        out["default"] = False
    return out


def ident(name):
    """The name an input has inside the generated shader: its own, or a pvj_ name where the player owns the word."""
    return "pvj_in_" + name if name.lower() in _INPUT_RENAMED else name


def _no_constant(word):
    raise ShaderError("the JSON header holds %s, which is not a number a shader can use" % word)


def _no_repeats(pairs):
    out = {}
    for key, value in pairs:
        if key in out:              # the last one would win: an INPUTS list that is checked, and another that is used
            raise ShaderError("the JSON header has %s twice" % _text(str(key), 40))
        out[key] = value
    return out


def default_in_vibes(parsed):
    """Whether a bundled shader is in the Vibes rotation before anyone chose: all but those of the category
    "Performance", which would not suit a room's ambience."""
    return PERFORMANCE not in (c.lower() for c in parsed.get("categories", ()))


def strip_comments(body):
    """The code without its comments (each replaced by a space, line breaks kept so line numbers stay true). The
    checks below read what the compiler will read: a directive hidden behind /**/ is a directive."""
    out, i, n = [], 0, len(body)
    while i < n:
        two = body[i:i + 2]
        if two == "//":
            j = body.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif two == "/*":
            j = body.find("*/", i + 2)
            if j < 0:
                raise ShaderError("a /* comment in the code is never closed")
            out.append(" " + "\n" * body.count("\n", i, j))
            i = j + 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def parse(source):
    """An ISF generator from untrusted text: {"description", "credit", "cost", "inputs", "body", "line"}. `body` is
    the shader code after the JSON comment and `line` the line of the file it starts on. Raises ShaderError."""
    if isinstance(source, bytes):
        if len(source) > MAX_SOURCE:
            raise ShaderError("the file is larger than %d KB" % (MAX_SOURCE // 1024))
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError:
            raise ShaderError("the file is not plain text")
    if not isinstance(source, str):
        raise ShaderError("the shader must be text")
    if len(source.encode("utf-8", "replace")) > MAX_SOURCE:
        raise ShaderError("the file is larger than %d KB" % (MAX_SOURCE // 1024))
    source = source.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "//!" in source:
        raise ShaderError("the text //! is not allowed anywhere in the file (the player reads it as a command)")
    start = len(source) - len(source.lstrip())
    if not source.startswith("/*", start):
        raise ShaderError("not an ISF file: it must start with a /*{ ... }*/ comment that holds the JSON header")
    end = source.find("*/", start + 2)
    if end < 0 or end - start > MAX_HEADER:
        raise ShaderError("the JSON header comment is not closed, or is larger than %d KB" % (MAX_HEADER // 1024))
    try:
        head = json.loads(source[start + 2:end], parse_constant=_no_constant, object_pairs_hook=_no_repeats)
    except ShaderError:
        raise
    except (ValueError, RecursionError):
        raise ShaderError("the JSON header at the top cannot be read")
    if not isinstance(head, dict):
        raise ShaderError("the JSON header must be an object")
    passes = head.get("PASSES")
    if passes:
        if not isinstance(passes, list) or len(passes) > 1 or not isinstance(passes[0], dict):
            raise ShaderError("it draws in several passes (PASSES), which is not supported")
        if passes[0].get("PERSISTENT") or passes[0].get("TARGET") or passes[0].get("FLOAT"):
            raise ShaderError("it keeps a picture between frames (a persistent buffer), which is not supported")
        if passes[0]:
            raise ShaderError("it sets its own drawing size (PASSES), which is not supported")
    if head.get("PERSISTENT_BUFFERS"):
        raise ShaderError("it keeps a picture between frames (a persistent buffer), which is not supported")
    if head.get("IMPORTED"):
        raise ShaderError("it loads pictures from other files (IMPORTED), which is not supported")
    inputs = head.get("INPUTS", [])
    if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS:
        raise ShaderError("INPUTS must be a list of at most %d" % MAX_INPUTS)
    seen = set()
    clean = [_input(i, seen) for i in inputs]
    body = source[end + 2:]
    # Comments go first and are never passed on, so they may hold any text (real ISF files have dashes, arrows and
    # bullets in theirs, and a backslash at times). What is left is what the compiler will read, and that must be
    # plain ASCII without a backslash. A // comment ends at its line break here whatever its last character is, and
    # the comment is not passed on, so a backslash at its end continues nothing.
    body = strip_comments(body)
    if "\\" in body:
        raise ShaderError("the character \\ is not allowed in the code (no line continuations)")
    bad = sorted({ch for ch in body if not (" " <= ch <= "~" or ch in "\n\t")})
    if bad:
        raise ShaderError("the shader code may hold plain ASCII text only (found %r)" % bad[0])
    if "##" in body:                        # two pieces joined into a name that the checks below never saw
        raise ShaderError("the text ## is not allowed in the code (no joining of names)")
    for m in _DIRECTIVE.finditer(body):
        if m.group(1) not in _ALLOWED_DIRECTIVES:
            raise ShaderError("the line #%s is not allowed (no includes, versions, extensions or pragmas)" % (m.group(1) or "?"))
        word = m.group(2) or ""
        if m.group(1) in ("define", "undef") and (word.lower() in RESERVED_LOWER or _OWN.fullmatch(word)
                                                   or word.lower().startswith(("gl_", "isf_")) or "__" in word):
            raise ShaderError("#%s %s is not allowed: the name belongs to the shader language or the player" % (m.group(1), word))
    if _GLOBAL_IO.search(body):
        raise ShaderError("the code declares its own uniform, varying, in or out, which the player cannot fill")
    if _IMG.search(body):
        raise ShaderError("it reads a picture (IMG_PIXEL and the like): only generator shaders are supported")
    own = next((m for m in _OWN.finditer(body) if m.group(0) != _RENAMED), None)
    if own:
        raise ShaderError("the name %s is used by the player; rename it" % own.group(0))
    if len(_MAIN.findall(body)) != 1:
        raise ShaderError("the code must have exactly one void main()")
    # The code as the player will get it, made once per file: comments gone, ISF's names exchanged for ours.
    code = re.sub(r"\bgl_FragColor\b", "pvj_color", body)
    code = re.sub(r"\bgl_FragCoord\b", "pvj_coord", code)
    code = re.sub(r"\b%s\b" % _RENAMED, "pvj_u_" + _RENAMED, code)
    for i in clean:
        if ident(i["name"]) != i["name"]:
            code = re.sub(r"\b%s\b" % i["name"], ident(i["name"]), code)
    code = _MAIN.sub("void pvj_main()", code).rstrip()
    cats = head.get("CATEGORIES")
    cats = [c for c in (_text(c, 40) for c in (cats if isinstance(cats, list) else [])[:16]) if c]
    return {"description": _text(head.get("DESCRIPTION")), "credit": _text(head.get("CREDIT")), "cost": _text(head.get("COST")),
            "categories": cats, "inputs": clean, "body": body, "code": code, "line": source.count("\n", 0, end + 2) + 1}


def clean_value(i, v):
    """One input's value from untrusted input, by the input's type; raises ShaderError. Numbers are kept inside MIN
    and MAX; everything else must be exactly of its kind (true is not 1, "2" is not 2)."""
    name, kind = i["name"], i["type"]
    if kind == "float":
        return min(i["max"], max(i["min"], _num(v, name)))
    if kind in ("bool", "event"):
        if not isinstance(v, bool):
            raise ShaderError("%s must be true or false" % name)
        return v
    if kind == "long":
        try:
            whole = not isinstance(v, bool) and isinstance(v, (int, float)) and v == int(v) and abs(v) <= 100000
        except (OverflowError, ValueError):
            whole = False
        if not whole:
            raise ShaderError("%s must be a whole number" % name)
        if "values" in i:
            if int(v) not in i["values"]:
                raise ShaderError("%s must be one of %s" % (name, ", ".join(str(x) for x in i["values"])))
            return int(v)
        return min(i["max"], max(i["min"], int(v)))
    if kind == "color":
        if not isinstance(v, list) or len(v) not in (3, 4):
            raise ShaderError("%s must be [r, g, b] or [r, g, b, a], each 0 to 1" % name)
        rgba = [min(1.0, max(0.0, _num(c, name))) for c in v]
        return rgba + [1.0] * (4 - len(rgba))
    if not isinstance(v, list) or len(v) != 2:
        raise ShaderError("%s must be [x, y]" % name)
    xy = [_num(c, name) for c in v]
    if "min" in i:
        xy = [min(i["max"][n], max(i["min"][n], xy[n])) for n in (0, 1)]
    return xy


def clean_values(parsed, values):
    """{name: value} for the inputs, from untrusted input: unknown names are refused, each value is checked by its
    input's type (see clean_value)."""
    if values is None:
        return {}
    if not isinstance(values, dict) or len(values) > MAX_INPUTS:
        raise ShaderError("values must be an object of input name and value")
    inputs = {i["name"]: i for i in parsed["inputs"]}
    out = {}
    for name, v in values.items():
        if name not in inputs:
            raise ShaderError("no input called %s" % _text(str(name), 32))
        out[name] = clean_value(inputs[name], v)
    return out


def clean_controls(controls, base=None):
    """The controls every shader has, from untrusted input: {"speed": 0 to 4 (1 is the shader's own pace, 0 freezes
    it), "hue": -180 to 180 degrees of palette shift, "brightness": 0 to 2 (1 leaves it as it is)}. Keys that are
    left out keep the value in `base` (or the neutral one); an unknown key or a value that is not a number is refused,
    a number outside its range is kept inside it."""
    out = dict({"speed": 1.0, "hue": 0.0, "brightness": 1.0} if base is None else base)
    if controls is None:
        return out
    if not isinstance(controls, dict):
        raise ShaderError("controls must be an object of speed, hue and brightness")
    for name, v in controls.items():
        if name == "speed":
            out[name] = min(SPEED_MAX, max(SPEED_MIN, _num(v, "speed")))
        elif name == "hue":
            out[name] = min(180.0, max(-180.0, _num(v, "hue")))
        elif name == "brightness":
            out[name] = min(GAIN_MAX, max(GAIN_MIN, _num(v, "brightness")))
        else:
            raise ShaderError("no control called %s (there are speed, hue and brightness)" % _text(str(name), 32))
    return out


def turn(*degrees):
    """Palette shifts added up, brought back into -180 to 180."""
    return (sum(degrees) + 180.0) % 360.0 - 180.0


def shape_of(parsed, values):
    """The part of a set of values that can change what the GPU compiler makes of the code (switches and whole
    numbers; plain numbers and colours cannot): a text that was taken with one shape is watched again with another."""
    kinds = {i["name"]: i["type"] for i in parsed["inputs"]}
    return repr(sorted((n, v) for n, v in values.items() if kinds.get(n) in ("bool", "long", "event")))


def hue_matrix(degrees):
    """The 3x3 matrix (row-major) that turns colours around the grey axis by `degrees`: a palette shift that keeps
    black, white and greys as they are."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    k, r = (1.0 - c) / 3.0, s / math.sqrt(3.0)
    return [c + k, k - r, k + r, k + r, c + k, k - r, k - r, k + r, c + k]


def translate(parsed, size, values=None, hue=0.0, offset=0.0, desc="nxlx shader", today=None, speed=1.0, gain=1.0, anchor=None):
    """The mpv user shader for a parsed ISF generator, drawn at `size` (width, height). `values` replaces the inputs'
    defaults, `hue` (degrees) shifts the palette, `gain` trims the brightness, `speed` is how fast TIME runs.
    TIME is `offset` plus `speed` times the seconds since `anchor`, a frame number of the carrier (see carrier_url);
    with no anchor it is counted from mpv's own `frame` number, as the first version did."""
    width, height = int(size[0]), int(size[1])
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise ShaderError("bad drawing size")
    if not re.fullmatch(r"[a-z0-9 ]{1,40}", desc):
        raise ShaderError("bad description")
    values = clean_values(parsed, values)
    hue, offset = _num(hue, "hue", 360.0), _num(offset, "offset", 1e9)
    speed, gain = _num(speed, "speed", SPEED_MAX), _num(gain, "brightness", GAIN_MAX)
    if speed < SPEED_MIN or gain < GAIN_MIN:
        raise ShaderError("speed and brightness cannot be below 0")
    if anchor is not None and (isinstance(anchor, bool) or not isinstance(anchor, int) or not 0 <= anchor < FRAME_WRAP):
        raise ShaderError("bad anchor")
    t = time.localtime() if today is None else today
    lines = ["// nxlx.mastercontrol shader source (generated; do not edit)",
             "//!HOOK NATIVE", "//!BIND HOOKED", "//!WIDTH %d" % width, "//!HEIGHT %d" % height, "//!DESC %s" % desc, "",
             # The name again, inside the code: mpv remembers a shader text it could not compile and says nothing the
             # second time (seen in CI: the same broken shader was "taken" on a second try). A text that differs is
             # compiled, and complained about, afresh.
             "// %s" % desc,
             "#if defined(GL_ES) && (__VERSION__ >= 300 || defined(GL_FRAGMENT_PRECISION_HIGH))",
             "precision highp float;", "precision highp int;", "#define PVJ_HP highp",
             "#else", "#define PVJ_HP", "#endif",
             "#define RENDERSIZE vec2(%s, %s)" % (_f(width), _f(height)),
             "#define TIME pvj_time",
             "#define TIMEDELTA %s" % _f(1.0 / CARRIER_FPS),
             "#define FRAMEINDEX frame",
             "#define PASSINDEX 0",
             "#define DATE vec4(%s, %s, %s, %s)" % (_f(t.tm_year), _f(t.tm_mon), _f(t.tm_mday), _f(t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)),
             "#define isf_FragNormCoord pvj_norm",
             "#define vv_FragNormCoord pvj_norm",
             "PVJ_HP float pvj_time;", "vec2 pvj_norm;", "vec4 pvj_coord;", "vec4 pvj_color;"]
    for i in parsed["inputs"]:
        d, name = values.get(i["name"], i["default"]), ident(i["name"])
        if i["type"] == "float":
            lines.append("const float %s = %s;" % (name, _f(values.get(i["name"], d))))
        elif i["type"] in ("bool", "event"):
            lines.append("const bool %s = %s;" % (name, "true" if d else "false"))
        elif i["type"] == "long":
            lines.append("const int %s = %d;" % (name, d))
        elif i["type"] == "color":
            lines.append("const vec4 %s = vec4(%s);" % (name, ", ".join(_f(c) for c in d)))
        else:
            lines.append("const vec2 %s = vec2(%s, %s);" % (name, _f(d[0]), _f(d[1])))
    lines += ["#line %d" % parsed["line"], parsed["code"], "",
              "vec4 hook() {"]
    if anchor is None:
        # frame = hi * 512 + lo, in whole numbers small enough for 16 bits; hi starts again after 8192 (38.8 hours)
        lines += ["    int pvj_hi = frame / 512;",
                  "    int pvj_lo = frame - pvj_hi * 512;",
                  "    pvj_hi = pvj_hi - (pvj_hi / 8192) * 8192;"]
    else:
        # The carrier's frame number, read from its colour (flat, so any place will do), as hi * 512 + lo again;
        # then the frames since the anchor, which stay right when the three bytes start again from 0.
        lines += ["    PVJ_HP vec3 pvj_px = floor(HOOKED_tex(vec2(0.5, 0.5)).rgb * 255.0 + 0.5);",
                  "    int pvj_g = int(pvj_px.g);",
                  "    int pvj_lo = int(pvj_px.r) + 256 * (pvj_g - (pvj_g / 2) * 2) - %d;" % (anchor % 512),
                  "    int pvj_hi = pvj_g / 2 + 128 * int(pvj_px.b) - %d;" % (anchor // 512),
                  "    if (pvj_lo < 0) { pvj_lo += 512; pvj_hi -= 1; }",
                  "    if (pvj_hi < 0) { pvj_hi += 32768; }"]
    lines += ["    PVJ_HP float pvj_k = 512.0;",
              "    pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / %s%s + %s;" % (
                  _f(CARRIER_FPS), "" if speed == 1.0 else " * %s" % _f(speed), _f(offset, 10)),
              "    pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y);",
              "    pvj_coord = vec4(pvj_norm * RENDERSIZE, 0.0, 1.0);",
              "    pvj_color = vec4(0.0, 0.0, 0.0, 1.0);",
              "    pvj_main();",
              "    vec3 c = clamp(pvj_color.rgb, 0.0, 1.0) * clamp(pvj_color.a, 0.0, 1.0);"]
    if hue:
        h = hue_matrix(hue)
        lines.append("    c = clamp(mat3(%s) * c, 0.0, 1.0);" % ", ".join(_f(h[r * 3 + col]) for col in range(3) for r in range(3)))
    if gain != 1.0:
        lines.append("    c = clamp(c * %s, 0.0, 1.0);" % _f(gain))
    # half a step of noise against bands in slow gradients: a fine diagonal pattern from the pixel's own position
    lines += ["    c += (fract(pvj_coord.x * 0.6113 + pvj_coord.y * 0.3791) - 0.5) / 255.0;",
              "    return vec4(c, 1.0);",
              "}", ""]
    return "\n".join(lines)


# ---- the carrier -------------------------------------------------------------------------------------------------------
def usable(screen):
    """The screen's size as whole numbers, or 1920 x 1080 for one that is no size at all (a player without a window
    reports 0 x 0; dividing by it raised, and 0 x 5 gave a carrier of no width)."""
    try:
        sw, sh = int(screen[0]), int(screen[1])
    except (TypeError, ValueError, IndexError):
        return 1920, 1080
    return (sw, sh) if 16 <= sw <= 16384 and 16 <= sh <= 16384 else (1920, 1080)


def render_size(screen, height):
    """(width, height) the shader is drawn at: `height` lines (never more than the screen has) in the screen's shape."""
    sw, sh = usable(screen)
    h = max(16, min(int(height), sh, 1080))
    w = max(16, min(1920, int(round(h * sw / float(sh) / 2.0)) * 2))
    return w, h


def carrier_url(screen, counter=True):
    """The blank picture the shader is drawn over: RGB, 30 frames a second, in the screen's exact shape and as small
    as that shape allows (the shader sets its own size, so the carrier costs next to nothing). With `counter` each
    frame's colour is its own number (nobody sees it: the shader draws in its place), otherwise it is black. Built
    from whole numbers only; it is the same kind of address as the test pattern, which the hardened player already
    plays."""
    sw, sh = usable(screen)
    g = math.gcd(sw, sh)
    aw, ah = sw // g, sh // g
    m = max(1, -(-36 // ah))
    return "av://lavfi:color=c=black:size=%dx%d:rate=%d,%sformat=rgb0" % (aw * m, ah * m, CARRIER_FPS, _COUNTER if counter else "")


def is_carrier(path):
    return isinstance(path, str) and bool(CARRIER.fullmatch(path))


# ---- listening to the player's own error log -----------------------------------------------------------------------------
class LogTap:
    """A second connection to the player that receives its error messages (request_log_messages). The one-shot
    connections of player.Ipc cannot: messages arrive as events on the connection that asked for them."""

    def __init__(self, socket_path, level="error"):
        self._buf = b""
        self._s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._s.settimeout(2.0)
            self._s.connect(socket_path)
            self._s.sendall(json.dumps({"command": ["request_log_messages", level]}).encode() + b"\n")
        except OSError:
            self._s.close()
            raise

    def drain(self, seconds):
        """[(prefix, level, text)] received within `seconds`."""
        out = []
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            self._s.settimeout(left)
            try:
                chunk = self._s.recv(65536)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            self._buf += chunk
            if len(self._buf) > 4 * 1024 * 1024:
                self._buf = b""
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if isinstance(m, dict) and m.get("event") == "log-message":
                    out.append((str(m.get("prefix", "")), str(m.get("level", "")), str(m.get("text", "")).rstrip()))
        return out

    def close(self):
        try:
            self._s.close()
        except OSError:
            pass


_COMPILER_LINE = re.compile(r"^(?:(ERROR|WARNING): )?\d+:(\d+)(?:\(\d+\))?: (?:(error|warning): )?(.*)$", re.I)
_SOURCE_LINE = re.compile(r"^\[\s*\d+\]( |$)")                 # mpv prints the shader it could not compile, numbered
_INTERNAL = re.compile(r"\bpvj_\w*|\bPVJ_\w*|\bHOOKED\w*|//!|^\s*#|[;{}]\s*$")


def shader_errors(lines):
    """What the player said about a shader it refused, as one short message, or "" if it said nothing of the kind.
    mpv prints the whole shader and then the GPU compiler's log, all at error level, from its video output. Only the
    compiler's errors are passed on: its warnings are left out (a refused shader's log also carries warnings about
    this translator's own pvj_ names, which mean nothing to the person), and so is every line of the shader text."""
    mine = [t for p, level, t in lines if level in ("error", "fatal") and p.startswith("vo/") and not _SOURCE_LINE.match(t)]
    if not any(re.search(r"shader|compile|link log|Unrecognized command|hook", t, re.I) for t in mine):
        return ""
    said, warned = [], []
    for t in mine:
        m = _COMPILER_LINE.match(t.strip())
        if m:
            warning = "warning" in ((m.group(1) or "") + (m.group(3) or "")).lower()
            if warning and _INTERNAL.search(m.group(4)):
                continue
            (warned if warning else said).append("line %s: %s" % (m.group(2), m.group(4)))
    if not said:
        # No compiler line was understood. Pass on what reads like a message, never a line of the generated shader.
        said = warned or [t.strip() for t in mine if t.strip() and not _INTERNAL.search(t)
                          and not re.search(r"shader source:|compile log|link log", t)]
    return "; ".join(said[:4])[:500] or "the GPU refused the shader"


# ---- the engine ----------------------------------------------------------------------------------------------------------
def default_config():
    return {"dwell": DWELL_DEFAULT, "vary": True, "height": DEFAULT_HEIGHT, "disabled": []}


class Engine:
    """The library of shaders and the one that is on screen. Everything the player reads is written to its runtime
    folder under a fresh name; a shader the GPU refuses is replaced by the one before it, or by black."""

    def __init__(self, api, log=print, clock=time.monotonic, tap=LogTap):
        self.api = api
        self.log = log
        self._clock = clock
        self._tap = tap
        self.dir = os.path.join(os.path.dirname(api.settings.path), "shaders")
        self.bundled_dir = BUNDLED_DIR
        self._lock = threading.RLock()          # one change at a time
        self._serial = 0
        self._cache = {}                        # path -> (mtime, size, parsed or ShaderError)
        self._checked = set()                   # (source hash, shape of the values) the GPU has taken
        # what is on: {"id", "values", "hue", "offset", "controls", "anchor", "path", "carrier", "epoch", "desc",
        # "checked", "digest", "size", "preset"}
        self.playing = None
        self.error = None                       # {"id", "message", "at"}: the last shader that was refused

    # -- settings --
    def enabled(self):
        return self.api.registry.enabled("shaders")

    def config(self):
        """The module's settings, with defaults for anything missing or wrong (the settings file has no entry until
        something is changed, so no settings migration is needed)."""
        saved = self.api.settings.data.get("shaders")
        cfg = default_config()
        if isinstance(saved, dict):
            d = saved.get("dwell")
            if isinstance(d, (int, float)) and not isinstance(d, bool) and DWELL_MIN <= d <= DWELL_MAX:
                cfg["dwell"] = int(d)
            if isinstance(saved.get("vary"), bool):
                cfg["vary"] = saved["vary"]
            if saved.get("height") in HEIGHTS:
                cfg["height"] = saved["height"]
            if isinstance(saved.get("disabled"), list):
                cfg["disabled"] = [n for n in saved["disabled"] if isinstance(n, str) and FILE.fullmatch(n)][:MAX_UPLOADS + 64]
            if isinstance(saved.get("included"), list):         # shaders that are out of Vibes by default and were put in
                cfg["included"] = [n for n in saved["included"] if isinstance(n, str) and FILE.fullmatch(n)][:MAX_UPLOADS + 64]
        return cfg

    def _save(self, cfg):
        with self.api.settings.lock:
            self.api.settings.data["shaders"] = cfg
            self.api.settings.save()

    # -- the library --
    def _names(self, folder):
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return sorted((n for n in names if FILE.fullmatch(n) and valid_name(n)), key=str.lower)

    def packs(self):
        """[(pack name, folder)] of the bundled shaders: the project's own first, then each pack folder by name."""
        out = [(PACK_OWN, self.bundled_dir)]
        try:
            names = sorted(os.listdir(self.bundled_dir))
        except OSError:
            names = []
        for n in names:                     # folders are picked out first and counted after: the project's own files,
            folder = os.path.join(self.bundled_dir, n)      # however many, can never push a pack off the list
            if PACK.fullmatch(n) and n not in (PACK_OWN, PACK_UPLOADS) and os.path.isdir(folder) and not os.path.islink(folder):
                out.append((n, folder))
                if len(out) > MAX_PACKS:
                    break
        return out

    def _bundled(self, name):
        """(path, pack) of a bundled shader by its file name, or None. A name is looked for in the project's own
        folder first, so no pack can stand in for one of the project's shaders."""
        for pack, folder in self.packs():
            path = os.path.join(folder, name)
            if os.path.isfile(path) and not os.path.islink(path):
                return path, pack
        return None

    def _hidden_upload(self, name):
        """The path of an uploaded file that has the name of a bundled shader (and so is never listed or played), or None."""
        path = os.path.join(self.dir, name)
        if os.path.islink(self.dir) or os.path.islink(path) or not os.path.isfile(path):
            return None
        return path

    def _path(self, sid):
        """(path, "bundled" or "uploaded") of a shader by its file name; never a path outside the known folders."""
        if not isinstance(sid, str) or not FILE.fullmatch(sid) or not valid_name(sid):
            raise ApiError(400, "invalid shader name")
        hit = self._bundled(sid)
        if hit:
            return hit[0], "bundled"
        path = os.path.join(self.dir, sid)
        if os.path.islink(self.dir) or os.path.islink(path) or not os.path.isfile(path):
            raise ApiError(404, "no such shader")
        return path, "uploaded"

    def _read(self, path):
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            return f.read(MAX_SOURCE + 1)

    def _parsed(self, path):
        """(parsed, source bytes hash) of a file, remembered until the file changes. Raises ShaderError."""
        try:
            st = os.stat(path)
            hit = self._cache.get(path)
            if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
                data = self._read(path)
                try:
                    result = (parse(data), hashlib.sha256(data).hexdigest())
                except ShaderError as e:
                    result = e
                except Exception as e:      # one file that trips the parser must not take the whole list down
                    result = ShaderError("the file could not be checked (%s)" % type(e).__name__)
                if len(self._cache) > 4 * MAX_UPLOADS:
                    self._cache.clear()
                hit = self._cache[path] = ((st.st_mtime_ns, st.st_size), result)
        except OSError as e:
            raise ShaderError("cannot read the file: %s" % (e.strerror or e))
        if isinstance(hit[1], ShaderError):
            raise hit[1]
        return hit[1]

    def library(self):
        """[{"id", "name", "source", "pack", "description", "credit", "cost", "categories", "vibes", "inputs", "error"}],
        bundled first: the project's own, then each third-party pack, then the uploads. The project's shaders and uploads
        are in Vibes until they are taken out ("disabled"). Two kinds are in the library but not in Vibes until put in
        ("included"): a third-party pack's shaders, and the project's own of the category "Performance"."""
        cfg = self.config()
        disabled, included = set(cfg["disabled"]), set(cfg.get("included", ()))
        out = []
        folders = [(folder, "bundled", pack) for pack, folder in self.packs()]
        if not os.path.islink(self.dir):
            folders.append((self.dir, "uploaded", PACK_UPLOADS))
        seen = set()
        for folder, source, pack in folders:
            for n in self._names(folder)[:MAX_UPLOADS + 64]:
                path = os.path.join(folder, n)
                if n in seen or os.path.islink(path) or not os.path.isfile(path):
                    continue
                seen.add(n)
                vibes = n in included if pack not in (PACK_OWN, PACK_UPLOADS) else n not in disabled
                item = {"id": n, "name": n[:-3], "source": source, "pack": pack, "vibes": vibes, "description": "", "credit": "",
                        "cost": "", "categories": [], "inputs": [], "error": None}
                if source == "bundled" and self._hidden_upload(n):
                    item["hides_upload"] = True     # an older upload of this name lies unused; delete by this id removes it
                try:
                    p = self._parsed(path)[0]
                    item.update(description=p["description"], credit=p["credit"], cost=p["cost"], categories=list(p["categories"]),
                                inputs=[dict(i) for i in p["inputs"]])
                    if pack == PACK_OWN and not default_in_vibes(p):
                        item["vibes"] = n in included
                except ShaderError as e:
                    item["error"] = str(e)
                    item["vibes"] = False
                out.append(item)
        return out

    def vibes_ids(self):
        """The shaders Vibes may pick from."""
        return [s["id"] for s in self.library() if s["vibes"] and not s["error"]]

    def _opt_in(self, sid):
        """True for a bundled shader that is out of Vibes until it is put in (see library)."""
        hit = self._bundled(sid)
        if not hit:
            return False
        if hit[1] != PACK_OWN:
            return True
        try:
            return not default_in_vibes(self._parsed(hit[0])[0])
        except ShaderError:
            return False

    # -- files for the player --
    def _write(self, text):
        rundir = self.api.player.rundir
        self._serial += 1
        name = os.path.join(rundir, "shader-%d-%d.glsl" % (os.getpid(), self._serial))
        tmp = name + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(text)
            os.replace(tmp, name)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return name

    def _cleanup(self, keep):
        """Remove the generated files that are no longer in use (and those an earlier run of the service left)."""
        rundir = self.api.player.rundir
        try:
            names = os.listdir(rundir)
        except OSError:
            return
        for n in names:
            full = os.path.join(rundir, n)
            if re.fullmatch(r"shader-\d+-\d+\.glsl(\.tmp)?", n) and full not in keep:
                try:
                    os.unlink(full)
                except OSError:
                    pass

    def tidy(self):
        """Remove generated shader texts nobody uses: called when the panel starts (an earlier panel process leaves
        its last text behind, and it stayed until the first play) and after Stop (the text of the shader that was on
        stayed until the next one). What the player has loaded is kept: the player outlives the panel, and it reads a
        text again when its video output starts anew. If the player runs and cannot say what it has loaded, nothing
        is removed."""
        with self._lock:
            player = self.api.player
            if not getattr(player, "rundir", None):
                return                              # a player with no folder of its own has no texts either
            keep = set()
            on = self.on_screen()
            if on:
                keep.add(on["path"])
            if getattr(player, "source_shader", None):
                keep.add(player.source_shader)
            try:
                up = player.is_running()
            except Exception:
                up = True                           # not known: ask it
            if up:
                try:
                    loaded = player.ipc.request("get_property", "glsl-shaders")
                except Exception:
                    return
                if not isinstance(loaded, list):
                    return
                keep.update(p for p in loaded if isinstance(p, str))
            self._cleanup(keep)

    # -- the player --
    is_carrier = staticmethod(is_carrier)

    def screen(self):
        try:
            size = self.api.player.osd_size()
        except Exception:
            size = None
        return tuple(size) if size else (1920, 1080)

    def frame_now(self, carrier):
        """The number of the carrier frame the player shows now (what a shader reads from the carrier's colour), or 0
        when `carrier` is not what plays (a carrier that starts counts from 0) or the player cannot say."""
        try:
            ipc = self.api.player.ipc
            if ipc.request("get_property", "path") != carrier:
                return 0
            t = ipc.request("get_property", "time-pos")
        except Exception:
            return 0
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t < 1e9:
            return 0
        return int(round(t * CARRIER_FPS)) % FRAME_WRAP

    def compose(self, parsed, size, state, desc):
        """The player's text for a shader in this state: {"values", "hue", "offset", "controls", "anchor"} (and any
        events that are held down, in "held")."""
        c = state["controls"]
        return translate(parsed, size, dict(state["values"], **state.get("held", {})), turn(state["hue"], c["hue"]), state["offset"],
                         desc, speed=c["speed"], gain=c["brightness"], anchor=state["anchor"])

    def _gpu_output(self):
        """True when the player draws with its GPU output, where a shader is compiled (not the null output of tests)."""
        try:
            return str(self.api.player.ipc.request("get_property", "current-vo")).startswith("gpu")
        except Exception:
            return False

    def _passes(self):
        try:
            p = self.api.player.ipc.request("get_property", "vo-passes")
        except Exception:
            return []
        return [x for key in ("fresh", "redraw") for x in ((p or {}).get(key) or []) if isinstance(x, dict)]

    def _fresh(self, desc):
        """The passes mpv drew for the newest frame that belong to the shader called `desc` (and to no other: "shader
        7 1" is not "shader 7 12"). The redraw list is left out: it can still hold the shader before this one."""
        try:
            p = self.api.player.ipc.request("get_property", "vo-passes")
        except Exception:
            return []
        mine = re.compile(re.escape(desc) + r"(?![0-9])")
        return [x for x in ((p or {}).get("fresh") or []) if isinstance(x, dict) and mine.search(str(x.get("desc", "")))]

    def _watch(self, tap, desc):
        """Wait until the player has drawn a frame with the shader called `desc` or has complained.
        ("ok" | "refused" | "unknown", message)."""
        lines, drawn, listed = [], False, None
        deadline = self._clock() + VERIFY_SECONDS
        while self._clock() < deadline and not drawn:
            lines += tap.drain(0.1)
            if shader_errors(lines):
                break
            named = re.compile(re.escape(desc) + r"(?![0-9])")
            mine = [x for x in self._passes() if named.search(str(x.get("desc", "")))]
            # A refused shader is listed too, with no time against it (seen in CI): only a pass that took time was
            # drawn. A GPU that reports no times at all gives "unknown" after a second, never "ok".
            drawn = any(isinstance(x.get(k), (int, float)) and x[k] > 0 for x in mine for k in ("avg", "last"))
            if mine and not drawn:
                listed = self._clock() if listed is None else listed
                if self._clock() - listed >= 1.0:
                    break
        lines += tap.drain(0.2)
        message = shader_errors(lines)
        if message:
            return "refused", message
        return ("ok" if drawn else "unknown"), ""

    def on_screen(self):
        """The shader that is on the screen now, or None: the player must still be playing the carrier it was given."""
        p = self.playing            # read once; nothing is written here (it runs from every status poll, unlocked)
        if p is None:
            return None
        player = self.api.player
        try:
            if player.source_epoch != p["epoch"] or player.ipc.request("get_property", "path") != p["carrier"]:
                return None
        except Exception:
            return None
        return p

    def show(self, sid, values=None, hue=0.0, offset=0.0, epoch=None, cut=True, controls=None, preset=None):
        """Put a shader on the screen. With `epoch`, only if nothing else was played since (None is returned then).
        `cut` (a preview from the panel) sets the picture's opacity like any other play; a rotation that fades by
        itself passes False. `controls` are speed, hue and brightness (see clean_controls); `preset` is only the
        name to remember for the values. Returns {"ok", "epoch", "id", "error"?}; ok False means the GPU refused it
        and the screen shows the shader before it, or black."""
        if not self.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")
        path, _ = self._path(sid)
        with self._lock:
            try:
                parsed, digest = self._parsed(path)
                cfg = self.config()
                size = render_size(self.screen(), cfg["height"])
                carrier = carrier_url(self.screen(), cfg.get("clock", CLOCKS[0]) == "carrier")
                desc = "nxlx shader %d %d" % (os.getpid(), self._serial + 1)      # mpv outlives this service: no two alike
                clean = clean_values(parsed, values)
                events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
                state = {"values": {n: v for n, v in clean.items() if n not in events},        # an event is never kept
                         "held": {n: True for n in clean if n in events and clean[n]},
                         "hue": _num(hue, "hue", 360.0), "offset": _num(offset, "offset", 1e9), "controls": self.limit(parsed, clean_controls(controls)),
                         "anchor": self.frame_now(carrier) if cfg.get("clock", CLOCKS[0]) == "carrier" else None}
                text = self.compose(parsed, size, state, desc)
                clean, key = state["values"], (digest, shape_of(parsed, clean))
            except ShaderError as e:
                raise ApiError(422, "%s: %s" % (sid, e))
            player = self.api.player
            before = self.on_screen()
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
                if cut:
                    self.api.fader.cancel()
                try:
                    new = player.play_source(out, carrier, epoch, getattr(self.api, "spawn", False))
                except PlayerError as e:
                    self._cleanup({before["path"]} if before else set())
                    raise ApiError(503, str(e))
                if new is None:                         # something else was played meanwhile: it keeps the screen
                    self._cleanup({before["path"]} if before else set())
                    return None
                verdict, message = self._watch(tap, desc) if tap else ("unknown", "")
            finally:
                if tap:
                    tap.close()
            if verdict == "refused":
                back = before if before and before["carrier"] == carrier and os.path.exists(before["path"]) else None
                keep = back["path"] if back else None
                try:
                    keep = keep or self._write(BLACK)       # nothing to go back to: black, never the bare carrier
                    player.swap_source(keep, new)
                except (PlayerError, OSError):
                    pass
                self.playing = dict(back, epoch=new) if back else None
                self._cleanup({keep} if keep else set())
                self.error = {"id": sid, "message": message, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                self.refused(sid, digest, message)
                self.log("pvj-web: shader %s refused by the player: %s" % (sid, message))
                return {"ok": False, "epoch": new, "id": sid, "error": message, "showing": back["id"] if back else None}
            if verdict == "ok":
                if len(self._checked) > 2048:
                    self._checked.clear()
                self._checked.add(key)
            if self.error and self.error["id"] == sid:
                self.error = None
            self.playing = {"id": sid, "values": clean, "hue": state["hue"], "offset": state["offset"], "controls": state["controls"],
                            "anchor": state["anchor"], "path": out, "carrier": carrier, "epoch": new, "desc": desc,
                            "digest": digest, "size": size, "preset": preset, "held": state["held"],
                            "checked": True if (verdict == "ok" or key in self._checked) else None}
            self._cleanup({out})
            if cut:
                self.api._apply_opacity(0 if self.api.mix["blackout"] else self.api.mix["opacity"])
            self.api._started_playing()
            return {"ok": True, "epoch": new, "id": sid}

    def refused(self, sid, digest, message):
        """The GPU refused this file (see shaderlive.py, which remembers it until the file changes)."""

    def limit(self, parsed, controls):
        """The controls as this shader may have them (see shaderlive.py: the flash limit of Performance shaders)."""
        return controls

    def off(self, epoch=None):
        """The module was switched off, or Vibes was stopped: take the shader off the screen if one is on. With
        `epoch`, also stop a bare carrier (black, after a refused shader) that this epoch put there."""
        with self._lock:
            player = self.api.player
            if epoch is None:
                epoch = self.playing["epoch"] if self.playing else None
            if epoch is not None:
                try:                        # the player checks the epoch and stops in one step: a clip started
                    player.clear_source(epoch)      # in between is never stopped
                except PlayerError:
                    pass
            if self.on_screen() is None:    # nothing of ours is showing (another shader may have taken the screen)
                self.playing = None
                self._cleanup({player.source_shader} if player.source_shader else set())

    # -- uploads --
    def upload(self, name, source, replace=False):
        """Store an ISF file from a full-access device. It is checked and translated first; what cannot be shown is
        refused with the reason and never stored."""
        if not isinstance(name, str) or not FILE.fullmatch(name) or not valid_name(name):
            raise ApiError(400, "a shader file is named with letters, digits, spaces or . _ - and ends in .fs")
        if not isinstance(source, str):
            raise ApiError(400, "send the shader text")
        if not isinstance(replace, bool):
            raise ApiError(400, "replace must be true or false")
        if self._bundled(name):
            raise ApiError(409, "%s is the name of a bundled shader; choose another name" % name)
        try:
            data = source.encode("utf-8")
            translate(parse(data), (1280, 720))
        except UnicodeEncodeError:
            raise ApiError(422, "%s: the file is not plain text" % name)
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (name, e))
        with self._lock:
            try:
                os.makedirs(self.dir, mode=0o750, exist_ok=True)
            except OSError as e:
                raise ApiError(500, "the shaders folder is not writable: %s" % (e.strerror or e))
            if os.path.islink(self.dir):
                raise ApiError(500, "the shaders folder is a link; refusing to write there")
            final = os.path.join(self.dir, name)
            exists = os.path.lexists(final)
            if exists and not replace:
                raise ApiError(409, "a shader with that name already exists")
            if os.path.islink(final):
                raise ApiError(409, "refusing to replace a link")
            # a file hidden behind a bundled shader of the same name is not one of the owner's usable uploads
            if not exists and len([n for n in self._names(self.dir) if not self._bundled(n)]) >= MAX_UPLOADS:
                raise ApiError(409, "at most %d uploaded shaders; delete one first" % MAX_UPLOADS)
            tmp = os.path.join(self.dir, ".upload-%d-%d" % (os.getpid(), threading.get_ident()))
            try:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                if replace:
                    os.replace(tmp, final)
                else:
                    try:
                        os.link(tmp, final)             # never overwrites silently
                    except FileExistsError:
                        raise ApiError(409, "a shader with that name already exists")
                    os.unlink(tmp)
            except OSError as e:
                raise ApiError(500, "could not store the shader: %s" % (e.strerror or e))
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return {"name": name, "size": len(data)}

    def delete(self, sid):
        path, source = self._path(sid)
        if source != "uploaded":
            # An upload of the same name that was there before the bundled file came is hidden behind it (the
            # library says so with "hides_upload"): deleting by that name removes the upload, never the bundled file.
            path = self._hidden_upload(sid)
            if not path:
                raise ApiError(409, "a bundled shader cannot be deleted; switch it off for Vibes instead")
        with self._lock:
            try:
                os.unlink(path)
            except OSError as e:
                raise ApiError(500, "could not delete: %s" % (e.strerror or e))
            if self.error and self.error["id"] == sid:      # a refusal of a file that is gone says nothing any more
                self.error = None
            cfg = self.config()
            hit = self._bundled(sid)        # one of the project's own keeps its entry: there it is that shader's switch
            if sid in cfg["disabled"] and not (hit and hit[1] == PACK_OWN):
                cfg["disabled"] = [n for n in cfg["disabled"] if n != sid]
                self._save(cfg)

    # -- requests --
    def state(self):
        cfg = self.config()
        showing = self.on_screen() if self.enabled() else None
        playing = None
        if showing:
            playing = {"id": showing["id"], "name": showing["id"][:-3], "values": dict(showing["values"]), "checked": showing["checked"]}
            for x in self._fresh(showing.get("desc") or "nxlx shader"):     # its own pass, never the one before it
                if isinstance(x.get("avg"), (int, float)) and not isinstance(x["avg"], bool) and x["avg"] > 0:
                    playing["pass_ms"] = round(x["avg"] / 1e6, 2)
        vibes = getattr(self.api, "vibes", None)
        size = render_size(self.screen(), cfg["height"])
        return {"enabled": self.enabled(), "shaders": self.library() if self.enabled() else [], "playing": playing,
                "error": self.error, "vibes": vibes.status() if vibes else {"running": False},
                "config": {"dwell": cfg["dwell"], "vary": cfg["vary"], "height": cfg["height"]},
                "render": {"width": size[0], "height": size[1], "fps": CARRIER_FPS, "heights": list(HEIGHTS)},
                "limits": {"bytes": MAX_SOURCE, "inputs": MAX_INPUTS, "uploads": MAX_UPLOADS, "dwell": [DWELL_MIN, DWELL_MAX]}}

    def _need(self):
        if not self.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")

    def api_get(self, body, device, client):
        return self.state()

    def api_play(self, body, device, client):
        """{"id": "name.fs", "values"?: {input: number}}: show one shader until something else is played."""
        self._need()
        vibes = getattr(self.api, "vibes", None)
        if vibes:
            vibes.yield_screen()                    # the operator chose a shader: the rotation ends
        try:
            result = self.show(body.get("id"), body.get("values"))
        except ShaderError as e:
            raise ApiError(422, str(e))
        if not result["ok"]:
            if not result["showing"]:
                self.off(result["epoch"])           # nothing to go back to: stop, which leaves the screen black
            raise ApiError(422, "the player refused %s: %s. %s" % (
                result["id"], result["error"], "The shader before it is back on." if result["showing"] else "The screen is black."))
        return self.state()

    def api_set(self, body, device, client):
        """One change (full access): {"action": "upload", "name", "source", "replace"?}, {"action": "delete", "id"},
        {"action": "vibes", "id", "on"} (in or out of the Vibes rotation), {"action": "config", "dwell"?, "vary"?,
        "height"?}."""
        self._need()
        action = body.get("action")
        if action == "upload":
            self.upload(body.get("name"), body.get("source"), body.get("replace", False))
        elif action == "delete":
            self.delete(body.get("id"))
        elif action == "vibes":
            sid, on = body.get("id"), body.get("on")
            self._path(sid)
            if not isinstance(on, bool):
                raise ApiError(400, "on must be true or false")
            cfg = self.config()
            if self._opt_in(sid):                   # a third-party pack's shader, or one of ours made for performing
                cfg["included"] = [n for n in cfg.get("included", []) if n != sid] + ([sid] if on else [])
            else:
                cfg["disabled"] = [n for n in cfg["disabled"] if n != sid] + ([] if on else [sid])
            self._save(cfg)
        elif action == "config":
            cfg = self.config()
            if "dwell" in body:
                d = body["dwell"]
                if isinstance(d, bool) or not isinstance(d, (int, float)) or d != d or not DWELL_MIN <= d <= DWELL_MAX:
                    raise ApiError(400, "each shader stays %d to %d seconds" % (DWELL_MIN, DWELL_MAX))
                cfg["dwell"] = int(d)
            if "vary" in body:
                if not isinstance(body["vary"], bool):
                    raise ApiError(400, "vary must be true or false")
                cfg["vary"] = body["vary"]
            if "height" in body:
                if isinstance(body["height"], bool) or body["height"] not in HEIGHTS:
                    raise ApiError(400, "height must be one of %s" % ", ".join(str(h) for h in HEIGHTS))
                cfg["height"] = body["height"]
            self._save(cfg)
        else:
            raise ApiError(400, "action must be upload, delete, vibes or config")
        return self.state()
