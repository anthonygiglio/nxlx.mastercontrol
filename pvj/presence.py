# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Who is connected now, and what each last changed (D126). In memory only: nothing here is ever written to the disk.

A device is "connected now" when it made a request in the last 30 seconds. The panel asks for the status every second
while it is visible, so a device with the panel open is always in; a tab in the background drops out after 30 seconds
(browsers slow a hidden page's timers), and comes back with its next request. The window is a count of seconds since
the last request, not a promise that someone is looking.

What each viewer is told (`view`): the Owner sees every connected device by name and role, and a remote support session as
"Remote support"; an Operator sees the names and roles of the Owner's and the Operators' devices and only a count of the
Guests; a Guest sees only a count. The same rule decides who is named in the last ten changes. Never an address, a token or an
id: a device is told only whether an entry is itself.

A change is a request that changed something, by the area of the control it belongs to (Mix, Pads, Projectors ...); a
path that is not a control (sign-in, devices, codes, access, support) is never a change here."""

import collections
import threading
import time

WINDOW = 30             # seconds: a request this recently is "connected now"
FORGET = 600            # seconds: a device not heard from for this long is dropped from memory
CHANGES = 10            # how many of the last changes are kept and told
NAMES = {"view": "Guest", "live": "Operator", "full": "Owner"}
REMOTE = "Remote support"

# The control areas, by the second part of the path. Anything not here is not a change (sign-in, devices, codes, access,
# the support tunnel, HTTPS, recovery and the like are not the controls and are not told to anyone).
AREAS = {
    "play": "Playback", "control": "Playback", "blackout": "Playback", "fade": "Playback", "fadein": "Playback",
    "fadeout": "Playback", "testpattern": "Playback", "testtone": "Playback", "player": "Playback",
    "mix": "Mix", "overlay": "Mix", "pads": "Pads", "vibes": "Vibes", "shaders": "Shaders", "effects": "Effects",
    "mapper": "Mapping", "projector": "Projectors", "projectors": "Projectors", "room": "Room", "sound": "Sound",
    "audio": "Sound", "tempo": "Tempo", "sync": "Tempo", "renderer": "Renderer", "render": "Renderer", "screen": "Screen",
    "network": "Network", "schedule": "Schedule", "autostart": "Schedule", "midi": "Controllers", "osc": "Controllers",
    "dmx": "Controllers", "ndi": "Sources", "streams": "Sources", "sources": "Sources", "media": "Media", "theme": "Look",
    "modules": "Modules", "system": "System",
}
NOT_CHANGES = {("POST", "/api/media/info"), ("POST", "/api/system/settings/export"), ("POST", "/api/theme/export")}


def area_of(method, path):
    """The control area a request changes, or None (a read, or not a control)."""
    if method not in ("POST", "PUT", "DELETE", "PATCH") or (method, path) in NOT_CHANGES:
        return None
    parts = path.split("/")
    return AREAS.get(parts[2]) if len(parts) > 2 and parts[1] == "api" else None


class Presence:
    def __init__(self, clock=time.monotonic, controllers=()):
        self._clock = clock
        self._controllers = tuple(controllers)      # the node's own callers (midi, osc, dmx, room): never a person
        self.lock = threading.Lock()
        self._devices = {}                          # id -> {"name", "role", "remote", "t"}
        self._changes = collections.deque(maxlen=CHANGES)

    def _who(self, device):
        """(id, name, role, remote) of a paired device or a support login, or None for anything else."""
        if not isinstance(device, dict):
            return None
        ident, name, role = device.get("id"), device.get("name"), device.get("role")
        if not isinstance(ident, str) or ident in self._controllers or role not in NAMES or not isinstance(name, str):
            return None
        return ident, name[:40], role, bool(device.get("remote"))

    def seen(self, device):
        """A request from `device` just came in."""
        who = self._who(device)
        if who is None:
            return
        now = self._clock()
        with self.lock:
            self._devices[who[0]] = {"name": who[1], "role": who[2], "remote": who[3], "t": now}
            if len(self._devices) > 64:             # a flood of one-off devices: forget the oldest
                for ident in [i for i, v in self._devices.items() if now - v["t"] > FORGET]:
                    del self._devices[ident]

    def changed(self, device, method, path):
        """A request of `device` went well: if it changed a control, remember which area and when."""
        who = self._who(device)
        area = area_of(method, path)
        if who is None or area is None:
            return
        with self.lock:
            self._changes.append({"id": who[0], "name": who[1], "role": who[2], "remote": who[3], "area": area, "t": self._clock()})

    def forget(self, ident):
        """A device was removed: it is not connected."""
        with self.lock:
            self._devices.pop(ident, None)

    # ---- what a viewer is told -------------------------------------------------------------------------------------
    @staticmethod
    def _tier(viewer):
        """"owner" (the Owner, in the room), "operator" (an Operator, and a support login: it is the Owner's role without
        being the Owner), "guest"."""
        if not isinstance(viewer, dict):
            return "guest"
        if viewer.get("remote"):
            return "operator"
        return {"full": "owner", "live": "operator"}.get(viewer.get("role"), "guest")

    def view(self, viewer):
        now = self._clock()
        tier = self._tier(viewer)
        me = viewer.get("id") if isinstance(viewer, dict) else None
        with self.lock:
            devices = [dict(v, id=i) for i, v in self._devices.items() if now - v["t"] <= WINDOW]
            changes = list(self._changes)
        # a support login is told to the Owner, and to itself; to nobody else
        shown = [d for d in devices if not d["remote"] or tier == "owner" or d["id"] == me]
        named = lambda d: d["id"] == me or tier == "owner" or (tier == "operator" and d["role"] in ("full", "live") and not d["remote"])
        rows = []
        for d in sorted(shown, key=lambda d: (-d["t"], d["name"])):
            if not named(d):
                continue
            rows.append({"name": REMOTE if d["remote"] else d["name"], "role": REMOTE if d["remote"] else NAMES[d["role"]],
                         "you": d["id"] == me, "seconds_ago": int(max(0, now - d["t"]))})
        guests = sum(1 for d in shown if d["role"] == "view" and not d["remote"])
        out = {"window_seconds": WINDOW, "count": len(shown), "devices": rows, "guests": guests, "changes": []}
        if tier == "guest":
            out["devices"] = []                                 # a Guest is told the count and nothing else
        for c in reversed(changes):
            if c["remote"] and not (tier == "owner" or c["id"] == me):
                continue
            by = role = None
            if tier != "guest" and (c["remote"] or named(c)):
                by = REMOTE if c["remote"] else c["name"]
                role = REMOTE if c["remote"] else NAMES[c["role"]]
            elif tier == "operator":
                role = NAMES[c["role"]]                         # an Operator is told "a Guest", not which one
            out["changes"].append({"area": c["area"], "by": by, "role": role, "you": c["id"] == me,
                                   "seconds_ago": int(max(0, now - c["t"]))})
        return out
