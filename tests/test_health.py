# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest import mock

from pvj import health
from tests.test_server import ServerBase


def tree(alarm=None):
    root = tempfile.mkdtemp()
    sysfs, proc = os.path.join(root, "sys"), os.path.join(root, "proc")
    os.makedirs(os.path.join(sysfs, "class/hwmon/hwmon0"))
    with open(os.path.join(sysfs, "class/hwmon/hwmon0/name"), "w") as f:
        f.write("cpu_thermal\n")
    if alarm is not None:
        os.makedirs(os.path.join(sysfs, "class/hwmon/hwmon1"))
        with open(os.path.join(sysfs, "class/hwmon/hwmon1/name"), "w") as f:
            f.write("rpi_volt\n")
        with open(os.path.join(sysfs, "class/hwmon/hwmon1/in0_lcrit_alarm"), "w") as f:
            f.write("1\n" if alarm else "0\n")
    os.makedirs(proc)
    with open(os.path.join(proc, "loadavg"), "w") as f:
        f.write("%.2f 0.50 0.40 1/100 123\n" % (2.0 * (os.cpu_count() or 1) / 4))
    with open(os.path.join(proc, "meminfo"), "w") as f:
        f.write("MemTotal:        4000000 kB\nMemFree:          100000 kB\nMemAvailable:    3000000 kB\n")
    run = os.path.join(root, "run")
    os.makedirs(run)
    return sysfs, proc, run


def set_alarm(sysfs, on):
    with open(os.path.join(sysfs, "class/hwmon/hwmon1/in0_lcrit_alarm"), "w") as f:
        f.write("1\n" if on else "0\n")


class Api:
    support = None

    def _ip_json(self):
        return [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
                {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.0.169"}]}]

    class player:
        @staticmethod
        def status():
            return {"running": True, "path": None}


class PowerTest(unittest.TestCase):
    def test_undervoltage_now_then_remembered_until_a_reboot(self):
        sysfs, proc, run = tree(alarm=False)
        h = health.Health(Api(), run, sysfs=sysfs, proc=proc, log=lambda *_: None)
        self.assertEqual(h.power()["state"], "ok")
        set_alarm(sysfs, True)
        self.assertEqual(h.power()["state"], "bad")
        set_alarm(sysfs, False)
        p = h.power()
        self.assertEqual(p["state"], "warn")                      # it happened since the node started
        self.assertIn("since the node started", p["text"])
        fresh = health.Health(Api(), run, sysfs=sysfs, proc=proc, log=lambda *_: None)
        self.assertEqual(fresh.power()["state"], "warn")          # a restart of the panel does not forget it

    def test_a_board_without_the_alarm_says_so(self):
        sysfs, proc, run = tree(alarm=None)
        self.assertEqual(health.Health(Api(), run, sysfs=sysfs, proc=proc).power()["state"], "unknown")


class ReportTest(unittest.TestCase):
    def test_load_memory_addresses_and_overall(self):
        sysfs, proc, run = tree(alarm=False)
        h = health.Health(Api(), run, sysfs=sysfs, proc=proc, log=lambda *_: None)
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 55.0}]):
            r = h.report()
        self.assertEqual((r["cpu_percent"], r["memory_percent"], r["memory_mb"]), (50, 25, 3906))
        self.assertIn("http://192.168.0.169/", r["addresses"])
        self.assertNotIn("http://127.0.0.1/", r["addresses"])
        self.assertEqual((r["temperature"]["state"], r["overall"]), ("ok", "ok"))
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 86.0}]):
            self.assertEqual(h.report()["overall"], "bad")
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 81.0}]):
            self.assertEqual(h.temperature()["state"], "warn")

    def test_a_helper_that_does_not_answer_is_a_problem(self):
        sysfs, proc, run = tree(alarm=False)
        api = Api()

        class Down:
            def request(self, m):
                raise OSError("not running")
        api.sysd = Down()
        h = health.Health(api, run, sysfs=sysfs, proc=proc)
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: []):
            r = h.report()
        self.assertEqual(r["helpers"][0], {"name": "pvj-sysd", "label": "System helper (restart, power off, clock)", "running": False})
        self.assertEqual(r["overall"], "bad")


class ApiTest(ServerBase):
    def test_every_paired_device_can_read_it_and_the_address_can_go_on_screen(self):
        full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=full)[1]["token"]
        st, body, _ = self.call("GET", "/api/health", token=view)
        self.assertEqual(st, 200)
        for key in ("power", "temperature", "player", "helpers", "addresses", "overall"):
            self.assertIn(key, body)
        self.assertEqual(self.call("GET", "/api/health")[0], 401)


class AddressOnScreenTest(unittest.TestCase):
    def test_the_address_alone_shows_no_code(self):
        from pvj.pinscreen import PinScreen

        class Auth:
            current_pin = "1234"

            def list_joins(self):
                return []

            def list_devices(self):
                return [1]

        class A:
            def _ip_json(self):
                return [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.0.169"}]}]
        p = PinScreen(A(), Auth(), log=lambda *_: None, hostname="box", clock=lambda: 0.0)
        lines = p.manual_lines({"until": 60.0, "items": ["address"]})
        text = "\n".join(lines)
        self.assertIn("http://box.local/", text)
        self.assertNotIn("1234", text)


if __name__ == "__main__":
    unittest.main()
