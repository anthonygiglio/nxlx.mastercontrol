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
from tests.test_support import TUNNEL, SupportBase


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

    def test_the_help_says_whose_folder_root_is(self):
        out = io.StringIO()
        self.assertEqual(ndisetup.refresh_main(["--help"], out), 2)
        self.assertIn("the installer's own --stage folder and is trusted", out.getvalue())
        self.assertIn("never a path somebody else can write", ndisetup.refresh.__doc__)

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
    """A fake passwd and group file that groupadd and useradd, as run by the code under test, fill in.
    users: name -> (uid, gid, shell); groups: name -> (gid, members)."""

    NOLOGIN = "/usr/sbin/nologin"

    def __init__(self, users=None, groups=None, fail=()):
        self.users, self.groups, self.ran, self.fail = dict(users or {}), dict(groups or {}), [], tuple(fail)

    def run(self, command):
        self.ran.append(" ".join(command))
        if command[0] in self.fail:
            return 1
        if command[0] == "groupadd":
            self.groups[command[-1]] = (900, [])
        elif command[0] == "useradd":
            self.users[command[-1]] = (901, self.groups[command[command.index("--gid") + 1]][0], command[command.index("--shell") + 1])
        return 0

    def user(self, name):
        if name not in self.users:
            return None
        uid, gid, shell = self.users[name]
        return types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid, pw_shell=shell)

    def group(self, name):
        return types.SimpleNamespace(gr_name=name, gr_gid=self.groups[name][0], gr_mem=self.groups[name][1]) if name in self.groups else None

    def all(self):
        return [self.group(n) for n in self.groups]

    def everyone(self):
        return [self.user(n) for n in self.users]

    def check(self):
        return ndisetup.check_account(user=self.user, group=self.group, groups=self.all, users=self.everyone)

    def ensure(self, run=None, say=None):
        return ndisetup.ensure_account(run=self.run, say=lambda m: None, user=self.user, group=self.group, groups=self.all, users=self.everyone)


