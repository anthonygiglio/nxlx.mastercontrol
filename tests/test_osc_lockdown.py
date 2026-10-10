# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The three layers on who may send OSC (D78): the list of devices, the paired device, the key in the address."""
import json
import os
import socket
import tempfile
import time
import unittest
from unittest import mock

from pvj import osc, themes as themes_mod
from pvj.api import Api
from pvj.auth import Auth
from pvj.modules import Registry
from pvj.osc import OscManager, OscServer
from pvj.settings import Settings
from tests.test_boxcare import Base as CareBase
from tests.test_osc import bundle, msg
from tests.test_server import FakePlayer
from tests.test_support import LAN, TUNNEL

TABLET, OTHER = "192.168.1.20", "192.168.1.66"
KEY = "0123456789abcdef0123"
LONG6 = "fd12:3456:789a:bcde:f012:3456:789a:bcde"
SECRET_TEXT = "Body-Sentinel-91"


class LayerBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        media = os.path.join(tmp, "video")
        os.makedirs(media)
        open(os.path.join(media, "05_x.mp4"), "w").close()
        self.settings = Settings(os.path.join(tmp, "s.json"))
        self.settings.load()
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.now = [1000.0]
        self.clock = lambda: self.now[0]
        self.auth = Auth(self.settings, clock=self.clock)
        self.player = FakePlayer(tmp)
        self.api = Api(self.player, self.settings, self.auth, Registry(self.settings, "x86"), themes_mod.load_themes(),
                       media, {"kind": "x86", "model": "t", "arch": "x86_64"})
        self.manager = OscManager(self.api, self.settings, host="127.0.0.1", log=lambda *_: None)
        self.manager.watch = osc.Watch(self.clock)
        self.api.osc = self.manager
        self.logs = []
        self.rules(**{})

    def rules(self, **keys):
        """Set the layer keys and make a receiver from the settings, as OscManager.apply does."""
        self.settings.data["osc"].update(keys)
        self.server = OscServer(self.api, clock=self.clock, log=self.logs.append, rules=self.settings.data["osc"],
                                paired=self.manager._paired, watch=self.manager.watch,
                                extra_allow=osc.parse_networks(self.settings.data["osc"]["allow"]))

    def send(self, data, ip=TABLET):
        return self.server.handle_packet(data, ip)

    def speeds(self):
        return [c[1] for c in self.player.calls if c[0] == "speed"]

    def device(self, role, ip, name="d"):
        """A paired device of that role that has just made a panel request from `ip`."""
        token, dev = self.auth._add_device(name, role)
        self.assertIsNotNone(self.auth.authenticate(token, ip))
        return token, dev

    def why(self, ip=TABLET):
        return next(s for s in self.manager.watch.senders() if s["address"] == ip)["why"]


class NothingChangesTest(LayerBase):
    def test_an_updated_box_behaves_as_before(self):
        self.assertEqual(osc.layers({"enabled": True, "port": 9876, "allow": []}),
                         {"only_on": False, "only": [], "paired_on": False, "paired_roles": "full", "paired_hours": 12,
                          "key_on": False, "key": ""})
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.assertEqual(self.send(msg("/stopall", 1.0), "10.1.2.3"), 1)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0), "8.8.8.8"), 0)
        self.assertEqual(set(self.settings.data["osc"]), {"enabled", "port", "allow"})   # nothing is written by itself

    def test_a_prefix_is_dropped_while_the_key_layer_is_off(self):
        self.assertEqual(self.send(msg("/k/whatever/pvj/speed", 3.0)), 1)
        self.assertEqual(self.speeds(), [3.0])

    def test_damaged_settings_fail_closed(self):
        self.assertEqual(osc.layers({"only_on": "yes", "only": "192.168.1.20"})["only"], [])
        self.rules(only_on=True, only="192.168.1.20")
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.rules(only_on=False, key_on=True, key="")
        self.assertEqual(self.send(msg("/k//pvj/speed", 2.0)), 0)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.assertEqual(self.why(), osc.WHY_UNSET)
        self.assertEqual(self.speeds(), [])


