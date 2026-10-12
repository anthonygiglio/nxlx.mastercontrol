# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""OSC state feedback (D82): off by default, the Owner's targets only, capped, and never an answer to a stranger."""
import collections
import json
import os
import re
import socket
import subprocess
import textwrap
import threading
import time
import unittest

from pvj import boxcare, osc, oscfeedback as fb, policy, support
from tests.test_osc import bundle, msg
from tests.test_osc_lockdown import KEY, LayerBase, OTHER, TABLET
from tests.test_shader_engine import ALL, Live
from tests.test_support import LAN, TUNNEL

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLDER = "b786488"       # master where this branch left it: the release before feedback existed, for the rollback test
BOX = "192.168.1.5"
IP_JSON = [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
           {"ifname": "eth0", "addr_info": [{"family": "inet", "local": BOX, "prefixlen": 24},
                                            {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]},
           {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.99.0.2", "prefixlen": 24}]}]
NETS = fb.local_nets(IP_JSON)
T1 = {"name": "Desk tablet", "address": TABLET, "port": 9000}
T2 = {"name": "Second tablet", "address": OTHER, "port": 9001}


class Sock:
    """A socket that only remembers: (bytes, (address, port), when)."""
    def __init__(self, clock=None, fail=None):
        self.sent, self.clock, self.fail = [], clock or (lambda: 0.0), fail

    def sendto(self, data, where):
        if self.fail:
            raise self.fail
        self.sent.append((bytes(data), where, self.clock()))

    def close(self):
        pass


def decode(sent):
    """Every message of every datagram: [(address, [args], where)], through the receiver's own strict parser."""
    return [(a, args, where) for data, where, _t in sent for a, args in osc.parse_packet(data)]


class Base(LayerBase):
    def setUp(self):
        super().setUp()
        self.api._ip_json = lambda: IP_JSON
        self.sock = Sock(self.clock)
        self.token, self.owner = self.device("full", "192.168.1.9", "Owner-Laptop-Sentinel")

    def feedback(self, targets=(T1,), **kw):
        kw.setdefault("allowed", self.manager._may_be_sent)
        f = fb.Feedback(self.api, list(targets), nets=lambda: fb.local_nets(self.api._ip_json()), clock=self.clock,
                        log=self.logs.append, sock=self.sock, **kw)
        self.server.feedback = f
        return f

    def post(self, body, device=None, client=LAN):
        return self.api.handle("POST", "/api/osc/feedback", body, device or self.owner, client)

    def run_for(self, f, seconds, step=0.05):
        end = self.now[0] + seconds
        while self.now[0] < end:
            f.tick()
            self.now[0] += step


class OffByDefaultTest(Base):
    def test_a_fresh_box_has_it_off_with_no_targets_and_no_sender(self):
        self.assertEqual(fb.stored(self.settings.data["osc"]), (False, []))
        self.assertNotIn("feedback_on", self.settings.data["osc"])
        self.settings.data["osc"].update(enabled=True, port=0)
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        self.assertIsNone(self.manager.feedback)
        self.assertIsNone(self.manager.server.feedback)

    def test_off_sends_nothing_under_any_input(self):
        made = []
        real = socket.socket

        def spy(*a, **kw):
            s = real(*a, **kw)
            made.append(s)
            return s
        socket.socket, before = spy, threading.active_count()
        self.addCleanup(setattr, socket, "socket", real)
        self.settings.data["osc"].update(enabled=True, port=0, feedback=[T1])       # a target, and the switch off
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        server = self.manager.server
        for data in (msg("/pvj/state"), msg("/pvj/state", 1), msg("/pvj/state/alive", 1.0), msg("/pvj/stop"), msg("/pvj/opacity", 50.0),
                     bundle(msg("/pvj/state"), msg("/pvj/blackout", 1)), b"junk", b"", msg("/k/%s/pvj/state" % KEY)):
            for ip in (TABLET, OTHER, "8.8.8.8", "127.0.0.1"):
                server.handle_packet(data, ip)
        self.assertIsNone(self.manager.feedback)
        self.assertEqual(len(made), 1, "the receiver's own socket and no other")
        self.assertEqual(threading.active_count(), before + 1, "the receiver's thread and no sender")
        self.assertEqual(self.why(TABLET), osc.NO_STATE)

    def test_only_a_real_true_is_on_and_a_damaged_list_is_no_list(self):
        for cfg in ({"feedback_on": "yes", "feedback": [T1]}, {"feedback_on": 1, "feedback": [T1]}, {"feedback": [T1]}):
            self.assertFalse(fb.stored(cfg)[0], cfg)
        for bad in ("x", [T1, "x"], [dict(T1, address="8.8.8.8")], [T1] * 5, {"a": 1}):
            self.assertEqual(fb.stored({"feedback_on": True, "feedback": bad}), (False, []), bad)

    def test_it_runs_only_while_osc_listens_the_switch_is_on_and_there_is_a_target(self):
        cfg = self.settings.data["osc"]
        cfg.update(enabled=False, port=0, feedback_on=True, feedback=[T1])
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        self.assertIsNone(self.manager.feedback, "OSC itself is off")
        cfg.update(enabled=True, feedback=[])
        self.manager.apply()
        self.assertIsNone(self.manager.feedback, "nobody to send to")
        cfg.update(feedback=[T1])
        self.manager.apply()
        f = self.manager.feedback
        self.assertTrue(f is not None and f.running and self.manager.server.feedback is f)
        cfg.update(port=self.manager.server.port)
        self.manager.apply()
        self.assertIs(self.manager.feedback, f, "a save of the page keeps the one sender")
        cfg.update(feedback_on=False)
        self.manager.apply()
        self.assertIsNone(self.manager.feedback)
        self.assertIsNone(self.manager.server.feedback)
        self.assertFalse(f.running)
        self.assertFalse(any(t.name == "osc-feedback" and t.is_alive() for t in threading.enumerate()))


class TargetTest(Base):
    def refused(self, **change):
        with self.assertRaises(fb.FeedbackError, msg=change):
            fb.validate_targets([dict(T1, **change)], NETS)

    def test_what_may_not_be_a_target(self):
        for address in ("8.8.8.8", "1.1.1.1", "192.168.1.255", "192.168.1.0", "255.255.255.255", "224.0.0.251", "239.255.255.250",
                        "127.0.0.1", BOX, "fe80::2", "::1", "::ffff:192.168.1.20", "tablet.local", "tablet", "192.168.1.20/32",
                        "192.168.001.020", " 192.168.1.20", "192.168.1.20 ", "192.168.2.20", "10.99.0.7", "172.16.0.9", "169.254.3.4",
                        "100.64.0.1", "192.0.2.1", "", None, 3232235796, ["192.168.1.20"], "192.168.1.20\n"):
            self.refused(address=address)
        for port in (0, -1, 65536, 99999, 53, 123, 1023, "9000", 9000.0, True, None):
            self.refused(port=port)
        for name in ("", " ", "x" * 33, "a\nb", None, 5):
            self.refused(name=name)
        with self.assertRaises(fb.FeedbackError):
            fb.validate_targets([T1, {"name": "x", "address": TABLET, "port": 9000, "extra": 1}], NETS)
        with self.assertRaises(fb.FeedbackError):
            fb.validate_targets([T1, dict(T1, name="again")], NETS)
        five = [dict(T1, port=9000 + n) for n in range(5)]
        with self.assertRaises(fb.FeedbackError):
            fb.validate_targets(five, NETS)
        self.assertEqual(fb.validate_targets(five[:4] and [T1, T2], NETS), [T1, T2])
        self.assertEqual(len(fb.validate_targets(five[:4], NETS)), 4)

    def test_only_the_box_s_real_ports_are_its_own_networks(self):
        """Wired (eth0, end0, enx...) and Wi-Fi with its hotspot (wlan0) count; what a container, a bridge or a VPN
        makes does not."""
        def nets(name):
            return fb.local_nets([{"ifname": name, "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]}])
        for name in ("docker0", "veth1a2b", "br-4f5e", "tun0", "tap0", "wg0", "wg-pvj", "lo", "virbr0", "tailscale0", "", None, 5):
            self.assertEqual(nets(name), [], name)
        for name in ("eth0", "end0", "enx00e04c680001", "enp1s0", "wlan0", "wlan1", "usb0"):
            self.assertEqual(len(nets(name)), 1, name)

    def test_the_box_s_networks_leave_out_loopback_the_tunnel_ipv6_and_what_cannot_be_read(self):
        self.assertEqual([str(n) for n in NETS], ["192.168.1.5/24"])
        self.assertEqual(fb.local_nets(None), [])
        self.assertEqual(fb.local_nets([{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "x", "prefixlen": 24}, {"family": "inet"}]}, 5]), [])
        public = fb.local_nets([{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "8.8.8.1", "prefixlen": 24}]}])
        self.assertIsNotNone(fb.not_local("8.8.8.8", public), "a box with a public address still sends to no public address")
        wide = fb.local_nets([{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 0}]}])
        self.assertIsNotNone(fb.not_local("192.168.9.9", wide), "a subnet wider than the private ranges is not a local network")
        link = fb.local_nets([{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "169.254.7.1", "prefixlen": 16}]}])
        self.assertIsNone(fb.not_local("169.254.3.4", link), "a direct cable")

    def test_the_route_adds_and_removes_and_no_other_route_or_packet_does(self):
        st, out = self.post({"add": T1})
        self.assertEqual((st, [t["address"] for t in out["targets"]], out["on"]), (200, [TABLET], False))
        for bad in ({"add": dict(T1, address="8.8.8.8")}, {"add": dict(T1, port=0)}, {"add": T1}, {"on": "yes"}, {}, {"targets": [T2]},
                    {"add": "192.168.1.20"}, {"remove": {"address": OTHER, "port": 1}}):
            self.assertIn(self.post(bad)[0], (400, 404), bad)
        for n in range(3):
            self.assertEqual(self.post({"add": dict(T2, port=9100 + n)})[0], 200)
        self.assertEqual(self.post({"add": dict(T2, port=9200)})[0], 400, "a fifth")
        self.assertEqual(self.post({"remove": {"address": OTHER, "port": 9100}})[0], 200)
        self.assertEqual(len(self.settings.data["osc"]["feedback"]), 3)
        saved = json.load(open(self.settings.path))["osc"]
        self.assertEqual((saved["feedback_on"], len(saved["feedback"])), (False, 3))
        # the OSC page's own save carries the keys over and takes none from its body
        before = json.dumps(self.settings.data["osc"]["feedback"])
        st, out = self.api.handle("POST", "/api/osc", {"feedback_on": True, "feedback": [dict(T1, address="192.168.1.77")], "enabled": False}, self.owner, LAN)
        self.assertEqual(st, 200, out)
        self.assertEqual((self.settings.data["osc"]["feedback_on"], json.dumps(self.settings.data["osc"]["feedback"])), (False, before))
        # and no packet adds one: a flood of requests from a stranger leaves the list as it was
        self.rules()
        for _ in range(50):
            self.send(msg("/pvj/state"), "192.168.1.77")
        self.assertEqual(json.dumps(self.settings.data["osc"]["feedback"]), before)

    def test_with_the_box_s_networks_unreadable_nothing_is_added(self):
        self.api._ip_json = lambda: []
        self.assertEqual(self.post({"add": T1})[0], 409)
        self.assertEqual(fb.stored(self.settings.data["osc"]), (False, []))

    def test_the_form_fills_from_the_last_sender_that_passed_and_nothing_is_added_by_it(self):
        self.rules(only_on=True, only=[TABLET])
        self.send(msg("/pvj/stop"), OTHER)                 # refused by the list: never suggested
        self.assertEqual(self.manager.feedback_status()["sender"], "")
        self.send(msg("/pvj/stop"), TABLET)
        out = self.manager.feedback_status()
        self.assertEqual((out["sender"], out["targets"]), (TABLET, []))


