# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Render nodes (D94): the box cues a render node (a laptop or a GPU box running VDMX, Resolume, MadMapper,
Max/Jitter, TouchDesigner ...) over OSC and shows the node's picture back over NDI.

Only what every render tool shares is built in: a cue is an address and arguments that a person types. A tool's
profile (its address templates) has a place, PROFILES below. The one profile there is, "max", is this project's own
scheme under /nxlx/ with a companion Max patch (tools/max/): no other tool's OSC addresses are written anywhere in the
box. pvj/RENDERNODES.md lists what a profile for another tool needs.

Where the box may send is the rule of an OSC state target (D82, pvj/oscfeedback.py), checked again at every send:

* a plain IPv4 address in a private or link-local range, on a subnet of one of the box's own real ports NOW, never
  that subnet's network or broadcast address, never the box itself, loopback or multicast, and never the support
  tunnel's network (left out by interface name in oscfeedback.local_nets, and here also by the session's own
  network, D88);
* or a `.local` name, whose form is checked when it is saved and which is looked up when a cue is sent (with a
  deadline, one lookup at a time per node); the address it gives is held to the rule above;
* a port from 1024 up; the socket's time to live is 1, so a packet cannot cross a router.

No packet from anywhere decides where the box sends: the list is the Owner's. The socket is opened for one datagram,
never read and closed again: the box listens on no new port. A cue is one datagram of at most 1200 bytes, under a
cap per node and for all nodes together, and one that does not fit is refused in words, never queued.

OSC over UDP has no reply. A cue that left the box is "sent", never "received": the node may be off, deaf or another
program. A tool's profile may later add a reply; none does now.
"""

import ipaddress
import re
import socket
import struct
import threading
import time
import uuid

from . import oscfeedback as fb

MAX_NODES = 8
MAX_ARGS = 8                    # typed arguments of one cue
MAX_STRING = 64                 # characters of a string argument
MAX_ADDRESS = 128               # characters of an OSC address
MIN_PORT = fb.MIN_PORT
MAX_DATAGRAM = fb.MAX_DATAGRAM
TYPES = ("int", "float", "string", "bool")
INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1
FLOAT_MOST = fb.FLOAT_MOST

# The caps, as D82's buckets: a bucket holds `burst` and fills at `rate` a second, so never more than rate + burst
# in any second. A cue is one datagram.
NODE_RATE, NODE_BURST = 15.0, 5.0               # one node: at most 20 cues in any second
ALL_RATE, ALL_BURST = 45.0, 15.0                # all nodes together: at most 60

RESOLVE_SECONDS = 1.0           # a `.local` name is waited for this long when a cue is sent from the panel
RESOLVE_QUICK = 0.2             # and this long from a controller, whose reading thread must not wait
RESOLVED_FOR = 30.0             # an address a name gave is used this long before it is looked up again

# The fallback (Api.render_tick): the node's picture missing this long means it is gone, and the box goes back to
# what played before the cue. A tick is once a second, so the box acts within GONE_SECONDS + 1 of seeing it gone.
GONE_SECONDS = 3.0
SHOW_SECONDS = 20.0             # a show that has not reached the screen in this long is given up (the press failed)
STILL_DEFAULT = 10              # seconds a node's picture may stand still before it counts as gone (0: never)
STILL_MIN, STILL_MAX = 5, 120

GENERIC = "generic"             # the one profile there is: no templates, the person types the address

# A tool's profile, for later (D94). Nothing is filled in: the owner names the first tool, and its facts come from
# him or from the tool's own documentation (pvj/RENDERNODES.md says which). The shape, so that the first one has a
# place to go and the editor a structure to read:
#
#   PROFILES = {
#       "<tool id>": {                          # lower case, e.g. the tool's name
#           "name": "<the tool's name as people say it>",
#           "port": <the tool's usual OSC input port, or None when it has none>,
#           "notes": "<where the OSC input is switched on in the tool, in plain words>",
#           "reply": False,                     # True only if the tool answers, and then how is written beside it
#           "cues": {
#               "<cue id>": {
#                   "label": "<what the person reads, e.g. Go to scene>",
#                   "address": "<an address template; {name} for each part the person fills in>",
#                   "args": [{"name": "<a part>", "type": "int" | "float" | "string" | "bool",
#                             "min": <lowest>, "max": <highest>}],
#               },
#           },
#       },
#   }
#
# A filled template becomes an ordinary cue (address and typed arguments) and goes through cue_form and the same
# sender, so a profile can never send what a typed cue could not.
#
# The first profile, the owner's choice of 2026-10-16 ("Try to start with Max and Jitter as the first render node"):
# Max has no OSC address space of its own (a patch receives whatever it routes), so the scheme is this project's,
# under /nxlx/, short and meant to stay. The companion patch tools/max/nxlx-render-node.maxpat receives exactly it.
# Changing an address here breaks every patch made from it: add, never rename.
MAX_PORT = 7400
PROFILES = {
    "max": {
        "name": "Max/Jitter (the nxlx patch)",
        "port": MAX_PORT,
        "notes": "Open tools/max/nxlx-render-node.maxpat in Max 9; it listens with [udpreceive %d] and sends its picture as NDI with jit.ndi." % MAX_PORT,
        "reply": False,     # /nxlx/hello is defined for a patch that answers; this box does not read an answer yet
        "cues": {
            "scene": {"label": "Go to scene", "address": "/nxlx/scene",
                      "args": [{"name": "scene", "type": "int", "min": 0, "max": 999}]},
            "param": {"label": "Set a parameter", "address": "/nxlx/param",
                      "args": [{"name": "name", "type": "string"}, {"name": "value", "type": "float", "min": 0.0, "max": 1.0}]},
            "color": {"label": "Set a colour", "address": "/nxlx/color",
                      "args": [{"name": "name", "type": "string"}, {"name": "r", "type": "float", "min": 0.0, "max": 1.0},
                               {"name": "g", "type": "float", "min": 0.0, "max": 1.0}, {"name": "b", "type": "float", "min": 0.0, "max": 1.0}]},
            "go": {"label": "Go (the next look)", "address": "/nxlx/go", "args": []},
            "hello": {"label": "Hello (is the patch there?)", "address": "/nxlx/hello", "args": []},
        },
    },
}


def profile_cue(profile, cue_id, values, node):
    """A profile's template filled in: an ordinary cue for `node` ({"node", "address", "args", "show": False}).
    `values` are the parts in the template's order. Raises RenderError for a profile, a cue or a value that is not
    the template's."""
    p = PROFILES.get(profile)
    if p is None or cue_id not in p["cues"]:
        raise RenderError("no such cue in that profile")
    t = p["cues"][cue_id]
    if not isinstance(values, list) or len(values) != len(t["args"]):
        raise RenderError("%s takes %d values" % (t["label"], len(t["args"])))
    args = []
    for spec, v in zip(t["args"], values):
        a = arg_form({"type": spec["type"], "value": v})
        if "min" in spec and not spec["min"] <= a["value"] <= spec["max"]:
            raise RenderError("%s is %s to %s" % (spec["name"], spec["min"], spec["max"]))
        args.append(a)
    return cue_form({"node": node, "address": t["address"], "args": args})

