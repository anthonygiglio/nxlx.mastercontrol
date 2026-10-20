# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""One box of the design sandbox: the real panel, with nothing real behind it, in one scenario.

Started by tools/sandbox/serve.py (one at a time; a new scenario is a new process, so nothing of the one before is
left over). It builds the panel as pvj/server.py does, in a temporary folder of its own, with what the browser test's
harness puts behind it (tests/ui/harness.py): a headless mpv (no picture, no sound) when this machine has mpv, else no
player at all; fake PJLink projectors on loopback; a fake NetworkManager; the report of a 1440p monitor. Then it pairs
one device per role and serves the panel on four ports of 127.0.0.1, one per way of looking at it:

    owner      the Owner's device          operator   an Operator's device
    guest      a Guest's device            nobody     no device: the pairing screen

Each port hands the panel that role's token itself (the server's own `_token`), so the browser needs no cookie and
three roles can be open side by side in one browser. Everything else is the box's own code: the routes, the roles
(pvj/policy.py), the page and its policy. Then the scenario (a JSON file, see README.md) is put in place through
the box's own routes, as the Owner, and one JSON line says the box is ready.

Run by hand only to look at one scenario: python3 tools/sandbox/box.py <scenario.json> <first port>.
"""
import ipaddress
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

ROLES = ("owner", "operator", "guest", "nobody")     # in the order of their ports
POWER = {"on": "1", "off": "0", "cooling": "2", "warming": "3"}
LIMITS = {"steps": 200, "clips": 40, "pictures": 20, "projectors": 8}
READY_OUT = sys.stdout       # where the one ready line goes (main() keeps the real stdout for it)


class ScenarioError(Exception):
    pass


def read_scenario(path, depth=0):
    """A scenario file as JSON, with what it extends put under it: another file of the same folder, named without
    its .json ("_base"), whose keys this one replaces, except steps, which run the base's first."""
    try:
        with open(path, encoding="utf-8") as f:
            sc = json.load(f)
    except (OSError, ValueError) as e:
        raise ScenarioError("%s cannot be read: %s" % (os.path.basename(path), e))
    if not isinstance(sc, dict):
        raise ScenarioError("a scenario is a JSON object")
    parent = sc.pop("extends", None)
    if parent is None:
        return sc
    if depth >= 3 or not isinstance(parent, str) or not re.fullmatch(r"_?[a-z0-9][a-z0-9-]{0,39}", parent):
        raise ScenarioError("extends names another scenario of this folder, at most three deep")
    base = read_scenario(os.path.join(os.path.dirname(path), parent + ".json"), depth + 1)
    steps = base.get("steps", []) + sc.get("steps", [])
    base.update(sc)
    if steps:
        base["steps"] = steps
    return base


def load_scenario(path):
    """A scenario file, read and checked: a JSON object of the keys README.md names, each of its type. Raises
    ScenarioError with what is wrong; never runs anything."""
    sc = read_scenario(path)
    known = {"title", "about", "clips", "pictures", "modules", "projectors", "steps", "guests_locked",
             "offline_after", "slow_masks"}
    extra = sorted(set(sc) - known)
    if extra:
        raise ScenarioError("unknown keys: %s" % ", ".join(extra))
    if not isinstance(sc.get("title"), str) or not sc["title"].strip():
        raise ScenarioError("a scenario needs a title")
    for key in ("clips", "pictures"):
        names = sc.get(key, [])
        if not isinstance(names, list) or len(names) > LIMITS[key] or not all(_plain_name(n) for n in names):
            raise ScenarioError("%s is a list of at most %d plain file names" % (key, LIMITS[key]))
    if not isinstance(sc.get("modules", {}), dict) or not all(isinstance(v, bool) for v in sc.get("modules", {}).values()):
        raise ScenarioError("modules is an object of module ids and true or false")
    ps = sc.get("projectors", [])
    if not isinstance(ps, list) or len(ps) > LIMITS["projectors"]:
        raise ScenarioError("projectors is a list of at most %d" % LIMITS["projectors"])
    for p in ps:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or p.get("state", "off") not in tuple(POWER) + ("silent",):
            raise ScenarioError("a projector is {name, state}; state is on, off, warming, cooling or silent")
        if "errors" in p and not (isinstance(p["errors"], str) and len(p["errors"]) == 6 and set(p["errors"]) <= set("012")):
            raise ScenarioError("a projector's errors are six digits of 0, 1 or 2 (PJLink ERST)")
    steps = sc.get("steps", [])
    if not isinstance(steps, list) or len(steps) > LIMITS["steps"]:
        raise ScenarioError("steps is a list of at most %d" % LIMITS["steps"])
    for s in steps:
        if not isinstance(s, dict) or s.get("method", "POST") not in ("GET", "POST") or not isinstance(s.get("path"), str) \
                or not s["path"].startswith("/api/") or s.get("as", "owner") not in ROLES[:3] or not isinstance(s.get("body", {}), dict):
            raise ScenarioError("a step is {as: owner|operator|guest, method: GET|POST, path: /api/..., body: {...}}")
    if not isinstance(sc.get("guests_locked", False), bool):
        raise ScenarioError("guests_locked is true or false")
    for key, top in (("offline_after", 600), ("slow_masks", 60)):
        v = sc.get(key)
        if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= top):
            raise ScenarioError("%s is a number of seconds from 0 to %d" % (key, top))
    return sc


