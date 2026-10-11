# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A stand-in for mpv on the player's control socket, for the tests. Tests only.

Why: the tests of playing, stopping and the picture's level ran against fakes that rewrote pvj.player.Player's own
methods, so nothing of player.py was under test there: not its lock, not what it remembers of what it loaded
(`pipe_playing`), not the order of its questions to mpv. This is the other side of the socket instead. The REAL
Player talks to it; it answers as mpv does for the commands the player uses, keeps what a real mpv would keep (what
is loaded, the playlist, the brightness, the overlays, the pause, its process number), and writes down in `log`, in
the order they arrived, the commands that change what plays. It answers one request at a time, as mpv's own loop
does, and can answer late: `lag(kind)` gives the seconds a command of that kind takes ("opacity" for a level,
"play" for a load, "still" for a screenshot, "path" for how long the old path is still read after a load).

It knows nothing of video. A screenshot is a small PNG of one colour, of the size it says its screen has.
"""
import json
import os
import socket
import struct
import threading
import time
import zlib


def png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 0))
            + chunk(b"IEND", b""))


class Unavailable(Exception):
    pass


class FakeMpv:
    def __init__(self, socket_path, size=(8, 4), lag=None):
        self.socket_path, self.size, self.lag = socket_path, size, lag
        self.pid = 5000
        self.log = []               # ("load", kind, name) for every command that changes what plays, in order
        self.levels = []            # every brightness that was set
        self.commands = []          # the name of every command, in order
        self.fail = set()           # names of commands that are answered with an error
        self.mute = set()           # names of commands that are not answered at all (the connection is closed)
        self.dies = False           # True: `quit` ends the process without an answer, as a player that goes at once
        self._reset()
        self._stop = threading.Event()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(socket_path)
        self._server.listen(64)
        self._server.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, name="fake-mpv", daemon=True)
        self._thread.start()

    def _reset(self):
        """A new process: nothing loaded, nothing drawn over it, the picture at its own brightness."""
        self.props = {"pause": False, "brightness": 0, "glsl-shaders": [], "loop-file": "no", "loop-playlist": "no", "current-vo": "null",
                      "speed": 1.0, "volume": 100.0, "mute": False, "frame-drop-count": 0, "decoder-frame-drop-count": 0}
        self.playlist, self.pos = [], -1
        self.path, self._path_before, self._path_at = None, None, 0.0
        self.overlays = {}          # id -> (x, y, offset, width, height, the alpha of its first pixel)

    def stop(self):
        self._stop.set()
        self._thread.join(2)
        self._server.close()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    # -- the socket --
    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.settimeout(2)
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                if b"\n" in buf:
                    msg = json.loads(buf.split(b"\n", 1)[0])
                    reply = self._answer(msg.get("command") or [])
                    if reply is not None:
                        reply["request_id"] = msg.get("request_id")
                        conn.sendall(json.dumps(reply).encode() + b"\n")
            except (OSError, ValueError):
                pass
            finally:
                conn.close()

    def _wait(self, kind):
        if self.lag:
            time.sleep(self.lag(kind))

    def _answer(self, command):
        name = command[0] if command else ""
        self.commands.append(name if name not in ("get_property", "set_property") else "%s %s" % (name, command[1]))
        if name in self.mute or (len(command) > 1 and command[1] in self.mute):
            return None
        if name == "quit" and self.dies:
            self._do(name, command[1:])
            return None
        if name in self.fail:
            return {"error": "error running command"}
        try:
            return {"error": "success", "data": self._do(name, command[1:])}
        except Unavailable:
            return {"error": "property unavailable"}
        except (IndexError, KeyError, TypeError, ValueError):
            return {"error": "invalid parameter"}

    def _now_plays(self, path):
        self._path_before, self._path_at = self.path, time.monotonic() + (self.lag("path") if self.lag else 0.0)
        self.path = path

    @staticmethod
    def kind(path):
        if not path.startswith("av://") and "://" in path:
            return "stream"
        if path.startswith("av://lavfi:smptehdbars"):
            return "pattern"
        if path.startswith("av://lavfi:aevalsrc"):
            return "tone"
        if path.startswith("av://"):
            return "carrier"
        if path.endswith(".fifo"):
            return "pipe"
        return "file"

    def _do(self, name, args):
        if name == "get_property":
            return self._get(args[0])
        if name == "set_property":
            if args[0] == "brightness":
                self._wait("opacity")
                self.levels.append(args[1])
            self.props[args[0]] = args[1]
            return None
        if name == "loadfile":
            self._wait("play")
            if len(args) > 1 and args[1] == "append":
                self.playlist.append(args[0])
                return None
            self.playlist, self.pos = [args[0]], 0
            self._now_plays(args[0])
            self.log.append(("load", self.kind(args[0]), os.path.basename(args[0])))
            return None
        if name == "stop":
            self.playlist, self.pos = [], -1
            self._now_plays(None)
            self.log.append(("load", "clear", ""))
            return None
        if name in ("playlist-next", "playlist-prev"):
            step = 1 if name == "playlist-next" else -1
            self.pos = (self.pos + step) % len(self.playlist)
            self._now_plays(self.playlist[self.pos])
            self.log.append(("load", "step", os.path.basename(self.path)))
            return None
        if name == "screenshot-to-file":
            self._wait("still")
            if self.path is None:
                raise ValueError("nothing to take a screenshot of")
            with open(args[0], "wb") as f:
                f.write(png(self.size[0], self.size[1], (200, 100, 50)))
            return None
        if name == "overlay-add":
            oid, x, y, path, offset, _fmt, w, h, stride = args[:9]
            with open(path, "rb") as f:
                data = f.read()
            if len(data) < offset + (h - 1) * stride + w * 4:
                raise ValueError("the overlay's file is too short")
            self.overlays[oid] = (x, y, offset, w, h, data[offset + 3])
            return None
        if name == "overlay-remove":
            self.overlays.pop(args[0], None)
            return None
        if name == "quit":
            self.pid += 1                       # the unit starts a new one at once
            self._reset()
            self.log.append(("load", "quit", ""))
            return None
        return None                             # seek, vf, playlist-shuffle and the rest: taken, nothing kept

    def _get(self, prop):
        if prop == "pid":
            return self.pid
        if prop == "path":
            path = self._path_before if time.monotonic() < self._path_at else self.path
            if path is None:
                raise Unavailable()
            return path
        if prop == "idle-active":
            return self.path is None
        if prop in ("time-pos", "duration", "playback-time"):
            if self.path is None:
                raise Unavailable()
            return 0.5
        if prop == "seeking":
            if self.path is None:
                raise Unavailable()
            return False
        if prop in ("video-params", "video-out-params", "container-fps"):
            # what a real mpv said in CI: of a generator's carrier a small RGB picture, of a clip its own
            if self.path is None or self.kind(self.path) == "tone":
                raise Unavailable()
            if prop == "container-fps":
                return 30.0
            if self.kind(self.path) == "carrier":
                return {"pixelformat": "rgb0", "w": 64, "h": 36, "colormatrix": "rgb", "colorlevels": "full"}
            return {"pixelformat": "yuv420p", "w": 1920, "h": 1080, "colormatrix": "bt.709", "colorlevels": "limited"}
        if prop == "osd-width":
            return self.size[0]
        if prop == "osd-height":
            return self.size[1]
        if prop == "playlist-count":
            return len(self.playlist)
        if prop == "playlist-pos":
            return self.pos
        return self.props.get(prop)
