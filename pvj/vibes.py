# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Vibes: shaders played one after another, endlessly, for ambience.

One action starts it. It picks from the shaders that are switched on for Vibes (bundled and uploaded), in a shuffled
order, shows each for the dwell time, then dips to black and shows the next. Each round the number inputs get new
values between their MIN and MAX (pulled towards the default, which the author chose as the good place), the palette
is turned by some degrees, and time starts somewhere else, so a round never looks quite like the one before.

It never fights the operator (the same idea as autostart, D18): the player counts an "epoch" that goes up whenever
anything is played or stopped, and already when a clip is accepted that will load after its own dip. Vibes remembers
the epoch of its own last shader; when the player's epoch is another one, or the player is no longer playing the
carrier, something else has the screen and Vibes simply ends. The same check is made inside the player, under its
lock, at the moment of each change and of each step of a fade, so a rotation that was just about to change can never
land on top of a clip someone started, nor darken it.

Two rules that came out of the review of the first version:
* Vibes fades with its own steps, never with the panel's Fader. The Fader keeps a play that waits for its dip as a
  callback, and any other use of the Fader drops that callback: the operator's clip was accepted and never played.
* start, stop, skip and the dwell time only set a few fields under a short lock and return. The slow work (the dip,
  the wait for the GPU to take a shader) runs under a second lock that nothing else waits for, so an OSC cue, a MIDI
  button, a DMX channel or the schedule is never held up by a change that is in progress.
