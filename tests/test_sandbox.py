# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The design sandbox (tools/sandbox): its scenarios, its switcher's checks, and every scenario on a real box.

ScenarioFileTest and SwitcherTest need nothing but Python. EveryScenarioTest starts the real panel once per
scenario (box.py, as serve.py does) and asks each role's port who it is; it uses mpv when this machine has it.
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(ROOT, "tools", "sandbox")
sys.path.insert(0, SANDBOX)
import box  # noqa: E402
import serve  # noqa: E402


def write(folder, name, data):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def free_ports(n):
    """A first port with the n - 1 after it free too, on 127.0.0.1."""
    for _ in range(50):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        first = s.getsockname()[1]
        s.close()
        if first + n > 65535:
            continue
        ok = True
        for p in range(first, first + n):
            t = socket.socket()
            try:
                t.bind(("127.0.0.1", p))
            except OSError:
                ok = False
            finally:
                t.close()
        if ok:
            return first
    raise RuntimeError("no free ports")


class ScenarioFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def check(self, data):
        write(self.dir, "x.json", data)
        return box.load_scenario(os.path.join(self.dir, "x.json"))

    def test_every_shipped_scenario_loads(self):
        found = serve.scenarios()
        self.assertGreaterEqual(len(found), 8)
        for name, sc in found.items():
            self.assertNotIn("broken", sc, name)
            self.assertTrue(sc["title"], name)
        for want in ("idle", "playing", "projector-warming", "projector-error", "masks-combining", "mapping-surfaces",
                     "network-error", "empty", "guests-locked"):
            self.assertIn(want, found)

    def test_base_is_not_listed_but_extended(self):
        self.assertNotIn("_base", serve.scenarios())
        sc = box.load_scenario(os.path.join(serve.SCENARIOS, "playing.json"))
        self.assertEqual(sc["steps"][-1], {"path": "/api/play", "body": {"pad": [0, 0]}})      # the base's first, its own after
        self.assertIn("intro.mkv", sc["clips"])

    def test_refused(self):
        bad = [
            [],                                                        # not an object
            {"about": "no title"},
            {"title": "t", "colour": "red"},                           # an unknown key
            {"title": "t", "clips": ["../etc/passwd"]},                # a name that is a path
            {"title": "t", "clips": [".hidden"]},
            {"title": "t", "pictures": ["a" * 81]},
            {"title": "t", "modules": {"projector": "yes"}},
            {"title": "t", "projectors": [{"name": "x", "state": "exploding"}]},
            {"title": "t", "projectors": [{"name": "x", "errors": "99"}]},
            {"title": "t", "steps": [{"path": "/etc/passwd"}]},         # only the box's own routes
            {"title": "t", "steps": [{"path": "http://example.com/api/x"}]},
            {"title": "t", "steps": [{"path": "/api/play", "as": "root"}]},
            {"title": "t", "steps": [{"path": "/api/play", "method": "DELETE"}]},
            {"title": "t", "steps": [{"path": "/api/play", "body": [1]}]},
            {"title": "t", "guests_locked": "yes"},
            {"title": "t", "offline_after": -1},
            {"title": "t", "slow_masks": True},
            {"title": "t", "steps": [{"path": "/api/play"}] * 201},
            {"title": "t", "extends": "../x"},
            {"title": "t", "extends": "missing"},
        ]
        for data in bad:
            with self.subTest(data=str(data)[:60]):
                with self.assertRaises(box.ScenarioError):
                    self.check(data)

    def test_broken_json_is_a_scenario_error(self):
        with self.assertRaises(box.ScenarioError):
            self.check("{not json")

    def test_extends_is_bounded(self):
        write(self.dir, "a.json", {"extends": "b"})
        write(self.dir, "b.json", {"extends": "a", "title": "loop"})
        with self.assertRaises(box.ScenarioError):
            box.load_scenario(os.path.join(self.dir, "a.json"))

    def test_broken_file_is_listed_not_chosen(self):
        write(self.dir, "good.json", {"title": "Good"})
        write(self.dir, "bad.json", {"title": "Bad", "nope": 1})
        write(self.dir, "Not-a-name.json", {"title": "x"})
        found = serve.scenarios(self.dir)
        self.assertEqual(sorted(found), ["bad", "good"])
        self.assertIn("unknown keys", found["bad"]["broken"])
        boxes = serve.Boxes(free_ports(4), folder=self.dir)
        self.addCleanup(os.unlink, boxes.log.name)
        self.addCleanup(boxes.log.close)
        with self.assertRaises(box.ScenarioError):
            boxes.start("bad")
        with self.assertRaises(ValueError):
            boxes.start("../good")
        self.assertIsNone(boxes.proc)          # nothing was started for either


