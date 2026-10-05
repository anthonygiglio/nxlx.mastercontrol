# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Effects: ISF filter shaders applied over whatever is playing (a clip, a stream, a live input).

An effect is a third kind beside a clip and a generator shader. It never takes the screen: it is put on OVER the
picture that plays and changes it, and it stays on when the clip changes, until it is taken off, Stop is pressed, a
generator shader (or Vibes) takes the screen, the module goes off or the player restarts. One effect at a time in
this first version; a chain of several is a later step.

Where it sits in the player (settled by experiment on a real mpv in CI, tests/test_effects_gpu.py):
* A filter has to work on RGB, and it has to sit before the brightness that the panel's opacity, fades and Blackout
  use, or a filter that adds light (an invert) would undo a Blackout. mpv applies that brightness while it converts
  the clip's own colours (YUV for almost every video) to RGB. The stages after that (MAINPRESUB, MAIN) are RGB but
  too late; the one stage before it, NATIVE, is not RGB for a video. So the effect hooks NATIVE and does the
  conversion itself: every pixel it reads is turned into RGB, the filter works, and the result is turned back into
  the clip's own colours, so that the player's conversion, with the brightness in it, still comes afterwards. The
  way back is the exact inverse of the way in, and the "amount" mix is done after it, so amount 0 gives back
  exactly the pixel that was read.
* NATIVE is the picture at the clip's own size, before any scaling: the filter sees the whole picture and nothing
  else whether mpv letterboxes it or (while a projection mapping is on) stretches it, and the mapping, which hooks
  OUTPUT at the other end, shows the filtered picture.
* The text of an effect depends on the picture under it: its colour matrix and range (for the conversion) and its
  frame rate (for TIME). A worker looks at the picture once a second while an effect is on and writes a new text
  when another kind of picture has started.
* TIME, for a filter that moves by itself, is counted from mpv's own frame number and the clip's frame rate. Nothing
  outside the shader can read that number, so a change of the speed makes TIME jump (the generators have a carrier
  that counts its own frames; a clip has none).

