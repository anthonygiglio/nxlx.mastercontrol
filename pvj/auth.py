# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Device pairing and access control.

* A 4-digit PIN (shown on the box) pairs a phone or tablet and yields a token.
* Tokens are random 192-bit values; only their SHA-256 is stored.
* Roles: view (read only) < live (play and mix) < full (everything).
* A paired full-access device can also make short-lived JOIN CODES (6 digits) for guests (view) or presenters (live).
  They are meant to be shown on the display, so they can never give full access, they expire, they work a limited
  number of times, and only a few can exist at once. They live in memory only: a restart clears them.
* A presenter (live) may make and end the GUEST code, within PRESENTER_JOIN_MINUTES and PRESENTER_JOIN_MAX_USES, and
  nothing else (D48; the API decides what a role may ask for, this module keeps the limits and who made a code).
* Bounds (review of D48): at most MAX_DEVICES paired devices, of which FULL_RESERVED places are kept for full-access
  devices, so guests and presenters can never fill the list and keep the owner's PIN from pairing; at most
  PRESENTER_CODES_PER_HOUR guest codes made by presenters in an hour; and a device that joined with a GUEST code is
  dropped once it has not been used for GUEST_IDLE_DAYS (its last use is written down at most once a day).
* A CONTROLLER CODE (D61) is one more 6-digit code, made only by a deliberate hold on a MIDI controller plugged into
  the box and drawn only on the box's display, for someone who stands at the box with no paired device. It has its
  own single place here, apart from the join codes: it works once, for CONTROLLER_SECONDS, is never listed with its
  digits, and at most CONTROLLER_PER_HOUR are made in an hour. Its "join" kind pairs a presenter, its "owner" kind
  a full-access device. Both need the box setting `controller_code` (off unless a full-access device switched it
  on; the owner kind has its own switch), which is read again when the code is used.
* The PIN is stored as a salted scrypt hash. Because it is short, guessing is
  throttled per client and globally, and comparisons are constant-time. The PIN in clear is known to the run that
  made it (`current_pin`), and a paired full-access device may read it back (`show_pin`, D77; rate limited, and
  the API writes a journal line each time). A run that started without making a new PIN does not know it.
