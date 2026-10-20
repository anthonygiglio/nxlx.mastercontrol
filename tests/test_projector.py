# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from pvj import osc, projector, scheduler
from tests.test_roles import LAN, TUNNEL, RolesBase
from tests.test_server import ServerBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class FakeProjector:
    """A PJLink class 1 projector on 127.0.0.1, written from the standard ("PJLink Specifications" version 1.04,
    JBMIA, 2013-12-10), not from our client. Section numbers below are that document's.

    - 2.1, 2.2: a command is "%1" + four characters + a space + a parameter of up to 128 bytes + CR alone; the
      answer is "%1" + the same four characters + "=" + a parameter + CR. The command body is case-insensitive.
    - 2.3, 2.4: OK, ERR1 (undefined command), ERR2 (out of parameter), ERR3 (unavailable time), ERR4 (failure).
    - 4.1, 4.2 POWR: 0 standby, 1 on, 2 cooling, 3 warm-up; off => warm-up => on => cooling => off.
    - 4.3, 4.4 INPT: a type 1 to 5 (RGB, VIDEO, DIGITAL, STORAGE, NETWORK) and a number 1 to 9; ERR2 for an input
      that does not exist, ERR3 in standby and such.
    - 4.5, 4.6 AVMT: 11/10 video, 21/20 audio, 31/30 both; the query answers 11, 21, 31 or 30 only; ERR2 for a
      video-only or audio-only mute on a product without one; ERR3 in standby.
    - 4.7 ERST: six digits, fan, lamp, temperature, cover open, filter, other; 0 none, 1 warning, 2 error.
    - 4.8 LAMP: "hours on" per lamp, up to 8, hours 0 to 99999; ERR1 on a display without a lamp.
    - 4.9 INST: the inputs, separated by spaces. 4.10 NAME: UTF-8, 0 to 64 characters. 4.11 to 4.13 INF1, INF2,
      INFO: ASCII, 0 to 32 characters. 4.14 CLSS: "1".
    - 5: "PJLINK 0", or "PJLINK 1 <8 hex digits>" and then the first command is prefixed with the 32 character
      MD5 of the random number followed by the password; a wrong one is answered "PJLINK ERRA". Later commands
      on the same connection may leave the digest out (5.3). No command for 30 seconds ends the connection.
    - 6: a command that does not meet the format gets no answer at all.

    `slow`: power changes pass through warm-up and cooling and stay there until finish() is called (a test's
    stand-in for the minute a real projector takes). `standby_answers` False: INST, NAME and the other
    information queries are "unavailable" in standby, which 2.4 allows. `lowercase`: answers in lower case.
    `mute_off`: what AVMT ? answers with nothing muted; 4.6 names 30, its table of values also allows 10 and 20."""

    def __init__(self, password="", inputs=("11", "31", "32"), lamps=(1234,), separate_mute=True, slow=False,
                 standby_answers=True, lowercase=False, name="Fake projector"):
        self.password, self.inputs, self.lamps, self.separate_mute = password, list(inputs), list(lamps), separate_mute
        self.slow, self.standby_answers, self.lowercase, self.name = slow, standby_answers, lowercase, name
        self.maker, self.model, self.info = "NXLX Test Works", "FP-1", "fake, firmware 0.1"
        self.power, self.input, self.video_mute, self.audio_mute, self.errors, self.fault = "0", self.inputs[0], False, False, "000000", False
        self.received, self.raw, self.times, self.connections, self.mute_off = [], [], [], 0, "30"
        self.guard = threading.Lock()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def read_line(self):
                buf = b""
                while not buf.endswith(b"\r") and len(buf) < 300:
                    c = self.rfile.read(1)
                    if not c:
                        return None
                    buf += c
                outer.raw.append(buf)
                return buf[:-1].decode("utf-8", "replace")

            def handle(self):
                rnd = "498e4a67"
                self.request.settimeout(30)
                with outer.guard:
                    outer.connections += 1
                try:
                    self.wfile.write(("PJLINK 1 %s\r" % rnd if outer.password else "PJLINK 0\r").encode())
                    first = True
                    while True:
                        line = self.read_line()
                        if line is None:
                            return
                        outer.received.append(line)
                        outer.times.append(time.monotonic())
                        if outer.password:
                            want = hashlib.md5((rnd + outer.password).encode()).hexdigest()
                            if first and not line.startswith(want):
                                self.wfile.write(b"PJLINK ERRA\r")
                                continue
                            if line.startswith(want):
                                line = line[32:]
                        first = False
                        m = re.fullmatch(r"%1([A-Za-z0-9]{4}) (.{1,128})", line)
                        if not m:
                            continue                       # not a command line: no answer (chapter 6)
                        cmd = m.group(1).upper()
                        with outer.guard:
                            answer = "%%1%s=%s\r" % (cmd, outer.answer(cmd, m.group(2)))
                        self.wfile.write((answer.lower() if outer.lowercase and cmd in ("POWR", "CLSS", "INF1") else answer).encode("utf-8"))
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def mute(self):
        """What AVMT ? answers (4.6)."""
        return "31" if self.video_mute and self.audio_mute else "11" if self.video_mute else "21" if self.audio_mute else self.mute_off

    def finish(self):
        """The warm-up or the cooling is over."""
        with self.guard:
            self.power = {"3": "1", "2": "0"}.get(self.power, self.power)

    def answer(self, cmd, val):
        on = self.power == "1"
        if self.fault and cmd != "ERST":
            return "ERR4" if cmd in ("POWR", "INPT", "AVMT", "LAMP", "INST", "NAME", "INF1", "INF2", "INFO", "CLSS") else "ERR1"
        if cmd == "POWR":
            if val == "?":
                return self.power
            if val not in ("0", "1"):
                return "ERR2"
            if self.power in ("2", "3"):
                return "ERR3"                              # in transition
            if val != self.power:
                self.power = (("3" if val == "1" else "2") if self.slow else val)
                if val == "0":
                    self.video_mute = self.audio_mute = False
            return "OK"
        if cmd == "INPT":
            if not on:
                return "ERR3"
            if val == "?":
                return self.input
            if not re.fullmatch(r"[1-5][1-9]", val) or val not in self.inputs:
                return "ERR2"
            self.input = val
            return "OK"
        if cmd == "AVMT":
            if val != "?" and val not in ("10", "11", "20", "21", "30", "31"):
                return "ERR2"
            if not on:
                return "ERR3"
            if val == "?":
                return self.mute
            if val[0] in "12" and not self.separate_mute:
                return "ERR2"
            if val[0] in "13":
                self.video_mute = val[1] == "1"
            if val[0] in "23":
                self.audio_mute = val[1] == "1"
            return "OK"
        if cmd not in ("ERST", "LAMP", "INST", "NAME", "INF1", "INF2", "INFO", "CLSS"):
            return "ERR1"
        if val != "?":
            return "ERR2"
        if cmd == "ERST":
            return self.errors
        if cmd == "LAMP":
            return " ".join("%d %d" % (h, 1 if on else 0) for h in self.lamps) if self.lamps else "ERR1"
        if not on and not self.standby_answers:
            return "ERR3"
        return {"INST": " ".join(self.inputs), "NAME": self.name, "INF1": self.maker, "INF2": self.model,
                "INFO": self.info, "CLSS": "1"}[cmd]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def raw_server(test, handle):
    """A TCP server on loopback that is not a projector: handle(rfile, wfile) per connection. Returns its port."""
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                handle(self.rfile, self.wfile)
            except OSError:
                pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return srv.server_address[1]


def poll_threads():
    return [t for t in threading.enumerate() if t.name == "projector-poll"]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


LOOPBACK_OK = mock.patch.object(projector, "PRIVATE", projector.PRIVATE + [ipaddress.ip_network("127.0.0.0/8")])


class PrivateNetworkTest(unittest.TestCase):
    def test_only_private_addresses(self):
        for good in ("192.168.0.50", "10.1.2.3", "172.16.0.9", "169.254.10.20", "fe80::1"):
            self.assertEqual(projector.private_address(good), good)
        for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "172.32.0.1", "::1", "", None, "a b", "http://x", "projector;rm"):
            with self.assertRaises(projector.ProjectorError, msg=bad):
                projector.private_address(bad)

    def test_names_dns_cannot_encode_and_the_metadata_address_are_clear_errors(self):
        for bad in ("a..b", "..", "a" * 64 + ".lan", "169.254.169.254", "fd00:ec2::254", "FD00:EC2:0:0::254"):
            with self.assertRaises(projector.ProjectorError, msg=bad):
                projector.private_address(bad)
        for ip in ("fd00:ec2::254", "169.254.169.254"):                    # a name that leads there is refused too
            with self.assertRaises(projector.ProjectorError, msg=ip):
                projector.private_address("meta.lan", lambda h, p, proto=0: [(0, 0, 0, "", (ip, p))])
        self.assertEqual(projector.private_address("fd00:ec2::253"), "fd00:ec2::253")      # its neighbour is an ordinary private address

    def test_a_name_must_resolve_to_a_private_address(self):
        ok = lambda h, p, proto=0: [(0, 0, 0, "", ("192.168.0.60", p))]
        public = lambda h, p, proto=0: [(0, 0, 0, "", ("93.184.216.34", p))]
        self.assertEqual(projector.private_address("beamer.lan", ok), "192.168.0.60")
        with self.assertRaises(projector.ProjectorError):
            projector.private_address("example.com", public)

    def test_validate(self):
        e = projector.validate({"name": " Main ", "host": "192.168.0.50", "password": "JBMIAProjectorLink"})
        self.assertEqual((e["name"], e["port"], len(e["id"])), ("Main", 4352, 8))
        for bad in ({"host": "192.168.0.50", "name": ""}, {"host": "192.168.0.50", "port": 0}, {"host": "192.168.0.50", "password": "has space"},
                    {"host": "192.168.0.50", "password": "x" * 33}, {"host": 5}, [], {"host": "192.168.0.50", "port": True}):
            with self.assertRaises(projector.ProjectorError, msg=str(bad)):
                projector.validate(bad)


