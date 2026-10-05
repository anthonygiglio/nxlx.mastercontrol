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

from . import hardware, shaderlive as L, shaders as S
from .api import ApiError
from .player import PlayerError
from .shaders import ShaderError

EFFECTS_DIR = os.path.join(os.path.dirname(__file__), "effects.d")
WATCH = 1.0                     # seconds between two looks at the picture under the effect
MAX_READS = 64                  # an upload may read the picture at most this often for one pixel (a count from its text)
MAX_ROUNDS = 256                # and its loops may run at most this many rounds for one pixel
DEFAULT_FPS = 30.0
SWITCH_GAP = 0.35               # seconds between two switches of the effect asked for through the worker (the flash limit)
# A stream or a live input has no frame rate of its own: the player estimates one from the frames as they come, and
# the estimate moves all the time. It is taken for the common rate nearest to it when one is close, and an estimate
# within FPS_SAME of the rate the text on screen was made for is the same rate. A file's own rate is exact and is
# followed exactly.
COMMON_FPS = (10.0, 12.0, 12.5, 15.0, 23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0, 100.0, 120.0)
FPS_NEAR = 0.03
FPS_SAME = 0.06
# Pictures the effect's two hooks cannot take: a hook binds the LUMA plane (a video) or the RGB plane. A picture in XYZ
# has neither, and one with no colour (grey, grey with alpha, black and white, a palette) was never drawn through
# them. The list names what is known not to fit; a format it does not know (a decoder's own, such as drm_prime with
# the real layout in hw-pixelformat) is let through.
UNFIT_FORMATS = re.compile(r"^(?:xyz|gray|y8|y1[0-6]|ya8|ya16|mono[bw]|pal8)")
UNFIT = "This picture's format cannot take an effect"
# Kr and Kb of the colour matrices mpv names in video-params/colormatrix. Anything else is treated as BT.709.
MATRICES = {"bt.601": (0.299, 0.114), "bt.709": (0.2126, 0.0722), "bt.2020-ncl": (0.2627, 0.0593),
            "bt.2020-cl": (0.2627, 0.0593), "smpte-240m": (0.212, 0.087)}
ENDED = {"stop": "Stop was pressed", "generator": "a generator shader took the screen", "restart": "the player was restarted",
         "module": "the module was switched off", "panel": "the panel was restarted", "format": "a picture came that cannot take an effect"}


GENERATOR_HAS_IT = ("A generator shader has the screen. An effect changes a picture that is playing, and a generator is drawn "
                    "from nothing, so there is no picture to change. Play a clip, a stream or a live input first.")


# Measured on a real board: a Raspberry Pi 4 (mpv 0.40, desktop OpenGL 3.1 on V3D, a 2560 x 1440 screen at 75 Hz), every
# bundled filter with its defaults (the seven that change nothing with them, with the values of the GPU test) over an
# H.264 clip of 1920 x 1080 and one of 1280 x 720, both 30 pictures a second and decoded in software, on 2026-10-05,
# for 15 seconds each (60 where the first look was near the line). Per filter: the class, then for the 1080 line clip
# and for the 720 line clip ((the filter's own pass in milliseconds, frames dropped a second) at full size, the same
# with Half resolution). The clips by themselves dropped nothing. The method and what else was seen are in
# project-log/JOURNAL.md ("effects on the Pi 4").
#   light   holds 30 frames a second over a 1080 line clip
#   medium  holds over a 720 line clip only
#   heavy   drops frames over a 720 line clip
# "Holds" is fewer than HOLDS dropped frames a second, the generators' line (shaderlive.HOLDS), where the guard says "ok".
# A filter's own pass is not its whole cost: with any effect on, the player merges the clip's planes and converts the
# colours in passes of their own. All its passes together took 12 ms for the 720 line clip and 13 ms for the 1080 line
# clip by themselves, and with an effect on, the filter's own pass plus about 14 ms at 720 lines and plus about 21 ms
# at 1080 lines (plus 12 and 18 with Half resolution).
HOLDS = L.HOLDS
PI4 = {
    "fx-edge-glow.fs": ("medium", ((21.1, 7.93), (5.3, 0.0)), ((9.2, 0.0), (2.4, 0.0))),
    "fx-grade.fs": ("medium", ((11.1, 2.53), (2.8, 0.0)), ((4.7, 0.0), (1.2, 0.0))),
    "fx-kaleido.fs": ("medium", ((17.6, 6.73), (4.5, 0.0)), ((7.7, 0.0), (2.0, 0.0))),
    "fx-mirror-quad.fs": ("medium", ((9.7, 1.27), (2.4, 0.0)), ((4.0, 0.0), (1.1, 0.0))),
    "fx-pixel-grid.fs": ("medium", ((11.5, 2.53), (2.9, 0.0)), ((4.9, 0.0), (1.3, 0.0))),
    "fx-rgb-split.fs": ("medium", ((15.4, 4.93), (3.9, 0.0)), ((6.6, 0.0), (1.8, 0.0))),
    "fx-ring.fs": ("medium", ((15.5, 5.13), (4.0, 0.0)), ((6.7, 0.0), (1.8, 0.0))),
    "fx-ripple.fs": ("medium", ((14.6, 4.93), (3.6, 0.0)), ((6.3, 0.0), (1.6, 0.0))),
    "fx-slit-bands.fs": ("medium", ((13.5, 4.26), (3.4, 0.0)), ((5.8, 0.0), (1.5, 0.0))),
    "fx-twirl.fs": ("medium", ((16.0, 5.4), (4.0, 0.0)), ((6.9, 0.0), (1.8, 0.0))),
    "fx-vignette.fs": ("medium", ((10.9, 2.22), (2.7, 0.0)), ((4.6, 0.0), (1.2, 0.0))),
    "fx-wash.fs": ("medium", ((9.1, 0.83), (2.3, 0.0)), ((3.7, 0.0), (1.0, 0.0))),
    "isf-chromatic-aberration.fs": ("medium", ((12.2, 3.13), (3.5, 0.0)), ((5.2, 0.0), (1.5, 0.0))),
    "isf-color-monochrome.fs": ("medium", ((11.1, 2.53), (2.8, 0.0)), ((4.8, 0.0), (1.2, 0.0))),
    "isf-corner-color-tint.fs": ("medium", ((20.7, 8.33), (5.2, 0.0)), ((9.0, 0.0), (2.3, 0.0))),
    "isf-double-vision.fs": ("medium", ((16.4, 5.93), (4.4, 0.0)), ((7.1, 0.0), (1.9, 0.0))),
    "isf-duotone.fs": ("light", ((8.2, 0.18), (2.1, 0.0)), ((3.3, 0.0), (1.0, 0.0))),
    "isf-edge-blowout.fs": ("medium", ((30.4, 11.33), (7.7, 0.0)), ((13.5, 0.35), (3.4, 0.0))),
    "isf-false-color.fs": ("medium", ((10.0, 1.6), (2.5, 0.0)), ((4.2, 0.0), (1.1, 0.0))),
    "isf-flip-h.fs": ("medium", ((8.9, 0.72), (2.2, 0.0)), ((3.6, 0.0), (1.0, 0.0))),
    "isf-flip-v.fs": ("medium", ((8.9, 0.7), (2.2, 0.0)), ((3.6, 0.0), (1.0, 0.0))),
    "isf-gamma-correction.fs": ("medium", ((10.3, 1.73), (2.5, 0.0)), ((4.2, 0.0), (1.1, 0.0))),
    "isf-hyperspace.fs": ("medium", ((14.3, 4.6), (4.2, 0.0)), ((6.1, 0.0), (1.8, 0.0))),
    "isf-interlace-mirror.fs": ("medium", ((9.9, 1.57), (3.2, 0.0)), ((4.1, 0.0), (1.4, 0.0))),
    "isf-kaleidoscope-tile.fs": ("medium", ((17.5, 6.06), (4.5, 0.0)), ((7.6, 0.0), (2.0, 0.0))),
    "isf-kaleidoscope.fs": ("medium", ((17.1, 6.0), (4.3, 0.0)), ((7.4, 0.0), (1.9, 0.0))),
    "isf-lgg.fs": ("medium", ((11.5, 2.8), (2.9, 0.0)), ((4.9, 0.0), (1.3, 0.0))),
    "isf-mirror.fs": ("medium", ((9.0, 0.78), (2.2, 0.0)), ((3.6, 0.0), (1.0, 0.0))),
    "isf-posterize.fs": ("medium", ((12.0, 2.86), (2.9, 0.0)), ((5.1, 0.0), (1.3, 0.0))),
    "isf-quad-tile.fs": ("medium", ((17.0, 6.32), (6.8, 0.0)), ((7.2, 0.0), (2.8, 0.0))),
    "isf-rgb-eq.fs": ("medium", ((9.5, 1.15), (2.4, 0.0)), ((3.9, 0.0), (1.0, 0.0))),
    "isf-rgb-halftone.fs": ("medium", ((18.5, 7.26), (4.6, 0.0)), ((8.1, 0.0), (2.1, 0.0))),
    "isf-rgb-invert.fs": ("medium", ((9.5, 1.15), (2.4, 0.0)), ((3.9, 0.0), (1.0, 0.0))),
    "isf-sine-warp-tile.fs": ("medium", ((16.5, 5.86), (4.2, 0.0)), ((7.2, 0.0), (1.9, 0.0))),
    "isf-triple-rotate.fs": ("medium", ((18.5, 6.72), (4.6, 0.0)), ((8.1, 0.0), (2.0, 0.0))),
    "isf-white-point-adjust.fs": ("medium", ((9.1, 0.85), (2.3, 0.0)), ((3.7, 0.0), (1.0, 0.0))),
    "isf-zoom.fs": ("medium", ((11.8, 3.13), (2.9, 0.0)), ((5.0, 0.0), (1.3, 0.0))),
}