class AccountTest(unittest.TestCase):
    def test_the_group_then_the_account_with_no_home_no_shell_and_no_other_group(self):
        a = Accounts(users={"pvj-web": (800, 800, Accounts.NOLOGIN)}, groups={"pvj": (800, [])})
        a.check()                                          # an account that is not there yet is nothing to refuse
        a.ensure()
        self.assertEqual(a.ran, ["groupadd --system pvj-ndi",
                                 "useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj-ndi pvj-ndi"])
        self.assertEqual(a.users["pvj-ndi"][1], a.groups["pvj-ndi"][0])
        self.assertEqual(a.groups["pvj"], (800, []))       # nobody was put into group pvj
        a.ran.clear()
        a.ensure()                                         # again: nothing to do
        self.assertEqual(a.ran, [])

    def test_an_account_that_is_in_another_group_is_refused_not_repaired(self):
        ok = (901, 900, Accounts.NOLOGIN)
        for groups in ({"pvj-ndi": (900, []), "pvj": (800, ["pvj-ndi"])}, {"pvj-ndi": (900, []), "video": (44, ["x", "pvj-ndi"])}):
            a = Accounts(users={"pvj-ndi": ok}, groups=groups)
            with self.assertRaises(ndisetup.SetupError) as e:
                a.ensure()
            self.assertIn("no other group", str(e.exception))
            self.assertEqual(a.ran, [])
        a = Accounts(users={"pvj-ndi": (901, 800, Accounts.NOLOGIN)}, groups={"pvj-ndi": (900, []), "pvj": (800, [])})      # its own group is pvj
        with self.assertRaises(ndisetup.SetupError):
            a.ensure()
        self.assertEqual(a.ran, [])

    def test_an_account_that_is_root_shares_a_number_or_can_log_in_is_refused(self):
        """Review L5: the group checks alone took an account pvj-ndi with uid 0, with another account's uid, or with
        a shell. Each is refused before anything is made, also when the group is still missing."""
        groups = {"pvj-ndi": (900, []), "pvj": (800, [])}
        for users, words in (({"pvj-ndi": (0, 900, Accounts.NOLOGIN)}, "root under another name"),
                             ({"pvj-ndi": (800, 900, Accounts.NOLOGIN), "pvj-web": (800, 800, Accounts.NOLOGIN)}, "shares its number with pvj-web"),
                             ({"pvj-ndi": (901, 900, "/bin/bash")}, "has a login shell (/bin/bash)"),
                             ({"pvj-ndi": (901, 900, "")}, "has a login shell")):
            for g in (groups, {"pvj": (800, [])}):
                a = Accounts(users=users, groups=g)
                with self.assertRaises(ndisetup.SetupError, msg=words) as e:
                    a.ensure()
                self.assertIn(words, str(e.exception))
                self.assertEqual(a.ran, [], words)         # nothing was made, not the group either
                with self.assertRaises(ndisetup.SetupError):
                    a.check()
        for shell in ndisetup.NO_LOGIN:                    # every way of saying "no login" is taken
            Accounts(users={"pvj-ndi": (901, 900, shell)}, groups=groups).ensure()

    def test_a_group_that_is_roots_shares_its_number_or_has_members_is_refused(self):
        """Second review, M2: only the account's number was looked at. A group pvj-ndi with gid 0 was taken, and so
        was one that shares its number with group pvj while no account exists yet: useradd --gid pvj-ndi then puts
        the helper into pvj's number, which is what D62 forbids."""
        ok = (901, 900, Accounts.NOLOGIN)
        for users, groups, words in (
                ({"pvj-ndi": (901, 0, Accounts.NOLOGIN)}, {"pvj-ndi": (0, [])}, "root's group under another name"),
                ({}, {"pvj-ndi": (0, [])}, "root's group under another name"),
                ({}, {"pvj-ndi": (800, []), "pvj": (800, [])}, "shares its number with pvj"),
                ({"pvj-ndi": (901, 800, Accounts.NOLOGIN)}, {"pvj-ndi": (800, []), "pvj": (800, [])}, "shares its number with pvj"),
                ({"pvj-ndi": ok}, {"pvj-ndi": (900, ["mallory"])}, "has members (mallory)"),
                ({}, {"pvj-ndi": (900, ["pvj-web", "gigbox"])}, "has members (gigbox, pvj-web)"),
                ({"pvj-ndi": ok, "guest": (1001, 900, "/bin/bash")}, {"pvj-ndi": (900, [])}, "is the own group of guest")):
            a = Accounts(users=users, groups=groups)
            with self.assertRaises(ndisetup.SetupError, msg=words) as e:
                a.ensure()
            self.assertIn(words, str(e.exception))
            self.assertIn("nothing was set up", str(e.exception))
            self.assertEqual(a.ran, [], words)             # no account is made into such a group
            with self.assertRaises(ndisetup.SetupError):
                a.check()
        # the account named as a member of its own group is what some tools write, and is fine
        Accounts(users={"pvj-ndi": ok}, groups={"pvj-ndi": (900, ["pvj-ndi"]), "pvj": (800, [])}).ensure()

    def test_an_account_that_comes_out_of_useradd_wrong_is_refused_and_the_words_are_true(self):
        """Second review, L2: the look after groupadd and useradd had no test, and its refusal said "nothing was set
        up" about an account that had just been made."""
        class Meddling(Accounts):                          # a box whose rules for new accounts add a group, or a shell
            def __init__(self, how, **kw):
                super().__init__(**kw)
                self.how = how

            def run(self, command):
                code = super().run(command)
                if command[0] == "useradd" and self.how == "group":
                    self.groups["users"] = (100, ["pvj-ndi"])
                if command[0] == "useradd" and self.how == "shell":
                    uid, gid, _shell = self.users["pvj-ndi"]
                    self.users["pvj-ndi"] = (uid, gid, "/bin/sh")
                return code
        for how, words in (("group", "is also in users"), ("shell", "has a login shell (/bin/sh)")):
            a = Meddling(how, groups={"pvj": (800, [])})
            with self.assertRaises(ndisetup.SetupError) as e:
                a.ensure()
            said = str(e.exception)
            self.assertIn(words, said)
            self.assertNotIn("nothing was set up", said)   # something was: the group and the account
            self.assertIn("it was made a moment ago by this command", said)
            self.assertIn("sudo deluser pvj-ndi; sudo delgroup pvj-ndi", said)
            self.assertEqual([c.split()[0] for c in a.ran], ["groupadd", "useradd"])
        # an account that was there before and is wrong is still said as "nothing was set up"
        a = Accounts(users={"pvj-ndi": (901, 900, "/bin/sh")}, groups={"pvj-ndi": (900, [])})
        with self.assertRaises(ndisetup.SetupError) as e:
            a.ensure()
        self.assertIn("nothing was set up", str(e.exception))

    def test_a_command_that_fails_stops_the_setup(self):
        for fail in ("groupadd", "useradd"):
            a = Accounts(fail=(fail,))
            with self.assertRaises(ndisetup.SetupError, msg=fail):
                a.ensure()
            self.assertEqual(a.ran[-1].split()[0], fail)


