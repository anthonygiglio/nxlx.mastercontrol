# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The routes of slice 2 (D132) on a real TLS listener: GET /api/fleet/card, the Owner's routes for the list, the
acceptances and the switch "Accept devices from other nodes"; who may use them and where; and that the list, the
acceptances and the deny file appear in no GET answer, no diagnostics file, no settings export or import and no log
line (a walk like D79's). Needs openssl; skipped without it."""
import http.client
import json
import os
import ssl
import subprocess
import threading
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import events as events_mod, fleetlist as FL, handover as hv, peer as peer_mod, policy, server, ticket as ticket_mod
from tests.peerfix import PeerNode, list_data, sign_list
from tests.test_boxcert import openssls
from tests.test_https import HttpsBase, PASS, boxcert

ME = "a" * 32
PEER = {"id": "b" * 32, "name": "leyline", "key": "2" * 64, "carry": "live"}
THIRD = {"id": "c" * 32, "name": "third", "key": "3" * 64}
B_ID = "b" * 32
DENIED = "4" * 64

def fake_screenshot(path, size, with_text):
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0 a picture \xff\xd9")


def raw(path):
    with open(path, "rb") as f:
        return f.read()


ROUTES = [("GET", "/api/fleet"), ("POST", "/api/fleet/accept"), ("POST", "/api/fleet/list"), ("POST", "/api/fleet/accepted"),
          ("POST", "/api/fleet/accepted/remove"), ("POST", "/api/fleet/accepted/look"), ("POST", "/api/fleet/sync")]


@unittest.skipUnless(openssls(), "no openssl on this machine")
class FleetApiBase(HttpsBase):
    def setUp(self):
        super().setUp()
        self.full, _ = self.pair("owner laptop")
        self.view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.full)[1]["token"]
        self.operator = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        self.api.events = events_mod.EventLog(os.path.join(self.tmp, "events.json"))
        self.hv = hv.Handover(self.tmp, self.box, self.settings, lambda: {"id": ME, "name": "testbox"}, event=self.api.event, log=self.lines.append,
                              openssl=self.exe)
        self.api.handover = self.hv
        self.directory = {}
        self.peers = peer_mod.Peers(self.hv, self.box, lambda i: list(self.directory.get(i, [])), log=self.lines.append, spawn=lambda fn: fn())
        self.api.peers = self.peers
        self.tickets = ticket_mod.Tickets(self.hv, self.peers, self.auth, self.box, event=self.api.event, log=self.lines.append)
        self.api.tickets = self.tickets
        self.peer_srv = server.PeerServer(("127.0.0.1", 0), server.make_peer_handler(self.api), self.box)     # the real peer listener (D132, 4a)
        self.peer_port = self.peer_srv.server_address[1]
        threading.Thread(target=self.peer_srv.serve_forever, daemon=True).start()
        self.addCleanup(self.peer_srv.server_close)
        self.addCleanup(self.peer_srv.shutdown)

    def pcall(self, node, path, body, method="POST", trust_root=None):
        """A call to the peer port with the certificate of `node` (a PeerNode), or with none. (status, payload)."""
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(os.path.join(self.ca, "root.pem"))
        if node is not None:
            ctx.load_cert_chain(node.cert_path, node.key_path)
        c = http.client.HTTPSConnection("127.0.0.1", self.peer_port, timeout=10, context=ctx)
        try:
            c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"} if body is not None else {})
            r = c.getresponse()
            text = r.read()
        finally:
            c.close()
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text

    def workdir(self):
        self._n = getattr(self, "_n", 0) + 1
        path = os.path.join(self.tmp, "w%d" % self._n)
        os.mkdir(path)
        return path

    def peer_b(self, **kw):
        """A second node (node-02) with a certificate from this test's root, listening on loopback, and known to this node."""
        if getattr(self, "_b", None) is None or kw:
            self._b = PeerNode(self, "node-02", B_ID, self.ca, raw(os.path.join(self.ca, "root.pem")).decode(), self.exe, passphrase=PASS, **kw)
            self.directory[B_ID] = [self._b.where]
        return self._b

    def accept_b(self, **kw):
        b = self.peer_b()
        body = dict({"id": B_ID, "carry": "live", "typed": hv.fingerprint(b.key)}, **kw)
        return self.scall("POST", "/api/fleet/accepted", body, token=self.full)[:2]

    def said(self):
        """The event log as (kind, text, who)."""
        return [(e["kind"], e["text"], e.get("who")) for e in self.api.events.events()]

    def with_certificate(self):
        self.assertEqual(self.install(self.request_and_sign())[0], 200)
        self.hv.load()
        return self.hv.own_key()

    def sign_list(self, serial=1, nodes=None, root=None, **more):
        key = self.hv.own_key()
        data = {"v": 1, "fleet": "0123456789abcdef", "name": "Rig", "serial": serial, "issued": "2026-11-09",
                "nodes": nodes if nodes is not None else [{"id": ME, "name": "testbox", "key": key, "carry": "full"}, PEER]}
        data.update(more)
        body = FL.canonical(data)
        folder = root or self.ca
        msg, sig = os.path.join(self.tmp, "m"), os.path.join(self.tmp, "s")
        with open(msg, "wb") as f:
            f.write(FL.signed_bytes(data["fleet"], serial, body))
        r = subprocess.run([self.exe, "dgst", "-sha256", "-sign", os.path.join(folder, "root.key"), "-passin", "stdin", "-out", sig, msg],
                           input=PASS + "\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        import base64
        with open(sig, "rb") as f:
            return body.decode(), base64.encodebytes(f.read()).decode()

    def post_list(self, text, sig, tls=True, token=None):
        return (self.scall if tls else self.call)("POST", "/api/fleet/list", {"list": text, "sig": sig}, token=token or self.full)[:2]


class Card(FleetApiBase):
    def test_the_card_over_tls_says_the_id_the_name_and_the_hash_of_the_public_key_and_nothing_else(self):
        key = self.with_certificate()
        st, card, r = self.scall("GET", "/api/fleet/card")                      # no token: public
        self.assertEqual(st, 200, card)
        self.assertEqual(card, {"v": 1, "id": ME, "name": "testbox", "key": key})
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        # the key is the hash of the public key as openssl gives it
        pem = self.box.cert_pem()
        pub = subprocess.run([self.exe, "x509", "-noout", "-pubkey"], input=pem.encode(), capture_output=True, check=True).stdout
        der = subprocess.run([self.exe, "pkey", "-pubin", "-outform", "DER"], input=pub, capture_output=True, check=True).stdout
        import hashlib
        self.assertEqual(card["key"], hashlib.sha256(der).hexdigest())
        # it holds nothing that is secret
        text = json.dumps(card)
        for word in ("PRIVATE", "BEGIN", "token", "pin"):
            self.assertNotIn(word, text)
        self.assertNotIn(raw(self.box.path("key.pem")).decode().splitlines()[1], text)
        # and a card builder agrees with the one the tool reads
        self.assertEqual(FL.parse_card(json.dumps(card).encode())["key"], key)

    def test_the_card_exists_over_tls_only_and_only_to_a_get(self):
        self.with_certificate()
        self.assertEqual(self.call("GET", "/api/fleet/card")[0], 404)               # plain http
        self.assertEqual(self.scall("POST", "/api/fleet/card", {})[0], 404)
        self.assertEqual(self.scall("GET", "/api/fleet/card", token="nonsense")[0], 200)      # asks nobody who they are

    def test_no_certificate_or_no_handover_no_card(self):
        self.assertEqual(self.call("GET", "/api/fleet/card")[0], 404)
        self.with_certificate()
        self.api.handover = None
        self.assertEqual(self.scall("GET", "/api/fleet/card")[0], 404)

    def test_the_card_is_the_one_route_outside_the_gate_besides_the_probe(self):
        self.assertEqual(sorted(policy.NO_GATE), [("GET", "/api/fleet/card"), ("GET", "/api/https/probe")])      # the peer calls left the panel's port (4a)
        for route in ROUTES:
            self.assertNotIn(route, policy.NO_GATE)


class Routes(FleetApiBase):
    def test_every_route_is_the_owners_and_over_the_secure_connection_only(self):
        self.with_certificate()
        for method, path in ROUTES:
            body = {} if method == "POST" else None
            self.assertEqual(self.call(method, path, body)[0], 401, path)
            for tls in (False, True):
                ask = self.scall if tls else self.call
                self.assertEqual(ask(method, path, body, token=self.view)[0], 403, (path, tls))
                self.assertEqual(ask(method, path, body, token=self.operator)[0], 403, (path, tls))
            st, out = self.call(method, path, body, token=self.full)[:2]               # the Owner, but over plain http
            self.assertEqual(st, 403, path)
            self.assertIn("secure connection", out["error"], path)
            st, out = self.scall(method, path, body, token=self.full)[:2]               # the Owner over https: past the gate
            self.assertNotIn(st, (401, 403, 404, 500), (path, out))

    def test_no_handover_on_this_node_is_a_plain_not_found(self):
        self.with_certificate()
        self.api.handover = None
        self.assertEqual(self.scall("GET", "/api/fleet", token=self.full)[0], 404)

    def test_the_owners_status_names_things_and_shows_no_key_of_another_node(self):
        self.with_certificate()
        self.assertEqual(self.post_list(*self.sign_list(2))[0], 200)
        st, out = self.scall("GET", "/api/fleet", token=self.full)[:2]
        self.assertEqual(st, 200, out)
        self.assertFalse(out["accept"])
        self.assertEqual(out["self"]["id"], ME)
        self.assertEqual(out["self"]["fingerprint"], hv.fingerprint(self.hv.own_key()))
        self.assertEqual((out["list"]["serial"], out["list"]["name"]), (2, "Rig"))
        self.assertEqual(sorted(n["name"] for n in out["list"]["nodes"]), ["leyline", "testbox"])
        self.assertEqual(sorted(out["list"]["nodes"][0]), ["carry", "id", "name"])
        self.assertNotIn(PEER["key"], json.dumps(out))


class Lists(FleetApiBase):
    def test_a_signed_list_is_taken_over_https_and_kept_beside_tls(self):
        self.with_certificate()
        st, out = self.post_list(*self.sign_list(1))
        self.assertEqual(st, 200, out)
        self.assertEqual(out["list"]["serial"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "fleet.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "fleet.sig")))
        self.assertNotIn("fleet.json", json.dumps(self.settings.data))
        self.assertIn(("handover", "The fleet list (serial 1, 2 nodes) was loaded.", "owner laptop"), self.said())
        self.assertEqual(self.hv.approved(PEER["id"], PEER["key"])["carry"], "full")

    def test_a_bad_list_is_refused_with_its_reason_and_changes_nothing(self):
        self.with_certificate()
        self.assertEqual(self.post_list(*self.sign_list(5))[0], 200)
        kept = raw(os.path.join(self.tmp, "fleet.json"))
        other = os.path.join(self.tmp, "other-ca")
        boxcert.main(["--openssl", self.exe, "--dir", other, "make-root", "--allow-address", "127.0.0.0/255.0.0.0"], ask=lambda p: PASS, out=lambda l: None)
        for (text, sig), word in ((self.sign_list(4), "older"), (self.sign_list(9, root=other), "not signed by this node's root"),
                                  (self.sign_list(9, nodes=[PEER]), "does not name this node")):
            st, out = self.post_list(text, sig)
            self.assertEqual(st, 400, out)
            self.assertIn(word, out["error"])
        for body in ({}, {"list": "x"}, {"list": 5, "sig": "x"}, {"list": "x", "sig": "x", "more": 1}):
            self.assertEqual(self.scall("POST", "/api/fleet/list", body, token=self.full)[0], 400, body)
        self.assertEqual(raw(os.path.join(self.tmp, "fleet.json")), kept)
        self.assertEqual(self.hv.list.serial, 5)

    def test_below_an_owner_and_over_plain_http_nothing_is_taken(self):
        self.with_certificate()
        text, sig = self.sign_list(1)
        self.assertEqual(self.post_list(text, sig, token=self.operator)[0], 403)
        self.assertEqual(self.post_list(text, sig, tls=False)[0], 403)
        self.assertEqual(self.post_list(text, sig, token=self.view)[0], 403)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "fleet.json")))
        self.assertIsNone(self.hv.list)


