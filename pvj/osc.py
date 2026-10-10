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
* Three more layers, each off until the owner switches it on (D78): a list of single addresses that may send, a
  rule that the sender's address is one a paired panel device asked from lately, and a key every address must start
  with (`/k/<key>/pvj/stop`). None of them is encryption: UDP is readable and a source address can be forged by a
  device on the same network, and the key can be read from one captured packet. They keep out devices that are
  merely on the network. What was refused is counted and shown; nothing from a refused packet is applied or kept.
"""

import collections
import hmac
import ipaddress
import re
import secrets
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

# The three layers (D78). Every key below is optional in settings["osc"]: a file without them means "all off".
MAX_ONLY = 16                    # single addresses in "Only these devices may send"
PAIRED_ROLES = ("full", "live")  # "full": the owner's devices only; "live": owner and presenter. A guest never counts.
PAIRED_HOURS, PAIRED_HOURS_MAX = 12, 72
KEY_PREFIX = "/k/"
KEY_FORM = re.compile(r"[0-9a-f]{16,64}")
KEY_SECTION = "osc_secret"        # where the key is kept: a section of its own that no release exports or imports
SENDER_WINDOW = 600.0            # the page shows the senders of the last ten minutes
MAX_SENDERS = 64
MAX_LOG = 50
SHOWN_ADDRESS = 64               # characters of an OSC address that are kept for the page

WHY_NETWORK = "not on a private or listed network"
WHY_RATE = "sending too fast"
WHY_ONLY = "not in the list of devices that may send"
WHY_PAIRED = "no paired device at this address"
WHY_PACKET = "not a valid OSC packet"
WHY_NO_KEY = "no key in the address"
WHY_KEY = "wrong key"
WHY_TWICE = "the key prefix is there twice"
WHY_UNSET = "the key is switched on but none is set"
DID_NOTHING = "nothing to do (an unknown address, or a button release)"
NOT_OVER_OSC = "not available over OSC"
VALUE_REFUSED = "the value was refused"

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


def _address(ip):
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr


def plain_address(ip):
    """One spelling per address (an IPv4 address written the IPv6 way is the IPv4 one), or None for anything else."""
    addr = _address(ip)
    return None if addr is None else str(addr)


def source_allowed(ip, extra=()):
    addr = _address(ip)
    if addr is None:
        return False
    return any(addr in net for net in list(PRIVATE_NETS) + list(extra))


# --- the three layers: the list, the paired device, the key (D78) --------------
def new_key():
    return secrets.token_hex(10)       # 80 random bits, and only characters an OSC address may hold


def valid_key(key):
    return isinstance(key, str) and KEY_FORM.fullmatch(key) is not None


def split_key(address):
    """"/k/<key>/pvj/stop" -> (key, "/pvj/stop"); (None, None) when the address does not have that form."""
    if not address.startswith(KEY_PREFIX):
        return None, None
    given, slash, rest = address[len(KEY_PREFIX):].partition("/")
    if not given or not slash or not rest:
        return None, None
    return given, "/" + rest


def key_matches(given, key):
    """Constant time, on bytes: the given part comes from a stranger's packet and may hold any UTF-8."""
    return hmac.compare_digest(given.encode("utf-8"), key.encode("utf-8"))


def validate_only(items, extra=()):
    """The single addresses that may send. Each must be inside the outer wall (the private ranges or an extra
    network): the list narrows who may send, it never opens the box to an address the wall keeps out."""
    if not isinstance(items, list) or len(items) > MAX_ONLY or not all(isinstance(i, str) and len(i) < 50 for i in items):
        raise OscError("only must be a list of up to %d single addresses" % MAX_ONLY)
    out = []
    for item in items:
        plain = None if "/" in item else plain_address(item.strip())
        if plain is None:
            raise OscError("%r is not a single address" % (item,))
        if not source_allowed(plain, extra):
            raise OscError("%s is not on a private network; add its network under \"Also accept from these networks\" first" % plain)
        if plain not in out:
            out.append(plain)
    return out


