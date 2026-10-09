# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Transitions between clips that blend two pictures: the crossfade (D71).

The player shows one picture at a time, so the outgoing picture cannot go on moving under the incoming one. What
is built: a still of the SCREEN as it is at the moment of the play is laid over everything as the player's own
overlay, the new clip starts under it, and from the new clip's first frame the still is made more transparent step
by step until it is gone. The outgoing picture therefore stands still for the length of the transition.

Why the still is an overlay and not a texture in a shader (settled by experiment on a real mpv in CI, on the
throwaway branch transitions-spike; the numbers are in D71):
* A shader has no clock it can start. mpv's `frame` number never starts again (not at a new file, a seek, a new
  shader text or a new buffer format), nothing outside the shader can read it, and it stands still over a still
  picture and a frozen clip. Progress could only come as a new text at every step, and a new text is drawn 0.05 to
  0.37 seconds after it was asked for on a Pi 4 (measured for the effects): three to five steps a second.
* No screenshot gives the picture with the effect in it and without the mapping: the "video" kind runs the shader
  at OUTPUT too, and holds the brightness. Fed in before the mapping it would be warped twice and dimmed twice.
* A shader only draws where the new clip's picture is. A still of another shape would be squeezed into it.
The still of the window is the screen itself: the effect, the mapping, the opacity, the size and the logo are in
it once, it is drawn after all of them, and it covers the whole screen whatever the new clip's shape.

What that costs, and the rules that follow:
* The player's brightness does not reach an overlay, so Blackout, a fade and a change of opacity END the
  transition at once (the still goes, the new clip is there). So does Stop, and any other way of playing.
* Every step sends the whole still again with its new transparency (bytes.translate, a file, one command). The
  steps are paced against the clock: a slow box makes fewer steps, never a longer transition.
* The outgoing clip is frozen before the still is taken: the still takes time (on a Pi 4 a screenshot of a
  2560 x 1440 screen took about 0.7 seconds), and a clip that played on meanwhile would jump back to the still.
