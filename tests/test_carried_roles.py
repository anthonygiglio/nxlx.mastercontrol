# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A device carried from another node, route by route (D132, slice 4c): the inverse of the role test.

tests/test_roles.py says who passes the gate on every route. This module asks the same of a device that was carried here
from an approved node (a record with `via: node`, made by Auth.add_node_device), and proves:

* a carried device never gets more than the role it was carried with, and that role is never higher than either what the
  device has on its home node or what this node gives that node (handover.carried_role: full, live or none);
* a carried Owner from an approved node gets everything a local Owner gets, on every route. That is the owner's choice
  (design/node-handover_2026-11-09.md, section 4: "a carried Owner can do everything, with no exceptions"). So the
  walk compares the carried Owner with the table of the local Owner, and a few of the routes that make permanent access
  (the PIN, invites, recovery codes, removing a local Owner) are run for real;
* what is the Owner's stays the Owner's for a carried Operator and for an Owner whose node gave it only `live`: the switch
  "Accept devices from other nodes", the fleet list, the acceptances, and by name anything that removes or denies a node;
* a carried device cannot make a ticket for any node (no chaining: a third node asks the home node, not this one);
* the handler-level proof covers the routes listed in Handlers below (the ones that make permanent access, the Owner-only
  switch, the ticket, People, GET /api/sound and GET /api/nodes); every other route is proved at the gate only. The records
  the walk uses come from a node that is on no list: approval is enforced when a ticket is redeemed and by the sweep
  (pvj/lease.py), not on each request, and other tests cover that (tests/test_ticket.py, tests/test_lease.py);
* no code outside auth.py, ticket.py, lease.py and handover.py may branch on `via` being "node": a handler that treated
  carried devices specially would not be seen by the walk, so the source is searched (Decisions);
* a route without a decision here fails the test: CARRIED below must name every route the server has, so adding one
  means deciding what a carried device gets. A route that is simply the same as a local device of that role says SAME.
