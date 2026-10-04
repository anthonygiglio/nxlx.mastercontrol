# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Looking after the box: settings export and import, a diagnostics file, and factory reset.

All four are for full-access devices, and all four are done by the unprivileged panel itself (no root helper): the
settings file and the media folder already belong to it.

What happens to secrets (see also docs/MANUAL.md):

* The PIN, paired devices and their tokens, remote-support settings (server address, keys) and the support history
  are NEVER exported and NEVER imported. They belong to one box: an import leaves the box's own as they are, and a
  file that carries them is refused. Join codes and support codes live in memory only and are in no file.
* Projector passwords and the secrets in a stream address are left out of an export unless the person ticks
  "include passwords"; that cannot be asked for through the remote-support tunnel. What is secret in an address is
  decided in one place (streams._pieces): a user name and password; for RTMP the stream key (the last part of the
  path) and the whole query; for SRT the passphrase, the streamid and every query name not on a short list of
  harmless ones; for RTSP query names that sound like a secret. Importing a file without them keeps the password
  the box already has for the same projector (same id, address and port), and the box's own full address of a stream
  with the same id when the file's address is that address without its secrets: a shortened address never replaces
  a working one.
* The diagnostics file never contains any of these, with or without a tick: its settings are built from a list of
  what may be shown, a net over key names catches what a later version might add, and every piece of text in it
  (log lines too) is scrubbed of the secrets the box knows and of anything that looks like a login in an address.

An import goes the way a normal load does: the same migrations (a file from a newer version is refused, an older one
is brought up to date), then every section through the same checks the panel's own forms use. The checks here know
the settings as of schema KNOWN_SCHEMA; a section a later version adds is not exported and not imported (the box keeps
its own) until its check is added to SECTIONS.

