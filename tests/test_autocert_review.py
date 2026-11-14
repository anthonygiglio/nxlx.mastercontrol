# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The findings of the independent review of D139, one test or more each: addresses pinned like names (H1), no first
root without an expected fingerprint (H2), a pin file that fails closed, atomically and under one lock (M1), pins
seeded for nodes signed by hand and the pin command (M2), `openssl verify` with the owner's root as the only anchor
(M3), serverAuth and clientAuth on the root and the signer (M4), Host, Origin and Content-Type at the service (M5),
refusals charged to the caller only and a long back-off after a taken name (M6), a root move checked against the
expected root before anything (L1), one CN and EC P-256 only (L2), the owner's names and request left alone (L3),
install.sh's checks (L4), the handler threads bounded (L5). Throwaway roots in temporary folders only."""
import http.client
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from pvj import autocert, httpsbox
from tests import test_autocert as ta
from tests.test_boxcert import boxcert, make_request

S = ta.signer_mod
read = ta.read
quiet = ta.quiet
REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


class PinTest(ta.SignerBase):
    def test_h1_an_address_is_pinned_to_its_key_like_a_name(self):
        real, _ = self.csr(["node-01.local"], ["192.168.0.169"])
        self.assertEqual(self.ask(real, host="node-01", caller="192.168.0.169")["names"], ["node-01.local", "192.168.0.169"])
        thief, _ = self.csr(["anything.local"], ["192.168.0.169"])
        with self.assertRaises(S.Refused) as cm:
            self.ask(thief, host="anything", caller="192.168.0.169")       # whoever holds .169 now
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("192.168.0.169", cm.exception.message)
        self.assertNotIn("anything.local", self.signer.pins())
        lines = []
        S.main(["--dir", self.sdir, "--openssl", self.exe, "list"], out=lines.append)
        self.assertTrue(any(l.startswith("192.168.0.169") for l in lines))
        self.assertEqual(S.main(["--dir", self.sdir, "--openssl", self.exe, "release", "192.168.0.169"], out=quiet), 0)
        self.assertEqual(self.ask(thief, host="anything", caller="192.168.0.169")["names"], ["anything.local", "192.168.0.169"])

    def test_m1_a_damaged_or_missing_pin_file_signs_nothing(self):
        text, _ = self.csr(["node-07.local"])
        self.ask(text)
        pins = os.path.join(self.sdir, "signed.json")
        self.assertTrue(os.path.isfile(pins + ".bak"), "the copy before the last change")
        good = read(pins)
        thief, _ = self.csr(["node-07.local"])
        for damage in (good[:len(good) // 2], "[]", '{"node-07.local": "x"}'):
            with open(pins, "w") as f:
                f.write(damage)
            with self.assertRaises(S.Refused) as cm:
                self.ask(thief, caller="192.168.0.%d" % (30 + len(damage) % 50))
            self.assertEqual(cm.exception.status, 503)
            self.assertIn("signed.json.bak", cm.exception.message)
            self.assertEqual(read(pins), damage, "nothing written over it")
        os.unlink(pins)
        with self.assertRaises(S.Refused) as cm:
            self.ask(thief, caller="192.168.0.99")
        self.assertEqual(cm.exception.status, 503)
        with self.assertRaises(S.Refused):
            self.signer.release("node-07.local")
        self.assertEqual(S.main(["--dir", self.sdir, "--openssl", self.exe, "list"], out=quiet), 1)
        with open(pins, "w") as f:
            f.write(good)
        with self.assertRaises(S.Refused) as cm:
            self.ask(thief, caller="192.168.0.98")
        self.assertEqual(cm.exception.status, 409, "restored: the pin holds again")

    def test_m1_one_process_at_a_time(self):
        with boxcert.PinLock(self.sdir):
            started = time.monotonic()
            with self.assertRaises(boxcert.BoxcertError):
                with boxcert.PinLock(self.sdir, wait=0.3):
                    pass
            self.assertLess(time.monotonic() - started, 5)
            p = subprocess.Popen(["python3", os.path.join(REPO, "tools", "nxlx-signer.py"), "--dir", self.sdir, "--openssl", self.exe, "list"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(1.5)
            self.assertIsNone(p.poll(), "another process waits while the lock is held")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, "and reads once it is free: " + out + err)

    def test_m2_the_pin_command(self):
        work = self.workdir()
        key, csr = make_request(self.exe, work, ["node-02.local"], ["192.168.0.174"], cn="node-02.local")
        cert = os.path.join(work, "node-02-cert.pem")
        boxcert.sign(self.exe, self.ca, csr, [], [], [], 397, cert, lambda p: ta.PASS, out=quiet)
        lines = []
        self.assertEqual(S.main(["--dir", self.sdir, "--openssl", self.exe, "pin", "--cert", cert], out=lines.append), 0, lines)
        self.assertEqual(sorted(k for k in self.signer.pins() if k.startswith(("node-02", "192.168.0.174"))), ["192.168.0.174", "node-02.local"])
        thief, _ = self.csr(["node-02.local"])
        with self.assertRaises(S.Refused):
            self.ask(thief, host="node-02", caller="192.168.0.66")
        with open(csr) as f:                                       # the real node, same key: signed
            self.assertEqual(self.ask(f.read(), host="node-02", caller="192.168.0.174")["names"], ["node-02.local", "192.168.0.174"])
        work2 = self.workdir()
        _, csr2 = make_request(self.exe, work2, ["node-02.local"], [], cn="node-02.local")
        cert2 = os.path.join(work2, "c.pem")
        boxcert.sign(self.exe, self.ca, csr2, [], [], [], 397, cert2, lambda p: ta.PASS, out=quiet)
        self.assertEqual(S.main(["--dir", self.sdir, "--openssl", self.exe, "pin", "node-02.local", "--cert", cert2], out=quiet), 1,
                         "pinned to another key: release first")


class SeedTest(ta.Base):
    def test_m2_move_root_pins_the_nodes_signed_by_hand_with_the_old_root(self):
        old = os.path.join(self.tmp, "old")
        ta.make_root(self.exe, old, passphrase=ta.OLD_PASS, pathlen0=True)
        work = self.workdir()
        _, csr = make_request(self.exe, work, ["node-01.local"], ["192.168.0.169"], cn="node-01.local")
        boxcert.sign(self.exe, old, csr, [], [], [], 397, None, lambda p: ta.OLD_PASS, out=quiet)    # writes old/node-01-cert.pem and a row
        sdir = self.make_signer()
        lines = []
        boxcert.move_root(self.exe, old, self.ca, lambda p: ta.OLD_PASS, lines.append)
        self.assertTrue(any("Pinned for the nodes you signed before: node-01.local, 192.168.0.169" in l for l in lines), lines)
        pins = boxcert.read_pins(sdir)
        self.assertEqual(pins["node-01.local"]["key"], boxcert.public_key_hash(self.exe, os.path.join(old, "node-01-cert.pem")))
        self.assertEqual(pins["192.168.0.169"]["key"], pins["node-01.local"]["key"])
        sg = S.Signer(sdir, self.exe)
        _, thief = make_request(self.exe, self.workdir(), ["node-01.local"], [], cn="node-01.local")
        with self.assertRaises(S.Refused) as cm:
            sg.sign({"csr": read(thief), "hostname": "node-01"}, "192.168.0.66")
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(S.Refused):                     # nor the address, by whoever holds it
            sg.sign({"csr": read(self.csr_with_ip("anything", "192.168.0.169")), "hostname": "anything"}, "192.168.0.169")
        sg.sign({"csr": read(csr), "hostname": "node-01"}, "192.168.0.169")       # the node itself, its own key: signed

    def csr_with_ip(self, host, ip):
        return make_request(self.exe, self.workdir(), [host + ".local"], [ip], cn=host + ".local")[1]


class AnchorTest(ta.SignerBase):
    """M3: a certificate from a root the system trusts, but not the owner's, is never "from the owner's root"."""

    def foreign(self):
        other = os.path.join(self.tmp, "other")
        boxcert.make_root(self.exe, other, "Some public authority", ("local",), True, lambda p: ta.PASS, quiet,
                          ranges=tuple(boxcert.PRIVATE_RANGES) + (ta.LOOP,))
        cadir = os.path.join(self.tmp, "cadir")
        os.mkdir(cadir)
        with open(os.path.join(cadir, "o.pem"), "w") as f:
            f.write(read(os.path.join(other, "root.pem")))
        subprocess.run([self.exe, "rehash", cadir], capture_output=True, timeout=30)
        box, _ = self.node_box()
        box.make_request(["testbox.local"])
        rq = os.path.join(self.tmp, "f.csr")
        with open(rq, "w") as f:
            f.write(box.request_pem())
        out = os.path.join(self.tmp, "foreign.pem")
        boxcert.sign(self.exe, other, rq, [], [], [], 397, out, lambda p: ta.PASS, out=quiet)
        leaf_only = os.path.join(self.tmp, "foreign-leaf.pem")
        with open(leaf_only, "w") as f:
            f.write(boxcert.pem_blocks(read(out))[0])
        return box, cadir, leaf_only

    def test_m3_the_systems_ca_folder_is_never_an_anchor(self):
        box, cadir, leaf_only = self.foreign()
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(self.ca, "root.pem")))
        leaf = httpsbox.read_certificate(httpsbox.pem_certificates(read(leaf_only))[0])
        plain = subprocess.run([self.exe, "verify", "-CAfile", os.path.join(self.ca, "root.pem"), "-purpose", "sslserver", leaf_only],
                               capture_output=True, text=True, timeout=30, env=dict(os.environ, SSL_CERT_DIR=cadir))
        with mock.patch.dict(os.environ, {"SSL_CERT_DIR": cadir}):
            self.assertFalse(boxcert.verify(self.exe, self.ca, leaf_only, out=quiet))
            self.assertFalse(box._issued_by_stored_root(read(leaf_only), leaf))
            # the other way of saying it, for an openssl without -no-CApath (LibreSSL): an empty folder as -CApath
            with mock.patch.dict(boxcert._ISOLATE, {self.exe: (False, False)}):
                with tempfile.TemporaryDirectory() as tmp:
                    self.assertEqual(boxcert.verify_args(self.exe, "r.pem", tmp)[3:5], ["-CApath", os.path.join(tmp, "no-ca-folder")])
                self.assertTrue(boxcert.verify(self.exe, self.ca, self.signed_chain(), out=quiet), "the owner's own chain still checks out")
            box._isolate_args = ["-CApath", box.path(httpsbox.NO_CA)]
            self.assertTrue(box._issued_by_stored_root(read(self.signed_chain()), httpsbox.read_certificate(httpsbox.pem_certificates(read(self.signed_chain()))[0])))
        self.assertIn(": OK", plain.stdout, "the setup is right: without the guard openssl would have trusted the folder")

    def signed_chain(self):
        path = os.path.join(self.tmp, "good-chain.pem")
        if not os.path.exists(path):
            text, _ = self.csr(["node-09.local"])
            with open(path, "w") as f:
                f.write(self.ask(text, host="node-09", caller="192.168.0.9")["chain"])
        return path


class ExtendedUsageTest(ta.SignerBase):
    def test_m4_the_root_and_the_signer_say_what_certificates_under_them_are_for(self):
        for path in (os.path.join(self.ca, "root.pem"), os.path.join(self.sdir, "signer.pem")):
            self.assertIn("TLS Web Server Authentication, TLS Web Client Authentication", ta.cert_text(self.exe, path))
        text, _ = self.csr(["node-07.local"])
        chain = os.path.join(self.tmp, "c.pem")
        with open(chain, "w") as f:
            f.write(self.ask(text)["chain"])
        self.assertTrue(boxcert.verify(self.exe, self.ca, chain, out=quiet))


class HttpTest(ta.SignerBase):
    def setUp(self):
        super().setUp()
        self.srv = S.Server(("127.0.0.1", 0), S.make_handler(self.signer, {"yt-1300f.local", "yt-1300f"}))
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    def post(self, body, headers):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", "/v1/sign", skip_host=True)
        data = json.dumps(body).encode()
        for k, v in dict({"Content-Length": str(len(data))}, **headers).items():
            c.putheader(k, v)
        c.endheaders(data)
        r = c.getresponse()
        out = (r.status, json.loads(r.read()))
        c.close()
        return out

    def test_m5_host_origin_and_content_type(self):
        text, _ = self.csr(["testbox.local"])
        body = {"csr": text, "hostname": "testbox"}
        good = {"Host": "127.0.0.1:%d" % self.port, "Content-Type": "application/json"}
        self.assertEqual(self.post(body, dict(good, Origin="http://evil.example"))[0], 403, "a web page's request")
        self.assertEqual(self.post(body, dict(good, Origin="null"))[0], 403)
        self.assertEqual(self.post(body, dict(good, Host="evil.example:%d" % self.port))[0], 421, "DNS rebinding")
        self.assertEqual(self.post(body, dict(good, Host=""))[0], 421)
        self.assertEqual(self.post(body, dict(good, **{"Content-Type": "text/plain"}))[0], 415, "a form post")
        self.assertEqual(self.post(body, {"Host": good["Host"]})[0], 415)
        self.assertEqual(self.post(body, dict(good, Host="YT-1300F.local:8781"))[0], 200, "the configured name")
        self.assertTrue(S.host_ok("[::1]:8781", set()) and S.host_ok("192.168.0.5", set()))
        self.assertFalse(S.host_ok("yt-1300f.local.evil.example", {"yt-1300f.local"}))

    def test_l5_requests_beyond_the_limit_are_closed(self):
        for _ in range(S.MAX_THREADS):
            self.srv.slots.acquire()
        try:
            c = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                c.sendall(b"GET /v1/info HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                got = c.recv(100)
            except ConnectionResetError:
                got = b""
            self.assertEqual(got, b"", "closed without an answer")
            c.close()
        finally:
            for _ in range(S.MAX_THREADS):
                self.srv.slots.release()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/v1/info")
        self.assertEqual(c.getresponse().status, 200)
        c.close()


class LimitTest(ta.SignerBase):
    def test_m6_junk_from_a_few_addresses_does_not_stop_a_real_node(self):
        for a in range(10):
            for _ in range(S.PER_CALLER):
                with self.assertRaises(S.Refused):
                    self.signer.sign({"csr": "junk", "hostname": "x"}, "192.168.0.%d" % (80 + a))
            with self.assertRaises(S.Refused) as cm:
                self.signer.sign({"csr": "junk", "hostname": "x"}, "192.168.0.%d" % (80 + a))
            self.assertEqual(cm.exception.status, 429)
        text, _ = self.csr(["node-04.local"])
        self.assertEqual(self.ask(text, host="node-04", caller="192.168.0.44")["names"], ["node-04.local"])

    def test_m6_signings_still_have_a_ceiling_for_everybody(self):
        with mock.patch.object(S, "IN_ALL", 2):
            for i in range(2):
                text, _ = self.csr(["n%d.local" % i])
                self.ask(text, host="n%d" % i, caller="192.168.1.%d" % i)
            text, _ = self.csr(["n9.local"])
            with self.assertRaises(S.Refused) as cm:
                self.ask(text, host="n9", caller="192.168.1.9")
            self.assertEqual(cm.exception.status, 429)


class RequestShapeTest(ta.SignerBase):
    def raw_csr(self, keyargs, subj, names=("x1.local",)):
        work = self.workdir()
        k = os.path.join(work, "k.pem")
        subprocess.run([self.exe] + keyargs + ["-out", k], check=True, capture_output=True, timeout=60)
        cnf, out = os.path.join(work, "c.cnf"), os.path.join(work, "r.csr")
        with open(cnf, "w") as f:
            f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=%s\n"
                    % (names[0], ",".join("DNS:" + n for n in names)))
        args = [self.exe, "req", "-new", "-key", k, "-config", cnf, "-out", out] + (["-subj", subj] if subj else [])
        subprocess.run(args, check=True, capture_output=True, timeout=60)
        return read(out)

    def test_l2_one_cn_and_an_ec_p256_key(self):
        ec = ["ecparam", "-name", "prime256v1", "-genkey", "-noout"]
        for subj in ("/CN=x1.local/CN=bank.example.com", "/CN=x1.local/O=Owner Inc", "/O=Owner Inc"):
            with self.assertRaises(S.Refused) as cm:
                self.ask(self.raw_csr(ec, subj), host="x1", caller="192.168.0.70")
            self.assertIn("exactly one CN", cm.exception.message)
        for keyargs in (["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:1024"], ["ecparam", "-name", "secp384r1", "-genkey", "-noout"]):
            with self.assertRaises(S.Refused) as cm:
                self.ask(self.raw_csr(keyargs, None), host="x1", caller="192.168.0.71")
            self.assertIn("EC P-256", cm.exception.message)
        self.assertEqual(self.ask(self.raw_csr(ec, None), host="x1", caller="192.168.0.72")["names"], ["x1.local"])


class NodeTest(ta.SignerBase):
    def fp(self, folder=None):
        return boxcert.read_cert(self.exe, os.path.join(folder or self.ca, "root.pem"))["fingerprint"]

    def test_h2_a_node_with_no_root_takes_none_without_the_fingerprint(self):
        fake = ta.FakeSigner(self, self.signer)
        box, settings = self.node_box()
        st = autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": fake.url}).run_once()
        self.assertEqual(st["state"], "needs-fingerprint")
        self.assertIsNone(box.root_info()[0])
        self.assertFalse(os.path.exists(box.path("key.pem")), "nothing made, nothing sent")
        self.assertEqual(self.read_log().count("signed"), 0)
        # told the wrong root: the signer is not even sent a request
        evil = os.path.join(self.tmp, "evil")
        ta.make_root(self.exe, evil)
        st = autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": fake.url, "PVJ_ROOT_FINGERPRINT": self.fp(evil)}).run_once()
        self.assertEqual(st["state"], "refused")
        self.assertIn("nothing was sent", st["message"])
        self.assertIsNone(box.root_info()[0])
        st = autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": fake.url, "PVJ_ROOT_FINGERPRINT": self.fp()}).run_once()
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(box.root_info()[1], self.fp())

    def read_log(self):
        return read(self.signer.log_path) if os.path.exists(self.signer.log_path) else ""

    def test_l1_a_move_to_another_root_than_expected_is_not_applied(self):
        old = os.path.join(self.tmp, "old")
        ta.make_root(self.exe, old, passphrase=ta.OLD_PASS, pathlen0=True)
        boxcert.move_root(self.exe, old, self.ca, lambda p: ta.OLD_PASS, quiet)
        move = self.signer.root_move()
        box, _ = self.node_box()
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(old, "root.pem")))
        with self.assertRaises(httpsbox.HttpsError) as cm:
            box.apply_root_move(move["move"].encode(), move["sig"], expect_to=["ab" * 32])
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(box.root_info()[1], self.fp(old), "nothing written")
        self.assertEqual(box.apply_root_move(move["move"].encode(), move["sig"], expect_to=[self.fp(), None])["new"], self.fp())

    def test_l3_the_owners_names_and_request_are_left_alone(self):
        fake = ta.FakeSigner(self, self.signer)
        box, settings = self.node_box()
        settings.data["https"] = {"names": ["stage.local"]}
        box.make_request(["testbox.local", "stage.local"])            # the owner's own, waiting to be downloaded
        before = box.request_pem()
        settings.data["https"]["names"] = ["stage.local", "panel.local"]
        st = autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": fake.url, "PVJ_ROOT_FINGERPRINT": self.fp()}).run_once()
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(settings.data["https"]["names"], ["stage.local", "panel.local"])
        self.assertEqual(box.request_pem(), before)
        self.assertTrue(os.path.isfile(box.path(httpsbox.AUTO_REQUEST)))