class EnableTest(Base):
    """`sudo pvj-ndi-runtime install ...`, the part after the library: the opt-in. Every command it runs, in order.
    The files are the real ones, written into a fake root; only the commands and the accounts are stand-ins."""

    def setUp(self):
        super().setUp()
        self.slept = []

    def enable(self, run, missing=(), systemd=True, apt=True, account=None, overlay=False, **more):
        def files(root, prefix, template=None):
            run.ran.append("FILES %s" % prefix)
            return real(root, prefix, template)
        real = ndisetup.write_units
        with mock.patch.object(ndisetup, "missing_packages", lambda root="": list(missing)), \
                mock.patch.object(ndisetup, "write_units", files):
            return ndisetup.enable(run=run, say=self.said.append, systemd=lambda: systemd, root=self.root,
                                   account=account or (lambda run, say: run.ran.append("ACCOUNT")),
                                   which=lambda name: "/usr/bin/apt-get" if apt else None,
                                   check=more.get("check", lambda root: ndisetup.check(root, account=lambda: None)),
                                   sleep=self.slept.append, overlay=lambda: overlay)

    UP = ["systemctl is-active --quiet pvj-ndi.service"] * 3

    def test_everything_it_does_and_the_order(self):
        run = Commands()
        self.assertIs(self.enable(run, missing=["avahi-daemon", "libavahi-client3"]), True)
        self.assertEqual(run.ran, ["apt-get install -y --no-install-recommends avahi-daemon libavahi-client3",
                                   "ACCOUNT", "FILES /opt/pvj",
                                   "systemctl daemon-reload", "systemctl enable pvj-ndi.service", "systemctl restart pvj-ndi.service"]
                         + self.UP + ["systemctl try-restart pvj-web.service pvj-player.service"])
        self.assertTrue(ndisetup.opted_in(self.root))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-ndi\n", self.read(ndisetup.UNIT_PATH))
        self.assertIn("the screen goes dark for a moment", " ".join(self.said))
        self.assertIn("NDI is set up on this box", self.said[-1])
        self.assertIn("was running 3 seconds after its start", self.said[-1])
        self.assertEqual(self.slept, [1, 1, 1])

    def test_nothing_is_installed_where_avahi_is_already_there(self):
        run = Commands()
        self.enable(run)
        self.assertEqual([c for c in run.ran if "apt" in c], [])
        self.assertEqual(run.ran[:2], ["ACCOUNT", "FILES /opt/pvj"])

    def test_no_network_for_apt_is_said_and_is_not_the_end(self):
        run = Commands(fail=("apt-get",))
        self.enable(run, missing=["avahi-daemon"])
        self.assertIn("systemctl restart pvj-ndi.service", run.ran)
        self.assertIn("sudo apt-get install avahi-daemon", " ".join(self.said))
        run = Commands()
        self.enable(run, missing=["avahi-daemon"], apt=False)                 # not a Debian: said, nothing run for it
        self.assertEqual([c for c in run.ran if "apt" in c], [])
        self.assertIn("no apt-get here", " ".join(self.said))

    def test_a_prefix_or_a_template_that_is_refused_stops_it_before_anything_is_changed(self):
        """Review L1: the install folder and the unit's template were looked at only after apt and the account, so a
        box whose install.json holds a folder written another way got a package and an account and then an error."""
        os.makedirs(self.p("/etc/pvj"))
        for written in ("/opt/pvj/", "/opt//pvj", "/opt/pvj\nExecStartPre=/bin/evil", "relative/pvj"):
            with open(self.p(ndisetup.INSTALL_JSON), "w") as f:
                json.dump({"prefix": written}, f)
            run = Commands()
            with self.assertRaises(ndisetup.SetupError, msg=written):
                self.enable(run, missing=["avahi-daemon"])
            self.assertEqual((run.ran, self.tree()), ([], ["/etc/pvj/install.json"]), written)
            with self.assertRaises(ndisetup.SetupError):   # and the command asks before it copies the library
                ndisetup.check(self.root, account=lambda: None)
        os.unlink(self.p(ndisetup.INSTALL_JSON))
        with mock.patch.object(ndisetup, "TEMPLATE", self.p("/no/such/template")):
            run = Commands()
            with self.assertRaises(OSError):
                self.enable(run, missing=["avahi-daemon"])
        self.assertEqual((run.ran, self.tree()), ([], []))

    def test_an_account_that_is_refused_stops_it_before_a_package_is_installed(self):
        """Review L1, second half: the refusal of an account came after avahi."""
        bad = Accounts(users={"pvj-ndi": (901, 900, Accounts.NOLOGIN)}, groups={"pvj-ndi": (900, []), "pvj": (800, ["pvj-ndi"])})
        run = Commands()
        with self.assertRaises(ndisetup.SetupError) as e:
            self.enable(run, missing=["avahi-daemon"], check=lambda root: ndisetup.check(root, account=bad.check))
        self.assertIn("no other group", str(e.exception))
        self.assertEqual((run.ran, self.tree(), bad.ran), ([], [], []))

    def test_a_step_that_fails_after_the_files_were_written_undoes_them_on_a_box_that_was_not_set_up(self):
        """Review L2: a failing daemon-reload, enable or restart left the mark, so the box counted as opted in."""
        for fail, last in (("systemctl daemon-reload", "systemctl daemon-reload"), ("systemctl enable", "systemctl enable pvj-ndi.service"),
                           ("systemctl restart", "systemctl restart pvj-ndi.service")):
            run = Commands(fail=(fail,))
            self.said.clear()
            with self.assertRaises(ndisetup.SetupError, msg=fail):
                self.enable(run)
            done = run.ran[:run.ran.index(last) + 1]
            self.assertEqual(done[-1], last)
            self.assertNotIn("systemctl try-restart pvj-web.service pvj-player.service", run.ran)      # nothing later ran
            self.assertEqual(run.ran[len(done):], ["systemctl disable --now pvj-ndi.service", "systemctl daemon-reload"], fail)
            self.assertFalse(ndisetup.opted_in(self.root), fail)
            self.assertEqual(self.tree(), [], fail)
            self.assertIn("the setup failed and was undone: this box is not set up for NDI", " ".join(self.said))

    def test_something_in_the_place_of_a_file_is_refused_before_anything_is_changed(self):
        """Second review, M1, the first half: a folder or a link where one of the three files goes is found by the
        check, before a package, an account or a file."""
        for path, make in ((ndisetup.WEB_DROPIN, "folder"), (ndisetup.WEB_DROPIN, "link"), (ndisetup.UNIT_PATH, "folder"),
                           (ndisetup.PLAYER_DROPIN, "link"), (os.path.dirname(ndisetup.PLAYER_DROPIN), "link"),
                           (os.path.dirname(ndisetup.WEB_DROPIN), "file")):
            self.root = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, self.root, True)
            os.makedirs(os.path.dirname(self.p(path)))
            if make == "folder":
                os.makedirs(self.p(path))
            elif make == "link":
                os.symlink(self.p("/elsewhere"), self.p(path))
            else:
                open(self.p(path), "w").close()
            before = self.tree()
            run = Commands()
            with self.assertRaises(ndisetup.SetupError, msg=(path, make)) as e:
                self.enable(run, missing=["avahi-daemon"])
            self.assertIn("nothing was changed", str(e.exception))
            self.assertIn("remove it by hand", str(e.exception))
            self.assertEqual((run.ran, self.tree()), ([], before), (path, make))
            self.assertFalse(os.path.lexists(self.p("/elsewhere")))

    def test_a_file_that_cannot_be_written_undoes_the_ones_that_were(self):
        """Second review, M1: write_units was outside the part that is undone, and only SetupError was caught. With
        a folder in the place of the mark (here past the check, as if it appeared in between) the unit and the
        player's drop-in were written and stayed, under an error that said nothing had been written."""
        os.makedirs(self.p(ndisetup.WEB_DROPIN))           # a folder where the mark goes; the box is not set up
        run = Commands()
        with self.assertRaises(ndisetup.SetupError) as e:
            self.enable(run, check=lambda root: None)
        self.assertNotIn("nothing was written", str(e.exception))
        self.assertIn("it was not written", str(e.exception))
        for path in (ndisetup.UNIT_PATH, ndisetup.PLAYER_DROPIN):
            self.assertFalse(os.path.lexists(self.p(path)), path)
        self.assertFalse(ndisetup.opted_in(self.root))
        self.assertTrue(os.path.isdir(self.p(ndisetup.WEB_DROPIN)))            # not this command's to remove; it says so
        said = " ".join(self.said)
        self.assertIn("the setup failed and was undone: this box is not set up for NDI", said)
        self.assertIn("50-pvj-ndi.conf could not be removed", said)
        self.assertEqual(run.ran, ["ACCOUNT", "FILES /opt/pvj", "systemctl disable --now pvj-ndi.service", "systemctl daemon-reload"])

    def test_a_disk_that_is_full_or_read_only_undoes_the_setup_too(self):
        real = ndisetup._write
        for failing in (ndisetup.PLAYER_DROPIN, ndisetup.WEB_DROPIN):
            def write(path, text, failing=failing):
                if path.endswith(failing):
                    raise OSError(28, "No space left on device")
                return real(path, text)
            run = Commands()
            self.said.clear()
            with mock.patch.object(ndisetup, "_write", write):
                with self.assertRaises(OSError):
                    self.enable(run)
            self.assertEqual(self.tree(), [], failing)
            self.assertFalse(ndisetup.opted_in(self.root))
            self.assertIn("the setup failed and was undone", " ".join(self.said))
            self.assertNotIn("systemctl enable pvj-ndi.service", run.ran)
        # and the command says it in words and ends with an error
        out = io.StringIO()

        class Setup(FakeSetup):
            def enable(self, say):
                raise OSError(30, "Read-only file system")
        with mock.patch.object(ndi.os, "geteuid", lambda: 0), mock.patch.object(ndi, "install_runtime", lambda source: "/opt/pvj-ndi/libndi.so.6"):
            self.assertEqual(ndi.runtime_main(["install", "/sdk"], out, setup=Setup()), 1)
        self.assertIn("Read-only file system", out.getvalue())

    def test_a_step_that_fails_on_a_box_that_was_already_set_up_leaves_it_set_up(self):
        self.enable(Commands())
        before = self.tree()
        run = Commands(fail=("systemctl restart",))
        self.said.clear()
        with self.assertRaises(ndisetup.SetupError):
            self.enable(run)                               # run again, to repair or for a newer library
        self.assertTrue(ndisetup.opted_in(self.root))
        self.assertEqual(self.tree(), before)
        self.assertNotIn("systemctl disable --now pvj-ndi.service", run.ran)
        self.assertNotIn("undone", " ".join(self.said))

    def test_a_helper_that_does_not_stay_up_is_said_and_is_not_called_running(self):
        """Review L3: the unit is Type=simple, so `restart` answers 0 for a helper that ends at once."""
        run = Commands(fail=("systemctl is-active",))
        self.assertIs(self.enable(run), False)
        self.assertIn("NOT running", self.said[-1])
        self.assertIn("journalctl -u pvj-ndi", self.said[-1])
        self.assertNotIn("installed and running", " ".join(self.said))
        self.assertNotIn("was running", " ".join(self.said))
        self.assertTrue(ndisetup.opted_in(self.root))      # left in place, to be looked at on the box
        self.assertIn("systemctl try-restart pvj-web.service pvj-player.service", run.ran)
        self.assertEqual(run.ran.count("systemctl is-active --quiet pvj-ndi.service"), 1)       # the first "no" is the answer

        class Later(Commands):                             # up at first, gone at the third look
            def __call__(self, command):
                super().__call__(command)
                return 1 if self.ran.count("systemctl is-active --quiet pvj-ndi.service") == 3 and "is-active" in command else 0
        self.assertIs(self.enable(Later()), False)

    def test_the_panel_or_the_player_not_restarting_is_said(self):
        run = Commands(fail=("systemctl try-restart",))
        self.assertIs(self.enable(run), True)
        self.assertIn("restart the box before using NDI", " ".join(self.said))

    def test_the_account_is_there_before_a_file_names_its_group(self):
        def refuse(run, say):
            raise ndisetup.SetupError("could not create the group pvj-ndi")
        run = Commands()
        with self.assertRaises(ndisetup.SetupError):
            self.enable(run, account=refuse)
        self.assertEqual((run.ran, self.tree()), ([], []))                    # no file, no reload, no start

    def test_without_a_running_systemd_it_only_enables(self):
        run = Commands()
        self.assertIs(self.enable(run, systemd=False), True)
        self.assertEqual(run.ran, ["ACCOUNT", "FILES /opt/pvj", "systemctl enable pvj-ndi.service"])
        self.assertEqual(self.slept, [])

    def test_on_the_read_only_root_it_says_that_the_setup_will_not_last(self):
        run = Commands()
        self.enable(run, overlay=True)
        said = " ".join(self.said)
        for words in ("the read-only root is active", "gone at the next restart", "sudo pvj-rootfs disable", "sudo pvj-rootfs enable"):
            self.assertIn(words, said)
        self.assertEqual(self.said.count(ndisetup.OVERLAY_WARNING), 2)        # before it starts and near the end
        self.said.clear()
        self.enable(Commands())
        self.assertNotIn("read-only root", " ".join(self.said))

    def test_what_counts_as_avahi_being_there(self):
        self.assertEqual(ndisetup.missing_packages(self.root), ["avahi-daemon", "libavahi-client3"])
        os.makedirs(self.p("/usr/sbin"))
        os.makedirs(self.p("/usr/lib/aarch64-linux-gnu"))
        for path in ("/usr/sbin/avahi-daemon", "/usr/lib/aarch64-linux-gnu/libavahi-client.so.3"):
            open(self.p(path), "w").close()
        self.assertEqual(ndisetup.missing_packages(self.root), [])
        self.assertEqual([name for name, _files in ndisetup.PACKAGES], ["avahi-daemon", "libavahi-client3"])


