# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from pvj import auth as auth_mod, pinscreen
from pvj.auth import Auth, AuthError
from pvj.player import PlayerError
from pvj.settings import Settings
from tests.test_pinscreen import Api as PinApi, Auth as PinAuth, Player as PinPlayer
from tests.test_server import ServerBase


class JoinCodeTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t = [1000.0]
        self.a = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)

    def test_a_code_pairs_a_guest_or_presenter_but_never_full_access(self):
        view, live = self.a.create_join("view"), self.a.create_join("live")
        self.assertEqual(len(view), 6)
        _, d1 = self.a.pair(view, "guest phone", "c1")
        _, d2 = self.a.pair(live, "presenter", "c2")
        self.assertEqual((d1["role"], d2["role"]), ("view", "live"))
        _, d3 = self.a.pair(self.a.current_pin, "owner", "c3")
        self.assertEqual(d3["role"], "full")                                  # the box's own PIN still gives full access

    def test_only_view_and_live_codes_exist(self):
        for role in ("full", "admin", "", None, 5):
            with self.assertRaises(AuthError):
                self.a.create_join(role)

    def test_codes_expire(self):
        code = self.a.create_join("view", minutes=2)
        self.t[0] += 119
        self.assertEqual(self.a.pair(code, "x", "c")[1]["role"], "view")
        self.t[0] += 2
        with self.assertRaises(AuthError):
            self.a.pair(code, "late", "c2")
        self.assertEqual(self.a.list_joins(), [])

    def test_codes_have_a_use_limit(self):
        code = self.a.create_join("live", uses=2)
        self.a.pair(code, "a", "c1")
        self.a.pair(code, "b", "c2")
        with self.assertRaises(AuthError):
            self.a.pair(code, "c", "c3")

    def test_a_new_code_for_a_role_replaces_the_old_one_and_the_total_is_capped(self):
        first = self.a.create_join("view")
        second = self.a.create_join("view")
        with self.assertRaises(AuthError):
            self.a.pair(first, "x", "c")
        self.assertEqual(self.a.pair(second, "x", "c2")[1]["role"], "view")
        self.assertEqual(len(self.a.list_joins()), 1)

    def test_wrong_codes_are_throttled_like_wrong_pins(self):
        self.a.create_join("view")
        for _ in range(auth_mod.PER_CLIENT_FAILS):
            with self.assertRaises(AuthError):
                self.a.pair("000000" if self.a.list_joins()[0]["code"] != "000000" else "000001", "x", "guesser")
        with self.assertRaises(AuthError) as cm:
            self.a.pair(self.a.list_joins()[0]["code"], "x", "guesser")       # even the right code, from a locked-out client
        self.assertTrue(cm.exception.retry_after)

    def test_bad_input(self):
        for kw in ({"minutes": 0}, {"minutes": 121}, {"minutes": True}, {"minutes": "5"}, {"minutes": 1.5}, {"uses": 0}, {"uses": 51}, {"uses": True}):
            with self.assertRaises(AuthError, msg=str(kw)):
                self.a.create_join("view", **kw)
        for given in (None, 5, "12345", "1234567", "abcdef", "12345\n", " 123456"):
            with self.assertRaises(AuthError):
                self.a.pair(given, "x", "c")

    def test_cancel_one_or_all_and_they_never_reach_the_settings_file(self):
        v, l = self.a.create_join("view"), self.a.create_join("live")
        self.assertTrue(self.a.cancel_join(v))
        self.assertFalse(self.a.cancel_join(v))
        with open(self.settings.path) as f:
            text = f.read()
        self.assertNotIn(l, text)                                              # memory only
        self.assertTrue(self.a.cancel_join(None))
        self.assertEqual(self.a.list_joins(), [])


    def test_a_code_says_who_made_it_and_can_be_left_alone_or_ended_by_role(self):
        owner = self.a.create_join("view")
        self.assertEqual([(j["role"], j["by"]) for j in self.a.list_joins()], [("view", "owner")])
        with self.assertRaises(auth_mod.JoinExists):
            self.a.create_join("view", by="presenter", replace=False)
        self.assertEqual(self.a.list_joins()[0]["code"], owner)             # untouched
        mine = self.a.create_join("view", by="presenter")
        self.assertEqual([(j["code"], j["by"]) for j in self.a.list_joins()], [(mine, "presenter")])
        with self.assertRaises(AuthError):
            self.a.create_join("view", by="anyone")
        live = self.a.create_join("live")
        self.assertTrue(self.a.cancel_join_role("view"))
        self.assertFalse(self.a.cancel_join_role("view"))
        self.assertEqual([j["code"] for j in self.a.list_joins()], [live])
        self.t[0] += 16 * 60                                                # an expired code is not "one to end"
        self.assertFalse(self.a.cancel_join_role("live"))
        self.a.create_join("view", replace=False)                           # and is not in the way of a new one


