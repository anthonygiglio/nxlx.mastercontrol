# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI discovery server on the box (D85): what root's command accepts as NDI's program, what the supervisor runs
and with which arguments, who may switch and set it, what the page is told, and what an older release makes of the
settings. NDI's program itself is never run here: a small script stands in for it."""
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from pvj import boxcare, ndi, ndiscovery as D, ndisetup, support
from tests.test_ndi import wait
from tests.test_ndi_pads import MASTER, REPO, PadBase
from tests.test_roles import CONTROLLER_DEVICES, TUNNEL

ARM64, X86 = 183, 62
FAKE = """#!/bin/sh
echo "fake discovery server $*"
echo "$*" >> "%s"
%s
"""


def elf(machine=ARM64, kind=D.ET_DYN, size=4096):
    """A file that begins as an ELF program of this processor does (64 bit, little endian); the rest is filler."""
    return b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HH", kind, machine) + b"\0" * (size - 20)


class ProgramFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dest = os.path.join(self.tmp, "opt")

    def put(self, name, data, sub=""):
        folder = os.path.join(self.tmp, sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def install(self, source, machine=ARM64):
        return D.install_server(source, dest_dir=self.dest, machine=machine, chown=False)

    def test_the_program_is_found_in_the_sdk_and_in_the_discovery_package_and_placed_as_roots_program(self):
        self.put(D.SERVER_NAME, elf(), "sdk/bin/aarch64-rpi4-linux-gnueabi")
        self.put(D.SERVER_NAME, elf(X86), "sdk/bin/x86_64-linux-gnu")
        placed = self.install(os.path.join(self.tmp, "sdk"))
        self.assertEqual(placed, os.path.join(self.dest, D.SERVER_NAME))
        st = os.lstat(placed)
        self.assertEqual((stat.S_IMODE(st.st_mode), stat.S_ISREG(st.st_mode)), (0o755, True))
        self.assertEqual(stat.S_IMODE(os.lstat(self.dest).st_mode), 0o755)
        self.assertEqual(os.listdir(self.dest), [D.SERVER_NAME])                  # no temporary file is left
        with open(placed, "rb") as f:
            self.assertEqual(f.read(), elf())                                    # this processor's, not the other one
        self.assertIsNone(D.server_problem(placed, owner_uids=(os.getuid(),), machine=ARM64))
        self.assertEqual(len(D.sha256_of(placed)), 64)
        # the file itself is taken too, and a second install replaces the first
        self.assertEqual(self.install(self.put("server", elf(size=8192))), placed)
        self.assertEqual(os.path.getsize(placed), 8192)

    def test_what_is_refused_and_that_a_refusal_leaves_nothing(self):
        cases = {
            "wrong processor": self.put("x86", elf(X86)),
            "not a program at all": self.put("text", b"#!/bin/sh\necho hello\n" + b" " * 100),
            "a library, not a program": self.put("lib", elf(kind=1)),
            "too large": self.put("big", elf(size=D.SERVER_MAX_BYTES + 1)),
            "empty": self.put("empty", b""),
        }
        for what, path in cases.items():
            with self.assertRaises(ndi.NdiError, msg=what):
                self.install(path)
            self.assertFalse(os.path.lexists(self.dest), "%s left the folder behind" % what)
        good = self.put("good", elf())
        # a link that leaves its folder, a link to nothing, a folder without the program, a pipe
        os.symlink(good, os.path.join(self.tmp, "abs-link"))
        os.symlink("../good", self.put("x", b"", "sub") + "-link")
        os.symlink("nothing-here", os.path.join(self.tmp, "dangling"))
        os.mkfifo(os.path.join(self.tmp, "pipe"))
        os.makedirs(os.path.join(self.tmp, "empty-sdk", "bin", "aarch64-rpi4-linux-gnueabi"))
        for name in ("abs-link", "sub/x-link", "dangling", "pipe", "empty-sdk", "no-such-file"):
            with self.assertRaises(ndi.NdiError, msg=name):
                self.install(os.path.join(self.tmp, name))
            self.assertFalse(os.path.lexists(self.dest), name)
        # inside an SDK folder, a link in the program's place is not followed
        os.symlink(good, os.path.join(self.tmp, "empty-sdk", "bin", "aarch64-rpi4-linux-gnueabi", D.SERVER_NAME))
        with self.assertRaises(ndi.NdiError) as c:
            self.install(os.path.join(self.tmp, "empty-sdk"))
        self.assertIn("link", str(c.exception))
        # a refused file does not take a good one away
        placed = self.install(good)
        with self.assertRaises(ndi.NdiError):
            self.install(cases["wrong processor"])
        with open(placed, "rb") as f:
            self.assertEqual(f.read(), elf())

    def test_nothing_is_executed_during_an_install(self):
        ran = os.path.join(self.tmp, "ran")
        body = b"\x7fELF"                                                       # would not run anyway; the point is that nobody tries
        real = subprocess.Popen
        calls = []
        subprocess.Popen = lambda *a, **k: calls.append(a) or real(*a, **k)
        self.addCleanup(setattr, subprocess, "Popen", real)
        try:
            self.install(self.put("prog", elf()))
        finally:
            subprocess.Popen = real
        self.assertEqual((calls, os.path.exists(ran), body[:1]), ([], False, b"\x7f"))

    def test_the_file_that_would_be_started_is_judged_every_time(self):
        placed = self.install(self.put("prog", elf()))
        mine = (os.getuid(),)
        self.assertIsNone(D.server_problem(placed, mine, ARM64))
        self.assertIn("is for", D.server_problem(placed, mine, X86))
        self.assertIn("only root can change", D.server_problem(placed, (0,) if os.getuid() else (12345,), ARM64))       # not root's
        os.chmod(placed, 0o775)
        self.assertIn("only root can change", D.server_problem(placed, mine, ARM64))
        os.chmod(placed, 0o755)
        os.chmod(self.dest, 0o777)
        self.assertIn("only root can change", D.server_problem(placed, mine, ARM64), "a folder anyone can write is not root's folder")
        os.chmod(self.dest, 0o755)
        os.rename(placed, placed + ".real")
        os.symlink(placed + ".real", placed)
        self.assertIn("only root can change", D.server_problem(placed, mine, ARM64), "a link is not the program")
        os.unlink(placed)
        self.assertEqual(D.server_problem(placed, mine, ARM64), "NDI's discovery server program is not on this box yet")
        self.assertTrue(D.remove_program(self.dest) is False)


class SetUp(unittest.TestCase):
    """Root's part, in a staged tree with fake commands (as tests/test_ndi_setup.py does for the NDI input)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ran, self.said = [], []

    def run_ok(self, command):
        self.ran.append(command)
        return 0

    def tree(self):
        return sorted(os.path.relpath(os.path.join(d, n), self.root) for d, _dirs, names in os.walk(self.root) for n in names)

    def account(self, **kw):
        self.assertEqual(kw.get("name"), "pvj-ndisc")

    def test_two_files_are_all_the_set_up_writes_and_the_drop_in_is_the_mark(self):
        self.assertFalse(D.opted_in(self.root))
        self.assertTrue(D.enable(run=self.run_ok, say=self.said.append, systemd=lambda: True, account=self.account, root=self.root, sleep=lambda s: None))
        self.assertEqual(self.tree(), ["etc/systemd/system/pvj-ndi-discovery.service", "etc/systemd/system/pvj-web.service.d/50-pvj-ndisc.conf"])
        self.assertTrue(D.opted_in(self.root))
        with open(self.root + D.UNIT_PATH) as f:
            unit = f.read()
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-ndi-discovery\n", unit)
        self.assertNotIn("@PVJ", unit)
        with open(self.root + D.WEB_DROPIN) as f:
            lines = [l for l in f.read().splitlines() if l and not l.startswith("#")]
        self.assertEqual(lines, ["[Service]", "SupplementaryGroups=pvj-ndisc", "Environment=PVJ_NDISC_DIR=/run/pvj-ndisc"])
        self.assertEqual([c for c in self.ran if c[1] != "is-active"],
                         [["systemctl", "daemon-reload"], ["systemctl", "enable", D.UNIT], ["systemctl", "restart", D.UNIT],
                          ["systemctl", "try-restart", "pvj-web.service"]])
        self.assertIn("Nothing of it listens yet", " ".join(self.said))
        # the installer's refresh writes the unit again, and leaves a box without the mark alone
        with open(self.root + D.UNIT_PATH, "a") as f:
            f.write("# a line from an older version\n")
        self.assertTrue(D.refresh(self.root, "/opt/pvj"))
        with open(self.root + D.UNIT_PATH) as f:
            self.assertEqual(f.read(), unit)
        # and off again: nothing is left, the NDI input's own drop-in beside it stays
        ndisetup._write(self.root + ndisetup.WEB_DROPIN, ndisetup.WEB_TEXT)
        D.disable(run=self.run_ok, say=self.said.append, systemd=lambda: True, root=self.root)
        self.assertEqual(self.tree(), ["etc/systemd/system/pvj-web.service.d/50-pvj-ndi.conf"])
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        self.assertFalse(D.refresh(other, "/opt/pvj"))
        self.assertEqual(os.listdir(other), [])

    def test_a_set_up_that_fails_is_undone(self):
        def run(command):
            self.ran.append(command)
            return 1 if command[:2] == ["systemctl", "restart"] else 0
        with self.assertRaises(ndisetup.SetupError):
            D.enable(run=run, say=self.said.append, systemd=lambda: True, account=self.account, root=self.root, sleep=lambda s: None)
        self.assertEqual(self.tree(), [])
        self.assertIn("was undone", " ".join(self.said))

    def test_a_link_where_a_file_goes_is_refused_before_anything_is_changed(self):
        os.makedirs(os.path.dirname(self.root + D.UNIT_PATH))
        os.symlink("/etc/passwd", self.root + D.UNIT_PATH)
        with self.assertRaises(ndisetup.SetupError):
            D.enable(run=self.run_ok, say=self.said.append, systemd=lambda: True, account=self.account, root=self.root, sleep=lambda s: None)
        self.assertEqual(self.ran, [])

    def test_the_account_is_its_own_and_held_to_the_helpers_rules(self):
        made = []

        class G:
            def __init__(self, name, gid, mem=()):
                self.gr_name, self.gr_gid, self.gr_mem = name, gid, list(mem)

        class U:
            def __init__(self, name, uid, gid, shell="/usr/sbin/nologin"):
                self.pw_name, self.pw_uid, self.pw_gid, self.pw_shell = name, uid, gid, shell
        groups, users = {}, {}

        def run(command):
            made.append(command)
            if command[0] == "groupadd":
                groups[command[-1]] = G(command[-1], 990)
            if command[0] == "useradd":
                users[command[-1]] = U(command[-1], 990, 990)
            return 0
        kw = dict(run=run, say=lambda m: None, user=users.get, group=groups.get, groups=lambda: list(groups.values()), users=lambda: list(users.values()))
        ndisetup.ensure_account(name="pvj-ndisc", **kw)
        self.assertEqual(made, [["groupadd", "--system", "pvj-ndisc"],
                                ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "--gid", "pvj-ndisc", "pvj-ndisc"]])
        self.assertNotIn("pvj-ndi", groups)                                    # not the helper's account: its own
        groups["pvj-ndisc"].gr_mem.append("pvj-web")                           # a member: refused, the panel is let in by its unit
        with self.assertRaises(ndisetup.SetupError) as c:
            ndisetup.ensure_account(name="pvj-ndisc", **kw)
        self.assertIn("pvj-ndisc", str(c.exception))
        groups["pvj-ndisc"].gr_mem.clear()
        users["pvj-ndisc"].pw_shell = "/bin/bash"
        with self.assertRaises(ndisetup.SetupError):
            ndisetup.check_account(user=users.get, group=groups.get, groups=lambda: list(groups.values()), users=lambda: list(users.values()), name="pvj-ndisc")

    def test_the_command_says_what_it_is_and_asks_for_root(self):
        out = io.StringIO()
        self.assertEqual(ndi.runtime_main(["server"], out), 2)
        self.assertIn("server install", out.getvalue())
        self.assertIn("never downloaded by the box", out.getvalue())
        if os.geteuid() != 0:
            out = io.StringIO()
            self.assertEqual(ndi.runtime_main(["server", "install", "/nowhere"], out), 1)
            self.assertIn("run it with sudo", out.getvalue())
        out = io.StringIO()
        self.assertEqual(ndi.runtime_main(["server", "refresh", "--root", self.root, "--prefix", "/opt/pvj"], out), 0)
        self.assertEqual(self.tree(), [])                                      # a box without the mark is left as it is


class UnitFile(unittest.TestCase):
    def setUp(self):
        with open(D.TEMPLATE) as f:
            self.lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.keys = {}
        for l in self.lines:
            if "=" in l:
                k, v = l.split("=", 1)
                self.keys.setdefault(k, []).append(v)
        with open(ndisetup.TEMPLATE) as f:
            self.helper = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    def test_its_own_account_and_no_privileges(self):
        k = self.keys
        self.assertEqual((k["User"], k["Group"], k["RuntimeDirectory"], k["RuntimeDirectoryMode"]), (["pvj-ndisc"], ["pvj-ndisc"], ["pvj-ndisc"], ["0750"]))
        self.assertNotIn("SupplementaryGroups", k)
        self.assertEqual((k["CapabilityBoundingSet"], k["AmbientCapabilities"], k["NoNewPrivileges"]), ([""], [""], ["yes"]))
        self.assertEqual((k["ProtectSystem"], k["ProtectHome"], k["PrivateDevices"], k["ReadWritePaths"]), (["strict"], ["yes"], ["yes"], ["/run/pvj-ndisc"]))
        self.assertEqual(k["ExecStart"], ["@PVJ_DIR@/bin/pvj-ndi-discovery"])
        self.assertEqual((k["Restart"], k["RestartSec"], k["StartLimitIntervalSec"]), (["always"], ["2"], ["0"]))

    def test_it_is_sandboxed_at_least_as_tightly_as_the_ndi_helper(self):
        loosened = {"ReadWritePaths", "RuntimeDirectory", "User", "Group", "Environment", "ExecStart", "Description", "After", "Wants",
                    "ConditionPathExists", "InaccessiblePaths", "MemoryMax", "CPUQuota", "RestrictAddressFamilies", "IPAddressAllow",
                    "TasksMax"}             # the helper's went to 96 for a second connection (D87); this unit keeps 64, checked below
        for line in self.helper:
            if "=" in line and line.split("=", 1)[0] not in loosened:
                self.assertIn(line, self.lines, "the NDI helper has this line and the discovery server does not")
        self.assertEqual(self.keys["RestrictAddressFamilies"], ["AF_UNIX AF_INET AF_NETLINK"])      # fewer than the helper: no IPv6
        self.assertEqual(self.keys["IPAddressDeny"], ["any"])
        self.assertEqual(self.keys["IPAddressAllow"], ["link-local 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"])     # no multicast, no internet, no localhost
        self.assertEqual(self.keys["MemoryMax"], ["128M"])
        self.assertEqual(self.keys["TasksMax"], ["64"])                                       # fewer than the helper's 96
        hidden = self.keys["InaccessiblePaths"][0].split()
        for path in ("-/var/lib/pvj", "-/etc/pvj", "-/run/pvj", "-/run/pvj-ndi", "-/run/pvj-ndi-pipe", "-/run/pvj-ndi-config", "-/run/pvj-sysd",
                     "-/run/pvj-supportd", "-/opt/pvj-ndi", "-/etc/ndi", "-/usr/local/etc/ndi", "-/media", "-/mnt"):
            self.assertIn(path, hidden)
        self.assertNotIn("-/opt/pvj-ndi-server", hidden)                         # its own program it must reach

    def test_the_launcher_is_the_supervisor_and_nothing_else(self):
        with open(os.path.join(REPO, "bin", "pvj-ndi-discovery")) as f:
            text = f.read()
        self.assertIn("from pvj.ndiscovery import main", text)
        self.assertTrue(os.access(os.path.join(REPO, "bin", "pvj-ndi-discovery"), os.X_OK))


class Supervisor(unittest.TestCase):
    """The real Service with a script standing in for NDI's program."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.args = os.path.join(self.tmp, "args")
        self.program = os.path.join(self.tmp, D.SERVER_NAME)
        self.write("exec sleep 30")
        self.problem = None
        self.s = D.Service(self.program, log=lambda m: None, problem=lambda p: self.problem, first=0.05, most=0.2, settled=30)
        self.addCleanup(self.s.close)

    def write(self, tail):
        with open(self.program, "w") as f:
            f.write(FAKE % (self.args, tail))
        os.chmod(self.program, 0o755)

    def runs(self):
        try:
            with open(self.args) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def test_nothing_runs_until_the_panel_says_on_and_then_exactly_the_checked_arguments(self):
        self.assertEqual((self.s.status()["state"], self.s.status()["on"], self.runs()), ("off", False, []))
        out = self.s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertEqual((out["ok"], out["state"], out["bind"], out["port"]), (True, "running", "192.168.1.9", 5959))
        self.assertTrue(wait(lambda: self.runs() == ["-service -bind 192.168.1.9 -port 5959"]))
        self.assertEqual(self.s.argv(), [self.program, "-service", "-bind", "192.168.1.9", "-port", "5959"])
        self.assertTrue(wait(lambda: self.s.status()["last"] == "fake discovery server -service -bind 192.168.1.9 -port 5959"))
        child = self.s.child
        # the same again changes nothing; another address starts it again there; off ends it
        self.s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertIs(self.s.child, child)
        self.s.handle({"cmd": "configure", "on": True, "bind": "10.0.0.4", "port": 6000})
        self.assertTrue(wait(lambda: self.runs()[-1:] == ["-service -bind 10.0.0.4 -port 6000"]))
        self.assertIsNotNone(child.poll(), "the first one was not ended")
        child = self.s.child
        out = self.s.handle({"cmd": "configure", "on": False})
        self.assertEqual((out["state"], out["on"], self.s.child), ("off", False, None))
        self.assertIsNotNone(child.poll())
        self.assertEqual(len(self.runs()), 2)

    def test_what_the_supervisor_refuses_to_run(self):
        for bad in ({"bind": "8.8.8.8"}, {"bind": "127.0.0.1"}, {"bind": "192.168.1.9; rm -rf /"}, {"bind": "-config"}, {"bind": "box.local"}, {"bind": ["192.168.1.9"]},
                    {"bind": "192.168.1.9", "port": 80}, {"bind": "192.168.1.9", "port": "5959"}, {"bind": "192.168.1.9", "port": 70000},
                    {"bind": "192.168.1.9", "port": True}, {}):
            out = self.s.handle(dict({"cmd": "configure", "on": True}, **bad))
            self.assertEqual(out["ok"], False, bad)
        self.assertEqual(self.s.handle({"cmd": "configure", "on": "yes"})["ok"], False)
        self.assertEqual(self.s.handle({"cmd": "run", "program": "/bin/sh"})["ok"], False)
        self.assertEqual(self.s.handle("x")["ok"], False)
        self.assertEqual((self.runs(), self.s.child), ([], None))
        self.assertEqual(self.s.handle({"cmd": "configure", "on": True, "bind": "0.0.0.0", "port": 5959})["state"], "running")     # "all the box's networks", when the Owner chose it

    def test_a_file_that_is_not_roots_program_is_never_started(self):
        self.problem = "NDI's discovery server program is not on this box yet"
        out = self.s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertEqual((out["state"], out["message"], out["installed"]), ("missing", self.problem, False))
        time.sleep(0.3)
        self.assertEqual(self.runs(), [])

    def test_a_program_that_ends_is_started_again_with_a_growing_pause_and_says_its_last_line(self):
        self.write("echo 'cannot bind: address in use' >&2\nexit 3")
        self.s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertTrue(wait(lambda: len(self.runs()) >= 3, 5))
        st = self.s.status()
        self.assertGreaterEqual(st["restarts"], 2)
        self.assertEqual(st["last"], "cannot bind: address in use")
        self.assertTrue(wait(lambda: "exit 3" in self.s.status()["message"] or self.s.status()["state"] == "running"))
        began = time.monotonic()
        n = len(self.runs())
        self.assertTrue(wait(lambda: len(self.runs()) >= n + 3, 5))
        self.assertGreater(time.monotonic() - began, 0.3, "the pause between starts did not grow to its most")
        self.s.handle({"cmd": "configure", "on": False})
        n = len(self.runs())
        time.sleep(0.5)
        self.assertEqual(len(self.runs()), n, "it was started again after it was switched off")

    def test_a_program_that_says_it_listens_elsewhere_is_stopped_and_not_started_again(self):
        """It has never been run here, so whether it honours -bind in service mode is not known. If it says it
        listens on another address than the one chosen, it is ended and stays ended."""
        self.write("echo 'Listening... [0.0.0.0:5959]'\nexec sleep 30")
        self.s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertTrue(wait(lambda: self.s.status()["state"] == "failed"))
        st = self.s.status()
        self.assertIn("listened somewhere other than the address chosen (192.168.1.9)", st["message"])
        self.assertEqual(st["last"], "Listening... [0.0.0.0:5959]")
        time.sleep(0.4)
        self.assertEqual((len(self.runs()), self.s.child), (1, None), "it was started again")
        # the address it was given, or no address at all in the line, is fine; so is 0.0.0.0 when that was chosen
        for line, bind in (("Listening... [192.168.1.9:5959]", "192.168.1.9"), ("Listening... [5959]", "192.168.1.9"), ("Listening... [0.0.0.0:5959]", "0.0.0.0")):
            self.s.handle({"cmd": "configure", "on": False})
            self.write("echo '%s'\nexec sleep 30" % line)
            self.s.handle({"cmd": "configure", "on": True, "bind": bind, "port": 5959})
            self.assertTrue(wait(lambda: self.s.status()["last"] == line))
            time.sleep(0.1)
            self.assertEqual(self.s.status()["state"], "running", line)

    def test_a_line_the_program_prints_is_made_plain(self):
        self.assertEqual(D.clean_line(b"ok \x1b[31mred\x00\xff\n"), "ok  [31mred")
        self.assertEqual(len(D.clean_line(b"x" * 5000)), D.MAX_LINE)


class Choice(unittest.TestCase):
    IP = [{"ifname": "lo", "addr_info": [{"local": "127.0.0.1"}]}, {"ifname": "eth0", "addr_info": [{"local": "192.168.1.9"}, {"local": "8.8.4.4"}]},
          {"ifname": "wlan0", "addr_info": [{"local": "10.0.0.7"}]}, {"ifname": "wg0", "addr_info": [{"local": "10.9.0.2"}]}, "junk", {"ifname": 7}]

    def test_the_address_is_the_chosen_networks_and_never_all_of_them_unless_chosen(self):
        rows = D.box_addresses(self.IP)
        self.assertEqual(rows, [("eth0", "192.168.1.9"), ("wlan0", "10.0.0.7")])       # never the support tunnel (wg*) or another tunnel
        self.assertEqual((D.address_for("wired", rows), D.address_for("wifi", rows), D.address_for("all", rows)), ("192.168.1.9", "10.0.0.7", "0.0.0.0"))
        self.assertIsNone(D.address_for("wired", [("wlan0", "10.0.0.7")]))       # no cable: it does not move to the Wi-Fi by itself
        self.assertEqual(D.box_addresses(None), [])

    def test_the_saved_choice(self):
        self.assertEqual(D.validate_saved({}), {"network": "wired", "port": 5959})
        self.assertEqual(D.validate_saved({"network": "all", "port": 6000}), {"network": "all", "port": 6000})
        for bad in ({"network": "0.0.0.0"}, {"network": "eth0"}, {"port": 80}, {"port": "5959"}, {"port": 5959.0}, {"bind": "1.2.3.4"}, "x", None):
            with self.assertRaises(ndi.NdiError, msg=bad):
                D.validate_saved(bad)
            self.assertEqual(D.saved(bad), {"network": "wired", "port": 5959})
        self.assertIn("ndi_discovery", boxcare.NEVER)                           # which network a listener is on is not carried between boxes


class FakeClient:
    def __init__(self, service):
        self.service, self.sent, self.down = service, [], False

    def request(self, message, timeout=None):
        self.sent.append(message)
        if self.down:
            raise ndi.NdiError("not running")
        return json.loads(json.dumps(self.service.handle(json.loads(json.dumps(message)))))

    def status(self):
        try:
            return self.request({"cmd": "status"})
        except ndi.NdiError:
            return {"ok": False}


class Panel(PadBase):
    """The routes, with the real supervisor behind a client that calls it directly, and the script behind that."""

    def setUp(self):
        super().setUp()
        self.dtmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dtmp, True)
        self.args = os.path.join(self.dtmp, "args")
        program = os.path.join(self.dtmp, D.SERVER_NAME)
        with open(program, "w") as f:
            f.write(FAKE % (self.args, "exec sleep 30"))
        os.chmod(program, 0o755)
        self.missing = None
        self.sup = D.Service(program, log=lambda m: None, problem=lambda p: self.missing)
        self.addCleanup(self.sup.close)
        self.dclient = FakeClient(self.sup)
        self.addrs = [("eth0", "192.168.1.9"), ("wlan0", "10.0.0.7")]
        reg, st = self.api.registry, self.settings
        self.api.ndisc = D.Manager(self.dclient, lambda: st.data, lambda: reg.enabled("ndi-discovery"), lambda: list(self.addrs), log=lambda m: None)
        self.api.ndi.own_server = self.api._own_discovery

    def switch(self, on, by=None, client="192.168.1.50"):
        return self.h("POST", "/api/modules/ndi-discovery", {"enabled": on}, by or self.owner, client)

    def page(self):
        st, out = self.h("GET", "/api/ndi/discovery", {}, self.owner)
        self.assertEqual(st, 200, out)
        return out

    def runs(self):
        try:
            with open(self.args) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def test_off_by_default_and_nothing_runs(self):
        p = self.page()
        self.assertEqual((p["module"], p["on"], p["state"], p["listening"], p["network"], p["port"], p["tell"]), (False, False, "off", None, "wired", 5959, ""))
        self.assertEqual((self.runs(), self.sup.child), ([], None))
        self.assertNotIn("ndi_discovery", self.settings.data)
        self.assertFalse(self.api.registry.enabled("ndi-discovery"))

    def test_only_the_owner_in_the_room_switches_and_sets_it(self):
        self.ready()
        others = [("guest", self.guest), ("operator", self.operator)] + sorted(CONTROLLER_DEVICES.items())
        for name, dev in others:
            self.later()
            self.assertEqual(self.switch(True, dev)[0], 403, name)
            self.assertEqual(self.h("POST", "/api/ndi/discovery", {"network": "all"}, dev)[0], 403, name)
            self.assertEqual(self.h("GET", "/api/ndi/discovery", {}, dev)[0], 403, name)
        self.assertEqual(self.h("GET", "/api/ndi/discovery", {}, None)[0], 401)
        for role in ("view", "live", "full"):                # every remote support session, the full one too
            if self.api.support.session is not None:
                self.h("POST", "/api/support/stop", {}, self.owner)
            code = self.h("POST", "/api/support/start", {"confirm": "start", "role": role}, self.owner)[1]["code"]
            dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, None, TUNNEL)[1]["token"])
            self.assertEqual(self.switch(True, dev, TUNNEL)[0], 403, role)
            self.assertEqual(self.h("POST", "/api/ndi/discovery", {"network": "all"}, dev, TUNNEL)[0], 403, role)
            self.assertEqual(self.h("GET", "/api/ndi/discovery", {}, dev, TUNNEL)[0], 403, role)
        self.assertIn(("POST", "/api/modules/ndi-discovery"), support.REMOTE_DENY)
        self.assertIn("/api/ndi/discovery", support.REMOTE_DENY_PREFIX)
        self.assertEqual((self.runs(), self.api.registry.enabled("ndi-discovery")), ([], False))
        self.assertNotIn("ndi_discovery", self.settings.data)
        self.assertEqual(self.switch(True)[0], 200)

    def test_switched_on_it_listens_on_the_wired_address_and_the_page_says_what_to_tell_senders(self):
        self.assertEqual(self.switch(True)[0], 200)
        self.assertTrue(wait(lambda: self.runs() == ["-service -bind 192.168.1.9 -port 5959"]))
        p = self.page()
        self.assertEqual((p["state"], p["listening"], p["tell"], p["bind"]), ("running", {"address": "192.168.1.9", "port": 5959}, "192.168.1.9:5959", "192.168.1.9"))
        self.assertIn("the NDI discovery server was switched on by device", " ".join(self.lines))
        # another network and port: validated, saved, and the server starts again there
        for bad in ({"network": "0.0.0.0"}, {"network": "eth0"}, {"port": 80}, {"port": "x"}, {"bind": "8.8.8.8"}, {"network": "wifi", "program": "/bin/sh"}):
            self.assertEqual(self.h("POST", "/api/ndi/discovery", bad, self.owner)[0], 400, bad)
        st, p = self.h("POST", "/api/ndi/discovery", {"network": "wifi", "port": 6000}, self.owner)
        self.assertEqual((st, p["tell"], self.settings.data["ndi_discovery"]), (200, "10.0.0.7:6000", {"network": "wifi", "port": 6000}))
        self.assertTrue(wait(lambda: self.runs()[-1:] == ["-service -bind 10.0.0.7 -port 6000"]))
        st, p = self.h("POST", "/api/ndi/discovery", {"network": "all"}, self.owner)
        self.assertEqual((p["bind"], p["tell"]), ("0.0.0.0", "192.168.1.9:6000"))
        self.assertTrue(wait(lambda: self.runs()[-1:] == ["-service -bind 0.0.0.0 -port 6000"]))
        self.assertEqual(self.switch(False)[0], 200)
        self.assertEqual((self.sup.child, self.page()["state"]), (None, "off"))

    def test_the_address_changes_and_the_server_follows_it(self):
        self.switch(True)
        self.assertTrue(wait(lambda: self.runs() == ["-service -bind 192.168.1.9 -port 5959"]))
        self.assertFalse(self.api.ndisc_tick())                                 # nothing changed: nothing is restarted
        self.addrs[0] = ("eth0", "192.168.1.77")                                # a new lease
        self.assertTrue(self.api.ndisc_tick())
        self.assertTrue(wait(lambda: self.runs()[-1:] == ["-service -bind 192.168.1.77 -port 5959"]))
        self.assertEqual(self.page()["tell"], "192.168.1.77:5959")
        self.addrs[:] = [("wlan0", "10.0.0.7")]                                 # the cable is pulled: it stops, and does not move to the Wi-Fi
        self.assertTrue(self.api.ndisc_tick())
        p = self.page()
        self.assertEqual((self.sup.child, p["state"], p["bind"], p["tell"], p["on"]), (None, "off", None, "", True))
        self.addrs[:] = [("eth0", "192.168.1.77")]
        self.assertTrue(self.api.ndisc_tick())
        self.assertTrue(wait(lambda: self.sup.child is not None))

    def test_the_pages_states(self):
        self.missing = "NDI's discovery server program is not on this box yet"
        self.switch(True)
        p = self.page()
        self.assertEqual((p["setup"], p["supervisor"], p["installed"], p["state"], p["problem"]), (True, True, False, "missing", self.missing))
        self.assertEqual(p["install"]["command"], 'sudo pvj-ndi-runtime server install "/path/to/NDI SDK for Linux"')
        self.dclient.down = True
        p = self.page()
        self.assertEqual((p["supervisor"], p["state"], p["listening"]), (False, "", None))
        self.api.ndisc.setup, self.api.ndisc.client = False, None                # a box where nobody ran the command
        p = self.page()
        self.assertEqual((p["setup"], p["supervisor"]), (False, False))
        self.assertEqual(self.switch(False)[0], 200)                             # and the switch still answers
        self.api.ndisc = None
        self.assertEqual(self.h("GET", "/api/ndi/discovery", {}, self.owner)[0], 404)

    def test_a_supervisor_that_lies_is_not_believed(self):
        self.switch(True)
        real = self.dclient.request
        self.dclient.request = lambda m, timeout=None: dict(real(m), bind="8.8.8.8", port="x", last="\x1b[2Jhello" + "y" * 900, restarts="many", state="pwned", message=["x"])
        p = self.page()
        self.assertEqual((p["listening"], p["state"], p["restarts"], p["message"]), (None, "", 0, ""))
        self.assertLessEqual(len(p["last"]), D.MAX_LINE)
        self.assertNotIn("\x1b", p["last"])

    def test_this_boxs_own_input_uses_this_boxs_own_server(self):
        confdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, confdir, True)
        self.api.ndi.config_dir = self.service.config_dir = confdir
        for dev in (self.operator, self.guest):
            self.later()
            self.assertEqual(self.h("POST", "/api/ndi", {"action": "own_discovery", "on": True}, dev)[0], 403)
        st, out = self.h("POST", "/api/ndi", {"action": "own_discovery", "on": True}, self.owner)
        self.assertEqual((st, out["own_discovery"], out["discovery"]), (200, True, []))        # the server is off: nothing to ask yet
        self.switch(True)
        out = self.h("GET", "/api/ndi", {}, self.owner)[1]
        self.assertEqual((out["discovery"], out["discovery_saved"]), (["192.168.1.9"], []))
        with open(os.path.join(confdir, ndi.CONFIG_NAME)) as f:
            self.assertEqual(json.load(f), {"ndi": {"networks": {"discovery": "192.168.1.9"}}})
        self.addrs[0] = ("eth0", "192.168.1.77")                                # the server moves: so does what the input asks
        self.api.ndisc_tick()
        with open(os.path.join(confdir, ndi.CONFIG_NAME)) as f:
            self.assertEqual(json.load(f), {"ndi": {"networks": {"discovery": "192.168.1.77"}}})
        self.assertEqual(self.settings.data["ndi"].get("own_discovery"), True)
        self.assertNotIn("discovery", self.settings.data["ndi"], "the address of the moment is not saved as a server")
        self.h("POST", "/api/ndi", {"action": "sound", "on": True}, self.owner)
        self.assertEqual(self.settings.data["ndi"].get("own_discovery"), True, "another NDI setting dropped the switch")
        self.h("POST", "/api/ndi", {"action": "own_discovery", "on": False}, self.owner)
        self.assertNotIn("own_discovery", self.settings.data["ndi"])
        with open(os.path.join(confdir, ndi.CONFIG_NAME)) as f:
            self.assertEqual(json.load(f), {"ndi": {}})

    def test_the_setting_is_not_exported_and_an_older_release_ignores_it(self):
        self.switch(True)
        self.h("POST", "/api/ndi/discovery", {"network": "wifi", "port": 6000}, self.owner)
        self.h("POST", "/api/ndi", {"action": "own_discovery", "on": True}, self.owner)
        st, out = self.h("POST", "/api/system/settings/export", {}, self.owner)
        self.assertEqual(st, 200)
        self.assertNotIn("ndi_discovery", json.dumps(out))
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", MASTER], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % MASTER)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", MASTER, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "modules.d", "ndi-discovery.json")), "master has no such module: this is the older release")
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "ndiscovery.py")))
        with open(copy) as f:
            self.assertIs(json.load(f)["modules"]["enabled"]["ndi-discovery"], True)      # the file says the module is on
        script = ("import sys, json\nfrom pvj.settings import Settings\nfrom pvj.modules import Registry\ns = Settings(sys.argv[1])\ns.load()\n"
                  "r = Registry(s, 'x86')\ntry:\n    on = bool(r.enabled('ndi-discovery'))\nexcept Exception as e:\n    on = type(e).__name__\n"
                  "s.data['mix'] = {'transition': 'dip', 'duration': 2.0}\ns.save()\nprint(json.dumps([s.data['schema'], on]))\n")
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(got[0], self.settings.data["schema"])
        self.assertIs(got[1], False, "an older release must not count a module it does not have as on")
        with open(copy) as f:
            kept = json.load(f)
        self.assertEqual((kept["ndi_discovery"], kept["ndi"]["own_discovery"]), ({"network": "wifi", "port": 6000}, True))


if __name__ == "__main__":
    unittest.main()
