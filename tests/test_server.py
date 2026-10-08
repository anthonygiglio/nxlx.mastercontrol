# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import http.client
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from pvj import server, themes as themes_mod
from pvj.api import Api
from pvj.auth import Auth
from pvj.modules import Registry
from pvj.osc import OscManager
from pvj.settings import Settings


class FakePlayer:
    def __init__(self, rundir):
        self.rundir = rundir
        self.calls = []
        self.plays = []
        self.running = False

    TEST_PATTERN = "av://lavfi:smptehdbars=size=1920x1080:rate=25"

    TEST_TONES = {"left": "av://lavfi:aevalsrc=L", "right": "av://lavfi:aevalsrc=R", "both": "av://lavfi:aevalsrc=B"}

    def osd_size(self):
        return (1920, 1080)

    def status(self):
        return {"running": self.running, "path": None}

    def play(self, paths, loop=True, audio_device=None, windowed=False, spawn=True, ending=None, image_seconds=None):
        self.calls.append(("play", paths, loop, spawn))
        self.plays.append({"paths": paths, "loop": loop, "ending": ending or ("loop" if loop else "stop"), "image_seconds": image_seconds})
        self.running = True

    def __getattr__(self, name):
        if name in ("set_shaders", "set_mapping_mode", "pause", "seek", "seek_to", "playlist_step", "shuffle", "flip", "overlay_remove", "overlay_file", "play_pipe", "speed", "volume", "opacity", "size", "position", "rotate", "loop", "mute", "clear", "volume_step"):
            def call(*args):
                self.calls.append((name,) + args)
                return True if name in ("pause", "playlist_step") else None
            return call
        raise AttributeError(name)

    class ipc:
        @staticmethod
        def request(*a):
            return None


class ServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "video")
        os.makedirs(self.media)
        for n in ("a.mp4", "b.mov", ".hidden.mp4", "notes.txt"):
            open(os.path.join(self.media, n), "w").close()
        outside = os.path.join(self.tmp, "secret.mp4")
        open(outside, "w").close()
        os.symlink(outside, os.path.join(self.media, "link.mp4"))
        self.web = os.path.join(self.tmp, "web")
        os.makedirs(self.web)
        for name, text in (("index.html", "<html>hi</html>"), ("app.js", "//js")):
            with open(os.path.join(self.web, name), "w") as f:
                f.write(text)
        self.settings = Settings(os.path.join(self.tmp, "settings.json"))
        self.settings.load()
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.auth = Auth(self.settings, rotate_on_start=True)
        self.pin = self.auth.current_pin
        rundir = os.path.join(self.tmp, "run")
        os.makedirs(rundir, mode=0o700)
        self.rundir = rundir
        self.player = FakePlayer(rundir)
        board = {"kind": "x86", "model": "test", "arch": "x86_64"}
        self.api = Api(self.player, self.settings, self.auth, Registry(self.settings, "x86"),
                       themes_mod.load_themes(), self.media, board,
                       on_pin=lambda pin: server.write_pin_file(rundir, pin))
        self.api.osc = OscManager(self.api, self.settings, host="127.0.0.1", log=lambda *_: None)
        self.addCleanup(self.api.osc.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, self.web))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, headers=None, raw=None, token=None, csrf=True):
        h = {}
        if method == "POST":
            h["Content-Type"] = "application/json"
            if csrf:
                h["X-PVJ-Request"] = "1"
        if token:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        text = r.read()
        c.close()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return r.status, payload, r

    def pair(self, name="phone"):
        st, body, r = self.call("POST", "/api/pair", {"pin": self.pin, "name": name})
        self.assertEqual(st, 200, body)
        return body["token"], r


