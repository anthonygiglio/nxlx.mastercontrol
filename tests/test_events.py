# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The event log (pvj/events.py, D125), the helper's journal command (pvj/sysd.py) and the diagnostics that use them."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import events, sysd
from tests.test_boxcare import Base as CareBase, PIN


class Clock:
    def __init__(self, t=1790000000):
        self.t = t

    def __call__(self):
        return self.t


class LogTest(unittest.TestCase):
    def make(self, **kw):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "events.json")
        self.clock = Clock()
        self.scheduled = []
        self.lines = []
        return events.EventLog(self.path, now=self.clock, schedule=lambda d, fn: self.scheduled.append((d, fn)),
                               log=self.lines.append, boot_id=kw.pop("boot_id", lambda: "boot-1"), **kw)

    def on_disk(self):
        with open(self.path) as f:
            return json.load(f)

    def test_a_crash_type_event_is_written_at_once(self):
        log = self.make()
        for kind in ("boot", "player", "renderer"):
            log.add(kind, "something happened to the %s" % kind)
            self.assertEqual(self.on_disk()["events"][-1]["kind"], kind, kind)
        log.add("update", "Update failed: x", urgent=True)
        self.assertEqual(self.on_disk()["events"][-1]["kind"], "update")
        self.assertEqual(self.scheduled, [], "nothing waits")

    def test_the_others_are_batched_to_once_a_minute(self):
        log = self.make()
        log.add("signin", "A sign-in was refused: wrong PIN.", who="192.168.0.9")
        log.add("network", "The network settings were changed.")
        log.add("scene", "A scheduled scene ran.")
        self.assertFalse(os.path.exists(self.path), "not written yet")
        self.assertEqual(len(self.scheduled), 1, "one write is waiting, however many come")
        self.assertEqual(self.scheduled[0][0], events.BATCH_SECONDS)
        self.scheduled[0][1]()
        self.assertEqual([e["kind"] for e in self.on_disk()["events"]], ["signin", "network", "scene"])
        log.add("projector", "A projector did not answer.", who="Left")
        self.assertEqual(len(self.scheduled), 2, "the next one starts a new wait")
        self.assertEqual(len(self.on_disk()["events"]), 3)
        log.flush()
        self.assertEqual(len(self.on_disk()["events"]), 4, "a stop writes what waits")

    def test_it_never_holds_more_than_500_and_the_file_is_the_same(self):
        log = self.make()
        for i in range(620):
            self.clock.t += 400            # beyond the dedupe time: each its own line
            log.add("player", "event number %d" % i)
        self.assertEqual(len(log.events()), 500)
        saved = self.on_disk()["events"]
        self.assertEqual(len(saved), 500)
        self.assertEqual(saved[0]["text"], "event number 120")
        self.assertEqual(saved[-1]["text"], "event number 619")
        self.assertEqual(log.events()[0]["text"], "event number 619", "newest first")
        again = events.EventLog(self.path, now=self.clock)
        self.assertEqual(len(again.events()), 500)
        small = self.make(cap=5)
        for i in range(9):
            self.clock.t += 400
            small.add("player", "n%d" % i)
        self.assertEqual([e["text"] for e in self.on_disk()["events"]], ["n4", "n5", "n6", "n7", "n8"])

    def test_the_same_event_again_is_one_line_with_a_count_and_few_writes(self):
        log = self.make()
        log.add("player", "The player restarted by itself.")
        writes = []
        real = log._write
        log._write = lambda: (writes.append(1), real())
        for _ in range(5):
            self.clock.t += 1
            log.add("player", "The player restarted by itself.")
        rows = log.events()
        self.assertEqual((len(rows), rows[0]["n"]), (1, 6))
        self.assertEqual(writes, [], "within ten seconds the count waits for the next write")
        self.assertEqual(len(self.scheduled), 1)
        self.clock.t += 30
        log.add("player", "The player restarted by itself.")
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.on_disk()["events"][0]["n"], 7)
        self.clock.t += 400
        log.add("player", "The player restarted by itself.")
        self.assertEqual(len(log.events()), 2, "after five minutes it is a new line")
        log.add("player", "The player restarted by itself.", who="x")
        self.assertEqual(len(log.events()), 3, "another who is another line")

    def test_text_is_one_short_plain_line_and_a_bad_kind_is_nothing(self):
        log = self.make()
        log.add("player", "line one\n\x1b[31mred\x00\r\n   " + "x" * 400, who="Dev\nice " + "y" * 200)
        e = log.events()[0]
        self.assertNotRegex(e["text"] + e["who"], r"[\x00-\x1f\x7f]")
        self.assertLessEqual(len(e["text"]), events.TEXT_MAX)
        self.assertLessEqual(len(e["who"]), events.WHO_MAX)
        log.add("nonsense", "x")
        log.add("player", "")
        log.add("player", None)
        self.assertEqual(len(log.events()), 1)

    def test_names_and_addresses_are_for_the_owner_only(self):
        log = self.make()
        log.add("signin", "A sign-in was refused: wrong PIN.", who="192.168.0.9")
        log.add("projector", "A projector did not answer.", who="Left wall")
        self.assertEqual([e["who"] for e in log.events()], ["Left wall", "192.168.0.9"])
        for e in log.events(who=False):
            self.assertNotIn("who", e)
        self.assertNotIn("192.168.0.9", "\n".join(log.line(e) for e in log.events(who=False)))
        self.assertIn("(3 times)", log.line({"t": 1790000000, "kind": "player", "text": "x", "n": 3}))

    def test_a_damaged_file_is_ignored_and_a_write_that_fails_goes_on_in_memory(self):
        log = self.make()
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(events.EventLog(self.path).events(), [])
        with open(self.path, "w") as f:
            json.dump({"events": [{"t": 1, "kind": "player", "text": "ok"}, {"t": "x", "kind": "player", "text": "no"},
                                  {"t": 2, "kind": "bogus", "text": "no"}, "junk", {"t": 3, "kind": "boot", "text": ""}]}, f)
        self.assertEqual([e["text"] for e in events.EventLog(self.path).events()], ["ok"])
        bad = events.EventLog(os.path.join(self.dir, "nowhere", "events.json"), log=self.lines.append)
        bad.add("player", "still here")
        self.assertEqual(bad.events()[0]["text"], "still here")
        self.assertEqual(len([l for l in self.lines if "could not be written" in l]), 1)
        self.assertFalse(bad.state()["kept"])

    def test_a_hostile_file_never_stops_the_panel(self):
        self.make()
        good = {"t": 5, "kind": "player", "text": "kept"}
        for text in ('{"events": "no"}', '{"events": 5}', '{"events": null}', '{"events": {"a": 1}}', "[1, 2]", "5", "null", '"x"',
                     '{"events": [1, "x", null, [], {"t": 1e400, "kind": "player", "text": "inf"}, {"t": NaN, "kind": "player", "text": "nan"},'
                     ' {"t": -Infinity, "kind": "player", "text": "ninf"}, {"t": -5, "kind": "player", "text": "neg"},'
                     ' {"t": 1e30, "kind": "player", "text": "huge"}, {"t": true, "kind": "player", "text": "bool"},'
                     ' {"t": 5, "kind": ["player"], "text": "unhashable"}, {"t": 5, "kind": "player", "text": ["x"]},'
                     ' {"t": 5, "kind": "player", "text": "n", "n": "many", "who": 5}, {"t": 5, "kind": "player", "text": "kept"}],'
                     ' "meta": {"boot": ["x"], "update_at": NaN}}',
                     '{"events": [' + '[' * 100000 + ']}', '\x00\x01'):
            with open(self.path, "w") as f:
                f.write(text)
            log = events.EventLog(self.path, now=self.clock)
            texts = [e["text"] for e in log.events()]
            self.assertTrue(set(texts) <= {"kept", "n"}, (text[:40], texts))
            log.add("player", "and it still works")
            self.assertEqual(log.events()[0]["text"], "and it still works")
        with open(self.path, "w") as f:
            f.write('{"events": [{"t": 5, "kind": "player", "text": "x", "n": 99999999}], "meta": {"boot": "b", "update_at": 7.9}}')
        log = events.EventLog(self.path)
        self.assertEqual((log.events()[0]["n"], log.meta), (9999, {"boot": "b", "update_at": 7}))

    def test_a_new_boot_id_is_a_node_restart_written_at_once(self):
        log = self.make()
        log.start()
        self.assertEqual(log.events(), [], "the first start is no restart")
        self.assertEqual(self.on_disk()["meta"]["boot"], "boot-1")
        again = events.EventLog(self.path, now=self.clock, boot_id=lambda: "boot-1")
        again.start()
        self.assertEqual(again.events(), [], "a panel restart on the same boot is none")
        after = events.EventLog(self.path, now=self.clock, boot_id=lambda: "boot-2")
        after.start()
        self.assertEqual([e["text"] for e in after.events()], ["The node restarted."])
        self.assertEqual(self.on_disk()["events"][0]["kind"], "boot")
        self.assertEqual(self.on_disk()["meta"]["boot"], "boot-2")

    def test_an_update_result_is_announced_once(self):
        log = self.make()
        self.assertTrue(log.update_seen(100))
        self.assertFalse(log.update_seen(100))
        self.assertFalse(log.update_seen("x"))
        self.assertFalse(log.update_seen(True))
        self.assertTrue(log.update_seen(101))
        log.flush()
        self.assertEqual(events.EventLog(self.path).meta["update_at"], 101, "kept across a restart")

    def test_the_note_says_when_the_disk_is_read_only(self):
        log = self.make()
        self.assertIn("survive a restart", log.state()["note"])
        ro = log.state(read_only_root=True)
        self.assertFalse(ro["kept"])
        self.assertIn("lost at a restart", ro["note"])