class PJLinkTest(unittest.TestCase):
    def test_power_state_mute_without_and_with_password(self):
        with LOOPBACK_OK:
            for pw in ("", "secret1"):
                fake = FakeProjector(pw)
                self.addCleanup(fake.close)
                link = projector.PJLink("127.0.0.1", fake.port, pw)
                self.assertEqual(link.state(), "off")
                link.power(True)
                self.assertEqual((fake.power, link.state()), ("1", "on"))
                link.mute(True)
                self.assertEqual(fake.mute, "31")
                if pw:
                    self.assertTrue(all(len(r) > 32 for r in fake.received))            # every command carries the digest
                    self.assertNotIn(pw, "".join(fake.received))                        # the password itself never travels

    def test_wrong_password_unknown_command_and_nobody_home(self):
        with LOOPBACK_OK:
            fake = FakeProjector("right")
            self.addCleanup(fake.close)
            with self.assertRaises(projector.ProjectorError) as cm:
                projector.PJLink("127.0.0.1", fake.port, "wrong").power(True)
            self.assertIn("password", str(cm.exception))
            with self.assertRaises(projector.ProjectorError):
                projector.PJLink("127.0.0.1", fake.port, "").power(True)                # needs a password
            fake.lamps = []                                                             # a display without a lamp (4.8)
            for unknown in ("LAMP ?", "FREZ ?"):                                        # FREZ is class 2
                with self.assertRaises(projector.ProjectorError) as cm:
                    projector.PJLink("127.0.0.1", fake.port, "right").command(unknown)
                self.assertIn("does not know", str(cm.exception))
                self.assertEqual(cm.exception.code, "ERR1")
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
            s.close()
            with self.assertRaises(projector.ProjectorError):
                projector.PJLink("127.0.0.1", dead, "", timeout=1).power(True)

    def test_a_slow_trickle_is_cut_off(self):
        import time as _t

        class Slow(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    for c in b"PJLINK 0":
                        self.wfile.write(bytes([c]))
                        _t.sleep(0.3)
                except OSError:
                    pass
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Slow)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        start = _t.monotonic()
        with LOOPBACK_OK, self.assertRaises(projector.ProjectorError):
            projector.PJLink("127.0.0.1", srv.server_address[1], timeout=0.5).power(True)
        self.assertLess(_t.monotonic() - start, 2.0)                    # each byte was quick; the whole line was not

    def test_one_command_at_a_time_per_projector(self):
        with LOOPBACK_OK:
            fake = FakeProjector()
            self.addCleanup(fake.close)
            live, peak, guard = [0], [0], threading.Lock()
            real = socket.create_connection

            def counting_connect(addr, timeout=None):
                with guard:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                s = real(addr, timeout=timeout)

                class Counted:
                    def __getattr__(self, name):
                        return getattr(s, name)

                    def close(self):
                        with guard:
                            live[0] -= 1
                        s.close()
                return Counted()
            links = [projector.PJLink("127.0.0.1", fake.port, connect=counting_connect) for _ in range(6)]
            ts = [threading.Thread(target=l.state) for l in links]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(peak[0], 1)

    def test_connect_shares_the_deadline(self):
        seen = []

        def connect(addr, timeout=None):
            seen.append(timeout)
            raise OSError("refused")
        with self.assertRaises(projector.ProjectorError):
            projector.PJLink("192.168.0.9", timeout=3, connect=connect).power(True)
        self.assertLessEqual(seen[0], 3)

    def test_something_that_is_not_a_projector(self):
        class Web(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        srv = socketserver.TCPServer(("127.0.0.1", 0), Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with LOOPBACK_OK, self.assertRaises(projector.ProjectorError):
            projector.PJLink("127.0.0.1", srv.server_address[1]).power(True)


class Phase1Test(unittest.TestCase):
    """Input, the separate mutes, lamp hours, warnings and the details, against the fake from the standard."""

    def setUp(self):
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)

    def pair(self, **kw):
        fake = FakeProjector(**kw)
        self.addCleanup(fake.close)
        return fake, projector.PJLink("127.0.0.1", fake.port, kw.get("password", ""))

    def test_identify_reads_who_the_projector_is(self):
        fake, link = self.pair(name="Saal 1 \u00e9\u00df", lowercase=True, password="pw")
        self.assertEqual(link.identify(), {"class": "1", "name": "Saal 1 \u00e9\u00df", "maker": "nxlx test works", "model": "FP-1",
                                           "info": "fake, firmware 0.1", "inputs": ["11", "31", "32"]})
        fake.name = ""                                    # "enter CR directly after =" (4.10)
        self.assertEqual(link.identify()["name"], "")
        fake.name = "x" * 64 + "\x07tail"
        self.assertEqual(link.identify()["name"], "x" * 64)

    def test_identify_in_standby_keeps_what_it_got_and_an_unplugged_one_costs_one_try(self):
        fake, link = self.pair(standby_answers=False)
        self.assertEqual(link.identify(), {"class": None, "name": None, "maker": None, "model": None, "info": None, "inputs": None})
        tries = []

        def refused(addr, timeout=None):
            tries.append(addr)
            raise OSError("refused")
        with self.assertRaises(projector.ProjectorError) as cm:
            projector.PJLink("192.168.0.9", connect=refused).identify()
        self.assertEqual((len(tries), cm.exception.code), (1, "unreachable"))

    def test_input_from_the_projectors_own_list(self):
        fake, link = self.pair(slow=True)
        self.assertEqual(link.inputs(), ["11", "31", "32"])
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("31")                          # standby: unavailable time
        self.assertEqual(cm.exception.code, "ERR3")
        link.power(True)
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("31")                          # warming up
        self.assertEqual(cm.exception.code, "ERR3")
        fake.finish()
        link.set_input("31")
        self.assertEqual((fake.input, link.input()), ("31", "31"))
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("59")                          # a valid number this projector does not have
        self.assertEqual((cm.exception.code, fake.input), ("ERR2", "31"))
        before = len(fake.received)
        for bad in ("61", "3", "31\r%1POWR 0", 31, None, "1A"):
            with self.assertRaises(projector.ProjectorError, msg=str(bad)):
                link.set_input(bad)
        self.assertEqual(len(fake.received), before)      # never sent

    def test_picture_and_sound_mute_apart_and_together(self):
        fake, link = self.pair()
        with self.assertRaises(projector.ProjectorError) as cm:
            link.mute(True, "picture")                    # standby
        self.assertEqual(cm.exception.code, "ERR3")
        link.power(True)
        link.mute(True, "picture")
        self.assertEqual((fake.mute, link.mute_state()), ("11", {"picture": True, "sound": False}))
        link.mute(True, "sound")
        self.assertEqual((fake.mute, link.mute_state()), ("31", {"picture": True, "sound": True}))
        link.mute(False, "picture")
        self.assertEqual((fake.mute, link.mute_state()), ("21", {"picture": False, "sound": True}))
        link.mute(False)
        self.assertEqual((fake.mute, link.mute_state()), ("30", {"picture": False, "sound": False}))
        self.assertEqual([r[2:] for r in fake.received if r[2:].startswith("AVMT 1") or r[2:].startswith("AVMT 2")],
                         ["AVMT 11", "AVMT 11", "AVMT 21", "AVMT 10"])         # the first one was refused in standby
        fake.separate_mute = False
        for what in ("picture", "sound"):
            with self.assertRaises(projector.ProjectorError) as cm:
                link.mute(True, what)
            self.assertIn("separately", str(cm.exception))
        link.mute(True)
        self.assertEqual(fake.mute, "31")

    def test_lamp_hours_and_warnings(self):
        fake, link = self.pair()
        self.assertEqual(link.lamps(), [{"hours": 1234, "on": False}])
        link.power(True)
        fake.lamps = [0, 99999]
        self.assertEqual(link.lamps(), [{"hours": 0, "on": True}, {"hours": 99999, "on": True}])
        fake.lamps = []
        with self.assertRaises(projector.ProjectorError) as cm:
            link.lamps()
        self.assertEqual(cm.exception.code, "ERR1")
        self.assertEqual(set(link.warnings().values()), {"ok"})
        fake.errors = "012012"
        self.assertEqual(link.warnings(), {"fan": "ok", "lamp": "warning", "temperature": "error", "cover": "ok",
                                           "filter": "warning", "other": "error"})
        fake.fault = True
        with self.assertRaises(projector.ProjectorError) as cm:
            link.state()
        self.assertEqual(cm.exception.code, "ERR4")

    def test_every_line_ends_with_cr_alone_and_one_command_per_connection(self):
        fake, link = self.pair(password="pw")
        link.power(True)
        link.identify()
        link.set_input("32")
        link.mute(True, "sound")
        link.lamps(), link.warnings(), link.input(), link.mute_state()
        self.assertEqual(len(fake.raw), fake.connections)
        self.assertEqual(len(fake.raw), 13)
        for raw in fake.raw:
            self.assertTrue(raw.endswith(b"\r") and b"\n" not in raw and raw.count(b"\r") == 1, raw)
            self.assertRegex(raw.decode(), r"^[0-9a-f]{32}%1[A-Z0-9]{4} ")       # the digest on every one
        self.assertNotIn(b"pw", b"".join(fake.raw))                              # the password itself never travels

    def test_a_public_address_is_refused_before_every_new_command(self):
        def connect(addr, timeout=None):
            raise AssertionError("connected to %s" % (addr,))
        public = lambda h, p, proto=0: [(0, 0, 0, "", ("93.184.216.34", p))]
        for host, resolve in (("8.8.8.8", socket.getaddrinfo), ("beamer.example", public)):
            link = projector.PJLink(host, connect=connect, resolve=resolve)
            for call in (link.identify, link.inputs, link.input, lambda: link.set_input("31"), lambda: link.mute(True, "picture"),
                         link.mute_state, link.lamps, link.warnings):
                with self.assertRaises(projector.ProjectorError) as cm:
                    call()
                self.assertIn("private", str(cm.exception))

    def test_answers_that_are_not_in_the_standard_are_refused(self):
        fake, link = self.pair()
        link.power(True)
        for attr, value, call in (("errors", "00000", link.warnings), ("errors", "000003", link.warnings),
                                  ("lamps", [123456], link.lamps), ("input", "6A", link.input)):
            old = getattr(fake, attr)
            setattr(fake, attr, value)
            with self.assertRaises(projector.ProjectorError, msg=attr):
                call()
            setattr(fake, attr, old)
        fake.inputs = ["11", "zz", "31", "31", "6A"] + ["2%d" % (i % 9 + 1) for i in range(60)]
        got = link.inputs()
        self.assertEqual(got[:2], ["11", "31"])
        self.assertTrue(all(re.fullmatch(r"[1-5][1-9]", c) for c in got) and len(got) == len(set(got)) <= 50)

    def test_the_mute_query_may_also_answer_10_or_20(self):
        fake, link = self.pair()
        link.power(True)
        for off in ("30", "10", "20"):
            fake.mute_off = off
            self.assertEqual(link.mute_state(), {"picture": False, "sound": False}, off)
        fake.mute_off = "40"
        with self.assertRaises(projector.ProjectorError) as cm:
            link.mute_state()
        self.assertEqual(cm.exception.code, "odd")

    def test_a_set_command_is_only_done_when_the_projector_says_ok(self):
        answers = {"POWR": b"%1POWR=1\r", "AVMT": b"%1AVMT=31\r", "INPT": b"%1INPT=\r"}

        def handle(rfile, wfile):
            wfile.write(b"PJLINK 0\r")
            line = b""
            while not line.endswith(b"\r"):
                line += rfile.read(1)
            wfile.write(answers[line[2:6].decode()])
        link = projector.PJLink("127.0.0.1", raw_server(self, handle), timeout=1)
        for call in (lambda: link.power(True), lambda: link.mute(True), lambda: link.mute(False, "sound"), lambda: link.set_input("31")):
            with self.assertRaises(projector.ProjectorError) as cm:
                call()
            self.assertEqual(cm.exception.code, "odd")
        fake, link = self.pair(lowercase=True)            # "%1powr=ok" is OK: the case is free
        link.power(True)
        self.assertEqual(fake.power, "1")

    def test_a_line_that_never_ends_or_is_cut_off_is_refused(self):
        def then(*payloads, **kw):
            def handle(rfile, wfile):
                wfile.write(b"PJLINK 0\r")
                wfile.flush()
                rfile.read(1)
                for pay in payloads:
                    wfile.write(pay)
                    wfile.flush()
                if kw.get("hang"):
                    time.sleep(3)
            return projector.PJLink("127.0.0.1", raw_server(self, handle), timeout=0.4)
        for name, link, code in (("100 kB and no CR", then(b"%1NAME=" + b"A" * 100000), "odd"),
                                 ("cut off before the CR", then(b"%1NAME=Hall"), "odd"),
                                 ("exactly at the limit, no CR", then(b"%1NAME=" + b"A" * 293), "odd"),
                                 ("closed without a word", then(), "unreachable"),
                                 ("silent after the greeting", then(hang=True), "unreachable")):
            start = time.monotonic()
            with self.assertRaises(projector.ProjectorError, msg=name) as cm:
                link.command("NAME ?")
            self.assertEqual(cm.exception.code, code, name)
            self.assertLess(time.monotonic() - start, 1.5, name)

        def no_greeting_end(rfile, wfile):
            wfile.write(b"PJLINK 0")                      # and then it closes
        with self.assertRaises(projector.ProjectorError):
            projector.PJLink("127.0.0.1", raw_server(self, no_greeting_end), timeout=0.4).command("NAME ?")
        link = then(b"%1NAME=\xff\xfe<b>Hall</b>\x1b[31m\x00\xe2\x80\xae one\r")       # not UTF-8, markup, escapes, a bidi override
        self.assertEqual(projector.clean_text(link.command("NAME ?"), 64), "<b>Hall</b>[31m one")
        self.assertEqual(then(b"%1NAME=" + b"A" * 128 + b"\r").command("NAME ?"), "A" * 128)       # the longest the standard allows

    def test_one_device_under_two_spellings_shares_one_connection_and_locks_are_not_kept(self):
        fake = FakeProjector()
        self.addCleanup(fake.close)
        orig = fake.answer
        fake.answer = lambda c, v: (time.sleep(0.2), orig(c, v))[1]
        live, peak, guard, real = [0], [0], threading.Lock(), socket.create_connection

        def counting(addr, timeout=None):
            with guard:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            sock = real(addr, timeout=timeout)

            class Counted:
                def __getattr__(self, name):
                    return getattr(sock, name)

                def close(self):
                    with guard:
                        live[0] -= 1
                    sock.close()
            return Counted()
        by_name = lambda h, p, proto=0: [(0, 0, 0, "", ("127.0.0.1", p))]
        links = [projector.PJLink("127.0.0.1", fake.port, connect=counting),
                 projector.PJLink("beamer.lan", fake.port, connect=counting, resolve=by_name),
                 projector.PJLink("BEAMER.lan", fake.port, connect=counting, resolve=by_name)]
        ts = [threading.Thread(target=l.state) for l in links]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual((peak[0], len(fake.received)), (1, 3))
        self.assertEqual(projector._busy, {})             # no lock is kept for a projector nobody is talking to

    def test_the_deadline_covers_the_wait_for_an_earlier_command(self):
        fake = FakeProjector()
        self.addCleanup(fake.close)
        orig = fake.answer
        # The earlier command is held for 3 seconds, so "busy" at the second one's own deadline (0.6 s) is far from
        # "busy" after waiting for the first (a limit of 1.0 against a hold of 1.2 left no room on a busy runner).
        fake.answer = lambda c, v: (time.sleep(3.0), orig(c, v))[1]
        first = threading.Thread(target=projector.PJLink("127.0.0.1", fake.port, timeout=2).state)
        first.start()
        self.assertTrue(wait_for(lambda: fake.received))
        start = time.monotonic()
        with self.assertRaises(projector.ProjectorError) as cm:
            projector.PJLink("127.0.0.1", fake.port, timeout=0.3).state()      # 0.6 seconds in all, the wait included
        took = time.monotonic() - start
        self.assertEqual(cm.exception.code, "busy")
        self.assertIn("busy", str(cm.exception))
        self.assertTrue(0.4 < took < 2.0, took)
        self.assertTrue(first.is_alive())                 # it did not wait for the earlier command to end
        first.join()
        self.assertEqual(len(fake.received), 1)           # the second was never sent
        self.assertEqual(projector._busy, {})

    def test_a_cancelled_link_sends_nothing(self):
        fake, link = self.pair()
        link.cancel = threading.Event()
        self.assertEqual(link.state(), "off")
        link.cancel.set()
        for call in (link.state, link.identify, lambda: link.power(True)):
            with self.assertRaises(projector.ProjectorError) as cm:
                call()
            self.assertEqual(cm.exception.code, "stopped")
        self.assertEqual((len(fake.received), fake.power), (1, "0"))

    def test_labels(self):
        self.assertEqual(projector.validate_label(["31"], "31", " Matrix "), "Matrix")
        self.assertEqual(projector.validate_label(["31"], "31", ""), "")
        for code, label in (("32", "Box"), ("31", "x" * 25), ("31", "a\nb"), ("31", 5), (31, "Box"), ("31\n", "Box")):
            with self.assertRaises(projector.ProjectorError, msg=str((code, label))):
                projector.validate_label(["31"], code, label)
        self.assertEqual(projector.input_name("31"), "Digital 1")

    def test_control_and_format_characters_are_kept_out_of_text(self):
        hidden = ("\u202e", "\u2028", "\u2029", "\x85", "\u200b", "\u2066", "\x1b", "\x00", "\ufeff", "\x7f")
        for ch in hidden:
            self.assertEqual(projector.clean_text("a" + ch + "b", 9), "ab", repr(ch))
            with self.assertRaises(projector.ProjectorError, msg=repr(ch)):
                projector.validate_label(["31"], "31", "a" + ch + "b")
            with self.assertRaises(projector.ProjectorError, msg=repr(ch)):
                projector.validate({"host": "192.168.0.50", "name": "a" + ch + "b"})
        self.assertEqual(projector.validate_label(["31"], "31", "Saal \u00e9 <1>"), "Saal \u00e9 <1>")       # plain text, accents too
        fake, link = self.pair(name="Hall\u202e \u2028one\u200b")
        self.assertEqual(link.identify()["name"], "Hall one")


class MonitorTest(unittest.TestCase):
    """The background status: per projector, staggered, stopped with the module, never more than one thread each."""

    def setUp(self):
        from pvj.settings import Settings
        from pvj.modules import Registry
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")
        self.settings = Settings(self.path)
        self.settings.load()
        self.registry = Registry(self.settings, "x86")
        self.registry.set_enabled("projector", True)
        outer = self

        class Api:
            settings, registry = self.settings, self.registry

            def _pjlink(self, e):
                return projector.PJLink(e["host"], e["port"], e["password"], timeout=outer.timeout)
        self.timeout, self.logged = 1.0, []
        self.mon = projector.Monitor(Api(), interval=0.15, changing=0.05, stagger=0.0, retry_for=1.5, retry_every=0.1, log=self.logged.append)
        self.addCleanup(self.mon.stop, True)

    def add(self, fake=None, port=None, **kw):
        if fake is None and port is None:
            fake = FakeProjector(**kw)
        if fake is not None:
            self.addCleanup(fake.close)
        e = projector.validate({"name": "P%d" % len(self.settings.data["projectors"]), "host": "127.0.0.1", "port": port or fake.port,
                                "password": kw.get("password", "")})
        with self.settings.lock:
            self.settings.data["projectors"] = self.settings.data["projectors"] + [e]
            self.settings.save()
        return fake, e

    def entry(self, pid):
        return [p for p in self.settings.data["projectors"] if p["id"] == pid][0]

    def power(self, pid):
        return self.mon.status(pid).get("power")

    def test_the_status_follows_the_projector_without_being_asked(self):
        fake, e = self.add(slow=True, password="pw")
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        st = self.mon.status(e["id"])
        self.assertEqual((st["ok"], st["input"], st["mute"], st["lamps"], st["warnings"]["fan"]),
                         (True, None, None, [{"hours": 1234, "on": False}], "ok"))
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        d = self.entry(e["id"])["details"]
        self.assertEqual((d["maker"], d["model"], d["class"], d["inputs"]), ("NXLX Test Works", "FP-1", "1", ["11", "31", "32"]))
        self.mon.api._pjlink(e).power(True)
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "warming up"))
        fake.finish()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "on"))
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["input"] == "11"))
        self.assertEqual(self.mon.status(e["id"])["mute"], {"picture": False, "sound": False})
        self.mon.api._pjlink(e).power(False)
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "cooling down"))
        fake.finish()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        with open(self.path) as f:
            saved = f.read()
        self.assertNotIn("power", saved)                  # the status lives in memory only
        self.assertNotIn("warming", saved)

    def test_switching_the_module_on_and_off_never_piles_up_threads(self):
        fakes = [self.add()[0] for _ in range(3)]
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        self.add(port=dead.getsockname()[1])              # nobody listens there
        dead.close()
        peak = 0
        for i in range(20):
            self.registry.set_enabled("projector", i % 2 == 0)
            self.mon.apply()
            peak = max(peak, len(poll_threads()), len(self.mon.threads()))      # every thread of that name, tracked or not
            time.sleep(0.01 * (i % 3))
        self.assertLessEqual(peak, 4)                     # one per projector, also while an old one is still ending
        self.registry.set_enabled("projector", True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(self.mon.threads()) == 4 and all(self.mon.status(p["id"]).get("ok") is not None
                                                                              for p in self.settings.data["projectors"])))
        self.assertLessEqual(len([t for t in threading.enumerate() if t.name == "projector-poll"]), 4)
        self.registry.set_enabled("projector", False)
        self.mon.apply()
        start = time.monotonic()
        self.assertTrue(wait_for(lambda: not [t for t in threading.enumerate() if t.name == "projector-poll"], 1.5))
        self.assertLess(time.monotonic() - start, 1.5)
        asked = [len(f.received) for f in fakes]
        time.sleep(0.5)
        self.assertEqual([len(f.received) for f in fakes], asked)         # and nothing is asked any more
        self.assertEqual(self.mon.status(self.settings.data["projectors"][0]["id"]).get("power"), None)

    def test_a_check_asked_for_during_a_check_is_not_lost(self):
        fake, e = self.add()
        gate, calls, real = threading.Event(), [], self.mon.api._pjlink

        class Slow:
            def __init__(self, link):
                self.link = link

            def __getattr__(self, name):
                return getattr(self.link, name)

            def state(self):
                calls.append(1)
                if len(calls) == 1:
                    gate.wait(5)                          # the first check is still running...
                return self.link.state()
        self.mon.api._pjlink = lambda entry: Slow(real(entry))
        self.mon.interval = 60
        self.mon.apply()
        self.assertTrue(wait_for(lambda: calls))
        self.mon.poke(e["id"])                            # ...when a button asks for a fresh one
        gate.set()
        self.assertTrue(wait_for(lambda: len(calls) >= 2, 1.0))
        time.sleep(0.3)
        self.assertEqual(len(calls), 2)                   # one more, not a busy loop

    def test_a_worker_that_stops_without_apply_also_ends(self):
        fake, e = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        self.registry.set_enabled("projector", False)     # switched off behind its back
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        self.assertEqual(self.mon.status(e["id"]).get("power"), None)

    def test_a_removed_projector_is_let_go_and_details_do_not_bring_it_back(self):
        fake, e = self.add()
        fake2, e2 = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off" and self.power(e2["id"]) == "off"))
        with self.settings.lock:
            self.settings.data["projectors"] = [p for p in self.settings.data["projectors"] if p["id"] != e["id"]]
            self.settings.save()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(self.mon.threads()) == 1, 1.5))
        self.assertEqual(self.mon.status(e["id"]).get("power"), None)
        self.assertIsNone(self.mon.identify(e))           # answered, but there is nowhere to keep it
        self.assertEqual([p["id"] for p in self.settings.data["projectors"]], [e2["id"]])
        self.mon.stop(final=True)
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        self.mon.apply()
        self.assertEqual(self.mon.threads(), [])          # closed for good

    def test_an_unreachable_projector_is_reported_not_guessed(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        _, e = self.add(port=s.getsockname()[1])
        s.close()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"]).get("ok") is False))
        self.assertIn("cannot reach", self.mon.status(e["id"])["error"])
        row = self.mon.health()[0]
        self.assertEqual((row["state"], row["name"]), ("unknown", "P0"))
        self.assertIn("No answer", row["text"])
        self.assertIsNone(self.entry(e["id"]).get("details"))

    def test_projectors_are_not_all_asked_at_once(self):
        # Three projectors, 0.4 s apart: the first is checked at once, the second no sooner than 0.4 s after the
        # checks were started, the third no sooner than 0.8 s. Each is measured from that start. (It used to be the
        # gap between the first two, "more than 0.3 s": a projector is asked who it is before its first check, and
        # on a loaded runner that brought the two within 0.295 and 0.248 s in CI.) Here each already has its
        # details, so nobody is asked who it is and the first one's check has nothing to wait for. One delay for
        # all, or for all but the first, does not pass.
        self.mon.stagger = 0.4
        fakes = [self.add()[0] for _ in range(3)]
        with self.settings.lock:
            self.settings.data["projectors"] = [dict(p, details={"name": "known", "read": 1}) for p in self.settings.data["projectors"]]
        started = time.monotonic()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: all(any("POWR ?" in r for r in f.received) for f in fakes), 6))
        first = lambda f: [t for r, t in zip(f.received, f.times) if "POWR ?" in r][0] - started
        a, b, c = (first(f) for f in fakes)
        self.assertEqual([r for f in fakes for r in f.received if "NAME" in r], [])     # nobody was asked who it is
        self.assertLess(a, 0.4)
        self.assertGreaterEqual(b, 0.4)
        self.assertGreaterEqual(c, 0.8)

    def test_the_input_list_is_read_once_the_projector_is_on(self):
        fake, e = self.add(standby_answers=False, slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off" and self.entry(e["id"]).get("details")))
        self.assertIsNone(self.entry(e["id"])["details"]["inputs"])
        self.mon.api._pjlink(e).power(True)
        fake.finish()
        self.assertTrue(wait_for(lambda: self.entry(e["id"])["details"]["inputs"] == ["11", "31", "32"]))
        self.assertEqual(self.entry(e["id"])["details"]["maker"], "NXLX Test Works")
        n = len([r for r in fake.received if "INST" in r])
        time.sleep(0.5)
        self.assertEqual(len([r for r in fake.received if "INST" in r]), n)       # not asked again at every check

    def test_details_keep_older_values_and_an_odd_input_list_erases_nothing(self):
        fake, e = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        with self.settings.lock:
            self.settings.data["projectors"] = [dict(self.entry(e["id"]), labels={"31": "Matrix", "32": "Box"})]
        fake.standby_answers = False
        d = self.mon.identify(self.entry(e["id"]))
        self.assertEqual((d["maker"], d["inputs"]), ("NXLX Test Works", ["11", "31", "32"]))
        fake.standby_answers = True
        for odd in (["zz"], ["6A", "6B"], []):            # nothing class 1 in the answer: not known, the older list stays
            fake.inputs = odd
            self.assertEqual(self.mon.identify(self.entry(e["id"]))["inputs"], ["11", "31", "32"], odd)
            self.assertEqual(self.entry(e["id"])["labels"], {"31": "Matrix", "32": "Box"}, odd)
        fake.inputs = ["11", "31"]                        # a real, shorter list is taken; the label of the input that went is kept
        self.assertEqual(self.mon.identify(self.entry(e["id"]))["inputs"], ["11", "31"])
        self.assertEqual(self.entry(e["id"])["labels"], {"31": "Matrix", "32": "Box"})
        self.assertEqual(self.entry(e["id"])["password"], "")

    def test_an_input_change_while_warming_up_is_retried_until_it_works(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)               # now warming up
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "31"), {"pending": True})
        self.assertEqual(self.mon.status(e["id"])["pending_input"], "31")
        time.sleep(0.4)
        self.assertEqual(fake.input, "11")
        self.assertGreaterEqual(len([r for r in fake.received if r.endswith("INPT 31")]), 3)
        fake.finish()
        self.assertTrue(wait_for(lambda: fake.input == "31"))
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["notice"] == {"ok": True, "text": "Input switched to Digital 1."}))
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["input"] == "31"))
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "32"), {"pending": False})      # on: at once
        self.assertIsNone(self.mon.status(e["id"])["notice"])
        self.assertEqual(len(self.mon.threads()), 1)

    def test_the_retry_gives_up_and_says_so_plainly(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        with self.settings.lock:
            self.settings.data["projectors"] = [dict(self.entry(e["id"]), labels={"31": "Matrix"})]
        self.mon.api._pjlink(e).power(True)               # warming up, and it stays there
        start = time.monotonic()
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "31"), {"pending": True})
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["notice"]))
        took = time.monotonic() - start
        self.assertTrue(1.0 < took < 2.5, took)           # retry_for is 1.5 here (90 in the panel)
        n = self.mon.status(e["id"])["notice"]
        self.assertEqual(n["ok"], False)
        self.assertIn("Could not switch to Matrix: it was still not ready after 1 seconds", n["text"])
        self.assertIn("Could not switch to Matrix", self.logged[-1])
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])
        sent = len([r for r in fake.received if r.endswith("INPT 31")])
        self.assertLessEqual(sent, 17)
        fake.finish()
        time.sleep(0.4)
        self.assertEqual((fake.input, len([r for r in fake.received if r.endswith("INPT 31")])), ("11", sent))    # it really stopped

    def test_a_newer_choice_replaces_the_one_being_retried_and_a_refusal_ends_it(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)
        self.mon.set_input(self.entry(e["id"]), "31")
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "32"), {"pending": True})
        n31 = len([r for r in fake.received if r.endswith("INPT 31")])      # counted the moment the newer choice is in
        self.assertEqual(self.mon.status(e["id"])["pending_input"], "32")
        time.sleep(0.3)
        fake.finish()
        self.assertTrue(wait_for(lambda: fake.input == "32"))
        time.sleep(0.3)
        self.assertEqual((fake.input, len([r for r in fake.received if r.endswith("INPT 31")])), ("32", n31))     # not one more
        with self.assertRaises(projector.ProjectorError):                 # a plain refusal is not retried
            self.mon.set_input(self.entry(e["id"]), "59")
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])

    def test_a_retry_being_sent_never_overrides_a_newer_choice(self):
        fake, e = self.add(slow=True)
        self.mon.interval = 60
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)               # warming up
        real, gate, held = self.mon.api._pjlink, threading.Event(), threading.Event()

        class Hold:
            def __init__(self, link):
                self.__dict__["link"] = link

            def __getattr__(self, name):
                return getattr(self.link, name)

            def __setattr__(self, name, value):
                setattr(self.link, name, value)

            def set_input(self, code):
                if threading.current_thread().name == "projector-poll" and code == "31" and not held.is_set():
                    held.set()
                    gate.wait(5)                          # the retry of 31 is on its way out...
                return self.link.set_input(code)
        self.mon.api._pjlink = lambda entry: Hold(real(entry))
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "31"), {"pending": True})
        self.assertTrue(held.wait(3))
        fake.finish()                                     # ...the projector is ready now...
        newer = []
        t = threading.Thread(target=lambda: newer.append(self.mon.set_input(self.entry(e["id"]), "32")))
        t.start()                                         # ...and the user chooses 32
        time.sleep(0.2)
        gate.set()
        t.join(5)
        self.assertEqual(newer, [{"pending": False}])
        time.sleep(0.5)
        self.assertEqual(fake.input, "32")                # the last choice made stands
        self.assertEqual([r[-2:] for r in fake.received if "INPT " in r and not r.endswith("?")][-1], "32")
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])

    def test_an_odd_secondary_answer_does_not_wipe_the_status(self):
        fake, e = self.add()
        fake.power = "1"
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "on" and self.mon.status(e["id"])["lamps"]))
        for attr, value, key in (("errors", "00000", "warnings"), ("errors", "000003", "warnings"), ("lamps", [123456], "lamps"),
                                 ("input", "6A", "input"), ("mute_off", "40", "mute")):
            old = getattr(fake, attr)
            setattr(fake, attr, value)
            self.assertTrue(wait_for(lambda: self.mon.status(e["id"])[key] is None), attr)
            st = self.mon.status(e["id"])
            self.assertEqual((st["ok"], st["power"]), (True, "on"), attr)         # still answering, still on
            self.assertEqual(self.mon.health()[0]["state"], "ok", attr)
            setattr(fake, attr, old)
            self.assertTrue(wait_for(lambda: self.mon.status(e["id"])[key] is not None), attr)
        fake.fault = True                                 # ERR4 to the power question itself is not "on"
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["ok"] is False))

    def test_nothing_is_sent_or_saved_once_the_module_is_off(self):
        fake, e = self.add()
        orig = fake.answer
        fake.answer = lambda c, v: (time.sleep(0.3), orig(c, v))[1]
        self.timeout, self.mon.interval = 2.0, 60
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(fake.received) == 2))        # in the middle of reading the details
        self.registry.set_enabled("projector", False)
        self.mon.apply()
        n = len(fake.received)
        self.assertTrue(wait_for(lambda: not poll_threads(), 1.5))        # it ends with the command it was in
        time.sleep(0.5)
        self.assertEqual(len(fake.received), n)           # not one more command
        self.assertIsNone(self.entry(e["id"]).get("details"))             # and nothing written with the module off
        with open(self.path) as f:
            self.assertNotIn("NXLX Test Works", f.read())
        self.registry.set_enabled("projector", True)      # a removed projector: the same
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(fake.received) >= n + 2))
        with self.settings.lock:
            self.settings.data["projectors"] = []
            self.settings.save()
        self.mon.apply()
        n = len(fake.received)
        self.assertTrue(wait_for(lambda: not poll_threads(), 1.5))
        time.sleep(0.5)
        self.assertEqual((len(fake.received), self.settings.data["projectors"]), (n, []))

    def test_never_more_than_eight_threads_and_a_waiting_projector_says_so(self):
        self.timeout, self.mon.interval = 2.0, 60
        fakes = []
        for _ in range(8):
            f = FakeProjector()
            self.addCleanup(f.close)
            f.answer = (lambda o: lambda c, v: (time.sleep(0.3), o(c, v))[1])(f.answer)
            fakes.append(f)
        peak, waited = 0, False
        for _ in range(6):                                # all eight removed and added again, six times
            with self.settings.lock:
                self.settings.data["projectors"] = [projector.validate({"name": "P%d" % i, "host": "127.0.0.1", "port": f.port})
                                                    for i, f in enumerate(fakes)]
                self.settings.save()
            self.mon.apply()
            rows = self.mon.health()                      # straight away: the old threads are still in a command
            if any(self.mon.status(p["id"])["waiting"] for p in self.settings.data["projectors"]):
                waited = waited or "Waiting for an earlier check to end." in [r["text"] for r in rows]
            for _ in range(10):
                peak = max(peak, len(poll_threads()))
                time.sleep(0.02)
        self.assertLessEqual(peak, projector.MAX_PROJECTORS)
        self.assertTrue(waited)                           # the limit was really reached
        ids = [p["id"] for p in self.settings.data["projectors"]]
        self.assertTrue(wait_for(lambda: all(self.mon.status(i).get("power") == "off" for i in ids), 20))     # nobody is left waiting
        self.assertEqual(len(poll_threads()), 8)
        self.assertFalse(any(self.mon.status(i)["waiting"] for i in ids))
        self.mon.stop(final=True)
        self.assertTrue(wait_for(lambda: not poll_threads(), 3))

    def test_a_name_that_moves_to_a_public_address_is_refused_in_the_background(self):
        fake = FakeProjector()
        self.addCleanup(fake.close)
        where, connects, real = ["127.0.0.1"], [], socket.create_connection

        def connect(addr, timeout=None):
            connects.append(addr[0])
            return real(addr, timeout=timeout)
        resolve = lambda h, p, proto=0: [(0, 0, 0, "", (where[0], p))]
        self.mon.api._pjlink = lambda e: projector.PJLink(e["host"], e["port"], e["password"], timeout=1, connect=connect, resolve=resolve)
        e = projector.validate({"name": "Named", "host": "beamer.lan", "port": fake.port})
        with self.settings.lock:
            self.settings.data["projectors"] = [e]
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        where[0] = "93.184.216.34"                        # the name now points at the internet
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"]).get("ok") is False))
        self.assertIn("private", self.mon.status(e["id"])["error"])
        n = len(fake.received)
        time.sleep(0.5)
        self.assertEqual(set(connects), {"127.0.0.1"})    # never connected there
        self.assertEqual(len(fake.received), n)
        with self.assertRaises(projector.ProjectorError):
            self.mon.set_input(dict(e, details={"inputs": ["31"]}), "31")
        self.assertEqual(set(connects), {"127.0.0.1"})

    def test_a_check_that_could_not_be_made_does_not_ask_for_the_details_again(self):
        fake, e = self.add(standby_answers=False)
        fake.power = "1"
        self.mon.apply()
        self.assertTrue(wait_for(lambda: (self.entry(e["id"]).get("details") or {}).get("inputs") and self.power(e["id"]) == "on"))
        with self.settings.lock:                          # as if the list had not been given: asked once per "on", no more
            self.settings.data["projectors"] = [dict(self.entry(e["id"]), details=dict(self.entry(e["id"])["details"], inputs=None))]
        fake.inputs = ["zz"]
        self.assertTrue(wait_for(lambda: len([r for r in fake.received if "INST" in r]) >= 2))
        time.sleep(0.4)
        n = len([r for r in fake.received if "INST" in r])
        real, busy = self.mon.api._pjlink, [True]

        class Busy:
            def __init__(self, link):
                self.__dict__["link"] = link

            def __getattr__(self, name):
                return getattr(self.link, name)

            def __setattr__(self, name, value):
                setattr(self.link, name, value)

            def state(self):
                if busy[0]:
                    raise projector.ProjectorError("the projector is busy with another command", "busy")
                return self.link.state()
        self.mon.api._pjlink = lambda entry: Busy(real(entry))
        time.sleep(0.5)                                   # several checks that could not be made
        busy[0] = False
        checked = len([r for r in fake.received if "POWR ?" in r])
        self.assertTrue(wait_for(lambda: len([r for r in fake.received if "POWR ?" in r]) >= checked + 2))
        self.assertEqual(len([r for r in fake.received if "INST" in r]), n)
        self.assertEqual(self.power(e["id"]), "on")       # and the last answer stood meanwhile

    def test_switching_the_module_off_ends_a_retry(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)
        self.mon.set_input(self.entry(e["id"]), "31")
        self.registry.set_enabled("projector", False)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        fake.finish()
        time.sleep(0.4)
        self.assertEqual(fake.input, "11")

    def test_health_rows_show_lamp_hours_and_warnings(self):
        fake, e = self.add(lamps=(1200, 800))
        self.assertEqual(self.mon.health(), [{"id": e["id"], "name": "P0", "state": "unknown", "text": "Not checked yet."}])
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "ok"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h.")
        fake.errors = "100010"
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "warn"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h. Warning: fan, filter.")
        fake.errors = "102010"
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "bad"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h. Error: temperature. Warning: fan, filter.")
        self.registry.set_enabled("projector", False)
        self.assertEqual(self.mon.health(), [])


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.0.5"}})[0], 409)       # module off
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.addCleanup(self.api.projectors.stop, True)
        self.fake = FakeProjector("pw1")
        self.addCleanup(self.fake.close)
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def add(self, host="127.0.0.1"):
        return self.post("/api/projectors", {"add": {"name": "Main", "host": host, "port": self.fake.port, "password": "pw1"}})

    def test_add_list_hides_password_power_and_remove(self):
        st, body, _ = self.add()
        self.assertEqual(st, 200)
        p = body["projectors"][0]
        self.assertEqual((p["name"], p["has_password"]), ("Main", True))
        self.assertNotIn("pw1", str(self.call("GET", "/api/projectors", token=self.full)[1]))
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projector", {"id": p["id"], "action": "on"}, token=live)[0], 200)
        self.assertEqual(self.fake.power, "1")
        st, body, _ = self.post("/api/projector", {"id": "all", "action": "state"})
        self.assertEqual(body["results"][p["id"]]["power"], "on")
        self.assertEqual(self.post("/api/projectors", {"remove": p["id"]})[1]["projectors"], [])

    def test_public_addresses_and_bad_input_are_refused(self):
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "8.8.8.8"}})[0], 400)
        self.assertEqual(self.post("/api/projectors", {"nonsense": 1})[0], 400)
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "explode"})[0], 400)
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on"})[0], 404)          # none added

    def test_an_unreachable_projector_is_a_clear_error(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.fake.close()
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertEqual(st, 502)
        self.assertIn("cannot reach", body["error"])

    def test_background_answers_at_once_and_still_switches(self):
        self.add()
        st, body, _ = self.post("/api/projector", {"id": "all", "action": "on", "background": True})
        self.assertEqual((st, body), (200, {"started": True}))
        import time as _t
        end = _t.monotonic() + 5
        while self.fake.power != "1" and _t.monotonic() < end:
            _t.sleep(0.05)
        self.assertEqual(self.fake.power, "1")
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on", "background": "yes"})[0], 400)

    def test_an_unexpected_error_is_a_502_not_a_crash(self):
        pid = self.add()[1]["projectors"][0]["id"]

        class Broken:
            def power(self, on):
                raise RuntimeError("boom")
        with mock.patch.object(self.api, "_pjlink", lambda p: Broken()):
            st, body, _ = self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertEqual(st, 502)
        self.assertIn("boom", body["error"])

    def test_a_slow_name_lookup_does_not_hold_the_settings_lock(self):
        api = self.api
        held = []

        def check(host, resolve=None):
            got = []
            t = threading.Thread(target=lambda: got.append(api.settings.lock.acquire(timeout=1)) or (got[0] and api.settings.lock.release()))
            t.start()
            t.join()
            held.append(not got[0])
            return "192.168.0.7"
        def refuse(addr, timeout=None):                   # the lookup above is made up: nothing may leave this machine
            raise OSError("refused")
        nowhere = lambda e: projector.PJLink(e["host"], e["port"], e["password"], connect=refuse)
        with mock.patch.object(api, "_pjlink", nowhere), mock.patch.object(projector, "private_address", check):
            self.assertEqual(self.post("/api/projectors", {"add": {"host": "beamer.lan"}})[0], 200)
            self.assertTrue(wait_for(lambda: len(held) >= 2))     # the background check looks the name up too
            api.projectors.stop(final=True)
            self.assertTrue(wait_for(lambda: not api.projectors.threads(), 3))
        self.assertNotIn(True, held)                      # never under the lock, neither the add nor the background check

    def projector(self):
        return self.call("GET", "/api/projectors", token=self.full)[1]["projectors"][0]

    def test_details_inputs_labels_and_status_are_listed_without_the_password(self):
        st, body, _ = self.add()
        pid = body["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"] and self.projector()["status"].get("power") == "off"))
        p = self.projector()
        self.assertEqual((p["details"]["maker"], p["details"]["model"], p["details"]["class"]), ("NXLX Test Works", "FP-1", "1"))
        self.assertEqual(p["inputs"], [{"code": "11", "name": "RGB 1", "label": ""}, {"code": "31", "name": "Digital 1", "label": ""},
                                       {"code": "32", "name": "Digital 2", "label": ""}])
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}}, token=live)[0], 403)
        st, body, _ = self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}})
        self.assertEqual((st, body["projectors"][0]["inputs"][1]), (200, {"code": "31", "name": "Digital 1", "label": "Matrix"}))
        for bad in ({"id": pid, "input": "59", "label": "x"}, {"id": pid, "input": "31", "label": "x" * 25}, {"id": pid, "input": "31"},
                    {"id": pid, "input": "31", "label": "a\u202eb"}, "x", None, [pid, "31", "x"], {"input": "31", "label": "x"}, {"id": 5}):
            self.assertEqual(self.post("/api/projectors", {"label": bad})[0], 400, bad)
        self.assertEqual(self.post("/api/projectors", {"label": {"id": "nope", "input": "31", "label": "x"}})[0], 404)
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": ""}})[1]["projectors"][0]["inputs"][1]["label"], "")
        for path in ("/api/projectors", "/api/health"):
            self.assertNotIn("pw1", json.dumps(self.call("GET", path, token=self.full)[1]))
        self.assertNotIn("password", [k for k in self.projector() if k != "has_password"])

    def test_input_and_the_separate_mutes(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "input", "input": "31"})[0], 400)
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "on"})[0], 200)
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        self.settings.data["guest_controls"] = {"locked": True}       # a guest who only watches; what he may do while open is tests/test_roles.py
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "input", "input": "31"}, token=view)[0], 403)
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"}, token=live)
        self.assertEqual((st, body["results"][pid], self.fake.input), (200, {"ok": True, "pending": False}, "31"))
        for bad in ("59", "31\r", 31, None, ""):
            self.assertEqual(self.post("/api/projector", {"id": pid, "action": "input", "input": bad})[0], 400, bad)
        self.assertEqual(self.fake.input, "31")
        for action, want in (("mute_picture", "11"), ("mute_sound", "31"), ("unmute_picture", "21"), ("unmute_sound", "30"),
                             ("mute", "31"), ("unmute", "30")):
            self.assertEqual(self.post("/api/projector", {"id": pid, "action": action}, token=live)[0], 200, action)
            self.assertEqual(self.fake.mute, want, action)
            shows = {"picture": want in ("11", "31"), "sound": want in ("21", "31")}
            self.assertTrue(wait_for(lambda: self.projector()["status"].get("mute") == shows), action)    # at once, not 45 seconds later
        self.assertEqual(self.projector()["status"]["input"], "31")
        self.fake.separate_mute = False
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "mute_sound"})
        self.assertEqual(st, 502)
        self.assertIn("separately", body["error"])

    def test_an_input_change_refused_as_unavailable_goes_to_the_background(self):
        pid = self.add()[1]["projectors"][0]["id"]
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"})
        self.assertIn(st, (200, 409))                     # 409 if the details were not read yet
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.api.projectors.retry_every = 0.1
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "32"})       # the projector is off
        self.assertEqual((st, body["results"][pid]), (200, {"ok": True, "pending": True}))
        self.assertEqual(self.projector()["status"]["pending_input"], "32")
        self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertTrue(wait_for(lambda: self.fake.input == "32"))
        self.assertTrue(wait_for(lambda: (self.projector()["status"]["notice"] or {}).get("ok") is True))

    def test_labelling_sends_nothing_and_a_refresh_by_a_presenter_keeps_labels(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("input") == "11"))
        n = len([r for r in self.fake.received if "INPT 3" in r])
        for code, label in (("31", "Matrix"), ("32", "Box")):
            self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": code, "label": label}})[0], 200)
        self.assertEqual((self.fake.input, len([r for r in self.fake.received if "INPT 3" in r])), ("11", n))       # no input command
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.fake.inputs = ["zz"]                         # one odd answer, on a refresh by a presenter
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "identify"}, token=live)[0], 200)
        self.assertEqual([(i["code"], i["label"]) for i in self.projector()["inputs"]], [("11", ""), ("31", "Matrix"), ("32", "Box")])
        self.fake.inputs = ["11", "31"]                   # the input went: its label is not shown, and not thrown away
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "identify"}, token=live)[0], 200)
        self.assertEqual([(i["code"], i["label"]) for i in self.projector()["inputs"]], [("11", ""), ("31", "Matrix")])
        self.fake.inputs = ["11", "31", "32"]
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "identify"}, token=live)[0], 200)
        self.assertEqual(self.projector()["inputs"][2], {"code": "32", "name": "Digital 2", "label": "Box"})

    def test_the_same_projector_cannot_be_added_twice(self):
        self.assertEqual(self.add()[0], 200)
        st, body, _ = self.add()
        self.assertEqual(st, 409)
        self.assertIn("already in the list as Main", body["error"])
        by_name = lambda host, resolve=None: "127.0.0.1"  # another spelling of the same device
        with mock.patch.object(projector, "private_address", by_name), mock.patch.object(self.api.projectors, "apply", lambda: None):
            self.assertEqual(self.add(host="beamer.lan")[0], 409)
        self.assertEqual(len(self.call("GET", "/api/projectors", token=self.full)[1]["projectors"]), 1)
        other = FakeProjector()                           # the same address on another port is another projector
        self.addCleanup(other.close)
        self.assertEqual(self.post("/api/projectors", {"add": {"name": "Side", "host": "127.0.0.1", "port": other.port}})[0], 200)

    def test_the_status_is_fresh_after_a_button_that_failed(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.fake.errors = "000200"                       # something changed that only a new check can see
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "mute_picture"})[0], 502)       # refused: it is in standby
        self.assertTrue(wait_for(lambda: self.projector()["status"]["warnings"]["cover"] == "error", 2))    # not 45 seconds later

    def test_the_status_before_the_first_check(self):
        self.fake.close()
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            p = self.add()[1]["projectors"][0]
        self.assertEqual(p["status"], {"pending_input": None, "notice": None, "waiting": False})

    def test_inputs_not_known_yet_is_a_clear_refusal(self):
        self.fake.standby_answers = False
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"})
        self.assertEqual(st, 409)
        self.assertIn("not known yet", body["error"])
        self.assertEqual(self.projector()["inputs"], [])

    def test_refresh_details(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.fake.model, self.fake.inputs = "FP-2", ["31", "51"]
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "identify"}, token=live)[0], 200)
        p = self.projector()
        self.assertEqual((p["details"]["model"], [i["name"] for i in p["inputs"]]), ("FP-2", ["Digital 1", "Network 1"]))
        self.fake.close()
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "identify"})
        self.assertEqual(st, 502)
        self.assertEqual(self.projector()["details"]["model"], "FP-2")

    def test_adding_answers_at_once_even_if_the_projector_is_not_there(self):
        self.fake.close()
        start = time.monotonic()
        st, body, _ = self.add()
        self.assertEqual(st, 200)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual((body["projectors"][0]["details"], body["projectors"][0]["inputs"]), (None, []))

    def test_switching_the_module_off_stops_the_background_checks(self):
        self.add()
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.assertEqual(len(self.api.projectors.threads()), 1)
        self.call("POST", "/api/modules/projector", {"enabled": False}, token=self.full)
        self.assertTrue(wait_for(lambda: not self.api.projectors.threads(), 1.5))
        self.assertEqual(self.projector()["status"], None)
        self.assertEqual(self.call("GET", "/api/health", token=self.full)[1]["projectors"], [])
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.assertEqual(len(self.api.projectors.threads()), 1)
        pid = self.projector()["id"]
        self.post("/api/projectors", {"remove": pid})
        self.assertTrue(wait_for(lambda: not self.api.projectors.threads(), 1.5))

    def test_health_shows_lamp_hours_and_a_projector_warning(self):
        self.add()
        health = lambda: self.call("GET", "/api/health", token=self.full)[1]
        self.assertTrue(wait_for(lambda: health()["projectors"] and health()["projectors"][0]["state"] == "ok"))
        self.assertEqual((health()["projectors"][0]["name"], health()["projectors"][0]["text"]), ("Main", "Off, lamp 1234 h."))
        before = health()["overall"]
        self.fake.errors = "020000"
        self.api.projectors.poke("all")
        self.assertTrue(wait_for(lambda: health()["projectors"][0]["state"] == "bad"))
        self.assertEqual((health()["projectors"][0]["text"], health()["overall"]), ("Off, lamp 1234 h. Error: lamp.", "bad"))
        self.fake.errors = "000000"
        self.api.projectors.poke("all")
        self.assertTrue(wait_for(lambda: health()["overall"] == before))

    def test_a_background_failure_is_logged(self):
        self.add()
        self.fake.close()
        lines = []
        with mock.patch.object(self.api, "log", lines.append):
            self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on", "background": True})[1], {"started": True})
            self.assertTrue(wait_for(lambda: [l for l in lines if "cannot reach" in l]))
        failed = [l for l in lines if "cannot reach" in l]      # since D99 the change itself has a line of its own first
        self.assertIn("projector on", failed[0])

    def test_roles(self):
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.call("GET", "/api/projectors", token=view)[0], 200)
        self.settings.data["guest_controls"] = {"locked": True}       # a guest who only watches; what he may do while open is tests/test_roles.py
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on"}, token=view)[0], 403)
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.0.5"}}, token=live)[0], 403)

    # --- edit ---------------------------------------------------------------------------------------------
    def fast(self):
        """The background checks every 50 ms, so "nothing more is sent" can be seen in a second."""
        mon = self.api.projectors
        mon.interval, mon.changing, mon.stagger = 0.05, 0.05, 0.0

    def stored(self, pid=None):
        items = self.settings.data["projectors"]
        return next(p for p in items if p["id"] == pid) if pid else items[0]

    def test_edit_a_name_changes_nothing_else(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"] and self.projector()["status"].get("ok")))
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}})[0], 200)
        before, threads, status = dict(self.stored()), self.api.projectors.threads(), self.projector()["status"]
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "name": "  Main wall "}})
        self.assertEqual(st, 200)
        self.assertEqual(self.stored(), dict(before, name="Main wall"))       # the password, details and labels as they were
        self.assertEqual((body["projectors"][0]["name"], body["projectors"][0]["has_password"]), ("Main wall", True))
        self.assertEqual(self.api.projectors.threads(), threads)              # the same worker: no restart
        self.assertEqual(self.projector()["status"]["power"], status["power"])
        self.assertNotIn("pw1", json.dumps(body))

    def test_edit_the_password_left_out_keeps_it_an_empty_one_clears_it_and_it_is_never_returned(self):
        pid = self.add()[1]["projectors"][0]["id"]
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "name": "Side"}})
        self.assertEqual((st, self.stored()["password"], body["projectors"][0]["has_password"]), (200, "pw1", True))
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "on"})[0], 200)                 # and it still works
        self.assertTrue(wait_for(lambda: self.stored().get("details")))
        details = dict(self.stored()["details"])
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "password": "Secret-2"}})
        self.assertEqual((st, self.stored()["password"]), (200, "Secret-2"))
        self.assertNotIn("Secret-2", json.dumps(body))
        self.assertNotIn("Secret-2", json.dumps(self.call("GET", "/api/projectors", token=self.full)[1]))
        self.assertEqual(self.stored()["details"], details)                                                # a new password keeps the details
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "off"})[0], 502)                # the projector's is still pw1
        self.assertTrue(wait_for(lambda: "wrong projector password" in (self.projector()["status"].get("error") or ""), 3))   # said at once, not 45 s later
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "password": ""}})
        self.assertEqual((st, self.stored()["password"], body["projectors"][0]["has_password"]), (200, "", False))
        for bad_pw in ("with space", "x" * 33, 5, None, "café"):
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "password": bad_pw}})[0], 400, bad_pw)
        self.assertEqual(self.stored()["password"], "")

    def test_edit_is_checked_like_an_add(self):
        pid = self.add()[1]["projectors"][0]["id"]
        core = lambda: {k: self.stored()[k] for k in ("id", "name", "host", "port", "password")}      # the details arrive in the background
        before = core()
        for change in ({"host": "8.8.8.8"}, {"host": "169.254.169.254"}, {"host": "127.0.0.1x!"}, {"host": ""}, {"host": 7},
                       {"host": "a" * 64 + ".lan"},                        # a label DNS cannot encode
                       {"host": "x..lan"}, {"port": 0}, {"port": 65536}, {"port": "4352"}, {"port": True},
                       {"name": ""}, {"name": "x" * 41}, {"name": "bad‮name"}, {"name": 5}, {}):
            st, body, _ = self.post("/api/projectors", {"edit": dict(change, id=pid)})
            self.assertEqual(st, 400, (change, body))
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": "nope", "name": "x"}})[0], 404)
        for junk in ("x", None, [pid], {"name": "x"}, {"id": 5, "name": "x"}):
            self.assertEqual(self.post("/api/projectors", {"edit": junk})[0], 400, junk)
        self.assertEqual(core(), before)
        public = lambda host, port, proto=0: [(0, 0, 0, "", ("93.184.216.34", port))]
        real = projector.private_address
        with mock.patch.object(projector, "private_address", lambda host, resolve=None: real(host, public)):   # a name that points at the internet
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "host": "beamer.example"}})[0], 400)
        self.assertEqual(core(), before)
        # full access only, with the CSRF header, and only with the module on
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "name": "Mine"}}, token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/projectors", {"edit": {"id": pid, "name": "Mine"}}, token=self.full, csrf=False)[0], 403)
        self.call("POST", "/api/modules/projector", {"enabled": False}, token=self.full)
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "name": "Mine"}})[0], 409)
        self.assertEqual(core(), before)

    def test_edit_cannot_make_a_duplicate_except_of_itself(self):
        other = FakeProjector()
        self.addCleanup(other.close)
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            pid = self.add()[1]["projectors"][0]["id"]
            side = self.post("/api/projectors", {"add": {"name": "Side", "host": "127.0.0.1", "port": other.port}})[1]["projectors"][1]["id"]
            st, body, _ = self.post("/api/projectors", {"edit": {"id": side, "port": self.fake.port}})
            self.assertEqual(st, 409)
            self.assertIn("already in the list as Main", body["error"])
            by_name = lambda host, resolve=None: "127.0.0.1"      # another spelling of the same device
            with mock.patch.object(projector, "private_address", by_name):
                self.assertEqual(self.post("/api/projectors", {"edit": {"id": side, "host": "beamer.lan", "port": self.fake.port}})[0], 409)
                st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "host": "beamer.lan"}})       # itself under a new spelling
                self.assertEqual((st, self.stored(pid)["host"]), (200, "beamer.lan"))
            # sending its own unchanged address is no duplicate either
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": side, "host": "127.0.0.1", "port": other.port, "name": "Side wall"}})[0], 200)
            self.assertEqual((self.stored(side)["name"], self.stored(side)["port"]), ("Side wall", other.port))
            self.assertEqual(len(self.settings.data["projectors"]), 2)

    def test_edit_does_not_touch_the_limit_of_8(self):
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            for n in range(projector.MAX_PROJECTORS):
                self.assertEqual(self.post("/api/projectors", {"add": {"name": "P%d" % n, "host": "192.168.9.%d" % (n + 1)}})[0], 200)
            self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.9.99"}})[0], 400)
            pid = self.stored()["id"]
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "host": "192.168.9.77", "name": "Moved"}})[0], 200)
            self.assertEqual([len(self.settings.data["projectors"]), self.stored()["host"]], [projector.MAX_PROJECTORS, "192.168.9.77"])

    def test_edit_the_address_keeps_labels_and_starts_over_at_the_new_one_with_one_thread(self):
        self.fast()
        other = FakeProjector("pw1", inputs=("31", "33"), name="The other one")
        self.addCleanup(other.close)
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: (self.projector()["details"] or {}).get("name") == "Fake projector" and self.projector()["status"].get("ok")))
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}})[0], 200)
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "port": other.port}})
        self.assertEqual(st, 200)
        now = body["projectors"][0]
        self.assertEqual((now["id"], now["port"], now["has_password"]), (pid, other.port, True))
        self.assertEqual(self.stored()["labels"], {"31": "Matrix"})       # the labels stay
        time.sleep(0.3)                    # a command already on the wire when the edit landed was the last one
        sent = len(self.fake.raw)
        self.assertTrue(wait_for(lambda: (self.projector()["details"] or {}).get("name") == "The other one"))      # identified anew
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.assertEqual([(i["code"], i["label"]) for i in self.projector()["inputs"]], [("31", "Matrix"), ("33", "")])
        time.sleep(0.5)                    # ten rounds of the background check
        self.assertEqual(len(self.fake.raw), sent, "nothing goes to the old address after an edit")
        self.assertGreater(len(other.raw), 10)
        self.assertEqual(len(self.api.projectors.threads()), 1)
        self.assertTrue(wait_for(lambda: len(poll_threads()) <= 1))       # an earlier test's thread may still be ending
        self.assertEqual(self.projector()["status"]["waiting"], False)

    def test_edit_the_address_drops_the_details_and_the_status_at_once(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"] and self.projector()["status"].get("ok")))
        self.api.projectors.stop()                                        # no worker: nothing fills them in again behind the test
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "host": "192.168.7.7"}})
        self.assertEqual(st, 200)
        self.assertIsNone(body["projectors"][0]["details"])               # what the old address said it is
        self.assertNotIn("ok", body["projectors"][0]["status"])
        self.assertNotIn("details", self.stored())

    def test_edit_while_the_old_address_is_silent_never_makes_a_second_thread(self):
        """The common case: the address was typed wrong, so the worker is in the middle of a connection that will
        never be answered. The edit does not wait for it, and the same thread goes on at the right address."""
        self.fast()
        release = threading.Event()
        self.addCleanup(release.set)
        silent = raw_server(self, lambda rfile, wfile: release.wait(20))
        real = self.api._pjlink
        self.api._pjlink = lambda e: projector.PJLink(e["host"], e["port"], e["password"], timeout=1.5)
        self.addCleanup(setattr, self.api, "_pjlink", real)
        st, body, _ = self.post("/api/projectors", {"add": {"name": "Main", "host": "127.0.0.1", "port": silent, "password": "pw1"}})
        pid = body["projectors"][0]["id"]
        time.sleep(0.2)                    # the worker is now waiting for a greeting that does not come
        began = time.monotonic()
        st, body, _ = self.post("/api/projectors", {"edit": {"id": pid, "port": self.fake.port}})
        self.assertEqual(st, 200)
        self.assertLess(time.monotonic() - began, 1.0)                    # not held up by the old address
        self.assertTrue(body["projectors"][0]["status"]["waiting"])       # the old command is still ending
        self.assertEqual(len(self.api.projectors.threads()), 1)
        self.assertTrue(wait_for(lambda: (self.projector()["details"] or {}).get("name") == "Fake projector", 8))
        self.assertEqual(len(self.api.projectors.threads()), 1)
        self.assertTrue(wait_for(lambda: len(poll_threads()) == 1))

    def test_an_answer_from_the_old_address_is_not_kept_after_an_edit(self):
        """Refresh details asked the old address; the edit lands before the answer is saved."""
        other = FakeProjector("pw1", name="The other one")
        self.addCleanup(other.close)
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            pid = self.add()[1]["projectors"][0]["id"]
            old = dict(self.stored())
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": other.port}})[0], 200)
            self.assertIsNone(self.api.projectors.identify(old))          # asked of the old address, as a request under way would
            self.assertNotIn("details", self.stored())
            self.assertEqual(self.api.projectors.identify(self.stored())["name"], "The other one")

    def test_settings_export_and_import_after_an_edit_to_another_projector(self):
        """The whole story: inputs named, the projector swapped for one at another address that lists other inputs,
        the box reads the new one. A name for an input the new one does not list is kept and not shown, and the
        box's own export of that state imports again."""
        other = FakeProjector("pw1", inputs=("31", "33"), name="The other one")
        self.addCleanup(other.close)
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            pid = self.add()[1]["projectors"][0]["id"]
            self.assertEqual(self.api.projectors.identify(self.stored())["inputs"], ["11", "31", "32"])
            for code, label in (("31", "Matrix"), ("32", "Box")):
                self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": code, "label": label}})[0], 200)
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": other.port, "name": "New one"}})[0], 200)
            self.assertEqual(self.api.projectors.identify(self.stored())["inputs"], ["31", "33"])
            self.assertEqual(self.stored()["labels"], {"31": "Matrix", "32": "Box"})
            self.assertEqual([(i["code"], i["label"]) for i in self.projector()["inputs"]], [("31", "Matrix"), ("33", "")])
            before = json.loads(json.dumps(self.settings.data["projectors"]))
            st, out, _ = self.post("/api/system/settings/export", {"passwords": True})
            self.assertEqual(st, 200, out)
            self.assertEqual(out["file"]["settings"]["projectors"], before)
            self.settings.data["projectors"] = []
            st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(out["file"]).encode(), token=self.full)
            self.assertEqual(st, 200, body)
            self.assertEqual(self.settings.data["projectors"], before)

    def test_an_input_change_refused_as_the_edit_lands_says_so_even_while_the_old_check_is_still_ending(self):
        """The test below failed now and then with 'ERR3' in place of 'stopped'. When the edit lands while the old
        worker's thread is in the middle of a status check, the projector has no worker for a moment (the old thread
        takes it over when it is done), and set_input looked for the worker before it looked at the address: the
        person was told "unavailable" about an address they had just replaced. Here the old check is held open, so
        it is so every time."""
        self.fast()
        other = FakeProjector("pw1", inputs=("11", "33"), name="The other one")
        self.addCleanup(other.close)
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        held, real = dict(self.stored()), self.api._pjlink
        inside, let_go, mine = threading.Event(), threading.Event(), threading.current_thread()
        self.addCleanup(let_go.set)

        def link(entry):
            lk = real(entry)
            if entry["port"] == self.fake.port and threading.current_thread() is not mine:
                state = lk.state

                def slow_state():                                       # the background check, held in the middle
                    inside.set()
                    let_go.wait(5)
                    return state()
                lk.state = slow_state
            elif entry["port"] == self.fake.port:
                send = lk.set_input

                def set_input(code):
                    try:
                        return send(code)                               # the old projector: ERR3
                    finally:
                        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": other.port}})[0], 200)
                        self.assertTrue(self.api.projectors.status(pid)["waiting"])     # no worker: the old thread still has it
                lk.set_input = set_input
            return lk
        with mock.patch.object(self.api, "_pjlink", link):
            self.assertTrue(inside.wait(5))
            with self.assertRaises(projector.ProjectorError) as e:
                self.api.projectors.set_input(held, "31")
            self.assertEqual(e.exception.code, "stopped")
            self.assertIn("address was changed", str(e.exception))
            let_go.set()
            self.assertTrue(wait_for(lambda: (self.projector()["details"] or {}).get("name") == "The other one"))
        self.assertIsNone(self.projector()["status"]["pending_input"])
        self.assertEqual([r for r in other.received if "INPT" in r and "?" not in r], [])       # the new one got no input command

    def refused_as_it_vanishes(self, vanishes, still_there):
        """An input change is answered "unavailable" while the projector is taken out of the settings, and once more
        while the module is switched off. `vanishes(pid, vanish)` runs between the projector's answer and the
        handling of it: it calls vanish() and puts the worker where the test wants it at that instant, and returns
        what lets the worker go on afterwards (or None). Either way the refusal comes back as it is, and the
        projector, back again, has nothing waiting to be tried and was sent the command once."""
        self.fast()
        mon, mine = self.api.projectors, threading.current_thread()
        pid = self.add()[1]["projectors"][0]["id"]
        real, saved = self.api._pjlink, list(self.settings.data["projectors"])

        def ready():
            return pid in mon._workers and self.projector()["status"].get("power") == "off"

        def sent():
            return len([r for r in self.fake.received if "INPT" in r and "?" not in r])

        def gone_from_the_settings():
            self.settings.data["projectors"] = []

        def module_off():
            self.api.registry.set_enabled("projector", False)
        for vanish, back in ((gone_from_the_settings, lambda: self.settings.data.__setitem__("projectors", saved)),
                             (module_off, lambda: self.api.registry.set_enabled("projector", True))):
            name = vanish.__name__
            self.assertTrue(wait_for(ready), name)
            held, after, before = dict(saved[0]), [], sent()

            def link(entry):
                lk = real(entry)
                if threading.current_thread() is mine:
                    send = lk.set_input

                    def set_input(code):
                        try:
                            return send(code)                           # in standby: ERR3
                        finally:
                            after.append(vanishes(pid, vanish))         # and no apply(), unless `vanishes` makes one
                    lk.set_input = set_input
                return lk
            with mock.patch.object(self.api, "_pjlink", link):
                with self.assertRaises(projector.ProjectorError) as e:  # a retry kept would be {"pending": True}, not this
                    mon.set_input(held, "31")
            self.assertEqual(e.exception.code, "ERR3", name)
            self.assertEqual(len(after), 1, name)                       # the projector vanished in the middle, once
            w = mon._workers.get(pid)
            self.assertEqual(w is not None, still_there, name)
            if w is not None:
                self.assertFalse(w.stop.is_set(), name)                 # there and not stopped: only the settings say "gone"
                self.assertIsNone(w.pending, name)
            self.assertIsNone(mon.status(pid)["pending_input"], name)
            back()
            if after[0]:
                after[0]()
            mon.apply()
            self.assertTrue(wait_for(ready), name)
            self.assertIsNone(self.projector()["status"]["pending_input"], name)
            self.assertEqual(sent(), before + 1, name)

    def test_an_input_change_refused_as_the_projector_is_removed_or_switched_off_gets_no_retry(self):
        """Review of #86, finding 1. The projector is taken out of the settings (or the module is switched off) while
        it is answering "unavailable", and its worker has not been stopped yet (the settings are saved before the
        workers are matched to them). The refusal comes back as it is; nothing is kept to try again for a
        projector that is no longer there.
        This test failed once in CI on its own set-up: "the worker was still there" was true only until the worker
        woke (every 50 ms here), saw its entry gone and left by itself. Now the worker is held at the door: it has
        seen the entry gone and is on its way out, and it cannot go before the refusal has been handled."""
        mon = self.api.projectors
        leave, gates = mon._after, []

        def held_at_the_door(w):
            at_the_door, let_go = gates[-1]
            at_the_door.set()
            let_go.wait(5)
            return leave(w)

        def vanishes(pid, vanish):
            at_the_door, let_go = threading.Event(), threading.Event()
            self.addCleanup(let_go.set)
            gates.append((at_the_door, let_go))                         # before the entry goes: the worker may see it at once
            vanish()
            mon._workers[pid].wake.set()
            self.assertTrue(at_the_door.wait(5), vanish.__name__)
            return let_go.set
        with mock.patch.object(mon, "_after", held_at_the_door):
            self.refused_as_it_vanishes(vanishes, still_there=True)

    def test_an_input_change_refused_after_the_worker_of_a_removed_projector_left_by_itself_gets_no_retry(self):
        """The other order of the test above: the worker saw its entry gone and left before the refusal is handled.
        No worker, so no retry: the refusal comes back as it is then too."""
        mon = self.api.projectors

        def vanishes(pid, vanish):
            w = mon._workers[pid]
            vanish()
            w.wake.set()
            self.assertTrue(wait_for(lambda: pid not in mon._workers), vanish.__name__)
        self.refused_as_it_vanishes(vanishes, still_there=False)

    def test_an_input_change_refused_after_apply_stopped_the_worker_of_a_removed_projector_gets_no_retry(self):
        """And the order of the panel itself when all of it fits inside the projector's answer: the settings saved,
        then apply(), which stops the worker, then the refusal is handled."""
        def vanishes(pid, vanish):
            vanish()
            self.api.projectors.apply()
            self.assertNotIn(pid, self.api.projectors._workers)
        self.refused_as_it_vanishes(vanishes, still_there=False)

    def test_an_input_change_asked_before_an_edit_goes_nowhere_after_it(self):
        """Review finding 2. The input was chosen from the old projector's list. Queued behind the edit it is not
        sent to the old address and not to the new one; refused by the old one as the edit lands, it is not
        retried at the new one (which may not have that input at all)."""
        self.fast()
        other = FakeProjector("pw1", inputs=("11", "33"), name="The other one")
        self.addCleanup(other.close)
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        asked_before = dict(self.stored())                              # what a request under way holds
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": other.port}})[0], 200)
        time.sleep(0.3)
        sent = len(self.fake.raw)
        with self.assertRaises(projector.ProjectorError) as e:
            self.api.projectors.set_input(asked_before, "31")
        self.assertEqual(e.exception.code, "stopped")
        self.assertIn("address was changed", str(e.exception))
        self.assertEqual(len(self.fake.raw), sent)                      # the old one got nothing after the edit
        # the edit lands while the old address is answering "unavailable" (it is in standby)
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": self.fake.port}})[0], 200)
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        held, real = dict(self.stored()), self.api._pjlink

        def link(entry):
            lk = real(entry)
            if entry["port"] == self.fake.port:
                send = lk.set_input

                def set_input(code):
                    try:
                        return send(code)                               # the old projector: ERR3
                    finally:
                        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "port": other.port}})[0], 200)
                lk.set_input = set_input
            return lk
        with mock.patch.object(self.api, "_pjlink", link):
            with self.assertRaises(projector.ProjectorError) as e:
                self.api.projectors.set_input(held, "31")
            self.assertEqual(e.exception.code, "stopped")
            self.assertTrue(wait_for(lambda: (self.projector()["details"] or {}).get("name") == "The other one"))
            self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
            time.sleep(0.6)                                             # six retry periods of a retry that must not exist
        self.assertIsNone(self.projector()["status"]["pending_input"])
        self.assertEqual([r for r in other.received if "INPT 3" in r or "INPT 1" in r], [])       # the new one got no input command
        sent = len(self.fake.raw)
        time.sleep(0.3)
        self.assertEqual(len(self.fake.raw), sent)
        # an input chosen after the edit, from what is stored now, goes to the new address as before
        self.assertEqual(self.api.projectors.set_input(self.stored(), "33"), {"pending": True})

    def test_groups_keep_pointing_at_an_edited_projector(self):
        with mock.patch.object(self.api.projectors, "apply", lambda: None):
            pid = self.add()[1]["projectors"][0]["id"]
            self.call("POST", "/api/modules/room", {"enabled": True}, token=self.full)
            st, body, _ = self.post("/api/room", {"group": {"name": "Main wall", "projectors": [pid]}})
            self.assertEqual(st, 200, body)
            self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "name": "Renamed", "host": "192.168.7.7"}})[0], 200)
            room = self.call("GET", "/api/room", token=self.full)[1]
            self.assertEqual(room["groups"][0]["projectors"], [pid])
            self.assertIn("Renamed", json.dumps(room))


