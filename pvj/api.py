# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The control API: pure request handling, no sockets.

`Api.handle(method, path, body, device, client)` returns (status, payload). The
HTTP layer (server.py) authenticates, enforces POST plus the CSRF header, and
calls this. Every input is validated here; nothing user-supplied reaches a shell
or a path unchecked.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata

from . import dmx as dmx_mod, hardware, midi as midi_mod, netcfg, osc as osc_mod, presets, streams as streams_mod, themes as themes_mod
from . import auth as auth_mod
from .auth import Auth, AuthError
from .modules import ModuleError
from .player import AUDIO_EXTENSIONS, ENDINGS, IMAGE_EXTENSIONS, PlayerError, VIDEO_EXTENSIONS
from .themes import ThemeError

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + IMAGE_EXTENSIONS + AUDIO_EXTENSIONS
PLAYLIST_EXTENSIONS = VIDEO_EXTENSIONS + IMAGE_EXTENSIONS      # what "play all" picks up: the old panel played pictures, not songs
SLIDE_MIN, SLIDE_MAX = 0.1, 3600
_NAME = re.compile(r"[^\x00-\x1f/\\]{1,120}")
_USB_LABEL = re.compile(r"[A-Za-z0-9._-]{1,64}")
USB_SCAN_LIMIT = 2000          # directory entries looked at per drive; a hostile drive can hold millions
USB_CACHE_SECONDS = 2.0
PRESET_MAX_FILES = 200         # files a preset will queue in the player


def _usb_label_ok(label):
    """A drive folder name: the safe characters, and never '.', '..' or a hidden name."""
    return bool(_USB_LABEL.fullmatch(label)) and not label.startswith(".")