class JournalCommandTest(unittest.TestCase):
    def make(self, out="", code=0):
        self.calls = []

        def run(argv, **kw):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, code, stdout=out, stderr="boom" if code else "")
        return sysd.SysService(runner=run, log=lambda *_: None)

    def test_the_command_is_fixed_and_bounded(self):
        s = self.make("2026-11-07T10:00:00+0000 node pvj-web[1]: hello\n")
        r = s.handle({"cmd": "journal"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["lines"], ["2026-11-07T10:00:00+0000 node pvj-web[1]: hello"])
        argv = self.calls[0]
        self.assertEqual(argv[0], "journalctl")
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-u"], list(sysd.JOURNAL_UNITS))
        self.assertTrue(all(u.startswith("pvj-") for u in sysd.JOURNAL_UNITS))
        self.assertIn("--boot=0", argv)
        self.assertEqual(argv[argv.index("-n") + 1], "300")
        self.assertEqual(argv[argv.index("--since") + 1], "-24h")
        self.assertFalse([a for a in argv if "&" in a or ";" in a or " " in a], "never a shell")

    def test_the_numbers_from_the_panel_are_clamped_and_the_boot_is_this_or_the_one_before(self):
        for lines, hours, boot, want_n, want_since, want_boot in (
                (99999, 10 ** 9, 5, "500", "-168h", "--boot=0"), (0, 0, "x", "1", "-1h", "--boot=0"), (True, None, None, "300", "-24h", "--boot=0"),
                (float("nan"), float("inf"), 0, "300", "-168h", "--boot=0"), ("9", "9", -1, "300", None, "--boot=-1")):
            s = self.make("a\n")
            s.handle({"cmd": "journal", "lines": lines, "hours": hours, "boot": boot})
            argv = self.calls[0]
            self.assertEqual(argv[argv.index("-n") + 1], want_n, (lines, hours, boot))
            self.assertIn(want_boot, argv)
            if want_since:
                self.assertEqual(argv[argv.index("--since") + 1], want_since, (lines, hours, boot))
            else:
                self.assertNotIn("--since", argv, "the boot before has no window")

    def test_a_failure_is_an_answer_and_the_reply_fits_what_the_panel_reads(self):
        r = self.make("", code=1).handle({"cmd": "journal"})
        self.assertFalse(r["ok"])
        self.assertIn("could not be read", r["error"])
        out = "\n".join("2026-11-07T10:00:%02d+0000 node pvj-web[1]: %s" % (i % 60, "y" * 380) for i in range(500)) + "\n"
        r = self.make(out).handle({"cmd": "journal", "lines": 500})
        self.assertTrue(r["ok"])
        self.assertLessEqual(len(json.dumps(r)), 60000)
        self.assertGreater(r["cut"], 0)
        self.assertEqual(len(r["lines"]) + r["cut"], 500)
        self.assertTrue(r["lines"][-1].startswith("2026-11-07T10:00:"), "the newest are kept")
        self.assertTrue(all(len(l) <= sysd.JOURNAL_LINE_MAX for l in r["lines"]))

    def test_a_journalctl_failure_is_logged_by_the_helper_and_a_timeout_is_said(self):
        logged = []
        s = self.make("", code=1)
        s.log = logged.append
        s.handle({"cmd": "journal"})
        self.assertIn("journalctl failed: boom", logged[0])
        self.assertEqual(self.calls[0][0], "journalctl")

        def slow(argv, **kw):
            self.timeout = kw["timeout"]
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        s = sysd.SysService(runner=slow, log=logged.append)
        r = s.handle({"cmd": "journal"})
        self.assertEqual(self.timeout, 20)
        self.assertIn("did not answer within 20 seconds", r["error"])
        self.assertIn("did not answer within 20 seconds", logged[-1])

    def test_it_says_whether_the_journal_is_kept_and_whether_the_root_is_read_only(self):
        s = self.make("a\n")
        s.journal_dir = tempfile.mkdtemp()
        with mock.patch("pvj.rootfs.current_state", return_value="overlay"):
            r = s.journal()
        self.assertEqual((r["persistent"], r["read_only_root"]), (True, True))
        s.journal_dir = "/nonexistent-journal-dir"
        with mock.patch("pvj.rootfs.current_state", return_value="normal"):
            r = s.journal()
        self.assertEqual((r["persistent"], r["read_only_root"]), (False, False))


class FakeSysd:
    def __init__(self, now, before=None, fail=False):
        self.now, self.before, self.fail, self.asked = now, before, fail, []

    def request(self, message):
        self.asked.append(message)
        if self.fail:
            raise OSError("the system helper (pvj-sysd) is not running")
        if message.get("cmd") != "journal":
            return {"ok": True, "clock_from_network": True, "now": 1}
        return self.now if message.get("boot", 0) == 0 else (self.before or {"ok": False})


class ApiTest(CareBase):
    def setUp(self):
        super().setUp()
        self.auth.set_pin(PIN)
        self.events_path = os.path.join(tempfile.mkdtemp(), "events.json")
        self.clock = Clock()
        self.api.events = events.EventLog(self.events_path, now=self.clock, schedule=lambda d, fn: None)

    def test_only_the_owner_reads_the_events(self):
        self.api.event("signin", "A sign-in was refused: wrong PIN.", who="192.168.0.9")
        for dev in (self.view_dev, self.live_dev):
            self.assertEqual(self.h("GET", "/api/events", {}, dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/events", {}, None)[0], 401)
        self.assertEqual(self.h("POST", "/api/events", {}, self.full_dev)[0], 405)
        st, out = self.h("GET", "/api/events", {}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["cap"], 500)
        self.assertEqual(out["events"][0]["who"], "192.168.0.9")
        self.assertEqual(self.call("GET", "/api/events", token=self.full)[0], 200)

    def test_a_support_login_never_gets_the_names_and_addresses(self):
        self.api.event("signin", "A sign-in was refused: wrong PIN.", who="192.168.0.9")
        remote = dict(self.full_dev, remote=True)
        with mock.patch.object(self.api.support, "is_remote", return_value=True):
            out = self.h("GET", "/api/events", {}, remote)[1]
        self.assertEqual(len(out["events"]), 1)
        self.assertNotIn("who", out["events"][0])
        self.assertNotIn("192.168.0.9", json.dumps(out))
        self.assertEqual(self.h("GET", "/api/events", {}, self.full_dev)[1]["events"][0]["who"], "192.168.0.9", "the Owner in the room still sees it")

    def test_a_secret_in_an_event_is_taken_out_for_the_owner_too(self):
        self.api.event("player", "something said %s aloud" % PIN)
        out = self.h("GET", "/api/events", {}, self.full_dev)[1]
        self.assertNotIn(PIN, json.dumps(out))

    def test_the_diagnostics_carry_the_events_without_names_and_say_where_they_are_kept(self):
        self.api.event("signin", "A sign-in was refused: wrong PIN.", who="192.168.0.9")
        self.api.event("projector", "A projector did not answer.", who="Left wall")
        st, out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)
        self.assertEqual(st, 200)
        ev = out["file"]["events"]
        self.assertEqual(len(ev["lines"]), 2)
        text = json.dumps(out["file"]["events"])
        self.assertNotIn("192.168.0.9", text)
        self.assertNotIn("Left wall", text)
        self.assertIn("[projector] A projector did not answer.", ev["lines"][0])
        self.assertIn("survive a restart", ev["note"])
        with mock.patch("pvj.rootfs.current_state", return_value="overlay"):
            note = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)[1]["file"]["events"]["note"]
        self.assertIn("lost at a restart", note)

    def test_a_failed_sign_in_and_a_scene_and_a_restart_are_events(self):
        self.assertEqual(self.call("POST", "/api/pair", {"pin": "7391", "name": "x"})[0], 403)
        self.assertEqual(self.api.events.events()[0]["kind"], "signin")
        self.assertNotIn("7391", json.dumps(self.api.events.events()))
        notes = []
        self.api.event = lambda *a, **k: notes.append((a, k))
        from pvj.scheduler import Scheduler
        sched = Scheduler(self.api, self.settings, self.api.registry)
        import datetime
        sched._execute({"id": "e1", "label": "Doors", "action": "scene", "scene": "abcd"}, datetime.datetime(2026, 11, 7, 10, 0))
        self.assertEqual(notes[0][0][0], "scene")
        self.assertEqual(notes[0][1], {"who": "Doors"})

    def test_the_player_restart_hook_is_called_for_an_unexpected_pid_only(self):
        from pvj.player import Player
        p = Player.__new__(Player)
        import threading
        p._pid_lock, p._seen_pid, p._own_start, p._new_expected = threading.Lock(), 10, False, False
        p.clears, p.cleared_by, p._widen_due, p._pipe_loading = 0, None, False, False
        calls = []
        p.on_restart = lambda: calls.append(1)
        p._widen = lambda: None
        p._note_pid(10)
        self.assertEqual(calls, [])
        p._note_pid(11)
        self.assertEqual(calls, [1])
        p._new_expected = True
        p._note_pid(12)
        self.assertEqual(calls, [1], "a restart the panel asked for is no crash")

    def test_events_tick_says_an_update_and_a_fall_back_once(self):
        result = {"state": "failed", "message": "the update stopped before it finished", "at": 1790000100}
        self.api._update_result = lambda: result
        rep = {"drawing": "classic", "why": "pvj-render ended twice in a row (exit 3)", "pid": 5}
        self.api.renderer.report = lambda: rep
        self.api.renderer.saved = lambda: {"use": "new"}
        self.api.events_tick()
        self.api.events_tick()
        rows = self.api.events.events()
        self.assertEqual(sorted(e["kind"] for e in rows), ["renderer", "update"])
        self.assertIn("ended twice in a row", [e for e in rows if e["kind"] == "renderer"][0]["text"])
        with open(self.events_path) as f:
            self.assertEqual(len(json.load(f)["events"]), 2, "both are crash-type: on the disk at once")
        result.update(state="done", message="updated to 1.2.3", at=1790000200)
        self.api.events_tick()
        self.assertEqual(self.api.events.events()[0]["kind"], "update")
        self.assertIn("finished", self.api.events.events()[0]["text"])

    def test_the_log_comes_through_the_helper_scrubbed_and_says_it_is_in_memory_when_it_is(self):
        line = "2026-11-07T10:00:03+0000 node pvj-web[1]: listening; pairing PIN %s; rtsp://u:pw12345@192.168.1.60/x" % PIN
        helper = FakeSysd({"ok": True, "lines": [line, "second"], "persistent": False, "read_only_root": True})
        self.api.sysd = helper
        out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)[1]["file"]["log"]
        self.assertTrue(out["readable"])
        self.assertNotIn(PIN, json.dumps(out))
        self.assertNotIn("pw12345", json.dumps(out))
        self.assertIn("kept in memory", out["note"])
        self.assertEqual(out["journal"], {"persistent": False, "read_only_root": True})
        self.assertEqual([m.get("boot", 0) for m in helper.asked if m.get("cmd") == "journal"], [0], "no earlier start when nothing is kept")
        helper = FakeSysd({"ok": True, "lines": ["a"], "persistent": True, "read_only_root": False},
                          before={"ok": True, "lines": ["old one %s" % PIN], "persistent": True, "read_only_root": False})
        self.api.sysd = helper
        out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)[1]["file"]["log"]
        self.assertEqual(len(out["previous_start"]), 1)
        self.assertNotIn(PIN, json.dumps(out))
        self.assertEqual([m.get("boot", 0) for m in helper.asked if m.get("cmd") == "journal"], [0, -1])
        self.assertTrue(all(m["lines"] <= 500 for m in helper.asked if m.get("cmd") == "journal"))

    def test_the_reason_the_helper_could_not_give_the_log_is_in_the_note(self):
        for fake, want in ((FakeSysd({"ok": False, "error": "the system log could not be read: Failed to open journal %s" % PIN}), "Failed to open journal"),
                           (FakeSysd(None, fail=True), "pvj-sysd) is not running")):
            self.api.sysd = fake
            out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)[1]["file"]["log"]
            self.assertIn("The system helper could not give the log", out["note"])
            self.assertIn(want, out["note"])
            self.assertNotIn(PIN, json.dumps(out))

    def test_without_the_helper_the_old_way_is_tried(self):
        self.api.sysd = FakeSysd(None, fail=True)
        out = self.h("GET", "/api/system/diagnostics", {}, self.full_dev)[1]["file"]["log"]
        self.assertIn("readable", out)
        self.assertNotIn("journal", out)


if __name__ == "__main__":
    unittest.main()