class BoundsTest(unittest.TestCase):
    """Review of D47, finding 1: the device list, presenter-made codes and idle guest devices are bounded."""

    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t, self.wall = [1000.0], [1_800_000_000]
        self.a = Auth(self.settings, clock=lambda: self.t[0], now=lambda: self.wall[0], rotate_on_start=True)

    def fill(self, n, role="view"):
        self.settings.data["devices"] += [{"id": "f%07d" % (len(self.settings.data["devices"]) + i), "name": "x", "role": role,
                                           "token_hash": "0" * 64, "created": self.wall[0]} for i in range(n)]

    def test_guests_and_presenters_can_never_fill_the_list_and_lock_the_owner_out(self):
        room = auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED
        self.fill(room - 1)
        code = self.a.create_join("view", uses=5)
        self.assertEqual(self.a.pair(code, "last one in", "c1")[1]["role"], "view")
        for _ in range(auth_mod.PER_CLIENT_FAILS + 2):                   # a right code against a full list is not a wrong guess
            with self.assertRaises(auth_mod.TooManyDevices) as e:
                self.a.pair(code, "one too many", "c1")
            self.assertIsNone(e.exception.retry_after)
        self.assertIn("too many devices are paired", str(e.exception))
        self.assertEqual(self.a.list_joins()[0]["uses_left"], 4)         # and it is not used up by being refused
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.invite("link", "live")
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.pair(self.a.create_join("live"), "presenter", "c2")
        self.assertEqual(len(self.settings.data["devices"]), room)
        for n in range(auth_mod.FULL_RESERVED):                          # the PIN still pairs: those places are the owner's
            self.assertEqual(self.a.pair(self.a.current_pin, "owner %d" % n, "c3")[1]["role"], "full")
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.pair(self.a.current_pin, "owner 21", "c3")
        self.assertEqual(len(self.settings.data["devices"]), auth_mod.MAX_DEVICES)
        self.assertTrue(self.a.revoke(self.settings.data["devices"][0]["id"]))       # nothing was evicted; removing one makes room
        self.assertEqual(self.a.pair(code, "now there is room", "c1")[1]["role"], "view")

    def test_presenters_make_a_few_guest_codes_an_hour_and_the_owner_is_not_counted(self):
        for _ in range(auth_mod.PRESENTER_CODES_PER_HOUR):
            self.a.create_join("view", by="presenter")
            self.t[0] += 60
        with self.assertRaises(auth_mod.JoinLimit) as e:
            self.a.create_join("view", by="presenter")
        self.assertIn("6 guest codes were made in the last hour", str(e.exception))
        self.assertTrue(0 < e.exception.retry_after <= 3600)
        self.assertEqual(len(self.a.list_joins()), 1)                    # the active code stays
        self.a.create_join("view")                                       # the owner still can
        self.a.create_join("live")
        self.t[0] += e.exception.retry_after
        self.a.create_join("view", by="presenter")                       # and a presenter again once the hour has moved on
        with self.assertRaises(auth_mod.JoinLimit):
            self.a.create_join("view", by="presenter")
        with self.assertRaises(auth_mod.JoinExists):                     # a refusal that made nothing is not counted
            self.a.create_join("view", by="presenter", replace=False)

    def test_a_device_removed_meanwhile_changes_nothing(self):
        owner = self.a.create_join("view")
        with self.assertRaises(auth_mod.NotPaired):
            self.a.create_join("view", by="presenter", check=lambda: False)
        self.assertEqual([j["code"] for j in self.a.list_joins()], [owner])
        self.assertNotEqual(self.a.create_join("view", by="presenter", check=lambda: True), owner)

    def test_a_guest_code_s_device_goes_when_unused_for_a_week_and_nothing_else_does(self):
        day = 86400
        idle, _ = self.a.pair(self.a.create_join("view"), "came once", "c")
        used, _ = self.a.pair(self.a.create_join("view"), "comes daily", "c")
        staff, _ = self.a.pair(self.a.create_join("live"), "staff by code", "c")
        link, _ = self.a.invite("resident", "view")
        owner, _ = self.a.pair(self.a.current_pin, "owner", "c")
        self.assertEqual([d.get("via") for d in self.settings.data["devices"]], ["code", "code", "code", None, None])
        for _ in range(auth_mod.GUEST_IDLE_DAYS + 1):
            self.wall[0] += day
            self.assertEqual(self.a.authenticate(used)["name"], "comes daily")
        with open(self.settings.path) as f:                              # its last use is on disk, so a restart does not forget it
            self.assertIn('"seen"', f.read())
        self.assertEqual(len(self.settings.data["devices"]), 5)          # nothing has looked at the idle one yet
        self.assertIsNone(self.a.authenticate(idle))                     # it comes back after 8 days: refused, and dropped
        self.assertEqual([d["name"] for d in self.settings.data["devices"]], ["comes daily", "staff by code", "resident", "owner"])
        for token in (staff, link, owner):
            self.assertIsNotNone(self.a.authenticate(token))
        # one that never comes back goes at the next start, or when the next person joins
        gone, _ = self.a.pair(self.a.create_join("view"), "never again", "c")
        for _ in range(auth_mod.GUEST_IDLE_DAYS + 1):
            self.wall[0] += day
            self.assertIsNotNone(self.a.authenticate(used))
        again = Auth(self.settings, clock=lambda: self.t[0], now=lambda: self.wall[0])
        self.assertEqual([d["name"] for d in self.settings.data["devices"]], ["comes daily", "staff by code", "resident", "owner"])
        self.assertIsNone(again.authenticate(gone))
        self.wall[0] -= 400 * day                                        # a clock that jumps back drops nobody
        self.assertIsNotNone(again.authenticate(used))


class ReviewFindingsTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.a = Auth(self.settings, rotate_on_start=True)

    def test_non_ascii_digits_are_a_counted_failure_not_a_crash(self):
        self.a.create_join("view")
        with self.assertRaises(AuthError):
            self.a.pair("\u0661\u0662\u0663\u0664\u0665\u0666", "x", "c")        # Arabic-Indic digits
        self.assertEqual(len(self.a._fails.get("c", [])), 1)

    def test_a_real_code_does_not_reset_the_pin_guess_counter(self):
        code = self.a.create_join("view", uses=10)
        for _ in range(auth_mod.PER_CLIENT_FAILS - 1):
            with self.assertRaises(AuthError):
                self.a.pair("0000" if self.a.current_pin != "0000" else "1111", "x", "c")
        self.a.pair(code, "guest", "c")
        with self.assertRaises(AuthError):
            self.a.pair("0000" if self.a.current_pin != "0000" else "1111", "x", "c")
        with self.assertRaises(AuthError) as cm:
            self.a.pair(self.a.current_pin, "x", "c")                                  # now locked out
        self.assertTrue(cm.exception.retry_after)

    def test_unblock_lifts_a_lockout_without_changing_the_pin(self):
        pin = self.a.current_pin
        for i in range(auth_mod.GLOBAL_FAILS):
            try:
                self.a.pair("0000" if pin != "0000" else "1111", "x", "c%d" % i)
            except AuthError:
                pass
        with self.assertRaises(AuthError):
            self.a.pair(pin, "x", "someone-else")
        self.a.clear_lockout()
        self.assertEqual(self.a.pair(pin, "x", "someone-else")[1]["role"], "full")
        self.assertEqual(self.a.current_pin, pin)


class ManualDisplayTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t = [50.0]
        self.auth = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)
        self.api = PinApi()
        self.p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="nxlx-mastercontrol", clock=lambda: self.t[0])

    def text(self):
        return self.api.player.shown[-1][1]

    def test_shows_the_chosen_items_even_over_a_playing_clip(self):
        self.api.player.st = {"running": True, "path": "/media/a.mp4"}
        st = self.p.show(["pin", "view", "live"], 60)
        self.assertTrue(st["showing"])
        text = self.text()
        self.assertIn("Full access PIN  " + self.auth.current_pin, text)
        codes = {j["role"]: j["code"] for j in self.auth.list_joins()}
        self.assertIn("Guest, watch only, code  " + codes["view"], text)
        self.assertIn("Presenter, play and mix, code  " + codes["live"], text)
        self.assertIn("http://nxlx-mastercontrol.local/", text)
        self.assertIn("Hides in 60 s", text)
        self.assertIn("guest QR on the left, presenter QR on the right", text)

    def test_only_what_was_asked_for_is_shown(self):
        self.p.show(["view"], 30)
        text = self.text()
        self.assertIn("Guest", text)
        self.assertNotIn("Full access PIN", text)
        self.assertNotIn("Presenter", text)

    def test_it_hides_by_itself_and_clears_the_text(self):
        self.auth._add_device("owner", "full")            # a paired box: the automatic first-run PIN is off, so nothing redraws it
        self.p.show(["pin"], 20)
        self.t[0] += 21
        self.p.tick()
        self.assertEqual(self.api.player.shown[-1][:2], ("show-text", ""))
        self.assertFalse(self.p.status()["showing"])

    def test_hide_now(self):
        self.auth._add_device("owner", "full")
        self.p.show(["pin"], 600)
        self.p.hide()
        self.assertFalse(self.p.status()["showing"])
        self.assertEqual(self.api.player.shown[-1][:2], ("show-text", ""))

    def test_a_hide_in_the_middle_of_a_tick_can_never_leave_the_pin_qr_on_screen(self):
        self.auth._add_device("owner", "full")
        drawn = []
        self.api.player.osd_size = lambda: (1920, 1080)
        self.api.player.overlay = lambda oid, *a: drawn.append(oid)
        self.api.player.overlay_remove = lambda oid: None
        self.p.show(["view", "pin"], 60)
        real = self.p.manual_lines

        def slow(m):
            self.p.manual = None                      # a Hide lands while the text is being built
            return real(m)
        self.p.manual_lines = slow
        self.p.tick()
        self.assertNotIn(pinscreen.QR_IDS["pin"], drawn)
        self.assertEqual(self.p._qr_targets(None, {}), [])                    # with a device paired, never a PIN QR

    def test_on_request_the_full_pin_is_text_only(self):
        targets = self.p._qr_targets({"items": ["pin", "view"], "until": 1e9}, {"view": "123456"})
        self.assertEqual([t[0] for t in targets], [pinscreen.QR_IDS["view"]])

    def test_the_qr_bitmap_size_is_bounded_whatever_the_player_reports(self):
        sizes = []
        self.api.player.osd_size = lambda: (10 ** 6, 10 ** 6)
        self.api.player.overlay = lambda oid, x, y, w, h, px: sizes.append(w)
        self.api.player.overlay_remove = lambda oid: None
        self.p.show(["view"], 30)
        self.assertTrue(sizes and max(sizes) <= (41 + 8) * pinscreen.QR_MAX_SCALE)

    def test_start_and_stop_take_leftover_codes_off_the_screen(self):
        removed = []
        self.api.player.overlay_remove = lambda oid: removed.append(oid)
        self.p.start()
        self.p.stop()
        self.assertEqual(sorted(set(removed)), sorted(pinscreen.QR_IDS.values()))

    def test_bad_requests(self):
        for items in ([], None, "pin", ["full"], ["pin", "pin"], ["pin", "view", "live", "pin"], [1]):
            with self.assertRaises(ValueError, msg=str(items)):
                self.p.show(items, 60)
        for seconds in (0, 9, 3601, True, "60", 1.5):
            with self.assertRaises(ValueError, msg=str(seconds)):
                self.p.show(["pin"], seconds)

    def test_a_dead_player_does_not_raise(self):
        self.api.player.down = True
        self.p.show(["pin"], 60)                                                # must not raise
        self.assertTrue(self.p.status()["showing"])

    def test_a_caller_limited_to_the_guest_code_cannot_touch_anything_else_on_the_display(self):
        self.auth._add_device("owner", "full")
        only = ("view",)
        for items in (["pin"], ["live"], ["view", "pin"], ["view", "live"], ["address"], ["view", "address"]):
            with self.assertRaises(PermissionError, msg=str(items)):
                self.p.show(items, 60, by="presenter", only=only)
        self.assertFalse(self.p.status()["showing"])
        self.assertEqual(self.auth.list_joins(), [])                             # and no code was made on the way
        st = self.p.show(["view"], 60, by="presenter", only=only)
        self.assertEqual((st["items"], [(j["role"], j["by"]) for j in self.auth.list_joins()]), (["view"], [("view", "presenter")]))
        self.assertNotIn("Presenter", self.text())
        self.assertNotIn(self.auth.current_pin, self.text().replace(self.auth.list_joins()[0]["code"], ""))
        # the owner has the PIN and the presenter code up: a limited caller neither replaces them nor keeps them up longer
        self.p.show(["pin", "live", "view"], 100)
        with self.assertRaises(pinscreen.Busy):
            self.p.show(["view"], 3600, by="presenter", only=only)
        self.assertEqual((self.p.status()["items"], self.p.status()["seconds_left"]), (["pin", "live", "view"], 100))
        # its hide takes only the guest code off; the rest stays for the time the owner gave it
        st = self.p.hide(only=only)
        self.assertEqual((st["showing"], st["items"], st["seconds_left"]), (True, ["pin", "live"], 100))
        self.assertIn("Full access PIN", self.text())
        self.assertNotIn("Guest", self.text())
        self.assertFalse(self.p.hide()["showing"])                               # the owner's hide takes everything off
        for items in (["address"], ["view", "address"]):                         # the address alone is no secret: it may be replaced
            self.p.show(items, 100)
            self.assertEqual(self.p.show(["view"], 60, by="presenter", only=only)["items"], ["view"])
            self.p.hide()
        self.p.show(["view"], 60)
        self.assertFalse(self.p.hide(only=only)["showing"])
        self.t[0] += 500                                                         # an owner's display that ran out is not in the way
        self.p.show(["pin"], 20)
        self.t[0] += 21
        self.assertTrue(self.p.show(["view"], 60, by="presenter", only=only)["showing"])

    def test_only_fixed_words_digits_and_the_address_reach_the_display(self):
        """What a show request can vary is which items and for how long. Every character drawn is from the
        whitelist, whatever the box is called and whatever a device is named."""
        self.auth._add_device("${osd-ass-cc/0}{\\an5}OWNED\n$>", "live")
        p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="box${x}\n{\\b1}", clock=lambda: self.t[0])
        p.show(["view"], 60, by="presenter", only=("view",))
        text = self.api.player.shown[-1][1]
        self.assertNotIn("OWNED", text)
        self.assertRegex(text, r"\A[A-Za-z0-9 .:/_\-,\n]*\Z")              # the whitelist, plus the comma of the fixed labels
        self.assertNotRegex(text, r"[${}\\]")


class AccessApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.shown = []

        class Ipc:
            def request(inner, *cmd):
                self.shown.append(cmd)
        self.player.ipc = Ipc()
        self.api.pinscreen = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="box")

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_make_show_cancel(self):
        st, body, _ = self.post("/api/access/code", {"role": "view", "minutes": 30})
        self.assertEqual((st, body["codes"][0]["role"], body["codes"][0]["seconds_left"] > 1700), (200, "view", True))
        code = body["codes"][0]["code"]
        st, body, _ = self.post("/api/access/screen", {"show": True, "items": ["pin", "view", "live"], "seconds": 90})
        self.assertEqual((st, body["screen"]["showing"], sorted(c["role"] for c in body["codes"])), (200, True, ["live", "view"]))
        self.assertIn("show-text", self.shown[-1][0])
        st, body, _ = self.post("/api/access/screen", {"show": False})
        self.assertEqual((st, body["screen"]["showing"]), (200, False))
        self.assertEqual(self.post("/api/access/cancel", {"code": code})[0], 200)
        self.assertEqual(self.post("/api/access/cancel", {"code": code})[0], 404)
        self.assertEqual(self.post("/api/access/cancel", {"all": True})[1]["codes"], [])

    def test_a_guest_can_join_with_the_code_and_is_view_only(self):
        code = self.post("/api/access/code", {"role": "view"})[1]["codes"][0]["code"]
        st, body, _ = self.call("POST", "/api/pair", {"pin": code, "name": "guest"})
        self.assertEqual((st, body["device"]["role"]), (200, "view"))
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=body["token"])[0], 403)
        self.assertEqual(self.call("GET", "/api/access", token=body["token"])[0], 403)

    def test_bad_input_and_only_full_devices(self):
        for path, body in (("/api/access/code", {"role": "full"}), ("/api/access/code", {"role": "view", "minutes": 999}),
                           ("/api/access/screen", {"show": "yes"}), ("/api/access/screen", {"show": True, "items": ["x"]}),
                           ("/api/access/screen", {"show": True, "items": ["pin"], "seconds": 1})):
            self.assertEqual(self.post(path, body)[0], 400, (path, body))
        # Until D47 a presenter (live) was refused all four routes. It may now handle the guest code and nothing else
        # (PresenterGuestCodeTest has every refusal); a guest and an unpaired device are refused everything, as before.
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        for path, body in (("/api/access/code", {"role": "view"}), ("/api/access/screen", {"show": False}), ("/api/access/cancel", {"all": True}),
                           ("/api/access/cancel", {"role": "view"}), ("/api/access/screen", {"show": True, "items": ["view"]})):
            self.assertEqual(self.post(path, body, token=view)[0], 403, path)
            self.assertEqual(self.call("POST", path, body)[0], 401, path)
        self.assertEqual(self.call("GET", "/api/access", token=view)[0], 403)
        self.assertEqual(self.call("GET", "/api/access")[0], 401)
        self.assertEqual(self.auth.list_joins(), [])


