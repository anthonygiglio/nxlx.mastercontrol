# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The secure connection on the box (D79): a real TLS listener on loopback with a certificate the tool signed from the
box's own request; the same API over both; the Secure cookie over HTTPS only; every kind of bad certificate refused
with its message; undo and removal; the switch "Owner access only over the secure connection"; nothing secret in any
GET, export or diagnostics file; the factory reset; and a settings file with the new key loading in master's code.
Needs openssl (CI's Ubuntu and a Mac have it); skipped cleanly without."""
import http.client
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest

from pvj import httpsbox, server
from pvj.settings import Settings
from tests.test_boxcert import TOOL, openssls
from tests.test_server import ServerBase

spec = importlib.util.spec_from_file_location("boxcert", TOOL)
boxcert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boxcert)
PASS = "a long enough passphrase"
HOSTS = {"testbox.local", "other.local", "localhost", "elsewhere.local"}


class Clock:
    def __init__(self, t=None):
        import time
        self.t = time.time() if t is None else t

    def __call__(self):
        return self.t


@unittest.skipUnless(openssls(), "no openssl on this machine")
class HttpsBase(ServerBase):
    def setUp(self):
        super().setUp()
        self.exe = openssls()[0]
        self.lines = []
        self.api.log = self.lines.append
        self.clock = Clock()
        self.trusted = True
        self.box = httpsbox.HttpsBox(os.path.join(self.tmp, "tls"), self.settings, openssl=self.exe, hostname="testbox",
                                     addresses=lambda: ["127.0.0.1", "fe80::1"], clock_trusted=lambda: self.trusted,
                                     now=self.clock, log=self.lines.append)
        self.api.https = self.box
        handler = server.make_handler(self.api, self.auth, self.web, host_names=HOSTS)
        self.plain = server.PvjServer(("127.0.0.1", 0), handler)
        self.port = self.plain.server_address[1]
        threading.Thread(target=self.plain.serve_forever, daemon=True).start()
        self.addCleanup(self.plain.server_close)
        self.addCleanup(self.plain.shutdown)
        self.tls = server.TlsServer(("127.0.0.1", 0), handler, self.box)
        self.tls_port = self.tls.server_address[1]
        threading.Thread(target=self.tls.serve_forever, daemon=True).start()
        self.addCleanup(self.tls.server_close)
        self.addCleanup(self.tls.shutdown)
        self.ca = os.path.join(self.tmp, "ca")
        # loopback is allowed in the test root's constraints, since that is where the test listener is
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", self.ca, "make-root", "--allow-address", "127.0.0.0/255.0.0.0"],
                                      ask=lambda p: PASS, out=lambda l: None), 0)

    # a client that trusts the test root, like a phone with the root installed
    def scall(self, method, path, body=None, headers=None, token=None, csrf=True, cookie=None, trust=True, host=None):
        ctx = ssl.create_default_context()
        if trust:
            ctx.load_verify_locations(os.path.join(self.ca, "root.pem"))
        else:
            ctx = ssl.create_default_context()      # the system's roots: it does not know ours
        h = {}
        if method == "POST":
            h["Content-Type"] = "application/json"
            if csrf:
                h["X-PVJ-Request"] = "1"
        if token:
            h["Authorization"] = "Bearer " + token
        if cookie:
            h["Cookie"] = cookie
        if host:
            h["Host"] = host
        h.update(headers or {})
        c = http.client.HTTPSConnection("127.0.0.1", self.tls_port, timeout=10, context=ctx)
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        text = r.read()
        c.close()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return r.status, payload, r

    def request_and_sign(self, names=None, days=397, drop=(), add=(), csr=None, out="box-cert.pem"):
        if csr is None:
            st, body, _ = self.call("POST", "/api/https/request", {"names": names} if names else {}, token=self.full)
            self.assertEqual(st, 200, body)
            csr = os.path.join(self.tmp, "box.csr")
            with open(csr, "w") as f:
                f.write(body["request"])
        args = ["--openssl", self.exe, "--dir", self.ca, "sign", csr, "--days", str(days), "--out", os.path.join(self.tmp, out)]
        for d in drop:
            args += ["--drop", d]
        for a in add:
            args += ["--name", a]
        self.assertEqual(boxcert.main(args, ask=lambda p: PASS, out=lambda l: None), 0)
        with open(os.path.join(self.tmp, out)) as f:
            return f.read()

    def install(self, pem, host="testbox.local", token=None):
        return self.call("POST", "/api/https/certificate", {"certificate": pem}, token=token or self.full, headers={"Host": host})


