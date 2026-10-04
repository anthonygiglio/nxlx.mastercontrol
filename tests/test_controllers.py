# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Controller profiles: the shipped files, matching, precedence, pickup, the double press, hot-plug, the API.

Nothing here has touched a real controller: pipes and direct calls stand in for the devices."""
import copy
import json
import os
import random
import tempfile
import threading
import time
import unittest

from pvj import boxcare, midi, vibes as V
from pvj.midi import MidiHub, MidiMapper
from tests.test_midi import Recorder
from tests.test_server import ServerBase
from tests.test_shader_engine import ALL, Live

PROFILES = midi.load_profiles(log=lambda *_: None)
BY_ID = {p["id"]: p for p in PROFILES}
NANO, MIX, PAD = "korg-nanokontrol2", "akai-midimix", "novation-launchpad-mini"
ROOT = os.path.join(os.path.dirname(__file__), "..")


def control(profile, cid):
    return next(c for c in BY_ID[profile]["controls"] if c["id"] == cid)


def raw(name):
    with open(os.path.join(midi.PROFILE_DIR, name + ".json")) as f:
        return json.load(f)


class ProfileFilesTest(unittest.TestCase):
    def test_every_shipped_file_passes_the_strict_check(self):
        files = sorted(n[:-5] for n in os.listdir(midi.PROFILE_DIR) if n.endswith(".json"))
        self.assertEqual(files, sorted(BY_ID))                         # none was left out for a mistake
        self.assertEqual(sorted(BY_ID), [MIX, NANO, PAD])
        for name in files:
            self.assertEqual(midi.validate_profile(raw(name), name)["id"], name)

    def test_every_listed_control_maps_to_an_action_the_api_has(self):
        for p in PROFILES:
            entries = midi.profile_entries(p, "x")
            self.assertEqual(len(entries), sum(c["action"] is not None for c in p["controls"]))
            for e in entries:
                self.assertIn(e["action"], midi.ACTIONS, p["id"])
                clean = midi.validate_entry({k: v for k, v in e.items() if k not in ("id", "profile", "guard", "pickup")})
                self.assertEqual(clean["action"], e["action"])
            for c in p["controls"]:                                    # a fader or knob is followed, a button or pad is pressed
                if c["action"]:
                    kind = midi.ACTIONS[c["action"]["action"]][0]
                    self.assertTrue(kind != "trigger" if c["kind"] in ("fader", "knob") else kind != "level", (p["id"], c["id"]))

    def test_the_layouts_are_what_the_docs_say(self):
        self.assertEqual([len(BY_ID[i]["controls"]) for i in (NANO, MIX, PAD)], [51, 60, 80])
        for p in (NANO, MIX):                                          # the same eight faders on both, so the hand finds them
            self.assertEqual([(control(p, "fader%d" % n)["action"] or {}).get("action") for n in range(1, 9)],
                             ["opacity", "volume", "speed", "shader_speed", "shader_hue", "shader_brightness", None, None])
        for p, first in ((NANO, "knob1"), (MIX, "knob_a1")):
            self.assertEqual(control(p, first)["action"], {"action": "shader_control_1"})
        self.assertEqual((control(NANO, "fader1")["send"], control(NANO, "knob8")["send"], control(NANO, "play")["send"]["number"]),
                         ({"type": "cc", "channel": 0, "number": 0}, {"type": "cc", "channel": 0, "number": 23}, 41))
        self.assertEqual((control(MIX, "fader5")["send"]["number"], control(MIX, "mute8")["send"], control(MIX, "master")["send"]["number"]),
                         (49, {"type": "note", "channel": 0, "number": 22}, 62))
        # the Launchpad's X-Y layout: 16 x row + column, the side buttons are column 8, the top row is CC 104 to 111
        self.assertEqual([control(PAD, i)["send"]["number"] for i in ("pad11", "pad25", "pad78", "side_a", "side_h", "top1", "top8")], [0, 20, 103, 8, 120, 104, 111])
        self.assertEqual((control(PAD, "pad11")["action"], control(PAD, "pad26")["action"], control(PAD, "pad61")["action"]),
                         ({"action": "pad", "bank": 0, "index": 0}, {"action": "pad", "bank": 0, "index": 11}, {"action": "pad", "bank": 2, "index": 6}))

    def test_what_darkens_the_screen_or_changes_the_room_needs_the_press_twice(self):
        for p in PROFILES:
            for c in p["controls"]:
                name = c["action"]["action"] if c["action"] else ""
                if name == "blackout" or name.startswith("scene_"):
                    self.assertTrue(c["guard"], (p["id"], c["id"]))
                    self.assertIn(c["kind"], ("button", "pad"))
                self.assertNotIn(name, ("reset", "blackout_hold"), (p["id"], c["id"]))    # not in any standard layout

    def test_what_no_manufacturer_document_confirms_is_marked(self):
        for p in (NANO, MIX):
            self.assertTrue(all(c["unverified"] for c in BY_ID[p]["controls"]), p)
            self.assertIn("Unverified against the manufacturer", BY_ID[p]["note"])
        self.assertFalse(any(c["unverified"] for c in BY_ID[PAD]["controls"]))
        for p in PROFILES:
            self.assertIn("Not yet confirmed by pressing every control", p["note"])
            self.assertTrue(p["sources"])

    def test_a_bad_file_is_refused_and_says_why(self):
        good = raw(NANO)

        def broken(change):
            p = copy.deepcopy(good)
            change(p)
            return p
        bad = [lambda p: p.update(extra=1), lambda p: p.pop("sources"), lambda p: p.update(id="Korg"), lambda p: p.update(id="other"),
               lambda p: p["match"].update(card_ids=[]), lambda p: p["match"].update(card_ids=["^Mini$"]), lambda p: p["match"].update(card_ids=["a{9999}"]),
               lambda p: p["match"].update(card_ids=["("]), lambda p: p["match"].update(more=[]), lambda p: p["match"].update(card_ids=["(.*)*(.*)*(.*)*x"]),
               lambda p: p["match"].update(card_names=["Mini.*"]), lambda p: p["match"].update(card_names=["a|b"]), lambda p: p["match"].update(usb_ids=["1235"]),
               lambda p: p["match"].update(usb_ids=["1235:00ZZ"]), lambda p: p["match"].update(usb_ids="1235:0036"), lambda p: p["match"].pop("card_names"),
               lambda p: p["controls"][0].update(action={"action": {}}), lambda p: p["controls"][0].update(action={"action": ["stop"]}),
               lambda p: p["controls"][0].update(action={"action": None}), lambda p: p["controls"][0].update(action={"action": "blackout", "guard": True}), lambda p: p["layout"].update(rows=0),
               lambda p: p["layout"].update(cols=99), lambda p: p.update(controls=[]), lambda p: p["controls"][0].update(row=5),
               lambda p: p["controls"][0].update(kind="wheel"), lambda p: p["controls"][0].update(id=p["controls"][1]["id"]),
               lambda p: p["controls"][0].update(row=p["controls"][1]["row"], col=p["controls"][1]["col"]),
               lambda p: p["controls"][0]["send"].update(number=128), lambda p: p["controls"][0]["send"].update(type="program"),
               lambda p: p["controls"][0]["send"].update(number=p["controls"][1]["send"]["number"]),
               lambda p: p["controls"][0].update(action={"action": "shutdown"}), lambda p: p["controls"][0].update(action={"action": "pad"}),
               lambda p: p["controls"][0].update(action={"action": "scene", "scene": "abcd1234"}), lambda p: p["controls"][0].update(action={"action": "none"}),
               lambda p: p["controls"][0].update(action={"action": "opacity"}),                 # a button cannot follow a level
               lambda p: p["controls"][-1].update(action={"action": "stop"}),                   # a fader cannot be pressed
               lambda p: p["controls"][-1].update(guard=True), lambda p: p["controls"][0].update(lights={"on": 1}),
               lambda p: p["controls"][0].update(name="x" * 25), lambda p: p["controls"][0].update(name="bad\nname"), lambda p: p.update(name=5)]
        for i, change in enumerate(bad):
            with self.assertRaises(midi.MidiError, msg="case %d" % i):
                midi.validate_profile(broken(change), NANO)
        for junk in ([], None, "x", 5):
            with self.assertRaises(midi.MidiError):
                midi.validate_profile(junk)

    def test_a_broken_file_in_the_folder_is_left_out_and_the_others_load(self):
        d, said = tempfile.mkdtemp(), []
        for name, text in (("korg-nanokontrol2.json", json.dumps(raw(NANO))), ("broken.json", "{not json"), ("wrong.json", json.dumps({"id": "wrong"})),
                           ("notes.txt", "ignored")):
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        self.assertEqual([p["id"] for p in midi.load_profiles(d, said.append)], [NANO])
        self.assertEqual(len(said), 2)
        self.assertEqual(midi.load_profiles(os.path.join(d, "missing"), said.append), [])
        for name, text in (("deep.json", "[" * 100000), ("odd.json", json.dumps(dict(raw(NANO), id="odd", controls=[{"id": "a", "name": "A", "row": 0, "col": 0,
                           "kind": "button", "send": {"type": "cc", "channel": 0, "number": 1}, "action": {"action": {"x": 1}}}]))), ("bytes.json", "\udcff")):
            with open(os.path.join(d, name), "w", errors="surrogateescape") as f:
                f.write(text)                                            # none of these may take the hub down with it
        self.assertEqual([p["id"] for p in midi.load_profiles(d, said.append)], [NANO])
        for junk in ({}, [], 5, None, {"x": 1}):                         # an action name that is not text is refused, never a crash
            with self.assertRaises(midi.MidiError):
                midi.validate_entry({"kind": "cc", "number": 1, "action": junk})

    def test_the_files_are_covered_by_the_licence_file(self):
        with open(os.path.join(ROOT, "REUSE.toml")) as f:
            self.assertIn('"pvj/controllers.d/**"', f.read())


# /proc/asound/cards as the owner's Pi shows it with the three controllers plugged in (ids, names and USB ids as reported
# from that Pi on 2026-10-05; the HDMI and capture rows around them are typical, not copied)
REAL_CARDS = (" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n                      vc4-hdmi-0\n"
              " 1 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n                      vc4-hdmi-1\n"
              " 2 [nanoKONTROL2   ]: USB-Audio - nanoKONTROL2\n                      KORG INC. nanoKONTROL2 at usb-0000:01:00.0-1.2.1, full speed\n"
              " 3 [Mix            ]: USB-Audio - MIDI Mix\n                      AKAI MIDI Mix at usb-0000:01:00.0-1.2.2, full speed\n"
              " 4 [Mini           ]: USB-Audio - Launchpad Mini\n                      Focusrite A.E. Ltd Launchpad Mini at usb-0000:01:00.0-1.2.3, full speed\n")
REAL_IDS = {2: ("nanoKONTROL2", "0944:0117"), 3: ("Mix", "09e8:0031"), 4: ("Mini", "1235:0036")}


def asound(cards, ids, binary=False):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "cards"), "wb") as f:
        f.write(cards if binary else cards.encode())
    for n, (card_id, usbid) in ids.items():
        os.makedirs(os.path.join(d, "card%d" % n))
        with open(os.path.join(d, "card%d" % n, "id"), "w") as f:
            f.write(card_id + "\n")
        if usbid:
            with open(os.path.join(d, "card%d" % n, "usbid"), "w") as f:
                f.write(usbid + "\n")
    return d


class MatchTest(unittest.TestCase):
    def test_the_real_card_list_of_the_owners_pi(self):
        d = asound(REAL_CARDS, {**REAL_IDS, 0: ("vc4hdmi0", None)})
        got = {n: midi.card_info("/dev/snd/midiC%dD0" % n, d) for n in (0, 2, 3, 4, 9)}
        self.assertEqual(got[2], {"name": "nanoKONTROL2", "usbid": "0944:0117", "readable": True})
        self.assertEqual(got[3], {"name": "MIDI Mix", "usbid": "09e8:0031", "readable": True})
        self.assertEqual(got[4], {"name": "Launchpad Mini", "usbid": "1235:0036", "readable": True})
        self.assertEqual((got[0]["name"], got[0]["usbid"], got[9]["readable"]), ("vc4-hdmi-0", None, False))
        for n, want in ((2, NANO), (3, MIX), (4, PAD)):
            self.assertEqual(midi.match_profile(PROFILES, REAL_IDS[n][0], got[n])["id"], want)
            self.assertEqual(midi.match_profile(PROFILES, REAL_IDS[n][0], dict(got[n], usbid=None))["id"], want)     # by id and name alone too
            self.assertEqual(midi.match_profile(PROFILES, "whatever", got[n])["id"], want)                            # and the USB id decides by itself
        self.assertEqual([p["match"]["usb_ids"] for p in (BY_ID[NANO], BY_ID[MIX], BY_ID[PAD])], [["0944:0117"], ["09e8:0031"], ["1235:0036"]])

    def test_by_card_id_and_by_the_cards_product_name(self):
        for card, name, want in (("nanoKONTROL2", None, NANO), ("nanoKONTROL2", "nanoKONTROL2", NANO), ("nanoKONTROL2_1", "nanoKONTROL2", NANO),
                                 ("Mix", "MIDI Mix", MIX), ("Mix", None, MIX), ("Mini", "Launchpad Mini", PAD), ("Mini_2", "Launchpad Mini", PAD),
                                 ("Mini", "Launchkey Mini", None),          # another product whose card id is also "Mini"
                                 ("Mini", "X Launchpad Mini", None), ("Mini", "Launchpad Mini MK3", None),     # no prefix and no suffix is accepted
                                 ("Mix", "Some Other Mix", None), ("Minimal", None, None), ("xMini", None, None), ("nanoKONTROL", None, None),
                                 ("MK3", "Launchpad Mini MK3", None), ("midiC1D0", None, None), ("Mini_x", None, None), ("Mini_1_1", None, None)):
            found = midi.match_profile(PROFILES, card, name)
            self.assertEqual(found["id"] if found else None, want, (card, name))

    def test_a_card_list_that_was_read_but_gives_no_usable_name_does_not_match_by_id_alone(self):
        unknown = {"name": None, "usbid": None, "readable": True}
        self.assertIsNone(midi.match_profile(PROFILES, "Mini", unknown))
        self.assertEqual(midi.match_profile(PROFILES, "Mini", {"name": None, "usbid": None, "readable": False})["id"], PAD)   # nothing to look at: the id decides
        self.assertEqual(midi.match_profile(PROFILES, "Mini", {"name": None, "usbid": "1235:0036", "readable": True})["id"], PAD)
        self.assertIsNone(midi.match_profile(PROFILES, "Mini", {"name": "Launchkey Mini", "usbid": "1235:0123", "readable": True}))
        self.assertIsNone(midi.match_profile(PROFILES, None, None))

    def test_the_card_list_cannot_confuse_the_reader(self):
        # one byte that is not UTF-8, anywhere in the file: the other cards are still read, by name
        d = asound(REAL_CARDS.encode() + b" 5 [odd            ]: USB-Audio - caf\xe9 box\n", {**REAL_IDS, 5: ("odd", None)}, binary=True)
        self.assertEqual(midi.card_info("/dev/snd/midiC4D0", d)["name"], "Launchpad Mini")
        self.assertEqual(midi.card_info("/dev/snd/midiC5D0", d), {"name": None, "usbid": None, "readable": True})
        # a Launchkey Mini with a letter that is not ASCII, and one with an over-long name: read, unusable, so no match
        for name in ("Launchkey Mini \u00e9", "Launchkey Mini " + "x" * 80):
            d = asound(" 2 [Mini           ]: USB-Audio - %s\n                      Novation %s at usb-1, full speed\n" % (name, name), {2: ("Mini", None)})
            info = midi.card_info("/dev/snd/midiC2D0", d)
            self.assertEqual((info["name"], info["readable"]), (None, True), name)
            self.assertIsNone(midi.match_profile(PROFILES, "Mini", info), name)
        # the long name of one card made to look like another card's row: only the row with this card's own id counts
        forged = (" 2 [Mini           ]: USB-Audio - Launchkey Mini\n                       3 [Mini           ]: USB-Audio - Launchpad Mini\n"
                  " 3 [keys           ]: USB-Audio - Launchpad Mini\n 4 [Other          ]: USB-Audio - x\n 2 [Mini           ]: USB-Audio - Launchpad Mini\n")
        d = asound(forged, {2: ("Mini", None), 3: ("Mini_1", None), 4: ("Other", None)})
        self.assertEqual(midi.card_info("/dev/snd/midiC2D0", d)["name"], "Launchkey Mini")       # the first row that is really card 2's
        self.assertEqual(midi.card_info("/dev/snd/midiC3D0", d)["name"], None)                   # row 3 carries another id: not this card's row
        self.assertIsNone(midi.match_profile(PROFILES, "Mini_1", midi.card_info("/dev/snd/midiC3D0", d)))
        self.assertEqual(midi.card_info("/etc/passwd", d), {"name": None, "usbid": None, "readable": False})
        d = asound("", {2: ("Mini", "12zz:0036")})                                              # a usbid file that is not one
        self.assertIsNone(midi.card_info("/dev/snd/midiC2D0", d)["usbid"])


class MapperTest(unittest.TestCase):
    """Precedence, pickup, the double press and the controllers' bank, on a fake clock."""

    def setUp(self):
        self.rec, self.t, self.have = Recorder(), [100.0], {}

    def mapper(self, profile, source, *mine, standard=True):
        m = MidiMapper(self.rec, list(mine) + (midi.profile_entries(BY_ID[profile], source) if standard else []) + midi.builtin_map(),
                       {"blackout": False}, clock=lambda: self.t[0])
        m.profiled = {source} if standard else set()
        m.target = lambda action: self.have.get(action)
        return m

    def cc(self, m, source, number, value, step=1.0):
        self.t[0] += step
        m.message(source, ("cc", 0, number, value))

    def test_the_standard_layout_applies_and_the_built_in_map_does_not_show_through(self):
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 16, 127)                    # knob 1
        self.cc(m, "nanoKONTROL2", 20, 64)                     # knob 5: CC 20 is "opacity" in the built-in map
        self.cc(m, "nanoKONTROL2", 22, 10)                     # knob 7: CC 22 is "position" there
        self.cc(m, "nanoKONTROL2", 6, 127)                     # fader 7 is spare: nothing, not even the built-in map
        self.cc(m, "nanoKONTROL2", 99, 127)                    # not a control of this controller
        self.assertEqual(self.rec.calls, [("/api/shaders/values", {"control": 1, "level": 127}), ("/api/shaders/values", {"control": 5, "level": 64}),
                                          ("/api/shaders/values", {"control": 7, "level": 10})])
        mix = self.mapper(MIX, "Mix")
        self.cc(mix, "Mix", 25, 127)                           # knob B3 is CC 25: "blackout while held up" in the built-in map
        self.assertEqual(self.rec.calls[-1], ("/api/vibes", {"dwell": 3600}))
        self.assertFalse(any(p == "/api/blackout" for p, _ in self.rec.calls))

    def test_another_controller_still_gets_the_built_in_map(self):
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "keys", 20, 127)
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "opacity", "value": 100.0})])

    def test_a_mapping_the_person_made_wins_for_that_control_only(self):
        own = midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 16, "action": "size"})
        m = self.mapper(NANO, "nanoKONTROL2", own)
        self.cc(m, "nanoKONTROL2", 16, 127)
        self.cc(m, "nanoKONTROL2", 17, 127)
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "size", "value": 200.0}), ("/api/shaders/values", {"control": 2, "level": 127})])
        every = midi.validate_entry({"source": "*", "kind": "cc", "number": 17, "action": "volume"})     # for any controller: it wins over the layout
        m = self.mapper(NANO, "nanoKONTROL2", every)
        self.cc(m, "nanoKONTROL2", 17, 127)
        self.assertEqual(self.rec.calls[-1], ("/api/control", {"action": "volume", "value": 100.0}))
        m = self.mapper(NANO, "nanoKONTROL2", every, midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 17, "action": "size"}))
        self.rec.calls.clear()
        self.cc(m, "nanoKONTROL2", 17, 0)                      # and the controller's own mapping comes before that one, alone
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "size", "value": 1.0})])
        self.rec.calls.clear()
        self.cc(m, "nanoKONTROL2", 16, 127)
        self.cc(m, "nanoKONTROL2", 17, 127)
        nothing = midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 42, "action": "none"})
        m = self.mapper(NANO, "nanoKONTROL2", nothing)
        self.cc(m, "nanoKONTROL2", 42, 127)                    # Stop, switched off by the person
        self.assertEqual(len(self.rec.calls), 2)

    def test_standard_layout_off_leaves_the_persons_mappings_and_the_built_in_map(self):
        own = midi.validate_entry({"source": "nanoKONTROL2", "kind": "cc", "number": 16, "action": "size"})
        m = self.mapper(NANO, "nanoKONTROL2", own, standard=False)
        self.cc(m, "nanoKONTROL2", 16, 127)
        self.cc(m, "nanoKONTROL2", 17, 127)                    # no standard layout: nothing
        self.cc(m, "nanoKONTROL2", 20, 127)                    # the built-in map again
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "size", "value": 200.0}), ("/api/control", {"action": "opacity", "value": 100.0})])

    def test_pickup_a_fader_left_down_does_not_black_the_screen(self):
        self.have["opacity"] = 100.0
        m = self.mapper(NANO, "nanoKONTROL2")
        for v in (0, 1, 30, 90, 115):                           # fader 1 from the bottom: nothing until it reaches what the box has
            self.cc(m, "nanoKONTROL2", 0, v)
            self.assertEqual(self.rec.calls, [], v)
            self.assertTrue(m.waiting("nanoKONTROL2", "cc", 0))
        self.cc(m, "nanoKONTROL2", 0, 124)                      # within reach of 100 percent: caught
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "opacity", "value": round(124 / 127 * 100, 2)})])
        self.assertFalse(m.waiting("nanoKONTROL2", "cc", 0))
        self.have["opacity"] = round(124 / 127 * 100, 2)
        self.cc(m, "nanoKONTROL2", 0, 60)                       # and now it is followed
        self.assertEqual(self.rec.calls[-1], ("/api/control", {"action": "opacity", "value": round(60 / 127 * 100, 2)}))

    def test_pickup_catches_when_the_control_passes_the_value(self):
        self.have["opacity"] = 50.0                             # 63.5 of 127
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 0, 10)
        self.cc(m, "nanoKONTROL2", 0, 100)                      # a quick move straight past it
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "opacity", "value": round(100 / 127 * 100, 2)})])

    def test_pickup_is_lost_when_the_panel_moved_the_value_meanwhile(self):
        self.have["opacity"] = 100.0
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 0, 127)
        self.assertEqual(len(self.rec.calls), 1)
        self.have["opacity"] = 20.0                             # someone used the panel's slider
        self.cc(m, "nanoKONTROL2", 0, 126, step=5)              # the fader is touched again, still at the top
        self.assertEqual(len(self.rec.calls), 1)
        self.assertTrue(m.waiting("nanoKONTROL2", "cc", 0))
        m.forget("nanoKONTROL2")                                # unplugged: it starts clean
        self.assertFalse(m.waiting("nanoKONTROL2", "cc", 0))

    def test_pickup_a_fader_that_does_not_reach_its_ends_still_picks_up(self):
        self.have["opacity"] = 100.0
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 0, 118)                      # not the top
        self.assertEqual(self.rec.calls, [])
        self.cc(m, "nanoKONTROL2", 0, 121)                      # a worn fader's top: counts as the top
        self.assertEqual(len(self.rec.calls), 1)
        self.have["opacity"] = 0.0
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 0, 30)
        self.assertEqual(len(self.rec.calls), 1)
        self.cc(m, "nanoKONTROL2", 0, 7)                        # and its bottom
        self.assertEqual(len(self.rec.calls), 2)
        self.have["opacity"] = 50.0
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 0, 122)                      # the ends are no shortcut to a value in the middle
        self.assertEqual(len(self.rec.calls), 2)

    def test_which_levels_pick_up_and_which_may_jump(self):
        by_action = {e["action"]: e["pickup"] for p in PROFILES for e in midi.profile_entries(p, "x")}
        self.assertEqual({a for a, on in by_action.items() if on}, {"opacity", "volume", "speed", "shader_speed", "shader_brightness"})
        self.have.update(shader_hue=0.0)
        m = self.mapper(NANO, "nanoKONTROL2")
        self.cc(m, "nanoKONTROL2", 4, 0)                        # the hue fader jumps: a colour turn hides nothing
        self.cc(m, "nanoKONTROL2", 16, 0)                       # and so does a shader control
        self.assertEqual(self.rec.calls, [("/api/shaders/values", {"controls": {"hue": -180.0}}), ("/api/shaders/values", {"control": 1, "level": 0})])
        self.rec.calls.clear()
        self.cc(m, "nanoKONTROL2", 1, 0)                        # volume: the box's value is not known to this bare mapper, so it is followed
        self.assertEqual(len(self.rec.calls), 1)

    def press(self, m, source, kind, number, after):
        self.t[0] += after
        m.message(source, ("cc", 0, number, 127) if kind == "cc" else ("on", 0, number, 127))
        self.t[0] += 0.05
        m.message(source, ("cc", 0, number, 0) if kind == "cc" else ("off", 0, number, 0))

    def test_blackout_needs_the_same_press_twice_within_a_second(self):
        m = self.mapper(NANO, "nanoKONTROL2")
        self.press(m, "nanoKONTROL2", "cc", 71, 5)             # R 8 once: nothing
        self.assertEqual(self.rec.calls, [])
        self.press(m, "nanoKONTROL2", "cc", 71, 0.4)           # and again: blackout
        self.assertEqual(self.rec.calls, [("/api/blackout", {"on": True})])
        self.press(m, "nanoKONTROL2", "cc", 71, 0.4)           # a third press is a first press again
        self.assertEqual(len(self.rec.calls), 1)
        self.press(m, "nanoKONTROL2", "cc", 71, 1.5)           # too late for a second: it arms again
        self.assertEqual(len(self.rec.calls), 1)
        self.press(m, "nanoKONTROL2", "cc", 71, 0.9)           # 0.95 s after the last press: in time
        self.assertEqual(len(self.rec.calls), 2)

    def test_the_edges_of_the_double_press(self):
        m = self.mapper(PAD, "Mini")
        m.message("Mini", ("on", 0, 120, 127))                 # H
        for gap in (0.02, 0.02, 0.02):                         # contact bounce is not a second press
            self.t[0] += gap
            m.message("Mini", ("off", 0, 120, 0))
            self.t[0] += gap
            m.message("Mini", ("on", 0, 120, 127))
        self.assertEqual(self.rec.calls, [])
        m.message("Mini", ("off", 0, 120, 0))
        self.t[0] += midi.GUARD_MIN
        m.message("Mini", ("on", 0, 120, 127))
        self.assertEqual(self.rec.calls, [("/api/blackout", {"on": True})])
        m = self.mapper(PAD, "Mini")
        self.rec.calls.clear()
        self.press(m, "Mini", "note", 8, 5)                    # A: Room scene 1
        self.press(m, "Mini", "note", 8, midi.GUARD_MAX + 0.01)
        self.assertEqual(self.rec.calls, [])
        self.press(m, "Mini", "note", 24, 0.3)                 # B is another button: it does not complete A's press
        self.assertEqual(self.rec.calls, [])
        self.press(m, "Mini", "note", 8, 0.3)
        self.assertEqual(self.rec.calls, [("/api/room/scene", {"number": 1})])

    def test_an_unguarded_button_fires_at_once(self):
        m = self.mapper(PAD, "Mini")
        self.press(m, "Mini", "note", 104, 1)                  # G: Stop
        self.press(m, "Mini", "note", 20, 1)                   # pad 2.5 is bank A, pad 11
        self.press(m, "Mini", "cc", 106, 1)                    # top 3: Vibes
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "stop"}), ("/api/play", {"pad": [0, 10]}), ("/api/vibes", {"on": True})])

    def test_the_controllers_bank(self):
        m = self.mapper(NANO, "nanoKONTROL2")
        self.press(m, "nanoKONTROL2", "cc", 32, 1)             # S 1: pad 1 of bank A
        self.press(m, "nanoKONTROL2", "cc", 62, 1)             # Marker >: bank B
        self.press(m, "nanoKONTROL2", "cc", 39, 1)             # S 8
        self.press(m, "nanoKONTROL2", "cc", 61, 1)
        self.press(m, "nanoKONTROL2", "cc", 61, 1)             # Marker < twice: round to bank C
        self.press(m, "nanoKONTROL2", "cc", 32, 1)
        self.assertEqual(self.rec.calls, [("/api/play", {"pad": [0, 0]}), ("/api/play", {"pad": [1, 7]}), ("/api/play", {"pad": [2, 0]})])
        self.assertEqual(m.bank, 2)

    def test_the_new_actions_make_calls_the_api_already_had(self):
        m = self.mapper(NANO, "nanoKONTROL2")
        for number in (43, 44, 69, 70, 58, 59, 46, 41, 48, 60, 45):    # rewind, forward, R 6, R 7, track < >, cycle, play, M 1, marker set, rec
            self.press(m, "nanoKONTROL2", "cc", number, 1)
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "prev"}), ("/api/control", {"action": "next"}), ("/api/fadein", {"seconds": 2}),
                                          ("/api/fadeout", {"seconds": 2}), ("/api/shaders/step", {"dir": -1}), ("/api/shaders/step", {"dir": 1}),
                                          ("/api/vibes", {"on": True}), ("/api/control", {"action": "pause"}), ("/api/shaders/preset", {"index": 1}),
                                          ("/api/vibes", {"on": True, "set": "Ambient"}), ("/api/vibes", {"on": True, "set": "Show"})])


