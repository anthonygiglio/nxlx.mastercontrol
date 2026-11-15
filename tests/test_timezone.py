# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's time zone (J-129): the name check, the helper's action, the Owner's route, the first-pairing set and the
schedule's report. The role table rows are in tests/test_roles.py and tests/test_carried_roles.py."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401  (tests/test_lockrank.py checks that every test module imports the package)
from pvj import sysd, tz
from tests.test_server import ServerBase

HAVE_ZONES = os.path.isfile(os.path.join(tz.ZONEINFO, "America", "Regina"))


@unittest.skipUnless(HAVE_ZONES, "needs the system's zoneinfo")
class NameCheck(unittest.TestCase):
    def test_real_zones_pass(self):
        for name in ("America/Regina", "UTC", "Etc/UTC", "Europe/London", "America/Argentina/Buenos_Aires", "Etc/GMT+5"):
            self.assertTrue(tz.valid_zone(name), name)

    def test_empty_unknown_and_tricks_are_refused(self):
        for name in ("", None, 5, ["UTC"], "Not/AZone", "America", "../etc/passwd", "../etc", "America/../Europe/London",
                     "/etc/passwd", "/usr/share/zoneinfo/UTC", "America//Regina", "America/Regina/", " America/Regina", "America/Regina\n",
                     "UTC; reboot", "a b", "UTC\x00", ".", "..", "America/.Regina", "posix/UTC", "right/UTC", "x" * 100,
                     "tzdata.zi", "zone.tab", "leapseconds", "localtime", "posixrules", "Factory", "posixrules/x"):
            self.assertFalse(tz.valid_zone(name), repr(name))

    def test_names_that_are_links_to_the_systems_own_files_or_not_places_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("localtime", "posixrules", "Factory", "lowercase", "Good"):
                with open(os.path.join(root, name), "wb") as f:
                    f.write(b"TZif2" + b"\0" * 20)
            self.assertTrue(tz.valid_zone("Good", root))
            for name in ("localtime", "posixrules", "Factory", "lowercase"):
                self.assertFalse(tz.valid_zone(name, root), name)

    def test_a_link_out_of_the_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            with open(os.path.join(other, "Evil"), "wb") as f:
                f.write(b"TZif2" + b"\0" * 20)
            os.symlink(os.path.join(other, "Evil"), os.path.join(root, "Evil"))
            with open(os.path.join(root, "Fake"), "wb") as f:
                f.write(b"not a zone")
            with open(os.path.join(root, "Good"), "wb") as f:
                f.write(b"TZif2" + b"\0" * 20)
            self.assertFalse(tz.valid_zone("Evil", root))
            self.assertFalse(tz.valid_zone("Fake", root))
            self.assertTrue(tz.valid_zone("Good", root))

    def test_the_list_holds_real_zones_only(self):
        zones = tz.zones()
        self.assertIn("America/Regina", zones)
        self.assertIn("UTC", zones)
        self.assertFalse([z for z in zones if z.startswith(("posix", "right"))])
        self.assertTrue(all(tz.valid_zone(z) for z in zones[:50]))

    def test_current_name_reads_the_link_then_the_old_file_then_tz(self):
        with tempfile.TemporaryDirectory() as d:
            link, old = os.path.join(d, "localtime"), os.path.join(d, "timezone")
            self.assertIsNone(tz.current_name(link, old, {}))
            self.assertEqual(tz.current_name(link, old, {"TZ": ":America/Regina"}), "America/Regina")
            with open(old, "w") as f:
                f.write("Europe/London\n")
            self.assertEqual(tz.current_name(link, old, {}), "Europe/London")
            os.symlink("/usr/share/zoneinfo/America/Regina", link)
            self.assertEqual(tz.current_name(link, old, {}), "America/Regina")


class FakeTimedate:
    """timedatectl as the helper calls it, keeping a zone."""

    def __init__(self, zone="Etc/UTC", fail_set=False, lies=False, list_fails=False, empty_list=False):
        self.zone, self.fail_set, self.lies, self.list_fails, self.empty_list = zone, fail_set, lies, list_fails, empty_list
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out, code = "", 0
        if argv[:2] == ["timedatectl", "list-timezones"]:
            if self.list_fails:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
            out = "" if self.empty_list else "\n".join(["America/Regina", "Europe/London", "UTC"]) + "\n"
        elif argv[:2] == ["timedatectl", "set-timezone"]:
            if self.fail_set:
                code, out = 1, "Access denied"
            elif not self.lies:
                self.zone = argv[-1]
        elif argv[:2] == ["timedatectl", "show"]:
            out = self.zone + "\n"
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


