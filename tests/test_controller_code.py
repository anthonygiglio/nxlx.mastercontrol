# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A pairing code on the box's display, asked for by a hold on a MIDI controller (D61).

Every rule of pvj/controllercode.py has a test here: off unless switched on, the full-access kind behind its own
switch, a hold that ends with letting go and nothing else, once, two minutes, a few an hour, the PIN's lockout, the
display only, no way in but a MIDI device on the box, and what a full-access device sees and can end.
No controller, no Pi and no screen were used: a fake clock, a fake player and messages handed to the hub."""
import copy
import inspect
import json
import os
import re
import tempfile
import types
import unittest

from pvj import auth as auth_mod, boxcare, controllercode, dmx, midi, osc, pinscreen, room, scheduler, settings as settings_mod, support, sync
from pvj.auth import Auth, AuthError
from pvj.settings import Settings
from tests.test_pinscreen import Api as PinApi
from tests.test_server import ServerBase

NOTE_JOIN, NOTE_OWNER, CC_JOIN = 7, 9, 30
PAD = "/dev/snd/midiC3D0"


def switch(settings, enabled=True, owner=False):
    settings.data["controller_code"] = {"enabled": enabled, "owner": owner}


class AuthTest(unittest.TestCase):
    """The code itself: auth.py keeps it, apart from the join codes."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t = [1000.0]
        self.a = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)

    def digits(self):
        return self.a.controller_digits()[1]

    def test_it_is_off_until_switched_on_and_the_owner_kind_has_its_own_switch(self):
        self.assertEqual(self.settings.data["controller_code"], {"enabled": False, "owner": False})
        for kind in ("join", "owner"):
            with self.assertRaises(auth_mod.ControllerOff):
                self.a.create_controller_code(kind)
        switch(self.settings)
        self.a.create_controller_code("join")
        with self.assertRaises(auth_mod.ControllerOff):
            self.a.create_controller_code("owner")
        self.assertEqual(self.a.controller_status()["kind"], "join")          # and the refusal left the join code alone
        switch(self.settings, owner=True)
        self.a.create_controller_code("owner")
        self.assertEqual(self.a.controller_status()["kind"], "owner")
        with self.assertRaises(AuthError):
            self.a.create_controller_code("full")

    def test_a_setting_that_is_not_exactly_true_counts_as_off(self):
        for junk in (None, [], "yes", {"enabled": 1, "owner": 1}, {"enabled": "true"}, {"owner": True}):
            self.settings.data["controller_code"] = junk
            with self.assertRaises(auth_mod.ControllerOff, msg=junk):
                self.a.create_controller_code("join")
        del self.settings.data["controller_code"]                             # a file from before the setting existed
        with self.assertRaises(auth_mod.ControllerOff):
            self.a.create_controller_code("join")

    def test_the_join_kind_pairs_one_presenter_and_the_owner_kind_one_full_device(self):
        switch(self.settings, owner=True)
        self.a.create_controller_code("join")
        code = self.digits()
        self.assertTrue(re.fullmatch(r"[0-9]{6}", code))
        _, dev = self.a.pair(code, "phone", "c1")
        self.assertEqual(dev["role"], "live")
        self.assertEqual(self.settings.data["devices"][-1]["via"], "controller")
        with self.assertRaises(AuthError):                                    # once only
            self.a.pair(code, "second", "c2")
        self.assertIsNone(self.a.controller_digits())
        last = self.a.controller_status()["last"]
        self.assertEqual((last["kind"], last["how"], last["device"]), ("join", "used", "phone"))
        self.a.create_controller_code("owner")
        self.assertEqual(self.a.pair(self.digits(), "laptop", "c3")[1]["role"], "full")

    def test_it_works_for_two_minutes(self):
        switch(self.settings)
        self.a.create_controller_code("join")
        code = self.digits()
        self.t[0] += auth_mod.CONTROLLER_SECONDS - 1
        self.assertEqual(self.a.controller_status()["seconds_left"], 1)
        self.t[0] += 1
        self.assertIsNone(self.a.controller_digits())
        with self.assertRaises(AuthError):
            self.a.pair(code, "late", "c")
        self.assertEqual(self.a.controller_status()["last"]["how"], "expired")
        self.assertEqual(auth_mod.CONTROLLER_SECONDS, 120)

    def test_a_few_an_hour_and_no_more(self):
        switch(self.settings, owner=True)
        for n in range(auth_mod.CONTROLLER_PER_HOUR):
            self.a.create_controller_code("join" if n % 2 else "owner")       # both kinds count together
            self.t[0] += 60
        with self.assertRaises(auth_mod.JoinLimit) as e:
            self.a.create_controller_code("join")
        self.assertTrue(0 < e.exception.retry_after <= 3600)
        self.assertEqual(self.a.controller_status()["made_this_hour"], auth_mod.CONTROLLER_PER_HOUR)
        self.a.cancel_controller_code()
        with self.assertRaises(auth_mod.JoinLimit):                           # ending one gives nothing back
            self.a.create_controller_code("join")
        self.t[0] += 3600
        self.a.create_controller_code("join")
        self.assertLessEqual(auth_mod.CONTROLLER_PER_HOUR, 6)

    def test_guesses_meet_the_same_lockout_as_wrong_pins_and_the_right_code_buys_none(self):
        switch(self.settings)
        self.a.create_controller_code("join")
        code = self.digits()
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        for _ in range(auth_mod.PER_CLIENT_FAILS):
            with self.assertRaises(AuthError):
                self.a.pair(wrong, "x", "guesser")
        with self.assertRaises(AuthError) as e:                               # locked out: even the right code waits
            self.a.pair(code, "x", "guesser")
        self.assertTrue(e.exception.retry_after)
        self.assertIsNotNone(self.a.controller_digits())                      # and was not used up by that
        for n in range(auth_mod.PER_CLIENT_FAILS - 1):                        # another client: four wrong, then the code
            with self.assertRaises(AuthError):
                self.a.pair(wrong, "x", "other")
        self.a.pair(code, "x", "other")
        with self.assertRaises(AuthError) as e:                               # the fifth wrong one still locks: the count was kept
            self.a.pair("0000", "x", "other")
        with self.assertRaises(AuthError) as e:
            self.a.pair("0000", "x", "other")
        self.assertTrue(e.exception.retry_after)

    def test_the_global_lockout_covers_it_too(self):
        switch(self.settings)
        self.a.create_controller_code("join")
        code = self.digits()
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        for n in range(auth_mod.GLOBAL_FAILS):
            with self.assertRaises(AuthError):
                self.a.pair(wrong, "x", "client%d" % n)
        with self.assertRaises(AuthError) as e:
            self.a.pair(code, "x", "someone new")
        self.assertTrue(e.exception.retry_after)

    def test_switching_the_setting_off_ends_the_code_without_anyone_cancelling_it(self):
        switch(self.settings, owner=True)
        self.a.create_controller_code("owner")
        code = self.digits()
        switch(self.settings, owner=False)                                    # only the owner kind goes off
        with self.assertRaises(AuthError):
            self.a.pair(code, "x", "c")
        self.assertEqual(self.a.controller_status()["last"]["how"], "switched off")
        self.a.create_controller_code("join")
        code = self.digits()
        switch(self.settings, enabled=False)
        with self.assertRaises(AuthError):
            self.a.pair(code, "x", "c2")
        self.assertFalse(self.a.controller_status()["active"])

    def test_it_is_apart_from_the_join_codes(self):
        switch(self.settings, owner=True)
        guest, presenter = self.a.create_join("view"), self.a.create_join("live")
        self.a.create_controller_code("join")
        code = self.digits()
        self.assertEqual(sorted(j["code"] for j in self.a.list_joins()), sorted([guest, presenter]))     # not listed, and nothing replaced
        self.assertNotIn(code, (guest, presenter))
        self.a.create_controller_code("owner")                                # a new one takes the place of the old
        with self.assertRaises(AuthError):
            self.a.pair(code, "x", "c")
        self.assertEqual(self.a.pair(guest, "g", "c2")[1]["role"], "view")
        self.assertEqual(self.a.pair(presenter, "p", "c3")[1]["role"], "live")
        self.a.cancel_join(None)                                              # and ending every join code leaves it
        self.assertTrue(self.a.controller_status()["active"])

    def test_the_status_never_holds_the_digits(self):
        switch(self.settings)
        self.a.create_controller_code("join")
        code = self.digits()
        self.assertNotIn(code, json.dumps(self.a.controller_status()))
        self.a.pair(code, "phone", "c")
        self.assertNotIn(code, json.dumps(self.a.controller_status()))
        self.assertNotIn(code, json.dumps(self.a.list_devices()))

    def test_no_code_when_the_list_of_devices_is_full(self):
        switch(self.settings)
        self.settings.data["devices"] = [{"id": "%08x" % n, "name": "g", "role": "live", "token_hash": "0" * 64, "created": 1}
                                         for n in range(auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED)]
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.create_controller_code("join")