class DisableTest(Base):
    def disable(self, run, systemd=True):
        return ndisetup.disable(run=run, say=self.said.append, systemd=lambda: systemd, root=self.root)

    def test_opting_out_stops_and_removes_the_helper_and_leaves_the_account_and_avahi_and_says_so(self):
        ndisetup.write_units(self.root, "/opt/pvj")
        run = Commands()
        self.disable(run)
        self.assertEqual(run.ran, ["systemctl disable --now pvj-ndi.service", "systemctl daemon-reload",
                                   "systemctl try-restart pvj-web.service pvj-player.service"])
        self.assertEqual(self.tree(), [])
        self.assertFalse(ndisetup.opted_in(self.root))
        said = " ".join(self.said)
        for words in ("Left in place: the account and group pvj-ndi", "sudo deluser pvj-ndi", "avahi-daemon and libavahi-client3",
                      "sudo apt-get remove avahi-daemon"):
            self.assertIn(words, said)
        body = inspect.getsource(ndisetup.disable)
        for never in ("userdel", "deluser\"", "groupdel", "\"apt-get\"", "purge"):      # it removes no account and no package itself
            self.assertNotIn(never, body, never)

    def test_on_a_box_that_never_opted_in_it_restarts_nothing(self):
        run = Commands()
        self.disable(run)
        self.assertEqual(run.ran, ["systemctl daemon-reload"])

    def test_something_that_cannot_be_removed_does_not_stop_the_rest(self):
        """Review L6: a folder where the mark should be made unlink raise, and `remove` stopped after disabling the
        helper: the other files and the library stayed."""
        ndisetup.write_units(self.root, "/opt/pvj")
        os.unlink(self.p(ndisetup.WEB_DROPIN))
        os.makedirs(self.p(ndisetup.WEB_DROPIN + "/inside"))
        run = Commands()
        with self.assertRaises(ndisetup.SetupError) as e:
            self.disable(run)
        self.assertIn("50-pvj-ndi.conf could not be removed", str(e.exception))
        self.assertIn("remove it by hand", str(e.exception))
        for path in (ndisetup.UNIT_PATH, ndisetup.PLAYER_DROPIN):             # the rest went all the same
            self.assertFalse(os.path.lexists(self.p(path)), path)
        self.assertEqual(run.ran, ["systemctl disable --now pvj-ndi.service", "systemctl daemon-reload",
                                   "systemctl try-restart pvj-web.service pvj-player.service"])
        self.assertIn("Left in place", " ".join(self.said))
        self.assertEqual(len(ndisetup.remove_units(self.root)), 1)            # and it says so again, every time