@unittest.skipUnless(os.path.exists("/bin/bash"), "needs bash")
class InstallOptionsTest(unittest.TestCase):
    def run_install(self, *args):
        stage = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, stage, True)
        r = subprocess.run(["bash", os.path.join(REPO, "install", "install.sh"), "--stage", stage] + list(args),
                           capture_output=True, text=True, timeout=300)
        env = os.path.join(stage, "etc", "pvj", "pvj.env")
        return r, (read(env) if os.path.exists(env) else "")

    def test_l4_and_h2_the_signer_options(self):
        fp = "ab" * 32
        for bad in ("http://x;rm -rf /", "http://x\nPVJ_BIND=0.0.0.0", "ftp://x", "http://x:8781/path", "http://$(id)"):
            r, env = self.run_install("--signer", bad, "--root-fingerprint", fp)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("--signer must look like", r.stderr)
        r, _ = self.run_install("--signer", "http://yt-1300f.local:8781")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs --root-fingerprint", r.stderr)
        r, env = self.run_install("--signer", "http://yt-1300f.local:8781", "--root-fingerprint", "AB:" * 31 + "AB")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PVJ_SIGNER_URL=http://yt-1300f.local:8781\n", env)
        self.assertIn("PVJ_ROOT_FINGERPRINT=%s\n" % fp, env)


if __name__ == "__main__":
    unittest.main()
