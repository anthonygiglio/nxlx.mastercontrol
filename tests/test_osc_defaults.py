# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""OSC's safer defaults (D83): the paired-device lock comes on with the first switch-on, a box where OSC is on or ever
was keeps its settings exactly, and a box that is on with every lock off says so."""
import json
import os
import socket
import subprocess
import textwrap

from pvj import osc
from pvj.settings import Settings, default_settings
from tests.test_boxcare import Base as CareBase
from tests.test_osc import msg
from tests.test_osc_lockdown import OTHER, TABLET
from tests.test_support import LAN

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLDER = "106d77f"       # master where this branch left it: the release before D83, for the rollback test
LOCKS = ("only_on", "paired_on", "key_on")


class Base(CareBase):
    def setUp(self):
        super().setUp()
        self.settings.data["osc"] = dict(default_settings()["osc"])      # a new box
        self.settings.save()
        self.addCleanup(self.api.osc.stop)

    def post(self, body, device=None):
        return self.h("POST", "/api/osc", body, device or self.full_dev)

    def get(self, device=None):
        st, out = self.h("GET", "/api/osc", device=device or self.full_dev)
        self.assertEqual(st, 200, out)
        return out

    def on(self, **more):
        body = {"enabled": True}
        if self.settings.data["osc"]["port"] != 0:          # a port that is free on this machine, as a request must name one
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(("127.0.0.1", 0))
                body["port"] = sock.getsockname()[1]
        st, out = self.post(dict(body, **more))
        self.assertEqual(st, 200, out)
        return out

    def locks(self):
        return tuple(self.settings.data["osc"].get(k, False) for k in LOCKS)

    def master_file(self, **keys):
        """A settings file as master (before D83) writes its OSC part: no mark, and only the keys its page saved."""
        self.settings.data["osc"] = dict({"enabled": False, "port": 9876, "allow": []}, **keys)
        self.settings.save()
        self.settings.load()
        self.api.osc.apply()            # what the panel does when it starts


class FirstSwitchOnTest(Base):
    def test_the_first_switch_on_brings_the_paired_lock(self):
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)
        self.assertTrue(osc.never_on(self.settings.data))
        out = self.on()
        self.assertEqual((out["enabled"], out["paired_on"], out["paired_default"], out["open"]), (True, True, True, False))
        self.assertEqual(self.locks(), (False, True, False))
        with open(self.settings.path) as f:
            saved = json.load(f)["osc"]
        self.assertEqual((saved["paired_on"], saved[osc.EVER_ON], saved[osc.PAIRED_DEFAULT]), (True, True, True))
        # and it holds: a stranger on the network is refused, the owner's own device (which opened the panel) is not
        server = self.api.osc.server
        self.assertEqual(server.handle_packet(msg("/pvj/stop"), OTHER), 0)
        self.assertEqual(server.handle_packet(msg("/pvj/stop"), "127.0.0.1"), 1)

    def test_a_request_that_says_what_it_wants_for_the_lock_is_obeyed(self):
        out = self.on(paired_on=False)
        self.assertEqual((out["paired_on"], out["paired_default"], out["open"]), (False, False, True))
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], True)

    def test_it_is_the_first_time_only(self):
        self.on()
        self.assertEqual(self.post({"paired_on": False})[0], 200)       # the owner's own choice
        self.assertNotIn(osc.PAIRED_DEFAULT, self.settings.data["osc"])
        self.assertEqual(self.post({"enabled": False})[0], 200)
        out = self.on()
        self.assertEqual((out["paired_on"], out["paired_default"], out["open"]), (False, False, True), "never put back on")
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], True)

    def test_the_port_and_the_networks_changed_first_on_a_new_box_is_still_the_first_time(self):
        self.assertEqual(self.post({"port": 9001, "allow": ["192.168.50.0/24"]})[0], 200)
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)
        self.assertTrue(self.on()["paired_on"])

    def test_another_lock_chosen_with_the_switch_does_not_stand_in_for_it(self):
        out = self.on(only_on=True, only=[TABLET])
        self.assertEqual((out["only_on"], out["paired_on"]), (True, True))

    def test_only_the_owner_switches_it(self):
        for dev in (self.live_dev, self.view_dev):
            self.assertEqual(self.post({"enabled": True}, dev)[0], 403)
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)

    def test_after_a_factory_reset_it_is_the_first_time_again(self):
        self.on(paired_on=False)
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["osc"], default_settings()["osc"])
        self.assertTrue(osc.never_on(self.settings.data))


class ExistingBoxTest(Base):
    def test_an_open_box_stays_open_and_says_so(self):
        """The update must never lock out a working tablet: a file as master writes it, OSC on and every lock off."""
        self.master_file(enabled=True, port=0)
        cfg = self.settings.data["osc"]
        self.assertEqual({k: v for k, v in cfg.items() if k != osc.EVER_ON}, {"enabled": True, "port": 0, "allow": []})
        self.assertIs(cfg[osc.EVER_ON], True)
        self.assertEqual(self.api.osc.server.handle_packet(msg("/pvj/stop"), OTHER), 1, "the tablet that worked still works")
        out = self.get()
        self.assertEqual((out["open"], out["paired_on"], out["paired_default"]), (True, False, False))
        self.assertEqual(out["open_text"], "OSC is open: anyone on this network can control the box. Switch on a lock.")
        # switched off and on again, to find a fault: still as the owner had it
        self.assertEqual(self.post({"enabled": False})[0], 200)
        self.assertFalse(self.get()["open"])
        out = self.on()
        self.assertEqual((self.locks(), out["open"]), ((False, False, False), True))

    def test_a_box_that_was_on_with_its_locks_keeps_them_exactly(self):
        self.master_file(enabled=True, port=0, only_on=True, only=[TABLET], paired_on=False, key_on=False)
        before = {k: v for k, v in self.settings.data["osc"].items() if k != osc.EVER_ON}
        self.assertEqual(self.post({"enabled": False})[0], 200)
        self.on()
        self.assertEqual({k: v for k, v in self.settings.data["osc"].items() if k != osc.EVER_ON}, before)

    def test_a_box_where_osc_is_off_but_was_set_up_keeps_its_settings(self):
        for keys in ({"port": 9001}, {"allow": ["192.168.50.0/24"]}, {"paired_on": False}, {"only": []}, {"feedback_on": False}, {"key": "a" * 20}):
            self.api.osc.stop()
            self.settings.data.pop(osc.KEY_SECTION, None)
            self.master_file(**keys)
            self.assertIs(self.settings.data["osc"][osc.EVER_ON], True, keys)
            out = self.on()
            self.assertEqual((out["paired_on"], out["paired_default"]), (False, False), keys)
            self.assertEqual(self.post({"enabled": False})[0], 200)
        self.api.osc.stop()
        self.settings.data.pop(osc.KEY_SECTION, None)
        self.master_file()
        self.settings.data[osc.KEY_SECTION] = {"key": "b" * 20}
        del self.settings.data["osc"][osc.EVER_ON]
        self.assertFalse(osc.never_on(self.settings.data), "a key was made here once: OSC was set up")

    def test_the_one_box_that_cannot_be_told_from_a_new_one(self):
        """Said in D83: OSC was on under master, then off, and nothing else was ever changed. Its file is a new box's
        file, so its next switch-on brings the lock (and the page says the lock came with the switch)."""
        self.master_file()
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)
        self.assertTrue(self.on()["paired_default"])

    def test_the_mark_is_written_once_and_only_ever_goes_up(self):
        self.master_file(enabled=True, port=0)
        self.assertEqual(self.post({"enabled": False})[0], 200)
        for _ in range(2):
            self.api.osc.apply()
            self.assertIs(self.settings.data["osc"][osc.EVER_ON], True)
        self.settings.data["osc"][osc.EVER_ON] = False      # a file an older release imported into: on, with no true mark
        self.settings.data["osc"]["enabled"] = True
        self.api.osc.apply()
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], True)


class ReviewTest(Base):
    """The independent review of #124 at 8f8fb8a."""

    def test_a_save_that_fails_at_the_first_start_does_not_stop_the_receiver(self):
        """M1. The mark is written when the receiver is set up; on a full or read-only disk that save raised and
        nothing listened, where master writes nothing there. The receiver starts, it is said once, and the mark is
        written at the next start or change."""
        self.api.osc.stop()
        self.settings.data["osc"] = {"enabled": True, "port": 0, "allow": [], "paired_on": True}
        self.settings.save()
        self.settings.load()
        lines, real = [], self.settings.save
        self.api.osc.log = lines.append

        def full(*a, **kw):
            raise OSError(28, "No space left on device")
        self.settings.save = full
        try:
            self.api.osc.apply()
            self.api.osc.apply()
        finally:
            self.settings.save = real
        self.assertTrue(self.api.osc.server and self.api.osc.server.listening, "the receiver listens")
        self.assertEqual(self.api.osc.server.handle_packet(msg("/pvj/stop"), OTHER), 0, "and the lock that was on holds")
        self.assertEqual(len([x for x in lines if "could not be saved" in x]), 1, lines)
        self.api.osc.apply()                # the disk is back
        with open(self.settings.path) as f:
            self.assertIs(json.load(f)["osc"][osc.EVER_ON], True)

    def test_an_import_does_not_lose_the_first_switch_on_lock(self):
        """M2. A new box, the port changed, its own export imported, then switched on: the import had read the changed
        port as "OSC was set up here" and marked it, so no lock came and the box was open."""
        self.assertEqual(self.post({"port": 9050})[0], 200)
        st, out = self.send(self.export())
        self.assertEqual(st, 200, out)
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)
        out = self.on()
        self.assertEqual((out["paired_on"], out["paired_default"], out["open"]), (True, True, False))

    def test_an_import_into_a_file_that_has_no_mark_yet_writes_the_mark_it_would_have_had(self):
        from pvj import boxcare
        for cfg, mark in (({"enabled": False, "port": 9876, "allow": []}, False), ({"enabled": False, "port": 9001, "allow": []}, True),
                          ({"enabled": True, "port": 9876, "allow": []}, True)):
            new = {"osc": {"enabled": False, "port": 9050, "allow": []}}
            boxcare.BoxCare._osc_layers(new, {"osc": dict(cfg)})
            self.assertIs(new["osc"][osc.EVER_ON], mark, cfg)
            self.assertEqual(osc.never_on(new), not mark, "and the port the file brought does not count as a trace later")

    def test_a_guest_is_not_told_that_osc_is_open_on_any_route(self):
        """L1. GET /api/osc gave a Guest `open` and the words, against D83."""
        self.on(paired_on=False)
        seen = self.get(self.view_dev)
        self.assertNotIn("open", seen)
        self.assertNotIn("open_text", seen)
        self.assertNotIn(osc.OPEN_TEXT, json.dumps(seen))
        self.assertEqual((seen["enabled"], seen["paired_on"]), (True, False), "what he could read before is as it was (D78)")
        for dev in (self.live_dev, self.full_dev):
            self.assertIs(self.get(dev)["open"], True)

    def test_the_health_summary_agrees_with_its_own_row(self):
        """L4. The row said "Check" and `overall` said ok. An open OSC is a check, not a failure."""
        st, out = self.h("GET", "/api/health", device=self.full_dev)
        before = out["overall"]
        self.on(paired_on=False)
        for dev in (self.full_dev, self.live_dev):
            out = self.h("GET", "/api/health", device=dev)[1]
            self.assertEqual(out["osc_open"]["state"], "warn")
            self.assertEqual(out["overall"], "bad" if before == "bad" else "warn")
        self.assertEqual(self.h("GET", "/api/health", device=self.view_dev)[1]["overall"], before, "a Guest's summary does not tell him either")
        self.api.health.report = lambda: {"overall": "bad"}
        self.assertEqual(self.h("GET", "/api/health", device=self.full_dev)[1]["overall"], "bad", "a real problem stays a problem")
        self.api.health.report = lambda: {"overall": "ok"}
        self.assertEqual(self.h("GET", "/api/health", device=self.full_dev)[1]["overall"], "warn")


