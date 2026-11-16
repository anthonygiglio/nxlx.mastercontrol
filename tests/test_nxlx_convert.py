# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""bin/nxlx-convert in stage mode, on a copy of an old node made by install/install.sh --stage (rename PR 2, D136).

The staged node is node-02's shape (the player runs as the login account pvj-dev) with node-01's NDI helper and its
folders added, accounts in the staged etc/passwd and etc/group, a sudoers file, a settings file with a paired
device's hash, a node id and a certificate. Nothing outside the temp folder is touched."""
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONVERT = os.path.join(REPO, "bin", "nxlx-convert")
BEFORE_SYSTEM_RENAME = "0ff0ddbeb662efb405186a0e774839cb1f61b2de"   # master with release 0.1.1, before rename PR 7
OLDNAME = re.compile(r"pvj", re.I)


def load_converter():
    loader = importlib.machinery.SourceFileLoader("nxlx_convert", CONVERT)
    spec = importlib.util.spec_from_loader("nxlx_convert", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def write(root, rel, text, mode=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def link(root, rel, target):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.symlink(target, path)


PASSWD = """root:x:0:0:root:/root:/bin/bash
pi:x:1000:1000:,,,:/home/pi:/bin/bash
pvj-dev:x:1001:1001:deploy:/home/pvj-dev:/bin/bash
pvj-web:x:990:990::/nonexistent:/usr/sbin/nologin
pvj-player:x:989:990::/var/lib/pvj-player:/usr/sbin/nologin
pvj-sound:x:988:29::/nonexistent:/usr/sbin/nologin
pvj-link:x:987:991::/nonexistent:/usr/sbin/nologin
pvj-raster:x:986:992::/nonexistent:/usr/sbin/nologin
pvj-ndi:x:985:993::/nonexistent:/usr/sbin/nologin
"""
GROUP = """root:x:0:
sudo:x:27:pi,pvj-dev
audio:x:29:pvj-dev,pvj-sound
video:x:44:pvj-dev,pvj-player
pi:x:1000:
pvj-dev:x:1001:
pvj:x:990:pvj-dev,pvj-web,pvj-player
pvj-link:x:991:pvj-web
pvj-raster:x:992:pvj-web
pvj-ndi:x:993:pvj-web,pvj-dev
"""


def stage_old_node(src_copy):
    """A temp folder holding an old node: the installer's own stage output plus what a real node has besides."""
    root = tempfile.mkdtemp()
    r = subprocess.run([os.path.join(src_copy, "install", "install.sh"), "--stage", root, "--user", "pvj-dev"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    write(root, "etc/passwd", PASSWD)
    write(root, "etc/group", GROUP)
    write(root, "etc/shadow", "".join("%s:*:20000:0:99999:7:::\n" % l.split(":")[0] for l in PASSWD.splitlines()), 0o640)
    write(root, "etc/gshadow", "".join("%s:!::%s\n" % (l.split(":")[0], l.split(":")[3]) for l in GROUP.splitlines()), 0o640)
    write(root, "etc/subuid", "pi:100000:65536\npvj-dev:165536:65536\n")
    write(root, "etc/subgid", "pi:100000:65536\npvj-dev:165536:65536\n")
    write(root, "etc/sudoers.d/010_pvj-dev", "pvj-dev ALL=(ALL) NOPASSWD:ALL\n", 0o440)
    write(root, "etc/sudoers.d/README", "# files in this folder are read by sudo\n", 0o440)
    write(root, "home/pvj-dev/.ssh/authorized_keys", "ssh-ed25519 AAAAC3Nza deploy\n", 0o600)
    write(root, "var/lib/pvj/settings.json", json.dumps({"schema": 14, "devices": [{"token_hash": "9f" * 32}]}))
    write(root, "var/lib/pvj/node-id", "0123456789abcdef0123456789abcdef\n")
    write(root, "var/lib/pvj/tls/node.key", "-----BEGIN PRIVATE KEY-----\nx\n", 0o600)
    write(root, "var/lib/pvj/events.json", "[]\n")
    write(root, "var/lib/pvj-player/late-screen/.keep", "")
    write(root, "var/lib/pvj-netd/.keep", "")
    write(root, "var/lib/pvj-support/keys", "")
    write(root, "opt/pvj-ndi/lib/libndi.so.6", "not really a library\n")
    os.makedirs(os.path.join(root, "opt/pvj/releases/9.9.1.new"))          # an install that was cut short
    write(root, "etc/systemd/system/pvj-ndi.service",
          "[Service]\nUser=pvj-ndi\nGroup=pvj-ndi\nExecStart=/opt/pvj/current/bin/pvj-ndi\n"
          "Environment=PVJ_NDI_LIB=/opt/pvj-ndi/lib\nRuntimeDirectory=pvj-ndi\n")
    write(root, "etc/systemd/system/pvj-web.service.d/50-pvj-ndi.conf",
          "[Service]\nSupplementaryGroups=pvj-ndi\nEnvironment=PVJ_NDI_DIR=/run/pvj-ndi\n")
    write(root, "etc/systemd/system/pvj-player.service.d/50-pvj-ndi.conf",
          "[Service]\nSupplementaryGroups=pvj-ndi\nEnvironment=PVJ_NDI_PIPE_DIR=/run/pvj-ndi-pipe\n")
    write(root, "etc/tmpfiles.d/pvj-ndi.conf", "d /run/pvj-ndi-pipe 0750 root pvj-ndi -\n")
    for unit in ("pvj-web.service", "pvj-player.service", "pvj-ndi.service", "pvj-sysd.service"):
        link(root, "etc/systemd/system/multi-user.target.wants/" + unit, "/etc/systemd/system/" + unit)
    link(root, "etc/systemd/system/sockets.target.wants/pvj-raster.socket", "/etc/systemd/system/pvj-raster.socket")
    write(root, "etc/avahi/services/nxlx-mastercontrol.service", "<!-- announced by pvj-sysd -->\n")
    write(root, "proc/mounts", "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n")
    # The release the cutover installs: the programs under their new names (a copy of the old one, renamed), and
    # "current" pointing at it, as install.sh leaves it from rename PR 7 on. 9.9.1 stays as the previous release.
    old, new = os.path.join(root, "opt/pvj/releases/9.9.1"), os.path.join(root, "opt/pvj/releases/9.9.2")
    # install.sh now stages the package as mctl/; an old node holds it as pvj/
    os.rename(os.path.join(old, "mctl"), os.path.join(old, "pvj"))
    shutil.copytree(old, new, symlinks=True)
    os.rename(os.path.join(new, "pvj"), os.path.join(new, "mctl"))
    for name in os.listdir(os.path.join(new, "bin")):
        os.rename(os.path.join(new, "bin", name), os.path.join(new, "bin", name.replace("pvj-", "nxlx-")))
    os.unlink(os.path.join(root, "opt/pvj/current"))
    os.symlink("/opt/pvj/releases/9.9.2", os.path.join(root, "opt/pvj/current"))
    write(root, "opt/pvj/previous", "/opt/pvj/releases/9.9.1\n")
    return root


def snapshot(root, skip=("var/backups",)):
    """{path: (kind, content hash or link target, mode)} of a whole staged node."""
    out = {}
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        if any(rel == s or rel.startswith(s + "/") for s in skip):
            dirs[:] = []
            continue
        for n in dirs + files:
            p = os.path.join(d, n)
            r = os.path.normpath(os.path.join(rel, n))
            if r in skip:
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[r] = ("link", os.readlink(p), 0)
            elif stat.S_ISSOCK(st.st_mode):
                out[r] = ("socket", "", 0)
            elif stat.S_ISDIR(st.st_mode):
                out[r] = ("dir", "", stat.S_IMODE(st.st_mode))
            else:
                with open(p, "rb") as f:
                    out[r] = ("file", hashlib.sha256(f.read()).hexdigest(), stat.S_IMODE(st.st_mode))
    return out


def convert(root, *args, env=None):
    e = dict(os.environ)
    e.pop("NXLX_CONVERT_FAIL_AT", None)
    e.update(env or {})
    return subprocess.run([CONVERT, "--root", root, *args], capture_output=True, text=True, timeout=120, env=e)


class FakePlayer:
    """An mpv IPC socket at the player's place that answers idle-active (or never answers)."""

    def __init__(self, root, idle):
        self.path = os.path.join(root, "run/pvj/player/player.sock")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.idle, self.stop = idle, False
        self.t = threading.Thread(target=self.serve, daemon=True)
        self.t.start()

    def serve(self):
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                line = c.makefile("rb").readline()
                if self.idle is None:
                    continue                                # a player that hangs: no answer, then closed
                rid = json.loads(line)["request_id"]
                c.sendall(json.dumps({"event": "property-change"}).encode() + b"\n")
                c.sendall(json.dumps({"data": self.idle, "error": "success", "request_id": rid}).encode() + b"\n")

    def close(self):
        self.stop = True
        self.sock.close()
        self.t.join(5)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The old node is installed by the installer of the release before the system rename (rename PR 7): this
        # tree's installer makes the new names, and converts a node that has the old ones.
        cls.src = tempfile.mkdtemp()
        try:
            tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE_SYSTEM_RENAME, "mctl", "bin", "install"],
                                 check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError):
            shutil.rmtree(cls.src, True)
            raise unittest.SkipTest("no %s here to install the old node from" % BEFORE_SYSTEM_RENAME)
        subprocess.run(["tar", "-x", "-C", cls.src], input=tar, check=True)
        with open(os.path.join(cls.src, "mctl", "__init__.py"), "w") as f:
            f.write('__version__ = "9.9.1"\n')
        cls.template = stage_old_node(cls.src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, True)
        shutil.rmtree(cls.template, True)

    def setUp(self):
        self.root = tempfile.mkdtemp()
        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root, symlinks=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def p(self, *rel):
        return os.path.join(self.root, *rel)

    def read(self, *rel):
        with open(self.p(*rel)) as f:
            return f.read()


class Plan(Base):
    def test_a_plan_changes_nothing_and_says_what_it_would_do(self):
        before = snapshot(self.root, skip=())
        r = convert(self.root, "--plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(snapshot(self.root, skip=()), before, "a plan writes nothing, not even a journal")
        out = r.stdout
        for said in ("rename group pvj to nxlx-svc (gid 990 stays)", "rename account pvj-web to nxlx-web (uid 990 stays)",
                     "rename account pvj-player to nxlx-player (uid 989 stays), home /var/lib/nxlx-player",
                     "move /opt/pvj to /opt/nxlx", "move /var/lib/pvj to /var/lib/nxlx", "move /etc/pvj to /etc/nxlx",
                     "move /opt/pvj-ndi to /opt/nxlx-ndi", "rename /etc/nxlx/pvj.env to nxlx.env",
                     "point /opt/nxlx/current at /opt/nxlx/releases/9.9.2",
                     "rename /etc/systemd/system/pvj-web.service to nxlx-web.service",
                     "rename /etc/systemd/system/pvj-web.service.d to nxlx-web.service.d",
                     "point /etc/systemd/system/multi-user.target.wants/pvj-ndi.service at /etc/systemd/system/nxlx-ndi.service",
                     "rename /usr/local/bin/pvj-player to nxlx-player",
                     "rename the login account pvj-dev to nxlx-dev (uid 1001 stays)",
                     "move the home /home/pvj-dev to /home/nxlx-dev",
                     "rename /etc/sudoers.d/010_pvj-dev to 010_nxlx-dev", "rename the accounts in /etc/subuid",
                     "/opt/nxlx/releases/9.9.1.new: left from an install that was cut short",
                     "carried over byte for byte, checked before the services start again: node-id, settings.json",
                     "Nothing was changed. Run with --run to convert."):
            self.assertIn(said, out)
        self.assertNotIn("REFUSED", out)
        # The order of the steps: service accounts, folders, files, then the login account last.
        steps = [int(s) for s in re.findall(r"(?m)^  step (\d)", out)]
        self.assertEqual(steps, sorted(steps))
        self.assertEqual(steps[-1], 8)
        self.assertNotIn("avahi", out, "the announcement file holds only a comment and is not the converter's")

    def test_the_converters_rows_are_the_rename_tables_rows(self):
        conv = load_converter()
        table = {}
        with open(os.path.join(REPO, "tools", "rename-map.txt"), encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 4 and not line.startswith("#"):
                    table[parts[0]] = (parts[1], parts[2])
        mine = conv.rows()
        self.assertGreater(len(mine), 100)
        for row in mine:
            self.assertEqual(table.get(row.old), (row.new, row.kind), "%s differs from tools/rename-map.txt" % row.old)
        kinds = {k for _, k in table.values()}
        for old, (new, kind) in table.items():
            if kind in ("system", "env", "release"):
                self.assertIn(old, {r.old for r in mine}, "a %s row the converter does not carry: %s" % (kind, old))
        self.assertIn("system", kinds)
        self.assertTrue(conv.RELEASE_NAMES_LIVE and conv.ENV_NAMES_LIVE, "rename PR 8 turned both on")


class Run(Base):
    def test_a_run_converts_the_node_and_carries_the_state_over(self):
        before = snapshot(self.root)
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Converted:", r.stdout)
        for gone in ("opt/pvj", "var/lib/pvj", "etc/pvj", "opt/pvj-ndi", "var/lib/pvj-player", "home/pvj-dev",
                     "etc/systemd/system/pvj-web.service", "usr/local/bin/pvj-player", "etc/sudoers.d/010_pvj-dev"):
            self.assertFalse(os.path.lexists(self.p(gone)), gone)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/9.9.1\n")
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-player")), "/opt/nxlx/current/bin/nxlx-player")
        self.assertEqual(os.readlink(self.p("etc/systemd/system/multi-user.target.wants/nxlx-ndi.service")),
                         "/etc/systemd/system/nxlx-ndi.service")
        web = self.read("etc/systemd/system/nxlx-web.service")
        self.assertIn("User=nxlx-web", web)
        self.assertIn("Group=nxlx-svc", web)
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-web", web)
        self.assertIn("Environment=NXLX_RUNTIME_DIR=/run/nxlx/web", web)     # the variable names changed with rename PR 8
        self.assertIn("RuntimeDirectory=nxlx/web", web)
        self.assertIn("User=nxlx-dev", self.read("etc/systemd/system/nxlx-player.service"))
        self.assertIn("SupplementaryGroups=nxlx-ndi", self.read("etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf"))
        self.assertIn("NXLX_MEDIA_DIR=/var/lib/nxlx/video", self.read("etc/nxlx/nxlx.env"))
        inst = json.loads(self.read("etc/nxlx/install.json"))
        self.assertEqual((inst["prefix"], inst["user"]), ("/opt/nxlx", "nxlx-dev"))
        self.assertIn("nxlx-release", self.read("etc/nxlx/allowed_signers"))
        self.assertEqual(self.read("etc/sudoers.d/010_nxlx-dev"), "nxlx-dev ALL=(ALL) NOPASSWD:ALL\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.p("etc/sudoers.d/010_nxlx-dev")).st_mode), 0o440)
        self.assertIn("ExecStart=/opt/nxlx/current/bin/nxlx-ndi", self.read("etc/systemd/system/nxlx-ndi.service"))
        self.assertTrue(os.path.isfile(self.p("opt/nxlx-ndi/lib/libndi.so.6")))
        self.assertTrue(os.path.isfile(self.p("home/nxlx-dev/.ssh/authorized_keys")))
        # Accounts: new names, the same ids, memberships followed.
        passwd = {l.split(":")[0]: l.split(":") for l in self.read("etc/passwd").splitlines()}
        self.assertEqual(passwd["nxlx-dev"][2], "1001")
        self.assertEqual(passwd["nxlx-dev"][5], "/home/nxlx-dev")
        self.assertEqual(passwd["nxlx-player"][5], "/var/lib/nxlx-player")
        self.assertEqual(passwd["nxlx-web"][2:4], ["990", "990"])
        groups = {l.split(":")[0]: l.split(":") for l in self.read("etc/group").splitlines()}
        self.assertEqual(groups["nxlx-svc"], ["nxlx-svc", "x", "990", "nxlx-dev,nxlx-web,nxlx-player"])
        self.assertEqual(groups["sudo"][3], "pi,nxlx-dev")
        self.assertEqual(groups["nxlx-dev"][2], "1001")
        self.assertNotIn("nxlx", groups, "the shared group is nxlx-svc, never nxlx")
        self.assertIn("nxlx-dev:165536:65536", self.read("etc/subuid"))
        self.assertEqual(self.read("etc/shadow").count("nxlx-"), 7)
        # No old name is left in an account file or anywhere in a converted system file.
        for f in ("etc/passwd", "etc/group", "etc/shadow", "etc/gshadow", "etc/subuid", "etc/subgid"):
            self.assertNotRegex(self.read(f), OLDNAME, f)
        after = snapshot(self.root)
        for rel in after:
            if rel.startswith(("etc/systemd", "etc/tmpfiles.d", "etc/udev", "etc/sudoers.d", "usr/local/bin",
                               "etc/modules-load.d")) or rel in ("etc/nxlx/install.json", "etc/nxlx/nxlx.env"):
                self.assertNotRegex(rel, OLDNAME)
                if after[rel][0] == "link":
                    self.assertNotRegex(after[rel][1], OLDNAME, rel)
        # Carried over byte for byte: settings (the paired devices' hashes, so nobody is signed out), node id, key.
        for name in ("settings.json", "node-id", "tls/node.key", "events.json"):
            self.assertEqual(after["var/lib/nxlx/" + name], before["var/lib/pvj/" + name], name)
        self.assertEqual(after["opt/nxlx/releases/9.9.1/pvj/player.py"], before["opt/pvj/releases/9.9.1/pvj/player.py"],
                         "the old release is kept as it is, for the undo")
        self.assertEqual(after["etc/avahi/services/nxlx-mastercontrol.service"],
                         before["etc/avahi/services/nxlx-mastercontrol.service"])
        # The backup and the journal: root only.
        runs = [d for d in os.listdir(self.p("var/backups/nxlx-convert")) if os.path.isdir(self.p("var/backups/nxlx-convert", d))]
        self.assertEqual(len(runs), 1)
        tar = self.p("var/backups/nxlx-convert", runs[0], "backup.tar")
        self.assertEqual(stat.S_IMODE(os.stat(tar).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.p("var/backups/nxlx-convert")).st_mode), 0o700)
        import tarfile
        with tarfile.open(tar) as t:
            names = set(t.getnames())
        for want in ("etc/passwd", "etc/shadow", "var/lib/pvj/settings.json", "var/lib/pvj/node-id",
                     "var/lib/pvj/tls/node.key", "etc/pvj/install.json", "etc/sudoers.d/010_pvj-dev",
                     "etc/systemd/system/pvj-web.service"):
            self.assertIn(want, names)
        self.assertFalse(any(n.startswith("var/lib/pvj/video/") or n.startswith("opt/pvj/releases") for n in names),
                         "neither the videos nor the releases are copied")

    def test_a_second_run_finds_nothing_to_do_and_changes_nothing(self):
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        once = snapshot(self.root)
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Nothing to convert: this node already uses the new names.", r.stdout)
        self.assertEqual(snapshot(self.root), once)
        self.assertIn("Nothing to convert.", convert(self.root, "--plan").stdout)

    def test_undo_puts_the_old_node_back_exactly(self):
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        r = convert(self.root, "--undo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Undone: the node uses the old names again.", r.stdout)
        self.assertEqual(snapshot(self.root), before)
        self.assertIn("Nothing to undo.", convert(self.root, "--undo").stdout)
        r = convert(self.root, "--run")                     # and it can be converted again after an undo
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(self.p("opt/pvj")))

    def test_a_failure_at_any_change_can_be_undone_or_finished(self):
        plan = convert(self.root, "--plan").stdout
        total = len(re.findall(r"(?m)^    +\d+  ", plan))
        self.assertGreater(total, 40)
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        converted = snapshot(self.root)
        for at in sorted({1, 2, total // 3, total // 2, total - 1, total} | set(range(5, total, 7))):
            with self.subTest(fail_at=at):
                shutil.rmtree(self.root)
                shutil.copytree(self.template, self.root, symlinks=True)
                r = convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(at)})
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("FAILED at change %d" % at, r.stdout)
                self.assertIn("%d of %d changes were made" % (at - 1, total), r.stdout)
                half = snapshot(self.root)
                u = convert(self.root, "--undo")
                self.assertEqual(u.returncode, 0, u.stdout)
                self.assertEqual(snapshot(self.root), before, "undo after a failure at %d" % at)
                # The same failure again, then a plain --run carries on from where it stopped.
                convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(at)})
                self.assertEqual(snapshot(self.root), half)
                f = convert(self.root, "--run")
                self.assertEqual(f.returncode, 0, f.stdout)
                self.assertEqual(snapshot(self.root), converted, "finished after a failure at %d" % at)

    def test_confirm_stops_the_timed_undo_but_not_a_manual_one(self):
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        self.assertIn("Confirmed", convert(self.root, "--confirm").stdout)
        converted = snapshot(self.root)
        r = convert(self.root, "--undo", "--auto")
        self.assertIn("Not undone: the conversion was confirmed.", r.stdout)
        self.assertEqual(snapshot(self.root), converted)
        self.assertEqual(convert(self.root, "--undo").returncode, 0)
        self.assertEqual(snapshot(self.root), before)


class ReviewFixes(Base):
    """The findings of the converter's review (2026-11-14), each shown where stage mode can show it."""

    def test_a_terminal_that_goes_away_does_not_stop_the_run(self):
        p = subprocess.Popen([CONVERT, "--root", self.root, "--run"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdout.close()                                     # the ssh connection drops at once
        _, err = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, err)
        self.assertFalse(os.path.exists(self.p("opt/pvj")))
        self.assertTrue(os.path.islink(self.p("opt/nxlx/current")))
        runs = [d for d in os.listdir(self.p("var/backups/nxlx-convert")) if os.path.isdir(self.p("var/backups/nxlx-convert", d))]
        log = self.read("var/backups/nxlx-convert", runs[0], "run.log")
        self.assertIn("Checks", log, "the log has the report from its first line")
        self.assertIn("Converted:", log)
        self.assertEqual(stat.S_IMODE(os.stat(self.p("var/backups/nxlx-convert", runs[0], "run.log")).st_mode), 0o600)

    def test_undo_leaves_a_logged_in_account_with_its_home_and_sudoers(self):
        before = snapshot(self.root, skip=("var/backups", "proc"))
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        converted = snapshot(self.root, skip=("var/backups", "proc"))
        write(self.root, "proc/900/status", "Name:\tbash\nUid:\t1001\t1001\t1001\t1001\n")
        write(self.root, "proc/900/cgroup", "0::/user.slice/user-1001.slice/session-4.scope\n")
        r = convert(self.root, "--undo")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("the account nxlx-dev is in use outside its services (process 900)", r.stdout)
        self.assertEqual(snapshot(self.root, skip=("var/backups", "proc")), converted, "a refused undo changes nothing")
        # The timer cannot wait: it puts back everything but the account, and leaves its home and sudoers with it.
        r = convert(self.root, "--undo", "--auto")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nxlx-dev is logged in (process 900)", r.stdout)
        self.assertIn("left with nxlx-dev, which keeps its new name", r.stdout)
        passwd = {l.split(":")[0]: l.split(":") for l in self.read("etc/passwd").splitlines()}
        self.assertEqual(passwd["nxlx-dev"][5], "/home/nxlx-dev")
        self.assertIn("pvj-web", passwd)
        self.assertTrue(os.path.isfile(self.p("home/nxlx-dev/.ssh/authorized_keys")), "its home stays with it")
        self.assertEqual(self.read("etc/sudoers.d/010_nxlx-dev"), "nxlx-dev ALL=(ALL) NOPASSWD:ALL\n", "so does sudo")
        self.assertTrue(os.path.isdir(self.p("opt/pvj")), "the rest is back")
        # Once the login is gone, a second undo finishes the job.
        shutil.rmtree(self.p("proc/900"))
        r = convert(self.root, "--undo")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(snapshot(self.root, skip=("var/backups", "proc")), before)

    def test_units_to_start_counts_every_run_that_is_not_undone(self):
        conv = load_converter()
        recs = [{"run": "a", "n": 0, "op": "stop", "state": "done", "active": ["pvj-web.service", "pvj-player.service"]},
                {"run": "a", "state": "failed"},
                {"run": "b", "n": 0, "op": "stop", "state": "done", "active": []},          # the run that carried on
                {"run": "c", "n": 0, "op": "stop", "state": "done", "active": ["pvj-sound.service"]},
                {"run": "c", "n": 0, "op": "stop", "state": "undone"}]
        self.assertEqual(conv.units_to_start(recs), ["pvj-web.service", "pvj-player.service"])

    def test_a_release_without_the_new_names_is_refused(self):
        os.unlink(self.p("opt/pvj/current"))
        os.symlink("/opt/pvj/releases/9.9.1", self.p("opt/pvj/current"))
        before = snapshot(self.root, skip=())
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("the current release (/opt/pvj/releases/9.9.1) does not have the new names: bin/nxlx-", r.stdout)
        self.assertIn("Install a release with the new names first", r.stdout)
        self.assertEqual(snapshot(self.root, skip=()), before)
        os.unlink(self.p("opt/pvj/current"))
        os.symlink("/opt/pvj/releases/9.9.2", self.p("opt/pvj/current"))
        self.assertIn("the current release (/opt/pvj/releases/9.9.2) has the programs the converted units run",
                      convert(self.root, "--plan").stdout)

    def test_release_points_current_at_the_new_release_and_the_undo_points_it_back(self):
        """install.sh (rename PR 7) puts the new release beside the old one and leaves "current" on the old one; the
        converter points it at the new one as one of its changes, so an undo brings back the release that ran before,
        whose programs have the old names."""
        os.unlink(self.p("opt/pvj/current"))
        os.symlink("/opt/pvj/releases/9.9.1", self.p("opt/pvj/current"))
        before = snapshot(self.root)
        plan = convert(self.root, "--plan", "--release", "9.9.2")
        self.assertEqual(plan.returncode, 0, plan.stdout)
        self.assertIn("point /opt/nxlx/current at /opt/nxlx/releases/9.9.2", plan.stdout)
        self.assertIn("the current release (/opt/nxlx/releases/9.9.2) has the programs the converted units run", plan.stdout)
        r = convert(self.root, "--run", "--release", "9.9.2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")
        self.assertTrue(os.path.isdir(self.p("opt/nxlx/releases/9.9.1/pvj")), "the old release stays, for the undo")
        u = convert(self.root, "--undo")
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.1")
        self.assertEqual(snapshot(self.root), before)

    def test_a_release_that_is_not_there_or_not_a_plain_name_is_refused(self):
        before = snapshot(self.root, skip=())
        for name in ("9.9.7", "../../etc", "9.9.2/../9.9.1", "", "/opt/pvj/releases/9.9.2"):
            r = convert(self.root, "--run", "--release", name)
            self.assertEqual(r.returncode, 2, name + r.stdout + r.stderr)
            self.assertIn("is not a folder in /opt/pvj/releases", r.stdout, name)
        self.assertEqual(snapshot(self.root, skip=()), before)
        self.assertNotEqual(convert(self.root, "--undo", "--release", "9.9.2").returncode, 0)

    def test_a_pipe_or_a_link_planted_in_the_panels_folder_is_not_read(self):
        """install.sh runs the converter as root; /var/lib/pvj belongs to the panel's account, which could plant a
        fifo (a plan that reads it would hang the install) or a link to a file only root may read."""
        os.mkfifo(self.p("var/lib/pvj/planted.json"))
        write(self.root, "secret", "pvj pvj pvj\n", 0o600)
        os.symlink(self.p("secret"), self.p("var/lib/pvj/linked.json"))
        r = convert(self.root, "--plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("planted.json", r.stdout)
        self.assertNotIn("linked.json", r.stdout)

    def test_variable_and_signing_names_are_rewritten_and_the_keys_are_kept(self):
        conv = load_converter()
        self.assertTrue(conv.ENV_NAMES_LIVE and conv.RELEASE_NAMES_LIVE)
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPVJ_notAKeyJustTheLetters"
        write(self.root, "etc/pvj/allowed_signers", '# who may sign updates\npvj-release namespaces="pvj-release" %s owner\n' % key)
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("held until", r.stdout)
        self.assertEqual(self.read("etc/nxlx/allowed_signers"),
                         '# who may sign updates\nnxlx-release namespaces="nxlx-release" %s owner\n' % key,
                         "principal and namespace change, the key line stays as it was")
        self.assertIn("Environment=NXLX_NDI_LIB=/opt/nxlx-ndi/lib", self.read("etc/systemd/system/nxlx-ndi.service"))
        self.assertIn("Environment=NXLX_NDI_DIR=/run/nxlx-ndi", self.read("etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf"))
        self.assertIn("Environment=NXLX_NDI_PIPE_DIR=/run/nxlx-ndi-pipe",
                      self.read("etc/systemd/system/nxlx-player.service.d/50-nxlx-ndi.conf"))
        env = self.read("etc/nxlx/nxlx.env")
        self.assertRegex(env, r"(?m)^NXLX_MEDIA_DIR=/var/lib/nxlx/video$")
        for name in ("etc/nxlx/nxlx.env", "etc/systemd/system/nxlx-web.service", "etc/systemd/system/nxlx-ndi.service"):
            self.assertNotIn("PVJ_", self.read(name), name)

    def test_the_undo_puts_the_variable_and_signing_names_back_byte_for_byte(self):
        write(self.root, "etc/pvj/allowed_signers", 'pvj-release namespaces="pvj-release" ssh-ed25519 AAAAC3Nza owner\n')
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        self.assertIn("NXLX_MEDIA_DIR", self.read("etc/nxlx/nxlx.env"))
        r = convert(self.root, "--undo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(snapshot(self.root), before)
        self.assertIn("PVJ_MEDIA_DIR", self.read("etc/pvj/pvj.env"))
        self.assertIn('pvj-release namespaces="pvj-release"', self.read("etc/pvj/allowed_signers"))
        r = convert(self.root, "--run")                      # and again after an undo
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('nxlx-release namespaces="nxlx-release"', self.read("etc/nxlx/allowed_signers"))

    def test_a_second_run_leaves_the_rewritten_names_alone(self):
        write(self.root, "etc/pvj/allowed_signers", 'pvj-release namespaces="pvj-release" ssh-ed25519 AAAAC3Nza owner\n')
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        once = snapshot(self.root)
        r = convert(self.root, "--run")
        self.assertIn("Nothing to convert", r.stdout)
        self.assertEqual(snapshot(self.root), once)

    def pr7(self):
        """The converter of rename PR 7 (variable and signing names held), kept in a temp file, or None."""
        r = subprocess.run(["git", "-C", REPO, "show", "168eb53:bin/nxlx-convert"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("no 168eb53 here to convert the node as rename PR 7 did")
        path = os.path.join(tempfile.mkdtemp(), "nxlx-convert-pr7")
        with open(path, "wb") as f:
            f.write(r.stdout)
        os.chmod(path, 0o755)
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        return path

    def converted_by_pr7(self):
        """A node that took rename PR 7's conversion AND confirmed it (its journal is kept): new folders, accounts
        and units, the old variable names in nxlx.env and the units, pvj-release in allowed_signers."""
        pr7 = self.pr7()
        write(self.root, "etc/pvj/allowed_signers", 'pvj-release namespaces="pvj-release" ssh-ed25519 AAAAC3Nza owner\n')
        for args in (("--run",), ("--confirm",)):
            r = subprocess.run([pr7, "--root", self.root, *args], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PVJ_MEDIA_DIR", self.read("etc/nxlx/nxlx.env"))
        self.assertIn("pvj-release", self.read("etc/nxlx/allowed_signers"))
        return snapshot(self.root)

    def test_a_node_converted_and_confirmed_before_rename_pr_8_gets_a_second_pass_and_its_undo_covers_only_that(self):
        """The first conversion is confirmed and its journal kept. A second pass followed by --undo (by hand or the
        timer) puts back only the second pass: every change before the confirm is the node's state now."""
        pr7 = self.converted_by_pr7()
        journal_before = self.read("var/backups/nxlx-convert/journal.jsonl")
        plan = convert(self.root, "--plan").stdout
        for rel in ("etc/nxlx/nxlx.env", "etc/nxlx/allowed_signers", "etc/systemd/system/nxlx-web.service",
                    "etc/systemd/system/nxlx-ndi.service"):
            self.assertIn("rewrite /" + rel, plan)
        self.assertNotIn("rename account", plan)
        self.assertNotIn("move /", plan)
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("PVJ_", self.read("etc/nxlx/nxlx.env"))
        self.assertIn('nxlx-release namespaces="nxlx-release" ssh-ed25519 AAAAC3Nza owner', self.read("etc/nxlx/allowed_signers"))
        self.assertIn("NXLX_RUNTIME_DIR", self.read("etc/systemd/system/nxlx-web.service"))
        self.assertTrue(self.read("var/backups/nxlx-convert/journal.jsonl").startswith(journal_before), "the journal is kept whole")
        u = convert(self.root, "--undo")
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        undone = [l for l in u.stdout.splitlines() if l.startswith("  undone  ")]
        self.assertEqual(len(undone), 12, u.stdout)
        self.assertTrue(all(l.startswith("  undone  rewrite /etc/") for l in undone), "only the rewrites of the second pass: " + u.stdout)
        self.assertTrue(os.path.isdir(self.p("opt/nxlx")))
        self.assertFalse(os.path.lexists(self.p("opt/pvj")))
        self.assertIn("nxlx-dev:", self.read("etc/passwd"))
        self.assertNotIn("pvj-dev:", self.read("etc/passwd"))
        self.assertEqual(snapshot(self.root), pr7, "back to the node as rename PR 7 left it")

    def test_the_timed_undo_of_a_second_pass_covers_only_the_second_pass_and_a_confirm_keeps_it(self):
        pr7 = self.converted_by_pr7()
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        u = convert(self.root, "--undo", "--auto")
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertEqual(snapshot(self.root), pr7)
        self.assertEqual(convert(self.root, "--run").returncode, 0)          # again after the undo
        self.assertEqual(convert(self.root, "--confirm").returncode, 0)
        after = snapshot(self.root)
        self.assertIn("Not undone", convert(self.root, "--undo", "--auto").stdout)
        self.assertEqual(snapshot(self.root), after)
        self.assertIn("Nothing to convert", convert(self.root, "--run").stdout)

    def test_the_services_a_run_stops_and_starts_are_the_ones_since_the_last_confirm_under_either_name(self):
        conv = load_converter()
        recs = [{"run": "r1", "state": "start"},
                {"run": "r1", "n": 0, "op": "stop", "state": "done", "active": ["pvj-web.service", "pvj-player.service"]},
                {"run": "r1", "state": "finished"}, {"state": "confirmed"},
                {"run": "r2", "state": "start"},
                {"run": "r2", "n": 0, "op": "stop", "state": "done", "active": ["nxlx-player.service", "nxlx-web.service"]}]
        self.assertEqual(conv.units_to_start(recs), ["nxlx-web.service", "nxlx-player.service"],
                         "the first run's units are not started again by the second run's undo or start")
        self.assertEqual(conv.units_to_start(recs[:3]), ["pvj-web.service", "pvj-player.service"])
        fake = type("R", (), {"stdout": "pvj-web.service loaded active running x\n"
                                        "nxlx-web.service loaded active running x\n"
                                        "nxlx-convert-undo.timer loaded active waiting x\n"
                                        "nxlx-raster@7.service loaded inactive dead x\n"
                                        "other.service loaded active running x\n"})()
        old = conv.sh
        conv.sh = lambda *a, **k: fake
        try:
            self.assertEqual(conv.loaded_units(), [("pvj-web.service", "active"), ("nxlx-web.service", "active"),
                                                   ("nxlx-raster@7.service", "inactive")])
        finally:
            conv.sh = old
        # the plan of a node with the new names stops its nxlx-* units too (a real node only)
        self.assertEqual([u for u in conv.stop_order() if u.startswith("nxlx-")][0], "nxlx-web.service")

    def test_the_undo_timer_is_a_unit_that_survives_a_restart_and_is_armed_again(self):
        total = len(re.findall(r"(?m)^    +\d+  ", convert(self.root, "--plan").stdout))
        r = convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(total // 2)})
        self.assertEqual(r.returncode, 1, r.stdout)
        timer = self.read("etc/systemd/system/nxlx-convert-undo.timer")
        self.assertRegex(timer, r"(?m)^OnBootSec=5min$")
        self.assertRegex(timer, r"(?m)^OnActiveSec=15min$")
        self.assertRegex(timer, r"(?m)^WantedBy=timers.target$")
        self.assertEqual(os.readlink(self.p("etc/systemd/system/timers.target.wants/nxlx-convert-undo.timer")),
                         "/etc/systemd/system/nxlx-convert-undo.timer")
        service = self.read("etc/systemd/system/nxlx-convert-undo.service")
        self.assertIn("ExecStart=/usr/bin/python3 /var/backups/nxlx-convert/nxlx-convert --undo --auto", service)
        copy = self.p("var/backups/nxlx-convert/nxlx-convert")
        self.assertTrue(os.access(copy, os.X_OK))
        with open(copy, "rb") as a, open(CONVERT, "rb") as b:
            self.assertEqual(a.read(), b.read(), "the copy the timer runs, outside the folders that move")
        # The run that carries on arms the same timer again instead of failing on the one that is there.
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("the timer is written", r.stdout)
        self.assertEqual(self.read("etc/systemd/system/nxlx-convert-undo.timer"), timer)
        self.assertEqual(convert(self.root, "--confirm").returncode, 0)
        for gone in ("nxlx-convert-undo.timer", "nxlx-convert-undo.service", "timers.target.wants"):
            self.assertFalse(os.path.lexists(self.p("etc/systemd/system", gone)), gone)

    def test_a_confirm_of_an_earlier_conversion_does_not_stop_the_timed_undo(self):
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        self.assertEqual(convert(self.root, "--confirm").returncode, 0)
        self.assertEqual(convert(self.root, "--undo").returncode, 0)
        self.assertEqual(convert(self.root, "--run").returncode, 0)          # converted again, not confirmed
        r = convert(self.root, "--undo", "--auto")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Undone", r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_a_process_left_in_a_helper_unit_stops_the_run_before_any_rename(self):
        # A picture job (pvj-raster@N.service, Accept=yes) that is still there when the accounts are renamed.
        write(self.root, "proc/777/status", "Name:\tpvj-raster\nUid:\t986\t986\t986\t986\n")
        write(self.root, "proc/777/cgroup", "0::/system.slice/system-pvj\\x2draster.slice/pvj-raster@3.service\n")
        self.assertEqual(convert(self.root, "--plan").returncode, 0, "a service's process stops with its unit")
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAILED at change 1: pvj-raster still running after the services stopped (process 777)", r.stdout)
        self.assertTrue(os.path.isdir(self.p("opt/pvj")))
        self.assertEqual(self.read("etc/passwd"), PASSWD, "no account was renamed")


class SecondReview(Base):
    """The findings of the converter's second review (2026-11-14)."""

    def test_confirm_needs_a_finished_run_and_one_converter_works_at_a_time(self):
        total = len(re.findall(r"(?m)^    +\d+  ", convert(self.root, "--plan").stdout))
        self.assertEqual(convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(total // 2)}).returncode, 1)
        r = convert(self.root, "--confirm")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("the latest run did not finish (an injected failure", r.stdout)
        self.assertTrue(os.path.isfile(self.p("etc/systemd/system/nxlx-convert-undo.timer")), "the undo stays armed")
        half = snapshot(self.root)
        with open(self.p("var/backups/nxlx-convert/lock")) as held:          # an undo (or a run) is working
            fcntl.flock(held, fcntl.LOCK_EX)
            for args in (("--run",), ("--undo",), ("--undo", "--auto"), ("--confirm",)):
                r = convert(self.root, *args)
                self.assertEqual(r.returncode, 2, "%s: %s" % (args, r.stdout))
                self.assertIn("another nxlx-convert (a run, an undo or a confirm) is working on this node now", r.stdout)
            self.assertEqual(snapshot(self.root), half, "nothing was changed while the lock was held")
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        r = convert(self.root, "--confirm")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-convert-undo.timer")))

    def test_an_undone_conversion_cannot_be_confirmed(self):
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        self.assertEqual(convert(self.root, "--undo").returncode, 0)
        r = convert(self.root, "--confirm")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("the latest conversion was undone", r.stdout)

    def test_a_helper_the_node_had_must_be_in_the_new_release(self):
        # The picture helper was built on this node (the old release has it); its unit runs it from render/.
        write(self.root, "opt/pvj/releases/9.9.1/render/pvj-raster", "a program\n", 0o755)
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("does not have the new names: render/nxlx-raster is missing", r.stdout)
        write(self.root, "opt/pvj/releases/9.9.2/render/nxlx-raster", "a program\n", 0o755)
        self.assertEqual(convert(self.root, "--plan").returncode, 0)

    def test_an_optional_helper_the_node_never_had_is_not_asked_for(self):
        self.assertFalse(os.path.exists(self.p("opt/pvj/releases/9.9.1/render/pvj-raster")))
        out = convert(self.root, "--plan").stdout
        self.assertIn("has the programs the converted units run", out)


def changes(out):
    """{number: text} of the changes a plan lists."""
    return {int(n): t for n, t in re.findall(r"(?m)^    +(\d+)  (.*)$", out)}


class ThirdReview(Base):
    """The findings of the second review of rename PR 7 (2026-11-16)."""

    def journal(self):
        return self.p("var/backups/nxlx-convert/journal.jsonl")

    def records(self):
        with open(self.journal()) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_what_is_carried_over_is_checked_before_the_start_and_never_again(self):
        """The panel writes settings.json at every start (a new PIN): the check is made before step 9, and its result
        is in the journal, so a run that carries on after a failed start does not compare again."""
        total = len(changes(convert(self.root, "--plan").stdout))
        r = convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(total + 1)})       # where step 9 is
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual([x["state"] for x in self.records() if x.get("state") == "carried-same"], ["carried-same"])
        with open(self.p("var/lib/nxlx/settings.json"), "a") as f:           # the panel started and wrote a PIN
            f.write(" ")
        again = convert(self.root, "--run")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("starting and checking again", again.stdout)
        self.assertEqual(convert(self.root, "--confirm").returncode, 0)

    def test_a_change_to_what_is_carried_over_before_the_start_still_fails_the_run(self):
        total = len(changes(convert(self.root, "--plan").stdout))
        self.assertEqual(convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(total // 2)}).returncode, 1)
        with open(self.p("var/lib/nxlx/settings.json"), "a") as f:
            f.write(" ")
        r = convert(self.root, "--run")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAILED: settings.json changed during the conversion (before any service was started again)",
                      r.stdout)
        self.assertNotIn("carried-same", [x.get("state") for x in self.records()])
        self.assertEqual(convert(self.root, "--state").stdout.strip(), "pending")

    def test_a_torn_last_line_of_the_journal_is_passed_over_and_one_in_the_middle_refuses(self):
        before = snapshot(self.root)
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        with open(self.journal(), "a") as f:
            f.write('{"run": "20261116T000000Z-1", "n": 3, "op": "wri')              # a power cut while writing
        s = convert(self.root, "--state")
        self.assertEqual((s.returncode, s.stdout), (0, "unconfirmed\n"), s.stderr)
        self.assertIn("the last line of the journal (line", s.stderr)
        u = convert(self.root, "--undo", "--auto")                  # the timed undo, which must still work
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertIn("Undone", u.stdout)
        self.assertEqual(snapshot(self.root), before)
        self.assertTrue(any(n.startswith("journal.jsonl.torn-") for n in os.listdir(self.p("var/backups/nxlx-convert"))),
                        "the torn line is kept in a copy")
        self.records()                                              # every line reads again
        self.assertEqual(convert(self.root, "--state").stderr, "")
        # A line that cannot be read in the middle: refused, and nothing is armed or changed.
        with open(self.journal()) as f:
            lines = f.readlines()
        lines.insert(len(lines) // 2, "not a record\n")
        with open(self.journal(), "w") as f:
            f.writelines(lines)
        half = snapshot(self.root, skip=())
        for args in (("--state",), ("--run",), ("--undo", "--auto"), ("--confirm",)):
            r = convert(self.root, *args)
            self.assertEqual(r.returncode, 2, "%s: %s %s" % (args, r.stdout, r.stderr))
            self.assertIn("cannot be read at line", r.stdout + r.stderr)
            self.assertIn(self.journal(), r.stdout + r.stderr)             # says where the journal is
            self.assertIn("remove line", r.stdout + r.stderr)               # and what to do
            self.assertIn("sudo nxlx-convert --undo", r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(snapshot(self.root, skip=()), half)

    def test_a_change_cut_off_before_its_end_was_written_is_undone(self):
        """A power cut between a change and its "done" record: the timed undo puts that change back too, and does
        not say "Nothing to undo" (which took the timer away and left the node half converted)."""
        listed = changes(convert(self.root, "--plan").stdout)
        before = snapshot(self.root)
        first_effect = min(n for n, t in listed.items() if not t.startswith("check that no process"))
        folder = next(n for n, t in listed.items() if t == "move /opt/pvj to /opt/nxlx")
        for at in sorted({first_effect, folder, folder + 1, len(listed) // 2, len(listed)}):
            with self.subTest(cut_at=at, change=listed[at]):
                shutil.rmtree(self.root)
                shutil.copytree(self.template, self.root, symlinks=True)
                r = convert(self.root, "--run", env={"NXLX_CONVERT_CUT_AT": str(at)})
                self.assertEqual(r.returncode, 137, r.stdout + r.stderr)
                self.assertEqual(self.records()[-1]["state"], "begin")
                u = convert(self.root, "--undo", "--auto")
                self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
                self.assertIn("change %d of run" % at, u.stdout)
                self.assertIn("Undone: the node uses the old names again.", u.stdout)
                self.assertEqual(snapshot(self.root), before, "undo after a cut at %d" % at)
                self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-convert-undo.timer")))
                self.assertIn("Nothing to undo.", convert(self.root, "--undo").stdout)

    def test_a_timer_left_without_its_journal_is_taken_away(self):
        for end in (("--confirm",), ("--undo", "--auto"), ("--undo",)):
            with self.subTest(end=end):
                shutil.rmtree(self.root)
                shutil.copytree(self.template, self.root, symlinks=True)
                self.assertEqual(convert(self.root, "--run").returncode, 0)
                os.unlink(self.journal())                                  # the journal is lost
                r = convert(self.root, *end)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("The undo timer that was left is taken away.", r.stdout)
                for gone in ("nxlx-convert-undo.timer", "nxlx-convert-undo.service", "timers.target.wants"):
                    self.assertFalse(os.path.lexists(self.p("etc/systemd/system", gone)), gone)

    def test_a_previous_that_is_a_link_is_refused_by_the_plan_not_half_way(self):
        os.unlink(self.p("opt/pvj/current"))
        os.symlink("/opt/pvj/releases/9.9.1", self.p("opt/pvj/current"))
        os.unlink(self.p("opt/pvj/previous"))
        os.symlink("/etc/shadow", self.p("opt/pvj/previous"))
        before = snapshot(self.root, skip=())
        r = convert(self.root, "--run", "--release", "9.9.2")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("/opt/pvj/previous is not a plain file", r.stdout)
        self.assertEqual(snapshot(self.root, skip=()), before)

    def test_a_release_that_is_already_current_still_has_previous_rewritten(self):
        """--run --release X with X already current: "previous" keeps its release, under the new path."""
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.2")
        before = snapshot(self.root)
        plan = convert(self.root, "--plan", "--release", "9.9.2").stdout
        self.assertIn("rewrite /opt/nxlx/previous (1 names)", plan)
        r = convert(self.root, "--run", "--release", "9.9.2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/9.9.1\n")
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/9.9.2")
        self.assertEqual(convert(self.root, "--undo").returncode, 0)
        self.assertEqual(snapshot(self.root), before)


class Refusals(Base):
    def assertRefused(self, r, said):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(said, r.stdout)

    def test_a_show_that_is_playing_stops_it_unless_forced(self):
        player = FakePlayer(self.root, idle=False)
        self.addCleanup(player.close)
        before = snapshot(self.root)
        self.assertRefused(convert(self.root, "--run"), "a show is playing (the player has something loaded)")
        self.assertRefused(convert(self.root, "--plan"), "Stop the show first, or give --force")
        self.assertEqual(snapshot(self.root), before, "a refusal changes nothing")
        r = convert(self.root, "--run", "--force")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("FORCED", r.stdout)

    def test_an_idle_player_does_not_stop_it_and_a_silent_one_does(self):
        player = FakePlayer(self.root, idle=True)
        r = convert(self.root, "--plan")
        player.close()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("nothing is playing (the player is idle)", r.stdout)
        os.unlink(player.path)
        player = FakePlayer(self.root, idle=None)
        self.addCleanup(player.close)
        self.assertRefused(convert(self.root, "--run"), "a show is playing (the player closed the connection without an answer)")

    def test_a_player_that_dribbles_lines_without_an_answer_is_given_up_on_in_time(self):
        path = self.p("run/pvj/player/player.sock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(2)
        stop = threading.Event()

        def serve():
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                while not stop.is_set():
                    try:
                        c.sendall(b'{"event": "property-change"}\n')
                    except OSError:
                        return
                    stop.wait(0.5)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.addCleanup(srv.close)
        self.addCleanup(stop.set)
        import time
        began = time.monotonic()
        self.assertRefused(convert(self.root, "--run"), "a show is playing (the player did not answer")      # in time, or a read timed out at the limit
        self.assertLess(time.monotonic() - began, 30)

    def test_a_pipe_in_place_of_the_settings_or_of_the_old_update_lock_hangs_nothing(self):
        """settings.json is in the panel's folder and /run/lock is writable by every account: as root the converter
        opens neither through a pipe (it would wait for ever) or a link."""
        os.unlink(self.p("var/lib/pvj/settings.json"))
        os.mkfifo(self.p("var/lib/pvj/settings.json"))
        os.makedirs(self.p("run/lock"), exist_ok=True)
        os.mkfifo(self.p("run/lock/pvj-update.lock"))
        r = convert(self.root, "--plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no update is running", r.stdout)
        conv = load_converter()
        self.assertIsNone(conv.sha(self.p("var/lib/pvj/settings.json")))
        os.symlink("/dev/zero", self.p("var/lib/pvj/zero"))
        self.assertIsNone(conv.sha(self.p("var/lib/pvj/zero")))
        write(self.root, "big", "x" * 100)
        self.assertEqual(conv.sha(self.p("big"), limit=10), "too-big:100")

    def test_a_run_that_stops_after_its_last_change_is_finished_by_the_next_and_can_be_confirmed(self):
        """On a real node the last part of a run starts the services (step 9); when it fails, every change is made and
        the next --run must finish the run, or --confirm could never succeed. One past the last change stands in."""
        total = len(re.findall(r"(?m)^    +\d+  ", convert(self.root, "--plan").stdout))
        r = convert(self.root, "--run", env={"NXLX_CONVERT_FAIL_AT": str(total + 1)})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(convert(self.root, "--state").stdout.strip(), "pending")
        self.assertNotEqual(convert(self.root, "--confirm").returncode, 0, "a run that did not finish is not confirmed")
        again = convert(self.root, "--run")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("the last run did not finish: starting and checking again", again.stdout)
        self.assertEqual(convert(self.root, "--state").stdout.strip(), "unconfirmed")
        self.assertEqual(convert(self.root, "--confirm").returncode, 0)
        self.assertEqual(convert(self.root, "--state").stdout.strip(), "confirmed")
        self.assertIn("Nothing to convert", convert(self.root, "--run").stdout)

    def test_a_read_only_root_an_update_or_a_mounted_stick_stop_it(self):
        write(self.root, "proc/mounts", "overlayroot / overlay ro 0 0\n")
        self.assertRefused(convert(self.root, "--run"), "the root file system is read-only")
        write(self.root, "proc/mounts", "/dev/root / ext4 rw 0 0\n/dev/sda1 /media/pvj/NXLX-USB vfat ro 0 0\n")
        self.assertRefused(convert(self.root, "--run"), "a USB drive is mounted at /media/pvj/NXLX-USB")
        write(self.root, "proc/mounts", "/dev/root / ext4 rw 0 0\n")
        write(self.root, "run/pvj-update/update.lock", "")
        with open(self.p("run/pvj-update/update.lock")) as held:      # this process is the update that runs
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertRefused(convert(self.root, "--run"), "an update is running (/run/pvj-update/update.lock is held)")
        self.assertEqual(convert(self.root, "--plan").returncode, 0)
        self.assertFalse(os.path.exists(self.p("var/backups/nxlx-convert")), "nothing was written by the refusals")

    def test_the_login_account_in_use_stops_it_but_its_own_player_does_not(self):
        # node-02: the player runs as pvj-dev; its process is in the player's unit and stops with it.
        write(self.root, "proc/412/status", "Name:\tpvj-player\nUid:\t1001\t1001\t1001\t1001\n")
        write(self.root, "proc/412/cgroup", "0::/system.slice/pvj-player.service\n")
        self.assertEqual(convert(self.root, "--plan").returncode, 0)
        # An ssh login as pvj-dev: Linux would refuse to rename the account.
        write(self.root, "proc/900/status", "Name:\tbash\nUid:\t1001\t1001\t1001\t1001\n")
        write(self.root, "proc/900/cgroup", "0::/user.slice/user-1001.slice/session-4.scope\n")
        self.assertRefused(convert(self.root, "--run"), "the account pvj-dev is in use outside its services (process 900)")
        self.assertIn("another admin login", convert(self.root, "--plan").stdout)

    def test_a_name_that_exists_twice_is_never_overwritten(self):
        os.makedirs(self.p("opt/nxlx"))
        self.assertRefused(convert(self.root, "--run"), "both /opt/pvj and /opt/nxlx exist")
        os.rmdir(self.p("opt/nxlx"))
        with open(self.p("etc/group"), "a") as f:
            f.write("nxlx-svc:x:995:\n")
        self.assertRefused(convert(self.root, "--run"), "both groups pvj and nxlx-svc exist")

    def test_a_login_group_called_nxlx_is_left_alone(self):
        with open(self.p("etc/group"), "a") as f:
            f.write("nxlx:x:1002:\n")              # node-01's login user and group
        self.assertEqual(convert(self.root, "--run").returncode, 0)
        groups = {l.split(":")[0]: l for l in self.read("etc/group").splitlines()}
        self.assertEqual(groups["nxlx"], "nxlx:x:1002:")
        self.assertIn("nxlx-svc", groups)

    def test_a_root_that_is_not_a_folder_is_refused(self):
        r = subprocess.run([CONVERT, "--root", "relative/path", "--plan"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--root must be", r.stderr)


if __name__ == "__main__":
    unittest.main()