class ScheduleAndOscTest(unittest.TestCase):
    def test_schedule_accepts_presets_and_projector_power(self):
        s = scheduler.validate({"enabled": True, "entries": [
            {"time": "18:00", "days": [0], "action": "projector_on"},
            {"time": "18:01", "days": [0], "action": "preset", "preset": "startlessonce01"},
            {"time": "23:59", "days": [0], "action": "projector_off"}]})
        self.assertEqual([e["action"] for e in s["entries"]], ["projector_on", "preset", "projector_off"])
        for bad in ({"action": "preset"}, {"action": "preset", "preset": "rm -rf /"}, {"action": "preset", "preset": "startwifi01"}):
            with self.assertRaises(scheduler.ScheduleError):
                scheduler.validate({"entries": [dict({"time": "10:00", "days": [1]}, **bad)]})

    def test_legacy_osc_beamer_addresses(self):
        self.assertEqual(osc.translate("/beameron", [1.0]), ("/api/projector", {"id": "all", "action": "on", "background": True}))
        self.assertEqual(osc.translate("/beameroff", [1.0]), ("/api/projector", {"id": "all", "action": "off", "background": True}))
        self.assertIsNone(osc.translate("/beameron", [0.0]))                                       # the release does nothing


class ScheduledProjectorTest(unittest.TestCase):
    def test_projector_entries_run_off_the_schedule_thread_and_record_the_result(self):
        import tempfile, os, time as _t
        from pvj.settings import Settings
        from pvj.modules import Registry
        gate = threading.Event()

        class Api:
            def projector_action(self, body, device, client):
                gate.wait(5)
                return {"results": {"p1": {"ok": False, "error": "cannot reach the projector"}}}
        st = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        st.load()
        s = scheduler.Scheduler(Api(), st, Registry(st, "x86"), log=lambda *_: None)
        e = {"id": "e1", "action": "projector_on"}
        start = _t.monotonic()
        s._run(e, __import__("datetime").datetime(2026, 9, 30, 18, 0))
        self.assertLess(_t.monotonic() - start, 0.5)                          # did not wait for the projector
        self.assertNotIn("e1", s.last)
        gate.set()
        end = _t.monotonic() + 5
        while "e1" not in s.last and _t.monotonic() < end:
            _t.sleep(0.02)
        self.assertEqual((s.last["e1"]["ok"], s.last["e1"]["message"]), (False, "cannot reach the projector"))


