# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Feedback from people with no login, the QR sticker (J-179 slice B, D147 addendum): the page GET /feedback, the one route
POST /api/feedback/visitor, the Owner's switch POST /api/feedback/open, the sticker GET /api/feedback/sticker, the limit for
each address, and the QR code of the sticker."""
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from mctl import boxcare, feedback, qr, server
from tests.test_boxcare import Base as CareBase, PIN
from tests.test_feedback import Clock

PHONE = "192.168.7.41"


class LimitTest(unittest.TestCase):
    def make(self, **kw):
        self.clock = Clock()
        return feedback.AddressLimit(clock=self.clock, **kw)

    def test_one_in_ten_seconds_and_ten_in_an_hour_for_an_address(self):
        lim = self.make()
        lim.admit(PHONE)
        self.clock.t += 3
        with self.assertRaises(feedback.TooFast) as c:
            lim.admit(PHONE)
        self.assertTrue(1 <= c.exception.wait <= 10)
        lim.admit("192.168.7.42")                                  # another address is not held
        for i in range(9):
            self.clock.t += 11
            if i < 8:
                lim.admit(PHONE)
        # the PHONE address has sent 9 now (1 + 8); the tenth is allowed, the eleventh is not
        self.clock.t += 11
        lim.admit(PHONE)
        self.clock.t += 11
        with self.assertRaises(feedback.TooFast) as c:
            lim.admit(PHONE)
        self.assertIn("try again later", str(c.exception))
        self.assertGreater(c.exception.wait, 60)
        self.clock.t += 3600
        lim.admit(PHONE)                                           # an hour on, it starts again

    def test_a_refused_send_counts_nothing_and_release_takes_one_back(self):
        lim = self.make()
        lim.admit(PHONE)
        self.clock.t += 1
        with self.assertRaises(feedback.TooFast):
            lim.admit(PHONE)
        lim.release(PHONE)                                         # the node did not keep the note: no wait for the sender
        lim.admit(PHONE)
        self.assertEqual(lim.size(), 1)
        lim.release(PHONE)
        lim.release(PHONE)                                         # nothing left to take back: no error
        self.assertEqual(lim.size(), 0)

    def test_the_table_is_bounded_and_the_quiet_leave_it(self):
        lim = self.make(most=8)
        for i in range(500):
            lim.admit("10.1.%d.%d" % (i // 250, i % 250 + 1))
            self.assertLessEqual(lim.size(), 8)
        self.assertEqual(lim.size(), 8)
        self.clock.t += 3601
        lim.admit(PHONE)
        self.assertEqual(lim.size(), 1, "an address with nothing in the last hour leaves no entry")

    def test_one_ipv6_prefix_is_one_sender_and_a_mapped_ipv4_address_is_the_ipv4_one(self):
        lim = self.make()
        lim.admit("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        with self.assertRaises(feedback.TooFast):
            lim.admit("2001:db8:1:2:1111:2222:3333:4444")          # a phone changes the end of its address
        lim.admit("2001:db8:1:3::1")
        lim.admit("::ffff:192.168.7.50")
        with self.assertRaises(feedback.TooFast):
            lim.admit("192.168.7.50")
        self.assertEqual(feedback.address_key("fe80::1%eth0"), "fe80::/64")

    def test_an_address_that_cannot_be_read_is_refused(self):
        lim = self.make()
        for junk in ("", "not an address", None, "999.1.1.1"):
            with self.assertRaises(feedback.TooFast):
                lim.admit(junk)
        self.assertEqual(lim.size(), 0)

    def test_no_address_is_ever_written_by_the_store(self):
        d = tempfile.mkdtemp()
        s = feedback.FeedbackStore(os.path.join(d, "feedback.json"), now=lambda: 1790000000, clock=Clock(), log=lambda m: None)
        s.visitors.admit(PHONE)
        s.add(None, "good", "ok", "visitor", None, {"name": "n", "id": "i"}, [])
        s.add(None, "bad", "", "visitor", None, {"name": "n", "id": "i"}, [])       # no session: no gap between the two
        with open(os.path.join(d, "feedback.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(PHONE, raw)
        self.assertEqual([n["role"] for n in s.notes()], ["visitor", "visitor"])
        self.assertEqual(s._last, {}, "no session is remembered for a sender with no login")


class VisitorBase(CareBase):
    def setUp(self):
        super().setUp()
        self.auth.set_pin(PIN)
        self.path = os.path.join(tempfile.mkdtemp(), "feedback.json")
        self.clock = Clock()
        self.api.feedback = feedback.FeedbackStore(self.path, now=lambda: 1790000000, clock=self.clock, log=lambda m: None)
        self.api.node_id = "cd" * 16
        # the real folder of the panel, so the page is the page that ships
        self.real = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, server.WEB_DIR))
        self.real.daemon_threads = True
        threading.Thread(target=self.real.serve_forever, daemon=True).start()
        self.addCleanup(self.real.server_close)
        self.addCleanup(self.real.shutdown)

    def on(self, value=True):
        self.assertEqual(self.h("POST", "/api/feedback/open", {"on": value}, self.full_dev)[0], 200)

    def send(self, body, client=PHONE, device=None):
        return self.h("POST", "/api/feedback/visitor", body, device, client)

    def raw(self, method, path, body=None, headers=None, data=None):
        """A request to the server with the real page folder, and nothing else added (no cookie, no token)."""
        h = {}
        if method == "POST":
            h["Content-Type"] = "application/json"
            h["X-MCTL-Request"] = "1"
        h.update(headers or {})
        payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
        c = http.client.HTTPConnection("127.0.0.1", self.real.server_address[1], timeout=10)
        c.request(method, path, body=payload, headers=h)
        r = c.getresponse()
        text = r.read()
        c.close()
        return r.status, text, r


class SwitchTest(VisitorBase):
    def test_it_is_off_by_default_and_the_post_is_403_and_stores_nothing(self):
        self.assertFalse(self.api.visitors_on())
        st, out = self.send({"face": "good"})
        self.assertEqual(st, 403, out)
        self.assertIn("switched off", out["error"])
        self.assertEqual(self.api.feedback.notes(), [])
        self.assertFalse(os.path.exists(self.path))
        st, out = self.h("GET", "/api/feedback", {}, self.full_dev)
        self.assertEqual((st, out["visitors"]), (200, False))

    def test_the_owner_switches_it_on_and_off_and_it_is_kept(self):
        self.on()
        self.assertTrue(self.api.visitors_on())
        self.assertEqual(self.settings.data["feedback_visitors"], {"on": True})
        self.assertTrue(self.h("GET", "/api/feedback", {}, self.full_dev)[1]["visitors"])
        self.assertEqual(self.send({"face": "good"})[0], 200)
        self.on(False)
        self.assertEqual(self.send({"face": "good"}, client="192.168.7.99")[0], 403)
        with open(self.settings.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["feedback_visitors"], {"on": False})

    def test_only_the_owner_switches_it_and_reads_the_sticker(self):
        for dev in (self.view_dev, self.live_dev, None):
            want = 401 if dev is None else 403
            self.assertEqual(self.h("POST", "/api/feedback/open", {"on": True}, dev)[0], want)
            self.assertEqual(self.h("GET", "/api/feedback/sticker", {}, dev)[0], want)
        self.assertFalse(self.api.visitors_on())

    def test_remote_support_may_not_switch_it_on_or_use_the_route(self):
        remote, _ = self.remote_login()
        from tests.test_support import TUNNEL
        for path, body in (("/api/feedback/open", {"on": True}), ("/api/feedback/visitor", {"face": "good"})):
            self.assertEqual(self.h("POST", path, body, remote, TUNNEL)[0], 403, path)
            self.assertEqual(self.h("POST", path, body, None, TUNNEL)[0], 403, path + " with no login through the tunnel")
        self.assertFalse(self.api.visitors_on())
        self.assertIn(("POST", "/api/feedback/open"), __import__("mctl.support", fromlist=["x"]).REMOTE_DENY)

    def test_only_true_or_false_and_nothing_else(self):
        for body in ({}, {"on": "yes"}, {"on": 1}, {"on": None}, {"on": True, "extra": 1}):
            self.assertEqual(self.h("POST", "/api/feedback/open", body, self.full_dev)[0], 400, body)
        self.assertFalse(self.api.visitors_on())

    def test_a_failed_save_leaves_it_as_it_was(self):
        with mock.patch.object(self.settings, "save", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.h("POST", "/api/feedback/open", {"on": True}, self.full_dev)[0], 500)
        self.assertFalse(self.api.visitors_on())
        self.assertNotIn("feedback_visitors", self.settings.data)

    def test_an_import_file_cannot_switch_it_on(self):
        self.assertIn("feedback_visitors", boxcare.NEVER)
        file = self.export()
        file["settings"]["feedback_visitors"] = {"on": True}
        CareBase.send(self, file)                           # the file is read as far as it is good; this key is not taken from it
        self.assertFalse(self.api.visitors_on())
        self.assertNotIn("feedback_visitors", self.settings.data)
        self.on()
        self.assertNotIn("feedback_visitors", self.export()["settings"], "and it is not written into an export")
        CareBase.send(self, dict(self.export(), settings=dict(self.export()["settings"], feedback_visitors={"on": False})))
        self.assertTrue(self.api.visitors_on(), "an import leaves the node's own switch as it is")


class VisitorSendTest(VisitorBase):
    def test_a_visitor_sends_and_the_node_adds_the_context(self):
        self.on()
        st, out = self.send({"face": "so-so", "text": "the left wall is too bright"})
        self.assertEqual((st, out), (200, {"sent": True}))
        [note] = self.api.feedback.notes()
        self.assertEqual((note["face"], note["role"], note["t"], note["text"]), ("so-so", "visitor", 1790000000, "the left wall is too bright"))
        self.assertEqual(note["node"]["id"], "cd" * 16)
        self.assertTrue(note["node"]["name"])
        self.assertNotIn("screen", note)
        self.assertIsInstance(note["playing"], list)
        out = self.h("GET", "/api/feedback", {}, self.full_dev)[1]
        self.assertEqual(out["notes"][0]["role"], "visitor")

    def test_a_face_alone_is_a_whole_note_and_a_long_text_is_cut(self):
        self.on()
        self.assertEqual(self.send({"face": "good"})[0], 200)
        self.assertNotIn("text", self.api.feedback.notes()[0])
        self.assertEqual(self.send({"face": "bad", "text": "y" * 9000}, client="192.168.7.43")[0], 200)
        self.assertEqual(len(self.api.feedback.notes()[0]["text"]), 1000)

    def test_no_address_and_no_device_is_stored(self):
        self.on()
        self.send({"face": "bad", "text": "slow"}, client="192.168.0.77")
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        for word in ("192.168.0.77", "192.168.0", self.full_dev["id"], PIN, "token_hash"):
            self.assertNotIn(word, raw)
        self.assertNotIn("192.168.0.77", json.dumps(self.api.feedback.notes()))

    def test_strict_bodies(self):
        self.on()
        for body in ({}, {"face": "great"}, {"face": "good", "text": 5}, {"face": "good", "screen": "room"}, {"face": "good", "extra": 1},
                     {"face": ["good"]}, {"face": "good", "text": ["a"]}):
            self.assertEqual(self.send(body, client="192.168.7.%d" % (100 + len(json.dumps(body))))[0], 400, body)
        self.assertEqual(self.api.feedback.notes(), [])

    def test_anyone_who_carries_a_device_is_sent_to_the_other_way(self):
        self.on()
        for dev in (self.view_dev, self.live_dev, self.full_dev, {"id": "midi", "name": "midi", "role": "live"}):
            self.clock.t += 20
            st, out = self.send({"face": "good"}, device=dev)
            self.assertEqual(st, 403, dev["id"])
            self.assertIn("Feedback button", out["error"])
        self.assertEqual(self.api.feedback.notes(), [])

    def test_each_address_one_in_ten_seconds_and_ten_in_an_hour(self):
        self.on()
        self.assertEqual(self.send({"face": "good"})[0], 200)
        self.clock.t += 2
        st, out = self.send({"face": "good"})
        self.assertEqual(st, 429, out)
        self.assertTrue(1 <= out["retry_after"] <= 10)
        self.assertEqual(self.send({"face": "good"}, client="192.168.7.42")[0], 200, "another phone is not held")
        self.assertEqual(len(self.api.feedback.notes()), 2, "the refused send stored nothing")
        for i in range(9):                                  # nine more from the first phone: ten in all
            self.clock.t += 11
            self.assertEqual(self.send({"face": "good"})[0], 200, i)
        self.clock.t += 11
        st, out = self.send({"face": "good"})
        self.assertEqual(st, 429, out)
        self.assertIn("try again later", out["error"])
        self.assertGreater(out["retry_after"], 60)

    def test_the_node_wide_limits_apply_and_do_not_use_up_the_senders_turn(self):
        self.on()
        for i in range(30):
            self.clock.t += 1
            self.assertEqual(self.send({"face": "good"}, client="10.9.%d.%d" % (i // 200, i % 200 + 1))[0], 200, i)
        self.clock.t += 1
        st, out = self.send({"face": "good"}, client="10.8.0.1")
        self.assertEqual(st, 429, out)
        self.assertIn("Lots of feedback just now", out["error"])
        self.assertEqual(len(self.api.feedback.notes()), 30)
        self.assertEqual(self.api.feedback.visitors.size(), 30, "the refused sender was given back his turn")
        self.clock.t += 600
        self.assertEqual(self.send({"face": "good"}, client="10.8.0.1")[0], 200)

    def test_the_visitors_table_stays_bounded_whatever_the_addresses(self):
        self.on()
        self.api.feedback.visitors.most = 16
        self.api.feedback.box_limits = ()                   # the node's own cap is tested above; here only the table
        for i in range(300):
            self.clock.t += 0.01
            self.send({"face": "good"}, client="10.%d.%d.%d" % (i // 65000, (i // 250) % 250, i % 250 + 1))
            self.assertLessEqual(self.api.feedback.visitors.size(), 16)

    def test_a_failed_write_gives_the_senders_turn_back(self):
        self.on()
        with mock.patch("mctl.feedback.os.replace", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.send({"face": "good"})[0], 500)
        self.assertEqual(self.send({"face": "good"})[0], 200, "at once, no wait")

    def test_not_running_is_said_plainly(self):
        self.on()
        self.api.feedback = None
        self.assertEqual(self.send({"face": "good"})[0], 503)

    def test_the_words_are_scrubbed_of_secrets_when_the_owner_reads(self):
        self.on()
        self.send({"face": "bad", "text": "my pin is %s" % PIN})
        self.assertNotIn(PIN, json.dumps(self.h("GET", "/api/feedback", {}, self.full_dev)[1]))


class StickerTest(VisitorBase):
    def sticker(self, host, secure=False, dev=False):
        self.api.request.host, self.api.request.secure = host, secure
        try:
            return self.h("GET", "/api/feedback/sticker", {}, self.full_dev if dev is False else dev)
        finally:
            self.api.request.host, self.api.request.secure = None, False

    def test_the_url_is_the_node_the_panel_was_opened_by(self):
        st, out = self.sticker("nxlx-node-01.local")
        self.assertEqual((st, out["url"], out["on"]), (200, "http://nxlx-node-01.local/feedback", False))
        self.assertTrue(out["svg"].lstrip().startswith("<svg") or "<svg" in out["svg"][:200])
        self.assertEqual(self.sticker("192.168.0.169:8080")[1]["url"], "http://192.168.0.169:8080/feedback")
        self.assertEqual(self.sticker("nxlx-node-01.local", secure=True)[1]["url"], "https://nxlx-node-01.local/feedback")
        self.on()
        self.assertTrue(self.sticker("192.168.0.169")[1]["on"])

    def test_https_is_preferred_when_the_certificate_names_the_host(self):
        class Box:
            def trusted_origin(self, host):
                return "https://nxlx-node-01.local:8443" if host == "nxlx-node-01.local" else None
        self.api.https = Box()
        self.assertEqual(self.sticker("nxlx-node-01.local")[1]["url"], "https://nxlx-node-01.local:8443/feedback")
        self.assertEqual(self.sticker("192.168.0.169")[1]["url"], "http://192.168.0.169/feedback", "a host the certificate does not name stays http")

    def test_a_panel_opened_as_localhost_or_with_a_odd_host_makes_no_sticker(self):
        for host in ("localhost", "localhost:8080", "127.0.0.1", "127.0.0.1:8080", "[::1]:8080", None, "", "a b", "x" * 200):
            st, out = self.sticker(host)
            self.assertEqual(st, 400, host)

    def test_the_code_holds_the_address_and_nothing_else(self):
        st, out = self.sticker("nxlx-node-01.local")
        self.assertNotIn("token", out["url"])
        self.assertNotIn("#", out["url"])
        self.assertEqual(set(out), {"url", "svg", "on"})


class PageTest(VisitorBase):
    def test_the_page_is_served_without_a_login_and_without_a_cookie(self):
        self.on()
        for path in ("/feedback", "/feedback.html", "/feedback?x=1"):
            st, body, r = self.raw("GET", path)
            self.assertEqual(st, 200, path)
            self.assertIn("text/html", r.getheader("Content-Type"))
            self.assertIsNone(r.getheader("Set-Cookie"))
            self.assertEqual(r.getheader("Cache-Control"), "no-store")
            self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
            self.assertIn('data-open="1"', body.decode())
            self.assertIn("Send", body.decode())
        self.assertEqual(self.raw("GET", "/feedback.js")[0], 200)

    def test_when_it_is_off_the_page_says_so_and_offers_nothing_to_send(self):
        st, body, _ = self.raw("GET", "/feedback")
        text = body.decode()
        self.assertEqual(st, 200)
        self.assertIn('data-open="0"', text)
        self.assertIn("switched off on this node", text)
        self.assertEqual(self.raw("HEAD", "/feedback")[0], 200)
        # and the switch turns it on without a restart
        self.on()
        self.assertIn('data-open="1"', self.raw("GET", "/feedback")[1].decode())
        self.on(False)
        self.assertIn('data-open="0"', self.raw("GET", "/feedback")[1].decode())

    def test_the_page_is_three_faces_optional_words_and_send_with_no_remote_files(self):
        text = self.raw("GET", "/feedback")[1].decode()
        self.assertEqual(re.findall(r'data-face="([a-z-]+)"', text), ["good", "so-so", "bad"])
        self.assertIn('maxlength="1000"', text)
        self.assertIn('id="visitorsend"', text)
        self.assertNotRegex(text, r"(?i)(src|href)=\"https?://")
        self.assertNotIn("<style", text)
        self.assertNotRegex(text, r"\son[a-z]+=")
        with open(os.path.join(server.WEB_DIR, "feedback.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("Thanks, sent", js)
        self.assertIn("credentials: 'omit'", js)
        self.assertIn("X-MCTL-Request", js)

    def test_the_page_opens_nothing_else(self):
        self.on()
        for method, path in (("GET", "/api/status"), ("GET", "/api/feedback"), ("GET", "/api/feedback/sticker"), ("GET", "/api/preview.jpg"),
                             ("GET", "/api/system"), ("GET", "/api/media")):
            self.assertEqual(self.raw(method, path)[0], 401, path)
        for path, body in (("/api/feedback", {"face": "good"}), ("/api/feedback/open", {"on": True}), ("/api/pair", {"pin": "000000", "name": "x"})):
            self.assertIn(self.raw("POST", path, body)[0], (401, 403), path)
        self.assertFalse(self.api.feedback.notes())

    def test_the_post_needs_the_same_origin_header_a_json_type_and_a_small_body(self):
        self.on()
        good = {"face": "good"}
        st, body, _ = self.raw("POST", "/api/feedback/visitor", good, headers={"X-MCTL-Request": "0"})
        self.assertEqual(st, 403, "the header must be 1")
        c = http.client.HTTPConnection("127.0.0.1", self.real.server_address[1], timeout=10)
        c.request("POST", "/api/feedback/visitor", body=json.dumps(good).encode(), headers={"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 403, "no header at all")
        st, _, _ = self.raw("POST", "/api/feedback/visitor", good, headers={"Content-Type": "text/plain"})
        self.assertEqual(st, 415)
        st, _, _ = self.raw("POST", "/api/feedback/visitor", good, headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 415)
        st, _, _ = self.raw("POST", "/api/feedback/visitor", good, headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403, "another origin")
        st, _, _ = self.raw("POST", "/api/feedback/visitor", good, headers={"Origin": "http://127.0.0.1:%d" % self.real.server_address[1]})
        self.assertEqual(st, 200, "the page's own origin")
        self.assertEqual(len(self.api.feedback.notes()), 1)
        big = json.dumps({"face": "good", "text": "z" * (feedback.VISITOR_BODY_MAX + 10)}).encode()
        st, _, _ = self.raw("POST", "/api/feedback/visitor", data=big, headers={"Content-Length": str(len(big))})
        self.assertEqual(st, 413)
        self.assertEqual(self.raw("POST", "/api/feedback/visitor", data=b"[1]")[0], 400, "an object is needed")
        self.assertEqual(self.raw("POST", "/api/feedback/visitor", data=b"{nope")[0], 400)
        self.assertEqual(len(self.api.feedback.notes()), 1)
        self.assertLess(feedback.VISITOR_BODY_MAX, server.MAX_BODY)

    def test_over_http_off_is_403_on_is_200_and_the_address_limit_holds(self):
        st, body, _ = self.raw("POST", "/api/feedback/visitor", {"face": "good"})
        self.assertEqual(st, 403)
        self.assertIn("switched off", json.loads(body)["error"])
        self.on()
        st, body, r = self.raw("POST", "/api/feedback/visitor", {"face": "good", "text": "lovely"})
        self.assertEqual((st, json.loads(body)), (200, {"sent": True}))
        self.assertIsNone(r.getheader("Set-Cookie"))
        st, body, r = self.raw("POST", "/api/feedback/visitor", {"face": "good"})
        self.assertEqual(st, 429)
        self.assertTrue(r.getheader("Retry-After"))
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("127.0.0.1", raw)
        self.assertEqual(json.loads(raw)["notes"][0]["role"], "visitor")

    def test_a_paired_device_that_sends_its_token_is_sent_to_the_other_way(self):
        self.on()
        token = self.call("POST", "/api/devices/invite", {"name": "g2", "role": "view"}, token=self.full)[1]["token"]
        st, body, _ = self.raw("POST", "/api/feedback/visitor", {"face": "good"}, headers={"Authorization": "Bearer " + token})
        self.assertEqual(st, 403)
        self.assertEqual(self.api.feedback.notes(), [])


class QrTest(unittest.TestCase):
    def test_the_data_codewords_of_a_known_byte_mode_code(self):
        # ISO/IEC 18004, byte mode: 0100 | count 8 bits | the bytes | 0000 | pad codewords 0xEC 0x11 ... to 16 for version 1-M.
        # "A" is 0100 00000001 01000001 0000 = 0x40 0x14 0x10.
        self.assertEqual(qr._data_codewords(b"A", 1), [0x40, 0x14, 0x10] + [0xEC, 0x11] * 6 + [0xEC])
        self.assertEqual(qr._data_codewords(b"Hi", 1)[:4], [0x40, 0x24, 0x86, 0x90])        # 0100 00000010 01001000 01101001 0000

    def test_a_feedback_address_fits_and_is_a_valid_code(self):
        for url in ("http://192.168.0.169/feedback", "https://nxlx-node-01.local:8443/feedback", "http://[fd00::1234:5678:9abc:def0]:8080/feedback"):
            m = qr.encode(url)
            n = len(m)
            self.assertIn(n, (25, 29, 33, 37), url)
            self.assertTrue(m[n - 8][8])                              # the always-dark module
            self.assertTrue(all(m[6][x] == (x % 2 == 0) for x in range(8, n - 8)))
            self.assertEqual(m, qr.encode(url), "the same address gives the same code")
            self.assertIn("<svg", qr.svg(m))

    @unittest.skipUnless(shutil.which("zbarimg"), "needs the zbar decoder (zbar-tools)")
    def test_a_real_decoder_reads_the_sticker_back(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for url in ("http://192.168.0.169/feedback", "https://nxlx-node-01.local:8443/feedback"):
            path = os.path.join(d, "s.png")
            with open(path, "wb") as f:
                f.write(qr.png(qr.encode(url), scale=8))
            r = subprocess.run(["zbarimg", "--quiet", "--raw", "-Sdisable", "-Sqrcode.enable", path], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), url)


class WebFilesTest(unittest.TestCase):
    def test_the_sticker_and_the_switch_are_in_the_owners_card_and_only_there(self):
        with open(os.path.join(server.WEB_DIR, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("Let people without a login send feedback (QR sticker)", js)
        self.assertIn("Print the QR sticker", js)
        self.assertIn("/api/feedback/open", js)
        self.assertIn("Tell us how the night is going", js)
        self.assertIn("Scan to send feedback", js)
        with open(os.path.join(server.WEB_DIR, "room.js"), encoding="utf-8") as f:
            self.assertNotIn("feedback/visitor", f.read())


if __name__ == "__main__":
    unittest.main()