class FakeSetup:
    SetupError = ndisetup.SetupError
    COMMAND = ndisetup.COMMAND

    def __init__(self, fail=None, opted=False, refuse=None, running=True, left=None):
        self.calls, self.fail, self.opted, self.refuse, self.running, self.left = [], fail, opted, refuse, running, left

    def check(self, root=""):
        self.calls.append("check")
        if self.refuse:
            raise ndisetup.SetupError(self.refuse)

    def enable(self, say):
        self.calls.append("enable")
        if self.fail:
            raise ndisetup.SetupError(self.fail)
        return self.running

    def disable(self, say):
        self.calls.append("disable")
        if self.left:
            raise ndisetup.SetupError(self.left)

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

    def test_install_asks_what_could_refuse_then_puts_the_library_in_place_then_sets_the_helper_up(self):
        s = FakeSetup()
        code, said = self.main(["install", "/sdk"], s)
        self.assertEqual((code, s.calls), (0, ["check", "library", "enable"]))
        self.assertIn("installed /opt/pvj-ndi/libndi.so.6", said)

    def test_a_setup_that_would_be_refused_does_not_even_copy_the_library(self):
        s = FakeSetup(refuse="the account pvj-ndi is also in pvj and must be in no other group; nothing was set up")
        code, said = self.main(["install", "/sdk"], s)
        self.assertEqual((code, s.calls), (1, ["check"]))
        self.assertIn("must be in no other group", said)

    def test_a_library_that_is_refused_sets_nothing_up(self):
        s = FakeSetup()
        code, said = self.main(["install", "/sdk"], s, library=ndi.NdiError("this file is for x86_64, this box is aarch64"))
        self.assertEqual((code, s.calls), (1, ["check", "library"]))
        self.assertIn("this file is for x86_64", said)

    def test_a_refused_library_through_the_real_command_leaves_no_folder(self):
        """Review M1, through the command itself: nothing else is changed, not even /opt/pvj-ndi."""
        wrong = self.p("/not-a-library")
        with open(wrong, "w") as f:
            f.write("#!/bin/sh\n" + " " * 80)
        s, out = FakeSetup(), io.StringIO()
        with mock.patch.object(ndi.os, "geteuid", lambda: 0), mock.patch.object(ndi, "LIB_DIR", self.p("/opt/pvj-ndi")), \
                mock.patch.object(ndi.install_runtime, "__defaults__", (self.p("/opt/pvj-ndi"), None, False)):
            self.assertEqual(ndi.runtime_main(["install", wrong], out, setup=s), 1)
        self.assertEqual(s.calls, ["check"])
        self.assertFalse(os.path.lexists(self.p("/opt/pvj-ndi")))

    def test_a_setup_that_fails_is_said_and_is_an_error(self):
        s = FakeSetup(fail="could not create the group pvj-ndi")
        code, said = self.main(["install", "/sdk"], s)
        self.assertEqual(code, 1)
        self.assertIn("could not create the group pvj-ndi", said)

    def test_a_helper_that_did_not_stay_up_is_an_error_for_the_command(self):
        self.assertEqual(self.main(["install", "/sdk"], FakeSetup(running=False))[0], 1)
        self.assertEqual(self.main(["install", "/sdk"], FakeSetup(running=True))[0], 0)

    def test_remove_takes_the_helper_off_and_then_the_library(self):
        os.makedirs(self.p("/opt/pvj-ndi"))
        with open(self.p("/opt/pvj-ndi/libndi.so.6"), "w") as f:
            f.write("x")
        s = FakeSetup(opted=True)
        code, said = self.main(["remove"], s)
        self.assertEqual((code, s.calls), (0, ["disable"]))
        self.assertFalse(os.path.exists(self.p("/opt/pvj-ndi")))
        self.assertEqual(self.main(["remove"], FakeSetup())[0], 0)             # nothing there: still fine

    def test_remove_takes_the_library_also_when_something_of_the_helper_could_not_be_removed(self):
        """Review L6: the library stayed when the helper's files could not all be removed."""
        os.makedirs(self.p("/opt/pvj-ndi"))
        with open(self.p("/opt/pvj-ndi/libndi.so.6"), "w") as f:
            f.write("x")
        code, said = self.main(["remove"], FakeSetup(opted=True, left="not everything of the NDI helper could be removed: x"))
        self.assertEqual(code, 1)
        self.assertIn("not everything of the NDI helper could be removed", said)
        self.assertIn("removed NDI's library", said)
        self.assertFalse(os.path.exists(self.p("/opt/pvj-ndi")))

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


