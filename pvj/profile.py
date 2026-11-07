# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What this node is asked to do, by tier: one table where the renderer, the effects, the shaders and the panel used to
each keep their own list of board kinds.

A tier (hardware.tier) is lean (Pi 3 class), standard (Pi 4 class), full (Pi 5, x86) or unknown (a Pi below the floor,
an ARM board the node cannot name). `of(kind)` is the row for a kind; `describe(kind, model)` is the same row in words,
each value with its reason, for the Renderer card and the player's report.

Kept by exact kind elsewhere, because the number was measured on that chip only and is not a tier question:
shaderlive.AUTO_BOARDS (the per-shader heights measured on a Pi 4), probe.advice (what was measured on a Pi 4, what a
Pi 3 was not tested at, that a Pi 5 has no H.264 decoder), and effects' Pi 4 table of lines (a row here names it, the
numbers stay in effects.py with their measurements).
"""

from . import hardware

KINDS = ("pi3", "pi4", "pi5", "pi-other", "arm-other", "x86")     # every kind hardware.detect_board() returns
SOURCES_MAX = 8                         # the renderer's own bound on sources (D101)

# One row per tier. "small" is the group that was SMALL_BOARDS (a Pi 3 or 4): Automatic draws 540 lines inside (the spike
# of 2026-10-16, D95), pvj-render gets mpv's cheap scalers and no deblocking (D120), Automatic final pass is allowed
# (D108), and the panel warns when a 1080p clip goes to a source while two others play (D101).
#
# Sources (D101), measured on the owner's Pi 4 (2026-10-20), software H.264 decode alone: 1080p 83 fps for one stream,
# 44 each for two, 22 each for four; 720p 161, 88, 44; 540p 229, 137, 67. A Pi 4 carries about 2 at 1080p, 4 at 720p, 6
# at 540p, so it has 4 (an idle source costs nothing). A Pi 3 (not measured, about half a Pi 4) has 2. Others: 4 by default,
# 8 at most.
PROFILES = {
    "lean": {"small": True, "sources_default": 2, "sources_cap": 2,
             "shader_heights": 3, "shader_default": 540, "effects": "lean"},
    "standard": {"small": True, "sources_default": 4, "sources_cap": 4,
                 "shader_heights": 3, "shader_default": 540, "effects": "standard"},
    "full": {"small": False, "sources_default": 4, "sources_cap": SOURCES_MAX,
             "shader_heights": 4, "shader_default": 720, "effects": None},
    # Nothing measured: the careful values for effects and shaders (as before), the heavy path for the renderer.
    "unknown": {"small": False, "sources_default": 4, "sources_cap": SOURCES_MAX,
                "shader_heights": 3, "shader_default": 540, "effects": "lean"},
}


def of(kind):
    """The row for a board kind (anything unnamed, None too, has the unknown tier's)."""
    return PROFILES[hardware.tier(kind)]


def small(kind):
    return of(kind)["small"]


def sources_default(kind):
    return of(kind)["sources_default"]


def sources_cap(kind):
    return of(kind)["sources_cap"]


def inside_lines(kind):
    """Lines pvj-render draws inside under Automatic, or None for the output's own size."""
    return 540 if small(kind) else None


def effects_row(kind):
    """"lean", "standard" or None: which of effects.py's Automatic tables this kind uses (None: nothing scaled)."""
    return of(kind)["effects"] if kind in KINDS else None


def shader_heights(kind):
    return of(kind)["shader_heights"]


def shader_default(kind):
    return of(kind)["shader_default"]


_TIER_WORDS = {"lean": "lean (Pi 3 class)", "standard": "standard (Pi 4 class)", "full": "full (Pi 5 or a PC)",
               "unknown": "unknown (not measured, careful values)"}


def describe(kind, model=""):
    """{"tier", "kind", "model", "values": [{"name", "value", "why"}]}: the settings this node's tier gives, in plain
    words, each with its reason."""
    p = of(kind)
    tier = hardware.tier(kind)
    on = "on" if p["small"] else "off"
    sm = p["small"]
    values = [
        {"name": "Picture detail inside (Automatic)", "value": "540 lines" if sm else "the screen's own size",
         "why": "A Pi 3 or 4 draws the picture at 540 lines and scales it once (measured 2026-10-16, D95)." if sm
         else "This graphics chip has room to draw at the screen's own size."},
        {"name": "Cheap scalers (new renderer)", "value": on,
         "why": "Bilinear scaling drops far fewer frames on a Pi at 1440p." if sm else "Nothing needs the lighter scalers here."},
        {"name": "Video deblocking skipped (new renderer)", "value": on,
         "why": "Skipping the deblocking filter saves decoding time on a Pi (D120)." if sm else "The decoder has room to keep it."},
        {"name": "Final picture, Automatic (new renderer)", "value": "may drop to 720 lines when busy" if sm else "always the screen's size",
         "why": "A busy Pi 3 or 4 keeps motion steadier at 720 lines (D108)." if sm else "The graphics chip has the room."},
        {"name": "Sources by default", "value": str(p["sources_default"]),
         "why": "A Pi 4 carries about 4 clips at 720p; a Pi 3 about half that (D101)." if sm else "Nothing is measured here; 4 is a modest start."},
        {"name": "Most sources", "value": str(p["sources_cap"]),
         "why": "More sources than this node decodes would only drop frames (D101)." if sm else "The renderer's own limit."},
        {"name": "Load warning", "value": on,
         "why": "The panel warns when a clip over 720p goes to a source while two others play (D101)." if sm
         else "No warning: nothing says this node runs short."},
        {"name": "Shader heights offered", "value": "up to %d lines" % [360, 540, 720, 1080][p["shader_heights"] - 1],
         "why": "On a Pi 4 even the lightest shaders drop frames at 1080 lines." if p["shader_heights"] == 3 else "Nothing is measured here, so every height is offered."},
        {"name": "Shader height by default", "value": "%d lines" % p["shader_default"],
         "why": "All bundled shaders keep up at 540 lines on a Pi 4." if p["shader_default"] == 540 else "A guess until someone measures this board."},
        {"name": "Effect detail by default", "value": "Automatic" if p["effects"] else "full size",
         "why": "Automatic scales each filter to what this board holds." if p["effects"] else "No effect has been measured here."},
    ]
    return {"tier": tier, "tier_words": _TIER_WORDS[tier], "kind": kind, "model": model or "", "values": values}