@unittest.skipUnless(HAVE_ZONES, "needs the system's zoneinfo")
class HelperAction(unittest.TestCase):
    def make(self, **kw):
        self.run = FakeTimedate(**kw)
        self.logged = []
        return sysd.SysService(runner=self.run, schedule=lambda d, fn: None, log=self.logged.append, rundir=tempfile.mkdtemp())

    def sets(self):
        return [c for c in self.run.calls if c[:2] == ["timedatectl", "set-timezone"]]

    def test_sets_the_zone_and_reads_it_back(self):
        s = self.make()
        self.assertEqual(s.handle({"cmd": "set_timezone", "zone": "America/Regina"}), {"ok": True, "timezone": "America/Regina"})
        self.assertEqual(self.sets(), [["timedatectl", "set-timezone", "--", "America/Regina"]])      # "--": a name is never an option
        self.assertIn(["timedatectl", "show", "--property=Timezone", "--value"], self.run.calls)
        self.assertEqual(s.handle({"cmd": "timezone"}), {"ok": True, "timezone": "America/Regina"})

    def test_refuses_empty_unknown_and_path_tricks_without_running_anything(self):
        s = self.make()
        for zone in ("", None, 3, "Not/AZone", "../etc", "../etc/passwd", "/etc/passwd", "UTC; reboot", "America/../UTC", {"zone": "UTC"}):
            r = s.handle({"cmd": "set_timezone", "zone": zone})
            self.assertFalse(r["ok"], repr(zone))
        self.assertEqual(s.handle({"cmd": "set_timezone"})["ok"], False)
        self.assertEqual(self.run.calls, [])

    def test_a_zone_timedatectl_does_not_list_is_refused(self):
        s = self.make()
        self.assertFalse(s.handle({"cmd": "set_timezone", "zone": "Asia/Tokyo"})["ok"])         # a real file, not in the fake list
        self.assertEqual(self.sets(), [])

    def test_a_list_that_cannot_be_asked_or_is_empty_allows_nothing(self):
        for kw in ({"list_fails": True}, {"empty_list": True}):
            s = self.make(**kw)
            self.assertFalse(s.handle({"cmd": "set_timezone", "zone": "America/Regina"})["ok"], kw)
            self.assertEqual(self.sets(), [], kw)

    def test_a_failure_to_set_or_a_wrong_read_back_is_reported(self):
        self.assertFalse(self.make(fail_set=True).handle({"cmd": "set_timezone", "zone": "America/Regina"})["ok"])
        r = self.make(lies=True).handle({"cmd": "set_timezone", "zone": "America/Regina"})
        self.assertFalse(r["ok"])
        self.assertIn("reading it back", r["error"])

    def test_only_the_fixed_programs_still_run(self):
        with self.assertRaises(AssertionError):
            self.make()._run(["sh", "-c", "id"])


class FakeSysd:
    def __init__(self, zone="Etc/UTC", fail=False):
        self.sent, self.zone, self.fail = [], zone, fail

    def request(self, message):
        self.sent.append(message)
        if message["cmd"] == "set_timezone":
            if self.fail:
                return {"ok": False, "error": "refused"}
            self.zone = message["zone"]
            return {"ok": True, "timezone": self.zone}
        if message["cmd"] == "timezone":
            return {"ok": True, "timezone": self.zone}
        return {"ok": True, "clock_from_network": True, "now": 1790000000}

    def sets(self):
        return [m["zone"] for m in self.sent if m["cmd"] == "set_timezone"]


