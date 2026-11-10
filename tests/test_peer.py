# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The peer channel (D132, slice 3; pvj/peer.py): one node calling another it approves, over real local TLS servers with
certificates from throwaway roots. Good peer; another root; the right root and the wrong key; a name that does not cover
the address; an address that is not private; a node that hangs; two addresses with the first dead; a lower serial refused
and a higher one adopted after it is verified; a run-out certificate refused only when the clock is trusted; one call at
a time per node; failures (not attempts) counted; calls from nodes that are not approved cheap; the deny list spreading,
never an allow; and accepting a node by a key that comes from its card over the checked connection."""
import os
import shutil
import tempfile
import time
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import fleetlist as FL, handover as hv, peer
from tests.peerfix import PeerNode, Silent, list_data, make_root, sign_list
from tests.test_boxcert import openssls

FLEET = "0123456789abcdef"
A_ID, B_ID, C_ID = "a" * 32, "b" * 32, "c" * 32


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@unittest.skipUnless(openssls(), "no openssl on this machine")
class PeerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = openssls()[0]
        cls.tmp = tempfile.mkdtemp()
        cls.roots = {}
        for name in ("one", "two"):
            folder = os.path.join(cls.tmp, name)
            cls.roots[name] = (folder, make_root(cls.exe, folder, "Test root " + name))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def workdir(self):
        self._n = getattr(self, "_n", 0) + 1
        path = os.path.join(self.tmp, "w-%s-%d" % (self._testMethodName[:40], self._n))
        os.mkdir(path)
        return path

    def node(self, name, node_id, which="one", **kw):
        folder, text = self.roots["one"]
        # the node's certificate comes from `which`; the root it PINS is always the first one
        sign_folder = self.roots[which][0]
        return PeerNode(self, name, node_id, sign_folder, text, self.exe, **kw)

    def sign(self, serial, nodes, root="one", fleet=FLEET):
        return sign_list(self.exe, self.roots[root][0], list_data(fleet, serial, nodes))

    def pair(self, a, b, serial=1, carry="full"):
        """Both nodes hold the same list naming both, and know where the other is."""
        body, sig = self.sign(serial, [(a, carry), (b, carry)])
        a.take(body, sig)
        b.take(body, sig)
        a.directory[b.id] = [b.where]
        b.directory[a.id] = [a.where]
        return body, sig


class Checks(PeerBase):
    def setUp(self):
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID)

    def test_a_good_peer_answers_and_says_its_serial(self):
        self.pair(self.a, self.b, 4)
        out = self.a.peers.hello(B_ID)
        self.assertEqual(out["serial"], 4)
        self.assertEqual(self.b.calls, [("POST", "/api/fleet/peer")])
        self.assertNotIn("list", out)                       # same serial: no list is sent

    def test_a_node_with_a_certificate_from_another_root_is_refused(self):
        evil = self.node("node-02", B_ID, which="two")
        a = self.a
        body, sig = self.sign(1, [(a, "full"), (evil, "full")])
        a.take(body, sig)
        a.directory[B_ID] = [evil.where]
        with self.assertRaises(peer.PeerError) as cm:
            a.peers.hello(B_ID)
        self.assertIn("not from this node's root", cm.exception.message)
        self.assertEqual(evil.calls, [])                    # nothing was sent to it

    def test_the_right_root_and_the_wrong_key_is_refused(self):
        self.pair(self.a, self.b, 2)
        other = self.node("node-02", B_ID)                  # a node with the same id and name, another key, the same root
        self.a.directory[B_ID] = [other.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("not the one on the list", cm.exception.message)
        self.assertEqual(other.calls, [])

    def test_a_key_that_is_renewed_with_the_same_key_is_accepted(self):
        self.pair(self.a, self.b, 2)
        again = self.node("node-02", B_ID, key_from=self.b)
        self.assertEqual(again.key, self.b.key)
        self.a.directory[B_ID] = [again.where]
        again.take(*self.sign(2, [(self.a, "full"), (self.b, "full")]))
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 2)

    def test_a_certificate_that_does_not_name_the_address_is_refused(self):
        far = self.node("node-02", B_ID, ips=("10.9.9.9",))      # names 10.9.9.9 only; the call goes to 127.0.0.1
        body, sig = self.sign(1, [(self.a, "full"), (far, "full")])
        self.a.take(body, sig)
        self.a.directory[B_ID] = [far.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("does not name that address", cm.exception.message)
        self.assertEqual(far.calls, [])

    def test_an_address_that_is_not_private_is_never_tried(self):
        self.pair(self.a, self.b)
        self.a.directory[B_ID] = [("8.8.8.8", 443), ("1.1.1.1", 443), ("::1", 443)]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("no address is known", cm.exception.message)
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers._open("8.8.8.8", 443, None)
        self.assertIn("not on a private network", cm.exception.message)

    def test_a_node_that_hangs_is_given_up_on(self):
        self.pair(self.a, self.b)
        silent = Silent(self)
        self.a.directory[B_ID] = [silent.where]
        old = peer.CONNECT_SECONDS
        peer.CONNECT_SECONDS = 0.4
        self.addCleanup(setattr, peer, "CONNECT_SECONDS", old)
        started = time.time()
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertLess(time.time() - started, 3)
        self.assertIn("could not connect", cm.exception.message)

    def test_with_two_addresses_the_first_dead_the_second_is_used(self):
        self.pair(self.a, self.b, 3)
        dead = Silent(self)
        dead.close()
        self.a.directory[B_ID] = [dead.where, self.b.where]
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 3)
        self.a.directory[B_ID] = [self.b.where, dead.where]
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 3)

    def test_a_node_that_is_not_approved_is_not_called(self):
        c = self.node("node-03", C_ID)
        self.a.directory[C_ID] = [c.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(C_ID)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(c.calls, [])
        # the root alone is not enough: c has a certificate from the same root

    def test_a_denied_node_is_not_called(self):
        self.pair(self.a, self.b)
        self.a.handover.deny_add(self.b.key, "node-02", "node-01")
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.b.calls, [])


class Serials(PeerBase):
    def setUp(self):
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID)
        self.nodes = [(self.a, "full"), (self.b, "full")]

    def hold(self, node, serial):
        node.take(*self.sign(serial, self.nodes))

    def test_a_peer_with_a_lower_serial_is_refused(self):
        self.hold(self.a, 5)
        self.hold(self.b, 3)
        self.a.directory[B_ID] = [self.b.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("older list", cm.exception.message)
        self.assertEqual(self.a.handover.list.serial, 5)

    def test_a_higher_serial_is_fetched_verified_and_adopted(self):
        self.hold(self.a, 2)
        self.hold(self.b, 6)
        self.a.directory[B_ID] = [self.b.where]
        out = self.a.peers.hello(B_ID)
        self.assertEqual(out["serial"], 6)
        self.assertEqual(self.a.handover.list.serial, 6)
        self.assertEqual(open(self.a.handover.path("fleet.json"), "rb").read(), self.b.handover.list.body)
        self.assertIn(("handover", "The fleet list (serial 6, 2 nodes) was taken from node-02.", None), self.a.events.lines)

    def test_a_list_that_fails_the_checks_is_not_adopted_and_counts_as_a_failure(self):
        self.hold(self.a, 2)
        self.hold(self.b, 6)
        self.a.directory[B_ID] = [self.b.where]
        real = self.b.handover.list_text
        text, sig = real()
        self.b.handover.list_text = lambda: (text.replace("node-01", "node-0X"), sig)
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("not taken", cm.exception.message)
        self.assertEqual(self.a.handover.list.serial, 2)
        # a list of another fleet, properly signed, is refused too (the floor and the fleet pin apply)
        body, sig = self.sign(9, self.nodes, fleet="fedcba9876543210")
        self.b.handover.list_text = lambda: (body.decode(), sig)
        self.b.handover.list.serial = 9
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertEqual((self.a.handover.list.fleet, self.a.handover.list.serial), (FLEET, 2))

    def test_a_list_signed_by_another_root_is_not_adopted(self):
        self.hold(self.a, 2)
        self.hold(self.b, 3)
        self.a.directory[B_ID] = [self.b.where]
        body, sig = self.sign(8, self.nodes, root="two")
        self.b.handover.list_text = lambda: (body.decode(), sig)
        self.b.handover.list.serial = 8
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertEqual(self.a.handover.list.serial, 2)

    def test_a_node_with_no_list_takes_the_one_a_peer_sends_when_it_names_it(self):
        self.hold(self.b, 4)
        self.a.handover.accept_node(B_ID, "node-02", self.b.key, "full", self.b.key)            # approved by acceptance only
        self.a.directory[B_ID] = [self.b.where]
        self.b.handover.accept_node(A_ID, "node-01", self.a.key, "full", self.a.key)
        self.a.peers.hello(B_ID)
        self.assertEqual(self.a.handover.list.serial, 4)

    def test_a_peer_the_new_list_drops_is_no_longer_approved_after_it_is_adopted(self):
        self.hold(self.a, 1)
        body, sig = self.sign(5, [(self.a, "full")])                  # a newer list that no longer names b
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers._after(B_ID, self.b.key, {"serial": 5, "list": body.decode(), "sig": sig})
        self.assertIn("no longer approves", cm.exception.message)
        self.assertEqual(self.a.handover.list.serial, 5)
        self.assertIsNone(self.a.handover.approved(B_ID, self.b.key))


class Dates(PeerBase):
    def test_a_run_out_certificate_is_refused_only_when_the_clock_is_trusted(self):
        a = self.node("node-01", A_ID, wall=lambda: time.time() + 3 * 86400)      # the system time moved three days on
        short = self.node("node-02", B_ID, days=1)
        body, sig = self.sign(1, [(a, "full"), (short, "full")])
        a.take(body, sig)
        short.take(body, sig)
        a.directory[B_ID] = [short.where]
        a.trusted = False
        self.assertEqual(a.peers.hello(B_ID)["serial"], 1)             # dates ignored when the clock is not trusted
        a.trusted = True
        with self.assertRaises(peer.PeerError) as cm:
            a.peers.hello(B_ID)
        self.assertIn("ended on", cm.exception.message)

    def test_a_certificate_inside_its_dates_is_accepted_when_the_clock_is_trusted(self):
        a = self.node("node-01", A_ID)
        b = self.node("node-02", B_ID)
        self.pair(a, b)
        a.trusted = True
        self.assertEqual(a.peers.hello(B_ID)["serial"], 1)


class Limits(PeerBase):
    def setUp(self):
        self.clock = Clock()
        self.a = self.node("node-01", A_ID, now=self.clock)
        self.b = self.node("node-02", B_ID)
        self.pair(self.a, self.b)

    def test_one_call_at_a_time_per_node_and_another_node_is_not_held_up(self):
        with self.a.peers._slot(B_ID):
            with self.assertRaises(peer.PeerError) as cm:
                self.a.peers.hello(B_ID)
            self.assertEqual(cm.exception.status, 429)
            with self.a.peers._slot(C_ID):                  # a different node has its own slot
                pass
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)        # and the slot is free again

    def test_failures_count_toward_the_limit_and_attempts_do_not(self):
        for _ in range(30):                                  # thirty good calls: nothing counts
            self.a.peers.hello(B_ID)
        self.assertFalse(self.a.peers._node_fails.over())
        dead = Silent(self)
        dead.close()
        self.a.directory[B_ID] = [dead.where]
        seen = self.b.calls[:]
        outcomes = []
        for _ in range(7):
            try:
                self.a.peers.hello(B_ID)
            except peer.PeerError as e:
                outcomes.append(e.status)
        self.assertEqual(outcomes[:5], [502] * 5)            # five failed connects to one address
        self.assertEqual(outcomes[5:], [429, 429])           # then refused locally, with no connect at all
        self.a.directory[B_ID] = [self.b.where]
        with self.assertRaises(peer.PeerError) as cm:             # the same address (the limit is per address), still inside the minute
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(self.b.calls, seen)
        self.clock.t += peer.FAIL_WINDOW + 1                      # the minute is up
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)
        self.assertEqual(self.b.calls[len(seen):], [("POST", "/api/fleet/peer")])

    def test_the_node_wide_limit_stops_calls_to_every_address_until_the_minute_is_up(self):
        for i in range(peer.NODE_FAILS):
            self.a.peers._fail("10.0.0.%d" % i)
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(self.b.calls, [])                   # not even a connect
        self.clock.t += peer.FAIL_WINDOW + 1
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)

    def test_a_bad_answer_counts_as_a_failure(self):
        real = self.b.peers.handle_peer
        self.b.peers.handle_peer = lambda body, source: {"serial": "soon"}
        before = len(self.a.peers._node_fails.hits)
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertEqual(len(self.a.peers._node_fails.hits), before + 1)
        self.b.peers.handle_peer = real


class Inbound(PeerBase):
    def setUp(self):
        self.clock = Clock()
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID, now=self.clock)
        self.pair(self.a, self.b, 2)

    def hello(self, **kw):
        body = dict({"from": A_ID, "key": self.a.key, "serial": 2, "deny": self.a.handover.deny_digest()}, **kw)
        return self.b.peers.handle_peer(body, "10.0.0.7")

    def test_an_approved_caller_gets_the_serial_and_nothing_else_when_it_is_level(self):
        self.assertEqual(self.hello(), {"v": 1, "serial": 2, "deny": self.b.handover.deny_digest()})

    def test_a_caller_that_is_behind_is_sent_the_list(self):
        out = self.hello(serial=1)
        self.assertEqual(out["list"], self.b.handover.list.body.decode())
        self.assertTrue(out["sig"].strip())
        self.assertIn("list", self.hello(serial="pairing"))

    def test_an_unknown_caller_costs_a_lookup_and_a_count(self):
        looked = []
        self.b.peers.lookup = lambda i: looked.append(i) or []
        before = len(self.b.events.lines)
        for caller in (C_ID, A_ID):
            kw = {"from": caller, "key": "9" * 64} if caller == C_ID else {"key": "9" * 64}       # an unknown id, then a known id with another key
            with self.assertRaises(peer.PeerError) as cm:
                self.hello(**kw)
            self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.b.handover.unknown_calls, 2)
        self.assertEqual(looked, [])                          # no address was looked up: no call went out
        self.assertEqual(self.b.pulls, [])
        self.assertEqual(len(self.b.events.lines), before)    # no event line
        self.assertEqual(self.b.handover.status()["unknown_calls"], 2)

    def test_bad_calls_count_and_a_source_that_keeps_sending_them_is_stopped(self):
        for body in ({}, {"from": A_ID}, {"from": "x", "key": "y", "serial": 1, "deny": "z"}, [], "text",
                     {"from": A_ID, "key": self.a.key, "serial": True, "deny": "0" * 16}, {"from": A_ID, "key": self.a.key, "serial": 0, "deny": "0" * 16}):
            with self.assertRaises(peer.PeerError) as cm:
                self.b.peers.handle_peer(body, "10.0.0.8")
            self.assertIn(cm.exception.status, (400, 429))
        with self.assertRaises(peer.PeerError) as cm:
            self.b.peers.handle_peer({}, "10.0.0.8")
        self.assertEqual(cm.exception.status, 429)
        # another source, and a good call, are not affected by it
        self.assertEqual(self.hello()["serial"], 2)
        self.clock.t += peer.FAIL_WINDOW + 1
        with self.assertRaises(peer.PeerError) as cm:
            self.b.peers.handle_peer({}, "10.0.0.8")
        self.assertEqual(cm.exception.status, 400)

    def test_a_caller_that_is_ahead_makes_this_node_ask_it(self):
        self.b.handover.list.serial = 1                      # b is behind a, which claims 2
        self.a.directory = {B_ID: [self.b.where]}
        self.b.directory[A_ID] = [self.a.where]
        self.hello(serial=2)
        self.assertEqual(len(self.b.pulls), 1)


class Denies(PeerBase):
    def setUp(self):
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID)
        self.pair(self.a, self.b)
        self.x, self.y = "7" * 64, "8" * 64

    def test_a_deny_is_taken_from_an_approved_peer(self):
        self.b.handover.deny_add(self.x, "node-03", "node-02")
        self.a.peers.hello(B_ID)
        self.assertIn(self.x, self.a.handover.denied_keys())
        self.assertIn(("handover", "node-02 removed node-03 from this cluster.", None), self.a.events.lines)
        self.assertEqual(self.a.handover.status()["removed"], [{"name": "node-03", "by": "node-02"}])

    def test_an_allow_is_never_taken_and_both_nodes_end_with_every_deny(self):
        self.a.handover.deny_add(self.y, "node-04", "node-01")
        self.b.handover.deny_add(self.x, "node-03", "node-02")
        self.a.peers.hello(B_ID)                             # a takes x, and b (whose digest differs) asks a back for y
        self.assertEqual(self.a.handover.denied_keys(), {self.x, self.y})
        self.assertEqual(self.b.handover.denied_keys(), {self.x, self.y})
        self.assertEqual(self.a.handover.deny_digest(), self.b.handover.deny_digest())
        # nothing a peer sends removes an entry on the other node
        self.a.handover.deny_remove(self.y)
        self.a.peers.hello(B_ID)
        self.assertIn(self.y, self.b.handover.denied_keys())
        # a deny that one node lifted comes back from a peer that still holds it: lifting a deny for good needs every node,
        # or a memory of the lifting (slice 4d, found here)
        self.assertIn(self.y, self.a.handover.denied_keys())

    def test_a_peer_can_not_make_a_node_approve_anybody_by_what_it_sends(self):
        before = self.a.handover.approved_nodes()
        out = self.a.handover.take_denies([{"key": self.x, "name": "n"}, {"key": "nope", "name": "n"}, 5, {"key": self.y}], "node-02")
        self.assertEqual(out, ["n"])
        self.assertEqual(self.a.handover.approved_nodes(), before)

    def test_a_deny_for_this_nodes_own_key_is_not_stored_and_the_status_says_so(self):
        added = self.a.handover.take_denies([{"key": self.a.key, "name": "node-01"}], "node-02")
        self.assertEqual(added, [])
        self.assertNotIn(self.a.key, self.a.handover.denied_keys())
        self.assertTrue(self.a.handover.status()["removed_from_cluster"])

    def test_a_peer_that_was_denied_gets_no_more_denies_from_it(self):
        self.b.handover.deny_add(self.x, "node-03", "node-02")
        self.a.handover.deny_add(self.b.key, "node-02", "node-01")          # a has removed b: b is no longer approved
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertNotIn(self.x, self.a.handover.denied_keys())

    def test_several_denies_at_once_are_one_event_naming_them(self):
        self.a.handover.take_denies([{"key": "5" * 64, "name": "n1"}, {"key": "6" * 64, "name": "n2"}], "node-02")
        self.assertEqual(self.a.events.lines[-1][1], "node-02 removed n1 and n2 from this cluster.")


class Cards(PeerBase):
    def setUp(self):
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID)
        self.a.directory[B_ID] = [self.b.where]

    def test_the_key_to_accept_comes_from_the_peers_card_and_the_typed_fingerprint_is_compared_with_it(self):
        look = self.a.peers.look(B_ID)
        self.assertEqual(look, {"id": B_ID, "name": "node-02", "fingerprint": hv.fingerprint(self.b.key)})
        with self.assertRaises(hv.HandoverError) as cm:
            self.a.peers.accept(B_ID, "live", hv.fingerprint("5" * 64))
        self.assertIn("does not match", cm.exception.message)
        self.assertEqual(self.a.handover.status()["accepted"], [])
        self.a.peers.accept(B_ID, "live", hv.fingerprint(self.b.key).upper().replace(" ", ":"), "Anthony iPad")
        self.assertEqual(self.a.handover.approved(B_ID, self.b.key)["via"], "pairing")
        self.assertEqual(self.b.calls, [("GET", "/api/fleet/card")] * 3)

    def test_the_caller_of_the_api_cannot_supply_the_key(self):
        import inspect
        self.assertEqual(sorted(inspect.signature(peer.Peers.accept).parameters), ["carry", "node_id", "self", "typed", "who"])

    def test_a_card_that_lies_about_the_key_is_refused_and_its_fingerprint_confirms_nothing(self):
        lie = {"v": 1, "id": B_ID, "name": "node-02", "key": "9" * 64}
        self.b.card_override = lie
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.accept(B_ID, "full", hv.fingerprint("9" * 64))           # the lie typed back from the same lying card
        self.assertIn("does not match", cm.exception.message)
        self.assertEqual(self.a.handover.status()["accepted"], [])
        self.b.card_override = dict(lie, key=self.b.key, id=C_ID)                  # the right key under another id
        with self.assertRaises(peer.PeerError):
            self.a.peers.card(B_ID)

    def test_a_node_with_a_certificate_from_another_root_is_not_accepted(self):
        evil = self.node("node-02", B_ID, which="two")
        self.a.directory[B_ID] = [evil.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.accept(B_ID, "full", hv.fingerprint(evil.key))
        self.assertIn("not from this node's root", cm.exception.message)
        self.assertEqual(self.a.handover.status()["accepted"], [])

    def test_a_card_that_is_not_a_card_is_refused(self):
        for card in ({}, {"id": B_ID}, {"v": 1, "id": "short", "name": "x", "key": self.b.key}, {"v": 1, "id": B_ID, "name": "a b", "key": self.b.key}):
            self.b.card_override = card
            with self.assertRaises(peer.PeerError):
                self.a.peers.card(B_ID)

    def test_a_bad_node_id_is_refused_before_any_connection(self):
        for bad in ("", "x", None, 5, "G" * 32):
            with self.assertRaises(peer.PeerError):
                self.a.peers.card(bad)
        self.assertEqual(self.b.calls, [])

    def test_two_nodes_accepted_by_fingerprint_then_talk(self):
        self.b.directory[A_ID] = [self.a.where]
        self.a.peers.accept(B_ID, "full", self.b.key)
        self.b.peers.accept(A_ID, "full", self.a.key)
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], "pairing")
        self.assertEqual(self.b.peers.hello(A_ID)["serial"], "pairing")


class Sync(PeerBase):
    def test_sync_says_one_line_for_every_approved_node(self):
        a, b, c = self.node("node-01", A_ID), self.node("node-02", B_ID), self.node("node-03", C_ID)
        body, sig = self.sign(3, [(a, "full"), (b, "full"), (c, "live")])
        for n in (a, b, c):
            n.take(body, sig)
        a.directory[B_ID] = [b.where]                        # c is not on the network
        out = {r["name"]: r for r in a.peers.sync()}
        self.assertTrue(out["node-02"]["ok"])
        self.assertEqual(out["node-02"]["serial"], 3)
        self.assertFalse(out["node-03"]["ok"])
        self.assertIn("no address", out["node-03"]["error"])


if __name__ == "__main__":
    unittest.main()