class Odd(FleetApiBase):
    def test_a_list_with_a_lone_surrogate_is_refused_in_words_not_with_a_500(self):
        self.with_certificate()
        for text in ("\ud800", "{\"v\": 1}\udfff"):
            st, out = self.scall("POST", "/api/fleet/list", {"list": text, "sig": "AAAAAAAAAAAA"}, token=self.full)[:2]
            self.assertEqual(st, 400, out)
            self.assertIn("error", out)

    def test_the_card_is_not_given_while_discovery_is_off(self):
        self.with_certificate()
        self.assertEqual(self.scall("GET", "/api/fleet/card")[0], 200)
        self.assertEqual(self.call("POST", "/api/nodes/discovery", {"on": False}, token=self.full)[0], 200)
        self.assertEqual(self.scall("GET", "/api/fleet/card")[0], 404)
        self.assertEqual(self.call("POST", "/api/nodes/discovery", {"on": True}, token=self.full)[0], 200)
        self.assertEqual(self.scall("GET", "/api/fleet/card")[0], 200)


class Push(FleetApiBase):
    """`boxcert.py fleet push`: the signed list sent to a node over https, with only the owner's root trusted."""

    def tool(self, *argv, pin=None, folder=None):
        import contextlib
        import io
        err = io.StringIO()
        asked = []
        self.out = getattr(self, "out", [])

        def ask(prompt):
            asked.append(prompt)
            return (self.pin if pin is None else pin) if "PIN" in prompt else PASS
        with contextlib.redirect_stderr(err):
            code = boxcert.main(["--openssl", self.exe, "--dir", folder or self.ca] + list(argv), ask=ask, out=self.out.append)
        self.errors, self.asked = err.getvalue(), asked
        return code

    def make_list(self, serial_args=()):
        card = os.path.join(self.tmp, "card.json")
        with open(card, "w") as f:
            json.dump(self.hv.card(), f)
        self.out = []
        if not os.path.exists(os.path.join(self.ca, "fleet-draft.json")):
            self.assertEqual(self.tool("fleet", "new", "--name", "Rig"), 0, self.errors)
            self.assertEqual(self.tool("fleet", "add", "--card", card, "--carry", "full"), 0, self.errors)
        self.assertEqual(self.tool("fleet", "sign", *serial_args), 0, self.errors)

    def test_the_list_is_pushed_with_the_pin_asked_once_and_the_device_it_made_is_gone(self):
        self.with_certificate()
        self.make_list()
        devices = len(self.settings.data["devices"])
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port)), 0, self.errors)
        self.assertEqual(self.hv.list.serial, 1)
        self.assertIn("took the list", "\n".join(self.out))
        self.assertEqual(len([a for a in self.asked if "PIN" in a]), 1)
        self.assertEqual(len(self.settings.data["devices"]), devices, "boxcert's own device is logged out again")

    def test_a_wrong_pin_or_a_pin_that_pairs_less_than_an_owner_sends_nothing(self):
        self.with_certificate()
        self.make_list()
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port), pin="0000" if self.pin != "0000" else "1111"), 1)
        self.assertIn("did not pair", self.errors)
        self.assertIsNone(self.hv.list)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "fleet.json")))

    def test_a_token_file_works_instead_of_the_pin_and_a_lesser_token_is_refused(self):
        self.with_certificate()
        self.make_list()
        port = str(self.tls_port)
        token_file = os.path.join(self.tmp, "token.txt")
        with open(token_file, "w") as f:
            f.write(self.full + "\n")
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", port, "--token-file", token_file), 0, self.errors)
        self.assertEqual([a for a in self.asked if "PIN" in a], [])
        self.assertEqual(self.hv.list.serial, 1)
        self.make_list()                                                  # serial 2
        with open(token_file, "w") as f:
            f.write(self.operator)
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", port, "--token-file", token_file), 1)
        self.assertIn("403", self.errors)
        self.assertEqual(self.hv.list.serial, 1)
        with open(token_file, "w") as f:
            f.write("two words")
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", port, "--token-file", token_file), 1)

    def test_only_the_owners_root_is_trusted_and_an_unsigned_folder_sends_nothing(self):
        self.with_certificate()
        other = os.path.join(self.tmp, "other-ca")
        boxcert.main(["--openssl", self.exe, "--dir", other, "make-root", "--allow-address", "127.0.0.0/255.0.0.0"], ask=lambda p: PASS, out=lambda l: None)
        card = os.path.join(self.tmp, "c2.json")
        with open(card, "w") as f:
            json.dump(self.hv.card(), f)
        self.out = []
        self.assertEqual(self.tool("fleet", "new", "--name", "Other", folder=other), 0)
        self.assertEqual(self.tool("fleet", "add", "--card", card, "--carry", "full", folder=other), 0)
        self.assertEqual(self.tool("fleet", "sign", folder=other), 0)
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port), folder=other), 1)
        self.assertIn("did not sign", self.errors)
        self.assertEqual([a for a in self.asked if "PIN" in a], [])          # it never got as far as asking for the PIN
        empty = os.path.join(self.tmp, "empty-ca")
        boxcert.main(["--openssl", self.exe, "--dir", empty, "make-root"], ask=lambda p: PASS, out=lambda l: None)
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port), folder=empty), 1)
        self.assertIn("nothing is signed", self.errors)
        self.assertIsNone(self.hv.list)

    def test_if_the_logout_fails_the_owner_is_told_which_device_to_remove(self):
        from pvj.api import ApiError
        self.with_certificate()
        self.make_list()

        def broken(body, device, client):
            raise ApiError(500, "no")
        self.api.logout = broken
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port)), 0, self.errors)
        said = "\n".join(self.out)
        self.assertIn('Remove "boxcert push" under People', said)
        self.assertEqual(said.count('under People'), 1)
        # the same for a code that pairs less than an Owner: it is refused and the lesser device is logged out, or the owner is told
        with self.settings.lock:
            self.settings.data["controller_code"] = {"enabled": True, "owner": False}
        self.auth.create_controller_code("join")
        _kind, digits, _ = self.auth.controller_digits()
        self.out = []
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port), pin=digits), 1)
        self.assertIn("not an Owner", self.errors)
        self.assertIn('Remove "boxcert push" under People', "\n".join(self.out))

    def test_when_the_logout_works_nothing_more_is_said(self):
        self.with_certificate()
        self.make_list()
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", str(self.tls_port)), 0, self.errors)
        self.assertNotIn("under People", "\n".join(self.out))

    def test_a_node_that_is_not_there_is_an_error(self):
        self.with_certificate()
        self.make_list()
        self.assertEqual(self.tool("fleet", "push", "127.0.0.1", "--port", "9"), 1)
        self.assertEqual(self.tool("fleet", "push", "not a host!"), 1)

    def test_fleet_add_by_name_still_works_with_discovery_on_and_says_what_to_do_with_it_off(self):
        self.with_certificate()
        self.assertEqual(self.tool("fleet", "new", "--name", "Rig"), 0)
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(self.tls_port), "--carry", "live"), 0, self.errors)
        self.call("POST", "/api/nodes/discovery", {"on": False}, token=self.full)
        self.assertEqual(self.tool("fleet", "remove", ME), 0)
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(self.tls_port), "--carry", "live"), 1)
        self.assertIn("--cert", self.errors)
        self.assertIn("discovery", self.errors)


