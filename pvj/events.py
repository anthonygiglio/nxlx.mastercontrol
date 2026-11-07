# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A small capped log of what happened on this node (D125): the last 500 events, one line each.

An event is a time, a kind and a short plain sentence, and may name a device or an address (`who`), which only the Owner
sees: the diagnostics file carries the lines without it. Nothing secret is ever written (the PIN, a code or a login is
not in a sentence), and the text is cut to a short plain line.

It lives in the panel's state folder (events.json beside settings.json), the one place that is kept across a restart. With
the read-only root (pvj/rootfs.py, overlayroot) nothing on the system disk is kept, settings included, so there the file
is lost at a restart too, and the Owner is told so (`state()`).

A crash-type event (the node restarted, the player crashed, the renderer fell back, an update failed) is written at once.
The others are collected and written at most once a minute. The same event again within five minutes is one line with a
count, so a loop cannot fill the log or wear the card. The file is whole at every write (a rename), and never longer than
500 events."""

import json
import os
import re
import threading
import time

CAP = 500
BATCH_SECONDS = 60
DEDUPE_SECONDS = 300
REPEAT_WRITE_SECONDS = 10       # a count going up on a line already written is not worth a write more often than this
TEXT_MAX, WHO_MAX = 160, 80
FILE = "events.json"

KINDS = ("boot", "player", "renderer", "update", "projector", "network", "signin", "scene")
URGENT = ("boot", "player", "renderer")     # written at once; an update is when it failed (add(..., urgent=True))
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def plain(value, limit):
    """One short plain line: no control characters, runs of space made one, cut to `limit`."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:limit].strip()


class EventLog:
    def __init__(self, path, now=time.time, boot_id=lambda: "", schedule=None, log=print, cap=CAP, batch=BATCH_SECONDS):
        self.path, self._now, self._boot_id, self.log = path, now, boot_id, log
        self.cap, self.batch = cap, batch
        self._schedule = schedule or self._timer
        self.lock = threading.Lock()
        self.items = []
        self.meta = {}
        self._dirty = False
        self._timer_set = False
        self._written = None            # when the file was last written (the clock of _now)
        self._ok = True
        self._load()

    # ---- the file ----------------------------------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.items = [e for e in (self._clean_item(x) for x in data.get("events", []) if isinstance(x, dict)) if e][-self.cap:]
        meta = data.get("meta")
        if isinstance(meta, dict):
            self.meta = {k: v for k, v in meta.items() if k in ("boot", "update_at") and isinstance(v, (str, int)) and not isinstance(v, bool)}

    def _clean_item(self, x):
        t, kind, text = x.get("t"), x.get("kind"), plain(x.get("text"), TEXT_MAX)
        if isinstance(t, bool) or not isinstance(t, (int, float)) or kind not in KINDS or not text:
            return None
        out = {"t": int(t), "kind": kind, "text": text}
        who = plain(x.get("who"), WHO_MAX)
        if who:
            out["who"] = who
        n = x.get("n")
        if isinstance(n, int) and not isinstance(n, bool) and n > 1:
            out["n"] = min(n, 9999)
        return out

    def _write(self):
        """The whole file, by a rename. Called with the lock held."""
        data = {"version": 1, "meta": self.meta, "events": self.items[-self.cap:]}
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            if self._ok:
                self.log("pvj-web: the event log could not be written (%s); it goes on in memory" % (e.strerror or e))
            self._ok = False
            return
        self._ok = True
        self._dirty = False
        self._written = self._now()

    def _timer(self, delay, fn):
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()

    def _later(self):
        """A write is wanted within the batch time: once, whatever comes in meanwhile. Lock held."""
        if not self._timer_set:
            self._timer_set = True
            self._schedule(self.batch, self._due)

    def _due(self):
        with self.lock:
            self._timer_set = False
            if self._dirty:
                self._write()

    def flush(self):
        with self.lock:
            if self._dirty:
                self._write()

    # ---- adding ------------------------------------------------------------------------------------------------
    def add(self, kind, text, who=None, urgent=None):
        """One event. `urgent` (default: the kinds in URGENT) writes at once; else within a minute. The same kind, text and
        `who` again within five minutes is the same line with a count."""
        if kind not in KINDS:
            return
        text, who = plain(text, TEXT_MAX), plain(who, WHO_MAX)
        if not text:
            return
        now = int(self._now())
        urgent = kind in URGENT if urgent is None else bool(urgent)
        with self.lock:
            last = self.items[-1] if self.items else None
            repeat = bool(last and last["kind"] == kind and last["text"] == text and last.get("who", "") == who
                          and 0 <= now - last["t"] <= DEDUPE_SECONDS)
            if repeat:
                last["n"] = min(last.get("n", 1) + 1, 9999)
                last["t"] = now
            else:
                item = {"t": now, "kind": kind, "text": text}
                if who:
                    item["who"] = who
                self.items.append(item)
                del self.items[:-self.cap]
            self._dirty = True
            fresh = self._written is None or self._now() - self._written >= REPEAT_WRITE_SECONDS
            if urgent and (not repeat or fresh):
                self._write()
            else:
                self._later()

    def start(self):
        """At the panel's start: a boot id that is not the one in the file is a restart of the node. Written at once."""
        boot = self._boot_id()
        with self.lock:
            before = self.meta.get("boot")
            self.meta["boot"] = boot
            self._dirty = True
        if boot and before and before != boot:
            self.add("boot", "The node restarted.")
        else:
            self.flush()

    def update_seen(self, at):
        """True when an update result time (`at`) is newer than the last one announced; remembers it."""
        with self.lock:
            if not isinstance(at, (int, float)) or isinstance(at, bool) or at <= self.meta.get("update_at", 0):
                return False
            self.meta["update_at"] = int(at)
            self._dirty = True
            return True

    # ---- reading -----------------------------------------------------------------------------------------------
    def events(self, who=True):
        """Newest first. `who` False leaves out the device names and addresses (the diagnostics file)."""
        with self.lock:
            rows = [dict(e) for e in self.items]
        rows.reverse()
        if not who:
            for r in rows:
                r.pop("who", None)
        return rows

    @staticmethod
    def line(e):
        """One line of text: time, kind and sentence (with the count when it happened more than once)."""
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e["t"]))
        return "%s UTC [%s] %s%s" % (stamp, e["kind"], e["text"], " (%d times)" % e["n"] if e.get("n") else "")

    def state(self, read_only_root=False):
        """What to tell the Owner about keeping it."""
        note = ("The system disk of this node is read-only, so this log is kept in memory and is lost at a restart."
                if read_only_root else
                "The last %d events are kept in the panel's state folder and survive a restart." % self.cap)
        if not self._ok and not read_only_root:
            note = "The event log could not be written to the disk just now; it goes on in memory."
        return {"cap": self.cap, "kept": not read_only_root and self._ok, "note": note}
