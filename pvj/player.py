# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-player core: one mpv process controlled over its JSON IPC socket.

This replaces omxplayer plus D-Bus. A single long-lived mpv is started idle and
clips are switched with `loadfile`, so changing clips does not tear down the
window (no black gap like the old stopall plus new process).
"""

import json
import math
import os
import signal
import socket
import stat
import subprocess
import threading
import time

from . import locks, paths

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus")
ENDINGS = ("loop", "stop", "next", "hold")      # what happens when a clip (or the list) ends


class PlayerError(Exception):
    pass


def runtime_dir():
    """The folder this process writes into (pvj/paths.py): created if missing, refused if it is not private."""
    try:
        return paths.own_dir()
    except paths.UnsafeDirectory as e:
        raise PlayerError(str(e))


def expand_media(paths, extensions=VIDEO_EXTENSIONS + IMAGE_EXTENSIONS):
    """Expand directories into sorted media files; keep URLs and files as given."""
    out = []
    for p in paths:
        if "://" in p:
            out.append(p)
        elif os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(extensions) and not name.startswith("."):
                    out.append(os.path.join(p, name))
        else:
            out.append(p)
    return out


def open_socket_when_ready(path, mode=0o660, timeout=15.0, poll=0.05):
    """Wait for a unix socket to appear at `path` and give it `mode`. True if it did. The caller must have
    removed any old socket first, or a stale file would be mistaken for the new one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if stat.S_ISSOCK(os.stat(path).st_mode):
                os.chmod(path, mode)
                return True
        except FileNotFoundError:
            pass
        except OSError:
            return False
        time.sleep(poll)
    return False


def detach_socket_opener(path, mode=0o660):
    """Start open_socket_when_ready in a fully detached grandchild, so the caller can exec mpv without leaving
    a zombie: the first child exits at once and is reaped here, and init adopts the grandchild."""
    pid = os.fork()
    if pid == 0:
        try:
            if os.fork() == 0:
                os.setsid()
                os._exit(0 if open_socket_when_ready(path, mode) else 1)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)


# A player that this process has just started is deaf for a while: mpv answers nothing while it makes its window and
# its GPU output. Measured on CI's runners (Mesa's software GPU under xvfb, 2026-10-08, pull request #96; the runs
# are in the journal): at the first start of a player on a fresh machine, the first answer came 0.2 to 5.0 seconds
# after the request (19 machines: 6 of them over 2 seconds, the longest 5.04), with the player's threads asleep and
# the machine idle; every later start answered within half a second and every other request within a second. The
# deaf moment can also begin after the first answer (seen once, 2026-10-05: the first question answered, the next
# request lost, a test failed). So for START_GRACE seconds after a start a request waits up to START_WAIT for its
# answer. Only then: at any other time a player that says nothing for `timeout` is a fault, and the caller hears of
# it as soon as before. Nothing here says what a real board needs; this was a software GPU.
START_WAIT = 10.0
START_GRACE = 15.0


class Ipc:
    def __init__(self, path, timeout=2.0):
        self.path = path
        self.timeout = timeout
        self.patient_until = 0.0    # time.monotonic() until which a request waits START_WAIT (Player._spawn sets it)
        self._id = 0

    def request(self, *command):
        self._id += 1
        rid = self._id
        payload = json.dumps({"command": list(command), "request_id": rid}).encode() + b"\n"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wait = max(self.timeout, START_WAIT) if time.monotonic() < getattr(self, "patient_until", 0.0) else self.timeout
        s.settimeout(wait)
        try:
            s.connect(self.path)
            s.sendall(payload)
            buf = b""
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == rid:
                        if msg.get("error") != "success":
                            raise PlayerError("mpv: %s" % msg.get("error"))
                        return msg.get("data")
            raise PlayerError("no reply from mpv")
        except (ConnectionRefusedError, FileNotFoundError):
            raise PlayerError("player is not running")
        except OSError as e:
            raise PlayerError("ipc error: %s" % e)
        finally:
            s.close()