NAME_FORM = fb.NAME_FORM
_ID = re.compile(r"[0-9a-f]{8}")
_NDI_ID = re.compile(r"[0-9a-f]{12}")
# An OSC address the box sends: parts of letters, digits and _ . - only, so no OSC pattern characters (* ? [ ] { } ,
# and #) that would make the receiving tool match many methods at once, no spaces, and no empty part.
_ADDRESS = re.compile(r"(/[A-Za-z0-9_.\-]+)+")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

WHY_TUNNEL = "on the support connection's network"
SENT_WORDS = "OSC over UDP has no reply: sent means the cue left the box, not that the node took it"


class RenderError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --- the form of what is stored ------------------------------------------------------------------------------------
def is_ipv4_text(text):
    return isinstance(text, str) and bool(re.fullmatch(r"[0-9.]{1,15}", text))


def host_form(text):
    """The host of a node as it is kept: a private or link-local IPv4 address written the plain way, or a `.local`
    name in lower case. Raises RenderError for everything else (a public address, an IPv6 address, a name on the
    internet, a name with a port or a path)."""
    if not isinstance(text, str) or not text or len(text) > 253:
        raise RenderError("the host is an IPv4 address like 192.168.1.50 or a name like render-mac.local")
    if is_ipv4_text(text):
        try:
            return str(fb.address_form(text))
        except fb.FeedbackError as e:
            raise RenderError(str(e).replace("state is only sent", "cues are only sent"))
    name = text.lower()
    labels = name.split(".")
    if len(labels) < 2 or labels[-1] != "local" or not all(_LABEL.fullmatch(p) for p in labels):
        raise RenderError("a name must be the machine's own .local name, like render-mac.local (or give its IPv4 address)")
    return name