class OnlyTheseDevicesTest(LayerBase):
    def test_the_list_replaces_the_private_ranges(self):
        self.rules(only_on=True, only=[TABLET])
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.assertEqual(self.send(msg("/pvj/speed", 3.0), OTHER), 0)          # private, but not listed
        self.assertEqual(self.send(msg("/stopall", 1.0), "127.0.0.1"), 0)       # loopback too; old names follow the rule
        self.assertEqual(self.speeds(), [2.0])
        self.assertEqual(self.why(OTHER), osc.WHY_ONLY)
        self.assertEqual(self.manager.watch.refused, 2)
        self.rules(only_on=True, only=[])                                       # an empty list lets nobody in
        self.assertEqual(self.send(msg("/pvj/speed", 4.0)), 0)

    def test_one_spelling_per_address(self):
        self.rules(only_on=True, only=[TABLET])
        self.assertEqual(self.send(msg("/pvj/speed", 2.0), "::ffff:" + TABLET), 1)
        self.assertEqual(osc.validate_only([" 192.168.1.20 ", "192.168.1.20", "FD12::0001"]), [TABLET, "fd12::1"])

    def test_the_private_networks_stay_the_outer_wall(self):
        for bad in (["8.8.8.8"], ["192.168.1.0/24"], ["tablet.local"], [5], "192.168.1.20", [TABLET] * 17, ["2001:db8::1"]):
            with self.assertRaises(osc.OscError, msg=bad):
                osc.validate_only(bad)
        self.assertEqual(osc.validate_only(["203.0.113.7"], osc.parse_networks(["203.0.113.0/24"])), ["203.0.113.7"])
        # an address put in the file by hand, outside the wall: still refused when its packet arrives
        self.rules(only_on=True, only=["8.8.8.8"])
        self.assertEqual(self.send(msg("/pvj/speed", 2.0), "8.8.8.8"), 0)
        self.assertEqual(self.why("8.8.8.8"), osc.WHY_NETWORK)


class PairedDeviceTest(LayerBase):
    def setUp(self):
        super().setUp()
        self.rules(paired_on=True)

    def test_only_from_where_a_paired_device_asked(self):
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.assertEqual(self.why(), osc.WHY_PAIRED)
        self.device("full", "::ffff:" + TABLET)            # the panel may report an IPv4 address the IPv6 way
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.assertEqual(self.send(msg("/stopall", 1.0), OTHER), 0)
        self.assertEqual(self.speeds(), [2.0])

    def test_a_request_without_a_valid_token_teaches_nothing(self):
        self.assertIsNone(self.auth.authenticate("not-a-token", TABLET))
        self.assertIsNone(self.auth.authenticate(None, TABLET))
        self.assertEqual(self.auth.paired_addresses(3600), set())
        token, _ = self.auth._add_device("old way", "full")
        self.auth.authenticate(token)                      # no address given (the upload's re-check): nothing remembered
        self.assertEqual(self.auth.paired_addresses(3600), set())

    def test_sending_osc_does_not_make_an_address_count(self):
        self.rules(paired_on=False)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)      # goes through Api.handle as the OSC device
        self.rules(paired_on=True)
        self.assertEqual(self.send(msg("/pvj/speed", 3.0)), 0)

    def test_roles(self):
        self.device("view", TABLET, "guest")
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)       # a guest never counts
        self.device("live", OTHER, "presenter")
        self.assertEqual(self.send(msg("/pvj/speed", 2.0), OTHER), 0)   # owner only, until the owner says otherwise
        self.rules(paired_roles="live")
        self.assertEqual(self.send(msg("/pvj/speed", 2.0), OTHER), 1)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)       # still not the guest

    def test_revoke_stops_it_at_once(self):
        _, dev = self.device("full", TABLET)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.assertTrue(self.auth.revoke(dev["id"]))
        self.assertEqual(self.send(msg("/pvj/speed", 3.0)), 0)
        _, dev = self.device("full", TABLET)
        self.auth.revoke_all()
        self.assertEqual(self.send(msg("/pvj/speed", 4.0)), 0)
        self.assertEqual(self.speeds(), [2.0])
        self.assertEqual(self.auth._addresses, {})                   # nothing is kept of a device that is gone

    def test_a_device_that_is_forgotten_must_open_the_panel_again(self):
        # the panel has no log out today; this is what one would call, and what a factory reset calls
        token, dev = self.device("full", TABLET)
        self.auth.forget_address(dev["id"])
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.auth.authenticate(token, TABLET)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.auth.forget_address()
        self.assertEqual(self.send(msg("/pvj/speed", 3.0)), 0)

    def test_it_expires_and_the_hours_are_the_owners(self):
        token, _ = self.device("full", TABLET)
        self.now[0] += 12 * 3600 - 1
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)
        self.now[0] += 2
        self.assertEqual(self.send(msg("/pvj/speed", 3.0)), 0)
        self.auth.authenticate(token, TABLET)               # opening the panel again starts the hours again
        self.assertEqual(self.send(msg("/pvj/speed", 4.0)), 1)
        self.rules(paired_hours=1)
        self.now[0] += 3601
        self.assertEqual(self.send(msg("/pvj/speed", 5.0)), 0)
        self.assertEqual(self.speeds(), [2.0, 4.0])

    def test_two_devices_behind_one_address(self):
        _, a = self.device("full", TABLET, "tablet")
        _, b = self.device("full", TABLET, "phone on the same hotspot")
        self.auth.revoke(a["id"])
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 1)      # the address still counts: the phone is paired
        self.auth.revoke(b["id"])
        self.assertEqual(self.send(msg("/pvj/speed", 3.0)), 0)

    def test_a_device_that_moved_counts_at_its_new_address_only(self):
        token, _ = self.device("full", TABLET)
        self.auth.authenticate(token, OTHER)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.assertEqual(self.send(msg("/pvj/speed", 3.0), OTHER), 1)


