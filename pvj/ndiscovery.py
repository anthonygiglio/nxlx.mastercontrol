# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI discovery server on the box (D85). The owner's words (2026-10-11): "let's proceed with option 2 and build
the discovery server and bake into the box."

NDI's Discovery Server is NDI's own closed program, `ndi-discovery-server`. Senders register with it and receivers
ask it, in place of (senders) or beside (receivers) mDNS. Its licence forbids handing it on, so it is never in this
repository or an image and the box never downloads it: root places it from the owner's copy
(`sudo pvj-ndi-runtime server install <folder or file>`), as the NDI runtime is placed.

Four parts, as with the NDI input (pvj/ndi.py, pvj/ndisetup.py):

* install_server: root copies the program into /opt/pvj-ndi-server after checking the copy (never through a link, a
  size cap, an ELF program for this processor, root's and writable by nobody else). Nothing is executed.
* enable / disable / refresh: root's set-up. An account and a group of its own (pvj-ndisc), the unit
  (pvj/systemd/pvj-ndi-discovery.service) and a drop-in that lets the panel into the supervisor's folder; that
  drop-in is the mark that this box has the server set up.
* Service: the supervisor that runs under that unit. It starts NDI's program only while the panel says the module is
  on, with `-bind <address> -port <port>` and nothing else, and starts it again with a growing pause when it ends.
  It checks the program file before every start and never passes a string it did not check.
* Manager: the panel's side. It reads the Owner's choice (which network, which port), finds that network's address
  now, tells the supervisor, and tells it again when the address changed.

A discovery server is not authentication: whatever can reach its port can register with it and ask it. What the
sandbox keeps from it is in the unit and in SECURITY.md.
"""
import errno
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time

from . import ndisetup, paths
from .ndi import ELF_MACHINES, SDK_FOLDERS, NdiError, PRIVATE_NETS, _text, box_machine, elf_machine, same_folder_target

SERVER_NAME = "ndi-discovery-server"
SERVER_DIR = "/opt/pvj-ndi-server"            # root's; nothing else is in it
SERVER_PATH = SERVER_DIR + "/" + SERVER_NAME
SERVER_MAX_BYTES = 32 * 1024 * 1024           # NDI's program is about 1 MB (6.3.2); a file far larger is not it
CONFIG_BESIDE = "ndi-discovery-service.v1.json"      # NDI's program also reads this name lying beside it; the box never writes one
ET_EXEC, ET_DYN = 2, 3                        # an ELF program (a position-independent one is ET_DYN)
ACCOUNT = GROUP = "pvj-ndisc"
UNIT = "pvj-ndi-discovery.service"
UNIT_PATH = ndisetup.SYSTEMD + "/" + UNIT
WEB_DROPIN = ndisetup.SYSTEMD + "/pvj-web.service.d/50-pvj-ndisc.conf"       # also the mark that the server is set up here
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "systemd", UNIT)
WEB_TEXT = ("# Written by pvj-ndi-runtime (pvj/ndiscovery.py) when the NDI discovery server was set up on this box; removed by\n"
            "# 'pvj-ndi-runtime server remove'. The panel may reach the server's supervisor (D85). This file is also the mark.\n"
            "[Service]\nSupplementaryGroups=pvj-ndisc\nEnvironment=PVJ_NDISC_DIR=/run/pvj-ndisc\n")
COMMAND = 'sudo pvj-ndi-runtime server install "/path/to/NDI SDK for Linux"'
NOT_SET_UP = "the NDI discovery server is not set up on this box; someone with a keyboard or SSH on the box runs: " + COMMAND
SOCKET = "ndisc.sock"
DEFAULT_PORT = 5959                           # NDI's own default
MIN_PORT, MAX_PORT = 1024, 65535              # the account has no privileges: no port below 1024
ALL = "0.0.0.0"
NETWORKS = ("wired", "wifi", "all")           # the Owner's choice; an address is found for it at each look
BACKOFF_FIRST, BACKOFF_MAX, SETTLED = 2.0, 60.0, 30.0      # seconds between starts; a run this long resets the pause
MAX_LINE = 200


class SetupError(ndisetup.SetupError):
    pass


# ---- the program file -----------------------------------------------------------------------------------------------
def find_server(folder, machine):
    """NDI's program for this processor inside an unpacked folder: the NDI SDK and the Discovery Server package both
    keep it at bin/<architecture>/ndi-discovery-server. None when it is not there."""
    for arch in SDK_FOLDERS.get(machine, ()):
        path = os.path.join(folder, "bin", arch, SERVER_NAME)
        if os.path.lexists(path):
            return path
    return None


def _elf_type(path):
    try:
        with open(path, "rb") as f:
            head = f.read(18)
    except OSError:
        return None
    if len(head) < 18 or head[:4] != b"\x7fELF" or head[5] not in (1, 2):
        return None
    return int.from_bytes(head[16:18], "little" if head[5] == 1 else "big")


def server_problem(path=SERVER_PATH, owner_uids=(0,), machine=None):
    """None when the program file is there and may be started, else the reason in words: a plain file and not a
    link, its owner root, writable by nobody else, in a folder that is root's and that nobody else can change, and
    an ELF program for this processor."""
    try:
        st, folder = os.lstat(path), os.lstat(os.path.dirname(path))
    except OSError:
        return "NDI's discovery server program is not on this box yet"
    if (not stat.S_ISREG(st.st_mode) or st.st_uid not in owner_uids or st.st_mode & 0o022 or st.st_size > SERVER_MAX_BYTES
            or not stat.S_ISDIR(folder.st_mode) or folder.st_uid not in owner_uids or folder.st_mode & 0o022):
        return "NDI's discovery server program is not a plain file that only root can change; install it again with pvj-ndi-runtime"
    extra = beside(path)
    if extra:
        # NDI's program reads a configuration file lying beside it (ndi-discovery-service.v1.json), which would
        # count for more than the arguments it is given. The box writes nothing into this folder but the program,
        # so anything else there is somebody else's, and the program is not started with it.
        return ("%s lies beside NDI's program in %s and this box did not put it there; remove it (the program is not started while it is there)"
                % (", ".join(extra), os.path.dirname(path)))
    found, want = elf_machine(path), machine if machine is not None else box_machine()
    if found is None or _elf_type(path) not in (ET_EXEC, ET_DYN):
        return "the file is not a program (NDI's discovery server is one)"
    if found[0] != want:
        return "NDI's discovery server program is for %s, this box is %s" % (ELF_MACHINES.get(found[0], "another processor"),
                                                                           ELF_MACHINES.get(want, "another kind"))
    return None


def beside(path):
    """The other names in the program's folder, sorted (at most a few are named); [] when it is alone there. The
    name being judged and this module's own temporary copy of the moment do not count."""
    folder, me = os.path.dirname(path), os.path.basename(path)
    try:
        names = sorted(n for n in os.listdir(folder) if n not in (me, SERVER_NAME) and not (n.startswith(".ndisc-") and me.startswith(".ndisc-")))
    except OSError:
        return []
    return [_text(n, 80) for n in names[:4]]


def install_server(source, dest_dir=SERVER_DIR, machine=None, chown=True):
    """Copy NDI's program from `source` (the file, or an unpacked NDI folder) to dest_dir/SERVER_NAME; returns the
    path. Raises NdiError in words. As with the library (ndi.install_runtime): root reads a name somebody else may
    control, so never through a link in the last part of the name, never waiting on a pipe; the COPY is checked and
    only then given its name and its mode. Nothing is executed."""
    machine = machine if machine is not None else box_machine()
    if os.path.isdir(source):
        path = find_server(source, machine)
        if path is None:
            raise NdiError("no %s for this box (%s) under %s/bin" % (SERVER_NAME, ELF_MACHINES.get(machine, "unknown processor"), source))
    else:
        path = same_folder_target(source)
    try:
        src = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as e:
        raise NdiError("%s cannot be read as a file (%s)" % (path, "it is a link" if e.errno in (errno.ELOOP, errno.EMLINK) else e.strerror or "error"))
    tmp, made, placed = None, False, False
    try:
        st = os.fstat(src)
        if not stat.S_ISREG(st.st_mode):
            raise NdiError("%s is not a file" % path)
        if st.st_size > SERVER_MAX_BYTES:
            raise NdiError("%s is too large to be NDI's discovery server" % path)
        made = not os.path.lexists(dest_dir)
        os.makedirs(dest_dir, mode=0o755, exist_ok=True)
        if os.path.islink(dest_dir):
            raise NdiError("%s is a link; nothing was installed" % dest_dir)
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".ndisc-")       # 0600: nobody runs it before every check has passed
        with os.fdopen(fd, "wb") as out:
            copied = 0
            while True:
                chunk = os.read(src, min(1024 * 1024, SERVER_MAX_BYTES + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > SERVER_MAX_BYTES:                            # it grew while it was read
                    raise NdiError("%s is too large to be NDI's discovery server" % path)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if chown:
            os.chown(tmp, 0, 0)
            os.chown(dest_dir, 0, 0)
        os.chmod(dest_dir, 0o755)
        problem = server_problem(tmp, owner_uids=(0,) if chown else (os.getuid(),), machine=machine)
        if problem:
            raise NdiError(problem.replace("; install it again with pvj-ndi-runtime", "") + "; nothing was installed")
        os.chmod(tmp, 0o755)                                             # a program only now
        dest = os.path.join(dest_dir, SERVER_NAME)
        os.replace(tmp, dest)
        placed = True
        return dest
    finally:
        os.close(src)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if made and not placed:
            try:
                os.rmdir(dest_dir)
            except OSError:
                pass


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- root's set-up ----------------------------------------------------------------------------------------------------
def opted_in(root=""):
    """True when this box (or the staged tree under `root`) has the discovery server set up: the panel's drop-in is there."""
    path = root + WEB_DROPIN
    return os.path.isfile(path) and not os.path.islink(path)


def check_paths(root=""):
    for path in (UNIT_PATH, WEB_DROPIN):
        full, folder = root + path, os.path.dirname(root + path)
        if os.path.islink(folder) or (os.path.lexists(folder) and not os.path.isdir(folder)):
            raise SetupError("%s is not a plain folder; nothing was changed. Look at what is there and remove it by hand" % folder)
        if os.path.islink(full) or (os.path.lexists(full) and not os.path.isfile(full)):
            raise SetupError("%s is a link or a folder, not the plain file this set-up writes; nothing was changed. "
                             "Look at what is there and remove it by hand" % full)


def write_units(root="", prefix=ndisetup.DEFAULT_PREFIX, template=None):
    """The unit, then the panel's drop-in (the mark, written last)."""
    text = ndisetup.unit_text(prefix, template or TEMPLATE)
    ndisetup._write(root + UNIT_PATH, text)
    ndisetup._write(root + WEB_DROPIN, WEB_TEXT)


def remove_units(root=""):
    """The mark first. Returns what could not be removed, in words. The drop-in's folder goes only when empty (the
    NDI input's drop-in may be in it)."""
    left = []
    for path in (WEB_DROPIN, UNIT_PATH):
        try:
            os.unlink(root + path)
        except FileNotFoundError:
            pass
        except OSError as e:
            left.append("%s could not be removed (%s); remove it by hand" % (root + path, e.strerror or "error"))
    try:
        os.rmdir(os.path.dirname(root + WEB_DROPIN))
    except OSError:
        pass
    return left


def check(root="", account=ndisetup.check_account):
    """Everything that can refuse the set-up, looked at before anything on the box is changed."""
    ndisetup.unit_text(ndisetup.read_prefix(root), TEMPLATE)
    check_paths(root)
    account(name=ACCOUNT)


def enable(run=ndisetup._run, say=print, systemd=ndisetup._systemd_runs, account=ndisetup.ensure_account, root="", sleep=None):
    """Set the server up on this box: the account, the unit, the panel's drop-in; the supervisor is started (it runs
    nothing of NDI until the module is switched on in the panel) and the panel is restarted into the new group.
    Root, on the box. Safe to run again. A step that fails after the files were written undoes them on a box that
    was not set up before. Returns True when the supervisor is running (or enabled, where no systemd runs)."""
    sleep = sleep or time.sleep
    check(root)
    was = opted_in(root)
    account(run=run, say=say, name=ACCOUNT)
    try:
        write_units(root, ndisetup.read_prefix(root))
        if not systemd():
            if run(["systemctl", "enable", UNIT]) != 0:
                raise SetupError("could not enable %s" % UNIT)
            say("systemd is not running here: the discovery server's supervisor is enabled and starts at the next boot")
            return True
        if run(["systemctl", "daemon-reload"]) != 0:
            raise SetupError("systemctl daemon-reload failed; the discovery server's supervisor was not started")
        if run(["systemctl", "enable", UNIT]) != 0:
            raise SetupError("could not enable %s" % UNIT)
        if run(["systemctl", "restart", UNIT]) != 0:
            raise SetupError("could not start %s; see: journalctl -u pvj-ndi-discovery -n 30" % UNIT)
    except (ndisetup.SetupError, OSError):
        if not was:
            run(["systemctl", "disable", "--now", UNIT] if systemd() else ["systemctl", "disable", UNIT])
            for line in remove_units(root):
                say(line)
            if systemd():
                run(["systemctl", "daemon-reload"])
            say("the set-up failed and was undone: this box has no NDI discovery server set up. NDI's program and the "
                "account and group %s stay (sudo pvj-ndi-runtime server remove takes the program off)." % ACCOUNT)
        raise
    running = True
    for _ in range(ndisetup.STAYS_UP_SECONDS):
        sleep(1)
        if run(["systemctl", "is-active", "--quiet", UNIT]) != 0:
            running = False
            break
    say("restarting the panel so it may reach the discovery server's supervisor")
    if run(["systemctl", "try-restart", "pvj-web.service"]) != 0:
        say("the panel did not restart; restart the box before using the discovery server")
    if running:
        say("the NDI discovery server is set up on this box. Nothing of it listens yet: an owner switches it on in the "
            "panel under Setup, NDI discovery server.")
    else:
        say("the NDI discovery server is set up on this box, but its supervisor is NOT running: it was started and did not "
            "stay up. See: journalctl -u pvj-ndi-discovery -n 30")
    return running


def disable(run=ndisetup._run, say=print, systemd=ndisetup._systemd_runs, root=""):
    """Take the server's set-up off again: no supervisor, no unit, nothing of it in the panel's unit."""
    was = opted_in(root) or os.path.lexists(root + UNIT_PATH)
    if os.path.lexists(root + UNIT_PATH):
        run(["systemctl", "disable", "--now", UNIT] if systemd() else ["systemctl", "disable", UNIT])
    left = remove_units(root)
    for line in left:
        say(line)
    if systemd():
        run(["systemctl", "daemon-reload"])
        if was:
            say("restarting the panel without the discovery server's group")
            if run(["systemctl", "try-restart", "pvj-web.service"]) != 0:
                say("the panel did not restart; restart the box")
    if was:
        say("the NDI discovery server's set-up is removed from this box. Left in place: the account and group %s "
            "(sudo deluser %s; sudo delgroup %s)." % (ACCOUNT, ACCOUNT, GROUP))
    if left:
        raise SetupError("not everything of the discovery server could be removed: " + "; ".join(left))


def refresh(root="", prefix=None, run=ndisetup._run, say=print, account=ndisetup.ensure_account):
    """For the installer, at every install and update: write the unit and the drop-in of a box that has the server
    set up again. A box without the mark is left exactly as it is. Returns whether the box has it set up."""
    if not opted_in(root):
        return False
    if not root:
        account(run=run, say=say, name=ACCOUNT)
    write_units(root, prefix or ndisetup.read_prefix(root))
    return True


def remove_program(dest_dir=SERVER_DIR):
    """Take NDI's program off the box; True when there was one."""
    was = os.path.lexists(os.path.join(dest_dir, SERVER_NAME))
    try:
        os.unlink(os.path.join(dest_dir, SERVER_NAME))
    except FileNotFoundError:
        pass
    try:
        os.rmdir(dest_dir)                 # only when nothing else is in it
    except OSError:
        pass
    return was


def server_main(argv, out=sys.stdout):
    """`pvj-ndi-runtime server install <folder or file> | remove | status | refresh ...`."""
    say = lambda m: print("pvj-ndi-runtime: %s" % m, file=out)      # noqa: E731
    usage = ("usage: pvj-ndi-runtime server install <the unpacked NDI SDK or Discovery Server folder, or ndi-discovery-server> | status | remove\n"
             "install: puts NDI's discovery server program on this box from YOUR copy and sets up the service that can run it\n"
             "  (an account of its own, pvj-ndisc). Nothing of it listens until an owner switches it on in the panel.\n"
             "NDI's Discovery Server is proprietary: get it from https://ndi.video/ and read its licence first. It is never\n"
             "part of nxlx.mastercontrol and is never downloaded by the box. NDI(R) is a registered trademark of Vizrt NDI AB.")
    if argv and argv[0] == "refresh":
        root, prefix, rest = "", None, list(argv[1:])
        while rest:
            if len(rest) >= 2 and rest[0] == "--root":
                root = rest[1].rstrip("/")
            elif len(rest) >= 2 and rest[0] == "--prefix":
                prefix = rest[1]
            else:
                print("usage: pvj-ndi-runtime server refresh [--root DIR] [--prefix DIR]", file=out)
                return 2
            rest = rest[2:]
        if not root and os.geteuid() != 0:
            say("run it with sudo")
            return 1
        try:
            if refresh(root, prefix, say=say):
                say("this box has the NDI discovery server set up; its unit was written again")
            return 0
        except (ndisetup.SetupError, OSError) as e:
            say(str(e))
            return 1
    if argv == ["status"]:
        print(server_problem() or "NDI's discovery server program is in place: %s" % SERVER_PATH, file=out)
        print("the NDI discovery server is set up on this box (the service pvj-ndi-discovery is installed)" if opted_in()
              else "the NDI discovery server is not set up on this box; to set it up: %s" % COMMAND, file=out)
        return 0
    if argv and argv[0] in ("install", "remove") and os.geteuid() != 0:
        say("run it with sudo")
        return 1
    if len(argv) == 2 and argv[0] == "install":
        try:
            check()
            placed = install_server(argv[1])
            print("installed %s" % placed, file=out)
            print("sha256 %s  (compare with: sha256sum <your copy of ndi-discovery-server>)" % sha256_of(placed), file=out)
            return 0 if enable(say=say) else 1
        except (NdiError, ndisetup.SetupError, OSError) as e:
            say(str(e))
            return 1
    if argv == ["remove"]:
        code = 0
        try:
            disable(say=say)
        except (ndisetup.SetupError, OSError) as e:
            say(str(e))
            code = 1
        try:
            if remove_program():
                print("removed NDI's discovery server program (%s)" % SERVER_PATH, file=out)
        except OSError as e:
            say("%s could not be removed (%s)" % (SERVER_PATH, e.strerror or "error"))
            code = 1
        return code
    print(usage, file=out)
    return 2


# ---- what the Owner chose, and its checks (both sides use them) -------------------------------------------------------
def clean_bind(text):
    """The address the server listens on: one private or link-local IPv4 address of the box, or 0.0.0.0."""
    if text == ALL:
        return ALL
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}", text):
        raise NdiError("the address to listen on must be an IPv4 address")
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        raise NdiError("the address to listen on must be an IPv4 address")
    if not any(ip in net for net in PRIVATE_NETS):
        raise NdiError("the discovery server listens on a private network only (10.x, 172.16 to 172.31, 192.168.x, 169.254.x)")
    return str(ip)


def clean_port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_PORT <= value <= MAX_PORT:
        raise NdiError("the port is a whole number from %d to %d (NDI's own is %d)" % (MIN_PORT, MAX_PORT, DEFAULT_PORT))
    return value


def validate_saved(cfg):
    """The stored `ndi_discovery` section: {"network": "wired" | "wifi" | "all", "port": n}. Raises NdiError."""
    if not isinstance(cfg, dict) or set(cfg) - {"network", "port"}:
        raise NdiError("ndi_discovery must be an object with network and port")
    if cfg.get("network", "wired") not in NETWORKS:
        raise NdiError("ndi_discovery.network must be one of %s" % ", ".join(NETWORKS))
    return {"network": cfg.get("network", "wired"), "port": clean_port(cfg.get("port", DEFAULT_PORT))}


def saved(section):
    """What can be used of a stored section a person may have edited: anything wrong is the default."""
    try:
        return validate_saved(section)
    except NdiError:
        return {"network": "wired", "port": DEFAULT_PORT}


def box_addresses(ip_json):
    """[(interface name, address)] of the box's private and link-local IPv4 addresses, from `ip -j -4 addr show`."""
    out = []
    for row in ip_json if isinstance(ip_json, list) else []:
        name = row.get("ifname") if isinstance(row, dict) else None
        if not isinstance(name, str) or name == "lo":
            continue
        for a in row.get("addr_info") or []:
            try:
                out.append((name, clean_bind(a.get("local"))))
            except (NdiError, AttributeError):
                pass
    # Not the remote support tunnel or any other tunnel: a discovery server is for the room's network.
    return [(n, a) for n, a in out if a != ALL and not n.startswith(("wg", "tun", "tap", "docker", "veth", "br-"))]


def address_for(network, addresses):
    """The address to bind for the Owner's choice, or None when that network has none now. "wired": the first
    address of an Ethernet interface (eth*, en*); "wifi": of a wireless one (wl*); "all": every address (0.0.0.0)."""
    if network == "all":
        return ALL
    want = ("eth", "en") if network == "wired" else ("wl",)
    for name, address in addresses:
        if name.startswith(want):
            return address
    return None


# ---- the supervisor (runs under the unit, as pvj-ndisc) ---------------------------------------------------------------
def clean_line(raw):
    """One line the program printed, for the page: printable ASCII only, bounded."""
    text = raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw)
    return "".join(ch if " " <= ch <= "~" else " " for ch in text).strip()[:MAX_LINE]


