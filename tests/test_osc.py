# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import random
import socket
import struct
import tempfile
import time
import unittest

from pvj import osc, themes as themes_mod
from pvj.api import Api
from pvj.auth import Auth
from pvj.modules import Registry
from pvj.osc import OscError, OscServer
from pvj.settings import Settings
from tests.test_server import FakePlayer


def pad(b):
    return b + b"\0" * (-len(b) % 4 or 0)


def s(text):
    raw = text.encode() + b"\0"
    return raw + b"\0" * (-len(raw) % 4)


def msg(address, *args):
    tags, body = ",", b""
    for a in args:
        if a is True:
            tags += "T"
        elif a is False:
            tags += "F"
        elif isinstance(a, bool):
            raise AssertionError
        elif isinstance(a, int):
            tags, body = tags + "i", body + struct.pack(">i", a)
        elif isinstance(a, float):
            tags, body = tags + "f", body + struct.pack(">f", a)
        elif isinstance(a, str):
            tags, body = tags + "s", body + s(a)
        elif isinstance(a, bytes):
            tags, body = tags + "b", body + struct.pack(">i", len(a)) + a + b"\0" * (-len(a) % 4)
    return s(address) + s(tags) + body


def bundle(*elements, tt=b"\0" * 7 + b"\1"):
    return b"#bundle\0" + tt + b"".join(struct.pack(">i", len(e)) + e for e in elements)


class ParserTest(unittest.TestCase):
    def test_round_trip_of_common_types(self):
        addr, args = osc.parse_message(msg("/mctl/x", 5, 1.5, "hello", True, False, b"\1\2\3"))
        self.assertEqual(addr, "/mctl/x")
        self.assertEqual(args, [5, 1.5, "hello", True, False, b"\1\2\3"])

    def test_string_padding_edge_lengths(self):
        for text in ("", "a", "abc", "abcd", "abcde"):
            self.assertEqual(osc.parse_message(msg("/p", text))[1], [text])

    def test_message_without_type_tags_is_accepted(self):
        self.assertEqual(osc.parse_message(s("/mctl/stop")), ("/mctl/stop", []))

    def test_rejects_malformed_messages(self):
        good = msg("/p", 1)
        bad = [b"", b"/p", b"p\0\0\0", s("/p") + b"i\0\0\0", s("/p") + s(",i"),  # missing payload
               s("/p") + s(",z") + b"\0\0\0\0", s("/p") + s(",b") + struct.pack(">i", -1),
               s("/p") + s(",b") + struct.pack(">i", 10 ** 9), good[:-1],
               b"/p\0\1" + s(",i") + b"\0\0\0\1",  # non-zero padding
               s("/p") + s(",s") + b"\xff\xfe\xfd\0", s("/" + "a" * 300),
               s("/p") + s("," + "i" * 20) + b"\0" * 80]
        for data in bad:
            with self.assertRaises(OscError, msg=repr(data[:24])):
                osc.parse_packet(data)

    def test_bundles_flatten_and_nesting_is_bounded(self):
        out = osc.parse_packet(bundle(msg("/a", 1), bundle(msg("/b", 2)), msg("/c")))
        self.assertEqual([m[0] for m in out], ["/a", "/b", "/c"])
        nested = msg("/x")
        for _ in range(osc.MAX_DEPTH + 1):
            nested = bundle(nested)
        with self.assertRaises(OscError):
            osc.parse_packet(nested)
        deep = msg("/x")
        for _ in range(1500):  # a datagram-sized recursion bomb must not exhaust the stack
            deep = bundle(deep)
            if len(deep) > osc.MAX_PACKET:
                break
        with self.assertRaises(OscError):
            osc.parse_packet(deep[:osc.MAX_PACKET])

    def test_bundle_element_and_size_abuse(self):
        for data in (b"#bundle\0", bundle(b""), b"#bundle\0" + b"\0" * 8 + struct.pack(">i", 3) + b"abc",
                     b"#bundle\0" + b"\0" * 8 + struct.pack(">i", 400),
                     b"#bundle\0" + b"\0" * 8 + struct.pack(">i", -4)):
            with self.assertRaises(OscError):
                osc.parse_packet(data)
        many = bundle(*[msg("/a")] * (osc.MAX_MESSAGES + 1))
        with self.assertRaises(OscError):
            osc.parse_packet(many)
        with self.assertRaises(OscError):
            osc.parse_packet(b"x" * (osc.MAX_PACKET + 1))

    def test_random_bytes_never_raise_anything_but_osc_error(self):
        rng = random.Random(1234)
        seeds = [msg("/mctl/opacity", 50.0), bundle(msg("/a", 1)), msg("/p", "x", b"yy")]
        for _ in range(4000):
            base = bytearray(rng.choice(seeds)) if rng.random() < 0.7 else bytearray(rng.randbytes(rng.randint(0, 64)))
            for _ in range(rng.randint(0, 4)):
                if base:
                    base[rng.randrange(len(base))] = rng.randrange(256)
            data = bytes(base[:rng.randint(0, len(base))]) if rng.random() < 0.3 else bytes(base)
            try:
                osc.parse_packet(data)
            except OscError:
                pass