# --- Epson control, ESC/VP.net (D99) ------------------------------------------------------------------------------------
class FakeEpson:
    """An Epson projector's ESC/VP.net on 127.0.0.1, written from what an Epson X27 answered on 2026-10-19 (the
    coordinator's reads, not our client): the 16 byte "ESC/VP.net" header, version 0x10, type 0x03, is answered by
    16 bytes with the status at byte 14 (0x20 is OK); then a command ends with CR and every answer ends with ":";
    a query is answered "KEY=value" before it, a set that was taken by ":" alone, a refusal by "ERR". In standby only
    PWR, LAMP, ERR and SNO answer. BRIGHT 40 reads back as 36 (the X27 has steps). CMODE took 3.2 s; `cmode_wait`
    stands in for it. CMODE 0C, 07 and 14 are refused, as on the X27.

    `mode`: "ok"; "silent" (takes the connection and never answers); "short" (half a header, then closes); "deaf"
    (the header is answered, commands never are); "long" (an answer that never ends: 300 bytes, no ":")."""

    def __init__(self, status=0x20, mode="ok", modes=("01", "04", "05", "06"), cmode_wait=0.0, drip=0.0):
        self.status, self.mode, self.modes, self.cmode_wait, self.drip = status, mode, set(modes), cmode_wait, drip
        self.v = {"PWR": "01", "LAMP": "25", "ERR": "00", "MUTE": "OFF", "SOURCE": "30", "FREEZE": "OFF", "LUMINANCE": "00",
                  "BRIGHT": "125", "CMODE": "04", "SNO": "VU3K7601207", "CONTRAST": "125"}
        self.received, self.done_at, self.connections = [], {}, 0
        self.guard = threading.Lock()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                with outer.guard:
                    outer.connections += 1
                try:
                    head = self.rfile.read(16)
                    if outer.mode == "silent":
                        time.sleep(3)
                        return
                    if outer.mode == "short":
                        self.wfile.write(b"ESC/VP")
                        return
                    if len(head) < 16 or head[:10] != b"ESC/VP.net":
                        return
                    self.wfile.write(b"ESC/VP.net\x10\x03\x00\x00" + bytes([outer.status, 0]))
                    if outer.status != 0x20:
                        return
                    while True:
                        buf = b""
                        while not buf.endswith(b"\r") and len(buf) < 200:
                            c = self.rfile.read(1)
                            if not c:
                                return
                            buf += c
                        cmd = buf[:-1].decode("ascii", "replace")
                        outer.received.append(cmd)
                        if outer.mode == "deaf":
                            continue
                        if outer.mode == "long":
                            self.wfile.write(b"PWR=" + b"1" * 300)
                            continue
                        time.sleep(outer.drip)              # each answer just inside its own limit
                        self.wfile.write(outer.answer(cmd).encode())
                        outer.done_at[cmd] = time.monotonic()
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, cmd):
        on = self.v["PWR"] == "01"
        if cmd.endswith("?"):
            key = cmd[:-1]
            if key not in self.v or (not on and key not in ("PWR", "LAMP", "ERR", "SNO")):
                return "ERR\r:"
            return "%s=%s\r:" % (key, self.v[key])
        key, _, val = cmd.partition(" ")
        if key == "PWR" and val in ("ON", "OFF"):
            self.v["PWR"] = "01" if val == "ON" else "04"
            return ":"
        if not on:
            return "ERR\r:"
        if key in ("MUTE", "FREEZE") and val in ("ON", "OFF"):
            self.v[key] = val
            return ":"
        if key == "BRIGHT" and val.isdigit() and 0 <= int(val) <= 255:
            self.v[key] = str(int(val) - int(val) % 6)
            return ":"
        if key == "LUMINANCE" and val in ("00", "01"):
            self.v[key] = val
            return ":"
        if key == "CMODE" and val in self.modes:
            time.sleep(self.cmode_wait)
            self.v[key] = val
            return ":"
        return "ERR\r:"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class EscVpTest(unittest.TestCase):
    def setUp(self):
        LOOPBACK_OK.start()
        self.addCleanup(LOOPBACK_OK.stop)
        self.fake = FakeEpson()
        self.addCleanup(self.fake.close)

    def esc(self, fake=None, **kw):
        return projector.EscVp("127.0.0.1", lock_port=4352, port=(fake or self.fake).port, **kw)

    def test_the_status_in_one_connection(self):
        st = projector.epson_status(self.esc())
        self.assertEqual(self.fake.connections, 1)
        self.assertEqual((st["ok"], st["power"], st["lamps"], st["mute"]), (True, "on", [{"hours": 25, "on": True}], {"picture": False, "sound": False}))
        self.assertEqual(set(st["warnings"].values()), {"ok"})
        self.assertEqual(st["picture"], {"freeze": False, "light": "normal", "brightness": 125, "colour_mode": "04"})
        self.fake.v.update(PWR="04", ERR="04")
        st = projector.epson_status(self.esc())
        self.assertEqual((st["power"], st["warnings"]["temperature"], st["mute"]), ("off", "error", None))
        self.assertNotIn("picture", st, "in standby the picture settings are not known")
        for code, power in (("02", "warming up"), ("03", "cooling down"), ("09", "off")):
            self.fake.v["PWR"] = code
            self.assertEqual(projector.EpsonLink("127.0.0.1", port=self.fake.port).state(), power)

    def test_each_set_and_what_it_reads_back(self):
        e = self.esc()
        for cmd, key, want in (("MUTE ON", "MUTE", "ON"), ("FREEZE ON", "FREEZE", "ON"), ("BRIGHT 40", "BRIGHT", "36"),
                               ("LUMINANCE 01", "LUMINANCE", "01"), ("CMODE 06", "CMODE", "06"), ("FREEZE OFF", "FREEZE", "OFF")):
            self.assertEqual(e.send(cmd), "OK")
            self.assertEqual(e.query(key), want)
        with self.assertRaises(projector.ProjectorError) as c:
            e.send("CMODE 0C")
        self.assertEqual(c.exception.code, "ERR")
        self.assertIn("refused CMODE", str(c.exception))

    def test_a_refusal_does_not_end_the_others(self):
        a = self.esc().session(["DENSITY?", "PWR?", "VKEYST?", "BRIGHT?"])
        self.assertIsInstance(a[0], projector.ProjectorError)
        self.assertEqual((a[0].code, a[1], a[3]), ("ERR", "01", "125"))
        self.assertIsInstance(a[2], projector.ProjectorError)

    def test_a_slow_colour_mode_is_waited_for(self):
        self.fake.cmode_wait = 1.2
        self.assertEqual(self.esc(timeout=0.5, slow=3.0).send("CMODE 06"), "OK")          # a set of CMODE waits `slow`
        self.fake.cmode_wait = 1.2
        with self.assertRaises(projector.ProjectorError) as c:
            self.esc(timeout=0.5, slow=0.6).send("CMODE 05")
        self.assertEqual(c.exception.code, "unreachable")

    def test_a_refused_connection_is_said_in_plain_words(self):
        for status, words in ((0x41, "asks for a password"), (0x53, "busy with another Epson control"), (0x7E, "code 7E")):
            f = FakeEpson(status=status)
            self.addCleanup(f.close)
            with self.assertRaises(projector.ProjectorError) as c:
                self.esc(f).query("PWR")
            self.assertIn(words, str(c.exception))
            self.assertEqual(c.exception.code, "refused")

    def test_a_projector_that_never_answers_costs_a_bounded_wait(self):
        for mode in ("silent", "deaf"):
            f = FakeEpson(mode=mode)
            self.addCleanup(f.close)
            start = time.monotonic()
            with self.assertRaises(projector.ProjectorError) as c:
                self.esc(f, timeout=0.4, connect_timeout=0.4).query("PWR")
            self.assertEqual(c.exception.code, "unreachable", mode)
            self.assertLess(time.monotonic() - start, 2.0, mode)
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()
        with self.assertRaises(projector.ProjectorError) as c:
            projector.EscVp("127.0.0.1", port=port).query("PWR")
        self.assertIn("cannot reach", str(c.exception))

    def test_cut_short_overlong_and_not_epson(self):
        for mode, code in (("short", "odd"), ("long", "odd")):
            f = FakeEpson(mode=mode)
            self.addCleanup(f.close)
            with self.assertRaises(projector.ProjectorError) as c:
                self.esc(f, timeout=1.0).query("PWR")
            self.assertEqual(c.exception.code, code, mode)
        web = raw_server(self, lambda r, w: (r.read(16), w.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")))
        with self.assertRaises(projector.ProjectorError) as c:
            projector.EscVp("127.0.0.1", port=web).query("PWR")
        self.assertIn("does not answer like an Epson", str(c.exception))
        odd = raw_server(self, lambda r, w: (r.read(16), w.write(b"ESC/VP.net\x10\x03\x00\x00\x20\x00"), r.read(5), w.write(b"LAMP=12\r:")))
        with self.assertRaises(projector.ProjectorError) as c:
            projector.EscVp("127.0.0.1", port=odd).query("PWR")           # the answer to another question
        self.assertEqual(c.exception.code, "odd")
        for bad in (["PWR?\rMUTE ON"], ["pwr?"], [], ["PWR?"] * 17, [5]):
            with self.assertRaises(projector.ProjectorError):
                self.esc().session(bad)

    def test_only_private_addresses_and_nothing_sent_otherwise(self):
        LOOPBACK_OK.stop()
        try:
            def never(*a, **kw):
                raise AssertionError("connected")
            for host in ("8.8.8.8", "127.0.0.1", "169.254.169.254"):
                with self.assertRaises(projector.ProjectorError, msg=host):
                    projector.EscVp(host, connect=never).query("PWR")
        finally:
            LOOPBACK_OK.start()

    def test_a_slow_device_holds_the_lock_no_longer_than_the_session_limit(self):
        """Review of #151: each answer just inside its own 0.4 s, nine of them: the session ends at its own limit
        (2 x 0.4 + 0.4), and a PJLink command to the same projector then gets through."""
        pj = FakeProjector()
        self.addCleanup(pj.close)
        drip = FakeEpson(drip=0.3)
        self.addCleanup(drip.close)
        esc = projector.EscVp("127.0.0.1", lock_port=pj.port, port=drip.port, timeout=0.4, slow=0.4)
        self.assertAlmostEqual(esc.session_limit(), 1.2)
        failed, start = [], time.monotonic()

        def status():
            try:
                projector.epson_status(esc)
            except projector.ProjectorError as e:
                failed.append(e.code)
        t = threading.Thread(target=status)
        t.start()
        self.assertTrue(wait_for(lambda: drip.received))
        self.assertEqual(projector.PJLink("127.0.0.1", pj.port).state(), "off")       # waits for the lock, then goes
        took = pj.times[-1] - start
        t.join()
        self.assertEqual(failed, ["unreachable"])
        self.assertLess(took, 1.2 + 0.4, "the lock was held about the session's limit, not nine answers' worth (2.7 s)")
        self.assertEqual(projector._busy, {})

    def test_more_headers_than_any_projector_sends_are_refused(self):
        many = raw_server(self, lambda r, w: (r.read(16), w.write(b"ESC/VP.net\x10\x03\x00\x00\x20\x09"), time.sleep(2)))
        with self.assertRaises(projector.ProjectorError) as c:
            projector.EscVp("127.0.0.1", port=many, timeout=1.0).query("PWR")
        self.assertEqual(c.exception.code, "odd")

    def test_one_command_at_a_time_with_its_pjlink(self):
        pj = FakeProjector()
        self.addCleanup(pj.close)
        pj.power = "1"
        self.fake.cmode_wait = 0.6
        t = threading.Thread(target=lambda: projector.EscVp("127.0.0.1", lock_port=pj.port, port=self.fake.port).send("CMODE 06"))
        t.start()
        self.assertTrue(wait_for(lambda: "CMODE 06" in self.fake.received))
        projector.PJLink("127.0.0.1", pj.port).state()
        t.join()
        self.assertGreaterEqual(pj.times[-1], self.fake.done_at["CMODE 06"], "PJLink waited for the Epson command")
        self.assertEqual(projector._busy, {})

    def test_epson_alone_power_mute_and_what_it_cannot(self):
        link = projector.EpsonLink("127.0.0.1", port=self.fake.port)
        link.power(False)
        self.assertEqual((self.fake.v["PWR"], link.state()), ("04", "off"))
        link.power(True)
        link.mute(True)
        self.assertEqual((self.fake.v["MUTE"], link.mute_state()), ("ON", {"picture": True, "sound": True}))
        for f in (lambda: link.mute(True, "picture"), link.inputs, lambda: link.set_input("31")):
            with self.assertRaises(projector.ProjectorError):
                f()
        self.assertEqual(link.lamps()[0]["hours"], 25)
        self.assertEqual(link.identify()["info"], "serial VU3K7601207")


class ProbeTest(unittest.TestCase):
    def setUp(self):
        LOOPBACK_OK.start()
        self.addCleanup(LOOPBACK_OK.stop)
        self.ep = FakeEpson()
        self.addCleanup(self.ep.close)
        p = mock.patch.object(projector, "ESCVP_PORT", self.ep.port)
        p.start()
        self.addCleanup(p.stop)

    def test_what_answers_and_whether_a_password_is_needed(self):
        pj = FakeProjector()
        self.addCleanup(pj.close)
        found = projector.probe("127.0.0.1", pj.port)
        self.assertEqual((found["suggest"], found["pjlink"]["password"], found["pjlink"]["model"], found["epson"]["serial"]),
                         ("both", "none", "FP-1", "VU3K7601207"))
        locked = FakeProjector("JBMIAProjectorLink")
        self.addCleanup(locked.close)
        for given, said in (("", "needed"), ("JBMIAProjectorLink", "right"), ("guess", "wrong")):
            r = projector.probe("127.0.0.1", locked.port, given)["pjlink"]
            self.assertEqual(r["password"], said, given)
            self.assertNotIn("JBMIAProjectorLink", json.dumps(r))
        real = projector.PJLink.state

        def late(link):
            time.sleep(0.8)
            return real(link)
        with mock.patch.object(projector.PJLink, "state", late):
            found = projector.probe("127.0.0.1", pj.port, wait=0.3)
            frozen = json.dumps(found, sort_keys=True)
            self.assertEqual(found["pjlink"], {"ok": False, "error": "the projector did not answer in time"})
            time.sleep(1.5)                                   # the late thread ends now: the answer given is not changed
        self.assertEqual(json.dumps(found, sort_keys=True), frozen)
        self.ep.close()
        found = projector.probe("127.0.0.1", pj.port, timeout=1.0)
        self.assertEqual((found["suggest"], found["epson"]["ok"]), ("pjlink", False))
        self.assertIn("cannot reach", found["epson"]["error"])


class EpsonApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.addCleanup(self.api.projectors.stop, True)
        self.pj = FakeProjector("pw1")
        self.addCleanup(self.pj.close)
        self.pj.power = "1"
        self.ep = FakeEpson()
        self.addCleanup(self.ep.close)
        for p in (LOOPBACK_OK, mock.patch.object(projector, "ESCVP_PORT", self.ep.port)):
            p.start()
            self.addCleanup(p.stop)
        self.lines = []
        self.api.log = self.lines.append

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def add(self, protocol="both"):
        st, body, _ = self.post("/api/projectors", {"add": {"name": "Main", "host": "127.0.0.1", "port": self.pj.port, "password": "pw1", "protocol": protocol}})
        self.assertEqual(st, 200, body)
        return body["projectors"][0]["id"]

    def one(self):
        return self.call("GET", "/api/projectors", token=self.full)[1]["projectors"][0]

    def act(self, pid, action, **kw):
        return self.post("/api/projector", dict({"id": pid, "action": action}, **kw))

    def test_both_status_and_each_control(self):
        pid = self.add()
        self.assertEqual(self.one()["protocol"], "both")
        self.assertTrue(wait_for(lambda: (self.one()["status"] or {}).get("picture"), 10))
        pic = self.one()["status"]["picture"]
        self.assertEqual((pic["brightness"], pic["light"], pic["colour_mode"], pic["freeze"]), (125, "normal", "04", False))
        self.assertIn({"code": "04", "known": True}, pic["colour_modes"])
        self.assertEqual(self.act(pid, "freeze")[0], 200)
        self.assertEqual(self.ep.v["FREEZE"], "ON")
        self.assertEqual(self.act(pid, "unfreeze")[0], 200)
        self.assertEqual(self.act(pid, "brightness", value=200)[0], 200)
        self.assertEqual(self.ep.v["BRIGHT"], "198")
        self.assertEqual(self.act(pid, "light", value="eco")[0], 200)
        self.assertEqual(self.ep.v["LUMINANCE"], "01")
        self.assertEqual(self.act(pid, "colour_mode", value="06")[0], 200)
        self.assertEqual(self.ep.v["CMODE"], "06")
        st, body, _ = self.act(pid, "colour_mode", value="0C")
        self.assertEqual(st, 502)
        self.assertIn("does not have colour mode 0C", body["error"])
        self.assertTrue(wait_for(lambda: (self.one()["status"] or {}).get("picture", {}).get("colour_mode") == "06", 10))
        modes = {m["code"]: m["known"] for m in self.one()["status"]["picture"]["colour_modes"]}
        self.assertNotIn("0C", modes, "a refused mode is not offered again")
        self.assertTrue(modes["06"])
        self.assertEqual(self.act(pid, "mute")[0], 200)                    # mutes go over PJLink when it has both
        self.assertTrue(self.pj.video_mute and self.pj.audio_mute)
        self.assertEqual(self.ep.v["MUTE"], "OFF")
        self.assertEqual(self.act(pid, "off")[0], 200)
        self.assertEqual(self.pj.power, "0")

    def test_values_are_checked(self):
        pid = self.add()
        for action, value in (("brightness", 256), ("brightness", -1), ("brightness", "10"), ("brightness", True), ("light", "dim"),
                              ("colour_mode", "ZZ"), ("colour_mode", "4"), ("colour_mode", 4)):
            self.assertEqual(self.act(pid, action, value=value)[0], 400, (action, value))
        self.assertEqual(self.act("all", "brightness", value=10)[0], 400)
        self.assertEqual(self.act(pid, "on", value=1)[0], 400)
        self.assertEqual(self.ep.received, [c for c in self.ep.received if c.endswith("?")], "nothing was set")

    def test_pjlink_alone_has_no_freeze_and_is_stored_as_before(self):
        st, body, _ = self.post("/api/projectors", {"add": {"name": "Old", "host": "127.0.0.1", "port": self.pj.port, "password": "pw1"}})
        pid = body["projectors"][0]["id"]
        self.assertNotIn("protocol", self.settings.data["projectors"][0], "PJLink is stored as before D99")
        self.assertEqual(self.act(pid, "freeze")[0], 409)
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "protocol": "both"}})[0], 200)
        self.assertEqual(self.settings.data["projectors"][0]["protocol"], "both")
        self.assertEqual(self.act(pid, "freeze")[0], 200)
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "protocol": "pjlink"}})[0], 200)
        self.assertNotIn("protocol", self.settings.data["projectors"][0])
        self.assertEqual(self.post("/api/projectors", {"edit": {"id": pid, "protocol": "serial"}})[0], 400)

    def test_epson_alone(self):
        pid = self.add("epson")
        self.assertTrue(wait_for(lambda: (self.one()["status"] or {}).get("ok"), 10))
        st = self.one()["status"]
        self.assertEqual((st["power"], st["lamps"][0]["hours"], st["picture"]["brightness"]), ("on", 25, 125))
        self.assertEqual(self.act(pid, "mute")[0], 200)
        self.assertEqual(self.ep.v["MUTE"], "ON")
        self.assertEqual(self.act(pid, "off")[0], 200)
        self.assertEqual(self.ep.v["PWR"], "04")
        self.assertEqual(self.pj.received, [], "nothing went to PJLink")

    def test_the_owners_test_says_what_answers_and_saves_nothing(self):
        st, body, _ = self.post("/api/projectors", {"test": {"host": "127.0.0.1", "port": self.pj.port}})
        self.assertEqual(st, 200)
        t = body["test"]
        self.assertEqual((t["pjlink"]["ok"], t["pjlink"]["password"], t["epson"]["ok"], t["suggest"]), (False, "needed", True, "both"))
        t = self.post("/api/projectors", {"test": {"host": "127.0.0.1", "port": self.pj.port, "password": "pw1"}})[1]["test"]
        self.assertEqual((t["pjlink"]["password"], t["pjlink"]["maker"], t["suggest"]), ("right", "NXLX Test Works", "both"))
        self.assertEqual(self.settings.data["projectors"], [])
        pid = self.add()
        self.assertEqual(self.post("/api/projectors", {"test": {"id": pid}})[1]["test"]["pjlink"]["password"], "right")
        self.assertEqual(self.post("/api/projectors", {"test": {"host": "8.8.8.8"}})[0], 400)
        self.assertEqual(self.post("/api/projectors", {"test": {"id": "nope"}})[0], 404)
        live = self.post("/api/devices/invite", {"name": "op", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"test": {"host": "127.0.0.1"}}, token=live)[0], 403)

    def test_the_password_is_never_answered_or_logged(self):
        pid = self.add()
        answers = [self.call("GET", "/api/projectors", token=self.full)[1],
                   self.post("/api/projectors", {"test": {"host": "127.0.0.1", "port": self.pj.port, "password": "pw1"}})[1],
                   self.post("/api/projectors", {"test": {"id": pid}})[1],
                   self.post("/api/projectors", {"edit": {"id": pid, "name": "Wall"}})[1],
                   self.act(pid, "brightness", value=90)[1], self.act(pid, "freeze")[1], self.act(pid, "on")[1],
                   self.post("/api/projectors", {"test": {"host": "127.0.0.1", "port": self.pj.port, "password": "pw2"}})[1]]
        self.assertTrue(any("brightness 90" in l for l in self.lines), self.lines)
        self.assertTrue(any("freeze by device" in l for l in self.lines), self.lines)
        for a in answers:
            self.assertNotIn("pw1", json.dumps(a))
            self.assertNotIn("pw2", json.dumps(a))
        for line in self.lines:
            self.assertNotIn("pw1", line)
            self.assertNotIn("pw2", line)

    def test_viewing_checks_often(self):
        mon = self.api.projectors
        mon.interval, mon.changing = 60.0, 0.3
        self.add()
        self.assertTrue(wait_for(lambda: (self.one()["status"] or {}).get("ok"), 10))
        n = len(self.pj.received)
        self.assertTrue(wait_for(lambda: len(self.pj.received) > n, 3), "while the list is read, every `changing` seconds")
        mon._viewed = time.monotonic() - projector.VIEW_HOLD - 1     # nobody has looked for a while
        time.sleep(0.5)
        n = len(self.pj.received)
        time.sleep(1.0)
        self.assertEqual(len(self.pj.received), n, "back to every `interval` seconds")


