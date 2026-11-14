# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A client that still uses the names from before the rename keeps working for one release (D138).

The protocol names moved from pvj to mctl (D135, kind protocol): the OSC family /pvj/... is now /mctl/..., the header
X-PVJ-Request is X-MCTL-Request and the login cookie pvj_token is mctl_token (__Host-pvj_token is __Host-mctl_token).
For one release the node still reads the old names: an old TouchOSC layout, a script with the old header and a
browser that holds the old cookie all work, and the browser is moved to the new cookie without a new pairing. What
the node sends is new only. Remove this file with the bridge, in the release after the cutover."""
import os
import unittest

from pvj import osc, oscfeedback, peer
from tests.test_osc import ServerLogicTest, msg
from tests.test_server import ServerBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def cookies(r):
    """[(name, value, the rest)] of every Set-Cookie of a response."""
    out = []
    for k, v in r.getheaders():
        if k.lower() == "set-cookie":
            first, _, rest = v.partition(";")
            name, _, value = first.partition("=")
            out.append((name, value, rest))
    return out


class OldOscAddressesTest(ServerLogicTest):
    def test_the_old_family_means_the_new_one(self):
        for old, new in [("/pvj/stop", "/mctl/stop"), ("/pvj/pad/1/3", "/mctl/pad/1/3"), ("/pvj/scene/2", "/mctl/scene/2"),
                         ("/pvj/strobe/rate", "/mctl/strobe/rate"), ("/pvj/state", "/mctl/state"), ("/pvj", "/mctl")]:
            self.assertEqual(osc.current(old), new)
        for same in ("/mctl/stop", "/stopall", "/pvjx/stop", "/x/pvj/stop", "/fastforward", "/"):
            self.assertEqual(osc.current(same), same, "only the old family itself is moved")
        for args in ((1.0,), (50.0,), ()):
            for name in ("stop", "opacity", "pad/1/3", "fade", "scene/1", "shader/speed"):
                self.assertEqual(osc.translate("/pvj/" + name, args), osc.translate("/mctl/" + name, args), name)

    def test_an_old_layout_still_plays_a_pad_and_sets_a_value(self):
        self.assertEqual(self.send(msg("/pvj/pad/1/3", 1.0)), 1)
        self.assertEqual(next(c for c in self.player.calls if c[0] == "play")[1][0].split("/")[-1], "a.mp4")
        self.assertEqual(self.send(msg("/pvj/opacity", 50.0)), 1)
        self.assertIn(("opacity", 127), self.player.calls)

    def test_the_old_family_behind_the_key(self):
        self.server.rules = dict(self.server.rules, key_on=True, key="k" * 24)
        self.assertEqual(self.send(msg("/k/%s/pvj/opacity" % ("k" * 24), 40.0)), 1)
        self.assertEqual(self.send(msg("/pvj/opacity", 40.0)), 0, "the key layer still holds for the old family")
        self.assertEqual(self.send(msg("/k/wrong/pvj/opacity", 40.0)), 0)

    def test_what_the_node_sends_is_the_new_family(self):
        self.assertEqual(oscfeedback.PREFIX, "/mctl/state/")
        self.assertEqual(oscfeedback.REQUEST, "/mctl/state")


class OldHeaderAndCookieTest(ServerBase):
    def test_a_script_with_the_old_header_still_changes_things(self):
        token, _ = self.pair()
        body = {"action": "volume", "value": 40}
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False,
                                   headers={"X-PVJ-Request": "1"})[0], 200)
        self.assertEqual(self.call("POST", "/api/control", body, token=token)[0], 200, "the new header")
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False)[0], 403, "neither: refused")
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False,
                                   headers={"X-PVJ-Request": "0"})[0], 403)
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False,
                                   headers={"X-PVJ-Request": "1", "Origin": "http://evil.example"})[0], 403,
                         "the Origin check holds for the old header too")

    def test_the_old_cookie_signs_in_and_the_browser_moves_to_the_new_one(self):
        token, r = self.pair()
        self.assertEqual([c[0] for c in cookies(r)], ["mctl_token"], "a pairing sets the new name only")
        st, body, r = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})
        self.assertEqual(st, 200)
        self.assertEqual(body["device"]["role"], "full")
        got = cookies(r)
        self.assertEqual([(n, v) for n, v, _ in got], [("mctl_token", token), ("pvj_token", "")], "same token, new name; old gone")
        self.assertIn("Max-Age=31536000", got[0][2])
        self.assertIn("HttpOnly", got[0][2])
        self.assertIn("SameSite=Strict", got[0][2])
        self.assertIn("Max-Age=0", got[1][2])
        # the next request, with the new cookie only, is signed in and sets nothing
        st, _, r = self.call("GET", "/api/status", headers={"Cookie": "mctl_token=" + token})
        self.assertEqual(st, 200)
        self.assertEqual(cookies(r), [])

    def test_an_old_cookie_that_is_not_paired_gets_nothing(self):
        st, _, r = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=not-a-token"})
        self.assertEqual(st, 401)
        self.assertEqual(cookies(r), [], "no new cookie for a token that is not paired")
        token, _ = self.pair()
        self.auth.revoke(self.auth.authenticate(token)["id"])
        st, _, r = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})
        self.assertEqual(st, 401)
        self.assertEqual(cookies(r), [])

    def test_the_new_cookie_wins_and_an_old_one_beside_it_is_expired(self):
        new, _ = self.pair("new")
        old, _ = self.pair("old")
        st, body, r = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=%s; mctl_token=%s" % (old, new)})
        self.assertEqual(st, 200)
        self.assertEqual(body["device"]["name"], "new")
        self.assertEqual([(n, v) for n, v, _ in cookies(r)], [("pvj_token", "")])

    def test_logout_with_the_old_cookie_drops_both_names(self):
        token, _ = self.pair()
        st, _, r = self.call("POST", "/api/logout", {}, headers={"Cookie": "pvj_token=" + token})
        self.assertEqual(st, 200)
        self.assertEqual(sorted(n for n, v, _ in cookies(r) if v == ""), ["mctl_token", "pvj_token"])
        self.assertIsNone(self.auth.authenticate(token), "the token is gone from the node")
        self.assertEqual(self.call("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})[0], 401)

    def test_a_kept_alive_connection_does_not_carry_the_old_cookie_over(self):
        import http.client
        token, _ = self.pair()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})
            r = c.getresponse()
            r.read()
            self.assertEqual([n for n, _, _ in cookies(r)], ["mctl_token", "pvj_token"])
            c.request("GET", "/api/hello")
            r = c.getresponse()
            r.read()
            self.assertEqual(cookies(r), [], "the second request on the connection sent no cookie")
        finally:
            c.close()


class WhatIsSentTest(unittest.TestCase):
    def test_the_node_and_the_panel_send_the_new_names(self):
        with open(os.path.join(REPO, "pvj", "peer.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"User-Agent": "mctl-peer"', text)
        self.assertIn('"X-MCTL-Request": "1"', text)
        self.assertNotIn("X-PVJ-Request", text)
        self.assertTrue(peer)
        with open(os.path.join(REPO, "pvj", "web", "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertNotIn("X-PVJ-Request", app, "the panel sends the new header only")
        self.assertEqual(app.count("pvj.nodes.opened"), 1, "the old key is named once: where it is copied over")
        self.assertIn("localStorage.setItem(NODES_KEY, oldNodes)", app)
        self.assertIn("localStorage.removeItem(OLD_NODES_KEY)", app)


if __name__ == "__main__":
    unittest.main()
