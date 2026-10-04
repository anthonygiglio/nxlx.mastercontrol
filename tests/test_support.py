# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import base64
import json
import os
import tempfile
import unittest

from pvj import support as sp, supportd as sd
from pvj.settings import Settings
from tests.test_server import ServerBase

KEY = base64.b64encode(b"k" * 32).decode()
BOX_KEY = base64.b64encode(b"b" * 32).decode()
CFG = {"endpoint": "support.example.com:51820", "server_key": KEY, "address": "10.77.0.5", "network": "10.77.0.0/24"}
TUNNEL, LAN = "10.77.0.9", "192.168.1.20"


class FakeRun:
    """Records every command; `fail` makes the command starting with those words fail."""

    def __init__(self):
        self.link = False                    # whether wg-pvj exists, as the real `ip` would say
        self.stuck = False                   # `ip link delete` fails and the link stays
        self.calls, self.fail, self.dump = [], None, "priv\tpub\t51820\toff\n%s\t(none)\t1.2.3.4:51820\t10.77.0.0/24\t1790000000\t1200\t3400\t25\n" % KEY

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        name = os.path.basename(argv[0])
        self.calls.append([name] + argv[1:])
        out, code = "", 0
        if self.fail and [name] + argv[1:1 + len(self.fail) - 1] == self.fail:
            code, out = 1, "failed on purpose"
        elif argv[1:4] == ["link", "add", sd.IFACE]:
            self.link = True
        elif argv[1:4] == ["link", "delete", sd.IFACE]:
            if self.stuck:
                code = 1
            else:
                code, self.link = (0 if self.link else 1), False
        elif argv[1:4] == ["link", "show", sd.IFACE]:
            code = 0 if self.link else 1
        elif argv[1:] == ["genkey"]:
            out = "PRIVATEKEY=\n"
        elif argv[1:] == ["pubkey"]:
            out = BOX_KEY + "\n"
        elif argv[1:4] == ["show", sd.IFACE, "dump"]:
            out = self.dump

        class R:
            pass
        r = R()
        r.returncode, r.stdout, r.stderr = code, out, ""
        return r


class Timers:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        entry = [delay, fn, True]
        self.pending.append(entry)

        def cancel():
            entry[2] = False
        return cancel

    def fire(self):
        for e in list(self.pending):
            if e[2]:
                e[2] = False
                e[1]()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def service(run=None, clock=None):
    run = run or FakeRun()
    clock = clock or Clock()
    timers = Timers()
    svc = sd.SupportService(tempfile.mkdtemp(), runner=run, log=lambda *_: None, timer=timers, clock=clock,
                            now=lambda: 1790000000 + clock.t, which=lambda name, path=None: "/usr/sbin/" + name)
    return svc, run, timers, clock


class ValidationTest(unittest.TestCase):
    def test_good_settings_pass(self):
        c = sd.check_config(dict(CFG, minutes=60))
        self.assertEqual((c["endpoint"], c["address"], c["network"]), ("support.example.com:51820", "10.77.0.5", "10.77.0.0/24"))
        self.assertEqual(sd.check_config(dict(CFG, endpoint="[2001:db8::1]:443", minutes=5))["endpoint"], "[2001:db8::1]:443")

    def test_bad_settings_are_refused(self):
        for bad in (dict(CFG, endpoint="nohost"), dict(CFG, endpoint="a b:1"), dict(CFG, endpoint="x:0"), dict(CFG, endpoint="x:99999"),
                    dict(CFG, endpoint="-bad-.com:1"), dict(CFG, server_key="short"), dict(CFG, server_key="!" * 44),
                    dict(CFG, server_key=base64.b64encode(b"x" * 31).decode() + "AA"),
                    dict(CFG, address="10.78.0.5"), dict(CFG, network="8.8.8.0/24", address="8.8.8.5"),
                    dict(CFG, network="10.77.0.0/8", address="10.77.0.5"), dict(CFG, address="10.77.0.0"),
                    dict(CFG, address="10.77.0.255"), dict(CFG, network="10.77.0.1/24"), dict(CFG, minutes=4), dict(CFG, minutes=241),
                    dict(CFG, minutes=True), dict(CFG, endpoint="host:1\nevil")):
            bad.setdefault("minutes", 60)
            with self.assertRaises(sd.SupportError, msg=str(bad)):
                sd.check_config(bad)

    def test_the_firewall_lets_in_only_the_panel_and_ping(self):
        r = sd.ruleset("10.77.0.0/24", "10.77.0.5", 8080)
        self.assertIn('iifname "wg-pvj" ip saddr 10.77.0.0/24 tcp dport 8080 ct state new accept', r)
        self.assertIn('iifname "wg-pvj" ip daddr != 10.77.0.5 drop', r)          # only this box's tunnel address
        self.assertIn('iifname "wg-pvj" drop', r)
        self.assertIn('oifname "wg-pvj" drop', r)                 # the box starts nothing into the tunnel
        self.assertNotIn("22", r.replace("10.77.0.0/24", ""))       # no SSH, nothing else
        self.assertLess(r.index("daddr !="), r.index("established"))


