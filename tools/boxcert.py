#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""boxcert: your own root certificate, and a certificate for each box signed with it (D79, docs/HTTPS.md).

For the owner's computer, never for a box. Needs Python 3 and the system's `openssl` (macOS: LibreSSL in /usr/bin or
OpenSSL from Homebrew, both work; Linux; Windows with OpenSSL on the path). Nothing else.

  boxcert.py make-root [--dir DIR] [--name NAME] [--allow-name DOMAIN ...] [--no-constraints]
  boxcert.py sign REQUEST.csr [--dir DIR] [--name NAME ...] [--address IP ...] [--drop NAME ...] [--days 397] [--out FILE]
  boxcert.py renew REQUEST.csr ...        the same as sign: the box keeps its key and its request
  boxcert.py show CERT.pem                what a certificate says and when it ends
  boxcert.py verify CERT.pem [--dir DIR]  does the root in DIR vouch for it
  boxcert.py list [--dir DIR]             what this root has signed, and what is due
  boxcert.py root [--dir DIR]             the root's SHA-256 fingerprint, to compare with the box's page once

  Automatic certificates (D139, docs/HTTPS.md "Automatic certificates"): a signer under the root, run by a small service.
  boxcert.py make-signer [--dir DIR]      make the "NXLX node signer" (once per root); writes DIR/signer/ for the signing computer
  boxcert.py move-root --to NEWDIR [--dir OLDDIR]   a message signed by the OLD root that tells each node to trust the new
                                          one; written into NEWDIR/signer/, where the signing service hands it to the nodes

  The fleet list: which of your nodes may open each other without pairing again (D132). Sign it here, load it on a node.
  boxcert.py fleet new --name NAME        start a list (once)
  boxcert.py fleet add HOST --carry full|live|none     add or change a node: asks it for its card over https and trusts
                                          only your root; or --card FILE, or --cert FILE --id ID --name NAME
  boxcert.py fleet remove NODE            take a node (name or id) off the working list
  boxcert.py fleet clear NODE|KEYHASH ... allow a removed node again, for every node, in the next signed list
  boxcert.py fleet sign [--serial N]      sign the working list with the root (asks the passphrase; serial goes up by one)
  boxcert.py fleet show                   the signed list and what the working copy changes
  boxcert.py fleet verify [--list F --sig F]    does your root's signature hold (also for a list taken off a node)
  boxcert.py fleet push HOST [--port N] [--token-file F]    send the signed list to a node over https (asks its PIN once)

