# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import ipaddress
import os
import tempfile
import unittest

from pvj import netcfg
from pvj.netcfg import NetError

WIRED = {"name": "eth0", "kind": "wired", "state": "up", "carrier": True, "mac": "aa", "speed_mbps": 1000}
WIFI = {"name": "wlan0", "kind": "wifi", "state": "up", "carrier": True, "mac": "bb", "speed_mbps": None}
IFACES = [WIRED, WIFI]


def ok(**kw):
    return netcfg.validate(dict({"iface": "eth0", "mode": "dhcp"}, **kw), IFACES)


class ValidateTest(unittest.TestCase):
    def test_static_config_is_normalised(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24, gateway="192.168.50.1", dns=["1.1.1.1", "8.8.8.8"])
        self.assertEqual(cfg, {"iface": "eth0", "mode": "static", "revert_seconds": 60, "address": "192.168.50.20",
                               "prefix": 24, "gateway": "192.168.50.1", "dns": ["1.1.1.1", "8.8.8.8"]})

    def test_simple_modes_take_no_address(self):
        for mode in ("dhcp", "linklocal"):
            self.assertEqual(set(ok(mode=mode)), {"iface", "mode", "revert_seconds"})

    def test_share_defaults_and_private_only(self):
        self.assertEqual((ok(mode="share")["address"], ok(mode="share")["prefix"]), ("10.42.0.1", 24))
        self.assertEqual(ok(mode="share", address="192.168.7.1")["address"], "192.168.7.1")
        with self.assertRaises(NetError):
            ok(mode="share", address="8.8.8.8")

    def test_bad_addresses_are_refused(self):
        base = dict(mode="static", prefix=24)
        for addr in ("", None, 5, "192.168.1", "192.168.1.256", "192.168.001.5", "0.0.0.0", "255.255.255.255",
                     "127.0.0.1", "224.0.0.1", "169.254.1.1", "240.0.0.1", "192.168.1.0", "192.168.1.255",
                     "192.168.1.5; reboot", "::1", " 192.168.1.5", "192.168.1.5\n"):
            with self.assertRaises(NetError, msg=repr(addr)):
                ok(address=addr, **base)

    def test_bad_prefix_gateway_dns_and_revert(self):
        base = dict(mode="static", address="192.168.1.20")
        for prefix in (None, 7, 31, 32, "24", True, 24.0):
            with self.assertRaises(NetError, msg=repr(prefix)):
                ok(prefix=prefix, **base)
        for gw in ("192.168.2.1", "192.168.1.20", "192.168.1.0", "192.168.1.255", "x", 5):
            with self.assertRaises(NetError, msg=repr(gw)):
                ok(prefix=24, gateway=gw, **base)
        for dns in ("1.1.1.1", ["1.1.1.1"] * 4, ["nope"], ["0.0.0.0"], ["127.0.0.1"], [5]):
            with self.assertRaises(NetError, msg=repr(dns)):
                ok(prefix=24, dns=dns, **base)
        for sec in (5, 301, True, "60", 60.5):
            with self.assertRaises(NetError, msg=repr(sec)):
                ok(revert_seconds=sec)

    def test_review_findings_on_ranges(self):
        for addr in ("0.1.2.3", "0.255.0.1"):
            for mode in ("static", "share"):
                with self.assertRaises(NetError, msg=addr + mode):
                    ok(mode=mode, address=addr, prefix=24)
        for addr in ("192.0.2.1", "198.18.0.1", "192.0.0.5", "100.64.0.1", "172.32.0.1", "11.0.0.1"):
            with self.assertRaises(NetError, msg=addr):  # a served pool must be RFC 1918
                ok(mode="share", address=addr, prefix=24)
        for prefix in (8, 12, 15):
            with self.assertRaises(NetError, msg=prefix):  # a pool bigger than a /16 would flood a network
                ok(mode="share", address="10.0.0.1", prefix=prefix)
        self.assertEqual(ok(mode="share", address="172.16.5.1", prefix=16)["prefix"], 16)
        self.assertEqual(ok(mode="static", address="100.64.0.5", prefix=24)["address"], "100.64.0.5")  # CGNAT is fine as a fixed address

    def test_dns_rules(self):
        base = dict(mode="static", address="192.168.1.20", prefix=24)
        for dns in (["169.254.169.254"], ["0.1.2.3"], ["1.1.1.1", "1.1.1.1"], ["192.168.1.20"], ["224.0.0.1"]):
            with self.assertRaises(NetError, msg=repr(dns)):
                ok(dns=dns, **base)
        self.assertEqual(ok(dns=["1.1.1.1", "9.9.9.9"], **base)["dns"], ["1.1.1.1", "9.9.9.9"])

    def test_a_new_range_may_not_overlap_another_ports_network(self):
        import ipaddress
        others = [("wlan0", ipaddress.ip_network("10.5.0.0/24")), ("eth0", ipaddress.ip_network("192.168.1.0/24"))]
        req = {"iface": "eth0", "mode": "static", "address": "10.5.0.9", "prefix": 24}
        with self.assertRaises(NetError) as cm:
            netcfg.validate(req, IFACES, others)
        self.assertIn("wlan0", str(cm.exception))
        with self.assertRaises(NetError):
            netcfg.validate({"iface": "eth0", "mode": "share", "address": "10.5.0.1", "prefix": 16}, IFACES, others)
        # its own current network is fine to keep or change
        self.assertEqual(netcfg.validate(dict(req, address="192.168.1.77"), IFACES, others)["address"], "192.168.1.77")

    def test_interface_and_mode_rules(self):
        for iface in ("eth9", "lo", "", None, "eth0; reboot", "../eth0", "ETH0", "eth0 ", "a" * 40):
            with self.assertRaises(NetError, msg=repr(iface)):
                netcfg.validate({"iface": iface, "mode": "dhcp"}, IFACES)
        for mode in ("", None, "DHCP", "bridge", 5):
            with self.assertRaises(NetError, msg=repr(mode)):
                ok(mode=mode)
        with self.assertRaises(NetError):
            netcfg.validate("eth0", IFACES)


