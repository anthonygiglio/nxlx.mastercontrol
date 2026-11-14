# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Automatic certificates (D139): boxcert make-signer and move-root, the signing service's rules (constraints, names,
pins, the rate limit, the log), a node certificate that chains through the signer (install, the peer's handshake,
boxcert verify), the root move, and the node's hook against a real signing service on loopback. Throwaway roots in
temporary folders only. Needs openssl; skipped cleanly without."""
import http.server
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from pvj import autocert, httpsbox
from pvj.settings import Settings
from tests.test_boxcert import boxcert, make_request, openssls

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nxlx_signer", os.path.join(HERE, os.pardir, "tools", "nxlx-signer.py"))
signer_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signer_mod)

PASS = "a throwaway passphrase"
OLD_PASS = "the old throwaway passphrase"
LOOP = "127.0.0.0/255.0.0.0"
quiet = lambda *a: None  # noqa: E731


def make_root(exe, folder, passphrase=PASS, pathlen0=False):
    orig = boxcert.root_config
    cfg = (lambda *a, **k: orig(*a, **k).replace("pathlen:1", "pathlen:0")) if pathlen0 else orig
    with mock.patch.object(boxcert, "root_config", cfg):
        boxcert.make_root(exe, folder, "Test root", ("local",), True, lambda p: passphrase, quiet, ranges=tuple(boxcert.PRIVATE_RANGES) + (LOOP,))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def cert_text(exe, path):
    return subprocess.run([exe, "x509", "-in", path, "-noout", "-text"], capture_output=True, text=True, timeout=30).stdout


@unittest.skipUnless(openssls(), "no openssl on this machine")
class Base(unittest.TestCase):
    def setUp(self):
        self.exe = openssls()[0]
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ca = os.path.join(self.tmp, "ca")
        make_root(self.exe, self.ca)

    def make_signer(self, folder=None, passphrase=PASS):
        return boxcert.make_signer(self.exe, folder or self.ca, lambda p: passphrase, quiet, ranges=tuple(boxcert.SIGNER_RANGES) + (LOOP,))

    def workdir(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        return d

    def node_box(self, hostname="testbox", addresses=("127.0.0.1", "192.168.0.50"), now=time.time):
        state = self.workdir()
        settings = Settings(os.path.join(state, "settings.json"))
        settings.load()
        box = httpsbox.HttpsBox(os.path.join(state, "tls"), settings, openssl=self.exe, hostname=hostname,
                                addresses=lambda: list(addresses), clock_trusted=lambda: True, now=now)
        return box, settings


class MakeSignerTest(Base):
    def test_a_new_root_allows_one_signer_and_the_signer_is_constrained(self):
        self.assertIn("pathlen:1", cert_text(self.exe, os.path.join(self.ca, "root.pem")))
        sdir = self.make_signer()
        self.assertEqual(sorted(os.listdir(sdir)), ["root.pem", "signed.json", "signer.key", "signer.pem"])
        self.assertEqual(os.stat(os.path.join(sdir, "signer.key")).st_mode & 0o777, 0o600)
        self.assertNotIn("ENCRYPTED", read(os.path.join(sdir, "signer.key")), "the service signs without a person")
        text = cert_text(self.exe, os.path.join(sdir, "signer.pem"))
        self.assertIn("NXLX node signer", text)
        self.assertRegex(text, r"CA:TRUE, pathlen:0")
        self.assertIn("Certificate Sign", text)
        self.assertIn("Name Constraints: critical", text)
        for allowed in ("DNS:local", "IP:10.0.0.0/255.0.0.0", "IP:172.16.0.0/255.240.0.0", "IP:192.168.0.0/255.255.0.0"):
            self.assertIn(allowed, text)
        self.assertNotIn("169.254", text)
        r = subprocess.run([self.exe, "verify", "-CAfile", os.path.join(self.ca, "root.pem"), os.path.join(sdir, "signer.pem")], capture_output=True, text=True, timeout=30)
        self.assertIn(": OK", r.stdout)
        self.assertTrue(any(row.get("box") == "signer" for row in json.loads(read(os.path.join(self.ca, "signed.json")))))

    def test_it_refuses_to_overwrite_a_signer_a_wrong_passphrase_and_an_old_root(self):
        self.make_signer()
        before = read(os.path.join(self.ca, "signer", "signer.key"))
        with self.assertRaises(boxcert.BoxcertError) as cm:
            self.make_signer()
        self.assertIn("exists already", str(cm.exception))
        self.assertEqual(read(os.path.join(self.ca, "signer", "signer.key")), before)
        other = os.path.join(self.tmp, "ca2")
        make_root(self.exe, other)
        with self.assertRaises(boxcert.BoxcertError) as cm:
            self.make_signer(other, passphrase="not the passphrase")
        self.assertIn("wrong passphrase", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(other, "signer")))
        old = os.path.join(self.tmp, "old")
        make_root(self.exe, old, pathlen0=True)
        with self.assertRaises(boxcert.BoxcertError) as cm:
            self.make_signer(old)
        self.assertIn("pathlen:0", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(old, "signer")))

    def test_the_command_line(self):
        ca = os.path.join(self.tmp, "cli")
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", ca, "make-root"], ask=lambda p: PASS, out=quiet), 0)
        lines = []
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", ca, "make-signer"], ask=lambda p: PASS, out=lines.append), 0)
        self.assertTrue(any("copy the whole folder" in l for l in lines))
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", ca, "make-signer"], ask=lambda p: PASS, out=quiet), 1)


class SignerBase(Base):
    def setUp(self):
        super().setUp()
        self.sdir = self.make_signer()
        self.t = time.time()
        self.signer = signer_mod.Signer(self.sdir, self.exe, now=lambda: self.t)

    def csr(self, names, ips=(), cn=None, key_from=None):
        work = self.workdir()
        if key_from:
            key = key_from
            cnf, csr = os.path.join(work, "req.cnf"), os.path.join(work, "box.csr")
            san = ",".join(["DNS:%s" % n for n in names] + ["IP:%s" % i for i in ips])
            with open(cnf, "w") as f:
                f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=%s\n" % (cn or names[0], san))
            subprocess.run([self.exe, "req", "-new", "-key", key, "-config", cnf, "-out", csr], check=True, capture_output=True, timeout=30)
        else:
            key, csr = make_request(self.exe, work, list(names), list(ips), cn=cn or names[0])
        with open(csr) as f:
            return f.read(), key

    def ask(self, text, host="node-07", caller="192.168.0.20"):
        return self.signer.sign({"csr": text, "hostname": host}, caller)


class ServiceRulesTest(SignerBase):
    def test_a_good_request_gets_the_chain_that_ends_at_the_root(self):
        text, _ = self.csr(["node-07.local"], ["192.168.0.20", "192.168.0.99"])
        out = self.ask(text)
        self.assertEqual(out["names"], ["node-07.local", "192.168.0.20"], "the caller's own address only")
        self.assertEqual(out["left_out"], ["192.168.0.99"])
        blocks = httpsbox.pem_blocks(out["chain"])
        self.assertEqual(len(blocks), 3)
        leaf, signer, root = (httpsbox.read_certificate(d) for d, _ in blocks)
        self.assertEqual((leaf["ca"], signer["ca"], signer["self_issued"], root["self_issued"]), (False, True, False, True))
        self.assertTrue(leaf["server_auth"] and leaf["client_auth"])
        self.assertLessEqual(leaf["not_after"] - leaf["not_before"], 397 * 86400 + 60)
        path = os.path.join(self.tmp, "chain.pem")
        with open(path, "w") as f:
            f.write(out["chain"])
        lines = []
        self.assertTrue(boxcert.verify(self.exe, self.ca, path, out=lines.append), lines)
        self.assertIn("signed for node-07.local", read(self.signer.log_path))

    def test_wrong_names_are_refused_and_logged(self):
        cases = [((["node-07.example.com", "node-07.local"],), "only .local names"),
                 ((["other.local"],), "must name the node's own node-07.local"),
                 ((["node-07.local"], (), "evil.example.com"), "subject"),
                 ((["*.local", "node-07.local"],), "only .local names")]
        for args, words in cases:
            text, _ = self.csr(*args)
            with self.assertRaises(signer_mod.Refused) as cm:
                self.ask(text)
            self.assertIn(words, cm.exception.message)
        with self.assertRaises(signer_mod.Refused):
            self.ask("-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----\n")
        with self.assertRaises(signer_mod.Refused):
            self.ask(self.csr(["node-07.local"])[0], host="bad name")
        self.assertEqual(read(self.signer.log_path).count("refused"), 6)
        self.assertEqual(self.signer.pins(), {})

    def test_a_public_caller_is_refused(self):
        text, _ = self.csr(["node-07.local"])
        with self.assertRaises(signer_mod.Refused) as cm:
            self.ask(text, caller="8.8.8.8")
        self.assertEqual(cm.exception.status, 403)

    def test_first_come_pins_a_name_to_its_key_and_release_frees_it(self):
        text, key = self.csr(["node-07.local"])
        self.ask(text)
        again, _ = self.csr(["node-07.local"], key_from=key)
        self.ask(again)                                          # the same key: a renewal
        thief, _ = self.csr(["node-07.local"])
        with self.assertRaises(signer_mod.Refused) as cm:
            self.ask(thief)
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("nxlx-signer.py release node-07.local", cm.exception.message)
        lines = []
        self.assertEqual(signer_mod.main(["--dir", self.sdir, "--openssl", self.exe, "release", "node-07.local"], out=lines.append), 0)
        self.ask(thief)                                          # released: the new key takes the name
        self.assertEqual(signer_mod.main(["--dir", self.sdir, "--openssl", self.exe, "release", "nobody.local"], out=lines.append), 1)

    def test_the_rate_limit(self):
        text, key = self.csr(["node-07.local"])
        self.ask(text)
        for _ in range(signer_mod.PER_CALLER - 1):
            self.ask(self.csr(["node-07.local"], key_from=key)[0])
        with self.assertRaises(signer_mod.Refused) as cm:
            self.ask(self.csr(["node-07.local"], key_from=key)[0])
        self.assertEqual(cm.exception.status, 429)
        other, _ = self.csr(["node-08.local"])
        self.ask(other, host="node-08", caller="192.168.0.21")   # another caller is not held up by this one
        self.t += signer_mod.WINDOW + 1
        self.ask(self.csr(["node-07.local"], key_from=key)[0])


class ChainOnTheNodeTest(SignerBase):
    def signed_for_box(self, box):
        box.make_request(["testbox.local"])
        return self.signer.sign({"csr": box.request_pem(), "hostname": "testbox"}, "127.0.0.1")["chain"]

    def test_a_node_with_no_root_pins_the_signers_root_and_serves_the_whole_path(self):
        box, _ = self.node_box()
        chain = self.signed_for_box(box)
        root_fp = httpsbox.fingerprint(httpsbox.pem_certificates(read(os.path.join(self.ca, "root.pem")))[0])
        leaf = box.install_from_signer(chain, root_fp)
        self.assertEqual(leaf["dns"], ["testbox.local"])
        self.assertEqual(box.root_info()[1], root_fp)
        self.assertTrue(box.load())
        self.assertIs(box.chained, True)
        self.assertEqual(len(httpsbox.pem_blocks(box.cert_pem())), 2, "the node's certificate and the signer; the root is pinned apart")
        # a device that trusts only the root accepts the node over TLS: the node sends the signer too
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(10)

        def serve():
            try:
                c, _ = srv.accept()
                box.context.wrap_socket(c, server_side=True).close()
            except (OSError, ssl.SSLError):
                pass
        threading.Thread(target=serve, daemon=True).start()
        cli = ssl.create_default_context(cafile=os.path.join(self.ca, "root.pem"))
        s = cli.wrap_socket(socket.create_connection(srv.getsockname(), timeout=10), server_hostname="testbox.local")
        s.close()
        srv.close()
        # a peer's view: the node's certificate alone does not reach the root, with the signer it does
        der = httpsbox.pem_certificates(box.cert_pem())
        self.assertEqual(box.chains_to_root(der[0])[0], False)
        self.assertEqual(box.chains_to_root(der[0], der[1:])[0], True)
        self.assertIsNotNone(box.peer_client_context())

    def test_the_expected_root_and_the_signers_word_must_hold_on_first_pin(self):
        box, _ = self.node_box()
        chain = self.signed_for_box(box)
        root_fp = httpsbox.fingerprint(httpsbox.pem_certificates(read(os.path.join(self.ca, "root.pem")))[0])
        with self.assertRaises(httpsbox.HttpsError) as cm:
            box.install_from_signer(chain, root_fp, want_root="ab" * 32)
        self.assertIn("set up to trust", cm.exception.message)
        with self.assertRaises(httpsbox.HttpsError):
            box.install_from_signer(chain, "cd" * 32)
        self.assertIsNone(box.root_info()[0], "nothing pinned")
        self.assertEqual(box.install_from_signer(chain, root_fp, want_root=root_fp)["dns"], ["testbox.local"])

    def test_a_pinned_other_root_refuses_the_signers_certificate(self):
        box, _ = self.node_box()
        other = os.path.join(self.tmp, "other")
        make_root(self.exe, other)
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(other, "root.pem")))
        chain = self.signed_for_box(box)
        with self.assertRaises(httpsbox.HttpsError) as cm:
            box.install_from_signer(chain, "00" * 32)
        self.assertIn("not issued by the root this node knows", cm.exception.message)
        self.assertIsNone(box.context)

    def test_the_manual_upload_takes_a_file_from_the_signer_too(self):
        box, _ = self.node_box()
        chain = self.signed_for_box(box)
        box.install(chain, "testbox.local")
        self.assertEqual(box.root_info()[0]["subject"], "Test root", "the root is pinned, never the signer")
        self.assertTrue(box.load())
        self.assertIs(box.chained, True)

    def test_an_old_root_with_pathlen0_cannot_vouch_for_a_signer(self):
        """Why D139 needs a new root: what a signer under a pathlen:0 root signs is refused."""
        old = os.path.join(self.tmp, "old")
        make_root(self.exe, old, pathlen0=True)
        box, _ = self.node_box()
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(old, "root.pem")))
        # a signer made under the old root by hand, as make-signer refuses to
        work = self.workdir()
        key, csr = make_request(self.exe, work, ["x.local"], [], cn="signer")
        cfg = os.path.join(work, "s.cnf")
        with open(cfg, "w") as f:
            f.write(boxcert.signer_config(ranges=tuple(boxcert.SIGNER_RANGES) + (LOOP,)))
        spem = os.path.join(work, "s.pem")
        subprocess.run([self.exe, "x509", "-req", "-in", csr, "-CA", os.path.join(old, "root.pem"), "-CAkey", os.path.join(old, "root.key"), "-passin", "stdin",
                        "-set_serial", "0x1234", "-days", "30", "-sha256", "-extfile", cfg, "-extensions", "v3_signer", "-out", spem],
                       input=PASS + "\n", check=True, capture_output=True, text=True, timeout=30)
        box.make_request(["testbox.local"])
        rq = os.path.join(work, "n.csr")
        with open(rq, "w") as f:
            f.write(box.request_pem())
        lpem = os.path.join(work, "l.pem")
        lcfg = os.path.join(work, "l.cnf")
        with open(lcfg, "w") as f:
            f.write(boxcert.leaf_config(["testbox.local"], []))
        subprocess.run([self.exe, "x509", "-req", "-in", rq, "-CA", spem, "-CAkey", key, "-set_serial", "0x5678", "-days", "30", "-sha256",
                        "-extfile", lcfg, "-extensions", "v3_leaf", "-out", lpem], check=True, capture_output=True, timeout=30)
        with self.assertRaises(httpsbox.HttpsError):
            box.install(read(lpem) + read(spem), None)


class RootMoveTest(SignerBase):
    def setUp(self):
        super().setUp()
        self.old = os.path.join(self.tmp, "old")
        make_root(self.exe, self.old, passphrase=OLD_PASS, pathlen0=True)    # like the owner's root of 2026-10-13

    def move(self):
        return boxcert.move_root(self.exe, self.old, self.ca, lambda p: OLD_PASS, quiet)

    def pinned_box(self, root_dir):
        box, settings = self.node_box()
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(root_dir, "root.pem")))
        return box, settings

    def test_a_node_takes_the_new_root_when_the_old_one_says_so_once(self):
        self.move()
        move = self.signer.root_move()
        box, _ = self.pinned_box(self.old)
        out = box.apply_root_move(move["move"].encode(), move["sig"])
        new_fp = boxcert.read_cert(self.exe, os.path.join(self.ca, "root.pem"))["fingerprint"]
        self.assertEqual(out["new"], new_fp)
        self.assertEqual(box.root_info()[1], new_fp)
        with self.assertRaises(httpsbox.HttpsError) as cm:          # replayed: it names a root this node no longer pins
            box.apply_root_move(move["move"].encode(), move["sig"])
        self.assertEqual(cm.exception.status, 409)

    def test_a_move_not_signed_by_the_pinned_root_is_refused(self):
        self.move()
        move = self.signer.root_move()
        stranger = os.path.join(self.tmp, "stranger")
        make_root(self.exe, stranger)
        box, _ = self.pinned_box(stranger)
        with self.assertRaises(httpsbox.HttpsError) as cm:
            box.apply_root_move(move["move"].encode(), move["sig"])
        self.assertIn("for another root", cm.exception.message)
        # the same move with "from" changed to the stranger's root: the signature no longer holds
        body = json.loads(move["move"])
        body["from"] = box.root_info()[1].replace(" ", "")
        forged = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(httpsbox.HttpsError) as cm:
            box.apply_root_move(forged, move["sig"])
        self.assertEqual(cm.exception.status, 403)
        box2, _ = self.pinned_box(self.old)
        body = json.loads(move["move"])
        body["root"] = read(os.path.join(stranger, "root.pem"))   # a swapped root under a good "to"
        with self.assertRaises(httpsbox.HttpsError):
            box2.apply_root_move(json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), move["sig"])
        fresh, _ = self.node_box()
        with self.assertRaises(httpsbox.HttpsError) as cm:
            fresh.apply_root_move(move["move"].encode(), move["sig"])
        self.assertIn("no root yet", cm.exception.message)

    def test_move_root_needs_the_signer_and_the_right_passphrase(self):
        bare = os.path.join(self.tmp, "bare")
        make_root(self.exe, bare)
        with self.assertRaises(boxcert.BoxcertError) as cm:
            boxcert.move_root(self.exe, self.old, bare, lambda p: OLD_PASS, quiet)
        self.assertIn("make the signer first", str(cm.exception))
        with self.assertRaises(boxcert.BoxcertError):
            boxcert.move_root(self.exe, self.old, self.ca, lambda p: PASS, quiet)
        self.assertIsNone(self.signer.root_move())
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", self.old, "move-root", "--to", self.ca], ask=lambda p: OLD_PASS, out=quiet), 0)
        self.assertIsNotNone(self.signer.root_move())


class FakeSigner:
    """The real signing service on loopback, as a node finds it."""

    def __init__(self, case, signer):
        self.srv = signer_mod.Server(("127.0.0.1", 0), signer_mod.make_handler(signer))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        case.addCleanup(self.srv.server_close)
        case.addCleanup(self.srv.shutdown)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]


class HookTest(SignerBase):
    def auto(self, box, settings, url, now=time.time):
        return autocert.AutoCert(box, settings, env={"PVJ_SIGNER_URL": url} if url else {}, now=now)

    def test_a_new_node_gets_its_certificate_and_then_leaves_the_signer_alone(self):
        fake = FakeSigner(self, self.signer)
        box, settings = self.node_box()
        a = self.auto(box, settings, fake.url)
        st = a.run_once()
        self.assertEqual(st["state"], "done", st)
        self.assertIsNotNone(box.context)
        self.assertEqual(box.info["dns"], ["testbox.local"])
        self.assertEqual(box.info["ips"], [], "192.168.0.50 is not the address the request came from; 127.0.0.1 is never asked for")
        self.assertIs(box.chained, True)
        self.assertEqual(a.run_once()["state"], "good")
        self.assertEqual(box.status(False, "testbox.local")["auto"]["state"], "good")
        # 30 days before the end it asks again, with the same key
        key = read(box.path("key.pem"))
        later = self.auto(box, settings, fake.url, now=lambda: box.info["not_after"] - 10 * 86400)
        self.assertEqual(later.run_once()["state"], "done")
        self.assertEqual(read(box.path("key.pem")), key)

    def test_no_signer_set_and_a_silent_signer_change_nothing(self):
        box, settings = self.node_box()
        self.assertEqual(self.auto(box, settings, None).run_once()["state"], "off")
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()
        started = time.time()
        st = self.auto(box, settings, "http://127.0.0.1:%d" % port).run_once()
        self.assertLess(time.time() - started, autocert.TIMEOUT + 2)
        self.assertEqual(st["state"], "waiting")
        self.assertIn("Waiting for the signer", st["words"])
        self.assertIsNone(box.context)
        self.assertEqual(json.loads(read(box.path("auto.json")))["state"], "waiting", "the page reads it after a restart")
        settings.data["https"] = {"signer_url": "not a url"}
        self.assertEqual(self.auto(box, settings, None).run_once()["state"], "off")

    def test_a_node_on_the_old_root_moves_and_renews_by_itself(self):
        old = os.path.join(self.tmp, "old")
        make_root(self.exe, old, passphrase=OLD_PASS, pathlen0=True)
        box, settings = self.node_box()
        box.make_request(["testbox.local"])
        csr = os.path.join(self.tmp, "n.csr")
        with open(csr, "w") as f:
            f.write(box.request_pem())
        out = os.path.join(self.tmp, "manual.pem")
        boxcert.sign(self.exe, old, csr, [], [], [], 397, out, lambda p: OLD_PASS, out=quiet)
        box.install(read(out), None)                     # signed by hand, the way node-01 and node-02 were
        fake = FakeSigner(self, self.signer)
        st = self.auto(box, settings, fake.url).run_once()
        self.assertEqual(st["state"], "refused", "no move written yet: the node keeps its root and says why")
        self.assertIn("no move", st["message"])
        boxcert.move_root(self.exe, old, self.ca, lambda p: OLD_PASS, quiet)
        st = self.auto(box, settings, fake.url).run_once()
        self.assertEqual(st["state"], "done", st)
        self.assertIn("Moved to the new root", st["message"])
        self.assertEqual(box.root_info()[1], boxcert.read_cert(self.exe, os.path.join(self.ca, "root.pem"))["fingerprint"])
        self.assertIs(box.chained, True)

    def test_a_signer_for_another_root_is_refused_by_a_pinned_node(self):
        other = os.path.join(self.tmp, "other")
        make_root(self.exe, other)
        box, settings = self.node_box()
        box._ensure_folder()
        box._write("root.pem", read(os.path.join(other, "root.pem")))
        fake = FakeSigner(self, self.signer)
        st = self.auto(box, settings, fake.url).run_once()
        self.assertEqual(st["state"], "refused")
        self.assertIsNone(box.context)
        self.assertEqual(box.root_info()[1], boxcert.read_cert(self.exe, os.path.join(other, "root.pem"))["fingerprint"])

    def test_the_signers_refusal_reaches_the_page(self):
        fake = FakeSigner(self, self.signer)
        squatter, _ = self.csr(["testbox.local"])
        self.signer.sign({"csr": squatter, "hostname": "testbox"}, "127.0.0.1")
        box, settings = self.node_box()
        st = self.auto(box, settings, fake.url).run_once()
        self.assertEqual(st["state"], "refused")
        self.assertIn("release testbox.local", st["message"])


from tests.test_https import HttpsBase  # noqa: E402


class PanelRouteTest(HttpsBase):
    def test_the_owner_sets_the_signer_and_the_page_shows_the_state(self):
        full, _ = self.pair("owner laptop")
        self.api.autocert = autocert.AutoCert(self.box, self.settings, env={})
        self.assertIsNone(self.call("GET", "/api/https", token=full)[1]["auto"])
        self.api.autocert.run_once()
        self.assertEqual(self.call("GET", "/api/https", token=full)[1]["auto"]["state"], "off")
        st, body, _ = self.call("POST", "/api/https/auto", {"signer_url": "http://192.168.0.10:8781"}, token=full)
        self.assertEqual((st, body["signer_url"]), (200, "http://192.168.0.10:8781"), body)
        self.assertEqual(self.settings.data["https"]["signer_url"], "http://192.168.0.10:8781")
        for bad in ("ftp://x", "http://a b", 5):
            self.assertEqual(self.call("POST", "/api/https/auto", {"signer_url": bad}, token=full)[0], 400)
        view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)[1]["token"]
        self.assertEqual(self.call("POST", "/api/https/auto", {"signer_url": ""}, token=view)[0], 403)
        self.assertEqual(self.call("GET", "/api/https/auto", token=full)[0], 405)


if __name__ == "__main__":
    unittest.main()