class ServerTest(ServerBase):
    # --- authentication ------------------------------------------------
    def test_public_and_protected(self):
        self.assertEqual(self.call("GET", "/api/hello")[0], 200)
        self.assertEqual(self.call("GET", "/api/status")[0], 401)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("POST", "/api/status", {})[0], 405)

    def test_pairing_sets_secure_cookie_and_cookie_authenticates(self):
        token, r = self.pair()
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        st, body, _ = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})
        self.assertEqual(st, 200)
        self.assertEqual(body["device"]["role"], "full")
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)
        self.assertEqual(self.call("GET", "/api/status", token="wrong")[0], 401)

    def test_wrong_pin_then_rate_limit_with_retry_after(self):
        wrong = "0000" if self.pin != "0000" else "1111"
        codes = [self.call("POST", "/api/pair", {"pin": wrong})[0] for _ in range(5)]
        self.assertEqual(codes, [403] * 5)
        st, body, r = self.call("POST", "/api/pair", {"pin": self.pin})
        self.assertEqual(st, 429)
        self.assertGreater(int(r.getheader("Retry-After")), 0)

    # --- CSRF and request hygiene -------------------------------------
    def test_post_needs_header_matching_origin_json_and_sane_size(self):
        token, _ = self.pair()
        body = {"action": "pause"}
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Origin": "http://127.0.0.1:%d" % self.port})[0], 200)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"{bad")[0], 400)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"[1]")[0], 400)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"x" * 70000)[0], 413)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("PUT", "/api/control", token=token)[0], 405)

    def test_origin_check_handles_ipv6_hosts_and_garbage_ports(self):
        token, _ = self.pair()
        ok = {"Host": "[::1]:8080", "Origin": "http://[::1]:8080"}
        self.assertEqual(self.call("POST", "/api/control", {"action": "pause"}, token=token, headers=ok)[0], 200)
        for origin in ("http://127.0.0.1:99999", "http://[::1", "null", "http://", "http://evil.example:80"):
            st, _, _ = self.call("POST", "/api/control", {"action": "pause"}, token=token, headers={"Origin": origin})
            self.assertEqual(st, 403, origin)

    def test_absurdly_nested_json_is_a_clean_400(self):
        token, _ = self.pair()
        st, body, _ = self.call("POST", "/api/control", token=token, raw=b"[" * 60000)
        self.assertEqual(st, 400)
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)

    def test_reset_keeps_the_screen_dark_during_blackout_and_cut_play_stops_old_fades(self):
        token, _ = self.pair()
        self.call("POST", "/api/blackout", {"on": True}, token=token)
        self.call("POST", "/api/control", {"action": "reset"}, token=token)
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"][-1], ("opacity", 0))
        self.call("POST", "/api/blackout", {"on": False}, token=token)
        before = self.api.fader._token
        self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)
        self.assertGreater(self.api.fader._token, before)

    def test_get_never_changes_state(self):
        token, _ = self.pair()
        self.call("GET", "/api/control?action=stop", token=token)
        self.call("GET", "/api/play?file=a.mp4", token=token)
        self.assertEqual(self.player.calls, [])

    # --- roles ---------------------------------------------------------
    def test_roles_are_enforced(self):
        full, _ = self.pair()
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)
        view = body["token"]
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=full)
        live = body["token"]
        self.assertEqual(self.call("GET", "/api/status", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=live)[0], 200)
        for path, body in (("/api/pads", {"bank": 0, "index": 0, "label": "x", "file": "a.mp4"}),
                           ("/api/theme", {"name": "light"}), ("/api/modules/mapper", {"enabled": False}),
                           ("/api/devices/invite", {"name": "x", "role": "view"}), ("/api/pin/rotate", {})):
            self.assertEqual(self.call("POST", path, body, token=live)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/devices", token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/devices/invite", {"name": "x", "role": "full"}, token=full)[0], 400)

    def test_guest_link_session_and_revoke(self):
        full, _ = self.pair()
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)
        st, _, r = self.call("POST", "/api/session", {"token": body["token"]})
        self.assertEqual(st, 200)
        self.assertIn("pvj_token=", r.getheader("Set-Cookie"))
        self.assertEqual(self.call("POST", "/api/session", {"token": "nope"})[0], 403)
        self.call("POST", "/api/devices/revoke", {"id": body["device"]["id"]}, token=full)
        self.assertEqual(self.call("GET", "/api/status", token=body["token"])[0], 401)

    # --- input validation and paths -----------------------------------
    def test_play_rejects_paths_outside_media_folder(self):
        token, _ = self.pair()
        for name in ("../secret.mp4", "/etc/passwd", "..", ".hidden.mp4", "notes.txt", "a/b.mp4",
                     "a.mp4\x00", "", 5, None, "link.mp4"):
            st, _, _ = self.call("POST", "/api/play", {"file": name}, token=token)
            self.assertIn(st, (400, 404), repr(name))
        self.assertEqual(self.player.calls, [])
        st, body, _ = self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)
        self.assertEqual((st, body["playing"]), (200, "a.mp4"))
        kind, paths, loop, spawn = next(c for c in self.player.calls if c[0] == "play")
        self.assertEqual(paths, [os.path.realpath(os.path.join(self.media, "a.mp4"))])
        self.assertFalse(spawn)

    def test_controls_validate_numbers(self):
        token, _ = self.pair()
        ok = [("opacity", 50), ("size", 100), ("position", -50), ("speed", 1.5), ("volume", 100), ("seek", -5),
              ("rotate", 90)]
        for action, value in ok:
            self.assertEqual(self.call("POST", "/api/control", {"action": action, "value": value}, token=token)[0],
                             200, action)
        bad = [("opacity", 101), ("opacity", "50"), ("opacity", True), ("opacity", None), ("size", 201),
               ("speed", 0), ("volume", -1), ("seek", 99999), ("rotate", 45), ("loop", 1), ("nonsense", 1)]
        for action, value in bad:
            st, _, _ = self.call("POST", "/api/control", {"action": action, "value": value}, token=token)
            self.assertEqual(st, 400, (action, value))
        st, _, _ = self.call("POST", "/api/control", token=token, raw=b'{"action":"opacity","value":NaN}')
        self.assertEqual(st, 400)

    def test_stop_volume_step_and_legacy_presets(self):
        token, _ = self.pair()
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=token)[0], 200)
        self.assertEqual(self.player.calls[-1], ("clear",))
        self.assertEqual(self.call("POST", "/api/control", {"action": "volume_step", "value": -10}, token=token)[0], 200)
        self.assertEqual(self.player.calls[-1], ("volume_step", -10.0))
        self.assertEqual(self.call("POST", "/api/control", {"action": "volume_step", "value": 99}, token=token)[0], 400)
        for n in ("05_intro.mp4", "05_outro.mov", "07_other.mp4"):
            open(os.path.join(self.media, n), "w").close()
        st, body, _ = self.call("POST", "/api/play", {"preset": "startlessonce05"}, token=token)
        self.assertEqual((st, body["files"]), (200, 2))
        kind, paths, loop, spawn = next(c for c in reversed(self.player.calls) if c[0] == "play")
        self.assertFalse(loop)  # "once" presets do not loop
        self.assertEqual([os.path.basename(p) for p in paths], ["05_intro.mp4", "05_outro.mov"])
        st, body, _ = self.call("POST", "/api/play", {"preset": "startless07"}, token=token)
        self.assertEqual(st, 200)
        self.assertTrue(next(c for c in reversed(self.player.calls) if c[0] == "play")[2])  # loops
        for bad in ("startless99", "startmaster05; reboot", "../startless01", "reboot", "startslave", 5, None, ""):
            self.assertEqual(self.call("POST", "/api/play", {"preset": bad}, token=token)[0], 400, repr(bad))

    def test_osc_settings_endpoint(self):
        import socket
        full, _ = self.pair()
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=full)
        live = body["token"]
        st, body, _ = self.call("GET", "/api/osc", token=live)
        self.assertEqual((st, body["enabled"], body["listening"]), (200, False, False))
        self.assertEqual(self.call("POST", "/api/osc", {"enabled": True}, token=live)[0], 403)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        st, body, _ = self.call("POST", "/api/osc", {"enabled": True, "port": port, "allow": ["10.20.0.0/16"]}, token=full)
        self.assertEqual((st, body["listening"], body["allow"]), (200, True, ["10.20.0.0/16"]))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"/pvj/speed\0\0,f\0\0" + struct.pack(">f", 3.0), ("127.0.0.1", port))
            deadline = time.time() + 5
            while time.time() < deadline and ("speed", 3.0) not in self.player.calls:
                time.sleep(0.05)
        self.assertIn(("speed", 3.0), self.player.calls)
        from pvj.settings import Settings as S2
        self.assertTrue(S2(self.settings.path).load()["osc"]["enabled"])  # persisted
        st, body, _ = self.call("POST", "/api/osc", {"enabled": False}, token=full)
        self.assertEqual((st, body["listening"]), (200, False))

    def test_osc_settings_validation_and_port_clash(self):
        import socket
        token, _ = self.pair()
        for body in ({"enabled": "yes"}, {"port": 80}, {"port": 70000}, {"port": "9876"}, {"port": True},
                     {"allow": ["0.0.0.0/0"]}, {"allow": ["::/0"]}, {"allow": ["8.0.0.0/7"]}, {"allow": "10.0.0.0/8"},
                     {"allow": ["nonsense"]}, {"allow": ["10.0.0.0/8"] * 17}):
            self.assertEqual(self.call("POST", "/api/osc", body, token=token)[0], 400, body)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        st, body, _ = self.call("POST", "/api/osc", {"enabled": True, "port": blocker.getsockname()[1]}, token=token)
        self.assertEqual(st, 409)
        self.assertFalse(self.settings.data["osc"]["enabled"])  # last working configuration kept
        self.assertFalse(self.call("GET", "/api/osc", token=token)[1]["listening"])

    def test_pads_validation_and_playing_a_pad(self):
        token, _ = self.pair()
        self.assertEqual(self.call("POST", "/api/pads", {"bank": 0, "index": 1, "label": "Tunnel", "file": "b.mov"},
                                   token=token)[0], 200)
        for body in ({"bank": 9, "index": 0}, {"bank": 0, "index": 12}, {"bank": 0, "index": 0, "file": "../x.mp4"},
                     {"bank": 0, "index": 0, "label": "x" * 41}, {"bank": 0, "index": 0, "file": "x.exe"}):
            self.assertEqual(self.call("POST", "/api/pads", body, token=token)[0], 400, body)
        self.assertEqual(self.call("POST", "/api/play", {"pad": [0, 1]}, token=token)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"pad": [0, 5]}, token=token)[0], 400)  # empty pad
        self.assertEqual(self.call("POST", "/api/play", {"pad": [7, 1]}, token=token)[0], 400)
        self.assertEqual(self.call("POST", "/api/play", {"pad": "x"}, token=token)[0], 400)

    def test_blackout_and_opacity_interaction(self):
        token, _ = self.pair()
        self.call("POST", "/api/control", {"action": "opacity", "value": 40}, token=token)
        self.call("POST", "/api/blackout", {"on": True}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 0))
        self.call("POST", "/api/control", {"action": "opacity", "value": 80}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 0))  # blackout still holds
        self.call("POST", "/api/blackout", {"on": False}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 204))
        self.assertEqual(self.call("POST", "/api/blackout", {"on": "yes"}, token=token)[0], 400)

    def test_unexpected_error_is_a_clean_500_and_the_server_keeps_working(self):
        token, _ = self.pair()

        def boom(*a, **k):
            raise RuntimeError("bug")
        self.player.status = boom
        st, body, _ = self.call("GET", "/api/status", token=token)
        self.assertEqual((st, body), (500, {"error": "internal error"}))
        del self.player.status  # back to the class method
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)

    def test_player_down_is_503_not_a_crash(self):
        token, _ = self.pair()
        from pvj.player import PlayerError

        def boom(*a, **k):
            raise PlayerError("player service is not running")
        self.player.play = boom
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)[0], 503)

    # --- modules, themes, pin -----------------------------------------
    def test_modules_and_theme(self):
        token, _ = self.pair()
        st, body, _ = self.call("GET", "/api/modules", token=token)
        self.assertTrue(any(m["id"] == "inputs-ndi" for m in body["modules"]))
        self.assertEqual(self.call("POST", "/api/modules/core", {"enabled": False}, token=token)[0], 409)
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": True}, token=token)[0], 409)
        self.assertEqual(self.call("POST", "/api/modules/Bad..Id", {"enabled": True}, token=token)[0], 404)
        self.assertEqual(self.call("POST", "/api/theme", {"name": "night-red", "accent": "#ffffff"}, token=token)[0], 200)
        st, css, r = self.call("GET", "/theme.css")
        self.assertIn(b"--ac:#ffffff", css)
        self.assertIn(b"--on:#000000", css)
        for body in ({"name": "nope"}, {"name": "light", "accent": "red"},
                     {"name": "light", "accent": "#fff;}*{display:none"}):
            self.assertEqual(self.call("POST", "/api/theme", body, token=token)[0], 400, body)

    def real_panel(self):
        """From here on the test talks to a server that serves the real pvj/web (the others use a two-file folder)."""
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, server.WEB_DIR))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]

    def test_a_style_comes_from_the_theme_and_reaches_the_page_as_a_fixed_name(self):
        self.real_panel()
        token, _ = self.pair()
        st, body, _ = self.call("GET", "/api/theme", token=token)
        styles = {t["id"]: (t["style"], t["areas"]) for t in body["available"]}
        self.assertEqual(styles["dark-stage"], ("default", False))
        self.assertEqual(styles["signal"], ("signal", True))
        self.assertEqual(styles["signal-light"], ("signal", True))
        st, page, _ = self.call("GET", "/")
        self.assertIn(b'<html lang="en">', page)                    # the default look: the page as the file has it
        self.assertNotIn(b"data-style", page)
        with open(os.path.join(server.WEB_DIR, "index.html"), "rb") as f:
            self.assertEqual(page, f.read())
        self.assertEqual(self.call("POST", "/api/theme", {"name": "signal", "accent": "#ff0000"}, token=token)[0], 200)
        st, page, _ = self.call("GET", "/")
        self.assertEqual(page.count(b'<html lang="en" data-style="signal">'), 1)
        self.assertEqual(page.count(b"data-style"), 1)
        st, css, _ = self.call("GET", "/theme.css")
        self.assertIn(b"--ar-shaders:#ff4fa3", css)
        self.assertIn(b"--ac:#ffd60a", css)                         # the accent chosen earlier does not replace an area theme's
        self.assertEqual(self.call("POST", "/api/theme", {"name": "signal-light", "accent": None}, token=token)[0], 200)
        self.assertIn(b'data-style="signal"', self.call("GET", "/")[1])
        # a stored theme this box no longer has, or one whose style this version does not know: the default look
        self.settings.data["theme"] = {"name": "gone", "accent": None}
        self.assertNotIn(b"data-style", self.call("GET", "/")[1])
        self.assertEqual(self.api.theme_style(), "default")
        self.api.themes["later"] = dict(self.api.themes["signal"], id="later", style="neon")
        self.settings.data["theme"] = {"name": "later", "accent": None}
        self.assertEqual(self.api.theme_style(), "default")
        self.assertNotIn(b"data-style", self.call("GET", "/")[1])
        # a damaged settings file: the page and its colours still come, in the look the box comes with (found by the
        # review: the page is served before anyone has paired, and it dropped the connection)
        with open(os.path.join(server.WEB_DIR, "index.html"), "rb") as f:
            plain_page = f.read()
        for broken in ({}, None, "x", 5, [], {"name": ["a"]}, {"name": None}, {"name": {"a": 1}}, {"accent": "#ffffff"}, {"name": "signal", "accent": ["x"]},
                       {"name": "signal", "accent": "#ffffff\n"}):
            self.settings.data["theme"] = broken
            st, page, _ = self.call("GET", "/")
            self.assertEqual((st, page), (200, plain_page) if not (isinstance(broken, dict) and broken.get("name") == "signal") else (st, page), repr(broken))
            self.assertEqual(st, 200, repr(broken))
            st, css, _ = self.call("GET", "/theme.css")
            self.assertEqual(st, 200, repr(broken))
            self.assertRegex(css, rb"^:root\{[-a-z0-9:#;]+\}$", repr(broken))
        for broken in ({}, None, "x", {"name": ["a"]}):
            self.settings.data["theme"] = broken
            self.assertEqual(self.api.theme_style(), "default", repr(broken))
            self.assertIn(b"--bg:#121214", self.call("GET", "/theme.css")[1], repr(broken))
        self.settings.data["theme"] = {"name": "dark-stage", "accent": None}
        for body in ({"name": ["signal"]}, {"name": {"a": 1}}, {"name": None}, {"name": "signal", "accent": "#ffffff\n"}, {"name": "light", "accent": "\n#ffffff"}):
            self.assertEqual(self.call("POST", "/api/theme", body, token=token)[0], 400, body)
        self.settings.data["theme"] = {"name": "later", "accent": None}
        self.api.themes["later"]["style"] = 'x"><script>'           # not a name from the fixed set: never written
        self.assertNotIn(b"script>", self.call("GET", "/")[1].split(b"<head>")[0])
        self.assertEqual(server.styled_html(b'<html lang="en">', 'x"><script>'), b'<html lang="en">')
        self.assertEqual(server.styled_html(b'<html lang="en">', "default"), b'<html lang="en">')
        del self.api.themes["later"]
        self.assertEqual(self.call("POST", "/api/theme", {"name": "dark-stage"}, token=token)[0], 200)
        self.assertNotIn(b"data-style", self.call("GET", "/")[1])

    def test_fonts_are_served_from_the_box_and_nothing_else_from_that_folder(self):
        self.real_panel()
        st, page, r = self.call("GET", "/")
        self.assertIn("font-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        for name in ("archivo-latin.06fa7831.woff2", "jetbrains-mono-500-latin.6c95bc2f.woff2"):
            st, body, r = self.call("GET", "/fonts/" + name)
            self.assertEqual(st, 200, name)
            self.assertEqual(r.getheader("Content-Type"), "font/woff2")
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r.getheader("Cache-Control"), "max-age=86400")
            self.assertEqual(body[:4], b"wOF2")
            with open(os.path.join(server.WEB_DIR, "fonts", name), "rb") as f:
                self.assertEqual(body, f.read())
        self.assertEqual(self.call("GET", "/app.css")[2].getheader("Cache-Control"), "no-store")
        for path in ("/fonts", "/fonts/", "/fonts/OFL-Archivo.txt", "/fonts/../app.css", "/fonts/%2e%2e/app.css", "/fonts/..%2fapp.css",
                     "/fonts/../../settings.json", "/fonts/archivo-latin.06fa7831.woff2/", "/fonts//archivo-latin.06fa7831.woff2", "/fonts/nope.woff2",
                     "/fonts/archivo-latin.06fa7831.woff2%00", "/FONTS/archivo-latin.06fa7831.woff2"):
            self.assertEqual(self.call("GET", path)[0], 404, path)
        # the map is made of files only: a folder, or a file with another ending, is never in it
        web = tempfile.mkdtemp()
        os.makedirs(os.path.join(web, "fonts", "deep.woff2"))
        os.makedirs(os.path.join(web, "sub"))
        for name in ("index.html", "fonts/a.woff2", "fonts/notes.txt", "sub/x.js"):
            with open(os.path.join(web, name), "w") as f:
                f.write("x")
        self.assertEqual(server.static_files(web), {"/": "index.html", "/index.html": "index.html", "/fonts/a.woff2": os.path.join("fonts", "a.woff2")})
        # a symbolic link is never served (found by the review): not a linked font, not a linked file beside the page,
        # and not a fonts folder that is itself a link
        secret = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(secret, "w") as f:
            f.write("secret")
        os.symlink(secret, os.path.join(web, "fonts", "leak.woff2"))
        os.symlink(secret, os.path.join(web, "leak.js"))
        os.symlink(os.path.join(web, "fonts", "a.woff2"), os.path.join(web, "fonts", "inside.woff2"))
        self.assertEqual(server.static_files(web), {"/": "index.html", "/index.html": "index.html", "/fonts/a.woff2": os.path.join("fonts", "a.woff2")})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, web))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]
        self.assertEqual(self.call("GET", "/fonts/a.woff2")[0], 200)
        for path in ("/fonts/leak.woff2", "/leak.js", "/fonts/inside.woff2"):
            st, body, _ = self.call("GET", path)
            self.assertEqual(st, 404, path)
            self.assertNotIn(b"secret", body if isinstance(body, bytes) else json.dumps(body).encode(), path)
        linked = tempfile.mkdtemp()
        with open(os.path.join(linked, "index.html"), "w") as f:
            f.write("x")
        os.symlink(os.path.join(web, "fonts"), os.path.join(linked, "fonts"))
        self.assertEqual(server.static_files(linked), {"/": "index.html", "/index.html": "index.html"})

    def test_pin_rotation_writes_pin_file_and_invalidates_old_pin(self):
        token, _ = self.pair()
        old = self.pin
        st, body, _ = self.call("POST", "/api/pin/rotate", {}, token=token)
        self.assertEqual(st, 200)
        with open(os.path.join(self.rundir, "pin")) as f:
            self.assertEqual(f.read().strip(), body["pin"])
        self.assertEqual(oct(os.stat(os.path.join(self.rundir, "pin")).st_mode & 0o777), "0o600")   # not for group pvj (D45)
        if body["pin"] != old:
            self.assertEqual(self.call("POST", "/api/pair", {"pin": old})[0], 403)
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)  # paired device stays paired

    # --- static files and headers -------------------------------------
    def test_static_files_and_security_headers(self):
        st, body, r = self.call("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        for path in ("/../settings.json", "/%2e%2e/settings.json", "/settings.json", "/etc/passwd", "/.env"):
            self.assertEqual(self.call("GET", path)[0], 404, path)
        st, _, r = self.call("GET", "/api/hello")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_a_bookmark_or_a_home_screen_tile_gets_an_icon(self):
        """The real box answered 404 for /favicon.ico and the two apple-touch-icon names (seen in its journal)."""
        import struct
        import zlib
        from pvj import icon
        seen = set()
        for path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico?v=2"):
            for method in ("GET", "HEAD"):
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                c.request(method, path)                               # no token: a browser asks before anyone is paired
                r = c.getresponse()
                body = r.read()
                c.close()
                self.assertEqual((r.status, r.getheader("Content-Type"), r.getheader("X-Content-Type-Options")), (200, "image/png", "nosniff"), path)
                self.assertEqual(int(r.getheader("Content-Length")), len(icon.png()))
                if method == "GET":
                    seen.add(body)
        (png,) = seen                                                 # one picture at every address
        self.assertLess(len(png), 4096)                               # a few KB at most
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (180, 180))
        self.assertEqual(png[24:26], b"\x08\x02")                     # 8 bits, RGB: no transparency (iOS paints that black)
        at, pixels = 8, b""
        while at < len(png):                                          # every chunk's checksum is right, and the picture unpacks
            n, kind = struct.unpack(">I", png[at:at + 4])[0], png[at + 4:at + 8]
            data = png[at + 8:at + 8 + n]
            self.assertEqual(struct.unpack(">I", png[at + 8 + n:at + 12 + n])[0], zlib.crc32(kind + data) & 0xFFFFFFFF)
            if kind == b"IDAT":
                pixels += data
            at += 12 + n
        raw = zlib.decompress(pixels)
        self.assertEqual(len(raw), 180 * (1 + 3 * 180))
        colours = {raw[y * 541 + 1 + 3 * x:y * 541 + 4 + 3 * x] for y in range(0, 180, 6) for x in range(0, 180, 6)}
        self.assertEqual(colours, {bytes(icon.BACK), bytes(icon.MARK)})  # a mark on a ground, not an empty tile
        import pvj
        with open(os.path.join(os.path.dirname(pvj.__file__), "web", "index.html")) as f:      # the real page names them
            text = f.read()
        self.assertIn('href="/favicon.ico"', text)
        self.assertIn('rel="apple-touch-icon" href="/apple-touch-icon.png"', text)
        self.assertEqual(self.call("GET", "/apple-touch-icon-120x120.png")[0], 404)      # only the three names

    def test_settings_file_holds_no_clear_tokens_or_pin(self):
        token, _ = self.pair()
        with open(self.settings.path) as f:
            raw = f.read()
        self.assertNotIn(token, raw)


