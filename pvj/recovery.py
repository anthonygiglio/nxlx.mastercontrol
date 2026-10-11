# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The recovery stick (D81): a USB stick with an empty file named `pvj-recover` (or `pvj-recover.txt`) at its top
makes the box draw a FRESH owner PIN on its own screen for STICK_SECONDS, for an owner who lost every device and
stands at the box. It proves presence at the box and needs no keyboard, no network and no paired device.

* The drive is mounted by the existing mounter (pvj/usb.py: /media/pvj/<label>, read-only, noexec). Only the name
  and the kind of the file are looked at (lstat: a regular file, never a symlink or a folder); it is never opened.
* poll() is asked by the PIN screen's tick (every 3 s). It fires on the change from absent to present: once per
  insertion, and once at boot when a stick is in. At most STICK_PER_HOUR an hour; while the switch (`recovery.usb`,
  on unless the owner turned it off) is off, nothing is shown and the insertion is logged.
* The PIN is a new one (auth.rotate_pin, which also lifts any lockout: presence at the box outranks a stranger on
  the network). It ends at its first use, at STICK_SECONDS, when the stick is pulled or when the switch goes off;
  at the end the PIN is rotated again, so the digits that were on the room's projector are dead.
* The journal gets a line at the start and at the end (never the PIN); owners see the event on People and codes.
"""

import os
import stat
import threading
import time

NAMES = ("pvj-recover", "pvj-recover.txt")   # compared case-blind: a vfat or exfat stick may upper-case a short name
STICK_SECONDS = 120
STICK_PER_HOUR = 6
SCAN_LIMIT = 2000           # entries looked at per drive (a hostile drive can hold millions)
DRIVES_LIMIT = 16


def stick_present(root):
    """The label of a mounted drive whose top level holds the file, else None. Reads names only."""
    try:
        labels = sorted(os.listdir(root))[:DRIVES_LIMIT]
    except OSError:
        return None
    for label in labels:
        base = os.path.join(root, label)
        if label.startswith(".") or os.path.islink(base) or not os.path.isdir(base):
            continue
        try:
            with os.scandir(base) as it:
                for n, entry in enumerate(it):
                    if n >= SCAN_LIMIT:
                        break
                    if entry.name.lower() not in NAMES:
                        continue
                    try:
                        st = os.lstat(entry.path)       # the entry itself: a symlink to a file is not a file
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        return label
        except OSError:
            continue
    return None


class Recovery:
    def __init__(self, api, auth, log=print, clock=time.monotonic, now=time.time):
        self.api, self.auth, self.log = api, auth, log
        self._clock, self._now = clock, now
        self._lock = threading.Lock()
        self._present = None        # the label seen at the last poll, or None
        self._active = None         # {"until", "shown", "label"} while the PIN is on the screen for a stick
        self._made = []             # when (monotonic) the PIN was shown for a stick, within the last hour
        self.last = None            # what became of the one before: {"shown", "ended", "how"}

    # --- state --------------------------------------------------------------------------------------------
    def active(self):
        """{"pin", "seconds_left", "shown"} while the PIN is shown for a stick, else None. For the box's own display
        and for pair(): nothing that answers a device without full access may pass the PIN on."""
        with self._lock:
            a = self._active
            if a is None:
                return None
            return {"pin": self.auth.current_pin, "seconds_left": max(0, int(a["until"] - self._clock())), "shown": a["shown"]}

    def status(self):
        """What an owner may know: whether it is on the screen now and since when, the last one, and how many this
        hour. Never the PIN."""
        with self._lock:
            a, t = self._active, self._clock()
            made = [x for x in self._made if t - x < 3600.0]
            return {"active": a is not None, "shown": a["shown"] if a else None,
                    "seconds_left": max(0, int(a["until"] - t)) if a else 0, "last": dict(self.last) if self.last else None,
                    "made_this_hour": len(made), "per_hour": STICK_PER_HOUR, "seconds": STICK_SECONDS}

    # --- the tick -----------------------------------------------------------------------------------------
    def poll(self):
        """Look at the drives once. Returns True when the display should be drawn to now (something changed)."""
        label = stick_present(self.api.usb_root)
        changed = False
        with self._lock:
            arrived = label is not None and self._present is None
            self._present = label
            a = self._active
            if a is not None:
                if label is None:
                    changed = self._end("removed")
                elif a["until"] <= self._clock():
                    changed = self._end("expired")
                elif not self.auth.usb_enabled():
                    changed = self._end("switched off")
            if arrived and self._active is None:
                changed = self._start(label) or changed
        return changed

    def _start(self, label):
        """Call with the lock held. A fresh PIN on the screen, or a journal line saying why not."""
        if not self.auth.usb_enabled():
            self.log("pvj-web: a recovery stick was put in (%s) but the stick way is switched off; nothing shown" % label)
            return False
        t = self._clock()
        self._made = [x for x in self._made if t - x < 3600.0]
        if len(self._made) >= STICK_PER_HOUR:
            self.log("pvj-web: a recovery stick was put in (%s) but the PIN was shown %d times this hour; nothing shown"
                     % (label, STICK_PER_HOUR))
            return False
        try:
            self.auth.rotate_pin()                       # new digits, and any lockout lifted
        except OSError as e:
            self.log("pvj-web: a recovery stick was put in but a new PIN could not be saved: %s" % e)
            return False
        shown = int(self._now())
        self._active = {"until": t + STICK_SECONDS, "shown": shown, "label": label}
        self._made.append(t)
        try:
            self.auth.recovery_event("stick", label=label)
        except OSError:
            pass
        self.log("pvj-web: the owner PIN is on the box's screen for %d s: a recovery stick was put in (%s)" % (STICK_SECONDS, label))
        return True

    def _end(self, how):
        """Call with the lock held. The PIN that was on the wall is replaced, whatever ended it."""
        a, self._active = self._active, None
        if a is None:
            return False
        self.last = {"shown": a["shown"], "ended": int(self._now()), "how": how}
        try:
            self.auth.rotate_pin()
        except OSError as e:
            self.log("pvj-web: the PIN shown for a recovery stick could not be replaced: %s" % e)
        self.log("pvj-web: the PIN shown for a recovery stick is off the screen (%s) and replaced" % how)
        return True

    def used(self, device):
        """A device paired with the PIN while it was on the screen for a stick: that was its one use."""
        with self._lock:
            if self._active is None or not device or device.get("role") != "full":
                return False
            ended = self._end("used")
            if ended:
                self.last["device"] = device.get("name")
        screen = self.api.pinscreen
        if ended and screen is not None:
            screen.recovery_changed()
        return ended

    def lines(self, active, addresses):
        """What the display says. Only characters from the PIN screen's safe set are used by the caller."""
        addr = " or ".join(addresses[:2]) if addresses else "the panel in a browser"
        return ["nxlx.mastercontrol", "Recovery stick found",
                "Open %s" % addr,
                "Owner PIN  %s" % active["pin"],
                "Type it in the PIN field. It works once.",
                "Hides in %d s. Take the stick out to hide it now" % active["seconds_left"]]
