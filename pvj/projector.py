# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Projector control over PJLink (class 1): the old panel's Beamer On and Beamer Off, and more.

PJLink is the common network control standard for projectors (Epson, NEC, Panasonic, Sony, Hitachi, Eiki, Christie
and most others): TCP port 4352, one short text command per connection. With a projector password set, the projector
sends a random number and every command is prefixed with MD5(random + password), as the standard requires (PJLink's
own design; it keeps the password off the wire, nothing more).

Commands used, all class 1 (PJLink Specifications 1.04, chapter 4): power on and off and the power state (POWR),
the input (INPT) from the projector's own list (INST), picture and sound mute apart or together (AVMT), the warnings
(ERST), the lamp hours (LAMP), and who the projector is (NAME, INF1, INF2, INFO, CLSS).

Epson projectors also speak Epson's own control, ESC/VP.net on TCP 3629 (D99): freeze, brightness, the Eco light
setting and the colour mode, which PJLink class 1 has not. A projector is set to "pjlink", "epson" or "both"; with
"both", PJLink does power, input and mutes and Epson control the picture settings. Both go through the same
private-address check and the same one-command-at-a-time lock per projector.

A Monitor asks each projector for its state now and then in the background, one small thread per projector with
its own stop signal, so the panel shows on, off, warming up or cooling down without asking; the same thread
retries an input change that the projector refused as "unavailable" (it does that while warming up).

Only projectors on a private network can be added (IP addresses in 10/8, 172.16/12, 192.168/16, 169.254/16 and their
IPv6 equivalents, or a name that resolves to one), so the panel cannot be used to make connections to the internet.
"""

import hashlib
import ipaddress
import re
import socket
import threading
import time
import unicodedata
import uuid

PORT = 4352
MAX_PROJECTORS = 8
POWER = {"0": "off", "1": "on", "2": "cooling down", "3": "warming up"}
INPUT_KINDS = {"1": "RGB", "2": "Video", "3": "Digital", "4": "Storage", "5": "Network"}     # the first digit of an input
WARNINGS = ("fan", "lamp", "temperature", "cover", "filter", "other")                        # the order of ERST's six digits
MUTE = {"picture": "1", "sound": "2", "both": "3"}
POLL_EVERY = 45.0                # seconds between two status checks of one projector
POLL_CHANGING = 10.0             # while it warms up or cools down
STAGGER = 2.0                    # projector n starts n x this later, so they are never all asked at once
RETRY_FOR, RETRY_EVERY = 90.0, 5.0      # an input change refused as "unavailable" is tried again this long, this often
ERRORS = {"ERR1": "the projector does not know that command", "ERR2": "the projector refused that value",
          "ERR3": "the projector cannot do that right now", "ERR4": "the projector reports a fault",
          "ERRA": "wrong projector password"}
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                             "fc00::/7", "fe80::/10")]
REFUSED = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}     # the cloud metadata service (x86 in a VM), IPv4 and IPv6
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}|[0-9A-Fa-f:.]{2,45}")
_INPUT = re.compile(r"[1-5][1-9]")
SOFT = ("ERR1", "ERR2", "ERR3", "ERR4", "ERR", "odd")     # an answer, but no usable value: a detail that is simply not known (yet)

# Epson ESC/VP.net (D99): Epson's own control on TCP 3629, beside PJLink. It reaches what PJLink class 1 cannot:
# freeze, brightness, the light source's Eco setting and the colour mode. Measured on an Epson X27 (2026-10-19).
PROTOCOLS = ("pjlink", "epson", "both")
PROTOCOL_NAMES = {"pjlink": "PJLink", "epson": "Epson control", "both": "PJLink and Epson control"}
ESCVP_PORT = 3629
ESCVP_HELLO = b"ESC/VP.net\x10\x03\x00\x00\x00\x00"       # "connect", version 1.0, no headers
ESCVP_REFUSED = {
    0x40: "the projector did not understand the Epson control request",
    0x41: "the projector asks for a password for Epson control, which this box cannot send; use PJLink, or switch the projector's Epson control password off",
    0x43: "the projector refused the Epson control password",
    0x45: "the projector does not allow Epson control from this box right now",
    0x53: "the projector is busy with another Epson control connection",
    0x55: "the projector does not speak this version of Epson control"}
EPSON_POWER = {"00": "off", "01": "on", "02": "warming up", "03": "cooling down", "04": "off", "05": "off", "09": "off"}
# ERR? answers, from Epson's ESC/VP21 list for business projectors, into the six PJLink warning names (only 00 was
# seen on a real projector). Anything else is "other".
EPSON_ERRORS = {"01": "fan", "03": "lamp", "04": "temperature", "06": "lamp", "07": "cover", "08": "filter", "0C": "fan", "0D": "filter"}
# The colour modes the panel may try, as Epson's codes. Treated as opaque: the names differ by model, so the panel
# shows the code. A code the projector refuses ("ERR": 07, 0C and 14 on the X27) is not offered again until the
# panel restarts; one it reports as in use, or takes, is marked as known to work.
COLOUR_MODES = ("01", "02", "03", "04", "05", "06", "07", "08", "0C", "14")
EPSON_SLOW = 8.0                 # seconds for an answer to a power or colour mode change (CMODE 06 took 3.2 s on the X27)
VIEW_HOLD = 30.0                 # seconds after the panel last read the list during which the projectors are checked often
_ESC_COMMAND = re.compile(r"[A-Z]{2,10}(\?| [0-9A-Z]{1,4})")
_ESC_VALUE = re.compile(r"[0-9A-Za-z][0-9A-Za-z ._-]{0,31}")


def protocol_of(entry):
    """How the box talks to this projector. An entry saved before D99 has no protocol: it is PJLink."""
    p = entry.get("protocol", "pjlink")
    return p if p in PROTOCOLS else "pjlink"


def epson(entry):
    return protocol_of(entry) in ("epson", "both")


class ProjectorError(Exception):
    """`code` says why, for code that must decide: ERR1 to ERR4 and ERRA from the projector, "busy" (another
    command of ours is still running), "unreachable" (no connection or no answer), "odd" (an answer that is not
    what the standard says), "stopped" (the background check was told to stop), or None."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def input_name(code):
    """ "31" -> "Digital 1": what the standard calls the input; the projector's own names are class 2."""
    return "%s %s" % (INPUT_KINDS.get(code[:1], "Input"), code[1:])


