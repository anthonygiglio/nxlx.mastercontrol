# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The lease on a carried device (D132, slice 4b; pvj/lease.py): two real nodes (certificates from a throwaway root, the real peer
listeners, real Handover, Peers, Auth, Tickets and Leases) and fake clocks. A good answer of the home node renews; the lapse is
24 hours after the last answer, not a second before, by the wall clock only while it is trusted and otherwise by the monotonic
clock; a demotion lowers the carried role and nothing raises it; a removal signs the device out (by the poke, and by the
check when the poke never arrives); a home node that does not answer leaves the lease to run to the lapse; a deny, the switch
or a list without the node ends everything at once; the leases survive a restart; pokes have allowances of their own that
an outsider cannot fill; and no handle is in any event, log line or answer."""
import json
import os
import time
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import auth as auth_mod, lease as L
from pvj.settings import Settings
from tests.test_peer import A_ID, B_ID, C_ID, Clock
from tests.test_ticket import Rig, SOURCE

DAY = L.LEASE_SECONDS


class LeaseRig(Rig):
    """node-01 (A) and node-02 (B) with leases, a fake monotonic clock (`self.clock`, shared), a fake wall clock and a boot id. Nothing is
    paired yet: a test calls `self.pair()` (the list) or accepts by fingerprint."""

    def setUp(self):
        super().setUp()
        self.wall = Clock(time.time())
        self.boot = "boot-1"
        for n in (self.a, self.b):
            n.leases = L.Leases(n.handover, n.peers, n.auth, n.box, event=n.events, log=n.log_lines.append, now=self.clock, wall=self.wall,
                                boot=lambda: self.boot, spawn=lambda fn: fn())
            n.tickets.leases = n.leases
            n.api.leases = n.leases
            n.auth.on_lent_gone = n.leases.poke
            n.handover.on_change = n.leases.sweep

    def carry(self, name="Anthony iPad", role="full"):
        """A device paired on A opens B: (the device on A, the sign-in result, B's record)."""
        dev = self.device(self.a, name=name, role=role)
        out, secret = self.ask(self.a, dev)
        res = self.go(out, secret)
        self.assertEqual(res["result"], "signed-in", res)
        return dev, res, self.record(dev["id"])

    def record(self, a_device_id, node=None):
        for r in (node or self.b).auth.node_records():
            if r["device"] == a_device_id:
                return r

    def set_role(self, node, device_id, role):
        for d in node.settings.data["devices"]:
            if d["id"] == device_id:
                d["role"] = role
        node.settings.save()

    def alive(self, token):
        return self.b.auth.authenticate(token) is not None

    def calls(self, node, path):
        return [c for c in node.calls if c[1] == path]

    def everything_said(self):
        return json.dumps([n.events.lines + n.log_lines for n in (self.a, self.b)])


class Renewing(LeaseRig):
    def test_the_hand_over_gives_the_record_a_lease_and_a_good_answer_renews_it(self):
        self.pair()
        dev, res, rec = self.carry()
        self.assertRegex(rec["lease"]["h"], r"^[0-9a-f]{32}$")
        self.assertEqual(rec["lease"]["m"], 1000.0)
        self.clock.t += 3600
        self.assertTrue(self.b.leases.check_node(A_ID))
        again = self.record(dev["id"])
        self.assertEqual((again["lease"]["h"], again["lease"]["m"]), (rec["lease"]["h"], 4600.0))      # same handle, a new time
        self.clock.t += 23 * 3600                       # 24 hours after the sign-in, 23 after the last answer
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.clock.t += 3600                            # now 24 hours after the last answer
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))

    def test_the_worker_asks_about_every_device_of_a_node_in_one_call_about_every_ten_minutes(self):
        self.pair()
        devs = [self.carry(name="d%d" % i) for i in range(3)]
        self.b.leases.tick()                            # a fresh node has not been asked yet: the first tick asks
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), 1)
        self.clock.t += 599
        self.b.leases.tick()
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), 1)
        self.clock.t += 2
        self.b.leases.tick()
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), 2)
        for dev, _res, _rec in devs:
            self.assertEqual(self.record(dev["id"])["lease"]["m"], 1601.0)

    def test_the_same_device_again_gets_a_new_handle_and_the_old_one_is_dead(self):
        self.pair()
        dev, _res, rec = self.carry()
        old = rec["lease"]["h"]
        out, secret = self.ask(self.a, self.a.auth.device(dev["id"]))
        self.assertEqual(self.go(out, secret)["result"], "signed-in")
        new = self.record(dev["id"])["lease"]["h"]
        self.assertNotEqual(old, new)
        got = self.a.leases.answer({"handles": [old, new], "to": B_ID, "key": self.b.key}, self.b.key, SOURCE)
        self.assertEqual([x["state"] for x in got["leases"]], ["gone", "ok"])

    def test_a_refusal_an_unreadable_answer_and_a_node_that_is_off_renew_nothing(self):
        self.pair()
        dev, res, rec = self.carry()
        self.clock.t += 700
        self.a.handover.deny_add(self.b.key, "node-02", "someone")          # A refuses B from now on
        self.assertFalse(self.b.leases.check_node(A_ID))
        self.assertEqual(self.record(dev["id"])["lease"], rec["lease"])
        self.assertTrue(self.alive(res["token"]))                            # it runs until the lapse
        self.assertTrue(any("did not answer" in t for t in self.said(self.b)))