if __name__ == "__main__":
    unittest.main()


def make_test_video(directory):
    """A 2 second clip made by mpv itself; None if this mpv cannot encode."""
    import shutil
    import subprocess
    if not shutil.which("mpv"):
        return None
    path = os.path.join(directory, "clip.mkv")
    subprocess.run(["mpv", "av://lavfi:testsrc=size=160x120:rate=25", "--length=2", "--o=" + path, "--no-terminal"],
                   capture_output=True, timeout=60)
    return path if os.path.isfile(path) and os.path.getsize(path) > 1000 else None


class EndToEndTest(ServerBase):
    """Same server, real headless mpv behind it, real video file."""

    def setUp(self):
        super().setUp()
        clip = make_test_video(self.media)
        if not clip:
            self.skipTest("cannot generate a test video with this mpv")
        from pvj.player import Player
        real = Player(extra_args=["--vo=null", "--ao=null"], rundir=self.rundir)
        self.addCleanup(real.stop)
        self.api.player = real
        self.api.spawn = True  # development mode: the API starts mpv itself

    def test_play_through_http_reaches_real_mpv(self):
        token, _ = self.pair()
        st, body, _ = self.call("POST", "/api/play", {"file": "clip.mkv"}, token=token)
        self.assertEqual(st, 200, body)
        st, status, _ = self.call("GET", "/api/status", token=token)
        self.assertTrue(status["player"]["running"])
        self.assertTrue(status["player"]["path"].endswith("clip.mkv"))
        self.call("POST", "/api/control", {"action": "speed", "value": 2}, token=token)
        self.call("POST", "/api/control", {"action": "mute", "value": True}, token=token)
        self.call("POST", "/api/blackout", {"on": True}, token=token)
        st, status, _ = self.call("GET", "/api/status", token=token)
        self.assertEqual(status["player"]["speed"], 2.0)
        self.assertTrue(status["player"]["muted"])
        self.assertEqual(self.api.player.ipc.request("get_property", "brightness"), -100)
        self.call("POST", "/api/blackout", {"on": False}, token=token)
        self.assertEqual(self.api.player.ipc.request("get_property", "brightness"), 0)
        self.assertEqual(self.call("POST", "/api/player/restart", {}, token=token)[0], 200)


