# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Owner recovery (D81): printed recovery codes, and the USB stick that shows a fresh PIN on the box's screen."""
import json
import os
import re
import threading
import types
import unittest

from pvj import auth as auth_mod, pinscreen as pinscreen_mod, recovery as recovery_mod
from tests.test_server import ServerBase
from tests.test_support import LAN, SupportBase

CODE = re.compile(r"^[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}$")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class RecoveryCodesTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.log = self.lines.append
        self.full, _ = self.pair("owner laptop")
        self.live = self.call("POST", "/api/devices/invite", {"name": "presenter", "role": "live"}, token=self.full)[1]["token"]
        self.view = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=self.full)[1]["token"]

    def make(self):
        st, body, _ = self.call("POST", "/api/recovery/codes", {}, token=self.full)
        self.assertEqual(st, 200, body)
        return body["codes"]

    def redeem(self, code, name="new phone"):
        return self.call("POST", "/api/pair", {"pin": code, "name": name})

    def everything_written(self):
        """Every text the box can give away: the settings file, the journal lines, an export, the diagnostics file,
        every GET's answer."""
        texts = ["\n".join(self.lines)]
        for path in ("/api/system/settings/export", "/api/system/diagnostics"):
            st, body, _ = self.call("POST", path, {}, token=self.full) if "export" in path else self.call("GET", path, token=self.full)
            texts.append(json.dumps(body) if not isinstance(body, bytes) else body.decode("latin1"))
        for (m, p) in self.api.routes():
            if m == "GET":
                st, body, _ = self.call("GET", p, token=self.full)
                texts.append(json.dumps(body) if not isinstance(body, bytes) else body.decode("latin1"))
        return "\n".join(texts)

    def test_a_set_is_shown_once_in_the_form_for_paper_and_is_in_clear_nowhere(self):
        codes = self.make()
        self.assertEqual(len(codes), auth_mod.RECOVERY_COUNT)
        self.assertEqual(len(set(codes)), len(codes))
        for c in codes:
            self.assertRegex(c, CODE)
        st, body, _ = self.call("GET", "/api/recovery", token=self.full)
        self.assertEqual(st, 200, body)
        self.assertEqual((body["codes"]["left"], body["codes"]["count"], body["codes"]["by"]), (8, 8, "owner laptop"))
        self.assertTrue(body["usb"])
        everything = self.everything_written()
        file = open(self.settings.path).read()
        for c in codes:
            self.assertNotIn(c, everything)
            self.assertNotIn(c.replace("-", ""), everything)
            self.assertNotIn(c.replace("-", ""), file)
        self.assertNotIn("hashes", everything)              # no hash leaves the box either
        for h in self.settings.data["recovery"]["codes"]["hashes"]:
            self.assertNotIn(h, everything)
        self.assertIn("recovery", file)
        self.assertEqual(self.call("GET", "/api/recovery/codes", token=self.full)[0], 405)

    def test_redeem_pairs_an_owner_burns_the_code_logs_it_and_shows_the_owners_a_notice(self):
        codes = self.make()
        st, body, r = self.redeem(codes[3], "Anna's phone")
        self.assertEqual(st, 200, body)
        self.assertEqual(body["device"]["role"], "full")
        self.assertNotIn(self.pin, json.dumps(body))
        self.assertTrue(r.getheader("Set-Cookie").startswith("pvj_token="))
        self.assertEqual(self.call("GET", "/api/recovery", token=body["token"])[0], 200)      # it is an owner now
        line = [x for x in self.lines if "recovery code was used" in x]
        self.assertEqual(len(line), 1)
        self.assertIn("Anna's phone", line[0])
        self.assertIn("7 left", line[0])
        self.assertNotIn(codes[3], line[0])
        st, body, _ = self.call("GET", "/api/recovery", token=self.full)
        self.assertEqual(body["codes"]["left"], 7)
        self.assertEqual((body["log"][-1]["kind"], body["log"][-1]["name"], body["log"][-1]["left"]), ("code", "Anna's phone", 7))

    def test_typed_from_paper_lower_case_spaces_and_dashes(self):
        codes = self.make()
        bare = codes[0].replace("-", "")
        self.assertEqual(self.redeem(bare.lower())[0], 200)
        self.assertEqual(self.redeem(" ".join(codes[1].split("-")).lower() + " ")[0], 200)
        self.assertEqual(self.redeem(codes[2].replace("-", " - "))[0], 200)

    def test_wrong_reused_cancelled_and_replaced_codes_all_get_the_same_answer_as_no_set(self):
        none = self.redeem("ABCD-EFGH-JKMN")
        self.assertEqual(none[0], 403)
        codes = self.make()
        wrong = self.redeem("ABCD-EFGH-JKMN")
        self.assertEqual((wrong[0], wrong[1]), (none[0], none[1]))
        self.assertEqual(self.redeem(codes[0])[0], 200)
        reused = self.redeem(codes[0])
        self.assertEqual((reused[0], reused[1]), (none[0], none[1]))
        old = codes[1]
        new = self.make()                                   # a new set replaces the old one
        self.assertEqual(self.redeem(old)[0], 403)
        self.assertEqual(self.redeem(new[0])[0], 200)
        self.assertEqual(self.call("POST", "/api/recovery/cancel", {}, token=self.full)[1], {"ok": True, "had": True})
        self.assertEqual(self.redeem(new[1])[0], 403)
        self.assertEqual(self.call("GET", "/api/recovery", token=self.full)[1]["codes"], None)
        self.assertEqual(self.call("POST", "/api/recovery/cancel", {}, token=self.full)[1], {"ok": True, "had": False})

    def test_two_devices_racing_one_code_get_exactly_one_winner(self):
        codes = self.make()
        results = []

        def go(i):
            results.append(self.redeem(codes[0], "racer %d" % i)[0])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200] + [403] * 5)
        self.assertEqual(self.call("GET", "/api/recovery", token=self.full)[1]["codes"]["left"], 7)

    def test_a_failed_pairing_costs_no_code(self):
        codes = self.make()
        now = int(__import__("time").time())
        self.settings.data["devices"] += [{"id": "f%07d" % i, "name": "x", "role": "view", "token_hash": "0" * 64, "created": now}
                                          for i in range(auth_mod.MAX_DEVICES)]
        st, body, _ = self.redeem(codes[0])
        self.assertEqual(st, 409, body)                     # the list is full: not a wrong guess
        self.assertEqual(len(self.settings.data["recovery"]["codes"]["hashes"]), 8)
        del self.settings.data["devices"][3:]
        self.assertEqual(self.redeem(codes[0])[0], 200)

    def test_wrong_codes_share_the_pin_throttle(self):
        self.make()
        for _ in range(auth_mod.PER_CLIENT_FAILS):
            self.assertEqual(self.redeem("ABCD-EFGH-JKMN")[0], 403)
        st, body, _ = self.redeem("ABCD-EFGH-JKMN")
        self.assertEqual(st, 429, body)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "p"})[0], 429)     # the PIN too
        self.assertEqual(self.call("POST", "/api/pin/unlock", {}, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "p"})[0], 200)

    def test_only_an_owner_here_may_see_make_cancel_or_switch(self):
        for method, path, body in (("GET", "/api/recovery", None), ("POST", "/api/recovery/codes", {}),
                                   ("POST", "/api/recovery/cancel", {}), ("POST", "/api/recovery/usb", {"on": False})):
            self.assertEqual(self.call(method, path, body)[0], 401, path)
            for token in (self.view, self.live):
                self.assertEqual(self.call(method, path, body, token=token)[0], 403, path)
            if method == "POST":
                self.assertEqual(self.call(method, path, body, token=self.full, csrf=False)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/recovery", token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/recovery/usb", {"on": "no"}, token=self.full)[0], 400)
        token2, _ = self.pair("second")
        dev2 = self.auth.authenticate(token2)
        self.auth.revoke(dev2["id"])                        # gone between the token check and the handler
        for method, path in (("GET", "/api/recovery"), ("POST", "/api/recovery/codes"), ("POST", "/api/recovery/cancel")):
            self.assertEqual(self.api.handle(method, path, {}, dev2, LAN)[0], 401, path)
        self.assertIsNone(self.settings.data.get("recovery"))

    def test_the_switch_is_in_the_export_the_hashes_are_not_and_an_import_keeps_the_boxs_codes(self):
        codes = self.make()
        self.assertEqual(self.call("POST", "/api/recovery/usb", {"on": False}, token=self.full)[1], {"usb": False})
        st, body, _ = self.call("POST", "/api/system/settings/export", {}, token=self.full)
        self.assertEqual(body["file"]["settings"]["recovery"], {"usb": False})
        file = body["file"]
        file["settings"]["recovery"] = {"usb": True, "codes": {"salt": "00", "hashes": ["00"]}}    # a file that tries to bring codes
        st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(file).encode(), token=self.full,
                                headers={"Content-Type": "application/json"})
        self.assertEqual(st, 200, body)
        self.assertTrue(self.auth.usb_enabled())
        self.assertEqual(len(self.settings.data["recovery"]["codes"]["hashes"]), 8)     # the box's own set, untouched
        self.assertEqual(self.redeem(codes[0])[0], 200)
        st, body, _ = self.call("GET", "/api/system/diagnostics", token=self.full)
        self.assertEqual(body["file"]["settings"]["recovery"]["codes_left"], 7)
        self.assertNotIn("hashes", json.dumps(body))

    def test_a_factory_reset_removes_the_codes(self):
        codes = self.make()
        dev = self.auth.authenticate(self.full)
        st, body = self.api.handle("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, dev, LAN)
        self.assertEqual(st, 200, body)
        self.assertNotIn("recovery", self.settings.data)
        self.assertEqual(self.redeem(codes[0])[0], 403)


class RecoverySupportTest(SupportBase):
    def test_remote_support_never_touches_recovery(self):
        from tests.test_support import TUNNEL
        self.ready()
        st, started = self.start(role="full")
        self.assertEqual(st, 200, started)
        st, body = self.h("POST", "/api/support/login", {"code": started["code"]}, client=TUNNEL)
        self.assertEqual(st, 200, body)
        token = body["token"]
        for method, path in (("GET", "/api/recovery"), ("POST", "/api/recovery/codes"), ("POST", "/api/recovery/cancel"), ("POST", "/api/recovery/usb")):
            st, body = self.api.handle(method, path, {"on": False}, self.api.support.authenticate(token), TUNNEL)
            self.assertEqual(st, 403, (path, body))


class StickTest(ServerBase):
    """The stick, with a fake mount folder in the place of /media/pvj."""

    def setUp(self):
        super().setUp()
        self.lines = []
        self.usb = os.path.join(self.tmp, "usbroot")
        self.drive = os.path.join(self.usb, "STICK")
        os.makedirs(self.drive)
        self.api.usb_root = self.usb
        self.clock = Clock()
        self.rec = recovery_mod.Recovery(self.api, self.auth, log=self.lines.append, clock=self.clock, now=lambda: 1700000000 + int(self.clock.t))
        self.api.recovery = self.rec
        self.shown = []
        self.player.ipc = types.SimpleNamespace(request=lambda *a: self.shown.append(a))
        self.screen = pinscreen_mod.PinScreen(self.api, self.auth, log=self.lines.append, hostname="nxlx-mastercontrol", clock=self.clock)
        self.api.pinscreen = self.screen
        self.pair("owner")                      # a paired device: the first-run screen is off, as at a venue

    def insert(self, name="pvj-recover", content=b""):
        with open(os.path.join(self.drive, name), "wb") as f:
            f.write(content)

    def remove(self, name="pvj-recover"):
        os.unlink(os.path.join(self.drive, name))

    def texts(self):
        return [c[1] for c in self.shown if c[0] == "show-text"]

    def test_the_file_shows_a_fresh_pin_over_the_show_and_the_stick_out_ends_it(self):
        before = self.pin
        self.player.running, self.player.path = True, "/x/clip.mp4"
        self.assertFalse(self.screen.tick())                 # nothing to draw: a clip plays, a device is paired
        self.insert()
        self.assertTrue(self.screen.tick())
        pin = self.auth.current_pin
        self.assertNotEqual(pin, before)
        self.assertEqual(self.rec.active()["pin"], pin)
        text = self.texts()[-1]
        self.assertIn("Owner PIN  " + pin, text)
        self.assertIn("Recovery stick found", text)
        self.assertIn("http://nxlx-mastercontrol.local/", text)
        self.assertIn("Take the stick out", text)
        self.assertTrue(self.api.access_on_screen())        # snapshots for a guest leave the text out
        self.assertFalse(self.call("POST", "/api/pair", {"pin": before, "name": "x"})[0] == 200)
        self.remove()
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.assertNotEqual(self.auth.current_pin, pin)      # dead the moment the stick is out
        self.assertEqual(self.call("POST", "/api/pair", {"pin": pin, "name": "late"})[0], 403)
        self.assertEqual(self.rec.status()["last"]["how"], "removed")
        self.assertEqual(self.shown[-1][:2], ("show-text", ""))         # cleared
        log = self.auth.recovery_status()["log"]
        self.assertEqual((log[-1]["kind"], log[-1]["label"]), ("stick", "STICK"))
        self.assertTrue(any("recovery stick was put in" in x for x in self.lines))
        self.assertFalse(any(pin in x for x in self.lines))

    def test_the_pin_works_once_and_then_changes(self):
        self.insert()
        self.screen.tick()
        pin = self.auth.current_pin
        st, body, _ = self.call("POST", "/api/pair", {"pin": pin, "name": "found phone"})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["device"]["role"], "full")
        self.assertIsNone(self.rec.active())
        self.assertNotEqual(self.auth.current_pin, pin)
        self.assertEqual(self.call("POST", "/api/pair", {"pin": pin, "name": "second"})[0], 403)
        self.assertEqual((self.rec.status()["last"]["how"], self.rec.status()["last"]["device"]), ("used", "found phone"))
        self.assertEqual(self.call("POST", "/api/pin/show", {}, token=body["token"])[1]["pin"], self.auth.current_pin)   # the new owner reads the new PIN

    def test_it_expires_and_a_stick_left_in_shows_nothing_more(self):
        self.insert()
        self.screen.tick()
        pin = self.auth.current_pin
        self.clock.t += recovery_mod.STICK_SECONDS - 1
        self.screen.tick()
        self.assertEqual(self.rec.active()["seconds_left"], 1)
        self.clock.t += 2
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.assertNotEqual(self.auth.current_pin, pin)
        self.assertEqual(self.rec.status()["last"]["how"], "expired")
        for _ in range(5):
            self.clock.t += 3
            self.assertIsNone(self.rec.active() if not self.screen.tick() else "drawn")

    def test_insertion_only_at_most_six_an_hour_and_once_at_boot(self):
        for i in range(recovery_mod.STICK_PER_HOUR):
            self.insert()
            self.screen.tick()
            self.assertIsNotNone(self.rec.active(), i)
            self.remove()
            self.screen.tick()
        self.insert()
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.assertTrue(any("times this hour" in x for x in self.lines))
        self.remove()
        self.clock.t += 3601
        fresh = recovery_mod.Recovery(self.api, self.auth, log=self.lines.append, clock=self.clock)     # a boot with the stick in
        self.api.recovery = fresh
        self.insert()
        self.screen.tick()
        self.assertIsNotNone(fresh.active())

    def test_the_switch_off_shows_nothing_and_ends_what_shows(self):
        full, _ = self.pair("owner 2")
        self.assertEqual(self.call("POST", "/api/recovery/usb", {"on": False}, token=full)[0], 200)
        self.insert()
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.assertTrue(any("switched off" in x for x in self.lines))
        self.remove()
        self.screen.tick()
        self.assertEqual(self.call("POST", "/api/recovery/usb", {"on": True}, token=full)[0], 200)
        self.insert()
        self.screen.tick()
        self.assertIsNotNone(self.rec.active())
        pin = self.auth.current_pin
        self.assertEqual(self.call("POST", "/api/recovery/usb", {"on": False}, token=full)[0], 200)   # ends it at once
        self.assertIsNone(self.rec.active())
        self.assertNotEqual(self.auth.current_pin, pin)
        self.assertEqual(self.call("GET", "/api/recovery", token=full)[1]["stick"]["last"]["how"], "switched off")

    def test_only_a_regular_file_at_the_top_of_a_drive_counts_and_clips_beside_it_still_list(self):
        os.makedirs(os.path.join(self.drive, "pvj-recover"))              # a folder of that name
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        os.rmdir(os.path.join(self.drive, "pvj-recover"))
        outside = os.path.join(self.tmp, "real")
        open(outside, "w").close()
        os.symlink(outside, os.path.join(self.drive, "pvj-recover"))      # a symlink
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        os.unlink(os.path.join(self.drive, "pvj-recover"))
        os.makedirs(os.path.join(self.drive, "sub"))
        self.insert("sub/pvj-recover")                                     # not at the top
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.insert("pvj-recover.md")                                      # another name
        self.insert("clip.mp4", b"x" * 10)
        self.screen.tick()
        self.assertIsNone(self.rec.active())
        self.insert("PVJ-RECOVER.TXT", b"anything, never read")           # case-blind, the .txt form, a stick with clips
        self.screen.tick()
        self.assertIsNotNone(self.rec.active())
        self.assertEqual([f["name"] for d in self.api.usb_drives() for f in d["files"]], ["clip.mp4"])
        os.symlink(self.drive, os.path.join(self.usb, "ALIAS"))           # a linked drive folder is skipped
        self.assertIsNone(recovery_mod.stick_present(os.path.join(self.tmp, "nowhere")))

    def test_it_needs_no_paired_device_no_player_and_survives_a_player_restart(self):
        self.auth.revoke_all()
        self.player.running = False
        self.player.ipc = types.SimpleNamespace(request=lambda *a: (_ for _ in ()).throw(__import__("pvj.player", fromlist=["PlayerError"]).PlayerError("down")))
        self.insert()
        self.assertFalse(self.screen.tick())                 # the player is down: nothing drawn, the PIN is set anyway
        pin = self.auth.current_pin
        self.assertIsNotNone(self.rec.active())
        self.player.ipc = types.SimpleNamespace(request=lambda *a: self.shown.append(a))
        self.clock.t += 3
        self.assertTrue(self.screen.tick())                  # back: drawn at the next tick, same PIN
        self.assertIn(pin, self.texts()[-1])
        self.assertEqual(self.call("POST", "/api/pair", {"pin": pin, "name": "owner again"})[1]["device"]["role"], "full")

    def test_a_lockout_by_a_stranger_is_lifted_by_the_stick(self):
        for _ in range(auth_mod.PER_CLIENT_FAILS):
            self.call("POST", "/api/pair", {"pin": "0000", "name": "x"})
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.pin, "name": "x"})[0], 429)
        self.insert()
        self.screen.tick()
        self.assertEqual(self.call("POST", "/api/pair", {"pin": self.auth.current_pin, "name": "owner"})[0], 200)


