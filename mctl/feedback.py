# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Feedback from the people in the room (J-179, D147): a face and, if they want, a few words.

A note is a face (good, so-so or bad), an optional short text, and what the node knows about that moment: its name and id,
the screen the tester was on, the time, what was playing on each output, and the tester's role (owner, operator or guest).
The tester types none of the context. Nothing that names a person or a device is kept: no device name, no device id, no
address, no token. A note is made of plain text only (no control characters), the text is cut to 1000 characters, and the
playing lines to a short plain sentence each.

The newest 500 notes are kept in the panel's state folder (feedback.json beside settings.json and events.json), the one
place that is kept across a restart. The file is written by the panel's own account only, by a rename, and is never
opened through a link: a link planted at the name or at the temporary name is refused, and a file that is not ours (damaged,
not a list, an odd note) is read as far as it is good and never stops the panel. With the read-only root (mctl/rootfs.py)
nothing on the system disk is kept, so the notes are lost at a restart and the Owner is told so (`state()`).

A session (one paired device) may send one note in five seconds, so a stuck finger cannot fill the store. The node as a
whole takes at most 30 notes in ten minutes and 200 in a day (BOX_LIMITS), so a crowd of devices, or one that keeps
re-pairing, cannot push the real notes out. A refused send is answered with the seconds to wait, and nothing is stored.