class HubBase(ServerBase):
    NAMES = {"/dev/snd/midiC1D0": "nanoKONTROL2", "/dev/snd/midiC2D0": "Mix", "/dev/snd/midiC3D0": "Mini", "/dev/snd/midiC4D0": "keys",
             "/dev/snd/midiC5D0": "Mini_1"}
    PRODUCTS = {"/dev/snd/midiC1D0": "nanoKONTROL2", "/dev/snd/midiC2D0": "MIDI Mix", "/dev/snd/midiC3D0": "Launchpad Mini", "/dev/snd/midiC4D0": "Keystation",
                "/dev/snd/midiC5D0": "Launchkey Mini"}

    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.call("POST", "/api/modules/control-midi", {"enabled": True}, token=self.full)
        self.pipes, self.present, self.said = {}, [], []

        def open_fn(path):
            r, w = os.pipe()
            self.pipes[path] = (r, w)
            return r
        self.hub = MidiHub(self.api, self.settings, log=self.said.append, open_fn=open_fn, lister=lambda: list(self.present),
                           namer=lambda p: self.NAMES[p], scan_interval=0.05, describer=lambda p: self.PRODUCTS[p])
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)

    def enable(self):
        self.settings.data["control"]["midi"]["enabled"] = True
        self.hub.apply()

    def send(self, path, data):
        os.write(self.pipes[path][1], bytes(data))

    def wait(self, cond, timeout=4):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.02)
        self.assertTrue(cond(), "condition not met")

    def controller(self, name):
        return next(c for c in self.hub.status()["controllers"] if c["name"] == name)

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)


