# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Who is connected now and what each changed last (pvj/presence.py, D126): the 30 second window, what each role is told,
the support session, and that nothing is ever written to the disk."""
import json
import os
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import presence
from tests.test_boxcare import Base as CareBase


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def dev(ident, name, role, remote=False):
    d = {"id": ident, "name": name, "role": role, "created": 1}
    if remote:
        d["remote"] = True
    return d


OWNER, OWNER2 = dev("aaaa0001", "Studio laptop", "full"), dev("aaaa0002", "Owner phone", "full")
OP, GUEST, GUEST2 = dev("bbbb0001", "Operator iPad", "live"), dev("cccc0001", "Guest phone", "view"), dev("cccc0002", "Other guest", "view")
SUPPORT = dev("support-1", "Support", "full", remote=True)


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.p = presence.Presence(clock=self.clock, controllers=("midi", "osc", "dmx", "room"))

    def seen_all(self):
        for d in (OWNER, OWNER2, OP, GUEST, GUEST2, SUPPORT):
            self.p.seen(d)

    def test_connected_means_a_request_in_the_last_30_seconds(self):
        self.p.seen(OWNER)
        self.p.seen(GUEST)
        self.clock.t += 29
        self.p.seen(GUEST)
        self.assertEqual(self.p.view(OWNER)["count"], 2)
        self.clock.t += 2             # the owner's last request was 31 s ago, the guest's 2 s
        v = self.p.view(GUEST)
        self.assertEqual((v["count"], v["window_seconds"]), (1, 30))
        self.clock.t += 40
        self.assertEqual(self.p.view(OWNER)["count"], 0, "and it is not there again until it asks again")
        self.p.seen(OWNER)
        self.assertEqual(self.p.view(OWNER)["devices"][0]["seconds_ago"], 0)

    def test_the_nodes_own_callers_and_odd_devices_are_nobody(self):
        for d in (dev("midi", "MIDI", "live"), dev("room", "Room", "full"), dev("x", "n", "bogus"), {"id": "x"}, {}, None, "no",
                  {"id": 5, "name": "n", "role": "full"}, {"id": "zz", "name": 5, "role": "full"}):
            self.p.seen(d)
            self.p.changed(d, "POST", "/api/mix")
        self.assertEqual(self.p.view(OWNER), {"window_seconds": 30, "count": 0, "devices": [], "guests": 0, "changes": []})

    def test_the_owner_sees_everyone_and_remote_support_by_that_name(self):
        self.seen_all()
        self.clock.t += 5
        self.p.seen(OWNER)
        v = self.p.view(OWNER)
        self.assertEqual(v["count"], 6)
        self.assertEqual([(r["name"], r["role"], r["you"]) for r in v["devices"]][0], ("Studio laptop", "Owner", True))
        names = {r["name"]: r for r in v["devices"]}
        self.assertEqual(set(names), {"Studio laptop", "Owner phone", "Operator iPad", "Guest phone", "Other guest", "Remote support"})
        self.assertEqual(names["Remote support"]["role"], "Remote support")
        self.assertEqual(names["Operator iPad"]["seconds_ago"], 5)
        self.assertEqual(v["guests"], 2)
        self.assertNotIn("Support", [r["name"] for r in v["devices"] if r["name"] != "Remote support"])

    def test_an_operator_sees_operators_by_name_owners_by_role_and_a_count_of_guests(self):
        self.seen_all()
        v = self.p.view(OP)
        self.assertEqual(sorted((r["name"], r["role"]) for r in v["devices"] if r["name"]), [("Operator iPad", "Operator")])
        self.assertEqual(sorted(r["role"] for r in v["devices"] if r["name"] is None), ["Owner", "Owner"], "an Owner's device by its role only")
        self.assertEqual(len(v["devices"]), 3)
        self.assertEqual(v["guests"], 2)
        self.assertEqual(v["count"], 5, "the support session is not counted for him")
        self.assertEqual([r["you"] for r in v["devices"] if r["name"] == "Operator iPad"], [True])
        text = json.dumps(v)
        for hidden in ("Studio laptop", "Owner phone", "Guest phone", "Other guest", "Support", "Remote support", "aaaa", "cccc", "support-1"):
            self.assertNotIn(hidden, text)

    def test_a_guest_sees_only_a_count(self):
        self.seen_all()
        v = self.p.view(GUEST)
        self.assertEqual((v["devices"], v["count"], v["guests"]), ([], 5, 0), "the count, and nothing that splits it")
        text = json.dumps(v)
        for hidden in ("Studio", "Owner", "Operator", "Other guest", "Remote", "Support", "aaaa", "bbbb"):
            self.assertNotIn(hidden, text)
        self.assertEqual(self.p.view(None)["devices"], [], "nobody in particular is a guest")

    def test_a_support_login_is_told_roles_and_counts_and_no_device_name(self):
        self.seen_all()
        v = self.p.view(SUPPORT)
        self.assertEqual(sorted(r["role"] for r in v["devices"]), ["Operator", "Owner", "Owner", "Remote support"])
        self.assertEqual([r["name"] for r in v["devices"] if not r["you"]], [None, None, None])
        self.assertEqual([r["name"] for r in v["devices"] if r["you"]], ["Remote support"], "its own entry carries only the label")
        text = json.dumps(v)
        for hidden in ("Studio laptop", "Owner phone", "Operator iPad", "Guest phone", "Other guest"):
            self.assertNotIn(hidden, text)
        self.assertEqual((v["guests"], v["count"]), (2, 6))

    def test_the_last_changes_by_area_newest_first_and_ten_at_most(self):
        for i in range(14):
            self.clock.t += 1
            self.p.seen(OP)
            self.p.changed(OP, "POST", "/api/mix" if i % 2 else "/api/pads/play")
        self.p.seen(GUEST)
        ch = self.p.view(OWNER)["changes"]
        self.assertEqual(len(ch), 10)
        self.assertEqual([c["seconds_ago"] for c in ch], list(range(0, 10)))
        self.assertEqual({c["area"] for c in ch}, {"Mix", "Pads"})
        self.assertEqual(ch[0]["by"], "Operator iPad")
        self.assertEqual((ch[0]["role"], ch[0]["you"]), ("Operator", False))
        self.assertEqual(self.p.view(OP)["changes"][0]["you"], True)

    def test_what_is_a_change_and_what_is_not(self):
        for method, path in (("GET", "/api/mix"), ("POST", "/api/media/info"), ("POST", "/api/pair"), ("POST", "/api/session"),
                             ("POST", "/api/devices/revoke"), ("POST", "/api/access/code"), ("POST", "/api/pin/rotate"),
                             ("POST", "/api/support/start"), ("POST", "/api/https/root"), ("POST", "/api/recovery/codes"),
                             ("POST", "/api/unknown"), ("POST", "/api/guests"), ("POST", "/api"), ("POST", "/x/mix")):
            self.p.changed(OWNER, method, path)
        self.assertEqual(self.p.view(OWNER)["changes"], [])
        for method, path, area in (("POST", "/api/projector", "Projectors"), ("POST", "/api/projectors", "Projectors"),
                                   ("POST", "/api/play", "Playback"), ("POST", "/api/room/scene", "Room"), ("POST", "/api/network/apply", "Network"),
                                   ("POST", "/api/renderer", "Renderer"), ("POST", "/api/sound/config", "Sound"), ("POST", "/api/shaders/play", "Shaders"),
                                   ("DELETE", "/api/media/x", "Media"), ("POST", "/api/system/reboot", "System")):
            self.assertEqual(presence.area_of(method, path), area, path)

    def test_who_made_a_change_follows_the_same_rules(self):
        self.seen_all()
        self.p.changed(GUEST, "POST", "/api/mix")
        self.p.changed(SUPPORT, "POST", "/api/projectors")
        self.p.changed(OP, "POST", "/api/pads/play")
        self.p.changed(OWNER2, "POST", "/api/play")
        by = lambda viewer: [(c["area"], c["by"], c["role"]) for c in self.p.view(viewer)["changes"]]
        self.assertEqual(by(OWNER), [("Playback", "Owner phone", "Owner"), ("Pads", "Operator iPad", "Operator"),
                                     ("Projectors", "Remote support", "Remote support"), ("Mix", "Guest phone", "Guest")])
        self.assertEqual(by(OP), [("Playback", None, "Owner"), ("Pads", "Operator iPad", "Operator"), ("Mix", None, "Guest")],
                         "an Owner's device and a guest by role only, and no support change")
        self.assertEqual(by(SUPPORT), [("Playback", None, "Owner"), ("Pads", None, "Operator"), ("Projectors", "Remote support", "Remote support"),
                                       ("Mix", None, "Guest")], "no device name, its own change as Remote support")
        self.assertEqual(by(GUEST), [("Playback", None, None), ("Pads", None, None), ("Mix", None, None)])
        self.assertEqual([c["you"] for c in self.p.view(GUEST)["changes"]], [False, False, True])
        for viewer in (OWNER, OP, GUEST, SUPPORT):
            text = json.dumps(self.p.view(viewer))
            for secret in ("aaaa0001", "bbbb0001", "cccc0001", "support-1", "token"):
                self.assertNotIn(secret, text)

    def test_looking_or_asking_is_not_a_change(self):
        for method, path, body in (("POST", "/api/network/scan", {}), ("POST", "/api/osc/key", {}), ("POST", "/api/osc/key", {"new": False}),
                                   ("POST", "/api/osc/key", None), ("POST", "/api/midi/learn", {"start": True}), ("POST", "/api/midi/learn", {"start": False})):
            self.p.changed(OWNER, method, path, body)
        self.assertEqual(self.p.view(OWNER)["changes"], [])
        self.p.changed(OWNER, "POST", "/api/osc/key", {"new": True})
        self.assertEqual([c["area"] for c in self.p.view(OWNER)["changes"]], ["Controllers"], "a new key is one")

    def test_a_removed_device_is_gone_at_once_from_the_list_and_the_changes(self):
        self.seen_all()
        self.p.changed(GUEST, "POST", "/api/mix")
        self.p.changed(OP, "POST", "/api/play")
        self.p.forget(GUEST["id"])
        v = self.p.view(OWNER)
        self.assertNotIn("Guest phone", json.dumps(v))
        self.assertEqual((v["count"], [c["area"] for c in v["changes"]]), (5, ["Playback"]))
        self.p.forget(None)
        self.assertEqual(self.p.view(OWNER), {"window_seconds": 30, "count": 0, "devices": [], "guests": 0, "changes": []})

    def test_nothing_is_ever_written_to_the_disk(self):
        folder = tests.TMP if hasattr(tests, "TMP") else os.environ.get("TMPDIR", "/tmp")
        def refuse(*a, **k):
            raise AssertionError("presence touched the disk: %r %r" % (a, k))
        with mock.patch("builtins.open", refuse), mock.patch("os.replace", refuse), mock.patch("os.rename", refuse), \
                mock.patch("os.fsync", refuse), mock.patch("os.open", refuse), mock.patch("os.write", refuse):
            for i in range(50):
                self.clock.t += 1
                for d in (OWNER, OP, GUEST, SUPPORT):
                    self.p.seen(d)
                    self.p.changed(d, "POST", "/api/mix")
                    self.p.view(d)
        self.assertTrue(os.path.isdir(folder))

    def test_memory_stays_small(self):
        for i in range(500):
            self.clock.t += 1
            self.p.seen(dev("d%04d" % i, "n%d" % i, "view"))
        self.clock.t += presence.FORGET + 1
        self.p.seen(OWNER)
        for i in range(80):
            self.p.seen(dev("e%04d" % i, "m%d" % i, "view"))
        self.assertLessEqual(len(self.p._devices), 64 + 80, "devices not heard from for ten minutes are dropped once there are many")


class ApiTest(CareBase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.api.presence = presence.Presence(clock=self.clock, controllers=("midi", "osc", "dmx", "room"))
        self.names = {k: getattr(self, k)["name"] for k in ("full_dev", "live_dev", "view_dev")}

    def status(self, device):
        st, out = self.h("GET", "/api/status", {}, device)
        self.assertEqual(st, 200, out)
        return out["presence"]

    def test_status_carries_presence_by_role(self):
        for d in (self.full_dev, self.live_dev, self.view_dev):
            self.status(d)
        owner, op, guest = (self.status(self.full_dev), self.status(self.live_dev), self.status(self.view_dev))
        self.assertEqual(sorted(r["role"] for r in owner["devices"]), ["Guest", "Operator", "Owner"])
        self.assertEqual(sorted(r["role"] for r in op["devices"]), ["Operator", "Owner"])
        self.assertEqual([r["name"] for r in op["devices"] if r["role"] == "Owner"], [None], "an Owner's device by role only")
        self.assertEqual((guest["devices"], guest["count"], guest["guests"]), ([], 3, 0))
        self.assertEqual(op["guests"], 1)
        self.assertTrue(all(set(r) == {"name", "role", "you", "seconds_ago"} for r in owner["devices"]))
        self.assertEqual(len(json.dumps(owner)) < 1500, True, "small enough to ride on a status every second")

    def test_a_change_made_through_the_api_is_told_by_area(self):
        self.status(self.full_dev)
        st, out = self.h("POST", "/api/mix", {"duration": 2.0}, self.live_dev)
        self.assertEqual(st, 200, out)
        self.clock.t += 12
        ch = self.status(self.full_dev)["changes"]
        self.assertEqual((ch[0]["area"], ch[0]["by"], ch[0]["seconds_ago"]), ("Mix", self.names["live_dev"], 12))
        failed = self.h("POST", "/api/mix", {"duration": "no"}, self.live_dev)
        self.assertNotEqual(failed[0], 200)
        self.assertEqual(len(self.status(self.full_dev)["changes"]), 1, "a request that failed changed nothing")
        self.assertEqual(self.status(self.view_dev)["changes"][0]["by"], None)

    def test_a_support_session_is_remote_support_to_the_owner_only(self):
        sup = dev("support-1", "Support", "full", remote=True)
        self.api.handle("GET", "/api/status", {}, sup, "10.99.0.2")         # refused or not, it asked
        self.status(self.live_dev)
        owner = self.status(self.full_dev)
        self.assertIn("Remote support", [r["name"] for r in owner["devices"]])
        self.assertNotIn("Remote support", json.dumps(self.status(self.live_dev)))
        self.assertNotIn("Remote support", json.dumps(self.status(self.view_dev)))

    def test_revoking_a_device_and_a_factory_reset_drop_it_at_once(self):
        op_id = self.live_dev["id"]
        for d in (self.full_dev, self.live_dev, self.view_dev):
            self.status(d)
        self.assertEqual(self.h("POST", "/api/mix", {"duration": 2.0}, self.live_dev)[0], 200)
        owner = self.status(self.full_dev)
        self.assertEqual((owner["count"], owner["changes"][0]["by"]), (3, self.names["live_dev"]))
        st, out = self.h("POST", "/api/devices/revoke", {"id": op_id}, self.full_dev)
        self.assertEqual(st, 200, out)
        owner = self.status(self.full_dev)
        self.assertEqual((owner["count"], owner["changes"]), (2, []))
        self.assertNotIn("Operator", [r["role"] for r in owner["devices"]])
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.api.presence.view(self.full_dev), {"window_seconds": 30, "count": 0, "devices": [], "guests": 0, "changes": []})

    def test_the_requests_answered_outside_the_route_table_count(self):
        full = self.call("POST", "/api/devices/invite", {"name": "Booth", "role": "live"}, token=self.full)[1]["token"]
        before = self.status(self.full_dev)
        st, body, _ = self.call("POST", "/api/media/upload?name=cue.mp4", raw=b"not really a video", token=full,
                                headers={"Content-Type": "application/octet-stream"})
        self.assertIn(st, (200, 201, 415, 422, 400), body)
        names = [c["area"] for c in self.status(self.full_dev)["changes"]]
        if st == 200:
            self.assertEqual(names[0], "Media")
        self.assertIn("Booth", [r["name"] for r in self.status(self.full_dev)["devices"]], "marked as connected by that request")
        self.assertEqual(len(before["changes"]) + (1 if st == 200 else 0), len(self.status(self.full_dev)["changes"]))
        file = self.export()
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.status(self.full_dev)["changes"][0]["area"], "System")

    def test_nothing_new_is_in_the_settings_file(self):
        before = self.on_disk()
        for d in (self.full_dev, self.live_dev, self.view_dev):
            self.status(d)
            self.h("POST", "/api/mix", {"duration": 1.5}, d)
        self.assertNotIn("presence", self.settings.data)
        self.assertNotIn(b"presence", self.on_disk())
        self.assertEqual(self.on_disk().count(b"last_seen"), before.count(b"last_seen"))


if __name__ == "__main__":
    unittest.main()