def _unprintable(ch):
    """Control and format characters (Unicode Cc, Cf and the other C categories: C0, C1, the bidi overrides, zero
    width marks), the line and paragraph separators, and the "could not decode" mark."""
    return unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp") or ch == "\ufffd"


def clean_text(value, limit):
    """Text from a projector, safe to store and show: no control or format characters, cut to `limit` characters."""
    return "".join(ch for ch in value if not _unprintable(ch)).strip()[:limit]


def validate_label(inputs, code, label):
    """A friendly label ("Matrix", "Box") for one of the projector's own inputs; "" takes the label away."""
    if not isinstance(code, str) or not _INPUT.fullmatch(code) or code not in inputs:
        raise ProjectorError("that is not one of this projector's inputs")
    if not isinstance(label, str) or len(label.strip()) > 24 or any(_unprintable(ch) for ch in label):
        raise ProjectorError("a label may have up to 24 plain characters")
    return label.strip()


def private_address(host, resolve=socket.getaddrinfo):
    """The IP address to connect to for `host`, only if it is on a private network. Raises ProjectorError."""
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ProjectorError("enter the projector's IP address or name")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = resolve(host, PORT, proto=socket.IPPROTO_TCP)
        except (OSError, UnicodeError):          # a name that is not valid for DNS raises UnicodeError
            raise ProjectorError("cannot find %s on the network" % host)
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
        if not addrs:
            raise ProjectorError("cannot find %s on the network" % host)
        addr = addrs[0]
    if addr in REFUSED or not any(addr in n for n in PRIVATE):
        raise ProjectorError("only projectors on a private network (such as 192.168.x.x) can be added")
    return str(addr)


