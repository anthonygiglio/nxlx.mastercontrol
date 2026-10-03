# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Settings export and import, the diagnostics file and factory reset (pvj/boxcare.py)."""
import copy
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from pvj import boxcare, settings as settings_mod
from pvj.api import ApiError
from tests.test_support import CFG, LAN, TUNNEL, SupportBase

PROJECTOR_PASSWORD = "Pj-Sentinel-77"
STREAM_USER, STREAM_PASSWORD, STREAM_PHRASE = "camuser-sentinel", "CamPw-Sentinel-55", "Phrase-Sentinel-33"
STREAM_KEY = "streamkey-sentinel-11"
PIN = "4821"


def quad(sid, x=100):
    return {"id": sid, "type": "quad", "name": "Wall", "on": True,
            "vertices": [[x, 100], [x + 400, 100], [x + 400, 400], [x, 400]], "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}


class Base(SupportBase):
    def setUp(self):
        super().setUp()
        self.care = self.api.boxcare
        self.view_dev = self.auth.authenticate(self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"])
        d = self.settings.data
        d["projectors"] = [{"id": "aaaa0001", "name": "Left", "host": "192.168.1.50", "port": 4352, "password": PROJECTOR_PASSWORD},
                           {"id": "aaaa0002", "name": "Right", "host": "192.168.1.51", "port": 4352, "password": ""}]
        d["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://%s:%s@192.168.1.60:554/live" % (STREAM_USER, STREAM_PASSWORD)},
                        {"id": "bbbb0002", "name": "Truck", "url": "srt://192.168.1.61:9000?mode=caller&passphrase=%s" % STREAM_PHRASE},
                        {"id": "bbbb0003", "name": "Open", "url": "rtmp://192.168.1.62/live/%s" % STREAM_KEY}]
        d["pads"]["banks"][0]["pads"][0] = {"label": "Intro", "file": "a.mp4", "ending": "hold"}
        d["osc"] = {"enabled": False, "port": 9001, "allow": ["192.168.7.0/24"]}
        d["mix"] = {"transition": "dip", "duration": 2.5}
        self.settings.save()

    def export(self, **body):
        st, out = self.h("POST", "/api/system/settings/export", body, self.full_dev)
        self.assertEqual(st, 200, out)
        return out["file"]

    def send(self, file, token=None, confirm="import", raw=None, **kw):
        """Import over HTTP, as the panel does: the file is the body."""
        data = raw if raw is not None else json.dumps(file).encode()
        path = "/api/system/settings/import" + ("?confirm=" + confirm if confirm else "")
        st, body, _ = self.call("POST", path, raw=data, token=token or self.full, **kw)
        return st, body

    def on_disk(self):
        with open(self.settings.path, "rb") as f:
            return f.read()

    def remote_login(self):
        """A support login (full role) through the tunnel."""
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)[0], 200)
        code = self.h("POST", "/api/support/start", {"confirm": "start"}, self.full_dev)[1]["code"]
        token = self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"]
        return self.api.support.authenticate(token), code


class StrictJsonTest(unittest.TestCase):
    def refused(self, raw, word=None):
        with self.assertRaises(ApiError) as c:
            boxcare.parse_strict(raw)
        if word:
            self.assertIn(word, c.exception.message)
        return c.exception

    def test_a_plain_object_is_read(self):
        self.assertEqual(boxcare.parse_strict(b'{"a": [1, 2.5, "x", true, null], "b": {"c": -3}}'),
                         {"a": [1, 2.5, "x", True, None], "b": {"c": -3}})

    def test_repeated_keys_are_refused_at_every_level(self):
        self.refused(b'{"a": 1, "a": 2}', "twice")
        self.refused(b'{"x": {"deep": [{"k": 1, "k": 2}]}}', "twice")

    def test_numbers_that_are_not_numbers_are_refused(self):
        for raw in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}', b'{"a": 1e999}', b'{"a": -1e999}',
                    b'{"a": ' + b"9" * 60 + b'}', b'{"a": 0.' + b"1" * 60 + b'}'):
            self.refused(raw)

    def test_only_utf8_text_holding_one_object(self):
        self.refused('{"a": 1}'.encode("utf-16"), "UTF-8")
        self.refused('{"a": 1}'.encode("utf-32"), "UTF-8")
        self.refused(b'{"a": "\xff\xfe"}', "UTF-8")
        self.refused(b'\xef\xbb\xbf{"a": 1}')                  # a byte order mark is not JSON
        self.refused(b'[1, 2]')
        self.refused(b'"text"')
        self.refused(b'{"a": 1} {"b": 2}')
        self.refused(b'{"a": 1,}')
        self.refused(b'')
        self.refused('{"a": 1}')                               # text, not the bytes of the body

    def test_size_and_depth_are_bounded(self):
        e = self.refused(b'{"a": "' + b"x" * boxcare.MAX_IMPORT + b'"}')
        self.assertEqual(e.status, 413)
        self.refused(b'{"a": ' + b"[" * 40 + b"]" * 40 + b'}', "nested")
        self.refused(b'{"a": ' + b"[" * 100000 + b"]" * 100000 + b'}', "nested")


