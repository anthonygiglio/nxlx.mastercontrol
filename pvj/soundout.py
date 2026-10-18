# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound reactivity, the panel's side (D96, pvj/SOUND.md).

* The setting `sound` (the box's own: never exported or imported) and its form.
* Telling the service `pvj-sound` what the Owner chose, over its socket, again after a restart of either.
* Reading the levels file for the page's meter.
* OSC out under /nxlx/sound/..., to the D82 state targets and the D94 render nodes, each held at every send to the
  rule of an OSC state target (and a state target to the address locks too), under caps. The service itself has no
  network; the values leave the box only from here, and only while the Owner's switch for it is on.
* A slow envelope onto one number input of the shader on the screen, for the classic renderer (a value change there
  is a new shader text: at most twice a second here, and only for a change that shows).

No sound passes through here: only the numbers of the levels file.
"""

import math
import socket
import struct
import threading
import time

from . import oscfeedback as fb
from . import paths
from . import sound as sound_mod

# --- the setting ----------------------------------------------------------------------------------------------------------
SOURCES = ("level", "beat") + tuple("band%d" % (i + 1) for i in range(len(sound_mod.BANDS)))
REST_MIN, REST_MAX = 0.0, 1.0
DEPTH_MIN, DEPTH_MAX = -1.0, 1.0
SMOOTH_MIN, SMOOTH_MAX = 0.5, 10.0
NAME_MAX = 64
DEFAULT_MAP = {"on": False, "input": "", "source": "level", "rest": 0.0, "depth": 0.5, "smooth": 1.5}
DEFAULT = {"on": False, "device": "", "gain_db": 0.0, "osc_state": False, "osc_render": False, "map": dict(DEFAULT_MAP)}


class SoundError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _number(v, low, high, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not low <= v <= high:
        raise SoundError("%s is %s to %s" % (what, _plain(low), _plain(high)))
    return float(v)


def _plain(x):
    return ("%d" % x) if float(x).is_integer() else ("%g" % x)


def map_form(m):
    if not isinstance(m, dict) or set(m) - set(DEFAULT_MAP):
        raise SoundError("the shader mapping is on, an input, a source, where it rests, a depth and a smoothing")
    out = dict(DEFAULT_MAP)
    out.update(m)
    if not isinstance(out["on"], bool):
        raise SoundError("on must be true or false")
    name = out["input"]
    if not isinstance(name, str) or len(name) > NAME_MAX or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SoundError("the input is named as the shader names it")
    if out["source"] not in SOURCES:
        raise SoundError("the source is the level, the beat or a band (band1 to band%d)" % len(sound_mod.BANDS))
    out["rest"] = _number(out["rest"], REST_MIN, REST_MAX, "where it rests")
    out["depth"] = _number(out["depth"], DEPTH_MIN, DEPTH_MAX, "the depth")
    out["smooth"] = _number(out["smooth"], SMOOTH_MIN, SMOOTH_MAX, "the smoothing (seconds)")
    return out


def form(section):
    """The `sound` section, checked. Raises SoundError."""
    if not isinstance(section, dict) or set(section) - set(DEFAULT):
        raise SoundError("the sound section holds on, device, gain_db, osc_state, osc_render and map")
    out = dict(DEFAULT)
    out.update(section)
    for key in ("on", "osc_state", "osc_render"):
        if not isinstance(out[key], bool):
            raise SoundError("%s must be true or false" % key)
    if out["device"] != "":
        try:
            out["device"] = sound_mod.device_form(out["device"])
        except ValueError as e:
            raise SoundError(str(e))
    out["gain_db"] = _number(out["gain_db"], sound_mod.GAIN_MIN, sound_mod.GAIN_MAX, "the gain trim (dB)")
    out["map"] = map_form(out["map"])
    return out


def stored(section):
    """The setting as the box reads it. Fails closed: anything damaged is the default, which is off."""
    try:
        return form(section) if section is not None else form({})
    except (SoundError, TypeError, AttributeError):
        return form({})


# --- the service's socket ------------------------------------------------------------------------------------------------
class Client:
    """One request, one reply, with a deadline; the reply re-checked (the service is the part that reads a device)."""

    def __init__(self, path, timeout=1.0):
        self.path, self.timeout = path, timeout

    def request(self, message):
        import json
        from .netd import exchange
        from .ndi import json_depth
        try:
            raw = exchange(self.path, message, self.timeout)
        except (OSError, ValueError):
            return None
        try:
            if len(raw) > 65536 or json_depth(raw) > 8:
                return None
            reply = json.loads(raw)
        except (ValueError, RecursionError, MemoryError):
            return None
        return reply if isinstance(reply, dict) else None


STATES = ("idle", "capturing", "problem")


def _helper_view(reply):
    """What the page may say of the service's answer, field by field."""
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        return {"running": False}
    want = reply.get("want") if isinstance(reply.get("want"), dict) else {}
    why = reply.get("why") if isinstance(reply.get("why"), str) else ""
    out = {"running": True, "state": reply.get("state") if reply.get("state") in STATES else "problem",
           "why": sound_mod._clean(why, 200), "gen": reply.get("gen") if isinstance(reply.get("gen"), int) else -1,
           "want": {"on": want.get("on") is True, "device": want.get("device") if isinstance(want.get("device"), str) else "",
                    "gain_db": want.get("gain_db") if isinstance(want.get("gain_db"), (int, float)) and not isinstance(want.get("gain_db"), bool) else None}}
    cpu = reply.get("cpu_seconds")
    up = reply.get("uptime")
    if isinstance(cpu, (int, float)) and isinstance(up, (int, float)) and not isinstance(cpu, bool) and up > 0:
        out["cpu_percent"] = round(100.0 * cpu / up, 1)
    return out


# --- OSC out ---------------------------------------------------------------------------------------------------------------
PREFIX = "/nxlx/sound/"
SEND_EVERY = 0.05               # the level, the envelope, the pulse and the tempo: 20 a second
BANDS_EVERY = sound_mod.SLOW_SECONDS    # the bands: the slow ones only, 5 a second at most (review M1 of #145)
LOOK_EVERY = 0.01               # the file is looked at this often while sound goes out (for the beats)
IDLE_LOOK = 1.0                 # and this often while nothing goes out
TARGETS_EVERY = 1.0             # where it may go is worked out again this often, or at once after a change (review L4)
# The caps (review L3 of #145): what one target is sent is 20 values bundles, 5 bands bundles and at most 1 / BEAT_GAP
# (5.6) beats a second, about 31 datagrams and 6,000 bytes; the rate holds that with room, so nothing drops in normal
# work, and the burst bounds any second at 38 datagrams and 12,000 bytes. All targets (4 state targets and 8 render
# nodes at most) together: 456 datagrams and 86,400 bytes in any second.
TARGET_RATE, TARGET_BURST = 32.0, 6.0           # datagrams to one target: at most 38 in any second
TARGET_BYTES, TARGET_BYTES_BURST = 9600.0, 2400.0   # bytes to one target: at most 12,000 in any second
ALL_RATE, ALL_BURST = 384.0, 72.0               # all targets together: at most 456 datagrams ...
ALL_BYTES, ALL_BYTES_BURST = 72000.0, 14400.0   # ... and 86,400 bytes in any second


def _msg(address, *values):
    """One OSC message: float32 values, or one int32 given as ("i", n)."""
    tags, data = ",", b""
    for v in values:
        if isinstance(v, tuple):
            tags, data = tags + "i", data + struct.pack(">i", int(v[1]) & 0x7FFFFFFF)
        else:
            v = float(v)
            v = 0.0 if v != v else max(-fb.FLOAT_MOST, min(fb.FLOAT_MOST, v))
            tags, data = tags + "f", data + struct.pack(">f", v)
    return fb._string(address) + fb._string(tags) + data


def bundle(messages):
    return fb._BUNDLE + b"".join(struct.pack(">i", len(m)) + m for m in messages)


def values_bundle(v):
    """The bundle sent 20 times a second (pvj/SOUND.md's table): the level, dB, the envelope, the pulse, the tempo.
    No band: those leave only in bands_bundle."""
    return bundle([_msg(PREFIX + "level", v["level"]), _msg(PREFIX + "db", v["rms_db"]), _msg(PREFIX + "envelope", v["envelope"]),
                   _msg(PREFIX + "pulse", v["pulse"]), _msg(PREFIX + "bpm", v["bpm"], v["confidence"])])


def bands_bundle(v):
    """The bands as they may leave the box: the slow ones (the mean of 200 ms, changed at most 5 times a second), never
    the 50 a second of the file."""
    bands = list(v.get("slow_bands", []))[:len(sound_mod.BANDS)]
    bands += [0.0] * (len(sound_mod.BANDS) - len(bands))
    msgs = [_msg(PREFIX + "band/%d" % (i + 1), b) for i, b in enumerate(bands)]
    msgs.append(_msg(PREFIX + "bands", *bands))
    return bundle(msgs)


def public(v, tunnel=False):
    """What any reader off the box is given of one record (the page's meter, review M1 of #145): the level, the
    envelope, the beat and the tempo as they are, the bands only as the slow ones, and through the support tunnel no
    band at all."""
    if v is None:
        return None
    out = {"fresh": v["fresh"], "capturing": v["capturing"] and v["fresh"], "idle": v["idle"], "age": round(v["age"], 2),
           "db": round(v["rms_db"], 1), "level": round(v["level"], 3), "envelope": round(v["envelope"], 3),
           "pulse": round(v["pulse"], 3), "bpm": round(v["bpm"], 1), "confidence": round(v["confidence"], 2),
           "beats": v["beats"], "peak_db": round(v["peak_db"], 1), "clipped": v["clipped"]}
    out["bands"] = [] if tunnel else [round(b, 2) for b in v.get("slow_bands", [])]
    out["bands_hidden"] = bool(tunnel)
    return out


def beat_message(count):
    return _msg(PREFIX + "beat", ("i", count))


class _Caps:
    def __init__(self, now):
        self.count = fb.Bucket(TARGET_RATE, TARGET_BURST, now)
        self.bytes = fb.Bucket(TARGET_BYTES, TARGET_BYTES_BURST, now)


def _udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
    return sock


# --- the shader input -------------------------------------------------------------------------------------------------------
MAP_EVERY = 0.5                 # a new value at most twice a second (the classic renderer compiles each one)
MAP_STEP = 0.02                 # and only when it moved by this share of the input's range


class Sound:
    """The panel's side. `api` gives the settings, the OSC manager, the render bridge and the shader engine."""

    def __init__(self, api, socket_path=None, levels_path=None, proc=sound_mod.PROC, clock=time.monotonic, log=print, udp=_udp):
        self.api = api
        self.client = Client(socket_path or paths.sound_socket())
        self.levels_path = levels_path or paths.sound_levels()
        self.proc = proc
        self._clock, self.log, self._udp = clock, log, udp
        self.lock = threading.Lock()        # not in the order of pvj/locks.py: never held over a call out
        self.gen = 1
        self.helper = {"running": False}
        self.helper_at = None
        self._thread = None
        self._stop = threading.Event()
        self._caps = {}                     # (address, port) -> _Caps
        self._all = None
        self.stats = {"sent": 0, "held": 0, "failed": 0, "beats": 0}
        self.osc_why = {}                   # (kind, name) -> why nothing goes there now
        self.map_state = {"what": "", "value": None, "at": None}
        self._smooth = None                 # (time, value) of the slow source
        self._map_sent = None               # (shader id, epoch, input, value)
        self._map_at = None
        self._last_beats = None
        self._plan = {"next_send": 0.0, "next_bands": 0.0}
        self._targets = None                # (when, gen, the list): where it may go, for TARGETS_EVERY
        self.targets_asked = 0

    # -- the setting --
    def config(self):
        return stored(self.api.settings.data.get("sound"))

    def save(self, cfg):
        settings = self.api.settings
        with settings.lock:
            old = settings.data.get("sound")
            settings.data["sound"] = cfg
            try:
                settings.save()
            except OSError:
                if old is None:
                    settings.data.pop("sound", None)
                else:
                    settings.data["sound"] = old
                raise
        with self.lock:
            self.gen += 1
        self.push()
        self.ensure()

    # -- the service --
    def push(self):
        """Tell the service what the Owner chose. Returns its view."""
        cfg = self.config()
        with self.lock:
            gen = self.gen
        reply = self.client.request({"cmd": "configure", "on": cfg["on"], "device": cfg["device"], "gain_db": cfg["gain_db"], "gen": gen})
        view = _helper_view(reply)
        with self.lock:
            self.helper, self.helper_at = view, self._clock()
        return view

    def status(self):
        view = _helper_view(self.client.request({"cmd": "status"}))
        with self.lock:
            self.helper, self.helper_at = view, self._clock()
        return view

    def tick(self):
        """About once a second (server.py, beside ndi_tick): the service is told again after a restart of either, and
        the sending thread runs exactly while something needs it."""
        view = self.status()
        cfg = self.config()
        if view.get("running"):
            with self.lock:
                gen = self.gen
            want = view.get("want") or {}
            if view.get("gen") != gen or want.get("on") != cfg["on"] or want.get("device") != cfg["device"] or want.get("gain_db") != cfg["gain_db"]:
                self.push()
        self.ensure()

    def levels(self):
        """The last record of the levels file, with "fresh" (younger than STALE_SECONDS), or None."""
        v = sound_mod.read_levels(self.levels_path)
        if v is None:
            return None
        v["age"] = max(0.0, time.monotonic() - v["time"])
        v["fresh"] = v["age"] <= sound_mod.STALE_SECONDS
        return v

    def devices(self):
        return sound_mod.capture_devices(self.proc)

    # -- the sending thread --
    def needed(self, cfg=None):
        cfg = cfg or self.config()
        return bool(cfg["on"] and (cfg["osc_state"] or cfg["osc_render"] or cfg["map"]["on"]))

    def ensure(self):
        want = self.needed()
        with self.lock:
            running = self._thread is not None and self._thread.is_alive()
            if want and not running:
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._loop, args=(self._stop,), name="sound-out", daemon=True)
                self._thread.start()
            elif not want and running:
                self._stop.set()
                self._thread = None

    def stop(self):
        with self.lock:
            self._stop.set()
            self._thread = None

    def plan(self, v, now):
        """The datagrams of this look at the file: a beat at once, the values every SEND_EVERY, the slow bands every
        BANDS_EVERY."""
        msgs = []
        if self._last_beats is not None and v["beats"] != self._last_beats:
            msgs.append(beat_message(v["beats"]))
            self.stats["beats"] += 1
        self._last_beats = v["beats"]
        if now >= self._plan["next_send"]:
            msgs.append(values_bundle(v))
            self._plan["next_send"] = now + SEND_EVERY
        if now >= self._plan["next_bands"]:
            msgs.append(bands_bundle(v))
            self._plan["next_bands"] = now + BANDS_EVERY
        return msgs

    def current_targets(self, cfg, now):
        """targets(), worked out again every TARGETS_EVERY or at once after a change of the setting."""
        with self.lock:
            gen = self.gen
        held = self._targets
        if held is None or held[1] != gen or now - held[0] >= TARGETS_EVERY or now < held[0]:
            self.targets_asked += 1
            held = self._targets = (now, gen, self.targets(cfg))
        return held[2]

    def _loop(self, stop):
        sock = None
        cfg_at = None
        try:
            while not stop.is_set():
                now = self._clock()
                with self.lock:
                    gen = self.gen
                if cfg_at is None or cfg_at[1] != gen or now - cfg_at[0] >= TARGETS_EVERY:
                    cfg_at = (now, gen, self.config())
                cfg = cfg_at[2]
                v = self.levels()
                live = v is not None and v["fresh"] and v["capturing"]
                osc = cfg["osc_state"] or cfg["osc_render"]
                if live and osc:
                    if sock is None:
                        sock = self._udp()
                    targets = self.current_targets(cfg, now)
                    for data in self.plan(v, now):
                        self.send(sock, data, targets, now)
                elif sock is not None:
                    sock.close()
                    sock = None
                if cfg["map"]["on"]:
                    self.map_step(cfg["map"], v if live else None, now)
                stop.wait(LOOK_EVERY if live and osc else (0.05 if cfg["map"]["on"] else IDLE_LOOK))
        except Exception as e:                   # the thread must not take the panel down; it is started again by tick
            self.log("pvj-web: sound out stopped: %s" % e)
        finally:
            if sock is not None:
                sock.close()

    def targets(self, cfg):
        """[(kind, name, address, port)] where sound may go now; the reason for each that is left out is noted in
        osc_why. The rule of a state target (D82) is asked at this moment for every one, and the render bridge's
        (which also leaves out the support tunnel's network, D88)."""
        out, why = [], {}
        bridge = getattr(self.api, "render", None)

        def allowed(addr):
            if bridge is None:
                return "not on this network now"
            return bridge.why_not(addr)
        if cfg["osc_state"]:
            osc = getattr(self.api, "osc", None)
            on, listed = fb.stored(self.api.settings.data.get("osc") or {})
            sending = osc is not None and getattr(osc, "feedback", None) is not None
            for t in listed:
                w = None
                if not sending:
                    w = "OSC or Send state back is off"
                else:
                    w = allowed(t["address"])
                    if w is None and not osc._may_be_sent(t["address"]):
                        w = fb.WHY_LOCKS
                if w:
                    why[("state", t["name"])] = w
                else:
                    out.append(("state", t["name"], t["address"], t["port"]))
        if cfg["osc_render"] and bridge is not None:
            from . import render as render_mod
            for n in render_mod.stored(self.api.settings.data.get("render")):
                addr = self._node_address(bridge, n)
                w = "its name has not been found on the network yet" if addr is None else allowed(addr)
                if w:
                    why[("render", n["name"])] = w
                else:
                    out.append(("render", n["name"], addr, n["port"]))
        with self.lock:
            self.osc_why = why
        return out

    @staticmethod
    def _node_address(bridge, node):
        from . import render as render_mod
        if render_mod.is_ipv4_text(node["host"]):
            return node["host"]
        try:
            with bridge.lock:
                st = bridge._slot(node["id"], time.monotonic())
            return bridge._look_up(node, st, 0.0)       # from the bridge's memory, or a lookup started for next time
        except render_mod.RenderError:
            return None

    def send(self, sock, data, targets, now):
        if len(data) > fb.MAX_DATAGRAM:
            return
        with self.lock:
            if self._all is None:
                self._all = _Caps(now)
                self._all.count = fb.Bucket(ALL_RATE, ALL_BURST, now)
                self._all.bytes = fb.Bucket(ALL_BYTES, ALL_BYTES_BURST, now)
            keys = {(a, p) for _k, _n, a, p in targets}
            self._caps = {k: c for k, c in self._caps.items() if k in keys}
        for _kind, _name, addr, port in targets:
            with self.lock:
                caps = self._caps.setdefault((addr, port), _Caps(now))
                buckets = (caps.count, caps.bytes, self._all.count, self._all.bytes)
                for b in buckets:
                    b.fill(now)
                if caps.count.have < 1 or self._all.count.have < 1 or caps.bytes.have < len(data) or self._all.bytes.have < len(data):
                    self.stats["held"] += 1
                    continue
                caps.count.have -= 1
                self._all.count.have -= 1
                caps.bytes.have -= len(data)
                self._all.bytes.have -= len(data)
            try:
                sock.sendto(data, (addr, port))
                self.stats["sent"] += 1
            except OSError:
                self.stats["failed"] += 1

    # -- the shader input --
    def source_value(self, m, v):
        if v is None:
            return 0.0
        if m["source"] == "level":
            return v["envelope"]
        if m["source"] == "beat":
            return v["pulse"]
        n = int(m["source"][4:]) - 1
        bands = v.get("bands") or []
        return bands[n] if 0 <= n < len(bands) else 0.0

    def map_step(self, m, v, now):
        """Smooth the source, and at most every MAP_EVERY send the input a new value if it moved enough."""
        raw = self.source_value(m, v)
        if self._smooth is None:
            self._smooth = (now, 0.0)          # from where it rests: never a jump when it is switched on
        t0, y = self._smooth
        dt = max(0.0, now - t0)
        y += (1.0 - math.exp(-dt / m["smooth"])) * (raw - y)
        self._smooth = (now, y)
        if self._map_at is not None and now - self._map_at < MAP_EVERY:
            return
        self._map_at = now
        what, value = self._apply(m, y)
        with self.lock:
            self.map_state = {"what": what, "value": value, "at": time.time()}

    def _input_of(self, m):
        """(the shader on the screen, its input of that name) or (on, None), or (None, None)."""
        eng = getattr(self.api, "shaders", None)
        if eng is None:
            return None, None
        try:
            on = eng.on_screen() if eng.enabled() else None
        except Exception:
            return None, None
        if not on:
            return None, None
        try:
            parsed = eng._parsed(eng._path(on["id"])[0])[0]
        except Exception:
            return on, None
        for i in parsed.get("inputs", []):
            if i.get("name") == m["input"] and i.get("type") == "float" and i.get("max", 0) > i.get("min", 0):
                return on, i
        return on, None

    def _apply(self, m, y):
        if not m["input"]:
            return "No shader input is chosen", None
        on, i = self._input_of(m)
        if on is None:
            return "No shader is on the screen", None
        if i is None:
            return "%s has no number input called %s" % (on["id"], m["input"]), None
        lo, hi = float(i["min"]), float(i["max"])
        value = lo + (hi - lo) * min(1.0, max(0.0, m["rest"] + m["depth"] * y))
        key = (on["id"], on.get("epoch"), i["name"])
        sent = self._map_sent
        if sent is not None and sent[:3] == key and abs(sent[3] - value) < MAP_STEP * (hi - lo):
            return "Moving %s of %s" % (i["name"], on["id"]), sent[3]
        try:
            self.api.shaders.change({"id": on["id"], "values": {i["name"]: value}})
        except Exception as e:
            return "The shader did not take the value: %s" % (getattr(e, "message", None) or e), None
        self._map_sent = key + (value,)
        return "Moving %s of %s" % (i["name"], on["id"]), value

    def shader_inputs(self):
        """The number inputs of the shader on the screen, for the page: {"id", "inputs": [{"name", "label", "min", "max"}]}."""
        eng = getattr(self.api, "shaders", None)
        try:
            on = eng.on_screen() if eng is not None and eng.enabled() else None
            if not on:
                return None
            parsed = eng._parsed(eng._path(on["id"])[0])[0]
        except Exception:
            return None
        rows = [{"name": i["name"], "label": sound_mod._clean(str(i.get("label") or i["name"]), 60), "min": i["min"], "max": i["max"]}
                for i in parsed.get("inputs", []) if i.get("type") == "float" and i.get("max", 0) > i.get("min", 0)]
        return {"id": on["id"], "inputs": rows[:32]}

    def osc_status(self):
        with self.lock:
            why = dict(self.osc_why)
            stats = dict(self.stats)
        return {"left_out": [{"kind": k, "name": n, "why": w} for (k, n), w in sorted(why.items())], "stats": stats}
