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
    ("POST", "/api/guests"): STUDIO("op"),
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
    ("POST", "/api/midi"): KINDS["op"],
    ("POST", "/api/midi/learn"): KINDS["op"],
    ("POST", "/api/midi/map"): KINDS["op"],
    ("POST", "/api/midi/lights"): KINDS["op"],
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

# Guest forms whose real handler does not answer 200 on the test rig, and why. Everything else must run to its end,
# so "nothing was saved" is said about handlers that really ran.
def _not_here():
    out = {}
    for path, why in (("/api/effects", 409), ("/api/effects/step", 409), ("/api/effects/preset", 409), ("/api/shaders/play", 409),
                      ("/api/shaders/step", 409), ("/api/shaders/preset", 409),       # the Shaders module is off on this rig
                      ("/api/projector", 404), ("/api/room/group", 404)):             # no projector is added
        for body in GUEST_BODIES[("POST", path)][0]:
            out[(path, json.dumps(body))] = why
    for body in GUEST_BODIES[("POST", "/api/vibes")][0]:
        if body != {"on": False}:
            out[("/api/vibes", json.dumps(body))] = 409
    out[("/api/projector", "off")] = out[("/api/room/group", "off")] = 404
    return out


# What saves the settings in the modules behind those routes, by the function that does it, read from the source.
# None is a guest's form: api_set and api_config and api_presets are the Operator's routes, set_dwell is the "dwell"
# key (refused for a guest by the form), identify is "Refresh details" (refused) or the monitor's own reading.
SAVERS = {"shaders": ["_save"], "effects": [], "vibes": [], "room": ["api_set"], "projector": ["identify"]}
SAVE_CALLERS = {"shaders": ["api_set", "delete"], "effects": ["_edit_presets", "set_detail"], "vibes": []}

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
        for key in (("POST", "/api/support/start"), ("POST", "/api/support/login"), ("POST", "/api/support/stop")):
            self.real_support = getattr(self, "real_support", {})
            self.real_support[key] = real[key][1]
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
        """A support session of this role, made with the real handlers (the table's are stubs by now)."""
        do = self.real_support
        if self.api.support.session is not None:
            do[("POST", "/api/support/stop")]({}, self.owner, LAN)
        body = do[("POST", "/api/support/start")]({"confirm": "start", "role": role}, self.owner, LAN)
        login = do[("POST", "/api/support/login")]({"code": body["code"]}, None, TUNNEL)
        dev = self.api.support.authenticate(login["token"])
        self.assertEqual((dev["role"], dev["remote"]), (role, True))
        return dev

    def test_every_route_has_a_row_and_no_row_is_left_over(self):
        routes = set(self.api.routes()) | set(policy.OUTSIDE) | {("POST", "/api/modules/*")}
        # the paths the server answers by itself are found in its source, so a new one cannot be forgotten
        # (every one of them asks _who(method, path), which is Api.gate with the row of policy.OUTSIDE)
        source = inspect.getsource(server)
        asked = {(m.group(1), path) for m in re.finditer(r'_who\("(GET|POST)", ([^\n]*)', source)
                 for path in re.findall(r'"(/api/[^"]+)"', m.group(2))}
        self.assertEqual(sorted(asked), sorted(policy.OUTSIDE))
        # and a request gets its device in two places only: _who (the gate) and _api (Api.handle, the gate again)
        self.assertEqual(len(re.findall(r"device = auth\.authenticate\(", source)), 2)
        self.assertEqual(sorted(routes - set(ROWS)), [], "routes without a row in tests/test_roles.py: decide who may use them")
        self.assertEqual(sorted(set(ROWS) - routes), [], "rows for routes that are gone")
        self.assertEqual(sorted(set(GUEST_BODIES)), sorted(policy.GUEST))
        self.assertEqual(sorted(k for k, row in ROWS.items() if row[2] == 200 and row[1] == 403), sorted(policy.GUEST))

    def test_the_route_table_and_the_rows_agree_about_the_minimum_role(self):
        floors = {k: need for k, (need, _h) in self.api.routes().items()}
        floors.update(policy.OUTSIDE)
        floors[("POST", "/api/modules/*")] = "full"
        for key, row in ROWS.items():
            need = floors[key]
            want = None if row[0] == 200 else "view" if row[1] == 200 else "live" if row[3] == 200 else "full"
            self.assertEqual(need, want, key)

    def test_who_passes_the_gate_on_every_route(self):
        self.ready()
        self.stub()
        wrong = []

        def check(who, device, client, column, forms=False):
            for (method, path), row in sorted(ROWS.items()):
                bodies = [{}]
                if (forms or who == "guest, open") and (method, path) in GUEST_BODIES:
                    bodies = GUEST_BODIES[(method, path)][0]
                for body in bodies:
                    got = self.ask(method, path, body, device, client)
                    if got != row[column]:
                        wrong.append("%s: %s %s %s answered %s, the row says %s" % (who, method, path, json.dumps(body), got, row[column]))

        check("nobody", None, LAN, 0)
        self.api.guests.set_locked(True)              # not through the API: its handlers are stubs here
        self.assertEqual(self.h("POST", "/api/blackout", {"on": True}, self.guest)[1].get("error"), policy.LOCKED_TEXT)
        check("guest, locked", self.guest, LAN, 1, forms=True)
        self.api.guests.set_locked(False)
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
        def text(name):
            with open(os.path.join(pvj_dir, name)) as f:
                return f.read()
        callers = sorted(n for n in os.listdir(pvj_dir) if n.endswith(".py") and re.search(r"api\.handle\(", text(n)))
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
        self.later()                                 # past the guests' cooldown on blackout
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

    def test_a_factory_reset_opens_it_again(self):
        self.lock(True)
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": boxcare.CONFIRM_RESET, "media": "keep"}, self.owner)
        self.assertEqual(st, 200, out)
        self.assertNotIn("guest_controls", self.settings.data)
        self.assertFalse(self.api.guests.locked())

    def test_a_lock_set_while_a_guests_request_is_on_its_way_leaves_no_confirm_behind(self):
        """The race: the guest's request has passed the first look at the lock when an operator locks. Under the
        controls' own lock it is looked at again, so no token is made after the lock cleared them."""
        guests, real = self.api.guests, self.api.guests.locked
        calls = []

        def locked():
            calls.append(1)
            if len(calls) == 1:                       # the first look says open; the operator locks right after it
                answer = real()
                guests.locked = real
                guests.set_locked(True)
                guests.locked = locked
                return answer
            return real()
        guests.locked = locked
        st, out = self.h("POST", "/api/projector", {"id": "all", "action": "off"}, self.guest)
        guests.locked = real
        self.assertEqual((st, out.get("error")), (403, policy.LOCKED_TEXT))
        self.assertEqual(guests._confirms, {})

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
            for wrong in ("", "x", True, 1, None, [token], token + "x"):      # nine requests in all: under the limit of ten
                self.assertEqual(self.off(path, body, confirm=wrong)[0], 409, wrong)
            self.assertEqual(self.done, [])
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
        body = {"action": "stop"}                # not one of the cooled routes (policy.COOLED)
        for _ in range(policy.GUEST_ACTIONS):
            self.assertEqual(self.h("POST", "/api/control", body, self.guest)[0], 200)
        st, out = self.h("POST", "/api/control", body, self.guest)
        self.assertEqual(st, 429, out)
        self.assertGreaterEqual(out["retry_after"], 1)
        self.assertEqual(self.h("GET", "/api/status", device=self.guest)[0], 200)            # watching is not an action
        self.assertEqual(self.h("POST", "/api/control", body, self.operator)[0], 200)        # an operator is never held up by guests
        self.later()
        self.assertEqual(self.h("POST", "/api/control", body, self.guest)[0], 200)
        # the box: many guests, each under his own limit
        self.later()
        guests = [self.auth.invite("g%d" % i, "view")[1] for i in range(policy.BOX_ACTIONS // 5 + 1)]
        done = 0
        for g in guests:
            for _ in range(5):
                done += self.h("POST", "/api/control", body, g)[0] == 200
        self.assertEqual(done, policy.BOX_ACTIONS)
        self.assertEqual(self.h("POST", "/api/control", body, self.operator)[0], 200)

    def test_what_saves_in_the_modules_behind_the_guest_routes_is_known(self):
        """The rig above cannot run a shader, an effect or a projector to the end, so this is read from the source:
        the functions that save are these and no other, and none is a form a guest may send."""
        import ast
        from pvj import effects, projector, shaders, vibes
        for module in (shaders, effects, vibes, room, projector):
            name = module.__name__.split(".")[-1]
            saving, calling = set(), set()
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for n in ast.walk(node):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                        if n.func.attr == "save":
                            saving.add(node.name)
                        if n.func.attr == "_save":
                            calling.add(node.name)
            self.assertEqual(sorted(saving), SAVERS[name], name)
            if name in SAVE_CALLERS:
                self.assertEqual(sorted(calling), SAVE_CALLERS[name], name)

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
        with self.settings.lock:
            self.settings.data["pads"]["banks"][0]["pads"][0] = {"label": "A", "file": "a.mp4", "ending": "loop"}
            self.settings.save()
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
        other = {k: v for k, v in answers.items() if v != 200}
        self.assertEqual(other, _not_here(), "a guest form that did not run to its end here saved nothing only by chance")
        self.assertEqual(made, [])
        with open(self.settings.path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.h("POST", "/api/mix", {"transition": "cut", "duration": 1}, self.operator)[0], 200)
        self.assertEqual(len(made), 1)                         # the counter does count


class HeldBackTest(RolesBase):
    """What does not rise with the Operator, beside the controllers and support of the table."""
    def test_a_presenter_paired_from_a_controller_before_the_update_is_a_guest(self):
        """The owner, 2026-10-10: "Old presenters are moved up to Guests." The record stays as it was (an older
        release reads it as before); the box treats it as a Guest everywhere, the lock included. Through pairing and
        a token over HTTP, not a device written by hand."""
        token, dev = self.auth._add_device("From the Launchpad", "live", via="controller")
        stored = [d for d in self.settings.data["devices"] if d["id"] == dev["id"]][0]
        self.assertEqual((stored["role"], stored["via"], dev["role"]), ("live", "controller", "view"))
        self.assertEqual(self.auth.authenticate(token)["role"], "view")
        status = self.call("GET", "/api/status", token=token)[1]
        self.assertEqual((status["reach"], status["device"]["role"]), ("guest", "view"))
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)[0], 200)          # a guest's form, the lock open
        for path, body in (("/api/mix", {"transition": "cut", "duration": 1}), ("/api/fadeout", {"seconds": 1}), ("/api/pads", {"bank": 0, "index": 0, "label": "x", "file": "a.mp4"}),
                           ("/api/media/delete", {"name": "a.mp4"}), ("/api/guests", {"locked": True}), ("/api/access/code", {"role": "view"}),
                           ("/api/devices/invite", {"name": "x", "role": "view"}), ("/api/control", {"action": "seek", "value": 5})):
            self.assertEqual(self.call("POST", path, body, token=token)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/devices", token=token)[0], 403)
        self.assertEqual(self.call("GET", "/api/access", token=token)[0], 403)
        self.lock(True)
        self.later()
        st, out, _ = self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)
        self.assertEqual((st, out["error"]), (403, policy.LOCKED_TEXT))
        self.assertEqual(self.call("GET", "/api/status", token=token)[1]["reach"], "watch")
        self.lock(False)
        # the owner sees it as a Guest, and an Operator removes it as one
        listed = {d["id"]: d["role"] for d in self.h("GET", "/api/devices", device=self.owner)[1]["devices"]}
        self.assertEqual(listed[dev["id"]], "view")
        # a presenter the owner invited, by a link or a code, is an Operator
        op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/status", token=op)[1]["reach"], "operator")
        code = self.h("POST", "/api/access/code", {"role": "live"}, self.owner)[1]["codes"][0]["code"]
        joined = self.call("POST", "/api/pair", {"pin": code, "name": "by code"})[1]
        self.assertEqual(self.call("GET", "/api/status", token=joined["token"])[1]["reach"], "operator")
        # an owner paired from a controller stays an owner; the unused ones still go after a week
        self.assertEqual(auth_mod.Auth.role_of({"role": "full", "via": "controller"}), "full")
        self.assertTrue(auth_mod.Auth._expires(stored))
        self.assertFalse(policy.held_to_legacy(self.auth.authenticate(token)))
        self.assertEqual(sorted(k for k in dev), ["created", "id", "name", "role"])              # nothing else is handed out

    def test_the_panel_is_told_the_reach_in_one_word(self):
        def reach(dev, client=LAN):
            return self.h("GET", "/api/status", device=dev, client=client)[1]["reach"]
        self.assertEqual([reach(self.owner), reach(self.operator), reach(self.guest)], ["owner", "operator", "guest"])
        self.lock(True)
        self.assertEqual([reach(self.owner), reach(self.operator), reach(self.guest)], ["owner", "operator", "watch"])
        self.lock(False)
        self.assertEqual(policy.reach({"id": "support-1", "role": "live", "remote": True}, False), "presenter")
        self.assertEqual(policy.reach({"id": "support-1", "role": "view", "remote": True}, False), "watch")   # never guest controls
        self.assertEqual(policy.reach({"id": "support-1", "role": "full", "remote": True}, False), "owner")
        self.assertEqual(policy.reach(midi.MIDI_DEVICE, False), "presenter")
        self.assertEqual(policy.reach(None, False), "watch")

    def test_the_look_pages_details_are_for_an_operator_and_not_for_who_is_held_back(self):
        def has(dev):
            return any("look" in x for x in self.api.get_theme({}, dev, LAN)["available"])
        self.assertTrue(has(self.owner) and has(self.operator))
        self.assertFalse(has(self.guest) or has(midi.MIDI_DEVICE) or has({"id": "support-1", "role": "live", "remote": True}))


