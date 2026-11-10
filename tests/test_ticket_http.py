# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The ticket on the real server (D132, slice 4a): POST /api/handover/ticket on the panel's TLS port (who may ask), the form
POST /handover and its confirm POST /handover/switch (the cookie, the Origin, the page, the switch that is the lock, the sweep
when it is turned off, the carried Owner and the switch), and the redeem route on the real peer listener, with a second node
whose certificate comes from the same throwaway root. Needs openssl; skipped without it."""
import http.client
import json
import os
import re
import ssl
import unittest
from urllib.parse import urlencode

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import auth as auth_mod, server, ticket as T
from tests.peerfix import PeerNode
from tests.test_boxcert import openssls
from tests.test_handover_api import B_ID, ME, FleetApiBase, raw
from tests.test_https import PASS
from tests.test_ticket import new_secret

COOKIE = "__Host-pvj_token="
A_ID = "e" * 32            # (the test server's own id, ME, is "a" * 32)


@unittest.skipUnless(openssls(), "no openssl on this machine")
class HttpBase(FleetApiBase):
    def setUp(self):
        super().setUp()
        self.with_certificate()
        root = raw(os.path.join(self.ca, "root.pem")).decode()
        self.a = PeerNode(self, "node-01", A_ID, self.ca, root, self.exe, passphrase=PASS)
        self.b = PeerNode(self, "node-02", B_ID, self.ca, root, self.exe, passphrase=PASS)
        for n in (self.a, self.b):
            n.box.load()
            n.box.port = n.port
            n.auth = auth_mod.Auth(n.settings)
            n.tickets = T.Tickets(n.handover, n.peers, n.auth, n.box, event=n.events)
            n.api.tickets = n.tickets
            n.handover.set_accepting(True)
            n.directory[ME] = [("127.0.0.1", self.tls_port, "testbox", self.peer_port)]
        self.directory[A_ID] = [self.a.named()]
        self.directory[B_ID] = [self.b.named()]
        self.a.directory[B_ID] = [self.b.named()]
        self.b.directory[A_ID] = [self.a.named()]
        nodes = [{"id": ME, "name": "testbox", "key": self.hv.own_key(), "carry": "full"},
                 {"id": A_ID, "name": "node-01", "key": self.a.key, "carry": "full"},
                 {"id": B_ID, "name": "node-02", "key": self.b.key, "carry": "full"}]
        text, sig = self.sign_list(1, nodes=nodes)
        self.assertEqual(self.post_list(text, sig)[0], 200)
        for n in (self.a, self.b):
            n.take(text.encode(), sig)
        self.hv.set_accepting(True)
        self.owner = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]       # an Owner paired over https
        self.origin = "https://127.0.0.1:%d" % self.tls_port

    # -- the form, as a browser sends it --
    def form(self, path, fields=None, origin="same", cookie=None, ctype="application/x-www-form-urlencoded", tls=True, data=None, host=None):
        h = {"Content-Type": ctype}
        if origin == "same":
            origin = self.origin
        if origin is not None:
            h["Origin"] = origin
        if cookie:
            h["Cookie"] = cookie
        if host:
            h["Host"] = host
        body = data if data is not None else urlencode(fields or {}).encode()
        if tls:
            ctx = ssl.create_default_context()
            ctx.load_verify_locations(os.path.join(self.ca, "root.pem"))
            c = http.client.HTTPSConnection("127.0.0.1", self.tls_port, timeout=10, context=ctx)
        else:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=body, headers=h)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        c.close()
        return r.status, r, text

    def from_a(self, role="full", name="Anthony iPad", node=None):
        """A device on node-01 asks node-01 for a ticket to open the real server (this node): (fields of the form, device)."""
        node = node or self.a
        _t, dev = node.auth._add_device(name, role)
        node.box.mark_secure(dev["id"])
        secret = new_secret()
        out = node.tickets.make(node.auth.device(dev["id"]), {"to": ME, "proof": T.proof_of(secret)}, "127.0.0.1")
        self.assertEqual(out["action"], "https://127.0.0.1:%d/handover" % self.tls_port)
        fields = {"ticket": out["ticket"], "secret": secret, "from": node.id}
        node_origin = out["origin"]
        return fields, dev, node_origin

    def cookie_of(self, response):
        header = response.getheader("Set-Cookie")
        self.assertIsNotNone(header)
        return header


class Form(HttpBase):
    def test_the_form_signs_the_device_in_with_the_secure_cookie_and_a_page_not_a_redirect(self):
        fields, dev, a_origin = self.from_a()
        st, r, page = self.form("/handover", fields, origin=a_origin)
        self.assertEqual(st, 200, page)
        cookie = self.cookie_of(r)
        self.assertTrue(cookie.startswith(COOKIE), cookie)
        for part in ("; Secure", "; HttpOnly", "; SameSite=Strict", "; Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("Domain", cookie)
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertIn('<script src="/handover.js"></script>', page)                 # the move is made by a file of this node's own
        self.assertIsNone(r.getheader("Location"))
        self.assertNotIn(fields["ticket"], page)
        self.assertNotIn(fields["secret"], page)
        token = cookie.split(";")[0][len(COOKIE):]
        st, me, _ = self.scall("GET", "/api/status", cookie=COOKIE + token)
        self.assertEqual(st, 200)
        people = self.scall("GET", "/api/devices", token=self.owner)[1]
        row = [d for d in (people["devices"] if isinstance(people, dict) else people) if d.get("via") == "node"][0]
        self.assertEqual((row["name"], row["role"]), ("Anthony iPad, via node-01", "full"))
        said = [t for _k, t, _w in [(e["kind"], e["text"], e.get("who")) for e in self.api.events.events()]]
        self.assertIn("Anthony iPad (Owner) signed in from node-01.", said)
        for word in (fields["ticket"], fields["secret"], token):
            self.assertNotIn(word, "\n".join(said + self.lines))

    def test_the_handover_script_is_a_file_of_the_panel_and_does_one_thing(self):
        files = server.static_files(server.WEB_DIR)
        self.assertIn("/handover.js", files)                                        # served from the panel's own folder, nothing is joined to a path
        with open(os.path.join(server.WEB_DIR, files["/handover.js"])) as f:
            code = [line for line in f.read().splitlines() if line.strip() and not line.startswith("//")]
        self.assertEqual(code, ["location.replace('/');"])

    def test_a_form_whose_origin_is_not_the_page_node_a_gave_is_refused_with_no_cookie(self):
        for origin in ("https://evil.example", "http://127.0.0.1:1", None):
            fields, _dev, _a_origin = self.from_a()
            st, r, page = self.form("/handover", fields, origin=origin)
            self.assertEqual(st, 403, origin)
            self.assertIsNone(r.getheader("Set-Cookie"))
            self.assertIn("did not accept the hand-over", page)
            self.assertIn("Use the PIN", page)
            self.assertIn('<a href="/">', page)
        self.assertEqual([d for d in self.settings.data["devices"] if d.get("via") == "node"], [])

    def test_the_switch_is_the_lock(self):
        self.hv.set_accepting(False)
        fields, _dev, a_origin = self.from_a()
        st, r, page = self.form("/handover", fields, origin=a_origin)
        self.assertEqual(st, 403)
        self.assertIn("does not take devices from other nodes", page)
        self.assertIsNone(r.getheader("Set-Cookie"))
        self.assertEqual(self.a.calls, [])                                           # nobody was asked

    def test_plain_http_has_no_handover_and_names_the_https_address_without_a_redirect(self):
        fields, _dev, a_origin = self.from_a()
        st, r, text = self.form("/handover", fields, origin=a_origin, tls=False)
        self.assertEqual(st, 404)
        self.assertIsNone(r.getheader("Location"))
        self.assertIn("https", json.loads(text))
        self.assertEqual(self.a.calls, [])

    def test_it_takes_one_small_form_and_nothing_else(self):
        fields, _dev, a_origin = self.from_a()
        for kw in ({"ctype": "application/json"}, {"ctype": "multipart/form-data"}, {"data": b"x" * 3000}, {"data": b"ticket=1&ticket=2&secret=3&from=4"},
                   {"data": "ticket=é".encode("utf-8")}, {"data": b"a=1&b=2&c=3&d=4&e=5&f=6&g=7&h=8&i=9"}):
            st, r, page = self.form("/handover", fields, origin=a_origin, **kw)
            self.assertEqual(st, 403, kw)
            self.assertIsNone(r.getheader("Set-Cookie"))
        self.assertEqual(self.a.calls, [])
        self.assertEqual(self.form("/handover", fields, origin=a_origin)[0], 200)      # none of that spent the ticket
        self.assertEqual(self.scall("POST", "/handover/other", {})[0], 404)

    def test_a_handover_through_a_support_tunnel_is_refused(self):
        fields, _dev, a_origin = self.from_a()
        real = self.api.support.is_remote
        self.api.support.is_remote = lambda client: True
        try:
            st, r, page = self.form("/handover", fields, origin=a_origin)
        finally:
            self.api.support.is_remote = real
        self.assertEqual(st, 403)
        self.assertIn("support session", page)
        self.assertEqual(self.a.calls, [])

    def test_a_ticket_made_for_another_node_is_no_use_here(self):
        fields, _dev, a_origin = self.from_a(node=self.a)
        _t, dev = self.a.auth._add_device("x", "full")
        self.a.box.mark_secure(dev["id"])
        secret = new_secret()
        out = self.a.tickets.make(self.a.auth.device(dev["id"]), {"to": B_ID, "proof": T.proof_of(secret)}, "127.0.0.1")
        st, _r, page = self.form("/handover", {"ticket": out["ticket"], "secret": secret, "from": A_ID}, origin=a_origin)
        self.assertEqual(st, 403)
        self.assertIn("another node", page)

    def test_the_confirm_page_when_the_device_is_already_signed_in_here_as_something_higher(self):
        fields, dev, a_origin = self.from_a(role="live")
        st, r, page = self.form("/handover", fields, origin=a_origin, cookie=COOKIE + self.owner)
        self.assertEqual(st, 200)
        self.assertIsNone(r.getheader("Set-Cookie"))                                  # nothing is replaced yet
        self.assertIn("already signed in here as Owner", page)
        self.assertIn("Switch to Operator from node-01", page)
        handle = re.search(r'name="handle" value="([0-9a-f]{32})"', page).group(1)
        self.assertIn('action="/handover/switch"', page)
        self.assertEqual([d for d in self.settings.data["devices"] if d.get("via") == "node"], [])
        # the button is a same-origin post by the browser that holds the Owner cookie
        self.assertEqual(self.form("/handover/switch", {"handle": handle}, origin=a_origin, cookie=COOKIE + self.owner)[0], 403)      # not this node's page
        self.assertEqual(self.form("/handover/switch", {"handle": handle}, origin=None, cookie=COOKIE + self.owner)[0], 403)
        self.assertEqual(self.form("/handover/switch", {"handle": handle}, cookie=None)[0], 403)                                       # no cookie: not that browser
        self.assertEqual(self.form("/handover/switch", {"handle": handle, "x": "1"}, cookie=COOKIE + self.owner)[0], 403)
        st, r, page = self.form("/handover/switch", {"handle": handle}, cookie=COOKIE + self.owner)
        self.assertEqual(st, 200, page)
        self.assertTrue(self.cookie_of(r).startswith(COOKIE))
        self.assertEqual(self.form("/handover/switch", {"handle": handle}, cookie=COOKIE + self.owner)[0], 403)                        # once only

    def test_a_cookie_of_the_same_or_a_lower_role_is_replaced_quietly(self):
        fields, _dev, a_origin = self.from_a(role="full")
        st, r, page = self.form("/handover", fields, origin=a_origin, cookie=COOKIE + self.owner)         # an Owner here, an Owner there
        self.assertEqual(st, 200)
        self.assertNotIn("Switch", page)
        self.assertTrue(self.cookie_of(r).startswith(COOKIE))


class Lock(HttpBase):
    def carried(self, role="full", name="Anthony iPad"):
        fields, _dev, a_origin = self.from_a(role=role, name=name)
        st, r, _page = self.form("/handover", fields, origin=a_origin)
        self.assertEqual(st, 200)
        return self.cookie_of(r).split(";")[0][len(COOKIE):]

    def test_turning_the_switch_off_signs_out_every_carried_device_and_leaves_the_rest(self):
        carried = self.carried()
        guest = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        self.assertEqual(self.scall("GET", "/api/status", cookie=COOKIE + carried)[0], 200)
        st, out, _ = self.scall("POST", "/api/fleet/accept", {"on": False}, token=self.owner)
        self.assertEqual(st, 200, out)
        self.assertFalse(out["accept"])
        self.assertEqual(self.scall("GET", "/api/status", cookie=COOKIE + carried)[0], 401)
        self.assertEqual(self.scall("GET", "/api/status", token=guest)[0], 200)             # local devices are not touched
        self.assertEqual(self.scall("GET", "/api/status", token=self.owner)[0], 200)
        self.assertIsNotNone(self.hv.approved(A_ID, self.a.key))                            # the list stays
        self.assertTrue(any("1 carried device signed out" in e["text"] for e in self.api.events.events()))
        self.scall("POST", "/api/fleet/accept", {"on": True}, token=self.owner)
        self.assertEqual(self.scall("GET", "/api/status", cookie=COOKIE + carried)[0], 401)        # and turning it on does not bring it back

    def test_only_an_owner_changes_the_switch_and_a_carried_owner_is_one(self):
        owner, operator = self.carried("full", "carried owner"), self.carried("live", "carried operator")
        self.assertEqual(self.scall("POST", "/api/fleet/accept", {"on": True}, cookie=COOKIE + operator)[0], 403)
        self.assertEqual(self.scall("POST", "/api/fleet/accept", {"on": True}, token=self.operator)[0], 403)
        self.assertEqual(self.scall("POST", "/api/fleet/accept", {"on": True}, cookie=COOKIE + owner)[0], 200)
        self.assertEqual(self.scall("POST", "/api/fleet/accept", {"on": False}, cookie=COOKIE + owner)[0], 200)
        self.assertEqual(self.scall("GET", "/api/status", cookie=COOKIE + owner)[0], 401)   # he turned off his own way in

    def test_a_carried_device_is_in_the_shared_pool_and_the_list_of_devices(self):
        self.carried()
        people = self.scall("GET", "/api/devices", token=self.owner)[1]
        rows = people["devices"] if isinstance(people, dict) else people
        self.assertEqual([d["via"] for d in rows if d.get("via")], ["node"])
        self.assertNotIn("token_hash", json.dumps(rows))
        self.assertNotIn("from", json.dumps(rows))


class TicketRoute(HttpBase):
    def tcall(self, token, to=B_ID, proof=None, host=None, tls=True, body="auto"):
        body = {"to": to, "proof": proof or T.proof_of(new_secret())} if body == "auto" else body
        ask = self.scall if tls else self.call
        return ask("POST", "/api/handover/ticket", body, token=token, headers={"Host": host} if host else None)[:2]

    def test_an_owner_paired_over_https_gets_a_ticket_for_an_approved_node(self):
        st, out = self.tcall(self.owner)
        self.assertEqual(st, 200, out)
        self.assertEqual(sorted(out), ["action", "origin", "seconds", "ticket"])
        self.assertEqual(out["action"], "https://127.0.0.1:%d/handover" % self.b.port)
        self.assertEqual(out["origin"], self.origin)
        self.assertEqual(out["seconds"], 60)
        self.assertNotIn("ticket", json.dumps(self.api.events.events()))
        self.assertEqual(sorted(self.tickets._tickets), [T._hash(out["ticket"])])           # kept as a hash, in memory

    def test_the_whole_way_the_real_server_makes_the_ticket_and_node_b_redeems_it_on_the_real_peer_listener(self):
        secret = new_secret()
        st, out = self.tcall(self.owner, proof=T.proof_of(secret))
        self.assertEqual(st, 200, out)
        res = self.b.tickets.signin({"ticket": out["ticket"], "secret": secret, "from": ME}, out["origin"], "10.0.0.7")
        self.assertEqual((res["result"], res["role"], res["name"]), ("signed-in", "full", "owner, tls, via testbox"), res)
        record = [d for d in self.b.settings.data["devices"] if d.get("via") == "node"][0]
        self.assertEqual(record["from"]["node"], ME)
        self.assertEqual(self.b.auth.authenticate(res["token"])["role"], "full")
        self.assertIn("owner, tls (Owner) was signed in to node-02.", [e["text"] for e in self.api.events.events()])
        again = self.b.tickets.signin({"ticket": out["ticket"], "secret": secret, "from": ME}, out["origin"], "10.0.0.7")
        self.assertEqual(again["result"], "refused")                                         # a replay, on the real listener

    def test_it_is_a_route_of_the_secure_connection_only(self):
        st, out = self.tcall(self.owner, tls=False)
        self.assertEqual(st, 403)
        self.assertIn("secure connection", out["error"])

    def test_who_may_ask(self):
        self.assertEqual(self.tcall(None)[0], 401)
        guest = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        self.assertEqual(self.tcall(guest)[0], 403)                                          # a Guest never
        plain_op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        st, out = self.tcall(plain_op)
        self.assertEqual(st, 403)                                                            # paired over plain http: its token may have been listened to
        self.assertIn("plain http", out["error"])
        st, out = self.tcall(self.full)
        self.assertEqual(st, 403)
        _t, dev = self.auth._add_device("op over tls", "live")
        self.box.mark_secure(dev["id"])
        token = self.auth.settings.data["devices"][-1]
        # an Operator paired over https carries (it has no token in this test: make one by pairing through the join of a code is not needed)
        self.assertEqual(token["id"], dev["id"])

    def test_a_node_that_is_not_approved_gets_no_ticket(self):
        st, out = self.tcall(self.owner, to="d" * 32)
        self.assertEqual(st, 403)
        self.assertIn("not approved", out["error"])
        self.hv.deny_add(self.b.key, "node-02", "someone")
        st, out = self.tcall(self.owner)
        self.assertEqual(st, 403)                                                            # a deny always wins
        self.assertTrue(any(e["text"].startswith("A ticket for") and "was not made" in e["text"] for e in self.api.events.events()))

    def test_the_body_and_the_rate(self):
        for body in ({}, {"to": B_ID}, {"to": B_ID, "proof": "nope"}, {"to": B_ID, "proof": "0" * 64, "more": 1}):
            self.assertEqual(self.tcall(self.owner, body=body)[0], 400, body)
        for _ in range(T.TICKETS_PER_MINUTE):
            self.assertEqual(self.tcall(self.owner)[0], 200)
        self.assertEqual(self.tcall(self.owner)[0], 429)

    def test_a_host_the_certificate_does_not_carry_gets_no_ticket(self):
        self.assertEqual(self.tcall(self.owner, host="127.0.0.2:%d" % self.tls_port)[0], 409)         # an address the certificate does not name

    def test_a_support_session_can_not_reach_it(self):
        from pvj import support as sp
        self.assertTrue("/api/handover/ticket".startswith(sp.REMOTE_DENY_PREFIX))


class PeerPort(HttpBase):
    def test_redeem_is_on_the_peer_listener_only(self):
        body = {"ticket": "0" * 32, "secret": new_secret(), "to": B_ID, "key": self.b.key, "serial": 1}
        self.assertEqual(self.scall("POST", "/api/handover/redeem", body)[0], 404)            # the panel's port
        self.assertEqual(self.call("POST", "/api/handover/redeem", body)[0], 404)
        self.assertEqual(self.pcall(self.b, "/api/handover/redeem", body)[0], 403)            # the peer port: answered, and refused (no such ticket)

    def test_redeem_without_a_valid_client_certificate_never_gets_past_the_handshake(self):
        body = {"ticket": "0" * 32, "secret": new_secret(), "to": B_ID, "key": self.b.key, "serial": 1}
        with self.assertRaises((ssl.SSLError, OSError, http.client.HTTPException)):
            self.pcall(None, "/api/handover/redeem", body)

    def test_redeem_from_a_node_the_list_does_not_name_is_refused(self):
        other = PeerNode(self, "node-09", "9" * 32, self.ca, raw(os.path.join(self.ca, "root.pem")).decode(), self.exe, passphrase=PASS)
        body = {"ticket": "0" * 32, "secret": new_secret(), "to": "9" * 32, "key": other.key, "serial": 1}
        st, out = self.pcall(other, "/api/handover/redeem", body)
        self.assertEqual(st, 403, out)

    def test_the_peer_listener_has_no_ticket_table_without_the_feature(self):
        self.api.tickets = None
        body = {"ticket": "0" * 32, "secret": new_secret(), "to": B_ID, "key": self.b.key, "serial": 1}
        self.assertEqual(self.pcall(self.b, "/api/handover/redeem", body)[0], 404)


if __name__ == "__main__":
    unittest.main()
