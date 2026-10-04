# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Versioned settings store with automatic migration.

One JSON file holds pads, paired devices, module switches and the theme. It is
written atomically (a pulled plug never leaves half a file) with a backup of the
previous version kept alongside. When the schema changes, `load()` backs the file
up as settings.json.bak-v<old>, applies the migrations in order and saves.

A file from a NEWER version is never rewritten: the store refuses to load it, so
rolling back the program cannot silently destroy settings.
"""

import copy
import json
import os
import tempfile
import threading

SCHEMA = 13


class SettingsError(Exception):
    pass


def default_control():
    return {"dmx": {"enabled": False, "protocol": "artnet", "universe": 0, "start": 1, "allow": []},
            "midi": {"enabled": False, "builtin": True, "map": []}}


def default_settings():
    return {
        "schema": SCHEMA,
        "auth": {"pin_hash": None, "pin_salt": None},
        "devices": [],
        "pads": {"banks": [{"name": "Bank %s" % c, "pads": [{"label": "", "file": ""} for _ in range(12)]}
                           for c in "ABC"]},
        "modules": {"enabled": {}},
        "theme": {"name": "dark-stage", "accent": None},
        "mix": {"transition": "dip", "duration": 1.0},
        "osc": {"enabled": False, "port": 9876, "allow": []},
        "schedule": {"enabled": False, "entries": []},
        "streams": [],
        "control": default_control(),
        "autostart": {"mode": "off", "file": "", "preset": "", "loop": True, "delay": 0},
        "audio": {"device": "auto"},
        "overlay": {"file": "", "on": False},
        "projectors": [],
        "mapper": {"on": False, "screen": None, "surfaces": [], "sets": {}},
        "support": {"allowed": False, "endpoint": "", "server_key": "", "address": "", "network": "", "max_minutes": 240},
        "support_log": [],
        "sync": {"role": "off", "group": "main", "port": 5577, "wall": {"cols": 1, "rows": 1, "col": 0, "row": 0, "bezel": 0.0}},
        "room": {"groups": [], "scenes": []},      # read with defaults everywhere: an older file has no such key (no schema change)
    }


# version -> function that upgrades a settings dict FROM that version to the next.
def _v1_to_v2(data):
    """2: OSC settings. Off by default; only private networks may send, plus any extra ranges listed."""
    data.setdefault("osc", {"enabled": False, "port": 9876, "allow": []})


def _v2_to_v3(data):
    """3: weekly schedule. Off, and empty."""
    data.setdefault("schedule", {"enabled": False, "entries": []})


def _v3_to_v4(data):
    """4: saved network streams (SRT, RTSP, RTMP). None yet."""
    data.setdefault("streams", [])


def _v4_to_v5(data):
    """5: DMX (Art-Net, sACN) and MIDI input. Both off."""
    data.setdefault("control", default_control())


def _v5_to_v6(data):
    """6: autostart (what plays by itself at power-up). Off."""
    data.setdefault("autostart", {"mode": "off", "file": "", "preset": "", "loop": True, "delay": 0})


def _v6_to_v7(data):
    """7: audio output. "auto" means HDMI on the connected port (else mpv's own choice)."""
    data.setdefault("audio", {"device": "auto"})


def _v7_to_v8(data):
    """8: MIDI reads every controller and has a learnable map. The single chosen device and channel go; whether MIDI
    was switched on is kept; the built-in map stays on so nothing changes until the user maps a control."""
    control = data.setdefault("control", default_control())
    old = control.get("midi") or {}
    control["midi"] = {"enabled": bool(old.get("enabled", False)), "builtin": True, "map": []}


def _v8_to_v9(data):
    """9: a picture over the video (logo or mask). Off."""
    data.setdefault("overlay", {"file": "", "on": False})


def _v9_to_v10(data):
    """10: projectors (PJLink). None yet."""
    data.setdefault("projectors", [])


def _v10_to_v11(data):
    """11: projection mapping. Off, no surfaces."""
    data.setdefault("mapper", {"on": False, "screen": None, "surfaces": [], "sets": {}})


def _v11_to_v12(data):
    """12: remote support. Not allowed until someone at the studio allows it; no server set."""
    data.setdefault("support", {"allowed": False, "endpoint": "", "server_key": "", "address": "", "network": "", "max_minutes": 240})
    data.setdefault("support_log", [])


def _v12_to_v13(data):
    """13: multi-box sync and the video wall. Off; the whole picture."""
    data.setdefault("sync", {"role": "off", "group": "main", "port": 5577,
                             "wall": {"cols": 1, "rows": 1, "col": 0, "row": 0, "bezel": 0.0}})


MIGRATIONS = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5, 5: _v5_to_v6, 6: _v6_to_v7, 7: _v7_to_v8, 8: _v8_to_v9,
              9: _v9_to_v10, 10: _v10_to_v11, 11: _v11_to_v12, 12: _v12_to_v13}


def migrate(data, migrations=None, current=SCHEMA):
    """Upgrade `data` in place to `current`. Returns the list of versions applied."""
    migrations = MIGRATIONS if migrations is None else migrations
    version = data.get("schema", 0)
    if not isinstance(version, int) or version < 0:
        raise SettingsError("bad schema value %r" % (version,))
    if version > current:
        raise SettingsError("settings are from a newer version (schema %d, this program knows %d); "
                            "not touching them" % (version, current))
    applied = []
    while version < current:
        step = migrations.get(version)
        if step is None:
            raise SettingsError("no migration from schema %d" % version)
        step(data)
        applied.append(version)
        version += 1
        data["schema"] = version
    return applied


class Settings:
    def __init__(self, path, migrations=None, current=SCHEMA):
        self.path = path
        self._migrations = migrations
        self._current = current
        self.data = None
        # Web threads mutate and save concurrently. Holding this around a mutation AND its
        # save keeps the snapshot and the write in order, so an older snapshot can never
        # land on disk after a newer one.
        self.lock = threading.RLock()

    def load(self):
        if not os.path.exists(self.path) and not os.path.exists(self.path + ".bak"):
            self.data = default_settings()
            self.data["schema"] = self._current
            self.save()
            return self.data
        try:
            data = self._read(self.path)
        except SettingsError:
            # A cut power supply can corrupt the main file; the backup is written first.
            if not os.path.exists(self.path + ".bak"):
                raise
            data = self._read(self.path + ".bak")
            self.recovered_from_backup = True
        old = data.get("schema", 0)
        if old != self._current or getattr(self, "recovered_from_backup", False):
            original = copy.deepcopy(data)
            migrate(data, self._migrations, self._current)  # raises for newer files
            if old != self._current:
                self._write("%s.bak-v%s" % (self.path, old), original)
            self.data = data
            self.save()
        else:
            self.data = data
        return self.data

    @staticmethod
    def _read(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise SettingsError("cannot read %s: %s" % (path, e))
        if not isinstance(data, dict):
            raise SettingsError("%s does not contain a settings object" % path)
        return data

    def save(self):
        if self.data is None:
            raise SettingsError("nothing loaded")
        with self.lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path) as f:
                        previous = f.read()
                    json.loads(previous)  # never overwrite a good backup with a corrupt file
                    self._write_text(self.path + ".bak", previous)
                except (OSError, ValueError):
                    pass
            self._write(self.path, self.data)

    def _write(self, path, data):
        self._write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _write_text(path, text):
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
