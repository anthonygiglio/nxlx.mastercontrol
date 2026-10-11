# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What a remote support session may do (D84): with the Owner's role, anything that ends when the session ends, and
nothing that outlives it or locks the owner out. Sessions below the Owner's role are as they were (D80).

The table of every route against a support session of each role is tests.test_roles.ROWS (columns 5 to 7), asked
through the gate there; a route without a row fails there. Here: the two lists as data, held against that table, and
every opened and every refused route asked with the real handlers."""
import json
import os
from unittest import mock

from pvj import boxcare, osc, policy, support as sp, themes
from pvj.api import ApiError
from tests.test_boxcare import Base as CareBase
from tests.test_roles import ROWS, WHO
from tests.test_support import LAN, TUNNEL
from tests.test_theme_api import mine

VIEW, LIVE, FULL = WHO.index("support view"), WHO.index("support live"), WHO.index("support full")
OWNER = WHO.index("owner")

# REFUSED to every support session, whatever its role. Written out by hand: a new route under a refused prefix, or a
# new entry in support.REMOTE_DENY, must be added here with a thought (the test below says which list it is missing from).
REFUSED = {
    # pairing and sessions, invitations
    ("POST", "/api/pair"), ("POST", "/api/session"), ("POST", "/api/devices/invite"),
    # everything under /api/access: codes, links, what is shown on the display, the controller code choice
    ("GET", "/api/access"), ("POST", "/api/access/code"), ("POST", "/api/access/cancel"), ("POST", "/api/access/screen"),
    ("POST", "/api/access/controller"),
    # the PIN and the QR code
    ("POST", "/api/pin/show"), ("POST", "/api/pin/rotate"), ("POST", "/api/pin/unlock"), ("GET", "/api/qr.svg"),
    # the ways back in
    ("GET", "/api/recovery"), ("POST", "/api/recovery/codes"), ("POST", "/api/recovery/cancel"), ("POST", "/api/recovery/usb"),
    # the secure connection
    ("GET", "/api/https"), ("GET", "/api/https/request.csr"), ("GET", "/api/https/root.crt"), ("POST", "/api/https/request"),
    ("POST", "/api/https/certificate"), ("POST", "/api/https/undo"), ("POST", "/api/https/remove"), ("POST", "/api/https/root"),
    ("POST", "/api/https/owner-only"),
    # the session's own settings, start and length
    ("POST", "/api/support/config"), ("POST", "/api/support/start"), ("POST", "/api/support/extend"),
    # power-off and factory reset
    ("POST", "/api/system/poweroff"), ("POST", "/api/system/factory-reset"),
    # the confirm of a network change: only someone at the studio can show that the studio still reaches the box
    ("POST", "/api/network/confirm"),
}
# OPENED by D84 to a support session of the Owner's role: route -> a body the real handler takes.
OPENED = {
    ("POST", "/api/system/settings/import"): None,          # answered by the server itself: asked through boxcare below
    ("POST", "/api/theme/add"): {"file": json.dumps(mine(id="helper"))},
    ("POST", "/api/theme/remove"): {"id": "helper"},
    ("POST", "/api/osc/key"): {},
    ("POST", "/api/osc/feedback"): {"on": True},
    ("POST", "/api/guests"): {"locked": True},
    ("POST", "/api/devices/revoke"): None,                  # the body names a device: asked below
}


class Base(CareBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.support.log = self.lines.append
        self.api.log = self.lines.append
        self.addCleanup(self.api.osc.stop)
        self.api.theme_store = themes.Store(os.path.join(os.path.dirname(self.settings.path), "addons"))
        self.ready()

    def session(self, role="full"):
        if self.api.support.session is not None:
            self.h("POST", "/api/support/stop", device=self.full_dev)
        st, body = self.start(role=role)
        self.assertEqual(st, 200, body)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        dev = self.api.support.authenticate(login["token"])
        self.assertEqual((dev["role"], dev["remote"]), (role, True))
        return dev

    def did(self):
        return [d["what"] for d in self.settings.data["support_log"][0].get("did", [])]

    def remote(self, method, path, body, dev):
        if (method, path) in policy.OUTSIDE and (method, path) not in self.api.routes():      # answered by the server itself: its gate
            try:
                self.api.gate(method, path, body, dev, TUNNEL)
                return 200, {}
            except ApiError as e:
                return e.status, {"error": e.message}
        return self.h(method, path, body, dev, TUNNEL)


class TwoListsTest(Base):
    def test_the_refused_list_is_exactly_what_the_guard_refuses_and_every_entry_says_why(self):
        by_guard = {k for k in ROWS if k in sp.REMOTE_DENY or k[1].startswith(sp.REMOTE_DENY_PREFIX)}
        self.assertEqual(sorted(by_guard ^ REFUSED), [], "a route is refused by support.py and not listed here, or the other way round")
        self.assertEqual(sorted(set(sp.REMOTE_DENY) - set(ROWS)), [], "a refused route that does not exist")
        for table in (sp.REMOTE_DENY, sp.REMOTE_DENY_PREFIX_WHY, sp.REMOTE_OWNER_OPENED):
            for key, why in table.items():
                self.assertTrue(isinstance(why, str) and len(why) > 20, key)
        self.assertEqual(sp.REMOTE_DENY_PREFIX, tuple(sp.REMOTE_DENY_PREFIX_WHY))

    def test_the_two_lists_against_the_table_of_every_route(self):
        """tests.test_roles.ROWS is the table of every route and every asker (a route without a row fails there)."""
        self.assertEqual(sorted(OPENED), sorted(sp.REMOTE_OWNER_OPENED))
        self.assertEqual(set(OPENED) & REFUSED, set())
        for key, row in sorted(ROWS.items()):
            if key in REFUSED:
                self.assertEqual(row[VIEW:FULL + 1], (403, 403, 403), key)
            else:
                # everything else: a support session of the Owner's role is answered as the Owner at the studio is
                self.assertEqual(row[FULL], row[OWNER], key)
            # below the Owner's role nothing changed (D80): what a presenter had, and never a refused route
            self.assertEqual(row[LIVE] == 200, key in policy.LEGACY_LIVE and key not in REFUSED or key in sp.REMOTE_OPEN or row[0] == 200 and key not in REFUSED, key)
        for key in OPENED:
            self.assertEqual(ROWS[key][VIEW:FULL + 1], (403, 403, 200), key)
            self.assertNotIn(key, policy.LEGACY_LIVE)

    def test_every_refused_route_is_refused_by_the_real_handlers_to_every_role(self):
        for role in ("view", "live", "full"):
            dev = self.session(role)
            for method, path in sorted(REFUSED - {("GET", "/api/qr.svg"), ("POST", "/api/pair"), ("POST", "/api/session")}):
                st = self.remote(method, path, {"confirm": "start", "minutes": 60, "allowed": True, "media": "keep", "on": True}, dev)[0]
                self.assertEqual(st, 403, (role, method, path))
            for method, path in (("GET", "/api/qr.svg"), ("POST", "/api/pair"), ("POST", "/api/session")):
                with self.assertRaises(sp.SupportApiError):
                    self.api.support.guard(method, path, dev, TUNNEL)
            self.assertTrue(self.api.support.session, "and none of that ended the session")
        self.assertEqual(self.did(), [], "nothing was changed, so nothing is in the log")

    def test_every_opened_route_is_answered_by_its_real_handler_to_the_owners_role_only(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]}]
        for role in ("view", "live"):
            dev = self.session(role)
            for (method, path), body in sorted(OPENED.items()):
                if body is not None:
                    self.assertEqual(self.remote(method, path, body, dev)[0], 403, (role, path))
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.view_dev["id"]}, dev)[0], 403)
            with self.assertRaises(ApiError) as e:
                self.api.gate("POST", "/api/system/settings/import", {}, dev, TUNNEL)
            self.assertEqual(e.exception.status, 403)
            with self.assertRaises(ApiError) as e:          # and the handler itself, should the table ever change
                self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
            self.assertEqual(e.exception.status, 403)
            for call, body in ((self.api.add_theme, OPENED[("POST", "/api/theme/add")]), (self.api.remove_theme, {"id": "x"}),
                               (self.api.osc_key, {}), (self.api.osc_feedback, {"on": True}), (self.api.revoke, {"id": self.view_dev["id"]})):
                with self.assertRaises(ApiError) as e:
                    call(body, dev, TUNNEL)
                self.assertEqual(e.exception.status, 403, (role, call.__name__))
            self.assertEqual(self.did(), [])
        dev = self.session("full")
        for (method, path), body in OPENED.items():        # in the order written: the theme is added before it is removed
            if body is not None:
                st, out = self.remote(method, path, body, dev)
                self.assertEqual(st, 200, (path, out))
        self.assertIsNone(self.api.gate("POST", "/api/system/settings/import", None, dev, TUNNEL))
        # a studio device's own token through the tunnel is still no login (the guard), opened route or not
        for (method, path), body in OPENED.items():
            if body is not None:
                self.assertEqual(self.h(method, path, body, self.full_dev, TUNNEL)[0], 403, path)
        # and a support login used from the studio's network is none either
        self.assertEqual(self.h("POST", "/api/guests", {"locked": False}, dev, LAN)[0], 403)


class LogTest(Base):
    def test_everything_a_session_changes_is_in_the_support_log_with_what_was_done(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]}]
        dev = self.session("full")
        self.assertEqual(self.remote("POST", "/api/theme/add", OPENED[("POST", "/api/theme/add")], dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/theme/remove", {"id": "helper"}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/osc/key", {"new": True}, dev)[0], 200)
        st, key = self.remote("POST", "/api/osc/key", {}, dev)
        self.assertEqual(st, 200, key)
        self.assertEqual(self.remote("POST", "/api/osc/feedback", {"add": {"name": "Desk", "address": "192.168.1.77", "port": 9000}}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/guests", {"locked": True}, dev)[0], 200)
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.view_dev["id"]}, dev)[0], 200)
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        did = self.did()
        for n, part in enumerate(("added the theme helper", "removed the theme helper", "made a new OSC key and knows it", "was shown the OSC key",
                                  "OSC state sent back: added a target", "locked guest controls", "removed the guest device " + self.view_dev["id"],
                                  "imported a settings file; taken: ")):
            self.assertIn(part, did[n], did)
        self.assertEqual(len(did), 8)
        entry = self.settings.data["support_log"][0]
        self.assertIs(entry["osc_key"], True, "the page tells the owner to make a new key")
        text = json.dumps(self.settings.data["support_log"]) + "\n".join(self.lines)
        for secret in (key["key"], self.api.osc.key(), "192.168.1.77"):
            self.assertNotIn(secret, text, "what was done, never the value")
        self.assertEqual(len([x for x in self.lines if "remote support support-1: " in x]), 8, "and each is a line in the journal")
        self.assertEqual([d.get("n", 1) for d in entry["did"]], [1] * 8, "said once each: an opened route is not also counted as a route that was used")
        # the owner reads it on the Remote support page, and a support login does not
        seen = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]
        self.assertEqual([d["what"] for d in seen["did"]], did)
        self.assertNotIn("log", self.remote("GET", "/api/support", {}, dev)[1])

    def test_the_log_of_one_session_is_bounded_and_cannot_be_flushed(self):
        """The same thing again is one line with a count; when the list is full the first lines stay and the rest
        are counted. So what a helper did first cannot be pushed out of sight by forty harmless things after it."""
        dev = self.session("full")
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        for n in range(90):
            self.assertEqual(self.remote("POST", "/api/guests", {"locked": n % 2 == 0}, dev)[0], 200)
        entry = self.settings.data["support_log"][0]
        self.assertEqual([d.get("n", 1) for d in entry["did"]], [1, 45, 45])
        self.assertEqual(len([x for x in self.lines if "remote support support-1: " in x]), 3, "a repeat is counted, not written again")
        saved = json.load(open(self.settings.path))["support_log"][0]["did"]
        self.assertEqual([d["what"] for d in saved], self.did(), "every line is on the disk at once; a repeat's count goes with the next save")
        self.assertIn("imported a settings file", self.did()[0])
        for n in range(sp.DID_KEEP + 15):
            self.api.support.note(dev, "thing %d" % n)
        entry = self.settings.data["support_log"][0]
        self.assertEqual((len(entry["did"]), entry["did_more"]), (sp.DID_KEEP, 18))
        self.assertIn("imported a settings file", self.did()[0], "the first line is still the first")
        self.api.support.note(dev, "x" * 500 + "\n\x00")
        self.assertEqual(self.settings.data["support_log"][0]["did_more"], 19)
        self.assertLessEqual(max(len(w) for w in self.did()), sp.DID_TEXT)
        before = self.did()
        self.assertEqual(self.h("POST", "/api/guests", {"locked": False}, self.full_dev)[0], 200)       # the owner, at the studio
        self.api.support.note(self.full_dev, "not a support login")
        self.assertEqual(self.did(), before)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        self.api.support.note(dev, "after the end")                     # no session: nothing to write to, and no error
        self.assertEqual(self.did(), before)


class EveryChangeTest(Base):
    def test_every_post_a_session_is_answered_leaves_a_line(self):
        """Not only the routes D84 opened: every POST that a support session of the Owner's role is answered (the table
        of every route, with the handlers stubbed as tests.test_roles does) is in the session's log by its route, and
        never by its body. A new route is covered by itself. The few that change nothing are named in NOT_A_CHANGE."""
        from tests.test_roles import GUEST_BODIES
        real = self.api.routes()
        dev = self.session("full")
        stubbed = {k: (need, (lambda b, d, c: {})) for k, (need, _h) in real.items()}
        self.api.routes = lambda: stubbed
        self.api.set_module = lambda *a: {}
        for key in sp.NOT_A_CHANGE:
            self.assertTrue(key in real or key in policy.OUTSIDE, key)
        missing = []
        for (method, path), row in sorted(ROWS.items()):
            if method != "POST" or row[FULL] != 200 or (method, path) not in stubbed and path != "/api/modules/*":
                continue
            path = path.replace("*", "shaders")
            body = (GUEST_BODIES.get((method, path)) or [[{}]])[0][0]
            before = sum(d.get("n", 1) for d in self.settings.data["support_log"][0].get("did", [])) + self.settings.data["support_log"][0].get("did_more", 0)
            st, out = self.h(method, path, dict(body, secret="Body-Sentinel-91"), dev, TUNNEL)
            self.assertEqual(st, 200, (path, out))
            entry = self.settings.data["support_log"][0]
            after = sum(d.get("n", 1) for d in entry.get("did", [])) + entry.get("did_more", 0)
            quiet = (method, path) in sp.NOT_A_CHANGE or (method, path) in sp.REMOTE_OWNER_OPENED      # the opened ones speak in their real handlers
            if after - before != (0 if quiet else 1):
                missing.append(path)
        self.assertEqual(missing, [])
        self.assertNotIn("Body-Sentinel-91", json.dumps(self.settings.data["support_log"]) + "\n".join(self.lines))
        self.assertTrue(any(l.endswith("used /api/osc") for l in self.lines), "the change that D84 leaves open is at least in the owner's list")
        # a GET, a refused request and a studio device leave nothing
        n = len(self.lines)
        self.h("GET", "/api/status", {}, dev, TUNNEL)
        self.h("POST", "/api/pin/show", {}, dev, TUNNEL)
        self.h("POST", "/api/osc", {}, self.full_dev, LAN)
        self.assertEqual([l for l in self.lines[n:] if "remote support support-" in l], [])


class ImportTest(Base):
    def test_what_a_file_can_carry_for_a_helper_and_what_is_left_as_the_box_has_it(self):
        """The stick switch (recovery, D81) is the one section whose own route is refused to support: it is dropped
        from the file and the box's own stays. Every other section is one a session of the Owner's role may set by
        its own route."""
        self.assertEqual(sorted(boxcare.REMOTE_SETS), sorted(name for name, _c in boxcare.SECTIONS), "a new section needs a row in REMOTE_SETS")
        for name, path in boxcare.REMOTE_SETS.items():
            row = ROWS[("POST", path)]
            self.assertEqual(row[FULL] == 403, name in boxcare.REMOTE_KEPT, "%s is set by %s" % (name, path))
        dev = self.session("full")
        self.assertTrue(self.auth.usb_enabled())
        file = self.export()
        file["settings"]["recovery"] = {"usb": False}
        file["settings"]["osc"]["port"] = 9911
        out = self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(out["kept_for_support"], ["recovery"])
        self.assertNotIn("recovery", out["imported"])
        self.assertTrue(any("recovery" in n and "remote support" in n for n in out["notes"]))
        self.assertTrue(self.auth.usb_enabled(), "the stick way is still as the owner set it")
        self.assertEqual(self.settings.data["osc"]["port"], 9911, "the rest of the file came")
        self.assertIn("left as the box has it: recovery; taken: ", self.did()[-1])
        # the same file from the owner at the studio switches it, as before
        st, out = self.send(file)
        self.assertEqual((st, out["kept_for_support"]), (200, []))
        self.assertFalse(self.auth.usb_enabled())

    def test_a_file_cannot_name_devices_the_pin_codes_support_or_the_secure_connection(self):
        """None of these is a section of a settings file, for anybody: a file that names one is refused whole."""
        dev = self.session("full")
        devices = [dict(d) for d in self.settings.data["devices"]]
        before = json.dumps({k: v for k, v in self.settings.data.items() if k != "support_log"}, sort_keys=True)
        extra = {"devices": [{"id": "deadbeef", "name": "helper", "role": "full", "token_hash": "0" * 64}],
                 "auth": {"pin_hash": "0" * 64, "pin_salt": "0" * 32}, "support": dict(self.settings.data["support"], max_minutes=240),
                 "support_log": [], "joins": [{"role": "full", "code": "1234"}], "controller_code": {"enabled": True, "owner": True, "join": "live"},
                 "guest_controls": {"locked": True}, "https": {"owner_only": True}, osc.KEY_SECTION: {"key": "a" * 20}}
        for name, value in extra.items():
            file = self.export()
            file["settings"][name] = value
            with self.assertRaises(ApiError, msg=name) as e:
                self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
            self.assertIn(e.exception.status, (400, 422), name)
            file = self.export()
            file[name] = value                                  # nor beside the settings
            with self.assertRaises(ApiError, msg=name):
                self.care.import_settings(json.dumps(file).encode(), "import", dev, TUNNEL)
        self.assertEqual(json.dumps({k: v for k, v in self.settings.data.items() if k != "support_log"}, sort_keys=True), before)
        self.assertEqual(self.settings.data["devices"], devices)
        self.assertEqual(self.did(), [], "a refused file changed nothing and is no line in the log")

    def test_an_import_keeps_the_pin_every_device_and_the_session_itself(self):
        dev = self.session("full")
        pin_hash = dict(self.settings.data["auth"])
        ids = sorted(d["id"] for d in self.settings.data["devices"])
        cfg = dict(self.settings.data["support"])
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertEqual(self.settings.data["auth"], pin_hash)
        self.assertEqual(sorted(d["id"] for d in self.settings.data["devices"]), ids)
        self.assertEqual(self.settings.data["support"], cfg)
        self.assertTrue(self.api.support.session)
        self.assertEqual(self.h("GET", "/api/status", device=self.full_dev)[0], 200, "the owner is still in")

    def test_the_export_holds_the_feedback_targets_for_him_and_a_diagnostics_file_never_does(self):
        """D82 took the targets out of a support session's export. He reads and changes them on their own route now,
        so the export is the Owner's export; with passwords it stays refused, and the diagnostics file is unchanged."""
        from tests.test_boxcare import FakeJournal
        self.settings.data["osc"].update(feedback_on=True, feedback=[{"name": "Desk", "address": "192.168.1.77", "port": 9000}])
        dev = self.session("full")
        st, out = self.remote("POST", "/api/system/settings/export", {}, dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["file"]["settings"]["osc"]["feedback"][0]["address"], "192.168.1.77")
        self.assertEqual(self.remote("POST", "/api/system/settings/export", {"passwords": True}, dev)[0], 403)
        self.assertNotIn(osc.KEY_SECTION, out["file"]["settings"])
        self.care._run = FakeJournal()
        st, out = self.remote("GET", "/api/system/diagnostics", {}, dev)
        self.assertEqual(st, 200, out)
        self.assertNotIn("192.168.1.77", json.dumps(out))


