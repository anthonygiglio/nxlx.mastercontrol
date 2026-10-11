# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A pairing code on the box's display, asked for from a MIDI controller (D61).

For the person who stands at the box with a controller and no paired phone or laptop: hold a control that was given
the action "Show a presenter code" or "Show a full access code" for 3 to 10 seconds and let go, and the box draws a
one-time code on its own display for two minutes. A new device pairs with it on the panel's pairing page.

This hands out access to whoever can touch the controller, so:

* It is a box setting, `controller_code`, OFF unless a full-access device switched it on (System > People and
  codes), and the full-access kind has a second switch of its own. The setting is never in a settings file that is
  exported or imported, a factory reset switches it off, and it cannot be changed through the remote-support tunnel
  (every /api/access route is refused there).
* There is NO API route that makes such a code. The only caller of `request` is the MIDI hub, for a message read
  from a MIDI device file on the box (midi.MidiHub._local), and only from a controller that is on USB. OSC, DMX, the
  schedule, a Room scene and the sync channel reach the box through `Api.handle`, which has no such route, so none
  of them can ask for a code.
* The code is single-use, lasts auth.CONTROLLER_SECONDS, and at most auth.CONTROLLER_PER_HOUR are made in an hour
  (auth.py keeps these, and reads the setting again when the code is used). A wrong guess counts against the same
  lockout as a wrong PIN.
* The digits go to the display and nowhere else: not into any answer of the API, not into the log, not to a
  controller's lights. Snapshots for devices without full access leave the on-screen text out while it is shown.
* It comes off the display when it is used, when it runs out, when any control with one of the two actions is
  pressed again, and when a full-access device cancels it or switches the setting off.
"""

import time

from . import auth as auth_mod

KINDS = tuple(auth_mod.CONTROLLER_KINDS)        # "join" (a presenter), "owner" (full access)
WORDS = {"join": "presenter", "owner": "full access"}


class ControllerCodeError(Exception):
    pass


def validate(body, current):
    """New switches from untrusted input, based on `current`. Only the keys given change. Raises ControllerCodeError."""
    new = auth_mod.controller_setting(current)      # owner counts only beside enabled: a stale "owner" in a file with
    for key in ("enabled", "owner"):                # "enabled" false must not come back when the first switch goes on
        if key in body:
            if not isinstance(body[key], bool):
                raise ControllerCodeError("%s must be true or false" % key)
            new[key] = body[key]
    if "join" in body:                          # what the join kind pairs (D80): a Guest unless the owner says an Operator
        if body["join"] not in ("view", "live"):
            raise ControllerCodeError("join must be view (a guest) or live (an operator)")
        new.pop("join", None)
        if body["join"] == "live":
            new["join"] = "live"
    if not new["enabled"]:
        new["owner"] = False                    # switching it off switches the full-access kind off with it
    return new


class ControllerCodes:
    def __init__(self, api, log=print, clock=time.monotonic):
        self.api, self.log, self._clock = api, log, clock
        self._cancelled_press = None            # the start time of the press that took a code off the display
        self._said = 0.0

    def cfg(self):
        on, owner = self.api.auth._controller_cfg()
        return {"enabled": on, "owner": owner, "join": self.api.auth.controller_role("join")}

    def _screen(self):
        return getattr(self.api, "pinscreen", None)

    def _refresh(self):
        screen = self._screen()
        if screen is not None:
            return screen.controller_changed()
        return False

    def _quiet(self, text):
        """One line in the log at most every ten seconds: a control held again and again must not fill it."""
        now = self._clock()
        if now >= self._said:
            self._said = now + 10
            self.log("pvj-web: controller code: " + text)

    # --- from the controller (the MIDI hub only) -----------------------------------------------------
    def press(self, since):
        """A control with one of the two actions went down, at `since` on the hub's clock. If a code is on the
        display it is taken off, and the hold this press starts gives no new code. True if one was taken off."""
        if not self.api.auth.cancel_controller_code("pressed again"):
            return False
        self._cancelled_press = since
        self._refresh()
        self.log("pvj-web: controller code: taken off the display by a press on the controller")
        return True

    def request(self, kind, since, source="?"):
        """The control was held long enough and let go: show a code of `kind`. `since`: when its press began. `source`:
        the controller's name, for the log only. True if a code is now on the display."""
        if kind not in KINDS:
            return False
        if self._cancelled_press is not None and since == self._cancelled_press:
            return False                        # this hold began with the press that hid the last code
        screen, auth = self._screen(), self.api.auth
        if screen is None:
            self._quiet("asked for, but this box has no on-screen display")
            return False
        locked = auth.pairing_locked()
        if locked >= auth_mod.CONTROLLER_SECONDS and self.cfg()["enabled"] and (kind != "owner" or self.cfg()["owner"]):
            # Wrong guesses have locked pairing for longer than a code would last: it could not be used, so none
            # is made (and none is counted); the display says why. The lockout itself is not touched.
            self._quiet("asked for from %s, but pairing is locked for %d more seconds after wrong guesses" % (source, locked))
            screen.controller_notice("locked", -(-locked // 60))
            return False
        try:
            auth.create_controller_code(kind)
        except auth_mod.ControllerOff as e:
            self._quiet("asked for from %s, but %s" % (source, e))
            if self.cfg()["enabled"]:           # only the full-access kind is off: say so; with all of it off, nothing is drawn
                screen.controller_notice("owner off")
            return False
        except auth_mod.JoinLimit as e:
            self._quiet("asked for from %s, but %s" % (source, e))
            screen.controller_notice("limit", -(-(e.retry_after or 60) // 60))
            return False
        except auth_mod.TooManyDevices:
            self._quiet("asked for from %s, but too many devices are paired" % source)
            screen.controller_notice("full")
            return False
        if not self._refresh():                 # the player did not take the text: a code nobody can see is ended
            auth.cancel_controller_code("not shown")
            self._quiet("asked for from %s, but the display could not show it" % source)
            return False
        self.log("pvj-web: controller code: a one-time %s code was put on the display from %s, for %d seconds"
                 % (WORDS[kind], source, auth_mod.CONTROLLER_SECONDS))
        return True

    # --- from the panel (full-access devices) ---------------------------------------------------------
    def used(self):
        """Someone just paired: if it was with the code, it is off the display now and not three seconds later."""
        if self._screen() is not None and self._screen()._controller_up:
            self._refresh()

    def state(self):
        """For a full-access device: the two switches and what the box did. Never the digits."""
        midi = self.api.settings.data.get("control", {}).get("midi", {})
        return dict(self.cfg(), status=self.api.auth.controller_status(), seconds=auth_mod.CONTROLLER_SECONDS,
                    midi_on=bool(midi.get("enabled")) and self.api.registry.enabled("control-midi"))

    def cancel(self):
        ended = self.api.auth.cancel_controller_code("cancelled")
        if ended:
            self._refresh()
        return ended

    def switched(self):
        """The setting changed: a code the new setting does not allow is gone (auth reads the setting), so is its
        picture."""
        self.api.auth.controller_status()
        self._refresh()


ACTION_KINDS = {"code_join": "join", "code_owner": "owner"}     # the MIDI actions (midi.ACTIONS) and the kind each asks for
