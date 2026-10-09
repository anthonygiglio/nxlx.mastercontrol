# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Transitions between clips that blend two pictures: the crossfade, the wipes and the slides (D71).

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
transition beside it in `style`, a key older code never reads. It stays in the file there, unread, until
somebody saves the transition or its duration or imports a settings file, which writes `mix` without it.

A wipe or a slide (class Move) is the same still, of which a smaller and smaller part is drawn: no byte of it is
changed, so a step costs the panel one command and the player a copy of the part that is still to be seen.
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
WIPES = ("wipe-from-left", "wipe-from-right", "wipe-from-top", "wipe-from-bottom")      # where the new picture comes in from
SLIDES = ("slide-left", "slide-right", "slide-up", "slide-down")                         # where the old picture leaves to
STYLES = ("crossfade",) + WIPES + SLIDES        # kept in mix.style, with mix.transition "dip" beside it for an older release
NAMES = OLD + STYLES
OVERLAY_ID = 63                 # the last one: nothing of the panel's is drawn over the still
MAX_PIXELS = 2560 * 1440        # of the screen; above it the box dips (a step would send more than 14 MB)
SLOW = 1.0                      # seconds the still may take (the screenshot and its conversion) before the box gives up on crossfades
MIN_RATE = 5.0                  # steps a second a transition must manage, or the box gives up on crossfades
MAX_DROPS = 5.0                 # frames a second the clip under it may drop meanwhile, or the box gives up on crossfades
STEPS = 20.0                    # steps a second asked for by a crossfade (the Fader's rate)
MOVES = 30.0                    # and by a wipe or a slide: a step of theirs rewrites nothing, and an edge that jumps shows
FIRST_FRAME = 10.0              # seconds to wait for the new clip's first frame under the still (a stream that does not come)
POLL = 0.03
LATE = 5.0                      # seconds after a command the player did not answer, when the still is taken off once more
STILL_NAME = r"transition-\d+\.(?:png|bgra)(?:\.tmp)?"


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


class Gone(Exception):
    """The player a still was given to is not there any more (it stopped, or it is another process)."""


def read_still(path, size):
    """The pixels of the player's screenshot at `path` as premultiplied BGRA, opaque: (width, height, bytearray).
    The file must be what Player.still writes: an 8-bit PNG of exactly `size` whose rows are plain bytes (no row
    filter), so that reading it is a few copies and no arithmetic in Python. Anything else is refused with a
    StillError, a file cut short anywhere too, and no more is unpacked than such a picture holds.

    Memory: the file, its unpacked rows and the result are never all alive at once (the file goes before the
    result is made, and the rows are copied straight into it through a view). Measured with tracemalloc: at most
    2.3 times the picture's own bytes, which is about 34 MB for a 2560 x 1440 screen (the picture is 14.7), where
    the first version held about five times the picture."""
    try:
        with open(path, "rb") as f:
            data = f.read(size[0] * size[1] * 4 + size[1] + (1 << 20))
    except OSError as e:
        raise StillError("the still could not be read: %s" % e)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise StillError("the still is not a PNG")
    view = memoryview(data)
    pos, w, h, bpp = 8, 0, 0, 0
    unpacker, got = zlib.decompressobj(), []
    try:
        while pos + 8 <= len(data):
            n, kind = struct.unpack(">I4s", view[pos:pos + 8])
            if kind == b"IHDR":
                w, h, depth, ctype = struct.unpack(">IIBB", view[pos + 8:pos + 18])
                bpp = {2: 3, 6: 4}.get(ctype, 0) if depth == 8 else 0
                if (w, h) != tuple(size) or not bpp:
                    raise StillError("the still is not an 8-bit picture of the screen's size")
            elif kind == b"IDAT":
                if not bpp:
                    raise StillError("the still has no header")
                room = (w * bpp + 1) * h + 1 - sum(len(x) for x in got)
                got.append(unpacker.decompress(view[pos + 8:pos + 8 + n], max(1, room)))
                if unpacker.unconsumed_tail:
                    raise StillError("the still holds more than a screen")
            pos += 12 + n
    except (struct.error, zlib.error) as e:
        raise StillError("the still is damaged: %s" % e)
    finally:
        view.release()
    del data
    if not bpp:
        raise StillError("the still has no header")
    stride = w * bpp + 1
    raw = got[0] if len(got) == 1 else b"".join(got)
    del got
    if len(raw) != stride * h or raw[0::stride].count(0) != h:
        raise StillError("the still's rows are not plain bytes")
    out = bytearray(w * h * 4)
    rows, pixels = memoryview(raw), memoryview(out)
    for y in range(h):
        row, a, b = rows[y * stride + 1:(y + 1) * stride], y * w * 4, (y + 1) * w * 4
        pixels[a:b:4], pixels[a + 1:b:4], pixels[a + 2:b:4] = row[2::bpp], row[1::bpp], row[0::bpp]
    pixels.release()
    rows.release()
    out[3::4] = b"\xff" * (w * h)
    return w, h, out


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
    """The whole still, more transparent at every step. Every step is the still's bytes again, scaled: `step` gives
    (x, y, width, height, pixels) for Player.overlay, or None for nothing."""
    rate = STEPS
    moves = False

    def step(self, width, height, pixels, progress):
        level = int(round(255 * (1.0 - progress)))
        return (0, 0, width, height, faded(pixels, level)) if level > 0 else None

    def same(self, a, b):
        """True when two moments of the transition draw the same thing (a step is left out then)."""
        return int(round(255 * (1.0 - a))) == int(round(255 * (1.0 - b)))