class PresenterGuestCodeTest(AccessApiTest):
    """D47: a presenter (live) may make, see, show and end the GUEST code, and nothing more."""

    test_make_show_cancel = test_a_guest_can_join_with_the_code_and_is_view_only = test_bad_input_and_only_full_devices = None

    def setUp(self):
        super().setUp()
        self.live = self.post("/api/devices/invite", {"name": "staff", "role": "live"})[1]["token"]

    def as_live(self, path, body):
        return self.post(path, body, token=self.live)

    def codes(self):
        return {j["role"]: j for j in self.auth.list_joins()}

    def test_the_allowed_path_make_see_show_hide_end(self):
        st, body, _ = self.call("GET", "/api/access", token=self.live)
        self.assertEqual((st, body["codes"], body["screen"]), (200, [], {"showing": False, "items": [], "seconds_left": 0, "other": False}))
        st, body, _ = self.as_live("/api/access/code", {"role": "view", "minutes": 60})
        self.assertEqual(st, 200)
        code = body["codes"][0]
        self.assertEqual((code["role"], code["by"], code["uses_left"], 3500 < code["seconds_left"] <= 3600), ("view", "presenter", 20, True))
        st, body, _ = self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 300})
        self.assertEqual((st, body["screen"]["showing"], body["screen"]["items"], body["screen"]["other"]), (200, True, ["view"], False))
        self.assertIn(code["code"], self.shown[-1][1])
        self.assertNotIn(self.auth.current_pin, self.shown[-1][1].replace(code["code"], ""))
        st, body, _ = self.as_live("/api/access/screen", {"show": False})
        self.assertEqual((st, body["screen"]["showing"]), (200, False))
        # someone joins with it, and is a guest
        st, guest, _ = self.call("POST", "/api/pair", {"pin": code["code"], "name": "visitor"})
        self.assertEqual((st, guest["device"]["role"]), (200, "view"))
        self.assertEqual(self.call("GET", "/api/access", token=guest["token"])[0], 403)
        self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 300})
        st, body, _ = self.as_live("/api/access/cancel", {"role": "view"})
        self.assertEqual((st, body["codes"], body["screen"]["showing"]), (200, [], False))      # ended, and off the room screen
        self.assertEqual(self.as_live("/api/access/cancel", {"role": "view"})[0], 404)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": code["code"], "name": "late"})[0], 403)

    def test_show_makes_the_guest_code_if_there_is_none_within_the_presenter_limits(self):
        """Review finding 4: a code made by a presenter's show is one of the three lengths, with the presenter's
        uses, and never replaces a code."""
        for seconds, minutes in ((60, 15), (900, 15), (1020, 60), (3600, 60)):
            st, body, _ = self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": seconds})
            self.assertEqual(st, 200)
            code = self.codes()["view"]
            self.assertEqual((code["by"], code["uses_left"]), ("presenter", auth_mod.PRESENTER_JOIN_MAX_USES))
            self.assertTrue(minutes * 60 - 5 <= code["seconds_left"] <= minutes * 60, (seconds, code))
            self.assertNotIn("live", self.codes())
            self.assertEqual(self.as_live("/api/access/cancel", {"role": "view"})[0], 200)
        owner = self.post("/api/access/code", {"role": "view", "minutes": 100, "uses": 50})[1]["codes"][0]["code"]
        self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 3600})[0], 200)
        self.assertEqual((self.codes()["view"]["code"], self.codes()["view"]["by"]), (owner, "owner"))      # shown, not replaced
        for minutes in (15.0, 60.0, 120.0):                              # equal to a choice, and not a whole number
            self.assertEqual(self.as_live("/api/access/code", {"role": "view", "minutes": minutes, "replace": True})[0], 400, minutes)
        self.assertEqual(self.as_live("/api/access/code", {"role": "view", "uses": 20.0, "replace": True})[0], 400)
        self.assertEqual(self.codes()["view"]["code"], owner)

    def test_a_presenter_s_codes_per_hour_also_through_show_and_the_device_list_is_bounded(self):
        """Review finding 1, at the API."""
        for n in range(auth_mod.PRESENTER_CODES_PER_HOUR):
            if n % 2:
                self.assertEqual(self.as_live("/api/access/code", {"role": "view", "minutes": 15, "replace": True})[0], 200)
            else:
                self.as_live("/api/access/cancel", {"role": "view"})
                self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 60})[0], 200)
        active = self.codes()["view"]["code"]
        st, body, resp = self.as_live("/api/access/code", {"role": "view", "minutes": 15, "replace": True})
        self.assertEqual(st, 429)
        self.assertIn("6 guest codes were made in the last hour", body["error"])
        self.assertTrue(0 < body["retry_after"] <= 3601)
        self.assertEqual(self.codes()["view"]["code"], active)           # refused: the active code is still there
        self.as_live("/api/access/cancel", {"role": "view"})
        self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 60})[0], 429)
        self.assertEqual(self.auth.list_joins(), [])
        self.assertEqual(self.post("/api/access/code", {"role": "view"})[0], 200)       # the owner is not counted
        # the device list: full for guests, never for the owner's PIN
        code = self.codes()["view"]["code"]
        have = sum(1 for d in self.settings.data["devices"] if d["role"] != "full")
        self.settings.data["devices"] += [{"id": "f%07d" % i, "name": "x", "role": "view", "token_hash": "0" * 64, "created": 1}
                                          for i in range(auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED - have)]
        st, body, _ = self.call("POST", "/api/pair", {"pin": code, "name": "one too many"})
        self.assertEqual(st, 409)
        self.assertIn("too many devices are paired", body["error"])
        self.assertEqual(self.post("/api/devices/invite", {"name": "x", "role": "view"})[0], 409)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "the owner's other phone"})[0], 200)

    def test_what_a_presenter_does_is_in_the_log_with_its_device_id(self):
        """Review finding 5."""
        lines = []
        self.api.log = lines.append
        did = self.auth.authenticate(self.live)["id"]
        self.post("/api/access/code", {"role": "view"})
        self.assertEqual(lines, [])                                      # the owner's own are not logged here
        self.as_live("/api/access/code", {"role": "view", "minutes": 60, "replace": True})
        self.assertIn("in the place of the one the owner made", lines[-1])
        self.as_live("/api/access/code", {"role": "view", "minutes": 15, "uses": 3, "replace": True})
        self.assertIn("guest code made by presenter device %s" % did, lines[-1])
        self.assertIn("15 minutes, 3 uses, in the place of the one the presenter made", lines[-1])
        self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 120})
        self.assertIn("guest code put on the room screen for 120 s by presenter device %s" % did, lines[-1])
        self.assertNotIn("was made for it", lines[-1])
        self.as_live("/api/access/screen", {"show": False})
        self.assertIn("guest code taken off the room screen by presenter device %s" % did, lines[-1])
        self.as_live("/api/access/cancel", {"role": "view"})
        self.assertIn("guest code ended by presenter device %s" % did, lines[-1])
        self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 60})
        self.assertIn("a guest code was made for it", lines[-1])
        self.as_live("/api/access/cancel", {"role": "view"})
        self.as_live("/api/access/code", {"role": "view", "minutes": 60})
        self.assertNotIn("in the place of", lines[-1])
        self.assertEqual(len(lines), 8)
        n = len(lines)
        for path, body in (("/api/access/code", {"role": "live"}), ("/api/access/screen", {"show": True, "items": ["pin"]}), ("/api/access/cancel", {"all": True})):
            self.as_live(path, body)
        self.assertEqual(len(lines), n)                                  # a refusal did nothing, so it says nothing
        for line in lines:
            self.assertNotRegex(line, r"[0-9]{6}")                       # never a code's digits

    def test_never_a_presenter_code(self):
        for body in ({"role": "live"}, {"role": "live", "minutes": 15}, {"role": "full"}, {"role": ["view", "live"]}, {"role": "LIVE"}, {}, {"role": None},
                     {"role": "live", "replace": True}):
            st, out, _ = self.as_live("/api/access/code", body)
            self.assertEqual(st, 403, (body, out))
        self.assertEqual(self.auth.list_joins(), [])
        for body in ({"show": True, "items": ["live"]}, {"show": True, "items": ["view", "live"]}, {"show": True, "items": ["live", "view"], "seconds": 60}):
            self.assertEqual(self.as_live("/api/access/screen", body)[0], 403, body)
        self.assertEqual(self.auth.list_joins(), [])                    # showing would have made the code: it did not
        self.assertFalse(self.api.pinscreen.status()["showing"])
        # the owner's presenter code: not listed, not as a QR code, not ended, not testable by its digits
        self.post("/api/access/code", {"role": "live"})
        self.post("/api/access/code", {"role": "view"})
        secret = self.codes()["live"]["code"]
        st, body, _ = self.call("GET", "/api/access", token=self.live)
        self.assertEqual([c["role"] for c in body["codes"]], ["view"])
        self.assertNotIn(secret, str(body))
        self.assertEqual(self.call("GET", "/api/qr.svg?for=live", token=self.live)[0], 403)
        for body in ({"role": "live"}, {"code": secret}, {"code": "000000"}, {"all": True}, {"role": "view", "all": True}, {"role": "view", "code": secret}, {}):
            st, out, _ = self.as_live("/api/access/cancel", body)
            self.assertEqual(st, 403, (body, out))                      # the same answer for a right and a wrong guess
            self.assertNotIn(secret, str(out))
        self.assertEqual(sorted(self.codes()), ["live", "view"])
        for path, body in (("/api/access/code", {"role": "view", "replace": True}), ("/api/access/cancel", {"role": "view"}),
                           ("/api/access/screen", {"show": True, "items": ["view"]}), ("/api/access/screen", {"show": False})):
            st, out, _ = self.as_live(path, body)
            self.assertEqual(st, 200, (path, out))
            self.assertNotIn(secret, str(out))
        self.assertEqual(self.codes()["live"]["code"], secret)          # and none of that touched it

    def test_never_the_pin(self):
        pin = self.auth.current_pin
        for body in ({"show": True, "items": ["pin"]}, {"show": True, "items": ["pin", "view"], "seconds": 60}, {"show": True, "items": ["view", "pin", "live"]}):
            self.assertEqual(self.as_live("/api/access/screen", body)[0], 403, body)
        self.assertFalse(self.api.pinscreen.status()["showing"])
        self.assertEqual(self.as_live("/api/pin/rotate", {})[0], 403)
        self.assertEqual(self.as_live("/api/pin/unlock", {})[0], 403)
        self.assertEqual(self.auth.current_pin, pin)
        # the owner has the PIN on the display: a presenter is told only that "something else" is there, cannot
        # replace it or keep it up longer, and cannot take it off
        self.post("/api/access/screen", {"show": True, "items": ["pin", "view"], "seconds": 120})
        st, body, _ = self.call("GET", "/api/access", token=self.live)
        self.assertEqual((body["screen"]["items"], body["screen"]["other"]), (["view"], True))
        self.assertNotIn(pin, str(body))
        self.assertNotIn("pin", str(body["screen"]))
        self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 3600})[0], 409)
        self.assertLessEqual(self.api.pinscreen.status()["seconds_left"], 120)
        st, body, _ = self.as_live("/api/access/screen", {"show": False})
        self.assertEqual((st, body["screen"]["showing"], body["screen"]["other"]), (200, False, True))
        self.assertEqual(self.api.pinscreen.status()["items"], ["pin"])            # the owner's PIN stays up
        self.assertIn("Full access PIN", self.shown[-1][1])

    def test_what_a_presenter_is_told_about_the_room_screen_matches_what_it_may_do(self):
        """ "other" is true exactly when a show would be refused: the PIN or the presenter code is up. The plain
        address is not "other", and can be replaced."""
        for items, other in ((["address"], False), (["view", "address"], False), (["live"], True), (["pin", "address"], True), (["view", "live"], True)):
            self.post("/api/access/screen", {"show": True, "items": items, "seconds": 100})
            told = self.call("GET", "/api/access", token=self.live)[1]["screen"]
            self.assertEqual(told["other"], other, items)
            st = self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 60})[0]
            self.assertEqual(st, 409 if other else 200, items)
            self.post("/api/access/screen", {"show": False})

    def test_never_a_permanent_link_and_never_device_removal(self):
        devices = [d["id"] for d in self.auth.list_devices()]
        for role in ("view", "live"):
            self.assertEqual(self.as_live("/api/devices/invite", {"name": "x", "role": role})[0], 403)
        self.assertEqual(self.call("GET", "/api/devices", token=self.live)[0], 403)
        for did in devices:
            self.assertEqual(self.as_live("/api/devices/revoke", {"id": did})[0], 403)
        self.assertEqual([d["id"] for d in self.auth.list_devices()], devices)

    def test_limits_minutes_uses_and_one_code_that_is_not_replaced_by_accident(self):
        for body in ({"minutes": 16}, {"minutes": 121}, {"minutes": 1}, {"minutes": 0}, {"minutes": "60"}, {"minutes": True}, {"minutes": 60.5}, {"minutes": None},
                     {"minutes": [60]}, {"uses": 21}, {"uses": 50}, {"uses": 0}, {"uses": -1}, {"uses": True}, {"uses": "5"}, {"uses": 2.5}):
            st, out, _ = self.as_live("/api/access/code", dict({"role": "view"}, **body))
            self.assertEqual(st, 400, (body, out))
        self.assertEqual(self.auth.list_joins(), [])
        for minutes in auth_mod.PRESENTER_JOIN_MINUTES:
            st, out, _ = self.as_live("/api/access/code", {"role": "view", "minutes": minutes, "uses": 20, "replace": True})
            self.assertEqual(st, 200, out)
            self.assertTrue(minutes * 60 - 5 <= out["codes"][0]["seconds_left"] <= minutes * 60)
        self.assertEqual(max(auth_mod.PRESENTER_JOIN_MINUTES), 120)
        self.assertEqual(self.as_live("/api/access/code", {"role": "view", "uses": 1, "replace": True})[1]["codes"][0]["uses_left"], 1)
        self.assertEqual(len(self.auth.list_joins()), 1)                # still at most one per role
        # the owner's code is in use: a presenter's new code needs an explicit "replace"
        owner = self.post("/api/access/code", {"role": "view", "minutes": 100, "uses": 50})[1]["codes"][0]
        self.assertEqual(owner["by"], "owner")
        st, out, _ = self.call("GET", "/api/access", token=self.live)
        self.assertEqual((out["codes"][0]["code"], out["codes"][0]["by"]), (owner["code"], "owner"))      # it may see and show it
        for body in ({"role": "view"}, {"role": "view", "replace": False}, {"role": "view", "replace": "yes"}, {"role": "view", "replace": 1}):
            self.assertEqual(self.as_live("/api/access/code", body)[0], 409, body)
        self.assertEqual(self.codes()["view"]["code"], owner["code"])
        self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": 60})[0], 200)
        self.assertEqual(self.codes()["view"]["code"], owner["code"])   # showing it does not replace it
        st, out, _ = self.as_live("/api/access/code", {"role": "view", "replace": True})
        self.assertEqual((st, out["codes"][0]["by"], out["codes"][0]["code"] != owner["code"]), (200, "presenter", True))
        # a code is still 6 digits, in memory only, and a wrong guess is throttled as before
        self.assertRegex(out["codes"][0]["code"], r"^[0-9]{6}$")
        with open(self.settings.path) as f:
            self.assertNotIn(out["codes"][0]["code"], f.read())
        wrong = "%06d" % ((int(out["codes"][0]["code"]) + 1) % 10 ** 6)
        got = [self.call("POST", "/api/pair", {"pin": wrong, "name": "x"})[0] for _ in range(auth_mod.PER_CLIENT_FAILS + 1)]
        self.assertEqual((got[0], got[-1]), (403, 429))

    def test_csrf_and_seconds(self):
        for path, body in (("/api/access/code", {"role": "view"}), ("/api/access/cancel", {"role": "view"}),
                           ("/api/access/screen", {"show": True, "items": ["view"]}), ("/api/access/screen", {"show": False})):
            self.assertEqual(self.call("POST", path, body, token=self.live, csrf=False)[0], 403, path)
            st = self.call("POST", path, body, token=self.live, headers={"Origin": "http://attacker.example"})[0]
            self.assertEqual(st, 403, path)
        self.assertEqual(self.auth.list_joins(), [])
        self.assertFalse(self.api.pinscreen.status()["showing"])
        for seconds in (0, 9, 3601, True, "60", 1.5):
            self.assertEqual(self.as_live("/api/access/screen", {"show": True, "items": ["view"], "seconds": seconds})[0], 400, seconds)
        for body in ({"show": "yes"}, {"show": True}, {"show": True, "items": "view"}, {"show": True, "items": []}, {"show": True, "items": ["view", "view"]}):
            self.assertEqual(self.as_live("/api/access/screen", body)[0], 400, body)

    def test_a_show_request_cannot_draw_text_on_the_display(self):
        evil = "${osd-ass-cc/0}{\\an5}OWNED"
        for body in ({"show": True, "items": ["view"], "text": evil}, {"show": True, "items": ["view"], "label": evil, "title": evil, "name": evil, "code": evil}):
            self.assertEqual(self.as_live("/api/access/screen", body)[0], 200)
            self.assertNotIn("OWNED", str(self.shown))
        for body in ({"show": True, "items": [evil]}, {"show": True, "items": ["view", evil]}, {"show": True, "items": {"view": evil}}):
            self.assertIn(self.as_live("/api/access/screen", body)[0], (400, 403))
        self.as_live("/api/access/code", {"role": "view", "replace": True, "code": "OWNED1", "by": "owner", "name": evil})
        self.assertEqual(self.codes()["view"]["by"], "presenter")      # who made it is not for the caller to say
        self.assertRegex(self.codes()["view"]["code"], r"^[0-9]{6}$")
        for cmd in self.shown:
            if cmd[0] == "show-text":
                self.assertNotIn("OWNED", cmd[1])
                self.assertRegex(cmd[1], r"\A[A-Za-z0-9 .:/_\-,\n]*\Z")     # the whitelist, plus the comma of the fixed labels

    def test_any_live_role_device_may_however_it_joined(self):
        code = self.post("/api/access/code", {"role": "live"})[1]["codes"][0]["code"]
        st, joined, _ = self.call("POST", "/api/pair", {"pin": code, "name": "joined by code"})
        self.assertEqual((st, joined["device"]["role"]), (200, "live"))
        st, body, _ = self.post("/api/access/code", {"role": "view", "minutes": 15}, token=joined["token"])
        self.assertEqual((st, body["codes"][0]["by"]), (200, "presenter"))
        self.assertEqual(len(body["codes"]), 1)                        # and it does not see the presenter code it joined with
        # removed by the owner: nothing more
        self.assertEqual(self.post("/api/devices/revoke", {"id": joined["device"]["id"]})[0], 200)
        self.assertEqual(self.post("/api/access/code", {"role": "view", "replace": True}, token=joined["token"])[0], 401)

    def test_a_device_removed_while_its_request_ran_leaves_nothing_behind(self):
        """Review finding 3: no code made by it stays, and the owner's code is not destroyed by it."""
        from unittest import mock
        dev = self.auth.authenticate(self.live)
        self.auth.revoke(dev["id"])                                     # its token was checked a moment ago; now it is gone
        self.assertEqual(self.api.handle("POST", "/api/access/code", {"role": "view"}, dev, "192.168.1.9")[0], 401)
        self.assertEqual(self.auth.list_joins(), [])
        self.assertEqual(self.api.handle("POST", "/api/access/screen", {"show": True, "items": ["view"]}, dev, "192.168.1.9")[0], 401)
        self.assertFalse(self.api.pinscreen.status()["showing"])
        self.assertEqual(self.auth.list_joins(), [])                    # the show made no code that stays
        owner = self.post("/api/access/code", {"role": "view"})[1]["codes"][0]["code"]
        self.assertEqual(self.api.handle("POST", "/api/access/code", {"role": "view", "replace": True}, dev, "192.168.1.9")[0], 401)
        self.assertEqual([j["code"] for j in self.auth.list_joins()], [owner])       # the owner's code was not replaced
        self.assertEqual(self.api.handle("POST", "/api/access/screen", {"show": True, "items": ["view"]}, dev, "192.168.1.9")[0], 401)
        self.assertEqual([j["code"] for j in self.auth.list_joins()], [owner])
        self.assertFalse(self.api.pinscreen.status()["showing"])
        self.auth.cancel_join(None)
        # removed in the instant between the check under the lock and the end of the request: what the call made is taken back
        for path, body in (("/api/access/screen", {"show": True, "items": ["view"]}), ("/api/access/code", {"role": "view"})):
            with mock.patch.object(self.api, "_still_paired", side_effect=[True, False]):
                self.assertEqual(self.api.handle("POST", path, body, dev, "192.168.1.9")[0], 401, path)
            self.assertEqual(self.auth.list_joins(), [], path)
            self.assertFalse(self.api.pinscreen.status()["showing"])

    def test_the_owner_s_own_requests_are_as_before(self):
        st, body, _ = self.post("/api/access/code", {"role": "view", "minutes": 7, "uses": 50})
        self.assertEqual((st, body["codes"][0]["by"], body["codes"][0]["uses_left"]), (200, "owner", 50))
        self.assertEqual(self.post("/api/access/code", {"role": "view"})[0], 200)      # replaces, without being asked to
        self.post("/api/access/code", {"role": "live"})
        st, body, _ = self.call("GET", "/api/access", token=self.full)
        self.assertEqual((sorted(c["role"] for c in body["codes"]), "other" in body["screen"]), (["live", "view"], False))
        self.assertEqual(self.post("/api/access/cancel", {"role": "live"})[0], 200)
        self.assertEqual(self.post("/api/access/cancel", {"role": "live"})[0], 404)
        self.assertEqual(self.post("/api/access/screen", {"show": True, "items": ["pin", "address"], "seconds": 30})[0], 200)