class FilterTest(unittest.TestCase):
    def test_private_networks_allowed_public_refused(self):
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.9", "172.31.255.1", "192.168.1.50", "169.254.10.10",
                   "::1", "fe80::1%eth0", "fd12::1", "::ffff:192.168.1.5"):
            self.assertTrue(osc.source_allowed(ip), ip)
        for ip in ("8.8.8.8", "172.32.0.1", "192.169.0.1", "2001:db8::1", "::ffff:8.8.8.8", "garbage", ""):
            self.assertFalse(osc.source_allowed(ip), ip)

    def test_extra_networks(self):
        extra = osc.parse_networks(["203.0.113.0/24"])
        self.assertTrue(osc.source_allowed("203.0.113.9", extra))
        self.assertFalse(osc.source_allowed("203.0.114.9", extra))
        with self.assertRaises(OscError):
            osc.parse_networks(["nonsense"])

    def test_rate_limiter_refills_and_bounds_memory(self):
        t = [0.0]
        rl = osc.RateLimiter(clock=lambda: t[0], rate=10, burst=5, max_sources=4)
        self.assertEqual([rl.allow("a") for _ in range(7)], [True] * 5 + [False] * 2)
        t[0] += 1
        self.assertTrue(rl.allow("a"))
        for i in range(50):
            rl.allow("src%d" % i)
        self.assertLessEqual(len(rl._buckets), 4)


class TranslateTest(unittest.TestCase):
    def test_pads_are_one_based_and_only_the_press_fires(self):
        self.assertEqual(osc.translate("/mctl/pad/1/3", [1.0]), ("/api/play", {"pad": [0, 2]}))
        self.assertIsNone(osc.translate("/mctl/pad/1/3", [0.0]))  # button release
        self.assertEqual(osc.translate("/mctl/pad/2/12", []), ("/api/play", {"pad": [1, 11]}))
        self.assertEqual(osc.translate("/mctl/play/pad", [3, 4]), ("/api/play", {"pad": [2, 3]}))
        self.assertIsNone(osc.translate("/mctl/play/pad", [3]))
        self.assertIsNone(osc.translate("/mctl/pad/x/1", [1]))

    def test_continuous_values_and_types(self):
        self.assertEqual(osc.translate("/mctl/opacity", [42.5]), ("/api/control", {"action": "opacity", "value": 42.5}))
        self.assertEqual(osc.translate("/mctl/speed", [2]), ("/api/control", {"action": "speed", "value": 2}))
        self.assertEqual(osc.translate("/mctl/rotate", [90.0]), ("/api/control", {"action": "rotate", "value": 90}))
        for bad in ([], ["x"], [True], [float("nan")], [float("inf")], [None]):
            self.assertIsNone(osc.translate("/mctl/opacity", bad), bad)

    def test_toggles_and_flags(self):
        self.assertEqual(osc.translate("/mctl/blackout", [1.0]), ("/api/blackout", {"on": True}))
        self.assertEqual(osc.translate("/mctl/blackout", [False]), ("/api/blackout", {"on": False}))
        self.assertEqual(osc.translate("/mctl/blackout", [], {"blackout": True}), ("/api/blackout", {"on": False}))
        self.assertEqual(osc.translate("/mctl/pause", []), ("/api/control", {"action": "pause"}))
        self.assertEqual(osc.translate("/mctl/pause", [0]), ("/api/control", {"action": "pause", "value": False}))
        self.assertEqual(osc.translate("/mctl/mute", [1]), ("/api/control", {"action": "mute", "value": True}))
        self.assertIsNone(osc.translate("/mctl/loop", []))

    def test_legacy_names_still_work_and_dangerous_ones_do_not_exist(self):
        self.assertEqual(osc.translate("/startlessonce05", [1.0]), ("/api/play", {"preset": "startlessonce05"}))
        self.assertIsNone(osc.translate("/startlessonce05", [0.0]))
        self.assertEqual(osc.translate("/rotate180", []), ("/api/control", {"action": "rotate", "value": 180}))
        self.assertEqual(osc.translate("/volumeup", [1]), ("/api/control", {"action": "volume_step", "value": 10}))
        self.assertEqual(osc.translate("/fastforward", []), ("/api/control", {"action": "seek", "value": 10}))
        self.assertEqual(osc.translate("/stopall", []), ("/api/control", {"action": "stop"}))
        for dangerous in ("/shutdown", "/reboot", "/rebootall", "/shutdownall", "/factoryreset", "/updateall",
                          "/passwddisable", "/mctl/pin/rotate", "/mctl/modules/core", "/api/control"):
            self.assertIsNone(osc.translate(dangerous, [1]), dangerous)

    def test_trailing_slash_is_tolerated(self):
        self.assertEqual(osc.translate("/mctl/stop/", []), ("/api/control", {"action": "stop"}))


class ServerLogicTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        media = os.path.join(tmp, "video")
        os.makedirs(media)
        for n in ("a.mp4", "05_x.mp4"):
            open(os.path.join(media, n), "w").close()
        settings = Settings(os.path.join(tmp, "s.json"))
        settings.load()
        settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        settings.data["pads"]["banks"][0]["pads"][2] = {"label": "A", "file": "a.mp4"}
        self.player = FakePlayer(tmp)
        self.api = Api(self.player, settings, Auth(settings), Registry(settings, "x86"), themes_mod.load_themes(),
                       media, {"kind": "x86", "model": "t", "arch": "x86_64"})
        self.logs = []
        self.now = [0.0]
        self.server = OscServer(self.api, clock=lambda: self.now[0], log=self.logs.append)

    def send(self, data, ip="192.168.1.20"):
        return self.server.handle_packet(data, ip)

    def test_commands_reach_the_player(self):
        self.assertEqual(self.send(msg("/mctl/pad/1/3", 1.0)), 1)
        self.assertEqual(next(c for c in self.player.calls if c[0] == "play")[1][0].split("/")[-1], "a.mp4")
        self.send(msg("/mctl/opacity", 50.0))
        self.assertIn(("opacity", 127), self.player.calls)  # 50% of 255, floating point lands just under 127.5
        self.send(msg("/mctl/blackout", 1))
        self.assertTrue(self.api.mix["blackout"])
        self.send(msg("/mctl/speed", 2.0))
        self.assertIn(("speed", 2.0), self.player.calls)

    def test_bundle_runs_every_message(self):
        self.assertEqual(self.send(bundle(msg("/mctl/speed", 1.5), msg("/mctl/volume", 40.0), msg("/nothing"))), 2)

    def test_release_and_unknown_and_refused_do_nothing(self):
        before = list(self.player.calls)
        for data in (msg("/mctl/pad/1/3", 0.0), msg("/whatever", 1), msg("/shutdown", 1), msg("/reboot"),
                     msg("/mctl/play/file", "../../etc/passwd")):
            self.send(data)
        self.assertEqual(self.player.calls, before)
        self.assertTrue(any("not available over OSC" in line for line in self.logs))

    def test_invalid_values_are_rejected_by_the_api_not_applied(self):
        self.assertEqual(self.send(msg("/mctl/opacity", 500.0)), 0)
        self.assertEqual(self.send(msg("/mctl/rotate", 45)), 0)
        self.assertEqual(self.send(msg("/mctl/play/preset", "startmaster05; reboot")), 0)
        self.assertEqual([c for c in self.player.calls if c[0] in ("opacity", "rotate", "play")], [])

    def test_preset_over_osc(self):
        self.assertEqual(self.send(msg("/startlessonce05", 1.0)), 1)
        self.assertFalse(next(c for c in self.player.calls if c[0] == "play")[2])

    def test_public_source_dropped_and_extra_range_allowed(self):
        self.assertEqual(self.send(msg("/mctl/speed", 2.0), "8.8.8.8"), 0)
        self.assertEqual(self.server.stats["dropped"], 1)
        self.server.extra = osc.parse_networks(["203.0.113.0/24"])
        self.assertEqual(self.send(msg("/mctl/speed", 2.0), "203.0.113.7"), 1)

    def test_flood_is_rate_limited_and_logging_is_bounded(self):
        results = [self.send(msg("/mctl/speed", 1.0)) for _ in range(int(osc.RATE_BURST) + 200)]
        self.assertEqual(sum(results), int(osc.RATE_BURST))
        self.assertLessEqual(len([l for l in self.logs if "rate limit" in l]), 1)
        self.now[0] += 1
        self.assertEqual(self.send(msg("/mctl/speed", 1.0)), 1)

    def test_garbage_never_raises(self):
        rng = random.Random(7)
        for _ in range(1500):
            self.send(rng.randbytes(rng.randint(0, 200)))
        self.assertGreater(self.server.stats["dropped"], 0)

    def test_real_udp_socket_on_loopback(self):
        server = OscServer(self.api, port=0, host="127.0.0.1", log=self.logs.append)
        server.start()
        self.addCleanup(server.stop)
        self.assertTrue(server.listening)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.sendto(msg("/mctl/speed", 3.0), ("127.0.0.1", server.port))
        deadline = time.time() + 5
        while time.time() < deadline and ("speed", 3.0) not in self.player.calls:
            time.sleep(0.05)
        self.assertIn(("speed", 3.0), self.player.calls)
        sock.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(1024)  # the receiver never replies
        server.stop()
        self.assertFalse(server.listening)

    def test_port_in_use_is_a_clear_error(self):
        first = OscServer(self.api, port=0, host="127.0.0.1")
        first.start()
        self.addCleanup(first.stop)
        second = OscServer(self.api, port=first.port, host="127.0.0.1")
        with self.assertRaises(OscError):
            second.start()



