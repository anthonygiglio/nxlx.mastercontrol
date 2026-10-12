# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Remote support sessions, the panel's side (the tunnel itself is pvj-supportd's job).

How it works for the people involved:

* The owner's support server details are set once (System > Remote support, or /etc/pvj/support.json for a fleet),
  and remote support stays off until a full-access device *at the studio* allows it.
* When help is needed, someone at the studio with full access presses Start, chooses how long (1 hour unless they
  choose otherwise, at most 4) and how much support may do. The box dials out to the support server; nothing is
  opened on the studio's router.
* The panel shows a support code (like ABCD-2345). The studio reads it to support on the phone; support opens the
  box's tunnel address in a browser and types it. The code works only through the tunnel, only during that session,
  for at most 3 logins, with the same guess limits as the PIN.
* Every connected device shows a banner with the time left and a Stop button. The session ends by itself at the
  deadline (the helper enforces it), on Stop, on a restart or a reboot; support's logins end with it. They are never
  saved as devices.
* Over the tunnel support cannot, whatever its role (D84: nothing that outlives the session or locks the owner out):
  change these settings, start or extend a session, pair or invite devices, make codes or links, change, read or
  unlock the PIN, touch recovery or the secure connection, remove an Owner's device, reset the box to factory
  settings, export settings with their passwords, or power the box off (a restart is allowed; it ends the session).
* With the Owner's role support can do the rest, including what was the studio's alone before D84: import a
  settings file (without the parts above), add and remove a theme, show and renew the OSC key, change where the
  box sends its state, lock and open guest controls, and remove a Guest's or an Operator's device.

All of this is logged (the journal, and the last sessions in the panel with what support changed).
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time

from . import supportd as sd

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O or 1/I: read aloud over the phone
CODE_LENGTH = 8
MAX_LOGINS = 3
DURATIONS = (15, 30, 60, 120, 240)
DEFAULT_MINUTES = 60
ROLES = ("view", "live", "full")
LOG_KEEP = 20
FAILS, FAIL_WINDOW, LOCKOUT = 5, 600, 300
HANDSHAKE_FRESH = 180            # seconds: the support server answered recently = connected
DEFAULTS_FILE = "/etc/pvj/support.json"

