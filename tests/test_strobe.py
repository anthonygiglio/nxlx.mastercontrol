# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The Strobe (pvj/strobe.py, pvj/effects.d/fx-strobe.fs): a flash held down that stops the moment it is let go.

What is held here:
* the state machine, against stand-ins for the chain and the effect engine and a clock the test moves: the fast path (a
  hold is one value, no file, no compile), the dead-man (a lease that is not renewed ends the strobe within a half
  second, a renewal of a lapsed lease is refused, the longest hold, several holders), the cap (no way to ask for more than
  3 a second without the opt-in), and the gap between lit stretches (random presses and rate changes never start two
  closer together than the gap);
* the routes with the real API and a stand-in for mpv (the classic path: the strobe is the effect, and what was on comes
  back), the roles, the effect in the library;
* MIDI (note on, note off, a pulled cable, lost messages) and OSC (1 and 0, the lockdown, the sender as holder);
* the filter itself, run by pvj-render when it is built (CI builds it): the rate cap in the shader's own timing, and a
  hold on the real renderer being one value.
"""
import json
import os
import random
import re
import threading
import time
import unittest

import tests        # noqa: F401  (the run's own temp folder, ranked locks)
from pvj import actions, effects as E, midi, osc, policy, shaders as S, strobe as ST
from pvj.api import ApiError
from pvj.player import PlayerError
from tests.test_effects import Base as FxBase
from tests.test_osc import msg


# ---- stand-ins ---------------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Chains:
    """The chain slots of source 1: what the strobe loads, gates and takes off."""
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail_values = 0                    # the next this many `values` calls raise
        self.fail_off = False

    def state(self):
        return {"chains": [{"source": s, "slot": k, "id": r["id"]} for (s, k), r in sorted(self.rows.items())]}

    def put(self, body):
        self.calls.append(("put", body["slot"], body["controls"]["amount"]))
        self.rows[(body["source"], body["slot"])] = {"id": body["id"], "amount": body["controls"]["amount"], "speed": body["controls"]["speed"],
                                                      "values": dict(body["values"])}
        return {"ok": True}

    def values(self, body):
        if self.fail_values:
            self.fail_values -= 1
            raise ApiError(503, "the renderer did not answer")
        r = self.rows.get((body["source"], body["slot"]))
        if r is None:
            raise ApiError(409, "no effect is on in slot %d of source %d" % (body["slot"], body["source"]))
        c = body.get("controls") or {}
        r["amount"] = c.get("amount", r["amount"])
        r["speed"] = c.get("speed", r["speed"])
        r["values"].update(body.get("values") or {})
        self.calls.append(("values", body["slot"], r["amount"]))
        return {"ok": True}

    def off(self, body):
        if self.fail_off:
            raise ApiError(503, "the renderer did not answer")
        self.calls.append(("off", body["slot"]))
        self.rows.pop((body["source"], body["slot"]), None)
        return {"ok": True}

    def amounts(self):
        return [c[2] for c in self.calls if c[0] in ("put", "values")]


class Effects:
    """The effect engine as the strobe uses it, for the path where the strobe is the effect."""
    def __init__(self):
        self.fast = False
        self.on = None
        self.recent = None
        self.calls = []

    def faster(self):
        return self.fast

    def _need(self):
        pass

    def _path(self, sid):
        return "/x/" + sid, "bundled"

    def current(self):
        return dict(self.on) if self.on else None

    def put(self, sid, values=None, controls=None, preset=None):
        self.calls.append(("put", sid, dict(controls or {})))
        self.on = {"id": sid, "values": dict(values or {}), "controls": dict(controls or {})}
        self.recent = sid
        return {"ok": True}

    def off(self, why=None, asked=None):
        self.calls.append(("off",))
        self.on = None
        return {"ok": True}


class FakePlayer:
    def __init__(self, native=True):
        self.native = native

    def native_chains(self):
        return self.native


class Api:
    def __init__(self, native=True):
        self.player, self.effects, self.chains = FakePlayer(native), Effects(), Chains()
        self.levels = {"volume": 100.0, "speed": 1.0}
        self.lines = []

    def log(self, line):
        self.lines.append(line)


DEVICE = {"id": "ab12cd34", "name": "phone", "role": "live"}
OTHER = {"id": "ef56ab78", "name": "tablet", "role": "live"}
MIDI = midi.MIDI_DEVICE
OSC = osc.OSC_DEVICE


def rig(native=True):
    api, clock = Api(native), Clock()
    s = ST.Strobe(api, clock=clock, thread=False)
    return api, clock, s


def lit(api):
    return any(r["amount"] > 0 for r in api.chains.rows.values())


# ---- the state machine -------------------------------------------------------------------------------------------------
class FastPathTest(unittest.TestCase):
    def test_once_it_is_loaded_a_hold_is_one_value_and_a_release_is_one_value(self):
        api, clock, s = rig()
        s.set({"arm": True})
        self.assertEqual(api.chains.calls, [("put", 3, 0.0)])               # loaded at amount 0: nothing is drawn for it
        self.assertFalse(lit(api))
        out = s.hold(DEVICE, {"holder": "tab1"})
        self.assertEqual((out["ok"], out["on"], out["held"]), (True, True, 1))
        self.assertEqual(api.chains.calls, [("put", 3, 0.0), ("values", 3, 1.0)])      # one value: no file, no compile, no effect switch
        self.assertEqual(api.effects.calls, [])
        s.release(DEVICE, {"holder": "tab1"})
        self.assertEqual(api.chains.calls[-1], ("values", 3, 0.0))
        self.assertFalse(lit(api))
        self.assertEqual(api.effects.calls, [])
        self.assertIsNotNone(s.state()["started_ms"])

    def test_the_first_hold_loads_it_then_lights_it(self):
        api, clock, s = rig()
        s.hold(DEVICE, {})
        self.assertEqual(api.chains.amounts(), [0.0, 1.0])
        self.assertTrue(lit(api) and s.state()["armed"])

    def test_the_loaded_copy_is_taken_off_after_a_while_without_use_and_not_while_held(self):
        api, clock, s = rig()
        s.set({"arm": True})
        clock.t += ST.PARK_SECONDS - 1
        s.tick()
        self.assertTrue(s.state()["armed"])
        s.hold(DEVICE, {})
        clock.t += ST.PARK_SECONDS * 2          # (the lease ran out meanwhile; the point is that nothing is taken off while it is lit)
        s.hold(DEVICE, {})
        self.assertTrue(lit(api) and s.state()["armed"])
        s.release(DEVICE, {})
        clock.t += ST.PARK_SECONDS - 1
        s.tick()
        self.assertTrue(s.state()["armed"])
        clock.t += 2
        s.tick()
        self.assertEqual((s.state()["armed"], api.chains.rows), (False, {}))

    def test_a_stop_of_the_player_that_took_the_loaded_copy_off_is_loaded_again(self):
        api, clock, s = rig()
        s.set({"arm": True})
        api.chains.rows.clear()                 # Stop, or a restart of the player
        self.assertFalse(s.state()["armed"] and s._loaded() is not None)
        s.hold(DEVICE, {})
        self.assertTrue(lit(api))

    def test_it_uses_the_other_slot_when_one_is_taken_and_leaves_what_is_in_them(self):
        api, clock, s = rig()
        api.chains.rows[(1, 3)] = {"id": "fx-grade.fs", "amount": 1.0, "speed": 1.0, "values": {}}
        s.hold(DEVICE, {})
        self.assertEqual(api.chains.rows[(1, 3)]["id"], "fx-grade.fs")
        self.assertEqual(api.chains.rows[(1, 2)]["id"], ST.EFFECT)
        self.assertEqual(api.effects.calls, [])

    def test_with_both_slots_taken_or_on_classic_it_is_the_effect_and_what_was_on_comes_back(self):
        for native, full in ((False, False), (True, True)):
            with self.subTest(native=native):
                api, clock, s = rig(native)
                if full:
                    for slot in (2, 3):
                        api.chains.rows[(1, slot)] = {"id": "fx-grade.fs", "amount": 1.0, "speed": 1.0, "values": {}}
                api.effects.on = {"id": "fx-wash.fs", "values": {"strength": 0.5}, "controls": {"amount": 0.7, "speed": 1.0}}
                api.effects.recent = "fx-wash.fs"
                s.hold(DEVICE, {})
                self.assertEqual(api.effects.on["id"], ST.EFFECT)
                self.assertEqual(api.effects.on["controls"]["amount"], 1.0)
                self.assertEqual(s.state()["path"], "effect")
                s.release(DEVICE, {})
                self.assertEqual((api.effects.on["id"], api.effects.on["controls"]["amount"], api.effects.on["values"]), ("fx-wash.fs", 0.7, {"strength": 0.5}))
                self.assertEqual(api.effects.recent, "fx-wash.fs")               # the one button must not put the strobe back
        api, clock, s = rig(False)
        s.hold(DEVICE, {})
        s.release(DEVICE, {})
        self.assertIsNone(api.effects.on)                               # nothing was on before: nothing is after
        self.assertIsNone(api.effects.recent)

    def test_if_the_effect_was_taken_off_by_someone_else_meanwhile_nothing_is_put_back_over_it(self):
        api, clock, s = rig(False)
        api.effects.on = {"id": "fx-wash.fs", "values": {}, "controls": {"amount": 1.0, "speed": 1.0}}
        s.hold(DEVICE, {})
        api.effects.on = {"id": "fx-grade.fs", "values": {}, "controls": {"amount": 1.0, "speed": 1.0}}      # the person chose another
        s.release(DEVICE, {})
        self.assertEqual(api.effects.on["id"], "fx-grade.fs")


class DeadManTest(unittest.TestCase):
    def test_a_lease_that_is_not_renewed_ends_the_strobe_within_half_a_second_of_the_watcher(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        self.assertTrue(lit(api))
        clock.t += ST.PANEL_TTL - 0.05
        s.tick()
        self.assertTrue(lit(api))
        clock.t += 0.1                              # the lease has run out
        s.tick()
        self.assertFalse(lit(api))
        self.assertLessEqual(ST.PANEL_TTL + ST.TICK, 0.55)          # and the watcher looks every TICK: at most this long after the last renewal
        self.assertEqual(s.state()["held"], 0)

    def test_renewals_every_200_milliseconds_keep_it_and_one_missed_does_not_end_it(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        for _ in range(40):                         # eight seconds
            clock.t += ST.RENEW_EVERY
            s.tick()
            s.hold(DEVICE, {"holder": "t"}, renew=True)
        self.assertTrue(lit(api))
        clock.t += 2 * ST.RENEW_EVERY               # two renewals late: still inside the lease
        s.tick()
        self.assertTrue(lit(api))
        s.hold(DEVICE, {"holder": "t"}, renew=True)

    def test_a_renewal_of_a_hold_that_ended_is_refused_and_does_not_start_it_again(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        clock.t += 1.0
        s.tick()
        self.assertFalse(lit(api))
        with self.assertRaises(ApiError) as e:
            s.hold(DEVICE, {"holder": "t"}, renew=True)         # the network came back, late
        self.assertEqual(e.exception.status, 409)
        self.assertFalse(lit(api))
        s.hold(DEVICE, {"holder": "t"})                         # a new press does
        clock.t += ST.GAP + 0.01
        s.tick()
        self.assertTrue(lit(api))

    def test_a_renewal_that_comes_after_the_lease_ran_out_even_if_nobody_looked_is_refused(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        clock.t += 5.0                                          # the watcher has not run at all
        with self.assertRaises(ApiError):
            s.hold(DEVICE, {"holder": "t"}, renew=True)
        self.assertFalse(lit(api))                              # and the refusal itself let go

    def test_an_osc_lease_is_the_panels_half_second_and_a_repeated_1_keeps_it(self):
        api, clock, s = rig()
        s.hold(OSC, {"holder": "10.0.0.5"})
        clock.t += ST.PANEL_TTL - 0.05
        s.tick()
        self.assertTrue(lit(api))
        clock.t += 0.1                                          # one press and no repeats: about half a second
        s.tick()
        self.assertFalse(lit(api))
        s.release(OSC, {"all": True})
        clock.t += ST.GAP + 0.01
        s.hold(OSC, {"holder": "10.0.0.5"})
        for _ in range(30):                                     # a sender that repeats its 1 every 0.2 s
            clock.t += ST.RENEW_EVERY
            s.tick()
            self.assertTrue(lit(api))
            s.hold(OSC, {"holder": "10.0.0.5"})

    def test_a_midi_lease_is_a_backstop_for_a_hub_that_stopped_renewing(self):
        api, clock, s = rig()
        s.hold(MIDI, {"holder": "pad"})
        clock.t += ST.CONTROLLER_TTL - 1
        s.tick()
        self.assertTrue(lit(api))
        clock.t += 2
        s.tick()
        self.assertFalse(lit(api))

    def test_no_hold_lasts_longer_than_the_longest_however_it_is_renewed_and_it_must_be_let_go_of(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        t0 = clock.t
        refused = None
        while clock.t - t0 < ST.MAX_HOLD + 5:
            clock.t += ST.RENEW_EVERY
            s.tick()
            try:
                s.hold(DEVICE, {"holder": "t"}, renew=True)
            except ApiError as e:
                refused = clock.t - t0
                break
        self.assertIsNotNone(refused, "a stuck tab that goes on renewing was never stopped")
        self.assertAlmostEqual(refused, ST.MAX_HOLD, delta=0.5)
        self.assertFalse(lit(api))                              # a finger left on the button, a tab that renews for ever
        for _ in range(20):                                     # and it is refused for as long as it goes on, renewing or pressing
            clock.t += ST.RENEW_EVERY
            with self.assertRaises(ApiError):
                s.hold(DEVICE, {"holder": "t"})
            with self.assertRaises(ApiError):
                s.hold(DEVICE, {"holder": "t"}, renew=True)
        self.assertFalse(lit(api))
        clock.t += ST.SPENT_SECONDS + 0.1                       # it stopped for a moment: a new press is a new hold
        s.hold(DEVICE, {"holder": "t"})
        clock.t += ST.GAP
        s.tick()
        self.assertTrue(lit(api))
        t1 = clock.t
        s.release(DEVICE, {"holder": "t"})                      # and a hold can be let go of any time before
        s.hold(DEVICE, {"holder": "t"})
        clock.t += ST.GAP + 0.01
        s.tick()
        self.assertTrue(lit(api))

    def test_holders_are_counted_each_has_its_own_lease_and_the_strobe_lasts_while_one_does(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "a"})
        s.hold(OTHER, {"holder": "a"})                          # the same name from another device is another holder
        s.hold(MIDI, {"holder": "Mini:note60"})
        self.assertEqual(s.state()["held"], 3)
        s.release(DEVICE, {"holder": "a"})
        self.assertTrue(lit(api))
        clock.t += 0.3
        s.hold(MIDI, {"holder": "Mini:note60"})
        clock.t += 0.3                                          # the tablet vanished
        s.tick()
        self.assertEqual(s.state()["held"], 1)
        self.assertTrue(lit(api))
        s.release(MIDI, {"holder": "Mini:note60"})
        self.assertFalse(lit(api))
        s.hold(DEVICE, {"holder": "a"})
        s.hold(OTHER, {"holder": "a"})
        s.release(DEVICE, {"all": True})                        # the panic button
        self.assertEqual((s.state()["held"], lit(api)), (0, False))

    def test_there_are_few_holders_and_the_name_is_checked(self):
        api, clock, s = rig()
        for n in range(ST.MAX_HOLDERS):
            s.hold(DEVICE, {"holder": "h%d" % n})
        with self.assertRaises(ApiError) as e:
            s.hold(DEVICE, {"holder": "one-more"})
        self.assertEqual(e.exception.status, 429)
        for bad in ("x" * 65, "a;b", "a\nb", "<b>", 5, ["a"]):
            with self.assertRaises(ApiError, msg=repr(bad)):
                s.hold(DEVICE, {"holder": bad})

    def test_the_watcher_thread_does_the_stop_with_a_real_clock(self):
        api = Api()
        s = ST.Strobe(api, thread=True)
        self.addCleanup(s.stop)
        s.hold(DEVICE, {"holder": "t"})
        self.assertTrue(lit(api))
        began = time.monotonic()
        deadline = began + 3.0
        while time.monotonic() < deadline and lit(api):
            time.sleep(0.01)
        took = time.monotonic() - began
        self.assertFalse(lit(api), "the watcher did not stop it")
        self.assertLess(took, ST.PANEL_TTL + 0.25, took)        # about half a second, with a little room for a slow machine
        self.assertGreaterEqual(took, ST.PANEL_TTL - 0.05, took)

    def test_stopping_the_node_lets_go_of_everything(self):
        api = Api()
        s = ST.Strobe(api, thread=True)
        s.hold(DEVICE, {})
        s.hold(MIDI, {"holder": "x"})
        s.stop()
        self.assertFalse(lit(api))
        self.assertEqual(s.state()["held"], 0)

    def test_a_stop_that_fails_is_tried_again_and_a_gentle_failure_takes_the_copy_off(self):
        api, clock, s = rig()
        s.hold(DEVICE, {})
        api.chains.fail_values = 1                              # the amount cannot be set to 0
        s.release(DEVICE, {})
        self.assertFalse(lit(api))                              # so the copy was taken off instead
        self.assertEqual(api.chains.calls[-1][0], "off")
        api, clock, s = rig()
        s.hold(DEVICE, {})
        api.chains.fail_values = 1
        api.chains.fail_off = True                              # and that fails too
        s.release(DEVICE, {})
        self.assertTrue(s.lit)                                  # it is known to be lit, and says so ...
        api.chains.fail_off = False
        clock.t += 0.1
        s.tick()                                                # ... and the watcher goes on until it is off
        self.assertFalse(lit(api))
        self.assertFalse(s.lit)


class CapTest(unittest.TestCase):
    def test_the_rate_is_kept_to_three_a_second_and_the_speed_to_one(self):
        api, clock, s = rig()
        out = s.set({"rate": 9})
        self.assertEqual(out["settings"]["rate"], 3.0)
        self.assertEqual(out["limits"]["rate"], {"min": 0.5, "max": 3.0, "step": 0.05, "cap": 3.0})
        s.hold(DEVICE, {})
        row = api.chains.rows[(1, 3)]
        self.assertEqual((row["values"]["rate"], row["speed"]), (3.0, 1.0))
        for bad in (True, "fast", None, float("nan"), float("inf"), [3]):
            with self.assertRaises(ApiError, msg=repr(bad)):
                s.set({"rate": bad})
        self.assertEqual(s.set({"rate": -5})["settings"]["rate"], ST.RATE_MIN)
        self.assertEqual(s.set({"rate": 2.37})["settings"]["rate"], 2.35)       # kept to a step: a knob does not send a timing for every step

    def test_above_three_only_with_the_opt_in_and_then_as_the_filters_three_times_a_speed(self):
        api, clock, s = rig()
        api.effects.fast = True
        out = s.set({"rate": 9})
        self.assertEqual(out["settings"]["rate"], 9.0)
        self.assertEqual(s.set({"rate": 99})["settings"]["rate"], ST.FAST_CAP)
        s.set({"rate": 9})
        s.hold(DEVICE, {})
        row = api.chains.rows[(1, 3)]
        self.assertEqual((row["values"]["rate"], row["speed"]), (3.0, 3.0))     # the filter's own cap stays 3; the speed is the opt-in's
        self.assertLessEqual(row["values"]["rate"] * row["speed"], ST.FAST_CAP)

    def test_switching_the_opt_in_off_while_it_flashes_brings_the_rate_back_to_three(self):
        api, clock, s = rig()
        api.effects.fast = True
        s.set({"rate": 9})
        s.hold(DEVICE, {"holder": "t"})
        self.assertEqual(api.chains.rows[(1, 3)]["speed"], 3.0)
        api.effects.fast = False
        clock.t += 0.2
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        self.assertFalse(lit(api))                              # the old timing ended
        clock.t += ST.GAP + 0.01
        s.tick()
        row = api.chains.rows[(1, 3)]
        self.assertEqual((lit(api), row["values"]["rate"], row["speed"]), (True, 3.0, 1.0))

    def test_a_new_rate_or_flash_length_while_it_flashes_starts_a_new_stretch_a_gap_later_a_colour_does_not(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        s.set({"colour": "#ff0000"})
        self.assertTrue(lit(api))                               # a colour is at once
        self.assertEqual(api.chains.rows[(1, 3)]["values"]["tint"], [1.0, 0.0, 0.0, 1.0])
        s.set({"way": "black"})
        self.assertEqual((lit(api), api.chains.rows[(1, 3)]["values"]["way"]), (True, 1))
        s.set({"rate": 1.0})
        self.assertFalse(lit(api))                              # the filter's place in its cycle would jump: it ends here ...
        clock.t += 0.2
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        s.tick()
        self.assertFalse(lit(api))
        clock.t += ST.GAP - 0.2 + 0.01
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        s.tick()
        self.assertTrue(lit(api))                               # ... and a new one starts a gap after the last ended
        self.assertEqual(api.chains.rows[(1, 3)]["values"]["rate"], 1.0)
        s.set({"flash": 120})
        self.assertFalse(lit(api))

    def test_letting_go_and_pressing_again_cannot_start_a_flash_sooner_than_the_gap(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        s.release(DEVICE, {"holder": "t"})
        clock.t += 0.1
        out = s.hold(DEVICE, {"holder": "t"})
        self.assertEqual((out["on"], out["waiting"]), (False, True))
        self.assertFalse(lit(api))
        clock.t += ST.GAP - 0.1 + 0.01
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        s.tick()
        self.assertTrue(lit(api))

    def test_with_the_opt_in_there_is_no_gap_as_the_effect_worker_has_none(self):
        api, clock, s = rig()
        api.effects.fast = True
        s.hold(DEVICE, {"holder": "t"})
        s.release(DEVICE, {"holder": "t"})
        s.hold(DEVICE, {"holder": "t"})
        self.assertTrue(lit(api))

    def test_no_sequence_of_presses_releases_and_changes_starts_two_stretches_closer_than_the_gap(self):
        """The property the cap rests on: with the filter's rate at most 3, flashes inside a stretch are at least a third of
        a second apart, so what could break the cap is two stretches near each other. Random presses, releases, renewals,
        rate and colour changes, lapses and a clock that does not tick evenly never do it."""
        rnd = random.Random(20261111)
        for round_ in range(30):
            api, clock, s = rig()
            starts, ends = [], []               # (when, which step) of every start and every end of a lit stretch
            was = False
            for step in range(400):
                clock.t += rnd.choice((0.0, 0.01, 0.05, 0.2, 0.3, 0.6))
                who = rnd.choice((DEVICE, OTHER, MIDI))
                name = {"holder": rnd.choice("ab")}
                try:
                    act = rnd.randrange(8)
                    if act == 0:
                        s.hold(who, name)
                    elif act == 1:
                        s.release(who, name)
                    elif act == 2:
                        s.hold(who, name, renew=True)
                    elif act == 3:
                        s.set({"rate": rnd.choice((0.5, 1, 2, 3, 3, 9))})
                    elif act == 4:
                        s.set({"flash": rnd.choice((10, 60, 250))})
                    elif act == 5:
                        s.set({"colour": rnd.choice(("#ff0000", "#00ff00"))})
                    else:
                        s.tick()
                except ApiError:
                    pass
                now = lit(api)
                if now and not was:
                    starts.append((clock.t, step))
                if was and not now:
                    ends.append((clock.t, step))
                was = now
            self.assertGreater(len(starts), 3)
            for t, step in starts[1:]:
                last_end = max(e for e, at in ends if at < step)
                self.assertGreaterEqual(t - last_end, ST.GAP - 1e-6, (round_, step))


class ColourTest(unittest.TestCase):
    def test_colours(self):
        self.assertEqual(ST.clean_colour("#FF8800"), "#ff8800")
        self.assertEqual(ST.clean_colour("ff8800"), "#ff8800")
        self.assertEqual(ST.clean_colour([1, 0.5, 0]), "#ff8000")
        self.assertEqual(ST.clean_colour([1, 0, 0, 0.2]), "#ff0000")
        for bad in ("red", "#12345", "#gggggg", "", None, 5, [1, 2], [2, 0, 0], [1, 0, "x"], [True, 0, 0], {"r": 1}):
            with self.assertRaises(ApiError, msg=repr(bad)):
                ST.clean_colour(bad)
        self.assertEqual(ST.colour_rgba("#ff0000"), [1.0, 0.0, 0.0, 1.0])

    def test_the_settings_are_checked(self):
        api, clock, s = rig()
        for bad in ({"colour": "red"}, {"rate": "x"}, {"flash": None}, {"way": "blink"}, {"way": 7}, {"arm": 1}, {"nope": 1}, []):
            with self.assertRaises(ApiError, msg=repr(bad)):
                s.set(bad)
        out = s.set({"colour": "#00ff00", "flash": 5000, "way": 2})
        self.assertEqual(out["settings"], {"rate": 2.0, "colour": "#00ff00", "flash": 250, "way": "add"})
        self.assertEqual(s.set({"color": "#0000ff"})["settings"]["colour"], "#0000ff")


# ---- the routes with the real API, and the effect in the library ---------------------------------------------------------
class RouteBase(FxBase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.api.strobe = ST.Strobe(self.api, clock=self.clock, thread=False, log=lambda *_: None)
        self.strobe = self.api.strobe
        self.addCleanup(self.strobe.stop)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, path, body=None, token=None):
        return self.call("POST", path, body or {}, token=token or self.full)

    def get(self, path, token=None):
        return self.call("GET", path, token=token or self.full)


class RouteTest(RouteBase):
    def test_the_state_says_how_to_hold(self):
        st, body, _ = self.get("/api/strobe")
        self.assertEqual(st, 200)
        self.assertEqual((body["available"], body["effect"], body["held"], body["on"]), (True, "fx-strobe.fs", 0, False))
        self.assertEqual(body["hold"], {"renew_every": 0.2, "expires_after": 0.5, "controller_expires_after": 10.0, "longest": 60.0, "holders": 8})
        self.assertEqual(body["settings"], {"rate": 2.0, "colour": "#ffffff", "flash": 60, "way": "colour"})
        self.assertEqual(body["limits"]["rate"]["max"], 3.0)

    def test_hold_renew_release_and_the_answers(self):
        st, body, _ = self.post("/api/strobe/hold", {"holder": "tab"})
        self.assertEqual(st, 200, body)
        self.assertEqual((body["ok"], body["held"], body["renew_every"], body["expires_in"]), (True, 1, 0.2, 0.5))
        self.assertTrue(body["on"])
        self.assertEqual(self.fx.on["id"], "fx-strobe.fs")      # classic mpv on this rig: the strobe is the effect
        self.clock.t += 0.2
        st, body, _ = self.post("/api/strobe/renew", {"holder": "tab"})
        self.assertEqual((st, body["held"]), (200, 1))
        st, body, _ = self.post("/api/strobe/release", {"holder": "tab"})
        self.assertEqual((st, body["held"], body["on"]), (200, 0, False))
        self.assertIsNone(self.fx.on)
        self.clock.t += 1
        st, body, _ = self.post("/api/strobe/renew", {"holder": "tab"})
        self.assertEqual(st, 409)
        self.assertIn("press again", body["error"])
        self.assertEqual(self.post("/api/strobe/release", {"holder": "tab"})[0], 200)       # letting go twice is fine

    def test_a_lapsed_hold_is_ended_by_the_watcher_through_the_real_engine(self):
        self.post("/api/strobe/hold", {"holder": "tab"})
        self.assertEqual(self.fx.on["id"], "fx-strobe.fs")
        self.clock.t += 0.6
        self.strobe.tick()
        self.assertIsNone(self.fx.on)
        self.assertEqual(self.get("/api/strobe")[1]["held"], 0)

    def test_what_was_on_comes_back_through_the_real_engine(self):
        self.fx.put("fx-wash.fs", {"strength": 0.5})
        self.post("/api/strobe/hold", {})
        self.assertEqual(self.fx.on["id"], "fx-strobe.fs")
        self.post("/api/strobe/release", {})
        self.assertEqual((self.fx.on["id"], self.fx.on["values"]["strength"]), ("fx-wash.fs", 0.5))
        self.assertEqual(self.fx.recent, "fx-wash.fs")

    def test_the_settings_route_and_what_it_refuses(self):
        st, body, _ = self.post("/api/strobe", {"rate": 1.5, "colour": "#ff0000", "flash": 40, "way": "black"})
        self.assertEqual((st, body["settings"]), (200, {"rate": 1.5, "colour": "#ff0000", "flash": 40, "way": "black"}))
        for bad in ({"rate": "x"}, {"colour": "red"}, {"nope": 1}, {"holder": "x"}):
            st, body, _ = self.post("/api/strobe", bad)
            self.assertEqual(st, 400, bad)
        st, body, _ = self.post("/api/strobe/hold", {"holder": "a;b"})
        self.assertEqual(st, 400)
        self.assertEqual(self.get("/api/strobe")[1]["held"], 0)

    def test_the_module_off_is_409_and_nothing_is_held(self):
        self.api.registry.set_enabled("shaders", False)
        for path in ("/api/strobe/hold", "/api/strobe/renew"):
            self.assertEqual(self.post(path, {})[0], 409, path)
        self.assertEqual(self.get("/api/strobe")[1]["available"], False)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_a_start_that_fails_is_an_error_and_holds_nothing(self):
        self.player.ipc.down = True                             # the player is not running
        st, body, _ = self.post("/api/strobe/hold", {})
        self.assertGreaterEqual(st, 409, body)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_the_effect_is_in_the_library_marked_as_flashing_and_never_stepped_onto(self):
        rows = {s["id"]: s for s in self.state()["effects"]}
        row = rows["fx-strobe.fs"]
        self.assertTrue(row["flashes"] and row["moves"])
        self.assertEqual((row["speed_max"], row["weight"] in ("light", "medium"), row["measured"]), (1.0, True, None))
        self.assertEqual({i["name"] for i in row["inputs"]}, {"rate", "tint", "flash", "way"})
        self.assertIn("fx-strobe.fs", E.HOLD_ONLY)
        self.assertNotIn("fx-strobe.fs", self.fx.order())
        self.assertEqual(self.api.vibes_ids() if hasattr(self.api, "vibes_ids") else [], [])
        self.assertEqual(self.fx.vibes_ids(), [])                           # an effect is never in a rotation
        self.api.settings.data["shaders"] = dict(self.api.settings.data.get("shaders") or {}, faster=True)
        self.assertEqual({s["id"]: s for s in self.state()["effects"]}["fx-strobe.fs"]["speed_max"], 4.0)

    def test_put_by_the_effects_routes_it_still_has_the_cap_in_its_filter_and_in_the_speed(self):
        self.fx.put("fx-strobe.fs", {"rate": 99.0}, {"speed": 4})
        on = self.state()["on"]
        self.assertEqual((on["values"]["rate"], on["controls"]["speed"]), (3.0, 1.0))
        text = self.text()
        self.assertRegex(text, r"clamp\(rate, 0\.0, 3\.0\)")
        self.api.settings.data["shaders"] = dict(self.api.settings.data.get("shaders") or {}, faster=True)
        self.fx.put("fx-strobe.fs", {"rate": 99.0}, {"speed": 4})
        on = self.state()["on"]
        self.assertEqual((on["values"]["rate"], on["controls"]["speed"]), (3.0, 4.0))


class FilterTest(unittest.TestCase):
    def test_the_filter_caps_its_own_rate_and_has_a_flash_that_is_never_more_than_half_the_gap(self):
        path = os.path.join(E.EFFECTS_DIR, "fx-strobe.fs")
        with open(path) as f:
            source = f.read()
        parsed = S.parse(source, S.FILTER)
        self.assertTrue(parsed["clock"] and parsed["flashes"])
        rate = next(i for i in parsed["inputs"] if i["name"] == "rate")
        self.assertEqual((rate["min"], rate["max"]), (0.5, 3.0))
        self.assertIn("clamp(rate, 0.0, 3.0)", source)
        self.assertIn("fract(TIME * min(rate, 3.0))", source)           # the place in the cycle is capped on its own, as the project's other filters do
        self.assertIn("min(clamp(flash, 10.0, 250.0) * 0.001 * hz, 0.5)", source)
        for text in (E.translate(parsed), E.translate_native(parsed)):
            self.assertIn("clamp(rate, 0.0, 3.0)", text)
        self.assertEqual(E.estimate(parsed)["weight"], "light")

    def test_a_filter_text_cannot_be_given_a_rate_above_the_inputs_own_maximum(self):
        parsed = S.parse(open(os.path.join(E.EFFECTS_DIR, "fx-strobe.fs")).read(), S.FILTER)
        self.assertEqual(S.clean_values(parsed, {"rate": 99})["rate"], 3.0)
        self.assertEqual(S.clean_values(parsed, {"rate": -1})["rate"], 0.5)


# ---- MIDI --------------------------------------------------------------------------------------------------------------
class MidiMapperTest(unittest.TestCase):
    def mapper(self, kind="note", number=60, action="strobe_hold", **more):
        calls = []
        entry = midi.validate_entry(dict({"kind": kind, "number": number, "action": action, "source": "Mini"}, **more))
        return midi.MidiMapper(lambda p, b: calls.append((p, b)) or True, [entry], {}), calls

    def test_note_on_holds_and_note_off_releases_and_a_repeat_asks_for_nothing(self):
        m, calls = self.mapper()
        m.message("Mini", ("on", 0, 60, 100))
        m.message("Mini", ("on", 0, 60, 90))                    # a controller that repeats: the same press
        self.assertEqual(calls, [("/api/strobe/hold", {"holder": "Mini:note60"})])
        m.message("Mini", ("off", 0, 60, 0))
        m.message("Mini", ("off", 0, 60, 0))
        self.assertEqual(calls[1:], [("/api/strobe/release", {"holder": "Mini:note60"})])

    def test_a_note_on_with_velocity_zero_is_a_release_and_a_cc_is_down_from_64(self):
        m, calls = self.mapper()
        m.message("Mini", ("on", 0, 60, 1))
        m.message("Mini", ("on", 0, 60, 0))
        self.assertEqual([c[0] for c in calls], ["/api/strobe/hold", "/api/strobe/release"])
        m, calls = self.mapper("cc", 20)
        for v in (10, 63, 64, 127, 70, 63, 0):
            m.message("Mini", ("cc", 0, 20, v))
        self.assertEqual([c[0] for c in calls], ["/api/strobe/hold", "/api/strobe/release"])
        self.assertEqual(calls[0][1], {"holder": "Mini:cc20"})

    def test_lost_messages_clear_a_cc_pad_too_so_its_next_press_is_a_press(self):
        m, calls = self.mapper("cc", 20)
        m.message("Mini", ("cc", 0, 20, 127))
        m.forget_held("Mini", pressed=True)                     # messages were lost: the release may be among them
        m.message("Mini", ("cc", 0, 20, 127))
        self.assertEqual([c[0] for c in calls], ["/api/strobe/hold", "/api/strobe/hold"])

    def test_it_is_a_note_or_a_cc_never_a_program_change_and_never_a_level(self):
        with self.assertRaises(midi.MidiError):
            midi.validate_entry({"kind": "program", "number": 3, "action": "strobe_hold"})
        self.assertEqual(midi.ACTIONS["strobe_hold"][0], "gate")
        e = midi.validate_entry({"kind": "cc", "number": 21, "action": "strobe_rate", "source": "Mini"})
        self.assertEqual(e["action"], "strobe_rate")

    def test_the_rate_follows_a_knob_in_flashes_a_second(self):
        m, calls = self.mapper("cc", 21, "strobe_rate")
        m.message("Mini", ("cc", 0, 21, 0))
        self.assertEqual(calls, [("/api/strobe", {"rate": 0.5})])
        m._last.clear()
        m.message("Mini", ("cc", 0, 21, 127))
        self.assertEqual(calls[-1], ("/api/strobe", {"rate": 3.0}))
        self.assertEqual(actions.level("strobe_rate", 1.234), ("/api/strobe", {"rate": 1.23}))
        self.assertIsNone(actions.press("strobe_hold"))                  # a hold is not a press: MIDI and OSC each own theirs


class MidiHubTest(RouteBase):
    """The hub with pipes in the place of controllers, the real strobe and the real engines behind."""
    PATH = "/dev/snd/midiC1D0"

    def setUp(self):
        super().setUp()
        self.call("POST", "/api/modules/control-midi", {"enabled": True}, token=self.full)
        self.pipes, self.present = {}, []

        def open_fn(path):
            r, w = os.pipe()
            self.pipes[path] = (r, w)
            return r
        self.hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=open_fn, lister=lambda: list(self.present),
                                namer=lambda p: "Mini", scan_interval=0.05, profiles=[])
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)
        self.strobe._use_thread = False
        self.settings.data["control"]["midi"]["map"] = [
            midi.validate_entry({"kind": "note", "number": 60, "action": "strobe_hold", "source": "Mini"}),
            midi.validate_entry({"kind": "cc", "number": 21, "action": "strobe_rate", "source": "Mini"})]
        self.settings.data["control"]["midi"]["enabled"] = True
        self.present = [self.PATH]
        self.hub.apply()
        self.wait(lambda: self.PATH in self.pipes)

    def wait(self, cond, timeout=4):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.01)
        self.assertTrue(cond(), "condition not met")

    def send(self, data):
        os.write(self.pipes[self.PATH][1], bytes(data))

    def test_note_on_then_note_off_through_a_reader_thread(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.strobe.state()["held"] == 1)
        self.wait(lambda: self.fx.on is not None and self.fx.on["id"] == "fx-strobe.fs")
        self.send([0x80, 60, 0])
        self.wait(lambda: self.fx.on is None)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_the_hub_renews_while_the_control_is_down_and_the_device_is_read(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.strobe.state()["held"] == 1)
        first = self.strobe.holders["midi:Mini:note60"]["seen"]
        self.clock.t += 0.3
        self.wait(lambda: self.strobe.holders.get("midi:Mini:note60", {}).get("seen") == self.clock.t, timeout=2)
        self.assertGreater(self.strobe.holders["midi:Mini:note60"]["seen"], first)

    def test_a_cable_pulled_while_it_is_down_ends_it_at_once_through_the_hub(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        pipe = self.pipes.pop(self.PATH)
        self.present = []
        began = time.monotonic()
        os.close(pipe[1])                                       # unplugged: no note-off will ever come
        self.wait(lambda: self.fx.on is None, timeout=2)
        self.assertLess(time.monotonic() - began, 0.5 + 0.3)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_the_lease_alone_ends_it_if_nothing_renews_it_and_nothing_releases_it(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        self.hub._strobe.clear()                                # the hub forgot it (as if its thread had died)
        self.clock.t += ST.CONTROLLER_TTL + 1
        self.strobe.tick()
        self.assertIsNone(self.fx.on)

    def test_midi_switched_off_or_lost_messages_let_go(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        self.hub.on_message("Mini", midi.LOST)
        self.assertIsNone(self.fx.on)
        self.assertEqual(self.strobe.state()["held"], 0)
        self.clock.t += ST.GAP + 0.1
        self.hub.on_message("Mini", ("off", 0, 60, 0))
        self.hub.on_message("Mini", ("on", 0, 60, 100))
        self.wait(lambda: self.fx.on is not None)
        self.settings.data["control"]["midi"]["enabled"] = False
        self.hub.apply()
        self.assertIsNone(self.fx.on)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_a_release_is_not_dropped_by_the_hubs_limiter(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        self.hub.calls.allow = lambda key: False               # a flood of fader moves has used the 50 a second up
        self.send([0x80, 60, 0])
        self.wait(lambda: self.fx.on is None, timeout=2)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_a_release_that_failed_is_asked_again_directly(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        real = self.hub._do
        self.hub._do = lambda path, body: False if path == midi.STROBE_RELEASE else real(path, body)
        self.send([0x80, 60, 0])
        self.wait(lambda: self.fx.on is None, timeout=2)

    def test_a_renewal_the_node_cannot_take_lets_go_and_one_it_refuses_as_ended_does_not(self):
        self.send([0x90, 60, 100])
        self.wait(lambda: self.fx.on is not None)
        orig = self.api.handle
        self.api.handle = lambda m, p, b, d, c: (503, {"error": "x"}) if p == midi.STROBE_RENEW else orig(m, p, b, d, c)
        self.wait(lambda: self.fx.on is None, timeout=2)
        self.assertEqual(self.hub._strobe, {})

    def test_the_rate_knob_reaches_the_node_and_is_kept_to_its_cap(self):
        self.send([0xB0, 21, 127])
        self.wait(lambda: self.strobe.cfg["rate"] == 3.0)
        self.settings.data["control"]["midi"]["map"] = [midi.validate_entry({"kind": "cc", "number": 22, "action": "strobe_rate", "source": "Mini", "max": 12})]
        self.send([0xB0, 22, 127])
        self.wait(lambda: self.hub.mapper.last_message.endswith("22 127"))
        time.sleep(0.1)
        self.assertEqual(self.strobe.cfg["rate"], 3.0)          # the mapping asked for 12; the node does not give it without the opt-in


# ---- OSC ---------------------------------------------------------------------------------------------------------------
def tr(address, *args):
    (a, parsed), = osc.parse_packet(msg(address, *args))
    return osc.translate(a, parsed, {})


class OscTest(unittest.TestCase):
    def test_one_and_zero_hold_and_release_and_nothing_starts_without_an_argument(self):
        self.assertEqual(tr("/pvj/strobe", 1), ("/api/strobe/hold", {}))
        self.assertEqual(tr("/pvj/strobe", 1.0), ("/api/strobe/hold", {}))
        self.assertEqual(tr("/pvj/strobe", True), ("/api/strobe/hold", {}))
        self.assertEqual(tr("/pvj/strobe", 0), ("/api/strobe/release", {}))
        self.assertEqual(tr("/pvj/strobe", False), ("/api/strobe/release", {}))
        self.assertIsNone(tr("/pvj/strobe"))
        self.assertIsNone(tr("/pvj/strobe", "on"))
        self.assertIsNone(tr("/pvj/strobefoo", 1))

    def test_rate_colour_flash_and_way(self):
        self.assertEqual(tr("/pvj/strobe/rate", 2.5), ("/api/strobe", {"rate": 2.5}))
        self.assertEqual(tr("/pvj/strobe/colour", "#ff0000"), ("/api/strobe", {"colour": "#ff0000"}))
        self.assertEqual(tr("/pvj/strobe/color", 1.0, 0.0, 0.0), ("/api/strobe", {"colour": [1.0, 0.0, 0.0]}))
        self.assertEqual(tr("/pvj/strobe/flash", 80), ("/api/strobe", {"flash": 80}))
        self.assertEqual(tr("/pvj/strobe/way", "black"), ("/api/strobe", {"way": "black"}))
        for address in ("/pvj/strobe/rate", "/pvj/strobe/colour", "/pvj/strobe/flash", "/pvj/strobe/way"):
            self.assertIsNone(tr(address), address)
        self.assertIsNone(tr("/pvj/strobe/rate", "fast"))
        self.assertIsNone(tr("/pvj/strobe/colour", 1.0, 2.0))
        self.assertIsNone(tr("/pvj/strobe/other", 1))


class OscHandlerTest(RouteBase):
    SENDER = "192.168.1.20"

    def setUp(self):
        super().setUp()
        self.strobe._use_thread = False

    def make_server(self, key="", **rules):
        data = dict(self.settings.data["osc"], **rules)
        return osc.OscServer(self.api, log=lambda *_: None, rules=data, key=key, watch=osc.Watch(time.monotonic),
                             extra_allow=osc.parse_networks(data.get("allow", [])))

    def test_a_one_holds_a_zero_lets_go_and_the_sender_is_the_holder(self):
        server = self.make_server()
        self.assertEqual(server.handle_packet(msg("/pvj/strobe", 1), self.SENDER), 1)
        self.assertEqual(list(self.strobe.holders), ["osc:" + self.SENDER])
        self.assertEqual(self.fx.on["id"], "fx-strobe.fs")
        self.assertEqual(server.handle_packet(msg("/pvj/strobe", 1), "192.168.1.21"), 1)        # a second sender is a second holder
        self.assertEqual(self.strobe.state()["held"], 2)
        server.handle_packet(msg("/pvj/strobe", 0), self.SENDER)
        self.assertEqual(list(self.strobe.holders), ["osc:192.168.1.21"])
        self.assertIsNotNone(self.fx.on)
        server.handle_packet(msg("/pvj/strobe", 0), "192.168.1.21")
        self.assertIsNone(self.fx.on)

    def test_stopping_the_listener_lets_go(self):
        server = self.make_server()
        server.handle_packet(msg("/pvj/strobe", 1), self.SENDER)
        self.assertIsNotNone(self.fx.on)
        server.stop()
        self.assertIsNone(self.fx.on)
        self.assertEqual(self.strobe.state()["held"], 0)

    def test_the_lockdown_layers_stand_before_the_strobe(self):
        from tests.test_osc_lockdown import KEY
        server = self.make_server(only_on=True, only=["10.0.0.5"])
        self.assertEqual(server.handle_packet(msg("/pvj/strobe", 1), self.SENDER), 0)       # not on the list of devices
        self.assertIsNone(self.fx.on)
        self.assertEqual(self.strobe.state()["held"], 0)
        keyed = self.make_server(key=KEY, key_on=True)
        self.assertEqual(keyed.handle_packet(msg("/pvj/strobe", 1), self.SENDER), 0)        # no key in the address
        self.assertIsNone(self.fx.on)
        self.assertEqual(keyed.handle_packet(msg("/k/%s/pvj/strobe" % KEY, 1), self.SENDER), 1)
        self.assertIsNotNone(self.fx.on)

    def test_a_lease_from_osc_ends_in_half_a_second_without_a_repeat_and_a_repeat_keeps_it(self):
        server = self.make_server()
        server.handle_packet(msg("/pvj/strobe", 1), self.SENDER)
        self.assertIsNotNone(self.fx.on)
        self.clock.t += 0.3
        server.handle_packet(msg("/pvj/strobe", 1), self.SENDER)         # repeated while held
        self.clock.t += 0.4
        self.strobe.tick()
        self.assertIsNotNone(self.fx.on)
        self.clock.t += 0.2                                              # and then nothing
        self.strobe.tick()
        self.assertIsNone(self.fx.on)

    def test_the_rate_over_osc_is_kept_to_the_cap(self):
        server = self.make_server()
        server.handle_packet(msg("/pvj/strobe/rate", 40.0), self.SENDER)
        self.assertEqual(self.strobe.cfg["rate"], 3.0)
        server.handle_packet(msg("/pvj/strobe/colour", "#00ff00"), self.SENDER)
        self.assertEqual(self.strobe.cfg["colour"], "#00ff00")


class RolesTest(RouteBase):
    def test_a_guest_cannot_touch_the_strobe_with_guest_controls_open_or_locked(self):
        guest = self.call("POST", "/api/pair", {"pin": self.pin, "name": "g"})[1]["token"]
        # (the Owner's PIN pairs an Owner: a Guest is made by an invitation, as tests/test_roles.py does; here the table is held)
        self.assertNotIn(("POST", "/api/strobe/hold"), policy.GUEST)
        for key in (("POST", "/api/strobe"), ("POST", "/api/strobe/hold"), ("POST", "/api/strobe/renew"), ("POST", "/api/strobe/release"), ("GET", "/api/strobe")):
            self.assertNotIn(key, policy.GUEST)
        self.assertIn(("POST", "/api/strobe/hold"), policy.LEGACY_LIVE)
        self.assertNotIn(("GET", "/api/strobe"), policy.LEGACY_LIVE)
        self.assertEqual(self.api.routes()[("POST", "/api/strobe/hold")][0], "live")


# ---- the real renderer -------------------------------------------------------------------------------------------------
from tests.test_pvj_render import Renderer, need_program         # noqa: E402
from pvj import paths                                            # noqa: E402
from pvj.player import Player as RealPlayer                                    # noqa: E402


class NativeTest(unittest.TestCase):
    """The strobe on the New renderer through the real Player: loaded once, then a hold is a value."""

    def setUp(self):
        need_program(self)
        from tests.test_server import ServerBase
        self.server = ServerBase("run")
        self.server.setUp()
        self.addCleanup(self.server.doCleanups)
        self.r = Renderer("--sources=4")
        self.addCleanup(lambda: self.r.close())
        os.symlink(self.r.sock, os.path.join(self.server.rundir, paths.PLAYER_SOCKET))
        self.api = self.server.api
        self.player = RealPlayer(rundir=self.server.rundir)
        self.api.player = self.player
        self.api.registry.set_enabled("shaders", True)
        self.api.log = lambda *_: None
        self.clock = Clock()
        self.strobe = self.api.strobe = ST.Strobe(self.api, clock=self.clock, thread=False, log=lambda *_: None)
        self.addCleanup(self.strobe.stop)

    def slot(self, k):
        return [s for s in self.r("get_property", "pvj-shaders") if s["source"] == 1 and s["slot"] == k][0]

    def test_a_hold_is_a_value_on_the_loaded_copy_and_a_release_is_a_value(self):
        out = self.strobe.set({"arm": True})
        self.assertTrue(out["armed"] and out["path"] == "chain")
        path = self.api.chains.on[(1, 3)]["path"]
        self.assertEqual(self.api.chains.on[(1, 3)]["controls"]["amount"], 0.0)
        began = time.monotonic()
        self.strobe.hold({"id": "ab12cd34", "role": "live"}, {"holder": "t"})
        took = time.monotonic() - began
        self.assertEqual(self.api.chains.on[(1, 3)]["controls"]["amount"], 1.0)
        self.assertEqual(self.api.chains.on[(1, 3)]["path"], path)              # no new file: a value, not a build
        self.assertLess(took, 0.25, took)
        self.assertEqual(self.api.chains.state()["chains"][0]["id"], "fx-strobe.fs")
        self.strobe.release({"id": "ab12cd34", "role": "live"}, {"holder": "t"})
        self.assertEqual(self.api.chains.on[(1, 3)]["controls"]["amount"], 0.0)
        self.assertEqual(self.api.chains.on[(1, 3)]["path"], path)
        self.assertEqual([n for n in os.listdir(self.server.rundir) if n.startswith("nxlx-chain")], [os.path.basename(path)])
        self.strobe.set({"arm": False})
        self.assertEqual(self.api.chains.state()["chains"], [])
        self.assertEqual([n for n in os.listdir(self.server.rundir) if n.startswith("nxlx-chain")], [])

    def test_colour_and_way_are_set_while_it_flashes_with_no_new_file(self):
        self.strobe.hold({"id": "ab12cd34", "role": "live"}, {"holder": "t"})
        path = self.api.chains.on[(1, 3)]["path"]
        self.strobe.set({"colour": "#ff0000", "way": "black"})
        self.assertEqual(self.api.chains.on[(1, 3)]["path"], path)
        self.assertEqual(self.api.chains.on[(1, 3)]["values"]["tint"], [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(self.api.chains.on[(1, 3)]["values"]["way"], 1)
        self.strobe.release({"id": "ab12cd34", "role": "live"}, {"all": True})

    def test_a_restart_of_the_panel_with_the_slot_at_amount_1_ends_with_no_flashing(self):
        from pvj import fxchain
        self.strobe.hold({"id": "ab12cd34", "role": "live"}, {"holder": "t"})
        self.assertEqual(self.slot(3)["state"], "on")
        self.api.chains = fxchain.Chains(self.api)                # the panel restarted: empty records, no holders, slot None
        fresh = self.api.strobe = ST.Strobe(self.api, clock=self.clock, thread=False, log=lambda *_: None)
        self.addCleanup(fresh.stop)
        self.assertTrue(fresh.reset_player(tries=1))
        self.assertEqual(self.slot(3)["state"], "off")
        self.assertEqual(self.slot(2)["state"], "off")

    def test_a_stop_of_source_one_takes_the_loaded_copy_off_and_the_next_hold_loads_it_again(self):
        self.strobe.set({"arm": True})
        self.player.clear()                              # source 1's Stop takes its chain off
        self.assertEqual(self.api.chains.state()["chains"], [])
        self.strobe.hold({"id": "ab12cd34", "role": "live"}, {"holder": "t"})
        self.assertEqual(self.api.chains.on[(1, 3)]["controls"]["amount"], 1.0)
        self.strobe.release({"id": "ab12cd34", "role": "live"}, {"all": True})


from tests.test_native_shaders import NativeBase                 # noqa: E402


class ShaderTimingTest(NativeBase):
    """The filter's own cap, on the real program: even if every check before it were gone and the renderer were given a
    rate of 99 flashes a second, the filter flashes at most 3 a second."""

    def edges(self, seconds):
        """How many times the picture went from its own to the strobe's colour in `seconds`, sampled as fast as it can be read."""
        count, was, samples = 0, False, 0
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            px = self.r.pixel(5, 5)
            on = min(px) > 200                                  # white: the picture's corner is dark blue
            if on and not was:
                count += 1
            was = on
            samples += 1
        return count, samples

    def put(self, **values):
        parsed = S.parse(open(os.path.join(E.EFFECTS_DIR, "fx-strobe.fs"), encoding="utf-8").read(), S.FILTER, passes=True)
        self.r("pvj-shader", "load", 1, 1, self.write(E.translate_native(parsed, values={"flash": 250.0}, controls={"amount": 1.0, "speed": 1.0})))
        self.assertTrue(self.r.wait(lambda: self.slot(1)["state"] != "off", 5))
        self.r("pvj-shader", "set", 1, 1, dict({"flash": 250.0, "way": 0}, **values))

    def test_a_rate_of_99_flashes_no_faster_than_3_a_second(self):
        self.picture()
        self.put(rate=3.0)
        base_count, base_samples = self.edges(4.0)
        self.assertGreaterEqual(base_samples, 20, "the picture could not be read fast enough to count flashes")
        self.assertGreaterEqual(base_count, 8, (base_count, base_samples))              # it does flash, about 3 a second
        self.assertLessEqual(base_count, 14, (base_count, base_samples))
        self.put(rate=99.0)                                                              # nothing in the way: the program is given 99
        count, samples = self.edges(4.0)
        self.assertLessEqual(count, 14, (count, samples))                                # 3 a second for 4 seconds, with room for the sampling
        self.assertGreaterEqual(count, 6, (count, samples))


