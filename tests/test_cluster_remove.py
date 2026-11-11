# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Remove a node from this cluster, and Allow again (D132, slice 4d; pvj/handover.py, pvj/peer.py): three real nodes (certificates
from a throwaway root, the real peer listeners, real Handover, Peers, Auth, Tickets and Leases) and a fake monotonic clock.

* the removal spreads to every approved node that can be reached, and a node that takes it passes it on;
* every device the removed node carried is signed out at once on the node where it was pressed (the sweep) and on the others as
  they hear it; devices carried from other nodes stay;
* a removed node is refused on the peer port in both directions, for the hello, the redeem, the lease and the poke;
* Allow again keeps a local tombstone (epoch + 1) that is never sent: an old deny that is still going round does not undo it, a
  newer deny does come back, a copy of an old Allow again request does not undo the newer deny, and no peer can allow anybody;
* a node that was off catches up on its next talk to anybody, or when the node that pressed the button tries it again;
* spreading has allowances of its own: a failing push charges its target only, never the node-wide count of outbound failures, and
  a node that pushes a lot fills the allowance of its own key only;
* only an Owner can remove a node or allow it again, over the secure connection, and the routes are not on the peer port.
"""
import json
import time
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import handover as hv, lease as L, peer
from tests.peerfix import answer
from tests.test_handover_api import FleetApiBase, ME
from tests.test_lease import LeaseRig
from tests.test_peer import A_ID, B_ID, C_ID
from tests.test_ticket import new_secret

UNKNOWN = "d" * 32


class Cluster(LeaseRig):
    """node-01 (A), node-02 (B) and node-03 (C), all on one signed list with carry full, devices may be carried between any two."""

    def setUp(self):
        super().setUp()
        self.c = self.node("node-03", C_ID, now=self.clock)
        self.wire(self.c)
        n = self.c
        n.leases = L.Leases(n.handover, n.peers, n.auth, n.box, event=n.events, log=n.log_lines.append, now=self.clock, wall=self.wall,
                            boot=lambda: self.boot, spawn=lambda fn: fn())
        n.tickets.leases = n.leases
        n.api.leases = n.leases
        n.auth.on_lent_gone = n.leases.poke
        n.handover.on_change = n.leases.sweep
        self.nodes = (self.a, self.b, self.c)
        self.list_for([(self.a, "full"), (self.b, "full"), (self.c, "full")], 1)

    def settle(self):
        for _ in range(6):                                 # a push can start a pull and the other way round: until none is left
            for n in self.nodes:
                n.settle()

    def remove(self, node, target, who="Anthony iPad"):
        """What POST /api/fleet/remove does on `node`, without the HTTP: the deny, the sweep, the push."""
        done = node.handover.remove_node(target.id, who)
        node.peers.spread()
        self.settle()
        return done

    def carry_from(self, src, dst, name="Anthony iPad"):
        dev = self.device(src, name=name)
        out, secret = self.ask(src, dev, to=dst.id)
        res = self.go(out, secret, b=dst, frm=src.id)
        self.assertEqual(res["result"], "signed-in", res)
        return dev, res

    def alive_on(self, node, token):
        return node.auth.authenticate(token) is not None

    def row(self, node, target):
        return [r for r in node.handover.status()["removed"] if r["name"] == target.name]


class Spreading(Cluster):
    def test_a_removal_goes_to_every_approved_node_and_the_removed_node_is_not_told(self):
        self.remove(self.a, self.c)
        for n in (self.a, self.b):
            self.assertEqual(n.handover.denied_keys(), {self.c.key}, n.name)
            self.assertIsNone(n.handover.approved(C_ID, self.c.key), n.name)
        self.assertEqual(self.a.handover.deny_digest(), self.b.handover.deny_digest())
        self.assertEqual(self.c.handover.denied_keys(), set())
        self.assertNotIn(("POST", "/api/fleet/peer"), self.c.calls)           # nothing was sent to the node that was removed
        self.assertIn("node-01 removed node-03 from this cluster.", self.said(self.b))
        self.assertEqual(self.b.handover.status()["removed"][0]["epoch"], 1)

    def test_a_node_that_takes_a_removal_passes_it_on_to_its_other_peers(self):
        self.d = self.node("node-04", "d" * 32, now=self.clock)
        self.wire(self.d)
        self.nodes = self.nodes + (self.d,)
        self.list_for([(self.a, "full"), (self.b, "full"), (self.c, "full"), (self.d, "full")], 2)
        self.a.directory.pop(self.d.id)                                       # a cannot reach d at all: only b can
        self.a.handover.remove_node(C_ID, "Anthony")
        self.a.peers.spread()
        self.settle()
        self.assertEqual(self.d.handover.denied_keys(), {self.c.key})
        self.assertEqual(self.b.handover.denied_keys(), {self.c.key})

    def test_removing_a_node_takes_its_acceptance_and_a_node_that_is_not_approved_cannot_be_removed(self):
        with self.assertRaises(hv.HandoverError) as cm:
            self.a.handover.remove_node("e" * 32)
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(hv.HandoverError):
            self.a.handover.remove_node(A_ID)                                 # a node is never its own peer to remove
        self.assertEqual(self.a.handover.denied_keys(), set())
        self.remove(self.a, self.c)
        with self.assertRaises(hv.HandoverError) as cm:
            self.a.handover.remove_node(C_ID)                                 # removed already: not in the approved set any more
        self.assertEqual(cm.exception.status, 404)

    def test_what_goes_over_the_wire_is_the_key_the_name_and_the_epoch_and_nothing_else(self):
        self.remove(self.a, self.c)
        self.assertEqual(self.a.handover.deny_wire(), [{"key": self.c.key, "name": "node-03", "epoch": 1}])
        out = answer(self.a.peers, {"from": B_ID, "key": self.b.key, "serial": 1, "deny": "0" * 16}, "10.0.0.9")
        self.assertEqual(out["denied"], [{"key": self.c.key, "name": "node-03", "epoch": 1}])
        status = json.dumps(self.a.handover.status())
        self.assertNotIn(self.c.key, status)                                  # the Owner's status names the node, never its key
        self.assertNotIn(hv.fingerprint(self.c.key), status)


class Carried(Cluster):
    def test_every_device_the_removed_node_carried_is_signed_out_at_once_and_others_stay(self):
        _d1, from_c_to_a = self.carry_from(self.c, self.a, "iPad from c")
        _d2, from_c_to_b = self.carry_from(self.c, self.b, "Mac from c")
        _d3, from_b_to_a = self.carry_from(self.b, self.a, "Phone from b")
        for node, res in ((self.a, from_c_to_a), (self.b, from_c_to_b), (self.a, from_b_to_a)):
            self.assertTrue(self.alive_on(node, res["token"]))
        self.a.handover.remove_node(C_ID, "Anthony")                          # the Owner's press: before any network
        self.assertFalse(self.alive_on(self.a, from_c_to_a["token"]))         # a signed out at once, by the sweep
        self.assertTrue(self.alive_on(self.a, from_b_to_a["token"]))          # a device from a node that is still approved stays
        self.assertTrue(any("carried from node-03" in t for t in self.said(self.a)))
        self.a.peers.spread()
        self.settle()
        self.assertFalse(self.alive_on(self.b, from_c_to_b["token"]))         # and on the other node as soon as it hears
        self.assertEqual([r["name"] for r in self.b.auth.node_records()], [])
        self.assertEqual(len(self.a.auth.node_records()), 1)

    def test_a_node_that_is_removed_cannot_carry_a_new_device_to_a_node_that_has_heard(self):
        self.remove(self.a, self.c)
        dev = self.device(self.c)
        out, secret = self.ask(self.c, dev, to=B_ID)                          # c still trusts b, so it makes the ticket
        res = self.go(out, secret, b=self.b, frm=C_ID)
        self.assertNotEqual(res.get("result"), "signed-in", res)
        self.assertEqual(self.b.auth.node_records(), [])


class Refusing(Cluster):
    def test_a_removed_node_is_refused_on_the_peer_port_for_every_call(self):
        self.remove(self.a, self.b)
        before = len(self.b.calls)
        with self.assertRaises(peer.PeerError) as cm:                         # b is still told nothing: it calls a, a refuses
            self.b.peers.hello(A_ID)
        self.assertTrue(cm.exception.refused)
        self.assertIn("(403)", cm.exception.message)
        calls = (("/api/handover/redeem", {"ticket": "0" * 32, "secret": new_secret(), "to": B_ID, "key": self.b.key, "serial": 1}),
                 ("/api/handover/lease", {"handles": ["0" * 32], "to": B_ID, "key": self.b.key}),
                 ("/api/handover/poke", {"from": B_ID, "key": self.b.key}))
        for path, body in calls:
            with self.assertRaises(peer.PeerError, msg=path) as cm:
                self.b.peers.call_once(A_ID, path, body, budget=False)
            self.assertTrue(cm.exception.refused, path)
            self.assertIn("(403)", cm.exception.message, path)
        self.assertGreaterEqual(self.a.handover.unknown_calls, 1)                # calls from a node that is not approved are counted, nothing more
        self.assertEqual(len(self.b.calls), before)                           # a made no call to b in answer
        with self.assertRaises(peer.PeerError) as cm:                         # and a does not call b
            self.a.peers.hello(B_ID)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(len(self.b.calls), before)

    def test_a_removal_pushed_to_c_is_refused_from_b_afterwards_and_the_banner_shows(self):
        self.remove(self.a, self.b)
        with self.assertRaises(peer.PeerError):
            self.b.peers.hello(C_ID)                                          # c took the removal from a: b's pushes are refused there too
        self.assertIn(self.b.key, self.c.handover.denied_keys())
        banner = self.c.handover.status()["banner"]
        self.assertEqual([(b["by"], b["names"], b["red"]) for b in banner], [("node-01", ["node-02"], False)])

    def test_a_ticket_for_a_removed_node_is_not_made(self):
        self.remove(self.a, self.b)
        with self.assertRaises(Exception) as cm:
            self.ask(self.a, self.device(self.a), to=B_ID)
        self.assertIn("not approved", str(getattr(cm.exception, "message", cm.exception)))


class AllowAgain(Cluster):
    def removal(self, node=None, target=None):
        r = self.row(node or self.a, target or self.c)[0]
        return r["n"], r["epoch"]

    def test_allow_again_makes_the_node_approved_here_and_an_old_deny_going_round_does_not_undo_it(self):
        self.remove(self.a, self.c)
        n, epoch = self.removal()
        status = self.a.handover.allow_again(n, hv.fingerprint(self.c.key), epoch, "Anthony iPad")
        self.assertEqual(status["removed"], [])
        self.assertIsNotNone(self.a.handover.approved(C_ID, self.c.key))
        self.assertIn("node-03 was allowed again by Anthony iPad.", self.said(self.a))
        # b still holds the epoch 1 deny and says so, by a hello each way and by a push
        self.b.peers.hello(A_ID)
        self.settle()
        self.a.peers.hello(B_ID)
        self.b.peers.spread()
        self.settle()
        self.assertEqual(self.a.handover.denied_keys(), set())                # the old deny was not taken again
        self.assertIsNotNone(self.a.handover.approved(C_ID, self.c.key))
        self.assertEqual(self.a.handover.floods, {})                          # and it was no flood: an old deny is ignored without a word
        self.assertFalse(any("sent 1 removals" in t for t in self.said(self.a)))
        self.assertIn(self.c.key, self.b.handover.denied_keys())              # an allow is local: b still denies it

    def test_a_newer_deny_after_an_allow_again_is_taken_and_a_copy_of_the_old_allow_cannot_undo_it(self):
        self.remove(self.a, self.c)
        n, epoch = self.removal()
        typed = hv.fingerprint(self.c.key)
        self.a.handover.allow_again(n, typed, epoch)
        self.remove(self.a, self.c, "Anthony iPad")                           # removed again: epoch 3 (above the tombstone's 2)
        self.assertEqual(self.removal(self.a), (2, 3))                        # the number is not used twice: 1 was the first removal
        self.assertEqual(self.removal(self.b), (1, 3))                        # b's epoch 1 row was renewed
        self.assertIn("node-01 removed node-03 from this cluster again.", self.said(self.b))
        for node, status in ((self.a, 404), (self.b, 409)):                   # the old Allow again, sent a second time: a has a new number, b a new epoch
            with self.assertRaises(hv.HandoverError, msg=node.name) as cm:
                node.handover.allow_again(n, typed, epoch)
            self.assertEqual(cm.exception.status, status)
            self.assertIn(self.c.key, node.handover.denied_keys())
        # the epochs only go up: 3 now, a deny at 2 or 3 changes nothing, a deny at 4 renews
        self.assertEqual(self.a.handover.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 2}], "node-02"), [])
        self.assertEqual(self.a.handover.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 3}], "node-02"), [])
        self.assertEqual(self.a.handover.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 4}], "node-02"), ["node-03"])
        self.assertEqual(self.removal(self.a), (2, 4))

    def test_the_tombstone_is_local_it_stays_across_a_restart_and_is_never_sent(self):
        self.remove(self.a, self.c)
        n, epoch = self.removal()
        self.a.handover.allow_again(n, hv.fingerprint(self.c.key), epoch)
        self.assertNotIn(b"tombstone", json.dumps(self.a.handover.deny_wire()).encode())
        out = answer(self.a.peers, {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}, "10.0.0.9")
        self.assertNotIn("tombstones", out)
        self.assertEqual(out.get("denied", []), [])                           # a has nothing to say about c any more
        cold = hv.Handover(self.a.state, self.a.box, self.a.settings, lambda: {"id": A_ID, "name": "node-01"}, event=self.a.events, openssl=self.exe)
        cold.load()
        self.assertEqual(cold.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 1}], "node-02"), [])
        self.assertEqual(cold.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 2}], "node-02"), [])
        self.assertEqual(cold.denied_keys(), set())
        self.assertEqual(cold.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 3}], "node-02"), ["node-03"])
        self.assertEqual(cold.denied_keys(), {self.c.key})

    def test_allow_again_needs_the_fingerprint_the_epoch_the_owner_saw_and_a_removal_that_is_there(self):
        self.remove(self.a, self.c)
        n, epoch = self.removal()
        for bad, kind in (((n, hv.fingerprint(self.b.key), epoch), hv.HandoverError), ((n, "", epoch), hv.HandoverError),
                          ((n, hv.fingerprint(self.c.key), epoch + 1), hv.HandoverError), ((n + 5, hv.fingerprint(self.c.key), epoch), hv.HandoverError),
                          ((True, hv.fingerprint(self.c.key), epoch), hv.HandoverError), ((n, hv.fingerprint(self.c.key), True), hv.HandoverError),
                          ((n, hv.fingerprint(self.c.key), 0), hv.HandoverError), ((n, 5, epoch), hv.HandoverError)):
            with self.assertRaises(kind, msg=str(bad)):
                self.a.handover.allow_again(*bad)
        self.assertEqual(self.a.handover.denied_keys(), {self.c.key})        # nothing was changed
        self.a.handover.allow_again(n, hv.fingerprint(self.c.key).upper().replace(" ", ":"), epoch)    # spaces, colons and case do not matter
        self.assertEqual(self.a.handover.denied_keys(), set())

    def test_the_way_back_from_a_node_that_denied_the_others(self):
        """A compromised approved node (b) denies c. a takes it (accepted by design), the Owner removes b, then allows again everything b denied."""
        self.b.handover.deny_add(self.c.key, "node-03", "node-02")
        self.b.peers.spread()
        self.settle()
        self.assertEqual(self.a.handover.denied_keys(), {self.c.key})
        self.assertEqual(self.a.handover.status()["banner"][0]["by"], "node-02")
        self.remove(self.a, self.b)
        self.assertIn(self.b.key, self.a.handover.denied_keys())
        self.assertIsNone(self.a.handover.approved(C_ID, self.c.key))
        status = self.a.handover.allow_again_by("node-02", "Anthony iPad")
        self.assertEqual([r["name"] for r in status["removed"]], ["node-02"])  # only what b sent is lifted; b's own removal stays
        self.assertIsNotNone(self.a.handover.approved(C_ID, self.c.key))
        with self.assertRaises(hv.HandoverError) as cm:
            self.a.handover.allow_again_by("node-02")
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(peer.PeerError):                               # b is out: its calls are refused and it cannot deny c again
            self.b.peers.hello(A_ID)
        self.assertIsNotNone(self.a.handover.approved(C_ID, self.c.key))
        self.assertEqual(self.a.handover.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 1}], "node-02"), [])    # and b's old deny is old


class CatchingUp(Cluster):
    def test_a_removal_that_came_while_a_node_was_off_is_caught_up_when_it_talks_to_anybody(self):
        self.b.directory[A_ID] = [self.dead().where]
        self.a.directory[B_ID] = [self.dead().where]                          # b is off, as far as a can tell
        self.remove(self.a, self.c)
        self.assertEqual(self.c.handover.denied_keys(), set())
        self.assertIn(B_ID, self.a.peers._owed)                               # a remembers that b was not told
        self.assertIsNotNone(self.b.handover.approved(C_ID, self.c.key))      # and b still trusts c
        _d, res = self.carry_from(self.c, self.b, "Mac from c")
        self.assertTrue(self.alive_on(self.b, res["token"]))
        self.b.directory[A_ID] = [self.a.where]                               # b is back and asks a something, anything
        self.b.peers.hello(A_ID)
        self.settle()
        self.assertEqual(self.b.handover.denied_keys(), {self.c.key})
        self.assertFalse(self.alive_on(self.b, res["token"]))                 # what c carried to b goes the moment b hears
        self.assertEqual(self.b.handover.status()["removed"][0]["epoch"], 1)

    def test_the_node_that_pressed_the_button_tries_again_by_itself_after_a_while(self):
        dead = self.dead()
        self.a.directory[B_ID] = [dead.where]
        self.remove(self.a, self.c)
        self.assertEqual(self.b.handover.denied_keys(), set())
        self.a.directory[B_ID] = [self.b.where]                               # b is back
        self.clock.t += peer.PUSH_RETRY - 1
        self.a.peers.retry_owed()
        self.settle()
        self.assertEqual(self.b.handover.denied_keys(), set())                # not yet
        self.clock.t += 2
        self.a.leases.tick()                                                  # the lease worker's tick does it
        self.settle()
        self.assertEqual(self.b.handover.denied_keys(), {self.c.key})
        self.assertNotIn(B_ID, self.a.peers._owed)


class Levers(Cluster):
    def test_a_push_that_fails_charges_its_target_and_never_the_node_wide_count(self):
        self.a.directory[B_ID] = [self.dead().where]
        self.a.handover.remove_node(C_ID, "Anthony")
        for _ in range(8):
            self.a.peers.push_all(True)
        self.assertEqual(len(self.a.peers._out_node.hits), 0)
        self.assertTrue(any(t.over() for t in self.a.peers._out_target._map.values()))      # the target's own allowance is what ran out
        self.a.handover.deny_remove(self.c.key)                               # c is allowed again, and a's other calls still work
        self.assertEqual(self.a.peers.hello(C_ID)["serial"], 1)

    def test_pushes_a_node_sends_use_the_allowance_of_its_own_key_and_cost_no_failure(self):
        def push(sender, i, source):
            body = {"from": sender.id, "key": sender.key, "serial": 1, "deny": sender.handover.deny_digest(),
                    "denied": [{"key": "%064x" % i, "name": "n%d" % i, "epoch": 1}]}
            return answer(self.a.peers, body, source, key=sender.key)
        for i in range(1, peer.PUSHES_PER_KEY + 6):
            self.assertEqual(push(self.b, i, "10.0.0.7")["serial"], 1)            # the call is answered every time
        taken = {k for k in self.a.handover.denied_keys()}
        self.assertEqual(len(taken), peer.PUSHES_PER_KEY)                          # but only the first allowance of them were read
        self.assertFalse(self.a.peers._in_blocked("10.0.0.7"))                    # no failure was charged to anybody
        push(self.c, 900, "10.0.0.8")                                             # another key has its own allowance
        self.assertIn("%064x" % 900, self.a.handover.denied_keys())
        self.assertEqual(len(self.a.peers._out_node.hits), 0)

    def test_a_bad_push_is_a_bad_call_charged_to_its_sender_only(self):
        good = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": "0" * 16}
        too_many = [{"key": "%064x" % i, "name": "n", "epoch": 1} for i in range(hv.WIRE_ENTRIES + 1)]
        for extra in ({"denied": "text"}, {"denied": too_many}, {"allowed": [self.c.key]}, {"tombstones": []}, {"denied": [5, {"key": 1}]} ):
            body = dict(good, **extra)
            try:
                answer(self.a.peers, body, "10.0.0.6", key=self.b.key)
            except peer.PeerError as e:
                self.assertEqual(e.status, 400, extra)
            self.assertEqual(self.a.handover.denied_keys(), set(), extra)
        self.assertFalse(self.a.peers._out_node.hits)
        self.assertEqual(answer(self.a.peers, dict(good), "10.0.0.9", key=self.b.key)["serial"], 1)   # another sender is not affected

    def test_a_node_that_is_not_approved_can_push_nothing_and_a_peer_can_never_allow_anybody(self):
        d = self.node("node-04", "d" * 32, now=self.clock)                       # a certificate from the root, but on no list
        with self.assertRaises(peer.PeerError) as cm:
            answer(self.a.peers, {"from": d.id, "key": d.key, "serial": 1, "deny": "0" * 16,
                                  "denied": [{"key": self.c.key, "name": "node-03", "epoch": 1}]}, "10.0.0.5", key=d.key)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.a.handover.denied_keys(), set())
        self.assertEqual(self.a.handover.unknown_calls, 1)
        self.remove(self.a, self.c)
        for body in ({"denied": []}, {"denied": [{"key": "9" * 64, "name": "x", "epoch": 1}]}):       # whatever an approved peer says, the rows stay
            answer(self.a.peers, dict({"from": B_ID, "key": self.b.key, "serial": 1, "deny": "0" * 16}, **body), "10.0.0.4", key=self.b.key)
        self.assertIn(self.c.key, self.a.handover.denied_keys())


class Banner(Cluster):
    def test_the_banner_names_who_removed_whom_and_goes_red_for_more_than_one_in_a_minute(self):
        now = time.time()
        self.a.handover.take_denies([{"key": "5" * 64, "name": "node-05", "epoch": 1}], "node-02", now)
        first = self.a.handover.status()["banner"]
        self.assertEqual([(b["by"], b["names"], b["red"], b["text"]) for b in first],
                         [("node-02", ["node-05"], False, "node-02 removed node-05 from this cluster")])
        self.a.handover.take_denies([{"key": "6" * 64, "name": "node-06", "epoch": 1}], "node-02", now + 5)
        second = self.a.handover.status()["banner"]
        self.assertTrue(second[0]["red"])
        self.assertEqual(second[0]["names"], ["node-05", "node-06"])
        self.assertTrue(all(isinstance(r["epoch"], int) and r["from_peer"] for r in self.a.handover.status()["removed"]))


def cold(node):
    """The same node started again: a Handover that reads the files."""
    h = hv.Handover(node.state, node.box, node.settings, lambda: {"id": node.id, "name": node.name}, event=node.events, openssl=node.exe)
    h.load()
    return h


class Storage(Cluster):
    """deny.json: bounded in bytes, read back whole, never silently empty; epochs at both ends; numbers; tombstones."""

    def keys(self, start, count):
        return ["%064x" % (start + i) for i in range(count)]

    def test_the_worst_case_file_is_read_back_whole_after_a_restart(self):
        h = self.a.handover
        long_by = "\u00e9" * 64                                                # 6 bytes each once written: the longest a row can be
        for key in self.keys(1, hv.MAX_DENY):
            h.deny_add(key, "n" * 64, long_by)
        for key in self.keys(1, hv.MAX_DENY):                                 # every one allowed again: 256 tombstones
            h.deny_remove(key)
        for key in self.keys(1000, hv.MAX_DENY):                              # and 256 rows again
            h.deny_add(key, "n" * 64, long_by)
        import os
        size = os.path.getsize(os.path.join(self.a.state, "deny.json"))
        self.assertGreater(size, hv.MAX_JSON)                                 # past the old 64 KiB limit that used to empty it
        self.assertLessEqual(size, hv.DENY_READ_LIMIT)
        again = cold(self.a)
        self.assertIsNone(again.deny_problem)
        self.assertEqual(again.denied_keys(), h.denied_keys())
        self.assertEqual(len(again.denied_keys()), hv.MAX_DENY)
        self.assertEqual(len(again._tombs), hv.MAX_DENY)
        self.assertEqual(again.take_denies([{"key": self.keys(1, 1)[0], "name": "x", "epoch": 1}], "node-02"), [])    # the tombstones held
        self.assertEqual(again.deny_digest(), h.deny_digest())

    def test_a_file_that_cannot_be_used_is_never_taken_for_no_file(self):
        import os
        path = os.path.join(self.a.state, "deny.json")
        for junk in (b" " * (hv.DENY_READ_LIMIT + 10), b"{not json", b'{"entries": 5}'):
            with open(path, "wb") as f:
                f.write(junk)
            h = cold(self.a)
            self.assertEqual(h.denied_keys(), set())
            self.assertIn("could not be used", h.deny_problem)
            self.assertIn("NO removal is in force", h.status()["deny_problem"])
            self.assertTrue(os.path.exists(path + ".unreadable"))             # kept for repair
            self.assertFalse(os.path.exists(path))
        h.deny_add("5" * 64, "x", "y")                                         # and a node can go on removing nodes
        self.assertEqual(cold(self.a).denied_keys(), {"5" * 64})
        self.assertIsNone(cold(self.a).deny_problem)

    def test_epochs_at_both_ends(self):
        top = hv.MAX_EPOCH
        h = self.a.handover
        key = self.c.key
        entry = lambda e: [{"key": key, "name": "node-03", "epoch": e}]
        self.assertEqual(h.take_denies(entry(top), "node-02"), [])            # a peer cannot pin a key at the top, or one below it
        self.assertEqual(h.take_denies(entry(top - 1), "node-02"), [])
        self.assertEqual(h.take_denies(entry(top + 5), "node-02"), [])
        self.assertEqual(h.take_denies(entry(top - 2), "node-02"), ["node-03"])
        row = h.status()["removed"][0]
        h.allow_again(row["n"], hv.fingerprint(key), row["epoch"])             # tombstone at top - 1
        h.remove_node(C_ID)                                                    # removed again at the top
        self.assertEqual(h.status()["removed"][0]["epoch"], top)
        row = h.status()["removed"][0]
        h.allow_again(row["n"], hv.fingerprint(key), row["epoch"])             # tombstone stays at top - 1, never above
        self.assertEqual(h._tomb(key)["epoch"], top - 1)
        again = cold(self.a)
        self.assertEqual(again._tomb(key)["epoch"], top - 1)
        self.assertEqual(again.take_denies(entry(top - 2), "node-02"), [])    # the pinned deny, replayed, stays out
        again.remove_node(C_ID)
        self.assertEqual(again.status()["removed"][0]["epoch"], top)          # and a removal here still beats the tombstone

    def test_a_stored_epoch_out_of_range_is_clamped_not_set_to_one(self):
        import json as j, os
        key, other = self.c.key, self.b.key
        data = {"v": 1, "entries": [{"key": key, "name": "node-03", "by": "x", "n": 1, "epoch": 10 ** 9 + 5}],
                "tombstones": [{"key": other, "name": "node-02", "epoch": 2 * 10 ** 9}]}
        with open(os.path.join(self.a.state, "deny.json"), "w") as f:
            j.dump(data, f)
        h = cold(self.a)
        self.assertEqual(h.status()["removed"][0]["epoch"], hv.MAX_EPOCH)
        self.assertEqual(h._tomb(other)["epoch"], hv.MAX_EPOCH - 1)
        self.assertEqual(h.take_denies([{"key": other, "name": "node-02", "epoch": 1}], "node-03"), [])

    def test_a_renewal_belongs_to_the_node_that_renewed_it_and_counts_in_the_banner_and_the_cap(self):
        h = self.a.handover
        h.remove_node(C_ID, "Anthony")                                         # the Owner's own removal
        self.assertFalse(h.status()["removed"][0]["from_peer"])
        h.take_denies([{"key": self.c.key, "name": "node-03", "epoch": 5}], "node-02")
        row = h.status()["removed"][0]
        self.assertEqual((row["epoch"], row["from_peer"]), (5, True))
        self.assertEqual(h.status()["banner"][0]["names"], ["node-03"])
        h.remove_node(B_ID, "Anthony")
        self.assertEqual(h.allow_again_by("node-02")["removed"][0]["name"], "node-02")      # only what that peer sent
        # the peer share of the file is full: a renewal of the Owner's own row is not taken
        for i, by in enumerate(("p1", "p2", "p3")):
            h.take_denies([{"key": "%064x" % (500 + i * 64 + j), "name": "n", "epoch": 1} for j in range(64)][:64], by)
        self.assertEqual(sum(1 for r in h.deny_entries() if r.get("peer")), hv.PEER_DENY_CAP)
        h2 = self.a.handover
        h2.deny_add("7" * 64, "own", "Anthony")
        self.assertEqual(h2.take_denies([{"key": "7" * 64, "name": "own", "epoch": 9}], "p4"), [])
        self.assertEqual([r for r in h2.deny_entries() if r["key"] == "7" * 64][0]["epoch"], 1)

    def test_tombstones_from_a_flood_are_evicted_before_the_owners(self):
        old = hv.MAX_TOMBS
        hv.MAX_TOMBS = 4
        self.addCleanup(setattr, hv, "MAX_TOMBS", old)
        h = self.a.handover
        o1, o2, o3, p1, p2, p3 = self.keys(1, 6)
        for k in (o1, o2, o3):
            h.deny_add(k, "own", "Anthony")
        h.take_denies([{"key": k, "name": "p", "epoch": 1} for k in (p1, p2, p3)], "node-02")
        for k in (o1, p1, o2, p2, p3, o3):
            h.deny_remove(k)
        self.assertEqual(sorted(t["key"] for t in h._tombs if not t.get("peer")), sorted([o1, o2, o3]))
        self.assertEqual(len(h._tombs), 4)

    def test_owed_pushes_are_pruned_and_a_busy_slot_is_tried_at_the_next_tick(self):
        import threading
        peers = self.a.peers
        peers._owed["e" * 32] = 0.0                                            # a node that is not approved at all
        peers.retry_owed()
        self.assertNotIn("e" * 32, peers._owed)
        self.a.handover.remove_node(C_ID, "Anthony")
        lock = peers._slots.setdefault(B_ID, threading.Lock())
        lock.acquire()
        try:
            peers.push_all(True)                                               # b has a call running: 429
        finally:
            lock.release()
        self.assertLessEqual(peers._owed[B_ID], self.clock())                 # due at the next tick, not in five minutes
        self.assertEqual(self.b.handover.denied_keys(), set())
        peers.retry_owed()
        self.settle()
        self.assertEqual(self.b.handover.denied_keys(), {self.c.key})

    def test_a_removal_number_is_never_used_twice_even_after_a_restart(self):
        h = self.a.handover
        h.remove_node(C_ID, "Anthony")
        self.assertEqual(h.status()["removed"][0]["n"], 1)
        row = h.status()["removed"][0]
        h.allow_again(row["n"], hv.fingerprint(self.c.key), row["epoch"])     # no rows left
        again = cold(self.a)
        again.remove_node(C_ID, "Anthony")
        self.assertEqual(again.status()["removed"][0]["n"], 2)


class Api(FleetApiBase):
    """The Owner's routes on the real secure listener."""

    def setUp(self):
        super().setUp()
        from pvj import lease as lease_mod
        self.with_certificate()
        self.b = self.peer_b()
        self.peers.push_spawn = lambda fn: fn()
        self.other = "d" * 32
        nodes = [{"id": ME, "name": "testbox", "key": self.hv.own_key(), "carry": "full"},
                 {"id": B_ID, "name": "node-02", "key": self.b.key, "carry": "full"},
                 {"id": self.other, "name": "leyline", "key": "2" * 64, "carry": "full"}]
        text, sig = self.sign_list(1, nodes=nodes)
        self.assertEqual(self.post_list(text, sig)[0], 200)
        self.leases = lease_mod.Leases(self.hv, self.peers, self.auth, self.box, event=self.api.event, log=self.lines.append)
        self.api.leases = self.leases
        self.hv.on_change = self.leases.sweep
        self.hv.set_accepting(True)
        self.auth.add_node_device("Far iPad", "full", B_ID, "00000001")
        self.auth.add_node_device("Leyline iPad", "full", self.other, "00000002")

    def sources(self):
        return sorted(r["node"] for r in self.auth.node_records())

    def removed(self):
        return self.scall("GET", "/api/fleet", token=self.full)[1]["removed"]

    def test_the_owner_removes_a_node_and_the_devices_it_carried_are_signed_out_here_at_once(self):
        self.assertEqual(self.sources(), [B_ID, self.other])
        st, out, _ = self.scall("POST", "/api/fleet/remove", {"id": B_ID}, token=self.full)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["signed_out"], 1)
        self.assertEqual(self.sources(), [self.other])                       # the other approved node's device stays
        self.assertEqual([(r["name"], r["epoch"], r["from_peer"]) for r in out["removed"]], [("node-02", 1, False)])
        self.assertIsNone(self.hv.approved(B_ID, self.b.key))
        self.assertIn(("handover", "node-02 was removed from this cluster by owner laptop (on testbox). 1 device it carried was signed out.", "owner laptop"), self.said())
        self.assertNotIn(self.b.key, json.dumps(out))
        self.assertNotIn(hv.fingerprint(self.b.key), json.dumps(out))
        self.assertIn(self.other, self.peers._owed)                           # leyline is not on the network: it is owed the push, the press still worked
        self.assertEqual(self.scall("POST", "/api/fleet/remove", {"id": B_ID}, token=self.full)[0], 404)      # already out

    def test_bad_requests(self):
        for body in ({}, {"id": "nope"}, {"id": B_ID, "x": 1}, {"id": 5}):
            self.assertEqual(self.scall("POST", "/api/fleet/remove", body, token=self.full)[0], 400, body)
        self.assertEqual(self.scall("POST", "/api/fleet/remove", {"id": "e" * 32}, token=self.full)[0], 404)
        self.assertEqual(self.scall("POST", "/api/fleet/remove", {"id": ME}, token=self.full)[0], 404)      # never itself
        self.assertEqual(self.hv.denied_keys(), set())

    def test_allow_again_by_the_owner_with_the_fingerprint_and_the_epoch_and_a_replay_is_refused(self):
        self.scall("POST", "/api/fleet/remove", {"id": B_ID}, token=self.full)
        row = self.removed()[0]
        body = {"n": row["n"], "epoch": row["epoch"], "typed": hv.fingerprint(self.b.key)}
        self.assertEqual(self.scall("POST", "/api/fleet/allow", dict(body, typed=hv.fingerprint("2" * 64)), token=self.full)[0], 400)
        self.assertEqual(self.scall("POST", "/api/fleet/allow", dict(body, epoch=row["epoch"] + 1), token=self.full)[0], 409)
        self.assertEqual(len(self.removed()), 1)
        st, out, _ = self.scall("POST", "/api/fleet/allow", body, token=self.full)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["removed"], [])
        self.assertIsNotNone(self.hv.approved(B_ID, self.b.key))
        self.assertEqual(self.scall("POST", "/api/fleet/allow", body, token=self.full)[0], 404)             # the same request again
        self.scall("POST", "/api/fleet/remove", {"id": B_ID}, token=self.full)                              # removed again: epoch 3
        self.assertEqual(self.removed()[0]["epoch"], 3)
        self.assertEqual(self.scall("POST", "/api/fleet/allow", body, token=self.full)[0], 404)             # the old request cannot undo the new deny (new number)
        self.assertEqual(self.scall("POST", "/api/fleet/allow", dict(body, n=self.removed()[0]["n"]), token=self.full)[0], 409)    # nor with the new number and the old epoch
        self.assertIsNone(self.hv.approved(B_ID, self.b.key))
        for bad in ({}, {"n": 1}, {"n": 1, "epoch": 3, "typed": 5}, {"n": 1, "epoch": 3, "typed": "x", "more": 1}):
            self.assertEqual(self.scall("POST", "/api/fleet/allow", bad, token=self.full)[0], 400, bad)

    def test_allow_again_everything_one_node_denied(self):
        self.hv.take_denies([{"key": "5" * 64, "name": "node-05", "epoch": 1}, {"key": "6" * 64, "name": "node-06", "epoch": 1}], "leyline")
        self.assertEqual(len(self.removed()), 2)
        self.assertEqual(self.scall("POST", "/api/fleet/allow/by", {"by": "nobody"}, token=self.full)[0], 404)
        self.assertEqual(self.scall("POST", "/api/fleet/allow/by", {}, token=self.full)[0], 400)
        st, out, _ = self.scall("POST", "/api/fleet/allow/by", {"by": "leyline"}, token=self.full)
        self.assertEqual((st, out["removed"]), (200, []))
        self.assertEqual(self.hv.take_denies([{"key": "5" * 64, "name": "node-05", "epoch": 1}], "leyline"), [])     # the old deny stays out

    def test_only_an_owner_over_the_secure_connection_can_remove_or_allow_again(self):
        row_body = {"n": 1, "epoch": 1, "typed": hv.fingerprint(self.b.key)}
        for path, body in (("/api/fleet/remove", {"id": B_ID}), ("/api/fleet/allow", row_body), ("/api/fleet/allow/by", {"by": "leyline"})):
            self.assertEqual(self.scall("POST", path, body)[0], 401, path)                                  # nobody
            self.assertEqual(self.scall("POST", path, body, token=self.view)[0], 403, path)                 # a guest
            self.assertEqual(self.scall("POST", path, body, token=self.operator)[0], 403, path)             # an operator
            self.assertEqual(self.call("POST", path, body, token=self.full)[0], 403, path)                  # the Owner, over plain http
        self.assertEqual(self.hv.denied_keys(), set())
        self.assertEqual(self.sources(), [B_ID, self.other])
        self.assertEqual(self.scall("POST", "/api/fleet/remove", {"id": B_ID}, token=self.full)[0], 200)

    def test_the_peer_port_does_not_answer_these_routes(self):
        for path, body in (("/api/fleet/remove", {"id": B_ID}), ("/api/fleet/allow", {"n": 1, "epoch": 1, "typed": "x"}), ("/api/fleet/allow/by", {"by": "x"})):
            self.assertEqual(self.pcall(self.b, path, body)[0], 404, path)
        self.assertEqual(self.hv.denied_keys(), set())


if __name__ == "__main__":
    unittest.main()