# D84. The principle: a helper in a remote session with the Owner's role may do anything that ends when the session
# ends, and nothing that outlives it or locks the owner out. (A remote Owner session can already upload a software
# update signed by a key this box trusts: pvj-update checks the signature against /etc/pvj/allowed_signers, and no
# route changes the signers. So what is refused below is not about limiting a trusted helper in the
# moment; it is about a way in that would still be there after Stop, and about the owner's own way in.)
#
# Never over the tunnel, whatever the role: route -> why. A route that is not here and not under a prefix below is
# answered to a remote session by its role like any other (a session below Owner is held to policy.LEGACY_LIVE, D80).
REMOTE_DENY = {
    # the session's own settings, start and length: a helper does not lengthen or widen his own access
    ("POST", "/api/support/config"): "a helper does not change where the box dials out to, or whether it may",
    ("POST", "/api/support/start"): "a session is started by someone at the studio",
    ("POST", "/api/support/extend"): "a helper does not lengthen his own session",
    # pairing and sessions: a device paired through the tunnel would still be paired after Stop
    ("POST", "/api/pair"): "a device paired now would stay paired after the session",
    ("POST", "/api/session"): "a panel session of a paired device is not made through the tunnel",
    ("POST", "/api/devices/invite"): "an invitation is a way in that outlives the session",
    # the PIN pairs an Owner: showing it, changing it or lifting its lockout is a way in, or a way to shut the owner out
    ("POST", "/api/pin/rotate"): "a new PIN would shut out whoever knows the old one, and be known to the helper",
    ("POST", "/api/pin/unlock"): "lifting the guessing lockout is done at the studio",
    ("POST", "/api/pin/show"): "the PIN pairs an Owner's device",
    ("GET", "/api/qr.svg"): "the QR code carries a way to pair",
    # nobody remote can switch the box back on (a restart is allowed: it ends the session and the box comes back)
    ("POST", "/api/system/poweroff"): "nobody remote can switch the box back on",
    # a network change stays pending until someone confirms it, and goes back by itself otherwise (netcfg.py): the
    # confirm is the proof that the studio still reaches the box, which a helper in the tunnel cannot give. He may
    # make the change and take it back; someone at the studio confirms it. (Added by D84: found while trying to
    # lock the owner out.)
    ("POST", "/api/network/confirm"): "a network change is confirmed from the studio: only that shows the studio still reaches the box",
    # the NDI discovery server (D85): a program listening on the network is opened by the Owner in the room. Its
    # switch is here; its own routes are under a prefix below.
    ("POST", "/api/modules/ndi-discovery"): "a program that listens on the network is switched by the owner in the room (D85)",
    # a reset removes every paired device, the owner's too (see boxcare.py)
    ("POST", "/api/system/factory-reset"): "a reset removes every device, the owner's too",
}
# Everything under these, whatever the role: path prefix -> why.
REMOTE_DENY_PREFIX_WHY = {
    "/api/access": "join codes, links and the choice of what a controller's code pairs are ways in",
    "/api/https": "the box's key and certificate, and the switch that could shut the owner's devices out (D79)",
    "/api/recovery": "the owner's ways back in (D81)",
    "/api/ndi/discovery": "the NDI discovery server's own settings: a listener on the network is the owner's, in the room (D85)",
}
REMOTE_DENY_PREFIX = tuple(REMOTE_DENY_PREFIX_WHY)
# Modules no support session switches ON, whatever its role, by the route or by a settings file (D84 meeting D85):
# module id -> why. Switching one of them off takes something away and is allowed, except where the whole switch
# is refused above (ndi-discovery).
REMOTE_NO_MODULE_ON = {
    "inputs-ndi": "the NDI input makes the box listen to senders on the network and run NDI's library, and stays on after the session",
    "ndi-discovery": "a program that listens on the network is switched by the owner in the room (D85)",
}
REMOTE_OPEN = {("GET", "/api/hello"), ("POST", "/api/support/login")}
# Opened to a remote session of the Owner's role by D84 (each was in REMOTE_DENY before): route -> the bound that
# keeps it inside the principle. Not read by the guard (not being refused is what opens them); it is the record
# the tests hold the handlers to, and every one of them writes what was done into the session's log (Support.note).
REMOTE_OWNER_OPENED = {
    ("POST", "/api/system/settings/import"): "what a file may not carry for a helper is left as the box has it (boxcare.REMOTE_KEPT)",
    ("POST", "/api/theme/add"): "a theme is checked data, never code",
    ("POST", "/api/theme/remove"): "only a theme that was added; the box's own look comes back",
    ("POST", "/api/osc/key"): "the helper then knows the key: the owner is told to make a new one after the session",
    ("POST", "/api/osc/feedback"): "a target is still only a device on the box's own network, never the tunnel",
    ("POST", "/api/guests"): "the lock on guest controls: a switch the owner or an Operator sets back",
    ("POST", "/api/devices/revoke"): "a Guest's or an Operator's device only: never an Owner's",
}
# What a session did, kept with its entry in the support log (SupportManager.note): the major lines, the routine
# lines (one per route, so a few hundred covers every route there is), and the characters of one line.
MAJOR_KEEP, DID_KEEP, DID_TEXT = 200, 200, 400
ALWAYS_KEEP, SOME_KEEP = 100, 5     # the lines that are never displaced; the details kept beside a counted line
# POSTs of a support login that change nothing on the box, so they are no line in what a session did
NOT_A_CHANGE = {("POST", "/api/support/login"), ("POST", "/api/support/stop"), ("POST", "/api/logout"), ("POST", "/api/media/info"),
                ("POST", "/api/system/settings/export"), ("POST", "/api/theme/export")}


