# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The lease on a carried device (D132, design/node-handover_2026-11-09.md section 8 and slice 4b): how long a device that
node A handed to node B stays signed in at B, and what ends it.

A device carried to B is a record of B's own (`via: node`). When A redeems the ticket it also gives B a **lease handle**
(128 random bits). A keeps only the hash of it, with the device record, and B keeps the handle with its record, so both
survive a restart. The handle is a secret between the two nodes: it is never in a log line, an event, an answer to a
panel or a status.

* **The check (B asks A).** About every ten minutes B asks A over the peer port, in one call for all the devices it holds
  from A: "is this device still paired, and with what role?". A good answer renews the lease. "Gone" signs the device out
  of B. A lower role (or a lower carry at B) lowers the record; a higher role is ignored (a carried role is never raised,
  `Auth.apply_node_updates` enforces it, in one save for all the devices of a check). An answer that is not understood, a refusal and a node that does not answer renew
  nothing.
* **The poke (A tells B).** When A removes a device it had lent, it sends each node it lent it to a poke over the peer port.
  The poke has no authority: B reacts only by asking A, so a forged poke can cause one extra question. It is limited by
  its own allowances (per caller key for an approved node, per source address for anything else), never by a count that
  an outsider can fill, and it never touches the throttles of tickets, redeem or the peer channel. A poke from a node with
  nothing lent costs a lookup and a counter.
* **The lapse.** A carried device stays signed in until 24 hours after its home node last answered (`LEASE_SECONDS`), then
  it is signed out, whether the home node is switched off, unreachable or refuses. The time is measured by the wall clock
  only while it is trusted (`HttpsBox.clock_trusted()`), otherwise by the monotonic clock since boot. A record is stamped
  with both and the boot it was stamped in (an empty boot id cannot tell boots apart). After a restart on an untrusted clock
  the age cannot be told; for the first `CLOCK_GRACE` seconds the record is neither lapsed nor renewed (the clock may be set
  from the network, and a home node that is up renews at the first tick). If the home node has not answered by then, the
  device is signed out. The stamp is never rewritten without an answer from the home node (design section 20).
* **The sweep (no network).** Every tick, and at once when the list, an acceptance or the deny list changes: if the "Accept
  devices from other nodes" switch is off every carried device is signed out; every device from a node that is no longer
  approved (a deny always wins) is signed out; a device whose role is above the carry now is lowered; a lapsed device is
  signed out.