Import and factory reset are refused through the remote-support tunnel: an import can switch on OSC, DMX or MIDI
(new ways to control the box) and a reset removes every paired device.
"""

import contextlib
import copy
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from urllib.parse import unquote

from . import dmx as dmx_mod, midi as midi_mod, osc as osc_mod, projector as projector_mod, streams as streams_mod
from . import mapper as mapper_mod, scheduler as scheduler_mod, sync as sync_mod, themes as themes_mod
from . import autostart as autostart_mod, room as room_mod, shaders as shaders_mod
from .api import ApiError, MEDIA_EXTENSIONS, valid_name
from .settings import SettingsError, default_control, default_settings, migrate

FORMAT = "nxlx.mastercontrol settings"
FORMAT_VERSION = 1
KNOWN_SCHEMA = 13                    # the section checks below know the settings as of this schema
MAX_IMPORT = 1024 * 1024             # bytes: a full mapper with every saved mapping is well under this
MAX_NUMBER_DIGITS = 40
MAX_DEPTH = 24
NEVER = ("auth", "devices", "support", "support_log")      # never exported, never imported
ENVELOPE = ("format", "format_version", "exported", "version", "box", "passwords_included", "settings")
KEEP_IMPORT_BACKUPS = 3
LOG_UNITS = ("pvj-web.service", "pvj-player.service", "pvj-sysd.service", "pvj-netd.service", "pvj-supportd.service")
LOG_LINES, LOG_LINE_MAX = 300, 400
CONFIRM_IMPORT, CONFIRM_RESET = "import", "factory-reset"
SECRET_WORDS = "pass|pwd|psk|secret|token|key|auth|sign"       # a name holding one of these is followed by a secret
_ID = re.compile(r"[0-9a-f]{8}")
_SECRET_KEY = re.compile(r"pass|secret|token|salt|hash|credential|(^|_)(pin|key|code|login)(_|$)", re.I)
_LOG_PIN = re.compile(r"(?i)\b(pin|code)\b([ :=]+)(?=[A-Za-z-]*[0-9])[A-Za-z0-9-]{4,}")
_LOG_URL_LOGIN = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)
_LOG_BEARER = re.compile(r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?)(?:[a-z]+\s+)?[^\s\"',;]+|\b(bearer\s+)[^\s\"',;]+")
_LOG_PAIR = re.compile(r"(?i)\b([a-z0-9_.-]*(?:%s)[a-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?)[^&\s\"',;]+" % SECRET_WORDS)
_LOG_ENCODED = re.compile(r"([A-Za-z0-9_.-]*%[0-9A-Fa-f]{2}[A-Za-z0-9_.%-]*=)([^&\s\"',;]+)")      # pass%70hrase=...
_SECRET_WORD = re.compile(SECRET_WORDS, re.I)


def bad(message, status=400):
    return ApiError(status, message)


# ---- strict JSON -------------------------------------------------------------------------------------------------
def parse_strict(raw):
    """A settings file from untrusted bytes: UTF-8 only, one JSON object, no repeated keys at any level, no NaN or
    Infinity (spelled out or as a number too large to hold), no absurd numbers or nesting. Raises ApiError."""
    if not isinstance(raw, (bytes, bytearray)):
        raise bad("send the file as the request body")
    if len(raw) > MAX_IMPORT:
        raise bad("the file is too large for a settings file", 413)
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise bad("the file is not UTF-8 text")

    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("the key %r appears twice" % k)
            out[k] = v
        return out

    def constant(name):
        raise ValueError("%s is not a number a settings file may hold" % name)

    def real(text):
        if len(text) > MAX_NUMBER_DIGITS:
            raise ValueError("a number is too long")
        v = float(text)
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("a number is too large")
        return v

    def whole(text):
        if len(text) > MAX_NUMBER_DIGITS:
            raise ValueError("a number is too long")
        return int(text)
    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=real, parse_int=whole)
    except RecursionError:
        raise bad("the file is nested too deeply")
    except ValueError as e:
        raise bad("the file is not valid JSON: %s" % e)
    if not isinstance(data, dict):
        raise bad("the file does not hold a settings export")
    if _depth(data) > MAX_DEPTH:
        raise bad("the file is nested too deeply")
    return data


def _depth(value):
    """How deeply `value` is nested, without recursion (the parser may hand back something very deep)."""
    deepest, todo = 0, [(value, 1)]
    while todo:
        v, level = todo.pop()
        deepest = max(deepest, level)
        if deepest > MAX_DEPTH:
            break
        if isinstance(v, dict):
            todo.extend((x, level + 1) for x in v.values())
        elif isinstance(v, list):
            todo.extend((x, level + 1) for x in v)
    return deepest


# ---- stream addresses (what is secret in one is decided in streams.py, for the panel and for the files alike) ------
strip_login, stream_secrets, stream_where = streams_mod.strip_login, streams_mod.stream_secrets, streams_mod.stream_where


# ---- the section checks (each: the section from the file -> a clean section; raises on anything wrong) -----------
def _text(v, limit, what):
    if not isinstance(v, str) or len(v) > limit or re.search(r"[\x00-\x1f\x7f]", v):
        raise ValueError("%s must be text of up to %d characters" % (what, limit))
    return v


def _flag(v, what):
    if not isinstance(v, bool):
        raise ValueError("%s must be true or false" % what)
    return v


def _obj(v, what="it"):
    if not isinstance(v, dict):
        raise ValueError("%s must be an object" % what)
    return v


def _media_name(v, what, extensions=MEDIA_EXTENSIONS):
    if v == "":
        return v
    if not valid_name(v) or not v.lower().endswith(extensions):
        raise ValueError("%s is not a media file name" % what)
    return v


def check_pads(v, care):
    banks = _obj(v).get("banks")
    if not isinstance(banks, list) or len(banks) != 3:
        raise ValueError("there must be 3 banks")
    out = []
    for bank in banks:
        pads = _obj(bank, "a bank").get("pads")
        if not isinstance(pads, list) or len(pads) != 12:
            raise ValueError("a bank has 12 pads")
        clean = []
        for pad in pads:
            _obj(pad, "a pad")
            item = {"label": _text(pad.get("label", ""), 40, "a pad label"), "file": _media_name(pad.get("file", ""), "a pad's clip")}
            if "ending" in pad:
                if pad["ending"] not in ("loop", "stop", "hold"):
                    raise ValueError("a pad ends with loop, stop or hold")
                item["ending"] = pad["ending"]
            clean.append(item)
        name = _text(bank.get("name", ""), 40, "a bank name")
        if not name:
            raise ValueError("a bank needs a name")
        out.append({"name": name, "pads": clean})
    return {"banks": out}


def check_modules(v, care):
    """Only switches for modules this box knows; one that is not built or does not run on this board is left off
    (a stored "on" would otherwise count, whatever the board), and so is one whose requirement is off."""
    enabled = _obj(_obj(v).get("enabled", {}), "enabled")
    registry = care.api.registry
    out = {}
    for mid, on in enabled.items():
        _flag(on, "a module switch")
        m = registry.manifests.get(mid)
        if m is None:
            care.note("module %s is not known to this version; left out" % _printable(mid))
        elif m["type"] != "core":
            out[mid] = on
    for mid in sorted(k for k, on in out.items() if on):
        m = registry.manifests[mid]
        if m["status"] != "ready" or not registry._supported(m):
            out[mid] = False
            care.note("module %s does not run on this box; left off" % m["name"])
    changed = True
    while changed:                       # a requirement that is off switches off what needs it, however deep
        changed = False
        for mid in sorted(k for k, on in out.items() if on):
            for dep in registry.manifests[mid]["requires"]:
                d = registry.manifests.get(dep)
                dep_on = bool(d) and (d["type"] == "core" or out.get(dep, bool(d.get("default_enabled")) and d["status"] == "ready"
                                                                    and registry._supported(d)))
                if not dep_on:
                    out[mid], changed = False, True
                    care.note("module %s needs %s, which is off; left off" % (registry.manifests[mid]["name"], dep))
                    break
    return {"enabled": out}


def check_theme(v, care):
    name, accent = _obj(v).get("name"), v.get("accent")
    if name not in care.api.themes:
        care.note("the theme %s is not on this box; the theme was left as it is" % _printable(name))
        return copy.deepcopy(care.api.settings.data["theme"])
    try:
        themes_mod.css(care.api.themes[name], accent)
    except themes_mod.ThemeError as e:
        raise ValueError(str(e))
    return {"name": name, "accent": accent}


def check_mix(v, care):
    mode, duration = _obj(v).get("transition"), v.get("duration")
    if mode not in ("cut", "dip"):
        raise ValueError("transition must be cut or dip")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.1 <= duration <= 10:
        raise ValueError("duration must be 0.1 to 10 seconds")
    return {"transition": mode, "duration": float(duration)}


def check_osc(v, care):
    port = _obj(v).get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("port must be 1024 to 65535")
    return {"enabled": _flag(v.get("enabled"), "enabled"), "port": port, "allow": osc_mod.validate_allow(v.get("allow", []))}


def check_schedule(v, care):
    return scheduler_mod.validate(v)


def check_streams(v, care):
    streams_mod.validate_saved(v)
    return [{"id": s["id"], "name": streams_mod.clean_name(s["name"]), "url": s["url"]} for s in v]


def check_control(v, care):
    _obj(v)
    blank = default_control()
    dmx = dmx_mod.validate(_obj(v.get("dmx", {}), "dmx"), blank["dmx"])
    midi_in = _obj(v.get("midi", {}), "midi")
    midi = midi_mod.validate(midi_in, blank["midi"])
    entries = midi_in.get("map", [])
    if not isinstance(entries, list) or len(entries) > midi_mod.MAX_MAP:
        raise ValueError("at most %d MIDI mappings" % midi_mod.MAX_MAP)
    midi["map"] = [midi_mod.validate_entry(e, keep_id=True) for e in entries]
    if len({e["id"] for e in midi["map"]}) != len(midi["map"]):
        raise ValueError("two MIDI mappings have the same id")
    if len({(e["kind"], e["number"], e["source"], e["channel"]) for e in midi["map"]}) != len(midi["map"]):
        raise ValueError("two MIDI mappings are for the same control")        # the panel replaces; a file may not hold both
    return {"dmx": dmx, "midi": midi}


def check_autostart(v, care):
    return autostart_mod.validate(_obj(v), None)


def check_audio(v, care):
    device = _text(_obj(v).get("device"), 200, "the sound output")
    return {"device": device or "auto"}             # an output this box does not have plays as Automatic


def check_overlay(v, care):
    name = _obj(v).get("file", "")
    if not isinstance(name, str):
        raise ValueError("file must be text")
    out = {"file": _media_name(name, "the picture", (".png",)), "on": _flag(v.get("on", False), "on")}
    if out["on"] and not out["file"]:
        raise ValueError("a picture that is on needs a file")
    return out


def _projector_details(v):
    """What a projector said it is, as projector.py stores it: text cleaned the way an answer from the projector is,
    the input list only codes the standard knows. A key this version does not know is dropped."""
    _obj(v, "a projector's details")
    out = {}
    for key, limit in (("name", 64), ("maker", 32), ("model", 32), ("info", 32)):
        if key in v:
            if v[key] is not None and not isinstance(v[key], str):
                raise ValueError("a projector's %s must be text" % key)
            out[key] = None if v[key] is None else projector_mod.clean_text(v[key], limit)
    if "class" in v:
        if v["class"] is not None and not (isinstance(v["class"], str) and re.fullmatch(r"[1-9]", v["class"])):
            raise ValueError("a projector's class is one digit")
        out["class"] = v["class"]
    if "inputs" in v:
        codes = v["inputs"]
        if codes is not None and not (isinstance(codes, list) and len(set(map(str, codes))) == len(codes)
                                      and all(isinstance(c, str) and projector_mod._INPUT.fullmatch(c) for c in codes)):
            raise ValueError("a projector's inputs are codes such as 31, each once")
        out["inputs"] = None if codes is None else list(codes)
    if "read" in v:
        if type(v["read"]) is not int or v["read"] < 0:
            raise ValueError("when a projector's details were read must be a time")
        out["read"] = v["read"]
    return out


def check_projectors(v, care):
    if not isinstance(v, list) or len(v) > projector_mod.MAX_PROJECTORS:
        raise ValueError("at most %d projectors" % projector_mod.MAX_PROJECTORS)
    out, seen = [], set()
    for entry in v:
        clean = projector_mod.validate(entry)
        given = entry.get("id")                    # validate() makes a new id; the file's own is kept if it is one
        if isinstance(given, str) and _ID.fullmatch(given) and given not in seen:
            clean["id"] = given
        seen.add(clean["id"])
        if "details" in entry:
            clean["details"] = _projector_details(entry["details"])
        if "labels" in entry:                      # a label is for one of the inputs the projector itself listed
            known = (clean.get("details") or {}).get("inputs") or []
            labels = {code: projector_mod.validate_label(known, code, label) for code, label in _obj(entry["labels"], "labels").items()}
            clean["labels"] = {code: label for code, label in labels.items() if label}
        try:                                       # an address is judged now; a name is looked up (and judged) at each use
            ipaddress.ip_address(clean["host"])
        except ValueError:
            pass
        else:
            projector_mod.private_address(clean["host"])
        out.append(clean)
    return out


def _screen(v):
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 16384 for n in v)):
        raise ValueError("a screen is [width, height]")
    return list(v)


def check_mapper(v, care):
    _obj(v)
    sets_in = _obj(v.get("sets", {}), "sets")
    if len(sets_in) > mapper_mod.MAX_SETS:
        raise ValueError("at most %d saved mappings" % mapper_mod.MAX_SETS)
    sets = {}
    for name, one in sets_in.items():
        if not mapper_mod.NAME.fullmatch(name) or name != name.strip():
            raise ValueError("a mapping name is 1 to 40 letters, digits, spaces or . _ -")
        _obj(one, "a saved mapping")
        sets[name] = {"screen": _screen(one.get("screen")), "surfaces": mapper_mod.validate_mapping({"surfaces": one.get("surfaces", [])})}
    surfaces = mapper_mod.validate_mapping({"surfaces": v.get("surfaces", [])})
    screen = v.get("screen")
    return {"on": _flag(v.get("on", False), "on"), "screen": None if screen is None else _screen(screen),
            "surfaces": surfaces, "sets": sets}


def check_sync(v, care):
    return sync_mod.validate(_obj(v), sync_mod.blank())


def check_shaders(v, care):
    """The Shaders and Vibes settings, by the rules its own API applies (shaders.py): how long a shader stays, whether
    it varies, the height it is drawn at, and the shaders left out of the rotation (names only; the uploaded shader
    files themselves are not part of a settings file). The section is in the settings only once something was changed."""
    _obj(v)
    out = shaders_mod.default_config()
    if "dwell" in v:
        d = v["dwell"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d != d or not shaders_mod.DWELL_MIN <= d <= shaders_mod.DWELL_MAX:
            raise ValueError("each shader stays %d to %d seconds" % (shaders_mod.DWELL_MIN, shaders_mod.DWELL_MAX))
        out["dwell"] = int(d)
    if "vary" in v:
        out["vary"] = _flag(v["vary"], "vary")
    if "height" in v:
        if type(v["height"]) is not int or v["height"] not in shaders_mod.HEIGHTS:
            raise ValueError("height must be one of %s" % ", ".join(str(h) for h in shaders_mod.HEIGHTS))
        out["height"] = v["height"]
    if "disabled" in v:
        names = v["disabled"]
        if (not isinstance(names, list) or len(names) > shaders_mod.MAX_UPLOADS + 64 or len(set(map(str, names))) != len(names)
                or not all(isinstance(n, str) and shaders_mod.FILE.fullmatch(n) for n in names)):
            raise ValueError("disabled must be a list of shader file names, each once")
        out["disabled"] = list(names)
    return out


def check_room(v, care):
    """Groups of projectors and scenes, by the Room module's own rules (room.py). Only the form is checked: a scene or
    group may name a projector, clip or stream that is not on this box, and it is then skipped when the scene runs."""
    return room_mod.validate(_obj(v))


# Every section that is exported and imported, in the order they are checked.
SECTIONS = (("pads", check_pads), ("modules", check_modules), ("theme", check_theme), ("mix", check_mix), ("osc", check_osc),
            ("schedule", check_schedule), ("streams", check_streams), ("control", check_control),
            ("autostart", check_autostart), ("audio", check_audio), ("overlay", check_overlay),
            ("projectors", check_projectors), ("mapper", check_mapper), ("sync", check_sync), ("shaders", check_shaders),
            ("room", check_room))
CHECK_ERRORS = (ValueError, KeyError, TypeError, AttributeError, osc_mod.OscError, streams_mod.StreamError,
                scheduler_mod.ScheduleError, dmx_mod.DmxError, midi_mod.MidiError, autostart_mod.AutostartError,
                projector_mod.ProjectorError, themes_mod.ThemeError, room_mod.RoomError)


def _printable(v):
    return re.sub(r"[^A-Za-z0-9 ._-]", "?", str(v))[:40]


# ---- diagnostics: what may be shown of the settings --------------------------------------------------------------
def _net(value):
    """A copy with every value under a key that sounds like a secret removed (for what a later version may add)."""
    if isinstance(value, dict):
        return {k: ("(removed)" if _SECRET_KEY.search(str(k)) else _net(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_net(v) for v in value]
    return value


def public_settings(data, support_configured=None):
    """The settings with every secret removed, for the diagnostics file. Built from what may be shown, not by
    taking things out: a section this version does not know is named, not copied."""
    out = {"schema": data.get("schema")}
    for name, _check in SECTIONS:
        if name in ("streams", "projectors") or name not in data:
            continue
        out[name] = _net(copy.deepcopy(data[name]))
    out["streams"] = [{"id": s.get("id"), "name": s.get("name"), "from": stream_where(s.get("url")),
                       "has_login": strip_login(s.get("url")) != s.get("url")} for s in data.get("streams", [])]
    out["projectors"] = [{"id": p.get("id"), "name": p.get("name"), "host": p.get("host"), "port": p.get("port"),
                          "has_password": bool(p.get("password"))} for p in data.get("projectors", [])]
    out["devices"] = [{"name": d.get("name"), "role": d.get("role"), "created": d.get("created")} for d in data.get("devices", [])]
    support = data.get("support") or {}
    out["support"] = {"allowed": bool(support.get("allowed")), "max_minutes": support.get("max_minutes"),
                      "server_set": bool(support.get("endpoint")), "configured": support_configured}
    out["support_log"] = [{k: e.get(k) for k in ("started", "ended", "minutes", "role", "reason", "logins")}
                          for e in data.get("support_log", []) if isinstance(e, dict)]
    known = {n for n, _ in SECTIONS} | set(NEVER) | {"schema"}
    out["not_shown"] = sorted(k for k in data if k not in known) + ["auth (the PIN)"]
    return out


def scrub(value, secrets):
    """Every piece of text in `value` without the given secrets, without a login in an address, without what follows
    a name that sounds like a secret ("passphrase=", "psk=", "password:", "Authorization: Bearer", also when the name
    is percent-encoded), and without what follows "PIN" or "code" (the panel writes its pairing PIN to the log at
    every start)."""
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, "(removed)")
        value = _LOG_URL_LOGIN.sub(r"\1(removed)@", value)
        value = _LOG_BEARER.sub(lambda m: (m.group(1) or m.group(2)) + "(removed)", value)
        value = _LOG_PAIR.sub(r"\1(removed)", value)
        value = _LOG_ENCODED.sub(lambda m: m.group(1) + "(removed)" if _SECRET_WORD.search(unquote(m.group(1))) else m.group(0), value)
        return _LOG_PIN.sub(r"\1\2(removed)", value)
    if isinstance(value, dict):
        return {scrub(k, secrets) if isinstance(k, str) else k: scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    return value


class BoxCare:
    def __init__(self, api, runner=subprocess.run, now=time.time, hostname=socket.gethostname):
        self.api = api
        self._run, self._now, self._hostname = runner, now, hostname
        self._notes = []

    def note(self, text):
        self._notes.append(text)

    @property
    def settings(self):
        return self.api.settings

    def routes(self):
        return {
            ("POST", "/api/system/settings/export"): ("full", self.export),
            ("POST", "/api/system/settings/import"): ("full", self._import_route),
            ("GET", "/api/system/diagnostics"): ("full", self.diagnostics),
            ("POST", "/api/system/factory-reset"): ("full", self.factory_reset),
        }

    def _local(self, client, what):
        """Refuse through the remote-support tunnel (the route table refuses it too; this holds even if that changes)."""
        if self.api.support.is_remote(client):
            raise ApiError(403, "%s cannot be done through remote support; ask someone at the studio" % what)

    @contextlib.contextmanager
    def _busy(self, what, usb_copy=False):
        """One import or reset at a time, and none beside an update (or, for a reset, a copy from USB). The flag is
        set under the lock a copy and an update start under, and the update is looked at only after it is set: so
        neither can begin between the check and the work. Raises 409 instead of waiting."""
        api = self.api
        with api._import_lock:
            if api._care_busy:
                raise ApiError(409, "%s is running; try again when it has finished" % api._care_busy)
            if usb_copy and api._import.get("active"):
                raise ApiError(409, "a copy from USB is running; reset when it has finished")
            api._care_busy = what
        try:
            if api._update_running():
                raise ApiError(409, "an update is running; try again when it has finished")
            yield
        finally:
            with api._import_lock:
                api._care_busy = None

    def _name(self, kind):
        host = re.sub(r"[^A-Za-z0-9-]", "", self._hostname() or "")[:40] or "box"
        return "nxlx-%s-%s-%s.json" % (kind, host, time.strftime("%Y%m%d-%H%M%S", time.gmtime(self._now())))

    # ---- export ------------------------------------------------------------------------------------------------
    def export(self, body, device, client):
        """{"passwords": false}: the settings as one file. With "passwords": true the projector passwords and the full
        stream addresses (logins, stream keys, passphrases) are in it, never through the support tunnel. The PIN,
        devices and remote support never are."""
        passwords = body.get("passwords", False)
        if not isinstance(passwords, bool):
            raise bad("passwords must be true or false")
        if passwords:
            self._local(client, "an export with passwords")
        with self.settings.lock:
            data = copy.deepcopy(self.settings.data)
        out = {"schema": data["schema"]}
        for name, _check in SECTIONS:
            if name in data:
                out[name] = data[name]
        out["projectors"] = [dict({"id": p["id"], "name": p["name"], "host": p["host"], "port": p["port"],
                                   "password": p.get("password", "") if passwords else ""},
                                  **{k: p[k] for k in ("details", "labels") if k in p})       # what it said it is; input labels
                             for p in out.get("projectors", [])]
        out["streams"] = [{"id": s["id"], "name": s["name"], "url": s["url"] if passwords else strip_login(s["url"])}
                          for s in out.get("streams", [])]
        from . import __version__
        return {"name": self._name("settings"),
                "file": {"format": FORMAT, "format_version": FORMAT_VERSION, "exported": int(self._now()), "version": __version__,
                         "box": self._hostname(), "passwords_included": passwords, "settings": out}}

    # ---- import ------------------------------------------------------------------------------------------------
    def _import_route(self, body, device, client):
        # server.py reads this path's body itself (as bytes, up to MAX_IMPORT) and calls import_settings
        raise bad("send the exported file as the request body")

    def check_file(self, raw):
        """(clean sections, notes, passwords_included) from the bytes of an export. Changes nothing. Raises ApiError."""
        envelope = parse_strict(raw)
        if envelope.get("format") != FORMAT:
            raise bad("this is not a settings export of nxlx.mastercontrol")
        if type(envelope.get("format_version")) is not int or envelope["format_version"] != FORMAT_VERSION:      # true == 1
            raise bad("this export has a format this version does not know; update the box first", 409)
        extra = sorted(k for k in envelope if k not in ENVELOPE)
        if extra:
            raise bad("the file has parts that do not belong in a settings export: %s" % ", ".join(_printable(k) for k in extra))
        passwords = envelope.get("passwords_included", False)
        if not isinstance(passwords, bool):
            raise bad("passwords_included must be true or false")
        data = envelope.get("settings")
        if not isinstance(data, dict):
            raise bad("the file holds no settings")
        held = sorted(k for k in data if k in NEVER)
        if held:
            raise bad("the file holds access data (%s), which is never imported; use a file made by Export" % ", ".join(held))
        current = self.settings._current
        if isinstance(data.get("schema"), bool) or not isinstance(data.get("schema"), int):
            raise bad("the file does not say which settings schema it holds")
        schema, present = data["schema"], set(data)       # an older file: the migrations add the sections it lacks
        try:
            migrate(data, self.settings._migrations, current)          # the same path as a normal load
        except SettingsError as e:
            if data["schema"] > current:
                raise bad("the file is from a newer version (settings schema %d, this box knows %d); update the box first"
                          % (data["schema"], current), 409)
            raise bad("the file cannot be used: %s" % e)
        except Exception as e:                   # a migration met something an older version never wrote
            raise bad("the file cannot be used: its settings are not what schema %s held (%s)" % (schema, type(e).__name__))
        known = {n for n, _ in SECTIONS}
        unknown = sorted(k for k in data if k not in known and k != "schema" and k not in default_settings())
        if unknown:
            raise bad("the file has sections this version does not know: %s" % ", ".join(_printable(k) for k in unknown))
        self._notes = []
        clean = {}
        for name, check in SECTIONS:
            if name not in present or name not in data:      # what a migration added is a default, not the file's word
                self.note("%s is not in the file; left as it is" % name)
                continue
            try:
                clean[name] = check(data[name], self)
            except CHECK_ERRORS as e:
                raise bad("%s: %s" % (name, e))
        for name in sorted(k for k in data if k not in known and k != "schema" and k not in NEVER):
            self.note("%s is not imported by this version; left as it is" % name)
        a = clean.get("autostart")
        if a and a["mode"] == "pad":             # the pad it starts: the file's, or the box's own when the file has no pads
            b, i = a["pad"]
            with self.settings.lock:
                banks = copy.deepcopy((clean if "pads" in clean else self.settings.data)["pads"]["banks"])
            if not (b < len(banks) and banks[b]["pads"][i].get("file")):
                raise bad("autostart: the pad it starts has no clip")
        return clean, list(self._notes), passwords

    def _keep_secrets(self, clean, current):
        """A file made without passwords: keep the one this box already has for the same projector, and the box's
        own full address of a stream whose shortened form is what the file holds."""
        kept = 0
        mine = {p["id"]: p for p in current.get("projectors", [])}
        for p in clean.get("projectors", []):
            old = mine.get(p["id"])
            if not p["password"] and old and old.get("password") and (old["host"], old["port"]) == (p["host"], p["port"]):
                p["password"] = old["password"]
                kept += 1
        mine = {s["id"]: s for s in current.get("streams", [])}
        for s in clean.get("streams", []):
            old = mine.get(s["id"])
            if old and old["url"] != s["url"] and self._shortened(old["url"], s["url"]):
                s["url"] = old["url"]
                kept += 1
        return kept

    @staticmethod
    def _shortened(full, short):
        """Is `short` what is left of `full` after its secrets were cut, once or more than once? An RTMP address
        loses the last part of its path at every export, so a file that went through a box that never had the
        stream holds less than this box's own export would. Cutting never changes the scheme, host or port."""
        while True:
            less = strip_login(full)
            if less == short:
                return True
            if less == full or not less:
                return False
            full = less

    def _backup(self):
        """The settings as they are now, beside the file, for whoever wants them back (mode 0600; the last few)."""
        path = "%s.before-import-%d" % (self.settings.path, int(self._now()))
        self.settings._write(path, self.settings.data)
        for old in self._siblings(".before-import-")[:-KEEP_IMPORT_BACKUPS]:
            self._unlink(old)
        return os.path.basename(path)

    def _siblings(self, suffix):
        """Files beside settings.json named settings.json<suffix>..., oldest first (plain files only)."""
        folder, base = os.path.dirname(self.settings.path) or ".", os.path.basename(self.settings.path)
        found = []
        try:
            for entry in os.scandir(folder):
                if entry.name.startswith(base + suffix) and entry.is_file(follow_symlinks=False):
                    found.append(entry.path)
        except OSError:
            pass
        return sorted(found, key=lambda p: (len(p), p))

    @staticmethod
    def _unlink(path):
        try:
            os.unlink(path)
            return True
        except OSError:
            return False

    def _replace(self, new):
        """Swap the settings in place (others hold the dictionary itself), never passing through an empty state."""
        data = self.settings.data
        for k, v in new.items():
            data[k] = v
        for k in [k for k in data if k not in new]:
            del data[k]

    def _apply(self):
        """Make the running box match the settings. Nothing here may stop the rest: what fails is reported."""
        api, problems = self.api, []
        api.mapper.edit.update(on=False, selected=None, corner=0)

        def control():
            with api._control_lock:
                for manager in (api.dmx, api.midi):
                    if manager is not None:
                        manager.apply()
        for label, fn in (("OSC", api.osc.apply if api.osc else None), ("DMX and MIDI", control), ("Sync", api.sync.apply),
                          ("Mapper", api.mapper.apply), ("Picture over the video", api.apply_overlay),
                          ("Sound output", api.apply_audio), ("Projectors", api.projectors.apply)):
            if fn is None:
                continue
            try:
                fn()
            except Exception as e:
                problems.append("%s: %s" % (label, getattr(e, "message", None) or e))
        return problems

    def import_settings(self, raw, confirm, device, client):
        """Check an exported file and make it the box's settings. The PIN, devices and remote support stay as they
        are. Nothing is changed unless the whole file passes."""
        self._local(client, "an import")
        if confirm != CONFIRM_IMPORT:
            raise bad("send confirm=%s" % CONFIRM_IMPORT)
        with self._busy("an import"):
            return self._import(raw, device)

    def _import(self, raw, device):
        clean, notes, passwords = self.check_file(raw)
        with self.settings.lock:
            current = self.settings.data
            kept = 0 if passwords else self._keep_secrets(clean, current)
            # The checks know schema KNOWN_SCHEMA: what a later schema adds to these sections is put back by its own
            # migration. Only what came from the file goes through it; the box's own sections are already current,
            # and a migration is not written to run twice.
            fresh = dict(copy.deepcopy(clean), schema=min(KNOWN_SCHEMA, self.settings._current))
            try:
                migrate(fresh, self.settings._migrations, self.settings._current)
            except Exception as e:
                raise bad("the file cannot be brought up to this version's settings: %s" % (e if isinstance(e, SettingsError) else type(e).__name__))
            new = copy.deepcopy(current)
            new.update({k: fresh[k] for k in clean})
            try:
                backup = self._backup()
            except OSError as e:
                raise ApiError(500, "could not keep a copy of the present settings, so nothing was changed: %s" % (e.strerror or e))
            previous = copy.deepcopy(current)
            self._replace(new)
            try:
                self.settings.save()
            except OSError as e:                 # a full or read-only disk: the box goes on with what it had
                self._replace(previous)
                self._unlink(os.path.join(os.path.dirname(self.settings.path) or ".", backup))
                raise ApiError(500, "the settings could not be saved, so nothing was changed: %s" % (e.strerror or e))
        problems = self._apply()
        print("pvj-web: settings imported by %s" % device["name"], flush=True)
        return {"imported": sorted(clean), "notes": notes, "problems": problems, "backup": backup,
                "passwords_kept": kept, "passwords_in_file": passwords}

    # ---- diagnostics -------------------------------------------------------------------------------------------
    def _secrets(self, data):
        """Every secret the box knows right now, to take out of any text in the diagnostics file."""
        api, found = self.api, set()
        found.add(getattr(api.auth, "current_pin", None))
        auth = data.get("auth") or {}
        found.update((auth.get("pin_hash"), auth.get("pin_salt")))
        found.update(d.get("token_hash") for d in data.get("devices", []))
        found.update(p.get("password") for p in data.get("projectors", []))
        for s in data.get("streams", []):
            found.update(stream_secrets(s.get("url")))
        found.add((data.get("support") or {}).get("server_key"))
        try:
            found.update(j["code"] for j in api.auth.list_joins())
        except Exception:
            pass
        session = api.support.session
        if session:
            code = session.get("code") or ""
            found.update((code, code[:4] + "-" + code[4:]))
            found.update(session.get("tokens", {}))
        return sorted((s for s in found if isinstance(s, str) and len(s) >= 3), key=len, reverse=True)

    def read_log(self, secrets=()):
        """The services' recent log lines, if this unprivileged service may read them. It is told apart: lines came
        back, none came back (the usual case: the panel's user is not in the systemd-journal group, on purpose),
        or the command could not run. Each line is scrubbed whole and only then cut to length: cut first, a secret
        that straddles the cut would lose the end that makes it recognisable and keep its beginning."""
        argv = ["journalctl", "--no-pager", "-q", "-b", "-n", str(LOG_LINES), "-o", "short-iso"]
        for unit in LOG_UNITS:
            argv += ["-u", unit]
        try:
            r = self._run(argv, capture_output=True, text=True, timeout=10, errors="replace")
        except (OSError, subprocess.SubprocessError) as e:
            return {"readable": False, "lines": [], "note": "the system log could not be read: %s" % (getattr(e, "strerror", None) or type(e).__name__)}
        lines = [scrub(line, secrets)[:LOG_LINE_MAX] for line in [x for x in (r.stdout or "").splitlines() if x.strip()][-LOG_LINES:]]
        if r.returncode != 0:
            return {"readable": False, "lines": [], "note": "the system log could not be read: %s" % (r.stderr or "").strip()[-200:]}
        if not lines:
            return {"readable": False, "lines": [],
                    "note": "No log lines came back. The panel runs as its own unprivileged user, which is not allowed to read the "
                            "system log. On the box: sudo journalctl -b -u 'pvj-*'"}
        return {"readable": True, "lines": lines, "note": "the last %d lines of this start, with PINs, codes and logins removed" % len(lines)}

    def diagnostics(self, body, device, client):
        """One file to send to whoever is helping: versions, the board, module states, health, the last update, the
        log if it can be read, and the settings with every secret removed."""
        api = self.api
        with self.settings.lock:
            data = copy.deepcopy(self.settings.data)
        secrets = self._secrets(data)

        def part(fn):
            try:
                return fn()
            except Exception as e:                # a missing piece must not cost the rest
                return {"error": "not available: %s" % (getattr(e, "message", None) or type(e).__name__)}

        def player():
            """What plays, with a stream shown only as where it comes from (its saved name stays): a path can be a
            stream key, and the address may be one that is not among the saved streams."""
            status = api._public_player_status()
            if isinstance(status.get("path"), str) and "://" in status["path"]:
                status["path"] = stream_where(status["path"])
            return status
        from . import __version__
        out = {"made": int(self._now()), "made_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self._now())),
               "version": __version__, "settings_schema": data.get("schema"), "board": dict(api.board),
               "system": part(lambda: api.system_info({}, device, client)),
               "player": part(player),
               "modules": part(lambda: [{k: m[k] for k in ("id", "version", "type", "status", "supported", "enabled")}
                                        for m in api.registry.list()]),
               "health": part(api.health.report),
               "update": part(lambda: {k: v for k, v in api.update_status({}, device, client).items() if k != "version"}),
               "log": part(lambda: self.read_log(secrets)),
               "settings": public_settings(data, part(api.support.configured)),
               "left_out": "The PIN, device tokens, join and support codes, remote-support keys and addresses, projector "
                           "passwords and stream logins are never in this file."}
        return {"name": self._name("diagnostics"), "file": scrub(out, secrets)}

    # ---- factory reset -----------------------------------------------------------------------------------------
    def _media_on_usb(self):
        """True when the media folder is on (or is) a USB drive: PVJ_MEDIA_DIR may point there, and a reset must
        never empty a drive someone plugged in."""
        api = self.api
        media = os.path.realpath(api.media_dir)
        for root in (api.usb_root, api.usb_link):
            try:
                root = os.path.realpath(root)
                if os.path.commonpath([media, root]) == root:
                    return True
            except (ValueError, OSError, TypeError):
                pass
        return False

    def _delete_media(self, problems):
        """The clips in the box's own media folder: media files (and left-over upload pieces) at its top level only,
        plain files and links, one by one. Anything else someone put there stays."""
        api, removed = self.api, 0
        with api._media_lock:
            try:
                entries = list(os.scandir(api.media_dir))
            except OSError as e:
                problems.append("the media folder could not be read: %s" % (e.strerror or e))
                return 0
            for entry in entries:
                if not (entry.is_symlink() or entry.is_file(follow_symlinks=False)):
                    continue
                if not (entry.name.lower().endswith(MEDIA_EXTENSIONS) or entry.name.startswith(".upload-")):
                    continue
                if self._unlink(entry.path):
                    removed += 1
                else:
                    problems.append("could not delete %s" % _printable(entry.name))
        return removed

    def _wipe_access(self):
        """Settings back to defaults, no device, no join code, a new PIN: one step, under the two locks every way
        in needs (pairing holds the first, adding a device the second; both in the order pair() takes them). A full
        device's request that was already past its token check cannot leave a join code or an invited device
        behind: it runs before all of this, and what it made is wiped here, or after all of it, and then the API
        takes back what it made (api._still_paired). Returns the new PIN."""
        auth = self.api.auth
        with auth._pair_lock, self.settings.lock:
            previous = copy.deepcopy(self.settings.data)
            new = default_settings()
            new["schema"] = self.settings._current
            new["auth"] = dict(previous["auth"])           # replaced below: the box is never without a PIN
            self._replace(new)
            auth._joins.clear()
            auth._fails.clear()
            auth._global_fails = []
            auth._locked_until.clear()
            try:
                pin = auth._new_pin()                      # saves the emptied settings with the new PIN
            except OSError as e:
                self._replace(previous)
                raise ApiError(500, "the settings could not be saved, so the box was not reset: %s" % (e.strerror or e))
            auth.last_seen.clear()
            auth._joins.clear()
        return pin

    def factory_reset(self, body, device, client):
        """{"confirm": "factory-reset", "media": "keep" | "delete"}: playback stopped, settings back to defaults, every
        paired device, join code and support session gone, a new PIN (so the PIN screen returns), and the clips kept
        or deleted (never on a USB drive)."""
        self._local(client, "a factory reset")
        if body.get("confirm") != CONFIRM_RESET:
            raise bad('send {"confirm": "%s"}' % CONFIRM_RESET)
        media = body.get("media")
        if media not in ("keep", "delete"):
            raise bad('say what happens to the clips: "media": "keep" or "delete"')
        with self._busy("a factory reset", usb_copy=True):
            return self._reset(media, device, client)

    def _reset(self, media, device, client):
        api = self.api
        if media == "delete" and self._media_on_usb():
            raise ApiError(409, "the clips of this box are on a USB drive, which a reset never empties; choose to keep the clips")
        if not api._upload_lock.acquire(blocking=False):
            raise ApiError(409, "an upload is running; reset when it has finished")
        try:
            problems, deleted = [], 0
            # Playback stops and the picture comes back from black either way: the PIN is only drawn on an idle
            # player, and nobody would see it behind a looping clip or a blackout.
            try:
                api.vibes.stop()                          # the shader rotation would put the next shader over the PIN
            except Exception:
                pass
            api.room.stop()                               # a scene still under way sends nothing more
            for call, body in ((api.blackout, {"on": False}), (api.control, {"action": "reset"}), (api.control, {"action": "stop"})):
                try:
                    call(body, device, client)
                except Exception:
                    pass
            with api.support.lock:
                if api.support.session:
                    api.support._end("factory reset", tell_helper=True)
            api.auth.cancel_join(None)
            if api.pinscreen is not None:
                try:
                    api.pinscreen.hide()
                except Exception:
                    pass
            pin = self._wipe_access()
            with api.support.lock:                        # one started between the first look and the wipe; none can be now
                if api.support.session:
                    api.support._end("factory reset", tell_helper=True)
            if api.on_pin:
                api.on_pin(pin)
            with self.settings.lock:                      # the automatic backup would still hold the old devices
                self.settings._write(self.settings.path + ".bak", self.settings.data)
            for suffix in (".before-import-", ".bak-v", ".rolled-back-"):
                for path in self._siblings(suffix):
                    if not self._unlink(path):
                        problems.append("could not remove %s" % os.path.basename(path))
            try:
                for name in os.listdir(api._update_inbox()):
                    self._unlink(os.path.join(api._update_inbox(), name))
            except OSError:
                pass
            if media == "delete":
                deleted = self._delete_media(problems)
            problems += self._apply()
        finally:
            api._upload_lock.release()
        print("pvj-web: factory reset by %s (clips %s)" % (device["name"], "deleted" if media == "delete" else "kept"), flush=True)
        return {"reset": True, "media": media, "deleted": deleted, "problems": problems,
                "note": "Every device is unpaired. The new PIN is on the box's display (or: sudo pvj-pin)."}