"""
import json
import types
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import handover as hv, policy, ticket as T
from tests import test_roles as R
from tests.test_boxcert import openssls
from tests.test_handover_api import B_ID
from tests.test_roles import ROWS
from tests.test_support import LAN
from tests.test_ticket import new_secret
from tests.test_ticket_http import A_ID, HttpBase

HOME = "e" * 32                 # the node the devices below came from
RANK = {"view": 0, "live": 1, "full": 2}

# What a carried device gets on a route.
SAME = "same"                   # exactly what a local device of its role gets: the gate's answer and the handler's
NO_CHAIN = "no chaining"        # the gate lets it through as it does a local Operator or Owner, then the handler refuses it:
                                # a device that came from another node does not carry to a third one (Tickets.may_carry)

CARRIED = {
    ("POST", "/api/handover/ticket"): NO_CHAIN,
    ("GET", "/api/access"): SAME,
    ("POST", "/api/access/cancel"): SAME,
    ("POST", "/api/access/code"): SAME,
    ("POST", "/api/access/controller"): SAME,
    ("POST", "/api/access/screen"): SAME,
    ("GET", "/api/audio"): SAME,
    ("POST", "/api/audio"): SAME,
    ("GET", "/api/autostart"): SAME,
    ("POST", "/api/autostart"): SAME,
    ("POST", "/api/autostart/test"): SAME,
    ("POST", "/api/blackout"): SAME,
    ("POST", "/api/control"): SAME,
    ("GET", "/api/devices"): SAME,
    ("POST", "/api/devices/invite"): SAME,
    ("POST", "/api/devices/revoke"): SAME,
    ("GET", "/api/dmx"): SAME,
    ("POST", "/api/dmx"): SAME,
    ("GET", "/api/effects"): SAME,
    ("POST", "/api/effects"): SAME,
    ("GET", "/api/effects/chain"): SAME,
    ("POST", "/api/effects/chain"): SAME,
    ("POST", "/api/effects/config"): SAME,
    ("POST", "/api/effects/library"): SAME,
    ("POST", "/api/effects/preset"): SAME,
    ("POST", "/api/effects/presets"): SAME,
    ("POST", "/api/effects/step"): SAME,
    ("POST", "/api/effects/values"): SAME,
    ("GET", "/api/events"): SAME,
    ("POST", "/api/fade"): SAME,
    ("POST", "/api/fadein"): SAME,
    ("POST", "/api/fadeout"): SAME,
    ("GET", "/api/fleet"): SAME,
    ("POST", "/api/fleet/accept"): SAME,
    ("POST", "/api/fleet/accepted"): SAME,
    ("POST", "/api/fleet/accepted/look"): SAME,
    ("POST", "/api/fleet/accepted/remove"): SAME,
    ("POST", "/api/fleet/list"): SAME,
    ("POST", "/api/fleet/sync"): SAME,
    ("POST", "/api/fleet/remove"): SAME,
    ("POST", "/api/fleet/allow"): SAME,
    ("POST", "/api/fleet/allow/by"): SAME,
    ("GET", "/api/graphics"): SAME,
    ("POST", "/api/graphics"): SAME,
    ("GET", "/api/graphics/file"): SAME,
    ("POST", "/api/graphics/file"): SAME,
    ("GET", "/api/graphics/fonts"): SAME,
    ("GET", "/api/graphics/preview.png"): SAME,
    ("POST", "/api/graphics/preview.png"): SAME,
    ("POST", "/api/graphics/remote"): SAME,
    ("GET", "/api/graphics/thumb"): SAME,
    ("POST", "/api/graphics/upload"): SAME,
    ("POST", "/api/guests"): SAME,
    ("GET", "/api/health"): SAME,
    ("GET", "/api/hello"): SAME,
    ("GET", "/api/https"): SAME,
    ("POST", "/api/https/certificate"): SAME,
    ("POST", "/api/https/owner-only"): SAME,
    ("POST", "/api/https/remove"): SAME,
    ("POST", "/api/https/request"): SAME,
    ("GET", "/api/https/request.csr"): SAME,
    ("POST", "/api/https/root"): SAME,
    ("GET", "/api/https/root.crt"): SAME,
    ("POST", "/api/https/undo"): SAME,
    ("GET", "/api/inputs"): SAME,
    ("POST", "/api/logout"): SAME,
    ("GET", "/api/mapper"): SAME,
    ("POST", "/api/mapper"): SAME,
    ("POST", "/api/mapper/nudge"): SAME,
    ("POST", "/api/mapper/remote"): SAME,
    ("GET", "/api/media"): SAME,
    ("POST", "/api/media/delete"): SAME,
    ("GET", "/api/media/import"): SAME,
    ("POST", "/api/media/import"): SAME,
    ("POST", "/api/media/import/cancel"): SAME,
    ("POST", "/api/media/info"): SAME,
    ("POST", "/api/media/rename"): SAME,
    ("POST", "/api/media/upload"): SAME,
    ("GET", "/api/midi"): SAME,
    ("POST", "/api/midi"): SAME,
    ("POST", "/api/midi/learn"): SAME,
    ("POST", "/api/midi/lights"): SAME,
    ("POST", "/api/midi/map"): SAME,
    ("POST", "/api/mix"): SAME,
    ("GET", "/api/modules"): SAME,
    ("POST", "/api/modules/*"): SAME,
    ("GET", "/api/ndi"): SAME,
    ("POST", "/api/ndi"): SAME,
    ("GET", "/api/ndi/discovery"): SAME,
    ("POST", "/api/ndi/discovery"): SAME,
    ("GET", "/api/network"): SAME,
    ("POST", "/api/network/apply"): SAME,
    ("POST", "/api/network/confirm"): SAME,
    ("POST", "/api/network/plan"): SAME,
    ("POST", "/api/network/revert"): SAME,
    ("POST", "/api/network/scan"): SAME,
    ("GET", "/api/nodes"): SAME,
    ("POST", "/api/nodes/discovery"): SAME,
    ("GET", "/api/osc"): SAME,
    ("POST", "/api/osc"): SAME,
    ("POST", "/api/osc/feedback"): SAME,
    ("POST", "/api/osc/key"): SAME,
    ("GET", "/api/osc/messages"): SAME,
    ("GET", "/api/output"): SAME,
    ("POST", "/api/output"): SAME,
    ("POST", "/api/output/av"): SAME,
    ("GET", "/api/overlay"): SAME,
    ("POST", "/api/overlay"): SAME,
    ("GET", "/api/pads"): SAME,
    ("POST", "/api/pads"): SAME,
    ("POST", "/api/pair"): SAME,
    ("POST", "/api/pin/rotate"): SAME,
    ("POST", "/api/pin/show"): SAME,
    ("POST", "/api/pin/unlock"): SAME,
    ("POST", "/api/play"): SAME,
    ("POST", "/api/player/restart"): SAME,
    ("GET", "/api/preview.jpg"): SAME,
    ("POST", "/api/projector"): SAME,
    ("GET", "/api/projectors"): SAME,
    ("POST", "/api/projectors"): SAME,
    ("GET", "/api/qr.svg"): SAME,
    ("GET", "/api/recovery"): SAME,
    ("POST", "/api/recovery/cancel"): SAME,
    ("POST", "/api/recovery/codes"): SAME,
    ("POST", "/api/recovery/usb"): SAME,
    ("GET", "/api/render"): SAME,
    ("POST", "/api/render"): SAME,
    ("POST", "/api/render/cue"): SAME,
    ("GET", "/api/renderer"): SAME,
    ("POST", "/api/renderer"): SAME,
    ("GET", "/api/room"): SAME,
    ("POST", "/api/room"): SAME,
    ("POST", "/api/room/group"): SAME,
    ("POST", "/api/room/scene"): SAME,
    ("GET", "/api/schedule"): SAME,
    ("POST", "/api/schedule"): SAME,
    ("GET", "/api/screen"): SAME,
    ("POST", "/api/screen"): SAME,
    ("POST", "/api/screen/confirm"): SAME,
    ("POST", "/api/screen/revert"): SAME,
    ("POST", "/api/session"): SAME,
    ("GET", "/api/shaders"): SAME,
    ("POST", "/api/shaders"): SAME,
    ("POST", "/api/shaders/play"): SAME,
    ("POST", "/api/shaders/preset"): SAME,
    ("POST", "/api/shaders/presets"): SAME,
    ("POST", "/api/shaders/step"): SAME,
    ("POST", "/api/shaders/values"): SAME,
    ("GET", "/api/sound"): SAME,
    ("POST", "/api/sound"): SAME,
    ("GET", "/api/sound/levels"): SAME,
    ("POST", "/api/sound/map"): SAME,
    ("POST", "/api/sound/source"): SAME,
    ("GET", "/api/sources"): SAME,
    ("POST", "/api/sources"): SAME,
    ("GET", "/api/status"): SAME,
    ("GET", "/api/streams"): SAME,
    ("POST", "/api/streams"): SAME,
    ("GET", "/api/support"): SAME,
    ("POST", "/api/support/config"): SAME,
    ("POST", "/api/support/extend"): SAME,
    ("POST", "/api/support/login"): SAME,
    ("POST", "/api/support/start"): SAME,
    ("POST", "/api/support/stop"): SAME,
    ("GET", "/api/sync"): SAME,
    ("POST", "/api/sync"): SAME,
    ("GET", "/api/system"): SAME,
    ("POST", "/api/system/clock"): SAME,
    ("GET", "/api/system/diagnostics"): SAME,
    ("POST", "/api/system/factory-reset"): SAME,
    ("POST", "/api/system/poweroff"): SAME,
    ("POST", "/api/system/reboot"): SAME,
    ("POST", "/api/system/settings/export"): SAME,
    ("POST", "/api/system/settings/import"): SAME,
    ("GET", "/api/system/update"): SAME,
    ("POST", "/api/system/update"): SAME,
    ("POST", "/api/system/update/upload"): SAME,
    ("GET", "/api/tempo"): SAME,
    ("POST", "/api/tempo"): SAME,
    ("POST", "/api/tempo/link"): SAME,
    ("POST", "/api/tempo/tap"): SAME,
    ("POST", "/api/testpattern"): SAME,
    ("POST", "/api/testtone"): SAME,
    ("GET", "/api/theme"): SAME,
    ("POST", "/api/theme"): SAME,
    ("POST", "/api/theme/add"): SAME,
    ("POST", "/api/theme/export"): SAME,
    ("POST", "/api/theme/remove"): SAME,
    ("POST", "/api/vibes"): SAME,
}


def carried_floor(api):
    """The lowest role each route asks for, as the server itself has it (before a test stubs the handlers)."""
    floors = {k: need for k, (need, _h) in api.routes().items()}
    floors.update(policy.OUTSIDE)
    floors[("POST", "/api/modules/*")] = "full"
    return floors


class WalkBase(R.RolesBase):
    """The Roles rig (an Owner, an Operator and a Guest at the studio) plus an Owner and an Operator carried from HOME, and an
    Owner that HOME carried with `live` only (what handover.carried_role gives it)."""
    stub = R.GateTableTest.stub
    ask = R.GateTableTest.ask

    def setUp(self):
        super().setUp()
        self.carried = {}
        capped = hv.carried_role("full", "live")
        self.assertEqual(capped, "live")
        for i, (name, role) in enumerate((("full", "full"), ("live", "live"), ("capped", capped))):
            token, _rec = self.auth.add_node_device("Anthony iPad, via node-01", role, HOME, "%08x" % (i + 1))
            device = self.auth.authenticate(token)
            self.assertEqual((device["via"], device["role"]), ("node", role))
            self.carried[name] = device
        self.local = {"full": self.owner, "live": self.operator}

    def routes(self):
        return set(self.api.routes()) | set(policy.OUTSIDE) | {("POST", "/api/modules/*")}


class Decisions(WalkBase):
    def test_every_route_has_a_decision_for_carried_devices_and_none_is_left_over(self):
        routes = self.routes()
        self.assertEqual(sorted(routes - set(CARRIED)), [],
                         "routes without a decision in tests/test_carried_roles.py: decide what a device carried from another node gets "
                         "(SAME is the answer for a route that is no different from a local device of that role)")
        self.assertEqual(sorted(set(CARRIED) - routes), [], "decisions for routes that are gone")
        self.assertEqual(sorted(set(CARRIED) - set(ROWS)), [])
        self.assertEqual(sorted(k for k, v in CARRIED.items() if v != SAME), [("POST", "/api/handover/ticket")])
        self.assertEqual(set(CARRIED.values()), {SAME, NO_CHAIN})

    def test_nothing_outside_the_four_files_branches_on_a_device_being_carried(self):
        import glob
        import os
        import re
        pvj_dir = os.path.dirname(policy.__file__)
        near = re.compile(r"via\W{0,12}[\"']node[\"']|[\"']node[\"']\W{0,12}via|SHARED_VIA")
        found = []
        for path in sorted(glob.glob(os.path.join(pvj_dir, "*.py"))):
            name = os.path.basename(path)
            if name in ("auth.py", "ticket.py", "lease.py", "handover.py"):
                continue
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if near.search(line):
                        found.append("%s:%d: %s" % (name, n, line.strip()))
        self.assertEqual(found, [], "a handler treats carried devices specially: the walk cannot see that; decide it in "
                                    "tests/test_carried_roles.py or move it into auth.py")

    def test_the_handover_namespace_is_the_ticket_and_nothing_else_so_a_new_route_there_is_noticed(self):
        """The peer calls (hello, redeem, lease, poke) are on the peer listener, not in the route table (policy.PEER_ONLY)."""
        self.assertEqual(sorted(k for k in self.routes() if k[1].startswith("/api/handover")), [("POST", "/api/handover/ticket")])
        for key in policy.PEER_ONLY:
            self.assertNotIn(key, self.routes())
            self.assertNotIn(key, ROWS)


class Walk(WalkBase):
    def test_a_carried_device_gets_what_a_local_device_of_its_role_gets_on_every_route(self):
        """The Owner's column and the Operator's column of tests/test_roles.py, for the carried device and the local one
        side by side: no route is accidentally shut to a carried Owner, or opened to a carried Operator."""
        self.stub()
        wrong = []
        for role, column in (("full", 4), ("live", 3)):
            for (method, path), row in sorted(ROWS.items()):
                carried = self.ask(method, path, {}, self.carried[role], LAN)
                local = self.ask(method, path, {}, self.local[role], LAN)
                if not carried == local == row[column]:
                    wrong.append("%s %s %s: carried %s, local %s, the row says %s" % (role, method, path, carried, local, row[column]))
        self.assertEqual(wrong, [])

    def test_a_carried_device_never_gets_more_than_its_role_on_any_route(self):
        floors = carried_floor(self.api)
        self.stub()
        wrong = []
        for name, device in sorted(self.carried.items()):
            for key in sorted(ROWS):
                if self.ask(key[0], key[1], {}, device, LAN) == 200 and floors[key] is not None \
                        and RANK[floors[key]] > RANK[device["role"]]:
                    wrong.append("%s (%s) passes %s %s, which asks for %s" % (name, device["role"], key[0], key[1], floors[key]))
        self.assertEqual(wrong, [])

    def test_the_role_here_is_never_higher_than_the_home_role_or_the_carry(self):
        expect = {("full", "full"): "full", ("full", "live"): "live", ("live", "full"): "live", ("live", "live"): "live"}
        for home in ("view", "live", "full", "owner", "", None, 7):
            for carry in ("full", "live", "none", "view", "owner", "", None, 7):
                got = hv.carried_role(home, carry)
                self.assertEqual(got, expect.get((home, carry)), (home, carry))
                if got is not None:
                    self.assertLessEqual(RANK[got], min(RANK[home], RANK[hv.CARRY_ROLE[carry]]), (home, carry))
        for home in ("view", "live", "full"):
            self.assertIsNone(hv.carried_role(home, "none"))                     # a node that takes nobody in makes no record
        self.assertIsNone(hv.carried_role("view", "full"))                       # and a Guest never carries
        with self.assertRaises(Exception):
            self.auth.add_node_device("guest", "view", HOME, "00000099")         # nor can a record be made for one
        self.assertEqual([d["role"] for d in self.settings.data["devices"] if d.get("via") == "node"].count("view"), 0)

    def test_the_owners_things_stay_the_owners_for_a_carried_operator_and_an_owner_capped_at_operator(self):
        owner_routes = sorted(k for k, row in ROWS.items() if row[4] == 200 and row[3] == 403 and row[0] == 401)
        self.assertGreater(len(owner_routes), 60)
        self.stub()
        wrong = []
        for method, path in owner_routes:
            for who, want in (("live", 403), ("capped", 403), ("full", 200)):
                got = self.ask(method, path, {}, self.carried[who], LAN)
                if got != want:
                    wrong.append("%s on %s %s answered %s, not %s" % (who, method, path, got, want))
        self.assertEqual(wrong, [])

    def test_the_switch_and_everything_about_trusting_a_node_is_the_owners_so_a_new_one_is_held_to_it(self):
        """By name, so the removal of a node from the cluster (slice 4d) cannot be added as an Operator's route: whatever
        has fleet, cluster or deny in its path, and every POST under /api/nodes, must be an Owner's route."""
        routes = self.routes()
        floors = carried_floor(self.api)
        trust = sorted(k for k in routes if k[1].startswith("/api/fleet") or "cluster" in k[1] or "deny" in k[1]
                       or (k[0] == "POST" and k[1].startswith("/api/nodes")))
        self.assertIn(("POST", "/api/fleet/accept"), trust)                      # the switch that is the lock
        self.assertGreaterEqual(len(trust), 8)
        self.assertEqual([k for k in trust if floors[k] != "full"], [], "these must be the Owner's")
        self.stub()
        for method, path in trust:
            for who in ("live", "capped"):
                self.assertEqual(self.ask(method, path, {}, self.carried[who], LAN), 403, (who, method, path))

    def test_a_support_session_and_the_boxs_own_callers_are_not_carried_devices(self):
        """Nothing in the carry rules gives a support login or a controller anything: they stay held to LEGACY_LIVE."""
        self.assertFalse(policy.held_to_legacy(self.carried["full"]))
        self.assertFalse(policy.is_guest(self.carried["full"]))
        self.assertFalse(policy.is_controller(self.carried["full"]))
        self.assertEqual(policy.reach(self.carried["full"], False), "owner")
        self.assertEqual(policy.reach(self.carried["live"], False), "operator")
        self.assertEqual(policy.reach(self.carried["capped"], False), "operator")

    def test_a_carried_device_does_not_carry_on_the_ticket_check(self):
        """The check that the handler makes (Tickets.may_carry reads the stored record, not what the request says)."""
        asker = types.SimpleNamespace(auth=self.auth)
        for who in ("full", "live", "capped"):
            why = T.Tickets.may_carry(asker, self.carried[who])
            self.assertIn("from another node", why, who)
            lie = dict(self.carried[who], via=None)                              # a request cannot say otherwise
            self.assertIn("from another node", T.Tickets.may_carry(asker, lie), who)


