# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The one-time ticket (D132, slice 4a; pvj/ticket.py): node A holds a ticket for one device and one node, node B redeems it by
calling A back over the real peer listener (a client certificate required) and records the carried device. Two real nodes
(certificates from a throwaway root, real Handover, Peers, Auth and Tickets, real peer servers on loopback) and a fake
monotonic clock for the 60 seconds. One-time use (also under two threads), expiry, replay, the wrong node, the wrong secret,
a denied node, a node that is not approved, the role never raised (every row that 4a can reach), the device removed or
demoted between ticket and redeem, the same device again, the ninth device of one source, the Origin of the form, the switch
that is the lock, the throttles on failures, and that no ticket, secret or token is in any log line, event or answer."""
import json
import secrets
import threading
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import auth as auth_mod, ticket as T
from tests.peerfix import list_data, sign_list
from tests.test_boxcert import openssls
from tests.test_peer import A_ID, B_ID, C_ID, FLEET, Clock, PeerBase

SOURCE = "10.0.0.5"


def new_secret():
    return secrets.token_urlsafe(16)


@unittest.skipUnless(openssls(), "no openssl on this machine")
class Rig(PeerBase):
    """node-01 (A) and node-02 (B), both with their peer listeners and a list naming both, the switch on at B."""

    def wire(self, n):
        n.box.load()                                  # the context and the certificate's names, so the node can say its own origin
        n.box.port = n.port
        n.auth = auth_mod.Auth(n.settings)
        n.log_lines = []
        n.tickets = T.Tickets(n.handover, n.peers, n.auth, n.box, event=n.events, log=n.log_lines.append, now=self.clock)
        n.api.tickets = n.tickets
        n.handover.set_accepting(True)

    def setUp(self):
        self.clock = Clock(1000.0)
        self.a = self.node("node-01", A_ID, now=self.clock)
        self.b = self.node("node-02", B_ID, now=self.clock)
        for n in (self.a, self.b):
            self.wire(n)

    def list_for(self, nodes, serial=1):
        body, sig = sign_list(self.exe, self.roots["one"][0], list_data(FLEET, serial, nodes))
        for n, _carry in nodes:
            n.take(body, sig)
        for n, _c in nodes:
            for m, _d in nodes:
                if m is not n:
                    n.directory[m.id] = [m.named()]
        return body, sig

    def pair(self, a=None, b=None, serial=1, carry_a="full", carry_b="full"):
        a, b = a or self.a, b or self.b
        return self.list_for([(a, carry_a), (b, carry_b)], serial)

    def device(self, node, name="Anthony iPad", role="full", secure=True, via=None):
        _token, dev = node.auth._add_device(name, role, via=via)
        if secure:
            node.box.mark_secure(dev["id"])
        return node.auth.device(dev["id"])

    def ask(self, a, dev, to=B_ID, secret=None, host="127.0.0.1"):
        secret = secret or new_secret()
        out = a.tickets.make(dev, {"to": to, "proof": T.proof_of(secret)}, host)
        return out, secret

    def go(self, out, secret, b=None, frm=A_ID, origin=None, source=None, current_role=None):
        b = b or self.b
        if source is None:                                     # a fresh address each time, unless a test is about one address
            self._n = getattr(self, "_n", 0) + 1
            source = "10.0.%d.%d" % divmod(self._n, 250)
        return b.tickets.signin({"ticket": out["ticket"], "secret": secret, "from": frm}, out["origin"] if origin is None else origin, source, current_role)

    def carried(self, node):
        return [d for d in node.settings.data["devices"] if d.get("via") == "node"]

    def said(self, node):
        return [t for _k, t, _w in node.events.lines]


class Making(Rig):
    def test_a_ticket_says_where_to_post_and_which_origin_the_form_comes_from(self):
        self.pair()
        out, _secret = self.ask(self.a, self.device(self.a))
        self.assertRegex(out["ticket"], r"^[0-9a-f]{32}$")                       # 128 random bits
        self.assertEqual(out["action"], "https://127.0.0.1:%d/handover" % self.b.port)
        self.assertEqual(out["origin"], "https://127.0.0.1:%d" % self.a.port)
        self.assertEqual(out["seconds"], 60)

    def test_only_an_owner_or_an_operator_paired_over_https_by_the_pin_carries(self):
        self.pair()
        refused = {
            "a guest": self.device(self.a, role="view"),
            "a device that joined with a code": self.device(self.a, role="view", via="code"),
            "an operator from a controller's code": self.device(self.a, role="live", via="controller"),
            "a device paired over plain http": self.device(self.a, secure=False),
        }
        _t, carried = self.a.auth.add_node_device("Far iPad", "full", C_ID, "ab12")
        refused["a device that came from another node (no chaining)"] = self.a.auth.device(carried["id"])
        for what, dev in refused.items():
            with self.assertRaises(T.TicketError, msg=what) as cm:
                self.ask(self.a, dev)
            self.assertEqual(cm.exception.status, 403, what)
        for dev in (dict(self.device(self.a), remote=True), {"id": "midi", "name": "MIDI", "role": "live"}, None, "x"):
            with self.assertRaises(T.TicketError):
                self.ask(self.a, dev)
        self.assertEqual(self.a.tickets._tickets, {})
        self.assertEqual(self.b.calls, [])                                         # nothing even reached b
        ok = self.device(self.a, name="op", role="live")
        self.assertIn("ticket", self.ask(self.a, ok)[0])
        recovered = self.device(self.a, name="stick", via="recovery")
        self.assertIn("ticket", self.ask(self.a, recovered)[0])

    def test_a_node_that_is_not_approved_gets_no_ticket_and_the_refusal_is_an_event(self):
        self.pair()
        for to in (C_ID, "d" * 32):
            with self.assertRaises(T.TicketError) as cm:
                self.ask(self.a, self.device(self.a), to=to)
            self.assertIn("not approved", cm.exception.message)
        self.assertTrue(any(t.startswith("A ticket for a node was not made:") for t in self.said(self.a)))
        with self.assertRaises(T.TicketError):
            self.ask(self.a, self.device(self.a), to="not an id")

    def test_the_root_alone_is_not_enough_a_node_off_the_list_gets_no_ticket(self):
        self.pair()
        self.list_for([(self.a, "full")], 2)                                       # a newer list without b
        with self.assertRaises(T.TicketError) as cm:
            self.ask(self.a, self.device(self.a))
        self.assertIn("not approved", cm.exception.message)

    def test_a_denied_node_gets_no_ticket_a_deny_always_wins(self):
        self.pair()
        self.a.handover.deny_add(self.b.key, "node-02", "someone")
        with self.assertRaises(T.TicketError) as cm:
            self.ask(self.a, self.device(self.a))
        self.assertIn("not approved", cm.exception.message)

    def test_a_node_whose_carry_is_none_gets_no_ticket_and_nothing_is_called(self):
        self.pair(carry_b="none")
        with self.assertRaises(T.TicketError) as cm:
            self.ask(self.a, self.device(self.a))
        self.assertIn("takes no devices", cm.exception.message)
        self.assertEqual(self.b.calls, [])

    def test_a_node_that_cannot_prove_it_is_who_the_list_says_gets_no_ticket(self):
        self.pair()
        evil = self.node("node-02", B_ID, which="two", now=self.clock)             # B's id, a certificate from another root
        self.a.directory[B_ID] = [evil.named()]
        with self.assertRaises(T.TicketError):
            self.ask(self.a, self.device(self.a))
        self.assertEqual(self.a.tickets._tickets, {})

    def test_six_tickets_a_minute_for_one_device_then_a_wait(self):
        self.pair()
        dev = self.device(self.a)
        for _ in range(T.TICKETS_PER_MINUTE):
            self.ask(self.a, dev)
        with self.assertRaises(T.TicketError) as cm:
            self.ask(self.a, dev)
        self.assertEqual(cm.exception.status, 429)
        self.ask(self.a, self.device(self.a, name="other"))                        # another device is not held back
        self.clock.t += 61
        self.ask(self.a, dev)

    def test_the_table_is_bounded_and_ended_tickets_leave_it(self):
        self.pair()
        devices = [self.device(self.a, name="d%d" % i) for i in range(T.MAX_TICKETS // T.TICKETS_PER_MINUTE + 2)]
        made = 0
        with self.assertRaises(T.TicketError) as cm:
            for dev in devices:
                for _ in range(T.TICKETS_PER_MINUTE):
                    self.ask(self.a, dev)
                    made += 1
        self.assertEqual(made, T.MAX_TICKETS)
        self.assertEqual(cm.exception.status, 429)
        self.clock.t += T.TICKET_SECONDS + 1
        self.ask(self.a, devices[0])
        self.assertEqual(len(self.a.tickets._tickets), 1)

    def test_the_body_is_checked(self):
        self.pair()
        dev = self.device(self.a)
        for body in ({}, {"to": B_ID}, {"to": B_ID, "proof": "x" * 64}, {"to": B_ID, "proof": "0" * 63}, {"to": 7, "proof": "0" * 64},
                     {"to": B_ID, "proof": "0" * 64, "extra": 1}, [], None):
            with self.assertRaises(T.TicketError) as cm:
                self.a.tickets.make(dev, body, "127.0.0.1")
            self.assertEqual(cm.exception.status, 400, body)

    def test_a_host_the_certificate_does_not_carry_makes_no_ticket(self):
        self.pair()
        with self.assertRaises(T.TicketError) as cm:
            self.ask(self.a, self.device(self.a), host="evil.example")
        self.assertEqual(cm.exception.status, 409)


class Redeeming(Rig):
    def test_a_ticket_signs_the_device_in_once_with_a_record_of_its_own(self):
        self.pair()
        dev = self.device(self.a)
        out, secret = self.ask(self.a, dev)
        res = self.go(out, secret)
        self.assertEqual(res["result"], "signed-in", res)
        self.assertEqual((res["role"], res["name"]), ("full", "Anthony iPad, via node-01"))
        record = self.carried(self.b)[0]
        self.assertEqual((record["via"], record["role"], record["secure"], record["from"]), ("node", "full", True, {"node": A_ID, "device": dev["id"]}))
        self.assertEqual(self.b.auth.authenticate(res["token"])["name"], "Anthony iPad, via node-01")
        self.assertEqual(self.b.auth.authenticate(res["token"])["role"], "full")
        self.assertEqual(self.b.box.device_secure(self.b.auth.authenticate(res["token"])), True)
        self.assertNotIn("token_hash", json.dumps(self.b.auth.list_devices()))
        self.assertEqual(self.a.settings.data["devices"][0]["id"], dev["id"])      # the token of the device on A never went anywhere
        self.assertIn("Anthony iPad (Owner) was signed in to node-02.", self.said(self.a))
        self.assertIn("Anthony iPad (Owner) signed in from node-01.", self.said(self.b))

    def test_a_ticket_is_used_once_a_replay_is_refused_and_makes_nothing(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        self.assertEqual(self.go(out, secret)["result"], "signed-in")
        again = self.go(out, secret)
        self.assertEqual(again["result"], "refused")
        self.assertEqual(len(self.carried(self.b)), 1)
        self.assertEqual(self.a.tickets._tickets, {})

    def test_two_calls_at_once_have_one_winner(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        body = {"ticket": out["ticket"], "secret": secret, "to": B_ID, "key": self.b.key, "serial": 1}
        barrier, results = threading.Barrier(8), []

        def call():
            barrier.wait()
            try:
                results.append(self.a.tickets.redeem(dict(body), self.b.key, "10.0.0.%d" % len(results)))
            except T.TicketError as e:
                results.append(e)
        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(1 for r in results if isinstance(r, dict)), 1)

    def test_a_ticket_ends_at_sixty_seconds_on_the_monotonic_clock(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        self.clock.t += T.TICKET_SECONDS - 1
        self.assertEqual(self.go(out, secret)["result"], "signed-in")              # still good at 59
        out, secret = self.ask(self.a, self.device(self.a, name="slow"))
        self.clock.t += T.TICKET_SECONDS
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertIn("had ended", res["sentence"])
        self.assertEqual(self.go(out, secret)["result"], "refused")                # and the ended ticket was removed, not kept
        self.assertEqual(len(self.carried(self.b)), 1)
        self.assertTrue(any("was not redeemed: the ticket had ended" in t for t in self.said(self.a)))

    def test_the_wrong_secret_is_refused_and_the_ticket_is_gone_for_the_right_one_too(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        res = self.go(out, new_secret())
        self.assertEqual(res["result"], "refused")
        self.assertEqual(self.go(out, secret)["result"], "refused")
        self.assertEqual(self.carried(self.b), [])

    def test_a_ticket_nobody_made_is_refused(self):
        self.pair()
        res = self.go({"ticket": "0" * 32, "origin": "https://127.0.0.1:1"}, new_secret())
        self.assertEqual(res["result"], "refused")

    def test_a_ticket_made_for_one_node_is_refused_for_another_and_is_spent(self):
        c = self.node("node-03", C_ID, now=self.clock)
        self.wire(c)
        self.list_for([(self.a, "full"), (self.b, "full"), (c, "full")])
        out, secret = self.ask(self.a, self.device(self.a), to=B_ID)
        res = self.go(out, secret, b=c)                                            # c is approved, has a certificate, holds the ticket and the secret
        self.assertEqual(res["result"], "refused")
        self.assertIn("another node", res["sentence"])
        self.assertEqual(self.carried(c), [])
        self.assertEqual(self.go(out, secret)["result"], "refused")                # b is too late: the ticket was spent by the attempt
        self.assertTrue(any("another node than the one it was made for" in t for t in self.said(self.a)))

    def test_a_caller_that_names_another_nodes_key_in_the_body_is_refused(self):
        c = self.node("node-03", C_ID, now=self.clock)
        self.wire(c)
        self.list_for([(self.a, "full"), (self.b, "full"), (c, "full")])
        out, secret = self.ask(self.a, self.device(self.a), to=B_ID)
        body = {"ticket": out["ticket"], "secret": secret, "to": B_ID, "key": self.b.key, "serial": 1}
        with self.assertRaises(T.TicketError) as cm:                               # the handshake shows c's key, the body claims b's
            self.a.tickets.redeem(body, c.key, SOURCE)
        self.assertEqual(cm.exception.status, 403)

    def test_a_denied_node_cannot_redeem_and_a_deny_made_after_the_ticket_wins(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        self.a.handover.deny_add(self.b.key, "node-02", "someone")                 # after the ticket was made
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertEqual(self.carried(self.b), [])
        self.assertTrue(any("that node is not approved" in t for t in self.said(self.a)))
        self.a.handover.deny_remove(self.b.key)
        self.b.handover.deny_add(self.a.key, "node-01", "someone")                 # b denies a: b will not even call
        out, secret = self.ask(self.a, self.device(self.a, name="again"))
        before = len(self.a.calls)
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertIn("not approved", res["sentence"])
        self.assertEqual(len(self.a.calls), before)

    def test_a_node_not_approved_at_b_is_refused_before_any_call(self):
        self.pair()
        b_only = self.node("node-04", "d" * 32, now=self.clock)
        self.wire(b_only)
        out, secret = self.ask(self.a, self.device(self.a))
        res = self.go(out, secret, frm="d" * 32)
        self.assertEqual(res["result"], "refused")
        self.assertEqual(b_only.calls, [])
        self.assertEqual(len(self.a.tickets._tickets), 1)                          # the ticket was not touched

    def test_the_switch_at_b_is_the_lock_nothing_is_called_and_nothing_is_made(self):
        self.pair()
        self.b.handover.set_accepting(False)
        out, secret = self.ask(self.a, self.device(self.a))
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertIn("does not take devices from other nodes", res["sentence"])
        self.assertEqual(self.a.calls, [])
        self.assertEqual(self.carried(self.b), [])
        self.b.handover.set_accepting(True)
        self.assertEqual(self.go(out, secret)["result"], "signed-in")              # the ticket was never touched

    def test_the_device_removed_between_ticket_and_redeem_is_refused(self):
        self.pair()
        dev = self.device(self.a)
        out, secret = self.ask(self.a, dev)
        self.a.auth.revoke(dev["id"])
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertIn("no longer paired", res["sentence"])
        self.assertEqual(self.carried(self.b), [])

    def test_the_device_demoted_between_ticket_and_redeem_gets_what_it_has_now_and_never_more(self):
        self.pair()
        dev = self.device(self.a)
        out, secret = self.ask(self.a, dev)
        for d in self.a.settings.data["devices"]:
            if d["id"] == dev["id"]:
                d["role"] = "live"
        res = self.go(out, secret)
        self.assertEqual((res["result"], res["role"]), ("signed-in", "live"))
        out, secret = self.ask(self.a, self.device(self.a, name="guest now"))
        for d in self.a.settings.data["devices"]:
            if d["name"] == "guest now":
                d["role"] = "view"
        self.assertEqual(self.go(out, secret)["result"], "refused")                # a Guest never carries
        out, secret = self.ask(self.a, self.device(self.a, name="http now"))
        for d in self.a.settings.data["devices"]:
            if d["name"] == "http now":
                d["secure"] = False
        self.assertEqual(self.go(out, secret)["result"], "refused")                # nor a device whose token may have been listened to

    def test_a_carried_role_is_never_raised_the_rows_of_the_role_table(self):
        rows = [("full", "full", "full"), ("full", "live", "live"), ("live", "full", "live"), ("live", "live", "live")]
        for role, carry, expect in rows:
            self.setUp()
            self.pair(carry_b=carry)
            out, secret = self.ask(self.a, self.device(self.a, role=role))
            res = self.go(out, secret)
            self.assertEqual((res["result"], res["role"]), ("signed-in", expect), (role, carry))
            self.assertEqual(self.carried(self.b)[0]["role"], expect)
        self.setUp()
        self.pair(carry_b="none")                                                  # a ticket that a made although b gives nothing (b decides for itself)
        out, secret = self.ask_around(self.a)
        res = self.go(out, secret)
        self.assertEqual(res["result"], "refused")
        self.assertEqual(self.carried(self.b), [])

    def ask_around(self, a):
        """A ticket although b's carry is none, as if a did not know it (a only knows b's row from the list: ask the table directly)."""
        secret = new_secret()
        ticket = secrets.token_hex(16)
        dev = self.device(a)
        a.tickets._tickets[T._hash(ticket)] = {"device": dev["id"], "name": dev["name"], "to": B_ID, "key": self.b.key, "proof": T.proof_of(secret),
                                               "origin": "https://127.0.0.1:%d" % a.port, "end": self.clock() + 60}
        return {"ticket": ticket, "origin": "https://127.0.0.1:%d" % a.port}, secret

    def test_an_a_that_claims_more_than_b_gives_is_capped_by_b(self):
        self.pair(carry_b="live")
        out, secret = self.ask(self.a, self.device(self.a))
        self.assertEqual(self.go(out, secret)["role"], "live")                     # a says Owner, b's carry for a is Operator

    def test_the_same_device_again_gets_the_same_record_with_a_new_token(self):
        self.pair()
        dev = self.device(self.a)
        first = self.go(*self.ask(self.a, dev))
        second = self.go(*self.ask(self.a, dev))
        self.assertEqual((first["result"], second["result"]), ("signed-in", "signed-in"))
        self.assertEqual(first["device"], second["device"])
        self.assertEqual(len(self.carried(self.b)), 1)
        self.assertIsNone(self.b.auth.authenticate(first["token"]))
        self.assertIsNotNone(self.b.auth.authenticate(second["token"]))

    def test_the_ninth_device_of_one_source_is_refused_and_an_old_one_can_still_come_again(self):
        self.pair()
        first = None
        for i in range(auth_mod.NODE_DEVICES_PER_SOURCE):
            dev = self.device(self.a, name="phone %d" % i)
            first = first or dev
            self.assertEqual(self.go(*self.ask(self.a, dev))["result"], "signed-in")
        ninth = self.go(*self.ask(self.a, self.device(self.a, name="one too many")))
        self.assertEqual(ninth["result"], "refused")
        self.assertIn("already holds", ninth["sentence"])
        self.assertEqual(len(self.carried(self.b)), auth_mod.NODE_DEVICES_PER_SOURCE)
        self.assertEqual(self.go(*self.ask(self.a, first))["result"], "signed-in")
        self.assertEqual(len(self.carried(self.b)), auth_mod.NODE_DEVICES_PER_SOURCE)

    def test_carried_devices_count_in_the_pool_beside_guests(self):
        self.pair()
        self.go(*self.ask(self.a, self.device(self.a)))
        shared = sum(1 for d in self.b.settings.data["devices"] if d["role"] != "full" or d.get("via") in auth_mod.SHARED_VIA)
        self.assertEqual(shared, 1)

    def test_the_origin_of_the_form_must_be_the_page_a_gave_and_a_mismatch_spends_the_ticket(self):
        self.pair()
        for i, origin in enumerate(("https://evil.example", "https://127.0.0.1:1")):
            out, secret = self.ask(self.a, self.device(self.a, name="x%d" % i))
            res = self.go(out, secret, origin=origin, source="10.2.0.%d" % i)
            self.assertEqual(res["result"], "refused", origin)
            self.assertIn("not made from that node's own page", res["sentence"])
            self.assertEqual(self.go(out, secret)["result"], "refused")            # spent: the real page cannot use it now
        for i, origin in enumerate((None, "", "null", "https://", "https://a b", "http://127.0.0.1:%d" % self.a.port)):
            self.clock.t += 61                                                      # (the throttle of failures forgets: this test is not about it)
            out, secret = self.ask(self.a, self.device(self.a, name="y%d" % i))
            calls = len(self.a.calls)
            res = self.b.tickets.signin({"ticket": out["ticket"], "secret": secret, "from": A_ID}, origin, "10.3.0.%d" % i)
            self.assertEqual(res["result"], "refused", origin)
            self.assertEqual(len(self.a.calls), calls)                              # no usable Origin is refused before a is called
            self.assertEqual(self.go(out, secret)["result"], "signed-in")           # and the ticket was not touched
        self.assertEqual(len(self.carried(self.b)), 6)

    def test_the_form_fields_are_checked_before_anything_is_called(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        good = {"ticket": out["ticket"], "secret": secret, "from": A_ID}
        for fields in ({}, dict(good, extra="1"), dict(good, ticket="zz"), dict(good, secret="short"), dict(good, **{"from": "nope"}), {"ticket": out["ticket"]}, None):
            res = self.b.tickets.signin(fields, out["origin"], "10.1.1.%d" % len(str(fields)))
            self.assertEqual(res["result"], "refused", fields)
        self.assertEqual(self.a.calls, [])

    def test_junk_forms_from_one_address_never_block_a_real_hand_over_from_another_device(self):
        self.pair()
        for _ in range(T.peer_mod.SOURCE_FAILS):
            self.b.tickets.signin({"nonsense": 1}, "https://x.example", "10.9.9.9")
        out, secret = self.ask(self.a, self.device(self.a))
        blocked = self.go(out, secret, source="10.9.9.9")
        self.assertIn("wait a minute", blocked["sentence"])
        self.assertEqual(len(self.a.calls), 0)                                      # refused locally, a was not asked
        for i in range(40):                                                         # junk from forty other addresses...
            self.b.tickets.signin({"nonsense": 1}, "https://x.example", "10.7.%d.1" % i)
        self.assertEqual(self.go(out, secret, source="10.8.8.8")["result"], "signed-in")     # ...there is no count for the whole node to run out
        self.clock.t += 61
        out, secret = self.ask(self.a, self.device(self.a, name="later"))
        self.assertEqual(self.go(out, secret, source="10.9.9.9")["result"], "signed-in")

    def test_junk_that_looks_like_a_form_and_is_passed_on_to_a_does_not_block_b_as_a_caller(self):
        self.pair()
        for i in range(T.FORM_ATTEMPTS):                                            # the most one address may post in a minute
            res = self.b.tickets.signin({"ticket": "%032x" % i, "secret": new_secret(), "from": A_ID}, "https://%s" % "127.0.0.1:1", "10.9.9.9")
            self.assertEqual(res["result"], "refused")
        self.assertIn("wait a minute", self.b.tickets.signin({"ticket": "0" * 32, "secret": new_secret(), "from": A_ID}, "https://127.0.0.1:1", "10.9.9.9")["sentence"])
        calls = len([c for c in self.a.calls if c[1] == "/api/handover/redeem"])
        self.assertLessEqual(calls, T.FORM_ATTEMPTS)                                # a was asked at most that many times by this address
        out, secret = self.ask(self.a, self.device(self.a))
        self.assertEqual(self.go(out, secret)["result"], "signed-in")               # b may still call a: a counted those against b's own counter, which is far higher
        for _ in range(2):                                                           # two more addresses doing the same
            for i in range(T.FORM_ATTEMPTS):
                self.b.tickets.signin({"ticket": "%032x" % i, "secret": new_secret(), "from": A_ID}, "https://127.0.0.1:1", "10.6.6.%d" % (_ + 1))
        out, secret = self.ask(self.a, self.device(self.a, name="again"))
        self.assertEqual(self.go(out, secret)["result"], "signed-in")

    def test_attempts_count_not_only_failures_six_a_minute_from_one_address(self):
        self.pair()
        dev = self.device(self.a)
        for _ in range(T.FORM_ATTEMPTS):
            self.assertEqual(self.go(*self.ask(self.a, dev), source="10.5.5.5")["result"], "signed-in")
            self.clock.t += 1
        out, secret = self.ask(self.a, self.device(self.a, name="seventh"))
        self.assertIn("wait a minute", self.go(out, secret, source="10.5.5.5")["sentence"])
        self.assertEqual(self.go(out, secret, source="10.5.5.6")["result"], "signed-in")
        self.clock.t += 61
        self.assertEqual(self.go(*self.ask(self.a, dev), source="10.5.5.5")["result"], "signed-in")

    def test_an_approved_peers_refused_redeems_are_never_throttled_at_a(self):
        self.pair()
        junk = {"ticket": "0" * 32, "secret": new_secret(), "to": B_ID, "key": self.b.key, "serial": 1}
        for i in range(60):
            with self.assertRaises(T.TicketError) as cm:
                self.a.tickets.redeem(dict(junk), self.b.key, "10.9.9.%d" % (i % 3))
            self.assertEqual(cm.exception.status, 403)                              # refused every time, never 429
        out, secret = self.ask(self.a, self.device(self.a))
        good = {"ticket": out["ticket"], "secret": secret, "to": B_ID, "key": self.b.key, "serial": 1}
        self.assertEqual(self.a.tickets.redeem(good, self.b.key, "10.9.9.1")["role"], "full")

    def test_junk_forms_through_b_from_many_addresses_never_stop_a_real_hand_over(self):
        self.pair()
        for n in range(8):                                                           # eight addresses, each at its limit for the minute
            for i in range(T.FORM_ATTEMPTS):
                self.b.tickets.signin({"ticket": "%032x" % (n * 10 + i), "secret": new_secret(), "from": A_ID}, "https://127.0.0.1:1", "10.20.0.%d" % n)
        out, secret = self.ask(self.a, self.device(self.a))
        self.assertEqual(self.go(out, secret)["result"], "signed-in")                # b may still call a, and a still answers b

    def test_malformed_and_unapproved_calls_at_a_cost_only_their_own_address(self):
        self.pair()
        c = self.node("node-03", C_ID, now=self.clock)                              # a node of the root that a does not approve
        for i in range(T.peer_mod.SOURCE_FAILS):
            with self.assertRaises(T.TicketError):
                self.a.tickets.redeem({"junk": 1}, c.key, "10.9.9.9")
        with self.assertRaises(T.TicketError) as cm:
            self.a.tickets.redeem({"junk": 1}, c.key, "10.9.9.9")
        self.assertEqual(cm.exception.status, 429)
        for i in range(40):                                                          # forty other addresses: no whole-node count to fill
            with self.assertRaises(T.TicketError):
                self.a.tickets.redeem({"junk": 1}, c.key, "10.3.%d.1" % i)
        out, secret = self.ask(self.a, self.device(self.a))
        good = {"ticket": out["ticket"], "secret": secret, "to": B_ID, "key": self.b.key, "serial": 1}
        self.assertEqual(self.a.tickets.redeem(good, self.b.key, "10.9.9.9")["role"], "full")      # even from the blocked address: b is approved, b's counter is clean

    def test_a_refusal_from_a_counts_at_b_as_a_failure(self):
        self.pair()
        for i in range(T.peer_mod.SOURCE_FAILS):
            out, secret = self.ask(self.a, self.device(self.a, name="w%d" % i))
            self.assertEqual(self.go(out, new_secret(), source="10.9.9.9")["result"], "refused")
        out, secret = self.ask(self.a, self.device(self.a, name="last"))
        self.assertIn("wait a minute", self.go(out, secret, source="10.9.9.9")["sentence"])

    def test_good_hand_overs_never_lock_the_room_out(self):
        self.pair()
        dev = self.device(self.a)
        for _ in range(12):
            self.clock.t += 11                                                    # (the per-device allowance of tickets is six a minute)
            self.assertEqual(self.go(*self.ask(self.a, dev))["result"], "signed-in")

    def test_a_pending_confirm_changes_nothing_until_the_person_says_so(self):
        self.pair()
        dev = self.device(self.a, role="live")
        out, secret = self.ask(self.a, dev)
        res = self.go(out, secret, current_role="full")                            # already an Owner here
        self.assertEqual((res["result"], res["have"], res["would"]), ("confirm", "Owner", "Operator"), res)
        self.assertEqual(self.carried(self.b), [])
        done = self.b.tickets.confirm(res["handle"], SOURCE)
        self.assertEqual((done["result"], done["role"]), ("signed-in", "live"))
        self.assertEqual(len(self.carried(self.b)), 1)
        self.assertEqual(self.b.tickets.confirm(res["handle"], SOURCE)["result"], "refused")      # once only

    def test_a_pending_confirm_runs_out_and_a_lower_or_equal_role_replaces_quietly(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a, role="live"))
        res = self.go(out, secret, current_role="full")
        self.clock.t += T.PENDING_SECONDS + 1
        late = self.b.tickets.confirm(res["handle"], SOURCE)
        self.assertEqual(late["result"], "refused")
        self.assertEqual(self.carried(self.b), [])
        for have in ("live", "view"):
            out, secret = self.ask(self.a, self.device(self.a, role="live", name="o" + have))
            self.assertEqual(self.go(out, secret, current_role=have)["result"], "signed-in", have)
        self.assertEqual(self.b.tickets.confirm("0" * 32, SOURCE)["result"], "refused")
        self.assertEqual(self.b.tickets.confirm(None, SOURCE)["result"], "refused")

    def test_the_confirm_table_is_bounded(self):
        self.pair()
        handles = []
        for i in range(T.MAX_PENDING + 3):
            self.clock.t += 6
            out, secret = self.ask(self.a, self.device(self.a, role="live", name="p%d" % i))
            handles.append(self.go(out, secret, current_role="full")["handle"])
        self.assertEqual(len(self.b.tickets._pending), T.MAX_PENDING)
        self.assertEqual(self.b.tickets.confirm(handles[0], SOURCE)["result"], "refused")          # the oldest was dropped
        self.assertEqual(self.b.tickets.confirm(handles[-1], SOURCE)["result"], "signed-in")

    def test_a_confirm_asks_again_whether_the_node_is_still_approved(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a, role="live"))
        res = self.go(out, secret, current_role="full")
        self.b.handover.deny_add(self.a.key, "node-01", "someone")                  # after the confirm page was shown
        done = self.b.tickets.confirm(res["handle"], SOURCE)
        self.assertEqual(done["result"], "refused")
        self.assertIn("no longer approved", done["sentence"])
        self.assertEqual(self.carried(self.b), [])
        self.b.handover.deny_remove(self.a.key)
        out, secret = self.ask(self.a, self.device(self.a, role="live", name="again"))
        res = self.go(out, secret, current_role="full")
        self.list_for([(self.a, "full"), (self.b, "none")], 2)                      # b now gives nothing to a
        self.assertEqual(self.b.tickets.confirm(res["handle"], SOURCE)["result"], "refused")

    def test_the_switch_turned_off_while_the_call_was_in_flight_is_not_undone(self):
        self.pair()
        out, secret = self.ask(self.a, self.device(self.a))
        real = self.b.peers.call_once

        def then_off(node_id, path, body):
            answer = real(node_id, path, body)
            self.b.handover.set_accepting(False)                                   # the Owner switches it off while a was answering
            return answer
        self.b.peers.call_once = then_off
        res = self.go(out, secret)
        self.b.peers.call_once = real
        self.assertEqual(res["result"], "refused")
        self.assertIn("does not take devices from other nodes", res["sentence"])
        self.assertEqual(self.carried(self.b), [])

    def test_a_peer_certificate_is_judged_for_both_ends_of_its_dates_only_while_the_clock_is_trusted(self):
        from pvj import fleetlist as FL
        self.pair()
        der = FL.certificate_der(self.a.cert_pem)
        info = T.peer_mod.httpsbox.read_certificate(der)
        peers = self.b.peers
        peers.wall = lambda: info["not_before"] - 86400
        self.b.trusted = False
        self.assertEqual(peers.authenticate(der, "10.0.0.1"), self.a.key)           # a wrong clock: not judged
        self.b.trusted = True
        with self.assertRaises(T.peer_mod.PeerError) as cm:
            peers.authenticate(der, "10.0.0.1")
        self.assertIn("not valid yet", cm.exception.message)
        peers.wall = lambda: info["not_after"] + 86400
        with self.assertRaises(T.peer_mod.PeerError) as cm:
            peers.authenticate(der, "10.0.0.2")
        self.assertIn("run out", cm.exception.message)
        peers.wall = lambda: info["not_before"] + 60
        self.assertEqual(peers.authenticate(der, "10.0.0.3"), self.a.key)

    def test_an_odd_certificate_is_a_403_not_a_crash(self):
        for der in (b"", b"\x30\x03\x02\x01", b"\x30\x84\xff\xff\xff\xff" + b"x" * 20, b"not a certificate at all", "text"):
            with self.assertRaises(T.peer_mod.PeerError) as cm:
                self.b.peers.authenticate(der, "10.0.0.%d" % (len(str(der)) % 200))
            self.assertEqual(cm.exception.status, 403)

    def test_b_does_not_retry_after_the_call_failed_the_ticket_is_probably_spent(self):
        self.pair()
        dev = self.device(self.a)
        out, secret = self.ask(self.a, dev)
        self.a.directory[B_ID] = [self.b.named()]
        calls = []
        real = self.b.peers.call_once

        def failing(node_id, path, body):
            calls.append(path)
            raise T.peer_mod.PeerError("the call failed: timed out")
        self.b.peers.call_once = failing
        res = self.go(out, secret)
        self.b.peers.call_once = real
        self.assertEqual(res["result"], "refused")
        self.assertIn("could not be reached", res["sentence"])
        self.assertEqual(calls, ["/api/handover/redeem"])                           # exactly one try
        self.assertEqual(self.carried(self.b), [])

    def test_nothing_secret_is_in_any_log_line_event_or_answer(self):
        self.pair()
        dev = self.device(self.a)
        out, secret = self.ask(self.a, dev)
        res = self.go(out, secret)
        out2, secret2 = self.ask(self.a, dev)
        self.go(out2, new_secret())                                                 # a failure too
        seen = json.dumps([self.a.events.lines, self.b.events.lines, self.a.log_lines, self.b.log_lines, self.a.settings.data, self.b.settings.data,
                           self.a.auth.list_devices(), self.b.auth.list_devices(), self.a.handover.status(), self.b.handover.status()], default=str)
        for word in (out["ticket"], secret, res["token"], out2["ticket"], secret2, T.proof_of(secret)):
            self.assertNotIn(word, seen)


if __name__ == "__main__":
    unittest.main()