class KeyTest(LayerBase):
    def setUp(self):
        super().setUp()
        self.rules(key_on=True, key=KEY)

    def test_missing_wrong_and_right(self):
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)
        self.assertEqual(self.why(), osc.WHY_NO_KEY)
        for wrong in ("/k/%s/pvj/speed" % KEY[:-1], "/k/%sX/pvj/speed" % KEY, "/k/" + "0" * 20 + "/pvj/speed",
                      "/k/%s" % KEY, "/k/%s/" % KEY, "/k//pvj/speed", "/K/%s/pvj/speed" % KEY, "/k/klüç/pvj/speed"):
            self.assertEqual(self.send(msg(wrong, 2.0)), 0, wrong)
        self.assertEqual(self.speeds(), [])
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 2.0)), 1)
        self.assertEqual(self.send(msg("/k/%s/startlessonce05" % KEY, 1.0)), 1)     # an old name, same rule
        self.assertEqual(self.send(msg("/startlessonce05", 1.0)), 0)
        self.assertEqual(self.send(msg("/k/%s/shutdown" % KEY, 1.0)), 0)             # the key opens nothing more
        self.assertEqual(self.speeds(), [2.0])

    def test_the_old_key_stops_at_once_when_a_new_one_is_made(self):
        self.manager.make_key()
        new = self.settings.data["osc"]["key"]
        self.assertTrue(osc.valid_key(new))
        self.assertNotEqual(new, KEY)
        self.rules()
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 2.0)), 0)
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % new, 3.0)), 1)
        self.assertEqual(len({osc.new_key() for _ in range(50)}), 50)

    def test_the_comparison_is_constant_time(self):
        with mock.patch("pvj.osc.hmac.compare_digest", side_effect=lambda a, b: a == b) as compare:
            self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 2.0)), 1)
            self.assertEqual(self.send(msg("/k/nope/pvj/speed", 2.0)), 0)
        self.assertEqual([c.args for c in compare.call_args_list], [(KEY.encode(), KEY.encode()), (b"nope", KEY.encode())])

    def test_wrong_keys_are_rate_limited_per_sender(self):
        wrong = msg("/k/%s/pvj/speed" % ("f" * 20), 2.0)
        with mock.patch("pvj.osc.hmac.compare_digest", side_effect=lambda a, b: a == b) as compare:
            for _ in range(200):
                self.send(wrong, OTHER)
            self.assertEqual(compare.call_count, int(osc.WRONG_KEYS_BURST))   # after that, not even compared
        self.assertEqual(self.why(OTHER), osc.WHY_KEYS)
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 2.0), OTHER), 0)   # the right key waits too
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 3.0)), 1)          # another sender is not held up
        self.now[0] += 1 / osc.WRONG_KEYS_PER_SECOND
        self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 4.0), OTHER), 1)
        self.assertEqual(self.speeds(), [3.0, 4.0])

    def test_one_wrong_message_refuses_the_whole_packet(self):
        good, bad = msg("/k/%s/pvj/speed" % KEY, 2.0), msg("/pvj/volume", 40.0)
        self.assertEqual(self.send(bundle(good, bad)), 0)
        self.assertEqual(self.send(bundle(bad, good)), 0)
        self.assertEqual(self.send(bundle(good, bundle(msg("/k/x/pvj/speed", 9.0)))), 0)
        self.assertEqual([c for c in self.player.calls if c[0] in ("speed", "volume")], [])
        self.assertEqual(self.send(bundle(good, msg("/k/%s/pvj/speed" % KEY, 3.0))), 2)


