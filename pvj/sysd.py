# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-sysd: the small root helper for reboot, power off and setting the clock, on behalf of the unprivileged panel.

The old panel had Reboot, Power off and Set time buttons (it ran `sudo reboot` and `date -s` from PHP). Here the panel
(user pvj-web) sends one JSON line over a Unix socket in /run/pvj-sysd; this daemon answers only root and pvj-web (checked
with SO_PEERCRED), and runs only these fixed commands, as argument lists, never a shell:

* reboot, poweroff: `systemctl reboot|poweroff`, one second after answering, so the panel gets its reply;
* set_time: only while the clock has NOT been set from the network (a Pi has no clock battery, so without a network
  it starts at the last shutdown time); `timedatectl set-time` with a validated epoch, then network time back on, so
  a network that appears later still corrects it;
* status: whether the clock is set from the network;
* update: start pvj-update-usb@V.service or pvj-update-inbox@V.service (fixed template units, V a checked version: pvj-update checks the signature,
  refuses older versions and rolls back by itself).

It holds no capabilities of its own (systemd and timedated do the work), and OSC, MIDI and DMX cannot reach it.
"""

import datetime
import json
import socket
import os
import re
import subprocess
import threading
import time

from . import paths
from .netd import exchange, NetServer, peer_uid  # noqa: F401  (the same small, reviewed socket server)

MIN_EPOCH = 1735689600      # 2025-01-01: anything earlier is a wrong clock, not a date to set
MAX_EPOCH = 2082758400      # 2036-01-01: a phone set to a far future year would otherwise stick (timesyncd saves the
                            # clock and never moves it back), so such a date is refused, not set
MARKER = "ntp-was-on"       # left in the helper's own runtime folder while network time is switched off
STATUS_CACHE = 5.0


class SysService:
    def __init__(self, runner=subprocess.run, schedule=None, log=print, rundir=None, clock=time.monotonic, now=time.time):
        self.runner, self.log = runner, log
        self._schedule = schedule or (lambda delay, fn: threading.Timer(delay, fn).start())
        self.lock = threading.Lock()
        self.rundir = rundir
        self._clock, self._now = clock, now
        self._status = None            # (time, reply): a view device polling the panel must not make root spawn a process each time
        self._power_requested = None

    def _run(self, argv, timeout=15):
        assert isinstance(argv, list) and argv[0] in ("systemctl", "timedatectl")
        try:
            r = self.runner(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _show(self, prop):
        code, out = self._run(["timedatectl", "show", "--property=" + prop, "--value"])
        return None if code != 0 else out.strip() == "yes"

    def synchronized(self):
        """True if the clock has been set from the network, None if that cannot be read."""
        return self._show("NTPSynchronized")

    def status(self):
        t = self._clock()
        if self._status and t - self._status[0] < STATUS_CACHE:
            return dict(self._status[1], now=int(self._now()))
        reply = {"ok": True, "clock_from_network": self.synchronized(), "now": int(self._now())}
        self._status = (t, reply)
        return reply

    def power(self, verb):
        if self._power_requested:
            return {"ok": True, verb: True, "already": self._power_requested}
        # Ask systemd whether it would do it before saying yes: a refusal is reported, not logged and lost.
        code, out = self._run(["systemctl", "--dry-run", verb])
        if code != 0:
            return {"ok": False, "error": "the system refused to %s: %s" % (verb, out.strip()[-160:])}
        self._power_requested = verb

        def go():
            code, out = self._run(["systemctl", verb])
            if code != 0:
                self._power_requested = None
                self.log("pvj-sysd: systemctl %s failed: %s" % (verb, out.strip()[-200:]))
        self.log("pvj-sysd: %s requested by the panel" % verb)
        self._schedule(1.0, go)
        return {"ok": True, verb: True}

    # --- the clock ----------------------------------------------------------
    def _marker(self):
        return os.path.join(self.rundir, MARKER) if self.rundir else None

    def _ntp_on(self):
        """Switch network time back on, check it, and clear the marker. True if it is on."""
        for _ in range(2):
            self._run(["timedatectl", "set-ntp", "true"])
            if self._show("NTP"):
                m = self._marker()
                if m:
                    try:
                        os.unlink(m)
                    except FileNotFoundError:
                        pass
                return True
        return False

    def recover(self):
        """At start: if an earlier run switched network time off and died before switching it back, switch it on."""
        m = self._marker()
        if m and os.path.exists(m):
            ok = self._ntp_on()
            self.log("pvj-sysd: network time was left off by an interrupted clock change; %s"
                     % ("switched it back on" if ok else "could NOT switch it back on"))
            return ok
        return None

    def set_time(self, epoch):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not MIN_EPOCH <= epoch <= MAX_EPOCH:
            return {"ok": False, "error": "that is not a sensible date"}
        with self.lock:
            synced = self.synchronized()
            if synced is None:
                return {"ok": False, "error": "cannot tell whether the clock is set from the network; not changing it"}
            if synced:
                return {"ok": False, "error": "the clock is already set from the network"}
            ntp_was_on = self._show("NTP")
            stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if ntp_was_on:
                m = self._marker()
                if m:
                    with open(m, "w") as f:
                        f.write("1\n")
                code, out = self._run(["timedatectl", "set-ntp", "false"])
                if code != 0:
                    self._ntp_on()
                    return {"ok": False, "error": "could not pause network time: %s" % out.strip()[-160:]}
            code, out = self._run(["timedatectl", "--adjust-system-clock", "set-time", stamp + " UTC"])
            back_on = self._ntp_on() if ntp_was_on else True
            self._status = None
            if code != 0:
                return {"ok": False, "error": "the clock could not be set: %s" % out.strip()[-160:]}
            if not back_on:
                return {"ok": False, "error": "the clock was set, but network time could not be switched back on"}
            now = int(self._now())
            self.log("pvj-sysd: clock set to %s UTC by the panel" % stamp)
            if abs(now - epoch) > 120:
                return {"ok": False, "error": "the clock was set but moved again (to %s UTC); a saved clock may be ahead"
                        % datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")}
            return {"ok": True, "now": now}

    UPDATE_SOURCES = ("usb", "inbox")
    UPDATE_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")

    def update(self, source, version):
        """Start pvj-update-<source>@<version>.service, a fixed template unit (pvj-update checks the signature,
        refuses older versions and rolls back by itself). Only the source, from a fixed list, and a version that
        is three numbers reach systemctl."""
        if source not in self.UPDATE_SOURCES:
            return {"ok": False, "error": "source must be usb or inbox"}
        if not isinstance(version, str) or not self.UPDATE_VERSION.fullmatch(version):
            return {"ok": False, "error": "version must look like 1.2.3"}
        with self.lock:                       # two requests at once must not both see "nothing running"
            code, out = self._run(["systemctl", "list-units", "--plain", "--no-legend", "--state=active,activating",
                                   "pvj-update-usb@*.service", "pvj-update-inbox@*.service"])
            if code != 0 or out.strip():
                return {"ok": False, "error": "an update is already running"}
            code, out = self._run(["systemctl", "start", "--no-block", "pvj-update-%s@%s.service" % (source, version)])
        if code != 0:
            return {"ok": False, "error": "could not start the update: %s" % out.strip()[-160:]}
        self.log("pvj-sysd: update to %s from %s started by the panel" % (version, source))
        return {"ok": True, "started": source, "version": version}

    def handle(self, message):
        if not isinstance(message, dict):
            return {"ok": False, "error": "bad request"}
        cmd = message.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd in ("reboot", "poweroff"):
            return self.power(cmd)
        if cmd == "set_time":
            return self.set_time(message.get("epoch"))
        if cmd == "update":
            return self.update(message.get("source"), message.get("version"))
        return {"ok": False, "error": "unknown command"}


class SysdClient:
    """Used by the panel: one request, one reply."""

    def __init__(self, path, timeout=90):          # longer than the helper's worst case (four 15 s commands)
        self.path, self.timeout = path, timeout

    def request(self, message):
        try:
            reply = json.loads(exchange(self.path, message, self.timeout))
        except socket.timeout:
            raise OSError("the system helper (pvj-sysd) did not answer in time; the change may still be going on")
        except (OSError, ValueError):
            raise OSError("the system helper (pvj-sysd) is not running")
        if not isinstance(reply, dict):
            raise OSError("the system helper (pvj-sysd) gave a bad answer")
        return reply

def main(argv=None):
    import grp
    import pwd
    # Its own folder (root:pvj 0750): nobody but root can create or replace anything in it, so no member of group
    # pvj can put a fake socket in its place.
    rundir = paths.sysd_dir()
    os.makedirs(rundir, exist_ok=True)
    allowed = {0}
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        pass
    service = SysService(log=lambda m: print(m, flush=True), rundir=rundir)
    service.recover()
    server = NetServer(os.path.join(rundir, paths.SYSD_SOCKET), service, lambda uid: uid in allowed)
    try:
        os.chown(server.server_address, 0, grp.getgrnam("pvj").gr_gid, follow_symlinks=False)
    except (KeyError, OSError):
        pass
    print("pvj-sysd: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
