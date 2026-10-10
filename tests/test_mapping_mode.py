# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""D75: mapping mode from a controller. The owner's switch, the mode, what works only inside it, what ends it, and
that every nudge is the mapper's own checked move.

Nothing here has touched a real controller or a projector: pipes, a fake player and direct calls stand in."""
import time

from pvj import boxcare, midi, osc
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
        self.assertEqual({k: v["mapping"]["action"] for k, v in nano.items() if v["mapping"]},
                         {"track_prev": "map_surface_prev", "track_next": "map_surface_next", "marker_prev": "map_corner_prev",
                          "marker_next": "map_corner_next", "marker_set": "map_undo", "cycle": "map_step", "knob2": "map_x", "knob3": "map_y"})
        pad = {c["id"]: c for c in BY_ID[PAD]["controls"]}
        self.assertEqual((pad["top8"]["action"], pad["top8"]["guard"]), ({"action": "mapping_mode"}, True))
        self.assertEqual({k: v["mapping"]["action"] for k, v in pad.items() if v["mapping"]},
                         {"pad71": "map_surface_prev", "pad72": "map_surface_next", "pad73": "map_corner_prev", "pad74": "map_corner_next",
                          "pad76": "map_up", "pad78": "map_undo", "pad84": "map_step", "pad85": "map_left", "pad86": "map_down", "pad87": "map_right"})
        for p in BY_ID.values():                                    # nothing that darkens or stops is ever given another self
            for c in p["controls"]:
                if c["mapping"]:
                    self.assertNotIn((c["action"] or {}).get("action"), ("blackout", "stop", "fade", "pause", "pad", "bank_pad"), (p["id"], c["id"]))
        self.assertTrue(midi.guardable("mapping_mode"))
        for bad in ({"action": "stop"}, {"action": "map_x"}, {"action": "map_left", "guard": True}):      # a button: only choose, nudge, undo
            raw = {"id": "x", "name": "X", "row": 0, "col": 0, "kind": "button", "send": {"type": "note", "channel": 0, "number": 1}, "action": None, "mapping": bad}
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
        self.assertEqual((ctl["knob2"]["mapping"], ctl["m8"]["action"], ctl["m8"]["guard"]), ({"action": "map_x"}, {"action": "mapping_mode"}, True))
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
        self.assertEqual(self.osc("/pvj/mapping/undo", 1.0), 1)
        self.assertEqual(self.chosen()[2], [at[0] + 1, at[1] + 1])
        self.assertEqual(self.osc("/pvj/mapping/step", 10), 1)
        self.assertEqual(self.osc("/pvj/mapping/left"), 1)
        self.assertEqual(self.chosen()[2], [at[0] - 9, at[1] + 1])
        self.assertEqual(self.osc("/pvj/mapping/corner/next", 1.0), 1)
        self.assertEqual(self.osc("/pvj/mapping/surface/prev", 1.0), 1)
        self.assertNotEqual(self.chosen()[0]["id"], s["id"])
        for address, args in (("/pvj/mapping/nudge", (1,)), ("/pvj/mapping/nudge", (1.5, 0.0)), ("/pvj/mapping/step", (7,)),
                              ("/pvj/mapping/add", (1,)), ("/pvj/mapping/mode", ("x",))):
            self.assertEqual(self.osc(address, *args), 0, address)
        self.assertEqual(self.osc("/pvj/mapping/mode", 0), 1)
        self.assertFalse(self.mapper.remote_on())
        self.assertEqual(osc.translate("/pvj/mapping/mode", []), ("/api/mapper/nudge", {"mode": "toggle"}))
