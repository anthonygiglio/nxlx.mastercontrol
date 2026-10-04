# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import socket
import struct
import threading
import time
import unittest

from pvj import dmx, midi
from pvj.dmx import DmxManager, DmxMapper, DmxServer
from pvj.midi import MidiHub, MidiParser
from pvj.settings import Settings
from tests.test_server import ServerBase


def artnet(universe, channels, opcode=0x5000, version=14):
    return (b"Art-Net\x00" + struct.pack("<H", opcode) + struct.pack(">H", version) + bytes([1, 0, universe & 0xFF, universe >> 8])
            + struct.pack(">H", len(channels)) + bytes(channels))


def sacn(universe, channels, options=0):
    d = bytearray(126 + len(channels))
    d[0:2] = b"\x00\x10"
    d[4:16] = b"ASC-E1.17\x00\x00\x00"
    d[18:22] = struct.pack(">I", 4)
    d[40:44] = struct.pack(">I", 2)
    d[112] = options
    d[113:115] = struct.pack(">H", universe)
    d[117], d[118] = 0x02, 0xA1
    d[123:125] = struct.pack(">H", len(channels) + 1)
    d[125] = 0
    d[126:] = bytes(channels)
    return bytes(d)


class Recorder:
    def __init__(self):
        self.calls = []
        self.status = 200

    def __call__(self, path, body):
        self.calls.append((path, body))
        return self.status == 200


class DmxParseTest(unittest.TestCase):
    def test_artnet(self):
        self.assertEqual(dmx.parse_artnet(artnet(258, [1, 2, 3, 4])), (258, b"\x01\x02\x03\x04"))
        for bad in (b"", b"Art-Net\x00", artnet(0, [1, 2], opcode=0x2000), artnet(0, [1, 2], version=13), artnet(0, [1]),
                    artnet(0, [0] * 512)[:100], b"x" * 40):
            self.assertIsNone(dmx.parse_artnet(bad))

    def test_sacn(self):
        self.assertEqual(dmx.parse_sacn(sacn(7, [9, 8, 7])), (7, b"\x09\x08\x07"))
        self.assertIsNone(dmx.parse_sacn(sacn(7, [1], options=0x80)))   # preview
        self.assertIsNone(dmx.parse_sacn(sacn(7, [1], options=0x40)))   # terminated
        self.assertIsNone(dmx.parse_sacn(sacn(7, [1, 2, 3])[:127]))     # cut short
        self.assertIsNone(dmx.parse_sacn(b"x" * 200))
        bad = bytearray(sacn(7, [1])); bad[125] = 1                      # not start code 0
        self.assertIsNone(dmx.parse_sacn(bytes(bad)))

    def test_junk_never_raises(self):
        import random
        rng = random.Random(1)
        for _ in range(500):
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
            dmx.parse_artnet(blob), dmx.parse_sacn(blob)
            dmx.parse_artnet(b"Art-Net\x00\x00\x50\x00\x0e" + blob), dmx.parse_sacn(sacn(1, [1, 2])[:40] + blob)