class Lapse(LeaseRig):
    def test_signed_out_24_hours_after_the_last_answer_and_not_a_second_before_on_the_monotonic_clock(self):
        self.pair()
        dev, res, _rec = self.carry()
        self.b.directory[A_ID] = [self.dead().where]                          # the home node is switched off
        self.assertFalse(self.b.leases.check_node(A_ID))
        self.clock.t += DAY - 1
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.clock.t += 1
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))
        self.assertIn("Anthony iPad, via node-01 was signed out: node-01 has not answered for 24 hours.", self.said(self.b))
        self.assertIsNone(self.record(dev["id"]))

    def test_the_wall_clock_is_used_only_while_it_is_trusted(self):
        self.pair()
        self.b.trusted = True
        _dev, res, rec = self.carry()
        self.assertTrue(rec["lease"]["t"])
        self.clock.t += DAY * 2                           # the monotonic clock says two days; the trusted wall clock says none
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.wall.t += DAY - 1
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.wall.t += 1
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))

    def test_when_the_clock_is_not_trusted_a_wrong_wall_clock_changes_nothing(self):
        self.pair()
        _dev, res, _rec = self.carry()                    # stamped on an untrusted clock
        self.wall.t += DAY * 30                           # the wall clock jumps: it is not believed
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.b.trusted = True                             # trusted now, but the record was stamped untrusted: the monotonic clock still counts
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.clock.t += DAY
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))

    def test_a_trusted_stamp_read_on_an_untrusted_clock_falls_back_to_the_monotonic_clock(self):
        self.pair()
        self.b.trusted = True
        _dev, res, _rec = self.carry()
        self.b.trusted = False                            # the clock is no longer believed
        self.wall.t += DAY * 5
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))
        self.clock.t += DAY
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))

    def test_a_record_from_before_leases_starts_its_24_hours_at_the_first_sweep(self):
        self.pair()
        token, rec = self.b.auth.add_node_device("old, via node-01", "live", A_ID, "ab12")
        self.b.leases.sweep()
        stored = self.b.auth.node_records()[0]["lease"]
        self.assertNotIn("h", stored)                     # no handle: it can never be renewed
        self.clock.t += DAY - 1
        self.b.leases.sweep()
        self.assertIsNotNone(self.b.auth.authenticate(token))
        self.clock.t += 1
        self.b.leases.sweep()
        self.assertIsNone(self.b.auth.authenticate(token))