class OwnerOnlyTest(Base):
    def test_only_the_owner_at_the_studio(self):
        _t, operator = self.device("live", "192.168.1.31", "op")
        _t, guest = self.device("view", "192.168.1.32", "guest")
        remote = dict(self.owner, remote=True)
        from pvj import dmx, midi, room
        for who in (None, guest, operator, midi.MIDI_DEVICE, osc.OSC_DEVICE, dmx.DMX_DEVICE, room.ROOM_DEVICE,
                    {"id": "support-1", "name": "s", "role": "view", "remote": True}, {"id": "support-1", "name": "s", "role": "live", "remote": True}):
            for body in ({"on": True}, {"add": T1}):
                st, out = self.api.handle("POST", "/api/osc/feedback", body, who, LAN)
                self.assertIn(st, (401, 403), (who, out))
        self.assertEqual(self.api.osc_feedback.__func__(self.api, {"on": True}, self.owner, LAN)["on"], True)
        with self.assertRaises(Exception) as e:
            self.api.osc_feedback({"on": False}, remote, TUNNEL)
        self.assertEqual(e.exception.status, 403)
        self.assertIn(("POST", "/api/osc/feedback"), support.REMOTE_DENY)
        self.assertNotIn(("POST", "/api/osc/feedback"), policy.LEGACY_LIVE)
        self.assertNotIn(("POST", "/api/osc/feedback"), policy.GUEST)
        self.assertEqual(fb.stored(self.settings.data["osc"]), (True, []), "nothing but the owner's own call changed it")

    def test_the_targets_are_shown_to_the_owner_at_the_studio_only(self):
        self.post({"add": T1})
        _t, operator = self.device("live", "192.168.1.31", "op")
        _t, guest = self.device("view", "192.168.1.32", "guest")
        for who in (operator, guest, dict(self.owner, remote=True), osc.OSC_DEVICE):
            out = self.api.get_osc({}, who, LAN)            # the handler itself: a support session of the Owner's role too
            self.assertIn("enabled", out)
            self.assertNotIn("feedback", out, who)
            self.assertNotIn(TABLET, json.dumps(out))
        out = self.api.set_osc({"port": self.settings.data["osc"]["port"]}, dict(self.owner, remote=True), TUNNEL)
        self.assertIn("senders", out)
        self.assertNotIn(TABLET, json.dumps(out), "the answer to a save of the page through support names no target")
        st, out = self.api.handle("GET", "/api/osc", {}, self.owner, LAN)
        self.assertEqual(out["feedback"]["targets"][0]["address"], TABLET)

    def test_a_support_session_reads_the_targets_in_no_export_and_nobody_in_a_diagnostics_file(self):
        """The review of #123: the settings export and the diagnostics file carried the osc section whole."""
        self.post({"add": dict(T1, name="Desk-Name-Sentinel")})
        self.post({"on": True})
        care, remote = self.api.boxcare, dict(self.owner, remote=True)
        mine = care.export({}, self.owner, LAN)["file"]["settings"]["osc"]
        self.assertEqual((mine["feedback_on"], mine["feedback"][0]["address"]), (True, TABLET), "the Owner's own export holds them, as settings")
        self.api.support.is_remote = lambda client: client == TUNNEL
        for device, client in ((remote, LAN), (self.owner, TUNNEL), (remote, TUNNEL)):
            got = care.export({}, device, client)
            self.assertIn("enabled", got["file"]["settings"]["osc"])
            for word in (TABLET, "Desk-Name-Sentinel", "feedback"):
                self.assertNotIn(word, json.dumps(got), (word, client))
        for device, client in ((self.owner, LAN), (remote, TUNNEL)):
            got = care.diagnostics({}, device, client)["file"]
            self.assertIn("enabled", got["settings"]["osc"])
            for word in (TABLET + '"', "Desk-Name-Sentinel", "feedback"):
                self.assertNotIn(word, json.dumps(got["settings"]), (word, client))
        self.assertNotIn("Desk-Name-Sentinel", json.dumps(care.diagnostics({}, self.owner, LAN)))

    def test_a_new_target_is_added_beside_one_that_is_no_longer_on_this_network(self):
        self.post({"add": T1})
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.0.0.5", "prefixlen": 24}]}]
        st, out = self.post({"add": {"name": "New", "address": "10.0.0.20", "port": 9000}})
        self.assertEqual(st, 200, out)
        self.assertEqual([t["address"] for t in out["targets"]], [TABLET, "10.0.0.20"])
        self.assertEqual(out["targets"][0]["why"], fb.WHY_NETWORK, "the stale one is said to be stale at once, before any tick")
        self.assertEqual(out["targets"][1]["why"], "")
        st, out = self.post({"add": {"name": "Old net", "address": "192.168.1.30", "port": 9000}})
        self.assertEqual(st, 400)
        self.assertIn("192.168.1.30", out["error"])
        self.assertEqual(self.post({"add": {"name": "Twice", "address": "10.0.0.20", "port": 9000}})[0], 400)
        self.assertEqual(self.post({"remove": {"address": TABLET, "port": 9000}})[0], 200)

    def test_a_removed_owner_changes_nothing(self):
        self.auth.revoke(self.owner["id"])
        self.assertEqual(self.post({"on": True})[0], 401)

    def test_box_care_takes_the_form_and_an_older_file_cannot_switch_it(self):
        class Care:
            def note(self, *_):
                pass
        ok = boxcare.check_osc({"enabled": True, "port": 9876, "allow": [], "feedback_on": True, "feedback": [dict(T1, address="10.7.7.7")]}, Care())
        self.assertEqual((ok["feedback_on"], ok["feedback"][0]["address"]), (True, "10.7.7.7"))
        for bad in ({"feedback_on": "yes"}, {"feedback": [dict(T1, address="8.8.8.8")]}, {"feedback": [T1] * 5}, {"feedback": [dict(T1, port=53)]}):
            with self.assertRaises(boxcare.CHECK_ERRORS, msg=bad):
                boxcare.check_osc(dict({"enabled": True, "port": 9876, "allow": []}, **bad), Care())
        new = {"osc": {"enabled": True, "port": 9876, "allow": []}}
        boxcare.BoxCare._osc_layers(new, {"osc": {"feedback_on": True, "feedback": [T1]}})
        self.assertEqual((new["osc"]["feedback_on"], new["osc"]["feedback"]), (True, [T1]), "a file that says nothing leaves the box's own")
        # a file from another building: its target is kept as a setting and is sent nothing here
        f = self.feedback([dict(T1, address="10.7.7.7")])
        self.run_for(f, 3)
        self.assertEqual(self.sock.sent, [])
        self.assertEqual(f.status()[0]["why"], fb.WHY_NETWORK)


