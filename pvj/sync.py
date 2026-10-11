# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Multi-box sync (server and client) and the video wall.

The old PocketVJ had a "master" box broadcasting its position over UDP and "slaves" that jumped when they drifted
more than 50 ms (omxplayer-sync). Here, in the owner's words, a **server** box leads and **client** boxes follow:

* The server sends, about ten times a second, a small message to every local
  network it is on (UDP broadcast, port 5577 unless changed): the clip's file name, the position, whether it is
  paused, the speed, whether it loops, and whether the screen is blacked out.
* A client plays the clip with the same file name from its own media folder (or the top of a USB drive) and keeps
  in step: a small difference is corrected by playing up to 4 percent faster or slower for a moment (not visible,
  and the sound keeps its pitch); only a large one (over half a second: a clip change, a seek, a late start) makes it
  jump, aimed a little ahead to allow for the time a jump takes, and that allowance is learned from each jump.
* Messages are only accepted from private network addresses and with the same group name, are small and strictly
  checked, and more than 40 a second are ignored; a client follows one server at a time (the first it hears; another after 3 s of silence).
  UDP senders can be forged on the same network, so keep the show network private (as for OSC and DMX).

Video wall: any box (server or client) can show one tile of the picture: columns and rows of the wall, which tile
this screen is, and how much picture hides behind the screens' frames (bezel, as a percentage of a tile). It is
done by the player cropping the picture (mpv's video-crop), so all boxes play the same full file.
"""

import ipaddress
import json
import re
import secrets
import socket
import statistics
import threading
import time

PORT = 5577
SEND_EVERY = 0.1                 # seconds between server messages
MAX_PACKET = 1024
GROUP = re.compile(r"[A-Za-z0-9_-]{1,24}")
NAME = re.compile(r"[^/\\\x00-\x1f]{1,200}")
SEEK_AT = 0.5                    # seconds of difference that make a client jump instead of nudging the speed
TOLERANCE = 0.015                # within this, play at the server's speed
GAIN = 0.5                       # speed change per second of difference (0.04 s off: 2 percent slower or faster)
MAX_NUDGE = 0.04
MAX_PER_SECOND = 40             # a server sends ten a second; more from the network is ignored
FOLLOW_TIMEOUT = 3.0             # seconds of silence before another server may be followed
SETTLE = 1.5                     # seconds after a jump before the difference counts again
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8")]
ROLES = ("off", "server", "client")


class SyncError(ValueError):
    pass


def blank():
    return {"role": "off", "group": "main", "port": PORT,
            "wall": {"cols": 1, "rows": 1, "col": 0, "row": 0, "bezel": 0.0}}


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise SyncError("%s must be %d to %d" % (what, lo, hi))
    return v


def validate(body, current):
    """New settings from untrusted input (only the keys given change). Raises SyncError."""
    if not isinstance(body, dict):
        raise SyncError("send an object")
    cfg = json.loads(json.dumps(current))
    if "role" in body:
        if body["role"] not in ROLES:
            raise SyncError("role must be off, server or client")
        cfg["role"] = body["role"]
    if "group" in body:
        if not isinstance(body["group"], str) or not GROUP.fullmatch(body["group"]):
            raise SyncError("the group name is 1 to 24 letters, digits, - or _")
        cfg["group"] = body["group"]
    if "port" in body:
        cfg["port"] = _int(body["port"], 1024, 65535, "the port")
    if "wall" in body:
        w = body["wall"]
        if not isinstance(w, dict):
            raise SyncError("wall must be an object")
        wall = dict(cfg["wall"])
        for k, hi in (("cols", 8), ("rows", 8)):
            if k in w:
                wall[k] = _int(w[k], 1, hi, k)
        for k, lim in (("col", "cols"), ("row", "rows")):
            if k in w:
                wall[k] = _int(w[k], 0, 7, k)
        if "bezel" in w:
            b = w["bezel"]
            if isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 <= b <= 20:
                raise SyncError("the bezel is 0 to 20 percent of a tile")
            wall["bezel"] = round(float(b), 2)
        if wall["col"] >= wall["cols"] or wall["row"] >= wall["rows"]:
            raise SyncError("this screen's column and row must be inside the wall")
        cfg["wall"] = wall
    return cfg


def wall_crop(wall, width, height):
    """mpv video-crop for this screen's tile of a width x height picture ("" for the whole picture).
    The bezel hides picture between screens: of the picture's width, cols tiles and (cols - 1) bezels fit."""
    cols, rows, b = wall["cols"], wall["rows"], wall["bezel"] / 100.0
    if cols == 1 and rows == 1:
        return ""
    tw = width / (cols + (cols - 1) * b)
    th = height / (rows + (rows - 1) * b)
    x = wall["col"] * tw * (1 + b)
    y = wall["row"] * th * (1 + b)
    return "%dx%d+%d+%d" % (round(tw), round(th), round(x), round(y))


# ---- messages ---------------------------------------------------------------------------------------------------
def encode(group, seq, state):
    msg = {"v": 1, "g": group, "s": seq}
    msg.update(state)
    data = json.dumps(msg, separators=(",", ":")).encode()
    if len(data) > MAX_PACKET:
        raise SyncError("message too large")
    return data


def decode(data, group):
    """A checked server message, or None. Never raises on bad input."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_PACKET:
        return None
    try:
        m = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(m, dict) or m.get("v") != 1 or m.get("g") != group:
        return None
    seq = m.get("s")
    state = m.get("st")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 or state not in ("play", "stop"):
        return None
    run = m.get("r", "")
    if not isinstance(run, str) or not re.fullmatch(r"[0-9a-f]{0,16}", run):
        return None
    out = {"seq": seq, "run": run, "state": state, "black": m.get("bk") is True}
    if state == "play":
        f, pos, speed = m.get("f"), m.get("p"), m.get("sp", 1.0)
        if not isinstance(f, str) or not NAME.fullmatch(f) or f in (".", ".."):
            return None
        for v in (pos, speed):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
                return None
        if not 0 <= pos <= 360000 or not 0.1 <= speed <= 4:
            return None
        dur = m.get("d")
        if dur is not None and (isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 0 < dur <= 360000):
            return None
        out.update(file=f, pos=float(pos), speed=float(speed), paused=m.get("pa") is True, loop=m.get("lp") is True,
                   duration=float(dur) if dur else None)
    return out


def private(addr):
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(a in n for n in PRIVATE)


# ---- the client's control --------------------------------------------------------------------------------------
class Follower:
    """Keeps a player in step with server messages. `player` has: load(path, loop), playing() (the file name it is
    playing, or None), position(), set_speed(x), seek(seconds), pause(on), stop(), blackout(on). `find(name)` returns
    a local path or None. All time from `clock`."""

    def __init__(self, player, find, clock=time.monotonic, log=print):
        self.player, self.find, self._clock, self.log = player, find, clock, log
        self.file = None             # the server's file name we are playing
        self.local = None            # its file name here (the same name, maybe on a USB drive)
        self.lead = 0.25             # learned: how far ahead (seconds of clip at speed 1) to aim a jump
        self.errors = []
        self.settle_until = 0.0
        self.last_jump = None        # the speed at the last jump, to learn the lead after it
        self.speed = None            # the speed we last set
        self.server_speed = None     # the server's speed in the last message
        self.paused = None
        self.black = None
        self.status = {"state": "waiting for a server"}

    def _error(self, pos, srv, duration):
        err = pos - srv
        if duration and abs(err) > duration / 2:          # one side looped already
            err -= duration if err > 0 else -duration
        return err

    def _speed(self, x):
        if self.speed is None or abs(x - self.speed) > 0.0005:
            self.player.set_speed(x)
            self.speed = x

    def _jump(self, msg, now, aim_ahead=True):
        target = msg["pos"] + (self.lead * msg["speed"] if aim_ahead else 0.0)
        if msg["duration"]:
            target %= msg["duration"]
        self._speed(msg["speed"])                        # never settle at a nudged or old speed
        self.player.seek(target)
        self.last_jump = msg["speed"] if aim_ahead else None
        self.settle_until = now + SETTLE
        self.errors = []

    def apply(self, msg):
        now = self._clock()
        if msg["black"] != self.black:
            self.player.blackout(msg["black"])
            self.black = msg["black"]
        if msg["state"] == "stop":
            if self.file is not None:
                self.player.stop()
                self.file = None
            self.status = {"state": "server stopped"}
            return
        # The server's clip, and really playing here: a clip started on this box's own panel, or a player that
        # restarted, is put right by the next message.
        if msg["file"] != self.file or self.player.playing() != self.local:
            path = self.find(msg["file"])
            if path is None:
                self.status = {"state": "missing file", "file": msg["file"]}
                self.file = None
                return
            self.player.load(path, msg["loop"])
            self.file, self.local = msg["file"], path.replace("\\", "/").rsplit("/", 1)[-1]
            self.errors, self.paused, self.speed = [], None, None
            self.server_speed = msg["speed"]
            self._speed(msg["speed"])
            self.settle_until = now + SETTLE
            self.status = {"state": "starting", "file": msg["file"]}
            return
        if msg["speed"] != self.server_speed:            # the server changed speed: follow at once
            self.server_speed = msg["speed"]
            self._speed(msg["speed"])
            self.errors = []
        if msg["paused"] != self.paused:
            self.player.pause(msg["paused"])
            self.paused = msg["paused"]
            if msg["paused"]:
                self._jump(msg, now, aim_ahead=False)
        pos = self.player.position()
        if pos is None:
            return
        err = self._error(pos, msg["pos"], msg["duration"])
        if self.last_jump is not None and now >= self.settle_until:
            # learn how long a jump takes, in clip seconds at speed 1: landed behind, aim further ahead next time
            self.lead = min(2.0, max(0.0, self.lead - 0.7 * err / max(self.last_jump, 0.1)))
            self.last_jump = None
        if now < self.settle_until:
            return
        self.errors = (self.errors + [err])[-5:]
        e = statistics.median(self.errors)
        if msg["paused"]:
            if abs(e) > 0.04:                            # the server moved while paused
                self._jump(msg, now, aim_ahead=False)
            self.status = {"state": "paused with the server", "file": self.file, "error_ms": round(e * 1000)}
            return
        if abs(e) > SEEK_AT:
            self._jump(msg, now)
            self.status = {"state": "jumped to catch up", "file": self.file, "error_ms": round(e * 1000)}
            return
        want = msg["speed"]
        if abs(e) > TOLERANCE:
            want = msg["speed"] * (1 - max(-MAX_NUDGE, min(MAX_NUDGE, e * GAIN)))
        self._speed(want)
        self.status = {"state": "in step" if abs(e) <= 0.04 else "catching up", "file": self.file,
                       "error_ms": round(e * 1000), "speed": round(want, 4)}


# ---- the service -------------------------------------------------------------------------------------------------
class SyncManager:
    """Runs the server's sender, the client's receiver, or (sync off with a wall tile) a crop watcher, in a thread,
    as the settings say. Each thread has its own stop signal, so one that is slow to stop can never pick up a later
    run's settings."""

    def __init__(self, api, settings, log=print, clock=time.monotonic, targets=None):
        self.api, self.settings, self.log, self._clock = api, settings, log, clock
        self._targets = targets          # tests: send here instead of the networks' broadcast addresses
        self._thread = None
        self._stop = threading.Event()
        self.lock = threading.Lock()
        self.follower = None
        self.server = None               # (address, last heard) the client follows
        self.sent = 0
        self.dropped = 0
        self.error = None                # why the last thread stopped, for the card
        self.crop_for = None             # the file the wall crop was last set for
        self._bcast = (0.0, [])          # broadcast addresses, kept for a while (reading them runs `ip`)
        self._quiet_until = 0.0

    def config(self):
        return self.settings.data.get("sync", blank())

    def enabled(self):
        return self.api.registry.enabled("wall")

    def _note(self, text):
        """Log at most once every 10 seconds: a flood or a player that is down must not fill the journal."""
        now = self._clock()
        if now >= self._quiet_until:
            self._quiet_until = now + 10
            self.log("pvj-web: sync: " + text)

    # -- player access --
    def _get(self, prop):
        try:
            return self.api.player.ipc.request("get_property", prop)
        except Exception:
            return None

    def _set(self, prop, value):
        try:
            self.api.player.ipc.request("set_property", prop, value)
        except Exception:
            pass

    def apply_wall(self, force=False):
        """Crop the picture to this screen's tile (or show it whole). Called when a clip starts and on changes; a
        clip whose picture size is not known yet is tried again on the next call."""
        path = self._get("path")
        if not force and path == self.crop_for:
            return
        wall = self.config()["wall"]
        whole = (wall["cols"] == 1 and wall["rows"] == 1) or not self.enabled()
        if whole:
            self._set("video-crop", "")
            self.crop_for = path
            return
        params = self._get("video-params") or {}
        w, h = params.get("w"), params.get("h")
        if not (w and h):
            return                         # not decoded yet: next time
        self._set("video-crop", wall_crop(wall, w, h))
        self.crop_for = path

    # -- the server --
    def _synced_file(self, path):
        """The file name to send for `path`, or None for what clients cannot have: the test pattern, a live input,
        a stream. Only files in the media folder or at the top of a USB drive are synced."""
        if not path or "://" in path:
            return None
        import os
        name = os.path.basename(path)
        roots = [getattr(self.api, "media_dir", None), getattr(self.api, "usb_root", None)]
        real = os.path.realpath(path)
        for root in roots:
            if root:
                r = os.path.realpath(root)
                if real.startswith(r + os.sep):
                    return name
        return None

    def state(self):
        path = self._get("path")
        black = bool(getattr(self.api, "mix", {}).get("blackout"))
        name = self._synced_file(path) if path else None
        if name is None:
            return {"st": "stop", "bk": black}
        out = {"st": "play", "f": name, "p": round(float(self._get("time-pos") or 0.0), 3),
               "sp": float(self._get("speed") or 1.0), "pa": bool(self._get("pause")),
               "lp": self._get("loop-file") not in (None, False, "no"), "bk": black}
        dur = self._get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            out["d"] = round(float(dur), 3)
        return out

    def _broadcasts(self):
        if self._targets is not None:
            return list(self._targets)
        now = self._clock()
        if now - self._bcast[0] < 15 and self._bcast[1]:
            return self._bcast[1]
        out = []
        try:
            for entry in self.api._ip_json():
                if entry.get("ifname") in ("lo", "wg-pvj"):
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("broadcast") and private(a.get("local", "")):
                        out.append(a["broadcast"])
        except Exception:
            pass
        out = out or ["255.255.255.255"]
        self._bcast = (now, out)
        return out

    def _serve(self, cfg, stop):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        run = secrets.token_hex(4)           # a new run: clients start counting again
        seq = 0
        try:
            while not stop.wait(SEND_EVERY):
                try:
                    data = encode(cfg["group"], seq, dict(self.state(), r=run))
                except SyncError:
                    continue
                seq += 1
                for addr in self._broadcasts():
                    try:
                        s.sendto(data, (addr, cfg["port"]))
                        self.sent += 1
                    except OSError:
                        pass
                self.apply_wall()
        finally:
            s.close()

    def _watch_wall(self, cfg, stop):
        """Sync off but a wall tile set: keep cropping each new clip."""
        while not stop.wait(0.5):
            self.apply_wall()

    # -- the client --
    def _local_player(self):
        api = self.api
        mgr = self

        class P:
            def load(self, path, loop):
                # the panel's own way to start a clip: fades cancelled, opacity kept, the PIN screen and a live
                # input put away
                start = getattr(api, "_start_list", None)
                if start is not None:
                    start([path], "loop" if loop else "stop", False)
                else:
                    api.player.play([path], loop=loop, spawn=getattr(api, "spawn", True))
                mgr.crop_for = None

            def playing(self):
                p = mgr._get("path")
                return p.rsplit("/", 1)[-1] if isinstance(p, str) and p else None

            def position(self):
                v = mgr._get("time-pos")
                return float(v) if isinstance(v, (int, float)) else None

            def set_speed(self, x):
                mgr._set("speed", x)

            def seek(self, seconds):
                try:
                    api.player.ipc.request("seek", seconds, "absolute+exact")
                except Exception:
                    pass

            def pause(self, on):
                mgr._set("pause", bool(on))

            def stop(self):
                try:
                    stop = getattr(api, "_stop_screen", None)
                    if stop is not None:
                        stop()      # as the panel's Stop: a clip of this box's own that is on its way loads nothing
                    else:           # after it, and its dip does not leave the picture dark
                        api.player.clear()
                except Exception:
                    pass

            def blackout(self, on):
                try:
                    api.blackout({"on": bool(on)}, None, "sync")
                except Exception:
                    pass
        return P()

    def find(self, name):
        try:
            return self.api.resolve_media(name)
        except Exception:
            pass
        try:
            for drive in self.api.usb_drives():
                if any(f["name"] == name for f in drive["files"]):
                    return self.api.resolve_usb(drive["drive"] + "/" + name)
        except Exception:
            pass
        return None

    def _receive(self, cfg, stop):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", cfg["port"]))
        s.settimeout(0.5)
        self.follower = Follower(self._local_player(), self.find, clock=self._clock, log=self.log)
        last = None                          # (run, seq) of the last message used
        window = (0.0, 0)                    # rate limit: messages in the current second
        try:
            while not stop.is_set():
                try:
                    data, (addr, _) = s.recvfrom(MAX_PACKET + 1)
                except socket.timeout:
                    if self.server and self._clock() - self.server[1] > FOLLOW_TIMEOUT:
                        self.follower.status = {"state": "lost the server", "server": self.server[0]}
                    continue
                except OSError:
                    continue
                now = self._clock()
                if now - window[0] >= 1.0:
                    window = (now, 0)
                window = (window[0], window[1] + 1)
                if window[1] > MAX_PER_SECOND:     # a server sends ten a second; more is a flood
                    self.dropped += 1
                    if window[1] == MAX_PER_SECOND + 1:
                        self._note("more than %d messages a second; the extra ones are ignored" % MAX_PER_SECOND)
                    continue
                if not private(addr):
                    self.dropped += 1
                    continue
                msg = decode(data, cfg["group"])
                if msg is None:
                    self.dropped += 1
                    continue
                if self.server and self.server[0] != addr and now - self.server[1] < FOLLOW_TIMEOUT:
                    continue                     # someone else's server on the same network: follow one only
                if self.server is None or self.server[0] != addr:
                    last = None
                    self.log("pvj-web: sync: following the server at %s" % addr)
                if last is not None and msg["run"] == last[0] and msg["seq"] <= last[1]:
                    continue                     # old or repeated (a restarted server has a new run)
                last = (msg["run"], msg["seq"])
                self.server = (addr, now)
                try:
                    self.follower.apply(msg)
                except Exception as e:           # a hiccup with the player must not end following
                    self._note(str(e))
                self.apply_wall()
        finally:
            s.close()

    def _run(self, target, cfg, stop):
        try:
            target(cfg, stop)
        except Exception as e:                   # a busy port, for example: say so on the card
            self.error = str(e)
            self.log("pvj-web: sync stopped: %s" % e)

    # -- start, stop, status --
    def apply(self):
        """Start or stop the sender, receiver or crop watcher to match the settings and the module switch."""
        with self.lock:
            self._stop.set()                     # only the old thread's own signal
            old = self._thread
            if old:
                old.join(timeout=1.0)            # it has its own signal: if it is slow, it still ends on its own
            self._thread = None
            self._stop = stop = threading.Event()
            self.server, self.follower, self.error = None, None, None
            cfg = self.config()
            self.apply_wall(force=True)
            if not self.enabled():
                return
            if cfg["role"] == "server":
                target = self._serve
            elif cfg["role"] == "client":
                target = self._receive
            elif cfg["wall"]["cols"] * cfg["wall"]["rows"] > 1:
                target = self._watch_wall
            else:
                return
            self._thread = threading.Thread(target=self._run, args=(target, cfg, stop), name="sync-" + cfg["role"], daemon=True)
            self._thread.start()

    def stop(self):
        with self.lock:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1.0)
            self._thread = None

    def status(self):
        cfg = self.config()
        out = {"enabled": self.enabled(), "config": cfg, "running": bool(self._thread and self._thread.is_alive()),
               "error": self.error}
        if cfg["role"] == "server":
            out["sent"] = self.sent
        if cfg["role"] == "client":
            out["server"] = self.server[0] if self.server else None
            out["follow"] = dict(self.follower.status) if self.follower else {"state": "starting"}
            out["dropped"] = self.dropped
        return out