class Following(LeaseRig):
    def test_a_demotion_on_the_home_node_lowers_the_record_and_a_promotion_is_ignored(self):
        self.pair()
        dev, res, rec = self.carry(role="full")
        self.assertEqual(rec["role"], "full")
        self.set_role(self.a, dev["id"], "live")
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.assertEqual(self.b.auth.authenticate(res["token"])["role"], "live")
        self.assertIn("Anthony iPad, via node-01 was lowered to Operator: node-01 now gives it that.", self.said(self.b))
        self.set_role(self.a, dev["id"], "full")          # raised again on A: a carried role is never raised
        self.clock.t += 700
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.assertEqual(self.b.auth.authenticate(res["token"])["role"], "live")

    def test_a_device_that_became_a_guest_on_the_home_node_is_signed_out(self):
        self.pair()
        dev, res, _rec = self.carry()
        self.set_role(self.a, dev["id"], "view")
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.assertFalse(self.alive(res["token"]))
        self.assertTrue(any("no longer gives it a role that carries" in t for t in self.said(self.b)))

    def test_a_lower_carry_at_this_node_lowers_the_record_without_asking_anybody(self):
        self.pair()
        _dev, res, _rec = self.carry()
        before = len(self.calls(self.a, "/api/handover/lease"))
        self.list_for([(self.a, "full"), (self.b, "live")], 2)                 # a new signed list: B now gives Operator at most
        self.assertEqual(self.b.auth.authenticate(res["token"])["role"], "live")
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), before)
        self.list_for([(self.a, "full"), (self.b, "none")], 3)                 # and then nothing
        self.assertFalse(self.alive(res["token"]))

    def test_the_role_of_a_record_can_only_go_down(self):
        self.pair()
        _dev, _res, rec = self.carry(role="live")
        self.assertTrue(self.b.auth.update_node_device(rec["id"], role="full"))
        self.assertEqual(self.b.auth.node_records()[0]["role"], "live")
        self.assertTrue(self.b.auth.update_node_device(rec["id"], role="view"))
        self.assertEqual(self.b.auth.node_records()[0]["role"], "view")


class Removing(LeaseRig):
    def test_removing_the_device_on_the_home_node_signs_it_out_at_once_by_the_poke(self):
        self.pair()
        dev, res, _rec = self.carry()
        self.assertTrue(self.alive(res["token"]))
        self.a.auth.revoke(dev["id"])
        self.assertEqual(len(self.calls(self.b, "/api/handover/poke")), 1)
        self.assertFalse(self.alive(res["token"]))
        self.assertIn("Anthony iPad, via node-01 was signed out: node-01 no longer has it paired.", self.said(self.b))

    def test_removing_every_device_on_the_home_node_signs_them_all_out(self):
        self.pair()
        carried = [self.carry(name="d%d" % i) for i in range(3)]
        self.a.auth.revoke_all()
        for _dev, res, _rec in carried:
            self.assertFalse(self.alive(res["token"]))

    def test_without_the_poke_the_next_check_removes_it_within_ten_minutes(self):
        self.pair()
        dev, res, _rec = self.carry()
        self.a.directory[B_ID] = [self.dead().where]                          # the poke cannot get through
        self.a.auth.revoke(dev["id"])
        self.assertTrue(self.alive(res["token"]))
        self.clock.t += 601
        self.b.leases.tick()
        self.assertFalse(self.alive(res["token"]))

    def test_removing_one_device_leaves_the_others_carried(self):
        self.pair()
        gone, gone_res, _ = self.carry(name="one")
        _keep, keep_res, _ = self.carry(name="two")
        self.a.auth.revoke(gone["id"])
        self.assertFalse(self.alive(gone_res["token"]))
        self.assertTrue(self.alive(keep_res["token"]))

    def test_a_poke_that_has_to_wait_is_sent_at_the_next_tick(self):
        self.pair()
        first, first_res, _ = self.carry(name="one")
        second, second_res, _ = self.carry(name="two")
        self.a.auth.revoke(first["id"])
        self.a.auth.revoke(second["id"])                                       # within the gap: left for the tick
        self.assertEqual(len(self.calls(self.b, "/api/handover/poke")), 1)
        self.assertTrue(self.alive(second_res["token"]))                       # not yet told
        self.clock.t += L.OUT_POKE_GAP + 1
        self.a.leases.tick()
        self.assertEqual(len(self.calls(self.b, "/api/handover/poke")), 2)
        self.assertTrue(self.alive(second_res["token"]))                       # B had just checked: this poke waits for B's own tick
        self.b.leases.tick()
        self.assertFalse(self.alive(second_res["token"]))