class RollbackTest(unittest.TestCase):
    """Review M2: an update or a rollback switches every service to another release. The helper must follow, and a
    release from before the NDI input must not leave a unit that fails for ever."""

    def test_the_updater_restarts_a_running_helper_with_the_release_it_switched_to(self):
        from pvj.update import Updater
        ran = []
        u = Updater(root="", run=lambda command, **kw: ran.append(command))
        real = os.path.isdir
        with mock.patch("os.path.isdir", lambda path: True if path == "/run/systemd/system" else real(path)):
            u._systemd_restart()
        self.assertEqual(ran, [["systemctl", "try-restart", "pvj-netd.service"], ["systemctl", "try-restart", "pvj-ndi.service"],
                               ["systemctl", "restart", "pvj-player.service", "pvj-web.service"]])
        self.assertEqual([c for c in ran if "pvj-ndi.service" in c], [["systemctl", "try-restart", "pvj-ndi.service"]])      # alone in its call
        # try-restart, never restart or start: on a box that did not opt in there is no such unit, and nothing is started
        self.assertFalse([c for c in ran if "pvj-ndi.service" in c and c[1] != "try-restart"])

    def test_the_unit_is_skipped_not_failed_where_its_program_is_not(self):
        with open(ndisetup.TEMPLATE) as f:
            text = f.read()
        unit, service = text.split("[Service]")
        self.assertIn("\nConditionPathExists=@PVJ_DIR@/bin/pvj-ndi\n", unit)      # in [Unit], and the very file ExecStart names
        self.assertIn("\nExecStart=@PVJ_DIR@/bin/pvj-ndi\n", service)
        self.assertIn("ConditionPathExists=/opt/pvj/current/bin/pvj-ndi\n", ndisetup.unit_text("/opt/pvj"))
        self.assertNotIn("AssertPathExists", text)         # an assertion that is not met is a failure; a condition is not


