# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI input is opt-in per box (D62, the owner's answers of 2026-10-08): what root's one command does and in
which order, what a box that did not opt in looks like to the panel, and that a support session is treated for NDI
exactly as for Streams. Driven with a fake root folder and fake commands: NOTHING here ran groupadd, apt-get or
systemctl, and nothing ran on Linux or a Pi. The real commands under a real systemd are tests/real_install_test.sh
(the CI runner), and the device steps N0 to N18 in tools/DEVICE-TESTING.md."""
import inspect
import io
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from pvj import ndi, ndisetup, paths, server
from pvj import support as sp
from tests.test_server import ServerBase
from tests.test_support import CFG, TUNNEL, SupportBase


class Commands:
    """Stands in for running a command: notes it, and answers with an exit status."""

    def __init__(self, fail=()):
        self.ran, self.fail = [], tuple(fail)

    def __call__(self, command):
        self.ran.append(" ".join(command))
        return 1 if any(self.ran[-1].startswith(f) for f in self.fail) else 0


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.said = []

    def p(self, path):
        return self.root + path

    def read(self, path):
        with open(self.p(path)) as f:
            return f.read()

    def tree(self):
        return sorted(os.path.join(d, n)[len(self.root):] for d, _dirs, names in os.walk(self.root) for n in names)


class FilesTest(Base):
    def test_a_box_starts_out_not_opted_in_and_three_files_are_all_that_opting_in_writes(self):
        self.assertFalse(ndisetup.opted_in(self.root))
        ndisetup.write_units(self.root, "/opt/pvj")
        self.assertTrue(ndisetup.opted_in(self.root))
        self.assertEqual(self.tree(), ["/etc/systemd/system/pvj-ndi.service",
                                       "/etc/systemd/system/pvj-player.service.d/50-pvj-ndi.conf",
                                       "/etc/systemd/system/pvj-web.service.d/50-pvj-ndi.conf"])
        unit = self.read(ndisetup.UNIT_PATH)
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-ndi\n", unit)
        self.assertNotIn("@PVJ", unit)
        with open(ndisetup.TEMPLATE) as f:                 # nothing but the folder differs from the template the tests pin
            self.assertEqual(unit, f.read().replace("@PVJ_DIR@", "/opt/pvj/current"))
        self.assertEqual(self.read(ndisetup.WEB_DROPIN), ndisetup.WEB_TEXT)
        self.assertEqual(self.read(ndisetup.PLAYER_DROPIN), ndisetup.PLAYER_TEXT)
        for path in self.tree():
            self.assertEqual(os.stat(self.p(path)).st_mode & 0o777, 0o644, path)
        self.assertEqual([n for n in os.listdir(self.p("/etc/systemd/system")) if n.startswith(".")], [])     # no half-written file left

    def test_the_mark_is_written_last_and_removed_first(self):
        order = []
        real = ndisetup._write
        with mock.patch.object(ndisetup, "_write", lambda path, text: (order.append(path[len(self.root):]), real(path, text))):
            ndisetup.write_units(self.root, "/opt/pvj")
        self.assertEqual(order, [ndisetup.UNIT_PATH, ndisetup.PLAYER_DROPIN, ndisetup.WEB_DROPIN])
        gone = []
        real_unlink = os.unlink
        with mock.patch.object(ndisetup.os, "unlink", lambda path: (gone.append(path[len(self.root):]), real_unlink(path))):
            ndisetup.remove_units(self.root)
        self.assertEqual(gone, [ndisetup.WEB_DROPIN, ndisetup.PLAYER_DROPIN, ndisetup.UNIT_PATH])
        self.assertEqual(self.tree(), [])
        self.assertEqual(os.listdir(self.p("/etc/systemd/system")), [])       # the two folders went too
        ndisetup.remove_units(self.root)                   # and again, on a box that has none of it: nothing to do, no error

    def test_a_template_that_cannot_be_read_or_a_bad_folder_writes_nothing(self):
        with self.assertRaises(OSError):
            ndisetup.write_units(self.root, "/opt/pvj", template=self.p("/missing"))
        for bad in ("opt/pvj", "/opt", "/opt/pvj; rm -rf /", "/opt/../etc", "/opt/pvj\n", "", None, "/opt/p vj"):
            with self.assertRaises(ndisetup.SetupError, msg=repr(bad)):
                ndisetup.write_units(self.root, bad)
        self.assertEqual(self.tree(), [])
        self.assertFalse(ndisetup.opted_in(self.root))

    def test_removing_leaves_somebody_elses_drop_in_and_its_folder(self):
        ndisetup.write_units(self.root, "/opt/pvj")
        other = "/etc/systemd/system/pvj-web.service.d/90-owner.conf"
        with open(self.p(other), "w") as f:
            f.write("[Service]\nEnvironment=X=1\n")
        ndisetup.remove_units(self.root)
        self.assertEqual(self.tree(), [other])
        self.assertFalse(ndisetup.opted_in(self.root))

    def test_a_link_is_not_a_mark_and_is_never_written_through(self):
        target = self.p("/elsewhere")
        os.makedirs(self.p("/etc/systemd/system/pvj-web.service.d"))
        os.symlink(target, self.p(ndisetup.WEB_DROPIN))
        with open(target, "w") as f:
            f.write("x")
        self.assertFalse(ndisetup.opted_in(self.root))
        with self.assertRaises(ndisetup.SetupError):
            ndisetup.write_units(self.root, "/opt/pvj")
        self.assertEqual(self.read("/elsewhere"), "x")
        self.assertFalse(ndisetup.opted_in(self.root))
        # a folder that is a link is refused as well
        root2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root2, True)
        os.makedirs(root2 + "/etc/systemd/system")
        os.makedirs(root2 + "/planted")
        os.symlink(root2 + "/planted", root2 + "/etc/systemd/system/pvj-player.service.d")
        with self.assertRaises(ndisetup.SetupError):
            ndisetup.write_units(root2, "/opt/pvj")
        self.assertEqual(os.listdir(root2 + "/planted"), [])
        self.assertFalse(ndisetup.opted_in(root2))         # the mark comes last, so a setup that stopped is not a box that opted in

    def test_the_install_folder_is_read_from_what_the_installer_wrote(self):
        self.assertEqual(ndisetup.read_prefix(self.root), "/opt/pvj")
        os.makedirs(self.p("/etc/pvj"))
        for text, want in (('{"version": "1", "prefix": "/srv/box/pvj", "user": "x"}', "/srv/box/pvj"), ("not json", "/opt/pvj"),
                           ('["a"]', "/opt/pvj"), ('{"user": "x"}', "/opt/pvj")):
            with open(self.p(ndisetup.INSTALL_JSON), "w") as f:
                f.write(text)
            self.assertEqual(ndisetup.read_prefix(self.root), want, text)
        with open(self.p(ndisetup.INSTALL_JSON), "w") as f:
            f.write('{"prefix": "/opt/pvj\\nExecStartPre=/bin/evil"}')
        with self.assertRaises(ndisetup.SetupError):
            ndisetup.read_prefix(self.root)


class RefreshTest(Base):
    """What install.sh calls at every install and update."""

    def test_a_box_that_did_not_opt_in_is_left_exactly_as_it_is(self):
        run = Commands()
        account = mock.Mock()
        self.assertFalse(ndisetup.refresh(self.root, "/opt/pvj", run=run, say=self.said.append, account=account))
        self.assertEqual((self.tree(), run.ran, account.call_count, self.said), ([], [], 0, []))
        out = io.StringIO()
        self.assertEqual(ndisetup.refresh_main(["--root", self.root, "--prefix", "/opt/pvj"], out), 0)
        self.assertEqual((self.tree(), out.getvalue()), ([], ""))

    def test_a_box_that_opted_in_keeps_its_helper_and_gets_the_unit_of_the_new_program(self):
        ndisetup.write_units(self.root, "/opt/pvj")
        for path in (ndisetup.UNIT_PATH, ndisetup.PLAYER_DROPIN):          # what an older version wrote, or a file that went missing
            with open(self.p(path), "w") as f:
                f.write("old\n")
        os.unlink(self.p(ndisetup.PLAYER_DROPIN))
        account = mock.Mock()
        out = io.StringIO()
        self.assertEqual(ndisetup.refresh_main(["--root", self.root + "/", "--prefix", "/srv/box/pvj"], out), 0)
        self.assertIn("ExecStart=/srv/box/pvj/current/bin/pvj-ndi\n", self.read(ndisetup.UNIT_PATH))
        self.assertEqual(self.read(ndisetup.PLAYER_DROPIN), ndisetup.PLAYER_TEXT)
        self.assertTrue(ndisetup.opted_in(self.root))
        self.assertIn("written again", out.getvalue())
        # in a staged tree no account is touched; on the box itself the account comes before the files
        self.assertTrue(ndisetup.refresh(self.root, "/opt/pvj", run=Commands(), say=self.said.append, account=account))
        self.assertEqual(account.call_count, 0)
        order = []
        with mock.patch.object(ndisetup, "opted_in", lambda root="": True), \
                mock.patch.object(ndisetup, "write_units", lambda root, prefix: order.append(("write", root, prefix))):
            self.assertTrue(ndisetup.refresh("", "/opt/pvj", run=Commands(), say=self.said.append,
                                             account=lambda run, say: order.append("account")))
        self.assertEqual(order, ["account", ("write", "", "/opt/pvj")])

    def test_it_never_installs_a_package_or_starts_anything_and_bad_arguments_change_nothing(self):
        body = inspect.getsource(ndisetup.refresh)
        for never in ("apt", "systemctl", "missing_packages", "enable("):
            self.assertNotIn(never, body, never)
        ndisetup.write_units(self.root, "/opt/pvj")
        before = self.read(ndisetup.UNIT_PATH)
        out = io.StringIO()
        self.assertEqual(ndisetup.refresh_main(["--root", self.root, "--prefix", "/opt"], out), 1)       # a prefix the installer would refuse
        self.assertEqual(ndisetup.refresh_main(["--root", self.root, "--frobnicate"], out), 2)
        self.assertEqual(ndisetup.refresh_main(["--root"], out), 2)
        self.assertEqual(self.read(ndisetup.UNIT_PATH), before)
        if os.geteuid() != 0:                              # on the box itself it is root's to run
            self.assertEqual(ndisetup.refresh_main([], out), 1)
            self.assertIn("sudo", out.getvalue())


class Accounts:
    """A fake passwd and group file that groupadd and useradd, as run by the code under test, fill in."""

    def __init__(self, users=None, groups=None, fail=()):
        self.users, self.groups, self.ran, self.fail = dict(users or {}), dict(groups or {}), [], tuple(fail)

    def run(self, command):
        self.ran.append(" ".join(command))
        if command[0] in self.fail:
            return 1
        if command[0] == "groupadd":
            self.groups[command[-1]] = (900, [])
        elif command[0] == "useradd":
            self.users[command[-1]] = self.groups[command[command.index("--gid") + 1]][0]
        return 0

    def user(self, name):
        return types.SimpleNamespace(pw_name=name, pw_gid=self.users[name]) if name in self.users else None

    def group(self, name):
        return types.SimpleNamespace(gr_name=name, gr_gid=self.groups[name][0], gr_mem=self.groups[name][1]) if name in self.groups else None

    def all(self):
        return [self.group(n) for n in self.groups]

    def ensure(self):
        return ndisetup.ensure_account(run=self.run, say=lambda m: None, user=self.user, group=self.group, groups=self.all)


class AccountTest(unittest.TestCase):
    def test_the_group_then_the_account_with_no_home_no_shell_and_no_other_group(self):
        a = Accounts(users={"pvj-web": 800}, groups={"pvj": (800, [])})
        a.ensure()
        self.assertEqual(a.ran, ["groupadd --system pvj-ndi",
                                 "useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj-ndi pvj-ndi"])
        self.assertEqual(a.users["pvj-ndi"], a.groups["pvj-ndi"][0])
        self.assertEqual(a.groups["pvj"], (800, []))       # nobody was put into group pvj
        a.ran.clear()
        a.ensure()                                         # again: nothing to do
        self.assertEqual(a.ran, [])

    def test_an_account_that_is_in_another_group_is_refused_not_repaired(self):
        for groups in ({"pvj-ndi": (900, []), "pvj": (800, ["pvj-ndi"])}, {"pvj-ndi": (900, []), "video": (44, ["x", "pvj-ndi"])}):
            a = Accounts(users={"pvj-ndi": 900}, groups=groups)
            with self.assertRaises(ndisetup.SetupError) as e:
                a.ensure()
            self.assertIn("no other group", str(e.exception))
            self.assertEqual(a.ran, [])
        a = Accounts(users={"pvj-ndi": 800}, groups={"pvj-ndi": (900, []), "pvj": (800, [])})      # its own group is pvj
        with self.assertRaises(ndisetup.SetupError):
            a.ensure()
        self.assertEqual(a.ran, [])

    def test_a_command_that_fails_stops_the_setup(self):
        for fail in ("groupadd", "useradd"):
            a = Accounts(fail=(fail,))
            with self.assertRaises(ndisetup.SetupError, msg=fail):
                a.ensure()
            self.assertEqual(a.ran[-1].split()[0], fail)


class EnableTest(Base):
    """`sudo pvj-ndi-runtime install ...`, the part after the library: the opt-in. Every command it runs, in order."""

    def enable(self, run, missing=(), systemd=True, apt=True):
        self.order = []
        with mock.patch.object(ndisetup, "missing_packages", lambda root="": list(missing)), \
                mock.patch.object(ndisetup, "read_prefix", lambda root="": "/opt/pvj"), \
                mock.patch.object(ndisetup, "write_units", lambda root, prefix: (self.order.append("files"), run.ran.append("FILES %s %s" % (root, prefix)))):
            ndisetup.enable(run=run, say=self.said.append, systemd=lambda: systemd,
                            account=lambda run, say: run.ran.append("ACCOUNT"), which=lambda name: "/usr/bin/apt-get" if apt else None)

    def test_everything_it_does_and_the_order(self):
        run = Commands()
        self.enable(run, missing=["avahi-daemon", "libavahi-client3"])
        self.assertEqual(run.ran, ["apt-get install -y --no-install-recommends avahi-daemon libavahi-client3",
                                   "ACCOUNT", "FILES  /opt/pvj",
                                   "systemctl daemon-reload", "systemctl enable pvj-ndi.service", "systemctl restart pvj-ndi.service",
                                   "systemctl try-restart pvj-web.service pvj-player.service"])
        self.assertIn("the screen goes dark for a moment", " ".join(self.said))
        self.assertIn("NDI is set up on this box", self.said[-1])

    def test_nothing_is_installed_where_avahi_is_already_there(self):
        run = Commands()
        self.enable(run)
        self.assertEqual([c for c in run.ran if "apt" in c], [])
        self.assertEqual(run.ran[:2], ["ACCOUNT", "FILES  /opt/pvj"])

    def test_no_network_for_apt_is_said_and_is_not_the_end(self):
        run = Commands(fail=("apt-get",))
        self.enable(run, missing=["avahi-daemon"])
        self.assertIn("systemctl restart pvj-ndi.service", run.ran)
        self.assertIn("sudo apt-get install avahi-daemon", " ".join(self.said))
        run = Commands()
        self.enable(run, missing=["avahi-daemon"], apt=False)                 # not a Debian: said, nothing run for it
        self.assertEqual([c for c in run.ran if "apt" in c], [])
        self.assertIn("no apt-get here", " ".join(self.said))

    def test_a_step_that_fails_stops_it_and_nothing_later_runs(self):
        for fail, last in (("systemctl daemon-reload", "systemctl daemon-reload"), ("systemctl enable", "systemctl enable pvj-ndi.service"),
                           ("systemctl restart", "systemctl restart pvj-ndi.service")):
            run = Commands(fail=(fail,))
            with self.assertRaises(ndisetup.SetupError, msg=fail):
                self.enable(run)
            self.assertEqual(run.ran[-1], last)
        run = Commands(fail=("systemctl try-restart",))                        # the panel or the player: said, the helper is up
        self.enable(run)
        self.assertIn("restart the box before using NDI", " ".join(self.said))

    def test_the_account_is_there_before_a_file_names_its_group(self):
        def refuse(run, say):
            raise ndisetup.SetupError("the account pvj-ndi is also in pvj and must be in no other group; nothing was set up")
        run = Commands()
        with mock.patch.object(ndisetup, "missing_packages", lambda root="": []), \
                mock.patch.object(ndisetup, "write_units", lambda root, prefix: run.ran.append("FILES")):
            with self.assertRaises(ndisetup.SetupError):
                ndisetup.enable(run=run, say=self.said.append, systemd=lambda: True, account=refuse)
        self.assertEqual(run.ran, [])                      # no file, no reload, no start

    def test_without_a_running_systemd_it_only_enables(self):
        run = Commands()
        self.enable(run, systemd=False)
        self.assertEqual(run.ran, ["ACCOUNT", "FILES  /opt/pvj", "systemctl enable pvj-ndi.service"])

    def test_what_counts_as_avahi_being_there(self):
        self.assertEqual(ndisetup.missing_packages(self.root), ["avahi-daemon", "libavahi-client3"])
        os.makedirs(self.p("/usr/sbin"))
        os.makedirs(self.p("/usr/lib/aarch64-linux-gnu"))
        for path in ("/usr/sbin/avahi-daemon", "/usr/lib/aarch64-linux-gnu/libavahi-client.so.3"):
            open(self.p(path), "w").close()
        self.assertEqual(ndisetup.missing_packages(self.root), [])
        self.assertEqual([name for name, _files in ndisetup.PACKAGES], ["avahi-daemon", "libavahi-client3"])


class DisableTest(Base):
    def test_opting_out_stops_and_removes_the_helper_and_leaves_the_account_and_avahi_and_says_so(self):
        run = Commands()
        removed = []
        with mock.patch.object(ndisetup, "opted_in", lambda root="": True), \
                mock.patch.object(ndisetup.os.path, "exists", lambda path: True), \
                mock.patch.object(ndisetup, "remove_units", lambda root: (removed.append(root), run.ran.append("FILES GONE"))):
            ndisetup.disable(run=run, say=self.said.append, systemd=lambda: True)
        self.assertEqual(run.ran, ["systemctl disable --now pvj-ndi.service", "FILES GONE", "systemctl daemon-reload",
                                   "systemctl try-restart pvj-web.service pvj-player.service"])
        self.assertEqual(removed, [""])
        said = " ".join(self.said)
        for words in ("Left in place: the account and group pvj-ndi", "sudo deluser pvj-ndi", "avahi-daemon and libavahi-client3",
                      "sudo apt-get remove avahi-daemon"):
            self.assertIn(words, said)
        body = inspect.getsource(ndisetup.disable)
        for never in ("userdel", "deluser\"", "groupdel", "\"apt-get\"", "purge"):      # it removes no account and no package itself
            self.assertNotIn(never, body, never)

    def test_on_a_box_that_never_opted_in_it_restarts_nothing(self):
        run = Commands()
        with mock.patch.object(ndisetup, "opted_in", lambda root="": False), \
                mock.patch.object(ndisetup.os.path, "exists", lambda path: False), \
                mock.patch.object(ndisetup, "remove_units", lambda root: None):
            ndisetup.disable(run=run, say=self.said.append, systemd=lambda: True)
        self.assertEqual(run.ran, ["systemctl daemon-reload"])


class FakeSetup:
    SetupError = ndisetup.SetupError
    COMMAND = ndisetup.COMMAND

    def __init__(self, fail=None, opted=False):
        self.calls, self.fail, self.opted = [], fail, opted

    def enable(self, say):
        self.calls.append("enable")
        if self.fail:
            raise ndisetup.SetupError(self.fail)

    def disable(self, say):
        self.calls.append("disable")

    def opted_in(self, root=""):
        return self.opted

    def refresh_main(self, argv, out):
        self.calls.append(("refresh", list(argv)))
        return 0


class CommandTest(Base):
    """`pvj-ndi-runtime`: one command opts a box in (the library and the helper together), one opts it out."""

    def main(self, argv, setup, library=None):
        out = io.StringIO()

        def install(source):
            setup.calls.append("library")
            if isinstance(library, Exception):
                raise library
            return "/opt/pvj-ndi/libndi.so.6"
        with mock.patch.object(ndi.os, "geteuid", lambda: 0), mock.patch.object(ndi, "install_runtime", install), \
                mock.patch.object(ndi, "LIB_DIR", self.p("/opt/pvj-ndi")):
            code = ndi.runtime_main(argv, out, setup=setup)
        return code, out.getvalue()

    def test_install_puts_the_library_in_place_and_then_sets_the_helper_up(self):
        s = FakeSetup()
        code, said = self.main(["install", "/sdk"], s)
        self.assertEqual((code, s.calls), (0, ["library", "enable"]))
        self.assertIn("installed /opt/pvj-ndi/libndi.so.6", said)

    def test_a_library_that_is_refused_sets_nothing_up(self):
        s = FakeSetup()
        code, said = self.main(["install", "/sdk"], s, library=ndi.NdiError("this file is for x86_64, this box is aarch64"))
        self.assertEqual((code, s.calls), (1, ["library"]))
        self.assertIn("this file is for x86_64", said)

    def test_a_setup_that_fails_is_said_and_is_an_error(self):
        s = FakeSetup(fail="could not create the group pvj-ndi")
        code, said = self.main(["install", "/sdk"], s)
        self.assertEqual(code, 1)
        self.assertIn("could not create the group pvj-ndi", said)

    def test_remove_takes_the_helper_off_and_then_the_library(self):
        os.makedirs(self.p("/opt/pvj-ndi"))
        with open(self.p("/opt/pvj-ndi/libndi.so.6"), "w") as f:
            f.write("x")
        s = FakeSetup(opted=True)
        code, said = self.main(["remove"], s)
        self.assertEqual((code, s.calls), (0, ["disable"]))
        self.assertFalse(os.path.exists(self.p("/opt/pvj-ndi")))
        self.assertEqual(self.main(["remove"], FakeSetup())[0], 0)             # nothing there: still fine

    def test_status_says_whether_the_box_is_set_up_and_names_the_command(self):
        code, said = self.main(["status"], FakeSetup())
        self.assertEqual(code, 0)
        self.assertIn("NDI is not set up on this box; to set it up: " + ndi.SETUP_COMMAND, said)
        self.assertIn("NDI is set up on this box", self.main(["status"], FakeSetup(opted=True))[1])
        self.assertEqual(ndisetup.COMMAND, ndi.SETUP_COMMAND)
        self.assertIn(ndi.SETUP_COMMAND, ndi.NOT_SET_UP)

    def test_the_installers_call_goes_to_the_refresh_and_never_to_the_setup(self):
        s = FakeSetup()
        self.assertEqual(self.main(["refresh", "--root", "/x", "--prefix", "/opt/pvj"], s)[0], 0)
        self.assertEqual(s.calls, [("refresh", ["--root", "/x", "--prefix", "/opt/pvj"])])

    def test_the_usage_says_what_install_changes_on_the_box(self):
        code, said = self.main([], FakeSetup())
        self.assertEqual(code, 2)
        for words in ("installs and starts the NDI", "avahi-daemon if it is missing", "restarts the", "nothing of NDI on it",
                      "remove: takes the helper and the library off again", "read its licence", "The owner of the box decides"):
            self.assertIn(words, said)

    def test_without_root_nothing_is_tried(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        s = FakeSetup()
        for argv in (["install", "/sdk"], ["remove"]):
            out = io.StringIO()
            self.assertEqual(ndi.runtime_main(argv, out, setup=s), 1)
            self.assertIn("sudo", out.getvalue())
        self.assertEqual(s.calls, [])


class Untouchable:
    """A helper's client on a box that has no helper: it must not even be asked."""

    def request(self, message, timeout=None):
        raise AssertionError("the socket of a helper that was never installed was tried")

    status = request


