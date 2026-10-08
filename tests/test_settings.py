# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import stat
import tempfile
import unittest

from pvj import settings
from pvj.settings import Settings, SettingsError


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "settings.json")

    def text(self):
        with open(self.path) as f:
            return f.read()

    def read(self, path=None):
        with open(path or self.path) as f:
            return json.load(f)

    def test_first_run_writes_defaults_private(self):
        s = Settings(self.path)
        data = s.load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(len(data["pads"]["banks"]), 3)
        self.assertEqual(len(data["pads"]["banks"][0]["pads"]), 12)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_save_and_reload_keeps_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "night-red"
        s.save()
        self.assertEqual(self.read()["theme"]["name"], "night-red")
        self.assertEqual(self.read(self.path + ".bak")["theme"]["name"], "dark-stage")
        self.assertEqual(Settings(self.path).load()["theme"]["name"], "night-red")

    def test_no_temp_files_left_behind(self):
        s = Settings(self.path)
        s.load()
        self.assertEqual(sorted(os.listdir(self.dir)), ["settings.json"])
        s.save()
        self.assertEqual(sorted(os.listdir(self.dir)), ["settings.json", "settings.json.bak"])

    def test_migration_runs_in_order_and_backs_up_original(self):
        def v0_to_v1(d):
            d["pads"] = {"banks": [{"name": "A", "pads": [{"label": f, "file": f} for f in d.pop("files")]}]}

        def v1_to_v2(d):
            d["theme"] = {"name": d.pop("skin")}

        with open(self.path, "w") as f:
            json.dump({"files": ["a.mp4", "b.mp4"], "skin": "light"}, f)
        s = Settings(self.path, migrations={0: v0_to_v1, 1: v1_to_v2}, current=2)
        data = s.load()
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["theme"], {"name": "light"})
        self.assertEqual(data["pads"]["banks"][0]["pads"][1]["file"], "b.mp4")
        original = self.read(self.path + ".bak-v0")
        self.assertEqual(original["files"], ["a.mp4", "b.mp4"])
        self.assertEqual(self.read()["schema"], 2)

    def test_real_migration_from_schema_1_adds_osc_section(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 1, "theme": {"name": "light", "accent": None}}, f)
        data = Settings(self.path).load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(data["osc"], {"enabled": False, "port": 9876, "allow": []})
        self.assertEqual(data["theme"]["name"], "light")  # existing values untouched
        self.assertEqual(self.read(self.path + ".bak-v1")["schema"], 1)

    def test_real_migration_from_schema_2_adds_an_empty_schedule(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 2, "theme": {"name": "light", "accent": None}}, f)
        data = Settings(self.path).load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(data["schedule"], {"enabled": False, "entries": []})
        self.assertEqual(data["theme"]["name"], "light")
        self.assertEqual(self.read(self.path + ".bak-v2")["schema"], 2)

    def test_real_migration_from_schema_13_goes_through_14_to_15_and_adds_the_ndi_input_last(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 13, "streams": [], "modules": {"enabled": {"inputs-srt": True}}}, f)
        data = settings.Settings(self.path).load()
        self.assertEqual((data["schema"], data["ndi"]), (15, {"addresses": []}))
        self.assertEqual(data["controller_code"], settings.default_controller_code())      # 14, the controller code, ran first
        self.assertEqual(data["modules"]["enabled"], {"inputs-srt": True})         # the NDI module itself stays off
        self.assertEqual(self.read(self.path + ".bak-v13")["schema"], 13)
        self.assertEqual(settings.default_settings()["ndi"], {"addresses": []})
        applied = settings.migrate({"schema": 13})
        self.assertEqual(applied, [13, 14])                                        # 13 to 14, then 14 to 15, in that order
        self.assertIn("NDI", settings.MIGRATIONS[14].__doc__)
        self.assertIn("controller", settings.MIGRATIONS[13].__doc__)

    def test_real_migration_from_schema_14_adds_the_ndi_input_and_leaves_the_controller_code_alone(self):
        code = dict(settings.default_controller_code())
        marker = sorted(code)[0]
        with open(self.path, "w") as f:
            json.dump({"schema": 14, "controller_code": dict(code, **{marker: code[marker]}), "streams": []}, f)
        data = settings.Settings(self.path).load()
        self.assertEqual((data["schema"], data["ndi"], data["controller_code"]), (15, {"addresses": []}, code))
        self.assertEqual(self.read(self.path + ".bak-v14")["schema"], 14)
        self.assertEqual(settings.migrate({"schema": 14}), [14])
        self.assertEqual(settings.SCHEMA, 15)

    def test_real_migration_from_schema_3_adds_empty_streams(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 3, "schedule": {"enabled": True, "entries": []}}, f)
        data = Settings(self.path).load()
        self.assertEqual((data["schema"], data["streams"]), (settings.SCHEMA, []))
        self.assertTrue(data["schedule"]["enabled"])
        self.assertEqual(self.read(self.path + ".bak-v3")["schema"], 3)

    def test_real_migration_from_schema_4_adds_control_off(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 4, "streams": []}, f)
        data = Settings(self.path).load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertFalse(data["control"]["dmx"]["enabled"] or data["control"]["midi"]["enabled"])
        self.assertEqual(self.read(self.path + ".bak-v4")["schema"], 4)

    def test_real_migration_from_schema_5_adds_autostart_off(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 5, "control": {}}, f)
        data = Settings(self.path).load()
        self.assertEqual((data["schema"], data["autostart"]["mode"]), (settings.SCHEMA, "off"))
        self.assertEqual(self.read(self.path + ".bak-v5")["schema"], 5)

    def test_real_migration_from_schema_7_turns_the_single_midi_device_into_the_hub_settings(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 7, "control": {"dmx": {"enabled": True, "protocol": "sacn", "universe": 3, "start": 5, "allow": []},
                                                 "midi": {"enabled": True, "device": "/dev/snd/midiC1D0", "channel": 4}}}, f)
        data = Settings(self.path).load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(data["control"]["midi"], {"enabled": True, "builtin": True, "map": []})   # on stays on; device and channel go
        self.assertTrue(data["control"]["dmx"]["enabled"])                                          # DMX untouched
        self.assertEqual(self.read(self.path + ".bak-v7")["control"]["midi"]["device"], "/dev/snd/midiC1D0")

    def test_real_migration_from_schema_8_adds_the_overlay_off(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 8, "audio": {"device": "auto"}}, f)
        data = Settings(self.path).load()
        self.assertEqual((data["schema"], data["overlay"]), (settings.SCHEMA, {"file": "", "on": False}))

    def test_real_migration_from_schema_9_adds_no_projectors(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 9, "overlay": {"file": "", "on": False}}, f)
        data = Settings(self.path).load()
        self.assertEqual((data["schema"], data["projectors"]), (settings.SCHEMA, []))

    def test_real_migration_from_schema_10_adds_an_empty_mapping(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 10, "projectors": []}, f)
        data = Settings(self.path).load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(data["mapper"], {"on": False, "screen": None, "surfaces": [], "sets": {}})

    def test_newer_file_is_never_rewritten(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 99, "precious": True}, f)
        before = self.text()
        with self.assertRaises(SettingsError):
            Settings(self.path).load()
        self.assertEqual(self.text(), before)
        self.assertEqual(os.listdir(self.dir), ["settings.json"])

    def test_missing_migration_and_bad_files_are_errors(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 0}, f)
        with self.assertRaises(SettingsError):
            Settings(self.path, migrations={}, current=1).load()
        for bad in ("not json", "[1, 2]", json.dumps({"schema": "x"})):
            with open(self.path, "w") as f:
                f.write(bad)
            with self.assertRaises(SettingsError, msg=bad):
                Settings(self.path).load()

    def test_corrupt_main_file_recovers_from_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "light"
        s.save()
        s.data["theme"]["name"] = "night-red"
        s.save()  # main = night-red, .bak = light
        with open(self.path, "w") as f:
            f.write('{"schema": 1, "theme": {"na')  # torn write
        s2 = Settings(self.path)
        self.assertEqual(s2.load()["theme"]["name"], "light")
        self.assertTrue(s2.recovered_from_backup)
        self.assertEqual(self.read()["theme"]["name"], "light")

    def test_missing_main_with_backup_recovers_and_corrupt_main_never_replaces_good_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "light"
        s.save()
        os.unlink(self.path)
        self.assertEqual(Settings(self.path).load()["theme"]["name"], "dark-stage")
        with open(self.path, "w") as f:
            f.write("garbage")
        good_bak = self.read(self.path + ".bak")
        s3 = Settings(self.path)
        s3.data = settings.default_settings()
        s3.save()
        self.assertEqual(self.read(self.path + ".bak"), good_bak)

    def test_concurrent_saves_never_let_an_older_snapshot_win(self):
        import threading
        import time as _time
        s = Settings(self.path)
        s.load()
        real = Settings._write_text
        first = threading.Event()

        def slow(path, text):
            if not first.is_set() and path == self.path:  # only the first main-file write is slow
                first.set()
                _time.sleep(0.3)
            return real(path, text)
        Settings._write_text = staticmethod(slow)
        self.addCleanup(setattr, Settings, "_write_text", staticmethod(real))

        def edit(key, value):
            with s.lock:
                s.data[key] = value
                s.save()
        a = threading.Thread(target=edit, args=("theme_a", 1))
        b = threading.Thread(target=edit, args=("theme_b", 2))
        a.start()
        first.wait(2)
        b.start()
        a.join()
        b.join()
        on_disk = self.read()
        self.assertEqual((on_disk.get("theme_a"), on_disk.get("theme_b")), (1, 2))

    def test_failed_migration_leaves_original_untouched(self):
        def boom(d):
            raise RuntimeError("bug")

        with open(self.path, "w") as f:
            json.dump({"x": 1}, f)
        before = self.text()
        with self.assertRaises(RuntimeError):
            Settings(self.path, migrations={0: boom}, current=1).load()
        self.assertEqual(self.text(), before)


if __name__ == "__main__":
    unittest.main()