The folder (default ~/nxlx-root-ca) holds root.key (encrypted with your passphrase), root.pem (public: this is what
your devices install) and signed.json (box, names, serial, start, end). Back the folder up. Never put root.key on
a box. Every extension goes through a configuration file, because LibreSSL has no -addext.
"""

import argparse
import base64
import datetime
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "nxlx-root-ca")
ROOT_KEY, ROOT_PEM, SIGNED = "root.key", "root.pem", "signed.json"
ROOT_DAYS = 7300                  # twenty years: the root is renewed by making a new one (docs/HTTPS.md)
LEAF_DAYS, MAX_LEAF_DAYS = 397, 825   # Apple: 825 at most for every TLS server certificate; 397 keeps a yearly rhythm
ROOT_CURVE, BOX_CURVE = "secp384r1", "prime256v1"
PRIVATE_RANGES = ("10.0.0.0/255.0.0.0", "172.16.0.0/255.240.0.0", "192.168.0.0/255.255.0.0", "169.254.0.0/255.255.0.0")
SIGNER_RANGES = PRIVATE_RANGES[:3]  # the signer: .local names and the three private ranges, nothing link-local (D139)
SIGNER_DIR, SIGNER_KEY, SIGNER_PEM, SIGNER_PINS = "signer", "signer.key", "signer.pem", "signed.json"
SIGNER_DAYS = 825
SIGNER_NAME = "NXLX node signer"
ROOT_MOVE, ROOT_MOVE_SIG = "root-move.json", "root-move.sig"
ROOT_MOVE_MAGIC = b"nxlx-root-move-v1\n"     # what the old root signs: these bytes, then the exact bytes of root-move.json
DEFAULT_ALLOWED = ("local",)       # "local" permits every name under .local (RFC 5280: labels may be added on the left)
SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


class BoxcertError(Exception):
    pass


def openssl_bin(explicit=None):
    exe = explicit or os.environ.get("BOXCERT_OPENSSL") or shutil.which("openssl")
    if not exe or not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        raise BoxcertError("openssl was not found. On a Mac and on Linux it comes with the system; on Windows install "
                           "OpenSSL (or use the one in Git for Windows) and put it on the path, or set BOXCERT_OPENSSL.")
    return exe


def run(exe, args, passphrase=None, timeout=120):
    """One openssl command with a fixed argument list, never a shell. A passphrase goes in on stdin, never on the
    command line (where every other program on the machine could read it)."""
    try:
        r = subprocess.run([exe] + args, input=(passphrase + "\n") if passphrase is not None else None,
                           capture_output=True, text=True, timeout=timeout)
    except OSError as e:
        raise BoxcertError("could not run openssl: %s" % e)
    except subprocess.TimeoutExpired:
        raise BoxcertError("openssl did not finish")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _fail(what, err):
    err = err.strip().splitlines()
    tail = err[-1] if err else "no message"
    if "bad decrypt" in tail or "bad password" in tail or "wrong pass" in tail.lower() or "Decrypt error" in tail or "unable to load" in tail:
        tail = "wrong passphrase (or the key file is damaged): " + tail
    raise BoxcertError("%s: %s" % (what, tail))


def write_private(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def valid_dns(name):
    return bool(re.fullmatch(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*", name))


def valid_ip(text):
    import ipaddress
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


# --- the root ----------------------------------------------------------------------------------------------------
def root_config(name, allowed, constraints, ranges=PRIVATE_RANGES):
    # pathlen:1 since D139: one signer may sit between the root and a node. A root of pathlen:0 (every root made
    # before 2026-11-14) cannot have one: devices refuse "path length constraint exceeded".
    lines = ["[req]", "distinguished_name = dn", "prompt = no", "[dn]", "CN = %s" % name, "[v3_ca]",
             "basicConstraints = critical, CA:TRUE, pathlen:1", "keyUsage = critical, keyCertSign, cRLSign",
             "subjectKeyIdentifier = hash"]
    if constraints:
        parts = ["permitted;DNS:%s" % d for d in allowed] + ["permitted;IP:%s" % r for r in ranges]
        lines.append("nameConstraints = critical, " + ", ".join(parts))
    return "\n".join(lines) + "\n"


def valid_range(text):
    """An IPv4 range as openssl writes it in a constraint: address/mask, both dotted."""
    import ipaddress
    net, _, mask = text.partition("/")
    try:
        return str(ipaddress.ip_network("%s/%s" % (net, mask), strict=True).with_netmask).replace("/", "/")
    except ValueError:
        return None


def make_root(exe, folder, name, allowed, constraints, ask, out=print, ranges=PRIVATE_RANGES):
    for bad in [a for a in allowed if not valid_dns(a)]:
        raise BoxcertError("not a domain name: %r" % bad)
    for bad in [r for r in ranges if not valid_range(r)]:
        raise BoxcertError("not an address range (write it as 192.168.0.0/255.255.0.0): %r" % bad)
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,48}", name):
        raise BoxcertError("the root's name may hold letters, digits, spaces, dots, dashes and underscores (48 at most)")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    key, pem = os.path.join(folder, ROOT_KEY), os.path.join(folder, ROOT_PEM)
    for p in (key, pem):
        if os.path.lexists(p):
            raise BoxcertError("%s exists already. A root is made once; to start over, move the old folder away first "
                               "(and read 'If the root key is lost' in docs/HTTPS.md)." % p)
    p1 = ask("Passphrase for the root key (you will type it each time you sign): ")
    if len(p1) < 8:
        raise BoxcertError("use a passphrase of at least 8 characters")
    if ask("The same passphrase again: ") != p1:
        raise BoxcertError("the two passphrases differ; nothing was made")
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        plain, cfg = os.path.join(tmp, "plain.key"), os.path.join(tmp, "root.cnf")
        with open(cfg, "w") as f:
            f.write(root_config(name, allowed, constraints, ranges))
        code, _, err = run(exe, ["ecparam", "-name", ROOT_CURVE, "-genkey", "-noout", "-out", plain])
        if code:
            _fail("could not make the root key", err)
        os.chmod(plain, 0o600)
        # PKCS#8 with AES-256 named outright: the default cipher differs between LibreSSL and OpenSSL versions
        code, encrypted, err = run(exe, ["pkcs8", "-topk8", "-v2", "aes-256-cbc", "-in", plain, "-passout", "stdin"], p1)
        if code or "ENCRYPTED PRIVATE KEY" not in encrypted:
            _fail("could not encrypt the root key", err)
        write_private(key, encrypted)                       # 0600 from the first byte, never a wider mode for a moment
        code, _, err = run(exe, ["req", "-x509", "-new", "-key", key, "-passin", "stdin", "-sha256", "-days", str(ROOT_DAYS),
                                 "-config", cfg, "-extensions", "v3_ca", "-out", pem], p1)
        if code:
            os.unlink(key)
            _fail("could not make the root certificate", err)
        with open(plain, "r+b") as f:          # the clear key: overwritten before the folder goes
            f.write(b"\0" * os.path.getsize(plain))
    if not os.path.exists(os.path.join(folder, SIGNED)):
        with open(os.path.join(folder, SIGNED), "w") as f:
            json.dump([], f)
    out("Root made.")
    out("  private key : %s  (encrypted with your passphrase; BACK IT UP; NEVER put it on a box)" % key)
    out("  public root : %s  (this is what each phone, tablet and laptop installs once)" % pem)
    out("  SHA-256     : %s" % read_cert(exe, pem)["fingerprint"])
    out("  constraints : %s" % ("names under %s and addresses in %s only" % (", ".join("." + a for a in allowed), ", ".join(ranges)) if constraints else "none"))
    out("Next: boxcert.py --dir %s make-signer   (automatic certificates), or on the box's panel, System > Secure connection, "
        "download the request, then: boxcert.py sign <the request>" % folder)
    return key, pem


# --- reading requests and certificates ----------------------------------------------------------------------------
def names_in(text):
    """The DNS names and addresses of a Subject Alternative Name line as `openssl -text` prints it (both OpenSSL and
    LibreSSL: 'DNS:a, DNS:b, IP Address:1.2.3.4'). Returns (dns names, addresses), lowercased, in order."""
    m = re.search(r"Subject Alternative Name:\s*\n\s*(.+)", text)
    if not m:
        return [], []
    dns, ips = [], []
    for part in m.group(1).split(","):
        part = part.strip()
        if part.lower().startswith("dns:"):
            dns.append(part[4:].strip().lower())
        elif part.lower().startswith("ip address:") or part.lower().startswith("ip:"):
            ips.append(part.split(":", 1)[1].strip())
    return dns, ips


def common_name(text):
    m = re.search(r"Subject:.*?CN\s*=\s*([^,/\n]+)", text)
    return m.group(1).strip() if m else ""


def read_request(exe, path):
    code, text, err = run(exe, ["req", "-in", path, "-noout", "-text", "-verify"])
    if code:
        _fail("%s is not a certificate request the box made" % path, err)
    dns, ips = names_in(text)
    return {"cn": common_name(text), "dns": dns, "ips": ips, "key": "EC" if re.search(r"id-ecPublicKey|EC Public Key|ecPublicKey", text) else
            ("RSA" if "rsaEncryption" in text or "RSA Public Key" in text else "?")}


def parse_time(line):
    """'notAfter=Oct 10 12:00:00 2027 GMT' to a UTC datetime."""
    value = line.split("=", 1)[1].strip()
    return datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y GMT").replace(tzinfo=datetime.timezone.utc)


def new_serial(digits=None):
    """32 hex digits for -set_serial: a positive 128-bit number whose first digit is 1 to 7, so it is 16 bytes in the
    file and openssl prints all 32 digits. (The first digit was masked with 7 alone, so one serial in 8 began with 0
    and one in 128 with 00, which openssl prints as 30 digits: CI met it on 2026-10-18.)"""
    digits = digits or secrets.token_hex(16)
    return "%x" % (1 + int(digits[0], 16) % 7) + digits[1:]


def read_cert(exe, path):
    code, text, err = run(exe, ["x509", "-in", path, "-noout", "-text", "-startdate", "-enddate", "-serial", "-issuer", "-fingerprint", "-sha256"])
    if code:
        _fail("%s is not a certificate" % path, err)
    dns, ips = names_in(text)
    start = end = None
    serial = issuer = fp = ""
    for line in text.splitlines():
        if line.startswith("notBefore="):
            start = parse_time(line)
        elif line.startswith("notAfter="):
            end = parse_time(line)
        elif line.startswith("serial="):
            serial = line.split("=", 1)[1].strip().lower()
        elif line.startswith("issuer="):
            issuer = line.split("=", 1)[1].strip()
        elif "Fingerprint=" in line and line.upper().startswith("SHA256"):
            h = line.split("=", 1)[1].replace(":", "").strip().lower()
            fp = " ".join(h[i:i + 4] for i in range(0, len(h), 4))     # the same groups of four the box's page shows
    return {"cn": common_name(text), "dns": dns, "ips": ips, "start": start, "end": end, "serial": serial, "issuer": issuer, "fingerprint": fp,
            "ca": "CA:TRUE" in text, "server_auth": "TLS Web Server Authentication" in text or SERVER_AUTH in text,
            "constraints": "Name Constraints" in text}


# --- signing --------------------------------------------------------------------------------------------------------
def leaf_config(dns, ips):
    san = ", ".join(["DNS:%s" % d for d in dns] + ["IP:%s" % i for i in ips])
    return "\n".join(["[v3_leaf]", "basicConstraints = CA:FALSE", "keyUsage = critical, digitalSignature",
                      "extendedKeyUsage = serverAuth, clientAuth", "subjectKeyIdentifier = hash", "authorityKeyIdentifier = keyid",
                      "subjectAltName = " + san]) + "\n"


def within_constraints(root, dns, ips):
    """Names and addresses the root's constraints would refuse, from the root's own text (empty when it has none)."""
    import ipaddress
    allowed_dns, allowed_ip = [], []
    m = re.search(r"Name Constraints:.*?Permitted:\s*\n((?:\s+\S.*\n?)+)", root.get("text", ""), re.S)
    if not m:
        return []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("DNS:"):
            allowed_dns.append(line[4:].strip().lower().lstrip("."))
        elif line.startswith("IP:"):
            net, _, mask = line[3:].strip().partition("/")
            try:
                allowed_ip.append(ipaddress.ip_network("%s/%s" % (net, mask), strict=False))
            except ValueError:
                pass
    bad = [d for d in dns if not any(d == a or d.endswith("." + a) for a in allowed_dns)]
    for i in ips:
        ip = valid_ip(i)
        if ip is None or not any(ip in n for n in allowed_ip):
            bad.append(i)
    return bad


def sign(exe, folder, request, add_names, add_ips, drop, days, out_path, ask, out=print, now=None):
    key, pem = os.path.join(folder, ROOT_KEY), os.path.join(folder, ROOT_PEM)
    for p in (key, pem):
        if not os.path.isfile(p):
            raise BoxcertError("%s is missing: make the root first (boxcert.py make-root), or point --dir at its folder" % p)
    if not isinstance(days, int) or not 1 <= days <= MAX_LEAF_DAYS:
        raise BoxcertError("--days must be from 1 to %d (Apple refuses longer certificates)" % MAX_LEAF_DAYS)
    req = read_request(exe, request)
    dns = [d for d in req["dns"] if d not in drop]
    ips = [i for i in req["ips"] if i not in drop]
    for n in add_names:
        n = n.lower().rstrip(".")
        if not valid_dns(n):
            raise BoxcertError("not a host name: %r" % n)
        if n not in dns:
            dns.append(n)
    for i in add_ips:
        ip = valid_ip(i)
        if ip is None:
            raise BoxcertError("not an address: %r" % i)
        if str(ip) not in ips:
            ips.append(str(ip))
    if not dns and not ips:
        raise BoxcertError("the request names nothing and nothing was added: give at least --name <the box's .local name>")
    if req["cn"] and req["cn"].lower() not in dns and req["cn"].lower() not in drop and valid_dns(req["cn"].lower()):
        dns.insert(0, req["cn"].lower())
    v6 = [i for i in ips if ":" in i]
    if v6:
        out("Note: IPv6 addresses (%s) are left out; the root's constraints allow private IPv4 ranges only." % ", ".join(v6))
        ips = [i for i in ips if ":" not in i]
    code, root_text, err = run(exe, ["x509", "-in", pem, "-noout", "-text"])
    if code:
        _fail("could not read the root", err)
    refused = within_constraints({"text": root_text}, dns, ips)
    if refused:
        # one name outside the constraints makes a device refuse the WHOLE certificate (openssl verify: "permitted
        # subtree violation"), so such names are left out, not kept with a warning
        out("Left out: %s. The root's constraints allow names under .local (and what make-root was told) and private "
            "addresses only; a device that enforces them would refuse the whole certificate for these." % ", ".join(refused))
        dns = [d for d in dns if d not in refused]
        ips = [i for i in ips if i not in refused]
        if not dns and not ips:
            raise BoxcertError("nothing is left to sign for")
    box = re.sub(r"[^A-Za-z0-9-]", "", (dns[0] if dns else "box").split(".")[0])[:40] or "box"
    out_path = out_path or os.path.join(folder, "%s-cert.pem" % box)
    if os.path.lexists(out_path) and not os.path.isfile(out_path):
        raise BoxcertError("%s is not a plain file" % out_path)
    out("Signing for: %s" % ", ".join(dns + ips))
    passphrase = ask("Passphrase of the root key: ")
    code, _, err = run(exe, ["pkey", "-in", key, "-passin", "stdin", "-noout"], passphrase)
    if code:
        raise BoxcertError("wrong passphrase (or %s is damaged); nothing was signed" % key)
    serial = new_serial()
    with tempfile.TemporaryDirectory() as tmp:
        cfg, leaf = os.path.join(tmp, "leaf.cnf"), os.path.join(tmp, "leaf.pem")
        with open(cfg, "w") as f:
            f.write(leaf_config(dns, ips))
        code, _, err = run(exe, ["x509", "-req", "-in", request, "-CA", pem, "-CAkey", key, "-passin", "stdin",
                                 "-set_serial", "0x" + serial, "-days", str(days), "-sha256",
                                 "-extfile", cfg, "-extensions", "v3_leaf", "-out", leaf], passphrase)
        if code:
            _fail("signing failed", err)
        info = read_cert(exe, leaf)
        with open(leaf) as f, open(pem) as r:
            bundle = f.read().strip() + "\n" + r.read().strip() + "\n"
    tmp_out = out_path + ".tmp"
    with open(tmp_out, "w") as f:
        f.write(bundle)
    os.replace(tmp_out, out_path)
    entry = {"box": box, "names": dns, "addresses": ips, "serial": info["serial"], "start": info["start"].isoformat(),
             "end": info["end"].isoformat(), "file": os.path.basename(out_path),
             "signed": (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0).isoformat()}
    _record(folder, entry)
    out("Certificate written: %s  (the box's certificate followed by the root; upload this one file on the box's page)" % out_path)
    out("  names   : %s" % ", ".join(dns + ips))
    out("  ends on : %s  (%d days)" % (info["end"].date().isoformat(), days))
    out("  serial  : %s" % info["serial"])
    return out_path, entry


def _record(folder, entry):
    path = os.path.join(folder, SIGNED)
    try:
        with open(path) as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    rows.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


# --- show, verify, list ---------------------------------------------------------------------------------------------
def pem_blocks(text):
    """Every CERTIFICATE block of a PEM text, in order, as text."""
    return [m.group(0) + "\n" for m in re.finditer(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]{1,65536}?-----END CERTIFICATE-----", text or "")]


def _norm(block):
    return "".join(block.split())


def is_signer(c):
    """A CA certificate that another CA issued: a signer (an intermediate), not a root."""
    return bool(c.get("ca")) and c.get("cn", "") not in re.split(r"\s*[,/]\s*|\s*=\s*", c.get("issuer", ""))


def _intermediates_file(path, tmp, root_text=None):
    """A file of the certificates after the first one in `path`, leaving out the root itself (D139: a node's file is the
    node's certificate, then the signer, then the root). None when there are none."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        blocks = pem_blocks(f.read(262144))
    rest = [b for b in blocks[1:] if root_text is None or _norm(b) not in [_norm(r) for r in pem_blocks(root_text)]]
    if not rest:
        return None
    out = os.path.join(tmp, "untrusted.pem")
    with open(out, "w") as f:
        f.write("".join(rest))
    return out


def show(exe, path, out=print, now=None):
    c = read_cert(exe, path)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    left = (c["end"] - now).days if c["end"] else None
    out("%s" % path)
    out("  for     : %s" % (", ".join(c["dns"] + c["ips"]) or c["cn"] or "(no names)"))
    out("  kind    : %s" % (("a SIGNER certificate (it signs node certificates for your root; never upload it as a node's certificate)" if is_signer(c) else
                             "a ROOT certificate (install this on devices; never upload it as a box's certificate)") if c["ca"] else
                            "a box's certificate" + ("" if c["server_auth"] else " WITHOUT serverAuth (browsers will refuse it)")))
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        more = len(pem_blocks(f.read(262144))) - 1
    if more > 0:
        out("  also    : %d more certificate(s) in this file (the signer and the root that vouch for it)" % more)
    out("  from    : %s" % (c["start"].date().isoformat() if c["start"] else "?"))
    out("  ends on : %s  (%s)" % (c["end"].date().isoformat() if c["end"] else "?",
                                 "RUN OUT" if left is not None and left < 0 else "%d days left" % left if left is not None else "?"))
    out("  serial  : %s" % c["serial"])
    out("  issuer  : %s" % c["issuer"])
    out("  SHA-256 : %s" % c["fingerprint"])
    return c


def root_fingerprint(exe, folder, out=print):
    """The stored root's fingerprint, to compare once with what the box's page shows (docs/HTTPS.md)."""
    pem = os.path.join(folder, ROOT_PEM)
    if not os.path.isfile(pem):
        raise BoxcertError("%s is missing: make the root first, or point --dir at its folder" % pem)
    c = read_cert(exe, pem)
    out("root    : %s" % pem)
    out("name    : %s" % c["cn"])
    out("SHA-256 : %s" % c["fingerprint"])
    out("Compare this with the fingerprint on the box's page, System > Secure connection, once.")
    return c["fingerprint"]


def verify(exe, folder, path, out=print):
    pem = os.path.join(folder, ROOT_PEM)
    if not os.path.isfile(pem):
        raise BoxcertError("%s is missing: point --dir at the root's folder" % pem)
    with tempfile.TemporaryDirectory() as tmp, open(pem) as r:
        # a node certificate from the signer (D139) needs the signer, which comes after it in the same file
        untrusted = _intermediates_file(path, tmp, r.read())
        code, text, err = run(exe, ["verify", "-CAfile", pem, "-purpose", "sslserver"] + (["-untrusted", untrusted] if untrusted else []) + [path])
    ok = code == 0 and ": OK" in text
    out("%s: %s" % (path, "signed by this root and good for a server" if ok else "NOT verified: " + (err.strip() or text.strip())))
    return ok


def list_signed(folder, out=print, now=None):
    path = os.path.join(folder, SIGNED)
    try:
        with open(path) as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = []
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not rows:
        out("Nothing signed yet (%s)." % path)
        return rows
    for r in rows:
        try:
            end = datetime.datetime.fromisoformat(r["end"])
            left = (end - now).days
            state = "RUN OUT" if left < 0 else ("due in %d days" % left if left <= 30 else "%d days left" % left)
        except (KeyError, ValueError):
            state = "?"
        out("%-24s %-12s ends %s  %s  (%s)" % (r.get("box", "?"), state, str(r.get("end", "?"))[:10], ", ".join(r.get("names", [])), r.get("file", "")))
    return rows


# --- the signer and the root move (D139, docs/HTTPS.md "Automatic certificates") --------------------------------------------
def signer_config(allowed=DEFAULT_ALLOWED, ranges=SIGNER_RANGES):
    parts = ["permitted;DNS:%s" % d for d in allowed] + ["permitted;IP:%s" % r for r in ranges]
    return "\n".join(["[req]", "distinguished_name = dn", "prompt = no", "[dn]", "CN = %s" % SIGNER_NAME, "[v3_signer]",
                      "basicConstraints = critical, CA:TRUE, pathlen:0", "keyUsage = critical, keyCertSign, cRLSign",
                      "subjectKeyIdentifier = hash", "authorityKeyIdentifier = keyid",
                      "nameConstraints = critical, " + ", ".join(parts)]) + "\n"


def root_allows_signer(root_text):
    """False for a root that may sign node certificates only (pathlen:0, every root made before D139)."""
    return not re.search(r"CA:TRUE,\s*pathlen:0", root_text)


def make_signer(exe, folder, ask, out=print, days=SIGNER_DAYS, ranges=SIGNER_RANGES, now=None):
    """The intermediate CA the signing service signs nodes with. Its key is written UNENCRYPTED (mode 600), because the
    service must sign without a person; the root key never leaves this computer."""
    key, pem = os.path.join(folder, ROOT_KEY), os.path.join(folder, ROOT_PEM)
    for p in (key, pem):
        if not os.path.isfile(p):
            raise BoxcertError("%s is missing: make the root first (boxcert.py --dir %s make-root)" % (p, folder))
    if not isinstance(days, int) or not 30 <= days <= MAX_LEAF_DAYS:
        raise BoxcertError("--days must be from 30 to %d" % MAX_LEAF_DAYS)
    for bad in [r for r in ranges if not valid_range(r)]:
        raise BoxcertError("not an address range (write it as 192.168.0.0/255.255.0.0): %r" % bad)
    sdir = os.path.join(folder, SIGNER_DIR)
    if os.path.lexists(sdir):
        raise BoxcertError("%s exists already. A signer is made once; to replace it (it ran out, or it may have been stolen), move "
                           "that folder away first, then run this again and copy the new folder to the signing computer." % sdir)
    code, root_text, err = run(exe, ["x509", "-in", pem, "-noout", "-text"])
    if code:
        _fail("could not read the root", err)
    if not root_allows_signer(root_text):
        raise BoxcertError("this root was made to sign node certificates only (pathlen:0, every root made before 2026-11-14), so "
                           "devices would refuse anything a signer under it signs. Make a new root in a new folder "
                           "(boxcert.py --dir ~/nxlx-root-ca-2 make-root) and read 'One evening' in docs/HTTPS.md.")
    passphrase = ask("Passphrase of the root key: ")
    code, _, err = run(exe, ["pkey", "-in", key, "-passin", "stdin", "-noout"], passphrase)
    if code:
        raise BoxcertError("wrong passphrase (or %s is damaged); nothing was made" % key)
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        skey, csr, cfg, spem = (os.path.join(tmp, n) for n in ("signer.key", "signer.csr", "signer.cnf", "signer.pem"))
        with open(cfg, "w") as f:
            f.write(signer_config(DEFAULT_ALLOWED, ranges))
        code, _, err = run(exe, ["ecparam", "-name", BOX_CURVE, "-genkey", "-noout", "-out", skey])
        if code:
            _fail("could not make the signer's key", err)
        os.chmod(skey, 0o600)
        code, _, err = run(exe, ["req", "-new", "-key", skey, "-config", cfg, "-out", csr])
        if code:
            _fail("could not make the signer's request", err)
        code, _, err = run(exe, ["x509", "-req", "-in", csr, "-CA", pem, "-CAkey", key, "-passin", "stdin", "-set_serial", "0x" + new_serial(),
                                 "-days", str(days), "-sha256", "-extfile", cfg, "-extensions", "v3_signer", "-out", spem], passphrase)
        if code:
            _fail("signing the signer failed", err)
        code, text, err = run(exe, ["verify", "-CAfile", pem, spem])
        if code or ": OK" not in text:
            _fail("the signer does not check out against the root", err or text)
        info = read_cert(exe, spem)
        with open(skey) as f:
            key_text = f.read()
        with open(spem) as f:
            signer_text = f.read()
        with open(skey, "r+b") as f:
            f.write(b"\0" * os.path.getsize(skey))
    os.makedirs(sdir, mode=0o700)
    write_private(os.path.join(sdir, SIGNER_KEY), key_text)
    with open(pem) as f:
        root_pem_text = f.read()
    for name, text in ((SIGNER_PEM, signer_text), (ROOT_PEM, root_pem_text), (SIGNER_PINS, "{}\n")):
        with open(os.path.join(sdir, name), "w") as f:
            f.write(text)
    _record(folder, {"box": "signer", "names": [SIGNER_NAME], "addresses": [], "serial": info["serial"], "start": info["start"].isoformat(),
                     "end": info["end"].isoformat(), "file": os.path.join(SIGNER_DIR, SIGNER_PEM),
                     "signed": (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0).isoformat()})
    out("Signer made: %s" % sdir)
    out("  %s : the signer's key, NOT encrypted, so the signing service can sign without you. Keep this folder on the signing computer only." % SIGNER_KEY)
    out("  %s : the signer (ends on %s)" % (SIGNER_PEM, info["end"].date().isoformat()))
    out("  %s    : a copy of your root, public" % ROOT_PEM)
    out("  constraints: names under .local and addresses in %s only" % ", ".join(ranges))
    out("Next: copy the whole folder %s to the signing computer (on the HP: C:\\Users\\<you>\\nxlx-signer), then start the service there "
        "(tools/signer/install-windows.ps1). Then delete the copy here if you like; this folder can make a new signer at any time." % sdir)
    return sdir


def _fp_hex(exe, path):
    return read_cert(exe, path)["fingerprint"].replace(" ", "")


def move_root(exe, old_folder, new_folder, ask, out=print, now=None):
    """A message, signed by the OLD root, that names the new root. A node that pins the old root takes the new one when
    it gets this message from the signing service, with no person at the node (D139). The old root signs it, so only
    whoever holds the old key and its passphrase can make one."""
    FL = fleetlist()
    old_key, old_pem, new_pem = os.path.join(old_folder, ROOT_KEY), os.path.join(old_folder, ROOT_PEM), os.path.join(new_folder, ROOT_PEM)
    for p in (old_key, old_pem):
        if not os.path.isfile(p):
            raise BoxcertError("%s is missing: --dir must be the OLD root's folder (the one your nodes trust today)" % p)
    if not os.path.isfile(new_pem):
        raise BoxcertError("%s is missing: --to must be the NEW root's folder (make it with make-root first)" % new_pem)
    sdir = os.path.join(new_folder, SIGNER_DIR)
    if not os.path.isdir(sdir):
        raise BoxcertError("%s is missing: make the signer first (boxcert.py --dir %s make-signer), then run this" % (sdir, new_folder))
    old_fp, new_fp = _fp_hex(exe, old_pem), _fp_hex(exe, new_pem)
    if old_fp == new_fp:
        raise BoxcertError("--dir and --to hold the same root; nothing to move")
    with open(new_pem) as f:
        new_text = f.read()
    with open(old_pem) as f:
        old_text = f.read()
    body = json.dumps({"v": 1, "kind": "nxlx-root-move", "from": old_fp, "to": new_fp, "root": new_text,
                       "issued": (now or datetime.datetime.now(datetime.timezone.utc)).date().isoformat()},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    out("Moving your nodes from the root %s... to the root %s..." % (old_fp[:16], new_fp[:16]))
    passphrase = ask("Passphrase of the OLD root key (%s): " % old_key)
    code, _, err = run(exe, ["pkey", "-in", old_key, "-passin", "stdin", "-noout"], passphrase)
    if code:
        raise BoxcertError("wrong passphrase (or %s is damaged); nothing was written" % old_key)
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        msg, sig = os.path.join(tmp, "message"), os.path.join(tmp, "signature")
        with open(msg, "wb") as f:
            f.write(ROOT_MOVE_MAGIC + body)
        code, _, err = run(exe, ["dgst", "-sha256", "-sign", old_key, "-passin", "stdin", "-out", sig, msg], passphrase)
        if code or not os.path.isfile(sig):
            _fail("signing the move failed", err)
        with open(sig, "rb") as f:
            sig_text = base64.encodebytes(f.read()).decode("ascii")
    try:                                                       # checked the way a node checks it, before anything is written
        ok = FL.signature_ok(ROOT_MOVE_MAGIC + body, sig_text, old_text, exe)
    except FL.FleetError as e:
        raise BoxcertError("the move could not be checked: %s" % e.message)
    if not ok:
        raise BoxcertError("the move's signature does not check out against the old root; nothing was written")
    _write_file(os.path.join(sdir, ROOT_MOVE), body)
    _write_file(os.path.join(sdir, ROOT_MOVE_SIG), sig_text.encode("ascii"))
    out("Written: %s and %s in %s." % (ROOT_MOVE, ROOT_MOVE_SIG, sdir))
    out("The signing service gives them to every node that still trusts the old root; each node checks the old root's signature,")
    out("takes the new root and asks for a new certificate by itself. Nothing to do at the nodes.")
    if any(os.path.exists(os.path.join(old_folder, n)) for n in (FLEET_DRAFT, FLEET_JSON)):
        data = _load_draft(exe, old_folder)
        last = max(data.get("serial") or 0, (signed_list(exe, old_folder).serial if os.path.exists(os.path.join(old_folder, FLEET_JSON)) else 0))
        if not os.path.exists(os.path.join(new_folder, FLEET_DRAFT)):
            data["serial"] = last
            _save_draft(new_folder, data)          # the same fleet id; the old folder is only read
        out("You have a fleet list. Nodes check it against their root, so after the move it must be signed by the new root:")
        out("  boxcert.py --dir %s fleet sign --serial %d   then   boxcert.py --dir %s fleet push <each node>" % (new_folder, last + 1, new_folder))
    return os.path.join(sdir, ROOT_MOVE)


# --- the fleet list (D132, docs/HTTPS.md "Letting your nodes open each other") ----------------------------------------------
FLEET_DRAFT, FLEET_JSON, FLEET_SIG = "fleet-draft.json", "fleet.json", "fleet.sig"
_FLEETLIST = []


def fleetlist():
    """pvj/fleetlist.py, the file format and the check a node makes, loaded by its path from the same checkout so the
    tool and the node can never read a list differently."""
    if not _FLEETLIST:
        import importlib.util
        path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "pvj", "fleetlist.py"))
        if not os.path.isfile(path):
            raise BoxcertError("the fleet commands need pvj/fleetlist.py from the same checkout as this tool (not found at %s)" % path)
        spec = importlib.util.spec_from_file_location("nxlx_fleetlist", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FLEETLIST.append(mod)
    return _FLEETLIST[0]


def _fleet_call(fn, *args, **kw):
    """A call into the module with its refusals turned into the tool's own."""
    FL = fleetlist()
    try:
        return fn(*args, **kw)
    except FL.FleetError as e:
        raise BoxcertError(e.message)


def _write_file(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _read_file(path, limit):
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError as e:
        raise BoxcertError("could not read %s: %s" % (path, e.strerror or e))
    if len(data) > limit:
        raise BoxcertError("%s is longer than %d bytes, which no fleet file is" % (path, limit))
    return data


def _root_pem_text(folder):
    pem = os.path.join(folder, ROOT_PEM)
    if not os.path.isfile(pem):
        raise BoxcertError("%s is missing: make the root first (boxcert.py make-root), or point --dir at its folder" % pem)
    return _read_file(pem, 65536).decode("utf-8", "replace")


def signed_list(exe, folder):
    """The list this folder has signed, checked against its root again, or None when there is none."""
    FL = fleetlist()
    jpath, spath = os.path.join(folder, FLEET_JSON), os.path.join(folder, FLEET_SIG)
    if not os.path.exists(jpath) and not os.path.exists(spath):
        return None
    if not (os.path.isfile(jpath) and os.path.isfile(spath)):
        raise BoxcertError("%s and %s must both be in %s and one is missing. Restore the pair from your backup of this folder "
                           "(boxcert.py fleet verify checks a pair). If there is no backup, remove the half that is left and then sign with "
                           "--serial N, N above the serial in %s (a node refuses a lower one). Do not make a new list with `fleet new`: "
                           "it gets a new fleet id, which every node refuses." % (FLEET_JSON, FLEET_SIG, folder, FLEET_DRAFT))
    body = _read_file(jpath, FL.MAX_BYTES)
    sig = _read_file(spath, FL.MAX_SIG_TEXT)
    try:
        return FL.verify(body, sig, _root_pem_text(folder), exe)
    except FL.FleetError as e:
        raise BoxcertError("the signed list in %s does not check out against your root (%s); it was changed or is not yours. "
                           "Restore %s and %s from your backup of this folder (boxcert.py fleet verify checks a pair). Do not make a new list "
                           "with `fleet new`: it gets a new fleet id, which every node refuses." % (folder, e.message, FLEET_JSON, FLEET_SIG))


def _load_draft(exe, folder):
    """The working copy as a dict; made from the signed list when only that is there."""
    path = os.path.join(folder, FLEET_DRAFT)
    if os.path.isfile(path):
        try:
            data = json.loads(_read_file(path, 262144).decode("utf-8"))
        except ValueError:
            raise BoxcertError("%s is not a list I wrote (not valid JSON); fix it or move it away" % path)
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            raise BoxcertError("%s is not a list I wrote; fix it or move it away" % path)
        data.setdefault("clear", [])
        return data
    done = signed_list(exe, folder)
    if done is None:
        raise BoxcertError("there is no fleet list in %s yet: start one with `boxcert.py fleet new`" % folder)
    data = done.as_dict()
    data.setdefault("clear", [])
    return data


def _save_draft(folder, data):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    _write_file(os.path.join(folder, FLEET_DRAFT), (json.dumps(data, indent=1, ensure_ascii=True) + "\n").encode("ascii"))


def fleet_new(exe, folder, name, out=print):
    FL = fleetlist()
    if os.path.lexists(os.path.join(folder, FLEET_DRAFT)) or os.path.lexists(os.path.join(folder, FLEET_JSON)):
        raise BoxcertError("there is a fleet list in %s already. A fleet id is made once; to start over, move the old %s, %s and %s away first."
                           % (folder, FLEET_DRAFT, FLEET_JSON, FLEET_SIG))
    _root_pem_text(folder)
    try:
        FL._plain_text(name, "the fleet's name", 64)
    except FL.FleetError as e:
        raise BoxcertError(e.message)
    data = {"v": FL.VERSION, "fleet": secrets.token_hex(8), "name": name, "serial": 0, "issued": "", "nodes": [], "clear": []}
    _save_draft(folder, data)
    out("Fleet list started: %s  (fleet id %s)" % (name, data["fleet"]))
    out("Next: boxcert.py fleet add <node's .local name> --carry full|live|none   (for each node), then: boxcert.py fleet sign")
    return data


def _check_host(host):
    h = (host or "").strip().lower().rstrip(".")
    if not (valid_dns(h) or valid_ip(h)):
        raise BoxcertError("not a host name or address: %r" % host)
    return h


def fetch_card(folder, host, port=443, timeout=6):
    """Ask a node for its fleet card over https, trusting only your root (root.pem in the folder), and check that the key
    hash it states is that of the certificate it showed. Returns the card (id, name, key)."""
    import http.client
    import ssl
    FL = fleetlist()
    host = _check_host(host)
    pem = os.path.join(folder, ROOT_PEM)
    _root_pem_text(folder)
    ctx = ssl.create_default_context(cafile=pem)
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    try:
        conn.request("GET", "/api/fleet/card", headers={"Accept": "application/json", "User-Agent": "boxcert"})
        der = conn.sock.getpeercert(binary_form=True) if conn.sock else None      # before the answer: a closed connection forgets it
        resp = conn.getresponse()
        body = resp.read(4097)
    except ssl.SSLCertVerificationError as e:
        raise BoxcertError("%s showed a certificate your root did not sign, or one that does not cover that name (%s). "
                           "Nothing was added." % (host, e.verify_message))
    except (OSError, http.client.HTTPException) as e:
        raise BoxcertError("could not ask %s:%d for its card: %s" % (host, port, e))
    finally:
        conn.close()
    if resp.status == 404:
        raise BoxcertError("%s does not give a fleet card: its software is older than this tool, or discovery is switched off on it "
                           "(the card is given only while discovery is on). Use --cert with its certificate file, --id and --name, "
                           "or --card with a card file, or switch discovery on for a minute." % host)
    if resp.status != 200 or not der:
        raise BoxcertError("%s answered %d to its card request; nothing was added" % (host, resp.status))
    card = _fleet_call(FL.parse_card, body)
    if card["key"] != _fleet_call(FL.key_hash, der):
        raise BoxcertError("%s's card states a key that is not the key of the certificate it showed; nothing was added" % host)
    return card


def card_from_files(exe, folder, card_path, cert_path, node_id, name):
    FL = fleetlist()
    if card_path:
        return _fleet_call(FL.parse_card, _read_file(card_path, 4096))
    _root_pem_text(folder)
    info = read_cert(exe, cert_path)
    # the certificate must be one your root signed for a server (dates left out, as a node judges it: the key is what counts)
    with tempfile.TemporaryDirectory() as tmp:
        untrusted = _intermediates_file(cert_path, tmp, _root_pem_text(folder))
        code, text, err = run(exe, ["verify", "-CAfile", os.path.join(folder, ROOT_PEM), "-purpose", "sslserver",
                                    "-attime", str(int(info["start"].timestamp()) + 60)] + (["-untrusted", untrusted] if untrusted else []) + [cert_path])
    if code or ": OK" not in text:
        raise BoxcertError("%s is not a certificate your root signed (%s). Nothing was added." % (cert_path, (err.strip() or text.strip()).splitlines()[-1] if (err.strip() or text.strip()) else "no reason given"))
    der = _fleet_call(FL.certificate_der, _read_file(cert_path, 65536).decode("utf-8", "replace"))
    try:
        card = _fleet_call(FL.parse_card, json.dumps({"id": node_id, "name": name, "key": _fleet_call(FL.key_hash, der)}).encode())
    except BoxcertError as e:
        raise BoxcertError("with --cert, --id must be the node's 32 hex digit id and --name its host name (%s)" % e)
    return card


CARRY_WORDS = {"full": "Owners carry in full", "live": "Operators at most", "none": "takes nobody in"}


def fleet_add(exe, folder, card, carry, replace_key=False, out=print):
    FL = fleetlist()
    if carry not in FL.CARRY:
        raise BoxcertError("--carry is one of: %s" % ", ".join(FL.CARRY))
    data = _load_draft(exe, folder)
    nodes = data["nodes"]
    same = [n for n in nodes if n.get("id") == card["id"]]
    row = {"id": card["id"], "name": card["name"], "key": card["key"], "carry": carry}
    if same:
        old = same[0]
        if old["key"] != card["key"] and not replace_key:
            raise BoxcertError("%s is on the list with another key. A new key means a new decision: if you know why it changed "
                               "(a new key was made on purpose), run this again with --replace-key." % old["name"])
        if any(n.get("name", "").lower() == card["name"].lower() for n in nodes if n is not old):
            raise BoxcertError("another node on the list is already called %s" % card["name"])
        for n in nodes:
            if n is not old and n.get("key") == card["key"]:
                raise BoxcertError("%s already has that key; two nodes cannot share one" % n["name"])
        nodes[nodes.index(old)] = row
        what = "updated"
    else:
        if len(nodes) >= FL.MAX_NODES:
            raise BoxcertError("a list holds %d nodes at most" % FL.MAX_NODES)
        for n in nodes:
            if n.get("key") == card["key"]:
                raise BoxcertError("%s already has that key; two nodes cannot share one" % n["name"])
        if any(n.get("name", "").lower() == card["name"].lower() for n in nodes):
            raise BoxcertError("another node on the list is already called %s" % card["name"])
        nodes.append(row)
        what = "added"
    _save_draft(folder, data)
    out("%s %s: %s  carry %s (%s)  key %s" % (card["name"], what, card["id"], carry, CARRY_WORDS[carry], card["key"][:16] + "..."))
    out("This is the working copy. Nothing is signed until you run: boxcert.py fleet sign")
    return row


def _find_node(data, which):
    which = (which or "").strip().lower()
    hits = [n for n in data["nodes"] if which in (n.get("id", "").lower(), n.get("name", "").lower()) or (len(which) >= 8 and n.get("key", "").startswith(which))]
    if not hits:
        raise BoxcertError("no node on the working list is called or has the id %r (boxcert.py fleet show lists them)" % which)
    if len(hits) > 1:
        raise BoxcertError("%r matches more than one node; use its id" % which)
    return hits[0]


def fleet_remove(exe, folder, which, out=print):
    data = _load_draft(exe, folder)
    node = _find_node(data, which)
    data["nodes"].remove(node)
    _save_draft(folder, data)
    out("%s removed from the working list. Sign a new list to make it final; until then nodes keep trusting what you signed before." % node["name"])
    out("A node that was stolen or lost needs more than this: press 'Remove a node from this cluster' on a node (docs/HTTPS.md).")
    return node


def fleet_clear(exe, folder, whats, undo=False, out=print):
    """Put key hashes into the list's `clear` field: the one cluster-wide way to lift the denies nodes have pushed on
    each other. A key hash, or the name or id of a node on the working list."""
    FL = fleetlist()
    data = _load_draft(exe, folder)
    clear = list(data.get("clear") or [])
    for w in whats:
        w = (w or "").strip().lower()
        key = w if FL.KEY_WORD.fullmatch(w) else _find_node(data, w)["key"]
        if undo:
            if key not in clear:
                raise BoxcertError("%s is not in clear" % key[:16])
            clear.remove(key)
            out("%s taken out of clear." % key[:16])
        elif key in clear:
            out("%s is in clear already." % key[:16])
        else:
            if len(clear) >= FL.MAX_CLEAR:
                raise BoxcertError("clear holds %d keys at most" % FL.MAX_CLEAR)
            clear.append(key)
            out("%s will be allowed again by every node that loads the next signed list." % key[:16])
    data["clear"] = sorted(clear)
    _save_draft(folder, data)
    out("This is the working copy. Sign it (boxcert.py fleet sign) with a higher serial, then load it on one node; it spreads.")
    return clear


def fleet_sign(exe, folder, ask, serial=None, out=print, now=None):
    FL = fleetlist()
    key = os.path.join(folder, ROOT_KEY)
    if not os.path.isfile(key):
        raise BoxcertError("%s is missing: this folder has no root key to sign with" % key)
    root_text = _root_pem_text(folder)
    data = _load_draft(exe, folder)
    done = signed_list(exe, folder)
    if done is not None and done.fleet != data.get("fleet"):
        raise BoxcertError("the working list is fleet %s but the signed one is fleet %s; move one of them away" % (data.get("fleet"), done.fleet))
    drafted = data.get("serial") if isinstance(data.get("serial"), int) else 0
    low = max(done.serial if done else 0, drafted)
    if done is None and drafted > 0 and serial is None:
        raise BoxcertError("the working copy was last signed at serial %d but the signed pair is not in %s. Restore %s and %s from your "
                           "backup. If they are gone for good, sign with --serial N, N above %d (a serial must be higher than any a node "
                           "may hold) and load the result on every node." % (drafted, folder, FLEET_JSON, FLEET_SIG, drafted))
    if serial is not None and not (isinstance(serial, int) and not isinstance(serial, bool) and serial > low):
        raise BoxcertError("--serial must be above the last signed serial (%d): a serial only counts up" % low)
    if serial is not None and serial > FL.MAX_SERIAL - 100000:
        raise BoxcertError("--serial %d is too high: the most a serial can be is %d, and jumping close to it leaves no room to go on; "
                           "use a number just above %d" % (serial, FL.MAX_SERIAL, low))
    data["serial"] = serial if serial is not None else low + 1
    data["issued"] = (now or datetime.datetime.now(datetime.timezone.utc)).date().isoformat()
    if not data["nodes"]:
        raise BoxcertError("the list names no nodes; add some first")
    body = _fleet_call(FL.canonical, data)
    message = _fleet_call(FL.signed_bytes, data["fleet"], data["serial"], body)
    out("Signing fleet %s \"%s\", serial %d, %d node(s):" % (data["fleet"], data["name"], data["serial"], len(data["nodes"])))
    for n in sorted(data["nodes"], key=lambda n: n["name"].lower()):
        out("  %-20s carry %-4s %s" % (n["name"], n["carry"], n["id"]))
    if data.get("clear"):
        out("  and lifts the removal of %d key(s)" % len(data["clear"]))
    passphrase = ask("Passphrase of the root key: ")
    code, _, err = run(exe, ["pkey", "-in", key, "-passin", "stdin", "-noout"], passphrase)
    if code:
        raise BoxcertError("wrong passphrase (or %s is damaged); nothing was signed" % key)
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        msg, sig = os.path.join(tmp, "message"), os.path.join(tmp, "signature")
        with open(msg, "wb") as f:
            f.write(message)
        code, _, err = run(exe, ["dgst", "-sha256", "-sign", key, "-passin", "stdin", "-out", sig, msg], passphrase)
        if code or not os.path.isfile(sig):
            _fail("signing failed", err)
        with open(sig, "rb") as f:
            sig_text = base64.encodebytes(f.read()).decode("ascii")
    # the signature is checked the way a node will check it, before anything is written
    _fleet_call(FL.verify, body, sig_text, root_text, exe, fleet=data["fleet"], current=done)
    # the signature first: a crash between the two leaves a signature that matches no list, which no node accepts and the
    # next signing replaces; the other order would leave a new list with the old signature
    _write_file(os.path.join(folder, FLEET_SIG), sig_text.encode("ascii"))
    _write_file(os.path.join(folder, FLEET_JSON), body)
    _save_draft(folder, data)
    out("Signed: %s and %s in %s" % (FLEET_JSON, FLEET_SIG, folder))
    out("Load both on each node (Setup > Nodes on this network). A node that holds a list ignores one with a lower serial.")
    return data["serial"]


def fleet_push(exe, folder, host, port, ask, token_file=None, out=print, timeout=10):
    """Send the signed pair to a node (POST /api/fleet/list) over https, trusting only your root. The node wants an Owner:
    by default the tool pairs as one with the node's PIN (asked on the prompt, never on the command line, and the device it
    makes is logged out again at the end), or it uses a token from --token-file. Nothing is kept."""
    import http.client
    import ssl
    FL = fleetlist()
    done = signed_list(exe, folder)
    if done is None:
        raise BoxcertError("nothing is signed yet in %s: run `fleet sign` first" % folder)
    body = _read_file(os.path.join(folder, FLEET_JSON), FL.MAX_BYTES)
    sig = _read_file(os.path.join(folder, FLEET_SIG), FL.MAX_SIG_TEXT)
    host = _check_host(host)
    _root_pem_text(folder)
    token = None
    if token_file:
        token = _read_file(token_file, 512).decode("utf-8", "replace").strip()
        if not token or len(token.split()) != 1:
            raise BoxcertError("%s should hold one token and nothing else" % token_file)
    ctx = ssl.create_default_context(cafile=os.path.join(folder, ROOT_PEM))
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)

    def call(method, path, payload, tok):
        headers = {"Content-Type": "application/json", "X-PVJ-Request": "1", "Accept": "application/json", "User-Agent": "boxcert"}
        if tok:
            headers["Authorization"] = "Bearer " + tok
        conn.request(method, path, body=json.dumps(payload).encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        raw = resp.read(262145)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {}
        return resp.status, data if isinstance(data, dict) else {}

    def log_out(tok):
        """Take the device this tool paired off the node again; one plain line if that did not work."""
        try:
            gone = call("POST", "/api/logout", {}, tok)[0] == 200
        except Exception:
            gone = False
        if not gone:
            out("Could not log boxcert's device out on %s. Remove \"boxcert push\" under People on that node." % host)

    paired = False
    try:
        conn.connect()                      # the node's certificate is checked against your root BEFORE anything is asked or sent
        if token is None:
            pin = ask("PIN of %s (on its display, or `sudo pvj-pin`; used once, not kept): " % host)
            st, res = call("POST", "/api/pair", {"pin": pin, "name": "boxcert push"}, None)
            if st != 200 or not res.get("token"):
                raise BoxcertError("%s did not pair this computer (%s). Nothing was sent." % (host, res.get("error") or "answered %d" % st))
            if (res.get("device") or {}).get("role") != "full":
                log_out(res["token"])
                raise BoxcertError("that code pairs a device that is not an Owner, and only an Owner may load a list. Nothing was sent.")
            token, paired = res["token"], True
        st, res = call("POST", "/api/fleet/list", {"list": body.decode("utf-8"), "sig": sig.decode("ascii")}, token)
        if st != 200:
            raise BoxcertError("%s did not take the list (%d): %s" % (host, st, res.get("error") or "no reason given"))
        lst = res.get("list") or {}
        out("%s took the list: fleet %s, serial %s, %d node(s)." % (host, lst.get("fleet"), lst.get("serial"), len(lst.get("nodes") or [])))
        return True
    except ssl.SSLCertVerificationError as e:
        raise BoxcertError("%s showed a certificate your root did not sign, or one that does not cover that name (%s). "
                           "Nothing was sent." % (host, e.verify_message))
    except (OSError, http.client.HTTPException) as e:
        raise BoxcertError("could not reach %s:%d: %s" % (host, port, e))
    finally:
        if paired:
            log_out(token)                                       # the device this made is removed again
        conn.close()


def _describe(lst, out):
    out("  fleet   : %s  \"%s\"" % (lst["fleet"], lst["name"]))
    out("  serial  : %s  (issued %s; dates are for you, nodes never judge them)" % (lst["serial"], lst.get("issued") or "-"))
    for n in sorted(lst["nodes"], key=lambda n: n["name"].lower()):
        out("  node    : %-20s carry %-4s id %s  key %s" % (n["name"], n["carry"], n["id"], n["key"][:16] + "..."))
    if lst.get("clear"):
        out("  clear   : %s" % ", ".join(k[:16] + "..." for k in lst["clear"]))


def fleet_show(exe, folder, out=print):
    done = signed_list(exe, folder)
    if done is not None:
        out("Signed list (checked against your root just now):")
        _describe(done.as_dict(), out)
    else:
        out("No list has been signed in %s yet." % folder)
    path = os.path.join(folder, FLEET_DRAFT)
    if os.path.isfile(path):
        draft = _load_draft(exe, folder)
        if done is None:
            out("Working copy:")
            _describe(draft, out)
        else:
            was = {n["id"]: n for n in done.nodes}
            now_ = {n["id"]: n for n in draft["nodes"]}
            changes = ["+ %s (carry %s)" % (n["name"], n["carry"]) for i, n in now_.items() if i not in was]
            changes += ["- %s" % n["name"] for i, n in was.items() if i not in now_]
            changes += ["~ %s: carry %s to %s" % (n["name"], was[i]["carry"], n["carry"]) for i, n in now_.items() if i in was and was[i]["carry"] != n["carry"]]
            changes += ["~ %s: new key" % n["name"] for i, n in now_.items() if i in was and was[i]["key"] != n["key"]]
            if sorted(draft.get("clear") or []) != sorted(done.clear):
                changes.append("~ clear changed")
            out("Working copy: %s" % ("same as the signed list" if not changes else "differs, and is not signed yet:"))
            for c in changes:
                out("    " + c)
    return done


def fleet_verify(exe, folder, list_path, sig_path, fleet=None, out=print):
    FL = fleetlist()
    list_path = list_path or os.path.join(folder, FLEET_JSON)
    sig_path = sig_path or os.path.join(folder, FLEET_SIG)
    body, sig = _read_file(list_path, FL.MAX_BYTES), _read_file(sig_path, FL.MAX_SIG_TEXT)
    try:
        lst = FL.verify(body, sig, _root_pem_text(folder), exe, fleet=fleet)
    except FL.FleetError as e:
        out("%s: NOT good: %s" % (list_path, e.message))
        return False
    out("%s: signed by this root, fleet %s, serial %d, %d node(s)" % (list_path, lst.fleet, lst.serial, len(lst.nodes)))
    return True


# --- command line -----------------------------------------------------------------------------------------------------
def main(argv=None, ask=None, out=print):
    ask = ask or getpass.getpass
    p = argparse.ArgumentParser(prog="boxcert.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", default=DEFAULT_DIR, help="the root's folder (default %s)" % DEFAULT_DIR)
    p.add_argument("--openssl", default=None, help="which openssl to run (default: the first on the path)")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("make-root", help="make the root (once)")
    r.add_argument("--name", default="NXLX boxes root", help="the root's name as devices show it")
    r.add_argument("--allow-name", action="append", default=[], metavar="DOMAIN", help="a further domain the root may sign under (besides local)")
    r.add_argument("--allow-address", action="append", default=[], metavar="RANGE", help="a further address range, as 203.0.113.0/255.255.255.0 (besides the private ranges)")
    r.add_argument("--no-constraints", action="store_true", help="a root without name constraints (read docs/HTTPS.md first)")
    for name in ("sign", "renew"):
        s = sub.add_parser(name, help="sign a box's request" if name == "sign" else "the same as sign")
        s.add_argument("request")
        s.add_argument("--name", action="append", default=[], help="a further name the box is reached by")
        s.add_argument("--address", action="append", default=[], help="a further address")
        s.add_argument("--drop", action="append", default=[], help="a name or address from the request to leave out")
        s.add_argument("--days", type=int, default=LEAF_DAYS)
        s.add_argument("--out", default=None)
    x = sub.add_parser("make-signer", help="make the signer the signing service uses (once per root; D139)")
    x.add_argument("--days", type=int, default=SIGNER_DAYS, help="how long the signer lasts (default %d)" % SIGNER_DAYS)
    x.add_argument("--allow-address", action="append", default=[], metavar="RANGE", help="a further address range (besides the private ranges)")
    x = sub.add_parser("move-root", help="sign, with the OLD root (--dir), a message that moves every node to the new root (--to)")
    x.add_argument("--to", required=True, help="the new root's folder, e.g. ~/nxlx-root-ca-2 (its signer must exist)")
    sub.add_parser("show").add_argument("cert")
    sub.add_parser("verify").add_argument("cert")
    sub.add_parser("list")
    sub.add_parser("root", help="the root's fingerprint, to compare with the box's page")
    f = sub.add_parser("fleet", help="the fleet list: which nodes may open each other without pairing again (D132)",
                       description="The fleet list names the nodes you trust to carry your devices between them. You make it here, "
                                   "sign it with your root (the passphrase is asked), and load it on a node. The root key never "
                                   "goes on a node. Dates are shown to you and never judged; a serial only counts up. Read "
                                   "'Letting your nodes open each other' in docs/HTTPS.md first.")
    fs = f.add_subparsers(dest="fleet_cmd")
    x = fs.add_parser("new", help="start a list (once; it gets its own fleet id)")
    x.add_argument("--name", required=True, help="a name for the fleet, as the panels show it, e.g. \"Anthony's rig\"")
    x = fs.add_parser("add", help="add a node to the working list, or change its carry")
    x.add_argument("host", nargs="?", help="the node's .local name or address; it is asked for its card over https")
    x.add_argument("--port", type=int, default=443, help="the node's https port (default 443)")
    x.add_argument("--carry", required=True, choices=("full", "live", "none"),
                   help="the highest role this node gives a device that arrives from another node on the list: "
                        "full (Owner), live (Operator), none (takes nobody in). Choose it on purpose: a venue's node says live or none")
    x.add_argument("--card", help="a card file instead of asking the node (id, name, key)")
    x.add_argument("--cert", help="the node's certificate file instead of asking it; needs --id and --name")
    x.add_argument("--id", help="with --cert: the node's id (32 hex digits, on its panel and in the Nodes list)")
    x.add_argument("--name", help="with --cert: the node's host name")
    x.add_argument("--replace-key", action="store_true", help="the node is on the list with another key and you know why (it made a new one)")
    x = fs.add_parser("remove", help="take a node off the working list")
    x.add_argument("node", help="its name or id")
    x = fs.add_parser("clear", help="lift the removal of nodes for every node, in the next signed list")
    x.add_argument("what", nargs="+", help="a node on the working list, or a key hash (64 hex digits)")
    x.add_argument("--undo", action="store_true", help="take these out of clear again")
    x = fs.add_parser("sign", help="sign the working list with the root")
    x.add_argument("--serial", type=int, default=None, help="the serial to use (default: one above the last signed); never lower")
    fs.add_parser("show", help="the signed list, and what the working copy changes")
    x = fs.add_parser("push", help="send the signed list to a node over https",
                      description="Posts fleet.json and fleet.sig to the node (POST /api/fleet/list). Only your root is trusted for the "
                                  "connection. The node wants an Owner: the tool asks for the node's PIN on the prompt (never on the "
                                  "command line), pairs as an Owner, sends the list and logs that device out again; or give --token-file "
                                  "with a token of an Owner device. Nothing is kept on this computer.")
    x.add_argument("host", help="the node's .local name or address")
    x.add_argument("--port", type=int, default=443, help="the node's https port (default 443)")
    x.add_argument("--token-file", default=None, help="a file holding an Owner device's token, instead of the PIN")
    x = fs.add_parser("verify", help="does the root's signature hold")
    x.add_argument("--list", default=None, help="the list file (default: fleet.json in the folder)")
    x.add_argument("--sig", default=None, help="the signature file (default: fleet.sig in the folder)")
    x.add_argument("--fleet", default=None, help="also require this fleet id")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    if a.cmd == "fleet" and not a.fleet_cmd:
        f.print_help()
        return 2
    try:
        exe = openssl_bin(a.openssl)
        if a.cmd == "make-root":
            make_root(exe, a.dir, a.name, tuple(DEFAULT_ALLOWED) + tuple(x.lower() for x in a.allow_name), not a.no_constraints, ask, out,
                      tuple(PRIVATE_RANGES) + tuple(a.allow_address))
        elif a.cmd in ("sign", "renew"):
            sign(exe, a.dir, a.request, a.name, a.address, [d.lower() for d in a.drop], a.days, a.out, ask, out)
        elif a.cmd == "make-signer":
            make_signer(exe, a.dir, ask, out, a.days, tuple(SIGNER_RANGES) + tuple(a.allow_address))
        elif a.cmd == "move-root":
            move_root(exe, a.dir, os.path.expanduser(a.to), ask, out)
        elif a.cmd == "show":
            show(exe, a.cert, out)
        elif a.cmd == "verify":
            return 0 if verify(exe, a.dir, a.cert, out) else 1
        elif a.cmd == "list":
            list_signed(a.dir, out)
        elif a.cmd == "root":
            root_fingerprint(exe, a.dir, out)
        elif a.cmd == "fleet":
            c = a.fleet_cmd
            if c == "new":
                fleet_new(exe, a.dir, a.name, out)
            elif c == "add":
                if sum(bool(v) for v in (a.host, a.card, a.cert)) != 1:
                    raise BoxcertError("give exactly one of: the node's name or address, --card FILE, --cert FILE")
                if a.cert and not (a.id and a.name):
                    raise BoxcertError("--cert needs --id and --name as well")
                card = card_from_files(exe, a.dir, a.card, a.cert, a.id, a.name) if (a.card or a.cert) else fetch_card(a.dir, a.host, a.port)
                fleet_add(exe, a.dir, card, a.carry, a.replace_key, out)
            elif c == "remove":
                fleet_remove(exe, a.dir, a.node, out)
            elif c == "clear":
                fleet_clear(exe, a.dir, a.what, a.undo, out)
            elif c == "sign":
                fleet_sign(exe, a.dir, ask, a.serial, out)
            elif c == "show":
                fleet_show(exe, a.dir, out)
            elif c == "push":
                fleet_push(exe, a.dir, a.host, a.port, ask, a.token_file, out)
            elif c == "verify":
                return 0 if fleet_verify(exe, a.dir, a.list, a.sig, a.fleet, out) else 1
        return 0
    except BoxcertError as e:
        print("boxcert: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nboxcert: stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
