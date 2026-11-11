# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Strobe: a flash you hold down, that stops the moment you let go (project-log/decisions, "Strobe safety").

The look is the bundled filter fx-strobe.fs (an ISF filter, so it is in the Effects library, marked as flashing, with a
rate, a colour, the length of a flash and a way: to the colour, to black, or the colour added). This module is the hold.

THE FAST PATH. A hold must start at once, and the effect worker keeps a gap of 0.35 seconds between two switches (and a
text is compiled on classic mpv). So on the New renderer the strobe is kept LOADED in a chain slot of source 1 (slot 3,
else 2: the slots after the effect of the Effects page, which it leaves alone) at amount 0, where the renderer draws
nothing for it, and a hold sets the amount to 1: one value sent to the renderer, no file, no compile, no worker. Release
sets it back to 0. The slot is loaded when the panel asks (`arm`) or at the first hold, and taken off again after
PARK_SECONDS without use. On classic mpv, or when both chain slots hold other effects, there is no loaded copy to
gate: a hold puts the strobe on as THE effect (what was on is remembered) and a release takes it off and puts that
back. That is slower (a text is compiled) and is said so in the state (`"path": "effect"`).

THE DEAD-MAN. A hold is a lease, not a switch. Every holder has a deadline; the node's own watcher (TICK seconds) ends
the strobe when the last lease has run out, so a closed tab, a dropped network, a pulled cable or a silent controller
cannot leave it on. The panel renews every RENEW_EVERY seconds and a lease from the panel lasts PANEL_TTL, so the strobe
ends at most PANEL_TTL + TICK after the last renewal. OSC is the same (the owner's decision, 2026-11-11): the sender repeats
/pvj/strobe 1 about every RENEW_EVERY while held, and one press with no repeats lasts at most PANEL_TTL. A MIDI pad sends a
press and a release and nothing between, so the MIDI hub renews for a held control every RENEW_EVERY while the device is still
read; a MIDI controller that goes ends the strobe in about half a second, and CONTROLLER_TTL is only the backstop. No lease lasts longer than MAX_HOLD in all,
renewed or not: a finger left on a button, or a stuck tab, ends it, and the holder must let go and press again.

SEVERAL HOLDERS are counted, one lease for each (a device and the holder name it gives): the strobe is on while at least
one lease is, and a holder that vanishes loses only its own. Why not one holder: a second person's press would steal
the first one's, and the first one's release would stop the second. Why counted by lease and not by presses: a release
that never arrives cannot keep it on, since every lease runs out by itself.

THE CAP. 3 flashes a second: the filter caps its own rate at 3 in its code, in TIME; this module caps the rate it
sends at 3 and keeps the speed at 1; and the node's existing speed limit (effects.py, "Allow faster than the flash
limit") keeps the speed at 1 for a flashing filter unless the opt-in is on. With the opt-in on, a rate above 3 is the
filter's 3 times a speed above 1, to 12 a second. Besides the rate, two things could make flashes closer together than
the rate says, and both are held back by one rule: a lit stretch may start only GAP seconds after the last one ended.
(1) Changing the rate or the length of a flash while it flashes moves the filter's place in its cycle at once (it
computes the place from TIME times the rate), which can start a flash early: a change of either ends the lit stretch
and starts a new one GAP later. (2) Letting go and pressing again, as fast as a finger can, would start a flash at an
arbitrary place in the cycle each time. With the opt-in on the gap is 0, as the worker's is. The colour and the way
change at once; they do not move the timing.
"""

import re
import threading
import time

from . import policy
from .api import ApiError

from .effects import HOLD_ONLY

EFFECT = "fx-strobe.fs"
assert EFFECT in HOLD_ONLY
CAP = 3.0                       # flashes a second, without the opt-in; the filter caps its own rate here too
FAST_CAP = 12.0                 # with the opt-in: the filter's 3 at a speed of 4
RATE_MIN = 0.5
RATE_STEP = 0.05                # a rate is kept to this step, so a knob does not send a new timing for every step it makes
FLASH_MIN, FLASH_MAX, FLASH_DEFAULT = 10, 250, 60       # milliseconds a flash lasts
WAYS = ("colour", "black", "add")
SOURCE, SLOTS = 1, (3, 2)       # where the loaded copy sits on the New renderer
PANEL_TTL = 0.5                 # seconds a lease from the panel lasts without a renewal
RENEW_EVERY = 0.2               # what the panel is told to renew at (and the MIDI hub does)
CONTROLLER_TTL = 10.0           # a lease from MIDI (the hub renews it every RENEW_EVERY): a backstop only. OSC's is PANEL_TTL
MAX_HOLD = 60.0                 # the longest one holder may hold in all, renewed or not
MAX_HOLDERS = 8
TICK = 0.05                     # the watcher looks this often while anything is held or lit
GAP = 0.35                      # seconds between the end of one lit stretch and the start of the next (the effect worker's SWITCH_GAP)
PARK_SECONDS = 300.0            # the loaded copy is taken off this long after the last use
SPENT_SECONDS = 1.0             # a holder that was ended for going on too long must be silent for this long before it can press again
RETRY_SECONDS = 1.0             # after the box refused to start one, the watcher waits this long before it tries again
HOLDER = re.compile(r"[A-Za-z0-9 ._:/@-]{0,64}")
HEX = re.compile(r"#?([0-9A-Fa-f]{6})")


def clean_colour(v):
    """'#rrggbb' from '#rrggbb' or 'rrggbb' or three or four numbers 0 to 1 (red, green, blue, and alpha, which is ignored)."""
    if isinstance(v, str):
        m = HEX.fullmatch(v.strip())
        if m:
            return "#" + m.group(1).lower()
    elif isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        if all(0.0 <= x <= 1.0 for x in v[:3]):
            return "#" + "".join("%02x" % int(round(x * 255)) for x in v[:3])
    raise ApiError(400, "colour must be a hex colour like #ff0000, or red, green and blue as numbers from 0 to 1")


def colour_rgba(hexa):
    return [int(hexa[i:i + 2], 16) / 255.0 for i in (1, 3, 5)] + [1.0]


def _number(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ApiError(400, "%s must be a number" % what)
    return float(v)


class Strobe:
    def __init__(self, api, clock=time.monotonic, thread=True, log=None):
        self.api, self._clock, self._use_thread = api, clock, thread
        self.log = log or (lambda line: api.log(line))
        self._lock = threading.Lock()           # the fields below; never held across a call into the engines or the player
        self._apply = threading.Lock()          # one start or stop at a time (held across the calls)
        self._wake = threading.Condition(self._lock)
        self.cfg = {"rate": 2.0, "colour": "#ffffff", "flash": FLASH_DEFAULT, "way": "colour"}
        self.holders = {}                       # key -> {"since", "seen", "ttl", "who"}
        self._spent = {}                        # key -> when a holder past MAX_HOLD was last refused (it must let go for a second, or release)
        self.lit = False                        # the strobe is drawing now
        self.applied = None                     # the timing and the look it was started with
        self.route = None                       # "chain" (a loaded copy, gated by its amount) or "effect" (it is the effect)
        self.slot = None                        # the chain slot of the loaded copy
        self._off_at = -1e9                     # when a lit stretch last ended
        self._idle_since = None                 # when the loaded copy was last used (for PARK_SECONDS)
        self._prior = None                      # the effect that was on before a strobe that is the effect
        self._recent = None                     # and the one the one-button toggle would put back
        self._retry_at = 0.0
        self.error = None
        self._thread = None
        self._stopped = False
        self.started_ms = None                  # how long the last start took, for the state (a number to look at, not a promise)

    # ---- what the box allows now ---------------------------------------------------------------------------------
    def faster(self):
        try:
            return bool(self.api.effects.faster())
        except Exception:
            return False

    def top(self):
        return FAST_CAP if self.faster() else CAP

    def _gap(self):
        return 0.0 if self.faster() else GAP

    def _need(self):
        self.api.effects._need()                # the Shaders and Vibes module must be on (409 otherwise)
        self.api.effects._path(EFFECT)          # and the file must be there (404 otherwise); both are cheap: a hold asks every 200 ms

    def _plan(self):
        """What the strobe is to be like if started now: the timing (rate sent, speed sent, flash length) and the look."""
        rate = min(self.cfg["rate"], self.top())
        speed = max(1.0, rate / CAP) if rate > CAP else 1.0
        if not self.faster():
            rate, speed = min(rate, CAP), 1.0
        return {"rate": round(min(rate, CAP), 3), "speed": round(speed, 3), "flash": self.cfg["flash"],
                "tint": self.cfg["colour"], "way": WAYS.index(self.cfg["way"])}

    @staticmethod
    def _timing(plan):
        return (plan["rate"], plan["speed"], plan["flash"])

    @staticmethod
    def _values(plan):
        return {"rate": plan["rate"], "tint": colour_rgba(plan["tint"]), "flash": float(plan["flash"]), "way": plan["way"]}

    # ---- the settings --------------------------------------------------------------------------------------------
    def set(self, body):
        """{"rate"?, "colour"?, "flash"?, "way"?, "arm"?}: change the look. A rate or a flash length that changes while
        it flashes ends the lit stretch and starts a new one GAP later (see the top); a colour or a way changes at once."""
        if not isinstance(body, dict):
            raise ApiError(400, "send an object")
        bad = set(body) - {"rate", "colour", "color", "flash", "way", "arm"}
        if bad:
            raise ApiError(400, "unknown: %s" % ", ".join(sorted(bad)))
        new = {}
        if "rate" in body:
            new["rate"] = round(round(max(RATE_MIN, min(self.top(), _number(body["rate"], "rate"))) / RATE_STEP) * RATE_STEP, 3)
        for key in ("colour", "color"):
            if key in body:
                new["colour"] = clean_colour(body[key])
        if "flash" in body:
            new["flash"] = int(round(max(FLASH_MIN, min(FLASH_MAX, _number(body["flash"], "flash")))))
        if "way" in body:
            way = body["way"]
            if isinstance(way, int) and not isinstance(way, bool) and 0 <= way < len(WAYS):
                way = WAYS[way]
            if way not in WAYS:
                raise ApiError(400, "way must be one of %s" % ", ".join(WAYS))
            new["way"] = way
        if "arm" in body and not isinstance(body["arm"], bool):
            raise ApiError(400, "arm must be true or false")
        if new or "arm" in body:
            self._need()
        with self._lock:
            self.cfg.update(new)
        if body.get("arm") is True:
            self._arm()
        elif body.get("arm") is False:
            self._unload(force=True)
        self.settle()
        self._ensure_thread()
        return self.state()

    # ---- the holders ---------------------------------------------------------------------------------------------
    def _key(self, device, body):
        holder = body.get("holder", "")
        if not isinstance(holder, str) or not HOLDER.fullmatch(holder):
            raise ApiError(400, "holder must be up to 64 letters, digits and . _ : / @ - (or left out)")
        return "%s:%s" % ((device or {}).get("id") or "?", holder.strip()), (device or {}).get("name") or (device or {}).get("id") or "?"

    def _expire(self, now):
        """Under the lock: drop the leases that have run out or passed MAX_HOLD. Returns how many were dropped."""
        gone = [k for k, h in self.holders.items() if now > h["seen"] + h["ttl"] or now - h["since"] > MAX_HOLD]
        for k in gone:
            h = self.holders[k]
            if now - h["since"] > MAX_HOLD and now <= h["seen"] + h["ttl"]:
                self._spent[k] = now            # it ended because it went on too long, not because it fell silent
            del self.holders[k]
        for k in [k for k, t in self._spent.items() if now - t > SPENT_SECONDS]:
            del self._spent[k]
        return len(gone)

    def hold(self, device, body, renew=False):
        """Take or renew a lease. A press is a hold; a renewal of a lease that is gone is refused (409), so a late
        message from a stalled network cannot start the strobe again: the person lets go and presses again."""
        self._need()
        key, who = self._key(device, body)
        # OSC has no heartbeat of the node's own: the sender must repeat its 1, so its lease is the panel's. MIDI's is renewed by
        # the hub while the control is down and the device is read; its longer lease is only a backstop for a hub that stopped.
        ttl = CONTROLLER_TTL if policy.is_controller(device) and (device or {}).get("id") != "osc" else PANEL_TTL
        now = self._clock()
        refused = None
        with self._lock:
            self._expire(now)
            h = self.holders.get(key)
            if h is None:
                if key in self._spent:
                    self._spent[key] = now      # still being renewed: stays refused until it stops for a second
                    refused = ApiError(409, "held for the longest time (%d seconds); let go and press again" % MAX_HOLD)
                elif renew:
                    refused = ApiError(409, "that hold has ended; let go and press again")
                elif len(self.holders) >= MAX_HOLDERS:
                    refused = ApiError(429, "too many holds at once")
                else:
                    h = self.holders[key] = {"since": now, "ttl": ttl, "who": who}
            if h is not None:
                h["seen"] = now
                self._idle_since = None
                self._wake.notify_all()
        if refused is not None:
            self.settle()                       # whatever ran out while nobody looked ends now, before the refusal is told
            raise refused
        status = self.settle()
        with self._lock:
            held = len(self.holders)
            err = self.error
        if status == "error" and not renew:
            self.release(device, body)
            raise ApiError(409 if err and err.get("status") in (409, 422) else 503, (err or {}).get("message") or "the strobe could not start")
        self._ensure_thread()
        return self._answer(held)

    def release(self, device, body):
        """Let go. Acts on the caller's own thread, at once. {"all": true} lets go of every holder (a panic button)."""
        if not isinstance(body, dict):
            raise ApiError(400, "send an object")
        if body.get("all") is True:
            with self._lock:
                self.holders.clear()
                self._spent.clear()
        else:
            key, _ = self._key(device, body)
            with self._lock:
                self.holders.pop(key, None)
                self._spent.pop(key, None)
        self.settle()
        self._ensure_thread()
        with self._lock:
            held = len(self.holders)
        return self._answer(held)

    def drop(self, prefix):
        """Let go of every lease whose device is `prefix` ('midi', 'osc'): the controller went, or was switched off."""
        with self._lock:
            for k in [k for k in self.holders if k.startswith(prefix + ":")]:
                del self.holders[k]
                self._spent.pop(k, None)
        self.settle()

    def _answer(self, held):
        with self._lock:
            return {"ok": True, "held": held, "on": self.lit, "renew_every": RENEW_EVERY, "expires_in": PANEL_TTL,
                    "waiting": bool(self.holders) and not self.lit and self.error is None}

    # ---- turning it on and off -----------------------------------------------------------------------------------
    def settle(self):
        """Bring the screen to what the leases say: the one place that starts or stops it. Returns "lit", "off",
        "waiting" (a start held back by the gap) or "error" (the start failed; `self.error` says why)."""
        with self._apply:
            now = self._clock()
            with self._lock:
                self._expire(now)
                want = bool(self.holders)
                lit = self.lit
                retime = lit and self.applied is not None and self._timing(self._plan()) != self._timing(self.applied)
                wait = self._off_at + self._gap() - now
                retry = self._retry_at - now
            if lit and (not want or retime):
                self._stop(now)
                lit = False
                with self._lock:
                    wait = self._off_at + self._gap() - now
            if want and not lit:
                if wait > 0:
                    return "waiting"
                if retry > 0 and self.error is not None:
                    return "error"
                return self._start(now)
            if lit and want:
                self._follow_look()
            return "lit" if lit else "off"

    def _follow_look(self):
        """A colour or a way that changed while it flashes: sent at once (the timing is the same; see settle)."""
        plan = self._plan()
        if plan == self.applied:
            return
        try:
            self._send(plan, 1.0)
            with self._lock:
                self.applied = plan
        except ApiError as e:
            self._fail(e)

    def _fail(self, e):
        with self._lock:
            self.error = {"message": str(getattr(e, "message", e)), "status": getattr(e, "status", 503)}
            self._retry_at = self._clock() + RETRY_SECONDS
        self.log("pvj-web: strobe: %s" % self.error["message"])

    # -- the New renderer: the loaded copy in a chain slot --
    def _native(self):
        player = self.api.player
        try:
            return bool(getattr(player, "native_chains", lambda: False)())
        except Exception:
            return False

    def _chain_rows(self):
        return {(r["source"], r["slot"]): r["id"] for r in self.api.chains.state()["chains"]}

    def _pick_slot(self):
        """The chain slot of source 1 the strobe may use: one it already holds, else a free one; None if both hold others."""
        rows = self._chain_rows()
        for slot in SLOTS:
            if rows.get((SOURCE, slot)) == EFFECT:
                return slot
        for slot in SLOTS:
            if (SOURCE, slot) not in rows:
                return slot
        return None

    def _arm(self):
        """Load the strobe into its slot at amount 0 (nothing is drawn for it), so a hold is one value. True if it is loaded."""
        if not self._native():
            return False
        with self._apply:
            return self._load(self._plan())

    def _load(self, plan):
        slot = self._pick_slot()
        if slot is None:
            return False
        try:
            self.api.chains.put({"source": SOURCE, "slot": slot, "id": EFFECT, "values": self._values(plan),
                                 "controls": {"amount": 0.0, "speed": plan["speed"]}})
        except ApiError as e:
            self._fail(e)
            return False
        with self._lock:
            self.slot = slot
            self.applied = None
            self._idle_since = self._clock()
            self._wake.notify_all()
        return True

    def _loaded(self):
        """The slot the loaded copy is in now, or None (a Stop, a restart of the player or someone else's change takes it off)."""
        if not self._native():
            return None
        slot = self.slot
        if slot is None:
            return None
        try:
            return slot if self._chain_rows().get((SOURCE, slot)) == EFFECT else None
        except Exception:
            return None

    def _unload(self, force=False):
        with self._apply:
            with self._lock:
                if self.lit and not force:
                    return
                slot, self.slot = self.slot, None
            if slot is not None and self._native():
                try:
                    if self._chain_rows().get((SOURCE, slot)) == EFFECT:
                        self.api.chains.off({"source": SOURCE, "slot": slot})
                except ApiError as e:
                    self.log("pvj-web: strobe: could not take the loaded copy off: %s" % e.message)

    def _send(self, plan, amount):
        if self.route == "chain":
            self.api.chains.values({"source": SOURCE, "slot": self.slot, "values": self._values(plan),
                                    "controls": {"amount": amount, "speed": plan["speed"]}})
        else:
            self.api.effects.put(EFFECT, self._values(plan), {"amount": amount, "speed": plan["speed"]})

    def _start(self, now):
        began = time.monotonic()
        plan = self._plan()
        try:
            route, slot = "effect", None
            if self._native():
                slot = self._loaded()
                if slot is None and self._load(plan):
                    slot = self.slot
                if slot is not None:
                    route = "chain"
            if route == "effect":
                prior = self.api.effects.current()
                with self._lock:
                    self._recent = self.api.effects.recent
                    self._prior = None if (prior is None or prior["id"] == EFFECT) else {
                        "id": prior["id"], "values": dict(prior["values"]), "controls": dict(prior["controls"])}
            with self._lock:
                self.route = route
                if slot is not None:
                    self.slot = slot
                if not self.holders:            # let go while it was loading: it is not lit for nobody
                    self._idle_since = self._clock()
                    return "off"
            self._send(plan, 1.0)
        except ApiError as e:
            self._fail(e)
            return "error"
        with self._lock:
            self.lit, self.applied, self.error = True, plan, None
            self.started_ms = round((time.monotonic() - began) * 1000.0, 1)
            self._idle_since = None
        return "lit"

    def _stop(self, now):
        """End the lit stretch. This must not fail quietly: if the gentle way fails, the loaded copy or the effect is taken off."""
        done = False
        try:
            if self.route == "chain":
                self.api.chains.values({"source": SOURCE, "slot": self.slot, "controls": {"amount": 0.0}})
                done = True
            else:
                self._stop_effect()
                done = True
        except ApiError as e:
            self.log("pvj-web: strobe: stopping gently failed (%s); taking it off" % e.message)
        except Exception as e:
            self.log("pvj-web: strobe: stopping failed (%s); taking it off" % e)
        if not done:
            try:
                if self.route == "chain":
                    self.api.chains.off({"source": SOURCE, "slot": self.slot})
                    with self._lock:
                        self.slot = None
                else:
                    self.api.effects.off()
                done = True
            except Exception as e:
                self.log("pvj-web: strobe: could not stop it (%s); will try again" % e)
        with self._lock:
            if done:
                self.lit, self._off_at, self._idle_since = False, now, self._clock()
                self.applied = None

    def _stop_effect(self):
        """The strobe was the effect: take it off, and put back the one that was on before it, if nothing else is on now."""
        fx = self.api.effects
        on = fx.current()
        if on is not None and on["id"] == EFFECT:
            fx.off()
        with self._lock:
            prior, self._prior = self._prior, None
            recent = self._recent
        if prior is not None and fx.current() is None:
            try:
                fx.put(prior["id"], prior["values"], prior["controls"])
            except ApiError as e:
                self.log("pvj-web: strobe: the effect that was on before could not be put back: %s" % e.message)
        if fx.recent == EFFECT:
            fx.recent = recent                  # the one button must not put the strobe back on as an ordinary effect

    # ---- the watcher ---------------------------------------------------------------------------------------------
    def _ensure_thread(self):
        with self._lock:
            if self._use_thread and self._thread is None and not self._stopped:
                self._thread = threading.Thread(target=self._loop, name="strobe-watch", daemon=True)
                self._thread.start()

    def tick(self):
        """One look of the watcher (tests call it with a fake clock): end what has run out, start what the gap held back,
        take the loaded copy off after PARK_SECONDS."""
        self.settle()
        now = self._clock()
        with self._lock:
            park = (not self.holders and not self.lit and self.slot is not None and self._idle_since is not None
                    and now - self._idle_since >= PARK_SECONDS)
        if park:
            self._unload()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:                  # nothing may end the watcher while a hold could be running
                self.log("pvj-web: strobe watcher: %r" % (e,))
            with self._lock:
                busy = bool(self.holders) or self.lit
                parked = self.slot is not None and self._idle_since is not None
                if self._stopped or not (busy or parked):
                    self._thread = None
                    return
                self._wake.wait(TICK if busy else 5.0)

    def stop(self, timeout=3.0):
        """The node is going down (or a test is over): let go of everything and end the watcher."""
        with self._lock:
            self._stopped = True
            self.holders.clear()
            worker = self._thread
            self._wake.notify_all()
        try:
            self.settle()
        except Exception:
            pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    # ---- the API -------------------------------------------------------------------------------------------------
    def state(self):
        try:
            self._need()
            ok, why = True, None
        except ApiError as e:
            ok, why = False, e.message
        native, top, faster, gap = self._native(), self.top(), self.faster(), self._gap()
        now = self._clock()
        with self._lock:
            self._expire(now)
            return {"available": ok, "why": why, "effect": EFFECT, "settings": dict(self.cfg), "held": len(self.holders), "on": self.lit,
                    "armed": self.slot is not None, "path": "chain" if self.slot is not None else (self.route if self.lit else ("chain" if native else "effect")),
                    "error": self.error["message"] if self.error else None,
                    "limits": {"rate": {"min": RATE_MIN, "max": top, "step": RATE_STEP, "cap": CAP}, "flash": {"min": FLASH_MIN, "max": FLASH_MAX},
                               "ways": list(WAYS), "faster": faster, "gap": gap},
                    "hold": {"renew_every": RENEW_EVERY, "expires_after": PANEL_TTL, "controller_expires_after": CONTROLLER_TTL, "longest": MAX_HOLD,
                             "holders": MAX_HOLDERS},
                    "started_ms": self.started_ms}

    def api_get(self, body, device, client):
        return self.state()

    def api_set(self, body, device, client):
        return self.set(body)

    def api_hold(self, body, device, client):
        return self.hold(device, body if isinstance(body, dict) else {})

    def api_renew(self, body, device, client):
        return self.hold(device, body if isinstance(body, dict) else {}, renew=True)

    def api_release(self, body, device, client):
        return self.release(device, body)
