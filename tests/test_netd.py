# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import unittest

from pvj import netcfg
from pvj.netcfg import NetError
from pvj.netd import NetdClient, NetServer, NetService


UUID_OLD = "0b3c2f57-7d0a-4a5e-9d6a-1f2e3d4c5b6a"


class FakeNm:
    """A tiny NetworkManager: profiles with properties and uuids, which one is active on eth0, and
    switches to make commands fail (always, or only the first N times) or take time."""

    def __init__(self):
        self.profiles = {"Wired connection 1": {"uuid": UUID_OLD, "ipv4.method": "auto",
                                                "connection.autoconnect": "yes", "connection.autoconnect-priority": "0"}}
        self.active = "Wired connection 1"
        self.wifi_active = None          # the profile active on wlan0 (profiles with "_dev": "wlan0")
        self.radio_hw, self.radio = "enabled", "enabled"
        self.scan_text = ""
        self.hang_on = None              # a command that times out
        self.calls = []
        self.fail_on = None
        self.fail_times = None
        self.on_up = None
        self.addrs = [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
                      {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]
        self._n = 0

    def nmcli_calls(self):
        return [c for c in self.calls if c[0] == "nmcli"]

    def _by_uuid(self, uuid):
        return next((n for n, p in self.profiles.items() if p.get("uuid") == uuid), None)

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        line = " ".join(argv)
        done = lambda rc=0, out="", err="": subprocess.CompletedProcess(argv, rc, out, err)
        if self.fail_on and self.fail_on in line and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            return done(4, "", "Error: connection activation failed")
        if argv[0] == "ip":
            return done(0, json.dumps(self.addrs))
        if argv[:5] == ["nmcli", "-t", "-f", "UUID,DEVICE", "connection"]:
            out = ""
            if self.active in self.profiles:
                out += "%s:eth0\n" % self.profiles[self.active]["uuid"]
            if self.wifi_active in self.profiles:
                out += "%s:wlan0\n" % self.profiles[self.wifi_active]["uuid"]
            return done(0, out)
        if argv[1:5] == ["-t", "-f", "WIFI-HW,WIFI", "radio"]:
            return done(0, "%s:%s\n" % (self.radio_hw, self.radio))
        if argv[1:3] == ["radio", "wifi"]:
            self.radio = "enabled" if argv[3] == "on" else "disabled"
            if argv[3] == "off":
                self.wifi_active = None
            return done()
        if argv[1:7] == ["-t", "-f", "SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list"]:
            return done(0, self.scan_text)
        if argv[1:4] == ["-t", "-g", "802-11-wireless.ssid,802-11-wireless.mode"]:
            name = self._by_uuid(argv[-1])
            p = self.profiles.get(name, {})
            return done(0, "%s\n%s\n" % (p.get("_ssid", ""), p.get("_mode", ""))) if name else done(10)
        if self.hang_on and self.hang_on in line:
            raise subprocess.TimeoutExpired(argv, 10)
        if argv[1:3] == ["device", "disconnect"]:
            self.wifi_active = None
            return done()
        if argv[1:4] == ["-t", "-f", "connection.id"]:
            return done(0 if argv[-1] in self.profiles else 10, argv[-1] + "\n" if argv[-1] in self.profiles else "")
        verb = argv[2]
        if verb == "load":
            path = argv[3]
            st = os.lstat(path)
            if st.st_mode & 0o077:
                return done(1, "", "Error: file is readable by others")
            kf = {}
            section = None
            with open(path) as f:
                for line in f.read().splitlines():
                    if line.startswith("["):
                        section = line.strip("[]")
                    elif "=" in line:
                        k, v = line.split("=", 1)
                        kf["%s.%s" % (section, k)] = v
            # GKeyFile string escapes, as NetworkManager reads them
            kf["_psk"] = re.sub(r"\\(.)", lambda m: {"s": " ", "\\": "\\"}.get(m.group(1), "?"), kf.get("wifi-security.psk", ""))
            ssid = bytes(int(b) for b in kf["wifi.ssid"].rstrip(";").split(";")).decode()
            self.profiles[kf["connection.id"]] = {"uuid": kf["connection.uuid"], "_dev": "wlan0", "_keyfile": path,
                                                  "_ssid": ssid, "_mode": kf["wifi.mode"], "_kf": kf,
                                                  "connection.autoconnect": "no"}
            return done()
        if verb == "add":
            self._n += 1
            props = dict(zip(argv[9::2], argv[10::2]))
            props["uuid"] = "00000000-0000-4000-8000-%012d" % self._n
            self.profiles[argv[argv.index("con-name") + 1]] = props
        elif verb == "modify":
            name = argv[4]
            if name not in self.profiles:
                return done(10, "", "Error: unknown connection")
            props = dict(zip(argv[5::2], argv[6::2]))
            new_id = props.pop("connection.id", None)
            self.profiles[name].update(props)
            if new_id:
                self.profiles[new_id] = self.profiles.pop(name)
                if self.active == name:
                    self.active = new_id
                if self.wifi_active == name:
                    self.wifi_active = new_id
        elif verb == "up":
            name = self._by_uuid(argv[4]) if argv[3] == "uuid" else argv[4]
            if name not in self.profiles:
                return done(10, "", "Error: unknown connection")
            if self.on_up:
                self.on_up()
            if self.profiles[name].get("_dev") == "wlan0":
                self.wifi_active = name
            else:
                self.active = name
        elif verb == "down":
            if self.active == argv[4]:
                self.active = None
            if self.wifi_active == argv[4]:
                self.wifi_active = None
        elif verb == "delete":
            if argv[4] not in self.profiles:
                return done(10, "", "Error: unknown connection")
            gone = self.profiles.pop(argv[4])
            if gone.get("_keyfile"):
                os.unlink(gone["_keyfile"])   # NetworkManager removes the file a profile came from
            if self.active == argv[4]:
                self.active = None
            if self.wifi_active == argv[4]:
                self.wifi_active = None
        return done()


def make_sysfs():
    root = tempfile.mkdtemp()
    for name, wireless in (("eth0", False), ("wlan0", True)):
        d = os.path.join(root, name)
        os.makedirs(d)
        for fn, text in (("address", "aa:bb"), ("operstate", "up"), ("carrier", "1"), ("speed", "1000")):
            with open(os.path.join(d, fn), "w") as f:
                f.write(text)
        if wireless:
            os.makedirs(os.path.join(d, "wireless"))
    return root


STATIC = {"iface": "eth0", "mode": "static", "address": "192.168.50.20", "prefix": 24, "gateway": "192.168.50.1",
          "dns": ["1.1.1.1"]}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.now = [1000.0]
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.state = os.path.join(self.dir, "net-pending.json")
        self.logs = []
        self.sysfs = make_sysfs()
        self.svc = self.make_service()

    def make_service(self):
        return NetService(runner=self.nm, clock=lambda: self.now[0], sysfs=self.sysfs, state_dir=self.dir,
                          log=self.logs.append)

    # --- the basic flow -------------------------------------------------------
    def test_apply_builds_a_candidate_and_leaves_the_confirmed_setup_alone(self):
        before = dict(self.nm.profiles["Wired connection 1"])
        st = self.svc.apply(dict(STATIC))
        self.assertEqual((st["pending"]["iface"], st["pending"]["seconds_left"]), ("eth0", 60))
        cand = self.nm.profiles["pvj-eth0-try"]
        self.assertEqual((cand["ipv4.method"], cand["ipv4.addresses"], cand["connection.autoconnect"]),
                         ("manual", "192.168.50.20/24", "no"))
        self.assertEqual(self.nm.active, "pvj-eth0-try")
        self.assertEqual(self.nm.profiles["Wired connection 1"], before)
        self.assertNotIn("pvj-eth0", self.nm.profiles)
        self.assertTrue(os.path.exists(self.state))

    def test_confirm_makes_the_candidate_the_permanent_profile(self):
        self.svc.apply(dict(STATIC))
        self.assertIsNone(self.svc.confirm()["pending"])
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        final = self.nm.profiles["pvj-eth0"]
        self.assertEqual((final["connection.autoconnect"], final["connection.autoconnect-priority"], final["ipv4.method"]),
                         ("yes", "101", "manual"))
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.assertFalse(os.path.exists(self.state))
        self.now[0] += 1000
        self.assertFalse(self.svc.tick())

    def test_a_second_confirmed_change_replaces_the_first(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        self.svc.apply({"iface": "eth0", "mode": "linklocal"})
        self.svc.confirm()
        self.assertEqual(self.nm.profiles["pvj-eth0"]["ipv4.method"], "link-local")
        self.assertEqual([n for n in self.nm.profiles if n.startswith("pvj-")], ["pvj-eth0"])

    def test_unconfirmed_change_reverts_at_the_deadline(self):
        self.svc.apply(dict(STATIC, revert_seconds=30))
        self.now[0] += 29
        self.assertFalse(self.svc.tick())
        self.now[0] += 2
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])
        self.assertFalse(os.path.exists(self.state))

    # --- review finding 2 and 5: an existing confirmed profile, and what to go back to -------------
    def test_reverting_a_change_to_a_confirmed_profile_restores_it_exactly_and_reactivates_it(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        confirmed = dict(self.nm.profiles["pvj-eth0"])
        self.svc.apply({"iface": "eth0", "mode": "linklocal"})
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)  # never edited in place
        self.svc.revert()
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)
        self.assertEqual(self.nm.active, "pvj-eth0")  # and it is the one in use again

    def test_power_cut_while_pending_keeps_the_old_network_and_the_restart_undoes_the_candidate(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        confirmed = dict(self.nm.profiles["pvj-eth0"])
        self.svc.apply({"iface": "eth0", "mode": "dhcp"})
        # ---- the box loses power here. What is on disk:
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)  # old profile untouched, still autoconnect
        self.assertEqual(self.nm.profiles["pvj-eth0-try"]["connection.autoconnect"], "no")
        # ---- boot: the helper starts and finds the saved state
        reborn = self.make_service()
        self.assertTrue(reborn.recover())
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(reborn.recover())

    # --- review finding 3: an undo that fails must not claim success -----------------------------------
    def test_a_failing_undo_is_retried_until_it_works_and_only_then_reported(self):
        self.svc.apply(dict(STATIC))
        self.nm.fail_on, self.nm.fail_times = "connection up uuid", 2
        st = self.svc.revert()
        self.assertTrue(st["reverting"])
        self.assertEqual(self.nm.active, None)  # candidate gone, old one not back yet
        self.assertTrue(os.path.exists(self.state))  # kept until it really worked
        self.assertFalse(self.svc.tick())  # too soon
        self.now[0] += 3
        self.assertFalse(self.svc.tick())  # second try fails too
        self.assertTrue(self.svc.status()["reverting"])
        self.now[0] += 3
        self.assertTrue(self.svc.tick())  # third succeeds
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertFalse(self.svc.status()["reverting"])
        self.assertFalse(os.path.exists(self.state))

    def test_an_undo_that_keeps_failing_is_never_dropped_and_blocks_new_changes(self):
        self.svc.apply(dict(STATIC))
        self.nm.fail_on, self.nm.fail_times = "connection up uuid", None  # always fails
        self.svc.revert()
        for _ in range(60):
            self.now[0] += 3
            self.svc.tick()
        self.assertTrue(any("STILL FAILING" in m for m in self.logs))
        self.assertTrue(self.svc.status()["reverting"])        # still owed, now tried once a minute
        self.assertTrue(os.path.exists(self.state))
        with self.assertRaises(NetError):                       # found by review: a new change overwrote what was owed
            self.svc.apply(dict(STATIC, address="192.168.50.21"))
        with open(self.state) as f:
            self.assertEqual(json.load(f)["previous_uuid"], UUID_OLD)
        calls = len(self.nm.calls)
        self.now[0] += 30
        self.svc.tick()
        self.assertEqual(len(self.nm.calls), calls)            # not yet: once a minute
        self.nm.fail_on = None
        self.now[0] += 31
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertFalse(os.path.exists(self.state))

    def test_a_restart_finishes_an_undo_that_was_still_failing(self):
        self.svc.apply(dict(STATIC))
        self.nm.fail_on, self.nm.fail_times = "connection up uuid", None
        self.svc.revert()
        self.assertTrue(os.path.exists(self.state))
        self.nm.fail_on = None
        reborn = self.make_service()
        self.assertTrue(reborn.recover())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertFalse(os.path.exists(self.state))

    def test_a_failed_apply_undoes_itself_and_says_how_it_went(self):
        self.nm.fail_on = "connection up id pvj-eth0-try"
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC))
        self.assertIn("previous setup was restored", str(cm.exception))
        self.nm.fail_on = None
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])

    def test_a_failed_apply_whose_undo_also_fails_does_not_claim_a_restore(self):
        self.nm.fail_on, self.nm.fail_times = "connection up", 2  # the new one and the first undo attempt fail
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC))
        self.assertIn("still being retried", str(cm.exception))
        self.assertTrue(self.svc.status()["reverting"])

    # --- review finding 4: the countdown starts when the new network is up ------------------------------------
    def test_a_slow_activation_does_not_eat_the_confirm_window(self):
        self.nm.on_up = lambda: self.now.__setitem__(0, self.now[0] + 25)  # DHCP takes 25 s to answer
        st = self.svc.apply(dict(STATIC, revert_seconds=20))
        self.assertEqual(st["pending"]["seconds_left"], 20)
        self.assertFalse(self.svc.tick())
        self.now[0] += 21
        self.assertTrue(self.svc.tick())

    # --- guard rails ----------------------------------------------------------------------------------------------
    def test_only_one_change_at_a_time_and_nothing_to_confirm_or_revert_when_idle(self):
        self.svc.apply(dict(STATIC))
        with self.assertRaises(NetError):
            self.svc.apply({"iface": "eth0", "mode": "dhcp"})
        fresh = NetService(runner=self.nm, sysfs=self.sysfs)
        for op in (fresh.confirm, fresh.revert):
            with self.assertRaises(NetError):
                op()

    def test_invalid_requests_run_no_nmcli_commands(self):
        for bad in ({"iface": "wlan0", "mode": "dhcp"}, {"iface": "eth0", "mode": "static", "address": "8.8.8.8; reboot",
                                                        "prefix": 24}, {"iface": "eth0; reboot", "mode": "dhcp"}, "x", None):
            with self.assertRaises(NetError):
                self.svc.apply(bad)
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_a_range_that_overlaps_another_port_is_refused_using_the_kernels_addresses(self):
        self.nm.addrs.append({"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.5.0.2", "prefixlen": 24}]})
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC, address="10.5.0.9", gateway="10.5.0.1"))
        self.assertIn("wlan0", str(cm.exception))
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_plan_is_a_dry_run(self):
        out = self.svc.plan(dict(STATIC))
        self.assertTrue(any("ipv4.addresses 192.168.50.20/24" in c for c in out["commands"]))
        self.assertEqual(self.nm.nmcli_calls(), [])
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)

    def test_missing_nmcli_is_a_clear_error(self):
        def gone(argv, **kw):
            raise FileNotFoundError()
        with self.assertRaises(NetError) as cm:
            NetService(runner=gone, sysfs=self.sysfs).apply(dict(STATIC))
        self.assertIn("not installed", str(cm.exception))

    def test_handle_returns_errors_as_data(self):
        self.assertFalse(self.svc.handle({"cmd": "nope"})["ok"])
        self.assertFalse(self.svc.handle([1])["ok"])
        self.assertTrue(self.svc.handle({"cmd": "status"})["ok"])
        self.assertFalse(self.svc.handle({"cmd": "apply", "config": {"iface": "x"}})["ok"])

    # --- review finding 1: the saved state is never trusted --------------------------------------------------
    def test_planted_or_damaged_state_is_discarded_without_running_anything(self):
        bad_states = ['{not json', '[1]', '"x"', '{}', json.dumps({"iface": "eth0; reboot", "previous_uuid": None}),
                      json.dumps({"iface": "eth0", "previous_uuid": "--ask"}), json.dumps({"iface": "eth0", "previous_uuid": 5}),
                      json.dumps({"iface": "../eth0", "previous_uuid": None}), json.dumps({"iface": None}),
                      json.dumps({"cfg": "x", "iface": ["a"], "previous_uuid": None})]
        for text in bad_states:
            with open(self.state, "w") as f:
                f.write(text)
            self.assertFalse(self.svc.recover(), text)
            self.assertFalse(os.path.exists(self.state), text)
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_a_planted_valid_state_can_only_trigger_the_fixed_undo_commands(self):
        with open(self.state, "w") as f:
            json.dump({"iface": "eth0", "previous_uuid": UUID_OLD, "cfg": {"iface": "eth0", "gateway": "6.6.6.6"},
                       "extra": "ignored"}, f)
        self.assertTrue(self.svc.recover())
        verbs = {tuple(c[1:4]) for c in self.nm.nmcli_calls()}
        self.assertLessEqual(verbs, {("connection", "up", "uuid"), ("-t", "-f", "connection.id")})

    def test_the_temp_file_is_never_followed_through_a_link(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("do not touch")
        os.symlink(victim, self.state + ".tmp")
        self.svc.apply(dict(STATIC))
        with open(victim) as f:
            self.assertEqual(f.read(), "do not touch")
        with open(self.state) as f:
            self.assertEqual(json.load(f)["phase"], "pending")

    def test_the_state_file_holds_only_an_interface_and_a_connection_id(self):
        self.svc.apply(dict(STATIC))
        with open(self.state) as f:
            data = json.load(f)
        self.assertEqual(set(data), {"phase", "iface", "previous_uuid", "radio_was_off", "keyfile"})
        self.assertEqual((data["radio_was_off"], data["keyfile"]), (False, None))
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), "0o600")

    def test_state_directory_must_be_private(self):
        from pvj import netd
        good = tempfile.mkdtemp()
        os.chmod(good, 0o700)
        loose = tempfile.mkdtemp()
        os.chmod(loose, 0o770)
        old = os.environ.get("STATE_DIRECTORY")
        self.addCleanup(lambda: os.environ.__setitem__("STATE_DIRECTORY", old) if old else os.environ.pop("STATE_DIRECTORY", None))
        os.environ["STATE_DIRECTORY"] = good
        self.assertEqual(netd.safe_state_dir(), good)
        os.environ["STATE_DIRECTORY"] = loose
        self.assertIsNone(netd.safe_state_dir())


