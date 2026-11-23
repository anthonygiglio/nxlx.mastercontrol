# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The daily heartbeat (J-057, D149): a small HTTPS ping the node sends on its own to a dead man's switch, so a phone is told
when the node goes quiet.

Silence cannot be noticed from inside the node. Something outside has to expect a ping and raise the alarm when it stops
coming. This module is the node's half: it works with any service that takes a ping URL (healthchecks.io is one example,
Uptime Kuma's push monitors and self-hosted copies are others). Nothing here names or needs one service.

Off by default and the Owner's alone (the setting `heartbeat`, the node's own: not exported, not imported, never changed
through remote support). While it is on, a background thread sends one HTTPS GET to the Owner's URL every few minutes.

What is sent: a plain GET to the URL, nothing else. No body, no added query, no node name, no address, no device list; the
only header besides the usual ones is a fixed User-Agent. With the option "tell the service when the player is down" on,
the request goes to the URL with `/fail` added to its path while the player is not running (healthchecks.io's way of
saying "this is a failure now"); with it off, the ping is the same whatever the player does, and the service only learns
that the node is alive.

What it never does: block the panel or the player (its own thread, a short timeout, no lock held while it waits on the
network), raise (every error becomes a short sentence), follow a redirect (so a ping never leaves https), or write the URL
into a log (the URL is a key to the check, so it is shown back only in a masked form).
"""

import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT = {"on": False, "url": "", "minutes": 15, "fail": False}
MINUTES = (5, 10, 15, 30, 60)
MAX_URL = 300
TIMEOUT = 10.0               # seconds, for the background ping
TEST_TIMEOUT = 8.0           # seconds, for the test ping the Owner waits on
FIRST_PING_AFTER = 20.0      # seconds after the node starts (or the Owner switches it on): the service sees a restart soon
TICK = 30.0                  # seconds: how often the thread looks at the setting and the clock
READ_AT_MOST = 1024
USER_AGENT = "nxlx-mastercontrol-heartbeat/1"
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f-\x9f\\<>\"'`{}|^]")


class HeartbeatError(ValueError):
    pass


def valid_url(url):
    """The URL, checked strictly, as it is kept. Raises HeartbeatError with a sentence for the Owner. https only; no spaces
    or control characters; at most MAX_URL characters; a host name or an IPv4 address (no login, no brackets); a port in range;
    no fragment. A query is allowed (some services use one)."""
    if not isinstance(url, str):
        raise HeartbeatError("the address must be text")
    if len(url) > MAX_URL:
        raise HeartbeatError("the address is longer than %d characters" % MAX_URL)
    if not url.isascii():
        raise HeartbeatError("the address has a character that cannot be in an address")
    if not url.lower().startswith("https://"):
        raise HeartbeatError("the address must start with https://")
    if _BAD_CHARS.search(url):
        raise HeartbeatError("the address has a space or a character that cannot be in an address")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise HeartbeatError("the address is not a valid web address")
    if parts.scheme.lower() != "https":
        raise HeartbeatError("the address must start with https://")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise HeartbeatError("the address must not hold a user name or password")
    host = parts.hostname or ""
    if not host or not _HOST.fullmatch(host) or len(host) > 253:
        raise HeartbeatError("the address needs a host name, like hc-ping.com")
    if host.replace(".", "").isdigit():
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise HeartbeatError("the host is not a valid name or address")
    if port is not None and not 1 <= port <= 65535:
        raise HeartbeatError("the port must be between 1 and 65535")
    if parts.fragment or "#" in url:
        raise HeartbeatError("the address must not have a # part")
    if not parts.path or parts.path == "/":
        # a ping URL always has a path that names the check; a bare host would only ping the service's front page
        raise HeartbeatError("the address needs the check's own part after the host name")
    return urllib.parse.urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))


def form(section):
    """The `heartbeat` setting, checked. Raises HeartbeatError."""
    if not isinstance(section, dict) or set(section) - set(DEFAULT):
        raise HeartbeatError("the heartbeat setting holds on, url, minutes and fail")
    out = dict(DEFAULT)
    out.update(section)
    for key in ("on", "fail"):
        if not isinstance(out[key], bool):
            raise HeartbeatError("%s must be true or false" % key)
    if not isinstance(out["minutes"], int) or isinstance(out["minutes"], bool) or out["minutes"] not in MINUTES:
        raise HeartbeatError("minutes must be one of %s" % ", ".join(str(m) for m in MINUTES))
    out["url"] = valid_url(out["url"]) if out["url"] != "" else ""
    if out["on"] and not out["url"]:
        raise HeartbeatError("paste the check's ping address before switching the heartbeat on")
    return out


def stored(section):
    """The setting as the node reads it. Fails closed: anything damaged is the default, which is off."""
    try:
        return form(section) if section is not None else dict(DEFAULT)
    except HeartbeatError:
        return dict(DEFAULT)


def mask(url):
    """The address as it is shown back: the host, then only the last four characters of the path, nothing of a query. The
    URL is the key to the check, so it is never shown in full."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    tail = path[-4:] if len(path) > 4 else ""
    return "https://%s/…%s%s" % (parts.netloc, tail, "?…" if parts.query else "")