class HttpsTest(HttpsBase):
    def setUp(self):
        super().setUp()
        self.full, _ = self.pair("owner laptop")
        self.view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.full)[1]["token"]

    def test_the_request_names_the_box_and_holds_no_key(self):
        st, body, _ = self.call("GET", "/api/https", token=self.full)
        self.assertEqual(st, 200, body)
        self.assertFalse(body["https"])
        self.assertEqual(body["default_names"], ["testbox.local", "127.0.0.1"])      # not the bare name, not IPv6
        st, body, _ = self.call("POST", "/api/https/request", {}, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertIn("BEGIN CERTIFICATE REQUEST", body["request"])
        self.assertNotIn("PRIVATE", body["request"])
        self.assertEqual(body["names"], ["testbox.local", "127.0.0.1"])
        self.assertEqual(oct(os.stat(self.box.path("key.pem")).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(self.box.folder).st_mode & 0o777), "0o700")
        st, text, r = self.call("GET", "/api/https/request.csr", token=self.full)
        self.assertEqual(st, 200)
        self.assertIn(b"BEGIN CERTIFICATE REQUEST", text)
        self.assertNotIn(b"PRIVATE", text)
        self.assertEqual(r.getheader("Content-Disposition"), 'attachment; filename="testbox.csr"')
        # names are checked: a bare host name and IPv6 are refused with the reason
        for names, word in (([], "1 to"), (["testbox"], "bare host name"), (["fe80::1"], "IPv6"), (["bad name"], "not a host name"), (["a.local"] * 13, "1 to")):
            st, body, _ = self.call("POST", "/api/https/request", {"names": names}, token=self.full)
            self.assertEqual(st, 400, (names, body))
            self.assertIn(word, body["error"])
        # extra names are kept for the next request, in a key an older release ignores
        self.call("POST", "/api/https/request", {"names": ["testbox.local", "studio.local"]}, token=self.full)
        self.assertEqual(self.settings.data["https"]["names"], ["studio.local"])
        self.assertEqual(self.settings.data["schema"], 14)

    def test_the_same_api_answers_over_tls_with_a_secure_cookie(self):
        # before a certificate: a connection is closed at once
        with self.assertRaises((ssl.SSLError, OSError)):
            self.scall("GET", "/api/hello")
        pem = self.request_and_sign()
        st, body, _ = self.install(pem)
        self.assertEqual(st, 200, body)
        self.assertEqual(body["status"]["certificate"]["names"], ["testbox.local", "127.0.0.1"])
        self.assertTrue(body["status"]["https"])
        # the real handshake, with the root installed
        st, body, _ = self.scall("GET", "/api/hello")
        self.assertEqual(st, 200, body)
        # without the root the device refuses the box (what a phone without it would do)
        with self.assertRaises(ssl.SSLError):
            self.scall("GET", "/api/hello", trust=False)
        # the same API: pair over TLS, the cookie is __Host- and Secure
        st, body, r = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "phone over tls"})
        self.assertEqual(st, 200, body)
        cookie = r.getheader("Set-Cookie")
        self.assertTrue(cookie.startswith("__Host-pvj_token="), cookie)
        self.assertIn("; Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        secure_token = body["token"]
        # and over plain http it is the plain cookie, without Secure
        st, body, r = self.call("POST", "/api/pair", {"pin": self.pin, "name": "phone over http"})
        self.assertTrue(r.getheader("Set-Cookie").startswith("pvj_token="))
        self.assertNotIn("Secure", r.getheader("Set-Cookie"))
        # the secure cookie name is read over TLS, and ignored over plain http (a replay there carries nothing)
        self.assertEqual(self.scall("GET", "/api/status", cookie="__Host-pvj_token=" + secure_token)[0], 200)
        self.assertEqual(self.call("GET", "/api/status", headers={"Cookie": "__Host-pvj_token=" + secure_token})[0], 401)
        # the plain cookie works over TLS too (browsers send it there): nothing is lost by moving
        self.assertEqual(self.scall("GET", "/api/status", cookie="pvj_token=" + self.full)[0], 200)
        # the device record remembers how its token was issued
        self.assertTrue(self.box.device_secure(self.auth.authenticate(secure_token)))
        self.assertFalse(self.box.device_secure(self.auth.authenticate(self.full)))
        # logout over TLS clears both names; over http the plain one
        st, body, r = self.scall("POST", "/api/logout", {}, token=secure_token)
        cleared = r.getheaders()
        names = [v.split("=")[0] for k, v in cleared if k.lower() == "set-cookie"]
        self.assertEqual(sorted(names), ["__Host-pvj_token", "pvj_token"])
        st, body, r = self.call("POST", "/api/logout", {}, token=self.call("POST", "/api/pair", {"pin": self.pin, "name": "x"})[1]["token"])
        self.assertEqual([v.split("=")[0] for k, v in r.getheaders() if k.lower() == "set-cookie"], ["pvj_token"])
        # the status says what the page needs
        st, body, _ = self.scall("GET", "/api/https", token=self.full)
        self.assertTrue(body["secure"])
        self.assertIn(body["certificate"]["days_left"], (396, 397))      # the second may tick between signing and asking
        self.assertFalse(body["certificate"]["soon"])
        self.assertTrue(body["root"])
        st, text, r = self.call("GET", "/api/https/root.crt", token=self.full)
        self.assertEqual((st, r.getheader("Content-Type")), (200, "application/x-x509-ca-cert"))
        self.assertIn(b"BEGIN CERTIFICATE", text)
        self.assertEqual(self.call("GET", "/api/https/probe")[1], {"https": False})
        self.assertEqual(self.scall("GET", "/api/https/probe")[1], {"https": True})

    def test_each_kind_of_bad_certificate_is_refused_with_its_message(self):
        good = self.request_and_sign()
        cases = [
            ("garbage", "no certificate in the file", 400),
            (open(os.path.join(self.tmp, "box.csr")).read(), "no certificate in the file", 400),
            (open(os.path.join(self.ca, "root.pem")).read(), "root certificate", 400),
            ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", "something that is not a certificate", 400),
            ("-----BEGIN CERTIFICATE-----\n%%%\n-----END CERTIFICATE-----\n", "not a certificate file", 400),
            ("x" * (httpsbox.MAX_PEM + 1), "too large", 413),
        ]
        for pem, word, status in cases:
            st, body, _ = self.install(pem)
            self.assertEqual(st, status, (word, body))
            self.assertIn(word, body["error"], body)
        self.assertFalse(self.box.context, "nothing was taken into use")
        # for another name than the box is reached by
        other = self.request_and_sign(drop=["testbox.local", "127.0.0.1"], add=["elsewhere.local"], out="other.pem")
        st, body, _ = self.install(other, host="testbox.local")
        self.assertEqual(st, 400, body)
        self.assertIn("does not name testbox.local", body["error"])
        self.assertIn("elsewhere.local", body["error"])
        # a certificate whose key is not this box's (a request from another key, signed by the same root)
        from tests.test_boxcert import make_request
        _, foreign = make_request(self.exe, self.tmp, ["testbox.local"], ["127.0.0.1"])
        wrong_key = self.request_and_sign(csr=foreign, out="wrongkey.pem")
        st, body, _ = self.install(wrong_key)
        self.assertEqual(st, 400, body)
        self.assertIn("does not match this box's key", body["error"])
        # run out, by a clock the box trusts
        short = self.request_and_sign(days=1, out="short.pem")
        self.clock.t += 3 * 86400
        st, body, _ = self.install(short)
        self.assertEqual(st, 400, body)
        self.assertIn("has run out", body["error"])
        # the same with a clock not set from the network: taken, the page says the date is as the box counts it
        self.trusted = False
        st, body, _ = self.install(short)
        self.assertEqual(st, 200, body)
        self.assertTrue(body["status"]["certificate"]["run_out"])
        self.assertFalse(body["status"]["clock_trusted"])
        self.assertTrue(self.box.context, "a run-out certificate is still served: the visiting device judges")
        # a guest, a presenter and nobody cannot reach any of it
        for token, status in ((self.view, 403), (None, 401)):
            for method, path in (("GET", "/api/https"), ("POST", "/api/https/request"), ("POST", "/api/https/certificate"),
                                 ("POST", "/api/https/undo"), ("POST", "/api/https/remove"), ("POST", "/api/https/owner-only"),
                                 ("GET", "/api/https/root.crt"), ("GET", "/api/https/request.csr")):
                st, body, _ = self.call(method, path, {} if method == "POST" else None, token=token)
                self.assertEqual(st, status, (path, token, body))
        # without the request header nothing changes (a cross-site POST)
        self.assertEqual(self.install(good, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/https/remove", {}, token=self.full, csrf=False)[0], 403)
        self.assertTrue(self.box.context)
        # the key is for the panel's account alone
        self.assertEqual(oct(os.stat(self.box.path("key.pem")).st_mode & 0o777), "0o600")

    def test_undo_removal_and_a_new_key(self):
        first = self.request_and_sign(days=100, out="first.pem")
        second = self.request_and_sign(days=200, out="second.pem")
        self.assertEqual(self.install(first)[0], 200)
        self.assertEqual(self.call("POST", "/api/https/undo", {}, token=self.full)[0], 409, "nothing earlier yet")
        self.assertEqual(self.install(second)[0], 200)
        self.assertIn(self.box.status(False, "")["certificate"]["days_left"], (199, 200))
        st, body, _ = self.call("POST", "/api/https/undo", {}, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertIn(body["status"]["certificate"]["days_left"], (99, 100))
        self.assertTrue(body["status"]["previous"])
        self.assertEqual(self.scall("GET", "/api/hello")[0], 200)
        st, body, _ = self.call("POST", "/api/https/remove", {}, token=self.full)
        self.assertEqual((st, body["removed"]), (200, True))
        self.assertFalse(body["status"]["https"])
        with self.assertRaises((ssl.SSLError, OSError)):
            self.scall("GET", "/api/hello")
        self.assertTrue(os.path.isfile(self.box.path("key.pem")), "the key stays for the next request")
        # the same request signed again works, the key being the same
        self.assertEqual(self.install(first)[0], 200)
        # a new key drops the certificate in use, and the old certificate no longer fits
        st, body, _ = self.call("POST", "/api/https/request", {"new_key": True}, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertFalse(self.box.context)
        self.assertEqual(self.install(first)[0], 400)
        self.assertEqual(self.install(self.request_and_sign(csr=None, out="third.pem"))[0], 200)

    def test_the_switch_keeps_owners_off_plain_http(self):
        pem = self.request_and_sign()
        self.assertEqual(self.install(pem)[0], 200)
        on = {"on": True}
        # cannot be switched from plain http, nor over TLS by a device paired over plain http
        st, body, _ = self.call("POST", "/api/https/owner-only", on, token=self.full)
        self.assertEqual(st, 403, body)
        self.assertIn("secure connection only", body["error"])
        st, body, _ = self.scall("POST", "/api/https/owner-only", on, token=self.full)
        self.assertEqual(st, 403, body)
        self.assertIn("pair it again over https", body["error"])
        self.assertFalse(self.box.owner_only)
        # a device paired over TLS switches it on
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner phone, tls"})[1]["token"]
        st, body, _ = self.scall("POST", "/api/https/owner-only", on, token=secure)
        self.assertEqual((st, body["owner_only"]), (200, True), body)
        self.assertTrue(self.settings.data["https"]["owner_only"])
        self.assertTrue(any("switched on" in l for l in self.lines))
        # now, over plain http: the PIN is refused, every owner route is refused and points at https, a guest is fine
        st, body, _ = self.call("POST", "/api/pair", {"pin": self.pin, "name": "late owner"})
        self.assertEqual(st, 403, body)
        self.assertIn(self.box.https_address("testbox.local"), body["error"])
        self.assertIn(":%d/" % self.tls_port, body["error"], "the address names the port when it is not 443")
        code = self.scall("POST", "/api/access/join", {"role": "view"}, token=secure)[1].get("code")
        if code:
            self.assertEqual(self.call("POST", "/api/pair", {"pin": code, "name": "a guest by code"})[0], 200)
        owner_routes = [("GET", "/api/status"), ("GET", "/api/media"), ("POST", "/api/pin/show"), ("POST", "/api/pin/rotate"),
                        ("GET", "/api/system"), ("POST", "/api/devices/invite"), ("GET", "/api/health"), ("GET", "/api/preview.jpg"),
                        ("GET", "/api/qr.svg?for=panel"), ("POST", "/api/https/request"), ("GET", "/api/https/root.crt")]
        for method, path in owner_routes:
            for token in (secure, self.full):
                st, body, _ = self.call(method, path, {} if method == "POST" else None, token=token)
                self.assertEqual(st, 403, (path, body))
                self.assertIn(self.box.https_address("testbox.local"), body["error"])
                self.assertNotIn(self.pin, json.dumps(body))
        for method, path in (("GET", "/api/hello"), ("GET", "/api/https"), ("POST", "/api/logout")):
            st, body, _ = self.call(method, path, {} if method == "POST" else None, token=self.full if path != "/api/logout" else None)
            self.assertEqual(st, 200, (path, body))
        self.assertEqual(self.call("GET", "/api/status", token=self.view)[0], 200, "a guest keeps working over plain http")
        # over TLS the device paired over TLS works; one paired over plain http must pair again
        self.assertEqual(self.scall("GET", "/api/status", token=secure)[0], 200)
        self.assertEqual(self.scall("POST", "/api/pin/show", {}, token=secure)[0], 200)
        st, body, _ = self.scall("GET", "/api/status", token=self.full)
        self.assertEqual(st, 403, body)
        self.assertIn("pair it again", body["error"])
        self.assertEqual(self.scall("POST", "/api/pair", {"pin": self.pin, "name": "again over tls"})[0], 200)
        # with the certificate removed the switch no longer bites: nobody is locked out
        self.scall("POST", "/api/https/remove", {}, token=secure)
        self.assertTrue(self.box.owner_only)
        self.assertFalse(self.box.effective())
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "owner back on http"})[0], 200)
        # and it cannot be switched on again with no certificate (there would be nothing left to reach the owner by)
        self.assertEqual(self.install(pem, token=self.full)[0], 200)
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": False}, token=secure)[0], 200)
        self.assertEqual(self.scall("POST", "/api/https/remove", {}, token=secure)[0], 200)
        with self.assertRaises((ssl.SSLError, OSError)):
            self.scall("POST", "/api/https/owner-only", on, token=secure)
        self.assertEqual(self.install(pem, token=self.full)[0], 200)
        self.assertEqual(self.scall("POST", "/api/https/remove", {}, token=secure)[0], 200)
        # over plain http, with no certificate: the switch route itself answers that it is for the secure connection
        st, body, _ = self.call("POST", "/api/https/owner-only", on, token=secure)
        self.assertEqual(st, 403, body)

    def switched_on(self):
        """A certificate in use and the switch on, by a device paired over TLS; returns that device's token."""
        self.assertEqual(self.install(self.request_and_sign())[0], 200)
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)[0], 200)
        return secure

    def test_m1_a_token_that_crossed_plain_http_is_never_secure_even_after_a_session_over_tls(self):
        """Review of #119, M1: an owner token sniffed off plain http, presented once to /api/session over TLS while
        the switch is off, must not become a secure token."""
        self.assertEqual(self.install(self.request_and_sign())[0], 200)
        st, body, r = self.scall("POST", "/api/session", {"token": self.full})       # the exact sequence
        self.assertEqual(st, 200, body)
        self.assertTrue(r.getheader("Set-Cookie").startswith("__Host-"))
        self.assertFalse(self.box.device_secure(self.auth.authenticate(self.full)), "a session marks nothing")
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)[0], 200)
        for method, path in (("GET", "/api/status"), ("POST", "/api/pin/show")):
            st, body, _ = self.scall(method, path, {} if method == "POST" else None, token=self.full)
            self.assertEqual(st, 403, (path, body))
            self.assertNotIn(self.pin, json.dumps(body))
        # and the session route itself, over TLS with the switch on, does not let it in either
        self.assertEqual(self.scall("POST", "/api/session", {"token": self.full})[0], 200)      # a cookie, nothing more
        self.assertEqual(self.scall("POST", "/api/pin/show", {}, token=self.full)[0], 403)

    def test_m2_a_run_out_certificate_opens_renewal_only_over_http_for_a_paired_owner(self):
        short = self.request_and_sign(days=1, out="short.pem")
        self.assertEqual(self.install(short)[0], 200)
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)[0], 200)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 403, "bites while the certificate is good")
        self.assertEqual(self.call("GET", "/api/https/request.csr", token=self.full)[0], 403)
        # a clock stepped forward (what whoever answers the box's time requests can do) reopens NOTHING of the PIN
        self.clock.t += 3 * 86400
        st, body, _ = self.call("GET", "/api/https", token=self.full)
        self.assertEqual((body["relief"], body["effective"], body["owner_only"]), ("run_out", True, True))
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "owner back over http"})[0], 403)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 403)
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 403)
        self.assertEqual(self.call("POST", "/api/https/request", {}, token=self.full)[0], 403)
        self.assertEqual(self.call("POST", "/api/https/remove", {}, token=self.full)[0], 403)
        self.assertEqual(self.call("GET", "/api/https/request.csr", token=self.view)[0], 403, "a guest still cannot")
        # only what renewal needs, for a device already paired as owner
        self.assertEqual(self.call("GET", "/api/https/request.csr", token=self.full)[0], 200)
        fresh = self.request_and_sign(days=100, out="fresh.pem", csr=os.path.join(self.tmp, "box.csr"))
        st, body, _ = self.install(fresh, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertIsNone(body["status"]["relief"])
        self.assertEqual(self.call("GET", "/api/https/request.csr", token=self.full)[0], 403, "closed again at once")
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 403)
        self.assertTrue(self.scall("GET", "/api/https", token=secure)[1]["effective"])
        # a clock NOT from the network: the box cannot tell, nothing opens
        self.trusted = False
        st, body, _ = self.scall("POST", "/api/https/certificate", {"certificate": short}, token=secure)
        self.assertEqual(st, 200, body)
        self.clock.t += 3 * 86400
        self.assertEqual(self.call("GET", "/api/https", token=self.full)[1]["relief"], None)
        self.assertEqual(self.call("GET", "/api/https/request.csr", token=self.full)[0], 403)
        self.trusted = True
        # a certificate removed while the switch is on: everything open, as if off
        self.assertEqual(self.scall("POST", "/api/https/remove", {}, token=secure)[0], 200)
        self.assertEqual(self.call("GET", "/api/https", token=self.full)[1]["relief"], "no_certificate")
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full)[0], 200)
        # an address the certificate does not carry is no relief: the Host header is the sender's to choose
        self.assertEqual(self.install(fresh, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full, headers={"Host": "other.local"})[0], 403)
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=self.full, headers={"Host": "10.9.9.9"})[0], 403)

    def other_root(self, name="NXLX boxes root"):
        """A second root with the SAME name as the first (an attacker's), allowed for loopback like the first."""
        ca2 = os.path.join(self.tmp, "ca2")
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", ca2, "make-root", "--name", name, "--allow-address", "127.0.0.0/255.0.0.0"],
                                      ask=lambda p: PASS, out=lambda l: None), 0)
        return ca2

    def sign_with(self, ca, csr, out, days=100):
        self.assertEqual(boxcert.main(["--openssl", self.exe, "--dir", ca, "sign", csr, "--days", str(days), "--out", os.path.join(self.tmp, out)],
                                      ask=lambda p: PASS, out=lambda l: None), 0)
        with open(os.path.join(self.tmp, out)) as f:
            return f.read()

    def test_the_root_is_pinned_once_the_box_has_one(self):
        """Third review of #119: the trust anchor cannot be swapped through an upload."""
        first = self.request_and_sign(days=100, out="first.pem")
        csr = os.path.join(self.tmp, "box.csr")
        self.assertEqual(self.install(first)[0], 200)          # the first root is taken as today
        root_before = open(self.box.path("root.pem")).read()
        fp = self.call("GET", "/api/https", token=self.full)[1]["root_fingerprint"]
        self.assertEqual(len(fp.replace(" ", "")), 64)
        self.assertEqual(fp, boxcert.read_cert(self.exe, os.path.join(self.ca, "root.pem"))["fingerprint"], "the tool prints the same fingerprint")
        ca2 = self.other_root()                                 # the same subject name, another key
        foreign = self.sign_with(ca2, csr, "foreign.pem")
        # with the switch off: refused, and the stored root untouched, whatever root the file carries
        st, body, _ = self.install(foreign)
        self.assertEqual(st, 400, body)
        self.assertIn("not issued by the root this box knows", body["error"])
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        self.assertEqual(self.box.info["serial"], httpsbox.read_certificate(httpsbox.pem_certificates(first)[0])["serial"])
        # the reviewer's exact sequence: switch on, the certificate run out, a sniffed http owner token
        short = self.sign_with(self.ca, csr, "short.pem", days=1)
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.assertEqual(self.scall("POST", "/api/https/certificate", {"certificate": short}, token=secure)[0], 200)
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)[0], 200)
        self.clock.t += 3 * 86400
        st, text, _ = self.call("GET", "/api/https/request.csr", token=self.full)
        self.assertEqual(st, 200)                               # he can read the request: the public key and names, no secret
        st, body, _ = self.install(foreign, token=self.full)
        self.assertEqual(st, 400, body)
        self.assertIn("not issued by the root this box knows", body["error"])
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        self.assertEqual(self.call("GET", "/api/https", token=self.full)[1]["relief"], "run_out", "still run out: nothing was taken")
        # a renewal the owner's root issued is accepted over http in that state
        renewed = self.sign_with(self.ca, csr, "renewed.pem", days=100)
        st, body, _ = self.install(renewed, token=self.full)
        self.assertEqual(st, 200, body)
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        self.assertIsNone(body["status"]["relief"])
        # a root replacement over plain http is refused in every state: on, run out, off
        with open(os.path.join(ca2, "root.pem")) as f:
            root2 = f.read()
        fp2 = boxcert.read_cert(self.exe, os.path.join(ca2, "root.pem"))["fingerprint"]
        # (with the http-paired token, and with the TLS-paired token sent over plain http, which a sniffer could hold)
        for token in (self.full, secure):
            self.assertEqual(self.call("POST", "/api/https/root", {"root": root2, "confirm": fp2}, token=token)[0], 403)
        self.scall("POST", "/api/https/certificate", {"certificate": self.sign_with(self.ca, csr, "s2.pem", days=1)}, token=secure)
        self.clock.t += 3 * 86400
        for token in (self.full, secure):
            self.assertEqual(self.call("POST", "/api/https/root", {"root": root2, "confirm": fp2}, token=token)[0], 403)
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": False}, token=secure)[0], 200)
        for token in (self.full, secure):
            st, body, _ = self.call("POST", "/api/https/root", {"root": root2, "confirm": fp2}, token=token)
            self.assertEqual(st, 403, body)
            self.assertIn("secure connection only", body["error"])
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        # over TLS: an upload never replaces it; the explicit action needs a TLS-paired owner and the confirm
        self.assertEqual(self.scall("POST", "/api/https/certificate", {"certificate": foreign}, token=secure)[0], 400)
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        self.assertEqual(self.scall("POST", "/api/https/root", {"root": root2, "confirm": fp2}, token=self.full)[0], 403, "paired over http")
        st, body, _ = self.scall("POST", "/api/https/root", {"root": root2, "confirm": ""}, token=secure)
        self.assertEqual(st, 409, body)
        self.assertIn(fp2, body["error"])
        self.assertEqual(open(self.box.path("root.pem")).read(), root_before)
        st, body, _ = self.scall("POST", "/api/https/root", {"root": first, "confirm": fp}, token=secure)      # the bundle with the SAME root: harmless
        self.assertEqual((st, body["old"], body["new"]), (200, fp, fp), body)
        self.assertEqual(open(self.box.path("root.pem")).read().strip(), root_before.strip())
        self.assertEqual(self.scall("POST", "/api/https/root", {"root": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", "confirm": fp2}, token=secure)[0], 400)
        st, body, _ = self.scall("POST", "/api/https/root", {"root": root2, "confirm": fp2.upper().replace(" ", ":")}, token=secure)
        self.assertEqual(st, 200, body)
        self.assertEqual((body["old"], body["new"]), (fp, fp2))
        self.assertEqual(open(self.box.path("root.pem")).read().strip(), root2.strip())
        self.assertTrue(any("root was replaced" in l for l in self.lines))
        # from now on the other root's certificates are the ones taken, and the first root's refused
        self.assertEqual(self.scall("POST", "/api/https/certificate", {"certificate": foreign}, token=secure)[0], 200)
        # (the box now serves the other root's certificate, which this test's TLS client does not trust: plain http from here, the switch being off)
        self.assertEqual(self.install(renewed, token=self.full)[0], 400)
        self.assertEqual(self.call("GET", "/api/https/root.crt", token=self.full)[1].decode().strip(), root2.strip())

    def test_a_write_that_fails_leaves_the_certificate_in_use_where_it_is(self):
        first = self.request_and_sign(days=100, out="first.pem")
        second = self.request_and_sign(days=200, out="second.pem")
        self.assertEqual(self.install(first)[0], 200)
        real = self.box._write

        def failing(name, text, mode=0o640):
            if name == httpsbox.NEW:
                raise OSError(28, "No space left on device")
            return real(name, text, mode)
        self.box._write = failing
        st, body, _ = self.install(second)
        self.assertEqual(st, 500, body)
        self.assertIn("could not be saved", body["error"])
        self.box._write = real
        self.assertTrue(os.path.isfile(self.box.path("cert.pem")))
        self.assertFalse(os.path.isfile(self.box.path("previous.pem")))
        self.assertFalse(os.path.isfile(self.box.path(httpsbox.NEW)))
        self.assertIn(self.box.status(False, "")["certificate"]["days_left"], (99, 100))
        self.assertTrue(self.box.load(), "what is on disk still loads")
        self.assertEqual(self.scall("GET", "/api/hello")[0], 200)

    def test_m3_a_pem_made_to_backtrack_is_refused_at_once(self):
        import time
        nasty = "-----BEGIN CERTIFICATE-----\n" + " " * (httpsbox.MAX_PEM - 40)
        t0 = time.monotonic()
        st, body, _ = self.install(nasty)
        self.assertLess(time.monotonic() - t0, 2.0, "the panel must not hang on a wrong file")
        self.assertEqual(st, 400, body)
        self.assertIn("no certificate", body["error"])
        for text in ("-----BEGIN CERTIFICATE-----\n" + "-----BEGIN CERTIFICATE-----\n" * 2000, "\n".join(["-----END CERTIFICATE-----"] * 1500)):
            t0 = time.monotonic()
            self.assertIn(self.install(text)[0], (400, 413))
            self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(httpsbox.pem_blocks("x\n-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\ny")[0][0], b"\x00")

    def test_an_owner_code_from_a_controller_is_refused_over_plain_http_while_the_switch_is_on(self):
        from pvj import auth as auth_mod
        secure = self.switched_on()
        with self.settings.lock:
            self.settings.data["controller_code"] = {"enabled": True, "owner": True}
        self.auth.create_controller_code("owner")
        kind, digits, _ = self.auth.controller_digits()
        before = len(self.settings.data["devices"])
        st, body, _ = self.call("POST", "/api/pair", {"pin": digits, "name": "at the controller"})
        self.assertEqual(st, 403, body)
        self.assertIn("secure connection", body["error"])
        self.assertEqual(len(self.settings.data["devices"]), before, "the device it made is taken back")
        # a presenter code still works there
        self.auth.create_controller_code("join")
        kind, digits, _ = self.auth.controller_digits()
        self.assertEqual(self.call("POST", "/api/pair", {"pin": digits, "name": "presenter"})[0], 200)
        self.assertEqual(kind, "join")
        self.assertIsNotNone(auth_mod)

    def test_nothing_secret_in_any_answer_export_or_diagnostics(self):
        pem = self.request_and_sign()
        self.assertEqual(self.install(pem)[0], 200)
        key = open(self.box.path("key.pem")).read()
        body_of = lambda st, body, r: body if isinstance(body, bytes) else json.dumps(body).encode()  # noqa: E731
        seen = 0
        for (method, path), (role, _) in self.api.routes().items():
            if method != "GET":
                continue
            st, body, r = self.call("GET", path, token=self.full)
            text = body_of(st, body, r)
            self.assertNotIn(b"PRIVATE KEY", text, path)
            self.assertNotIn(key.splitlines()[1].encode(), text, path)
            seen += 1
        self.assertGreater(seen, 20)
        for path in ("/api/https", "/api/https/request.csr", "/api/https/root.crt", "/api/system/diagnostics"):
            text = body_of(*self.call("GET", path, token=self.full))
            self.assertNotIn(b"PRIVATE KEY", text, path)
            self.assertNotIn(key.splitlines()[1].encode(), text, path)
        st, body, _ = self.call("POST", "/api/system/settings/export", {"passwords": True}, token=self.full)
        text = json.dumps(body)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertNotIn("https", body["file"]["settings"], "the switch is this box's, not for a file")
        self.assertNotIn("BEGIN CERTIFICATE", text)
        st, body, _ = self.call("GET", "/api/system/diagnostics", token=self.full)
        self.assertNotIn("BEGIN CERTIFICATE", json.dumps(body))
        self.assertNotIn(key, "\n".join(self.lines))

    def test_a_factory_reset_takes_the_key_and_certificate_and_the_box_is_http_only(self):
        pem = self.request_and_sign()
        self.assertEqual(self.install(pem)[0], 200)
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)
        st, body, _ = self.scall("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, token=secure)
        self.assertEqual(st, 200, body)
        self.assertFalse(os.path.exists(self.box.folder))
        self.assertIsNone(self.box.context)
        self.assertNotIn("https", self.settings.data)
        with self.assertRaises((ssl.SSLError, OSError)):
            self.scall("GET", "/api/hello")
        self.assertEqual(self.call("GET", "/api/hello")[0], 200)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.auth.current_pin, "name": "after"})[0], 200, "the PIN works over http again")

    def test_a_certificate_on_disk_that_will_not_load_leaves_http_as_it_is(self):
        self.box._ensure_folder()
        self.box._write("cert.pem", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
        self.assertFalse(self.box.load())
        self.assertIsNone(self.box.context)
        st, body, _ = self.call("GET", "/api/https", token=self.full)
        self.assertEqual(st, 200)
        self.assertTrue(body["load_error"])
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 200)

    def test_support_logins_cannot_touch_it(self):
        from pvj import support as support_mod
        self.assertTrue(any(p == "/api/https" for p in support_mod.REMOTE_DENY_PREFIX))


