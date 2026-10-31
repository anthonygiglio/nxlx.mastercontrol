# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box clock (D116): the flywheel, the sources and their order, the trust rule for sound, taps, odd input, drift; then
the routes, the controller actions and the consumer (what follows the beat follows the clock). A fake clock throughout:
nothing sleeps. Nothing here has met a real tapper, a Max patch or a band."""
import math
import os
import shutil
import tempfile
import time
import unittest

from pvj import actions, clock as clk, dmx, midi, osc, sound, soundout
from pvj.clock import Clock, ClockError
from tests.test_roles import TUNNEL, RolesBase
from tests.test_sound import FakeApi, proc_dir, record
from tests.test_vibes_control import Recorder


class Fake:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def make(t=100.0):
    f = Fake(t)
    return Clock(now=f), f


class FlywheelTest(unittest.TestCase):
    def test_nothing_runs_until_there_is_a_tempo(self):
        c, f = make()
        self.assertEqual(c.state(), "none")
        self.assertIsNone(c.beats())
        self.assertIsNone(c.bpm())
        self.assertIsNone(c.bar_position())
        self.assertIsNone(c.next_beat())
        self.assertEqual((c.pulse(), c.beat_count(), c.source()), (0.0, 0, None))
        self.assertIsNone(c.reading())
        st = c.status()
        self.assertEqual((st["state"], st["bpm"], st["source"], st["held"], st["age"]), ("none", None, None, False, None))

    def test_a_tempo_by_number_runs_by_itself(self):
        c, f = make(100.0)
        c.set_bpm(120)
        self.assertEqual(c.state(), "running")
        f.t = 110.0
        self.assertAlmostEqual(c.beats(), 20.0, places=9)
        self.assertAlmostEqual(c.bar_position(), 0.0, places=9)
        f.t = 110.25
        self.assertAlmostEqual(c.beats(), 20.5, places=9)
        self.assertAlmostEqual(c.bar_position(), 0.5, places=9)
        self.assertAlmostEqual(c.next_beat(), 110.5, places=9)
        self.assertEqual(c.beat_count(), 20)
        self.assertAlmostEqual(c.beats(at=111.0), 22.0, places=9, msg="a question about another time")
        self.assertAlmostEqual(c.bar_position(at=111.5), 3.0, places=9)

    def test_the_pulse_is_one_at_a_beat_and_gone_in_150_ms(self):
        c, f = make(0.0)
        c.set_bpm(120)
        f.t = 10.0
        self.assertAlmostEqual(c.pulse(), 1.0, places=6)
        f.t = 10.075
        self.assertAlmostEqual(c.pulse(), 0.5, places=6)
        f.t = 10.15
        self.assertAlmostEqual(c.pulse(), 0.0, places=6)
        f.t = 10.4
        self.assertEqual(c.pulse(), 0.0)

    def test_it_keeps_the_beat_through_a_long_gap_in_its_input(self):
        c, f = make(0.0)
        for t in (0.0, 0.5, 1.0, 1.5):
            f.t = t
            c.tap()
        self.assertAlmostEqual(c.bpm(), 120.0, places=6)
        before = c.beats(at=1.5)
        f.t = 601.5                                         # ten minutes with nothing said
        st = c.status()
        self.assertEqual((st["state"], st["source"], st["held"]), ("running", "tap", True), "kept, and says whose it was")
        self.assertAlmostEqual(st["age"], 600.0, places=2)
        self.assertAlmostEqual(c.beats(), before + 1200.0, places=6)
        self.assertEqual(c.bpm(), 120.0)

    def test_a_clock_that_goes_backwards_never_steps_the_beat_back(self):
        c, f = make(100.0)
        c.set_bpm(120)
        f.t = 110.0
        self.assertAlmostEqual(c.beats(), 20.0, places=9)
        f.t = 105.0
        self.assertAlmostEqual(c.beats(), 20.0, places=9)
        f.t = 106.0
        self.assertAlmostEqual(c.beats(), 22.0, places=9)

    def test_long_run_drift_is_nil(self):
        c, f = make(0.0)
        c.set_bpm(123.4)
        for i in range(1, 86401):                           # a day, ticked every second like the panel's loop
            f.t = float(i)
            c.tick()
        self.assertAlmostEqual(c.beats(), 86400 * 123.4 / 60.0, delta=1e-6)
        self.assertEqual(c.state(), "running")
        g, h = make(0.0)
        for i in range(1, 3601):                            # an hour of a program that says its tempo every 2 s
            h.t = i * 1.0
            if i % 2 == 0:
                g.osc_bpm(97.3)
        self.assertAlmostEqual(g.beats(), (3600 - 2) * 97.3 / 60.0, delta=1e-6,
                               msg="the first word, at 2 s, is the origin; saying the same tempo again never gains or loses")

    def test_a_beat_count_stays_inside_an_int32(self):
        c, f = make(0.0)
        c.set_bpm(300)
        f.t = 1.0e7
        self.assertLess(c.beat_count(), 2 ** 31)
        self.assertGreaterEqual(c.beat_count(), 0)


class SourcesTest(unittest.TestCase):
    def test_priority_and_the_beat_does_not_jump_at_a_handover(self):
        c, f = make(0.0)
        self.assertTrue(c.sound(100.0, 0.9, 1))
        self.assertEqual((c.source(), c.bpm()), ("sound", 100.0))
        f.t = 1.0
        before = c.beats()
        c.osc_bpm(130.0)
        self.assertEqual((c.source(), c.bpm()), ("osc", 130.0))
        self.assertAlmostEqual(c.beats(), before, places=9, msg="the position carried on from where it was")
        self.assertAlmostEqual(c.beats(at=2.0), before + 130.0 / 60.0, places=9, msg="and goes on at the new tempo")
        f.t = 2.0
        c.set_bpm(90.0)
        self.assertEqual((c.source(), c.bpm()), ("tap", 90.0))
        f.t = 3.0
        self.assertTrue(c.hear("link", 140.0))
        self.assertEqual((c.source(), c.bpm()), ("link", 140.0), "the slot for Link is the most trusted")
        c.sound(100.0, 0.9, 2)
        c.osc_bpm(130.0)
        self.assertEqual((c.source(), c.bpm()), ("link", 140.0), "nothing below overrules it")

    def test_a_source_that_goes_quiet_hands_over_to_the_next(self):
        c, f = make(0.0)
        c.sound(100.0, 0.9, 1)
        c.osc_bpm(130.0)
        c.set_bpm(90.0)
        c.hear("link", 140.0)
        f.t = 4.9
        c.sound(100.0, 0.9, 2)                              # sound keeps talking
        self.assertEqual(c.source(), "link")
        f.t = 5.1                                           # link (5 s) is quiet; tap (30 s) is next
        self.assertEqual((c.source(), c.bpm()), ("tap", 90.0))
        f.t = 31.0                                          # tap quiet; osc quiet since 5 s; sound spoke at 4.9
        c.sound(105.0, 0.9, 3)
        self.assertEqual((c.source(), c.bpm()), ("sound", 105.0))
        f.t = 40.0                                          # now everybody is quiet: the last tempo runs on, held
        st = c.status()
        self.assertEqual((st["state"], st["source"], st["held"], st["bpm"]), ("running", "sound", True, 105.0))
        self.assertAlmostEqual(st["age"], 9.0, places=2)
        self.assertEqual(st["steering"], None)
        self.assertEqual(st["ages"]["tap"], 40.0)

    def test_sound_is_believed_only_at_half_confidence_or_more(self):
        c, f = make(0.0)
        self.assertEqual(clk.SOUND_MIN_CONFIDENCE, 0.5)
        self.assertFalse(c.sound(120.0, 0.49, 1))
        self.assertEqual(c.state(), "none", "a doubtful tempo starts nothing")
        self.assertTrue(c.sound(120.0, 0.5, 2))
        self.assertEqual(c.bpm(), 120.0)
        f.t = 1.0
        self.assertFalse(c.sound(60.0, 0.2, 3))
        self.assertEqual(c.bpm(), 120.0, "a doubtful word changes nothing")
        self.assertEqual(c.status()["ages"]["sound"], 1.0, "and does not count as a word")
        for odd in ((500.0, 0.9), (10.0, 0.9), (float("nan"), 0.9), (120.0, float("nan")), ("x", None), (None, None), (True, True)):
            self.assertFalse(c.sound(odd[0], odd[1], "x"), odd)
        self.assertEqual(c.bpm(), 120.0)

    def test_a_tap_or_an_osc_word_is_never_overruled_by_sound(self):
        c, f = make(0.0)
        c.set_bpm(90.0)
        for i in range(10):
            f.t += 0.5
            c.sound(140.0, 1.0, i)
        self.assertEqual((c.source(), c.bpm()), ("tap", 90.0))
        c.clear()
        c.osc_bpm(100.0)
        f.t += 1.0
        c.sound(140.0, 1.0, 99)
        self.assertEqual((c.source(), c.bpm()), ("osc", 100.0))

    def test_a_stray_sound_hit_cannot_jump_the_phase_and_a_near_one_only_pushes(self):
        c, f = make(0.0)
        c.sound(120.0, 0.9, 1, at=0.0)
        f.t = 10.25                                         # half a beat off the grid
        c.sound(120.0, 0.9, 2, at=10.25)
        self.assertAlmostEqual(c.beats(), 20.5, places=9, msg="a stray hit changes nothing")
        f.t = 10.55                                         # a tenth of a beat late
        c.sound(120.0, 0.9, 3, at=10.55)
        self.assertAlmostEqual(c.beats(), 21.1 - 0.015, places=9)
        self.assertLessEqual(clk.SOUND_GAIN * clk.SOUND_CAPTURE, 0.045 + 1e-9)
        f.t = 20.0                                          # a block read late from a backlog carries its older time
        c.sound(120.0, 0.9, 4, at=19.0)
        self.assertLess(abs(c.beats() - round(c.beats())), 0.5)
        self.assertEqual(c.status()["sound_hits"] >= 1, True)

    def test_no_new_beat_means_no_nudge_and_a_restarted_service_does_not_count_as_a_hit(self):
        c, f = make(0.0)
        c.sound(120.0, 0.9, 5, at=0.0)
        f.t = 10.55
        c.sound(120.0, 0.9, 5, at=10.55)                    # the same count: no beat
        self.assertAlmostEqual(c.beats(), 21.1, places=9)
        c.sound(120.0, 0.9, 1, at=10.55)                    # the count went down: the service started again
        self.assertAlmostEqual(c.beats(), 21.1, places=9)

    def test_tap_and_osc_beats_set_the_phase_at_once(self):
        c, f = make(0.0)
        c.set_bpm(120.0)
        f.t = 10.2
        self.assertAlmostEqual(c.beats(), 20.4, places=9)
        c.tap()
        self.assertAlmostEqual(c.beats(), 20.0, places=9, msg="the tap is the beat")
        f.t = 10.7
        self.assertAlmostEqual(c.beats(), 21.0, places=9)
        d, g = make(0.0)
        d.osc_bpm(120.0)
        g.t = 10.2
        d.osc_beat()
        self.assertAlmostEqual(d.beats(), 20.0, places=9)
        e, h = make(0.0)
        e.sound(120.0, 0.9, 1)
        h.t = 10.2
        e.osc_beat()
        self.assertEqual(e.source(), "osc", "a beat from OSC takes charge at the tempo the box had")
        self.assertAlmostEqual(e.beats(), 20.0, places=9)

    def test_osc_beats_alone_give_a_tempo_but_a_young_bpm_is_not_overruled(self):
        c, f = make(0.0)
        for t in (0.0, 0.5, 1.0, 1.5):
            f.t = t
            c.osc_beat()
        self.assertAlmostEqual(c.bpm(), 120.0, places=6)
        self.assertEqual(c.source(), "osc")
        d, g = make(0.0)
        d.osc_bpm(100.0)
        for t in (0.5, 1.0, 1.5, 2.0):
            g.t = t
            d.osc_beat()
        self.assertEqual(d.bpm(), 100.0)

    def test_clear_forgets_everything(self):
        c, f = make(0.0)
        c.tap()
        f.t = 0.5
        c.tap()
        c.sound(120.0, 0.9, 1)
        c.clear()
        st = c.status()
        self.assertEqual((st["state"], st["source"], st["taps"], st["ages"]), ("none", None, 0, {s: None for s in clk.SOURCES}))
        self.assertIsNone(c.beats())
        f.t = 5.0
        c.set_bpm(100.0)
        self.assertAlmostEqual(c.beats(), 0.0, places=9, msg="a new origin")


class TapTest(unittest.TestCase):
    def taps(self, c, f, times):
        out = None
        for t in times:
            f.t = t
            out = c.tap()
        return out

    def test_two_to_eight_taps_are_averaged(self):
        c, f = make(0.0)
        r = self.taps(c, f, [0.0])
        self.assertEqual((r["taps"], r["bpm"]), (1, None), "one tap gives no tempo")
        r = self.taps(c, f, [0.5])
        self.assertEqual((r["taps"], r["bpm"]), (2, 120.0))
        c.clear()
        r = self.taps(c, f, [10.0, 10.52, 10.98, 11.5])      # uneven hands: the average over the whole run
        self.assertAlmostEqual(r["bpm"], 120.0, places=2)
        c.clear()
        r = self.taps(c, f, [float(i) for i in range(12)])   # twelve slow taps: 60 BPM
        self.assertEqual((r["taps"], r["bpm"]), (8, 60.0), "never more than eight are kept")
        r = self.taps(c, f, [11.5 + 0.5 * i for i in range(8)])
        self.assertAlmostEqual(r["bpm"], 120.0, places=6, msg="the last eight only")

    def test_a_pause_over_two_seconds_starts_a_new_count(self):
        c, f = make(0.0)
        self.taps(c, f, [0.0, 0.5])
        r = self.taps(c, f, [2.5])                           # exactly 2.0 s after the last: still the same count
        self.assertEqual(r["taps"], 3)
        r = self.taps(c, f, [5.0])                           # 2.5 s: a new count
        self.assertEqual(r["taps"], 1)
        self.assertEqual(c.bpm() is not None, True, "the tempo goes on; only the count starts again")

    def test_the_limits_30_to_300(self):
        c, f = make(0.0)
        self.taps(c, f, [0.0])
        with self.assertRaises(ClockError):
            self.taps(c, f, [0.1])                           # 600 BPM
        self.assertEqual(c.status()["taps"], 1, "a refused tap is not counted")
        self.assertEqual(c.state(), "none")
        r = self.taps(c, f, [0.5])
        self.assertEqual(r["bpm"], 120.0)
        c.clear()
        self.taps(c, f, [10.0])
        self.assertEqual(self.taps(c, f, [12.0])["bpm"], 30.0, "30 is allowed")
        c.clear()
        self.taps(c, f, [0.0])
        self.assertEqual(self.taps(c, f, [0.2])["bpm"], 300.0, "300 is allowed")
        c.clear()
        self.taps(c, f, [30.0])
        with self.assertRaises(ClockError):
            self.taps(c, f, [30.19])

    def test_a_tempo_by_number_and_a_nudge(self):
        c, f = make(0.0)
        for bad in (29.9, 300.1, 0, -5, float("nan"), float("inf"), True, "120", None, [120], 10 ** 400):
            with self.assertRaises(ClockError, msg=repr(bad)):
                c.set_bpm(bad)
        self.assertEqual(c.state(), "none")
        self.assertEqual(c.set_bpm(30)["bpm"], 30.0)
        self.assertEqual(c.set_bpm(300)["bpm"], 300.0)
        c.set_bpm(120)
        f.t = 1.0
        b = c.beats()
        c.nudge(0.25)
        self.assertAlmostEqual(c.beats(), b + 0.25, places=9)
        c.nudge(-0.5)
        self.assertAlmostEqual(c.beats(), b - 0.25, places=9)
        for bad in (0.51, -0.51, 4, float("nan"), True, "0.1", None):
            with self.assertRaises(ClockError, msg=repr(bad)):
                c.nudge(bad)
        e, g = make()
        with self.assertRaises(ClockError):
            e.nudge(0.1)


class OddInputTest(unittest.TestCase):
    def test_osc_and_sound_never_raise_and_never_change_the_clock_on_odd_input(self):
        c, f = make(0.0)
        c.osc_bpm(120.0)
        odd = (float("nan"), float("inf"), -float("inf"), True, False, "120", None, [1], {}, 10 ** 400, -1, 0, 29.99, 300.01, b"x")
        not_times = (float("nan"), float("inf"), True, "x", [1], {}, 10 ** 400, b"x")
        for v in odd:
            self.assertFalse(c.osc_bpm(v), repr(v))
            self.assertFalse(c.hear("osc", v), repr(v))
            c.sound(v, v, v, at=v, now=f.t)
        self.assertFalse(c.hear("nowhere", 120.0))
        self.assertEqual((c.bpm(), c.source()), (120.0, "osc"))
        for t in not_times:
            c.osc_beat(now=t)                                # a time that is not a time: the latest one is used
            c.tick(now=t)
            c.beats(at=t)
            c.status(now=t)
        self.assertEqual(c.bpm(), 120.0)

    def test_a_flood_stays_bounded(self):
        c, f = make(0.0)
        for i in range(20000):
            c.osc_beat()                                     # twenty thousand beats at one instant
        self.assertLessEqual(len(c._osc_taps), clk.TAP_MAX)
        for i in range(20000):
            f.t += 0.001
            c.osc_beat()
        self.assertLessEqual(len(c._osc_taps), clk.TAP_MAX)
        d, g = make(0.0)
        for i in range(500):
            g.t += 0.5
            d.tap()
        self.assertLessEqual(d.status()["taps"], clk.TAP_MAX)


# --- the consumer: the levels the panel, OSC out and the shader input are given ----------------------------------------------
class ConsumerTest(unittest.TestCase):
    def setUp(self):
        self.f = Fake(1000.0)
        self.dir = tempfile.mkdtemp(prefix="pvj-clock-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "levels")
        self.out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path=self.path, clock=self.f, log=lambda *_: None)
        self.out.status = lambda: {}

    def write(self, **over):
        values = record(**over)
        with open(self.path, "wb") as fh:
            fh.write(sound.pack(values, 7, time.monotonic(), sound.F_CAPTURING))

    def test_with_no_tap_or_osc_clock_sound_is_exactly_as_it_was(self):
        self.write()
        raw = sound.read_levels(self.path)
        got = self.out.levels()
        for k in ("pulse", "bpm", "confidence", "beats", "level", "envelope", "rms_db", "capturing", "idle", "slow_bands"):
            self.assertEqual(got[k], raw[k], k)
        self.assertNotIn("clock", got)
        self.assertEqual(self.out.clock.source(), "sound", "the clock heard it, as a source below the others")
        self.assertEqual(self.out.levels()["pulse"], raw["pulse"], "and sound still steers the pulse the way it did")
        os.remove(self.path)
        self.assertIsNone(self.out.levels(), "no file, no clock: nothing, as before")

    def test_with_a_tap_clock_and_no_sound_device_the_beat_follows_the_clock(self):
        self.out.clock.set_bpm(120.0)                        # at 1000.0
        got = self.out.levels()
        self.assertTrue(got["clock"])
        self.assertTrue(got["fresh"])
        self.assertEqual((got["bpm"], got["confidence"]), (120.0, 1.0))
        self.assertAlmostEqual(got["pulse"], 1.0, places=6)
        self.assertEqual((got["level"], got["envelope"], got["slow_bands"], got["capturing"]), (0.0, 0.0, [0.0] * 6, False),
                         "no level is made up: nothing is heard")
        self.assertTrue(self.out.live(got))
        self.f.t = 1000.075
        self.assertAlmostEqual(self.out.levels()["pulse"], 0.5, places=6)
        self.f.t = 1000.3
        self.assertEqual(self.out.levels()["pulse"], 0.0)
        pub = soundout.public(self.out.levels())
        self.assertEqual((pub["bpm"], pub["capturing"], pub["bands"], pub["fresh"]), (120.0, False, [0.0] * 6, True))
        self.assertEqual(self.out.source_value({"source": "beat"}, self.out.levels()), 0.0)
        self.f.t = 1000.5
        self.assertAlmostEqual(self.out.source_value({"source": "beat"}, self.out.levels()), 1.0, places=6, msg="the shader input")

    def test_osc_out_sends_a_beat_message_at_each_clock_beat(self):
        self.out.clock.set_bpm(120.0)
        counts = []
        for step in range(5):
            self.f.t = 1000.0 + 0.25 * step
            v = self.out.levels()
            counts.append([m for m in self.out.plan(v, self.f.t + 10 * step) if m == soundout.beat_message(v["beats"])])
        self.assertEqual([bool(x) for x in counts], [False, False, True, False, True], "one at each half second after the first look")

    def test_with_live_sound_the_levels_stay_sounds_and_only_the_beat_is_the_clocks(self):
        self.write(pulse=0.25, bpm=124.0, beats=5)
        self.out.clock.set_bpm(100.0)
        got = self.out.levels()
        raw = sound.read_levels(self.path)
        self.assertEqual((got["level"], got["envelope"], got["slow_bands"], got["capturing"]), (raw["level"], raw["envelope"], raw["slow_bands"], True))
        self.assertEqual((got["bpm"], got["confidence"]), (100.0, 1.0))
        self.assertAlmostEqual(got["pulse"], 1.0, places=6)

    def test_clear_gives_the_sound_back(self):
        self.write()
        self.out.clock.set_bpm(100.0)
        self.assertTrue(self.out.levels()["clock"])
        self.out.clock.clear()
        self.out.clock.tick(self.f())
        got = self.out.levels()
        self.assertNotIn("clock", got)
        self.assertEqual(got["bpm"], 124.0)

    def test_a_tap_goes_stale_and_sound_takes_over_again(self):
        self.write()
        self.out.clock.set_bpm(100.0)
        self.assertTrue(self.out.levels()["clock"])
        self.f.t += clk.HOLD["tap"] + 1.0
        self.write()
        self.out.levels()
        self.f.t += 0.1
        got = self.out.levels()
        self.assertNotIn("clock", got, "tap is quiet and the sound is believed again")
        self.assertEqual(self.out.clock.source(), "sound")

    def test_a_tap_that_went_quiet_with_no_sound_keeps_the_beat(self):
        self.out.clock.set_bpm(100.0)
        self.f.t += 600.0
        got = self.out.levels()
        self.assertTrue(got["clock"])
        self.assertEqual(got["bpm"], 100.0)

    def test_osc_in_feeds_the_clock_and_odd_input_changes_nothing(self):
        addr = "192.168.0.44"
        self.out.osc_in([("/nxlx/sound/in/bpm", [128.0]), ("/nxlx/sound/in/beat", [])], addr)
        self.assertEqual((self.out.clock.source(), self.out.clock.bpm()), ("osc", 128.0))
        self.assertIsNotNone(self.out.levels(), "and the levels follow it")
        self.assertTrue(self.out.levels()["clock"])
        self.out.osc_in([("/nxlx/sound/in/bpm", [float("nan")]), ("/nxlx/sound/in/bpm", [9999.0]), ("/nxlx/sound/in/bpm", ["x"]),
                         ("/nxlx/sound/in/beat", ["x", None, [1]]), ("/nxlx/sound/in/bpm", [])], addr)
        self.assertEqual(self.out.clock.bpm(), 128.0)

    def test_tick_hears_the_sound_file_and_hands_over(self):
        self.write()
        self.out.tick()
        self.assertEqual(self.out.clock.source(), "sound")
        self.assertEqual(self.out.clock.bpm(), 124.0)

    def test_the_privacy_rules_stand_nothing_is_added_to_what_leaves(self):
        self.out.clock.set_bpm(120.0)
        pub = soundout.public(self.out.levels(), tunnel=True)
        self.assertEqual((pub["bands"], pub["bands_hidden"]), ([], True))
        v = self.out.levels()
        body = soundout.values_bundle(v) + soundout.bands_bundle(v)
        self.assertIn(b"/nxlx/sound/pulse", body)
        with open(os.path.join(os.path.dirname(clk.__file__), "clock.py")) as fh:
            code = fh.read()
        for word in ("socket", "open(", "subprocess", "import os"):
            self.assertNotIn(word, code, "the clock touches no file, no socket and no process")


# --- the routes -----------------------------------------------------------------------------------------------------------
class ClockRoutesTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.f = Fake(500.0)
        self.sdir = tempfile.mkdtemp(prefix="pvj-clock-api-")
        self.addCleanup(shutil.rmtree, self.sdir, True)
        self.api.sound = soundout.Sound(self.api, socket_path=os.path.join(self.sdir, "none.sock"),
                                        levels_path=os.path.join(self.sdir, "levels"), clock=self.f, proc=proc_dir(self), log=lambda *_: None)
        self.addCleanup(self.api.sound.stop)

    def session(self):
        from tests.test_support import CFG
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.owner)[0], 200)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        return self.api.support.authenticate(login["token"])

    def test_the_owner_and_the_operator_look_tap_set_nudge_and_clear_and_a_guest_nothing(self):
        for who in (self.owner, self.operator):
            self.assertEqual(self.h("GET", "/api/clock", {}, who)[1]["clock"]["state"], "none")
        for method, path, body in (("GET", "/api/clock", {}), ("POST", "/api/clock", {"bpm": 120}), ("POST", "/api/clock/tap", {})):
            self.assertEqual(self.h(method, path, body, self.guest)[0], 403, path)
        self.assertEqual(self.h("POST", "/api/clock/tap", {}, self.operator)[1]["tap"]["taps"], 1)
        self.f.t += 0.5
        st, d = self.h("POST", "/api/clock/tap", {}, self.owner)
        self.assertEqual((st, d["tap"]["taps"], d["clock"]["bpm"], d["clock"]["source"]), (200, 2, 120.0, "tap"))
        self.assertEqual(self.h("POST", "/api/clock", {"bpm": 90}, self.operator)[1]["clock"]["bpm"], 90.0)
        self.assertEqual(self.h("POST", "/api/clock", {"nudge": 0.1}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/clock", {"clear": True}, self.operator)[1]["clock"]["state"], "none")
        self.assertEqual(self.h("GET", "/api/sound", {}, self.operator)[1]["clock"]["state"], "none", "the sound page carries it too")

    def test_refusals_are_plain(self):
        for body in ({}, {"bpm": 1000}, {"bpm": "fast"}, {"bpm": True}, {"nudge": 3}, {"nudge": 0.1}, {"clear": False}, {"clear": 1},
                     {"bpm": 120, "clear": True}, {"x": 1}, {"bpm": None}):
            st, d = self.h("POST", "/api/clock", body, self.operator)
            self.assertEqual(st, 400, body)
            self.assertIn("error", d)
        self.f.t = 500.0
        self.h("POST", "/api/clock/tap", {}, self.operator)
        self.f.t = 500.05
        st, d = self.h("POST", "/api/clock/tap", {}, self.operator)
        self.assertEqual(st, 409)
        self.assertIn("not counted", d["error"])

    def test_a_support_session_may_look_and_may_not_change(self):
        self.h("POST", "/api/clock", {"bpm": 100}, self.owner)
        dev = self.session()
        self.assertEqual(self.h("GET", "/api/clock", {}, dev, TUNNEL)[1]["clock"]["bpm"], 100.0)
        for path, body in (("/api/clock", {"bpm": 150}), ("/api/clock", {"nudge": 0.1}), ("/api/clock", {"clear": True}), ("/api/clock/tap", {})):
            st, d = self.h("POST", path, body, dev, TUNNEL)
            self.assertEqual(st, 403, (path, body))
        st, d = self.h("POST", "/api/clock", {"clear": True}, dev, TUNNEL)
        self.assertIn("set in the room", d["error"])
        c = self.api.sound.clock.status()
        self.assertEqual((c["bpm"], c["taps"]), (100.0, 0), "nothing changed")

    def test_controllers_may_only_tap(self):
        for device in (midi.MIDI_DEVICE, osc.OSC_DEVICE, dmx.DMX_DEVICE):
            self.f.t += 0.5
            st, d = self.api.handle("POST", "/api/clock/tap", {}, device, device["id"])
            self.assertEqual(st, 200, device)
            self.assertEqual(self.api.handle("POST", "/api/clock", {"bpm": 100}, device, device["id"])[0], 403, device)
            self.assertEqual(self.api.handle("GET", "/api/clock", {}, device, device["id"])[0], 403, device)
        self.assertEqual(self.api.sound.clock.status()["taps"], 3, "three taps, one from each")

    def test_a_tap_does_not_switch_listening_on_or_save_anything(self):
        before = self.settings.data.get("sound")
        saved = self.saves()
        self.h("POST", "/api/clock/tap", {}, self.operator)
        self.f.t += 0.5
        self.h("POST", "/api/clock/tap", {}, self.operator)
        self.h("POST", "/api/clock", {"bpm": 100}, self.operator)
        self.assertEqual(saved, [], "the tempo is kept in memory only: it starts empty after a restart")
        self.assertEqual(self.settings.data.get("sound"), before)
        self.assertFalse(self.api.sound.config()["on"])


# --- the controller actions: MIDI, OSC and DMX ----------------------------------------------------------------------------
class ControllerActionTest(unittest.TestCase):
    def test_the_one_table_has_it_and_midi_and_osc_make_the_same_call(self):
        self.assertEqual(actions.press("clock_tap"), ("/api/clock/tap", {}))
        self.assertEqual(midi.ACTIONS["clock_tap"][0], "trigger")
        calls = []
        m = midi.MidiMapper(lambda p, b: calls.append((p, b)) or True,
                            [midi.validate_entry({"kind": "note", "number": 40, "action": "clock_tap"})], {})
        m.message("x", ("on", 0, 40, 127))
        self.assertEqual(calls, [("/api/clock/tap", {})])
        (a, parsed), = osc.parse_packet(_osc_message("/pvj/clock/tap", 1.0))
        self.assertEqual(osc.translate(a, parsed, None), actions.press("clock_tap"))
        (a, parsed), = osc.parse_packet(_osc_message("/pvj/clock/tap", 0.0))
        self.assertIsNone(osc.translate(a, parsed, None), "the release of a button does nothing")
        (a, parsed), = osc.parse_packet(_osc_message("/pvj/clock/tap"))
        self.assertEqual(osc.translate(a, parsed, None), actions.press("clock_tap"), "no argument is a press")

    def test_dmx_taps_each_time_the_tenth_channel_moves_up_to_128_or_more(self):
        rec, t = Recorder(), [100.0]
        m = dmx.DmxMapper(rec, start=1, clock=lambda: t[0])

        def frame(tenth, ninth=0):
            t[0] += 0.1
            return m.frame(bytes([0] * 8 + [ninth, tenth]))
        self.assertEqual(frame(255), 0, "the first frame is a baseline: a console at rest taps nothing")
        self.assertEqual(rec.calls, [])
        frame(0)
        frame(127)
        self.assertEqual(rec.calls, [])
        frame(128)
        self.assertEqual(rec.calls, [("/api/clock/tap", {})])
        frame(255)
        frame(200)
        self.assertEqual(len(rec.calls), 1, "held up: one tap")
        frame(0)
        frame(255)
        self.assertEqual(len(rec.calls), 2)
        rec2 = Recorder()
        m2 = dmx.DmxMapper(rec2, start=1, clock=lambda: t[0])
        m2.frame(bytes([0] * 9))
        t[0] += 0.1
        self.assertEqual(m2.frame(bytes([0] * 8 + [120])), 1, "a console that sends nine channels still has the Vibes channel")
        self.assertEqual(rec2.calls, [("/api/vibes", {"on": True})])
        self.assertEqual(dmx.CLOCK_CHANNEL, dmx.VIBES_CHANNEL + 1)


def _osc_message(address, *args):
    from tests.test_osc import msg
    return msg(address, *args)


if __name__ == "__main__":
    unittest.main()