class HelperTest(unittest.TestCase):
    def test_start_builds_the_tunnel_firewall_first_and_status_reads_it(self):
        svc, run, timers, clock = service()
        reply = svc.handle(dict(CFG, cmd="start", minutes=60))
        self.assertTrue(reply["active"], reply)
        names = [" ".join(c[:4]) for c in run.calls]
        up = names.index("ip link set wg-pvj")
        self.assertLess(names.index("nft -f -"), up)                # the firewall is there before the link is up
        wg = [c for c in run.calls if c[:2] == ["wg", "set"]][0]
        self.assertEqual(wg[wg.index("allowed-ips") + 1], "10.77.0.0/24")
        self.assertEqual(wg[wg.index("persistent-keepalive") + 1], "25")
        self.assertIn(["ip", "address", "add", "10.77.0.5/24", "dev", "wg-pvj"], run.calls)
        st = svc.handle({"cmd": "status"})
        self.assertEqual((st["last_handshake"], st["received"], st["sent"], st["available"]), (1790000000, 1200, 3400, True))

    def test_the_helper_keeps_the_deadline(self):
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=30))
        self.assertEqual(timers.pending[-1][0], 1800)
        clock.t += 1800
        timers.fire()
        self.assertFalse(svc.handle({"cmd": "status"})["active"])
        self.assertIn(["nft", "delete", "table", "inet", sd.TABLE], run.calls)
        self.assertIn(["ip", "link", "delete", sd.IFACE], run.calls[-3:] + run.calls[-2:])

    def test_a_lost_timer_does_not_keep_it_open(self):
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=30))
        clock.t += 1801                                            # the timer never fired
        self.assertFalse(svc.handle({"cmd": "status"})["active"])

    def test_extend_sets_a_new_deadline_without_touching_the_tunnel(self):
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=15))
        before = len(run.calls)
        reply = svc.handle({"cmd": "extend", "minutes": 120})
        self.assertTrue(reply["active"])
        self.assertFalse(any(c[:2] in (["ip", "link"], ["nft", "delete"]) for c in run.calls[before:]))
        clock.t += 15 * 60 + 5
        timers.pending[0][1]()                                     # the old timer is cancelled or harmless
        self.assertTrue(svc.handle({"cmd": "status"})["active"])
        self.assertFalse(svc.handle({"cmd": "extend", "minutes": 500})["ok"])

    def test_the_panel_port_is_used_and_checked(self):
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=60, port=8080))
        nft = [c for c in run.calls if c[:2] == ["nft", "-f"]]
        self.assertTrue(nft)
        self.assertFalse(svc.handle(dict(CFG, cmd="start", minutes=60, port=0))["ok"])

    def test_teardown_removes_the_link_first_and_keeps_the_firewall_if_it_cannot(self):
        """Review finding: the firewall went first, so a link that would not go was left open without it."""
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=60))
        run.stuck = True
        start = len(run.calls)
        svc.handle({"cmd": "stop"})
        after = run.calls[start:]
        self.assertEqual(after[0][:3], ["ip", "link", "delete"])
        self.assertNotIn(["nft", "delete", "table", "inet", sd.TABLE], after)
        run.stuck = False
        svc.handle({"cmd": "stop"})
        self.assertIn(["nft", "delete", "table", "inet", sd.TABLE], run.calls)

    def test_a_session_ends_eight_hours_after_it_started_however_often_it_is_extended(self):
        svc, run, timers, clock = service()
        svc.handle(dict(CFG, cmd="start", minutes=240))
        clock.t += 200 * 60
        self.assertTrue(svc.handle({"cmd": "extend", "minutes": 240})["active"])       # 440 minutes in all
        clock.t += 200 * 60
        self.assertFalse(svc.handle({"cmd": "extend", "minutes": 240})["ok"])          # would be 640

    def test_a_key_file_left_half_written_does_not_block_the_key(self):
        svc, run, timers, clock = service()
        os.makedirs(svc.keydir, exist_ok=True)
        open(svc._keyfile() + ".tmp", "w").close()
        self.assertEqual(svc.handle({"cmd": "key"})["public_key"], BOX_KEY)

    def test_a_failed_step_leaves_nothing_open(self):
        run = FakeRun()
        run.fail = ["wg", "set"]
        svc, run, timers, clock = service(run)
        reply = svc.handle(dict(CFG, cmd="start", minutes=60))
        self.assertFalse(reply["ok"])
        self.assertIn(["ip", "link", "delete", sd.IFACE], run.calls[-3:])
        self.assertIsNone(svc.session)

    def test_the_key_is_made_once_and_kept_private(self):
        svc, run, timers, clock = service()
        self.assertEqual(svc.handle({"cmd": "key"})["public_key"], BOX_KEY)
        svc.handle({"cmd": "key"})
        self.assertEqual(sum(1 for c in run.calls if c == ["wg", "genkey"]), 1)
        self.assertEqual(oct(os.stat(svc._keyfile()).st_mode & 0o777), "0o600")

    def test_missing_tools_are_reported_not_run(self):
        svc = sd.SupportService(tempfile.mkdtemp(), runner=FakeRun(), log=lambda *_: None, which=lambda name, path=None: None)
        st = svc.handle({"cmd": "status"})
        self.assertFalse(st["available"])
        self.assertIn("wireguard-tools", st["why"])
        self.assertFalse(svc.handle(dict(CFG, cmd="start", minutes=60))["ok"])

    def test_unknown_and_bad_requests(self):
        svc, run, timers, clock = service()
        self.assertFalse(svc.handle({"cmd": "shell"})["ok"])
        self.assertFalse(svc.handle([])["ok"])
        self.assertFalse(svc.handle(dict(CFG, cmd="start", minutes=60, endpoint="x"))["ok"])
        self.assertEqual(run.calls, [])