class ReaderTest(unittest.TestCase):
    def test_name_matching_is_what_a_browser_does(self):
        self.assertTrue(httpsbox.name_matches("TestBox.local.", ["testbox.local"], []))
        self.assertTrue(httpsbox.name_matches("192.168.0.169", [], ["192.168.0.169"]))
        self.assertTrue(httpsbox.name_matches("a.studio.local", ["*.studio.local"], []))
        self.assertFalse(httpsbox.name_matches("studio.local", ["*.studio.local"], []))
        self.assertFalse(httpsbox.name_matches("testbox", ["testbox.local"], []))
        self.assertFalse(httpsbox.name_matches("", ["testbox.local"], []))
        self.assertEqual(httpsbox.host_of("TestBox.local:8080"), "testbox.local")
        self.assertEqual(httpsbox.host_of("[fe80::1]:443"), "fe80::1")
        self.assertEqual(httpsbox.host_of("192.168.0.2"), "192.168.0.2")

    @unittest.skipUnless(openssls(), "no openssl on this machine")
    def test_the_der_reader_agrees_with_openssl(self):
        exe = openssls()[0]
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ca = os.path.join(tmp, "ca")
        self.assertEqual(boxcert.main(["--openssl", exe, "--dir", ca, "make-root"], ask=lambda p: PASS, out=lambda l: None), 0)
        from tests.test_boxcert import make_request
        _, csr = make_request(exe, tmp, ["box.local", "two.local"], ["10.1.2.3"], cn="box.local")
        out = os.path.join(tmp, "c.pem")
        self.assertEqual(boxcert.main(["--openssl", exe, "--dir", ca, "sign", csr, "--days", "10", "--out", out], ask=lambda p: PASS, out=lambda l: None), 0)
        leaf, root = [httpsbox.read_certificate(d) for d in httpsbox.pem_certificates(open(out).read())]
        self.assertEqual((leaf["dns"], leaf["ips"], leaf["server_auth"], leaf["ca"]), (["box.local", "two.local"], ["10.1.2.3"], True, False))
        self.assertEqual(leaf["issuer"], "NXLX boxes root")
        self.assertEqual((leaf["not_after"] - leaf["not_before"]) // 86400, 10)
        info = boxcert.read_cert(exe, out)
        self.assertEqual(leaf["serial"].lstrip("0"), info["serial"].lstrip("0"))
        self.assertEqual(leaf["not_after"], int(info["end"].timestamp()))
        self.assertTrue(root["ca"])
        self.assertFalse(root["server_auth"])


class OlderReleaseTest(unittest.TestCase):
    def test_master_loads_a_settings_file_with_the_https_key(self):
        """Rollback: master's own pvj/settings.py (schema 14) loads a file this branch wrote, keeps the key it does
        not know and changes nothing. Skipped where origin/master cannot be read."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            text = subprocess.run(["git", "-C", here, "show", "origin/master:pvj/settings.py"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            text = None
        if not text or text.returncode != 0:
            self.skipTest("origin/master is not readable here")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        pkg = os.path.join(tmp, "pvjmaster")
        os.makedirs(pkg)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        shutil.copy(os.path.join(here, "pvj", "paths.py"), pkg)
        with open(os.path.join(pkg, "settings.py"), "w") as f:
            f.write(text.stdout)
        import sys
        sys.path.insert(0, tmp)
        self.addCleanup(sys.path.remove, tmp)
        master = importlib.import_module("pvjmaster.settings")
        self.assertEqual(master.SCHEMA, 14, "master moved on: look at the migration before trusting this test")
        path = os.path.join(tmp, "settings.json")
        s = Settings(path)
        s.load()
        s.data["https"] = {"owner_only": True, "names": ["studio.local"]}
        s.data["devices"].append({"id": "abcd1234", "name": "x", "role": "full", "token_hash": "0" * 64, "created": 1, "secure": True})
        s.save()
        m = master.Settings(path)
        data = m.load()
        self.assertEqual(data["https"], {"owner_only": True, "names": ["studio.local"]})
        self.assertEqual(data["devices"][0]["secure"], True)
        self.assertEqual(data["schema"], 14)
        self.assertFalse(os.path.exists(path + ".bak-v14"), "no migration ran")


if __name__ == "__main__":
    unittest.main()
