# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Show the pairing PIN and the panel's address on the screen, until the first device has paired, and on request
afterwards: a full-access device can put the full PIN and/or a guest code and a presenter code on the display for a
while (System > Access), for people who need to join.

The panel and the manual say the PIN is on the projector; until this existed it was only in a file in RAM, so a
box with no keyboard could not be paired. It is drawn by mpv on its own idle screen (its on-screen text), and:

* the automatic first-run screen (PIN plus a QR code with it): only while NO device is paired and nothing is playing,
  so it never draws over a show. If every device is later removed it comes back, which is the way back in for a box
  with no keyboard;
* on request (System > Access): the chosen items, for a chosen time, even over a playing clip; the full PIN is then
  shown as text only, never as a QR code;
* only characters from a short safe set, because mpv would expand `${...}` in the text.

A third thing can be on the display: the one-time code a person asked for by holding a control on a MIDI controller
(D61, controllercode.py). It is not a request kept here: each tick asks `auth` whether such a code is active and
draws it if so, in the place of anything else, so it leaves the display the moment the code is used, cancelled or
run out. Its presenter kind has a QR code; its full-access kind is text only, like the PIN.

One lock serialises show, hide and each tick, and a tick reads the state once, so a Hide can never be followed by a
stale draw. Snapshots for devices without full access leave the on-screen text out while any of this is shown.
"""

import re
import socket
import threading

from . import qr
from .player import PlayerError

SAFE = re.compile(r"[^A-Za-z0-9 .:/_\-]")
SHOW_MS = 3500        # each draw lasts this long (a little longer than the 3 s tick, so it is steady); a clip clears it at once


def clean(text):
    return SAFE.sub("", text)


def lines(pin, hostname, addresses):
    out = ["nxlx.mastercontrol", "Open on your phone:"]
    if hostname:
        out.append("http://%s.local/" % clean(hostname))
    out += ["http://%s/" % clean(a) for a in addresses[:2]]
    out.append("PIN  %s" % clean(str(pin)))
    return out


QR_IDS = {"view": 1, "live": 2, "pin": 3, "controller": 4}       # overlay ids on the player
QR_REFRESH = 15.0
QR_MAX_SCALE = 16                                # pixels per module; bounds the bitmap whatever size the player reports                                # a player that restarted has lost its overlays: draw them again after this long
MANUAL_ITEMS = ("pin", "view", "live", "address")    # "address": only where to open the panel
MANUAL_MIN_SECONDS, MANUAL_MAX_SECONDS, MANUAL_DEFAULT_SECONDS = 10, 3600, 60
LABELS = {"pin": "Full access PIN", "view": "Guest, watch only, code", "live": "Presenter, play and mix, code"}
CONTROLLER_LABELS = {"join": "One-time presenter code", "owner": "One-time full access code"}
NOTICE_SECONDS = 8            # a line that says why no code came (too many this hour) stays this long
NOTICES = {"limit": "No more codes from a controller for now. Try again in %d minutes.",
           "owner off": "Full access codes from a controller are switched off on this box.",
           "full": "Too many devices are paired with this box. No code was made.",
           "locked": "Pairing is locked for %d minutes after wrong guesses. No code was made."}
LOCKED_LINE = "Pairing is locked for %d minutes after wrong guesses"     # under a code that is showing


class Busy(Exception):
    """The display shows something this caller may not replace."""


class PinScreen:
    def __init__(self, api, auth, log=print, interval=3.0, hostname=None, clock=None):
        import time
        self.api, self.auth, self.log = api, auth, log
        self._clock = clock or time.monotonic
        self.manual = None       # {"until": t, "items": [...]} while shown on request
        self._controller_up = False     # a controller code (or a notice about one) was drawn at the last tick
        self._notice = None      # (until, text): why a hold on a controller gave no code, for a few seconds
        self._lock = threading.RLock()
        self._qr_sig = None      # what the QR overlays currently show (so they are only redrawn when it changes)
        self._qr_stale = False   # a QR code could not be taken off (the player did not answer): tried again each tick
        self._qr_at = 0.0
        self.interval = interval
        self.hostname = hostname if hostname is not None else socket.gethostname()
        self._stop = threading.Event()
        self._thread = None
        self.shown = 0

    def addresses(self):
        out = []
        try:
            for entry in self.api._ip_json():
                if entry.get("ifname") == "lo":
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("local"):
                        out.append(a["local"])
        except Exception:
            pass
        return out

    def show(self, items, seconds=MANUAL_DEFAULT_SECONDS, by="owner", only=None, made=None, check=None):
        """Put access details on the display for `seconds`, even over a playing clip (it is an explicit request).
        `items` is any of "pin" (full access), "view" (guest code), "live" (presenter code); a code that does not
        exist yet is made (`by` says by whom). Returns the status. Raises ValueError for bad input.
        `only` (a presenter's request): the items this caller may put up or take down. Checked here, under the same
        lock as the change, so nothing a full-access device put up meanwhile (the PIN, the presenter code) is ever
        replaced or kept up longer by it: PermissionError for an item outside `only`, Busy when such an item (other
        than the plain address, which is no secret) is on the display now.
        A code made here for a presenter keeps to the presenter's limits: it lasts the shortest of
        auth.PRESENTER_JOIN_MINUTES that covers the show, has the presenter's number of uses, never replaces a code,
        and counts towards the presenter's codes per hour. `made`: a list that gets the codes this call made.
        `check`: asked under the lock before anything is changed; auth.NotPaired if it says no."""
        from . import auth as auth_mod
        if (not isinstance(items, list) or not items or len(items) > len(MANUAL_ITEMS) or len(set(items)) != len(items)
                or not all(i in MANUAL_ITEMS for i in items)):
            raise ValueError("choose what to show: pin, view, live and/or address")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not MANUAL_MIN_SECONDS <= seconds <= MANUAL_MAX_SECONDS:
            raise ValueError("seconds must be %d to %d" % (MANUAL_MIN_SECONDS, MANUAL_MAX_SECONDS))
        if only is not None and not all(i in only for i in items):
            raise PermissionError("this device may show the guest code only")
        with self._lock:
            m = self._current()
            if only is not None and m and not all(i in only or i == "address" for i in m["items"]):      # the address is no secret: it may be replaced
                raise Busy("something else is on the room screen; a full-access device takes it off")
            if check is not None and not check():
                raise auth_mod.NotPaired("this device is no longer paired")
            have = {j["role"] for j in self.auth.list_joins()}
            for role in items:
                if role in ("view", "live") and role not in have:
                    minutes = min(120, max(15, -(-seconds // 60)))
                    if by == "presenter":
                        minutes = min(m for m in auth_mod.PRESENTER_JOIN_MINUTES if m >= minutes)
                        try:
                            code = self.auth.create_join(role, minutes=minutes, uses=auth_mod.PRESENTER_JOIN_MAX_USES, by=by, replace=False)
                        except auth_mod.JoinExists:       # made by someone else this instant: that one is shown
                            continue
                    else:
                        code = self.auth.create_join(role, minutes=minutes, by=by)
                    if made is not None:
                        made.append(code)
            self.manual = {"until": self._clock() + seconds, "items": list(items)}
            self.tick()
            return self.status()

    def hide(self, only=None):
        """Take the access details off the display. `only` (a presenter's request): just these items go; what a
        full-access device put up besides them stays, for the time it was given."""
        with self._lock:
            m = self._current()
            rest = [i for i in m["items"] if i not in only] if m and only is not None else []
            if rest:
                self.manual = {"until": m["until"], "items": rest}
                self._qr_sig = None           # the QR codes are drawn again without the one that went
                self.tick()
                return self.status()
            self.manual = None
            self.clear()
            return self.status()

    def _current(self):
        """The request being shown, or None (an expired one counts as None). Reads only; tick() does the clearing."""
        m = self.manual
        return m if m and self._clock() < m["until"] else None

    def status(self):
        m = self._current()
        return {"showing": bool(m), "items": list(m["items"]) if m else [],
                "seconds_left": max(0, int(m["until"] - self._clock())) if m else 0}

    def manual_lines(self, m):
        joins = {j["role"]: j["code"] for j in self.auth.list_joins()}
        addr = ["http://%s.local/" % clean(self.hostname)] if self.hostname else []
        addr += ["http://%s/" % clean(a) for a in self.addresses()[:1]]
        out = ["nxlx.mastercontrol", "Open " + "  or  ".join(addr) if addr else "Open the panel in a browser"]
        for item in m["items"]:
            if item == "address":                       # the address is the line above; nothing secret to add
                continue
            value = clean(str(self.auth.current_pin)) if item == "pin" else clean(joins.get(item, "----"))
            out.append("%s  %s" % (LABELS[item], value))
        qrs = [r for r in ("view", "live") if r in m["items"]]
        if len(qrs) == 2:
            out.append("Scan: guest QR on the left, presenter QR on the right")
        elif qrs:
            out.append("Scan the QR code to join as %s" % ("a guest" if qrs[0] == "view" else "a presenter"))
        out.append("Hides in %d s" % max(0, int(m["until"] - self._clock())))
        return out

    # --- the code asked for from a MIDI controller (D61) ----------------------------------------------
    def _controller(self):
        """(kind, digits, seconds left) of the active controller code, or None."""
        ask = getattr(self.auth, "controller_digits", None)
        return ask() if ask else None

    def controller_up(self):
        """True while a controller code is active, so on the display. Snapshots for devices without full access
        leave the on-screen text out then (api.access_on_screen)."""
        return self._controller() is not None

    def controller_changed(self):
        """A controller code was made, used or ended: draw or clear now, not at the next tick. True if drawn."""
        with self._lock:
            return self.tick()

    def controller_notice(self, why, minutes=0):
        """Say on the display, for a few seconds, why a hold on a controller gave no code. Fixed words only."""
        text = NOTICES[why]
        with self._lock:
            self._notice = (self._clock() + NOTICE_SECONDS, text % minutes if "%d" in text else text)
            self.tick()

    def controller_lines(self, c):
        kind, digits, left = c
        addr = ["http://%s.local/" % clean(self.hostname)] if self.hostname else []
        addr += ["http://%s/" % clean(a) for a in self.addresses()[:1]]
        out = ["nxlx.mastercontrol", "Open " + "  or  ".join(addr) if addr else "Open the panel in a browser",
               "%s  %s" % (CONTROLLER_LABELS[kind], clean(digits)),
               "Scan the QR code or type it in the 6 digit code field" if kind == "join" else "Type it in the 6 digit code field",
               "Works once. Hides in %d s. Press the control again to hide it now" % left]
        locked = getattr(self.auth, "pairing_locked", lambda: 0)()
        if locked:                                  # wrong guesses locked pairing: the code will be refused until then
            out.append(LOCKED_LINE % -(-locked // 60))
        return out

    # --- the PIN for a recovery stick (D81) ----------------------------------------------------------------
    def _recovery(self):
        """{"pin", "seconds_left", ...} while the PIN is on the screen for a stick, else None. The poll that notices
        a stick lives here too, so it runs as often as the display is drawn."""
        r = getattr(self.api, "recovery", None)
        if r is None:
            return None
        try:
            r.poll()
        except Exception as e:                      # a drive that vanished mid-scan must not stop the display
            self.log("pvj-web: recovery stick check: %s" % e)
        return r.active()

    def recovery_changed(self):
        """The PIN for a stick had its use: off the display now, not at the next tick."""
        with self._lock:
            return self.tick()

    def recovery_lines(self, active):
        addr = ["http://%s.local/" % clean(self.hostname)] if self.hostname else []
        addr += ["http://%s/" % clean(a) for a in self.addresses()[:1]]
        out = [clean(line) for line in self.api.recovery.lines(active, addr)]
        locked = getattr(self.auth, "pairing_locked", lambda: 0)()
        if locked:
            out.append(LOCKED_LINE % -(-locked // 60))
        return out

    def auto_wanted(self):
        """True when the first-run screen (PIN and its QR code) should be up: no device paired, player idle."""
        if self.auth.list_devices():
            return False
        try:
            status = self.api.player.status()
        except PlayerError:
            return False
        return bool(status.get("running")) and not status.get("path")

    def wanted(self):
        """True when anything should be on the screen right now."""
        return bool(self._current()) or self.auto_wanted()

    def base_url(self):
        """Where a phone should go: the first real IPv4 address (works on every phone), else the .local name."""
        ips = self.addresses()
        if ips:
            return "http://%s/" % clean(ips[0])
        return "http://%s.local/" % clean(self.hostname) if self.hostname else None

    def _qr_targets(self, m, codes):
        """[(overlay id, url)] for what is being shown. `m` is the request being shown (read once by the caller) or
        None for the first-run screen. Guest and presenter links carry their code in the address fragment; only the
        first-run screen, with no device paired, carries the PIN."""
        base = self.base_url()
        if not base:
            return []
        if m:
            return [(QR_IDS[r], "%s#code=%s" % (base, clean(codes[r]))) for r in ("view", "live") if r in m["items"] and r in codes]
        if self.auth.list_devices():
            return []
        return [(QR_IDS["pin"], "%s#pin=%s" % (base, clean(str(self.auth.current_pin))))]

    def draw_qr(self, targets):
        """Show the QR codes for `targets` in the top right corner, side by side; only when they have changed."""
        sig = tuple(targets)
        now = self._clock()
        if sig == self._qr_sig and now - self._qr_at < QR_REFRESH:
            return
        player = self.api.player
        if not hasattr(player, "overlay"):          # a player that cannot draw pictures: the text is enough
            return
        try:
            size = player.osd_size()
        except PlayerError:
            size = None
        if size is None:
            return
        width, height = size
        try:
            for oid in QR_IDS.values():
                if oid not in [t[0] for t in targets]:
                    player.overlay_remove(oid)
            x = width - 40
            for oid, url in sorted(targets, key=lambda t: -t[0]):
                matrix = qr.encode(url)
                scale = min(QR_MAX_SCALE, max(2, int(0.30 * min(height, 4320)) // (len(matrix) + 8)))
                w, h, pixels = qr.bgra(matrix, scale)
                x -= w
                player.overlay(oid, x, 40, w, h, pixels)
                x -= 20
            self._qr_sig, self._qr_at, self._qr_stale = sig, now, False
        except (PlayerError, qr.QrError, OSError):
            pass

    def clear_qr(self):
        """Take every QR code off. If the player does not answer, that is remembered (`_qr_stale`) and each tick
        tries again until it does: a QR code is an access code, and must not come back into view with the player."""
        self._qr_sig = None
        if not hasattr(self.api.player, "overlay_remove"):
            self._qr_stale = False
            return
        failed = False
        for oid in QR_IDS.values():                 # every one is tried: each call also removes that code's file
            try:
                self.api.player.overlay_remove(oid)
            except PlayerError:
                failed = True
        self._qr_stale = failed

    def clear(self):
        """Take the text and the QR codes off the screen now (a clip is starting). Best effort."""
        try:
            self.api.player.ipc.request("show-text", "", 1)
        except PlayerError:
            pass
        self.clear_qr()

    def tick(self):
        with self._lock:
            m = self._current()
            if self.manual is not None and m is None:        # a request just ran out: take it off the screen
                self.manual = None
                self.clear()
            rec = self._recovery()
            if rec:                                          # the PIN for a recovery stick: before anything else (D81)
                self.draw_qr([])
                self._controller_up = True                   # so that it is cleared the moment it ends, like a controller code
                try:
                    self.api.player.ipc.request("show-text", "\n".join(self.recovery_lines(rec)), SHOW_MS)
                except PlayerError:
                    return False
                self.shown += 1
                return True
            c = self._controller()
            notice = self._notice[1] if self._notice and self._clock() < self._notice[0] else None
            if c or notice:                                  # a code from a controller, before anything else
                if c:
                    self._notice, text = None, "\n".join(self.controller_lines(c))
                else:
                    text = "nxlx.mastercontrol\n" + notice
                base = self.base_url()
                self.draw_qr([(QR_IDS["controller"], "%s#code=%s" % (base, clean(c[1])))] if c and c[0] == "join" and base else [])
                self._controller_up = True
                try:
                    self.api.player.ipc.request("show-text", text, SHOW_MS)
                except PlayerError:
                    return False
                self.shown += 1
                return True
            if self._controller_up:                          # it was used, cancelled or ran out: off the screen at once
                self._controller_up, self._notice = False, None
                self.clear()
            if not (m or self.auto_wanted()):
                if self._qr_sig is not None or self._qr_stale:     # e.g. the first device just paired; or the player did not answer last time
                    self.clear_qr()
                return False
            if m:
                text = "\n".join(self.manual_lines(m))
                self.draw_qr(self._qr_targets(m, {j["role"]: j["code"] for j in self.auth.list_joins()}))
            else:
                text = "\n".join(lines(self.auth.current_pin, self.hostname, self.addresses()))
                self.draw_qr(self._qr_targets(None, {}))
            try:
                self.api.player.ipc.request("show-text", text, SHOW_MS)
            except PlayerError:
                return False
            self.shown += 1
            return True

    def start(self):
        if self._thread:
            return
        self.clear_qr()           # codes drawn by an earlier run of the service (it crashed or restarted) come off
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as e:
                    self.log("pvj-web: pin screen error: %s" % e)
        self._thread = threading.Thread(target=loop, name="pinscreen", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            self.manual = None
            self._notice = None
            self.clear()