class StateTest(Base):
    def play(self):
        self.player.running = True
        self.player.status = lambda: {"running": True, "path": "/secret/folder/05_x.mp4", "position": 12.7, "duration": 60.0, "paused": False,
                                      "speed": 1.5, "volume": 80, "muted": True, "loop_file": "inf", "loop_playlist": False,
                                      "playlist_pos": 1, "playlist_count": 3}

    def test_every_packet_is_a_documented_address_with_a_float_or_a_string(self):
        self.play()
        self.settings.data["pads"]["banks"][1]["pads"][2]["file"] = "05_x.mp4"
        self.api.mix.update(opacity=40, size=150, position=-20, position_y=7.5, blackout=True)
        f = self.feedback()
        self.run_for(f, 1)
        got = {a: args for a, args, where in decode(self.sock.sent)}
        for a, args in got.items():
            self.assertTrue(a.startswith(fb.PREFIX), a)
            self.assertEqual(len(args), 1, a)
            self.assertIn(type(args[0]), (float, str), a)
        for data, where, _t in self.sock.sent:
            self.assertLessEqual(len(data), fb.MAX_DATAGRAM)
            self.assertEqual(where, (TABLET, 9000))
            for tags in re.findall(rb",[a-zA-Z]+\0", data):
                self.assertIn(tags, (b",f\0", b",s\0"))
        want = {"alive": 1.0, "playing": 1.0, "pause": 0.0, "clip/name": "05_x.mp4", "clip/number": 2.0, "clip/count": 3.0, "time": 12.0,
                "length": 60.0, "fade": "idle", "fade/on": 0.0, "blackout": 1.0, "transition": "cut", "transition/duration": 1.0,
                "opacity": 40.0, "size": 150.0, "position/x": -20.0, "position/y": 7.5, "speed": 1.5, "volume": 80.0, "loop": 1.0, "mute": 1.0,
                "shader": "", "effect": "", "effect/on": 0.0, "vibes": 0.0, "mapping/mode": 0.0, "guests/open": 1.0,
                "pad/bank": 2.0, "pad/number": 3.0, "pad/2/3": 1.0, "pad/1/1": 0.0, "pad/3/12": 0.0, "rotate": 0.0, "flip/h": 0.0, "overlay": 0.0,
                "shader/speed": 1.0, "shader/hue": 0.0, "shader/brightness": 1.0, "shader/control/8": 0.0, "shader/preset/1": 0.0}
        for tail, value in want.items():
            self.assertEqual(got.get(fb.PREFIX + tail), [value], tail)
        self.assertAlmostEqual(got[fb.PREFIX + "progress"][0], 0.21, places=5)
        self.assertEqual(len(got), 1 + len(fb.build(self.api, {"pads": [[""] * 12] * 3}, {})), "one message per address, and the heartbeat")
        self.assertNotIn(b"/secret", b"".join(d for d, _w, _t in self.sock.sent), "a clip's own name, never its folder")

    def test_the_table_in_osc_md_is_exactly_what_is_sent(self):
        text = open(os.path.join(REPO, "pvj", "OSC.md")).read()
        part = text[text.index("## State sent back"):]
        part = part[:part.index("\n## ", 5)]
        said = set()
        for a in re.findall(r"`(/pvj/state/[a-z/<>0-9_]+)`", part):
            if "<bank>" in a:
                said |= {a.replace("<bank>", str(b)).replace("<pad>", str(p)) for b in (1, 2, 3) for p in range(1, 13)}
            elif "<n>" in a:
                said |= {a.replace("<n>", str(n)) for n in range(1, 9)}
            else:
                said.add(a)
        sent = {a for a, _v in fb.build(self.api, {"pads": [[""] * 12] * 3}, {})} | {fb.ALIVE}
        self.assertEqual(said ^ sent, set())

    def test_it_is_the_controller_lights_own_state(self):
        from pvj import boxstate, midi
        self.play()
        self.settings.data["pads"]["banks"][0]["pads"][0]["file"] = "05_x.mp4"
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [])
        snap = hub._snapshot(1.0, fresh=True)
        self.assertEqual(snap, boxstate.snapshot(self.api, 1.0, True, type("H", (), {"_player_seen": None})()))
        light = midi.light_state({"action": "pad", "bank": 0, "index": 0}, snap)
        self.assertEqual((light, dict(fb.build(self.api, snap, {}))[fb.PREFIX + "pad/1/1"]), ("active", 1.0))

    def test_a_change_leaves_once_and_only_what_changed(self):
        f = self.feedback()
        self.run_for(f, 1)                  # everything, over the first looks (the cap on bytes spreads it)
        first = len(decode(self.sock.sent))
        del self.sock.sent[:]
        self.now[0] += 0.2
        f.tick()
        self.assertEqual(self.sock.sent, [], "nothing changed, nothing sent")
        self.rules()
        self.server.feedback = f
        self.assertEqual(self.send(msg("/pvj/opacity", 25.0)), 1)
        self.assertTrue(f._wake.is_set(), "the receiver only woke the sender")
        self.now[0] += 0.2
        f.tick()
        self.assertEqual([(a, v) for a, v, _w in decode(self.sock.sent)], [(fb.PREFIX + "opacity", [25.0])])
        del self.sock.sent[:]
        self.run_for(f, fb.HEARTBEAT + 0.1)
        self.assertEqual([a for a, _v, _w in decode(self.sock.sent)], [fb.ALIVE])
        del self.sock.sent[:]
        self.run_for(f, fb.FULL_EVERY)
        self.assertGreaterEqual(len(decode(self.sock.sent)), first, "everything again at the slow interval")

    def test_a_name_or_a_number_that_cannot_be_sent_stops_nothing_else(self):
        """The review of #123: a file name with a broken byte (a lone surrogate, as `os` hands it over) or a number
        beyond a float32 raised in the encoder and silenced the sender until the clip changed."""
        bad = "05_\udcff\udce9x.mp4"
        raw = fb.encode(fb.PREFIX + "clip/name", bad)
        self.assertEqual(osc.parse_message(raw)[1], ["05_??x.mp4"])
        for v in (1e39, -1e39, 3.5e38, float("inf"), float("nan")):
            self.assertLessEqual(abs(osc.parse_message(fb.encode("/x", fb._num(v)))[1][0]), 3.1e38, v)
            self.assertLessEqual(abs(osc.parse_message(fb.encode("/x", v))[1][0]), 3.1e38, v)
        self.player.running = True
        self.player.status = lambda: {"running": True, "path": "/m/" + bad, "position": 1e300, "duration": 2e300, "speed": 1e39, "volume": -1e39}
        f = self.feedback()
        self.run_for(f, 1)
        got = {a: args for a, args, _w in decode(self.sock.sent)}
        self.assertEqual(got[fb.PREFIX + "clip/name"], ["05_??x.mp4"])
        self.assertEqual(got[fb.PREFIX + "playing"], [1.0])
        self.assertEqual(len(got), 1 + len(fb.build(self.api, {"pads": [[""] * 12] * 3}, {})))
        # and whatever else a later address might hold: one message that cannot be built is left out, the rest leave
        del self.sock.sent[:]
        f2 = self.feedback(state=lambda now: [(fb.PREFIX + "a", 1.0), (fb.PREFIX + "b", object()), (fb.PREFIX + "c", "x"), (fb.PREFIX + "d", 1e300)])
        self.run_for(f2, 1)
        self.assertEqual(sorted(a for a, _v, _w in decode(self.sock.sent)), sorted([fb.ALIVE, fb.PREFIX + "a", fb.PREFIX + "c", fb.PREFIX + "d"]))
        self.assertEqual(len([x for x in self.logs if "could not be sent" in x]), 1, "said once")

    def test_fade_words_and_a_stream_s_address_is_never_a_name(self):
        self.assertEqual([fb.fade_word(*x) for x in ((None, None), ("out", 60), ("out", 0), ("in", 30), (None, 0))], ["idle", "out", "black", "in", "idle"])
        for path in ("rtsp://user:Stream-Pass-7@cam/1", "http://x/y.mp4", "relative.mp4", None, 5, "/tmp/a://b"):
            self.assertEqual(fb.clip_name(self.api, path), "", path)