class FakeHelperClient:
    """The panel's view of the helper, backed by a real SupportService with fake commands."""

    def __init__(self, clock):
        self.svc, self.run, self.timers, _ = service(clock=clock)
        self.messages = []

    def request(self, message):
        self.messages.append(message.get("cmd"))
        return self.svc.handle(json.loads(json.dumps(message)))


class SupportBase(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "Studio laptop"})[1]["token"]
        self.full_dev = self.auth.authenticate(self.full)
        self.live_dev = self.auth.authenticate(self.call("POST", "/api/devices/invite", {"name": "p", "role": "live"}, token=self.full)[1]["token"])
        self.clock = Clock()
        self.api.support = sp.SupportManager(self.settings, self.auth, FakeHelperClient(self.clock), log=lambda *_: None,
                                             clock=self.clock, now=lambda: 1790000000 + self.clock.t, defaults_file="/nonexistent")

    def h(self, method, path, body=None, device=None, client=LAN):
        return self.api.handle(method, path, body or {}, device, client)

    def ready(self):
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)[0], 200)

    def start(self, **kw):
        return self.h("POST", "/api/support/start", dict({"confirm": "start"}, **kw), self.full_dev)


class SessionApiTest(SupportBase):
    def test_off_and_unset_by_default(self):
        st, body = self.h("GET", "/api/support", device=self.full_dev)
        self.assertEqual((st, body["active"], body["config"]["allowed"], body["configured"]), (200, False, False, False))
        self.assertEqual(self.settings.data["support"]["allowed"], False)
        self.assertEqual(self.start()[0], 409)

    def test_a_session_from_start_to_stop(self):
        self.ready()
        self.assertEqual(self.start(confirm="yes")[0], 400)
        st, body = self.start()
        self.assertEqual((st, body["active"], body["role"], body["seconds_left"]), (200, True, "full", 3600))
        code = body["code"]
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
        self.assertTrue(self.h("GET", "/api/status", device=self.live_dev)[1]["support"]["active"])       # everyone's banner
        # support signs in through the tunnel with the code read to them
        st, login = self.h("POST", "/api/support/login", {"code": code.lower().replace("-", " ")}, client=TUNNEL)
        self.assertEqual(st, 200)
        dev = self.api.support.authenticate(login["token"])
        self.assertEqual((dev["name"], dev["role"], dev["remote"]), ("Remote support", "full", True))
        self.assertEqual(self.h("GET", "/api/status", device=dev, client=TUNNEL)[0], 200)
        self.assertEqual(self.h("POST", "/api/control", {"action": "stop"}, dev, TUNNEL)[0], 200)
        # support sees the session but not the code or the settings
        seen = self.h("GET", "/api/support", device=dev, client=TUNNEL)[1]
        self.assertNotIn("code", seen)
        self.assertNotIn("config", seen)
        self.assertNotIn("code", self.h("GET", "/api/support", device=self.live_dev)[1])
        # the studio stops it: support's login ends with it, and the helper closed the tunnel
        self.assertFalse(self.h("POST", "/api/support/stop", device=self.live_dev)[1]["active"])
        self.assertIsNone(self.api.support.authenticate(login["token"]))
        self.assertIsNone(self.api.support.client.svc.session)
        log = self.settings.data["support_log"][0]
        self.assertEqual((log["by"], log["minutes"], log["logins"]), ("Studio laptop", 60, 1))
        self.assertIn("stopped by p", log["reason"])

    def test_what_support_can_never_do_through_the_tunnel(self):
        self.ready()
        code = self.start()[1]["code"]
        dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
        api_routes = sorted(r for r in sp.REMOTE_DENY if r != ("GET", "/api/qr.svg"))      # the QR code: see the cookie test
        for method, path in api_routes + [("POST", "/api/access/code"), ("GET", "/api/access")]:
            st, body = self.h(method, path, {"confirm": "start", "minutes": 60, "allowed": True}, dev, TUNNEL)
            self.assertEqual(st, 403, (method, path, body))
        self.assertTrue(self.api.support.session)                              # and none of that ended it

    def test_guest_codes_stay_refused_through_the_tunnel_whatever_the_role(self):
        """A presenter at the studio may handle the guest code (D47); a support login may not, with either role:
        the whole /api/access path and the QR code are refused in the tunnel, before any role is looked at."""
        self.ready()
        requests = [("GET", "/api/access", {}), ("POST", "/api/access/code", {"role": "view", "minutes": 60}),
                    ("POST", "/api/access/code", {"role": "view", "replace": True}), ("POST", "/api/access/cancel", {"role": "view"}),
                    ("POST", "/api/access/screen", {"show": True, "items": ["view"], "seconds": 60}), ("POST", "/api/access/screen", {"show": False})]
        for role in ("live", "full"):
            code = self.start(role=role)[1]["code"]
            dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
            self.assertEqual((dev["role"], dev.get("remote")), (role, True))
            for method, path, body in requests:
                st, out = self.h(method, path, body, dev, TUNNEL)
                self.assertEqual(st, 403, (role, method, path, out))
                self.assertIn("remote support", out["error"])
                self.assertEqual(self.h(method, path, body, dev, LAN)[0], 403, (role, method, path))     # nor from the studio's network with that login
            with self.assertRaises(sp.SupportApiError):
                self.api.support.guard("GET", "/api/qr.svg", dev, TUNNEL)
            self.assertEqual(self.auth.list_joins(), [])
            self.h("POST", "/api/support/stop", device=self.full_dev)
        # a studio presenter's own token does not work through the tunnel either
        self.start(role="live")
        for method, path, body in requests:
            self.assertEqual(self.h(method, path, body, self.live_dev, TUNNEL)[0], 403, (method, path))
        self.assertEqual(self.auth.list_joins(), [])
        self.assertEqual(self.h("POST", "/api/access/code", {"role": "view", "minutes": 60}, self.live_dev, LAN)[0], 200)      # at the studio it may

    def test_only_the_studio_starts_or_changes_it_and_only_support_logins_work_in_the_tunnel(self):
        self.ready()
        code = self.start()[1]["code"]
        self.assertEqual(self.h("POST", "/api/support/extend", {"minutes": 60}, self.full_dev, TUNNEL)[0], 403)
        self.assertEqual(self.h("POST", "/api/support/config", {"allowed": False}, self.full_dev, TUNNEL)[0], 403)
        self.assertEqual(self.h("GET", "/api/status", device=self.full_dev, client=TUNNEL)[0], 403)    # a studio token over the tunnel
        self.assertEqual(self.h("POST", "/api/support/login", {"code": code}, client=LAN)[0], 403)      # the code only in the tunnel
        token = self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"]
        dev = self.api.support.authenticate(token)
        self.assertEqual(self.h("GET", "/api/status", device=dev, client=LAN)[0], 403)                  # support's login only in the tunnel

    def test_wrong_codes_are_throttled_and_a_code_has_three_logins(self):
        self.ready()
        code = self.start()[1]["code"]
        for _ in range(sp.FAILS):
            self.assertEqual(self.h("POST", "/api/support/login", {"code": "AAAA-AAAA"}, client=TUNNEL)[0], 403)
        self.assertEqual(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[0], 429)   # even the right one now
        self.clock.t += sp.LOCKOUT + 1
        for _ in range(sp.MAX_LOGINS):
            self.assertEqual(self.h("POST", "/api/support/login", {"code": code}, client="10.77.0.10")[0], 200)
        self.assertEqual(self.h("POST", "/api/support/login", {"code": code}, client="10.77.0.10")[0], 403)

    def test_the_session_ends_at_its_deadline_everywhere(self):
        self.ready()
        code = self.start(minutes=15, role="live")[1]["code"]
        token = self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"]
        self.assertEqual(self.api.support.authenticate(token)["role"], "live")
        self.clock.t += 15 * 60
        self.assertIsNone(self.api.support.authenticate(token))
        self.assertFalse(self.h("GET", "/api/status", device=self.live_dev)[1]["support"]["active"])
        self.assertIn("stop", self.api.support.client.messages)
        self.assertIn("ran out", self.settings.data["support_log"][0]["reason"])

    def test_extend_and_limits(self):
        self.ready()
        self.assertEqual(self.h("POST", "/api/support/config", {"max_minutes": 60}, self.full_dev)[0], 200)
        self.assertEqual(self.start(minutes=120)[0], 400)
        self.assertEqual(self.start(minutes=60, role="root")[0], 400)
        self.start(minutes=30)
        self.assertEqual(self.h("POST", "/api/support/config", {"allowed": False}, self.full_dev)[0], 409)   # not during a session
        st, body = self.h("POST", "/api/support/extend", {"minutes": 60}, self.full_dev)
        self.assertEqual((st, body["seconds_left"]), (200, 3600))
        self.assertEqual(self.h("POST", "/api/support/extend", {"minutes": 120}, self.full_dev)[0], 400)

    def test_settings_are_checked(self):
        for bad in ({"endpoint": "nope"}, {"server_key": "x"}, {"address": "10.9.9.9", "network": "10.77.0.0/24"}, {"allowed": "yes"},
                    {"max_minutes": 7}, {"endpoint": 5}):
            self.assertEqual(self.h("POST", "/api/support/config", bad, self.full_dev)[0], 400, bad)
        self.assertEqual(self.h("POST", "/api/support/config", {"allowed": True}, self.live_dev)[0], 403)

    def test_fleet_defaults_fill_in_but_never_allow(self):
        path = os.path.join(tempfile.mkdtemp(), "support.json")
        with open(path, "w") as f:
            json.dump(dict(CFG, allowed=True), f)
        self.api.support.defaults_file = path
        body = self.h("GET", "/api/support", device=self.full_dev)[1]
        self.assertEqual((body["configured"], body["config"]["allowed"], body["config"]["endpoint"]), (True, False, CFG["endpoint"]))

    def test_hello_tells_the_panel_where_the_request_came_from(self):
        self.ready()
        self.assertFalse(self.h("GET", "/api/hello", client=TUNNEL)[1]["remote"])      # no session: nothing is remote
        self.start()
        self.assertTrue(self.h("GET", "/api/hello", client=TUNNEL)[1]["remote"])
        self.assertTrue(self.api.support.is_remote("::ffff:10.77.0.9"))
        self.assertFalse(self.h("GET", "/api/hello")[1]["remote"])

    def test_support_login_cookie_and_the_qr_route(self):
        """Over HTTP, pretending 127.0.0.1 is in the tunnel (the real tunnel is tested on the Pi)."""
        self.ready()
        code = self.start(minutes=15)[1]["code"]
        self.api.support.is_remote = lambda client: True
        st, body, r = self.call("POST", "/api/support/login", {"code": code})
        self.assertEqual(st, 200)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("Max-Age=%d" % (sd.MAX_TOTAL_MINUTES * 60), cookie)          # the box decides; the cookie may outlive it
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=body["token"])[0], 403)       # access codes: never