class Switch(FleetApiBase):
    def setUp(self):
        super().setUp()
        self.with_certificate()

    def put(self, on, token=None, tls=True):
        return (self.scall if tls else self.call)("POST", "/api/fleet/accept", {"on": on}, token=token or self.full)[:2]

    def test_the_switch_is_off_by_default_and_only_an_owner_changes_it(self):
        self.assertFalse(self.hv.accepting())
        self.assertEqual(self.put(True, token=self.operator)[0], 403)
        self.assertEqual(self.put(True, token=self.view)[0], 403)
        self.assertEqual(self.put(True, tls=False)[0], 403)
        self.assertFalse(self.hv.accepting())
        st, out = self.put(True)
        self.assertEqual(st, 200, out)
        self.assertTrue(out["accept"])
        self.assertEqual(self.settings.data["fleet"]["accept"], True)
        self.assertIn(("handover", "Accepting devices from other nodes was switched on.", "owner laptop"), self.said())
        for bad in ({}, {"on": "yes"}, {"on": 1}, {"on": True, "x": 1}, {"off": True}):
            self.assertEqual(self.scall("POST", "/api/fleet/accept", bad, token=self.full)[0], 400, bad)
        self.assertEqual(self.put(False)[1]["accept"], False)

    def test_the_discovery_switch_and_this_one_do_not_undo_each_other(self):
        self.put(True)
        self.assertEqual(self.call("POST", "/api/nodes/discovery", {"on": False}, token=self.full)[0], 200)
        self.assertTrue(self.hv.accepting())
        self.assertIs(self.settings.data["fleet"]["discovery"], False)
        self.put(False)
        self.assertIs(self.settings.data["fleet"]["discovery"], False)

    def test_the_switch_is_not_exported_and_a_file_cannot_turn_it_on(self):
        self.put(False)
        st, out, _ = self.call("POST", "/api/system/settings/export", {"passwords": True}, token=self.full)
        self.assertEqual(st, 200, out)
        self.assertNotIn("fleet", out["file"]["settings"])
        file = out["file"]
        file["settings"]["fleet"] = {"accept": True, "discovery": False}
        st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(file).encode(), token=self.full)
        self.assertFalse(self.hv.accepting(), body)
        self.assertNotEqual(self.settings.data.get("fleet"), {"accept": True, "discovery": False})

    def test_the_diagnostics_file_shows_the_switch_and_no_list(self):
        self.put(True)
        st, body, _ = self.call("GET", "/api/system/diagnostics", token=self.full)
        self.assertEqual(st, 200)
        self.assertIn('"accept": true', json.dumps(body))


