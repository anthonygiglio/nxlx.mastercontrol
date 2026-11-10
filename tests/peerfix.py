# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Test helpers for the peer channel (D132, slices 3 and 4a): a node made of the real Handover and Peers, a certificate from
a throwaway root, a small TLS server that answers the card the way the panel's port does, and the REAL peer listener of
pvj/server.py (a client certificate required, the pinned root the only CA) on a port of its own. Not a test module. The
roots here have no name constraints so that loopback addresses can be used."""
import base64
import http.server
import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import types

from pvj import fleetlist as FL, handover as hv, httpsbox, peer, server
from pvj.settings import Settings
from tests.test_boxcert import boxcert, make_request

PASS = "correct horse battery"


def make_root(exe, folder, name="Test root"):
    """A root with the production name constraints (.local names and the private ranges) plus loopback, where the tests listen."""
    boxcert.make_root(exe, folder, name, ("local",), True, lambda prompt: PASS, lambda *a: None,
                      ranges=tuple(boxcert.PRIVATE_RANGES) + ("127.0.0.0/255.0.0.0",))
    with open(os.path.join(folder, "root.pem")) as f:
        return f.read()


def sign_list(exe, root_dir, data, passphrase=PASS):
    """(body bytes, signature text) of a list, signed with plain openssl by the root in root_dir."""
    body = FL.canonical(data)
    message = FL.signed_bytes(data["fleet"], data["serial"], body)
    sig = os.path.join(root_dir, "list.sig.tmp")
    msg = os.path.join(root_dir, "list.msg.tmp")
    with open(msg, "wb") as f:
        f.write(message)
    r = subprocess.run([exe, "dgst", "-sha256", "-sign", os.path.join(root_dir, "root.key"), "-passin", "stdin", "-out", sig, msg],
                       input=passphrase + "\n", capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(sig, "rb") as f:
        return body, base64.encodebytes(f.read()).decode()


def list_data(fleet, serial, nodes):
    return {"v": 1, "fleet": fleet, "name": "Rig", "serial": serial, "issued": "2026-11-09",
            "nodes": [{"id": n.id, "name": n.name, "key": n.key, "carry": carry} for n, carry in nodes]}


class Events:
    def __init__(self):
        self.lines = []

    def __call__(self, kind, text, who=None):
        self.lines.append((kind, text, who))


class PeerNode:
    """One node: its certificate (from `root_dir`), its Handover and Peers, and a TLS server answering for it."""

    def __init__(self, case, name, node_id, root_dir, root_text, exe, ips=("127.0.0.1",), days=30, key_from=None, serve=True,
                 now=None, wall=None, passphrase=PASS):
        self.case, self.name, self.id, self.exe = case, name, node_id, exe
        self.trusted = False
        self.events = Events()
        self.state = case.workdir()
        work = case.workdir()
        if key_from is not None:                              # a second certificate for the same key
            key, csr = key_from.reissue(work, [name + ".local"], list(ips))
        else:
            key, csr = make_request(exe, work, [name + ".local"], list(ips), cn=name + ".local")
        cert = os.path.join(work, "cert.pem")
        boxcert.sign(exe, root_dir, csr, [], [], [], days, cert, lambda p: passphrase, out=lambda *a: None)
        self.key_path = key
        with open(cert) as f:
            self.cert_pem = f.read()
        self.cert_path = cert
        self.key = FL.key_hash(FL.certificate_der(self.cert_pem))
        self.settings = Settings(os.path.join(self.state, "settings.json"))
        self.settings.load()
        self.box = httpsbox.HttpsBox(os.path.join(self.state, "tls"), self.settings, openssl=exe, clock_trusted=lambda: self.trusted)
        self.box._ensure_folder()
        self.box._write("root.pem", root_text)
        self.box._write("cert.pem", self.cert_pem)
        shutil.copy(key, self.box.path("key.pem"))
        os.chmod(self.box.path("key.pem"), 0o600)
        self.handover = hv.Handover(self.state, self.box, self.settings, lambda: {"id": node_id, "name": name}, event=self.events, openssl=exe)
        self.handover.load()
        self.directory = {}
        self.pulls = []
        self.threads = []
        kw = {}
        if now:
            kw["now"] = now
        if wall:
            kw["wall"] = wall
        self.peers = peer.Peers(self.handover, self.box, lambda i: list(self.directory.get(i, [])), spawn=self._spawn, **kw)
        self.calls = []                                       # what reached this node's servers: (method, path)
        self.port = None
        self.peer_port = None
        self.api = types.SimpleNamespace(peers=self.peers, tickets=None)       # what the real peer handler reads
        if serve:
            self._serve()
            self._serve_peers()

    def _spawn(self, fn):
        """A pull runs in a thread of its own, as in production; `settle()` waits for them."""
        self.pulls.append(fn)
        t = threading.Thread(target=fn, daemon=True)
        self.threads.append(t)
        t.start()

    def settle(self, seconds=10):
        for t in list(self.threads):
            t.join(seconds)
        self.threads = [t for t in self.threads if t.is_alive()]

    def reissue(self, work, names, ips):
        """A new request for the SAME key as this node's (a renewal that keeps the key)."""
        import shutil
        shutil.copy(self.key_path, os.path.join(work, "box.key"))
        cnf = os.path.join(work, "req.cnf")
        san = ",".join(["DNS:%s" % n for n in names] + ["IP:%s" % i for i in ips])
        with open(cnf, "w") as f:
            f.write("[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=%s\n" % (names[0], san))
        subprocess.run([self.exe, "req", "-new", "-key", os.path.join(work, "box.key"), "-config", cnf, "-out", os.path.join(work, "box.csr")],
                       check=True, capture_output=True)
        return os.path.join(work, "box.key"), os.path.join(work, "box.csr")

    def take(self, body, sig):
        return self.handover.take_list(body, sig)

    # -- the server --
    def _serve(self):
        node = self
        bundle = self.cert_path

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                node.calls.append(("GET", self.path))
                if self.path == "/api/fleet/card":
                    card = node.card_override if node.card_override is not None else node.handover.card()
                    return self._answer(200 if card else 404, card or {"error": "not found"})
                self._answer(404, {"error": "not found"})

            def do_POST(self):
                node.calls.append(("POST", self.path))
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._answer(400, {"error": "invalid JSON"})
                self._answer(404, {"error": "not found"})            # the panel's port no longer answers a peer call (D132, 4a)

            def log_message(self, *a):
                pass

        self.card_override = None
        class Quiet(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):      # a client that walked away mid-handshake is not a test failure
                pass

        server = Quiet(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(bundle, self.key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        server.daemon_threads = True
        self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.case.addCleanup(server.server_close)
        self.case.addCleanup(server.shutdown)
        self.server = server

    def _serve_peers(self):
        """The real peer listener (server.PeerServer with server.make_peer_handler) on a port of its own."""
        node = self
        base = server.make_peer_handler(self.api)

        class Recording(base):
            def do_POST(self):
                node.calls.append(("POST", self.path.split("?")[0]))
                super().do_POST()

            def log_message(self, *a):
                pass

        class Quiet(server.PeerServer):
            def handle_error(self, request, client_address):
                pass

        srv = Quiet(("127.0.0.1", 0), Recording, self.box)
        self.peer_port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.case.addCleanup(srv.server_close)
        self.case.addCleanup(srv.shutdown)
        self.peer_server = srv

    @property
    def where(self):
        """As the Nodes list would give it: address, the panel's port, no name, the peer port."""
        return ("127.0.0.1", self.port, None, self.peer_port)

    def named(self, name=None):
        """The same place as the Nodes list would give it, with the name the node announced."""
        return ("127.0.0.1", self.port, name or self.name, self.peer_port)


class Silent:
    """A listener that accepts a connection and never says anything: a node that hangs."""

    def __init__(self, case):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.held = []
        self.stop = threading.Event()

        def run():
            self.sock.settimeout(0.2)
            while not self.stop.is_set():
                try:
                    c, _ = self.sock.accept()
                    self.held.append(c)
                except OSError:
                    pass
        threading.Thread(target=run, daemon=True).start()
        case.addCleanup(self.close)

    def close(self):
        self.stop.set()
        for c in self.held:
            c.close()
        self.sock.close()

    @property
    def where(self):
        return ("127.0.0.1", self.port, None, self.port)         # the same port for the panel and the peer call: it answers neither


def answer(peers, body, source, key=None):
    """What the peer listener does after the handshake, for a test that calls the answer directly: the key the handshake proved is
    `key`, or, when a test does not care, the one the body claims (so the claim and the proof agree)."""
    return peers.handle_peer(body, source, key if key is not None else (body.get("key") if isinstance(body, dict) else None))