class DmxMapperTest(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        self.t = [100.0]
        self.m = DmxMapper(self.rec, start=1, clock=lambda: self.t[0])

    def frame(self, *v, at=None):
        if at is not None:
            self.t[0] = at
        return self.m.frame(bytes(list(v) + [0] * (8 - len(v))))

    def test_first_frame_is_only_a_baseline(self):
        self.assertEqual(self.frame(0, 0, 0, 0, 0, 255, 200, 255), 0)   # would blackout, pick a pad and fade
        self.assertEqual(self.rec.calls, [])
        self.assertEqual(self.frame(0, 0, 0, 0, 0, 255, 200, 255, at=101), 0)   # unchanged: still nothing

    def test_levels(self):
        self.frame(0)
        self.frame(255, 255, 255, 255, 255, at=101)
        self.assertEqual(self.rec.calls, [
            ("/api/control", {"action": "opacity", "value": 100.0}), ("/api/control", {"action": "size", "value": 200.0}),
            ("/api/control", {"action": "position", "value": 100.0}), ("/api/control", {"action": "speed", "value": 2.0}),
            ("/api/control", {"action": "volume", "value": 100.0})])

    def test_level_throttle_retries_on_the_next_frame(self):
        self.frame(0)
        self.frame(100, at=101.0)
        self.frame(200, at=101.01)      # too soon: skipped
        self.assertEqual(len(self.rec.calls), 1)
        self.frame(200, at=101.2)       # the next frame carries the change through
        self.assertEqual(self.rec.calls[-1], ("/api/control", {"action": "opacity", "value": round(200 / 255 * 100, 1)}))

    def test_blackout_switches_at_128(self):
        self.frame(0)
        self.frame(0, 0, 0, 0, 0, 127, at=101)
        self.assertEqual(self.rec.calls, [])
        self.frame(0, 0, 0, 0, 0, 128, at=102)
        self.frame(0, 0, 0, 0, 0, 255, at=103)     # still on: nothing new
        self.frame(0, 0, 0, 0, 0, 0, at=104)
        self.assertEqual(self.rec.calls, [("/api/blackout", {"on": True}), ("/api/blackout", {"on": False})])

    def test_pad_channel_ranges_and_retrigger(self):
        self.frame(0)
        self.frame(0, 0, 0, 0, 0, 0, 5, at=101)     # 0 to 5: idle
        self.frame(0, 0, 0, 0, 0, 0, 6, at=102)     # pad 1
        self.frame(0, 0, 0, 0, 0, 0, 11, at=103)    # still pad 1: does not fire again
        self.frame(0, 0, 0, 0, 0, 0, 13, at=104)    # pad 2
        self.frame(0, 0, 0, 0, 0, 0, 6 * 13, at=105)  # pad 13 = bank 2, index 0
        self.frame(0, 0, 0, 0, 0, 0, 255, at=106)   # above the last pad (6*37=222): nothing
        pads = [b["pad"] for p, b in self.rec.calls if p == "/api/play"]
        self.assertEqual(pads, [[0, 0], [0, 1], [1, 0]])

    def test_function_channel(self):
        self.frame(0)
        for at, v in enumerate((60, 0, 120, 0, 170, 0, 230), 101):
            self.frame(0, 0, 0, 0, 0, 0, 0, v, at=at)
        self.assertEqual([c for c in self.rec.calls], [
            ("/api/control", {"action": "stop"}), ("/api/control", {"action": "pause", "value": True}),
            ("/api/control", {"action": "pause", "value": False}), ("/api/fadeout", {"seconds": 2})])

    def test_dithering_inside_a_range_fires_once(self):
        self.frame(0)
        for at, v in enumerate((6, 7, 8, 11, 9, 6), 101):
            self.frame(0, 0, 0, 0, 0, 0, v, at=at)
        for at, v in enumerate((0, 50, 60, 99, 70, 0, 200, 230, 255, 210), 110):
            self.frame(0, 0, 0, 0, 0, 0, 0, v, at=at)
        self.assertEqual(self.rec.calls, [("/api/play", {"pad": [0, 0]}), ("/api/control", {"action": "stop"}), ("/api/fadeout", {"seconds": 2})])

    def test_a_level_that_failed_is_retried_and_a_returning_source_is_a_new_baseline(self):
        self.frame(0)
        self.rec.status = 500
        self.assertEqual(self.frame(100, at=101), 0)
        self.rec.status = 200
        self.assertEqual(self.frame(100, at=102), 1)                # the same value goes through on the next frame
        self.assertEqual(self.frame(0, 0, 0, 0, 0, 255, at=200), 0)  # 98 s of silence: this frame is only a baseline
        self.assertEqual(len(self.rec.calls), 2)                     # the failed try and the retry; nothing from the baseline
        self.assertEqual(self.frame(0, 0, 0, 0, 0, 0, at=201), 1)   # and later changes are acted on again

    def test_start_address_and_short_frames(self):
        m = DmxMapper(self.rec, start=10, clock=lambda: self.t[0])
        base = bytes(9) + bytes(8)
        m.frame(base)
        self.assertEqual(m.frame(bytes(9) + bytes([255]) + bytes(7)), 1)
        self.assertEqual(self.rec.calls[-1][1]["action"], "opacity")
        self.assertEqual(m.frame(bytes(12)), 0)           # universe too short for eight channels at 10

    def test_failed_calls_are_not_counted(self):
        self.rec.status = 500
        self.frame(0)
        self.assertEqual(self.frame(255, at=101), 0)


class DmxServerTest(ServerBase):
    def make(self, clock=time.monotonic, **over):
        cfg = {"enabled": True, "protocol": "artnet", "universe": 3, "start": 1, "allow": []}
        cfg.update(over)
        return DmxServer(self.api, cfg, host="127.0.0.1", log=lambda *_: None, clock=clock)

    def test_forged_sources_cannot_get_past_the_global_limits(self):
        srv = self.make(clock=lambda: 50.0)      # time stands still: only the burst allowance exists
        srv.handle_packet(artnet(3, [0] * 8), "10.0.0.1")
        handled = 0
        for i in range(2000):                    # every packet from a new private address
            level = i % 2 * 255
            handled += srv.handle_packet(artnet(3, [level] + [0] * 7), "10.1.%d.%d" % (i // 250, i % 250 + 1))
        self.assertLessEqual(srv.stats["received"] - srv.stats["dropped"], 501 + 2)
        self.assertLessEqual(srv.stats["handled"], 51)      # commands to the player are capped as well
        self.assertGreater(srv.stats["dropped"], 1000)

    def test_packets_end_to_end(self):
        srv = self.make()
        self.assertEqual(srv.handle_packet(artnet(3, [0] * 8), "127.0.0.1"), 0)      # baseline
        self.assertEqual(srv.handle_packet(artnet(4, [255] * 8), "127.0.0.1"), 0)    # other universe: ignored
        self.assertEqual(srv.handle_packet(artnet(3, [255] * 8), "8.8.8.8"), 0)      # public address: refused
        self.assertEqual(srv.stats["dropped"], 1)
        self.assertEqual(srv.handle_packet(artnet(3, [0, 0, 0, 0, 0, 255, 0, 0]), "127.0.0.1"), 1)
        self.assertTrue(self.api.mix["blackout"])
        self.assertIn(("opacity", 0), self.player.calls)

    def test_allow_list_extends_sources(self):
        srv = self.make(allow=["8.8.8.0/24"])
        srv.handle_packet(artnet(3, [0] * 8), "8.8.8.8")
        self.assertEqual(srv.handle_packet(artnet(3, [0, 0, 0, 0, 0, 255, 0, 0]), "8.8.8.8"), 1)

    def test_real_udp_socket(self):
        srv = self.make()
        srv.port = 0
        srv.cfg["protocol"] = "artnet"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
            srv.port = free
            srv.start()
            self.assertTrue(srv.listening)
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.addCleanup(tx.close)
            tx.sendto(artnet(3, [0] * 8), ("127.0.0.1", free))
            tx.sendto(artnet(3, [0, 0, 0, 0, 0, 255, 0, 0]), ("127.0.0.1", free))
            deadline = time.time() + 3
            while time.time() < deadline and not self.api.mix["blackout"]:
                time.sleep(0.02)
            self.assertTrue(self.api.mix["blackout"])
        finally:
            srv.stop()
        self.assertFalse(srv.listening)

    def test_a_port_in_use_is_reported(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", 0))
        self.addCleanup(blocker.close)
        srv = self.make()
        srv.port = blocker.getsockname()[1]
        with self.assertRaises(dmx.DmxError):
            srv.start()

    def test_sacn_protocol(self):
        srv = self.make(protocol="sacn", universe=5)
        srv.handle_packet(sacn(5, [0] * 8), "127.0.0.1")
        self.assertEqual(srv.handle_packet(sacn(5, [0, 0, 0, 0, 0, 200, 0, 0]), "127.0.0.1"), 1)
        self.assertEqual(srv.handle_packet(artnet(5, [0, 0, 0, 0, 0, 0, 0, 0]), "127.0.0.1"), 0)   # wrong protocol


class DmxValidateTest(unittest.TestCase):
    cur = {"enabled": False, "protocol": "artnet", "universe": 0, "start": 1, "allow": []}

    def test_good(self):
        new = dmx.validate({"enabled": True, "universe": 7, "start": 505, "allow": ["10.9.0.0/16"]}, self.cur)
        self.assertEqual((new["enabled"], new["universe"], new["start"], new["allow"]), (True, 7, 505, ["10.9.0.0/16"]))
        self.assertEqual(dmx.validate({"protocol": "sacn"}, self.cur)["universe"], 1)   # sACN has no universe 0

    def test_bad(self):
        for body in ({"enabled": 1}, {"protocol": "dmx512"}, {"universe": 32768}, {"universe": -1}, {"universe": True}, {"universe": "1"},
                     {"protocol": "sacn", "universe": 0}, {"start": 0}, {"start": 506}, {"start": 1.5}, {"start": True},
                     {"allow": ["0.0.0.0/0"]}, {"allow": "x"}):
            with self.assertRaises(dmx.DmxError, msg=str(body)):
                dmx.validate(body, self.cur)


class MidiParserTest(unittest.TestCase):
    def test_messages_and_running_status(self):
        p = MidiParser()
        self.assertEqual(p.feed(bytes([0x90, 36, 100, 37, 90, 36, 0])), [("on", 0, 36, 100), ("on", 0, 37, 90), ("off", 0, 36, 0)])
        self.assertEqual(p.feed(bytes([0xB2, 20, 64, 0xC1, 5, 0x81, 40, 0])), [("cc", 2, 20, 64), ("program", 1, 5, 0), ("off", 1, 40, 0)])

    def test_split_reads_realtime_and_sysex(self):
        p = MidiParser()
        self.assertEqual(p.feed(bytes([0x90, 36])), [])
        self.assertEqual(p.feed(bytes([0xF8, 100, 0xFE])), [("on", 0, 36, 100)])
        self.assertEqual(p.feed(bytes([0xF0, 1, 2, 3, 0x7F, 0xF7, 0xB0, 20, 1])), [("cc", 0, 20, 1)])
        self.assertEqual(p.feed(bytes([0xF0, 1, 2])), [])
        self.assertEqual(p.feed(bytes([3, 4, 5])), [])            # still inside the sysex: ignored
        self.assertEqual(p.feed(bytes([0xF7])), [])
        self.assertEqual(p.feed(bytes([40, 50])), [])             # system exclusive cancelled running status

    def test_garbage_never_raises(self):
        import random
        rng = random.Random(2)
        p = MidiParser()
        for _ in range(300):
            p.feed(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60))))


class ControlApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.api.dmx = DmxManager(self.api, self.settings, host="127.0.0.1", log=lambda *_: None)
        self.api.midi = MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=lambda p: (_ for _ in ()).throw(OSError()),
                                lister=lambda: [], scan_interval=0.05)
        self.addCleanup(self.api.dmx.stop)
        self.addCleanup(self.api.midi.stop)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def test_gated_by_module(self):
        for path in ("/api/dmx", "/api/midi"):
            self.assertEqual(self.call("GET", path, token=self.full)[0], 409)
            self.assertEqual(self.call("POST", path, {"enabled": False}, token=self.full)[0], 409)

    def test_dmx_roundtrip_and_port_clash_keeps_last_config(self):
        self.call("POST", "/api/modules/control-dmx", {"enabled": True}, token=self.full)
        st, body, _ = self.call("GET", "/api/dmx", token=self.full)
        self.assertEqual((st, body["enabled"], body["listening"], body["port"]), (200, False, False, 6454))
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        blocker.bind(("127.0.0.1", 6454))
        self.addCleanup(blocker.close)
        st, body, _ = self.call("POST", "/api/dmx", {"enabled": True, "universe": 2}, token=self.full)
        self.assertEqual(st, 409)       # the port is taken: the last working configuration is kept
        self.assertFalse(self.settings.data["control"]["dmx"]["enabled"])
        self.assertEqual(self.settings.data["control"]["dmx"]["universe"], 0)
        self.assertEqual(self.call("POST", "/api/dmx", {"universe": 99999}, token=self.full)[0], 400)

    def test_any_failure_while_applying_reverts_and_answers_409(self):
        self.call("POST", "/api/modules/control-dmx", {"enabled": True}, token=self.full)
        before = dict(self.settings.data["control"]["dmx"])
        real = self.api.dmx.apply
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] == 1:
                raise KeyError("hand-edited settings")
            return real()
        self.api.dmx.apply = flaky
        st, body, _ = self.call("POST", "/api/dmx", {"universe": 4}, token=self.full)
        self.assertEqual(st, 409)
        self.assertEqual(self.settings.data["control"]["dmx"], before)

    def test_roles(self):
        self.call("POST", "/api/modules/control-midi", {"enabled": True}, token=self.full)
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/midi", token=live)[0], 200)      # a presenter may look at the controllers; changing them is full
        self.assertEqual(self.call("POST", "/api/midi", {"enabled": False}, token=live)[0], 403)
        self.assertEqual(self.call("GET", "/api/dmx")[0], 401)


if __name__ == "__main__":
    unittest.main()