class PlanTest(unittest.TestCase):
    UUID = "0b3c2f57-7d0a-4a5e-9d6a-1f2e3d4c5b6a"

    def props(self, cmd):
        rest = cmd[cmd.index("con-name") + 2:]
        return dict(zip(rest[::2], rest[1::2]))

    def test_candidate_profile_commands_leave_the_confirmed_profile_alone(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24, gateway="192.168.50.1", dns=["1.1.1.1"])
        cmds = netcfg.plan(cfg)
        self.assertEqual(cmds[0][:9], ["nmcli", "connection", "add", "type", "ethernet", "ifname", "eth0",
                                       "con-name", "pvj-eth0-try"])
        p = self.props(cmds[0])
        self.assertEqual((p["ipv4.method"], p["ipv4.addresses"], p["ipv4.gateway"], p["ipv4.dns"]),
                         ("manual", "192.168.50.20/24", "192.168.50.1", "1.1.1.1"))
        self.assertEqual(p["connection.autoconnect"], "no")  # a reboot now falls back to the old network
        self.assertEqual(cmds[1], ["nmcli", "connection", "up", "id", "pvj-eth0-try"])
        self.assertFalse(any("pvj-eth0" in a and not a.endswith("-try") for c in cmds for a in c if a.startswith("pvj-")))

    def test_each_mode(self):
        expect = {"dhcp": "auto", "linklocal": "link-local", "share": "shared"}
        for mode, method in expect.items():
            self.assertEqual(self.props(netcfg.plan(ok(mode=mode))[0])["ipv4.method"], method)

    def test_no_shell_and_nothing_user_supplied_outside_validated_fields(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24)
        for cmd in netcfg.plan(cfg) + netcfg.confirm_plan("eth0", True) + netcfg.revert_plan("eth0", True, self.UUID):
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(a, str) and "\n" not in a and ";" not in a for a in cmd), cmd)

    def test_confirm_swaps_in_the_candidate_without_ever_leaving_no_autoconnect_profile(self):
        first = netcfg.confirm_plan("eth0", old_exists=True)
        self.assertEqual(first[0][3:6], ["id", "pvj-eth0-try", "connection.autoconnect"])
        self.assertEqual(first[1][3:], ["id", "pvj-eth0", "connection.id", "pvj-eth0-old"])   # renamed, not deleted, until
        self.assertEqual(first[2][3:], ["id", "pvj-eth0-try", "connection.id", "pvj-eth0"])   # the candidate has its name
        self.assertEqual(first[3], ["nmcli", "connection", "delete", "id", "pvj-eth0-old"])   # and only then deleted
        self.assertEqual(len(netcfg.confirm_plan("eth0", old_exists=False)), 2)

    def test_revert_drops_the_candidate_and_reactivates_the_previous_connection_by_uuid(self):
        self.assertEqual(netcfg.revert_plan("eth0", True, self.UUID),
                         [["nmcli", "connection", "down", "id", "pvj-eth0-try"],
                          ["nmcli", "connection", "delete", "id", "pvj-eth0-try"],
                          ["nmcli", "connection", "up", "uuid", self.UUID]])
        self.assertEqual(netcfg.revert_plan("eth0", False, None), [])
        for bad in ("--ask", "id", "Wired connection 1", "0b3c2f57", self.UUID + "x"):
            with self.assertRaises(NetError, msg=bad):
                netcfg.revert_plan("eth0", True, bad)

    def test_preview_keeps_empty_and_spaced_arguments_visible(self):
        self.assertEqual(netcfg.preview([["nmcli", "x", "", "a b"]]), ["nmcli x '' 'a b'"])