class EpsonRolesBase(RolesBase):
    """Who may use the Epson controls (D99): the Owner and an Operator; a Guest none; remote support neither power
    off nor a picture setting, and freeze, mute and power on as before."""

    def session(self):
        from tests.test_support import CFG
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.owner)[0], 200)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        return self.api.support.authenticate(login["token"])

    def setUp(self):
        super().setUp()
        self.addCleanup(self.api.projectors.stop, True)
        self.ep = FakeEpson()
        self.addCleanup(self.ep.close)
        for p in (LOOPBACK_OK, mock.patch.object(projector, "ESCVP_PORT", self.ep.port)):
            p.start()
            self.addCleanup(p.stop)
        st, body = self.h("POST", "/api/projectors", {"add": {"name": "Wall", "host": "127.0.0.1", "port": 9, "protocol": "epson"}}, self.owner)
        self.assertEqual(st, 200, body)
        self.pid = body["projectors"][0]["id"]


class EpsonRolesTest(EpsonRolesBase):
    def test_owner_and_operator_use_them_a_guest_none(self):
        for dev in (self.owner, self.operator):
            self.assertEqual(self.h("POST", "/api/projector", {"id": self.pid, "action": "freeze"}, dev)[0], 200)
            self.assertEqual(self.h("POST", "/api/projector", {"id": self.pid, "action": "brightness", "value": 60}, dev)[0], 200)
            self.assertEqual(self.h("POST", "/api/projector", {"id": self.pid, "action": "light", "value": "eco"}, dev)[0], 200)
        for body in ({"id": self.pid, "action": "freeze"}, {"id": self.pid, "action": "brightness", "value": 1}, {"id": self.pid, "action": "mute"}):
            self.later()
            self.assertEqual(self.h("POST", "/api/projector", body, self.guest)[0], 403, body)
        self.assertEqual(self.h("POST", "/api/projectors", {"test": {"id": self.pid}}, self.operator)[0], 403)

    def test_remote_support_neither_switches_off_nor_changes_the_picture(self):
        dev = self.session()
        for body in ({"action": "off"}, {"action": "brightness", "value": 10}, {"action": "light", "value": "eco"},
                     {"action": "colour_mode", "value": "06"}, {"action": "off", "background": True}):
            st, out = self.h("POST", "/api/projector", dict(body, id=self.pid), dev, TUNNEL)
            self.assertEqual(st, 403, body)
            self.assertIn("not through remote support", out["error"])
        self.assertEqual((self.ep.v["PWR"], self.ep.v["BRIGHT"], self.ep.v["LUMINANCE"], self.ep.v["CMODE"]), ("01", "125", "00", "04"))
        for action in ("freeze", "unfreeze", "mute", "unmute", "on"):
            self.assertEqual(self.h("POST", "/api/projector", {"id": self.pid, "action": action}, dev, TUNNEL)[0], 200, action)
        log = json.dumps(self.settings.data["support_log"])
        self.assertIn("was refused a projector change that is made in the room", log, "written into the session's log")
        self.assertIn("used /api/projector", log)