class HelperIdlesTest(unittest.TestCase):
    """Second review, N6: what the helper does when the library is missing or does not load decides how hard the
    unit's "start again every 2 seconds, without a limit" can bite. It stays up and says what is wrong; it does not
    end. (What does end it: the module switched off with the library loaded, on purpose; and a crash in the library.)"""

    def test_a_library_that_does_not_load_leaves_the_helper_up_and_saying_so(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        tried, ended = [], []

        def loader(path):
            tried.append(path)
            raise ndi.NdiError("the NDI runtime could not be loaded (wrong ELF class)")
        s = ndi.Service(d, os.path.join(d, "libndi.so.6"), loader=loader, log=lambda *_: None, problem=lambda path: None)
        self.addCleanup(s.close)
        s.on_unload = lambda: ended.append(True)
        for _ in range(3):
            reply = s.handle({"cmd": "configure", "on": True, "addresses": []})
            st = s.handle({"cmd": "status"})
            self.assertIn("could not be loaded", json.dumps([reply, st]))
        self.assertTrue(tried)
        self.assertEqual(ended, [])                        # nothing asked the process to end
        s.handle({"cmd": "configure", "on": False, "addresses": []})
        self.assertEqual(ended, [])                        # off without a library loaded: no clean start is needed either
        src = inspect.getsource(ndi.main)
        self.assertEqual(src.count("os._exit"), 1)         # the one way out is the on_unload above
        self.assertIn("service.on_unload = lambda: threading.Timer(0.5, os._exit, [0]).start()", src)


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

    def test_the_panel_takes_the_mark_from_its_environment_which_only_root_sets(self):
        # The panel believes PVJ_NDI_DIR in its environment and nothing else. Two things can put it there, both
        # root's: the drop-in the opt-in writes, and /etc/pvj/pvj.env (the unit's EnvironmentFile, root's file).
        # Set there by hand on a box without the helper, the page says "the helper is not running": wrong words,
        # no harm, and not something the panel's own account can do.
        src = inspect.getsource(server.build)
        self.assertIn('setup=bool(env.get("PVJ_NDI_DIR"))', src)
        self.assertEqual(src.count("PVJ_NDI_DIR"), 2)      # the line and the comment that explains it
        with open(os.path.join(os.path.dirname(ndisetup.__file__), "..", "install", "pvj-web.service")) as f:
            self.assertIn("EnvironmentFile=-/etc/pvj/pvj.env\n", f.read())
        with open(os.path.join(os.path.dirname(ndisetup.__file__), "..", "install", "install.sh")) as f:
            self.assertNotIn("PVJ_NDI", f.read())          # the installer never writes it into pvj.env
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
