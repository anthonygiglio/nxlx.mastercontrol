# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The peer channel (D132, slice 3; pvj/peer.py): one node calling another it approves, over real local TLS servers with
certificates from throwaway roots that carry the production name constraints. Good peer; another root; the right root and
the wrong key; a name that does not cover the address, and a certificate that names the host only; an address that is not
private; a node that hangs; two addresses with the first dead; a lower serial and a higher one; a run-out certificate
refused only when the clock is trusted; one call at a time per node; two separate budgets for failures (junk sent to this
node never stops its own calls); calls from nodes that are not approved cheap; the deny list spreading, never an allow,
and bounded; and accepting a node by a key that comes from its card over the checked connection."""
import os
import shutil
import tempfile
import time
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import fleetlist as FL, handover as hv, peer
from tests.peerfix import PeerNode, Silent, answer, list_data, make_root, sign_list
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
        # the node's certificate comes from `which`; the root it PINS is always the first one
        return PeerNode(self, name, node_id, self.roots[which][0], self.roots["one"][1], self.exe, **kw)

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

    def dead(self):
        d = Silent(self)
        d.close()
        return d


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

    def test_a_node_with_a_certificate_from_another_root_is_refused_and_an_event_says_so(self):
        evil = self.node("node-02", B_ID, which="two")
        a = self.a
        a.take(*self.sign(1, [(a, "full"), (evil, "full")]))
        a.directory[B_ID] = [evil.where]
        with self.assertRaises(peer.PeerError) as cm:
            a.peers.hello(B_ID)
        self.assertIn("not from this node's root", cm.exception.message)
        self.assertEqual(evil.calls, [])                    # nothing was sent to it
        self.assertTrue(any(k == "handover" and t.startswith("node-02 was not trusted:") and "not from this node's root" in t
                            for k, t, _w in a.events.lines))

    def test_the_right_root_and_the_wrong_key_is_refused(self):
        self.pair(self.a, self.b, 2)
        other = self.node("node-02", B_ID)                  # a node with the same id and name, another key, the same root
        self.a.directory[B_ID] = [other.where]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("not the one on the list", cm.exception.message)
        self.assertEqual(other.calls, [])
        self.assertTrue(any("not trusted" in t for _k, t, _w in self.a.events.lines))

    def test_a_key_that_is_renewed_with_the_same_key_is_accepted(self):
        self.pair(self.a, self.b, 2)
        again = self.node("node-02", B_ID, key_from=self.b)
        self.assertEqual(again.key, self.b.key)
        self.a.directory[B_ID] = [again.where]
        again.take(*self.sign(2, [(self.a, "full"), (self.b, "full")]))
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 2)

    def test_a_certificate_that_names_neither_the_address_nor_the_announced_name_is_refused(self):
        far = self.node("node-02", B_ID, ips=("10.9.9.9",))      # names 10.9.9.9 and node-02.local; the call goes to 127.0.0.1
        self.a.take(*self.sign(1, [(self.a, "full"), (far, "full")]))
        far.take(*self.sign(1, [(self.a, "full"), (far, "full")]))
        self.a.directory[B_ID] = [far.where]                      # no name announced: nothing else to match
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("names neither", cm.exception.message)
        self.assertEqual(far.calls, [])
        self.a.directory[B_ID] = [far.named("node-09")]           # a name that is not in the certificate
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertEqual(far.calls, [])

    def test_a_certificate_that_names_the_host_only_is_reached_by_the_name_the_node_announced(self):
        """After a DHCP change, or when the certificate holds only node-02.local, the address is not in it: the .local name
        the Nodes list announced is. The key hash from the list is still what binds the node."""
        local_only = self.node("node-02", B_ID, ips=())
        body, sig = self.sign(3, [(self.a, "full"), (local_only, "full")])
        self.a.take(body, sig)
        local_only.take(body, sig)
        self.a.directory[B_ID] = [local_only.named()]
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 3)
        self.a.directory[B_ID] = [local_only.where]               # without the name, the same node is refused
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)

    def test_a_name_announced_by_a_stranger_does_not_replace_the_key_check(self):
        self.pair(self.a, self.b, 2)
        impostor = self.node("node-02", B_ID, ips=())             # names node-02.local, but its key is not the listed one
        self.a.directory[B_ID] = [impostor.named()]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("not the one on the list", cm.exception.message)

    def test_an_address_that_is_not_private_is_never_tried(self):
        self.pair(self.a, self.b)
        self.a.directory[B_ID] = [("8.8.8.8", 443), ("1.1.1.1", 443), ("::1", 443)]
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertIn("no address is known", cm.exception.message)
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers._open("8.8.8.8", 443, None, None, self.a.peers.now())
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

    def test_the_whole_call_has_one_budget_across_addresses(self):
        self.pair(self.a, self.b)
        with self.assertRaises(peer.PeerError) as cm:             # a call that began long ago has no time left to connect
            self.a.peers._open("127.0.0.1", self.b.port, None, None, self.a.peers.now() - 100)
        self.assertIn("took too long", cm.exception.message)

    def test_with_two_addresses_the_first_dead_the_second_is_used(self):
        self.pair(self.a, self.b, 3)
        dead = self.dead()
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
        self.assertEqual(c.calls, [])                         # the root alone is not enough: c has a certificate from the same root

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

    def link(self):
        self.a.directory[B_ID] = [self.b.where]
        self.b.directory[A_ID] = [self.a.where]

    def test_a_higher_serial_is_fetched_verified_and_adopted(self):
        self.hold(self.a, 2)
        self.hold(self.b, 6)
        self.link()
        out = self.a.peers.hello(B_ID)
        self.assertEqual(out["serial"], 6)
        self.assertEqual(self.a.handover.list.serial, 6)
        with open(self.a.handover.path("fleet.json"), "rb") as f:
            self.assertEqual(f.read(), self.b.handover.list.body)
        self.assertIn(("handover", "The fleet list (serial 6, 2 nodes) was taken from node-02.", None), self.a.events.lines)

    def test_a_node_that_is_ahead_carries_its_list_and_the_peer_takes_it(self):
        self.hold(self.a, 5)
        self.hold(self.b, 3)
        self.link()
        out = self.a.peers.hello(B_ID)                          # no refusal, no second call: the list went with the hello
        self.assertEqual(out["serial"], 5)
        self.assertEqual(self.b.handover.list.serial, 5)
        # nothing proves who sent it until 4a, so the event does not credit node-01
        self.assertIn(("handover", "The fleet list (serial 5, 2 nodes) was taken from node-01.", None), self.b.events.lines)
        self.assertEqual(self.b.calls, [("POST", "/api/fleet/peer")])

    def test_being_ahead_of_a_peer_that_cannot_take_the_list_is_refused_but_never_a_failure(self):
        clock = Clock()
        self.b = self.node("node-02", B_ID, now=clock)                 # its own clock, to let the minute pass
        self.nodes = [(self.a, "full"), (self.b, "full")]
        self.hold(self.a, 5)
        self.hold(self.b, 3)
        self.link()
        real = self.b.handover.take_list

        def refuse(*a, **kw):
            raise hv.HandoverError("not now")
        self.b.handover.take_list = refuse
        for _ in range(peer.NODE_FAILS + 5):                    # many more than the failure limits
            with self.assertRaises(peer.PeerError) as cm:
                self.a.peers.hello(B_ID)
            self.assertEqual(cm.exception.status, 409)
            self.assertIn("older list", cm.exception.message)
        self.assertEqual(self.a.handover.list.serial, 5)
        self.b.handover.take_list = real                         # and this node is not stopped: the next call works
        clock.t += peer.FAIL_WINDOW + 1                          # (b allows itself two tries at a carried list a minute per sender)
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 5)
        self.assertEqual(self.b.handover.list.serial, 5)

    def test_a_carried_list_that_fails_the_checks_is_not_taken_and_is_charged_to_its_sender_only(self):
        self.hold(self.b, 3)
        body, sig = self.sign(9, self.nodes, root="two")           # signed by a root this node does not pin
        call = {"from": A_ID, "key": self.a.key, "serial": 9, "deny": self.a.handover.deny_digest(), "list": body.decode(), "sig": sig}
        for _ in range(peer.SOURCE_FAILS):
            answer(self.b.peers, dict(call), "10.0.0.5")
        self.assertEqual(self.b.handover.list.serial, 3)
        # (the list is tried only twice a minute per source, the rest are plain hellos: only bad lists and bad calls are failures)
        bad = {"from": A_ID, "key": self.a.key, "serial": "x", "deny": "0" * 16}
        for _ in range(peer.SOURCE_FAILS):
            with self.assertRaises(peer.PeerError):
                answer(self.b.peers, dict(bad), "10.0.0.5")
        with self.assertRaises(peer.PeerError) as cm:
            answer(self.b.peers, dict(call), "10.0.0.5")
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(answer(self.b.peers, dict(call, serial=3, list="", sig=""), "10.0.0.6")["serial"], 3)     # another source is fine

    def test_a_list_too_long_to_fit_a_hello_is_not_carried_and_the_hello_still_works(self):
        self.hold(self.a, 5)
        self.hold(self.b, 3)
        self.link()
        text, sig = self.a.handover.list_text()
        self.a.handover.list_text = lambda: (text + " " * (peer.MAX_CARRIED + 1), sig)
        pre = self.a.peers.preamble()
        self.assertNotIn("list", pre)
        self.assertNotIn("sig", pre)
        with self.assertRaises(peer.PeerError) as cm:               # b did not get the list: it is behind, and refused for this call
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.b.handover.list.serial, 3)
        self.a.handover.list_text = lambda: (text, sig)
        self.assertLessEqual(len(text) + len(sig), 2 * peer.MAX_CARRIED)

    def test_a_list_of_another_fleet_carried_in_a_hello_is_not_taken(self):
        self.hold(self.b, 3)
        body, sig = self.sign(9, self.nodes, fleet="fedcba9876543210")
        out = answer(self.b.peers, {"from": A_ID, "key": self.a.key, "serial": 9, "deny": self.a.handover.deny_digest(),
                                        "list": body.decode(), "sig": sig}, "10.0.0.5")
        self.assertEqual((out["serial"], self.b.handover.list.fleet), (3, FLEET))

    def test_a_list_that_fails_the_checks_in_an_answer_is_not_adopted_and_counts_as_a_failure(self):
        self.hold(self.a, 2)
        self.hold(self.b, 6)
        self.link()
        text, sig = self.b.handover.list_text()
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

    def test_a_list_signed_by_another_root_in_an_answer_is_not_adopted(self):
        self.hold(self.a, 2)
        self.hold(self.b, 3)
        self.link()
        body, sig = self.sign(8, self.nodes, root="two")
        self.b.handover.list_text = lambda: (body.decode(), sig)
        self.b.handover.list.serial = 8
        with self.assertRaises(peer.PeerError):
            self.a.peers.hello(B_ID)
        self.assertEqual(self.a.handover.list.serial, 2)

    def test_a_node_with_no_list_takes_the_one_a_peer_sends_when_it_names_it(self):
        self.hold(self.b, 4)
        self.a.handover.accept_node(B_ID, "node-02", self.b.key, "full", self.b.key)            # approved by acceptance only
        self.b.handover.accept_node(A_ID, "node-01", self.a.key, "full", self.a.key)
        self.link()
        self.a.peers.hello(B_ID)
        self.assertEqual(self.a.handover.list.serial, 4)

    def test_a_peer_the_new_list_drops_is_no_longer_approved_after_it_is_adopted(self):
        self.hold(self.a, 1)
        body, sig = self.sign(5, [(self.a, "full")])                  # a newer list that no longer names b
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers._after(B_ID, self.b.key, {"serial": 5, "list": body.decode(), "sig": sig}, ("127.0.0.1", 1))
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
        dead = self.dead()
        self.a.directory[B_ID] = [dead.where]
        seen = self.b.calls[:]
        outcomes = []
        for _ in range(7):
            try:
                self.a.peers.hello(B_ID)
            except peer.PeerError as e:
                outcomes.append(e.status)
        self.assertEqual(outcomes[:5], [502] * 5)            # five failed connects to one target
        self.assertEqual(outcomes[5:], [429, 429])           # then refused locally, with no connect at all
        self.a.directory[B_ID] = [self.b.where]              # the good target is a different port: not held back by the dead one
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)
        self.assertEqual(self.b.calls[len(seen):], [("POST", "/api/fleet/peer")])

    def test_ten_failures_in_a_minute_stop_this_nodes_calls_to_every_target_until_the_minute_is_up(self):
        for _ in range(peer.NODE_FAILS):
            self.a.directory[B_ID] = [self.dead().where]
            with self.assertRaises(peer.PeerError):
                self.a.peers.hello(B_ID)
        self.a.directory[B_ID] = [self.b.where]
        seen = len(self.b.calls)
        with self.assertRaises(peer.PeerError) as cm:
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(len(self.b.calls), seen)             # not even a connect
        self.clock.t += peer.FAIL_WINDOW + 1
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)

    def test_a_bad_answer_is_filed_under_the_target_that_gave_it(self):
        real = self.b.peers.handle_peer
        self.b.peers.handle_peer = lambda body, source, key: {"serial": "soon"}
        for _ in range(peer.TARGET_FAILS):
            with self.assertRaises(peer.PeerError) as cm:
                self.a.peers.hello(B_ID)
            self.assertIn("did not say", cm.exception.message)
        seen = len(self.b.calls)
        with self.assertRaises(peer.PeerError) as cm:         # that target is stopped; nothing reaches it
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(len(self.b.calls), seen)
        self.b.peers.handle_peer = real
        self.clock.t += peer.FAIL_WINDOW + 1
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 1)


class Budgets(PeerBase):
    """H1: junk sent to a node is charged to its senders only; it never stops this node's own calls."""

    def setUp(self):
        self.clock = Clock()
        self.a = self.node("node-01", A_ID, now=self.clock)
        self.b = self.node("node-02", B_ID)
        self.pair(self.a, self.b, 2)

    def test_a_flood_of_junk_from_two_sources_stops_neither_sync_nor_good_hellos_nor_the_owners_look_and_accept(self):
        for source in ("10.0.0.1", "10.0.0.2"):
            for _ in range(50):
                try:
                    answer(self.a.peers, {"junk": True}, source)
                except peer.PeerError:
                    pass
            with self.assertRaises(peer.PeerError) as cm:
                answer(self.a.peers, {"junk": True}, source)
            self.assertEqual(cm.exception.status, 429)          # the senders are stopped...
        c = self.node("node-03", C_ID)                          # ...and nothing else is
        self.a.directory[C_ID] = [c.where]
        self.assertEqual([r["ok"] for r in self.a.peers.sync() if r["name"] == "node-02"], [True])
        good = {"from": B_ID, "key": self.b.key, "serial": 2, "deny": self.b.handover.deny_digest()}
        self.assertEqual(answer(self.a.peers, dict(good), "10.0.0.3")["serial"], 2)
        self.assertEqual(self.a.peers.look(C_ID)["id"], C_ID)
        self.a.peers.accept(C_ID, "live", c.key)
        self.assertIsNotNone(self.a.handover.approved(C_ID, c.key))

    def test_junk_from_unapproved_callers_is_charged_to_the_sender_and_not_to_the_node(self):
        unknown = {"from": C_ID, "key": "9" * 64, "serial": 1, "deny": "0" * 16}
        for _ in range(peer.SOURCE_FAILS):
            with self.assertRaises(peer.PeerError):
                answer(self.a.peers, dict(unknown), "10.0.0.9")
        with self.assertRaises(peer.PeerError) as cm:
            answer(self.a.peers, dict(unknown), "10.0.0.9")
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(self.a.peers.hello(B_ID)["serial"], 2)          # this node's own call is unaffected

    def test_the_senders_budget_is_per_address_and_a_new_source_has_its_own(self):
        for i in range(peer.MAX_SOURCES + 20):                          # many sources: the table stays bounded
            try:
                answer(self.a.peers, {}, "10.%d.%d.1" % (i // 250, i % 250))
            except peer.PeerError:
                pass
        self.assertEqual(answer(self.a.peers, {"from": B_ID, "key": self.b.key, "serial": 2, "deny": self.b.handover.deny_digest()}, "10.9.9.9")["serial"], 2)


class Inbound(PeerBase):
    def setUp(self):
        self.clock = Clock()
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID, now=self.clock)
        self.pair(self.a, self.b, 2)

    def hello(self, source="10.0.0.7", **kw):
        body = dict({"from": A_ID, "key": self.a.key, "serial": 2, "deny": self.a.handover.deny_digest()}, **kw)
        return answer(self.b.peers, body, source)

    def test_an_approved_caller_gets_the_serial_and_nothing_else_when_it_is_level(self):
        self.assertEqual(self.hello(), {"v": 1, "serial": 2, "deny": self.b.handover.deny_digest()})

    def test_a_caller_that_is_behind_is_sent_the_list(self):
        out = self.hello(serial=1)
        self.assertEqual(out["list"], self.b.handover.list.body.decode())
        self.assertTrue(out["sig"].strip())
        self.assertIn("list", self.hello(serial="pairing"))

    def test_answers_that_carry_the_list_or_denies_are_capped_per_source_and_the_call_still_works(self):
        self.b.handover.deny_add("5" * 64, "gone", "node-02")
        heavy = [("list" in self.hello(serial=1)) for _ in range(peer.HEAVY_ANSWERS + 3)]
        self.assertEqual(heavy, [True] * peer.HEAVY_ANSWERS + [False] * 3)
        plain = self.hello(serial=1)
        self.assertEqual(plain["serial"], 2)
        self.assertNotIn("denied", plain)
        self.assertIn("list", self.hello(source="10.0.0.99", serial=1))          # another source has its own allowance
        self.clock.t += peer.FAIL_WINDOW + 1
        self.assertIn("list", self.hello(serial=1))

    def test_pulls_a_source_can_start_are_capped(self):
        started = []
        for _ in range(peer.PULLS_PER_SOURCE + 3):
            self.hello(deny="1" * 16)                                           # the caller says its removals differ
            started.append(len(self.b.pulls))
            self.b.settle()
            self.a.settle()
            self.clock.t += peer.PULL_GAP + 1                                  # past the gap between two pulls of one node
        self.assertEqual(started[-1], peer.PULLS_PER_SOURCE)

    def test_an_unknown_caller_costs_a_lookup_and_a_count(self):
        looked = []
        self.b.peers.lookup = lambda i: looked.append(i) or []
        before = len(self.b.events.lines)
        for kw in ({"from": C_ID, "key": "9" * 64}, {"key": "9" * 64}):       # an unknown id, then a known id with another key
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
                answer(self.b.peers, body, "10.0.0.8")
            self.assertIn(cm.exception.status, (400, 429))
        with self.assertRaises(peer.PeerError) as cm:
            answer(self.b.peers, {}, "10.0.0.8")
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(self.hello()["serial"], 2)           # another source, and a good call, are not affected by it
        self.clock.t += peer.FAIL_WINDOW + 1
        with self.assertRaises(peer.PeerError) as cm:
            answer(self.b.peers, {}, "10.0.0.8")
        self.assertEqual(cm.exception.status, 400)

    def test_a_list_or_signature_that_is_not_text_or_is_huge_is_a_bad_call(self):
        for extra in ({"list": 5, "sig": "x"}, {"list": "x", "sig": ["x"]}, {"list": "x" * (peer.MAX_CARRIED + 1), "sig": "x"}):
            with self.assertRaises(peer.PeerError) as cm:
                self.hello(source="10.0.0.20", serial=3, **extra)
            self.assertEqual(cm.exception.status, 400)


class Denies(PeerBase):
    def setUp(self):
        self.a = self.node("node-01", A_ID)
        self.b = self.node("node-02", B_ID)
        self.pair(self.a, self.b)
        self.x, self.y = "7" * 64, "8" * 64

    def settle(self):
        for _ in range(4):                                   # a pull can start another on the other node: until none is left
            self.a.settle()
            self.b.settle()

    def test_a_deny_is_taken_from_an_approved_peer(self):
        self.b.handover.deny_add(self.x, "node-03", "node-02")
        self.a.peers.hello(B_ID)
        self.settle()
        self.assertIn(self.x, self.a.handover.denied_keys())
        self.assertIn(("handover", "node-02 removed node-03 from this cluster.", None), self.a.events.lines)
        self.assertEqual(self.a.handover.status()["removed"], [{"n": 1, "name": "node-03", "by": "node-02", "epoch": 1, "from_peer": True}])

    def test_an_allow_is_never_taken_and_both_nodes_end_with_every_deny(self):
        self.a.handover.deny_add(self.y, "node-04", "node-01")
        self.b.handover.deny_add(self.x, "node-03", "node-02")
        self.a.peers.hello(B_ID)                             # a takes x, and b (whose digest differs) asks a back for y
        self.settle()
        self.assertEqual(self.a.handover.denied_keys(), {self.x, self.y})
        self.assertEqual(self.b.handover.denied_keys(), {self.x, self.y})
        self.assertEqual(self.a.handover.deny_digest(), self.b.handover.deny_digest())
        # nothing a peer sends removes an entry on the other node
        self.a.handover.deny_remove(self.y)
        self.a.peers.hello(B_ID)
        self.settle()
        self.assertIn(self.y, self.b.handover.denied_keys())
        # a deny that one node lifted does not come back from a peer that still holds it: the lifting leaves a local tombstone
        # (slice 4d, design section 18, item 10), tests/test_cluster_remove.py goes through it
        self.assertNotIn(self.y, self.a.handover.denied_keys())

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

    def test_a_card_is_read_from_a_certificate_that_names_the_host_only(self):
        local_only = self.node("node-04", C_ID, ips=())
        self.a.directory[C_ID] = [local_only.named()]
        self.assertEqual(self.a.peers.look(C_ID)["name"], "node-04")


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