class ShaderStateTest(Live):
    """With the Shaders and Vibes module on and a shader on the screen (the engine's own fake player and clock): what
    goes in over /pvj/shader/... comes back under /pvj/state/shader/... in the same units."""

    def setUp(self):
        super().setUp()
        self.engine.upload("all.fs", ALL)
        self.engine.play("all.fs")
        self.server = osc.OscServer(self.api, log=lambda *_: None)

    def state(self):
        from pvj import boxstate
        holder = type("H", (), {"_player_seen": None, "_player_raw": {}})()
        snap = boxstate.snapshot(self.api, time.monotonic(), True, holder)
        return dict(fb.build(self.api, snap, holder._player_raw)), snap

    def send(self, address, value):
        self.assertEqual(self.server.handle_packet(msg(address, value), "192.168.1.20"), 1, address)
        self.pump()

    def test_the_shader_s_levels_and_controls_come_back_as_they_went_in(self):
        for tail, value in (("shader/speed", 2.5), ("shader/hue", -90.0), ("shader/brightness", 1.5), ("shader/control/1", 0.5), ("shader/control/2", 1.0)):
            self.send("/pvj/" + tail, value)
        got, snap = self.state()
        self.assertTrue(snap["shader_on"])
        self.assertEqual([got[fb.PREFIX + t] for t in ("shader", "shader/speed", "shader/hue", "shader/brightness", "shader/control/2")],
                         ["all", 2.5, -90.0, 1.5, 1.0])
        self.assertAlmostEqual(got[fb.PREFIX + "shader/control/1"], 0.5, delta=1 / 127.0)
        self.assertEqual(got[fb.PREFIX + "shader/control/8"], 0.0, "this shader has six")
        self.send("/pvj/vibes/dwell", 45)
        got, snap = self.state()
        self.assertEqual(got[fb.PREFIX + "vibes/dwell"], 45.0)
        self.api.vibes.running = True                        # the rotation's own flag, as the lights read it
        self.assertEqual(self.state()[0][fb.PREFIX + "vibes"], 1.0)
        self.api.vibes.running = False
        self.api.effects._seen = lambda: {"id": "glow.fs", "controls": {"amount": 0.35}}
        got, snap = self.state()
        self.assertEqual([got[fb.PREFIX + t] for t in ("effect", "effect/on", "effect/amount")], ["glow", 1.0, 35.0])
        self.assertEqual((got[fb.PREFIX + "vibes"], bool(got[fb.PREFIX + "vibes/set"])), (0.0, True))
        self.assertEqual(osc.parse_packet(fb.pack(sorted(got.items()))[0][0])[0][1].__class__, list)
        for a, v in got.items():
            self.assertIn(type(v), (float, str), a)

    def names(self, got, what="shader"):
        return ([got[fb.PREFIX + "%s/control/%d/name" % (what, n)] for n in range(1, 9)],
                [got[fb.PREFIX + "%s/control/%d/kind" % (what, n)] for n in range(1, 9)])

    def show(self, name, source):
        self.engine.upload(name, source)
        self.engine.play(name)
        self.pump()
        return self.state()[0]

    def test_the_names_and_kinds_of_the_shader_s_controls_as_the_panel_labels_them(self):
        """D83. The LABEL where the file has one, else the NAME (shaders.js: `i.label || i.name`), in the count of the
        knobs (nth_control): a colour and a point have no knob, so they have no place among the eight."""
        got, _snap = self.state()
        self.assertEqual(self.names(got), (["Level", "lit", "mode", "count", "bang", "steps", "", ""],
                                           ["float", "bool", "choice", "int", "event", "float", "", ""]))
        self.assertEqual(got[fb.PREFIX + "shader/control/5"], 0.0, "an event has a name and no value")

    def test_a_shader_with_no_control_three_and_more_than_eight(self):
        body = "void main() { gl_FragColor = vec4(%s, 0.0, 0.0, 1.0); }"
        got = self.show("none.fs", '/*{"INPUTS": []}*/\n' + body % "0.5")
        self.assertEqual(self.names(got), ([""] * 8, [""] * 8))
        self.assertEqual([got[fb.PREFIX + "shader/control/%d" % n] for n in range(1, 9)], [0.0] * 8)
        three = '/*{"INPUTS": [{"NAME": "a", "TYPE": "float", "LABEL": "Glow amount"}, {"NAME": "b", "TYPE": "bool", "DEFAULT": true},' \
                ' {"NAME": "c", "TYPE": "float", "MIN": 1.0, "MAX": 1.0, "LABEL": "Fixed"}, {"NAME": "d", "TYPE": "float"}]}*/\n'
        got = self.show("three.fs", three + body % "a + d + c + (b ? 1.0 : 0.0)")
        self.assertEqual(self.names(got), (["Glow amount", "b", "d"] + [""] * 5, ["float", "bool", "float"] + [""] * 5),
                         "a number that cannot move (MIN is MAX) is no knob, as on a controller")
        self.assertEqual(got[fb.PREFIX + "shader/control/2"], 1.0)
        ten = '/*{"INPUTS": [%s]}*/\n' % ", ".join('{"NAME": "k%d", "TYPE": "float", "LABEL": "Knob %d"}' % (n, n) for n in range(1, 11))
        got = self.show("ten.fs", ten + body % " + ".join("k%d" % n for n in range(1, 11)))
        self.assertEqual(self.names(got), (["Knob %d" % n for n in range(1, 9)], ["float"] * 8), "the first eight, as the knobs")
        self.assertNotIn("Knob 9", [v for v in got.values() if isinstance(v, str)])
        self.assertNotIn(fb.PREFIX + "shader/control/9/name", got)

    def test_a_name_is_cleaned_as_a_clip_s_name_is(self):
        long = '/*{"INPUTS": [{"NAME": "a", "TYPE": "float", "LABEL": "%s"}, {"NAME": "b", "TYPE": "float", "LABEL": "tab\\there"}]}*/\n' % ("W" * 60)
        got = self.show("long.fs", long + "void main() { gl_FragColor = vec4(a, b, 0.0, 1.0); }")
        names = self.names(got)[0]
        self.assertEqual(names[0], "W" * fb.MAX_NAME)
        self.assertEqual(re.sub(r"[\x00-\x1f\x7f]", "", names[1]), names[1])
        self.assertEqual(osc.parse_message(fb.encode("/x", "a\udcffb"))[1], ["a?b"])
        i = {"name": "n", "type": "float", "label": "L\x00\x1f" + "\u00e9" * 80, "min": 0.0, "max": 1.0, "default": 0.0}

        class Engine:
            def _path(self, sid):
                return ("x",)

            def _parsed(self, path):
                return ({"inputs": [i]},)

            def current_values(self, parsed, on):
                return {}

            def nth_control(self, parsed, n):
                return i if n == 1 else None
        fb._PARSED["shader"].clear()
        _v, names, kinds = fb._controls("shader", Engine(), {"id": "x"})
        self.assertEqual((names[0], kinds[0]), ("L" + "\u00e9" * (fb.MAX_NAME - 1), "float"))
        fb._PARSED["shader"].clear()

    def test_the_effect_s_controls_their_names_and_their_kinds(self):
        """The effect that is on: its eight values as /pvj/effect/control/<n> takes them, and what the panel calls
        each (effects.js: the filter's own "amount" is told apart from the effect's Amount)."""
        from pvj import shaders as S
        src = '/*{"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}, {"NAME": "amount", "TYPE": "float", "MIN": 0.0, "MAX": 4.0, "DEFAULT": 1.0},' \
              ' {"NAME": "tint", "TYPE": "color", "DEFAULT": [1.0, 1.0, 1.0, 1.0]}, {"NAME": "soft", "TYPE": "bool", "LABEL": "Soft edge", "DEFAULT": true},' \
              ' {"NAME": "shape", "TYPE": "long", "VALUES": [0, 1, 2], "LABELS": ["a", "b", "c"], "DEFAULT": 2}]}*/\n' \
              'void main() { gl_FragColor = IMG_THIS_PIXEL(inputImage) * amount * tint * (soft ? 1.0 : 0.5) * float(shape); }'
        parsed = S.parse(src, S.FILTER)
        fx, real = self.api.effects, {}
        for name in ("_seen", "_path", "_parsed", "current_values"):
            real[name] = getattr(fx, name)
            self.addCleanup(setattr, fx, name, real[name])
        on = {"id": "glow.fs", "digest": "d1", "controls": {"amount": 0.35}, "values": {"amount": 3.0}}
        fx._seen = lambda: on
        fx._path = lambda sid: ("/x/" + sid,)
        fx._parsed = lambda path: (parsed, "d1")
        fx.current_values = lambda p, rec: dict({i["name"]: i.get("default") for i in p["inputs"]}, **rec["values"])
        fb._PARSED["effect"].clear()
        self.addCleanup(fb._PARSED["effect"].clear)
        got, _snap = self.state()
        self.assertEqual(self.names(got, "effect"), (["Amount (the filter's own)", "Soft edge", "shape"] + [""] * 5, ["float", "bool", "choice"] + [""] * 5))
        self.assertEqual([got[fb.PREFIX + "effect/control/%d" % n] for n in range(1, 9)], [0.75, 1.0, 1.0] + [0.0] * 5)
        self.assertEqual(got[fb.PREFIX + "effect/amount"], 35.0)
        self.assertEqual(self.names(got)[0][0], "Level", "the shader under it keeps its own names: one cache does not put the other out")
        self.assertEqual(sorted(len(c) for c in fb._PARSED.values()), [1, 1])
        fx._seen = lambda: None
        got, _snap = self.state()
        self.assertEqual(self.names(got, "effect"), ([""] * 8, [""] * 8))
        self.assertEqual(got[fb.PREFIX + "effect/control/1"], 0.0)

    def test_names_leave_when_the_shader_changes_and_not_at_every_look(self):
        f = fb.Feedback(self.api, [T1], nets=lambda: NETS, clock=lambda: self.now[0], log=lambda *_: None, sock=Sock(lambda: self.now[0]))
        sock = f._sock

        def looks(n):
            del sock.sent[:]
            for _ in range(n):
                f.tick()
                self.now[0] += 0.2
            return [a for a, _v, _w in decode(sock.sent)]
        first = looks(10)
        self.assertEqual(len([a for a in first if "/control/" in a and a.endswith("/name")]), 16, "everything once: eight names for the shader, eight for the effect")
        self.send("/pvj/shader/control/1", 0.9)
        after = looks(10)
        self.assertIn(fb.PREFIX + "shader/control/1", after)
        self.assertEqual([a for a in after if "/control/" in a and a.endswith(("/name", "/kind"))], [], "a knob that moves sends its value, never the names again")
        self.engine.upload("two.fs", '/*{"INPUTS": [{"NAME": "z", "TYPE": "bool"}]}*/\nvoid main() { gl_FragColor = vec4(z ? 1.0 : 0.0); }')
        self.engine.play("two.fs")
        self.pump()
        changed = looks(10)
        self.assertIn(fb.PREFIX + "shader/control/1/name", changed)
        self.assertNotIn(fb.PREFIX + "shader/control/8/name", changed, "an empty place that stays empty is not sent again")
        self.assertNotIn(fb.PREFIX + "effect/control/1/name", changed)

    def test_a_full_state_with_the_longest_names_fits_every_cap(self):
        """The worst sixteen names a file can give (MAX_NAME characters of four bytes each) with every kind: each
        datagram within MAX_DATAGRAM, each second within the target's cap, and all of it out within a second."""
        from pvj import boxstate
        holder = type("H", (), {"_player_seen": None, "_player_raw": {}})()
        snap = boxstate.snapshot(self.api, time.monotonic(), True, holder)
        usual = fb.build(self.api, snap, holder._player_raw)
        worst = [(a, ("\U0001f39b" * fb.MAX_NAME) if "/control/" in a and a.endswith("/name") else ("choice" if a.endswith("/kind") else v)) for a, v in usual]
        sizes = {}
        for label, state in (("usual", usual), ("worst", worst)):
            now = [1000.0]
            sock = Sock(lambda: now[0])
            f = fb.Feedback(self.api, [T1], nets=lambda: NETS, clock=lambda: now[0], log=lambda *_: None, sock=sock, state=lambda t: state)
            ticks = 0
            while len(decode(sock.sent)) < len(state) + 1 and ticks < 50:
                f.tick()
                now[0] += fb.TICK
                ticks += 1
            sent = sock.sent
            self.assertEqual(len(decode(sent)), len(state) + 1, label)
            self.assertLessEqual(max(len(d) for d, _w, _t in sent), fb.MAX_DATAGRAM, label)
            self.assertLessEqual(len(sent), fb.TARGET_PACKETS + fb.TARGET_PACKET_BURST, label)
            self.assertLessEqual(sum(len(d) for d, _w, _t in sent), fb.TARGET_BYTES + fb.TARGET_BYTE_BURST, label)
            self.assertLessEqual(sent[-1][2] - sent[0][2], 1.0, label)
            sizes[label] = (len(state) + 1, len(sent), sum(len(d) for d, _w, _t in sent), round(sent[-1][2] - sent[0][2], 1))
        self.assertEqual(sizes["usual"][0], 132, "92 before D83: 8 effect values, and a name and a kind for 16 controls")
        self.assertLessEqual(sizes["usual"][2], 6000, sizes)
        self.assertLessEqual(sizes["worst"][2], 9000, sizes)
        if os.environ.get("PVJ_SAY_SIZES"):
            print(sizes)