class PanelNotSetUpTest(unittest.TestCase):
    def test_the_panels_handle_never_tries_the_socket_and_says_how_to_set_ndi_up(self):
        i = ndi.Input(Untouchable(), "/run/pvj-ndi/ndi.fifo", lambda: (True, ["192.168.1.20"]), log=lambda *_: None, setup=False)
        self.assertEqual(i.sync(), {"ok": False})
        st = i.status()
        self.assertEqual((st["setup"], st["helper"], st["sources"], st["playing"], st["addresses"]), (False, False, [], None, ["192.168.1.20"]))
        self.assertEqual(st["install"]["command"], 'sudo pvj-ndi-runtime install "/path/to/NDI SDK for Linux"')
        self.assertEqual(st["runtime"], {"present": False, "loaded": False, "version": "", "problem": ""})
        with self.assertRaises(ndi.NdiError) as e:
            i.open("0123456789ab")
        self.assertEqual(str(e.exception), ndi.NOT_SET_UP)
        self.assertFalse(i.tick(lambda sid: self.fail("nothing is shown again")))
        self.assertFalse(i.stop())

    def test_a_box_that_opted_in_says_so_and_the_default_is_as_before(self):
        class Down:
            def request(self, message, timeout=None):
                raise ndi.NdiError("the NDI helper (pvj-ndi) is not running")

            def status(self):
                return {"ok": False}
        st = ndi.Input(Down(), "x", lambda: (True, []), log=lambda *_: None).status()
        self.assertEqual((st["setup"], st["helper"]), (True, False))           # set up, and its helper is down: a different thing to say

    def test_the_panel_takes_the_mark_from_its_unit_and_from_nowhere_else(self):
        src = inspect.getsource(server.build)
        self.assertIn('setup=bool(env.get("PVJ_NDI_DIR"))', src)
        self.assertEqual(src.count("PVJ_NDI_DIR"), 2)      # the line and the comment that explains it
        self.assertIn("Environment=PVJ_NDI_DIR=" + paths.NDI_DIR + "\n", ndisetup.WEB_TEXT)
        with open(os.path.join(os.path.dirname(ndisetup.__file__), "..", "install", "pvj-web.service")) as f:
            self.assertNotIn("PVJ_NDI_DIR", f.read())

    def test_a_missing_socket_on_a_box_that_did_opt_in_answers_at_once(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        i = ndi.Input(ndi.Client(os.path.join(d, "missing", "ndi.sock")), os.path.join(d, "ndi.fifo"), lambda: (True, []), log=lambda *_: None)
        t = time.monotonic()
        st = i.status()
        self.assertEqual(i.sync(), {"ok": False})
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertEqual((st["setup"], st["helper"]), (True, False))


class ApiNotSetUpTest(ServerBase):
    """The routes on a box where nobody ran the opt-in command: clean answers, never a 500, nothing waited for."""

    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(Untouchable(), os.path.join(self.tmp, "ndi.fifo"),
                                 lambda: (reg.enabled("inputs-ndi"), list(st.data["ndi"]["addresses"])), log=lambda *_: None, setup=False)

    def test_every_route_answers_cleanly(self):
        self.assertEqual(self.call("GET", "/api/ndi", token=self.full)[0], 409)                # the module is off, as on any box
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": True}, token=self.full)[0], 200)
        t = time.monotonic()
        st, body, _ = self.call("GET", "/api/ndi", token=self.full)
        self.assertEqual((st, body["setup"], body["helper"], body["sources"]), (200, False, False, []))
        self.assertEqual(body["install"]["command"], ndi.SETUP_COMMAND)
        st, body, _ = self.call("POST", "/api/play", {"ndi": "0123456789ab"}, token=self.full)
        self.assertEqual(st, 409)
        self.assertIn("NDI is not set up on this box", body["error"])
        self.assertIn("sudo pvj-ndi-runtime install", body["error"])
        # the addresses are plain settings: they can be filled in before the box is set up, and are kept
        st, body, _ = self.call("POST", "/api/ndi", {"action": "add_address", "address": "192.168.1.20"}, token=self.full)
        self.assertEqual((st, body["addresses"], body["setup"]), (200, ["192.168.1.20"], False))
        self.assertEqual(self.call("POST", "/api/ndi", {"action": "add_address", "address": "8.8.8.8"}, token=self.full)[0], 400)
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[0], 200)
        self.assertFalse(self.api.ndi_tick())
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=self.full)[0], 200)
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": False}, token=self.full)[0], 200)
        self.assertLess(time.monotonic() - t, 5.0)