class StreamAddressTest(unittest.TestCase):
    def test_only_the_secrets_are_cut_and_every_other_byte_stays(self):
        srt = "srt://192.168.1.61:9000?mode=caller&streamid=#!::r=live/cam 1,m=request&latency=120"
        for url, want in (
                ("rtsp://user:pw@192.168.1.60:554/live?x=1", "rtsp://192.168.1.60:554/live?x=1"),
                ("rtsp://user@cam.local/live", "rtsp://cam.local/live"),
                ("rtsp://192.168.1.60/a%20b/c?q=%41&r=a+b", "rtsp://192.168.1.60/a%20b/c?q=%41&r=a+b"),
                (srt, srt),
                (srt + "&passphrase=Secret-1234567", srt),
                ("srt://192.168.1.61:9000?passphrase=Secret-1234567&" + srt.split("?")[1], srt),
                ("srt://192.168.1.61:9000?PassPhrase=Secret-1234567", "srt://192.168.1.61:9000"),
                ("srt://192.168.1.61:9000?pbkeylen=16&passphrase=x", "srt://192.168.1.61:9000?pbkeylen=16"),
                ("srt://192.168.1.61:9000?", "srt://192.168.1.61:9000?"),
                ("srt://[fe80::1]:9000?passphrase=x", "srt://[fe80::1]:9000"),
                ("rtmp://u:p@192.168.1.62/live/key", "rtmp://192.168.1.62/live/key")):
            self.assertEqual(boxcare.strip_login(url), want)
            self.assertEqual(boxcare.strip_login(want), want)
        self.assertEqual(boxcare.strip_login(None), "")
        self.assertEqual(boxcare.strip_login("not an address"), "")

    def test_the_secret_pieces_are_found_even_behind_a_hash(self):
        url = "srt://me:pw-1@192.168.1.61:9000?streamid=#!::r=live&passphrase=Phrase-9"
        self.assertEqual(sorted(boxcare.stream_secrets(url)), ["Phrase-9", "me", "pw-1"])
        self.assertEqual(boxcare.stream_secrets("rtsp://192.168.1.60/live"), [])
        self.assertEqual(boxcare.stream_secrets(None), [])
        self.assertEqual(boxcare.stream_where(url), "srt://192.168.1.61:9000")


