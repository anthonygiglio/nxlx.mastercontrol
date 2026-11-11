# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The server's part of the hand-over panel (D132, slice 5): the page's form-action names only the trusted nodes' https
origins, of one shape, read from what discovery already knows (never a scan); the Nodes list says which rows open signed
in and whether this device may carry."""
import functools
import types
import unittest

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import api as api_mod, fleet, server

ME, B, C, D, E = "a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32


class FakeHandover:
    def __init__(self, approved, carry):
        self.approved, self.carry = approved, carry

    def approved_nodes(self):
        return [(i, "n", "k" * 64) for i in self.approved]

    def their_carry(self, node_id):
        return self.carry.get(node_id)


class FakeTickets:
    def __init__(self, why=None, carried=False):
        self.why, self.is_carried = why, carried

    def may_carry(self, device):
        return self.why

    def carried(self, device):
        return self.is_carried


class FakeFleet:
    def __init__(self, rows):
        self.rows = rows

    def known(self):
        return list(self.rows)

    def view(self):
        return {"self": {"id": ME, "name": "node-01"}, "nodes": list(self.rows), "scanning": False, "error": None, "discovery": {"on": True}}


def row(node_id, address, port=443, https=True, name="node-02"):
    return {"id": node_id, "name": name, "address": address, "port": port, "tier": "standard", "version": "1", "https": https}


def fake_api(rows, approved, carry=None, why=None, carried=False, secure=True):
    o = types.SimpleNamespace(fleet=FakeFleet(rows), handover=FakeHandover(approved, carry or {}), tickets=FakeTickets(why, carried),
                              request=types.SimpleNamespace(secure=secure), log=lambda *_: None)
    o._handover_view = functools.partial(api_mod.Api._handover_view, o)
    return o


class PageCsp(unittest.TestCase):
    def test_forms_of_one_shape_only(self):
        csp = server.page_csp(None, ["https://192.168.0.170", "https://node-02.local:8443"])
        self.assertIn("form-action 'self' https://192.168.0.170 https://node-02.local:8443;", csp)
        self.assertIn("connect-src 'self';", csp)
        for bad in ["http://192.168.0.170", "https://*.local", "https://a; script-src *", "https://a/handover", "*", "", None, 5]:
            self.assertEqual(server.page_csp(None, [bad]), server.CSP, bad)

    def test_at_most_32(self):
        csp = server.page_csp(None, ["https://10.0.0.%d" % i for i in range(1, 60)])
        self.assertEqual(csp.split("form-action ", 1)[1].split(";")[0].count("https://"), 32)

    def test_origin_and_forms_together(self):
        csp = server.page_csp("https://192.168.0.169", ["https://192.168.0.170"])
        self.assertIn("connect-src 'self' https://192.168.0.169;", csp)
        self.assertIn("form-action 'self' https://192.168.0.170;", csp)


class HandoverForms(unittest.TestCase):
    def test_trusted_nodes_only(self):
        a = fake_api([row(B, "192.168.0.170"), row(B, "10.0.0.7", 8443), row(C, "192.168.0.171", name="venue"),
                      row(D, "192.168.0.172", name="plain", https=False), row(E, "8.8.8.8", name="far")],
                     approved=[B, C, D, E], carry={C: "none"})
        self.assertEqual(sorted(api_mod.Api.handover_forms(a)), sorted(["https://192.168.0.170", "https://node-02.local", "https://10.0.0.7:8443",
                                                                        "https://node-02.local:8443"]))      # (a public address: not even its .local name)

    def test_none_without_trust(self):
        self.assertEqual(api_mod.Api.handover_forms(fake_api([row(B, "192.168.0.170")], approved=[])), [])

    def test_bad_ports_and_names_dropped(self):
        a = fake_api([row(B, "192.168.0.170", port="443"), row(B, "192.168.0.171", port=0), row(B, "192.168.0.172", port=True),
                      row(B, "192.168.0.173", name="bad name; x")], approved=[B])
        self.assertEqual(api_mod.Api.handover_forms(a), ["https://192.168.0.173"])

    def test_not_built(self):
        a = fake_api([row(B, "192.168.0.170")], approved=[B])
        a.tickets = None
        self.assertEqual(api_mod.Api.handover_forms(a), [])

    def test_every_origin_passes_the_csp_shape(self):
        ids = ["%032x" % (i + 1) for i in range(40)]
        a = fake_api([row(i, "192.168.0.%d" % (n + 1), name="node-%d" % n) for n, i in enumerate(ids)], approved=ids)
        forms = api_mod.Api.handover_forms(a)
        self.assertEqual(len(forms), 32)
        self.assertTrue(all(server.CSP_ORIGIN.fullmatch(f) for f in forms))

    def test_a_flood_for_one_id_takes_four_places(self):
        # whoever announces a trusted node's id at many addresses gets at most 4 places for it, and the other node keeps its own
        flood = [row(B, "192.168.1.%d" % i, name="spoof") for i in range(1, 60)]
        a = fake_api(flood + [row(C, "192.168.0.171", name="venue")], approved=[B, C])
        forms = api_mod.Api.handover_forms(a)
        self.assertIn("https://192.168.0.171", forms)
        self.assertLessEqual(sum(1 for f in forms if f.startswith("https://192.168.1.")), 4)

    def test_the_checked_address_comes_first(self):
        flood = [row(B, "192.168.1.%d" % i, name="spoof") for i in range(1, 60)]
        a = fake_api(flood, approved=[B])
        a.peers = types.SimpleNamespace(verified={B: "https://192.168.0.170", D: "https://192.168.0.99"})
        forms = api_mod.Api.handover_forms(a)
        self.assertEqual(forms[0], "https://192.168.0.170", "the address checked by certificate is first")
        self.assertNotIn("https://192.168.0.99", forms, "a node that is not trusted now is left out, checked or not")


class NodesAnswer(unittest.TestCase):
    def test_rows_marked(self):
        a = fake_api([row(B, "192.168.0.170"), row(C, "192.168.0.171", name="venue"), row(D, "192.168.0.172", name="other"),
                      row(E, "192.168.0.173", name="plain", https=False)], approved=[B, C, E], carry={C: "none"})
        out = api_mod.Api.get_nodes(a, None, {"id": "x"}, None)
        self.assertEqual({r["id"]: r["carry"] for r in out["nodes"]}, {B: True, C: False, D: False, E: False})
        self.assertEqual(out["handover"], {"may": True, "why": None, "carried": False})

    def test_device_that_may_not(self):
        a = fake_api([row(B, "192.168.0.170")], approved=[B], why="only an Operator or an Owner opens another node without its PIN")
        out = api_mod.Api.get_nodes(a, None, {"id": "x"}, None)
        self.assertFalse(out["handover"]["may"])
        self.assertIn("Operator", out["handover"]["why"])
        self.assertEqual([r["carry"] for r in out["nodes"]], [False])

    def test_plain_http(self):
        out = api_mod.Api.get_nodes(fake_api([row(B, "192.168.0.170")], approved=[B], secure=False), None, {"id": "x"}, None)
        self.assertFalse(out["handover"]["may"])
        self.assertIn("plain http", out["handover"]["why"])

    def test_carried_device(self):
        out = api_mod.Api.get_nodes(fake_api([], approved=[], why="a device that joined ... from another node does not carry", carried=True), None, {"id": "x"}, None)
        self.assertTrue(out["handover"]["carried"])
        self.assertFalse(out["handover"]["may"])


class KnownNeverScans(unittest.TestCase):
    def test_known_reads_without_asking(self):
        calls = []
        b = fleet.Browser(lambda: calls.append(1) or [], ME)
        self.assertEqual(b.known(), [])
        self.assertEqual(calls, [])

    def test_fleet_known_off(self):
        f = types.SimpleNamespace(_on=lambda: False, browser=types.SimpleNamespace(known=lambda: [1]))
        self.assertEqual(fleet.Fleet.known(f), [])
        f._on = lambda: True
        self.assertEqual(fleet.Fleet.known(f), [1])




from tests.test_boxcert import openssls  # noqa: E402
from tests.test_https import HttpsBase  # noqa: E402


@unittest.skipUnless(openssls(), "no openssl on this machine")
class PolicyOnlyForPairedStaff(HttpsBase):
    """The page over TLS names the trusted nodes in form-action only for a paired Operator or Owner: the PIN screen and a
    Guest do not learn which nodes this one trusts (review of PR 276)."""

    def test_who_sees_the_trusted_nodes(self):
        owner, _ = self.pair("owner laptop")
        self.full = owner                      # (request_and_sign asks as the Owner)
        self.install(self.request_and_sign())
        self.api.handover_forms = lambda: ["https://192.168.0.170"]
        guest = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=owner)[1]["token"]
        operator = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=owner)[1]["token"]

        def policy(token):
            st, _, r = self.scall("GET", "/", token=token)
            self.assertEqual(st, 200)
            return r.getheader("Content-Security-Policy")
        named = "form-action \x27self\x27 https://192.168.0.170;"
        self.assertNotIn("192.168.0.170", policy(None), "the PIN screen")
        self.assertNotIn("192.168.0.170", policy("0" * 32), "a token that is no device")
        self.assertNotIn("192.168.0.170", policy(guest), "a Guest")
        self.assertIn(named, policy(operator))
        self.assertIn(named, policy(owner))


if __name__ == "__main__":
    unittest.main()
