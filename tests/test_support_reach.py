# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What a remote support session may do (D84): with the Owner's role, anything that ends when the session ends, and
nothing that outlives it or locks the owner out. Sessions below the Owner's role are as they were (D80).

The table of every route against a support session of each role is tests.test_roles.ROWS (columns 5 to 7), asked
through the gate there; a route without a row fails there. Here: the two lists as data, held against that table, and
every opened and every refused route asked with the real handlers."""
import ipaddress
import json
import os
import types
from unittest import mock

from pvj import boxcare, osc, policy, support as sp, themes
from pvj.api import ApiError
from tests.test_boxcare import Base as CareBase
from tests.test_roles import ROWS, WHO
from tests.test_support import LAN, TUNNEL
from tests.test_theme_api import mine

VIEW, LIVE, FULL = WHO.index("support view"), WHO.index("support live"), WHO.index("support full")
OWNER = WHO.index("owner")

# REFUSED to every support session, whatever its role. Written out by hand: a new route under a refused prefix, or a
# new entry in support.REMOTE_DENY, must be added here with a thought (the test below says which list it is missing from).
REFUSED = {
    # pairing and sessions, invitations
    ("POST", "/api/pair"), ("POST", "/api/session"), ("POST", "/api/devices/invite"),
    # everything under /api/access: codes, links, what is shown on the display, the controller code choice
    ("GET", "/api/access"), ("POST", "/api/access/code"), ("POST", "/api/access/cancel"), ("POST", "/api/access/screen"),
    ("POST", "/api/access/controller"),
    # the PIN and the QR code
    ("POST", "/api/pin/show"), ("POST", "/api/pin/rotate"), ("POST", "/api/pin/unlock"), ("GET", "/api/qr.svg"),
    # the ways back in
    ("GET", "/api/recovery"), ("POST", "/api/recovery/codes"), ("POST", "/api/recovery/cancel"), ("POST", "/api/recovery/usb"),
    # the secure connection
    ("GET", "/api/https"), ("GET", "/api/https/request.csr"), ("GET", "/api/https/root.crt"), ("POST", "/api/https/request"),
    ("POST", "/api/https/certificate"), ("POST", "/api/https/undo"), ("POST", "/api/https/remove"), ("POST", "/api/https/root"),
    ("POST", "/api/https/owner-only"),
    # the session's own settings, start and length
    ("POST", "/api/support/config"), ("POST", "/api/support/start"), ("POST", "/api/support/extend"),
    # power-off and factory reset
    ("POST", "/api/system/poweroff"), ("POST", "/api/system/factory-reset"),
    # the confirm of a network change: only someone at the studio can show that the studio still reaches the box
    ("POST", "/api/network/confirm"),
    # the NDI discovery server on this box (D85): a listener on the network is the owner's, in the room
    ("GET", "/api/ndi/discovery"), ("POST", "/api/ndi/discovery"),
}
# and one module's switch, which the table of routes holds only as /api/modules/*
REFUSED_MODULES = {("POST", "/api/modules/ndi-discovery")}
# OPENED by D84 to a support session of the Owner's role: route -> a body the real handler takes.
OPENED = {
    ("POST", "/api/system/settings/import"): None,          # answered by the server itself: asked through boxcare below
    ("POST", "/api/theme/add"): {"file": json.dumps(mine(id="helper"))},
    ("POST", "/api/theme/remove"): {"id": "helper"},
    ("POST", "/api/osc/key"): {},
    ("POST", "/api/osc/feedback"): {"on": True},
    ("POST", "/api/guests"): {"locked": True},
    ("POST", "/api/devices/revoke"): None,                  # the body names a device: asked below
}


class Base(CareBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.support.log = self.lines.append
        self.api.log = self.lines.append
        self.addCleanup(self.api.osc.stop)
        self.api.theme_store = themes.Store(os.path.join(os.path.dirname(self.settings.path), "addons"))
        self.ready()

    def session(self, role="full"):
        if self.api.support.session is not None:
            self.h("POST", "/api/support/stop", device=self.full_dev)
        st, body = self.start(role=role)
        self.assertEqual(st, 200, body)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        dev = self.api.support.authenticate(login["token"])
        self.assertEqual((dev["role"], dev["remote"]), (role, True))
        return dev

    def did(self):
        """The major lines of the running (or last) session: what the owner must be able to read whatever else was done."""
        return [d["what"] for d in self.settings.data["support_log"][0].get("major", [])]

    def kept(self):
        """The lines nothing can displace: an update, a settings file, a device removed."""
        return [d["what"] for d in self.settings.data["support_log"][0].get("always", [])]

    def routine(self):
        return [d["what"] for d in self.settings.data["support_log"][0].get("did", [])]

    def remote(self, method, path, body, dev):
        if (method, path) in policy.OUTSIDE and (method, path) not in self.api.routes():      # answered by the server itself: its gate
            try:
                self.api.gate(method, path, body, dev, TUNNEL)
                return 200, {}
            except ApiError as e:
                return e.status, {"error": e.message}
        return self.h(method, path, body, dev, TUNNEL)


class TwoListsTest(Base):
    def test_the_refused_list_is_exactly_what_the_guard_refuses_and_every_entry_says_why(self):
        by_guard = {k for k in ROWS if k in sp.REMOTE_DENY or k[1].startswith(sp.REMOTE_DENY_PREFIX)}
        self.assertEqual(sorted(by_guard ^ REFUSED), [], "a route is refused by support.py and not listed here, or the other way round")
        self.assertEqual(set(sp.REMOTE_DENY) - set(ROWS), REFUSED_MODULES, "a refused route that does not exist")
        for table in (sp.REMOTE_DENY, sp.REMOTE_DENY_PREFIX_WHY, sp.REMOTE_OWNER_OPENED):
            for key, why in table.items():
                self.assertTrue(isinstance(why, str) and len(why) > 20, key)
        self.assertEqual(sp.REMOTE_DENY_PREFIX, tuple(sp.REMOTE_DENY_PREFIX_WHY))

    def test_the_two_lists_against_the_table_of_every_route(self):
        """tests.test_roles.ROWS is the table of every route and every asker (a route without a row fails there)."""
        self.assertEqual(sorted(OPENED), sorted(sp.REMOTE_OWNER_OPENED))
        self.assertEqual(set(OPENED) & REFUSED, set())
        for key, row in sorted(ROWS.items()):
            if key in REFUSED:
                self.assertEqual(row[VIEW:FULL + 1], (403, 403, 403), key)
            else:
                # everything else: a support session of the Owner's role is answered as the Owner at the studio is
                self.assertEqual(row[FULL], row[OWNER], key)
            # below the Owner's role nothing changed (D80): what a presenter had, and never a refused route
            self.assertEqual(row[LIVE] == 200, key in policy.LEGACY_LIVE and key not in REFUSED or key in sp.REMOTE_OPEN or row[0] == 200 and key not in REFUSED, key)
        for key in OPENED:
            self.assertEqual(ROWS[key][VIEW:FULL + 1], (403, 403, 200), key)
            self.assertNotIn(key, policy.LEGACY_LIVE)

    def test_every_refused_route_is_refused_by_the_real_handlers_to_every_role(self):
        for role in ("view", "live", "full"):
            dev = self.session(role)
            for method, path in sorted(REFUSED - {("GET", "/api/qr.svg"), ("POST", "/api/pair"), ("POST", "/api/session")}):
                st = self.remote(method, path, {"confirm": "start", "minutes": 60, "allowed": True, "media": "keep", "on": True}, dev)[0]
                self.assertEqual(st, 403, (role, method, path))
            for method, path in (("GET", "/api/qr.svg"), ("POST", "/api/pair"), ("POST", "/api/session")):
                with self.assertRaises(sp.SupportApiError):
                    self.api.support.guard(method, path, dev, TUNNEL)
            self.assertTrue(self.api.support.session, "and none of that ended the session")
            # nothing was changed, and every one of them is in the session's log as refused (the security review, M2)
            self.assertEqual(sorted(self.did()), sorted("was refused %s %s" % k for k in REFUSED - {("GET", "/api/qr.svg"), ("POST", "/api/pair"), ("POST", "/api/session")}), role)
            self.assertEqual(self.routine(), [])

    def test_every_opened_route_is_answered_by_its_real_handler_to_the_owners_role_only(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]}]
        for role in ("view", "live"):
            dev = self.session(role)
            for (method, path), body in sorted(OPENED.items()):
                if body is not None:
                    self.assertEqual(self.remote(method, path, body, dev)[0], 403, (role, path))
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.view_dev["id"]}, dev)[0], 403)
            with self.assertRaises(ApiError) as e:
                self.api.gate("POST", "/api/system/settings/import", {}, dev, TUNNEL)
            self.assertEqual(e.exception.status, 403)
            with self.assertRaises(ApiError) as e:          # and the handler itself, should the table ever change
                self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
            self.assertEqual(e.exception.status, 403)
            for call, body in ((self.api.add_theme, OPENED[("POST", "/api/theme/add")]), (self.api.remove_theme, {"id": "x"}),
                               (self.api.osc_key, {}), (self.api.osc_feedback, {"on": True}), (self.api.revoke, {"id": self.view_dev["id"]})):
                with self.assertRaises(ApiError) as e:
                    call(body, dev, TUNNEL)
                self.assertEqual(e.exception.status, 403, (role, call.__name__))
            self.assertEqual(self.did(), [])
        dev = self.session("full")
        for (method, path), body in OPENED.items():        # in the order written: the theme is added before it is removed
            if body is not None:
                st, out = self.remote(method, path, body, dev)
                self.assertEqual(st, 200, (path, out))
        self.assertIsNone(self.api.gate("POST", "/api/system/settings/import", None, dev, TUNNEL))
        # a studio device's own token through the tunnel is still no login (the guard), opened route or not
        for (method, path), body in OPENED.items():
            if body is not None:
                self.assertEqual(self.h(method, path, body, self.full_dev, TUNNEL)[0], 403, path)
        # and a support login used from the studio's network is none either
        self.assertEqual(self.h("POST", "/api/guests", {"locked": False}, dev, LAN)[0], 403)


class LogTest(Base):
    def test_everything_a_session_changes_is_in_the_support_log_with_what_was_done(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]}]
        dev = self.session("full")
        self.assertEqual(self.remote("POST", "/api/theme/add", OPENED[("POST", "/api/theme/add")], dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/theme/remove", {"id": "helper"}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/osc/key", {"new": True}, dev)[0], 200)
        st, key = self.remote("POST", "/api/osc/key", {}, dev)
        self.assertEqual(st, 200, key)
        self.assertEqual(self.remote("POST", "/api/osc/feedback", {"add": {"name": "Desk", "address": "192.168.1.77", "port": 9000}}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/guests", {"locked": True}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.view_dev["id"]}, dev)[0], 200)
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        did = self.did()
        self.assertEqual(did, ["added a theme", "removed a theme", "made a new OSC key and knows it", "was shown the OSC key",
                               "changed where the box sends its state over OSC", "locked guest controls"])
        entry = self.settings.data["support_log"][0]
        self.assertEqual([d.get("some") for d in entry["major"]], [["helper"], ["helper"], None, None, ["added a target; 1 target(s), off"], None])
        kept = self.kept()
        self.assertEqual(len(kept), 2)
        self.assertIn("removed the guest device " + self.view_dev["id"], kept[0])
        self.assertIn("imported a settings file, sha256 ", kept[1])
        self.assertIs(entry["osc_key"], True, "the page tells the owner to make a new key")
        text = json.dumps(self.settings.data["support_log"]) + "\n".join(self.lines)
        for secret in (key["key"], self.api.osc.key(), "192.168.1.77"):
            self.assertNotIn(secret, text, "what was done, never the value")
        self.assertEqual(len([x for x in self.lines if "remote support support-1: " in x]), 8, "and each is a line in the journal")
        self.assertEqual([d.get("n", 1) for d in entry["major"] + entry["always"]], [1] * 8)
        self.assertNotIn("did", entry, "said once each: an opened route is not also counted as a route that was used")
        # the owner reads it on the Remote support page, and a support login does not
        seen = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]
        self.assertEqual(([d["what"] for d in seen["major"]], [d["what"] for d in seen["always"]]), (did, kept))
        self.assertNotIn("log", self.remote("GET", "/api/support", {}, dev)[1])

    def test_the_log_of_one_session_is_bounded_and_cannot_be_flushed(self):
        """The same thing again is one line with a count; when the list is full the first lines stay and the rest
        are counted. So what a helper did first cannot be pushed out of sight by forty harmless things after it."""
        dev = self.session("full")
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        for n in range(90):
            self.assertEqual(self.remote("POST", "/api/guests", {"locked": n % 2 == 0}, dev)[0], 200)
        entry = self.settings.data["support_log"][0]
        self.assertEqual(([d.get("n", 1) for d in entry["always"]], [d.get("n", 1) for d in entry["major"]]), ([1], [45, 45]))
        self.assertEqual(len([x for x in self.lines if "remote support support-1: " in x]), 3, "a repeat is counted, not written again")
        with open(self.settings.path) as f:
            saved = json.load(f)["support_log"][0]["major"]
        self.assertEqual([d["what"] for d in saved], self.did(), "every line is on the disk at once; a repeat's count goes with the next save")
        self.assertIn("imported a settings file", self.kept()[0])
        for n in range(sp.DID_KEEP + 15):
            self.api.support.note(dev, "thing %d" % n, major=False)
        entry = self.settings.data["support_log"][0]
        self.assertEqual((len(entry["did"]), entry["did_more"]), (sp.DID_KEEP, 15))
        self.assertEqual((len(self.did()), len(self.kept())), (2, 1), "routine lines never touch the others")
        self.api.support.note(dev, "x" * 500 + "\n\x00")
        self.assertLessEqual(max(len(w) for w in self.did()), sp.DID_TEXT)
        before = self.did()
        self.assertEqual(self.h("POST", "/api/guests", {"locked": False}, self.full_dev)[0], 200)       # the owner, at the studio
        self.api.support.note(self.full_dev, "not a support login")
        self.assertEqual(self.did(), before)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        self.api.support.note(dev, "after the end")                     # no session: nothing to write to, and no error
        self.assertEqual(self.did(), before)


class EveryChangeTest(Base):
    def test_every_post_a_session_is_answered_leaves_a_line(self):
        """Not only the routes D84 opened: every POST that a support session of the Owner's role is answered (the table
        of every route, with the handlers stubbed as tests.test_roles does) is in the session's log by its route, and
        never by its body. A new route is covered by itself. The few that change nothing are named in NOT_A_CHANGE."""
        from tests.test_roles import GUEST_BODIES
        real = self.api.routes()
        dev = self.session("full")
        stubbed = {k: (need, (lambda b, d, c: {})) for k, (need, _h) in real.items()}
        self.api.routes = lambda: stubbed
        self.api.set_module = lambda *a: {}
        for key in sp.NOT_A_CHANGE:
            self.assertTrue(key in real or key in policy.OUTSIDE, key)
        missing = []
        for (method, path), row in sorted(ROWS.items()):
            if method != "POST" or row[FULL] != 200 or (method, path) not in stubbed and path != "/api/modules/*":
                continue
            path = path.replace("*", "shaders")
            body = (GUEST_BODIES.get((method, path)) or [[{}]])[0][0]
            before = sum(d.get("n", 1) for d in self.settings.data["support_log"][0].get("did", [])) + self.settings.data["support_log"][0].get("did_more", 0)
            st, out = self.h(method, path, dict(body, secret="Body-Sentinel-91"), dev, TUNNEL)
            self.assertEqual(st, 200, (path, out))
            entry = self.settings.data["support_log"][0]
            after = sum(d.get("n", 1) for d in entry.get("did", [])) + entry.get("did_more", 0)
            quiet = (method, path) in sp.NOT_A_CHANGE or (method, path) in sp.REMOTE_OWNER_OPENED      # the opened ones speak in their real handlers
            if after - before != (0 if quiet else 1):
                missing.append(path)
        self.assertEqual(missing, [])
        self.assertNotIn("Body-Sentinel-91", json.dumps(self.settings.data["support_log"]) + "\n".join(self.lines))
        self.assertTrue(any(l.endswith("used /api/osc") for l in self.lines), "the change that D84 leaves open is at least in the owner's list")
        # a GET, a refused request and a studio device leave nothing
        n = len(self.lines)
        self.h("GET", "/api/status", {}, dev, TUNNEL)
        self.h("POST", "/api/osc", {}, self.full_dev, LAN)
        self.assertEqual([l for l in self.lines[n:] if "remote support support-" in l], [])
        self.h("POST", "/api/pin/show", {}, dev, TUNNEL)            # a refusal is a line of its own kind (the security review)
        self.assertEqual([l for l in self.lines[n:] if "remote support support-" in l], ["pvj-web: remote support support-1: was refused POST /api/pin/show"])


class NdiTest(Base):
    """NDI met D84 in a merge (D85): what a support session may do with it."""

    def test_the_ndi_routes_for_a_session_of_each_role(self):
        """The discovery server's own routes and its module switch: never, whatever the role. The input's routes follow
        Streams: a session of the Owner's role as the Owner, below it as before."""
        self.assertEqual(ROWS[("GET", "/api/ndi/discovery")][VIEW:FULL + 1], (403, 403, 403))
        self.assertEqual(ROWS[("POST", "/api/ndi/discovery")][VIEW:FULL + 1], (403, 403, 403))
        self.assertEqual(ROWS[("POST", "/api/ndi")][VIEW:FULL + 1], ROWS[("POST", "/api/streams")][VIEW:FULL + 1])
        self.assertEqual(ROWS[("GET", "/api/ndi")][FULL], 200)
        for role in ("view", "live", "full"):
            dev = self.session(role)
            for method, path, body in (("GET", "/api/ndi/discovery", {}), ("POST", "/api/ndi/discovery", {"on": True}),
                                       ("POST", "/api/modules/ndi-discovery", {"enabled": True}), ("POST", "/api/modules/ndi-discovery", {"enabled": False})):
                self.assertEqual(self.remote(method, path, body, dev)[0], 403, (role, path))
        self.assertIn("was refused POST /api/modules/ndi-discovery", self.did())

    def test_a_session_switches_the_ndi_input_off_and_never_on(self):
        """Decided: switching the NDI input on makes the box listen to senders on the network and run NDI's library,
        which stays after the session; switching it off takes something away and is allowed."""
        calls = []
        self.api.registry.set_enabled = lambda mid, on: calls.append((mid, on))
        dev = self.session("full")
        st, out = self.remote("POST", "/api/modules/inputs-ndi", {"enabled": True}, dev)
        self.assertEqual(st, 403, out)
        self.assertIn("remote support", out["error"])
        self.assertEqual(calls, [])
        self.assertIn("was refused switching on a module", self.did())
        self.assertEqual([d["some"] for d in self.settings.data["support_log"][0]["major"] if "module" in d["what"]], [["inputs-ndi"]])
        self.assertEqual(self.remote("POST", "/api/modules/inputs-ndi", {"enabled": False}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/modules/streams", {"enabled": True}, dev)[0], 200, "another module is his as before")
        self.assertEqual(self.h("POST", "/api/modules/inputs-ndi", {"enabled": True}, self.full_dev)[0], 200, "at the studio the Owner switches it on")
        self.assertEqual(calls, [("inputs-ndi", False), ("streams", True), ("inputs-ndi", True)])
        with self.assertRaises(ApiError) as e:              # and the handler itself, whatever the gate
            self.api.set_module("inputs-ndi", {"enabled": True}, dev, TUNNEL)
        self.assertEqual(e.exception.status, 403)

    def test_a_file_switches_neither_ndi_module_on_for_a_session(self):
        dev = self.session("full")
        file = self.export()
        clean = {"modules": {"enabled": {"inputs-ndi": True, "ndi-discovery": True, "shaders": True}}}
        with self.settings.lock:
            self.settings.data["modules"]["enabled"].update({"inputs-ndi": False, "ndi-discovery": False, "shaders": False})
        notes = []
        kept = self.care._remote_parts(clean, notes)
        self.assertEqual(clean["modules"]["enabled"], {"inputs-ndi": False, "ndi-discovery": False, "shaders": True})
        self.assertEqual(sorted(k for k in kept if k.startswith("modules")), ["modules.inputs-ndi", "modules.ndi-discovery"])
        # one that is on here stays on, and a file may switch it off
        with self.settings.lock:
            self.settings.data["modules"]["enabled"]["inputs-ndi"] = True
        for wants in (True, False):
            clean = {"modules": {"enabled": {"inputs-ndi": wants}}}
            self.assertEqual(self.care._remote_parts(clean, []), [])
            self.assertIs(clean["modules"]["enabled"]["inputs-ndi"], wants)
        # through the whole import, and for the owner in the room it stays as it is
        with self.settings.lock:
            self.settings.data["modules"]["enabled"]["inputs-ndi"] = False
        file["settings"]["modules"]["enabled"]["inputs-ndi"] = True
        for who, client, on in ((dev, TUNNEL, False), (self.full_dev, LAN, None)):
            seen = []
            real = self.care._remote_parts
            self.care._remote_parts = lambda clean, notes: seen.append(1) or real(clean, notes)
            try:
                self.care.import_settings(json.dumps(file).encode(), "import", who, client)
            finally:
                self.care._remote_parts = real
            self.assertEqual(len(seen), 1 if who is dev else 0)
            if on is False:
                self.assertFalse(self.settings.data["modules"]["enabled"].get("inputs-ndi", False))

    def test_a_file_adds_no_ndi_address_or_discovery_server_off_the_boxes_own_networks(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.5", "prefixlen": 24}]}]
        with self.settings.lock:
            self.settings.data["ndi"] = {"addresses": ["10.9.9.9"], "discovery": ["10.9.9.8:5959"]}
        for part, bad, ok in (("addresses", ["10.9.9.9", "10.77.0.9"], ["10.9.9.9", "192.168.1.40"]),
                              ("addresses", ["172.16.3.3"], []),
                              ("discovery", ["10.9.9.8:5959", "10.77.0.9:5959"], ["10.9.9.8:5959", "192.168.1.41", "192.168.1.42:5960"]),
                              ("discovery", ["192.168.2.5"], ["10.9.9.8:5959"]),
                              ("discovery", ["192.168.1.5:5959"], ["10.9.9.8:5959"]),           # the box itself (D88)
                              ("addresses", ["192.168.1.255"], [])):                             # a broadcast address is no device
            clean = {"ndi": {"addresses": ["10.9.9.9"], "discovery": ["10.9.9.8:5959"]}}
            clean["ndi"][part] = list(bad)
            kept = self.care._remote_parts(clean, [])
            self.assertEqual(kept, ["ndi." + part], bad)
            self.assertEqual(clean["ndi"][part], self.settings.data["ndi"][part], "the box's own list stays")
            clean["ndi"][part] = list(ok)
            self.assertEqual(self.care._remote_parts(clean, []), [], ok)
            self.assertEqual(clean["ndi"][part], ok)
        clean = {"ndi": {"addresses": [], "own_discovery": True}}
        self.assertEqual(self.care._remote_parts(clean, []), ["ndi.own_discovery"], "nor point the input at a discovery server on this box that he cannot switch")
        self.assertNotIn("own_discovery", clean["ndi"])
        self.assertEqual(boxcare.REMOTE_SETS["ndi"], "/api/ndi")

    def test_what_counts_as_on_the_boxes_own_networks_for_ndi(self):
        """D88, from the review of #137: the box's own address is this box's own discovery server by another name, so a
        file could point the input at it although `own_discovery` is held back; a
        network's own and broadcast addresses are no device; a studio network that overlaps the support session's
        network does not make the tunnel's addresses local; and with no way to read the networks nothing is local."""
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.5", "prefixlen": 24}]}]
        on = self.api.ndi_on_own_net
        self.assertEqual([on(a) for a in ("192.168.1.40", "192.168.1.41:5960", "192.168.1.5", "192.168.1.5:5959", "192.168.1.0",
                                          "192.168.1.255", "10.77.0.9", "192.168.2.5", "x")],
                         [True, True, False, False, False, False, False, False, False])
        with mock.patch.object(self.api.support, "network", lambda: ipaddress.ip_network("192.168.1.128/25")):
            self.assertEqual((on("192.168.1.40"), on("192.168.1.200")), (True, False))
        self.api.osc = None
        self.assertFalse(on("192.168.1.40"))
        self.assertEqual(self.api.ndi_nets(), [])


class SecondReviewTest(Base):
    """The critical security review of #125 at 0c06cec."""

    def test_m1_the_telling_lines_are_never_capped_or_pushed_out(self):
        """A remote Owner has some sixty POST routes that each wrote a distinct line into a list of forty: 45 calls,
        then an update or an import, and the update was a count. The telling lines have a list of their own now."""
        dev = self.session("full")
        for n in range(300):
            self.api.support.used(dev, "POST", "/api/route%d" % n)
        self.api.support.note(dev, "uploaded a software update x", always=True)
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertEqual(self.remote("POST", "/api/guests", {"locked": True}, dev)[0], 200)
        major = self.kept()
        self.assertEqual((len(major), self.did()), (2, ["locked guest controls"]))
        self.assertIn("uploaded a software update", major[0])
        self.assertIn("imported a settings file", major[1])
        entry = self.settings.data["support_log"][0]
        self.assertLessEqual(len(entry["did"]), sp.DID_KEEP)
        self.assertEqual(len(entry["did"]) + entry.get("did_more", 0), 300, "the routine ones fold into counts and are still counted")
        # generous, and when even that fills it is said in a place nothing can push out
        for n in range(sp.MAJOR_KEEP + 30):
            self.api.support.note(dev, "a line of the middle kind %08d" % n)
        entry = self.settings.data["support_log"][0]
        self.assertEqual((len(entry["major"]), entry["major_more"]), (sp.MAJOR_KEEP, 31))
        self.assertEqual(entry["major"][0]["what"], "locked guest controls", "the first lines stay")
        self.assertEqual(len(self.kept()), 2, "and the list above it is untouched")
        seen = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]
        self.assertEqual((len(seen["major"]), seen["major_more"], len(seen["always"])), (sp.MAJOR_KEEP, 31, 2))

    def test_no_sequence_keeps_an_update_an_import_or_a_revoke_out_of_sight(self):
        """The re-check of 181e614: 110 theme add and remove cycles with distinct ids filled the major list, and the
        update after them was a count. Those three kinds have a list nothing else writes to, and a theme added or
        removed is one line per action with a count and a few of the ids."""
        dev = self.session("full")
        for n in range(110):
            self.assertEqual(self.remote("POST", "/api/theme/add", {"file": json.dumps(mine(id="t%d" % n))}, dev)[0], 200)
            self.assertEqual(self.remote("POST", "/api/theme/remove", {"id": "t%d" % n}, dev)[0], 200)
        self.api.support.note(dev, sp.upload_line(True, {"name": "pvj-9.9.9.tar.gz", "sha256": "ab" * 32}), always=True)
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.view_dev["id"]}, dev)[0], 200)
        entry = self.settings.data["support_log"][0]
        kept = [d["what"] for d in entry["always"]]
        self.assertEqual(len(kept), 3, kept)
        self.assertIn("uploaded a software update: pvj-9.9.9.tar.gz", kept[0])
        self.assertIn("imported a settings file, sha256 ", kept[1])
        self.assertIn("removed the guest device " + self.view_dev["id"], kept[2])
        themes = [d for d in entry["major"] if "theme" in d["what"]]
        self.assertEqual([(d["what"], d["n"], len(d["some"])) for d in themes], [("added a theme", 110, sp.SOME_KEEP), ("removed a theme", 110, sp.SOME_KEEP)])
        self.assertEqual(themes[0]["some"][0], "t0")
        self.assertNotIn("major_more", entry)
        seen = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]      # what the panel is given
        self.assertEqual([d["what"] for d in seen["always"]], kept)
        # even this list has an end: past it the first and the last stay, and the number dropped is said
        for n in range(sp.ALWAYS_KEEP + 40):
            self.api.support.note(dev, "imported a settings file, sha256 %064d" % n, always=True)
        entry = self.settings.data["support_log"][0]
        kept = [d["what"] for d in entry["always"]]
        self.assertEqual((len(kept), entry["always_dropped"]), (sp.ALWAYS_KEEP, 43))
        self.assertIn("uploaded a software update", kept[0])
        self.assertTrue(kept[-1].endswith("%064d" % (sp.ALWAYS_KEEP + 39)), "the last one is there too")
        self.assertTrue(kept[sp.ALWAYS_KEEP // 2 - 1].endswith("%064d" % (sp.ALWAYS_KEEP // 2 - 4)), "the first half is the first ones")

    def test_every_caller_of_a_major_line_folds_or_is_never_capped(self):
        """Read from the source: a call that writes a major line either passes always=True, or its text has no part
        that a request chooses (what varies goes into `detail`, which is counted and kept only for a few)."""
        import inspect
        import re as re_mod
        from pvj import api as api_mod, boxcare as boxcare_mod, server as server_mod
        calls = []
        for mod in (api_mod, boxcare_mod, server_mod):
            src = inspect.getsource(mod)
            calls += [(mod.__name__, c) for c in re_mod.findall(r"support\.(?:note|refused)\((?:[^()]|\([^()]*\))*\)", src)]
        self.assertGreaterEqual(len(calls), 10)
        for where, call in calls:
            if "always=True" in call or "major=False" in call or "major=update" in call:
                continue
            first = call.split(",", 1)[1].split("detail=")[0]
            self.assertNotIn("%", first.replace("%s %s\" % (method, path)", ""), "%s: a major line with a part a request chooses: %s" % (where, call))
        # the routes a refusal can name are those of the route table: a path nobody answers is a 404 and no line
        dev = self.session("full")
        for n in range(50):
            self.assertEqual(self.h("POST", "/api/access/nothing%d" % n, {}, dev, TUNNEL)[0], 404)
        self.assertEqual(self.did(), [])

    def test_m2_what_a_session_was_refused_is_in_its_log(self):
        dev = self.session("full")
        for method, path in (("POST", "/api/pair"), ("POST", "/api/pin/show"), ("POST", "/api/access/code"), ("GET", "/api/recovery"),
                             ("POST", "/api/https/owner-only"), ("POST", "/api/system/factory-reset"), ("POST", "/api/support/extend")):
            st = self.remote(method, path, {"pin": "0000", "name": "me", "minutes": 240}, dev)[0]
            self.assertEqual(st, 403, path)
            self.assertEqual(self.did()[-1], "was refused %s %s" % (method, path))
        n = len(self.did())
        for _ in range(50):
            self.remote("POST", "/api/pair", {"pin": "0000"}, dev)
        entry = self.settings.data["support_log"][0]
        self.assertEqual(len(entry["major"]), n, "the same refusal again is a count")
        self.assertEqual(entry["major"][0]["n"], 51)
        self.assertEqual(len([x for x in self.lines if "was refused POST /api/pair" in x]), 1, "and one line in the journal, not fifty")
        # the two rules added by decision, and an owner's device
        with mock.patch.object(self.api.osc, "apply", lambda: None):
            self.assertEqual(self.remote("POST", "/api/osc", {"allow": ["203.0.113.9/32"]}, dev)[0], 403)
        self.assertIn("was refused adding a network OSC accepts from", self.did()[-1])
        self.assertNotIn("203.0.113.9", json.dumps(self.settings.data["support_log"]))
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.full_dev["id"]}, dev)[0], 403)
        self.assertIn("was refused removing an owner's device", self.did()[-1])
        # a request through the tunnel that carries no login is refused and said too
        self.assertEqual(self.h("POST", "/api/pair", {"pin": "0000", "name": "x"}, None, TUNNEL)[0], 403)
        self.assertIn("was refused POST /api/pair", self.did()[-1])
        self.assertIn("not signed in", self.settings.data["support_log"][0]["major"][-1]["what"])
        # a studio device's refusals, and a plain 403 by role, are no lines
        n = len(self.did())
        self.h("POST", "/api/osc", {}, self.live_dev, LAN)
        self.remote("GET", "/api/status", {}, dev)
        self.assertEqual(len(self.did()), n)

    def test_m2_a_code_control_refused_is_in_the_log(self):
        from pvj import midi
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=lambda p: (_ for _ in ()).throw(OSError()),
                           lister=lambda: [], scan_interval=0.05)
        self.api.midi = hub
        self.addCleanup(hub.stop)
        self.assertEqual(self.h("POST", "/api/modules/control-midi", {"enabled": True}, self.full_dev)[0], 200)
        dev = self.session("full")
        st, out = self.remote("POST", "/api/midi/map", {"add": {"kind": "note", "number": 41, "source": "*", "action": "code_owner"}}, dev)
        self.assertEqual(st, 403, out)
        self.assertIn("was refused setting a MIDI control that shows an access code", self.did()[-1])

    def test_l1_every_opened_handler_refuses_a_login_whose_session_is_over(self):
        """Driven directly, past the gate and the token check: each of the seven asks for itself."""
        dev = self.session("full")
        self.h("POST", "/api/support/stop", device=self.full_dev)
        routes = self.api.routes()
        bodies = dict(OPENED)
        bodies[("POST", "/api/devices/revoke")] = {"id": self.view_dev["id"]}
        asked = []
        for key in sorted(sp.REMOTE_OWNER_OPENED):
            for client in (TUNNEL, LAN):
                with self.assertRaises(ApiError, msg=key) as e:
                    if key == ("POST", "/api/system/settings/import"):
                        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, client)
                    else:
                        routes[key][1](bodies[key], dev, client)
                self.assertEqual(e.exception.status, 403, key)
            asked.append(key)
        self.assertEqual(len(asked), 7)
        self.assertFalse(self.api.guests.locked())
        self.assertIn(self.view_dev["id"], [d["id"] for d in self.auth.list_devices()])

    def test_l2_an_update_and_an_import_are_named_by_what_they_were(self):
        import hashlib
        dev = self.session("full")
        raw = json.dumps(self.export()).encode()
        file = json.loads(raw)
        file["settings"]["osc"]["port"] = 9933
        raw = json.dumps(file).encode()
        out = self.care.import_settings(raw, "import", dev, TUNNEL)
        self.assertIn("osc", out["changed"])
        self.assertNotIn("pads", out["changed"], "only what the file really changed")
        line = self.kept()[-1]
        self.assertIn("sha256 " + hashlib.sha256(raw).hexdigest(), line)
        self.assertIn("changed: " + ", ".join(out["changed"]), line)
        data = b"not a real bundle" * 100
        parts = [data]
        got = self.api.update_upload("pvj-9.9.9.tar.gz", len(data), lambda n: parts.pop() if parts else b"")
        self.assertEqual(got["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(sp.upload_line(True, got), "uploaded a software update: pvj-9.9.9.tar.gz, sha256 " + got["sha256"])
        self.assertEqual(sp.upload_line(False, {"name": "a.mp4", "size": 3}), "uploaded a clip: a.mp4")

    def test_a_backup_from_before_the_session_that_no_session_can_push_out(self):
        """Three imports by a helper pushed the owner's own copies out (the last three were kept). A session's imports
        have their own copies now, the first of them is the settings as they were before the session touched them,
        and the owner's own copies are never removed by a session."""
        folder = os.path.dirname(self.settings.path)
        names = lambda part: sorted(n for n in os.listdir(folder) if part in n)
        tick = [1790000000]

        def now():                      # every copy at its own second, as the file names are by the second
            tick[0] += 2
            return tick[0]
        self.care._now = now
        for _ in range(3):
            self.clock.t += 2
            st, out = self.send(self.export())
            self.assertEqual(st, 200, out)
        own = names(".before-import-")
        self.assertEqual(len(own), 3)
        self.settings.data["osc"]["port"] = 9944                 # the state the owner had when he asked for help
        self.settings.save()
        dev = self.session("full")
        sid = self.api.support.session["id"]
        file = self.export()
        for n in range(6):
            self.clock.t += 2
            file["settings"]["osc"]["port"] = 9100 + n
            self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(names(".before-import-"), own, "the owner's own copies are all still there")
        first = names(".before-support-session-")
        self.assertEqual(len(first), 1)
        self.assertIn(sid, first[0])
        with open(os.path.join(folder, first[0])) as f:
            self.assertEqual(json.load(f)["osc"]["port"], 9944, "the settings as they were before the session's first import")
        self.assertEqual(len(names(".before-support-import-")), boxcare.KEEP_IMPORT_BACKUPS, "his later copies turn over among themselves")
        # one such copy per session, the last few sessions kept: only a new session the owner starts makes another
        for _ in range(boxcare.KEEP_SESSION_BACKUPS + 1):
            self.clock.t += 2
            dev = self.session("full")
            self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(len(names(".before-support-session-")), boxcare.KEEP_SESSION_BACKUPS)
        self.assertEqual(names(".before-import-"), own)
        # a factory reset takes them all, as it takes the others
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(names(".before-"), [])


class ImportTest(Base):
    def test_what_a_file_can_carry_for_a_helper_and_what_is_left_as_the_box_has_it(self):
        """The stick switch (recovery, D81) is the one section whose own route is refused to support: it is dropped
        from the file and the box's own stays. Every other section is one a session of the Owner's role may set by
        its own route."""
        self.assertEqual(sorted(boxcare.REMOTE_SETS), sorted(name for name, _c in boxcare.SECTIONS), "a new section needs a row in REMOTE_SETS")
        for name, path in boxcare.REMOTE_SETS.items():
            row = ROWS[("POST", path)]
            self.assertEqual(row[FULL] == 403, name in boxcare.REMOTE_KEPT, "%s is set by %s" % (name, path))
        dev = self.session("full")
        self.assertTrue(self.auth.usb_enabled())
        file = self.export()
        file["settings"]["recovery"] = {"usb": False}
        file["settings"]["osc"]["port"] = 9911
        out = self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(out["kept_for_support"], ["recovery"])
        self.assertNotIn("recovery", out["imported"])
        self.assertTrue(any("recovery" in n and "remote support" in n for n in out["notes"]))
        self.assertTrue(self.auth.usb_enabled(), "the stick way is still as the owner set it")
        self.assertEqual(self.settings.data["osc"]["port"], 9911, "the rest of the file came")
        self.assertTrue(self.kept()[-1].endswith("; left as the box has it: recovery"), self.kept()[-1])
        # the same file from the owner at the studio switches it, as before
        st, out = self.send(file)
        self.assertEqual((st, out["kept_for_support"]), (200, []))
        self.assertFalse(self.auth.usb_enabled())

    def test_a_file_cannot_name_devices_the_pin_codes_support_or_the_secure_connection(self):
        """None of these is a section of a settings file, for anybody: a file that names one is refused whole."""
        dev = self.session("full")
        devices = [dict(d) for d in self.settings.data["devices"]]
        before = json.dumps({k: v for k, v in self.settings.data.items() if k != "support_log"}, sort_keys=True)
        extra = {"devices": [{"id": "deadbeef", "name": "helper", "role": "full", "token_hash": "0" * 64}],
                 "auth": {"pin_hash": "0" * 64, "pin_salt": "0" * 32}, "support": dict(self.settings.data["support"], max_minutes=240),
                 "support_log": [], "joins": [{"role": "full", "code": "1234"}], "controller_code": {"enabled": True, "owner": True, "join": "live"},
                 "guest_controls": {"locked": True}, "https": {"owner_only": True}, osc.KEY_SECTION: {"key": "a" * 20}}
        for name, value in extra.items():
            file = self.export()
            file["settings"][name] = value
            with self.assertRaises(ApiError, msg=name) as e:
                self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
            self.assertIn(e.exception.status, (400, 422), name)
            file = self.export()
            file[name] = value                                  # nor beside the settings
            with self.assertRaises(ApiError, msg=name):
                self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(json.dumps({k: v for k, v in self.settings.data.items() if k != "support_log"}, sort_keys=True), before)
        self.assertEqual(self.settings.data["devices"], devices)
        self.assertEqual(self.did(), [], "a refused file changed nothing and is no line in the log")

    def test_an_import_keeps_the_pin_every_device_and_the_session_itself(self):
        dev = self.session("full")
        pin_hash = dict(self.settings.data["auth"])
        ids = sorted(d["id"] for d in self.settings.data["devices"])
        cfg = dict(self.settings.data["support"])
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertEqual(self.settings.data["auth"], pin_hash)
        self.assertEqual(sorted(d["id"] for d in self.settings.data["devices"]), ids)
        self.assertEqual(self.settings.data["support"], cfg)
        self.assertTrue(self.api.support.session)
        self.assertEqual(self.h("GET", "/api/status", device=self.full_dev)[0], 200, "the owner is still in")

    def test_the_export_holds_the_feedback_targets_for_him_and_a_diagnostics_file_never_does(self):
        """D82 took the targets out of a support session's export. He reads and changes them on their own route now,
        so the export is the Owner's export; with passwords it stays refused, and the diagnostics file is unchanged."""
        from tests.test_boxcare import FakeJournal
        self.settings.data["osc"].update(feedback_on=True, feedback=[{"name": "Desk", "address": "192.168.1.77", "port": 9000}])
        dev = self.session("full")
        st, out = self.remote("POST", "/api/system/settings/export", {}, dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["file"]["settings"]["osc"]["feedback"][0]["address"], "192.168.1.77")
        self.assertEqual(self.remote("POST", "/api/system/settings/export", {"passwords": True}, dev)[0], 403)
        self.assertNotIn(osc.KEY_SECTION, out["file"]["settings"])
        self.care._run = FakeJournal()
        st, out = self.remote("GET", "/api/system/diagnostics", {}, dev)
        self.assertEqual(st, 200, out)
        self.assertNotIn("192.168.1.77", json.dumps(out))


class RevokeTest(Base):
    def test_a_helper_removes_a_guest_and_an_operator_and_never_an_owner_himself_or_a_system_identity(self):
        second = self.auth._add_device("second owner", "full")[1]
        dev = self.session("full")
        other = self.h("POST", "/api/support/login", {"code": self.api.support.session["code"]}, client=TUNNEL)[1]["device"]
        for owner in (self.full_dev, second):
            st, out = self.remote("POST", "/api/devices/revoke", {"id": owner["id"]}, dev)
            self.assertEqual(st, 403, out)
            self.assertIn("owner", out["error"])
        for name in (dev["id"], other["id"], "midi", "osc", "dmx", "room", "support-9", "", "nobody00"):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": name}, dev)[0], 404, name)
        for bad in (None, 7, ["x"], {"id": self.full_dev["id"]}):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": bad}, dev)[0], 404)
        self.assertEqual(self.did(), ["was refused removing an owner's device"], "the two tries at an owner are one line with a count")
        ids = lambda: sorted(d["id"] for d in self.auth.list_devices())
        self.assertEqual(ids(), sorted(d["id"] for d in (self.full_dev, second, self.live_dev, self.view_dev)))
        for target, word in ((self.view_dev, "guest"), (self.live_dev, "operator")):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": target["id"]}, dev)[0], 200)
            self.assertIn("removed the %s device %s" % (word, target["id"]), self.kept()[-1])
        self.assertEqual(ids(), sorted(d["id"] for d in (self.full_dev, second)), "both owners are still paired")
        self.assertTrue(self.api.support.authenticate is not None and self.api.support.session["tokens"], "and both support logins")
        # at the studio the Owner removes an Owner as before
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": second["id"]}, self.full_dev)[0], 200)

    def test_a_device_whose_role_is_raised_in_the_list_meanwhile_is_judged_by_the_list(self):
        dev = self.session("full")
        with self.settings.lock:
            for d in self.settings.data["devices"]:
                if d["id"] == self.live_dev["id"]:
                    d["role"] = "full"
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.live_dev["id"]}, dev)[0], 403)