def _plain_name(n):
    return isinstance(n, str) and 0 < len(n) <= 80 and "/" not in n and "\\" not in n and not n.startswith(".")


def rgba_png(w, h, rgba):
    """A PNG of one colour with an alpha channel (colour type 6), for a mask or a logo."""
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def loopback_only():
    """Hold every socket this process makes to this machine (the review of #155): a bind to every address ("",
    0.0.0.0, ::) is made on loopback instead, and a bind, connect or send to any other address is refused. So what the
    panel opens when the Owner switches OSC, DMX or Boxes in step on listens on 127.0.0.1, a leader's broadcast goes
    nowhere, and nothing reaches the network a node would be on. Unix sockets (the player's) are left as they are."""
    import errno
    import socket as so

    def host_of(address):
        return address[0] if isinstance(address, tuple) and address else None

    def loop(host):
        if host in ("localhost",):
            return True
        try:
            return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
        except ValueError:
            return False

    def refuse(what, host):
        raise OSError(errno.EADDRNOTAVAIL, "the design sandbox keeps to this machine: no %s to %s" % (what, host))

    real_bind, real_connect, real_connect_ex, real_sendto = so.socket.bind, so.socket.connect, so.socket.connect_ex, so.socket.sendto

    def bind(self, address):
        host = host_of(address)
        if host is not None and host in ("", "0.0.0.0", "::"):
            address = ("::1" if self.family == so.AF_INET6 else "127.0.0.1",) + tuple(address[1:])
        elif host is not None and not loop(host):
            refuse("bind", host)
        return real_bind(self, address)

    def connect(self, address):
        host = host_of(address)
        if host is not None and not loop(host):
            refuse("connection", host)
        return real_connect(self, address)

    def connect_ex(self, address):
        host = host_of(address)
        if host is not None and not loop(host):
            return errno.EADDRNOTAVAIL
        return real_connect_ex(self, address)

    def sendto(self, data, *rest):
        host = host_of(rest[-1]) if rest else None
        if host is not None and not loop(host):
            refuse("message", host)
        return real_sendto(self, data, *rest)
    so.socket.bind, so.socket.connect, so.socket.connect_ex, so.socket.sendto = bind, connect, connect_ex, sendto


def private_env(tmp):
    """The box's environment: every PVJ_ variable of the shell it was started from taken out (PVJ_ALLOWED_HOSTS would
    widen the Host check), and each folder or file the panel reads from one put inside the box's own folder, so
    nothing of this computer's USB sticks, update results or helper sockets is read (the review of #155)."""
    for k in [k for k in os.environ if k.startswith("PVJ_")]:
        del os.environ[k]
    folders = {"PVJ_RUNTIME_DIR": "runtime", "PVJ_USB_BASE": "usb", "PVJ_SYSD_DIR": "sysd", "PVJ_SUPPORTD_DIR": "supportd",
               "PVJ_SOUND_DIR": "sound", "PVJ_NETD_DIR": "netd", "PVJ_NDI_CONFIG_DIR": "ndi-config", "PVJ_NDI_PIPE_DIR": "ndi-pipe"}
    for k, rel in folders.items():                       # folders the panel looks in: there, and empty
        path = os.path.join(tmp, rel)
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.environ[k] = path
    names = {"PVJ_USB_DIR": "usb-newest", "PVJ_USB_LINK": "usb-link", "PVJ_UPDATE_RESULT": "update-result.json",
             "PVJ_UPDATE_LOCK": "update.lock"}
    for k, rel in names.items():                         # a link, a file: a name inside the folder, not made
        os.environ[k] = os.path.join(tmp, rel)