Everything else is the generators' machinery (shaders.py, shaderlive.py): the translator and its refusals, values as
constants in a fresh text, the worker that applies the newest values at most five times a second, the memory of what
the GPU refused, presets, and a guard that counts dropped frames.
"""

import math
import os
import re
import threading
import time

from . import shaderlive as L, shaders as S
from .api import ApiError
from .player import PlayerError
from .shaders import ShaderError

EFFECTS_DIR = os.path.join(os.path.dirname(__file__), "effects.d")
WATCH = 1.0                     # seconds between two looks at the picture under the effect
MAX_READS = 64                  # an upload may read the picture at most this often for one pixel (a count from its text)
MAX_ROUNDS = 256                # and its loops may run at most this many rounds for one pixel
UNKNOWN_ROUNDS = 32             # what a loop counts for when its length cannot be read from the text
DEFAULT_FPS = 30.0
# Kr and Kb of the colour matrices mpv names in video-params/colormatrix. Anything else is treated as BT.709.
MATRICES = {"bt.601": (0.299, 0.114), "bt.709": (0.2126, 0.0722), "bt.2020-ncl": (0.2627, 0.0593),
            "bt.2020-cl": (0.2627, 0.0593), "smpte-240m": (0.212, 0.087)}
ENDED = {"stop": "Stop was pressed", "generator": "a generator shader took the screen", "restart": "the player was restarted",
         "module": "the module was switched off", "panel": "the panel was restarted"}


# ---- colours ---------------------------------------------------------------------------------------------------------
def _inverse(m):
    """The inverse of a 3 x 3 matrix given as three rows."""
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def native_maps(colormatrix, levels):
    """(to_rgb, offset, to_native) for a picture whose colours are `colormatrix` ("rgb", "bt.709", ...) at `levels`
    ("limited" or "full"): rgb = to_rgb x (native - offset) and native = to_native x rgb + offset, each matrix as
    three rows. For RGB all three are the identity. The numbers follow mpv's own conversion for 8 bits; for deeper
    pictures they are off by less than half a percent, and since the way back is the exact inverse of the way in,
    a filter that changes nothing gives back exactly what it read."""
    if colormatrix == "rgb":
        one = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        return one, [0.0, 0.0, 0.0], one
    kr, kb = MATRICES.get(colormatrix, MATRICES["bt.709"])
    kg = 1.0 - kr - kb
    if levels == "full":
        ymin, ys, cs = 0.0, 1.0, 255.0 / 254.0
    else:
        ymin, ys, cs = 16.0 / 255.0, 255.0 / 219.0, 255.0 / 224.0
    cmid = 128.0 / 255.0
    to_rgb = [[ys, 0.0, 2.0 * (1.0 - kr) * cs],
              [ys, -2.0 * kb * (1.0 - kb) / kg * cs, -2.0 * kr * (1.0 - kr) / kg * cs],
              [ys, 2.0 * (1.0 - kb) * cs, 0.0]]
    return to_rgb, [ymin, cmid, cmid], _inverse(to_rgb)


def _g(x):
    s = "%.9g" % x
    return s if ("." in s or "e" in s) else s + ".0"


def _mat3(rows):
    """A GLSL mat3 (its constructor takes columns) from three rows."""
    return "mat3(%s)" % ", ".join(_g(rows[r][c]) for c in range(3) for r in range(3))


def native_glsl(colormatrix, levels, prefix="pvj"):
    """The GLSL for the two conversions: `vec3 <prefix>_rgb(vec3 native)` (kept inside 0 to 1) and
    `vec3 <prefix>_native(vec3 rgb)`."""
    to_rgb, off, to_native = native_maps(colormatrix, levels)
    if colormatrix == "rgb":
        return ["vec3 %s_rgb(vec3 n) { return clamp(n, 0.0, 1.0); }" % prefix, "vec3 %s_native(vec3 c) { return c; }" % prefix]
    o = "vec3(%s)" % ", ".join(_g(v) for v in off)
    return ["vec3 %s_rgb(vec3 n) { return clamp(%s * (n - %s), 0.0, 1.0); }" % (prefix, _mat3(to_rgb), o),
            "vec3 %s_native(vec3 c) { return %s * c + %s; }" % (prefix, _mat3(to_native), o)]


def clean_picture(matrix=None, levels=None, fps=None):
    """What an effect's text needs to know of the picture under it, from what the player says: {"matrix", "levels",
    "fps"}. Anything the player cannot say becomes BT.709, limited range and 30 pictures a second."""
    if matrix != "rgb" and matrix not in MATRICES:
        matrix = "bt.709"
    if levels not in ("limited", "full"):
        levels = "limited"
    ok = isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps == fps and 1.0 <= fps <= 240.0
    return {"matrix": matrix, "levels": levels, "fps": round(float(fps), 3) if ok else DEFAULT_FPS}


# ---- the text for the player ---------------------------------------------------------------------------------------------
def _block(parsed, values, c, pic, desc, plane, t):
    """One hook of the effect's text, for a picture that has the plane `plane`: "LUMA" (the picture is YUV and is
    converted with its matrix and range) or "RGB" (nothing to convert)."""
    lines = ["//!HOOK NATIVE", "//!BIND HOOKED",
             # mpv leaves a hook out when a texture it binds is not there (seen in CI on mpv 0.37): a video has a LUMA
             # plane and no RGB plane, an RGB picture the other way round, so exactly one of the two hooks runs
             "//!BIND %s" % plane]
    if c["half"]:
        lines += ["//!WIDTH HOOKED.w 2 /", "//!HEIGHT HOOKED.h 2 /"]
    size = "(HOOKED_size * 0.5)" if c["half"] else "HOOKED_size"
    lines += ["//!DESC %s" % desc, "",
              "// %s" % desc,              # the name again, inside the code: see shaders.translate
              "#if defined(GL_ES) && (__VERSION__ >= 300 || defined(GL_FRAGMENT_PRECISION_HIGH))",
              "precision highp float;", "precision highp int;", "#define PVJ_HP highp",
              "#else", "#define PVJ_HP", "#endif",
              # In ISF a filter is drawn at the size of its picture, so both sizes are the same thing here, also at
              # half size: the picture is then read as one of half the width and height.
              "#define RENDERSIZE %s" % size,
              "#define TIME pvj_time",
              "#define TIMEDELTA %s" % S._f(1.0 / pic["fps"]),
              "#define FRAMEINDEX frame",
              "#define PASSINDEX 0",
              "#define DATE vec4(%s, %s, %s, %s)" % (S._f(t.tm_year), S._f(t.tm_mon), S._f(t.tm_mday), S._f(t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)),
              "#define isf_FragNormCoord pvj_norm",
              "#define vv_FragNormCoord pvj_norm",
              "PVJ_HP float pvj_time;", "vec2 pvj_norm;", "vec4 pvj_coord;", "vec4 pvj_color;"]
    lines += native_glsl("rgb" if plane == "RGB" else ("bt.709" if pic["matrix"] == "rgb" else pic["matrix"]), pic["levels"])
    # The picture, read at a place counted from the top left as the player counts (0 to 1); outside it, its edge.
    lines += ["vec4 pvj_at(vec2 p) { return vec4(pvj_rgb(HOOKED_tex(clamp(p, vec2(0.0), vec2(1.0))).rgb), 1.0); }",
              "vec4 pvj_img_this() { return pvj_at(HOOKED_pos); }",
              "vec4 pvj_img_norm(vec2 n) { return pvj_at(vec2(n.x, 1.0 - n.y)); }",
              "vec4 pvj_img_px(vec2 p) { return pvj_at(vec2(p.x, RENDERSIZE.y - p.y) / RENDERSIZE); }",
              "vec2 pvj_img_size() { return RENDERSIZE; }"]
    lines += S.input_lines(parsed, values)
    lines += ["#line %d" % parsed["line"], parsed["code"], "",
              "vec4 hook() {",
              "    vec4 pvj_src = HOOKED_tex(HOOKED_pos);"]
    if parsed.get("clock"):
        # mpv's frame number as hi * 512 + lo, in whole numbers small enough for 16 bits (see shaders.translate)
        lines += ["    int pvj_hi = frame / 512;",
                  "    int pvj_lo = frame - pvj_hi * 512;",
                  "    pvj_hi = pvj_hi - (pvj_hi / 8192) * 8192;",
                  "    PVJ_HP float pvj_k = 512.0;",
                  "    pvj_time = (float(pvj_hi) * pvj_k + float(pvj_lo)) / %s%s;" % (S._f(pic["fps"]), "" if c["speed"] == 1.0 else " * %s" % S._f(c["speed"]))]
    else:
        lines.append("    pvj_time = 0.0;")
    lines += ["    pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y);",
              "    pvj_coord = vec4(pvj_norm * RENDERSIZE, 0.0, 1.0);",
              "    pvj_color = vec4(0.0, 0.0, 0.0, 1.0);",
              "    pvj_main();",
              "    vec3 c = clamp(pvj_color.rgb, 0.0, 1.0) * clamp(pvj_color.a, 0.0, 1.0);",
              # back into the picture's own colours, then the mix: at amount 0 this is the pixel that was read
              "    return vec4(mix(pvj_src.rgb, pvj_native(c), %s), pvj_src.a);" % S._f(c["amount"]),
              "}", ""]
    return lines


def translate(parsed, values=None, controls=None, picture=None, desc="nxlx effect", today=None):
    """The mpv user shader for a parsed ISF filter: `values` replace the inputs' defaults, `controls` are amount (the
    mix with the picture as it is), speed (of TIME) and half (work at half the width and height), `picture` says what
    is under it (see clean_picture). The text holds the filter twice, as two hooks of which the player runs one:
    the first for a picture in YUV, the second for one in RGB (a PNG, some streams), so a change between the two
    kinds in the middle of a playlist is right from its first frame without a new text."""
    if parsed.get("kind") != S.FILTER:
        raise ShaderError("this is a generator, not a filter of the playing picture: it is played under Shaders")
    if not re.fullmatch(r"[a-z0-9 ]{1,40}", desc):
        raise ShaderError("bad description")
    values = S.clean_values(parsed, values)
    c = L.clean_fx_controls(controls)
    pic = clean_picture(**(picture or {}))
    t = time.localtime() if today is None else today
    lines = ["// nxlx.mastercontrol effect (generated; do not edit)"]
    for plane in ("LUMA", "RGB"):
        lines += _block(parsed, values, c, pic, desc, plane, t)
    return "\n".join(lines)


# ---- how much work a filter is, counted from its text ------------------------------------------------------------------------
_LOOP = re.compile(r"\b(for|while)\s*\(")
_READ = re.compile(r"\bIMG_(?:THIS_|NORM_|THIS_NORM_)?PIXEL\b")
_FUNC = re.compile(r"\b[A-Za-z_]\w*\s+([A-Za-z_]\w*)\s*\(([^()]*)\)\s*\{")


def _close(text, at, opening="{", closing="}"):
    """The index just after the bracket that closes the one opened at `at`."""
    depth = 0
    for j in range(at, len(text)):
        if text[j] == opening:
            depth += 1
        elif text[j] == closing:
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def _rounds(head, numbers):
    """How many rounds a `for` loop with this head runs, or None if the text does not say: from where it starts (a
    number) to what it is compared with (a number, a named constant, or an input, counted at its MAX), by its step."""
    parts = head.split(";")
    if len(parts) != 3:
        return None

    def number(text):
        best = None
        for m in re.finditer(r"(-\s*)?([A-Za-z_]\w*|\d+(?:\.\d*)?)", text):
            word = m.group(2)
            if word[0].isdigit():
                n = float(word)
            elif word in numbers:
                n = float(numbers[word])
            else:
                continue
            best = -n if m.group(1) else n
        return best
    limit = re.search(r"([<>]=?)(.*)", parts[1], re.S)
    lo = number(parts[0].split("=", 1)[1]) if "=" in parts[0] else None
    hi = number(limit.group(2)) if limit else None
    if lo is None or hi is None:
        return None
    step = re.search(r"[+\-]=\s*(\d+(?:\.\d*)?)", parts[2])
    by = float(step.group(1)) if step and float(step.group(1)) > 0 else 1.0
    return max(1, int(math.ceil(abs(hi - lo) / by)) + (1 if "=" in limit.group(1) else 0))


def estimate(parsed):
    """{"reads", "rounds", "weight", "sure"}: how often a filter reads the picture for one pixel and how many rounds
    its loops run, counted from its text (every branch counts, so it is an upper limit), and the class that makes:
    light (at most 2 reads, no loop to speak of), medium (at most 12 reads and 16 rounds), heavy. `sure` is false
    when a loop's length could not be read. An estimate from the text, not a measurement: see SHADERS.md."""
    if "_estimate" in parsed:
        return parsed["_estimate"]
    code = parsed["body"]
    numbers = {i["name"]: i["max"] for i in parsed["inputs"] if i["type"] in ("float", "long") and "max" in i}
    for m in re.finditer(r"(?:#define\s+|const\s+(?:int|float)\s+)([A-Za-z_]\w*)\s*=?\s*(\d+(?:\.\d*)?)\b", code):
        numbers.setdefault(m.group(1), float(m.group(2)))
    for m in re.finditer(r"\b(?:int|float)\s+([A-Za-z_]\w*)\s*=\s*(?:(?:int|float)\s*\(\s*)?([A-Za-z_]\w*)\s*\)?\s*;", code):
        if m.group(2) in numbers:                   # a local name for an input: "int samples = quality;"
            numbers.setdefault(m.group(1), numbers[m.group(2)])
    functions, sure = {}, [True]
    for m in _FUNC.finditer(code):
        if m.group(1) not in ("if", "for", "while", "switch", "return"):
            functions[m.group(1)] = (m.end() - 1, _close(code, m.end() - 1))

    def cost(lo, hi, depth):
        """(reads, rounds) of code[lo:hi], with what the functions it calls read."""
        reads, rounds, i = 0, 1, lo
        while i < hi:
            loop = _LOOP.search(code, i, hi)
            part = code[i:loop.start() if loop else hi]
            reads += len(_READ.findall(part))
            if depth < 4:
                for name, (a, b) in functions.items():
                    calls = len(re.findall(r"\b%s\s*\(" % re.escape(name), part))
                    if calls and not (a <= lo < b):          # not a function calling itself
                        r, n = cost(a, b, depth + 1)
                        reads, rounds = reads + calls * r, max(rounds, n)
            if not loop:
                break
            head_end = _close(code, loop.end() - 1, "(", ")")
            brace, semi = code.find("{", head_end, hi), code.find(";", head_end, hi)
            if brace >= 0 and not code[head_end:brace].strip():
                body_end = _close(code, brace)
            else:                                           # a loop of one statement, without braces
                brace, body_end = head_end, (semi + 1 if semi >= 0 else hi)
            n = _rounds(code[loop.end():head_end - 1], numbers) if loop.group(1) == "for" else None
            if n is None:
                n, sure[0] = UNKNOWN_ROUNDS, False
            r, inner = cost(brace, min(body_end, hi), depth)
            reads, rounds = reads + n * r, max(rounds, n * inner)
            i = min(body_end, hi)
        return reads, rounds

    main = functions.get("main", (0, len(code)))
    reads, rounds = cost(main[0], main[1], 0)
    weight = "light" if reads <= 2 and rounds <= 4 else ("medium" if reads <= 12 and rounds <= 16 else "heavy")
    parsed["_estimate"] = {"reads": reads, "rounds": rounds, "weight": weight, "sure": sure[0]}
    return parsed["_estimate"]


