# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The owner PIN read back from the panel, and Log out (D77)."""
import json
import re
import unittest

from pvj import auth as auth_mod, pinscreen as pinscreen_mod
from tests.test_server import ServerBase
from tests.test_support import LAN, TUNNEL, SupportBase

EXPIRED = "pvj_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def alone(pin):
    """The PIN as a number of its own in a text (not inside a longer number such as a time stamp)."""
    return re.compile(r"(?<![0-9])%s(?![0-9])" % re.escape(pin))


class PinShowTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.log = self.lines.append
        self.full, _ = self.pair("owner laptop")
        self.live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=self.full)[1]["token"]
        self.view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.full)[1]["token"]

    def test_the_owner_gets_the_pin_and_nobody_else_does(self):
        st, body, r = self.call("POST", "/api/pin/show", {}, token=self.full)
        self.assertEqual((st, body), (200, {"known": True, "pin": self.pin}))
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        for token in (self.live, self.view):
            st, body, _ = self.call("POST", "/api/pin/show", {}, token=token)
            self.assertEqual(st, 403, body)
            self.assertNotIn(self.pin, json.dumps(body))
        self.assertEqual(self.call("POST", "/api/pin/show", {})[0], 401)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full, csrf=False)[0], 403)   # like every POST
        self.assertEqual(self.call("GET", "/api/pin/show", token=self.full)[0], 405)                   # never a GET: not in an address, never cached

    def test_a_device_removed_a_moment_ago_gets_nothing(self):
        dev = self.auth.authenticate(self.full)
        self.auth.revoke(dev["id"])
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 401)
        # the window between the token check and the handler: the device is gone by the time the handler runs
        token2, _ = self.pair("second")
        dev2 = self.auth.authenticate(token2)
        st, body = self.api.handle("POST", "/api/pin/show", {}, dev2, LAN)
        self.assertEqual(st, 200)
        self.auth.revoke(dev2["id"])
        st, body = self.api.handle("POST", "/api/pin/show", {}, dev2, LAN)      # a stale device record
        self.assertEqual(st, 401)
        self.assertNotIn(self.pin, json.dumps(body))

    def test_when_this_run_does_not_know_the_pin_it_says_so_and_new_pin_shows_one(self):
        self.auth.current_pin = None            # what Auth(settings) is after a start that made no new PIN (test_auth)
        st, body, _ = self.call("POST", "/api/pin/show", {}, token=self.full)
        self.assertEqual((st, body), (200, {"known": False, "pin": None}))
        self.assertTrue(any("not known" in line for line in self.lines))
        st, body, _ = self.call("POST", "/api/pin/rotate", {}, token=self.full)
        self.assertEqual(st, 200)
        new = body["pin"]
        st, body, _ = self.call("POST", "/api/pin/show", {}, token=self.full)
        self.assertEqual((st, body), (200, {"known": True, "pin": new}))

    def test_the_rate_limit_and_the_journal_line_without_the_pin(self):
        self.auth._clock = Clock()
        dev = self.auth.authenticate(self.full)
        for _ in range(auth_mod.PIN_SHOWS):
            self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 200)
        st, body, r = self.call("POST", "/api/pin/show", {}, token=self.full)
        self.assertEqual(st, 429, body)
        self.assertTrue(r.getheader("Retry-After"))
        self.assertNotIn(self.pin, json.dumps(body))
        token2, _ = self.pair("second owner")                     # counted per device
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=token2)[0], 200)
        self.auth._clock.t += auth_mod.PIN_SHOW_WINDOW + 1
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 200)
        shown = [line for line in self.lines if "owner PIN" in line]
        self.assertEqual(len(shown), auth_mod.PIN_SHOWS + 2)      # one line per answer that carried the PIN, none for the 429
        for line in shown:
            self.assertIn(dev["id"], line) if "owner laptop" in line else self.assertIn("second owner", line)
            self.assertNotIn(self.pin, line.replace(dev["id"], ""))   # (the id is random hex and may hold any four digits)

    def test_the_pin_is_in_no_other_answer(self):
        """Every GET the API has, the diagnostics file and a settings export, read as the owner, with the PIN set
        to digits that are no port, size or year: the PIN is in none of them, and the one route that gives it does."""
        self.auth.set_pin("7351")
        wanted = alone("7351")
        self.assertRegex(json.dumps(self.call("POST", "/api/pin/show", {}, token=self.full)[1]), wanted)
        gets = sorted(p for (m, p) in self.api.routes() if m == "GET")
        self.assertIn("/api/status", gets)
        self.assertIn("/api/access", gets)
        self.assertIn("/api/devices", gets)
        self.assertIn("/api/system/diagnostics", gets)
        for path in gets + ["/api/qr.svg?for=panel"]:          # (the preview is a picture, and the fake player takes none)
            st, body, _ = self.call("GET", path, token=self.full)
            text = body if isinstance(body, (str, bytes)) else json.dumps(body)
            if isinstance(text, bytes):
                text = text.decode("latin-1")
            self.assertNotRegex(text, wanted, path)
        st, body, _ = self.call("POST", "/api/system/settings/export", {}, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertNotRegex(json.dumps(body), wanted, "the export")
        self.assertNotRegex(json.dumps(self.call("GET", "/api/status", token=self.live)[1]), wanted)


class LogoutTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.log = self.lines.append
        self.full, self.pair_reply = self.pair("owner laptop")
        self.full2, _ = self.pair("owner phone")
        self.live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=self.full)[1]["token"]
        self.view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.full)[1]["token"]

    def names(self):
        return sorted(d["name"] for d in self.call("GET", "/api/devices", token=self.full)[1]["devices"])

    def test_every_role_logs_itself_out_and_only_itself(self):
        for token, name in ((self.view, "guest"), (self.live, "presenter"), (self.full2, "owner phone")):
            self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)
            st, body, r = self.call("POST", "/api/logout", {}, token=token)
            self.assertEqual((st, body), (200, {"ok": True, "ended": True}))
            self.assertEqual(r.getheader("Set-Cookie"), EXPIRED)
            self.assertEqual(self.call("GET", "/api/status", token=token)[0], 401)       # the token is dead
            self.assertNotIn(name, self.names())                                          # the device is gone from the list
            st, body, r = self.call("POST", "/api/logout", {}, token=token)               # a second press
            self.assertEqual((st, body), (200, {"ok": True, "ended": False}))
            self.assertEqual(r.getheader("Set-Cookie"), EXPIRED)                          # still clears the cookie
        self.assertEqual(self.names(), ["owner laptop"])                                  # the other device is untouched
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 200)
        self.assertEqual(len([line for line in self.lines if "logged out" in line]), 3)

    def test_without_the_request_header_nothing_happens(self):
        st, body, r = self.call("POST", "/api/logout", {}, token=self.view, csrf=False)
        self.assertEqual(st, 403)
        self.assertIsNone(r.getheader("Set-Cookie"))
        self.assertEqual(self.call("GET", "/api/status", token=self.view)[0], 200)
        self.assertEqual(self.call("GET", "/api/logout", token=self.view)[0], 405)

    def test_the_cookie_path_as_a_browser_uses_it(self):
        cookie = self.pair_reply.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(self.call("GET", "/api/status", headers={"Cookie": cookie})[0], 200)
        st, body, r = self.call("POST", "/api/logout", {}, headers={"Cookie": cookie, "Origin": "http://127.0.0.1:%d" % self.port,
                                                                    "Host": "127.0.0.1:%d" % self.port})
        self.assertEqual(st, 200, body)
        self.assertEqual(r.getheader("Set-Cookie"), EXPIRED)
        # a replayed cookie after logout
        self.assertEqual(self.call("GET", "/api/status", headers={"Cookie": cookie})[0], 401)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, headers={"Cookie": cookie})[0], 401)
        # a cross-site page: the browser would not send the cookie (SameSite=Strict); if it did, no header, no logout
        self.assertEqual(self.call("POST", "/api/logout", {}, headers={"Cookie": cookie, "Origin": "http://evil.example"})[0], 403)

    def test_a_device_the_owner_removed_meanwhile_is_logged_out_the_same_way(self):
        dev = self.auth.authenticate(self.view)
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": dev["id"]}, token=self.full)[0], 200)
        st, body, r = self.call("POST", "/api/logout", {}, token=self.view)
        self.assertEqual((st, body), (200, {"ok": True, "ended": False}))
        self.assertEqual(r.getheader("Set-Cookie"), EXPIRED)

    def test_the_last_owner_may_log_out_and_the_pin_screen_returns(self):
        for token in (self.view, self.live, self.full2):
            self.assertEqual(self.call("POST", "/api/logout", {}, token=token)[0], 200)
        self.assertEqual(self.names(), ["owner laptop"])
        st, body, _ = self.call("POST", "/api/pin/show", {}, token=self.full)         # the panel shows it in the question first
        self.assertEqual(body["pin"], self.pin)
        self.assertEqual(self.call("POST", "/api/logout", {}, token=self.full)[0], 200)
        self.assertEqual(self.auth.list_devices(), [])
        self.player.running = True                                                     # the player idle on its own screen
        self.assertTrue(pinscreen_mod.PinScreen(self.api, self.auth).auto_wanted())    # the way back in: the PIN on the display
        self.assertTrue(self.auth.pair(self.pin, "again", LAN)[0])                     # and the PIN still pairs

    def test_log_out_every_device_is_the_owners_alone(self):
        for token in (self.view, self.live):
            st, body, _ = self.call("POST", "/api/logout", {"all": True}, token=token)
            self.assertEqual(st, 403, body)
        self.assertEqual(len(self.names()), 4)
        st, body, r = self.call("POST", "/api/logout", {"all": True}, token=self.full)
        self.assertEqual((st, body), (200, {"ok": True, "ended": True, "all": True}))
        self.assertEqual(r.getheader("Set-Cookie"), EXPIRED)
        self.assertEqual(self.auth.list_devices(), [])
        for token in (self.full, self.full2, self.live, self.view):
            self.assertEqual(self.call("GET", "/api/status", token=token)[0], 401)
        self.assertTrue(any("every device (4) logged out" in line for line in self.lines))