class RevokeTest(Base):
    def test_a_helper_removes_a_guest_and_an_operator_and_never_an_owner_himself_or_a_system_identity(self):
        second = self.auth._add_device("second owner", "full")[1]
        dev = self.session("full")
        other = self.h("POST", "/api/support/login", {"code": self.api.support.session["code"]}, client=TUNNEL)[1]["device"]
        for owner in (self.full_dev, second):
            st, out = self.remote("POST", "/api/devices/revoke", {"id": owner["id"]}, dev)
            self.assertEqual(st, 403, out)
            self.assertIn("owner", out["error"])
        for name in (dev["id"], other["id"], "midi", "osc", "dmx", "room", "support-9", "", "nobody00"):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": name}, dev)[0], 404, name)
        for bad in (None, 7, ["x"], {"id": self.full_dev["id"]}):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": bad}, dev)[0], 404)
        self.assertEqual(self.did(), [])
        ids = lambda: sorted(d["id"] for d in self.auth.list_devices())
        self.assertEqual(ids(), sorted(d["id"] for d in (self.full_dev, second, self.live_dev, self.view_dev)))
        for target, word in ((self.view_dev, "guest"), (self.live_dev, "operator")):
            self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": target["id"]}, dev)[0], 200)
            self.assertIn("removed the %s device %s" % (word, target["id"]), self.did()[-1])
        self.assertEqual(ids(), sorted(d["id"] for d in (self.full_dev, second)), "both owners are still paired")
        self.assertTrue(self.api.support.authenticate is not None and self.api.support.session["tokens"], "and both support logins")
        # at the studio the Owner removes an Owner as before
        self.assertEqual(self.h("POST", "/api/devices/revoke", {"id": second["id"]}, self.full_dev)[0], 200)

    def test_a_device_whose_role_is_raised_in_the_list_meanwhile_is_judged_by_the_list(self):
        dev = self.session("full")
        with self.settings.lock:
            for d in self.settings.data["devices"]:
                if d["id"] == self.live_dev["id"]:
                    d["role"] = "full"
        self.assertEqual(self.remote("POST", "/api/devices/revoke", {"id": self.live_dev["id"]}, dev)[0], 403)