def with_fail(url):
    """The same address with /fail added to its path (before any query)."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/fail", parts.query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_open(req, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(req, timeout=timeout)


def words(error):
    """A short sentence for a failure, with no address in it."""
    if isinstance(error, urllib.error.HTTPError):
        if 300 <= error.code < 400:
            return "The service redirected the ping (%d). Use the address it gives for the check itself." % error.code
        if error.code in (401, 403):
            return "The service refused the ping (%d). Check the address." % error.code
        if error.code == 404:
            return "The service does not know that address (404). Check that the check exists and the address is right."
        if error.code == 429:
            return "The service says pings come too often (429)."
        return "The service answered with an error (%d)." % error.code
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLError) or isinstance(error, ssl.SSLError):
        return "The service's certificate was not accepted. Check the address, and the node's date and time."
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(error, (socket.timeout, TimeoutError)):
        return "No answer in time. The node may be offline, or the service slow."
    if isinstance(reason, socket.gaierror):
        return "The service's name could not be found. The node may be offline, or the address is wrong."
    if isinstance(error, (OSError, urllib.error.URLError)):
        return "The service could not be reached."
    return "The ping failed."


class Heartbeat:
    """Settings in, one ping out, the last result kept. `opener(req, timeout)` and `clock` are replaceable for tests."""

    def __init__(self, settings, player_running=None, log=print, opener=None, clock=time.time):
        self.settings = settings
        self._player_running = player_running
        self.log = log
        self._open = opener or default_open
        self._clock = clock
        self._lock = threading.Lock()          # guards _last, _due
        self._testing = threading.Lock()       # one test ping at a time
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last = None
        self._due = None
        self._failing = False

    # -- the setting --
    def config(self):
        with self.settings.lock:
            return stored(self.settings.data.get("heartbeat"))

    def save(self, new):
        """Keep a checked setting, and have the next ping soon so the Owner sees it work. Raises OSError when it cannot be saved."""
        with self.settings.lock:
            self.settings.data["heartbeat"] = new
            self.settings.save()
        with self._lock:
            self._due = self._clock() + FIRST_PING_AFTER if new["on"] else None
        self._wake.set()

    # -- what is shown --
    def status(self):
        cfg = self.config()
        with self._lock:
            last = dict(self._last) if self._last else None
            due = self._due
        out = {"on": cfg["on"], "has_url": bool(cfg["url"]), "url_shown": mask(cfg["url"]), "minutes": cfg["minutes"],
               "minutes_choices": list(MINUTES), "fail": cfg["fail"], "last": last}
        if last is not None:
            out["last"]["ago"] = max(0, int(self._clock() - last["at"]))
        out["next_in"] = max(0, int(due - self._clock())) if (cfg["on"] and due is not None) else None
        return out

    # -- one ping, never raising --
    def ping(self, how="auto", timeout=TIMEOUT):
        """Send one ping to the saved address. Returns {"ok", "text", "at", "how"}; never raises and never holds a lock while
        it waits on the network. With no address saved nothing is sent."""
        cfg = self.config()
        if not cfg["url"]:
            return self._remember({"ok": False, "text": "No address is saved yet.", "how": how}, count=False)
        url = cfg["url"]
        try:
            if cfg["fail"] and self._player_running is not None and not self._player_running():
                url, sent = with_fail(url), "failure"
            else:
                sent = "alive"
        except Exception:
            sent = "alive"
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            resp = self._open(req, timeout)
            try:
                status = getattr(resp, "status", None) or resp.getcode()
                resp.read(READ_AT_MOST)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            if isinstance(status, int) and 200 <= status < 300:
                text = "The service took the ping." if sent == "alive" else "The service was told the player is down."
                result = {"ok": True, "text": text, "how": how}
            else:
                result = {"ok": False, "text": "The service answered with %s." % status, "how": how}
        except Exception as e:                  # a ping must never raise into the thread or the panel
            try:
                result = {"ok": False, "text": words(e), "how": how}
            except Exception:
                result = {"ok": False, "text": "The ping failed.", "how": how}
        return self._remember(result)

    def _remember(self, result, count=True):
        result["at"] = int(self._clock())
        with self._lock:
            was_failing = self._failing
            self._last = result
            if count:
                self._failing = not result["ok"]
        if count and result["how"] == "auto" and result["ok"] == was_failing:     # said once when it changes, not every time
            try:
                self.log("nxlx-web: heartbeat %s" % ("working again" if result["ok"] else "failed: %s" % result["text"]))
            except Exception:
                pass
        return result

    def test(self):
        """The Owner's test: one ping now, the answer in plain words. None when another test is still running."""
        if not self._testing.acquire(blocking=False):
            return None
        try:
            return self.ping("test", TEST_TIMEOUT)
        finally:
            self._testing.release()

    # -- the background thread --
    def start(self):
        if self._thread:
            return
        cfg = self.config()
        if cfg["on"]:
            with self._lock:
                self._due = self._clock() + FIRST_PING_AFTER
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def tick(self):
        """One look: ping when the setting is on and the time has come. Separate from the loop so a test can drive it."""
        cfg = self.config()
        if not cfg["on"] or not cfg["url"]:
            with self._lock:
                self._due = None
            return False
        now = self._clock()
        with self._lock:
            if self._due is None:
                self._due = now + FIRST_PING_AFTER
            if now < self._due:
                return False
            self._due = now + cfg["minutes"] * 60
        self.ping("auto")
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                try:
                    self.log("nxlx-web: heartbeat: %s" % type(e).__name__)
                except Exception:
                    pass
            self._wake.wait(TICK)
            self._wake.clear()
