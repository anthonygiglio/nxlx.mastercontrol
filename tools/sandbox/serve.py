# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The design sandbox: the real panel on this computer, with no node, in any role and any scenario.

    python3 tools/sandbox/serve.py              then open the address it prints
    python3 tools/sandbox/serve.py --scenario playing --port 8700

The switcher (this file) is a small page of its own on 127.0.0.1:<port>. The panel itself is served by a box process
(box.py) on the next four ports: Owner, Operator, Guest, and a device that is not paired (the pairing screen). The
switcher opens the panel in a window of its own at the width chosen, so nothing of the switcher is ever in the
panel's page: the panel's policy refuses to be shown in a frame of another page, and that is kept.

A scenario (scenarios/<name>.json, README.md) is put in place by starting a new box process; the one before is
stopped first, so a scenario always starts from nothing. An address can choose one, for repeatable captures:

    http://127.0.0.1:<port>/open?scenario=playing&role=guest     (loads it if it is not the one running, then goes there)

Everything listens on 127.0.0.1 only. Standard library only; the page is plain JavaScript under a strict policy.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios")
PAGE = os.path.join(HERE, "page")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/sandbox.js": ("sandbox.js", "text/javascript; charset=utf-8"),
         "/sandbox.css": ("sandbox.css", "text/css; charset=utf-8")}
START_SECONDS = 180          # a box that has not said it is ready by then is stopped
MAX_BODY = 4096


def scenarios(folder=SCENARIOS):
    """{name: scenario} for every file of the folder whose name is a plain name, each read and checked; a broken one
    is listed with its problem and cannot be chosen."""
    out = {}
    for fn in sorted(os.listdir(folder)):
        name, ext = os.path.splitext(fn)
        if ext != ".json" or not NAME.fullmatch(name):
            continue
        try:
            out[name] = box.load_scenario(os.path.join(folder, fn))
        except box.ScenarioError as e:
            out[name] = {"title": name, "broken": str(e)}
    return out


class Busy(Exception):
    """A scenario is being loaded: the next waits for the person to ask again (409), it does not queue."""


def leftovers(folder):
    """The pids of processes whose command line names `folder` (a box's mpv, or the box itself). Linux only; [] else."""
    out = []
    if not folder or not os.path.isdir("/proc"):
        return out
    want = folder.encode()
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid():
            continue
        try:
            with open("/proc/%s/cmdline" % p, "rb") as f:
                if want in f.read():
                    out.append(int(p))
        except OSError:
            pass
    return out


class Boxes:
    """The one box process: started for a scenario, stopped before the next. One change at a time; a second one
    asked meanwhile is refused (Busy)."""

    def __init__(self, first_port, folder=SCENARIOS):
        self.first_port, self.folder = first_port, folder
        self.lock = threading.Lock()
        self.proc = None
        self.folder_in_use = None            # the running box's own temporary folder, which it removes when stopped
        self.failed = False                  # a box that did not start: its log is kept at the end
        self.state = {"scenario": None, "ready": False, "player": None, "problems": [], "error": ""}
        self.log = tempfile.NamedTemporaryFile(prefix="nxlx-sandbox-", suffix=".log", delete=False)

    def ports(self):
        return {r: self.first_port + i for i, r in enumerate(box.ROLES)}

    def stop(self):
        """Stop the box: its stdin closed (it stops itself, mpv first), then SIGTERM, then SIGKILL; and whatever is
        still left of it afterwards (an mpv a killed box could not stop, its folder) is removed here."""
        p, self.proc = self.proc, None
        folder, self.folder_in_use = self.folder_in_use, None
        if p:
            for how in ("close", "terminate", "kill"):
                if p.poll() is not None:
                    break
                if how == "close":
                    try:
                        p.stdin.close()
                    except OSError:
                        pass
                else:
                    getattr(p, how)()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    pass
            if p.stdout:
                p.stdout.close()
        if folder and os.path.basename(folder).startswith("nxlx-sandbox-") and os.path.dirname(folder) == tempfile.gettempdir():
            for pid in leftovers(folder):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            shutil.rmtree(folder, ignore_errors=True)

    def close(self):
        """The end of the sandbox: the box stopped, and its log removed unless a box failed (then it is said where)."""
        self.stop()
        self.log.close()
        if self.failed:
            print("A box failed; its log is kept: %s" % self.log.name, file=sys.stderr)
            return
        try:
            os.unlink(self.log.name)
        except OSError:
            pass

    def loading(self):
        return self.lock.locked()

    def start(self, name):
        """Stop the box that runs, start one for `name`, and wait until it is ready. Returns the state."""
        if not NAME.fullmatch(name or ""):
            raise ValueError("no such scenario")
        path = os.path.join(self.folder, name + ".json")
        if not os.path.isfile(path):
            raise ValueError("no such scenario")
        box.load_scenario(path)               # raises ScenarioError before anything is stopped
        if not self.lock.acquire(blocking=False):
            raise Busy("a scenario is loading; ask again when it is ready")
        try:
            self.stop()
            self.state = {"scenario": name, "ready": False, "player": None, "problems": [], "error": ""}
            # its own session: a Ctrl+C in the terminal reaches this process only, which then stops the box in order
            proc = subprocess.Popen([sys.executable, os.path.join(HERE, "box.py"), path, str(self.first_port)],
                                    stdout=subprocess.PIPE, stderr=self.log, stdin=subprocess.PIPE, text=True,
                                    start_new_session=True)
            self.proc = proc
            timer = threading.Timer(START_SECONDS, proc.kill)
            timer.start()
            said = {}
            try:
                for _ in range(2):                    # {"folder"}, then {"ready", ...}
                    try:
                        said.update(json.loads(proc.stdout.readline() or "{}"))
                    except ValueError:
                        break
                    if said.get("ready"):
                        break
            finally:
                timer.cancel()
            self.folder_in_use = said.get("folder")
            if said.get("ready"):
                self.state.update(ready=True, player=said.get("player"), problems=said.get("problems", []))
            else:
                self.state["error"] = "the box did not start (its log: %s)" % self.log.name
                self.failed = True
                self.stop()
            return dict(self.state)
        finally:
            self.lock.release()