class HubTest(HubBase):
    def test_a_known_controller_works_when_it_is_plugged_in_and_its_layout_goes_when_it_is_unplugged(self):
        self.enable()
        self.assertEqual(self.hub.status()["controllers"], [])
        self.present = ["/dev/snd/midiC1D0"]                              # plugged in: nothing is taught
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.wait(lambda: self.controller("nanoKONTROL2")["connected"])
        c = self.controller("nanoKONTROL2")
        self.assertEqual((c["profile"]["id"], c["profile"]["name"], c["standard"], len(c["controls"]), c["profile"]["rows"], c["profile"]["cols"]),
                         (NANO, "Korg nanoKONTROL2", True, 51, 5, 13))
        self.assertTrue(any("nanoKONTROL2 is a Korg nanoKONTROL2: its standard layout is on" in line for line in self.said))
        self.send("/dev/snd/midiC1D0", [0xB0, 42, 127])                   # Stop
        self.wait(lambda: self.player.calls)
        stop = next(x for x in self.controller("nanoKONTROL2")["controls"] if x["id"] == "stop")
        self.assertEqual((stop["action"], stop["origin"], stop["value"], stop["unverified"]), ({"action": "stop"}, "standard", 127, True))
        self.assertLess(stop["ago"], 4)
        first = self.pipes.pop("/dev/snd/midiC1D0")
        self.present = []
        os.close(first[1])                                                  # unplugged
        self.wait(lambda: not self.hub.inputs and not self.hub._matched)       # the layout goes once the reader has ended
        self.assertEqual((self.hub.status()["controllers"], self.hub._matched, self.hub.activity), ([], {}, {}))
        handled = []
        self.api.handle = lambda *a, **k: handled.append(a) or (200, {})
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 20, 0))              # a late message from it (knob 5) is dropped: the built-in map
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 0, 0))               # would read CC 20 as opacity and black the screen out
        self.assertEqual((self.api.mix["opacity"], handled, self.hub.activity), (100, [], {}))

    def test_several_controllers_each_use_their_own_layout_and_an_unknown_one_has_none(self):
        self.present = sorted(self.NAMES)
        self.enable()
        self.wait(lambda: len(self.pipes) == 5)
        got = {c["name"]: (c["profile"] or {}).get("id") for c in self.hub.status()["controllers"]}
        self.assertEqual(got, {"nanoKONTROL2": NANO, "Mix": MIX, "Mini": PAD, "keys": None, "Mini_1": None})   # a Launchkey Mini is not a Launchpad Mini
        self.assertEqual(next(c for c in self.hub.status()["controllers"] if c["name"] == "keys")["controls"], [])
        launchkey = {"set": {"controller": "Mini_1", "control": "side_g", "action": {"action": "none"}}}
        self.assertEqual(self.post("/api/midi/map", launchkey)[0], 404)       # its card id fits, its product name does not: no layout to change
        self.assertEqual(self.post("/api/midi/map", {"reset": {"controller": "Mini_1"}})[0], 404)
        self.assertEqual(self.post("/api/midi/map", {"reset": {"controller": "Mix_1"}})[0], 404)      # not plugged in
        self.send("/dev/snd/midiC3D0", [0x90, 104, 127])                  # the Launchpad's G: Stop
        self.wait(lambda: len(self.player.calls) >= 1)
        before = len(self.player.calls)
        self.send("/dev/snd/midiC2D0", [0x90, 104, 127])                  # the MIDI Mix has no note 104: nothing
        self.send("/dev/snd/midiC4D0", [0x90, 74, 127])                   # the unknown one keeps the built-in map: blackout
        self.wait(lambda: self.api.mix["blackout"])
        self.send("/dev/snd/midiC1D0", [0xB0, 25, 127])                   # CC 25 on the nanoKONTROL2 is no control: not "blackout while up"
        self.send("/dev/snd/midiC4D0", [0x90, 74, 0, 0x90, 72, 127])      # and Stop from the unknown one
        self.wait(lambda: len(self.player.calls) > before)
        self.assertTrue(self.api.mix["blackout"])

    def test_the_switch_per_controller_turns_the_layout_off_without_unplugging(self):
        self.present = ["/dev/snd/midiC1D0", "/dev/snd/midiC3D0"]
        self.enable()
        self.wait(lambda: len(self.pipes) == 2)
        st, body, _ = self.post("/api/midi", {"controller": "Mini", "standard": False})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.settings.data["control"]["midi"]["controllers"], {"Mini": {"standard": False}})
        self.assertEqual([(c["name"], c["standard"]) for c in body["controllers"]], [("nanoKONTROL2", True), ("Mini", False)])
        self.assertTrue(all(x["action"] is None for x in self.controller("Mini")["controls"]))
        self.assertEqual(next(x for x in self.controller("Mini")["controls"] if x["id"] == "side_g")["standard"], {"action": "stop"})
        reached, real = [], self.api.handle

        def handle(method, path, body, *rest):
            reached.append((path, body))
            return real(method, path, body, *rest)
        self.api.handle = handle
        self.send("/dev/snd/midiC3D0", [0x90, 104, 127])                  # G does nothing now
        self.send("/dev/snd/midiC3D0", [0x90, 74, 127])                   # and the built-in map is back for it: blackout
        self.wait(lambda: self.api.mix["blackout"])
        self.assertEqual(reached, [("/api/blackout", {"on": True})])       # no Stop was sent for G
        self.api.handle = real
        st, body, _ = self.post("/api/midi", {"controller": "Mini", "standard": True})
        self.assertEqual(self.settings.data["control"]["midi"]["controllers"], {})        # on is the default: nothing is kept
        for bad in ({"controller": "Mini"}, {"standard": True}, {"controller": "a/b", "standard": True}, {"controller": "Mini", "standard": 1}):
            self.assertEqual(self.post("/api/midi", bad)[0], 400, bad)

    def test_a_control_can_be_changed_and_put_back(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "r5", "action": {"action": "pad", "bank": 1, "index": 2}}})
        self.assertEqual(st, 200, body)
        rec = next(x for x in body["controllers"][0]["controls"] if x["id"] == "r5")
        self.assertEqual((rec["action"], rec["origin"], rec["standard"]), ({"action": "pad", "bank": 1, "index": 2}, "yours", None))
        self.assertEqual([(e["source"], e["kind"], e["number"]) for e in body["map"]], [("nanoKONTROL2", "cc", 68)])
        self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "none"}}})
        self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "fader7", "action": {"action": "size"}}})
        self.post("/api/midi/map", {"add": {"source": "*", "kind": "cc", "number": 99, "action": "stop"}})
        stop = next(x for x in self.controller("nanoKONTROL2")["controls"] if x["id"] == "stop")
        self.assertEqual((stop["action"], stop["origin"], stop["standard"]), ({"action": "none"}, "yours", {"action": "stop"}))
        st, body, _ = self.post("/api/midi/map", {"reset": {"controller": "nanoKONTROL2", "control": "stop"}})
        stop = next(x for x in body["controllers"][0]["controls"] if x["id"] == "stop")
        self.assertEqual((stop["action"], stop["origin"], len(body["map"])), ({"action": "stop"}, "standard", 3))
        st, body, _ = self.post("/api/midi/map", {"reset": {"controller": "nanoKONTROL2"}})          # the whole controller
        self.assertEqual([(e["source"], e["number"]) for e in body["map"]], [("*", 99)])               # other mappings are kept
        self.assertTrue(all(x["origin"] != "yours" for x in body["controllers"][0]["controls"]))
        # a mapping for any controller wins on this one too, is shown as such, and no reset here removes it; neither
        # does a reset remove this controller's mapping on a number that is not in its layout
        self.post("/api/midi/map", {"add": {"source": "*", "kind": "cc", "number": 42, "action": "pause"}})
        self.post("/api/midi/map", {"add": {"source": "nanoKONTROL2", "kind": "cc", "number": 100, "action": "stop"}})
        stop = next(x for x in self.controller("nanoKONTROL2")["controls"] if x["id"] == "stop")
        self.assertEqual((stop["action"], stop["origin"]), ({"action": "pause"}, "any"))
        self.post("/api/midi/map", {"reset": {"controller": "nanoKONTROL2", "control": "stop"}})
        st, body, _ = self.post("/api/midi/map", {"reset": {"controller": "nanoKONTROL2"}})
        self.assertEqual(sorted((e["source"], e["number"]) for e in body["map"]), [("*", 42), ("*", 99), ("nanoKONTROL2", 100)])
        self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "fadeout"}}})
        stop = next(x for x in self.controller("nanoKONTROL2")["controls"] if x["id"] == "stop")
        self.assertEqual((stop["action"], stop["origin"]), ({"action": "fadeout"}, "yours"))      # its own mapping comes before the one for any
        for body, want in (({"set": {"controller": "nanoKONTROL2", "control": "nope", "action": {"action": "stop"}}}, 400),
                           ({"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "shutdown"}}}, 400),
                           ({"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "opacity"}}}, 400),
                           ({"set": {"controller": "nanoKONTROL2", "control": "fader1", "action": {"action": "stop"}}}, 400),
                           ({"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "stop", "number": 3}}}, 400),
                           ({"set": {"controller": "keys", "control": "stop", "action": {"action": "stop"}}}, 404),
                           ({"set": {"controller": "a/b", "control": "stop", "action": {"action": "stop"}}}, 400), ({"set": "x"}, 400),
                           ({"reset": {"controller": "nanoKONTROL2", "control": "nope"}}, 400), ({"reset": {"controller": "keys"}}, 404)):
            self.assertEqual(self.post("/api/midi/map", body)[0], want, body)

    def test_roles_a_presenter_sees_and_only_full_access_changes(self):
        self.enable()
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        view = self.post("/api/devices/invite", {"name": "v", "role": "view"})[1]["token"]
        self.assertEqual(self.call("GET", "/api/midi", token=live)[0], 200)
        self.assertEqual(self.call("GET", "/api/midi", token=view)[0], 403)
        self.assertEqual(self.call("GET", "/api/midi")[0], 401)
        for path, body in (("/api/midi", {"controller": "Mini", "standard": False}),
                           ("/api/midi/map", {"set": {"controller": "Mini", "control": "side_g", "action": {"action": "none"}}}),
                           ("/api/midi/map", {"reset": {"controller": "Mini"}})):
            self.assertEqual(self.post(path, body, token=live)[0], 403, path)
            self.assertEqual(self.post(path, body, token=view)[0], 403, path)

    def test_nothing_is_acted_on_once_midi_is_switched_off(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        handled = []
        real = self.api.handle

        def handle(method, path, *rest):
            handled.append(path)
            return real(method, path, *rest)
        self.api.handle = handle
        order = []
        reader = self.hub.inputs["/dev/snd/midiC1D0"]
        stop = reader.stop
        reader.stop = lambda: (order.append(dict(self.hub._matched)), stop())[1]    # what the hub still knows when it joins the reader
        self.settings.data["control"]["midi"]["enabled"] = False
        self.hub.apply()
        self.assertEqual([sorted(m) for m in order], [["/dev/snd/midiC1D0"]])          # the layout was still there: it goes after the reader
        self.assertEqual(self.hub._matched, {})
        for msg in (("cc", 0, 20, 0), ("cc", 0, 0, 0), ("cc", 0, 42, 127), ("on", 0, 74, 127), None):
            self.hub.on_message("nanoKONTROL2", msg)                                   # still on its way from the reader
        self.assertEqual((handled, self.api.mix["opacity"], self.api.mix["blackout"]), ([], 100, False))
        seen = []
        halted = midi.MidiInput("/dev/snd/midiC1D0", "nanoKONTROL2", lambda source, msg: seen.append(msg), open_fn=lambda path: os.pipe()[0])
        halted.halt()                                                                  # a reader told to stop hands nothing on
        halted._run()
        self.assertEqual(seen, [])

    def test_a_scan_does_not_hold_the_lock_while_a_reader_ends(self):
        self.present = ["/dev/snd/midiC1D0", "/dev/snd/midiC4D0"]
        self.enable()
        self.wait(lambda: len(self.pipes) == 2)
        slow = self.hub.inputs["/dev/snd/midiC4D0"]
        held, real = [], slow.stop

        def stop():
            held.append(self.hub._lock._is_owned())
            t0 = time.monotonic()
            got = []
            t = threading.Thread(target=lambda: got.append(self.hub.status()))        # the page asks meanwhile
            t.start()
            t.join(2)
            held.append((bool(got), time.monotonic() - t0 < 1.5))
            real()
        slow.stop = stop
        self.present = ["/dev/snd/midiC1D0"]
        self.wait(lambda: len(held) == 2)
        self.assertEqual(held, [False, (True, True)])
        self.wait(lambda: [c["name"] for c in self.hub.status()["controllers"]] == ["nanoKONTROL2"])

    def test_saving_a_guarded_control_unchanged_keeps_the_guard(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)

        def r(cid="r8"):
            return next(x for x in self.controller("nanoKONTROL2")["controls"] if x["id"] == cid)
        for cid, action in (("r8", {"action": "blackout"}), ("r1", {"action": "scene_1"}), ("r8", {"action": "blackout", "guard": True})):
            st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": cid, "action": action}})
            self.assertEqual((st, body["map"]), (200, []), cid)                          # Save without a change stores nothing
            self.assertEqual((r(cid)["origin"], r(cid)["guard"]), ("standard", True))
        clock = [50.0]
        self.hub._clock = self.hub.mapper._clock = lambda: clock[0]
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 71, 127))                           # one press of R 8 still does nothing
        self.assertFalse(self.api.mix["blackout"])
        # blackout put on another button is guarded too, unless the person switches that off themselves
        st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "r5", "action": {"action": "blackout"}}})
        self.assertEqual([(e["number"], e["action"], e["guard"]) for e in body["map"]], [(68, "blackout", True)])
        self.assertEqual((r("r5")["origin"], r("r5")["guard"]), ("yours", True))
        clock[0] += 5
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 68, 127))
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 68, 0))
        self.assertFalse(self.api.mix["blackout"])
        clock[0] += 0.5
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 68, 127))
        self.assertTrue(self.api.mix["blackout"])
        st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "r8", "action": {"action": "blackout", "guard": False}}})
        self.assertEqual(sorted((e["number"], e["guard"]) for e in body["map"]), [(68, True), (71, False)])       # a deliberate choice, stored as one
        self.assertEqual((r()["origin"], r()["guard"]), ("yours", False))
        clock[0] += 5
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 71, 0))
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 71, 127))
        self.assertFalse(self.api.mix["blackout"])                                          # one press: it toggled the blackout off
        st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "r8", "action": {"action": "blackout"}}})
        self.assertEqual([e["number"] for e in body["map"]], [68])                          # back to the standard: the own entry is gone
        for bad in ({"action": "stop", "guard": True}, {"action": "blackout", "guard": 1}):
            self.assertEqual(self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "r5", "action": bad}})[0], 400, bad)
        self.assertEqual(self.post("/api/midi/map", {"add": {"kind": "cc", "number": 3, "action": {"x": 1}}})[0], 400)    # not a 500

    def test_one_control_never_has_two_own_mappings_that_fire(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.post("/api/midi/map", {"add": {"source": "nanoKONTROL2", "kind": "cc", "channel": 1, "number": 42, "action": "pause"}})    # learned, channel 1
        st, body, _ = self.post("/api/midi/map", {"set": {"controller": "nanoKONTROL2", "control": "stop", "action": {"action": "fadeout"}}})
        self.assertEqual([(e["channel"], e["action"]) for e in body["map"]], [(0, "fadeout")])       # the card's choice replaced it
        both = [midi.validate_entry({"source": "nano", "kind": "cc", "channel": c, "number": 5, "action": a}) for c, a in ((0, "stop"), (1, "pause"))]
        rec = Recorder()
        m = MidiMapper(rec, both, {}, clock=lambda: 1.0)                                            # a hand-edited file may still hold both
        m.message("nano", ("cc", 0, 5, 127))
        self.assertEqual(rec.calls, [("/api/control", {"action": "pause"})])                         # the channel's own one runs, alone
        m.message("nano", ("cc", 3, 5, 127))
        self.assertEqual(rec.calls[-1], ("/api/control", {"action": "stop"}))

    def test_pickup_for_volume_and_speed_uses_what_the_panel_last_set(self):
        self.hub._matched["/dev/snd/midiC1D0"] = ("nanoKONTROL2", BY_ID[NANO])
        self.assertEqual((self.hub._target("volume"), self.hub._target("speed")), (100.0, 1.0))
        self.assertEqual(self.post("/api/control", {"action": "volume", "value": 20})[0], 200)       # someone turns it down in the panel
        self.assertEqual(self.hub._target("volume"), 20.0)
        clock = [10.0]
        self.hub._clock = self.hub.mapper._clock = lambda: clock[0]
        before = len(self.player.calls)
        for v in (127, 126, 90, 40):                                                                   # fader 2 was at the top: no jump to 100
            clock[0] += 1
            self.hub.on_message("nanoKONTROL2", ("cc", 0, 1, v))
            self.assertEqual((len(self.player.calls), self.api.levels["volume"]), (before, 20.0), v)
        clock[0] += 1
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 1, 20))                                         # it passed 20 percent (25 of 127): caught
        self.assertEqual(self.api.levels["volume"], round(20 / 127 * 100, 2))
        self.post("/api/control", {"action": "speed", "value": 2})
        self.assertEqual(self.hub._target("speed"), 2.0)
        self.post("/api/control", {"action": "reset"})
        self.assertEqual(self.hub._target("speed"), 1.0)
        self.post("/api/control", {"action": "volume_step", "value": 10})
        self.assertEqual(self.hub._target("volume"), round(20 / 127 * 100, 2) + 10)

    def test_the_switch_list_cannot_be_locked_or_filled(self):
        self.present = ["/dev/snd/midiC3D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC3D0" in self.pipes)
        def c():
            return self.settings.data["control"]["midi"]
        for junk in ("x", [], {"Mini": "x"}, {"a/b": {"standard": False}}, {"Mini": {"standard": False, "more": 1}}):
            c()["controllers"] = junk                                                                   # a hand-edited file
            st, body, _ = self.post("/api/midi", {"controller": "Mini", "standard": False})
            self.assertEqual((st, c()["controllers"]), (200, {"Mini": {"standard": False}}), junk)
        for name in ("ghost", "Mix", "nanoKONTROL2"):                                                   # well formed, never plugged in
            self.assertEqual(self.post("/api/midi", {"controller": name, "standard": False})[0], 400, name)
        self.assertEqual(c()["controllers"], {"Mini": {"standard": False}})
        c()["controllers"]["Mix"] = {"standard": False}                                                   # stored earlier, unplugged now: still its own
        self.assertEqual(self.post("/api/midi", {"controller": "Mix", "standard": True})[0], 200)
        self.assertEqual(c()["controllers"], {"Mini": {"standard": False}})
        first = self.pipes.pop("/dev/snd/midiC3D0")
        self.present = []
        os.close(first[1])
        self.wait(lambda: not self.hub.inputs)
        self.assertEqual(self.post("/api/midi", {"controller": "Mini", "standard": True})[0], 200)      # seen since the start: it can still be switched
        self.assertEqual(c()["controllers"], {})
        full = {"old%d" % i: {"standard": False} for i in range(32)}
        self.assertEqual(midi.validate({"controller": "Mini", "standard": False}, {"controllers": dict(full)}, {"Mini"})["controllers"],
                         dict({"old%d" % i: {"standard": False} for i in range(1, 32)}, Mini={"standard": False}))      # full: one never seen goes

    def test_a_layout_cannot_send_more_than_fifty_commands_a_second(self):
        self.hub._matched["/dev/snd/midiC3D0"] = ("Mini", BY_ID[PAD])
        self.hub._clock = lambda: 5.0
        self.hub.calls = midi.RateLimiter(lambda: 5.0, rate=midi.MAX_CALLS_PER_SECOND, burst=midi.MAX_CALLS_PER_SECOND)
        self.settings.data["control"]["midi"]["enabled"] = True
        self.api.registry.set_enabled("shaders", True)
        reached, real = [], self.api.handle

        def handle(method, path, *rest):
            reached.append(path)
            return real(method, path, *rest)
        self.api.handle = handle
        for n in range(64):                                                 # every pad of the grid, four times over, in no time
            for _ in range(4):
                self.hub.on_message("Mini", ("on", 0, 16 * (n // 8) + n % 8, 127))
                self.hub.on_message("Mini", ("off", 0, 16 * (n // 8) + n % 8, 0))
        self.assertEqual(len(reached), midi.MAX_CALLS_PER_SECOND)           # 52 of the 64 pads have an action: two were dropped

    def test_everything_goes_through_the_api_as_a_presenter(self):
        seen = []
        real = self.api.handle

        def handle(method, path, body, device, client):
            seen.append((method, path, device["role"], client, self.hub._lock._is_owned()))
            return real(method, path, body, device, client)
        self.api.handle = handle
        self.hub._matched["/dev/snd/midiC1D0"] = ("nanoKONTROL2", BY_ID[NANO])
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 42, 127))
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 4, 64))
        self.assertEqual(seen, [("POST", "/api/control", "live", "midi", False)])     # and the shader fader did nothing: its module is off

    def test_settings_go_round_through_export_and_import(self):
        d = self.settings.data["control"]
        d["midi"].update(enabled=True, controllers={"Mini": {"standard": False}},
                         map=[midi.validate_entry(midi.override_entry(BY_ID[NANO], "nanoKONTROL2", "s1", {"action": "bank_pad", "index": 11})),
                              midi.validate_entry(midi.override_entry(BY_ID[NANO], "nanoKONTROL2", "r5", {"action": "scene_8"})),
                              midi.validate_entry(midi.override_entry(BY_ID[NANO], "nanoKONTROL2", "fader8", {"action": "shader_hue"}))])
        want = copy.deepcopy(d)
        self.assertEqual(boxcare.check_control(copy.deepcopy(d), None), want)
        plain = copy.deepcopy(d)
        del plain["midi"]["controllers"]                                    # a file from before this version: all layouts on, no key made up
        self.assertNotIn("controllers", boxcare.check_control(plain, None)["midi"])
        for bad in ({"Mini": {"standard": 1}}, {"a/b": {"standard": False}}, {"Mini": {"standard": False, "x": 1}}, {"Mini": {}}, [], "x",
                    {"c%d" % i: {"standard": False} for i in range(33)}):
            broken = copy.deepcopy(d)
            broken["midi"]["controllers"] = bad
            with self.assertRaises((ValueError, midi.MidiError), msg=str(bad)[:60]):
                boxcare.check_control(broken, None)
        self.hub._matched["/dev/snd/midiC3D0"] = ("Mini", BY_ID[PAD])
        self.assertFalse(self.hub.standard_on("Mini"))
        self.assertTrue(self.hub.standard_on("nanoKONTROL2"))
        for junk in ("x", [], {"Mini": "x"}, {"Mini": {"standard": "no"}}):  # a hand-edited file cannot break the reader: read as "on"
            d["midi"]["controllers"] = junk
            self.assertTrue(self.hub.standard_on("Mini"), junk)
            self.hub.on_message("Mini", ("on", 0, 104, 127))


class NoBlockingTest(Live):
    """No control of any shipped layout makes the thread that reads the controller wait for the shader engine's
    lock (the GPU's look at a shader can take four seconds), and a status request for the page does not either."""

    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        self.api.registry.set_enabled("control-midi", True)
        self.settings.data["control"]["midi"]["enabled"] = True
        self.clock = [900.0]
        self.hub = MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], clock=lambda: self.clock[0], describer=lambda p: None)
        self.hub.calls = midi.RateLimiter(lambda: self.clock[0], rate=1e9, burst=1e9)       # the cap is not what is tested here
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)
        self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None, rng=random.Random(1), thread=True, log=lambda *_: None)
        self.addCleanup(self.api.vibes.stop)
        for name in ("volume", "speed", "pause", "clear", "playlist_step", "status"):      # what the fake player of these tests lacks
            if not hasattr(self.player, name):
                setattr(self.player, name, lambda *a, **k: True)

    def test_every_control_of_every_layout_answers_while_the_gpu_is_busy(self):
        paths, took, errors, answers = set(), [], [], {}
        real = self.hub._do

        def do(path, body):
            paths.add(path)
            return real(path, body)
        self.hub._do = self.hub.mapper.do = do
        handle = self.api.handle

        def answered(method, path, body, device, client):
            status, payload = handle(method, path, body, device, client)
            answers.setdefault((path, status), body)
            return status, payload
        self.api.handle = answered
        for n, p in enumerate(PROFILES):
            self.hub._matched["/dev/snd/midiC%dD0" % n] = (p["id"], p)       # the controller's name does not matter here

        def run():
            for p in PROFILES:
                for c in p["controls"]:
                    number = c["send"]["number"]
                    if c["send"]["type"] == "cc" and c["kind"] in ("fader", "knob"):
                        msgs = [("cc", 0, number, v) for v in (0, 127, 64, 0)]
                    elif c["send"]["type"] == "cc":
                        msgs = [("cc", 0, number, 127), ("cc", 0, number, 0)] * 2
                    else:
                        msgs = [("on", 0, number, 127), ("off", 0, number, 0)] * 2      # twice: a guarded button fires on the second
                    for msg in msgs + [None]:
                        self.clock[0] += 0.3
                        t0 = time.monotonic()
                        try:
                            self.hub.on_message(p["id"], msg)
                        except Exception as e:                     # the fake player, not the hub: say which control
                            errors.append((p["id"], c["id"], repr(e)))
                        took.append((time.monotonic() - t0, p["id"], c["id"]))
            t0 = time.monotonic()
            self.hub.status()
            took.append((time.monotonic() - t0, "status", ""))
        with self.engine._lock:                                               # as if the GPU were looking at a shader
            t = threading.Thread(target=run)
            t.start()
            t.join(30)
            self.assertFalse(t.is_alive(), "a control waited for the engine's lock: %s" % (took[-1:],))
        self.assertEqual(errors, [])
        self.assertLess(max(took)[0], 2.0, max(took))
        # and the calls were real ones: everything this fake box can do answered 200. What is left: a pad with no clip
        # on it, Room scenes with the Room module off
        # (and, once a Stop button of a layout has been pressed, shader values and presets with no shader on)
        refused = sorted({(path, status) for (path, status) in answers if status != 200})
        self.assertEqual(refused, [("/api/play", 400), ("/api/room/scene", 409), ("/api/shaders/preset", 409), ("/api/shaders/values", 409)], answers)
        for path in ("/api/shaders/values", "/api/shaders/step", "/api/vibes", "/api/blackout", "/api/fadein", "/api/fadeout", "/api/control"):
            self.assertIn((path, 200), answers, path)
        self.assertTrue({"/api/shaders/values", "/api/shaders/step", "/api/shaders/preset", "/api/vibes", "/api/play", "/api/blackout",
                         "/api/room/scene", "/api/control", "/api/fadein", "/api/fadeout"} <= paths, paths)

    def test_pickup_reads_the_shader_s_speed_without_the_lock(self):
        self.hub._matched["/dev/snd/midiC1D0"] = ("nanoKONTROL2", BY_ID[NANO])
        with self.engine._lock:
            self.assertEqual((self.hub._target("shader_speed"), self.hub._target("shader_brightness"), self.hub._target("opacity")), (1.0, 1.0, 100.0))
            self.assertEqual((self.hub._target("volume"), self.hub._target("speed"), self.hub._target("shader_hue")), (100.0, 1.0, None))
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 3, 127))                 # fader 4 at the top: the speed is 1, a quarter of the way
        self.assertEqual(self.engine.playing["controls"]["speed"], 1.0)
        self.clock[0] += 1
        self.hub.on_message("nanoKONTROL2", ("cc", 0, 3, 30))                  # it passed 1: caught
        self.pump()
        self.assertEqual(self.engine.playing["controls"]["speed"], round(4 * 30 / 127, 2))


if __name__ == "__main__":
    unittest.main()
