# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Automatic certificates (D139, docs/HTTPS.md "Automatic certificates"): the node asks the owner's signing service
(tools/nxlx-signer.py) for its certificate by itself, so it has HTTPS with no step from the owner.

Where the signer is: the setting `https.signer_url`, else PVJ_SIGNER_URL. Which root to expect: `https.root_fingerprint`,
else PVJ_ROOT_FINGERPRINT. install.sh --signer writes both into /etc/pvj/pvj.env and refuses one without the other.
Nothing is discovered: the signer runs on a computer that cannot announce itself the way nodes do (pvj/fleet.py
announces through avahi, which a Windows computer does not have), and an address the owner gave is safer than the
first stranger to answer.

What a try does, in order, and never more often than it must:
  1. A node that trusts no root and was not told which root to expect does NOTHING: it says it needs the root
     fingerprint (the D139 review: the first root is never taken from whoever answers over plain http).
  2. GET /v1/info: the root the signer works for. While no root is pinned it must be the expected root. Once a root is
     pinned, the expected fingerprint plays no part any more: only a move the pinned root signed changes the root.
  3. When this node pins another root, GET /v1/root-move; the move must lead to the signer's root before it is even
     checked, and it is taken only if the pinned root signed it (HttpsBox.apply_root_move).
  4. When the certificate in use is missing, has under 30 days left, or is not from the pinned root: make the
     automatic request with the node's own key (its own file: the owner's request.pem and saved names stay as they
     are), POST /v1/sign, and load the answer through HttpsBox.install_from_signer, the checks of a manual upload.