class Ending(LeaseRig):
    def test_a_deny_ends_everything_from_that_node_at_once(self):
        self.pair()
        _d1, r1, _ = self.carry(name="one")
        _d2, r2, _ = self.carry(name="two", role="live")
        self.b.handover.deny_add(self.a.key, "node-01", "someone")           # no sweep is called by the test: the change does it
        self.assertFalse(self.alive(r1["token"]))
        self.assertFalse(self.alive(r2["token"]))
        self.assertTrue(any("Signed out 2 devices carried from node-01" in t for t in self.said(self.b)))

    def test_a_deny_taken_from_a_peer_ends_it_too(self):
        self.pair()
        _dev, res, _ = self.carry()
        self.b.handover.take_denies([{"key": self.a.key, "name": "node-01"}], "node-03")
        self.assertFalse(self.alive(res["token"]))

    def test_the_switch_off_ends_everything_and_turning_it_on_brings_nothing_back(self):
        self.pair()
        _d1, r1, _ = self.carry(name="one")
        _d2, r2, _ = self.carry(name="two")
        self.b.handover.set_accepting(False)
        self.b.leases.sweep()
        self.assertFalse(self.alive(r1["token"]))
        self.assertFalse(self.alive(r2["token"]))
        self.b.handover.set_accepting(True)
        self.b.leases.sweep()
        self.assertFalse(self.alive(r1["token"]))
        self.assertEqual(self.b.auth.node_records(), [])

    def test_a_list_that_leaves_the_node_out_ends_it(self):
        self.pair()
        _dev, res, _ = self.carry()
        self.list_for([(self.b, "full")], 2)
        self.assertFalse(self.alive(res["token"]))

    def test_removing_an_acceptance_by_fingerprint_ends_it(self):
        for n, other in ((self.a, self.b), (self.b, self.a)):
            n.directory[other.id] = [other.named()]
            n.handover.accept_node(other.id, other.name, other.key, "full", other.key)
        _dev, res, _ = self.carry()
        self.assertTrue(self.alive(res["token"]))
        self.b.handover.remove_acceptance(A_ID)
        self.assertFalse(self.alive(res["token"]))

    def test_the_deny_beats_a_lease_that_has_months_left(self):
        self.pair()
        _dev, res, _ = self.carry()
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.b.handover.deny_add(self.a.key, "node-01", "someone")
        self.assertFalse(self.alive(res["token"]))
        self.assertFalse(self.b.leases.check_node(A_ID))                        # nothing left to ask about, and it is not asked