class WarningTest(Base):
    def health(self, device):
        st, out = self.h("GET", "/api/health", device=device)
        self.assertEqual(st, 200, out)
        return out

    def test_the_three_places_by_role(self):
        self.on(paired_on=False)
        text = osc.OPEN_TEXT
        # the Owner: the OSC page and Setup's index row (both drawn from GET /api/osc), and the health page
        self.assertEqual((self.get()["open"], self.get()["open_text"]), (True, text))
        self.assertEqual(self.health(self.full_dev)["osc_open"], {"state": "warn", "text": text})
        # an Operator: the health page says it; of the OSC page he gets no list and may change nothing
        self.assertEqual(self.health(self.live_dev)["osc_open"]["text"], text)
        seen = self.get(self.live_dev)
        self.assertTrue(seen["open"])
        for key in ("only", "senders", "paired_now", "feedback", "key_set"):
            self.assertNotIn(key, seen)
        self.assertEqual(self.post({"paired_on": True}, self.live_dev)[0], 403)
        # a Guest: the health page does not tell him what is open
        self.assertNotIn("osc_open", self.health(self.view_dev))

    def test_no_warning_with_any_one_lock_on_or_with_osc_off(self):
        self.on(paired_on=False)
        for lock in ({"only_on": True, "only": [TABLET]}, {"paired_on": True}, {"key_on": True}):
            self.assertEqual(self.post(lock)[0], 200)
            self.assertFalse(self.get()["open"], lock)
            self.assertNotIn("osc_open", self.health(self.full_dev), lock)
            self.assertEqual(self.post({k: False for k in LOCKS})[0], 200)
            self.assertTrue(self.get()["open"])
        self.assertEqual(self.post({"enabled": False})[0], 200)
        self.assertFalse(self.get()["open"])
        self.assertNotIn("osc_open", self.health(self.full_dev))

    def test_a_damaged_lock_setting_fails_closed_and_is_not_called_open(self):
        self.assertFalse(osc.is_open({"enabled": True, "paired_on": "yes"}))
        self.assertTrue(osc.is_open({"enabled": True}))
        self.assertFalse(osc.is_open({"enabled": False}))