_MODULE_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def valid_name(name):
    """A plain file name: no path parts, no control, bidi or other invisible characters, short enough for
    the filesystem in bytes (ext4 limit 255), not a dotfile."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in (".", "..") or name.startswith("."):
        return False
    if len(name.encode("utf-8", "surrogatepass")) > 200:
        return False
    # U+FFFD is what invalid UTF-8 from a client turns into: a name that is not really a name
    return "\ufffd" not in name and not any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") for ch in name)


class ApiError(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def bad(message):
    return ApiError(400, message)


def number(body, key, lo, hi, integer=False):
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise bad("%s must be a number from %s to %s" % (key, lo, hi))
    return int(v) if integer else float(v)


PREVIEW_MIN_INTERVAL = 3.0    # seconds: a snapshot stalls playback for about a quarter second on a Pi 4, so viewers share one frame
PREVIEW_MAX_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get("PVJ_MAX_UPLOAD_MB", "8192")) * 1024 * 1024
FREE_SPACE_RESERVE = 200 * 1024 * 1024  # never fill the disk completely: the system needs room to work
CHUNK = 256 * 1024
MIN_UPLOAD_RATE = 20 * 1024   # bytes/second: slower than this after the grace period is abandoned
RATE_GRACE_SECONDS = 30.0


class Fader:
    """Ramps opacity in steps inside the player, on a background thread, so one
    request replaces the old one-process-per-step approach."""

    def __init__(self, apply):
        self._apply = apply
        self._token = 0
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._token += 1

    def ramp(self, start, end, seconds, then=None):
        with self._lock:
            self._token += 1
            token = self._token
        steps = max(1, int(seconds * 20))

        def run():
            for i in range(1, steps + 1):
                with self._lock:
                    if token != self._token:
                        return
                self._apply(start + (end - start) * i / steps)
                time.sleep(seconds / steps)
            if then:
                with self._lock:
                    if token != self._token:
                        return
                then()
        threading.Thread(target=run, daemon=True).start()


class Api:
    def __init__(self, player, settings, auth, registry, themes, media_dir, board, addons_dir=None,
                 spawn=False, on_pin=None, osc=None, free_space=None, net=None, ip_json=None, net_sysfs="/sys/class/net",
                 usb_root=None, usb_link=None):
        self.player = player
        self.settings = settings
        self.auth = auth
        self.registry = registry
        self.themes = themes
        self.media_dir = media_dir
        self.usb_root = usb_root or os.environ.get("PVJ_USB_BASE", "/media/pvj")     # one folder per mounted drive
        self.usb_link = usb_link or os.environ.get("PVJ_USB_DIR", "/media/usb")      # the newest drive, for the old presets
        self.board = board
        self.addons_dir = addons_dir
        self.spawn = spawn        # True only for development: start mpv ourselves
        self.on_pin = on_pin      # called with the new PIN so the box can show it
        self.osc = osc            # OscManager or None
        self._free_space = free_space or self._statvfs_free
        self.net = net            # NetdClient or None
        self._sysfs = net_sysfs
        self._ip_json = ip_json or self._run_ip
        self._upload_lock = threading.Lock()  # one upload at a time: protects the SD card and the threads
        self._media_lock = threading.Lock()   # rename, delete and publishing an upload never interleave
        self.mix = {"opacity": 100, "blackout": False, "size": 100, "position": 0, "position_y": 0, "rotate": 0,
                    "flip_h": False, "flip_v": False}
        self.fader = Fader(self._apply_opacity)
        self._preview_lock = threading.Lock()
        self._control_lock = threading.RLock()
        self._usb_cache = (0.0, [])
        self._preview = None      # (time, jpeg bytes) of the last frame
        self.scheduler = None     # Scheduler or None
        self.autostart = None     # Autostart or None
        self.pinscreen = None     # PinScreen or None
        self.sysd = None          # SysdClient or None (reboot, power off, set the clock)
        self.capture = None       # Capture or None (live input from a USB capture device)
        self._import = {}         # the USB copy running or last run
        self._import_lock = threading.Lock()
        from . import mapper as mapper_mod
        self.mapper = mapper_mod.Engine(self)
        from . import health as health_mod
        self.health = health_mod.Health(self, getattr(player, "rundir", "/run/pvj"))     # checks start in server.build
        from . import sync as sync_mod
        self.sync = sync_mod.SyncManager(self, settings)            # started by server.build
        from . import support as support_mod
        self.support = support_mod.SupportManager(settings, auth, None,      # the helper client is set by server.build
                                                  networks_in_use=self._support_clash_networks)
        from . import projector as projector_mod
        self.projectors = projector_mod.Monitor(self, log=lambda line: self.log(line))    # started by server.build and the module switch
        self.dmx = None           # DmxManager or None
        self.midi = None          # MidiManager or None

    # --- helpers -------------------------------------------------------
    def _apply_opacity(self, percent):
        try:
            self.player.opacity(round(min(100, max(0, percent)) * 2.55))
        except PlayerError:
            pass

    def resolve_media(self, name):
        """A media file by name, guaranteed to live inside the media folder."""
        if not valid_name(name):
            raise bad("invalid file name")
        if not name.lower().endswith(MEDIA_EXTENSIONS):
            raise bad("not a media file")
        root = os.path.realpath(self.media_dir)
        path = os.path.realpath(os.path.join(root, name))
        if os.path.dirname(path) != root or not os.path.isfile(path):
            raise ApiError(404, "file not found")
        return path

    def usb_drives(self):
        """Media files at the top level of each mounted USB drive: [{"drive": label, "files": [{"name", "size"}]}].
        The drive is untrusted and can hold any number of files, so the scan stops after USB_SCAN_LIMIT entries and the
        result is kept for a few seconds instead of being rebuilt for every request."""
        now = time.monotonic()
        if now - self._usb_cache[0] < USB_CACHE_SECONDS:
            return self._usb_cache[1]
        out = []
        try:
            labels = sorted(os.listdir(self.usb_root))[:16]
        except OSError:
            labels = []
        for label in labels:
            base = os.path.join(self.usb_root, label)
            if not _usb_label_ok(label) or os.path.islink(base) or not os.path.isdir(base):
                continue
            files, seen = [], 0
            try:
                with os.scandir(base) as it:
                    for e in it:
                        seen += 1
                        if seen > USB_SCAN_LIMIT:
                            break
                        if (e.name.startswith(".") or not e.name.lower().endswith(MEDIA_EXTENSIONS) or not valid_name(e.name)
                                or e.is_symlink() or not e.is_file()):
                            continue
                        files.append({"name": e.name, "size": e.stat().st_size})
            except OSError:
                continue
            files.sort(key=lambda f: f["name"].lower())
            out.append({"drive": label, "files": files[:200], "truncated": seen > USB_SCAN_LIMIT or len(files) > 200})
        self._usb_cache = (now, out)
        return out

    def resolve_usb(self, ref):
        """A media file on a mounted USB drive, given as 'LABEL/name.mp4'; never a path outside that drive's folder."""
        parts = ref.split("/") if isinstance(ref, str) else []
        if len(parts) != 2 or not _usb_label_ok(parts[0]) or not valid_name(parts[1]) or not parts[1].lower().endswith(MEDIA_EXTENSIONS):
            raise bad("invalid USB file")
        root = os.path.realpath(self.usb_root)
        base = os.path.join(root, parts[0])
        path = os.path.join(base, parts[1])
        if (os.path.islink(base) or os.path.islink(path) or os.path.realpath(base) != base
                or os.path.dirname(os.path.realpath(path)) != base or not os.path.isfile(path)):
            raise ApiError(404, "file not found on the USB drive")
        return path

    def media_list(self):
        try:
            names = sorted(n for n in os.listdir(self.media_dir)
                           if not n.startswith(".") and n.lower().endswith(MEDIA_EXTENSIONS)
                           and os.path.isfile(os.path.join(self.media_dir, n)))
        except OSError:
            names = []
        return names

    def _player_call(self, fn, *args):
        try:
            return fn(*args)
        except PlayerError as e:
            raise ApiError(503, str(e))

    # --- handlers ------------------------------------------------------
    def hello(self, body, device, client):
        return {"name": "nxlx.mastercontrol", "paired": bool(device), "board": self.board["kind"],
                "remote": self.support.is_remote(client)}

    def pair(self, body, device, client):
        try:
            token, dev = self.auth.pair(str(body.get("pin", "")), str(body.get("name", "device")), client)
        except AuthError as e:
            raise ApiError(429 if e.retry_after else 403, str(e), e.retry_after)
        return {"device": dev, "token": token}

    def session(self, body, device, client):
        """Start a session from a token (a guest link): the server sets the cookie."""
        dev = self.auth.authenticate(body.get("token"))
        if dev is None:
            raise ApiError(403, "invalid or revoked token")
        return {"device": dev, "token": body["token"]}

    def status(self, body, device, client):
        temps = hardware.temperatures()
        return {"player": self._public_player_status(), "mix": dict(self.mix, **self.settings.data["mix"]),
                "system": {"board": self.board["kind"], "model": self.board["model"],
                           "temp_c": max((t["celsius"] for t in temps), default=None)},
                "device": device, "support": self.support.banner()}

    def access_on_screen(self):
        """True while the PIN or join codes are drawn on the display (on request, or the first-run screen)."""
        ps = self.pinscreen
        if ps is None:
            return False
        try:
            return bool(ps.status()["showing"]) or ps.auto_wanted()
        except Exception:
            return True                           # when unsure, treat it as shown: a snapshot then leaves the text out

    def preview_jpeg(self, device=None):
        """A JPEG of what the player is showing on the screen. One screenshot at a time; viewers who ask within
        PREVIEW_MIN_INTERVAL of the last one get the same frame, so ten phones cost no more than one. A failure
        (no picture yet) is remembered for a few seconds too, so requests cannot queue up behind a slow player.
        While the PIN or join codes are on the display, a device without full access gets the video only (no
        on-screen text or QR codes): otherwise a guest could read the full PIN or a presenter code off a snapshot."""
        with_text = not self.access_on_screen() or Auth.allows(device, "full")
        with self._preview_lock:
            now = time.monotonic()
            cached = self._preview if self._preview and self._preview[2] == with_text else None
            if cached and now - cached[0] < PREVIEW_MIN_INTERVAL:
                if isinstance(cached[1], ApiError):
                    raise ApiError(cached[1].status, cached[1].message)
                return cached[1]
            path = os.path.join(self.player.rundir, "preview.jpg")
            try:
                try:
                    os.unlink(path)               # never write through a link someone left there
                except FileNotFoundError:
                    pass
                self._player_call(self.player.screenshot, path, 60, with_text)
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ApiError(500, "preview file is not a regular file")
                    with os.fdopen(fd, "rb", closefd=False) as f:
                        data = f.read(PREVIEW_MAX_BYTES + 1)
                finally:
                    os.close(fd)
                if not data.startswith(b"\xff\xd8") or len(data) > PREVIEW_MAX_BYTES:
                    raise ApiError(503, "the player did not produce a picture")
            except ApiError as e:
                self._preview = (time.monotonic(), e, with_text)
                raise
            except OSError as e:
                err = ApiError(503, "no picture to show: %s" % (e.strerror or e))
                self._preview = (time.monotonic(), err, with_text)
                raise err
            self._preview = (time.monotonic(), data, with_text)
            return data

    def _public_player_status(self):
        """Player status with stream passwords hidden and the saved stream's name added."""
        status = dict(self.player.status())
        path = status.get("path")
        if path == getattr(self.player, "TEST_PATTERN", None):
            status["path"], status["test_pattern"] = None, True
            return status
        if self.capture is not None and path == self.capture.fifo:
            cur = self.capture.status(devices=False)["current"] or {}
            status["path"], status["capture"] = None, cur or True
            return status
        for channel, url in getattr(self.player, "TEST_TONES", {}).items():
            if path == url:
                status["path"], status["test_tone"] = None, channel
                return status
        if isinstance(path, str) and "://" in path:
            for st in self.settings.data.get("streams", []):
                if st["url"] == path:
                    status["stream"] = st["name"]
            status["path"] = streams_mod.redact(path)
        return status

    def _statvfs_free(self):
        path = self.media_dir
        while path and not os.path.exists(path):  # a media folder not created yet: ask its parent
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        try:
            st = os.statvfs(path)
        except OSError:
            return 0
        return st.f_bavail * st.f_frsize

    def sweep_stale_uploads(self):
        """Delete .upload-* temp files. They are hidden, can be gigabytes, and are left behind by a
        power cut or a killed service. Safe whenever no upload is running (we hold the lock or are starting)."""
        removed = 0
        try:
            names = os.listdir(self.media_dir)
        except OSError:
            return 0
        for n in names:
            if n.startswith(".upload-"):
                try:
                    os.unlink(os.path.join(self.media_dir, n))
                    removed += 1
                except OSError:
                    pass
        return removed

    def media(self, body, device, client):
        details = []
        for name in self.media_list():
            try:
                st = os.stat(os.path.join(self.media_dir, name))
                details.append({"name": name, "size": st.st_size, "modified": int(st.st_mtime)})
            except OSError:
                pass
        return {"files": [d["name"] for d in details], "details": details, "free": self._free_space(),
                "max_upload": MAX_UPLOAD_BYTES, "usb": self.usb_drives(),
                "autostart_usb": self.settings.data.get("autostart", {}).get("mode") == "usb"}

    def _safe_new_name(self, name):
        if not valid_name(name):
            raise bad("invalid file name")
        if not name.lower().endswith(MEDIA_EXTENSIONS):
            raise bad("only video, image and audio files: " + ", ".join(MEDIA_EXTENSIONS))
        return name

    def upload(self, name, length, read, replace=False, check=None, clock=time.monotonic):
        """Store a file from a stream. `read(n)` returns up to n bytes (b'' at the end); `check()` is
        called between chunks and may raise ApiError to abandon the upload (device revoked)."""
        name = self._safe_new_name(name)
        if not isinstance(length, int) or length <= 0:
            raise ApiError(411, "Content-Length required")
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(413, "file is larger than the %d MB limit" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        root = os.path.realpath(self.media_dir)
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise ApiError(500, "media folder is not writable: %s" % (e.strerror or e))
        if not self._upload_lock.acquire(blocking=False):
            raise ApiError(409, "another upload is in progress")
        tmp = None
        try:
            self.sweep_stale_uploads()  # any .upload-* file now is left over from a crash: no upload is running
            if length + FREE_SPACE_RESERVE > self._free_space():
                raise ApiError(507, "not enough free space on the box")
            final = os.path.join(root, name)
            if os.path.lexists(final) and not replace:
                raise ApiError(409, "a file with that name already exists")
            if os.path.islink(final):
                raise ApiError(409, "refusing to replace a link")
            fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=root)  # hidden, so it never shows in the list
            got, started = 0, clock()
            with os.fdopen(fd, "wb") as out:
                while got < length:
                    if check:
                        check()
                    chunk = read(min(CHUNK, length - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    elapsed = clock() - started
                    if elapsed > RATE_GRACE_SECONDS and got / elapsed < MIN_UPLOAD_RATE:
                        raise ApiError(408, "upload too slow (under %d KB/s); try again on a better connection"
                                       % (MIN_UPLOAD_RATE // 1024))
                out.flush()
                os.fsync(out.fileno())
            if got != length:
                raise ApiError(400, "upload was cut short (%d of %d bytes)" % (got, length))
            os.chmod(tmp, 0o664)
            with self._media_lock:
                if replace:
                    os.replace(tmp, final)
                else:
                    try:
                        os.link(tmp, final)  # fails if the name appeared meanwhile: never overwrites silently
                    except FileExistsError:
                        raise ApiError(409, "a file with that name appeared during the upload")
                    os.unlink(tmp)
                tmp = None
            self._fsync_dir(root)
            return {"name": name, "size": length}
        except (TimeoutError, ConnectionError):
            raise ApiError(400, "upload interrupted")
        except OSError as e:
            raise ApiError(500, "could not store the file: %s" % (e.strerror or e))
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            self._upload_lock.release()

    # --- copy a clip from a USB drive into the media folder (the old "Loading from USB to internal") ---------
    def import_usb(self, body, device, client):
        """{"usb": "LABEL/name.mp4", "replace": bool}: copy it into the media folder in the background, with the
        same checks as an upload (name, free space, a hidden temporary file, never overwriting unless asked).
        One copy or upload at a time. GET /api/media/import shows the progress."""
        src = self.resolve_usb(body.get("usb"))
        replace = body.get("replace", False)
        if not isinstance(replace, bool):
            raise bad("replace must be true or false")
        name = os.path.basename(src)
        try:
            fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as e:
            raise ApiError(404, "cannot read that file: %s" % (e.strerror or e))
        try:
            f = os.fdopen(fd, "rb")
        except OSError:
            os.close(fd)
            raise ApiError(404, "cannot read that file")
        try:
            size = os.fstat(f.fileno()).st_size
        except OSError:
            f.close()
            raise ApiError(404, "cannot read that file")
        if size == 0:
            f.close()
            raise bad("that file is empty")
        with self._import_lock:
            if self._import.get("active"):
                f.close()
                raise ApiError(409, "a copy is already running")
            self._import = {"active": True, "name": name, "from": body["usb"], "size": size, "done": 0, "cancel": False}
        job = self._import

        def read(n):
            try:
                chunk = f.read(n)
            except OSError:
                raise ApiError(503, "the USB drive stopped answering (pulled out, or damaged)")
            job["done"] += len(chunk)
            return chunk

        token_device = device.get("id") if device else None

        def check():
            if job["cancel"]:
                raise ApiError(499, "copy cancelled")
            if token_device and not any(d["id"] == token_device for d in self.auth.list_devices()) and not device.get("remote"):
                raise ApiError(403, "the device that started the copy was removed")

        def run():
            try:
                result = self.upload(name, size, read, replace=replace, check=check)
                job.update(active=False, result=result)
            except ApiError as e:
                job.update(active=False, error=e.message)
            except Exception as e:           # something unexpected: logged, said plainly
                print("pvj-web: USB copy failed: %s" % e)
                job.update(active=False, error="the copy failed")
            finally:
                f.close()
        threading.Thread(target=run, name="usb-import", daemon=True).start()
        return self.import_status({}, device, client)

    def import_status(self, body, device, client):
        j = self._import
        return {k: j.get(k) for k in ("active", "name", "from", "size", "done", "error", "result") if k in j} or {"active": False}

    def import_cancel(self, body, device, client):
        if self._import.get("active"):
            self._import["cancel"] = True
        return self.import_status(body, device, client)

    @staticmethod
    def _fsync_dir(path):
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # not every filesystem supports it (some USB mounts); the file itself was synced

    def _real_media_file(self, name):
        """The path of a real (non-link) media file called `name`; links are refused so a delete or rename
        can never act on something other than the name the user saw."""
        path = self.resolve_media(name)
        plain = os.path.join(os.path.realpath(self.media_dir), name)
        if os.path.islink(plain):
            raise ApiError(409, "%s is a link; change it on the box, not from here" % name)
        return plain

    def _pads_using(self, name):
        return [(b, i) for b, bank in enumerate(self.settings.data["pads"]["banks"])
                for i, p in enumerate(bank["pads"]) if p.get("file") == name]

    def delete_media(self, body, device, client):
        name = body.get("name")
        with self._media_lock:
            path = self._real_media_file(name)
            try:
                os.unlink(path)
            except OSError as e:
                raise ApiError(500, "could not delete: %s" % (e.strerror or e))
        return {"deleted": name, "pads_using": len(self._pads_using(name))}

    def rename_media(self, body, device, client):
        name = body.get("name")
        new = self._safe_new_name(body.get("new"))
        with self._media_lock:
            src = self._real_media_file(name)
            dst = os.path.join(os.path.realpath(self.media_dir), new)
            try:
                os.link(src, dst)  # fails if `new` exists: a rename must never overwrite
            except FileExistsError:
                raise ApiError(409, "a file with that name already exists")
            except OSError as e:
                raise ApiError(500, "could not rename: %s" % (e.strerror or e))
            os.unlink(src)
        with self.settings.lock:  # pads that used the old name follow the file
            for b, i in self._pads_using(name):
                self.settings.data["pads"]["banks"][b]["pads"][i]["file"] = new
            self.settings.save()
        return {"name": new}

    def get_pads(self, body, device, client):
        return {"banks": self.settings.data["pads"]["banks"]}

    def set_pad(self, body, device, client):
        bank = number(body, "bank", 0, len(self.settings.data["pads"]["banks"]) - 1, integer=True)
        index = number(body, "index", 0, 11, integer=True)
        label, file = body.get("label", ""), body.get("file", "")
        if not isinstance(label, str) or len(label) > 40 or re.search(r"[\x00-\x1f]", label):
            raise bad("invalid label")
        ending = body.get("ending", "loop")
        if ending not in ("loop", "stop", "hold"):
            raise bad("a pad ends with loop, stop or hold")
        if file != "":
            if not valid_name(file) or not file.lower().endswith(MEDIA_EXTENSIONS):
                raise bad("invalid file name")
        with self.settings.lock:
            self.settings.data["pads"]["banks"][bank]["pads"][index] = {"label": label, "file": file, "ending": ending}
            self.settings.save()
        return {"banks": self.settings.data["pads"]["banks"]}

    @staticmethod
    def _ending(body, default):
        """What happens at the end: body["ending"] (loop, stop, next, hold), else the legacy body["loop"], else `default`."""
        if "ending" in body:
            if body["ending"] not in ENDINGS:
                raise bad("ending must be one of " + ", ".join(ENDINGS))
            return body["ending"]
        if "loop" in body:
            if not isinstance(body["loop"], bool):
                raise bad("loop must be true or false")
            return "loop" if body["loop"] else "stop"
        return default

    @staticmethod
    def _shuffle_flag(body):
        v = body.get("shuffle", False)
        if not isinstance(v, bool):
            raise bad("shuffle must be true or false")
        return v

    def _start_list(self, paths, ending, shuffle, image_seconds=None):
        if shuffle:
            import random
            paths = list(paths)
            random.SystemRandom().shuffle(paths)
        self.fader.cancel()
        self._player_call(self.player.play, paths, ending == "loop", None, False, self.spawn, ending, image_seconds)
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        self._started_playing()
        return paths

    def play_usb_drive(self, body):
        """{"usb_drive": label, "shuffle": bool}: every clip at the top of that USB drive, looping. Used by autostart,
        so the drive that was plugged in is the one that plays (not whichever the /media/usb link points to)."""
        label = body.get("usb_drive")
        shuffle = body.get("shuffle", False)
        if not isinstance(shuffle, bool):
            raise bad("shuffle must be true or false")
        drive = [d for d in self.usb_drives() if d["drive"] == label]
        if not drive:
            raise ApiError(404, "no USB drive called %s" % label)
        paths = []
        for f in drive[0]["files"]:
            try:
                paths.append(self.resolve_usb("%s/%s" % (label, f["name"])))
            except ApiError:
                pass
        if not paths:
            raise ApiError(404, "no clips at the top of %s" % label)
        self._start_list(paths, "loop", shuffle)
        return {"playing": label, "files": len(paths)}

    def play_slideshow(self, body):
        """The images of the media folder, or of a USB drive, one after another: {"slideshow": {"source": "media" or a
        drive label, "seconds": 0.1 to 3600, "ending": ..., "shuffle": ...}}. The old Presenter tab's Slide Show."""
        spec = body.get("slideshow")
        if not isinstance(spec, dict):
            raise bad("slideshow must be an object")
        seconds = number(spec, "seconds", SLIDE_MIN, SLIDE_MAX)
        ending = self._ending(spec, "loop")
        shuffle = self._shuffle_flag(spec)
        source = spec.get("source", "media")
        if source == "media":
            root = os.path.realpath(self.media_dir)
        else:
            if not isinstance(source, str) or not _usb_label_ok(source):
                raise bad("unknown source")
            root = os.path.join(os.path.realpath(self.usb_root), source)
            if os.path.islink(root) or os.path.realpath(root) != root or not os.path.isdir(root):
                raise ApiError(404, "that USB drive is not mounted")
        names = []
        try:
            with os.scandir(root) as it:
                for _, e in zip(range(USB_SCAN_LIMIT), it):
                    if (not e.name.startswith(".") and e.name.lower().endswith(IMAGE_EXTENSIONS) and valid_name(e.name)
                            and not e.is_symlink() and e.is_file()):
                        names.append(e.name)
        except OSError:
            raise ApiError(404, "cannot read that folder")
        if not names:
            raise ApiError(404, "no images there")
        names.sort(key=str.lower)
        paths = [os.path.join(root, n) for n in names[:PRESET_MAX_FILES]]
        self._start_list(paths, ending, shuffle, seconds)
        return {"playing": "slideshow", "images": len(paths), "seconds": seconds}

    def play_preset(self, body, name):
        """A legacy start script name (startlessonce05 ...) played from the media folder or, for the usb ones, the USB drive."""
        try:
            preset = presets.parse_legacy_name(name if isinstance(name, str) else "")
            files = presets.resolve_files(preset, self.media_dir, self.usb_link)
        except PlayerError as e:
            raise bad(str(e))
        if preset["usb"]:
            root = os.path.realpath(self.usb_link)
            # /media/usb is either a link to a mounted drive folder or (old images) a real folder the drive is mounted on
            if os.path.islink(self.usb_link) and os.path.dirname(root) != os.path.realpath(self.usb_root):
                raise ApiError(404, "no USB drive is mounted")
        else:
            root = os.path.realpath(self.media_dir)
        paths = [f for f in (os.path.realpath(f) for f in files)
                 if os.path.dirname(f) == root and f.lower().endswith(PLAYLIST_EXTENSIONS) and valid_name(os.path.basename(f))]
        if not paths:
            raise ApiError(404, "no playable files for that preset")
        ending = self._ending(body, "loop" if preset["loop"] else "stop")
        self._start_list(paths[:PRESET_MAX_FILES], ending, self._shuffle_flag(body))
        return {"playing": name, "files": len(paths[:PRESET_MAX_FILES])}

    def play(self, body, device, client):
        if "preset" in body:
            return self.play_preset(body, body["preset"])
        if "stream" in body:
            return self.play_stream(body)
        if "slideshow" in body:
            return self.play_slideshow(body)
        if "capture" in body:
            return self.play_capture(body)
        if "usb_drive" in body:
            return self.play_usb_drive(body)
        if "pad" in body:
            pad = body["pad"]
            if not (isinstance(pad, list) and len(pad) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in pad)):
                raise bad("pad must be [bank, index]")
            banks = self.settings.data["pads"]["banks"]
            if not (0 <= pad[0] < len(banks) and 0 <= pad[1] < 12):
                raise bad("no such pad")
            name = banks[pad[0]]["pads"][pad[1]]["file"]
            if not name:
                raise bad("pad is empty")
            if "ending" not in body and "loop" not in body:
                body = dict(body, ending=banks[pad[0]]["pads"][pad[1]].get("ending", "loop"))
        elif "usb" in body:
            name = body["usb"]
        else:
            name = body.get("file")
        path = self.resolve_usb(name) if "usb" in body and "pad" not in body else self.resolve_media(name)
        ending = self._ending(body, "loop")
        if ending == "next":
            ending = "stop"            # one clip: there is no next
        loop = ending == "loop"
        transition = self.settings.data["mix"]
        playing = self._player_call(self.player.status).get("running")
        self.fader.cancel()  # a fade still running from an earlier action must not darken the new clip

        dip = transition["transition"] == "dip" and not self.mix["blackout"]

        def start():
            self._player_call(self.player.play, [path], loop, None, False, self.spawn, ending)
            if self.mix["blackout"]:
                return
            if dip:
                self._apply_opacity(0)
                self.fader.ramp(0, self.mix["opacity"], transition["duration"] / 2)
            else:
                self._apply_opacity(self.mix["opacity"])

        if playing and dip:
            self.fader.ramp(self.mix["opacity"], 0, transition["duration"] / 2, then=start)
        else:
            start()
        self._started_playing()
        return {"playing": name}

    def _need_streams(self):
        if not self.registry.enabled("inputs-srt"):
            raise ApiError(409, "turn on the Streams module in System first")

    def play_stream(self, body):
        self._need_streams()
        sid = body.get("stream")
        match = [s for s in self.settings.data["streams"] if s["id"] == sid]
        if not match:
            raise ApiError(404, "no such stream")
        self.fader.cancel()
        self._player_call(self.player.play, [match[0]["url"]], False, None, False, self.spawn)
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        self._started_playing()
        return {"playing": match[0]["name"]}

    def get_streams(self, body, device, client):
        self._need_streams()
        return {"streams": [{"id": s["id"], "name": s["name"], "url": streams_mod.redact(s["url"]),
                             "has_login": s["url"] != streams_mod.redact(s["url"])}
                            for s in self.settings.data["streams"]],
                "schemes": list(streams_mod.SCHEMES)}

    def set_streams(self, body, device, client):
        """Add or remove one stream. Saved addresses are never sent back to the panel, so an edit
        cannot round-trip a hidden password."""
        self._need_streams()
        action = body.get("action")
        with self.settings.lock:
            items = list(self.settings.data["streams"])
            try:
                if action == "add":
                    if len(items) >= streams_mod.MAX_STREAMS:
                        raise bad("at most %d streams" % streams_mod.MAX_STREAMS)
                    items.append(streams_mod.new_entry(body.get("name"), body.get("url")))
                elif action == "remove":
                    if not any(s["id"] == body.get("id") for s in items):
                        raise ApiError(404, "no such stream")
                    items = [s for s in items if s["id"] != body.get("id")]
                else:
                    raise bad("action must be add or remove")
            except streams_mod.StreamError as e:
                raise bad(str(e))
            self.settings.data["streams"] = items
            self.settings.save()
        return self.get_streams({}, device, client)

    def control(self, body, device, client):
        action = body.get("action")
        p = self.player
        if action == "pause":
            value = body.get("value")
            if value is not None and not isinstance(value, bool):
                raise bad("value must be true, false or null")
            return {"paused": self._player_call(p.pause, value)}
        if action == "seek":
            self._player_call(p.seek, number(body, "value", -3600, 3600))
        elif action == "speed":
            self._player_call(p.speed, number(body, "value", 0.1, 4))
        elif action == "volume":
            self._player_call(p.volume, number(body, "value", 0, 130))
        elif action == "opacity":
            self.mix["opacity"] = number(body, "value", 0, 100)
            if not self.mix["blackout"]:
                self.fader.cancel()
                self._player_call(p.opacity, round(self.mix["opacity"] * 2.55))
        elif action == "size":
            self.mix["size"] = number(body, "value", 1, 200)
            self._player_call(p.size, self.mix["size"])
        elif action == "position":
            self.mix["position"] = number(body, "value", -100, 100)
            self._player_call(p.position, self.mix["position"] * 10, self.mix["position_y"] * 10)
        elif action == "position_y":
            self.mix["position_y"] = number(body, "value", -100, 100)
            self._player_call(p.position, self.mix["position"] * 10, self.mix["position_y"] * 10)
        elif action in ("flip_h", "flip_v"):
            if not isinstance(body.get("value"), bool):
                raise bad("value must be true or false")
            self.mix[action] = body["value"]
            self._player_call(p.flip, action == "flip_h", body["value"])
        elif action == "rotate":
            degrees = number(body, "value", 0, 270, integer=True)
            if degrees not in (0, 90, 180, 270):
                raise bad("rotation must be 0, 90, 180 or 270")
            self.mix["rotate"] = degrees
            self._player_call(p.rotate, degrees)
        elif action == "loop":
            if not isinstance(body.get("value"), bool):
                raise bad("value must be true or false")
            self._player_call(p.loop, body["value"])
        elif action == "mute":
            if not isinstance(body.get("value"), bool):
                raise bad("value must be true or false")
            self._player_call(p.mute, body["value"])
        elif action == "stop":
            self._player_call(p.clear)
            self._stop_capture()
        elif action == "seek_to":
            self._player_call(p.seek_to, number(body, "value", 0, 24 * 3600))
        elif action == "shuffle":
            self._player_call(p.shuffle)
        elif action in ("next", "prev"):
            if not self._player_call(p.playlist_step, action == "next"):
                raise ApiError(409, "no %s clip in the playlist" % ("next" if action == "next" else "previous"))
        elif action == "volume_step":
            self._player_call(p.volume_step, number(body, "value", -50, 50))
        elif action == "reset":
            flipped = [k for k in ("flip_h", "flip_v") if self.mix[k]]
            self.mix.update(opacity=100, size=100, position=0, position_y=0, rotate=0, flip_h=False, flip_v=False)
            for k in flipped:
                self._player_call(p.flip, k == "flip_h", False)
            self.fader.cancel()
            # During a blackout the screen must stay dark: reset changes the stored mix, not the picture.
            shown = 0 if self.mix["blackout"] else 255
            for fn, arg in ((p.opacity, shown), (p.size, 100), (p.position, 0), (p.speed, 1), (p.rotate, 0)):
                self._player_call(fn, arg)
        else:
            raise bad("unknown action")
        return {"ok": True}

    def blackout(self, body, device, client):
        on = body.get("on")
        if not isinstance(on, bool):
            raise bad("on must be true or false")
        self.fader.cancel()
        self.mix["blackout"] = on
        self._player_call(self.player.opacity, 0 if on else round(self.mix["opacity"] * 2.55))
        return {"blackout": on}

    def fadeout(self, body, device, client):
        seconds = number(body, "seconds", 0.1, 30)
        self._player_call(self.player.status)
        self.fader.ramp(self.mix["opacity"], 0, seconds)
        return {"ok": True}

    def fadein(self, body, device, client):
        """From black (a blackout, or a faded-out picture) up to the mix opacity over `seconds`. The legacy panel had
        a fade in; until now the only way back from black here was an instant Show."""
        seconds = number(body, "seconds", 0.1, 30)
        self._player_call(self.player.status)
        self.fader.cancel()
        self.mix["blackout"] = False
        self._apply_opacity(0)
        self.fader.ramp(0, self.mix["opacity"], seconds)
        return {"ok": True}

    def test_tone(self, body, device, client):
        """5 seconds of a 440 Hz tone on the left, the right or both speakers, through the chosen sound output."""
        channel = body.get("channel")
        tones = getattr(self.player, "TEST_TONES", {})
        if channel not in tones:
            raise bad("channel must be left, right or both")
        self.fader.cancel()
        self._player_call(self.player.play, [tones[channel]], False, None, False, self.spawn, "stop")
        self._started_playing()
        return {"test_tone": channel}

    def media_info(self, body, device, client):
        """What is in a clip: {"name": file} from the media folder or {"usb": "LABEL/file"}."""
        from . import probe
        path = self.resolve_usb(body.get("usb")) if "usb" in body else self.resolve_media(body.get("name"))
        try:
            info = probe.probe(path, getattr(self.player, "mpv_bin", "mpv"))
        except probe.ProbeError as e:
            raise ApiError(422, str(e))
        info["advice"] = probe.advice(info, self.board["kind"])
        return info

    def system_info(self, body, device, client):
        """Versions, storage and screens: the old Settings and Display tabs' information buttons."""
        import platform
        import shutil
        from . import __version__
        try:
            du = shutil.disk_usage(self.media_dir)
            disk = {"total": du.total, "used": du.used, "free": du.free}
        except OSError:
            disk = None
        screens = []
        for c in hardware.drm_connectors():
            if c["connector"].lower().startswith("writeback"):
                continue                      # an internal capture path of the display chip, not a socket on the box
            modes = []
            for m in c["modes"]:
                if m not in modes:
                    modes.append(m)
            screens.append({"connector": c["connector"], "connected": c["status"] == "connected", "modes": modes[:24]})
        out = {"version": __version__, "mpv": self._mpv_version(), "os": hardware.os_release().get("name", ""),
               "kernel": platform.release(), "board": self.board.get("model") or self.board.get("kind"),
               "disk": disk, "screens": screens, "output": None, "clock": self.clock_status(),
               "system_actions": self.sysd is not None}
        try:
            size = self.player.osd_size()
            fps = self.player.ipc.request("get_property", "display-fps")
            if size:
                out["output"] = {"width": size[0], "height": size[1], "refresh": round(fps, 2) if isinstance(fps, (int, float)) else None}
        except (PlayerError, AttributeError):
            pass
        return out

    def _sysd(self, message):
        if self.sysd is None:
            raise ApiError(503, "the system helper is not available on this box")
        try:
            reply = self.sysd.request(message)
        except OSError as e:
            raise ApiError(503, str(e))
        if not reply.get("ok"):
            raise ApiError(409, reply.get("error", "the system helper refused"))
        return reply

    def clock_status(self):
        try:
            return self._sysd({"cmd": "status"})
        except ApiError:
            return {"clock_from_network": None, "now": int(time.time())}

    # --- updates from the panel (D33) ---------------------------------------------------------
    UPDATE_NAME = re.compile(r"pvj-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz(\.sig|\.sha256)?")
    UPDATE_MAX = 300 * 1024 * 1024           # the same limit as pvj-update
    UPDATE_SIDE_MAX = 64 * 1024              # a signature or a checksum file

    def _update_inbox(self):
        return os.path.join(os.path.dirname(self.settings.path), "update-inbox")

    def _update_result(self):
        path = os.environ.get("PVJ_UPDATE_RESULT", "/run/pvj-update/result.json")
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _update_running(self):
        """True while an update reports progress (a stale "running" older than the units' time limit is not)."""
        last = self._update_result()
        return bool(last and last.get("state") == "running" and time.time() - (last.get("at") or 0) < 35 * 60)

    def update_status(self, body, device, client):
        """What is installed, what is waiting (on a USB drive or uploaded), and the last update's progress."""
        from . import __version__
        import glob
        usb = []
        for path in sorted(glob.glob(os.path.join(self.usb_root, "*", "pvj-update", "pvj-*.tar.gz"))):
            m = self.UPDATE_NAME.fullmatch(os.path.basename(path))
            if m and not m.group(2):
                usb.append({"drive": path.split(os.sep)[-3], "version": m.group(1), "signed": os.path.isfile(path + ".sig")})
        inbox = []
        try:
            for n in sorted(os.listdir(self._update_inbox())):
                m = self.UPDATE_NAME.fullmatch(n)
                if m and not m.group(2):
                    inbox.append({"version": m.group(1), "signed": os.path.isfile(os.path.join(self._update_inbox(), n + ".sig"))})
        except OSError:
            pass
        return {"version": __version__, "usb": usb, "inbox": inbox, "last": self._update_result()}

    def start_update(self, body, device, client):
        """{"source": "usb" | "inbox", "version": "N.N.N", "confirm": "update"}: pvj-sysd starts a fixed update unit
        as root, for exactly the version the person confirmed."""
        if body.get("confirm") != "update":
            raise bad('send {"confirm": "update"}')
        source, version = body.get("source"), body.get("version")
        if source not in ("usb", "inbox"):
            raise bad("source must be usb or inbox")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version):
            raise bad("version must look like 1.2.3")
        if self._update_running():
            raise ApiError(409, "an update is already running")
        self._sysd({"cmd": "update", "source": source, "version": version})
        return {"started": source, "version": version}

    def update_upload(self, name, length, read, check=None):
        """Store an update bundle, its signature or its checksum in the inbox (the panel cannot install anything:
        pvj-update verifies the signature as root, on its own copy, when the update is started)."""
        m = self.UPDATE_NAME.fullmatch(name or "")
        if not m:
            raise bad("an update is named pvj-N.N.N.tar.gz (with .sig and .sha256 beside it)")
        limit = self.UPDATE_SIDE_MAX if m.group(2) else self.UPDATE_MAX
        if not isinstance(length, int) or length <= 0:
            raise ApiError(411, "Content-Length required")
        if length > limit:
            raise ApiError(413, "too large for an update file")
        if self._update_running():
            raise ApiError(409, "an update is running; upload again when it has finished")
        inbox = self._update_inbox()
        try:
            os.makedirs(inbox, mode=0o750, exist_ok=True)
            if not m.group(2) and length * 5 + FREE_SPACE_RESERVE > shutil.disk_usage(inbox).free:
                raise ApiError(507, "not enough free space to unpack and install this update")
        except OSError as e:
            raise ApiError(500, "cannot use the update folder: %s" % (e.strerror or e))
        if not self._upload_lock.acquire(blocking=False):
            raise ApiError(409, "another upload is in progress")
        tmp = None
        try:
            for old in os.listdir(inbox):
                om = self.UPDATE_NAME.fullmatch(old)
                stale = old.startswith(".upload-") and time.time() - os.lstat(os.path.join(inbox, old)).st_mtime > 3600
                if stale or (om and not m.group(2) and om.group(1) != m.group(1)):
                    os.unlink(os.path.join(inbox, old))     # a new bundle replaces any other; crashed uploads go
            fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=inbox)
            got = 0
            with os.fdopen(fd, "wb") as out:
                while got < length:
                    if check:
                        check()
                    chunk = read(min(CHUNK, length - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            if got != length:
                raise ApiError(400, "upload was cut short")
            os.chmod(tmp, 0o640)
            os.replace(tmp, os.path.join(inbox, name))
            tmp = None
            return {"name": name, "size": length}
        except (TimeoutError, ConnectionError):
            raise ApiError(400, "upload interrupted")
        except OSError as e:
            raise ApiError(500, "could not store the file: %s" % (e.strerror or e))
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            self._upload_lock.release()

    def reboot(self, body, device, client):
        if body.get("confirm") != "reboot":
            raise bad('send {"confirm": "reboot"}')
        self._sysd({"cmd": "reboot"})
        return {"rebooting": True}

    def poweroff(self, body, device, client):
        if body.get("confirm") != "poweroff":
            raise bad('send {"confirm": "poweroff"}')
        self._sysd({"cmd": "poweroff"})
        return {"powering_off": True}

    def set_clock(self, body, device, client):
        """Set the box clock from the phone's (only while it is not set from the network)."""
        epoch = body.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise bad("epoch must be whole seconds since 1970")
        self._sysd({"cmd": "set_time", "epoch": epoch})
        return self.clock_status()

    def _mpv_version(self):
        if not hasattr(self, "_mpv_ver"):
            try:
                r = subprocess.run([getattr(self.player, "mpv_bin", "mpv"), "--version"], capture_output=True, text=True, timeout=10)
                self._mpv_ver = (r.stdout.splitlines() or [""])[0].split(" Copyright")[0].strip()
            except (OSError, subprocess.TimeoutExpired):
                self._mpv_ver = ""
        return self._mpv_ver

    def test_pattern(self, body, device, client):
        """Show colour bars (for lining up a projector), or stop them. They come from the player itself, no file."""
        on = body.get("on")
        if not isinstance(on, bool):
            raise bad("on must be true or false")
        if not on:
            self._player_call(self.player.clear)
            self._stop_capture()
            return {"test_pattern": False}
        self.fader.cancel()
        self._player_call(self.player.play, [self.player.TEST_PATTERN], True, None, False, self.spawn)
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        self._started_playing()
        return {"test_pattern": True}

    def set_mix(self, body, device, client):
        mode, duration = body.get("transition"), body.get("duration")
        if mode not in ("cut", "dip"):
            raise bad("transition must be cut or dip (crossfade is not built yet)")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.1 <= duration <= 10:
            raise bad("duration must be 0.1 to 10 seconds")
        with self.settings.lock:
            self.settings.data["mix"] = {"transition": mode, "duration": float(duration)}
            self.settings.save()
        return self.settings.data["mix"]

    def stop_player(self, body, device, client):
        # The systemd unit (Restart=always) brings the player straight back.
        self._player_call(self.player.ipc.request, "quit")
        self._stop_capture()
        return {"ok": True}

    def get_modules(self, body, device, client):
        return {"modules": self.registry.list()}

    def set_module(self, module_id, body, device, client):
        if not _MODULE_ID.match(module_id):
            raise ApiError(404, "unknown module")
        try:
            self.registry.set_enabled(module_id, body.get("enabled"))
        except ModuleError as e:
            raise ApiError(409, str(e))
        if module_id == "mapper":          # switching it off takes the mapping off the screen
            self.mapper.apply()
        if module_id == "wall":            # starts or stops following or leading, and the wall crop
            self.sync.apply()
        if module_id == "projector":       # starts or stops the background status checks
            self.projectors.apply()
        for mid, manager in (("control-dmx", self.dmx), ("control-midi", self.midi)):
            if module_id == mid and manager is not None:   # switching the module off stops the receiver
                try:
                    manager.apply()
                except Exception as e:
                    print("pvj-web: %s: %s" % (mid, e))
        return {"modules": self.registry.list()}

    def get_theme(self, body, device, client):
        t = self.settings.data["theme"]
        return {"theme": t, "available": [{"id": k, "name": v["name"], "source": v["source"]}
                                          for k, v in self.themes.items()]}

    def set_theme(self, body, device, client):
        name, accent = body.get("name"), body.get("accent")
        if name not in self.themes:
            raise bad("unknown theme")
        try:
            themes_mod.css(self.themes[name], accent)
        except ThemeError as e:
            raise bad(str(e))
        with self.settings.lock:
            self.settings.data["theme"] = {"name": name, "accent": accent}
            self.settings.save()
        return {"theme": self.settings.data["theme"]}

    def theme_css(self):
        t = self.settings.data["theme"]
        theme = self.themes.get(t["name"]) or self.themes["dark-stage"]
        try:
            return themes_mod.css(theme, t.get("accent"))
        except ThemeError:
            return themes_mod.css(theme)

    def devices(self, body, device, client):
        return {"devices": self.auth.list_devices()}

    def invite(self, body, device, client):
        try:
            token, dev = self.auth.invite(str(body.get("name", "guest"))[:40], body.get("role"))
        except AuthError as e:
            raise bad(str(e))
        out = {"device": dev, "token": token, "note": "Share this token once; it is not shown again."}
        origin = body.get("origin")
        if isinstance(origin, str) and re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]{1,100}", origin):
            from . import qr as qr_mod
            out["qr_svg"] = qr_mod.svg(qr_mod.encode("%s/#token=%s" % (origin, token)))
        return out

    def revoke(self, body, device, client):
        did = body.get("id")
        if not isinstance(did, str) or not self.auth.revoke(did):
            raise ApiError(404, "no such device")
        return {"ok": True}

    def unlock_pairing(self, body, device, client):
        """Lift a guessing lockout without changing the PIN, so waiting guests can join with their codes."""
        self.auth.clear_lockout()
        return {"ok": True}

    def rotate_pin(self, body, device, client):
        pin = self.auth.rotate_pin()
        if self.on_pin:
            self.on_pin(pin)
        return {"ok": True, "pin": pin}

    def get_osc(self, body, device, client):
        if self.osc is None:
            raise ApiError(404, "OSC is not available")
        return self.osc.status()

    def set_osc(self, body, device, client):
        if self.osc is None:
            raise ApiError(404, "OSC is not available")
        cfg = self.settings.data["osc"]
        new = dict(cfg)
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise bad("enabled must be true or false")
            new["enabled"] = body["enabled"]
        if "port" in body:
            new["port"] = number(body, "port", 1024, 65535, integer=True)
        if "allow" in body:
            try:
                new["allow"] = osc_mod.validate_allow(body["allow"])
            except osc_mod.OscError as e:
                raise bad(str(e))
        with self.settings.lock:
            self.settings.data["osc"] = new
        try:
            self.osc.apply()
        except osc_mod.OscError as e:
            with self.settings.lock:
                self.settings.data["osc"] = cfg  # keep the last working configuration
            try:
                self.osc.apply()
            except osc_mod.OscError:
                pass
            raise ApiError(409, str(e))
        self.settings.save()
        return self.osc.status()

    # --- a picture over the video ---------------------------------------------------
    def apply_overlay(self):
        """Make the screen match settings["overlay"]. Called when it changes and when the player restarts (a restart
        loses what was drawn). Returns the state. Raises ApiError when the picture cannot be shown."""
        from . import overlay as overlay_mod
        cfg = self.settings.data["overlay"]
        player = self.player
        try:
            player.overlay_remove(overlay_mod.OVERLAY_ID)
        except PlayerError:
            pass
        if not cfg["on"] or not cfg["file"]:
            return self.overlay_state()
        src = self.resolve_media(cfg["file"])
        size = self._player_call(player.osd_size)
        if size is None:
            raise ApiError(503, "the player has no screen size yet")
        out = os.path.join(player.rundir, "overlay.bgra")
        try:
            overlay_mod.convert(src, size[0], size[1], out, getattr(player, "mpv_bin", "mpv"))
        except overlay_mod.OverlayError as e:
            raise ApiError(409, str(e))
        self._player_call(player.overlay_file, overlay_mod.OVERLAY_ID, out, size[0], size[1])
        return self.overlay_state()

    def overlay_state(self):
        cfg = self.settings.data["overlay"]
        return {"file": cfg["file"], "on": cfg["on"],
                "choices": [n for n in self.media_list() if n.lower().endswith(".png")]}

    def get_overlay(self, body, device, client):
        return self.overlay_state()

    def set_overlay(self, body, device, client):
        """{"on": bool, "file": "logo.png"}: a PNG from the media folder over the video, fitted to the screen."""
        cfg = dict(self.settings.data["overlay"])
        if "file" in body:
            f = body["file"]
            if f != "" and (not valid_name(f) or not f.lower().endswith(".png")):
                raise bad("choose a PNG picture from the media folder")
            cfg["file"] = f
        if "on" in body:
            if not isinstance(body["on"], bool):
                raise bad("on must be true or false")
            cfg["on"] = body["on"]
        if cfg["on"] and not cfg["file"]:
            raise bad("choose a picture first")
        previous = self.settings.data["overlay"]
        with self.settings.lock:
            self.settings.data["overlay"] = cfg
        try:
            state = self.apply_overlay()
        except ApiError:
            with self.settings.lock:
                self.settings.data["overlay"] = previous
            raise
        with self.settings.lock:
            self.settings.save()
        return state

    def get_health(self, body, device, client):
        return self.health.report()

    # --- remote support (see support.py) --------------------------------------------------
    def _support_clash_networks(self):
        """The IPv4 networks this box is on (not the support tunnel itself), for refusing an overlapping support network."""
        import ipaddress
        out = []
        try:
            for entry in self._ip_json():
                if entry.get("ifname") in ("lo", "wg-pvj"):
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("local"):
                        out.append(ipaddress.ip_network("%s/%s" % (a["local"], a.get("prefixlen", 32)), strict=False))
        except Exception:
            pass
        return out

    def _support(self, fn, body, device, client):
        from . import support as support_mod
        try:
            return fn(body, device, client)
        except support_mod.SupportApiError as e:
            raise ApiError(e.status, e.message)

    def get_support(self, body, device, client):
        return self._support(lambda b, d, c: self.support.status(d, c), body, device, client)

    def set_support(self, body, device, client):
        return self._support(self.support.set_config, body, device, client)

    def start_support(self, body, device, client):
        return self._support(self.support.start, body, device, client)

    def extend_support(self, body, device, client):
        return self._support(self.support.extend, body, device, client)

    def stop_support(self, body, device, client):
        return self._support(self.support.stop, body, device, client)

    def support_login(self, body, device, client):
        return self._support(lambda b, d, c: self.support.login(b, c), body, device, client)

    # --- multi-box sync and the video wall (see sync.py) ------------------------------------
    def get_sync(self, body, device, client):
        return self.sync.status()

    def set_sync(self, body, device, client):
        from . import sync as sync_mod
        if not self.registry.enabled("wall"):
            raise ApiError(409, "turn on the Video wall and sync module in System first")
        with self.settings.lock:
            try:
                cfg = sync_mod.validate(body, self.settings.data["sync"])
            except sync_mod.SyncError as e:
                raise bad(str(e))
            self.settings.data["sync"] = cfg
            self.settings.save()
        self.sync.apply()
        return self.sync.status()

    # --- projection mapping ------------------------------------------------------------
    def get_mapper(self, body, device, client):
        return self.mapper.state()

    def set_mapper(self, body, device, client):
        from . import mapper as mapper_mod
        if not self.registry.enabled("mapper"):
            raise ApiError(409, "turn on the Projection mapper module in System first")
        try:
            return self.mapper.handle(body)
        except mapper_mod.MapperError as e:
            raise bad(str(e))

    def apply_mapper(self):
        """Put the mapping back on a player that restarted (it lost its shaders)."""
        self.mapper.apply()

    # --- projectors (PJLink) -------------------------------------------------------
    def _pjlink(self, entry):
        from . import projector as projector_mod
        return projector_mod.PJLink(entry["host"], entry["port"], entry["password"])

    def log(self, line):
        print(line, flush=True)       # to the journal, like every other line of the panel service

    def _need_projectors(self):
        if not self.registry.enabled("projector"):
            raise ApiError(409, "turn on the Projector control module in System first")

    def get_projectors(self, body, device, client):
        """The projectors, without their passwords: what each said it is, its inputs with their labels, and the
        last status the background check got (nothing is asked here)."""
        from . import projector as projector_mod
        enabled = self.registry.enabled("projector")

        def one(p):
            details, labels = p.get("details") or None, p.get("labels") or {}
            return {"id": p["id"], "name": p["name"], "host": p["host"], "port": p["port"], "has_password": bool(p["password"]),
                    "details": details,
                    "inputs": [{"code": c, "name": projector_mod.input_name(c), "label": labels.get(c, "")}
                               for c in (details or {}).get("inputs") or []],
                    "status": self.projectors.status(p["id"]) if enabled else None}
        return {"enabled": enabled, "projectors": [one(p) for p in self.settings.data["projectors"]]}

    def set_projectors(self, body, device, client):
        """{"add": {name, host, port, password}}, {"remove": id} or {"label": {"id", "input", "label"}}."""
        from . import projector as projector_mod
        self._need_projectors()
        entry = None
        if "add" in body:
            try:
                entry = projector_mod.validate(body["add"])
                addr = projector_mod.private_address(entry["host"])      # refuse a public address now; a name lookup is slow, so outside the lock
            except projector_mod.ProjectorError as e:
                raise bad(str(e))
            for p in list(self.settings.data["projectors"]):      # the same device under another spelling is one projector, not two
                if p["port"] != entry["port"]:
                    continue
                try:
                    same = projector_mod.private_address(p["host"]) == addr
                except projector_mod.ProjectorError:
                    same = False
                if same or p["host"].lower() == entry["host"].lower():
                    raise ApiError(409, "that projector is already in the list as %s" % p["name"])
        with self.settings.lock:
            items = list(self.settings.data["projectors"])
            try:
                if entry is not None:
                    if len(items) >= projector_mod.MAX_PROJECTORS:
                        raise bad("at most %d projectors" % projector_mod.MAX_PROJECTORS)
                    items.append(entry)
                elif "remove" in body:
                    if not any(p["id"] == body["remove"] for p in items):
                        raise ApiError(404, "no such projector")
                    items = [p for p in items if p["id"] != body["remove"]]
                elif "label" in body:
                    want = body["label"]
                    if not isinstance(want, dict) or not isinstance(want.get("id"), str):
                        raise bad("label must be {id, input, label}")
                    at = [i for i, p in enumerate(items) if p["id"] == want.get("id")]
                    if not at:
                        raise ApiError(404, "no such projector")
                    old = items[at[0]]
                    label = projector_mod.validate_label((old.get("details") or {}).get("inputs") or [], want.get("input"), want.get("label"))
                    labels = dict(old.get("labels") or {})
                    labels.pop(want["input"], None)
                    if label:
                        labels[want["input"]] = label
                    items[at[0]] = dict(old, labels=labels)
                else:
                    raise bad("send add, remove or label")
            except projector_mod.ProjectorError as e:
                raise bad(str(e))
            self.settings.data["projectors"] = items
            self.settings.save()
        self.projectors.apply()           # a new projector is identified and checked in the background; a removed one is let go
        return self.get_projectors({}, device, client)

    def projector_action(self, body, device, client):
        """{"id": projector id or "all", "action": one of PROJECTOR_ACTIONS, "background"?: bool}.
        "mute" and "unmute" are picture and sound together; "input" needs one projector and {"input": "31"}, a
        code from that projector's own list; "identify" reads the projector's details again.
        With "background" the commands are sent from a thread of their own and the answer is {"started": true}; OSC
        uses that, so an unplugged projector never delays the next cue."""
        self._need_projectors()
        action, pid = body.get("action"), body.get("id", "all")
        if action not in self.PROJECTOR_ACTIONS:
            raise bad("action must be one of: " + ", ".join(self.PROJECTOR_ACTIONS))
        if not isinstance(body.get("background", False), bool):
            raise bad("background must be true or false")
        targets = [p for p in self.settings.data["projectors"] if pid == "all" or p["id"] == pid]
        if not targets:
            raise ApiError(404, "no such projector" if pid != "all" else "no projectors added")
        if action == "input":
            if pid == "all":
                raise bad("choose one projector to switch its input")
            known = (targets[0].get("details") or {}).get("inputs") or []
            if not known:
                raise ApiError(409, "the projector's inputs are not known yet; use Refresh details while it is on")
            if not isinstance(body.get("input"), str) or body["input"] not in known:
                raise bad("that is not one of this projector's inputs")
            action = ("input", body["input"])
        if body.get("background"):
            def run():
                failed = [r["error"] for r in self._projector_run(targets, action).values() if not r["ok"]]
                if failed:
                    self.log("pvj-web: projector %s (from %s): %s" % (body["action"], client, "; ".join(failed)))
            threading.Thread(target=run, name="projector", daemon=True).start()
            return {"started": True}
        results = self._projector_run(targets, action)
        if len(targets) == 1 and not results[targets[0]["id"]]["ok"]:
            raise ApiError(502, results[targets[0]["id"]]["error"])
        return {"results": results}

    PROJECTOR_WAIT = 30.0     # a name lookup is the only unbounded step; nobody waits longer than this
    PROJECTOR_ACTIONS = ("on", "off", "mute", "unmute", "mute_picture", "unmute_picture", "mute_sound", "unmute_sound",
                         "input", "identify", "state")

    def _projector_run(self, targets, action):
        """Send `action` to every target at once; {id: {"ok", "power"?, "pending"?, "error"?}}."""
        from . import projector as projector_mod
        results = {}

        def one(p):
            link = self._pjlink(p)
            try:
                if action in ("on", "off"):
                    link.power(action == "on")
                    results[p["id"]] = {"ok": True}
                elif isinstance(action, tuple):                 # ("input", code); "pending": it is being retried
                    results[p["id"]] = dict(self.projectors.set_input(p, action[1]), ok=True)
                elif action == "identify":
                    self.projectors.identify(p)
                    results[p["id"]] = {"ok": True}
                elif action != "state":
                    verb, _, what = action.partition("_")
                    link.mute(verb == "mute", what or "both")
                    results[p["id"]] = {"ok": True}
                else:
                    results[p["id"]] = {"ok": True, "power": link.state()}
            except projector_mod.ProjectorError as e:
                results[p["id"]] = {"ok": False, "error": str(e)}
            except Exception as e:      # never leave a projector without an answer
                results[p["id"]] = {"ok": False, "error": "error: %s" % e}
            finally:
                self.projectors.poke(p["id"])                   # the panel's status follows at once, after a failure too
        # All at once: eight projectors that are off the network cost one timeout, not eight.
        workers = [threading.Thread(target=one, args=(p,), daemon=True) for p in targets]
        for w in workers:
            w.start()
        end = time.monotonic() + self.PROJECTOR_WAIT
        for w in workers:
            w.join(max(0, end - time.monotonic()))
        return {p["id"]: dict(results.get(p["id"]) or {"ok": False, "error": "the projector did not answer in time"}) for p in targets}

    # --- audio output --------------------------------------------------
    def _audio_devices(self):
        """mpv's list of sound outputs, [{"name", "description"}]; raises ApiError 503 if the player is down."""
        lst = self._player_call(self.player.ipc.request, "get_property", "audio-device-list")
        return [{"name": d["name"], "description": str(d.get("description", ""))[:120]}
                for d in (lst or []) if isinstance(d, dict) and isinstance(d.get("name"), str)]

    def _auto_audio(self, names):
        """The sound output "automatic" means: on a Pi, HDMI on the connected port (the picture and the sound go to
        the same screen); otherwise mpv's own choice, which on a Pi is the headphone jack (found on a real Pi 4)."""
        for c in hardware.drm_connectors():
            m = re.fullmatch(r"HDMI-A-([0-9])", c["connector"])
            if m and c["status"] == "connected":
                want = "alsa/sysdefault:CARD=vc4hdmi%d" % (int(m.group(1)) - 1)
                if want in names:
                    return want
        return "auto"

    def _started_playing(self, capture=False):
        """Something is about to be on screen: take any on-screen pairing PIN off it at once, and stop a live input
        that is no longer shown (its helper must not keep the device busy)."""
        if self.pinscreen is not None:
            self.pinscreen.clear()
        if not capture:
            self._stop_capture()

    def play_capture(self, body):
        """{"capture": {"device": "video0", "mode": "720p30"}}: a live input, read by a separate helper process."""
        from . import capture as capture_mod
        if self.capture is None:
            raise ApiError(404, "live inputs are not available")
        spec = body.get("capture")
        if not isinstance(spec, dict):
            raise bad("capture must be an object")
        mode = spec.get("mode", "720p30")
        with self.capture.lock:            # prepare, load and start as one step: a double tap cannot leak a helper
            try:
                w, h, fps = self.capture.prepare(spec.get("device"), mode)
            except capture_mod.CaptureError as e:
                raise bad(str(e))
            self.fader.cancel()
            try:
                self._player_call(self.player.play_pipe, self.capture.fifo, w, h, fps)
            except ApiError:
                self.capture.stop()
                raise
            try:
                self.capture.start(spec["device"], mode)
            except capture_mod.CaptureError as e:
                raise ApiError(409, str(e))
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        self._started_playing(capture=True)
        return {"playing": "capture", "device": spec["device"], "mode": mode}

    def _stop_capture(self):
        if self.capture is not None:
            with self.capture.lock:
                self.capture.stop()

    def get_inputs(self, body, device, client):
        if self.capture is None:
            return {"running": False, "current": None, "devices": [], "modes": []}
        return self.capture.status()

    def ensure_audio(self):
        """Keep the player on the right sound output while the choice is Automatic or the saved output is missing:
        a screen switched on after the Pi booted, or a USB sound device plugged in late, must not leave the sound on the
        headphone jack until the player restarts. Cheap (one property read); does nothing when already right."""
        try:
            names = {d["name"] for d in self._audio_devices()}
            chosen = self.settings.data["audio"]["device"]
            target = chosen if chosen != "auto" and chosen in names else self._auto_audio(names)
            if self.player.ipc.request("get_property", "audio-device") != target:
                self.player.ipc.request("set_property", "audio-device", target)
            return target
        except (ApiError, PlayerError):
            return None

    def apply_audio(self):
        """Point the player at the chosen output. Called when the setting changes and whenever the player restarts."""
        names = {d["name"] for d in self._audio_devices()}
        chosen = self.settings.data["audio"]["device"]
        target = chosen if chosen != "auto" and chosen in names else self._auto_audio(names)
        self._player_call(self.player.ipc.request, "set_property", "audio-device", target)
        return target

    def get_audio(self, body, device, client):
        devices = self._audio_devices()
        names = {d["name"] for d in devices}
        return {"device": self.settings.data["audio"]["device"], "devices": devices, "automatic_is": self._auto_audio(names)}

    def set_audio(self, body, device, client):
        chosen = body.get("device")
        names = {d["name"] for d in self._audio_devices()}
        if not isinstance(chosen, str) or (chosen != "auto" and chosen not in names):
            raise bad("choose one of the listed sound outputs")
        with self.settings.lock:
            self.settings.data["audio"]["device"] = chosen
            self.settings.save()
        target = self.apply_audio()
        return dict(self.get_audio({}, device, client), in_use=target)

    # --- autostart -----------------------------------------------------
    def get_autostart(self, body, device, client):
        if self.autostart is None:
            raise ApiError(404, "autostart is not available")
        return self.autostart.status()

    def set_autostart(self, body, device, client):
        from . import autostart as autostart_mod   # imported here: autostart itself imports this module
        if self.autostart is None:
            raise ApiError(404, "autostart is not available")
        with self.settings.lock:
            try:
                new = autostart_mod.validate(body, self.settings.data["autostart"])
            except autostart_mod.AutostartError as e:
                raise bad(str(e))
            if new["mode"] == "pad":           # a pad that exists and has a clip, or it would fail at every start
                banks = self.settings.data["pads"]["banks"]
                b, i = new["pad"]
                if not (b < len(banks) and i < 12 and banks[b]["pads"][i].get("file")):
                    raise bad("choose a pad that has a clip")
            self.settings.data["autostart"] = new
            self.settings.save()
        return self.autostart.status()

    def test_autostart(self, body, device, client):
        """Run it now (what a reboot would do), to check it without restarting the box."""
        if self.autostart is None:
            raise ApiError(404, "autostart is not available")
        message = self.autostart.run_now()
        return dict(self.autostart.status(), message=message)

    # --- join codes and access on the display --------------------------------
    def _access_state(self):
        return {"codes": self.auth.list_joins(), "screen": self.pinscreen.status() if self.pinscreen else {"showing": False, "items": [], "seconds_left": 0},
                "screen_available": self.pinscreen is not None}

    def get_access(self, body, device, client):
        return self._access_state()

    def make_join_code(self, body, device, client):
        try:
            self.auth.create_join(body.get("role"), body.get("minutes", auth_mod.JOIN_DEFAULT_MINUTES), body.get("uses", auth_mod.JOIN_DEFAULT_USES))
        except AuthError as e:
            raise bad(str(e))
        return self._access_state()

    def cancel_join_code(self, body, device, client):
        if body.get("all") is True:
            self.auth.cancel_join(None)
        elif not isinstance(body.get("code"), str) or not self.auth.cancel_join(body["code"]):
            raise ApiError(404, "no such code")
        return self._access_state()

    def access_qr(self, target, origin):
        """An SVG QR code for the panel address ("panel", no access in it) or for a live join code ("view", "live").
        The address comes from the Host the browser used, so the code works from the same network as the viewer."""
        from . import qr as qr_mod
        if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,100}", origin or ""):
            raise bad("unknown address")
        base = "http://%s/" % origin
        if target == "panel":
            text = base
        elif target in ("view", "live"):
            codes = {j["role"]: j["code"] for j in self.auth.list_joins()}
            if target not in codes:
                raise ApiError(404, "make a %s code first" % ("guest" if target == "view" else "presenter"))
            text = "%s#code=%s" % (base, codes[target])
        else:
            raise bad("unknown QR code")
        return qr_mod.svg(qr_mod.encode(text)).encode()

    def show_access(self, body, device, client):
        """Put the PIN and/or the guest and presenter codes on the display for a while, or take them off."""
        if self.pinscreen is None:
            raise ApiError(404, "the on-screen display is not available")
        show = body.get("show")
        if not isinstance(show, bool):
            raise bad("show must be true or false")
        if not show:
            self.pinscreen.hide()
            return self._access_state()
        try:
            self.pinscreen.show(body.get("items"), body.get("seconds", 60))
        except (ValueError, AuthError) as e:
            raise bad(str(e))
        return self._access_state()

    # --- DMX and MIDI input --------------------------------------------
    def _need_control(self, module, manager):
        if manager is None or not self.registry.enabled(module):
            raise ApiError(409, "turn on the %s module in System first" % ("DMX" if module == "control-dmx" else "MIDI"))

    def _set_control(self, key, module, manager, validate, error_type, body):
        self._need_control(module, manager)
        with self._control_lock:      # one change at a time; the settings lock is NOT held while a receiver starts or stops,
            with self.settings.lock:  # because stopping joins threads that may need that lock
                current = self.settings.data["control"][key]
                try:
                    new = validate(body, current)
                except error_type as e:
                    raise bad(str(e))
                self.settings.data["control"][key] = new
            try:
                manager.apply()
            except Exception as e:
                with self.settings.lock:
                    self.settings.data["control"][key] = current   # keep the last working configuration
                try:
                    manager.apply()
                except Exception:
                    pass
                raise ApiError(409, str(e))
            with self.settings.lock:
                self.settings.save()
            return manager.status()

    def get_dmx(self, body, device, client):
        self._need_control("control-dmx", self.dmx)
        return self.dmx.status()

    def set_dmx(self, body, device, client):
        return self._set_control("dmx", "control-dmx", self.dmx, dmx_mod.validate, dmx_mod.DmxError, body)

    def get_midi(self, body, device, client):
        self._need_control("control-midi", self.midi)
        return self.midi.status()

    def set_midi(self, body, device, client):
        return self._set_control("midi", "control-midi", self.midi, midi_mod.validate, midi_mod.MidiError, body)

    def midi_learn(self, body, device, client):
        """Start (or cancel) waiting for the next control the owner moves or presses on any controller."""
        self._need_control("control-midi", self.midi)
        if not self.settings.data["control"]["midi"]["enabled"]:
            raise ApiError(409, "turn MIDI on first")
        start = body.get("start")
        if not isinstance(start, bool):
            raise bad("start must be true or false")
        (self.midi.start_learn if start else self.midi.cancel_learn)()
        return self.midi.status()

    def midi_map(self, body, device, client):
        """Add, remove or clear mappings. {"add": {kind, number, channel, source, action, ...}}, {"remove": id}, {"clear": true}."""
        self._need_control("control-midi", self.midi)
        with self.settings.lock:
            current = list(self.settings.data["control"]["midi"]["map"])
            try:
                if "add" in body:
                    changed = midi_mod.add_entry(current, body["add"])
                elif "remove" in body:
                    if not any(e["id"] == body["remove"] for e in current):
                        raise ApiError(404, "no such mapping")
                    changed = [e for e in current if e["id"] != body["remove"]]
                elif body.get("clear") is True:
                    changed = []
                else:
                    raise bad("send add, remove or clear")
            except midi_mod.MidiError as e:
                raise bad(str(e))
            self.settings.data["control"]["midi"]["map"] = changed
            try:
                self.settings.save()
            except Exception:
                self.settings.data["control"]["midi"]["map"] = current    # memory and disk must not disagree
                raise
        self.midi.cancel_learn()
        return self.midi.status()

    # --- schedule ------------------------------------------------------
    def _need_scheduler(self):
        if self.scheduler is None or not self.registry.enabled("scheduler"):
            raise ApiError(409, "turn on the Scheduler module in System first")

    def get_schedule(self, body, device, client):
        self._need_scheduler()
        return self.scheduler.status()

    def set_schedule(self, body, device, client):
        from . import scheduler as scheduler_mod
        self._need_scheduler()
        try:
            clean = scheduler_mod.validate(body)
        except scheduler_mod.ScheduleError as e:
            raise bad(str(e))
        with self.settings.lock:
            self.settings.data["schedule"] = clean
            self.settings.save()
        return self.scheduler.status()

    # --- network (wired) -----------------------------------------------
    @staticmethod
    def _run_ip():
        try:
            r = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, timeout=5)
            return json.loads(r.stdout) if r.returncode == 0 else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []

    def _need_network_module(self):
        if not self.registry.enabled("network"):
            raise ApiError(409, "turn on the Network module in System first")

    def _netd(self, message):
        if self.net is None:
            raise ApiError(503, "network settings are not available on this system")
        try:
            reply = self.net.request(message)
        except netcfg.NetError as e:
            raise ApiError(503, str(e))
        if not reply.get("ok"):
            raise ApiError(409, reply.get("error", "the network helper refused"))
        return reply

    def _local_status(self):
        addrs = {}
        for entry in self._ip_json():
            addrs[entry.get("ifname")] = ["%s/%s" % (a.get("local"), a.get("prefixlen")) for a in entry.get("addr_info", [])
                                          if a.get("family") == "inet"]
        interfaces = netcfg.list_interfaces(self._sysfs)
        for i in interfaces:
            i["addresses"] = addrs.get(i["name"], [])
        return interfaces

    def get_network(self, body, device, client):
        self._need_network_module()
        out = {"interfaces": self._local_status(), "pending": None, "helper": False, "modes": list(netcfg.MODES)}
        if self.net is not None:
            try:
                reply = self.net.request({"cmd": "status"})
                if reply.get("ok"):
                    out["pending"], out["helper"] = reply.get("pending"), True
                    out["reverting"] = bool(reply.get("reverting"))
            except netcfg.NetError:
                pass
        return out

    def _networks_in_use(self):
        import ipaddress
        out = []
        for entry in self._ip_json():
            if entry.get("ifname") == "lo":
                continue
            for a in entry.get("addr_info", []):
                try:
                    out.append((entry.get("ifname"), ipaddress.ip_network("%s/%s" % (a["local"], a["prefixlen"]), strict=False)))
                except (KeyError, ValueError):
                    pass
        return out

    def _checked_config(self, body):
        try:
            return netcfg.validate(body, netcfg.list_interfaces(self._sysfs), self._networks_in_use())
        except netcfg.NetError as e:
            raise bad(str(e))

    def plan_network(self, body, device, client):
        self._need_network_module()
        cfg = self._checked_config(body)
        if self.net is not None:
            try:
                reply = self.net.request({"cmd": "plan", "config": cfg})
                if reply.get("ok"):
                    return {"config": reply["config"], "commands": reply["commands"]}
            except netcfg.NetError:
                pass
        return {"config": cfg, "commands": netcfg.preview(netcfg.plan(cfg))}

    def apply_network(self, body, device, client):
        self._need_network_module()
        cfg = self._checked_config(body)  # the helper validates again; refusing early gives a clear 400
        reply = self._netd({"cmd": "apply", "config": cfg})
        return {"pending": reply.get("pending"), "config": cfg}

    def confirm_network(self, body, device, client):
        # not gated on the module: a pending change must always be confirmable or revertable
        return {"pending": self._netd({"cmd": "confirm"}).get("pending")}

    def revert_network(self, body, device, client):
        return {"pending": self._netd({"cmd": "revert"}).get("pending")}

    # --- routing -------------------------------------------------------
    def routes(self):
        # (method, path) -> (minimum role or None, handler)
        return {
            ("GET", "/api/hello"): (None, self.hello),
            ("POST", "/api/pair"): (None, self.pair),
            ("POST", "/api/session"): (None, self.session),
            ("GET", "/api/status"): ("view", self.status),
            ("GET", "/api/media"): ("view", self.media),
            ("GET", "/api/pads"): ("view", self.get_pads),
            ("GET", "/api/modules"): ("view", self.get_modules),
            ("GET", "/api/theme"): ("view", self.get_theme),
            ("GET", "/api/osc"): ("view", self.get_osc),
            ("POST", "/api/osc"): ("full", self.set_osc),
            ("POST", "/api/play"): ("live", self.play),
            ("POST", "/api/control"): ("live", self.control),
            ("POST", "/api/blackout"): ("live", self.blackout),
            ("POST", "/api/fadeout"): ("live", self.fadeout),
            ("POST", "/api/fadein"): ("live", self.fadein),
            ("POST", "/api/testpattern"): ("live", self.test_pattern),
            ("POST", "/api/testtone"): ("live", self.test_tone),
            ("POST", "/api/media/info"): ("view", self.media_info),
            ("POST", "/api/media/import"): ("full", self.import_usb),
            ("GET", "/api/media/import"): ("view", self.import_status),
            ("POST", "/api/media/import/cancel"): ("full", self.import_cancel),
            ("GET", "/api/system"): ("view", self.system_info),
            ("POST", "/api/system/reboot"): ("full", self.reboot),
            ("POST", "/api/system/poweroff"): ("full", self.poweroff),
            ("POST", "/api/system/clock"): ("full", self.set_clock),
            ("GET", "/api/system/update"): ("full", self.update_status),
            ("POST", "/api/system/update"): ("full", self.start_update),
            ("POST", "/api/mix"): ("live", self.set_mix),
            ("GET", "/api/access"): ("full", self.get_access),
            ("POST", "/api/access/code"): ("full", self.make_join_code),
            ("POST", "/api/access/cancel"): ("full", self.cancel_join_code),
            ("POST", "/api/access/screen"): ("full", self.show_access),
            ("GET", "/api/inputs"): ("view", self.get_inputs),
            ("GET", "/api/overlay"): ("view", self.get_overlay),
            ("POST", "/api/overlay"): ("live", self.set_overlay),
            ("GET", "/api/health"): ("view", self.get_health),
            ("GET", "/api/support"): ("view", self.get_support),
            ("POST", "/api/support/config"): ("full", self.set_support),
            ("POST", "/api/support/start"): ("full", self.start_support),
            ("POST", "/api/support/extend"): ("full", self.extend_support),
            ("POST", "/api/support/stop"): ("live", self.stop_support),
            ("POST", "/api/support/login"): (None, self.support_login),
            ("GET", "/api/sync"): ("view", self.get_sync),
            ("POST", "/api/sync"): ("full", self.set_sync),
            ("GET", "/api/mapper"): ("view", self.get_mapper),
            ("POST", "/api/mapper"): ("full", self.set_mapper),
            ("GET", "/api/projectors"): ("view", self.get_projectors),
            ("POST", "/api/projectors"): ("full", self.set_projectors),
            ("POST", "/api/projector"): ("live", self.projector_action),
            ("GET", "/api/audio"): ("view", self.get_audio),
            ("POST", "/api/audio"): ("full", self.set_audio),
            ("GET", "/api/autostart"): ("view", self.get_autostart),
            ("POST", "/api/autostart"): ("full", self.set_autostart),
            ("POST", "/api/autostart/test"): ("live", self.test_autostart),
            ("GET", "/api/dmx"): ("full", self.get_dmx),
            ("POST", "/api/dmx"): ("full", self.set_dmx),
            ("GET", "/api/midi"): ("full", self.get_midi),
            ("POST", "/api/midi"): ("full", self.set_midi),
            ("POST", "/api/midi/learn"): ("full", self.midi_learn),
            ("POST", "/api/midi/map"): ("full", self.midi_map),
            ("GET", "/api/streams"): ("view", self.get_streams),
            ("POST", "/api/streams"): ("full", self.set_streams),
            ("GET", "/api/schedule"): ("view", self.get_schedule),
            ("POST", "/api/schedule"): ("full", self.set_schedule),
            ("GET", "/api/network"): ("full", self.get_network),
            ("POST", "/api/network/plan"): ("full", self.plan_network),
            ("POST", "/api/network/apply"): ("full", self.apply_network),
            ("POST", "/api/network/confirm"): ("full", self.confirm_network),
            ("POST", "/api/network/revert"): ("full", self.revert_network),
            ("POST", "/api/media/delete"): ("full", self.delete_media),
            ("POST", "/api/media/rename"): ("full", self.rename_media),
            ("POST", "/api/pads"): ("full", self.set_pad),
            ("POST", "/api/theme"): ("full", self.set_theme),
            ("GET", "/api/devices"): ("full", self.devices),
            ("POST", "/api/devices/invite"): ("full", self.invite),
            ("POST", "/api/devices/revoke"): ("full", self.revoke),
            ("POST", "/api/pin/rotate"): ("full", self.rotate_pin),
            ("POST", "/api/pin/unlock"): ("full", self.unlock_pairing),
            ("POST", "/api/player/restart"): ("full", self.stop_player),
        }

    @staticmethod
    def require(device, role):
        if device is None:
            raise ApiError(401, "pair this device first")
        if not Auth.allows(device, role):
            raise ApiError(403, "this device may not do that (%s access needed)" % role)

    def handle(self, method, path, body, device, client):
        try:
            m = re.match(r"^/api/modules/([^/]+)$", path)
            if m and method == "POST":
                need, handler = "full", lambda b, d, c: self.set_module(m.group(1), b, d, c)
            else:
                route = self.routes().get((method, path))
                if route is None:
                    known = any(p == path for (_, p) in self.routes()) or bool(m)
                    raise ApiError(405 if known else 404, "method not allowed" if known else "not found")
                need, handler = route
            from . import support as support_mod
            try:                      # requests through the support tunnel: only support's login, never some things
                self.support.guard(method, path, device, client)
            except support_mod.SupportApiError as e:
                raise ApiError(e.status, e.message)
            if need is not None:
                if device is None:
                    raise ApiError(401, "pair this device first")
                if not Auth.allows(device, need):
                    raise ApiError(403, "this device may not do that (%s access needed)" % need)
            return 200, handler(body if isinstance(body, dict) else {}, device, client)
        except ApiError as e:
            payload = {"error": e.message}
            if e.retry_after:
                payload["retry_after"] = e.retry_after
            return e.status, payload