def upload_line(update, result):
    """The log line for an update a support login uploaded: its file name and its sha256, so the owner can tell
    afterwards which bundle it was. Neither is a secret. (`update` is kept for the callers; a clip is a routine
    line with its name as a detail.)"""
    name = "".join(c for c in str(result.get("name", "")) if c.isprintable())[:80]
    if update:
        return "uploaded a software update: %s, sha256 %s" % (name, result.get("sha256", "?"))
    return "uploaded a clip: %s" % name


class SupportApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normal_code(text):
    return "".join(c for c in str(text).upper() if c in CODE_ALPHABET)


def blank():
    return {"allowed": False, "endpoint": "", "server_key": "", "address": "", "network": "", "max_minutes": 240}


def validate_config(body, current):
    """New settings from untrusted input (only the keys given change). Raises SupportApiError(400)."""
    cfg = dict(current)
    for k in ("endpoint", "server_key", "address", "network"):
        if k in body:
            v = body[k]
            if not isinstance(v, str) or len(v) > 260:
                raise SupportApiError(400, "%s must be text" % k)
            cfg[k] = v.strip()
    if "allowed" in body:
        if not isinstance(body["allowed"], bool):
            raise SupportApiError(400, "allowed must be true or false")
        cfg["allowed"] = body["allowed"]
    if "max_minutes" in body:
        m = body["max_minutes"]
        if isinstance(m, bool) or m not in DURATIONS:
            raise SupportApiError(400, "the longest session must be one of %s minutes" % ", ".join(map(str, DURATIONS)))
        cfg["max_minutes"] = m
    try:
        if cfg["endpoint"]:
            sd.check_endpoint(cfg["endpoint"])
        if cfg["server_key"]:
            sd.check_key(cfg["server_key"])
        if cfg["address"] or cfg["network"]:
            sd.check_addresses(cfg["address"], cfg["network"])
    except sd.SupportError as e:
        raise SupportApiError(400, str(e))
    return cfg


