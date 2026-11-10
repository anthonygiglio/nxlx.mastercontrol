# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The fleet list: the file format, the signed bytes, and the check a node makes before it believes a list (D132,
design/node-handover_2026-11-09.md section 3.3).

A fleet list names the nodes you trust to carry your devices between them. You make and sign it on your own computer
with the root (tools/boxcert.py fleet ...); a node only ever READS it, with this module: it checks the root's
signature with the system's `openssl`, as pvj/httpsbox.py does for a certificate, and refuses anything else. Nothing
here signs, and nothing here needs the root's private key.

Two files go together:

    fleet.json   the list, as bytes (below)
    fleet.sig    base64 of the root's signature (ECDSA or RSA, SHA-256) over the SIGNED BYTES

    {"v": 1, "fleet": "<16 hex>", "name": "Anthony's rig", "serial": 3, "issued": "2026-11-09",
     "nodes": [{"id": "<32 hex>", "name": "node-01", "key": "<64 hex>", "carry": "full"}, ...],
     "clear": ["<64 hex>", ...]}            ("clear" is left out when empty)

    SIGNED BYTES = b"nxlx-fleet-v1\\n" + fleet id + b"\\n" + serial in decimal + b"\\n" + the exact bytes of fleet.json

The prefix means a signature the root made for anything else (a certificate, a different file) cannot pass as a
list. The fleet id and the serial are inside the signed bytes twice (in the prefix and in the JSON, and the two must
agree), so a list cannot be moved to another fleet and a serial cannot be turned back. `key` is the SHA-256 of the
node's public key in DER form (SubjectPublicKeyInfo): the same value `openssl x509 -pubkey | openssl pkey -pubin
-outform DER | openssl dgst -sha256` prints, and the same for every certificate that reuses the key. `carry` is the
highest role the node gives to a device that arrives from another node on the list: `full` (Owner), `live`
(Operator) or `none` (takes nobody in). Dates are for people to read and are never judged here.

Standard library only, no import from the rest of pvj (tools/boxcert.py loads this file by its path).
"""
import base64
import binascii
import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata

PREFIX = b"nxlx-fleet-v1\n"
VERSION = 1
MAX_BYTES = 65536                  # the file; a list of 64 nodes and 256 clears is about 30 KB
MAX_SIG_TEXT = 2048                # the signature file, in text
MAX_NODES = 64
MAX_CLEAR = 256
MAX_SERIAL = 4294967295
CARRY = ("full", "live", "none")
FLEET_WORD = re.compile(r"[0-9a-f]{16}")
ID_WORD = re.compile(r"[0-9a-f]{32}")              # the node id of pvj/fleet.py
KEY_WORD = re.compile(r"[0-9a-f]{64}")
NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")      # the host name of pvj/fleet.py
DATE_WORD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TOP_KEYS = {"v", "fleet", "name", "serial", "issued", "nodes", "clear"}
NODE_KEYS = {"id", "name", "key", "carry"}
CARD_KEYS = ("id", "name", "key")


class FleetError(ValueError):
    """A list, a signature or a card that cannot be used. `code` is a short word for code and tests to look at; the
    message is the sentence a person reads."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# --- reading the bytes -------------------------------------------------------------------------------------------------
def _pairs(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise FleetError("bad-list", "the list names %r twice" % k)
        seen[k] = v
    return seen


def _no_constant(name):
    raise FleetError("bad-list", "the list holds %s, which is not a value a list may hold" % name)


def _json(body, what):
    if not isinstance(body, (bytes, bytearray)):
        raise FleetError("bad-list", "the %s must be bytes" % what)
    if len(body) > MAX_BYTES:
        raise FleetError("too-big", "the %s is %d bytes; the most a list may be is %d" % (what, len(body), MAX_BYTES))
    try:
        text = bytes(body).decode("utf-8")
    except UnicodeDecodeError:
        raise FleetError("bad-list", "the %s is not UTF-8 text" % what)
    if text.startswith("﻿"):
        raise FleetError("bad-list", "the %s starts with a byte order mark" % what)
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_no_constant)
    except FleetError:
        raise
    except (ValueError, RecursionError):
        raise FleetError("bad-list", "the %s is not valid JSON" % what)


def _plain_text(value, what, limit):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise FleetError("bad-list", "%s must be text of 1 to %d characters" % (what, limit))
    if value != value.strip() or any(unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") for c in value):
        raise FleetError("bad-list", "%s must not start or end with a space or hold control characters" % what)
    return value


def _int(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise FleetError("bad-list", "%s must be a whole number from %d to %d" % (what, low, high))
    return value


def _word(value, pattern, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FleetError("bad-list", "%s is not in the form a list needs" % what)
    return value


class FleetList:
    """A list that has been read and found well formed. Not yet believed: only `verify` returns one that was signed."""

    def __init__(self, data, body):
        self.fleet, self.name, self.serial, self.issued = data["fleet"], data["name"], data["serial"], data["issued"]
        self.nodes = [dict(n) for n in data["nodes"]]
        self.clear = list(data.get("clear", []))
        self.body = bytes(body)
        self.digest = hashlib.sha256(self.body).hexdigest()

    def node(self, node_id):
        for n in self.nodes:
            if n["id"] == node_id:
                return n
        return None

    def node_by_key(self, key):
        for n in self.nodes:
            if n["key"] == key:
                return n
        return None

    def as_dict(self):
        out = {"v": VERSION, "fleet": self.fleet, "name": self.name, "serial": self.serial, "issued": self.issued,
               "nodes": [dict(n) for n in self.nodes]}
        if self.clear:
            out["clear"] = list(self.clear)
        return out


def check_data(data):
    """The rules for what a list may say, on the parsed JSON. Raises FleetError; returns the same data."""
    if not isinstance(data, dict):
        raise FleetError("bad-list", "the list is not an object")
    extra = sorted(set(data) - TOP_KEYS)
    if extra:
        raise FleetError("bad-list", "the list has a field this version does not know: %s" % ", ".join(repr(str(k))[:40] for k in extra[:3]))
    missing = sorted(TOP_KEYS - {"clear"} - set(data))
    if missing:
        raise FleetError("bad-list", "the list lacks: %s" % ", ".join(missing))
    if type(data["v"]) is not int or data["v"] != VERSION:
        raise FleetError("bad-version", "the list is version %r; this node reads version %d" % (data["v"], VERSION))
    _word(data["fleet"], FLEET_WORD, "the fleet id")
    _plain_text(data["name"], "the fleet's name", 64)
    _int(data["serial"], "the serial", 1, MAX_SERIAL)
    issued = _word(data["issued"], DATE_WORD, "the date")
    try:
        datetime.date.fromisoformat(issued)
    except ValueError:
        raise FleetError("bad-list", "the date %r is not a day" % issued)
    nodes = data["nodes"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise FleetError("bad-list", "a list names 1 to %d nodes" % MAX_NODES)
    ids, keys, names = set(), set(), set()
    for n in nodes:
        if not isinstance(n, dict) or set(n) != NODE_KEYS:
            raise FleetError("bad-list", "every node has exactly: id, name, key, carry")
        _word(n["id"], ID_WORD, "a node id")
        _word(n["name"], NODE_NAME, "a node name")
        _word(n["key"], KEY_WORD, "a node's key hash")
        if n["carry"] not in CARRY or not isinstance(n["carry"], str):
            raise FleetError("bad-list", "carry is %s, not one of: %s" % (repr(n["carry"])[:30], ", ".join(CARRY)))
        if n["id"] in ids:
            raise FleetError("duplicate", "the node id %s is on the list twice" % n["id"])
        if n["key"] in keys:
            raise FleetError("duplicate", "the key %s is on the list twice (two nodes cannot share a key)" % n["key"][:16])
        if n["name"].lower() in names:
            raise FleetError("duplicate", "the name %s is on the list twice" % n["name"])
        ids.add(n["id"]), keys.add(n["key"]), names.add(n["name"].lower())
    if "clear" in data:
        clear = data["clear"]
        if not isinstance(clear, list) or not 1 <= len(clear) <= MAX_CLEAR:
            raise FleetError("bad-list", "clear holds 1 to %d key hashes (leave the field out when there are none)" % MAX_CLEAR)
        for k in clear:
            _word(k, KEY_WORD, "a key hash in clear")
        if len(set(clear)) != len(clear):
            raise FleetError("duplicate", "clear names a key twice")
    return data


def parse(body):
    """The bytes of a fleet.json as a FleetList, or FleetError. This is NOT proof the root made it: use `verify`."""
    return FleetList(check_data(_json(body, "list")), body)


def canonical(data):
    """The bytes the tool writes for a list: the same data always gives the same bytes. Checks the rules first."""
    data = {k: v for k, v in data.items() if not (k == "clear" and v == [])}        # a working copy may carry an empty one
    check_data(json.loads(json.dumps(data)))
    ordered = {"v": VERSION, "fleet": data["fleet"], "name": data["name"], "serial": data["serial"], "issued": data["issued"],
               "nodes": [{k: n[k] for k in ("id", "name", "key", "carry")} for n in sorted(data["nodes"], key=lambda n: (n["name"].lower(), n["id"]))]}
    if data.get("clear"):
        ordered["clear"] = sorted(data["clear"])
    return (json.dumps(ordered, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def signed_bytes(fleet, serial, body):
    """What the root signs: the prefix, the fleet id, the serial, then the list's own bytes."""
    if not FLEET_WORD.fullmatch(fleet or "") or isinstance(serial, bool) or not isinstance(serial, int) or serial < 1:
        raise FleetError("bad-list", "no signed bytes for that fleet id and serial")
    return PREFIX + fleet.encode("ascii") + b"\n" + str(serial).encode("ascii") + b"\n" + bytes(body)


def signed_bytes_of(body):
    """The signed bytes of a list's bytes, taking the fleet id and serial from the list itself."""
    lst = parse(body)
    return signed_bytes(lst.fleet, lst.serial, body)


# --- the signature -----------------------------------------------------------------------------------------------------
def _openssl(exe, args, data=None, timeout=20):
    try:
        return subprocess.run([exe] + args, input=data, capture_output=True, timeout=timeout)
    except OSError:
        raise FleetError("no-openssl", "openssl could not be run, so the signature cannot be checked")
    except subprocess.TimeoutExpired:
        raise FleetError("no-openssl", "openssl did not finish checking the signature")


def decode_signature(sig_text):
    """The raw signature bytes out of the text of a fleet.sig (base64, line breaks and spaces allowed)."""
    if isinstance(sig_text, (bytes, bytearray)):
        try:
            sig_text = bytes(sig_text).decode("ascii")
        except UnicodeDecodeError:
            raise FleetError("bad-signature", "the signature file is not base64 text")
    if not isinstance(sig_text, str) or len(sig_text) > MAX_SIG_TEXT:
        raise FleetError("bad-signature", "the signature file is missing or too long")
    packed = "".join(sig_text.split())
    if not packed:
        raise FleetError("bad-signature", "the signature file is empty")
    try:
        raw = base64.b64decode(packed, validate=True)
    except (binascii.Error, ValueError):
        raise FleetError("bad-signature", "the signature file is not base64 text")
    if not 8 <= len(raw) <= 1024:
        raise FleetError("bad-signature", "the signature is not the size of a signature")
    return raw


def signature_ok(message, sig_text, root_pem, openssl="openssl"):
    """Did the key of this root certificate sign exactly these bytes (SHA-256)? `root_pem` is the text of the pinned
    root's certificate (the node's root.pem). Fixed arguments, no shell, the message on stdin and nothing else of the
    network's in a command line."""
    raw = decode_signature(sig_text)
    if not isinstance(root_pem, str) or "-----BEGIN CERTIFICATE-----" not in root_pem or len(root_pem) > 65536:
        raise FleetError("no-root", "there is no root certificate to check the list against")
    r = _openssl(openssl, ["x509", "-noout", "-pubkey"], root_pem.encode("utf-8"))
    pub = r.stdout
    if r.returncode or b"-----BEGIN PUBLIC KEY-----" not in pub:
        raise FleetError("no-root", "the root certificate could not be read")
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        pub_path, sig_path = os.path.join(tmp, "root.pub"), os.path.join(tmp, "list.sig")
        with open(pub_path, "wb") as f:
            f.write(pub)
        with open(sig_path, "wb") as f:
            f.write(raw)
        r = _openssl(openssl, ["dgst", "-sha256", "-verify", pub_path, "-signature", sig_path], message)
    return r.returncode == 0 and b"Verified OK" in r.stdout


def verify(body, sig_text, root_pem, openssl="openssl", fleet=None, current=None):
    """Believe a list only if it is well formed, signed by the root, of the right fleet and not older than the one held.

    body, sig_text   the bytes of fleet.json and the text of fleet.sig
    root_pem         the text of the root certificate the node pins
    fleet            the fleet id this node belongs to, or None for a node that holds no list yet (the first list
                     it takes sets the fleet)
    current          the list the node holds now (a FleetList), or None

    Returns the FleetList. Raises FleetError; `.code` tells why: too-big, bad-list, bad-version, duplicate,
    bad-signature, no-root, no-openssl, not-signed (the signature does not match: a changed byte, another root or
    another prefix), other-fleet, older (a serial below the one held), same-serial (the same serial with other
    contents). The same bytes again are accepted (a node told twice)."""
    lst = parse(body)
    if not signature_ok(signed_bytes(lst.fleet, lst.serial, body), sig_text, root_pem, openssl):
        raise FleetError("not-signed", "the signature is not from this node's root, or the list was changed after it was signed")
    want = fleet or (current.fleet if current is not None else None)
    if want is not None and lst.fleet != want:
        raise FleetError("other-fleet", "this list is for another fleet (%s), not this node's (%s)" % (lst.fleet, want))
    if current is not None:
        if lst.serial < current.serial:
            raise FleetError("older", "this list is serial %d and the node holds serial %d" % (lst.serial, current.serial))
        if lst.serial == current.serial and lst.digest != current.digest:
            raise FleetError("same-serial", "this list has serial %d like the one held, but says something else" % lst.serial)
    return lst


# --- a node's key hash and its card ------------------------------------------------------------------------------------
def _children(b, i=0, end=None):
    """The (tag, start of the whole item, start of its value, end) of each DER item between i and end."""
    end = len(b) if end is None else end
    out = []
    while i < end:
        start = i
        if i + 2 > end:
            raise ValueError("short")
        tag, length, i = b[i], b[i + 1], i + 2
        if length & 0x80:
            n = length & 0x7F
            if n == 0 or n > 4 or i + n > end:
                raise ValueError("length")
            length, i = int.from_bytes(b[i:i + n], "big"), i + n
        if i + length > end:
            raise ValueError("short")
        out.append((tag, start, i, i + length))
        i += length
    return out


def key_hash(cert_der):
    """The SHA-256 (64 hex digits) of the public key in a DER certificate: of its SubjectPublicKeyInfo, as written
    there. Two certificates for one key give one hash, so a renewal that keeps the key keeps the hash."""
    try:
        (tag, _s, v0, v1), = _children(cert_der)
        if tag != 0x30:
            raise ValueError("not a sequence")
        tbs = _children(cert_der, v0, v1)[0]
        fields = _children(cert_der, tbs[2], tbs[3])
        i = 1 if fields and fields[0][0] == 0xA0 else 0
        tag, start, _v0, stop = fields[i + 5]                    # version, serial, algorithm, issuer, validity, subject, KEY
        if tag != 0x30:
            raise ValueError("no key")
        return hashlib.sha256(cert_der[start:stop]).hexdigest()
    except (ValueError, IndexError):
        raise FleetError("bad-cert", "that is not a certificate with a public key in it")


def certificate_der(pem_text):
    """The DER of the first certificate in a PEM text (the node's own certificate comes first in its file)."""
    lines, body, inside = (pem_text or "").splitlines(), [], False
    for line in lines:
        line = line.strip()
        if line == "-----BEGIN CERTIFICATE-----":
            body, inside = [], True
        elif line == "-----END CERTIFICATE-----" and inside:
            try:
                return base64.b64decode("".join(body), validate=True)
            except (binascii.Error, ValueError):
                break
        elif inside and line:
            body.append(line)
    raise FleetError("bad-cert", "no certificate found in that text")


def make_card(node_id, name, cert_der):
    """What a node tells the tool about itself (GET /api/fleet/card, which a later slice adds to the node): the
    facts mDNS already announces, plus the hash of its key. Nothing secret."""
    return {"v": VERSION, "id": node_id, "name": name, "key": key_hash(cert_der)}


def parse_card(body):
    """A card as bytes to {id, name, key}. Fields it does not know are ignored; the three it needs must be sound."""
    if isinstance(body, (bytes, bytearray)) and len(body) > 4096:
        raise FleetError("bad-card", "a node's card is a few hundred bytes; this one is longer")
    try:
        data = _json(body, "card")
    except FleetError as e:
        raise FleetError("bad-card", e.message)
    if not isinstance(data, dict) or any(k not in data for k in CARD_KEYS):
        raise FleetError("bad-card", "the card lacks its id, name or key")
    try:
        return {"id": _word(data["id"], ID_WORD, "the card's id"), "name": _word(data["name"], NODE_NAME, "the card's name"),
                "key": _word(data["key"], KEY_WORD, "the card's key hash")}
    except FleetError as e:
        raise FleetError("bad-card", e.message)
