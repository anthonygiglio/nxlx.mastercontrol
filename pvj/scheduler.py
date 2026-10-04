# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A weekly schedule: at a time of day, play a clip, stop, or black out or show the screen.

The schedule lives in settings["schedule"]. A background thread checks the box's clock and runs
entries through the same Api handlers as the panel and OSC, so every value is validated the
same way.

Entries can also start a legacy start script (startlessonce01 and so on) and switch every projector on or off.

Safety rules:
- A schedule does nothing until both the Scheduler module and the schedule switch are on.
- Entries fire at most once per minute, and only for minutes the thread actually watched. If the
  clock jumps (a Pi has no clock until the network sets it) or the thread stalls for more than
  two minutes, the skipped minutes are NOT replayed: a wrong clock must never fire old events.
- Nothing dangerous is schedulable: only play, stop, blackout, show, a start script, projector power and the shader
  rotation (Vibes).
"""

import datetime
import re
import threading
import time
import uuid

from .api import ApiError, MEDIA_EXTENSIONS, valid_name

ACTIONS = ("play", "stop", "blackout", "show", "preset", "projector_on", "projector_off", "vibes")
MAX_ENTRIES = 50
MAX_CATCHUP_MINUTES = 2
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ID = re.compile(r"^[0-9a-f]{8}$")


class ScheduleError(Exception):
    pass


def default_schedule():
    return {"enabled": False, "entries": []}


def validate(body):
    """A clean schedule from untrusted input. Raises ScheduleError with a message fit for the panel."""
    if not isinstance(body, dict):
        raise ScheduleError("schedule must be an object")
    enabled = body.get("enabled", False)
    entries = body.get("entries", [])
    if not isinstance(enabled, bool):
        raise ScheduleError("enabled must be true or false")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ScheduleError("entries must be a list of at most %d" % MAX_ENTRIES)
    clean, seen = [], set()
    for n, e in enumerate(entries, 1):
        where = "entry %d: " % n
        if not isinstance(e, dict):
            raise ScheduleError(where + "must be an object")
        eid = e.get("id")
        if eid is None or eid == "":
            eid = uuid.uuid4().hex[:8]
        if not isinstance(eid, str) or not _ID.fullmatch(eid) or eid in seen:
            raise ScheduleError(where + "bad or repeated id")
        seen.add(eid)
        at = e.get("time")
        if not isinstance(at, str) or not _TIME.fullmatch(at):
            raise ScheduleError(where + "time must be HH:MM, 24 hour")
        days = e.get("days")
        if (not isinstance(days, list) or not days or len(days) > 7
                or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
            raise ScheduleError(where + "days must be a list of 0 (Monday) to 6 (Sunday)")
        action = e.get("action")
        if action not in ACTIONS:
            raise ScheduleError(where + "action must be one of %s" % ", ".join(ACTIONS))
        label = e.get("label", "")
        if not isinstance(label, str) or len(label) > 40 or re.search(r"[\x00-\x1f]", label):
            raise ScheduleError(where + "invalid label")
        item = {"id": eid, "label": label, "time": at, "days": sorted(set(days)), "action": action}
        if action == "play":
            name, loop = e.get("file"), e.get("loop", True)
            if not valid_name(name) or not name.lower().endswith(MEDIA_EXTENSIONS):
                raise ScheduleError(where + "choose a video or image file to play")
            if not isinstance(loop, bool):
                raise ScheduleError(where + "loop must be true or false")
            item["file"], item["loop"] = name, loop
        elif action == "preset":                      # a legacy start script, as the old cron timetable ran them
            from . import presets
            from .player import PlayerError
            name = e.get("preset")
            try:
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", name):
                    raise PlayerError("x")
                presets.parse_legacy_name(name)
            except PlayerError:
                raise ScheduleError(where + "enter a start script name such as startlessonce01")
            item["preset"] = name
        clean.append(item)
    return {"enabled": enabled, "entries": clean}


class Scheduler:
    def __init__(self, api, settings, registry, now=None, log=print, interval=5.0):
        self.api, self.settings, self.registry, self.log = api, settings, registry, log
        self._now = now or datetime.datetime.now
        self.interval = interval
        self._checked = None            # the last minute (a datetime) already handled
        self.last = {}                  # entry id -> {"at": "...", "ok": bool, "message": str}
        self._stop = threading.Event()
        self._thread = None

    def active(self):
        cfg = self.settings.data.get("schedule") or default_schedule()
        return bool(cfg["enabled"] and self.registry.enabled("scheduler"))

    def status(self):
        cfg = self.settings.data.get("schedule") or default_schedule()
        now = self._now()
        return {"enabled": cfg["enabled"], "entries": cfg["entries"], "active": self.active(), "last": dict(self.last),
                "now": now.strftime("%Y-%m-%d %H:%M:%S"), "timezone": time.strftime("%Z") or "local"}

    def tick(self):
        """Handle every minute since the last tick. Returns the ids fired."""
        now = self._now().replace(second=0, microsecond=0)
        previous, self._checked = self._checked, now
        if previous is None or now <= previous:
            return []           # first look, or the clock went backwards: fire nothing
        gap = int((now - previous).total_seconds() // 60)
        if gap > MAX_CATCHUP_MINUTES:
            return []           # a jump (clock set, long stall): never replay old events
        fired = []
        if not self.active():
            return fired
        for step in range(1, gap + 1):
            minute = previous + datetime.timedelta(minutes=step)
            for entry in list(self.settings.data["schedule"]["entries"]):
                if minute.weekday() in entry["days"] and entry["time"] == minute.strftime("%H:%M"):
                    self._run(entry, minute)
                    fired.append(entry["id"])
        return fired

    def _run(self, entry, minute):
        if entry["action"] in ("projector_on", "projector_off"):
            # A projector that is off the network can take seconds to fail; the next entry must not wait for it.
            threading.Thread(target=self._execute, args=(entry, minute), name="schedule-projector", daemon=True).start()
        else:
            self._execute(entry, minute)

    def _execute(self, entry, minute):
        action = entry["action"]
        try:
            if action == "play":
                self.api.play({"file": entry["file"], "loop": entry.get("loop", True)}, None, "schedule")
            elif action == "stop":
                self.api.control({"action": "stop"}, None, "schedule")
            elif action == "preset":
                self.api.play({"preset": entry["preset"]}, None, "schedule")
            elif action == "vibes":
                self.api.vibes.api_vibes({"on": True}, None, "schedule")
            elif action in ("projector_on", "projector_off"):
                out = self.api.projector_action({"id": "all", "action": "on" if action == "projector_on" else "off"}, None, "schedule")
                failed = [r["error"] for r in out["results"].values() if not r["ok"]]
                if failed:
                    raise ApiError(502, "; ".join(failed))
            else:
                self.api.blackout({"on": action == "blackout"}, None, "schedule")
            result = {"ok": True, "message": "done"}
        except ApiError as e:
            result = {"ok": False, "message": e.message}
        except Exception as e:  # a failing entry must never stop the schedule
            result = {"ok": False, "message": "error: %s" % e}
        result["at"] = minute.strftime("%Y-%m-%d %H:%M")
        self.last[entry["id"]] = result
        self.log("pvj-web: schedule %s %s at %s: %s" % (entry["id"], action, result["at"], result["message"]))

    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._checked = None

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as e:
                    self.log("pvj-web: scheduler error: %s" % e)
        self._thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
