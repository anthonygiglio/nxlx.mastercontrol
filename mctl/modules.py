# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Module manifests and the enable/disable registry.

Each optional feature is a module described by a small JSON manifest, so features
arrive, update and switch off independently. Core modules are locked on. A module
that is not built yet is marked "planned" and cannot be switched on; the panel
shows it as coming, never as broken.
"""

import glob
import json
import os
import re

TYPES = ("core", "optional", "legacy")
CHANNELS = ("stable", "beta", "nightly")
STATUSES = ("ready", "planned")
BOARDS = ("pi3", "pi4", "pi5", "x86")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$")
DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "modules.d")


class ModuleError(Exception):
    pass


def validate_manifest(m):
    """Return a list of problems (empty if the manifest is valid)."""
    problems = []
    if not isinstance(m, dict):
        return ["manifest is not an object"]
    if not isinstance(m.get("id"), str) or not _ID.match(m["id"]):
        problems.append("bad id")
    if not isinstance(m.get("name"), str) or not m.get("name"):
        problems.append("missing name")
    if not isinstance(m.get("version"), str) or not _VERSION.match(m["version"]):
        problems.append("bad version")
    if m.get("type") not in TYPES:
        problems.append("type must be one of %s" % ", ".join(TYPES))
    if m.get("channel") not in CHANNELS:
        problems.append("channel must be one of %s" % ", ".join(CHANNELS))
    if m.get("status") not in STATUSES:
        problems.append("status must be one of %s" % ", ".join(STATUSES))
    for key in ("requires", "boards"):
        if not isinstance(m.get(key), list) or not all(isinstance(x, str) for x in m.get(key, [])):
            problems.append("%s must be a list of strings" % key)
    if isinstance(m.get("boards"), list) and (not m["boards"] or any(b not in BOARDS for b in m["boards"])):
        problems.append("boards must be a non-empty list from %s" % ", ".join(BOARDS))
    return problems


def load_manifests(directory=DEFAULT_DIR):
    manifests = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path) as f:
                m = json.load(f)
        except (OSError, ValueError) as e:
            raise ModuleError("%s: %s" % (os.path.basename(path), e))
        problems = validate_manifest(m)
        if problems:
            raise ModuleError("%s: %s" % (os.path.basename(path), "; ".join(problems)))
        if m["id"] in manifests:
            raise ModuleError("duplicate module id %s" % m["id"])
        manifests[m["id"]] = m
    for m in manifests.values():
        for dep in m["requires"]:
            if dep not in manifests:
                raise ModuleError("%s requires unknown module %s" % (m["id"], dep))
    return manifests


class Registry:
    def __init__(self, settings, board_kind, manifests=None):
        self.settings = settings
        self.board = board_kind
        self.manifests = manifests if manifests is not None else load_manifests()

    def _state(self, m):
        if m["type"] == "core":
            return True
        enabled = self.settings.data["modules"]["enabled"]
        if m["id"] in enabled:
            return bool(enabled[m["id"]])
        return bool(m.get("default_enabled")) and m["status"] == "ready" and self._supported(m)

    def _supported(self, m):
        return self.board in m["boards"] or self.board == "arm-other" and "pi4" in m["boards"]

    def list(self):
        out = []
        order = {"core": 0, "optional": 1, "legacy": 2}
        for m in sorted(self.manifests.values(), key=lambda m: (order.get(m["type"], 9), m["name"].lower())):
            out.append({"id": m["id"], "name": m["name"], "version": m["version"], "type": m["type"],
                        "channel": m["channel"], "status": m["status"], "description": m.get("description", ""),
                        "supported": self._supported(m), "locked": m["type"] == "core",
                        "enabled": self._state(m), "requires": list(m["requires"])})
        return out

    def enabled(self, module_id):
        m = self.manifests.get(module_id)
        return bool(m) and self._state(m)

    def set_enabled(self, module_id, value):
        m = self.manifests.get(module_id)
        if m is None:
            raise ModuleError("unknown module %s" % module_id)
        if not isinstance(value, bool):
            raise ModuleError("enabled must be true or false")
        if m["type"] == "core":
            raise ModuleError("%s is a core module and cannot be switched off" % m["name"])
        if value:
            if m["status"] != "ready":
                raise ModuleError("%s is not built yet" % m["name"])
            if not self._supported(m):
                raise ModuleError("%s does not run on this board (%s)" % (m["name"], self.board))
            for dep in m["requires"]:
                if not self._state(self.manifests[dep]):
                    raise ModuleError("%s needs %s switched on first" % (m["name"], self.manifests[dep]["name"]))
        else:
            for other in self.manifests.values():
                if module_id in other["requires"] and self._state(other):
                    raise ModuleError("%s is needed by %s; switch that off first" % (m["name"], other["name"]))
        self.settings.data["modules"]["enabled"][module_id] = value
        self.settings.save()
