# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import datetime
import os
import tempfile
import unittest

from pvj import scheduler
from pvj.api import ApiError
from pvj.modules import Registry
from pvj.settings import Settings
from tests.test_server import ServerBase

MON_9 = datetime.datetime(2026, 9, 28, 9, 0, 0)   # a Monday


class FakeApi:
    def __init__(self):
        self.calls = []
        self.fail = None

    def _rec(self, name, body):
        if self.fail:
            raise self.fail
        self.calls.append((name, body))

    def play(self, body, device, client):
        self._rec("play", body)

    def control(self, body, device, client):
        self._rec("control", body)

    def blackout(self, body, device, client):
        self._rec("blackout", body)


def entry(**kw):
    e = {"id": "abcd1234", "label": "", "time": "09:00", "days": [0], "action": "play", "file": "a.mp4", "loop": True}
    e.update(kw)
    return e


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        self.settings.load()
        self.registry = Registry(self.settings, "x86")
        self.registry.set_enabled("scheduler", True)
        self.api = FakeApi()
        self.clock = [MON_9 - datetime.timedelta(minutes=1)]
        self.logs = []
        self.s = scheduler.Scheduler(self.api, self.settings, self.registry, now=lambda: self.clock[0], log=self.logs.append)

    def set(self, entries, enabled=True):
        self.settings.data["schedule"] = scheduler.validate({"enabled": enabled, "entries": entries})

    def at(self, dt):
        self.clock[0] = dt
        return self.s.tick()

    def test_fires_once_at_its_minute(self):
        self.set([entry()])
        self.assertEqual(self.at(MON_9 - datetime.timedelta(minutes=1)), [])   # first look never fires
        self.assertEqual(self.at(MON_9), ["abcd1234"])
        self.assertEqual(self.at(MON_9 + datetime.timedelta(seconds=30)), [])  # same minute again
        self.assertEqual(self.api.calls, [("play", {"file": "a.mp4", "loop": True})])

    def test_wrong_day_and_time_do_not_fire(self):
        self.set([entry(days=[1])])
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.assertEqual(self.at(MON_9), [])
        self.set([entry(time="09:01")])
        self.assertEqual(self.at(MON_9 + datetime.timedelta(minutes=1)), ["abcd1234"])

    def test_actions(self):
        self.set([entry(id="00000001", action="stop"), entry(id="00000002", action="blackout"), entry(id="00000003", action="show")])
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.at(MON_9)
        self.assertEqual(self.api.calls, [("control", {"action": "stop"}), ("blackout", {"on": True}), ("blackout", {"on": False})])

    def test_off_switch_and_off_module_fire_nothing(self):
        self.set([entry()], enabled=False)
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.assertEqual(self.at(MON_9), [])
        self.set([entry()], enabled=True)
        self.registry.set_enabled("scheduler", False)
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.assertEqual(self.at(MON_9 + datetime.timedelta(days=7)), [])
        self.assertEqual(self.api.calls, [])

    def test_clock_jump_never_replays_old_events(self):
        self.set([entry(days=[0, 1, 2, 3, 4, 5, 6])])
        self.at(datetime.datetime(1970, 1, 1, 0, 0))            # no clock yet
        self.assertEqual(self.at(MON_9), [])                    # network time arrives: a big jump
        self.assertEqual(self.at(MON_9 - datetime.timedelta(hours=3)), [])   # and backwards
        self.assertEqual(self.api.calls, [])

    def test_short_stall_catches_up_but_not_a_long_one(self):
        self.set([entry()])
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.assertEqual(self.at(MON_9 + datetime.timedelta(minutes=1)), ["abcd1234"])   # 2 minutes late
        self.s._checked = None
        self.at(MON_9 + datetime.timedelta(days=1) - datetime.timedelta(minutes=1))
        self.set([entry(days=[1])])
        self.assertEqual(self.at(MON_9 + datetime.timedelta(days=1) + datetime.timedelta(minutes=5)), [])

    def test_failure_is_recorded_and_does_not_stop_the_schedule(self):
        self.set([entry(id="00000001"), entry(id="00000002", action="stop")])
        self.at(MON_9 - datetime.timedelta(minutes=1))
        self.api.fail = ApiError(404, "file not found")
        self.assertEqual(self.at(MON_9), ["00000001", "00000002"])
        self.assertEqual(self.s.last["00000001"]["message"], "file not found")
        self.assertFalse(self.s.last["00000001"]["ok"])
        self.api.fail = RuntimeError("boom")
        self.at(MON_9 + datetime.timedelta(days=7, minutes=-1))
        self.at(MON_9 + datetime.timedelta(days=7))
        self.assertIn("boom", self.s.last["00000001"]["message"])

    def test_validation(self):
        good = scheduler.validate({"enabled": True, "entries": [{"time": "23:59", "days": [6, 0, 6], "action": "stop"}]})
        e = good["entries"][0]
        self.assertEqual((e["days"], len(e["id"])), ([0, 6], 8))   # sorted, de-duplicated, id assigned
        bad = [
            {"enabled": "yes"}, {"entries": "x"}, {"entries": [entry()] * 2},
            {"entries": [entry(time="9:00")]}, {"entries": [entry(time="24:00")]}, {"entries": [entry(time="09:60")]},
            {"entries": [entry(days=[])]}, {"entries": [entry(days=[7])]}, {"entries": [entry(days=[True])]},
            {"entries": [entry(days="0")]}, {"entries": [entry(action="reboot")]}, {"entries": [entry(action="shutdown")]},
            {"entries": [entry(file="../x.mp4")]}, {"entries": [entry(file="a.txt")]}, {"entries": [entry(file=None)]},
            {"entries": [entry(loop="yes")]}, {"entries": [entry(label="x" * 41)]}, {"entries": [entry(label="a\nb")]},
            {"entries": [entry(id="ZZZZ")]}, {"entries": [entry(time="09:00\n")]}, {"entries": [entry(id="abcd1234\n")]}, {"entries": ["x"]},
            {"entries": [entry(id="%08x" % i) for i in range(scheduler.MAX_ENTRIES + 1)]},
        ]
        for body in bad:
            with self.assertRaises(scheduler.ScheduleError, msg=str(body)[:80]):
                scheduler.validate(body)
        with self.assertRaises(scheduler.ScheduleError):
            scheduler.validate([])

    def test_thread_starts_and_stops(self):
        s = scheduler.Scheduler(self.api, self.settings, self.registry, interval=0.01, log=self.logs.append)
        s.start()
        s.start()
        s.stop()
        self.assertIsNone(s._thread)


class ScheduleApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.api.scheduler = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        self.full = self.pair("full")

    def pair(self, role):
        if role == "full":
            return self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        return self.call("POST", "/api/devices/invite", {"name": "g", "role": role}, token=self.full)[1]["token"]

    def test_needs_module_then_roundtrip_and_persist(self):
        self.assertEqual(self.call("GET", "/api/schedule", token=self.full)[0], 409)
        self.call("POST", "/api/modules/scheduler", {"enabled": True}, token=self.full)
        st, body, _ = self.call("GET", "/api/schedule", token=self.full)
        self.assertEqual((st, body["entries"], body["enabled"]), (200, [], False))
        cfg = {"enabled": True, "entries": [{"time": "18:30", "days": [4, 5], "action": "play", "file": "a.mp4", "label": "Doors"}]}
        st, body, _ = self.call("POST", "/api/schedule", cfg, token=self.full)
        self.assertEqual(st, 200)
        self.assertTrue(body["active"])
        self.assertEqual(body["entries"][0]["label"], "Doors")
        self.assertEqual(Settings(self.settings.path).load()["schedule"]["entries"][0]["time"], "18:30")

    def test_bad_input_is_400_and_changes_nothing(self):
        self.call("POST", "/api/modules/scheduler", {"enabled": True}, token=self.full)
        st, body, _ = self.call("POST", "/api/schedule", {"entries": [{"time": "25:00", "days": [1], "action": "stop"}]}, token=self.full)
        self.assertEqual(st, 400)
        self.assertEqual(self.settings.data["schedule"], {"enabled": False, "entries": []})

    def test_roles(self):
        self.call("POST", "/api/modules/scheduler", {"enabled": True}, token=self.full)
        live, view = self.pair("live"), self.pair("view")
        self.assertEqual(self.call("GET", "/api/schedule", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/schedule", {"enabled": True, "entries": []}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/schedule", {"enabled": True, "entries": []}, token=live)[0], 200)     # the Operator's since D80
        self.assertEqual(self.call("GET", "/api/schedule")[0], 401)