class TogetherTest(LayerBase):
    def test_a_packet_must_pass_every_layer_that_is_on(self):
        self.rules(only_on=True, only=[TABLET], paired_on=True, key_on=True, key=KEY)
        keyed = msg("/k/%s/pvj/speed" % KEY, 2.0)
        self.assertEqual(self.send(keyed), 0)
        self.assertEqual(self.why(), osc.WHY_PAIRED)             # listed and keyed, but no paired device there
        self.device("full", TABLET)
        self.device("full", OTHER)
        self.assertEqual(self.send(keyed, OTHER), 0)             # paired and keyed, not listed
        self.assertEqual(self.why(OTHER), osc.WHY_ONLY)
        self.assertEqual(self.send(msg("/pvj/speed", 2.0)), 0)   # listed and paired, no key
        self.assertEqual(self.why(), osc.WHY_NO_KEY)
        self.assertEqual(self.speeds(), [])
        self.assertEqual(self.send(keyed), 1)
        for off in ("only_on", "paired_on", "key_on"):           # each one alone is enough to refuse
            self.rules(only_on=True, paired_on=True, key_on=True)
            self.rules(**{off: False})
            self.assertEqual(self.send(msg("/k/%s/pvj/speed" % KEY, 5.0), "10.9.9.9"), 0, off)
        self.assertEqual(self.speeds(), [2.0])

    def test_the_existing_limits_are_kept(self):
        self.rules(only_on=True, only=[TABLET], key_on=True, key=KEY)
        keyed = msg("/k/%s/pvj/speed" % KEY, 1.0)
        done = sum(self.send(keyed) for _ in range(int(osc.RATE_BURST) + 100))
        self.assertEqual(done, int(osc.RATE_BURST))
        self.assertEqual(self.why(), osc.WHY_RATE)
        self.now[0] += 5
        self.assertEqual(self.send(b"\0" * (osc.MAX_PACKET + 1)), 0)
        self.assertEqual(self.why(), osc.WHY_PACKET)