class InterfaceListTest(unittest.TestCase):
    def make(self, name, wireless=False, extra=(), speed="1000", state="up"):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        for fn, text in (("address", "aa:bb"), ("operstate", state), ("carrier", "1" if state == "up" else "0"),
                         ("speed", speed)):
            with open(os.path.join(d, fn), "w") as f:
                f.write(text)
        if wireless:
            os.makedirs(os.path.join(d, "wireless"))
        for x in extra:
            os.makedirs(os.path.join(d, x))

    def test_wired_first_and_virtual_interfaces_hidden(self):
        self.root = tempfile.mkdtemp()
        self.make("wlan0", wireless=True)
        self.make("enp3s0")
        self.make("eth0", speed="-1", state="down")
        self.make("docker0")
        self.make("br0", extra=("bridge",))
        self.make("veth123")
        os.makedirs(os.path.join(self.root, "lo"))
        got = netcfg.list_interfaces(self.root)
        self.assertEqual([i["name"] for i in got], ["enp3s0", "eth0", "wlan0"])
        self.assertEqual(got[0]["speed_mbps"], 1000)
        self.assertIsNone(got[1]["speed_mbps"])  # -1 means unknown
        self.assertFalse(got[1]["carrier"])
        self.assertEqual(got[2]["kind"], "wifi")

    def test_missing_sysfs_is_empty(self):
        self.assertEqual(netcfg.list_interfaces("/nonexistent"), [])


class PendingTest(unittest.TestCase):
    def test_deadline_and_restart(self):
        p = netcfg.PendingChange({}, None, started=100.0, seconds=60)
        self.assertEqual(p.seconds_left(100.0), 60)
        self.assertEqual(p.seconds_left(159.2), 1)
        self.assertFalse(p.expired(159.9))
        self.assertTrue(p.expired(160.0))
        p.restart(200.0)  # the countdown restarts once the new network is up
        self.assertEqual(p.seconds_left(200.0), 60)


def wifi(**kw):
    return netcfg.validate(dict({"iface": "wlan0", "mode": "dhcp", "ssid": "Leyline Staff", "password": "correct horse"}, **kw), IFACES)


