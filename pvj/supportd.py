# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-supportd: the small root helper that opens and closes a remote support tunnel, on behalf of the panel.

A support session is started by someone at the studio (the panel checks who may). This helper only does the network
part, as fixed argument lists, never a shell:

* start: a WireGuard interface `wg-pvj` that dials out to the owner's support server (nothing listens on the internet
  at the studio), with this box's own key, and a firewall table that lets in only the panel (TCP 80) and ping from
  the tunnel, blocks everything else there (OSC, DMX, SSH and the rest), forwards nothing, and lets the box start no
  connection into the tunnel. It closes by itself at the deadline, which this helper keeps, not the panel;
* extend: a new deadline for the running session, without touching the tunnel;
* stop: removes both; status: whether it is up, until when, and when the support server last answered;
* key: this box's public key (the private key is made on first use and never leaves /var/lib/pvj-support).

It fails closed: at start-up it removes any interface or firewall table left by an earlier run (a crash or a power cut
never leaves a tunnel open), and a reboot ends a session. It answers only root and pvj-web on its socket.
"""

import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

from . import paths
from .netd import exchange, NetServer  # the same small, reviewed socket server

IFACE = "wg-pvj"
TABLE = "pvj_support"
PANEL_PORT = 80
MIN_MINUTES, MAX_MINUTES = 5, 240
MAX_TOTAL_MINUTES = 480          # however often it is extended, a session ends 8 hours after it started
KEEPALIVE = 25                   # seconds: keeps the studio router's NAT entry open, so support can reach the box
HOST = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
SUPPORT_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]


class SupportError(ValueError):
    pass


def check_key(key):
    """A WireGuard public key: 32 bytes, base64. Returns it or raises SupportError."""
    if not isinstance(key, str) or len(key) != 44:
        raise SupportError("the support server's key is 44 characters of base64")
    try:
        raw = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        raise SupportError("the support server's key is not valid base64")
    if len(raw) != 32:
        raise SupportError("the support server's key is not a WireGuard key")
    return key


def check_endpoint(endpoint):
    """host:port of the support server, or [IPv6]:port. Returns (host, port) or raises SupportError."""
    if not isinstance(endpoint, str) or not 3 <= len(endpoint) <= 260:
        raise SupportError("the support server is host:port, for example support.example.com:51820")
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d{1,5})", endpoint) or re.fullmatch(r"([^:\[\]]+):(\d{1,5})", endpoint)
    if not m:
        raise SupportError("the support server is host:port, for example support.example.com:51820")
    host, port = m.group(1), int(m.group(2))
    if not 1 <= port <= 65535:
        raise SupportError("the port must be 1 to 65535")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST.fullmatch(host):
            raise SupportError("that is not a valid server name")
    return host, port


def check_addresses(address, network):
    """This box's tunnel address and the support network (both private IPv4). Returns (address, network)."""
    try:
        net = ipaddress.ip_network(network, strict=True)
        addr = ipaddress.ip_address(address)
    except (ValueError, TypeError):
        raise SupportError("the tunnel address and network must be like 10.77.0.5 and 10.77.0.0/24")
    if net.version != 4 or addr.version != 4:
        raise SupportError("the tunnel uses IPv4 addresses")
    if not any(net.subnet_of(n) for n in SUPPORT_NETS):
        raise SupportError("the support network must be a private range (such as 10.77.0.0/24)")
    if not 16 <= net.prefixlen <= 30:
        raise SupportError("the support network must be a /16 to /30")
    if addr not in net or addr in (net.network_address, net.broadcast_address):
        raise SupportError("this box's tunnel address must be inside the support network")
    return str(addr), str(net)


def check_config(message):
    """The tunnel settings from a start request, validated here again (the panel checks them too)."""
    host, port = check_endpoint(message.get("endpoint"))
    key = check_key(message.get("server_key"))
    addr, net = check_addresses(message.get("address"), message.get("network"))
    minutes = message.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise SupportError("a session lasts %d to %d minutes" % (MIN_MINUTES, MAX_MINUTES))
    ep = ("[%s]:%d" % (host, port)) if ":" in host else ("%s:%d" % (host, port))
    panel = message.get("port", PANEL_PORT)
    if isinstance(panel, bool) or not isinstance(panel, int) or not 1 <= panel <= 65535:
        raise SupportError("the panel port must be 1 to 65535")
    return {"endpoint": ep, "server_key": key, "address": addr, "network": net, "minutes": minutes, "port": panel}


