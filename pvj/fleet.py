# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Nodes that find each other on the local network (D127, pvj/FLEET.md).

Two halves, both standard library only:

* Announcing. Each node says `_nxlx._tcp` by mDNS, with TXT records for its id, name, tier and version. The panel
  does not speak mDNS itself: it asks the root helper (pvj-sysd) to write one static avahi service file, which
  avahi-daemon (already on Raspberry Pi OS) serves and refreshes. A change of name, version, port or the switch rewrites
  that file; nothing runs between changes. Without avahi the node works as before and says so (state `no-avahi`).
* Listing. `Browser` sends one multicast DNS question for `_nxlx._tcp.local` from an ordinary UDP port (a "legacy
  unicast" question, which avahi answers directly to the sender, so no socket on port 5353 is needed and avahi-utils
  need not be installed), waits about a second and a half, and keeps the answer for ten seconds. It runs in its own
  thread, on request only; the request that asked never waits more than a couple of seconds, and only the first time.

What arrives from the network is a hint from a stranger until proven otherwise: every field is checked and cut, the
sender must be a private, link-local or loopback address, packets and names are bounded, and a malformed record is
dropped. The list is never a credential: opening a node still needs that node's pairing.
"""

import ipaddress
import os
import re
import secrets
import select
import socket
import struct
import threading
import time

SERVICE = ("_nxlx", "_tcp", "local")
SERVICE_TYPE = "_nxlx._tcp"
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
TIERS = ("lean", "standard", "full", "unknown")
ID_FILE = "node-id"
CACHE_SECONDS = 10.0          # a listing is reused this long
KEEP_SECONDS = 35.0           # a node missed by one or two scans stays listed; after this long unseen it goes
FIRST_WAIT = 2.5              # the first request, with nothing known, waits at most this long for the first scan
SCAN_SECONDS = 1.2            # how long the first question is listened for; follow-up questions get half of it
MAX_ROWS = 64
MAX_PACKET = 9000
MAX_PACKETS = 200
MAX_RECORDS = 200
MAX_JUMPS = 10
HOST_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
ID_WORD = re.compile(r"[0-9a-f]{32}")
VERSION_WORD = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,19}")
RETRY_SECONDS = 60.0          # while on, the helper is asked again this often: avahi may have been installed, stopped or the file deleted

# What each state of the announcement says, in words for the Operator. No path, no system error text: the raw reason
# goes to the node's log only.
STATES = {
    "announcing": "This node tells the local network its name, model and version.",
    "off": "Discovery is switched off on this node: it announces nothing and does not look for other nodes.",
    "no-avahi": "avahi-daemon is not installed on this node, so it cannot announce itself. It still works, and it can still list other nodes.",
    "avahi-stopped": "avahi-daemon is installed but not running, so other nodes cannot see this one yet.",
    "no-helper": "The system helper (pvj-sysd) is not running, so this node cannot announce itself.",
    "error": "The announcement could not be written; the node's log says why. It tries again every minute.",
}


SHORT = {"no-avahi": "avahi-daemon is not installed", "avahi-stopped": "avahi-daemon is not running",
         "no-helper": "the system helper is not running", "error": "the announcement could not be written"}
_UNSET = object()


class FleetError(Exception):
    pass


def stored_on(section):
    """The Owner's switch as the node reads it: on when never set (the default), as set when it is true or false, and off
    when the value is damaged (fails closed, like the other switches)."""
    if section is None or (isinstance(section, dict) and "discovery" not in section):
        return True
    return isinstance(section, dict) and section.get("discovery") is True


# --- the node's own id ------------------------------------------------------------------------------------------------
def node_id(state_dir):
    """The node's stable id: 32 hex digits, made once from the system's random source and kept in `node-id` in the state
    folder (/var/lib/pvj), so it survives a restart, a rename and an update. It is made on first use, never at build, so
    a card imaged from a master copy gets its own. It is not a secret and not a credential (it is announced to the
    whole network); a node on the read-only root cannot keep it, and then has a new one at each start. Returns
    (id, kept)."""
    path = os.path.join(state_dir, ID_FILE)
    try:
        with open(path, "r", encoding="ascii") as f:
            text = f.read(64).strip()
        if ID_WORD.fullmatch(text):
            return text, True
    except (OSError, UnicodeDecodeError):
        pass
    fresh = secrets.token_hex(16)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(fresh + "\n")
        os.replace(tmp, path)
        return fresh, True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return fresh, False


def clean_hostname(raw):
    """The name as the panel shows it (the node's host name), made safe for the announcement."""
    text = re.sub(r"[^A-Za-z0-9._-]", "", (raw or "").split(".")[0])[:63].lstrip("._-")
    return text if HOST_WORD.fullmatch(text or "") else "node"


# --- reading the network's answer -------------------------------------------------------------------------------------
class Bad(Exception):
    """A packet or record that does not parse. Always caught: the packet is dropped, nothing else is."""


def _name(data, pos):
    """The name at `pos` as a tuple of lower-case labels, and the offset after it. Compression pointers go backwards
    only and at most MAX_JUMPS are followed, so a hostile packet cannot loop or read outside itself."""
    labels, end, jumps, total = [], None, 0, 0
    while True:
        if pos >= len(data):
            raise Bad("name runs off the packet")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise Bad("pointer runs off the packet")
            target = ((n & 0x3F) << 8) | data[pos + 1]
            if end is None:
                end = pos + 2
            jumps += 1
            if jumps > MAX_JUMPS or target >= pos:
                raise Bad("bad pointer")
            pos = target
            continue
        if n & 0xC0:
            raise Bad("reserved label type")
        if pos + 1 + n > len(data):
            raise Bad("label runs off the packet")
        total += n + 1
        if total > 255:
            raise Bad("name too long")
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace").lower())
        pos += 1 + n
    return tuple(labels), (pos if end is None else end)


def encode_name(labels):
    out = b""
    for label in labels:
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise FleetError("bad label")
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def build_query(labels, qtype):
    """One multicast DNS question (id 0, one question, class IN, no unicast-response bit: the source port is not 5353,
    which is what makes the answer come back to us alone)."""
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + encode_name(labels) + struct.pack(">HH", qtype, 1)


def parse(packet):
    """The records of one answer packet as {"ptr": [(instance, ttl)], "srv": {instance: port}, "txt": {instance: {k: v}}}.
    Raises Bad for a packet that is not an answer or does not parse."""
    if len(packet) > MAX_PACKET or len(packet) < 12:
        raise Bad("size")
    _id, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", packet[:12])
    if not flags & 0x8000 or qd > 20 or an + ns + ar > MAX_RECORDS:
        raise Bad("not an answer, or too many records")
    pos = 12
    for _ in range(qd):
        _n, pos = _name(packet, pos)
        pos += 4
    out = {"ptr": [], "srv": {}, "txt": {}}
    for _ in range(an + ns + ar):
        owner, pos = _name(packet, pos)
        if pos + 10 > len(packet):
            raise Bad("record header")
        rtype, _cls, ttl, rdlen = struct.unpack(">HHIH", packet[pos:pos + 10])
        pos += 10
        end = pos + rdlen
        if end > len(packet):
            raise Bad("record data")
        if rtype == 12 and owner == SERVICE:
            target, _ = _name(packet[:end], pos)
            out["ptr"].append((target, ttl))
        elif rtype == 33 and owner[1:3] == SERVICE[:2] and rdlen >= 7:
            out["srv"][owner] = struct.unpack(">H", packet[pos + 4:pos + 6])[0]
        elif rtype == 16 and owner[1:3] == SERVICE[:2]:
            out["txt"][owner] = _txt(packet[pos:end])
        pos = end
    return out


def _txt(data):
    items, pos = {}, 0
    while pos < len(data) and len(items) < 16:
        n = data[pos]
        text = data[pos + 1:pos + 1 + n].decode("utf-8", "replace")
        pos += 1 + n
        key, eq, value = text.partition("=")
        if eq and key and key.lower() not in items:
            items[key.lower()] = value
    return items


def _printable(text, limit):
    return "".join(c for c in text if c.isprintable())[:limit].strip()


def row_from(instance, port, txt, address):
    """One listed node from what the network said, or None when it is not a usable record. Everything is checked."""
    ident = (txt.get("id") or "").lower()
    if not ID_WORD.fullmatch(ident) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    version = txt.get("version", "")
    tier = txt.get("tier", "")
    return {"id": ident, "name": _printable(txt.get("name", ""), 63) or _printable(instance[0], 63) or "node",
            "address": address, "port": port, "tier": tier if tier in TIERS else "unknown",
            "version": version if VERSION_WORD.fullmatch(version) else "", "https": txt.get("https") == "1"}


def local_enough(address):
    """Only an address on a private, link-local or loopback range may tell us about a node."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and (ip.is_private or ip.is_link_local or ip.is_loopback)


def scan(transport, window=SCAN_SECONDS):
    """Ask the network and return the rows found, in no order. `transport(queries, seconds)` sends the questions and
    returns [(source address, packet)] heard in that time (net_transport is the real one). The first question is
    "who is `_nxlx._tcp`"; avahi puts the service and text records in the same answer, and when a node's did not fit,
    one more question per such node asks for them by name."""
    instances, srv, txt = {}, {}, {}

    def take(heard):
        for address, packet in heard[:MAX_PACKETS]:
            if not local_enough(address):
                continue
            try:
                got = parse(packet)
            except (Bad, struct.error):
                continue
            for target, ttl in got["ptr"]:
                if ttl == 0:
                    instances.pop(target, None)         # a goodbye
                elif len(instances) < MAX_ROWS * 2:
                    instances.setdefault(target, address)
            for owner, port in got["srv"].items():
                if len(srv) < MAX_ROWS * 2:
                    srv.setdefault(owner, (port, address))
            for owner, items in got["txt"].items():
                if len(txt) < MAX_ROWS * 2:
                    txt.setdefault(owner, items)

    take(transport([build_query(SERVICE, 12)], window))
    follow = []
    for i in [i for i in instances if i not in srv or i not in txt][:16]:
        try:
            follow.append(build_query(i, 255))
        except FleetError:                       # a name that cannot be asked for again (a label that grows when re-encoded): that node is skipped
            instances.pop(i, None)
    if follow:
        take(transport(follow, window / 2))
    rows = {}
    for instance, address in instances.items():
        if instance in srv and instance in txt:
            port, srv_address = srv[instance]
            row = row_from(instance, port, txt[instance], srv_address)
            if row is not None:
                rows[(row["id"], row["address"], row["port"])] = row
    return list(rows.values())[:MAX_ROWS]


def net_transport(addresses, clock=time.monotonic):
    """The real transport: one UDP socket per address of this node (so every network it is on is asked), the question
    sent twice a third of the window apart in case the first is lost, answers read until the window ends. Sockets are
    closed on the way out. Nothing here binds port 5353 or joins the multicast group."""
    def run(queries, seconds):
        socks = []
        try:
            for local in (addresses() or [""])[:4]:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                socks.append(s)
                s.setblocking(False)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
                if local:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
                    s.bind((local, 0))
            heard, start = [], clock()
            sent = 0
            while True:
                now = clock()
                if now - start >= seconds:
                    break
                if sent < 2 and now - start >= sent * seconds / 3:
                    sent += 1
                    for s in socks:
                        for q in queries:
                            try:
                                s.sendto(q, (MDNS_GROUP, MDNS_PORT))
                            except OSError:
                                pass
                ready, _, _ = select.select(socks, [], [], 0.05) if socks else ([], [], [])
                for s in ready:
                    for _ in range(50):
                        try:
                            data, (host, _port) = s.recvfrom(MAX_PACKET + 1)
                        except OSError:
                            break
                        heard.append((host, data))
                if len(heard) >= MAX_PACKETS:
                    break
                if not socks:
                    break
            return heard
        finally:
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass
    return run


# --- the list, cached ------------------------------------------------------------------------------------------------
class Browser:
    """Other nodes on the network, from `scan` at most every CACHE_SECONDS, in a thread of its own."""

    def __init__(self, scanner, own_id, now=time.monotonic, log=print):
        self._scan, self._own, self._now, self._log = scanner, own_id, now, log
        self._lock = threading.Lock()
        self._seen = {}              # (id, address, port) -> (row, time seen)
        self._started = None         # when the last scan began
        self._running = False
        self._done = threading.Event()
        self._error = None
        self._scanned = None

    def _work(self):
        try:
            rows, err = self._scan(), None
        except Exception as e:                    # the network, a socket, a hostile packet: never past this thread
            rows, err = [], "%s" % (e,)
        t = self._now()
        with self._lock:
            for row in rows:
                if row["id"] != self._own:
                    self._seen[(row["id"], row["address"], row["port"])] = (row, t)
            for key in [k for k, (_r, at) in self._seen.items() if t - at > KEEP_SECONDS]:
                del self._seen[key]
            while len(self._seen) > MAX_ROWS:
                del self._seen[min(self._seen, key=lambda k: self._seen[k][1])]
            if err and err != self._error:
                self._log("pvj-web: looking for other nodes failed: %s" % err)
            self._error, self._scanned, self._running = err, t, False
        self._done.set()

    def _maybe_start(self):
        t = self._now()
        with self._lock:
            if self._running or (self._started is not None and t - self._started < CACHE_SECONDS):
                return
            self._running, self._started = True, t
            self._done.clear()
        try:
            threading.Thread(target=self._work, name="fleet-scan", daemon=True).start()
        except BaseException:                    # no thread to give: the next request may try again
            with self._lock:
                self._running = False
            raise

    def known(self):
        """The rows seen so far, without asking the network: no scan is started and nothing waits. For the page's own policy,
        which is made at every load of the panel and must never be a reason to look (D127: only a press looks)."""
        with self._lock:
            return [dict(row) for row, _at in sorted(self._seen.values(), key=lambda v: (v[0]["name"].lower(), v[0]["id"]))]

    def nodes(self, wait=FIRST_WAIT):
        """{"nodes": [...], "scanning": bool, "error": None | text}. Asking starts a scan when the last one is older than
        CACHE_SECONDS; the caller gets what is known now. Only before the first scan has finished does it wait (at most
        `wait` seconds) so that the first look is not empty."""
        self._maybe_start()
        with self._lock:
            first = self._scanned is None
        if first:
            self._done.wait(wait)
        t = self._now()
        with self._lock:
            rows = []
            for row, at in sorted(self._seen.values(), key=lambda v: (v[0]["name"].lower(), v[0]["id"])):
                rows.append(dict(row, seen_seconds_ago=max(0, int(t - at))))
            return {"nodes": rows, "scanning": self._running, "error": "the network could not be asked" if self._error else None}


# --- the announcement ------------------------------------------------------------------------------------------------
class Fleet:
    """What the API and the server loop use. `describe()` gives the node's own facts, `on()` whether discovery is
    switched on, `sysd` the helper's client (request(dict) -> dict)."""

    def __init__(self, describe, on, sysd, scanner, log=print, event=None, now=time.monotonic):
        self._describe, self._on, self.sysd = describe, on, sysd
        self._log, self._event, self._now = log, event or (lambda *_: None), now
        self.browser = Browser(scanner, describe()["id"], now=now, log=log)
        self.state = "off"
        self._sent = _UNSET          # the record last written (None: withdrawn), to tell a change
        self._tried = None
        self._last_why = None
        self._lock = threading.Lock()

    def _why(self, text):
        """The reason an attempt failed, in the log once per change of reason: the attempt is repeated every minute."""
        if text != self._last_why:
            self._last_why = text
            self._log("pvj-web: " + text)

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self._log("pvj-web: discovery of other nodes: %s" % state)
            if state in SHORT:
                self._event("network", "Other nodes cannot see this one: %s." % SHORT[state])

    def tick(self, force=False):
        """Make the announcement match the node: called at start, then every few seconds. Nothing is sent unless the
        record or the switch changed, or an attempt that failed (or found no avahi) is due again. Never raises."""
        try:
            with self._lock:
                self._tick(force)
        except Exception as e:
            self._log("pvj-web: announcing this node failed: %s" % e)
            self._set_state("error")

    def _tick(self, force):
        d = self._describe()
        want = None
        if self._on():
            want = {"id": d["id"], "name": d["name"], "tier": d["tier"], "version": d["version"], "port": d["port"], "https": d["https"]}
        t = self._now()
        due = want is not None and self._tried is not None and t - self._tried >= RETRY_SECONDS      # the helper skips a file that is right, and rewrites one that was deleted
        if not (force or due or want != self._sent or self._tried is None):
            return
        self._tried = t
        if self.sysd is None:
            self._sent = _UNSET
            self._set_state("off" if want is None else "no-helper")
            return
        try:
            reply = self.sysd.request(dict(want, cmd="fleet_announce") if want else {"cmd": "fleet_withdraw"})
        except OSError as e:
            self._why("the system helper did not take the announcement: %s" % e)
            self._sent = _UNSET
            self._set_state("no-helper")
            return
        if not reply.get("ok"):
            self._why("the announcement was refused: %s" % reply.get("error"))
            self._sent = _UNSET
            self._set_state("error")
            return
        self._sent, self._last_why = want, None
        if want is None:
            self._set_state("off")
        else:
            self._set_state({"missing": "no-avahi", "stopped": "avahi-stopped"}.get(reply.get("avahi"), "announcing"))
    def status(self):
        return {"on": bool(self._on()), "state": self.state, "detail": STATES.get(self.state, "")}

    def known(self):
        """The rows already seen while discovery is on, [] while it is off. Never starts a scan (Browser.known)."""
        return self.browser.known() if self._on() else []

    def view(self):
        """The reply of GET /api/nodes."""
        d = self._describe()
        on = bool(self._on())
        out = {"self": {k: d[k] for k in ("id", "name", "tier", "version")}, "discovery": self.status()}
        if on:
            out.update(self.browser.nodes())
        else:
            out.update({"nodes": [], "scanning": False, "error": None})
        return out