class Move:
    """A wipe or a slide: the still is not changed, a smaller and smaller part of it is drawn. The still's bytes are
    written to a file once; `step` gives (x, y, offset, width, height) for Player.overlay_part: draw `width` x
    `height` pixels of the file, beginning `offset` bytes into it, at x, y. The rows keep the still's own length in
    the file (the stride), which is how a part that begins in the middle of a row is read. Nothing is rewritten
    and nothing is worked out in Python; the player copies the part that is still to be seen.

    `cut` says which side of the still goes first and `shift` whether what is left moves there (a slide) or stays
    where it was (a wipe): ("left", False) is the wipe from the left, ("left", True) the slide off to the left."""
    rate = MOVES
    moves = True

    def __init__(self, cut, shift):
        self.cut, self.shift = cut, shift

    def gone(self, width, height, progress):
        """Columns or rows of the still that are gone at `progress`."""
        return int(round(max(0.0, min(1.0, progress)) * (width if self.cut in ("left", "right") else height)))

    def step(self, width, height, pixels, progress):
        n = self.gone(width, height, progress)
        if self.cut in ("left", "right"):
            if n >= width:
                return None
            first = self.cut == "left"                   # the still's left columns go: what is left begins n columns in
            x = (0 if self.shift else n) if first else (n if self.shift else 0)
            return (x, 0, n * 4 if first else 0, width - n, height)
        if n >= height:
            return None
        first = self.cut == "top"
        y = (0 if self.shift else n) if first else (n if self.shift else 0)
        return (0, y, n * width * 4 if first else 0, width, height - n)

    def same(self, a, b):
        return int(round(a * 4096)) == int(round(b * 4096))     # finer than any screen's pixels: the worker's rate decides


BLENDS = {"crossfade": Crossfade,
          "wipe-from-left": lambda: Move("left", False), "wipe-from-right": lambda: Move("right", False),
          "wipe-from-top": lambda: Move("top", False), "wipe-from-bottom": lambda: Move("bottom", False),
          "slide-left": lambda: Move("left", True), "slide-right": lambda: Move("right", True),
          "slide-up": lambda: Move("top", True), "slide-down": lambda: Move("bottom", True)}