class RemoteOffTest(EpsonRolesBase):
    """Review of #151: through remote support no route switches a projector off: a group, a scene, a scene changed
    to do it, a schedule entry added or changed to do it. The owner's own plan still runs at its time."""

    def setUp(self):
        super().setUp()
        with self.settings.lock:
            self.settings.data["modules"]["enabled"]["scheduler"] = True
            self.settings.save()
        self.dev = self.session()

    def remote(self, path, body):
        return self.h("POST", path, body, self.dev, TUNNEL)

    def still_on(self):
        time.sleep(0.5)
        return self.ep.v["PWR"] == "01"

    def test_a_group_or_a_scene_that_switches_off(self):
        for path, body in (("/api/room/group", {"group": "all", "action": "off"}), ("/api/room/group", {"number": 0, "action": "off"}),
                           ("/api/room/scene", {"name": "Good night"})):
            st, out = self.remote(path, body)
            self.assertEqual(st, 403, (path, body, out))
            self.assertIn("not through remote support", out["error"])
        self.assertTrue(self.still_on())
        self.assertEqual(self.remote("/api/room/group", {"group": "all", "action": "on"})[0], 200, "on is allowed")
        self.assertEqual(self.remote("/api/room/scene", {"name": "Show"})[0], 200, "a scene that switches nothing off is allowed")
        self.assertIn("a group switched off", json.dumps(self.settings.data["support_log"]))
        self.assertEqual(self.h("POST", "/api/room/group", {"group": "all", "action": "off"}, self.owner)[0], 200, "in the room it works")
        self.assertTrue(wait_for(lambda: self.ep.v["PWR"] == "04", 10))

    def scene(self, name):
        return next(s for s in self.settings.data["room"]["scenes"] if s["name"] == name)

    def test_a_scene_changed_to_switch_off(self):
        show = json.loads(json.dumps(self.scene("Show")))
        show["groups"][0]["power"] = "off"
        self.assertEqual(self.remote("/api/room", {"scene": show})[0], 403)
        self.assertEqual(self.remote("/api/room", {"scene": dict(show, id=None)})[0], 403, "nor a new scene that does")
        night = dict(self.scene("Good night"), name="Late night")
        self.assertEqual(self.remote("/api/room", {"scene": night})[0], 200, "a scene that already switched off may be renamed")
        show["groups"][0]["power"] = "on"
        self.assertEqual(self.remote("/api/room", {"scene": dict(show, name="Show 2")})[0], 200)
        self.assertEqual(self.h("POST", "/api/room", {"scene": dict(show, groups=[dict(show["groups"][0], power="off")])}, self.owner)[0], 200)

    def entries(self):
        return json.loads(json.dumps(self.settings.data["schedule"]["entries"]))

    def test_a_schedule_entry_that_switches_off(self):
        sched = lambda entries, dev=None: self.h("POST", "/api/schedule", {"enabled": True, "entries": entries}, dev or self.dev, TUNNEL if dev is None else LAN)
        off = {"time": "23:00", "days": [0, 1, 2, 3, 4, 5, 6], "action": "projector_off"}
        night = {"time": "23:30", "days": [5], "action": "scene", "scene": self.scene("Good night")["id"]}
        play = {"time": "18:00", "days": [0], "action": "stop"}
        for entries in ([off], [night], [play, off]):
            st, out = sched(entries)
            self.assertEqual(st, 403, entries)
            self.assertIn("not through remote support", out["error"])
        self.assertEqual(sched([play])[0], 200, "an entry that switches nothing off may be added")
        self.assertEqual(sched(self.entries() + [off], self.owner)[0], 200, "the owner plans an off in the room")
        kept = self.entries()
        self.assertEqual(sched(kept + [dict(play, time="19:00")])[0], 200, "the owner's off may stay as it is")
        changed = self.entries()
        for e in changed:
            if e["action"] == "projector_off":
                e["time"] = "23:59"
        self.assertEqual(sched(changed)[0], 403, "but not be moved")
        self.assertEqual(sched([e for e in self.entries() if e["action"] != "projector_off"])[0], 200, "or it may be removed")


