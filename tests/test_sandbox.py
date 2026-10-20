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

    busy = False

    def loading(self):
        return self.busy

    def start(self, name):
        if not serve.NAME.fullmatch(name or "") or not os.path.isfile(os.path.join(self.folder, name + ".json")):
            raise ValueError("no such scenario")
        if self.busy:
            raise serve.Busy("a scenario is loading")
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

    def test_another_site_cannot_load_a_scenario(self):
        # the review of #155: an <img src=".../open?scenario=network-error"> on any page restarted the box
        st, _h, _b = self.ask("GET", "/open?scenario=network-error&role=owner", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)
        st, _h, _b = self.post({"scenario": "idle"}, **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)
        self.assertEqual(self.boxes.asked, [])
        for site in ("none", "same-origin", None):          # typed in the address bar, the switcher's own page, a script
            st, _h, _b = self.ask("GET", "/open?scenario=playing&role=owner", headers={"Sec-Fetch-Site": site} if site else None)
            self.assertEqual(st, 303, site)

    def test_a_second_load_while_one_loads_is_refused(self):
        # the review of #155: two loads at once both answered 200, one of them with the other's scenario
        self.boxes.busy = True
        st, _h, body = self.post({"scenario": "vibes"})
        self.assertEqual(st, 409)
        st, _h, _b = self.ask("GET", "/open?scenario=idle&role=owner")
        self.assertEqual(st, 409)
        self.assertEqual(self.boxes.asked, [])

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
        # (the sandbox's own sockets; what pvj opens inside a box is held by box.loopback_only, and
        # BoxKeepsToThisMachineTest reads the box's sockets)
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


def own_tmp(test):
    """A temporary folder of the test's own, given to a sandbox as its TMPDIR: what is left in it is that sandbox's."""
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    return d


def holders(folder):
    """The command lines of the processes that name `folder` (a box, its mpv). Linux only."""
    out = []
    want = folder.encode()
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % p, "rb") as f:
                line = f.read()
        except OSError:
            continue
        if want in line:
            out.append(line.replace(b"\0", b" ").decode(errors="replace")[:160])
    return out


@unittest.skipUnless(os.path.isdir("/proc"), "looks for processes in /proc")
class WholeSandboxTest(unittest.TestCase):
    """serve.py itself, stopped every way a person or a system stops it: nothing of its box is left, mpv included.
    The review of #155 found mpv left by every Ctrl+C (the box got the signal too, and the second one cut its own
    cleanup short), and by a kill of serve.py."""

    def run_sandbox(self, scenario="playing"):
        import subprocess
        import time
        tmp = own_tmp(self)
        env = dict(os.environ, TMPDIR=tmp)
        proc = subprocess.Popen([sys.executable, os.path.join(SANDBOX, "serve.py"), "--port", str(free_ports(5)), "--scenario", scenario],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.addCleanup(proc.stdout.close)
        deadline, out = time.time() + 120, ""
        while "Stop with" not in out and time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            out += line
        self.assertIn("Stop with", out)
        # the running box's folder (beside it the box's own pvj-t-* of tests/tmpguard.py, which it removes at exit)
        folders = [os.path.join(tmp, d) for d in os.listdir(tmp) if d.startswith("nxlx-sandbox-") and os.path.isdir(os.path.join(tmp, d))]
        self.assertEqual(len(folders), 1)
        if shutil.which("mpv"):
            self.assertTrue(any("mpv" in c for c in holders(folders[0])), "the box's mpv runs")
        return proc, tmp, folders[0]

    def gone(self, tmp, folder, also_log=True):
        import time
        for _ in range(100):
            if not holders(folder) and not os.path.exists(folder):
                break
            time.sleep(0.1)
        self.assertEqual(holders(folder), [])                 # no box, no mpv
        self.assertFalse(os.path.exists(folder))
        for _ in range(50):                                   # the box's pvj-t-* goes with its last breath
            if not os.listdir(tmp) or not also_log:
                break
            time.sleep(0.1)
        if also_log:
            self.assertEqual(os.listdir(tmp), [])             # and no log (nothing failed), nothing else
        else:
            self.assertEqual([d for d in os.listdir(tmp) if not d.endswith(".log")], [])

    def test_ctrl_c_in_the_terminal(self):
        import signal
        proc, tmp, folder = self.run_sandbox()
        os.killpg(proc.pid, signal.SIGINT)                    # what a terminal sends: to the whole group
        proc.wait(60)
        self.gone(tmp, folder)

    def test_sigterm(self):
        import signal
        proc, tmp, folder = self.run_sandbox()
        proc.send_signal(signal.SIGTERM)
        proc.wait(60)
        self.gone(tmp, folder)

    def test_sigterm_to_the_group(self):
        import signal
        proc, tmp, folder = self.run_sandbox()
        os.killpg(proc.pid, signal.SIGTERM)                   # `timeout` does this
        proc.wait(60)
        self.gone(tmp, folder)

    def test_a_killed_switcher_takes_its_box_with_it(self):
        import signal
        proc, tmp, folder = self.run_sandbox()
        os.kill(proc.pid, signal.SIGKILL)                     # nothing of serve.py runs: the box sees its stdin close
        proc.wait(60)
        self.gone(tmp, folder, also_log=False)                # (the log is serve.py's, which had no chance to remove it)

    def test_a_taken_port_makes_nothing(self):
        import subprocess
        tmp = own_tmp(self)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)
        r = subprocess.run([sys.executable, os.path.join(SANDBOX, "serve.py"), "--port", str(s.getsockname()[1])],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, TMPDIR=tmp))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(tmp), [])                 # no log, no box folder