if __name__ == "__main__":
    unittest.main()


class RecoveryOverTlsTest(__import__("tests.test_https", fromlist=["HttpsBase"]).HttpsBase):
    """While "Owner access only over the secure connection" (D79) is on: making and cancelling codes is over TLS
    only, and so is redeeming one (a code is worth more than the PIN)."""

    def test_with_the_switch_on_codes_are_made_and_redeemed_over_tls_only(self):
        plain, _ = self.pair("owner, plain")
        self.full = plain
        self.assertEqual(self.install(self.request_and_sign())[0], 200)               # as HttpsTest.switched_on
        secure = self.scall("POST", "/api/pair", {"pin": self.pin, "name": "owner, tls"})[1]["token"]
        self.assertEqual(self.scall("POST", "/api/https/owner-only", {"on": True}, token=secure)[0], 200)
        for method, path in (("GET", "/api/recovery"), ("POST", "/api/recovery/codes"), ("POST", "/api/recovery/cancel")):
            st, body, _ = self.call(method, path, {} if method == "POST" else None, token=secure)
            self.assertEqual(st, 403, (path, body))
            self.assertIn("https://", body["error"])
            self.assertEqual(self.call(method, path, {} if method == "POST" else None, token=plain)[0], 403, path)   # a plain-http token, even over TLS, never
        st, body, _ = self.scall("POST", "/api/recovery/codes", {}, token=secure)
        self.assertEqual(st, 200, body)
        codes = body["codes"]
        st, body, _ = self.call("POST", "/api/pair", {"pin": codes[0], "name": "new phone, plain http"})
        self.assertEqual(st, 403, body)
        self.assertIn("https://", body["error"])
        self.assertEqual(self.scall("GET", "/api/recovery", token=secure)[1]["codes"]["left"], 8)      # the code was not spent
        self.assertEqual(len(self.auth.list_devices()), 2)                                            # and no device was made
        st, body, _ = self.scall("POST", "/api/pair", {"pin": codes[0], "name": "new phone, tls"})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.scall("GET", "/api/recovery", token=body["token"])[1]["codes"]["left"], 7)   # a secure owner at once