class MediaBase(ServerBase):
    OCT = {"Content-Type": "application/octet-stream"}

    def upload(self, name, data, token, query="", **kw):
        from urllib.parse import quote
        return self.call("POST", "/api/media/upload?name=%s%s" % (quote(name), query), raw=data, token=token,
                         headers=dict(self.OCT, **kw.pop("headers", {})), **kw)

    def leftovers(self):
        return [n for n in os.listdir(self.media) if n.startswith(".upload-")]

class MediaTest(MediaBase):
    def test_listing_has_sizes_and_free_space(self):
        token, _ = self.pair()
        st, body, _ = self.call("GET", "/api/media", token=token)
        self.assertEqual(st, 200)
        self.assertEqual(body["files"], ["a.mp4", "b.mov", "link.mp4"])
        self.assertEqual({d["name"] for d in body["details"]}, set(body["files"]))
        self.assertGreater(body["free"], 0)
        self.assertGreater(body["max_upload"], 0)

    def test_upload_stores_the_exact_bytes_and_refuses_overwrite_unless_asked(self):
        token, _ = self.pair()
        data = os.urandom(3 * 1024 * 1024 + 17)  # more than one copy chunk
        st, body, _ = self.upload("new clip.mp4", data, token)
        self.assertEqual((st, body), (200, {"name": "new clip.mp4", "size": len(data)}))
        with open(os.path.join(self.media, "new clip.mp4"), "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(oct(os.stat(os.path.join(self.media, "new clip.mp4")).st_mode & 0o777), "0o664")
        self.assertEqual(self.upload("new clip.mp4", b"other", token)[0], 409)
        with open(os.path.join(self.media, "new clip.mp4"), "rb") as f:
            self.assertEqual(f.read(), data)  # untouched by the refused upload
        self.assertEqual(self.upload("new clip.mp4", b"replacement", token, query="&replace=1")[0], 200)
        with open(os.path.join(self.media, "new clip.mp4"), "rb") as f:
            self.assertEqual(f.read(), b"replacement")
        self.assertEqual(self.leftovers(), [])

    def test_bad_names_types_and_missing_length_are_refused_before_anything_is_stored(self):
        token, _ = self.pair()
        for name in ("../evil.mp4", "..%2Fevil.mp4", ".hidden.mp4", "a/b.mp4", "a\\b.mp4", "clip.exe", "clip", "",
                     "x" * 130 + ".mp4", "bad\x00.mp4", "clip.php.txt", ".upload-x.mp4"):
            st, _, _ = self.upload(name, b"data", token)
            self.assertEqual(st, 400, repr(name))
        st, _, _ = self.call("POST", "/api/media/upload?name=x.mp4", raw=b"data", token=token,
                             headers={"Content-Type": "application/json"})
        self.assertEqual(st, 415)
        st, _, _ = self.call("POST", "/api/media/upload?name=x.mp4", raw=b"", token=token, headers=self.OCT)
        self.assertEqual(st, 411)  # empty body
        self.assertEqual(sorted(os.listdir(self.media)), sorted([".hidden.mp4", "a.mp4", "b.mov", "link.mp4", "notes.txt"]))

    def test_only_full_devices_with_the_header_may_upload(self):
        full, _ = self.pair()
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=full)
        live = body["token"]
        self.assertEqual(self.upload("x.mp4", b"data", live)[0], 403)
        self.assertEqual(self.upload("x.mp4", b"data", None)[0], 401)
        self.assertEqual(self.upload("x.mp4", b"data", full, csrf=False)[0], 403)
        self.assertEqual(self.upload("x.mp4", b"data", full, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertFalse(os.path.exists(os.path.join(self.media, "x.mp4")))

    def test_size_limit_and_full_disk_are_refused_up_front(self):
        import pvj.api as api_mod
        token, _ = self.pair()
        old = api_mod.MAX_UPLOAD_BYTES
        api_mod.MAX_UPLOAD_BYTES = 10
        self.addCleanup(setattr, api_mod, "MAX_UPLOAD_BYTES", old)
        self.assertEqual(self.upload("big.mp4", b"x" * 11, token)[0], 413)
        api_mod.MAX_UPLOAD_BYTES = old
        self.api._free_space = lambda: 100 * 1024 * 1024  # less than the reserve
        st, body, _ = self.upload("full.mp4", b"x" * 10, token)
        self.assertEqual(st, 507)
        self.assertFalse(os.path.exists(os.path.join(self.media, "full.mp4")))

    def test_cut_short_upload_leaves_nothing_behind(self):
        import socket
        token, _ = self.pair()
        c = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        c.sendall(("POST /api/media/upload?name=cut.mp4 HTTP/1.1\r\nHost: 127.0.0.1\r\nX-PVJ-Request: 1\r\n"
                   "Authorization: Bearer %s\r\nContent-Type: application/octet-stream\r\n"
                   "Content-Length: 5000000\r\n\r\n" % token).encode() + b"x" * 1000)
        time.sleep(0.3)
        c.close()  # the phone lost its connection half way
        time.sleep(0.5)
        self.assertFalse(os.path.exists(os.path.join(self.media, "cut.mp4")))
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.upload("after.mp4", b"fine", token)[0], 200)  # the lock was released

    def test_second_upload_is_refused_while_one_is_running(self):
        import socket
        token, _ = self.pair()
        slow = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(slow.close)
        slow.sendall(("POST /api/media/upload?name=slow.mp4 HTTP/1.1\r\nHost: 127.0.0.1\r\nX-PVJ-Request: 1\r\n"
                      "Authorization: Bearer %s\r\nContent-Type: application/octet-stream\r\n"
                      "Content-Length: 2000\r\n\r\n" % token).encode() + b"x" * 100)
        time.sleep(0.3)
        st, body, _ = self.upload("second.mp4", b"data", token)
        self.assertEqual((st, body["error"]), (409, "another upload is in progress"))
        slow.sendall(b"x" * 1900)  # finish the first one
        slow.settimeout(5)
        self.assertIn(b"200 OK", slow.recv(4096))
        self.assertTrue(os.path.exists(os.path.join(self.media, "slow.mp4")))

    def test_delete_and_rename_are_validated(self):
        token, _ = self.pair()
        self.assertEqual(self.call("POST", "/api/media/rename", {"name": "a.mp4", "new": "renamed.mp4"}, token=token)[0], 200)
        self.assertTrue(os.path.exists(os.path.join(self.media, "renamed.mp4")))
        for body in ({"name": "renamed.mp4", "new": "b.mov"}, {"name": "renamed.mp4", "new": "../x.mp4"},
                     {"name": "renamed.mp4", "new": "x.exe"}, {"name": "../a.mp4", "new": "y.mp4"},
                     {"name": "link.mp4", "new": "z.mp4"}, {"name": "renamed.mp4"}, {"name": "gone.mp4", "new": "q.mp4"}):
            st, _, _ = self.call("POST", "/api/media/rename", body, token=token)
            self.assertIn(st, (400, 404, 409), body)
        self.assertEqual(self.call("POST", "/api/media/delete", {"name": "renamed.mp4"}, token=token)[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.media, "renamed.mp4")))
        for name in ("../secret.mp4", "link.mp4", "notes.txt", "missing.mp4", 5, None):
            st, _, _ = self.call("POST", "/api/media/delete", {"name": name}, token=token)
            self.assertIn(st, (400, 404), repr(name))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "secret.mp4")))  # the symlink target is safe
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=token)
        self.assertEqual(self.call("POST", "/api/media/delete", {"name": "b.mov"}, token=body["token"])[0], 403)


