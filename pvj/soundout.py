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

from . import clock as clock_mod
from . import oscfeedback as fb
from . import paths
from . import sound as sound_mod

# --- the setting ----------------------------------------------------------------------------------------------------------
SOURCES = ("level", "beat") + tuple("band%d" % (i + 1) for i in range(len(sound_mod.BANDS)))
REST_MIN, REST_MAX = 0.0, 1.0
DEPTH_MIN, DEPTH_MAX = -1.0, 1.0
SMOOTH_MIN, SMOOTH_MAX = 0.05, 10.0
NAME_MAX = 64
DEFAULT_MAP = {"on": False, "input": "", "source": "level", "rest": 0.0, "depth": 0.5, "smooth": 1.5}
# The sources (D97): what pvj-sound hears. The device is the one capture device; the others are fed in by the panel.
INPUTS = ("device", "box", "ndi", "osc")
INPUT_NAMES = {"device": "The capture device", "box": "The box's own sound", "ndi": "The NDI sound", "osc": "Levels sent in over OSC"}
MAX_IN_HOSTS = 8
DEFAULT = {"on": False, "device": "", "gain_db": 0.0, "osc_state": False, "osc_render": False, "map": dict(DEFAULT_MAP),
           "source": "device", "allowed": ["device"], "channel": "mix", "in_hosts": []}


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
        raise SoundError("the sound section holds on, device, gain_db, osc_state, osc_render, map, source, allowed, channel and in_hosts")
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
    allowed = out["allowed"]
    if not isinstance(allowed, list) or any(a not in INPUTS for a in allowed) or len(set(allowed)) != len(allowed):
        raise SoundError("the allowed sources are some of: %s" % ", ".join(INPUTS))
    out["allowed"] = [a for a in INPUTS if a in allowed]
    if out["source"] not in INPUTS:
        raise SoundError("the source is one of: %s" % ", ".join(INPUTS))
    if out["source"] not in out["allowed"]:
        raise SoundError("%s is not one of the sources the owner allowed" % INPUT_NAMES[out["source"]])
    ch = out["channel"]
    if ch != "mix" and (isinstance(ch, bool) or not isinstance(ch, int) or not 1 <= ch <= sound_mod.MAX_CHANNELS):
        raise SoundError("the input is the mix or a number from 1 to %d" % sound_mod.MAX_CHANNELS)
    hosts = out["in_hosts"]
    if not isinstance(hosts, list) or len(hosts) > MAX_IN_HOSTS:
        raise SoundError("at most %d addresses may send levels in" % MAX_IN_HOSTS)
    try:
        out["in_hosts"] = [str(fb.address_form(h)) for h in hosts]
    except fb.FeedbackError as e:
        raise SoundError(str(e).replace("state is only sent to", "levels are only taken from"))
    if len(set(out["in_hosts"])) != len(out["in_hosts"]):
        raise SoundError("an address is in the list twice")
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


STATES = ("idle", "capturing", "problem", "waiting")


def _helper_view(reply):
    """What the page may say of the service's answer, field by field."""
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        return {"running": False}
    want = reply.get("want") if isinstance(reply.get("want"), dict) else {}
    extra = {k: want.get(k) for k in ("source", "channel", "channels") if isinstance(want.get(k), (str, int)) and not isinstance(want.get(k), bool)}
    why = reply.get("why") if isinstance(reply.get("why"), str) else ""
    out = {"running": True, "state": reply.get("state") if reply.get("state") in STATES else "problem",
           "why": sound_mod._clean(why, 200), "gen": reply.get("gen") if isinstance(reply.get("gen"), int) else -1,
           "want": {"on": want.get("on") is True, "device": want.get("device") if isinstance(want.get("device"), str) else "",
                    "gain_db": want.get("gain_db") if isinstance(want.get("gain_db"), (int, float)) and not isinstance(want.get("gain_db"), bool) else None}}
    out["want"].update(extra)
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


CLOCK_BACK = 4              # the clock's count may step back this far (a phase nudge) without the next beat counting twice

# What the levels are while the box clock stands in for sound that is not there (no device, or none heard).
QUIET = {"seq": 0, "flags": 0, "time": 0.0, "blocks": 0, "rate": 0, "rms_db": sound_mod.FLOOR_DB, "level": 0.0, "envelope": 0.0,
         "onset": 0.0, "pulse": 0.0, "bpm": 0.0, "confidence": 0.0, "beats": 0, "bands": [], "peak_db": sound_mod.FLOOR_DB,
         "slow_bands": [0.0] * len(sound_mod.BANDS), "capturing": False, "beat": False, "onset_flag": False, "clipped": False,
         "idle": True, "fresh": True, "age": 0.0}