class Player:
    # Defaults for a Player made without __init__ (some tests do); __init__ gives every player its own lock.
    _lock = locks.make("player", reentrant=True)
    _mapping_shaders, _mapping_mode, _source, _source_pid, _carrier, source_epoch = [], False, None, None, None, 0
    _effect, _effect_pid, effect_serial, effect_ended, effect_8bit, clears = None, None, 0, "", False, 0
    cleared_by, _seen_pid, _new_expected = "", None, False
    _pid_lock = locks.make("player.pid")
    _pipe, _pipe_pid = False, None      # a live input's pipe is what was loaded last, and the mpv it was loaded into

    def __init__(self, mpv_bin="mpv", extra_args=None, rundir=None):
        self.mpv_bin = mpv_bin
        self.extra_args = list(extra_args or [])
        # rundir is the folder THIS process writes (in the panel: the panel's own folder, where it puts the files it
        # hands to the player). The socket and the preview picture are in the player's folder, which only the
        # player creates; with an explicit rundir, or on a desk, that is the same folder (pvj/paths.py).
        self.rundir = rundir or runtime_dir()
        peer = rundir or paths.player_dir()
        self.socket_path = os.path.join(peer, paths.PLAYER_SOCKET)
        self.preview_path = os.path.join(peer, paths.PREVIEW)
        self.pid_path = os.path.join(self.rundir, paths.PLAYER_PID)
        self.ipc = Ipc(self.socket_path)
        self._proc = None
        # The player's shader list has two layers: a shader source (a generator drawn in place of a clip, see
        # pvj/shaders.py) and the projection mapping. Both are kept here so neither wipes the other.
        self._lock = locks.make("player", reentrant=True)        # its place among the locks: pvj/locks.py
        self._mapping_shaders = []
        self._mapping_mode = False
        self._source = None         # the generator shader file, only while its carrier picture is playing
        self._source_pid = None     # the mpv it was given to; a restarted mpv has lost it
        self._carrier = None        # the blank picture the source is drawn over, while it plays
        self.source_epoch = 0       # goes up each time what is playing changes hands; a shader rotation checks it
        # A third layer between the two: an effect, a filter shader over whatever plays (pvj/effects.py), a shader
        # source included (D74: both hook the same stage, and the order of the list makes the effect filter what
        # the source drew). It stays on when what plays changes and comes off when the screen is cleared (Stop, a
        # source taken off) and with a restart.
        self._effect = None         # the filter shader file
        self._effect_pid = None     # the mpv it was given to
        self.effect_serial = 0      # goes up each time an effect goes on or comes off; the effect's worker checks it
        self.effect_ended = ""      # why the last one came off: "off", "stop", "cleared", "restart", "refused", "format"
        self.effect_8bit = False    # 8-bit GPU buffers while an effect is on (the effects engine says: see _apply_fbo)
        # Goes up each time the screen is cleared (Stop, a shader taken off) and when the panel ends the player: the
        # moments after which an effect that was asked for before must not arrive. A change of what plays does not
        # move it, since an effect stays through that (D74); `effect_serial` does not do for it, since it moves on a
        # clearing only when an effect was on.
        self.clears = 0
        self.cleared_by = ""        # what moved it last: "stop", "cleared" (a shader taken off), "restart"
        self._seen_pid = None       # the mpv last heard from: another one is a new player, with nothing on its screen
        self._new_expected = False  # the panel ended the player (quit): the next new one is that restart, already counted
        self._pid_lock = locks.make("player.pid")       # a leaf: the look at the pid and the count are one step

    # --- lifecycle -------------------------------------------------------
    def is_running(self):
        try:
            self._note_pid(self.ipc.request("get_property", "pid"))
            return True
        except PlayerError:
            return False

    def _note_pid(self, pid):
        """Another mpv than the one last heard from (it crashed and its service started a new one, or the panel
        ended it): its screen began empty, which is a clearing for whatever was asked for before (see `clears`).
        Every play and every status asks for the pid, so it is noticed by whoever comes first."""
        with self._pid_lock:                # status polls come from several threads, unlocked: one restart is counted once
            if self._seen_pid is not None and pid != self._seen_pid:
                if self._new_expected:
                    # The panel itself ended the old one (quit), which was counted then. A wish made since is for
                    # the new player: it is not dropped for a restart it came after. (If that new player crashes
                    # before anyone has heard of it, the one after it is taken for it: two restarts counted as
                    # one. Harmless: nobody could have made a wish for a player that was never heard from.)
                    self._new_expected = False
                else:
                    self.clears += 1
                    self.cleared_by = "restart"
            self._seen_pid = pid
        return pid

    def mpv_command(self, audio_device=None, windowed=False):
        args = [self.mpv_bin, "--idle=yes", "--input-ipc-server=" + self.socket_path,
                "--no-terminal", "--really-quiet", "--osd-level=0", "--no-osc",
                "--keep-open=no", "--force-window=yes",
                # A file is judged by its content, not its name: a hostile drive can hold an "mp4" that is really a
                # playlist or EDL naming other files or URLs, plus sidecar subtitle and audio files. Play media only.
                # (--load-unsafe-playlists=no alone did not stop a fake .mp4 that was really a playlist: tested on a Pi 4.
                # --access-references=no does: such a file is no longer followed to other files or URLs.)
                "--load-unsafe-playlists=no", "--access-references=no", "--sub-auto=no", "--audio-file-auto=no", "--ytdl=no",
                "--load-scripts=no", "--load-auto-profiles=no"]
        if not windowed:
            args.append("--fullscreen")
        if audio_device:
            args.append("--audio-device=" + audio_device)
        args += self.extra_args
        return args

    def serve(self, audio_device=None, windowed=False):
        """Run mpv in the foreground, replacing this process. For systemd: it
        supervises mpv and restarts it, and the socket is the control channel."""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        args = self.mpv_command(audio_device, windowed)
        # mpv creates its socket owner-only (0600) whatever the umask, so the web panel (another user, same
        # group) could not reach it. A short-lived helper waits for the NEW socket and opens it to the group.
        detach_socket_opener(self.socket_path)
        try:
            os.execvp(args[0], args)
        except FileNotFoundError:
            raise PlayerError("mpv not found; install it (sudo apt install mpv)")

    def _spawn(self, audio_device=None, windowed=False):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        args = self.mpv_command(audio_device, windowed)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError:
            raise PlayerError("mpv not found; install it (sudo apt install mpv)")
        self._proc = proc
        self.ipc.patient_until = time.monotonic() + START_GRACE     # a player that is coming up is given time to answer
        with open(self.pid_path, "w") as f:
            f.write(str(proc.pid))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise PlayerError("mpv exited immediately (code %s)" % proc.returncode)
            if self.is_running():
                return
            time.sleep(0.1)
        proc.terminate()
        raise PlayerError("mpv did not become ready")

    def play(self, paths, loop=True, audio_device=None, windowed=False, spawn=True, ending=None, image_seconds=None):
        """Play `paths` (see _play). A shader source that was on is taken off first: it draws in place of the
        picture, so it must never stay over a clip."""
        with self._lock:
            self._end_source()
            self._pipe = False
            return self._play(paths, loop, audio_device, windowed, spawn, ending, image_seconds)

    def _play(self, paths, loop=True, audio_device=None, windowed=False, spawn=True, ending=None, image_seconds=None):
        """Play `paths` (a list plays as a playlist). `ending` says what happens at the end: "loop" (the clip, or the
        whole list), "stop" (black, player idle), "next" (a list goes on and stops after the last clip) or "hold"
        (the last frame stays on screen). Without `ending`, `loop` picks "loop" or "stop" as before.
        `image_seconds` is how long each image stays up in a list of images (a slideshow); a single image stays until
        something else is played."""
        ending = ending or ("loop" if loop else "stop")
        if ending not in ENDINGS:
            raise PlayerError("ending must be one of " + ", ".join(ENDINGS))
        files = expand_media(paths)
        if not files:
            raise PlayerError("no playable files found")
        if not self.is_running():
            if not spawn:
                raise PlayerError("player service is not running (systemctl start pvj-player)")
            self._spawn(audio_device, windowed)
        elif audio_device:
            self.ipc.request("set_property", "audio-device", audio_device)
        single = len(files) == 1
        self._undo_pipe_globals()
        # Set before loading: a new file picks these up as it starts.
        self.ipc.request("set_property", "keep-open", "yes" if ending == "hold" else "no")
        self.ipc.request("set_property", "image-display-duration",
                         "inf" if single or image_seconds is None else max(0.1, min(3600.0, float(image_seconds))))
        first = True
        for f in files:
            # "--" style option injection is impossible here: the path is a
            # loadfile argument, never a command line option.
            self.ipc.request("loadfile", f, "replace" if first else "append")
            first = False
        self._wait_for_path(files[0])
        self.ipc.request("set_property", "pause", False)
        looping = ending == "loop"
        self.ipc.request("set_property", "loop-file", "inf" if (looping and single) else "no")
        self.ipc.request("set_property", "loop-playlist", "inf" if (looping and not single) else "no")

    def _undo_pipe_globals(self):
        """Take back the options a live input set for the whole player on an old mpv."""
        if getattr(self, "_pipe_globals", None):
            for k in self._pipe_globals:
                try:
                    self.ipc.request("set_property", k, {"demuxer": "", "cache": "auto", "demuxer-readahead-secs": 1,
                                                         "demuxer-max-bytes": "150MiB"}.get(k, 0 if "rawvideo-w" in k or "rawvideo-h" in k else ""))
                except PlayerError:
                    pass
            self._pipe_globals = None

    def play_pipe(self, path, width, height, fps):
        """Play raw YUYV frames from a pipe (a live input read by a separate helper; see pvj/capture.py)."""
        with self._lock:
            self._end_source()
            self._pipe = False
            self._play_pipe(path, width, height, fps)       # raises if the pipe was not loaded: then it is not what plays
            self._pipe, self._pipe_pid = True, self.ipc.request("get_property", "pid")

    def _play_pipe(self, path, width, height, fps):
        if not self.is_running():
            raise PlayerError("player service is not running (systemctl start pvj-player)")
        opts = {"demuxer": "rawvideo", "demuxer-rawvideo-w": int(width), "demuxer-rawvideo-h": int(height),
                "demuxer-rawvideo-mp-format": "yuyv422", "demuxer-rawvideo-fps": int(fps), "cache": "no",
                "demuxer-readahead-secs": 0, "demuxer-max-bytes": "32MiB"}
        self.ipc.request("set_property", "keep-open", "no")
        self.ipc.request("set_property", "loop-file", "no")
        self.ipc.request("set_property", "loop-playlist", "no")
        try:        # mpv 0.38 and later take per-file options as the 4th argument of loadfile
            self.ipc.request("loadfile", path, "replace", -1, ",".join("%s=%s" % kv for kv in opts.items()))
        except PlayerError:
            # mpv 0.35 (Raspberry Pi OS Bookworm): set them for the player instead, and undo them on the next play
            for k, v in opts.items():
                self.ipc.request("set_property", k, v)
            self._pipe_globals = list(opts)
            self.ipc.request("loadfile", path, "replace")
        self.ipc.request("set_property", "pause", False)

    def shuffle(self):
        """Put the current playlist in a random order (the old panel's random player)."""
        self.ipc.request("playlist-shuffle")

    def _wait_for_path(self, path, timeout=3.0):
        """loadfile returns before mpv switches; wait so status() is truthful."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.ipc.request("get_property", "path") == path:
                    return
            except PlayerError:
                pass
            time.sleep(0.05)

    def stop(self):
        """Stop playback and end the mpv process."""
        try:
            self.ipc.request("quit")
        except PlayerError:
            pass
        pid = self._pid()
        if pid:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.05)
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if self._proc is not None:
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        for p in (self.socket_path, self.pid_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    @property
    def pipe_playing(self):
        """True from a live input's pipe being loaded until anything else is loaded, the screen is cleared or the
        player is another process (it was restarted, by the panel or by itself: the new one never had the pipe;
        a player that does not answer has not said that, and the answer stays what it was).
        Set and cleared under the lock with each load and clear, so it says what this side loaded last and does not
        wait for mpv's own `path` to follow. Api._stop_capture stops the live input's helper by it."""
        with self._lock:
            if not self._pipe:
                return False
            try:
                same = self.ipc.request("get_property", "pid") == self._pipe_pid
            except PlayerError:
                return True         # no answer is no news: one lost question must not make the pipe "not playing" for good
            if not same:
                self._pipe = False
            return self._pipe

    def quit(self):
        """End the mpv process (the service's unit starts a new one). What was loaded goes with it."""
        with self._lock:
            self._pipe = False
            if self._source is not None or self._carrier is not None:      # as _check_source does when it notices by itself
                self._source = self._carrier = None
            self.source_epoch += 1          # the screen has changed hands: a rotation's next change is not for this one
            with self._pid_lock:
                self.clears += 1            # and an effect that waits to go on is not for the next player
                self.cleared_by = "restart"
                self._new_expected = self._seen_pid is not None
            try:
                self.ipc.request("quit")
            except PlayerError:
                # The player did not take the quit (it does not answer): the same mpv may live on, and no new one
                # is on its way by this call. Left armed, the next restart that nobody asked for would pass as
                # this one, and a wish made before that crash would land after it.
                with self._pid_lock:
                    self._new_expected = False
                raise

    def _pid(self):
        try:
            with open(self.pid_path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    # --- controls --------------------------------------------------------
    def _set(self, name, value):
        self.ipc.request("set_property", name, value)

    def pause(self, paused=None):
        if paused is None:
            paused = not self.ipc.request("get_property", "pause")
        self._set("pause", bool(paused))
        return bool(paused)

    def seek(self, seconds):
        self.ipc.request("seek", float(seconds), "relative")

    def seek_to(self, seconds):
        """Jump to an absolute position in the current clip."""
        self.ipc.request("seek", max(0.0, float(seconds)), "absolute")

    def playlist_step(self, forward):
        """Next or previous clip in the playlist. Returns False when there is nothing in that direction."""
        count = self.ipc.request("get_property", "playlist-count") or 0
        pos = self.ipc.request("get_property", "playlist-pos")
        if count <= 1 or pos is None or pos < 0:
            return False
        looping = self.ipc.request("get_property", "loop-playlist") not in (False, "no", None)
        if forward and pos >= count - 1 and not looping:
            return False
        if not forward and pos <= 0 and not looping:
            return False
        self.ipc.request("playlist-next" if forward else "playlist-prev", "force")
        return True

    def speed(self, factor):
        self._set("speed", min(4.0, max(0.1, float(factor))))

    def volume(self, percent):
        self._set("volume", min(130.0, max(0.0, float(percent))))

    def clear(self, why="stop"):
        """Stop the current clip but keep the player service and window alive. The loop settings go back to off, so
        an idle player does not report the last clip's looping (the panel's Loop button read "on" with nothing
        playing, seen on the Pi after the test pattern); every play sets them again. An effect comes off with the
        picture it was over; `why` is what its record then says (see effect_ended)."""
        with self._lock:
            self._pipe = False
            self.clears += 1
            self.cleared_by = why
            try:
                self.ipc.request("stop")
            finally:
                self._end_source()      # also when the player is down: the screen has changed hands either way
                self._end_effect(why)
            self.ipc.request("set_property", "loop-file", "no")
            self.ipc.request("set_property", "loop-playlist", "no")

    def screenshot(self, path, quality=60, with_text=True):
        """Save what the player is showing right now as a JPEG. With `with_text` it is the whole window (brightness,
        size, on-screen text and pictures such as QR codes); without, only the video, so nothing drawn on top of it
        can be read from the snapshot. Takes about 0.7 s at 2560x1440 on a Pi 4."""
        self._set("screenshot-format", "jpg")
        self._set("screenshot-jpeg-quality", int(quality))
        self.ipc.request("screenshot-to-file", path, "window" if with_text else "video")

    def still(self, path):
        """Save the whole window as it is now (the picture with its effect, mapping and brightness, and what is
        drawn over it) into `path` as a PNG whose rows are plain bytes: no compression and no row filter, so the
        player does not spend time packing it and Python can read it with a few copies (pvj/transitions.py). The
        file's ending says PNG to the player; `path` may be a file that exists, which is then written over."""
        self._set("screenshot-high-bit-depth", False)
        self._set("screenshot-png-compression", 0)
        self._set("screenshot-png-filter", 0)
        self.ipc.request("screenshot-to-file", path, "window")

    def osd_size(self):
        """(width, height) of the picture the player is drawing on, in pixels, or None if unknown."""
        try:
            w, h = self.ipc.request("get_property", "osd-width"), self.ipc.request("get_property", "osd-height")
        except PlayerError:
            return None
        return (int(w), int(h)) if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0 else None

    def overlay(self, oid, x, y, width, height, pixels):
        """Draw a bitmap (raw BGRA, `width` x `height`) over the picture and the on-screen text, at x, y, until
        overlay_remove(oid). The pixels go through a file in the runtime folder that the player reads."""
        if not (isinstance(oid, int) and 0 <= oid < 64) or len(pixels) != width * height * 4:
            raise PlayerError("bad overlay")
        path = os.path.join(self.rundir, "overlay-%d.bgra" % oid)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o640)
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o640)          # on the open file: a name swapped for a link cannot redirect it
            f.write(pixels)
        self.ipc.request("overlay-add", oid, int(x), int(y), path, 0, "bgra", int(width), int(height), int(width) * 4)

    def overlay_remove(self, oid):
        """Take an overlay off, and remove the file overlay() wrote for it: for a QR code those pixels are an access
        code, which must not lie in the runtime folder after it ended. The file goes even if the player does not
        answer (it has read the pixels already or never will)."""
        try:
            self.ipc.request("overlay-remove", oid)
        finally:
            if isinstance(oid, int) and 0 <= oid < 64:
                try:
                    os.unlink(os.path.join(self.rundir, "overlay-%d.bgra" % oid))
                except OSError:
                    pass

    def overlay_file(self, oid, path, width, height):
        """Draw a ready raw BGRA file (width x height, at 0, 0) over the picture until overlay_remove(oid)."""
        self.ipc.request("overlay-add", oid, 0, 0, path, 0, "bgra", int(width), int(height), int(width) * 4)

    def overlay_part(self, oid, path, x, y, offset, width, height, stride):
        """Draw a part of a raw BGRA file at x, y until overlay_remove(oid): `width` x `height` pixels, the first of
        them `offset` bytes into the file, each row `stride` bytes after the one before. With the stride of the
        whole picture this draws any rectangle of it without writing it anew (a wipe, pvj/transitions.py). The
        file must hold `offset` plus `height` times `stride` bytes: that much the player maps."""
        self.ipc.request("overlay-add", oid, int(x), int(y), path, int(offset), "bgra", int(width), int(height), int(stride))

    def set_mapping_mode(self, on):
        """While a projection mapping is shown: stretch the picture to the whole screen (the mapping is in screen
        pixels; mpv's final-picture shader only covers the picture's own area, so a letterboxed clip would move every
        surface) and use 8-bit GPU buffers (16-bit ones made the mapping's extra pass drop frames on a Pi 4)."""
        with self._lock:
            self.ipc.request("set_property", "keepaspect", not on)
            self._mapping_mode = bool(on)
            self._apply_fbo()

    def _check_source(self):
        """Forget a shader source that was given to an mpv that has since been restarted: the carrier is gone, so the
        source must go too, and the screen has changed hands."""
        if self._source is None and self._carrier is None:
            return
        try:
            same = self.ipc.request("get_property", "pid") == self._source_pid
        except PlayerError:
            same = False
        if not same:
            self._source = self._carrier = None
            self.source_epoch += 1

    def _check_effect(self):
        """Forget an effect that was given to an mpv that has since been restarted: the new one never had it."""
        if self._effect is None:
            return
        try:
            same = self.ipc.request("get_property", "pid") == self._effect_pid
        except PlayerError:
            same = False
        if not same:
            self._drop_effect("restart")

    def _drop_effect(self, why):
        self._effect, self.effect_ended = None, why
        self.effect_serial += 1

    def _end_effect(self, why):
        """The effect comes off (a player that is down has lost it anyway)."""
        if self._effect is not None:
            self._drop_effect(why)
            try:
                self._push_shaders()
                # Only where the effect was the one reason for the 8-bit buffers: under a mapping or a shader source
                # they stay as they are, and setting the format again makes the player set its renderer up anew.
                if self.effect_8bit and not (self._mapping_mode or self._source):
                    self._apply_fbo()
            except PlayerError:
                pass

    def _apply_fbo(self):
        """8-bit GPU buffers while a mapping or a shader source adds a pass, mpv's own choice otherwise (16-bit ones
        made the mapping's extra pass drop frames on a Pi 4). For an effect only where `effect_8bit` says so: the
        effects engine sets it on the boards that run mpv with its cheap scaling (a Pi 4). There the setting was
        harmless in CI, over a playing and over a frozen clip. With mpv's default scalers (other boards) a
        screenshot right after an effect went on with this setting was black, and a filter has only the picture to
        draw from and goes on in the middle of a clip, so there the buffers are left as they are."""
        self._check_source()
        self._check_effect()
        want = self._mapping_mode or self._source or (self._effect and self.effect_8bit)
        self.ipc.request("set_property", "fbo-format", "rgba8" if want else "auto")

    def set_shaders(self, paths):
        """Use these GLSL user shader files (the projection mapping), or none. The files must be readable by the
        player; they are compiled on the GPU at once. A shader source that is on stays on, in front of them."""
        with self._lock:
            self._mapping_shaders = list(paths)
            self._push_shaders()

    def _push_shaders(self):
        """The player's one shader list, in the order the picture passes them: the source (a generator, in place of
        the picture), the effect (a filter of the picture), the projection mapping (the last stage). The source and
        the effect hook the same stage, and there the order of this list is the order they run in (seen on a real
        mpv in CI, tests/test_pair_gpu.py: the other way round the generator draws over what the effect made). So
        an effect over a source filters the source's picture."""
        self._check_source()
        self._check_effect()
        self.ipc.request("set_property", "glsl-shaders", ([self._source] if self._source else []) + ([self._effect] if self._effect else [])
                         + self._mapping_shaders)

    # --- the effect layer (pvj/effects.py) ------------------------------------
    @property
    def effect_shader(self):
        return self._effect

    def effect_on(self):
        """The effect's shader file if this mpv still has it, else None (a restarted mpv has lost it)."""
        with self._lock:
            self._check_effect()
            return self._effect

    def put_effect(self, shader, serial=None, epoch=None, clears=None):
        """Put the filter shader file `shader` on over whatever plays (a clip, a live input, a shader source), in
        place of the effect that is on. Returns the new effect serial, or None, with nothing changed, when `clears`
        is given and the screen was cleared since it was handed out (what a wish that waited for the worker
        carries: see `clears`), or when `serial` or `epoch` are given and an effect went on or off, or something
        was played or stopped, since they were handed out (the stricter rule, which the effects no longer use)."""
        with self._lock:
            self._check_source()
            self._check_effect()
            pid = self._note_pid(self.ipc.request("get_property", "pid"))      # before the look at `clears`: a new mpv moves it
            if clears is not None and clears != self.clears:
                return None
            if (serial is not None and serial != self.effect_serial) or (epoch is not None and epoch != self.source_epoch):
                return None
            previous = self._effect
            self._effect, self._effect_pid = shader, pid
            try:
                self._push_shaders()
                # the first text of an effect (a change of text leaves the buffers), and only where they are not
                # 8-bit already for a mapping or a shader source
                if self.effect_8bit and previous is None and not (self._mapping_mode or self._source):
                    self._apply_fbo()
            except PlayerError:
                self._effect = previous
                try:
                    self._push_shaders()
                except PlayerError:
                    pass
                raise
            self.effect_serial += 1
            self.effect_ended = ""
            return self.effect_serial

    def swap_effect(self, shader, serial):
        """Exchange the effect's file for another text of the same effect, only while `serial` is still current.
        True if it was done."""
        with self._lock:
            self._check_effect()
            if serial != self.effect_serial or self._effect is None:
                return False
            self._effect = shader
            self._push_shaders()
            return True

    def clear_effect(self, serial=None, why="off"):
        """Take the effect off; with `serial`, only that one. True if one was on and is now off. Without `serial`
        (somebody said Off) the effect serial moves on even when nothing is on: an effect that was asked for before
        the Off and has not reached the player yet must not arrive after it."""
        with self._lock:
            self._check_effect()
            if self._effect is None or (serial is not None and serial != self.effect_serial):
                if serial is None:
                    self.effect_serial += 1
                return False
            self._end_effect(why)
            return True

    def _end_source(self):
        """Something else takes the screen: the shader source comes off (a player that is down has lost it anyway)."""
        self.source_epoch += 1
        had, self._source, self._carrier = self._source, None, None
        if had is not None:
            try:
                self._push_shaders()
                self._apply_fbo()
            except PlayerError:
                pass

    def claim_screen(self):
        """Something else is about to be played but has not been loaded yet (a clip waiting for its dip to black):
        from now on a shader rotation no longer owns the screen. The source itself stays until the clip loads."""
        with self._lock:
            self.source_epoch += 1

    def source_opacity(self, value, epoch):
        """Set the opacity (0 to 255) only while `epoch` is still current; the check and the change are one step, so
        a rotation's own fade can never darken a clip that was started meanwhile. True if it was done."""
        with self._lock:
            if epoch != self.source_epoch:
                return False
            self.opacity(value)
            return True

    def opacity_now(self):
        """The opacity the picture has at this moment, 0 to 100 percent, or None if the player cannot say."""
        try:
            b = self.ipc.request("get_property", "brightness")
        except PlayerError:
            return None
        return max(0.0, min(100.0, 100.0 + b)) if isinstance(b, (int, float)) and not isinstance(b, bool) else None

    def clear_source(self, epoch):
        """Stop the carrier and its shader, only if `epoch` is still current and a carrier is what plays. True if done.
        The check and the stop are one step: a clip started in between is never stopped."""
        with self._lock:
            if epoch != self.source_epoch or self._carrier is None:
                return False
            self.clear("cleared")
            return True

    @property
    def source_shader(self):
        return self._source

    def play_source(self, shader, carrier, epoch=None, spawn=False):
        """Draw the generator shader file `shader` in place of the picture, over `carrier` (a blank picture from the
        player itself that gives the shader frames to draw on). With `epoch`, only if nothing else has been played
        since that epoch was handed out; otherwise None is returned and nothing changes. Returns the new epoch.
        If the carrier is already playing only the shader is exchanged, so the picture does not restart. An effect
        that is on stays on, after the source in the list: it filters what the source draws (D74)."""
        with self._lock:
            if epoch is not None and epoch != self.source_epoch:
                return None
            self._pipe = False
            if not self.is_running():
                if not spawn:
                    raise PlayerError("player service is not running (systemctl start pvj-player)")
                self._spawn(None, False)
            self._undo_pipe_globals()
            previous = self._source
            try:
                pid = self.ipc.request("get_property", "pid")
                # The carrier is left alone only if this side loaded it last, into this mpv, and mpv says it plays.
                # mpv's `path` alone is not enough: it lags a load (a live input's pipe is loaded without waiting
                # for it, so `path` could still name the carrier of the generator before, and the pipe then stayed
                # on screen with its helper stopped). The record alone is not enough either: a carrier mpv has
                # dropped must be loaded again. When the two disagree it is loaded, which costs a restart of a
                # blank picture and nothing else.
                mine = self._carrier == carrier and self._source_pid == pid
                self._source, self._source_pid = shader, pid
                self._push_shaders()
                self._apply_fbo()
                try:
                    current = self.ipc.request("get_property", "path")
                except PlayerError:
                    current = None
                if not mine or current != carrier:
                    self.ipc.request("loadfile", carrier, "replace")
                    self._wait_for_path(carrier)
                    if self.ipc.request("get_property", "path") != carrier:
                        raise PlayerError("the player did not start the blank picture the shader is drawn on")
                    self.ipc.request("set_property", "keep-open", "no")     # only once the carrier is what plays
                self._carrier = carrier
                self.ipc.request("set_property", "pause", False)
                self.ipc.request("set_property", "loop-file", "no")
                self.ipc.request("set_property", "loop-playlist", "no")
            except PlayerError:
                self._source = previous if previous != shader else None
                if self._source is None:
                    self._carrier = None
                try:
                    self._push_shaders()
                    self._apply_fbo()
                except PlayerError:
                    pass
                raise
            self.source_epoch += 1
            return self.source_epoch

    def swap_source(self, shader, epoch):
        """Exchange the shader source for another file, or None for the bare carrier (black), only while `epoch` is
        still current. True if it was done. The buffers' format is set only when a source comes or goes (the bare
        carrier, a player that was restarted), never for another text of a source that stays: setting it makes mpv
        set its renderer up anew, and a generator's text is exchanged at every change of a value."""
        with self._lock:
            if epoch != self.source_epoch:
                return False
            had, self._source = self._source, shader
            self._push_shaders()
            if (had is None) != (self._source is None):
                self._apply_fbo()
            return True

    def flip(self, horizontal, on):
        """Mirror the picture left-right or upside down (a video filter; about half a core more on a Pi 4 at 1080p)."""
        label = "@pvjfliph" if horizontal else "@pvjflipv"
        try:
            self.ipc.request("vf", "remove", label)
        except PlayerError:
            pass
        if on:
            self.ipc.request("vf", "add", "%s:%s" % (label, "hflip" if horizontal else "vflip"))

    def volume_step(self, delta):
        self.ipc.request("add", "volume", float(delta))

    def opacity(self, value):
        """0..255 like the old panel. mpv cannot blend against other layers, so
        this fades to black (correct for a black stage background)."""
        value = min(255, max(0, int(value)))
        self._set("brightness", int(round(-100 * (1 - value / 255.0))))

    def size(self, percent):
        """Scale the picture around the centre; 100 is fit-to-window."""
        self._set("video-zoom", math.log2(max(1, min(400, float(percent))) / 100.0))

    def position(self, x, y=0):
        """Shift the picture; units are thousandths of the picture width and height (y positive moves it down)."""
        self._set("video-pan-x", max(-3000, min(3000, float(x))) / 1000.0)
        self._set("video-pan-y", max(-3000, min(3000, float(y))) / 1000.0)

    def loop(self, enabled):
        """Loop the current clip (or the whole playlist when there are several)."""
        count = self.ipc.request("get_property", "playlist-count") or 0
        self._set("loop-file", "inf" if (enabled and count <= 1) else "no")
        self._set("loop-playlist", "inf" if (enabled and count > 1) else "no")

    def mute(self, muted):
        self._set("mute", bool(muted))

    def rotate(self, degrees):
        if degrees not in (0, 90, 180, 270):
            raise PlayerError("rotation must be 0, 90, 180 or 270")
        self._set("video-rotate", int(degrees))

    TEST_PATTERN = "av://lavfi:smptehdbars=size=1920x1080:rate=25"
    # 5 s of 440 Hz, on the left, the right or both channels (the old Settings tab's test tones)
    TEST_TONES = {"left": "av://lavfi:aevalsrc=0.5*sin(440*2*PI*t)|0:s=48000:d=5",
                  "right": "av://lavfi:aevalsrc=0|0.5*sin(440*2*PI*t):s=48000:d=5",
                  "both": "av://lavfi:aevalsrc=0.5*sin(440*2*PI*t)|0.5*sin(440*2*PI*t):s=48000:d=5"}

    def status(self):
        if not self.is_running():
            return {"running": False}
        out = {"running": True}
        for key, prop in (("path", "path"), ("position", "time-pos"), ("duration", "duration"),
                          ("paused", "pause"), ("speed", "speed"), ("volume", "volume"), ("muted", "mute"), ("loop_file", "loop-file"), ("loop_playlist", "loop-playlist"),
                          ("playlist_pos", "playlist-pos"), ("playlist_count", "playlist-count")):
            try:
                out[key] = self.ipc.request("get_property", prop)
            except PlayerError:
                out[key] = None
        return out


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except (OSError, IndexError):
        return True
