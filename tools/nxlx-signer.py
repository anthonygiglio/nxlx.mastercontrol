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

Rules for /v1/sign (a request breaks one, nothing is signed and the answer says why):
  * the caller is on a private network (10/8, 172.16/12, 192.168/16, loopback);
  * the request is a valid one (its own signature checks out); names are `.local` names only, and must include
    "<hostname>.local"; the subject, when it has a name, is one of them;
  * an address in the request goes into the certificate only when it is the caller's own address and private
    (an address moves between nodes with DHCP, so it is never pinned); other addresses are left out and listed;
  * first come, first pinned: a name signed once is signed again only for the same public key. A name signed for
    another key is refused with the way out: `nxlx-signer.py release NAME` on this computer;
  * at most 6 signings an hour per caller and 30 an hour in all; every signing and every refusal is logged
    (signer.log, beside the key).

  nxlx-signer.py [--dir DIR] [--bind 0.0.0.0] [--port 8781] [--openssl EXE]   serve
  nxlx-signer.py list [--dir DIR]                                             the names pinned so far
  nxlx-signer.py release NAME [--dir DIR]                                     forget one pinned name (a node made a new key)

The trade-off, plainly: anyone on the local network can get a certificate for a `.local` name nobody has taken yet.
A name already taken is protected by the pin. Devices that trust the root trust such a certificate for that name.
"""

import argparse
import base64
import datetime
import hashlib
import http.server
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "nxlx-signer")
DEFAULT_PORT = 8781
MAX_BODY = 16 * 1024
MAX_CSR = 8 * 1024
PER_CALLER, IN_ALL, WINDOW = 6, 30, 3600.0
LEAF_DAYS = 397
CALLER_RANGES = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128")]
SIGNED_RANGES = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
HOST_WORD = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def _load_boxcert():
    """tools/boxcert.py from beside this file: the request reader and the node certificate's profile are the same as `sign`."""
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


def private_caller(address):
    try:
        ip = ipaddress.ip_address((address or "").split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in n for n in CALLER_RANGES)


def _now_iso(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).replace(microsecond=0).isoformat()