class CapTest(Base):
    def windows(self, sent, where=None):
        """The most datagrams and bytes in any one second."""
        rows = [(t, len(d)) for d, w, t in sent if where is None or w == where]
        most = [0, 0]
        q = collections.deque()
        size = 0
        for t, n in rows:
            q.append((t, n))
            size += n
            while t - q[0][0] >= 1.0:
                size -= q.popleft()[1]
            most = [max(most[0], len(q)), max(most[1], size)]
        return most

    def flood(self, targets):
        n = [0]

        def state(now):                 # the worst a box could have to say: everything changes at every look
            n[0] += 1
            return [(fb.PREFIX + "x/%d" % i, "%d-%s" % (n[0], "y" * 80)) for i in range(120)]
        f = self.feedback(targets, state=state)
        self.rules()
        self.server.feedback = f
        for _ in range(3000):           # three seconds, a look every millisecond, each after a request from every target
            for t in targets:
                self.server.limiter = osc.RateLimiter(self.clock)
                self.send(msg("/pvj/state"), t["address"])
            f.tick()
            self.now[0] += 0.001
        return f

    def test_one_target_under_a_flood(self):
        f = self.flood([T1])
        packets, size = self.windows(self.sock.sent)
        self.assertGreaterEqual(packets, 10)
        self.assertLessEqual(packets, fb.TARGET_PACKETS + fb.TARGET_PACKET_BURST)
        self.assertLessEqual(size, fb.TARGET_BYTES + fb.TARGET_BYTE_BURST)
        self.assertGreater(f.stats["held"], 0)

    def test_four_targets_under_a_flood(self):
        targets = [T1, T2, dict(T1, port=9002, name="c"), dict(T2, port=9003, name="d")]
        self.flood(targets)
        packets, size = self.windows(self.sock.sent)
        self.assertLessEqual(packets, fb.ALL_PACKETS + fb.ALL_PACKET_BURST)
        self.assertLessEqual(size, fb.ALL_BYTES + fb.ALL_BYTE_BURST)
        for t in targets:
            packets, size = self.windows(self.sock.sent, (t["address"], t["port"]))
            self.assertLessEqual(packets, fb.TARGET_PACKETS + fb.TARGET_PACKET_BURST)
            self.assertLessEqual(size, fb.TARGET_BYTES + fb.TARGET_BYTE_BURST)
        self.assertEqual((fb.TARGET_PACKETS + fb.TARGET_PACKET_BURST, fb.TARGET_BYTES + fb.TARGET_BYTE_BURST,
                          fb.ALL_PACKETS + fb.ALL_PACKET_BURST, fb.ALL_BYTES + fb.ALL_BYTE_BURST, fb.MAX_DATAGRAM, fb.MAX_TARGETS),
                         (20, 12000, 60, 36000, 1200, 4), "the numbers D82 and OSC.md state")

    def test_every_target_gets_a_full_state_while_many_values_keep_changing(self):
        """The review of #124: with 40 values changing at every look, four targets and the longest names, the changed
        values took the budget first and the shared cap followed the list's order: the fourth target had no full
        state after 20 seconds. Now what a target was never sent goes before what it has an older value of, and
        the target that goes first changes with every look. No cap is raised."""
        targets = [T1, T2, dict(T1, port=9002, name="c"), dict(T2, port=9003, name="d")]
        n = [0]
        still = [(fb.PREFIX + "still/%d" % i, float(i)) for i in range(60)]
        names = [(fb.PREFIX + "name/%d" % i, "\U0001f39b" * fb.MAX_NAME) for i in range(32)]

        def state(now):
            n[0] += 1
            return [(fb.PREFIX + "hot/%d" % i, float(n[0] * 100 + i)) for i in range(40)] + still + names
        f = self.feedback(targets, state=state)
        every = {a for a, _v in state(0)}
        got = {(t["address"], t["port"]): set() for t in targets}
        done = {}
        start = self.now[0]
        while self.now[0] - start < 20 and len(done) < len(targets):
            before = len(self.sock.sent)
            f.tick()
            for a, _args, where in decode(self.sock.sent[before:]):
                got[where].add(a)
            for where, seen in got.items():
                if where not in done and every <= seen:
                    done[where] = round(self.now[0] - start, 1)
            self.now[0] += fb.TICK
        self.assertEqual(sorted(done), sorted(got), "every target has every address: %r" % ({w: len(s) for w, s in got.items()},))
        self.assertLessEqual(max(done.values()), 5.0, done)
        packets, size = self.windows(self.sock.sent)
        self.assertLessEqual(packets, fb.ALL_PACKETS + fb.ALL_PACKET_BURST)
        self.assertLessEqual(size, fb.ALL_BYTES + fb.ALL_BYTE_BURST)
        for t in targets:
            packets, size = self.windows(self.sock.sent, (t["address"], t["port"]))
            self.assertLessEqual(packets, fb.TARGET_PACKETS + fb.TARGET_PACKET_BURST)
            self.assertLessEqual(size, fb.TARGET_BYTES + fb.TARGET_BYTE_BURST)
        # and the changing values still arrive at every target afterwards, the newest only
        del self.sock.sent[:]
        self.run_for(f, 3, fb.TICK)
        hot = {where: 0 for where in got}
        for a, _args, where in decode(self.sock.sent):
            hot[where] += "/hot/" in a
        self.assertGreater(min(hot.values()), 40, hot)

    def test_a_flood_of_requests_is_one_full_state_every_two_seconds(self):
        f = self.feedback()
        self.rules()
        self.server.feedback = f
        f.tick()
        full = 1 + len(fb.build(self.api, {"pads": [[""] * 12] * 3}, {}))     # every address, and the heartbeat
        del self.sock.sent[:]
        for _ in range(4000):           # four seconds of requests, a thousand a second
            self.server.limiter = osc.RateLimiter(self.clock)
            self.send(msg("/pvj/state"), TABLET)
            f.tick()
            self.now[0] += 0.001
        got = [a for a, _v, _w in decode(self.sock.sent) if a != fb.ALIVE]
        self.assertLessEqual(len(got), 3 * (full - 1))
        self.assertGreaterEqual(len(got), full - 1)

    def test_one_received_packet_is_answered_with_at_most_one_full_state(self):
        f = self.feedback()
        self.rules()
        self.server.feedback = f
        self.run_for(f, 1)
        del self.sock.sent[:]
        self.now[0] += fb.REQUEST_EVERY
        request = msg("/pvj/state")
        self.send(request, TABLET)
        self.run_for(f, 1.0, 0.01)
        out = sum(len(d) for d, _w, _t in self.sock.sent)
        self.assertGreater(out, 1000)
        self.assertLessEqual(out, fb.TARGET_BYTES + fb.TARGET_BYTE_BURST)
        self.assertLess(out, 6000, "a full state today; the cap is the bound that holds whatever is added")
        self.assertEqual({w for _d, w, _t in self.sock.sent}, {(TABLET, 9000)})