class OldTouchOscNamesTest(unittest.TestCase):
    """The old panel's TouchOSC layouts: names whose features exist now (found in the manual deep dive)."""

    def test_startmasteronce_runs_the_play_once_presets(self):
        # the old receiver ran startmasteroneNN for /startmasteronceNN; it used to fail silently here
        self.assertEqual(osc.translate("/startmasteronce07", [1.0]), ("/api/play", {"preset": "startmasterone07"}))
        from pvj import presets
        self.assertFalse(presets.parse_legacy_name("startmasterone07")["loop"])
        self.assertIsNone(osc.translate("/startmasteronce07", [0.0]))         # the release does nothing

    def test_names_for_features_that_exist(self):
        cases = {"/testscreen": ("/api/testpattern", {"on": True}), "/testscreenoff": ("/api/testpattern", {"on": False}),
                 "/testtone": ("/api/testtone", {"channel": "both"}), "/testtoneleft": ("/api/testtone", {"channel": "left"}),
                 "/testtoneright": ("/api/testtone", {"channel": "right"}), "/overlay": ("/api/overlay", {"on": True}),
                 "/stopoverlay": ("/api/overlay", {"on": False}), "/image": ("/api/play", {"slideshow": {"source": "media"}}),
                 "/stopimage": ("/api/control", {"action": "stop"})}
        for address, want in cases.items():
            self.assertEqual(osc.translate(address, [1.0]), want, address)

    def test_flip_toggles_like_the_old_buttons(self):
        self.assertEqual(osc.translate("/fliph", [1.0], {"flip_h": False}), ("/api/control", {"action": "flip_h", "value": True}))
        self.assertEqual(osc.translate("/flipv", [], {"flip_v": True}), ("/api/control", {"action": "flip_v", "value": False}))

    def test_start_names_that_are_not_presets_do_nothing(self):
        for address in sorted(osc.NOT_HERE):
            self.assertIsNone(osc.translate(address, [1.0]), address)


if __name__ == "__main__":
    unittest.main()
