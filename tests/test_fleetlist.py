# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj/fleetlist.py (D132, slice 1): the check a node makes before it believes a fleet list. Every root here is a
throwaway made by the test; nothing reads the owner's real root. A list is signed here with plain `openssl`, not with
the tool, so the tool and the checker do not vouch for each other. Skipped where there is no openssl."""
import ast
import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import fleetlist as FL
from tests.test_boxcert import boxcert, make_request, openssls

PASS = "correct horse battery"


def slurp(path):
    with open(path) as f:
        return f.read()

FLEET = "0123456789abcdef"
NODE1 = {"id": "a" * 32, "name": "node-01", "key": "1" * 64, "carry": "full"}
NODE2 = {"id": "b" * 32, "name": "leyline", "key": "2" * 64, "carry": "live"}


def good(serial=3, fleet=FLEET, nodes=None, **more):
    data = {"v": 1, "fleet": fleet, "name": "Anthony's rig", "serial": serial, "issued": "2026-11-09",
            "nodes": copy.deepcopy(nodes if nodes is not None else [NODE1, NODE2])}
    data.update(more)
    return data


def raw(data):
    """Bytes for a list the tool would never write (to see the checker refuse them)."""
    return json.dumps(data).encode()


@unittest.skipUnless(openssls(), "no openssl on this machine")
class FleetListBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = openssls()[0]
        cls.tmp = tempfile.mkdtemp()
        cls.roots = {}
        for name in ("one", "two"):
            folder = os.path.join(cls.tmp, name)
            boxcert.make_root(cls.exe, folder, "Test root " + name, ("local",), True, lambda prompt: PASS, lambda *a: None)
            with open(os.path.join(folder, "root.pem")) as f:
                cls.roots[name] = (os.path.join(folder, "root.key"), f.read(), folder)
        cls.root_pem = cls.roots["one"][1]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def workdir(self):
        """A new folder inside the test's own temp folder (which is removed as a whole)."""
        self._n = getattr(self, "_n", 0) + 1
        path = os.path.join(self.tmp, "work-%s-%d" % (self._testMethodName[:40], self._n))
        os.mkdir(path)
        return path

    def sign_message(self, message, which="one"):
        """The root's signature over any bytes, as base64 text, made with plain openssl."""
        key = self.roots[which][0]
        msg, sig = os.path.join(self.tmp, "m.bin"), os.path.join(self.tmp, "m.sig")
        with open(msg, "wb") as f:
            f.write(message)
        r = subprocess.run([self.exe, "dgst", "-sha256", "-sign", key, "-passin", "stdin", "-out", sig, msg], input=PASS + "\n",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(sig, "rb") as f:
            return base64.encodebytes(f.read()).decode()

    def signed(self, data, which="one", body=None):
        body = body if body is not None else FL.canonical(data)
        lst = json.loads(body)
        return body, self.sign_message(FL.signed_bytes(lst["fleet"], lst["serial"], body), which)

    def verify(self, body, sig, **kw):
        return FL.verify(body, sig, kw.pop("root", self.root_pem), self.exe, **kw)

    def refused(self, code, body, sig, **kw):
        with self.assertRaises(FL.FleetError) as cm:
            self.verify(body, sig, **kw)
        self.assertEqual(cm.exception.code, code, cm.exception.message)
        return cm.exception


class Accepting(FleetListBase):
    def test_a_list_the_root_signed_is_believed_and_read(self):
        body, sig = self.signed(good())
        lst = self.verify(body, sig)
        self.assertEqual((lst.fleet, lst.name, lst.serial, lst.issued), (FLEET, "Anthony's rig", 3, "2026-11-09"))
        self.assertEqual(sorted(n["name"] for n in lst.nodes), ["leyline", "node-01"])
        self.assertEqual(lst.node(NODE1["id"])["carry"], "full")
        self.assertEqual(lst.node_by_key(NODE2["key"])["name"], "leyline")
        self.assertIsNone(lst.node("c" * 32))
        self.assertEqual(lst.digest, hashlib.sha256(body).hexdigest())
        self.assertEqual(lst.body, body)

    def test_the_signed_bytes_are_the_prefix_the_fleet_the_serial_and_the_file(self):
        self.assertEqual(FL.signed_bytes(FLEET, 3, b"{}"), b"nxlx-fleet-v1\n" + FLEET.encode() + b"\n3\n{}")
        body = FL.canonical(good())
        self.assertEqual(FL.signed_bytes_of(body), b"nxlx-fleet-v1\n0123456789abcdef\n3\n" + body)
        for fleet, serial in (("short", 1), (FLEET, 0), (FLEET, True), (FLEET, "3"), (None, 1)):
            with self.assertRaises(FL.FleetError):
                FL.signed_bytes(fleet, serial, b"")

    def test_a_signature_with_line_breaks_or_spaces_is_the_same_signature(self):
        body, sig = self.signed(good())
        packed = "".join(sig.split())
        self.verify(body, packed)
        self.verify(body, "  " + sig.replace("\n", "\r\n") + "\n\n")
        self.verify(body, sig.encode("ascii"))

    def test_clear_is_read_and_kept_sorted(self):
        body, sig = self.signed(good(clear=["f" * 64, "e" * 64]))
        lst = self.verify(body, sig)
        self.assertEqual(lst.clear, ["e" * 64, "f" * 64])
        self.assertEqual(lst.as_dict()["clear"], ["e" * 64, "f" * 64])
        self.assertNotIn("clear", self.verify(*self.signed(good())).as_dict())

    def test_the_same_bytes_again_are_accepted_and_a_higher_serial_replaces(self):
        body, sig = self.signed(good(serial=3))
        held = self.verify(body, sig)
        self.assertEqual(self.verify(body, sig, current=held).serial, 3)           # told twice by two peers
        newer = self.signed(good(serial=4))
        self.assertEqual(self.verify(*newer, current=held).serial, 4)
        self.assertEqual(self.verify(*newer, fleet=FLEET, current=held).serial, 4)

    def test_a_first_list_sets_the_fleet_and_a_named_fleet_is_enforced(self):
        body, sig = self.signed(good())
        self.assertEqual(self.verify(body, sig, fleet=None).fleet, FLEET)
        self.assertEqual(self.verify(body, sig, fleet=FLEET).fleet, FLEET)


class Refusing(FleetListBase):
    def test_one_changed_byte_anywhere_fails(self):
        body, sig = self.signed(good())
        positions = list(range(0, len(body), 29)) + [len(body) - 1, len(body) - 2]
        for at in positions:
            for flip in (0x01, 0x20):
                bad = bytearray(body)
                bad[at] ^= flip
                try:
                    self.verify(bytes(bad), sig)
                except FL.FleetError:
                    pass
                else:
                    self.fail("a list with byte %d changed (xor %#x) was believed" % (at, flip))

    def test_the_changes_that_matter_fail_as_not_signed(self):
        body, sig = self.signed(good())
        text = body.decode()
        for old, new in (('"carry": "live"', '"carry": "full"'), ('"leyline"', '"leylinf"'), ('"serial": 3', '"serial": 9'),
                         ('"serial": 3', '"serial": 2'), (FLEET, "f" * 16), ('"node-01"', '"node-09"'), (' ' * 3, ' ' * 4),
                         (NODE2["key"], "3" * 64), ("2026-11-09", "2026-11-10")):
            self.assertIn(old, text)
            e = self.refused("not-signed", text.replace(old, new, 1).encode(), sig)
            self.assertIn("changed after it was signed", e.message)

    def test_a_trailing_byte_or_a_missing_one_fails(self):
        body, sig = self.signed(good())
        self.refused("not-signed", body + b"\n", sig)
        self.refused("not-signed", body + b" ", sig)
        self.refused("bad-list", body[:-3], sig)

    def test_a_list_signed_by_another_root_fails(self):
        body, sig = self.signed(good(), which="two")
        self.refused("not-signed", body, sig)
        self.verify(body, sig, root=self.roots["two"][1])               # and is good for the root that did sign it

    def test_a_list_for_another_fleet_is_refused_even_when_signed(self):
        other = self.signed(good(fleet="fedcba9876543210"))
        e = self.refused("other-fleet", *other, fleet=FLEET)
        self.assertIn("another fleet", e.message)
        held = self.verify(*self.signed(good()))
        self.refused("other-fleet", *other, current=held)                  # a node holding a list is in that fleet

    def test_the_fleet_in_the_signature_must_be_the_fleet_in_the_file(self):
        """The root signed fleet A's prefix over fleet B's bytes (a list moved to another fleet): the prefix is made
        from the file's own fleet id, so the signature does not match."""
        body = FL.canonical(good(fleet="fedcba9876543210"))
        sig = self.sign_message(FL.signed_bytes(FLEET, 3, body))
        self.refused("not-signed", body, sig)

    def test_the_serial_in_the_signature_must_be_the_serial_in_the_file(self):
        body = FL.canonical(good(serial=3))
        sig = self.sign_message(FL.signed_bytes(FLEET, 2, body))
        self.refused("not-signed", body, sig)

    def test_a_lower_serial_is_refused(self):
        held = self.verify(*self.signed(good(serial=5)))
        e = self.refused("older", *self.signed(good(serial=4)), current=held)
        self.assertIn("serial 4", e.message)
        self.assertIn("serial 5", e.message)
        self.refused("older", *self.signed(good(serial=1)), current=held)

    def test_the_same_serial_with_other_contents_is_refused(self):
        held = self.verify(*self.signed(good(serial=5)))
        e = self.refused("same-serial", *self.signed(good(serial=5, nodes=[NODE1])), current=held)
        self.assertIn("serial 5", e.message)

    def test_a_signature_without_the_prefix_does_not_pass(self):
        body = FL.canonical(good())
        for message in (body,                                                    # the file alone
                        FLEET.encode() + b"\n3\n" + body,                       # no prefix
                        b"nxlx-fleet-v1" + FLEET.encode() + b"\n3\n" + body,    # prefix without its newline
                        b"nxlx-fleet-v2\n" + FLEET.encode() + b"\n3\n" + body,  # another version of the prefix
                        b"nxlx-box-v1\n" + FLEET.encode() + b"\n3\n" + body,    # another purpose
                        b"nxlx-fleet-v1\n3\n" + FLEET.encode() + b"\n" + body,  # the parts in another order
                        b"nxlx-fleet-v1\n" + FLEET.encode() + b"\n03\n" + body, # another way to write the serial
                        b"nxlx-fleet-v1\n" + FLEET.encode() + b"\n3\n" + body.strip()):
            self.refused("not-signed", body, self.sign_message(message))

    def test_a_signature_the_root_made_for_something_else_does_not_pass(self):
        """A box's certificate, or any other file the root signs, is not a list."""
        body = FL.canonical(good())
        with open(os.path.join(self.roots["one"][2], "root.pem"), "rb") as f:
            other = f.read()
        self.refused("not-signed", body, self.sign_message(other))
        self.refused("not-signed", body, self.sign_message(b""))

    def test_a_replayed_older_signature_over_a_newer_list_fails(self):
        _, old_sig = self.signed(good(serial=2))
        self.refused("not-signed", FL.canonical(good(serial=3)), old_sig)

    def test_duplicates_are_refused_even_when_the_root_signed_them(self):
        twin_id = dict(NODE2, id=NODE1["id"])
        twin_key = dict(NODE2, key=NODE1["key"])
        twin_name = dict(NODE2, name="NODE-01")
        for nodes in ([NODE1, twin_id], [NODE1, twin_key], [NODE1, twin_name], [NODE1, dict(NODE1)]):
            body = raw(good(nodes=nodes))
            _, sig = self.signed(None, body=body)
            self.refused("duplicate", body, sig)
        body = raw(good(clear=["e" * 64, "e" * 64]))
        self.refused("duplicate", body, self.signed(None, body=body)[1])

    def test_a_field_written_twice_in_the_file_is_refused(self):
        body = FL.canonical(good()).decode().replace('"serial": 3,', '"serial": 3, "serial": 99,')
        self.assertIn('"serial": 99', body)
        e = self.refused("bad-list", body.encode(), self.sign_message(b"x"))
        self.assertIn("twice", e.message)

    def test_bad_carry_values_are_refused(self):
        for bad in ("root", "FULL", "Full", "", " full", "owner", None, 1, True, ["full"], {"a": 1}):
            body = raw(good(nodes=[dict(NODE1, carry=bad)]))
            self.refused("bad-list", body, self.signed(None, body=body)[1])
        gone = {k: v for k, v in NODE1.items() if k != "carry"}
        self.refused("bad-list", raw(good(nodes=[gone])), "AAAAAAAAAAAA")

    def test_a_signed_list_with_a_bad_shape_is_still_refused(self):
        cases = {
            "unknown top field": good(admin=True),
            "version 2": good(v=2),
            "version true": good(v=True),
            "version float": good(v=1.0),
            "no fleet": {k: v for k, v in good().items() if k != "fleet"},
            "fleet upper case": good(fleet="0123456789ABCDEF"),
            "fleet short": good(fleet="0123456789abcde"),
            "fleet long": good(fleet="0123456789abcdef0"),
            "serial 0": good(serial=0),
            "serial negative": good(serial=-1),
            "serial bool": good(serial=True),
            "serial text": good(serial="3"),
            "serial float": good(serial=3.0),
            "serial huge": good(serial=2 ** 32),
            "date not a day": good(issued="2026-13-40"),
            "date shape": good(issued="9 Nov 2026"),
            "name empty": good(name=""),
            "name padded": good(name=" rig"),
            "name control": good(name="rig\x07"),
            "name line break": good(name="rig\nrig"),
            "name too long": good(name="x" * 65),
            "name number": good(name=5),
            "no nodes": good(nodes=[]),
            "nodes not a list": good(nodes={"a": 1}),
            "node not an object": good(nodes=["node-01"]),
            "node extra field": good(nodes=[dict(NODE1, role="owner")]),
            "node id short": good(nodes=[dict(NODE1, id="a" * 31)]),
            "node id upper": good(nodes=[dict(NODE1, id="A" * 32)]),
            "node key short": good(nodes=[dict(NODE1, key="1" * 63)]),
            "node key not hex": good(nodes=[dict(NODE1, key="g" * 64)]),
            "node name slash": good(nodes=[dict(NODE1, name="a/b")]),
            "node name empty": good(nodes=[dict(NODE1, name="")]),
            "node name long": good(nodes=[dict(NODE1, name="n" * 64)]),
            "clear empty": good(clear=[]),
            "clear not hashes": good(clear=["node-01"]),
            "clear not a list": good(clear="e" * 64),
            "too many nodes": good(nodes=[{"id": "%032x" % i, "name": "n%d" % i, "key": "%064x" % i, "carry": "none"} for i in range(1, FL.MAX_NODES + 2)]),
            "too many clears": good(clear=["%064x" % i for i in range(1, FL.MAX_CLEAR + 2)]),
        }
        for what, data in cases.items():
            body = raw(data)
            sig = self.signed(None, body=body)[1] if isinstance(data.get("serial"), int) and isinstance(data.get("fleet"), str) and FL.FLEET_WORD.fullmatch(data["fleet"]) and data["serial"] >= 1 and not isinstance(data["serial"], bool) else "AAAAAAAAAAAA"
            with self.assertRaises(FL.FleetError, msg=what) as cm:
                self.verify(body, sig)
            self.assertIn(cm.exception.code, ("bad-list", "bad-version", "duplicate", "too-big"), what)

    def test_not_a_list_at_all(self):
        for body in (b"", b"[]", b"3", b"null", b"{", b'{"v": 1,}', b"\xef\xbb\xbf" + FL.canonical(good()), b"\xff\xfe", "{}".encode("utf-16"),
                     b'{"v": NaN}', b'{"v": Infinity}', b"[" * 5000 + b"]" * 5000):
            with self.assertRaises(FL.FleetError, msg=repr(body[:20])) as cm:
                self.verify(body, "AAAAAAAAAAAA")
            self.assertEqual(cm.exception.code, "bad-list" if len(body) < FL.MAX_BYTES else "too-big", repr(body[:20]))
        with self.assertRaises(FL.FleetError):
            FL.parse("a string, not bytes")

    def test_a_list_over_the_size_limit_is_refused_before_it_is_read(self):
        body = FL.canonical(good())
        pad = body[:-2] + b" " * (FL.MAX_BYTES - len(body) + 5) + b"}\n"
        self.assertGreater(len(pad), FL.MAX_BYTES)
        e = self.refused("too-big", pad, self.sign_message(pad))
        self.assertIn(str(FL.MAX_BYTES), e.message)
        at_limit = body[:-2] + b" " * (FL.MAX_BYTES - len(body) + 1) + b"}"
        self.assertEqual(len(at_limit), FL.MAX_BYTES)
        FL.parse(at_limit)                                                      # exactly the limit is fine
        with self.assertRaises(FL.FleetError):
            FL.parse(at_limit + b" ")

    def test_the_largest_legal_list_fits_in_the_limit(self):
        nodes = [{"id": "%032x" % i, "name": "n" * 63, "key": "%064x" % i, "carry": "none"} for i in range(1, FL.MAX_NODES + 1)]
        nodes = [dict(n, name=("n%d-" % i) + "x" * 55) for i, n in enumerate(nodes)]
        data = good(nodes=nodes, clear=["%064x" % (1000 + i) for i in range(FL.MAX_CLEAR)])
        body = FL.canonical(data)
        self.assertLessEqual(len(body), FL.MAX_BYTES)
        self.verify(*self.signed(data))

    def test_bad_signature_files(self):
        body = FL.canonical(good())
        for sig in ("", "   \n", "not base64 !!", "AAAA", "A" * (FL.MAX_SIG_TEXT + 1), b"\xff\xfe", None, 5):
            with self.assertRaises(FL.FleetError, msg=repr(sig)[:20]) as cm:
                self.verify(body, sig)
            self.assertEqual(cm.exception.code, "bad-signature", repr(sig)[:20])
        junk = base64.b64encode(os.urandom(104)).decode()
        self.refused("not-signed", body, junk)
        good_sig = self.signed(good())[1]
        raw_sig = base64.b64decode(good_sig)
        self.refused("not-signed", body, base64.b64encode(raw_sig[:-1] + bytes([raw_sig[-1] ^ 1])).decode())
        self.refused("not-signed", body, base64.b64encode(raw_sig[:-2]).decode())

    def test_no_root_and_no_openssl_are_said_plainly(self):
        body, sig = self.signed(good())
        for pem in ("", None, "not a certificate", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", 5):
            with self.assertRaises(FL.FleetError) as cm:
                FL.verify(body, sig, pem, self.exe)
            self.assertEqual(cm.exception.code, "no-root", repr(pem))
        with self.assertRaises(FL.FleetError) as cm:
            FL.verify(body, sig, self.root_pem, os.path.join(self.tmp, "no-such-openssl"))
        self.assertEqual(cm.exception.code, "no-openssl")

    def test_the_roots_private_key_is_never_needed_or_named(self):
        """The checker is given the root CERTIFICATE text and nothing else; a key in that text would not be read."""
        key_text = slurp(self.roots["one"][0])
        body, sig = self.signed(good())
        with self.assertRaises(FL.FleetError) as cm:
            FL.verify(body, sig, key_text, self.exe)
        self.assertEqual(cm.exception.code, "no-root")


class Keys(FleetListBase):
    def leaf(self, folder, key_from=None, name="node-01.local"):
        """A certificate for a node: a request (a new key, or the key of a former request), signed by the first root."""
        work = self.workdir()
        if key_from:
            shutil.copy(key_from, os.path.join(work, "box.key"))
            cnf = os.path.join(work, "req.cnf")
            with open(cnf, "w") as f:
                f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=DNS:%s\n" % (name, name))
            subprocess.run([self.exe, "req", "-new", "-key", os.path.join(work, "box.key"), "-config", cnf, "-out", os.path.join(work, "box.csr")],
                           check=True, capture_output=True)
            key, csr = os.path.join(work, "box.key"), os.path.join(work, "box.csr")
        else:
            key, csr = make_request(self.exe, work, [name], [], cn=name)
        out = os.path.join(work, "cert.pem")
        boxcert.sign(self.exe, self.roots["one"][2], csr, [], [], [], 30, out, lambda prompt: PASS, out=lambda *a: None)
        return key, out

    def spki_hash_by_openssl(self, cert):
        pub = subprocess.run([self.exe, "x509", "-in", cert, "-noout", "-pubkey"], capture_output=True, check=True).stdout
        der = subprocess.run([self.exe, "pkey", "-pubin", "-outform", "DER"], input=pub, capture_output=True, check=True).stdout
        return hashlib.sha256(der).hexdigest()

    def test_the_key_hash_is_the_hash_of_the_public_key_as_openssl_gives_it(self):
        _, cert = self.leaf(None)
        with open(cert) as f:
            text = f.read()
        self.assertEqual(text.count("BEGIN CERTIFICATE"), 2)                       # the node's, then the root
        self.assertEqual(FL.key_hash(FL.certificate_der(text)), self.spki_hash_by_openssl(cert))

    def test_a_renewal_that_keeps_the_key_keeps_the_hash_and_a_new_key_changes_it(self):
        key, first = self.leaf(None)
        _, renewed = self.leaf(None, key_from=key)
        _, other = self.leaf(None)
        h = lambda p: FL.key_hash(FL.certificate_der(slurp(p)))               # noqa: E731
        self.assertNotEqual(slurp(first), slurp(renewed))
        self.assertEqual(h(first), h(renewed))
        self.assertNotEqual(h(first), h(other))
        self.assertTrue(FL.KEY_WORD.fullmatch(h(first)))

    def test_bad_certificates_are_said_so(self):
        for text in ("", "nothing", "-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n"):
            with self.assertRaises(FL.FleetError):
                FL.certificate_der(text)
        for der in (b"", b"\x30\x00", b"\x02\x01\x01", b"\x30\x03\x30\x01\x00", os.urandom(40)):
            with self.assertRaises(FL.FleetError):
                FL.key_hash(der)

    def test_the_card_holds_the_id_the_name_and_the_key_and_nothing_secret(self):
        _, cert = self.leaf(None)
        der = FL.certificate_der(slurp(cert))
        card = FL.make_card("c" * 32, "node-03", der)
        self.assertEqual(sorted(card), ["id", "key", "name", "v"])
        self.assertEqual(FL.parse_card(json.dumps(card).encode()), {"id": "c" * 32, "name": "node-03", "key": FL.key_hash(der)})
        self.assertEqual(FL.parse_card(json.dumps(dict(card, tier="lean")).encode())["id"], "c" * 32)    # extra facts are ignored
        for bad in (b"", b"[]", b"{}", json.dumps(dict(card, id="x")).encode(), json.dumps(dict(card, key="1" * 63)).encode(),
                    json.dumps(dict(card, name="a b")).encode(), b"x" * 5000, b'{"id": "%s", "id": "%s"}' % (b"c" * 32, b"c" * 32)):
            with self.assertRaises(FL.FleetError) as cm:
                FL.parse_card(bad)
            self.assertEqual(cm.exception.code, "bad-card")


class Shape(unittest.TestCase):
    def test_canonical_bytes_are_the_same_for_the_same_list_whatever_the_order_given(self):
        a = good()
        b = good(nodes=[NODE2, NODE1])
        b = dict(reversed(list(b.items())))
        self.assertEqual(FL.canonical(a), FL.canonical(b))
        text = FL.canonical(a).decode("ascii")
        self.assertTrue(text.endswith("}\n"))
        self.assertLess(text.index("leyline"), text.index("node-01"))
        self.assertEqual(FL.parse(FL.canonical(a)).as_dict(), good(nodes=[NODE2, NODE1]))      # sorted by name
        self.assertEqual(FL.canonical(good(name="Réseau")).count(b"\\u00e9"), 1)          # ASCII only: nothing a terminal or an editor could change
        FL.parse(FL.canonical(good(name="Réseau")))

    def test_canonical_refuses_what_a_node_would_refuse(self):
        with self.assertRaises(FL.FleetError):
            FL.canonical(good(serial=0))
        with self.assertRaises(FL.FleetError):
            FL.canonical(good(nodes=[NODE1, dict(NODE2, key=NODE1["key"])]))

    def test_the_module_stands_alone(self):
        """tools/boxcert.py loads this file by its path, so it may import nothing from the rest of pvj."""
        path = os.path.join(os.path.dirname(os.path.abspath(FL.__file__)), "fleetlist.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").split(".")[0] in ("pvj", "tests")):
                bad.append(node.module)
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in ("pvj", "tests")]
        self.assertEqual(bad, [])

    def test_the_prefix_and_limits_are_what_the_design_says(self):
        self.assertEqual(FL.PREFIX, b"nxlx-fleet-v1\n")
        self.assertEqual(FL.CARRY, ("full", "live", "none"))
        self.assertEqual((FL.MAX_NODES, FL.MAX_BYTES), (64, 65536))


if __name__ == "__main__":
    unittest.main()