def make_handler(boxes, port):
    names = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class Handler(BaseHTTPRequestHandler):
        server_version = "nxlx-sandbox"
        sys_version = ""
        timeout = 10

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, ctype, extra=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _cross_site(self):
            # a browser says where a request comes from: a link or an <img> on another site is "cross-site". Absent
            # (a script, an old browser), "none" (typed in the address bar) and "same-origin" are this page's own.
            return (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"

        def _host_ok(self):
            # only this machine's own names for this port: a page that rebinds a name of its own to 127.0.0.1 is refused
            return (self.headers.get("Host") or "").lower() in names

        def _summary(self):
            lst = [{"name": n, "title": s.get("title", n), "about": s.get("about", ""), "broken": s.get("broken", "")}
                   for n, s in scenarios(boxes.folder).items()]
            return dict(boxes.state, scenarios=lst, ports=boxes.ports())

        def do_GET(self):
            if not self._host_ok():
                return self._json(421, {"error": "this sandbox answers at 127.0.0.1 or localhost only"})
            url = urlsplit(self.path)
            if url.path in FILES:
                fn, ctype = FILES[url.path]
                with open(os.path.join(PAGE, fn), "rb") as f:
                    return self._send(200, f.read(), ctype)
            if url.path == "/api/sandbox":
                return self._json(200, self._summary())
            if url.path == "/open":
                if self._cross_site():
                    return self._json(403, {"error": "another site cannot load a scenario"})
                q = parse_qs(url.query)
                name, role = (q.get("scenario") or [""])[0], (q.get("role") or ["owner"])[0]
                if role not in box.ROLES:
                    return self._json(400, {"error": "role is owner, operator, guest or nobody"})
                if name and (name != boxes.state["scenario"] or not boxes.state["ready"] or boxes.loading()):
                    try:
                        st = boxes.start(name)
                    except Busy as e:
                        return self._json(409, {"error": str(e)})
                    except (ValueError, box.ScenarioError) as e:
                        return self._json(404, {"error": str(e)})
                    if not st["ready"]:
                        return self._json(503, {"error": st["error"]})
                where = "http://127.0.0.1:%d/" % boxes.ports()[role]
                return self._send(303, b"", "text/plain", [("Location", where)])
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(421, {"error": "this sandbox answers at 127.0.0.1 or localhost only"})
            origin = (self.headers.get("Origin") or "").lower()
            if self.headers.get("X-Sandbox-Request") != "1" or (origin and urlsplit(origin).netloc not in names) or self._cross_site():
                return self._json(403, {"error": "not from the sandbox's own page"})
            if urlsplit(self.path).path != "/api/sandbox/scenario":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                return self._json(400, {"error": "a small JSON body, please"})
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                return self._json(400, {"error": "not JSON"})
            name = body.get("scenario") if isinstance(body, dict) else None
            try:
                st = boxes.start(name if isinstance(name, str) else "")
            except Busy as e:
                return self._json(409, {"error": str(e)})
            except (ValueError, box.ScenarioError) as e:
                return self._json(404, {"error": str(e)})
            return self._json(200 if st["ready"] else 503, self._summary())
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="The nxlx.mastercontrol design sandbox (127.0.0.1 only).")
    ap.add_argument("--port", type=int, default=8700, help="the switcher's port; the panel takes the next four")
    ap.add_argument("--scenario", default="idle", help="the scenario to start with (a file in tools/sandbox/scenarios)")
    a = ap.parse_args(argv)

    def stopped(*_):
        raise KeyboardInterrupt
    # Stopped by SIGTERM (a kill, a session that ends) as by Ctrl+C: the box is stopped with it. SIGINT is set here
    # too: a process started in the background by a shell script inherits it ignored, and Python keeps that.
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), None)     # the port first: if it is taken, nothing is made
    boxes = Boxes(a.port + 1)
    httpd.RequestHandlerClass = make_handler(boxes, a.port)
    try:
        st = boxes.start(a.scenario)
        p = boxes.ports()
        print("Design sandbox: http://127.0.0.1:%d/" % a.port)
        print("  scenario %s%s" % (a.scenario, "" if st["ready"] else ": " + st["error"]))
        print("  the panel: Owner %d, Operator %d, Guest %d, not paired %d (all on 127.0.0.1)" % (p["owner"], p["operator"], p["guest"], p["nobody"]))
        if st.get("player") is False:
            print("  no mpv on this machine: the panel says the player is not running, and nothing plays")
        for line in st.get("problems", []):
            print("  scenario step refused: " + line)
        print("Stop with Ctrl+C.", flush=True)
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        boxes.close()


if __name__ == "__main__":
    main()