class MediaHardeningTest(MediaBase):
    """Findings from the independent review of the upload feature."""

    def open_upload(self, token, name, length, sent, extra_headers=""):
        import socket
        c = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(c.close)
        c.sendall(("POST /api/media/upload?name=%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-PVJ-Request: 1\r\n"
                   "Authorization: Bearer %s\r\nContent-Type: application/octet-stream\r\n%s"
                   "Content-Length: %d\r\n\r\n" % (name, token, extra_headers, length)).encode() + sent)
        time.sleep(0.3)
        return c

    def reply(self, c):
        c.settimeout(5)
        return c.recv(4096)

    def read_file(self, name):
        with open(os.path.join(self.media, name), "rb") as f:
            return f.read()

    def test_a_file_renamed_into_place_during_an_upload_is_never_overwritten(self):
        token, _ = self.pair()
        with open(os.path.join(self.media, "a.mp4"), "wb") as f:
            f.write(b"precious")
        c = self.open_upload(token, "x.mp4", 1000, b"y" * 100)
        st, _, _ = self.call("POST", "/api/media/rename", {"name": "a.mp4", "new": "x.mp4"}, token=token)
        self.assertEqual(st, 200)  # the name was free at that moment
        c.sendall(b"y" * 900)
        self.assertIn(b"409", self.reply(c))  # the upload notices and refuses, instead of clobbering
        self.assertEqual(self.read_file("x.mp4"), b"precious")
        self.assertEqual(self.leftovers(), [])

    def test_stale_temp_files_from_a_crash_are_swept(self):
        token, _ = self.pair()
        for n in (".upload-crash1", ".upload-crash2"):
            with open(os.path.join(self.media, n), "wb") as f:
                f.write(b"x" * 1000)
        self.assertEqual(self.api.sweep_stale_uploads(), 2)
        with open(os.path.join(self.media, ".upload-crash3"), "wb") as f:
            f.write(b"x")
        self.assertEqual(self.upload("ok.mp4", b"data", token)[0], 200)  # an upload also clears leftovers
        self.assertEqual(self.leftovers(), [])

    def test_a_trickling_sender_is_cut_off_and_frees_the_lock(self):
        import pvj.api as api_mod
        token, _ = self.pair()
        old = (api_mod.RATE_GRACE_SECONDS, api_mod.MIN_UPLOAD_RATE)
        api_mod.RATE_GRACE_SECONDS, api_mod.MIN_UPLOAD_RATE = 0.4, 10 * 1024 * 1024
        self.addCleanup(lambda: setattr(api_mod, "RATE_GRACE_SECONDS", old[0]))
        self.addCleanup(lambda: setattr(api_mod, "MIN_UPLOAD_RATE", old[1]))
        c = self.open_upload(token, "slow.mp4", 5000000, b"x" * 100)
        time.sleep(0.6)
        c.sendall(b"x" * 100)  # the next packet arrives after the grace period, far below the minimum rate
        self.assertIn(b"408", self.reply(c))
        self.assertEqual(self.leftovers(), [])
        self.assertFalse(os.path.exists(os.path.join(self.media, "slow.mp4")))
        api_mod.RATE_GRACE_SECONDS, api_mod.MIN_UPLOAD_RATE = old
        self.assertEqual(self.upload("after.mp4", b"fine", token)[0], 200)

    def test_revoking_a_device_stops_its_running_upload(self):
        owner, _ = self.pair("owner")
        st, body, _ = self.call("POST", "/api/pair", {"pin": self.pin, "name": "second"})
        second, second_id = body["token"], body["device"]["id"]
        c = self.open_upload(second, "revoked.mp4", 1000, b"z" * 100)
        self.assertEqual(self.call("POST", "/api/devices/revoke", {"id": second_id}, token=owner)[0], 200)
        c.sendall(b"z" * 100)
        self.assertIn(b"403", self.reply(c))
        self.assertFalse(os.path.exists(os.path.join(self.media, "revoked.mp4")))
        self.assertEqual(self.upload("free.mp4", b"ok", owner)[0], 200)  # lock released

    def test_upload_creates_a_missing_media_folder(self):
        import shutil
        token, _ = self.pair()
        for n in os.listdir(self.media):
            p = os.path.join(self.media, n)
            os.unlink(p)
        shutil.rmtree(self.media)
        st, body, _ = self.upload("first.mp4", b"data", token)
        self.assertEqual((st, body["name"]), (200, "first.mp4"))

    def test_links_inside_the_folder_are_never_deleted_or_renamed_through(self):
        token, _ = self.pair()
        os.symlink(os.path.join(self.media, "a.mp4"), os.path.join(self.media, "alias.mp4"))
        self.assertEqual(self.call("POST", "/api/media/delete", {"name": "alias.mp4"}, token=token)[0], 409)
        self.assertEqual(self.call("POST", "/api/media/rename", {"name": "alias.mp4", "new": "b2.mp4"}, token=token)[0], 409)
        self.assertTrue(os.path.exists(os.path.join(self.media, "a.mp4")))

    def test_names_are_checked_in_bytes_and_for_invisible_characters(self):
        token, _ = self.pair()
        for name in ("\u20ac" * 100 + ".mp4", "evil\u202egnp.mp4", "c1\x85.mp4", "x.mp4\n", "zero\u200bwidth.mp4"):
            st, _, _ = self.upload(name, b"data", token)
            self.assertEqual(st, 400, repr(name))
        st, _, _ = self.call("POST", "/api/media/upload?name=%ff%fe.mp4", raw=b"data", token=token, headers=self.OCT)
        self.assertEqual(st, 400)  # bytes that are not valid UTF-8
        self.assertEqual(self.upload("caf\u00e9 \u65e5\u672c.mp4", b"data", token)[0], 200)  # real names still work

    def test_chunked_bodies_are_refused_not_stored_with_their_framing(self):
        token, _ = self.pair()
        c = self.open_upload(token, "chunk.mp4", 10, b"5\r\nhello\r\n0\r\n\r\n", extra_headers="Transfer-Encoding: chunked\r\n")
        self.assertIn(b"501", self.reply(c))
        self.assertFalse(os.path.exists(os.path.join(self.media, "chunk.mp4")))

    def test_pads_follow_a_rename_and_delete_reports_pads_using_the_clip(self):
        token, _ = self.pair()
        self.call("POST", "/api/pads", {"bank": 0, "index": 4, "label": "Intro", "file": "b.mov"}, token=token)
        self.call("POST", "/api/media/rename", {"name": "b.mov", "new": "b-renamed.mov"}, token=token)
        self.assertEqual(self.settings.data["pads"]["banks"][0]["pads"][4]["file"], "b-renamed.mov")
        st, body, _ = self.call("POST", "/api/media/delete", {"name": "b-renamed.mov"}, token=token)
        self.assertEqual((st, body["pads_using"]), (200, 1))


class NetworkApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        from tests.test_netd import FakeNm, make_sysfs
        from pvj.netd import NetService
        self.nm = FakeNm()
        self.sysfs = make_sysfs()
        self.kdir = tempfile.mkdtemp()
        self.svc = NetService(runner=self.nm, sysfs=self.sysfs, keyfile_dir=self.kdir)

        class Direct:
            def request(_, message):
                return self.svc.handle(message)
        self.api.net = Direct()
        self.api._sysfs = self.sysfs
        self.api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]
        # the module is beta and off by default: make one test switch it on the way a user would
        self.token, _ = self.pair()

    def enable(self):
        manifests = self.api.registry.manifests
        self.assertEqual(self.call("POST", "/api/modules/network", {"enabled": True}, token=self.token)[0], 200, manifests["network"])

    STATIC = {"iface": "eth0", "mode": "static", "address": "192.168.50.20", "prefix": 24, "gateway": "192.168.50.1"}

    def test_off_by_default_and_full_access_only(self):
        st, body, _ = self.call("GET", "/api/network", token=self.token)
        self.assertEqual(st, 409)
        self.assertIn("Network module", body["error"])
        self.assertEqual(self.call("POST", "/api/network/apply", self.STATIC, token=self.token)[0], 409)
        self.enable()
        _, inv, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=self.token)
        for method, path in (("GET", "/api/network"), ("POST", "/api/network/apply"), ("POST", "/api/network/confirm"),
                             ("POST", "/api/network/revert"), ("POST", "/api/network/plan"), ("POST", "/api/network/scan")):
            body = self.STATIC if method == "POST" else None
            self.assertEqual(self.call(method, path, body, token=inv["token"])[0], 403, path)
        self.assertEqual(self.call("GET", "/api/network")[0], 401)

    def test_status_lists_wired_first_with_addresses(self):
        self.enable()
        st, body, _ = self.call("GET", "/api/network", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual([i["name"] for i in body["interfaces"]], ["eth0", "wlan0"])
        self.assertEqual(body["interfaces"][0]["addresses"], ["192.168.1.9/24"])
        self.assertTrue(body["helper"])
        self.assertEqual(body["modes"], ["dhcp", "static", "linklocal", "share"])
        self.assertIsNone(body["pending"])

    def test_plan_apply_confirm_and_revert_through_the_panel(self):
        self.enable()
        st, plan, _ = self.call("POST", "/api/network/plan", self.STATIC, token=self.token)
        self.assertEqual(st, 200)
        self.assertTrue(any("192.168.50.20/24" in c for c in plan["commands"]))
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)  # a plan changes nothing
        st, body, _ = self.call("POST", "/api/network/apply", self.STATIC, token=self.token)
        self.assertEqual((st, body["pending"]["iface"]), (200, "eth0"))
        self.assertEqual(self.call("POST", "/api/network/apply", {"iface": "eth0", "mode": "dhcp"}, token=self.token)[0], 409)
        self.assertEqual(self.call("POST", "/api/network/revert", {}, token=self.token)[0], 200)
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.call("POST", "/api/network/apply", self.STATIC, token=self.token)
        st, body, _ = self.call("POST", "/api/network/confirm", {}, token=self.token)
        self.assertEqual((st, body["pending"]), (200, None))
        self.assertEqual(self.nm.profiles["pvj-eth0"]["connection.autoconnect"], "yes")
        self.assertEqual(self.call("POST", "/api/network/confirm", {}, token=self.token)[0], 409)  # nothing pending

    WIFI = {"iface": "wlan0", "mode": "dhcp", "ssid": "Leyline Staff", "password": "s3cret pass"}

    def test_wifi_through_the_panel_never_returns_the_password(self):
        self.enable()
        st, body, _ = self.call("GET", "/api/network", token=self.token)
        self.assertEqual(body["wifi_modes"], ["dhcp", "static", "hotspot", "off"])
        self.assertEqual(body["wifi"], {"hardware": True, "radio": True, "ports": {"wlan0": None}})
        st, plan, _ = self.call("POST", "/api/network/plan", self.WIFI, token=self.token)
        self.assertEqual(st, 200)
        st, body, _ = self.call("POST", "/api/network/apply", self.WIFI, token=self.token)
        self.assertEqual((st, body["pending"]["ssid"], body["config"]["password_set"]), (200, "Leyline Staff", True))
        _, status, _ = self.call("GET", "/api/network", token=self.token)
        self.assertNotIn("s3cret", json.dumps([plan, body, status]))
        self.assertEqual(self.nm.profiles["pvj-wlan0-try"]["_kf"]["wifi-security.psk"], "s3cret\\spass")
        self.assertEqual(self.call("POST", "/api/network/confirm", {}, token=self.token)[0], 200)
        _, status, _ = self.call("GET", "/api/network", token=self.token)
        self.assertEqual(status["wifi"]["ports"]["wlan0"], {"ssid": "Leyline Staff", "hotspot": False})

    def test_wifi_scan(self):
        self.enable()
        self.nm.scan_text = "Leyline Staff:60:WPA2:36\n"
        st, body, _ = self.call("POST", "/api/network/scan", {"iface": "wlan0"}, token=self.token)
        self.assertEqual((st, body["networks"][0]["ssid"]), (200, "Leyline Staff"))
        self.assertEqual(self.call("POST", "/api/network/scan", {"iface": "eth0"}, token=self.token)[0], 409)
        for bad in ({}, {"iface": "wlan0; reboot"}, {"iface": 5}, []):
            self.assertEqual(self.call("POST", "/api/network/scan", bad, token=self.token)[0], 400, bad)

    def test_bad_requests_are_400_before_the_helper_sees_them(self):
        self.enable()
        bad = [dict(self.STATIC, address="8.8.8.8; reboot"), dict(self.STATIC, prefix=99), {"iface": "wlan0", "mode": "dhcp"},
               {"iface": "eth0", "mode": "bridge"}, dict(self.STATIC, gateway="10.0.0.1"), {"iface": "../eth0", "mode": "dhcp"}]
        for body in bad:
            self.assertEqual(self.call("POST", "/api/network/apply", body, token=self.token)[0], 400, body)
        self.assertEqual(self.nm.calls, [])

    def test_helper_down_is_a_clean_503_and_status_still_works(self):
        self.enable()
        from pvj.netcfg import NetError

        class Down:
            def request(_, message):
                raise NetError("the network helper (pvj-netd) is not running")
        self.api.net = Down()
        st, body, _ = self.call("POST", "/api/network/apply", self.STATIC, token=self.token)
        self.assertEqual((st, body["error"]), (503, "the network helper (pvj-netd) is not running"))
        st, body, _ = self.call("GET", "/api/network", token=self.token)
        self.assertEqual((st, body["helper"]), (200, False))
        self.assertEqual(self.call("POST", "/api/network/plan", self.STATIC, token=self.token)[0], 200)  # local preview

    def test_a_pending_change_can_still_be_confirmed_or_reverted_after_the_module_is_switched_off(self):
        self.enable()
        self.call("POST", "/api/network/apply", self.STATIC, token=self.token)
        self.assertEqual(self.call("POST", "/api/modules/network", {"enabled": False}, token=self.token)[0], 200)
        self.assertEqual(self.call("GET", "/api/network", token=self.token)[0], 409)  # new work is gated
        self.assertEqual(self.call("POST", "/api/network/apply", self.STATIC, token=self.token)[0], 409)
        self.assertEqual(self.call("POST", "/api/network/revert", {}, token=self.token)[0], 200)  # but undoing is not
        self.assertEqual(self.nm.active, "Wired connection 1")

    def test_a_range_used_by_another_port_is_a_400(self):
        self.enable()
        self.api._ip_json = lambda: [{"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.5.0.2", "prefixlen": 24}]}]
        st, body, _ = self.call("POST", "/api/network/apply", dict(self.STATIC, address="10.5.0.9", gateway="10.5.0.1"),
                                token=self.token)
        self.assertEqual(st, 400)
        self.assertIn("wlan0", body["error"])

    def test_failed_apply_reports_the_restored_state(self):
        self.enable()
        self.nm.fail_on = "connection up id pvj-eth0-try"
        st, body, _ = self.call("POST", "/api/network/apply", self.STATIC, token=self.token)
        self.assertEqual(st, 409)
        self.assertIn("previous setup was restored", body["error"])
        self.assertEqual(self.nm.active, "Wired connection 1")


