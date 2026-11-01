# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Ableton Link, the panel's side (D117, pvj/SOUND.md).

The helper `pvj-link` (render/link/, GPL, its own unit and account) joins a Link session and only follows: it reads the
tempo and the place in a four-beat bar and writes them into one small file, `/run/pvj-link/state`. This module holds the
Owner's switch (the setting `link`, the box's own), tells the helper on or off over its socket, again after a restart of
either, and hands what the file says to the box clock (pvj/clock.py). Off is the default.

The panel only reads the file and talks over the socket, as it does with pvj-render; nothing of Link is in this process.

What reaches the clock, and when: only a whole record, written by a helper that is on, with at least one peer, and no
older than STALE_SECONDS. A session of nobody says nothing, so Link's own default tempo never reaches the clock, and a
helper that stopped says nothing either: the clock's own rules (D116) then hand over as for any quiet source.

The helper missing (its packages were not there at install, or the service is down) is an ordinary state: the switch
keeps its value, the status says unavailable, and everything else on the box runs as before.
"""

import os
import stat
import struct
import threading
import time
import zlib

from . import paths

# --- the record (render/link/pvj-link.cpp has the table) ---------------------------------------------------------------
MAGIC = b"NXLK"
VERSION = 1
SIZE = 56
F_ON, F_ENABLED = 1, 2
_HEAD = struct.Struct("<4sHHIIdddII")       # bytes 0 to 47
_TAIL = struct.Struct("<II")                # the CRC-32 and the sequence number again
assert _HEAD.size == 48 and _HEAD.size + _TAIL.size == SIZE
STALE_SECONDS = 0.5                         # older than this: the helper is not delivering, as for the levels file
LOOK_EVERY = 0.05                           # the file is read at most this often: the helper writes no more than that
DEFAULT = {"on": False}


class LinkError(ValueError):
    pass


def form(section):
    """The `link` section, checked. Raises LinkError."""
    if not isinstance(section, dict) or set(section) - set(DEFAULT):
        raise LinkError("the link section holds on")
    out = dict(DEFAULT)
    out.update(section)
    if not isinstance(out["on"], bool):
        raise LinkError("on must be true or false")
    return out


def stored(section):
    """The setting as the box reads it. Fails closed: anything damaged is the default, which is off."""
    try:
        return form(section) if section is not None else dict(DEFAULT)
    except LinkError:
        return dict(DEFAULT)


def pack(flags, at, bpm, phase, peers, seq=1):
    """The 56 bytes of the file: what the helper writes. For the tests' fake helper."""
    head = _HEAD.pack(MAGIC, VERSION, SIZE, seq & 0xFFFFFFFF, flags, float(at), float(bpm), float(phase), peers, 0)
    return head + _TAIL.pack(zlib.crc32(head) & 0xFFFFFFFF, seq & 0xFFFFFFFF)


def unpack(data):
    """The values in 56 bytes of the file, or None if they are not a whole record of this version."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != SIZE:
        return None
    f = _HEAD.unpack_from(data)
    crc, seq2 = _TAIL.unpack_from(data, _HEAD.size)
    if f[0] != MAGIC or f[1] != VERSION or f[2] != SIZE or f[3] != seq2 or zlib.crc32(bytes(data[:_HEAD.size])) & 0xFFFFFFFF != crc:
        return None
    return {"seq": f[3], "on": bool(f[4] & F_ON), "enabled": bool(f[4] & F_ENABLED), "time": f[5], "bpm": f[6], "phase": f[7], "peers": f[8]}


def read_state(path, tries=2):
    """The last whole record in the file, or None. Opened without following a link and without waiting, and only a
    regular file of exactly SIZE bytes is read. A record caught half written is read again once."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != SIZE:
            return None
        for _ in range(tries):
            got = unpack(os.pread(fd, SIZE, 0))
            if got is not None:
                return got
        return None
    except OSError:
        return None
    finally:
        os.close(fd)


STAGES = ("off", "listening", "joining", "following")


def helper_view(reply):
    """What the page may say of the helper's answer, field by field."""
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        return {"running": False}
    out = {"running": True, "on": reply.get("on") is True, "stage": reply.get("stage") if reply.get("stage") in STAGES else "off",
           "why": reply.get("why")[:80] if isinstance(reply.get("why"), str) else ""}
    cpu, up = reply.get("cpu_seconds"), reply.get("uptime")
    if isinstance(cpu, (int, float)) and isinstance(up, (int, float)) and not isinstance(cpu, bool) and not isinstance(up, bool) and up > 0:
        out["cpu_percent"] = round(100.0 * cpu / up, 2)
    return out


class Link:
    """`api` gives the settings; `clock` is the box clock; `client` asks the helper (soundout.Client)."""

    def __init__(self, api, clock, client, state_path=None, now=time.monotonic):
        self.api, self.clock, self.client = api, clock, client
        self.state_path = state_path or paths.link_state()
        self._now = now
        self.lock = threading.Lock()        # not in the order of pvj/locks.py: never held over a call out
        self.helper = {"running": False}
        self.on = False                     # what the setting said at the last look: nothing is read while it is off
        self._looked = None

    def config(self):
        return stored(self.api.settings.data.get("link"))

    def save(self, cfg):
        settings = self.api.settings
        with settings.lock:
            old = settings.data.get("link")
            settings.data["link"] = cfg
            try:
                settings.save()
            except OSError:
                if old is None:
                    settings.data.pop("link", None)
                else:
                    settings.data["link"] = old
                raise
        self.tick()

    def tick(self):
        """About once a second (Sound.tick), and at once after a change: the helper is told again after a restart of either."""
        want = self.config()["on"]
        view = helper_view(self.client.request({"cmd": "status"}))
        if view["running"] and view["on"] != want:
            view = helper_view(self.client.request({"cmd": "on" if want else "off"}))
        with self.lock:
            self.helper, self.on = view, want
        return view

    def hear(self, now=None):
        """A look at the file, at most every LOOK_EVERY, nothing at all while the Owner's switch is off. Returns True if
        the clock took a word."""
        with self.lock:
            on = self.on
        if not on:
            return False
        t = self._now() if now is None else now
        with self.lock:
            if self._looked is not None and 0.0 <= t - self._looked < LOOK_EVERY:
                return False
            self._looked = t
        got = read_state(self.state_path)
        t = self._now() if now is None else now      # after the read: a record written just now is not "ahead of now"
        if got is None or not got["on"] or got["peers"] < 1 or abs(t - got["time"]) > STALE_SECONDS:
            return False
        return self.clock.link(got["bpm"], got["phase"], got["time"], t)

    def view(self):
        """`link` in GET /api/tempo and GET /api/sound: the switch, whether the helper is there, the peers and the tempo."""
        with self.lock:
            helper = dict(self.helper)
        on = self.config()["on"]
        out = {"on": on, "available": helper.get("running", False), "state": "unavailable", "peers": 0, "bpm": None}
        if not out["available"]:
            return out
        out["state"] = helper.get("stage", "off") if on else "off"
        out["why"] = helper.get("why", "")
        if "cpu_percent" in helper:
            out["cpu_percent"] = helper["cpu_percent"]
        got = read_state(self.state_path) if on else None
        if got is not None and got["peers"] >= 1 and abs(self._now() - got["time"]) <= STALE_SECONDS:
            out["peers"], out["bpm"] = got["peers"], round(got["bpm"], 2)
        return out