class AttackTest(Base):
    """What I tried as a helper who wants a way in after Stop, or the owner out."""

    def test_a_theme_file_is_data_and_never_more(self):
        dev = self.session("full")
        base = mine(id="sneak")
        tries = [dict(base, css="body{display:none}"), dict(base, script="alert(1)"), dict(base, name="<script>alert(1)</script>"),
                 dict(base, id="../../etc/passwd"), dict(base, id="sneak/../../x")]
        for bad in ("url(http://203.0.113.9/x)", "red;}</style><script>alert(1)</script>", "expression(alert(1))", "@import 'http://203.0.113.9'",
                    "#fff; background:url(//203.0.113.9)"):
            for part in ("tokens", "states", "areas"):
                if isinstance(base.get(part), dict) and base[part]:
                    t = json.loads(json.dumps(base))
                    first = next(iter(t[part]))
                    if isinstance(t[part][first], dict):
                        inner = next(iter(t[part][first]))
                        t[part][first][inner] = bad
                    else:
                        t[part][first] = bad
                    tries.append(t)
        for t in tries:
            st, out = self.remote("POST", "/api/theme/add", {"file": json.dumps(t)}, dev)
            self.assertIn(st, (400, 413, 422), json.dumps(t)[:120])
        self.assertEqual(self.remote("POST", "/api/theme/add", {"file": "x" * (2 * 1024 * 1024)}, dev)[0], 413)
        self.assertEqual(self.did(), [])
        # a theme that comes with the box cannot be removed, by him or anyone
        stock = next(tid for tid, t in self.api.themes.items() if t.get("source") != "addon")
        self.assertEqual(self.remote("POST", "/api/theme/remove", {"id": stock}, dev)[0], 409)

    def test_feedback_targets_cannot_be_pointed_at_the_helper_or_out_of_the_building(self):
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.5", "prefixlen": 24}]},
                                     {"ifname": "wg-pvj", "addr_info": [{"family": "inet", "local": "10.77.0.5", "prefixlen": 24}]}]
        dev = self.session("full")
        for address in (TUNNEL, "10.77.0.1", "203.0.113.9", "8.8.8.8", "192.168.1.5", "192.168.1.255", "127.0.0.1", "192.168.2.9"):
            st, out = self.remote("POST", "/api/osc/feedback", {"add": {"name": "me", "address": address, "port": 9000}}, dev)
            self.assertEqual(st, 400, (address, out))
        self.assertEqual(self.remote("POST", "/api/osc/feedback", {"add": {"name": "me", "address": "192.168.1.60", "port": 53}}, dev)[0], 400)
        self.assertEqual(self.settings.data["osc"].get("feedback", []), [])
        # by a file: the form is taken, and at every send the tunnel's address is still no device on the box's own network
        from pvj import oscfeedback
        self.assertIsNotNone(oscfeedback.not_local(TUNNEL, oscfeedback.local_nets(self.api._ip_json())))

    def test_the_key_he_saw_is_said_and_the_owner_is_told_to_make_a_new_one(self):
        dev = self.session("full")
        st, out = self.remote("POST", "/api/osc/key", {"new": True}, dev)
        self.assertEqual(st, 200, out)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        entry = self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]
        self.assertEqual((entry["osc_key"], bool(entry["ended"])), (True, True))
        self.assertEqual(self.api.osc.key(), out["key"], "the key he made still works: only a new one ends what he knows")
        # the owner makes a new key at the studio: now nobody outside knows it, and the page stops saying so
        self.assertEqual(self.h("POST", "/api/osc/key", {}, self.full_dev)[0], 200)             # only looking at it changes nothing
        self.assertTrue(self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0]["osc_key"])
        st, new = self.h("POST", "/api/osc/key", {"new": True}, self.full_dev)
        self.assertEqual(st, 200, new)
        self.assertNotEqual(new["key"], out["key"])
        self.assertNotIn("osc_key", self.h("GET", "/api/support", device=self.full_dev)[1]["log"][0])
        # he is held to the same few shows as a device at the studio
        dev = self.session("full")
        got = [self.remote("POST", "/api/osc/key", {}, dev)[0] for _ in range(12)]
        self.assertIn(429, got)

    def test_he_cannot_lengthen_widen_or_restart_his_own_access(self):
        dev = self.session("live")
        for path, body in (("/api/support/extend", {"minutes": 240}), ("/api/support/start", {"confirm": "start", "role": "full"}),
                           ("/api/support/config", {"max_minutes": 240, "allowed": True}), ("/api/devices/invite", {"name": "me", "role": "full"}),
                           ("/api/pair", {"pin": "0000", "name": "me"}), ("/api/access/code", {"role": "live"}), ("/api/pin/show", {}),
                           ("/api/pin/rotate", {}), ("/api/recovery/codes", {}), ("/api/https/owner-only", {"on": True})):
            for d in (dev, self.session("full")):
                self.assertEqual(self.remote("POST", path, body, d)[0], 403, path)
        dev = self.session("full")
        self.assertEqual(self.api.support.session["role"], "full")
        left = self.api.support._seconds_left()
        self.assertLessEqual(left, 3600)
        # a restart of the box ends the session: allowed. Power-off and a reset are not.
        for path in ("/api/system/poweroff", "/api/system/factory-reset"):
            self.assertEqual(self.remote("POST", path, {"confirm": "factory-reset", "media": "keep"}, dev)[0], 403)

    def test_when_the_session_ends_his_login_is_gone_and_no_device_was_added(self):
        before = sorted(d["id"] for d in self.auth.list_devices())
        dev = self.session("full")
        token = next(iter(self.api.support.session["tokens"]))
        self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.remote("POST", "/api/guests", {"locked": True}, dev)
        self.h("POST", "/api/support/stop", device=self.full_dev)
        self.assertIsNone(self.api.support.session)
        self.assertEqual(sorted(d["id"] for d in self.auth.list_devices()), before)
        self.assertEqual(self.auth.list_joins(), [])
        for path, body in (("/api/guests", {"locked": False}), ("/api/osc/key", {}), ("/api/theme/remove", {"id": "x"})):
            self.assertIn(self.h("POST", path, body, dev, TUNNEL)[0], (401, 403), path)
            self.assertIn(self.h("POST", path, body, dev, LAN)[0], (401, 403), path)
        with self.assertRaises(ApiError):
            self.care.import_settings(json.dumps(self.export()).encode(), "import", dev, TUNNEL)
        self.assertNotIn(token, json.dumps(self.settings.data))

    def test_two_more_ways_to_shut_the_owner_out_the_clock_and_the_network(self):
        """The clock set far forward: only a device that joined with a guest code and went unused is dropped by time
        (auth._idle); an Owner's and an Operator's never are. The network: a helper may propose a change and take it
        back, and it goes back by itself unless someone AT THE STUDIO confirms it."""
        owner = dict(self.settings.data["devices"][0])
        self.auth._now = lambda: 2082758400            # the year 2036, the latest a clock is taken
        self.auth._prune_idle()
        ids = [d["id"] for d in self.settings.data["devices"]]
        self.assertIn(owner["id"], ids)
        self.assertIn(self.live_dev["id"], ids)
        self.assertIsNotNone(self.auth.authenticate(self.full))
        for role in ("live", "full"):
            dev = self.session(role)
            st, out = self.remote("POST", "/api/network/confirm", {}, dev)
            self.assertEqual(st, 403, out)
        self.assertIn(("POST", "/api/network/confirm"), sp.REMOTE_DENY)
        self.assertNotIn(("POST", "/api/network/revert"), sp.REMOTE_DENY)

    def test_known_and_left_open_the_networks_osc_accepts_from(self):
        """Found and NOT closed here (it was so before D84, through POST /api/osc; D84 says so and asks the owner):
        a session of the Owner's role can add an address outside the building to the networks OSC accepts from. This
        test holds the fact still, so whoever closes it changes this test on purpose."""
        dev = self.session("full")
        with mock.patch.object(self.api.osc, "apply", lambda: None):
            st, out = self.remote("POST", "/api/osc", {"allow": ["203.0.113.9/32"]}, dev)
        self.assertEqual(st, 200, out)


if __name__ == "__main__":
    import unittest
    unittest.main()
