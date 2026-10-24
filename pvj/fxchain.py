# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Chains of effects on the New renderer (project-log/DECISIONS.md, D106 step b).

pvj-render runs up to three effects in order over any source: slots 1 to 3 of a source 2 to N, and slots 2 and 3
of source 1, whose slot 1 is the one effect of pvj/effects.py (with its worker, Vibes' guard, the floor and the
controllers). An effect here is put on, given values and taken off through this class: the same effect files, the
same checks of a file and of its values, the same flash limit and the same presets as pvj/effects.py, written as the
renderer's program files (effects.translate_native), with values set while it plays (nothing compiled).

What it is not: it has no worker, no queue and no guard of its own (a value is one message to the renderer, which
answers at once), it is not on any controller, and the panel does not offer it before step e: in this step it is the
API's (`GET` and `POST /api/effects/chain`). Classic mpv has no chain: everything here answers 409 there. A source's
Stop takes its chain off, as Stop takes source 1's effect off (D74), and a restart of the player loses it, as it
loses source 1's effect.
"""

import os
import re
import threading

from . import shaderlive as L, shaders as S
from .api import ApiError
from .effects import translate_native
from .player import PlayerError
from .shaders import ShaderError

STEM = "nxlx-chain"
MAX_SLOTS = 3


class Chains:
    def __init__(self, api):
        self.api = api
        self._lock = threading.Lock()
        self._serial = 0
        self.on = {}            # {(source, slot): {"id", "values", "controls", "preset", "path"}}

    # -- files for the player --
    def _write(self, text):
        rundir = self.api.player.rundir
        self._serial += 1
        name = os.path.join(rundir, "%s-%d-%d.prog" % (STEM, os.getpid(), self._serial))
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

    def _cleanup(self):
        """Remove the program files no slot holds any more (and those an earlier run of the service left)."""
        keep = {r["path"] for r in self.on.values()}
        rundir = self.api.player.rundir
        try:
            names = os.listdir(rundir)
        except OSError:
            return
        for n in names:
            full = os.path.join(rundir, n)
            if re.fullmatch(r"%s-\d+-\d+\.prog(\.tmp)?" % STEM, n) and full not in keep:
                try:
                    os.unlink(full)
                except OSError:
                    pass

    def _seen(self):
        """What the chains hold, as the player knows it: a slot the player no longer holds (a Stop, a restart) is
        forgotten here too."""
        held = self.api.player.chain_state() if hasattr(self.api.player, "chain_state") else {}
        for key in [k for k, r in self.on.items() if held.get(k) != r["path"]]:
            if key not in held and self.api.player._chain_there(key[0], key[1], self.on[key]["path"]):
                continue                    # a slow build the renderer took after all (Player.chain_put, "unknown")
            del self.on[key]
        return self.on

    def _place(self, body, generator=False):
        player = self.api.player
        source, slot = body.get("source"), body.get("slot")
        chains = getattr(player, "native_chains", None)
        if not (chains() if chains else False):
            raise ApiError(409, "a chain of effects needs the New renderer (System > Screen output)")
        n = player.sources() if hasattr(player, "sources") else 1
        if isinstance(source, bool) or not isinstance(source, int) or not 1 <= source <= n:
            raise ApiError(400, "source must be 1 to %d" % n)
        slots = player.chain_slots(source, generator)
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in slots:
            raise ApiError(400, "slot must be %s on source %d%s" % (" or ".join(map(str, slots)), source,
                                                                     " (slot 1 is the effect under Effects)" if source == 1 else ""))
        return source, slot

    # -- the API --
    def state(self):
        fx = self.api.effects
        with self._lock:
            rows = [{"source": s, "slot": k, "id": r["id"], "values": r["values"], "controls": r["controls"], "preset": r["preset"],
                     "kind": "shader" if k == 0 else "effect"}
                    for (s, k), r in sorted(self._seen().items())]
        try:
            native = bool(self.api.player.native_chains())
        except Exception:
            native = False
        # available: a chain can be used at all (an effect with Effects on, a shader as a source with Shaders on);
        # "effects" and "shaders" say which (the review of #171: a generator plays with Effects off)
        effects, shaders = fx.enabled(), self.api.shaders.enabled()
        return {"available": native and (effects or shaders), "effects": native and effects, "shaders": native and shaders,
                "chains": rows,
                "slots": {s: list(self.api.player.chain_slots(s, True)) for s in range(1, (self.api.player.sources() if native else 1) + 1)}
                if native else {}}

    def put(self, body):
        """{"source", "slot", "id", "values"?, "controls"?, "preset"?}: that effect in that slot (in place of the one
        there). Without values or a preset it starts from the preset called default, else the file's defaults."""
        fx = self.api.effects
        fx._need()
        source, slot = self._place(body)
        sid = body.get("id")
        if not isinstance(sid, str):
            raise ApiError(400, "id must be the effect's name")
        path, _ = fx._path(sid)
        try:
            parsed, _digest = fx._parsed(path)
            start, stored, name = fx.start(parsed, fx._live().config(), sid, body.get("preset"))
            values = body.get("values")
            if values is not None:
                start = dict(start, **S.clean_values(parsed, values))
                name = None if values else name
            events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
            start = {n: v for n, v in start.items() if n not in events}
            controls = fx.limit(parsed, L.clean_fx_controls(body.get("controls"), stored))
            text = translate_native(parsed, start, controls)
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        with self._lock:
            out = self._write(text)
            try:
                verdict, message = self.api.player.chain_put(source, slot, out)
            except PlayerError as e:
                self._seen()
                self._cleanup()
                raise ApiError(503, str(e))
            if verdict == "unknown":
                # no word from the renderer in time (a slow build): not a refusal; kept, and found again by its path
                self.on[(source, slot)] = {"id": sid, "values": start, "controls": controls, "preset": name, "path": out}
                self._cleanup()
                return {"ok": True, "source": source, "slot": slot, "id": sid, "note": "the renderer has not said yet that it took it"}
            self._seen()
            if verdict != "ok":
                self._cleanup()
                raise ApiError(422, "the player refused %s: %s. %s" % (
                    sid, message, "The effect before it in that slot stays on." if (source, slot) in self.on else "Nothing is on in that slot."))
            self.on[(source, slot)] = {"id": sid, "values": start, "controls": controls, "preset": name, "path": out}
            self._cleanup()
        self.api.log("pvj-web: effect %s on source %d, slot %d" % (sid, source, slot))
        return {"ok": True, "source": source, "slot": slot, "id": sid}

    # -- a generator as a source (D106 step d) --
    def put_generator(self, source, sid, preset=None, values=None, controls=None, expect=None):
        """The generator `sid` as the whole picture of source `source` (2 to N), in slot 0, in place of its clip: the
        source's clip stops, the shader draws at the height Picture detail gives it (D93), with the preset named, else
        the one called default, else the file's own values. Its effects (slots 1 to 3) stay on over it. The clip stops
        only once the renderer has taken the shader (or not said in time, a slow build): a refusal leaves the clip
        playing, never a black source (the review of #171).

        `expect` (a rebuild, rebuild_generators): the program file the source held when the rebuild was asked. If
        anything else came to the source since (a Stop, a clip, another shader), the rebuild leaves it be and None is
        returned; and a value the file no longer takes (an input that is gone or changed) is dropped, not refused."""
        eng = self.api.shaders
        eng._need()
        source, _ = self._place({"source": source, "slot": 0}, generator=True)
        if not isinstance(sid, str):
            raise ApiError(400, "shader must be a shader's name")
        path, _ = eng._path(sid)
        try:
            parsed, _digest = eng._parsed(path)
            start, stored, name = eng.start_values(parsed, eng.config(), sid, preset)
            if values is not None and expect is not None:
                kept = {}
                for n, v in values.items():
                    try:
                        kept.update(S.clean_values(parsed, {n: v}))
                    except ShaderError:
                        pass                    # the new file has no such input, or not of that kind: it starts as the file says
                start = dict(start, **kept)
                name = None if values else name
            elif values is not None:
                start = dict(start, **S.clean_values(parsed, values))
                name = None if values else name
            events = {i["name"] for i in parsed["inputs"] if i["type"] == "event"}
            start = {n: v for n, v in start.items() if n not in events}
            ctl = eng.limit(parsed, S.clean_controls(controls, S.clean_controls(stored)))
            size = S.render_size(eng.screen(), eng.height_for(sid))
            text = S.translate_native(parsed, size, start, ctl["hue"], 0.0, speed=ctl["speed"], gain=ctl["brightness"])
        except ShaderError as e:
            raise ApiError(422, "%s: %s" % (sid, e))
        with self._lock:
            if expect is not None and self._seen().get((source, 0), {}).get("path") != expect:
                return None                     # something else came to the source since the rebuild was asked: it stands
            out = self._write(text)
            try:
                verdict, message = self.api.player.chain_put(source, 0, out)
            except PlayerError as e:
                self._seen()
                self._cleanup()
                raise ApiError(503, str(e))
            if verdict not in ("ok", "unknown"):
                self._seen()
                self._cleanup()
                raise ApiError(422, "the player refused %s: %s. What source %d played stays on." % (sid, message, source))
            try:
                self.api.player.stop_source_clip(source)        # the clip under it goes: the shader is the picture
            except PlayerError as e:
                self.api.log("pvj-web: source %d: its clip did not stop under the shader: %s" % (source, e))
            self.on[(source, 0)] = {"id": sid, "values": start, "controls": ctl, "preset": name, "path": out, "generator": True}
            self._cleanup()
        self.api.log("pvj-web: shader %s on source %d" % (sid, source))
        return {"ok": True, "source": source, "slot": 0, "id": sid, "playing": sid}

    def generator_values(self, source, values=None, controls=None):
        """New values or controls (speed, hue, brightness) for the generator of source `source`, set while it plays."""
        eng = self.api.shaders
        eng._need()
        self._place({"source": source, "slot": 0}, generator=True)
        with self._lock:
            r = self._seen().get((source, 0))
            if r is None:
                raise ApiError(409, "no shader plays on source %d" % source)
            try:
                parsed, _digest = eng._parsed(eng._path(r["id"])[0])
                vals = dict(r["values"], **S.clean_values(parsed, values))
                ctl = eng.limit(parsed, S.clean_controls(controls, r["controls"]))
            except ShaderError as e:
                raise ApiError(400, str(e))
            send = S.native_send(parsed, vals, {})
            send.update({"@speed": ctl["speed"], "@hue": ctl["hue"], "@gain": ctl["brightness"]})
            try:
                done = self.api.player.chain_values(source, 0, send, r["path"])
            except PlayerError as e:
                raise ApiError(503, str(e))
            if not done:
                raise ApiError(409, "no shader plays on source %d" % source)
            r.update(values=vals, controls=ctl, preset=None)
            return {"ok": True, "source": source, "id": r["id"], "values": vals, "controls": ctl}

    def rebuild_generators(self, sid=None):
        """Build again the shaders that are the picture of Sources 2 to N (all of them, or those of `sid`), with the
        values and controls they have: after an upload that replaces the file, or a change of Picture detail (the
        review of #171; Source 1's shader is the engine's). One the renderer refuses now leaves the one before it on,
        and the log says so. A source that took something else while this ran (a Stop, a clip, another shader) is
        left as it is (the review of #172). Returns {"rebuilt": [sources], "not_rebuilt": [{"source", "id", "why"}]}."""
        out = {"rebuilt": [], "not_rebuilt": []}
        player = self.api.player
        if not getattr(player, "native_chains", lambda: False)():
            return out
        with self._lock:
            todo = [(s, dict(r)) for (s, k), r in sorted(self._seen().items()) if k == 0 and (sid is None or r["id"] == sid)]
        for s, r in todo:
            try:
                put = self.put_generator(s, r["id"], values=r["values"], controls=r["controls"], expect=r["path"])
            except ApiError as e:
                self.api.log("pvj-web: source %d: %s could not be built again: %s" % (s, r["id"], e.message))
                out["not_rebuilt"].append({"source": s, "id": r["id"], "why": e.message})
                continue
            if put is None:
                continue                        # the source took something else meanwhile
            with self._lock:
                if (s, 0) in self.on and r["preset"] is not None and self.on[(s, 0)]["values"] == r["values"]:
                    self.on[(s, 0)]["preset"] = r["preset"]          # the same preset, built again
            out["rebuilt"].append(s)
        return out

    def generator_of(self, source):
        """The id of the shader that is source `source`'s picture, or None (as the player's record says it now)."""
        return self.generators().get(source)

    def generators(self):
        """{source: shader id} of every source whose picture is a shader, from one look at the player's record (the
        sources' rows ask once a request, not once a row: the review of #171)."""
        with self._lock:
            return {s: r["id"] for (s, k), r in self._seen().items() if k == 0}

    def values(self, body):
        """{"source", "slot", "values"?, "controls"?}: new values for the effect in that slot, set while it plays."""
        if body.get("slot") == 0:
            return self.generator_values(body.get("source"), body.get("values"), body.get("controls"))
        fx = self.api.effects
        fx._need()
        source, slot = self._place(body)
        with self._lock:
            r = self._seen().get((source, slot))
            if r is None:
                raise ApiError(409, "no effect is on in slot %d of source %d" % (slot, source))
            try:
                parsed, _digest = fx._parsed(fx._path(r["id"])[0])
                values = dict(r["values"], **S.clean_values(parsed, body.get("values")))
                controls = fx.limit(parsed, L.clean_fx_controls(body.get("controls"), r["controls"]))
            except ShaderError as e:
                raise ApiError(400, str(e))
            send = S.native_send(parsed, values, {})
            send.update({"@amount": controls["amount"], "@speed": controls["speed"]})
            try:
                done = self.api.player.chain_values(source, slot, send, r["path"])
            except PlayerError as e:
                raise ApiError(503, str(e))
            if not done:
                self._seen()
                raise ApiError(409, "no effect is on in slot %d of source %d" % (slot, source))
            r.update(values=values, controls=controls, preset=None)
            return {"ok": True, "source": source, "slot": slot, "id": r["id"], "values": values, "controls": controls}

    def off(self, body):
        """{"source", "slot"?, "off": true}: take that slot's effect off (every slot of the source without one)."""
        player = self.api.player
        source = body.get("source")
        slot = body.get("slot")
        if slot is None:
            self._place(dict(body, slot=player.chain_slots(source)[0] if isinstance(source, int) and not isinstance(source, bool) else None))
        else:
            self._place(body, generator=True)
        with self._lock:
            try:
                had = player.chain_clear(source, slot)
            except PlayerError as e:
                raise ApiError(503, str(e))
            self._seen()
            self._cleanup()
        return {"ok": True, "source": source, "slot": slot, "was_on": had}

    def api_get(self, body, device, client):
        return self.state()

    def api_post(self, body, device, client):
        if not isinstance(body, dict):
            raise ApiError(400, "send an object")
        if body.get("off") is True:
            return self.off(body)
        if "shader" in body:
            return self.put_generator(body.get("source"), body["shader"], body.get("preset"), body.get("values"), body.get("controls"))
        if "id" in body:
            return self.put(body)
        if "values" in body or "controls" in body:
            return self.values(body)
        raise ApiError(400, "send an effect's id, values or controls, or off: true")
