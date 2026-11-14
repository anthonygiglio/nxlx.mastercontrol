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
        cls.src = tempfile.mkdtemp()
        for d in ("pvj", "bin", "install"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(cls.src, d), ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(cls.src, "pvj", "__init__.py"), "w") as f:
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
                     "carried over byte for byte and checked after: node-id, settings.json",
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
        self.assertFalse(conv.RELEASE_NAMES_LIVE, "release names change with rename PR 8, not before")


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
        self.assertIn("Environment=NXLX_RUNTIME_DIR=/run/nxlx/web", web)
        self.assertIn("RuntimeDirectory=nxlx/web", web)
        self.assertIn("User=nxlx-dev", self.read("etc/systemd/system/nxlx-player.service"))
        self.assertIn("SupplementaryGroups=nxlx-ndi", self.read("etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf"))
        self.assertIn("NXLX_MEDIA_DIR=/var/lib/nxlx/video", self.read("etc/nxlx/nxlx.env"))
        inst = json.loads(self.read("etc/nxlx/install.json"))
        self.assertEqual((inst["prefix"], inst["user"]), ("/opt/nxlx", "nxlx-dev"))
        self.assertIn("pvj-release", self.read("etc/nxlx/allowed_signers"), "held until rename PR 8")
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