class Restarting(LeaseRig):
    def reopen(self, node):
        s = Settings(os.path.join(node.state, "settings.json"))
        s.load()
        return s, auth_mod.Auth(s)

    def test_the_lease_is_stored_with_the_record_and_survives_a_restart(self):
        self.pair()
        dev, res, rec = self.carry()
        _s, auth2 = self.reopen(self.b)
        again = [r for r in auth2.node_records() if r["device"] == dev["id"]][0]
        self.assertEqual(again["lease"], rec["lease"])
        self.assertIsNotNone(auth2.authenticate(res["token"]))
        self.assertNotIn("lease", json.dumps(auth2.list_devices()))              # not in what People shows

    def test_after_a_restart_on_a_trusted_clock_the_lapse_is_still_measured_from_the_last_answer(self):
        self.pair()
        self.b.trusted = True
        _dev, res, _ = self.carry()
        s2, auth2 = self.reopen(self.b)
        self.boot = "boot-2"
        leases2 = L.Leases(self.b.handover, self.b.peers, auth2, self.b.box, event=self.b.events, now=self.clock, wall=self.wall, boot=lambda: self.boot)
        self.wall.t += DAY - 1
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))
        self.wall.t += 1
        leases2.sweep()
        self.assertIsNone(auth2.authenticate(res["token"]))

    def reboot(self, boot="boot-2", node=None):
        node = node or self.b
        s2, auth2 = self.reopen(node)
        self.boot = boot
        self.clock.t = 50.0                                                       # the monotonic clock started again
        return auth2, L.Leases(node.handover, node.peers, auth2, node.box, event=node.events, now=self.clock, wall=self.wall, boot=lambda: self.boot)

    def test_after_a_restart_on_an_untrusted_clock_a_silent_home_node_means_signed_out_at_the_end_of_the_grace(self):
        self.pair()
        _dev, res, rec = self.carry()                                             # stamped by the monotonic clock of boot-1
        auth2, leases2 = self.reboot()
        self.b.directory[A_ID] = [self.dead().where]                              # the home node does not answer
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))
        self.clock.t += L.CLOCK_GRACE - 1
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))                    # still waiting for the clock
        self.assertEqual(auth2.node_records()[0]["lease"], rec["lease"])         # and nothing was written without an answer
        self.clock.t += 1
        leases2.sweep()
        self.assertIsNone(auth2.authenticate(res["token"]))
        self.assertIn("Anthony iPad, via node-01 was signed out: this node restarted and node-01 has not answered since.", self.said(self.b))

    def test_after_a_restart_a_home_node_that_is_up_renews_at_the_first_tick_and_the_device_stays(self):
        self.pair()
        _dev, res, _ = self.carry()
        auth2, leases2 = self.reboot()
        leases2.tick()                                                            # asks at once: the answer is the only thing that stamps
        self.assertEqual(auth2.node_records()[0]["lease"]["b"], "boot-2")
        self.clock.t += L.CLOCK_GRACE + 100
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))
        self.clock.t += DAY - L.CLOCK_GRACE - 101
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))
        self.clock.t += L.CLOCK_GRACE + 101
        leases2.sweep()
        self.assertIsNone(auth2.authenticate(res["token"]))

    def test_after_a_restart_a_failed_first_check_is_tried_again_at_the_next_tick_inside_the_grace(self):
        self.pair()
        _dev, res, _ = self.carry()
        auth2, leases2 = self.reboot()
        self.b.directory[A_ID] = [self.dead().where]                              # the home node is still booting
        self.clock.t += L.TICK_EVERY
        leases2.tick()
        self.assertEqual(auth2.node_records()[0]["lease"]["b"], "boot-1")        # nothing answered
        self.b.directory[A_ID] = [self.a.named()]                                 # a minute later it is up
        self.clock.t += L.TICK_EVERY
        leases2.tick()
        self.assertEqual(auth2.node_records()[0]["lease"]["b"], "boot-2")        # renewed, well inside the grace
        self.clock.t += L.CLOCK_GRACE
        leases2.sweep()
        self.assertIsNotNone(auth2.authenticate(res["token"]))

    def test_with_no_boot_id_a_record_the_home_node_just_renewed_is_not_signed_out_at_the_end_of_the_grace(self):
        self.pair()
        self.boot = ""
        _dev, res, _ = self.carry()
        self.clock.t += 700
        self.assertTrue(self.b.leases.check_node(A_ID))                           # answered in this process
        self.clock.t += L.CLOCK_GRACE
        self.b.leases.started = 0.0
        self.b.leases.sweep()
        self.assertTrue(self.alive(res["token"]))

    def test_an_empty_boot_id_cannot_tell_one_boot_from_another(self):
        self.pair()
        self.boot = ""
        _dev, res, _ = self.carry()
        self.b.directory[A_ID] = [self.dead().where]
        self.clock.t += L.CLOCK_GRACE
        self.b.leases.started = 0.0
        self.b.leases.sweep()
        self.assertFalse(self.alive(res["token"]))

    def test_the_home_node_remembers_what_it_lent_across_a_restart(self):
        self.pair()
        dev, _res, rec = self.carry()
        s2, auth2 = self.reopen(self.a)
        leases2 = L.Leases(self.a.handover, self.a.peers, auth2, self.a.box, now=self.clock, wall=self.wall)
        got = leases2.answer({"handles": [rec["lease"]["h"]], "to": B_ID, "key": self.b.key}, self.b.key, SOURCE)
        self.assertEqual(got["leases"], [{"state": "ok", "role": "full"}])
        auth2.revoke(dev["id"])
        got = leases2.answer({"handles": [rec["lease"]["h"]], "to": B_ID, "key": self.b.key}, self.b.key, SOURCE)
        self.assertEqual(got["leases"], [{"state": "gone"}])


