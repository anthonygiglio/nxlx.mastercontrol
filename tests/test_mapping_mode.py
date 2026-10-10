# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""D75: layers on a controller. Mapping mode (the owner's switch, the mode, what works only inside it, what ends it,
every nudge the mapper's own checked move) and the nanoKONTROL2's geometry layer.

Nothing here has touched a real controller or a projector: pipes, a fake player and direct calls stand in."""
import json
import time
import unittest

from pvj import boxcare, midi, osc
from pvj.midi import MidiMapper
from tests.test_lights import LightsHubBase
from tests.test_midi import Recorder
from pvj.osc import OscServer
from tests.test_controllers import BY_ID, HubBase, NANO, PAD
from tests.test_osc import msg

C_NANO = "/dev/snd/midiC1D0"
NUDGE = "/api/mapper/nudge"


class Base(HubBase):
    def setUp(self):
        super().setUp()
        self.post("/api/modules/mapper", {"enabled": True})
        for kind in ("quad", "triangle"):
            self.assertEqual(self.post("/api/mapper", {"action": "add", "type": kind})[0], 200)
        self.mapper = self.api.mapper

    def allow(self, on=True):
        return self.post("/api/mapper/remote", {"allow": on})

    def ask(self, body, device=midi.MIDI_DEVICE):
        return self.api.handle("POST", NUDGE, body, device, "t")

    def chosen(self):
        st = self.mapper.state()
        s = next(x for x in st["surfaces"] if x["id"] == st["edit"]["selected"])
        return s, st["edit"]["corner"], s["vertices"][st["edit"]["corner"]]


class SwitchAndModeTest(Base):
    def test_the_owners_switch(self):
        self.assertEqual(self.mapper.state()["controllers"], {"allow": False, "mode": False, "step": 1, "undo": False, "seconds_left": 0})
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/mapper/remote", {"allow": True}, token=live)[0], 403)     # full access only
        self.assertEqual(self.post("/api/mapper/remote", {"allow": "yes"})[0], 400)
        self.assertEqual(self.post("/api/mapper/remote", {"allow": True, "mode": True})[0], 400)
        before = self.chosen()
        for body in ({"mode": True}, {"steps": [5, 0]}, {"surface": 1}, {"undo": True}):           # the switch is off: nothing at all
            self.assertEqual(self.ask(body)[0], 409, body)
        self.assertEqual((self.chosen(), self.mapper.edit["on"], self.mapper.remote_on()), (before, False, False))
        self.assertEqual(self.allow()[1]["controllers"]["allow"], True)
        self.assertEqual(self.settings.data["control"]["mapping"], True)
        # a presenter's phone gets no way round the Mapping screen's full access through the controllers' route
        self.assertEqual(self.post(NUDGE, {"mode": True}, token=live)[0], 403)
        self.assertEqual(self.post(NUDGE, {"mode": True})[0], 403)                                # nor a full-access phone: it has the screen
        for device in ({"id": "dmx", "role": "live"}, {"id": "room", "role": "live"}):
            self.assertEqual(self.ask({"mode": True}, device)[0], 403)
        self.assertFalse(self.mapper.remote_on())

    def test_nothing_moves_outside_the_mode(self):
        self.allow()
        before = self.chosen()
        for body in ({"steps": [5, 0]}, {"surface": 1}, {"corner": 1}, {"step": 10}, {"undo": True}):
            st, payload = self.ask(body)
            self.assertEqual((st, payload["error"]), (409, "not in mapping mode"), body)
        self.assertEqual(self.chosen(), before)

    def test_in_the_mode_choose_nudge_and_undo(self):
        self.allow()
        self.assertEqual(self.ask({"mode": True})[1]["controllers"]["mode"], True)
        self.assertTrue(self.mapper.edit["on"])                     # the display shows the outlines, the chosen surface and corner marked
        first, corner, at = self.chosen()
        self.assertEqual(corner, 0)
        self.ask({"steps": [3, -2]})
        self.assertEqual(self.chosen()[2], [at[0] + 3, at[1] - 2])  # one pixel a step
        self.assertEqual(self.ask({"step": "next"})[1]["controllers"]["step"], 10)
        self.ask({"steps": [1, 1]})
        self.assertEqual(self.chosen()[2], [at[0] + 13, at[1] + 8])
        self.assertEqual(self.ask({"undo": True})[0], 200)          # the last nudge only
        self.assertEqual(self.chosen()[2], [at[0] + 3, at[1] - 2])
        self.assertEqual(self.ask({"undo": True})[0], 409)
        self.assertEqual(self.ask({"step": 50})[1]["controllers"]["step"], 50)
        self.ask({"steps": [100, 0]})                               # a hundred steps of fifty: one message moves 200 pixels at most
        self.assertEqual(self.chosen()[2][0], at[0] + 3 + 200)
        self.ask({"undo": True})
        # the corner, round and round; the surface, round and round
        self.ask({"corner": 1})
        self.assertEqual(self.chosen()[1], 1)
        self.ask({"corner": -1}); self.ask({"corner": -1})
        self.assertEqual(self.chosen()[1], len(first["vertices"]) - 1)
        self.ask({"surface": 1})
        second = self.chosen()[0]
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(self.chosen()[1], 0)
        self.ask({"surface": 1})
        self.assertEqual(self.chosen()[0]["id"], first["id"])
        self.assertEqual(self.ask({"undo": True})[0], 409)          # an undo does not reach back across another choice
        for bad in ({"steps": [1]}, {"steps": [1.5, 0]}, {"steps": [200, 0]}, {"steps": "x"}, {"surface": 2}, {"corner": 0}, {"step": 7},
                    {"undo": False}, {"mode": 1}, {"steps": [1, 0], "undo": True}, {}):
            self.assertEqual(self.ask(bad)[0], 409, bad)

    def test_a_nudge_is_the_mappers_own_checked_move(self):
        self.allow()
        self.ask({"mode": True})
        self.ask({"step": 50})
        if self.chosen()[0]["type"] != "quad":
            self.ask({"surface": 1})
        s, corner, at = self.chosen()
        self.assertEqual(s["type"], "quad")
        saved = [list(p) for p in s["vertices"]]
        refused = None
        for _ in range(60):                                         # push one corner of the quad across the others until it would fold
            st, payload = self.ask({"steps": [4, 4]})
            if st != 200:
                refused = payload["error"]
                break
        self.assertIsNotNone(refused, "a surface could be folded from a controller")
        now = self.chosen()[0]["vertices"]
        self.assertEqual(self.settings.data["mapper"]["surfaces"][0]["vertices"] if self.settings.data["mapper"]["surfaces"][0]["id"] == s["id"]
                         else now, now)                             # what is saved is what the mapper accepted last
        self.assertNotEqual(now, saved)

    def on_disk(self, sid, corner):
        with open(self.settings.path) as f:
            return next(x for x in json.load(f)["mapper"]["surfaces"] if x["id"] == sid)["vertices"][corner]

    def test_a_flood_of_nudges_is_saved_twice_a_second_and_nothing_is_lost(self):
        self.allow()
        self.ask({"mode": True})
        s, corner, at = self.chosen()
        saves, shown = [], []
        save, show = self.settings.save, self.mapper.apply
        self.settings.save = lambda: (saves.append(1), save())[1]
        self.mapper.apply = lambda: (shown.append(self.chosen()[2]), show())[1]
        self.addCleanup(lambda: (setattr(self.settings, "save", save), setattr(self.mapper, "apply", show)))
        began = time.monotonic()
        for i in range(500):                                        # OSC lets 200 a second through, a knob gives 20
            self.assertEqual(self.ask({"steps": [[1, 0], [1, 0], [-1, 0], [0, 1], [0, -1]][i % 5]})[0], 200)
        took = time.monotonic() - began
        end = [at[0] + 100, at[1]]
        self.assertEqual(self.chosen()[2], end)                     # every step counted, at once
        self.assertLessEqual(len(saves), 2 + 2 * took, "%d saves in %.2f s" % (len(saves), took))
        self.assertLessEqual(len(shown), 3 + 25 * took, "%d pictures in %.2f s" % (len(shown), took))
        time.sleep(1.2)                                             # nothing more comes: within a second it is saved and shown as it ended
        self.assertEqual(self.on_disk(s["id"], corner), end)
        self.assertEqual(shown[-1], end)
        self.assertLessEqual(len(saves), 3 + 2 * took)
        n = len(saves)
        time.sleep(0.7)
        self.assertEqual(len(saves), n, "it went on saving with nothing to save")

    def test_leaving_the_mode_saves_what_was_not_saved_yet(self):
        self.allow()
        self.ask({"mode": True})
        s, corner, at = self.chosen()
        for _ in range(4):
            self.ask({"steps": [1, 1]})
        self.ask({"mode": False})
        self.assertEqual(self.on_disk(s["id"], corner), [at[0] + 4, at[1] + 4])
        self.ask({"mode": True})
        for _ in range(4):
            self.ask({"steps": [1, 1]})
        self.allow(False)                                           # and the owner's switch going off
        self.assertEqual(self.on_disk(s["id"], corner), [at[0] + 8, at[1] + 8])

    def test_one_undo_takes_back_one_run_of_nudges(self):
        """A knob's turn comes as many messages: an undo that took back the last of them would take back a pixel
        or two. One step of undo is a run: the nudges of one corner that follow each other within a second."""
        now = [1000.0]
        self.mapper._remote_clock = lambda: now[0]
        self.allow()
        self.ask({"mode": True})
        s, corner, at = self.chosen()
        for _ in range(5):                                          # a knob turned
            now[0] += 0.05
            self.ask({"steps": [2, 1]})
        now[0] += 2
        for _ in range(3):                                          # and after a pause an arrow pressed three times
            now[0] += 0.4
            self.ask({"steps": [0, -1]})
        self.assertEqual(self.chosen()[2], [at[0] + 10, at[1] + 2])
        self.assertTrue(self.mapper.state()["controllers"]["undo"])
        self.assertEqual(self.ask({"undo": True})[0], 200)
        self.assertEqual(self.chosen()[2], [at[0] + 10, at[1] + 5]) # the three presses, in one move
        self.assertEqual(self.ask({"undo": True})[0], 409)          # one step back, no further
        now[0] += 0.1
        self.ask({"steps": [1, 0]})
        now[0] += 0.1
        self.ask({"step": 10})                                      # another step size: what comes now is another run
        now[0] += 0.1
        self.ask({"steps": [1, 0]})
        self.ask({"undo": True})
        self.assertEqual(self.chosen()[2], [at[0] + 11, at[1] + 5])
        now[0] += 0.1
        self.ask({"steps": [3, 3]})                                 # a run that a refused nudge interrupts is still one run
        self.assertEqual(self.ask({"steps": [1.5, 0]})[0], 409)
        self.ask({"steps": [1, 0]})
        self.ask({"undo": True})
        self.assertEqual(self.chosen()[2], [at[0] + 11, at[1] + 5])
        self.ask({"mode": False})
        self.assertEqual(self.on_disk(s["id"], corner), [at[0] + 11, at[1] + 5])

    def test_the_panels_own_edit_is_as_it_was_afterwards(self):
        """The mode shows its outlines through the panel's "Edit on the display" and chooses through the panel's
        selection. A full-access person who had that on, with a surface and a corner chosen, has them back."""
        self.allow()
        other = self.mapper.state()["surfaces"][1]["id"]
        self.assertEqual(self.post("/api/mapper", {"action": "edit", "on": True, "selected": other})[0], 200)
        self.assertEqual(self.post("/api/mapper", {"action": "edit", "corner": 2})[0], 200)
        before = dict(self.mapper.edit)
        self.assertEqual((before["on"], before["selected"], before["corner"]), (True, other, 2))
        for leave in (lambda: self.ask({"mode": False}), lambda: self.allow(False),
                      lambda: (setattr(self.mapper, "_remote_clock", lambda: time.monotonic() + 1000), self.mapper._expire(self.mapper._remote["serial"]))):
            self.allow()
            self.ask({"mode": True})
            self.ask({"surface": 1})
            self.ask({"corner": 1})
            self.assertNotEqual(self.mapper.edit, before)
            leave()
            self.assertFalse(self.mapper._remote["on"])
            self.assertEqual(self.mapper.edit, before)
        self.mapper._remote_clock = time.monotonic
        self.allow()
        self.ask({"mode": True})                                    # its surface was removed meanwhile: edit stays on, on what there is
        self.post("/api/mapper", {"action": "remove", "id": other})
        self.ask({"mode": False})
        self.assertTrue(self.mapper.edit["on"])
        self.assertNotEqual(self.mapper.edit["selected"], other)

    def test_what_ends_the_mode_and_what_does_not(self):
        self.allow()
        self.ask({"mode": True})
        for path, body in (("/api/blackout", {"on": True}), ("/api/blackout", {"on": False}), ("/api/control", {"action": "stop"}),
                           ("/api/play", {"file": "a.mp4"}), ("/api/fade", {"seconds": 0.1})):
            self.post(path, body)
        self.assertTrue(self.mapper.remote_on(), "Blackout, Stop, a play or a fade ended mapping mode")
        self.assertEqual(self.ask({"mode": "toggle"})[1]["controllers"]["mode"], False)       # the same button
        self.assertFalse(self.mapper.edit["on"])                    # and the outlines leave the display
        # by itself, three minutes after the last thing done in it
        now = [1000.0]
        self.mapper._remote_clock = lambda: now[0]
        self.ask({"mode": True})
        serial = self.mapper._remote["serial"]
        now[0] += 170
        self.ask({"steps": [1, 0]})                                 # something done: the three minutes start again
        now[0] += 170
        self.assertTrue(self.mapper.remote_on())
        self.assertEqual(self.mapper.state()["controllers"]["seconds_left"], 10)
        now[0] += 11
        self.assertFalse(self.mapper.remote_on())
        self.assertEqual(self.ask({"steps": [1, 0]})[0], 409)
        self.mapper._expire(serial)                                 # the timer's own call
        self.assertFalse(self.mapper.edit["on"])
        # the owner's switch going off ends it at once
        self.ask({"mode": True})
        self.allow(False)
        self.assertEqual((self.mapper.remote_on(), self.mapper.edit["on"], "mapping" in self.settings.data["control"]), (False, False, False))

    def test_the_switch_is_not_taken_from_a_settings_file(self):
        self.allow()
        clean = boxcare.check_control(self.settings.data["control"], None)
        self.assertNotIn("mapping", clean)                          # an import leaves it off
        from pvj.settings import default_control
        self.assertNotIn("mapping", default_control())              # and so does a factory reset


class OnTheControllersTest(Base):
    def test_the_layouts_other_selves(self):
        nano = {c["id"]: c for c in BY_ID[NANO]["controls"]}
        self.assertEqual((nano["m8"]["action"], nano["m8"]["guard"]), ({"action": "mapping_mode"}, True))
        self.assertEqual({k: v["layers"]["mapping"]["action"] for k, v in nano.items() if "mapping" in v["layers"]},
                         {"track_prev": "map_surface_prev", "track_next": "map_surface_next", "marker_prev": "map_corner_prev",
                          "marker_next": "map_corner_next", "marker_set": "map_undo", "cycle": "map_step", "knob2": "map_x", "knob3": "map_y"})
        pad = {c["id"]: c for c in BY_ID[PAD]["controls"]}
        self.assertEqual((pad["top8"]["action"], pad["top8"]["guard"]), ({"action": "mapping_mode"}, True))
        self.assertEqual({k: v["layers"]["mapping"]["action"] for k, v in pad.items() if "mapping" in v["layers"]},
                         {"pad71": "map_surface_prev", "pad72": "map_surface_next", "pad73": "map_corner_prev", "pad74": "map_corner_next",
                          "pad76": "map_up", "pad78": "map_undo", "pad84": "map_step", "pad85": "map_left", "pad86": "map_down", "pad87": "map_right"})
        for p in BY_ID.values():                                    # nothing that darkens or stops is ever given another self
            for c in p["controls"]:
                if c["layers"]:
                    self.assertNotIn((c["action"] or {}).get("action"), ("blackout", "stop", "fade", "pause", "pad", "bank_pad"), (p["id"], c["id"]))
        self.assertTrue(midi.guardable("mapping_mode"))
        for bad in ({"action": "stop"}, {"action": "map_x"}, {"action": "map_left", "guard": True}):      # a button: only choose, nudge, undo
            raw = {"id": "x", "name": "X", "row": 0, "col": 0, "kind": "button", "send": {"type": "note", "channel": 0, "number": 1}, "action": None, "layers": {"mapping": bad}}
            with self.assertRaises(midi.MidiError, msg=bad):
                midi.validate_profile({"id": "t", "name": "T", "match": {"card_ids": ["T"], "card_names": []}, "description": "d", "sources": ["s"],
                                       "layout": {"rows": 1, "cols": 1}, "controls": [raw]}, "t")
        with self.assertRaises(midi.MidiError):
            midi.validate_entry({"kind": "note", "number": 1, "action": "map_x"})
        snap = {"mapping": False, "mapping_ready": False}
        mode = {"action": "mapping_mode"}
        self.assertEqual([midi.light_state(mode, dict(snap, **k)) for k in ({}, {"mapping_ready": True}, {"mapping_ready": True, "mapping": True})],
                         ["off", "on", "flash"])

    def press_twice(self, number):
        for _ in range(2):
            self.send(C_NANO, [0xB0, number, 127])
            self.send(C_NANO, [0xB0, number, 0])
            time.sleep(0.35)

    def test_a_nanokontrol_enters_the_mode_nudges_and_leaves(self):
        self.present = [C_NANO]
        self.enable()
        self.wait(lambda: C_NANO in self.pipes)
        self.player.running = True
        self.press_twice(55)                                        # M 8, twice: but the owner's switch is off
        time.sleep(0.2)
        self.assertFalse(self.mapper.remote_on())
        self.allow()
        self.send(C_NANO, [0xB0, 55, 127]); self.send(C_NANO, [0xB0, 55, 0])
        time.sleep(0.4)
        self.assertFalse(self.mapper.remote_on(), "one press entered mapping mode")
        self.press_twice(55)
        self.wait(self.mapper.remote_on)
        s, corner, at = self.chosen()
        bank, x = self.hub.mapper.bank, self.api.mix["position"]
        self.send(C_NANO, [0xB0, 17, 70])                           # knob 2: its first touch moves nothing
        time.sleep(0.3)
        self.assertEqual(self.chosen()[2], at)
        self.send(C_NANO, [0xB0, 17, 73])                           # three steps to the right
        self.wait(lambda: self.chosen()[2] == [at[0] + 3, at[1]])
        self.send(C_NANO, [0xB0, 18, 40]); time.sleep(0.2)
        self.send(C_NANO, [0xB0, 18, 38])                           # knob 3: two steps up
        self.wait(lambda: self.chosen()[2] == [at[0] + 3, at[1] - 2])
        self.assertEqual(self.api.mix["position"], x)               # the picture's own position was not touched
        self.send(C_NANO, [0xB0, 62, 127]); self.send(C_NANO, [0xB0, 62, 0])      # Marker >: the next corner, not the next bank
        self.wait(lambda: self.chosen()[1] == 1)
        self.assertEqual(self.hub.mapper.bank, bank)
        self.send(C_NANO, [0xB0, 59, 127]); self.send(C_NANO, [0xB0, 59, 0])      # Track >: the next surface
        self.wait(lambda: self.chosen()[0]["id"] != s["id"])
        self.send(C_NANO, [0xB0, 42, 127]); self.send(C_NANO, [0xB0, 42, 0])      # Stop is still Stop
        self.wait(lambda: ("clear",) in self.player.calls)
        self.assertTrue(self.mapper.remote_on())
        ctl = {c["id"]: c for c in self.controller("nanoKONTROL2")["controls"]}
        self.assertEqual((ctl["knob2"]["layers"]["mapping"], ctl["m8"]["action"], ctl["m8"]["guard"]), ({"action": "map_x"}, {"action": "mapping_mode"}, True))
        self.press_twice(55)                                        # out again
        self.wait(lambda: not self.mapper.remote_on())
        moved = self.chosen()
        self.send(C_NANO, [0xB0, 62, 127]); self.send(C_NANO, [0xB0, 62, 0])      # Marker > is the next bank again
        self.wait(lambda: self.hub.mapper.bank == (bank + 1) % 3)
        self.send(C_NANO, [0xB0, 17, 90]); time.sleep(0.3)          # and knob 2 nudges nothing
        self.assertEqual(self.chosen(), moved)


class OverOscTest(Base):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.server = OscServer(self.api, log=self.logs.append)

    def osc(self, address, *args):
        return self.server.handle_packet(msg(address, *args), "192.168.1.20")

    def test_the_same_mode_behind_the_same_switch(self):
        for address, args in (("/pvj/mapping/mode", (1,)), ("/pvj/mapping/right", (1.0,)), ("/pvj/mapping/nudge", (2, 3))):
            self.assertEqual(self.osc(address, *args), 0)           # the switch is off
        self.assertFalse(self.mapper.remote_on())
        self.allow()
        self.assertEqual(self.osc("/pvj/mapping/right", 1.0), 0)    # and nothing outside the mode
        self.assertEqual(self.osc("/pvj/mapping/mode", 1), 1)
        self.assertEqual(self.osc("/pvj/mapping/mode", 1), 1)       # said outright: on twice is on
        self.assertTrue(self.mapper.remote_on())
        s, corner, at = self.chosen()
        self.assertEqual(self.osc("/pvj/mapping/right", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/right", 0.0), 0)    # a button's release
        self.assertEqual(self.osc("/pvj/mapping/down", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/nudge", 2, -3), 1)
        self.assertEqual(self.chosen()[2], [at[0] + 3, at[1] - 2])
        self.assertEqual(self.osc("/pvj/mapping/undo", 1.0), 1)     # the three came within a second: one run, one step of undo
        self.assertEqual(self.chosen()[2], at)
        self.assertEqual(self.osc("/pvj/mapping/step", 10), 1)
        self.assertEqual(self.osc("/pvj/mapping/left"), 1)
        self.assertEqual(self.chosen()[2], [at[0] - 10, at[1]])
        self.assertEqual(self.osc("/pvj/mapping/corner/next", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/surface/prev", 1.0), 1)
        self.assertNotEqual(self.chosen()[0]["id"], s["id"])
        for address, args in (("/pvj/mapping/nudge", (1,)), ("/pvj/mapping/nudge", (1.5, 0.0)), ("/pvj/mapping/step", (7,)),
                              ("/pvj/mapping/add", (1,)), ("/pvj/mapping/mode", ("x",))):
            self.assertEqual(self.osc(address, *args), 0, address)
        self.assertEqual(self.osc("/pvj/mapping/mode/off", 1), 1)
        self.assertFalse(self.mapper.remote_on())
        self.assertEqual(osc.translate("/pvj/mapping/mode", []), ("/api/mapper/nudge", {"mode": "toggle"}))

    def test_a_buttons_release_does_not_leave_the_mode(self):
        """A TouchOSC button sends 1 when it is pressed and 0 when it is let go. The 0 used to be "leave": the mode
        was over as the finger came up (the trap /pvj/fadein had)."""
        self.allow()
        logged = len(self.logs)
        self.assertEqual(self.osc("/pvj/mapping/mode", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/mode", 0.0), 0)     # the release: nothing, and nothing in the log
        self.assertTrue(self.mapper.remote_on())
        self.assertEqual(self.osc("/pvj/mapping/mode", False), 0)
        self.assertTrue(self.mapper.remote_on())
        self.assertEqual(self.osc("/pvj/mapping/mode/off", 0.0), 0) # and the release of the button that leaves
        self.assertTrue(self.mapper.remote_on())
        self.assertEqual(self.osc("/pvj/mapping/mode/off", 1.0), 1) # said outright
        self.assertFalse(self.mapper.remote_on())
        self.assertEqual(self.osc("/pvj/mapping/mode/off"), 1)      # off twice is off
        self.assertEqual(self.osc("/pvj/mapping/mode/on", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/mode/on", 0.0), 0)
        self.assertTrue(self.mapper.remote_on())
        for want in (False, True):                                  # one momentary button for both: press, release, press, release
            self.assertEqual(self.osc("/pvj/mapping/mode/toggle", 1.0), 1)
            self.assertEqual(self.osc("/pvj/mapping/mode/toggle", 0.0), 0)
            self.assertEqual(self.mapper.remote_on(), want)
        self.assertEqual(self.osc("/pvj/mapping/mode"), 1)          # no argument switches over, as it did
        self.assertFalse(self.mapper.remote_on())
        self.assertEqual(self.logs[logged:], [])


class GeometryLayerTest(unittest.TestCase):
    """The nanoKONTROL2's geometry layer on the bare mapper and a fake clock: all eight knobs are the shader's, one
    button turns the first three into zoom and the two positions, and nothing jumps going in or coming out."""

    def setUp(self):
        self.rec, self.t, self.have, self.mapping = Recorder(), [100.0], {"size": 100.0, "position": 0.0, "position_y": 0.0, "opacity": 100.0}, [False]
        self.m = MidiMapper(self.rec, midi.profile_entries(BY_ID[NANO], "nano") + midi.builtin_map(), {"blackout": False}, clock=lambda: self.t[0])
        self.m.profiled = {"nano"}
        self.m.target = lambda action: self.have.get(action)
        self.m.mapping_mode = lambda: self.mapping[0]

    def cc(self, number, v, after=1.0):
        self.t[0] += after
        n = len(self.rec.calls)
        self.m.message("nano", ("cc", 0, number, v))
        return self.rec.calls[n:]

    def button(self):
        self.cc(53, 127)
        self.cc(53, 0, 0.05)

    def test_in_and_out_by_one_plain_press(self):
        self.assertEqual(self.cc(16, 30), [("/api/shaders/values", {"control": 1, "level": 30})])
        self.assertIsNone(self.m.active_layer("nano"))
        self.button()
        self.assertEqual((self.m.active_layer("nano"), self.rec.calls[1:]), ("geometry", []))    # one press, and nothing asked of the box
        self.assertEqual(self.cc(16, 127), [])                      # knob 1 is the zoom now, and waits for the picture's 100 percent
        self.assertEqual(self.cc(16, 64), [("/api/control", {"action": "size", "value": 100.0})])
        self.assertEqual(self.cc(17, 64), [("/api/control", {"action": "position", "value": 0.0})])
        self.assertEqual(self.cc(18, 64), [("/api/control", {"action": "position_y", "value": 0.0})])
        for knob in (19, 20, 21, 22, 23):                           # the other five rest: they must not move the shader unseen
            self.assertEqual(self.cc(knob, 99), [], knob)
        self.assertEqual(self.cc(0, 127), [("/api/control", {"action": "opacity", "value": 100.0})])     # a fader is what it always is
        self.assertEqual(self.cc(42, 127), [("/api/control", {"action": "stop"})])
        self.button()
        self.assertIsNone(self.m.active_layer("nano"))
        self.assertEqual(self.cc(20, 50), [("/api/shaders/values", {"control": 5, "level": 50})])       # never set a control before: free, as always

    def test_coming_back_a_shader_knob_waits_until_it_is_where_it_stood(self):
        self.cc(16, 30)                                             # knob 1 set the shader's first control at 30
        self.button()
        self.cc(16, 64)
        self.cc(16, 90)                                             # and was then turned up as the zoom
        self.button()
        self.assertEqual(self.cc(16, 88), [])                       # back on the shader: 88 would be a jump from 30
        self.assertEqual(self.cc(16, 60), [])
        self.assertEqual(self.cc(16, 32), [("/api/shaders/values", {"control": 1, "level": 32})])       # it met where it stood: followed again
        self.assertEqual(self.cc(16, 80), [("/api/shaders/values", {"control": 1, "level": 80})])
        # and going in again, the zoom knob starts its pickup afresh: it was at 90 as the zoom, the picture still is, the knob is at 80
        self.have["size"] = midi.level_value("size", 90)
        self.button()
        self.assertEqual(self.cc(16, 20), [])
        self.assertTrue(self.m.waiting("nano", "cc", 16))

    def test_coming_back_from_mapping_mode_a_shader_knob_waits_until_it_is_where_it_stood(self):
        """The twin of the test above for the box's own layer. Mapping mode comes and goes in the mapper's memory
        (a controller, OSC, the panel's switch, the time running out): nobody tells this controller."""
        self.assertEqual(self.cc(17, 20), [("/api/shaders/values", {"control": 2, "level": 20})])      # knob 2 set the shader's second control at 20
        self.mapping[0] = True
        self.assertEqual(self.cc(17, 22), [])                       # its first touch as the nudge moves nothing
        self.assertEqual(self.cc(17, 90), [(NUDGE, {"steps": [68, 0]})])
        self.mapping[0] = False
        self.assertEqual(self.cc(17, 92), [])                       # back on the shader: 92 would be a jump from 20
        self.assertEqual(self.cc(17, 60), [])
        self.assertEqual(self.cc(17, 21), [("/api/shaders/values", {"control": 2, "level": 21})])       # it met where it stood
        self.assertEqual(self.cc(17, 70), [("/api/shaders/values", {"control": 2, "level": 70})])
        # a level starts its pickup afresh on the way in and on the way out, as with Geometry
        self.cc(0, 127)
        self.assertFalse(self.m.waiting("nano", "cc", 0))
        self.have["opacity"] = 40.0                                 # the panel moved it meanwhile
        self.mapping[0] = True
        self.assertEqual(self.cc(0, 126), [])
        self.assertTrue(self.m.waiting("nano", "cc", 0))

    def test_into_mapping_mode_a_second_time_a_knob_does_not_nudge_by_where_it_was_turned_meanwhile(self):
        """The other direction of the same change of layer: a knob that nudges remembers where it stood, and outside
        the mode nothing tells it that it moved (it is the shader's knob there)."""
        self.mapping[0] = True
        self.cc(17, 10)
        self.assertEqual(self.cc(17, 12), [(NUDGE, {"steps": [2, 0]})])
        self.mapping[0] = False
        self.cc(17, 100)                                            # the shader's second control, far up
        self.mapping[0] = True
        self.assertEqual(self.cc(17, 101), [])                      # its first touch in the mode moves nothing, the second time too
        self.assertEqual(self.cc(17, 103), [(NUDGE, {"steps": [2, 0]})])
        # steps turned but not yet sent when the mode ends are not sent into the next one
        self.cc(17, 104, after=0.001)
        self.mapping[0] = False
        self.t[0] += 1
        self.assertEqual(self.m.flush_calls(), [])
        self.mapping[0] = True
        self.t[0] += 1
        self.assertEqual(self.m.flush_calls(), [])
        self.assertEqual(self.cc(17, 104), [])

    def test_mapping_mode_over_geometry_and_back_nothing_jumps(self):
        self.cc(16, 30)                                             # knob 1 on the shader at 30
        self.button()
        self.cc(16, 64); self.cc(16, 90)                            # the zoom
        self.mapping[0] = True                                      # mapping mode comes over it: knob 1 is the shader's again
        self.assertEqual(self.m.active_layer("nano"), "mapping")
        self.assertEqual(self.cc(16, 91), [])                       # and must not jump from 30 to 91
        self.mapping[0] = False                                     # Geometry is still on underneath
        self.assertEqual(self.m.active_layer("nano"), "geometry")
        self.have["size"] = 100.0
        self.assertEqual(self.cc(16, 92), [])                       # the zoom's pickup started afresh
        self.assertTrue(self.m.waiting("nano", "cc", 16))

    def test_unplugged_in_a_layer_it_comes_back_plain(self):
        self.cc(16, 30)
        self.button()
        self.cc(16, 64); self.cc(16, 90)
        self.assertEqual(self.m.active_layer("nano"), "geometry")
        self.m.forget("nano")                                       # the cable came out
        self.assertIsNone(self.m.active_layer("nano"))
        self.assertEqual(self.m.layers, {})
        self.assertEqual(self.cc(16, 91), [("/api/shaders/values", {"control": 1, "level": 91})])      # plugged in again: a shader knob, free as on a first touch
        # and in mapping mode: the mode is the box's and goes on, the controller's memory of it does not
        self.mapping[0] = True
        self.cc(17, 10)
        self.m.forget("nano")
        self.assertEqual(self.m.active_layer("nano"), "mapping")
        self.assertEqual(self.cc(17, 50), [])                       # the first touch after coming back nudges nothing
        self.assertEqual(self.cc(17, 51), [(NUDGE, {"steps": [1, 0]})])

    def test_two_controllers_each_in_its_own_layer(self):
        self.m.entries.extend(midi.profile_entries(BY_ID[NANO], "two"))
        self.m.profiled = {"nano", "two"}

        def cc(source, number, v):
            self.t[0] += 1.0
            n = len(self.rec.calls)
            self.m.message(source, ("cc", 0, number, v))
            return self.rec.calls[n:]
        cc("nano", 16, 30); cc("two", 16, 40)
        self.button()                                               # the first goes into Geometry, the second does not
        self.assertEqual((self.m.active_layer("nano"), self.m.active_layer("two")), ("geometry", None))
        self.assertEqual(cc("nano", 16, 64), [("/api/control", {"action": "size", "value": 100.0})])
        self.assertEqual(cc("two", 16, 41), [("/api/shaders/values", {"control": 1, "level": 41})])
        self.assertEqual(cc("two", 53, 127), [])                    # and the second, by its own button
        cc("two", 53, 0)
        self.button()                                               # the first comes out
        self.assertEqual((self.m.active_layer("nano"), self.m.active_layer("two")), (None, "geometry"))
        self.assertEqual(cc("nano", 16, 66), [])                    # it waits where it stood (30); the other is the zoom
        self.assertEqual(cc("two", 16, 64), [("/api/control", {"action": "size", "value": 100.0})])
        self.mapping[0] = True                                      # the box's layer is over both
        self.assertEqual((self.m.active_layer("nano"), self.m.active_layer("two")), ("mapping", "mapping"))
        cc("nano", 17, 10); cc("two", 17, 100)
        self.assertEqual(cc("nano", 17, 12), [(NUDGE, {"steps": [2, 0]})])
        self.assertEqual(cc("two", 17, 97), [(NUDGE, {"steps": [-3, 0]})])
        self.mapping[0] = False                                     # and off: each is back where it was
        self.assertEqual((self.m.active_layer("nano"), self.m.active_layer("two")), (None, "geometry"))
        self.assertEqual(cc("nano", 17, 14), [("/api/shaders/values", {"control": 2, "level": 14})])    # the first's knob 2 never set a control: free, and no nudge
        self.have["position"] = 0.0
        self.assertEqual(cc("two", 17, 97), [])                     # the second's knob 2 is the position, and waits for the picture's

    def test_a_learned_mapping_wins_inside_a_layer(self):
        mine = midi.validate_entry({"kind": "cc", "number": 17, "action": "volume"})       # the person's own: knob 2 is the volume
        self.m.entries.append(mine)
        self.have["volume"] = 100.0
        self.cc(17, 127)
        self.assertEqual(self.rec.calls[-1][1]["action"], "volume")
        self.button()                                               # in Geometry knob 2 would be the position
        self.assertEqual(self.cc(17, 120)[0][1]["action"], "volume")
        self.mapping[0] = True                                      # in mapping mode it would nudge
        calls = self.cc(17, 110) + self.cc(17, 100)
        self.assertEqual([c[1]["action"] for c in calls], ["volume", "volume"])
        self.assertNotIn(NUDGE, [c[0] for c in self.rec.calls])
        # a layer that changed while only Learned controls were touched is still seen: knob 1 parked for Geometry stays right
        self.mapping[0] = False
        self.assertEqual(self.m.active_layer("nano"), "geometry")
        self.assertEqual(self.cc(17, 90)[0][1]["action"], "volume")

    def test_it_ends_by_itself_two_minutes_after_the_last_touch(self):
        self.button()
        self.cc(16, 64, after=100)                                  # a touch of one of its controls: two minutes from here
        self.cc(0, 100, after=100)                                  # a fader is not one of them
        self.assertEqual(self.m.active_layer("nano"), "geometry")
        self.t[0] += 21
        self.assertIsNone(self.m.active_layer("nano"))
        self.assertEqual(midi.LAYER_SECONDS, {"geometry": 120.0})
        self.cc(19, 40)
        self.assertEqual(self.rec.calls[-1], ("/api/shaders/values", {"control": 4, "level": 40}))      # the shader knobs live again

    def test_one_layer_at_a_time(self):
        self.mapping[0] = True                                      # mapping mode is on (the box's own layer)
        self.button()
        self.assertEqual((self.m.active_layer("nano"), self.m.layers), ("mapping", {}))        # the geometry button does nothing meanwhile
        self.assertEqual(self.cc(16, 50), [("/api/shaders/values", {"control": 1, "level": 50})])       # knob 1 has no other self in mapping mode
        self.mapping[0] = False
        self.button()
        self.mapping[0] = True                                      # and mapping mode comes over a geometry layer that is on
        self.assertEqual(self.m.active_layer("nano"), "mapping")
        self.cc(17, 10)
        self.assertEqual(self.cc(17, 12), [("/api/mapper/nudge", {"steps": [2, 0]})])           # knob 2 nudges, it does not move the picture

    def test_the_buttons_light_and_the_files_rules(self):
        action = {"action": "layer_geometry"}
        self.assertEqual([midi.light_state(action, {}, 0, layer) for layer in (None, "geometry", "mapping")], ["on", "flash", "on"])
        self.assertEqual(midi.light_meaning(action), "layer")
        lights = BY_ID[NANO]["lights"]
        self.assertEqual((midi.light_value(lights, "layer", "on"), midi.light_value(lights, "layer", "flash")), (0, 127))
        self.assertTrue(midi.light_flashes(lights, "layer", "flash"))                           # the writer flashes it
        self.assertIn("m6", lights["controls"])
        base = {"id": "t", "name": "T", "match": {"card_ids": ["T"], "card_names": []}, "description": "d", "sources": ["s"], "layout": {"rows": 1, "cols": 1}}
        knob = {"id": "k", "name": "K", "row": 0, "col": 0, "kind": "knob", "send": {"type": "cc", "channel": 0, "number": 1}, "action": {"action": "shader_control_1"}}
        ok = midi.validate_profile(dict(base, controls=[dict(knob, layers={"geometry": {"action": "size"}})]), "t")
        self.assertEqual(ok["controls"][0]["layers"], {"geometry": {"action": "size"}})
        for bad in ({"shift": {"action": "size"}}, {"geometry": {"action": "stop"}}, {"geometry": {"action": "layer_geometry"}}, {"mapping": None},
                    {"geometry": {"action": "none"}}, {}, [], {"geometry": {"action": "blackout", "guard": False}}):
            with self.assertRaises(midi.MidiError, msg=bad):
                midi.validate_profile(dict(base, controls=[dict(knob, layers=bad)]), "t")


class GeometryOnTheHubTest(LightsHubBase):
    def test_the_card_says_it_and_the_light_flashes_while_it_lasts(self):
        self.plug(C_NANO)
        self.post("/api/midi", {"controller": "nanoKONTROL2", "lights": True})
        self.wait(lambda: C_NANO in self.out and self.out[C_NANO].lit().get((0xB0, 32)) == 127)
        pipe = self.out[C_NANO]
        m6 = lambda: [m[2] for m in pipe.read() if m[:2] == (0xB0, 53)]
        self.wait(lambda: m6() == [0])
        self.send(C_NANO, [0xB0, 53, 127]); self.send(C_NANO, [0xB0, 53, 0])
        self.wait(lambda: self.controller("nanoKONTROL2")["layer"] == "geometry")
        self.wait(lambda: len(m6()) >= 6, timeout=8)                # on, off, on, off ...
        ctl = {c["id"]: c for c in self.controller("nanoKONTROL2")["controls"]}
        self.assertEqual((ctl["knob1"]["action"], ctl["knob1"]["origin"], ctl["knob5"]["action"], ctl["m6"]["lit"]), ({"action": "size"}, "layer", None, "flash"))
        self.send(C_NANO, [0xB0, 53, 127]); self.send(C_NANO, [0xB0, 53, 0])
        self.wait(lambda: self.controller("nanoKONTROL2")["layer"] is None)
        self.wait(lambda: pipe.lit()[(0xB0, 53)] == 0)
        count = len(m6())
        time.sleep(1.0)
        self.assertEqual(len(m6()), count)
        self.assertEqual(next(c for c in self.controller("nanoKONTROL2")["controls"] if c["id"] == "knob1")["action"], {"action": "shader_control_1"})
