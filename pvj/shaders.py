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

from .api import ApiError, valid_name
from .player import PlayerError

MAX_SOURCE = 32 * 1024        # bytes of one ISF file (it must also fit a JSON request)
MAX_HEADER = 8 * 1024
MAX_INPUTS = 24
MAX_UPLOADS = 64
MAX_TEXT = 200                # description, credit
CARRIER_FPS = 30
HEIGHTS = (360, 540, 720, 1080)
DEFAULT_HEIGHT = 720
DWELL_MIN, DWELL_MAX, DWELL_DEFAULT = 10, 3600, 180
VERIFY_SECONDS = 4.0
BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "shaders.d")
FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}\.fs")
INPUT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
CARRIER = re.compile(r"av://lavfi:color=c=black:size=[0-9]{1,4}x[0-9]{1,4}:rate=%d,format=rgb0" % CARRIER_FPS)
TYPES = ("float", "bool", "long", "color", "point2D", "event")
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
_MAIN = re.compile(r"\bvoid\s+main\s*\(\s*(?:void)?\s*\)")
_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(\w*)", re.M)
_ALLOWED_DIRECTIVES = ("define", "undef", "if", "ifdef", "ifndef", "else", "elif", "endif")
_GLOBAL_IO = re.compile(r"^[ \t]*(?:uniform|varying|attribute|layout|in|out)\b", re.M)
_IMG = re.compile(r"\bIMG_(?:PIXEL|NORM_PIXEL|THIS_PIXEL|THIS_NORM_PIXEL|SIZE)\b")
_OWN = re.compile(r"\b(?:pvj_\w*|HOOKED\w*|PASSINDEX)\b")


class ShaderError(ValueError):
    """An ISF file this box will not take; the message says why in plain words."""


# ---- numbers ---------------------------------------------------------------------------------------------------------
def _num(v, what, limit=1e6):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > limit:
        raise ShaderError("%s must be a number" % what)
    return float(v)


def _f(x):
    """A GLSL float literal (finite numbers only)."""
    s = "%.7g" % _num(x, "a value", 1e9)
    return s if ("." in s or "e" in s) else s + ".0"


def _text(v, limit=MAX_TEXT):
    """A short line of plain text for the panel (control characters dropped)."""
    if not isinstance(v, str):
        return ""
    return "".join(ch for ch in v if ch >= " " and ch != "\x7f")[:limit].strip()


# ---- the ISF header ----------------------------------------------------------------------------------------------------
def _input(spec, seen):
    if not isinstance(spec, dict):
        raise ShaderError("each input must be an object")
    name, kind = spec.get("NAME"), spec.get("TYPE")
    if not isinstance(name, str) or not INPUT_NAME.fullmatch(name):
        raise ShaderError("an input name must be 1 to 32 letters, digits or _ and start with a letter")
    if name in RESERVED or name.startswith(("gl_", "pvj_", "isf_", "HOOKED")) or "__" in name:
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
            if "DEFAULT" not in spec:
                d = values[0]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d != int(d) or abs(d) > 100000:
            raise ShaderError("DEFAULT of %s must be a whole number" % name)
        if values is not None and int(d) not in values:
            raise ShaderError("DEFAULT of %s is not one of its VALUES" % name)
        out["default"] = int(d)
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
    else:                                   # an event: a button in ISF; it is never pressed here
        out["default"] = False
    return out


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
        head = json.loads(source[start + 2:end])
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
    bad = sorted({ch for ch in body if not (" " <= ch <= "~" or ch in "\n\t")})
    if bad:
        raise ShaderError("the shader code may hold plain ASCII text only (found %r)" % bad[0])
    for m in _DIRECTIVE.finditer(body):
        if m.group(1) not in _ALLOWED_DIRECTIVES:
            raise ShaderError("the line #%s is not allowed (no includes, versions, extensions or pragmas)" % (m.group(1) or "?"))
    if _GLOBAL_IO.search(body):
        raise ShaderError("the code declares its own uniform, varying, in or out, which the player cannot fill")
    if _IMG.search(body):
        raise ShaderError("it reads a picture (IMG_PIXEL and the like): only generator shaders are supported")
    own = _OWN.search(body)
    if own:
        raise ShaderError("the name %s is used by the player; rename it" % own.group(0))
    if len(_MAIN.findall(body)) != 1:
        raise ShaderError("the code must have exactly one void main()")
    return {"description": _text(head.get("DESCRIPTION")), "credit": _text(head.get("CREDIT")), "cost": _text(head.get("COST")),
            "inputs": clean, "body": body, "line": source.count("\n", 0, end + 2) + 1}


