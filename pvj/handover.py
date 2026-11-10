# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What a node keeps and decides about the other nodes it trusts (D132, design/node-handover_2026-11-09.md, slice 2).

Slice 2 is storage and one question. Nothing here calls another node, makes a ticket or signs anybody in; that is
slice 3 on. What is here:

* **The fleet list** the owner signed with the root (pvj/fleetlist.py reads and checks it): `fleet.json` and
  `fleet.sig` beside `tls/` in the state folder, mode 0640, written by rename, never in `settings.json`. A list is
  taken only when it is signed by the root this node pins, is of this node's fleet, is not older than the one held,
  and names THIS node with THIS node's key.
* **Acceptances by fingerprint** (`pairings.json`): the substitute for a list for one pair of nodes (design 3.4). The
  Owner types the other node's key fingerprint back; without it nothing is stored.
* **The deny file** (`deny.json`): the keys of nodes removed from the cluster. Storage and reading only here.
* **The switch** "Accept devices from other nodes" (`fleet.accept` in settings, off by default).
* **`approved(node id, key hash)`**: the one question the later slices ask. A node is approved when it is on the held
  list with that id AND that key (an id alone is public and proves nothing), or accepted by fingerprint, and its key is
  not on the deny list. Deny wins over both.

None of these files is ever in a settings export, the diagnostics file or any API answer, except the Owner's own
status (names, serial, own fingerprint; no key of another node). A factory reset removes them.
"""
import hashlib
import json
import os
import re
import threading

from . import fleetlist
from .fleet import stored_on as fleet_stored_on

LIST_FILE, SIG_FILE, DENY_FILE, PAIRINGS_FILE = "fleet.json", "fleet.sig", "deny.json", "pairings.json"
FILES = (LIST_FILE, SIG_FILE, DENY_FILE, PAIRINGS_FILE)
MAX_JSON = 65536
MAX_DENY = 256
MAX_PAIRINGS = fleetlist.MAX_NODES
ROLE_RANK = {"view": 0, "live": 1, "full": 2}
CARRY_ROLE = {"none": None, "live": "live", "full": "full"}


class HandoverError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def fingerprint(key_hash):
    """The 64 hex digits of a key hash as people read and type them: sixteen groups of four."""
    h = (key_hash or "").lower()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


class Floor:
    """What a node remembers of a list it could not check at start: the fleet and the highest serial found on disk, and the
    digest of that file. It is not a list and approves nobody; it only stops a node from going back to an older serial or
    over to another fleet while the root is briefly missing or the node's key was renewed (the fields `verify` reads)."""

    def __init__(self, fleet, serial, digest):
        self.fleet, self.serial, self.digest = fleet, serial, digest


def same_fingerprint(typed, key_hash):
    """Is what was typed (spaces, colons, dashes and case do not matter) this key?"""
    if not isinstance(typed, str) or not isinstance(key_hash, str):
        return False
    norm = re.sub(r"[\s:-]", "", typed.lower())            # only spaces, colons and dashes may separate the digits
    return re.fullmatch(r"[0-9a-f]{64}", norm) is not None and norm == key_hash.lower()


def carried_role(role, carry):
    """The role a device gets on a node whose carry for the source is `carry`: the lowest of what it has and what the
    node gives, never higher. Only Operators ("live") and Owners ("full") carry; a Guest ("view") never does. None
    is "not carried" (the PIN screen)."""
    if role not in ("live", "full") or carry not in CARRY_ROLE or CARRY_ROLE[carry] is None:
        return None
    return role if ROLE_RANK[role] <= ROLE_RANK[CARRY_ROLE[carry]] else CARRY_ROLE[carry]


