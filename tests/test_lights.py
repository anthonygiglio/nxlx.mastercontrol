# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Controller lights: the lights section of the shipped profiles, what each light shows, the writer (only changes,
the rate limit, a full buffer, unplug and replug), the hub, the API.

Nothing here has touched a real controller: a pipe stands in for each device, and what is written to it is read back
and compared. Whether a real Launchpad Mini, nanoKONTROL2 or MIDI Mix lights up as its maker's document says is not
tested by this file and cannot be."""
import copy
import faulthandler
import os
import threading
import time
import unittest
from unittest import mock

from pvj import boxcare, midi
from pvj.midi import LightWriter
from tests.test_controllers import BY_ID, MIX, NANO, PAD, HubBase, raw
from tests.test_shader_engine import ALL, Live

QUIET = {"pads": [], "playing": None, "running": False, "paused": False, "playlist": False, "blackout": False, "fade": None,
         "vibes": False, "vibes_ready": False, "sets": {}, "set": None, "shader": None, "presets": [], "preset": None,
         "scenes": [], "applying": None}


def setUpModule():
    # This module is all threads and pipes. If anything in it ever waits for good, say where (every thread's stack)
    # and end the run, instead of a job that sits silent until someone cancels it. It takes under a minute.
    faulthandler.dump_traceback_later(420, exit=True)


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


def snap(**over):
    return dict(copy.deepcopy(QUIET), **over)


def value(profile, action, state_of, level="low", phase=0):
    lights = BY_ID[profile]["lights"]
    return midi.light_value(lights, midi.light_meaning(action), midi.light_state(action, state_of), level, phase)


class Pipe:
    """A device that can be written to: what arrives is kept as three-byte messages."""

    def __init__(self):
        self.r, self.w = os.pipe()
        os.set_blocking(self.r, False)
        os.set_blocking(self.w, False)
        self.data = b""
        self.closed = False

    def read(self):
        while not self.closed:
            try:
                chunk = os.read(self.r, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            self.data += chunk
        return [tuple(self.data[i:i + 3]) for i in range(0, len(self.data) - len(self.data) % 3, 3)]

    def lit(self):
        """The lights as the device would have them after everything read so far (B0 00 00 clears a Launchpad)."""
        have = {}
        for status, number, v in self.read():
            if (status, number, v) == (0xB0, 0, 0):
                have = {}
            elif (status, number) == (0xB0, 0):           # controller 0 is the Launchpad's own settings (flash mode), not a light
                continue
            else:
                have[(status, number)] = v
        return have

    def unplug(self):
        self.closed = True
        os.close(self.r)


class LightsFilesTest(unittest.TestCase):
    def test_each_shipped_profile_has_a_lights_section_that_passes_the_check(self):
        for pid, count, default, unverified, levels in ((PAD, 80, True, False, True), (NANO, 30, False, True, False), (MIX, 18, False, True, False)):
            lights = BY_ID[pid]["lights"]
            self.assertEqual((len(lights["controls"]), lights["default"], lights["unverified"], lights["brightness"], lights["channel"]),
                             (count, default, unverified, levels, 1), pid)
            self.assertTrue(lights["sources"] and lights["note"], pid)
        self.assertIn("Set LED mode to External in Korg's editor first", BY_ID[NANO]["lights"]["note"])
        self.assertIn("Unverified", BY_ID[MIX]["lights"]["note"])
        self.assertIn("Launchpad S Programmer's Reference", BY_ID[PAD]["lights"]["sources"][0])

    def test_which_controls_have_a_light(self):
        nano = set(BY_ID[NANO]["lights"]["controls"])
        self.assertEqual(nano, {"%s%d" % (row, n) for row in "smr" for n in range(1, 9)} | {"cycle", "rewind", "forward", "stop", "play", "rec"})
        mix = set(BY_ID[MIX]["lights"]["controls"])
        self.assertEqual(mix, {"%s%d" % (row, n) for row in ("mute", "rec") for n in range(1, 9)} | {"bank_left", "bank_right"})
        self.assertEqual(set(BY_ID[PAD]["lights"]["controls"]), {c["id"] for c in BY_ID[PAD]["controls"]})
        for p in BY_ID.values():                              # no fader or knob has a light
            for cid in p["lights"]["controls"]:
                ctl = next(c for c in p["controls"] if c["id"] == cid)
                self.assertIn(ctl["kind"], ("button", "pad"))
            # and every standard action of a lit control has a style in its own file, so none is dark by oversight
            # (dark on purpose: a control that asks for a pairing code, D61; a controller shows nothing about a code)
            for ctl in p["controls"]:
                # (and Mute, the overlay's switch and the quarter turn, D75: nothing of them is shown on a light)
                if ctl["id"] in p["lights"]["controls"] and ctl["action"] is not None and midi.ACTIONS[ctl["action"]["action"]][0] != "hold" \
                        and ctl["action"]["action"] not in ("mute", "overlay", "rotate"):
                    self.assertIn(midi.light_meaning(ctl["action"]), p["lights"]["styles"], (p["id"], ctl["id"]))

    def test_a_lights_section_does_not_depend_on_what_the_controls_do(self):
        """Another branch may give a spare control an action, or add an action this file has never heard of. Neither
        may make a profile fail its check (it would lose its whole layout): the section only lists which controls
        have a light, and a light with nothing to show is dark."""
        p = raw(PAD)
        code = next(c for c in p["controls"] if c["id"] == "top3")           # the third round button along the top: it asks for a code (D61)
        self.assertEqual(code["action"], {"action": "code_join"})
        self.assertIsNone(midi.light_meaning(code["action"]))                # nothing about a code is ever shown on a controller
        spare = next(c for c in p["controls"] if c["id"] == "pad68")         # the last pad beside bank C: spare
        self.assertIsNone(spare["action"])
        spare["action"] = {"action": "blackout"}
        clean = midi.validate_profile(p, PAD)
        self.assertIn("pad68", clean["lights"]["controls"])
        action = next(c for c in clean["controls"] if c["id"] == "pad68")["action"]
        self.assertEqual([value(PAD, action, snap()), value(PAD, action, snap(blackout=True))], [13, 11])       # and its light follows it
        with mock.patch.dict(midi.ACTIONS, {"strobe": ("trigger", None, None), "smear": ("level", 0, 1)}):
            p = raw(PAD)
            next(c for c in p["controls"] if c["id"] == "pad68")["action"] = {"action": "strobe"}
            next(c for c in p["controls"] if c["id"] == "top1")["action"] = {"action": "strobe"}
            clean = midi.validate_profile(p, PAD)
            self.assertEqual(len(clean["lights"]["controls"]), 80)
            self.assertIsNone(midi.light_meaning({"action": "strobe"}))
            self.assertEqual(value(PAD, {"action": "strobe"}, snap(running=True, blackout=True, vibes=True)), 12)     # dark, not an error
            self.assertEqual(value(NANO, {"action": "strobe"}, snap()), 0)

    def test_the_lights_of_the_effect_controls(self):
        """A control with an effect action is lit while an effect could go on, and "on now" while one is on (the one
        button); the two that step are lit then too; a control of the effect is lit while an effect is on."""
        toggle, nxt, prev, knob = ({"action": a} for a in ("effect_toggle", "effect_next", "effect_prev", "effect_control_3"))
        self.assertEqual([midi.light_meaning(a) for a in (toggle, nxt, prev, knob)], ["effect", "step", "step", "control"])
        self.assertEqual(midi.light_meaning({"action": "effect_amount"}), None)             # a level: nothing to show
        states = lambda s: [midi.light_state(a, s) for a in (toggle, nxt, prev, knob)]
        self.assertEqual(states(snap()), ["off", "off", "off", "off"])                      # nothing plays
        self.assertEqual(states(snap(running=True)), ["off", "off", "off", "off"])          # the module is off, or a generator has the screen
        self.assertEqual(states(snap(running=True, effect_ready=True)), ["on", "on", "on", "off"])
        self.assertEqual(states(snap(effect_ready=True)), ["off", "off", "off", "off"])     # ready, and nothing with a picture
        self.assertEqual(states(snap(running=True, effect_ready=True, effect="fx-wash.fs")), ["active", "on", "on", "on"])
        # the three shipped layouts: where an effect action sits on a control with a light, the light has a style
        self.assertEqual(next(c for c in BY_ID[PAD]["controls"] if c["id"] == "pad75")["action"], {"action": "effect_toggle"})
        self.assertEqual(next(c for c in BY_ID[NANO]["controls"] if c["id"] == "r5")["action"], {"action": "effect_toggle"})
        on = snap(running=True, effect_ready=True, effect="fx-wash.fs")
        self.assertEqual([value(PAD, toggle, snap()), value(PAD, toggle, snap(running=True, effect_ready=True)), value(PAD, toggle, on)], [12, 29, 28])
        self.assertEqual([value(NANO, toggle, snap()), value(NANO, toggle, snap(running=True, effect_ready=True)), value(NANO, toggle, on)], [0, 0, 127])
        self.assertEqual([value(PAD, nxt, snap()), value(PAD, nxt, on), value(PAD, knob, snap()), value(PAD, knob, on)], [12, 29, 12, 29])
        # an older picture of the box without the two entries (another branch's snapshot) is dark, not an error
        old = {k: v for k, v in snap(running=True).items() if not k.startswith("effect")}
        self.assertEqual(states(old), ["off", "off", "off", "off"])

    def test_the_bytes_of_a_light_are_the_makers(self):
        pad, nano, mix = BY_ID[PAD], BY_ID[NANO], BY_ID[MIX]
        ctl = {p["id"]: {c["id"]: c for c in p["controls"]} for p in BY_ID.values()}
        # Launchpad S Programmer's Reference: 90h, key, velocity for a pad; B0h, 68h to 6Fh, value for the top row;
        # velocity = 16 x green + red + 12. Off is 12, low red 13, full red 15, low amber 29, low green 28, full green 60
        self.assertEqual(midi.light_message(pad["lights"], ctl[PAD]["pad11"], 28), bytes((0x90, 0, 28)))
        self.assertEqual(midi.light_message(pad["lights"], ctl[PAD]["side_h"], 15), bytes((0x90, 120, 15)))
        self.assertEqual(midi.light_message(pad["lights"], ctl[PAD]["top1"], 29), bytes((0xB0, 104, 29)))
        self.assertEqual(midi.light_message(pad["lights"], ctl[PAD]["top8"], 12), bytes((0xB0, 111, 12)))
        self.assertEqual((pad["lights"]["off"], pad["lights"]["setup"], pad["lights"]["clear"]), (12, [b"\xb0\x00\x00", b"\xb0\x00\x28"], [b"\xb0\x00\x00"]))      # the reset, then flash mode (28h); the reset again to clear
        self.assertEqual(pad["lights"]["flash"], "device")
        for name, style in pad["lights"]["styles"].items():
            for level in midi.LIGHT_LEVELS:
                for state in midi.LIGHT_STATES:
                    v = style[level][state]
                    self.assertEqual(v & 0x0C, 12, "the flags of normal use")       # copy mode: lit in both buffers, so steady in flash mode
                    self.assertEqual(v & 0x40, 0)
                # the manual's "Adventures in Double Buffering": a flashing colour is the same colour with bit 2 cleared,
                # "subtracting 4 from the velocity value". The fade button and Blackout have one: full red, 0Fh less 4
                self.assertEqual(style[level].get("flash"), 11 if name in ("fade", "blackout") else 56 if name == "mapping" else None)       # (mapping mode: green, 3Ch less 4)
                if name in ("fade", "blackout"):
                    self.assertEqual((style[level]["flash"] & 0x0C, style[level]["flash"] + 4), (8, style[level]["active"]))
        # the three banks in three colours, by the manual's formula 16 x green + red + 12: amber (1, 1), yellow-green
        # (green 2, red 1), orange (green 1, red 2); the pad that plays is full green in every bank
        st = pad["lights"]["styles"]
        self.assertEqual([st[n]["low"]["on"] for n in ("clip", "clip_b", "clip_c")], [16 * 1 + 1 + 12, 16 * 2 + 1 + 12, 16 * 1 + 2 + 12])
        self.assertEqual([st[n]["high"]["on"] for n in ("clip", "clip_b", "clip_c")], [16 * 2 + 2 + 12, 16 * 3 + 2 + 12, 16 * 2 + 3 + 12])
        self.assertEqual({st[n][lv]["active"] for n in ("clip", "clip_b", "clip_c") for lv in midi.LIGHT_LEVELS}, {16 * 3 + 12})
        # nanoKONTROL2 in External LED mode: a control change with the button's own number, 127 or 0
        self.assertEqual(midi.light_message(nano["lights"], ctl[NANO]["s1"], 127), bytes((0xB0, 32, 127)))
        self.assertEqual(midi.light_message(nano["lights"], ctl[NANO]["play"], 0), bytes((0xB0, 41, 0)))
        # MIDI Mix: a note-on with the button's note, 127 or 0 (a note-off is said to do nothing, so none is sent)
        self.assertEqual(midi.light_message(mix["lights"], ctl[MIX]["mute1"], 127), bytes((0x90, 1, 127)))
        self.assertEqual(midi.light_message(mix["lights"], ctl[MIX]["rec8"], 0), bytes((0x90, 24, 0)))
        for p in (nano, mix):
            self.assertEqual((p["lights"]["off"], p["lights"]["setup"], p["lights"]["clear"], p["lights"]["flash"]), (0, [], [], "timer"))
            for style in p["lights"]["styles"].values():
                self.assertLessEqual({style["low"][s] for s in midi.LIGHT_STATES} | set(style["low"]["pulse"].values()), {0, 127})

    def test_a_bad_lights_section_is_refused_and_says_why(self):
        good = raw(PAD)
        midi.validate_profile(copy.deepcopy(good), PAD)

        def broken(change, expect):
            p = copy.deepcopy(good)
            change(p["lights"])
            with self.assertRaises(midi.MidiError) as e:
                midi.validate_profile(p, PAD)
            self.assertIn(expect, str(e.exception))
        broken(lambda v: v.update(sysex=[240, 0, 247]), "unknown key")
        broken(lambda v: v.update(setup=[[0xF0, 0, 0]]), "status")                        # no system message, so no SysEx
        broken(lambda v: v.update(setup=[[0xE0, 0, 0]]), "status")
        broken(lambda v: v.update(setup=[[0xA0, 0, 0]]), "only note-off, note-on and control change")
        broken(lambda v: v.update(setup=[[176, 0]]), "three numbers")
        # L3 of the review: only the section's own channel, and never a channel mode message
        broken(lambda v: v.update(setup=[[0xB1, 0, 0]]), "section's channel")
        broken(lambda v: v.update(clear=[[0x9F, 0, 0]]), "section's channel")
        broken(lambda v: v.update(clear=[[0x80, 0, 0]], channel=2), "section's channel")
        for mode in range(120, 128):
            broken(lambda v: v.update(setup=[[0xB0, mode, 0]]), "channel mode")
            broken(lambda v: v.update(clear=[[0xB0, mode, 127]]), "channel mode")
        ok = copy.deepcopy(good)
        ok["lights"].update(setup=[[0xB0, 119, 0], [0x90, 120, 0], [0x80, 127, 0]])      # controller 119 and notes 120 to 127 are ordinary
        midi.validate_profile(ok, PAD)
        broken(lambda v: v.update(clear=[[176, 0, 128]]), "0 to 127")
        broken(lambda v: v.update(clear=[[176, 0, 0]] * 9), "at most 8")
        broken(lambda v: v.update(setup="b00000"), "at most 8")
        broken(lambda v: v.update(channel=0), "lights.channel")
        broken(lambda v: v.update(channel=17), "lights.channel")
        broken(lambda v: v.update(off=200), "lights.off")
        broken(lambda v: v.update(default="yes"), "true or false")
        broken(lambda v: v.pop("sources"), "missing sources")
        broken(lambda v: v.update(sources=[]), "one to eight")
        broken(lambda v: v["styles"]["clip"]["low"].update(on=128), "0 to 127")
        broken(lambda v: v["styles"]["clip"]["low"].update(on="28"), "whole number")
        broken(lambda v: v["styles"]["clip"]["low"].update(on=True), "whole number")
        broken(lambda v: v["styles"]["clip"]["low"].update(extra=1), "unknown key")
        broken(lambda v: v["styles"]["clip"]["low"].update(pulse={"off": 1}), "pulse")
        broken(lambda v: v["styles"]["clip"].pop("high"), "missing high")
        broken(lambda v: v["styles"].update(disco={}), "not something a light can show")
        broken(lambda v: v["controls"].append("nosuch"), "no control called")
        broken(lambda v: v["controls"].append(7), "no control called")
        broken(lambda v: v["controls"].append("pad11"), "listed twice")
        broken(lambda v: v.update(controls=[]), "1 to 160")
        broken(lambda v: v.update(controls={"pad11": "clip"}), "1 to 160")
        nano = raw(NANO)
        nano["lights"]["controls"].append("fader1")
        with self.assertRaises(midi.MidiError) as e:
            midi.validate_profile(nano, NANO)
        self.assertIn("only a button or pad has a light", str(e.exception))
        plain = raw(NANO)
        del plain["lights"]                                    # a profile without lights is as good as before
        self.assertIsNone(midi.validate_profile(plain, NANO)["lights"])


class WhatALightShowsTest(unittest.TestCase):
    """The state of the box to the number each light is sent, for every kind of light of every shipped profile."""

    def test_a_pad_is_lit_when_it_holds_a_clip_and_another_colour_while_it_plays(self):
        pads = [["a.mp4"] + [""] * 11, [""] * 12, [""] * 12]
        a1, a2 = {"action": "pad", "bank": 0, "index": 0}, {"action": "pad", "bank": 0, "index": 1}
        self.assertEqual([value(PAD, a2, snap(pads=pads)), value(PAD, a1, snap(pads=pads)), value(PAD, a1, snap(pads=pads, running=True, playing="a.mp4"))],
                         [12, 29, 60])                                         # off, amber, full green
        self.assertEqual(value(PAD, a1, snap(pads=pads, running=True, playing="b.mov")), 29)
        self.assertEqual(value(PAD, a1, snap(pads=pads, running=False, playing="a.mp4")), 29)
        self.assertEqual([value(PAD, a1, snap(pads=pads, running=True, playing="a.mp4"), level) for level in ("low", "medium", "high")], [60, 60, 60])    # the one that plays stands out at any brightness
        b1, c1 = {"action": "pad", "bank": 1, "index": 0}, {"action": "pad", "bank": 2, "index": 0}
        three = [["a.mp4"] + [""] * 11, ["b.mp4"] + [""] * 11, ["c.mp4"] + [""] * 11]
        self.assertEqual([midi.light_meaning(a) for a in (a1, b1, c1, {"action": "bank_pad", "index": 0})], ["clip", "clip_b", "clip_c", "clip"])
        self.assertEqual([value(PAD, a, snap(pads=three)) for a in (a1, b1, c1)], [29, 45, 30])        # three banks, three colours
        self.assertEqual([value(PAD, a, snap(pads=three, running=True, playing="b.mp4")) for a in (a1, b1, c1)], [29, 60, 30])
        self.assertEqual([value(NANO, a, snap(pads=three)) for a in (a1, b1, c1)], [127, 127, 127])    # a controller with one colour: every bank as "clip"
        self.assertEqual([value(PAD, a1, snap(pads=pads), level) for level in ("low", "medium", "high")], [29, 29, 46])
        # one light per button: lit with a clip, slowly pulsing while it plays; the pad of the controllers' bank
        row = {"action": "bank_pad", "index": 0}
        for pid in (NANO, MIX):
            self.assertEqual(value(pid, row, snap(pads=pads)), 127)
            self.assertEqual([value(pid, row, snap(pads=pads, running=True, playing="a.mp4"), phase=ph) for ph in (0, 1)], [127, 0])
            self.assertEqual(value(pid, {"action": "bank_pad", "index": 1}, snap(pads=pads)), 0)
        self.assertEqual(midi.light_state(row, snap(pads=pads), bank=1), "off")           # bank B has nothing there

    def test_presets_vibes_sets_and_the_steps(self):
        on = snap(vibes_ready=True, shader="silk.fs", presets=["default", "red"], preset="red")
        p = lambda n: {"action": "shader_preset_%d" % n}
        self.assertEqual([value(PAD, p(1), on), value(PAD, p(2), on), value(PAD, p(3), on), value(PAD, p(1), snap())], [29, 28, 12, 12])
        self.assertEqual([value(NANO, p(1), on), value(NANO, p(2), on), value(NANO, p(2), on, phase=1), value(NANO, p(3), on)], [127, 127, 0, 0])
        self.assertEqual([value(PAD, {"action": "shader_control_1"}, on), value(PAD, {"action": "shader_control_1"}, snap())], [29, 12])
        vibes = {"action": "vibes"}
        self.assertEqual([value(PAD, vibes, snap()), value(PAD, vibes, snap(vibes_ready=True)), value(PAD, vibes, snap(vibes_ready=True, vibes=True))], [12, 29, 28])
        self.assertEqual([value(NANO, vibes, snap(vibes_ready=True)), value(NANO, vibes, snap(vibes_ready=True, vibes=True))], [0, 127])
        sets = dict(vibes_ready=True, sets={"Ambient": "00000000", "Show": "00000001"}, set="00000001")
        amb, show = {"action": "vibes_ambient"}, {"action": "vibes_show"}
        self.assertEqual([value(PAD, amb, snap(**sets)), value(PAD, show, snap(**sets)), value(PAD, show, snap(vibes=True, **sets)), value(PAD, amb, snap(vibes=True, **sets))],
                         [29, 29, 28, 29])
        self.assertEqual(value(PAD, show, snap(vibes_ready=True, sets={"Ambient": "00000000"})), 12)          # that set was removed
        self.assertEqual([value(NANO, show, snap(**sets)), value(NANO, show, snap(vibes=True, **sets))], [0, 127])
        for a in ("shader_prev", "shader_next"):
            self.assertEqual([value(PAD, {"action": a}, snap()), value(PAD, {"action": a}, on)], [12, 29])
        for a in ("clip_prev", "clip_next"):
            self.assertEqual([value(PAD, {"action": a}, snap(running=True)), value(PAD, {"action": a}, snap(running=True, playlist=True))], [12, 29])
            self.assertEqual([value(NANO, {"action": a}, snap(running=True)), value(NANO, {"action": a}, snap(running=True, playlist=True))], [0, 127])

    def test_blackout_fades_and_the_transport(self):
        black, out, fin = {"action": "blackout"}, {"action": "fadeout"}, {"action": "fadein"}
        self.assertEqual([value(PAD, black, snap(), lv) for lv in midi.LIGHT_LEVELS], [13, 13, 14])           # a dim red marks the button
        self.assertEqual([value(PAD, black, snap(blackout=True), lv) for lv in midi.LIGHT_LEVELS], [11, 11, 11])   # full red, flashing, while black, at any brightness
        self.assertEqual((midi.light_state(black, snap(blackout=True)), midi.light_state(black, snap(fade="out"))), ("flash", "on"))      # each button shows its own state
        self.assertFalse(midi.light_flashes(BY_ID[PAD]["lights"], "blackout", "flash"))           # the Launchpad flashes it by itself
        self.assertTrue(midi.light_flashes(BY_ID[NANO]["lights"], "blackout", "flash"))           # the nanoKONTROL2's writer does
        self.assertEqual([value(NANO, black, snap()), value(NANO, black, snap(blackout=True))], [0, 127])
        self.assertEqual([value(PAD, out, snap()), value(PAD, out, snap(fade="out")), value(PAD, fin, snap()), value(PAD, fin, snap(fade="in"))], [13, 15, 29, 28])
        self.assertEqual([value(NANO, out, snap()), value(NANO, out, snap(fade="out")), value(NANO, fin, snap()), value(NANO, fin, snap(fade="in"))], [0, 127, 0, 127])
        play, stop = {"action": "pause"}, {"action": "stop"}
        self.assertEqual([value(PAD, play, snap()), value(PAD, play, snap(running=True)), value(PAD, play, snap(running=True, paused=True))], [12, 28, 29])
        self.assertEqual([value(NANO, play, snap()), value(NANO, play, snap(running=True))], [0, 127])
        self.assertEqual([value(NANO, play, snap(running=True, paused=True), phase=ph) for ph in (0, 1)], [127, 0])
        self.assertEqual([value(PAD, stop, snap()), value(PAD, stop, snap(running=True))], [12, 13])
        self.assertEqual([value(NANO, stop, snap()), value(NANO, stop, snap(running=True))], [127, 0])           # lit while nothing plays

    def test_room_scenes_and_the_bank_buttons(self):
        room = snap(scenes=["aaaaaaaa", "bbbbbbbb"])
        s = lambda n: {"action": "scene_%d" % n}
        self.assertEqual([value(PAD, s(1), room), value(PAD, s(3), room), value(PAD, s(1), snap()), value(PAD, s(2), dict(room, applying="bbbbbbbb"))], [29, 12, 12, 28])
        self.assertEqual([value(NANO, s(1), room), value(NANO, s(3), room)], [127, 0])
        self.assertEqual([value(NANO, s(2), dict(room, applying="bbbbbbbb"), phase=ph) for ph in (0, 1)], [127, 0])
        by_id = {"action": "scene", "scene": "bbbbbbbb"}
        self.assertEqual([midi.light_state(by_id, room), midi.light_state(dict(by_id, scene="cccccccc"), room)], ["on", "off"])
        left, right = {"action": "bank_prev"}, {"action": "bank_next"}
        lights = BY_ID[MIX]["lights"]
        seen = [[midi.light_value(lights, "bank", midi.light_state(a, snap(), bank)) for a in (left, right)] for bank in (0, 1, 2)]
        self.assertEqual(seen, [[127, 0], [127, 127], [0, 127]])              # A: the left one; B: both; C: the right one

    def test_what_has_nothing_to_show_is_off(self):
        for p in BY_ID.values():
            for action in (None, {"action": "none"}, {"action": "reset"}, {"action": "opacity"}, {"action": "vibes_dwell"}):
                self.assertEqual(midi.light_value(p["lights"], midi.light_meaning(action), midi.light_state(action, snap())), p["lights"]["off"])
        self.assertEqual(midi.light_value(BY_ID[MIX]["lights"], "room", "on"), 0)       # a meaning this controller has no style for


class WriterBase(unittest.TestCase):
    KEYS = [bytes((0x90, n)) for n in range(120)] + [bytes((0xB0, n)) for n in range(104, 112)]
    LIGHTS = {"setup": [], "clear": [], "off": 0}

    def writer(self, pipe=None, lights=None, **kw):
        self.pipe = pipe or Pipe()
        w = LightWriter("/dev/snd/midiC9D0", "test", lights or self.LIGHTS, self.KEYS, open_fn=lambda path: self.pipe.w, log=lambda *_: None, **kw)
        self.addCleanup(w.stop, False)
        w.start()
        return w

    def wait(self, cond, timeout=6):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.01)
        self.assertTrue(cond(), "condition not met")


class WriterTest(WriterBase):
    def test_only_what_changed_is_sent(self):
        w = self.writer()
        table = {k: 0 for k in self.KEYS[:10]}
        w.show(table)
        self.wait(lambda: len(self.pipe.read()) == 10)                      # a fresh device gets the whole table, the offs too
        w.show(dict(table))
        w.show({**table, self.KEYS[3]: 127})
        self.wait(lambda: len(self.pipe.read()) == 11)
        time.sleep(0.2)
        self.assertEqual(self.pipe.read()[10:], [(0x90, 3, 127)])
        self.assertEqual((w.sent, w.state), (11, "on"))

    def test_many_tables_in_a_row_cost_one_change_per_light_not_one_per_table(self):
        w = self.writer()
        a, b = {k: 1 for k in self.KEYS}, {k: 2 for k in self.KEYS}
        w.show(a)
        self.wait(lambda: self.pipe.lit() == {tuple(k): 1 for k in self.KEYS})
        before = len(self.pipe.read())
        for n in range(2000):                                               # 256000 light changes asked for
            w.show(b if n % 2 == 0 else a)
        w.show(b)
        self.wait(lambda: self.pipe.lit() == {tuple(k): 2 for k in self.KEYS})
        self.assertLessEqual(len(self.pipe.read()) - before, 3 * len(self.KEYS))

    def test_never_more_than_two_hundred_messages_in_a_second(self):
        w = self.writer()
        stamps, stop = [], threading.Event()

        def flood():
            n = 0
            while not stop.is_set():
                n += 1
                w.show({k: n % 100 for k in self.KEYS})
                time.sleep(0.002)
        t = threading.Thread(target=flood, daemon=True)
        t.start()
        end, seen = time.monotonic() + 2.6, 0
        while time.monotonic() < end:
            got = len(self.pipe.read())
            stamps.extend([time.monotonic()] * (got - seen))
            seen = got
            time.sleep(0.004)
        stop.set()
        t.join(2)
        self.assertGreater(seen, 300)                                       # it was busy the whole time
        worst = max(sum(1 for s in stamps if t0 <= s < t0 + 1.0) for t0 in stamps)
        self.assertLessEqual(worst, 200 + 8, "more than 200 messages reached the device within a second")      # 8: the reader's own 4 ms steps

    def test_a_device_that_takes_nothing_blocks_nobody_and_gets_the_whole_state_afterwards(self):
        with mock.patch.object(midi, "LIGHT_RATE", 200000.0), mock.patch.object(midi, "LIGHT_BURST", 4000):
            w = self.writer()
            n, took = 0, []
            for n in range(1, 2000):                                        # nobody reads: the pipe fills
                t0 = time.monotonic()
                w.show({k: n % 100 for k in self.KEYS})
                took.append(time.monotonic() - t0)
                time.sleep(0.001)
            self.assertLess(max(took), 0.5)                                 # show() never waited for the device
            sent = w.sent
            time.sleep(0.3)
            self.assertEqual(w.sent, sent, "the writer kept counting while the device took nothing")
            self.assertEqual(w.state, "on")
            final = {k: 7 for k in self.KEYS}
            w.show(final)
            self.wait(lambda: self.pipe.lit() == {tuple(k): 7 for k in self.KEYS}, timeout=10)     # read again: everything arrives
            stream = self.pipe.data
            self.assertTrue(all((b >= 0x80) == (i % 3 == 0) for i, b in enumerate(stream)), "a message was cut in the stream")

    def test_a_write_that_is_cut_short_is_finished_before_anything_else(self):
        cut = [0]

        class Short(LightWriter):
            def _write(self, fd, data):
                cut[0] += 1
                return os.write(fd, data[:5] if cut[0] % 11 == 0 else data[:1] if cut[0] % 17 == 0 else data)
        self.pipe = Pipe()
        w = Short("/dev/snd/midiC9D0", "test", self.LIGHTS, self.KEYS, open_fn=lambda path: self.pipe.w, log=lambda *_: None)
        self.addCleanup(w.stop, False)
        w.start()
        for n in range(1, 60):
            w.show({k: n for k in self.KEYS})
            time.sleep(0.01)
        self.wait(lambda: self.pipe.lit() == {tuple(k): 59 for k in self.KEYS}, timeout=10)
        self.assertTrue(all((b >= 0x80) == (i % 3 == 0) for i, b in enumerate(self.pipe.data)))

    def test_unplugged_the_writer_ends_and_says_so(self):
        w = self.writer()
        w.show({self.KEYS[0]: 1})
        self.wait(lambda: self.pipe.read())
        self.pipe.unplug()
        w.show({self.KEYS[0]: 2})
        self.wait(lambda: not w.alive)
        self.assertEqual(w.state, "gone")

    def test_stop_switches_every_light_off(self):
        w = self.writer()
        w.show({k: 127 for k in self.KEYS})
        self.wait(lambda: len(self.pipe.lit()) == len(self.KEYS))
        t0 = time.monotonic()
        w.stop()
        self.assertLess(time.monotonic() - t0, 2.6)
        self.assertEqual((w.state, self.pipe.lit()), ("stopped", {tuple(k): 0 for k in self.KEYS}))
        # a profile with its own clear message (the Launchpad's reset) sends that instead
        w = self.writer(lights={"setup": [b"\xb0\x00\x00"], "clear": [b"\xb0\x00\x00"], "off": 12})
        w.show({self.KEYS[0]: 60})
        self.wait(lambda: self.pipe.read()[-1:] == [(0x90, 0, 60)])
        w.stop()
        self.assertEqual(self.pipe.read(), [(0xB0, 0, 0), (0x90, 0, 60), (0xB0, 0, 0)])

    def test_the_sweep_lights_one_after_another_and_goes_back(self):
        w = self.writer()
        keys = self.KEYS[:6]
        w.keys = keys
        w.show({k: (1 if i == 2 else 0) for i, k in enumerate(keys)})
        self.wait(lambda: len(self.pipe.read()) == 6)
        self.assertTrue(w.test({k: 100 + i for i, k in enumerate(keys)}))
        self.assertFalse(w.test({}))                                        # one sweep at a time
        self.wait(lambda: self.pipe.lit() == {tuple(k): 100 + i for i, k in enumerate(keys)})
        order = [m[1] for m in self.pipe.read()[6:] if m[2] >= 100]
        self.assertEqual(order, [0, 1, 2, 3, 4, 5])
        self.assertTrue(w.testing)
        self.wait(lambda: not w.testing and self.pipe.lit() == {tuple(k): (1 if i == 2 else 0) for i, k in enumerate(keys)})

    def test_no_permission_is_said_once_and_not_tried_again(self):
        tries = []

        def refuse(path):
            tries.append(path)
            raise PermissionError(1, "Operation not permitted")
        w = LightWriter("/dev/snd/midiC9D0", "test", self.LIGHTS, self.KEYS, open_fn=refuse, log=lambda *_: None)
        w.start()
        self.wait(lambda: not w.alive)
        self.assertEqual((w.state, tries), ("installer", ["/dev/snd/midiC9D0"]))
        self.assertFalse(w.test({}))
        w.show({self.KEYS[0]: 1})                                           # harmless on a writer that has ended
        w.stop()


class LightsHubBase(HubBase):
    def setUp(self):
        super().setUp()
        self.out, self.opens, self.refuse = {}, [], None

        def light_open(path):
            self.opens.append(path)
            if self.refuse:
                raise self.refuse
            self.out[path] = Pipe()
            return self.out[path].w
        self.hub._light_open_fn = light_open
        self.playing = [None]
        self.player.status = lambda: {"running": self.player.running, "path": self.playing[0], "paused": False, "playlist_count": 1}
        self.settings.data["pads"]["banks"][0]["pads"][0]["file"] = "a.mp4"

    def plug(self, *paths):
        self.present = sorted(set(self.present) | set(paths))
        self.enable()
        for p in paths:
            self.wait(lambda: p in self.pipes)

    def lights_of(self, name):
        return self.controller(name)["lights"]


C_NANO, C_MIX, C_PAD, C_KEYS, C_LAUNCHKEY = ("/dev/snd/midiC%dD0" % n for n in (1, 2, 3, 4, 5))


class LightsHubTest(LightsHubBase):
    def test_the_launchpad_lights_up_when_it_is_plugged_in_and_follows_the_box(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        pipe = self.out[C_PAD]
        self.assertEqual(pipe.read()[:2], [(0xB0, 0, 0), (0xB0, 0, 40)])    # the reference's reset first (X-Y layout, all off), then its flash mode
        lit = pipe.lit()
        self.assertEqual((lit[(0x90, 0)], lit[(0x90, 1)], lit[(0x90, 120)], lit[(0x90, 104)], lit[(0xB0, 106)]), (29, 12, 13, 12, 12))
        self.assertEqual(self.lights_of("Mini")["line"], "Lights on.")
        self.assertEqual(self.opens, [C_PAD])
        sent = len(pipe.read())
        time.sleep(1.0)                                                     # nothing changes on the box: nothing is sent
        self.assertEqual(len(pipe.read()), sent)
        self.playing[0] = os.path.join(self.media, "a.mp4")
        self.send(C_PAD, [0x90, 0, 127])                                    # the pad is pressed: it plays, and its light turns green
        self.wait(lambda: pipe.lit()[(0x90, 0)] == 60)
        self.assertEqual(pipe.lit()[(0x90, 104)], 13)                       # Stop: a dim red while something plays
        self.assertEqual(self.post("/api/blackout", {"on": True})[0], 200)
        self.wait(lambda: pipe.lit()[(0x90, 120)] == 11)                    # black: full red, flashing, within a tick
        self.post("/api/blackout", {"on": False})
        self.wait(lambda: pipe.lit()[(0x90, 120)] == 13)
        pad11 = next(x for x in self.controller("Mini")["controls"] if x["id"] == "pad11")
        self.assertEqual((pad11["light"], pad11["lit"]), (True, "active"))
        self.assertEqual(len(pipe.read()), sent + 7)                        # seven changes (the pad, Stop, Freeze, the two ten-second steps, black and back), seven messages

    def test_only_the_profiles_own_bytes_are_ever_written(self):
        self.present = [C_NANO, C_MIX, C_PAD, C_KEYS, C_LAUNCHKEY]
        self.enable()
        self.wait(lambda: len(self.pipes) == 5)
        for name in ("nanoKONTROL2", "Mix"):
            self.assertEqual(self.post("/api/midi", {"controller": name, "lights": True})[0], 200)
        self.wait(lambda: len(self.out) == 3)
        self.assertEqual(sorted(self.opens), [C_NANO, C_MIX, C_PAD])        # never a controller without a profile, never the Launchkey
        self.playing[0] = os.path.join(self.media, "a.mp4")
        self.player.running = True
        for name in ("nanoKONTROL2", "Mix", "Mini"):
            self.assertEqual(self.post("/api/midi/lights", {"controller": name, "test": True})[0], 200)
        self.post("/api/blackout", {"on": True})
        time.sleep(2.5)
        self.hub.stop()
        for path, pid in ((C_NANO, NANO), (C_MIX, MIX), (C_PAD, PAD)):
            p = BY_ID[pid]
            lights = p["lights"]
            allowed = {tuple(m) for m in lights["setup"] + lights["clear"]}
            values = {lights["off"]} | {v for style in lights["styles"].values() for lv in style.values() for k, v in lv.items() if k != "pulse"} \
                | {v for style in lights["styles"].values() for lv in style.values() for v in lv["pulse"].values()}
            for c in p["controls"]:
                if c["id"] in lights["controls"]:
                    allowed |= {tuple(midi.light_message(lights, c, v)) for v in values}
            got = self.out[path].read()
            self.assertGreater(len(got), 10, pid)
            self.assertEqual([m for m in got if m not in allowed], [], pid)
            self.assertEqual(len(self.out[path].data) % 3, 0)

    def test_defaults_the_switch_the_brightness_and_what_is_kept(self):
        self.plug(C_NANO, C_PAD)
        self.wait(lambda: C_PAD in self.out)
        nano, pad = self.lights_of("nanoKONTROL2"), self.lights_of("Mini")
        self.assertEqual((nano["on"], nano["default"], nano["state"], nano["line"], nano["levels"], nano["unverified"]), (False, False, "off", "Lights off.", False, True))
        self.assertIn("Set LED mode to External in Korg's editor first", nano["note"])
        self.assertEqual((pad["on"], pad["default"], pad["brightness"], pad["levels"], pad["unverified"]), (True, True, "low", True, False))
        self.assertNotIn(C_NANO, self.opens)                                # off until the owner switches it on
        self.assertNotIn("lights", self.settings.data["control"]["midi"])
        self.assertEqual(self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})[0], 200)
        self.wait(lambda: C_NANO in self.out and self.out[C_NANO].lit().get((0xB0, 32)) == 127)     # S 1: pad 1 of bank A holds a clip
        self.assertEqual(self.out[C_NANO].lit()[(0xB0, 42)], 127)           # Stop is lit while nothing plays
        self.wait(lambda: len(self.out[C_PAD].lit()) == 80)
        self.assertEqual(self.post("/api/midi", {"controller": "Mini", "brightness": "high"})[0], 200)
        self.wait(lambda: self.out[C_PAD].lit()[(0x90, 0)] == 46)           # the same amber, brighter; no new writer for it
        self.assertEqual(self.opens.count(C_PAD), 1)
        self.assertEqual(self.settings.data["control"]["midi"]["lights"], {"nanoKONTROL2": {"on": True}, "Mini": {"brightness": "high"}})
        # the standard layout switched off and on again does not forget the lights choice (it lives beside "controllers")
        self.post("/api/midi", {"controller": "Mini", "standard": False})
        self.post("/api/midi", {"controller": "Mini", "standard": True})
        self.assertEqual(self.settings.data["control"]["midi"]["lights"]["Mini"], {"brightness": "high"})
        self.assertEqual(self.post("/api/midi", {"controller": "Mini", "lights": False})[0], 200)
        self.wait(lambda: self.out[C_PAD].read()[-1] == (0xB0, 0, 0) and C_PAD not in self.hub.lights)
        self.assertEqual(self.lights_of("Mini")["line"], "Lights off.")
        for bad in ({"controller": "Mini", "lights": "on"}, {"controller": "Mini", "brightness": "max"}, {"controller": "Mini", "brightness": 3},
                    {"controller": "nope", "lights": True}, {"controller": "a/b", "lights": True}, {"controller": "Mini", "lights": True, "standard": True},
                    {"lights": True}, {"brightness": "low"}):
            self.assertEqual(self.post("/api/midi", bad)[0], 400, bad)

    def test_standard_layout_off_turns_the_lights_off_and_clears_them(self):
        self.plug(C_NANO)
        self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})
        self.wait(lambda: C_NANO in self.out and self.out[C_NANO].lit().get((0xB0, 32)) == 127)
        self.assertEqual(self.post("/api/midi", {"controller": "nanoKONTROL2", "standard": False})[0], 200)
        self.wait(lambda: C_NANO not in self.hub.lights and len(self.out[C_NANO].lit()) == 30 and set(self.out[C_NANO].lit().values()) == {0})
        self.assertEqual(len(self.out[C_NANO].lit()), 30)                   # each of its thirty lights was switched off
        self.assertEqual(self.lights_of("nanoKONTROL2")["line"], "Lights are off while the standard layout is off.")
        self.assertEqual(self.post("/api/midi/lights", {"controller": "nanoKONTROL2", "test": True})[0], 409)

    def test_unplugged_and_plugged_in_again_it_gets_the_whole_state(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        first = self.out.pop(C_PAD)
        reader = self.pipes.pop(C_PAD)
        self.present = []
        os.close(reader[1])                                                 # unplugged
        first.unplug()
        self.wait(lambda: not self.hub.inputs and C_PAD not in self.hub.lights)
        self.assertEqual(self.hub.status()["controllers"], [])
        self.present = [C_PAD]                                              # and back
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        again = self.out[C_PAD]
        self.assertEqual((again.read()[0], again.lit()[(0x90, 0)], len(again.read())), ((0xB0, 0, 0), 29, 82))       # the reset, flash mode, eighty lights

    def test_an_older_service_file_gives_one_clear_line_and_no_loop(self):
        self.refuse = PermissionError(1, "Operation not permitted")         # what a unit with "char-alsa r" answers to an open for writing
        self.plug(C_PAD)
        self.wait(lambda: self.lights_of("Mini")["state"] == "installer")
        self.assertEqual(self.lights_of("Mini")["line"], "Lights need the box's installer to run once.")
        time.sleep(1.5)                                                     # five looks at the lights later
        self.assertEqual(self.opens, [C_PAD])                               # it was tried once
        self.assertFalse([line for line in self.said if "light" in line.lower()])       # and nothing fills the log
        self.assertEqual(self.post("/api/midi/lights", {"controller": "Mini", "test": True})[0], 409)
        self.send(C_PAD, [0x90, 104, 127])                                  # the controls work as before
        self.wait(lambda: self.player.calls)
        self.refuse = None                                                  # the installer ran (the service restarts): here, the switch
        self.post("/api/midi", {"controller": "Mini", "lights": False})
        self.post("/api/midi", {"controller": "Mini", "lights": True})
        self.wait(lambda: self.lights_of("Mini")["state"] == "on")
        self.assertEqual(self.opens, [C_PAD, C_PAD])

    def test_a_device_that_fails_does_not_disturb_the_others(self):
        self.plug(C_NANO, C_PAD)
        self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})
        self.wait(lambda: C_NANO in self.out and C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        self.out[C_NANO].unplug()                                           # its output breaks while the controller stays listed
        self.post("/api/blackout", {"on": True})
        self.wait(lambda: self.out[C_PAD].lit()[(0x90, 120)] == 11)         # the Launchpad goes on
        self.wait(lambda: self.lights_of("nanoKONTROL2")["state"] == "failed")
        self.assertEqual(self.lights_of("Mini")["state"], "on")
        before = len(self.player.calls)
        self.send(C_NANO, [0xB0, 42, 127])                                  # and the nanoKONTROL2's controls still work: Stop
        self.wait(lambda: len(self.player.calls) > before)

    def test_test_lights_and_the_roles(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        guest = self.call("POST", "/api/devices/invite", {"role": "live"}, token=self.full)[1]["token"]
        view = self.call("POST", "/api/devices/invite", {"role": "view"}, token=self.full)[1]["token"]       # the lights are the Operator's since D80
        self.assertEqual(self.call("POST", "/api/midi/lights", {"controller": "Mini", "test": True}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/midi", {"controller": "Mini", "lights": False}, token=view)[0], 403)
        seen = self.call("GET", "/api/midi", token=guest)
        self.assertEqual((seen[0], seen[1]["controllers"][0]["lights"]["state"]), (200, "on"))       # a presenter may look
        for bad in ({"controller": "Mini"}, {"controller": "Mini", "test": False}, {"controller": "Mini", "test": True, "bytes": [144, 0, 127]},
                    {"controller": "Mini", "test": True, "value": 63}, {"test": True}, {"controller": 7, "test": True}):
            self.assertEqual(self.post("/api/midi/lights", bad)[0], 400, bad)
        self.assertEqual(self.post("/api/midi/lights", {"controller": "keys", "test": True})[0], 409)
        before = len(self.out[C_PAD].read())
        st, body, _ = self.post("/api/midi/lights", {"controller": "Mini", "test": True})
        self.assertEqual((st, body["controllers"][0]["lights"]["testing"]), (200, True))
        self.assertEqual(self.post("/api/midi/lights", {"controller": "Mini", "test": True})[0], 409)       # one sweep at a time
        self.wait(lambda: min(self.out[C_PAD].lit().values()) > 12, timeout=8)        # every one of the eighty is lit
        self.wait(lambda: not self.lights_of("Mini")["testing"] and self.out[C_PAD].lit()[(0x90, 1)] == 12, timeout=8)
        self.assertEqual(self.out[C_PAD].lit()[(0x90, 0)], 29)                         # and back to what the box says
        order = [m[:2] for m in self.out[C_PAD].read()[before:] if m[2] != 12][:80]
        self.assertEqual(order[:9], [(0xB0, 104 + n) for n in range(8)] + [(0x90, 0)])      # in the drawn order, top row first

    def test_the_lights_go_off_when_midi_is_switched_off_and_at_shutdown(self):
        self.plug(C_NANO, C_PAD)
        self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})
        self.wait(lambda: C_NANO in self.out and self.out[C_NANO].lit().get((0xB0, 32)) == 127 and len(self.out[C_PAD].lit()) == 80)
        self.assertEqual(self.post("/api/midi", {"enabled": False})[0], 200)
        self.assertEqual(self.out[C_PAD].read()[-1], (0xB0, 0, 0))
        self.assertEqual(set(self.out[C_NANO].lit().values()), {0})
        self.assertEqual((self.hub.lights, self.hub._ending), ({}, []))
        self.out.clear()
        self.assertEqual(self.post("/api/midi", {"enabled": True})[0], 200)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        self.hub.stop()                                                     # the panel is shutting down
        self.assertEqual(self.out[C_PAD].read()[-1], (0xB0, 0, 0))
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("midi-lights")])

    def test_a_control_the_person_changed_shows_what_it_does_now(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        self.assertEqual(self.out[C_PAD].lit()[(0x90, 6)], 12)              # a pad with nothing on it is dark
        self.assertEqual(self.post("/api/midi/map", {"set": {"controller": "Mini", "control": "pad17", "action": {"action": "blackout", "guard": False}}})[0], 200)
        self.wait(lambda: self.out[C_PAD].lit()[(0x90, 6)] == 13)
        self.post("/api/midi/map", {"set": {"controller": "Mini", "control": "pad11", "action": {"action": "none"}}})
        self.wait(lambda: self.out[C_PAD].lit()[(0x90, 0)] == 12)

    def test_the_choice_goes_round_through_export_and_import(self):
        d = self.settings.data["control"]
        d["midi"]["lights"] = {"Mini": {"on": False, "brightness": "medium"}, "nanoKONTROL2": {"on": True}}
        self.assertEqual(boxcare.check_control(copy.deepcopy(d), None), d)
        for bad in ({"Mini": {"on": 1}}, {"Mini": {"brightness": "max"}}, {"Mini": {}}, {"Mini": {"on": True, "bytes": [1]}}, {"a/b": {"on": True}}, [], "x",
                    {"c%d" % i: {"on": True} for i in range(33)}):
            broken = copy.deepcopy(d)
            broken["midi"]["lights"] = bad
            with self.assertRaises((ValueError, midi.MidiError), msg=str(bad)[:60]):
                boxcare.check_control(broken, None)
        for junk in ("x", [], {"Mini": "x"}, {"Mini": {"on": "no", "brightness": 9}}):       # a hand-edited file reads as the default
            d["midi"]["lights"] = junk
            self.assertEqual(self.hub.light_choice("Mini", BY_ID[PAD]), (True, "low"), junk)
            self.assertEqual(self.hub.light_choice("nanoKONTROL2", BY_ID[NANO]), (False, "low"), junk)


class ReviewFindingsTest(LightsHubBase):
    """What the independent security review of pull request #82 found, each with its test."""

    UNREADABLE = {"name": None, "usbid": None, "readable": False}

    def describe(self, answers):
        def describer(path):
            a = answers.get(path, self.PRODUCTS[path])
            if isinstance(a, Exception):
                raise a
            return a() if callable(a) else a
        self.hub._describer = describer

    def test_m1_a_controller_known_by_its_card_id_alone_is_read_but_never_written_to(self):
        # /proc/asound could not be read for the card, or reading it raised: the card id ("Mini") still gives the
        # layout, as before, and that is not enough to send the device a reset and eighty note-ons
        for answer in (self.UNREADABLE, OSError("no /proc/asound"), None, {"name": "Mini", "usbid": "ffff:0001", "readable": True}):
            self.opens.clear()
            self.describe({C_PAD: answer, C_LAUNCHKEY: self.UNREADABLE})
            self.present = [C_PAD, C_LAUNCHKEY]
            self.enable()
            self.wait(lambda: len(self.pipes) == 2)
            readable = isinstance(answer, dict) and answer["readable"]
            if not readable:
                self.wait(lambda: self.controller("Mini")["profile"] is not None)
                self.assertEqual(self.controller("Mini")["profile"]["id"], PAD)             # the layout is there (the weaker match, as before)
                self.assertEqual(self.controller("Mini_1")["profile"]["id"], PAD)           # and a Launchkey Mini with no readable name gets it too
            time.sleep(1.2)                                                     # four looks at the lights
            self.assertEqual(self.opens, [], answer)                            # nothing was opened for writing
            self.assertEqual(self.out, {})
            if not readable:
                lights = self.lights_of("Mini")
                self.assertEqual((lights["on"], lights["state"]), (True, "unsure"))
                self.assertIn("could not make sure which controller this is", lights["line"])
                self.assertEqual(self.post("/api/midi/lights", {"controller": "Mini", "test": True})[0], 409)
                before = len(self.player.calls)
                self.send(C_PAD, [0x90, 104, 127, 0x90, 104, 0])                # its controls work as before (Stop, pressed and let go)
                self.wait(lambda: len(self.player.calls) > before)
            self.settings.data["control"]["midi"]["enabled"] = False
            self.hub.apply()
            self.pipes.clear()

    def test_m1_a_usb_id_or_a_readable_product_name_is_what_lets_a_writer_start(self):
        self.describe({C_PAD: {"name": None, "usbid": "1235:0036", "readable": True}})
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        self.assertTrue(midi.sure_match(BY_ID[PAD], {"name": "Launchpad Mini", "usbid": None, "readable": True}))
        self.assertTrue(midi.sure_match(BY_ID[PAD], "Launchpad Mini"))
        for weak in (None, self.UNREADABLE, {"name": "Launchkey Mini", "usbid": None, "readable": True}, {"name": None, "usbid": "1235:0037", "readable": True},
                     "Mini", {"name": "", "usbid": "", "readable": True}, 7):
            self.assertFalse(midi.sure_match(BY_ID[PAD], weak), weak)
        self.assertFalse(midi.sure_match(None, "Launchpad Mini"))

    def test_m1_a_weak_answer_is_not_kept_as_good_and_a_later_good_one_starts_the_lights(self):
        answers = [self.UNREADABLE]
        self.describe({C_PAD: lambda: answers[0]})
        with mock.patch.object(midi, "LIGHT_RETRY", 0.4):
            self.plug(C_PAD)
            self.wait(lambda: self.lights_of("Mini")["state"] == "unsure")
            time.sleep(1.0)
            self.assertEqual(self.opens, [])                                    # asked again meanwhile, still unsure, still nothing
            answers[0] = "Launchpad Mini"                                       # the card list can be read now
            self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
            self.assertEqual(self.lights_of("Mini")["state"], "on")

    def test_l5_a_profile_without_a_lights_section_is_never_opened_for_writing(self):
        plain = []
        for p in midi.load_profiles(log=lambda *_: None):
            plain.append(dict(p, lights=None))
        self.hub.profiles = plain
        self.present = [C_NANO, C_MIX, C_PAD, C_KEYS]
        self.enable()
        self.wait(lambda: len(self.pipes) == 4)
        self.wait(lambda: self.controller("Mini")["profile"] is not None)
        self.assertEqual(self.post("/api/midi", {"controller": "Mini", "lights": True})[0], 200)
        time.sleep(1.0)
        self.assertEqual((self.opens, self.hub.lights, self.lights_of("Mini")), ([], {}, None))
        self.assertEqual(self.post("/api/midi/lights", {"controller": "Mini", "test": True})[0], 409)

    def test_l1_off_and_on_while_the_player_is_slow_leaves_one_lights_thread(self):
        self.plug(C_PAD)
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        inside, let_go = threading.Event(), threading.Event()
        self.addCleanup(let_go.set)

        def slow():
            inside.set()
            let_go.wait(30)                                                     # mpv hangs: a status call that takes its time
            return {"running": False, "path": None}
        self.player.status = slow
        self.hub._player_seen = None
        self.hub._light_wake.set()
        self.assertTrue(inside.wait(5))

        def loops():
            return [t for t in threading.enumerate() if t.name == "midi-lights-state" and t.is_alive()]
        old = loops()
        self.assertEqual(len(old), 1)
        self.settings.data["control"]["midi"]["enabled"] = False
        self.hub.apply()                                                        # the join gives up after three seconds: the loop is still inside
        self.assertTrue(old[0].is_alive())
        self.enable()                                                           # and on again: a new loop
        self.wait(lambda: len(loops()) == 2)
        let_go.set()                                                            # the player answers at last
        self.wait(lambda: len(loops()) == 1)
        self.assertFalse(old[0].is_alive())                                     # the old loop ended; it did not carry on beside the new one
        self.wait(lambda: C_PAD in self.hub.lights and self.lights_of("Mini")["state"] == "on")

    def test_l4_the_set_up_message_goes_once_per_plug_in_not_once_per_try(self):
        with mock.patch.object(midi, "LIGHT_RETRY", 0.4):
            self.plug(C_PAD)
            self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
            first = self.out[C_PAD]
            self.assertEqual(first.read()[0], (0xB0, 0, 0))
            first.unplug()                                                      # its output breaks; the controller stays plugged in
            self.post("/api/blackout", {"on": True})                            # the next write fails, and the writer is tried again
            self.wait(lambda: self.out[C_PAD] is not first and len(self.out[C_PAD].lit()) == 80, timeout=8)
            again = self.out[C_PAD].read()
            self.assertNotIn((0xB0, 0, 0), again)                               # the whole state, and no second reset
            self.assertEqual((len(again), self.opens), (80, [C_PAD, C_PAD]))
            self.assertEqual(self.out[C_PAD].lit()[(0x90, 120)], 11)
        # plugged in again, it is a new plug-in: the reset goes first again
        reader = self.pipes.pop(C_PAD)
        self.present = []
        os.close(reader[1])
        self.wait(lambda: not self.hub.inputs and C_PAD not in self.hub.lights)
        self.out.pop(C_PAD)
        self.present = [C_PAD]
        self.wait(lambda: C_PAD in self.out and len(self.out[C_PAD].lit()) == 80)
        self.assertEqual(self.out[C_PAD].read()[0], (0xB0, 0, 0))

    def test_l6_the_switch_and_the_brightness_take_nothing_else(self):
        self.plug(C_PAD)
        for bad in ({"controller": "Mini", "lights": True, "bytes": [240, 1, 247]}, {"controller": "Mini", "brightness": "low", "enabled": False},
                    {"controller": "Mini", "lights": False, "builtin": False}, {"controller": "Mini", "lights": True, "test": True},
                    {"controller": "Mini", "brightness": "high", "value": 63}):
            before = copy.deepcopy(self.settings.data["control"]["midi"])
            self.assertEqual(self.post("/api/midi", bad)[0], 400, bad)
            self.assertEqual(self.settings.data["control"]["midi"], before)     # and nothing of it was applied
        self.assertEqual(self.post("/api/midi", {"controller": "Mini", "lights": True, "brightness": "medium"})[0], 200)


