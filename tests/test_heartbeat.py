# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The daily heartbeat (J-057, D149): the address check, the setting, the ping with a fake service (success, a refusal,
a timeout, a network error, anything else: it never raises), what a request carries, the thread, the Owner's routes and
who may not use them, support's refusal, and what an export, an import and the diagnostics file do with the address. The
role table rows are in tests/test_roles.py, tests/test_carried_roles.py and tests/test_support_reach.py. Everything is
on loopback; nothing here reaches the internet."""
import http.server
import json
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import tests  # noqa: F401  (tests/test_lockrank.py checks that every test module imports the package)
from mctl import boxcare, heartbeat as hb, support
from mctl.settings import Settings
from tests.test_boxcare import Base as BoxcareBase
from tests.test_server import ServerBase

KEY = "3f2a9c1e-77d4-4b1a-9a0e-SECRETKEY1234"
URL = "https://hc-ping.com/" + KEY


class UrlCheck(unittest.TestCase):
    def test_good_addresses_pass(self):
        for url in (URL, "https://hc-ping.com/abc", "https://healthchecks.example.org:8443/ping/abc?x=1", "https://10.0.0.5/ping/abc",
                    "https://status.example.com/api/push/abcd1234?status=up&msg=OK&ping="):
            self.assertEqual(hb.valid_url(url), url, url)

    def test_the_scheme_and_host_are_made_lower_case_and_the_path_is_kept(self):
        self.assertEqual(hb.valid_url("HTTPS://HC-Ping.COM/AbC"), "https://hc-ping.com/AbC")

    def test_only_https_with_a_host_and_the_checks_own_part(self):
        for url in ("", None, 5, ["https://hc-ping.com/a"], "http://hc-ping.com/a", "ftp://hc-ping.com/a", "hc-ping.com/a", "//hc-ping.com/a",
                    "javascript:alert(1)", "https://", "https:///abc", "https://hc-ping.com", "https://hc-ping.com/", "file:///etc/passwd"):
            with self.assertRaises(hb.HeartbeatError, msg=repr(url)):
                hb.valid_url(url)

    def test_no_login_no_spaces_no_tricks(self):
        for url in ("https://user:pw@hc-ping.com/a", "https://user@hc-ping.com/a", "https://@hc-ping.com/a", "https://hc-ping.com@evil.example/a",
                    "https://hc ping.com/a", "https://hc-ping.com/a b", " https://hc-ping.com/a", "https://hc-ping.com/a ", "https://hc-ping.com/a\n",
                    "https://hc-ping.com/a\r\nHost: x", "https://hc-ping.com/a\x00", "https://hc-ping.com/a\tb", "https://hc-ping.com/a\\b",
                    "https://hc-ping.com/a#frag", "https://hc-ping.com/a#", "https://hc-ping.com:0/a", "https://hc-ping.com:99999/a",
                    "https://hc-ping.com:abc/a", "https://[::1]/a", "https://-bad.example/a", "https://bad-.example/a", "https://exa_mple.com/a",
                    "https://héllo.example/a", "https://hc-ping.com/é", "https://999.1.1.1/a", "https://1.2.3/a", "https://a..b/a",
                    "https://hc-ping.com/<script>", "https://hc-ping.com/a\"b", "https://hc-ping.com/a'b", "https://hc-ping.com/a`b"):
            with self.assertRaises(hb.HeartbeatError, msg=repr(url)):
                hb.valid_url(url)

    def test_length(self):
        head = "https://hc-ping.com/"
        self.assertEqual(hb.valid_url(head + "a" * (hb.MAX_URL - len(head))), head + "a" * (hb.MAX_URL - len(head)))
        with self.assertRaises(hb.HeartbeatError):
            hb.valid_url(head + "a" * (hb.MAX_URL - len(head) + 1))
        with self.assertRaises(hb.HeartbeatError):
            hb.valid_url("https://" + "a" * 300 + ".com/x")

    def test_what_is_shown_back_never_holds_the_key(self):
        shown = hb.mask(URL)
        self.assertNotIn(KEY, shown)
        self.assertNotIn("SECRET", shown)
        self.assertTrue(shown.startswith("https://hc-ping.com/"))
        self.assertTrue(shown.endswith("1234"))
        self.assertNotIn("status=up", hb.mask("https://s.example.com/push/abcdefgh?status=up&token=zzz"))
        self.assertNotIn("zzz", hb.mask("https://s.example.com/push/abcdefgh?status=up&token=zzz"))
        self.assertEqual(hb.mask(""), "")
        self.assertEqual(hb.mask("https://hc-ping.com/ab"), "https://hc-ping.com/…")      # too short to show any of it

    def test_fail_goes_on_the_path_before_a_query(self):
        self.assertEqual(hb.with_fail("https://hc-ping.com/abc"), "https://hc-ping.com/abc/fail")
        self.assertEqual(hb.with_fail("https://hc-ping.com/abc/"), "https://hc-ping.com/abc/fail")
        self.assertEqual(hb.with_fail("https://x.example/p/abc?q=1"), "https://x.example/p/abc/fail?q=1")


class SettingForm(unittest.TestCase):
    def test_off_by_default_and_damage_reads_as_the_default(self):
        self.assertEqual(hb.stored(None), {"on": False, "url": "", "minutes": 15, "fail": False})
        for junk in ("x", 5, [], {"on": "yes"}, {"on": True, "url": ""}, {"url": "http://x/y"}, {"minutes": 7}, {"other": 1}):
            self.assertEqual(hb.stored(junk), hb.DEFAULT, junk)

    def test_checked_as_a_whole(self):
        self.assertEqual(hb.form({"on": True, "url": URL, "minutes": 30})["minutes"], 30)
        for bad in ({"on": 1}, {"on": True}, {"minutes": True}, {"minutes": 15.0 + 0.5}, {"minutes": "15"}, {"minutes": 0}, {"minutes": 1440},
                    {"fail": "no"}, {"url": "http://hc-ping.com/a"}, {"surprise": 1}, [], None, "x"):
            with self.assertRaises(hb.HeartbeatError, msg=repr(bad)):
                hb.form(bad)


class Settled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.settings = Settings(os.path.join(self.tmp, "settings.json"))
        self.settings.load()
        self.now = [1_000_000.0]
        self.lines = []
        self.running = [True]
        self.requests = []
        self.answer = lambda req, timeout: Reply(204)
        self.beat = hb.Heartbeat(self.settings, player_running=lambda: self.running[0], log=self.lines.append, opener=self.open,
                                 clock=lambda: self.now[0])

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        return self.answer(req, timeout)

    def keep(self, **kw):
        self.beat.save(hb.form(dict({"on": True, "url": URL}, **kw)))


class Reply:
    def __init__(self, status, body=b""):
        self.status, self.body, self.closed = status, body, False

    def read(self, n=-1):
        return self.body[:n]

    def close(self):
        self.closed = True


class Ping(Settled):
    def test_a_good_answer(self):
        self.keep()
        r = self.beat.ping("test")
        self.assertEqual((r["ok"], r["how"]), (True, "test"))
        self.assertEqual(self.beat.status()["last"]["ok"], True)

    def test_what_a_request_carries_is_the_address_and_nothing_else(self):
        self.keep()
        self.beat.ping()
        req, timeout = self.requests[0]
        self.assertEqual((req.full_url, req.get_method(), req.data), (URL, "GET", None))
        self.assertEqual(dict((k.lower(), v) for k, v in req.header_items()), {"user-agent": hb.USER_AGENT, "accept": "*/*"})
        self.assertLessEqual(timeout, 10)
        self.assertEqual(hb.USER_AGENT, "nxlx-mastercontrol-heartbeat/1")          # no version, node name or address in it

    def test_a_refusal_a_timeout_and_a_network_error_are_sentences_and_never_raise(self):
        self.keep()
        errors = (urllib.error.HTTPError(URL, 404, "Not Found", {}, None), urllib.error.HTTPError(URL, 500, "x", {}, None),
                  urllib.error.HTTPError(URL, 302, "x", {}, None), urllib.error.HTTPError(URL, 429, "x", {}, None),
                  socket.timeout("timed out"), TimeoutError(), urllib.error.URLError(socket.timeout("t")),
                  urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), urllib.error.URLError(ssl.SSLCertVerificationError("bad")),
                  ConnectionResetError(), OSError("x"), ssl.SSLError("x"), RuntimeError("boom"), ValueError("v"))
        for e in errors:
            def fail(req, timeout, e=e):
                raise e
            self.answer = fail
            r = self.beat.ping()
            self.assertFalse(r["ok"], repr(e))
            self.assertIsInstance(r["text"], str)
            self.assertNotIn("hc-ping", r["text"], repr(e))
            self.assertNotIn(KEY, r["text"])
            self.assertFalse(self.beat.status()["last"]["ok"])

    def test_the_words_for_the_common_failures(self):
        self.keep()
        for e, part in ((urllib.error.HTTPError(URL, 404, "x", {}, None), "404"), (socket.timeout(), "No answer"),
                        (urllib.error.URLError(socket.gaierror(-2, "x")), "name could not be found"),
                        (urllib.error.URLError(ssl.SSLCertVerificationError("x")), "certificate")):
            self.answer = lambda req, timeout, e=e: (_ for _ in ()).throw(e)
            self.assertIn(part, self.beat.ping()["text"])

    def test_a_non_2xx_without_an_error_is_a_failure(self):
        self.keep()
        self.answer = lambda req, timeout: Reply(302)
        self.assertFalse(self.beat.ping()["ok"])
        self.answer = lambda req, timeout: Reply(200)
        self.assertTrue(self.beat.ping()["ok"])

    def test_a_reply_that_cannot_be_read_is_a_failure_not_a_crash(self):
        self.keep()

        class Broken(Reply):
            def read(self, n=-1):
                raise OSError("reset")
        self.answer = lambda req, timeout: Broken(200)
        self.assertFalse(self.beat.ping()["ok"])

    def test_the_reply_is_closed_and_only_a_little_of_it_is_read(self):
        self.keep()
        reply = Reply(200, b"x" * 100000)
        self.answer = lambda req, timeout: reply
        self.beat.ping()
        self.assertTrue(reply.closed)

    def test_no_address_sends_nothing(self):
        r = self.beat.ping("test")
        self.assertFalse(r["ok"])
        self.assertEqual(self.requests, [])
        self.settings.data["heartbeat"] = "junk"                 # a damaged file is the default: off and no address
        self.assertFalse(self.beat.ping()["ok"])
        self.assertEqual(self.requests, [])

    def test_fail_is_told_only_when_asked_for_and_the_player_is_down(self):
        self.keep()
        self.running[0] = False
        self.beat.ping()
        self.assertEqual(self.requests[-1][0].full_url, URL)                  # option off: the same ping whatever the player does
        self.keep(fail=True)
        self.beat.ping()
        self.assertEqual(self.requests[-1][0].full_url, URL + "/fail")
        self.running[0] = True
        self.beat.ping()
        self.assertEqual(self.requests[-1][0].full_url, URL)

    def test_a_player_check_that_breaks_is_not_a_failure_report(self):
        self.keep(fail=True)

        def boom():
            raise RuntimeError("x")
        self.beat._player_running = boom
        self.beat.ping()
        self.assertEqual(self.requests[-1][0].full_url, URL)

    def test_a_change_is_logged_once_and_never_with_the_address(self):
        self.keep()
        self.answer = lambda req, timeout: (_ for _ in ()).throw(socket.timeout())
        self.beat.ping()
        self.beat.ping()
        self.answer = lambda req, timeout: Reply(200)
        self.beat.ping()
        self.beat.ping()
        self.assertEqual(len(self.lines), 2, self.lines)
        self.assertIn("failed", self.lines[0])
        self.assertIn("working again", self.lines[1])
        self.assertNotIn(KEY, " ".join(self.lines))
        self.assertNotIn("hc-ping", " ".join(self.lines))

    def test_the_test_ping_is_one_at_a_time_and_holds_no_lock_while_it_waits(self):
        self.keep()
        inside, release = threading.Event(), threading.Event()

        def slow(req, timeout):
            inside.set()
            release.wait(5)
            return Reply(200)
        self.answer = slow
        out = []
        t = threading.Thread(target=lambda: out.append(self.beat.test()), daemon=True)
        t.start()
        self.assertTrue(inside.wait(5))
        self.assertIsNone(self.beat.test())                      # a second one at the same time: refused, not queued
        self.assertTrue(self.beat.status()["on"])                # and the panel's reads are not held up
        self.assertEqual(self.beat.config()["url"], URL)
        release.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertTrue(out[0]["ok"])


class Timing(Settled):
    def test_off_sends_nothing(self):
        for _ in range(3):
            self.now[0] += 100000
            self.assertFalse(self.beat.tick())
        self.assertEqual(self.requests, [])

    def test_on_pings_after_the_first_wait_and_then_every_interval(self):
        self.keep(minutes=15)
        self.assertFalse(self.beat.tick())                       # not before the first wait
        self.now[0] += hb.FIRST_PING_AFTER + 1
        self.assertTrue(self.beat.tick())
        self.assertEqual(len(self.requests), 1)
        self.now[0] += 14 * 60
        self.assertFalse(self.beat.tick())
        self.now[0] += 61
        self.assertTrue(self.beat.tick())
        self.assertEqual(len(self.requests), 2)

    def test_switching_off_stops_it_and_status_says_so(self):
        self.keep()
        self.beat.save(hb.form({"on": False, "url": URL}))
        self.now[0] += 100000
        self.assertFalse(self.beat.tick())
        st = self.beat.status()
        self.assertEqual((st["on"], st["next_in"]), (False, None))

    def test_a_tick_that_breaks_does_not_stop_the_thread(self):
        self.keep()
        calls = []
        real = self.beat.tick

        def tick():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("x")
            return real()
        self.beat.tick = tick
        with mock.patch.object(hb, "TICK", 0.02):
            self.beat.start()
            deadline = time.time() + 5
            while len(calls) < 3 and time.time() < deadline:
                self.beat._wake.set()
                time.sleep(0.02)
            self.beat.stop()
        self.beat._thread.join(5)
        self.assertGreaterEqual(len(calls), 3)
        self.assertFalse(self.beat._thread.is_alive())

    def test_the_real_thread_pings_and_stops(self):
        self.settings.data["heartbeat"] = {"on": True, "url": URL, "minutes": 5, "fail": False}
        got = threading.Event()
        self.answer = lambda req, timeout: (got.set(), Reply(200))[1]
        with mock.patch.object(hb, "TICK", 0.02), mock.patch.object(hb, "FIRST_PING_AFTER", 0.0):
            self.beat.start()
            self.assertTrue(got.wait(5))
            self.beat.stop()
        self.beat._thread.join(5)
        self.assertFalse(self.beat._thread.is_alive())
        self.assertEqual(len(self.requests), 1)

    def test_saving_a_setting_failing_to_write_is_an_oserror_for_the_route_to_answer(self):
        with mock.patch.object(self.settings, "save", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                self.keep()


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    mode = "ok"

    def do_GET(self):
        Handler.seen.append({"path": self.path, "headers": dict(self.headers), "method": "GET"})
        if Handler.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
        elif Handler.mode == "hang":
            time.sleep(1.5)
        else:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

    def log_message(self, *a):
        pass


class RealRequest(unittest.TestCase):
    """The default opener against a loopback server: what really goes on the wire, no redirect, a timeout. (The check of
    the address makes the node itself speak https only; this talks plain http to a local test server on purpose.)"""

    def setUp(self):
        Handler.seen, Handler.mode = [], "ok"
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def req(self, path="/ping/abc"):
        return urllib.request.Request(self.base + path, method="GET", headers={"User-Agent": hb.USER_AGENT, "Accept": "*/*"})

    def test_a_plain_get_with_two_headers_besides_the_usual(self):
        r = hb.default_open(self.req(), 3)
        self.assertEqual(r.status, 200)
        r.close()
        seen = Handler.seen[0]
        self.assertEqual((seen["path"], seen["method"]), ("/ping/abc", "GET"))
        names = {k.lower() for k in seen["headers"]}
        self.assertEqual(names - {"host", "user-agent", "accept", "accept-encoding", "connection"}, set())
        self.assertNotIn("authorization", names)
        self.assertNotIn("cookie", names)
        self.assertNotIn("content-length", names)

    def test_a_redirect_is_not_followed(self):
        Handler.mode = "redirect"
        with self.assertRaises(urllib.error.HTTPError) as c:
            hb.default_open(self.req(), 3)
        self.assertEqual(c.exception.code, 302)
        self.assertEqual(len(Handler.seen), 1)
        self.assertIn("redirected", hb.words(c.exception))

    def test_a_silent_server_is_given_up_on(self):
        Handler.mode = "hang"
        began = time.time()
        with self.assertRaises(Exception) as c:
            hb.default_open(self.req(), 0.3)
        self.assertLess(time.time() - began, 1.2)
        self.assertIn("No answer", hb.words(c.exception))

    def test_a_closed_port_is_a_sentence(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(Exception) as c:
            hb.default_open(urllib.request.Request("http://127.0.0.1:%d/x" % port), 2)
        self.assertEqual(hb.words(c.exception), "The service could not be reached.")


class Route(ServerBase):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.answer = lambda req, timeout: Reply(200)
        self.api.heartbeat._open = lambda req, timeout: (self.requests.append(req), self.answer(req, timeout))[1]

    def owner(self):
        return self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def other(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "x", "role": role}, token=self.owner())[1]["token"]

    def disk(self):
        with open(self.settings.path) as f:
            return json.load(f).get("heartbeat")

    def test_off_and_empty_by_default(self):
        st, body = self.call("GET", "/api/heartbeat", token=self.owner())[:2]
        self.assertEqual(st, 200)
        self.assertEqual((body["on"], body["has_url"], body["url_shown"], body["minutes"], body["last"], body["fail"]), (False, False, "", 15, None, False))
        self.assertEqual(self.requests, [])

    def test_the_owner_saves_an_address_and_switches_it_on(self):
        tok = self.owner()
        st, body = self.call("POST", "/api/heartbeat", {"url": URL}, token=tok)[:2]
        self.assertEqual((st, body["has_url"], body["on"]), (200, True, False))
        st, body = self.call("POST", "/api/heartbeat", {"on": True, "minutes": 30}, token=tok)[:2]
        self.assertEqual((st, body["on"], body["minutes"]), (200, True, 30))
        self.assertEqual(self.disk(), {"on": True, "url": URL, "minutes": 30, "fail": False})
        self.assertEqual(self.settings.data["heartbeat"]["url"], URL)

    def test_the_address_is_never_shown_back_in_full(self):
        tok = self.owner()
        for path, method, body in (("/api/heartbeat", "POST", {"url": URL, "on": True}), ("/api/heartbeat", "GET", None),
                                   ("/api/heartbeat/test", "POST", {})):
            st, out = self.call(method, path, body, token=tok)[:2]
            self.assertEqual(st, 200, out)
            blob = json.dumps(out)
            self.assertNotIn(KEY, blob)
            self.assertNotIn("SECRETKEY", blob)
            self.assertIn("hc-ping.com", blob)
        st, out = self.call("GET", "/api/status", token=tok)[:2]
        self.assertNotIn(KEY, json.dumps(out))

    def test_bad_input_is_a_400_and_keeps_what_was_there(self):
        tok = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL, "on": True}, token=tok)
        before = self.disk()
        for body in ({}, {"url": "http://hc-ping.com/a"}, {"url": "https://u:p@hc-ping.com/a"}, {"url": "https://hc-ping.com/a b"}, {"url": 5},
                     {"url": "https://hc-ping.com"}, {"minutes": 7}, {"minutes": True}, {"on": "yes"}, {"fail": 1}, {"surprise": 1}, {"url": "x" * 400}):
            st, out = self.call("POST", "/api/heartbeat", body, token=tok)[:2]
            self.assertEqual(st, 400, (body, out))
        self.assertEqual(self.disk(), before)

    def test_it_cannot_be_switched_on_without_an_address(self):
        tok = self.owner()
        self.assertEqual(self.call("POST", "/api/heartbeat", {"on": True}, token=tok)[0], 400)
        self.assertEqual(self.disk(), hb.DEFAULT)

    def test_forgetting_the_address_switches_it_off(self):
        tok = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL, "on": True}, token=tok)
        st, body = self.call("POST", "/api/heartbeat", {"url": ""}, token=tok)[:2]
        self.assertEqual((st, body["on"], body["has_url"]), (200, False, False))
        self.assertEqual(self.disk()["url"], "")
        self.assertEqual(self.call("POST", "/api/heartbeat", {"url": "", "on": True}, token=tok)[0], 400)

    def test_the_test_ping_needs_a_saved_address_and_says_the_result_in_words(self):
        tok = self.owner()
        self.assertEqual(self.call("POST", "/api/heartbeat/test", {}, token=tok)[0], 400)
        self.assertEqual(self.requests, [])
        self.call("POST", "/api/heartbeat", {"url": URL}, token=tok)
        st, body = self.call("POST", "/api/heartbeat/test", {}, token=tok)[:2]
        self.assertEqual((st, body["result"]["ok"]), (200, True))
        self.assertIn("took the ping", body["result"]["text"])
        self.assertEqual(self.requests[0].full_url, URL)                      # works while the heartbeat is off
        self.assertEqual((self.requests[0].get_method(), self.requests[0].data), ("GET", None))
        self.assertTrue(body["last"]["ok"])
        self.answer = lambda req, timeout: (_ for _ in ()).throw(urllib.error.HTTPError(URL, 404, "x", {}, None))
        st, body = self.call("POST", "/api/heartbeat/test", {}, token=tok)[:2]
        self.assertEqual((st, body["result"]["ok"]), (200, False))
        self.assertIn("404", body["result"]["text"])
        self.assertNotIn(KEY, json.dumps(body))
        self.assertFalse(self.call("GET", "/api/heartbeat", token=tok)[1]["last"]["ok"])

    def test_a_second_test_while_one_runs_is_a_409(self):
        tok = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL}, token=tok)
        self.assertTrue(self.api.heartbeat._testing.acquire(blocking=False))
        try:
            self.assertEqual(self.call("POST", "/api/heartbeat/test", {}, token=tok)[0], 409)
        finally:
            self.api.heartbeat._testing.release()

    def test_not_an_operator_or_a_guest(self):
        owner = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL, "on": True}, token=owner)
        before = self.disk()
        for role in ("live", "view"):
            tok = self.other(role)
            for method, path, body in (("GET", "/api/heartbeat", None), ("POST", "/api/heartbeat", {"url": "https://evil.example/x"}),
                                       ("POST", "/api/heartbeat", {"on": False}), ("POST", "/api/heartbeat/test", {})):
                self.assertEqual(self.call(method, path, body, token=tok)[0], 403, (role, method, path))
        self.assertEqual(self.disk(), before)
        self.assertEqual(self.requests, [])

    def test_no_sign_in_no_answer(self):
        for method, path in (("GET", "/api/heartbeat"), ("POST", "/api/heartbeat"), ("POST", "/api/heartbeat/test")):
            self.assertEqual(self.call(method, path, {} if method == "POST" else None)[0], 401)

    def test_not_over_remote_support(self):
        tok = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL, "on": True}, token=tok)
        before = self.disk()
        with mock.patch.object(self.api.support, "is_remote", lambda c: True):
            for method, path, body in (("POST", "/api/heartbeat", {"url": "https://evil.example/x"}), ("POST", "/api/heartbeat", {"on": False}),
                                       ("POST", "/api/heartbeat/test", {})):
                self.assertEqual(self.call(method, path, body, token=tok)[0], 403, path)
        self.assertEqual(self.disk(), before)
        self.assertEqual(self.requests, [])

    def test_the_handlers_themselves_refuse_a_support_device(self):
        """Not only the guard in front of them (support.REMOTE_DENY): the handler too."""
        from mctl.api import ApiError
        remote = {"id": "support-1", "role": "full", "remote": True}
        for fn, body in ((self.api.set_heartbeat, {"url": URL}), (self.api.heartbeat_test, {})):
            with self.assertRaises(ApiError) as c:
                fn(body, remote, "10.99.0.2")
            self.assertEqual(c.exception.status, 403)
        self.assertEqual(self.settings.data["heartbeat"], hb.DEFAULT)

    def test_support_may_not_use_the_routes_by_name(self):
        self.assertIn(("POST", "/api/heartbeat"), support.REMOTE_DENY)
        self.assertIn(("POST", "/api/heartbeat/test"), support.REMOTE_DENY)

    def test_the_setting_is_kept_where_the_others_are(self):
        tok = self.owner()
        self.call("POST", "/api/heartbeat", {"url": URL, "on": True, "fail": True}, token=tok)
        again = Settings(self.settings.path)
        again.load()
        self.assertEqual(hb.stored(again.data["heartbeat"]), {"on": True, "url": URL, "minutes": 15, "fail": True})


class ExportAndDiagnostics(BoxcareBase):
    def setUp(self):
        super().setUp()
        self.settings.data["heartbeat"] = {"on": True, "url": URL, "minutes": 15, "fail": False}

    def test_it_belongs_to_the_node(self):
        self.assertIn("heartbeat", boxcare.BOX_OWN)

    def test_an_export_does_not_carry_it(self):
        file = self.export()
        self.assertNotIn("heartbeat", file["settings"])
        self.assertNotIn(KEY, json.dumps(file))

    def test_an_import_does_not_set_it(self):
        file = self.export()
        file["settings"]["heartbeat"] = {"on": True, "url": "https://evil.example/steal", "minutes": 5, "fail": False}
        st, out = self.send(file)
        self.assertEqual((st, out["problems"]), (200, []), out)
        self.assertIn("heartbeat is not imported by this version; left as it is", out["notes"])
        self.assertEqual(self.settings.data["heartbeat"]["url"], URL)

    def test_the_diagnostics_file_has_the_address_masked(self):
        shown = boxcare.public_settings(self.settings.data)
        self.assertNotIn(KEY, json.dumps(shown))
        self.assertEqual(shown["heartbeat"]["url"], hb.mask(URL))
        self.assertTrue(shown["heartbeat"]["on"])


if __name__ == "__main__":
    unittest.main()