* A screen larger than MAX_PIXELS, a player that is too slow to take the still (SLOW seconds) or to make
  MIN_RATE steps a second, a clip that drops more than MAX_DROPS frames a second under the steps: the box uses
  the dip to black from then on, and says so in `GET /api/status` (mix.fallback), until the transition is chosen
  again. While an access code is on the display it dips as well (the code's pixels would go into the still).
* The player draws no overlay with nothing to draw on, so a transition needs something playing.

Rollback: an older release knows "cut" and "dip" only and refuses any other value when the transition or its
duration is saved or a settings file is imported. So the settings keep `transition: "dip"` and name the newer
transition beside it in `style`, a key older code never reads and drops at its next save.
"""
import os
import re
import struct
import threading
import time
import zlib

from . import paths
from .player import PlayerError

OLD = ("cut", "dip")            # what every release knows
STYLES = ("crossfade",)         # kept in mix.style, with mix.transition "dip" beside it for an older release
NAMES = OLD + STYLES
OVERLAY_ID = 63                 # the last one: nothing of the panel's is drawn over the still
MAX_PIXELS = 2560 * 1440        # of the screen; above it the box dips (a step would send more than 14 MB)
SLOW = 1.0                      # seconds the still may take (the screenshot and its conversion) before the box gives up on crossfades
MIN_RATE = 5.0                  # steps a second a transition must manage, or the box gives up on crossfades
MAX_DROPS = 5.0                 # frames a second the clip under it may drop meanwhile, or the box gives up on crossfades
STEPS = 20.0                    # steps a second asked for (the Fader's rate)
FIRST_FRAME = 10.0              # seconds to wait for the new clip's first frame under the still (a stream that does not come)
POLL = 0.03
STILL_NAME = r"transition-\d+\.png"


def named(mix):
    """The transition these Mix settings ask for: one of NAMES. A value nobody knows is a cut, as it always was."""
    mix = mix if isinstance(mix, dict) else {}
    if mix.get("style") in STYLES:
        return mix["style"]
    return mix["transition"] if mix.get("transition") in OLD else "cut"


def stored(name, duration):
    """The Mix settings for a transition, in the form an older release can read (see the top of this file)."""
    if name in STYLES:
        return {"transition": "dip", "style": name, "duration": float(duration)}
    return {"transition": name, "duration": float(duration)}


class StillError(Exception):
    pass


def read_still(path, size):
    """The pixels of the player's screenshot at `path` as premultiplied BGRA, opaque: (width, height, bytes).
    The file must be what Player.still writes: an 8-bit PNG of exactly `size` whose rows are plain bytes (no row
    filter), so that reading it is a few copies and no arithmetic in Python. Anything else is refused, and no more
    is unpacked than such a picture holds."""
    try:
        with open(path, "rb") as f:
            data = f.read(size[0] * size[1] * 4 + size[1] + (1 << 20))
    except OSError as e:
        raise StillError("the still could not be read: %s" % e)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise StillError("the still is not a PNG")
    pos, parts, w, h, bpp = 8, [], 0, 0, 0
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IHDR" and n >= 13:
            w, h, depth, ctype = struct.unpack(">IIBB", data[pos + 8:pos + 18])
            bpp = {2: 3, 6: 4}.get(ctype, 0) if depth == 8 else 0
        elif kind == b"IDAT":
            parts.append(data[pos + 8:pos + 8 + n])
        pos += 12 + n
    if (w, h) != tuple(size) or not bpp:
        raise StillError("the still is not an 8-bit picture of the screen's size")
    stride = w * bpp + 1
    unpacker = zlib.decompressobj()
    try:
        raw = unpacker.decompress(b"".join(parts), stride * h + 1)
    except zlib.error as e:
        raise StillError("the still is damaged: %s" % e)
    if len(raw) != stride * h or raw[0::stride].count(0) != h:
        raise StillError("the still's rows are not plain bytes")
    body = b"".join([raw[y * stride + 1:(y + 1) * stride] for y in range(h)])
    out = bytearray(w * h * 4)
    out[0::4], out[1::4], out[2::4] = body[2::bpp], body[1::bpp], body[0::bpp]
    out[3::4] = b"\xff" * (w * h)
    return w, h, bytes(out)


_TABLES = {}


def faded(pixels, level):
    """Premultiplied BGRA `pixels` at `level` of 255: every byte, the alpha too, scaled by it."""
    if level >= 255:
        return pixels
    table = _TABLES.get(level)
    if table is None:
        table = _TABLES[level] = bytes((v * level + 127) // 255 for v in range(256))
    return pixels.translate(table)


class Crossfade:
    """The one blend built so far: the whole still, more transparent at every step. Another transition is another
    class with the same `step`: what to draw of the still at `progress` (0 to 1), as (x, y, width, height, pixels),
    or None for nothing. (A wipe would give a narrower part of the same pixels and cost no arithmetic at all.)"""
    name = "crossfade"

    def step(self, width, height, pixels, progress):
        level = int(round(255 * (1.0 - progress)))
        return (0, 0, width, height, faded(pixels, level)) if level > 0 else None

    def same(self, a, b):
        """True when two moments of the transition draw the same thing (a step is left out then)."""
        return int(round(255 * (1.0 - a))) == int(round(255 * (1.0 - b)))


BLENDS = {"crossfade": Crossfade}


class Transitions:
    def __init__(self, api, clock=time.monotonic, sleep=time.sleep, thread=True, log=None):
        self.api = api
        self._clock, self._sleep, self._thread = clock, sleep, thread
        self.log = log or (lambda line: None)
        self._lock = threading.RLock()      # the token, the still and every overlay command of a transition
        self._token = 0
        self._still = None                  # (width, height, pixels) while a transition holds or runs
        self._pid = None                    # the player the still was given to: a restarted one never had it
        self._frozen = True                 # False while the outgoing clip is frozen by the transition, not by somebody
        self.running = None                 # the name of the transition that holds or runs
        self.given_up = ""                  # why this box dips instead, until the transition is chosen again
        self.last = {}                      # what the last one did: {"name", "still_ms", "steps", "seconds", "dropped", "ended"}

    # -- what the box will do --
    def fallback(self):
        """Why a crossfade would not be done now, or "": the reason the box gave up, a screen too large."""
        if self.given_up:
            return self.given_up
        size = self._size()
        if size and size[0] * size[1] > MAX_PIXELS:
            return "the screen is larger than %d x %d" % (2560, 1440)
        return ""

    def chosen_again(self):
        """Somebody saved the transition: the box tries a crossfade again."""
        self.given_up = ""

    def _size(self):
        try:
            return self.api.player.osd_size()
        except Exception:
            return None

    # -- one transition --
    def hold(self, name):
        """Lay a still of the screen over everything, in place of a transition that still runs. True if it is there:
        then the new clip can be started under it, and `run` must follow. False, with nothing on the screen changed
        and nothing left behind, if there is no such blend, the box dips instead (`fallback`), an access code is on
        the display (its pixels would go into the still) or the player would not do it."""
        blend = BLENDS.get(name)
        player = self.api.player
        if blend is None or self.fallback() or not hasattr(player, "still"):
            return False
        try:
            if self.api.access_on_screen():
                return False
        except Exception:
            return False
        began = self._clock()
        path = os.path.join(player.rundir, "transition-%d.png" % os.getpid())
        with self._lock:
            self._token += 1
            self._frozen = True
            try:
                size = player.osd_size()
                if not size or size[0] * size[1] > MAX_PIXELS:
                    raise StillError("no screen size")
                pid = player.ipc.request("get_property", "pid")
                self._frozen = player.ipc.request("get_property", "pause") is True
                player.pause(True)              # the outgoing picture stands still from the tap on (see the top)
                # The player writes into this file, which is ours: it is made here, group-writable as the units'
                # umask leaves every file of the panel, and removed here whatever happens.
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o660)
                os.close(fd)
                player.still(path)
                w, h, pixels = read_still(path, size)
                player.overlay(OVERLAY_ID, 0, 0, w, h, pixels)
            except (PlayerError, StillError, OSError) as e:
                self.log("pvj-web: no %s, a cut instead: %s" % (name, e))
                self._drop()
                self._thaw()
                return False
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._still, self.running, self._pid = (w, h, pixels), name, pid
            took = self._clock() - began
            self.last = {"name": name, "still_ms": int(round(took * 1000)), "steps": 0, "seconds": 0.0, "ended": ""}
            if took > SLOW:
                self._give_up("the still took %.1f seconds" % took)
            return True

    def run(self, seconds):
        """The new clip has been started under the still: wait for its first frame, then take the still away over
        `seconds`. Returns at once; the work is a thread's."""
        with self._lock:
            if self._still is None:
                return
            token = self._token
        if self._thread:
            threading.Thread(target=self._work, args=(token, seconds), name="transition", daemon=True).start()
        else:
            self._work(token, seconds)

    def end(self, why="ended"):
        """Take the still off now. Called by everything the still would be wrong over: Blackout, a fade, a change of
        opacity, Stop, another way of playing. Nothing happens when no transition runs."""
        with self._lock:
            self._token += 1
            if self._still is not None:
                self.last["ended"] = why
                self._drop()

    def abandon(self):
        """The new clip did not start after all: the still goes and the old clip plays on, unless it was frozen
        before the transition froze it."""
        with self._lock:
            self.end("the new clip did not start")
            self._thaw()

    def _thaw(self):
        if not self._frozen:
            try:
                self.api.player.pause(False)
            except Exception:
                pass
        self._frozen = True

    def _drop(self):
        """The still leaves the screen and its file goes (under the lock)."""
        self._still, self.running = None, None
        try:
            self.api.player.overlay_remove(OVERLAY_ID)
        except Exception:
            pass

    def _give_up(self, why):
        self.given_up = why + "; using the dip to black until the transition is chosen again"
        self.log("pvj-web: crossfade given up: %s" % why)

    def _first_frame(self):
        """True when the new clip's first frame is drawn, or nothing plays any more. Raises PlayerError."""
        ipc = self.api.player.ipc
        try:
            if isinstance(ipc.request("get_property", "time-pos"), (int, float)):
                return not ipc.request("get_property", "seeking")
        except PlayerError as e:
            if "unavailable" not in str(e):
                raise
        return ipc.request("get_property", "idle-active") is True

    def _dropped(self):
        """Frames the player has dropped so far (its output and its decoder), or None if it cannot say."""
        total = 0
        for prop in ("frame-drop-count", "decoder-frame-drop-count"):
            try:
                v = self.api.player.ipc.request("get_property", prop)
            except PlayerError:
                return None
            total += v if isinstance(v, int) and not isinstance(v, bool) else 0
        return total

    def _work(self, token, seconds):
        deadline = self._clock() + FIRST_FRAME
        try:
            while self._clock() < deadline:
                with self._lock:
                    if token != self._token:
                        return
                if self._first_frame():
                    break
                self._sleep(POLL)
            dropped = self._dropped()
            began, steps, before = self._clock(), 0, 0.0
            while True:
                progress = (self._clock() - began) / seconds if seconds > 0 else 1.0
                with self._lock:
                    if token != self._token:
                        return
                    if progress >= 1.0:
                        break
                    blend = BLENDS[self.running]()
                    if not steps or not blend.same(before, progress):
                        w, h, pixels = self._still
                        part = blend.step(w, h, pixels, progress)
                        if part is None:
                            break
                        if self.api.player.ipc.request("get_property", "pid") != self._pid:
                            raise PlayerError("the player was restarted")
                        self.api.player.overlay(OVERLAY_ID, *part)
                        steps, before = steps + 1, progress
                # until the next step's moment by the clock (not a step's length after this one: a step costs time)
                wait = began + (int((self._clock() - began) * STEPS + 1e-6) + 1) / STEPS - self._clock()
                self._sleep(max(wait, 0.002))       # always some: the lock must come free for whoever ends this
            after = self._dropped()
            lost = after - dropped if dropped is not None and after is not None else 0
            with self._lock:
                if token != self._token:
                    return
                self.last.update(steps=steps, seconds=round(self._clock() - began, 3), dropped=lost, ended="done")
                self._drop()
            if seconds >= 0.5 and steps < MIN_RATE * seconds:
                self._give_up("%d steps in %.1f seconds" % (steps, seconds))
            elif lost > MAX_DROPS * max(seconds, 1.0):
                self._give_up("the clip dropped %d frames in %.1f seconds" % (lost, seconds))
        except Exception as e:                  # a player that went away has lost the still with everything else
            with self._lock:
                if token == self._token:
                    self.last["ended"] = "the player: %s" % e
                    self._drop()

    def tidy(self):
        """When the panel starts: a still an earlier panel process left on the player comes off (nobody would take
        it away), and what that process left in the runtime folder goes (D70)."""
        player = self.api.player
        try:
            player.overlay_remove(OVERLAY_ID)
        except Exception:
            pass
        rundir = getattr(player, "rundir", None)
        if rundir:
            paths.remove_leftovers(rundir, STILL_NAME)
            paths.remove_leftovers(rundir, re.escape("overlay-%d.bgra" % OVERLAY_ID))