class WatchTest(LayerBase):
    def test_what_the_owner_sees(self):
        self.rules(only_on=True, only=[TABLET])
        self.send(msg("/pvj/speed", 2.0))
        self.send(msg("/pvj/nonsense", 1.0))
        self.send(msg("/pvj/speed", 3.0), OTHER)
        self.send(msg("/pvj/speed", 3.0), OTHER)
        rows = {s["address"]: s for s in self.manager.watch.senders()}
        self.assertEqual((rows[TABLET]["messages"], rows[TABLET]["last"], rows[TABLET]["accepted"]), (2, "/pvj/nonsense", True))
        self.assertEqual((rows[OTHER]["messages"], rows[OTHER]["refused"], rows[OTHER]["last"], rows[OTHER]["accepted"], rows[OTHER]["why"]),
                         (0, 2, "", False, osc.WHY_ONLY))
        log = self.manager.watch.messages()
        self.assertEqual([(m["from"], m["address"], m["ok"], m["why"], m["count"]) for m in log],
                         [(OTHER, "", False, osc.WHY_ONLY, 2), (TABLET, "/pvj/nonsense", False, osc.DID_NOTHING, 1),
                          (TABLET, "/pvj/speed", True, "", 1)])

    def test_nothing_from_a_refused_packet_is_kept_or_logged(self):
        self.rules(only_on=True, only=[TABLET], key_on=True, key=KEY)
        for ip, data in ((OTHER, msg("/pvj/play/file", SECRET_TEXT)), (OTHER, msg("/" + SECRET_TEXT, 1)),
                         (TABLET, msg("/" + SECRET_TEXT, SECRET_TEXT)), (TABLET, msg("/k/%s/x" % SECRET_TEXT, SECRET_TEXT)),
                         (TABLET, msg("/k/%s/pvj/speed" % KEY[:-1], 1.0)),       # a near miss of the key is not shown either
                         (TABLET, SECRET_TEXT.encode()), ("8.8.8.8", msg("/" + SECRET_TEXT, 1))):
            self.assertEqual(self.send(data, ip), 0)
        kept = json.dumps([self.manager.watch.senders(), self.manager.watch.messages(), self.logs])
        self.assertNotIn(SECRET_TEXT, kept)
        self.assertNotIn(KEY[:-1], kept)
        self.assertEqual(self.player.calls, [])
        self.send(msg("/k/%s/pvj/speed" % KEY, 2.0))
        kept = json.dumps([self.manager.watch.senders(), self.manager.watch.messages(), self.logs])
        self.assertNotIn(KEY, kept)                                              # nor the right key
        self.assertIn("/pvj/speed", kept)

    def test_the_lists_are_bounded(self):
        self.rules(only_on=True, only=[TABLET])
        self.send(msg("/pvj/speed", 2.0))                       # the tablet that works
        self.send(msg("/x" * 100, 1))
        for i in range(3000):                                   # a spray of senders, each forged
            self.send(msg("/pvj/speed", 2.0), "10.%d.%d.%d" % (i >> 16 & 255, i >> 8 & 255, i & 255))
        for i in range(3000):
            self.send(msg("/pvj/speed", 2.0), "8.8.%d.%d" % (i >> 8 & 255, i & 255))
        senders = self.manager.watch.senders()
        self.assertLessEqual(len(senders), osc.MAX_SENDERS)
        self.assertLessEqual(len(self.manager.watch.messages()), osc.MAX_LOG)
        self.assertTrue(all(len(m["address"]) <= osc.SHOWN_ADDRESS for m in self.manager.watch.messages()))
        self.assertLess(len(self.server._quiet_until), 2100)
        self.assertIn(TABLET, [s["address"] for s in senders])  # the refused ones went first
        self.assertEqual(osc.shown("/aé\n\x00<b>" + "x" * 200), ("/a???<b>" + "x" * 200)[:osc.SHOWN_ADDRESS])

    def test_only_the_last_ten_minutes(self):
        self.send(msg("/pvj/speed", 2.0))
        self.now[0] += osc.SENDER_WINDOW + 1
        self.send(msg("/pvj/speed", 2.0), OTHER)
        self.assertEqual([s["address"] for s in self.manager.watch.senders()], [OTHER])

    def test_a_change_of_who_may_send_keeps_the_socket_open(self):
        self.settings.data["osc"].update(enabled=True, port=0)
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        first = self.manager.server
        self.settings.data["osc"].update(port=first.port, only_on=True, only=[OTHER], allow=["203.0.113.0/24"])
        self.manager.apply()
        self.assertIs(self.manager.server, first)               # no gap in which a message would be lost
        self.assertEqual(first.handle_packet(msg("/pvj/speed", 2.0), TABLET), 0)
        self.assertEqual(first.handle_packet(msg("/pvj/speed", 2.0), OTHER), 1)
        self.assertTrue(osc.source_allowed("203.0.113.9", first.extra))
        self.settings.data["osc"].update(enabled=False)
        self.manager.apply()
        self.assertIsNone(self.manager.server)

    def test_the_list_outlives_a_save_of_the_page(self):
        self.settings.data["osc"].update(enabled=True, port=0)
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        self.manager.server.handle_packet(msg("/pvj/speed", 2.0), TABLET)
        self.manager.apply()
        self.assertEqual([s["address"] for s in self.manager.watch.senders()], [TABLET])