def validate_layers(v, extra=()):
    """The layer keys that are present in `v` (a request or an imported file), checked. The key itself is never
    taken from outside: the box makes it (OscManager.make_key)."""
    out = {}
    for flag in ("only_on", "paired_on", "key_on"):
        if flag in v:
            if not isinstance(v[flag], bool):
                raise OscError("%s must be true or false" % flag)
            out[flag] = v[flag]
    if "only" in v:
        out["only"] = validate_only(v["only"], extra)
    if "paired_roles" in v:
        if v["paired_roles"] not in PAIRED_ROLES:
            raise OscError("paired_roles must be full or live")
        out["paired_roles"] = v["paired_roles"]
    if "paired_hours" in v:
        hours = v["paired_hours"]
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= PAIRED_HOURS_MAX:
            raise OscError("paired_hours must be 1 to %d" % PAIRED_HOURS_MAX)
        out["paired_hours"] = hours
    return out


def stored_key(data):
    """The key in a settings dict: its own section, or (a file written before the key moved there) under "osc"."""
    for section in (KEY_SECTION, "osc"):
        part = data.get(section)
        if isinstance(part, dict) and valid_key(part.get("key")):
            return part["key"]
    return ""


def layers(cfg, key=""):
    """settings["osc"] as the receiver reads it, with the key (kept apart, see stored_key). A missing setting is
    "off"; a damaged one fails closed (a list that is not a list lets nobody in, a key layer with no proper key
    refuses everything)."""
    only = cfg.get("only")
    only = [plain_address(a) for a in only if isinstance(a, str)] if isinstance(only, list) else []
    hours = cfg.get("paired_hours")
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= PAIRED_HOURS_MAX:
        hours = PAIRED_HOURS
    return {"only_on": cfg.get("only_on", False) is not False, "only": [a for a in only if a][:MAX_ONLY],
            "paired_on": cfg.get("paired_on", False) is not False,
            "paired_roles": cfg.get("paired_roles") if cfg.get("paired_roles") in PAIRED_ROLES else "full",
            "paired_hours": hours,
            "key_on": cfg.get("key_on", False) is not False, "key": key if valid_key(key) else ""}


def shown(address, key=""):
    """An address as the page or the journal may show it: never the key, printable ASCII only, and short."""
    if key:
        address = address.replace(key, "<key>")
    return re.sub(r"[^\x21-\x7e]", "?", address)[:SHOWN_ADDRESS]


class Watch:
    """Who sent lately and the last messages, for the owner's page. In memory only, bounded, and a refused packet
    leaves nothing here but its sender's address, a count and the reason: never a byte of what it carried."""

    def __init__(self, clock=time.monotonic, now=time.time):
        self._clock, self._now = clock, now
        self._lock = threading.Lock()
        self._senders = {}
        self._log = collections.deque(maxlen=MAX_LOG)
        self.refused = 0

    def clear(self):
        with self._lock:
            self._senders.clear()
            self._log.clear()
            self.refused = 0

    def note(self, source, ok, why="", address=""):
        """One verdict: `ok` and `address` for a message that passed every layer, else `why` it was refused."""
        source = plain_address(source) or "?"
        address = shown(address) if address else ""
        t, wall = self._clock(), int(self._now())
        with self._lock:
            if not ok and not address:
                self.refused += 1
            s = self._senders.get(source)
            if s is None:
                if len(self._senders) >= MAX_SENDERS:
                    self._make_room(t)
                s = self._senders[source] = {"messages": 0, "refused": 0, "wrong_keys": 0, "address": "", "passed": False}
            s["last"], s["at"], s["why"] = t, wall, why
            if address:                       # it passed every layer (whether or not it then meant anything)
                s["messages"] += 1
                s["address"], s["passed"] = address, True
            else:
                s["refused"] += 1
                s["wrong_keys"] += why == WHY_KEY
                s["passed"] = False
            last = self._log[-1] if self._log else None
            if last and not address and (last["from"], last["why"], last["address"]) == (source, why, ""):
                last["count"] += 1            # a flood of the same refusal is one line
                last["at"] = wall
            else:
                self._log.append({"at": wall, "from": source, "address": address, "ok": bool(ok), "why": why, "count": 1})

    def _make_room(self, t):
        old = [k for k, s in self._senders.items() if t - s["last"] > SENDER_WINDOW]
        if not old:      # a spray of forged senders pushes out refused ones first, not the tablet that works
            refused = [k for k, s in self._senders.items() if not s["messages"]]
            old = [min(refused or self._senders, key=lambda k: self._senders[k]["last"])]
        for k in old:
            del self._senders[k]

    def senders(self):
        t = self._clock()
        with self._lock:
            rows = [dict(address=k, messages=s["messages"], refused=s["refused"], last=s["address"], accepted=s["passed"],
                         why=s["why"], at=s["at"], wrong_keys=s["wrong_keys"]) for k, s in self._senders.items() if t - s["last"] <= SENDER_WINDOW]
            order = {k: s["last"] for k, s in self._senders.items()}
        return sorted(rows, key=lambda r: -order[r["address"]])

    def messages(self):
        with self._lock:
            return [dict(m) for m in reversed(self._log)]


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