class HoldTest(unittest.TestCase):
    """The gesture: held for 3 to 10 seconds and then let go. Nothing else asks for a code."""

    def setUp(self):
        self.t, self.asked, self.api_calls = [100.0], [], []
        entries = [midi.validate_entry({"kind": "note", "number": NOTE_JOIN, "action": "code_join"}),
                   midi.validate_entry({"kind": "note", "number": NOTE_OWNER, "action": "code_owner"}),
                   midi.validate_entry({"kind": "cc", "number": CC_JOIN, "action": "code_join"})]
        self.m = midi.MidiMapper(lambda path, body: self.api_calls.append(path) or True, entries, {}, clock=lambda: self.t[0])
        self.m.local = lambda source, body: self.asked.append((source, dict(body))) or True

    def down(self, note=NOTE_JOIN):
        self.m.message("Mini", ("on", 0, note, 127))

    def up(self, note=NOTE_JOIN, off=True):
        self.m.message("Mini", ("off", 0, note, 0) if off else ("on", 0, note, 0))

    def requests(self):
        return [b for _, b in self.asked if "kind" in b]

    def test_a_hold_of_three_seconds_that_ends_asks_once(self):
        self.down()
        self.assertEqual(self.asked, [("Mini", {"press": 100.0})])            # the press itself only says "pressed"
        self.t[0] += 3.0
        self.assertEqual(self.requests(), [])                                 # still held: nothing yet
        self.up()
        self.assertEqual(self.requests(), [{"kind": "join", "since": 100.0}])
        self.up()                                                             # a second release does nothing
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.api_calls, [])                                  # and none of it is a call into the API
        self.assertEqual((midi.HOLD_MIN, midi.HOLD_MAX), (3.0, 10.0))

    def test_each_action_asks_for_its_own_kind(self):
        self.down(NOTE_OWNER)
        self.t[0] += 4
        self.up(NOTE_OWNER, off=False)                                        # a note-on with velocity 0 is a release too
        self.assertEqual(self.requests(), [{"kind": "owner", "since": 100.0}])

    def test_a_tap_and_a_short_hold_ask_for_nothing(self):
        for seconds in (0.0, 0.1, 1.0, 2.9):
            self.down()
            self.t[0] += seconds
            self.up()
            self.t[0] += 1
        self.assertEqual(self.requests(), [])

    def test_a_stuck_note_never_asks(self):
        self.down()
        for _ in range(600):                                                  # ten minutes with no release, the note sent again and again
            self.t[0] += 1.0
            self.down()
        self.assertEqual(self.requests(), [])
        self.assertEqual(len(self.asked), 1)                                  # one press, not 601
        self.up()                                                             # let go at last: far too long
        self.assertEqual(self.requests(), [])

    def test_something_left_on_the_button_too_long_asks_for_nothing(self):
        self.down()
        self.t[0] += 10.1
        self.up()
        self.assertEqual(self.requests(), [])
        self.down()
        self.t[0] += 10.0                                                     # the upper edge still counts
        self.up()
        self.assertEqual(len(self.requests()), 1)

    def test_a_held_control_does_not_repeat(self):
        self.down()
        self.t[0] += 2
        self.down()                                                           # sent again while down: the hold is not started again
        self.t[0] += 2
        self.up()
        self.assertEqual(self.requests(), [{"kind": "join", "since": 100.0}])
        self.t[0] += 5
        self.up()
        self.assertEqual(len(self.requests()), 1)

    def test_a_button_that_sends_a_controller_number_is_held_the_same_way(self):
        self.m.message("nano", ("cc", 0, CC_JOIN, 127))
        self.t[0] += 3.5
        self.m.message("nano", ("cc", 0, CC_JOIN, 127))                       # sent again while down
        self.assertEqual(self.requests(), [])
        self.m.message("nano", ("cc", 0, CC_JOIN, 0))
        self.assertEqual(self.asked[-1], ("nano", {"kind": "join", "since": 100.0}))

    def test_a_controller_that_went_takes_its_hold_with_it(self):
        self.down()
        self.t[0] += 4
        self.m.forget("Mini")                                                 # unplugged, or MIDI switched off
        self.up()
        self.assertEqual(self.requests(), [])
        self.down()
        self.m.forget()
        self.t[0] += 4
        self.up()
        self.assertEqual(self.requests(), [])

    def test_a_program_change_and_a_fader_cannot_have_the_action(self):
        for action in ("code_join", "code_owner"):
            with self.assertRaises(midi.MidiError):
                midi.validate_entry({"kind": "program", "number": 1, "action": action})
            self.assertEqual(midi.ACTIONS[action][0], "hold")
            self.assertFalse(midi.guardable(action))
        with open(os.path.join(midi.PROFILE_DIR, "korg-nanokontrol2.json")) as f:
            raw = json.load(f)
        next(c for c in raw["controls"] if c["id"] == "fader8")["action"] = {"action": "code_join"}
        with self.assertRaises(midi.MidiError):
            midi.validate_profile(raw, "korg-nanokontrol2")

    def test_the_shipped_layouts(self):
        """Only the Launchpad Mini has a pad for it: the last pad beside bank C (pad 6.8) asks for a presenter code. No
        layout asks for a full access code, and the MIDI Mix's Solo (a shift that is held while playing) stays spare."""
        have = {}
        for p in midi.load_profiles(log=lambda *_: None):
            for c in p["controls"]:
                if c["action"] and c["action"]["action"] in controllercode.ACTION_KINDS:
                    have[(p["id"], c["id"])] = (c["action"]["action"], c["kind"], c["guard"])
        self.assertEqual(have, {("novation-launchpad-mini", "pad68"): ("code_join", "pad", False)})
        mix = next(p for p in midi.load_profiles(log=lambda *_: None) if p["id"] == "akai-midimix")
        self.assertIsNone(next(c for c in mix["controls"] if c["send"] == {"type": "note", "channel": 0, "number": 27})["action"])
        for action in controllercode.ACTION_KINDS:
            self.assertIsNone(midi.light_meaning({"action": action}))         # no light says anything about a code
        self.assertEqual({a: midi.ACTIONS[a][1] for a in controllercode.ACTION_KINDS}, controllercode.ACTION_KINDS)


