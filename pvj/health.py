# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The box's health, in plain words, for the System screen.

The old panel had Powersupply (it decoded vcgencmd get_throttled), Check Services and GPU Usage buttons; its manual
blamed undervoltage for Wi-Fi drops, the rainbow square and damaged SD cards. Here, without root:

* power: the Pi's undervoltage alarm (hwmon `rpi_volt`, readable by anyone), checked every few seconds; any
  undervoltage is remembered in a small file in /run (RAM), so "since the box started" survives a restart of the
  panel and is forgotten at a reboot;
* temperature, with the Pi's slow-down points;
* CPU load and memory, from /proc;
* the player: running, hardware decode, dropped frames a second while playing;
* the helpers (network, system, remote support): answering or not;
* the projectors (when that module is on): lamp hours and their own warnings, as last read in the background;
* the box's addresses, to open the panel from another device.

No secrets and nothing that changes anything: every paired device may read it.
"""

import glob
import os
import socket
import threading
import time

CHECK_EVERY = 5.0
WARM_C, HOT_C = 80.0, 85.0          # a Pi 4 starts to slow down around 80 C and slows hard at 85 C


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def undervoltage_alarm(sysfs="/sys"):
    """True or False from the Pi's rpi_volt alarm, None where there is none (not a Pi, or an old kernel)."""
    for d in glob.glob(os.path.join(sysfs, "class/hwmon/hwmon*")):
        if _read(os.path.join(d, "name")) == "rpi_volt":
            v = _read(os.path.join(d, "in0_lcrit_alarm"))
            return None if v is None else v == "1"
    return None


def cpu_load(proc="/proc"):
    """The 1 minute load as a percentage of all cores (100 = every core busy)."""
    text = _read(os.path.join(proc, "loadavg"))
    try:
        one = float(text.split()[0])
    except (AttributeError, ValueError, IndexError):
        return None
    return round(100 * one / (os.cpu_count() or 1))