class AttackTest(Base):
    """What I tried as a helper who wants a way in after Stop, or the owner out."""

    def test_a_theme_file_is_data_and_never_more(self):
        dev = self.session("full")
        base = mine(id="sneak")
        tries = [dict(base, css="body{display:none}"), dict(base, script="alert(1)"), dict(base, name="<script>alert(1)</script>"),
                 dict(base, id="../../etc/passwd"), dict(base, id="sneak/../../x")]
        for bad in ("url(http://203.0.113.9/x)", "red;}</style><script>alert(1)</script>", "expression(alert(1))", "@import 'http://203.0.113.9'",
                    "#fff; background:url(//203.0.113.9)"):
            for part in ("tokens", "states", "areas"):
                if isinstance(base.get(part), dict) and base[part]:
                    t = json.loads(json.dumps(base))
                    first = next(iter(t[part]))
                    if isinstance(t[part][first], dict):
                        inner = next(iter(t[part][first]))
                        t[part][first][inner] = bad
                    else:
                        t[part][first] = bad
                    tries.append(t)
        for t in tries:
            st, out = self.remote("POST", "/api/theme/add", {"file": json.dumps(t)}, dev)
            self.assertIn(st, (400, 413, 422), json.dumps(t)[:120])
        self.assertEqual(self.remote("POST", "/api/theme/add", {"file": "x" * (2 * 1024 * 1024)}, dev)[0], 413)
        self.assertEqual(self.did(), [])
        # a theme that comes with the box cannot be removed, by him or anyone
        stock = next(tid for tid, t in self.api.themes.items() if t.get("source") != "addon")
        self.assertEqual(self.remote("POST", "/api/theme/remove", {"id": stock}, dev)[0], 409)

    def test_feedback_targets_cannot_be_pointed_at_the_helper_or_out_of_the_building(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.5", "prefixlen": 24}]}]
        dev = self.session("full")
        for address in (TUNNEL, "10.77.0.1", "203.0.113.9", "8.8.8.8", "192.168.1.5", "192.168.1.255", "127.0.0.1", "192.168.2.9"):
            st, out = self.remote("POST", "/api/osc/feedback", {"add": {"name": "me", "address": address, "port": 9000}}, dev)
            self.assertEqual(st, 400, (address, out))
        self.assertEqual(self.remote("POST", "/api/osc/feedback", {"add": {"name": "me", "address": "192.168.1.60", "port": 53}}, dev)[0], 400)
        self.assertEqual(self.settings.data["osc"].get("feedback", []), [])
        # by a file: the form is taken, and at every send the tunnel's address is still no device on the box's own network
        from pvj import oscfeedback
        self.assertIsNotNone(oscfeedback.not_local(TUNNEL, oscfeedback.local_nets(self.api._ip_json())))

    def test_the_key_he_saw_is_said_and_the_owner_is_told_to_make_a_new_one(self):
        dev = self.session("full")
        st, out = self.remote("POST", "/api/osc/key", {"new": True}, dev)
        self.assertEqual(st, 200, out)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        entry = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]
        self.assertEqual((entry["osc_key"], bool(entry["ended"])), (True, True))
        self.assertEqual(self.api.osc.key(), out["key"], "the key he made still works: only a new one ends what he knows")
        # the owner makes a new key at the studio: now nobody outside knows it, and the page stops saying so
        self.assertEqual(self.h("POST", "/api/osc/key", {}, self.full_dev)[0], 200)             # only looking at it changes nothing
        self.assertTrue(self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]["osc_key"])
        st, new = self.h("POST", "/api/osc/key", {"new": True}, self.full_dev)
        self.assertEqual(st, 200, new)
        self.assertNotEqual(new["key"], out["key"])
        self.assertNotIn("osc_key", self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0])
        # he is held to the same few shows as a device at the studio
        dev = self.session("full")
        got = [self.remote("POST", "/api/osc/key", {}, dev)[0] for _ in range(12)]
        self.assertIn(429, got)

    def test_he_cannot_lengthen_widen_or_restart_his_own_access(self):
        dev = self.session("live")
        for path, body in (("/api/support/extend", {"minutes": 240}), ("/api/support/start", {"confirm": "start", "role": "full"}),
                           ("/api/support/config", {"max_minutes": 240, "allowed": True}), ("/api/devices/invite", {"name": "me", "role": "full"}),
                           ("/api/pair", {"pin": "0000", "name": "me"}), ("/api/access/code", {"role": "live"}), ("/api/pin/show", {}),
                           ("/api/pin/rotate", {}), ("/api/recovery/codes", {}), ("/api/https/owner-only", {"on": True})):
            for d in (dev, self.session("full")):
                self.assertEqual(self.remote("POST", path, body, d)[0], 403, path)
        dev = self.session("full")
        self.assertEqual(self.api.support.session["role"], "full")
        left = self.api.support._seconds_left()
        self.assertLessEqual(left, 3600)
        # a restart of the box ends the session: allowed. Power-off and a reset are not.
        for path in ("/api/system/poweroff", "/api/system/factory-reset"):
            self.assertEqual(self.remote("POST", path, {"confirm": "factory-reset", "media": "keep"}, dev)[0], 403)

    def test_when_the_session_ends_his_login_is_gone_and_no_device_was_added(self):
        before = sorted(d["id"] for d in self.auth.list_devices())
        dev = self.session("full")
        token = next(iter(self.api.support.session["tokens"]))
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.remote("POST", "/api/guests", {"locked": True}, dev)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        self.assertIsNone(self.api.support.session)
        self.assertEqual(sorted(d["id"] for d in self.auth.list_devices()), before)
        self.assertEqual(self.auth.list_joins(), [])
        for path, body in (("/api/guests", {"locked": False}), ("/api/osc/key", {}), ("/api/theme/remove", {"id": "x"})):
            self.assertIn(self.h("POST", path, body, dev, TUNNEL)[0], (401, 403), path)
            self.assertIn(self.h("POST", path, body, dev, LAN)[0], (401, 403), path)
        with self.assertRaises(ApiError):
            self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertNotIn(token, json.dumps(self.settings.data))

    def test_two_more_ways_to_shut_the_owner_out_the_clock_and_the_network(self):
        """The clock set far forward: only a device that joined with a guest code and went unused is dropped by time
        (auth._idle); an Owner's and an Operator's never are. The network: a helper may propose a change and take it
        back, and it goes back by itself unless someone AT THE STUDIO confirms it."""
        owner = dict(self.settings.data["devices"][0])
        self.auth._now = lambda: 2082758400            # the year 2036, the latest a clock is taken
        self.auth._prune_idle()
        ids = [d["id"] for d in self.settings.data["devices"]]
        self.assertIn(owner["id"], ids)
        self.assertIn(self.live_dev["id"], ids)
        self.assertIsNotNone(self.auth.authenticate(self.full))
        for role in ("live", "full"):
            dev = self.session(role)
            st, out = self.remote("POST", "/api/network/confirm", {}, dev)
            self.assertEqual(st, 403, out)
        self.assertIn(("POST", "/api/network/confirm"), sp.REMOTE_DENY)
        self.assertNotIn(("POST", "/api/network/revert"), sp.REMOTE_DENY)

    def test_no_network_from_outside_is_added_to_those_osc_accepts_from(self):
        """Decided after the first build (2026-10-11): an outside network accepted for OSC would be a way in that
        outlives the session. Through the tunnel one may be removed or narrowed, and one added only if it is private
        and the box is directly connected to it; by the route and by a file."""
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.5", "prefixlen": 24}]}]
        self.settings.data["osc"]["allow"] = ["192.168.7.0/24", "172.16.0.0/16"]
        dev = self.session("full")
        with mock.patch.object(self.api.osc, "apply", lambda: None):
            for bad in (["203.0.113.9/32"], ["192.168.7.0/24", "8.8.8.0/24"], ["10.77.0.0/24"], ["10.0.0.0/8"], ["192.168.0.0/16"],
                        ["192.168.9.0/24"], ["192.168.7.0/23"], ["2001:db8::/32"], ["fe80::/64"]):
                st, out = self.remote("POST", "/api/osc", {"allow": bad}, dev)
                self.assertEqual(st, 403, (bad, out))
                self.assertIn("remote support", out["error"])
            self.assertEqual(self.settings.data["osc"]["allow"], ["192.168.7.0/24", "172.16.0.0/16"])
            for ok in (["192.168.7.0/24", "172.16.0.0/16"], ["192.168.7.0/25", "172.16.5.0/24"], ["192.168.7.0/25", "192.168.1.0/24"],
                       ["192.168.1.64/26"], []):
                st, out = self.remote("POST", "/api/osc", {"allow": ok}, dev)
                self.assertEqual(st, 200, (ok, out))
            self.assertEqual(self.remote("POST", "/api/osc", {"allow": ["192.168.7.0/24"]}, dev)[0], 403, "what he removed he cannot put back")
            self.assertEqual(self.remote("POST", "/api/osc", {"port": 9911}, dev)[0], 200)       # the rest of the page is his as before
            self.assertEqual(self.h("POST", "/api/osc", {"allow": ["203.0.113.9/32"]}, self.full_dev)[0], 200, "at the studio the Owner may, as before")
        # by a file: the outside network does not come, the box's own list stays, and the answer and the log say so
        self.settings.data["osc"]["allow"] = ["192.168.7.0/24"]
        file = self.export()
        file["settings"]["osc"]["allow"] = ["192.168.7.0/24", "203.0.113.0/24"]
        file["settings"]["osc"]["port"] = 9922
        out = self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertIn("osc.allow", out["kept_for_support"])
        self.assertEqual((self.settings.data["osc"]["allow"], self.settings.data["osc"]["port"]), (["192.168.7.0/24"], 9922))
        self.assertIn("osc.allow", self.kept()[-1])
        file["settings"]["osc"]["allow"] = []
        out = self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertNotIn("osc.allow", out["kept_for_support"])
        self.assertEqual(self.settings.data["osc"]["allow"], [], "removing by a file is allowed")

    def test_a_control_that_shows_an_access_code_is_never_set_or_changed_by_a_session(self):
        """Decided after the first build: the Operator's rule (D80) for every support session whatever its role,
        except that a session may take such a mapping away. By the route and by a file."""
        from pvj import midi
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=lambda p: (_ for _ in ()).throw(OSError()),
                           lister=lambda: [], scan_interval=0.05)
        self.api.midi = hub
        self.addCleanup(hub.stop)
        self.assertEqual(self.h("POST", "/api/modules/control-midi", {"enabled": True}, self.full_dev)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"enabled": True}, self.full_dev)[0], 200)
        code = {"kind": "note", "number": 40, "source": "*", "action": "code_owner"}
        join = dict(code, number=41, action="code_join")
        plain = {"kind": "note", "number": 50, "source": "*", "action": "blackout"}
        st, out = self.h("POST", "/api/midi/map", {"add": code}, self.full_dev)
        self.assertEqual(st, 200, out)
        mine = lambda: [e for e in self.settings.data["control"]["midi"]["map"] if e["action"].startswith("code_")]
        kept = [dict(e) for e in mine()]
        self.assertEqual(len(kept), 1)
        dev = self.session("full")
        for body in ({"add": join}, {"add": dict(code, number=42)}, {"add": dict(plain, number=40)}):
            st, out = self.remote("POST", "/api/midi/map", body, dev)
            self.assertEqual(st, 403, (body, out))
        self.assertEqual(mine(), kept)
        st, out = self.remote("POST", "/api/midi/map", {"add": plain}, dev)
        self.assertEqual(st, 200, out)                                  # a mapping of what a session may do itself: as before
        self.assertEqual(mine(), kept)
        for call in (self.api.midi_map,):                               # and with a role below the Owner's, the handler itself
            with self.assertRaises(ApiError) as e:
                call({"add": join}, dict(dev, role="live"), TUNNEL)
            self.assertEqual(e.exception.status, 403)
        # by a file: a new code control is left out, a changed one too; the one the box has stays
        file = self.export()
        m = file["settings"]["control"]["midi"]["map"]
        m.append(dict(join, id="0a0a0a0a"))
        for e in m:
            if e["action"] == "code_owner":
                e["number"] = 99
        out = self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertIn("control.midi.map (access code controls)", out["kept_for_support"])
        self.assertEqual(mine(), [], "the changed one did not come, and the file no longer holds the box's own: removed, which is allowed")
        self.assertTrue(any(e["action"] == "blackout" for e in self.settings.data["control"]["midi"]["map"]))
        # he may take one away
        self.assertEqual(self.h("POST", "/api/midi/map", {"add": code}, self.full_dev)[0], 200)
        st, out = self.remote("POST", "/api/midi/map", {"remove": mine()[0]["id"]}, dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(mine(), [])
        # and the switch that lets a controller show a code at all stays out of his reach
        self.assertEqual(self.remote("POST", "/api/access/controller", {"enabled": True, "owner": True}, dev)[0], 403)


if __name__ == "__main__":
    import unittest
    unittest.main()