def clean_values(parsed, values):
    """{name: number} for the float inputs, from untrusted input: unknown names are refused, numbers are kept inside
    each input's MIN and MAX."""
    if values is None:
        return {}
    if not isinstance(values, dict) or len(values) > MAX_INPUTS:
        raise ShaderError("values must be an object of input name and number")
    floats = {i["name"]: i for i in parsed["inputs"] if i["type"] == "float"}
    out = {}
    for name, v in values.items():
        if name not in floats:
            raise ShaderError("no number input called %s" % _text(str(name), 32))
        i = floats[name]
        out[name] = min(i["max"], max(i["min"], _num(v, name)))
    return out


def hue_matrix(degrees):
    """The 3x3 matrix (row-major) that turns colours around the grey axis by `degrees`: a palette shift that keeps
    black, white and greys as they are."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    k, r = (1.0 - c) / 3.0, s / math.sqrt(3.0)
    return [c + k, k - r, k + r, k + r, c + k, k - r, k - r, k + r, c + k]


def translate(parsed, size, values=None, hue=0.0, offset=0.0, desc="nxlx shader", today=None):
    """The mpv user shader for a parsed ISF generator, drawn at `size` (width, height). `values` replaces float
    defaults, `hue` (degrees) shifts the palette, `offset` (seconds) moves the start of TIME."""
    width, height = int(size[0]), int(size[1])
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise ShaderError("bad drawing size")
    if not re.fullmatch(r"[a-z0-9 ]{1,40}", desc):
        raise ShaderError("bad description")
    values = clean_values(parsed, values)
    hue, offset = _num(hue, "hue", 360.0), _num(offset, "offset", 100000.0)
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
        d = i["default"]
        if i["type"] == "float":
            lines.append("const float %s = %s;" % (i["name"], _f(values.get(i["name"], d))))
        elif i["type"] in ("bool", "event"):
            lines.append("const bool %s = %s;" % (i["name"], "true" if d else "false"))
        elif i["type"] == "long":
            lines.append("const int %s = %d;" % (i["name"], d))
        elif i["type"] == "color":
            lines.append("const vec4 %s = vec4(%s);" % (i["name"], ", ".join(_f(c) for c in d)))
        else:
            lines.append("const vec2 %s = vec2(%s, %s);" % (i["name"], _f(d[0]), _f(d[1])))
    body = re.sub(r"\bgl_FragColor\b", "pvj_color", parsed["body"])
    body = re.sub(r"\bgl_FragCoord\b", "pvj_coord", body)
    body = _MAIN.sub("void pvj_main()", body)
    lines += ["#line %d" % parsed["line"], body.rstrip("\n"), "",
              "vec4 hook() {",
              # frame = hi * 512 + lo, in whole numbers small enough for 16 bits; hi starts again after 8192 (38.8 hours)
              "    int pvj_hi = frame / 512;",
              "    int pvj_lo = frame - pvj_hi * 512;",
              "    pvj_hi = pvj_hi - (pvj_hi / 8192) * 8192;",
              "    PVJ_HP float pvj_k = 512.0;",
              "    pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / %s + %s;" % (_f(CARRIER_FPS), _f(offset)),
              "    pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y);",
              "    pvj_coord = vec4(pvj_norm * RENDERSIZE, 0.0, 1.0);",
              "    pvj_color = vec4(0.0, 0.0, 0.0, 1.0);",
              "    pvj_main();",
              "    vec3 c = clamp(pvj_color.rgb, 0.0, 1.0) * clamp(pvj_color.a, 0.0, 1.0);"]
    if hue:
        h = hue_matrix(hue)
        lines.append("    c = clamp(mat3(%s) * c, 0.0, 1.0);" % ", ".join(_f(h[r * 3 + col]) for col in range(3) for r in range(3)))
    lines += ["    c += (fract(sin(dot(pvj_coord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;",
              "    return vec4(c, 1.0);",
              "}", ""]
    return "\n".join(lines)


# ---- the carrier -------------------------------------------------------------------------------------------------------
def render_size(screen, height):
    """(width, height) the shader is drawn at: `height` lines (never more than the screen has) in the screen's shape."""
    sw, sh = screen
    h = max(16, min(int(height), sh, 1080))
    w = max(16, min(1920, int(round(h * sw / float(sh) / 2.0)) * 2))
    return w, h