class PeopleTest(RolesBase):
    def invite(self, role, by):
        return self.h("POST", "/api/devices/invite", {"name": "new", "role": role}, by)

    def test_an_operator_removes_guests_and_other_operators_never_himself_and_never_an_owner(self):
        """The owner, 2026-10-10: "Operators can remove other operators just not themselves or owners." """
        a_token = self.call("POST", "/api/devices/invite", {"name": "A", "role": "live"}, token=self.full)[1]["token"]
        b_token = self.call("POST", "/api/devices/invite", {"name": "B", "role": "live"}, token=self.full)[1]["token"]
        a, b = self.auth.authenticate(a_token), self.auth.authenticate(b_token)
        self.assertEqual(self.call("GET", "/api/status", token=b_token)[0], 200)
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": self.owner["id"]}, token=a_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": a["id"]}, token=a_token)[0], 403)            # himself
        self.assertEqual(self.call("GET", "/api/status", token=a_token)[0], 200)
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": b["id"]}, token=a_token)[0], 200)
        self.assertEqual(self.call("GET", "/api/status", token=b_token)[0], 401)                                      # dead at once
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=b_token)[0], 401)
        self.assertTrue(any("operator device %s removed by operator device %s" % (b["id"], a["id"]) in line for line in self.lines))
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": self.guest["id"]}, token=a_token)[0], 200)
        self.assertIsNone(self.auth.authenticate(self.guest_token))
        # what is not a paired device cannot be named: a controller, a support session, the box's own callers
        for did in ("midi", "osc", "dmx", "room", "support-1", "*", "", b["id"], ["x"], None):
            self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": did}, a)[0], 404, did)
        self.assertEqual(sorted(d["role"] for d in self.settings.data["devices"]), ["full", "live", "live"])
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": a["id"]}, self.owner)[0], 200)   # the owner still removes anyone
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": self.owner["id"]}, self.guest)[0], 403)

    def test_an_operator_sees_guests_and_operators_and_never_an_owner(self):
        seen = self.h("GET", "/api/devices", device=self.operator)[1]["devices"]
        self.assertEqual(sorted(d["id"] for d in seen), sorted([self.guest["id"], self.operator["id"]]))
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
                ("POST", "/api/modules/midi", {"enabled": True}), ("POST", "/api/access/controller", {"join": "live"}), ("POST", "/api/osc", {"enabled": True}),
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


