# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Nodes that find each other (pvj/fleet.py, the fleet_* commands of pvj/sysd.py, GET /api/nodes; D127): the reading of
the network's answer with several nodes, a malformed packet, our own node, a goodbye and a hostile one; the cached,
never-blocking list; the announcement being sent once and again only on a change; the service file the root helper
writes; the node id; who may ask."""
import json
import os
import stat
import struct
import tempfile
import threading
import unittest

import tests        # noqa: F401  (the run's own temp folder)
from pvj import fleet, sysd
from tests.test_roles import RolesBase

ID1, ID2, ID3, ID_SELF = "1" * 32, "2" * 32, "3" * 32, "a" * 32


def name(labels):
    return b"".join(bytes([len(l.encode())]) + l.encode() for l in labels) + b"\0"


def rr(owner, rtype, rdata, ttl=10):
    return name(owner) + struct.pack(">HHIH", rtype, 0x8001, ttl, len(rdata)) + rdata


def txt_data(items):
    out = b""
    for k, v in items.items():
        s = ("%s=%s" % (k, v)).encode()
        out += bytes([len(s)]) + s
    return out


def node_records(instance, ident, port=443, host="node", tier="standard", version="0.1.0", https="1", extra=None, ptr_ttl=10):
    owner = [instance, "_nxlx", "_tcp", "local"]
    items = {"txtvers": "1", "id": ident, "name": instance, "tier": tier, "version": version, "https": https}
    items.update(extra or {})
    ptr = rr(["_nxlx", "_tcp", "local"], 12, name(owner), ptr_ttl)
    srv = rr(owner, 33, struct.pack(">HHH", 0, 0, port) + name([host, "local"]))
    return ptr, srv, rr(owner, 16, txt_data(items))


def packet(*nodes_records):
    """One answer: the first record of each node is the answer, its two others are additional records."""
    ptrs = [g[0] for g in nodes_records]
    extra = [r for g in nodes_records for r in g[1:]]
    return struct.pack(">HHHHHH", 0, 0x8400, 0, len(ptrs), 0, len(extra)) + b"".join(ptrs) + b"".join(extra)


class Transport:
    """A network that answers the first question with `first` and any later question with `later`."""
    def __init__(self, first=(), later=()):
        self.first, self.later, self.asked = list(first), list(later), []

    def __call__(self, queries, seconds):
        self.asked.append((list(queries), seconds))
        return self.first if len(self.asked) == 1 else self.later


LAN = "192.168.0.169"
LAN2 = "192.168.0.170"


class ReadingTest(unittest.TestCase):
    def test_several_nodes_in_one_answer_with_all_their_fields(self):
        t = Transport([(LAN, packet(node_records("node-01", ID1, 443, tier="standard", version="0.1.0"),
                                     node_records("node-02", ID2, 80, tier="lean", version="0.1.1", https="0")))])
        rows = sorted(fleet.scan(t), key=lambda r: r["name"])
        self.assertEqual(len(t.asked), 1, "the answer carried everything: no second question")
        self.assertEqual(rows[0], {"id": ID1, "name": "node-01", "address": LAN, "port": 443, "tier": "standard", "version": "0.1.0", "https": True})
        self.assertEqual((rows[1]["name"], rows[1]["port"], rows[1]["tier"], rows[1]["version"], rows[1]["https"]), ("node-02", 80, "lean", "0.1.1", False))

    def test_the_question_is_one_ptr_question_for_the_service_from_no_special_port(self):
        t = Transport()
        fleet.scan(t)
        q = t.asked[0][0][0]
        self.assertEqual(struct.unpack(">HHHHHH", q[:12]), (0, 0, 1, 0, 0, 0))
        self.assertEqual(q[12:], name(["_nxlx", "_tcp", "local"]) + struct.pack(">HH", 12, 1), "class IN with no unicast bit")

    def test_a_node_whose_records_did_not_fit_is_asked_for_by_name(self):
        ptr, srv, txt = node_records("node-03", ID3, 443)
        t = Transport([(LAN, packet((ptr,)))], [(LAN, packet((srv, txt)))])
        rows = fleet.scan(t)
        self.assertEqual([(r["name"], r["port"]) for r in rows], [("node-03", 443)])
        self.assertEqual(len(t.asked), 2)
        self.assertEqual(t.asked[1][0][0][-4:], struct.pack(">HH", 255, 1))

    def test_nothing_heard_is_an_empty_list_not_an_error(self):
        self.assertEqual(fleet.scan(Transport()), [])

    def test_a_malformed_packet_is_dropped_and_the_others_still_count(self):
        good = packet(node_records("node-01", ID1))
        junk = [b"", b"\x00" * 5, good[:30], good[:-3], b"\xff" * 400, struct.pack(">HHHHHH", 0, 0x0000, 0, 1, 0, 0) + b"x",
                struct.pack(">HHHHHH", 0, 0x8400, 0, 60000, 0, 60000)]
        rows = fleet.scan(Transport([(LAN, p) for p in junk] + [(LAN, good)]))
        self.assertEqual([r["id"] for r in rows], [ID1])

    def test_a_pointer_loop_or_forward_pointer_cannot_hang_or_read_outside(self):
        loop = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c" + struct.pack(">HHIH", 12, 1, 10, 2) + b"\xc0\x0c"
        forward = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\xff" + struct.pack(">HHIH", 12, 1, 10, 0)
        for p in (loop, forward):
            self.assertEqual(fleet.scan(Transport([(LAN, p)])), [])
        with self.assertRaises(fleet.Bad):
            fleet.parse(loop)

    def test_a_compressed_answer_reads_the_same(self):
        owner = ["node-04", "_nxlx", "_tcp", "local"]
        head = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 2)
        ptr = name(["_nxlx", "_tcp", "local"]) + struct.pack(">HHIH", 12, 1, 10, 2 + 8) + bytes([7]) + b"node-04" + b"\xc0\x0c"
        # the owner names of the two additional records point back at the PTR's target (offset of the 7-byte label)
        target = len(head) + len(name(["_nxlx", "_tcp", "local"])) + 10
        srv = struct.pack(">H", 0xC000 | target) + struct.pack(">HHIH", 33, 1, 10, 6 + 1) + struct.pack(">HHH", 0, 0, 8443) + b"\0"
        txt_body = txt_data({"id": ID1, "name": "node-04", "tier": "full", "version": "0.2.0"})
        txt = struct.pack(">H", 0xC000 | target) + struct.pack(">HHIH", 16, 1, 10, len(txt_body)) + txt_body
        rows = fleet.scan(Transport([(LAN, head + ptr + srv + txt)]))
        self.assertEqual([(r["name"], r["port"], r["tier"]) for r in rows], [("node-04", 8443, "full")])

    def test_fields_are_checked_and_cut(self):
        hostile = node_records("node-05", ID1, extra={"tier": "ultra", "version": "<script>alert(1)</script>", "name": "A" * 200 + "\x07\x1b[31m"})
        no_id = node_records("node-06", "not-an-id")
        zero_port = node_records("node-07", ID2, 0)
        rows = fleet.scan(Transport([(LAN, packet(hostile, no_id, zero_port))]))
        self.assertEqual(len(rows), 1, "no id and no port are not a node")
        r = rows[0]
        self.assertEqual((r["tier"], r["version"]), ("unknown", ""))
        self.assertEqual(len(r["name"]), 63)
        self.assertTrue(r["name"].isprintable())

    def test_only_local_addresses_may_announce(self):
        p = packet(node_records("node-08", ID1))
        for bad in ("8.8.8.8", "not an address", "::1", "93.184.216.34"):
            self.assertEqual(fleet.scan(Transport([(bad, p)])), [], bad)
        for good in ("10.1.2.3", "172.16.0.9", "169.254.1.1", "127.0.0.1"):
            self.assertEqual(len(fleet.scan(Transport([(good, p)]))), 1, good)

    def test_a_goodbye_removes_the_node(self):
        p1 = packet(node_records("node-09", ID1))
        bye = packet(node_records("node-09", ID1, ptr_ttl=0))
        self.assertEqual(fleet.scan(Transport([(LAN, p1), (LAN, bye)])), [])

    def test_the_list_is_bounded(self):
        many = [(LAN, packet(*[node_records("n%d" % i, "%032x" % (i + 1)) for i in range(20)])) for _ in range(20)]
        self.assertLessEqual(len(fleet.scan(Transport(many))), fleet.MAX_ROWS)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def row(ident, name, address=LAN, port=443, tier="standard", version="0.1.0", https=True):
    return {"id": ident, "name": name, "address": address, "port": port, "tier": tier, "version": version, "https": https}


class BrowserTest(unittest.TestCase):
    def make(self, scanner, own=ID_SELF):
        self.clock = Clock()
        self.lines = []
        return fleet.Browser(scanner, own, now=self.clock, log=self.lines.append)

    def test_the_first_look_has_the_answer_and_drops_our_own_node(self):
        b = self.make(lambda: [row(ID1, "node-01"), row(ID_SELF, "me"), row(ID2, "node-02", LAN2, 80, "lean", "0.1.1", False)])
        v = b.nodes()
        self.assertEqual([n["name"] for n in v["nodes"]], ["node-01", "node-02"])
        self.assertEqual({k for n in v["nodes"] for k in n}, {"id", "name", "address", "port", "tier", "version", "https", "seen_seconds_ago"})
        self.assertEqual([n["seen_seconds_ago"] for n in v["nodes"]], [0, 0])
        self.assertIsNone(v["error"])

    def test_it_is_cached_for_ten_seconds_and_asked_again_after(self):
        calls = []
        b = self.make(lambda: calls.append(1) or [row(ID1, "node-01")])
        b.nodes()
        self.clock.t += 9
        self.assertEqual(b.nodes()["nodes"][0]["seen_seconds_ago"], 9)
        self.assertEqual(len(calls), 1)
        self.clock.t += 2
        b.nodes()
        for t in threading.enumerate():
            if t.name == "fleet-scan":
                t.join(5)
        self.assertEqual(len(calls), 2)

    def test_a_slow_scan_never_holds_the_request_longer_than_the_first_wait(self):
        release = threading.Event()
        b = self.make(lambda: release.wait(5) and [])
        import time
        t0 = time.monotonic()
        v = b.nodes(wait=0.2)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual((v["nodes"], v["scanning"]), ([], True))
        release.set()
        for th in threading.enumerate():
            if th.name == "fleet-scan":
                th.join(5)
        t0 = time.monotonic()
        b.nodes(wait=5)
        self.assertLess(time.monotonic() - t0, 0.15, "after the first look it does not wait at all")

    def test_a_node_missed_by_one_scan_stays_and_one_unseen_for_35_seconds_goes(self):
        found = [[row(ID1, "node-01")], [], [], []]
        b = self.make(lambda: found.pop(0))

        def scan_once():
            b.nodes()
            for t in threading.enumerate():
                if t.name == "fleet-scan":
                    t.join(5)
        scan_once()
        self.clock.t += 11
        scan_once()
        self.assertEqual([(n["name"], n["seen_seconds_ago"]) for n in b.nodes()["nodes"]], [("node-01", 11)])
        self.clock.t += 30
        scan_once()
        self.assertEqual(b.nodes()["nodes"], [])

    def test_a_failing_network_is_an_error_line_once_and_never_an_exception(self):
        def boom():
            raise OSError("Network is unreachable")
        b = self.make(boom)
        v = b.nodes()
        self.assertEqual((v["nodes"], v["error"]), ([], "the network could not be asked"))
        self.assertNotIn("unreachable", json.dumps(v), "the system's text is for the log")
        self.assertEqual(len(self.lines), 1)

    def test_two_rows_with_one_id_at_two_addresses_both_show(self):
        b = self.make(lambda: [row(ID1, "node-01", LAN), row(ID1, "node-01", LAN2)])
        self.assertEqual([n["address"] for n in b.nodes()["nodes"]], [LAN, LAN2])


class FakeSysd:
    def __init__(self, avahi="running", fail=None):
        self.sent, self.avahi, self.fail = [], avahi, fail

    def request(self, message):
        self.sent.append(message)
        if self.fail:
            raise self.fail
        return {"ok": True, "avahi": self.avahi} if message["cmd"] == "fleet_announce" else {"ok": True}


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.facts = {"id": ID_SELF, "name": "node-01", "tier": "standard", "version": "0.1.0", "port": 80, "https": False}
        self.on = True
        self.clock = Clock()
        self.lines, self.events = [], []
        self.sysd = FakeSysd()

    def make(self, sysd=None, scanner=lambda: []):
        return fleet.Fleet(lambda: dict(self.facts), lambda: self.on, self.sysd if sysd is None else sysd, scanner,
                           log=self.lines.append, event=lambda *a: self.events.append(a), now=self.clock)

    def test_announced_once_and_again_only_when_something_changed(self):
        f = self.make()
        f.tick()
        f.tick()
        self.clock.t += 20
        f.tick()
        self.assertEqual(len(self.sysd.sent), 1)
        self.assertEqual(self.sysd.sent[0], dict(cmd="fleet_announce", id=ID_SELF, name="node-01", tier="standard", version="0.1.0", port=80, https=False))
        self.assertEqual(f.state, "announcing")
        self.facts["name"] = "node-09"             # a rename
        f.tick()
        self.facts.update(version="0.1.1")         # an update
        f.tick()
        self.facts.update(port=443, https=True)    # a certificate arrived
        f.tick()
        self.assertEqual([(m["name"], m["version"], m["port"]) for m in self.sysd.sent[1:]],
                         [("node-09", "0.1.0", 80), ("node-09", "0.1.1", 80), ("node-09", "0.1.1", 443)])
        self.assertEqual(self.sysd.sent[-1]["id"], ID_SELF, "the id is the same through all of them")

    def test_the_switch_off_withdraws_the_file_and_stops_asking_others(self):
        asked = []
        f = self.make(scanner=lambda: asked.append(1) or [row(ID1, "node-01")])
        f.tick()
        self.on = False
        f.tick(force=True)
        self.assertEqual(self.sysd.sent[-1], {"cmd": "fleet_withdraw"})
        v = f.view()
        self.assertEqual((v["nodes"], v["discovery"]["on"], v["discovery"]["state"]), ([], False, "off"))
        self.assertEqual(asked, [], "no question was sent while it was off")
        self.on = True
        f.tick()
        self.assertEqual(self.sysd.sent[-1]["cmd"], "fleet_announce")

    def test_no_avahi_is_said_plainly_and_asked_again_each_minute(self):
        self.sysd = FakeSysd(avahi="missing")
        f = self.make()
        f.tick()
        self.assertEqual(f.state, "no-avahi")
        self.assertIn("avahi-daemon is not installed", f.view()["discovery"]["detail"])
        self.assertTrue(any("not installed" in e[1] for e in self.events), self.events)
        self.assertTrue(any("no-avahi" in l for l in self.lines))
        self.clock.t += 30
        f.tick()
        self.assertEqual(len(self.sysd.sent), 1)
        self.clock.t += 31
        self.sysd.avahi = "running"                # installed meanwhile
        f.tick()
        self.assertEqual((len(self.sysd.sent), f.state), (2, "announcing"))
        self.assertEqual(f.view()["nodes"], [], "it still lists others, and never failed")

    def test_avahi_installed_but_not_running(self):
        self.sysd = FakeSysd(avahi="stopped")
        f = self.make()
        f.tick()
        self.assertEqual(f.state, "avahi-stopped")
        self.assertIn("not running", f.view()["discovery"]["detail"])

    def test_avahi_stopping_later_shows_within_a_minute_and_the_file_is_asked_for_again(self):
        f = self.make()
        f.tick()
        self.assertEqual(f.state, "announcing")
        self.sysd.avahi = "stopped"
        self.clock.t += 30
        f.tick()
        self.assertEqual((len(self.sysd.sent), f.state), (1, "announcing"))
        self.clock.t += 31
        f.tick()
        self.assertEqual((len(self.sysd.sent), f.state), (2, "avahi-stopped"))
        self.sysd.avahi = "running"
        self.clock.t += 61
        f.tick()
        self.assertEqual(f.state, "announcing")

    def test_without_the_helper_it_keeps_working_and_says_so(self):
        for sysd in (None, FakeSysd(fail=OSError("the system helper (pvj-sysd) is not running"))):
            f = fleet.Fleet(lambda: dict(self.facts), lambda: self.on, sysd, lambda: [], log=self.lines.append, event=lambda *a: None, now=self.clock)
            f.tick()
            self.assertEqual(f.state, "no-helper")
            self.assertEqual(f.view()["nodes"], [])

    def test_a_refusal_is_an_error_state_without_the_helpers_text(self):
        class Refusing(FakeSysd):
            def request(self, message):
                return {"ok": False, "error": "the service file could not be written"}
        f = self.make(sysd=Refusing())
        f.tick()
        self.assertEqual(f.state, "error")
        self.assertNotIn("/", f.view()["discovery"]["detail"])

    def test_the_view_has_the_nodes_own_facts_and_not_a_path_or_a_secret(self):
        f = self.make(scanner=lambda: [row(ID1, "node-02")])
        v = f.view()
        self.assertEqual(v["self"], {"id": ID_SELF, "name": "node-01", "tier": "standard", "version": "0.1.0"})
        text = json.dumps(v)
        for never in ("/var", "/etc", "/run", "token", "pin", "settings"):
            self.assertNotIn(never, text.lower())

    def test_the_switch_as_stored(self):
        self.assertTrue(fleet.stored_on(None))
        self.assertTrue(fleet.stored_on({}))
        self.assertTrue(fleet.stored_on({"discovery": True}))
        self.assertFalse(fleet.stored_on({"discovery": False}))
        for damaged in ({"discovery": "yes"}, {"discovery": 1}, "on", [], 5):
            self.assertFalse(fleet.stored_on(damaged), damaged)


class NodeIdTest(unittest.TestCase):
    def test_it_is_made_once_kept_through_restarts_and_never_shared(self):
        d = tempfile.mkdtemp()
        a, kept = fleet.node_id(d)
        self.assertTrue(kept)
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertEqual(fleet.node_id(d), (a, True))
        self.assertNotEqual(fleet.node_id(tempfile.mkdtemp())[0], a, "another node, another id")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "node-id")).st_mode), 0o644)
        self.assertEqual(os.listdir(d), ["node-id"], "no temp file left")

    def test_a_damaged_file_is_replaced_and_an_unwritable_folder_gives_a_new_id_each_time(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "node-id"), "w") as f:
            f.write("garbage\n")
        a, kept = fleet.node_id(d)
        self.assertTrue(kept)
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        a, kept = fleet.node_id(os.path.join(d, "no", "such", "folder"))
        self.assertFalse(kept)
        self.assertRegex(a, r"^[0-9a-f]{32}$")

    def test_the_name_a_node_announces_is_made_safe(self):
        self.assertEqual(fleet.clean_hostname("node-01.local"), "node-01")
        self.assertEqual(fleet.clean_hostname("</txt-record>x"), "txt-recordx")
        self.assertEqual(fleet.clean_hostname(""), "node")
        self.assertEqual(fleet.clean_hostname("-.-"), "node")
        self.assertEqual(len(fleet.clean_hostname("a" * 90)), 63)


class ServiceFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.services = os.path.join(self.dir, "services")
        os.mkdir(self.services)
        self.socket = os.path.join(self.dir, "socket")
        self.lines = []
        self.s = sysd.SysService(log=self.lines.append, rundir=self.dir, avahi_services=self.services, avahi_socket=self.socket)
        self.msg = {"cmd": "fleet_announce", "id": ID1, "name": "node-01", "tier": "lean", "version": "0.1.0", "port": 443, "https": True}
        self.path = os.path.join(self.services, "nxlx-mastercontrol.service")

    def read(self):
        with open(self.path) as f:
            return f.read()

    def test_it_writes_the_file_avahi_reads_and_says_whether_avahi_runs(self):
        self.assertEqual(self.s.handle(self.msg), {"ok": True, "avahi": "stopped"})
        open(self.socket, "w").close()
        self.assertEqual(self.s.handle(self.msg), {"ok": True, "avahi": "running"})
        text = self.read()
        self.assertIn("<type>_nxlx._tcp</type>", text)
        self.assertIn("<port>443</port>", text)
        for line in ("txtvers=1", "id=" + ID1, "name=node-01", "tier=lean", "version=0.1.0", "https=1"):
            self.assertIn("<txt-record>%s</txt-record>" % line, text)
        import xml.dom.minidom
        xml.dom.minidom.parseString(text.split("\n", 2)[2].replace('<!DOCTYPE service-group SYSTEM "avahi-service.dtd">\n', ""))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644, "avahi's own account must be able to read it")
        self.assertEqual(os.listdir(self.services), ["nxlx-mastercontrol.service"], "no temp file left")

    def test_the_file_is_not_touched_when_nothing_changed_and_replaced_whole_when_something_did(self):
        self.s.handle(self.msg)
        before = os.stat(self.path).st_ino
        self.s.handle(self.msg)
        self.assertEqual(os.stat(self.path).st_ino, before)
        self.s.handle(dict(self.msg, name="node-02", port=80, https=False))
        text = self.read()
        self.assertIn("name=node-02", text)
        self.assertIn("https=0", text)
        self.assertNotIn("node-01", text)

    def test_no_avahi_is_not_an_error_and_writes_nothing(self):
        s = sysd.SysService(log=self.lines.append, rundir=self.dir, avahi_services=os.path.join(self.dir, "none"), avahi_socket=self.socket)
        self.assertEqual(s.handle(self.msg), {"ok": True, "avahi": "missing"})
        self.assertEqual(s.handle({"cmd": "fleet_withdraw"}), {"ok": True})
        self.assertFalse(os.path.exists(os.path.join(self.dir, "none")))

    def test_withdraw_removes_the_file_and_is_safe_twice(self):
        self.s.handle(self.msg)
        self.assertEqual(self.s.handle({"cmd": "fleet_withdraw"}), {"ok": True})
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.s.handle({"cmd": "fleet_withdraw"}), {"ok": True})

    def test_nothing_that_is_not_a_plain_field_reaches_the_file(self):
        bad = {"id": ["x" * 32, "G" * 32, ID1 + "0", ID1[:-1], 5, None],
               "name": ["</txt-record><service>", "a b", "-x", "", "x" * 64, "a\nb", "../../etc", 5, None],
               "tier": ["", "full\n", "gigantic", 5], "version": ["1.0\n", "<1>", "", "x" * 21, "1 2", 1.0],
               "port": [0, 65536, -1, "443", True, 1.5, None], "https": [1, "yes", None]}
        for key, values in bad.items():
            for v in values:
                r = self.s.handle(dict(self.msg, **{key: v}))
                self.assertEqual(r["ok"], False, (key, v))
        self.assertEqual(os.listdir(self.services), [], "nothing was written")
        missing = dict(self.msg)
        del missing["tier"]
        self.assertFalse(self.s.handle(missing)["ok"])

    def test_a_folder_that_cannot_be_written_is_an_error_without_a_path_in_the_answer(self):
        os.chmod(self.services, 0o500)
        self.addCleanup(os.chmod, self.services, 0o700)
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        r = self.s.handle(self.msg)
        self.assertEqual(r, {"ok": False, "error": "the service file could not be written"})

    def test_the_commands_are_known_to_the_server_and_run_nothing(self):
        self.s.runner = lambda *a, **k: self.fail("a process was started")
        self.s.handle(self.msg)
        self.s.handle({"cmd": "fleet_withdraw"})


class ApiTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.api.event = lambda *a, **k: None
        self.sysd = FakeSysd()
        self.facts = {"id": ID_SELF, "name": "node-01", "tier": "standard", "version": "0.1.0", "port": 80, "https": False}
        self.api.fleet = fleet.Fleet(lambda: dict(self.facts), lambda: fleet.stored_on(self.settings.data.get("fleet")), self.sysd,
                                     lambda: [row(ID1, "node-02", LAN2, 443, "lean", "0.1.1"), row(ID_SELF, "me")], log=lambda *_: None)

    def test_an_operator_and_an_owner_see_the_list_and_a_guest_does_not(self):
        for dev in (self.operator, self.owner):
            st, body = self.h("GET", "/api/nodes", device=dev)
            self.assertEqual(st, 200, body)
            self.assertEqual([(n["id"], n["name"], n["address"], n["port"], n["tier"], n["version"]) for n in body["nodes"]],
                             [(ID1, "node-02", LAN2, 443, "lean", "0.1.1")])
        self.assertEqual(self.h("GET", "/api/nodes", device=self.guest)[0], 403)
        self.lock(True)
        self.assertEqual(self.h("GET", "/api/nodes", device=self.guest)[0], 403)
        self.assertEqual(self.h("GET", "/api/nodes", device=None)[0], 401)

    def test_the_owner_switches_discovery_and_it_is_kept(self):
        st, body = self.h("POST", "/api/nodes/discovery", {"on": False}, self.owner)
        self.assertEqual((st, body["on"], body["state"]), (200, False, "off"))
        self.assertEqual(self.settings.data["fleet"], {"discovery": False})
        self.assertEqual(self.h("GET", "/api/nodes", device=self.operator)[1]["nodes"], [])
        self.assertEqual(self.sysd.sent[-1], {"cmd": "fleet_withdraw"})
        st, body = self.h("POST", "/api/nodes/discovery", {"on": True}, self.owner)
        self.assertEqual((st, body["state"]), (200, "announcing"))
        self.assertEqual(len(self.h("GET", "/api/nodes", device=self.owner)[1]["nodes"]), 1)

    def test_an_operator_and_a_guest_cannot_switch_it_and_a_bad_body_is_refused(self):
        for dev in (self.operator, self.guest):
            self.assertEqual(self.h("POST", "/api/nodes/discovery", {"on": False}, dev)[0], 403)
        for body in ({}, {"on": "no"}, {"on": 1}, {"on": True, "x": 1}, {"off": True}):
            self.assertEqual(self.h("POST", "/api/nodes/discovery", body, self.owner)[0], 400, body)
        self.assertNotIn("fleet", {k for k in self.settings.data if self.settings.data[k] == {"discovery": False}})

    def test_a_node_without_the_service_answers_an_empty_list(self):
        self.api.fleet = None
        st, body = self.h("GET", "/api/nodes", device=self.operator)
        self.assertEqual((st, body["nodes"], body["discovery"]["on"]), (200, [], False))


if __name__ == "__main__":
    unittest.main()
