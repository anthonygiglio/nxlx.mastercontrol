# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Vibes from a MIDI controller and from a DMX console: the mappers on a fake clock with a recorder, then through the
real API with a fake player. No real controller and no real console has been used."""
import random
import unittest

from mctl import midi, vibes as V
from mctl.dmx import DmxMapper, DmxServer, vibes_of
from mctl.midi import MidiHub, MidiMapper
from tests.test_control import artnet
from tests.test_shaders import Base


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        return True


def entry(**kw):
    e = {"source": "*", "kind": "note", "channel": 0, "number": 40, "action": "vibes"}
    e.update(kw)
    return midi.validate_entry(e)


class MidiMapTest(unittest.TestCase):
    def setUp(self):
        self.rec, self.t, self.on = Recorder(), [100.0], [False]
        self.m = MidiMapper(self.rec, [entry(), entry(number=41, action="vibes_next"), entry(kind="cc", number=7, action="vibes_dwell")],
                            {"blackout": False}, clock=lambda: self.t[0])
        self.m.vibes_on = lambda: self.on[0]

    def test_a_press_switches_vibes_on_or_off_and_a_release_does_nothing(self):
        self.m.message("Mini", ("on", 0, 40, 100))
        self.m.message("Mini", ("off", 0, 40, 0))
        self.assertEqual(self.rec.calls, [("/api/vibes", {"on": True})])
        self.on[0] = True
        self.t[0] += 1
        self.m.message("Mini", ("on", 0, 40, 100))
        self.assertEqual(self.rec.calls[-1], ("/api/vibes", {"on": False}))

    def test_a_bouncing_button_fires_once(self):
        for _ in range(5):
            self.m.message("Mini", ("on", 0, 41, 127))
            self.m.message("Mini", ("off", 0, 41, 0))
            self.t[0] += 0.02
        self.assertEqual(self.rec.calls, [("/api/vibes", {"next": True})])

    def test_a_fader_chooses_the_dwell_time_in_steps(self):
        for value, seconds in ((0, 15), (8, 15), (9, 30), (64, 240), (120, 3600), (127, 3600)):
            self.t[0] += 1
            self.m.message("nano", ("cc", 0, 7, value))
            self.assertEqual(self.rec.calls[-1], ("/api/vibes", {"dwell": seconds}), value)
        self.assertEqual(sorted(midi.VIBES_DWELLS), list(midi.VIBES_DWELLS))
        self.assertTrue(all(10 <= s <= 3600 for s in midi.VIBES_DWELLS))

    def test_the_actions_validate_like_the_others(self):
        self.assertEqual(entry(action="vibes_next")["action"], "vibes_next")
        with self.assertRaises(midi.MidiError):
            entry(kind="program", action="vibes_dwell")              # a program change cannot drive a level
        self.assertTrue(all(e["action"] not in ("vibes", "vibes_next", "vibes_dwell") for e in midi.builtin_map()))   # learn only


class DmxMapTest(unittest.TestCase):
    def setUp(self):
        self.rec, self.t = Recorder(), [100.0]
        self.m = DmxMapper(self.rec, start=1, clock=lambda: self.t[0])

    def frame(self, ninth, eight=(0,) * 8):
        self.t[0] += 0.1
        return self.m.frame(bytes(list(eight) + ([] if ninth is None else [ninth])))

    def test_zones(self):
        self.assertEqual([vibes_of(v) for v in (0, 49, 50, 99, 100, 149, 150, 199, 200, 255)],
                         [None, None, "stop", "stop", "start", "start", "next", "next", None, None])

    def test_the_first_frame_is_a_baseline_so_a_console_at_rest_starts_nothing(self):
        self.assertEqual(self.frame(120), 0)                          # sitting in the start zone already
        self.assertEqual(self.frame(120), 0)
        self.assertEqual(self.frame(130), 0)                          # a move inside the same zone
        self.assertEqual(self.rec.calls, [])

    def test_each_action_fires_once_on_entering_its_zone(self):
        self.frame(0)
        self.frame(100)
        self.frame(110)
        self.frame(149)
        self.assertEqual(self.rec.calls, [("/api/vibes", {"on": True})])
        self.frame(150)
        self.frame(160)
        self.frame(60)
        self.frame(0)
        self.frame(255)                                               # the top zone does nothing
        self.assertEqual(self.rec.calls[1:], [("/api/vibes", {"next": True}), ("/api/vibes", {"on": False})])
        self.frame(0)
        self.frame(175)
        self.frame(0)
        self.frame(175)                                               # next again needs a move out and back
        self.assertEqual(self.rec.calls[3:], [("/api/vibes", {"next": True})] * 2)

    def test_a_console_that_sends_eight_channels_works_as_before(self):
        self.frame(None)
        self.assertEqual(self.frame(None, (0, 0, 0, 0, 0, 255, 0, 0)), 1)
        self.assertEqual(self.rec.calls, [("/api/blackout", {"on": True})])
        self.assertEqual(len(self.m.seen), 8)

    def test_a_ninth_channel_that_appears_later_is_a_baseline_first(self):
        self.frame(None)
        self.assertEqual(self.frame(120), 0)                          # appears in the start zone: nothing
        self.assertEqual(self.frame(160), 1)
        self.assertEqual(self.rec.calls, [("/api/vibes", {"next": True})])
        self.frame(None)                                              # gone again, then back
        self.assertEqual(self.frame(120), 0)

    def test_a_signal_that_comes_back_after_a_gap_is_a_new_baseline(self):
        self.frame(0)
        self.t[0] += 10
        self.assertEqual(self.frame(120), 0)
        self.assertEqual(self.rec.calls, [])

    def test_the_other_channels_are_where_they_were(self):
        self.frame(0)
        self.frame(0, (128, 0, 0, 0, 0, 0, 0, 0))
        self.frame(0, (128, 0, 0, 0, 0, 0, 0, 60))
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "opacity", "value": 50.2}), ("/api/control", {"action": "stop"})])


class ThroughTheApiTest(Base):
    """The real API and the real Vibes on a fake clock and a fake player."""

    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        self.logs = []
        self.vibes = self.api.vibes = V.Vibes(self.api, self.engine, clock=lambda: self.now[0], sleep=lambda s: None,
                                              rng=random.Random(4), thread=False, log=lambda *_: None)
        self.hub = MidiHub(self.api, self.settings, log=self.logs.append, lister=lambda: [], clock=lambda: self.now[0])
        self.settings.data["control"]["midi"].update(enabled=True, builtin=False, map=[
            entry(), entry(number=41, action="vibes_next"), entry(kind="cc", number=7, action="vibes_dwell")])
        self.dmx = DmxServer(self.api, {"enabled": True, "protocol": "artnet", "universe": 0, "start": 1, "allow": []},
                             host="127.0.0.1", log=self.logs.append, clock=lambda: self.now[0])

    def press(self, note):
        self.now[0] += 1
        self.hub.on_message("Mini", ("on", 0, note, 100))
        self.hub.on_message("Mini", ("off", 0, note, 0))

    def dmx_ninth(self, value):
        self.now[0] += 0.1
        return self.dmx.handle_packet(artnet(0, [0] * 8 + [value]), "10.0.0.7")

    def test_midi_starts_skips_retimes_and_stops_vibes(self):
        self.press(40)
        self.assertTrue(self.vibes.running)
        self.assertTrue(self.vibes.tick())
        first = self.vibes.current
        self.press(41)
        self.assertTrue(self.vibes.tick())
        self.assertNotEqual(self.vibes.current, first)
        self.now[0] += 1
        self.hub.on_message("nano", ("cc", 0, 7, 9))                  # dwell 30 s
        self.assertEqual(self.engine.config()["dwell"], 30)
        self.assertEqual(self.vibes.status()["next_in"], 29)          # the shader on screen follows the new time
        self.now[0] += 29
        self.assertTrue(self.vibes.tick())
        saves = []
        self.engine._save = lambda cfg: saves.append(cfg)
        for _ in range(5):
            self.now[0] += 1
            self.hub.on_message("nano", ("cc", 0, 7, 10))             # the same step again: nothing is written
        self.assertEqual(saves, [])
        self.press(40)
        self.assertFalse(self.vibes.running)
        self.assertEqual(self.player.calls[-1], ("clear",))

    def test_midi_is_a_presenter_and_cannot_reach_the_full_access_calls(self):
        self.assertEqual(self.api.handle("POST", "/api/vibes", {"dwell": 60}, midi.MIDI_DEVICE, "midi")[0], 200)
        self.assertEqual(self.api.handle("POST", "/api/shaders", {"action": "config", "dwell": 60}, midi.MIDI_DEVICE, "midi")[0], 403)
        for bad in (5, 99999, "60", True, float("nan")):
            self.assertEqual(self.api.handle("POST", "/api/vibes", {"dwell": bad}, midi.MIDI_DEVICE, "midi")[0], 400, bad)

    def test_dmx_starts_skips_and_stops_vibes(self):
        self.assertEqual(self.dmx_ninth(0), 0)
        self.assertEqual(self.dmx_ninth(120), 1)
        self.assertTrue(self.vibes.running)
        self.vibes.tick()
        first = self.vibes.current
        self.assertEqual(self.dmx_ninth(175), 1)
        self.assertTrue(self.vibes.tick())
        self.assertNotEqual(self.vibes.current, first)
        self.assertEqual(self.dmx_ninth(60), 1)
        self.assertFalse(self.vibes.running)
        self.assertEqual(self.dmx_ninth(175), 0)                      # next with nothing running: refused, not applied
        self.assertFalse(self.vibes.running)

    def test_a_console_at_rest_in_the_start_zone_never_starts_vibes(self):
        for _ in range(20):
            self.assertEqual(self.dmx_ninth(120), 0)
        self.assertFalse(self.vibes.running)

    def test_with_the_module_off_nothing_happens_and_the_log_says_so_once(self):
        self.api.registry.set_enabled("shaders", False)
        for _ in range(6):
            self.press(40)
            self.press(41)
            self.now[0] += 60
        self.dmx_ninth(0)
        for v in (120, 0, 175, 0, 60, 0, 120):
            self.dmx_ninth(v)
            self.now[0] += 60
        self.assertFalse(self.vibes.running)
        self.assertEqual(self.player.calls, [])
        self.assertEqual(len([m for m in self.logs if m.startswith("midi:") and "module is off" in m]), 1, self.logs)
        self.assertEqual(len([m for m in self.logs if m.startswith("dmx:") and "module is off" in m]), 1, self.logs)
        self.assertEqual(len(self.logs), 2)
        self.api.registry.set_enabled("shaders", True)                # and it works again when the module is on
        self.press(40)
        self.assertTrue(self.vibes.running)

    def test_the_command_caps_cover_vibes(self):
        self.dmx_ninth(0)
        done = 0
        for i in range(400):                                          # time stands still: only the burst allowance
            done += self.dmx.handle_packet(artnet(0, [0] * 8 + [120 if i % 2 == 0 else 0]), "10.0.0.7")
        self.assertLessEqual(done, 51)


if __name__ == "__main__":
    unittest.main()
