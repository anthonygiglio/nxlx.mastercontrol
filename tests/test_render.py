# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The render bridge (D94): render nodes, a cue's exact bytes on the wire, where the box may send, the caps, who may
do what, a cue on a pad, the way back when a node's picture goes, the Max profile and its patch, and an older release
reading what this one wrote."""
import ipaddress
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from pvj import boxcare, render
from pvj.settings import Settings
from tests.test_ndi import frame, wait
from tests.test_ndi_pads import HOST, NAME, PadBase, REPO, RID, ROW
from tests.test_roles import CONTROLLER_DEVICES, LAN, SCENE, TUNNEL

ETH = [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.0.5", "prefixlen": 24}]},
       {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.2", "prefixlen": 24}]}]
NETS = [ipaddress.IPv4Interface("192.168.0.5/24")]
NODE_HOST, NODE_PORT = "192.168.0.30", 7400


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    return s


class LoopBridge(render.Bridge):
    """The real bridge, with 127.0.0.1 allowed as a node: the one thing the rule refuses that a test needs."""
    def why_not(self, addr, nets=None):
        return None if str(addr) == "127.0.0.1" else super().why_not(addr, nets)


class Redirect:
    """A socket factory: records where the bridge sent, and hands the datagram to a loopback listener."""
    def __init__(self, port):
        self.port, self.sent = port, []

    def __call__(self):
        outer = self

        class Sock:
            def sendto(self, data, where):
                outer.sent.append((data, where))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(data, ("127.0.0.1", outer.port))

            def close(self):
                pass
        return Sock()


class FormTest(unittest.TestCase):
    def test_hosts(self):
        self.assertEqual(render.host_form("192.168.1.50"), "192.168.1.50")
        self.assertEqual(render.host_form("Render-Mac.local"), "render-mac.local")
        for bad in ("8.8.8.8", "127.0.0.1", "224.0.0.1", "192.168.001.050", "max.example.com", "render-mac", "a..local",
                    "-x.local", "x.local:7400", "x.local/a", "::1", "", None, 5, "x" * 250 + ".local"):
            with self.assertRaises(render.RenderError, msg=bad):
                render.host_form(bad)

    def test_nodes(self):
        n = render.node_form({"name": " Max ", "host": "192.168.0.30", "port": 7400}, new_id=True)
        self.assertEqual((n["name"], n["profile"], n["still"], n["ndi"]), ("Max", "generic", render.STILL_DEFAULT, ""))
        self.assertRegex(n["id"], r"^[0-9a-f]{8}$")
        for bad in ({"name": "Max", "host": "192.168.0.30", "port": 53}, {"name": "Max", "host": "192.168.0.30", "port": True},
                    {"name": "", "host": "192.168.0.30", "port": 7400}, {"name": "M", "host": "192.168.0.30", "port": 7400, "ndi": NAME},
                    {"name": "M", "host": "192.168.0.30", "port": 7400, "profile": "resolume"},
                    {"name": "M", "host": "192.168.0.30", "port": 7400, "still": 2},
                    {"name": "M", "host": "192.168.0.30", "port": 7400, "password": "x"}):
            with self.assertRaises(render.RenderError, msg=bad):
                render.node_form(bad, new_id=True)
        two = [dict(n), dict(n, id="abcdef01")]
        with self.assertRaises(render.RenderError):
            render.validate_nodes(two)          # the same host and port twice
        self.assertEqual(len(render.validate_nodes([dict(n)] * 1 + [dict(n, id="abcdef01", port=7401)])), 2)
        with self.assertRaises(render.RenderError):
            render.validate_nodes([dict(n, id="%08x" % i, port=7400 + i) for i in range(render.MAX_NODES + 1)])
        self.assertEqual(render.stored({"nodes": "x"}), [])          # damaged: no list at all, fails closed
        self.assertEqual(render.stored(None), [])

    def test_cues_and_arguments(self):
        c = render.cue_form({"node": "abcd0001", "address": "/a/b_c.d-e", "args": [{"type": "int", "value": -3},
                             {"type": "float", "value": 1}, {"type": "string", "value": "x"}, {"type": "bool", "value": False}]})
        self.assertEqual(c["args"][1], {"type": "float", "value": 1.0})
        self.assertEqual(render.cue_form({"node": "abcd0001", "show": True})["address"], "")
        for bad in ({"node": "abcd0001"}, {"node": "abcd0001", "address": "a"}, {"node": "abcd0001", "address": "/a b"},
                    {"node": "abcd0001", "address": "/a/*"}, {"node": "abcd0001", "address": "/a//b"}, {"node": "abcd0001", "address": "/a/"},
                    {"node": "abcd0001", "address": "/a/{b,c}"}, {"node": "abcd0001", "address": "/" + "a" * 128},
                    {"node": "x", "address": "/a"}, {"node": "abcd0001", "address": "/a", "show": 1},
                    {"node": "abcd0001", "show": True, "args": [{"type": "int", "value": 1}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "int", "value": 1}] * 9},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "int", "value": 2 ** 31}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "int", "value": True}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "float", "value": float("nan")}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "float", "value": 1e39}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "string", "value": "a\nb"}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "string", "value": "x" * 65}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "bool", "value": 1}]},
                    {"node": "abcd0001", "address": "/a", "args": [{"type": "blob", "value": "x"}]},
                    {"node": "abcd0001", "address": "/a", "host": "8.8.8.8"}):
            with self.assertRaises(render.RenderError, msg=bad):
                render.cue_form(bad)

    def test_the_bytes_of_a_message(self):
        """OSC 1.0, written out by hand: address and tags padded to four bytes, then each argument big-endian."""
        args = [{"type": "int", "value": 3}, {"type": "float", "value": 0.5}, {"type": "string", "value": "hue"},
                {"type": "bool", "value": True}, {"type": "bool", "value": False}]
        want = (b"/nxlx/scene\0" + b",ifsTF\0\0" + struct.pack(">i", 3) + struct.pack(">f", 0.5) + b"hue\0")
        self.assertEqual(render.encode("/nxlx/scene", args), want)
        self.assertEqual(render.encode("/nxlx/go", []), b"/nxlx/go\0\0\0\0,\0\0\0")


class WhereTest(unittest.TestCase):
    def bridge(self, **kw):
        return render.Bridge(nets=lambda: NETS, tunnel=lambda: ipaddress.ip_network("10.77.0.0/24"), log=lambda *_: None, **kw)

    def test_where_the_box_may_send(self):
        b = self.bridge()
        self.assertIsNone(b.why_not("192.168.0.30"))
        for bad in ("127.0.0.1", "192.168.0.5", "192.168.0.255", "192.168.0.0", "192.168.1.30", "8.8.8.8", "10.77.0.9", "224.0.0.9"):
            self.assertIsNotNone(b.why_not(bad), bad)
        # the tunnel's network, should a studio network overlap it (D88)
        b2 = render.Bridge(nets=lambda: [ipaddress.IPv4Interface("10.77.0.5/16")], tunnel=lambda: ipaddress.ip_network("10.77.0.0/24"))
        self.assertEqual(b2.why_not("10.77.0.9"), render.WHY_TUNNEL)
        self.assertIsNone(b2.why_not("10.77.1.9"))

    def test_nothing_is_sent_where_the_rule_refuses(self):
        sent = []
        b = self.bridge(udp=lambda: (_ for _ in ()).throw(AssertionError("a socket was opened")))
        node = {"id": "abcd0001", "name": "Max", "host": "192.168.1.30", "port": 7400}      # a network the box left
        with self.assertRaises(render.RenderError) as e:
            b.send(node, "/x", [])
        self.assertEqual(e.exception.status, 409)
        self.assertIn("nothing was sent", str(e.exception))
        self.assertIn("nothing was sent", b.health("abcd0001")["error"])
        self.assertEqual(sent, [])

    def test_a_cue_is_exactly_these_bytes_on_a_loopback_socket(self):
        rx = listener()
        self.addCleanup(rx.close)
        b = LoopBridge(nets=lambda: NETS, log=lambda *_: None)
        node = {"id": "abcd0001", "name": "Max", "host": "127.0.0.1", "port": rx.getsockname()[1]}
        args = [{"type": "string", "value": "hue"}, {"type": "float", "value": 0.25}]
        self.assertEqual(b.send(node, "/nxlx/param", args), {"sent": True, "to": "/nxlx/param"})
        data, _ = rx.recvfrom(2048)
        self.assertEqual(data, b"/nxlx/param\0,sf\0hue\0" + struct.pack(">f", 0.25))
        h = b.health("abcd0001")
        self.assertEqual((h["last_address"], h["error"]), ("/nxlx/param", ""))
        self.assertIsNotNone(h["last_sent"])

    def test_the_caps(self):
        clock = Clock()
        rx = listener()
        self.addCleanup(rx.close)
        b = LoopBridge(nets=lambda: NETS, clock=clock, log=lambda *_: None)
        node = {"id": "abcd0001", "name": "Max", "host": "127.0.0.1", "port": rx.getsockname()[1]}
        got = []
        for _ in range(40):
            try:
                b.send(node, "/x", [])
                got.append(200)
            except render.RenderError as e:
                got.append(e.status)
        self.assertEqual(got.count(200), int(render.NODE_BURST), "a burst, then the rate")
        self.assertEqual(set(got[int(render.NODE_BURST):]), {429})
        clock.t += 1.0
        sent = 0
        for _ in range(40):
            try:
                b.send(node, "/x", [])
                sent += 1
            except render.RenderError:
                pass
        self.assertLessEqual(sent, int(render.NODE_RATE + render.NODE_BURST))
        # and all nodes together
        many = [{"id": "abcd%04d" % i, "name": "N", "host": "127.0.0.1", "port": rx.getsockname()[1]} for i in range(8)]
        clock.t += 100
        total = 0
        for _ in range(10):
            for n in many:
                try:
                    b.send(n, "/x", [])
                    total += 1
                except render.RenderError:
                    pass
        self.assertLessEqual(total, int(render.ALL_RATE + render.ALL_BURST))

    def test_a_cue_too_large_for_one_datagram(self):
        b = LoopBridge(nets=lambda: NETS)
        node = {"id": "abcd0001", "name": "Max", "host": "127.0.0.1", "port": 9}
        big = [{"type": "string", "value": "x" * 64}] * 8
        self.assertLess(len(render.encode("/" + "a" * 127, big)), render.MAX_DATAGRAM, "the form's own bounds fit one datagram")

    def test_a_local_name_is_looked_up_and_held_to_the_same_rule(self):
        rx = listener()
        self.addCleanup(rx.close)
        answers = {"render-mac.local": "127.0.0.1", "far.local": "8.8.8.8", "box.local": "192.168.0.5"}
        b = LoopBridge(nets=lambda: NETS, resolve=lambda name: answers[name])
        node = {"id": "abcd0001", "name": "Max", "host": "render-mac.local", "port": rx.getsockname()[1]}
        b.send(node, "/x", [])
        self.assertEqual(rx.recvfrom(64)[0], b"/x\0\0,\0\0\0")
        for host in ("far.local", "box.local", "missing.local"):
            with self.assertRaises(render.RenderError, msg=host) as e:
                b.send(dict(node, id="abcd0002", host=host), "/x", [])
            self.assertEqual(e.exception.status, 409)

    def test_a_lookup_that_hangs_holds_up_nobody_and_one_runs_at_a_time(self):
        go = threading.Event()
        self.addCleanup(go.set)
        calls = []

        def slow(name):
            calls.append(name)
            go.wait(5)
            return "192.168.0.30"
        b = render.Bridge(nets=lambda: NETS, resolve=slow)
        node = {"id": "abcd0001", "name": "Max", "host": "render-mac.local", "port": 7400}
        t = time.monotonic()
        with self.assertRaises(render.RenderError):
            b.send(node, "/x", [], wait=0.2)
        self.assertLess(time.monotonic() - t, 1.5)
        with self.assertRaises(render.RenderError) as e:
            b.send(node, "/x", [], wait=0.2)
        self.assertIn("still being looked up", str(e.exception))
        self.assertEqual(len(calls), 1)


class ProfileTest(unittest.TestCase):
    def test_the_max_profile_makes_ordinary_cues(self):
        c = render.profile_cue("max", "scene", [3], "abcd0001")
        self.assertEqual(c, {"node": "abcd0001", "address": "/nxlx/scene", "args": [{"type": "int", "value": 3}], "show": False})
        c = render.profile_cue("max", "color", ["bg", 1, 0.5, 0], "abcd0001")
        self.assertEqual([a["type"] for a in c["args"]], ["string", "float", "float", "float"])
        for bad in (("max", "scene", [1000]), ("max", "scene", []), ("max", "param", ["x", 2.0]), ("max", "nope", []),
                    ("resolume", "scene", [1])):
            with self.assertRaises(render.RenderError, msg=bad):
                render.profile_cue(bad[0], bad[1], bad[2], "abcd0001")
        self.assertEqual(sorted(render.PROFILES), ["max"], "the only profile is this project's own scheme")
        for cue in render.PROFILES["max"]["cues"].values():
            self.assertTrue(cue["address"].startswith("/nxlx/"))

    def test_the_patch_is_json_and_receives_the_profiles_addresses(self):
        path = os.path.join(REPO, "tools", "max", "nxlx-render-node.maxpat")
        with open(path) as f:
            patch = json.load(f)
        boxes = [b["box"] for b in patch["patcher"]["boxes"]]
        texts = [b.get("text", "") for b in boxes]
        self.assertIn("udpreceive %d" % render.MAX_PORT, texts)
        routes = " ".join(t for t in texts if t.startswith("route "))
        for cue in render.PROFILES["max"]["cues"].values():
            self.assertIn(cue["address"], routes)
        self.assertTrue(any(t.startswith("jit.world") for t in texts))
        self.assertTrue(any(t.startswith("jit.ndi.send~") or t.startswith("jit.ndi.send") for t in texts))
        ids = {b["id"] for b in boxes}
        for line in patch["patcher"]["lines"]:
            self.assertIn(line["patchline"]["source"][0], ids)
            self.assertIn(line["patchline"]["destination"][0], ids)
        self.assertIn("Apache-2.0", json.dumps(patch))


class ApiBase(PadBase):
    """The roles' rig with the NDI input (the fake library behind the real helper), the box on 192.168.0.5/24 and the
    tunnel on its own port, and a bridge whose datagrams reach a loopback listener."""

    def setUp(self):
        super().setUp()
        self.api._ip_json = lambda: ETH
        self.rx = listener()
        self.addCleanup(self.rx.close)
        self.clock = Clock()
        self.socks = Redirect(self.rx.getsockname()[1])
        self.api.render = render.Bridge(nets=self.api._render_nets, tunnel=lambda: self.api.support.network(), clock=self.clock,
                                        log=lambda *_: None, udp=self.socks)

    def add(self, by=None, client=LAN, **node):
        return self.h("POST", "/api/render", {"add": dict({"name": "Max", "host": NODE_HOST, "port": NODE_PORT}, **node)}, by or self.owner, client)

    def node_id(self):
        return self.settings.data["render"]["nodes"][0]["id"]


class NodesTest(ApiBase):
    def test_the_owner_keeps_the_list_and_its_rule(self):
        st, out = self.add(ndi=RID)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["nodes"][0]["host"], NODE_HOST)
        self.assertEqual(out["nodes"][0]["picture"], "here")
        self.assertEqual(self.settings.data["ndi"]["sources"], [ROW], "the node's source is allowed, as a pad's")
        for host, words in (("192.168.1.30", "not on one of the box's own networks"), ("192.168.0.5", "the box's own address"),
                            ("192.168.0.255", "broadcast"), ("10.77.0.9", "not on one of the box's own networks"), ("8.8.8.8", "private")):
            st, out = self.add(host=host, port=7500)
            self.assertEqual(st, 400, host)
            self.assertIn(words, out["error"], host)
        self.assertEqual(self.add()[0], 400, "the same host and port twice")
        self.assertEqual(self.add(host="render-mac.local")[0], 200)
        self.assertEqual(self.add(name="X", port=7600, ndi="0123456789ab")[0], 404, "a source that is not on the network")
        nid = self.node_id()
        st, out = self.h("POST", "/api/render", {"update": {"id": nid, "still": 0}}, self.owner)
        self.assertEqual((st, out["nodes"][0]["still"]), (200, 0))
        self.assertEqual(self.h("POST", "/api/render", {"update": {"id": nid, "host": "8.8.8.8"}}, self.owner)[0], 400)
        self.assertEqual(self.h("POST", "/api/render", {"remove": {"id": nid}}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/render", {"remove": {"id": nid}}, self.owner)[0], 404)
        self.assertEqual(self.settings.data["schema"], Settings(self.settings.path).load()["schema"])

    def test_an_operator_sees_names_and_health_and_no_address(self):
        self.add(ndi=RID)
        st, out = self.h("GET", "/api/render", {}, self.operator)
        self.assertEqual(st, 200)
        row = out["nodes"][0]
        self.assertEqual((row["name"], row["picture"], row["ndi_name"]), ("Max", "here", NAME))
        for key in ("host", "port", "ndi_from"):
            self.assertNotIn(key, row)
        self.assertNotIn(NODE_HOST, json.dumps(out))
        self.assertEqual(out["reply"], False)
        self.assertIn("no reply", out["sent_words"])
        for dev in (self.operator, self.guest, CONTROLLER_DEVICES["osc"]):
            self.later()
            self.assertEqual(self.add(by=dev, port=7700)[0], 403)
        self.assertEqual(self.h("GET", "/api/render", {}, self.guest)[0], 403)
        self.announce()
        self.assertEqual(self.h("GET", "/api/render", {}, self.operator)[1]["nodes"][0]["picture"], "away")
        self.enable(False)
        self.assertEqual(self.h("GET", "/api/render", {}, self.operator)[1]["nodes"][0]["picture"], "off")

    def test_a_support_session_of_the_owners_role_is_held_to_the_box_networks(self):
        self.h("POST", "/api/support/config", dict(__import__("tests.test_support", fromlist=["CFG"]).CFG, allowed=True), self.owner)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        self.assertEqual(st, 200, body)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        dev = self.api.support.authenticate(login["token"])
        net = self.api.support.network()
        inside = str(next(net.hosts()))
        self.assertEqual(self.add(by=dev, client=TUNNEL, host=inside)[0], 400, "never the tunnel's network")
        self.assertEqual(self.add(by=dev, client=TUNNEL)[0], 200)
        major = [d["what"] for d in self.settings.data["support_log"][0].get("major", [])]
        self.assertIn("changed the render nodes", " ".join(major))
        self.assertEqual(self.add(by=dict(dev), client=LAN, port=7800)[0], 403, "a support login from the studio's network is no login")


class CueTest(ApiBase):
    def cue_pad(self, index=0, **cue):
        return self.h("POST", "/api/pads", {"bank": 0, "index": index, "label": "Scene 3",
                                            "render": dict({"node": self.node_id(), "address": "/nxlx/scene", "args": [{"type": "int", "value": 3}]}, **cue)}, self.operator)

    def got(self):
        return self.rx.recvfrom(2048)[0]

    def test_by_hand_and_on_a_pad_for_everyone_who_may(self):
        self.add(ndi=RID)
        want = b"/nxlx/scene\0,i\0\0" + struct.pack(">i", 3)
        body = {"node": self.node_id(), "address": "/nxlx/scene", "args": [{"type": "int", "value": 3}]}
        st, out = self.h("POST", "/api/render/cue", body, self.operator)
        self.assertEqual((st, out["sent"], out["reply"]), (200, True, False), out)
        self.assertEqual(self.got(), want)
        self.assertEqual(self.socks.sent[-1][1], (NODE_HOST, NODE_PORT))
        for dev in (self.guest, CONTROLLER_DEVICES["midi"]):
            self.later()
            self.assertEqual(self.h("POST", "/api/render/cue", body, dev)[0], 403)
        st, out = self.cue_pad()
        self.assertEqual(st, 200, out)
        self.assertEqual(out["render"], {self.node_id(): {"name": "Max"}})
        self.assertEqual(self.h("GET", "/api/pads", {}, self.guest)[1].get("render"), None)
        for name, dev in (("operator", self.operator), ("owner", self.owner), ("midi", CONTROLLER_DEVICES["midi"]),
                          ("osc", CONTROLLER_DEVICES["osc"]), ("room", CONTROLLER_DEVICES["room"])):
            self.clock.t += 10
            st, out = self.press(dev)
            self.assertEqual(st, 200, (name, out))
            self.assertEqual(self.got(), want, name)
        self.later()
        st, out = self.press(self.guest)
        self.assertEqual((st, out["error"]), (403, "this pad cues a render node; ask an operator to start it"))
        for body in ({"scene": SCENE}, {"number": 1}):
            self.later()
            st, out = self.h("POST", "/api/room/scene", body, self.guest)
            self.assertEqual((st, out["error"]), (403, "this scene cues a render node; ask an operator to start it"), body)

    def test_what_a_pad_refuses(self):
        self.add()
        for cue, status in (({"node": "0f0f0f0f"}, 404), ({"address": "/a b"}, 400), ({"show": True}, 409)):
            self.assertEqual(self.cue_pad(**cue)[0], status, cue)
        st, out = self.h("POST", "/api/pads", {"bank": 0, "index": 0, "file": "a.mp4", "render": {"node": self.node_id(), "address": "/x"}}, self.operator)
        self.assertEqual(st, 400)
        self.assertEqual(self.pad(), {"label": "", "file": ""})

    def test_a_node_off_the_network_sends_nothing_and_says_so(self):
        self.add()
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.1.0.5", "prefixlen": 24}]}]
        self.api.osc._nets_seen = (None, [])
        st, out = self.h("POST", "/api/render/cue", {"node": self.node_id(), "address": "/x"}, self.operator)
        self.assertEqual(st, 409)
        self.assertIn("nothing was sent", out["error"])
        self.assertEqual(self.socks.sent, [])
        self.assertIn("nothing was sent", self.h("GET", "/api/render", {}, self.operator)[1]["nodes"][0]["error"])

    def test_a_show_whose_picture_is_away_sends_nothing(self):
        self.add(ndi=RID)
        self.announce()
        st, out = self.h("POST", "/api/render/cue", {"node": self.node_id(), "address": "/x", "show": True}, self.operator)
        self.assertEqual(st, 409)
        self.assertIn("nothing was sent", out["error"])
        self.assertEqual(self.socks.sent, [])


def as_state(real, state):
    """The helper's status with what plays said to be in `state`."""
    def status():
        st = real()
        if isinstance(st.get("playing"), dict):
            st = dict(st, playing=dict(st["playing"], state=state))
        return st
    return status


