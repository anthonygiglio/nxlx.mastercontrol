# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The one-time ticket (D132, design/node-handover_2026-11-09.md sections 4 and 5, slice 4a): how a device paired on node A
opens node B signed in, without a PIN and without any token leaving A.

A makes a ticket (128 random bits, kept in memory only, 60 seconds on A's monotonic clock) for one device and one node.
The device's browser hands it to B in a form; B calls A back over the peer port (a checked connection, the caller proved by
its client certificate) and asks "is this ticket good, for me?". A looks the ticket up and deletes it in one step under its
lock, whether or not the rest passes, so two calls can never both win. If it is good, A says who the device is and what role
it has now; B makes the device a record of its own (`via: node`) with the lowest of that role and its carry for A, never
higher, and gives it its own token.

Only approved nodes count: on the signed list or accepted by fingerprint, and not on the deny list (a deny always wins).
The "Accept devices from other nodes" switch of B is the lock, and only an Owner turns it. The lease (the 10 minute check, the
poke, the 24 hour lapse) is pvj/lease.py (slice 4b): A's redeem answer carries the lease handle and B's record keeps it. The
removal of a node from the cluster is slice 4d; nothing here blocks it.

Nothing secret is logged or answered: not a ticket, not the secret, not a token, not a key.
"""
import collections
import hashlib
import hmac
import re
import secrets
import threading
import time

from . import fleetlist, peer as peer_mod
from .auth import AuthError
from .handover import carried_role
from .lease import LeaseError

TICKET_SECONDS = 60
MAX_TICKETS = 64
TICKETS_PER_MINUTE = 6          # per device
FORM_ATTEMPTS = 6               # forms one source address may post to /handover in a minute, good or bad (B)
PENDING_SECONDS = 120
MAX_PENDING = 8
NAMES = {"view": "Guest", "live": "Operator", "full": "Owner"}
_TICKET = re.compile(r"[0-9a-f]{32}")
_SECRET = re.compile(r"[A-Za-z0-9_-]{16,128}")
_PROOF = re.compile(r"[0-9a-f]{64}")
_HANDLE = re.compile(r"[0-9a-f]{32}")
_ORIGIN = re.compile(r"https://(?:[a-z0-9.-]{1,253}|\[[0-9a-f:.]{2,45}\])(?::[0-9]{1,5})?")


class TicketError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def proof_of(secret):
    """The hash of the value the page keeps: what A holds, and all A holds, of it."""
    return hashlib.sha256(secret.encode("ascii", "replace")).hexdigest()


def _hash(ticket):
    return hashlib.sha256(ticket.encode("ascii", "replace")).hexdigest()


class Tickets:
    def __init__(self, handover, peers, auth, https, event=None, log=None, now=time.monotonic):
        """handover: the Handover (who is approved, the switch). peers: the Peers (the checked connection). auth: the Auth
        (device records). https: the HttpsBox (this node's origin, whether a device was paired over https)."""
        self.handover, self.peers, self.auth, self.https = handover, peers, auth, https
        self.event = event or (lambda kind, text, who=None: None)
        self.log = log or (lambda line: None)
        self.now = now
        self.leases = None                                  # lease.Leases or None (slice 4b): the lease handle given at redemption, kept with the record
        self.lock = threading.Lock()
        self._tickets = {}                                # hash of the ticket -> entry (memory only)
        self._device_asks = {}                              # device id -> deque of times a ticket was made
        self._pending = collections.OrderedDict()           # handle -> a carried device waiting for the confirm page
        # Four counters that never touch each other, and none of them is node-wide: whoever is on the network can post junk, and
        # junk must not stop a real hand-over from another device, nor this node's own role as the node that answers.
        self._a_source = peer_mod.Buckets(peer_mod.SOURCE_FAILS, now)         # A: redeems that failed, from callers that are not approved (by address)
        self._b_source = peer_mod.Buckets(peer_mod.SOURCE_FAILS, now)         # B: forms that failed (by address)
        self._b_attempts = peer_mod.Buckets(FORM_ATTEMPTS, now)               # B: forms posted, whatever came of them (by address), before A is called

    def _over(self, buckets, key):
        with self.lock:
            return buckets.over(key)

    def _hit(self, buckets, key):
        with self.lock:
            buckets.get(key).count()

    def _b_blocked(self, source):
        return self._over(self._b_source, source) or self._over(self._b_attempts, source)

    def _fail(self, source):
        """A failure at B, charged to the sender's address only."""
        self._hit(self._b_source, source)

    # ------------------------------------------------------------------------------------------------------------------
    # Node A: making a ticket
    # ------------------------------------------------------------------------------------------------------------------
    def may_carry(self, device):
        """Why this device may not carry to another node, or None when it may: an Owner or an Operator whose token was made over
        https, paired by the PIN or a recovery code (not a join code, a controller's code, another node or a remote
        support login)."""
        if not isinstance(device, dict) or device.get("remote") or not isinstance(device.get("id"), str):
            return "this device is not one that carries to other nodes"
        stored = self.auth.device(device["id"])
        if stored is None:
            return "this device is not paired here"
        if stored["role"] not in ("live", "full"):
            return "only an Operator or an Owner opens another node without its PIN"
        if stored.get("via") not in (None, "recovery"):
            return "a device that joined by a code, a link or from another node does not carry to other nodes"
        if not stored["secure"]:
            return "this device was paired over plain http: log out and pair it again over https first"
        return None

    def _refuse_make(self, device, node, why, status=403):
        name = self.handover.name_of(node) if isinstance(node, str) else None
        self.event("handover", "A ticket for %s was not made: %s." % (name or "a node", why), (device or {}).get("name") if isinstance(device, dict) else None)
        raise TicketError(why[:1].upper() + why[1:] + ".", status)

    def make(self, device, body, host):
        """POST /api/handover/ticket: {"to": node id, "proof": sha256 hex of a value only the page holds}. Answers
        {"ticket", "action", "origin", "seconds"}. The address in `action` is the one this node just checked."""
        h = self.handover
        if not isinstance(body, dict) or set(body) != {"to", "proof"} or not isinstance(body["to"], str) \
                or not fleetlist.ID_WORD.fullmatch(body["to"]) or not isinstance(body["proof"], str) or not _PROOF.fullmatch(body["proof"]):
            raise TicketError('send {"to": the node\'s id, "proof": the sha-256 of a value the page keeps}')
        to = body["to"]
        why = self.may_carry(device)
        if why:
            self._refuse_make(device, to, why)
        if self._asks(device["id"]):
            raise TicketError("too many tickets were asked for lately: wait a minute", 429)
        keys = h.expected_keys(to)
        if not keys:
            self._refuse_make(device, to, "that node is not approved (it is not on the fleet list, was not accepted by its fingerprint, or was removed from the cluster)")
        if h.their_carry(to) == "none":
            self._refuse_make(device, to, "that node takes no devices from other nodes")
        origin = self.https.trusted_origin(host) if self.https is not None else None
        if not origin:
            self._refuse_make(device, to, "open this node by an address or name its certificate carries, then try again", 409)
        try:
            address, port, key = self.peers.panel_address(to)
        except peer_mod.PeerError as e:
            self._refuse_make(device, to, e.message, e.status if e.status in (403, 429) else 502)
        ticket = secrets.token_hex(16)
        with self.lock:
            t = self.now()
            self._tickets = {k: v for k, v in self._tickets.items() if v["end"] > t}
            if len(self._tickets) >= MAX_TICKETS:
                raise TicketError("too many hand-overs are waiting: wait a minute", 429)
            self._tickets[_hash(ticket)] = {"device": device["id"], "name": device.get("name"), "to": to, "key": key, "proof": body["proof"],
                                            "origin": origin, "end": t + TICKET_SECONDS}
        return {"ticket": ticket, "action": "https://%s%s/handover" % (address, "" if port == 443 else ":%d" % port), "origin": origin,
                "seconds": TICKET_SECONDS}

    def _asks(self, device_id):
        """True when this device has asked for TICKETS_PER_MINUTE tickets in the last minute; otherwise counts this ask."""
        with self.lock:
            t = self.now()
            q = self._device_asks.setdefault(device_id, collections.deque())
            while q and t - q[0] > 60:
                q.popleft()
            if len(q) >= TICKETS_PER_MINUTE:
                return True
            q.append(t)
            for gone in [k for k, v in self._device_asks.items() if not v and k != device_id]:
                del self._device_asks[gone]
            if len(self._device_asks) > 256:
                self._device_asks.pop(next(iter(self._device_asks)))
            return False

    # ------------------------------------------------------------------------------------------------------------------
    # Node A: redeeming (POST /api/handover/redeem on the peer port)
    # ------------------------------------------------------------------------------------------------------------------
    def redeem(self, body, caller_key, source):
        """Is this ticket good, and for whom? `caller_key`: the hash of the key in the certificate the caller showed in the handshake
        (the caller is proved). The ticket is taken out of the table first, under the lock, whether or not the rest passes. Raises
        TicketError; answers {"v", "name", "device", "role", "origin", "node"}."""
        h = self.handover
        need = {"ticket", "secret", "to", "key", "serial"}
        ok = isinstance(body, dict) and set(body) == need and isinstance(body["ticket"], str) and _TICKET.fullmatch(body["ticket"]) \
            and isinstance(body["secret"], str) and _SECRET.fullmatch(body["secret"]) and isinstance(body["to"], str) \
            and fleetlist.ID_WORD.fullmatch(body["to"]) and isinstance(body["key"], str) and fleetlist.KEY_WORD.fullmatch(body["key"]) \
            and (body["serial"] == "pairing" or (isinstance(body["serial"], int) and not isinstance(body["serial"], bool) and body["serial"] >= 1))
        # A refusal to an approved, authenticated peer is never throttled: the ticket is 128 bits and spent on the first try, so a
        # count protects nothing, and anyone on the network could use it to stop every genuine hand-over (the event line stays).
        # A caller A does not approve is counted against its own address alone.
        peer = ok and h.approved(body["to"], caller_key) is not None

        def failed():
            if not peer:
                self._hit(self._a_source, source)
        if not peer and self._over(self._a_source, source):
            raise TicketError("too many calls have failed lately: wait a minute", 429)
        if not ok:
            failed()
            raise TicketError("not a redeem call", 400)
        with self.lock:                                     # the look-up and the delete are one step: two calls cannot both win
            entry = self._tickets.pop(_hash(body["ticket"]), None)
            late = entry is not None and entry["end"] <= self.now()
        if entry is None:
            failed()
            raise TicketError("that ticket is not good", 403)
        if late:
            self._say_redeem(entry, "the ticket had ended")
            raise TicketError("the ticket had ended", 403)
        if entry["to"] != body["to"] or entry["key"] != caller_key or body["key"] != caller_key:
            failed()
            self._say_redeem(entry, "it was asked for by another node than the one it was made for")
            raise TicketError("that ticket is for another node", 403)
        if h.approved(entry["to"], caller_key) is None:
            failed()
            self._say_redeem(entry, "that node is not approved")
            raise TicketError("that node is not approved", 403)
        lst = h.list
        mine = lst.serial if lst is not None else None
        if isinstance(body["serial"], int) and mine is not None and body["serial"] < mine:
            self._say_redeem(entry, "that node holds an older fleet list")
            raise TicketError("that node holds an older fleet list (serial %d, this node has %d)" % (body["serial"], mine), 409)
        if not hmac.compare_digest(proof_of(body["secret"]), entry["proof"]):
            failed()
            self._say_redeem(entry, "the secret did not match")
            raise TicketError("that ticket is not good", 403)
        stored = self.auth.device(entry["device"])
        if stored is None:
            self._say_redeem(entry, "the device is no longer paired")
            raise TicketError("that device is no longer paired on the node it came from", 403)
        if stored["role"] not in ("live", "full") or stored.get("via") not in (None, "recovery") or not stored["secure"]:
            self._say_redeem(entry, "the device may no longer carry")
            raise TicketError("that device may no longer carry to other nodes", 403)
        out = {"v": 1, "name": stored["name"], "device": stored["id"], "role": stored["role"], "origin": entry["origin"]}
        if self.leases is not None:
            try:
                out["lease"] = self.leases.grant(stored["id"], entry["to"])      # the lease handle: B keeps it, this node keeps its hash
            except LeaseError as e:
                self._say_redeem(entry, e.message[:1].lower() + e.message[1:])
                raise TicketError(e.message, e.status)
        node = h.name_of(entry["to"]) or "a node"
        self.event("handover", "%s (%s) was signed in to %s." % (stored["name"], NAMES[stored["role"]], node), stored["name"])
        own_id, _ = h.own()
        out["node"] = own_id
        return out

    def _say_redeem(self, entry, why):
        self.event("handover", "A ticket for %s was not redeemed: %s." % (self.handover.name_of(entry["to"]) or "a node", why), entry.get("name"))

    # ------------------------------------------------------------------------------------------------------------------
    # Node B: the form (POST /handover) and the confirm
    # ------------------------------------------------------------------------------------------------------------------
    def _refused(self, from_name, why, count=None):
        if count is not None:
            self._fail(count)
        self.event("handover", "A hand-over from %s was refused: %s." % (from_name or "a node", why))
        return {"result": "refused", "sentence": why}

    def signin(self, fields, origin, source, current_role=None):
        """Node B. `fields`: the form {ticket, secret, from}. `origin`: the Origin header of the POST. `source`: the sender's
        address. `current_role`: the role of the device a valid cookie at this node already is, or None. Returns
        {"result": "signed-in", "token", "name", "role", "from"}, {"result": "confirm", "handle", "have", "would", "from"} or
        {"result": "refused", "sentence"}. Never raises for a hand-over that fails: the tab ends at the PIN screen with the sentence."""
        h = self.handover
        if self._b_blocked(source):
            return {"result": "refused", "sentence": "too many hand-overs have failed lately: wait a minute, or use the PIN"}
        self._hit(self._b_attempts, source)                 # an attempt counts, before anything is called
        if not h.accepting():
            return self._refused(None, "this node does not take devices from other nodes")
        ok = isinstance(fields, dict) and set(fields) == {"ticket", "secret", "from"} and all(isinstance(v, str) for v in fields.values()) \
            and _TICKET.fullmatch(fields["ticket"]) and _SECRET.fullmatch(fields["secret"]) and fleetlist.ID_WORD.fullmatch(fields["from"])
        if not ok:
            return self._refused(None, "that was not a hand-over", source)
        if not isinstance(origin, str) or not _ORIGIN.fullmatch(origin.lower()):
            return self._refused(h.name_of(fields["from"]), "the browser did not say which page the hand-over came from", source)
        frm = fields["from"]
        name_a = h.name_of(frm)
        keys = h.expected_keys(frm)
        if not keys:
            return self._refused(None, "that node is not approved here", source)
        if not any((h.approved(frm, k) or {}).get("carry") in ("full", "live") for k in keys):
            return self._refused(name_a, "this node takes nothing from %s (its carry for it is none)" % name_a)
        own_id, _ = h.own()
        lst = h.list
        try:
            answer, a_key = self.peers.call_once(frm, "/api/handover/redeem", {
                "ticket": fields["ticket"], "secret": fields["secret"], "to": own_id, "key": h.own_key(),
                "serial": lst.serial if lst is not None else "pairing"})
        except peer_mod.PeerError as e:
            if e.refused:                                   # the node answered and said no: a bad ticket or secret counts as a failure here too
                return self._refused(name_a, e.message, source)
            return self._refused(name_a, e.message if e.status < 500 else "%s could not be reached (%s)" % (name_a, e.message))
        ok = isinstance(answer, dict) and isinstance(answer.get("name"), str) and isinstance(answer.get("device"), str) \
            and re.fullmatch(r"[0-9a-f]{1,32}", answer["device"]) and answer.get("role") in ("live", "full") \
            and isinstance(answer.get("origin"), str) and _ORIGIN.fullmatch(answer["origin"])
        if not ok:
            return self._refused(name_a, "%s sent an answer that was not understood" % name_a, source)
        if origin.lower() != answer["origin"]:
            return self._refused(name_a, "the hand-over was not made from that node's own page", source)
        approved = h.approved(frm, a_key)
        if approved is None:
            return self._refused(name_a, "that node is not approved here", source)
        role = carried_role(answer["role"], approved["carry"])
        if role is None:
            return self._refused(approved["name"], "this node takes nothing from %s" % approved["name"])
        label = self._label(answer["name"], approved["name"])
        handle = answer.get("lease") if isinstance(answer.get("lease"), str) and _HANDLE.fullmatch(answer["lease"]) else None     # (a node with no leases gives none: the record lapses)
        if current_role is not None and _rank(current_role) > _rank(role):
            return self._wait(label, role, frm, answer["device"], approved["name"], current_role, handle)
        return self._sign_in(label, role, frm, answer["device"], approved["name"], answer["name"], handle)

    @staticmethod
    def _label(name, node_name):
        suffix = ", via %s" % node_name
        return (name or "device")[:max(1, 40 - len(suffix))] + suffix

    def _sign_in(self, label, role, frm, device_id, node_name, plain_name, handle=None):
        try:
            lease = self.leases.stamp(handle) if self.leases is not None else None       # the 24 hours start with this answer of the home node
            token, record = self.auth.add_node_device(label, role, frm, device_id, allowed=self.handover.accepting, lease=lease)
        except AuthError as e:
            return self._refused(node_name, str(e))
        self.event("handover", "%s (%s) signed in from %s." % (plain_name, NAMES[role], node_name), record["name"])
        return {"result": "signed-in", "token": token, "name": record["name"], "role": role, "from": node_name, "device": record["id"]}

    def _wait(self, label, role, frm, device_id, node_name, have, handle=None):
        """The device is already signed in here as something higher: nothing is changed until the person says so."""
        handle = secrets.token_hex(16)
        with self.lock:
            t = self.now()
            for k in [k for k, v in self._pending.items() if v["end"] <= t]:
                del self._pending[k]
            while len(self._pending) >= MAX_PENDING:
                self._pending.popitem(last=False)
            self._pending[handle] = {"label": label, "role": role, "from": frm, "device": device_id, "node": node_name, "lease": handle, "end": t + PENDING_SECONDS}
        return {"result": "confirm", "handle": handle, "have": NAMES[have], "would": NAMES[role], "from": node_name}

    def confirm(self, handle, source):
        """The person pressed Switch: the pending hand-over becomes a record. Once only; refused after PENDING_SECONDS."""
        if self._b_blocked(source):
            return {"result": "refused", "sentence": "too many hand-overs have failed lately: wait a minute, or use the PIN"}
        self._hit(self._b_attempts, source)
        with self.lock:
            entry = self._pending.pop(handle, None) if isinstance(handle, str) else None
            late = entry is not None and entry["end"] <= self.now()
        if entry is None or late:
            return self._refused(None, "that switch has run out: tap Open on the other node again", None if late else source)
        if not self.handover.accepting():
            return self._refused(entry["node"], "this node does not take devices from other nodes")
        h = self.handover
        approved = [a for a in (h.approved(entry["from"], k) for k in h.expected_keys(entry["from"])) if a is not None]
        role = carried_role(entry["role"], approved[0]["carry"]) if approved else None      # still approved, not denied, still giving something
        if role is None:
            return self._refused(entry["node"], "%s is no longer approved here" % entry["node"])
        plain = entry["label"].rsplit(", via ", 1)[0]
        return self._sign_in(entry["label"], role, entry["from"], entry["device"], entry["node"], plain, entry.get("lease"))


def _rank(role):
    return {"view": 0, "live": 1, "full": 2}.get(role, 0)