class FileTest(Base):
    def test_an_export_does_not_carry_the_mark_and_an_import_never_lowers_it(self):
        self.on()
        file = self.export()
        self.assertNotIn(osc.EVER_ON, file["settings"]["osc"])
        self.assertNotIn(osc.PAIRED_DEFAULT, file["settings"]["osc"])
        file["settings"]["osc"].update(enabled=False, ever_on=False)       # a file that claims OSC was never on here
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], True)
        self.assertTrue(self.settings.data["osc"]["paired_on"], "the file's own lock setting came with it")

    def test_a_file_with_osc_on_comes_as_it_is(self):
        """The owner's own settings from a working box: no lock is added to it, and the page then says it is open."""
        file = self.export()
        file["settings"]["osc"] = {"enabled": True, "port": 9876, "allow": []}
        self.api.osc.apply = lambda: None                    # no socket in this test
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        cfg = self.settings.data["osc"]
        self.assertEqual((cfg["enabled"], cfg.get("paired_on", False), cfg[osc.EVER_ON]), (True, False, True))
        self.assertTrue(osc.is_open(cfg))

    def test_a_file_with_osc_off_leaves_a_new_box_new(self):
        file = self.export()
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertIs(self.settings.data["osc"][osc.EVER_ON], False)
        self.assertTrue(self.on()["paired_on"])