class Recording:
    def __init__(self):
        self.shown = []

    def request(self, *cmd):
        self.shown.append(cmd)


class BoxBase(ServerBase):
    """A whole box: the API, the display and the MIDI hub, with one fake clock under the hub, the codes and the
    display. Messages are handed to the hub as its reader would hand them."""
    USB = {PAD: {"name": "Launchpad Mini", "usbid": "1235:0036", "readable": True}}

    def setUp(self):
        super().setUp()
        self.t, self.said = [5000.0], []
        clock = lambda: self.t[0]
        self.auth._clock = clock
        self.api.log = self.said.append
        self.api.controller_codes._clock = clock
        self.player.ipc = Recording()
        self.player.running = True
        self.screen = pinscreen.PinScreen(self.api, self.auth, log=self.said.append, hostname="box", clock=clock)
        self.api.pinscreen = self.screen
        self.cards = dict(self.USB)
        self.hub = midi.MidiHub(self.api, self.settings, log=self.said.append, lister=lambda: [], clock=clock,
                                describer=lambda p: self.cards[p], profiles=[])
        self.api.midi = self.hub
        self.addCleanup(self.hub.stop)
        self.plug(PAD, "Mini")
        self.settings.data["control"]["midi"]["map"] = [
            midi.validate_entry({"kind": "note", "number": NOTE_JOIN, "action": "code_join"}),
            midi.validate_entry({"kind": "note", "number": NOTE_OWNER, "action": "code_owner"})]
        self.full_token = self.pair("owner phone")[0]
        self.full = self.auth.authenticate(self.full_token)
        self.live = self.auth.invite("presenter", "live")[1]
        self.view = self.auth.invite("guest", "view")[1]

    def plug(self, path, source):
        self.hub.inputs[path] = types.SimpleNamespace(source=source, connected=True, messages=0, alive=True, halt=lambda: None, stop=lambda *a: None)

    def hold(self, note=NOTE_JOIN, seconds=3.5, source="Mini"):
        self.hub.on_message(source, ("on", 0, note, 127))
        self.t[0] += seconds
        self.hub.on_message(source, ("off", 0, note, 0))

    def tap(self, note=NOTE_JOIN, source="Mini"):
        self.hold(note, 0.1, source)

    def on(self, owner=False):
        st, out = self.api.handle("POST", "/api/access/controller", {"enabled": True, "owner": owner}, self.full, "10.0.0.5")
        self.assertEqual(st, 200, out)
        return out

    def state(self, device=None):
        st, out = self.api.handle("GET", "/api/access", {}, device or self.full, "10.0.0.5")
        self.assertEqual(st, 200, out)
        return out

    def text(self):
        return self.player.ipc.shown[-1][1] if self.player.ipc.shown else ""

    def digits(self):
        got = self.auth.controller_digits()
        return got[1] if got else None


