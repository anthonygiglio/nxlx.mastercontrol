# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box clock (D116): one tempo and one beat position for the whole box.

pvj-sound hears single onsets and a rough tempo; nothing ran between beats. This clock is a flywheel: once it has a
tempo it keeps the beat position by itself, so a gap in what feeds it (a tap that stopped, a program that went quiet, a
band that stopped playing) does not stop the beat.

Pure and standard-library only. Time comes in through the `now` function given to Clock (time.monotonic on the box), as
in pvj/transitions.py, so a test needs no sleeping. It has no thread: the panel's once-a-second tick and whoever asks
for a value move it on. All methods take an optional `now`; the ones OSC and pvj-sound feed never raise on odd input.

Sources, most trusted first (SOURCES):

    link    nothing feeds it yet: a later change adds Ableton Link through `hear("link", bpm, now)`
    tap     a person's hand: taps, or a tempo typed in
    osc     /nxlx/sound/in/beat and /bpm (pvj/soundout.py)
    sound   pvj-sound's tempo and beats, only at a confidence of SOUND_MIN_CONFIDENCE or more

Each source's last word stays in charge for its own time (HOLD). A machine (osc, link, sound) that has gone quiet hands
over to the next fresh one. The hand wins: a tap or a tempo set by number holds until `clear`, and sound never takes over
from it (the status still shows how old the last tap is). When nobody is fresh the clock keeps the last good tempo running
and says which source steered it last and how old its word is ("held").

What reaches the consumers (pvj/soundout.py) is the clock under tap or OSC only. With sound alone nothing is changed:
the beat is the service's own, so when the music stops the beat stops, as it always did. The clock follows sound
internally so that it can hand over, but it is not what the pulse, the meter or OSC out are given in that case.
"""

import math
import threading
import time

BAR_BEATS = 4                       # beats in a bar
BPM_MIN, BPM_MAX = 30.0, 300.0      # a tempo outside this is refused
TAP_MIN, TAP_MAX = 2, 8             # taps averaged: at least two, the last eight
TAP_PAUSE = 2.0                     # a pause longer than this starts a new count
SOUND_MIN_CONFIDENCE = 0.5          # pvj-sound is believed at this confidence or more
SOUND_CAPTURE = 0.3                 # a sound beat further than this from the grid (in beats) is a stray hit: ignored
SOUND_GAIN = 0.15                   # a nearer one moves the phase by this share of its distance (0.045 beats at most)
NUDGE_MAX = 0.5                     # the largest phase nudge by hand, in beats
PULSE_SECONDS = 0.15                # the pulse is 1 at a beat and falls to 0 in this long (as pvj-sound's)
SOURCES = ("link", "tap", "osc", "sound")
FOLLOWED = ("link", "tap", "osc")    # the sources whose clock the beat pulse follows; sound is followed as it always was
HOLD = {"link": 5.0, "tap": math.inf, "osc": 5.0, "sound": 3.0}     # seconds a word stays in charge; the hand holds until clear
OSC_BPM_HOLD = 5.0                  # an OSC /bpm this young is not overruled by the tempo of OSC beats
COUNT_WRAP = 2 ** 31                # the beat count sent over OSC is an int32


class ClockError(ValueError):
    """A request refused (a tempo or a step outside the limits). The message is plain words."""


def number(x):
    """x as a finite float, or None for anything else (a bool, text, nan, infinity)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        x = float(x)
    except (OverflowError, ValueError):
        return None
    return x if math.isfinite(x) else None


def in_range(bpm):
    return bpm is not None and BPM_MIN <= bpm <= BPM_MAX


def series(taps, t):
    """Taps (or OSC beats) with one more at t: (the new list, the average tempo or None, whether t was refused). A pause
    over TAP_PAUSE, or a time that went backwards, starts a new count; at most TAP_MAX are kept; a tempo outside
    BPM_MIN to BPM_MAX leaves t out."""
    if taps and (t - taps[-1] > TAP_PAUSE or t < taps[-1]):
        taps = []
    taps = (taps + [t])[-TAP_MAX:]
    if len(taps) < TAP_MIN:
        return taps, None, False
    span = taps[-1] - taps[0]
    bpm = 60.0 * (len(taps) - 1) / span if span > 0 else None
    if not in_range(bpm):
        return taps[:-1], None, True
    return taps, bpm, False