class SpoofTest(Base):
    def test_a_sender_that_is_no_target_gets_nothing_and_causes_nothing(self):
        f = self.feedback([T1])
        self.rules()
        self.server.feedback = f
        self.run_for(f, 1)
        del self.sock.sent[:]
        for ip in (OTHER, "192.168.1.77", "10.0.0.1", "8.8.8.8", "127.0.0.1", BOX, "192.168.1.255"):
            self.send(msg("/pvj/state"), ip)
            self.send(bundle(msg("/pvj/state"), msg("/pvj/state", "x")), ip)
        self.assertEqual(f._wanted, set())
        self.run_for(f, 1.5)
        self.assertEqual([a for a, _v, _w in decode(self.sock.sent) if a != fb.ALIVE], [])
        self.assertEqual({w for _d, w, _t in self.sock.sent} - {(TABLET, 9000)}, set())
        self.assertEqual(self.why(OTHER), osc.NO_STATE)

    def test_a_refused_or_malformed_packet_from_a_target_s_address_asks_for_nothing(self):
        f = self.feedback([T1])
        self.rules(key_on=True, key=KEY)
        self.server.feedback = f
        for data in (msg("/pvj/state"), msg("/k/%s/pvj/state" % ("0" * 20)), msg("/pvj/state")[:-1], b"/pvj/state", b"\0" * 16,
                     bundle(msg("/k/%s/pvj/state" % KEY), msg("/pvj/state"))):
            self.send(data, TABLET)
        self.assertEqual(f._wanted, set())
        self.send(msg("/k/%s/pvj/state" % KEY), TABLET)
        self.assertEqual(f._wanted, {TABLET})
        self.rules(only_on=True, only=[OTHER])
        self.server.feedback = f
        f._wanted.clear()
        self.send(msg("/pvj/state"), TABLET)
        self.assertEqual(f._wanted, set())
        self.server.limiter = osc.RateLimiter(self.clock, rate=0, burst=0)
        self.rules()
        self.server.limiter = osc.RateLimiter(self.clock, rate=0, burst=0)
        self.server.feedback = f
        self.send(msg("/pvj/state"), TABLET)
        self.assertEqual(f._wanted, set(), "over the receiver's own rate limit")

    def test_every_packet_goes_to_a_listed_target_whatever_comes_in(self):
        f = self.feedback([T1, T2])
        self.rules()
        self.server.feedback = f
        for n in range(400):
            ip = "192.168.%d.%d" % (n % 3, 1 + n % 250)
            self.server.limiter = osc.RateLimiter(self.clock)
            self.send(msg("/pvj/state"), ip)
            self.send(msg("/pvj/opacity", float(n % 100)), ip)
            f.tick()
            self.now[0] += 0.05
        self.assertGreater(len(self.sock.sent), 20)
        self.assertEqual({w for _d, w, _t in self.sock.sent}, {(TABLET, 9000), (OTHER, 9001)})

    def test_what_the_box_sends_is_no_command_and_no_request_when_it_comes_back(self):
        self.player.running = True
        f = self.feedback([T1])
        f.tick()
        self.rules()
        self.server.feedback = f
        calls = list(self.player.calls)
        handled = []
        real = self.api.handle
        self.api.handle = lambda *a: handled.append(a) or real(*a)
        for data, _w, _t in self.sock.sent:
            self.assertEqual(self.send(data, TABLET), 0)
        self.assertEqual((handled, self.player.calls, f._wanted), ([], calls, set()))
        for a, args, _w in decode(self.sock.sent):
            self.assertNotEqual(a.rstrip("/"), fb.REQUEST)
            self.assertIsNone(osc.translate(a, args, self.api.mix), a)

    def test_the_locks_that_are_on_hold_for_a_target_too(self):
        f = self.feedback([T1])
        self.rules(only_on=True, only=[OTHER])
        self.run_for(f, 1)
        self.assertEqual((self.sock.sent, f.status()[0]["why"]), ([], fb.WHY_LOCKS))
        self.rules(only_on=False, paired_on=True)
        self.run_for(f, 1)
        self.assertEqual(self.sock.sent, [], "no paired device at the tablet's address")
        _token, dev = self.device("full", TABLET, "tablet")
        self.run_for(f, 1)
        self.assertGreater(len(self.sock.sent), 0)
        self.auth.revoke(dev["id"])
        del self.sock.sent[:]
        self.run_for(f, 3)
        self.assertEqual(self.sock.sent, [], "removing the device stops its feedback at once")

    def test_a_box_that_moved_to_another_network_sends_nothing(self):
        f = self.feedback([T1])
        self.run_for(f, 1)
        self.assertGreater(len(self.sock.sent), 0)
        del self.sock.sent[:]
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.0.0.5", "prefixlen": 24}]}]
        self.run_for(f, fb.NETS_EVERY + 1)
        late = [t for _d, _w, t in self.sock.sent if t > self.sock.sent[0][2] + fb.NETS_EVERY] if self.sock.sent else []
        self.assertEqual(late, [])
        del self.sock.sent[:]
        self.api._ip_json = lambda: []
        self.run_for(f, fb.NETS_EVERY + 1)
        self.assertEqual(self.sock.sent, [], "networks that cannot be read are no networks")