class FakeBoxes:
    """Boxes with no process: what the switcher asked for is written down."""

    def __init__(self, first):
        self.first_port, self.folder, self.asked = first, serve.SCENARIOS, []
        self.state = {"scenario": "idle", "ready": True, "player": True, "problems": [], "error": ""}

    def ports(self):
        return {r: self.first_port + i for i, r in enumerate(box.ROLES)}

    def start(self, name):
        if not serve.NAME.fullmatch(name or "") or not os.path.isfile(os.path.join(self.folder, name + ".json")):
            raise ValueError("no such scenario")
        self.asked.append(name)
        self.state = dict(self.state, scenario=name)
        return dict(self.state)


class SwitcherTest(unittest.TestCase):
    def setUp(self):
        self.boxes = FakeBoxes(9000)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = serve.make_handler(self.boxes, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def ask(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def post(self, body, **headers):
        h = {"X-Sandbox-Request": "1", "Content-Type": "application/json"}
        h.update(headers)
        return self.ask("POST", "/api/sandbox/scenario", body if isinstance(body, bytes) else json.dumps(body).encode(), h)

    def test_page_and_its_policy(self):
        for path in ("/", "/sandbox.js", "/sandbox.css"):
            st, h, body = self.ask("GET", path)
            self.assertEqual(st, 200, path)
            self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
            self.assertIn("script-src 'self'", h["Content-Security-Policy"])
            self.assertEqual(h["X-Frame-Options"], "DENY")
        st, _h, body = self.ask("GET", "/")
        self.assertNotIn(b"<script>", body)               # no script in the page itself (the policy would refuse it)
        st, _h, _b = self.ask("GET", "/../serve.py")
        self.assertEqual(st, 404)
        st, _h, _b = self.ask("GET", "/box.py")
        self.assertEqual(st, 404)

    def test_host_is_this_machine_only(self):
        for host in ("evil.example:%d" % self.port, "127.0.0.1:1", "192.168.1.9:%d" % self.port, "localhost"):
            st, _h, _b = self.ask("GET", "/api/sandbox", host=host)
            self.assertEqual(st, 421, host)
        st, _h, _b = self.ask("GET", "/api/sandbox", host="localhost:%d" % self.port)
        self.assertEqual(st, 200)
        st, _h, _b = self.post({"scenario": "idle"}, Host="evil.example:%d" % self.port)
        self.assertEqual(st, 421)
        self.assertEqual(self.boxes.asked, [])

    def test_post_needs_the_pages_own_header_and_origin(self):
        st, _h, _b = self.ask("POST", "/api/sandbox/scenario", json.dumps({"scenario": "idle"}).encode(), {"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        st, _h, _b = self.post({"scenario": "idle"}, Origin="http://evil.example")
        self.assertEqual(st, 403)
        st, _h, _b = self.post({"scenario": "idle"}, Origin="http://127.0.0.1:%d" % (self.port + 1))
        self.assertEqual(st, 403)
        self.assertEqual(self.boxes.asked, [])
        st, _h, body = self.post({"scenario": "idle"}, Origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(st, 200)
        self.assertEqual(self.boxes.asked, ["idle"])
        self.assertIn("scenarios", json.loads(body))

    def test_post_body(self):
        for body in (b"", b"x" * (serve.MAX_BODY + 1), b"{not json", json.dumps([1]).encode(), json.dumps({"scenario": 5}).encode()):
            st, _h, _b = self.post(body)
            self.assertIn(st, (400, 404), body[:20])
        for name in ("../idle", "_base", "IDLE", "no-such", ""):
            st, _h, _b = self.post({"scenario": name})
            self.assertEqual(st, 404, name)
        self.assertEqual(self.boxes.asked, [])
        st, _h, _b = self.ask("POST", "/elsewhere", b"{}", {"X-Sandbox-Request": "1"})
        self.assertEqual(st, 404)

    def test_open_loads_then_sends_to_the_roles_port(self):
        st, h, _b = self.ask("GET", "/open?scenario=playing&role=guest")
        self.assertEqual(st, 303)
        self.assertEqual(h["Location"], "http://127.0.0.1:9002/")
        self.assertEqual(self.boxes.asked, ["playing"])
        st, h, _b = self.ask("GET", "/open?scenario=playing&role=owner")      # already running: not started again
        self.assertEqual((st, h["Location"]), (303, "http://127.0.0.1:9000/"))
        self.assertEqual(self.boxes.asked, ["playing"])
        st, _h, _b = self.ask("GET", "/open?scenario=playing&role=root")
        self.assertEqual(st, 400)
        st, _h, _b = self.ask("GET", "/open?scenario=../x&role=owner")
        self.assertEqual(st, 404)

    def test_summary_lists_scenarios_and_ports(self):
        st, _h, body = self.ask("GET", "/api/sandbox")
        d = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual(d["ports"], {"owner": 9000, "operator": 9001, "guest": 9002, "nobody": 9003})
        self.assertIn("playing", [s["name"] for s in d["scenarios"]])

    def test_listens_on_loopback_only(self):
        src = ""
        for fn in ("serve.py", "box.py"):
            with open(os.path.join(SANDBOX, fn), encoding="utf-8") as f:
                src += f.read()
        import re
        self.assertNotIn("0.0.0.0", src)
        self.assertNotIn('("", ', src)
        binds = re.findall(r'(?:Server|bind)\(\(([^,]+),', src)       # every address a socket of the sandbox is given
        self.assertTrue(binds)
        self.assertEqual(set(binds), {'"127.0.0.1"'})


class EveryScenarioTest(unittest.TestCase):
    """Each scenario on the real panel: ready, no step refused, and each port is its role."""

    @classmethod
    def setUpClass(cls):
        cls.boxes = serve.Boxes(free_ports(4))

    @classmethod
    def tearDownClass(cls):
        cls.boxes.stop()
        cls.boxes.log.close()
        try:
            os.unlink(cls.boxes.log.name)
        except OSError:
            pass

    def status(self, port):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request("GET", "/api/status")
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        c.close()
        return out

    def test_each_scenario(self):
        want = {"owner": "owner", "operator": "operator", "guest": "guest"}
        for name in serve.scenarios():
            with self.subTest(scenario=name):
                st = self.boxes.start(name)
                self.assertTrue(st["ready"], st["error"])
                self.assertEqual(st["problems"], [])
                ports = self.boxes.ports()
                for role, reach in want.items():
                    code, d = self.status(ports[role])
                    self.assertEqual(code, 200, role)
                    self.assertIn(d.get("reach"), (reach, "watch") if name == "guests-locked" and role == "guest" else (reach,))
                code, _d = self.status(ports["nobody"])
                self.assertEqual(code, 401)

    def test_a_stopped_box_leaves_nothing(self):
        st = self.boxes.start("playing")
        self.assertTrue(st["ready"], st["error"])
        folder, pid = self.boxes.folder_in_use, self.boxes.proc.pid
        self.assertTrue(os.path.isdir(folder))
        self.boxes.stop()
        self.assertFalse(os.path.isdir(folder))                 # its media, settings and the player's socket
        if os.path.isdir("/proc"):                               # (Linux) no process of that box is left: not it, not its mpv
            import time

            def left():
                out = []
                for p in os.listdir("/proc"):
                    if not p.isdigit():
                        continue
                    try:
                        with open("/proc/%s/cmdline" % p, "rb") as f:
                            line = f.read()
                        if folder.encode() in line or int(p) == pid:
                            out.append(line.replace(b"\0", b" ").decode(errors="replace")[:200])
                    except OSError:
                        pass
                return out
            for _ in range(50):
                if not left():
                    break
                time.sleep(0.1)
            self.assertEqual(left(), [])


class WholeSandboxTest(unittest.TestCase):
    """serve.py itself, stopped as a session manager or `kill` stops it (SIGTERM): its box goes with it."""

    def test_sigterm_stops_the_box(self):
        import signal
        import subprocess
        import time
        first = free_ports(5)
        tmp = tempfile.gettempdir()
        before = set(os.listdir(tmp))
        proc = subprocess.Popen([sys.executable, os.path.join(SANDBOX, "serve.py"), "--port", str(first), "--scenario", "idle"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            deadline = time.time() + 120
            out = ""
            while "Stop with" not in out and time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                out += line
            self.assertIn("Stop with", out)
            new = [d for d in set(os.listdir(tmp)) - before if d.startswith("nxlx-sandbox-") and os.path.isdir(os.path.join(tmp, d))]
            self.assertEqual(len(new), 1)                    # the running box's folder
            proc.send_signal(signal.SIGTERM)
            proc.wait(30)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
        left = []
        for _ in range(50):
            left = [d for d in set(os.listdir(tmp)) - before if d.startswith("nxlx-sandbox-")]
            if not left:
                break
            time.sleep(0.1)
        self.assertEqual(left, [])                           # no folder, and no log (nothing failed)


if __name__ == "__main__":
    unittest.main()
