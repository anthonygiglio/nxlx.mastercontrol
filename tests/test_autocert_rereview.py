# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The second review of D139: an address held by another key is left out, not the whole certificate, and address pins
follow the newest certificate and lapse with it (N1); a replaced signer keeps the live pins (N2); seeding never
overwrites a pin of another key (N4); P-256 judged by the key's own bytes (N5); a pin file that cannot be replaced
for a moment is tried again, then 503 (N6); a fingerprint set at install does not block a later signed root move
(N7); the Windows script names the address the service answers to (N8). Throwaway roots in temporary folders only."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

from pvj import autocert
from tests import test_autocert as ta
from tests.test_boxcert import boxcert, make_request

S = ta.signer_mod
read = ta.read
quiet = ta.quiet
REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DAY = 86400


class AddressTest(ta.SignerBase):
    def test_n1_an_address_held_by_another_key_is_left_out_and_the_names_signed(self):
        a, _ = self.csr(["node-01.local"], ["192.168.0.169"])
        self.ask(a, host="node-01", caller="192.168.0.169")
        b, _ = self.csr(["node-05.local"], ["192.168.0.169"])
        out = self.ask(b, host="node-05", caller="192.168.0.169")         # DHCP gave node-01's old address to node-05
        self.assertEqual((out["names"], out["left_out"]), (["node-05.local"], ["192.168.0.169"]))
        leaf = ta.httpsbox.read_certificate(ta.httpsbox.pem_certificates(out["chain"])[0])
        self.assertEqual((leaf["dns"], leaf["ips"]), (["node-05.local"], []))

    def test_n1_the_newest_certificate_decides_which_addresses_a_key_holds(self):
        text, key = self.csr(["node-01.local"], ["192.168.0.169"])
        self.ask(text, host="node-01", caller="192.168.0.169")
        moved, _ = self.csr(["node-01.local"], ["192.168.0.170"], key_from=key)
        self.ask(moved, host="node-01", caller="192.168.0.170")            # the same node, a new address
        pins = self.signer.pins()
        self.assertNotIn("192.168.0.169", pins, "the old address is free again")
        self.assertEqual(pins["192.168.0.170"]["kind"], "address")
        other, _ = self.csr(["node-05.local"], ["192.168.0.169"])
        self.assertEqual(self.ask(other, host="node-05", caller="192.168.0.169")["names"], ["node-05.local", "192.168.0.169"])

    def test_n1_an_address_pin_lapses_when_its_certificate_ends(self):
        text, _ = self.csr(["node-01.local"], ["192.168.0.169"])
        self.ask(text, host="node-01", caller="192.168.0.169")
        ends = self.signer.pins()["192.168.0.169"]["ends"]
        self.t = ends + DAY                                                 # node-01 is gone; its certificate ran out
        other, _ = self.csr(["node-05.local"], ["192.168.0.169"])
        self.assertEqual(self.ask(other, host="node-05", caller="192.168.0.169")["names"], ["node-05.local", "192.168.0.169"])
        thief, _ = self.csr(["node-01.local"])
        with self.assertRaises(S.Refused) as cm:                            # a name never lapses by itself
            self.ask(thief, host="node-01", caller="192.168.0.66")
        self.assertEqual(cm.exception.status, 409)


class CarryPinsTest(ta.Base):
    def test_n2_a_replaced_signer_keeps_the_pins(self):
        sdir = self.make_signer()
        sg = S.Signer(sdir, self.exe)
        _, csr = make_request(self.exe, self.workdir(), ["node-01.local"], [], cn="node-01.local")
        sg.sign({"csr": read(csr), "hostname": "node-01"}, "192.168.0.169")
        live = os.path.join(self.tmp, "hp-signed.json")
        with open(live, "w") as f:
            f.write(read(os.path.join(sdir, "signed.json")))
        os.rename(sdir, sdir + "-old")
        lines = []
        boxcert.main(["--openssl", self.exe, "--dir", self.ca, "make-signer", "--allow-address", ta.LOOP, "--pins", live],
                     ask=lambda p: ta.PASS, out=lines.append)
        self.assertEqual(boxcert.read_pins(sdir)["node-01.local"], json.loads(read(live))["node-01.local"])
        _, thief = make_request(self.exe, self.workdir(), ["node-01.local"], [], cn="node-01.local")
        with self.assertRaises(S.Refused):
            S.Signer(sdir, self.exe).sign({"csr": read(thief), "hostname": "node-01"}, "192.168.0.66")
        os.rename(sdir, sdir + "-2")
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            f.write("[1, 2]")
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", self.ca, "make-signer", "--pins", bad], ask=lambda p: ta.PASS, out=quiet), 1)
        self.assertFalse(os.path.exists(sdir), "nothing made from a bad pin file")

    def test_n2_n8_the_windows_script(self):
        text = read(os.path.join(REPO, "tools", "signer", "install-windows.ps1"))
        self.assertIn('if ($_.Name -like "signed.json*" -and $livePins) { return }', text, "an existing signed.json is never overwritten")
        self.assertIn("--host-name", text)
        self.assertIn("socket.gethostname() or '').strip().lower().split('.')[0]", text, "the URL from the name the service checks")
        self.assertIn("(socket.gethostname() or \"\").strip().lower().split(\".\")[0]", read(os.path.join(REPO, "tools", "nxlx-signer.py")))