@unittest.skipUnless(HAVE_ZONES, "needs the system's zoneinfo")
class Route(ServerBase):
    def setUp(self):
        super().setUp()
        self.sysd = self.api.sysd = FakeSysd()
        p = mock.patch("pvj.api.tz_mod.current_name", lambda *a, **k: self.sysd.zone)
        p.start()
        self.addCleanup(p.stop)
        t = mock.patch("pvj.api.time.tzset", lambda: None)       # the real one would re-read this machine's own zone
        t.start()
        self.addCleanup(t.stop)

    def pair(self, body=None):
        return self.call("POST", "/api/pair", dict({"pin": self.pin, "name": "t"}, **(body or {})))

    def owner(self):
        return self.pair()[1]["token"]

    def other(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "x", "role": role}, token=self.owner())[1]["token"]

    # --- the Owner sets it ---
    def test_owner_sets_the_zone_and_it_is_confirmed(self):
        tok = self.owner()
        st, body = self.call("GET", "/api/timezone", token=tok)[:2]
        self.assertEqual((st, body["zone"], body["confirmed"], body["can_set"]), (200, "Etc/UTC", False, True))
        self.assertIn("America/Regina", body["zones"])
        st, body = self.call("POST", "/api/timezone", {"zone": "America/Regina"}, token=tok)[:2]
        self.assertEqual((st, body["zone"], body["confirmed"]), (200, "America/Regina", True))
        self.assertEqual(self.sysd.sets(), ["America/Regina"])
        self.assertTrue(self.settings.data["timezone"]["confirmed"])

    def test_owner_can_say_the_zone_it_is_on_is_right(self):
        tok = self.owner()
        self.assertTrue(self.call("POST", "/api/timezone", {"keep": True}, token=tok)[1]["confirmed"])
        self.assertEqual(self.sysd.sets(), [])

    def test_bad_names_are_a_400_and_reach_nothing(self):
        tok = self.owner()
        for body in ({}, {"zone": ""}, {"zone": "../etc"}, {"zone": "Not/AZone"}, {"zone": 5}, {"zone": "UTC; reboot"}):
            self.assertEqual(self.call("POST", "/api/timezone", body, token=tok)[0], 400, body)
        self.assertEqual(self.sysd.sets(), [])
        self.assertNotIn("timezone", self.settings.data)

    def test_a_refusal_by_the_helper_is_not_remembered_as_confirmed(self):
        tok = self.owner()
        self.sysd.fail = True
        self.assertEqual(self.call("POST", "/api/timezone", {"zone": "America/Regina"}, token=tok)[0], 409)
        self.assertNotIn("timezone", self.settings.data)

    def test_no_helper_cannot_set_but_can_report(self):
        tok = self.owner()
        self.api.sysd = None
        self.assertEqual(self.call("POST", "/api/timezone", {"zone": "America/Regina"}, token=tok)[0], 503)
        self.assertFalse(self.call("GET", "/api/timezone", token=tok)[1]["can_set"])

    def test_not_an_operator_or_a_guest(self):
        for role in ("live", "view"):
            tok = self.other(role)
            self.assertEqual(self.call("POST", "/api/timezone", {"zone": "America/Regina"}, token=tok)[0], 403, role)
            self.assertEqual(self.call("GET", "/api/timezone", token=tok)[0], 403, role)
        self.assertEqual(self.sysd.sets(), [])

    def test_not_over_remote_support(self):
        tok = self.owner()
        with mock.patch.object(self.api.support, "is_remote", lambda c: True):
            self.assertEqual(self.call("POST", "/api/timezone", {"zone": "America/Regina"}, token=tok)[0], 403)
        self.assertEqual(self.sysd.sets(), [])

    # --- the first pairing sets it ---
    def test_the_first_owner_pairing_sets_a_node_still_on_utc(self):
        st, body = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertEqual(body["timezone"], {"zone": "America/Regina", "was": "Etc/UTC"})
        self.assertEqual(self.sysd.sets(), ["America/Regina"])
        self.assertTrue(self.settings.data["timezone"]["confirmed"])

    def test_a_confirmed_zone_is_never_changed_by_a_pairing(self):
        self.owner()
        self.call("POST", "/api/timezone", {"keep": True}, token=self.owner())
        self.assertNotIn("timezone", self.pair({"tz": "America/Regina"})[1])
        self.assertEqual(self.sysd.sets(), [])

    def test_a_kept_zone_is_never_changed_by_a_later_pairing(self):
        tok = self.owner()
        self.call("POST", "/api/timezone", {"keep": True}, token=tok)       # what "Not now" sends
        self.assertTrue(self.settings.data["timezone"]["confirmed"])
        self.settings.data["devices"] = []
        self.assertNotIn("timezone", self.pair({"tz": "America/Regina"})[1])
        self.assertEqual(self.sysd.sets(), [])

    def test_a_pairing_after_the_auto_set_does_not_change_it_again(self):
        self.pair({"tz": "America/Regina"})
        self.sysd.zone = "Etc/UTC"                                   # even if something put it back
        self.assertNotIn("timezone", self.pair({"tz": "Europe/London"})[1])
        self.assertEqual(self.sysd.sets(), ["America/Regina"])

    def test_a_node_not_on_utc_is_left_alone(self):
        self.sysd.zone = "Europe/London"
        self.assertNotIn("timezone", self.pair({"tz": "America/Regina"})[1])
        self.assertEqual(self.sysd.sets(), [])

    def test_a_device_on_utc_or_with_no_or_a_bad_zone_changes_nothing(self):
        for tzval in ("Etc/UTC", "UTC", "", None, "../etc", "Not/AZone", 7):
            body = {"tz": tzval} if tzval is not None else {}
            st, out = self.pair(body)[:2]
            self.assertEqual(st, 200, tzval)
            self.assertNotIn("timezone", out)
        self.assertEqual(self.sysd.sets(), [])

    def test_a_guest_or_an_operator_pairing_never_sets_it(self):
        for role in ("view", "live"):
            code = self.call("POST", "/api/access/code", {"role": role}, token=self.owner())[1]["codes"][0]["code"]
            st, out = self.call("POST", "/api/pair", {"pin": code, "name": "g", "tz": "America/Regina"})[:2]
            self.assertEqual(st, 200, role)
            self.assertNotIn("timezone", out)
        self.assertEqual(self.sysd.sets(), [])

    def test_a_pairing_over_remote_support_never_sets_it(self):
        with mock.patch.object(self.api.support, "is_remote", lambda c: True):
            st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertNotIn("timezone", out)
        self.assertEqual(self.sysd.sets(), [])

    def test_a_failure_to_set_never_stops_the_pairing_and_is_said(self):
        self.sysd.fail = True
        st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertIn("token", out)
        self.assertIn("could not be set to America/Regina", out["timezone"]["error"])
        self.assertNotIn("zone", out["timezone"])
        self.assertNotIn("timezone", self.settings.data)

    def test_a_zone_set_but_not_saved_says_so_at_pairing_and_on_the_route(self):
        real_save = self.settings.save

        def full_disk(*a, **k):                     # the device is saved; the zone's own flag is what cannot be
            if "timezone" in self.settings.data:
                raise OSError("disk full")
            return real_save(*a, **k)
        with mock.patch.object(self.settings, "save", full_disk):
            st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertEqual(out["timezone"]["zone"], "America/Regina")
        self.assertIn("could not save", out["timezone"]["warning"])
        tok = out["token"]
        with mock.patch.object(self.settings, "save", full_disk):
            st, body =self.call("POST", "/api/timezone", {"zone": "Europe/London"}, token=tok)[:2]
            self.assertEqual((st, body["zone"]), (200, "Europe/London"))
            self.assertIn("could not save", body["warning"])
            self.assertIn("could not save", self.call("POST", "/api/timezone", {"keep": True}, token=tok)[1]["warning"])

    def test_a_node_that_already_has_an_owner_is_only_offered_the_change(self):
        self.owner()                                              # an Owner device is paired already (a node upgraded from UTC)
        st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertNotIn("timezone", out)
        self.assertEqual(self.sysd.sets(), [])
        self.assertNotIn("timezone", self.settings.data)

    def test_a_node_with_schedule_entries_is_only_offered_the_change(self):
        self.settings.data["schedule"] = {"enabled": True, "entries": [{"id": "abcd1234", "time": "20:00", "days": [0], "action": "stop"}]}
        st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertNotIn("timezone", out)
        self.assertEqual(self.sysd.sets(), [])
        self.assertNotIn("timezone", self.settings.data)

    def test_other_devices_do_not_count_as_owners(self):
        self.auth._add_device("op", "live", via="code")           # an Operator and a Guest on the node do not stop the first Owner
        self.auth._add_device("guest", "view", via="code")
        st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(out["timezone"]["zone"], "America/Regina")

    def test_no_helper_no_set_at_pairing(self):
        self.api.sysd = None
        st, out = self.pair({"tz": "America/Regina"})[:2]
        self.assertEqual(st, 200)
        self.assertNotIn("timezone", out)


@unittest.skipUnless(HAVE_ZONES, "needs the system's zoneinfo")
class ScheduleReport(unittest.TestCase):
    def test_the_status_names_the_zone_and_the_offset(self):
        from pvj import scheduler
        from pvj.settings import Settings

        class Reg:
            def enabled(self, name):
                return True
        s = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.load()
        with mock.patch("pvj.scheduler.tz_mod.current_name", lambda *a, **k: "America/Regina"):
            st = scheduler.Scheduler(None, s, Reg()).status()
        self.assertEqual(st["zone"], "America/Regina")
        self.assertIsInstance(st["utc_offset_minutes"], int)
        self.assertTrue(st["timezone"])


if __name__ == "__main__":
    unittest.main()