class Accepted(FleetApiBase):
    def test_an_acceptance_takes_its_key_from_the_nodes_card_and_needs_the_fingerprint_typed_back(self):
        self.with_certificate()
        b = self.peer_b()
        st, out = self.accept_b(typed="0000")
        self.assertEqual(st, 400)
        self.assertIn("fingerprint typed does not match", out["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "pairings.json")))
        st, out = self.accept_b()
        self.assertEqual(st, 200, out)
        self.assertEqual(out["accepted"], [{"id": B_ID, "name": "node-02", "carry": "live"}])
        self.assertNotIn(b.key, json.dumps(out))
        self.assertEqual(self.hv.approved(B_ID, b.key)["via"], "pairing")
        self.assertIn(("GET", "/api/fleet/card"), b.calls)

    def test_the_api_takes_no_key_and_no_name_from_the_caller(self):
        self.with_certificate()
        b = self.peer_b()
        for extra in ({"key": b.key}, {"name": "node-02"}, {"key": "9" * 64, "name": "x"}):
            body = dict({"id": B_ID, "carry": "live", "typed": hv.fingerprint(b.key)}, **extra)
            self.assertEqual(self.scall("POST", "/api/fleet/accepted", body, token=self.full)[0], 400, extra)
        for body in ({}, {"id": B_ID}, {"id": "short", "carry": "live", "typed": "x"}):
            self.assertEqual(self.scall("POST", "/api/fleet/accepted", body, token=self.full)[0], 400)
        self.assertEqual(self.hv.status()["accepted"], [])

    def test_a_card_that_lies_confirms_nothing_even_when_the_lie_is_typed_back(self):
        self.with_certificate()
        b = self.peer_b()
        b.card_override = {"v": 1, "id": B_ID, "name": "node-02", "key": "9" * 64}
        st, out = self.accept_b(typed=hv.fingerprint("9" * 64))
        self.assertEqual(st, 502, out)
        self.assertEqual(self.hv.status()["accepted"], [])

    def test_look_shows_the_fingerprint_to_compare_and_stores_nothing(self):
        self.with_certificate()
        b = self.peer_b()
        st, out = self.scall("POST", "/api/fleet/accepted/look", {"id": B_ID}, token=self.full)[:2]
        self.assertEqual(st, 200, out)
        self.assertEqual(out, {"id": B_ID, "name": "node-02", "fingerprint": hv.fingerprint(b.key)})
        self.assertEqual(self.hv.status()["accepted"], [])
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/look", {"id": "c" * 32}, token=self.full)[0], 502)      # not on the network
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/look", {"id": B_ID}, token=self.operator)[0], 403)

    def test_removing_an_acceptance_and_who_may_do_it(self):
        self.with_certificate()
        b = self.peer_b()
        self.accept_b()
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/remove", {"id": B_ID}, token=self.operator)[0], 403)
        self.assertIsNotNone(self.hv.approved(B_ID, b.key))
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/remove", {"id": B_ID}, token=self.full)[0], 200)
        self.assertIsNone(self.hv.approved(B_ID, b.key))
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/remove", {"id": B_ID}, token=self.full)[0], 404)
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/remove", {}, token=self.full)[0], 400)


class PeerRoute(FleetApiBase):
    """POST /api/fleet/peer on the real TLS listener, called by a second node that uses the real client."""

    def setUp(self):
        super().setUp()
        self.with_certificate()
        self.b = self.peer_b()
        self.b.directory[ME] = [("127.0.0.1", self.tls_port, None, self.peer_port)]

    def pair_lists(self, serial_a=1, serial_b=1):
        nodes = [{"id": ME, "name": "testbox", "key": self.hv.own_key(), "carry": "full"},
                 {"id": B_ID, "name": "node-02", "key": self.b.key, "carry": "full"}]
        for who, serial in ((self.b, serial_b), (None, serial_a)):
            data = {"v": 1, "fleet": "0123456789abcdef", "name": "Rig", "serial": serial, "issued": "2026-11-09", "nodes": nodes}
            body, sig = sign_list(self.exe, self.ca, data, PASS)
            if who is None:
                self.assertEqual(self.post_list(body.decode(), sig)[0], 200)
            else:
                who.take(body, sig)

    def test_an_approved_node_gets_an_answer_from_the_real_server_and_needs_no_token(self):
        self.pair_lists()
        out = self.b.peers.hello(ME)
        self.assertEqual(out["serial"], 1)

    def test_a_node_that_is_behind_is_sent_the_list_by_the_real_server(self):
        self.pair_lists(serial_a=3, serial_b=1)
        self.b.peers.hello(ME)
        self.assertEqual(self.b.handover.list.serial, 3)

    def test_a_node_that_is_ahead_is_adopted_by_this_node_through_a_pull(self):
        self.pair_lists(serial_a=1, serial_b=4)
        self.directory[B_ID] = [self.b.where]
        out = self.b.peers.hello(ME)                             # b carries its list in the hello; this node takes it (the signature decides)
        self.assertEqual(out["serial"], 4)
        self.assertEqual(self.hv.list.serial, 4)

    def test_the_panels_port_no_longer_answers_a_peer_call_at_all(self):
        self.pair_lists()
        body = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}
        self.assertEqual(self.call("POST", "/api/fleet/peer", body)[0], 404)             # plain http
        self.assertEqual(self.scall("POST", "/api/fleet/peer", body)[0], 404)            # the panel's own TLS port
        self.assertEqual(self.scall("GET", "/api/fleet/peer")[0], 404)
        self.assertEqual(self.pcall(self.b, "/api/fleet/peer", body)[0], 200)            # the peer port, with b's certificate

    def test_the_peer_port_answers_two_posts_and_nothing_else(self):
        self.pair_lists()
        self.assertEqual(self.pcall(self.b, "/api/fleet/peer", None, method="GET")[0], 404)
        self.assertEqual(self.pcall(self.b, "/api/https", None, method="GET")[0], 404)
        self.assertEqual(self.pcall(self.b, "/api/status", {})[0], 404)
        self.assertEqual(self.pcall(self.b, "/api/pair", {"pin": self.pin, "name": "x"})[0], 404)
        self.assertEqual(self.pcall(self.b, "/", None, method="GET")[0], 404)

    def test_a_caller_without_a_certificate_never_gets_past_the_handshake(self):
        self.pair_lists()
        body = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}
        with self.assertRaises((ssl.SSLError, OSError, http.client.HTTPException)):
            self.pcall(None, "/api/fleet/peer", body)
        self.assertEqual(self.hv.unknown_calls, 0)

    def test_a_caller_with_a_certificate_from_another_root_never_gets_past_the_handshake(self):
        self.pair_lists()
        from tests.peerfix import PASS as ROOT_PASS
        evil = PeerNode(self, "node-02", B_ID, self.other_root()[0], raw(os.path.join(self.ca, "root.pem")).decode(), self.exe, passphrase=ROOT_PASS)
        body = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}
        with self.assertRaises((ssl.SSLError, OSError, http.client.HTTPException)):
            self.pcall(evil, "/api/fleet/peer", body)

    def test_a_certificate_signed_for_servers_only_is_refused_even_from_the_right_root(self):
        self.pair_lists()
        server_only = self.server_only_node()
        body = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}
        try:
            st, out = self.pcall(server_only, "/api/fleet/peer", body)
        except (ssl.SSLError, OSError, http.client.HTTPException):
            return                                                       # refused in the handshake
        self.assertEqual(st, 403, out)
        self.assertIn("not for a client", out["error"])

    def test_the_key_in_the_body_must_be_the_key_of_the_certificate_that_called(self):
        self.pair_lists()
        third = PeerNode(self, "node-03", "c" * 32, self.ca, raw(os.path.join(self.ca, "root.pem")).decode(), self.exe, passphrase=PASS)
        body = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": third.handover.deny_digest()}
        st, out = self.pcall(third, "/api/fleet/peer", body)                 # a valid node of the root claims to be b
        self.assertEqual(st, 403, out)
        self.assertEqual(self.hv.unknown_calls, 1)
        self.assertEqual(self.pcall(self.b, "/api/fleet/peer", body)[0], 200)          # b itself is answered

    def test_an_unknown_caller_is_refused_with_a_count_and_no_log_line_and_the_route_is_throttled_by_failures(self):
        self.pair_lists()
        third = PeerNode(self, "node-03", "c" * 32, self.ca, raw(os.path.join(self.ca, "root.pem")).decode(), self.exe, passphrase=PASS)
        before = len(self.lines)
        said = len(self.said())
        body = {"from": "c" * 32, "key": third.key, "serial": 1, "deny": "0" * 16}
        st, out = self.pcall(third, "/api/fleet/peer", body)
        self.assertEqual(st, 403, out)
        self.assertEqual(self.hv.unknown_calls, 1)
        self.assertEqual(len(self.said()), said)
        self.assertEqual(len(self.lines), before)
        for _ in range(peer_mod.SOURCE_FAILS):
            self.pcall(third, "/api/fleet/peer", {"nonsense": True})
        self.assertEqual(self.pcall(third, "/api/fleet/peer", body)[0], 429)
        good = {"from": B_ID, "key": self.b.key, "serial": 1, "deny": self.b.handover.deny_digest()}
        self.assertEqual(self.pcall(self.b, "/api/fleet/peer", good)[0], 429)           # the same source stays stopped for the minute

    def test_a_support_session_can_not_reach_the_owners_routes(self):
        from pvj import support as sp
        self.assertTrue(any(p == "/api/fleet" for p in sp.REMOTE_DENY_PREFIX))

    def test_junk_sent_to_the_real_route_does_not_stop_the_owners_look_or_sync(self):
        self.pair_lists(serial_a=2, serial_b=2)
        self.directory[B_ID] = [self.b.where]
        for _ in range(15):
            self.pcall(self.b, "/api/fleet/peer", {"nonsense": True})
        self.assertEqual(self.pcall(self.b, "/api/fleet/peer", {"nonsense": True})[0], 429)        # the sender is stopped...
        self.assertEqual(self.scall("POST", "/api/fleet/accepted/look", {"id": B_ID}, token=self.full)[0], 200)      # ...this node is not
        self.assertEqual(self.scall("POST", "/api/fleet/sync", {}, token=self.full)[1]["nodes"][0]["ok"], True)

    def other_root(self):
        """(folder, root text) of a second, unrelated root."""
        from tests.peerfix import make_root
        folder = self.workdir()
        return folder, make_root(self.exe, folder, "Other root")

    def server_only_node(self):
        """A node whose certificate the right root signed for serverAuth ONLY (as before D132 slice 4a)."""
        from tests.test_boxcert import make_request
        work = self.workdir()
        key, csr = make_request(self.exe, work, ["node-09.local"], ["127.0.0.1"], cn="node-09.local")
        cnf, cert = os.path.join(work, "ext.cnf"), os.path.join(work, "cert.pem")
        with open(cnf, "w") as f:
            f.write("basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:node-09.local,IP:127.0.0.1\n")
        r = subprocess.run([self.exe, "x509", "-req", "-in", csr, "-CA", os.path.join(self.ca, "root.pem"), "-CAkey", os.path.join(self.ca, "root.key"),
                            "-passin", "pass:" + PASS, "-CAcreateserial", "-days", "30", "-extfile", cnf, "-out", cert], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return type("N", (), {"cert_path": cert, "key_path": key})()

    def test_sync_reaches_every_approved_node_through_the_real_client(self):
        self.pair_lists(serial_a=2, serial_b=2)
        self.directory[B_ID] = [self.b.where]
        st, out = self.scall("POST", "/api/fleet/sync", {}, token=self.full)[:2]
        self.assertEqual(st, 200, out)
        self.assertEqual(out["nodes"], [{"id": B_ID, "name": "node-02", "ok": True, "serial": 2}])
        self.assertEqual(self.scall("POST", "/api/fleet/sync", {"x": 1}, token=self.full)[0], 400)
        self.assertEqual(self.scall("POST", "/api/fleet/sync", {}, token=self.operator)[0], 403)
        self.assertEqual(self.hv.status()["unknown_calls"], 0)


class Walk(FleetApiBase):
    def test_the_list_the_acceptances_and_the_deny_file_appear_in_no_answer_export_diagnostics_or_log(self):
        self.with_certificate()
        text, sig = self.sign_list(3)
        self.assertEqual(self.post_list(text, sig)[0], 200)
        self.assertEqual(self.accept_b()[0], 200)
        self.hv.deny_add(DENIED, "gone", "leyline")
        self.hv.set_accepting(True)
        secrets = []
        for key in (PEER["key"], self.peer_b().key, DENIED):
            secrets += [key, hv.fingerprint(key), hv.fingerprint(key).upper(), key.upper()]
        secrets += ["".join(sig.split())[:40], "".join(sig.split())[-40:], "nxlx-fleet-v1"]
        seen = {"answers": 0}

        def look(what, data):
            if not isinstance(data, (bytes, str)):
                data = json.dumps(data)
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            for s in secrets:
                self.assertNotIn(s, data, what)

        for (method, path), _row in sorted(self.api.routes().items()):
            if method != "GET":
                continue
            for ask in (self.call, self.scall):
                st, body, _ = ask("GET", path, token=self.full)
                look((ask.__name__, path), body)
                seen["answers"] += 1
        self.assertGreater(seen["answers"], 40)
        for path in ("/api/system/diagnostics", "/api/https", "/api/fleet", "/api/nodes"):
            look(path, self.scall("GET", path, token=self.full)[1])
        # the routes the server answers itself, outside the table: downloads, pictures, the QR code
        self.player.screenshot = fake_screenshot
        for path in ("/api/https/root.crt", "/api/https/request.csr", "/api/qr.svg", "/api/preview.jpg", "/api/graphics/thumb",
                     "/api/graphics/preview.png", "/api/graphics/thumb?id=x", "/api/support", "/api/devices"):
            for ask in (self.call, self.scall):
                look((ask.__name__, path), ask("GET", path, token=self.full)[1])
                seen["answers"] += 1
        # the Owner's own status is the one place the list shows: names and the serial, no key of another node
        status = self.scall("GET", "/api/fleet", token=self.full)[1]
        self.assertEqual(status["list"]["serial"], 3)
        self.assertEqual([r["name"] for r in status["removed"]], ["gone"])
        st, out, _ = self.call("POST", "/api/system/settings/export", {"passwords": True}, token=self.full)
        self.assertEqual(st, 200)
        look("export", out)
        st, diag, _ = self.call("GET", "/api/system/diagnostics", token=self.full)
        look("diagnostics", diag)
        look("log", "\n".join(self.lines))
        look("events", "\n".join(" ".join(str(x) for x in i) for i in self.said()))
        self.assertTrue(any("fleet list" in t for _k, t, _w in self.said()))
        # an import carrying the files' contents changes none of them
        file = out["file"]
        file["settings"]["fleet_list"] = {"text": text, "sig": sig}
        file["settings"]["fleet"] = {"accept": True, "discovery": True, "list": text, "sig": sig, "deny": [DENIED]}
        before = {n: raw(os.path.join(self.tmp, n)) for n in ("fleet.json", "fleet.sig", "pairings.json", "deny.json")}
        self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(file).encode(), token=self.full)
        self.assertEqual({n: raw(os.path.join(self.tmp, n)) for n in before}, before)
        self.assertNotIn("fleet_list", json.dumps(self.settings.data))
        self.assertNotIn(DENIED, json.dumps(self.settings.data))
        self.assertNotIn("list", self.settings.data.get("fleet", {}))
        self.assertNotIn("sig", self.settings.data.get("fleet", {}))