class HostCheckTest(ServerBase):
    def test_only_ip_addresses_and_our_own_names_are_answered(self):
        from pvj import server
        names = {"localhost", "box", "box.local", "studio.example"}
        for good in ("192.168.0.169", "192.168.0.169:80", "[fe80::1]", "[::1]:8080", "localhost:8080", "box.local", "BOX.LOCAL", "studio.example", "box."):
            self.assertTrue(server.host_allowed(good, names), good)
        for bad in ("evil.example", "evil.example:80", "box.local.evil.example", "", "[::1", "[::1]x", "192.168.0.1:abc", "a" * 300):
            self.assertFalse(server.host_allowed(bad, names), bad)
        self.assertTrue(server.host_allowed(None, names))                           # no header at all: not a browser

    def test_a_rebinding_page_is_refused_before_it_reaches_the_api(self):
        st, body, _ = self.call("POST", "/api/pair", {"pin": "0000", "name": "x"}, headers={"Host": "attacker.example", "Origin": "http://attacker.example"})
        self.assertEqual(st, 421)
        self.assertEqual(self.auth._fails, {})                                      # it did not even count as a guess
        self.assertEqual(self.call("GET", "/api/hello", headers={"Host": "attacker.example"})[0], 421)

    def test_extra_names_from_the_environment(self):
        from pvj import server
        self.assertIn("screen.studio", server.allowed_names({"PVJ_ALLOWED_HOSTS": " Screen.Studio , ,other"}))


class QrEndpointTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def test_panel_and_code_qr_codes_are_svg_for_full_devices_only(self):
        st, body, resp = self.call("GET", "/api/qr.svg?for=panel", token=self.full)
        self.assertEqual((st, resp.getheader("Content-Type")), (200, "image/svg+xml"))
        self.assertTrue(body.startswith(b"<svg"))
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=self.full)[0], 404)          # no code yet
        self.call("POST", "/api/access/code", {"role": "view"}, token=self.full)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=self.full)[0], 200)
        for target in ("pin", "full", "", "../x"):
            self.assertEqual(self.call("GET", "/api/qr.svg?for=" + target, token=self.full)[0], 400, target)
        # Until D47 a presenter got no QR code at all. Now: the panel address and the guest code, never the presenter code.
        self.call("POST", "/api/access/code", {"role": "live"}, token=self.full)
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/qr.svg?for=panel", token=live)[0], 200)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=live)[0], 200)
        for target in ("live", "pin", "full", ""):
            self.assertEqual(self.call("GET", "/api/qr.svg?for=" + target, token=live)[0], 403, target)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=live", token=self.full)[0], 200)
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        for target in ("panel", "view", "live"):
            self.assertEqual(self.call("GET", "/api/qr.svg?for=" + target, token=view)[0], 403, target)
            self.assertEqual(self.call("GET", "/api/qr.svg?for=" + target)[0], 401, target)

    def test_a_bad_host_header_cannot_be_put_into_a_qr_code(self):
        st, _, _ = self.call("GET", "/api/qr.svg?for=panel", token=self.full, headers={"Host": "evil.example/<script>"})
        self.assertEqual(st, 421)                                          # refused before it reaches the QR code

    @unittest.skipUnless(__import__("shutil").which("zbarimg"), "needs zbar")
    def test_the_code_qr_decodes_to_the_join_link(self):
        import subprocess
        from pvj import qr
        code = self.call("POST", "/api/access/code", {"role": "live"}, token=self.full)[1]["codes"][0]["code"]
        text = "http://127.0.0.1:%d/#code=%s" % (self.port, code)
        path = os.path.join(self.tmp, "c.png")
        with open(path, "wb") as f:
            f.write(qr.png(qr.encode(text)))
        out = subprocess.run(["zbarimg", "--quiet", "--raw", path], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, text)

    def test_a_new_guest_link_comes_with_its_qr_code(self):
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view", "origin": "http://192.168.0.5"}, token=self.full)
        self.assertEqual(st, 200)
        self.assertTrue(body["qr_svg"].startswith("<svg"))
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view", "origin": "javascript:alert(1)"}, token=self.full)
        self.assertNotIn("qr_svg", body)