# --- the fed sources (D97) ----------------------------------------------------------------------------------------------------
METER_LABEL = "pvjsound"
# A filter that passes the sound through unchanged and leaves its RMS and peak level per audio frame as metadata.
METER_FILTER = "@%s:lavfi=[astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level+Peak_level]" % METER_LABEL
METER_EVERY = 0.04              # the player's meter is read 25 times a second
METER_CHECK = 2.0               # and whether the filter is still there (a player that restarted lost it) every 2 s
IN_PREFIX = "/nxlx/sound/in/"
IN_EVERY = 0.02                 # what came in over OSC is fed at most 50 times a second
IN_RATE, IN_BURST = 80.0, 20.0              # messages from one sender: at most 100 in any second
IN_ALL_RATE, IN_ALL_BURST = 160.0, 40.0     # from all senders: at most 200


# --- the shader input -------------------------------------------------------------------------------------------------------
MAP_EVERY = 0.5                 # a new value at most twice a second (the classic renderer compiles each one)
MAP_EVERY_NATIVE = 0.05         # on the New renderer nothing is compiled (D106): at most twenty a second, so a beat shows
MAP_STEP = 0.02                 # and only when it moved by this share of the input's range


class Sound:
    """The panel's side. `api` gives the settings, the OSC manager, the render bridge and the shader engine."""

    def __init__(self, api, socket_path=None, levels_path=None, proc=sound_mod.PROC, clock=time.monotonic, log=print, udp=_udp):
        self.api = api
        self.client = Client(socket_path or paths.sound_socket())
        self.levels_path = levels_path or paths.sound_levels()
        self.proc = proc
        self._clock, self.log, self._udp = clock, log, udp
        self.clock = clock_mod.Clock(now=clock)      # the box clock (D116): tap, OSC and the sound's tempo, kept by itself
        self.lock = threading.Lock()        # not in the order of pvj/locks.py: never held over a call out
        self.gen = 1
        self.helper = {"running": False}
        self.helper_at = None
        self._thread = None
        self._map_thread = None             # the shader input's own thread, started and stopped with _thread
        self._stop = threading.Event()
        self._caps = {}                     # (address, port) -> _Caps
        self._all = None
        self.stats = {"sent": 0, "held": 0, "failed": 0, "beats": 0}
        self.osc_why = {}                   # (kind, name) -> why nothing goes there now
        self.map_state = {"what": "", "value": None, "at": None}
        self._smooth = None                 # (time, value) of the slow source
        self._map_sent = None               # (shader id, epoch, input, value)
        self._map_at = None
        self._map_moving = False            # the last look reached a shader: only then is the New renderer's gap used
        self._last_beats = None
        self._last_src = False
        self._feeder, self._feed_stop = None, threading.Event()
        self.feed_why, self.meter_why = "", None
        self._in_latest, self._in_at, self._in_hosts = None, 0.0, None
        self._in_buckets = {}
        self.in_stats = {"taken": 0, "held": 0, "ignored": 0}
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
        self.ensure_feeder()

    # -- the service --
    def push(self):
        """Tell the service what the Owner chose. Returns its view."""
        cfg = self.config()
        with self.lock:
            gen = self.gen
        reply = self.client.request(dict({"cmd": "configure", "on": cfg["on"], "device": cfg["device"], "gain_db": cfg["gain_db"], "gen": gen},
                                         **self.wanted(cfg)))
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
            w = self.wanted(cfg)
            if (view.get("gen") != gen or want.get("on") != cfg["on"] or want.get("device") != cfg["device"] or want.get("gain_db") != cfg["gain_db"]
                    or any(want.get(k) != w[k] for k in w)):
                self.push()
        self.ensure()
        self.ensure_feeder()
        try:
            self.clock.tick(self._clock())      # the box clock hands over from a source gone quiet (D116)
            self.levels()                       # and hears pvj-sound even while nobody looks at the meter
        except Exception as e:
            self.log("pvj-web: box clock tick: %s" % e)

    def levels(self):
        """The last record of the levels file, with "fresh" (younger than STALE_SECONDS), or None."""
        v = sound_mod.read_levels(self.levels_path)
        if v is not None:
            v["age"] = max(0.0, time.monotonic() - v["time"])
            v["fresh"] = v["age"] <= sound_mod.STALE_SECONDS
        live = v is not None and v["fresh"] and v["capturing"]
        if live:
            self.clock.sound(v["bpm"], v["confidence"], v["beats"], at=v["time"])      # the clock's own rules decide how far it is believed
        return self.follow_clock(v, live)

    @staticmethod
    def live(v):
        """Whether the sending thread has something to send: sound that is being heard now, or the box clock's beat."""
        return v is not None and v["fresh"] and (v["capturing"] or bool(v.get("clock")))

    def follow_clock(self, v, live):
        """The beat pulse, the tempo and the beat count of a record are the box clock's while a tap or OSC steers it, so
        what follows the beat follows the clock, with or without a sound device (D116). Otherwise the record is returned
        as it is, untouched. Without live sound the record is a quiet one: the levels are zero and nothing is heard."""
        got = self.clock.reading(self._clock())
        if got is None or got["source"] not in clock_mod.FOLLOWED:
            return v
        out = dict(v) if live else dict(QUIET, time=self._clock())
        out.update(pulse=got["pulse"], bpm=got["bpm"], confidence=1.0, beats=got["beat_count"], clock=True)
        return out

    def devices(self):
        return sound_mod.capture_devices(self.proc)

    # -- the sending thread --
    def needed(self, cfg=None):
        cfg = cfg or self.config()
        return bool(cfg["on"] and (cfg["osc_state"] or cfg["osc_render"] or cfg["map"]["on"]))

    def ensure(self):
        want = self.needed()
        with self.lock:
            alive = [t for t in (self._thread, self._map_thread) if t is not None and t.is_alive()]
            if want and len(alive) < 2:
                self._stop.set()                # what is left of an earlier pair ends with it
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._loop, args=(self._stop,), name="sound-out", daemon=True)
                self._map_thread = threading.Thread(target=self._map_loop, args=(self._stop,), name="sound-map", daemon=True)
                self._thread.start()
                self._map_thread.start()
            elif not want and alive:
                self._stop.set()
                self._thread = self._map_thread = None

    def stop(self):
        with self.lock:
            self._stop.set()
            self._thread = self._map_thread = None
        self.stop_feeder()

    def plan(self, v, now):
        """The datagrams of this look at the file: a beat at once, the values every SEND_EVERY, the slow bands every
        BANDS_EVERY."""
        msgs = []
        src, n = bool(v.get("clock")), v["beats"]
        if self._last_beats is None or src != self._last_src:
            self._last_beats = n                    # who supplies the count changed: a silent resync, no message
        elif n > self._last_beats:
            msgs.append(beat_message(n))
            self.stats["beats"] += 1
            self._last_beats = n
        elif n < self._last_beats and not (src and self._last_beats - n <= CLOCK_BACK):
            self._last_beats = n                    # a count that went down (the service started again): silent resync
        # (a small step back of the clock's own count, a nudge, keeps the highest sent: it must not beat twice)
        self._last_src = src
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
                live = self.live(v)
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
                stop.wait(LOOK_EVERY if live and osc else IDLE_LOOK)
        except Exception as e:                   # the thread must not take the panel down; it is started again by tick
            self.log("pvj-web: sound out stopped: %s" % e)
        finally:
            if sock is not None:
                sock.close()

    def _map_loop(self, stop):
        """The shader input has a thread of its own: a value can wait on the player, and a beat must not wait for it."""
        cfg_at = None
        try:
            while not stop.is_set():
                now = self._clock()
                with self.lock:
                    gen = self.gen
                if cfg_at is None or cfg_at[1] != gen or now - cfg_at[0] >= TARGETS_EVERY:
                    cfg_at = (now, gen, self.config())
                m = cfg_at[2]["map"]
                if m["on"]:
                    v = self.levels()
                    self.map_step(m, v if self.live(v) else None, now)
                stop.wait(0.05 if m["on"] else IDLE_LOOK)
        except Exception as e:                   # as _loop: tick starts it again
            self.log("pvj-web: sound map stopped: %s" % e)

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
        """Smooth the source, and at most every map_gap() send the input a new value if it moved enough."""
        raw = self.source_value(m, v)
        if self._smooth is None:
            self._smooth = (now, 0.0)          # from where it rests: never a jump when it is switched on
        t0, y = self._smooth
        dt = max(0.0, now - t0)
        y += (1.0 - math.exp(-dt / m["smooth"])) * (raw - y)
        self._smooth = (now, y)
        if self._map_at is not None and now - self._map_at < (self.map_gap() if self._map_moving else MAP_EVERY):
            return
        self._map_at = now
        what, value = self._apply(m, y)
        self._map_moving = value is not None
        with self.lock:
            self.map_state = {"what": what, "value": value, "at": time.time()}

    def map_gap(self):
        """Seconds between two values: the slow classic gap unless the shader on the screen is the New renderer's own."""
        playing = getattr(getattr(self.api, "shaders", None), "playing", None)
        return MAP_EVERY_NATIVE if playing and playing.get("native") else MAP_EVERY

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

    # -- the fed sources (D97): the box's own sound and the NDI sound measured in the player; levels sent in over OSC --
    def device_channels(self, cfg=None):
        cfg = cfg or self.config()
        for d in self.devices():
            if d["id"] == cfg["device"]:
                return d.get("channels", sound_mod.DEFAULT_CHANNELS)
        return sound_mod.DEFAULT_CHANNELS

    def wanted(self, cfg=None):
        """What the service is told: the device and its channel, or a feed."""
        cfg = cfg or self.config()
        if cfg["source"] == "device":
            channels = self.device_channels(cfg)
            channel = cfg["channel"] if cfg["channel"] == "mix" or cfg["channel"] <= channels else "mix"
            return {"source": "device", "channel": channel, "channels": channels}
        return {"source": "feed", "channel": "mix", "channels": 1}

    def feeding(self, cfg=None):
        cfg = cfg or self.config()
        return bool(cfg["on"] and cfg["source"] in ("box", "ndi", "osc"))

    def ensure_feeder(self):
        want = self.feeding()
        with self.lock:
            running = self._feeder is not None and self._feeder.is_alive()
            if want and not running:
                self._feed_stop = threading.Event()
                self._feeder = threading.Thread(target=self._feed_loop, args=(self._feed_stop,), name="sound-feed", daemon=True)
                self._feeder.start()
            elif not want and running:
                self._feed_stop.set()
                self._feeder = None

    def stop_feeder(self):
        with self.lock:
            if self._feeder is not None:
                self._feed_stop.set()
                self._feeder = None

    def _player(self):
        player = getattr(self.api, "player", None)
        return getattr(player, "ipc", None)

    def meter(self, on):
        """Put the meter into the player's audio filters (or take it out). Returns None, or why it could not."""
        ipc = self._player()
        if ipc is None:
            return "the player is not there"
        try:
            listed = ipc.request("get_property", "af")         # Ipc.request: the answer's data, or PlayerError
            there = isinstance(listed, list) and any(isinstance(f, dict) and f.get("label") == METER_LABEL for f in listed)
            if on and not there:
                ipc.request("af", "add", METER_FILTER)
            elif not on and there:
                ipc.request("af", "remove", "@" + METER_LABEL)
        except Exception as e:
            return "the player did not take the meter (%s); the new renderer does not yet, classic mpv does" % sound_mod._clean(str(e), 80)
        return None

    def read_meter(self):
        """(rms dB, peak dB) of the player's last audio frame, or None. Only these two numbers ever leave the player."""
        ipc = self._player()
        try:
            data = ipc.request("get_property", "af-metadata/" + METER_LABEL) if ipc is not None else None
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        out = []
        for key in ("lavfi.astats.Overall.RMS_level", "lavfi.astats.Overall.Peak_level"):
            v = data.get(key)
            try:
                v = float(v) if isinstance(v, str) and len(v) < 32 else None
            except ValueError:
                v = None
            if v is None:
                return None
            out.append(sound_mod.FLOOR_DB if v != v or v < sound_mod.FLOOR_DB else min(sound_mod.GAIN_MAX, v))    # "-inf" is silence
        return tuple(out)

    def ndi_playing(self):
        ndi = getattr(self.api, "ndi", None)
        cur = getattr(ndi, "current", None) if ndi is not None else None
        return bool(isinstance(cur, dict) and cur.get("id"))

    def feed_once(self, cfg):
        """One look for the fed source; returns the feed sent, or None."""
        src = cfg["source"]
        if src in ("box", "ndi"):
            if src == "ndi" and not self.ndi_playing():
                self.feed_why = "No NDI picture is playing, so there is no NDI sound to measure"
                return None
            got = self.read_meter()
            if got is None:
                self.feed_why = "The player gives no sound level now (nothing plays, or it plays no sound)"
                return None
            f = {"rms_db": got[0], "peak_db": got[1]}
        else:
            with self.lock:
                held, at = self._in_latest, self._in_at
            if held is None or self._clock() - at > sound_mod.STALE_SECONDS:
                self.feed_why = "Nothing has been sent in over OSC in the last half second"
                return None
            with self.lock:
                f = dict(self._in_latest)
                self._in_latest.pop("beat", None)        # a beat is sent once
        self.feed_why = ""
        self.client.request(dict(f, cmd="feed"))
        return f

    def _feed_loop(self, stop):
        metered = None
        try:
            while not stop.is_set():
                cfg = self.config()
                if cfg["source"] in ("box", "ndi"):
                    if metered is None or self._clock() - metered >= METER_CHECK:
                        self.meter_why = self.meter(True)
                        metered = self._clock()
                elif metered is not None:
                    self.meter(False)
                    metered = None
                if not self.meter_why or cfg["source"] == "osc":
                    self.feed_once(cfg)
                stop.wait(METER_EVERY if cfg["source"] != "osc" else IN_EVERY)
        except Exception as e:
            self.log("pvj-web: sound feed stopped: %s" % e)
        finally:
            if metered is not None:
                self.meter(False)

    # OSC in: Max/RNBO or another program sends levels to the box (D97)
    def in_hosts_now(self):
        """The addresses levels may come in from now: the render nodes' IPv4 hosts and the Owner's list, each another
        device on one of the box's own networks at this moment and never on the support tunnel's."""
        cfg = self.config()
        if not (cfg["on"] and cfg["source"] == "osc"):
            return set()
        from . import render as render_mod
        hosts = set(cfg["in_hosts"])
        hosts |= {n["host"] for n in render_mod.stored(self.api.settings.data.get("render")) if render_mod.is_ipv4_text(n["host"])}
        bridge = getattr(self.api, "render", None)
        if bridge is None:
            return set()
        return {h for h in hosts if bridge.why_not(h) is None}

    def osc_in_host(self, address):
        now = self._clock()
        with self.lock:
            held = self._in_hosts
        if held is None or now - held[0] >= TARGETS_EVERY:
            held = (now, self.in_hosts_now())
            with self.lock:
                self._in_hosts = held
        return address in held[1]

    def osc_in(self, messages, address):
        """Levels sent in. Returns how many messages were taken. Nothing is ever sent in answer."""
        now = self._clock()
        taken = 0
        with self.lock:
            for key, rate, burst in ((address, IN_RATE, IN_BURST), ("*", IN_ALL_RATE, IN_ALL_BURST)):
                b = self._in_buckets.get(key)
                if b is None:
                    if len(self._in_buckets) > 64:
                        self._in_buckets.clear()
                    b = self._in_buckets[key] = fb.Bucket(rate, burst, now)
                b.fill(now)
            mine, every = self._in_buckets[address], self._in_buckets["*"]
            latest = dict(self._in_latest or {"rms_db": sound_mod.FLOOR_DB})
            for addr, args in messages:
                if mine.have < 1 or every.have < 1:
                    self.in_stats["held"] += 1
                    continue
                mine.have -= 1
                every.have -= 1
                name = addr[len(IN_PREFIX):]
                nums = [a for a in args if isinstance(a, (int, float)) and not isinstance(a, bool)]
                if name == "level" and nums:
                    latest["rms_db"] = sound_mod.LOW_DB * (1.0 - min(1.0, max(0.0, float(nums[0]))))
                elif name == "db" and nums:
                    latest["rms_db"] = min(sound_mod.GAIN_MAX, max(sound_mod.FLOOR_DB, float(nums[0])))
                elif name == "envelope" and nums:
                    latest["envelope"] = min(1.0, max(0.0, float(nums[0])))
                elif name == "beat":
                    latest["beat"] = True
                    self.clock.osc_beat(now)
                elif name == "bpm" and nums:
                    latest["bpm"] = min(400.0, max(0.0, float(nums[0])))
                    self.clock.osc_bpm(nums[0], now)
                elif name == "bands" and nums:
                    latest["bands"] = [min(1.0, max(0.0, float(x))) for x in nums[:len(sound_mod.BANDS)]]
                else:
                    self.in_stats["ignored"] += 1
                    continue
                taken += 1
            if taken:
                for k in ("rms_db",):
                    latest[k] = latest.get(k, sound_mod.FLOOR_DB)
                latest["peak_db"] = latest["rms_db"]
                self._in_latest, self._in_at = latest, now
                self.in_stats["taken"] += taken
        return taken
