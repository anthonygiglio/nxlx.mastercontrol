# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""tools/boxcert.py end to end with the real openssl (D79): the root, a box's request, signing, the chain, the names,
the validity, the extended key usage, a wrong passphrase, no overwrite, the list. Skipped where there is no openssl;
run once per openssl found (Homebrew's OpenSSL and the system's LibreSSL on a Mac)."""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "boxcert.py")
spec = importlib.util.spec_from_file_location("boxcert", TOOL)
boxcert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boxcert)

PASS = "correct horse battery"


def openssls():
    found = []
    for exe in (shutil.which("openssl"), "/usr/bin/openssl"):
        if exe and os.path.isfile(exe) and os.path.realpath(exe) not in [os.path.realpath(f) for f in found]:
            try:
                if subprocess.run([exe, "version"], capture_output=True, timeout=10).returncode == 0:
                    found.append(exe)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return found


def make_request(exe, folder, names, ips, cn="nxlx-mastercontrol.local", extra=""):
    """What the box does (pvj/httpsbox.py makes the same request its own way). `extra`: more lines a hostile request
    could carry in its extensions."""
    key, cnf, csr = (os.path.join(folder, n) for n in ("box.key", "req.cnf", "box.csr"))
    subprocess.run([exe, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True, capture_output=True)
    san = ",".join(["DNS:%s" % n for n in names] + ["IP:%s" % i for i in ips])
    with open(cnf, "w") as f:
        f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=%s\n%s" % (cn, san, extra))
    subprocess.run([exe, "req", "-new", "-key", key, "-config", cnf, "-out", csr], check=True, capture_output=True)
    return key, csr


@unittest.skipUnless(openssls(), "no openssl on this machine")
class BoxcertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lines = []

    def run_tool(self, exe, argv, passphrase=PASS):
        self.lines = []
        return boxcert.main(["--openssl", exe] + argv, ask=lambda prompt: passphrase, out=self.lines.append)

    def text(self, exe, args):
        return subprocess.run([exe] + args, capture_output=True, text=True).stdout

    def test_the_whole_way_with_each_openssl(self):
        for exe in openssls():
            with self.subTest(openssl=exe):
                self.whole_way(exe, os.path.join(self.tmp, os.path.basename(os.path.dirname(os.path.dirname(exe))) or "x"))

    def whole_way(self, exe, folder):
        ca = os.path.join(folder, "ca")
        os.makedirs(folder)
        # the root: made once, encrypted, refuses to overwrite
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "make-root"]), 0, self.lines)
        with open(os.path.join(ca, "root.key")) as f:
            self.assertIn("ENCRYPTED PRIVATE KEY", f.read())
        self.assertEqual(oct(os.stat(os.path.join(ca, "root.key")).st_mode & 0o777), "0o600")
        before = open(os.path.join(ca, "root.pem")).read()
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "make-root"]), 1)
        self.assertEqual(open(os.path.join(ca, "root.pem")).read(), before)
        root_text = self.text(exe, ["x509", "-in", os.path.join(ca, "root.pem"), "-noout", "-text"])
        self.assertIn("CA:TRUE", root_text)
        self.assertIn("Name Constraints", root_text)
        self.assertIn("DNS:local", root_text)
        # a passphrase typed twice differently makes nothing
        answers = iter(["one passphrase", "another one"])
        self.assertEqual(boxcert.main(["--openssl", exe, "--dir", ca + "2", "make-root"], ask=lambda p: next(answers), out=self.lines.append), 1)
        self.assertFalse(os.path.exists(os.path.join(ca + "2", "root.key")))

        # the box's request, signed: the names, a bare host name and a public address left out by the constraints
        key, csr = make_request(exe, folder, ["nxlx-mastercontrol.local", "nxlx-mastercontrol"], ["192.168.0.169"])
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "sign", csr, "--address", "8.8.8.8", "--name", "Studio.Local"]), 0, self.lines)
        self.assertTrue(any("Left out: nxlx-mastercontrol, 8.8.8.8" in l for l in self.lines), self.lines)
        cert = os.path.join(ca, "nxlx-mastercontrol-cert.pem")
        pem = open(cert).read()
        self.assertEqual(pem.count("BEGIN CERTIFICATE"), 2, "the box's certificate followed by the root")
        text = self.text(exe, ["x509", "-in", cert, "-noout", "-text"])
        self.assertIn("DNS:nxlx-mastercontrol.local, DNS:studio.local, IP Address:192.168.0.169", text)
        self.assertNotIn("8.8.8.8", text)
        self.assertIn("TLS Web Server Authentication", text)
        self.assertIn("CA:FALSE", text)
        self.assertIn("ecdsa-with-SHA256", text)
        info = boxcert.read_cert(exe, cert)
        self.assertEqual((info["end"] - info["start"]).days, 397)
        self.assertEqual(len(info["serial"]), 32)
        # the chain verifies with openssl itself, for a server
        r = subprocess.run([exe, "verify", "-CAfile", os.path.join(ca, "root.pem"), "-purpose", "sslserver", cert], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "verify", cert]), 0, self.lines)
        # the certificate is for the request's key (the public keys agree)
        pub_key = self.text(exe, ["pkey", "-in", key, "-pubout"])
        pub_cert = self.text(exe, ["x509", "-in", cert, "-noout", "-pubkey"])
        self.assertEqual(pub_key.strip(), pub_cert.strip())

        # a wrong passphrase signs nothing and says so
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "sign", csr, "--out", os.path.join(folder, "no.pem")], passphrase="not it"), 1)
        self.assertFalse(os.path.exists(os.path.join(folder, "no.pem")))
        # too many days is refused before anything is asked
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "sign", csr, "--days", "826"]), 1)
        # a name outside the constraints is refused by openssl: the proof the constraints bite
        bad_ca = os.path.join(folder, "bad")
        self.assertEqual(self.run_tool(exe, ["--dir", bad_ca, "make-root", "--no-constraints"]), 0, self.lines)
        self.assertEqual(self.run_tool(exe, ["--dir", bad_ca, "sign", csr, "--name", "bank.example", "--out", os.path.join(folder, "wide.pem")]), 0, self.lines)
        r = subprocess.run([exe, "verify", "-CAfile", os.path.join(ca, "root.pem"), os.path.join(folder, "wide.pem")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "a certificate from another root must not verify against this one")

        # a hostile request asking to be a CA, with any key usage: the tool writes its own extensions and copies none
        hostile = os.path.join(folder, "hostile")
        os.makedirs(hostile)
        _, bad_csr = make_request(exe, hostile, ["evil.local"], [], cn="evil.local",
                                  extra="basicConstraints=critical,CA:TRUE\nkeyUsage=keyCertSign,cRLSign\nextendedKeyUsage=clientAuth,codeSigning\n")
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "sign", bad_csr, "--out", os.path.join(folder, "hostile.pem")]), 0, self.lines)
        htext = self.text(exe, ["x509", "-in", os.path.join(folder, "hostile.pem"), "-noout", "-text"])
        self.assertIn("CA:FALSE", htext)
        self.assertNotIn("CA:TRUE", htext)
        self.assertNotIn("Certificate Sign", htext)
        self.assertNotIn("Code Signing", htext)
        self.assertEqual(htext.count("Subject Alternative Name"), 1)
        self.assertEqual(oct(os.stat(os.path.join(ca, "root.key")).st_mode & 0o777), "0o600")
        # show, list: what was signed and when it ends
        self.assertEqual(self.run_tool(exe, ["show", cert]), 0)
        self.assertTrue(any("ends on : %s" % info["end"].date().isoformat() in l for l in self.lines), self.lines)
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "list"]), 0)
        self.assertTrue(any(l.startswith("nxlx-mastercontrol") and "396 days left" in l or "397 days left" in l for l in self.lines), self.lines)
        with open(os.path.join(ca, "signed.json")) as f:
            rows = json.load(f)
        self.assertEqual([r["serial"] for r in rows][0], info["serial"])
        self.assertEqual(len(rows), 2, "the box and the hostile request")
        self.assertEqual(rows[0]["names"], ["nxlx-mastercontrol.local", "studio.local"])
        # the root itself shown: named as the root, so it is not uploaded as a box's certificate by mistake; its
        # fingerprint is what `root` prints and what the box's page shows (groups of four, SHA-256)
        self.assertEqual(self.run_tool(exe, ["show", os.path.join(ca, "root.pem")]), 0)
        self.assertTrue(any("ROOT certificate" in l for l in self.lines))
        shown = [l for l in self.lines if l.startswith("  SHA-256 : ")][0].split(": ", 1)[1]
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "root"]), 0)
        self.assertTrue(any(l == "SHA-256 : " + shown for l in self.lines), self.lines)
        import hashlib, base64, re as re_
        der = base64.b64decode(re_.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END", open(os.path.join(ca, "root.pem")).read(), re_.S).group(1).replace("\n", ""))
        self.assertEqual(shown.replace(" ", ""), hashlib.sha256(der).hexdigest())
        # renew is sign again with the same request: a second certificate for the same key, a new serial
        self.assertEqual(self.run_tool(exe, ["--dir", ca, "renew", csr, "--days", "30"]), 0, self.lines)
        again = boxcert.read_cert(exe, cert)
        self.assertNotEqual(again["serial"], info["serial"])
        self.assertEqual((again["end"] - again["start"]).days, 30)
        with open(os.path.join(ca, "signed.json")) as f:
            rows = json.load(f)
        self.assertEqual(len(rows), 3)
        # the list says what is due
        self.run_tool(exe, ["--dir", ca, "list"])
        self.assertTrue(any("due in" in l for l in self.lines), self.lines)

    def test_no_openssl_is_a_plain_message(self):
        with self.assertRaises(boxcert.BoxcertError):
            boxcert.openssl_bin(os.path.join(self.tmp, "nope"))

    def test_the_san_line_is_read_as_both_libraries_print_it(self):
        self.assertEqual(boxcert.names_in("    X509v3 Subject Alternative Name: \n        DNS:a.local, DNS:B.local, IP Address:10.0.0.2\n"),
                         (["a.local", "b.local"], ["10.0.0.2"]))
        self.assertEqual(boxcert.names_in("nothing here"), ([], []))

    def test_time_lines_are_read(self):
        t = boxcert.parse_time("notAfter=Nov 11 10:06:33 2027 GMT")
        self.assertEqual(t, datetime.datetime(2027, 11, 11, 10, 6, 33, tzinfo=datetime.timezone.utc))


if __name__ == "__main__":
    unittest.main()