class BoxTest(BoxBase):
    def test_off_by_default_a_hold_does_nothing_at_all(self):
        self.hold()
        self.hold(NOTE_OWNER)
        self.assertIsNone(self.digits())
        self.assertEqual(self.player.ipc.shown, [])                           # nothing was drawn, not even a notice
        self.assertEqual(self.state()["controller"]["status"]["made_this_hour"], 0)
        self.assertTrue(any("switched off" in line for line in self.said))

    def test_switched_on_a_hold_puts_a_one_time_presenter_code_on_the_display(self):
        self.on()
        self.tap()
        self.assertIsNone(self.digits())                                      # a tap is not enough
        self.hold()
        code = self.digits()
        text = self.text()
        self.assertIn("One-time presenter code  " + code, text)
        self.assertIn("http://box.local/", text)
        self.assertIn("Works once", text)
        self.assertTrue(self.screen.controller_up())
        st, out, _ = self.call("POST", "/api/pair", {"pin": code, "name": "new phone"})
        self.assertEqual((st, out["device"]["role"]), (200, "live"))
        self.assertFalse(self.screen.controller_up())
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))     # off the display at use, not at the next tick
        self.assertEqual(self.call("POST", "/api/pair", {"pin": code, "name": "again"})[0], 403)
        last = self.state()["controller"]["status"]["last"]
        self.assertEqual((last["how"], last["device"]), ("used", "new phone"))

    def test_the_full_access_kind_needs_its_own_switch(self):
        self.on()
        self.hold(NOTE_OWNER)
        self.assertIsNone(self.digits())
        self.assertIn("Full access codes from a controller are switched off", self.text())      # said on the display, with no code
        self.assertFalse(re.search(r"[0-9]{6}", self.text()))
        self.on(owner=True)
        self.hold(NOTE_OWNER)
        code = self.digits()
        self.assertIn("One-time full access code  " + code, self.text())
        st, out, _ = self.call("POST", "/api/pair", {"pin": code, "name": "laptop"})
        self.assertEqual((st, out["device"]["role"]), (200, "full"))

    def test_a_second_press_takes_it_off_and_that_press_gives_no_new_one(self):
        self.on()
        self.hold()
        code = self.digits()
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))                # the press alone is enough: hiding needs no hold
        self.assertIsNone(self.digits())
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))
        self.t[0] += 4                                                        # and held on for four seconds
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.assertIsNone(self.digits())
        self.assertEqual(self.call("POST", "/api/pair", {"pin": code, "name": "x"})[0], 403)
        self.assertEqual(self.state()["controller"]["status"]["last"]["how"], "pressed again")
        self.hold()                                                           # a new hold is a new code
        self.assertNotEqual(self.digits(), None)
        self.hub.on_message("Mini", ("on", 0, NOTE_OWNER, 127))               # the other control hides it as well
        self.assertIsNone(self.digits())

    def test_it_leaves_the_display_when_it_runs_out(self):
        self.on()
        self.hold()
        self.assertIn("Hides in 120 s", self.text())
        self.t[0] += 60
        self.screen.tick()
        self.assertIn("Hides in 60 s", self.text())
        self.t[0] += 60
        self.screen.tick()
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))
        self.assertFalse(self.screen.controller_up())
        self.assertEqual(self.state()["controller"]["status"]["last"]["how"], "expired")

    def test_a_few_an_hour_and_the_display_says_so(self):
        self.on()
        for _ in range(auth_mod.CONTROLLER_PER_HOUR):
            self.hold()
            self.assertIsNotNone(self.digits())
            self.tap()                                                        # hide it again
            self.t[0] += 20
        self.hold()
        self.assertIsNone(self.digits())
        self.assertIn("No more codes from a controller for now", self.text())
        self.assertFalse(re.search(r"[0-9]{6}", self.text()))
        self.t[0] += pinscreen.NOTICE_SECONDS + 1
        self.screen.tick()
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))     # the notice goes by itself

    def test_only_a_controller_on_usb_is_heard(self):
        self.on()
        for card in ({"name": "Virtual Raw MIDI", "usbid": None, "readable": True}, {"name": None, "usbid": None, "readable": False}, None, "Mini"):
            self.cards[PAD] = card
            self.hold()
            self.assertIsNone(self.digits(), card)
        self.hold(source="ghost")                                             # a name no plugged-in controller has
        self.assertIsNone(self.digits())
        self.cards[PAD] = self.USB[PAD]
        self.hold()
        self.assertIsNotNone(self.digits())

    def test_the_digits_are_on_the_display_and_nowhere_else(self):
        self.on(owner=True)
        seen = []
        for note in (NOTE_JOIN, NOTE_OWNER):
            self.hold(note)
            code = self.digits()
            seen.append(code)
            for device in (self.full, self.live):
                self.assertNotIn(code, json.dumps(self.state(device)))
            for path in ("/api/status", "/api/midi", "/api/devices", "/api/system", "/api/hello"):
                st, out = self.api.handle("GET", path, {}, self.full, "10.0.0.5")
                self.assertNotIn(code, json.dumps(out), path)
            self.assertEqual(self.api.handle("GET", "/api/access", {}, self.view, "10.0.0.5")[0], 403)
            self.tap(note)
        self.call("POST", "/api/pair", {"pin": "000000", "name": "x"})
        for code in seen:
            self.assertFalse([line for line in self.said if code in line])    # never in the log
            self.assertNotIn(code, json.dumps(self.settings.data))            # never saved
        self.assertTrue(any("one-time presenter code was put on the display from Mini" in line for line in self.said))

    def test_snapshots_for_devices_without_full_access_leave_the_text_out(self):
        self.on()
        self.auth.revoke(self.view["id"])
        self.assertFalse(self.api.access_on_screen())
        self.hold()
        self.assertTrue(self.api.access_on_screen())
        self.tap()
        self.assertFalse(self.api.access_on_screen())

    def test_only_a_full_access_device_sees_it_and_it_sees_no_digits(self):
        self.on(owner=True)
        self.assertNotIn("controller", self.state(self.live))
        c = self.state()["controller"]
        self.assertEqual((c["enabled"], c["owner"], c["seconds"], c["status"]["active"]), (True, True, 120, False))
        self.hold(NOTE_OWNER)
        s = self.state()["controller"]["status"]
        self.assertEqual((s["active"], s["kind"], s["seconds_left"], s["made_this_hour"]), (True, "owner", 120, 1))
        self.assertEqual(sorted(s), ["active", "kind", "last", "made_this_hour", "per_hour", "seconds_left", "shown", "shown_ago"])

    def test_a_full_access_device_ends_it_and_nobody_else_can(self):
        self.on()
        self.hold()
        code = self.digits()
        for device in (self.live, self.view):
            self.assertEqual(self.api.handle("POST", "/api/access/controller", {"cancel": True}, device, "10.0.0.5")[0], 403)
        self.assertEqual(self.api.handle("POST", "/api/access/controller", {"cancel": True}, None, "10.0.0.5")[0], 401)
        self.assertEqual(self.digits(), code)
        st, out = self.api.handle("POST", "/api/access/controller", {"cancel": True}, self.full, "10.0.0.5")
        self.assertEqual((st, out["controller"]["status"]["active"], out["controller"]["status"]["last"]["how"]), (200, False, "cancelled"))
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))
        self.assertEqual(self.call("POST", "/api/pair", {"pin": code, "name": "x"})[0], 403)
        self.assertEqual(self.api.handle("POST", "/api/access/controller", {"cancel": True}, self.full, "10.0.0.5")[0], 409)

    def test_only_a_full_access_device_changes_the_setting(self):
        for device, want in ((self.live, 403), (self.view, 403), (None, 401)):
            self.assertEqual(self.api.handle("POST", "/api/access/controller", {"enabled": True, "owner": True}, device, "10.0.0.5")[0], want)
        self.assertEqual(self.settings.data["controller_code"], {"enabled": False, "owner": False})
        for junk in ({}, {"enabled": 1}, {"enabled": "true"}, {"owner": None}, {"enabled": True, "code": "123456"}, {"cancel": True, "enabled": True},
                     {"cancel": "yes"}, {"kind": "owner"}, {"show": True}):
            self.assertEqual(self.api.handle("POST", "/api/access/controller", junk, self.full, "10.0.0.5")[0], 400, junk)
        self.assertEqual(self.api.handle("POST", "/api/access/controller", {"owner": True}, self.full, "10.0.0.5")[1]["controller"]["owner"], False)   # not without the first switch
        self.on(owner=True)
        with open(self.settings.path) as f:
            self.assertEqual(json.load(f)["controller_code"], {"enabled": True, "owner": True})     # and it is saved
        out = self.api.handle("POST", "/api/access/controller", {"enabled": False}, self.full, "10.0.0.5")[1]
        self.assertEqual((out["controller"]["enabled"], out["controller"]["owner"]), (False, False))   # off takes the owner kind off with it

    def test_switching_it_off_takes_the_code_off_the_display(self):
        self.on(owner=True)
        self.hold(NOTE_OWNER)
        self.api.handle("POST", "/api/access/controller", {"owner": False}, self.full, "10.0.0.5")
        self.assertIsNone(self.digits())
        self.assertEqual(self.player.ipc.shown[-1], ("show-text", "", 1))
        self.hold()
        self.api.handle("POST", "/api/access/controller", {"enabled": False}, self.full, "10.0.0.5")
        self.assertIsNone(self.digits())
        self.assertFalse(self.screen.controller_up())

    def test_never_through_the_support_tunnel(self):
        self.on()
        self.hold()
        code = self.digits()
        self.api.support.is_remote = lambda client: client == "10.77.0.1"
        support_login = dict(self.full, remote=True)
        for device in (self.full, support_login):
            for body in ({"enabled": True, "owner": True}, {"cancel": True}, {"enabled": False}):
                self.assertEqual(self.api.handle("POST", "/api/access/controller", body, device, "10.77.0.1")[0], 403)
            self.assertEqual(self.api.handle("GET", "/api/access", {}, device, "10.77.0.1")[0], 403)
            self.assertEqual(self.api.handle("POST", "/api/pair", {"pin": code, "name": "far away"}, device, "10.77.0.1")[0], 403)
        self.assertEqual(self.api.handle("POST", "/api/pair", {"pin": code, "name": "far away"}, None, "10.77.0.1")[0], 403)
        self.assertEqual(self.digits(), code)                                 # none of that used it or ended it
        self.assertEqual(self.settings.data["controller_code"], {"enabled": True, "owner": False})
        self.assertTrue("/api/access/controller".startswith(support.REMOTE_DENY_PREFIX))
        self.assertIn(("POST", "/api/pair"), support.REMOTE_DENY)

    def test_no_route_and_no_network_input_can_ask_for_a_code(self):
        self.on(owner=True)
        # 1. the API: one route knows the word, and it only takes the switches and Cancel
        routes = self.api.routes()
        self.assertEqual([k for k in routes if "controller" in k[1]], [("POST", "/api/access/controller")])
        self.assertEqual(routes[("POST", "/api/access/controller")][0], "full")
        for body in ({"kind": "join"}, {"show": True}, {"make": "owner"}, {"press": 1.0}, {"kind": "owner", "since": 1.0}):
            self.assertEqual(self.api.handle("POST", "/api/access/controller", body, self.full, "10.0.0.5")[0], 400)
        # 2. what OSC, DMX, the schedule and a Room scene do is call a route as a presenter: the mapper's marker is no route
        for device in (osc.OSC_DEVICE, dmx.DMX_DEVICE, room.ROOM_DEVICE, midi.MIDI_DEVICE):
            self.assertEqual(self.api.handle("POST", midi.LOCAL_CODE, {"kind": "owner", "since": 1.0}, device, "osc")[0], 404)
            self.assertEqual(self.api.handle("POST", "/api/access/controller", {"enabled": True}, device, "osc")[0], 403)
        self.assertFalse(midi.LOCAL_CODE.startswith("/"))
        # 3. none of the network inputs' code knows the manager, the auth call or the two actions
        for module in (osc, dmx, sync, scheduler, room):
            source = inspect.getsource(module)
            for word in ("controller_code", "controllercode", "create_controller", "code_join", "code_owner", "LOCAL_CODE"):
                self.assertNotIn(word, source, (module.__name__, word))
        for address in ("/pvj/code", "/pvj/code/owner", "/code_owner", "/pvj/access/controller", "/pvj/pin"):
            got = osc.translate(address, [1])
            self.assertFalse(got and "access" in got[0], address)
        # 4. the only callers of the two functions that make a code
        import pvj
        folder = os.path.dirname(pvj.__file__)
        users = {"create_controller_code": [], "controller_codes.request": [], "codes.request": []}
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                with open(os.path.join(folder, name)) as f:
                    text = f.read()
                for word in users:
                    if re.search(r"\b" + re.escape(word) + r"\(", text) and "def " + word + "(" not in text:
                        users[word].append(name)
        self.assertEqual(users, {"create_controller_code": ["controllercode.py"], "controller_codes.request": [], "codes.request": ["midi.py"]})
        self.assertIsNone(self.digits())                                      # and nothing above made one

    def test_the_hub_makes_no_api_call_for_it_and_writes_nothing_to_a_controller(self):
        self.on()
        calls = []
        real = self.api.handle
        self.api.handle = lambda *a: calls.append(a) or real(*a)
        self.hold()
        self.assertIsNotNone(self.digits())
        self.assertEqual([c for c in calls if c[3] == midi.MIDI_DEVICE], [])
        self.assertEqual(self.hub.lights, {})

    def test_a_display_that_cannot_show_it_leaves_no_code_behind(self):
        self.on()

        def gone(*cmd):
            from pvj.player import PlayerError
            raise PlayerError("no reply from mpv")
        self.player.ipc.request = gone
        self.hold()
        self.assertIsNone(self.digits())
        self.assertEqual(self.state()["controller"]["status"]["last"]["how"], "not shown")
        self.api.pinscreen = None                                             # a box with no on-screen display at all
        self.hold()
        self.assertIsNone(self.digits())

    def test_a_request_on_the_panel_stays_under_it_and_comes_back(self):
        self.on()
        self.screen.show(["view"], 600)
        self.assertIn("Guest, watch only, code", self.text())
        self.hold()
        self.assertIn("One-time presenter code", self.text())
        self.assertNotIn("Guest, watch only, code", self.text())
        self.tap()
        self.assertIn("Guest, watch only, code", self.text())                 # what the owner put up is back, for the time it was given
        self.assertTrue(self.screen.status()["showing"])


