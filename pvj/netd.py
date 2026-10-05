# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-netd: the small root helper that changes the wired and Wi-Fi network on behalf of the unprivileged panel.

The panel (user pvj-web) cannot and must not run nmcli. It sends one JSON line over a Unix socket in
its own folder /run/pvj/netd (root:pvj, 0750); this daemon re-validates everything with pvj.netcfg, runs only the fixed nmcli
commands that come out of it (argument lists, never a shell), and keeps a safety net:

* a change is built as a separate candidate profile; the confirmed profile is never edited in place
* it is pending until confirmed, and is undone when the timer runs out, when a command fails, when this
  daemon restarts, and when the box reboots (the candidate is not autoconnect until confirmed)
* an undo that fails is retried until it works, and never reported as done before it is
* the saved state lives in a root-only directory (never in /run, which is gone after a restart)
* a Wi-Fi password goes only into a root-only keyfile, never into a command line, a reply or a log
"""

import ipaddress
import json
import os
import re
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import uuid as uuidlib

from . import netcfg, paths
from .netcfg import NetError

MAX_LINE = 4096
UP_TIMEOUT = 20          # `connection up` may wait for DHCP; the panel request must outlast the total below
OTHER_TIMEOUT = 10
SCAN_TIMEOUT = 15        # `device wifi list --rescan yes` waits for the scan to finish
WIFI_UP_TIMEOUT = 45     # joining Wi-Fi (association, then DHCP) takes longer than a cable
MAX_REVERT_TRIES = 20    # then it keeps trying, slowly, and never gives up while running
REVERT_RETRY_SECONDS = 3
SLOW_RETRY_SECONDS = 60


class NetService:
    def __init__(self, runner=subprocess.run, clock=time.monotonic, sysfs="/sys/class/net", state_dir=None,
                 log=print, keyfile_dir=netcfg.KEYFILE_DIR, new_token=None):
        self.runner, self.clock, self.sysfs, self.log = runner, clock, sysfs, log
        self.keyfile_dir = keyfile_dir
        self.new_token = new_token or (lambda: os.urandom(4).hex())
        self.state_file = os.path.join(state_dir, "net-pending.json") if state_dir else None
        self.pending = None
        self._revert_job = None
        self.lock = threading.RLock()

    # --- running commands ------------------------------------------------
    def _run(self, argv, timeout=None):
        assert isinstance(argv, list) and argv[0] in ("nmcli", "ip")
        if timeout is None:
            timeout = UP_TIMEOUT if argv[:3] == ["nmcli", "connection", "up"] else OTHER_TIMEOUT
            if argv[1:7] == ["-t", "-f", "SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list"]:
                timeout = SCAN_TIMEOUT
        try:
            return self.runner(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise NetError("%s is not installed on this system" % argv[0])
        except subprocess.TimeoutExpired:
            raise NetError("%s did not answer in %d seconds" % (argv[0], timeout))

    def _must(self, argv, timeout=None):
        r = self._run(argv, timeout)
        if r.returncode != 0:
            raise NetError((r.stderr or r.stdout or "nmcli failed").strip()[:300])
        return r.stdout

    # --- what NetworkManager and the kernel say ------------------------------
    def _active_uuid(self, iface):
        r = self._run(["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"])
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            uuid, _, dev = line.partition(":")
            if dev == iface and netcfg.UUID.fullmatch(uuid):
                return uuid
        return None

    def _radio(self):
        """(hardware allows it, radio on), from `nmcli radio`; (None, None) when it cannot be read."""
        try:
            r = self._run(["nmcli", "-t", "-f", "WIFI-HW,WIFI", "radio"])
        except NetError:
            return None, None
        f = netcfg.parse_terse(r.stdout.strip()) if r.returncode == 0 else []
        if len(f) != 2:
            return None, None
        return f[0] == "enabled", f[1] == "enabled"

    def _wifi_now(self, iface):
        """What a Wi-Fi port is doing: the network name and whether it is the box's own hotspot."""
        uuid = self._active_uuid(iface)
        if not uuid:
            return None
        try:
            r = self._run(["nmcli", "-t", "-g", "802-11-wireless.ssid,802-11-wireless.mode", "connection", "show", "uuid", uuid])
        except NetError:
            return None
        lines = r.stdout.split("\n") if r.returncode == 0 else []
        if len(lines) < 2:
            return None
        return {"ssid": netcfg.clean_ssid(netcfg.unescape_terse(lines[0])) or "?",
                "hotspot": lines[1].strip() == "ap"}

    def _write_keyfile(self, cfg, token):
        """The candidate Wi-Fi profile, readable by root only. The directory must be root's and not writable by
        anyone else; the file must not exist (no following a planted link)."""
        d = self.keyfile_dir
        try:
            st = os.lstat(d)
        except OSError:
            raise NetError("NetworkManager's profile folder %s is missing" % d)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise NetError("NetworkManager's profile folder %s is not safe to write to" % d)
        path = netcfg.keyfile_path(cfg["iface"], token, d)
        text = netcfg.keyfile(cfg, str(uuidlib.uuid4()))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise NetError("could not write the Wi-Fi profile (%s exists or cannot be made)" % path)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            self._unlink_keyfile(cfg["iface"], token)
            raise NetError("could not write the Wi-Fi profile")

    def _unlink_keyfile(self, iface, token):
        if not token:
            return
        try:
            os.unlink(netcfg.keyfile_path(iface, token, self.keyfile_dir))
        except (OSError, NetError):
            pass

    def _exists(self, name):
        return self._run(["nmcli", "-t", "-f", "connection.id", "connection", "show", "id", name]).returncode == 0

    def _others(self):
        """(interface, network) for every address in use, so a new range cannot collide with one."""
        try:
            r = self._run(["ip", "-j", "-4", "addr", "show"])
            data = json.loads(r.stdout) if r.returncode == 0 else []
        except (NetError, ValueError):
            return []
        out = []
        for entry in data:
            name = entry.get("ifname")
            if name == "lo":
                continue
            for a in entry.get("addr_info", []):
                try:
                    out.append((name, ipaddress.ip_network("%s/%s" % (a["local"], a["prefixlen"]), strict=False)))
                except (KeyError, ValueError):
                    pass
        return out

    def _validated(self, request):
        return netcfg.validate(request, netcfg.list_interfaces(self.sysfs), self._others())

    # --- public operations ---------------------------------------------------------
    def status(self, wifi=False):
        with self.lock:
            p = self.pending
            out = {"interfaces": netcfg.list_interfaces(self.sysfs), "reverting": self._revert_job is not None,
                   "pending": None if p is None else {"iface": p.cfg["iface"], "mode": p.cfg["mode"],
                                                      "ssid": p.cfg.get("ssid"),
                                                      "seconds_left": p.seconds_left(self.clock())}}
        if wifi and any(i["kind"] == "wifi" for i in out["interfaces"]):
            hw, on = self._radio()   # outside the lock: a slow nmcli must not hold up an apply or the timer
            out["wifi"] = {"hardware": hw, "radio": on,
                           "ports": {i["name"]: self._wifi_now(i["name"]) for i in out["interfaces"] if i["kind"] == "wifi"}}
        return out

    def plan(self, request):
        cfg = self._validated(request)
        return {"config": netcfg.public(cfg), "commands": netcfg.preview(netcfg.plan(cfg, None, self.keyfile_dir))}

    def scan(self, iface):
        """Wi-Fi networks in range of one Wi-Fi port, strongest first."""
        ports = [i for i in netcfg.list_interfaces(self.sysfs) if i["kind"] == "wifi"]
        if not isinstance(iface, str) or iface not in [i["name"] for i in ports]:
            raise NetError("no such Wi-Fi port")
        hw, on = self._radio()
        if hw is False:
            raise NetError("Wi-Fi is blocked on this box (a switch, or no Wi-Fi country set)")
        if on is False:
            raise NetError("Wi-Fi is off; choose a network or a hotspot and apply it to switch it on")
        base = ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list", "ifname", iface]
        r = self._run(base + ["--rescan", "yes"])
        if r.returncode != 0:   # "scanning not allowed" right after another scan: the cached list is fine
            r = self._run(base + ["--rescan", "no"])
        if r.returncode != 0:
            raise NetError((r.stderr or "the Wi-Fi scan failed").strip()[:300])
        now = self._wifi_now(iface)
        return {"networks": netcfg.scan_results(r.stdout, current=now["ssid"] if now and not now["hotspot"] else None)}

    def apply(self, request):
        with self.lock:
            if self.pending or self._revert_job:
                raise NetError("a change is already waiting for confirmation (or being undone); wait or revert it first")
            cfg = self._validated(request)
            iface = cfg["iface"]
            wifi = netcfg.is_wifi(cfg)
            radio_was_off, token = False, None
            if wifi:
                hw, on = self._radio()
                if hw is False:
                    raise NetError("Wi-Fi is blocked on this box (a switch, or no Wi-Fi country set); it cannot be changed here")
                if cfg["mode"] == "off" and on is False:
                    raise NetError("Wi-Fi is already off")
                radio_was_off = cfg["mode"] != "off" and on is False
                if cfg["mode"] != "off":
                    token = self.new_token()
            previous = self._active_uuid(iface)
            # the password stays only in the keyfile: what is kept in memory and on disk here never holds it
            self.pending = netcfg.PendingChange(netcfg.public(cfg), previous, self.clock(), cfg["revert_seconds"])
            self.pending.radio_was_off, self.pending.keyfile = radio_was_off, token
            self._save_state("pending")
            try:
                if self._exists(netcfg.candidate_name(iface)):  # a leftover from an earlier crash
                    self._run(["nmcli", "connection", "delete", "id", netcfg.candidate_name(iface)])
                if radio_was_off:
                    self._must(["nmcli", "radio", "wifi", "on"])
                if token:
                    self._write_keyfile(cfg, token)
                for cmd in netcfg.plan(cfg, token, self.keyfile_dir):
                    self._must(cmd, WIFI_UP_TIMEOUT if wifi and cmd[2] == "up" else None)
            except NetError as e:
                self._begin_revert()
                undone = self._attempt_revert()
                raise NetError("could not apply (%s); %s" % (e, "the previous setup was restored" if undone
                                                             else "restoring the previous setup is still being retried"))
            self.pending.restart(self.clock())  # the countdown starts now that the new network is up
            return self.status()

    def confirm(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing is waiting for confirmation")
            iface = self.pending.cfg["iface"]
            wifi_off = netcfg.is_wifi(self.pending.cfg) and self.pending.cfg["mode"] == "off"
            if not wifi_off and self._exists(netcfg.old_name(iface)):   # left from an earlier confirm
                self._run(["nmcli", "connection", "delete", "id", netcfg.old_name(iface)])
            old_exists = not wifi_off and self._exists(netcfg.profile_name(iface))
            cmds = netcfg.confirm_plan(iface, old_exists, wifi_off)
            final = cmds.pop() if old_exists else None   # deleting the old profile is last, and not required
            try:
                for cmd in cmds:
                    self._must(cmd)
            except NetError:
                # still undoable: give the old profile its name back if it had been renamed (best effort)
                if old_exists and self._exists(netcfg.old_name(iface)):
                    self._run(["nmcli", "connection", "modify", "id", netcfg.old_name(iface),
                               "connection.id", netcfg.profile_name(iface)])
                raise
            if final:
                try:
                    if self._run(final).returncode != 0:
                        self.log("pvj-netd: kept the new network, but could not delete the old profile; it goes at the next change")
                except NetError:
                    self.log("pvj-netd: kept the new network, but could not delete the old profile; it goes at the next change")
            self.pending = None
            self._save_state(None)
            return self.status()

    def revert(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing to revert")
            self._begin_revert()
            self._attempt_revert()
            return self.status()

    # --- undoing, with retries ----------------------------------------------------------
    def _begin_revert(self):
        p, self.pending = self.pending, None
        self._revert_job = {"iface": p.cfg["iface"], "uuid": p.previous_uuid, "tries": 0, "next": 0,
                            "radio_was_off": getattr(p, "radio_was_off", False), "keyfile": getattr(p, "keyfile", None)}
        self._save_state("reverting")

    def _attempt_revert(self):
        """Run the undo. True only when every command really succeeded; otherwise it stays queued."""
        job = self._revert_job
        if job is None:
            return True
        ok = True
        try:
            # a slow or failing nmcli here is a failed try, never a reason to drop the undo
            candidate_exists = self._exists(netcfg.candidate_name(job["iface"]))
        except NetError:
            candidate_exists, ok = True, False
        try:
            cmds = netcfg.revert_plan(job["iface"], candidate_exists, job["uuid"], job.get("radio_was_off", False))
        except NetError as e:   # only a bad connection id in the saved state can get here
            self.log("pvj-netd: cannot plan the undo: %s" % e)
            self._revert_job = None
            self._save_state(None)
            return False
        for cmd in cmds:
            try:
                r = self._run(cmd)
            except NetError:
                ok = False
                continue
            if r.returncode != 0 and cmd[2] != "down":  # taking an already-down profile down may complain
                ok = False
        if ok:   # a keyfile written but never loaded (a crash in between) is not removed by nmcli
            self._unlink_keyfile(job["iface"], job.get("keyfile"))
        job["tries"] += 1
        job["next"] = self.clock() + (REVERT_RETRY_SECONDS if job["tries"] < MAX_REVERT_TRIES else SLOW_RETRY_SECONDS)
        if ok:
            self._revert_job = None
            self._save_state(None)
            return True
        if job["tries"] == MAX_REVERT_TRIES:
            # Never dropped while running: a new change must not overwrite what is still owed. From now on
            # it is tried once a minute; the saved state also lets a restart take it over.
            self.log("pvj-netd: STILL FAILING to undo a network change after %d tries; trying once a minute" % job["tries"])
        return False

    def tick(self):
        """About once a second. Returns True on the tick that finished undoing an unconfirmed change."""
        with self.lock:
            if self._revert_job is None:
                if self.pending and self.pending.expired(self.clock()):
                    self._begin_revert()
                else:
                    return False
            elif self.clock() < self._revert_job["next"]:
                return False
            return self._attempt_revert()

    # --- surviving a restart or a reboot -----------------------------------------------
    def _save_state(self, phase):
        if not self.state_file:
            return
        if phase is None:
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return
        job, p = self._revert_job, self.pending
        data = {"phase": phase, "iface": (job["iface"] if job else p.cfg["iface"]),
                "previous_uuid": (job["uuid"] if job else p.previous_uuid),
                "radio_was_off": bool(job.get("radio_was_off") if job else getattr(p, "radio_was_off", False)),
                "keyfile": (job.get("keyfile") if job else getattr(p, "keyfile", None))}
        tmp = self.state_file + ".tmp"
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_file)

    def recover(self):
        """At start-up (including after a reboot): undo whatever was left unconfirmed. The saved state is
        checked strictly first: only an interface name and a connection UUID are ever taken from it."""
        if not self.state_file or not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file) as f:
                d = json.load(f)
            iface, uuid = d["iface"], d["previous_uuid"]
            if not isinstance(iface, str) or not netcfg.IFACE.fullmatch(iface):
                raise ValueError("bad interface")
            if uuid is not None and not (isinstance(uuid, str) and netcfg.UUID.fullmatch(uuid)):
                raise ValueError("bad connection id")
            radio_was_off, token = d.get("radio_was_off", False), d.get("keyfile")
            if not isinstance(radio_was_off, bool):
                raise ValueError("bad radio state")
            if token is not None and not (isinstance(token, str) and netcfg.TOKEN.fullmatch(token)):
                raise ValueError("bad keyfile name")
        except (OSError, ValueError, KeyError, TypeError):
            self.log("pvj-netd: ignoring an unreadable or invalid saved state")
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return False
        with self.lock:
            self._revert_job = {"iface": iface, "uuid": uuid, "tries": 0, "next": 0,
                                "radio_was_off": radio_was_off, "keyfile": token}
            self._attempt_revert()
        return True

    # --- request dispatch --------------------------------------------------------------------
    def handle(self, message):
        try:
            if not isinstance(message, dict):
                raise NetError("request must be an object")
            cmd = message.get("cmd")
            if cmd == "status":
                return {"ok": True, **self.status(wifi=message.get("wifi") is True)}
            if cmd == "scan":
                return {"ok": True, **self.scan(message.get("iface"))}
            if cmd == "plan":
                return {"ok": True, **self.plan(message.get("config"))}
            if cmd == "apply":
                return {"ok": True, **self.apply(message.get("config"))}
            if cmd == "confirm":
                return {"ok": True, **self.confirm()}
            if cmd == "revert":
                return {"ok": True, **self.revert()}
            raise NetError("unknown command")
        except NetError as e:
            return {"ok": False, "error": str(e)}