UUID_WIFI = "5d0c2f57-7d0a-4a5e-9d6a-1f2e3d4c5b6a"
JOIN = {"iface": "wlan0", "mode": "dhcp", "ssid": "Leyline Staff", "password": "s3cret pass", "revert_seconds": 120}


class WifiServiceTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.nm.profiles["Home"] = {"uuid": UUID_WIFI, "_dev": "wlan0", "_ssid": "Home", "_mode": "infrastructure"}
        self.nm.wifi_active = "Home"
        self.now = [1000.0]
        self.dir = tempfile.mkdtemp()
        self.kdir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        os.chmod(self.kdir, 0o700)
        self.state = os.path.join(self.dir, "net-pending.json")
        self.logs = []
        self.sysfs = make_sysfs()
        self.tokens = iter(["0000aaaa", "0000bbbb", "0000cccc", "0000dddd"])
        self.svc = self.make_service()

    def make_service(self):
        return NetService(runner=self.nm, clock=lambda: self.now[0], sysfs=self.sysfs, state_dir=self.dir,
                          log=self.logs.append, keyfile_dir=self.kdir, new_token=lambda: next(self.tokens))

    def keyfiles(self):
        return sorted(os.listdir(self.kdir))

    def assert_no_password_anywhere(self, *extra):
        everything = repr(self.nm.calls) + repr(self.logs) + repr(extra) + repr(self.svc.status(wifi=True))
        if os.path.exists(self.state):
            with open(self.state) as f:
                everything += f.read()
        self.assertNotIn("s3cret", everything)

    def test_joining_writes_a_root_only_keyfile_and_brings_the_candidate_up(self):
        reply = self.svc.handle({"cmd": "apply", "config": dict(JOIN)})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["pending"]["ssid"], "Leyline Staff")
        self.assertEqual(self.keyfiles(), ["pvj-wlan0-try-0000aaaa.nmconnection"])
        path = os.path.join(self.kdir, self.keyfiles()[0])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        cand = self.nm.profiles["pvj-wlan0-try"]
        self.assertEqual((cand["_ssid"], cand["_kf"]["wifi-security.psk"], cand["ipv4.method"], cand["connection.autoconnect"]),
                         ("Leyline Staff", "s3cret\\spass", "auto", "no"))
        self.assertEqual(self.nm.wifi_active, "pvj-wlan0-try")
        self.assertEqual(self.nm.active, "Wired connection 1")      # the wired port is not touched
        self.assert_no_password_anywhere(reply)

    def test_confirm_keeps_the_new_network(self):
        self.svc.apply(dict(JOIN))
        self.svc.confirm()
        self.assertEqual(self.nm.wifi_active, "pvj-wlan0")
        self.assertEqual(self.nm.profiles["pvj-wlan0"]["connection.autoconnect"], "yes")
        self.assertIn("Home", self.nm.profiles)
        self.assertFalse(os.path.exists(self.state))
        # a second change replaces the first: the old confirmed profile and its file go
        self.svc.apply(dict(JOIN, ssid="Other"))
        self.svc.confirm()
        self.assertEqual(self.nm.profiles["pvj-wlan0"]["_ssid"], "Other")
        self.assertEqual(self.keyfiles(), ["pvj-wlan0-try-0000bbbb.nmconnection"])

    def test_no_confirmation_goes_back_to_the_previous_network_and_removes_the_keyfile(self):
        self.svc.apply(dict(JOIN))
        self.now[0] += 121
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.wifi_active, "Home")
        self.assertNotIn("pvj-wlan0-try", self.nm.profiles)
        self.assertEqual(self.keyfiles(), [])
        self.assertFalse(os.path.exists(self.state))
        self.assert_no_password_anywhere()

    def test_a_radio_that_was_off_is_switched_on_and_back_off(self):
        self.nm.radio, self.nm.wifi_active = "disabled", None
        self.svc.apply(dict(JOIN))
        self.assertEqual(self.nm.radio, "enabled")
        self.svc.revert()
        self.assertEqual(self.nm.radio, "disabled")
        self.assertEqual(self.keyfiles(), [])

    def test_a_restart_while_pending_undoes_everything(self):
        self.nm.radio, self.nm.wifi_active = "disabled", None
        self.svc.apply(dict(JOIN))
        with open(self.state) as f:
            self.assertEqual(json.load(f)["keyfile"], "0000aaaa")
        fresh = self.make_service()          # pvj-netd restarted (or the box rebooted)
        self.assertTrue(fresh.recover())
        self.assertNotIn("pvj-wlan0-try", self.nm.profiles)
        self.assertEqual((self.nm.radio, self.keyfiles()), ("disabled", []))

    def test_a_keyfile_written_but_never_loaded_is_removed(self):
        self.nm.fail_on = "connection load"
        with self.assertRaises(NetError):
            self.svc.apply(dict(JOIN))
        self.assertEqual(self.keyfiles(), [])
        self.assertEqual(self.nm.wifi_active, "Home")
        self.assertIsNone(self.svc.status()["pending"])

    def test_a_failed_connection_goes_back(self):
        self.nm.fail_on = "connection up id pvj-wlan0-try"
        with self.assertRaises(NetError):
            self.svc.apply(dict(JOIN))
        self.assertEqual((self.nm.wifi_active, self.keyfiles()), ("Home", []))
        self.assertNotIn("pvj-wlan0-try", self.nm.profiles)

    def test_hotspot(self):
        self.svc.apply({"iface": "wlan0", "mode": "hotspot", "ssid": "NXLX", "password": "s3cret pass", "band": "a"})
        cand = self.nm.profiles["pvj-wlan0-try"]
        self.assertEqual((cand["_mode"], cand["_kf"]["wifi.band"], cand["ipv4.method"], cand["ipv4.addresses"]),
                         ("ap", "a", "shared", "10.43.0.1/24"))
        self.svc.confirm()
        st = self.svc.status(wifi=True)
        self.assertEqual(st["wifi"]["ports"]["wlan0"], {"ssid": "NXLX", "hotspot": True})
        self.assert_no_password_anywhere()

    def test_wifi_off_disconnects_and_the_radio_goes_off_on_confirm(self):
        self.svc.apply({"iface": "wlan0", "mode": "off"})
        self.assertIsNone(self.nm.wifi_active)
        self.assertEqual(self.nm.radio, "enabled")       # still undoable
        self.svc.revert()
        self.assertEqual(self.nm.wifi_active, "Home")
        self.svc.apply({"iface": "wlan0", "mode": "off"})
        self.svc.confirm()
        self.assertEqual(self.nm.radio, "disabled")
        self.assertIn("Home", self.nm.profiles)           # saved networks are kept
        with self.assertRaises(NetError):
            self.svc.apply({"iface": "wlan0", "mode": "off"})

    def test_blocked_wifi_is_refused_before_anything_runs(self):
        self.nm.radio_hw = "disabled"
        n = len(self.nm.calls)
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(JOIN))
        self.assertIn("blocked", str(cm.exception))
        self.assertFalse([c for c in self.nm.calls[n:] if c[0] == "nmcli" and c[1] != "-t"])   # only questions were asked
        self.assertEqual(self.keyfiles(), [])
        self.assertIsNone(self.svc.pending)

    def test_an_unsafe_profile_folder_is_refused(self):
        os.chmod(self.kdir, 0o770)
        with self.assertRaises(NetError):
            self.svc.apply(dict(JOIN))
        self.assertEqual(self.nm.wifi_active, "Home")
        os.chmod(self.kdir, 0o700)
        link = os.path.join(self.dir, "linked")
        os.symlink(self.kdir, link)
        self.svc.keyfile_dir = link
        with self.assertRaises(NetError):
            self.svc.apply(dict(JOIN))

    def test_a_planted_file_is_never_followed_or_overwritten(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("do not touch")
        os.symlink(victim, os.path.join(self.kdir, "pvj-wlan0-try-0000aaaa.nmconnection"))
        with self.assertRaises(NetError):
            self.svc.apply(dict(JOIN))
        with open(victim) as f:
            self.assertEqual(f.read(), "do not touch")

    def test_saved_state_with_an_odd_keyfile_name_is_ignored(self):
        for bad in ("../../etc/passwd", "ABCDEF01", 5, "0000aaaa\n"):
            with open(self.state, "w") as f:
                json.dump({"phase": "pending", "iface": "wlan0", "previous_uuid": None, "keyfile": bad}, f)
            self.assertFalse(self.make_service().recover(), bad)
        with open(self.state, "w") as f:
            json.dump({"phase": "pending", "iface": "wlan0", "previous_uuid": None, "radio_was_off": "yes"}, f)
        self.assertFalse(self.make_service().recover())

    def test_plan_shows_commands_but_never_the_password(self):
        reply = self.svc.handle({"cmd": "plan", "config": dict(JOIN)})
        self.assertTrue(reply["ok"])
        self.assertNotIn("password", reply["config"])
        self.assertNotIn("s3cret", json.dumps(reply))
        self.assertEqual(self.keyfiles(), [])
        self.assertEqual(self.nm.wifi_active, "Home")

    def test_scan(self):
        self.nm.scan_text = "Home:70:WPA2:6\nLeyline Staff:60:WPA2 WPA3:36\nGuest:20::1\n"
        reply = self.svc.handle({"cmd": "scan", "iface": "wlan0"})
        self.assertEqual([n["ssid"] for n in reply["networks"]], ["Home", "Leyline Staff", "Guest"])
        for bad in ("eth0", "wlan9", None, "wlan0; reboot"):
            self.assertFalse(self.svc.handle({"cmd": "scan", "iface": bad})["ok"], bad)
        self.nm.radio = "disabled"
        self.assertIn("off", self.svc.handle({"cmd": "scan", "iface": "wlan0"})["error"])

    def test_a_timeout_during_an_undo_is_a_failed_try_not_the_end_of_the_undo(self):
        # found by review: one slow nmcli made the helper drop the undo and delete its state for good
        self.svc.apply(dict(JOIN))
        self.nm.hang_on = "connection.id connection show id pvj-wlan0-try"
        self.now[0] += 121
        self.assertFalse(self.svc.tick())
        self.assertTrue(self.svc.status()["reverting"])
        self.assertTrue(os.path.exists(self.state))
        self.nm.hang_on = None
        self.now[0] += 3
        self.assertTrue(self.svc.tick())
        self.assertEqual((self.nm.wifi_active, self.keyfiles()), ("Home", []))

    def test_a_confirm_that_fails_part_way_can_still_be_undone(self):
        # found by review: the old profile was deleted before the new one had its name, so the undo had nothing to go back to
        self.svc.apply(dict(JOIN))
        self.svc.confirm()                                   # pvj-wlan0 is now "Leyline Staff"
        self.svc.apply(dict(JOIN, ssid="Other"))
        self.nm.fail_on = "modify id pvj-wlan0-try connection.id pvj-wlan0"
        with self.assertRaises(NetError):
            self.svc.confirm()
        self.assertEqual(self.nm.profiles["pvj-wlan0"]["_ssid"], "Leyline Staff")   # renamed back
        self.assertNotIn("pvj-wlan0-old", self.nm.profiles)
        self.nm.fail_on = None
        self.now[0] += 121
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.wifi_active, "pvj-wlan0")
        self.assertEqual(self.nm.profiles["pvj-wlan0"]["_ssid"], "Leyline Staff")

    def test_an_old_profile_that_cannot_be_deleted_does_not_undo_a_confirm(self):
        self.svc.apply(dict(JOIN))
        self.svc.confirm()
        self.svc.apply(dict(JOIN, ssid="Other"))
        self.nm.fail_on = "delete id pvj-wlan0-old"
        self.assertIsNone(self.svc.confirm()["pending"])
        self.assertEqual((self.nm.wifi_active, self.nm.profiles["pvj-wlan0"]["_ssid"]), ("pvj-wlan0", "Other"))
        self.assertTrue(any("could not delete the old profile" in m for m in self.logs))
        self.nm.fail_on = None
        self.svc.apply(dict(JOIN, ssid="Third"))
        self.svc.confirm()                                   # the leftover goes now
        self.assertNotIn("pvj-wlan0-old", self.nm.profiles)
        self.assertEqual(self.nm.profiles["pvj-wlan0"]["_ssid"], "Third")

    def test_the_password_reaches_networkmanager_exactly(self):
        for pw in ("s3cret pass", " lead and trail ", "back\\slash\\\\s", "#;[x]=y \\ "):
            self.svc.apply(dict(JOIN, password=pw))
            self.assertEqual(self.nm.profiles["pvj-wlan0-try"]["_kf"]["_psk"], pw)
            self.svc.revert()

    def test_scan_marks_in_use_from_the_box_not_from_the_air(self):
        # a name with a line break inside could look like a record of its own; "in use" never comes from the list
        self.nm.scan_text = "Leyline Staff:60:WPA2:36\nHome:70:WPA2:6\n"
        got = {n["ssid"]: n["in_use"] for n in self.svc.scan("wlan0")["networks"]}
        self.assertEqual(got, {"Home": True, "Leyline Staff": False})
        self.nm.scan_text = "x\n*:Leyline Staff:99::1\n"
        self.assertEqual(self.svc.scan("wlan0")["networks"], [])

    def test_a_network_name_with_a_backslash_or_colon_is_shown_as_it_is(self):
        self.nm.profiles["Home"]["_ssid"] = "a\\\\b\\:c"            # as nmcli -g prints a\b:c
        self.assertEqual(self.svc.status(wifi=True)["wifi"]["ports"]["wlan0"]["ssid"], "a\\b:c")

    def test_joining_waits_longer_than_a_cable(self):
        seen = []
        real = self.nm.__call__
        self.svc.runner = lambda argv, **kw: (seen.append((argv[:3], kw.get("timeout"))), real(argv, **kw))[1]
        self.svc.apply(dict(JOIN))
        self.assertIn((["nmcli", "connection", "up"], 45), seen)

    def test_status_reports_what_wifi_is_doing(self):
        st = self.svc.handle({"cmd": "status", "wifi": True})
        self.assertEqual(st["wifi"], {"hardware": True, "radio": True, "ports": {"wlan0": {"ssid": "Home", "hotspot": False}}})
        self.assertNotIn("wifi", self.svc.handle({"cmd": "status"}))


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.svc = NetService(runner=self.nm, sysfs=make_sysfs())
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "netd.sock")
        self.allowed = True
        self.server = NetServer(self.path, self.svc, lambda uid: self.allowed)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = NetdClient(self.path, timeout=5)

    def test_round_trip_and_socket_is_group_only(self):
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o660")
        st = self.client.request({"cmd": "status"})
        self.assertTrue(st["ok"])
        self.assertEqual([i["name"] for i in st["interfaces"]], ["eth0", "wlan0"])
        r = self.client.request({"cmd": "apply", "config": dict(STATIC)})
        self.assertEqual(r["pending"]["iface"], "eth0")
        self.assertTrue(self.client.request({"cmd": "confirm"})["ok"])

    def test_disallowed_caller_is_refused_before_anything_is_read(self):
        self.allowed = False
        self.assertEqual(self.client.request({"cmd": "status"}), {"ok": False, "error": "not allowed"})
        self.assertEqual(self.nm.calls, [])

    def test_garbage_and_oversized_requests(self):
        for raw in (b"not json\n", b"[1,2]\n", b"[" * 3000 + b"\n", b"x" * 5000 + b"\n"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(self.path)
            s.sendall(raw)
            reply = json.loads(s.recv(4096))
            s.close()
            self.assertFalse(reply["ok"], raw[:20])
        self.assertEqual(self.nm.calls, [])
        self.assertTrue(self.client.request({"cmd": "status"})["ok"])  # still serving

    def test_a_stalled_client_does_not_block_others(self):
        stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stalled.connect(self.path)
        self.addCleanup(stalled.close)
        import time as t
        started = t.time()
        self.assertTrue(self.client.request({"cmd": "status"})["ok"])
        self.assertLess(t.time() - started, 7)  # waited at most the 5 s per-connection timeout

    def test_no_daemon_is_a_clear_error(self):
        with self.assertRaises(NetError):
            NetdClient(os.path.join(self.dir, "missing.sock"), timeout=1).request({"cmd": "status"})


if __name__ == "__main__":
    unittest.main()