class DisplayTest(unittest.TestCase):
    """What is drawn: the presenter kind with a QR code, the full access kind as text only (like the PIN)."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        switch(self.settings, owner=True)
        self.t = [50.0]
        self.auth = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)
        self.api = PinApi()
        self.overlays, self.removed = [], []
        self.api.player.osd_size = lambda: (1920, 1080)
        self.api.player.overlay = lambda oid, x, y, w, h, pixels: self.overlays.append(oid)
        self.api.player.overlay_remove = lambda oid: self.removed.append(oid)
        self.p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="nxlx-mastercontrol", clock=lambda: self.t[0])

    def test_the_presenter_kind_has_a_qr_code_and_the_full_access_kind_has_none(self):
        self.auth.create_controller_code("join")
        self.assertTrue(self.p.controller_changed())
        self.assertEqual(self.overlays, [pinscreen.QR_IDS["controller"]])
        self.assertIn("Scan the QR code", self.api.player.shown[-1][1])
        self.auth.create_controller_code("owner")
        self.overlays.clear()
        self.p.controller_changed()
        self.assertEqual(self.overlays, [])
        self.assertIn(pinscreen.QR_IDS["controller"], self.removed)
        text = self.api.player.shown[-1][1]
        self.assertIn("One-time full access code  " + self.auth.controller_digits()[1], text)
        self.assertNotIn("QR", text)
        self.assertEqual(pinscreen.clean(text.replace("\n", " ")), text.replace("\n", " "))       # only characters mpv draws as they are

    def test_it_is_drawn_over_a_playing_clip_and_cleared_with_its_qr_code(self):
        self.api.player.st = {"running": True, "path": "/media/a.mp4"}
        self.auth.create_controller_code("join")
        self.assertTrue(self.p.tick())
        self.auth.cancel_controller_code()
        self.removed.clear()
        self.p.tick()
        self.assertEqual(self.api.player.shown[-1], ("show-text", "", 1))
        self.assertIn(pinscreen.QR_IDS["controller"], self.removed)
        before = len(self.api.player.shown)
        self.p.tick()                                                         # and nothing more is drawn over the clip
        self.assertEqual(len(self.api.player.shown), before)


class SettingsTest(BoxBase):
    def test_the_migration_adds_it_switched_off_whatever_the_file_said(self):
        self.assertEqual(settings_mod.SCHEMA, 14)
        for before in ({}, {"controller_code": {"enabled": True, "owner": True}}):
            data = dict(before, schema=13)
            settings_mod.migrate(data)
            self.assertEqual((data["schema"], data["controller_code"]), (14, {"enabled": False, "owner": False}))
        self.assertEqual(settings_mod.default_settings()["controller_code"], {"enabled": False, "owner": False})

    def test_a_settings_file_never_carries_it(self):
        self.on(owner=True)
        self.assertIn("controller_code", boxcare.NEVER)
        file = self.api.boxcare.export({}, self.full, "10.0.0.5")["file"]
        self.assertNotIn("controller_code", file["settings"])
        self.api.handle("POST", "/api/access/controller", {"enabled": False}, self.full, "10.0.0.5")
        smuggled = copy.deepcopy(file)
        smuggled["settings"]["controller_code"] = {"enabled": True, "owner": True}
        with self.assertRaises(Exception) as e:
            self.api.boxcare.check_file(json.dumps(smuggled).encode())
        self.assertIn("access data", str(getattr(e.exception, "message", e.exception)))
        self.api.boxcare.import_settings(json.dumps(file).encode(), boxcare.CONFIRM_IMPORT, self.full, "10.0.0.5")
        self.assertEqual(self.settings.data["controller_code"], {"enabled": False, "owner": False})   # an import leaves the box's own
        self.assertEqual(boxcare.public_settings(self.settings.data)["controller_code"], {"enabled": False, "owner": False})

    def test_a_factory_reset_switches_it_off_and_ends_the_code(self):
        self.on(owner=True)
        self.hold(NOTE_OWNER)
        code = self.digits()
        self.api.boxcare._wipe_access()
        self.assertEqual(self.settings.data["controller_code"], {"enabled": False, "owner": False})
        self.assertIsNone(self.digits())
        self.assertIsNone(self.auth.controller_status()["last"])
        self.assertEqual(self.call("POST", "/api/pair", {"pin": code, "name": "x"})[0], 403)


if __name__ == "__main__":
    unittest.main()
