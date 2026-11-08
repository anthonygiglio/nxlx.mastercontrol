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
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 55.0}]), mock.patch.dict(os.environ, {"PVJ_STATE_DIR": run}):
            r = h.report()
        self.assertEqual((r["cpu_percent"], r["memory_percent"], r["memory_mb"]), (50, 25, 3906))
        self.assertIn("http://192.168.0.169/", r["addresses"])
        self.assertNotIn("http://127.0.0.1/", r["addresses"])
        self.assertEqual((r["temperature"]["state"], r["overall"]), ("ok", "ok"))
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 86.0}]), mock.patch.dict(os.environ, {"PVJ_STATE_DIR": run}):
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


class StorageTest(unittest.TestCase):
    """Free space on the media folder's disk and the state folder's disk (health.Health.storage), with a fake statvfs."""
    GB = health.GB

    def make(self, disks, media="/media/x", state="/state/y", clock=None):
        """disks: path -> (st_dev, free_bytes, total_bytes), or an exception (or None) for a folder that cannot be read."""
        calls = []

        def stat(path):
            d = disks.get(path)
            if not isinstance(d, tuple):
                raise (d or FileNotFoundError(path))
            return mock.Mock(st_dev=d[0])

        def statvfs(path):
            calls.append(path)
            d = disks[path]
            return mock.Mock(f_frsize=4096, f_blocks=int(d[2] // 4096), f_bavail=int(d[1] // 4096))

        class A(Api):
            media_dir = media
        sysfs, proc, run = tree()
        self.calls = calls
        h = health.Health(A(), run, sysfs=sysfs, proc=proc, log=lambda *_: None, statvfs=statvfs, stat=stat, clock=clock or (lambda: 1000.0))
        env = mock.patch.dict(os.environ, {"PVJ_STATE_DIR": state})
        env.start()
        self.addCleanup(env.stop)
        return h

    def test_the_thresholds(self):
        gb = self.GB
        for free, total, want in ((40 * gb, 100 * gb, "ok"), (1.9 * gb, 100 * gb, "bad"), (2.1 * gb, 100 * gb, "warn"),
                                  (9 * gb, 100 * gb, "warn"), (11 * gb, 100 * gb, "ok"), (0.49 * gb, 100 * gb, "bad"), (0.6 * gb, 100 * gb, "bad"),
                                  (2.9 * gb, 100 * gb, "warn"), (3.1 * gb, 100 * gb, "warn"), (2.5 * gb, 16 * gb, "ok"), (1.5 * gb, 16 * gb, "warn"),
                                  (0.4 * gb, 16 * gb, "bad"), (100 * gb, 4000 * gb, "ok"), (150 * gb, 4000 * gb, "ok"),
                                  (45 * gb, 4000 * gb, "ok"), (30 * gb, 4000 * gb, "ok"), (15 * gb, 4000 * gb, "warn"), (1.9 * gb, 4000 * gb, "bad"),
                                  (0, 100 * gb, "bad")):
            self.assertEqual(health.storage_state(free, total), want, (free / gb, total / gb))

    def test_words_and_shape_for_one_disk_with_both_folders(self):
        h = self.make({"/media/x": (7, 1.4 * self.GB, 31 * self.GB), "/state/y": (7, 1.4 * self.GB, 31 * self.GB)})
        rows = h.storage()
        self.assertEqual(len(rows), 1, "one line for one filesystem")
        r = rows[0]
        self.assertEqual((r["id"], r["state"], r["folders"]), ("media+state", "warn", ["media", "state"]))
        self.assertTrue(r["text"].startswith("Storage: 1.4 GB free on the node's disk (clips and settings) (5 percent)"), r["text"])
        self.assertEqual(set(r), {"id", "name", "folders", "state", "text", "free_bytes", "total_bytes", "free_percent"})
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 50.0}]):
            rep = h.report()
        self.assertEqual((rep["overall"], rep["storage"][0]["state"]), ("warn", "warn"))

    def test_two_disks_two_lines_and_the_worst_decides(self):
        h = self.make({"/media/x": (1, 300 * 10 ** 6, 64 * self.GB), "/state/y": (2, 20 * self.GB, 30 * self.GB)})
        a, b = h.storage()
        self.assertEqual((a["id"], a["state"], b["id"], b["state"]), ("media", "bad", "state", "ok"))
        self.assertTrue(a["text"].startswith("Storage: 300 MB free on the media disk (0 percent)"), a["text"])
        self.assertIn("Free space now", a["text"])
        self.assertIn("the system disk (settings and logs)", b["text"])
        with mock.patch("pvj.hardware.temperatures", lambda *a, **k: [{"celsius": 50.0}]):
            self.assertEqual(h.report()["overall"], "bad")

    def test_a_missing_or_unreadable_folder_is_said_and_never_raises(self):
        for err in (None, PermissionError(13, "no"), OSError(5, "io")):
            h = self.make({"/media/x": err, "/state/y": (2, 20 * self.GB, 30 * self.GB)})
            rows = h.storage()
            self.assertEqual((rows[0]["id"], rows[0]["state"]), ("media", "warn"))
            self.assertEqual(rows[0]["text"], "Storage: the media folder is missing or cannot be read.")
            self.assertIsNone(rows[0]["free_bytes"])
            self.assertEqual(rows[1]["state"], "ok")
        both = self.make({"/media/x": None, "/state/y": None}).storage()
        self.assertEqual([r["state"] for r in both], ["warn", "warn"], "two folders missing: two lines, not merged")

    def test_a_filesystem_that_reports_no_size_is_unknown_not_an_error(self):
        h = self.make({"/media/x": (3, 0, 0), "/state/y": (3, 0, 0)})
        r = h.storage()[0]
        self.assertEqual(r["state"], "unknown")
        self.assertIn("not reported", r["text"])

    def test_no_media_folder_known_is_only_the_state_disk(self):
        h = self.make({"/state/y": (4, 20 * self.GB, 30 * self.GB)}, media=None)
        self.assertEqual([r["id"] for r in h.storage()], ["state"])

    def test_the_answer_is_kept_for_ten_seconds(self):
        now = [1000.0]
        h = self.make({"/media/x": (1, 20 * self.GB, 30 * self.GB), "/state/y": (2, 20 * self.GB, 30 * self.GB)}, clock=lambda: now[0])
        h.storage()
        h.storage()
        self.assertEqual(len(self.calls), 2, "two folders asked once")
        now[0] += 11
        h.storage()
        self.assertEqual(len(self.calls), 4)

    def test_it_asks_the_disk_directly_with_nothing_started(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("a program was started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("a program was started")):
            h = health.Health(type("A", (), {"media_dir": tempfile.mkdtemp()})(), tempfile.mkdtemp(), log=lambda *_: None)
            with mock.patch.dict(os.environ, {"PVJ_STATE_DIR": tempfile.mkdtemp()}):
                rows = h.storage()
        self.assertTrue(rows and rows[0]["free_bytes"] > 0)

    def test_size_words(self):
        self.assertEqual([health.size_words(n) for n in (0, 450 * 10 ** 6, 10 ** 9, 1.44 * 10 ** 9, 123 * 10 ** 9)],
                         ["0 MB", "450 MB", "1.0 GB", "1.4 GB", "123.0 GB"])


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