class RealSocketTest(LayerBase):
    def test_real_udp_on_loopback_with_every_layer(self):
        token, _ = self.auth._add_device("laptop", "full")
        self.settings.data["osc"].update(enabled=True, port=0, only_on=True, only=["127.0.0.1"], paired_on=True, key_on=True, key=KEY)
        self.manager.apply()
        self.addCleanup(self.manager.stop)
        server = self.manager.server
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)

        def send(data, want):
            sock.sendto(data, ("127.0.0.1", server.port))
            deadline = time.time() + 5
            while time.time() < deadline and server.stats["received"] < want:
                time.sleep(0.02)
            self.assertEqual(server.stats["received"], want)
        send(msg("/k/%s/pvj/speed" % KEY, 2.0), 1)               # no paired device has asked from loopback yet
        self.assertEqual(self.speeds(), [])
        self.auth.authenticate(token, "127.0.0.1")
        send(msg("/pvj/speed", 3.0), 2)                          # no key
        send(msg("/k/%s/pvj/speed" % ("a" * 20), 4.0), 3)        # a wrong one
        self.assertEqual(self.speeds(), [])
        send(msg("/k/%s/pvj/speed" % KEY, 3.5), 4)
        deadline = time.time() + 5
        while time.time() < deadline and not self.speeds():
            time.sleep(0.02)
        self.assertEqual(self.speeds(), [3.5], self.manager.watch.messages())
        self.assertEqual(self.manager.watch.refused, 3)
        sock.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(1024)                                  # still never a reply, refused or not


