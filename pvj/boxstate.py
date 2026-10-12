# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box as its lights need it: one picture of what plays, read from memory, for the MIDI controllers' lights
(pvj/midi.py) and for the state sent back over OSC (pvj/oscfeedback.py, D82). Both read this, so a pad that is lit on
a controller is lit in a TouchOSC layout: there is no second copy of the rules.

It moved here from MidiHub._snapshot unchanged. It runs on its caller's own thread (the lights thread, the feedback
thread), never under a lock of the caller's, and never takes the shader engine's lock."""
import os

PLAYER_EVERY = 0.6      # how often, at most, one caller asks the player what is playing


def snapshot(api, now, fresh=False, holder=None, every=PLAYER_EVERY):
    """The picture, from memory, plus ONE status call to the player at most every `every` seconds. `holder` keeps
    what the player said last between calls (`_player_seen`, and `_player_raw`: the player's own answer). A part that
    cannot be read keeps its quiet default."""
    snap = {"pads": [], "pad_shaders": [], "pad_presets": [], "pad_ndi": [], "ndi": None, "shader_on": False, "playing": None, "running": False, "paused": False, "playlist": False, "blackout": False, "fade": None,
            "vibes": False, "vibes_ready": False, "sets": {}, "set": None, "shader": None, "presets": [], "preset": None,
            "scenes": [], "applying": None, "effect": None, "effect_ready": False}
    try:
        snap["pads"] = [[str(p.get("file") or "") for p in b["pads"]] for b in api.settings.data["pads"]["banks"]]
        snap["pad_shaders"] = [[("" if p.get("file") else str(p.get("shader") or "")) for p in b["pads"]]
                               for b in api.settings.data["pads"]["banks"]]
        snap["pad_presets"] = [[("" if p.get("file") else str(p.get("preset") or "")) for p in b["pads"]]
                               for b in api.settings.data["pads"]["banks"]]
        snap["pad_ndi"] = [[(api.pad_ndi(p) or "") for p in b["pads"]] for b in api.settings.data["pads"]["banks"]]
    except Exception:
        pass
    if fresh or holder._player_seen is None or now - holder._player_seen[0] >= every:
        seen, raw = {"running": False, "paused": False, "playing": None, "playlist": False}, {}
        try:
            st = api.player.status()
            raw = dict(st)
            path = st.get("path")
            seen = {"running": bool(st.get("running")) and isinstance(path, str) and bool(path), "paused": bool(st.get("paused")),
                    "playing": os.path.basename(path) if isinstance(path, str) else None,
                    "playlist": isinstance(st.get("playlist_count"), int) and st["playlist_count"] > 1}
        except Exception:
            pass
        holder._player_seen = (now, seen)
        holder._player_raw = raw
    snap.update(holder._player_seen[1])
    try:
        # The NDI source on the screen, by its id, for the pad that holds it (D62): from what the player said last
        # and what the panel noted when it loaded the pipe; nobody is asked.
        ndi = getattr(api, "ndi", None)
        cur = ndi.current if ndi is not None else None
        if cur and (getattr(holder, "_player_raw", None) or {}).get("path") == ndi.fifo:
            snap["ndi"] = cur.get("id")
    except Exception:
        pass
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
        snap["mapping"] = bool(api.mapper.remote_on())
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