class Allowances(LeaseRig):
    def poke_body(self):
        return {"from": A_ID, "key": self.a.key}

    def test_a_poke_from_a_node_with_nothing_lent_costs_a_lookup_and_a_count(self):
        self.pair()
        seen = (len(self.a.calls), len(self.said(self.b)), len(self.said(self.a)))
        self.assertEqual(self.b.leases.poked(self.poke_body(), self.a.key, SOURCE), {"v": 1})
        self.assertEqual(self.b.leases.unknown_pokes, 1)
        self.assertEqual((len(self.a.calls), len(self.said(self.b)), len(self.said(self.a))), seen)       # nobody was called, nothing was written

    def test_pokes_are_limited_for_each_approved_node_and_the_allowance_comes_back(self):
        self.pair()
        for _ in range(L.POKES_PER_MINUTE):
            self.b.leases.poked(self.poke_body(), self.a.key, SOURCE)
        with self.assertRaises(L.LeaseError) as cm:
            self.b.leases.poked(self.poke_body(), self.a.key, SOURCE)
        self.assertEqual(cm.exception.status, 429)
        self.clock.t += 61
        self.assertEqual(self.b.leases.poked(self.poke_body(), self.a.key, SOURCE), {"v": 1})

    def test_a_poke_that_starts_a_check_at_most_every_gap_and_the_rest_wait_for_the_tick(self):
        self.pair()
        self.carry()
        for _ in range(3):
            self.b.leases.poked(self.poke_body(), self.a.key, SOURCE)
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), 1)
        self.clock.t += L.POKE_GAP + 1
        self.b.leases.tick()
        self.assertEqual(len(self.calls(self.a, "/api/handover/lease")), 2)

    def test_junk_from_outsiders_fills_only_their_own_address_and_touches_no_other_throttle(self):
        self.pair()
        stranger = {"from": C_ID, "key": "d" * 64}
        for _ in range(L.STRANGER_FAILS):
            with self.assertRaises(L.LeaseError) as cm:
                self.b.leases.poked(stranger, "d" * 64, "10.9.9.9")
            self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(L.LeaseError) as cm:
            self.b.leases.poked(stranger, "d" * 64, "10.9.9.9")
        self.assertEqual(cm.exception.status, 429)                                # that address only
        with self.assertRaises(L.LeaseError) as cm:
            self.b.leases.poked(stranger, "d" * 64, "10.9.9.10")
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.b.leases.poked(self.poke_body(), self.a.key, "10.9.9.9"), {"v": 1})        # the real node, even from that address
        for junk in ({}, {"from": 1, "key": 2}, {"from": A_ID}, {"from": A_ID, "key": self.a.key, "x": 1}):
            with self.assertRaises(L.LeaseError):
                self.b.leases.poked(junk, self.a.key, "10.9.9.11")
        # the throttles that tickets, redeem and the peer channel use were never touched
        self.assertEqual(self.b.tickets._a_source._map, {})
        self.assertEqual(self.b.tickets._b_source._map, {})
        self.assertEqual(self.b.tickets._b_attempts._map, {})
        self.assertEqual(self.b.peers._in_fail._map, {})
        self.assertEqual(len(self.b.peers._out_node.hits), 0)

    def test_a_claimed_key_that_the_handshake_did_not_show_is_a_stranger(self):
        self.pair()
        with self.assertRaises(L.LeaseError) as cm:
            self.b.leases.poked({"from": A_ID, "key": self.a.key}, "e" * 64, "10.9.9.9")      # the handshake showed another key
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.b.handover.unknown_calls, 1)

    def test_lease_questions_are_limited_for_each_approved_node_and_strangers_are_refused(self):
        self.pair()
        _dev, _res, rec = self.carry()
        body = {"handles": [rec["lease"]["h"]], "to": B_ID, "key": self.b.key}
        for _ in range(L.LEASE_CALLS_PER_MINUTE):
            self.a.leases.answer(body, self.b.key, SOURCE)
        with self.assertRaises(L.LeaseError) as cm:
            self.a.leases.answer(body, self.b.key, SOURCE)
        self.assertEqual(cm.exception.status, 429)
        with self.assertRaises(L.LeaseError) as cm:
            self.a.leases.answer(dict(body, to=C_ID, key="d" * 64), "d" * 64, "10.9.9.9")
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.a.tickets._a_source._map, {})

    def test_the_lease_answer_is_for_the_node_the_handle_was_given_to_and_the_body_is_checked(self):
        self.pair()
        _dev, _res, rec = self.carry()
        handle = rec["lease"]["h"]
        self.assertEqual(self.a.leases.answer({"handles": [handle], "to": B_ID, "key": self.b.key}, self.b.key, SOURCE)["leases"][0]["state"], "ok")
        self.assertEqual(self.a.leases.answer({"handles": ["0" * 32], "to": B_ID, "key": self.b.key}, self.b.key, SOURCE)["leases"], [{"state": "gone"}])
        for junk in ({}, {"handles": [], "to": B_ID, "key": self.b.key}, {"handles": ["x"], "to": B_ID, "key": self.b.key},
                     {"handles": [handle] * 9, "to": B_ID, "key": self.b.key}, {"handles": [handle], "to": B_ID}):
            with self.assertRaises(L.LeaseError) as cm:
                self.a.leases.answer(junk, self.b.key, SOURCE)
            self.assertEqual(cm.exception.status, 400, junk)

    def test_a_real_poke_and_a_real_lease_question_travel_over_the_peer_listeners(self):
        self.pair()
        dev, res, _ = self.carry()
        self.a.auth.revoke(dev["id"])
        self.assertFalse(self.alive(res["token"]))
        self.assertEqual(self.calls(self.b, "/api/handover/poke"), [("POST", "/api/handover/poke")])
        self.assertEqual(self.calls(self.a, "/api/handover/lease"), [("POST", "/api/handover/lease")])