class OverlayTest(unittest.TestCase):
    def test_the_qr_code_is_drawn_in_the_top_right_and_removed_when_the_first_device_pairs(self):
        settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        settings.load()
        a = Auth(settings, rotate_on_start=True)
        api = PinApi()
        drawn = []
        api.player.osd_size = lambda: (2560, 1440)
        api.player.overlay = lambda oid, x, y, w, h, px: drawn.append(("add", oid, x, y, w, h, len(px)))
        api.player.overlay_remove = lambda oid: drawn.append(("remove", oid))
        p = pinscreen.PinScreen(api, a, log=lambda *_: None, hostname="box")
        self.assertTrue(p.tick())
        adds = [d for d in drawn if d[0] == "add"]
        self.assertEqual(len(adds), 1)
        _, oid, x, y, w, h, n = adds[0]
        self.assertEqual((oid, y, w, h, n), (pinscreen.QR_IDS["pin"], 40, w, w, w * w * 4))
        self.assertEqual(x + w, 2560 - 40)                       # right edge, with a margin
        self.assertGreater(w, 200)                                 # big enough to scan from across a room
        p.tick()
        self.assertEqual(len([d for d in drawn if d[0] == "add"]), 1)    # unchanged: not redrawn every tick
        a._add_device("owner", "full")
        p.tick()
        self.assertIn(("remove", pinscreen.QR_IDS["pin"]), drawn)


if __name__ == "__main__":
    unittest.main()