class Service:
    """What the supervisor answers, one JSON line each: status and configure. It starts nothing until the panel has
    said the module is on, and it knows no settings but what the panel sends."""

    def __init__(self, program=SERVER_PATH, log=print, popen=subprocess.Popen, clock=time.monotonic, problem=server_problem,
                 first=BACKOFF_FIRST, most=BACKOFF_MAX, settled=SETTLED):
        self.program, self.log, self._popen, self._clock, self._problem = program, log, popen, clock, problem
        self._first, self._most, self._settled = first, most, settled
        self.lock = threading.RLock()
        self.on, self.bind, self.port = False, None, DEFAULT_PORT
        self.child, self.gen, self.state, self.last, self.message = None, 0, "off", "", ""
        self.started, self.restarts = None, 0
        self._wake = threading.Event()

    def argv(self):
        """Exactly this and nothing else is ever run: the program root placed, "-service", the checked address, the
        checked port. "-service" is how NDI's own systemd unit starts the program (its example unit: Type=simple,
        ExecStart=... -service; the program's help calls it "service mode for linux/unix"): no console is waited on.
        Whether the program honours -bind and -port in that mode has not been seen (it has never been run here):
        _watch compares where the program SAYS it listens with what was asked, and stops it if they differ."""
        return [self.program, "-service", "-bind", clean_bind(self.bind), "-port", str(clean_port(self.port))]

    def _elsewhere(self, line):
        """Under the lock. True when a line the program printed says it listens on an address that is not the one it
        was given (it prints "Listening... [...]"). A line without an address says nothing either way."""
        if "isten" not in line or self.bind in (None, ALL):
            return False
        found = re.findall(r"(?<![0-9.])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9.])", line)
        return bool(found) and self.bind not in found

    def configure(self, message):
        on = message.get("on")
        if not isinstance(on, bool):
            return {"ok": False, "error": "on must be true or false"}
        try:
            bind = clean_bind(message.get("bind")) if on else None
            port = clean_port(message.get("port", DEFAULT_PORT)) if on else DEFAULT_PORT
        except NdiError as e:
            return {"ok": False, "error": str(e)}
        with self.lock:
            same = (on, bind, port) == (self.on, self.bind, self.port)
            if same and (not on or self.state in ("running", "waiting")):
                return self.status()
            self._stop()
            self.on, self.bind, self.port, self.restarts = on, bind, port, 0
            if on:
                self._start(self._first)
            else:
                self.state, self.message = "off", ""
        return self.status()

    def _start(self, pause):
        """Under the lock. Start the program, or say why not. `pause` is how long to wait before the start after
        this one, should this one end."""
        self.gen += 1
        gen = self.gen
        problem = self._problem(self.program)
        if problem:
            self.state, self.message, self.child = "missing", problem, None
            return
        try:
            child = self._popen(self.argv(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                close_fds=True, cwd="/", env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
                                start_new_session=True)      # a session and a process group of its own: _end takes all of it
        except (OSError, ValueError) as e:
            self.state, self.message, self.child = "failed", "the program could not be started (%s)" % (getattr(e, "strerror", None) or type(e).__name__), None
            self._again(gen, pause)
            return
        self.child, self.state, self.message, self.started = child, "running", "", self._clock()
        self.log("pvj-ndi-discovery: started NDI's discovery server on %s port %d" % (self.bind, self.port))
        threading.Thread(target=self._watch, args=(child, gen, pause), name="ndisc-watch", daemon=True).start()

    def _watch(self, child, gen, pause):
        try:
            for raw in iter(child.stdout.readline, b""):
                line = clean_line(raw)
                if line:
                    with self.lock:
                        if gen == self.gen:
                            self.last = line
                            if self._elsewhere(line):
                                # It listens where nobody chose: it is ended and NOT started again. The Owner sees why.
                                self.log("pvj-ndi-discovery: NDI's program says %r, not the address it was given (%s); stopping it" % (line, self.bind))
                                self.gen += 1
                                self.child, self.state = None, "failed"
                                self.message = "NDI's program listened somewhere other than the address chosen (%s) and was stopped; it is not started again until the setting is changed or switched off and on" % self.bind
                                try:
                                    os.killpg(child.pid, 9)
                                except OSError:
                                    pass
        except (OSError, ValueError):
            pass
        code = child.wait()
        try:
            child.stdout.close()
        except (OSError, ValueError):
            pass
        with self.lock:
            if gen != self.gen or not self.on:
                return
            ran = self._clock() - (self.started or self._clock())
            self.state, self.child = "failed", None
            self.message = "the program ended (exit %s) after %d seconds" % (code, int(ran))
            self.log("pvj-ndi-discovery: NDI's discovery server ended (exit %s); starting it again" % code)
            self._again(gen, self._first if ran >= self._settled else pause)

    def _again(self, gen, pause):
        """Under the lock: start once more after `pause` seconds, twice as long the next time, unless told otherwise meanwhile."""
        self._wake.clear()

        def later():
            self._wake.wait(pause)
            with self.lock:
                if gen == self.gen and self.on:
                    self.restarts += 1
                    self._start(min(self._most, pause * 2))
        threading.Thread(target=later, name="ndisc-again", daemon=True).start()

    def _stop(self):
        """Under the lock: end the program if it runs, and make every watcher of it stand down."""
        self.gen += 1
        self._wake.set()
        child, self.child = self.child, None
        if child is not None:
            self._end(child)
            self.log("pvj-ndi-discovery: NDI's discovery server was stopped")

    @staticmethod
    def _end(child):
        """End the program and whatever it started: its whole process group (it is the leader of a session of its
        own), politely and then for good. A group that is gone already is fine."""
        import signal
        for sig, pause in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
            try:
                os.killpg(child.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            except OSError:
                try:
                    child.kill() if sig == signal.SIGKILL else child.terminate()
                except OSError:
                    pass
            try:
                child.wait(timeout=pause)
                if sig == signal.SIGTERM:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)       # the leader went; nothing of its group stays
                    except OSError:
                        pass
                return
            except subprocess.TimeoutExpired:
                continue

    def status(self):
        with self.lock:
            problem = self._problem(self.program)
            return {"ok": True, "on": self.on, "bind": self.bind, "port": self.port, "state": self.state, "message": self.message,
                    "last": self.last, "restarts": self.restarts, "installed": problem is None, "problem": problem or "",
                    "seconds": int(self._clock() - self.started) if self.state == "running" and self.started is not None else 0}

    def close(self):
        with self.lock:
            self.on = False
            self._stop()
            self.state = "off"

    def handle(self, message):
        cmd = message.get("cmd") if isinstance(message, dict) else None
        if cmd == "status":
            return self.status()
        if cmd == "configure":
            return self.configure(message)
        return {"ok": False, "error": "unknown command"}


def main(argv=None):
    import pwd
    from .netd import NetServer
    rundir = os.environ.get("PVJ_NDISC_DIR") or paths.NDISC_DIR
    os.makedirs(rundir, mode=0o750, exist_ok=True)
    allowed = set()
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        allowed.add(os.getuid())                   # on a desk: whoever runs it
    service = Service(os.environ.get("PVJ_NDISC_PROGRAM") or SERVER_PATH, log=lambda m: print(m, flush=True))
    server = NetServer(os.path.join(rundir, SOCKET), service, lambda uid: uid in allowed)
    print("pvj-ndi-discovery: ready (%s)" % (server_problem(service.program) or "NDI's program is in place; idle until the panel says on"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
    return 0


# ---- the panel's side -------------------------------------------------------------------------------------------------
class Manager:
    """The panel's handle on the supervisor. `client` is an ndi.Client on the supervisor's socket (None where the
    box has no set-up); `data` returns the settings; `enabled` says whether the module is on; `addresses` returns
    the box's [(interface, address)] now."""

    def __init__(self, client, data, enabled, addresses, log=print):
        self.client, self._data, self._enabled, self._addresses, self.log = client, data, enabled, addresses, log
        self.setup = client is not None
        self._sent = None

    def wanted(self):
        """(on, bind or None, port, the saved choice). Never raises."""
        try:
            cfg = saved(self._data().get("ndi_discovery"))
            on = bool(self._enabled())
            bind = address_for(cfg["network"], self._addresses()) if on else None
        except Exception:
            cfg, on, bind = saved(None), False, None
        return on, bind, cfg["port"], cfg

    def sync(self):
        """Tell the supervisor what is wanted now. With the module on and no address on the chosen network, the
        server is stopped: it never falls back to another network by itself."""
        if not self.setup:
            return {"ok": False}
        on, bind, port, _cfg = self.wanted()
        message = {"cmd": "configure", "on": bool(on and bind), "bind": bind, "port": port}
        try:
            reply = self.client.request(message, timeout=10)
        except Exception as e:
            if not isinstance(e, NdiError):
                self.log("pvj-web: the discovery server's supervisor was not told the settings: %s" % type(e).__name__)
            return {"ok": False}
        self._sent = (message["on"], bind, port)
        return reply

    def tick(self):
        """Now and then (server.py): the address of the chosen network may have changed (DHCP); the server is then
        started again on the new one. Also brings a supervisor that started after the panel up to date."""
        if not self.setup:
            return False
        on, bind, port, _cfg = self.wanted()
        want = (bool(on and bind), bind, port)
        if want == self._sent:
            st = self.client.status()
            if st.get("ok") is not True or (st.get("on"), st.get("bind") if st.get("on") else bind, st.get("port") if st.get("on") else port) == want:
                return False
        self.sync()
        return True

    def own_server(self):
        """ "address:port" of this box's own server for this box's own NDI input, or None while it is off or has no
        address. With "all the box's networks" it is the first address the box has."""
        on, bind, port, _cfg = self.wanted()
        if not (self.setup and on and bind):
            return None
        if bind == ALL:
            rows = self._addresses()
            if not rows:
                return None
            bind = rows[0][1]
        return bind if port == DEFAULT_PORT else "%s:%d" % (bind, port)

    def status(self):
        """What the page shows. Every string and number from the supervisor is checked again here."""
        on, bind, port, cfg = self.wanted()
        st = self.client.status() if self.setup else {"ok": False}
        up = st.get("ok") is True
        state = st.get("state") if up and st.get("state") in ("off", "running", "failed", "missing", "waiting") else ""
        try:
            now_bind = clean_bind(st.get("bind")) if up and st.get("on") is True else None
            now_port = clean_port(st.get("port")) if up and st.get("on") is True else None
        except NdiError:
            now_bind = now_port = None
        try:
            rows = [{"interface": n, "address": a} for n, a in self._addresses()]
        except Exception:
            rows = []
        show = bind if bind and bind != ALL else (rows[0]["address"] if bind == ALL and rows else None)
        return {"setup": self.setup, "supervisor": up, "on": on, "network": cfg["network"], "port": port, "networks": list(NETWORKS),
                "addresses": rows, "bind": bind, "default_port": DEFAULT_PORT, "min_port": MIN_PORT, "max_port": MAX_PORT,
                "installed": up and st.get("installed") is True, "problem": _text(st.get("problem")) if up else "",
                "state": state, "message": _text(st.get("message")) if up else "", "last": clean_line(_text(st.get("last")) or "") if up else "",
                "restarts": st.get("restarts") if up and isinstance(st.get("restarts"), int) and not isinstance(st.get("restarts"), bool) else 0,
                "seconds": st.get("seconds") if up and isinstance(st.get("seconds"), int) and not isinstance(st.get("seconds"), bool) else 0,
                "listening": {"address": now_bind, "port": now_port} if state == "running" and now_bind else None,
                "tell": ("%s:%d" % (show, port)) if show else "",
                "install": {"command": COMMAND, "get": "https://ndi.video/"}}