class FallbackTest(ApiBase):
    """The way back (D94) with the fake NDI library behind the real helper: a clip plays, a cue shows the node's
    picture, the node goes, and the clip is back within GONE_SECONDS and a tick, said once."""

    def setUp(self):
        super().setUp()
        self.add(ndi=RID)
        self.clip = os.path.join(self.media, "a.mp4")
        self.player.status = lambda: {"running": True, "path": self.clip, "loop_file": "inf", "loop_playlist": "no"}
        self.feed()

    def show(self, address="/nxlx/go"):
        st, out = self.h("POST", "/api/render/cue", {"node": self.node_id(), "address": address, "show": True}, self.operator)
        self.assertEqual((st, out.get("shown")), (200, True), out)
        self.assertEqual(self.api.ndi.current["id"], RID)
        self.assertFalse(self.api.render_tick())
        self.assertTrue(self.api.render.showing["confirmed"])
        self.plays = len(self.player.plays)

    def test_the_source_goes_and_the_clip_comes_back_once(self):
        self.show()
        self.assertTrue(self.h("GET", "/api/render", {}, self.operator)[1]["nodes"][0]["showing"])
        self.announce()                                   # the node's sender is no longer on the network
        self.assertFalse(self.api.render_tick())          # seen gone: the count starts
        self.clock.t += render.GONE_SECONDS - 0.5
        self.assertFalse(self.api.render_tick())
        self.assertEqual(len(self.player.plays), self.plays, "not before GONE_SECONDS")
        self.clock.t += 0.5
        self.assertTrue(self.api.render_tick())
        self.assertEqual(self.player.plays[-1]["paths"], [self.clip])
        self.assertEqual(self.player.plays[-1]["ending"], "loop")
        self.assertTrue(wait(lambda: self.api.ndi.current is None))
        lines = [l for l in self.lines if "render node 'Max':" in l]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("not on the network any more", lines[0])
        self.assertIn("back to a.mp4", lines[0])
        back = self.h("GET", "/api/render", {}, self.operator)[1]["back"]
        self.assertEqual((back["node"], back["to"]), ("Max", "back to a.mp4"))
        self.clock.t += 10
        self.assertFalse(self.api.render_tick(), "said once: nothing more to do")
        self.assertEqual(len(self.player.plays), self.plays + 1)

    def test_a_later_choice_stands(self):
        self.show()
        self.h("POST", "/api/play", {"file": "b.mov"}, self.operator)
        self.announce()
        for _ in range(3):
            self.clock.t += render.GONE_SECONDS
            self.assertFalse(self.api.render_tick())
        self.assertEqual(self.player.plays[-1]["paths"][0], os.path.join(self.media, "b.mov"))
        self.assertIsNone(self.api.render.showing)

    def test_a_picture_that_ended_by_itself_goes_back_at_once(self):
        self.show()
        self.api.ndi.current, self.api.ndi.ended = None, {"name": NAME, "message": "the source stopped"}
        self.assertTrue(self.api.render_tick())
        self.assertEqual(self.player.plays[-1]["paths"], [self.clip])
        self.assertIn("the source stopped", self.api.render.back["why"])

    def test_a_connection_that_dropped_and_a_helper_that_went(self):
        self.show()
        real = self.client.status
        self.client.status = as_state(real, "waiting")
        self.api.render_tick()
        self.clock.t += render.GONE_SECONDS
        self.assertTrue(self.api.render_tick())
        self.assertIn("connection dropped", self.api.render.back["why"])
        self.client.status = real
        self.show()
        self.client.down = True
        self.api.render_tick()
        self.clock.t += render.GONE_SECONDS
        self.assertTrue(self.api.render_tick())
        self.assertIn("helper does not answer", self.api.render.back["why"])

    def test_a_picture_that_stands_still(self):
        self.show()
        real = self.client.status
        self.client.status = as_state(real, "still")
        self.api.render_tick()
        self.clock.t += render.STILL_DEFAULT
        self.api.render_tick()
        self.clock.t += render.GONE_SECONDS
        self.assertTrue(self.api.render_tick())
        self.assertIn("stood still", self.api.render.back["why"])
        # a node whose still time is 0 (never) keeps a still picture
        self.h("POST", "/api/render", {"update": {"id": self.node_id(), "still": 0}}, self.owner)
        self.show()
        for _ in range(5):
            self.clock.t += 60
            self.assertFalse(self.api.render_tick())

    def test_nothing_before_means_stop_and_a_shader_comes_back(self):
        self.player.status = lambda: {"running": False}
        self.show()
        self.announce()
        self.api.render_tick()
        self.clock.t += render.GONE_SECONDS
        self.assertTrue(self.api.render_tick())
        self.assertEqual(self.api.render.back["to"], "stopped: nothing played before the cue")
        self.assertIn(("clear",), [c[:1] for c in self.player.calls])

    def test_a_second_cue_while_the_picture_is_up_keeps_the_first_before(self):
        self.show()
        self.player.status = lambda: {"running": True, "path": self.service.fifo}
        self.show("/nxlx/scene")
        self.assertEqual(self.api.render.showing["before"]["kind"], "clips")