"""

import hashlib
import hmac
import secrets
import threading
import time

ROLES = {"view": 1, "live": 2, "full": 3}
PIN_LENGTH = 4
JOIN_LENGTH = 6
JOIN_ROLES = ("view", "live")
MAX_JOINS = 4
JOIN_MIN_MINUTES, JOIN_MAX_MINUTES, JOIN_DEFAULT_MINUTES = 1, 120, 15
JOIN_MAX_USES, JOIN_DEFAULT_USES = 50, 20
PRESENTER_JOIN_MINUTES = (15, 60, 120)          # what a presenter may choose for a guest code: the panel's own three choices
PRESENTER_JOIN_MAX_USES = 20
JOIN_MAKERS = ("owner", "presenter")
PRESENTER_CODES_PER_HOUR = 6                    # guest codes made by presenters (all of them together) in any hour
MAX_DEVICES = 200                               # paired devices in all
FULL_RESERVED = 20                              # of those, places only a full-access device can take
GUEST_IDLE_DAYS = 7                             # a device that joined with a guest code goes when unused this long
SEEN_EVERY = 86400                              # its last use is saved at most this often (seconds)
CONTROLLER_KINDS = {"join": "live", "owner": "full"}    # a code shown from a controller: its kind -> the role it pairs
CONTROLLER_SECONDS = 120                        # it works for this long
CONTROLLER_PER_HOUR = 6                         # and this many are made in any hour, of both kinds together
PER_CLIENT_FAILS, PER_CLIENT_WINDOW = 5, 60.0
GLOBAL_FAILS, GLOBAL_WINDOW = 20, 600.0
LOCKOUT_SECONDS = 60.0
PIN_SHOWS, PIN_SHOW_WINDOW = 10, 300.0          # a full-access device may ask for the PIN this often (D77)


class AuthError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class JoinExists(AuthError):
    """A code for that role is active, and the caller did not say it may be replaced."""


class JoinLimit(AuthError):
    """Presenters have made as many guest codes this hour as they may. `retry_after` says when the next is possible."""


class TooManyDevices(AuthError):
    """The list of paired devices is full for this kind of device. Not a wrong guess."""


class ControllerOff(AuthError):
    """Codes from a controller are switched off on this box, or the full-access kind is."""


class NotPaired(AuthError):
    """The device that asked is no longer paired (removed while its request was on its way)."""


def controller_setting(section):
    """The `controller_code` section as the two switches it means, whatever a file holds: anything that is not
    exactly true is off, and the full-access kind counts only beside the first switch. Every reader of the section
    goes through this, so a stale `"owner": true` beside `"enabled": false` is off everywhere and stays off when
    the first switch is turned on."""
    c = section if isinstance(section, dict) else {}
    on = c.get("enabled") is True
    return {"enabled": on, "owner": on and c.get("owner") is True}


def generate_pin():
    return "%0*d" % (PIN_LENGTH, secrets.randbelow(10 ** PIN_LENGTH))


def _scrypt(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, settings, clock=time.monotonic, now=time.time, rotate_on_start=False):
        self.settings = settings
        self._clock = clock
        self._now = now
        self._fails = {}        # client -> [timestamps]
        self._global_fails = []
        self._locked_until = {}  # client or "*" -> time
        self.last_seen = {}
        self._pair_lock = threading.Lock()  # one PIN attempt at a time, so the counters are exact
        self._joins = {}        # code -> {"role", "expires" (monotonic), "uses"}
        self._presenter_made = []   # when (monotonic) presenters made guest codes, within the last hour
        self._controller = None     # the code shown from a controller: {"code", "kind", "expires" (monotonic), "shown" (wall)}
        self._controller_made = []  # when (monotonic) such codes were made, within the last hour
        self.controller_last = None  # the one before: {"kind", "shown", "ended" (wall), "how", "device"?}, for the panel
        self.current_pin = None     # the PIN in clear, known only to this run and only once made here (_new_pin); never saved
        self._pin_shows = {}        # device id -> when (monotonic) it was given the PIN, within PIN_SHOW_WINDOW
        self._prune_idle()
        with settings.lock:                 # at load: the section is written as what it means (controller_setting)
            if "controller_code" in settings.data and settings.data["controller_code"] != controller_setting(settings.data["controller_code"]):
                settings.data["controller_code"] = controller_setting(settings.data["controller_code"])
                settings.save()
        if rotate_on_start or not settings.data["auth"].get("pin_hash"):
            self._new_pin()

    # --- PIN -----------------------------------------------------------
    def _new_pin(self, pin=None):
        pin = pin or generate_pin()
        salt = secrets.token_bytes(16)
        with self.settings.lock:
            self.settings.data["auth"] = {"pin_hash": _scrypt(pin, salt).hex(), "pin_salt": salt.hex()}
            self.settings.save()
        self.current_pin = pin  # only known in memory until shown; not stored in clear
        return pin

    def set_pin(self, pin):
        if not (isinstance(pin, str) and pin.isdigit() and len(pin) == PIN_LENGTH):
            raise AuthError("PIN must be %d digits" % PIN_LENGTH)
        return self._new_pin(pin)

    def clear_lockout(self):
        """Lift a lockout someone provoked by guessing, without changing the PIN (guests are waiting at the door)."""
        with self._pair_lock:
            self._fails.clear()
            self._global_fails = []
            self._locked_until.clear()

    def rotate_pin(self):
        """New PIN. A paired owner uses this to get past a lockout someone else provoked."""
        with self._pair_lock, self.settings.lock:        # the order pair() takes them in
            self._fails.clear()
            self._global_fails = []
            self._locked_until.clear()
            return self._new_pin()

    def show_pin(self, device_id):
        """The PIN in clear for a paired full-access device (the API checks the role), or None when this run does
        not know it: a panel that started without making a new one has only the hash (D77). Counted per device
        whether it is known or not, so asking says nothing by its timing: after PIN_SHOWS in PIN_SHOW_WINDOW it
        raises AuthError with `retry_after`. The caller writes the journal line; this never logs the PIN."""
        with self._pair_lock:
            t = self._clock()
            shows = [x for x in self._pin_shows.get(device_id, []) if t - x < PIN_SHOW_WINDOW]
            if len(shows) >= PIN_SHOWS:
                raise AuthError("the PIN was shown %d times in the last %d minutes; wait a little"
                                % (PIN_SHOWS, int(PIN_SHOW_WINDOW // 60)), retry_after=int(PIN_SHOW_WINDOW - (t - shows[0])) + 1)
            self._pin_shows[device_id] = shows + [t]
            return self.current_pin

    def _check_pin(self, pin):
        auth = self.settings.data["auth"]
        expected = bytes.fromhex(auth["pin_hash"])
        given = _scrypt(pin if isinstance(pin, str) else "", bytes.fromhex(auth["pin_salt"]))
        return hmac.compare_digest(expected, given)

    # --- throttling ----------------------------------------------------
    def _locked(self, client):
        t = self._clock()
        for key in (client, "*"):
            until = self._locked_until.get(key, 0)
            if until > t:
                return until - t
        return 0

    def _record_fail(self, client):
        t = self._clock()
        fails = [x for x in self._fails.get(client, []) if t - x < PER_CLIENT_WINDOW] + [t]
        self._fails[client] = fails
        if len(fails) >= PER_CLIENT_FAILS:
            self._locked_until[client] = t + LOCKOUT_SECONDS
            self._fails[client] = []
        self._global_fails = [x for x in self._global_fails if t - x < GLOBAL_WINDOW] + [t]
        if len(self._global_fails) >= GLOBAL_FAILS:
            self._locked_until["*"] = t + LOCKOUT_SECONDS * 5
            self._global_fails = []

    # --- pairing and tokens --------------------------------------------
    def pair(self, pin, name, client="?"):
        """Pair with the box's PIN (full access) or a join code (guest or presenter). A wrong try counts against the
        same throttle either way."""
        with self._pair_lock:
            wait = self._locked(client)
            if wait:
                raise AuthError("too many attempts", retry_after=int(wait) + 1)
            given = pin if isinstance(pin, str) else ""
            if len(given) == JOIN_LENGTH and given.isascii() and given.isdigit():
                role = self._match_controller(given)     # the code shown from a controller, if this is it (D61)
                if role:          # as for a join code: no guess is counted, and the failure count is not reset
                    # The device first, the code second: if the device cannot be added (the list filled up this
                    # instant, the settings could not be saved) the code is still good and still on the display.
                    # Two requests with the same code cannot both get here: this runs under `_pair_lock`.
                    token, device = self._add_device(name, role, via="controller")
                    self._end_controller("used")
                    self.controller_last["device"] = device["name"]
                    return token, device
                role = self._use_join(given)      # TooManyDevices: the code is right, is not used up, and no guess is counted
                if role:          # the failure count is NOT reset: a real code must not buy more PIN guesses
                    return self._add_device(name, role, via="code")
                self._record_fail(client)
                raise AuthError("wrong or expired code")
            if not self._check_pin(pin):
                self._record_fail(client)
                raise AuthError("wrong PIN")
            self._fails.pop(client, None)
            return self._add_device(name, "full")

    # --- join codes ----------------------------------------------------
    def _prune_joins(self):
        t = self._clock()
        for code in [c for c, j in self._joins.items() if j["expires"] <= t or j["uses"] <= 0]:
            del self._joins[code]

    def _use_join(self, given):
        """The role of an active join code that matches `given` (and use it up once), else None. Compares every
        active code in constant time, so timing does not say which digits were right."""
        self._prune_joins()
        found = None
        for code, j in self._joins.items():
            if hmac.compare_digest(code, given):
                found = code
        if found is None:
            return None
        self._prune_idle()
        if not self._room_for(self._joins[found]["role"]):
            raise TooManyDevices(self.FULL_TEXT)
        self._joins[found]["uses"] -= 1
        role = self._joins[found]["role"]
        self._prune_joins()
        return role

    def create_join(self, role, minutes=JOIN_DEFAULT_MINUTES, uses=JOIN_DEFAULT_USES, by="owner", replace=True, check=None):
        """A new join code for `role` (view or live). Raises AuthError on bad input or too many codes. `by` says who
        made it ("owner": a full-access device; "presenter"), for the panel to show. With `replace` False an active
        code for the role is left alone and JoinExists is raised. A presenter's codes are counted: JoinLimit after
        PRESENTER_CODES_PER_HOUR in an hour. `check`: asked under the lock before anything is changed; if it says
        no (the asking device was removed meanwhile), NotPaired is raised and the active code stays."""
        if role not in JOIN_ROLES:
            raise AuthError("a join code is for view (guest) or live (presenter) access")
        if by not in JOIN_MAKERS:
            raise AuthError("unknown maker")
        for name, v, lo, hi in (("minutes", minutes, JOIN_MIN_MINUTES, JOIN_MAX_MINUTES), ("uses", uses, 1, JOIN_MAX_USES)):
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise AuthError("%s must be a whole number from %d to %d" % (name, lo, hi))
        with self._pair_lock:
            self._prune_joins()
            same = [c for c, j in self._joins.items() if j["role"] == role]
            if same and not replace:
                raise JoinExists("a code for that access is already active")
            if check is not None and not check():
                raise NotPaired("this device is no longer paired")
            if by == "presenter":
                t = self._clock()
                self._presenter_made = [x for x in self._presenter_made if t - x < 3600.0]
                if len(self._presenter_made) >= PRESENTER_CODES_PER_HOUR:
                    wait = int(3600.0 - (t - self._presenter_made[0])) + 1
                    raise JoinLimit("%d guest codes were made in the last hour, which is the most a presenter may; "
                                    "use the code that is active, wait %d minutes, or ask the owner"
                                    % (PRESENTER_CODES_PER_HOUR, -(-wait // 60)), retry_after=wait)
            for c in same:                      # one live code per role: a new one replaces the old
                del self._joins[c]
            if len(self._joins) >= MAX_JOINS:
                raise AuthError("too many join codes; cancel one first")
            while True:
                code = "%0*d" % (JOIN_LENGTH, secrets.randbelow(10 ** JOIN_LENGTH))
                if code not in self._joins and not (self._controller and self._controller["code"] == code):
                    break
            self._joins[code] = {"role": role, "expires": self._clock() + minutes * 60, "uses": uses, "by": by}
            if by == "presenter":
                self._presenter_made.append(self._clock())
            return code

    def list_joins(self):
        with self._pair_lock:
            self._prune_joins()
            t = self._clock()
            return [{"code": c, "role": j["role"], "seconds_left": max(0, int(j["expires"] - t)), "uses_left": j["uses"], "by": j["by"]}
                    for c, j in sorted(self._joins.items(), key=lambda kv: kv[1]["role"])]

    def cancel_join(self, code=None):
        """Cancel one code (or all if `code` is None). True if something was cancelled."""
        with self._pair_lock:
            if code is None:
                changed = bool(self._joins)
                self._joins.clear()
                return changed
            return self._joins.pop(code, None) is not None

    def cancel_join_role(self, role):
        """Cancel the code for `role`, whatever its digits are. True if there was one."""
        with self._pair_lock:
            self._prune_joins()
            found = [c for c, j in self._joins.items() if j["role"] == role]
            for c in found:
                del self._joins[c]
            return bool(found)

    # --- the code shown from a controller (D61) -------------------------
    def _controller_cfg(self):
        """(codes from a controller are on, the full-access kind is allowed), from the settings. Anything that is
        not exactly true counts as off: the file is one a person may have edited."""
        c = controller_setting(self.settings.data.get("controller_code"))
        return c["enabled"], c["owner"]

    def _end_controller(self, how):
        """Forget the active code and keep what became of it. Call with `_pair_lock` held."""
        c, self._controller = self._controller, None
        if c is not None:
            self.controller_last = {"kind": c["kind"], "shown": c["shown"], "ended": int(self._now()), "how": how}
        return c is not None

    def _prune_controller(self):
        """Call with `_pair_lock` held. A code that ran out goes, and so does one whose switch was turned off."""
        c = self._controller
        if c is None:
            return
        on, owner = self._controller_cfg()
        if c["expires"] <= self._clock():
            self._end_controller("expired")
        elif not on or (c["kind"] == "owner" and not owner):
            self._end_controller("switched off")

    def _match_controller(self, given):
        """The role of the controller code if `given` is it, else None. It is NOT used up here: pair() ends it once
        the device is in the list. Call with `_pair_lock` held. The comparison is made even when no code is
        active, against digits nobody can type, so timing does not say whether one is."""
        self._prune_controller()
        c = self._controller
        if not hmac.compare_digest(c["code"] if c else "x" * JOIN_LENGTH, given) or c is None:
            return None
        role = CONTROLLER_KINDS[c["kind"]]
        self._prune_idle()
        if not self._room_for(role, "controller"):
            raise TooManyDevices(self.FULL_TEXT)
        return role

    def pairing_locked(self):
        """Seconds for which nobody at all can pair (the lockout after GLOBAL_FAILS wrong guesses), else 0. For the
        display: a code shown from a controller is no use while this lasts. Reads only."""
        return max(0, int(self._locked_until.get("*", 0) - self._clock()))

    def create_controller_code(self, kind):
        """A new code of `kind` ("join" or "owner") in the place of any that is active. Only the controller code
        manager calls this (controllercode.py), for a hold on a MIDI controller; no API route does. Raises
        ControllerOff when the setting (or the owner kind's) is off, JoinLimit after CONTROLLER_PER_HOUR in an hour,
        TooManyDevices when a device of that role could not be added anyway. Returns nothing: the digits are read
        by the display alone (controller_digits)."""
        if kind not in CONTROLLER_KINDS:
            raise AuthError("a controller code is a join code or an owner code")
        with self._pair_lock:
            self._prune_controller()
            on, owner = self._controller_cfg()
            if not on or (kind == "owner" and not owner):
                raise ControllerOff("codes from a controller are switched off" if not on else
                                    "full access codes from a controller are switched off")
            t = self._clock()
            self._controller_made = [x for x in self._controller_made if t - x < 3600.0]
            if len(self._controller_made) >= CONTROLLER_PER_HOUR:
                raise JoinLimit("too many codes were shown from a controller in the last hour",
                                retry_after=int(3600.0 - (t - self._controller_made[0])) + 1)
            if not self._room_for(CONTROLLER_KINDS[kind], "controller"):
                raise TooManyDevices(self.FULL_TEXT)
            self._end_controller("replaced")
            while True:
                code = "%0*d" % (JOIN_LENGTH, secrets.randbelow(10 ** JOIN_LENGTH))
                if code not in self._joins:
                    break
            self._controller = {"code": code, "kind": kind, "expires": t + CONTROLLER_SECONDS, "shown": int(self._now())}
            self._controller_made.append(t)

    def controller_digits(self):
        """(kind, digits, seconds left) of the active controller code, or None. For the box's own display only:
        nothing that answers a device may call this."""
        with self._pair_lock:
            self._prune_controller()
            c = self._controller
            return (c["kind"], c["code"], max(0, int(c["expires"] - self._clock()))) if c else None

    def controller_status(self):
        """What a full-access device may know: whether a code is on the display, its kind, when it was shown and
        how long it still works, and what became of the one before. Never the digits."""
        with self._pair_lock:
            self._prune_controller()
            c, t, now = self._controller, self._clock(), int(self._now())
            made = [x for x in self._controller_made if t - x < 3600.0]
            last = dict(self.controller_last) if self.controller_last else None
            if last:                                # how long ago, by the box's own clock (a phone's may differ)
                last["ended_ago"] = max(0, now - last["ended"])
            return {"active": c is not None, "kind": c["kind"] if c else None, "shown": c["shown"] if c else None,
                    "shown_ago": max(0, now - c["shown"]) if c else None,
                    "seconds_left": max(0, int(c["expires"] - t)) if c else 0, "last": last,
                    "made_this_hour": len(made), "per_hour": CONTROLLER_PER_HOUR}

    def cancel_controller_code(self, how="cancelled"):
        """End the active controller code. True if there was one."""
        with self._pair_lock:
            self._prune_controller()
            return self._end_controller(how)

    def invite(self, name, role):
        if role not in ("view", "live"):
            raise AuthError("invites are for view or live access")
        return self._add_device(name, role)

    FULL_TEXT = ("too many devices are paired with this box; the owner removes some under System, People and codes, "
                 "and then this works")

    def _room_for(self, role, via=None):
        """Is there a place for one more device of `role`? Guests and presenters share MAX_DEVICES - FULL_RESERVED
        places; the rest can only be taken by full-access devices, so the PIN always pairs while fewer than
        FULL_RESERVED full-access devices exist, however many guests there are. Nothing is ever evicted to make room.
        A full-access device paired with a code from a controller (`via` "controller") is counted with the guests
        and presenters, when it is added and ever after: the reserved places are for the PIN (and what else a
        full-access device at the box makes), and they hold in whatever order devices were paired."""
        devices = self.settings.data["devices"]
        if role == "full" and via != "controller":
            return len(devices) < MAX_DEVICES
        shared = sum(1 for d in devices if d["role"] != "full" or d.get("via") == "controller")
        if role == "full" and len(devices) >= MAX_DEVICES:     # (via a controller) the list as a whole is full too
            return False
        return shared < MAX_DEVICES - FULL_RESERVED

    @staticmethod
    def _expires(device):
        """Does this device go when it is not used for GUEST_IDLE_DAYS: a guest that joined with a join code, and a
        presenter that joined with a code shown from a controller (anyone at the controller can make those, so
        they must not pile up). A presenter from a join code or a link, and every full-access device, stays."""
        return ((device.get("via") == "code" and device["role"] == "view")
                or (device.get("via") == "controller" and device["role"] == "live"))

    def _idle(self, device, now):
        return self._expires(device) and now - device.get("seen", device["created"]) > GUEST_IDLE_DAYS * 86400

    def _prune_idle(self):
        """Drop the devices that joined with a guest code and were not used for GUEST_IDLE_DAYS. Run at start and
        whenever someone joins; such a device is also refused, and dropped, the moment it comes back."""
        now = int(self._now())
        with self.settings.lock:
            keep = [d for d in self.settings.data["devices"] if not self._idle(d, now)]
            if len(keep) != len(self.settings.data["devices"]):
                self.settings.data["devices"] = keep
                self.settings.save()

    def _add_device(self, name, role, via=None):
        """`via`: "code" for a device that joined with a join code (kept in its record; a guest that joined so is
        the only kind that expires)."""
        token = secrets.token_urlsafe(24)
        device = {"id": secrets.token_hex(4), "name": str(name or "device")[:40], "role": role,
                  "token_hash": _token_hash(token), "created": int(self._now())}
        if via:
            device["via"] = via
        with self.settings.lock:
            if not self._room_for(role, via):
                raise TooManyDevices(self.FULL_TEXT)
            self.settings.data["devices"].append(device)
            try:
                self.settings.save()
            except OSError:                 # not saved, so not paired: memory and disk must not disagree
                self.settings.data["devices"] = [d for d in self.settings.data["devices"] if d is not device]
                raise
        return token, self._public(device)

    def authenticate(self, token):
        if not isinstance(token, str) or not token:
            return None
        h = _token_hash(token)
        found = None
        for d in list(self.settings.data["devices"]):
            if hmac.compare_digest(d["token_hash"], h):
                found = d
        if found:
            now = int(self._now())
            if self._idle(found, now):                 # a guest code's device, unused for too long: gone
                self.revoke(found["id"])
                return None
            self.last_seen[found["id"]] = now
            if self._expires(found) and now - found.get("seen", found["created"]) >= SEEN_EVERY:
                with self.settings.lock:               # written down once a day at most, so its idle time survives a restart
                    found["seen"] = now
                    self.settings.save()
            return self._public(found)
        return None

    def revoke(self, device_id):
        with self.settings.lock:
            before = len(self.settings.data["devices"])
            self.settings.data["devices"] = [d for d in self.settings.data["devices"] if d["id"] != device_id]
            changed = len(self.settings.data["devices"]) != before
            if changed:
                self.settings.save()
            return changed

    def revoke_all(self):
        with self.settings.lock:
            self.settings.data["devices"] = []
            self.settings.save()

    def list_devices(self):
        return [dict(self._public(d), last_seen=self.last_seen.get(d["id"])) for d in self.settings.data["devices"]]

    @staticmethod
    def _public(device):
        return {k: device[k] for k in ("id", "name", "role", "created")}

    @staticmethod
    def allows(device, needed):
        return bool(device) and ROLES[device["role"]] >= ROLES[needed]
