# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""tools/boxcert.py fleet ... end to end (D132, slice 1), with the real openssl and a throwaway root made by each test:
make a list, add and remove nodes with their carry, sign it, show and verify it, `clear`, a wrong passphrase, a
serial that only counts up, and asking a node for its card over a local TLS server. The owner's real root is never
touched. Skipped where there is no openssl."""
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import ssl
import tempfile
import threading
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import fleetlist as FL
from tests.test_boxcert import boxcert, make_request, openssls

PASS = "correct horse battery"
N1 = {"id": "a" * 32, "name": "node-01", "key": "1" * 64}
N2 = {"id": "b" * 32, "name": "leyline", "key": "2" * 64}


def slurp(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


@unittest.skipUnless(openssls(), "no openssl on this machine")
class FleetToolBase(unittest.TestCase):
    constraints = True               # the root's name constraints (the default); the TLS tests need 127.0.0.1, which they refuse

    def setUp(self):
        self.exe = openssls()[0]
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = os.path.join(self.tmp, "ca")
        boxcert.make_root(self.exe, self.dir, "Test root", ("local",), self.constraints, lambda prompt: PASS, lambda *a: None)
        self.lines = []

    def workdir(self):
        """A new folder inside the test's own temp folder (which is removed as a whole)."""
        self._n = getattr(self, "_n", 0) + 1
        path = os.path.join(self.tmp, "work-%s-%d" % (self._testMethodName[:40], self._n))
        os.mkdir(path)
        return path

    def tool(self, *argv, passphrase=PASS, folder=None):
        self.lines = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = boxcert.main(["--openssl", self.exe, "--dir", folder or self.dir] + list(argv), ask=lambda prompt: passphrase, out=self.lines.append)
        self.errors = err.getvalue()
        return code

    def say(self):
        return "\n".join(self.lines) + self.errors

    def card_file(self, node, name=None):
        path = os.path.join(self.tmp, "card-%s.json" % (name or node["name"]))
        with open(path, "w") as f:
            json.dump(dict(node, v=1), f)
        return path

    def add(self, node, carry, *more, **kw):
        return self.tool("fleet", "add", "--card", self.card_file(node), "--carry", carry, *more, **kw)

    def new(self, name="Anthony's rig"):
        self.assertEqual(self.tool("fleet", "new", "--name", name), 0)
        return json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["fleet"]

    def signed(self):
        return FL.verify(slurp(os.path.join(self.dir, "fleet.json"), "rb"), slurp(os.path.join(self.dir, "fleet.sig")), slurp(os.path.join(self.dir, "root.pem")), self.exe)

    def files(self):
        return {n: os.stat(os.path.join(self.dir, n)).st_mtime_ns for n in os.listdir(self.dir)}