class SupportSessionTest(SupportBase):
    def login(self, role="full"):
        self.ready()
        st, started = self.start(role=role)
        self.assertEqual(st, 200, started)
        code = started["code"]
        st, body = self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)
        self.assertEqual(st, 200, body)
        return body["token"], self.api.support.authenticate(body["token"])

    def test_support_never_gets_the_pin_whatever_its_role(self):
        token, dev = self.login("full")
        self.assertEqual(dev["role"], "full")
        st, body = self.h("POST", "/api/pin/show", {}, dev, TUNNEL)
        self.assertEqual(st, 403, body)
        self.assertNotIn(self.pin, json.dumps(body))
        # nor can it make itself lasting access (what the guard already refused; kept here beside the new route)
        for path, body in ((("POST", "/api/pin/rotate"), {}), (("POST", "/api/devices/invite"), {"name": "x", "role": "live"}),
                           (("POST", "/api/access/code"), {"role": "view"}), (("POST", "/api/logout"), {"all": True})):
            self.assertEqual(self.h(path[0], path[1], body, dev, TUNNEL)[0], 403, path)
        self.assertEqual(len(self.auth.list_devices()), 2)

    def test_support_logs_out_its_own_login_and_the_session_goes_on(self):
        token, dev = self.login("live")
        self.assertEqual(self.h("GET", "/api/status", {}, dev, TUNNEL)[0], 200)
        st, body = self.h("POST", "/api/logout", {}, dev, TUNNEL)
        self.assertEqual((st, body), (200, {"ok": True, "ended": True}))
        self.assertIsNone(self.api.support.authenticate(token))                        # that login is over
        self.assertTrue(self.api.support.banner()["active"])                           # the session is not
        self.assertEqual(self.api.support.session["logins"], 1)                        # and the login still counts
        self.assertEqual(len(self.auth.list_devices()), 2)                             # no studio device was touched
        self.assertEqual(self.h("POST", "/api/logout", {}, None, TUNNEL)[0], 200)      # a second press: the same answer
        # the studio's own Log out works as before while support is in
        self.assertEqual(self.h("POST", "/api/logout", {}, self.live_dev, LAN)[0], 200)
        self.assertEqual(len(self.auth.list_devices()), 1)


if __name__ == "__main__":
    unittest.main()