class WifiValidateTest(unittest.TestCase):
    def test_join_is_normalised_and_public_drops_the_password(self):
        cfg = wifi()
        self.assertEqual(cfg, {"iface": "wlan0", "mode": "dhcp", "revert_seconds": 60, "kind": "wifi", "ssid": "Leyline Staff",
                               "security": "wpa-psk", "password": "correct horse", "hidden": False})
        pub = netcfg.public(cfg)
        self.assertNotIn("password", pub)
        self.assertTrue(pub["password_set"])
        self.assertNotIn("correct horse", repr(pub))

    def test_each_kind_of_port_has_its_own_modes(self):
        for mode in ("linklocal", "share"):
            with self.assertRaises(NetError, msg=mode):
                wifi(mode=mode)
        for mode in ("hotspot", "off"):
            with self.assertRaises(NetError, msg=mode):
                ok(mode=mode, ssid="x", password="12345678")
        self.assertEqual(wifi(mode="off"), {"iface": "wlan0", "mode": "off", "revert_seconds": 60, "kind": "wifi"})

    def test_network_names(self):
        self.assertEqual(wifi(ssid="Café 🎛")["ssid"], "Café 🎛")
        self.assertEqual(wifi(ssid="x" * 32)["ssid"], "x" * 32)
        for bad in ("", None, 5, "x" * 33, "é" * 17, "a\nb", "a\x00b", "a‮b", "\ud800", ["x"]):
            with self.assertRaises(NetError, msg=repr(bad)):
                wifi(ssid=bad)

    def test_passwords(self):
        self.assertEqual(wifi(password="a b\\c\"d'e")["password"], "a b\\c\"d'e")
        self.assertEqual(wifi(password="ab" * 32)["password"], "ab" * 32)          # 64 hex digits: a raw key
        for bad in (None, "", "short77", "x" * 64, "x" * 65, "pass\nword", "pässwörd", 12345678, "tab\there!"):
            with self.assertRaises(NetError, msg=repr(bad)):
                wifi(password=bad)

    def test_security_choices(self):
        self.assertNotIn("password", wifi(security="open", password=None))
        self.assertEqual(wifi(security="open", password="")["security"], "open")
        self.assertEqual(wifi(security="sae")["security"], "sae")
        with self.assertRaises(NetError):
            wifi(security="sae", password="ab" * 32)             # WPA3 has no raw hex key
        for bad in ("wep", "WPA2", "", None, "802.1x"):
            with self.assertRaises(NetError, msg=repr(bad)):
                wifi(security=bad)
        with self.assertRaises(NetError):
            wifi(security="open", password="12345678")      # a password for an open network is a mistake
        with self.assertRaises(NetError):
            wifi(mode="hotspot", security="open")           # the box never makes an open hotspot

    def test_hidden_and_band(self):
        self.assertTrue(wifi(hidden=True)["hidden"])
        for bad in ("yes", 1, None):
            with self.assertRaises(NetError):
                wifi(hidden=bad)
        h = wifi(mode="hotspot", band="a")
        self.assertEqual((h["band"], h["address"], h["prefix"]), ("a", "10.43.0.1", 24))
        self.assertNotIn("hidden", h)
        for bad in ("5", "abg", None):
            with self.assertRaises(NetError):
                wifi(mode="hotspot", band=bad)

    def test_hotspot_addresses_follow_the_served_address_rules(self):
        with self.assertRaises(NetError):
            wifi(mode="hotspot", address="8.8.8.1")
        with self.assertRaises(NetError):
            wifi(mode="hotspot", address="10.0.0.1", prefix=8)
        others = [("eth0", ipaddress.ip_network("10.43.0.0/24"))]
        with self.assertRaises(NetError):
            netcfg.validate({"iface": "wlan0", "mode": "hotspot", "ssid": "a", "password": "12345678"}, IFACES, others)

    def test_fixed_address_on_wifi(self):
        cfg = wifi(mode="static", address="192.168.8.20", prefix=24, gateway="192.168.8.1")
        self.assertEqual((cfg["address"], cfg["gateway"], cfg["ssid"]), ("192.168.8.20", "192.168.8.1", "Leyline Staff"))