def weigh(small, large):
    """The class that two dropped-frame rates (over a 720 line clip and over a 1080 line clip, at full size) make."""
    return "light" if large < HOLDS else ("medium" if small < HOLDS else "heavy")


def measured(sid):
    """What was measured for a bundled filter on a Pi 4, or None: the filter's own pass in milliseconds and the frames
    dropped a second by the clip's lines, at full size and with Half resolution ("1080", "1080_half", "720",
    "720_half"), the largest of the two clips it held 30 frames a second over, at full size and at half, and the
    lines Automatic lets it work at on that board ("works_at": where it held the 1080 line clip)."""
    if sid not in PI4:
        return None
    _, large, small = PI4[sid]
    by = {"1080": large[0], "1080_half": large[1], "720": small[0], "720_half": small[1]}
    holds = lambda a, b: 1080 if a[1] < HOLDS else (720 if b[1] < HOLDS else None)
    return {"board": "pi4", "pass_ms": {k: v[0] for k, v in by.items()}, "drops_per_second": {k: v[1] for k, v in by.items()},
            "holds": holds(large[0], small[0]), "holds_half": holds(large[1], small[1]), "works_at": auto_lines("pi4", sid)}


# ---- the working size ("Effect detail") ---------------------------------------------------------------------------------
# An effect runs once for every pixel of the clip, and on a small board a 1080 line clip has more pixels than the GPU
# can filter in a thirtieth of a second. So the filter may work on a smaller copy: the hook's output is given a size
# whose shorter side is at most a number of lines (the "cap"), the filter draws that many pixels, reading the clip
# itself with the GPU's own smoothing, and the player scales the result to the screen as it would a clip of that size.
# A clip whose shorter side is at or below the cap is not scaled at all. The shorter side, not the height: at the
# stage an effect hooks, a clip the decoder hands over turned (a phone's upright video) still lies on its side, and
# the shorter side is the same number whichever way it lies; it is also what "720p" and "1080p" name.
# The setting is one for the box: "auto" (the table below decides), a number of lines, or "full" (never scaled).
DETAILS = L.FX_DETAILS            # ("auto", 540, 720, "full"); kept with the settings they are saved in
HALF_LINES = 540                # what the superseded control "half": true means: this effect works at 540 lines at most
# What "auto" works at, by board: "lines" for a bundled filter, "lower" for the bundled filters that were measured as
# too much at that ("holds" is fewer than HOLDS dropped frames a second, as everywhere), "other" for a filter nobody
# measured on that board (an upload). A board that is not here is not scaled by "auto": nothing was measured on it.
# The rule, for a board that was measured: the largest of the choices at which a filter holds 30 frames a second over
# a 1080 line clip of 30 pictures a second; the next one down for the filters that do not hold there; the careful
# value for an upload.
#   pi4: filled in from the run on the owner's Pi 4 (project-log/JOURNAL.md, "effect detail on the Pi 4").
#   pi3: NOT MEASURED, and effects are not offered on a Pi 3 today (pvj/modules.d/shaders.json). The careful value,
#        should they ever be: its GPU is the weaker one.
AUTO = {
    "pi4": {"lines": 720, "lower": {}, "other": 540, "measured": False},
    "pi3": {"lines": 540, "lower": {}, "other": 540, "measured": False},
}
AUTO["pi-other"] = AUTO["arm-other"] = AUTO["pi3"]      # a small board the box cannot name: the careful value, not measured


def default_detail(board):
    """What Effect detail is on a box where nobody chose: "auto" on the small boards (a Pi 4, a Pi 3, and a small
    board the box cannot name), "full" on a Pi 5 and on x86, where no effect has been measured."""
    return "auto" if board in AUTO else "full"


def clean_detail(v):
    """An Effect detail from untrusted input; raises ValueError."""
    if not L.detail_ok(v):
        raise ValueError("detail must be one of %s" % ", ".join(str(d) for d in DETAILS))
    return v


def auto_lines(board, sid=None, bundled=True):
    """The cap "auto" gives a filter on this board, or None where nothing is scaled."""
    row = AUTO.get(board)
    if row is None:
        return None
    if not bundled or sid is None:
        return row["other"]
    return row["lower"].get(sid, row["lines"])


def cap_lines(detail, board, sid=None, bundled=True, half=False):
    """The lines an effect works at, at most, or None for the clip's own size: from the box's Effect detail, and
    never more than HALF_LINES for an effect whose own (superseded) "half" is true."""
    lines = auto_lines(board, sid, bundled) if detail == "auto" else (None if detail == "full" else detail)
    return min(lines or HALF_LINES, HALF_LINES) if half else lines