# ---- the review of PR 281 -------------------------------------------------------------------------------------------------
class PlaybackSpeedTest(unittest.TestCase):
    def test_the_rate_sent_is_divided_by_the_mix_screens_speed_on_both_paths(self):
        for native in (True, False):
            for speed, want in ((1.0, 3.0), (2.0, 1.5), (4.0, 0.75), (0.5, 3.0)):
                with self.subTest(native=native, speed=speed):
                    api, clock, s = rig(native)
                    api.levels["speed"] = speed
                    s.set({"rate": 3})
                    s.hold(DEVICE, {"holder": "t"})
                    sent = api.chains.rows[(1, 3)]["values"]["rate"] if native else api.effects.on["values"]["rate"]
                    self.assertEqual(sent, want)
                    self.assertLessEqual(sent * max(speed, 1.0), ST.CAP + 1e-9)       # flashes a second in the end: never above 3

    def test_a_change_of_speed_while_it_is_held_is_a_change_of_timing(self):
        api, clock, s = rig()
        s.set({"rate": 3})
        s.hold(DEVICE, {"holder": "t"})
        api.levels["speed"] = 4.0
        clock.t += 0.2
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        self.assertFalse(lit(api))                              # the old timing ended at once ...
        clock.t += ST.GAP + 0.01
        s.hold(DEVICE, {"holder": "t"}, renew=True)
        s.tick()
        self.assertTrue(lit(api))                               # ... and the new one starts a gap later
        self.assertEqual(api.chains.rows[(1, 3)]["values"]["rate"], 0.75)