class SeedConflictTest(ta.Base):
    def test_n4_seeding_never_overwrites_another_keys_pin_and_says_so(self):
        old = os.path.join(self.tmp, "old")
        ta.make_root(self.exe, old, passphrase=ta.OLD_PASS, pathlen0=True)
        _, csr = make_request(self.exe, self.workdir(), ["node-01.local"], ["192.168.0.169"], cn="node-01.local")
        boxcert.sign(self.exe, old, csr, [], [], [], 397, None, lambda p: ta.OLD_PASS, out=quiet)
        sdir = self.make_signer()
        _, other = make_request(self.exe, self.workdir(), ["node-01.local"], [], cn="node-01.local")
        S.Signer(sdir, self.exe).sign({"csr": read(other), "hostname": "node-01"}, "192.168.0.50")    # pinned live, another key
        before = boxcert.read_pins(sdir)["node-01.local"]
        lines = []
        boxcert.seed_pins(self.exe, old, sdir, lines.append)
        pins = boxcert.read_pins(sdir)
        self.assertEqual(pins["node-01.local"], before)
        self.assertEqual(pins["192.168.0.169"]["key"], boxcert.public_key_hash(self.exe, os.path.join(old, "node-01-cert.pem")))
        self.assertTrue(any("Left as they were" in l and "node-01.local" in l for l in lines), lines)


class KeyBytesTest(unittest.TestCase):
    def test_n5_p256_by_the_key_bytes(self):
        point = b"\x04" + bytes(64)
        good = boxcert.P256_SPKI_PREFIX + bytes(64)
        self.assertTrue(boxcert.is_p256(good))
        p384 = bytes.fromhex("3076301006072a8648ce3d020106052b81040022036200") + point + bytes(32)
        self.assertFalse(boxcert.is_p256(p384))
        self.assertFalse(boxcert.is_p256(good + b"\x00"), "trailing bytes")
        self.assertFalse(boxcert.is_p256(good[:-1]))
        self.assertFalse(boxcert.is_p256(bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420002") + bytes(64)), "not an uncompressed point")
        self.assertFalse(boxcert.is_p256(b"prime256v1 P-256 id-ecPublicKey" * 3))


@unittest.skipUnless(ta.openssls(), "no openssl on this machine")
class KeyBytesOpensslTest(ta.SignerBase):
    def test_n5_other_curves_are_refused(self):
        for curve in ("secp256k1", "prime256v1"):
            work = self.workdir()
            k, cnf, out = (os.path.join(work, n) for n in ("k.pem", "c.cnf", "r.csr"))
            subprocess.run([self.exe, "ecparam", "-name", curve, "-genkey", "-noout", "-out", k], check=True, capture_output=True, timeout=30)
            with open(cnf, "w") as f:
                f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=x1.local\n[v3_req]\nsubjectAltName=DNS:x1.local\n")
            subprocess.run([self.exe, "req", "-new", "-key", k, "-config", cnf, "-out", out], check=True, capture_output=True, timeout=30)
            if curve == "prime256v1":
                self.assertEqual(self.ask(read(out), host="x1", caller="192.168.0.72")["names"], ["x1.local"])
            else:
                with self.assertRaises(S.Refused) as cm:
                    self.ask(read(out), host="x1", caller="192.168.0.71")
                self.assertIn("EC P-256", cm.exception.message)


class ReplaceTest(ta.SignerBase):
    def test_n6_a_replace_that_fails_for_a_moment_is_tried_again(self):
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(13, "in use")
            return real(src, dst)
        text, _ = self.csr(["node-07.local"])
        with mock.patch.object(boxcert.os, "replace", flaky), mock.patch.object(time, "sleep", lambda s: None):
            self.assertEqual(self.ask(text)["names"], ["node-07.local"])
        self.assertGreaterEqual(len(calls), 3)

    def test_n6_a_replace_that_keeps_failing_is_a_503_and_logged(self):
        def never(src, dst):
            raise PermissionError(13, "in use")
        text, _ = self.csr(["node-07.local"])
        with mock.patch.object(boxcert.os, "replace", never), mock.patch.object(time, "sleep", lambda s: None):
            with self.assertRaises(S.Refused) as cm:
                self.ask(text)
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("pin file could not be saved", read(self.signer.log_path))
        self.assertEqual(self.signer.pins(), {}, "nothing changed")


class MoveAfterPinTest(ta.SignerBase):
    def test_n7_a_fingerprint_set_at_install_does_not_block_a_signed_move(self):
        old = os.path.join(self.tmp, "old")
        ta.make_root(self.exe, old, passphrase=ta.OLD_PASS, pathlen0=True)
        old_fp = boxcert.read_cert(self.exe, os.path.join(old, "root.pem"))["fingerprint"]
        box, settings = self.node_box()
        box.make_request(["testbox.local"])
        rq = os.path.join(self.tmp, "n.csr")
        with open(rq, "w") as f:
            f.write(box.request_pem())
        out = os.path.join(self.tmp, "manual.pem")
        boxcert.sign(self.exe, old, rq, [], [], [], 397, out, lambda p: ta.OLD_PASS, out=quiet)
        box.install(read(out), None)                                       # pinned the old root, as installed with its fingerprint
        boxcert.move_root(self.exe, old, self.ca, lambda p: ta.OLD_PASS, quiet)
        fake = ta.FakeSigner(self, self.signer)
        st = autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": fake.url, "PVJ_ROOT_FINGERPRINT": old_fp}).run_once()
        self.assertEqual(st["state"], "done", st)
        self.assertIn("Moved to the new root", st["message"])
        self.assertEqual(box.root_info()[1], boxcert.read_cert(self.exe, os.path.join(self.ca, "root.pem"))["fingerprint"])


if __name__ == "__main__":
    unittest.main()