class HostileClientTest(ServerBase):
    """Slow and flooding clients must not exhaust the server."""

    def setUp(self):
        super().setUp()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = server.PvjServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, self.web,
                                                                             max_lifetime=1.0), max_connections=3)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.socks = []
        self.addCleanup(lambda: [x.close() for x in self.socks])

    def idle(self):
        import socket
        c = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        c.sendall(b"GET /api/hello HTTP/1.1\r\nX-Slow: ")  # never finishes the header
        self.socks.append(c)
        return c

    def test_connection_cap_gives_503_and_slow_clients_are_cut_off(self):
        for _ in range(3):
            self.idle()
        time.sleep(0.2)
        st, _, _ = self.call("GET", "/api/hello")
        self.assertEqual(st, 503)  # cap reached: refused straight away, no thread spent
        time.sleep(1.6)  # the lifetime timer closes the trickling connections
        for c in self.socks:
            c.settimeout(2)
            self.assertEqual(c.recv(1024), b"")
        self.assertEqual(self.call("GET", "/api/hello")[0], 200)  # capacity is back


class ManyAtOnceTest(ServerBase):
    """Several browsers opening the panel in the same second: every file arrives (D68). A browser opens six
    connections at once and every request is its own connection, so three browsers ask for eighteen while the
    kernel, asked for socketserver's queue of five, kept about five waiting: macOS reset the rest, Linux makes
    them ask again a second later."""

    PATHS = ("/", "/app.js", "/api/hello")

    def serve(self, start=True, **kw):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = server.PvjServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, self.web, max_lifetime=5.0), **kw)
        self.port = self.httpd.server_address[1]
        self.addCleanup(self.httpd.server_close)
        if start:
            self.start()

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def connect(self, timeout=5.0):
        import socket
        c = socket.socket()
        c.settimeout(timeout)
        try:
            c.connect(("127.0.0.1", self.port))
        except OSError as e:
            c.close()
            return None, "no connection (%s)" % type(e).__name__
        return c, None

    def answer(self, c, path):
        """What came back on an open connection: the status, or a few words on what went wrong."""
        try:
            c.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % (path, self.port)).encode())
            data = b""
            while True:
                chunk = c.recv(65536)
                if not chunk:
                    break
                data += chunk
        except OSError as e:
            return "cut off (%s)" % type(e).__name__
        finally:
            c.close()
        return data.split(b" ", 2)[1].decode() if data.startswith(b"HTTP/") else "no answer"

    def get(self, path):
        c, bad = self.connect()
        return bad or self.answer(c, path)

    def test_the_queue_is_longer_than_a_room_of_browsers_and_the_cap_is_as_it_was(self):
        import inspect
        self.assertEqual(server.PvjServer.request_queue_size, 128)
        self.assertEqual(inspect.signature(server.PvjServer.__init__).parameters["max_connections"].default, 64)

    def test_six_browsers_at_once_all_get_every_file(self):
        self.serve()
        got, lock = {}, threading.Lock()

        def browser():
            for _ in range(15):
                todo = list(self.PATHS) * 3

                def worker():
                    while True:
                        with lock:
                            if not todo:
                                return
                            path = todo.pop()
                        out = self.get(path)
                        with lock:
                            got[out] = got.get(out, 0) + 1
                six = [threading.Thread(target=worker) for _ in range(6)]
                [t.start() for t in six]
                [t.join() for t in six]
        browsers = [threading.Thread(target=browser) for _ in range(6)]
        [t.start() for t in browsers]
        [t.join() for t in browsers]
        self.assertEqual(got, {"200": 6 * 15 * 9})

    def test_forty_connections_wait_their_turn_while_the_server_is_busy(self):
        # The server is not taking connections yet (as when its loop is busy with the one before): forty arrive.
        # With a queue of five Linux leaves the seventh to ask again after a second, which the half second here
        # does not wait for. Then the server starts, and each of them is answered.
        self.serve(start=False)
        conns = [None] * 40

        def arrive(i):
            conns[i] = self.connect(timeout=0.5)
        ts = [threading.Thread(target=arrive, args=(i,)) for i in range(40)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.addCleanup(lambda: [c.close() for c, _ in conns if c])
        self.assertEqual([bad for _, bad in conns if bad], [])
        self.start()
        got = {}
        for c, _ in conns:
            c.settimeout(5.0)
            out = self.answer(c, "/api/hello")
            got[out] = got.get(out, 0) + 1
        self.assertEqual(got, {"200": 40})

    def test_a_longer_queue_lets_nobody_past_the_cap(self):
        # Three connections that say nothing hold the three places; thirty more arrive at once. None of them is
        # served and none is left waiting: each is refused at once (a 503, or the connection is closed on it).
        self.serve(max_connections=3)
        idle = [self.connect()[0] for _ in range(3)]
        self.addCleanup(lambda: [c.close() for c in idle])
        time.sleep(0.2)
        got, lock = {}, threading.Lock()

        def one():
            t = time.monotonic()
            out = self.get("/api/hello")
            with lock:
                got[out] = got.get(out, 0) + 1
                got["slow"] = got.get("slow", 0) + (time.monotonic() - t > 2.0)
        ts = [threading.Thread(target=one) for _ in range(30)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(got.get("200", 0), 0, got)
        self.assertEqual(got["slow"], 0, got)
        self.assertEqual(sum(n for out, n in got.items() if out == "503" or out.startswith(("cut off", "no answer"))), 30, got)