class GuardTest(unittest.TestCase):
    """box.loopback_only(), in a process of its own (it changes the socket class of the process it runs in)."""

    def test_sockets_keep_to_this_machine(self):
        import subprocess
        code = (
            "import sys, socket; sys.path.insert(0, %r); import box; box.loopback_only()\n"
            "out = []\n"
            "s = socket.socket(); s.bind(('', 0)); out.append(s.getsockname()[0]); s.close()\n"
            "s = socket.socket(); s.bind(('0.0.0.0', 0)); out.append(s.getsockname()[0]); s.close()\n"
            "u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "for where in [('192.168.1.255', 9), ('255.255.255.255', 9), ('8.8.8.8', 53), ('example.com', 80)]:\n"
            "    try: u.sendto(b'x', where); out.append('sent')\n"
            "    except OSError: out.append('refused')\n"
            "u.sendto(b'x', ('127.0.0.1', 9)); out.append('loopback sent')\n"
            "c = socket.socket()\n"
            "try: c.connect(('10.0.0.1', 80)); out.append('connected')\n"
            "except OSError: out.append('refused')\n"
            "out.append(str(c.connect_ex(('192.168.0.169', 80)) != 0))\n"
            "try: socket.socket().bind(('192.168.1.9', 0)); out.append('bound')\n"
            "except OSError: out.append('refused')\n"
            "print(' '.join(out))\n") % SANDBOX
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.split(), ["127.0.0.1", "127.0.0.1", "refused", "refused", "refused", "refused",
                                            "loopback", "sent", "refused", "True", "refused"], r.stderr)


def inet_sockets(pid):
    """[(protocol, address)] of every TCP and UDP socket the process `pid` holds (Linux /proc)."""
    inodes = set()
    for fd in os.listdir("/proc/%d/fd" % pid):
        try:
            link = os.readlink("/proc/%d/fd/%s" % (pid, fd))
        except OSError:
            continue
        if link.startswith("socket:["):
            inodes.add(link[8:-1])
    out = []
    for proto in ("tcp", "udp", "tcp6", "udp6"):
        try:
            with open("/proc/%d/net/%s" % (pid, proto)) as f:
                lines = f.read().split("\n")[1:]
        except OSError:
            continue
        for line in lines:
            cols = line.split()
            if len(cols) > 9 and cols[9] in inodes:
                hexaddr = cols[1].split(":")[0]
                raw = bytes.fromhex(hexaddr)
                if len(raw) == 4:
                    addr = socket.inet_ntop(socket.AF_INET, raw[::-1])
                else:
                    addr = socket.inet_ntop(socket.AF_INET6, b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
                out.append((proto, addr))
    return out


@unittest.skipUnless(os.path.isdir("/proc"), "reads sockets from /proc")
class BoxKeepsToThisMachineTest(unittest.TestCase):
    """The review of #155: OSC, DMX and Boxes in step, switched on from the Owner's port, listened on 0.0.0.0."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = cls.tmp
        tempfile.tempdir = None
        os.environ["PVJ_ALLOWED_HOSTS"] = "evil.example"       # from the developer's shell: must not reach the box
        cls.boxes = serve.Boxes(free_ports(4))
        cls.st = cls.boxes.start("idle")

    @classmethod
    def tearDownClass(cls):
        cls.boxes.close()
        del os.environ["PVJ_ALLOWED_HOSTS"]
        if cls.old is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = cls.old
        tempfile.tempdir = None
        shutil.rmtree(cls.tmp, True)

    def post(self, path, body, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.boxes.ports()["owner"], timeout=30)
        h = {"Content-Type": "application/json", "X-PVJ-Request": "1"}
        if host:
            h["Host"] = host
        c.request("POST", path, json.dumps(body).encode(), h)
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def test_listeners_switched_on_listen_on_loopback(self):
        self.assertTrue(self.st["ready"], self.st["error"])
        for path, body in (("/api/modules/control-dmx", {"enabled": True}), ("/api/dmx", {"enabled": True}),
                           ("/api/osc", {"enabled": True})):
            st, out = self.post(path, body)
            self.assertEqual(st, 200, (path, out[:200]))
        import time
        time.sleep(1)
        socks = inet_sockets(self.boxes.proc.pid)
        self.assertTrue([s for s in socks if s[0].startswith("udp")], socks)        # OSC and DMX are listening
        wide = [s for s in socks if not s[1].startswith("127.") and s[1] != "::1"]
        self.assertEqual(wide, [])

    def test_the_shells_pvj_variables_do_not_reach_the_box(self):
        # with PVJ_ALLOWED_HOSTS from the shell, the panel would take this Host as its own
        st, _out = self.post("/api/osc", {"enabled": True}, host="evil.example")
        self.assertNotEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