def ruleset(network, address, port=PANEL_PORT):
    """The firewall table for the tunnel: in, only the panel and ping from the support network to this box's own
    tunnel address; out, only replies; nothing forwarded."""
    return """table inet %(t)s {
    chain input {
        type filter hook input priority -10; policy accept;
        iifname "%(i)s" ip daddr != %(a)s drop
        iifname "%(i)s" ct state established,related accept
        iifname "%(i)s" ip saddr %(n)s tcp dport %(p)d ct state new accept
        iifname "%(i)s" ip saddr %(n)s icmp type echo-request accept
        iifname "%(i)s" drop
    }
    chain forward {
        type filter hook forward priority -10; policy accept;
        iifname "%(i)s" drop
        oifname "%(i)s" drop
    }
    chain output {
        type filter hook output priority -10; policy accept;
        oifname "%(i)s" ct state established,related accept
        oifname "%(i)s" icmp type echo-reply accept
        oifname "%(i)s" drop
    }
}
""" % {"t": TABLE, "i": IFACE, "n": network, "a": address, "p": port}


class SupportService:
    def __init__(self, keydir, runner=subprocess.run, log=print, timer=None, clock=time.monotonic, now=time.time,
                 which=shutil.which):
        self.keydir, self.runner, self.log = keydir, runner, log
        self._timer = timer or (lambda delay, fn: _start_timer(delay, fn))
        self._clock, self._now = clock, now
        self.lock = threading.Lock()
        self.session = None             # {"until": monotonic, "until_epoch", "address", "network", "endpoint"}
        self._cancel = None
        self._tools = {name: which(name) or which(name, path="/usr/sbin:/sbin:/usr/bin:/bin") for name in ("ip", "wg", "nft")}

    # -- commands ---------------------------------------------------------------------------------------------
    def _run(self, argv, stdin=None, timeout=20):
        tool = self._tools.get(argv[0])
        if tool is None:
            return 127, "%s is not installed" % argv[0]
        try:
            r = self.runner([tool] + argv[1:], input=stdin, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()

    def available(self):
        missing = [n for n, p in self._tools.items() if p is None]
        return (False, "not installed on this box: %s (package wireguard-tools / nftables)" % ", ".join(missing)) if missing else (True, "")

    def _keyfile(self):
        return os.path.join(self.keydir, "private.key")

    def public_key(self):
        """This box's public key, making the key pair on first use (the private key is root-only and stays here)."""
        path = self._keyfile()
        if not os.path.exists(path):
            code, out = self._run(["wg", "genkey"])
            if code != 0:
                raise SupportError("could not make a key: %s" % out[-160:])
            os.makedirs(self.keydir, mode=0o700, exist_ok=True)
            try:
                os.unlink(path + ".tmp")            # left by a crash half-way through
            except FileNotFoundError:
                pass
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(out.strip() + "\n")
            os.replace(path + ".tmp", path)
        with open(path) as f:
            private = f.read().strip()
        code, out = self._run(["wg", "pubkey"], stdin=private + "\n")
        if code != 0:
            raise SupportError("could not read this box's key: %s" % out[-160:])
        return check_key(out.strip())

    def teardown(self, reason):
        """Remove the tunnel and then its firewall table, whatever state they are in. Always safe to call. The table
        goes last, and stays if the interface could not be removed: a tunnel without its firewall is never left."""
        self._run(["ip", "link", "delete", IFACE])
        still_up, _ = self._run(["ip", "link", "show", IFACE])
        if still_up == 0:
            self.log("pvj-supportd: could not remove %s; its firewall stays in place" % IFACE)
        else:
            self._run(["nft", "delete", "table", "inet", TABLE])
        if self._cancel:
            self._cancel()
            self._cancel = None
        had = self.session is not None
        self.session = None
        if had:
            self.log("pvj-supportd: support tunnel closed (%s)" % reason)

    def start(self, message):
        cfg = check_config(message)
        ok, why = self.available()
        if not ok:
            return {"ok": False, "error": why}
        with self.lock:
            self.teardown("replaced by a new session")
            key = self.public_key()          # makes the key if needed
            prefix = ipaddress.ip_network(cfg["network"]).prefixlen
            steps = [
                (["ip", "link", "add", IFACE, "type", "wireguard"], None),
                (["nft", "-f", "-"], ruleset(cfg["network"], cfg["address"], cfg["port"])),        # the firewall before the link comes up
                (["wg", "set", IFACE, "private-key", self._keyfile(), "peer", cfg["server_key"], "endpoint", cfg["endpoint"],
                  "allowed-ips", cfg["network"], "persistent-keepalive", str(KEEPALIVE)], None),
                (["ip", "address", "add", "%s/%d" % (cfg["address"], prefix), "dev", IFACE], None),
                (["ip", "link", "set", IFACE, "up"], None),
            ]
            for argv, stdin in steps:
                code, out = self._run(argv, stdin=stdin)
                if code != 0:
                    self.teardown("start failed")
                    what = "the support server's name could not be found" if "resolve" in out.lower() or "name or service" in out.lower() else out[-200:]
                    return {"ok": False, "error": "could not open the tunnel: %s" % what}
            seconds = cfg["minutes"] * 60
            self.session = {"started": self._clock(), "until": self._clock() + seconds, "until_epoch": int(self._now() + seconds),
                            "address": cfg["address"], "network": cfg["network"], "endpoint": cfg["endpoint"]}
            self._cancel = self._timer(seconds, self._expire)
            self.log("pvj-supportd: support tunnel open to %s for %d minutes, as %s (box key %s)"
                     % (cfg["endpoint"], cfg["minutes"], cfg["address"], key))
            return self.status_locked()

    def extend(self, minutes):
        """A new deadline, `minutes` from now, without touching the tunnel (support stays connected)."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise SupportError("a session lasts %d to %d minutes" % (MIN_MINUTES, MAX_MINUTES))
        with self.lock:
            if not self.session or self._clock() >= self.session["until"]:
                raise SupportError("no session is running")
            seconds = minutes * 60
            if self._clock() + seconds - self.session["started"] > MAX_TOTAL_MINUTES * 60:
                raise SupportError("a session cannot last more than %d hours in all; start a new one" % (MAX_TOTAL_MINUTES // 60))
            if self._cancel:
                self._cancel()
            self.session["until"] = self._clock() + seconds
            self.session["until_epoch"] = int(self._now() + seconds)
            self._cancel = self._timer(seconds, self._expire)
            self.log("pvj-supportd: support session now ends in %d minutes" % minutes)
            return self.status_locked()

    def _expire(self):
        with self.lock:
            if self.session and self._clock() >= self.session["until"] - 1:
                self.teardown("the session time ran out")

    def status_locked(self):
        s = self.session
        if not s:
            return {"ok": True, "active": False}
        if self._clock() >= s["until"]:           # the timer is late or lost: the deadline still holds
            self.teardown("the session time ran out")
            return {"ok": True, "active": False}
        handshake, rx, tx = 0, 0, 0
        code, out = self._run(["wg", "show", IFACE, "dump"])
        if code == 0:
            lines = out.splitlines()
            if len(lines) >= 2:
                f = lines[1].split("\t")
                if len(f) >= 7:
                    try:
                        handshake, rx, tx = int(f[4]), int(f[5]), int(f[6])
                    except ValueError:
                        pass
        else:                                      # someone removed the interface under us: the session is over
            self.teardown("the tunnel interface disappeared")
            return {"ok": True, "active": False}
        return {"ok": True, "active": True, "until": s["until_epoch"], "address": s["address"], "network": s["network"],
                "endpoint": s["endpoint"], "last_handshake": handshake, "received": rx, "sent": tx}

    def handle(self, message):
        if not isinstance(message, dict):
            return {"ok": False, "error": "bad request"}
        cmd = message.get("cmd")
        try:
            if cmd == "status":
                with self.lock:
                    reply = self.status_locked()
                ok, why = self.available()
                reply["available"], reply["why"] = ok, why
                return reply
            if cmd == "start":
                return self.start(message)
            if cmd == "extend":
                return self.extend(message.get("minutes"))
            if cmd == "stop":
                with self.lock:
                    self.teardown("stopped from the panel")
                return {"ok": True, "active": False}
            if cmd == "key":
                ok, why = self.available()
                if not ok:
                    return {"ok": False, "error": why}
                with self.lock:
                    return {"ok": True, "public_key": self.public_key()}
        except SupportError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": "the helper could not do that: %s" % e}
        return {"ok": False, "error": "unknown command"}


def _start_timer(delay, fn):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t.cancel


class SupportdClient:
    """Used by the panel: one request, one reply."""

    def __init__(self, path, timeout=40):
        self.path, self.timeout = path, timeout

    def request(self, message):
        try:
            reply = json.loads(exchange(self.path, message, self.timeout))
        except socket.timeout:
            raise OSError("the remote support helper (pvj-supportd) did not answer in time")
        except (OSError, ValueError):
            raise OSError("the remote support helper (pvj-supportd) is not running")
        if not isinstance(reply, dict):
            raise OSError("the remote support helper (pvj-supportd) gave a bad answer")
        return reply


def main(argv=None):
    import grp
    import pwd
    rundir = paths.supportd_dir()
    keydir = os.environ.get("PVJ_SUPPORT_KEYS", "/var/lib/pvj-support")
    os.makedirs(rundir, exist_ok=True)
    allowed = {0}
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        pass
    service = SupportService(keydir, log=lambda m: print(m, flush=True))
    service.teardown("the helper started")      # fail closed: nothing from an earlier run stays open
    server = NetServer(os.path.join(rundir, paths.SUPPORTD_SOCKET), service, lambda uid: uid in allowed)
    try:
        os.chown(server.server_address, 0, grp.getgrnam("pvj").gr_gid, follow_symlinks=False)
    except (KeyError, OSError):
        pass
    print("pvj-supportd: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.teardown("the helper stopped")
    return 0