def node_form(item, new_id=False):
    """One node, checked for its form. {"id", "name", "host", "port", "ndi", "profile", "still"}. With `new_id` an id is
    made when the item has none."""
    if not isinstance(item, dict) or set(item) - {"id", "name", "host", "port", "ndi", "profile", "still"}:
        raise RenderError("a render node is a name, a host, a port, an NDI source, a profile and a still time")
    nid = item.get("id")
    if nid in (None, "") and new_id:
        nid = uuid.uuid4().hex[:8]
    if not isinstance(nid, str) or not _ID.fullmatch(nid):
        raise RenderError("a render node's id is 8 hex digits")
    name, port = item.get("name"), item.get("port")
    if not isinstance(name, str) or not NAME_FORM.fullmatch(name.strip()):
        raise RenderError("a render node needs a name of 1 to 32 characters")
    if isinstance(port, bool) or not isinstance(port, int) or not MIN_PORT <= port <= 65535:
        raise RenderError("the OSC port must be %d to 65535" % MIN_PORT)
    sid = item.get("ndi", "")
    if sid not in ("", None) and not (isinstance(sid, str) and _NDI_ID.fullmatch(sid)):
        raise RenderError("a render node's NDI source is chosen from the list, by its id")
    profile = item.get("profile", GENERIC)
    if profile != GENERIC and profile not in PROFILES:
        raise RenderError("no such profile: only %s is built so far" % GENERIC)
    still = item.get("still", STILL_DEFAULT)
    if isinstance(still, bool) or not isinstance(still, int) or not (still == 0 or STILL_MIN <= still <= STILL_MAX):
        raise RenderError("a still picture counts as gone after %d to %d seconds, or 0 for never" % (STILL_MIN, STILL_MAX))
    return {"id": nid, "name": name.strip(), "host": host_form(item.get("host")), "port": port, "ndi": sid or "",
            "profile": profile, "still": still}


def validate_nodes(items):
    if not isinstance(items, list) or len(items) > MAX_NODES:
        raise RenderError("at most %d render nodes" % MAX_NODES)
    out = []
    for item in items:
        n = node_form(item)
        if any(o["id"] == n["id"] for o in out):
            raise RenderError("two render nodes have the id %s" % n["id"])
        if any((o["host"], o["port"]) == (n["host"], n["port"]) for o in out):
            raise RenderError("%s port %d is in the list already" % (n["host"], n["port"]))
        out.append(n)
    return out


def validate(section):
    """The `render` section of an imported file, form only: where it may really send is decided at every send."""
    if not isinstance(section, dict) or set(section) - {"nodes"}:
        raise RenderError("the render section holds nodes and nothing else")
    return {"nodes": validate_nodes(section.get("nodes", []))}


def stored(section):
    """The nodes as the box reads settings["render"]. Fails closed: a section that is damaged in any way is no list
    (and an older release, which does not know the key, keeps it as it is and sends nothing)."""
    try:
        return validate(section)["nodes"] if section is not None else []
    except (RenderError, AttributeError, TypeError):
        return []


def arg_form(a):
    """One typed argument: {"type": "int" | "float" | "string" | "bool", "value": ...}, checked against the type."""
    if not isinstance(a, dict) or set(a) != {"type", "value"} or a["type"] not in TYPES:
        raise RenderError("an argument is a type (int, float, string or bool) and a value")
    t, v = a["type"], a["value"]
    if t == "int":
        if isinstance(v, bool) or not isinstance(v, int) or not INT_MIN <= v <= INT_MAX:
            raise RenderError("an int argument is a whole number from %d to %d" % (INT_MIN, INT_MAX))
    elif t == "float":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > FLOAT_MOST:
            raise RenderError("a float argument is a number")
        v = float(v)
    elif t == "string":
        if not isinstance(v, str) or len(v) > MAX_STRING or _CONTROL.search(v):
            raise RenderError("a string argument is up to %d plain characters" % MAX_STRING)
    elif not isinstance(v, bool):
        raise RenderError("a bool argument is true or false")
    return {"type": t, "value": v}


def address_form(text):
    if not isinstance(text, str) or len(text) > MAX_ADDRESS or not _ADDRESS.fullmatch(text):
        raise RenderError("an OSC address starts with / and has parts of letters, digits, _ . and - (at most %d characters)" % MAX_ADDRESS)
    return text


