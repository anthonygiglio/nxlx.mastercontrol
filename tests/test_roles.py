# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Owner, Operator, Guest (D80): who passes the gate on every route, written out as data.

The table test replaces every handler with one that answers {}, so a status here is the gate's answer alone: 200 is
"let through", 401 "no device", 403 "refused". What a handler then does with a body is tested further down with the
real handlers. A route that has no row here fails the test: decide who may use it and add the row.
"""
import inspect
import json
import os
import re

from pvj import api as api_mod, auth as auth_mod, boxcare, dmx, midi, osc, policy, room, scheduler, server, support as sp
from pvj.settings import Settings
from tests.test_support import LAN, TUNNEL, SupportBase

# Who asks, in the order of every tuple below.
WHO = ("nobody", "guest, locked", "guest, open", "operator", "owner",
       "support view", "support live", "support full", "midi", "osc", "dmx", "room")

# The kinds of row. Each is the status for every one of WHO, in that order.
KINDS = {
    # no login needed
    "open":   (200, 200, 200, 200, 200,  200, 200, 200,  200, 200, 200, 200),
    # any paired device; what a guest always had
    "read":   (401, 200, 200, 200, 200,  200, 200, 200,  200, 200, 200, 200),
    # the operator's, and a presenter's before: controllers and a live support session keep it
    "live":   (401, 403, 403, 200, 200,  403, 200, 200,  200, 200, 200, 200),
    # the same, and a guest may use some forms of it while guest controls are open (GUEST_BODIES)
    "guest":  (401, 403, 200, 200, 200,  403, 200, 200,  200, 200, 200, 200),
    # the operator's since D80, the owner's before: no controller and no live support session gains it
    "op":     (401, 403, 403, 200, 200,  403, 403, 200,  403, 403, 403, 403),
    # the owner's
    "owner":  (401, 403, 403, 403, 200,  403, 403, 200,  403, 403, 403, 403),
}


def at_the_studio(kind):
    """The same row for a route that is never answered through the support tunnel (support.REMOTE_DENY)."""
    row = list(KINDS[kind])
    row[5:8] = [403, 403, 403]
    return tuple(row)


STUDIO = at_the_studio

ROWS = {
    ("GET", "/api/hello"): KINDS["open"],
    ("POST", "/api/pair"): STUDIO("open"),
    ("POST", "/api/session"): STUDIO("open"),
    ("POST", "/api/logout"): KINDS["open"],
    ("POST", "/api/support/login"): KINDS["open"],
    ("GET", "/api/status"): KINDS["read"],
    ("GET", "/api/media"): KINDS["read"],
    ("GET", "/api/pads"): KINDS["read"],
    ("GET", "/api/modules"): KINDS["read"],
    ("GET", "/api/theme"): KINDS["read"],
    ("GET", "/api/osc"): KINDS["read"],
    ("GET", "/api/media/import"): KINDS["read"],
    ("POST", "/api/media/info"): KINDS["read"],
    ("GET", "/api/system"): KINDS["read"],
    ("GET", "/api/inputs"): KINDS["read"],
    ("GET", "/api/overlay"): KINDS["read"],
    ("GET", "/api/health"): KINDS["read"],
    ("GET", "/api/support"): KINDS["read"],
    ("GET", "/api/sync"): KINDS["read"],
    ("GET", "/api/mapper"): KINDS["read"],
    ("GET", "/api/shaders"): KINDS["read"],
    ("GET", "/api/effects"): KINDS["read"],
    ("GET", "/api/projectors"): KINDS["read"],
    ("GET", "/api/room"): KINDS["read"],
    ("GET", "/api/audio"): KINDS["read"],
    ("GET", "/api/autostart"): KINDS["read"],
    ("GET", "/api/streams"): KINDS["read"],
    ("GET", "/api/schedule"): KINDS["read"],
    ("GET", "/api/preview.jpg"): KINDS["read"],
    ("POST", "/api/play"): KINDS["guest"],
    ("POST", "/api/control"): KINDS["guest"],
    ("POST", "/api/blackout"): KINDS["guest"],
    ("POST", "/api/vibes"): KINDS["guest"],
    ("POST", "/api/shaders/play"): KINDS["guest"],
    ("POST", "/api/shaders/step"): KINDS["guest"],
    ("POST", "/api/shaders/preset"): KINDS["guest"],
    ("POST", "/api/effects"): KINDS["guest"],
    ("POST", "/api/effects/step"): KINDS["guest"],
    ("POST", "/api/effects/preset"): KINDS["guest"],
    ("POST", "/api/projector"): KINDS["guest"],
    ("POST", "/api/room/scene"): KINDS["guest"],
    ("POST", "/api/room/group"): KINDS["guest"],
    ("POST", "/api/fadeout"): KINDS["live"],
    ("POST", "/api/fadein"): KINDS["live"],
    ("POST", "/api/testpattern"): KINDS["live"],
    ("POST", "/api/testtone"): KINDS["live"],
    ("POST", "/api/mix"): KINDS["live"],
    ("POST", "/api/overlay"): KINDS["live"],
    ("POST", "/api/shaders/values"): KINDS["live"],
    ("POST", "/api/effects/values"): KINDS["live"],
    ("POST", "/api/autostart/test"): KINDS["live"],
    ("POST", "/api/support/stop"): KINDS["live"],
    ("GET", "/api/midi"): KINDS["live"],
    ("GET", "/api/access"): STUDIO("live"),
    ("POST", "/api/access/code"): STUDIO("live"),
    ("POST", "/api/access/cancel"): STUDIO("live"),
    ("POST", "/api/access/screen"): STUDIO("live"),
    ("GET", "/api/qr.svg"): STUDIO("live"),
    ("POST", "/api/guests"): KINDS["op"],
    ("POST", "/api/media/upload"): KINDS["op"],
    ("POST", "/api/media/import"): KINDS["op"],
    ("POST", "/api/media/import/cancel"): KINDS["op"],
    ("POST", "/api/media/delete"): KINDS["op"],
    ("POST", "/api/media/rename"): KINDS["op"],
    ("POST", "/api/pads"): KINDS["op"],
    ("POST", "/api/shaders"): KINDS["op"],
    ("POST", "/api/shaders/presets"): KINDS["op"],
    ("POST", "/api/effects/presets"): KINDS["op"],
    ("POST", "/api/effects/library"): KINDS["op"],
    ("POST", "/api/effects/config"): KINDS["op"],
    ("POST", "/api/room"): KINDS["op"],
    ("POST", "/api/audio"): KINDS["op"],
    ("POST", "/api/autostart"): KINDS["op"],
    ("POST", "/api/schedule"): KINDS["op"],
    ("POST", "/api/mapper"): KINDS["op"],
    ("POST", "/api/theme"): KINDS["op"],
    ("POST", "/api/theme/export"): KINDS["op"],
    ("POST", "/api/player/restart"): KINDS["op"],
    ("GET", "/api/devices"): KINDS["op"],
    ("POST", "/api/devices/invite"): STUDIO("op"),
    ("POST", "/api/devices/revoke"): STUDIO("op"),
    ("POST", "/api/theme/add"): STUDIO("owner"),
    ("POST", "/api/theme/remove"): STUDIO("owner"),
    ("POST", "/api/osc"): KINDS["owner"],
    ("GET", "/api/osc/messages"): KINDS["owner"],
    ("POST", "/api/osc/key"): STUDIO("owner"),
    ("GET", "/api/dmx"): KINDS["owner"],
    ("POST", "/api/dmx"): KINDS["owner"],
    ("POST", "/api/midi"): KINDS["owner"],
    ("POST", "/api/midi/learn"): KINDS["owner"],
    ("POST", "/api/midi/map"): KINDS["owner"],
    ("POST", "/api/midi/lights"): KINDS["owner"],
    ("POST", "/api/access/controller"): STUDIO("owner"),
    ("POST", "/api/streams"): KINDS["owner"],
    ("POST", "/api/projectors"): KINDS["owner"],
    ("POST", "/api/sync"): KINDS["owner"],
    ("POST", "/api/modules/*"): KINDS["owner"],
    ("GET", "/api/network"): KINDS["owner"],
    ("POST", "/api/network/plan"): KINDS["owner"],
    ("POST", "/api/network/apply"): KINDS["owner"],
    ("POST", "/api/network/confirm"): KINDS["owner"],
    ("POST", "/api/network/revert"): KINDS["owner"],
    ("POST", "/api/network/scan"): KINDS["owner"],
    ("POST", "/api/system/reboot"): KINDS["owner"],
    ("POST", "/api/system/poweroff"): STUDIO("owner"),
    ("POST", "/api/system/clock"): KINDS["owner"],
    ("GET", "/api/system/update"): KINDS["owner"],
    ("POST", "/api/system/update"): KINDS["owner"],
    ("POST", "/api/system/update/upload"): KINDS["owner"],
    ("POST", "/api/pin/rotate"): STUDIO("owner"),
    ("POST", "/api/pin/unlock"): STUDIO("owner"),
    ("POST", "/api/pin/show"): STUDIO("owner"),
    ("POST", "/api/support/config"): STUDIO("owner"),
    ("POST", "/api/support/start"): STUDIO("owner"),
    ("POST", "/api/support/extend"): STUDIO("owner"),
    ("POST", "/api/system/settings/export"): KINDS["owner"],
    ("POST", "/api/system/settings/import"): STUDIO("owner"),
    ("GET", "/api/system/diagnostics"): KINDS["owner"],
    ("POST", "/api/system/factory-reset"): STUDIO("owner"),
}

SCENE, STREAM_SCENE, OFF_SCENE, GROUP = "0a0a0a0a", "0b0b0b0b", "0c0c0c0c", "0d0d0d0d"

# For each route a guest may use while guest controls are open: forms that pass, and forms that are refused (403).
GUEST_BODIES = {
    ("POST", "/api/play"): (
        [{"pad": [0, 0]}, {"file": "a.mp4"}, {"file": "a.mp4", "loop": False}],
        [{"stream": "x"}, {"preset": "p"}, {"usb": "STICK/a.mp4"}, {"usb_drive": "STICK"}, {"capture": "/dev/video0"},
         {"slideshow": {"source": "media"}}, {"pad": [0, 0], "file": "a.mp4"}, {"file": "a.mp4", "ending": "next"},
         {"pad": [0, 0], "stream": "x"}, {"file": ["a.mp4"]}, {}]),
    ("POST", "/api/control"): (
        [{"action": "next"}, {"action": "prev"}, {"action": "stop"}],
        [{"action": a, "value": v} for a, v in (("volume", 130), ("opacity", 0), ("size", 10), ("position", 5), ("rotate", 90),
                                                ("flip_h", True), ("speed", 4), ("seek", 5), ("mute", True), ("loop", True),
                                                ("pause", True), ("volume_step", 50))]
        + [{"action": "reset"}, {"action": "shuffle"}, {"action": "stop", "value": 1}, {}]),
    ("POST", "/api/blackout"): ([{"on": True}, {"on": False}], [{"on": True, "seconds": 3}, {}]),
    ("POST", "/api/vibes"): (
        [{"on": True}, {"on": True, "set": "Ambient"}, {"on": False}, {"next": True}, {"previous": True}],
        [{"dwell": 30}, {"on": True, "dwell": 30}, {"next": True, "dwell": 5}, {"on": 0}, {"next": 1}, {}]),
    ("POST", "/api/shaders/play"): ([{"id": "nxlx-silk.fs"}], [{"id": "nxlx-silk.fs", "values": {"speed": 9}}, {}]),
    ("POST", "/api/shaders/step"): ([{}, {"dir": -1}], [{"dir": 1, "values": {}}]),
    ("POST", "/api/shaders/preset"): ([{"name": "Calm"}, {"index": 1, "id": "nxlx-silk.fs"}], [{"name": "Calm", "save": True}]),
    ("POST", "/api/effects"): (
        [{"id": "fx-wash.fs"}, {"id": "fx-wash.fs", "preset": "Soft"}, {"off": True}],
        [{"id": "fx-wash.fs", "values": {"a": 1}}, {"id": "fx-wash.fs", "controls": {"amount": 1}}, {"toggle": True}, {"off": 1}, {}]),
    ("POST", "/api/effects/step"): ([{}, {"dir": -1}], [{"dir": 1, "half": True}]),
    ("POST", "/api/effects/preset"): ([{"name": "Soft"}], [{"name": "Soft", "values": {}}]),
    ("POST", "/api/projector"): (
        [{"id": "all", "action": "on"}, {"id": "0e0e0e0e", "action": "input", "input": "31"}],
        [{"action": a} for a in ("mute", "unmute", "mute_picture", "unmute_picture", "mute_sound", "unmute_sound", "identify", "state")]
        + [{"action": "on", "background": True}, {}]),
    ("POST", "/api/room/group"): (
        [{"group": "all", "action": "on"}, {"group": GROUP, "action": "input", "input": "31"}, {"number": 0, "action": "on"}],
        [{"group": "all", "action": "mute"}, {"group": "all", "action": "unmute_sound"}, {}]),
    ("POST", "/api/room/scene"): (
        [{"scene": SCENE}, {"number": 1}, {"name": "Show"}],
        [{"scene": STREAM_SCENE}, {"scene": SCENE, "force": True}]),
}

ROOM = {"groups": [], "scenes": [
    {"id": SCENE, "name": "Show", "groups": [{"group": "all", "power": "on", "input": "", "picture": "leave", "sound": "leave"}],
     "box": {"action": "pad", "pad": [0, 0]}},
    {"id": STREAM_SCENE, "name": "From outside", "groups": [], "box": {"action": "stream", "stream": "0f0f0f0f"}},
    {"id": OFF_SCENE, "name": "Good night", "groups": [{"group": "all", "power": "off", "input": "", "picture": "leave", "sound": "leave"}],
     "box": {"action": "stop"}},
]}

CONTROLLER_DEVICES = {"midi": midi.MIDI_DEVICE, "osc": osc.OSC_DEVICE, "dmx": dmx.DMX_DEVICE, "room": room.ROOM_DEVICE}


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class RolesBase(SupportBase):
    """An owner, an operator and a guest paired at the studio; the Room and Projectors modules on with three scenes."""
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.log = self.lines.append
        self.gclock = Clock()
        self.api.guests = policy.GuestControls(self.api, clock=self.gclock)
        self.owner, self.operator = self.full_dev, self.live_dev
        self.guest_token = self.call("POST", "/api/devices/invite", {"name": "Guest phone", "role": "view"}, token=self.full)[1]["token"]
        self.guest = self.auth.authenticate(self.guest_token)
        with self.settings.lock:
            self.settings.data["modules"]["enabled"].update({"projector": True, "room": True})
            self.settings.data["room"] = json.loads(json.dumps(ROOM))
            self.settings.save()

    def lock(self, locked, by=None):
        st, body = self.h("POST", "/api/guests", {"locked": locked}, by or self.operator)
        self.assertEqual(st, 200, body)

    def later(self, seconds=policy.GUEST_WINDOW + 1):
        self.gclock.t += seconds

    def saves(self):
        """Count settings.save() from here on; returns the list that grows."""
        made, real = [], self.settings.save

        def save(*a, **kw):
            made.append(1)
            return real(*a, **kw)
        self.settings.save = save
        self.addCleanup(lambda: setattr(self.settings, "save", real))
        return made


class GateTableTest(RolesBase):
    def stub(self):
        real = self.api.routes()
        stubbed = {k: (need, (lambda b, d, c: {})) for k, (need, _h) in real.items()}
        self.api.routes = lambda: stubbed
        self.api.set_module = lambda *a: {}
        return real

    def ask(self, method, path, body, device, client):
        path = path.replace("*", "shaders")
        if (method, path) in policy.OUTSIDE and (method, path) not in self.api.routes():
            try:
                self.api.gate(method, path, body, device, client)
                return 200
            except api_mod.ApiError as e:
                return e.status
        self.later()                                  # the table is not about the rate limit
        return self.api.handle(method, path, body, device, client)[0]

    def support_device(self, role):
        if self.api.support.session is not None:
            self.h("POST", "/api/support/stop", device=self.owner)
        st, body = self.start(role=role)
        self.assertEqual(st, 200, body)
        st, login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)
        self.assertEqual(st, 200, login)
        dev = self.api.support.authenticate(login["token"])
        self.assertEqual((dev["role"], dev["remote"]), (role, True))
        return dev

    def test_every_route_has_a_row_and_no_row_is_left_over(self):
        routes = set(self.api.routes()) | set(policy.OUTSIDE) | {("POST", "/api/modules/*")}
        # the paths the server answers by itself are found in its source, so a new one cannot be forgotten
        for path in re.findall(r'path == "(/api/[^"]+)"', inspect.getsource(server)):
            self.assertTrue(any(p == path for _m, p in policy.OUTSIDE), "%s is answered by the server and is not in policy.OUTSIDE" % path)
        self.assertEqual(sorted(routes - set(ROWS)), [], "routes without a row in tests/test_roles.py: decide who may use them")
        self.assertEqual(sorted(set(ROWS) - routes), [], "rows for routes that are gone")
        self.assertEqual(sorted(set(GUEST_BODIES)), sorted(policy.GUEST))
        self.assertEqual(sorted(k for k, row in ROWS.items() if row[2] == 200 and row[1] == 403), sorted(policy.GUEST))

    def test_the_route_table_and_the_rows_agree_about_the_minimum_role(self):
        floors = dict({k: need for k, (need, _h) in self.api.routes().items()}, **policy.OUTSIDE)
        floors[("POST", "/api/modules/*")] = "full"
        for key, row in ROWS.items():
            need = floors[key]
            want = None if row[0] == 200 else "view" if row[1] == 200 else "live" if row[3] == 200 else "full"
            self.assertEqual(need, want, key)

    def test_who_passes_the_gate_on_every_route(self):
        self.ready()
        self.stub()
        wrong = []

        def check(who, device, client, column):
            for (method, path), row in sorted(ROWS.items()):
                bodies = [{}]
                if who == "guest, open" and (method, path) in GUEST_BODIES:
                    bodies = GUEST_BODIES[(method, path)][0]
                for body in bodies:
                    got = self.ask(method, path, body, device, client)
                    if got != row[column]:
                        wrong.append("%s: %s %s %s answered %s, the row says %s" % (who, method, path, json.dumps(body), got, row[column]))

        check("nobody", None, LAN, 0)
        self.lock(True)
        check("guest, locked", self.guest, LAN, 1)
        self.lock(False)
        check("guest, open", self.guest, LAN, 2)
        check("operator", self.operator, LAN, 3)
        check("owner", self.owner, LAN, 4)
        for column, name in ((8, "midi"), (9, "osc"), (10, "dmx"), (11, "room")):
            check(name, CONTROLLER_DEVICES[name], name, column)
        for column, role in ((5, "view"), (6, "live"), (7, "full")):
            dev = self.support_device(role)
            check("support " + role, dev, TUNNEL, column)
            # the same login used from the studio's own network is no login at all
            self.assertEqual(self.api.handle("GET", "/api/status", {}, dev, LAN)[0], 403)
        self.assertEqual(wrong, [])

    def test_a_guest_is_refused_every_other_form_of_a_route_he_may_use(self):
        self.stub()
        for (method, path), (_ok, refused) in sorted(GUEST_BODIES.items()):
            for body in refused:
                self.later()
                st, out = self.api.handle(method, path, body, self.guest, LAN)
                self.assertEqual(st, 403, "%s %s" % (path, json.dumps(body)))
                # and the same form is the operator's
                self.assertEqual(self.api.handle(method, path, body, self.operator, LAN)[0], 200, "%s %s" % (path, json.dumps(body)))

    def test_no_controller_and_no_support_session_below_full_leaves_what_a_presenter_had(self):
        """policy.LEGACY_LIVE is what master's route table gave a presenter, and every caller of Api.handle inside the
        box uses one of the four identities."""
        self.ready()
        self.stub()
        legacy = {k for k, row in ROWS.items() if row[8] == 200}
        self.assertEqual(sorted(policy.LEGACY_LIVE), sorted(legacy))
        outside = sorted(set(ROWS) - legacy)
        self.assertGreater(len(outside), 50)
        live = self.support_device("live")
        for method, path in outside:
            for name, device in CONTROLLER_DEVICES.items():
                self.assertEqual(self.ask(method, path, {}, device, name), 403, "%s on %s %s" % (name, method, path))
                self.assertEqual(self.ask(method, path, {}, dict(device, role="full"), name), 403)   # not even with a higher role written on it
            self.assertEqual(self.ask(method, path, {}, live, TUNNEL), 403, "live support on %s %s" % (method, path))
        self.assertEqual(sorted(d["id"] for d in CONTROLLER_DEVICES.values()), sorted(policy.CONTROLLERS))
        for module in (midi, osc, dmx, room, scheduler):
            for call in re.findall(r"api\.handle\(([^\n]*)", inspect.getsource(module)):
                self.assertRegex(call, r"(MIDI|OSC|DMX|ROOM)_DEVICE", "%s calls the API as something else: %s" % (module.__name__, call))
        pvj_dir = os.path.dirname(api_mod.__file__)
        callers = sorted(n for n in os.listdir(pvj_dir) if n.endswith(".py") and re.search(r"api\.handle\(", open(os.path.join(pvj_dir, n)).read()))
        self.assertEqual(callers, ["dmx.py", "midi.py", "osc.py", "room.py", "scheduler.py", "server.py"])

    def test_a_guest_device_can_never_be_taken_for_a_controller(self):
        for _ in range(50):
            _token, dev = self.auth.invite("x", "view")
            self.assertRegex(dev["id"], r"^[0-9a-f]{8}$")
            self.assertFalse(policy.is_controller(dev))
        self.assertFalse(policy.is_guest(dict(self.guest, remote=True)))
        self.assertFalse(policy.is_guest(dict(room.ROOM_DEVICE, role="view")))


class LockTest(RolesBase):
    def test_open_by_default_and_in_the_status_of_every_role(self):
        self.assertNotIn("guest_controls", self.settings.data)
        for dev in (self.guest, self.operator, self.owner):
            self.assertEqual(self.h("GET", "/api/status", device=dev)[1]["guest_controls"], {"locked": False})

    def test_an_operator_and_an_owner_change_it_and_nobody_else(self):
        for dev in (self.operator, self.owner):
            self.lock(True, dev)
            self.assertTrue(self.h("GET", "/api/status", device=self.guest)[1]["guest_controls"]["locked"])
            self.lock(False, dev)
        self.assertEqual(self.h("POST", "/api/guests", {"locked": True}, self.guest)[0], 403)
        self.assertEqual(self.h("POST", "/api/guests", {"locked": True}, None)[0], 401)
        for name, dev in CONTROLLER_DEVICES.items():
            self.assertEqual(self.h("POST", "/api/guests", {"locked": True}, dev, name)[0], 403)
        for body in ({}, {"locked": 1}, {"locked": "yes"}, {"locked": True, "more": 1}):
            self.assertEqual(self.h("POST", "/api/guests", body, self.owner)[0], 400, body)
        self.assertFalse(self.api.guests.locked())
        self.assertTrue(any("guest controls locked by device %s" % self.operator["id"] in line for line in self.lines))

    def test_it_acts_on_a_guests_next_request_and_says_why(self):
        self.assertEqual(self.h("POST", "/api/blackout", {"on": True}, self.guest)[0], 200)
        self.lock(True)
        st, body = self.h("POST", "/api/blackout", {"on": False}, self.guest)
        self.assertEqual((st, body["error"]), (403, "The room is locked for a show: you can watch"))
        self.assertTrue(self.api.mix["blackout"])
        self.assertEqual(self.h("GET", "/api/status", device=self.guest)[0], 200)        # he still watches
        self.lock(False)
        self.assertEqual(self.h("POST", "/api/blackout", {"on": False}, self.guest)[0], 200)

    def test_it_is_kept_across_a_restart_and_a_damaged_value_means_locked(self):
        self.lock(True)
        again = Settings(self.settings.path)
        again.load()
        self.assertEqual(again.data["guest_controls"], {"locked": True})
        self.api.settings = again
        try:
            self.assertTrue(policy.GuestControls(self.api).locked())
            for damaged in ("no", 0, [], {}, {"locked": "false"}, {"locked": None}):
                again.data["guest_controls"] = damaged
                self.assertTrue(policy.GuestControls(self.api).locked(), damaged)
            again.data["guest_controls"] = {"locked": False}
            self.assertFalse(policy.GuestControls(self.api).locked())
        finally:
            self.api.settings = self.settings

    def test_a_save_that_fails_changes_nothing(self):
        def broken():
            raise OSError(28, "No space left on device")
        real, self.settings.save = self.settings.save, broken
        try:
            self.assertEqual(self.h("POST", "/api/guests", {"locked": True}, self.owner)[0], 500)
        finally:
            self.settings.save = real
        self.assertFalse(self.api.guests.locked())
        self.assertNotIn("guest_controls", self.settings.data)

    def test_the_lock_is_not_in_an_export_and_an_import_leaves_it(self):
        self.lock(True)
        st, out = self.h("POST", "/api/system/settings/export", {}, self.owner)
        self.assertEqual(st, 200, out)
        self.assertNotIn("guest_controls", json.dumps(out))
        self.assertIn("guest_controls", boxcare.NEVER)

    def test_a_settings_file_with_the_lock_loads_in_a_release_that_does_not_know_it(self):
        """The rollback: this branch does not touch settings.py, whose loader is master's own. It keeps a key it does
        not know through a load and a save (the same was tried against a checkout of master, see the journal)."""
        self.lock(True)
        old = Settings(self.settings.path)
        old.load()
        old.data["mix"] = {"transition": "dip", "duration": 2.0}
        old.save()
        with open(self.settings.path) as f:
            kept = json.load(f)
        self.assertEqual(kept["guest_controls"], {"locked": True})
        self.assertEqual(kept["schema"], self.settings.data["schema"])
        self.assertEqual([d["role"] for d in kept["devices"]], [d["role"] for d in self.settings.data["devices"]])


class PowerOffTest(RolesBase):
    OFFS = (("/api/projector", {"id": "all", "action": "off"}), ("/api/room/group", {"group": "all", "action": "off"}),
            ("/api/room/scene", {"scene": OFF_SCENE}))

    def setUp(self):
        super().setUp()
        self.done = []
        real = self.api.routes()
        table = dict(real)
        for path, _body in self.OFFS:
            table[("POST", path)] = (real[("POST", path)][0], lambda b, d, c, path=path: self.done.append((path, b)) or {})
        self.api.routes = lambda: table

    def off(self, path, body, device=None, **more):
        return self.h("POST", path, dict(body, **more), device or self.guest)

    def test_one_request_never_switches_off_and_the_second_must_carry_the_token(self):
        for path, body in self.OFFS:
            self.later(policy.OFF_WINDOW + 1)
            st, out = self.off(path, body)
            self.assertEqual(st, 409, out)
            self.assertEqual(out["confirm"]["seconds"], 30)
            token = out["confirm"]["token"]
            self.assertEqual(self.done, [])
            for wrong in ("", "x", True, 1, None, [token], token + "x"):
                self.later()
                self.assertEqual(self.off(path, body, confirm=wrong)[0], 409, wrong)
            self.assertEqual(self.done, [])
            self.later()
            st, out = self.off(path, body, confirm=token)
            self.assertEqual(st, 200, out)
            self.assertEqual(self.done, [(path, body)])          # the handler never sees the token
            del self.done[:]

    def test_a_token_works_once_for_its_own_device_and_its_own_request_and_not_late(self):
        path, body = self.OFFS[0]
        other = self.auth.authenticate(self.call("POST", "/api/devices/invite", {"name": "g2", "role": "view"}, token=self.full)[1]["token"])
        token = self.off(path, body)[1]["confirm"]["token"]
        self.assertEqual(self.off(path, body, other, confirm=token)[0], 409)                    # another guest's token
        token = self.off(path, body)[1]["confirm"]["token"]
        self.assertEqual(self.off("/api/room/group", {"group": "all", "action": "off"}, confirm=token)[0], 409)   # another request
        token = self.off(path, body)[1]["confirm"]["token"]
        self.assertEqual(self.off(path, {"id": "0e0e0e0e", "action": "off"}, confirm=token)[0], 409)       # another projector
        token = self.off(path, body)[1]["confirm"]["token"]
        self.later(policy.CONFIRM_SECONDS + 1)
        self.assertEqual(self.off(path, body, confirm=token)[0], 409)                           # too late
        self.assertEqual(self.done, [])
        self.later()
        token = self.off(path, body)[1]["confirm"]["token"]
        self.assertEqual(self.off(path, body, confirm=token)[0], 200)
        self.later(policy.OFF_WINDOW + 1)
        self.assertEqual(self.off(path, body, confirm=token)[0], 409)                           # used
        self.assertEqual(len(self.done), 1)

    def test_locking_ends_a_confirm_that_was_waiting(self):
        path, body = self.OFFS[0]
        token = self.off(path, body)[1]["confirm"]["token"]
        self.lock(True)
        self.assertEqual(self.off(path, body, confirm=token)[0], 403)
        self.lock(False)
        self.assertEqual(self.off(path, body, confirm=token)[0], 409)
        self.assertEqual(self.done, [])

    def test_power_offs_by_guests_are_few(self):
        path, body = self.OFFS[0]
        guests = [self.guest] + [self.auth.authenticate(self.call("POST", "/api/devices/invite", {"name": "g%d" % i, "role": "view"}, token=self.full)[1]["token"])
                                 for i in range(2)]

        def switch_off(dev):
            st, out = self.off(path, body, dev)
            if st != 409:
                return st, out
            return self.off(path, body, dev, confirm=out["confirm"]["token"])
        self.assertEqual(switch_off(guests[0])[0], 200)
        self.later()
        st, out = switch_off(guests[0])                         # one per device
        self.assertEqual(st, 429, out)
        self.assertGreater(out["retry_after"], 200)
        self.assertEqual(switch_off(guests[1])[0], 200)
        self.assertEqual(switch_off(guests[2])[0], 429)         # two for the box
        self.assertEqual(len(self.done), 2)
        self.later(policy.OFF_WINDOW + 1)
        self.assertEqual(switch_off(guests[2])[0], 200)

    def test_an_operator_and_a_controller_are_not_asked(self):
        for path, body in self.OFFS:
            self.assertEqual(self.off(path, body, self.operator)[0], 200)
        self.assertEqual(self.h("POST", "/api/projector", {"id": "all", "action": "off", "background": True}, osc.OSC_DEVICE, "osc")[0], 200)
        self.assertEqual(len(self.done), 4)


class GuestLimitTest(RolesBase):
    def test_one_guest_and_all_guests_together_are_limited_and_others_are_not(self):
        body = {"on": True}
        for _ in range(policy.GUEST_ACTIONS):
            self.assertEqual(self.h("POST", "/api/blackout", body, self.guest)[0], 200)
        st, out = self.h("POST", "/api/blackout", body, self.guest)
        self.assertEqual(st, 429, out)
        self.assertGreaterEqual(out["retry_after"], 1)
        self.assertEqual(self.h("GET", "/api/status", device=self.guest)[0], 200)            # watching is not an action
        self.assertEqual(self.h("POST", "/api/blackout", body, self.operator)[0], 200)        # an operator is never held up by guests
        self.later()
        self.assertEqual(self.h("POST", "/api/blackout", body, self.guest)[0], 200)
        # the box: many guests, each under his own limit
        self.later()
        guests = [self.auth.invite("g%d" % i, "view")[1] for i in range(policy.BOX_ACTIONS // 5 + 1)]
        done = 0
        for g in guests:
            for _ in range(5):
                done += self.h("POST", "/api/blackout", body, g)[0] == 200
        self.assertEqual(done, policy.BOX_ACTIONS)
        self.assertEqual(self.h("POST", "/api/blackout", body, self.operator)[0], 200)

    def test_every_guest_action_is_logged_with_the_devices_name(self):
        named = self.auth.invite('Eve"\n pvj-web: owner PIN shown', "view")[1]
        self.assertEqual(self.h("POST", "/api/play", {"file": "a.mp4"}, named)[0], 200)
        mine = [line for line in self.lines if "guest " in line and "/api/play" in line]
        self.assertEqual(len(mine), 1, self.lines)
        self.assertIn(named["id"], mine[0])
        self.assertIn("a.mp4", mine[0])
        self.assertNotIn("\n", mine[0])                       # a name cannot start a line of its own in the journal
        self.assertTrue(mine[0].startswith("pvj-web: guest \"Eve"))
        before = len(self.lines)
        self.h("POST", "/api/play", {"stream": "x"}, named)   # refused: nothing was done, nothing is claimed
        self.assertEqual(len(self.lines), before)

    def test_nothing_a_guest_does_is_written_to_the_settings(self):
        with open(self.settings.path, "rb") as f:
            before = f.read()
        made = self.saves()
        answers = {}
        for (method, path), (ok, _refused) in sorted(GUEST_BODIES.items()):
            for body in ok:
                self.later()
                answers[(path, json.dumps(body))] = self.api.handle(method, path, body, self.guest, LAN)[0]
        for path, body in PowerOffTest.OFFS:
            self.later(policy.OFF_WINDOW + 1)
            st, out = self.h("POST", path, body, self.guest)
            self.assertEqual(st, 409, out)
            self.later()
            answers[(path, "off")] = self.h("POST", path, dict(body, confirm=out["confirm"]["token"]), self.guest)[0]
        self.assertNotIn(403, answers.values(), answers)
        self.assertNotIn(401, answers.values(), answers)
        self.assertGreater(list(answers.values()).count(200), 8, answers)       # real handlers ran, not only refusals
        self.assertEqual(made, [])
        with open(self.settings.path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.h("POST", "/api/mix", {"transition": "cut", "duration": 1}, self.operator)[0], 200)
        self.assertEqual(len(made), 1)                         # the counter does count


class PeopleTest(RolesBase):
    def invite(self, role, by):
        return self.h("POST", "/api/devices/invite", {"name": "new", "role": role}, by)

    def test_an_operator_removes_a_guest_and_never_an_operator_or_an_owner(self):
        second = self.auth.invite("op2", "live")[1]
        for target in (self.owner, self.operator, second):
            st, out = self.h("POST", "/api/devices/revoke", {"id": target["id"]}, self.operator)
            self.assertEqual(st, 403, out)
        self.assertEqual(len(self.settings.data["devices"]), 4)
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": self.guest["id"]}, self.operator)[0], 200)
        self.assertIsNone(self.auth.authenticate(self.guest_token))
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": self.guest["id"]}, self.operator)[0], 404)
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": ["x"]}, self.operator)[0], 404)
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": second["id"]}, self.owner)[0], 200)   # the owner still removes anyone
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": self.owner["id"]}, self.guest)[0], 403)

    def test_an_operator_sees_the_guests_only(self):
        seen = self.h("GET", "/api/devices", device=self.operator)[1]["devices"]
        self.assertEqual([d["id"] for d in seen], [self.guest["id"]])
        self.assertEqual(len(self.h("GET", "/api/devices", device=self.owner)[1]["devices"]), 3)
        self.assertEqual(self.h("GET", "/api/devices", device=self.guest)[0], 403)

    def test_an_operator_makes_guest_links_and_guest_codes_and_nothing_higher(self):
        self.assertEqual(self.invite("view", self.operator)[0], 200)
        for role in ("live", "full", None, ["view"]):
            st, out = self.invite(role, self.operator)
            self.assertEqual(st, 403, out)
        self.assertEqual(self.invite("live", self.owner)[0], 200)
        self.assertEqual(self.invite("full", self.owner)[0], 400)            # never by a link, as before
        self.assertEqual(self.h("POST", "/api/access/code", {"role": "view", "minutes": 15}, self.operator)[0], 200)
        self.assertEqual(self.h("POST", "/api/access/code", {"role": "live", "minutes": 15}, self.operator)[0], 403)
        self.assertEqual(self.h("POST", "/api/access/code", {"role": "live"}, self.owner)[0], 200)
        seen = self.h("GET", "/api/access", device=self.operator)[1]
        self.assertEqual([c["role"] for c in seen["codes"]], ["view"])       # he never sees the operator code
        self.assertNotIn("controller", seen)
        st, out, _ = self.call("GET", "/api/qr.svg?for=live", token=self.guest_token)
        self.assertEqual(st, 403)
        self.assertEqual(self.h("POST", "/api/access/cancel", {"role": "live"}, self.operator)[0], 403)
        self.assertEqual(self.h("POST", "/api/access/cancel", {"all": True}, self.operator)[0], 403)

    def test_an_operator_cannot_make_himself_more(self):
        op = self.operator
        for method, path, body in (
                ("POST", "/api/pin/show", {}), ("POST", "/api/pin/rotate", {}), ("POST", "/api/pin/unlock", {}),
                ("POST", "/api/access/controller", {"enabled": True, "owner": True}),
                ("POST", "/api/system/settings/export", {}), ("GET", "/api/system/diagnostics", {}),
                ("POST", "/api/system/factory-reset", {"confirm": "reset"}), ("POST", "/api/support/start", {"confirm": "start"}),
                ("POST", "/api/modules/midi", {"enabled": True}), ("POST", "/api/midi/map", {}), ("POST", "/api/osc", {"enabled": True}),
                ("POST", "/api/osc/key", {}), ("POST", "/api/streams", {}), ("POST", "/api/projectors", {})):
            st, out = self.h(method, path, body, op)
            self.assertEqual(st, 403, "%s %s" % (path, out))
            self.assertNotIn(self.pin, json.dumps(out))
        st, out, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=b"{}", token=self.call(
            "POST", "/api/devices/invite", {"name": "op3", "role": "live"}, token=self.full)[1]["token"])
        self.assertEqual(st, 403, out)

    def test_a_code_from_a_controller_pairs_a_guest_now(self):
        self.assertEqual(auth_mod.CONTROLLER_KINDS, {"join": "view", "owner": "full"})
        self.assertTrue(auth_mod.Auth._expires({"role": "view", "via": "controller"}))
        self.assertFalse(auth_mod.Auth._expires({"role": "full", "via": "controller"}))


class UploadTest(RolesBase):
    def upload(self, token, path="/api/media/upload?name=new.mp4"):
        return self.call("POST", path, raw=b"0123", headers={"Content-Type": "application/octet-stream"}, token=token)[0]

    def test_an_operator_uploads_a_clip_and_deletes_it_and_a_guest_does_neither(self):
        op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.upload(self.guest_token), 403)
        self.assertEqual(self.upload(None), 401)
        self.assertEqual(self.upload(op), 200)
        self.assertEqual(self.call("POST", "/api/media/delete", {"name": "new.mp4"}, token=self.guest_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/media/delete", {"name": "new.mp4"}, token=op)[0], 200)
        self.assertEqual(self.upload(op, "/api/system/update/upload?name=u.pvjupdate"), 403)
        self.assertEqual(self.call("GET", "/api/preview.jpg", token=None)[0], 401)