class SupportManager:
    def __init__(self, settings, auth, client, log=print, clock=time.monotonic, now=time.time, defaults_file=DEFAULTS_FILE,
                 networks_in_use=lambda: [], panel_port=80):
        self.settings, self.auth, self.client, self.log = settings, auth, client, log
        self.networks_in_use, self.panel_port = networks_in_use, panel_port
        self._clock, self._now = clock, now
        self.defaults_file = defaults_file
        self.lock = threading.RLock()
        self.session = None            # see start()
        self._fails, self._locked_until = {}, {}
        self._key = None               # this box's public key, once read

    # -- settings ---------------------------------------------------------------------------------------------
    def defaults(self):
        """The fleet's support server details from /etc/pvj/support.json, if the owner put one there."""
        try:
            with open(self.defaults_file) as f:
                d = json.load(f)
            return validate_config({k: d[k] for k in ("endpoint", "server_key", "network", "address", "max_minutes") if k in d}, blank())
        except (OSError, ValueError, SupportApiError):
            return blank()

    def config(self):
        """Settings over the fleet defaults (a setting left empty takes the default)."""
        base = self.defaults()
        mine = self.settings.data.get("support", blank())
        out = dict(base)
        for k, v in mine.items():
            if v not in ("", None) or k in ("allowed",):
                out[k] = v
        return out

    def configured(self, cfg=None):
        cfg = cfg or self.config()
        return all(cfg[k] for k in ("endpoint", "server_key", "address", "network"))

    def network(self):
        cfg = self.config()
        try:
            return ipaddress.ip_network(cfg["network"]) if cfg["network"] else None
        except ValueError:
            return None

    def is_remote(self, client):
        """True for a request that came through the support tunnel: only while a session is open (no tunnel, nothing
        is remote, so a support network that happens to match a studio's own network can never lock the studio out),
        and only from an address in the support network. The address is the connection's own, never a header."""
        if not self.session:
            return False
        net = self.network()
        if net is None:
            return False
        try:
            addr = ipaddress.ip_address(client)
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return addr in net

    def overlap(self, network):
        """The first network this box is already on that overlaps `network`, or None."""
        try:
            net = ipaddress.ip_network(network)
        except ValueError:
            return None
        for n in self.networks_in_use():
            if n.version == net.version and n.overlaps(net):
                return n
        return None

    # -- guard for every API request ----------------------------------------------------------------------------
    def guard(self, method, path, device, client):
        remote = self.is_remote(client)
        if device and device.get("remote") and not remote:
            raise SupportApiError(403, "a support login works only through the support tunnel")
        if not remote:
            return
        if (method, path) in REMOTE_OPEN:
            return
        if device is not None and not device.get("remote"):
            raise SupportApiError(403, "through the support tunnel only a support login works")
        if (method, path) in REMOTE_DENY or path.startswith(REMOTE_DENY_PREFIX):
            raise SupportApiError(403, "this cannot be done through remote support; ask someone at the studio")

    def need_local(self, client):
        if self.is_remote(client):
            raise SupportApiError(403, "only a device at the studio can do this")

    # -- the helper -------------------------------------------------------------------------------------------
    def _helper(self, message):
        if self.client is None:
            raise SupportApiError(503, "remote support is not set up on this box")
        try:
            reply = self.client.request(message)
        except OSError as e:
            raise SupportApiError(503, str(e))
        if not reply.get("ok"):
            raise SupportApiError(409, reply.get("error") or "the remote support helper refused")
        return reply

    def close_leftover(self):
        """At the panel's start: a tunnel left open by an earlier run of the panel (a crash, a restart) has no banner
        and no Stop button here, so close it."""
        if self.client is None:
            return
        try:
            reply = self.client.request({"cmd": "stop"})
            if reply.get("ok"):
                self.log("pvj-web: remote support: closed any session left from an earlier run")
        except OSError:
            pass

    def public_key(self):
        if self._key is None:
            self._key = self._helper({"cmd": "key"})["public_key"]
        return self._key

    # -- sessions ---------------------------------------------------------------------------------------------
    def note(self, device, text, key=False, major=True, always=False, detail=None):
        """What a support login just did, into its session's entry of the support log and the journal: the owner reads
        it on the Remote support page. Never a secret: the caller says what was done, not the value.

        Three lists, so that NO sequence of actions keeps what matters most out of the owner's sight (the security
        review of #125 and its re-check: one list let sixty routine lines push an update off it, and two let 110
        themes do the same).
        * `always=True`: a software update, a settings file, a device removed. A list nothing else writes to. Each
          is rare and heavy; past ALWAYS_KEEP the first half and the newest half stay and `always_dropped` counts
          those between.
        * major (the default): what the other opened routes changed and everything the session was REFUSED. The
          text is fixed by the code; what a request chooses (a theme's id) goes into `detail`, of which the first
          SOME_KEEP are kept beside the count. So the number of distinct lines is bounded by the code, not by the
          helper; MAJOR_KEEP is far above it, and `major_more` would still say so if it were ever reached.
        * `major=False`: routine use, one line per route with a count.

        The same line again is a count, kept in memory until the next save: a fader dragged through the tunnel, or
        a refused request sent a thousand times, writes neither the settings file nor the journal each time.
        `key`: the login saw or made the OSC key, so the page says to make a new one."""
        if not (isinstance(device, dict) and device.get("remote")):
            return
        clean = lambda v: "".join(c for c in str(v) if c.isprintable())[:DID_TEXT]
        text = clean(text)
        which, keep, more = ("always", ALWAYS_KEEP, "always_dropped") if always else (("major", MAJOR_KEEP, "major_more") if major else ("did", DID_KEEP, "did_more"))
        with self.lock:
            s = self.session
            if not s:
                return
            again = False
            with self.settings.lock:
                for entry in self.settings.data.get("support_log", []):
                    if entry.get("id") != s["id"]:
                        continue
                    lines = [dict(d) for d in entry.get(which, [])]
                    same = next((d for d in lines if d.get("what") == text), None)
                    if same is not None:
                        same["n"] = int(same.get("n", 1)) + 1
                        same["last"] = int(self._now())
                        again = not (key and not entry.get("osc_key"))
                        if detail is not None and len(same.get("some", [])) < SOME_KEEP and clean(detail)[:80] not in same.get("some", []):
                            same["some"] = list(same.get("some", [])) + [clean(detail)[:80]]
                    else:
                        line = {"at": int(self._now()), "what": text}
                        if detail is not None:
                            line["some"], line["n"] = [clean(detail)[:80]], 1
                        if len(lines) < keep:
                            lines.append(line)
                        elif always:                    # the first half and the newest half stay; those between are counted
                            del lines[keep // 2]
                            lines.append(line)
                            entry[more] = int(entry.get(more, 0)) + 1
                        else:
                            entry[more] = int(entry.get(more, 0)) + 1
                            again = entry[more] > 1     # said once in the journal that the list is full, then counted
                    entry[which] = lines
                    if key:
                        entry["osc_key"] = True
                if not again:
                    try:
                        self.settings.save()
                    except OSError:
                        pass
        if not again:
            self.log("pvj-web: remote support %s: %s%s" % (device.get("id"), text, "" if detail is None else " (%s)" % clean(detail)[:80]))

    def refused(self, device, what, detail=None):
        """A support login (or a request through the tunnel with no login at all) was refused something no session may
        do: a major line. `what` names the request or the rule, never what was sent."""
        if not (isinstance(device, dict) and device.get("remote")):
            device = {"id": "support (not signed in)", "remote": True}
            what = "%s, not signed in" % what
        self.note(device, "was refused %s" % what, detail=detail)

    def used(self, device, method, path):
        """Every other request of a support login that changed something (Api.handle and the server's own uploads
        call this after a POST that went well): the route, never its body. The routes D84 opened say more, in their
        own words, and are not said twice."""
        if not (isinstance(device, dict) and device.get("remote")) or method != "POST":
            return
        if (method, path) in NOT_A_CHANGE or (method, path) in REMOTE_OWNER_OPENED:
            return
        self.note(device, "used %s" % path, major=False)

    def key_renewed(self):
        """A new OSC key was made at the studio: no past session knows the key any more, so the page stops saying so."""
        with self.settings.lock:
            changed = False
            for entry in self.settings.data.get("support_log", []):
                if entry.pop("osc_key", None):
                    changed = True
            if changed:
                try:
                    self.settings.save()
                except OSError:
                    pass

    def _log(self, entry):
        with self.settings.lock:
            log = list(self.settings.data.get("support_log", []))
            log.insert(0, entry)
            self.settings.data["support_log"] = log[:LOG_KEEP]
            self.settings.save()

    def _seconds_left(self):
        s = self.session
        return max(0, int(s["until"] - self._clock())) if s else 0

    def _check_expiry(self):
        if self.session and self._clock() >= self.session["until"]:
            self._end("the session time ran out", tell_helper=True)

    def _end(self, reason, tell_helper):
        s = self.session
        if not s:
            return
        self.session = None
        if tell_helper:
            try:
                self._helper({"cmd": "stop"})
            except SupportApiError as e:
                self.log("pvj-web: remote support: could not tell the helper to stop: %s" % e.message)
        with self.settings.lock:
            for entry in self.settings.data.get("support_log", []):
                if entry.get("id") == s["id"]:
                    entry.update(ended=int(self._now()), reason=reason, logins=s["logins"])
            self.settings.save()
        self.log("pvj-web: remote support session ended: %s (%d support logins)" % (reason, s["logins"]))

    def start(self, body, device, client):
        self.need_local(client)
        if body.get("confirm") != "start":
            raise SupportApiError(400, "send confirm: \"start\"")
        cfg = self.config()
        if not cfg["allowed"]:
            raise SupportApiError(409, "remote support is not allowed on this box; allow it first")
        if not self.configured(cfg):
            raise SupportApiError(409, "set the support server details first")
        minutes = body.get("minutes", DEFAULT_MINUTES)
        if isinstance(minutes, bool) or minutes not in DURATIONS or minutes > cfg["max_minutes"]:
            raise SupportApiError(400, "choose %s minutes" % ", ".join(str(m) for m in DURATIONS if m <= cfg["max_minutes"]))
        role = body.get("role", "full")
        if role not in ROLES:
            raise SupportApiError(400, "role must be view, live or full")
        clash = self.overlap(cfg["network"])
        if clash is not None:
            raise SupportApiError(409, "the support network %s overlaps a network this box is on (%s); ask your support "
                                       "provider for a different one" % (cfg["network"], clash))
        with self.lock:
            if self.session:
                raise SupportApiError(409, "a session is already running")
            reply = self._helper({"cmd": "start", "minutes": minutes, "endpoint": cfg["endpoint"], "server_key": cfg["server_key"],
                                  "address": cfg["address"], "network": cfg["network"], "port": self.panel_port})
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            sid = secrets.token_hex(4)
            self.session = {"id": sid, "until": self._clock() + minutes * 60, "until_epoch": reply.get("until", int(self._now() + minutes * 60)),
                            "role": role, "by": device["name"], "code": code, "logins": 0, "tokens": {}}
            self._fails.clear()
            self._locked_until.clear()
            self._log({"id": sid, "started": int(self._now()), "by": device["name"], "minutes": minutes, "role": role})
            self.log("pvj-web: remote support session started by %s for %d minutes, support role %s" % (device["name"], minutes, role))
        return self.status(device, client)

    def extend(self, body, device, client):
        self.need_local(client)
        minutes = body.get("minutes")
        cfg = self.config()
        if isinstance(minutes, bool) or minutes not in DURATIONS or minutes > cfg["max_minutes"]:
            raise SupportApiError(400, "choose %s minutes" % ", ".join(str(m) for m in DURATIONS if m <= cfg["max_minutes"]))
        with self.lock:
            self._check_expiry()
            if not self.session:
                raise SupportApiError(409, "no session is running")
            reply = self._helper({"cmd": "extend", "minutes": minutes})
            self.session["until"] = self._clock() + minutes * 60
            self.session["until_epoch"] = reply.get("until", int(self._now() + minutes * 60))
            self.log("pvj-web: remote support session set to end in %d minutes by %s" % (minutes, device["name"]))
        return self.status(device, client)

    def stop(self, body, device, client):
        with self.lock:
            if self.session:
                who = device["name"] + (" (support)" if device.get("remote") else "")
                self._end("stopped by %s" % who, tell_helper=True)
            else:
                try:
                    self._helper({"cmd": "stop"})
                except SupportApiError:
                    pass
        return self.status(device, client)

    # -- support's login ----------------------------------------------------------------------------------------
    def _throttled(self, client):
        t = self._clock()
        for key in (client, "*"):
            if self._locked_until.get(key, 0) > t:
                return int(self._locked_until[key] - t) + 1
        return 0

    def _fail(self, client):
        t = self._clock()
        for key, limit in ((client, FAILS), ("*", FAILS * 3)):
            fails = [x for x in self._fails.get(key, []) if t - x < FAIL_WINDOW] + [t]
            self._fails[key] = fails
            if len(fails) >= limit:
                self._locked_until[key] = t + LOCKOUT
                self._fails[key] = []

    def login(self, body, client):
        if not self.is_remote(client):
            raise SupportApiError(403, "the support code works only through the support tunnel")
        with self.lock:
            self._check_expiry()
            wait = self._throttled(client)
            if wait:
                raise SupportApiError(429, "too many attempts; wait %d seconds" % wait)
            s = self.session
            given = normal_code(body.get("code", ""))
            if not s or not hmac.compare_digest(given, s["code"]):
                self._fail(client)
                raise SupportApiError(403, "wrong code, or no support session is running")
            if s["logins"] >= MAX_LOGINS:
                raise SupportApiError(403, "this code has been used %d times; ask the studio to start a new session" % MAX_LOGINS)
            s["logins"] += 1
            token = secrets.token_urlsafe(24)
            dev = {"id": "support-%d" % s["logins"], "name": "Remote support", "role": s["role"], "created": int(self._now()), "remote": True}
            s["tokens"][_hash(token)] = dev
            self.log("pvj-web: remote support logged in from %s (login %d of %d)" % (client, s["logins"], MAX_LOGINS))
            return {"token": token, "device": dict(dev), "seconds_left": self._seconds_left()}

    def authenticate(self, token):
        if not isinstance(token, str) or not token:
            return None
        with self.lock:
            self._check_expiry()
            if not self.session:
                return None
            h = _hash(token)
            found = None
            for k, dev in self.session["tokens"].items():
                if hmac.compare_digest(k, h):
                    found = dev
            return dict(found) if found else None

    def logout(self, device):
        """Support's own Log out (D77): that one login's token is forgotten, as every login's is when the session
        ends. The session itself goes on until Stop or its deadline, and the login still counts toward MAX_LOGINS,
        so logging out buys no extra login. True if a token was forgotten."""
        with self.lock:
            s = self.session
            if not s:
                return False
            gone = [k for k, dev in s["tokens"].items() if dev["id"] == device.get("id")]
            for k in gone:
                del s["tokens"][k]
            if gone:
                self.log("pvj-web: remote support %s logged out (the session goes on)" % device.get("id"))
            return bool(gone)

    # -- what the panel shows -----------------------------------------------------------------------------------
    def banner(self):
        """For every device's status poll: cheap, no helper call."""
        with self.lock:
            self._check_expiry()
            s = self.session
            return {"active": bool(s), "seconds_left": self._seconds_left(), "role": s["role"] if s else None}

    def status(self, device, client):
        with self.lock:
            self._check_expiry()
            s = self.session
            out = {"active": bool(s), "seconds_left": self._seconds_left(), "until": s["until_epoch"] if s else None,
                   "role": s["role"] if s else None, "remote": self.is_remote(client)}
            tunnel = None
            if s:
                try:
                    tunnel = self._helper({"cmd": "status"})
                except SupportApiError as e:
                    tunnel = {"error": e.message}
                if tunnel.get("active") is False:          # the helper closed it (deadline, restart): end ours too
                    self._end("the tunnel closed", tell_helper=False)
                    s = None
                    out.update(active=False, seconds_left=0, until=None, role=None)
            if s and tunnel and "last_handshake" in tunnel:
                age = int(self._now()) - tunnel["last_handshake"] if tunnel["last_handshake"] else None
                out["connected"] = age is not None and age < HANDSHAKE_FRESH
                out["last_handshake_seconds"] = age
            if not device or device.get("remote") or self.is_remote(client) or not self.auth.allows(device, "full"):
                return out
            # A full-access device at the studio: everything, including the code to read out.
            cfg = self.config()
            out["config"] = {k: cfg[k] for k in ("allowed", "endpoint", "server_key", "address", "network", "max_minutes")}
            out["configured"] = self.configured(cfg)
            out["durations"] = [m for m in DURATIONS if m <= cfg["max_minutes"]]
            out["log"] = self.settings.data.get("support_log", [])[:10]
            if s:
                out["code"] = s["code"][:4] + "-" + s["code"][4:]
                out["logins"], out["max_logins"] = s["logins"], MAX_LOGINS
                out["address"] = cfg["address"] + ("" if self.panel_port == 80 else ":%d" % self.panel_port)
            try:
                out["public_key"] = self.public_key()
                out["available"] = True
            except SupportApiError as e:
                out["public_key"], out["available"], out["why"] = None, False, e.message
            return out

    def set_config(self, body, device, client):
        self.need_local(client)
        with self.lock:
            if self.session:
                raise SupportApiError(409, "stop the running session first")
            with self.settings.lock:
                cfg = validate_config(body, self.settings.data.get("support", blank()))
                clash = self.overlap(cfg["network"]) if cfg["network"] else None
                if clash is not None:
                    raise SupportApiError(400, "the support network %s overlaps a network this box is on (%s)" % (cfg["network"], clash))
                self.settings.data["support"] = cfg
                self.settings.save()
            self.log("pvj-web: remote support settings changed by %s (allowed: %s)" % (device["name"], cfg["allowed"]))
        return self.status(device, client)
