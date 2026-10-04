# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Vibes: shaders played one after another, endlessly, for ambience.

One action starts it. It picks from the shaders that are switched on for Vibes (bundled and uploaded), in a shuffled
order, shows each for the dwell time, then dips to black and shows the next. Each round the number inputs get new
values between their MIN and MAX (pulled towards the default, which the author chose as the good place), the palette
is turned by some degrees, and time starts somewhere else, so a round never looks quite like the one before.

It never fights the operator (the same idea as autostart, D18): the player counts an "epoch" that goes up whenever
anything is played or stopped. Vibes remembers the epoch of its own last shader; when the player's epoch is another
one, or the player is no longer playing the carrier, something else has the screen and Vibes simply ends. The same
check is made inside the player, under its lock, at the moment of each change, so a rotation that was just about to
change can never land on top of a clip someone started.
"""

import random
import threading
import time

from .api import ApiError

HUE_RANGE = 120.0           # degrees either way that a round may turn the palette
SPREAD = 0.6                # how far a varied value may go from the default towards MIN or MAX (1 is all the way)
OFFSET_MAX = 600.0          # seconds: where in its own time a round may start
TICK = 1.0


def vary(inputs, rng):
    """{name: value} for the float inputs: a value between MIN and MAX, pulled towards the default."""
    out = {}
    for i in inputs:
        if i["type"] == "float" and i["max"] > i["min"]:
            pick = rng.uniform(i["min"], i["max"])
            out[i["name"]] = round(i["default"] + (pick - i["default"]) * SPREAD, 4)
    return out


class Vibes:
    def __init__(self, api, engine, clock=time.monotonic, sleep=time.sleep, rng=None, thread=True, log=print):
        self.api, self.engine, self.log = api, engine, log
        self._clock, self._sleep = clock, sleep
        self._rng = rng or random.Random(random.SystemRandom().getrandbits(64))
        self._use_thread = thread
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self.running = False
        self.epoch = None           # the player's epoch after our last shader (or, before the first, when we started)
        self.started = False        # True once the first shader is on
        self.order = []
        self.current = None
        self.due = 0.0
        self.rounds = 0
        self.refused = set()        # shaders the GPU refused in this run: not tried again
        self.last = None            # {"at", "message"}: why it ended, or what went wrong

    # -- state --
    def status(self):
        left = max(0, int(round(self.due - self._clock()))) if self.running and self.started else None
        return {"running": self.running, "current": self.current, "next_in": left, "rounds": self.rounds, "last": self.last}

    def _note(self, message):
        self.last = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}
        self.log("pvj-web: vibes: %s" % message)

    def _end(self, message):
        self.running = False
        self.current = None
        self._note(message)

    # -- start and stop --
    def start(self):
        """Begin the rotation. Returns at once; the first shader goes on at the next tick (within a second), so a cue
        from OSC or the schedule never waits for the GPU to take a shader."""
        if not self.engine.enabled():
            raise ApiError(409, "turn on the Shaders and Vibes module in System first")
        with self._lock:
            if not self.engine.vibes_ids():
                raise ApiError(409, "no shader is switched on for Vibes")
            self.running, self.started = True, False
            self.epoch = self.api.player.source_epoch
            self.order, self.current, self.rounds, self.refused = [], None, 0, set()
            self.due = self._clock()
            self.last = None
        self._kick()
        return self.status()

    def stop(self):
        """End the rotation and, if it still has the screen, stop the picture (like the Stop button)."""
        with self._lock:
            had_screen = self.running and self.started and self._ours()
            if self.running:
                self._end("stopped")
            if had_screen:
                self.engine.off()
        return self.status()

    def yield_screen(self):
        """The operator is about to show one shader by hand: the rotation ends, the screen is left alone."""
        with self._lock:
            if self.running:
                self._end("ended: a shader was chosen by hand")

    def skip(self):
        """Go to the next shader now."""
        with self._lock:
            if not self.running:
                raise ApiError(409, "Vibes is not running")
            self.due = self._clock()
        self._kick()
        return self.status()

    def _kick(self):
        if not self._use_thread:
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="vibes", daemon=True)
                self._thread.start()
        self._wake.set()

    def _loop(self):
        while True:
            with self._lock:
                if not self.running:
                    self._thread = None
                    return
            try:
                self.tick()
            except Exception as e:          # never let one bad round end the thread silently
                with self._lock:
                    if self.running:
                        self._end("error: %s" % e)
            self._wake.wait(TICK)
            self._wake.clear()

    # -- the rotation --
    def _ours(self):
        """True while the screen is still ours: nothing else was played or stopped since our last shader."""
        if self.api.player.source_epoch != self.epoch:
            return False
        return not self.started or self.engine.on_screen() is not None

    def _next_id(self):
        """The next shader of the shuffled order; a new shuffle when it runs out (never the same one twice in a row
        when there is another)."""
        usable = [s for s in self.engine.vibes_ids() if s not in self.refused]
        if not usable:
            return None
        self.order = [s for s in self.order if s in usable]
        if not self.order:
            self.order = list(usable)
            self._rng.shuffle(self.order)
            if len(self.order) > 1 and self.order[0] == self.current:
                self.order.append(self.order.pop(0))
        return self.order.pop(0)

    def tick(self):
        """Call about once a second. Returns True if a shader was put on."""
        with self._lock:
            if not self.running:
                return False
            if not self.engine.enabled():
                self._end("ended: the module was switched off")
                return False
            if not self._ours():
                self._end("ended: something else was played or stopped")
                return False
            if self._clock() < self.due:
                return False
            return self._change()

    def _change(self):
        api = self.api
        half = api.settings.data["mix"]["duration"] / 2.0
        try:
            busy = self.started or bool(api.player.status().get("path"))
        except Exception:
            busy = self.started
        dip = busy and not api.mix["blackout"]
        if dip:                                     # down to black with the panel's own fade, then wait for it
            api.fader.ramp(api.mix["opacity"], 0, half)
            self._sleep(half)
            if not self._ours():
                self._end("ended: something else was played or stopped")
                return False
        cfg = self.engine.config()
        shown = False
        while not shown:
            sid = self._next_id()
            if sid is None:
                self._end("ended: the player refused every shader" if self.refused else "ended: no shader is switched on for Vibes")
                if self.started:
                    self.engine.off(self.epoch)
                self._undip(dip)
                return False
            values, hue, offset = {}, 0.0, 0.0
            if cfg["vary"]:
                inputs = next((s["inputs"] for s in self.engine.library() if s["id"] == sid), [])
                values = vary(inputs, self._rng)
                hue = round(self._rng.uniform(-HUE_RANGE, HUE_RANGE), 1)
                offset = round(self._rng.uniform(0.0, OFFSET_MAX), 1)
            try:
                result = self.engine.show(sid, values, hue, offset, epoch=self.epoch, cut=False)
            except ApiError as e:
                if e.status == 503:                 # the player is down: nothing to rotate on
                    self._end("ended: %s" % e.message)
                    self._undip(dip)
                    return False
                self.refused.add(sid)               # a file that no longer translates: leave it out
                self._note("%s left out: %s" % (sid, e.message))
                continue
            if result is None:
                self._end("ended: something else was played or stopped")
                return False
            self.epoch = result["epoch"]
            self.started = True
            if not result["ok"]:
                self.refused.add(sid)
                self._note("%s left out: the player refused it (%s)" % (sid, result["error"]))
                continue
            shown = True
            self.current = sid
        self.rounds += 1
        self.due = self._clock() + cfg["dwell"]
        if not api.mix["blackout"]:
            if dip:
                api._apply_opacity(0)
                api.fader.ramp(0, api.mix["opacity"], half)
            else:
                api.fader.cancel()
                api._apply_opacity(api.mix["opacity"])
        return True

    def _undip(self, dip):
        """The rotation ended in the middle of its own dip to black: put the picture's opacity back."""
        if dip and not self.api.mix["blackout"]:
            self.api.fader.cancel()
            self.api._apply_opacity(self.api.mix["opacity"])

    # -- requests --
    def api_vibes(self, body, device, client):
        """{"on": true} starts the rotation, {"on": false} stops it, {"next": true} goes to the next shader."""
        if body.get("next") is True:
            return self.skip()
        on = body.get("on")
        if not isinstance(on, bool):
            raise ApiError(400, "on must be true or false")
        return self.start() if on else self.stop()