A signer that does not answer changes nothing: the node keeps serving what it has, the page says "waiting for the
signer", and it is tried again in ten minutes; after a refusal for a taken name, in six hours. The panel starts
first; this runs in its own thread after it.
"""

import ipaddress
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from .httpsbox import AUTO, HttpsError, fingerprint, pem_certificates, same_fingerprint

RENEW_DAYS = 30
TIMEOUT = 8
MAX_ANSWER = 64 * 1024
FIRST_DELAY = 15.0          # after the panel starts: the network may still be coming up
RETRY = 600.0               # while waiting for the signer, or after a refusal
RECHECK = 6 * 3600.0        # while all is well, or after the signer said a name is taken (409): no point asking sooner
URL = re.compile(r"https?://[A-Za-z0-9.\-]{1,253}(?::[0-9]{1,5})?/?")
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]

WORDS = {
    "off": "Automatic certificates are off: no signer is set for this node.",
    "needs-fingerprint": "Needs the root fingerprint: this node trusts no root yet, and it takes one only when it was told which. "
                         "Type the fingerprint of your root (python3 tools/boxcert.py root) below, or install again with --root-fingerprint.",
    "waiting": "Waiting for the signer: it did not answer. This node keeps what it has and tries again every ten minutes.",
    "refused": "The signer's answer was not taken.",
    "done": "The certificate came from the signer.",
    "good": "The certificate in use is good; nothing to ask the signer for.",
}


class Unreachable(Exception):
    pass


class SignerSaid(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def private_v4(a):
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return False
    return ip.version == 4 and any(ip in n for n in PRIVATE)


def clean_fingerprint(text):
    """64 hex digits out of a fingerprint however it was written (spaces, colons, capitals), or None."""
    w = re.sub(r"[^0-9a-f]", "", (text or "").lower()) if isinstance(text, str) else ""
    return w if FINGERPRINT.fullmatch(w) else None


class AutoCert:
    def __init__(self, box, settings, env=None, now=time.time, log=None, opener=None):
        self.box, self.settings, self.env = box, settings, env if env is not None else os.environ
        self.now = now
        self.log = log or (lambda line: None)
        self._open = opener or (lambda req, timeout: urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout))
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.box.auto = self._read_state()

    # -- where the signer is, and which root to expect --
    def _section(self):
        s = self.settings.data.get("https")
        return s if isinstance(s, dict) else {}

    def url(self):
        u = self._section().get("signer_url")
        if not isinstance(u, str) or not u.strip():
            u = self.env.get("PVJ_SIGNER_URL", "")
        u = (u or "").strip()
        return u.rstrip("/") if URL.fullmatch(u) else None

    def want_root(self):
        return clean_fingerprint(self._section().get("root_fingerprint")) or clean_fingerprint(self.env.get("PVJ_ROOT_FINGERPRINT", ""))

    # -- the state the page shows --
    def _read_state(self):
        try:
            with open(self.box.path(AUTO), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _set(self, state, message="", url=None, status=None):
        out = {"state": state, "words": WORDS.get(state, ""), "message": message[:300], "url": url, "when": int(self.now()),
               "status": status, "root_fingerprint": self.want_root()}
        self.box.auto = out
        try:
            self.box._ensure_folder()
            self.box._write(AUTO, json.dumps(out))
        except OSError:
            pass
        return out

    # -- one try --
    def due(self):
        """Why a certificate should be asked for now, or None."""
        info = self.box.info
        if self.box.context is None or not info:
            return "there is no certificate in use"
        if info["not_after"] - int(self.now()) < RENEW_DAYS * 86400:
            return "the certificate ends within %d days" % RENEW_DAYS
        if self.box.chained is False:
            return "the certificate in use is not from the root this node trusts"
        return None

    def _call(self, url, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "pvj-autocert"})
        try:
            with self._open(req, TIMEOUT) as resp:
                raw, status = resp.read(MAX_ANSWER + 1), resp.status
        except urllib.error.HTTPError as e:
            with e:
                raw, status = e.read(MAX_ANSWER + 1), e.code
        except (OSError, ValueError) as e:            # refused, timed out, no route, a name that does not resolve
            raise Unreachable(str(getattr(e, "reason", e))[:160])
        if len(raw) > MAX_ANSWER:
            raise SignerSaid(502, "the signer's answer is too long")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SignerSaid(502, "the signer's answer is not JSON")
        if not isinstance(payload, dict):
            raise SignerSaid(502, "the signer's answer is not an object")
        if status != 200:
            raise SignerSaid(status, str(payload.get("error") or "the signer answered %d" % status)[:300])
        return payload

    def run_once(self):
        """One try. Returns the state written for the page; never raises."""
        with self.lock:
            url = self.url()
            if url is None:
                return self._set("off")
            if self.box.root_info()[0] is None and self.want_root() is None:
                return self._set("needs-fingerprint", "", url)
            try:
                return self._try(url)
            except Unreachable as e:
                return self._set("waiting", "No signer answered at %s (%s)." % (url, e), url)
            except SignerSaid as e:
                return self._set("refused", "The signer said: %s" % e.message, url, e.status)
            except HttpsError as e:
                return self._set("refused", "This node refused the signer's answer: %s" % e.message, url, e.status)
            except Exception as e:                   # nothing here may stop the panel
                return self._set("refused", "Unexpected: %s" % str(e)[:200], url)

    def _try(self, url):
        want = self.want_root()
        info = self._call(url + "/v1/info")
        root = info.get("root")
        if not isinstance(root, str):
            raise SignerSaid(502, "the signer did not say which root it works for")
        try:
            signer_fp = fingerprint(pem_certificates(root)[0])
        except (ValueError, IndexError):
            raise SignerSaid(502, "the signer's root cannot be read")
        moved = ""
        _stored, stored_fp = self.box.root_info()
        # the expected fingerprint decides the FIRST root only; once a root is pinned, only a move that root signed changes
        # it (a fingerprint set at install must not block the owner's later, signed move to another root)
        if stored_fp is None and want and not same_fingerprint(signer_fp, want):
            raise HttpsError("the signer at %s works for the root %s, not for the root this node expects (%s); nothing was sent to it"
                             % (url, signer_fp.replace(" ", "")[:16], want[:16]))
        if stored_fp is not None and not same_fingerprint(stored_fp, signer_fp):
            try:
                move = self._call(url + "/v1/root-move")
            except SignerSaid as e:
                if e.status == 404:
                    raise HttpsError("the signer works for another root (%s) than the one this node trusts (%s), and has no move "
                                     "signed by this node's root" % (signer_fp[:19], stored_fp[:19]))
                raise
            if not isinstance(move.get("move"), str) or not isinstance(move.get("sig"), str):
                raise SignerSaid(502, "the signer's root move is not one")
            done = self.box.apply_root_move(move["move"].encode("utf-8"), move["sig"], expect_to=[signer_fp])
            moved = "Moved to the new root %s. " % done["new"][:19]
        why = self.due()
        if why is None:
            return self._set("good", moved + "Good until %s." % self.box.date(self.box.info["not_after"]), url)
        names = [n for n in self.box.default_names() if ":" not in n and (not re.fullmatch(r"[0-9.]+", n) or private_v4(n))]
        names = [n for n in names if re.fullmatch(r"[0-9.]+", n) or n.endswith(".local")]
        made = self.box.make_request(names, auto=True)
        answer = self._call(url + "/v1/sign", {"csr": made["request"], "hostname": self.box.hostname()})
        if not isinstance(answer.get("chain"), str):
            raise SignerSaid(502, "the signer sent no certificate")
        leaf = self.box.install_from_signer(answer["chain"], signer_fp, want)
        self.log("pvj-web: certificate from the signer at %s (%s), until %s" % (url, why, self.box.date(leaf["not_after"])))
        return self._set("done", moved + "For %s, until %s." % (", ".join(leaf["dns"] + leaf["ips"]), self.box.date(leaf["not_after"])), url)

    # -- the thread --
    def kick(self):
        self.wake.set()

    def start(self, first_delay=FIRST_DELAY):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, args=(first_delay,), name="pvj-autocert", daemon=True)
        self.thread.start()

    @staticmethod
    def next_delay(state):
        """How long until the next try after this state."""
        if state.get("state") in ("good", "done", "off"):
            return RECHECK
        if state.get("state") == "refused" and state.get("status") == 409:
            return RECHECK                         # a taken name stays taken until the owner releases it; he can press Try now
        return RETRY

    def _loop(self, delay):
        while True:
            self.wake.wait(delay)
            self.wake.clear()
            delay = self.next_delay(self.run_once())