class Quiet(LeaseRig):
    def test_the_node_that_does_not_answer_is_said_once_and_the_devices_stay(self):
        self.pair()
        _dev, res, _ = self.carry()
        self.b.directory[A_ID] = [self.dead().where]
        for _ in range(3):
            self.clock.t += 1
            self.b.leases.check_node(A_ID)
        lines = [t for t in self.said(self.b) if "did not answer a check" in t]
        self.assertEqual(len(lines), 1)
        self.assertTrue(self.alive(res["token"]))

    def test_no_handle_is_in_any_event_log_line_answer_or_the_home_nodes_settings(self):
        self.pair()
        dev, res, rec = self.carry()
        handle = rec["lease"]["h"]
        self.clock.t += 700
        self.b.leases.check_node(A_ID)
        self.a.auth.revoke(dev["id"])
        self.b.leases.sweep()
        self.assertNotIn(handle, self.everything_said())
        self.assertNotIn(handle, json.dumps(res))
        self.assertNotIn(handle, json.dumps(self.b.auth.list_devices()))
        self.assertNotIn(handle, json.dumps(self.b.handover.status()))
        dev2, _res2, rec2 = self.carry(name="second")
        with open(os.path.join(self.a.state, "settings.json")) as f:
            text = f.read()
        self.assertNotIn(rec2["lease"]["h"], text)                                # node A keeps only the hash
        self.assertIn(L._hash(rec2["lease"]["h"]), text)
        self.assertNotIn(rec2["lease"]["h"], self.everything_said())