# --- the socket -----------------------------------------------------------------
def peer_uid(sock):
    _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


class NetServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """One thread per connection, so a stalled client only holds its own; the service serialises the real work."""

    daemon_threads = True

    def __init__(self, path, service, is_allowed):
        self.service, self.is_allowed = service, is_allowed
        if os.path.exists(path):
            os.unlink(path)
        super().__init__(path, _Handler)
        os.chmod(path, 0o660)

    def get_request(self):
        req, addr = super().get_request()
        req.settimeout(5)  # a stalled client must not block the others
        return req, addr


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            if not self.server.is_allowed(peer_uid(self.request)):
                self.wfile.write(b'{"ok": false, "error": "not allowed"}\n')
                return
            line = self.rfile.readline(MAX_LINE + 1)
            if len(line) > MAX_LINE:
                reply = {"ok": False, "error": "request too large"}
            else:
                try:
                    reply = self.server.service.handle(json.loads(line))
                except (ValueError, RecursionError):
                    reply = {"ok": False, "error": "invalid JSON"}
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except (OSError, socket.timeout):
            pass


def exchange(path, message, timeout):
    """One request line to a helper's socket and its one reply line, as bytes. Raises OSError (socket.timeout when
    the helper is slow).

    A helper refuses a caller it does not know by answering and closing WITHOUT reading. When it is quicker than
    the caller, the caller's write fails with a broken pipe while the refusal is already waiting to be read. So a
    failed write is not the end: what the helper sent is read first, and the write's error counts only if there
    was nothing."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        unsent = None
        try:
            s.sendall(json.dumps(message).encode() + b"\n")
        except socket.timeout:
            raise
        except OSError as e:                # a broken pipe or a reset: the helper has closed already
            unsent = e
        data = b""
        try:
            while not data.endswith(b"\n") and len(data) < 65536:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            raise
        except OSError:                     # closed under us: what was read before counts, if it is a whole answer
            if not data.endswith(b"\n"):
                raise
        if unsent is not None and not data:
            raise unsent
        return data
    finally:
        s.close()


class NetdClient:
    """Used by the panel: one request, one reply."""

    def __init__(self, path, timeout=180):   # a Wi-Fi apply that fails and is undone can take about two minutes
        self.path, self.timeout = path, timeout

    def request(self, message):
        try:
            return json.loads(exchange(self.path, message, self.timeout))
        except (OSError, ValueError):
            raise NetError("the network helper (pvj-netd) is not running")


def safe_state_dir():
    """A directory only root can touch, or None (then no restart safety net, said loudly)."""
    path = os.environ.get("STATE_DIRECTORY") or "/var/lib/pvj-netd"
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.stat(path)
        if st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0 and not os.path.islink(path):
            return path
    except OSError:
        pass
    print("pvj-netd: WARNING: %s is not private; pending changes will not survive a restart" % path, flush=True)
    return None


def main(argv=None):
    import grp
    import pwd
    import sys
    rundir = os.environ.get("PVJ_RUNTIME_DIR") or paths.NETD_DIR     # its own folder, root:pvj 0750 (the unit)
    os.makedirs(rundir, mode=0o750, exist_ok=True)
    allowed = {0}
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        pass
    service = NetService(state_dir=safe_state_dir(), log=lambda m: print(m, flush=True))
    if service.recover():
        print("pvj-netd: found an unconfirmed network change from before the restart and undid it", flush=True)
    server = NetServer(os.path.join(rundir, paths.NETD_SOCKET), service, lambda uid: uid in allowed)
    try:
        os.chown(server.server_address, 0, grp.getgrnam("pvj").gr_gid, follow_symlinks=False)
    except (KeyError, OSError):
        pass

    def ticker():
        while True:
            time.sleep(1)
            try:
                if service.tick():
                    print("pvj-netd: no confirmation arrived; the previous network setup was restored", flush=True)
            except Exception as e:  # never let the safety timer die
                print("pvj-netd: timer error: %r" % (e,), file=sys.stderr, flush=True)
    threading.Thread(target=ticker, daemon=True).start()
    print("pvj-netd: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