"""

import random
import threading
import time

from . import locks
from .api import ApiError
from .player import PlayerError

HUE_RANGE = 120.0           # degrees either way that a round may turn the palette
SPREAD = 0.6                # how far a varied value may go from the default towards MIN or MAX (1 is all the way)
OFFSET_MAX = 600.0          # seconds: where in its own time a round may start
TICK = 1.0
FADE_STEPS_PER_SECOND = 20


def vary(inputs, rng, centre=None):
    """{name: value} for the float inputs: a value between MIN and MAX, pulled towards the default (or towards the
    value in `centre`, a preset's). An input marked "varies": False is left alone: its value changes how much the
    GPU has to do, and a round must not be heavier than the shader was when it was tried."""
    out = {}
    for i in inputs:
        if i["type"] == "float" and i["max"] > i["min"] and i.get("varies", True):
            middle = (centre or {}).get(i["name"], i["default"])
            pick = rng.uniform(i["min"], i["max"])
            out[i["name"]] = round(middle + (pick - middle) * SPREAD, 4)
    return out


class Vibes:
    def __init__(self, api, engine, clock=time.monotonic, sleep=time.sleep, rng=None, thread=True, log=print):
        self.api, self.engine, self.log = api, engine, log
        self._clock, self._sleep = clock, sleep
        self._rng = rng or random.Random(random.SystemRandom().getrandbits(64))
        self._use_thread = thread
        self._state = locks.make("vibes.state")    # the fields below; held for moments only, never across a call to the player
        self._work = locks.make("vibes.work")     # one change at a time; nobody waits for it (tick gives up if it is taken)
        self._wake = threading.Event()
        self._thread = None
        self._clear = None          # the epoch of a screen that stop() wants cleared as soon as no change is in progress
        self._dipped = False        # True while our own dip has the picture down (so an early end can put it back)
        self._yielded = False       # ended by a shader chosen by hand or by a pad: something is about to take the screen
        self.running = False
        self.epoch = None           # the player's epoch after our last shader (or, before the first, when we started)
        self.started = False        # True once the first shader is on
        self.order = []
        self.current = None
        self.due = 0.0
        self.shown_at = 0.0         # when the shader now on screen came up
        self.rounds = 0
        self.refused = set()        # shaders that could not be shown in this run (the engine remembers what the GPU refused)
        self.last = None            # {"at", "message"}: why it ended, or what went wrong
        self.set_id = None          # the set this run rotates through; None follows the active set
        self.history = []           # the shaders this run has shown, the newest last (for "the one before")
        self._want = None           # the shader to show next whatever the order says
        self._tight = set()         # shaders seen dropping a few frames in this run: shown without the palette turn
        self._asked = False         # Next or Previous was pressed: the coming change is wanted, whatever the set holds
        self._marked = []           # shaders the guard marked heavy one after another, with no healthy one between

    # -- state --
    def status(self):
        left = max(0, int(round(self.due - self._clock()))) if self.running and self.started else None
        out = {"running": self.running, "current": self.current, "next_in": left, "rounds": self.rounds, "last": self.last}
        if self.running:
            try:
                e = self.engine.rotation(self.set_id)
                out["set"] = {"id": e["id"], "name": e["name"]}
            except Exception:
                out["set"] = None
        return out

    def _note(self, message):
        self.last = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}
        self.log("pvj-web: vibes: %s" % message)

    def _end(self, message):
        """End the rotation (call with the state lock held, or from the one working thread)."""
        self.running = False
        self.current = None
        self._note(message)

    def _finish(self, message):
        with self._state:
            if self.running:
                self._end(message)

    # -- start and stop: quick, they never wait for a change in progress --
    def start(self, set_id=None):
        """Begin the rotation through the active set, or through the set named (by id or name). Returns at once; the
        first shader goes on at the next tick (within a second), so a cue from OSC or the schedule never waits for
        the GPU to take a shader."""
        if not self.engine.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")
        if set_id is not None:
            set_id = self.engine.rotation(set_id)["id"]         # 404 for a set that is not there
        if not self.engine.vibes_ids(set_id):
            if getattr(self.engine, "rotation", None) and self.engine.rotation(set_id)["shaders"]:
                raise ApiError(409, "every shader of this set is left out on this box (refused by the GPU, or found too heavy): "
                                    "put one back on the Shaders page")
            raise ApiError(409, "no shader is switched on for Vibes")
        with self._state:
            # Started again while it still has the screen (it was running, or a stop is not carried out yet): it keeps
            # the screen it has, so a later stop still clears it.
            carry = self.started and (self.running or self._clear is not None)
            self.running, self.started, self._yielded = True, carry, False
            if not carry:
                self.epoch = self.api.player.source_epoch
            self.order, self.current, self.rounds, self.refused = [], None, 0, set()
            self.set_id, self.history, self._want, self._tight, self._marked = set_id, [], None, set(), []
        changer = getattr(self.engine, "changer", None)
        if changer:
            # a step, a preset or a pad's shader that was still waiting must not take the screen from this run: the
            # rotation wins, and the journal says what was dropped
            drop = getattr(self.engine, "drop_waiting", None)
            drop("Vibes was started after it was asked for") if drop else changer.clear()
            self.due = self._clock()
            self.last = None
            self._clear = None
        self._kick()
        return self.status()

    def stop(self):
        """End the rotation and, if it still has the screen, stop the picture (like the Stop button). Returns at once:
        if a change is in progress, the screen is cleared when that change notices."""
        with self._state:
            if self.running:
                if self.started:
                    self._clear = self.epoch
                self._end("stopped")
        # The screen is cleared by the rotation's own thread when it has one (it ends at once and settles): clearing
        # takes the engine's lock, which is held while the GPU looks at a shader, and a Stop from a controller or the
        # schedule must not wait for that.
        if not (self._use_thread and self._thread is not None):
            self._settle()
        self._wake.set()
        return self.status()

    def yield_screen(self):
        """The operator is about to show one shader by hand: the rotation ends, the screen is left alone, also by a
        change of the rotation's own that is in progress: it puts nothing on after this (Engine.show's `alive`), and
        a shader of its own that had already taken the screen stays until the chosen one shows (_change)."""
        with self._state:
            if self.running:
                self._yielded = True
                self._end("ended: a shader was chosen by hand")

    def skip(self, direction=1):
        """Go to the next shader now, or (-1) back to the one before."""
        with self._state:
            if not self.running:
                raise ApiError(409, "Vibes is not running")
            if direction == -1 and len(self.history) >= 2:
                self._want = self.history[-2]
                del self.history[-2:]
            self.due = self._clock()
            self._asked = True                      # a person's Next or Previous: the shader comes on again, also in a set of one
        self._kick()
        return self.status()

    def set_dwell(self, seconds):
        """How long each shader stays, from a presenter's control (a knob or fader). Saved only when it changes, so
        a sweep does not write the settings file for every step; the shader now on screen follows the new time."""
        from . import shaders as shaders_mod
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != seconds
                or not shaders_mod.DWELL_MIN <= seconds <= shaders_mod.DWELL_MAX):
            raise ApiError(400, "each shader stays %d to %d seconds" % (shaders_mod.DWELL_MIN, shaders_mod.DWELL_MAX))
        if not self.engine.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")
        target = self.set_id if self.running else None      # the set that is running, else the active one
        if self.engine.rotation(target)["dwell"] != int(seconds):
            self.engine.tune_set(target, dwell=int(seconds))
        with self._state:
            if self.running and self.started:
                self.due = self.shown_at + int(seconds)
        self._kick()
        return dict(self.status(), dwell=int(seconds))

    def _settle(self):
        """Clear the screen a stop asked for, unless a change is in progress (which then does it when it is done)."""
        if self._clear is None or not self._work.acquire(blocking=False):
            return
        try:
            self._do_clear()
        finally:
            self._work.release()

    def _do_clear(self):
        with self._state:
            epoch, self._clear = self._clear, None
        if epoch is not None:
            self._off(epoch)                    # a pad tapped right after this Stop is not refused by its clear
            self._undip()

    def _off(self, epoch):
        """Clear our screen on the way out, noting the move for the engine's queue in the same step (Engine.adopt)."""
        try:
            return self.engine.off(epoch, adopt=True)
        except TypeError:                           # an engine that knows no queue
            return self.engine.off(epoch)

    def _kick(self):
        if not self._use_thread or not self.running:
            return
        with self._state:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="vibes", daemon=True)
                self._thread.start()
        self._wake.set()

    def _loop(self):
        while True:
            with self._state:
                if not self.running:
                    self._thread = None
                    break
            try:
                self.tick()
            except Exception as e:          # never let one bad round end the thread silently, or leave the screen dark
                self._finish("error: %s" % e)
                self._undip()
            self._wake.wait(TICK)
            self._wake.clear()
        self._settle()

    # -- the rotation --
    def _path(self):
        try:
            return self.api.player.status().get("path")
        except Exception:
            return None

    def _ours(self):
        """True while the screen is still ours: nothing else was played, accepted or stopped since our last shader,
        and (once a shader is on) the player still plays the carrier."""
        if self.api.player.source_epoch != self.epoch:
            return False
        return not self.started or self.engine.is_carrier(self._path())

    def _undip(self):
        """After an end in the middle of our own dip to black: put the picture's opacity back, but only on a screen
        that nobody else is fading (an idle player, or one still showing our carrier). A clip that took over sets its
        own opacity."""
        dipped, self._dipped = self._dipped, False
        if not dipped or self.api.mix["blackout"]:
            return
        # A clip that was tapped during our dip is taking its still of the screen as it is, half dark: the level is
        # that play's from here (it sets it when its clip loads, or puts it back if it never does). Brought back
        # up now, the frozen picture went bright, then the half-dark still was laid over it, then the blend ran.
        busy = getattr(getattr(self.api, "transitions", None), "busy", None)
        if busy is not None and busy():
            return
        path = self._path()
        if path is None or self.engine.is_carrier(path):
            levels = getattr(self.api, "_levels", None)
            with (levels() if levels else locks.make("fader.stepping")):
                if not self.api.mix["blackout"] and getattr(getattr(self.api, "fader", None), "label", None) != "out":
                    self.api._apply_opacity(self.api.mix["opacity"])

    def _level(self):
        """The opacity on the screen now (0 to 100): the mix value, unless the player says otherwise (a Fade out)."""
        now = getattr(self.api.player, "opacity_now", None)
        level = None
        if now:
            try:
                level = now()
            except Exception:
                level = None
        return self.api.mix["opacity"] if level is None else level

    def _fade(self, start, up, seconds):
        """Our own fade, in steps: down from `start` to black, or up from black to the mix opacity. Every step is set
        through the player only while the epoch is still ours. False when the screen was lost or Vibes was stopped.
        The steps are paced against the clock, so the fade takes `seconds` whatever the round trips to the player
        cost (measured on a Pi 4: sleeping a full step each time made a 1.0 s dip take 1.15 to 1.49 s)."""
        steps = max(1, int(seconds * FADE_STEPS_PER_SECOND))
        if not up:
            self._dipped = True
        began = self._clock()
        for i in range(1, steps + 1):
            wait = began + seconds * i / steps - self._clock()
            if wait > 0:
                self._sleep(wait)
            if not self.running:
                return False
            if self.api.mix["blackout"]:
                return True                         # the operator blacked out meanwhile: leave the screen dark
            level = self.api.mix["opacity"] * i / steps if up else start * (steps - i) / steps
            # One write of the level at a time (Api._levels), with the look at Blackout inside it: a step that was
            # on its way when the operator blacked out could land after the Blackout's own dark and show the picture.
            # The player's lock first, then the level's (the order of the locks, pvj/locks.py): `source_opacity` needs
            # the player's lock for its look at the epoch, and whoever holds the level's lock may not wait for the
            # player's, or a Blackout would wait behind a load and a Stop could wait for this for ever.
            levels, players = getattr(self.api, "_levels", None), getattr(self.api, "_player_lock", None)
            try:
                with (players() if players else locks.make("player")):
                    with (levels() if levels else locks.make("fader.stepping")):
                        # dark by the operator's wish (Blackout, or a Fade out, which the fader's label says): the
                        # rotation's own dip leaves the screen as it is
                        if self.api.mix["blackout"] or getattr(getattr(self.api, "fader", None), "label", None) == "out":
                            return True
                        if not self.api.player.source_opacity(int(round(min(100, max(0, level)) * 2.55)), self.epoch):
                            return False
            except PlayerError:
                return False
        if up:
            self._dipped = False
        return True

    def _next_id(self, rotation):
        """The next shader of the set: in a shuffled order (a new shuffle when it runs out, never the same one twice
        in a row when there is another), or in the set's own order. A step back names its shader itself."""
        usable = [e["id"] for e in rotation["shaders"] if e["id"] not in self.refused]
        if not usable:
            return None
        want, self._want = self._want, None
        if want in usable:
            return want
        if rotation["order"] == "listed":
            self.order = []
            at = usable.index(self.current) if self.current in usable else -1
            return usable[(at + 1) % len(usable)]
        self.order = [s for s in self.order if s in usable]
        if not self.order:
            self.order = list(usable)
            self._rng.shuffle(self.order)
            if len(self.order) > 1 and self.order[0] == self.current:
                self.order.append(self.order.pop(0))
        return self.order.pop(0)

    def tick(self):
        """Call about once a second. Returns True if a shader was put on. If a change is already in progress (another
        thread is in here), it returns at once."""
        if not self._work.acquire(blocking=False):
            return False
        try:
            return self._tick()
        finally:
            self._work.release()
            self._settle()                          # a stop that came while we worked

    def _tick(self):
        if not self.running:
            return False
        if not self.engine.enabled():
            self._finish("ended: the module was switched off")
            if self.started:
                self.engine.off(self.epoch)         # the shader does not stay on a screen whose module is off
            return False
        if not self._ours():
            self._finish("ended: something else was played or stopped")
            return False
        if self.started and self.current:
            verdict = self._guard()
            if verdict == "box":
                return False
            if verdict:
                return self._change()
        if self._clock() < self.due:
            return False
        return self._change()

    def _guard(self):
        """Ask the engine's guard about the shader that is on. True when it is too heavy for this box: it is noted
        (no rotation shows it again until someone puts it back) and the rotation moves on now. A shader that drops
        a few frames only is shown without the palette turn for the rest of this run."""
        watch = getattr(self.engine, "watch", None)
        seen = watch() if watch else None
        if not seen or not seen["state"]:
            return False
        if seen["state"] == "tight":
            self._tight.add(self.current)
            return False
        if seen["state"] != "heavy":
            self._marked = []                       # this one keeps up: whatever was marked before it was the shader's fault
            return False
        if len(self._marked) >= 2:
            # Three in a row, none of them healthy: it is not the shaders. The two marks are taken back, nothing more
            # is marked, and the rotation ends with the shader that is on left on the screen.
            self.engine.unmark(self._marked)
            self._marked = []
            self._finish("ended: the box is dropping frames whatever plays: check the picture detail")
            return "box"
        self._marked.append(self.current)
        self.engine.note_heavy(self.current, seen)
        self._note("%s left out: it dropped %s frames a second (too heavy on this box at this drawing size)" % (self.current, seen["drops_per_second"]))
        return True

    def _lost(self):
        """The screen went to someone else, or Vibes was stopped, in the middle of a change."""
        self._finish("ended: something else was played or stopped")
        if self._clear is not None:
            self._do_clear()                        # stopped in the middle: clear first, then the opacity goes back
        self._undip()
        return False

    def _stay(self):
        """The set has one shader that can play, and it is the one on the screen: there is nothing to change to, so it
        stays on. No dip to black, and the shader is not loaded again (on the Pi a set of one went dark for the Mix
        duration every dwell and came back as the same picture). With variation on, its numbers get new values through
        the engine's live change, which keeps TIME and does not flash; the palette turn and the start time stay as
        they are, and so does everything while variation is off (switching variation off shows at the next Start or
        Next). A person's Next or Previous still loads it again. Returns True when it stayed."""
        asked, self._asked = self._asked, False
        change = getattr(self.engine, "change", None)
        if change is None:                          # the first version's engine cannot change a shader that is on: it loads it again
            return False
        if asked or not (self.started and self.current) or self._want not in (None, self.current):
            return False
        try:
            rotation = self.engine.playable(self.set_id)
        except ApiError:
            return False                            # the set is gone: the usual path says so and ends
        if [e["id"] for e in rotation["shaders"] if e["id"] not in self.refused] != [self.current]:
            return False
        if rotation["vary"]:
            try:
                inputs = next((s["inputs"] for s in self.engine.library() if s["id"] == self.current), [])
                centre = self.engine.entry_values(self.current, rotation["shaders"][0].get("preset"))[0]
                values = vary(inputs, self._rng, centre)
                if values:
                    change({"id": self.current, "values": values})
            except ApiError:
                pass                                # it left the screen meanwhile, or the GPU refused these: it stays as it is
        with self._state:
            self._want = None
            self.rounds += 1
            self.shown_at = self._clock()
            self.due = self.shown_at + rotation["dwell"]
        return True

    def _change(self):
        if self._stay():
            return False
        api = self.api
        half = api.settings.data["mix"]["duration"] / 2.0
        began = self._clock()
        level = self._level()
        busy = self.started or bool(self._path())
        # A screen that is already dark (Blackout, or the operator's Fade out) stays dark: no dip, and no fade up
        # that would flash the picture back.
        dark = api.mix["blackout"] or level <= 0.5
        dip = busy and not dark
        if dip and (not self._fade(level, False, half) or not self._ours()):
            return self._lost()
        shown = False
        while not shown:
            try:
                rotation = self.engine.playable(self.set_id)
            except ApiError:                        # the set this run was started with has been deleted
                self._finish("ended: the set it was running has been deleted")
                if self.started:
                    self.engine.off(self.epoch)
                self._undip()
                return False
            sid = self._next_id(rotation)
            if sid is None:
                self._finish("ended: the player refused every shader" if self.refused else "ended: no shader is switched on for Vibes")
                if self.started:
                    self.engine.off(self.epoch)
                self._undip()
                return False
            values, hue, offset, controls, preset = {}, 0.0, 0.0, None, None
            try:
                wanted = next((e.get("preset") for e in rotation["shaders"] if e["id"] == sid), None)
                inputs = next((s["inputs"] for s in self.engine.library() if s["id"] == sid), [])
                try:
                    values, controls, preset = self.engine.entry_values(sid, wanted)
                except ApiError:                    # the set names a preset that is gone: the shader's own start
                    values, controls, preset = self.engine.entry_values(sid, None)
                if rotation["vary"]:
                    values = dict(values, **vary(inputs, self._rng, values))
                    # A palette turn is one more multiplication for every pixel. A shader that was seen dropping
                    # frames in this run goes without it (on a Pi 4 it was what pushed nxlx-pulse over the edge).
                    hue = 0.0 if sid in self._tight else round(self._rng.uniform(-HUE_RANGE, HUE_RANGE), 1)
                    offset = round(self._rng.uniform(0.0, OFFSET_MAX), 1)
                before = self.epoch
                result = self.engine.show(sid, values, hue, offset, epoch=before, cut=False, controls=controls, preset=preset,
                                          alive=lambda: self.running)
            except ApiError as e:
                if e.status == 503:                 # the player is down: nothing to rotate on
                    self._finish("ended: %s" % e.message)
                    self._undip()
                    return False
                self.refused.add(sid)               # a file that no longer translates: leave it out
                self._note("%s left out: %s" % (sid, e.message))
                continue
            if result is None:
                return self._lost()
            with self._state:
                stopped = not self.running          # stop() or a shader chosen by hand, while the GPU took this one
                if not stopped:
                    self.epoch, self.started = result["epoch"], True
            if stopped:
                # Ours, put on while we were being ended (the GPU was looking at it). Whoever ended us read the
                # player's epoch at that moment, before or after our taking of the screen: what we made of it since
                # is ours to answer for, so a wish that is queued for the engine's worker is not refused by it.
                # (Engine.show notes the move itself, in the same step, when we were ended before it.)
                if not self._yielded:
                    # a Stop: take it off again, and a pad tapped right after the Stop still shows
                    self._off(result["epoch"])
                # ended for a shader chosen by hand or by a pad: it is about to take the screen. Cleared here, the
                # screen went black and the chosen shader, queued by a controller, was refused: at worst our
                # picture stays until the chosen one shows.
                self._undip()
                return False
            if not result["ok"]:
                self.refused.add(sid)
                self._note("%s left out: the player refused it (%s)" % (sid, result["error"]))
                continue
            shown = True
        with self._state:
            self.current = sid
            self.history = (self.history + [sid])[-64:]
            self.rounds += 1
            self.shown_at = self._clock()
            self.due = self.shown_at + rotation["dwell"]
        if dip:
            # The whole dip takes the Mix duration: the way up gets what the way down and the change have left of it,
            # but never less than half its share (the first time the GPU takes a shader can take a second or more).
            if not self._fade(0, True, max(half / 2.0, began + 2 * half - self._clock())):
                self._undip()
        elif not dark:
            try:
                api.player.source_opacity(int(round(api.mix["opacity"] * 2.55)), self.epoch)
            except PlayerError:
                pass
        return True

    # -- requests --
    def api_vibes(self, body, device, client):
        """{"on": true, "set"?: id or name} starts the rotation (through the active set, or the one named),
        {"on": false} stops it, {"next": true} goes to the next shader, {"previous": true} back to the one before,
        {"dwell": seconds} sets how long each shader stays."""
        if body.get("next") is True:
            return self.skip()
        if body.get("previous") is True:
            return self.skip(-1)
        if "dwell" in body:
            return self.set_dwell(body["dwell"])
        on = body.get("on")
        if not isinstance(on, bool):
            raise ApiError(400, "on must be true or false")
        if on and body.get("set") is not None and not isinstance(body["set"], str):
            raise ApiError(400, "set must be the id or the name of a set")
        return self.start(body.get("set")) if on else self.stop()
