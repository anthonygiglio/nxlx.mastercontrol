# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""install/install.sh over a node with the old names (rename PR 7, D136), in --stage mode: the node is installed by
the installer of the release before the rename, then this installer converts it with bin/nxlx-convert first and
carries on under the new names. The real run, with accounts, sudoers and systemd, is tests/real_install_test.sh."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BEFORE = "0ff0ddbeb662efb405186a0e774839cb1f61b2de"     # master with release 0.1.1, before rename PR 7


def version():
    with open(os.path.join(REPO, "mctl", "__init__.py")) as f:
        return next(line.split('"')[1] for line in f if line.startswith("__version__"))


def snapshot(root, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        if any(rel == s or rel.startswith(s + "/") for s in skip):
            dirs[:] = []
            continue
        for n in dirs + files:
            p = os.path.join(d, n)
            r = os.path.normpath(os.path.join(rel, n))
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[r] = ("link", os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                out[r] = ("dir", stat.S_IMODE(st.st_mode))
            else:
                with open(p, "rb") as f:
                    out[r] = ("file", hashlib.sha256(f.read()).hexdigest(), stat.S_IMODE(st.st_mode))
    return out


class InstallConvertsAnOldNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = tempfile.mkdtemp()
        try:
            tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE, "mctl", "bin", "install"],
                                 check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError):
            shutil.rmtree(cls.old, True)
            raise unittest.SkipTest("no %s here to install the old node from" % BEFORE)
        subprocess.run(["tar", "-x", "-C", cls.old], input=tar, check=True)
        with open(os.path.join(cls.old, "mctl", "__init__.py")) as f:
            cls.old_version = next(line.split('"')[1] for line in f if line.startswith("__version__"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.old, True)

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        r = subprocess.run([os.path.join(self.old, "install", "install.sh"), "--stage", self.root, "--user", "pvj-player"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isdir(self.p("opt/pvj/releases", self.old_version)), "the old node")

    def p(self, *rel):
        return os.path.join(self.root, *rel)

    def read(self, *rel):
        with open(self.p(*rel)) as f:
            return f.read()

    def install(self, *args):
        return subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", self.root, *args],
                              capture_output=True, text=True, timeout=120)

    def test_the_installer_converts_the_node_then_installs_under_the_new_names(self):
        old_release = "/opt/pvj/releases/" + self.old_version
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("putting version %s into /opt/pvj/releases, then converting the node" % version(), r.stdout)
        self.assertIn("Converted:", r.stdout)
        self.assertIn("CONVERTED from the old names", r.stdout)
        for gone in ("opt/pvj", "etc/pvj", "var/lib/pvj", "etc/systemd/system/pvj-web.service", "etc/tmpfiles.d/pvj.conf",
                     "etc/udev/rules.d/99-pvj-usb.rules", "usr/local/bin/pvj-player"):
            self.assertFalse(os.path.lexists(self.p(gone)), gone)
        self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/" + version())
        self.assertTrue(os.path.isdir(self.p("opt/nxlx/releases", self.old_version, "bin")), "the old release stays")
        self.assertFalse(os.path.exists(self.p("opt/nxlx/releases", version() + ".new")))
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/%s\n" % self.old_version)
        for name in os.listdir(self.p("etc/systemd/system")):
            self.assertNotIn("pvj", name)
        web = self.read("etc/systemd/system/nxlx-web.service")
        for line in ("User=nxlx-web", "Group=nxlx-svc", "ExecStart=/opt/nxlx/current/bin/nxlx-web",
                     "EnvironmentFile=-/etc/nxlx/nxlx.env", "Environment=NXLX_RUNTIME_DIR=/run/nxlx/web"):
            self.assertIn(line, web)
        self.assertIn("User=nxlx-player", self.read("etc/systemd/system/nxlx-player.service"), "the old account's new name")
        inst = json.loads(self.read("etc/nxlx/install.json"))
        self.assertEqual((inst["prefix"], inst["user"], inst["version"]), ("/opt/nxlx", "nxlx-player", version()))
        self.assertIn("NXLX_MEDIA_DIR=/var/lib/nxlx/video", self.read("etc/nxlx/nxlx.env"))
        self.assertNotIn("PVJ_", self.read("etc/nxlx/nxlx.env"))
        self.assertEqual(os.readlink(self.p("usr/local/bin/nxlx-convert")), "/opt/nxlx/current/bin/nxlx-convert")
        self.assertTrue(os.path.isfile(self.p("etc/systemd/system/nxlx-convert-undo.timer")), "the undo is armed")
        # A second run is a plain install: nothing is converted again, and the old release is still "previous".
        again = self.install()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("CONVERTED", again.stdout)
        self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/%s\n" % self.old_version)
        # The undo goes back to the old names and the release the node ran before.
        u = subprocess.run(["python3", os.path.join(REPO, "bin", "nxlx-convert"), "--root", self.root, "--undo"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), old_release)
        self.assertTrue(os.path.isfile(self.p("etc/systemd/system/pvj-web.service")))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-web", self.read("etc/systemd/system/pvj-web.service"))
        self.assertFalse(os.path.exists(self.p("opt/nxlx")))

    def confirmed_node(self):
        self.assertEqual(self.install().returncode, 0)
        subprocess.run(["python3", os.path.join(REPO, "bin", "nxlx-convert"), "--root", self.root, "--confirm"],
                       capture_output=True, text=True, timeout=120)

    def test_a_node_with_the_new_folders_and_the_old_variable_names_is_refused_and_nothing_changes(self):
        """The state between rename steps 7 and 8 is no state a node is meant to be in: the installer says so before
        it changes anything (also before it replaces a release of the same version in place)."""
        self.confirmed_node()
        with open(self.p("etc/nxlx/nxlx.env"), "a") as f:
            f.write("PVJ_SIGNER_URL=http://old.example:8781\nPVJ_PORT=81\n")
        os.makedirs(self.p("etc/systemd/system/nxlx-web.service.d"), exist_ok=True)
        with open(self.p("etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf"), "w") as f:
            f.write("[Service]\nSupplementaryGroups=nxlx-ndi\nEnvironment=PVJ_NDI_DIR=/run/nxlx-ndi\n")
        with open(self.p("etc/nxlx/allowed_signers"), "w") as f:
            f.write('# pvj-release is only a comment here\npvj-release namespaces="pvj-release" ssh-ed25519 AAAA owner\n')
        before = snapshot(self.root)
        r = self.install("--signer", "http://hp.example:8781", "--root-fingerprint", "ab" * 32)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        said = r.stdout + r.stderr
        message = r.stderr.strip().splitlines()[-1]
        self.assertTrue(message.startswith("nxlx-install: error: old names in /etc/"), "the reason and the first file come first: " + message)
        self.assertLessEqual(len(message), 480, "the panel shows only the end of what an installer says")
        self.assertIn("(and 2 more)", message)
        self.assertIn("This release cannot be installed over it and nothing was changed", message)
        self.assertIn("mctl/README.md", message)
        self.assertEqual(snapshot(self.root), before, "not a file, not the release of the same version, was touched")

    def test_a_file_name_that_holds_the_words_the_installer_once_looked_for_is_no_leftover(self):
        """The installer reads the converter's exit code, not its text: a drop-in called "Changes (0).conf" fooled it."""
        self.confirmed_node()
        os.makedirs(self.p("etc/systemd/system/nxlx-web.service.d"), exist_ok=True)
        stale = self.p("etc/systemd/system/nxlx-web.service.d/Changes (0).conf")
        with open(stale, "w") as f:
            f.write("[Service]\nEnvironment=PVJ_NDI_DIR=/run/nxlx-ndi\n")
        r = self.install()
        self.assertNotEqual(r.returncode, 0, "it holds an old variable name")
        self.assertIn("Changes (0).conf", r.stderr)
        with open(stale, "w") as f:
            f.write("[Service]\nEnvironment=NXLX_NDI_DIR=/run/nxlx-ndi\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_files_of_the_owner_and_comments_with_an_old_name_never_stop_an_install(self):
        """Only variable and signing names in nxlx.env, allowed_signers and the nxlx-* units count. The converter would
        still rewrite these others (a comment, a script, a rule), but that is no reason to refuse every later install."""
        self.confirmed_node()
        for rel, text in (("usr/local/bin/node-backup", "#!/bin/sh\n# copies what pvj-web wrote before the rename\n"),
                          ("etc/systemd/system/capture.service", "[Service]\nExecStart=/usr/bin/true\n# was After=pvj-player.service\n"),
                          ("etc/udev/rules.d/90-card.rules", '# old: RUN+="/usr/local/bin/pvj-usb"\n'),
                          ("etc/sudoers.d/020_owner", "# pvj-dev had this before\nowner ALL=(ALL) NOPASSWD: ALL\n")):
            os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
            with open(self.p(rel), "w") as f:
                f.write(text)
        os.makedirs(self.p("etc/systemd/system/nxlx-web.service.d"), exist_ok=True)
        with open(self.p("etc/systemd/system/nxlx-web.service.d/60-owner.conf"), "w") as f:
            f.write("[Service]\n# Environment=PVJ_PORT=81 stays a comment\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_converter_that_cannot_answer_stops_the_installer_before_any_change(self):
        """Only exit code 0 means "no leftovers": a crash, a kill or a missing answer is no yes."""
        self.confirmed_node()
        for name, body in (("silent exit 1", "exit 1"), ("traceback", "echo 'Traceback (most recent call last):' >&2; exit 1"),
                           ("killed", "kill -9 $$"), ("garbage and exit 0 is the converter's own word", None)):
            if body is None:
                continue
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            stub = os.path.join(d, "python3")
            with open(stub, "w") as f:
                f.write('#!/bin/bash\nfor a in "$@"; do if [ "$a" = --stale-names ]; then %s; fi; done\nexec %s "$@"\n'
                        % (body, shutil.which("python3")))
            os.chmod(stub, 0o755)
            before = snapshot(self.root)
            r = subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", self.root], capture_output=True,
                               text=True, timeout=120, env=dict(os.environ, PATH=d + ":" + os.environ["PATH"]))
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn("cannot ask the converter", r.stderr, name)
            self.assertEqual(snapshot(self.root), before, name)

    def test_what_the_converter_leaves_alone_is_no_reason_to_refuse(self):
        """The installer asks the converter, so a name the converter's rows do not take (pvj-release-2026 in a key's
        comment, MYPVJ_FOO) is left as it is and the install goes on."""
        self.confirmed_node()
        with open(self.p("etc/nxlx/allowed_signers"), "a") as f:
            f.write('nxlx-release namespaces="nxlx-release" ssh-ed25519 AAAAC3Nza pvj-release-2026\n')
        with open(self.p("etc/nxlx/nxlx.env"), "a") as f:
            f.write("MYPVJ_FOO=1\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("cannot be installed over it", r.stdout + r.stderr)

    def test_a_fresh_install_of_this_release_has_nothing_left_for_the_converter(self):
        """Any old name this release's own install writes (a unit, a drop-in, a template) shows up here."""
        fresh = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fresh, True)
        r = subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", fresh, "--signer", "http://hp.example:8781",
                            "--root-fingerprint", "ab" * 32], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = subprocess.run(["python3", os.path.join(REPO, "bin", "nxlx-convert"), "--root", fresh, "--plan"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("Changes (0)", plan.stdout)
        self.assertIn("Nothing to convert.", plan.stdout)
        # and again over itself: one line for the signer, not two
        again = subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", fresh, "--signer", "http://hp.example:9999",
                                "--root-fingerprint", "ab" * 32], capture_output=True, text=True, timeout=120)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        with open(os.path.join(fresh, "etc/nxlx/nxlx.env")) as f:
            env = f.read()
        self.assertEqual(env.count("NXLX_SIGNER_URL="), 1)
        self.assertIn("NXLX_SIGNER_URL=http://hp.example:9999", env)

    def test_a_refused_conversion_stops_the_installer_and_changes_nothing_but_the_waiting_release(self):
        os.makedirs(self.p("proc"))
        with open(self.p("proc/mounts"), "w") as f:
            f.write("overlayroot / overlay rw,relatime 0 0\n")            # a read-only root (nxlx-rootfs on)
        before = snapshot(self.root)
        r = self.install()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("the root file system is read-only", r.stdout)
        self.assertIn("the conversion was refused and nothing on the node was changed", r.stderr)
        after = snapshot(self.root)
        waiting = "opt/pvj/releases/" + version()
        self.assertTrue(os.path.isdir(self.p(waiting)))
        self.assertEqual({k: v for k, v in after.items() if k != waiting and not k.startswith(waiting + "/")}, before)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/" + self.old_version)

    def test_a_dry_run_on_an_old_node_says_what_it_would_do_and_changes_nothing(self):
        before = snapshot(self.root)
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would convert the node: python3 /opt/pvj/releases/%s/bin/nxlx-convert --run --release %s"
                      % (version(), version()), r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_another_prefix_or_an_uninstall_on_an_old_node_is_refused(self):
        before = snapshot(self.root)
        for args, said in ((("--prefix", "/srv/box/nxlx"), "run the installer without --prefix"),
                           (("--uninstall",), "run the installer once without --uninstall")):
            r = self.install(*args)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn(said, r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_a_user_the_conversion_does_not_rename_is_refused_before_anything_changes(self):
        before = snapshot(self.root)
        r = self.install("--user", "pvj-somebody")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("the conversion renames only the node's own accounts", r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_an_old_account_name_given_again_after_the_conversion_is_the_same_account(self):
        """--user pvj-player on a later install, once the node has the new names: the converted account, under its
        new name, never a second account made beside it."""
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.convert("--confirm").returncode, 0)
        again = self.install("--user", "pvj-player")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("--user pvj-player: since the conversion this account is called nxlx-player", again.stdout)
        self.assertEqual(json.loads(self.read("etc/nxlx/install.json"))["user"], "nxlx-player")
        player = self.read("etc/systemd/system/nxlx-player.service")
        self.assertIn("User=nxlx-player", player)
        self.assertNotIn("pvj-player", player)
        other = self.install("--user", "pvj-somebody")
        self.assertNotEqual(other.returncode, 0, other.stdout)
        self.assertIn("an old-style name that is not one of the node's own accounts", other.stderr)

    def convert(self, *args):
        return subprocess.run(["python3", os.path.join(REPO, "bin", "nxlx-convert"), "--root", self.root, *args],
                              capture_output=True, text=True, timeout=120)

    def changes(self):
        """How many changes a conversion of this node makes (the release staged as install.sh stages it)."""
        probe = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, probe, True)
        shutil.rmtree(probe)
        shutil.copytree(self.root, probe, symlinks=True)
        r = subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", probe],
                           capture_output=True, text=True, timeout=120)
        import re
        m = re.search(r"Converted: (\d+) changes", r.stdout)
        self.assertIsNotNone(m, r.stdout + r.stderr)
        return int(m.group(1))

    def test_an_install_cut_off_in_the_conversion_is_carried_on_by_the_next_one_then_confirmed_or_undone(self):
        """The reviewer's case: the first changes rename the old folders, so a re-run must see the conversion in the
        converter's journal, not in the folders, carry it on, and never move "current" itself (rename PR 7)."""
        total = self.changes()
        self.assertGreater(total, 30)
        old_release = "/opt/pvj/releases/" + self.old_version
        template = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, template, True)
        shutil.rmtree(template)
        shutil.copytree(self.root, template, symlinks=True)
        for at in (5, 30, total + 1):        # past the folder step; deep in the system files; after the last change
            for end in ("confirm", "undo"):
                with self.subTest(at=at, end=end):
                    shutil.rmtree(self.root)
                    shutil.copytree(template, self.root, symlinks=True)
                    before = snapshot(self.root)
                    cut = subprocess.run([os.path.join(REPO, "install", "install.sh"), "--stage", self.root],
                                         capture_output=True, text=True, timeout=120,
                                         env=dict(os.environ, NXLX_CONVERT_FAIL_AT=str(at)))
                    self.assertNotEqual(cut.returncode, 0, cut.stdout)
                    self.assertIn("the conversion stopped half-way", cut.stderr)
                    self.assertEqual(self.convert("--state").stdout.strip(), "pending")
                    again = self.install()
                    self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                    self.assertIn("carrying on with a conversion that stopped half-way", again.stdout)
                    self.assertIn("A conversion waits for its confirm", again.stdout)
                    self.assertEqual(self.convert("--state").stdout.strip(), "unconfirmed")
                    self.assertEqual(os.readlink(self.p("opt/nxlx/current")), "/opt/nxlx/releases/" + version())
                    self.assertEqual(self.read("opt/nxlx/previous"), "/opt/nxlx/releases/%s\n" % self.old_version)
                    for name in os.listdir(self.p("etc/systemd/system")):
                        self.assertNotIn("pvj", name, "an old unit left beside the new ones")
                    if end == "confirm":
                        c = self.convert("--confirm")
                        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
                        self.assertFalse(os.path.exists(self.p("etc/systemd/system/nxlx-convert-undo.timer")))
                        self.assertEqual(self.convert("--state").stdout.strip(), "confirmed")
                    else:
                        u = self.convert("--undo")
                        self.assertEqual(u.returncode, 0, u.stdout + u.stderr)
                        self.assertEqual(os.readlink(self.p("opt/pvj/current")), old_release)
                        self.assertTrue(os.path.isdir(self.p(old_release.lstrip("/"), "bin")), "current leads somewhere")
                        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-web", self.read("etc/systemd/system/pvj-web.service"))
                        self.assertFalse(os.path.exists(self.p("opt/nxlx")))
                        # What install.sh adds that is no change of the run: the waiting release, the command link,
                        # and the runtime folder (/run is rebuilt at boot and never the converter's).
                        waiting = "opt/pvj/releases/" + version()
                        after = snapshot(self.root, skip=("var/backups", waiting, "run/nxlx"))
                        for extra in (waiting, "usr/local/bin/nxlx-convert", "run/nxlx", "var/backups"):
                            after.pop(extra, None)
                        want = {k: v for k, v in before.items() if k != "var/backups"}
                        self.assertEqual(after, want)

    def test_while_a_conversion_waits_for_its_confirm_another_version_is_refused(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        src = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, src, True)
        for d in ("mctl", "bin", "install"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(src, d), ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(src, "mctl", "__init__.py"), "w") as f:
            f.write('__version__ = "9.9.9"\n')
        before = snapshot(self.root)
        newer = subprocess.run([os.path.join(src, "install", "install.sh"), "--stage", self.root],
                               capture_output=True, text=True, timeout=120)
        self.assertNotEqual(newer.returncode, 0, newer.stdout)
        self.assertIn("waits for its confirm: sudo nxlx-convert --confirm", newer.stderr)
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
