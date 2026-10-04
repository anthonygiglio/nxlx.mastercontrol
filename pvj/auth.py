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
  nothing else (D47; the API decides what a role may ask for, this module keeps the limits and who made a code).
* The PIN is stored as a salted scrypt hash. Because it is short, guessing is
  throttled per client and globally, and comparisons are constant-time.
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
PER_CLIENT_FAILS, PER_CLIENT_WINDOW = 5, 60.0
GLOBAL_FAILS, GLOBAL_WINDOW = 20, 600.0
LOCKOUT_SECONDS = 60.0


class AuthError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class JoinExists(AuthError):
    """A code for that role is active, and the caller did not say it may be replaced."""


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
                role = self._use_join(given)
                if role:          # the failure count is NOT reset: a real code must not buy more PIN guesses
                    return self._add_device(name, role)
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
        self._joins[found]["uses"] -= 1
        role = self._joins[found]["role"]
        self._prune_joins()
        return role

    def create_join(self, role, minutes=JOIN_DEFAULT_MINUTES, uses=JOIN_DEFAULT_USES, by="owner", replace=True):
        """A new join code for `role` (view or live). Raises AuthError on bad input or too many codes. `by` says who
        made it ("owner": a full-access device; "presenter"), for the panel to show. With `replace` False an active
        code for the role is left alone and JoinExists is raised."""
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
            for c in same:                      # one live code per role: a new one replaces the old
                del self._joins[c]
            if len(self._joins) >= MAX_JOINS:
                raise AuthError("too many join codes; cancel one first")
            while True:
                code = "%0*d" % (JOIN_LENGTH, secrets.randbelow(10 ** JOIN_LENGTH))
                if code not in self._joins:
                    break
            self._joins[code] = {"role": role, "expires": self._clock() + minutes * 60, "uses": uses, "by": by}
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

    def invite(self, name, role):
        if role not in ("view", "live"):
            raise AuthError("invites are for view or live access")
        return self._add_device(name, role)

    def _add_device(self, name, role):
        token = secrets.token_urlsafe(24)
        device = {"id": secrets.token_hex(4), "name": str(name or "device")[:40], "role": role,
                  "token_hash": _token_hash(token), "created": int(self._now())}
        with self.settings.lock:
            self.settings.data["devices"].append(device)
            self.settings.save()
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
            self.last_seen[found["id"]] = int(self._now())
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
