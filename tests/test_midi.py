# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import threading
import time
import unittest

from pvj import midi
from pvj.midi import MidiHub, MidiInput, MidiMapper
from pvj.settings import Settings
from tests.test_server import ServerBase


class Recorder:
    def __init__(self):
        self.calls = []
        self.ok = True

    def __call__(self, path, body):
        self.calls.append((path, body))
        return self.ok


def entry(**kw):
    e = {"source": "*", "kind": "cc", "channel": 0, "number": 20, "action": "opacity"}
    e.update(kw)
    return midi.validate_entry(e)


class BuiltinMapTest(unittest.TestCase):
    def setUp(self):
        self.rec, self.t = Recorder(), [100.0]
        self.m = MidiMapper(self.rec, midi.builtin_map(), {"blackout": False}, clock=lambda: self.t[0])

    def test_pads_functions_levels_and_program_change(self):
        for note in (36, 47, 48, 71, 35, 72, 73, 74, 75, 76, 77):
            self.t[0] += 1
            self.m.message("any", ("on", 0, note, 100))
            self.m.message("any", ("off", 0, note, 0))                         # release: must not fire again
        self.t[0] += 1
        self.m.message("any", ("program", 0, 14, 0))
        self.assertEqual(self.rec.calls, [
            ("/api/play", {"pad": [0, 0]}), ("/api/play", {"pad": [0, 11]}), ("/api/play", {"pad": [1, 0]}), ("/api/play", {"pad": [2, 11]}),
            ("/api/control", {"action": "stop"}), ("/api/control", {"action": "pause"}), ("/api/blackout", {"on": True}),
            ("/api/fadeout", {"seconds": 2}), ("/api/control", {"action": "reset"}), ("/api/play", {"pad": [1, 2]})])

    def test_cc_levels_and_blackout_hold(self):
        for cc, v in ((20, 127), (21, 0), (22, 127), (23, 127), (24, 0), (25, 64), (99, 5)):
            self.t[0] += 1
            self.m.message("any", ("cc", 0, cc, v))
        self.assertEqual(self.rec.calls, [
            ("/api/control", {"action": "opacity", "value": 100.0}), ("/api/control", {"action": "size", "value": 25.0}),    # the bottom of a size control is 25 percent (D75)
            ("/api/control", {"action": "position", "value": 100.0}), ("/api/control", {"action": "speed", "value": 2.0}),
            ("/api/control", {"action": "volume", "value": 0.0}), ("/api/blackout", {"on": True})])