class TunnelSameAsStreamsTest(SupportBase):
    """The owner's answer of 2026-10-08: a full-access support session may change NDI settings exactly as it may
    change Streams. Nothing had to change for it; this pins that the two stay the same."""

    PAIRS = ((("GET", "/api/streams"), ("GET", "/api/ndi")), (("POST", "/api/streams"), ("POST", "/api/ndi")))

    def test_neither_is_on_the_list_of_what_support_can_never_do_and_both_ask_the_same_role(self):
        routes = self.api.routes()
        for streams, ndi_route in self.PAIRS:
            self.assertEqual(streams in sp.REMOTE_DENY, ndi_route in sp.REMOTE_DENY, ndi_route)
            self.assertEqual(streams[1].startswith(sp.REMOTE_DENY_PREFIX), ndi_route[1].startswith(sp.REMOTE_DENY_PREFIX), ndi_route)
            self.assertEqual(streams in sp.REMOTE_OPEN, ndi_route in sp.REMOTE_OPEN, ndi_route)
            self.assertEqual(routes[streams][0], routes[ndi_route][0], ndi_route)
        from pvj.api import Api
        for words in ("is_remote", "need_local", "remote"):                    # neither handler has a rule of its own for the tunnel
            self.assertEqual(words in inspect.getsource(Api.set_streams), words in inspect.getsource(Api.set_ndi), words)
            self.assertEqual(words in inspect.getsource(Api.get_streams), words in inspect.getsource(Api.get_ndi), words)

    def test_a_support_session_changes_ndi_settings_exactly_as_it_changes_streams(self):
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(Untouchable(), os.path.join(self.tmp, "ndi.fifo"),
                                 lambda: (reg.enabled("inputs-ndi"), list(st.data["ndi"]["addresses"])), log=lambda *_: None, setup=False)
        for module in ("inputs-srt", "inputs-ndi"):
            self.assertEqual(self.h("POST", "/api/modules/" + module, {"enabled": True}, self.full_dev)[0], 200)
        self.ready()
        code = self.start()[1]["code"]
        dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
        self.assertEqual((dev["role"], dev["remote"]), ("full", True))
        add_stream = {"action": "add", "name": "Cam", "url": "srt://192.168.1.30:9000"}
        add_address = {"action": "add_address", "address": "192.168.1.20"}
        # through the tunnel, as support: both are allowed
        got = {"streams": (self.h("GET", "/api/streams", None, dev, TUNNEL)[0], self.h("POST", "/api/streams", add_stream, dev, TUNNEL)[0]),
               "ndi": (self.h("GET", "/api/ndi", None, dev, TUNNEL)[0], self.h("POST", "/api/ndi", add_address, dev, TUNNEL)[0])}
        self.assertEqual(got, {"streams": (200, 200), "ndi": (200, 200)})
        self.assertEqual(self.settings.data["ndi"]["addresses"], ["192.168.1.20"])
        self.assertEqual(len(self.settings.data["streams"]), 1)
        # and removing again, both
        sid = self.settings.data["streams"][0]["id"]
        self.assertEqual(self.h("POST", "/api/streams", {"action": "remove", "id": sid}, dev, TUNNEL)[0], 200)
        self.assertEqual(self.h("POST", "/api/ndi", {"action": "remove_address", "address": "192.168.1.20"}, dev, TUNNEL)[0], 200)
        # a studio device's own login does not work through the tunnel, for either
        for method, path, body in (("POST", "/api/streams", add_stream), ("POST", "/api/ndi", add_address)):
            self.assertEqual(self.h(method, path, body, self.full_dev, TUNNEL)[0], 403, path)
        # a support session of a lower role is held to that role, for both alike
        low = dict(dev, role="live")
        self.assertEqual((self.h("POST", "/api/streams", add_stream, low, TUNNEL)[0], self.h("POST", "/api/ndi", add_address, low, TUNNEL)[0]), (403, 403))
        self.assertEqual((self.h("GET", "/api/streams", None, low, TUNNEL)[0], self.h("GET", "/api/ndi", None, low, TUNNEL)[0]), (200, 200))


if __name__ == "__main__":
    unittest.main()
