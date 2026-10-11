# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Who may do what (D80): Owner (`full`), Operator (`live`), Guest (`view`).

The route table in api.py holds each route's minimum role. This module holds the three things that are NOT a rank:

* LEGACY_LIVE: what a presenter could reach before the Operator was raised. The box's own callers (MIDI, OSC, DMX, a
  Room scene, the schedule) and a remote support session below `full` are held to it, so they gain nothing when a
  route's minimum is lowered. It is written out by hand on purpose: never compute it from the route table.
* GUEST: the forms of a request a Guest may send while guest controls are open. Each was chosen so that it is
  reversible, shows in the room, and saves nothing.
* GuestControls: the lock, the limits, the two-step power-off and the log line.

A new route is in none of these lists, so it is for paired devices of its minimum role only until someone decides
otherwise; tests/test_roles.py fails for a route without a row.
"""

import json
import secrets
import threading
import time

NAMES = {"view": "Guest", "live": "Operator", "full": "Owner"}

# The device ids the box's own callers act as (midi.MIDI_DEVICE and so on). A paired device's id is 8 hex digits and
# a support login's is "support-N", so neither can be one of these.
CONTROLLERS = ("midi", "osc", "dmx", "room")

# Paths the server answers itself, outside Api.routes(): path -> minimum role.
OUTSIDE = {
    ("GET", "/api/preview.jpg"): "view",
    ("GET", "/api/qr.svg"): "live",
    ("POST", "/api/media/upload"): "live",
    ("POST", "/api/system/update/upload"): "full",
    ("POST", "/api/system/settings/import"): "full",
    # the secure connection's own routes (D79, server._https_route): the Owner's, every one that asks who is there
    ("GET", "/api/https"): "full",
    ("GET", "/api/https/request.csr"): "full",
    ("GET", "/api/https/root.crt"): "full",
    ("POST", "/api/https/request"): "full",
    ("POST", "/api/https/certificate"): "full",
    ("POST", "/api/https/undo"): "full",
    ("POST", "/api/https/remove"): "full",
    ("POST", "/api/https/root"): "full",
    ("POST", "/api/https/owner-only"): "full",
}

# Answered by the server before anyone is asked who they are (D79): it says only whether this request came over the
# secure connection, so a page over plain http can find out if its device trusts the box. Nothing else belongs here.
NO_GATE = frozenset([("GET", "/api/https/probe")])

# Every route a presenter (or less) could use on master before D80, 2026-10-10. Frozen: do not add to it.
LEGACY_LIVE = frozenset(
    [("GET", p) for p in (
        "/api/hello", "/api/status", "/api/media", "/api/pads", "/api/modules", "/api/theme", "/api/osc",
        "/api/media/import", "/api/system", "/api/access", "/api/inputs", "/api/overlay", "/api/health",
        "/api/support", "/api/sync", "/api/mapper", "/api/shaders", "/api/effects", "/api/projectors", "/api/room",
        "/api/audio", "/api/autostart", "/api/midi", "/api/streams", "/api/schedule", "/api/preview.jpg",
        "/api/qr.svg")]
    + [("POST", p) for p in (
        "/api/pair", "/api/session", "/api/logout", "/api/support/login", "/api/play", "/api/control",
        "/api/blackout", "/api/fadeout", "/api/fadein", "/api/testpattern", "/api/testtone", "/api/media/info",
        "/api/mix", "/api/access/code", "/api/access/cancel", "/api/access/screen", "/api/overlay",
        "/api/support/stop", "/api/shaders/play", "/api/shaders/values", "/api/shaders/step", "/api/shaders/preset",
        "/api/vibes", "/api/effects", "/api/effects/values", "/api/effects/step", "/api/effects/preset",
        "/api/projector", "/api/room/scene", "/api/room/group", "/api/autostart/test")])

GUEST_ACTIONS, GUEST_WINDOW = 10, 10.0          # one guest device: this many actions in this many seconds
BOX_ACTIONS, BOX_WINDOW = 30, 10.0              # all guests together
OFF_DEVICE, OFF_BOX, OFF_WINDOW = 1, 2, 300.0   # power-offs carried out: per guest device, for the box, in seconds
COOL_SECONDS = 2.0                              # play, blackout and a scene: one for all guests together in this long
CONFIRM_SECONDS = 30                            # a power-off's second request must come within this
MAX_CONFIRMS = 64                               # waiting confirms kept in memory, oldest dropped
LOCKED_TEXT = "The room is locked for a show: you can watch"
OFF = "off"                                     # what a form answers for a power-off: allowed, with the confirm


class Refused(Exception):
    """What the gate turns into an API error: status, message, and for a confirm or a limit the extra fields."""
    def __init__(self, status, message, retry_after=None, extra=None):
        super().__init__(message)
        self.status, self.message, self.retry_after, self.extra = status, message, retry_after, extra or {}


def _keys(body, *allowed):
    return all(k in allowed for k in body)


def _play(body, api):
    if not _keys(body, "pad", "file", "loop") or ("pad" in body) == ("file" in body):
        return False
    if "file" in body and not isinstance(body["file"], str):
        return False
    return "loop" not in body or isinstance(body["loop"], bool)


def _control(body, api):
    return set(body) == {"action"} and body["action"] in ("next", "prev", "stop")


def _blackout(body, api):
    return set(body) == {"on"} and isinstance(body["on"], bool)


def _vibes(body, api):
    if body == {"on": False} or body == {"next": True} or body == {"previous": True}:
        return all(isinstance(v, bool) for v in body.values())      # 1 == True in Python: only real booleans
    return (_keys(body, "on", "set") and body.get("on") is True
            and ("set" not in body or isinstance(body["set"], str)))


def _shader(body, api):
    return set(body) == {"id"} and isinstance(body["id"], str)


def _step(body, api):
    return _keys(body, "dir")


def _preset(body, api):
    return _keys(body, "id", "name", "index")


def _effect(body, api):
    if set(body) == {"off"}:
        return body["off"] is True
    return (_keys(body, "id", "preset") and isinstance(body.get("id"), str)
            and ("preset" not in body or isinstance(body["preset"], str)))


def _power(body, api, *names):
    if not _keys(body, "action", "input", *names) or body.get("action") not in ("on", "off", "input"):
        return False
    return OFF if body["action"] == "off" else True


def _projector(body, api):
    return _power(body, api, "id")


def _group(body, api):
    return _power(body, api, "group", "number", "name")


def _scene(body, api):
    """Every scene but one that plays a stream; one that switches a group off is a power-off. The scene is looked up
    as the handler will look it up, so a wrong id answers as it does for anyone."""
    if not _keys(body, "scene", "number", "name"):
        return False
    room = api.room
    room._need()
    scene = room._pick(room.config()["scenes"], body, "scene", "scene")
    if scene.get("no_guests") is True:
        raise Refused(403, "this scene is not for guests; ask an operator to start it")
    if (scene.get("box") or {}).get("action") == "stream":
        raise Refused(403, "this scene plays a stream; ask an operator to start it")
    return OFF if any(r.get("power") == "off" for r in scene.get("groups", [])) else True


# (method, path) -> form(body, api): False (not for a guest), True, or OFF (a power-off: needs the confirm).
GUEST = {
    ("POST", "/api/play"): _play,
    ("POST", "/api/control"): _control,
    ("POST", "/api/blackout"): _blackout,
    ("POST", "/api/vibes"): _vibes,
    ("POST", "/api/shaders/play"): _shader,
    ("POST", "/api/shaders/step"): _step,
    ("POST", "/api/shaders/preset"): _preset,
    ("POST", "/api/effects"): _effect,
    ("POST", "/api/effects/step"): _step,
    ("POST", "/api/effects/preset"): _preset,
    ("POST", "/api/projector"): _projector,
    ("POST", "/api/room/group"): _group,
    ("POST", "/api/room/scene"): _scene,
}


# What the room sees change at once and what a guest could flash it with: one of these for all guests together in
# COOL_SECONDS (two seconds: slower than any flicker that harms, and still one tap after another for a person). The
# Operator and the Owner never pass through here.
COOLED = frozenset([("POST", "/api/play"), ("POST", "/api/blackout"), ("POST", "/api/room/scene")])


def is_controller(device):
    return bool(device) and device.get("id") in CONTROLLERS


def is_guest(device):
    """A paired device of the lowest role. Not a support session and not one of the box's own callers."""
    return bool(device) and device.get("role") == "view" and not device.get("remote") and not is_controller(device)