def validate(entry):
    """A clean projector entry {"id", "name", "host", "port", "password"} from untrusted input."""
    if not isinstance(entry, dict):
        raise ProjectorError("a projector must be an object")
    name = entry.get("name", "Projector")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or any(_unprintable(ch) for ch in name):
        raise ProjectorError("give the projector a name of up to 40 characters")
    host = entry.get("host")
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ProjectorError("enter the projector's IP address or name")
    port = entry.get("port", PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ProjectorError("port must be 1 to 65535")
    password = entry.get("password", "")
    if not isinstance(password, str) or len(password) > 32 or re.search(r"[^\x21-\x7e]", password):
        raise ProjectorError("the password may have up to 32 plain characters")
    protocol = entry.get("protocol", "pjlink")
    if protocol not in PROTOCOLS:
        raise ProjectorError("protocol must be pjlink, epson or both")
    out = {"id": uuid.uuid4().hex[:8], "name": name.strip(), "host": host, "port": port, "password": password}
    if protocol != "pjlink":
        out["protocol"] = protocol         # PJLink is kept as no key at all, as every entry saved before D99
    return out


EDITABLE = ("name", "host", "port", "password", "protocol")


def validate_edit(old, change):
    """The fields of `change` ({"id", "name"?, "host"?, "port"?, "password"?}) that were sent, checked exactly as
    for a new projector: {field: clean value}. A field left out is not in the answer, so it keeps its stored value
    (the password too); an empty password is a value and takes the password away."""
    if not isinstance(change, dict) or not isinstance(change.get("id"), str):
        raise ProjectorError("edit must be {id, name, host, port, password}")
    sent = [k for k in EDITABLE if k in change]
    if not sent:
        raise ProjectorError("nothing to change: send a name, host, port, password or protocol")
    merged = {k: old[k] if k in old else protocol_of(old) for k in EDITABLE}      # only the protocol may be missing (before D99)
    merged.update({k: change[k] for k in sent})
    clean = validate(merged)
    return {k: clean.get(k, "pjlink") for k in sent}      # {"protocol": "pjlink"} is a change: the API then drops the key


def _read_line(sock, limit, deadline):
    """One PJLink line: it ends with a carriage return alone (not a line feed), so readline() would wait forever.
    `deadline` (time.monotonic) bounds the whole line, so a device that sends a byte now and then cannot hold us.
    A line that never ends (longer than `limit` bytes) or is cut off (the device closes first) is refused."""
    buf = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("timed out")
        sock.settimeout(left)
        c = sock.recv(1)
        if not c:
            if buf:
                raise ProjectorError("unexpected answer from the projector", "odd")
            raise ProjectorError("the projector did not answer: it closed the connection", "unreachable")
        if c in (b"\r", b"\n"):
            if buf:
                break
            continue
        buf += c
        if len(buf) > limit:
            raise ProjectorError("unexpected answer from the projector", "odd")
    return buf.decode("utf-8", "replace").strip()      # ASCII, except the projector's name, which is UTF-8


_busy = {}                       # (address, port) -> [Lock, users]: one command at a time per projector (many take one connection)
_busy_guard = threading.Lock()


def _take(key, deadline):
    """The lock of the projector at `key` (its resolved address and port, so two spellings of one device share
    it), or None if it is not free by `deadline`. Entries are counted and go when nobody uses them."""
    with _busy_guard:
        slot = _busy.setdefault(key, [threading.Lock(), 0])
        slot[1] += 1
    if slot[0].acquire(timeout=max(0.0, deadline - time.monotonic())):
        return slot
    _drop(key, slot)
    return None


def _drop(key, slot):
    with _busy_guard:
        slot[1] -= 1
        if slot[1] <= 0 and _busy.get(key) is slot:
            del _busy[key]


class PJLink:
    """One command per connection, as projectors expect. One deadline of 2 x timeout covers the wait for an
    earlier command of ours to the same projector, the connect and both reads; a name lookup is not bounded
    by us (use an IP address to avoid it). `cancel` (a threading.Event), once set, means nothing more is sent."""

    def __init__(self, host, port=PORT, password="", timeout=5.0, connect=socket.create_connection, resolve=socket.getaddrinfo):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._connect, self._resolve = connect, resolve
        self.cancel = None

    def _stopped(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ProjectorError("stopped", "stopped")

    def command(self, body):
        """Send "%1" + body (e.g. "POWR 1") and return the answer after "=". Raises ProjectorError."""
        self._stopped()
        addr = private_address(self.host, self._resolve)       # checked again at every use: a name may change
        deadline = time.monotonic() + 2 * self.timeout
        key = (addr, self.port)
        slot = _take(key, deadline)
        if slot is None:
            raise ProjectorError("the projector is busy with another command", "busy")
        try:
            return self._command(addr, body, deadline)
        finally:
            slot[0].release()
            _drop(key, slot)

    def _command(self, addr, body, deadline):
        try:
            self._stopped()
            s = self._connect((addr, self.port), timeout=min(self.timeout, max(0.1, deadline - time.monotonic())))
        except OSError as e:
            raise ProjectorError("cannot reach the projector at %s: %s" % (self.host, getattr(e, "strerror", None) or e), "unreachable")
        try:
            greeting = _read_line(s, 128, deadline)
            if greeting == "PJLINK ERRA":
                raise ProjectorError(ERRORS["ERRA"], "ERRA")
            if greeting == "PJLINK 0":
                prefix = ""
            elif re.fullmatch(r"PJLINK 1 [0-9A-Fa-f]{8}", greeting):
                if not self.password:
                    raise ProjectorError("this projector needs a password")
                prefix = hashlib.md5((greeting.split()[2] + self.password).encode()).hexdigest()
            else:
                raise ProjectorError("that does not answer like a PJLink projector")
            self._stopped()
            s.sendall((prefix + "%1" + body + "\r").encode("ascii"))
            answer = _read_line(s, 300, deadline)
        except (OSError, socket.timeout) as e:
            raise ProjectorError("the projector did not answer: %s" % e, "unreachable")
        finally:
            s.close()
        if answer == "PJLINK ERRA":
            raise ProjectorError(ERRORS["ERRA"], "ERRA")
        m = re.fullmatch(r"%1([A-Za-z0-9]{4})=(.*)", answer)       # INF1 and INF2 have a digit; the case is free
        if not m or m.group(1).upper() != body.split()[0]:
            raise ProjectorError("unexpected answer from the projector", "odd")
        value = m.group(2)
        if value.upper() in ERRORS:
            raise ProjectorError(ERRORS[value.upper()], value.upper())
        return value

    def _set(self, body):
        """A set command: the only good answer is OK (2.3 of the standard)."""
        if self.command(body).upper() != "OK":
            raise ProjectorError("unexpected answer from the projector", "odd")
        return "OK"

    def power(self, on):
        return self._set("POWR 1" if on else "POWR 0")

    def state(self):
        v = self.command("POWR ?")
        return POWER.get(v, "unknown")

    def mute(self, on, what="both"):
        """Mute or unmute the "picture", the "sound" or "both". A projector without separate mutes refuses those."""
        try:
            return self._set("AVMT %s%d" % (MUTE[what], 1 if on else 0))
        except ProjectorError as e:
            if e.code == "ERR2" and what != "both":
                raise ProjectorError("this projector cannot mute the picture and the sound separately", "ERR2")
            raise

    def mute_state(self):
        """{"picture": bool, "sound": bool}. The standard names four answers: 11, 21, 31 (both) and 30 (neither);
        its table of values also allows 10 and 20, which can only mean "not muted"."""
        v = self.command("AVMT ?")
        if v not in ("11", "21", "31", "30", "10", "20"):
            raise ProjectorError("unexpected answer from the projector", "odd")
        return {"picture": v in ("11", "31"), "sound": v in ("21", "31")}

    def input(self):
        """The input in use, such as "31"."""
        v = self.command("INPT ?")
        if not _INPUT.fullmatch(v):
            raise ProjectorError("unexpected answer from the projector", "odd")
        return v

    def set_input(self, code):
        if not isinstance(code, str) or not _INPUT.fullmatch(code):
            raise ProjectorError("the projector has no such input", "ERR2")
        try:
            return self._set("INPT " + code)
        except ProjectorError as e:
            if e.code == "ERR2":
                raise ProjectorError("the projector has no such input", "ERR2")
            raise

    def inputs(self):
        """The projector's own list of inputs, such as ["11", "31", "32"] (at most 50, says the standard). An
        answer with no class 1 input in it is "odd", not an empty list: a projector without inputs is no use."""
        out = []
        for c in self.command("INST ?").split()[:50]:
            if _INPUT.fullmatch(c) and c not in out:
                out.append(c)
        if not out:
            raise ProjectorError("unexpected answer from the projector", "odd")
        return out

    def lamps(self):
        """[{"hours": int, "on": bool}] for up to 8 lamps. A display without a lamp answers ERR1."""
        parts = self.command("LAMP ?").split()
        if not parts or len(parts) % 2 or len(parts) > 16 or not all(re.fullmatch(r"[0-9]{1,5}", h) and o in ("0", "1")
                                                                     for h, o in zip(parts[::2], parts[1::2])):
            raise ProjectorError("unexpected answer from the projector", "odd")
        return [{"hours": int(h), "on": o == "1"} for h, o in zip(parts[::2], parts[1::2])]

    def warnings(self):
        """{"fan": "ok" | "warning" | "error", "lamp": ..., "temperature", "cover", "filter", "other"}."""
        v = self.command("ERST ?")
        if not re.fullmatch(r"[0-2]{6}", v):
            raise ProjectorError("unexpected answer from the projector", "odd")
        return {name: ("ok", "warning", "error")[int(d)] for name, d in zip(WARNINGS, v)}

    def identify(self):
        """Who the projector is: {"name", "maker", "model", "info", "class", "inputs"}. A value the projector
        would not give right now (many refuse some of these in standby), or gave in a form that is not the
        standard's, is None; a projector that cannot be reached at all raises at the first question, so an
        unplugged one costs one timeout, not six."""
        def ask(fn):
            try:
                return fn()
            except ProjectorError as e:
                if e.code in SOFT:
                    return None
                raise
        out = {"class": ask(lambda: clean_text(self.command("CLSS ?"), 1))}
        if out["class"] is not None and not re.fullmatch(r"[1-9]", out["class"]):
            out["class"] = None
        for key, cmd, limit in (("name", "NAME ?", 64), ("maker", "INF1 ?", 32), ("model", "INF2 ?", 32), ("info", "INFO ?", 32)):
            out[key] = ask(lambda: clean_text(self.command(cmd), limit))
        out["inputs"] = ask(self.inputs)
        return out


def _read_exact(sock, n, deadline):
    """Exactly n bytes before `deadline`, or ProjectorError."""
    buf = b""
    while len(buf) < n:
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("timed out")
        sock.settimeout(left)
        c = sock.recv(n - len(buf))
        if not c:
            raise ProjectorError("the projector closed the Epson control connection", "unreachable" if not buf else "odd")
        buf += c
    return buf


def _read_answer(sock, limit, deadline):
    """One ESC/VP.net answer: everything before the ":" that ends it, bounded by `limit` bytes and by `deadline`.
    Read a byte at a time, so nothing after the ":" is taken from the next answer."""
    buf = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("timed out")
        sock.settimeout(left)
        c = sock.recv(1)
        if not c:
            if buf:
                raise ProjectorError("unexpected answer from the projector", "odd")
            raise ProjectorError("the projector did not answer: it closed the connection", "unreachable")
        if c == b":":
            return buf.decode("ascii", "replace").strip()
        buf += c
        if len(buf) > limit:
            raise ProjectorError("unexpected answer from the projector", "odd")


def _parse_answer(cmd, text):
    """The value of a query ("PWR?" answered "PWR=01" is "01"), "" for a set that was taken, or a ProjectorError
    (returned, not raised, so one refusal does not end the others asked on the same connection)."""
    if text == "ERR":
        if cmd.endswith("?"):
            return ProjectorError("the projector would not say %s now" % cmd[:-1], "ERR")
        return ProjectorError("the projector refused %s: it may be warming up, cooling down, showing its menu, "
                              "or not have that setting" % cmd.split()[0], "ERR")
    if cmd.endswith("?"):
        key, _, value = text.partition("=")
        if key != cmd[:-1] or not _ESC_VALUE.fullmatch(value):
            return ProjectorError("unexpected answer from the projector", "odd")
        return value
    return "" if text == "" else ProjectorError("unexpected answer from the projector", "odd")


class EscVp:
    """Epson ESC/VP.net on TCP 3629: the 16 byte "connect" header and its 16 byte answer, then commands that end
    with a carriage return, each answered by text ending in ":"; "ERR" means refused. Several commands may share
    one connection (session), each with its own bounded wait. The connect is bounded by `connect_timeout`, each
    answer by `timeout` (EPSON_SLOW for a power or colour mode change). One command at a time per projector: the
    same lock as its PJLink commands (`lock_port` is its PJLink port), so the two never talk to it at once.
    `cancel` (a threading.Event), once set, means nothing more is sent."""

    MAX = 16                         # commands in one session
    SLOW = ("PWR", "CMODE", "LUMINANCE")

    def __init__(self, host, lock_port=PORT, port=None, timeout=5.0, connect_timeout=3.0, slow=EPSON_SLOW,
                 connect=socket.create_connection, resolve=socket.getaddrinfo):
        self.host, self.lock_port = host, lock_port
        self.port = port if port is not None else ESCVP_PORT       # read when made, so a test can point it at a fake
        self.timeout, self.connect_timeout, self.slow = timeout, connect_timeout, slow
        self._connect, self._resolve = connect, resolve
        self.cancel = None

    def _stopped(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ProjectorError("stopped", "stopped")

    def session(self, commands):
        """Send each of `commands` ("PWR?", "MUTE ON") on one connection: a list with, for each, the value of a
        query, "" for a set that was taken, or a ProjectorError for one that was refused or answered oddly.
        Raises ProjectorError when there is no connection or no answer at all."""
        if not 1 <= len(commands) <= self.MAX or not all(isinstance(c, str) and _ESC_COMMAND.fullmatch(c) for c in commands):
            raise ProjectorError("not an Epson control command", "odd")
        self._stopped()
        addr = private_address(self.host, self._resolve)        # checked again at every use: a name may change
        key = (addr, self.lock_port)
        slot = _take(key, time.monotonic() + 2 * self.timeout)
        if slot is None:
            raise ProjectorError("the projector is busy with another command", "busy")
        try:
            return self._session(addr, commands)
        finally:
            slot[0].release()
            _drop(key, slot)

    def _session(self, addr, commands):
        try:
            self._stopped()
            s = self._connect((addr, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise ProjectorError("cannot reach the projector's Epson control at %s: %s" % (self.host, getattr(e, "strerror", None) or e), "unreachable")
        out = []
        try:
            s.sendall(ESCVP_HELLO)
            reply = _read_exact(s, 16, time.monotonic() + self.timeout)
            if reply[:10] != b"ESC/VP.net" or reply[11] != 0x03:
                raise ProjectorError("that does not answer like an Epson projector (ESC/VP.net)", "odd")
            if reply[14] != 0x20:
                raise ProjectorError(ESCVP_REFUSED.get(reply[14], "the projector refused the Epson control connection (code %02X)" % reply[14]), "refused")
            if reply[15]:                                       # headers the projector added: 18 bytes each, not used
                _read_exact(s, 18 * min(reply[15], 8), time.monotonic() + self.timeout)
            for cmd in commands:
                self._stopped()
                s.sendall((cmd + "\r").encode("ascii"))
                wait = self.slow if not cmd.endswith("?") and cmd.split()[0] in self.SLOW else self.timeout
                out.append(_parse_answer(cmd, _read_answer(s, 128, time.monotonic() + wait)))
        except (OSError, socket.timeout) as e:
            raise ProjectorError("the projector did not answer: %s" % e, "unreachable")
        finally:
            s.close()
        return out

    def query(self, key):
        v = self.session([key + "?"])[0]
        if isinstance(v, ProjectorError):
            raise v
        return v

    def send(self, cmd):
        v = self.session([cmd])[0]
        if isinstance(v, ProjectorError):
            raise v
        return "OK"


def _value(v, pattern):
    return v if isinstance(v, str) and re.fullmatch(pattern, v) else None


def epson_picture(answers):
    """{"freeze", "brightness", "light", "colour_mode"} from the answers to PICTURE_QUERIES; None for each not known."""
    freeze, light, bright, cmode = answers
    b = _value(bright, r"[0-9]{1,3}")
    return {"freeze": {"ON": True, "OFF": False}.get(freeze if isinstance(freeze, str) else None),
            "light": {"00": "normal", "01": "eco"}.get(light if isinstance(light, str) else None),
            "brightness": int(b) if b is not None and int(b) <= 255 else None,
            "colour_mode": _value(cmode, r"[0-9A-F]{2}")}


def epson_warnings(answer):
    """ERR?'s two digits in PJLink's shape ({"fan": "ok", ...}): 00 is none; any other is an error under its name."""
    err = _value(answer, r"[0-9A-F]{2}")
    if err is None:
        return None
    return {k: ("error" if err != "00" and EPSON_ERRORS.get(err, "other") == k else "ok") for k in WARNINGS}


PICTURE_QUERIES = ["FREEZE?", "LUMINANCE?", "BRIGHT?", "CMODE?"]
STATUS_QUERIES = ["PWR?", "LAMP?", "ERR?", "MUTE?", "SOURCE?"] + PICTURE_QUERIES


def epson_status(esc):
    """The whole status over Epson control alone, in one connection, in the Monitor's shape. In standby a projector
    refuses most of these; only the power state decides whether it "answers"."""
    a = esc.session(STATUS_QUERIES)
    power = EPSON_POWER.get(a[0]) if isinstance(a[0], str) else None
    if power is None:
        raise a[0] if isinstance(a[0], ProjectorError) else ProjectorError("unexpected answer from the projector", "odd")
    st = {"ok": True, "checked": int(time.time()), "power": power, "input": None, "mute": None}
    lamp = _value(a[1], r"[0-9]{1,5}")
    st["lamps"] = [{"hours": int(lamp), "on": power == "on"}] if lamp is not None else None
    st["warnings"] = epson_warnings(a[2])
    if power == "on":
        mute = {"ON": True, "OFF": False}.get(a[3] if isinstance(a[3], str) else None)
        st["mute"] = None if mute is None else {"picture": mute, "sound": mute}
        st["source"] = _value(a[4], r"[0-9A-F]{2}")
        st["picture"] = epson_picture(a[5:])
    return st


class EpsonLink:
    """A projector reached over Epson control alone, with the methods of PJLink that the Monitor, the API and the
    room call. What only PJLink has (its input list, input changes, separate picture and sound mutes) is refused
    as "not known" (ERR1), so a status check or a room scene carries on without it."""

    NO_INPUTS = "inputs are chosen over PJLink: set this projector to PJLink and Epson control"

    def __init__(self, host, lock_port=PORT, **kw):
        self.esc = EscVp(host, lock_port=lock_port, **kw)

    @property
    def cancel(self):
        return self.esc.cancel

    @cancel.setter
    def cancel(self, event):
        self.esc.cancel = event

    def power(self, on):
        return self.esc.send("PWR ON" if on else "PWR OFF")

    def state(self):
        return EPSON_POWER.get(self.esc.query("PWR"), "unknown")

    def mute(self, on, what="both"):
        if what != "both":
            raise ProjectorError("over Epson control this projector mutes the picture and the sound together", "ERR2")
        return self.esc.send("MUTE ON" if on else "MUTE OFF")

    def mute_state(self):
        v = {"ON": True, "OFF": False}.get(self.esc.query("MUTE"))
        if v is None:
            raise ProjectorError("unexpected answer from the projector", "odd")
        return {"picture": v, "sound": v}

    def input(self):
        raise ProjectorError(self.NO_INPUTS, "ERR1")

    def set_input(self, code):
        raise ProjectorError(self.NO_INPUTS, "ERR1")

    def inputs(self):
        raise ProjectorError(self.NO_INPUTS, "ERR1")

    def lamps(self):
        v = _value(self.esc.query("LAMP"), r"[0-9]{1,5}")
        if v is None:
            raise ProjectorError("unexpected answer from the projector", "odd")
        return [{"hours": int(v), "on": False}]

    def warnings(self):
        w = epson_warnings(self.esc.query("ERR"))
        if w is None:
            raise ProjectorError("unexpected answer from the projector", "odd")
        return w

    def identify(self):
        """What Epson control says of who it is: only a serial number (the X27 refuses PRODUCT?)."""
        a = self.esc.session(["SNO?"])[0]
        serial = clean_text(a, 32) if isinstance(a, str) else None
        return {"class": None, "name": None, "maker": "Epson", "model": None, "info": "serial " + serial if serial else None, "inputs": None}


def probe(host, port=PORT, password="", timeout=5.0, connect=socket.create_connection, resolve=socket.getaddrinfo):
    """What answers at `host`, for the Owner's Test before a projector is added: {"pjlink": {...}, "epson": {...},
    "suggest": a protocol or None}. Both are asked at once, each bounded; nothing is changed on the projector and
    the password is never in the answer (only whether one is needed and whether the one given was right)."""
    private_address(host, resolve)                              # a public address raises here, before anything is sent
    out = {"pjlink": {"ok": False}, "epson": {"ok": False}}

    def pj():
        r = out["pjlink"]
        link = PJLink(host, port, "", timeout=timeout, connect=connect, resolve=resolve)
        try:
            try:
                r["power"], r["password"] = link.state(), "none"
            except ProjectorError as e:
                if str(e) != "this projector needs a password" or not password:
                    raise
                link.password = password
                r["power"], r["password"] = link.state(), "right"
            r["ok"] = True
            got = link.identify()
            r.update({k: got.get(k) for k in ("maker", "model", "name")})
        except ProjectorError as e:
            if r.get("ok"):
                return                                          # it answered; a detail it would not give is just not known
            r["error"] = str(e)
            if e.code == "ERRA":
                r["password"] = "wrong"
            elif str(e) == "this projector needs a password":
                r["password"] = "needed"

    def ep():
        r = out["epson"]
        esc = EscVp(host, lock_port=port, timeout=timeout, connect=connect, resolve=resolve)
        try:
            a = esc.session(["PWR?", "SNO?"])
            if not isinstance(a[0], str) or a[0] not in EPSON_POWER:
                raise a[0] if isinstance(a[0], ProjectorError) else ProjectorError("unexpected answer from the projector", "odd")
            r.update(ok=True, power=EPSON_POWER[a[0]], serial=clean_text(a[1], 32) if isinstance(a[1], str) else None)
        except ProjectorError as e:
            r["error"] = str(e)
    threads = [threading.Thread(target=f, daemon=True) for f in (pj, ep)]
    for t in threads:
        t.start()
    end = time.monotonic() + 4 * timeout
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    for r in out.values():
        if not r["ok"] and "error" not in r:
            r["error"] = "the projector did not answer in time"
    pj_ok = out["pjlink"]["ok"] or out["pjlink"].get("password") in ("needed", "wrong")      # it is there; the password is for the person to fix
    ep_ok = out["epson"]["ok"]
    out["suggest"] = "both" if pj_ok and ep_ok else "pjlink" if pj_ok else "epson" if ep_ok else None
    return out


class _Worker:
    def __init__(self, pid):
        self.pid = pid
        self.stop = threading.Event()         # this worker's own: a newer worker never revives an old one
        self.wake = threading.Event()
        self.thread = None
        self.due = 0.0                        # time.monotonic() of the next status check
        self.identify = False                 # read the details at the next turn
        self.pending = None                   # {"input", "until", "next"}: an input change being retried
        self.asked_inputs = False             # asked for the input list since it was last seen switched on
        self.last = None                      # time.monotonic() of the last status check that was made


class Monitor:
    """The background status of every projector, and the retry of a refused input change.

    One worker per projector, each with its own stop signal, on at most MAX_PROJECTORS threads in all, counting
    those of stopped workers that are still finishing a command: a projector whose turn has not come is
    "waiting", and a thread that ends takes the next waiting projector over instead of a new thread being
    started. apply() matches the workers to the settings and the module switch. A stopped worker sends nothing
    more and saves nothing. The status lives in memory only. Nothing here holds a lock while it talks to a
    projector."""

    def __init__(self, api, interval=POLL_EVERY, changing=POLL_CHANGING, stagger=STAGGER, retry_for=RETRY_FOR,
                 retry_every=RETRY_EVERY, log=print):
        self.api, self.log = api, log
        self.interval, self.changing, self.stagger, self.retry_for, self.retry_every = interval, changing, stagger, retry_for, retry_every
        self.lock = threading.Lock()
        self._workers = {}       # projector id -> _Worker
        self._leaving = []       # stopped workers whose thread is still busy with them
        self._dying = []         # threads that have let go of everything and are about to end
        self._status = {}        # projector id -> the last answer
        self._notice = {}        # projector id -> {"ok", "text"}: how the last input change ended
        self._input_locks = {}   # projector id -> Lock: one input change at a time, the user's or the retry's
        self._modes = {}         # projector id -> {colour mode code: True (seen in use or taken) or False (refused)}
        self._viewed = None      # time.monotonic() when the panel last read the list (viewed())
        self._closed = False

    def _entries(self):
        if self._closed or not self.api.registry.enabled("projector"):
            return []
        return list(self.api.settings.data.get("projectors") or [])

    def _entry(self, pid):
        return next((p for p in self._entries() if p["id"] == pid), None)

    def _link(self, entry, w=None):
        link = self.api._pjlink(entry)
        if w is not None:
            link.cancel = w.stop                  # switched off or removed: not one more command
        return link

    def _esc(self, entry, w=None):
        esc = self.api._escvp(entry)
        if w is not None:
            esc.cancel = w.stop
        return esc

    def _new_worker(self, pid, index, entry):
        """With self.lock held."""
        w = self._workers[pid] = _Worker(pid)
        w.identify = not entry.get("details")
        w.due = time.monotonic() + index * self.stagger
        return w

    def _waiting(self):
        """With self.lock held: (index, entry) of projectors that have no worker and no old one still ending."""
        busy = set(self._workers) | {w.pid for w in self._leaving}
        return [(i, p) for i, p in enumerate(self._entries()) if p["id"] not in busy]

    def apply(self):
        """Match the workers to the projectors in the settings; none at all while the module is off."""
        for _ in range(2):
            with self.lock:
                want = {p["id"] for p in self._entries()}
                for pid in [k for k in self._workers if k not in want]:
                    self._retire(self._workers.pop(pid))
                for pid in [k for k in self._input_locks if k not in want]:
                    del self._input_locks[pid]
                self._dying = [t for t in self._dying if t.is_alive()]
                for i, p in self._waiting():
                    if len(self._workers) + len(self._leaving) + len(self._dying) >= MAX_PROJECTORS:
                        break                     # it waits; a thread that ends takes it over (see _after)
                    w = self._new_worker(p["id"], i, p)
                    w.thread = threading.Thread(target=self._run, args=(w,), name="projector-poll", daemon=True)
                    w.thread.start()
                dying = list(self._dying) if self._waiting() else []
            if not dying:
                return
            for t in dying:                       # they hold nothing and take no lock any more: gone in a moment
                t.join(0.5)

    def _retire(self, w):
        """With self.lock held."""
        w.stop.set()
        w.wake.set()
        self._status.pop(w.pid, None)
        self._notice.pop(w.pid, None)
        self._modes.pop(w.pid, None)
        self._leaving.append(w)

    def retire(self, pid):
        """This projector's address was changed: its worker stops now (it sends nothing more, and saves nothing),
        and its status, notice and any input change being retried are dropped. An input change that was asked for
        the old address and has not gone out yet is not sent anywhere (set_input reads the stored address again
        under the input lock), and one refused by the old address is not retried at the new one. What cannot be
        taken back is a command already on the wire to the old address when the edit is saved. apply() afterwards gives the
        projector a new worker, which reads its details anew; while the old worker's thread is still ending a
        command, the projector is "waiting", and that same thread then takes it over, so there are never two."""
        with self.lock:
            w = self._workers.pop(pid, None)
            if w is not None:
                self._retire(w)
            self._status.pop(pid, None)
            self._notice.pop(pid, None)
            self._modes.pop(pid, None)

    def stop(self, final=False):
        """Stop every worker. `final`: the panel is closing, start none again."""
        with self.lock:
            self._closed = self._closed or final
            for pid in list(self._workers):
                self._retire(self._workers.pop(pid))

    def threads(self):
        with self.lock:
            seen = [w.thread for w in list(self._workers.values()) + self._leaving] + self._dying
            return [t for i, t in enumerate(seen) if t.is_alive() and t not in seen[:i]]

    # -- what the panel reads --
    def status(self, pid):
        """The last answer, plus "pending_input", "notice" and "waiting" (no thread is free for it yet). With
        Epson control and the projector on, "picture" also holds "colour_modes": [{"code", "known"}], the modes the
        panel may offer (not those the projector refused); known is True for one it was seen using or took."""
        with self.lock:
            st = dict(self._status.get(pid) or {})
            if isinstance(st.get("picture"), dict):
                seen = self._modes.get(pid) or {}
                codes = [c for c in COLOUR_MODES if seen.get(c) is not False]
                cur = st["picture"].get("colour_mode")
                if cur and cur not in codes:
                    codes.append(cur)
                st["picture"] = dict(st["picture"], colour_modes=[{"code": c, "known": seen.get(c) is True or c == cur} for c in codes])
            w = self._workers.get(pid)
            st["pending_input"] = w.pending["input"] if w and w.pending else None
            st["notice"] = self._notice.get(pid)
            st["waiting"] = w is None and (any(x.pid == pid for x in self._leaving) or
                                           len(self._workers) + len(self._leaving) + len(self._dying) >= MAX_PROJECTORS)
        return st

    def poke(self, pid):
        """Check this projector (or "all") again now: something was just changed, or tried."""
        with self.lock:
            for w in self._workers.values():
                if pid in ("all", w.pid):
                    w.due = 0.0
                    w.wake.set()

    def viewed(self):
        """The panel is showing the projectors: check each one every `changing` seconds (10) instead of every
        `interval` (45) while it does, and for VIEW_HOLD seconds after it last asked. Asks nothing itself."""
        now = time.monotonic()
        with self.lock:
            self._viewed = now
            for w in self._workers.values():
                if w.last is not None and w.due != float("inf") and w.due > w.last + self.changing:
                    w.due = max(now, w.last + self.changing)
                    w.wake.set()

    def _watched(self):
        return self._viewed is not None and time.monotonic() - self._viewed < VIEW_HOLD

    def mode_result(self, pid, code, took):
        """A colour mode change was answered: `took` True (it is known to work) or False (refused, not offered again)."""
        with self.lock:
            if pid in self._workers:
                self._modes.setdefault(pid, {})[code] = bool(took)

    def health(self):
        """For the Health card: [{"id", "name", "state", "text"}] from the last answers; asks nothing."""
        out = []
        for p in self._entries():
            st = self.status(p["id"])
            row = {"id": p["id"], "name": p["name"], "state": "unknown"}
            out.append(row)
            if "ok" not in st:
                row["text"] = "Waiting for an earlier check to end." if st["waiting"] else "Not checked yet."
                continue
            if not st["ok"]:
                row["text"] = "No answer: %s." % st["error"]
                continue
            words = [st["power"].capitalize()]
            lamps = st.get("lamps")
            if lamps:
                words.append(("lamp %s h" if len(lamps) == 1 else "lamps %s h") % ", ".join(str(l["hours"]) for l in lamps))
            warn = st.get("warnings")
            errors = [k for k in WARNINGS if (warn or {}).get(k) == "error"]
            warns = [k for k in WARNINGS if (warn or {}).get(k) == "warning"]
            row["text"] = ", ".join(words) + "."
            if errors:
                row["text"] += " Error: %s." % ", ".join(errors)
            if warns:
                row["text"] += " Warning: %s." % ", ".join(warns)
            if warn is None:
                row["text"] += " Warnings not read."
            row["state"] = "bad" if errors else ("warn" if warns else "ok")
        return out

    # -- details --
    def identify(self, entry, w=None):
        """Ask the projector who it is and keep the answer in the settings. What it would not say this time, or
        said in a form that is not the standard's, keeps its older value (the input list too). Labels are left
        alone. Nothing is saved for a projector that was removed or whose address was changed meanwhile, or once
        the module is off. Raises
        ProjectorError if the projector cannot be reached."""
        got = self._link(entry, w).identify()             # no lock held: this is the slow part
        settings = self.api.settings
        with settings.lock:
            if (w is not None and w.stop.is_set()) or self._entry(entry["id"]) is None:
                return None
            items = list(settings.data.get("projectors") or [])
            for i, p in enumerate(items):
                if p["id"] == entry["id"]:
                    if (p["host"], p["port"]) != (entry["host"], entry["port"]):
                        return None               # edited meanwhile: these are another address's answers
                    details = dict(p.get("details") or {})
                    details.update({k: v for k, v in got.items() if v is not None or k not in details})
                    details["read"] = int(time.time())
                    items[i] = dict(p, details=details)
                    settings.data["projectors"] = items
                    settings.save()
                    return details
        return None

    # -- input, with the retry --
    def _input_lock(self, pid):
        with self.lock:
            return self._input_locks.setdefault(pid, threading.Lock())

    def set_input(self, entry, code, wanted=None, cancel=None):
        """Switch the input now. If the projector says "unavailable" (warming up, mostly), keep trying in the
        background for retry_for seconds; {"pending": True} then. Other refusals raise ProjectorError.
        One input change at a time per projector: a retry that is being sent is over before this one goes
        out, and it is not sent again afterwards, so the last choice made is the one that stands.
        `wanted` (the room's): asked once this change has the projector's input lock, just before it is sent; if
        it says no, a newer choice was made while this one waited, and nothing is sent ("stopped"). `cancel`: an
        Event that, once set, also keeps the command from being sent."""
        pid = entry["id"]
        def moved():        # edited to another address since `entry` was read: that input was chosen for the old one
            now = self._entry(pid)
            return now is None or (now["host"], now["port"]) != (entry["host"], entry["port"])
        gone = ProjectorError("the projector's address was changed; choose the input again", "stopped")
        with self._input_lock(pid):
            if wanted is not None and not wanted():
                raise ProjectorError("stopped", "stopped")
            if moved():                                # nothing goes to the old address, and nothing of this to the new one
                raise gone
            with self.lock:
                w = self._workers.get(pid)
                if w:
                    w.pending = None                   # a newer choice replaces one still being retried
                self._notice.pop(pid, None)
            try:
                link = self._link(entry)
                if cancel is not None:
                    link.cancel = cancel
                link.set_input(code)
            except ProjectorError as e:
                if e.code != "ERR3":
                    raise
                now = time.monotonic()
                with self.lock:
                    # The address first. Just after an edit the projector can be without a worker for a moment (the
                    # old one's thread is still ending a check and takes it over then): looking for the worker
                    # first told the person "unavailable" about an address they had just replaced.
                    # The entry is read once, here: a projector removed, or a module switched off, a moment ago is
                    # out of the settings before its worker is stopped, and must not be given a retry.
                    cur = self._entry(pid)
                    if cur is not None and moved():    # the edit landed while the old address was being asked: no retry at the new one
                        raise gone
                    w = self._workers.get(pid)
                    if cur is None or w is None or w.stop.is_set():    # removed, or switched off: the refusal as it came
                        raise
                    w.pending = {"input": code, "until": now + self.retry_for, "next": now + self.retry_every}
                    w.wake.set()
                return {"pending": True}
        self.poke(pid)
        return {"pending": False}

    def cancel_input(self, pid, code):
        """Stop retrying a change to input `code` (the room's scene was replaced by a newer choice). A retry that
        is being sent is over before this returns, and none follows. True if there was one to stop."""
        with self._input_lock(pid):
            with self.lock:
                w = self._workers.get(pid)
                if w is None or not w.pending or w.pending["input"] != code:
                    return False
                w.pending = None
        return True

    def _retry(self, w, entry, pending):
        label = (entry.get("labels") or {}).get(pending["input"]) or input_name(pending["input"])
        with self._input_lock(w.pid):
            with self.lock:
                if w.pending is not pending or w.stop.is_set():      # replaced by a newer choice, or switched off
                    return
            try:
                self._link(entry, w).set_input(pending["input"])
                notice = {"ok": True, "text": "Input switched to %s." % label}
            except ProjectorError as e:
                if e.code == "stopped":
                    return
                if e.code in ("ERR3", "busy", "unreachable") and time.monotonic() + self.retry_every <= pending["until"]:
                    pending["next"] = time.monotonic() + self.retry_every
                    return
                why = "it was still not ready after %d seconds (is it switched on?)" % self.retry_for if e.code == "ERR3" else str(e)
                notice = {"ok": False, "text": "Could not switch to %s: %s." % (label, why)}
            except Exception as e:
                notice = {"ok": False, "text": "Could not switch to %s: error: %s." % (label, e)}
            with self.lock:
                if w.pending is not pending or w.stop.is_set():
                    return
                w.pending = None
                w.due = 0.0
                self._notice[w.pid] = notice
        if not notice["ok"]:
            self.log("pvj-web: projector %s: %s" % (entry["name"], notice["text"]))

    # -- the status --
    def _poll(self, w, entry):
        """One status check; None if it could not be made (our own command was in the way, or we were stopped).
        Only the power state decides whether the projector "answers": lamp hours, warnings, input or mutes it
        would not give, or gave in a form that is not the standard's, are just not known."""
        link = self._link(entry, w)

        def ask(fn):
            try:
                return fn()
            except ProjectorError as e:
                if e.code in SOFT:
                    return None
                raise
        st = {"ok": True, "checked": int(time.time()), "input": None, "mute": None}
        proto = protocol_of(entry)
        try:
            if proto == "epson":                    # everything over Epson control, in one connection
                st = epson_status(self._esc(entry, w))
            else:
                st["power"] = link.state()
                st["warnings"] = ask(link.warnings)
                st["lamps"] = ask(link.lamps)
                if st["power"] == "on":                 # in standby these two are "unavailable" by the standard
                    st["input"] = ask(link.input)
                    st["mute"] = ask(link.mute_state)
                if proto == "both" and st["power"] == "on":
                    try:                            # the picture settings; PJLink alone says whether it answers
                        st["picture"] = epson_picture(self._esc(entry, w).session(PICTURE_QUERIES))
                    except ProjectorError as e:
                        if e.code == "stopped":
                            raise
                        st["picture_error"] = str(e)
        except ProjectorError as e:
            if e.code in ("busy", "stopped"):       # the last answer stands
                return None
            st = {"ok": False, "checked": int(time.time()), "error": str(e)}
        except Exception as e:
            st = {"ok": False, "checked": int(time.time()), "error": "error: %s" % e}
        with self.lock:
            if w.stop.is_set():
                return None
            self._status[w.pid] = st
            cur = (st.get("picture") or {}).get("colour_mode")
            if cur:
                self._modes.setdefault(w.pid, {})[cur] = True       # in use: the projector has it
        return st

    def _run(self, w):
        while w is not None:
            try:
                self._loop(w)
            except Exception as e:                  # never lose the thread's place in the count
                try:
                    self.log("pvj-web: projector check: %s" % e)
                except Exception:
                    pass
            w = self._after(w)

    def _after(self, w):
        """This thread is done with `w`. It takes over a waiting projector if there is one (so the number of
        threads never grows past the limit while old ones are still ending), else it ends."""
        with self.lock:
            if self._workers.get(w.pid) is w:
                del self._workers[w.pid]
                self._status.pop(w.pid, None)
                self._notice.pop(w.pid, None)
            self._leaving = [x for x in self._leaving if x is not w]
            for i, p in self._waiting():
                nxt = self._new_worker(p["id"], i, p)
                nxt.thread = threading.current_thread()
                return nxt
            self._dying.append(threading.current_thread())
        return None

    def _loop(self, w):
        while not w.stop.is_set():
            entry = self._entry(w.pid)
            if entry is None:
                break
            with self.lock:
                identify, w.identify = w.identify, False
                pending = w.pending
            if identify:
                try:
                    self.identify(entry, w)
                except Exception:               # the status check below says what is wrong
                    pass
                entry = self._entry(w.pid) or entry
            if pending and time.monotonic() >= pending["next"] and not w.stop.is_set():
                self._retry(w, entry, pending)
            if time.monotonic() >= w.due and not w.stop.is_set():
                with self.lock:
                    w.due = float("inf")
                st = self._poll(w, entry)
                with self.lock:
                    w.last = time.monotonic()
                    if w.due == float("inf"):       # else poke() asked for another check meanwhile: keep that
                        often = (st or {}).get("power") in ("warming up", "cooling down") or self._watched()
                        w.due = time.monotonic() + (self.changing if often else self.interval)
                    if st is None or not st["ok"]:
                        pass                        # nothing learnt this time: ask for the input list neither again nor anew
                    elif st["power"] != "on":
                        w.asked_inputs = False
                    elif not (entry.get("details") or {}).get("inputs") and not w.asked_inputs:
                        w.asked_inputs = w.identify = True      # it would not list its inputs in standby
            with self.lock:
                nxt = min(w.due, w.pending["next"]) if w.pending else w.due
                if w.identify:
                    nxt = 0.0
            w.wake.wait(max(0.0, nxt - time.monotonic()))
            w.wake.clear()