class ApiTest(CareBase):
    """Through the API and over HTTP, with the three roles (tests.test_boxcare's box)."""

    def setUp(self):
        super().setUp()
        self.settings.data["osc"]["allow"] = []
        self.addCleanup(self.api.osc.stop)

    def post(self, body, device=None):
        return self.h("POST", "/api/osc", body, device or self.full_dev)

    def test_the_panel_remembers_where_a_device_asked_from_and_only_in_memory(self):
        # setUp paired and invited over real HTTP from loopback
        self.assertEqual(self.auth.paired_addresses(3600, ("full", "live")), {"127.0.0.1"})
        self.assertEqual(self.post({"paired_on": True, "only_on": True, "only": ["127.0.0.1", LONG6]})[0], 200)
        on_disk = read(self.settings.path)
        self.assertNotIn("_addresses", on_disk)
        self.assertNotIn("paired_now", on_disk)
        file = json.dumps(self.export())
        self.assertNotIn("paired_now", file)
        self.assertNotIn("senders", file)

    def test_the_tunnel_address_is_never_remembered(self):
        self.ready()
        self.start()
        self.auth.forget_address()
        with mock.patch.object(self.api.support, "is_remote", return_value=True):
            self.call("GET", "/api/status", token=self.full)
        self.assertEqual(self.auth.paired_addresses(3600), set())
        self.call("GET", "/api/status", token=self.full)
        self.assertEqual(self.auth.paired_addresses(3600), {"127.0.0.1"})

    def test_saving_and_what_is_refused(self):
        st, d = self.post({"only_on": True, "only": [TABLET, LONG6], "paired_on": True, "paired_roles": "live", "paired_hours": 3})
        self.assertEqual(st, 200, d)
        self.assertEqual((d["only"], d["paired_roles"], d["paired_hours"], d["key_on"], d["key_set"]), ([TABLET, LONG6], "live", 3, False, False))
        before = json.dumps(self.settings.data["osc"], sort_keys=True)
        for bad in ({"only": ["8.8.8.8"]}, {"only": ["192.168.0.0/16"]}, {"only": [TABLET] * 17}, {"only_on": 1}, {"key_on": "yes"},
                    {"paired_roles": "view"}, {"paired_hours": 0}, {"paired_hours": 73}, {"paired_hours": 1.5}, {"paired_hours": True},
                    {"only": ["203.0.113.7"]}):
            st, d = self.post(bad)
            self.assertEqual(st, 400, bad)
        self.assertEqual(json.dumps(self.settings.data["osc"], sort_keys=True), before)
        # an address outside the private ranges only together with its network, and never without the wall
        self.assertEqual(self.post({"allow": ["203.0.113.0/24"], "only": ["203.0.113.7"]})[0], 200)
        # a key cannot be chosen from outside
        st, d = self.post({"key_on": True, "key": "aaaaaaaaaaaaaaaaaaaa"})
        self.assertEqual(st, 200)
        self.assertTrue(osc.valid_key(self.settings.data["osc"]["key"]))
        self.assertNotEqual(self.settings.data["osc"]["key"], "aaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(json.loads(read(self.settings.path))["osc"]["key"], self.settings.data["osc"]["key"])

    def test_saves_at_the_same_moment_do_not_trip_over_the_port(self):
        import threading
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertEqual(self.post({"enabled": True, "port": port})[0], 200)
        bodies = [{"paired_hours": 3}, {"key_on": True}, {"only_on": True}, {"paired_on": True}, {"paired_roles": "live"},
                  {"only": [TABLET]}] * 3
        results = []
        threads = [threading.Thread(target=lambda b=b: results.append(self.post(b)[0])) for b in bodies]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [200] * len(bodies))
        got = self.h("GET", "/api/osc", device=self.full_dev)[1]
        self.assertEqual((got["listening"], got["error"], got["paired_hours"], got["key_on"], got["only_on"], got["paired_on"],
                          got["paired_roles"], got["only"]), (True, None, 3, True, True, True, "live", [TABLET]))

    def test_the_key_is_shown_only_on_its_own_route_to_a_full_device_at_the_studio(self):
        self.assertEqual(self.post({"key_on": True})[0], 200)
        key = self.settings.data["osc"]["key"]
        for dev in (self.full_dev, self.live_dev, self.view_dev):
            self.assertNotIn(key, json.dumps(self.h("GET", "/api/osc", device=dev)))
            self.assertNotIn(key, json.dumps(self.h("GET", "/api/status", device=dev)))
        self.assertNotIn(key, json.dumps(self.post({"only_on": False})))
        st, d = self.h("POST", "/api/osc/key", {}, self.full_dev)
        self.assertEqual((st, d), (200, {"key": key, "prefix": "/k/" + key, "example": "/k/%s/pvj/stop" % key}))
        st, d = self.h("POST", "/api/osc/key", {"new": True}, self.full_dev)
        self.assertEqual(st, 200)
        self.assertNotEqual(d["key"], key)
        self.assertEqual(json.loads(read(self.settings.path))["osc"]["key"], d["key"])
        self.assertEqual(self.h("POST", "/api/osc/key", {"new": "yes"}, self.full_dev)[0], 400)
        self.assertEqual(self.h("GET", "/api/osc/key", device=self.full_dev)[0], 405)       # never a GET: no cache, no log line
        dev, _ = self.remote_login()
        self.assertEqual(self.h("POST", "/api/osc/key", {}, dev, TUNNEL)[0], 403)            # not through remote support

    def test_a_presenter_and_a_guest_see_neither_the_key_nor_the_lists(self):
        self.settings.data["osc"].update(enabled=True, port=0)
        self.assertEqual(self.post({"only_on": True, "only": [TABLET], "key_on": True})[0], 200)
        self.api.osc.server.handle_packet(msg("/pvj/speed", 2.0), TABLET)
        full = self.h("GET", "/api/osc", device=self.full_dev)[1]
        self.assertEqual([s["address"] for s in full["senders"]], [TABLET])
        self.assertEqual(full["refused"], 1)
        for dev in (self.live_dev, self.view_dev):
            st, d = self.h("GET", "/api/osc", device=dev)
            self.assertEqual(st, 200)
            self.assertEqual(set(d), {"enabled", "port", "allow", "listening", "error", "received", "only_on", "paired_on", "key_on"})
            self.assertNotIn(TABLET, json.dumps(d))
            self.assertEqual(self.h("GET", "/api/osc/messages", device=dev)[0], 403)
            self.assertEqual(self.h("POST", "/api/osc/key", {}, dev)[0], 403)
            self.assertEqual(self.h("POST", "/api/osc", {"key_on": False}, dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/osc/messages")[0], 401)
        self.assertEqual(self.h("POST", "/api/osc/key", {})[0], 401)
        self.assertEqual(self.h("GET", "/api/osc", device=None)[0], 401)
        st, d = self.h("GET", "/api/osc/messages", device=self.full_dev)
        self.assertEqual([(m["from"], m["address"], m["why"]) for m in d["messages"]], [(TABLET, "", osc.WHY_NO_KEY)])
        self.assertTrue(self.settings.data["osc"]["key_on"])

    def test_over_http_as_a_guest(self):
        guest = self.call("POST", "/api/devices/invite", {"name": "g2", "role": "view"}, token=self.full)[1]["token"]
        self.post({"key_on": True, "only_on": True, "only": [TABLET]})
        st, d, _ = self.call("GET", "/api/osc", token=guest)
        self.assertEqual((st, "only" in d, "senders" in d, "key" in d), (200, False, False, False))
        self.assertEqual(self.call("POST", "/api/osc/key", {}, token=guest)[0], 403)
        self.assertEqual(self.call("GET", "/api/osc/messages", token=guest)[0], 403)
        self.assertEqual(self.call("POST", "/api/osc/key", {}, token=self.full, csrf=False)[0], 403)   # and never cross-site


class BoxCareTest(CareBase):
    def setUp(self):
        super().setUp()
        self.settings.data["osc"] = {"enabled": False, "port": 9001, "allow": [], "only_on": True, "only": [TABLET],
                                     "paired_on": True, "paired_roles": "live", "paired_hours": 6, "key_on": True, "key": KEY}

    def test_an_export_never_carries_the_key(self):
        file = self.export()
        self.assertNotIn(KEY, json.dumps(file))
        self.assertNotIn("key", file["settings"]["osc"])
        self.assertEqual(file["settings"]["osc"]["only"], [TABLET])
        self.assertNotIn(KEY, json.dumps(self.export(passwords=True)))
        self.assertEqual(self.settings.data["osc"]["key"], KEY)             # the box still has it

    def test_the_diagnostics_file_does_not_show_it(self):
        from tests.test_boxcare import FakeJournal
        self.care._run = FakeJournal()
        st, out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev, LAN)
        self.assertEqual(st, 200, out)
        self.assertNotIn(KEY, json.dumps(out))
        self.assertEqual(out["file"]["settings"]["osc"]["only"], [TABLET])       # the rest of the section is there
        self.assertEqual(boxcare_net({"osc": {"key": KEY}})["osc"]["key"], "(removed)")

    def test_an_import_checks_the_layers_and_keeps_the_boxes_own_key(self):
        file = self.export()
        for bad in ({"only": ["8.8.8.8"]}, {"only": [TABLET] * 17}, {"only": "all"}, {"only_on": "on"}, {"paired_roles": "view"},
                    {"paired_hours": 999}, {"key_on": 1}, {"only": ["192.168.1.0/24"]}):
            broken = json.loads(json.dumps(file))
            broken["settings"]["osc"].update(bad)
            with self.assertRaises(Exception, msg=bad):
                self.care.import_settings(json.dumps(broken).encode(), "import", self.full_dev, LAN)
        self.assertEqual(self.settings.data["osc"]["only"], [TABLET])
        file["settings"]["osc"].update(key="f" * 20, only=[OTHER], paired_hours=2)          # a key in a file is not taken
        self.care.import_settings(json.dumps(file).encode(), "import", self.full_dev, LAN)
        got = self.settings.data["osc"]
        self.assertEqual((got["key"], got["only"], got["paired_hours"], got["key_on"]), (KEY, [OTHER], 2, True))

    def test_a_file_from_an_earlier_version_switches_no_layer_off(self):
        file = self.export()
        file["settings"]["osc"] = {"enabled": False, "port": 9002, "allow": []}
        self.care.import_settings(json.dumps(file).encode(), "import", self.full_dev, LAN)
        got = self.settings.data["osc"]
        self.assertEqual((got["port"], got["only_on"], got["only"], got["paired_on"], got["key_on"], got["key"]),
                         (9002, True, [TABLET], True, True, KEY))

    def test_a_box_without_a_key_makes_one_when_the_file_switches_the_layer_on(self):
        file = self.export()
        self.settings.data["osc"] = {"enabled": False, "port": 9001, "allow": []}
        self.care.import_settings(json.dumps(file).encode(), "import", self.full_dev, LAN)
        got = self.settings.data["osc"]
        self.assertTrue(got["key_on"] and osc.valid_key(got["key"]) and got["key"] != KEY)

    def test_a_factory_reset_clears_all_of_it(self):
        self.api.osc.watch.note(TABLET, True, "", "/pvj/stop")
        self.assertTrue(self.auth.paired_addresses(3600))
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["osc"], {"enabled": False, "port": 9876, "allow": []})
        self.assertNotIn(KEY, read(self.settings.path))
        self.assertNotIn(KEY, read(self.settings.path + ".bak"))
        self.assertEqual((self.api.osc.watch.senders(), self.api.osc.watch.messages(), self.auth._addresses), ([], [], {}))


def read(path):
    with open(path) as f:
        return f.read()


def boxcare_net(value):
    from pvj import boxcare
    return boxcare._net(value)


if __name__ == "__main__":
    unittest.main()