def main(scenario_path, first_port):
    sc = load_scenario(scenario_path)
    import signal
    stop = threading.Event()
    # serve.py stops a box with SIGTERM, or by closing its stdin (also when serve.py itself dies, however it dies).
    # Either sets `stop`; the cleanup then runs once, in the main thread, with nothing to interrupt it. Ctrl+C in the
    # terminal reaches serve.py, which stops the box: the box itself does not act on SIGINT (the review of #155).
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def parent_gone():
        try:
            sys.stdin.read()
        except (OSError, ValueError):
            pass
        stop.set()
    threading.Thread(target=parent_gone, daemon=True).start()
    # serve.py reads this box's stdout for two lines: where the box lives, and that it is ready. The panel prints some
    # log lines to stdout ("guest controls locked by ...") and a program it runs inherits the stdout of the process:
    # so the real stdout is kept for those two lines alone and every other writer gets stderr (serve.py's log).
    global READY_OUT
    READY_OUT = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    tmp = tempfile.mkdtemp(prefix="nxlx-sandbox-")
    print(json.dumps({"folder": tmp}), file=READY_OUT, flush=True)
    try:
        private_env(tmp)
        loopback_only()
        run(sc, tmp, first_port, stop)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(sc, tmp, first_port, stop):
    media = os.path.join(tmp, "video")
    rundir = os.path.join(tmp, "run")
    os.makedirs(media)
    os.makedirs(rundir, mode=0o700)
    os.environ.update(PVJ_STATE_DIR=tmp, PVJ_MEDIA_DIR=media, PVJ_DEV_SPAWN="1")

    from pvj import server, capture
    from pvj.player import Player
    from tests.test_server import make_test_video

    has_mpv = bool(shutil.which("mpv"))
    clip = make_test_video(tmp) if has_mpv and sc.get("clips") else None
    for name in sc.get("clips", []):
        if clip:
            shutil.copyfile(clip, os.path.join(media, name))
        else:
            with open(os.path.join(media, name), "wb") as f:      # a name in the Library; it will not play
                f.write(b"\x1aE\xdf\xa3 not a real clip")
    for i, name in enumerate(sc.get("pictures", [])):
        colours = [(0, 0, 0, 255), (255, 214, 10, 200), (0, 224, 255, 160), (255, 79, 163, 200)]
        with open(os.path.join(media, name), "wb") as f:
            f.write(rgba_png(320, 180, colours[i % len(colours)]))

    mpv = "mpv" if has_mpv else os.path.join(tmp, "no-such-mpv")
    player = Player(mpv_bin=mpv, extra_args=["--vo=null", "--ao=null"], rundir=rundir)
    servers, fakes = {}, []
    try:                                       # from here on, whatever happens, mpv is stopped and the ports closed
        api, auth, _ = server.build(os.environ, player=player)
        api.capture = capture.Capture(rundir, mpv, lister=lambda: [])     # no webcam or capture stick of this computer
        serve_box(sc, tmp, first_port, stop, api, auth, rundir, has_mpv, servers, fakes)
    finally:
        for httpd in servers.values():
            httpd.shutdown()
            httpd.server_close()
        for f in fakes:
            f.close()
        player.stop()