@unittest.skipUnless(openssls(), "no openssl on this machine")
class Handlers(HttpBase):
    """The same, with the real handlers on the real secure listener: this node (testbox) approves node-01 and node-02."""
    def carry(self, role, name="Anthony iPad, via node-01", node=A_ID, device="ab01cd02"):
        token, _rec = self.auth.add_node_device(name, role, node, device)
        return token

    def people(self, token):
        st, out = self.scall("GET", "/api/devices", token=token)[:2]
        self.assertEqual(st, 200, out)
        return out["devices"]

    def victim(self, role):
        _t, dev = self.auth._add_device("victim " + role, role)
        return dev["id"]

    def test_a_device_carried_from_a_node_makes_no_ticket_for_any_node(self):
        st, out = self.scall("POST", "/api/handover/ticket", {"to": B_ID, "proof": T.proof_of(new_secret())}, token=self.owner)[:2]
        self.assertEqual(st, 200, out)                                           # a local Owner does, so the node is approved and reachable
        before = dict(self.tickets._tickets)
        for role, ident in (("full", "ab01cd01"), ("live", "ab01cd02")):
            token = self.carry(role, device=ident)
            for to in (B_ID, A_ID):                                              # a third node, and the node it came from
                st, out = self.scall("POST", "/api/handover/ticket", {"to": to, "proof": T.proof_of(new_secret())}, token=token)[:2]
                self.assertEqual(st, 403, (role, to, out))
                self.assertIn("from another node", out["error"])
        self.assertEqual(self.tickets._tickets, before)                          # nothing was made
        said = [e["text"] for e in self.api.events.events()]
        self.assertEqual(sum("was not made" in t and "from another node" in t for t in said), 4)

    def test_a_carried_owner_does_what_a_local_owner_does_where_it_makes_permanent_access(self):
        carried = self.carry("full")
        for who, token in (("local", self.owner), ("carried", carried)):
            self.assertEqual(self.scall("POST", "/api/pin/show", {}, token=token)[0], 200, who)
            for role in ("view", "live"):
                st, out = self.scall("POST", "/api/devices/invite", {"name": "n", "role": role}, token=token)[:2]
                self.assertEqual(st, 200, (who, role, out))
            self.assertEqual(self.scall("POST", "/api/access/code", {"role": "view", "minutes": 60}, token=token)[0], 200, who)
            self.assertEqual(self.scall("POST", "/api/recovery/codes", {}, token=token)[0], 200, who)
            self.assertEqual(self.scall("GET", "/api/fleet", token=token)[0], 200, who)
            self.assertEqual(self.scall("POST", "/api/fleet/accept", {"on": True}, token=token)[0], 200, who)
            gone = self.victim("full")                                           # a local Owner, removed
            self.assertEqual(self.scall("POST", "/api/devices/revoke", {"id": gone}, token=token)[0], 200, who)
            self.assertIsNone(self.auth.device(gone))
            self.assertTrue(any(d["role"] == "full" for d in self.people(token)), who)       # and he sees Owners, as an Owner does
        self.assertEqual(self.scall("POST", "/api/pin/rotate", {}, token=carried)[0], 200)

    def test_a_carried_operator_does_what_a_local_operator_does_and_no_more(self):
        carried = self.carry("live")
        capped = self.carry("live", name="Owner capped, via node-02", node=B_ID, device="cd02ef03")
        for who, token in (("local", self.operator), ("carried", carried), ("capped", capped)):
            for path in ("/api/pin/show", "/api/recovery/codes", "/api/pin/rotate", "/api/fleet/accept", "/api/fleet/list",
                         "/api/fleet/accepted", "/api/fleet/accepted/remove", "/api/fleet/sync", "/api/fleet/remove", "/api/fleet/allow",
                         "/api/fleet/allow/by", "/api/nodes/discovery"):
                self.assertEqual(self.scall("POST", path, {}, token=token)[0], 403, (who, path))
            self.assertEqual(self.scall("GET", "/api/fleet", token=token)[0], 403, who)
            st, out = self.scall("POST", "/api/devices/invite", {"name": "n", "role": "live"}, token=token)[:2]
            self.assertEqual(st, 403, (who, out))                                # a guest link, never more
            self.assertEqual(self.scall("POST", "/api/devices/invite", {"name": "n", "role": "view"}, token=token)[0], 200, who)
            self.assertEqual(self.scall("POST", "/api/devices/revoke", {"id": self.victim("full")}, token=token)[0], 403, who)    # never an Owner
            self.assertEqual(self.scall("POST", "/api/devices/revoke", {"id": self.victim("live")}, token=token)[0], 200, who)     # another Operator
            self.assertFalse(any(d["role"] == "full" for d in self.people(token)), who)       # and never sees an Owner

    def test_what_a_handler_shows_a_carried_device_is_what_it_shows_a_local_one_of_that_role(self):
        pairs = (("local operator", self.operator, "carried operator", self.carry("live", device="aa000001")),
                 ("local owner", self.owner, "carried owner", self.carry("full", device="aa000002")))
        for lname, local, cname, carried in pairs:
            for path in ("/api/sound", "/api/nodes"):
                ls, lout = self.scall("GET", path, token=local)[:2]
                cs, cout = self.scall("GET", path, token=carried)[:2]
                self.assertEqual(ls, cs, (path, lname))
                self.assertEqual(json.dumps(lout, sort_keys=True), json.dumps(cout, sort_keys=True), (path, cname))
        st, out = self.scall("GET", "/api/sound", token=self.carry("live", device="aa000003"))[:2]
        if st == 200:                                       # the addresses are the Owner's to see
            self.assertEqual(out["config"]["in_hosts"], [])
        self.assertEqual(self.scall("GET", "/api/nodes", token=self.carry("live", device="aa000004"))[0], 200)

    def test_a_carried_device_signs_out_when_it_removes_its_own_way_in_and_cannot_remove_itself_as_an_operator(self):
        token = self.carry("live")
        me = [d["id"] for d in self.settings.data["devices"] if d.get("via") == "node"][0]
        st, out = self.scall("POST", "/api/devices/revoke", {"id": me}, token=token)[:2]
        self.assertEqual(st, 403, out)                                           # as a local Operator: use Log out
        self.assertEqual(self.scall("POST", "/api/logout", {}, token=token)[0], 200)
        self.assertEqual(self.scall("GET", "/api/status", token=token)[0], 401)