def held_to_legacy(device):
    """The callers that keep the presenter's old reach whatever the route table says: the box's own callers and a
    support session below full. (A presenter paired from a controller's code before D80 is a Guest: Auth.role_of.)"""
    if not device:
        return False
    return bool(is_controller(device) or (device.get("remote") and device.get("role") != "full"))


def is_operator(device):
    """An Operator or an Owner with the Operator's new reach: for what a handler shows or does beyond the gate."""
    return bool(device) and device.get("role") in ("live", "full") and not held_to_legacy(device)


def reach(device, locked):
    """One word for the panel, so it never works this out by itself: owner, operator, presenter (a device or session
    held to LEGACY_LIVE), guest (guest controls open) or watch."""
    if not device:
        return "watch"
    if is_controller(device):
        return "presenter"
    if device.get("role") == "full":
        return "owner"
    if is_operator(device):
        return "operator"
    if device.get("role") == "live":
        return "presenter"
    return "guest" if is_guest(device) and not locked else "watch"


class GuestControls:
    def __init__(self, api, clock=time.monotonic):
        self.api = api
        self._clock = clock
        self._lock = threading.Lock()
        self._acts = {}             # device id -> [times], and "*" for the box
        self._offs = {}             # the same for power-offs carried out
        self._confirms = {}         # token -> (device id, request key, expires)
        self._cooled = None         # when a guest last played, blacked out or applied a scene (COOLED)

    # -- the lock --
    def locked(self):
        """Open unless the settings say locked. A damaged value counts as locked."""
        value = self.api.settings.data.get("guest_controls")
        if value is None:
            return False
        return not (isinstance(value, dict) and value.get("locked") is False)

    def state(self):
        return {"locked": self.locked()}

    def set_locked(self, locked):
        settings = self.api.settings
        # self._lock first, then the settings': admit() looks at the lock under self._lock, so a guest's request is
        # either refused as locked or its confirm token is made before the clear below and goes with it.
        with self._lock, settings.lock:
            old = settings.data.get("guest_controls")
            settings.data["guest_controls"] = {"locked": bool(locked)}
            try:
                settings.save()
            except OSError:
                if old is None:
                    settings.data.pop("guest_controls", None)
                else:
                    settings.data["guest_controls"] = old
                raise
            if locked:
                self._confirms.clear()          # a power-off that was waiting for its second request is over
        return self.state()

    # -- one guest request --
    @staticmethod
    def _prune(store, now, window):
        """Forget what is older than the window, and every device that has nothing left (so a device that went quiet
        or was removed leaves no entry behind)."""
        for key in list(store):
            times = [t for t in store[key] if now - t < window]
            if times:
                store[key] = times
            else:
                del store[key]

    def _room_in(self, store, who, now, window, per_device, per_box):
        """Seconds to wait, or 0. Counts nothing; _count does."""
        self._prune(store, now, window)
        wait = 0.0
        for key, most in ((who, per_device), ("*", per_box)):
            times = store.get(key, [])
            if len(times) >= most:
                wait = max(wait, window - (now - times[0]))
        return wait

    @staticmethod
    def _count(store, who, now):
        store.setdefault(who, []).append(now)
        store.setdefault("*", []).append(now)

    @staticmethod
    def _uncount(store, who, now):
        for key in (who, "*"):
            times = store.get(key, [])
            if now in times:
                times.remove(now)
            if not times:
                store.pop(key, None)

    def admit(self, method, path, body, device, client):
        """Let a guest's request through, or raise Refused. Returns (the body for the handler, without "confirm";
        finish). The caller runs the handler and then calls finish(True) if it answered, finish(False) if it raised:
        only what was really done is written in the journal, and a switch-off or a cooled action that did not happen
        is given back (it is held from here on, so two requests at the same moment cannot both pass).

        In order: the lock; the limit on actions, which counts every request that comes this far, a refused form too;
        the form; the cooldown; for a switch-off its own limit and the confirm."""
        if self.locked():
            raise Refused(403, LOCKED_TEXT)
        key = (method, path)
        form = GUEST.get(key)
        body = dict(body) if isinstance(body, dict) else {}
        given = body.pop("confirm", None)
        who, now = device["id"], self._clock()
        with self._lock:
            if self.locked():                   # again, under the lock set_locked holds: see there
                raise Refused(403, LOCKED_TEXT)
            wait = self._room_in(self._acts, who, now, GUEST_WINDOW, GUEST_ACTIONS, BOX_ACTIONS)
            if wait:
                raise Refused(429, "too many guest actions at once; wait a moment", retry_after=int(wait) + 1)
            self._count(self._acts, who, now)
        kind = form(body, self.api) if form else False      # may ask the room for a scene: not under the lock
        if kind is not True and kind != OFF:
            raise Refused(403, "a guest may not do that (operator access needed)")
        request = json.dumps([method, path, body], sort_keys=True)
        cooled, before = key in COOLED, None
        with self._lock:
            if self.locked():
                raise Refused(403, LOCKED_TEXT)
            if cooled and self._cooled is not None and 0 <= now - self._cooled < COOL_SECONDS:
                raise Refused(429, "a guest changed the picture a moment ago; wait a moment",
                              retry_after=int(COOL_SECONDS - (now - self._cooled)) + 1)
            if kind == OFF:
                wait = self._room_in(self._offs, who, now, OFF_WINDOW, OFF_DEVICE, OFF_BOX)
                if wait:
                    raise Refused(429, "the projectors were switched off by a guest a moment ago; ask an operator",
                                  retry_after=int(wait) + 1)
                self._confirms = {t: c for t, c in self._confirms.items() if c[2] > now}
                held = self._confirms.pop(given, None) if isinstance(given, str) else None
                if held is None or held[0] != who or held[1] != request:
                    while len(self._confirms) >= MAX_CONFIRMS:
                        del self._confirms[next(iter(self._confirms))]
                    token = secrets.token_urlsafe(12)
                    self._confirms[token] = (who, request, now + CONFIRM_SECONDS)
                    raise Refused(409, "switching projectors off needs a second tap to confirm",
                                  extra={"confirm": {"token": token, "seconds": CONFIRM_SECONDS}})
                self._count(self._offs, who, now)
            if cooled:
                before, self._cooled = self._cooled, now

        def finish(done):
            if done:
                self.api.log("pvj-web: guest %s (device %s, from %s): %s %s"
                             % (json.dumps(str(device.get("name", ""))[:40]), who, client, path, json.dumps(body, sort_keys=True)[:160]))
                return
            with self._lock:                    # it did not happen: the switch-off and the cooldown are given back
                if kind == OFF:
                    self._uncount(self._offs, who, now)
                if cooled and self._cooled == now:
                    self._cooled = before
        return body, finish