class FallbackAndArmTest(unittest.TestCase):
    def test_the_renderer_falling_back_mid_hold_does_not_wedge_it_or_fill_the_log(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        api.player.native = False                               # pvj-render fell back to classic: there is no chain
        s.release(DEVICE, {"holder": "t"})
        self.assertFalse(s.lit)
        self.assertEqual((s.route, s.slot), (None, None))
        before = len(api.lines)
        for _ in range(100):
            clock.t += ST.TICK
            s.tick()
        self.assertEqual(len(api.lines), before)

    def test_a_stop_that_keeps_failing_says_so_once_in_ten_seconds(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        api.chains.fail_values = 10 ** 6
        api.chains.fail_off = True
        s.release(DEVICE, {"holder": "t"})
        for _ in range(150):
            clock.t += ST.TICK
            s.tick()
        self.assertLessEqual(len(api.lines), 6, api.lines)     # 7.5 seconds of ticks: a line or two for each of two kinds
        self.assertTrue(s.lit)

    def test_a_player_that_does_not_answer_is_not_taken_for_a_fallback(self):
        api, clock, s = rig()
        s.hold(DEVICE, {"holder": "t"})
        def boom():
            raise RuntimeError("no answer")
        api.player.native_chains = boom
        api.chains.fail_values = 10 ** 6
        api.chains.fail_off = True
        s.release(DEVICE, {"holder": "t"})
        self.assertTrue(s.lit)                                  # still known to be lit: tried again

    def test_arm_false_while_it_is_lit_changes_nothing_and_a_release_clears_only_its_own_slot(self):
        api, clock, s = rig()
        api.chains.rows[(1, 2)] = {"id": "fx-grade.fs", "amount": 1.0, "speed": 1.0, "values": {}}
        s.hold(DEVICE, {"holder": "t"})
        out = s.set({"arm": False})
        self.assertTrue(out["armed"] and out["on"])
        s.release(DEVICE, {"holder": "t"})
        self.assertFalse(lit_strobe(api))
        self.assertEqual(api.chains.rows[(1, 2)]["id"], "fx-grade.fs")
        self.assertNotIn(("off", None), api.chains.calls)
        s.set({"arm": False})                                   # when it is not lit, it is taken off
        self.assertNotIn((1, 3), api.chains.rows)
        self.assertIn((1, 2), api.chains.rows)


def lit_strobe(api):
    return any(r["amount"] > 0 for r in api.chains.rows.values() if r["id"] == ST.EFFECT)


class RestartTest(unittest.TestCase):
    def test_the_leftover_is_taken_off_with_a_back_off_until_the_player_answers(self):
        calls, waits = [], []

        class Ipc:
            n = 0

            def request(self, *c):
                Ipc.n += 1
                calls.append(c)
                if Ipc.n <= 2:
                    raise PlayerError("player is not running")
                if c[:2] == ("get_property", "pvj-shaders"):
                    return [{"source": 1, "slot": 3, "state": "on", "path": path}]
                return None
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "x.prog")
        parsed = S.parse(open(os.path.join(E.EFFECTS_DIR, "fx-strobe.fs")).read(), S.FILTER, passes=True)
        open(path, "w").write(E.translate_native(parsed))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        api = Api()
        api.player.ipc = Ipc()
        s = ST.Strobe(api, thread=False)
        self.assertTrue(s.reset_player(tries=5, delay=1.0, sleep=waits.append))
        self.assertEqual(waits, [1.0, 2.0])
        self.assertIn(("pvj-shader", "clear", 1, 3), calls)

    def test_another_program_in_the_slot_is_left_alone_and_a_used_strobe_is_not_reset(self):
        calls = []

        class Ipc:
            def request(self, *c):
                calls.append(c)
                if c[:2] == ("get_property", "pvj-shaders"):
                    return [{"source": 1, "slot": 3, "state": "on", "path": "/nonexistent/x.prog"}]
        api = Api()
        api.player.ipc = Ipc()
        s = ST.Strobe(api, thread=False)
        self.assertTrue(s.reset_player(tries=1))
        self.assertNotIn(("pvj-shader", "clear", 1, 3), calls)
        s.hold(DEVICE, {})
        calls.clear()
        self.assertTrue(s.reset_player(tries=1))
        self.assertEqual(calls, [])

    def test_a_restart_with_the_slot_lit_on_the_classic_path(self):
        pass


class ClassicRestartTest(RouteBase):
    def test_a_strobe_left_in_mpvs_list_is_taken_off_at_start_up(self):
        self.post("/api/strobe/hold", {"holder": "tab"})
        self.assertTrue(any("effect" in n for n in self.mpv.loaded))
        self.fx.on = None                                       # the panel restarted: it knows nothing
        fresh = ST.Strobe(self.api, clock=self.clock, thread=False, log=lambda *_: None)
        self.assertTrue(fresh.reset_player(tries=1))
        self.assertEqual(self.mpv.props["glsl-shaders"], [])
        fresh.stop()


if __name__ == "__main__":
    unittest.main()