class LearnedMapTest(unittest.TestCase):
    def setUp(self):
        self.rec, self.t = Recorder(), [100.0]

    def mapper(self, *entries):
        return MidiMapper(self.rec, list(entries), {"blackout": False}, clock=lambda: self.t[0])

    def test_a_control_is_matched_by_controller_and_channel(self):
        m = self.mapper(entry(source="nanoKONTROL2", number=0, channel=3))
        m.message("Mix", ("cc", 2, 0, 127))                 # another controller
        m.message("nanoKONTROL2", ("cc", 0, 0, 127))        # right controller, wrong channel
        self.assertEqual(self.rec.calls, [])
        m.message("nanoKONTROL2", ("cc", 2, 0, 127))
        self.assertEqual(len(self.rec.calls), 1)

    def test_any_controller_and_any_channel(self):
        m = self.mapper(entry(number=7))
        m.message("Mini", ("cc", 9, 7, 127))
        m.message("Mix", ("cc", 0, 7, 0))
        self.assertEqual(len(self.rec.calls), 2)

    def test_a_trigger_fires_once_per_press_not_on_release_or_repeat(self):
        m = self.mapper(entry(kind="note", number=60, action="pad", bank=0, index=4))
        for msg in (("on", 0, 60, 127), ("on", 0, 60, 127), ("off", 0, 60, 0), ("on", 0, 60, 90)):
            self.t[0] += 1
            m.message("Mini", msg)
        self.assertEqual(self.rec.calls, [("/api/play", {"pad": [0, 4]})] * 2)   # the repeated press with no release in between did not fire

    def test_a_bouncing_button_fires_once(self):
        m = self.mapper(entry(kind="note", number=60, action="stop"))
        for _ in range(6):
            m.message("Mini", ("on", 0, 60, 127))
            self.t[0] += 0.02                                    # 20 ms: contact bounce, faster than any hand
            m.message("Mini", ("off", 0, 60, 0))
            self.t[0] += 0.02
        self.assertEqual(len(self.rec.calls), 1)
        self.t[0] += 0.5
        m.message("Mini", ("on", 0, 60, 127))
        self.assertEqual(len(self.rec.calls), 2)

    def test_a_cc_button_fires_on_the_rising_edge_only(self):
        m = self.mapper(entry(number=41, action="stop"))
        for v in (0, 127, 127, 127, 0, 127):
            self.t[0] += 1
            m.message("nano", ("cc", 0, 41, v))
        self.assertEqual(len(self.rec.calls), 2)

    def test_a_learned_mapping_replaces_the_builtin_one_for_that_control(self):
        # note 36 is pad 1 in the built-in map; the user made it "stop": pressing it must NOT also play pad 1
        m = self.mapper(*([entry(kind="note", number=36, action="stop")] + midi.builtin_map()))
        m.message("Mini", ("on", 0, 36, 127))
        self.assertEqual(self.rec.calls, [("/api/control", {"action": "stop"})])
        m.message("Mini", ("on", 0, 37, 127))                                        # a control the user has not mapped still uses the built-in one
        self.assertEqual(self.rec.calls[-1], ("/api/play", {"pad": [0, 1]}))

    def test_a_mapping_for_another_controller_does_not_switch_off_the_builtin_one(self):
        m = self.mapper(*([entry(source="nano", kind="note", number=36, action="stop")] + midi.builtin_map()))
        m.message("Mini", ("on", 0, 36, 127))
        self.assertEqual(self.rec.calls, [("/api/play", {"pad": [0, 0]})])

    def test_a_fader_sweep_is_thinned_but_the_last_value_lands(self):
        m = self.mapper(entry(number=0))
        m.message("nano", ("cc", 0, 0, 10))
        for v in range(11, 60):
            self.t[0] += 0.001
            m.message("nano", ("cc", 0, 0, v))
        self.assertEqual(len(self.rec.calls), 1)
        self.t[0] += 0.1
        self.assertEqual(m.flush(), 1)
        self.assertEqual(self.rec.calls[-1], ("/api/control", {"action": "opacity", "value": round(59 / 127 * 100, 2)}))

    def test_two_faders_do_not_thin_each_other(self):
        m = self.mapper(entry(number=0), entry(number=1, action="volume"))
        m.message("nano", ("cc", 0, 0, 50))
        m.message("nano", ("cc", 0, 1, 50))
        self.assertEqual(len(self.rec.calls), 2)


