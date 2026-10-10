# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""D75: how a level follows a knob (its range, its centre, its curve, its takeover), the actions a controller gained,
the one fade button and its flashing light.

Nothing here has touched a real controller: a fake clock, pipes and direct calls stand in for the devices."""
import os
import threading
import time
import unittest

from pvj import midi
from pvj.midi import MidiMapper
from tests.test_controllers import BY_ID, HubBase, MIX, NANO, PAD
from tests.test_lights import C_MIX, C_NANO, C_PAD, LightsHubBase, WriterBase, snap, value
from tests.test_midi import Recorder
from tests.test_server import ServerBase


class LevelShapeTest(unittest.TestCase):
    """The transfer function itself, with exact values."""

    def test_the_middle_of_a_knob_is_exactly_the_centre(self):
        for action, centre in (("size", 100.0), ("position", 0.0), ("position_y", 0.0), ("speed", 1.0), ("shader_speed", 1.0),
                               ("shader_hue", 0.0), ("shader_brightness", 1.0)):
            got = {v: midi.level_value(action, v) for v in range(128)}
            self.assertEqual({got[v] for v in range(60, 69)}, {centre}, action)       # nine steps around 64: no knob has a notch
            self.assertNotEqual(got[59], centre, action)
            self.assertNotEqual(got[69], centre, action)
            self.assertEqual([got[v] <= got[v + 1] for v in range(127)], [True] * 127, action)      # never backwards
        self.assertEqual(midi.CENTRE_HALF, 4)

    def test_the_ranges_and_the_ends(self):
        ends = {a: (midi.level_value(a, 0), midi.level_value(a, 127)) for a in midi.SHAPES}
        self.assertEqual(ends, {"opacity": (0.0, 100.0), "volume": (0.0, 100.0), "effect_amount": (0.0, 1.0),
                                "size": (25.0, 200.0),          # not 1 to 200: under 25 percent a picture is a speck
                                "position": (-100.0, 100.0), "position_y": (-100.0, 100.0), "speed": (0.25, 2.0),
                                "shader_speed": (0.0, 4.0), "shader_hue": (-180.0, 180.0), "shader_brightness": (0.0, 2.0)})
        # what a mapping may ask for at most is the API's own range, not the default one
        self.assertEqual((midi.ACTIONS["size"][1:], midi.ACTIONS["position_y"]), ((1, 200), ("level", -100, 100)))

    def test_the_curve_is_fine_near_the_centre_and_coarse_at_the_ends(self):
        size = [midi.level_value("size", v) for v in range(128)]
        self.assertEqual(round(size[69], 3), round(100 + 100 * (1 / 59.0) ** 1.6, 3))       # the first step off the centre
        self.assertLess(size[69] - size[68], 0.2)                       # a seventh of a percent: no visible jump
        self.assertGreater(size[127] - size[126], 2.5)
        self.assertEqual(round(size[98], 2), round(100 + 100 * (30 / 59.0) ** 1.6, 2))      # half way up the upper half: about 134
        self.assertLess(size[98], 150)
        pos = [midi.level_value("position", v) for v in range(128)]
        self.assertLess(pos[69], 0.2)
        self.assertEqual(round(pos[30], 3), round(-100 * (30 / 60.0) ** 1.6, 3))
        for a in ("speed", "shader_speed", "shader_hue", "shader_brightness"):      # two straight halves that meet at the centre
            self.assertEqual(midi.SHAPES[a][3], 1.0)
        self.assertEqual((midi.level_value("speed", 30), midi.level_value("speed", 98)), (0.625, 1 + 30 / 59.0))
        self.assertEqual(midi.level_value("shader_speed", 30), 0.5)
        # a level with no centre is a straight line, as before
        self.assertEqual([midi.level_value("opacity", v) for v in (0, 64, 127)], [0.0, 100 * 64 / 127.0, 100.0])

    def test_a_mappings_own_range_and_direction(self):
        self.assertEqual([midi.level_value("size", v, {"min": 1, "max": 200}) for v in (0, 64, 127)], [1.0, 100.0, 200.0])
        self.assertEqual([midi.level_value("size", v, {"min": 100, "max": 150}) for v in (0, 127)], [100.0, 150.0])       # 100 is an end: no centre
        self.assertEqual(midi.level_shape("size", {"min": 100, "max": 150}), (100, 150, None, 1.0))
        self.assertEqual(round(midi.level_value("size", 64, {"min": 100, "max": 150}), 3), round(100 + 50 * 64 / 127.0, 3))
        self.assertEqual([midi.level_value("position", v, {"invert": True}) for v in (0, 64, 127)], [100.0, 0.0, -100.0])
        self.assertEqual([midi.level_value("opacity", v, {"min": 20, "max": 80, "invert": True}) for v in (0, 127)], [80.0, 20.0])

    def test_the_way_back_is_the_same_curve(self):
        for action in midi.SHAPES:
            for opts in (None, {"invert": True}):
                for v in range(128):
                    back = midi.level_position(action, midi.level_value(action, v, opts), opts)
                    self.assertEqual(midi.level_value(action, back, opts), midi.level_value(action, v, opts), (action, v))
        self.assertEqual([midi.level_position(a, c) for a, c in (("size", 100), ("position", 0), ("speed", 1.0))], [64, 64, 64])
        self.assertEqual((midi.level_position("size", 10), midi.level_position("size", 999)), (0, 127))     # outside the range: the nearest end
        self.assertEqual(midi.level_position("size", 200, {"invert": True}), 0)
        self.assertEqual(midi.level_position("size", 150), next(v for v in range(128) if midi.level_value("size", v) >= 150 - 1.2))

    def test_what_a_mapping_may_say(self):
        ok = midi.validate_entry({"kind": "cc", "number": 1, "action": "size", "min": 50, "max": 150, "invert": True, "takeover": "jump"})
        self.assertEqual({k: ok[k] for k in midi.OPTION_KEYS}, {"min": 50.0, "max": 150.0, "invert": True, "takeover": "jump"})
        self.assertEqual(midi.clean_action({"action": "position_y", "takeover": "pickup"}, "cc"), {"action": "position_y", "takeover": "pickup"})
        plain = midi.validate_entry({"kind": "cc", "number": 1, "action": "size"})
        self.assertFalse([k for k in midi.OPTION_KEYS if k in plain])          # nothing is stored that was not asked for
        for bad in ({"min": 0}, {"max": 201}, {"min": 150, "max": 150}, {"min": 160, "max": 150}, {"min": "1"}, {"min": True}, {"max": float("nan")},
                    {"invert": 1}, {"takeover": "soft"}, {"takeover": None}, {"min": 250}):
            with self.assertRaises(midi.MidiError, msg=bad):
                midi.validate_entry(dict({"kind": "cc", "number": 1, "action": "size"}, **bad))
        with self.assertRaises(midi.MidiError):                                # the default top is 200: a bottom above it leaves no range
            midi.validate_entry({"kind": "cc", "number": 1, "action": "speed", "min": 2.0})
        for action in ("stop", "fade", "shader_control_1", "vibes_dwell", "blackout_hold"):       # only a level with a shape
            with self.assertRaises(midi.MidiError, msg=action):
                midi.validate_entry({"kind": "cc", "number": 1, "action": action, "invert": True})


class GeometryOnAKnobTest(unittest.TestCase):
    """The mapper, on a fake clock: what a knob does to the size and the two positions."""

    def setUp(self):
        self.rec, self.t, self.have = Recorder(), [100.0], {}

    def mapper(self, *mine, profile=NANO, source="nanoKONTROL2"):
        entries = list(mine) + (midi.profile_entries(BY_ID[profile], source) if profile else []) + midi.builtin_map()
        m = MidiMapper(self.rec, entries, {"blackout": False}, clock=lambda: self.t[0])
        m.profiled = {source} if profile else set()
        m.target = lambda action: self.have.get(action)
        return m

    def cc(self, m, number, v, source="nanoKONTROL2"):
        self.t[0] += 1.0
        m.message(source, ("cc", 0, number, v))

    def values(self):
        return [(body["action"], body["value"]) for path, body in self.rec.calls if path == "/api/control"]

    def test_zoom_and_both_positions_have_a_knob_each(self):
        m = self.mapper()
        for number in (16, 17, 18):                                 # knobs 1, 2, 3: where the picture is not known to this bare mapper
            self.cc(m, number, 64)
            self.cc(m, number, 127)
            self.cc(m, number, 0)
        self.assertEqual(self.values(), [("size", 100.0), ("size", 200.0), ("size", 25.0), ("position", 0.0), ("position", 100.0), ("position", -100.0),
                                         ("position_y", 0.0), ("position_y", 100.0), ("position_y", -100.0)])

    def test_a_knob_that_is_not_where_the_picture_is_does_not_make_it_jump(self):
        self.have.update(size=100.0, position=0.0, position_y=0.0)
        m = self.mapper()
        self.cc(m, 16, 0)                                           # the size knob was left at the bottom: 25 percent would be a jump
        self.cc(m, 16, 20)
        self.cc(m, 17, 127)                                         # and the position knob at the far right
        self.assertEqual(self.values(), [])
        self.assertTrue(m.waiting("nanoKONTROL2", "cc", 16))
        self.cc(m, 16, 61)                                          # it reaches the middle, where 100 percent is
        self.assertEqual(self.values(), [("size", 100.0)])
        self.assertFalse(m.waiting("nanoKONTROL2", "cc", 16))
        self.cc(m, 16, 75)
        self.assertEqual(self.values()[-1], ("size", round(100 + 100 * (7 / 59.0) ** 1.6, 2)))

    def test_pickup_catches_where_the_curve_gives_the_value_not_on_a_straight_line(self):
        self.have.update(size=150.0)
        at = midi.level_position("size", 150.0)                     # far up the knob: the curve is slow near the centre
        self.assertGreater(at, 100)
        self.assertNotEqual(at, round((150 - 25) * 127 / 175.0))    # where a straight line would have put it (91)
        m = self.mapper()
        self.cc(m, 16, 91)
        self.assertEqual(self.values(), [])                         # not there yet: at 91 this knob gives about 122 percent
        self.cc(m, 16, at)
        self.assertEqual(len(self.values()), 1)
        self.assertLess(abs(self.values()[0][1] - 150.0), 1.5)

    def test_a_mapping_says_jump_or_pickup_itself(self):
        self.have.update(size=100.0, opacity=100.0)
        jump = midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 16, "action": "size", "takeover": "jump"})
        m = self.mapper(jump)
        self.cc(m, 16, 0)
        self.assertEqual(self.values(), [("size", 25.0)])           # it asked to jump
        self.rec.calls.clear()
        # a controller with no layout: Learn mappings jump as before, unless the mapping asks for pickup
        plain = midi.validate_entry({"source": "keys", "kind": "cc", "number": 1, "action": "size"})
        wait = midi.validate_entry({"source": "keys", "kind": "cc", "number": 2, "action": "size", "takeover": "pickup"})
        m = self.mapper(plain, wait, profile=None, source="keys")
        for e in m.entries:
            if e.get("source") == "keys":
                e["pickup"] = midi.takes_over(e, False)             # as the hub does for a controller without a layout
        self.cc(m, 1, 0, "keys")
        self.cc(m, 2, 0, "keys")
        self.assertEqual(self.values(), [("size", 25.0)])
        self.cc(m, 2, 64, "keys")
        self.assertEqual(self.values()[-1], ("size", 100.0))

    def test_an_inverted_knob_picks_up_on_its_own_side(self):
        self.have.update(position=-100.0)
        inv = midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 17, "action": "position", "invert": True})
        m = self.mapper(inv)
        for e in m.entries:
            if e.get("id") == inv["id"]:
                e["pickup"] = midi.takes_over(e, True)
        self.cc(m, 17, 0)                                           # inverted: the bottom is +100, the picture is at -100
        self.assertEqual(self.values(), [])
        self.cc(m, 17, 127)
        self.assertEqual(self.values(), [("position", -100.0)])

    def test_two_controllers_share_one_level_by_soft_pickup(self):
        """The model the owner described on 2026-10-10 for a later Map Mode: several physical controllers on one
        control of the box, each taking it over only where it meets the value, so none of them makes it jump."""
        entries = midi.profile_entries(BY_ID[NANO], "nanoKONTROL2") + midi.profile_entries(BY_ID[MIX], "Mix")
        m = MidiMapper(self.rec, entries, {"blackout": False}, clock=lambda: self.t[0])
        m.profiled = {"nanoKONTROL2", "Mix"}
        m.target = lambda action: self.have.get(action)
        self.have.update(size=100.0)

        def turn(source, number, v):
            self.t[0] += 2.0                                        # each rests for two seconds between its moves
            m.message(source, ("cc", 0, number, v))
            if self.values():
                self.have["size"] = self.values()[-1][1]            # the box has what was last set, whoever set it
            return len(self.values())
        self.assertEqual(turn("nanoKONTROL2", 16, 64), 1)           # the nanoKONTROL2's knob is where the picture is: it has it
        self.assertEqual(turn("nanoKONTROL2", 16, 100), 2)
        up = self.have["size"]
        self.assertGreater(up, 130)
        self.assertEqual(turn("Mix", 17, 0), 2)                     # the MIDI Mix's knob B1 stands at the bottom: nothing jumps
        self.assertTrue(m.waiting("Mix", "cc", 17))
        self.assertEqual(turn("Mix", 17, 99), 3)                    # it comes to where the picture is (within the tolerance): it has it now
        self.assertLess(abs(self.have["size"] - up), 4)
        self.assertEqual(turn("Mix", 17, 30), 4)                    # and takes the picture down
        down = self.have["size"]
        self.assertLess(down, 80)
        self.assertEqual(turn("nanoKONTROL2", 16, 101), 4)          # the first knob still stands at 100: it lost the picture and must meet it again
        self.assertTrue(m.waiting("nanoKONTROL2", "cc", 16))
        self.assertEqual(turn("nanoKONTROL2", 16, 31), 5)
        self.assertLess(abs(self.have["size"] - down), 4)

    def test_the_new_buttons_make_the_calls_of_the_panel(self):
        m = self.mapper(profile=None, source="keys")
        want = {"fade": ("/api/fade", {"seconds": 2}), "rotate": ("/api/control", {"action": "rotate", "value": "toggle"}),
                "flip_h": ("/api/control", {"action": "flip_h", "value": "toggle"}), "flip_v": ("/api/control", {"action": "flip_v", "value": "toggle"}),
                "mute": ("/api/control", {"action": "mute", "value": "toggle"}), "loop": ("/api/control", {"action": "loop", "value": "toggle"}),
                "seek_back": ("/api/control", {"action": "seek", "value": -10}), "seek_forward": ("/api/control", {"action": "seek", "value": 10}),
                "overlay": ("/api/overlay", {"toggle": True}), "test_pattern": ("/api/testpattern", {"on": "toggle"})}
        for n, action in enumerate(sorted(want)):
            self.assertEqual(midi.ACTIONS[action][0], "trigger")
            m.entries.insert(0, midi.validate_entry({"source": "keys", "kind": "note", "number": n, "action": action}))
            self.t[0] += 1
            m.message("keys", ("on", 0, n, 127))
            self.assertEqual(self.rec.calls[-1], want[action], action)
        self.assertEqual(len(self.rec.calls), len(want))


class HubGeometryTest(HubBase):
    def test_the_hub_reads_where_the_picture_is_from_memory(self):
        self.api.mix.update(size=140, position=-30, position_y=55)
        self.assertEqual([self.hub._target(a) for a in ("size", "position", "position_y")], [140.0, -30.0, 55.0])

    def test_a_nanokontrol_knob_on_the_real_hub_waits_then_follows(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.player.running = True
        self.send("/dev/snd/midiC1D0", [0xB0, 18, 0])               # knob 3 (position Y) left at the bottom; the picture is centred
        self.wait(lambda: self.controller("nanoKONTROL2")["messages"] >= 1)
        time.sleep(0.2)
        self.assertEqual(self.api.mix["position_y"], 0)
        knob = next(c for c in self.controller("nanoKONTROL2")["controls"] if c["id"] == "knob3")
        self.assertEqual((knob["action"], knob["pickup"], knob["waiting"], knob["zone"]), ({"action": "position_y"}, True, True, "picture"))
        self.send("/dev/snd/midiC1D0", [0xB0, 18, 64])
        time.sleep(0.2)
        self.send("/dev/snd/midiC1D0", [0xB0, 18, 127])
        self.wait(lambda: self.api.mix["position_y"] == 100.0)
        self.assertIn(("position", 0, 1000.0), self.player.calls)   # the player was told both axes, Y at its end

    def test_the_toggles_of_the_api(self):
        flips = lambda: (self.api.mix["flip_h"], self.api.mix["flip_v"], self.api.mix["rotate"])
        for action, after in (("flip_h", (True, False, 0)), ("flip_h", (False, False, 0)), ("flip_v", (False, True, 0)),
                              ("rotate", (False, True, 90)), ("rotate", (False, True, 180)), ("rotate", (False, True, 270)), ("rotate", (False, True, 0))):
            self.assertEqual(self.post("/api/control", {"action": action, "value": "toggle"})[0], 200)
            self.assertEqual(flips(), after, action)
        self.assertEqual(self.post("/api/control", {"action": "rotate", "value": 45})[0], 400)
        self.assertEqual(self.post("/api/control", {"action": "flip_h", "value": "yes"})[0], 400)
        state = {"muted": False, "loop_file": "inf", "loop_playlist": "no"}
        self.player.status = lambda: dict({"running": True, "path": "/x/a.mp4"}, **state)
        self.post("/api/control", {"action": "mute", "value": "toggle"})
        self.post("/api/control", {"action": "loop", "value": "toggle"})
        self.assertEqual(self.player.calls[-2:], [("mute", True), ("loop", False)])
        state.update(muted=True, loop_file=False)                   # mpv says "no" as false through its socket
        self.post("/api/control", {"action": "mute", "value": "toggle"})
        self.post("/api/control", {"action": "loop", "value": "toggle"})
        self.assertEqual(self.player.calls[-2:], [("mute", False), ("loop", True)])
        # the overlay's switch: refused with no picture chosen, else the other state
        self.assertEqual(self.post("/api/overlay", {"toggle": True})[0], 400)
        # the test pattern: on, and off again (which clears the screen)
        self.assertEqual(self.post("/api/testpattern", {"on": "toggle"})[1], {"test_pattern": True})
        self.player.status = lambda: {"running": True, "path": self.player.TEST_PATTERN}
        self.assertEqual(self.post("/api/testpattern", {"on": "toggle"})[1], {"test_pattern": False})


class FadeToggleTest(ServerBase):
    """One button: out, then in. Which one is decided from what the screen is doing, not from a count of presses."""

    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.player.running = True
        self.player.status = lambda: {"running": True, "path": "/x/a.mp4"}

    def post(self, path, body=None, token=None):
        return self.call("POST", path, body or {}, token=token or self.full)

    def fade(self):
        return self.call("GET", "/api/status", token=self.full)[1]["mix"]["fade"]

    def settle(self, label):
        end = time.time() + 4
        while time.time() < end and self.api.fader.label != label:
            time.sleep(0.01)
        self.assertEqual(self.api.fader.label, label)

    def level(self):
        return ([c for c in self.player.calls if c[0] == "opacity"] or [("opacity", 255)])[-1][1]

    def wait_level(self, want):
        end = time.time() + 4
        while time.time() < end and self.level() != want:
            time.sleep(0.01)
        self.assertEqual(self.level(), want)

    def test_out_then_in_and_what_the_panel_is_told(self):
        self.assertIsNone(self.fade())
        self.assertEqual(self.post("/api/fade", {"seconds": 0.2})[1], {"ok": True, "fade": "out"})
        self.assertEqual(self.fade(), "out")                        # while it goes down
        self.wait_level(0)
        self.assertEqual(self.fade(), "out")                        # and while it is black from it: the button flashes on
        self.assertEqual(self.post("/api/fade", {"seconds": 0.2})[1], {"ok": True, "fade": "in"})
        self.assertEqual(self.fade(), "in")
        self.wait_level(255)
        self.settle(None)
        self.assertIsNone(self.fade())                              # up again: steady
        self.assertEqual(self.post("/api/fade")[1]["fade"], "out")  # no seconds: two, as the buttons had
        self.assertEqual(self.post("/api/fade", {"seconds": 0})[0], 400)

    def test_a_press_in_the_middle_of_the_way_down_turns_it_round(self):
        self.post("/api/fade", {"seconds": 5})
        time.sleep(0.2)
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "in")
        self.wait_level(255)
        self.settle(None)

    def test_it_follows_a_fade_that_was_started_elsewhere(self):
        self.post("/api/fadeout", {"seconds": 0.1})                 # the old call (a stored mapping, OSC, a Room scene)
        self.settle("out")
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "in")
        self.wait_level(255)
        self.settle(None)
        self.post("/api/fade", {"seconds": 0.1})                    # out by the button ...
        self.wait_level(0)
        self.post("/api/fadein", {"seconds": 0.1})                  # ... and in by the old call
        self.wait_level(255)
        self.settle(None)
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "out")      # a counter would have said "in"

    def test_blackout(self):
        self.post("/api/blackout", {"on": True})
        self.assertIsNone(self.fade())                              # black by Blackout: its own button shows it, this one is steady
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "in")       # the screen is down: the press brings it up
        self.assertFalse(self.api.mix["blackout"])
        self.wait_level(255)
        self.settle(None)
        # faded out, then Blackout on and off: the picture is back, so the next press fades out
        self.post("/api/fade", {"seconds": 0.1})
        self.wait_level(0)
        self.post("/api/blackout", {"on": True})
        self.assertIsNone(self.fade())
        self.post("/api/blackout", {"on": False})
        self.assertEqual(self.level(), 255)
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "out")

    def test_a_play_that_brought_the_picture_back(self):
        self.post("/api/fade", {"seconds": 0.1})
        self.wait_level(0)
        self.assertEqual(self.post("/api/play", {"file": "a.mp4"})[0], 200)       # any play after a Fade out brings the picture back
        self.assertIsNone(self.fade())
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "out")
        # and a change of the opacity ends "faded out" too
        self.wait_level(0)
        self.post("/api/control", {"action": "opacity", "value": 80})
        self.assertIsNone(self.fade())
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1})[1]["fade"], "out")

    def test_the_old_two_calls_are_as_they_were(self):
        self.assertEqual(self.post("/api/fadeout", {"seconds": 0.1})[1], {"ok": True})
        self.wait_level(0)
        self.assertEqual(self.post("/api/fadeout", {"seconds": 0.1})[1], {"ok": True})      # out twice is out
        self.assertEqual(self.api.fader.label, "out")
        self.assertEqual(self.post("/api/fadein", {"seconds": 0.1})[1], {"ok": True})
        self.wait_level(255)
        for name in ("fadeout", "fadein"):                          # and the two old actions still plan the two old calls
            m = MidiMapper(Recorder(), [midi.validate_entry({"kind": "note", "number": 1, "action": name})], {})
            m.message("x", ("on", 0, 1, 127))
            self.assertEqual(m.do.calls, [("/api/" + name, {"seconds": 2})])

    def test_roles(self):
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        view = self.post("/api/devices/invite", {"name": "v", "role": "view"})[1]["token"]
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1}, token=live)[0], 200)
        self.assertEqual(self.post("/api/fade", {"seconds": 0.1}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/fade", {})[0], 401)

    def test_many_presses_at_once_leave_the_label_and_the_level_agreeing(self):
        threads = [threading.Thread(target=self.api.fade, args=({"seconds": 0.1}, None, "t")) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        time.sleep(0.5)
        label = self.api.fader.label
        self.assertIn(label, ("out", None))
        self.assertEqual(self.level(), 0 if label == "out" else 255)


class FlashStateTest(unittest.TestCase):
    def test_the_fade_button_flashes_while_the_picture_is_down_from_a_fade(self):
        fade = {"action": "fade"}
        self.assertEqual(midi.light_meaning(fade), "fade")
        self.assertEqual([midi.light_state(fade, snap(**k)) for k in ({}, {"fade": "out"}, {"fade": "in"}, {"blackout": True})],
                         ["on", "flash", "busy", "on"])              # under Blackout its own button is the red one
        # the Launchpad flashes it itself: full red in "clear" mode. Nothing for a writer to do
        lights = BY_ID[PAD]["lights"]
        self.assertEqual([value(PAD, fade, snap(**k)) for k in ({}, {"fade": "out"}, {"fade": "in"})], [13, 11, 28])
        self.assertFalse(midi.light_flashes(lights, "fade", "flash"))
        self.assertFalse(midi.light_flashes(lights, "fade", "on"))
        # the nanoKONTROL2 has one colour and no flashing of its own: lit, and its writer switches it on and off
        self.assertEqual([value(NANO, fade, snap(**k)) for k in ({}, {"fade": "out"}, {"fade": "in"})], [0, 127, 127])
        self.assertTrue(midi.light_flashes(BY_ID[NANO]["lights"], "fade", "flash"))
        # a profile written before there was a fade style shows the button as it showed Fade out, flashing by the writer
        old = dict(BY_ID[NANO]["lights"], styles={k: v for k, v in BY_ID[NANO]["lights"]["styles"].items() if k != "fade"})
        self.assertEqual(midi.light_value(old, "fade", "flash"), 127)
        self.assertTrue(midi.light_flashes(old, "fade", "flash"))
        self.assertEqual(midi.light_value(BY_ID[MIX]["lights"], "fade", "flash"), 0)        # no style at all: dark, and nothing to flash
        self.assertFalse(midi.light_flashes(BY_ID[MIX]["lights"], "fade", "flash"))

    def test_a_flash_value_needs_a_section_that_says_the_device_flashes(self):
        import copy
        import json
        with open(os.path.join(midi.PROFILE_DIR, NANO + ".json")) as f:
            raw = json.load(f)
        bad = copy.deepcopy(raw)
        bad["lights"]["styles"]["fade"]["flash"] = 5                # a timer section may not name a device value
        with self.assertRaises(midi.MidiError):
            midi.validate_profile(bad, NANO)
        bad = copy.deepcopy(raw)
        bad["lights"]["flash"] = "strobe"
        with self.assertRaises(midi.MidiError):
            midi.validate_profile(bad, NANO)
        ok = copy.deepcopy(raw)
        ok["lights"]["flash"] = "device"
        ok["lights"]["styles"]["fade"]["flash"] = 5
        self.assertEqual(midi.validate_profile(ok, NANO)["lights"]["styles"]["fade"]["low"]["flash"], 5)
        bad = copy.deepcopy(ok)
        bad["lights"]["styles"]["fade"]["flash"] = 128
        with self.assertRaises(midi.MidiError):
            midi.validate_profile(bad, NANO)


class WriterFlashTest(WriterBase):
    def stamps(self, seconds):
        out, seen, end = [], 0, time.monotonic() + seconds
        while time.monotonic() < end:
            got = self.pipe.read()
            out.extend((time.monotonic(), m) for m in got[seen:])
            seen = len(got)
            time.sleep(0.004)
        return out

    def test_a_light_flashes_twice_a_second_and_stops_with_the_state(self):
        w = self.writer()
        key, other = self.KEYS[5], self.KEYS[6]
        w.show({key: 127, other: 127}, flash={key})
        got = self.stamps(2.1)
        mine = [m[2] for t, m in got if m[:2] == tuple(key)]
        self.assertEqual([m for t, m in got if m[:2] == tuple(other)], [tuple(other) + (127,)])       # the steady one was sent once
        self.assertTrue(7 <= len(mine) <= 10, mine)                 # on and off, a quarter second each: eight changes in two seconds
        self.assertTrue(all(a != b for a, b in zip(mine, mine[1:])), mine)
        self.assertEqual(set(mine), {0, 127})
        times = [t for t, m in got if m[:2] == tuple(key)]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(0.15 < g < 0.4 for g in gaps[1:]), gaps)        # (the first half is cut short: the flash keeps the clock's beat)
        w.show({key: 127, other: 127})                              # the state ended: steady at its value, and nothing more
        self.wait(lambda: self.pipe.lit()[tuple(key)] == 127)
        sent = len(self.pipe.read())
        time.sleep(0.8)
        self.assertEqual(len(self.pipe.read()), sent)

    def test_every_light_flashing_still_keeps_the_limit(self):
        w = self.writer()
        w.show({k: 127 for k in self.KEYS}, flash=set(self.KEYS))   # far more than a layout would ever flash
        got = self.stamps(2.6)
        self.assertGreater(len(got), 300)
        times = [t for t, m in got]
        worst = max(sum(1 for s in times if t0 <= s < t0 + 1.0) for t0 in times)
        self.assertLessEqual(worst, 200 + 8, "more than 200 messages reached the device within a second")

    def test_stopping_while_it_flashes_leaves_the_light_off(self):
        w = self.writer()
        w.show({self.KEYS[0]: 127}, flash={self.KEYS[0]})
        self.wait(lambda: len(self.pipe.read()) >= 2)
        w.stop()
        self.assertEqual(self.pipe.lit()[tuple(self.KEYS[0])], 0)
        self.assertFalse(w.alive)


class FlashOnTheControllersTest(LightsHubBase):
    def test_the_launchpad_flashes_the_fade_button_by_itself(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        pipe, key = self.out[C_PAD], (0x90, 72)                     # the round button E
        self.assertEqual(pipe.lit()[key], 13)
        self.assertEqual(self.post("/api/fade", {"seconds": 0.2})[0], 200)
        self.wait(lambda: pipe.lit()[key] == 11)                    # full red, flashing: one message, the controller does the rest
        sent = len(pipe.read())
        time.sleep(1.0)
        self.assertEqual(len(pipe.read()), sent)                    # black and flashing: the box sends nothing more
        fade = next(c for c in self.controller("Mini")["controls"] if c["id"] == "side_e")
        self.assertEqual((fade["action"], fade["lit"], fade["zone"]), ({"action": "fade"}, "flash", "screen"))
        self.send(C_PAD, [0x90, 72, 127])                           # pressed on the controller: it fades in
        self.wait(lambda: pipe.lit()[key] == 13 and self.api.fader.label is None, timeout=8)
        self.assertEqual(self.api.mix["fade"] if "fade" in self.api.mix else None, None)

    def test_the_nanokontrol_is_flashed_by_its_writer_and_only_while_it_lasts(self):
        self.plug(C_NANO)
        self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})
        self.wait(lambda: C_NANO in self.out and self.out[C_NANO].lit().get((0xB0, 32)) == 127)
        pipe = self.out[C_NANO]
        r7 = lambda: [m[2] for m in pipe.read() if m[:2] == (0xB0, 70)]
        self.wait(lambda: r7() == [0])
        self.post("/api/fade", {"seconds": 0.2})
        self.wait(lambda: len(r7()) >= 6, timeout=8)                # on, off, on, off ...
        seen = r7()
        self.assertTrue(all(a != b for a, b in zip(seen[1:], seen[2:])), seen)
        self.post("/api/fade", {"seconds": 0.2})                    # in again
        self.wait(lambda: self.api.fader.label is None, timeout=8)
        self.wait(lambda: pipe.lit()[(0xB0, 70)] == 0)
        count = len(r7())
        time.sleep(1.0)
        self.assertEqual(len(r7()), count)                          # steady: the flashing ended with the state

    def test_the_midi_mix_has_no_light_for_it_and_says_nothing(self):
        self.plug(C_MIX)
        self.post("/api/midi", {"controller": "Mix", "lights": True})
        self.wait(lambda: C_MIX in self.out and len(self.out[C_MIX].lit()) == 18)
        sent = len(self.out[C_MIX].read())
        self.post("/api/fade", {"seconds": 0.2})
        time.sleep(1.2)
        self.assertEqual(len(self.out[C_MIX].read()), sent)         # its fade button is on the Solo row, which the box cannot light
        solo5 = next(c for c in self.controller("Mix")["controls"] if c["id"] == "solo5")
        self.assertEqual((solo5["action"], solo5["light"]), ({"action": "fade"}, False))


if __name__ == "__main__":
    unittest.main()