class WifiPlanTest(unittest.TestCase):
    UUID = "0b3c2f57-7d0a-4a5e-9d6a-1f2e3d4c5b6a"

    def parse(self, text):
        out, section = {}, None
        for line in text.splitlines():
            if line.startswith("["):
                section = line.strip("[]")
            elif "=" in line:
                k, v = line.split("=", 1)
                out["%s.%s" % (section, k)] = v
        return out

    def test_keyfile_for_joining(self):
        kf = self.parse(netcfg.keyfile(wifi(hidden=True, password="a b\\c d e"), self.UUID))
        self.assertEqual(kf["connection.id"], "pvj-wlan0-try")
        self.assertEqual(kf["connection.autoconnect"], "false")
        self.assertEqual(kf["wifi.mode"], "infrastructure")
        self.assertEqual(bytes(int(b) for b in kf["wifi.ssid"].rstrip(";").split(";")), b"Leyline Staff")
        self.assertEqual(kf["wifi.hidden"], "true")
        self.assertEqual(kf["wifi-security.key-mgmt"], "wpa-psk")
        self.assertEqual(kf["wifi-security.psk"], "a\\sb\\\\c\\sd\\se")   # GKeyFile: \s is a space, \\ a backslash
        self.assertNotIn("band", " ".join(kf))

    def test_keyfile_cannot_be_broken_out_of(self):
        # nothing a user types can start a new line or section: the SSID is bytes and the password is checked
        text = netcfg.keyfile(wifi(ssid="[ipv4]"), self.UUID)
        self.assertEqual(sum(1 for l in text.splitlines() if l == "[ipv4]"), 1)
        with self.assertRaises(NetError):
            netcfg.keyfile(wifi(), "not-a-uuid")

    def test_keyfile_for_hotspot_and_open(self):
        kf = self.parse(netcfg.keyfile(wifi(mode="hotspot", band="a"), self.UUID))
        self.assertEqual((kf["wifi.mode"], kf["wifi.band"], kf["wifi-security.proto"], kf["wifi-security.pairwise"],
                          kf["wifi-security.pmf"]), ("ap", "a", "rsn", "ccmp", "1"))
        kf = self.parse(netcfg.keyfile(wifi(security="open", password=None), self.UUID))
        self.assertFalse([k for k in kf if k.startswith("wifi-security")])

    def test_plan_and_preview_never_show_the_password(self):
        cfg = wifi(password="hunter2hunter2")
        cmds = netcfg.plan(cfg, "abcdef01", "/etc/NetworkManager/system-connections")
        self.assertEqual(cmds[0], ["nmcli", "connection", "load",
                                   "/etc/NetworkManager/system-connections/pvj-wlan0-try-abcdef01.nmconnection"])
        self.assertEqual(cmds[-1], ["nmcli", "connection", "up", "id", "pvj-wlan0-try"])
        self.assertIn("connection.autoconnect", cmds[1])
        self.assertEqual(cmds[1][cmds[1].index("connection.autoconnect") + 1], "no")
        text = "\n".join(netcfg.preview(cmds)) + repr(cmds)
        self.assertNotIn("hunter2", text)
        self.assertIn("readable by root only", text)
        hot = netcfg.plan(wifi(mode="hotspot"), "abcdef01")
        self.assertEqual(hot[1][hot[1].index("ipv4.method") + 1], "shared")
        self.assertEqual(netcfg.plan(wifi(mode="off")), [["nmcli", "device", "disconnect", "wlan0"]])
        for bad in ("../../x", "ABCDEF01", "abc", None):
            with self.assertRaises((NetError, TypeError)):
                netcfg.keyfile_path("wlan0", bad)

    def test_confirm_and_revert_for_wifi(self):
        self.assertEqual(netcfg.confirm_plan("wlan0", True, wifi_off=True), [["nmcli", "radio", "wifi", "off"]])
        self.assertEqual(netcfg.revert_plan("wlan0", False, None, radio_was_off=True)[-1], ["nmcli", "radio", "wifi", "off"])


class ScanTest(unittest.TestCase):
    def test_terse_lines_are_split_on_unescaped_colons(self):
        self.assertEqual(netcfg.parse_terse(r"*:My\:Net\\:80:WPA2:6"), ["*", "My:Net\\", "80", "WPA2", "6"])

    def test_results_are_deduplicated_cleaned_and_sorted(self):
        text = "\n".join([
            "Venue:40:WPA2:1", "Venue:70:WPA1 WPA2:36", "Leyline Staff:55:WPA2 WPA3:11",
            "Guest::0:6", ":90:WPA2:6", "Corp:80:WPA2 802.1X:6", "Old:30:WEP:6", "New:20:WPA3:149",
            "Open Cafe:10::1", "bad\x01name:99:WPA2:1", "Odd:x:WPA2:1", "garbage", "a:b:c:d:e"])
        got = netcfg.scan_results(text, current="Leyline Staff")
        self.assertEqual([n["ssid"] for n in got], ["Leyline Staff", "Corp", "Venue", "Old", "New", "Open Cafe"])
        by = {n["ssid"]: n for n in got}
        self.assertTrue(by["Leyline Staff"]["in_use"])
        self.assertEqual((by["Venue"]["signal"], by["Venue"]["channel"]), (70, 36))
        self.assertEqual({k: by[k]["security"] for k in by},
                         {"Leyline Staff": "wpa-psk", "Corp": "unsupported", "Venue": "wpa-psk", "Old": "unsupported",
                          "New": "sae", "Open Cafe": "open"})
        self.assertEqual(len(netcfg.scan_results("\n".join("n%d:50:WPA2:1" % i for i in range(100)))), 40)
        self.assertFalse(any(n["in_use"] for n in netcfg.scan_results(text)))


if __name__ == "__main__":
    unittest.main()