class OverlapTest(SupportBase):
    """Review finding: a support network equal to the studio's own locked every studio device out for good."""

    def test_a_support_network_on_the_studio_lan_is_refused_and_can_never_lock_the_studio_out(self):
        import ipaddress
        self.api.support.networks_in_use = lambda: [ipaddress.ip_network("10.77.0.0/24")]
        st, body = self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)
        self.assertEqual(st, 400)
        self.assertIn("overlaps", body["error"])
        # even if it got into the settings (the fleet file, or the studio's network changed later) ...
        with self.settings.lock:
            self.settings.data["support"] = dict(sp.blank(), allowed=True, **CFG)
        self.assertEqual(self.h("GET", "/api/status", device=self.full_dev, client=TUNNEL)[0], 200)    # not remote: no session
        st, body = self.start()
        self.assertEqual(st, 409)                                     # ... no session starts on it
        self.assertIn("overlaps", body["error"])

    def test_the_real_interface_list_is_used(self):
        """Found on the Pi: the overlap check was wired to a same-named method of the network settings (pairs, not
        networks) and failed with an internal error. Through the panel's own interface listing this time."""
        self.api.support.networks_in_use = self.api._support_clash_networks
        self.api._ip_json = lambda: [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
                                     {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "10.77.0.20", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.99.0.5", "prefixlen": 24}]}]
        st, body = self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)
        self.assertEqual(st, 400, body)
        self.assertIn("overlaps a network this box is on (10.77.0.0/24)", body["error"])
        ok = dict(CFG, address="10.99.0.5", network="10.99.0.0/24")      # the tunnel's own interface does not count
        self.assertEqual(self.h("POST", "/api/support/config", dict(ok, allowed=True), self.full_dev)[0], 200)

    def test_the_panel_closes_a_tunnel_left_by_an_earlier_run(self):
        """Review finding: after a panel restart the tunnel stayed up with no banner and no Stop."""
        self.ready()
        self.start()
        helper = self.api.support.client
        fresh = sp.SupportManager(self.settings, self.auth, helper, log=lambda *_: None, clock=self.clock,
                                  now=lambda: 1790000000 + self.clock.t, defaults_file="/nonexistent")
        self.assertIsNotNone(helper.svc.session)
        fresh.close_leftover()
        self.assertIsNone(helper.svc.session)

    def test_lifting_the_pin_lockout_is_never_allowed_through_the_tunnel(self):
        self.ready()
        code = self.start()[1]["code"]
        dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
        self.assertEqual(self.h("POST", "/api/pin/unlock", {}, dev, TUNNEL)[0], 403)


class MigrationTest(unittest.TestCase):
    def test_schema_11_gains_remote_support_switched_off(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(path, "w") as f:
            json.dump({"schema": 11, "mapper": {"on": False, "screen": None, "surfaces": [], "sets": {}}}, f)
        data = Settings(path).load()
        self.assertEqual((data["support"]["allowed"], data["support_log"]), (False, []))


if __name__ == "__main__":
    unittest.main()