class SecretTest(Base):
    def test_no_secret_is_in_any_packet_of_a_full_scenario(self):
        self.device("live", TABLET, "Tablet-Name-Sentinel")
        self.rules(key_on=True, key=KEY, paired_on=True, paired_roles="live")
        self.post({"add": dict(T1, name="Target-Name-Sentinel")})
        self.post({"on": True})
        code = self.api.handle("POST", "/api/access/code", {"role": "live"}, self.owner, LAN)[1]
        pin = self.auth.show_pin(self.owner["id"]) if hasattr(self.auth, "show_pin") else ""
        self.settings.data["streams"] = [{"name": "Cam", "url": "rtsp://user:Stream-Pass-7@192.168.1.90/1"}]
        f = self.feedback([dict(T1, name="Target-Name-Sentinel")])
        self.player.running = True
        states = [{"running": True, "path": "/media/Private-Folder-Sentinel/05_x.mp4", "position": 3, "duration": 9},
                  {"running": True, "path": "rtsp://user:Stream-Pass-7@192.168.1.90/1"}, {"running": False}]
        for n in range(60):
            self.player.status = lambda n=n: states[n % 3]
            f._player_seen = None
            self.send(msg("/k/%s/pvj/state" % KEY, "Body-Sentinel-91"), TABLET)
            self.send(msg("/k/%s/pvj/opacity" % KEY, float(n)), TABLET)
            self.send(msg("/k/%s/pvj/play/file" % KEY, "Asked-File-Sentinel.mp4"), TABLET)
            self.api.handle("POST", "/api/blackout", {"on": n % 2 == 0}, self.owner, LAN)
            f.tick()
            self.now[0] += 0.7
        out = b"".join(d for d, _w, _t in self.sock.sent)
        self.assertGreater(len(out), 5000)
        secrets_ = [KEY, "/k/", "Sentinel", "Stream-Pass", "rtsp", TABLET, BOX, "192.168.", self.token, self.owner["id"], "Owner-Laptop",
                    str(self.settings.data["osc"]["port"]), "9000", "Private-Folder", "/media"]
        for found in (code if isinstance(code, dict) else {}).values():
            if isinstance(found, str) and len(found) >= 4:
                secrets_.append(found)
        if isinstance(pin, str) and pin:
            secrets_.append(pin)
        text = json.dumps(self.settings.data)
        for word in re.findall(r'"(?:token|hash|pin|salt|key|secret|code)[a-z_]*": "([^"]{4,})"', text):
            secrets_.append(word)
        for s in secrets_:
            self.assertNotIn(s.encode(), out, s)
        self.assertIn(b"05_x.mp4", out)


