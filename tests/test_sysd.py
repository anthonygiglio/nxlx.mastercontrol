# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from mctl import sysd
from tests.test_server import ServerBase


class FakeRun:
    def __init__(self, synced="no", fail_set=False, ntp="yes", show_fails=False, ntp_on_fails=False, dry_run_fails=False):
        self.calls = []
        self.synced, self.fail_set, self.ntp = synced, fail_set, ntp
        self.show_fails, self.ntp_on_fails, self.dry_run_fails = show_fails, ntp_on_fails, dry_run_fails

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out, code = "", 0
        if argv[:2] == ["timedatectl", "show"]:
            if self.show_fails:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="timed out")
            out = (self.synced if "NTPSynchronized" in argv[2] else self.ntp) + "\n"
        if argv[:3] == ["timedatectl", "set-ntp", "false"]:
            self.ntp = "no"
        if argv[:3] == ["timedatectl", "set-ntp", "true"] and not self.ntp_on_fails:
            self.ntp = "yes"
        if "set-time" in argv and self.fail_set:
            code, out = 1, "Failed to set time"
        if "--dry-run" in argv and self.dry_run_fails:
            code, out = 1, "Access denied"
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


class ServiceTest(unittest.TestCase):
    def make(self, now=1790000000, **kw):
        import tempfile
        self.run = FakeRun(**kw)
        self.scheduled = []
        self.dir = tempfile.mkdtemp()
        return sysd.SysService(runner=self.run, schedule=lambda d, fn: self.scheduled.append((d, fn)), log=lambda *_: None,
                               rundir=self.dir, now=lambda: now)

    def test_reboot_and_poweroff_are_checked_first_answer_then_run(self):
        s = self.make()
        self.assertEqual(s.handle({"cmd": "reboot"}), {"ok": True, "reboot": True})
        self.assertEqual(self.run.calls, [["systemctl", "--dry-run", "reboot"]])   # asked first, not done yet
        delay, fn = self.scheduled[0]
        self.assertGreaterEqual(delay, 1.0)
        fn()
        self.assertEqual(self.run.calls[-1], ["systemctl", "reboot"])
        self.assertTrue(s.handle({"cmd": "poweroff"})["already"])                     # a second request does not queue another

    def backups(self, names=("settings-1.0.0-1790000000.json", "settings-1.1.0-1790000100.json")):
        state = tempfile.mkdtemp()
        folder = os.path.join(state, "backups")
        os.makedirs(folder)
        for n in names:
            with open(os.path.join(folder, n), "w") as f:
                f.write("{}")
        s = self.make()
        s.state_dir = state
        return s, folder

    def test_clear_update_backups_removes_the_settings_copies_and_nothing_else(self):
        s, folder = self.backups()
        other = os.path.join(folder, "notes.txt")
        with open(other, "w") as f:
            f.write("keep")
        os.makedirs(os.path.join(folder, "settings-9.9.9-1.json"))              # a folder with a matching name stays
        outside = os.path.join(os.path.dirname(folder), "settings-x-1.json")
        with open(outside, "w") as f:
            f.write("keep")
        os.symlink(outside, os.path.join(folder, "settings-2.0.0-5.json"))     # a link stays, and so does what it points at
        self.assertEqual(s.handle({"cmd": "clear_update_backups"}), {"ok": True, "removed": 2})
        self.assertEqual(sorted(os.listdir(folder)), ["notes.txt", "settings-2.0.0-5.json", "settings-9.9.9-1.json"])
        self.assertTrue(os.path.exists(outside))
        self.assertEqual(s.handle({"cmd": "clear_update_backups"}), {"ok": True, "removed": 0})      # again: nothing to do

    def test_clear_update_backups_takes_no_name_from_the_caller(self):
        s, folder = self.backups()
        victim = os.path.join(os.path.dirname(folder), "settings.json")
        with open(victim, "w") as f:
            f.write("{}")
        for extra in ({"path": victim}, {"name": "../settings.json"}, {"folder": "/"}, {"dir": os.path.dirname(folder)}):
            msg = dict(extra, cmd="clear_update_backups")
            self.assertTrue(s.handle(msg)["ok"])
        self.assertTrue(os.path.exists(victim))
        self.assertEqual(os.listdir(folder), [])                               # only the fixed folder was emptied

    def test_clear_update_backups_with_no_folder_or_a_linked_folder(self):
        s = self.make()
        s.state_dir = tempfile.mkdtemp()
        self.assertEqual(s.handle({"cmd": "clear_update_backups"}), {"ok": True, "removed": 0})
        target = tempfile.mkdtemp()
        with open(os.path.join(target, "settings-1.0.0-1.json"), "w") as f:
            f.write("{}")
        os.symlink(target, os.path.join(s.state_dir, "backups"))
        r = s.handle({"cmd": "clear_update_backups"})
        self.assertFalse(r["ok"])
        self.assertEqual(os.listdir(target), ["settings-1.0.0-1.json"])

    def test_clear_update_backups_says_when_a_file_stayed(self):
        s, folder = self.backups()
        real = os.unlink

        def stubborn(path, *a, **k):
            if path.endswith("1.1.0-1790000100.json"):
                raise PermissionError(1, "Operation not permitted")
            return real(path, *a, **k)
        with mock.patch("os.unlink", stubborn):
            r = s.handle({"cmd": "clear_update_backups"})
        self.assertFalse(r["ok"])
        self.assertIn("1 update backup could not be removed", r["error"])
        self.assertEqual(r["removed"], 1)

    def test_a_refusal_is_reported_not_swallowed(self):
        s = self.make(dry_run_fails=True)
        r = s.handle({"cmd": "poweroff"})
        self.assertFalse(r["ok"])
        self.assertIn("refused", r["error"])
        self.assertEqual(self.scheduled, [])

    def test_set_time_only_when_the_clock_is_not_from_the_network(self):
        s = self.make(synced="yes")
        self.assertFalse(s.handle({"cmd": "set_time", "epoch": 1790000000})["ok"])
        self.assertNotIn("set-time", [a for c in self.run.calls for a in c])

    def test_an_unreadable_status_refuses_instead_of_overriding(self):
        s = self.make(show_fails=True)
        r = s.handle({"cmd": "set_time", "epoch": 1790000000})
        self.assertFalse(r["ok"])
        self.assertNotIn("set-time", [a for c in self.run.calls for a in c])

    def test_set_time_sets_utc_and_turns_network_time_back_on_and_checks_it(self):
        s = self.make()
        self.assertTrue(s.handle({"cmd": "set_time", "epoch": 1790000000})["ok"])
        acts = [c for c in self.run.calls if c[0] == "timedatectl" and c[1] != "show"]
        self.assertEqual(acts[0], ["timedatectl", "set-ntp", "false"])
        self.assertEqual(acts[1], ["timedatectl", "--adjust-system-clock", "set-time", "2026-09-21 14:13:20 UTC"])
        self.assertEqual(acts[2], ["timedatectl", "set-ntp", "true"])
        self.assertEqual(self.run.ntp, "yes")
        self.assertFalse(os.path.exists(os.path.join(self.dir, sysd.MARKER)))

    def test_network_time_that_was_off_stays_off(self):
        s = self.make(ntp="no")
        self.assertTrue(s.handle({"cmd": "set_time", "epoch": 1790000000})["ok"])
        self.assertNotIn(["timedatectl", "set-ntp", "true"], self.run.calls)

    def test_if_network_time_cannot_be_switched_back_on_it_says_so_and_the_marker_stays(self):
        s = self.make(ntp_on_fails=True)
        r = s.handle({"cmd": "set_time", "epoch": 1790000000})
        self.assertFalse(r["ok"])
        self.assertIn("network time could not be switched back on", r["error"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, sysd.MARKER)))
        self.run.ntp_on_fails = False
        self.assertTrue(s.recover())                                              # the next start switches it back on
        self.assertFalse(os.path.exists(os.path.join(self.dir, sysd.MARKER)))

    def test_a_failed_set_still_switches_network_time_back_on(self):
        s = self.make(fail_set=True)
        self.assertFalse(s.handle({"cmd": "set_time", "epoch": 1790000000})["ok"])
        self.assertEqual(self.run.ntp, "yes")

    def test_a_clock_that_moves_again_is_reported(self):
        s = self.make(now=1790000000 + 86400 * 400)                               # timesyncd put a saved future time back
        r = s.handle({"cmd": "set_time", "epoch": 1790000000})
        self.assertFalse(r["ok"])
        self.assertIn("moved again", r["error"])

    def test_far_future_dates_are_refused(self):
        s = self.make()
        self.assertFalse(s.handle({"cmd": "set_time", "epoch": 4000000000})["ok"])   # 2096

    def test_status_is_cached_briefly(self):
        s = self.make()
        s.handle({"cmd": "status"})
        s.handle({"cmd": "status"})
        self.assertEqual(len([c for c in self.run.calls if c[:2] == ["timedatectl", "show"]]), 1)

    def test_bad_requests(self):
        s = self.make()
        for m in ([], "reboot", {"cmd": "shell", "argv": ["rm", "-rf", "/"]}, {"cmd": "set_time", "epoch": 5},
                  {"cmd": "set_time", "epoch": 5_000_000_000}, {"cmd": "set_time", "epoch": 2100000000}, {"cmd": "set_time", "epoch": "1790000000"},
                  {"cmd": "set_time", "epoch": True}, {"cmd": "set_time", "epoch": 1.79e9}, {"cmd": "halt"}):
            self.assertFalse(s.handle(m)["ok"], m)
        self.assertEqual([c for c in self.run.calls if c[0] == "systemctl"], [])
        self.assertEqual(self.scheduled, [])

    def test_only_the_two_fixed_programs_can_ever_run(self):
        s = self.make()
        with self.assertRaises(AssertionError):
            s._run(["sh", "-c", "id"])


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.sent = []

        class Client:
            def request(inner, message):
                self.sent.append(message)
                if message["cmd"] == "status":
                    return {"ok": True, "clock_from_network": False, "now": 1790000000}
                return {"ok": True}
        self.api.sysd = Client()

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_reboot_and_poweroff_need_a_typed_confirmation(self):
        self.assertEqual(self.post("/api/system/reboot", {})[0], 400)
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "poweroff"})[0], 400)
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "reboot"})[0], 200)
        self.assertEqual(self.post("/api/system/poweroff", {"confirm": "poweroff"})[0], 200)
        self.assertEqual([m["cmd"] for m in self.sent if m["cmd"] != "status"], ["reboot", "poweroff"])

    def test_only_full_devices(self):
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        for path, body in (("/api/system/reboot", {"confirm": "reboot"}), ("/api/system/poweroff", {"confirm": "poweroff"}),
                           ("/api/system/clock", {"epoch": 1790000000})):
            self.assertEqual(self.post(path, body, token=live)[0], 403, path)
        self.assertEqual(self.sent, [])

    def test_clock_and_system_info(self):
        self.assertEqual(self.post("/api/system/clock", {"epoch": "now"})[0], 400)
        self.assertEqual(self.post("/api/system/clock", {"epoch": 1790000000})[0], 200)
        self.assertIn({"cmd": "set_time", "epoch": 1790000000}, self.sent)
        body = self.call("GET", "/api/system", token=self.full)[1]
        self.assertEqual((body["clock"]["clock_from_network"], body["system_actions"]), (False, True))

    def test_no_helper(self):
        self.api.sysd = None
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "reboot"})[0], 503)
        self.assertEqual(self.call("GET", "/api/system", token=self.full)[1]["system_actions"], False)

    def test_writeback_connectors_are_not_screens(self):
        from unittest import mock
        with mock.patch("mctl.api.hardware.drm_connectors", return_value=[
                {"connector": "HDMI-A-1", "status": "connected", "modes": []},
                {"connector": "Writeback-1", "status": "unknown", "modes": []}]):
            body = self.call("GET", "/api/system", token=self.full)[1]
        self.assertEqual([s["connector"] for s in body["screens"]], ["HDMI-A-1"])


if __name__ == "__main__":
    unittest.main()
