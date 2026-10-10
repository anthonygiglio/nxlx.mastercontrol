# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from pvj import auth
from pvj.auth import Auth, AuthError
from pvj.settings import Settings


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(os.path.join(self.dir, "s.json"))
        self.settings.load()
        self.clock = Clock()
        self.auth = Auth(self.settings, clock=self.clock, now=self.clock)
        self.pin = self.auth.current_pin

    def test_pin_shape_and_never_stored_in_clear(self):
        self.assertRegex(self.pin, r"^[0-9]{4}$")
        stored = self.settings.data["auth"]
        self.assertEqual(len(stored["pin_hash"]), 64)
        self.assertNotEqual(stored["pin_hash"], self.pin)

    def test_pairing_gives_full_role_and_token_is_stored_hashed(self):
        token, dev = self.auth.pair(self.pin, "Anthony's phone", "10.0.0.5")
        self.assertEqual(dev["role"], "full")
        self.assertEqual(self.auth.authenticate(token)["name"], "Anthony's phone")
        self.assertNotIn(token, str(self.settings.data))
        self.assertIsNone(self.auth.authenticate(token + "x"))
        self.assertIsNone(self.auth.authenticate(""))
        self.assertIsNone(self.auth.authenticate(None))

    def test_wrong_pin_rejected_and_public_view_has_no_secrets(self):
        with self.assertRaises(AuthError):
            self.auth.pair("0000" if self.pin != "0000" else "1111", "x", "c")
        token, _ = self.auth.pair(self.pin, "x", "c")
        for d in self.auth.list_devices():
            self.assertNotIn("token_hash", d)

    def test_client_lockout_after_five_failures_then_recovers(self):
        wrong = "0000" if self.pin != "0000" else "1111"
        for _ in range(5):
            with self.assertRaises(AuthError):
                self.auth.pair(wrong, "x", "10.0.0.9")
        with self.assertRaises(AuthError) as cm:
            self.auth.pair(self.pin, "x", "10.0.0.9")  # even the right PIN is refused while locked
        self.assertGreater(cm.exception.retry_after, 0)
        token, _ = self.auth.pair(self.pin, "other client", "10.0.0.10")  # others unaffected
        self.assertTrue(token)
        self.clock.t += auth.LOCKOUT_SECONDS + 1
        self.assertTrue(self.auth.pair(self.pin, "x", "10.0.0.9")[0])

    def test_global_lockout_stops_attacker_rotating_addresses(self):
        wrong = "0000" if self.pin != "0000" else "1111"
        for i in range(auth.GLOBAL_FAILS):
            with self.assertRaises(AuthError):
                self.auth.pair(wrong, "x", "10.0.1.%d" % i)
        with self.assertRaises(AuthError) as cm:
            self.auth.pair(self.pin, "x", "10.9.9.9")
        self.assertIsNotNone(cm.exception.retry_after)

    def test_concurrent_wrong_pins_cannot_beat_the_limit(self):
        import threading
        guesses = []
        real = self.auth._check_pin
        self.auth._check_pin = lambda pin: (guesses.append(pin), real(pin))[1]
        wrong = "0000" if self.pin != "0000" else "1111"

        def attempt():
            try:
                self.auth.pair(wrong, "x", "10.0.0.66")
            except AuthError:
                pass
        threads = [threading.Thread(target=attempt) for _ in range(60)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(len(guesses), auth.PER_CLIENT_FAILS)  # the rest were refused without being checked

    def test_paired_owner_can_clear_a_lockout_by_rotating_the_pin(self):
        token, _ = self.auth.pair(self.pin, "owner", "10.0.0.1")
        wrong = "0000" if self.pin != "0000" else "1111"
        for i in range(auth.GLOBAL_FAILS):
            with self.assertRaises(AuthError):
                self.auth.pair(wrong, "x", "10.1.1.%d" % i)
        with self.assertRaises(AuthError):
            self.auth.pair(self.pin, "guest", "10.9.9.9")  # locked out for everyone
        new = self.auth.rotate_pin()
        self.assertTrue(self.auth.pair(new, "guest", "10.9.9.9")[0])
        self.assertTrue(self.auth.authenticate(token))  # already-paired devices were never affected

    def test_roles_and_invites(self):
        token, _ = self.auth.invite("guest link", "view")
        dev = self.auth.authenticate(token)
        self.assertTrue(Auth.allows(dev, "view"))
        self.assertFalse(Auth.allows(dev, "live"))
        live, _ = self.auth.invite("stage tech", "live")
        self.assertTrue(Auth.allows(self.auth.authenticate(live), "live"))
        self.assertFalse(Auth.allows(self.auth.authenticate(live), "full"))
        with self.assertRaises(AuthError):
            self.auth.invite("sneaky", "full")
        self.assertFalse(Auth.allows(None, "view"))

    def test_revoke_and_persistence(self):
        token, dev = self.auth.pair(self.pin, "phone", "c")
        reloaded = Settings(self.settings.path)
        reloaded.load()
        auth2 = Auth(reloaded)
        self.assertEqual(auth2.authenticate(token)["id"], dev["id"])
        self.assertTrue(auth2.revoke(dev["id"]))
        self.assertIsNone(auth2.authenticate(token))
        self.assertFalse(auth2.revoke("nope"))

    def test_set_and_rotate_pin(self):
        with self.assertRaises(AuthError):
            self.auth.set_pin("12")
        with self.assertRaises(AuthError):
            self.auth.set_pin("abcd")
        self.auth.set_pin("4321")
        self.assertTrue(self.auth.pair("4321", "x", "c")[0])
        with self.assertRaises(AuthError):
            self.auth.pair(self.pin if self.pin != "4321" else "0001", "x", "c2")
        new = self.auth.rotate_pin()
        self.assertRegex(new, r"^[0-9]{4}$")

    def test_rotate_on_start_replaces_pin_but_keeps_paired_devices(self):
        token, _ = self.auth.pair(self.pin, "phone", "c")
        s2 = Settings(self.settings.path)
        s2.load()
        a2 = Auth(s2, rotate_on_start=True)
        self.assertTrue(a2.authenticate(token))
        self.assertRegex(a2.current_pin, r"^[0-9]{4}$")
        self.assertTrue(a2.pair(a2.current_pin, "second phone", "c2")[0])

    def test_pin_survives_restart_without_being_regenerated(self):
        s2 = Settings(self.settings.path)
        s2.load()
        a2 = Auth(s2)
        self.assertIsNone(a2.current_pin)          # the PIN is not known to this run: only its hash is (D77)
        self.assertIsNone(a2.show_pin("some-device"))
        self.assertTrue(a2.pair(self.pin, "x", "c")[0])

    def test_show_pin_is_counted_per_device_and_never_written_down(self):
        for _ in range(auth.PIN_SHOWS):
            self.assertEqual(self.auth.show_pin("dev-a"), self.pin)
        with self.assertRaises(AuthError) as cm:
            self.auth.show_pin("dev-a")
        self.assertGreater(cm.exception.retry_after, 0)
        self.assertEqual(self.auth.show_pin("dev-b"), self.pin)         # another device is not counted with it
        self.clock.t += auth.PIN_SHOW_WINDOW + 1
        self.assertEqual(self.auth.show_pin("dev-a"), self.pin)
        self.assertNotIn(self.pin, str(self.settings.data))


if __name__ == "__main__":
    unittest.main()