class ThreadTest(Base):
    def test_a_dead_target_and_a_socket_that_fails_stall_nothing(self):
        for fail in (BlockingIOError(), OSError(65, "No route to host"), OSError(1, "Operation not permitted"), ConnectionRefusedError()):
            self.sock = Sock(self.clock, fail)
            f = self.feedback([T1])
            began = time.monotonic()
            self.run_for(f, 5)
            self.assertLess(time.monotonic() - began, 2.0)
            self.assertGreater(f.stats["failed"], 0)
            self.assertIsNone(f.status()[0]["last_sent"])

    def test_a_real_socket_to_an_address_where_nobody_is(self):
        link = [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "169.254.201.1", "prefixlen": 16}]}]
        self.api._ip_json = lambda: link
        f = fb.Feedback(self.api, [dict(T1, address="169.254.201.77")], nets=lambda: fb.local_nets(link))
        f.start()
        self.addCleanup(f.stop)
        self.assertFalse(f._sock.getblocking())
        self.assertEqual(f._sock.getsockopt(socket.IPPROTO_IP, socket.IP_TTL), 1)
        self.assertEqual(f._sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST), 0)
        began = time.monotonic()
        while f.stats["datagrams"] < 3 and time.monotonic() - began < 5:
            time.sleep(0.05)
        self.assertGreaterEqual(f.stats["datagrams"], 3, "it went on trying, and nothing waited")
        f.stop()
        self.assertFalse(any(t.name == "osc-feedback" and t.is_alive() for t in threading.enumerate()))

    def test_the_receiver_never_waits_for_the_sender(self):
        gate, inside = threading.Event(), threading.Event()

        def slow(now):                      # a look at the box that hangs (a player that does not answer)
            inside.set()
            gate.wait(10)
            return []
        f = self.feedback([T1], state=slow)
        self.rules()
        self.server.feedback = f
        t = threading.Thread(target=f.tick, daemon=True)
        t.start()
        self.assertTrue(inside.wait(5))
        began = time.monotonic()
        for n in range(200):
            self.server.limiter = osc.RateLimiter(self.clock)
            self.send(msg("/pvj/state"), TABLET)
            self.assertEqual(self.send(msg("/pvj/opacity", float(n % 100))), 1)
            f.status()
        self.assertLess(time.monotonic() - began, 2.0)
        gate.set()
        t.join(5)

    def test_it_reaches_a_listening_tablet_over_a_real_socket(self):
        """Loopback is never a target, so the check of what is local is stood aside here and only here: the thread,
        the socket and the wire are the real ones."""
        tablet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tablet.bind(("127.0.0.1", 0))
        tablet.settimeout(5)
        self.addCleanup(tablet.close)
        real = fb.not_local
        fb.not_local = lambda addr, nets: None
        self.addCleanup(setattr, fb, "not_local", real)
        f = fb.Feedback(self.api, [{"name": "t", "address": "127.0.0.1", "port": tablet.getsockname()[1]}])
        f.start()
        self.addCleanup(f.stop)
        got = dict(osc.parse_packet(tablet.recvfrom(4096)[0]))
        self.assertEqual(got[fb.ALIVE], [1.0])
        self.assertEqual(got[fb.PREFIX + "opacity"], [100.0])


class RollbackTest(Base):
    def test_an_older_release_keeps_the_keys_and_sends_nothing(self):
        """master before this change, run from this clone on a settings file written here: it loads the file, keeps
        the two keys through a save of its own OSC page, and has no code that could send. Skipped where the older
        commit is not in the clone (a shallow checkout)."""
        self.post({"add": T1})
        self.post({"on": True})
        try:
            tree = subprocess.run(["git", "archive", OLDER, "pvj"], cwd=REPO, capture_output=True, check=True, timeout=60).stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.skipTest("the older release %s is not in this clone" % OLDER)
        old = os.path.join(os.path.dirname(self.settings.path), "older")
        os.makedirs(old)
        subprocess.run(["tar", "-x", "-C", old], input=tree, check=True, timeout=60)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "oscfeedback.py")))
        script = textwrap.dedent("""
            import json, socket, sys
            sys.path.insert(0, sys.argv[1])
            sent = []
            real = socket.socket
            class Spy(real):
                def sendto(self, *a):
                    sent.append(a)
                    return real.sendto(self, *a)
            socket.socket = Spy
            from pvj.settings import Settings
            from pvj import osc
            s = Settings(sys.argv[2])
            s.load()
            class Api:
                mix = {}
                def handle(self, *a):
                    return 200, {}
            m = osc.OscManager(Api(), s, host="127.0.0.1", log=lambda *a: None)
            s.data["osc"].update(enabled=True, port=0)
            m.apply()
            for data in (b"/pvj/state\\0\\0", b"/pvj/stop\\0\\0\\0"):
                m.server.handle_packet(data, "192.168.1.20")
            cfg = dict(s.data["osc"], port=9877, enabled=False)       # what its own page's save does: a copy with its keys changed
            s.data["osc"] = cfg
            m.apply()
            s.save()
            print(json.dumps({"sent": len(sent), "feedback": hasattr(m, "feedback"), "osc": json.load(open(sys.argv[2]))["osc"]}))
            """)
        r = subprocess.run([os.sys.executable, "-c", script, old, self.settings.path], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["sent"], out["feedback"]), (0, False))
        self.assertEqual((out["osc"]["feedback_on"], out["osc"]["feedback"]), (True, [T1]))
        self.settings.load()                # and back on this release the Owner's choice is as it was
        self.assertEqual(fb.stored(self.settings.data["osc"]), (True, [T1]))


if __name__ == "__main__":
    unittest.main()