FAKE_PROFILE = {"controls": [
    {"id": "codepad", "kind": "pad", "send": {"type": "note", "channel": 0, "number": 7}, "action": {"action": "code_join"}, "guard": False},
    {"id": "stoppad", "kind": "pad", "send": {"type": "note", "channel": 0, "number": 1}, "action": {"action": "stop"}, "guard": False},
    {"id": "spare", "kind": "pad", "send": {"type": "note", "channel": 0, "number": 2}, "action": None, "guard": False}]}
PLAIN_PROFILE = {"controls": FAKE_PROFILE["controls"][1:]}


class OwnerAnswersTest(RolesBase):
    """What the owner decided on 2026-10-10 after the first build (D80 has his words)."""
    # -- a controller's join code: a Guest, or an Operator when the Owner chose so --
    def pair_from_controller(self, name="phone"):
        self.auth.create_controller_code("join")
        return self.call("POST", "/api/pair", {"pin": self.auth.controller_digits()[1], "name": name})[1]

    def test_the_join_code_pairs_a_guest_unless_the_owner_chose_an_operator(self):
        ctl = lambda: self.h("GET", "/api/access", device=self.owner)[1]["controller"]
        self.assertEqual(self.h("POST", "/api/access/controller", {"enabled": True}, self.owner)[0], 200)
        self.assertEqual(ctl()["join"], "view")
        self.assertEqual(self.settings.data["controller_code"], {"enabled": True, "owner": False})         # nothing new in the file by default
        first = self.pair_from_controller()
        self.assertEqual(first["device"]["role"], "view")
        # only an Owner, only at the studio, only the two words
        for dev in (self.operator, self.guest):
            self.assertEqual(self.h("POST", "/api/access/controller", {"join": "live"}, dev)[0], 403)
        for junk in ("full", "owner", True, 1, None, ["live"], ""):
            self.assertEqual(self.h("POST", "/api/access/controller", {"join": junk}, self.owner)[0], 400, junk)
        self.assertEqual(ctl()["join"], "view")
        # a code that is on the display when the choice changes is over: it said the other thing
        self.auth.create_controller_code("join")
        shown = self.auth.controller_digits()[1]
        st, out = self.h("POST", "/api/access/controller", {"join": "live"}, self.owner)
        self.assertEqual((st, out["controller"]["join"], out["controller"]["status"]["active"]), (200, "live", False))
        self.assertEqual(self.call("POST", "/api/pair", {"pin": shown, "name": "late"})[0], 403)             # a wrong code, like any other
        self.assertEqual(self.settings.data["controller_code"], {"enabled": True, "owner": False, "join": "live"})
        self.assertTrue(any("the join code pairs an operator" in line for line in self.lines))
        second = self.pair_from_controller("op phone")
        self.assertEqual(second["device"]["role"], "live")
        status = self.call("GET", "/api/status", token=second["token"])[1]
        self.assertEqual(status["reach"], "operator")                                                      # a full Operator: the owner chose it
        self.assertEqual(self.call("POST", "/api/guests", {"locked": False}, token=second["token"])[0], 200)
        stored = [d for d in self.settings.data["devices"] if d["id"] == second["device"]["id"]][0]
        self.assertEqual((stored["via"], stored["join"]), ("controller", "live"))
        self.assertTrue(auth_mod.Auth._expires(stored))                                                    # unused for a week, it goes, like every device from a controller
        # the lock does not flip the choice, and the choice does not flip the lock
        self.lock(True)
        self.assertEqual(ctl()["join"], "live")
        self.assertEqual(self.h("POST", "/api/access/controller", {"join": "view"}, self.owner)[0], 200)
        self.assertTrue(self.api.guests.locked())
        self.assertEqual(self.settings.data["controller_code"], {"enabled": True, "owner": False})
        # who was paired under either choice stays what he was
        self.assertEqual(self.call("GET", "/api/status", token=second["token"])[1]["device"]["role"], "live")
        self.assertEqual(self.call("GET", "/api/status", token=first["token"])[1]["device"]["role"], "view")
        third = self.pair_from_controller("third")
        self.assertEqual(third["device"]["role"], "view")
        # the owner kind is not touched by it, and switching codes off keeps the choice out of the way
        self.assertEqual(self.auth.controller_role("owner"), "full")
        self.assertEqual(auth_mod.controller_setting({"enabled": False, "join": "live"}), {"enabled": False, "owner": False, "join": "live"})
        for junk in ("full", True, 1, ["live"], "view"):
            self.assertNotIn("join", auth_mod.controller_setting({"enabled": True, "join": junk}))

    def test_the_display_says_which_of_the_two_the_code_pairs(self):
        from pvj import pinscreen

        class A:
            role = "view"

            def controller_pairs(self):
                return self.role
        screen = pinscreen.PinScreen.__new__(pinscreen.PinScreen)
        screen.auth = A()
        self.assertEqual(screen._controller_label("join"), "One-time guest code")
        screen.auth.role = "live"
        self.assertEqual(screen._controller_label("join"), "One-time operator code")
        self.assertEqual(screen._controller_label("owner"), "One-time full access code")

    # -- a scene marked "Not for guests" --
    def mark(self, scene, on, by=None):
        s = dict([x for x in self.settings.data["room"]["scenes"] if x["id"] == scene][0], no_guests=on)
        return self.h("POST", "/api/room", {"scene": s}, by or self.operator)

    def test_a_scene_marked_not_for_guests_is_refused_however_a_guest_names_it(self):
        self.assertFalse(any("no_guests" in s for s in self.settings.data["room"]["scenes"]))             # every scene as it was
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, self.guest)[0], 200)
        self.assertEqual(self.mark(SCENE, True, self.guest)[0], 403)
        st, out = self.mark(SCENE, True)
        self.assertEqual(st, 200, out)
        self.assertIs(self.settings.data["room"]["scenes"][0]["no_guests"], True)
        for body in ({"scene": SCENE}, {"number": 1}, {"name": "Show"}, {"name": " SHOW "}, {"scene": SCENE, "number": 3}, {"scene": SCENE, "confirm": "x"}):
            self.later()
            st, out = self.h("POST", "/api/room/scene", body, self.guest)
            self.assertEqual((st, out["error"]), (403, "this scene is not for guests; ask an operator to start it"), body)
        self.assertEqual([s["id"] for s in self.h("GET", "/api/room", device=self.guest)[1]["scenes"]], [STREAM_SCENE, OFF_SCENE])
        for dev in (self.operator, self.owner):
            self.assertEqual(len(self.h("GET", "/api/room", device=dev)[1]["scenes"]), 3)
            self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, dev)[0], 200)
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, room.ROOM_DEVICE, "schedule")[0], 200)   # the schedule and controllers as before
        # the switch-off scene marked: no confirm is even offered
        self.assertEqual(self.mark(OFF_SCENE, True)[0], 200)
        self.later()
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": OFF_SCENE}, self.guest)[0], 403)
        # taken off again, the key is gone from the file, and the scene is a guest's again
        self.assertEqual(self.mark(SCENE, False)[0], 200)
        self.assertNotIn("no_guests", self.settings.data["room"]["scenes"][0])
        self.later()
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, self.guest)[0], 200)
        for junk in (1, "yes", None, [True]):
            self.assertEqual(self.mark(SCENE, junk)[0], 400, junk)
        # the stream scene is refused with or without a mark
        self.later()
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": STREAM_SCENE}, self.guest)[0], 403)

    # -- MIDI is the Operator's, but never a control that shows an access code --
    def midi_on(self, profile=FAKE_PROFILE):
        hub = midi.MidiHub(self.api, self.settings, log=lambda *_: None, open_fn=lambda p: (_ for _ in ()).throw(OSError()),
                           lister=lambda: [], scan_interval=0.05)
        self.api.midi = hub
        self.addCleanup(hub.stop)
        self.assertEqual(self.h("POST", "/api/modules/control-midi", {"enabled": True}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"enabled": True}, self.owner)[0], 200)
        self.api.midi.profile_of = lambda name: profile if name == "Mini" else None
        self.api.midi.known_sources = lambda: {"Mini"}

    def owner_maps(self, action="code_owner", number=40):
        st, out = self.h("POST", "/api/midi/map", {"add": {"source": "*", "kind": "note", "number": number, "action": action}}, self.owner)
        self.assertEqual(st, 200, out)
        return [e for e in out["map"] if e["number"] == number][0]

    def test_an_operator_edits_the_midi_layout(self):
        self.midi_on()
        op = self.operator
        st, out = self.h("POST", "/api/midi/map", {"add": {"source": "*", "kind": "cc", "number": 3, "action": "opacity"}}, op)
        self.assertEqual(st, 200, out)
        eid = out["map"][0]["id"]
        self.assertEqual(self.h("POST", "/api/midi/map", {"set": {"controller": "Mini", "control": "stoppad", "action": {"action": "blackout"}}}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/map", {"set": {"controller": "Mini", "control": "spare", "action": {"action": "pause"}}}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/map", {"reset": {"controller": "Mini", "control": "stoppad"}}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/map", {"remove": eid}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"enabled": True}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/learn", {"start": True}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/learn", {"start": False}, op)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"controller": "Mini", "lights": True}, op)[0], 200)
        for dev in (self.guest, midi.MIDI_DEVICE, osc.OSC_DEVICE):
            self.later()
            self.assertEqual(self.h("POST", "/api/midi/map", {"clear": True}, dev)[0], 403)
        self.assertEqual(self.h("POST", "/api/modules/control-midi", {"enabled": False}, op)[0], 403)      # the module's switch stays the Owner's

    def test_an_operator_never_sets_changes_or_removes_a_control_that_shows_an_access_code(self):
        """Every way in: a posted mapping, what Learn found, the drawn layout's own control, an existing mapping edited,
        removed, cleared or reset away, the standard layout switched, a whole map sent to another route, an import."""
        self.midi_on()
        op, refused = self.operator, self.api.OWNER_MAPPING
        mine = self.owner_maps("code_owner", 40)
        join = self.owner_maps("code_join", 41)
        kept = lambda: [e for e in self.settings.data["control"]["midi"]["map"] if e["action"].startswith("code_")]
        before = json.dumps(kept(), sort_keys=True)
        self.assertEqual(sorted(a for a in midi.ACTIONS if self.api._owner_action(a)), ["code_join", "code_owner"])

        def no(path, body, status=403):
            st, out = self.h("POST", path, body, op)
            self.assertEqual(st, status, "%s %s: %s" % (path, body, out))
            if status == 403:
                self.assertIn("owner access needed", out["error"])
            self.assertEqual(json.dumps(kept(), sort_keys=True), before, body)
        for action in ("code_owner", "code_join"):
            no("/api/midi/map", {"add": {"source": "*", "kind": "note", "number": 50, "action": action}})                 # a posted mapping
            no("/api/midi/map", {"set": {"controller": "Mini", "control": "spare", "action": {"action": action}}})       # on the drawn layout
            no("/api/midi/map", {"set": {"controller": "Mini", "control": "stoppad", "action": {"action": action}}})
        # by Learn: Learn only finds the control; the mapping is the same posted one
        self.assertEqual(self.h("POST", "/api/midi/learn", {"start": True}, op)[0], 200)
        no("/api/midi/map", {"add": {"source": "Mini", "kind": "note", "number": 9, "action": "code_owner"}})
        # the layout's own code pad: not given another action, not switched off
        no("/api/midi/map", {"set": {"controller": "Mini", "control": "codepad", "action": {"action": "stop"}}})
        no("/api/midi/map", {"set": {"controller": "Mini", "control": "codepad", "action": {"action": "none"}}})
        no("/api/midi", {"controller": "Mini", "standard": False})
        no("/api/midi", {"controller": "Mini", "standard": True})
        no("/api/midi", {"controller": "Gone", "standard": False})                                                       # not plugged in: nothing to judge by
        # the owner's existing mappings: not removed, not replaced, not reset away
        no("/api/midi/map", {"remove": mine["id"]})
        no("/api/midi/map", {"remove": join["id"]})
        no("/api/midi/map", {"add": dict(mine, action="code_owner", number=60)})                                         # an id of his own choosing changes nothing
        no("/api/midi/map", {"add": {"source": "*", "kind": "note", "number": 40, "action": "stop"}})                    # a mapping on the same note would take the owner's place
        st, out = self.h("POST", "/api/midi/map", {"clear": True}, op)                                                   # his Clear leaves the owner's
        self.assertEqual((st, json.dumps([e for e in out["map"]], sort_keys=True)), (200, before))
        # an owner's override of a drawn control with a code action: the Operator cannot put something else there
        st, out = self.h("POST", "/api/midi/map", {"set": {"controller": "Mini", "control": "spare", "action": {"action": "code_owner"}}}, self.owner)
        self.assertEqual(st, 200, out)
        before = json.dumps(kept(), sort_keys=True)
        no("/api/midi/map", {"set": {"controller": "Mini", "control": "spare", "action": {"action": "stop"}}})
        no("/api/midi/map", {"reset": {"controller": "Mini", "control": "spare"}})
        no("/api/midi/map", {"reset": {"controller": "Mini"}})
        # a whole map sent where switches are expected is not read; a settings file is the owner's to load
        st, out = self.h("POST", "/api/midi", {"enabled": True, "map": [dict(mine, id="0a0a0a0a", number=70)]}, op)
        self.assertEqual(json.dumps(kept(), sort_keys=True), before)
        op_token = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("POST", "/api/system/settings/import?confirm=import", raw=b"{}", token=op_token)[0], 403)
        self.assertEqual(self.h("POST", "/api/access/controller", {"enabled": True, "owner": True}, op)[0], 403)        # nor the switches that make the pad do anything
        # he sees them (read only), and the owner still does all of it
        self.assertEqual(len([e for e in self.h("GET", "/api/midi", device=op)[1]["map"] if e["action"].startswith("code_")]), 3)
        self.assertEqual(self.h("POST", "/api/midi/map", {"remove": mine["id"]}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"controller": "Mini", "standard": False}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi/map", {"clear": True}, self.owner)[1]["map"], [])

    def test_a_layout_without_a_code_control_is_the_operators_to_switch(self):
        self.midi_on(PLAIN_PROFILE)
        self.assertEqual(self.h("POST", "/api/midi", {"controller": "Mini", "standard": False}, self.operator)[0], 200)
        self.assertEqual(self.h("POST", "/api/midi", {"controller": "Mini", "standard": True}, self.operator)[0], 200)

    def test_what_an_operator_maps_does_no_more_than_a_presenter_could(self):
        """A mapped control calls the API as "midi", which is held to the list of before D80: nothing an Operator
        gained, and nothing an Operator lacks, can be put on a pad."""
        self.assertTrue(policy.held_to_legacy(midi.MIDI_DEVICE))
        for key in (("POST", "/api/midi/map"), ("POST", "/api/midi"), ("POST", "/api/guests"), ("POST", "/api/pads"), ("POST", "/api/access/controller")):
            self.assertNotIn(key, policy.LEGACY_LIVE)
            self.assertEqual(self.h(key[0], key[1], {}, midi.MIDI_DEVICE, "midi")[0], 403)


class AttackTest(RolesBase):
    """What was tried against it, as tests. The write-up is in project-log/JOURNAL.md (2026-10-10, D80)."""
    def test_a_scene_cannot_be_named_one_way_and_checked_another(self):
        """The guest's form looks the scene up exactly as the handler does (room._pick: "scene" before "number" before
        "name"), so a harmless id beside the number of the stream scene or of the switch-off scene is the harmless
        scene for both, and the other way round is refused or asks for the confirm."""
        done = []
        real = self.api.routes()
        table = dict(real)
        table[("POST", "/api/room/scene")] = ("live", lambda b, d, c: done.append(self.api.room._pick(self.api.room.config()["scenes"], b, "scene", "scene")["id"]) or {})
        self.api.routes = lambda: table
        g = self.guest
        real_h = self.h

        def h(*a, **kw):                             # each try on its own: this is not about the limits
            self.later()
            return real_h(*a, **kw)
        self.h = h
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE, "number": 2}, g)[0], 200)        # 2 is the stream scene
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE, "name": "Good night"}, g)[0], 200)
        self.assertEqual(done, [SCENE, SCENE])
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": STREAM_SCENE, "number": 1}, g)[0], 403)
        self.assertEqual(self.h("POST", "/api/room/scene", {"number": 2, "name": "Show"}, g)[0], 403)
        self.assertEqual(self.h("POST", "/api/room/scene", {"name": " from OUTSIDE "}, g)[0], 403)            # names are matched loosely by the handler too
        self.assertEqual(self.h("POST", "/api/room/scene", {"number": 3, "name": "Show"}, g)[0], 409)         # 3 switches off
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": ["x"], "name": "Show"}, g)[0], 404)      # as for anyone
        self.assertEqual(done, [SCENE, SCENE])
        # the confirm of one way of naming the scene is not the confirm of another
        token = real_h("POST", "/api/room/scene", {"scene": OFF_SCENE}, g)[1]["confirm"]["token"]
        self.assertEqual(real_h("POST", "/api/room/scene", {"name": "Good night", "confirm": token}, g)[0], 409)
        self.assertEqual(done, [SCENE, SCENE])

    def test_a_scene_an_operator_edits_between_a_guests_two_taps(self):
        """Known and written down (SECURITY.md): the confirm is for the request, not for what the scene holds. A scene
        that gained a stream meanwhile is refused at the second tap all the same, because the form is checked again."""
        g = self.guest
        token = self.h("POST", "/api/room/scene", {"scene": OFF_SCENE}, g)[1]["confirm"]["token"]
        with self.settings.lock:
            self.settings.data["room"]["scenes"][2]["box"] = {"action": "stream", "stream": "0f0f0f0f"}
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": OFF_SCENE, "confirm": token}, g)[0], 403)

    def test_a_pad_holds_a_clip_of_the_box_and_nothing_else(self):
        """A guest may play a pad, so a pad must never be a way to a stream, a drive or a file outside the media folder:
        the Operator cannot store one, and one written into the settings by hand is refused when played."""
        for file in ("srt://203.0.113.9:9000", "http://203.0.113.9/a.mp4", "../secret.mp4", "/etc/passwd", "STICK/a.mp4", "a.txt"):
            st, out = self.h("POST", "/api/pads", {"bank": 0, "index": 0, "label": "x", "file": file}, self.operator)
            self.assertEqual(st, 400, file)
            with self.settings.lock:
                self.settings.data["pads"]["banks"][0]["pads"][0] = {"label": "x", "file": file, "ending": "loop"}
            self.later()
            self.assertIn(self.h("POST", "/api/play", {"pad": [0, 0]}, self.guest)[0], (400, 404), file)
        for body in ({"pad": [0, 0], "usb": "STICK/a.mp4"}, {"pad": [0, 0], "stream": "x"}, {"pad": [0, 0], "preset": "p"}, {"file": "a.mp4", "usb": "STICK/a.mp4"},
                     {"file": "../secret.mp4"}, {"file": "http://203.0.113.9/a.mp4"}):
            self.later()
            self.assertIn(self.h("POST", "/api/play", body, self.guest)[0], (400, 403, 404), body)

    def test_a_guest_cannot_say_he_is_someone_else(self):
        g = self.guest
        for extra in ({"role": "full"}, {"device": {"role": "full"}}, {"remote": True}, {"id": "midi"}, {"confirm": "x"}):
            self.later()
            self.assertEqual(self.h("POST", "/api/mix", dict({"transition": "cut", "duration": 1}, **extra), g)[0], 403, extra)
            self.assertEqual(self.h("POST", "/api/guests", dict({"locked": False}, **extra), g)[0], 403, extra)
        self.lock(True)
        for extra in ({"confirm": "x"}, {"locked": False}, {"force": True}):
            self.assertEqual(self.h("POST", "/api/blackout", dict({"on": True}, **extra), g)[0], 403, extra)
        # a device record cannot be given a controller's id or a higher role through the API
        st, out = self.h("POST", "/api/devices/invite", {"name": "midi", "role": "view", "id": "midi", "via": "controller"}, self.operator)
        self.assertEqual(st, 200, out)
        self.assertRegex(out["device"]["id"], r"^[0-9a-f]{8}$")
        self.assertNotIn("via", [d for d in self.settings.data["devices"] if d["id"] == out["device"]["id"]][0])

    def test_an_operator_finds_no_way_up(self):
        op, before = self.operator, json.dumps(self.settings.data["devices"], sort_keys=True)
        tries = (("/api/devices/invite", {"name": "x", "role": "live"}), ("/api/devices/invite", {"name": "x", "role": "full"}),
                 ("/api/devices/invite", {"name": "x", "role": ["view", "full"]}), ("/api/devices/invite", {"name": "x"}),
                 ("/api/devices/revoke", {"id": self.owner["id"]}), ("/api/devices/revoke", {"id": op["id"]}),
                 ("/api/access/code", {"role": "live"}), ("/api/access/code", {"role": "full"}), ("/api/access/code", {"role": "view", "minutes": 100000}),
                 ("/api/access/screen", {"show": True, "items": ["pin"]}), ("/api/access/screen", {"show": True, "items": ["view", "live"]}),
                 ("/api/access/controller", {"enabled": True}), ("/api/pin/show", {}), ("/api/modules/control-osc", {"enabled": True}),
                 ("/api/osc", {"enabled": True, "paired": False}), ("/api/dmx", {"enabled": True}), ("/api/access/controller", {"join": "live"}),
                 ("/api/modules/control-midi", {"enabled": True}), ("/api/support/start", {"confirm": "start", "role": "full"}),
                 ("/api/support/config", {"allowed": True}), ("/api/sync", {"role": "server"}), ("/api/system/update", {}),
                 ("/api/system/clock", {"time": "2030-01-01T00:00:00"}), ("/api/network/plan", {}), ("/api/theme/add", {"file": "{}"}),
                 ("/api/streams", {"action": "add", "name": "x", "url": "srt://203.0.113.9:9000"}),
                 ("/api/projectors", {"add": {"host": "192.168.0.5"}}))
        for path, body in tries:
            st, out = self.h("POST", path, body, op)
            # 404: this rig has no room screen to draw on (with one, tests/test_join.py has the 403 for these two)
            self.assertIn(st, (400, 403, 404) if path == "/api/access/screen" else (400, 403), "%s %s: %s" % (path, body, out))
        self.assertEqual(json.dumps(self.settings.data["devices"], sort_keys=True), before)
        self.assertEqual(self.auth.list_joins(), [])
        # what he may store runs with the controllers' reach, never his own: a scene and the schedule act as "room"
        self.assertEqual(room.ROOM_DEVICE["id"], "room")
        self.assertTrue(policy.held_to_legacy(room.ROOM_DEVICE))
        self.assertNotIn(("POST", "/api/room"), policy.LEGACY_LIVE)
        self.assertNotIn(("POST", "/api/guests"), policy.LEGACY_LIVE)

    def test_guests_and_the_lock_at_the_same_moment(self):
        """Eight guests asking for the switch-off confirm while the lock goes on and off 60 times: whenever the lock
        is on, no confirm is waiting, and a request that began after the lock was set is refused."""
        import threading
        guests = [self.auth.invite("g%d" % i, "view")[1] for i in range(8)]
        controls = policy.GuestControls(self.api)             # the real clock: no limit is reached in so short a time per device
        self.api.guests = controls
        stop, bad = threading.Event(), []

        def ask(dev):
            while not stop.is_set():
                try:
                    controls.admit("POST", "/api/projector", {"id": "all", "action": "off"}, dev, LAN)
                except policy.Refused:
                    pass
        threads = [threading.Thread(target=ask, args=(g,)) for g in guests]
        for t in threads:
            t.start()
        try:
            for n in range(60):
                controls.set_locked(True)
                with controls._lock:
                    if controls._confirms:
                        bad.append(n)
                try:
                    controls.admit("POST", "/api/blackout", {"on": True}, guests[0], LAN)
                    bad.append("let in while locked, round %d" % n)
                except policy.Refused as e:
                    if e.status != 403:
                        bad.append("round %d: %s" % (n, e.status))
                controls.set_locked(False)
        finally:
            stop.set()
            for t in threads:
                t.join(5)
        self.assertEqual(bad, [])