def carrier_url(screen):
    """The blank picture the shader is drawn over: black, RGB, 30 frames a second, in the screen's exact shape and
    as small as that shape allows (the shader sets its own size, so the carrier costs next to nothing). Built from
    whole numbers only; it is the same kind of address as the test pattern, which the hardened player already plays."""
    sw, sh = int(screen[0]), int(screen[1])
    g = math.gcd(sw, sh)
    aw, ah = sw // g, sh // g
    m = max(1, -(-36 // ah))
    return "av://lavfi:color=c=black:size=%dx%d:rate=%d,format=rgb0" % (aw * m, ah * m, CARRIER_FPS)


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


_COMPILER_LINE = re.compile(r"^(?:ERROR: )?\d+:(\d+)(?:\(\d+\))?: (?:error: )?(.*)$")


def shader_errors(lines):
    """What the player said about a shader it refused, as one short message, or "" if it said nothing of the kind.
    mpv prints the whole shader and then the GPU compiler's log, all at error level, from its video output."""
    mine = [t for p, level, t in lines if level in ("error", "fatal") and p.startswith("vo/")
            and not re.match(r"^\[\s*\d+\] ", t)]
    if not any(re.search(r"shader|compile|link log|Unrecognized command|hook", t, re.I) for t in mine):
        return ""
    said = []
    for t in mine:
        m = _COMPILER_LINE.match(t.strip())
        if m:
            said.append("line %s: %s" % (m.group(1), m.group(2)))
    if not said:
        said = [t for t in mine if t.strip() and not re.search(r"shader source:|compile log|link log", t)]
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
        self._checked = set()                   # (source hash) of shaders the GPU has taken
        self.playing = None                     # {"id", "values", "hue", "path", "carrier", "epoch", "checked"}
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

    def _path(self, sid):
        """(path, "bundled" or "uploaded") of a shader by its file name; never a path outside the two folders."""
        if not isinstance(sid, str) or not FILE.fullmatch(sid) or not valid_name(sid):
            raise ApiError(400, "invalid shader name")
        path = os.path.join(self.bundled_dir, sid)
        if os.path.isfile(path):
            return path, "bundled"
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
                if len(self._cache) > 4 * MAX_UPLOADS:
                    self._cache.clear()
                hit = self._cache[path] = ((st.st_mtime_ns, st.st_size), result)
        except OSError as e:
            raise ShaderError("cannot read the file: %s" % (e.strerror or e))
        if isinstance(hit[1], ShaderError):
            raise hit[1]
        return hit[1]

    def library(self):
        """[{"id", "name", "source", "description", "credit", "cost", "vibes", "inputs", "error"}], bundled first."""
        disabled = set(self.config()["disabled"])
        out = []
        folders = [(self.bundled_dir, "bundled")] + ([] if os.path.islink(self.dir) else [(self.dir, "uploaded")])
        seen = set()
        for folder, source in folders:
            for n in self._names(folder)[:MAX_UPLOADS + 64]:
                path = os.path.join(folder, n)
                if n in seen or os.path.islink(path) or not os.path.isfile(path):
                    continue
                seen.add(n)
                item = {"id": n, "name": n[:-3], "source": source, "vibes": n not in disabled, "description": "", "credit": "",
                        "cost": "", "inputs": [], "error": None}
                try:
                    p = self._parsed(path)[0]
                    item.update(description=p["description"], credit=p["credit"], cost=p["cost"],
                                inputs=[dict(i) for i in p["inputs"]])
                except ShaderError as e:
                    item["error"] = str(e)
                    item["vibes"] = False
                out.append(item)
        return out

    def vibes_ids(self):
        """The shaders Vibes may pick from."""
        return [s["id"] for s in self.library() if s["vibes"] and not s["error"]]

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

    # -- the player --
    is_carrier = staticmethod(is_carrier)

    def screen(self):
        try:
            size = self.api.player.osd_size()
        except Exception:
            size = None
        return tuple(size) if size else (1920, 1080)

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

    def _watch(self, tap, desc):
        """Wait until the player has drawn a frame with the shader called `desc` or has complained.
        ("ok" | "refused" | "unknown", message)."""
        lines, drawn, listed = [], False, None
        deadline = self._clock() + VERIFY_SECONDS
        while self._clock() < deadline and not drawn:
            lines += tap.drain(0.1)
            if shader_errors(lines):
                break
            mine = [x for x in self._passes() if desc in str(x.get("desc", ""))]
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
        p = self.playing
        if p is None:
            return None
        player = self.api.player
        try:
            if player.source_epoch != p["epoch"] or player.ipc.request("get_property", "path") != p["carrier"]:
                p = None
        except Exception:
            p = None
        if p is None:
            self.playing = None
        return p

    def show(self, sid, values=None, hue=0.0, offset=0.0, epoch=None, cut=True):
        """Put a shader on the screen. With `epoch`, only if nothing else was played since (None is returned then).
        `cut` (a preview from the panel) sets the picture's opacity like any other play; a rotation that fades by
        itself passes False. Returns {"ok", "epoch", "id", "error"?}; ok False means the GPU refused it and the
        screen shows the shader before it, or black."""
        if not self.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")
        path, _ = self._path(sid)
        with self._lock:
            try:
                parsed, digest = self._parsed(path)
                size = render_size(self.screen(), self.config()["height"])
                desc = "nxlx shader %d %d" % (os.getpid(), self._serial + 1)      # mpv outlives this service: no two alike
                text = translate(parsed, size, values, hue, offset, desc)
                clean = clean_values(parsed, values)
            except ShaderError as e:
                raise ApiError(422, "%s: %s" % (sid, e))
            player = self.api.player
            carrier = carrier_url(self.screen())
            before = self.on_screen()
            try:
                out = self._write(text)
            except OSError as e:
                raise ApiError(500, "could not write the shader: %s" % (e.strerror or e))
            tap = None
            if digest not in self._checked and self._gpu_output():
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
                try:
                    player.swap_source(back["path"] if back else None, new)
                except PlayerError:
                    pass
                self.playing = dict(back, epoch=new) if back else None
                self._cleanup({back["path"]} if back else set())
                self.error = {"id": sid, "message": message, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                self.log("pvj-web: shader %s refused by the player: %s" % (sid, message))
                return {"ok": False, "epoch": new, "id": sid, "error": message, "showing": back["id"] if back else None}
            if verdict == "ok":
                self._checked.add(digest)
            if self.error and self.error["id"] == sid:
                self.error = None
            self.playing = {"id": sid, "values": clean, "hue": float(hue), "path": out, "carrier": carrier, "epoch": new,
                            "checked": True if (verdict == "ok" or digest in self._checked) else None}
            self._cleanup({out})
            if cut:
                self.api._apply_opacity(0 if self.api.mix["blackout"] else self.api.mix["opacity"])
            self.api._started_playing()
            return {"ok": True, "epoch": new, "id": sid}

    def off(self, epoch=None):
        """The module was switched off, or Vibes was stopped: take the shader off the screen if one is on. With
        `epoch`, also stop a bare carrier (black, after a refused shader) that this epoch put there."""
        with self._lock:
            player = self.api.player
            bare = False
            if epoch is not None and self.playing is None:
                try:
                    bare = player.source_epoch == epoch and is_carrier(player.ipc.request("get_property", "path"))
                except Exception:
                    bare = False
            if self.on_screen() is not None or bare:
                try:
                    self.api.player.clear()
                except PlayerError:
                    pass
            self.playing = None
            self._cleanup(set())

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
        if os.path.isfile(os.path.join(self.bundled_dir, name)):
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
            if not exists and len(self._names(self.dir)) >= MAX_UPLOADS:
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
            raise ApiError(409, "a bundled shader cannot be deleted; switch it off for Vibes instead")
        with self._lock:
            try:
                os.unlink(path)
            except OSError as e:
                raise ApiError(500, "could not delete: %s" % (e.strerror or e))
            cfg = self.config()
            if sid in cfg["disabled"]:
                cfg["disabled"] = [n for n in cfg["disabled"] if n != sid]
                self._save(cfg)

    # -- requests --
    def state(self):
        cfg = self.config()
        showing = self.on_screen() if self.enabled() else None
        playing = None
        if showing:
            playing = {"id": showing["id"], "name": showing["id"][:-3], "values": dict(showing["values"]), "checked": showing["checked"]}
            for x in self._passes():
                if "nxlx shader" in str(x.get("desc", "")) and isinstance(x.get("avg"), (int, float)) and x["avg"] > 0:
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
