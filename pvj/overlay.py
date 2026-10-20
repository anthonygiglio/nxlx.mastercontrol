# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pictures over the video: a logo, a watermark or masks (the old panel's overlay.png). D100: a list of masks.

Each picture is a PNG from the media folder, with transparency. Up to MAX_MASKS of them are in a list, each with its
own switch, in an order; the whole overlay has its switch too. The masks that are on are combined, in the list's
order (a later one drawn over an earlier one, normal alpha "over"), into ONE raw premultiplied BGRA picture at the
size of the screen (each fitted and centred, the rest transparent), which the player draws over the video and the
on-screen text as overlay OVERLAY_ID. One overlay costs the GPU the same however many masks went into it (measured
on the node with an Epson X27 at 1024 x 768, 2026-10-19: one mask, two overlays and two combined all dropped 0 frames).

mpv itself does the work, so nothing needs installing (the node has mpv, but no ffmpeg program and no numpy): one
mask goes through mpv's `--vf` as before; several go in as one `--lavfi-complex` graph, the first as the file and the
others as `--external-file`, each scaled, padded, premultiplied, and laid over the one before with
`overlay=alpha=premultiplied`. Decoding and scaling full-screen PNGs in Python would take many seconds on a Pi.

Only plain file names from the media folder reach the converter, as an argument list (never a shell), with
`--no-config` so no user configuration or scripts are loaded. The work runs in a thread of its own (`Masks`), the
newest change wins, and a build that a newer change overtook is stopped (mpv is killed).