def memory(proc="/proc"):
    """(used percent, total MB) from /proc/meminfo."""
    info = {}
    for line in (_read(os.path.join(proc, "meminfo")) or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            info[parts[0].rstrip(":")] = int(parts[1])
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if not total or avail is None:
        return None, None
    return round(100 * (total - avail) / total), round(total / 1024)


class Health:
    def __init__(self, api, rundir, sysfs="/sys", proc="/proc", clock=time.monotonic, log=print):
        self.api, self.rundir, self.sysfs, self.proc, self._clock, self.log = api, rundir, sysfs, proc, clock, log
        self._stop = threading.Event()
        self._thread = None
        self._drops = None            # (time, count) for a rate
        self.lock = threading.Lock()

    # -- power, remembered until a reboot --
    def _marker(self):
        return os.path.join(self.rundir, "undervoltage-seen")

    def check_power(self):
        alarm = undervoltage_alarm(self.sysfs)
        if alarm:
            path = self._marker()
            if not os.path.exists(path):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
                    os.write(fd, str(int(time.time())).encode())
                    os.close(fd)
                    self.log("pvj-web: undervoltage: the power supply is too weak for the box")
                except OSError:
                    pass
        return alarm

    def power(self):
        alarm = self.check_power()
        seen = _read(self._marker())
        if alarm is None:
            return {"state": "unknown", "text": "Not measured on this board."}
        if alarm:
            return {"state": "bad", "text": "Undervoltage now: the power supply is too weak. Use the official supply (Pi 4: 5 V 3 A); "
                                            "a weak one causes stutters, network drops and damaged SD cards."}
        if seen:
            return {"state": "warn", "since": int(seen) if seen.isdigit() else None,
                    "text": "Undervoltage happened since the box started (not now). Check the power supply and its cable."}
        return {"state": "ok", "text": "Power supply OK since the box started."}

    # -- the rest --
    def temperature(self):
        try:
            from . import hardware
            temps = hardware.temperatures()
            c = max((t["celsius"] for t in temps), default=None)
        except Exception:
            c = None
        if c is None:
            return {"state": "unknown", "celsius": None, "text": "Not measured."}
        if c >= HOT_C:
            return {"state": "bad", "celsius": round(c), "text": "%d C: too hot, the box is slowing down. Give it air or a fan." % c}
        if c >= WARM_C:
            return {"state": "warn", "celsius": round(c), "text": "%d C: hot; above 80 C a Pi starts to slow down." % c}
        return {"state": "ok", "celsius": round(c), "text": "%d C" % c}

    def player(self):
        try:
            st = self.api.player.status()
        except Exception:
            st = {}
        if not st.get("running"):
            return {"state": "bad", "text": "The player is not running; it restarts by itself within seconds. If not: System > Restart player."}
        out = {"state": "ok", "playing": bool(st.get("path"))}
        try:
            ipc = self.api.player.ipc
            hw = ipc.request("get_property", "hwdec-current")
            drops = (ipc.request("get_property", "frame-drop-count") or 0) + (ipc.request("get_property", "decoder-frame-drop-count") or 0)
        except Exception:
            hw, drops = None, None
        out["hwdec"] = hw if isinstance(hw, str) else None
        rate = None
        now = self._clock()
        if isinstance(drops, int) and out["playing"]:
            with self.lock:
                if self._drops and now > self._drops[0] and drops >= self._drops[1]:
                    rate = round((drops - self._drops[1]) / (now - self._drops[0]), 1)
                self._drops = (now, drops)
        else:
            with self.lock:
                self._drops = None
        out["drops_per_second"] = rate
        if not out["playing"]:
            out["text"] = "Running, idle."
        else:
            out["text"] = "Playing" + (", decoded in hardware (%s)" % hw if out["hwdec"] and out["hwdec"] != "no" else ", decoded in software")
            if rate is not None:
                out["text"] += ", %s dropped frames a second" % rate
                if rate >= 1:
                    out["state"] = "warn"
                    out["text"] += ": the clip is too heavy for this box (see Prepare your clips in the manual)"
        return out

    def helpers(self):
        out = []
        for name, label, client in (("pvj-sysd", "System helper (restart, power off, clock)", getattr(self.api, "sysd", None)),
                                    ("pvj-netd", "Network helper (network settings)", getattr(self.api, "net", None)),
                                    ("pvj-supportd", "Remote support helper", getattr(getattr(self.api, "support", None), "client", None))):
            if client is None:
                continue
            try:
                ok = bool(client.request({"cmd": "status"}).get("ok", True))
            except Exception:
                ok = False
            out.append({"name": name, "label": label, "running": ok})
        return out

    def addresses(self):
        port = getattr(getattr(self.api, "support", None), "panel_port", 80) or 80
        suffix = "" if port == 80 else ":%d" % port
        out = []
        host = socket.gethostname()
        if host:
            out.append("http://%s.local%s/" % (host, suffix))
        try:
            for entry in self.api._ip_json():
                if entry.get("ifname") in ("lo", "wg-pvj"):
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("local"):
                        out.append("http://%s%s/" % (a["local"], suffix))
        except Exception:
            pass
        return out

    def projectors(self):
        """The projectors' last known state and warnings (lamp hours, fan, lamp, temperature, cover, filter),
        from the background check; nothing is asked here. Empty while the Projector control module is off."""
        try:
            return self.api.projectors.health()
        except Exception:
            return []

    def report(self):
        load = cpu_load(self.proc)
        mem, total = memory(self.proc)
        parts = {"power": self.power(), "temperature": self.temperature(), "player": self.player(),
                 "cpu_percent": load, "memory_percent": mem, "memory_mb": total,
                 "helpers": self.helpers(), "addresses": self.addresses(), "projectors": self.projectors()}
        states = [parts[k]["state"] for k in ("power", "temperature", "player")]
        states += ["bad" for h in parts["helpers"] if not h["running"] and h["name"] != "pvj-netd"]
        states += [p["state"] for p in parts["projectors"]]      # a projector's own warning or error; "unknown" (no answer) is not one
        parts["overall"] = "bad" if "bad" in states else ("warn" if "warn" in states else "ok")
        return parts

    # -- the background check, so a short undervoltage is noticed with nobody looking --
    def start(self):
        if self._thread:
            return

        def loop():
            while not self._stop.wait(CHECK_EVERY):
                try:
                    self.check_power()
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, name="health", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
