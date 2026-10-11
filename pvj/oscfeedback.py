# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box's state, sent back over OSC so a TouchOSC layout shows what is really playing (D82).

A box that answers over UDP can be made to send packets at someone else, so everything here is built to refuse that:

* OFF until the Owner switches it on, and it only sends while OSC itself is on.
* It sends ONLY to the targets the Owner named in the panel (at most MAX_TARGETS): a plain IPv4 address in a private
  or link-local range, on a subnet the box is directly connected to AT THE MOMENT OF SENDING, never a broadcast,
  multicast or loopback address and never the box itself, and a port from 1024 up. No packet ever adds a target.
* A received packet decides nothing about where anything goes. The receiver only sets a flag ("this target asked for
  everything"), and only for a sender that passed every lock and whose address is a listed target.
* No byte of a received packet is ever copied into a packet that is sent. Every packet is built from the box's state.
* A hard cap on datagrams and bytes, per target and in all, whatever comes in (the numbers are below).
* Nothing secret is in any packet: no PIN, code or key, no device name or address, no network detail, no path.
* State has its own namespace, /pvj/state/..., which is no command anywhere in the receiver, so a target that is
  another box (or two boxes that list each other) is driven by nothing.
* One thread of its own and a socket that never waits: a dead target holds up nobody. The socket's time to live is 1,
  so a packet cannot cross a router even if a check above were wrong.
"""

import ipaddress
import os
import re
import socket
import struct
import threading
import time

MAX_TARGETS = 4
MIN_PORT = 1024                 # below it sit DNS, NTP and SNMP: what a reflected packet is aimed at
NAME_FORM = re.compile(r"[^\x00-\x1f\x7f]{1,32}")
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
NOT_INTERFACES = ("lo", "wg-pvj")       # loopback and the support tunnel are never "the box's own local network"

PREFIX = "/pvj/state/"
REQUEST = "/pvj/state"          # a listed target asks for everything; the box itself never sends this address
ALIVE = PREFIX + "alive"

MAX_DATAGRAM = 1200             # bytes: under any network's packet size, so nothing is ever split
TICK = 0.2                      # one look at the box this often: changes within it leave as one
HEARTBEAT = 2.0                 # /pvj/state/alive this often, changed or not
FULL_EVERY = 30.0               # everything again this often, for a layout that was opened late
REQUEST_EVERY = 2.0             # a target's request for everything is honoured at most this often
NETS_EVERY = 10.0               # the box's own networks are read again this often
# The caps. A bucket holds `burst` and fills at `rate` a second: never more than rate + burst in any second.
TARGET_PACKETS, TARGET_PACKET_BURST = 15.0, 5.0         # one target: at most 20 datagrams in any second
TARGET_BYTES, TARGET_BYTE_BURST = 9000.0, 3000.0        # and at most 12,000 bytes
ALL_PACKETS, ALL_PACKET_BURST = 45.0, 15.0              # all targets together: at most 60 datagrams in any second
ALL_BYTES, ALL_BYTE_BURST = 27000.0, 9000.0             # and at most 36,000 bytes
MAX_TEXT = 96                   # characters of a name in a packet

WHY_NETWORK = "not on one of the box's own networks now"
WHY_LOCKS = "not let in by the locks that are on"
SLOTS = 8


class FeedbackError(ValueError):
    pass


# --- who may be a target --------------------------------------------------------------------------------------------
def local_nets(ip_json):
    """The box's IPv4 addresses with their subnets (ipaddress.IPv4Interface), from `ip -j addr`'s answer, without
    loopback and the support tunnel. Anything that cannot be read is left out: fewer networks, never more."""
    out = []
    try:
        for entry in ip_json or []:
            if entry.get("ifname") in NOT_INTERFACES:
                continue
            for a in entry.get("addr_info", []):
                if a.get("family") != "inet":
                    continue
                try:
                    out.append(ipaddress.IPv4Interface("%s/%d" % (a["local"], int(a["prefixlen"]))))
                except (KeyError, TypeError, ValueError):
                    continue
    except (AttributeError, TypeError):
        return []
    return out


def address_form(text):
    """An IPv4Address for a string that is one plain IPv4 address, written the one usual way, in a private or
    link-local range. Raises FeedbackError for everything else: a name, IPv6, a range, leading zeros, a public
    address, loopback, multicast, 255.255.255.255."""
    if not isinstance(text, str) or len(text) > 15:
        raise FeedbackError("the address must be an IPv4 address like 192.168.1.50")
    try:
        addr = ipaddress.IPv4Address(text)
    except ValueError:
        raise FeedbackError("the address must be an IPv4 address like 192.168.1.50 (not a name, and not IPv6)")
    if str(addr) != text:
        raise FeedbackError("write the address the plain way, like 192.168.1.50")
    if not any(addr in net for net in PRIVATE) or addr.is_multicast or addr.is_loopback:
        raise FeedbackError("%s is not an address on a private network; state is only sent to devices on the box's own network" % addr)
    return addr


def not_local(addr, nets):
    """None when `addr` (an IPv4Address or its text) is another device on one of the box's own subnets right now,
    else the reason as a sentence part. The box's own addresses, a subnet's network address and its broadcast
    address are never another device."""
    try:
        addr = address_form(str(addr))
    except FeedbackError:
        return "not an address on a private network"
    if any(addr == i.ip for i in nets):
        return "the box's own address"
    for i in nets:
        net = i.network
        if addr in net and any(net.subnet_of(p) for p in PRIVATE):
            if net.prefixlen <= 30 and addr in (net.network_address, net.broadcast_address):
                return "the network's own or broadcast address, not a device"
            return None
    return WHY_NETWORK


def _target_form(item):
    if not isinstance(item, dict) or set(item) - {"name", "address", "port"}:
        raise FeedbackError("a target is a name, an address and a port")
    name, port = item.get("name"), item.get("port")
    if not isinstance(name, str) or not NAME_FORM.fullmatch(name.strip()):
        raise FeedbackError("a target needs a name of 1 to 32 characters")
    if isinstance(port, bool) or not isinstance(port, int) or not MIN_PORT <= port <= 65535:
        raise FeedbackError("the port must be %d to 65535" % MIN_PORT)
    return {"name": name.strip(), "address": str(address_form(item.get("address"))), "port": port}


def validate_targets(items, nets=None):
    """The list of targets, checked. With `nets` (the box's own networks now) each must also be another device on
    one of them; without, only the form is checked (an imported file: where it may really be sent is decided again
    at every send)."""
    if not isinstance(items, list) or len(items) > MAX_TARGETS:
        raise FeedbackError("at most %d devices can be sent the state" % MAX_TARGETS)
    out = []
    for item in items:
        t = _target_form(item)
        if nets is not None:
            why = not_local(t["address"], nets)
            if why:
                raise FeedbackError("%s is %s" % (t["address"], why))
        if any((t["address"], t["port"]) == (o["address"], o["port"]) for o in out):
            raise FeedbackError("%s port %d is in the list already" % (t["address"], t["port"]))
        out.append(t)
    return out


def validate(v):
    """The feedback keys that are present in `v` (an imported file), form only."""
    out = {}
    if "feedback_on" in v:
        if not isinstance(v["feedback_on"], bool):
            raise FeedbackError("feedback_on must be true or false")
        out["feedback_on"] = v["feedback_on"]
    if "feedback" in v:
        out["feedback"] = validate_targets(v["feedback"])
    return out


def stored(cfg):
    """(on, targets) as the sender reads settings["osc"]. Fails closed: only a real `true` is on, and a list that is
    damaged in any way is no list."""
    try:
        targets = validate_targets(cfg.get("feedback", []))
    except (FeedbackError, AttributeError):
        return False, []
    return cfg.get("feedback_on") is True, targets


# --- the wire -------------------------------------------------------------------------------------------------------
def _string(text):
    raw = text.encode("utf-8") + b"\0"
    return raw + b"\0" * (-len(raw) % 4)


def encode(address, value):
    """One OSC message with one argument: a float32 for a number, a string for text. Nothing else is ever sent."""
    if isinstance(value, str):
        return _string(address) + _string(",s") + _string(value)
    return _string(address) + _string(",f") + struct.pack(">f", float(value))


_BUNDLE = b"#bundle\0" + struct.pack(">Q", 1)       # time tag 1: "now"


def pack(messages):
    """[(address, value)] -> [(datagram, [the messages in it])], each datagram at most MAX_DATAGRAM bytes: one message
    alone as it is, several as one bundle."""
    out, part, size = [], [], len(_BUNDLE)
    for m in messages:
        raw = encode(*m)
        if part and size + 4 + len(raw) > MAX_DATAGRAM:
            out.append(part)
            part, size = [], len(_BUNDLE)
        part.append((m, raw))
        size += 4 + len(raw)
    if part:
        out.append(part)
    done = []
    for part in out:
        if len(part) == 1:
            data = part[0][1]
        else:
            data = _BUNDLE + b"".join(struct.pack(">i", len(raw)) + raw for _m, raw in part)
        done.append((data, [m for m, _raw in part]))
    return done


class Bucket:
    def __init__(self, rate, burst, now):
        self.rate, self.burst, self.have, self.at = rate, burst, burst, now

    def fill(self, now):
        self.have = min(self.burst, self.have + max(0.0, now - self.at) * self.rate)
        self.at = now


# --- what is sent ---------------------------------------------------------------------------------------------------
def _text(v):
    """A name as it goes into a packet: text only, short, no control characters."""
    return re.sub(r"[\x00-\x1f\x7f]", "", v if isinstance(v, str) else "")[:MAX_TEXT]


def _num(v, default=0.0, digits=3):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v or abs(v) == float("inf"):
        return default
    return round(v, digits)


def _stem(name):
    return name[:-3] if isinstance(name, str) and name.endswith(".fs") else (name or "")


def clip_name(api, path):
    """The display name of what plays: the file's own name when it is a file in the media folder (or a drive's), and
    nothing for everything else. Never a folder, and never a stream's address (it can hold a password)."""
    if not isinstance(path, str) or not path.startswith("/") or "://" in path:
        return ""
    try:
        from . import shaders
        if shaders.is_carrier(path) or path == getattr(api.player, "TEST_PATTERN", None):
            return ""
        capture = getattr(api, "capture", None)
        if capture is not None and path == getattr(capture, "fifo", None):
            return ""
    except Exception:
        return ""
    return _text(os.path.basename(path))


def fade_word(label, at):
    """idle, out (the picture is going down), black (it is down, from a fade out), in (it is coming up)."""
    if label == "in":
        return "in"
    if label == "out":
        return "black" if at is not None and at <= 0 else "out"
    return "idle"


_PARSED = {}        # (shader id, digest) -> its parsed inputs: the file is read once per shader, not at every look


def _shader_controls(api, on):
    """The eight controls of the shader on screen as 0 to 1, as /pvj/shader/control/<n> takes them. A switch is 0 or
    1, a choice its place in the list. 0 for a control that is not there."""
    out = [0.0] * SLOTS
    try:
        engine = api.shaders
        key = (on["id"], on.get("digest"))
        parsed = _PARSED.get(key)
        if parsed is None:
            parsed = engine._parsed(engine._path(on["id"])[0])[0]
            _PARSED.clear()
            _PARSED[key] = parsed
        values = engine.current_values(parsed, on)
        for n in range(1, SLOTS + 1):
            i = engine.nth_control(parsed, n)
            if i is None or i["type"] == "event":
                continue
            v = values.get(i["name"], i.get("default"))
            if i["type"] == "bool":
                out[n - 1] = 1.0 if v else 0.0
            elif "values" in i:
                out[n - 1] = _num(i["values"].index(v) / max(1, len(i["values"]) - 1)) if v in i["values"] else 0.0
            elif i["max"] > i["min"]:
                out[n - 1] = min(1.0, max(0.0, _num((float(v) - i["min"]) / (i["max"] - i["min"]))))
    except Exception:
        pass
    return out


def build(api, snap, raw):
    """Every state message but the heartbeat, in the order of the table in pvj/OSC.md: [(address, value)], each value
    a float or a string. `snap` is boxstate.snapshot's picture (the one the controller lights read) and `raw` the
    player's own last answer. Reads memory only. A part that cannot be read keeps its quiet value."""
    from . import midi
    out = []

    def add(tail, value):
        out.append((PREFIX + tail, _text(value) if isinstance(value, str) else _num(value)))

    def flag(tail, value):
        add(tail, 1.0 if value else 0.0)

    def safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    running = bool(snap.get("running"))
    flag("playing", running)
    flag("pause", running and snap.get("paused"))
    add("clip/name", clip_name(api, raw.get("path")) if running else "")
    count = raw.get("playlist_count") if isinstance(raw.get("playlist_count"), int) else 0
    pos = raw.get("playlist_pos") if isinstance(raw.get("playlist_pos"), int) else -1
    add("clip/number", (pos + 1 if pos >= 0 else 1) if running else 0)
    add("clip/count", max(count, 1) if running else 0)
    at, length = _num(raw.get("position")), _num(raw.get("duration"))
    add("time", int(at) if running else 0)
    add("length", _num(length, digits=1) if running else 0)
    add("progress", _num(min(1.0, max(0.0, at / length)), digits=2) if running and length > 0 else 0)

    mix = safe(lambda: dict(api.mix), {})
    fader = getattr(api, "fader", None)
    word = fade_word(snap.get("fade"), getattr(fader, "at", None))
    add("fade", word)
    flag("fade/on", word != "idle")
    flag("blackout", snap.get("blackout"))
    add("transition", safe(lambda: __import__("pvj.transitions", fromlist=["named"]).named(api.settings.data["mix"]), ""))
    add("transition/duration", safe(lambda: api.settings.data["mix"]["duration"], 0))
    add("opacity", mix.get("opacity", 100))
    add("size", mix.get("size", 100))
    add("position/x", mix.get("position", 0))
    add("position/y", mix.get("position_y", 0))
    add("rotate", mix.get("rotate", 0))
    flag("flip/h", mix.get("flip_h"))
    flag("flip/v", mix.get("flip_v"))
    levels = safe(lambda: dict(api.levels), {})
    add("speed", _num(raw.get("speed"), _num(levels.get("speed"), 1.0)) if running else _num(levels.get("speed"), 1.0))
    add("volume", _num(raw.get("volume"), _num(levels.get("volume"), 100.0)) if running else _num(levels.get("volume"), 100.0))
    yes = (True, "inf", "yes")
    flag("loop", running and (raw.get("loop_file") in yes or raw.get("loop_playlist") in yes))
    flag("mute", running and raw.get("muted") is True)
    flag("overlay", safe(lambda: api.settings.data["overlay"]["on"] is True, False))

    on = safe(lambda: api.shaders.playing, None) if snap.get("shader_on") else None
    controls = (on or {}).get("controls") or {}
    add("shader", _stem(snap.get("shader")) if on else "")
    add("shader/speed", controls.get("speed", 1.0) if on else 1.0)
    add("shader/hue", controls.get("hue", 0.0) if on else 0.0)
    add("shader/brightness", controls.get("brightness", 1.0) if on else 1.0)
    for n, v in enumerate(_shader_controls(api, on) if on else [0.0] * SLOTS, 1):
        add("shader/control/%d" % n, v)
    for n in range(1, SLOTS + 1):
        flag("shader/preset/%d" % n, safe(lambda: midi.light_state({"action": "shader_preset_%d" % n}, snap) == "active", False))
    effect = safe(lambda: api.effects._seen(), None) if snap.get("effect") else None
    add("effect", _stem(snap.get("effect")) if snap.get("effect") else "")
    flag("effect/on", snap.get("effect"))
    add("effect/amount", _num(((effect or {}).get("controls") or {}).get("amount", 1.0)) * 100 if effect else 0)
    flag("vibes", snap.get("vibes"))
    sets = {v: k for k, v in (snap.get("sets") or {}).items()}
    add("vibes/set", sets.get(snap.get("set"), "") if snap.get("vibes_ready") else "")
    add("vibes/dwell", safe(lambda: api.shaders.rotation(snap.get("set"))["dwell"], 0) if snap.get("vibes_ready") else 0)
    flag("mapping/mode", snap.get("mapping"))
    flag("guests/open", safe(lambda: not api.guests.locked(), False))

    pads = snap.get("pads") or []
    lit = [0, 0]
    rows = []
    for b, bank in enumerate(pads[:3]):
        for p in range(len(bank[:12])):
            active = safe(lambda: midi.light_state({"action": "pad", "bank": b, "index": p}, snap) == "active", False)
            if active and not lit[0]:
                lit = [b + 1, p + 1]
            rows.append(("pad/%d/%d" % (b + 1, p + 1), active))
    add("pad/bank", lit[0])
    add("pad/number", lit[1])
    for tail, active in rows:
        flag(tail, active)
    return out


# --- the sender -----------------------------------------------------------------------------------------------------
class Feedback:
    """Sends the state to the targets. `allowed(address)` says whether the locks that are on would let that address
    send now (OscManager); `nets()` gives the box's own networks (local_nets). Everything that touches the box or
    the network happens in `tick`, on this object's own thread; `request` and `wake`, which the receiver calls, only
    set a flag."""

    def __init__(self, api, targets, allowed=None, nets=None, clock=time.monotonic, log=print, sock=None, state=None):
        self.api, self._clock, self.log = api, clock, log
        self._allowed = allowed or (lambda address: True)
        self._nets_fn = nets or (lambda: [])
        self._state_fn = state or self._state
        self._lock = threading.Lock()       # the flags and the counters; never held over a look at the box or a send
        self._targets = [dict(t) for t in targets]
        self._wanted = set()                # addresses of targets that asked for everything
        self._per = {}                      # (address, port) -> what was sent there last, its buckets, its counters
        self._nets = (None, [])
        self._all = None
        self._sock, self._own_sock = sock, sock is None
        self._thread = self._stop = None
        self._wake = threading.Event()
        self._player_seen = None            # boxstate keeps the player's last answer here
        self._player_raw = {}
        self._noted = {}
        self.stats = {"datagrams": 0, "bytes": 0, "failed": 0, "held": 0}

    # -- called by the receiver and the manager: flags only --
    def configure(self, targets):
        with self._lock:
            self._targets = [dict(t) for t in targets]
            keep = {(t["address"], t["port"]) for t in self._targets}
            self._per = {k: v for k, v in self._per.items() if k in keep}
            self._wanted &= {t["address"] for t in self._targets}

    def request(self, source):
        """A sender that passed every lock asked for everything. True when its address is a listed target: only
        then, and only for that target. The answer goes out on the feedback thread, at most every REQUEST_EVERY."""
        with self._lock:
            if not any(t["address"] == source for t in self._targets):
                return False
            self._wanted.add(source)
        self._wake.set()
        return True

    def wake(self):
        self._wake.set()

    def status(self):
        now = self._clock()
        with self._lock:
            out = []
            for t in self._targets:
                st = self._per.get((t["address"], t["port"])) or {}
                last = st.get("last")
                out.append(dict(t, last_sent=None if last is None else max(0, int(now - last)), why=st.get("why", "")))
            return out

    # -- the feedback thread --
    def _note(self, key, text):
        now = self._clock()
        if self._noted.get(key, 0) <= now:
            self._noted[key] = now + 60
            self.log("osc: state: " + text)

    def _state(self, now):
        from . import boxstate
        snap = boxstate.snapshot(self.api, now, False, self, 0.4)
        return build(self.api, snap, self._player_raw or {})

    def _local(self, now):
        at, nets = self._nets
        if at is None or now - at >= NETS_EVERY or now < at:
            try:
                nets = list(self._nets_fn())
            except Exception:
                nets = []
            self._nets = (now, nets)
        return nets

    def _slot(self, key, now):
        st = self._per.get(key)
        if st is None:
            st = self._per[key] = {"sent": {}, "full": now, "asked": None, "beat": None, "last": None, "why": "",
                                   "packets": Bucket(TARGET_PACKETS, TARGET_PACKET_BURST, now),
                                   "bytes": Bucket(TARGET_BYTES, TARGET_BYTE_BURST, now)}
        return st

    def _send(self, st, data, where, now):
        """One datagram, if every cap has room for it. False when a cap holds it (it is not queued: what it carried
        is still "not sent" and goes with a later look, as the newest values)."""
        buckets = (st["packets"], st["bytes"], self._all[0], self._all[1])
        costs = (1.0, float(len(data)), 1.0, float(len(data)))
        for b in buckets:
            b.fill(now)
        if len(data) > MAX_DATAGRAM or any(b.have < c for b, c in zip(buckets, costs)):
            self.stats["held"] += 1
            return False
        for b, c in zip(buckets, costs):
            b.have -= c
        self.stats["datagrams"] += 1
        self.stats["bytes"] += len(data)
        try:
            self._sock.sendto(data, where)
            st["last"] = now
        except (OSError, ValueError):       # no route, a full buffer, a closed socket: counted, never waited for
            self.stats["failed"] += 1
        return True

    def tick(self, now=None):
        """One look at the box and what follows from it for every target. Returns the datagrams handed to the socket."""
        now = self._clock() if now is None else now
        if self._all is None:
            self._all = (Bucket(ALL_PACKETS, ALL_PACKET_BURST, now), Bucket(ALL_BYTES, ALL_BYTE_BURST, now))
        with self._lock:
            targets = [dict(t) for t in self._targets]
            wanted = set(self._wanted)
        nets = self._local(now)
        live = []
        for t in targets:
            key = (t["address"], t["port"])
            why = not_local(t["address"], nets)
            if not why:
                try:
                    why = "" if self._allowed(t["address"]) is True else WHY_LOCKS
                except Exception:
                    why = WHY_LOCKS
            with self._lock:
                st = self._slot(key, now)
                st["why"] = why or ""
                if why:
                    st["sent"] = {}          # everything again when it is back
                    self._wanted.discard(t["address"])
            if not why:
                live.append((t, st))
        if not live or self._sock is None:
            return 0
        try:
            state = self._state_fn(now)
        except Exception as e:
            self._note("state", "could not read the box's state: %r" % (e,))
            return 0
        before = self.stats["datagrams"]
        for t, st in live:
            if t["address"] in wanted and (st["asked"] is None or now - st["asked"] >= REQUEST_EVERY):
                st["asked"], st["sent"], st["full"] = now, {}, now
                with self._lock:
                    self._wanted.discard(t["address"])
            if now - st["full"] >= FULL_EVERY:
                st["sent"], st["full"] = {}, now
            messages = [(ALIVE, 1.0)] if st["beat"] is None or now - st["beat"] >= HEARTBEAT else []
            messages += [m for m in state if st["sent"].get(m[0]) != m[1]]
            for data, part in pack(messages):
                if not self._send(st, data, (t["address"], t["port"]), now):
                    break
                for address, value in part:
                    if address == ALIVE:
                        st["beat"] = now
                    else:
                        st["sent"][address] = value
        return self.stats["datagrams"] - before

    def _open(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)                                     # a send never waits
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)        # and never crosses a router
        return sock

    def start(self):
        if self._thread:
            return
        if self._sock is None:
            self._sock = self._open()
        stop = self._stop = threading.Event()       # this loop's own signal (see MidiHub._lights_loop)
        self._thread = threading.Thread(target=self._loop, args=(stop,), daemon=True, name="osc-feedback")
        self._thread.start()

    def _loop(self, stop):
        while not stop.is_set():
            woken = self._wake.wait(TICK)
            if stop.is_set():
                return
            self._wake.clear()
            if woken:
                stop.wait(0.05)             # a flood of wakes is at most 20 looks a second; what leaves is capped anyway
            try:
                self.tick()
            except Exception as e:
                self._note("bug", "internal error: %r" % (e,))

    def stop(self):
        stop, thread, self._thread = self._stop, self._thread, None
        if stop:
            stop.set()
        self._wake.set()
        if thread:
            thread.join(timeout=2)
        sock = self._sock
        if sock is not None and self._own_sock:
            self._sock = None
            sock.close()

    @property
    def running(self):
        return self._thread is not None