class ExportTest(Base):
    def test_default_export_holds_no_secret(self):
        self.h("POST", "/api/access/code", {"role": "view"}, self.full_dev)
        file = self.export()
        text = json.dumps(file)
        self.assertEqual((file["format"], file["format_version"], file["passwords_included"]), (boxcare.FORMAT, 1, False))
        self.assertEqual(file["settings"]["schema"], settings_mod.SCHEMA)
        for name in boxcare.NEVER:
            self.assertNotIn(name, file["settings"])
        secrets = [PROJECTOR_PASSWORD, STREAM_USER, STREAM_PASSWORD, STREAM_PHRASE, self.pin, self.full,
                   self.settings.data["auth"]["pin_hash"], self.settings.data["auth"]["pin_salt"]]
        secrets += [d["token_hash"] for d in self.settings.data["devices"]]
        secrets += [j["code"] for j in self.auth.list_joins()]
        for secret in secrets:
            self.assertNotIn(secret, text)
        self.assertNotIn("token", text)
        self.assertEqual([p["password"] for p in file["settings"]["projectors"]], ["", ""])
        self.assertEqual([s["url"] for s in file["settings"]["streams"]],
                         ["rtsp://192.168.1.60:554/live", "srt://192.168.1.61:9000?mode=caller", "rtmp://192.168.1.62/live/" + STREAM_KEY])
        self.assertEqual(file["settings"]["pads"]["banks"][0]["pads"][0]["file"], "a.mp4")

    def test_passwords_only_when_asked_and_never_the_access_data(self):
        self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)
        file = self.export(passwords=True)
        text = json.dumps(file)
        self.assertTrue(file["passwords_included"])
        self.assertEqual(file["settings"]["projectors"][0]["password"], PROJECTOR_PASSWORD)
        self.assertIn(STREAM_PASSWORD, text)
        self.assertIn(STREAM_PHRASE, text)
        for secret in (CFG["server_key"], CFG["endpoint"], self.settings.data["auth"]["pin_hash"], self.settings.data["devices"][0]["token_hash"]):
            self.assertNotIn(secret, text)
        for name in boxcare.NEVER:
            self.assertNotIn(name, file["settings"])
        self.assertEqual(self.h("POST", "/api/system/settings/export", {"passwords": "yes"}, self.full_dev)[0], 400)

    def test_who_may_export(self):
        self.assertEqual(self.h("POST", "/api/system/settings/export", {}, None)[0], 401)
        self.assertEqual(self.h("POST", "/api/system/settings/export", {}, self.view_dev)[0], 403)
        self.assertEqual(self.h("POST", "/api/system/settings/export", {}, self.live_dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/system/settings/export", {}, self.full_dev)[0], 405)
        self.assertEqual(self.call("POST", "/api/system/settings/export", {}, token=self.full, csrf=False)[0], 403)

    def test_passwords_are_not_handed_through_the_support_tunnel(self):
        dev, _ = self.remote_login()
        st, out = self.h("POST", "/api/system/settings/export", {}, dev, TUNNEL)
        self.assertEqual(st, 200)
        self.assertNotIn(PROJECTOR_PASSWORD, json.dumps(out))
        st, out = self.h("POST", "/api/system/settings/export", {"passwords": True}, dev, TUNNEL)
        self.assertEqual(st, 403)
        self.assertNotIn(PROJECTOR_PASSWORD, json.dumps(out))


class ImportTest(Base):
    def test_round_trip_keeps_access_and_a_backup(self):
        from pvj import autostart
        file = self.export()
        before = copy.deepcopy(self.settings.data)
        before["autostart"] = autostart.validate({}, before["autostart"])      # the check fills in keys added since
        d = self.settings.data
        d["mix"] = {"transition": "cut", "duration": 1.0}
        d["pads"]["banks"][0]["pads"][0] = {"label": "", "file": ""}
        d["osc"]["port"] = 9999
        self.settings.save()
        changed = self.on_disk()
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["problems"], [])
        self.assertEqual(self.settings.data, before)             # passwords too: kept from the box
        self.assertEqual(out["passwords_kept"], 3)
        self.assertEqual(self.call("GET", "/api/devices", token=self.full)[0], 200)      # still paired
        self.assertEqual(self.settings.data["devices"], before["devices"])
        backup = os.path.join(self.tmp, out["backup"])
        with open(backup, "rb") as f:
            self.assertEqual(f.read(), changed)
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        with open(self.settings.path) as f:
            self.assertEqual(json.load(f), before)

    def test_a_file_with_passwords_brings_them(self):
        file = self.export(passwords=True)
        self.settings.data["projectors"] = []
        self.settings.data["streams"] = []
        st, out = self.send(file)
        self.assertEqual((st, out["passwords_kept"], out["passwords_in_file"]), (200, 0, True))
        self.assertEqual(self.settings.data["projectors"][0]["password"], PROJECTOR_PASSWORD)
        self.assertIn(STREAM_PASSWORD, self.settings.data["streams"][0]["url"])

    def test_a_stream_address_arrives_unchanged_on_a_box_that_never_had_it(self):
        srt = "srt://192.168.1.61:9000?mode=caller&streamid=#!::r=live/cam,m=request"
        self.settings.data["streams"] = [{"id": "bbbb0009", "name": "Truck", "url": srt + "&passphrase=" + STREAM_PHRASE}]
        file = self.export()
        self.assertEqual(file["settings"]["streams"][0]["url"], srt)
        self.settings.data["streams"] = []
        st, out = self.send(file)
        self.assertEqual((st, out["passwords_kept"]), (200, 1))          # the projector's; there was no stream to keep one from
        self.assertEqual(self.settings.data["streams"], [{"id": "bbbb0009", "name": "Truck", "url": srt}])

    def test_a_kept_password_never_follows_a_changed_address(self):
        file = self.export()
        file["settings"]["projectors"][0]["host"] = "192.168.1.99"
        file["settings"]["streams"][0]["url"] = "rtsp://192.168.1.99:554/live"
        self.assertEqual(self.send(file)[0], 200)
        self.assertEqual(self.settings.data["projectors"][0], {"id": "aaaa0001", "name": "Left", "host": "192.168.1.99", "port": 4352, "password": ""})
        self.assertEqual(self.settings.data["streams"][0]["url"], "rtsp://192.168.1.99:554/live")
        self.assertIn(STREAM_PHRASE, self.settings.data["streams"][1]["url"])       # unchanged address: kept

    def test_a_newer_file_is_refused_plainly_and_nothing_changes(self):
        file = self.export()
        file["settings"]["schema"] = settings_mod.SCHEMA + 1
        before = self.on_disk()
        st, out = self.send(file)
        self.assertEqual(st, 409)
        self.assertIn("newer version", out["error"])
        self.assertEqual(self.on_disk(), before)
        self.assertEqual(self.care._siblings(".before-import-"), [])

    def test_an_older_file_is_migrated(self):
        file = self.export()
        s = file["settings"]
        s["schema"] = 9
        for name in ("projectors", "mapper", "sync"):          # added by schemas 10, 11 and 13
            del s[name]
        self.settings.data["sync"]["group"] = "stage"
        self.settings.data["mapper"]["on"] = True
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        d = self.settings.data
        self.assertEqual(d["schema"], settings_mod.SCHEMA)
        self.assertEqual((d["projectors"], d["sync"], d["mapper"]["on"]), ([], settings_mod.default_settings()["sync"], False))
        self.assertTrue(d["devices"])                           # the migration to 12 adds "support": the box's own stays
        for bad in (0, -1, True, "13", None, 1.5):
            file["settings"]["schema"] = bad
            self.assertEqual(self.send(file)[0], 400, bad)
        del file["settings"]["schema"]
        self.assertEqual(self.send(file)[0], 400)

    def test_what_a_later_schema_adds_comes_from_its_own_migration(self):
        """The checks know schema 13. On a box with a later schema, what that schema adds is put back by its
        migration, and a section it adds keeps the box's value."""
        now, later = settings_mod.SCHEMA, settings_mod.SCHEMA + 1

        def to_later(data):               # like a real one: not written to run twice, and it sets up a new section
            for p in data.get("projectors", []):
                p.setdefault("input", "hdmi1")
            data["shaders"] = {"on": False}
            if "devices" in data:
                data["devices"] = []
        self.settings._migrations = dict(settings_mod.MIGRATIONS)
        self.settings._migrations[now] = to_later
        self.settings._current = later
        file = self.export()
        self.assertEqual(file["settings"]["schema"], now)
        self.assertEqual(sorted(file["settings"]["projectors"][0]), ["host", "id", "name", "password", "port"])
        self.settings.data["schema"] = later
        self.settings.data["shaders"] = {"on": True}
        for p in self.settings.data["projectors"]:
            p["input"] = "hdmi2"
        devices = copy.deepcopy(self.settings.data["devices"])
        defaults = dict(settings_mod.default_settings(), shaders={"on": False})
        with mock.patch.object(boxcare, "default_settings", lambda: copy.deepcopy(defaults)):
            st, out = self.send(file)
            self.assertEqual(st, 200, out)
            d = self.settings.data
            self.assertEqual((d["schema"], d["shaders"], d["devices"]), (later, {"on": True}, devices))   # the box's own: untouched
            self.assertEqual([p["input"] for p in d["projectors"]], ["hdmi1", "hdmi1"])
            self.assertEqual(d["projectors"][0]["password"], PROJECTOR_PASSWORD)
            file["settings"]["schema"] = later
            file["settings"]["shaders"] = {"on": False}
            st, out = self.send(file)
            self.assertEqual(st, 200, out)
            self.assertIn("shaders is not imported by this version; left as it is", out["notes"])
            self.assertEqual((self.settings.data["shaders"], self.settings.data["devices"]), ({"on": True}, devices))
            file["settings"]["schema"] = later + 1
            self.assertEqual(self.send(file)[0], 409)

            def broken(data):
                raise KeyError("auth")
            self.settings._migrations[now] = broken           # a migration that needs what an export never holds
            file["settings"]["schema"] = later
            before = self.on_disk()
            st, out = self.send(file)
            self.assertEqual(st, 400, out)
            self.assertEqual(self.on_disk(), before)

    def test_every_settings_section_is_either_checked_or_never_exported(self):
        """A new top-level section must be given a check in boxcare.SECTIONS (and KNOWN_SCHEMA raised), or be listed
        in NEVER: otherwise it would silently be missing from every export."""
        known = {name for name, _ in boxcare.SECTIONS} | set(boxcare.NEVER) | {"schema"}
        self.assertEqual(sorted(set(settings_mod.default_settings()) - known), [])
        self.assertLessEqual(boxcare.KNOWN_SCHEMA, settings_mod.SCHEMA)

    def test_access_data_in_a_file_is_refused(self):
        for name, value in (("devices", [{"id": "x", "name": "evil", "role": "full", "token_hash": "0" * 64, "created": 1}]),
                            ("auth", {"pin_hash": "00", "pin_salt": "00"}), ("support", dict(CFG, allowed=True)), ("support_log", [])):
            file = self.export()
            file["settings"][name] = value
            before = self.on_disk()
            st, out = self.send(file)
            self.assertEqual(st, 400, name)
            self.assertIn("never imported", out["error"])
            self.assertEqual(self.on_disk(), before)

    def test_what_is_not_a_settings_export_is_refused(self):
        good = self.export()
        before = self.on_disk()
        cases = [dict(good, format="something else"), dict(good, format_version=2), dict(good, extra=1), dict(good, settings=[]),
                 dict(good, passwords_included="no"), {k: v for k, v in good.items() if k != "settings"},
                 dict(good, settings=dict(good["settings"], surprise={"x": 1})), self.settings.data]
        for file in cases:
            st, out = self.send(file)
            self.assertIn(st, (400, 409), out)
            self.assertEqual(self.on_disk(), before)
        text = json.dumps(good)
        for raw in (text.replace('"settings": {', '"settings": {"mix": {"transition": "cut", "duration": 1}, ', 1).encode(),   # a repeated key
                    text.replace('"duration": 2.5', '"duration": NaN').encode(), text.replace('"duration": 2.5', '"duration": 1e999').encode(),
                    text.encode("utf-16"), b"[]", b"{", b""):
            st, out = self.send(None, raw=raw)
            self.assertEqual(st, 400, out)
            self.assertEqual(self.on_disk(), before)

    def test_every_section_is_checked_and_one_bad_value_stops_the_whole_import(self):
        good = self.export()
        before = self.on_disk()

        def with_(section, value):
            file = copy.deepcopy(good)
            file["settings"][section] = value
            return file
        s = good["settings"]
        bad = [
            ("pads", {"banks": s["pads"]["banks"][:2]}),
            ("pads", {"banks": [dict(b, pads=[dict(p, file="../etc/passwd") for p in b["pads"]]) for b in s["pads"]["banks"]]}),
            ("pads", {"banks": [dict(b, pads=b["pads"][:11]) for b in s["pads"]["banks"]]}),
            ("modules", {"enabled": {"scheduler": "yes"}}),
            ("theme", {"name": "dark-stage", "accent": "red"}),
            ("mix", {"transition": "wipe", "duration": 1}),
            ("mix", {"transition": "cut", "duration": True}),
            ("osc", {"enabled": True, "port": 80, "allow": []}),
            ("osc", {"enabled": True, "port": 9000, "allow": ["0.0.0.0/0"]}),
            ("schedule", {"enabled": True, "entries": [{"time": "25:00", "days": [0], "action": "stop"}]}),
            ("streams", [{"id": "bbbb0001", "name": "x", "url": "http://192.168.1.2/x"}]),
            ("streams", [{"id": "bbbb0001", "name": "x", "url": "rtsp://192.168.1.2/x"}, {"id": "bbbb0001", "name": "y", "url": "rtsp://192.168.1.3/x"}]),
            ("control", {"dmx": {"enabled": True, "protocol": "artnet", "universe": 99999, "start": 1, "allow": []}, "midi": {}}),
            ("control", {"dmx": {}, "midi": {"enabled": True, "map": [{"kind": "note", "number": 300, "action": "pad"}]}}),
            ("autostart", {"mode": "file", "file": ""}),
            ("autostart", {"mode": "pad", "pad": [2, 5]}),                  # that pad has no clip
            ("audio", {"device": 5}),
            ("overlay", {"file": "logo.mp4", "on": True}),
            ("overlay", {"file": "", "on": True}),
            ("projectors", [{"name": "P", "host": "8.8.8.8", "port": 4352, "password": ""}]),
            ("projectors", [{"name": "P", "host": "192.168.1.5", "port": 4352, "password": "has space"}]),
            ("projectors", "none"),
            ("mapper", {"on": True, "screen": [1920, 1080], "surfaces": [dict(quad("cccc0001"), vertices=[[0, 0], [10, 10], [0, 10], [10, 0]])], "sets": {}}),
            ("mapper", {"on": False, "screen": [0, 0], "surfaces": [], "sets": {}}),
            ("mapper", {"on": False, "screen": None, "surfaces": [], "sets": {"bad/name": {"screen": [1, 1], "surfaces": []}}}),
            ("sync", {"role": "boss"}),
            ("sync", {"wall": {"cols": 2, "rows": 2, "col": 5, "row": 0}}),
        ]
        for section, value in bad:
            st, out = self.send(with_(section, value))
            self.assertEqual(st, 400, (section, value, out))
            self.assertTrue(out["error"].startswith(section + ":"), out)
            self.assertEqual(self.on_disk(), before)
        self.assertEqual(self.care._siblings(".before-import-"), [])
        self.assertEqual(self.send(good)[0], 200)

    def test_modules_this_box_cannot_run_are_left_off(self):
        file = self.export()
        board = self.api.registry.board
        planned = next(m for m in self.api.registry.manifests.values() if m["status"] != "ready")
        self.api.registry.manifests["projector"] = dict(self.api.registry.manifests["projector"], boards=["pi5"])
        self.assertNotEqual(board, "pi5")
        file["settings"]["modules"] = {"enabled": {"projector": True, planned["id"]: True, "no-such-module": True, "scheduler": True, "core": False}}
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["modules"]["enabled"], {"projector": False, planned["id"]: False, "scheduler": True})
        self.assertFalse(self.api.registry.enabled("projector"))
        self.assertEqual(len([n for n in out["notes"] if n.startswith("module ")]), 3)

    def test_a_module_whose_requirement_is_off_is_left_off(self):
        m = self.api.registry.manifests           # no shipped module needs another optional one yet: make a chain
        m["scheduler"] = dict(m["scheduler"], requires=["core", "projector"])
        m["projector"] = dict(m["projector"], requires=["core", "mapper"])
        file = self.export()
        file["settings"]["modules"] = {"enabled": {"scheduler": True, "projector": True, "mapper": False}}
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["modules"]["enabled"], {"scheduler": False, "projector": False, "mapper": False})
        file["settings"]["modules"] = {"enabled": {"scheduler": True, "projector": True, "mapper": True}}
        self.assertEqual(self.send(file)[0], 200)
        self.assertEqual(self.settings.data["modules"]["enabled"], {"scheduler": True, "projector": True, "mapper": True})

    def test_a_full_mapper_is_larger_than_an_ordinary_request_and_still_imports(self):
        from pvj import server
        file = self.export()
        grid = {"id": "cccc0001", "type": "grid", "name": "Dome", "on": True, "cols": 8, "rows": 7,
                "vertices": [[100.123456 * c + 10, 90.654321 * r + 10] for r in range(8) for c in range(9)],
                "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}
        file["settings"]["mapper"] = {"on": False, "screen": [1920, 1080], "surfaces": [grid],
                                      "sets": {"Set %d" % n: {"screen": [1920, 1080], "surfaces": [grid]} for n in range(8)}}
        raw = json.dumps(file, indent=2).encode()
        self.assertGreater(len(raw), server.MAX_BODY)
        self.assertLess(len(raw), boxcare.MAX_IMPORT)
        st, out = self.send(None, raw=raw)
        self.assertEqual(st, 200, out)
        self.assertEqual(len(self.settings.data["mapper"]["sets"]), 8)
        # too large is refused from its declared length, before a byte of it is read
        self.assertEqual(self.send(file, headers={"Content-Length": str(boxcare.MAX_IMPORT + 1)})[0], 413)
        self.assertEqual(self.send(file, headers={"Content-Length": "-5"})[0], 413)

    def test_who_may_import_and_how(self):
        file = self.export()
        before = self.on_disk()
        live = self.call("POST", "/api/devices/invite", {"name": "p2", "role": "live"}, token=self.full)[1]["token"]
        before = self.on_disk()
        self.assertEqual(self.send(file, token="nobody")[0], 401)
        self.assertEqual(self.send(file, token=live)[0], 403)
        self.assertEqual(self.send(file, confirm=None)[0], 400)
        self.assertEqual(self.send(file, confirm="yes")[0], 400)
        self.assertEqual(self.send(file, csrf=False)[0], 403)
        self.assertEqual(self.send(file, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("GET", "/api/system/settings/import", token=self.full)[0], 405)
        self.assertEqual(self.h("POST", "/api/system/settings/import", {}, self.full_dev)[0], 400)      # not as an ordinary request
        self.assertEqual(self.on_disk(), before)

    def test_no_import_through_the_support_tunnel(self):
        file = self.export()
        dev, code = self.remote_login()
        before = self.on_disk()
        self.assertEqual(self.h("POST", "/api/system/settings/import", {}, dev, TUNNEL)[0], 403)
        with self.assertRaises(ApiError) as c:                   # and the function itself refuses, whatever the route table says
            self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(c.exception.status, 403)
        self.api.support.is_remote = lambda client: True         # over HTTP, pretending 127.0.0.1 is in the tunnel
        token = self.call("POST", "/api/support/login", {"code": code})[1]["token"]
        self.assertEqual(self.send(file, token=token)[0], 403)
        self.assertEqual(self.on_disk(), before)

    def test_not_while_an_update_runs(self):
        result = os.path.join(tempfile.mkdtemp(), "result.json")
        with open(result, "w") as f:
            json.dump({"state": "running", "message": "installing", "at": int(time.time())}, f)
        with mock.patch.dict(os.environ, {"PVJ_UPDATE_RESULT": result}):
            self.assertEqual(self.send(self.export())[0], 409)

    def test_only_the_last_few_backups_are_kept(self):
        file = self.export()
        t = [1790000000]
        self.care._now = lambda: t[0]
        for _ in range(boxcare.KEEP_IMPORT_BACKUPS + 2):
            t[0] += 10
            self.assertEqual(self.send(file)[0], 200)
        kept = self.care._siblings(".before-import-")
        self.assertEqual(len(kept), boxcare.KEEP_IMPORT_BACKUPS)
        self.assertTrue(kept[-1].endswith(str(t[0])))

    def test_what_changed_is_applied_to_the_running_box(self):
        file = self.export()
        file["settings"]["osc"] = {"enabled": True, "port": 0, "allow": []}
        self.assertEqual(self.send(file)[0], 400)
        applied = []
        self.api.osc.apply = lambda: applied.append("osc")
        self.api.sync.apply = lambda: applied.append("sync")

        def broken():
            raise RuntimeError("no screen")
        self.api.mapper.apply = broken
        st, out = self.send(self.export())
        self.assertEqual(st, 200)
        self.assertEqual(applied, ["osc", "sync"])
        self.assertEqual(out["problems"], ["Mapper: no screen"])         # reported, and the rest still happened


class FakeJournal:
    def __init__(self, stdout="", stderr="", code=0, error=None):
        self.stdout, self.stderr, self.code, self.error, self.argv = stdout, stderr, code, error, None

    def __call__(self, argv, **kw):
        self.argv = argv
        if self.error:
            raise self.error

        class R:
            returncode, stdout, stderr = self.code, self.stdout, self.stderr
        return R()


class DiagnosticsTest(Base):
    def setUp(self):
        super().setUp()
        self.auth.set_pin(PIN)

    def get(self, device=None, client=LAN):
        st, out = self.h("GET", "/api/system/diagnostics", {}, device or self.full_dev, client)
        self.assertEqual(st, 200, out)
        return out["file"]

    def test_no_secret_is_in_it(self):
        self.h("POST", "/api/access/code", {"role": "view"}, self.full_dev)
        self.h("POST", "/api/access/code", {"role": "live"}, self.full_dev)
        dev, code = self.remote_login()
        joins = [j["code"] for j in self.auth.list_joins()]
        self.assertEqual(len(joins), 2)
        raw_code = code.replace("-", "")
        log = "\n".join([
            "2026-10-03T10:00:00+0000 box pvj-web[1]: pvj-web: listening on 0.0.0.0:80; pairing PIN %s (also in /run/pvj/pin)" % PIN,
            "2026-10-02T09:00:00+0000 box pvj-web[1]: pvj-web: listening on 0.0.0.0:80; pairing PIN 7305 (also in /run/pvj/pin)",
            "2026-10-03T10:00:01+0000 box pvj-player[2]: [ffmpeg] Opening rtsp://%s:%s@192.168.1.60:554/live" % (STREAM_USER, STREAM_PASSWORD),
            "2026-10-03T10:00:02+0000 box pvj-player[2]: srt://192.168.1.61:9000?mode=caller&passphrase=%s failed" % STREAM_PHRASE,
            "2026-10-03T10:00:03+0000 box pvj-player[2]: rtsp://someone:OtherPw-99@192.168.1.70/x and ?passphrase=Other-Phrase-88",
            "2026-10-03T10:00:04+0000 box pvj-web[1]: projector said ERRA for %s" % PROJECTOR_PASSWORD,
            "2026-10-03T10:00:05+0000 box pvj-web[1]: join code %s, support code %s or %s, key %s" % (joins[0], code, raw_code, CFG["server_key"]),
            "2026-10-03T10:00:06+0000 box pvj-web[1]: 192.168.1.20 \"GET /api/status HTTP/1.1\" 200 -",
        ])
        journal = FakeJournal(stdout=log)
        self.care._run = journal
        self.player.status = lambda: {"running": True, "path": self.settings.data["streams"][0]["url"]}
        file = self.get()
        text = json.dumps(file)
        d = self.settings.data
        secrets = [PIN, "7305", PROJECTOR_PASSWORD, STREAM_USER, STREAM_PASSWORD, STREAM_PHRASE, STREAM_KEY, "OtherPw-99", "Other-Phrase-88",
                   code, raw_code, CFG["server_key"], CFG["endpoint"], CFG["address"], self.full,
                   d["auth"]["pin_hash"], d["auth"]["pin_salt"]] + joins + [x["token_hash"] for x in d["devices"]]
        secrets += list(self.api.support.session["tokens"])
        for secret in secrets:
            self.assertNotIn(secret, text, secret)
        for word in ("token_hash", "pin_hash", "pin_salt", "server_key", '"password"'):
            self.assertNotIn(word, text)
        # and it is still worth reading
        self.assertTrue(file["log"]["readable"])
        self.assertEqual(len(file["log"]["lines"]), 8)
        self.assertIn("GET /api/status", file["log"]["lines"][-1])
        self.assertIn("pairing PIN (removed)", file["log"]["lines"][0])
        s = file["settings"]
        self.assertEqual(s["projectors"][0], {"id": "aaaa0001", "name": "Left", "host": "192.168.1.50", "port": 4352, "has_password": True})
        self.assertEqual(s["streams"], [{"id": "bbbb0001", "name": "Camera", "from": "rtsp://192.168.1.60:554", "has_login": True},
                                        {"id": "bbbb0002", "name": "Truck", "from": "srt://192.168.1.61:9000", "has_login": True},
                                        {"id": "bbbb0003", "name": "Open", "from": "rtmp://192.168.1.62", "has_login": False}])
        self.assertEqual(s["support"], {"allowed": True, "max_minutes": 240, "server_set": True, "configured": True})
        self.assertEqual([x["role"] for x in s["devices"]], [x["role"] for x in d["devices"]])
        self.assertEqual(s["pads"]["banks"][0]["pads"][0]["file"], "a.mp4")
        self.assertEqual(s["osc"]["port"], 9001)
        self.assertNotIn("auth", s)
        from pvj import __version__
        self.assertEqual((file["version"], file["settings_schema"], file["board"]["kind"]), (__version__, settings_mod.SCHEMA, "x86"))
        self.assertIn("overall", file["health"])
        self.assertTrue(any(m["id"] == "scheduler" for m in file["modules"]))
        self.assertIn("disk", file["system"])
        # the same file through the tunnel (support is who it is for)
        self.assertNotIn(PROJECTOR_PASSWORD, json.dumps(self.get(dev, TUNNEL)))

    def test_the_log_is_asked_for_with_fixed_arguments(self):
        journal = FakeJournal(stdout="a line\n")
        self.care._run = journal
        self.get()
        self.assertEqual(journal.argv[0], "journalctl")
        self.assertIsInstance(journal.argv, list)
        for unit in boxcare.LOG_UNITS:
            self.assertIn(unit, journal.argv)
        self.assertEqual(journal.argv[:6], ["journalctl", "--no-pager", "-q", "-b", "-n", str(boxcare.LOG_LINES)])

    def test_a_log_that_cannot_be_read_is_said_so(self):
        hint = "Hint: You are currently not seeing messages from other users and the system.\n"
        for journal, word in ((FakeJournal(stderr=hint), "not allowed to read"),
                              (FakeJournal(stderr="No journal files were opened due to insufficient permissions.", code=1), "insufficient permissions"),
                              (FakeJournal(error=FileNotFoundError(2, "No such file or directory")), "could not be read"),
                              (FakeJournal(error=subprocess.TimeoutExpired("journalctl", 10)), "could not be read")):
            self.care._run = journal
            log = self.get()["log"]
            self.assertEqual((log["readable"], log["lines"]), (False, []))
            self.assertIn(word, log["note"])

    def test_a_long_log_is_cut(self):
        self.care._run = FakeJournal(stdout="\n".join("line %d %s" % (n, "x" * 2000) for n in range(1000)))
        lines = self.get()["log"]["lines"]
        self.assertEqual(len(lines), boxcare.LOG_LINES)
        self.assertTrue(all(len(line) <= boxcare.LOG_LINE_MAX for line in lines))
        self.assertTrue(lines[-1].startswith("line 999 "))

    def test_a_missing_piece_does_not_cost_the_rest(self):
        self.care._run = FakeJournal()

        def boom():
            raise RuntimeError("sysfs gone " + PROJECTOR_PASSWORD)
        self.api.health.report = boom
        file = self.get()
        self.assertEqual(file["health"], {"error": "not available: RuntimeError"})
        self.assertIn("settings", file)

    def test_a_secret_under_a_new_name_is_caught_by_the_net(self):
        self.care._run = FakeJournal()
        self.settings.data["control"]["midi"]["api_token"] = "Tok-Sentinel-1"
        self.settings.data["sync"]["password"] = "Sync-Sentinel-2"
        self.settings.data["osc"]["shared_key"] = "Osc-Sentinel-3"
        self.settings.data["shaders"] = {"licence": "Shader-Sentinel-4"}       # a section this version does not know
        file = self.get()
        text = json.dumps(file)
        for secret in ("Tok-Sentinel-1", "Sync-Sentinel-2", "Osc-Sentinel-3", "Shader-Sentinel-4"):
            self.assertNotIn(secret, text)
        self.assertEqual(file["settings"]["not_shown"], ["shaders", "auth (the PIN)"])

    def test_who_may_read_it(self):
        self.care._run = FakeJournal()
        self.assertEqual(self.h("GET", "/api/system/diagnostics", {}, None)[0], 401)
        self.assertEqual(self.h("GET", "/api/system/diagnostics", {}, self.view_dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/system/diagnostics", {}, self.live_dev)[0], 403)
        self.assertEqual(self.h("POST", "/api/system/diagnostics", {}, self.full_dev)[0], 405)
        self.assertEqual(self.call("GET", "/api/system/diagnostics", token=self.full)[0], 200)

    def test_the_web_service_is_not_given_the_system_log(self):
        """The bundle says the log is not readable because the unit keeps it that way; if that changes, the scrubbing
        above becomes the only thing between the PIN in the log and the file."""
        with open(os.path.join(os.path.dirname(__file__), "..", "install", "pvj-web.service")) as f:
            unit = f.read()
        self.assertNotIn("systemd-journal", unit)
        self.assertNotRegex(unit, r"SupplementaryGroups=.*\badm\b")


class FactoryResetTest(Base):
    def setUp(self):
        super().setUp()
        self.usb = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.usb, "STICK"))
        open(os.path.join(self.usb, "STICK", "show.mp4"), "w").close()
        self.api.usb_root = self.usb

    def reset(self, device=None, client=LAN, **body):
        return self.h("POST", "/api/system/factory-reset", body, device or self.full_dev, client)

    def test_every_refusal_changes_nothing(self):
        before = self.on_disk()
        media = sorted(os.listdir(self.media))
        ok = {"confirm": "factory-reset", "media": "delete"}
        self.assertEqual(self.reset()[0], 400)
        self.assertEqual(self.reset(media="delete")[0], 400)
        self.assertEqual(self.reset(confirm="yes", media="delete")[0], 400)
        self.assertEqual(self.reset(confirm=True, media="delete")[0], 400)
        self.assertEqual(self.reset(confirm="poweroff", media="delete")[0], 400)
        self.assertEqual(self.reset(confirm="factory-reset")[0], 400)                       # no word about the clips
        self.assertEqual(self.reset(confirm="factory-reset", media="all")[0], 400)
        self.assertEqual(self.reset(confirm="factory-reset", media=True)[0], 400)
        self.assertEqual(self.h("POST", "/api/system/factory-reset", ok, None)[0], 401)
        self.assertEqual(self.reset(self.view_dev, **ok)[0], 403)
        self.assertEqual(self.reset(self.live_dev, **ok)[0], 403)
        self.assertEqual(self.h("GET", "/api/system/factory-reset", {}, self.full_dev)[0], 405)
        self.assertEqual(self.call("POST", "/api/system/factory-reset", ok, token=self.full, csrf=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/system/factory-reset", ok, token="nobody")[0], 401)
        self.assertEqual(self.call("POST", "/api/system/factory-reset", raw=b"confirm=factory-reset&media=delete", token=self.full,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.assertEqual(self.on_disk(), before)
        self.assertEqual(sorted(os.listdir(self.media)), media)

    def test_refused_through_the_support_tunnel(self):
        dev, _ = self.remote_login()
        before = self.on_disk()
        st, out = self.reset(dev, TUNNEL, confirm="factory-reset", media="keep")
        self.assertEqual(st, 403)
        self.assertEqual(self.reset(self.full_dev, TUNNEL, confirm="factory-reset", media="keep")[0], 403)    # a studio token there
        with self.assertRaises(ApiError) as c:                  # and the function itself refuses
            self.care.factory_reset({"confirm": "factory-reset", "media": "keep"}, dev, TUNNEL)
        self.assertEqual(c.exception.status, 403)
        self.assertEqual(self.on_disk(), before)
        self.assertTrue(self.api.support.session)

    def test_refused_while_something_is_being_written(self):
        ok = {"confirm": "factory-reset", "media": "delete"}
        before = self.on_disk()
        result = os.path.join(tempfile.mkdtemp(), "result.json")
        with open(result, "w") as f:
            json.dump({"state": "running", "message": "installing", "at": int(time.time())}, f)
        with mock.patch.dict(os.environ, {"PVJ_UPDATE_RESULT": result}):
            st, out = self.reset(**ok)
            self.assertEqual((st, "update" in out["error"]), (409, True))
        self.api._import = {"active": True}
        st, out = self.reset(**ok)
        self.assertEqual((st, "USB" in out["error"]), (409, True))
        self.api._import = {}
        self.assertTrue(self.api._upload_lock.acquire(blocking=False))
        try:
            st, out = self.reset(**ok)
            self.assertEqual((st, "upload" in out["error"]), (409, True))
        finally:
            self.api._upload_lock.release()
        self.assertEqual(self.on_disk(), before)
        self.assertIn("a.mp4", os.listdir(self.media))

    def test_keeping_the_clips(self):
        old = copy.deepcopy(self.settings.data)
        old_pin = self.pin
        self.h("POST", "/api/access/code", {"role": "live"}, self.full_dev)
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.full_dev)[0], 200)
        self.assertEqual(self.h("POST", "/api/support/start", {"confirm": "start"}, self.full_dev)[0], 200)
        self.assertEqual(self.send(self.export())[0], 200)                        # leaves a before-import copy
        for name in ("settings.json.bak-v12", "settings.json.rolled-back-1790000000"):
            with open(os.path.join(self.tmp, name), "w") as f:
                json.dump(old, f)
        inbox = self.api._update_inbox()
        os.makedirs(inbox, exist_ok=True)
        open(os.path.join(inbox, "pvj-9.9.9.tar.gz"), "w").close()
        media = sorted(os.listdir(self.media))
        st, out = self.call("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, token=self.full)[:2]
        self.assertEqual(st, 200, out)
        self.assertEqual((out["reset"], out["media"], out["deleted"], out["problems"]), (True, "keep", 0, []))
        self.assertNotIn(self.auth.current_pin, json.dumps(out))          # the new PIN is on the display, not in the answer
        d = self.settings.data
        fresh = settings_mod.default_settings()
        for name in fresh:
            if name != "auth":
                self.assertEqual(d[name], fresh[name], name)
        self.assertEqual(sorted(d), sorted(fresh))
        # every way in is gone, and the PIN is a new one
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 401)
        self.assertEqual(self.auth.list_joins(), [])
        self.assertIsNone(self.api.support.session)
        self.assertEqual(d["devices"], [])
        self.assertNotEqual((d["auth"]["pin_hash"], d["auth"]["pin_salt"]), (old["auth"]["pin_hash"], old["auth"]["pin_salt"]))
        with open(os.path.join(self.rundir, "pin")) as f:
            self.assertEqual(f.read().strip(), self.auth.current_pin)
        if self.auth.current_pin != old_pin:
            self.assertEqual(self.call("POST", "/api/pair", {"pin": old_pin, "name": "x"})[0], 403)
        st, paired, _ = self.call("POST", "/api/pair", {"pin": self.auth.current_pin, "name": "new owner"})
        self.assertEqual(st, 200)
        # nothing beside the settings file still holds the old devices or passwords
        hashes = [x["token_hash"] for x in old["devices"]] + [PROJECTOR_PASSWORD, STREAM_PASSWORD]
        left = sorted(n for n in os.listdir(self.tmp) if n.startswith("settings.json"))
        self.assertEqual(left, ["settings.json", "settings.json.bak"])
        for name in left:
            with open(os.path.join(self.tmp, name)) as f:
                text = f.read()
            for h in hashes:
                self.assertNotIn(h, text, name)
        self.assertEqual(os.listdir(inbox), [])
        self.assertEqual(sorted(os.listdir(self.media)), media)                       # the clips stay

    def test_deleting_the_clips_touches_only_the_box_s_own_media_folder(self):
        os.mkdir(os.path.join(self.media, "folder"))
        open(os.path.join(self.media, "folder", "inside.mp4"), "w").close()
        open(os.path.join(self.media, ".upload-left"), "w").close()
        other = os.path.join(self.tmp, "addons")
        os.makedirs(other)
        open(os.path.join(other, "mine.json"), "w").close()
        st, out = self.reset(confirm="factory-reset", media="delete")
        self.assertEqual(st, 200, out)
        self.assertEqual(sorted(os.listdir(self.media)), ["folder", "notes.txt"])            # only clips and upload pieces go
        self.assertEqual(os.listdir(os.path.join(self.media, "folder")), ["inside.mp4"])     # folders are not walked
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "secret.mp4")))               # the link went, not what it pointed at
        self.assertEqual(os.listdir(os.path.join(self.usb, "STICK")), ["show.mp4"])         # never a USB drive
        self.assertEqual(os.listdir(other), ["mine.json"])
        self.assertGreaterEqual(out["deleted"], 5)
        self.assertIn(("clear",), self.player.calls)                                        # playback stopped first
        self.assertEqual(self.settings.data["devices"], [])

    def test_the_pin_screen_returns_even_when_a_clip_was_looping_behind_a_blackout(self):
        from pvj.pinscreen import PinScreen
        self.player.running = True                # a clip plays until the player is told to clear
        self.player.status = lambda: {"running": True, "path": None if ("clear",) in self.player.calls else "/media/a.mp4"}
        self.assertEqual(self.h("POST", "/api/blackout", {"on": True}, self.full_dev)[0], 200)
        screen = PinScreen(self.api, self.auth, log=lambda *_: None)
        self.assertFalse(screen.auto_wanted())
        self.assertEqual(self.reset(confirm="factory-reset", media="keep")[0], 200)
        self.assertTrue(screen.auto_wanted())
        self.assertIn(("clear",), self.player.calls)
        self.assertFalse(self.api.mix["blackout"])
        self.assertEqual(self.api.mix["opacity"], 100)
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"][-1], ("opacity", 255))
        self.assertIn("a.mp4", os.listdir(self.media))

    def test_clips_on_a_usb_drive_are_never_deleted(self):
        """PVJ_MEDIA_DIR may point at a USB drive (the read-only root needs media off the system disk)."""
        stick = os.path.join(self.usb, "STICK")
        link = os.path.join(self.tmp, "usb-link")
        os.symlink(stick, link)
        before = self.on_disk()
        for media_dir, usb_link in ((stick, "/nonexistent"), (os.path.join(stick, "clips"), "/nonexistent"), (self.usb, "/nonexistent"),
                                    (link, "/nonexistent"), (stick, link)):
            self.api.media_dir, self.api.usb_link = media_dir, usb_link
            st, out = self.reset(confirm="factory-reset", media="delete")
            self.assertEqual((st, "USB" in out.get("error", "")), (409, True), media_dir)
            self.assertEqual(os.listdir(stick), ["show.mp4"])
            self.assertEqual(self.on_disk(), before)
        self.api.usb_root = "/nonexistent"
        self.api.media_dir, self.api.usb_link = stick, link            # only the "newest drive" link points at it
        self.assertEqual(self.reset(confirm="factory-reset", media="delete")[0], 409)
        self.assertEqual(os.listdir(stick), ["show.mp4"])
        self.assertEqual(self.reset(confirm="factory-reset", media="keep")[0], 200)       # keeping them is fine
        self.assertEqual(os.listdir(stick), ["show.mp4"])

    def test_a_clip_that_cannot_be_deleted_is_reported(self):
        real = self.care._unlink
        self.care._unlink = lambda path: False if path.endswith("b.mov") else real(path)
        st, out = self.reset(confirm="factory-reset", media="delete")
        self.assertEqual(st, 200, out)
        self.assertEqual(out["problems"], ["could not delete b.mov"])
        self.assertIn("b.mov", os.listdir(self.media))


class RoutesTest(unittest.TestCase):
    def test_the_api_table_in_the_readme_lists_them(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "pvj", "README.md")) as f:
            text = f.read()
        for path in ("/api/system/settings/export", "/api/system/settings/import", "/api/system/diagnostics", "/api/system/factory-reset"):
            self.assertIn(path, text)


if __name__ == "__main__":
    unittest.main()