Nothing here raises a role, signs anybody in or calls anything but the nodes this node approves.
"""
import hashlib
import re
import secrets
import threading
import time

from . import fleetlist, peer as peer_mod
from .auth import NODE_DEVICES_PER_SOURCE, ROLES
from .handover import carried_role
from .peer import PeerError

LEASE_SECONDS = 24 * 3600
CHECK_EVERY = 600.0             # B asks A about the devices it holds from A this often
TICK_EVERY = 30.0               # the worker wakes this often
CLOCK_GRACE = 600.0             # after a restart on a clock that is not trusted: seconds of uptime to wait before the age starts again
POKE_GAP = 10.0                 # B: seconds between two checks that pokes started for one node
OUT_POKE_GAP = 3.0              # A: seconds between two pokes to one node
POKE_CHECKS_PER_MINUTE = 2      # B: checks that pokes may start in a minute, for all nodes together (the rest wait for the tick)
OUT_POKES_PER_MINUTE = 20       # A: pokes this node sends in a minute, for all nodes together (a removal of every device waits its turn)
POKES_PER_MINUTE = 12        # B: pokes one approved node may send in a minute
LEASE_CALLS_PER_MINUTE = 30     # A: lease questions one approved node may ask in a minute
STRANGER_FAILS = 5              # a call from a node this one does not approve: failures a minute, per source address
MAX_HANDLES = NODE_DEVICES_PER_SOURCE
NAMES = {"live": "Operator", "full": "Owner"}
_HANDLE = re.compile(r"[0-9a-f]{32}")


class LeaseError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def _hash(handle):
    return hashlib.sha256(handle.encode("ascii", "replace")).hexdigest()


def _rank(role):
    return ROLES.get(role, 0)


def _spawn(fn):
    threading.Thread(target=fn, name="lease-check", daemon=True).start()


class Leases:
    def __init__(self, handover, peers, auth, https, event=None, log=None, now=time.monotonic, wall=time.time, boot=None, spawn=None):
        """handover: the Handover (who is approved, the switch). peers: the Peers (the checked connection). auth: the Auth (the
        device records). https: the HttpsBox (`clock_trusted`). boot(): the id of this boot (a different one means the monotonic
        clock started again). spawn(fn): how work started by a poke is run."""
        self.handover, self.peers, self.auth, self.https = handover, peers, auth, https
        self.event = event or (lambda kind, text, who=None: None)
        self.log = log or (lambda line: None)
        self.now, self.wall = now, wall
        self.boot = boot or (lambda: "")
        self.spawn = spawn or _spawn
        self.lock = threading.Lock()
        self.started = now()
        self.unknown_pokes = 0                              # pokes from an approved node that had nothing lent here (a count, no line)
        self._next_check = {}                               # B: node id -> monotonic time its devices are asked about next
        self._down = set()                                  # B: nodes that did not answer the last check (said once)
        self._dirty = set()                                 # B: nodes a poke wanted checked while the gap had not passed
        self._last_poked_check = {}                         # B: node id -> when a poke last started a check
        self._out_dirty = set()                             # A: nodes to poke
        self._last_out = {}                                 # A: node id -> when it was last poked
        # Allowances of their own. None of them is a node-wide count, and none is the throttle of tickets, redeem or the peer
        # channel: whoever is on the network can only fill the bucket of its own address (or, if approved, of its own key).
        self._poke_allow = peer_mod.Buckets(POKES_PER_MINUTE, now)       # by the approved caller's key
        self._lease_allow = peer_mod.Buckets(LEASE_CALLS_PER_MINUTE, now)  # by the approved caller's key
        self._stranger = peer_mod.Buckets(STRANGER_FAILS, now)           # failures of callers this node does not approve, by address
        self._poke_checks = peer_mod.Throttle(POKE_CHECKS_PER_MINUTE, 60.0, now)    # B: checks that pokes started, all nodes together
        self._poke_sent = peer_mod.Throttle(OUT_POKES_PER_MINUTE, 60.0, now)       # A: pokes sent, all nodes together
        self._renewed = set()                               # B: record ids a home node answered for in THIS process (their stamp is good even without a boot id)
        self._stop = None
        self._thread = None

    # ------------------------------------------------------------------------------------------------------------------
    # Allowances
    # ------------------------------------------------------------------------------------------------------------------
    def _spend(self, buckets, key):
        with self.lock:
            t = buckets.get(key)
            if t.over():
                return False
            t.count()
            return True

    def _stranger_over(self, source):
        with self.lock:
            return self._stranger.over(source)

    def _stranger_hit(self, source):
        with self.lock:
            self._stranger.get(source).count()

    def _refuse_stranger(self, source):
        self.handover.count_unknown()                       # a count on the status, no line, no outbound call
        self._stranger_hit(source)
        raise LeaseError("not approved", 403)

    # ------------------------------------------------------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------------------------------------------------------
    def _trusted(self):
        return self.https is not None and bool(self.https.clock_trusted())

    def stamp(self, handle=None):
        """What a record keeps of a lease that was just renewed (or made): when its home node last answered, by the wall clock
        (and whether that clock could be trusted), by the monotonic clock, and which boot that was."""
        out = {"ok": int(self.wall()), "t": self._trusted(), "m": round(self.now(), 1), "b": self.boot() or ""}
        if handle is not None:
            out["h"] = handle
        return out

    def _age(self, lease, answered_here=False):
        """Seconds since the home node last answered, or None when that cannot be told. The wall clock is used only while it was
        trusted when the record was stamped AND is trusted now; otherwise the monotonic clock, and only inside the boot it
        was stamped in (the monotonic clock starts again at boot)."""
        if lease.get("t") is True and self._trusted() and isinstance(lease.get("ok"), int):
            return max(0.0, self.wall() - lease["ok"])
        boot = self.boot() or ""
        if answered_here and isinstance(lease.get("m"), (int, float)):                  # stamped by an answer in this very process
            return max(0.0, self.now() - lease["m"])
        if boot and isinstance(lease.get("m"), (int, float)) and lease.get("b") == boot:      # an empty boot id cannot tell one boot from another
            return max(0.0, self.now() - lease["m"])
        return None

    # ------------------------------------------------------------------------------------------------------------------
    # Node A: making a lease and answering about it
    # ------------------------------------------------------------------------------------------------------------------
    def grant(self, device_id, node_id):
        """At redemption: a handle for this device on that node. A keeps only its hash, with the device record. Raises
        LeaseError when the device is gone or the record could not be saved."""
        handle = secrets.token_hex(16)
        try:
            kept = self.auth.lend(device_id, node_id, _hash(handle))
        except OSError:
            raise LeaseError("the lease could not be kept: try again", 503)
        if not kept:
            raise LeaseError("that device is no longer paired on the node it came from", 403)
        return handle

    def answer(self, body, caller_key, source):
        """POST /api/handover/lease on the peer port: {"handles": [..], "to": the asking node's id, "key": its key hash}. Answers
        {"v": 1, "leases": [{"state": "ok", "role"} or {"state": "gone"}, ...]} in the order asked. The caller is proved by the
        handshake (`caller_key`), is approved here, and may ask for the handles it was given only."""
        h = self.handover
        ok = isinstance(body, dict) and set(body) == {"handles", "to", "key"} and isinstance(body["handles"], list) \
            and 1 <= len(body["handles"]) <= MAX_HANDLES and all(isinstance(x, str) and _HANDLE.fullmatch(x) for x in body["handles"]) \
            and isinstance(body["to"], str) and fleetlist.ID_WORD.fullmatch(body["to"]) \
            and isinstance(body["key"], str) and fleetlist.KEY_WORD.fullmatch(body["key"])
        approved = ok and body["key"] == caller_key and h.approved(body["to"], caller_key) is not None
        if not approved:
            if self._stranger_over(source):
                raise LeaseError("too many calls have failed lately: wait a minute", 429)
            if not ok:
                self._stranger_hit(source)
                raise LeaseError("not a lease call", 400)
            self._refuse_stranger(source)
        if not self._spend(self._lease_allow, caller_key):
            raise LeaseError("too many questions from that node: wait a minute", 429)
        out = []
        for handle in body["handles"]:
            dev = self.auth.lent_lookup(_hash(handle), body["to"])
            if dev is None or dev.get("via") not in (None, "recovery") or not dev.get("secure") or dev["role"] not in ROLES:
                out.append({"state": "gone"})
            else:
                out.append({"state": "ok", "role": dev["role"]})
        return {"v": 1, "leases": out}

    # ------------------------------------------------------------------------------------------------------------------
    # Node A: telling B that a device it lent is gone (the poke)
    # ------------------------------------------------------------------------------------------------------------------
    def poke(self, node_ids):
        """Called when a device that was lent is removed here: each of those nodes is poked, in the background. A poke that came
        within OUT_POKE_GAP of the last one to that node waits for the next tick (the node's own check covers the rest)."""
        with self.lock:
            self._out_dirty |= {n for n in node_ids if isinstance(n, str)}
        self.spawn(self._flush_pokes)

    def _flush_pokes(self):
        h = self.handover
        own_id, _ = h.own()
        while True:
            with self.lock:
                t = self.now()
                due = [n for n in self._out_dirty if t - self._last_out.get(n, -OUT_POKE_GAP) >= OUT_POKE_GAP]
                if not due or self._poke_sent.over():           # a removal of every device waits its turn, a few pokes a tick
                    return
                self._poke_sent.count()
                node = due[0]
                self._out_dirty.discard(node)
                self._last_out[node] = t
                if len(self._last_out) > 256:
                    self._last_out.pop(next(iter(self._last_out)))
            try:
                self.peers.call_once(node, "/api/handover/poke", {"from": own_id, "key": h.own_key()}, budget=False)
            except PeerError as e:
                if e.status == 429 and not e.refused:           # a call to that node was running: try again at the next tick
                    with self.lock:
                        self._out_dirty.add(node)
                    return
                # anything else (the node is off, or does not approve this one): its own check asks within ten minutes

    def poked(self, body, caller_key, source):
        """POST /api/handover/poke on the peer port: {"from": the poking node's id, "key": its key hash}. No authority: this node
        only asks that node about the devices it holds from it. Answers {"v": 1} whether or not there was anything to ask."""
        h = self.handover
        ok = isinstance(body, dict) and set(body) == {"from", "key"} and isinstance(body["from"], str) and fleetlist.ID_WORD.fullmatch(body["from"]) \
            and isinstance(body["key"], str) and fleetlist.KEY_WORD.fullmatch(body["key"])
        approved = ok and body["key"] == caller_key and h.approved(body["from"], caller_key) is not None
        if not approved:
            if self._stranger_over(source):
                raise LeaseError("too many calls have failed lately: wait a minute", 429)
            if not ok:
                self._stranger_hit(source)
                raise LeaseError("not a poke", 400)
            self._refuse_stranger(source)
        if not self._spend(self._poke_allow, caller_key):
            raise LeaseError("too many pokes: wait a minute", 429)
        node = body["from"]
        if not any(r["node"] == node and r["lease"] and r["lease"].get("h") for r in self.auth.node_records()):
            with self.lock:
                self.unknown_pokes += 1                     # a lookup and a count: no call, no event
            return {"v": 1}
        with self.lock:
            t = self.now()
            if t - self._last_poked_check.get(node, -POKE_GAP) < POKE_GAP or self._poke_checks.over():
                self._dirty.add(node)                       # the next tick asks
                return {"v": 1}
            self._last_poked_check[node] = t
            self._poke_checks.count()                       # at most POKE_CHECKS_PER_MINUTE checks started by pokes, for all nodes together
        self.spawn(lambda: self.check_node(node))
        return {"v": 1}

    # ------------------------------------------------------------------------------------------------------------------
    # Node B: the check
    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _node_name(record):
        tail = record["name"].rsplit(", via ", 1)
        return tail[1] if len(tail) == 2 and tail[1] else "its home node"

    def _approval(self, node_id):
        """(key, approved) of the node as this node approves it now, or (None, None)."""
        h = self.handover
        for key in h.expected_keys(node_id):
            ok = h.approved(node_id, key)
            if ok is not None:
                return key, ok
        return None, None

    def _sign_out(self, record, why):
        """Sign a carried device out, but only if it still has the lease this decision was made about (a device that opened the
        node again while the check ran has a new one)."""
        if self.auth.revoke_node_if(record["id"], (record["lease"] or {}).get("h")):
            self.event("handover", "%s was signed out: %s." % (record["name"], why), record["name"])

    def _lower_events(self, records, approved_by_id, done, why):
        for r in records:
            if done.get(r["id"]):
                new = approved_by_id[r["id"]]
                self.event("handover", "%s was lowered to %s: %s." % (r["name"], NAMES[new], why(r)), r["name"])

    def _decide(self, record, approved, item):
        """One answer of the home node about one device: ("out", why), ("keep", role to lower to or None) or None (not understood)."""
        state = item.get("state") if isinstance(item, dict) else None
        name = self._node_name(record)
        if state == "gone":
            return "out", "%s no longer has it paired" % name
        role = item.get("role") if state == "ok" else None
        if role not in ROLES:
            return None                                     # not understood: nothing is renewed
        new = carried_role(role, approved["carry"])
        if new is None:
            return "out", "%s no longer gives it a role that carries to other nodes" % name
        return "keep", new if _rank(new) < _rank(record["role"]) else None

    def check_node(self, node_id):
        """Ask the home node about every device carried from it. A good answer renews and follows it downward; no answer renews
        nothing (the lease runs on until the lapse). One save for all the devices. Returns True when the node answered."""
        records = [r for r in self.auth.node_records() if r["node"] == node_id and r["lease"] and r["lease"].get("h")]
        if not records:
            return False
        with self.lock:
            self._next_check[node_id] = self.now() + TICK_EVERY        # (until it has answered: a failed call is tried again at the next tick)
            self._dirty.discard(node_id)
        records = records[:MAX_HANDLES]
        h = self.handover
        own_id, _ = h.own()
        name = h.name_of(node_id) or self._node_name(records[0])
        try:
            # per-target budget only: a node that hangs, or keeps poking, cannot use up this node's budget for its other calls
            answer, key = self.peers.call_once(node_id, "/api/handover/lease", {
                "handles": [r["lease"]["h"] for r in records], "to": own_id, "key": h.own_key()}, budget=False)
        except PeerError as e:
            if not (e.status == 429 and not e.refused):     # (a call already running is not news)
                self._unanswered(node_id, name)
            return False
        approved = h.approved(node_id, key)                 # the node as the key it showed is approved now
        items = answer.get("leases") if isinstance(answer, dict) else None
        if approved is None or not isinstance(items, list) or len(items) != len(records):
            self._unanswered(node_id, name)
            return False
        with self.lock:
            self._down.discard(node_id)
            self._next_check[node_id] = self.now() + CHECK_EVERY
        stamp = self.stamp()
        changes, lowered_to = [], {}
        for record, item in zip(records, items):
            decision = self._decide(record, approved, item)
            if decision is None:
                continue
            if decision[0] == "out":
                self._sign_out(record, decision[1])
                continue
            changes.append({"id": record["id"], "h": record["lease"]["h"], "role": decision[1], "stamp": stamp})
            if decision[1]:
                lowered_to[record["id"]] = decision[1]
        done = self.auth.apply_node_updates(changes) if changes else {}
        with self.lock:
            self._renewed |= set(done)
        self._lower_events(records, lowered_to, done, lambda r: "%s now gives it that" % self._node_name(r))
        return True

    def _unanswered(self, node_id, name):
        with self.lock:
            first = node_id not in self._down
            self._down.add(node_id)
        if first:
            self.event("handover", "%s did not answer a check of its carried devices; they stay signed in until it has been silent for 24 hours (after a restart, for 10 minutes of this node running)." % name)

    # ------------------------------------------------------------------------------------------------------------------
    # Node B: the sweep (no network)
    # ------------------------------------------------------------------------------------------------------------------
    def sweep(self):
        """Sign out what must go now, with no call to anybody. Returns the number of devices signed out."""
        records = self.auth.node_records()
        if not records:
            return 0
        h = self.handover
        if not h.accepting():                               # the lock: the Owner-only switch
            gone = self.auth.revoke_node_devices()
            if gone:
                self.event("handover", "Signed out %d carried device%s: this node no longer accepts devices from other nodes." % (gone, "" if gone == 1 else "s"))
            return gone
        total, nodes = 0, {}
        for r in records:
            nodes.setdefault(r["node"], []).append(r)
        changes, lowered_to, lowered = [], {}, []
        for node_id, group in nodes.items():
            _key, approved = self._approval(node_id) if isinstance(node_id, str) else (None, None)
            if approved is None:                            # not on the list or accepted any more, or denied: a deny always wins
                if isinstance(node_id, str):
                    gone = self.auth.revoke_node_devices(node_id)
                else:                                       # (a record with no usable source: only these go, never every carried device)
                    gone = sum(1 for r in group if self.auth.revoke(r["id"]))
                total += gone
                if gone:
                    self.event("handover", "Signed out %d device%s carried from %s: that node is no longer approved here." % (
                        gone, "" if gone == 1 else "s", self._node_name(group[0])))
                continue
            for r in group:                                 # the role never rises; a lower carry now lowers the record, a carry of none ends it
                new = carried_role(r["role"], approved["carry"])
                if new is None:
                    self._sign_out(r, "this node no longer takes devices from %s" % self._node_name(r))
                    total += 1
                elif _rank(new) < _rank(r["role"]):
                    changes.append({"id": r["id"], "h": (r["lease"] or {}).get("h"), "role": new})
                    lowered_to[r["id"]] = new
                    lowered.append(r)
        done = self.auth.apply_node_updates(changes) if changes else {}
        self._lower_events(lowered, lowered_to, done, lambda r: "this node now gives %s that" % self._node_name(r))
        return total + self._lapse()

    def _lapse(self):
        """24 hours after the home node last answered, the device is signed out. `ok` is never written without an answer from
        the home node: a record that has none yet (made before leases) is stamped once, and one whose age cannot be told after a
        restart is signed out when the grace is over and its home node still has not answered."""
        gone, stamps = 0, []
        for r in self.auth.node_records():
            lease = r["lease"]
            if not lease:                                   # a record made before leases: its 24 hours start now
                stamps.append({"id": r["id"], "h": None, "stamp": self.stamp()})
                continue
            age = self._age(lease, r["id"] in self._renewed)
            name = self._node_name(r)
            if age is None:
                if self.now() - self.started < CLOCK_GRACE:
                    continue                                # the clock may still be set from the network, or the home node may answer
                self._sign_out(r, "this node restarted and %s has not answered since" % name)
                gone += 1
            elif age >= LEASE_SECONDS:
                self._sign_out(r, "%s has not answered for 24 hours" % name)
                gone += 1
        if stamps:
            self.auth.apply_node_updates(stamps)
        return gone

    # ------------------------------------------------------------------------------------------------------------------
    # The worker
    # ------------------------------------------------------------------------------------------------------------------
    def tick(self):
        """One round: the sweep, the checks that are due or that pokes asked for, and the pokes that had to wait."""
        try:
            self.sweep()
        except Exception as e:
            self.log("pvj-web: the sweep of carried devices failed: %s" % e)
        t = self.now()
        sources = {r["node"] for r in self.auth.node_records() if r["lease"] and r["lease"].get("h") and isinstance(r["node"], str)}
        with self.lock:
            due = [n for n in sources if self._next_check.get(n, 0.0) <= t or n in self._dirty]
            self._next_check = {k: v for k, v in self._next_check.items() if k in sources}
        for node_id in due:
            try:
                self.check_node(node_id)
            except Exception as e:
                self.log("pvj-web: a lease check failed: %s" % e)
        retry = getattr(self.peers, "retry_owed", None)       # removals that could not be delivered are tried again (slice 4d)
        if retry is not None:
            try:
                retry()
            except Exception as e:
                self.log("pvj-web: pushing removals again failed: %s" % e)
        with self.lock:
            waiting = bool(self._out_dirty)
        if waiting:
            try:
                self._flush_pokes()
            except Exception as e:
                self.log("pvj-web: pokes failed: %s" % e)

    def start(self):
        if self._thread is not None:
            return
        self._stop = threading.Event()

        def run():
            while not self._stop.wait(TICK_EVERY):
                self.tick()
        self._thread = threading.Thread(target=run, name="lease-worker", daemon=True)
        self._thread.start()

    def stop(self):
        if self._stop is not None:
            self._stop.set()
