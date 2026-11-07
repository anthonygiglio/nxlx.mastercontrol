# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Who is connected now, and what each last changed (D126). In memory only: nothing here is ever written to the disk.

A device is "connected now" when it made a request in the last 30 seconds. The panel asks for the status every second
while it is visible, so a device with the panel open is always in; a tab in the background drops out after 30 seconds
(browsers slow a hidden page's timers), and comes back with its next request. The window is a count of seconds since
the last request, not a promise that someone is looking.

What each viewer is told (`view`): the Owner sees every connected device by name and role, and a remote support session as
"Remote support"; an Operator sees the Operators' devices by name and role, the Owner's by role only, and a count of the
Guests; a support login sees roles and counts, no names; a Guest sees only a count. The same rule decides who is named in the last ten changes. Never an address, a token or an
id: a device is told only whether an entry is itself. As GET /api/devices hides an Owner's device from an Operator, an Operator is
told an Owner's device only by its role, and a support login (D125 keeps names from support) is told no device name at all.

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
NOT_CHANGES = {("POST", "/api/media/info"), ("POST", "/api/system/settings/export"), ("POST", "/api/theme/export"),
               ("POST", "/api/network/scan"), ("POST", "/api/midi/learn")}      # asking, showing or waiting: nothing is changed


def area_of(method, path, body=None):
    """The control area a request changes, or None (a read, or not a control). `body` matters for one path: POST /api/osc/key
    with nothing in it shows the key; only {"new": true} changes it."""
    if method not in ("POST", "PUT", "DELETE", "PATCH") or (method, path) in NOT_CHANGES:
        return None
    if path == "/api/osc/key" and not (isinstance(body, dict) and body.get("new") is True):
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

    def changed(self, device, method, path, body=None):
        """A request of `device` went well: if it changed a control, remember which area and when."""
        who = self._who(device)
        area = area_of(method, path, body)
        if who is None or area is None:
            return
        with self.lock:
            self._changes.append({"id": who[0], "name": who[1], "role": who[2], "remote": who[3], "area": area, "t": self._clock()})

    def forget(self, ident=None):
        """A device was removed (or, with none named, all of them: a factory reset): it is not connected, and what it
        changed is not told any more."""
        with self.lock:
            if ident is None:
                self._devices.clear()
                self._changes.clear()
                return
            self._devices.pop(ident, None)
            kept = [c for c in self._changes if c["id"] != ident]
            self._changes.clear()
            self._changes.extend(kept)

    # ---- what a viewer is told -------------------------------------------------------------------------------------
    @staticmethod
    def _tier(viewer):
        """"owner" (the Owner, in the room), "operator", "support" (a login through the tunnel: the Owner's role without
        being the Owner, and told no device name), "guest"."""
        if not isinstance(viewer, dict):
            return "guest"
        if viewer.get("remote"):
            return "support"
        return {"full": "owner", "live": "operator"}.get(viewer.get("role"), "guest")

    def view(self, viewer):
        now = self._clock()
        tier = self._tier(viewer)
        me = viewer.get("id") if isinstance(viewer, dict) else None
        with self.lock:
            devices = [dict(v, id=i) for i, v in self._devices.items() if now - v["t"] <= WINDOW]
            changes = list(self._changes)
        # a support login is told to the Owner and to itself, to nobody else
        shown = [d for d in devices if not d["remote"] or tier == "owner" or d["id"] == me]

        def label(d):
            return REMOTE if d["remote"] else NAMES[d["role"]]

        def named(d):
            """May the viewer be told this device's name? The Owner: all. Everyone: his own. An Operator: the Operators'
            (never an Owner's, as GET /api/devices hides an Owner's device from him). A support login: none."""
            if tier == "owner" or d["id"] == me:
                return True
            return tier == "operator" and d["role"] == "live" and not d["remote"]

        def listed(d):
            """Is the device one row of the list for this viewer? Guests are counted, not listed (to everyone but the Owner)."""
            return tier == "owner" or d["id"] == me or (tier in ("operator", "support") and d["role"] in ("full", "live") and not d["remote"])

        rows = []
        for d in sorted(shown, key=lambda d: (-d["t"], d["name"])):
            if tier != "guest" and listed(d):
                rows.append({"name": (REMOTE if d["remote"] else d["name"]) if named(d) else None, "role": label(d),
                             "you": d["id"] == me, "seconds_ago": int(max(0, now - d["t"]))})
        out = {"window_seconds": WINDOW, "count": len(shown),
               "guests": 0 if tier == "guest" else sum(1 for d in shown if d["role"] == "view" and not d["remote"]),
               "devices": rows, "changes": []}
        for c in reversed(changes):
            if c["remote"] and not (tier == "owner" or c["id"] == me):
                continue
            by = role = None
            if tier != "guest":
                role = label(c)
                if named(c):
                    by = REMOTE if c["remote"] else c["name"]
            out["changes"].append({"area": c["area"], "by": by, "role": role, "you": c["id"] == me,
                                   "seconds_ago": int(max(0, now - c["t"]))})
        return out