class RollbackTest(Base):
    def older(self):
        try:
            tree = subprocess.run(["git", "archive", OLDER, "pvj"], cwd=REPO, capture_output=True, check=True, timeout=60).stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.skipTest("the older release %s is not in this clone" % OLDER)
        old = os.path.join(os.path.dirname(self.settings.path), "older")
        os.makedirs(old)
        subprocess.run(["tar", "-x", "-C", old], input=tree, check=True, timeout=60)
        with open(os.path.join(old, "pvj", "osc.py")) as f:
            self.assertNotIn("ever_on", f.read())
        return old

    def run_old(self, old, script, path):
        r = subprocess.run([os.sys.executable, "-c", textwrap.dedent(script), old, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_master_s_own_open_file_stays_open_here(self):
        """The file is written by master's code, not by hand: a new box of master's, OSC switched on, no lock."""
        old = self.older()
        path = os.path.join(os.path.dirname(self.settings.path), "master.json")
        made = self.run_old(old, """
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from pvj.settings import Settings
            s = Settings(sys.argv[2])
            s.load()
            s.data["osc"] = dict(s.data["osc"], enabled=True, port=0)       # what its OSC page's save does
            s.save()
            print(json.dumps(json.load(open(sys.argv[2]))["osc"]))
            """, path)
        self.assertEqual(made, {"enabled": True, "port": 0, "allow": []})
        s = Settings(path)
        s.load()
        self.api.osc.stop()
        self.api.settings = self.api.osc.settings = s
        self.addCleanup(setattr, self.api, "settings", self.settings)
        self.api.osc.apply()
        self.assertEqual(s.data["osc"], {"enabled": True, "port": 0, "allow": [], osc.EVER_ON: True})
        self.assertEqual(self.api.osc.server.handle_packet(msg("/pvj/stop"), OTHER), 1)
        self.assertTrue(self.api.osc.status()["open"])

    def test_master_keeps_the_new_keys_and_holds_the_lock(self):
        """Going back: master's code on a file this code wrote after a first switch-on. It loads the file, refuses a
        stranger (the paired lock is its own, D78), and keeps both new keys through a save of its OSC page."""
        old = self.older()
        self.on()
        self.api.osc.stop()
        got = self.run_old(old, """
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from pvj.settings import Settings
            from pvj import osc
            s = Settings(sys.argv[2])
            s.load()
            class Api:
                mix = {}
                auth = None
                calls = []
                def handle(self, *a):
                    self.calls.append(a)
                    return 200, {}
            api = Api()
            m = osc.OscManager(api, s, host="127.0.0.1", log=lambda *a: None)
            m.apply()
            taken = m.server.handle_packet(b"/pvj/stop\\0\\0\\0,\\0\\0\\0", "192.168.1.77")
            cfg = dict(s.data["osc"], port=9877, enabled=False)       # its own page's save: a copy with its keys changed
            s.data["osc"] = cfg
            m.apply()
            s.save()
            print(json.dumps({"taken": taken, "calls": len(api.calls), "osc": json.load(open(sys.argv[2]))["osc"]}))
            """, self.settings.path)
        self.assertEqual((got["taken"], got["calls"]), (0, 0), "master refuses the stranger: the lock holds after going back")
        self.assertEqual((got["osc"]["paired_on"], got["osc"][osc.EVER_ON], got["osc"][osc.PAIRED_DEFAULT]), (True, True, True))
        self.settings.load()            # and forward again: nothing is the first time twice
        self.assertFalse(osc.never_on(self.settings.data))


if __name__ == "__main__":
    import unittest
    unittest.main()
