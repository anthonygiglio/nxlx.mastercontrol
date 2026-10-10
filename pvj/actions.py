# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The one table of what a controller can ask of the box, shared by MIDI and OSC (D75).

An action has a name ("fade", "clip_next", "shader_preset_3", "position_y" ...) and becomes one call into the API,
the same call the panel makes. MIDI maps a control to an action by its name (pvj/midi.py); OSC maps an address to
one (pvj/osc.py). Whatever is added here is added for both, and a later Map Mode needs only this table.

Two kinds are here: `press(name)`, for what a button does, and `level(name, value)`, for a level in its own real
units (percent, degrees, times). How a knob's 0 to 127 becomes those units, with a centre and a curve, is MIDI's
business (midi.level_value); OSC sends real units and gets no dead zone. Every value is checked by the API itself.
What needs more than a name (a pad, a Room scene, the controllers' bank) stays with its caller."""

SLOTS = 8           # shader controls, shader presets and effect controls a controller can reach
FADE_SECONDS = 2
_PRESS = {
    "stop": ("/api/control", {"action": "stop"}), "pause": ("/api/control", {"action": "pause"}),
    "reset": ("/api/control", {"action": "reset"}),
    "clip_next": ("/api/control", {"action": "next"}), "clip_prev": ("/api/control", {"action": "prev"}),
    "fadeout": ("/api/fadeout", {"seconds": FADE_SECONDS}), "fadein": ("/api/fadein", {"seconds": FADE_SECONDS}),
    "fade": ("/api/fade", {"seconds": FADE_SECONDS}),       # out, or in if the screen is down: the API looks and decides
    "rotate": ("/api/control", {"action": "rotate", "value": "toggle"}),       # the next quarter turn
    "flip_h": ("/api/control", {"action": "flip_h", "value": "toggle"}), "flip_v": ("/api/control", {"action": "flip_v", "value": "toggle"}),
    "mute": ("/api/control", {"action": "mute", "value": "toggle"}), "loop": ("/api/control", {"action": "loop", "value": "toggle"}),
    "seek_back": ("/api/control", {"action": "seek", "value": -10}), "seek_forward": ("/api/control", {"action": "seek", "value": 10}),
    "overlay": ("/api/overlay", {"toggle": True}), "test_pattern": ("/api/testpattern", {"on": "toggle"}),
    "blackout": ("/api/blackout", {"on": None}),            # None: the other state, decided by the caller from the mix
    "vibes": ("/api/vibes", {"on": None}),                  # the same, from whether the rotation runs
    "vibes_next": ("/api/vibes", {"next": True}),
    "vibes_ambient": ("/api/vibes", {"on": True, "set": "Ambient"}), "vibes_show": ("/api/vibes", {"on": True, "set": "Show"}),
    "shader_next": ("/api/shaders/step", {"dir": 1}), "shader_prev": ("/api/shaders/step", {"dir": -1}),
    "effect_next": ("/api/effects/step", {"dir": 1}), "effect_prev": ("/api/effects/step", {"dir": -1}),
    "effect_toggle": ("/api/effects", {"toggle": True}),
}
# Mapping mode (pvj/mapper.py, "from a controller"): entering and leaving it, and what works only inside it. The
# box refuses every one of these unless the owner switched "Controllers may adjust the mapping" on.
NUDGE = "/api/mapper/nudge"
_PRESS.update({
    "mapping_mode": (NUDGE, {"mode": "toggle"}),
    "map_surface_next": (NUDGE, {"surface": 1}), "map_surface_prev": (NUDGE, {"surface": -1}),
    "map_corner_next": (NUDGE, {"corner": 1}), "map_corner_prev": (NUDGE, {"corner": -1}),
    "map_left": (NUDGE, {"steps": [-1, 0]}), "map_right": (NUDGE, {"steps": [1, 0]}),
    "map_up": (NUDGE, {"steps": [0, -1]}), "map_down": (NUDGE, {"steps": [0, 1]}),
    "map_step": (NUDGE, {"step": "next"}), "map_undo": (NUDGE, {"undo": True}),
})
for _n in range(1, SLOTS + 1):
    _PRESS["shader_preset_%d" % _n] = ("/api/shaders/preset", {"index": _n})
    _PRESS["shader_control_%d" % _n] = ("/api/shaders/values", {"control": _n, "press": True})
    _PRESS["effect_control_%d" % _n] = ("/api/effects/values", {"control": _n, "press": True})
    _PRESS["scene_%d" % _n] = ("/api/room/scene", {"number": _n})
# a level -> (path, the key its value goes under, digits it is rounded to)
_LEVEL = {"opacity": 2, "size": 2, "position": 2, "position_y": 2, "speed": 2, "volume": 2}
_SHADER = {"shader_speed": "speed", "shader_hue": "hue", "shader_brightness": "brightness"}


def press(name):
    """The call a button with this action makes: (path, body), a fresh body each time. None for a name that is
    not a press known here."""
    found = _PRESS.get(name)
    return (found[0], dict(found[1])) if found else None


def level(name, value):
    """The call that sets a level to `value` in its own units: (path, body). None for a name that is not a level
    known here. The API checks the range."""
    if name in _LEVEL:
        return "/api/control", {"action": name, "value": round(value, _LEVEL[name])}
    if name in _SHADER:
        return "/api/shaders/values", {"controls": {_SHADER[name]: round(value, 2)}}
    if name == "effect_amount":
        return "/api/effects/values", {"controls": {"amount": round(value, 3)}}
    return None


def nudge(axis, count):
    """A knob turned in mapping mode: `count` steps along "x" or "y" for the chosen corner."""
    return NUDGE, {"steps": [count, 0] if axis == "x" else [0, count]}


def control(kind, n, value):
    """The n-th input of the shader on screen ("shader") or of the effect that is on ("effect"), set from a control's
    position, 0 to 127, as a knob does: a number spreads over its own range, a switch is on from 64 up."""
    path = "/api/shaders/values" if kind == "shader" else "/api/effects/values"
    return path, {"control": n, "level": value}
