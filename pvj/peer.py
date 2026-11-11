# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The peer channel (D132, design/node-handover_2026-11-09.md section 3.2 and slices 3 and 4a): how one node talks to another
it approves. Nothing here signs a device in (pvj/ticket.py does, over this channel); the lease and the poke (pvj/lease.py, slice 4b)
use it too. What is here:

* **The peer port (4a).** Calls between nodes go to a dedicated TLS listener (`PVJ_PEER_PORT`, 8443 on every node unless the
  discovery row says another), which asks the caller for a certificate (CERT_REQUIRED, the pinned root the only CA) and
  where the caller is proved by the handshake: its certificate chains to the root, has the clientAuth key usage, and the
  hash of its public key is the one the list or an acceptance gives for the id it names. The caller's own claim of a key
  proves nothing and is only compared with the handshake. The panel's port (443) no longer answers a peer call; it still
  gives the card, which an Owner reads before a node is accepted.

* **The checked connection.** To reach node B by its id the caller tries EVERY address its discovery list holds for that
  id (only private, link-local or loopback ones), one after the other, and uses the first whose certificate passes all of:
  it chains to the root this node pins (`openssl verify`, as for this node's own certificate); the handshake proved the
  peer holds the key; the certificate names the address that was connected to OR the `.local` name the node announced
  (an address changes with DHCP, and a certificate may name the host only; this check is belt and braces); the hash of
  its public key is the one the held list or an acceptance gives for that id (that is the real binding: a key that
  changed is refused, never trusted on first sight); and, only when this node's clock was set from the network, it has
  not run out. The root alone is never enough. A failed check writes an event.
* **The serial in every call.** Each call says which list serial the caller holds ("pairing" when it holds none) and
  carries the signed list it holds. The answerer takes a newer list through `Handover.take_list` (the root's signature,
  the floor and the fleet pin apply, so who the caller really is does not matter), and sends its own when the caller is
  behind. A peer that is behind and could not take the list is refused for that call; being ahead is not a failure.
* **Two budgets, never mixed.** Outbound: one call at a time per peer id, 3 seconds to connect and 6 in all, and this
  node's own failed calls count toward 10 a minute for the node and 5 for each target (address and port). Inbound:
  only the sender's address is charged (5 failures a minute), never the node, so nobody on the network can switch this
  node's calls off by sending it junk.
* **Removals are pushed and pulled (slice 4d).** When an Owner removes a node (or this node takes a removal from a peer) it tells
  every approved node it can reach, in a hello that carries the removals (key, name, epoch), from one background pusher at a time
  that waits for nobody and charges a failure to its target only, never to the node-wide count of outbound failures. A node that
  could not be reached is tried again later (every 5 minutes, from the lease worker) and also catches up by itself: its next hello
  to anybody carries a different digest, and the answer carries the removals. The receiver's allowance for pushes is its own,
  per caller key; a node taking a removal passes it on to its own approved peers. Only denies travel; "Allow again" is local and
  never sent (pvj/handover.py). A call
  whose caller cannot be proved (nothing authenticates the caller, only the callee) has no authority beyond making
  this node ask the caller back, over a checked connection, for its denies. Answers that carry the list or denies, and
  the pulls a source can start, are capped per source. A call from a node this node does not approve costs a lookup
  and a count: no outbound call and no log line.
* **Accepting a node by its fingerprint.** The key to accept comes from the peer's card, fetched over a connection that
  passed the checks above (except the key, which is not known yet) and that matches the certificate the peer showed. The
  fingerprint the Owner types is the Owner's own check and is compared with that key; the caller of the API never
  supplies the key.
"""
import collections
import contextlib
import datetime
import http.client
import json
import re
import ssl
import threading
import time

from . import fleetlist, httpsbox
from .fleet import local_enough
from .handover import WIRE_ENTRIES, HandoverError, fingerprint

DEFAULT_PEER_PORT = 8443        # the peer listener of every node, unless the discovery row names another (PVJ_PEER_PORT on this node)
CONNECT_SECONDS, TOTAL_SECONDS = 3.0, 6.0
NODE_FAILS, TARGET_FAILS, SOURCE_FAILS, FAIL_WINDOW = 10, 5, 5, 60.0
HEAVY_ANSWERS, ADOPT_TRIES, PULLS_PER_SOURCE = 2, 2, 2      # per source and minute: answers with the list or denies, lists tried, pulls started
PUSHES_PER_KEY = 12             # per approved caller's key and minute: pushes of removals that are read (its own allowance, not a failure count)
PUSH_RETRY = 300.0              # seconds before a node that could not be reached is tried again with the removals
MAX_PUSH_BODY = 60000           # bytes of a hello with removals; the signed list leaves it first when it would not fit the server's body limit
MAX_ANSWER = 262144
MAX_ADDRESSES = 4
PULL_GAP = 10.0                 # seconds between two pulls from the same node
MAX_SOURCES = 256
MAX_CARRIED = 30000             # a list or signature carried in a call, in characters: with quotes and line breaks escaped, a
                                # hello stays well under the server's 65536 byte body limit; a longer list is not carried
                                # (the answer path still brings it to a node that is behind)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")


class PeerError(Exception):
    def __init__(self, message, status=502, check=False, refused=False):
        super().__init__(message)
        self.message, self.status, self.check, self.refused = message, status, check, refused     # refused: the node answered and said no


class Throttle:
    """Failures in a sliding window. Only failures are counted; a good call costs nothing."""

    def __init__(self, limit, window, now):
        self.limit, self.window, self.now, self.hits = limit, window, now, collections.deque()

    def _prune(self):
        t = self.now()
        while self.hits and t - self.hits[0] > self.window:
            self.hits.popleft()

    def count(self):
        self._prune()
        self.hits.append(self.now())

    def over(self):
        self._prune()
        return len(self.hits) >= self.limit


class Buckets:
    """A bounded set of throttles by key; the one hit longest ago goes when it is full."""

    def __init__(self, limit, now, cap=MAX_SOURCES):
        self.limit, self.now, self.cap, self._map = limit, now, cap, collections.OrderedDict()

    def get(self, key):
        t = self._map.get(key)
        if t is None:
            t = self._map[key] = Throttle(self.limit, FAIL_WINDOW, self.now)
            while len(self._map) > self.cap:
                self._map.popitem(last=False)
        else:
            self._map.move_to_end(key)
        return t

    def over(self, key):
        t = self._map.get(key)
        return t is not None and t.over()


def _spawn(fn):
    threading.Thread(target=fn, name="fleet-pull", daemon=True).start()


def _spawn_push(fn):
    threading.Thread(target=fn, name="fleet-push", daemon=True).start()


class Peers:
    def __init__(self, handover, https, lookup, now=time.monotonic, wall=time.time, log=None, spawn=None, peer_port=DEFAULT_PEER_PORT, push_spawn=None):
        """handover: the Handover (who is approved, the list, the deny file). https: the HttpsBox (the pinned root, the clock).
        lookup(node_id) -> [(address, port, name, peer port)] from the discovery list (name and peer port may be left out; the
        port is the panel's, the peer port defaults to `peer_port`). spawn(fn): how a pull in the
        background is run. The lookup is the Nodes list itself, which starts a scan when its last one is old (fleet.py, rate
        limited), so a node that is not listed yet is found by the next call without a scan of this module's own."""
        self.handover, self.https, self.lookup = handover, https, lookup
        self.peer_port = peer_port
        self.now, self.wall = now, wall
        self.log = log or (lambda line: None)
        self.spawn = spawn or _spawn
        self.push_spawn = push_spawn or _spawn_push
        self.lock = threading.Lock()
        self._slots = {}
        self._out_node = Throttle(NODE_FAILS, FAIL_WINDOW, now)            # outbound only
        self._out_target = Buckets(TARGET_FAILS, now)                      # outbound only, by (address, port)
        self._in_fail = Buckets(SOURCE_FAILS, now)                         # inbound only, by the sender's address
        self._in_heavy = Buckets(HEAVY_ANSWERS, now)
        self._in_adopt = Buckets(ADOPT_TRIES, now)
        self._in_pull = Buckets(PULLS_PER_SOURCE, now)
        self._in_push = Buckets(PUSHES_PER_KEY, now)                       # inbound only, by the approved caller's key
        self._pushing = False                                              # one pusher at a time
        self._push_dirty = False
        self._push_force = False
        self._owed = {}                                                    # node id -> monotonic time it may be tried again
        self._peer_digest = {}                                             # node id -> the deny digest it last reported
        self._pulling = set()
        self._last_pull = {}

    # -- budgets --
    def _fail_out(self, target, node=True):
        """A failed outbound call. `node=False` (lease checks and pokes, slice 4b) charges the target alone: a node that
        hangs or keeps poking must not use up this node's budget for every other call."""
        with self.lock:
            if node:
                self._out_node.count()
            self._out_target.get(target).count()

    def _out_blocked(self, target, node=True):
        with self.lock:
            return (node and self._out_node.over()) or self._out_target.over(target)

    def _fail_in(self, source):
        with self.lock:
            self._in_fail.get(source).count()

    def _in_blocked(self, source):
        with self.lock:
            return self._in_fail.over(source)

    def _spend(self, buckets, source):
        """One use of a per-source allowance: False when it is used up (nothing is counted then)."""
        with self.lock:
            t = buckets.get(source)
            if t.over():
                return False
            t.count()
            return True

    @contextlib.contextmanager
    def _slot(self, node_id):
        """One outbound call at a time per peer id: a slow or dead node cannot hold up a call to another."""
        with self.lock:
            lock = self._slots.setdefault(node_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise PeerError("a call to that node is already running", 429)
        try:
            yield
        finally:
            lock.release()

    def _say(self, label, message):
        """A refused peer is an event (design 3.2): repeats collapse in the log."""
        self.handover.event("handover", "%s was not trusted: %s." % (label, message))

    # -- the checked connection (design 3.2, steps 1 to 6) --
    def _addresses(self, node_id):
        out, seen = [], set()
        for row in (self.lookup(node_id) or []):
            address, port = row[0], row[1]
            name = row[2] if len(row) > 2 and isinstance(row[2], str) and _NAME.fullmatch(row[2]) else None
            via = row[3] if len(row) > 3 and isinstance(row[3], int) and not isinstance(row[3], bool) and 1 <= row[3] <= 65535 else self.peer_port
            if (address, port) not in seen and local_enough(address):
                seen.add((address, port))
                out.append((address, port, name, via))
        return out[:MAX_ADDRESSES]

    def _open(self, address, port, keys, name, started, client=False, budget=True):
        """A connection to this address whose certificate passed every check, with the certificate's DER and key hash.
        `keys`: the key hashes that are acceptable, or None to take whatever key it shows (only to read a card). `client`:
        show this node's own certificate (the peer port asks for it)."""
        if not local_enough(address):
            raise PeerError("that address is not on a private network")
        if self._out_blocked((address, port), budget):
            raise PeerError("too many calls have failed lately: wait a minute", 429)
        left = TOTAL_SECONDS - (self.now() - started)
        if left <= 0:
            raise PeerError("the call took too long")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                 # the checks below are made by hand, against the pinned root
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if client and (self.https is None or not self.https.present_to_peer(ctx)):
            raise PeerError("this node has no certificate of its own to show another node")
        conn = http.client.HTTPSConnection(address, port, timeout=min(CONNECT_SECONDS, left), context=ctx)
        try:
            try:
                conn.connect()
                der = conn.sock.getpeercert(binary_form=True)
            except (OSError, ssl.SSLError, http.client.HTTPException) as e:
                raise PeerError("could not connect to %s: %s" % (address, e))
            if not der:
                raise PeerError("%s showed no certificate" % address, check=True)
            chains, info = self.https.chains_to_root(der) if self.https is not None else (False, None)
            if info is None:
                raise PeerError("%s did not show a certificate" % address, check=True)
            if not chains:
                raise PeerError("the certificate at %s is not from this node's root" % address, check=True)
            covers = httpsbox.name_matches(address, info["dns"], info["ips"]) \
                or (name is not None and httpsbox.name_matches(name + ".local", info["dns"], info["ips"]))
            if not covers:
                raise PeerError("the certificate at %s names neither that address nor %s" % (address, (name + ".local") if name else "the node's name"), check=True)
            try:
                key = fleetlist.key_hash(der)
            except fleetlist.FleetError:
                raise PeerError("the certificate at %s has no usable key" % address, check=True)
            if keys is not None and key not in keys:
                raise PeerError("the key at %s is not the one on the list or the acceptance for that node" % address, check=True)
            if self.https.clock_trusted() and info["not_after"] < int(self.wall()):
                ended = datetime.datetime.fromtimestamp(info["not_after"], datetime.timezone.utc).date().isoformat()
                raise PeerError("the certificate at %s ended on %s" % (address, ended), check=True)
            return conn, der, key
        except PeerError as e:
            conn.close()
            if e.status != 429:
                self._fail_out((address, port), budget)
            raise
        except BaseException:
            conn.close()
            raise

    def _http(self, conn, method, path, body, started):
        left = TOTAL_SECONDS - (self.now() - started)
        if left <= 0:
            raise PeerError("the call took too long")
        conn.sock.settimeout(left)
        headers = {"Accept": "application/json", "User-Agent": "pvj-peer"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers.update({"Content-Type": "application/json", "X-PVJ-Request": "1"})
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(MAX_ANSWER + 1)
        except (OSError, ssl.SSLError, http.client.HTTPException) as e:
            raise PeerError("the call failed: %s" % e)
        if len(raw) > MAX_ANSWER:
            raise PeerError("the answer was too long")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise PeerError("the answer was not JSON")
        if not isinstance(payload, dict):
            raise PeerError("the answer was not an object")
        if resp.status != 200:
            raise PeerError("the node refused (%d): %s" % (resp.status, str(payload.get("error", ""))[:120]), 502, refused=True)
        return payload

    def _through(self, node_id, keys, method, path, body, label, peer=False, count_refusals=True, budget=True):
        """Try each address in turn; the first connection that passes the checks carries the call. Returns (payload, key,
        target) where target is the (address, port) that answered. `peer`: the call goes to the peer port, showing this
        node's certificate; otherwise to the panel's own port (the card)."""
        addresses = self._addresses(node_id)
        if not addresses:
            raise PeerError("no address is known for that node: it is not on the network, or discovery is off")
        started, last = self.now(), None
        for address, port, name, via in addresses:
            if peer:
                port = via
            try:
                conn, der, key = self._open(address, port, keys, name, started, client=peer, budget=budget)
            except PeerError as e:
                last = e
                if e.check:
                    self._say(label, e.message)
                continue
            try:
                return self._http(conn, method, path, body, started), key, (address, port)
            except PeerError as e:
                if count_refusals or not e.refused:
                    self._fail_out((address, port), budget)
                raise
            finally:
                conn.close()
        raise last

    # -- calls between approved nodes --
    def preamble(self):
        h = self.handover
        node_id, _name = h.own()
        lst = h.list
        out = {"from": node_id, "key": h.own_key(), "serial": lst.serial if lst is not None else "pairing", "deny": h.deny_digest()}
        files = h.list_text()
        if files is not None and len(files[0]) <= MAX_CARRIED and len(files[1]) <= MAX_CARRIED:      # the signed list goes with the call
            out["list"], out["sig"] = files
        return out

    def request(self, node_id, path, body=None, budget=True):
        """A call to an approved node, with the serial, the list and the deny digest in it. Raises PeerError. A newer list the
        peer sends is taken (verified, with the floor) and the denies it reports are taken; a peer that holds an older list
        and could not take ours is refused for this call (being ahead is not a failure). `budget=False`: a failure is charged to the
        target only (the pusher of removals uses it)."""
        h = self.handover
        keys = h.expected_keys(node_id)
        if not keys:
            raise PeerError("that node is not approved", 403)
        with self._slot(node_id):
            data = dict(body or {}, **self.preamble())
            if len(json.dumps(data)) > MAX_PUSH_BODY:
                data.pop("list", None)
                data.pop("sig", None)
            payload, key, target = self._through(node_id, keys, "POST", path, data, h.name_of(node_id) or "a node", peer=True, budget=budget)
            self._after(node_id, key, payload, target, budget)
            return payload

    def _after(self, node_id, key, payload, target, budget=True):
        h = self.handover
        approved = h.approved(node_id, key)
        name = approved["name"] if approved else "a node"
        peer = payload.get("serial")
        own = h.list.serial if h.list is not None else None
        if not (peer == "pairing" or (isinstance(peer, int) and not isinstance(peer, bool) and peer >= 1)):
            self._fail_out(target, budget)
            raise PeerError("%s did not say which list it holds" % name)
        if isinstance(peer, int) and own is not None and peer < own:
            raise PeerError("%s holds an older list (serial %d, this node has %d) and could not take this node's" % (name, peer, own), 409)
        if isinstance(peer, int) and (own is None or peer > own):
            text, sig = payload.get("list"), payload.get("sig")
            if isinstance(text, str) and isinstance(sig, str):
                try:
                    h.take_list(text.encode("utf-8", "surrogatepass"), sig, source=name)
                except HandoverError as e:
                    if own is not None:                          # a node with a list that is told a better one that fails the checks
                        self._fail_out(target, budget)
                        raise PeerError("%s sent a list that was not taken: %s" % (name, e.message))
            elif own is not None:
                self._fail_out(target, budget)
                raise PeerError("%s holds a newer list but did not send it" % name)
            if key not in h.expected_keys(node_id):
                raise PeerError("this node no longer approves %s after the new list" % name, 403)
        digest = payload.get("deny")
        if isinstance(digest, str) and len(digest) == 16 and (node_id in self._peer_digest or len(self._peer_digest) < 64):
            self._peer_digest[node_id] = digest
        if h.take_denies(payload.get("denied"), name):
            self.spread()                                       # a removal taken here goes on to this node's other peers

    def hello(self, node_id):
        """Say hello to an approved node: this node's serial, list and deny digest go out, a newer list and new denies come back."""
        return self.request(node_id, "/api/fleet/peer", {})

    def sync(self):
        """Hello to every approved node, one after the other. Returns [{"name", "ok", "serial" or "error"}]."""
        out = []
        for node_id, name, _key in self.handover.approved_nodes():
            try:
                payload = self.hello(node_id)
                out.append({"id": node_id, "name": name, "ok": True, "serial": payload.get("serial")})
            except PeerError as e:
                out.append({"id": node_id, "name": name, "ok": False, "error": e.message})
        return out

    # -- pushing removals (slice 4d) --
    def spread(self, force=True):
        """Tell every approved node about the removals held now. Returns at once: one background pusher runs at a time, and a request
        made while it runs makes it go round once more. `force`: also nodes that could not be reached lately."""
        with self.lock:
            self._push_dirty = True
            self._push_force = self._push_force or force
            if self._pushing:
                return
            self._pushing = True
        try:
            self.push_spawn(self._push_run)
        except BaseException:
            with self.lock:
                self._pushing = False
            raise

    def _push_run(self):
        try:
            while True:
                with self.lock:
                    if not self._push_dirty:
                        self._pushing = False
                        return
                    self._push_dirty, force, self._push_force = False, self._push_force, False
                self.push_all(force)
        except BaseException:
            with self.lock:
                self._pushing = False
            raise

    def push_all(self, force=True):
        """One round: a hello with the removals to each approved node whose last reported digest differs from ours. A node that cannot
        be reached is owed the push and tried again after PUSH_RETRY. Returns [{"id", "name", "ok"}]."""
        h = self.handover
        out = []
        own = h.deny_digest()
        for node_id, name, _key in h.approved_nodes():
            with self.lock:
                skip = self._peer_digest.get(node_id) == own or (not force and self._owed.get(node_id, 0.0) > self.now())
            if skip:
                continue
            try:
                self.request(node_id, "/api/fleet/peer", {"denied": h.deny_wire(WIRE_ENTRIES)}, budget=False)
                with self.lock:
                    self._owed.pop(node_id, None)
                out.append({"id": node_id, "name": name, "ok": True})
            except PeerError as e:
                with self.lock:
                    self._owed[node_id] = self.now() + PUSH_RETRY
                out.append({"id": node_id, "name": name, "ok": False, "error": e.message})
        return out

    def retry_owed(self):
        """From the lease worker: nodes that could not be reached with the removals are tried again when their time has come."""
        t = self.now()
        with self.lock:
            due = any(v <= t for v in self._owed.values()) and not self._pushing
        if due:
            self.spread(force=False)

    def call_once(self, node_id, path, body, budget=True):
        """One POST to the peer port of an approved node, with the checks of 3.2 and nothing else added to the body, made once: a
        call that fails after it was sent is not tried again at another address (a ticket may be spent). A refusal the node
        answers is not counted as a failed call. Returns (payload, the key the node showed). Raises PeerError."""
        h = self.handover
        keys = h.expected_keys(node_id)
        if not keys:
            raise PeerError("that node is not approved", 403)
        with self._slot(node_id):
            payload, key, _target = self._through(node_id, keys, "POST", path, dict(body or {}), h.name_of(node_id) or "a node", peer=True, count_refusals=False, budget=budget)
        return payload, key

    def panel_address(self, node_id):
        """Where a browser may be sent to reach this node's panel (design 5, step 2): (host, port, key) of the first address
        whose certificate passes every check of 3.2 AND covers the host that is returned (the address, or the .local name
        the node announced), so the browser's own check passes there too. Raises PeerError."""
        h = self.handover
        keys = h.expected_keys(node_id)
        if not keys:
            raise PeerError("that node is not approved", 403)
        addresses = self._addresses(node_id)
        if not addresses:
            raise PeerError("no address is known for that node: it is not on the network, or discovery is off")
        started, last, label = self.now(), None, h.name_of(node_id) or "a node"
        for address, port, name, _via in addresses:
            try:
                conn, der, key = self._open(address, port, keys, name, started)
            except PeerError as e:
                last = e
                if e.check:
                    self._say(label, e.message)
                continue
            conn.close()
            info = httpsbox.read_certificate(der)
            if httpsbox.name_matches(address, info["dns"], info["ips"]):
                host = address
            elif name is not None and httpsbox.name_matches(name + ".local", info["dns"], info["ips"]):
                host = name + ".local"
            else:
                last = PeerError("the certificate at %s does not cover an address a browser could use" % address)
                continue
            return ("[%s]" % host if ":" in host else host), port, key
        raise last

    # -- the other end of a call: what a node answers (POST /api/fleet/peer) --
    def authenticate(self, der, source):
        """Who is calling, from the certificate the handshake on the peer port showed (the handshake has checked the chain to
        the pinned root and that the caller holds the key): the hash of its public key. The certificate must have the clientAuth
        key usage and, only while this node's clock is trusted, must not have run out. Raises PeerError (403, or 429 when the
        sender's address has failed too often). A failure is charged to the sender's address only."""
        if self._in_blocked(source):
            raise PeerError("too many calls have failed lately: wait a minute", 429)
        try:
            info = httpsbox.read_certificate(der)
            key = fleetlist.key_hash(der)
        except (ValueError, IndexError, OverflowError, UnicodeDecodeError, TypeError, fleetlist.FleetError):
            self._fail_in(source)
            raise PeerError("not a usable certificate", 403)
        if not info["client_auth"]:
            self._fail_in(source)
            raise PeerError("that certificate is not for a client: it was signed before nodes called each other and must be signed again", 403)
        if self.https is not None and self.https.clock_trusted():
            if info["not_after"] < int(self.wall()):
                self._fail_in(source)
                raise PeerError("that certificate has run out", 403)
            if info["not_before"] > int(self.wall()):
                self._fail_in(source)
                raise PeerError("that certificate is not valid yet", 403)
        return key

    def handle_peer(self, body, source, peer_key):
        """Answer a peer's hello. `peer_key` is the hash of the key in the certificate the caller showed in the handshake
        (`authenticate`): the caller is proved, not claimed, and its claim of a key must equal it. A node this one does not
        approve gets nothing. An approved one gets what an approved node may hold: it may carry a newer signed list (the
        root's signature decides, not the caller), is given the list if it is behind and the denies if its digest differs
        (each capped per source), and is asked back for its denies. Only the sender's address is ever charged for a bad call."""
        if self._in_blocked(source):
            raise PeerError("too many calls have failed lately: wait a minute", 429)
        h = self.handover
        need = {"from", "key", "serial", "deny"}
        ok = isinstance(body, dict) and need <= set(body) <= need | {"list", "sig", "denied"}
        if ok:
            serial = body["serial"]
            ok = isinstance(body["from"], str) and fleetlist.ID_WORD.fullmatch(body["from"]) and isinstance(body["key"], str) \
                and fleetlist.KEY_WORD.fullmatch(body["key"]) and isinstance(body["deny"], str) and len(body["deny"]) == 16 \
                and (serial == "pairing" or (isinstance(serial, int) and not isinstance(serial, bool) and serial >= 1)) \
                and all(isinstance(body.get(k, ""), str) and len(body.get(k, "")) <= MAX_CARRIED for k in ("list", "sig")) \
                and (not "denied" in body or (isinstance(body["denied"], list) and len(body["denied"]) <= WIRE_ENTRIES))
        if not ok:
            self._fail_in(source)
            raise PeerError("not a peer call", 400)
        approved = h.approved(body["from"], peer_key) if body["key"] == peer_key else None
        if approved is None:
            h.count_unknown()                                 # a count on the status, no log line, no outbound call
            self._fail_in(source)
            raise PeerError("not approved", 403)
        lst = h.list
        theirs = body["serial"]
        if isinstance(theirs, int) and "list" in body and "sig" in body and (lst is None or theirs > lst.serial) \
                and self._spend(self._in_adopt, source):
            try:
                h.take_list(body["list"].encode("utf-8", "surrogatepass"), body["sig"], source=approved["name"])
            except HandoverError:
                self._fail_in(source)                          # a list that fails the checks is a bad call, charged to its sender
            lst = h.list
        if "denied" in body and body["denied"] and self._spend(self._in_push, peer_key):
            if h.take_denies(body["denied"], approved["name"]):
                self.spread()                                 # a removal taken here goes on to this node's other peers
        with self.lock:
            if body["from"] in self._peer_digest or len(self._peer_digest) < 64:
                self._peer_digest[body["from"]] = body["deny"]  # what the caller held when it called (it may take ours in the answer)
        answer = {"v": 1, "serial": lst.serial if lst is not None else "pairing", "deny": h.deny_digest()}
        files = h.list_text()
        wants_list = files is not None and (theirs == "pairing" or theirs < lst.serial)
        different = body["deny"] != answer["deny"]
        if (wants_list or different) and self._spend(self._in_heavy, source):
            if wants_list:
                answer["list"], answer["sig"] = files
            if different:
                answer["denied"] = h.deny_wire()
        if different and self._spend(self._in_pull, source):
            self._pull_later(body["from"])
        return answer

    def _pull_later(self, node_id):
        t = self.now()
        with self.lock:
            if node_id in self._pulling or t - self._last_pull.get(node_id, -PULL_GAP) < PULL_GAP:
                return
            self._pulling.add(node_id)
            self._last_pull[node_id] = t

        def run():
            try:
                self.hello(node_id)
            except PeerError as e:
                self.log("pvj-web: asking a node for its removals failed: %s" % e.message)
            finally:
                with self.lock:
                    self._pulling.discard(node_id)
        try:
            self.spawn(run)
        except BaseException:
            with self.lock:
                self._pulling.discard(node_id)
            raise

    # -- accepting a node by its fingerprint (design 3.4) --
    def card(self, node_id):
        """The card of a node, fetched over a connection that passed the checks (except the key, not known yet) and
        confirmed against the certificate that node showed. Returns {"id", "name", "key"}."""
        if not isinstance(node_id, str) or not fleetlist.ID_WORD.fullmatch(node_id):
            raise PeerError("not a node id", 400)
        with self._slot(node_id):
            payload, key, target = self._through(node_id, None, "GET", "/api/fleet/card", None, "a node")
        try:
            card = fleetlist.parse_card(json.dumps(payload).encode("utf-8"))
        except fleetlist.FleetError as e:
            self._fail_out(target)
            raise PeerError("that node's card is not usable: %s" % e.message)
        if card["id"] != node_id or card["key"] != key:
            self._fail_out(target)
            raise PeerError("that node's card does not match the node or the certificate it showed")
        return card

    def look(self, node_id):
        """What the Owner reads to compare with the other node's own panel: its name and the fingerprint of its key."""
        card = self.card(node_id)
        return {"id": card["id"], "name": card["name"], "fingerprint": fingerprint(card["key"])}

    def accept(self, node_id, carry, typed, who=None):
        """Accept a node: the key comes from its card over the checked connection, never from the caller; `typed` is the
        fingerprint the Owner read on the other node's panel and is compared with that key."""
        card = self.card(node_id)
        return self.handover.accept_node(card["id"], card["name"], card["key"], carry, typed, who)