def cue_form(c):
    """A cue as it is kept on a pad and sent: {"node", "address", "args", "show"}. The address may be empty only when
    the cue just shows the node's picture."""
    if not isinstance(c, dict) or set(c) - {"node", "address", "args", "show"}:
        raise RenderError("a render cue is a node, an OSC address, its arguments and whether to show the node's picture")
    node = c.get("node")
    if not isinstance(node, str) or not _ID.fullmatch(node):
        raise RenderError("a render cue names a node by its id")
    address = c.get("address", "")
    show = c.get("show", False)
    if not isinstance(show, bool):
        raise RenderError("show must be true or false")
    if address != "":
        address_form(address)
    elif not show:
        raise RenderError("a render cue sends an OSC address, shows the node's picture, or both")
    args = c.get("args", [])
    if not isinstance(args, list) or len(args) > MAX_ARGS:
        raise RenderError("at most %d arguments" % MAX_ARGS)
    if args and address == "":
        raise RenderError("arguments go with an OSC address")
    return {"node": node, "address": address, "args": [arg_form(a) for a in args], "show": show}


# --- the wire -------------------------------------------------------------------------------------------------------
def encode(address, args):
    """One OSC 1.0 message: the address, the type tags, then each argument big-endian (int32 'i', float32 'f', a
    string 's' padded to four bytes, and a bool as the tag 'T' or 'F' with no data)."""
    tags, data = ",", b""
    for a in args:
        t, v = a["type"], a["value"]
        if t == "int":
            tags, data = tags + "i", data + struct.pack(">i", v)
        elif t == "float":
            tags, data = tags + "f", data + struct.pack(">f", v)
        elif t == "string":
            tags, data = tags + "s", data + fb._string(v)
        else:
            tags += "T" if v else "F"
    return fb._string(address) + fb._string(tags) + data


def _udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)                                     # a send never waits
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)        # and never crosses a router
    return sock