class EpsonRollbackTest(unittest.TestCase):
    def test_the_release_before_reads_an_epson_projector_and_keeps_it(self):
        master = "6713348"                                       # master before D99 (D62's lesson: by its commit)
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", master + "^{commit}"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % master)
        from pvj.settings import Settings
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "settings.json")
        s = Settings(path)
        s.load()
        s.data["projectors"] = [dict(projector.validate({"name": "Wall", "host": "192.168.0.211", "protocol": "both", "password": "pw"}), id="0a0a0a0a")]
        s.save()
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", master, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        script = ("import json, sys\nfrom pvj.settings import Settings\nfrom pvj import projector\ns = Settings(sys.argv[1])\ns.load()\n"
                  "p = s.data['projectors'][0]\nl = projector.PJLink(p['host'], p['port'], p['password'])\n"
                  "print(json.dumps([p['name'], l.port, projector.validate_edit(p, {'id': p['id'], 'name': 'Wall 2'})]))\ns.save()\n")
        r = subprocess.run([sys.executable, "-c", script, path], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), ["Wall", 4352, {"name": "Wall 2"}], "the release before uses it as PJLink")
        with open(path) as f:
            self.assertEqual(json.load(f)["projectors"], s.data["projectors"], "and keeps the protocol for this release")


if __name__ == "__main__":
    unittest.main()