class StoredTest(ApiBase):
    def test_export_and_import_keep_the_nodes_and_a_helpers_file_is_held_to_the_networks(self):
        self.add(ndi=RID)
        self.h("POST", "/api/pads", {"bank": 0, "index": 2, "render": {"node": self.node_id(), "address": "/nxlx/go"}}, self.operator)
        file = self.h("POST", "/api/system/settings/export", {}, self.owner)[1]["file"]
        self.assertEqual(file["settings"]["render"], self.settings.data["render"])
        self.assertEqual(file["settings"]["pads"]["banks"][0]["pads"][2]["render"]["address"], "/nxlx/go")
        clean, notes, _ = self.api.boxcare.check_file(json.dumps(file).encode())
        self.assertEqual(clean["render"], self.settings.data["render"])
        far = json.loads(json.dumps(clean))
        far["render"]["nodes"].append(dict(far["render"]["nodes"][0], id="abcd0009", host="192.168.9.9"))
        kept = []
        self.api.boxcare._remote_parts(far, kept)
        self.assertEqual(far["render"], self.settings.data["render"])
        diag = boxcare.public_settings(self.settings.data)
        self.assertNotIn(NODE_HOST, json.dumps(diag["render"]))

    def test_an_older_release_loads_the_file_and_the_pad_is_empty_there(self):
        master = "1ed58bcbb5acf8b1b4122b40c3f480b186957f8d"     # the release before D94, by its commit (D62's lesson)
        self.add(ndi=RID)
        self.h("POST", "/api/pads", {"bank": 0, "index": 0, "label": "Go", "render": {"node": self.node_id(), "address": "/nxlx/go"}}, self.operator)
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", master], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % master)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", master, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "render.py")), "the older release has no render bridge")
        script = r'''
import json, sys
from pvj.settings import Settings
from pvj import boxcare
from pvj.api import Api
s = Settings(sys.argv[1])
s.load()
pad = s.data["pads"]["banks"][0]["pads"][0]
out = {"schema": s.data["schema"], "file": pad["file"], "ndi": Api.pad_ndi(pad), "shader": Api.pad_shader(pad)}
out["checked"] = boxcare.check_pads(s.data["pads"], None)["banks"][0]["pads"][0]
s.data["mix"] = {"transition": "dip", "duration": 2.0}
s.save()
print(json.dumps(out))
'''
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual((out["schema"], out["file"], out["ndi"], out["shader"]), (self.settings.data["schema"], "", None, None))
        self.assertEqual(out["checked"], {"label": "Go", "file": ""})
        with open(copy) as f:
            kept = json.load(f)
        self.assertEqual(kept["render"], self.settings.data["render"], "the older release's save keeps what it does not know")
        self.assertEqual(kept["pads"]["banks"][0]["pads"][0]["render"]["address"], "/nxlx/go")


if __name__ == "__main__":
    unittest.main()