class Reset(FleetApiBase):
    def test_a_factory_reset_takes_the_list_the_acceptances_the_deny_file_and_the_switch(self):
        self.with_certificate()
        self.post_list(*self.sign_list(2))
        self.accept_b()
        self.hv.deny_add(DENIED, "gone", "leyline")
        self.hv.set_accepting(True)
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.post_list(*self.sign_list(3))                       # leaves the first pair as .prev
        leftovers = ("fleet.json.prev", "fleet.sig.prev", "fleet.json.new", "fleet.sig.new", "fleet.json.new.tmp", "deny.json.tmp", "pairings.json.new")
        for name in leftovers[2:]:
            with open(os.path.join(self.tmp, name), "w") as f:
                f.write("x")
        for name in ("fleet.json", "fleet.sig", "pairings.json", "deny.json") + leftovers:
            self.assertTrue(os.path.exists(os.path.join(self.tmp, name)), name)
        st, body, _ = self.scall("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, token=secure)
        self.assertEqual(st, 200, body)
        self.assertEqual(body["problems"], [])
        for name in ("fleet.json", "fleet.sig", "pairings.json", "deny.json") + leftovers:
            self.assertFalse(os.path.exists(os.path.join(self.tmp, name)), name)
        self.assertFalse(self.hv.accepting())
        self.assertNotIn("accept", self.settings.data.get("fleet", {}))
        self.assertIsNone(self.hv.list)
        self.assertIsNone(self.hv.approved(PEER["id"], PEER["key"]))
        self.assertEqual(self.hv.denied_keys(), set())


if __name__ == "__main__":
    unittest.main()