class Review(LeaseRig):
    def test_a_switch_hand_over_keeps_the_lease_handle_and_the_check_renews_it(self):
        self.pair()
        dev = self.device(self.a, role="live")
        out, secret = self.ask(self.a, dev)
        res = self.go(out, secret, current_role="full")                         # already an Owner here: the confirm page
        self.assertEqual(res["result"], "confirm")
        self.assertEqual(set(res), {"result", "handle", "have", "would", "from"})
        done = self.b.tickets.confirm(res["handle"], SOURCE)
        self.assertEqual(done["result"], "signed-in", done)
        rec = self.record(dev["id"])
        self.assertRegex(rec["lease"]["h"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(rec["lease"]["h"], res["handle"])                     # A's handle, not the confirm handle
        self.assertNotIn(rec["lease"]["h"], json.dumps([res, done]))
        self.clock.t += 700
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.assertTrue(self.alive(done["token"]))
        self.assertEqual(self.record(dev["id"])["lease"]["m"], 1700.0)

    def test_a_device_that_opened_the_node_again_during_a_check_keeps_its_new_lease(self):
        self.pair()
        dev, _res, rec = self.carry()
        old = rec["lease"]["h"]
        out, secret = self.ask(self.a, self.a.auth.device(dev["id"]))
        again = self.go(out, secret)
        new = self.record(dev["id"])["lease"]
        self.assertNotEqual(new["h"], old)
        stale = [{"id": rec["id"], "h": old, "role": "live", "stamp": {"ok": 1, "t": False, "m": 1.0, "b": "x"}}]
        self.assertEqual(self.b.auth.apply_node_updates(stale), {})              # the check read the record before the re-open: skipped
        self.assertFalse(self.b.auth.revoke_node_if(rec["id"], old))
        self.assertEqual(self.record(dev["id"])["lease"], new)
        self.assertEqual(self.record(dev["id"])["role"], "full")
        self.assertTrue(self.alive(again["token"]))

    def test_one_check_is_one_save_for_all_the_devices(self):
        self.pair()
        for i in range(3):
            self.carry(name="d%d" % i)
        saves = []
        real = self.b.settings.save
        self.b.settings.save = lambda *a, **k: (saves.append(1), real(*a, **k))[1]
        self.clock.t += 700
        self.assertTrue(self.b.leases.check_node(A_ID))
        self.assertEqual(len(saves), 1)

    def test_a_lowered_role_is_said_once(self):
        self.pair()
        dev, _res, _ = self.carry()
        self.set_role(self.a, dev["id"], "live")
        self.b.leases.check_node(A_ID)
        self.b.leases.sweep()
        self.clock.t += 700
        self.b.leases.check_node(A_ID)
        self.b.leases.sweep()
        self.assertEqual(len([t for t in self.said(self.b) if "was lowered" in t]), 1)

    def test_a_node_that_keeps_poking_while_unreachable_cannot_block_this_nodes_other_calls(self):
        c = self.node("node-03", C_ID, now=self.clock)
        self.list_for([(self.a, "full"), (self.b, "full"), (c, "full")])
        self.b.auth.add_node_device("x, via node-03", "live", C_ID, "ab12", lease=dict(self.b.leases.stamp("1" * 32)))
        self.b.directory[C_ID] = [self.dead().where for _ in range(4)]            # four addresses, none answers
        started = []
        real = self.b.leases.check_node
        self.b.leases.check_node = lambda node: (started.append(node), real(node))[1]
        for _ in range(3):
            self.clock.t += L.POKE_GAP + 1
            self.b.leases.poked({"from": C_ID, "key": c.key}, c.key, SOURCE)
        self.assertEqual(len(started), L.POKE_CHECKS_PER_MINUTE)                  # the third waits for the tick
        self.assertIn(C_ID, self.b.leases._dirty)
        for _ in range(3):
            real(C_ID)
        self.assertEqual(len(self.b.peers._out_node.hits), 0)                     # the node-wide budget was never charged
        dev, res, _rec = self.carry()                                             # and a hand-over from node-01 still works
        self.assertTrue(self.alive(res["token"]))

    def test_removing_every_device_paces_its_pokes(self):
        self.pair()
        ids = {"%032x" % (i + 1) for i in range(L.OUT_POKES_PER_MINUTE + 10)}
        self.a.leases.poke(ids)
        self.assertEqual(len(self.a.leases._out_dirty), 10)                       # the rest wait their turn
        self.clock.t += 61
        self.a.leases.tick()
        self.assertEqual(len(self.a.leases._out_dirty), 0)


if __name__ == "__main__":
    unittest.main()
