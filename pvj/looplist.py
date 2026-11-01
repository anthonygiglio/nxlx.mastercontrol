# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The seamless loop of a plain clip on the New renderer (D118).

mpv's loop-file seeks at every pass, and the decoder's restart holds the picture 113 to 201 ms on a Pi 4 under load
(measurements/2026-10-31-loop-seam). A generated ffconcat list of the clip keeps the packets flowing through one
decoder: 34 to 57 ms, with D22's access-references=no left on (measurements/2026-10-31-loop-concat). Two parts here:

* `Lists` writes the list and keeps the folder. `for_clip` answers at once: a clip whose exact length is not known yet
  plays as it always did, and its length is found on another thread for the next load. Anything unusual is the same
  plain load, so every answer here is a list or None and nothing raises.
* `Clips` is the one place where a list is seen for what it is. Everything above Player keeps seeing a plain clip.

What a list needs of its folder (the panel's own; the player reads it through group pvj, D45): the concat demuxer opens
the inner file again at every join, so a list and its link stay while any layer plays them, both sides of a crossfade
and a crossfade from a clip to itself among them. `_prune` keeps to that.
"""

import hashlib
import math
import os
import re
import stat
import threading
import time

from . import probe

PAIRS = 1000                    # 100 minutes of a 6 s clip; at the end of the list one ordinary seam, and it goes on
MIN_SECONDS = 0.2               # a shorter clip is a seam of its own
QUEUE = "vd-queue-enable=yes,vd-queue-max-samples=6,vd-queue-max-secs=2"    # with every load of a list, for that file alone
# What mpv calls the containers that were played through a list on the node and came out clean: MP4 and MOV, Matroska
# and WebM (both "mkv"), MPEG-TS, AVI (D118). Anything else may well work; it was not looked at, so it plays as it always did.
FORMATS = ("mov,mp4,m4a,3gp,3g2,mj2", "mkv", "mpegts", "avi")
KEEP = 32                       # lists left in the folder after a prune
MOST = 128                      # never more than this: a list is 45 KB of the box's memory. Past it, plain loads
GRACE = 180.0                   # seconds a list that was just used or just left is not touched: longer than any crossfade
WAITING = 32                    # clips waiting for their length, at most
KNOWN = 1000                    # lengths remembered, at most
_NAME = re.compile(r"[0-9a-f]{32}")
_FILE = re.compile(r"([0-9a-f]{32})(\.ffconcat|\.mp4)?(\.tmp)?")
_LENGTH = re.compile(rb"\nduration ([0-9]+\.[0-9]{6})\n")


class View:
    """A list seen as what it stands for."""
    __slots__ = ("clip", "length")

    def __init__(self, clip, length):
        self.clip, self.length = clip, length

    def inside(self, position):
        return position % self.length

    def pass_of(self, position):
        return max(0, math.floor(position / self.length))      # the very start may read a hair under 0


class Lists:
    def __init__(self, folder, mpv_bin="mpv", enabled=None, prober=probe.exact, clock=time.time):
        self.folder, self.mpv_bin, self.prober, self._clock = folder, mpv_bin, prober, clock
        self._enabled = enabled or (lambda: True)
        self._lock = threading.Lock()   # a leaf: a few fields, never held over a call (pvj/locks.py)
        self._lengths = {}              # (real path, size, mtime_ns): seconds, or None for a clip that stays plain
        self._queue = []
        self._worker = None
        self._views = {}                # list path: (its mtime_ns, View)
        self._any = False

    # ---- the list for a load -------------------------------------------------------------------------------------
    def for_clip(self, clip, playing=None):
        """The path of the list that loops `clip`, or None to load the clip itself. Takes no time worth the name: no
        program is started here. `playing`: what asks the player for the paths in use, for a prune."""
        try:
            if not self._enabled() or "://" in clip:
                return None
            real = os.path.realpath(clip)   # the name; the link is the clip as it was given, which is what the panel reads back
            st = os.stat(real)
            if not stat.S_ISREG(st.st_mode):
                return None
            key = (real, st.st_size, st.st_mtime_ns)
            with self._lock:
                known = key in self._lengths
                seconds = self._lengths.get(key)
            if not known:
                self._find(key)
                return None
            if seconds is None:
                return None
            return self._write(real, os.path.abspath(clip), seconds, playing)
        except (OSError, ValueError, TypeError, UnicodeError):
            return None

    def _find(self, key):
        """Find this clip's length on the worker. Never in the load: a cut does not wait for a program."""
        with self._lock:
            if key in self._queue or len(self._queue) >= WAITING:
                return
            self._queue.append(key)
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._work, name="loop-lengths", daemon=True)
            worker = self._worker
        worker.start()

    def _work(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                key = self._queue[0]
            try:
                seconds, kind, video = self.prober(key[0], self.mpv_bin)
                found = True
            except Exception:           # gone, or a box too busy to answer: nothing is known, and the next load asks again
                seconds, kind, video, found = None, None, None, False
            with self._lock:
                self._queue.remove(key)
                if found:
                    if len(self._lengths) >= KNOWN:
                        self._lengths.clear()
                    usable = kind in FORMATS and video and seconds is not None and seconds >= MIN_SECONDS
                    self._lengths[key] = seconds if usable else None

    @staticmethod
    def name_of(real):
        return hashlib.sha256(os.fsencode(real)).hexdigest()[:32]

    def _write(self, real, given, seconds, playing):
        os.makedirs(self.folder, mode=0o750, exist_ok=True)
        st = os.lstat(self.folder)
        if not stat.S_ISDIR(st.st_mode):
            return None
        name = self.name_of(real)
        path = os.path.join(self.folder, name + ".ffconcat")
        link = os.path.join(self.folder, name + ".mp4")
        body = ("ffconcat version 1.0\n" + ("file %s.mp4\nduration %.6f\n" % (name, seconds)) * PAIRS).encode()
        if not self._room(name, playing):
            return None
        same_link = False
        try:
            same_link = os.readlink(link) == given
        except OSError:
            pass
        if not same_link:
            tmp = link + ".tmp"
            self._remove(tmp)
            os.symlink(given, tmp)
            os.replace(tmp, link)           # the link first: a list never stands without its link
        try:
            with open(path, "rb") as f:
                same = f.read() == body
        except OSError:
            same = False
        if same:
            os.utime(path)                  # used now: see GRACE
        else:
            tmp = path + ".tmp"
            self._remove(tmp)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
        with self._lock:
            self._any = True
        return path

    @staticmethod
    def _remove(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def _names(self):
        """{name: when its list was last used} of what is in the folder, leftovers included (by their own time)."""
        out = {}
        with os.scandir(self.folder) as it:
            for e in it:
                m = _FILE.fullmatch(e.name)
                if m and (m.group(2) == ".ffconcat" and not m.group(3)):
                    out[m.group(1)] = e.stat(follow_symlinks=False).st_mtime
                elif m:
                    out.setdefault(m.group(1), e.stat(follow_symlinks=False).st_mtime)
        return out

    def _room(self, keep, playing):
        """Prune what no layer uses, once the folder holds more than KEEP. False when there is no room for one more
        (MOST), and the load is plain. A player that cannot say what it plays has nothing pruned under it."""
        names = self._names()
        if len(names) <= KEEP:
            return True
        try:
            used = set(playing()) if playing else None
        except Exception:
            used = None
        if used is None:
            return len(names) < MOST or keep in names
        used = {os.path.basename(p).split(".")[0] for p in used if isinstance(p, str) and os.path.dirname(p) == self.folder}
        now = self._clock()
        spare = sorted((t, n) for n, t in names.items() if n != keep and n not in used and abs(now - t) >= GRACE)
        for t, n in spare[:max(0, len(names) - KEEP)]:
            try:
                self._remove(os.path.join(self.folder, n + ".ffconcat"))    # the list first: a link is never missing under a list
                self._remove(os.path.join(self.folder, n + ".mp4"))
                self._remove(os.path.join(self.folder, n + ".ffconcat.tmp"))
                self._remove(os.path.join(self.folder, n + ".mp4.tmp"))
            except OSError:
                continue
            names.pop(n, None)
        return len(names) < MOST or keep in names

    def touch(self, path):
        """A list that a crossfade is leaving plays on under it: `_room` leaves it alone for GRACE from now."""
        if self.view(path) is not None:
            try:
                os.utime(path)
            except OSError:
                pass

    # ---- reading a list back -----------------------------------------------------------------------------------
    def any(self):
        """Whether a list can be playing at all: on a box that never made one no read costs a second question. On the
        folder, not in memory: a panel that started again finds the list its player still plays."""
        if self._any:
            return True
        try:
            with os.scandir(self.folder) as it:
                found = any(e.name.endswith(".ffconcat") for e in it)
        except OSError:
            return False
        if found:
            self._any = True
        return found

    def view(self, path):
        """The clip and its length if `path` is a list of this folder, else None. Read from the files, so that it holds
        for a player this panel did not load (it was started again) and for one whose list another panel wrote."""
        if not isinstance(path, str) or not path.endswith(".ffconcat") or os.path.dirname(path) != self.folder:
            return None
        name = os.path.basename(path)[:-len(".ffconcat")]
        if not _NAME.fullmatch(name):
            return None
        try:
            mark = os.stat(path).st_mtime_ns
            with self._lock:
                have = self._views.get(path)
            if have and have[0] == mark:
                return have[1]
            with open(path, "rb") as f:
                head = f.read(256)
            found = _LENGTH.search(head)
            view = View(os.readlink(os.path.join(self.folder, name + ".mp4")), float(found.group(1)))
        except (OSError, AttributeError, ValueError):
            return None
        if view.length <= 0:
            return None
        with self._lock:
            if len(self._views) > 16:
                self._views.clear()
            self._views[path] = (mark, view)
        return view


class Clips:
    """The one place between the socket and everything else (Ipc.translate). A source that plays a list reads as the
    plain clip: `path`, `duration`, `time-pos`, `percent-pos`, `playlist`, the rows of `pvj-sources`, and a seek inside
    the clip lands in the pass that plays now. What is not named here goes through untouched. The answers are worked
    out from what the player says at that moment, so nothing here can go stale."""

    def __init__(self, lists):
        self.lists = lists

    def request(self, ask, command, wait=None):
        head, rest = (command[:2], command[2:]) if command and command[0] == "pvj-source" else ((), command)
        if rest and rest[0] == "get_property" and len(rest) == 2:
            read = self._READS.get(rest[1])
            if read is not None:
                return read(self, lambda prop: ask(*head, "get_property", prop, wait=wait))
        elif rest and rest[0] == "seek" and len(rest) == 3 and self.lists.any():
            return self._seek(lambda *c: ask(*head, *c, wait=wait), lambda prop: ask(*head, "get_property", prop, wait=wait), rest[1], rest[2])
        return ask(*command, wait=wait)

    def _path(self, get):
        raw = get("path")
        view = self.lists.view(raw)
        return view.clip if view else raw

    def _view(self, get):
        """The View of what this source plays, None for anything else (no question at all on a box with no lists)."""
        return self.lists.view(get("path")) if self.lists.any() else None

    def _duration(self, get):
        view = self._view(get)
        return view.length if view else get("duration")

    def _position(self, get):
        view = self._view(get)
        position = get("time-pos")
        return view.inside(position) if view and isinstance(position, (int, float)) else position

    def _percent(self, get):
        view = self._view(get)
        if view is None:
            return get("percent-pos")
        position = get("time-pos")
        return 100.0 * view.inside(position) / view.length if isinstance(position, (int, float)) else get("percent-pos")

    def _playlist(self, get):
        rows = get("playlist")
        if isinstance(rows, list):
            for row in rows:
                view = self.lists.view(row.get("filename")) if isinstance(row, dict) else None
                if view:
                    row["filename"] = view.clip
        return rows

    def _sources(self, get):
        rows = get("pvj-sources")
        if isinstance(rows, list):
            for row in rows:
                view = self.lists.view(row.get("path")) if isinstance(row, dict) else None
                if view:
                    row["path"] = view.clip
        return rows

    _READS = {"path": _path, "duration": _duration, "time-pos": _position, "percent-pos": _percent, "playlist": _playlist,
              "pvj-sources": _sources}

    def _seek(self, send, get, value, flags):
        """An absolute place in the clip is that place in the pass that plays (`absolute+exact` is one string). A
        relative seek needs nothing: it is a distance on either timeline, and past a join it is the loop's own wrap."""
        words = str(flags).split("+")
        view = self._view(get) if isinstance(value, (int, float)) else None
        if view is None:
            return send("seek", value, flags)
        rest = [w for w in words if w not in ("absolute", "absolute-percent", "relative-percent", "relative")]
        if "relative-percent" in words:
            return send("seek", value * view.length / 100.0, "+".join(["relative"] + rest))
        if "absolute" not in words and "absolute-percent" not in words:
            return send("seek", value, flags)
        place = value if "absolute" in words else value * view.length / 100.0
        try:
            position = get("time-pos")
        except Exception:
            position = None
        at = view.pass_of(position) if isinstance(position, (int, float)) else 0
        return send("seek", at * view.length + max(0.0, place) % view.length, "+".join(["absolute"] + rest))
