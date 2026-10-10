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

The folder (default ~/nxlx-root-ca) holds root.key (encrypted with your passphrase), root.pem (public: this is what
your devices install) and signed.json (box, names, serial, start, end). Back the folder up. Never put root.key on
a box. Every extension goes through a configuration file, because LibreSSL has no -addext.
"""

import argparse
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
    lines = ["[req]", "distinguished_name = dn", "prompt = no", "[dn]", "CN = %s" % name, "[v3_ca]",
             "basicConstraints = critical, CA:TRUE, pathlen:0", "keyUsage = critical, keyCertSign, cRLSign",
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
        code, _, err = run(exe, ["pkcs8", "-topk8", "-v2", "aes-256-cbc", "-in", plain, "-out", key, "-passout", "stdin"], p1)
        if code:
            _fail("could not encrypt the root key", err)
        os.chmod(key, 0o600)
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
    out("  constraints : %s" % ("names under %s and addresses in %s only" % (", ".join("." + a for a in allowed), ", ".join(ranges)) if constraints else "none"))
    out("Next: on the box's panel, System > Secure connection, download the request, then: boxcert.py sign <the request>")
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


def read_cert(exe, path):
    code, text, err = run(exe, ["x509", "-in", path, "-noout", "-text", "-startdate", "-enddate", "-serial", "-issuer"])
    if code:
        _fail("%s is not a certificate" % path, err)
    dns, ips = names_in(text)
    start = end = None
    serial = issuer = ""
    for line in text.splitlines():
        if line.startswith("notBefore="):
            start = parse_time(line)
        elif line.startswith("notAfter="):
            end = parse_time(line)
        elif line.startswith("serial="):
            serial = line.split("=", 1)[1].strip().lower()
        elif line.startswith("issuer="):
            issuer = line.split("=", 1)[1].strip()
    return {"cn": common_name(text), "dns": dns, "ips": ips, "start": start, "end": end, "serial": serial, "issuer": issuer,
            "ca": "CA:TRUE" in text, "server_auth": "TLS Web Server Authentication" in text or SERVER_AUTH in text,
            "constraints": "Name Constraints" in text}


# --- signing --------------------------------------------------------------------------------------------------------
def leaf_config(dns, ips):
    san = ", ".join(["DNS:%s" % d for d in dns] + ["IP:%s" % i for i in ips])
    return "\n".join(["[v3_leaf]", "basicConstraints = CA:FALSE", "keyUsage = critical, digitalSignature",
                      "extendedKeyUsage = serverAuth", "subjectKeyIdentifier = hash", "authorityKeyIdentifier = keyid",
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
    serial = secrets.token_hex(16)
    serial = hex(int(serial[0], 16) & 0x7)[2:] + serial[1:]            # a positive 128-bit number, so 16 bytes in the file
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
def show(exe, path, out=print, now=None):
    c = read_cert(exe, path)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    left = (c["end"] - now).days if c["end"] else None
    out("%s" % path)
    out("  for     : %s" % (", ".join(c["dns"] + c["ips"]) or c["cn"] or "(no names)"))
    out("  kind    : %s" % ("a ROOT certificate (install this on devices; never upload it as a box's certificate)" if c["ca"] else
                            "a box's certificate" + ("" if c["server_auth"] else " WITHOUT serverAuth (browsers will refuse it)")))
    out("  from    : %s" % (c["start"].date().isoformat() if c["start"] else "?"))
    out("  ends on : %s  (%s)" % (c["end"].date().isoformat() if c["end"] else "?",
                                 "RUN OUT" if left is not None and left < 0 else "%d days left" % left if left is not None else "?"))
    out("  serial  : %s" % c["serial"])
    out("  issuer  : %s" % c["issuer"])
    return c


def verify(exe, folder, path, out=print):
    pem = os.path.join(folder, ROOT_PEM)
    if not os.path.isfile(pem):
        raise BoxcertError("%s is missing: point --dir at the root's folder" % pem)
    code, text, err = run(exe, ["verify", "-CAfile", pem, "-purpose", "sslserver", path])
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
    sub.add_parser("show").add_argument("cert")
    sub.add_parser("verify").add_argument("cert")
    sub.add_parser("list")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        exe = openssl_bin(a.openssl)
        if a.cmd == "make-root":
            make_root(exe, a.dir, a.name, tuple(DEFAULT_ALLOWED) + tuple(x.lower() for x in a.allow_name), not a.no_constraints, ask, out,
                      tuple(PRIVATE_RANGES) + tuple(a.allow_address))
        elif a.cmd in ("sign", "renew"):
            sign(exe, a.dir, a.request, a.name, a.address, [d.lower() for d in a.drop], a.days, a.out, ask, out)
        elif a.cmd == "show":
            show(exe, a.cert, out)
        elif a.cmd == "verify":
            return 0 if verify(exe, a.dir, a.cert, out) else 1
        elif a.cmd == "list":
            list_signed(a.dir, out)
        return 0
    except BoxcertError as e:
        print("boxcert: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nboxcert: stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
