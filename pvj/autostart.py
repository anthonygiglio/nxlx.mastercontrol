# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Autostart: what the box plays by itself, without anyone touching the panel.

Runs when the web service starts, and again whenever the player (mpv) has been restarted, so a
crash at a gig recovers on its own. It never plays again just because playback stopped: if the
player process is still the one we saw, a Stop from the panel stays a stop.

Modes: `off` (default), `file` (one clip), `all` (every clip in the media folder, in order or shuffled),
`slideshow` (the pictures, a set time each), `pad` (what a pad plays), `usb` (the clips at the top of a USB drive,
and again whenever a drive with clips is plugged in: a venue technician swaps the stick and it plays) and `preset`
(a legacy start script name such as startlessonce05). It plays through the same Api calls as the panel. `vibes` starts
the endless shader rotation (the Shaders and Vibes module, see SHADERS.md).
"""

import re
import threading
import time

from . import presets
from .api import ApiError, MEDIA_EXTENSIONS, valid_name
from .player import PlayerError

MODES = ("off", "file", "all", "slideshow", "pad", "usb", "preset", "vibes")
USB_DEBOUNCE = 10.0         # seconds: a drive must be gone this long to count as plugged in again
AUDIO_CHECK_EVERY = 5      # ticks (2 s each): the sound output is re-checked about every 10 seconds
MAX_DELAY = 120


class AutostartError(Exception):
    pass


def default_autostart():
    return {"mode": "off", "file": "", "preset": "", "loop": True, "delay": 0, "shuffle": False, "seconds": 10, "pad": [0, 0]}


def validate(body, current=None):
    """The new autostart settings from untrusted input."""
    if not isinstance(body, dict):
        raise AutostartError("autostart must be an object")
    new = dict(default_autostart(), **(current or {}))      # settings saved before a key existed get its default
    mode = body.get("mode", new["mode"])
    if mode not in MODES:
        raise AutostartError("mode must be one of %s" % ", ".join(MODES))
    new["mode"] = mode
    if "file" in body:
        f = body["file"]
        if f != "" and (not valid_name(f) or not f.lower().endswith(MEDIA_EXTENSIONS)):
            raise AutostartError("choose a video or image file")
        new["file"] = f
    if "preset" in body:
        p = body["preset"]
        if p != "":
            if not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", p):
                raise AutostartError("that is not a start script name")
            try:
                presets.parse_legacy_name(p)
            except PlayerError as e:
                raise AutostartError(str(e))
        new["preset"] = p
    if "loop" in body:
        if not isinstance(body["loop"], bool):
            raise AutostartError("loop must be true or false")
        new["loop"] = body["loop"]
    if "shuffle" in body:
        if not isinstance(body["shuffle"], bool):
            raise AutostartError("shuffle must be true or false")
        new["shuffle"] = body["shuffle"]
    if "seconds" in body:
        v = body["seconds"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 1 <= v <= 3600:
            raise AutostartError("a slide shows for 1 to 3600 seconds")
        new["seconds"] = v
    if "pad" in body:
        pad = body["pad"]
        if not (isinstance(pad, list) and len(pad) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in pad)
                and 0 <= pad[0] < 16 and 0 <= pad[1] < 12):
            raise AutostartError("pad must be [bank, pad]")
        new["pad"] = pad
    if "delay" in body:
        d = body["delay"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or d != d or not 0 <= d <= MAX_DELAY:
            raise AutostartError("delay must be 0 to %d seconds" % MAX_DELAY)
        new["delay"] = d
    if mode == "file" and not new["file"]:
        raise AutostartError("choose the clip to play at start")
    if mode == "preset" and not new["preset"]:
        raise AutostartError("enter the start script name")
    return new


class Autostart:
    def __init__(self, api, settings, log=print, sleep=None, interval=2.0, clock=time.monotonic):
        self.api, self.settings, self.log = api, settings, log
        self._clock = clock
        self._sleep = sleep or time.sleep
        self.interval = interval
        self.seen_pid = None
        self._ticks = 0
        self._usb_seen = None       # USB drives with clips at the last look (mode usb)
        self._usb_gone = {}         # label -> when it was last seen to disappear
        self._usb_last_seen = {}
        self._usb_last_run = -1e9
        self.last = None            # {"at": ..., "ok": bool, "message": str}
        self._stop = threading.Event()
        self._thread = None

    def _pid(self):
        try:
            return self.api.player.ipc.request("get_property", "pid")
        except PlayerError:
            return None

    def status(self):
        return {"config": dict(self.settings.data["autostart"]), "last": self.last, "player_pid": self.seen_pid}

    def run_now(self, drive=None):
        """Do what the settings say (in usb mode, `drive` is the drive just plugged in). Returns the message; errors
        are recorded, never raised."""
        cfg = self.settings.data["autostart"]
        if cfg["mode"] == "off":
            return "off"
        cfg = dict(default_autostart(), **cfg)
        try:
            if cfg["mode"] == "file":
                self.api.play({"file": cfg["file"], "loop": cfg["loop"]}, None, "autostart")
            elif cfg["mode"] == "all":
                body = {"preset": "startless" if cfg["loop"] else "startlessonce"}
                if cfg["shuffle"]:
                    body["shuffle"] = True
                self.api.play(body, None, "autostart")
            elif cfg["mode"] == "slideshow":
                self.api.play({"slideshow": {"source": "media", "seconds": cfg["seconds"], "shuffle": cfg["shuffle"],
                                             "ending": "loop" if cfg["loop"] else "stop"}}, None, "autostart")
            elif cfg["mode"] == "pad":
                self.api.play({"pad": list(cfg["pad"])}, None, "autostart")
            elif cfg["mode"] == "vibes":
                self.api.vibes.api_vibes({"on": True}, None, "autostart")
            elif cfg["mode"] == "usb":
                drives = self._drives_with_clips()
                self._usb_seen = set(drives)       # what is here now counts as seen (a stick mounted at boot is not missed)
                label = drive or (sorted(drives)[0] if drives else None)
                if label is None:
                    raise ApiError(404, "no USB drive with clips yet; it plays when one is plugged in")
                self.api.play({"usb_drive": label, "shuffle": cfg["shuffle"]}, None, "autostart")
            else:
                self.api.play({"preset": cfg["preset"]}, None, "autostart")
            result = {"ok": True, "message": "started"}
        except ApiError as e:
            result = {"ok": False, "message": e.message}
        except Exception as e:      # never let a bad clip take the service down
            result = {"ok": False, "message": "error: %s" % e}
        result["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last = result
        self.log("pvj-web: autostart %s: %s" % (cfg["mode"], result["message"]))
        return result["message"]

    def _drives_with_clips(self):
        """Labels of the USB drives that have clips at their top level (an error reading them counts as none)."""
        try:
            return {d["drive"] for d in self.api.usb_drives() if d.get("files")}
        except Exception:
            return set()

    def _watch_usb(self):
        """Mode usb: a drive with clips that was not there before is played (the stick was swapped). A drive that
        comes and goes within 10 seconds (a bad contact, a read error) is not played again, and nothing is
        started more often than every 10 seconds."""
        now_t = self._clock()
        now = self._drives_with_clips()
        for label in now:
            self._usb_last_seen[label] = now_t
        if self._usb_seen is None:          # the first look: what is here is not "new"
            self._usb_seen = now
            return False
        new = [l for l in now - self._usb_seen if now_t - self._usb_gone.get(l, -1e9) >= USB_DEBOUNCE]
        for label in self._usb_seen - now:
            self._usb_gone[label] = now_t
        self._usb_seen = now
        if not new or now_t - self._usb_last_run < USB_DEBOUNCE:
            return False
        label = sorted(new)[0]
        self._usb_last_run = now_t
        self.log("pvj-web: autostart: USB drive %s plugged in" % label)
        self.run_now(drive=label)
        return True

    def tick(self):
        """Call regularly. Starts playback the first time it sees a player and after each player restart.
        Returns True if it ran."""
        pid = self._pid()
        self._ticks += 1
        if pid is not None and pid == self.seen_pid and self._ticks % AUDIO_CHECK_EVERY == 0:
            ensure = getattr(self.api, "ensure_audio", None)
            if ensure:
                ensure()        # a screen switched on late, or a sound device plugged in later
        if self.settings.data["autostart"].get("mode") != "usb":
            self._usb_seen = None           # switching back to usb mode later must not count every stick as new
        elif pid is not None and pid == self.seen_pid:
            return self._watch_usb()
        if pid is None or pid == self.seen_pid:
            return False
        first_sight = self.seen_pid is None
        self.seen_pid = pid
        apply_audio = getattr(self.api, "apply_audio", None)
        if apply_audio:                 # a new player starts on mpv's own sound output: put it on the chosen one first
            try:
                apply_audio()
            except Exception as e:
                self.log("pvj-web: could not set the sound output: %s" % e)
        apply_overlay = getattr(self.api, "apply_overlay", None)
        if apply_overlay and self.settings.data.get("overlay", {}).get("on"):   # a restarted player lost the picture
            try:
                apply_overlay()
            except Exception as e:
                self.log("pvj-web: could not put the overlay back: %s" % e)
        sync = getattr(self.api, "sync", None)
        if sync is not None:                 # a restarted player lost the wall crop
            try:
                sync.apply_wall(force=True)
            except Exception as e:
                self.log("pvj-web: could not put the wall crop back: %s" % e)
        apply_mapper = getattr(self.api, "apply_mapper", None)
        if apply_mapper and self.settings.data.get("mapper", {}).get("surfaces"):   # it lost the mapping too
            try:
                apply_mapper()
            except Exception as e:
                self.log("pvj-web: could not put the mapping back: %s" % e)
        cfg = self.settings.data["autostart"]
        if cfg["mode"] == "off":
            return False
        if cfg["delay"]:
            self._sleep(cfg["delay"] if first_sight else min(cfg["delay"], 5))
        self.run_now()
        return True

    def start(self):
        if self._thread:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as e:
                    self.log("pvj-web: autostart error: %s" % e)
        self._thread = threading.Thread(target=loop, name="autostart", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