class Making(FleetToolBase):
    def test_a_list_is_made_signed_and_a_node_would_believe_it(self):
        fleet = self.new()
        self.assertRegex(fleet, r"^[0-9a-f]{16}$")
        self.assertEqual(self.add(N1, "full"), 0)
        self.assertEqual(self.add(N2, "live"), 0)
        self.assertIn("Nothing is signed until", self.say())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "fleet.json")))
        self.assertEqual(self.tool("fleet", "sign"), 0)
        lst = self.signed()                                       # checked with the module a node uses, against root.pem
        self.assertEqual((lst.fleet, lst.serial, lst.name), (fleet, 1, "Anthony's rig"))
        self.assertEqual({n["name"]: n["carry"] for n in lst.nodes}, {"node-01": "full", "leyline": "live"})
        self.assertRegex(lst.issued, r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(self.tool("fleet", "verify"), 0)
        self.assertIn("serial 1", self.say())

    def test_every_signing_raises_the_serial_and_it_can_be_set_higher_never_lower(self):
        self.new()
        self.add(N1, "full")
        self.tool("fleet", "sign")
        self.assertEqual(self.signed().serial, 1)
        self.tool("fleet", "sign")
        self.assertEqual(self.signed().serial, 2)
        self.assertEqual(self.tool("fleet", "sign", "--serial", "10"), 0)
        self.assertEqual(self.signed().serial, 10)
        before = (slurp(os.path.join(self.dir, "fleet.json"), "rb"), slurp(os.path.join(self.dir, "fleet.sig")))
        for bad in ("10", "9", "1", "0", "-3"):
            self.assertEqual(self.tool("fleet", "sign", "--serial", bad), 1, bad)
            self.assertEqual((slurp(os.path.join(self.dir, "fleet.json"), "rb"), slurp(os.path.join(self.dir, "fleet.sig"))), before)
        self.assertEqual(self.tool("fleet", "sign"), 0)
        self.assertEqual(self.signed().serial, 11)

    def test_a_wrong_passphrase_writes_nothing(self):
        self.new()
        self.add(N1, "full")
        before = self.files()
        self.assertEqual(self.tool("fleet", "sign", passphrase="not the passphrase"), 1)
        self.assertEqual(self.files(), before)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "fleet.json")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "fleet.sig")))
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])
        # and after a good list, a wrong passphrase leaves that list exactly as it was
        self.assertEqual(self.tool("fleet", "sign"), 0)
        self.add(N2, "none")
        before = self.files()
        kept = slurp(os.path.join(self.dir, "fleet.json"), "rb")
        self.assertEqual(self.tool("fleet", "sign", passphrase="nope"), 1)
        self.assertEqual(self.files(), before)
        self.assertEqual(slurp(os.path.join(self.dir, "fleet.json"), "rb"), kept)

    def test_a_list_is_made_once_and_never_over_another(self):
        self.new()
        self.assertEqual(self.tool("fleet", "new", "--name", "Another"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["name"], "Anthony's rig")

    def test_remove_takes_a_node_off_and_the_next_list_lacks_it(self):
        self.new()
        self.add(N1, "full")
        self.add(N2, "live")
        self.tool("fleet", "sign")
        self.assertEqual(self.tool("fleet", "remove", "leyline"), 0)
        self.assertEqual(len(self.signed().nodes), 2)                    # nothing signed changes until it is signed
        self.tool("fleet", "show")
        self.assertIn("- leyline", self.say())
        self.assertEqual(self.tool("fleet", "sign"), 0)
        lst = self.signed()
        self.assertEqual([n["name"] for n in lst.nodes], ["node-01"])
        self.assertEqual(lst.serial, 2)
        self.assertEqual(self.tool("fleet", "remove", "nobody"), 1)
        self.assertEqual(self.tool("fleet", "remove", N1["id"]), 0)       # by id
        self.assertEqual(self.tool("fleet", "sign"), 1)                   # an empty list is not signed
        self.assertEqual(self.signed().serial, 2)

    def test_carry_is_required_and_must_be_one_of_three(self):
        self.new()
        for argv in (["fleet", "add", "--card", self.card_file(N1)], ["fleet", "add", "--card", self.card_file(N1), "--carry", "owner"]):
            with self.assertRaises(SystemExit):
                self.tool(*argv)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])

    def test_adding_again_changes_the_carry_but_a_new_key_needs_to_be_said(self):
        self.new()
        self.add(N1, "full")
        self.assertEqual(self.add(N1, "none"), 0)
        self.assertIn("updated", self.say())
        draft = json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))
        self.assertEqual([(n["name"], n["carry"]) for n in draft["nodes"]], [("node-01", "none")])
        self.assertEqual(self.add(dict(N1, key="9" * 64), "none"), 1)       # same id, other key
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"][0]["key"], "1" * 64)
        self.assertEqual(self.add(dict(N1, key="9" * 64), "none", "--replace-key"), 0)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"][0]["key"], "9" * 64)

    def test_two_nodes_cannot_share_a_key_or_a_name(self):
        self.new()
        self.add(N1, "full")
        self.assertEqual(self.add(dict(N2, key=N1["key"]), "live"), 1)
        self.assertEqual(self.add(dict(N2, name="NODE-01"), "live"), 1)
        self.assertEqual(len(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"]), 1)

    def test_a_card_that_is_not_a_card_adds_nothing(self):
        self.new()
        for body in ('{}', 'not json', json.dumps(dict(N1, id="short")), json.dumps(dict(N1, key="x")), json.dumps(dict(N1, name="a b"))):
            path = os.path.join(self.tmp, "bad.json")
            with open(path, "w") as f:
                f.write(body)
            self.assertEqual(self.tool("fleet", "add", "--card", path, "--carry", "live"), 1, body)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])
        self.assertEqual(self.tool("fleet", "add", "--carry", "live"), 1)                         # nothing to add from
        self.assertEqual(self.tool("fleet", "add", "--card", self.card_file(N1), "--cert", "x", "--carry", "live"), 1)

    def test_a_node_added_from_its_certificate_gets_the_hash_of_its_key(self):
        self.new()
        work = self.workdir()
        _key, csr = make_request(self.exe, work, ["node-01.local"], [], cn="node-01.local")
        cert = os.path.join(work, "cert.pem")
        boxcert.sign(self.exe, self.dir, csr, [], [], [], 30, cert, lambda p: PASS, out=lambda *a: None)
        self.assertEqual(self.tool("fleet", "add", "--cert", cert, "--id", "c" * 32, "--name", "node-01", "--carry", "full"), 0)
        want = FL.key_hash(FL.certificate_der(slurp(cert)))
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"][0]["key"], want)
        self.assertEqual(self.tool("fleet", "add", "--cert", cert, "--carry", "full"), 1)             # needs --id and --name
        self.assertEqual(self.tool("fleet", "add", "--cert", cert, "--id", "nope", "--name", "x", "--carry", "full"), 1)

    def test_the_list_can_be_continued_after_the_working_copy_is_lost(self):
        self.new()
        self.add(N1, "full")
        self.tool("fleet", "sign")
        os.unlink(os.path.join(self.dir, boxcert.FLEET_DRAFT))
        self.assertEqual(self.add(N2, "live"), 0)                          # made again from the signed list
        self.tool("fleet", "sign")
        lst = self.signed()
        self.assertEqual((lst.serial, sorted(n["name"] for n in lst.nodes)), (2, ["leyline", "node-01"]))

    def test_the_serial_cannot_start_again_when_the_signed_pair_is_gone(self):
        self.new()
        self.add(N1, "full")
        self.tool("fleet", "sign")
        old = self.signed()
        for name in ("fleet.json", "fleet.sig"):
            os.unlink(os.path.join(self.dir, name))
        self.assertEqual(self.tool("fleet", "sign"), 1)                              # refused: restore the pair
        self.assertIn("backup", self.errors)
        self.assertNotIn("fleet.json", os.listdir(self.dir))
        self.assertEqual(self.tool("fleet", "sign", "--serial", "1"), 1)             # not below what was signed
        self.assertEqual(self.tool("fleet", "sign", "--serial", "2"), 0)
        again = self.signed()
        self.assertEqual((again.serial, again.fleet), (2, old.fleet))
        FL.verify(again.body, slurp(os.path.join(self.dir, "fleet.sig")), slurp(os.path.join(self.dir, "root.pem")), self.exe, current=old)   # a node holding the old one takes it

    def test_a_missing_half_of_the_pair_points_to_the_backup_not_to_a_new_list(self):
        self.new()
        self.add(N1, "full")
        self.tool("fleet", "sign")
        os.unlink(os.path.join(self.dir, "fleet.sig"))
        self.assertEqual(self.tool("fleet", "show"), 1)
        self.assertIn("fleet verify", self.errors)
        self.assertIn("backup", self.errors)
        self.assertNotIn("Move", self.errors)

    def test_a_serial_close_to_the_top_is_refused_in_plain_words(self):
        self.new()
        self.add(N1, "full")
        self.assertEqual(self.tool("fleet", "sign", "--serial", str(FL.MAX_SERIAL - 5)), 1)
        self.assertIn("too high", self.errors)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "fleet.json")))
        self.assertEqual(self.tool("fleet", "sign", "--serial", str(FL.MAX_SERIAL - 100000)), 0)

    def test_the_signature_is_written_before_the_list(self):
        self.new()
        self.add(N1, "full")
        order = []
        real = boxcert._write_file
        def spy(path, data):
            order.append(os.path.basename(path))
            return real(path, data)
        boxcert._write_file = spy
        try:
            self.tool("fleet", "sign")
        finally:
            boxcert._write_file = real
        self.assertEqual([n for n in order if n in ("fleet.json", "fleet.sig")], ["fleet.sig", "fleet.json"])

    def test_replace_key_cannot_take_a_key_another_node_has(self):
        self.new()
        self.add(N1, "full")
        self.add(N2, "live")
        self.assertEqual(self.add(dict(N2, key=N1["key"]), "live", "--replace-key"), 1)      # the key of node-01
        self.assertIn("already has that key", self.errors)
        rows = {n["name"]: n["key"] for n in json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"]}
        self.assertEqual(rows, {"node-01": N1["key"], "leyline": N2["key"]})
        self.assertEqual(self.add(dict(N2, key="8" * 64), "live", "--replace-key"), 0)        # a key nobody has
        self.assertEqual({n["name"]: n["key"] for n in json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"]}["leyline"], "8" * 64)

    def test_a_certificate_your_root_did_not_sign_is_not_taken_by_add_cert(self):
        self.new()
        other = os.path.join(self.tmp, "stranger-ca")
        boxcert.make_root(self.exe, other, "Stranger", ("local",), True, lambda p: PASS, lambda *a: None)
        work = self.workdir()
        _key, csr = make_request(self.exe, work, ["node-01.local"], [], cn="node-01.local")
        cert = os.path.join(work, "cert.pem")
        boxcert.sign(self.exe, other, csr, [], [], [], 30, cert, lambda p: PASS, out=lambda *a: None)
        self.assertEqual(self.tool("fleet", "add", "--cert", cert, "--id", "c" * 32, "--name", "node-01", "--carry", "full"), 1)
        self.assertIn("not a certificate your root signed", self.errors)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])
        # a self-signed certificate and the root's own certificate are not a node's either
        self.assertEqual(self.tool("fleet", "add", "--cert", os.path.join(self.dir, "root.pem"), "--id", "c" * 32, "--name", "node-01", "--carry", "full"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])

    def test_the_root_key_is_asked_for_only_when_signing(self):
        asked = []
        def ask(prompt):
            asked.append(prompt)
            return PASS
        def main(*argv):
            with contextlib.redirect_stderr(io.StringIO()):
                return boxcert.main(["--openssl", self.exe, "--dir", self.dir] + list(argv), ask=ask, out=lambda *a: None)
        main("fleet", "new", "--name", "x")
        main("fleet", "add", "--card", self.card_file(N1), "--carry", "live")
        main("fleet", "remove", "node-01")
        main("fleet", "show")
        main("fleet", "verify")
        self.assertEqual(asked, [])
        main("fleet", "add", "--card", self.card_file(N1), "--carry", "live")
        main("fleet", "sign")
        self.assertEqual(len(asked), 1)


class Checking(FleetToolBase):
    def setUp(self):
        super().setUp()
        self.new()
        self.add(N1, "full")
        self.add(N2, "live")
        self.tool("fleet", "sign")

    def test_show_names_the_signed_list_and_what_the_working_copy_changes(self):
        self.tool("fleet", "show")
        said = self.say()
        for word in ("node-01", "leyline", "carry full", "carry live", "serial  : 1", "same as the signed list"):
            self.assertIn(word, said)
        self.add(N1, "none")
        self.tool("fleet", "remove", "leyline")
        self.add({"id": "c" * 32, "name": "third", "key": "3" * 64}, "live")
        self.tool("fleet", "show")
        said = self.say()
        for word in ("+ third", "- leyline", "node-01: carry full to none", "not signed yet"):
            self.assertIn(word, said)

    def test_verify_fails_for_a_changed_list_a_foreign_signature_and_a_missing_file(self):
        body = slurp(os.path.join(self.dir, "fleet.json"), "rb")
        # a list taken off a node, changed on the way
        changed = os.path.join(self.tmp, "changed.json")
        with open(changed, "wb") as f:
            f.write(body.replace(b'"live"', b'"full"'))
        self.assertEqual(self.tool("fleet", "verify", "--list", changed), 1)
        self.assertIn("NOT good", self.say())
        # the same list signed by a different root
        other = os.path.join(self.tmp, "other-ca")
        boxcert.make_root(self.exe, other, "Other", ("local",), True, lambda p: PASS, lambda *a: None)
        self.assertEqual(self.tool("--dir", other, "fleet", "verify", "--list", os.path.join(self.dir, "fleet.json"), "--sig", os.path.join(self.dir, "fleet.sig"), folder=other), 1)
        self.assertEqual(self.tool("fleet", "verify", "--fleet", "f" * 16), 1)
        self.assertIn("another fleet", self.say())
        self.assertEqual(self.tool("fleet", "verify", "--list", os.path.join(self.tmp, "missing.json")), 1)

    def test_a_signed_file_that_was_changed_stops_further_work_until_it_is_moved_away(self):
        path = os.path.join(self.dir, "fleet.json")
        with open(path, "wb") as f:
            f.write(slurp(path, "rb").replace(b"node-01", b"node-99"))
        os.unlink(os.path.join(self.dir, boxcert.FLEET_DRAFT))
        for argv in (["fleet", "show"], ["fleet", "sign"], ["fleet", "remove", "leyline"]):
            self.assertEqual(self.tool(*argv), 1, argv)
        self.assertEqual(self.tool("fleet", "verify"), 1)

    def test_clear_goes_into_the_next_signed_list_and_a_node_reads_it(self):
        self.assertEqual(self.tool("fleet", "clear", "leyline"), 0)                  # by node name
        self.assertEqual(self.tool("fleet", "clear", "e" * 64), 0)                   # by key hash
        self.assertEqual(self.tool("fleet", "clear", "e" * 64), 0)                   # twice is the same
        self.assertEqual(self.signed().clear, [])                                    # not until signed
        self.assertEqual(self.tool("fleet", "sign"), 0)
        lst = self.signed()
        self.assertEqual(lst.clear, sorted([N2["key"], "e" * 64]))
        self.assertEqual(lst.serial, 2)                                              # a clear is a newer list: an older serial cannot do it
        self.assertEqual(self.tool("fleet", "clear", "--undo", "e" * 64), 0)
        self.assertEqual(self.tool("fleet", "clear", "--undo", "d" * 64), 1)
        self.assertEqual(self.tool("fleet", "sign"), 0)
        self.assertEqual(self.signed().clear, [N2["key"]])
        for bad in ("nobody", "xyz", "E" * 63):
            self.assertEqual(self.tool("fleet", "clear", bad), 1, bad)

    def test_fleet_help_says_what_it_is_in_plain_words(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.tool("fleet"), 2)
            for argv in (["fleet", "--help"], ["fleet", "add", "--help"], ["fleet", "sign", "--help"], ["fleet", "clear", "--help"]):
                with self.assertRaises(SystemExit):
                    boxcert.main(argv)
        text = " ".join(buf.getvalue().split())
        for word in ("full (Owner)", "live (Operator)", "none (takes nobody in)", "--card", "--cert", "--replace-key", "venue", "never judged",
                     "root key never goes on a node", "never lower", "docs/HTTPS.md"):
            self.assertIn(word, text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2014", slurp(boxcert.__file__))


class Asking(FleetToolBase):
    """`fleet add HOST` asks the node for its card over https, trusting only the root in the folder."""
    constraints = False


    def serve(self, ca_folder, card_for_cert=None, status=200, body_override=None, ips=("127.0.0.1",)):
        work = self.workdir()
        key, csr = make_request(self.exe, work, ["localhost"], list(ips), cn="localhost")
        cert = os.path.join(work, "cert.pem")
        boxcert.sign(self.exe, ca_folder, csr, [], [], [], 30, cert, lambda p: PASS, out=lambda *a: None)
        der = FL.certificate_der(slurp(cert))
        card = FL.make_card("d" * 32, "node-04", der)
        if card_for_cert is not None:
            card["key"] = card_for_cert
        payload = body_override if body_override is not None else json.dumps(card).encode()
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], card, seen

    def test_a_node_with_a_certificate_from_the_root_is_added_by_its_address(self):
        self.new()
        port, card, seen = self.serve(self.dir)
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "live"), 0, self.errors)
        self.assertEqual(seen, ["/api/fleet/card"])
        row = json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"][0]
        self.assertEqual((row["id"], row["name"], row["key"], row["carry"]), ("d" * 32, "node-04", card["key"], "live"))

    def test_a_node_with_a_certificate_from_another_root_is_not_added(self):
        self.new()
        other = os.path.join(self.tmp, "other-ca")
        boxcert.make_root(self.exe, other, "Other", ("local",), False, lambda p: PASS, lambda *a: None)
        port, _card, seen = self.serve(other)
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "full"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])

    def test_a_card_whose_key_is_not_that_of_the_certificate_shown_is_not_added(self):
        self.new()
        port, _card, _seen = self.serve(self.dir, card_for_cert="7" * 64)
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "full"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])

    def test_a_node_without_the_card_route_says_so(self):
        self.new()
        port, _card, _seen = self.serve(self.dir, status=404, body_override=b"{}")
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "full"), 1)
        port, _card, _seen = self.serve(self.dir, body_override=b"not a card")
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "full"), 1)
        self.assertEqual(self.tool("fleet", "add", "not a host!", "--carry", "full"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])

    def test_a_certificate_from_the_root_that_does_not_cover_the_address_is_refused(self):
        self.new()
        port, _card, _seen = self.serve(self.dir, ips=())                  # names localhost only; we ask by 127.0.0.1
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "full"), 1)
        self.assertEqual(json.loads(slurp(os.path.join(self.dir, boxcert.FLEET_DRAFT)))["nodes"], [])
        self.assertEqual(self.tool("fleet", "add", "localhost", "--port", str(port), "--carry", "full"), 0, self.errors)    # the name it does cover

    def test_the_tls_context_trusts_exactly_one_root_and_checks_the_name(self):
        self.new()
        port, _card, _seen = self.serve(self.dir)
        made = []
        real = ssl.create_default_context
        def spy(*a, **kw):
            ctx = real(*a, **kw)
            made.append(ctx)
            return ctx
        ssl.create_default_context = spy
        try:
            self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", str(port), "--carry", "live"), 0, self.errors)
        finally:
            ssl.create_default_context = real
        self.assertEqual(len(made), 1)
        ctx = made[0]
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(len(ctx.get_ca_certs()), 1)                          # your root, not the system's
        self.assertEqual(ctx.get_ca_certs()[0]["subject"][0][0][1], "Test root")

    def test_a_node_that_is_not_there_is_an_error_not_a_hang(self):
        self.new()
        self.assertEqual(self.tool("fleet", "add", "127.0.0.1", "--port", "9", "--carry", "full"), 1)


class Separate(unittest.TestCase):
    def test_the_other_commands_still_know_their_own_help(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            boxcert.main(["--help"])
        for word in ("make-root", "fleet", "the fleet list: which nodes may open each other"):
            self.assertIn(word, buf.getvalue())

    def test_the_tool_loads_the_checker_nodes_use(self):
        self.assertEqual(boxcert.fleetlist().__file__, FL.__file__)
        self.assertEqual(hashlib.sha256(slurp(boxcert.fleetlist().__file__, "rb")).hexdigest(), hashlib.sha256(slurp(FL.__file__, "rb")).hexdigest())


    def test_the_docs_describe_every_fleet_command(self):
        here = os.path.dirname(os.path.abspath(__file__))
        text = slurp(os.path.join(here, os.pardir, "docs", "HTTPS.md"))
        for cmd in ("fleet new", "fleet add", "fleet remove", "fleet sign", "fleet show", "fleet verify", "fleet clear", "--carry", "--replace-key", "--serial"):
            self.assertIn(cmd, text)
        self.assertNotIn("\u2014", text[text.index("## Letting your nodes open each other"):text.index("## Moving the signing key")])


if __name__ == "__main__":
    unittest.main()