class CloseTest(WriterBase):
    """L2: the kernel drains a rawmidi output when it is closed, for up to ten seconds; bytes that a controller is
    not taking are dropped first so the close is quick. A pipe has no such ioctl, so the call is watched."""

    def test_the_number_is_the_kernels(self):
        # _IOW('W', 0x30, int): write direction 1 in the top two bits, the size of an int, the type, the number
        self.assertEqual(midi.SNDRV_RAWMIDI_IOCTL_DROP, (1 << 30) | (4 << 16) | (ord("W") << 8) | 0x30)
        self.assertEqual(midi.SNDRV_RAWMIDI_STREAM_OUTPUT, 0)

    def test_waiting_bytes_are_dropped_before_the_close_and_only_then(self):
        calls = []

        def ioctl(fd, request, arg):
            calls.append((request, bytes(arg)))
            try:
                os.fstat(fd)
                calls.append("open")                                            # it came before the close
            except OSError:
                calls.append("closed")
            return 0
        with mock.patch.object(midi.fcntl, "ioctl", ioctl):
            w = self.writer()                                                   # a controller that takes its bytes
            w.show({k: 1 for k in self.KEYS[:10]})
            self.wait(lambda: len(self.pipe.read()) == 10)
            w.stop()
            self.assertEqual((calls, w.dropped, w.state), ([], False, "stopped"))
            with mock.patch.object(midi, "LIGHT_RATE", 200000.0), mock.patch.object(midi, "LIGHT_BURST", 4000):
                w = self.writer()                                               # one that takes nothing: the pipe fills
                for n in range(1, 1500):
                    w.show({k: n % 100 for k in self.KEYS})
                    if w._stuck:
                        break
                    time.sleep(0.001)
                self.assertTrue(w._stuck)
                t0 = time.monotonic()
                w.stop()
                self.assertLess(time.monotonic() - t0, 2.6)
            import struct
            self.assertEqual(calls, [(0x40045730, struct.pack("i", 0)), "open"])
            self.assertTrue(w.dropped)

    def test_a_drop_that_fails_is_tolerated(self):
        def ioctl(fd, request, arg):
            raise OSError(25, "Inappropriate ioctl for device")
        with mock.patch.object(midi.fcntl, "ioctl", ioctl):
            w = self.writer()
            w._stuck = True
            w.stop(False)
            self.assertEqual((w.state, w.dropped), ("stopped", False))
            with self.assertRaises(OSError):
                os.fstat(self.pipe.w)                                           # and the handle was closed all the same


class LightsNeverWaitTest(Live):
    """The lights are made from memory and one player status call: with the shader engine's lock held (the GPU
    looking at a shader) the picture of the box is still taken, and a controller's press is still acted on."""

    def test_the_picture_of_the_box_is_taken_with_the_engine_lock_held(self):
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        self.api.registry.set_enabled("control-midi", True)
        self.settings.data["control"]["midi"]["enabled"] = True
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], describer=lambda p: None)
        self.api.midi = hub
        self.addCleanup(hub.stop)
        got = []

        def run():
            got.append(hub._snapshot(1.0, fresh=True))
            got.append(hub._light_table("Mini", BY_ID[PAD], "low", got[0], 0, 0, hub.cfg()))
            hub._lights_tick()
            got.append(hub.status())
        with self.engine._lock:
            t = threading.Thread(target=run, daemon=True)
            t0 = time.monotonic()
            t.start()
            t.join(10)
            self.assertFalse(t.is_alive(), "the lights waited for the engine's lock")
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(len(got), 3)
        self.assertEqual((got[0]["shader"], got[0]["vibes_ready"], len(got[1][0])), ("all.fs", True, 80))


if __name__ == "__main__":
    unittest.main()
