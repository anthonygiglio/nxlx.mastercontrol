# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box's side of the secure connection (D79, docs/HTTPS.md): its own key and certificate request, the certificate
the owner uploads, the TLS context the panel's second listener serves with, and the switch "Owner access only over
the secure connection".

Files, all in one folder (`<state>/tls`, made 0700 by the panel, which is the only account that reads it; the key
0600): key.pem (the box's private key, made here and never sent anywhere), request.pem (the certificate signing
request the owner downloads and signs with tools/boxcert.py), request.json (the names in it), cert.pem (the
certificate in use), previous.pem (the one before, for Undo), root.pem (the owner's public root, kept for other
devices to download). None of them is in the settings file, an export or the diagnostics file.

The key and the request are made with the operating system's `openssl` (Debian installs it with ca-certificates),
run with fixed arguments as the panel's own user: Python's `ssl` can serve a certificate but cannot make one. A box
without `openssl` says so and serves HTTP only.

An uploaded certificate is read here with a small DER reader (names, dates, key usage, is it a CA) and checked
against the key by the standard library (`load_cert_chain` refuses a mismatch) before it is taken into use; each
failure has its own message. The box's clock judges "has run out" only when it is set from the network (a Pi has no
battery clock); the visiting device judges validity in any case, so a wrong clock never makes the box refuse to
serve what it has.
"""

import base64
import datetime
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time

KEY, REQUEST, REQUEST_META, CERT, PREVIOUS, ROOT, NEW = "key.pem", "request.pem", "request.json", "cert.pem", "previous.pem", "root.pem", "cert.new.pem"
CURVE = "prime256v1"
MAX_PEM = 32 * 1024                    # a certificate with its root is about 2 kB
WARN_DAYS = 30
MAX_NAMES = 12
OID_CN, OID_SAN, OID_EKU, OID_BASIC = "2.5.4.3", "2.5.29.17", "2.5.29.37", "2.5.29.19"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
SETTINGS_KEY = "https"                 # {"owner_only": bool, "names": [extra names for the request]}; no schema change (D79)
OPEN_WHILE_OWNER_ONLY = {"/api/hello", "/api/logout", "/api/https", "/api/https/probe"}   # a panel can still explain and log out
RENEWAL_ROUTES = {"/api/https/request.csr", "/api/https/certificate"}     # open over plain http to a paired owner once the certificate has run out


class HttpsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# --- a small DER reader: enough to say what a certificate is for ------------------------------------------------------
def _tlv(b, i):
    if i + 2 > len(b):
        raise ValueError("short")
    tag, length, i = b[i], b[i + 1], i + 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or i + n > len(b):
            raise ValueError("length")
        length, i = int.from_bytes(b[i:i + n], "big"), i + n
    if i + length > len(b):
        raise ValueError("short")
    return tag, b[i:i + length], i + length


def _children(b):
    out, i = [], 0
    while i < len(b):
        tag, val, i = _tlv(b, i)
        out.append((tag, val))
    return out


def _oid(b):
    if not b:
        return ""
    parts, v = [str(b[0] // 40), str(b[0] % 40)], 0
    for byte in b[1:]:
        v = (v << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(v))
            v = 0
    return ".".join(parts)


def _time(tag, val):
    text = val.decode("ascii", "replace")
    fmt = "%y%m%d%H%M%SZ" if tag == 0x17 else "%Y%m%d%H%M%SZ"
    dt = datetime.datetime.strptime(text, fmt).replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _name_cn(b):
    for _, rdn in _children(b):
        for _, atv in _children(rdn):
            kids = _children(atv)
            if len(kids) == 2 and _oid(kids[0][1]) == OID_CN:
                return kids[1][1].decode("utf-8", "replace")
    return ""


def pem_blocks(text):
    """Every CERTIFICATE block of a PEM text, in order, as (DER bytes, the block's own text). Read line by line: a
    regular expression over the whole text could be made to backtrack for seconds on a file of one BEGIN line and
    whitespace (review of #119), and this runs on the panel's one process."""
    out, body, inside = [], None, False
    for line in text.splitlines():
        line = line.strip()
        if line == "-----BEGIN CERTIFICATE-----":
            body, inside = [], True
        elif line == "-----END CERTIFICATE-----" and inside:
            try:
                der = base64.b64decode("".join(body), validate=True)
            except (ValueError, TypeError):
                raise ValueError("a certificate block is not base64")
            out.append((der, "-----BEGIN CERTIFICATE-----\n" + "\n".join(body) + "\n-----END CERTIFICATE-----\n"))
            body, inside = None, False
        elif inside:
            if line:
                body.append(line)
        if len(out) > 8:
            raise ValueError("too many certificates in one file")
    return out


def pem_certificates(text):
    """The DER of every CERTIFICATE block in a PEM text, in order."""
    return [der for der, _ in pem_blocks(text)]


def read_certificate(der):
    """{serial, subject, issuer, not_before, not_after, dns, ips, server_auth, ca} from a DER certificate. Raises
    ValueError for anything that is not one."""
    tag, cert_body, _ = _tlv(der, 0)
    if tag != 0x30:
        raise ValueError("not a certificate")
    tbs = _children(cert_body)[0][1]
    fields = _children(tbs)
    i = 1 if fields and fields[0][0] == 0xA0 else 0
    serial = fields[i][1].hex()
    issuer = _name_cn(fields[i + 2][1])
    validity = _children(fields[i + 3][1])
    not_before, not_after = _time(*validity[0]), _time(*validity[1])
    subject = _name_cn(fields[i + 4][1])
    out = {"serial": serial, "subject": subject, "issuer": issuer, "not_before": not_before, "not_after": not_after,
           "dns": [], "ips": [], "server_auth": False, "ca": False, "has_eku": False}
    for tag, val in fields[i + 5:]:
        if tag != 0xA3:
            continue
        for _, ext in _children(_children(val)[0][1]):
            kids = _children(ext)
            oid, value = _oid(kids[0][1]), kids[-1][1]
            try:
                inner = _children(value)[0][1]                  # the OCTET STRING holds one SEQUENCE
            except (ValueError, IndexError):
                continue
            if oid == OID_SAN:
                for gtag, gval in _children(inner):
                    if gtag == 0x82:
                        out["dns"].append(gval.decode("ascii", "replace").lower())
                    elif gtag == 0x87 and len(gval) in (4, 16):
                        out["ips"].append(socket.inet_ntop(socket.AF_INET if len(gval) == 4 else socket.AF_INET6, gval))
            elif oid == OID_EKU:
                out["has_eku"] = True
                out["server_auth"] = any(_oid(v) == OID_SERVER_AUTH for _, v in _children(inner))
            elif oid == OID_BASIC:
                bc = _children(inner)
                out["ca"] = bool(bc) and bc[0][0] == 0x01 and bc[0][1] != b"\x00"
    return out


def fingerprint(der):
    """SHA-256 of the certificate, in groups of four, the way the page and tools/boxcert.py both print it."""
    h = hashlib.sha256(der).hexdigest()
    return " ".join(h[i:i + 4] for i in range(0, 64, 4))


def same_fingerprint(a, b):
    norm = lambda x: re.sub(r"[^0-9a-f]", "", (x or "").lower())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def name_matches(host, dns, ips):
    """Does `host` (a name or address as typed in a browser, no port) match the certificate's names the way a browser
    matches them: a name exactly (case does not matter), or by the one left-most label against a wildcard; an
    address exactly."""
    import ipaddress
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%")[0])
        return any(ipaddress.ip_address(i) == ip for i in ips)
    except ValueError:
        pass
    for d in dns:
        if d == host:
            return True
        if d.startswith("*.") and "." in host and host.split(".", 1)[1] == d[2:]:
            return True
    return False


def host_of(header):
    """The name or address out of a Host header, without the port."""
    h = (header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if re.search(r":[0-9]{1,5}$", h) else h


# --- the box's files, the context, the switch ---------------------------------------------------------------------------
class HttpsBox:
    def __init__(self, folder, settings, openssl="openssl", hostname=None, addresses=None, clock_trusted=None,
                 now=time.time, log=None):
        self.folder = folder
        self.settings = settings
        self.openssl = openssl
        self._hostname = hostname
        self._addresses = addresses or (lambda: [])
        self._clock_trusted = clock_trusted or (lambda: None)
        self._now = now
        self.log = log or (lambda line: None)
        self.lock = threading.Lock()
        self.context = None          # the SSLContext the TLS listener serves with; None: every handshake is refused
        self.info = None             # read_certificate() of cert.pem while it is loaded
        self.load_error = None       # why the certificate on disk could not be loaded at start, for the page
        self.port = None             # set by the server when it listens
        self._openssl_ok = None

    # -- files --
    def path(self, name):
        return os.path.join(self.folder, name)

    def _ensure_folder(self):
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder, mode=0o700)
        os.chmod(self.folder, 0o700)

    def _read(self, name, limit=MAX_PEM):
        try:
            with open(self.path(name), "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit + 1)[:limit]
        except OSError:
            return None

    def _write(self, name, text, mode=0o640):
        tmp = self.path(name) + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
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

    def hostname(self):
        return (self._hostname or socket.gethostname() or "box").split(".")[0].lower()

    def has_openssl(self):
        """Remembered once it is there (a status read must not start a process each time); asked again while it is not."""
        if self._openssl_ok is True:
            return True
        try:
            self._openssl_ok = subprocess.run([self.openssl, "version"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            self._openssl_ok = False
        return self._openssl_ok

    # -- the switch --
    def _section(self):
        s = self.settings.data.get(SETTINGS_KEY)
        return s if isinstance(s, dict) else {}

    @property
    def owner_only(self):
        return self._section().get("owner_only") is True

    def extra_names(self):
        return [n for n in self._section().get("names", []) if isinstance(n, str)][:MAX_NAMES]

    def relief(self):
        """What the switch lets through right now although it is on, or None. "no_certificate": none is loaded, so
        HTTPS cannot be reached and everything is open over http as if the switch were off (a certificate removed
        while on is this case). "run_out": the loaded one has ended by a clock set from the network, every device
        refuses it, and ONLY what renewal needs is open over plain http, to an already paired full-access device
        (RENEWAL_ROUTES): never the PIN, never pairing, never another owner route, because the box's clock can be
        stepped from the network by whoever answers its time requests (second review of #119), and a relief that
        opened the PIN would then be theirs to open. A name the certificate does not carry is no relief at all."""
        if not self.owner_only:
            return None
        if self.context is None:
            return "no_certificate"
        if self.run_out():
            return "run_out"
        return None

    def run_out(self):
        return bool(self.info) and self.clock_trusted() and self.info["not_after"] < int(self._now())

    def effective(self):
        """The switch bites while a certificate is loaded; a run-out one opens the renewal routes only (owner_gate)."""
        return self.owner_only and self.context is not None

    def set_owner_only(self, on, secure, device_secure):
        if not isinstance(on, bool):
            raise HttpsError('"on" must be true or false')
        if not secure:
            raise HttpsError("this switch is changed over the secure connection only: open %s and try there" % self.https_address(), 403)
        if not device_secure:
            raise HttpsError("this device was paired over plain http: log out and pair it again over https://, then switch", 403)
        if on and self.context is None:
            raise HttpsError("there is no certificate in use, so nothing would be left to reach the owner by: upload one first", 409)
        with self.settings.lock:
            section = dict(self._section())
            section["owner_only"] = on
            self.settings.data[SETTINGS_KEY] = section
            self.settings.save()
        self.log("pvj-web: owner access only over the secure connection switched %s" % ("on" if on else "off"))
        return on

    def owner_gate(self, device, secure, path):
        """Why a full-access device's request is refused while the switch is on, or None. Over plain HTTP every route
        but a few that explain and log out; over HTTPS a token that was issued over plain HTTP (it may have been
        listened to). A support login comes through its own encrypted tunnel and is not gated here."""
        if not device or device.get("role") != "full" or device.get("remote") or not self.effective():
            return None
        if path in OPEN_WHILE_OWNER_ONLY:
            return None
        if path in RENEWAL_ROUTES and self.run_out():          # the way to a new certificate, for a paired owner device
            return None
        if not secure:
            return "owner access is only over the secure connection: open %s" % self.https_address()
        if not self.device_secure(device):
            return "this device was paired over plain http: log out and pair it again over %s" % self.https_address()
        return None

    def pin_allowed(self, secure):
        """May a 4-digit PIN be accepted on this connection?"""
        return secure or not self.effective()

    def device_secure(self, device):
        for d in self.settings.data.get("devices", []):
            if d.get("id") == (device or {}).get("id"):
                return d.get("secure") is True
        return False

    def mark_secure(self, device_id):
        """The token of this device was MADE over the secure connection (D79: a pairing over TLS, never a session
        started from a token that already existed, which may have crossed plain http before): remembered in its
        record; an older release ignores the key."""
        with self.settings.lock:
            for d in self.settings.data.get("devices", []):
                if d.get("id") == device_id and d.get("secure") is not True:
                    d["secure"] = True
                    self.settings.save()
                    return True
        return False

    # -- names --
    def first_name(self):
        if self.info and self.info["dns"]:
            return self.info["dns"][0]
        return self.hostname() + ".local"

    def default_names(self):
        """What goes in the request unless the owner changes it: the .local name first, then the box's IPv4 addresses,
        then names he added before. Never the bare host name (the root's constraints would refuse the whole
        certificate for it) and never IPv6 (same reason)."""
        names = [self.hostname() + ".local"]
        for n in self.extra_names():
            if n not in names:
                names.append(n)
        try:
            for a in self._addresses():
                if isinstance(a, str) and ":" not in a and a not in names:
                    names.append(a)
        except Exception:
            pass
        return names[:MAX_NAMES]

    @staticmethod
    def check_names(names):
        import ipaddress
        if not isinstance(names, list) or not names or len(names) > MAX_NAMES:
            raise HttpsError("give 1 to %d names or addresses" % MAX_NAMES)
        dns, ips = [], []
        for n in names:
            if not isinstance(n, str):
                raise HttpsError("a name must be text")
            n = n.strip().lower().rstrip(".")
            try:
                ip = ipaddress.ip_address(n)
                if ip.version == 6:
                    raise HttpsError("%s: IPv6 addresses are left out (the root allows private IPv4 ranges only)" % n)
                if str(ip) not in ips:
                    ips.append(str(ip))
                continue
            except ValueError:
                pass
            if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*", n):
                raise HttpsError("%r is not a host name" % n)
            if "." not in n:
                raise HttpsError("%s: a bare host name without .local would make the whole certificate refused by the root's constraints; use %s.local" % (n, n))
            if n not in dns:
                dns.append(n)
        return dns, ips

    # -- the key and the request --
    def make_request(self, names, new_key=False):
        dns, ips = self.check_names(names)
        if not self.has_openssl():
            raise HttpsError("this box has no openssl, so it cannot make a key; install the openssl package on it", 503)
        with self.lock:
            self._ensure_folder()
            if new_key or not os.path.isfile(self.path(KEY)):
                r = self._run(["ecparam", "-name", CURVE, "-genkey", "-noout", "-out", self.path(KEY) + ".tmp"])
                if r.returncode != 0:
                    raise HttpsError("the key could not be made: %s" % self._tail(r), 500)
                os.chmod(self.path(KEY) + ".tmp", 0o600)
                os.replace(self.path(KEY) + ".tmp", self.path(KEY))
                if new_key:
                    # the certificate in use no longer matches: HTTP only until the new one is uploaded
                    self._drop_certificate("a new key was made")
            cfg = self.path("request.cnf")
            san = ",".join(["DNS:%s" % d for d in dns] + ["IP:%s" % i for i in ips])
            self._write("request.cnf", "[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=v3_req\n[dn]\nCN=%s\n[v3_req]\nsubjectAltName=%s\n"
                        % ((dns or ips)[0], san), 0o600)
            try:
                r = self._run(["req", "-new", "-key", self.path(KEY), "-config", cfg, "-out", self.path(REQUEST) + ".tmp"])
            finally:
                self._unlink("request.cnf")
            if r.returncode != 0:
                self._unlink(REQUEST + ".tmp")
                raise HttpsError("the request could not be made: %s" % self._tail(r), 500)
            os.chmod(self.path(REQUEST) + ".tmp", 0o640)
            os.replace(self.path(REQUEST) + ".tmp", self.path(REQUEST))
            self._write(REQUEST_META, json.dumps({"dns": dns, "ips": ips, "made": int(self._now())}))
        extra = [d for d in dns if d != self.hostname() + ".local"]
        with self.settings.lock:
            section = dict(self._section())
            section["names"] = extra[:MAX_NAMES]
            self.settings.data[SETTINGS_KEY] = section
            self.settings.save()
        self.log("pvj-web: certificate request made for %s" % ", ".join(dns + ips))
        return {"request": self._read(REQUEST), "names": dns + ips, "file": self.hostname() + ".csr"}

    def _run(self, args, timeout=60):
        try:
            return subprocess.run([self.openssl] + args, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HttpsError("openssl could not run: %s" % e, 500)

    @staticmethod
    def _tail(r):
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        return lines[-1][:160] if lines else "no message"

    def request_pem(self):
        return self._read(REQUEST)

    def root_pem(self):
        return self._read(ROOT)

    # -- the certificate --
    def _try_load(self, cert_text):
        """A context for this certificate with the box's key, or an HttpsError that says why not."""
        if not os.path.isfile(self.path(KEY)):
            raise HttpsError("this box has no key yet: make the request first, sign it, then upload the certificate")
        tmp = self.path("check.pem")
        self._write("check.pem", cert_text, 0o600)
        try:
            ctx = self._context()
            try:
                ctx.load_cert_chain(tmp, self.path(KEY))
            except ssl.SSLError as e:
                text = str(e)
                if "key values mismatch" in text.lower() or "mismatch" in text.lower():
                    raise HttpsError("the certificate does not match this box's key: it was not made from this box's request (or a new key was made since); download the request again and sign that")
                raise HttpsError("the certificate could not be loaded: %s" % text[:160])
            return ctx
        finally:
            self._unlink("check.pem")

    @staticmethod
    def _context():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.verify_mode = ssl.CERT_NONE                    # no client certificates
        ctx.options |= getattr(ssl, "OP_NO_COMPRESSION", 0) | getattr(ssl, "OP_NO_RENEGOTIATION", 0)
        return ctx

    def clock_trusted(self):
        try:
            return self._clock_trusted() is True
        except Exception:
            return False

    def check(self, pem_text, host):
        """Read and judge an uploaded certificate: (leaf info, leaf PEM, root PEM or None). Raises HttpsError."""
        if not isinstance(pem_text, str) or not pem_text.strip():
            raise HttpsError("send the certificate file's text")
        if len(pem_text) > MAX_PEM:
            raise HttpsError("that file is too large to be a certificate", 413)
        try:
            blocks = pem_blocks(pem_text)
        except ValueError as e:
            raise HttpsError("not a certificate file: %s" % e)
        ders = [der for der, _ in blocks]
        if not ders:
            raise HttpsError("no certificate in the file (it should start with -----BEGIN CERTIFICATE-----); the request file or a key is not it")
        try:
            infos = [read_certificate(d) for d in ders]
        except (ValueError, IndexError, UnicodeDecodeError, OverflowError):
            raise HttpsError("the file holds something that is not a certificate")
        leaf = infos[0]
        if leaf["ca"]:
            raise HttpsError("this is the root certificate (install that on devices); upload the box's certificate, the file boxcert.py sign wrote")
        if not leaf["server_auth"]:
            raise HttpsError("the certificate is not for a server (no serverAuth extended key usage), so browsers would refuse it")
        if not leaf["dns"] and not leaf["ips"]:
            raise HttpsError("the certificate names no host (no Subject Alternative Name), so browsers would refuse it")
        if leaf["not_after"] <= leaf["not_before"]:
            raise HttpsError("the certificate's dates make no sense (it ends before it starts)")
        if self.clock_trusted() and leaf["not_after"] < int(self._now()):
            raise HttpsError("the certificate has run out (it ended on %s): sign the request again" % self.date(leaf["not_after"]))
        h = host_of(host)
        if h and h not in ("localhost", "127.0.0.1", "::1") and not name_matches(h, leaf["dns"], leaf["ips"]):
            raise HttpsError("the certificate does not name %s, which is how you reached the box; it names %s. Make the request again with that name in it, or open the box by a name it has"
                             % (h, ", ".join(leaf["dns"] + leaf["ips"])))
        root = None
        for info, (_, block) in zip(infos[1:], blocks[1:]):
            if info["ca"]:
                root = block
                break
        return leaf, blocks[0][1], root

    def root_info(self):
        """(info, fingerprint) of the stored root, or (None, None)."""
        text = self._read(ROOT)
        if not text:
            return None, None
        try:
            der = pem_certificates(text)[0]
            return read_certificate(der), fingerprint(der)
        except (ValueError, IndexError):
            return None, None

    def _issued_by_stored_root(self, leaf_pem, leaf):
        """Is this certificate signed by the root this box knows? Checked by the operating system's `openssl verify`
        with fixed arguments and a time bound: the issuer name and the key identifier can be forged by a root made
        to look the same, and the standard library has no public way to check a signature. `-attime` is the
        certificate's own first minute, so the dates (which the device judges, and which a stepped clock would
        confuse) play no part here; `-purpose sslserver` and the root's name constraints do."""
        self._write("check.pem", leaf_pem, 0o600)
        try:
            r = self._run(["verify", "-CAfile", self.path(ROOT), "-purpose", "sslserver", "-attime", str(leaf["not_before"] + 60), self.path("check.pem")],
                          timeout=20)
        finally:
            self._unlink("check.pem")
        return r.returncode == 0 and ": OK" in (r.stdout or "")

    def install(self, pem_text, host):
        leaf, leaf_pem, root_pem = self.check(pem_text, host)
        with self.lock:
            stored, stored_fp = self.root_info()
            if stored is not None:
                # the root is pinned once it exists (third review of #119): a certificate from another root is
                # refused whatever the file carries, and the stored root is never touched by an upload
                if not self.has_openssl():
                    raise HttpsError("the certificate cannot be checked against this box's root without openssl on the box", 503)
                if not self._issued_by_stored_root(leaf_pem, leaf):
                    raise HttpsError("the certificate was not issued by the root this box knows (fingerprint %s): a certificate from another "
                                     "root is refused. To move to a new root, replace the root first, over https://, then upload." % stored_fp[:19])
                root_pem = None
            ctx = self._try_load(leaf_pem)
            self._ensure_folder()
            try:
                # the new one lands under its own name first: a write that fails leaves the one in use where it is
                # (second review of #119: moving it aside first could leave no certificate, and with the switch on
                # the next start would open everything)
                self._write(NEW, leaf_pem)
                if root_pem:
                    self._write(ROOT, root_pem)
                if os.path.isfile(self.path(CERT)):
                    os.replace(self.path(CERT), self.path(PREVIOUS))
                os.replace(self.path(NEW), self.path(CERT))
            except OSError as e:
                self._unlink(NEW)
                raise HttpsError("the certificate could not be saved on the box: %s" % (e.strerror or e), 500)
            self.context, self.info, self.load_error = ctx, leaf, None
        self.log("pvj-web: certificate installed for %s, ends %s (serial %s)" % (", ".join(leaf["dns"] + leaf["ips"]), self.date(leaf["not_after"]), leaf["serial"]))
        return leaf

    def replace_root(self, pem_text, confirm, secure, device_secure):
        """A new root, over TLS only, by a TLS-paired owner, with the new root's fingerprint typed back as the confirm
        (the page shows the old and the new one side by side). The certificate in use stays as it is; the next
        upload must then be from the new root."""
        if not secure:
            raise HttpsError("the root is replaced over the secure connection only: open %s" % self.https_address(), 403)
        if not device_secure:
            raise HttpsError("this device was paired over plain http: log out and pair it again over https://, then replace the root", 403)
        if not isinstance(pem_text, str) or len(pem_text) > MAX_PEM:
            raise HttpsError("send the root certificate file's text")
        try:
            blocks = pem_blocks(pem_text)
        except ValueError as e:
            raise HttpsError("not a certificate file: %s" % e)
        cas = []
        for der, block in blocks:
            try:
                info = read_certificate(der)
            except (ValueError, IndexError, UnicodeDecodeError, OverflowError):
                raise HttpsError("the file holds something that is not a certificate")
            if info["ca"]:
                cas.append((der, block, info))
        if len(cas) != 1:
            raise HttpsError("the file should hold exactly one root certificate (root.pem from the tool's folder); it holds %d" % len(cas))
        der, block, info = cas[0]
        new_fp = fingerprint(der)
        if not same_fingerprint(confirm, new_fp):
            raise HttpsError("to replace the root, send its fingerprint back as the confirm: %s" % new_fp, 409)
        with self.lock:
            old_info, old_fp = self.root_info()
            self._ensure_folder()
            self._write(ROOT, block)
        self.log("pvj-web: the root was replaced: %s -> %s" % ((old_fp or "none")[:19], new_fp[:19]))
        return {"old": old_fp, "new": new_fp, "name": info["subject"]}

    def undo(self):
        with self.lock:
            prev = self._read(PREVIOUS)
            if prev is None:
                raise HttpsError("there is no earlier certificate to go back to", 409)
            try:
                infos = [read_certificate(d) for d in pem_certificates(prev)]
                ctx = self._try_load(prev)
            except (ValueError, IndexError) as e:
                raise HttpsError("the earlier certificate cannot be read: %s" % e, 409)
            current = self._read(CERT)
            self._write(CERT, prev)
            if current is not None:
                self._write(PREVIOUS, current)
            else:
                self._unlink(PREVIOUS)
            self.context, self.info, self.load_error = ctx, infos[0], None
        self.log("pvj-web: the earlier certificate is in use again")
        return self.info

    def _drop_certificate(self, why):
        for name in (CERT, PREVIOUS):
            self._unlink(name)
        self.context, self.info = None, None
        self.log("pvj-web: HTTPS off (%s); the panel answers on http only" % why)

    def remove(self):
        """HTTP only. The key stays for the next request; the root stays for devices (it is public)."""
        with self.lock:
            had = self.context is not None or os.path.isfile(self.path(CERT))
            self._drop_certificate("the certificate was removed")
        return had

    def wipe(self):
        """A factory reset: everything goes, the box returns to HTTP only. Returns the problems."""
        problems = []
        with self.lock:
            self.context, self.info, self.load_error = None, None, None
            for name in (KEY, REQUEST, REQUEST_META, CERT, PREVIOUS, ROOT, NEW, "check.pem", "request.cnf", KEY + ".tmp", REQUEST + ".tmp", NEW + ".tmp"):
                if not self._unlink(name):
                    problems.append("could not remove %s" % name)
            try:
                if os.path.isdir(self.folder):
                    os.rmdir(self.folder)
            except OSError:
                pass
        return problems

    def load(self):
        """At start: the certificate on disk, if it loads. Nothing here may stop the panel."""
        with self.lock:
            text = self._read(CERT)
            if text is None:
                return False
            try:
                self.info = read_certificate(pem_certificates(text)[0])
                self.context = self._try_load(text)
                self.load_error = None
                return True
            except (HttpsError, ValueError, IndexError, OSError) as e:
                self.context, self.info = None, None
                self.load_error = getattr(e, "message", None) or str(e)
                self.log("pvj-web: the certificate on disk was not loaded: %s" % self.load_error)
                return False

    # -- what the page shows --
    @staticmethod
    def date(epoch):
        return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).date().isoformat()

    def status(self, secure, host, device=None):
        now = int(self._now())
        cert = None
        if self.info:
            left = (self.info["not_after"] - now) // 86400
            cert = {"names": self.info["dns"] + self.info["ips"], "starts": self.date(self.info["not_before"]),
                    "ends": self.date(self.info["not_after"]), "serial": self.info["serial"], "issuer": self.info["issuer"],
                    "days_left": left, "run_out": left < 0, "soon": 0 <= left <= WARN_DAYS,
                    "names_this_host": name_matches(host_of(host), self.info["dns"], self.info["ips"])}
        return {"https": self.context is not None, "port": self.port, "secure": bool(secure), "owner_only": self.owner_only,
                "effective": self.effective(), "certificate": cert, "previous": os.path.isfile(self.path(PREVIOUS)),
                "request": os.path.isfile(self.path(REQUEST)), "request_names": self._request_names(), "key": os.path.isfile(self.path(KEY)),
                "root": os.path.isfile(self.path(ROOT)), "root_fingerprint": self.root_info()[1], "openssl": self.has_openssl(), "clock_trusted": self.clock_trusted(),
                "load_error": self.load_error, "default_names": self.default_names(), "host": host_of(host),
                "this_device_secure": self.device_secure(device) if device else False, "warn_days": WARN_DAYS, "relief": self.relief(),
                "https_address": self.https_address(host), "http_address": "http://%s/" % (host_of(host) or self.first_name())}

    def https_address(self, host=None):
        name = host_of(host) or self.first_name()
        if ":" in name and not name.startswith("["):
            name = "[%s]" % name
        return "https://%s%s/" % (name, "" if not self.port or self.port == 443 else ":%d" % self.port)

    def _request_names(self):
        try:
            meta = json.loads(self._read(REQUEST_META) or "null")
            return (meta.get("dns") or []) + (meta.get("ips") or []) if isinstance(meta, dict) else []
        except ValueError:
            return []