class Transitions:
    """Three locks and three counters, and no more (if a change seems to need a fourth of either, look again for
    a way to make these carry it):

    | lock       | guards                                                          | who waits for it                    |
    | ---------- | --------------------------------------------------------------- | ----------------------------------- |
    | `_holding` | one still is taken at a time, from the freeze to the overlay     | only another play's still. Never end() |
    | `_io`      | one overlay command at a time, each after a look at the token    | a step, a still being laid, end() for its one removal |
    | `_mark`    | the counters and the fields beside them                         | nobody for long: never held over a question to the player |

    The order is `_holding`, then `_io`, then `_mark`. One lock that is not this class's comes before all three:
    the player's own (`Player._lock`), under which `Api.play` claims the screen with its ticket and later looks
    and loads as one step, and under which a generator takes the screen.

    | counter  | goes up at                                                    | who looks at it                                   |
    | -------- | ------------------------------------------------------------- | ------------------------------------------------- |
    | `_token` | every hold that begins and every end()                         | a hold, its steps and its worker: is this transition still the current one |
    | `_gen`   | every play of a clip (`claim`) and every other newer wish (`newer`, `end(newer=True)`) | a play before its still, before the still is laid and with its load: is this still the newest wish |
    | `_ends`  | every end()                                                   | a play that waited for its turn: was its blend ended meanwhile (the clip loads without it) |

    Beside them: `_held`, the token of the newest hold that began, and `_paused`, true while a hold has frozen the
    clip and nobody has thawed it; the hold named by `_held` owns that thaw."""

    def __init__(self, api, clock=time.monotonic, sleep=time.sleep, thread=True, log=None):
        self.api = api
        self._clock, self._sleep, self._thread = clock, sleep, thread
        self.log = log or (lambda line: None)
        self._holding = threading.Lock()
        self._io = threading.Lock()
        self._mark = threading.Lock()
        self._token = 0                     # goes up at every hold and every end: who holds an older one stops
        self._still = None                  # (width, height, pixels) while a transition holds or runs
        self._up = False                    # an overlay of ours may be on the player
        self._pid = None                    # the player the still was given to: a restarted one never had it
        self._held = 0                      # the token of the newest hold that began
        self._paused = False                # the clip is frozen by a hold, not by somebody: whoever holds `_held` owns the thaw
        self._gen = 0                       # the play generation: goes up at every play of any kind, Stop and quit (`claim`, `newer`)
        self._ends = 0                      # goes up at every end(): a play that waits its turn sees that its blend is off
        self.running = None                 # the name of the transition that holds or runs
        self.given_up = ""                  # why this box dips instead, until the transition is chosen again
        self._large = ""                    # the same for a screen that is too large, from the last look at it
        self.late = []                      # with no threads (tests): the removals `_once_more` would send later
        self.last = {}                      # what the last one did: {"name", "still_ms", "steps", "seconds", "dropped", "ended"}

    # -- what the box will do --
    def fallback(self):
        """Why a transition would not be done now, or "": the reason the box gave up, or a screen too large at the
        last look (`look`). Nothing is asked of the player: every status request comes through here."""
        return self.given_up or self._large

    def look(self):
        """Look at the screen's size (one question to the player), then `fallback`. For a play, not for a status."""
        try:
            size = self.api.player.osd_size()
        except Exception:
            size = None
        self._large = "the screen is larger than 2560 x 1440" if size and size[0] * size[1] > MAX_PIXELS else ""
        return self.fallback()

    def chosen_again(self):
        """Somebody saved the transition: the box tries again."""
        self.given_up = ""
        self.look()

    def current(self, token):
        with self._mark:
            return bool(token) and token == self._token

    # -- which wish is the newest --
    def claim(self):
        """A play of a clip begins: it is the newest wish from now on. Returns its ticket, to be taken by the caller
        before any thread is started and before any lock is waited for, and handed to `hold`, `newest` and
        `ended_since`."""
        with self._mark:
            self._gen += 1
            return (self._gen, self._ends)

    def newer(self):
        """Something else became the newest wish (another way of playing, a Stop, a generator, the player's quit):
        a play that is still on its way (taking its still, or waiting its turn to) loads nothing after this."""
        with self._mark:
            self._gen += 1

    def newest(self, ticket):
        with self._mark:
            return ticket[0] == self._gen

    def ended_since(self, ticket):
        """True if an end() came since the ticket was taken: Blackout, a fade, an opacity change. The play's clip
        still loads, without its blend, and it leaves the picture's brightness to whoever set it since."""
        with self._mark:
            return ticket[1] != self._ends

    # -- one transition --
    def hold(self, name, ticket=None, wanted=None):
        """Lay a still of the screen over everything, in place of a transition that still runs. Returns a token
        (never 0) if it is there: then the new clip can be started under it, and `run(token, ...)` must follow.
        Returns 0, with no still on the screen, the clip thawed if this froze it and nothing left behind, if there
        is no such blend, the box dips instead (`fallback`), nothing is loaded in the player, an access code is on
        the display (its pixels would go into the still), an `end()` came meanwhile, or the still failed. A still
        that failed or took the player too long makes the box give up (`given_up`): the next plays dip.

        `ticket` is the play's own (`claim`). A play whose turn comes after a newer wish, or after an end(), returns
        0 at once and has touched nothing: it did not freeze the clip, take a still or stop the transition that
        runs. So of many plays that queue here only the newest takes a still. `wanted` is asked just before the
        still is laid down: False (the screen went dark meanwhile) and it is not."""
        blend = BLENDS.get(name)
        player = self.api.player
        if blend is None or not hasattr(player, "still"):
            return 0
        with self._holding:
            with self._mark:
                if ticket is not None and (ticket[0] != self._gen or ticket[1] != self._ends):
                    return 0
                if self.fallback():             # looked at here, in turn: the still before this one may have given up
                    return 0
                self._token += 1
                token = self._held = self._token
                inherited = self._paused        # an older hold froze the clip and nobody has thawed it: it is this one's now
            began = self._clock()               # from here: the wait behind another play's still is not this still's time
            froze, laid = inherited, False
            path = os.path.join(player.rundir, "transition-%d.png" % os.getpid())
            try:
                if self.api.access_on_screen():
                    return self._let_go(token, froze)
                # A player that runs with nothing loaded (after Stop, at power-up: on a box the player always
                # runs) has no picture to take a still of. No word in the log: this is the ordinary first play.
                if player.ipc.request("get_property", "idle-active") is True:
                    return self._let_go(token, froze)
                size = player.osd_size()
                if not size or size[0] * size[1] > MAX_PIXELS:
                    raise StillError("no screen size")
                pid = player.ipc.request("get_property", "pid")
                froze = inherited or player.ipc.request("get_property", "pause") is not True
                player.pause(True)              # the outgoing picture stands still from the tap on (see the top)
                if froze:
                    with self._mark:
                        self._paused = True
                # The player writes into this file, which is ours: it is made here, group-writable as the units'
                # umask leaves every file of the panel, and removed here whatever happens.
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o660))
                player.still(path)
                w, h, pixels = read_still(path, size)
                # An end() does not wait for a still (Stop and Blackout act at once), so the still looks for one:
                # here, before it is laid down, and again under the overlay's lock, where end() takes it off.
                stale = ticket is not None and (not self.newest(ticket) or self.ended_since(ticket))
                if stale or not self.current(token) or self.api.access_on_screen() or (wanted is not None and not wanted()):
                    return self._let_go(token, froze)
                with self._io:
                    if not self.current(token):
                        return self._let_go(token, froze, locked=True)
                    laid = True
                    with self._mark:
                        self._up = True
                    self._lay(blend(), w, h, pixels)
                    with self._mark:
                        # An end() that came while the still was laid (it marks at once, and then waits for `_io`
                        # to take the overlay off): the still is not this hold's any more, and goes here.
                        if token != self._token:
                            raise StillError("ended while the still was laid")
                        self._still, self.running, self._pid = (w, h, pixels), name, pid
            except Exception as e:              # every kind: a frozen clip and a 500 are worse than any cause
                ended = not self.current(token)
                gone = isinstance(e, PlayerError) and ("not running" in str(e) or "ipc error" in str(e))
                self._let_go(token, froze, remove=laid)
                if not ended:
                    self.log("pvj-web: no %s, a cut instead: %s" % (name, e))
                    if not gone:                # a player that went away is not a box that is too slow
                        self._give_up("the still failed (%s)" % (str(e) or type(e).__name__)[:80])
                if "no reply" in str(e):
                    self._once_more()
                return 0
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            took = self._clock() - began
            self.last = {"name": name, "still_ms": int(round(took * 1000)), "steps": 0, "seconds": 0.0, "ended": ""}
            if took > SLOW:
                self._give_up("the still took %.1f seconds" % took)
            return token

    def _source(self):
        return os.path.join(self.api.player.rundir, "transition-%d.bgra" % os.getpid())

    def _lay(self, blend, w, h, pixels):
        """The whole still on the screen, as the blend's first moment (under `_io`). For a wipe or a slide the
        still's bytes go into a file first, once: every step then names a part of it. One more row of nothing
        follows the picture, because the player maps `offset` plus `height` whole rows, and a part that begins in
        the middle of a row would otherwise end past the file. The file comes by a rename: the player may still be
        reading the one before it (a second play during a transition)."""
        if blend.moves:
            path = self._source()
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(pixels)
                f.write(bytes(w * 4))
            os.replace(path + ".tmp", path)
        self._show(blend, w, h, pixels, 0.0)

    def _show(self, blend, w, h, pixels, progress, part=None):
        """Draw the blend at `progress` (under `_io`). False if it has nothing more to draw. `part` is what
        `blend.step` gave for this moment, where the caller has asked already: a crossfade's step scales the whole
        still, and that is done once."""
        part = blend.step(w, h, pixels, progress) if part is None else part
        if part is None:
            return False
        if blend.moves:
            x, y, offset, pw, ph = part
            self.api.player.overlay_part(OVERLAY_ID, self._source(), x, y, offset, pw, ph, w * 4)
        else:
            self.api.player.overlay(OVERLAY_ID, *part)
        return True

    def _let_go(self, token, froze, remove=False, locked=False):
        """A hold that began and does not come about. If it is still the current one, the still that was on the
        screen before it goes too (this hold took its place); if an end() or a newer hold came, the screen is
        theirs and only what this hold itself laid (`remove`) is taken off. The clip plays on if this hold froze
        it. Returns 0. `locked`: the caller holds `_io`."""
        with self._mark:
            mine = token == self._token
            if mine:
                self._still, self.running = None, None
            up = self._up and (mine or remove)
        if up:
            if locked:
                self._remove()
            else:
                with self._io:
                    if not self._newer_up(token):
                        self._remove()
        if froze:
            self._thaw()
        return 0

    def _thaw(self):
        with self._mark:
            self._paused = False
        try:
            self.api.player.pause(False)
        except Exception:
            pass

    def _newer_up(self, token):
        with self._mark:
            return self._still is not None and self._token != token

    def _remove(self):
        """Take the overlay off the player and forget it (under `_io`). Its file goes with it."""
        with self._mark:
            self._up = False
        try:
            self.api.player.overlay_remove(OVERLAY_ID)
        except Exception:
            pass
        for name in (self._source(), self._source() + ".tmp"):
            try:
                os.unlink(name)
            except OSError:
                pass

    def run(self, token, seconds):
        """The new clip has been started under the still that `hold` returned `token` for: wait for its first
        frame, then take the still away over `seconds`. Returns at once; the work is a thread's. Nothing happens
        if that hold is no longer the current one (another play or an end() came since): two plays close together
        must never both step."""
        with self._mark:
            if token and token == self._held:
                self._paused = False            # the new clip plays (a load unfreezes): nothing to thaw any more
            if not token or token != self._token or self._still is None:
                return
            name = self.running
        if self._thread:
            threading.Thread(target=self._work, args=(token, seconds, name), name="transition", daemon=True).start()
        else:
            self._work(token, seconds, name)

    def end(self, why="ended", newer=False):
        """Take the still off now. Called by everything the still would be wrong over: Blackout, a fade, a change of
        opacity, Stop, another way of playing. It never waits for a still that is being taken: it marks that hold
        as ended, which the hold sees before it lays anything down, and a play that waits its turn sees it when
        its turn comes. It waits for at most one step that is on its way to the player, and then sends one command.
        With `newer` (Stop, another way of playing, a generator, the player's quit) a play on its way also loads
        nothing after this (see `newer`). No command is sent when no still is up."""
        with self._mark:
            self._token += 1
            self._ends += 1
            if newer:
                self._gen += 1
            had = self._still is not None
            self._still, self.running = None, None
            if had:
                self.last["ended"] = why
            up = self._up
        if up:
            with self._io:
                if not self._newer_up(0):
                    self._remove()

    def abandon(self, token):
        """The clip of the play that holds `token` did not start after all (it failed, or a newer wish came): its
        still goes, if it is still the one on the screen, and the old clip plays on, if that hold froze it. A still
        that a newer play has laid since is not this play's to take off."""
        with self._mark:
            thaw = self._paused and bool(token) and token == self._held     # a newer hold has taken the freeze over
            mine = bool(token) and token == self._token and self._still is not None
            if mine:                            # not an end(): no token and no count moves, so a play that waits its
                self._still, self.running = None, None      # turn keeps its blend
                self.last["ended"] = "the new clip did not start"
            up = mine and self._up
        if up:
            with self._io:
                if not self._newer_up(token):
                    self._remove()
        if thaw:
            self._thaw()

    def _once_more(self):
        """A command the player did not answer in time may still be run by it later, after this side gave up and
        took the still off: the still would then be back with nobody to remove it. So the removal is sent once
        more, LATE seconds on, unless a newer still is up by then. (Not seen; it cannot be ruled out from here.)"""
        def again():
            with self._io:
                if not self._newer_up(0):
                    self._remove()
        if self._thread:
            timer = threading.Timer(LATE, again)
            timer.daemon = True
            timer.start()
        else:
            self.late.append(again)

    def _give_up(self, why):
        self.given_up = why + "; using the dip to black until the transition is chosen again"
        self.log("pvj-web: transitions given up: %s" % why)

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

    def _draw(self, token, progress):
        """One step of the transition `token` at `progress`, if it is still the current one. "drawn", "over" (the
        blend has nothing more to draw) or "ended". Raises PlayerError; Gone for a player that is another one."""
        with self._io:
            with self._mark:
                if token != self._token or self._still is None:
                    return "ended"
                (w, h, pixels), name, pid = self._still, self.running, self._pid
            blend = BLENDS[name]()
            part = blend.step(w, h, pixels, progress)       # once: for a crossfade this is the whole still, scaled
            if part is None:
                return "over"
            try:
                same = self.api.player.ipc.request("get_property", "pid") == pid
            except PlayerError as e:
                raise Gone(str(e))
            if not same:
                raise Gone("the player was restarted")
            self._show(blend, w, h, pixels, progress, part)
            return "drawn"

    def _work(self, token, seconds, name):
        deadline = self._clock() + FIRST_FRAME
        mine = False
        try:
            while self._clock() < deadline:
                if not self.current(token):
                    return
                try:
                    if self._first_frame():
                        break
                except PlayerError as e:
                    raise Gone(str(e))
                self._sleep(POLL)
            dropped = self._dropped()
            blend = BLENDS[name]()
            began, steps, before = self._clock(), 0, 0.0
            while True:
                progress = (self._clock() - began) / seconds if seconds > 0 else 1.0
                if progress >= 1.0:
                    break
                if not steps or not blend.same(before, progress):
                    did = self._draw(token, progress)
                    if did == "ended":
                        return
                    if did == "over":
                        break
                    steps, before = steps + 1, progress
                elif not self.current(token):
                    return
                # until the next step's moment by the clock (not a step's length after this one: a step costs time)
                wait = began + (int((self._clock() - began) * blend.rate + 1e-6) + 1) / blend.rate - self._clock()
                self._sleep(max(wait, 0.002))       # always some: whoever ends this must get the overlay's lock
            after = self._dropped()
            lost = after - dropped if dropped is not None and after is not None else 0
            with self._io:
                with self._mark:
                    if token != self._token:
                        return
                    self.last.update(steps=steps, seconds=round(self._clock() - began, 3), dropped=lost, ended="done")
                    self._still, self.running = None, None
                self._remove()
            if seconds >= 0.5 and steps < MIN_RATE * seconds:
                self._give_up("%d steps in %.1f seconds" % (steps, seconds))
            elif lost > MAX_DROPS * max(seconds, 1.0):
                self._give_up("the clip dropped %d frames in %.1f seconds" % (lost, seconds))
        except Exception as e:
            # A player that went away or was restarted has lost the still with everything else: nothing to learn.
            # Any other failure of a step (no reply in time, a file that cannot be written) and the box gives up.
            with self._io:
                with self._mark:
                    mine = token == self._token
                    if mine:
                        self.last["ended"] = "the player: %s" % e
                        self._still, self.running = None, None
                if mine:
                    self._remove()
            if mine and not isinstance(e, Gone) and "not running" not in str(e):
                self._give_up("a step failed (%s)" % (str(e) or type(e).__name__)[:80])
            if "no reply" in str(e):
                self._once_more()

    def tidy(self):
        """When the panel starts: a still an earlier panel process left on the player comes off (nobody would take
        it away), and what that process left in the runtime folder goes (D70). On a box one panel owns that folder;
        two panels started by hand in one folder on a desk would take each other's still away."""
        player = self.api.player
        try:
            player.overlay_remove(OVERLAY_ID)
        except Exception:
            pass
        rundir = getattr(player, "rundir", None)
        if rundir:
            paths.remove_leftovers(rundir, STILL_NAME)
            paths.remove_leftovers(rundir, re.escape("overlay-%d.bgra" % OVERLAY_ID))