_ROOM_SCENE = re.compile(r"/pvj/scene/([1-9][0-9]?)")
_ROOM_GROUP = re.compile(r"/pvj/group/(all|[1-9][0-9]?)/(on|off|mute|mute_picture|mute_sound|input)")


def _room(a, args):
    """The Room module's scenes and group buttons (ROOM.md): /pvj/scene/<n> (press), /pvj/scene with a name or a
    number, /pvj/group/<n or all>/on, off (press), mute, mute_picture, mute_sound (1 or 0) and input (a code)."""
    m = _ROOM_SCENE.fullmatch(a)
    if m:
        return ("/api/room/scene", {"number": int(m.group(1))}) if pressed(args) else None
    if a == "/pvj/scene":
        v = args[0] if args else None
        if isinstance(v, str):
            return "/api/room/scene", {"name": v}
        if isinstance(v, float) and _number(v) and v == int(v):       # a controller that only sends floats
            v = int(v)
        return ("/api/room/scene", {"number": v}) if isinstance(v, int) and not isinstance(v, bool) else None
    m = _ROOM_GROUP.fullmatch(a)
    if not m:
        return None
    body = {"group": "all"} if m.group(1) == "all" else {"number": int(m.group(1))}
    what, v = m.group(2), (args[0] if args else None)
    if what in ("on", "off"):
        if not pressed(args):
            return None
        body["action"] = what
    elif what == "input":                             # "31", 31 or 31.0 (a controller that only sends floats)
        if _number(v) and v == int(v):
            v = str(int(v))
        if not isinstance(v, str):
            return None
        body["action"], body["input"] = "input", v
    else:
        on = v if isinstance(v, bool) else ((v > 0.5) if _number(v) else None)
        if on is None:
            return None
        body["action"] = what if on else "un" + what
    return "/api/room/group", body


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
    if a.startswith(("/pvj/scene", "/pvj/group/")):
        return _room(a, args)
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
    if a == "/pvj/vibes/set":                           # start the rotation through a set, named by its name or id
        return ("/api/vibes", {"on": True, "set": args[0]}) if args and isinstance(args[0], str) else None
    if a == "/pvj/vibes/previous":
        return ("/api/vibes", {"previous": True}) if pressed(args) else None
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
    def __init__(self, api, port=9876, extra_allow=(), host="0.0.0.0", clock=time.monotonic, log=print,
                 rules=None, paired=None, watch=None, key=""):
        self.api = api
        self.port = port
        self.host = host
        self.extra = list(extra_allow)
        self.log = log
        self.limiter = RateLimiter(clock)
        self.rules = layers(rules or {}, key)
        self.paired = paired or (lambda roles, hours: set())   # (roles, hours) -> the addresses that count now
        self.watch = watch or Watch(clock)
        self._clock = clock
        self._sock = None
        self._thread = None
        self._quiet_until = {}
        self.stats = {"received": 0, "handled": 0, "dropped": 0}

    def _note(self, key, text):
        """Log a message at most once per 10 seconds per kind, so a flood cannot fill the journal."""
        now = self._clock()
        if len(self._quiet_until) > 2048:
            self._quiet_until.clear()
        if self._quiet_until.get(key, 0) <= now:
            self._quiet_until[key] = now + 10
            self.log("osc: " + text)

    def _refuse(self, source_ip, why):
        """A packet that is not let in: counted, its sender and the reason noted, and nothing else."""
        self.stats["dropped"] += 1
        self.watch.note(source_ip, False, why)
        self._note(why + ":" + source_ip, "ignoring %s (%s)" % (source_ip, why))
        return 0

    def _unlock(self, messages, rules):
        """With the key layer on: every message of the packet must carry the key, or the whole packet is refused
        (returns the reason). The messages come back without the prefix. With the layer off a prefix is just dropped,
        so a layout that has it goes on working. Nothing here depends on who sent: a wrong key is counted for the
        owner to see and never held against the sender's address, which anyone on the network can forge (a limit
        per address would be a way to lock the real tablet out, and 80 random bits need none)."""
        out = []
        if rules["key_on"] and not rules["key"]:
            return None, WHY_UNSET
        for address, args in messages:
            given, rest = split_key(address)
            if rules["key_on"]:
                if given is None:
                    return None, WHY_NO_KEY
                if not key_matches(given, rules["key"]):
                    return None, WHY_KEY
            address = rest or address
            if address.startswith(KEY_PREFIX):     # "/k/<key>/k/<key>/...": the rest would be shown with a key in it
                return None, WHY_TWICE
            out.append((address, args))
        return out, None

    def handle_packet(self, data, source_ip):
        """Process one datagram. Returns the number of commands executed. The order: the outer wall (private and
        extra networks), the rate limit, the list of devices, the paired device, the packet's form, the key. A
        packet must pass every layer that is on before anything in it is looked at for meaning."""
        self.stats["received"] += 1
        rules = self.rules
        if not source_allowed(source_ip, self.extra):
            return self._refuse(source_ip, WHY_NETWORK)
        if not self.limiter.allow(source_ip):
            return self._refuse(source_ip, WHY_RATE)
        plain = plain_address(source_ip)
        if rules["only_on"] and plain not in rules["only"]:
            return self._refuse(source_ip, WHY_ONLY)
        if rules["paired_on"] and plain not in self.paired(rules["paired_roles"], rules["paired_hours"]):
            return self._refuse(source_ip, WHY_PAIRED)
        try:
            messages = parse_packet(data)
        except OscError as e:
            self.stats["dropped"] += 1
            self.watch.note(source_ip, False, WHY_PACKET)
            self._note("parse", "bad packet from %s: %s" % (source_ip, e))
            return 0
        messages, why = self._unlock(messages, rules)
        if why:
            return self._refuse(source_ip, why)
        done = 0
        for address, args in messages:
            seen = shown(address, rules["key"])      # for the page and the journal: never with the key in it
            if address in REFUSED:
                self.watch.note(source_ip, False, NOT_OVER_OSC, seen)
                self._note("refused:" + address, "%s is not available over OSC" % address)
                continue
            mapped = translate(address, args, self.api.mix)
            if mapped is None:
                self.watch.note(source_ip, False, DID_NOTHING, seen)
                continue
            status, payload = self.api.handle("POST", mapped[0], mapped[1], OSC_DEVICE, source_ip)
            if status == 200:
                done += 1
                self.stats["handled"] += 1
                self.watch.note(source_ip, True, "", seen)
            else:
                self.watch.note(source_ip, False, VALUE_REFUSED, seen)
                self._note("err:" + seen, "%s -> %s %s" % (seen, status, payload.get("error", "")))
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
        self.watch = Watch()        # outlives a save of the page (apply makes a new receiver each time)
        # One change at a time: two saves at once (two taps on the page, two devices) would each stop the receiver
        # and open the port again, and the slower one would find it "in use" and take the other's change back.
        self.lock = threading.RLock()

    def _paired(self, roles, hours):
        """The addresses from which a paired device of those roles made a panel request in the last `hours`."""
        auth = getattr(self.api, "auth", None)
        if auth is None:
            return set()
        allowed = ("full",) if roles == "full" else ("full", "live")
        return {plain_address(a) for a in auth.paired_addresses(hours * 3600, allowed)} - {None}

    def key(self):
        return stored_key(self.settings.data)

    def make_key(self):
        """A new key, in its own section (the caller saves and applies). The old one stops working at once."""
        key = new_key()
        with self.settings.lock:
            self.settings.data[KEY_SECTION] = {"key": key}
            self.settings.data["osc"].pop("key", None)
        return key

    def _move_key(self):
        """A key that an earlier build of this change kept under "osc" goes to its own section, once (no schema
        change: this runs whenever the receiver is set up, and does nothing when there is nothing to move)."""
        with self.settings.lock:
            cfg = self.settings.data["osc"]
            if "key" not in cfg:
                return
            old = cfg.pop("key")
            if valid_key(old) and not valid_key((self.settings.data.get(KEY_SECTION) or {}).get("key")):
                self.settings.data[KEY_SECTION] = {"key": old}
            self.settings.save()

    def apply(self):
        """Make reality match settings["osc"]. Raises OscError if the port cannot be opened."""
        with self.lock:
            self._move_key()
            cfg = self.settings.data["osc"]
            if self.server and self.server.listening and cfg["enabled"] and self.server.port == cfg["port"]:
                # Same port: who may send changes in place. The socket stays open, so no message is lost while the
                # owner switches a lock or allows a device in the middle of a show.
                self.server.extra = parse_networks(cfg["allow"])
                self.server.rules = layers(cfg, self.key())
                self.error = None
                return
            if self.server:
                self.server.stop()
                self.server = None
            self.error = None
            if not cfg["enabled"]:
                return
            server = OscServer(self.api, cfg["port"], parse_networks(cfg["allow"]), host=self.host, log=self.log,
                               rules=cfg, paired=self._paired, watch=self.watch, key=self.key())
            try:
                server.start()
            except OscError as e:
                self.error = str(e)
                raise
            self.server = server

    def status(self, full=False):
        """What every paired device may read; with `full` also the lists (never the key: see Api.osc_key)."""
        cfg = self.settings.data["osc"]
        rules = layers(cfg, self.key())
        out = {"enabled": cfg["enabled"], "port": cfg["port"], "allow": list(cfg["allow"]),
               "listening": bool(self.server and self.server.listening), "error": self.error,
               "received": self.server.stats["received"] if self.server else 0,
               "only_on": rules["only_on"], "paired_on": rules["paired_on"], "key_on": rules["key_on"]}
        if full:
            counting = self._paired(rules["paired_roles"], rules["paired_hours"])
            # The receiver listens on IPv4 only: a device that opened the panel over IPv6 is paired, and can never
            # be the sender of a packet. It is named apart, so the lock does not look satisfied when it is not.
            v6 = sorted(a for a in counting if ":" in a)
            panel = self._paired("live", rules["paired_hours"])      # a panel device of the owner or a presenter
            senders = [dict(s, panel=s["address"] in panel) for s in self.watch.senders()]
            out.update(only=rules["only"], paired_roles=rules["paired_roles"], paired_hours=rules["paired_hours"],
                       key_set=bool(rules["key"]), refused=self.watch.refused, senders=senders,
                       paired_now=sorted(counting - set(v6)), paired_v6=v6)
        return out

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