Settings (no schema change, read with defaults everywhere): "overlay": {"on", "file", "masks": [{"file", "on"}]}.
"file" is kept as the first mask that is on, so the release before D100, which knows only "file", still shows it.
"""

import os
import re
import subprocess
import tempfile
import threading
import time

from . import locks

OVERLAY_ID = 10                 # pvj/transitions.py's still is 63; the pairing codes are 1 to 4 (pinscreen.QR_IDS)
MAX_SIDE = 4096                 # one picture: what the single-mask path took before D100
MAX_MASKS = 8
MAX_WIDTH, MAX_HEIGHT = 3840, 2160      # several masks: the combined picture is at most this
FILE_RE = r"masks-(\d+)-(\d+)\.bgra(\.tmp)?"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TIMEOUT_ONE, TIMEOUT_EACH = 30, 10      # seconds for mpv: one picture, and more for each further mask


class OverlayError(Exception):
    pass


class Cancelled(OverlayError):
    """A newer change overtook this build; nothing was written."""


# ---- the settings ------------------------------------------------------------------------------------------------
def _entry(m):
    """A mask entry as stored, or None for something that is not one (a damaged file is read, never trusted)."""
    if not isinstance(m, dict) or not isinstance(m.get("file"), str) or not m["file"]:
        return None
    return {"file": m["file"], "on": m.get("on", True) is True}


def masks_of(cfg):
    """The list of masks of the "overlay" section, oldest first: [{"file", "on"}], at most MAX_MASKS, no name twice.

    A section without "masks" (the release before D100 wrote it) is its one picture, on. A section whose "file" is not
    the first mask that is on was changed by that older release after a rollback (it knows only "file"): its picture
    becomes the first mask, on, so what it chose is what shows."""
    cfg = cfg if isinstance(cfg, dict) else {}
    legacy = cfg.get("file") if isinstance(cfg.get("file"), str) else ""
    raw = cfg.get("masks")
    if not isinstance(raw, list):
        return [{"file": legacy, "on": True}] if legacy else []
    out, seen = [], set()
    for m in raw:
        e = _entry(m)
        if e is not None and e["file"] not in seen:
            seen.add(e["file"])
            out.append(e)
    if legacy and legacy != first_on(out):
        out = [m for m in out if m["file"] != legacy]
        out.insert(0, {"file": legacy, "on": True})
    return out[:MAX_MASKS]


def first_on(masks):
    return next((m["file"] for m in masks if m["on"]), "")


def stored(masks, on):
    """The "overlay" section for `masks` and the whole switch `on`."""
    masks = [{"file": m["file"], "on": bool(m["on"])} for m in masks]
    return {"file": first_on(masks), "on": bool(on), "masks": masks}


def showing(cfg):
    """True when the settings put something over the video: the overlay on and a mask in it on."""
    try:
        return bool(cfg.get("on")) and any(m["on"] for m in masks_of(cfg))
    except Exception:
        return True


def is_png(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except OSError:
        return False


# ---- turning PNGs into one raw picture -----------------------------------------------------------------------------
def _fit(size):
    return ("scale=%s:force_original_aspect_ratio=decrease,pad=%s:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            "format=rgba,premultiply=inplace=1" % (size, size))


def graph(count, width, height):
    """The lavfi graph that lays `count` pictures (count >= 2), fitted to width x height, over each other in order."""
    size = "%d:%d" % (width, height)
    parts = ["[vid%d]%s[m%d]" % (i + 1, _fit(size), i) for i in range(count)]
    below = "m0"
    for i in range(1, count):
        last = i == count - 1
        parts.append("[%s][m%d]overlay=alpha=premultiplied:format=rgb%s[%s]"
                     % (below, i, ",format=bgra" if last else "", "vo" if last else "o%d" % i))
        below = "o%d" % i
    return ";".join(parts)


def _run(args, tmp, out_path, size_bytes, timeout, cancelled):
    """Run mpv; kill it when `cancelled()` says so or when it takes longer than `timeout`. Check and move the result."""
    with tempfile.TemporaryFile() as err:
        try:
            p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
        except OSError as e:
            raise OverlayError("could not convert the picture: %s" % e)
        end = time.monotonic() + timeout
        stop = None
        while True:
            try:
                p.wait(0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancelled is not None and cancelled():
                stop = "cancelled"
            elif time.monotonic() > end:
                stop = "timeout"
            if stop:
                p.kill()
                p.wait()
                break
        if stop:
            _unlink(tmp)
            if stop == "cancelled":
                raise Cancelled("overtaken by a newer change")
            raise OverlayError("could not convert the picture: mpv took longer than %d s" % timeout)
        err.seek(0)
        said = err.read()[-2000:].decode(errors="replace").strip()
    try:
        got = os.stat(tmp).st_size
    except FileNotFoundError:
        raise OverlayError("the picture could not be read (%s)" % (said[-120:] or "no output"))
    if got != size_bytes:
        _unlink(tmp)
        raise OverlayError("the picture came out the wrong size")
    os.chmod(tmp, 0o640)
    os.replace(tmp, out_path)
    return out_path


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _start(mpv_bin, tmp):
    _unlink(tmp)
    return [mpv_bin, "--no-config", "--really-quiet", "--no-audio", "--frames=1", "--of=rawvideo", "--ovc=rawvideo"]


def convert(png_path, width, height, out_path, mpv_bin="mpv", timeout=TIMEOUT_ONE, cancelled=None):
    """Write `png_path` as width x height premultiplied BGRA to `out_path`. Raises OverlayError (Cancelled when
    `cancelled()` became true while mpv worked)."""
    if not (isinstance(width, int) and isinstance(height, int) and 16 <= width <= MAX_SIDE and 16 <= height <= MAX_SIDE):
        raise OverlayError("screen size out of range")
    size = "%d:%d" % (width, height)
    tmp = out_path + ".tmp"
    args = _start(mpv_bin, tmp) + ["--vf=lavfi=[%s,format=bgra]" % _fit(size), "--o=" + tmp, "--", png_path]
    return _run(args, tmp, out_path, width * height * 4, timeout, cancelled)


def combine(png_paths, width, height, out_path, mpv_bin="mpv", timeout=None, cancelled=None):
    """Lay the PNGs `png_paths` over each other in order (the last on top), each fitted to width x height, and write
    the result as premultiplied BGRA to `out_path`. One picture is `convert`. Raises OverlayError or Cancelled."""
    count = len(png_paths)
    if not 1 <= count <= MAX_MASKS:
        raise OverlayError("between 1 and %d masks" % MAX_MASKS)
    if count == 1:
        return convert(png_paths[0], width, height, out_path, mpv_bin, timeout or TIMEOUT_ONE, cancelled)
    if not (isinstance(width, int) and isinstance(height, int) and 16 <= width <= MAX_WIDTH and 16 <= height <= MAX_HEIGHT):
        raise OverlayError("several masks are combined for a screen of up to %d x %d; this one is %s x %s"
                           % (MAX_WIDTH, MAX_HEIGHT, width, height))
    tmp = out_path + ".tmp"
    args = (_start(mpv_bin, tmp) + ["--lavfi-complex=" + graph(count, width, height), "--o=" + tmp]
            + ["--external-file=" + p for p in png_paths[1:]] + ["--", png_paths[0]])
    return _run(args, tmp, out_path, width * height * 4, timeout or TIMEOUT_ONE + TIMEOUT_EACH * count, cancelled)


# ---- the worker ----------------------------------------------------------------------------------------------------
def _signature(path):
    """What says a file was replaced: its inode, size and time. None for a file that is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class Masks:
    """Builds the combined picture in a thread of its own and shows it. One build at a time: the newest change
    wins, and one that a newer change overtook is stopped. `status` is what the panel shows:
    {"state": "off" | "combining" | "on" | "error", "message", "count"}.

    | lock           | guards                                                                               |
    | -------------- | ------------------------------------------------------------------------------------ |
    | `_apply`       | masks.apply: the newest change's check and its draw into the player, together          |
    | `_lock`        | masks.state: the counters, the job and the status; never held over a call            |
    """

    def __init__(self, api, log=print):
        self.api = api
        self.log = log
        self._lock = locks.make("masks.state")
        self._apply = locks.make("masks.apply")
        self._gen = 0
        self._serial = 0
        self._job = None
        self._building = False
        self._key = None            # (screen size, [(name, signature)]) of the newest build started: watch() compares
        self.status = {"state": "off", "message": "", "count": 0}
        self.combine = combine      # a test puts its own here
        self._cleanup(None, 0)      # what an earlier run of the service left (this process has written nothing yet)

    # -- what is asked for --
    def _player(self):
        return self.api.player

    def _cfg(self):
        with self.api.settings.lock:
            return dict(self.api.settings.data.get("overlay") or {})

    def _wanted(self, cfg):
        """[(name, path or None)] of the masks that are on, in order; a path of None is a file not in the media folder."""
        out = []
        for m in masks_of(cfg):
            if not m["on"]:
                continue
            try:
                out.append((m["file"], self.api.resolve_media(m["file"])))
            except Exception:
                out.append((m["file"], None))
        return out

    def _size(self):
        try:
            return self._player().osd_size()
        except Exception:
            return None

    def _key_of(self, size, wanted):
        return (size, tuple((name, _signature(path) if path else None) for name, path in wanted))

    def _set_status(self, state, message="", count=0, gen=None):
        with self._lock:
            if gen is not None and gen != self._gen:
                return
            self.status = {"state": state, "message": message, "count": count}

    # -- the changes --
    def apply(self):
        """Make the screen match the settings. Returns at once: the combining goes on in the worker. Never raises."""
        with self._lock:
            self._gen += 1
            gen = self._gen
        cfg = self._cfg()
        if not showing(cfg):
            with self._lock:
                self._key = None
            self._commit(gen, None, None, "off", "no mask is switched on" if cfg.get("on") else "", 0)
            return
        self._set_status("combining", "", 0, gen)
        with self._lock:
            self._job = gen
            start = not self._building
            self._building = True
        if start:
            threading.Thread(target=self._builder, name="masks-build", daemon=True).start()

    def busy(self):
        with self._lock:
            return self._building

    def watch(self):
        """Called regularly (pvj/autostart.py's tick): build again when the screen's size changed (D90 to D92) or a
        mask's file was replaced or deleted. What was asked for last is compared, so a file that stays missing, or a
        build that failed, is not tried again and again."""
        cfg = self._cfg()
        if not showing(cfg):
            return False
        size = self._size()
        if size is None:
            return False
        with self._lock:
            if self._building:
                return False
            key = self._key
        if key == self._key_of(tuple(size), self._wanted(cfg)):
            return False
        self.apply()
        return True

    def _builder(self):
        while True:
            with self._lock:
                gen, self._job = self._job, None
                if gen is None:
                    self._building = False
                    return
            try:
                self._build(gen)
            except Exception as e:          # never leave the worker dead with _building set
                self._set_status("error", "could not combine the masks: %s" % e, 0, gen)
                self.log("pvj-web: masks: %s" % e)

    def _build(self, gen):
        cfg = self._cfg()
        wanted = self._wanted(cfg)
        size = self._size()
        size = tuple(size) if size else None
        with self._lock:
            if gen != self._gen:
                return
            self._key = self._key_of(size, wanted) if size else None
        if size is None:
            self._set_status("error", "the player has no screen size yet; the masks are drawn when it has one", 0, gen)
            return
        left_out, paths = [], []
        for name, path in wanted:
            if path is None:
                left_out.append("%s is not in the media folder any more" % name)
            elif not is_png(path):
                left_out.append("%s is not a PNG picture" % name)
            else:
                paths.append(path)
        note = "; ".join(left_out) + (" (left out)" if left_out else "")
        if not paths:
            self._commit(gen, None, None, "on" if left_out else "off", note or "no mask is switched on", 0)
            return
        player = self._player()
        rundir = player.rundir
        with self._lock:
            self._serial += 1
            out = os.path.join(rundir, "masks-%d-%d.bgra" % (os.getpid(), self._serial))
        try:
            self.combine(paths, size[0], size[1], out, getattr(player, "mpv_bin", "mpv"),
                         cancelled=lambda: self._gen != gen)
        except Cancelled:
            return
        except OverlayError as e:
            _unlink(out)
            self._set_status("error", "%s%s" % (e, "; " + note if note else ""), 0, gen)
            return
        self._commit(gen, out, size, "on", note, len(paths))

    def _commit(self, gen, path, size, state, message, count):
        """Show `path` (None: take the overlay off) and set the status, only if `gen` is still the newest change."""
        with self._apply:
            if gen != self._gen:
                if path:
                    _unlink(path)
                return False
            with self._lock:
                limit = self._serial
            player = self._player()
            try:
                if path:
                    player.overlay_file(OVERLAY_ID, path, size[0], size[1])
                else:
                    player.overlay_remove(OVERLAY_ID)
            except Exception as e:      # the player is down or restarting: it gets the masks when it comes back
                if path:
                    self._set_status("error", "the player did not take the masks (%s); they are drawn when it restarts" % e, 0, gen)
                    self._cleanup(None, limit)
                    return False
            self._cleanup(path, limit)
            self._set_status(state, message, count, gen)
            return True

    def _cleanup(self, keep, limit):
        """Remove the combined pictures this process wrote up to serial `limit` (taken when the switch was decided, so
        a file a newer change is writing is never touched), except `keep`, the one now shown; every one left by an
        earlier run of the service; and the single picture of the release before D100 (overlay.bgra)."""
        rundir = getattr(self._player(), "rundir", None)
        if not rundir:
            return
        try:
            names = os.listdir(rundir)
        except OSError:
            return
        for n in names:
            full = os.path.join(rundir, n)
            if full == keep:
                continue
            m = re.fullmatch(FILE_RE, n)
            if m:
                if int(m.group(1)) == os.getpid() and (m.group(3) or int(m.group(2)) > limit):
                    continue
            elif n not in ("overlay.bgra", "overlay.bgra.tmp"):
                continue
            try:
                if os.path.isfile(full) and not os.path.islink(full):
                    os.unlink(full)
            except OSError:
                pass