# --- the sender -----------------------------------------------------------------------------------------------------
class Bridge:
    """Sends cues to the nodes and keeps, per node, when a cue last left and why one did not. Also holds the record
    of a node's picture shown by a cue (`showing`) and the last time the box went back from one (`back`), which
    Api.render_tick reads and writes.

    `nets()` gives the box's own networks (oscfeedback.local_nets), `tunnel()` the support session's network or None.
    The lock guards the fields below and is never held over a lookup or a send."""

    def __init__(self, nets=None, tunnel=None, resolve=None, clock=time.monotonic, wall=time.time, log=print, udp=_udp):
        self._nets_fn = nets or (lambda: [])
        self._tunnel_fn = tunnel or (lambda: None)
        self._resolve_fn = resolve or (lambda name: socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0])
        self._clock, self._wall, self.log, self._udp = clock, wall, log, udp
        self.lock = threading.Lock()        # not in the order of pvj/locks.py: never held over a call
        self._per = {}                      # node id -> its counters, its bucket, its looked-up address
        self._all = None
        self.showing = None                 # {"node", "name", "sid", "before", "at", "confirmed", "gen", "gone_since", "still_since"}
        self.back = None                    # {"at" (wall clock), "node", "why", "to"}: the last time the box went back
        self.stats = {"sent": 0, "failed": 0, "held": 0}

    def _slot(self, nid, now):
        st = self._per.get(nid)
        if st is None:
            st = self._per[nid] = {"last": None, "address": "", "error": "", "error_at": None, "bucket": fb.Bucket(NODE_RATE, NODE_BURST, now),
                                   "resolved": None, "resolving": False}
        return st

    def forget(self, keep_ids):
        with self.lock:
            self._per = {k: v for k, v in self._per.items() if k in keep_ids}
            if self.showing is not None and self.showing["node"] not in keep_ids:
                self.showing = None          # a node that was removed takes no fallback with it

    def why_not(self, addr, nets=None):
        """None when the box may send to this IPv4 address now, else why not, as part of a sentence."""
        nets = self._nets_fn() if nets is None else nets
        why = fb.not_local(addr, nets)
        if why:
            return why
        try:
            tunnel = self._tunnel_fn()
            if tunnel is not None and ipaddress.IPv4Address(str(addr)) in tunnel:
                return WHY_TUNNEL
        except (ValueError, TypeError):
            return "not an address on a private network"
        return None

    def _look_up(self, node, st, wait):
        """The address a `.local` name gives now (from memory for RESOLVED_FOR), waiting at most `wait` seconds. At most
        one lookup per node is in flight: a lookup that outlives its wait ends on its own thread and is forgotten."""
        now = self._clock()
        with self.lock:
            got = st["resolved"]
            if got and got[0] == node["host"] and now - got[2] < RESOLVED_FOR:
                return got[1]
            if st["resolving"]:
                raise RenderError("%s is still being looked up on the network; try again in a moment" % node["host"], 409)
            st["resolving"] = True
        done, out = threading.Event(), [None]

        def run():
            try:
                out[0] = str(self._resolve_fn(node["host"]))
            except Exception:
                out[0] = None
            finally:
                with self.lock:
                    st["resolving"] = False
                    if out[0]:
                        st["resolved"] = (node["host"], out[0], self._clock())
                done.set()
        threading.Thread(target=run, name="render-lookup", daemon=True).start()
        if not done.wait(wait) or not out[0]:
            raise RenderError("%s was not found on the network now (is the machine on, on the same network?)" % node["host"], 409)
        return out[0]

    def send(self, node, address, args, wait=RESOLVE_SECONDS):
        """Send one cue to `node`. Returns {"sent": True, "to": address}. Raises RenderError with a status: 400 for a
        cue too large, 409 when the node is not where the box may send now, 429 over a cap, 502 when the socket
        refused it. Every outcome is noted for the node's health."""
        data = encode(address_form(address), [arg_form(a) for a in args])
        if len(data) > MAX_DATAGRAM:
            raise RenderError("the cue is %d bytes; at most %d go in one datagram" % (len(data), MAX_DATAGRAM))
        now = self._clock()
        with self.lock:
            st = self._slot(node["id"], now)
            if self._all is None:
                self._all = fb.Bucket(ALL_RATE, ALL_BURST, now)
        try:
            ip = node["host"] if is_ipv4_text(node["host"]) else self._look_up(node, st, wait)
            why = self.why_not(ip)
            if why:
                raise RenderError("%s is %s: nothing was sent" % (node["name"], why if why != fb.WHY_NETWORK else "not on this network now"), 409)
            with self.lock:
                for b in (st["bucket"], self._all):
                    b.fill(now)
                if st["bucket"].have < 1 or self._all.have < 1:
                    self.stats["held"] += 1
                    raise RenderError("too many cues at once: at most %d a second to one node; nothing was sent" % int(NODE_RATE + NODE_BURST), 429)
                st["bucket"].have -= 1
                self._all.have -= 1
            sock = self._udp()
            try:
                sock.sendto(data, (ip, node["port"]))
            except (OSError, ValueError) as e:
                raise RenderError("the box could not send to %s (%s)" % (node["name"], getattr(e, "strerror", None) or type(e).__name__), 502)
            finally:
                sock.close()
        except RenderError as e:
            with self.lock:
                st["error"], st["error_at"] = str(e), self._wall()
                if e.status != 429:
                    self.stats["failed"] += 1
            raise
        with self.lock:
            st["last"], st["address"], st["error"], st["error_at"] = self._wall(), address, "", None
            self.stats["sent"] += 1
        return {"sent": True, "to": address}

    def health(self, nid):
        """{"last_sent": wall-clock time or None, "last_address", "error", "error_at"} for the page."""
        with self.lock:
            st = self._per.get(nid) or {}
            return {"last_sent": st.get("last"), "last_address": st.get("address", ""), "error": st.get("error", ""),
                    "error_at": st.get("error_at")}

    # -- the picture a cue showed, for the fallback (Api.render_tick) --
    def begin_show(self, node, sid, before):
        """A cue is about to show `node`'s picture. `before` is what played (Api._render_before). A cue of the same
        node while its picture is already up keeps the first `before`: the box never goes back to the node itself."""
        now = self._clock()
        with self.lock:
            had = self.showing
            if had is not None and had["sid"] == sid and had["confirmed"]:
                before = had["before"]
            self.showing = {"node": node["id"], "name": node["name"], "sid": sid, "still": node.get("still", STILL_DEFAULT),
                            "before": before, "at": now, "confirmed": False, "gen": None, "gone_since": None}

    def end_show(self, why=None, back=None):
        with self.lock:
            had, self.showing = self.showing, None
            if had is not None and why is not None:
                self.back = {"at": self._wall(), "node": had["name"], "why": why, "to": back or ""}
        return had