class ValidateTest(unittest.TestCase):
    def test_good(self):
        e = midi.validate_entry({"kind": "note", "number": 60, "action": "pad", "bank": 1, "index": 3, "source": "Launchpad Mini"})
        self.assertEqual((e["bank"], e["index"], e["channel"], len(e["id"])), (1, 3, 0, 8))
        self.assertEqual(midi.validate_entry({"kind": "cc", "number": 5, "action": "speed", "channel": 16})["channel"], 16)

    def test_bad(self):
        base = {"kind": "cc", "number": 5, "action": "opacity"}
        bad = [[], None, dict(base, kind="sysex"), dict(base, action="shutdown"), dict(base, action="reboot"), dict(base, number=128),
               dict(base, number=-1), dict(base, number=True), dict(base, number="5"), dict(base, channel=17), dict(base, channel=True),
               dict(base, source="a/b"), dict(base, source="x" * 33), dict(base, source="evil\n"), dict(base, source=5),
               dict(base, kind="program"), {"kind": "note", "number": 1, "action": "pad"}, {"kind": "note", "number": 1, "action": "pad", "bank": 3, "index": 0},
               {"kind": "note", "number": 1, "action": "pad", "bank": 0, "index": 12}, {"kind": "note", "number": 1, "action": "pad", "bank": True, "index": 0}]
        for b in bad:
            with self.assertRaises(midi.MidiError, msg=str(b)[:70]):
                midi.validate_entry(b)

    def test_a_new_mapping_replaces_the_old_one_for_the_same_control(self):
        m = midi.add_entry([], {"kind": "cc", "number": 5, "action": "opacity", "source": "nano"})
        m = midi.add_entry(m, {"kind": "cc", "number": 5, "action": "volume", "source": "nano"})
        m = midi.add_entry(m, {"kind": "cc", "number": 5, "action": "size", "source": "Mix"})
        self.assertEqual([(e["source"], e["action"]) for e in m], [("nano", "volume"), ("Mix", "size")])

    def test_limit(self):
        m = []
        for i in range(midi.MAX_MAP):
            m = midi.add_entry(m, {"kind": "cc", "number": i % 128, "channel": i // 128 + 1, "action": "opacity"})
        with self.assertRaises(midi.MidiError):
            midi.add_entry(m, {"kind": "note", "number": 1, "action": "stop"})

    def test_switches(self):
        cur = {"enabled": False, "builtin": True, "map": []}
        self.assertEqual(midi.validate({"enabled": True, "builtin": False}, cur), {"enabled": True, "builtin": False, "map": []})
        for bad in ({"enabled": 1}, {"builtin": "no"}):
            with self.assertRaises(midi.MidiError):
                midi.validate(bad, cur)


class SourceNameTest(unittest.TestCase):
    def test_uses_the_alsa_card_id_which_stays_put_when_card_numbers_change(self):
        d = tempfile.mkdtemp()
        for n, name in ((1, "nanoKONTROL2"), (2, "Mix"), (3, "bad/name")):
            os.makedirs(os.path.join(d, "card%d" % n))
            with open(os.path.join(d, "card%d" % n, "id"), "w") as f:
                f.write(name + "\n")
        self.assertEqual(midi.source_name("/dev/snd/midiC1D0", d), "nanoKONTROL2")
        self.assertEqual(midi.source_name("/dev/snd/midiC2D0", d), "Mix")
        self.assertEqual(midi.source_name("/dev/snd/midiC3D0", d), "midiC3D0")     # not a safe name: fall back to the file name
        self.assertEqual(midi.source_name("/dev/snd/midiC9D0", d), "midiC9D0")     # no such card


class HubTest(ServerBase):
    """The hub with pipes standing in for controllers."""

    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.call("POST", "/api/modules/control-midi", {"enabled": True}, token=self.full)
        self.pipes = {}
        self.present = []
        self.opened = []
        names = {"/dev/snd/midiC1D0": "nano", "/dev/snd/midiC2D0": "Mix", "/dev/snd/midiC3D0": "Mini"}

        def open_fn(path):
            self.opened.append(path)
            r, w = os.pipe()
            self.pipes[path] = (r, w)
            return r
        self.hub = MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=open_fn, lister=lambda: list(self.present),
                           namer=lambda p: names[p], scan_interval=0.05, profiles=[])    # no profiles: the map by itself (see test_controllers.py)
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)

    def enable(self, **extra):
        self.settings.data["control"]["midi"].update(enabled=True, **extra)
        self.hub.apply()

    def send(self, path, data):
        os.write(self.pipes[path][1], bytes(data))

    def wait(self, cond, timeout=4):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.02)
        self.assertTrue(cond(), "condition not met")

    def test_reads_every_controller_at_once_and_notices_hotplug(self):
        self.present = ["/dev/snd/midiC1D0", "/dev/snd/midiC2D0"]
        self.enable()
        self.wait(lambda: len(self.pipes) == 2)
        self.send("/dev/snd/midiC1D0", [0x90, 74, 100])            # blackout toggle from one controller
        self.wait(lambda: self.api.mix["blackout"])
        self.send("/dev/snd/midiC2D0", [0x90, 74, 100])            # and the other
        self.wait(lambda: not self.api.mix["blackout"])
        self.present.append("/dev/snd/midiC3D0")                    # a third is plugged in later
        self.wait(lambda: "/dev/snd/midiC3D0" in self.pipes)
        st = self.hub.status()
        self.assertEqual(sorted(d["name"] for d in st["devices"]), ["Mini", "Mix", "nano"])
        self.assertTrue(all(d["connected"] for d in st["devices"]))

    def test_an_unplugged_controller_is_dropped_and_comes_back(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        first = self.pipes.pop("/dev/snd/midiC1D0")
        self.present = []
        os.close(first[1])                                          # the stream ends: unplugged
        self.wait(lambda: not self.hub.inputs)
        self.present = ["/dev/snd/midiC1D0"]
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.send("/dev/snd/midiC1D0", [0x90, 74, 100])
        self.wait(lambda: self.api.mix["blackout"])

    def test_a_button_held_while_midi_goes_off_is_not_still_held_when_midi_is_back(self):
        # The release arrives while MIDI is off and is dropped (as it must be). The hub used to keep the button as
        # pressed, so its first press after MIDI came back did nothing.
        now = [500.0]
        self.hub.mapper._clock = lambda: now[0]
        self.hub.on_message("nano", ("on", 0, 74, 100))                      # blackout, pressed and held
        self.assertTrue(self.api.mix["blackout"])
        self.settings.data["control"]["midi"]["enabled"] = False
        self.hub.apply()
        self.hub.on_message("nano", ("off", 0, 74, 0))                       # let go while nobody listens
        self.enable()
        now[0] += 5
        self.hub.on_message("nano", ("on", 0, 74, 100))
        self.assertFalse(self.api.mix["blackout"], "the first press after MIDI came back was taken for a button still held")

    def test_a_button_held_while_midi_goes_off_works_again_through_its_reader(self):
        # the same through a real reader thread: only the press is ever written to the pipe
        now = [500.0]
        self.hub.mapper._clock = lambda: now[0]
        path = "/dev/snd/midiC1D0"
        self.present = [path]
        self.enable()
        self.wait(lambda: path in self.pipes)
        self.send(path, [0x90, 74, 100])
        self.wait(lambda: self.api.mix["blackout"])
        self.settings.data["control"]["midi"]["enabled"] = False
        self.hub.apply()
        old = self.pipes.pop(path)
        self.enable()
        self.wait(lambda: path in self.pipes)
        now[0] += 5
        self.send(path, [0x90, 74, 100])
        self.wait(lambda: not self.api.mix["blackout"])
        for fd in old:
            try:
                os.close(fd)
            except OSError:
                pass

    def test_learn_captures_the_next_control_and_executes_nothing_meanwhile(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.hub.start_learn()
        self.send("/dev/snd/midiC1D0", [0x90, 74, 100])            # would toggle blackout, but we are learning
        self.wait(lambda: self.hub.captured is not None)
        self.assertEqual(self.hub.captured, {"source": "nano", "kind": "note", "channel": 1, "number": 74})
        self.assertFalse(self.api.mix["blackout"])
        st = self.hub.status()
        self.assertEqual((st["learn"]["active"], st["learn"]["captured"]["number"]), (False, 74))

    def test_learn_ignores_releases_and_times_out(self):
        clock = [1000.0]
        self.hub._clock = lambda: clock[0]
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.hub.start_learn()
        self.send("/dev/snd/midiC1D0", [0x80, 60, 0])              # a note-off is not a control being pressed
        time.sleep(0.3)
        self.assertIsNone(self.hub.captured)
        clock[0] += midi.LEARN_SECONDS + 1
        self.assertFalse(self.hub.status()["learn"]["active"])

    def test_a_hand_edited_map_cannot_break_the_reader(self):
        self.settings.data["control"]["midi"]["map"] = [
            {"id": "abcd1234", "source": "*", "kind": "cc", "channel": 0, "number": 0, "action": "opacity"},
            {"id": "x"}, {"kind": "cc"}, "junk", None, {"id": "abcd1235", "source": "*", "kind": "cc", "channel": 0, "number": 1, "action": "shutdown"}]
        self.assertEqual([e["id"] for e in self.hub.entries() if not e.get("builtin")], ["abcd1234"])
        self.hub.on_message("nano", ("cc", 0, 0, 100))                       # must not raise

    def test_the_control_just_learned_is_ignored_while_it_settles(self):
        clock = [50.0]
        self.hub._clock = lambda: clock[0]
        self.settings.data["control"]["midi"]["map"] = [midi.validate_entry({"kind": "cc", "number": 5, "action": "opacity", "source": "nano"})]
        self.hub.start_learn()
        self.hub.on_message("nano", ("cc", 0, 5, 40))                        # captured
        self.assertEqual(self.hub.captured["number"], 5)
        self.hub.on_message("nano", ("cc", 0, 5, 41))                        # the fader is still moving: no old mapping runs
        self.assertEqual(self.player.calls, [])
        clock[0] += midi.LEARN_QUIET + 0.1
        self.hub.on_message("nano", ("cc", 0, 5, 90))
        self.assertTrue(self.player.calls)

    def test_calls_into_the_player_are_made_without_the_hub_lock(self):
        held = []
        real = self.api.handle

        def handle(*a, **k):
            held.append(self.hub._lock._is_owned())                          # RLock: is this thread holding it?
            return real(*a, **k)
        self.api.handle = handle
        self.hub.on_message("Mini", ("on", 0, 74, 100))
        self.assertEqual(held, [False])

    def test_the_hub_stops_everything_when_switched_off(self):
        self.present = ["/dev/snd/midiC1D0"]
        self.enable()
        self.wait(lambda: "/dev/snd/midiC1D0" in self.pipes)
        self.settings.data["control"]["midi"]["enabled"] = False
        started = time.monotonic()
        self.hub.apply()
        self.assertLess(time.monotonic() - started, 1.5, "switching off must not wait for a timeout")
        self.assertEqual(self.hub.inputs, {})
        self.assertEqual(sum(t.name in ("midi", "midi-scan") and t.is_alive() for t in threading.enumerate()), 0)

    def test_a_faulty_controller_cannot_flood_the_player(self):
        self.hub._clock = lambda: 5.0
        self.hub.calls = midi.RateLimiter(lambda: 5.0, rate=midi.MAX_CALLS_PER_SECOND, burst=midi.MAX_CALLS_PER_SECOND)
        for _ in range(400):
            self.hub.on_message("Mini", ("on", 0, 74, 100))      # 400 presses of the blackout toggle
            self.hub.on_message("Mini", ("off", 0, 74, 0))
        self.assertTrue(0 < len(self.player.calls) <= midi.MAX_CALLS_PER_SECOND, len(self.player.calls))

    def test_only_real_midi_device_files_are_ever_opened(self):
        with self.assertRaises(OSError):
            MidiInput._open_device("/etc/passwd")
        with self.assertRaises(OSError):
            MidiInput._open_device("/dev/snd/midiC٣D٣")
        self.assertIsNone(midi.DEVICE_PATH.fullmatch("/dev/snd/midiC1D0\n"))

    def test_a_non_oserror_from_open_does_not_kill_the_hub(self):
        def bad_open(path):
            raise ValueError("embedded null byte")
        hub = MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=bad_open, lister=lambda: ["/dev/snd/midiC1D0"],
                      namer=lambda p: "x", scan_interval=0.05)
        self.addCleanup(hub.stop)
        self.settings.data["control"]["midi"]["enabled"] = True
        hub.apply()
        time.sleep(0.4)
        self.assertTrue(hub._scanner.is_alive())


class MidiApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.hub = MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=lambda p: (_ for _ in ()).throw(OSError()),
                           lister=lambda: [], scan_interval=0.05)
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_gated_by_the_module_and_by_the_switch(self):
        self.assertEqual(self.call("GET", "/api/midi", token=self.full)[0], 409)
        self.assertEqual(self.post("/api/midi/map", {"clear": True})[0], 409)
        self.post("/api/modules/control-midi", {"enabled": True})
        self.assertEqual(self.post("/api/midi/learn", {"start": True})[0], 409)          # MIDI itself is still off
        self.assertEqual(self.post("/api/midi", {"enabled": True})[0], 200)
        self.assertEqual(self.post("/api/midi/learn", {"start": True})[0], 200)
        self.assertTrue(self.hub.status()["learn"]["active"])
        self.assertEqual(self.post("/api/midi/learn", {"start": False})[1]["learn"]["active"], False)
        self.assertEqual(self.post("/api/midi/learn", {"start": "yes"})[0], 400)

    def test_add_list_remove_and_persist(self):
        self.post("/api/modules/control-midi", {"enabled": True})
        st, body, _ = self.post("/api/midi/map", {"add": {"source": "nano", "kind": "cc", "number": 0, "action": "opacity"}})
        self.assertEqual(st, 200)
        self.assertEqual([(e["source"], e["number"], e["action"]) for e in body["map"]], [("nano", 0, "opacity")])
        eid = body["map"][0]["id"]
        self.assertEqual(Settings(self.settings.path).load()["control"]["midi"]["map"][0]["id"], eid)
        self.assertEqual(self.post("/api/midi/map", {"remove": "00000000"})[0], 404)
        self.assertEqual(self.post("/api/midi/map", {"remove": eid})[1]["map"], [])
        self.assertEqual(self.post("/api/midi/map", {"nonsense": 1})[0], 400)
        self.assertEqual(self.post("/api/midi/map", {"add": {"kind": "cc", "number": 1, "action": "shutdown"}})[0], 400)
        self.post("/api/midi/map", {"add": {"kind": "cc", "number": 2, "action": "stop"}})
        self.assertEqual(self.post("/api/midi/map", {"clear": True})[1]["map"], [])

    def test_roles(self):
        self.post("/api/modules/control-midi", {"enabled": True})
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        for path, body in (("/api/midi", {"enabled": False}), ("/api/midi/learn", {"start": True}), ("/api/midi/map", {"clear": True})):
            self.assertEqual(self.post(path, body, token=live)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/midi", token=live)[0], 200)               # a presenter may look (the drawn layout); see test_controllers.py
        self.assertEqual(self.call("GET", "/api/midi")[0], 401)


if __name__ == "__main__":
    unittest.main()