class Handover:
    def __init__(self, folder, https, settings, describe, event=None, log=None, openssl="openssl"):
        """folder: the state folder (beside tls/). https: the HttpsBox (its pinned root and certificate). describe: a
        function giving {"id", "name"} of this node. event(kind, text): the event log."""
        self.folder, self.https, self.settings, self.describe = folder, https, settings, describe
        self.event = event or (lambda kind, text, who=None: None)
        self.log = log or (lambda line: None)
        self.openssl = openssl
        self.lock = threading.RLock()
        self.list = None             # the FleetList that checked out at load or when it was taken, or None
        self.floor = None            # a Floor, when files on disk did not check out: the fleet and the highest serial they show
        self.sig_text = None         # the signature text of the list in use (a peer that is behind is sent both)
        self.unknown_calls = 0       # peer calls from a node this one does not approve: a count, never a log line
        self.removed = False         # a peer told this node that its own key was removed from the cluster
        self._inputs = None          # (root, own key) when the files on disk were last read, to retry when they change
        self.problem = None          # why the files on disk are not in use, for the Owner's status
        self._deny = []
        self._pairings = []

    # -- files --
    def path(self, name):
        return os.path.join(self.folder, name)

    def _read(self, name, limit=MAX_JSON):
        try:
            with open(self.path(name), "rb") as f:
                data = f.read(limit + 1)
        except OSError:
            return None
        return data if len(data) <= limit else None

    def _write(self, name, data, mode=0o640):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.path(name) + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            os.fchmod(f.fileno(), mode)
        os.replace(tmp, self.path(name))

    def _unlink(self, name):
        try:
            os.unlink(self.path(name))
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    # -- this node --
    def own(self):
        d = self.describe() or {}
        return d.get("id"), d.get("name")

    def own_key(self):
        """This node's key hash, from the certificate in use; None when it has none."""
        text = self.https.cert_pem() if self.https is not None else None
        if not text:
            return None
        try:
            return fleetlist.key_hash(fleetlist.certificate_der(text))
        except fleetlist.FleetError:
            return None

    def card(self):
        """What GET /api/fleet/card says: the facts mDNS announces plus the hash of this node's key. Nothing secret.
        None when the node has no certificate (and so nothing to be trusted by)."""
        if not fleet_stored_on(self.settings.data.get("fleet")):
            return None                  # discovery is off: this node announces nothing, so it gives its id and name to nobody
        key = self.own_key()
        node_id, name = self.own()
        if not key or not node_id or not name:
            return None
        return {"v": fleetlist.VERSION, "id": node_id, "name": name, "key": key}

    # -- the switch --
    def accepting(self):
        section = self.settings.data.get("fleet")
        return isinstance(section, dict) and section.get("accept") is True

    def set_accepting(self, on):
        with self.settings.lock:
            section = self.settings.data.get("fleet")
            section = dict(section) if isinstance(section, dict) else {}
            section["accept"] = bool(on)
            self.settings.data["fleet"] = section
            self.settings.save()

    # -- loading at start --
    def load(self):
        """At start: read the files that are there. Nothing here may stop the panel."""
        with self.lock:
            self.list, self.problem, self.floor = None, None, None
            seen = {}
            for suffix in ("", ".prev"):
                try:
                    seen[suffix] = fleetlist.parse(self._read(LIST_FILE + suffix) or b"")      # well formed; not yet believed
                except fleetlist.FleetError:
                    seen[suffix] = None
            known = [x for x in seen.values() if x is not None]
            if known:                                  # remembered even when nothing below checks out
                top = max(known, key=lambda x: x.serial)
                self.floor = Floor(top.fleet, top.serial, top.digest)
            for suffix in ("", ".prev"):
                if suffix and seen[""] is not None and (seen[".prev"] is None or seen[""].serial > seen[".prev"].serial):
                    # a newer list is on disk and did not check out: the older pair is NOT put in its place (a crash between the
                    # two renames leaves no list file at all, or one no newer than the old pair)
                    break
                body, sig = self._read(LIST_FILE + suffix), self._read(SIG_FILE + suffix, fleetlist.MAX_SIG_TEXT)
                if body is None or sig is None:
                    if (body is not None or sig is not None) and not suffix:
                        self.problem = "the fleet list is there without its signature (or the other way round), so it is not in use"
                    continue
                try:
                    earlier = self.problem
                    self.list, self.problem = self._check(body, sig, None), None
                    self.sig_text = sig.decode("ascii", "replace")
                    if suffix:                                 # a good list, but not the one the files say is current
                        self.problem = "the previous list (serial %d) is in use: the current one is %s" % (
                            self.list.serial, ("not usable (" + earlier + ")") if earlier else "missing")
                    break
                except HandoverError as e:
                    self.problem = self.problem or e.message
                    self.log("pvj-web: the fleet list on disk%s is not in use: %s" % (" (the previous one)" if suffix else "", e.message))
            if self.list is None and self.floor is not None and self.problem is None:
                self.problem = "the fleet list on disk (serial %d) is not in use" % self.floor.serial
            if self.list is None and self.problem and self.floor is not None:
                self.problem += "; this node will not take a list of another fleet or one with a serial below %d" % self.floor.serial
            self._deny = self._load_deny()
            self._pairings = self._load_pairings()
            self._inputs = self._now_inputs()

    def _now_inputs(self):
        root = self.https.root_pem() if self.https is not None else None
        return (hashlib.sha256((root or "").encode("utf-8", "replace")).hexdigest(), self.own_key())

    def _maybe_reload(self):
        """A list on disk that did not check out at start (the root or the certificate was not there yet) is tried again
        when the root or the certificate has changed since the last try; nothing is read otherwise."""
        with self.lock:
            if self.list is not None or self._inputs is None or self._inputs == self._now_inputs():
                return
            if not any(os.path.exists(self.path(n)) for n in (LIST_FILE, LIST_FILE + ".prev")):
                self._inputs = self._now_inputs()
                return
            self.load()

    def _root(self):
        text = self.https.root_pem() if self.https is not None else None
        if not text:
            raise HandoverError("this node has no root certificate yet, so it cannot check a list: upload a certificate file that holds the root first (System > Secure connection)")
        return text

    def _check(self, body, sig, current):
        """The list if it is good for THIS node. Raises HandoverError with a sentence."""
        try:
            lst = fleetlist.verify(body, sig, self._root(), self.openssl, fleet=current.fleet if current else None, current=current)
        except fleetlist.FleetError as e:
            raise HandoverError(self._say(e))
        node_id, _name = self.own()
        key = self.own_key()
        if not key:
            raise HandoverError("this node has no certificate yet, so it cannot tell whether the list names it: upload its certificate first")
        row = lst.node(node_id)
        if row is None:
            raise HandoverError("this list does not name this node, so it was not taken")
        if row["key"] != key:
            raise HandoverError("this list names this node with another key than the one it uses now (a new key needs a new signed list), so it was not taken")
        return lst

    @staticmethod
    def _say(e):
        return {"not-signed": "the list is not signed by this node's root, or was changed after it was signed",
                "other-fleet": "this list is for another fleet than the one this node belongs to",
                "older": "this list is older than the one this node holds",
                "same-serial": "this list has the same serial as the one this node holds but says something else"}.get(e.code, e.message)

    # -- taking a list --
    def take_list(self, body, sig, who=None, source=None):
        """Verify, then store. Everything is checked before anything is written. Returns the Owner's status."""
        with self.lock:
            self._maybe_reload()
            lst = self._check(body, sig, self.list or self.floor)            # a list that did not check out at start still sets the floor
            same = self.list is not None and self.list.digest == lst.digest
            if not same:
                self._store_pair(body, sig)
            self.list, self.problem, self.floor = lst, None, None
            self.sig_text = sig.decode("ascii", "replace") if isinstance(sig, (bytes, bytearray)) else sig
            where = " taken from %s" % source if source else " loaded"
            if not same:
                self.event("handover", "The fleet list (serial %d, %d nodes) was%s." % (lst.serial, len(lst.nodes), where), who)
            return self.status()

    def _store_pair(self, body, sig):
        """The signature and the list as a pair, the old pair kept as .prev until the new one is in place."""
        text = sig if isinstance(sig, (bytes, bytearray)) else sig.encode("ascii", "replace")
        self._write(SIG_FILE + ".new", bytes(text))
        self._write(LIST_FILE + ".new", bytes(body))
        for name in (LIST_FILE, SIG_FILE):
            if os.path.exists(self.path(name)):
                os.replace(self.path(name), self.path(name + ".prev"))
        os.replace(self.path(SIG_FILE + ".new"), self.path(SIG_FILE))
        os.replace(self.path(LIST_FILE + ".new"), self.path(LIST_FILE))

    # -- acceptance by fingerprint (design 3.4) --
    def _load_pairings(self):
        return self._load_rows(PAIRINGS_FILE, "nodes", ("id", "name", "key", "carry"), MAX_PAIRINGS)

    def _load_rows(self, name, field, keys, cap):
        raw = self._read(name)
        if raw is None:
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
            rows = data[field]
            out = []
            seen = set()
            for r in rows[:cap]:
                if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in keys) and fleetlist.ID_WORD.fullmatch(r["id"]) \
                        and fleetlist.NODE_NAME.fullmatch(r["name"]) and fleetlist.KEY_WORD.fullmatch(r["key"]) \
                        and r["carry"] in fleetlist.CARRY and r["id"] not in seen:
                    seen.add(r["id"])
                    out.append({k: r[k] for k in keys})
            return out
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self.log("pvj-web: %s could not be read and is ignored" % name)
            return []

    def _save_pairings(self):
        self._write(PAIRINGS_FILE, (json.dumps({"v": 1, "nodes": self._pairings}, indent=1) + "\n").encode("ascii"))

    def accept_node(self, node_id, name, key, carry, typed, who=None):
        """The Owner accepts another node by the fingerprint of its key, typed back. Nothing is stored without it."""
        if not isinstance(carry, str) or carry not in fleetlist.CARRY:
            raise HandoverError("carry is one of: %s" % ", ".join(fleetlist.CARRY))
        for value, pattern, what in ((node_id, fleetlist.ID_WORD, "the node's id"), (name, fleetlist.NODE_NAME, "the node's name"),
                                     (key, fleetlist.KEY_WORD, "the node's key hash")):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise HandoverError("%s is not in the form a node's card gives it" % what)
        if not same_fingerprint(typed, key):
            raise HandoverError("the fingerprint typed does not match this node's key: read it from that node's own panel and type it again, nothing was saved")
        own_id, _ = self.own()
        if node_id == own_id or key == self.own_key():
            raise HandoverError("that is this node itself")
        with self.lock:
            if key in self.denied_keys():
                raise HandoverError("that node was removed from the cluster; allow it again first")
            rows = [r for r in self._pairings if r["id"] != node_id]
            if any(r["key"] == key for r in rows):
                raise HandoverError("another accepted node already has that key")
            if len(rows) >= MAX_PAIRINGS:
                raise HandoverError("this node accepts %d other nodes at most" % MAX_PAIRINGS)
            known = [r for r in self._pairings if r["id"] == node_id]
            if known and known[0]["key"] != key:
                raise HandoverError("that node is already accepted with another key: remove it first, then accept the new key on purpose")
            rows.append({"id": node_id, "name": name, "key": key, "carry": carry})
            self._pairings = rows
            self._save_pairings()
            if known:
                self.event("handover", "The acceptance of %s was changed (carry %s to %s)." % (name, known[0]["carry"], carry), who)
            else:
                self.event("handover", "%s was accepted by its key fingerprint (carry %s)." % (name, carry), who)
            return self.status()

    def remove_acceptance(self, node_id, who=None):
        with self.lock:
            gone = [r for r in self._pairings if r["id"] == node_id]
            if not gone:
                raise HandoverError("that node is not accepted", 404)
            self._pairings = [r for r in self._pairings if r["id"] != node_id]
            self._save_pairings()
            self.event("handover", "The acceptance of %s was removed." % gone[0]["name"], who)
            return self.status()

    # -- the deny file (storage only in slice 2) --
    def _load_deny(self):
        raw = self._read(DENY_FILE)
        if raw is None:
            return []
        try:
            rows = json.loads(raw.decode("utf-8"))["entries"]
            out = []
            for r in rows[:MAX_DENY]:
                if isinstance(r, dict) and isinstance(r.get("key"), str) and fleetlist.KEY_WORD.fullmatch(r["key"]) \
                        and isinstance(r.get("name"), str) and isinstance(r.get("by"), str) and isinstance(r.get("n"), int):
                    out.append({"key": r["key"], "name": r["name"][:64], "by": r["by"][:64], "n": r["n"]})
            return out
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self.log("pvj-web: deny.json could not be read and is ignored")
            return []

    def _save_deny(self):
        self._write(DENY_FILE, (json.dumps({"v": 1, "entries": self._deny}, indent=1) + "\n").encode("ascii"))

    def denied_keys(self):
        with self.lock:
            return {r["key"] for r in self._deny}

    def deny_add(self, key, name, by):
        """Storage only: record a key as removed from the cluster. (The action, the spreading and the sign-outs are slice 4d.)"""
        if not isinstance(key, str) or not fleetlist.KEY_WORD.fullmatch(key):
            raise HandoverError("not a key hash")
        with self.lock:
            if key == self.own_key():
                raise HandoverError("a node never removes itself from the cluster")
            if key in self.denied_keys():
                return False
            if len(self._deny) >= MAX_DENY:
                raise HandoverError("the deny file is full")
            self._deny.append({"key": key, "name": str(name)[:64], "by": str(by)[:64], "n": 1 + max([r["n"] for r in self._deny] or [0])})
            self._save_deny()
            return True

    def deny_remove(self, key):
        with self.lock:
            before = len(self._deny)
            self._deny = [r for r in self._deny if r["key"] != key]
            if len(self._deny) != before:
                self._save_deny()
            return len(self._deny) != before

    # -- the question the later slices ask --
    def approved(self, node_id, key):
        """Is the node with this id AND this key approved to carry devices to this node, and what does THIS node give
        them? Returns {"via": "list" or "pairing", "name", "carry"} or None. On the held list: both nodes must be on
        it, this node's own row says what it gives. Accepted by fingerprint: the carry chosen at the acceptance. A key
        on the deny list is never approved, whatever else says so. An id with another key is not that node."""
        if not isinstance(node_id, str) or not isinstance(key, str):
            return None
        with self.lock:
            self._maybe_reload()
            for k, via, name, carry in self._candidates(node_id):
                if k == key:
                    return {"via": via, "name": name, "carry": carry}
        return None

    def _candidates(self, node_id):
        """(key, via, name, carry) for a node id, in the order they count: the held list first, then an acceptance. Never
        a denied key, never this node's own."""
        out = []
        own_id, _ = self.own()
        if not isinstance(node_id, str) or node_id == own_id:
            return out
        own_key = self.own_key()
        lst = self.list
        if lst is not None:
            peer, me = lst.node(node_id), lst.node(own_id)
            if peer is not None and me is not None and me["key"] == own_key:
                out.append((peer["key"], "list", peer["name"], me["carry"]))
        for r in self._pairings:
            if r["id"] == node_id:
                out.append((r["key"], "pairing", r["name"], r["carry"]))
        denied = self.denied_keys()
        return [c for c in out if c[0] not in denied and c[0] != own_key]

    def expected_keys(self, node_id):
        """The key hashes this node would take for that node id (what a connection to it must show), or []."""
        with self.lock:
            self._maybe_reload()
            return [c[0] for c in self._candidates(node_id)]

    def approved_nodes(self):
        """[(id, name, key)] of every node this node approves, from the list and the acceptances, each id once."""
        with self.lock:
            self._maybe_reload()
            ids = []
            if self.list is not None:
                ids += [n["id"] for n in self.list.nodes]
            ids += [r["id"] for r in self._pairings]
            out, seen = [], set()
            for i in ids:
                if i in seen:
                    continue
                seen.add(i)
                for key, _via, name, _carry in self._candidates(i)[:1]:
                    out.append((i, name, key))
            return out

    # -- the deny list as it spreads (design section 9): a deny is taken from an approved peer, an allow never is --
    def deny_digest(self):
        """A short digest of the denied keys, carried in every peer call: a peer that sees another one exchanges entries."""
        keys = sorted(self.denied_keys())
        return hashlib.sha256("\n".join(keys).encode("ascii")).hexdigest()[:16]

    def deny_entries(self):
        with self.lock:
            return [dict(r) for r in self._deny]

    def take_denies(self, entries, by):
        """Take the denies an approved peer reported. Only additions: nothing here removes an entry, and a peer cannot make
        this node approve anybody. A deny for this node's own key is not stored; the status says so. Returns the names added."""
        added = []
        if not isinstance(entries, list):
            return added
        with self.lock:
            own = self.own_key()
            for e in entries[:MAX_DENY]:
                if not isinstance(e, dict) or not isinstance(e.get("key"), str) or not fleetlist.KEY_WORD.fullmatch(e["key"]) \
                        or not isinstance(e.get("name"), str):
                    continue
                if e["key"] == own:
                    self.removed = True
                    continue
                if e["key"] in self.denied_keys() or len(self._deny) >= MAX_DENY:
                    continue
                self._deny.append({"key": e["key"], "name": e["name"][:64], "by": str(by)[:64], "n": 1 + max([r["n"] for r in self._deny] or [0])})
                added.append(e["name"][:64])
            if added:
                self._save_deny()
                self.event("handover", "%s removed %s from this cluster." % (by, " and ".join(added) if len(added) < 3 else "%s and %d more" % (added[0], len(added) - 1)))
        return added

    def list_text(self):
        """(list text, signature text) of the list in use, for a peer that is behind; None when there is none."""
        with self.lock:
            if self.list is None or self.sig_text is None:
                return None
            return self.list.body.decode("utf-8"), self.sig_text

    # -- what the Owner sees --
    def status(self):
        """The Owner's view: names, serial, ids and this node's own fingerprint. No key of another node, no signature."""
        with self.lock:
            self._maybe_reload()
            node_id, name = self.own()
            key = self.own_key()
            lst = self.list
            problem = self.problem
            if lst is not None:
                row = lst.node(node_id)
                if row is None or key is None or row["key"] != key:
                    problem = ("the held list does not name this node with the key it uses now, so it approves nobody through the list "
                               "until a new signed list is loaded")
            return {
                "accept": self.accepting(),
                "self": {"id": node_id, "name": name, "fingerprint": fingerprint(key) if key else None},
                "list": None if lst is None else {
                    "fleet": lst.fleet, "name": lst.name, "serial": lst.serial, "issued": lst.issued,
                    "nodes": [{"id": n["id"], "name": n["name"], "carry": n["carry"]} for n in sorted(lst.nodes, key=lambda n: n["name"].lower())]},
                "list_problem": problem,
                "accepted": [{"id": r["id"], "name": r["name"], "carry": r["carry"]} for r in self._pairings],
                "removed": [{"name": r["name"], "by": r["by"]} for r in self._deny],
                "removed_from_cluster": self.removed,
                "unknown_calls": self.unknown_calls,
            }

    # -- a factory reset --
    def wipe(self):
        """Everything here goes. Returns the problems."""
        problems = []
        with self.lock:
            self.list, self.problem, self.floor, self._deny, self._pairings = None, None, None, [], []
            try:
                present = os.listdir(self.folder)
            except OSError:
                present = []
            for name in sorted(n for n in present if any(n == f or n.startswith(f + ".") for f in FILES)):      # also .prev, .new, .new.tmp
                if not self._unlink(name):
                    problems.append("could not remove %s" % name)
        return problems