# ---- the engine ----------------------------------------------------------------------------------------------------------
class Effects(S.Engine):
    """The library of filters and the one effect that is on. See the top of this file for where it sits."""
    KIND = S.FILTER
    STEM = "effect"

    def __init__(self, api, log=print, clock=time.monotonic, tap=S.LogTap, thread=True):
        super().__init__(api, log, clock, tap)
        self.dir = os.path.join(os.path.dirname(api.settings.path), "effects")
        self.bundled_dir = EFFECTS_DIR
        self.changer = L.Changer(self, clock, thread, refresh=WATCH)
        self.guard = L.Guard(self, clock)
        self._refusals = {}                         # source hash -> what the GPU said about the file
        self._bad = {}                              # (source hash, shape of the values) -> what the GPU said
        # the effect that is on: {"id", "values", "held", "controls", "picture", "path", "desc", "epoch", "digest",
        # "preset", "checked"}; "epoch" is the player's effect serial (the name the worker looks for)
        self.on = None
        self.playing = None                         # the base class's word; an effect never "plays"
        self.last = None                            # why the last effect came off, in words, or None
        self.recent = None                          # the effect that was on last: what "on" puts back

    # -- settings: only presets, kept in the Shaders and Vibes section (shaderlive.py) --
    def config(self):
        return {"dwell": S.DWELL_DEFAULT, "vary": False, "height": S.DEFAULT_HEIGHT, "disabled": []}

    def _save(self, cfg):
        raise RuntimeError("effects keep their settings through the shader engine")

    def _live(self):
        return self.api.shaders

    def presets(self, cfg=None):
        cfg = cfg or self._live().config()
        return cfg.get(L.FX_PRESETS, {})

    def faster(self):
        return bool(self._live().config().get("faster", False))

    # -- the library --
    def check_upload(self, data):
        parsed = S.parse(data, S.FILTER)
        translate(parsed)
        e = estimate(parsed)
        if e["reads"] > MAX_READS:
            raise ShaderError("it would read the picture about %d times for every pixel (at most %d): too heavy for this box" % (e["reads"], MAX_READS))
        if e["rounds"] > MAX_ROUNDS:
            raise ShaderError("its loops would run about %d rounds for every pixel (at most %d): too heavy for this box" % (e["rounds"], MAX_ROUNDS))

    def speed_max(self, parsed):
        """How fast TIME may run for this filter: null for one that does not move by itself, 1 for one that does or
        that says it flashes (the flash limit, see SHADERS.md), 4 once "faster" is switched on for the box."""
        if not (parsed.get("clock") or parsed.get("flashes")):
            return None
        return S.SPEED_MAX if self.faster() else 1.0

    def limit(self, parsed, controls):
        top = self.speed_max(parsed)
        if top is None:
            return dict(controls, speed=1.0)
        return dict(controls, speed=min(controls["speed"], top))

    def library(self, cfg=None):
        """[{"id", "name", "source", "pack", "description", "credit", "categories", "inputs", "error", "weight",
        "estimate", "moves", "flashes", "speed_max", "presets", "refused"}]: the project's own filters, then each
        third-party pack, then the uploads. An input carries "value": what is on now, or what putting it on would use."""
        cfg = cfg or self._live().config()
        on = self.current()
        rows = super().library()
        kept = self.presets(cfg)
        for s in rows:
            sid = s["id"]
            s.pop("vibes", None)
            s.pop("cost", None)
            s.update(weight="", estimate=None, moves=False, flashes=False, speed_max=None, refused=None,
                     presets=[p["name"] for p in kept.get(sid, [])])
            if s["error"]:
                continue
            try:
                parsed, digest = self._parsed(self._path(sid)[0])
            except (ShaderError, ApiError):
                continue
            e = estimate(parsed)
            s.update(weight=e["weight"], estimate={"reads": e["reads"], "rounds": e["rounds"], "sure": e["sure"]},
                     moves=bool(parsed.get("clock")), flashes=bool(parsed.get("flashes")), speed_max=self.speed_max(parsed),
                     refused=self._refusals.get(digest))
            now = self.current_values(parsed, on) if (on and on["id"] == sid) else self.start(parsed, cfg, sid)[0]
            for i in s["inputs"]:
                i["value"] = now.get(i["name"], i["default"])
        return rows

    def order(self):
        """The filters that can be put on, in the library's order: what Previous and Next step through."""
        out = []
        for s in S.Engine.library(self):
            if not s["error"]:
                try:
                    if self._parsed(self._path(s["id"])[0])[1] not in self._refusals:
                        out.append(s["id"])
                except (ShaderError, ApiError):
                    pass
        return out

    # -- presets --
    def start(self, parsed, cfg, sid, preset=None):
        """(values, controls or None, preset name or None) an effect starts with: the named preset, else the one called
        "default", else the file's own defaults."""
        rows = self.presets(cfg).get(sid, [])
        want = L.name_key(preset or L.DEFAULT_PRESET) if isinstance(preset or L.DEFAULT_PRESET, str) else None
        hit = next((p for p in rows if L.name_key(p["name"]) == want), None)
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

    def current_values(self, parsed, on):
        out = {i["name"]: i["default"] for i in parsed["inputs"]}
        out.update(on["values"])
        return out

    def _edit_presets(self, sid, change):
        """Change one effect's presets under the settings lock: `change(rows)` returns the new list."""
        live = self._live()
        with live._cfg:
            cfg = live.config()
            every = dict(cfg.get(L.FX_PRESETS, {}))
            rows = change(list(every.get(sid, [])), every)
            if rows:
                every[sid] = rows
            else:
                every.pop(sid, None)
            if every:
                cfg[L.FX_PRESETS] = every
            else:
                cfg.pop(L.FX_PRESETS, None)
            live._save(cfg)

    def preset_save(self, name, sid=None):
        if not L.name_ok(name):
            raise ApiError(400, "a preset needs a name of 1 to 40 characters")
        on = self.current()
        if on is None or (sid is not None and sid != on["id"]):
            raise ApiError(409, "put the effect on first: a preset keeps the values that are on the screen")
        wish = self.changer.pending() or {}
        wish = wish if (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"]) else {}
        entry = {"name": name, "values": dict(on["values"], **wish.get("values", {})), "controls": dict(on["controls"], **wish.get("controls", {}))}

        def change(rows, every):
            at = next((n for n, p in enumerate(rows) if L.name_key(p["name"]) == L.name_key(name)), None)
            if at is None:
                if len(rows) >= L.MAX_PRESETS:
                    raise ApiError(409, "at most %d presets for one effect; delete one first" % L.MAX_PRESETS)
                if on["id"] not in every and len(every) >= L.MAX_PRESET_SHADERS:
                    raise ApiError(409, "presets are kept for at most %d effects" % L.MAX_PRESET_SHADERS)
                return rows + [entry]
            rows[at] = entry
            return rows
        self._edit_presets(on["id"], change)
        on["preset"] = name

    def preset_rename(self, sid, name, to):
        self._path(sid)
        if not L.name_ok(to):
            raise ApiError(400, "a preset needs a name of 1 to 40 characters")

        def change(rows, every):
            hit = next((p for p in rows if isinstance(name, str) and L.name_key(p["name"]) == L.name_key(name)), None)
            if hit is None:
                raise ApiError(404, "no such preset")
            if any(p is not hit and L.name_key(p["name"]) == L.name_key(to) for p in rows):
                raise ApiError(409, "a preset with that name already exists")
            return [dict(p, name=to) if p is hit else p for p in rows]
        self._edit_presets(sid, change)

    def preset_delete(self, sid, name):
        self._path(sid)

        def change(rows, every):
            keep = [p for p in rows if not (isinstance(name, str) and L.name_key(p["name"]) == L.name_key(name))]
            if len(keep) == len(rows):
                raise ApiError(404, "no such preset")
            return keep
        self._edit_presets(sid, change)

    # -- what is under the effect --
    def picture(self):
        """What the player says of the picture that plays, as an effect's text needs it, or None when nothing with a
        picture is playing."""
        try:
            ipc = self.api.player.ipc
            params = ipc.request("get_property", "video-params")
        except Exception:
            return None
        if not isinstance(params, dict):
            return None
        fps = None
        for name in ("container-fps", "estimated-vf-fps"):
            try:
                fps = ipc.request("get_property", name)
            except Exception:
                fps = None
            if isinstance(fps, (int, float)) and not isinstance(fps, bool) and 1.0 <= fps <= 240.0:
                break
        return clean_picture(params.get("colormatrix"), params.get("colorlevels"), fps)

    def available(self):
        """(True, None), or (False, why an effect cannot be put on now, in plain words)."""
        if not self.enabled():
            return False, "The Shaders and Vibes module is off."
        player = self.api.player
        vibes = getattr(self.api, "vibes", None)
        if getattr(player, "source_shader", None) or self._live().on_screen() is not None or (vibes is not None and vibes.running):
            return False, ("A generator shader has the screen. An effect changes a picture that is playing, and a generator is drawn "
                           "from nothing, so there is no picture to change. Play a clip, a stream or a live input first.")
        if self.picture() is None:
            return False, "Nothing with a picture is playing. Play a clip, a stream or a live input, then put an effect on it."
        return True, None

    def current(self):
        """The effect that is on now, or None. The player is asked: Stop, a generator shader and a restart of the
        player each take the effect off there, and the record here follows."""
        rec = self.on
        if rec is None:
            return None
        player = self.api.player
        try:
            there = player.effect_on()
        except Exception:
            there = None
        if there is None or player.effect_serial != rec["epoch"]:
            if self.on is rec:
                self.on = None
                self.last = ENDED.get(getattr(player, "effect_ended", ""), None)
            return None
        return rec

    def sweep(self):
        """After a Stop: forget the effect the player has dropped and remove its text. It never waits (see
        shaders.Engine.tidy)."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            rec = self.current()
            self._cleanup({rec["path"]} if rec else set())
        finally:
            self._lock.release()

    def tidy(self):
        """When the panel starts: an effect an earlier panel process put on is still in the player, and nothing here
        knows its values any more. It is taken off, so the screen and this record never disagree, and the texts
        that process left are removed."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            player = self.api.player
            rundir = getattr(player, "rundir", None)
            if not rundir:
                return
            try:
                loaded = player.ipc.request("get_property", "glsl-shaders")
            except Exception:
                loaded = None
            mine = re.compile(r"%s-\d+-\d+\.glsl" % self.STEM)
            if isinstance(loaded, list) and self.on is None:
                keep = [p for p in loaded if not (isinstance(p, str) and os.path.dirname(p) == rundir and mine.fullmatch(os.path.basename(p)))]
                if len(keep) != len(loaded):
                    try:
                        player.ipc.request("set_property", "glsl-shaders", keep)
                        self.last = ENDED["panel"]
                    except Exception:
                        return
            rec = self.current()
            self._cleanup({rec["path"]} if rec else set())
        finally:
            self._lock.release()

    # -- putting one on, changing it, taking it off --
    def compose(self, parsed, state, desc):
        return translate(parsed, dict(state["values"], **state.get("held", {})), state["controls"], state["picture"], desc)

    def _key(self, parsed, digest, state):
        return (digest, S.shape_of(parsed, dict(state["values"], **state.get("held", {}))), bool(state["controls"]["half"]))

    def _now(self):
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def put(self, sid, values=None, controls=None, preset=None, serial=None, epoch=None, queued=False):
        """Put an effect on over what plays (in place of the one that is on, if any). Without values or a preset it
        uses the preset called default, else the file's defaults. With `serial` and `epoch` (the worker's call),
        only if no effect went on or off and nothing was played or stopped since they were handed out; None is
        returned then. Returns {"ok": True, "id"}; raises 409 when there is no picture to put it on, 422 when the
        file cannot be translated or the GPU refuses it (the effect before it stays on, else none)."""
        self._need()
        path, _ = self._path(sid)
        with self._lock:
            ok, why = self.available()
            if not ok:
                raise ApiError(409, why)
            try:
                parsed, digest = self._parsed(path)
                cfg = self._live().config()
                start, stored, name = self.start(parsed, cfg, sid, preset)
                if values is not None:
                    start = dict(start, **S.clean_values(parsed, values))
                    name = None if values else name
                events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
                state = {"values": {n: v for n, v in start.items() if n not in events},
                         "held": {n: True for n, v in start.items() if n in events and v},
                         "controls": self.limit(parsed, L.clean_fx_controls(controls, stored)),
                         "picture": self.picture() or clean_picture()}
                desc = "nxlx effect %d %d" % (os.getpid(), self._serial + 1)
                text = self.compose(parsed, state, desc)
                key = self._key(parsed, digest, state)
            except ShaderError as e:
                raise ApiError(422, "%s: %s" % (sid, e))
            player = self.api.player
            before = self.current()
            try:
                out = self._write(text)
            except OSError as e:
                raise ApiError(500, "could not write the effect: %s" % (e.strerror or e))
            tap = None
            if key not in self._checked and self._gpu_output():
                try:
                    tap = self._tap(player.socket_path)
                except OSError:
                    tap = None
            try:
                try:
                    new = player.put_effect(out, serial, epoch)
                except PlayerError as e:
                    self._cleanup({before["path"]} if before else set())
                    raise ApiError(503, str(e))
                if new is None:                         # the screen changed hands meanwhile
                    self._cleanup({before["path"]} if before else set())
                    if serial is None:
                        raise ApiError(409, self.available()[1] or "the screen changed hands; try again")
                    return None
                verdict, message = self._watch(tap, desc) if tap else ("unknown", "")
            finally:
                if tap:
                    tap.close()
            if verdict == "refused":
                back = before if before and os.path.exists(before["path"]) else None
                try:
                    if not (back and player.swap_effect(back["path"], new)):
                        back = None
                        player.clear_effect(new, "refused")
                except PlayerError:
                    back = None
                self.on = dict(back, epoch=new) if back else None
                self._cleanup({self.on["path"]} if self.on else set())
                self.error = {"id": sid, "message": message, "at": self._now()}
                if len(self._refusals) > 4 * S.MAX_UPLOADS:
                    self._refusals.clear()
                self._refusals[digest] = message
                self.log("pvj-web: effect %s refused by the player: %s" % (sid, message))
                raise ApiError(422, "the player refused %s: %s. %s" % (
                    sid, message, "The effect before it is back on." if self.on else "No effect is on."))
            if verdict == "ok":
                if len(self._checked) > 2048:
                    self._checked.clear()
                self._checked.add(key)
            self._refusals.pop(digest, None)
            if self.error and self.error["id"] == sid:
                self.error = None
            self.on = {"id": sid, "values": state["values"], "held": state["held"], "controls": state["controls"], "picture": state["picture"],
                       "path": out, "desc": desc, "epoch": new, "digest": digest, "preset": name,
                       "checked": True if (verdict == "ok" or key in self._checked) else None}
            self.recent, self.last = sid, None
            self._cleanup({out})
            self.changer.keep()                         # look at the picture under it from now on
            return {"ok": True, "id": sid}

    def play_job(self, job):
        """The worker's call for a whole effect (a step, on, a preset of another effect)."""
        if not self.enabled():
            return
        try:
            self.put(job["id"], job.get("values"), job.get("controls"), job.get("preset"), serial=job["serial"], epoch=job["epoch"], queued=True)
        except ApiError as e:
            self.error = {"id": job["id"], "message": e.message, "at": self._now()}

    def adjust(self, job):
        """The worker's call (see shaderlive.Changer): new values or controls for the effect that is on, the release
        of a pressed event (None), or the regular look ("anchor"): has another kind of picture started under the
        effect, or has the flash limit changed? Only the text is exchanged. Returns True while an event is held."""
        with self._lock:
            p = self.current()
            if p is None:
                return False
            look = job == "anchor"
            if job is None:
                if not p.get("held"):
                    return False
                job = {"epoch": p["epoch"], "id": p["id"], "values": {}, "controls": {}, "held": {}}
            elif look:
                job = {"epoch": p["epoch"], "id": p["id"], "values": {}, "controls": {}, "held": dict(p.get("held") or {})}
            if (job["epoch"], job["id"]) != (p["epoch"], p["id"]):
                return False
            try:
                parsed, digest = self._parsed(self._path(p["id"])[0])
                state = {"values": dict(p["values"], **job["values"]), "held": dict(job["held"]),
                         "controls": self.limit(parsed, dict(p["controls"], **job["controls"])),
                         "picture": (self.picture() or p["picture"]) if look else p["picture"]}
                if look:
                    self.changer.keep()
                    self.guard.sample(p)
                    if state["picture"] == p["picture"] and state["controls"] == p["controls"]:
                        return bool(p.get("held"))
                desc = "nxlx effect %d %d" % (os.getpid(), self._serial + 1)
                text = self.compose(parsed, state, desc)
                key = self._key(parsed, digest, state)
            except (ShaderError, ApiError) as e:
                self.error = {"id": p["id"], "message": str(getattr(e, "message", e)), "at": self._now()}
                return False
            if key in self._bad:
                self.error = {"id": p["id"], "message": self._bad[key], "at": self._now()}
                return False
            player = self.api.player
            try:
                out = self._write(text)
            except OSError as e:
                raise ApiError(500, "could not write the effect: %s" % (e.strerror or e))
            tap = None
            if key not in self._checked and self._gpu_output():
                try:
                    tap = self._tap(player.socket_path)
                except OSError:
                    tap = None
            try:
                try:
                    done = player.swap_effect(out, p["epoch"])
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
            if verdict == "refused":
                try:
                    player.swap_effect(p["path"], p["epoch"])
                except Exception:
                    pass
                self._cleanup({p["path"]})
                self.error = {"id": p["id"], "message": message, "at": self._now()}
                if len(self._bad) >= 256:
                    self._bad.clear()
                self._bad[key] = message
                self.log("pvj-web: effect %s refused with new values: %s" % (p["id"], message))
                return False
            if verdict == "ok":
                self._checked.add(key)
            preset = p.get("preset") if not job["values"] and not job["controls"] else job.get("preset")
            self.on = dict(p, values=state["values"], held=state["held"], controls=state["controls"], picture=state["picture"], path=out, desc=desc,
                           digest=digest, preset=preset, checked=True if (verdict == "ok" or key in self._checked) else p["checked"])
            self._cleanup({out})
            return bool(state["held"])

    def off(self, why=None):
        """Take the effect off (the Off button, or the module going off). Never waits for the GPU: it is one request
        to the player."""
        self.changer.clear()
        rec = self.on
        try:
            self.api.player.clear_effect(None, "off")
        except Exception:
            pass
        self.on = None
        if rec is not None or why:
            self.last = why
        if self._lock.acquire(blocking=False):
            try:
                self._cleanup(set())
            finally:
                self._lock.release()
        return {"ok": True}

    def nth_control(self, parsed, n):
        able = [i for i in parsed["inputs"] if i["type"] in ("bool", "long", "event") or (i["type"] == "float" and i["max"] > i["min"])]
        return able[n - 1] if 1 <= n <= min(len(able), L.MAX_CONTROLS) else None

    def change(self, body):
        """Note a change to the effect that is on and return at once: {"values"?: {input: value}, "controls"?:
        {amount, speed, half}, "id"?: the effect it is meant for}, or from a controller {"control": 1 to 8, "level":
        0 to 127} or {"control": n, "press": true}. Everything is checked here; the GPU gets it from the worker."""
        self._need()
        on = self.current()
        if on is None:
            raise ApiError(409, "no effect is on")
        if body.get("id") is not None and body["id"] != on["id"]:
            raise ApiError(409, "%s is not the effect that is on" % S._text(str(body["id"]), 60))
        try:
            parsed = self._parsed(self._path(on["id"])[0])[0]
            values = S.clean_values(parsed, body.get("values"))
            controls = L.clean_fx_controls(body.get("controls"), {}) if body.get("controls") is not None else {}
            wish = self.changer.pending() or {}
            wish = wish if (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"]) else {}
            if "control" in body:
                n, level, press = body["control"], body.get("level"), body.get("press")
                if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= L.MAX_CONTROLS:
                    raise ApiError(400, "control must be 1 to %d" % L.MAX_CONTROLS)
                if (level is None) == (press is None) or (press is not None and press is not True) or (
                        level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 127)):
                    raise ApiError(400, "send level (0 to 127) or press (true)")
                i = self.nth_control(parsed, n)
                if i is None:
                    raise ApiError(404, "%s has no control %d" % (on["id"], n))
                now = dict(self.current_values(parsed, on), **wish.get("values", {}))
                v = L.LiveEngine._from_controller(self, i, now.get(i["name"], i["default"]), level, press)
                if v is not None:
                    values[i["name"]] = S.clean_value(i, v)
        except ShaderError as e:
            raise ApiError(400, str(e))
        events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
        held = {n: True for n, v in values.items() if n in events and v}
        values = {n: v for n, v in values.items() if n not in events}
        after = {"values": dict(on["values"], **dict(wish.get("values", {}), **values)), "held": held,
                 "controls": dict(on["controls"], **dict(wish.get("controls", {}), **controls))}
        if self._bad:
            said = self._bad.get(self._key(parsed, on["digest"], after))
            if said:
                raise ApiError(422, "the GPU refused these values before (%s); they were not sent again" % said)
        if values or controls or held:
            self.changer.submit(on, values, controls, held)
        return {"ok": True, "id": on["id"], "values": dict(self.current_values(parsed, on), **after["values"]),
                "controls": self.limit(parsed, after["controls"])}

    def _queue(self, sid, preset=None):
        player = self.api.player
        self.changer.show({"id": sid, "preset": preset, "serial": player.effect_serial, "epoch": player.source_epoch})

    def apply_preset(self, body):
        """{"name"} or {"index": 1 to 16} for the effect that is on, or with "id" for another one, which is then put
        on with it. Answers at once; the worker does it."""
        self._need()
        on = self.current()
        sid = body.get("id") if body.get("id") is not None else (on["id"] if on else None)
        if sid is None:
            raise ApiError(409, "no effect is on")
        path, _ = self._path(sid)
        cfg = self._live().config()
        rows = self.presets(cfg).get(sid, [])
        name = body.get("name")
        if "index" in body:
            n = body["index"]
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= L.MAX_PRESETS:
                raise ApiError(400, "index must be 1 to %d" % L.MAX_PRESETS)
            if n > len(rows):
                raise ApiError(404, "%s has no preset %d" % (sid, n))
            name = rows[n - 1]["name"]
        if not isinstance(name, str):
            raise ApiError(400, "name the preset")
        try:
            parsed = self._parsed(path)[0]
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        values, controls, name = self.start(parsed, cfg, sid, name)
        if on and on["id"] == sid:
            full = {i["name"]: i["default"] for i in parsed["inputs"] if i["type"] != "event"}      # a preset sets every input
            self.changer.submit(on, dict(full, **values), controls)
            with self.changer._cond:
                if self.changer._adjust:
                    self.changer._adjust["preset"] = name
        else:
            ok, why = self.available()
            if not ok:
                raise ApiError(409, why)
            self._queue(sid, name)
        return {"ok": True, "id": sid, "preset": name}

    def step(self, direction):
        """The next filter of the library, or the one before, put on in place of the one that is on (the first or the
        last when none is). Answers at once; the worker puts it on, and only if nothing was played, stopped or put
        on in between."""
        self._need()
        if direction not in (1, -1):
            raise ApiError(400, "dir must be 1 (next) or -1 (the one before)")
        ok, why = self.available()
        if not ok:
            raise ApiError(409, why)
        ids = self.order()
        if not ids:
            raise ApiError(409, "there is no effect that can be put on")
        on = self.current()
        player = self.api.player
        wish = self.changer.queued()
        wish = wish if wish and (wish.get("serial"), wish.get("epoch")) == (player.effect_serial, player.source_epoch) else None
        at = (wish or {}).get("id") or (on["id"] if on else None)
        nxt = ids[(ids.index(at) + direction) % len(ids)] if at in ids else ids[0 if direction == 1 else -1]
        self._queue(nxt)
        return {"ok": True, "id": nxt}

    def toggle(self):
        """On or off from one button (a controller's): off if an effect is on, otherwise the one that was on last
        (or the first of the library) is put back."""
        self._need()
        if self.current() is not None:
            self.off()
            return {"ok": True, "on": False}
        ok, why = self.available()
        if not ok:
            raise ApiError(409, why)
        ids = self.order()
        if not ids:
            raise ApiError(409, "there is no effect that can be put on")
        sid = self.recent if self.recent in ids else ids[0]
        self._queue(sid)
        return {"ok": True, "on": True, "id": sid}

    # -- uploads --
    def delete(self, sid):
        path, source = self._path(sid)
        if source != "uploaded":
            raise ApiError(409, "a bundled effect cannot be deleted")
        on = self.current()
        if on and on["id"] == sid:
            self.off("its file was deleted")
        with self._lock:
            try:
                os.unlink(path)
            except OSError as e:
                raise ApiError(500, "could not delete: %s" % (e.strerror or e))
            if self.error and self.error["id"] == sid:
                self.error = None
            if self.recent == sid:
                self.recent = None
        if sid in self.presets():
            self._edit_presets(sid, lambda rows, every: [])

    # -- requests --
    def state(self):
        enabled = self.enabled()
        on = self.current() if enabled else None
        rows = self.library() if enabled else []
        ok, why = self.available() if enabled else (False, "The Shaders and Vibes module is off.")
        showing = None
        if on:
            wish = self.changer.pending() or {}
            mine = (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"])
            row = next((s for s in rows if s["id"] == on["id"]), None)
            showing = {"id": on["id"], "name": on["id"][:-3], "controls": dict(on["controls"]), "preset": on.get("preset"),
                       "values": {i["name"]: i["value"] for i in row["inputs"] if i["type"] != "event"} if row else dict(on["values"]),
                       "pending": bool(mine), "checked": on["checked"], "picture": dict(on["picture"])}
            for x in self._fresh(on["desc"]):
                if isinstance(x.get("avg"), (int, float)) and not isinstance(x["avg"], bool) and x["avg"] > 0:
                    showing["pass_ms"] = round(x["avg"] / 1e6, 2)
            seen = self.guard.sample(on)
            showing.update(load=seen["state"], drops_per_second=seen["drops_per_second"])
        else:
            self.guard.sample(None)
        return {"enabled": enabled, "effects": rows, "on": showing, "available": ok, "unavailable": why, "error": self.error, "last": self.last,
                "controls": {"amount": {"min": 0.0, "max": 1.0, "default": 1.0}, "speed": {"min": S.SPEED_MIN, "max": S.SPEED_MAX, "default": 1.0},
                             "half": {"default": False}},
                "faster": self.faster() if enabled else False,
                "limits": {"bytes": S.MAX_SOURCE, "inputs": S.MAX_INPUTS, "uploads": S.MAX_UPLOADS, "presets": L.MAX_PRESETS, "name": 40,
                           "controls": L.MAX_CONTROLS, "reads": MAX_READS, "rounds": MAX_ROUNDS, "at_once": 1}}

    def api_get(self, body, device, client):
        return self.state()

    def api_put(self, body, device, client):
        """Live access: {"id": "fx-vignette.fs", "values"?, "controls"?: {"amount"?, "speed"?, "half"?}, "preset"?}
        puts an effect on over what plays; {"off": true} takes it off; {"toggle": true} is a controller's one button."""
        self._need()
        if body.get("off") is True:
            self.off()
        elif body.get("toggle") is True:
            return self.toggle()
        else:
            try:
                self.put(body.get("id"), body.get("values"), body.get("controls"), body.get("preset"))
            except ShaderError as e:
                raise ApiError(422, str(e))
        return self.state()

    def api_values(self, body, device, client):
        return self.change(body)

    def api_step(self, body, device, client):
        return self.step(body.get("dir", 1))

    def api_preset(self, body, device, client):
        return self.apply_preset(body)

    def api_presets(self, body, device, client):
        """Full access: {"action": "save", "name", "id"?}, {"action": "rename", "id", "name", "to"}, {"action":
        "delete", "id", "name"}."""
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

    def api_library(self, body, device, client):
        """Full access: {"action": "upload", "name": "x.fs", "source": "<the file's text>", "replace"?: bool} and
        {"action": "delete", "id"}."""
        self._need()
        action = body.get("action")
        if action == "upload":
            self.upload(body.get("name"), body.get("source"), body.get("replace", False))
        elif action == "delete":
            self.delete(body.get("id"))
        else:
            raise ApiError(400, "action must be upload or delete")
        return self.state()

    # the generator engine's calls that make no sense for an effect
    def show(self, *args, **kwargs):
        raise ApiError(400, "an effect is put on over a picture, never played by itself")

    def api_play(self, body, device, client):
        raise ApiError(400, "an effect is put on over a picture, never played by itself")

    def api_set(self, body, device, client):
        raise ApiError(400, "an effect is put on over a picture, never played by itself")

    def vibes_ids(self):
        return []