class ReviewTest(RolesBase):
    """The Lows of the independent review of #120 at 571d2ac, each seen to fail before its fix."""
    def test_a_switch_off_that_did_not_happen_is_not_counted_and_not_logged(self):
        """No projector is added on this rig, so the handler answers 404: nothing was switched off. The journal does
        not say it was, and the guest's one switch-off in five minutes is not spent."""
        path, body = "/api/projector", {"id": "all", "action": "off"}
        for _ in range(3):
            self.later()
            st, out = self.h("POST", path, body, self.guest)
            self.assertEqual(st, 409, out)
            self.assertEqual(self.h("POST", path, dict(body, confirm=out["confirm"]["token"]), self.guest)[0], 404)
        self.assertEqual([line for line in self.lines if "guest " in line and "/api/projector" in line], [])
        self.assertFalse(any(self.api.guests._offs.values()), self.api.guests._offs)
        # and one that failed with something other than an API error is given back too
        table = dict(self.api.routes())

        def broken(b, d, c):
            raise RuntimeError("boom")
        table[("POST", path)] = ("live", broken)
        self.api.routes = lambda: table
        self.later()
        token = self.h("POST", path, body, self.guest)[1]["confirm"]["token"]
        with self.assertRaises(RuntimeError):
            self.h("POST", path, dict(body, confirm=token), self.guest)
        self.assertFalse(any(self.api.guests._offs.values()))

    def test_guests_cannot_flash_the_room(self):
        """Play, blackout and a scene: one of them for all guests together in policy.COOL_SECONDS. An Operator and an
        Owner are never slowed by it, a request that failed does not start it, and Next, Stop and the rest are not in it."""
        other = self.auth.invite("g2", "view")[1]
        cooled = (("/api/blackout", {"on": True}), ("/api/play", {"file": "a.mp4"}), ("/api/room/scene", {"scene": SCENE}))
        self.assertEqual(sorted(("POST", p) for p, _b in cooled), sorted(policy.COOLED))
        for n, (path, body) in enumerate(cooled):
            self.later()
            self.assertEqual(self.h("POST", path, body, self.guest)[0], 200, path)
            for who in (self.guest, other):
                for again, send in cooled:
                    st, out = self.h("POST", again, send, who)
                    self.assertEqual(st, 429, "%s after %s: %s" % (again, path, out))
                    self.assertGreaterEqual(out["retry_after"], 1)
            self.assertEqual(self.h("POST", "/api/control", {"action": "stop"}, other)[0], 200)      # not one of the three
            for dev in (self.operator, self.owner):
                self.assertEqual(self.h("POST", path, body, dev)[0], 200, path)
            self.gclock.t += policy.COOL_SECONDS
            self.assertEqual(self.h("POST", path, body, other)[0], 200, path)
        self.later()
        self.assertEqual(self.h("POST", "/api/play", {"file": "missing.mp4"}, self.guest)[0], 404)
        self.assertEqual(self.h("POST", "/api/blackout", {"on": False}, self.guest)[0], 200)

    def test_the_lock_is_never_changed_through_the_support_tunnel(self):
        self.assertIn(("POST", "/api/guests"), sp.REMOTE_DENY)
        self.ready()
        for role in ("live", "full"):
            code = self.start(role=role)[1]["code"]
            dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
            for locked in (True, False):
                self.assertEqual(self.h("POST", "/api/guests", {"locked": locked}, dev, TUNNEL)[0], 403, role)
            self.h("POST", "/api/support/stop", device=self.owner)
        self.assertFalse(self.api.guests.locked())
        self.assertEqual(self.h("POST", "/api/guests", {"locked": True}, self.owner)[0], 200)      # at the studio, as before

    def test_every_api_path_the_server_names_is_in_the_route_table_or_in_the_list_of_its_own(self):
        """The gate is asked by `_who` for the paths server.py answers itself; a new branch there that forgets `_who`
        would be a path with no check. Every "/api/..." in its source must be a route (answered by Api.handle, which is
        the gate) or a key of policy.OUTSIDE (and then `_who` is asked for it: see the test of the table)."""
        known = {p for _m, p in self.api.routes()} | {p for _m, p in policy.OUTSIDE}
        named = set(re.findall(r'"(/api/[^"]+)"', inspect.getsource(server)))
        self.assertGreaterEqual(len(named), 9)
        self.assertEqual(sorted(named - known), [])
        for path in sorted(p for _m, p in policy.OUTSIDE):
            self.assertIn(path, named)

    def test_a_refused_form_counts_as_an_action(self):
        for _ in range(policy.GUEST_ACTIONS):
            self.assertEqual(self.h("POST", "/api/control", {"action": "seek", "value": 5}, self.guest)[0], 403)
        st, out = self.h("POST", "/api/control", {"action": "stop"}, self.guest)
        self.assertEqual(st, 429, out)
        self.later()
        for _ in range(policy.GUEST_ACTIONS):                                   # a scene that is not there, asked for again and again
            self.assertEqual(self.h("POST", "/api/room/scene", {"name": "nope"}, self.guest)[0], 404)
        self.assertEqual(self.h("POST", "/api/room/scene", {"name": "nope"}, self.guest)[0], 429)
        self.assertEqual(self.h("POST", "/api/control", {"action": "stop"}, self.operator)[0], 200)

    def test_a_device_that_went_quiet_leaves_nothing_in_the_counters(self):
        controls = self.api.guests
        guests = [self.auth.invite("g%d" % i, "view")[1] for i in range(5)]
        for g in guests:
            self.assertEqual(self.h("POST", "/api/control", {"action": "stop"}, g)[0], 200)
        self.assertEqual(len(controls._acts), 6)
        self.later()
        self.assertEqual(self.h("POST", "/api/control", {"action": "stop"}, self.guest)[0], 200)
        self.assertEqual(sorted(controls._acts), sorted(["*", self.guest["id"]]))
        self.assertFalse(any(not times for times in list(controls._acts.values()) + list(controls._offs.values())))

    def test_head_and_the_servers_own_paths_over_http(self):
        op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]
        for path in ("/api/status", "/api/devices", "/api/preview.jpg", "/api/qr.svg?for=panel", "/api/system/diagnostics"):
            self.assertEqual(self.call("HEAD", path)[0], 401, path)
        self.assertEqual(self.call("HEAD", "/api/status", token=self.guest_token)[0], 200)
        for path in ("/api/devices", "/api/qr.svg?for=panel", "/api/system/diagnostics", "/api/network"):
            self.assertEqual(self.call("HEAD", path, token=self.guest_token)[0], 403, path)
        for path in ("/api/system/diagnostics", "/api/network"):
            self.assertEqual(self.call("HEAD", path, token=op)[0], 403, path)
        # a guest, with guest controls open, on the paths the server answers itself
        octet = {"Content-Type": "application/octet-stream"}
        self.assertEqual(self.call("GET", "/api/qr.svg?for=panel", token=self.guest_token)[0], 403)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=self.guest_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/media/upload?name=g.mp4", raw=b"0123", headers=octet, token=self.guest_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/system/update/upload?name=u.pvjupdate", raw=b"0123", headers=octet, token=self.guest_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/system/settings/import?confirm=import", raw=b"{}", token=self.guest_token)[0], 403)
        self.api.preview_jpeg = lambda device: b"jpeg"          # the rig's player takes no snapshot
        self.assertEqual(self.call("GET", "/api/preview.jpg", token=self.guest_token)[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.media, "g.mp4")))


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