The file is UTF-8 (an emoji is 4 bytes, not the 12 an escaped one takes) and is kept under 2 MB: the oldest notes are
dropped from the file and the list until it fits. It is read through a file descriptor that is a regular file and no
larger than the limit; anything else is read as no notes."""

import json
import math
import os
import re
import stat
import threading
import time

CAP = 500
TEXT_MAX = 1000
FACES = ("good", "so-so", "bad")
ROLES = ("owner", "operator", "guest")
MIN_GAP = 5.0                       # seconds between two sends of one session
BOX_LIMITS = ((600.0, 30), (86400.0, 200))      # (seconds, notes): all sessions together, the whole node
BUSY_TEXT = "Lots of feedback just now, try again in a few minutes"
MAX_BYTES = 2 * 1024 * 1024         # the file is kept under this: the oldest notes go first
FILE = "feedback.json"
SCREEN_MAX, NAME_MAX, LINE_MAX, OUTPUTS_MAX = 24, 60, 120, 16
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]+")     # tab and newline stay in the tester's own words; the rest goes
_SCREEN = re.compile(r"^[a-z0-9_-]{1,%d}(/[a-z0-9_-]{1,%d})?$" % (SCREEN_MAX, SCREEN_MAX))


class Busy(Exception):
    """The node took its share of notes for now (BOX_LIMITS). `wait` is the whole seconds to wait."""
    def __init__(self, wait):
        super().__init__(BUSY_TEXT)
        self.wait = wait


class TooFast(Exception):
    """A send came before the gap was over. `wait` is the whole seconds to wait."""
    def __init__(self, wait):
        super().__init__("wait a few seconds before sending more")
        self.wait = wait


def _utf8(value):
    """The text with any lone surrogate (which UTF-8 cannot hold) made a question mark."""
    return value.encode("utf-8", "replace").decode("utf-8")


def plain(value, limit):
    """Plain text: no control characters (a tab or a new line stays), cut to `limit`, trimmed."""
    if not isinstance(value, str):
        return ""
    return _utf8(_CONTROL.sub(" ", value)).strip()[:limit].strip()


def line(value, limit):
    """One plain line: every run of space and control characters made one space."""
    if not isinstance(value, str):
        return ""
    return _utf8(re.sub(r"[\s\x00-\x1f\x7f]+", " ", value)).strip()[:limit].strip()


def clean_time(t):
    """A whole number of seconds from 0 to 10**11, or None."""
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    if isinstance(t, float) and not math.isfinite(t):
        return None
    return int(t) if 0 <= t <= 10 ** 11 else None


def clean_outputs(rows):
    """What was playing, as a short list of {"output": name, "playing": sentence}: at most OUTPUTS_MAX, every word plain."""
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows[:OUTPUTS_MAX]:
        if not isinstance(r, dict):
            continue
        name, playing = line(r.get("output"), NAME_MAX), line(r.get("playing"), LINE_MAX)
        if name and playing:
            out.append({"output": name, "playing": playing})
    return out


def clean_note(x):
    """One note as it is kept, or None when it is not one. Used for what is sent and for what is read back from the file."""
    if not isinstance(x, dict):
        return None
    t, face, role = clean_time(x.get("t")), x.get("face"), x.get("role")
    if t is None or face not in FACES or role not in ROLES:
        return None
    out = {"t": t, "face": face, "role": role}
    text = plain(x.get("text"), TEXT_MAX)
    if text:
        out["text"] = text
    screen = x.get("screen")
    if isinstance(screen, str) and _SCREEN.match(screen):
        out["screen"] = screen
    node = x.get("node")
    if isinstance(node, dict):
        n = {"name": line(node.get("name"), NAME_MAX), "id": line(node.get("id"), 64)}
        out["node"] = {k: v for k, v in n.items() if v}
    out["playing"] = clean_outputs(x.get("playing"))
    return out


class FeedbackStore:
    def __init__(self, path, now=time.time, clock=time.monotonic, log=print, cap=CAP, gap=MIN_GAP,
                 box_limits=BOX_LIMITS, max_bytes=MAX_BYTES):
        self.path, self._now, self._clock, self.log = path, now, clock, log
        self.cap, self.gap, self.box_limits, self.max_bytes = cap, gap, tuple(box_limits), max_bytes
        self._stamps = []               # when notes were kept (the monotonic clock), newest last, memory only
        self.lock = threading.Lock()
        self.items = []
        self._last = {}                 # session id -> when it last sent (the monotonic clock), memory only
        self._ok = True
        self._load()

    # ---- the file ----------------------------------------------------------------------------------------------
    def _load(self):
        """The file if it is ours, opened with no following of a link. Damage leaves out what is wrong, never stops the panel."""
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_bytes:
                    raise ValueError("not a file of ours")      # a pipe, a device or a file far past what is ever written
                raw = f.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("too large")
            data = json.loads(raw.decode("utf-8"))
            rows = data.get("notes") if isinstance(data, dict) else None
            if isinstance(rows, list):
                items = []
                for x in rows:
                    try:
                        n = clean_note(x)
                    except Exception:
                        n = None
                    if n:
                        items.append(n)
                self.items = items[-self.cap:]
        except Exception:               # OSError (a link, a missing file), ValueError, RecursionError from a hostile file
            self.items = []

    def _fit(self, items):
        """`items` without its oldest notes until the file (UTF-8, not escaped) is under max_bytes, and that file's bytes."""
        sizes = [len(json.dumps(n, ensure_ascii=False).encode("utf-8")) + 2 for n in items]
        total, first = 64 + sum(sizes), 0
        while total > self.max_bytes and first < len(items) - 1:       # the newest note always stays
            total -= sizes[first]
            first += 1
        items = items[first:]
        return items, json.dumps({"version": 1, "notes": items}, ensure_ascii=False).encode("utf-8")

    def _write(self, blob):
        """The whole file, by a rename, from `blob` (the bytes of _fit). True when it is on the disk. Lock held."""
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            if self._ok:
                self.log("nxlx-web: the feedback notes could not be written (%s)" % (e.strerror or e))
            self._ok = False
            return False
        self._ok = True
        return True

    # ---- adding ------------------------------------------------------------------------------------------------
    def add(self, session, face, text, role, screen, node, playing):
        """Keep one note and return it. Raises ValueError for a face that is none of the three, TooFast when this session
        sent less than `gap` seconds ago, Busy when the node has taken its share of notes (BOX_LIMITS), OSError when it could not be written (then nothing is kept and the session may
        try again at once). The text is cut to TEXT_MAX; the rest is made plain by clean_note."""
        if face not in FACES:
            raise ValueError("choose good, so-so or bad")
        if role not in ROLES:
            raise ValueError("unknown role")
        now = self._clock()
        with self.lock:
            for k in [k for k, t in self._last.items() if now - t >= self.gap]:      # a session that went quiet leaves no entry
                del self._last[k]
            held = self._last.get(session)
            if held is not None and 0 <= now - held < self.gap:
                raise TooFast(int(self.gap - (now - held)) + 1)
            wait = self._box_wait(now)
            if wait:
                raise Busy(wait)
            note = clean_note({"t": int(self._now()), "face": face, "text": text, "role": role, "screen": screen,
                               "node": node, "playing": playing})
            if note is None:
                raise ValueError("not a note")
            items, blob = self._fit((self.items + [note])[-self.cap:])
            if not self._write(blob):
                raise OSError("could not save the note")
            self.items = items
            self._last[session] = now
            self._stamps.append(now)
        return dict(note)

    def _box_wait(self, now):
        """Whole seconds to wait before the node takes another note, or 0. Counts nothing. Lock held."""
        horizon = max((w for w, _ in self.box_limits), default=0.0)
        self._stamps = [t for t in self._stamps if 0 <= now - t < horizon]
        wait = 0.0
        for window, most in self.box_limits:
            inside = [t for t in self._stamps if now - t < window]
            if len(inside) >= most:
                wait = max(wait, window - (now - inside[len(inside) - most]))
        return int(wait) + 1 if wait else 0

    # ---- reading -----------------------------------------------------------------------------------------------
    def notes(self):
        """Every kept note, newest first (copies)."""
        with self.lock:
            rows = [dict(n) for n in self.items]
        rows.reverse()
        return rows

    def state(self, read_only_root=False):
        """What to tell the Owner about keeping them."""
        note = ("The system disk of this node is read-only, so the notes are kept in memory and are lost at a restart."
                if read_only_root else
                "The newest %d notes are kept in the panel's state folder and survive a restart." % self.cap)
        if not self._ok and not read_only_root:
            note = "The notes could not be written to the disk just now."
        return {"cap": self.cap, "kept": not read_only_root and self._ok, "note": note}
