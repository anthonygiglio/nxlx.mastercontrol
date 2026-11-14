#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""nxlx-signer: signs node certificates by itself, so a new node has HTTPS with no step from the owner (D139,
docs/HTTPS.md "Automatic certificates").

Runs on the owner's always-on computer, from the folder `boxcert.py make-signer` wrote (signer.key, signer.pem, root.pem,
signed.json). Python 3 and `openssl`, nothing else. Plain HTTP on the local network:

  GET  /v1/info        {"root", "signer", "fingerprint"}: the public certificates, nothing secret
  GET  /v1/root-move   {"move", "sig"}: the message the OLD root signed (boxcert.py move-root), or 404
  POST /v1/sign        {"csr": the node's request, "hostname": its host name}
                       -> {"chain": the node's certificate, the signer, the root; "names", "ends", "left_out"}

Every request: the caller is on a private network (10/8, 172.16/12, 192.168/16, loopback); the Host header is an
address or one of this computer's names (DNS rebinding); a request with an Origin header is refused (a web page in a
browser on the LAN cannot use this service); a POST must say Content-Type: application/json.

Rules for /v1/sign (a request breaks one, nothing is signed and the answer says why):
  * the request's own signature checks out; its key is EC P-256; its subject is exactly one CN, one of its names;
  * names are `.local` names only and must include "<hostname>.local";
  * an address goes into the certificate only when it is the caller's own private address;
  * first come, first pinned, for names AND addresses: a name or address signed once is signed again only for the
    same public key. One signed for another key is refused with the way out: `nxlx-signer.py release NAME`;
  * the pin file (signed.json) must read cleanly: missing or damaged means nothing is signed (503), never "nothing
    pinned". It is written atomically, the copy before each change is signed.json.bak, and one process at a time holds it;
  * refusals count against the caller only (6 tries an hour); signings count against everybody (30 an hour);
    every signing and every refusal is logged (signer.log, beside the key).

  nxlx-signer.py [--dir DIR] [--bind 0.0.0.0] [--port 8781] [--host-name NAME ...]   serve
  nxlx-signer.py list [--dir DIR]                          the names and addresses pinned so far
  nxlx-signer.py release NAME|ADDRESS [--dir DIR]          forget one pin (a node made a new key, or an address moved)
  nxlx-signer.py pin [NAME|ADDRESS ...] --cert FILE        pin these (default: every name and address in FILE) to the
                                                           key of a node's certificate, before that node asks

The trade-off, plainly: anyone on the local network can get a certificate for a `.local` name, or for their own
address, that nobody has taken yet. What is taken is protected by the pin.
"""

import argparse
import datetime
import http.server
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "nxlx-signer")
DEFAULT_PORT = 8781
MAX_BODY = 16 * 1024
MAX_CSR = 8 * 1024
MAX_THREADS = 16
PER_CALLER, IN_ALL, WINDOW = 6, 30, 3600.0
LEAF_DAYS = 397
CALLER_RANGES = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128")]
SIGNED_RANGES = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
HOST_WORD = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
SUBJECT = re.compile(r"subject\s*=\s*CN\s*=\s*([A-Za-z0-9.-]{1,253})\s*")


def _load_boxcert():
    """tools/boxcert.py from beside this file: the request reader, the pin file and the node certificate's profile are
    the same as the tool's."""
    spec = importlib.util.spec_from_file_location("nxlx_boxcert", os.path.join(HERE, "boxcert.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


boxcert = _load_boxcert()


class Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def find_openssl(explicit=None):
    for exe in (explicit, os.environ.get("BOXCERT_OPENSSL"), shutil.which("openssl"),
                r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if exe and os.path.isfile(exe):
            return exe
    raise SystemExit("nxlx-signer: openssl was not found (on Windows it comes with Git for Windows; or give --openssl)")


def _ip(text):
    try:
        ip = ipaddress.ip_address((text or "").split("%")[0])
    except ValueError:
        return None
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def private_caller(address):
    ip = _ip(address)
    return ip is not None and any(ip in n for n in CALLER_RANGES)


def _now_iso(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).replace(microsecond=0).isoformat()


class Signer:
    def __init__(self, folder, openssl, now=time.time, log_to=None):
        self.folder, self.exe, self.now = folder, openssl, now
        self.lock = threading.Lock()
        self.tries = {}                 # caller address -> times of its requests, signed or refused, in the window
        self.signed = []                # times of every signing in the window, whoever asked
        for name in (boxcert.SIGNER_KEY, boxcert.SIGNER_PEM, boxcert.ROOT_PEM):
            if not os.path.isfile(self.path(name)):
                raise SystemExit("nxlx-signer: %s is missing; --dir must be the folder `boxcert.py make-signer` wrote" % self.path(name))
        self.log_path = log_to or self.path("signer.log")

    def path(self, name):
        return os.path.join(self.folder, name)

    def _text(self, name):
        with open(self.path(name), "r", encoding="utf-8", errors="replace") as f:
            return f.read(65536)

    def log(self, line):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (_now_iso(self.now()), line))

    # -- the pins (boxcert's file code; one process at a time) --
    def _pins(self):
        try:
            return boxcert.read_pins(self.folder)
        except boxcert.BoxcertError as e:
            raise Refused(str(e), 503)

    def pins(self):
        with self._held():
            return self._pins()

    def _held(self):
        lock = boxcert.PinLock(self.folder)

        class Held:
            def __enter__(s):
                try:
                    lock.__enter__()
                except boxcert.BoxcertError as e:
                    raise Refused(str(e), 503)

            def __exit__(s, *a):
                lock.__exit__(*a)
        return Held()

    @staticmethod
    def _word(name):
        name = (name or "").strip().lower().rstrip(".")
        ip = _ip(name)
        return str(ip) if ip is not None else name

    def release(self, name):
        name = self._word(name)
        with self.lock, self._held():
            pins = self._pins()
            if name not in pins:
                raise Refused("%s is not pinned (nxlx-signer.py list shows what is)" % name, 404)
            del pins[name]
            boxcert.write_pins(self.folder, pins)
        self.log("released %s" % name)
        return name

    def pin(self, cert_path, names=()):
        """Pin names and addresses to the key of a node's certificate (existing nodes, before they ask)."""
        try:
            key = boxcert.public_key_hash(self.exe, cert_path)
            c = boxcert.read_cert(self.exe, cert_path)
        except boxcert.BoxcertError as e:
            raise Refused(str(e))
        words = [self._word(n) for n in names] or c["dns"] + c["ips"]
        if not words:
            raise Refused("%s names nothing to pin" % cert_path)
        stamp = _now_iso(self.now())
        with self.lock, self._held():
            pins = self._pins()
            for w in words:
                if w in pins and pins[w].get("key") != key:
                    raise Refused("%s is pinned to another key already; release it first if that is right" % w, 409)
            for w in words:
                pins[w] = {"key": key, "first": (pins.get(w) or {}).get("first", stamp), "last": stamp, "from": "pinned by hand", "serial": c["serial"]}
            boxcert.write_pins(self.folder, pins)
        self.log("pinned %s to the key of %s" % (", ".join(words), os.path.basename(cert_path)))
        return words

    # -- what the nodes ask --
    def info(self):
        return {"root": self._text(boxcert.ROOT_PEM), "signer": self._text(boxcert.SIGNER_PEM),
                "fingerprint": boxcert.read_cert(self.exe, self.path(boxcert.ROOT_PEM))["fingerprint"].replace(" ", "")}

    def root_move(self):
        if not (os.path.isfile(self.path(boxcert.ROOT_MOVE)) and os.path.isfile(self.path(boxcert.ROOT_MOVE_SIG))):
            return None
        return {"move": self._text(boxcert.ROOT_MOVE), "sig": self._text(boxcert.ROOT_MOVE_SIG)}

    def _limit(self, caller):
        """Refusals cost the caller only; signings cost everybody (so junk from a few addresses cannot block real nodes)."""
        t = self.now()
        for k in list(self.tries):
            self.tries[k] = [x for x in self.tries[k] if t - x < WINDOW]
            if not self.tries[k]:
                del self.tries[k]
        self.signed = [x for x in self.signed if t - x < WINDOW]
        if len(self.tries.get(caller, [])) >= PER_CALLER:
            raise Refused("too many requests from this address in the last hour; try again later", 429)
        if len(self.signed) >= IN_ALL:
            raise Refused("too many certificates signed in the last hour; try again later", 429)
        self.tries.setdefault(caller, []).append(t)

    def sign(self, body, caller):
        """The chain for a node's request, or Refused. Every outcome is logged."""
        try:
            with self.lock:
                if not private_caller(caller):
                    raise Refused("this signer answers the local network only", 403)
                self._limit(caller)
                with self._held():
                    out = self._sign(body, caller)
                self.signed.append(self.now())
            self.log("signed for %s (%s), serial %s, ends %s" % (", ".join(out["names"]), caller, out["serial"], out["ends"]))
            if out.pop("short", False):
                self.log("NOTE: the signer ends within a year, so certificates are getting shorter: make a new signer "
                         "(boxcert.py make-signer, after moving the old signer folder away) and copy it here")
            return out
        except Refused as e:
            self.log("refused %s: %s" % (caller, e.message))
            raise

    def _sign(self, body, caller):
        if not isinstance(body, dict) or not isinstance(body.get("csr"), str) or not isinstance(body.get("hostname"), str):
            raise Refused('send {"csr": the request, "hostname": the node\'s host name}')
        csr_text, host = body["csr"], body["hostname"].strip().lower()
        if len(csr_text) > MAX_CSR or "-----BEGIN CERTIFICATE REQUEST-----" not in csr_text:
            raise Refused("that is not a certificate request")
        if not HOST_WORD.fullmatch(host):
            raise Refused("%r is not a host name" % host[:64])
        own = host + ".local"
        pins = self._pins()                                     # before anything else: a bad pin file signs nothing
        with tempfile.TemporaryDirectory() as tmp:
            csr = os.path.join(tmp, "node.csr")
            with open(csr, "w", encoding="ascii", errors="replace") as f:
                f.write(csr_text)
            try:
                req = boxcert.read_request(self.exe, csr)
            except boxcert.BoxcertError:
                raise Refused("the request does not check out (its signature is wrong or it is not a request)")
            code, text, _ = boxcert.run(self.exe, ["req", "-in", csr, "-noout", "-text"])
            if code or "id-ecPublicKey" not in text or not ("prime256v1" in text or "P-256" in text):
                raise Refused("the request's key must be EC P-256, as a node makes it")
            dns = []
            for d in req["dns"]:
                d = d.lower().rstrip(".")
                if not (boxcert.valid_dns(d) and d.endswith(".local") and not d.startswith("*")):
                    raise Refused("%s: only .local names are signed here" % d[:80])
                if d not in dns:
                    dns.append(d)
            if own not in dns:
                raise Refused("the request must name the node's own %s" % own)
            if len(dns) > 12:
                raise Refused("too many names")
            code, subj, _ = boxcert.run(self.exe, ["req", "-in", csr, "-noout", "-subject", "-nameopt", "RFC2253"])
            m = SUBJECT.fullmatch(subj.strip()) if not code else None
            if not m:
                raise Refused("the request's subject must be exactly one CN (the node's .local name), nothing else")
            caller_ip = _ip(caller)
            ips, left_out = [], []
            for i in req["ips"]:
                ip = _ip(i)
                if ip is not None and ip.version == 4 and any(ip in n for n in SIGNED_RANGES) and ip == caller_ip:
                    ips.append(str(ip))
                else:
                    left_out.append(i)
            if m.group(1).lower() not in dns + ips:
                raise Refused("the request's subject %s is not one of its names" % m.group(1)[:80])
            try:
                key = boxcert.public_key_hash(self.exe, csr, "req")
            except boxcert.BoxcertError:
                raise Refused("the request's key could not be read")
            for d in dns + ips:
                pin = pins.get(d)
                if pin and pin.get("key") != key:
                    raise Refused("%s was signed before for another key (first on %s). If that node made a new key, was set up again or "
                                  "the address moved to another node, release it on the signing computer: python tools/nxlx-signer.py release %s"
                                  % (d, str(pin.get("first", "?"))[:10], d), 409)
            days = min(LEAF_DAYS, self._signer_days_left())
            if days < 1:
                raise Refused("the signer has run out; make a new one (boxcert.py make-signer) and copy it here", 503)
            serial = boxcert.new_serial()
            cfg, leaf = os.path.join(tmp, "leaf.cnf"), os.path.join(tmp, "leaf.pem")
            with open(cfg, "w") as f:
                f.write(boxcert.leaf_config(dns, ips))
            code, _, err = boxcert.run(self.exe, ["x509", "-req", "-in", csr, "-CA", self.path(boxcert.SIGNER_PEM), "-CAkey", self.path(boxcert.SIGNER_KEY),
                                                  "-set_serial", "0x" + serial, "-days", str(days), "-sha256",
                                                  "-extfile", cfg, "-extensions", "v3_leaf", "-out", leaf])
            if code:
                raise Refused("signing failed: %s" % (err.strip().splitlines() or ["no message"])[-1][:160], 500)
            info = boxcert.read_cert(self.exe, leaf)
            with open(leaf) as f:
                chain = f.read().strip() + "\n" + self._text(boxcert.SIGNER_PEM).strip() + "\n" + self._text(boxcert.ROOT_PEM).strip() + "\n"
        stamp = _now_iso(self.now())
        for d in dns + ips:
            pins[d] = {"key": key, "first": (pins.get(d) or {}).get("first", stamp), "last": stamp, "from": caller, "serial": info["serial"]}
        boxcert.write_pins(self.folder, pins)
        return {"chain": chain, "names": dns + ips, "ends": info["end"].date().isoformat(), "serial": info["serial"], "left_out": left_out,
                "short": days < LEAF_DAYS}

    def _signer_days_left(self):
        end = boxcert.read_cert(self.exe, self.path(boxcert.SIGNER_PEM))["end"]
        return int((end - datetime.datetime.fromtimestamp(self.now(), datetime.timezone.utc)).total_seconds() // 86400)


def host_names(extra=()):
    """The names a node may call this computer by: its own host name, with and without .local, and any given."""
    h = (socket.gethostname() or "").strip().lower().split(".")[0]
    names = {n.strip().lower().rstrip(".") for n in extra if n and n.strip()}
    if h:
        names.update({h, h + ".local"})
    return names


def host_ok(header, allowed):
    """An address literal, or one of `allowed`, with at most a port: nothing a rebinding web page could stand behind."""
    h = (header or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):
        end = h.find("]")
        return end > 0 and _ip(h[1:end]) is not None and re.fullmatch(r"(:[0-9]{1,5})?", h[end + 1:]) is not None
    name = re.sub(r":[0-9]{1,5}$", "", h)
    return _ip(name) is not None or name.rstrip(".") in allowed


def make_handler(signer, allowed=None):
    allowed = host_names() if allowed is None else allowed

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "nxlx-signer"
        sys_version = ""
        timeout = 15

        def _json(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _gate(self):
            """None, or why this request is not answered."""
            if not private_caller(self.client_address[0]):
                return 403, "this signer answers the local network only"
            if self.headers.get("Origin") is not None:
                return 403, "requests from web pages are not answered"
            if not host_ok(self.headers.get("Host"), allowed):
                return 421, "call this signer by its address or its own name"
            return None

        def do_GET(self):
            no = self._gate()
            if no:
                return self._json(no[0], {"error": no[1]})
            try:
                if self.path == "/v1/info":
                    return self._json(200, signer.info())
                if self.path == "/v1/root-move":
                    move = signer.root_move()
                    return self._json(200, move) if move else self._json(404, {"error": "no root move here"})
            except boxcert.BoxcertError as e:
                return self._json(500, {"error": str(e)[:200]})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            no = self._gate()
            if no:
                return self._json(no[0], {"error": no[1]})
            if self.path != "/v1/sign":
                return self._json(404, {"error": "not found"})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._json(415, {"error": "send Content-Type: application/json"})
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._json(413, {"error": "send a request of at most %d bytes" % MAX_BODY})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "that is not JSON"})
            try:
                return self._json(200, signer.sign(body, self.client_address[0]))
            except Refused as e:
                return self._json(e.status, {"error": e.message})
            except boxcert.BoxcertError as e:
                return self._json(500, {"error": str(e)[:200]})

        def log_message(self, *a):
            pass

    return Handler


class Server(http.server.ThreadingHTTPServer):
    """At most MAX_THREADS requests at once; a connection beyond that is closed at once."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *a, **k):
        self.slots = threading.BoundedSemaphore(MAX_THREADS)
        super().__init__(*a, **k)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main(argv=None, out=print):
    p = argparse.ArgumentParser(prog="nxlx-signer.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", default=DEFAULT_DIR, help="the signer's folder (default %s)" % DEFAULT_DIR)
    p.add_argument("--openssl", default=None)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host-name", action="append", default=[], help="a further name nodes call this computer by")
    p.add_argument("--cert", default=None, help="with pin: a node's certificate file")
    p.add_argument("cmd", nargs="?", choices=("serve", "list", "release", "pin"), default="serve")
    p.add_argument("names", nargs="*")
    a = p.parse_args(argv)
    signer = Signer(os.path.expanduser(a.dir), find_openssl(a.openssl))
    try:
        if a.cmd == "list":
            for name, pin in sorted(signer.pins().items()):
                out("%-32s key %s...  first %s  last %s  from %s" % (name, pin.get("key", "")[:16], str(pin.get("first"))[:10], str(pin.get("last"))[:10], pin.get("from")))
            return 0
        if a.cmd == "release":
            if len(a.names) != 1:
                raise Refused("give one name or address to release")
            out("released %s: the next node to ask for it gets it" % signer.release(a.names[0]))
            return 0
        if a.cmd == "pin":
            if not a.cert:
                raise Refused("give the node's certificate with --cert FILE")
            out("pinned %s" % ", ".join(signer.pin(a.cert, a.names)))
            return 0
    except Refused as e:
        out("nxlx-signer: %s" % e.message)
        return 1
    srv = Server((a.bind, a.port), make_handler(signer, host_names(a.host_name)))
    signer.log("listening on %s:%d" % (a.bind, a.port))
    if sys.stdout is not None:
        out("nxlx-signer: listening on %s:%d, folder %s" % (a.bind, a.port, signer.folder))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