def work_size(w, h, lines):
    """(width, height) of the picture a filter draws for a clip of w x h under a cap of `lines` (None: no cap): the
    clip's own size when its shorter side is at or below the cap, else the shorter side is the cap and the longer
    one keeps the shape, rounded as the player rounds (a half goes up). The clip's size is the one it is stored
    in; a rotation changes nothing here."""
    w, h = int(w), int(h)
    short = min(w, h)
    if lines is None or short <= lines or short <= 0:
        return w, h
    long_side = max(1, (2 * max(w, h) * lines + short) // (2 * short))
    return (long_side, lines) if w >= h else (lines, long_side)


def size_lines(lines, tall=False):
    """The two size lines of a hook for a cap: the player's own arithmetic (written the way it reads it, each value
    before the sign that uses it), relative to the picture it meets, so a clip of another size is right from its
    first frame. `tall` says which side is the shorter one: for a standing picture the width is capped."""
    a, b = ("HOOKED.w", "HOOKED.h") if tall else ("HOOKED.h", "HOOKED.w")       # a: the shorter side
    short = "%s %s %d > %s %d - * -" % (a, a, lines, a, lines)                    # a - (a > cap) * (a - cap)
    other = "%s %s %d > %s * %s %d - * %s / -" % (b, a, lines, b, a, lines, a)   # b - (a > cap) * b * (a - cap) / a
    return ["//!WIDTH %s" % (short if tall else other), "//!HEIGHT %s" % (other if tall else short)]


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


def fits(params):
    """Whether the effect's hooks can take a picture the player describes so (video-out-params or video-params)."""
    if not isinstance(params, dict):
        return True
    name = params.get("hw-pixelformat") or params.get("pixelformat")
    if isinstance(name, str) and UNFIT_FORMATS.match(name):
        return False
    return params.get("colormatrix") != "xyz"


def near_common(fps):
    """The player's estimate of a frame rate, taken for the common rate it is close to, if any."""
    rate = min(COMMON_FPS, key=lambda r: abs(fps - r))
    return rate if abs(fps - rate) <= FPS_NEAR * rate else fps


def steady_picture(new, old, clock, estimated=False):
    """The picture a text is to be made for, given the one the text on screen was made for: the frame rate stays
    what it was when the filter never reads the clock (the rate is then in no line that matters), and when it is
    the player's estimate and has only wobbled."""
    if old is None:
        return new
    same = not clock or (estimated and abs(new["fps"] - old["fps"]) <= FPS_SAME * old["fps"])
    return dict(new, fps=old["fps"]) if same else new


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
def _block(parsed, values, c, pic, desc, plane, t, cap=None, tall=False):
    """One hook of the effect's text, for a picture that has the plane `plane`: "LUMA" (the picture is YUV and is
    converted with its matrix and range) or "RGB" (nothing to convert). `cap` and `tall`: see size_lines."""
    lines = ["//!HOOK NATIVE", "//!BIND HOOKED",
             # mpv leaves a hook out when a texture it binds is not there (seen in CI on mpv 0.37): a video has a LUMA
             # plane and no RGB plane, an RGB picture the other way round, so exactly one of the two hooks runs
             "//!BIND %s" % plane]
    if cap is not None:
        lines += size_lines(cap, tall)
    if not c["amount"]:
        # Amount 0 is the picture as it is: the player is told to leave this hook out (a condition that is never
        # true), so nothing is drawn, nothing is scaled and nothing is paid. The effect stays "on" for the panel.
        lines.append("//!WHEN 0")
    lines += ["//!DESC %s" % desc, "",
              "// %s" % desc,              # the name again, inside the code: see shaders.translate
              "#if defined(GL_ES) && (__VERSION__ >= 300 || defined(GL_FRAGMENT_PRECISION_HIGH))",
              "precision highp float;", "precision highp int;", "#define PVJ_HP highp",
              "#else", "#define PVJ_HP", "#endif",
              # In ISF a filter is drawn at the size of its picture, so both sizes are the same thing here, also under
              # a cap: the picture is then read as one of the smaller size (pvj_work, set in hook()).
              "#define RENDERSIZE %s" % ("HOOKED_size" if cap is None else "pvj_work"),
              "#define TIME pvj_time",
              "#define TIMEDELTA %s" % S._f(1.0 / pic["fps"]),
              # the frame number with Speed in it, as TIME has: a filter that counts frames is under the flash limit too
              "#define FRAMEINDEX int(pvj_time * %s + 0.5)" % S._f(pic["fps"]),
              "#define PASSINDEX 0",
              "#define DATE vec4(%s, %s, %s, %s)" % (S._f(t.tm_year), S._f(t.tm_mon), S._f(t.tm_mday), S._f(t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)),
              "#define isf_FragNormCoord pvj_norm",
              "#define vv_FragNormCoord pvj_norm",
              "PVJ_HP float pvj_time;", "vec2 pvj_norm;", "vec4 pvj_coord;", "vec4 pvj_color;"]
    if cap is not None:
        lines.append("PVJ_HP vec2 pvj_work;")
    lines += native_glsl("rgb" if plane == "RGB" else ("bt.709" if pic["matrix"] == "rgb" else pic["matrix"]), pic["levels"])
    # The picture, read at a place counted from the top left as the player counts (0 to 1); outside it, its edge.
    lines += ["vec4 pvj_at(vec2 p) { return vec4(pvj_rgb(HOOKED_tex(clamp(p, vec2(0.0), vec2(1.0))).rgb), 1.0); }",
              "vec4 pvj_img_this() { return pvj_at(HOOKED_pos); }",
              "vec4 pvj_img_norm(vec2 n) { return pvj_at(vec2(n.x, 1.0 - n.y)); }",
              "vec4 pvj_img_px(vec2 p) { return pvj_at(vec2(p.x, RENDERSIZE.y - p.y) / RENDERSIZE); }",
              "vec2 pvj_img_size() { return RENDERSIZE; }"]
    lines += S.input_lines(parsed, values)
    # The file's own line numbers, for what the compiler says. "#line n" names the next line n on OpenGL ES and from
    # GLSL 3.30 on, and n + 1 before that (GLSL 1.40, what a Raspberry Pi 4 gets: seen in CI, where the same mistake
    # was reported one line further down).
    lines += ["#if defined(GL_ES) || __VERSION__ >= 330", "#define PVJ_LINE %d" % parsed["line"], "#else",
              "#define PVJ_LINE %d" % max(0, parsed["line"] - 1), "#endif",
              "#line PVJ_LINE", parsed["code"], "",
              "vec4 hook() {",
              "    vec4 pvj_src = HOOKED_tex(HOOKED_pos);"]
    if cap is not None:
        # the size this hook draws at: the same arithmetic as its WIDTH and HEIGHT lines, rounded as the player rounds
        side = "x" if tall else "y"
        lines += ["    pvj_work = HOOKED_size;",
                  "    if (HOOKED_size.%s > %s) pvj_work = floor(HOOKED_size - HOOKED_size * (HOOKED_size.%s - %s) / HOOKED_size.%s + 0.5);"
                  % (side, S._f(cap), side, S._f(cap), side)]
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


def translate(parsed, values=None, controls=None, picture=None, desc="nxlx effect", today=None, lines=None, tall=False):
    """The mpv user shader for a parsed ISF filter: `values` replace the inputs' defaults, `controls` are amount (the
    mix with the picture as it is), speed (of TIME) and half (superseded: work at HALF_LINES at most), `picture` says
    what is under it (see clean_picture), `lines` is the cap on the working size (None: the clip's own size; any
    whole number from 1 up, of which the settings offer DETAILS) and `tall` whether the picture stands (see
    size_lines). The text holds the filter twice, as two hooks of which the player runs one: the first for a picture
    in YUV, the second for one in RGB (a PNG, some streams), so a change between the two kinds in the middle of a
    playlist is right from its first frame without a new text."""
    if parsed.get("kind") != S.FILTER:
        raise ShaderError("this is a generator, not a filter of the playing picture: it is played under Shaders")
    if not re.fullmatch(r"[a-z0-9 ]{1,40}", desc):
        raise ShaderError("bad description")
    values = S.clean_values(parsed, values)
    c = L.clean_fx_controls(controls)
    pic = clean_picture(**(picture or {}))
    t = time.localtime() if today is None else today
    if lines is not None and (isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 8192):
        raise ShaderError("bad working size")
    cap = min(lines or HALF_LINES, HALF_LINES) if c["half"] else lines
    out = ["// nxlx.mastercontrol effect (generated; do not edit)"]
    for plane in ("LUMA", "RGB"):
        out += _block(parsed, values, c, pic, desc, plane, t, cap, bool(tall))
    return "\n".join(out)


# ---- how much work a filter is, counted from its text ------------------------------------------------------------------------
# A rule, not a guess. The count is made only from text whose every loop says how often it runs; a text that does not
# is refused at upload. What that gives: a bound on what an honestly written filter does for one pixel. What it does
# not give: a proof that a shader is cheap (one read of a 4K picture and one of a small one are both "1"; arithmetic
# without loops is not counted at all), and no reading of text can replace measuring on the board.
MAX_FUNCTIONS = 64              # functions and #defines the count follows; more is refused
MAX_NESTING = 4                 # loops inside loops
MAX_STEPS = 400000              # tokens the count may look at in all (a 32 KB file has a few thousand)
BIG = 10 ** 9
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\+\+|--|[-+*/]=|[<>=!]=|&&|\|\||\S")
_NUMBER = re.compile(r"(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)\Z")
_READS = frozenset(("IMG_PIXEL", "IMG_NORM_PIXEL", "IMG_THIS_PIXEL", "IMG_THIS_NORM_PIXEL"))
_WRITES = frozenset(("=", "+=", "-=", "*=", "/=", "++", "--"))
_OPEN = {"(": ")", "{": "}", "[": "]"}


def _number(tokens):
    """The number a few tokens are, or None: 12, 12.5, -3, (8)."""
    while len(tokens) >= 3 and tokens[0] == "(" and tokens[-1] == ")":
        tokens = tokens[1:-1]
    sign = 1.0
    if len(tokens) == 2 and tokens[0] in "+-":
        sign, tokens = (-1.0 if tokens[0] == "-" else 1.0), tokens[1:]
    if len(tokens) == 1 and _NUMBER.match(tokens[0]):
        return sign * float(tokens[0])
    return None


def measure(parsed):
    """(reads, rounds): how often a filter reads the picture for one pixel and how many rounds its loops run for
    one pixel, at most. Every branch counts, a function counts wherever its name appears, loops inside loops
    multiply and loops after each other add up. Raises ShaderError when the text does not let that be counted:

    * a loop that is not `for (int i = <number>; i < <limit>; i++)`, where the limit is a number, a constant given
      as a number (`const int N = 8;`, `#define N 8`) or a number input (counted at its MAX); `<=`, `>`, `>=`, `++i`,
      `i--`, `i += <number>` and `i -= <number>` are the other forms, the counter may be declared before the loop
      (`for (i = 0; ...)`), and the limit may be a local name given one of those once and never changed
      (`int samples = quality;`); so no `while`, no `do`, no `for(;;)`;
    * a loop whose counter is written to in its body, or handed there to something that can write to it (a function
      with an `out` or `inout` argument, a `#define` that holds an assignment), or named in such a `#define`;
    * a `#define` that holds a loop or a read of the picture (either could hide there);
    * a function that calls itself, or functions that call each other in a circle (two functions of one name that
      differ in their arguments may call each other), more than MAX_FUNCTIONS functions and #defines, loops more
      than MAX_NESTING deep.
    """
    lines, defines, steps = [], {}, [0]
    for line in parsed["body"].split("\n"):
        if not line.lstrip().startswith("#"):
            lines.append(line)
            continue
        words = _TOKEN.findall(line.lstrip()[1:])
        if not words or words[0] != "define" or len(words) < 2:
            continue
        name, rest = words[1], words[2:]
        if rest and rest[0] == "(" and re.match(r"\s*#\s*define\s+\w+\(", line):       # a macro with arguments: skip them
            close = rest.index(")") if ")" in rest else len(rest) - 1
            rest = rest[close + 1:]
        if any(w in ("for", "while", "do") for w in rest):
            raise ShaderError("a #define holds a loop (%s): write the loop where it runs, so its length can be counted" % name)
        if any(w in _READS or w == S.IMAGE for w in rest):
            raise ShaderError("a #define reads the picture (%s): write the read where it happens, so it can be counted" % name)
        defines.setdefault(name, []).append(rest)
    tok = _TOKEN.findall("\n".join(lines))
    if len(tok) > MAX_STEPS:
        raise ShaderError("too long to count")
    for word in ("while", "do"):
        if word in tok:
            raise ShaderError("it has a `%s` loop: only `for` loops with a counted length are taken (for (int i = 0; i < 8; i++))" % word)
    # matching brackets, found once, without recursion
    match, stack = {}, []
    for i, t in enumerate(tok):
        if t in _OPEN:
            stack.append(i)
        elif t in (")", "}", "]"):
            if not stack or _OPEN[tok[stack[-1]]] != t:
                raise ShaderError("its brackets do not match")
            match[stack.pop()] = i
    if stack:
        raise ShaderError("its brackets do not match")
    opened = {b: a for a, b in match.items()}
    # numbers with a name: inputs at their largest and smallest, constants given as a number
    highest = {i["name"]: float(i["max"]) for i in parsed["inputs"] if i["type"] in ("float", "long") and "max" in i}
    lowest = {i["name"]: float(i["min"]) for i in parsed["inputs"] if i["type"] in ("float", "long") and "min" in i}
    for i in parsed["inputs"]:
        if i["type"] == "long" and i.get("values"):
            highest[i["name"]], lowest[i["name"]] = float(max(i["values"])), float(min(i["values"]))
    unsure = set()
    for name, bodies in defines.items():
        values = [_number(b) for b in bodies]
        if None in values or name in highest:        # not a plain number, or a second meaning for an input's name
            unsure.add(name)
        else:
            highest[name], lowest[name] = max(values), min(values)
    for i in range(len(tok) - 5):
        if tok[i] == "const" and tok[i + 1] in ("int", "float") and tok[i + 3] == "=":
            end = i + 4
            while end < len(tok) and tok[end] != ";":
                end += 1
            value, name = _number(tok[i + 4:end]), tok[i + 2]
            if value is None or name in unsure or name in defines or any(p["name"] == name for p in parsed["inputs"]):
                unsure.add(name)
            else:
                highest[name], lowest[name] = max(value, highest.get(name, value)), min(value, lowest.get(name, value))
    for name in unsure:
        highest.pop(name, None)
        lowest.pop(name, None)
    # a local name for one of those, given once and never changed: "int samples = quality;" (checked further down,
    # once it is known what can write to a name it is handed)
    aliases = []
    for i in range(1, len(tok) - 4):
        if tok[i] in ("int", "float") and tok[i + 2] == "=" and tok[i - 1] != "const" and re.match(r"[A-Za-z_]\w*\Z", tok[i + 1]):
            end = i + 3
            while end < len(tok) and tok[end] not in (";", ","):
                end = match[end] + 1 if tok[end] in _OPEN else end + 1
            aliases.append((tok[i + 1], tok[i + 3:end]))
    # the functions: a name, round brackets and curly brackets, outside every other bracket
    functions, writers, i = {}, set(), 0
    while i < len(tok):
        t = tok[i]
        if t == "{":
            head = i - 1
            if head >= 1 and tok[head] == ")":
                opening = opened.get(head)
                if opening is not None and opening >= 1 and re.match(r"[A-Za-z_]\w*\Z", tok[opening - 1]):
                    name = tok[opening - 1]
                    functions.setdefault(name, []).append((i + 1, match[i]))
                    if any(w in ("out", "inout") for w in tok[opening + 1:head]):
                        writers.add(name)
            i = match[i] + 1
        elif t == "(":
            i = match[i] + 1
        else:
            i += 1
    # what can write to a counter it is given or that it names: a function with an out or inout argument, a #define
    # that holds an assignment, and a #define that names one of those
    assigning = {name: set(w for words in bodies for w in words) for name, bodies in defines.items()}
    writers |= {name for name, words in assigning.items() if words & _WRITES}
    grown = True
    while grown:
        grown = False
        for name, words in assigning.items():
            if name not in writers and words & writers:
                writers.add(name)
                grown = True
    def plain(tokens, table):
        while len(tokens) >= 4 and tokens[0] in ("int", "float") and tokens[1] == "(" and tokens[-1] == ")":
            tokens = tokens[2:-1]
        value = _number(tokens)
        return table.get(tokens[0]) if value is None and len(tokens) == 1 else value
    given = {}
    for name, value in aliases:
        given.setdefault(name, []).append(value)
    for name, values in given.items():
        if len(values) != 1 or name in highest or name in unsure or name in defines or name in functions or plain(values[0], highest) is None:
            continue
        written = handed = 0
        for j in range(1, len(tok) - 1):
            if tok[j] == name and (tok[j + 1] in _WRITES or tok[j - 1] in ("++", "--")):
                written += 1
            elif tok[j] in writers and ((tok[j + 1] == "(" and name in tok[j + 2:match[j + 1]]) or name in assigning.get(tok[j], ())):
                handed += 1
        if written == 1 and not handed:             # the one write is where it is given
            highest[name], lowest[name] = plain(values[0], highest), plain(values[0], lowest)
    # A name counts as a number only while it means one thing: a constant or a local given once is declared exactly
    # once, an input or a #define never (an argument of a function called `n` would be another `n`).
    declared = {}
    for j in range(1, len(tok)):
        if tok[j] in highest and tok[j - 1] in ("int", "float", "uint", "bool", "vec2", "vec3", "vec4"):
            declared[tok[j]] = declared.get(tok[j], 0) + 1
    for name in list(highest):
        if declared.get(name, 0) != (0 if (name in defines or any(p["name"] == name for p in parsed["inputs"])) else 1):
            highest.pop(name)
            lowest.pop(name, None)
    if len(functions) + len(defines) > MAX_FUNCTIONS:
        raise ShaderError("it has more than %d functions and #defines: too intricate to count" % MAX_FUNCTIONS)
    if "main" not in functions:
        raise ShaderError("the code must have exactly one void main()")

    def tick(n=1):
        steps[0] += n
        if steps[0] > MAX_STEPS:
            raise ShaderError("too intricate to count")

    def bound(tokens, low):
        """The number a loop's limit is, at its largest (or smallest, for a loop that counts down), or None."""
        while len(tokens) >= 4 and tokens[0] in ("int", "float") and tokens[1] == "(" and tokens[-1] == ")":
            tokens = tokens[2:-1]
        value = _number(tokens)
        if value is None and len(tokens) == 1:
            value = (lowest if low else highest).get(tokens[0])
        return value

    def length(head, body):
        """How many rounds the loop with this head runs, and its counter. Raises when the text does not say."""
        plain = "write it as for (int i = 0; i < 8; i++), with a number, a constant or a number input as the limit"
        parts, part = [], []
        for t in head:
            if t == ";":
                parts.append(part)
                part = []
            else:
                part.append(t)
        parts.append(part)
        if len(parts) == 3 and len(parts[0]) >= 3 and parts[0][1] == "=" and re.match(r"[A-Za-z_]\w*\Z", parts[0][0]):
            parts[0] = ["int"] + parts[0]               # the counter was declared before the loop: for (i = 0; ...)
        if len(parts) != 3 or len(parts[0]) < 4 or parts[0][0] not in ("int", "float") or parts[0][2] != "=":
            raise ShaderError("a loop does not say how often it runs (for (%s)): %s" % (S._text(" ".join(head), 60), plain))
        var, start = parts[0][1], _number(parts[0][3:])
        cond, step = parts[1], parts[2]
        if start is None or len(cond) < 3 or cond[0] != var or cond[1] not in ("<", "<=", ">", ">="):
            raise ShaderError("a loop does not say how often it runs (for (%s)): %s" % (S._text(" ".join(head), 60), plain))
        up = cond[1] in ("<", "<=")
        limit = bound(cond[2:], not up)
        if step in ([var, "++"], ["++", var]):
            by = 1.0
        elif step in ([var, "--"], ["--", var]):
            by = -1.0
        elif len(step) == 3 and step[0] == var and step[1] in ("+=", "-=") and _number(step[2:]) is not None:
            by = _number(step[2:]) * (1.0 if step[1] == "+=" else -1.0)
        else:
            by = 0.0
        if limit is None or by == 0.0 or (by > 0) != up:
            raise ShaderError("a loop does not say how often it runs (for (%s)): %s" % (S._text(" ".join(head), 60), plain))
        a, b = body
        for j in range(a, b):
            tick()
            if tok[j] == var:
                if tok[j + 1] in _WRITES or tok[j - 1] in ("++", "--"):
                    raise ShaderError("a loop's counter (%s) is changed inside the loop, so its length cannot be counted" % var)
            elif tok[j] in writers and ((tok[j + 1] == "(" and var in tok[j + 2:match[j + 1]]) or var in assigning.get(tok[j], ())):
                raise ShaderError("a loop's counter (%s) is handed to %s, which can change it, so the loop's length cannot be counted" % (var, tok[j]))
        span = (limit - start) if up else (start - limit)
        if span < 0:
            return 0
        return min(BIG, int(math.ceil(span / abs(by) - 1e-9)) + (1 if "=" in cond[1] else 0))

    def statement(i, end):
        """The index just after the statement that starts at token i."""
        while i < end:
            tick()
            t = tok[i]
            if t == "{":
                i = match[i] + 1
                break
            if t in ("for", "if") and i + 1 < end and tok[i + 1] == "(":
                i = match[i + 1] + 1
                continue
            if t == "else":
                i += 1
                continue
            while i < end and tok[i] != ";":
                i = match[i] + 1 if tok[i] in _OPEN else i + 1
            i += 1
            break
        while i < end and tok[i] == "else":         # an if with its else is one statement
            i = statement(i + 1, end)
        return min(i, end)

    costs, walking = {}, []

    def one(key):
        """What one function body, or one meaning of a #define, costs. `key` is (name, "f" or "d", which one)."""
        if key in costs:
            return costs[key]
        if key in walking:
            raise ShaderError("a function calls itself, or functions call each other in a circle (%s)" % key[0])
        if len(walking) >= MAX_FUNCTIONS:
            raise ShaderError("its functions call each other too many levels deep")
        walking.append(key)
        if key[1] == "f":
            a, b = functions[key[0]][key[2]]
            costs[key] = cost(a, b, 0, key)
        else:
            r = n = 0
            for w in defines[key[0]][key[2]]:
                if w in functions or w in defines:
                    cr, cn = called(w, key)
                    r, n = r + cr, n + cn
            costs[key] = (min(BIG, r), min(BIG, n))
        walking.pop()
        return costs[key]

    def called(name, me=None):
        """What a call of `name` costs at most: the dearest of its meanings. Two functions of one name (they differ
        in their arguments) may call each other, so from inside one of them the name means the others."""
        keys = [(name, "f", k) for k in range(len(functions.get(name, ())))] + [(name, "d", k) for k in range(len(defines.get(name, ())))]
        others = [k for k in keys if k != me]
        if me in keys and not others:
            raise ShaderError("a function calls itself, or functions call each other in a circle (%s)" % name)
        reads = rounds = 0
        for key in others:
            r, n = one(key)
            reads, rounds = max(reads, r), max(rounds, n)
        return reads, rounds

    def cost(a, b, depth, me=None):
        """(reads, rounds) of the tokens a..b: loops in a row add up, a loop multiplies what is in it."""
        reads = rounds = 0
        i = a
        while i < b:
            tick()
            t = tok[i]
            if t == "for":
                if depth >= MAX_NESTING:
                    raise ShaderError("its loops are more than %d deep" % MAX_NESTING)
                if i + 1 >= b or tok[i + 1] != "(":
                    raise ShaderError("a loop does not say how often it runs")
                close = match[i + 1]
                stop = statement(close + 1, b)
                n = length(tok[i + 2:close], (close + 1, stop))
                hr, hn = cost(i + 2, close, depth + 1, me)          # the head is run every round too
                br, bn = cost(close + 1, stop, depth + 1, me)
                reads += min(BIG, (n + 1) * hr + n * br)
                rounds += min(BIG, (n + 1) * hn + n * max(1, bn))
                i = stop
                continue
            if t in _READS:
                reads += 1
            elif t in functions or t in defines:
                r, n = called(t, me)
                reads, rounds = reads + r, rounds + n
            i += 1
        return min(BIG, reads), min(BIG, rounds)

    reads, rounds = called("main")
    return reads, max(1, rounds)


def estimate(parsed):
    """{"reads", "rounds", "weight", "sure", "why"}: the count of `measure` and the class it makes: light (at most 2
    reads, no loop to speak of), medium (at most 12 reads and 16 rounds), heavy. When the text cannot be counted,
    `sure` is false, `why` says what stands in the way and the weight is heavy; such a file is refused at upload.
    Kept with the parsed file, so it is made once for a file's content. A count from the text, not a measurement:
    a bundled filter's class in the library is the measured one (PI4), and this count is what an upload gets."""
    if "_estimate" not in parsed:
        try:
            reads, rounds = measure(parsed)
            weight = "light" if reads <= 2 and rounds <= 4 else ("medium" if reads <= 12 and rounds <= 16 else "heavy")
            parsed["_estimate"] = {"reads": reads, "rounds": rounds, "weight": weight, "sure": True, "why": None}
        except ShaderError as e:
            parsed["_estimate"] = {"reads": 0, "rounds": 0, "weight": "heavy", "sure": False, "why": str(e)}
        except Exception as e:                      # whatever a hostile text does to the count, the panel goes on
            parsed["_estimate"] = {"reads": 0, "rounds": 0, "weight": "heavy", "sure": False, "why": "its work could not be counted (%s)" % type(e).__name__}
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
        # What was asked for and has not reached the player yet. Every request to switch (on, off, a step) gets the
        # next number; the worker's job carries its number and is dropped when a later request exists. `_intent`
        # is what the last request wanted (True: an effect on) until it has been carried out: one button acts on
        # that, not on what the player has, so two quick presses are on and off, never on twice.
        self._gen = 0
        self._intent = None
        self._switched = -1e9                       # when an effect last went on or came off (the gap between switches)
        self.unfit = False                          # the picture that plays cannot take an effect (see UNFIT_FORMATS)
        self.estimated = False                      # its frame rate is the player's estimate (a stream, a live input)
        self.size = None                            # (width, height) the playing picture is stored in, when the player says
        self._waiting = False                       # `error` says an effect could not go on for want of a picture

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

    def board(self):
        return (getattr(self.api, "board", None) or {}).get("kind")

    def detail(self, cfg=None):
        """The box's Effect detail: what was chosen, or what this board has when nobody chose (default_detail)."""
        chosen = (cfg or self._live().config()).get(L.FX_DETAIL)
        return chosen if chosen in DETAILS and not isinstance(chosen, bool) else default_detail(self.board())

    def set_detail(self, value):
        """Choose the Effect detail (full access). The board's own default is kept by keeping nothing, so a box
        where nobody chose follows a later, better default."""
        try:
            value = clean_detail(value)
        except ValueError as e:
            raise ApiError(400, str(e))
        live = self._live()
        keep = None if value == default_detail(self.board()) else value
        with live._cfg:
            cfg = live.config()
            if cfg.get(L.FX_DETAIL) != keep:        # nothing is written when nothing changes
                if keep is None:
                    cfg.pop(L.FX_DETAIL, None)
                else:
                    cfg[L.FX_DETAIL] = keep
                live._save(cfg)
        on = self._seen()
        if on is not None:                          # it applies to the effect that is on, at once
            self.changer.submit(on, controls={"amount": on["controls"]["amount"]})

    def work(self, sid, controls, size=None, cfg=None):
        """How the effect `sid` works now: {"lines": the cap or None, "tall": whether the picture stands}."""
        try:
            bundled = self._path(sid)[1] == "bundled"
        except ApiError:
            bundled = False
        lines = cap_lines(self.detail(cfg), self.board(), sid, bundled, bool(controls.get("half")))
        return {"lines": lines, "tall": bool(size and size[1] > size[0])}

    def working(self, rec, cfg=None):
        """What the panel says of the working size of the effect that is on: {"lines": the cap or None, "auto":
        whether Automatic chose it, "clip": {"width", "height", "lines"} or None when the player did not say,
        "width", "height": what the filter draws, "scaled": whether that is smaller than the clip, "lower": the next
        Effect detail down that would make it smaller still, or None}."""
        work, size = rec.get("work") or {"lines": None}, rec.get("clip")
        out = {"lines": work["lines"], "auto": self.detail(cfg) == "auto" and not rec["controls"].get("half"),
               "clip": None, "width": None, "height": None, "scaled": False, "lower": None}
        if size:
            w, h = work_size(size[0], size[1], work["lines"])
            at = min(w, h)
            out.update(clip={"width": size[0], "height": size[1], "lines": min(size)}, width=w, height=h, scaled=(w, h) != tuple(size),
                       lower=max([d for d in DETAILS if isinstance(d, int) and d < at], default=None))
        return out

    def eight_bit(self):
        """Whether the player switches to 8-bit GPU buffers while an effect is on: on the boards that run mpv with
        its cheap scaling (pvj/hardware.py: a Pi 4), where the mapping's measurement says 16-bit buffers cost frames
        and where the switch was harmless in CI. See Player._apply_fbo for why not elsewhere."""
        kind = (getattr(self.api, "board", None) or {}).get("kind")
        try:
            return "--profile=fast" in hardware.playback_profile({"kind": kind}, True)["mpv_args"]
        except Exception:
            return False

    # -- the library --
    def read(self, data):
        return S.parse(data, S.FILTER)

    def check_upload(self, data):
        parsed = self.read(data)
        translate(parsed)
        e = estimate(parsed)
        if not e["sure"]:
            raise ShaderError("%s. The box takes a filter only when its work for one pixel can be counted from its text" % e["why"])
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
        "estimate", "measured", "moves", "flashes", "speed_max", "presets", "refused"}]: the project's own filters, then
        each third-party pack, then the uploads. "weight" is the class a Pi 4 measured for a bundled filter ("measured"
        has its numbers) and the count from the text ("estimate") for an upload. An input carries "value": what is on
        now, or what putting it on would use."""
        cfg = cfg or self._live().config()
        on = self.current()
        rows = super().library()
        kept = self.presets(cfg)
        for s in rows:
            sid = s["id"]
            s.pop("vibes", None)
            s.pop("cost", None)
            s.update(weight="", estimate=None, measured=None, moves=False, flashes=False, speed_max=None, refused=None,
                     presets=[p["name"] for p in kept.get(sid, [])])
            if s["error"]:
                continue
            try:
                parsed, digest = self._parsed(self._path(sid)[0])
            except (ShaderError, ApiError):
                continue
            e = estimate(parsed)
            seen = measured(sid) if s["source"] == "bundled" else None      # an upload is never one of the files that were measured
            s.update(weight=PI4[sid][0] if seen else e["weight"], measured=seen,
                     estimate={"reads": e["reads"], "rounds": e["rounds"], "weight": e["weight"], "sure": e["sure"], "why": e["why"]},
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
        picture is playing or the picture is one the hooks cannot take (`self.unfit` says which)."""
        self.unfit = self.estimated = False
        self.size = None
        try:
            ipc = self.api.player.ipc
            params = ipc.request("get_property", "video-params")
        except Exception:
            return None
        if not isinstance(params, dict):
            return None
        decoded = params
        w, h = params.get("w"), params.get("h")       # as it is stored: what the hook meets, before any turning
        if all(isinstance(n, int) and not isinstance(n, bool) and 0 < n <= 16384 for n in (w, h)):
            self.size = (w, h)
        try:                        # what the output was given, after the player's own video filters, if it says
            out = ipc.request("get_property", "video-out-params")
            params = out if isinstance(out, dict) and out.get("colormatrix") else params
        except Exception:
            pass
        if not (fits(params) and fits(decoded)):
            self.unfit = True
            return None
        fps = None
        for name in ("container-fps", "estimated-vf-fps"):
            try:
                fps = ipc.request("get_property", name)
            except Exception:
                fps = None
            if isinstance(fps, (int, float)) and not isinstance(fps, bool) and 1.0 <= fps <= 240.0:
                if name == "estimated-vf-fps":
                    fps, self.estimated = near_common(fps), True
                break
        return clean_picture(params.get("colormatrix"), params.get("colorlevels"), fps)

    def _seen(self):
        """The effect that is on, from what this process remembers: no question to the player and no lock, for the
        calls a controller makes (they run on the thread that reads it). A player that was restarted is noticed by
        the worker's look, within a second."""
        rec, player = self.on, self.api.player
        if rec is None or getattr(player, "effect_shader", None) is None or player.effect_serial != rec["epoch"]:
            return None
        return rec

    def _blocked(self):
        """Why a controller's wish for an effect cannot even be noted, or None: what can be said without asking the
        player. Whether there is a picture is found out by the worker, which then says so in `error`."""
        vibes = getattr(self.api, "vibes", None)
        if getattr(self.api.player, "source_shader", None) or (vibes is not None and vibes.running):
            return GENERATOR_HAS_IT
        return None

    def available(self):
        """(True, None), or (False, why an effect cannot be put on now, in plain words)."""
        if not self.enabled():
            return False, "The Shaders and Vibes module is off."
        player = self.api.player
        vibes = getattr(self.api, "vibes", None)
        if getattr(player, "source_shader", None) or self._live().on_screen() is not None or (vibes is not None and vibes.running):
            return False, GENERATOR_HAS_IT
        if self.picture() is None:
            if self.unfit:
                return False, UNFIT + " (it has no colour planes the filter could read). Play another clip."
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
        work = state.get("work") or {}
        return translate(parsed, dict(state["values"], **state.get("held", {})), state["controls"], state["picture"], desc,
                         lines=work.get("lines"), tall=work.get("tall", False))

    def _key(self, parsed, digest, state):
        """What the GPU's word about a text is remembered under: the file, the shape of its values, the working size
        and whether it is drawn at all (at amount 0 the player leaves the hook out, and has then looked at nothing)."""
        work = state.get("work") or {}
        return (digest, S.shape_of(parsed, dict(state["values"], **state.get("held", {}))), work.get("lines"), bool(work.get("tall")),
                state["controls"]["amount"] > 0)

    def _now(self):
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def show_wait(self, job):
        """Seconds the worker is to wait before it carries out this job (shaderlive.Changer asks). Off never waits.
        An effect goes on at the earliest SWITCH_GAP after the last switch, so a button held down, a sequencer or a
        fader mapped to Next cannot switch a filter on and off faster than about three times a second; the owner's
        "faster" opt-in lifts that."""
        if job.get("off") or self.faster():
            return 0.0
        return max(0.0, self._switched + SWITCH_GAP - self._clock())

    def put(self, sid, values=None, controls=None, preset=None, serial=None, epoch=None, queued=False, gen=None):
        """Put an effect on over what plays (in place of the one that is on, if any). Without values or a preset it
        uses the preset called default, else the file's defaults. With `serial` and `epoch` (the worker's call),
        only if no effect went on or off and nothing was played or stopped since they were handed out; None is
        returned then. Returns {"ok": True, "id"}; raises 409 when there is no picture to put it on, 422 when the
        file cannot be translated or the GPU refuses it (the effect before it stays on, else none)."""
        self._need()
        path, _ = self._path(sid)
        with self._lock:
            if gen is not None and gen != self._gen:    # something was asked for after this (an Off, another effect)
                return None
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
                state.update(clip=self.size, work=self.work(sid, state["controls"], self.size, cfg))
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
            if key not in self._checked and key[-1] and self._gpu_output():       # at amount 0 no pass is drawn: nothing to wait for
                try:
                    tap = self._tap(player.socket_path)
                except OSError:
                    tap = None
            try:
                try:
                    player.effect_8bit = self.eight_bit()
                    new = player.put_effect(out, serial, epoch)
                except PlayerError as e:
                    self._cleanup({before["path"]} if before else set())
                    raise ApiError(503, str(e))
                if new is None:                         # the screen changed hands meanwhile
                    self._cleanup({before["path"]} if before else set())
                    if serial is None:
                        raise ApiError(409, self.available()[1] or "the screen changed hands; try again")
                    return None
                if gen is not None and gen != self._gen:    # an Off came while the player was taking it: it does not stay
                    try:
                        player.clear_effect(new, "off")
                    except PlayerError:
                        pass
                    self.on = None
                    self._cleanup(set())
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
            self.error = None                           # an effect is on: whatever went wrong before it is over
            self.on = {"id": sid, "values": state["values"], "held": state["held"], "controls": state["controls"], "picture": state["picture"],
                       "clip": state["clip"], "work": state["work"],
                       "path": out, "desc": desc, "epoch": new, "digest": digest, "preset": name,
                       "checked": True if (verdict == "ok" or key in self._checked) else None}
            self.recent, self.last = sid, None
            self._switched = self._clock()
            if gen is None:
                self._intent = None                     # put on directly: what the player has is what was wanted
            self._cleanup({out})
            self.changer.keep()                         # look at the picture under it from now on
            return {"ok": True, "id": sid}

    def play_job(self, job):
        """The worker's call for a switch that was asked for without waiting: a whole effect (a step, on, a preset
        of another effect), or Off from a controller's one button."""
        try:
            if job.get("off"):
                if job["gen"] == self._gen:
                    self.off(asked=job["gen"])
                return
            if not self.enabled():
                return
            try:
                self.put(job["id"], job.get("values"), job.get("controls"), job.get("preset"), serial=job["serial"], epoch=job["epoch"], queued=True,
                         gen=job.get("gen"))
            except ApiError as e:
                self.error = {"id": job["id"], "message": e.message, "at": self._now()}
                self._waiting = e.status == 409         # there was nothing to put it on: over once there is
        finally:
            if job.get("gen") == self._gen:
                self._intent = None                     # carried out, or it could not be: the player's word counts again

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
                seen = self.picture() if look else None
                if look and seen is None and self.unfit:        # a picture the hooks cannot take: not "on" over nothing drawn
                    try:
                        self.api.player.clear_effect(p["epoch"], "format")
                    except Exception:
                        pass
                    self.on, self.last, self._switched = None, ENDED["format"], self._clock()
                    self._cleanup(set())
                    return False
                state = {"values": dict(p["values"], **job["values"]), "held": dict(job["held"]),
                         "controls": self.limit(parsed, dict(p["controls"], **job["controls"])),
                         "picture": seen or p["picture"]}
                state["clip"] = (self.size or p.get("clip")) if seen else p.get("clip")
                state["work"] = self.work(p["id"], state["controls"], state["clip"])
                if look:
                    self.changer.keep()
                    self.guard.sample(p)
                    # a frame rate that only wobbles, or that no line of this filter uses, is no reason for a new text
                    steady = steady_picture(state["picture"], p["picture"], parsed.get("clock"), self.estimated)
                    if steady == p["picture"] and state["controls"] == p["controls"] and state["work"] == p.get("work"):
                        # What the panel says of the picture follows it all the same: the clip's size, and the rate
                        # of a clip under a filter that never reads the clock (the text on screen keeps the old
                        # rate, in lines that filter does not use). An estimated rate that only wobbles stays.
                        known = state["picture"]
                        if self.estimated and abs(known["fps"] - p["picture"]["fps"]) <= FPS_SAME * p["picture"]["fps"]:
                            known = p["picture"]
                        if (known, state["clip"]) != (p["picture"], p.get("clip")) and self.on is p:
                            self.on = dict(p, picture=known, clip=state["clip"])
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
            if key not in self._checked and key[-1] and self._gpu_output():
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
            if self.error and self.error["id"] == p["id"]:
                self.error = None                       # the new text was taken: what was refused before it is not on
            self.on = dict(p, values=state["values"], held=state["held"], controls=state["controls"], picture=state["picture"], path=out, desc=desc,
                           clip=state["clip"], work=state["work"],
                           digest=digest, preset=preset, checked=True if (verdict == "ok" or key in self._checked) else p["checked"])
            self._cleanup({out})
            return bool(state["held"])

    def off(self, why=None, asked=None):
        """Take the effect off (the Off button, or the module going off). Never waits for the GPU: it is one request
        to the player. Whatever was asked for before it and has not reached the player is dropped, also when no
        effect is on: the player's effect serial and this engine's own count both move on. `asked` is the worker
        carrying out an Off that was already counted (off_soon)."""
        if asked is None:
            self._gen += 1
            self._intent = False
            self.changer.clear()
        self.error = None                               # nothing is on and nothing is wanted: no complaint applies
        rec = self.on
        try:
            was = self.api.player.clear_effect(None, "off")
        except Exception:
            was = False
        if was:
            self._switched = self._clock()
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
        on = self._seen()
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
        """Note that this effect is to go on and return: the worker puts it on. Nothing here asks the player."""
        player = self.api.player
        self._gen += 1
        self._intent = True
        self.changer.show({"id": sid, "preset": preset, "serial": getattr(player, "effect_serial", 0), "epoch": getattr(player, "source_epoch", 0),
                           "gen": self._gen})

    def off_soon(self):
        """Off, noted for the worker (a controller's button: its thread asks the player nothing). What was asked for
        before it is dropped at once, by the count; the player hears of it when the worker comes round, which is at
        once unless the GPU is looking at a new filter."""
        self._gen += 1
        self._intent = False
        self.changer.show({"off": True, "gen": self._gen})

    def apply_preset(self, body):
        """{"name"} or {"index": 1 to 16} for the effect that is on, or with "id" for another one, which is then put
        on with it. Answers at once; the worker does it."""
        self._need()
        on = self._seen()
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
            why = self._blocked()
            if why:
                raise ApiError(409, why)
            self._queue(sid, name)
        return {"ok": True, "id": sid, "preset": name}

    def step(self, direction):
        """The next filter of the library, or the one before, put on in place of the one that is on (the first or the
        last when none is). Answers at once; the worker puts it on, and only if nothing was played, stopped or put
        on in between."""
        self._need()
        if isinstance(direction, bool) or not isinstance(direction, int) or direction not in (1, -1):
            raise ApiError(400, "dir must be 1 (next) or -1 (the one before)")
        why = self._blocked()
        if why:
            raise ApiError(409, why)
        ids = self.order()
        if not ids:
            raise ApiError(409, "there is no effect that can be put on")
        on = self._seen() if self._intent is not False else None
        player = self.api.player
        wish = self.changer.queued()
        wish = wish if wish and (wish.get("serial"), wish.get("epoch")) == (getattr(player, "effect_serial", 0), getattr(player, "source_epoch", 0)) else None
        at = (wish or {}).get("id") or (on["id"] if on else None)
        nxt = ids[(ids.index(at) + direction) % len(ids)] if at in ids else ids[0 if direction == 1 else -1]
        self._queue(nxt)
        return {"ok": True, "id": nxt}

    def toggle(self):
        """On or off from one button (a controller's): off if an effect is on or on its way, otherwise the one that
        was on last (or the first of the library) is put back. It acts on what was last asked for, so two quick
        presses are on and off whether or not the worker came round in between. Answers at once; the worker does
        it, and the player is not asked anything here."""
        self._need()
        if self._intent if self._intent is not None else (self._seen() is not None):
            self.off_soon()
            return {"ok": True, "on": False}
        why = self._blocked()
        if why:
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
        if self.error and self._waiting and ok:     # "nothing is playing" is no longer so
            self.error = None
        if not self.error:
            self._waiting = False
        showing = None
        if on:
            wish = self.changer.pending() or {}
            mine = (wish.get("epoch"), wish.get("id")) == (on["epoch"], on["id"])
            row = next((s for s in rows if s["id"] == on["id"]), None)
            showing = {"id": on["id"], "name": on["id"][:-3], "controls": dict(on["controls"]), "preset": on.get("preset"),
                       "values": {i["name"]: i["value"] for i in row["inputs"] if i["type"] != "event"} if row else dict(on["values"]),
                       "pending": bool(mine), "checked": on["checked"], "picture": dict(on["picture"]), "working": self.working(on)}
            for x in self._fresh(on["desc"]):
                if isinstance(x.get("avg"), (int, float)) and not isinstance(x["avg"], bool) and x["avg"] > 0:
                    showing["pass_ms"] = round(x["avg"] / 1e6, 2)
            seen = self.guard.sample(on)
            showing.update(load=seen["state"], drops_per_second=seen["drops_per_second"])
        else:
            self.guard.sample(None)
        return {"enabled": enabled, "effects": rows, "on": showing, "available": ok, "unavailable": why, "error": self.error, "last": self.last,
                "controls": {"amount": {"min": 0.0, "max": 1.0, "default": 1.0}, "speed": {"min": S.SPEED_MIN, "max": S.SPEED_MAX, "default": 1.0},
                             "half": {"default": False, "superseded": "detail"}},
                "detail": self.detail_state(),
                "faster": self.faster() if enabled else False,
                "limits": {"bytes": S.MAX_SOURCE, "inputs": S.MAX_INPUTS, "uploads": S.MAX_UPLOADS, "presets": L.MAX_PRESETS, "name": 40,
                           "controls": L.MAX_CONTROLS, "reads": MAX_READS, "rounds": MAX_ROUNDS, "at_once": 1}}

    def detail_state(self):
        """{"value": the box's Effect detail, "default": this board's, "choices", "board", "auto": what Automatic
        works at on this board ({"lines", "other", "lower": {file: lines}, "measured"}) or None where it scales
        nothing}."""
        board = self.board()
        row = AUTO.get(board)
        return {"value": self.detail(), "default": default_detail(board), "choices": list(DETAILS), "board": board,
                "auto": {"lines": row["lines"], "other": row["other"], "lower": dict(row["lower"]), "measured": row["measured"]} if row else None}

    def api_config(self, body, device, client):
        """Full access: {"detail": "auto" | 540 | 720 | "full"}, the box's Effect detail."""
        self._need()
        if set(body) - {"detail"} or "detail" not in body:
            raise ApiError(400, "send detail: one of %s" % ", ".join(str(d) for d in DETAILS))
        self.set_detail(body["detail"])
        return self.state()

    def api_get(self, body, device, client):
        return self.state()

    def api_put(self, body, device, client):
        """Live access: {"id": "fx-vignette.fs", "values"?, "controls"?: {"amount"?, "speed"?, "half"?}, "preset"?}
        puts an effect on over what plays; {"off": true} takes it off; {"toggle": true} is a controller's one button."""
        self._need()
        for word in ("off", "toggle"):
            if word in body and body[word] is not True:
                raise ApiError(400, "%s must be true" % word)
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
