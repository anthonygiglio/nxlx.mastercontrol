# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""OSC control bridge (receive only).

Turns OSC messages from TouchOSC, Resolume, QLab, Max and similar into calls on
the same API the web panel uses, so every value is validated in one place and
fades and blackout behave identically.

Deliberate limits:
* OFF until switched on (System > Control).
* Only private networks may send (loopback, RFC 1918, link-local, unique-local)
  plus any ranges you add; UDP source addresses can be forged, so this stops
  the internet, not a hostile device on your own network.
* No replies are ever sent (no amplification, nothing to spoof).
* Nothing that shuts down, reboots, resets or reconfigures the box is reachable
  over OSC. The old /shutdown, /reboot and /shutdownall are refused.
* Per-sender rate limit; strict, bounded packet parsing.
"""

import ipaddress
import re
import socket
import struct
import threading
import time

MAX_PACKET = 8192
MAX_ARGS = 16
MAX_STRING = 256
MAX_BLOB = 1024
MAX_DEPTH = 4
MAX_MESSAGES = 64
RATE_PER_SECOND = 200.0
RATE_BURST = 400.0
OSC_DEVICE = {"id": "osc", "name": "OSC", "role": "live"}

PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10")]

# Old addresses that are refused on purpose (or not ported), so logs can say why.
REFUSED = {"/shutdown", "/reboot", "/rebootall", "/shutdownall", "/piwallmaster", "/piwallloop"}


class OscError(Exception):
    pass


# --- wire format ------------------------------------------------------------
def _padded(n):
    return (n + 3) & ~3


def _read_string(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        raise OscError("unterminated string")
    if end - pos > MAX_STRING:
        raise OscError("string too long")
    nxt = _padded(end + 1)
    if nxt > len(data):
        raise OscError("string padding runs past the packet")
    if any(data[end:nxt]):
        raise OscError("string padding is not zero")
    try:
        return data[pos:end].decode("utf-8"), nxt
    except UnicodeDecodeError:
        raise OscError("string is not UTF-8")


def _take(data, pos, size):
    if pos + size > len(data):
        raise OscError("argument runs past the packet")
    return data[pos:pos + size], pos + size


def parse_message(data):
    """Return (address, [args]). Accepts the old form with no type tag string."""
    address, pos = _read_string(data, 0)
    if not address.startswith("/"):
        raise OscError("address must start with /")
    if pos >= len(data):
        return address, []
    tags, pos = _read_string(data, pos)
    if not tags.startswith(","):
        raise OscError("bad type tag string")
    args = []
    for tag in tags[1:]:
        if tag in "[]":
            continue
        if tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag == "N":
            args.append(None)
        elif tag == "I":
            args.append(float("inf"))
        elif tag == "i":
            raw, pos = _take(data, pos, 4)
            args.append(struct.unpack(">i", raw)[0])
        elif tag == "f":
            raw, pos = _take(data, pos, 4)
            args.append(struct.unpack(">f", raw)[0])
        elif tag == "h":
            raw, pos = _take(data, pos, 8)
            args.append(struct.unpack(">q", raw)[0])
        elif tag == "d":
            raw, pos = _take(data, pos, 8)
            args.append(struct.unpack(">d", raw)[0])
        elif tag == "t":
            raw, pos = _take(data, pos, 8)
            args.append(struct.unpack(">Q", raw)[0])
        elif tag in "cs" or tag == "S":
            if tag == "c":
                raw, pos = _take(data, pos, 4)
                args.append(chr(struct.unpack(">I", raw)[0] & 0x10FFFF) if struct.unpack(">I", raw)[0] < 0x110000 else "")
            else:
                text, pos = _read_string(data, pos)
                args.append(text)
        elif tag in "rm":
            raw, pos = _take(data, pos, 4)
            args.append(raw)
        elif tag == "b":
            raw, pos = _take(data, pos, 4)
            size = struct.unpack(">i", raw)[0]
            if size < 0 or size > MAX_BLOB:
                raise OscError("bad blob size")
            blob, _ = _take(data, pos, size)
            pos = _padded(pos + size)
            if pos > len(data):
                raise OscError("blob padding runs past the packet")
            args.append(blob)
        else:
            raise OscError("unknown type tag %r" % tag)  # size unknown, cannot continue safely
        if len(args) > MAX_ARGS:
            raise OscError("too many arguments")
    return address, args


def parse_packet(data, depth=0):
    """A message or a (possibly nested) bundle, flattened to a list of (address, args)."""
    if len(data) > MAX_PACKET:
        raise OscError("packet too large")
    if not data:
        raise OscError("empty packet")
    if data.startswith(b"#bundle\0"):
        if depth >= MAX_DEPTH:
            raise OscError("bundles nested too deeply")
        if len(data) < 16:
            raise OscError("truncated bundle")
        pos, out = 16, []  # skip "#bundle\0" and the 8 byte time tag: messages run immediately
        while pos < len(data):
            raw, pos = _take(data, pos, 4)
            size = struct.unpack(">i", raw)[0]
            if size <= 0 or size % 4:
                raise OscError("bad bundle element size")
            element, pos = _take(data, pos, size)
            out.extend(parse_packet(element, depth + 1))
            if len(out) > MAX_MESSAGES:
                raise OscError("too many messages in one packet")
        return out
    return [parse_message(data)]


# --- who may send and how fast ----------------------------------------------
def parse_networks(items):
    nets = []
    for item in items:
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            raise OscError("bad network %r" % (item,))
    return nets


def source_allowed(ip, extra=()):
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in list(PRIVATE_NETS) + list(extra))


class RateLimiter:
    def __init__(self, clock=time.monotonic, rate=RATE_PER_SECOND, burst=RATE_BURST, max_sources=1024):
        self._clock, self._rate, self._burst, self._max = clock, rate, burst, max_sources
        self._buckets = {}

    def allow(self, source):
        now = self._clock()
        tokens, last = self._buckets.get(source, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        if len(self._buckets) >= self._max and source not in self._buckets:
            self._buckets.clear()  # bounded memory under a spray of forged sources
        if tokens < 1:
            self._buckets[source] = (tokens, now)
            return False
        self._buckets[source] = (tokens - 1, now)
        return True


# --- meaning ----------------------------------------------------------------
def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def pressed(args):
    """Buttons send 1 on press and 0 on release; only the press fires a trigger."""
    if not args:
        return True
    v = args[0]
    if v is True:
        return True
    if v is False or v is None:
        return False
    return _number(v) and v > 0.5


# Old names that start with /start but are not playback presets: sync and sound output need full access
# (System > Sync and video wall, Sound output), the audio player and the PDF presenter are not built.
NOT_HERE = {"/startslave", "/startaudio", "/startaudioslave", "/startaudiousb", "/startpdf", "/startpdfusb"}


def translate(address, args, mix=None):
    """Map an OSC message to (api_path, body), or None if it means nothing here."""
    a = address.rstrip("/") if len(address) > 1 else address

    def control(action, value=None):
        body = {"action": action}
        if value is not None:
            body["value"] = value
        return "/api/control", body

    def num(scale=1.0):
        return args[0] * scale if args and _number(args[0]) else None

    def flag():
        if not args:
            return None
        v = args[0]
        if isinstance(v, bool):
            return v
        return (v > 0.5) if _number(v) else None

    # -- stable names --------------------------------------------------------
    if a.startswith("/pvj/pad/"):
        parts = a.split("/")[3:]
        if len(parts) == 2 and all(p.isdigit() for p in parts) and pressed(args):
            return "/api/play", {"pad": [int(parts[0]) - 1, int(parts[1]) - 1]}
        return None
    if a == "/pvj/play/pad":
        if len(args) >= 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in args[:2]):
            return "/api/play", {"pad": [args[0] - 1, args[1] - 1]}
        return None
    if a == "/pvj/play/file":
        return ("/api/play", {"file": args[0]}) if args and isinstance(args[0], str) else None
    if a == "/pvj/play/preset":
        return ("/api/play", {"preset": args[0]}) if args and isinstance(args[0], str) else None
    if a in ("/pvj/vibes", "/pvj/vibes/next"):          # the shader rotation: start it, or go to the next shader
        return ("/api/vibes", {"on": True} if a == "/pvj/vibes" else {"next": True}) if pressed(args) else None
    if a == "/pvj/stop":
        return control("stop") if pressed(args) else None
    if a == "/pvj/pause":
        v = flag()
        if not args:
            return control("pause")
        return control("pause", v) if v is not None else None
    if a == "/pvj/blackout":
        v = flag()
        if v is None:
            v = not (mix or {}).get("blackout", False) if not args else None
        return ("/api/blackout", {"on": v}) if v is not None else None
    if a == "/pvj/fadeout":
        seconds = num() if args else 2.0
        return ("/api/fadeout", {"seconds": seconds}) if seconds is not None and pressed([]) else None
    if a == "/pvj/mix/reset":
        return control("reset") if pressed(args) else None
    if a in ("/pvj/loop", "/pvj/mute"):
        v = flag()
        return control(a.split("/")[2], v) if v is not None else None
    if a in ("/pvj/seek", "/pvj/speed", "/pvj/volume", "/pvj/opacity", "/pvj/size", "/pvj/position", "/pvj/rotate"):
        v = num()
        if v is None:
            return None
        return control(a.split("/")[2], int(v) if a == "/pvj/rotate" else v)

    # -- names from the old Node receiver that still make sense --------------
    legacy = {"/stopall": "stop", "/stopvideo": "stop", "/pause": "pause"}
    if a in legacy:
        return control(legacy[a]) if pressed(args) else None
    if a in ("/beameron", "/beameroff"):
        return ("/api/projector", {"id": "all", "action": "on" if a == "/beameron" else "off", "background": True}) if pressed(args) else None
    if a == "/fastforward":
        return control("seek", 10) if pressed(args) else None
    if a in ("/volumeup", "/volumedown"):
        return control("volume_step", 10 if a == "/volumeup" else -10) if pressed(args) else None
    if a in ("/rotate0", "/rotate90", "/rotate180", "/rotate270"):
        return control("rotate", int(a[len("/rotate"):])) if pressed(args) else None
    # Old names for things the new player does too (the old panel's TouchOSC layouts send these).
    simple = {
        "/testscreen": ("/api/testpattern", {"on": True}), "/testscreenoff": ("/api/testpattern", {"on": False}),
        "/testtone": ("/api/testtone", {"channel": "both"}), "/testtoneleft": ("/api/testtone", {"channel": "left"}),
        "/testtoneright": ("/api/testtone", {"channel": "right"}),
        "/overlay": ("/api/overlay", {"on": True}), "/stopoverlay": ("/api/overlay", {"on": False}),
        "/image": ("/api/play", {"slideshow": {"source": "media"}}), "/stopimage": control("stop"),
    }
    if a in simple:
        return simple[a] if pressed(args) else None
    if a in ("/fliph", "/flipv"):                     # the old buttons switched the mirror over each press
        key = "flip_h" if a == "/fliph" else "flip_v"
        return control(key, not (mix or {}).get(key, False)) if pressed(args) else None
    m = re.fullmatch(r"/startmasteronce([0-9]{2})", a)
    if m:                                             # the old receiver ran the startmasteroneNN scripts for these
        return ("/api/play", {"preset": "startmasterone" + m.group(1)}) if pressed(args) else None
    if a in NOT_HERE:
        return None
    if a.startswith("/start") and pressed(args):
        return "/api/play", {"preset": a[1:]}
    return None


class OscServer:
    def __init__(self, api, port=9876, extra_allow=(), host="0.0.0.0", clock=time.monotonic, log=print):
        self.api = api
        self.port = port
        self.host = host
        self.extra = list(extra_allow)
        self.log = log
        self.limiter = RateLimiter(clock)
        self._clock = clock
        self._sock = None
        self._thread = None
        self._quiet_until = {}
        self.stats = {"received": 0, "handled": 0, "dropped": 0}

    def _note(self, key, text):
        """Log a message at most once per 10 seconds per kind, so a flood cannot fill the journal."""
        now = self._clock()
        if self._quiet_until.get(key, 0) <= now:
            self._quiet_until[key] = now + 10
            self.log("osc: " + text)

    def handle_packet(self, data, source_ip):
        """Process one datagram. Returns the number of commands executed."""
        self.stats["received"] += 1
        if not source_allowed(source_ip, self.extra):
            self.stats["dropped"] += 1
            self._note("src:" + source_ip, "ignoring %s (not on an allowed network)" % source_ip)
            return 0
        if not self.limiter.allow(source_ip):
            self.stats["dropped"] += 1
            self._note("rate:" + source_ip, "rate limit hit by %s" % source_ip)
            return 0
        try:
            messages = parse_packet(data)
        except OscError as e:
            self.stats["dropped"] += 1
            self._note("parse", "bad packet from %s: %s" % (source_ip, e))
            return 0
        done = 0
        for address, args in messages:
            if address in REFUSED:
                self._note("refused:" + address, "%s is not available over OSC" % address)
                continue
            mapped = translate(address, args, self.api.mix)
            if mapped is None:
                continue
            status, payload = self.api.handle("POST", mapped[0], mapped[1], OSC_DEVICE, source_ip)
            if status == 200:
                done += 1
                self.stats["handled"] += 1
            else:
                self._note("err:" + address, "%s -> %s %s" % (address, status, payload.get("error", "")))
        return done

    # --- socket ---------------------------------------------------------
    def start(self):
        if self._sock:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            sock.close()
            raise OscError("cannot listen on UDP %d: %s" % (self.port, e))
        sock.settimeout(0.5)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._thread = threading.Thread(target=self._loop, daemon=True, name="osc")
        self._thread.start()

    def _loop(self):
        sock = self._sock
        while self._sock is sock:
            try:
                data, addr = sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.handle_packet(data, addr[0])
            except Exception as e:  # one bad packet must never kill the receiver
                self._note("bug", "internal error: %r" % (e,))

    def stop(self):
        sock, self._sock = self._sock, None
        if sock:
            sock.close()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def listening(self):
        return self._sock is not None


class OscManager:
    """Starts and stops the receiver from the saved settings."""

    def __init__(self, api, settings, host="0.0.0.0", log=print):
        self.api, self.settings, self.host, self.log = api, settings, host, log
        self.server = None
        self.error = None

    def apply(self):
        """Make reality match settings["osc"]. Raises OscError if the port cannot be opened."""
        cfg = self.settings.data["osc"]
        if self.server:
            self.server.stop()
            self.server = None
        self.error = None
        if not cfg["enabled"]:
            return
        server = OscServer(self.api, cfg["port"], parse_networks(cfg["allow"]), host=self.host, log=self.log)
        try:
            server.start()
        except OscError as e:
            self.error = str(e)
            raise
        self.server = server

    def status(self):
        cfg = self.settings.data["osc"]
        return {"enabled": cfg["enabled"], "port": cfg["port"], "allow": list(cfg["allow"]),
                "listening": bool(self.server and self.server.listening), "error": self.error,
                "received": self.server.stats["received"] if self.server else 0}

    def stop(self):
        if self.server:
            self.server.stop()
            self.server = None


def validate_allow(items):
    """Extra ranges allowed to send. Refuses anything so broad it would open the box to the internet."""
    if not isinstance(items, list) or len(items) > 16 or not all(isinstance(i, str) and len(i) < 50 for i in items):
        raise OscError("allow must be a list of up to 16 network ranges")
    nets = parse_networks(items)
    for net in nets:
        if net.version == 4 and net.prefixlen < 8 or net.version == 6 and net.prefixlen < 32:
            raise OscError("%s is too broad; list the specific range of your show network" % net)
    return [str(n) for n in nets]