class Signer:
    def __init__(self, folder, openssl, now=time.time, log_to=None):
        self.folder, self.exe, self.now = folder, openssl, now
        self.lock = threading.Lock()
        self.calls = {}                 # caller address -> times of signings and refusals in the window
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

    # -- the pins --
    def pins(self):
        try:
            with open(self.path(boxcert.SIGNER_PINS), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_pins(self, pins):
        tmp = self.path(boxcert.SIGNER_PINS) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path(boxcert.SIGNER_PINS))

    def release(self, name):
        name = (name or "").strip().lower().rstrip(".")
        with self.lock:
            pins = self.pins()
            if name not in pins:
                raise Refused("%s is not pinned (nxlx-signer.py list shows what is)" % name, 404)
            del pins[name]
            self._save_pins(pins)
        self.log("released %s" % name)
        return name

    # -- what the nodes ask --
    def info(self):
        root = self._text(boxcert.ROOT_PEM)
        return {"root": root, "signer": self._text(boxcert.SIGNER_PEM),
                "fingerprint": boxcert.read_cert(self.exe, self.path(boxcert.ROOT_PEM))["fingerprint"].replace(" ", "")}

    def root_move(self):
        if not (os.path.isfile(self.path(boxcert.ROOT_MOVE)) and os.path.isfile(self.path(boxcert.ROOT_MOVE_SIG))):
            return None
        return {"move": self._text(boxcert.ROOT_MOVE), "sig": self._text(boxcert.ROOT_MOVE_SIG)}

    def _limit(self, caller):
        """Raises when the caller or everybody together has asked too often in the last hour."""
        t = self.now()
        for k in list(self.calls):
            self.calls[k] = [x for x in self.calls[k] if t - x < WINDOW]
            if not self.calls[k]:
                del self.calls[k]
        if len(self.calls.get(caller, [])) >= PER_CALLER or sum(len(v) for v in self.calls.values()) >= IN_ALL:
            raise Refused("too many requests in the last hour; try again later", 429)
        self.calls.setdefault(caller, []).append(t)

    def sign(self, body, caller):
        """The chain for a node's request, or Refused. Every outcome is logged."""
        try:
            with self.lock:
                if not private_caller(caller):
                    raise Refused("this signer answers the local network only", 403)
                self._limit(caller)
                out = self._sign(body, caller)
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
        with tempfile.TemporaryDirectory() as tmp:
            csr = os.path.join(tmp, "node.csr")
            with open(csr, "w", encoding="ascii", errors="replace") as f:
                f.write(csr_text)
            try:
                req = boxcert.read_request(self.exe, csr)
            except boxcert.BoxcertError:
                raise Refused("the request does not check out (its signature is wrong or it is not a request)")
            dns = []
            for d in req["dns"]:
                d = d.lower().rstrip(".")
                if not (boxcert.valid_dns(d) and d.endswith(".local") and d.count(".") >= 1 and not d.startswith("*")):
                    raise Refused("%s: only .local names are signed here" % d[:80])
                if d not in dns:
                    dns.append(d)
            if own not in dns:
                raise Refused("the request must name the node's own %s" % own)
            cn = (req["cn"] or "").lower()
            if cn and cn not in dns and cn not in req["ips"]:
                raise Refused("the request's subject %s is not one of its names" % cn[:80])
            if len(dns) > 12:
                raise Refused("too many names")
            ips, left_out = [], []
            for i in req["ips"]:
                ip = boxcert.valid_ip(i)
                if ip is not None and ip.version == 4 and any(ip in n for n in SIGNED_RANGES) and str(ip) == caller.split("%")[0].replace("::ffff:", ""):
                    ips.append(str(ip))
                else:
                    left_out.append(i)
            key = self._key_hash(csr)
            pins = self.pins()
            for d in dns:
                pin = pins.get(d)
                if pin and pin.get("key") != key:
                    raise Refused("%s was signed before for another key (first on %s). If that node made a new key or was set up again, "
                                  "release the name on the signing computer: python tools/nxlx-signer.py release %s" % (d, str(pin.get("first", "?"))[:10], d), 409)
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
        for d in dns:
            pins[d] = {"key": key, "first": (pins.get(d) or {}).get("first", stamp), "last": stamp, "from": caller, "serial": info["serial"]}
        self._save_pins(pins)
        return {"chain": chain, "names": dns + ips, "ends": info["end"].date().isoformat(), "serial": info["serial"], "left_out": left_out,
                "short": days < LEAF_DAYS}

    def _key_hash(self, csr):
        code, pub, err = boxcert.run(self.exe, ["req", "-in", csr, "-noout", "-pubkey"])
        body = "".join(l for l in pub.splitlines() if l and not l.startswith("-----"))
        try:
            return hashlib.sha256(base64.b64decode(body, validate=True)).hexdigest()
        except ValueError:
            raise Refused("the request's key could not be read")

    def _signer_days_left(self):
        end = boxcert.read_cert(self.exe, self.path(boxcert.SIGNER_PEM))["end"]
        return int((end - datetime.datetime.fromtimestamp(self.now(), datetime.timezone.utc)).total_seconds() // 86400)


def make_handler(signer):
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

        def do_GET(self):
            if not private_caller(self.client_address[0]):
                return self._json(403, {"error": "this signer answers the local network only"})
            if self.path == "/v1/info":
                return self._json(200, signer.info())
            if self.path == "/v1/root-move":
                move = signer.root_move()
                return self._json(200, move) if move else self._json(404, {"error": "no root move here"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/v1/sign":
                return self._json(404, {"error": "not found"})
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

        def log_message(self, *a):
            pass

    return Handler


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16


def main(argv=None, out=print):
    p = argparse.ArgumentParser(prog="nxlx-signer.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", default=DEFAULT_DIR, help="the signer's folder (default %s)" % DEFAULT_DIR)
    p.add_argument("--openssl", default=None)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("cmd", nargs="?", choices=("serve", "list", "release"), default="serve")
    p.add_argument("name", nargs="?")
    a = p.parse_args(argv)
    signer = Signer(os.path.expanduser(a.dir), find_openssl(a.openssl))
    if a.cmd == "list":
        for name, pin in sorted(signer.pins().items()):
            out("%-32s key %s...  first %s  last %s  from %s" % (name, pin.get("key", "")[:16], str(pin.get("first"))[:10], str(pin.get("last"))[:10], pin.get("from")))
        return 0
    if a.cmd == "release":
        try:
            out("released %s: the next node to ask for it gets it" % signer.release(a.name))
            return 0
        except Refused as e:
            out("nxlx-signer: %s" % e.message)
            return 1
    srv = Server((a.bind, a.port), make_handler(signer))
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
