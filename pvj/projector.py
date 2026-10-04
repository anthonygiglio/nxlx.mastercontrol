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
SOFT = ("ERR1", "ERR2", "ERR3", "ERR4", "odd")     # an answer, but no usable value: a detail that is simply not known (yet)


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
    return {"id": uuid.uuid4().hex[:8], "name": name.strip(), "host": host, "port": port, "password": password}


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
        self._leaving.append(w)

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
        """The last answer, plus "pending_input", "notice" and "waiting" (no thread is free for it yet)."""
        with self.lock:
            st = dict(self._status.get(pid) or {})
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
        alone. Nothing is saved for a projector that was removed, or once the module is off. Raises
        ProjectorError if the projector cannot be reached."""
        got = self._link(entry, w).identify()             # no lock held: this is the slow part
        settings = self.api.settings
        with settings.lock:
            if (w is not None and w.stop.is_set()) or self._entry(entry["id"]) is None:
                return None
            items = list(settings.data.get("projectors") or [])
            for i, p in enumerate(items):
                if p["id"] == entry["id"]:
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

    def set_input(self, entry, code):
        """Switch the input now. If the projector says "unavailable" (warming up, mostly), keep trying in the
        background for retry_for seconds; {"pending": True} then. Other refusals raise ProjectorError.
        One input change at a time per projector: a retry that is being sent is over before this one goes
        out, and it is not sent again afterwards, so the last choice made is the one that stands."""
        pid = entry["id"]
        with self._input_lock(pid):
            with self.lock:
                w = self._workers.get(pid)
                if w:
                    w.pending = None                   # a newer choice replaces one still being retried
                self._notice.pop(pid, None)
            try:
                self._link(entry).set_input(code)
            except ProjectorError as e:
                if e.code != "ERR3":
                    raise
                now = time.monotonic()
                with self.lock:
                    w = self._workers.get(pid)
                    if w is None or w.stop.is_set():
                        raise
                    w.pending = {"input": code, "until": now + self.retry_for, "next": now + self.retry_every}
                    w.wake.set()
                return {"pending": True}
        self.poke(pid)
        return {"pending": False}

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
        try:
            st["power"] = link.state()
            st["warnings"] = ask(link.warnings)
            st["lamps"] = ask(link.lamps)
            if st["power"] == "on":                 # in standby these two are "unavailable" by the standard
                st["input"] = ask(link.input)
                st["mute"] = ask(link.mute_state)
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
                    if w.due == float("inf"):       # else poke() asked for another check meanwhile: keep that
                        w.due = time.monotonic() + (self.changing if (st or {}).get("power") in ("warming up", "cooling down") else self.interval)
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
