# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The findings of the independent review of the controller code (D61), one class per finding, each with the
sequence the reviewer gave where there was one. Same fakes as tests/test_controller_code.py: no controller, no Pi."""
import json
import os
import tempfile
import threading
import time
import unittest

from pvj import auth as auth_mod, boxcare, controllercode, midi, pinscreen
from pvj.auth import Auth, AuthError
from pvj.player import Player, PlayerError
from pvj.settings import Settings
from tests import test_controller_code as base
from tests.test_pinscreen import Api as PinApi

NOTE_JOIN, NOTE_OWNER, PAD = base.NOTE_JOIN, base.NOTE_OWNER, base.PAD
NOTE_CLIP = 60
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
LAN = "10.0.0.5"


class ReadTime(base.BoxBase):
    """1. A hold is timed by when its messages were read from the device, not by when they were handled."""

    def setUp(self):
        super().setUp()
        self.on()
        self.settings.data["control"]["midi"]["map"].append(midi.validate_entry({"kind": "note", "number": NOTE_CLIP, "action": "pad", "bank": 0, "index": 0}))
        real = self.api.handle

        def slow(method, path, body, device, client):         # a play that waits 3.2 seconds for the player
            if path == "/api/play":
                self.t[0] += 3.2
            return real(method, path, body, device, client)
        self.api.handle = slow

    def test_a_tap_is_not_a_hold_because_another_pad_kept_the_thread_busy(self):
        t0 = self.t[0]
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127), t0)
        self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127), t0 + 0.05)       # its call takes 3.2 seconds
        self.assertGreater(self.t[0] - t0, 3.0)
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0), t0 + 0.1)         # read a tenth of a second after the press
        self.assertIsNone(self.digits())
        self.assertEqual(self.state()["controller"]["status"]["made_this_hour"], 0)

    def test_a_real_hold_works_while_another_pad_is_busy(self):
        t0 = self.t[0]
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127), t0)
        self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127), t0 + 1.0)
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0), t0 + 3.5)         # handled at 4.2, read at 3.5: a hold of 3.5
        self.assertIsNotNone(self.digits())

    def test_a_hold_that_only_looks_long_enough_when_handled_late_is_too_short(self):
        t0 = self.t[0]
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127), t0)
        self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127), t0 + 2.0)
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0), t0 + 2.9)         # handled at 5.2
        self.assertIsNone(self.digits())

    def test_without_a_read_time_slow_calls_end_what_is_held(self):
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127))                  # 3.2 seconds, and nobody stamped the messages
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.assertIsNone(self.digits())
        self.assertGreater(3.2, midi.SLOW_CALLS)
        self.hold()                                                             # a hold with nothing slow in it still works
        self.assertIsNotNone(self.digits())

    def test_lost_messages_end_what_is_held(self):
        t0 = self.t[0]
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127), t0)
        self.hub.on_message("Mini", midi.LOST, t0 + 1)                          # the reader's queue was full
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0), t0 + 4)
        self.assertIsNone(self.digits())

    def test_the_reader_stamps_a_message_when_it_is_read_not_when_it_is_handled(self):
        r, w = os.pipe()
        got, first = [], threading.Event()

        def handler(source, msg, at=None):
            if msg is None:
                return
            got.append((msg[0], at, time.monotonic()))
            if len(got) == 1:
                first.set()
                time.sleep(0.6)                                                 # the player is slow
        inp = midi.MidiInput("/dev/snd/midiC9D0", "Mini", handler, log=lambda *_: None, open_fn=lambda p: r, clock=time.monotonic)
        inp.start()
        self.addCleanup(inp.stop)
        os.write(w, bytes([0x90, NOTE_JOIN, 127]))
        self.assertTrue(first.wait(3))
        os.write(w, bytes([0x80, NOTE_JOIN, 0]))                                # let go at once, while the handler is busy
        end = time.time() + 4
        while len(got) < 2 and time.time() < end:
            time.sleep(0.02)
        self.assertEqual([g[0] for g in got], ["on", "off"])
        self.assertLess(got[1][1] - got[0][1], 0.4)                             # read apart: a tap
        self.assertGreater(got[1][2] - got[0][1], 0.5)                          # handled apart: what the hold used to be timed by
        os.close(w)


class LearnAndTheMap(base.BoxBase):
    """4. A release that Learn swallowed, or a map changed under a held control, leaves nothing held."""

    def setUp(self):
        super().setUp()
        self.on()

    def test_the_reviewers_sequence(self):
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))                  # press at 0
        self.t[0] += 1
        self.hub.start_learn()                                                  # Learn starts at 1 s
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))                   # the release: dropped, Learn is listening
        self.hub.cancel_learn()
        self.t[0] += 3
        self.tap()                                                              # a tap at 4 s
        self.assertIsNone(self.digits())
        self.hold()
        self.assertIsNotNone(self.digits())

    def test_learn_that_ends_by_itself_or_by_a_capture(self):
        for end in ("time", "capture"):
            self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
            self.hub.start_learn()
            self.hub.mapper._held[("x", "Mini", "note", 99)] = self.t[0]       # as if something were still held
            if end == "time":
                self.t[0] += midi.LEARN_SECONDS + 1
                self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127))
            else:
                self.hub.on_message("Mini", ("on", 0, NOTE_CLIP, 127))
                self.assertIsNotNone(self.hub.captured)
            self.assertEqual(self.hub.mapper._held, {}, end)
            self.hub.cancel_learn()
            self.t[0] += 20

    def test_a_map_saved_or_a_switch_changed_during_a_hold(self):
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.t[0] += 1
        st, out = self.api.handle("POST", "/api/modules/control-midi", {"enabled": True}, self.full, LAN)
        self.assertEqual(st, 200, out)
        st, out = self.api.handle("POST", "/api/midi/map", {"add": {"kind": "note", "number": 61, "action": "stop"}}, self.full, LAN)
        self.assertEqual(st, 200, out)
        self.t[0] += 3
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.assertIsNone(self.digits())
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.hub.mapper.forget_held()                                           # what apply() does for a switch or a layout choice
        self.t[0] += 4
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.assertIsNone(self.digits())

    def test_midi_switched_off_in_the_middle_of_a_hold(self):
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.t[0] += 1
        self.hub.stop()                                                         # MIDI off: the release is never heard
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.hub._stop.clear()                                                  # and on again
        self.plug(PAD, "Mini")
        self.t[0] += 3
        self.tap()
        self.assertIsNone(self.digits())
        self.hold()
        self.assertIsNotNone(self.digits())

    def test_two_controllers_holding_at_once(self):
        second = "/dev/snd/midiC5D0"
        self.cards[second] = {"name": "Launchpad Mini", "usbid": "1235:0036", "readable": True}
        self.plug(second, "Mini_1")
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.t[0] += 1
        self.hub.on_message("Mini_1", ("on", 0, NOTE_JOIN, 127))
        self.t[0] += 2.5
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))                   # the first: 3.5 seconds
        first = self.digits()
        self.assertIsNotNone(first)
        self.t[0] += 1
        self.hub.on_message("Mini_1", ("off", 0, NOTE_JOIN, 0))                 # the second: 3.5 seconds too, its own hold
        s = self.state()["controller"]["status"]
        self.assertEqual((s["active"], s["made_this_hour"], s["last"]["how"]), (True, 2, "replaced"))     # one code at a time, both counted
        self.assertNotEqual(self.digits(), None)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": first, "name": "x"})[0] if first != self.digits() else 403, 403)


class OnlyAButton(unittest.TestCase):
    """7. A control that sends a controller number must behave as a button: 127 down, 0 up, nothing between."""

    def setUp(self):
        self.t, self.asked = [100.0], []
        entries = [midi.validate_entry({"kind": "cc", "number": 30, "action": "code_owner"})]
        self.m = midi.MidiMapper(lambda path, body: True, entries, {}, clock=lambda: self.t[0])
        self.m.local = lambda source, body: self.asked.append(dict(body)) or True

    def cc(self, value, wait=0.0):
        self.t[0] += wait
        self.m.message("nano", ("cc", 0, 30, value))

    def requests(self):
        return [b for b in self.asked if "kind" in b]

    def test_a_fader_pushed_up_left_there_and_pulled_down_asks_for_nothing(self):
        for v in range(0, 128, 3):
            self.cc(v)
        self.cc(127)
        for v in range(127, -1, -3):
            self.cc(v, 4.0 if v == 127 else 0.0)
        self.cc(0)
        self.assertEqual(self.requests(), [])

    def test_any_value_between_ends_the_hold(self):
        for between in (126, 64, 100, 1):
            self.cc(127)
            self.cc(between, 2.0)
            self.cc(0, 2.0)
            self.t[0] += 1
        self.assertEqual(self.requests(), [])
        self.cc(100)                                                            # 64 or more is not a press any more
        self.cc(0, 4.0)
        self.assertEqual(self.requests(), [])

    def test_a_button_still_works(self):
        self.cc(127)
        self.cc(0, 3.5)
        self.assertEqual(self.requests(), [{"kind": "owner", "since": 100.0}])


class OwnerSwitch(base.BoxBase):
    """2. The full access switch never comes back by itself."""

    def test_a_stale_owner_in_the_file_stays_off_when_the_first_switch_goes_on(self):
        self.settings.data["controller_code"] = {"enabled": False, "owner": True}       # a file someone edited
        st, out = self.api.handle("POST", "/api/access/controller", {"enabled": True}, self.full, LAN)
        self.assertEqual((st, out["controller"]["enabled"], out["controller"]["owner"]), (200, True, False))
        self.assertEqual(self.settings.data["controller_code"], {"enabled": True, "owner": False})
        self.hold(NOTE_OWNER)
        self.assertIsNone(self.digits())

    def test_the_section_means_the_same_everywhere(self):
        for junk, want in (({"enabled": False, "owner": True}, (False, False)), ({"owner": True}, (False, False)), ({"enabled": 1, "owner": True}, (False, False)),
                           ({"enabled": True, "owner": 1}, (True, False)), ({"enabled": True, "owner": True}, (True, True)), ("on", (False, False))):
            want = {"enabled": want[0], "owner": want[1]}
            self.assertEqual(auth_mod.controller_setting(junk), want, junk)
            self.assertEqual(controllercode.validate({}, junk), want, junk)
            self.assertEqual(boxcare.public_settings({"controller_code": junk})["controller_code"], want, junk)
            self.settings.data["controller_code"] = junk
            self.assertEqual(self.state()["controller"]["owner"], want["owner"], junk)

    def test_it_is_written_as_what_it_means_when_the_box_starts(self):
        self.settings.data["controller_code"] = {"enabled": False, "owner": True, "extra": 1}
        self.settings.save()
        Auth(self.settings)
        with open(self.settings.path) as f:
            self.assertEqual(json.load(f)["controller_code"], {"enabled": False, "owner": False})


class Snapshot(base.BoxBase):
    """3. The look at the display and the screenshot are one step for a device without full access."""

    def setUp(self):
        super().setUp()
        self.on()
        self.modes, self.during = [], None

        def screenshot(path, quality=60, with_text=True):
            self.modes.append(with_text)
            if self.during:
                during, self.during = self.during, None
                during()
            with open(path, "wb") as f:
                f.write(JPEG + bytes([len(self.modes)]))
        self.player.screenshot = screenshot

    def test_a_code_drawn_between_the_look_and_the_screenshot_is_not_served(self):
        self.during = lambda: self.auth.create_controller_code("join")         # the order forced: it appears while the picture is taken
        for device in (self.view, self.live):
            self.modes.clear()
            self.api._preview = None
            self.during = self.during or (lambda: None)
            got = self.api.preview_jpeg(device)
            self.assertEqual(self.modes[-1], False, device["role"])            # what it was given was taken without text
            self.assertEqual(got, JPEG + bytes([len(self.modes)]))
        self.modes.clear()
        self.api.preview_jpeg(self.view)                                        # and the next one is not the picture with text, kept
        self.assertNotIn(True, self.modes)

    def test_while_a_code_shows_a_lesser_device_is_given_video_only(self):
        self.hold()
        for device in (self.view, self.live):
            self.api._preview = None
            self.modes.clear()
            self.api.preview_jpeg(device)
            self.assertEqual(self.modes, [False], device["role"])               # the actual argument of the screenshot
        self.api._preview = None
        self.modes.clear()
        self.api.preview_jpeg(self.full)
        self.assertEqual(self.modes, [True])

    def test_a_picture_with_text_kept_for_the_owner_is_not_handed_to_a_guest(self):
        self.hold()
        withtext = self.api.preview_jpeg(self.full)
        self.tap()                                                              # the code is gone a moment later
        self.assertFalse(self.api.access_on_screen())
        got = self.api.preview_jpeg(self.view)                                  # within the interval: the kept picture has the code in it
        self.assertNotEqual(got, withtext)
        self.assertEqual(self.modes, [True, True])                              # a new one was taken (nothing secret is up now)
        self.assertEqual(self.api.preview_jpeg(self.live), got)                 # and that one may be shared


class Lockout(base.BoxBase):
    """5. The lockout is as it was; the display says so in plain words."""

    def setUp(self):
        super().setUp()
        self.on()

    def lock(self):
        for n in range(auth_mod.GLOBAL_FAILS):
            with self.assertRaises(AuthError):
                self.auth.pair("000000", "x", "guesser%d" % n)
        self.assertGreater(self.auth.pairing_locked(), 0)

    def test_asked_for_while_pairing_is_locked_for_longer_than_a_code_lasts(self):
        self.lock()
        self.assertEqual(self.auth.pairing_locked(), 300)
        self.hold()
        self.assertIsNone(self.digits())
        self.assertIn("Pairing is locked for 5 minutes after wrong guesses", self.text())
        self.assertEqual(self.state()["controller"]["status"]["made_this_hour"], 0)      # and none was counted
        with self.assertRaises(AuthError) as e:                                          # the lockout itself is untouched
            self.auth.pair(self.pin, "x", "owner at the door")
        self.assertTrue(e.exception.retry_after)

    def test_locked_while_a_code_shows(self):
        self.hold()
        self.assertNotIn("Pairing is locked", self.text())
        code = self.digits()
        self.lock()
        self.screen.tick()
        self.assertIn("One-time presenter code  " + code, self.text())
        self.assertIn("Pairing is locked for 5 minutes after wrong guesses", self.text())
        self.assertEqual(pinscreen.clean(self.text().replace("\n", " ")), self.text().replace("\n", " "))

    def test_a_short_lock_still_gives_a_code_and_says_so(self):
        self.auth._locked_until["*"] = self.t[0] + 60
        self.hold()
        self.assertIsNotNone(self.digits())
        self.assertIn("Pairing is locked for 1 minutes after wrong guesses", self.text())

    def test_with_the_setting_off_a_lock_draws_nothing(self):
        self.api.handle("POST", "/api/access/controller", {"enabled": False}, self.full, LAN)
        self.lock()
        before = len(self.player.ipc.shown)
        self.hold()
        self.assertEqual(len(self.player.ipc.shown), before)


class Devices(unittest.TestCase):
    """8 and 9. The list of devices: codes from a controller cannot fill it against the PIN, a presenter paired so
    goes when unused, and a code is used up only when its device is in the list."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        base.switch(self.settings, owner=True)
        self.t, self.now = [1000.0], [1_700_000_000]
        self.a = Auth(self.settings, clock=lambda: self.t[0], now=lambda: self.now[0], rotate_on_start=True)

    def code(self, kind):
        self.a.create_controller_code(kind)
        return self.a.controller_digits()[1]

    def fill(self, count, role="full", via="controller"):
        self.settings.data["devices"] += [{"id": "%08x" % (n + len(self.settings.data["devices"])), "name": "d", "role": role, "via": via,
                                           "token_hash": "%064x" % n, "created": self.now[0]} for n in range(count)]

    def test_full_access_codes_never_take_the_places_kept_for_the_pin(self):
        room = auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED
        self.fill(room - 1)
        self.assertEqual(self.a.pair(self.code("owner"), "last one", "c")[1]["role"], "full")
        self.t[0] += 3600
        with self.assertRaises(auth_mod.TooManyDevices):                        # no further code of that kind
            self.a.create_controller_code("owner")
        self.assertEqual(len(self.settings.data["devices"]), room)
        for n in range(auth_mod.FULL_RESERVED):                                 # and the PIN pairs, every reserved place
            self.assertEqual(self.a.pair(self.a.current_pin, "owner %d" % n, "c%d" % n)[1]["role"], "full")

    def test_a_presenter_paired_from_a_controller_goes_when_unused_for_a_week(self):
        token, dev = self.a.pair(self.code("join"), "phone", "c")
        full_token, _ = self.a.pair(self.code("owner"), "laptop", "c2")
        joined = self.a.create_join("live")
        join_token, _ = self.a.pair(joined, "resident", "c3")
        self.now[0] += auth_mod.GUEST_IDLE_DAYS * 86400 - 10
        self.assertIsNotNone(self.a.authenticate(token))                        # used: its week starts again
        self.now[0] += auth_mod.GUEST_IDLE_DAYS * 86400 - 10
        self.assertIsNotNone(self.a.authenticate(token))
        self.assertIn("seen", next(d for d in self.settings.data["devices"] if d["id"] == dev["id"]))
        self.now[0] += auth_mod.GUEST_IDLE_DAYS * 86400 + 10
        self.assertIsNone(self.a.authenticate(token))
        self.assertNotIn(dev["id"], [d["id"] for d in self.settings.data["devices"]])
        self.assertIsNotNone(self.a.authenticate(full_token))                   # a full-access device stays
        self.assertIsNotNone(self.a.authenticate(join_token))                   # so does a presenter from a join code, as before

    def test_unused_presenters_from_a_controller_do_not_pile_up(self):
        self.fill(50, role="live")
        self.now[0] += auth_mod.GUEST_IDLE_DAYS * 86400 + 10
        self.a.pair(self.code("join"), "new", "c")                              # pruned whenever someone joins
        self.assertEqual(len(self.settings.data["devices"]), 1)

    def test_two_requests_racing_for_one_code(self):
        code, results = self.code("join"), []
        gate = threading.Barrier(2)

        def go(n):
            gate.wait()
            try:
                results.append(self.a.pair(code, "racer %d" % n, "c%d" % n)[1]["role"])
            except AuthError:
                results.append(None)
        threads = [threading.Thread(target=go, args=(n,)) for n in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(5)
        self.assertEqual(sorted(results, key=str), [None, "live"])              # exactly one pairs
        self.assertEqual(len(self.settings.data["devices"]), 1)

    def test_a_list_that_filled_up_meanwhile_does_not_burn_the_code(self):
        code = self.code("join")
        self.fill(auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED, role="live", via=None)      # invites, while the code was up
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.pair(code, "phone", "c")
        self.assertEqual(self.a.controller_digits()[1], code)                   # still good, still on the display
        self.assertEqual(self.a._fails, {})                                     # and no guess was counted
        self.settings.data["devices"].pop()
        self.assertEqual(self.a.pair(code, "phone", "c")[1]["role"], "live")

    def test_a_device_that_slips_in_between_the_look_and_the_add(self):
        code = self.code("join")
        self.fill(auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED - 1, role="live", via=None)
        real = self.a._match_controller

        def match_then_invite(given):
            role = real(given)
            if role:
                self.a.invite("slipped in", "live")                             # invite() does not take the pairing lock
            return role
        self.a._match_controller = match_then_invite
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.pair(code, "phone", "c")
        self.assertEqual(self.a.controller_digits()[1], code)

    def test_settings_that_cannot_be_saved_do_not_burn_the_code_or_leave_a_device(self):
        code = self.code("owner")
        real, before = self.settings.save, len(self.settings.data["devices"])

        def full_disk():
            raise OSError(28, "No space left on device")
        self.settings.save = full_disk
        with self.assertRaises(OSError):
            self.a.pair(code, "phone", "c")
        self.settings.save = real
        self.assertEqual(len(self.settings.data["devices"]), before)
        self.assertEqual(self.a.controller_digits()[1], code)
        self.assertEqual(self.a.pair(code, "phone", "c")[1]["role"], "full")


class Leftovers(unittest.TestCase):
    """10. The QR code's file, a player that does not answer, and the diagnostics file."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        base.switch(self.settings, owner=True)
        self.t = [50.0]
        self.auth = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)
        self.api = PinApi()
        self.overlays, self.removed, self.deaf = [], [], False
        self.api.player.osd_size = lambda: (1920, 1080)
        self.api.player.overlay = lambda oid, x, y, w, h, pixels: self.overlays.append(oid)

        def remove(oid):
            if self.deaf:
                raise PlayerError("no reply from mpv")
            self.removed.append(oid)
        self.api.player.overlay_remove = remove
        self.p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="box", clock=lambda: self.t[0])

    def test_the_qr_codes_file_goes_with_the_overlay_even_if_the_player_is_silent(self):
        folder = tempfile.mkdtemp()
        player = Player.__new__(Player)
        player.rundir = folder
        sent = []

        class Ipc:
            fail = False

            def request(inner, *cmd):
                if inner.fail:
                    raise PlayerError("no reply from mpv")
                sent.append(cmd[0])
        player.ipc = Ipc()
        path = os.path.join(folder, "overlay-%d.bgra" % pinscreen.QR_IDS["controller"])
        player.overlay(pinscreen.QR_IDS["controller"], 0, 0, 2, 2, b"\x00" * 16)
        self.assertTrue(os.path.exists(path))
        player.overlay_remove(pinscreen.QR_IDS["controller"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(sent, ["overlay-add", "overlay-remove"])
        player.overlay(pinscreen.QR_IDS["controller"], 0, 0, 2, 2, b"\x00" * 16)
        player.ipc.fail = True
        with self.assertRaises(PlayerError):
            player.overlay_remove(pinscreen.QR_IDS["controller"])
        self.assertFalse(os.path.exists(path))
        player.overlay_remove(63) if not player.ipc.fail else None             # (nothing to remove is not an error; not run while silent)

    def test_a_qr_code_that_could_not_be_taken_off_is_tried_again(self):
        self.auth.create_controller_code("join")
        self.assertTrue(self.p.controller_changed())
        self.auth.list_devices = lambda: [{"id": "x"}]                          # a paired box: nothing else wants the display
        self.deaf = True
        self.removed.clear()
        self.auth.cancel_controller_code()
        self.p.tick()
        self.assertEqual(self.removed, [])
        self.assertTrue(self.p._qr_stale)
        self.p.tick()                                                           # still silent: still remembered
        self.assertTrue(self.p._qr_stale)
        self.deaf = False
        self.p.tick()
        self.assertIn(pinscreen.QR_IDS["controller"], self.removed)
        self.assertFalse(self.p._qr_stale)
        count = len(self.removed)
        self.p.tick()                                                           # and then no more
        self.assertEqual(len(self.removed), count)

    def test_the_qr_code_is_drawn_again_after_a_while_for_a_player_that_restarted(self):
        self.auth.create_controller_code("join")
        self.p.controller_changed()
        self.p.tick()
        self.assertEqual(self.overlays, [pinscreen.QR_IDS["controller"]])       # not at every tick
        self.t[0] += pinscreen.QR_REFRESH + 1
        self.p.tick()
        self.assertEqual(self.overlays, [pinscreen.QR_IDS["controller"]] * 2)   # a restarted player has lost it: here it is again
        self.assertIn("One-time presenter code", self.api.player.shown[-1][1])


class Diagnostics(base.BoxBase):
    def test_the_diagnostics_file_never_holds_the_digits(self):
        self.on(owner=True)
        for note in (NOTE_JOIN, NOTE_OWNER):
            self.hold(note)
            code = self.digits()
            secrets = self.api.boxcare._secrets(self.settings.data)
            self.assertIn(code, secrets)
            line = "Oct 07 21:04:11 box pvj-web[411]: GET /#code=%s and again %s." % (code, code)
            self.assertNotIn(code, json.dumps(boxcare.scrub({"log": [line], "note": "seen %s" % code}, secrets)))
            self.tap(note)
        self.assertNotIn(None, self.api.boxcare._secrets(self.settings.data))


if __name__ == "__main__":
    unittest.main()