def serve_box(sc, tmp, first_port, stop, api, auth, rundir, has_mpv, servers, fakes):
    from pvj import server, screenmode, projector, overlay, midi as midi_mod

    # What the browser test's harness puts behind the panel (tests/ui/harness.py), without its delivery fake; MIDI
    # sees no controller at all (a real one plugged into this computer is not read).
    api.midi.stop()
    api.midi = midi_mod.MidiHub(api, api.settings, lister=lambda: [], scan_interval=3600)
    api.midi.apply()
    from tests.test_screenmode import MONITOR, connected
    report = screenmode.decide({"mode": "auto"}, screenmode.parse_help(MONITOR), "", connected(), "sandbox")
    screenmode.write_json(os.path.join(rundir, screenmode.REPORT), dict(report, pid=None, boot="sandbox", at=0))
    from tests.test_netd import FakeNm, make_sysfs
    from pvj.netd import NetService
    nm = FakeNm()
    nm.scan_text = "Leyline Staff:72:WPA2:36\nVenue Guest:40::1\n"
    keyfiles = os.path.join(tmp, "nm-connections")
    os.makedirs(keyfiles, mode=0o700)
    net = NetService(runner=nm, sysfs=make_sysfs(), keyfile_dir=keyfiles)

    class DirectNet:
        def request(self, message):
            return net.handle(message)
    api.net = DirectNet()
    api._sysfs = net.sysfs
    api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]
    projector.PRIVATE = projector.PRIVATE + [ipaddress.ip_network("127.0.0.0/8")]   # loopback, in this process only

    slow = sc.get("slow_masks") or 0
    if slow:            # "Combining..." stays on the page long enough to look at it: each build waits first
        for fn in ("convert", "combine"):
            real = getattr(overlay, fn)

            def waited(*a, _real=real, **k):
                time.sleep(slow)
                return _real(*a, **k)
            setattr(overlay, fn, waited)

    from tests.test_projector import FakeProjector
    with_fakes, silent = [], []
    for p in sc.get("projectors", []):
        if p.get("state") == "silent":
            silent.append(p)
            continue
        f = FakeProjector(slow=p.get("state") in ("warming", "cooling"), lamps=(1234,), name=p["name"])
        f.power, f.errors = POWER[p.get("state", "off")], p.get("errors", "000000")
        fakes.append(f)
        with_fakes.append((p, f))

    # One device per role, paired by the box's own code. Their tokens never leave this process.
    tokens = {"owner": auth.pair(auth.current_pin, "Owner (sandbox)", client="sandbox")[0],
              "operator": auth.invite("Operator (sandbox)", "live")[0],
              "guest": auth.invite("Guest (sandbox)", "view")[0], "nobody": None}
    offline = threading.Event()
    base = server.make_handler(api, auth)

    def handler_for(role):
        class Handler(base):
            def _token(self):          # the role's own token; a cookie or header from the browser is not read
                return tokens[role]

            def do_GET(self):
                if offline.is_set() and self.path.startswith("/api/"):
                    self.close_connection = True       # what a lost connection is to the page
                    return None
                return super().do_GET()

            def do_POST(self):
                if offline.is_set():
                    self.close_connection = True
                    return None
                return super().do_POST()

            def log_message(self, fmt, *args):
                pass
        return Handler

    for i, role in enumerate(ROLES):
        httpd = server.PvjServer(("127.0.0.1", first_port + i), handler_for(role))
        servers[role] = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    problems = setup(sc, first_port, with_fakes, silent)
    if sc.get("guests_locked"):
        problems += call(first_port, "owner", "POST", "/api/guests", {"locked": True})
    if sc.get("offline_after") is not None:
        t = threading.Timer(sc["offline_after"], offline.set)
        t.daemon = True
        t.start()
    print(json.dumps({"ready": True, "ports": {r: first_port + i for i, r in enumerate(ROLES)}, "player": has_mpv,
                      "problems": problems, "folder": tmp}), file=READY_OUT, flush=True)
    while not stop.wait(1):            # (a wait with a deadline, so the signal handler always gets its turn)
        pass


def request(first_port, role, method, path, body=None):
    """One request through the box's own route, as that role: what a person would do on the page. Returns
    (problem or None, the answer's JSON)."""
    port = first_port + ROLES.index(role)
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-PVJ-Request": "1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return None, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read().decode() or "{}").get("error", "")
        except ValueError:
            why = ""
        return "%s %s as %s: %d %s" % (method, path, role, e.code, why), {}
    except (OSError, ValueError) as e:
        return "%s %s as %s: %s" % (method, path, role, e), {}


def call(first_port, role, method, path, body=None):
    """request(), keeping only the problem: a list with one line when the box refused it (a scenario that no longer
    fits the panel says so, and the rest goes on)."""
    problem, _ = request(first_port, role, method, path, body)
    return [problem] if problem else []


def resolve(first_port, value):
    """A step names what the box gives an id to by its name: "@projector:Main wall" and "@group:Stage" become the
    ids the box made for them, read afresh from its own lists when a step uses one."""
    if isinstance(value, list):
        return [resolve(first_port, v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(first_port, v) for k, v in value.items()}
    if isinstance(value, str) and value.startswith(("@projector:", "@group:")):
        kind, _, name = value[1:].partition(":")
        _, d = request(first_port, "owner", "GET", "/api/projectors" if kind == "projector" else "/api/room")
        found = [x["id"] for x in d.get("projectors" if kind == "projector" else "groups", []) if x.get("name") == name]
        if not found:
            raise ScenarioError("no %s named %s" % (kind, name))
        return found[0]
    return value


def setup(sc, first_port, fakes, silent):
    """Put the scenario in place through the box's own routes. `fakes`: [(the scenario's projector, its fake)]."""
    problems = []
    for mod, on in sc.get("modules", {}).items():
        problems += call(first_port, "owner", "POST", "/api/modules/" + mod, {"enabled": on})
    for p, f in fakes:
        problems += call(first_port, "owner", "POST", "/api/projectors", {"add": {"name": p["name"], "host": "127.0.0.1", "port": f.port}})
    for p in silent:          # an address where nothing answers: a port that was open a moment ago and is closed now
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        problems += call(first_port, "owner", "POST", "/api/projectors", {"add": {"name": p["name"], "host": "127.0.0.1", "port": port}})
    for s in sc.get("steps", []):
        try:
            body = resolve(first_port, s.get("body"))
        except ScenarioError as e:
            problems.append("%s %s: %s" % (s.get("method", "POST"), s["path"], e))
            continue
        problems += call(first_port, s.get("as", "owner"), s.get("method", "POST"), s["path"], body)
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: box.py <scenario.json> <first port>")
    main(sys.argv[1], int(sys.argv[2]))