class Clock:
    def __init__(self, now=time.monotonic):
        self._now = now
        self.lock = threading.Lock()
        self._reset()

    def _reset(self):
        self._bpm = None            # None: no tempo yet (state "none")
        self._t0, self._b0 = 0.0, 0.0       # the anchor: at time _t0 the position was _b0 beats
        self._seen = None           # the latest time looked at (a clock that goes backwards is noticed)
        self._words = {s: None for s in SOURCES}        # source -> {"at", "bpm"}
        self._steer = None          # the source in charge now, or None
        self._last = None           # the source that steered last
        self._taps, self._osc_taps = [], []
        self._osc_bpm_at = None
        self._sound_beats = None
        self._sound_hits = 0

    # -- the flywheel (called with the lock held) --
    def _at(self, now):
        n = number(self._now() if now is None else now)
        return n if n is not None else (self._seen if self._seen is not None else 0.0)

    def _pos(self, t):
        return self._b0 + (t - self._t0) * self._bpm / 60.0

    def _update(self, now):
        """Look at the time and the sources. A clock that went backwards is re-anchored (the position does not step
        back). The source in charge is the first of SOURCES whose word is still young; the tempo is its tempo."""
        t = self._at(now)
        if self._seen is not None and t < self._seen:
            if self._bpm is not None:
                self._b0, self._t0 = self._pos(self._seen), t
            for w in self._words.values():
                if w is not None and w["at"] > t:
                    w["at"] = t
        self._seen = t
        steer = next((s for s in SOURCES if self._words[s] is not None and 0.0 <= t - self._words[s]["at"] <= HOLD[s]), None)
        self._steer = steer
        if steer is not None:
            self._last = steer
            bpm = self._words[steer]["bpm"]
            if bpm != self._bpm:
                if self._bpm is not None:
                    self._b0 = self._pos(t)
                self._t0, self._bpm = t, bpm
        return t

    def _word(self, source, bpm, t):
        """A source speaks at t. The first tempo of all starts the position at 0 (the origin)."""
        if bpm is None:
            bpm = self._bpm
        if bpm is None:
            return
        if self._bpm is None:
            self._bpm, self._t0, self._b0 = bpm, t, 0.0
        self._words[source] = {"at": t, "bpm": bpm}
        self._update(t)

    def _snap(self, source, t):
        """The phase is set at once: the position at t becomes the nearest whole beat. Only for the source in charge."""
        if self._steer == source and self._bpm is not None:
            self._b0, self._t0 = float(round(self._pos(t))), t

    # -- the sources --
    def hear(self, source, bpm, now=None):
        """A source's word: its tempo. This is how the Link slot will be fed. False if refused."""
        with self.lock:
            b = number(bpm)
            if source not in SOURCES or not in_range(b):
                return False
            self._word(source, b, self._at(now))
            return True

    def tap(self, now=None):
        """One tap. The phase is set to the nearest beat at once; from the second tap on (taps within TAP_PAUSE of each
        other, the last TAP_MAX of them) the tempo is their average. Returns {"taps", "bpm", "steers"}. A tempo
        outside BPM_MIN to BPM_MAX raises ClockError and that tap is not counted."""
        with self.lock:
            t = self._update(now)
            taps, bpm, refused = series(self._taps, t)
            if refused:
                raise ClockError("that tap makes a tempo outside %d to %d BPM and was not counted" % (BPM_MIN, BPM_MAX))
            self._taps = taps
            known = self._words["tap"]["bpm"] if self._words["tap"] else None
            self._word("tap", bpm if bpm is not None else known, t)
            self._snap("tap", t)
            return {"taps": len(taps), "bpm": None if self._bpm is None else round(self._bpm, 2), "steers": self._steer == "tap"}

    def set_bpm(self, bpm, now=None):
        """A tempo by number, as a person's word (source "tap"). ClockError outside BPM_MIN to BPM_MAX."""
        with self.lock:
            b = number(bpm)
            if not in_range(b):
                raise ClockError("the tempo must be a number from %d to %d BPM" % (BPM_MIN, BPM_MAX))
            self._taps = []
            self._word("tap", b, self._at(now))
            return {"bpm": round(self._bpm, 2), "steers": self._steer == "tap"}

    def nudge(self, step, now=None):
        """The phase moved by `step` beats (-NUDGE_MAX to NUDGE_MAX): positive is later in the bar."""
        with self.lock:
            s = number(step)
            if s is None or abs(s) > NUDGE_MAX:
                raise ClockError("a phase nudge is a number of beats from -%g to %g" % (NUDGE_MAX, NUDGE_MAX))
            t = self._update(now)
            if self._bpm is None:
                raise ClockError("there is no tempo to nudge yet")
            self._b0 += s
            return {"beats": round(self._pos(t), 3)}

    def clear(self, now=None):
        """Forget everything: no tempo, no taps, no source. The clock is "none" until a source speaks again."""
        with self.lock:
            self._reset()

    def osc_bpm(self, bpm, now=None):
        """/nxlx/sound/in/bpm. False, and no change, for a value outside BPM_MIN to BPM_MAX or not a number."""
        with self.lock:
            b = number(bpm)
            if not in_range(b):
                return False
            t = self._at(now)
            self._osc_bpm_at = t
            self._word("osc", b, t)
            return True

    def osc_beat(self, now=None):
        """/nxlx/sound/in/beat: the phase is set at once when OSC is in charge. Without a young OSC /bpm the beats are
        averaged like taps, so beats alone give a tempo."""
        with self.lock:
            t = self._update(now)
            self._osc_taps, bpm, _refused = series(self._osc_taps, t)
            young = self._osc_bpm_at is not None and 0.0 <= t - self._osc_bpm_at <= OSC_BPM_HOLD
            known = self._words["osc"]["bpm"] if self._words["osc"] else None
            self._word("osc", known if (young or bpm is None) else bpm, t)
            self._snap("osc", t)
            return True

    def sound(self, bpm, confidence, beats, at=None, now=None):
        """What pvj-sound says: its tempo, confidence and beat count, and when the block was heard (`at`). Believed only
        at SOUND_MIN_CONFIDENCE or more, and then for the tempo and a gentle push of the phase when a new beat came: a
        hit further than SOUND_CAPTURE beats from the grid is a stray and changes nothing. A tap or an OSC word is never
        overruled. Returns True if the word was taken."""
        with self.lock:
            b, c = number(bpm), number(confidence)
            t = self._at(now)
            hit = False
            if isinstance(beats, int) and not isinstance(beats, bool):
                hit = self._sound_beats is not None and beats > self._sound_beats
                self._sound_beats = beats
            if c is None or c < SOUND_MIN_CONFIDENCE or not in_range(b):
                return False
            self._word("sound", b, t)
            if hit and self._steer == "sound":
                h = number(at)
                if h is None or h > t or t - h > 0.5:
                    h = t
                off = self._pos(h) - round(self._pos(h))
                if abs(off) <= SOUND_CAPTURE:
                    self._b0 -= SOUND_GAIN * off
                    self._sound_hits += 1
            return True

    # -- the questions --
    def _pulse(self, p):
        return max(0.0, 1.0 - (p - math.floor(p)) * 60.0 / self._bpm / PULSE_SECONDS)

    def beats(self, at=None):
        """Beats since the origin (the first tempo) as a float; None with no tempo."""
        with self.lock:
            t = self._update(at)
            return None if self._bpm is None else self._pos(t)

    def bpm(self, at=None):
        with self.lock:
            self._update(at)
            return self._bpm

    def bar_position(self, at=None):
        """Where in the bar: 0 up to, not including, BAR_BEATS. None with no tempo."""
        b = self.beats(at)
        return None if b is None else b % BAR_BEATS

    def beat_count(self, at=None):
        """The whole beats since the origin, kept inside an int32 for OSC. 0 with no tempo."""
        b = self.beats(at)
        return 0 if b is None else max(0, int(math.floor(b))) % COUNT_WRAP

    def next_beat(self, at=None):
        """The time of the next beat after `at`, or None with no tempo."""
        with self.lock:
            t = self._update(at)
            if self._bpm is None:
                return None
            p = self._pos(t)
            return t + (math.floor(p) + 1.0 - p) * 60.0 / self._bpm

    def pulse(self, at=None):
        """1 at a beat, falling to 0 in PULSE_SECONDS; 0 with no tempo."""
        with self.lock:
            t = self._update(at)
            return 0.0 if self._bpm is None else self._pulse(self._pos(t))

    def reading(self, at=None):
        """For the consumers, in one look: None with no tempo, else {"source", "bpm", "pulse", "beat_count"}."""
        with self.lock:
            t = self._update(at)
            if self._bpm is None:
                return None
            p = self._pos(t)
            return {"source": self._steer or self._last, "bpm": self._bpm, "pulse": self._pulse(p),
                    "beat_count": max(0, int(math.floor(p))) % COUNT_WRAP}

    def source(self, at=None):
        """The source in charge, or the one that was last when all are quiet; None before any."""
        with self.lock:
            self._update(at)
            return self._steer or self._last

    def state(self, at=None):
        with self.lock:
            self._update(at)
            return "running" if self._bpm is not None else "none"

    def tick(self, now=None):
        """The panel's loop: hand over from a source gone quiet."""
        with self.lock:
            self._update(now)

    def status(self, now=None):
        """Everything the API shows, as plain numbers and words."""
        with self.lock:
            t = self._update(now)
            run = self._bpm is not None
            last = self._steer or self._last
            ages = {s: (None if w is None else round(max(0.0, t - w["at"]), 2)) for s, w in self._words.items()}
            p = self._pos(t) if run else 0.0
            return {"state": "running" if run else "none", "bpm": round(self._bpm, 2) if run else None,
                    "source": last, "steering": self._steer, "held": run and self._steer is None,
                    "age": ages[last] if last else None, "ages": ages,
                    "beats": round(p, 3) if run else None, "beat_count": max(0, int(math.floor(p))) % COUNT_WRAP if run else 0,
                    "bar_position": round(p % BAR_BEATS, 3) if run else None, "bar_beats": BAR_BEATS,
                    "next_beat_in": round((math.floor(p) + 1.0 - p) * 60.0 / self._bpm, 3) if run else None,
                    "pulse": round(self._pulse(p), 3) if run else 0.0, "taps": len(self._taps), "sound_hits": self._sound_hits,
                    "limits": {"bpm_min": BPM_MIN, "bpm_max": BPM_MAX, "tap_pause": TAP_PAUSE, "tap_max": TAP_MAX,
                               "sound_min_confidence": SOUND_MIN_CONFIDENCE, "nudge_max": NUDGE_MAX, "hold": {s: (None if math.isinf(h) else h) for s, h in HOLD.items()}}}
