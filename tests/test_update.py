# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from mctl import update
from mctl.update import UpdateError, Updater
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HAVE_SSH = bool(shutil.which("ssh-keygen"))


def root_for_main():
    """A patch under which update.main() believes it runs as root (it refuses anybody else) while everything it
    calls still sees who we are. The lock compares the owner of its folder and its file with the effective user,
    so saying "root" to everybody would make every file here somebody else's."""
    real = os.geteuid

    def geteuid():
        return 0 if sys._getframe(1).f_code.co_name == "main" else real()
    return mock.patch("os.geteuid", geteuid)


def make_tree(version, schema=None):
    d = tempfile.mkdtemp()
    for sub in ("mctl", "bin", "install"):
        shutil.copytree(os.path.join(REPO, sub), os.path.join(d, sub), ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(d, "mctl", "__init__.py"), "w") as f:
        f.write('__version__ = "%s"\n' % version)
    if schema is not None:
        p = os.path.join(d, "mctl", "settings.py")
        with open(p) as f:
            text = f.read()
        with open(p, "w") as f:
            f.write(re.sub(r"^SCHEMA = \d+", "SCHEMA = %d" % schema, text, flags=re.M))
    return d


def make_bundle(version, schema=None, top=True):
    tree = make_tree(version, schema)
    out = os.path.join(tempfile.mkdtemp(), "nxlx-%s.tar.gz" % version)
    with tarfile.open(out, "w:gz") as tar:
        for sub in ("mctl", "bin", "install"):
            tar.add(os.path.join(tree, sub), arcname=("nxlx-%s/%s" % (version, sub)) if top else sub)
    with open(out + ".sha256", "w") as f:
        f.write(update.sha256_file(out) + "  " + os.path.basename(out) + "\n")
    shutil.rmtree(tree, True)           # the copy of the source is in the bundle now: 3 MB a call (D69)
    return out


def tar_with(members):
    """members: list of (TarInfo, bytes|None)"""
    out = os.path.join(tempfile.mkdtemp(), "x.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return out


class Env:
    """A fake root with the real installer running in --stage mode."""

    def __init__(self, test):
        self.root = tempfile.mkdtemp()
        self.restarts = 0
        self.healthy = True
        os.makedirs(os.path.join(self.root, "etc/nxlx"))
        os.makedirs(os.path.join(self.root, "var/lib/nxlx"))
        self.updater = Updater(root=self.root, install=self.install, restart=self.restart, health=lambda: self.healthy,
                               rootfs_state=lambda: "normal")      # not this machine's /proc/mounts (a container's / is an overlay)
        test.addCleanup(shutil.rmtree, self.root, True)

    def install(self, work, info):
        r = subprocess.run([os.path.join(work, "install", "install.sh"), "--stage", self.root, "--user", "gigbox"],
                           capture_output=True, text=True)
        if r.returncode:
            raise UpdateError(r.stderr)

    def restart(self):
        self.restarts += 1

    def write_settings(self, schema=1, marker="x"):
        with open(os.path.join(self.root, "var/lib/nxlx/settings.json"), "w") as f:
            json.dump({"schema": schema, "marker": marker}, f)

    def settings(self):
        with open(os.path.join(self.root, "var/lib/nxlx/settings.json")) as f:
            return json.load(f)

    def trust_nothing(self):
        return self.updater.allow_unsigned if False else None


class HelperTest(unittest.TestCase):
    def test_versions(self):
        self.assertLess(update.parse_version("1.9.0"), update.parse_version("1.10.0"))
        for bad in ("1.0", "v1.0.0", "1.0.0-beta", "", None, "1.0.0; rm"):
            with self.assertRaises(UpdateError):
                update.parse_version(bad)

    def test_sha256(self):
        f = os.path.join(tempfile.mkdtemp(), "f")
        with open(f, "wb") as fh:
            fh.write(b"abc")
        good = update.sha256_file(f)
        update.verify_sha256(f, good + "  f\n")
        for bad in ("0" * 64, "", None, "nothex"):
            with self.assertRaises(UpdateError):
                update.verify_sha256(f, bad)

    def test_safe_extract_strips_top_folder_and_drops_setuid(self):
        info = tarfile.TarInfo("nxlx-1.0.0/bin/tool")
        info.size, info.mode = 3, 0o4777
        d = tempfile.mkdtemp()
        update.safe_extract(tar_with([(info, b"abc")]), d)
        path = os.path.join(d, "bin", "tool")
        self.assertEqual(open(path).read() if False else oct(os.stat(path).st_mode & 0o7777), "0o755")

    def test_safe_extract_rejects_dangerous_members(self):
        def file_(name, size=1):
            i = tarfile.TarInfo(name)
            i.size = size
            return (i, b"x" * size)

        def special(kind, name, link=""):
            i = tarfile.TarInfo(name)
            i.type = kind
            i.linkname = link
            return (i, None)
        cases = [[file_("/etc/passwd")], [file_("../evil")], [file_("a/../../evil")],
                 [special(tarfile.SYMTYPE, "a/link", "/etc")], [special(tarfile.LNKTYPE, "a/hard", "b")],
                 [special(tarfile.CHRTYPE, "a/dev")], [file_("a\\b")]]
        for members in cases:
            d = tempfile.mkdtemp()
            with self.assertRaises(UpdateError, msg=members[0][0].name):
                update.safe_extract(tar_with(members), d)
            self.assertEqual(os.listdir(d), [])  # nothing escaped or was left half-written at the root

    def test_inspect_bundle(self):
        tree = make_tree("2.3.4")
        self.addCleanup(shutil.rmtree, tree, True)
        info = update.inspect_bundle(tree)
        self.assertEqual(info["version"], "2.3.4")
        self.assertGreaterEqual(info["schema"], 1)
        os.unlink(os.path.join(tree, "bin", "nxlx-web"))
        with self.assertRaises(UpdateError):
            update.inspect_bundle(tree)

    def test_inspect_bundle_refuses_a_release_from_before_the_system_rename(self):
        """A bundle with the old program names (bin/pvj-player) or the old package folder would install the old
        layout beside the converted one (rename PR 7): refused, with the way back named."""
        for old in ("bin", "pvj"):
            tree = make_tree("2.3.4")
            self.addCleanup(shutil.rmtree, tree, True)
            if old == "bin":
                for name in ("nxlx-player", "nxlx-web"):
                    os.rename(os.path.join(tree, "bin", name), os.path.join(tree, "bin", name.replace("nxlx-", "pvj-")))
            else:
                os.rename(os.path.join(tree, "mctl"), os.path.join(tree, "pvj"))
                os.unlink(os.path.join(tree, "bin", "nxlx-player"))
            with self.assertRaises(UpdateError) as e:
                update.inspect_bundle(tree)
            self.assertIn("before the rename of the system names", str(e.exception))
            self.assertIn("nxlx-convert --undo", str(e.exception))


@unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
class SignatureTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def keypair(self, name):
        key = os.path.join(self.d, name)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        allowed = os.path.join(self.d, name + ".allowed")
        with open(allowed, "w") as f:
            f.write('%s namespaces="nxlx-release" %s %s\n' % (update.PRINCIPAL, fields[0], fields[1]))
        return key, allowed

    def sign(self, key, path):
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", update.NAMESPACE, path],
                       check=True, capture_output=True)

    def test_good_tampered_wrong_key_and_missing(self):
        key, allowed = self.keypair("k1")
        _, other_allowed = self.keypair("k2")
        f = os.path.join(self.d, "bundle")
        with open(f, "wb") as fh:
            fh.write(b"payload")
        self.sign(key, f)
        update.verify_signature(f, f + ".sig", allowed)
        with self.assertRaises(UpdateError):
            update.verify_signature(f, f + ".sig", other_allowed)  # signed by a key we do not trust
        with open(f, "ab") as fh:
            fh.write(b"!")
        with self.assertRaises(UpdateError):
            update.verify_signature(f, f + ".sig", allowed)  # tampered after signing
        with self.assertRaises(UpdateError):
            update.verify_signature(f, f + ".nosuch", allowed)
        empty = os.path.join(self.d, "empty")
        open(empty, "w").close()
        with self.assertRaises(UpdateError):
            update.verify_signature(f, f + ".sig", empty)  # no trusted key means no updates
        with self.assertRaises(UpdateError):
            update.verify_signature(f, f + ".sig", os.path.join(self.d, "missing"))
        comments = os.path.join(self.d, "comments")
        with open(comments, "w") as fh:
            fh.write("# only a comment, no key\n\n")
        with self.assertRaises(UpdateError) as cm:
            update.verify_signature(f, f + ".sig", comments)
        self.assertIn("no trusted signing key", str(cm.exception))


class ReadOnlyRootTest(unittest.TestCase):
    """J-174: with the read-only root on, an update or a rollback is written to RAM and lost at the restart."""

    def setUp(self):
        self.env = Env(self)
        self.u = self.env.updater
        self.state = "normal"
        self.u._rootfs_state = lambda: self.state
        self.bundle = make_bundle("1.0.0")

    def snapshot(self):
        backups = sorted(os.listdir(self.u.backup_dir())) if os.path.isdir(self.u.backup_dir()) else None
        return (self.u.status(), self.env.restarts, backups)

    def test_every_action_that_writes_a_release_is_refused_under_the_overlay(self):
        from mctl import rootfs
        self.u.apply(self.bundle, allow_unsigned=True, log=lambda *_: None)          # installed while the root is writable
        self.env.write_settings(marker="kept")
        before = self.snapshot()
        self.state = "overlay"
        for call in (lambda: self.u.check(self.bundle, allow_unsigned=True),
                     lambda: self.u.apply(make_bundle("1.1.0"), allow_unsigned=True, log=lambda *_: None),
                     lambda: self.u.apply(make_bundle("1.1.0"), allow_unsigned=True, force=True, log=lambda *_: None),
                     lambda: self.u.rollback()):
            with self.assertRaises(UpdateError) as cm:
                call()
            self.assertEqual(str(cm.exception), rootfs.UPDATE_REFUSAL)
        self.assertEqual(before, self.snapshot())                         # no release, restart, backup or switch
        self.assertEqual(self.env.settings()["marker"], "kept")
        self.assertEqual([n for n in os.listdir(os.path.join(self.env.root, "opt/nxlx")) if n.startswith(".update-")], [])

    def test_the_message_says_what_to_do(self):
        from mctl import rootfs
        for words in ("read-only", "lost at the next restart", "sudo nxlx-rootfs disable", "sudo nxlx-rootfs enable"):
            self.assertIn(words, rootfs.UPDATE_REFUSAL)

    def test_status_still_works_under_the_overlay(self):
        self.state = "overlay"
        self.assertEqual(self.u.status(), {"current": None, "previous": None, "releases": []})

    def test_nothing_changes_when_the_root_is_normal(self):
        self.assertEqual(self.u.apply(self.bundle, allow_unsigned=True, log=lambda *_: None), "1.0.0")
        self.u.apply(make_bundle("1.1.0"), allow_unsigned=True, log=lambda *_: None)
        self.assertEqual(self.u.rollback(log=lambda *_: None), "1.0.0")
        info, _tree, scratch = self.u.check(make_bundle("1.2.0"), allow_unsigned=True)
        shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual(info["version"], "1.2.0")

    def test_the_default_state_is_read_from_proc_mounts(self):
        u = Updater(root=self.env.root)
        with mock.patch("mctl.rootfs.current_state", return_value="overlay"):
            with self.assertRaises(UpdateError):
                u.rollback()


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.u = self.env.updater

    def apply(self, version, **kw):
        return self.u.apply(make_bundle(version, kw.pop("schema", None)), allow_unsigned=True, log=lambda *_: None, **kw)

    def test_fresh_apply_then_upgrade_records_previous_and_backs_up_settings(self):
        self.apply("1.0.0")
        self.assertEqual(self.u.status()["current"], "1.0.0")
        self.env.write_settings(marker="before-upgrade")
        self.apply("1.1.0")
        st = self.u.status()
        self.assertEqual((st["current"], st["previous"], st["releases"]), ("1.1.0", "1.0.0", ["1.0.0", "1.1.0"]))
        self.assertEqual(len(os.listdir(self.u.backup_dir())), 1)  # the first install had no settings to back up
        self.assertGreater(self.env.restarts, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.env.root, "opt/nxlx/releases/1.1.0/bin/nxlx-web")))

    def test_failed_health_check_rolls_back_automatically(self):
        self.apply("1.0.0")
        self.env.write_settings(marker="good")
        self.env.healthy = False
        with self.assertRaises(UpdateError) as cm:
            self.apply("1.1.0")
        self.assertIn("rolled back", str(cm.exception))
        self.assertEqual(self.u.status()["current"], "1.0.0")
        self.assertEqual(self.env.settings()["marker"], "good")

    def test_refuses_same_older_and_schema_downgrade_unless_forced(self):
        self.apply("1.1.0")
        for v in ("1.1.0", "1.0.0"):
            with self.assertRaises(UpdateError, msg=v):
                self.apply(v)
        self.env.write_settings(schema=5)
        with self.assertRaises(UpdateError):
            self.apply("1.2.0", schema=2)
        self.assertEqual(self.u.status()["current"], "1.1.0")
        self.apply("1.1.0", force=True)  # reinstall
        self.apply("1.2.0", schema=2, force=True)
        self.assertEqual(self.u.status()["current"], "1.2.0")

    def test_bad_checksum_and_unsigned_are_refused_and_leave_no_trace(self):
        bundle = make_bundle("1.0.0")
        with open(bundle + ".sha256", "w") as f:
            f.write("0" * 64)
        with self.assertRaises(UpdateError):
            self.u.apply(bundle, allow_unsigned=True, log=lambda *_: None)
        fresh = make_bundle("1.0.0")
        with self.assertRaises(UpdateError):
            self.u.apply(fresh, log=lambda *_: None)  # signature required by default
        self.assertIsNone(self.u.status()["current"])
        self.assertFalse(os.path.isdir(os.path.join(self.env.root, "opt/nxlx/releases")) and os.listdir(os.path.join(self.env.root, "opt/nxlx/releases")))

    def test_installer_failure_leaves_current_untouched(self):
        self.apply("1.0.0")

        def broken(work, info):
            raise UpdateError("disk full")
        self.u._install = broken
        with self.assertRaises(UpdateError):
            self.apply("1.1.0")
        self.assertEqual(self.u.status()["current"], "1.0.0")

    def test_manual_rollback_restores_matching_settings_and_swaps_previous(self):
        self.apply("1.0.0")
        self.env.write_settings(schema=1, marker="v1-era")
        self.apply("1.1.0")
        self.env.write_settings(schema=2, marker="migrated-by-v1.1")
        self.assertEqual(self.u.rollback(log=lambda *_: None), "1.0.0")
        st = self.u.status()
        self.assertEqual((st["current"], st["previous"]), ("1.0.0", "1.1.0"))
        self.assertEqual(self.env.settings()["marker"], "v1-era")
        aside = [n for n in os.listdir(os.path.join(self.env.root, "var/lib/nxlx")) if "rolled-back" in n]
        self.assertEqual(len(aside), 1)  # newer settings set aside, not deleted

    def test_rollback_after_the_backups_were_removed_brings_nothing_back(self):
        """J-055: a factory reset removes the saved settings copies; a rollback then changes the program and says so in
        words, and the settings (the reset ones) stay as they are."""
        self.apply("1.0.0")
        self.env.write_settings(schema=1, marker="old-devices")
        self.apply("1.1.0")
        for name in os.listdir(self.u.backup_dir()):                  # what the helper's clear_update_backups does
            os.unlink(os.path.join(self.u.backup_dir(), name))
        self.env.write_settings(schema=2, marker="after-reset")
        said = []
        self.assertEqual(self.u.rollback(log=said.append), "1.0.0")
        self.assertEqual(self.env.settings()["marker"], "after-reset")
        self.assertTrue(any("no saved copy of the settings" in line and "factory reset" in line for line in said), said)

    def test_rollback_without_previous_is_an_error(self):
        self.apply("1.0.0")
        with self.assertRaises(UpdateError):
            self.u.rollback(log=lambda *_: None)

    def test_rollback_to_a_release_from_before_the_system_rename_is_refused_and_changes_nothing(self):
        """After a conversion the previous release is the one with the old names (bin/pvj-web): it does not run under
        the new units, so only the converter's undo goes back to it (rename PR 7)."""
        self.apply("1.0.0")
        self.apply("1.1.0")
        old = os.path.join(self.env.root, "opt/nxlx/releases/1.0.0/bin")
        for name in os.listdir(old):
            os.rename(os.path.join(old, name), os.path.join(old, name.replace("nxlx-", "pvj-")))
        before = (self.u.status(), self.env.restarts)
        with self.assertRaises(UpdateError) as e:
            self.u.rollback(log=lambda *_: None)
        self.assertIn("nxlx-convert --undo", str(e.exception))
        self.assertEqual((self.u.status(), self.env.restarts), before)

    def test_whitespace_only_checksum_file_is_a_clean_error(self):
        bundle = make_bundle("1.0.0")
        with open(bundle + ".sha256", "w") as f:
            f.write("  \n\t\n")
        with self.assertRaises(UpdateError):
            self.u.apply(bundle, allow_unsigned=True, log=lambda *_: None)

    def test_failed_update_leaves_rollback_history_alone(self):
        self.apply("1.0.0")
        self.apply("1.1.0")
        self.assertEqual(self.u.status()["previous"], "1.0.0")
        self.env.healthy = False
        with self.assertRaises(UpdateError):
            self.apply("1.2.0")
        st = self.u.status()
        self.assertEqual((st["current"], st["previous"]), ("1.1.0", "1.0.0"))
        self.env.healthy = True
        self.assertEqual(self.u.rollback(log=lambda *_: None), "1.0.0")  # still rolls back to the right place

    def test_installer_crash_also_restores_previous_pointer(self):
        self.apply("1.0.0")
        self.apply("1.1.0")

        def crash_after_switch(work, info):
            self.env.install(work, info)  # really installs 1.2.0 and moves the pointers
            raise UpdateError("service restart failed")
        self.u._install = crash_after_switch
        with self.assertRaises(UpdateError):
            self.apply("1.2.0")
        st = self.u.status()
        self.assertEqual((st["current"], st["previous"]), ("1.1.0", "1.0.0"))

    def test_bundle_swapped_after_verification_is_not_what_gets_installed(self):
        good = make_bundle("1.0.0")
        evil = make_bundle("6.6.6")
        real_verify = update.verify_sha256

        def verify_then_swap(path, expected):
            real_verify(path, expected)
            shutil.copyfile(evil, good)  # attacker replaces the file on the USB stick right after the check
        update.verify_sha256 = verify_then_swap
        self.addCleanup(setattr, update, "verify_sha256", real_verify)
        self.assertEqual(self.u.apply(good, allow_unsigned=True, log=lambda *_: None), "1.0.0")
        self.assertEqual(self.u.status()["current"], "1.0.0")

    def test_health_check_uses_the_port_from_the_env_file(self):
        import http.server
        import threading

        class Hello(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        httpd = http.server.HTTPServer(("127.0.0.1", 0), Hello)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        with open(os.path.join(self.env.root, "etc/nxlx/nxlx.env"), "w") as f:
            f.write("# panel port\nNXLX_PORT=%d\n" % port)
        real = Updater(root=self.env.root)
        self.assertEqual(real.configured_port(), str(port))
        self.assertTrue(real._http_health(timeout=3))
        with open(os.path.join(self.env.root, "etc/nxlx/nxlx.env"), "w") as f:
            f.write("NXLX_PORT=1\n")
        self.assertFalse(real._http_health(timeout=1.2))

    @unittest.skipUnless(os.geteuid() == 0, "needs root to set file owners")
    def test_restored_settings_keep_the_panels_owner_and_stay_private(self):
        self.apply("1.0.0")
        self.env.write_settings(marker="v1")
        self.apply("1.1.0")
        path = os.path.join(self.env.root, "var/lib/nxlx/settings.json")
        os.chown(path, 23456, 34567)  # as if nxlx-web owns it
        self.u.rollback(log=lambda *_: None)
        st = os.stat(path)
        self.assertEqual((st.st_uid, st.st_gid, st.st_mode & 0o777), (23456, 34567, 0o600))

    def test_backups_are_pruned(self):
        self.env.write_settings()
        clock = iter(range(1000, 2000))
        self.u.now = lambda: next(clock)
        for _ in range(8):
            self.u.backup_settings("1.0.0")
        self.assertEqual(len(os.listdir(self.u.backup_dir())), update.KEEP_BACKUPS)

    def test_usb_bundles_newest_first(self):
        base = tempfile.mkdtemp()
        for label, names in (("A", ["nxlx-1.2.0.tar.gz", "notes.txt"]), ("B", ["nxlx-1.10.0.tar.gz", "nxlx-1.9.0.tar.gz"])):
            os.makedirs(os.path.join(base, label, "nxlx-update"))
            for n in names:
                open(os.path.join(base, label, "nxlx-update", n), "w").close()
        found = [os.path.basename(p) for p in self.u.usb_bundles(base)]
        self.assertEqual(found, ["nxlx-1.10.0.tar.gz", "nxlx-1.9.0.tar.gz", "nxlx-1.2.0.tar.gz"])

    def test_the_old_bundle_names_and_the_old_usb_folder_are_not_offered(self):
        """Rename PR 8: a bundle is nxlx-N.N.N.tar.gz in the folder nxlx-update. One kept under the old names is
        re-signed and renamed by the owner; the updater does not take it as it is."""
        base = tempfile.mkdtemp()
        for folder, names in (("nxlx-update", ["pvj-1.4.0.tar.gz", "nxlx-1.3.0.tar.gz"]), ("pvj-update", ["nxlx-1.5.0.tar.gz", "pvj-1.6.0.tar.gz"])):
            os.makedirs(os.path.join(base, "STICK", folder), exist_ok=True)
            for n in names:
                open(os.path.join(base, "STICK", folder, n), "w").close()
        self.assertEqual([os.path.basename(p) for p in self.u.usb_bundles(base)], ["nxlx-1.3.0.tar.gz"])
        self.assertIsNone(update.Updater.BUNDLE_NAME.fullmatch("pvj-1.4.0.tar.gz"))
        self.assertEqual((update.PRINCIPAL, update.NAMESPACE), ("nxlx-release", "nxlx-release"))

    @unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
    def test_a_signature_made_under_the_old_namespace_does_not_verify(self):
        d = tempfile.mkdtemp()
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        with open(self.u.allowed_signers(), "w") as f:
            f.write('%s namespaces="%s" %s %s\n' % (update.PRINCIPAL, update.NAMESPACE, fields[0], fields[1]))
        bundle = make_bundle("1.0.0")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", "pvj-release", bundle], check=True, capture_output=True)
        with self.assertRaises(update.UpdateError):
            update.verify_signature(bundle, bundle + ".sig", self.u.allowed_signers())
        os.unlink(bundle + ".sig")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", "nxlx-release", bundle], check=True, capture_output=True)
        update.verify_signature(bundle, bundle + ".sig", self.u.allowed_signers())      # signed again, it does

    @unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
    def test_signed_bundle_end_to_end(self):
        d = tempfile.mkdtemp()
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        with open(self.u.allowed_signers(), "w") as f:
            f.write('%s namespaces="nxlx-release" %s %s\n' % (update.PRINCIPAL, fields[0], fields[1]))
        bundle = make_bundle("1.0.0")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", update.NAMESPACE, bundle], check=True, capture_output=True)
        self.assertEqual(self.u.apply(bundle, log=lambda *_: None), "1.0.0")
        tampered = make_bundle("1.1.0")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", update.NAMESPACE, tampered], check=True, capture_output=True)
        with open(tampered, "ab") as fh:
            fh.write(b"x")
        with open(tampered + ".sha256", "w") as f:
            f.write(update.sha256_file(tampered))
        with self.assertRaises(UpdateError):
            self.u.apply(tampered, log=lambda *_: None)
        self.assertEqual(self.u.status()["current"], "1.0.0")


class StateFolderLinksTest(unittest.TestCase):
    """J-177: /var/lib/nxlx is the panel's and has no sticky bit; root's update code must not follow a name the panel
    planted there. Each test plants a link and proves what it points to is untouched."""

    def setUp(self):
        self.env = Env(self)
        self.u = self.env.updater
        self.state = os.path.join(self.env.root, "var/lib/nxlx")
        self.victims = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.victims, True)
        self.victim = os.path.join(self.victims, "victim")
        with open(self.victim, "w") as f:
            f.write("ROOT-SECRET")
        os.chmod(self.victim, 0o644)
        self.env.write_settings(marker="mine")
        self.u.now = lambda: 1000

    def victim_untouched(self):
        with open(self.victim) as f:
            self.assertEqual(f.read(), "ROOT-SECRET")
        self.assertEqual(os.stat(self.victim).st_mode & 0o777, 0o644)

    def test_a_link_for_settings_json_is_not_copied_into_a_backup(self):
        os.unlink(os.path.join(self.state, "settings.json"))
        os.symlink(self.victim, os.path.join(self.state, "settings.json"))
        self.assertIsNone(self.u.backup_settings("1.0.0"))
        self.assertEqual(os.listdir(self.u.backup_dir()) if os.path.isdir(self.u.backup_dir()) else [], [])
        self.victim_untouched()

    def test_a_pipe_for_settings_json_does_not_hang_the_update(self):
        os.unlink(os.path.join(self.state, "settings.json"))
        os.mkfifo(os.path.join(self.state, "settings.json"))
        self.assertIsNone(self.u.backup_settings("1.0.0"))
        self.assertIsNone(self.u._settings_schema())

    def test_a_link_for_the_backups_folder_is_set_aside_not_written_through_or_pruned(self):
        target = os.path.join(self.victims, "folder")
        os.mkdir(target)
        names = ["settings-9.9.%d-%d.json" % (i, i) for i in range(8)]
        for i, n in enumerate(names):
            with open(os.path.join(target, n), "w") as f:
                f.write("keep")
            os.utime(os.path.join(target, n), (i, i))
        os.symlink(target, self.u.backup_dir())
        dst = self.u.backup_settings("1.0.0")
        self.assertEqual(sorted(os.listdir(target)), sorted(names))             # nothing added, nothing pruned
        self.assertFalse(os.path.islink(self.u.backup_dir()))
        self.assertTrue(os.path.isfile(dst) and not os.path.islink(dst))
        self.assertEqual(os.stat(dst).st_mode & 0o777, 0o600)

    def test_a_folder_the_panel_made_for_backups_is_not_trusted(self):
        os.mkdir(self.u.backup_dir())
        os.chmod(self.u.backup_dir(), 0o777)                                   # not ours alone to write
        dst = self.u.backup_settings("1.0.0")
        self.assertEqual(os.listdir(self.u.backup_dir()), [os.path.basename(dst)])
        self.assertEqual(os.stat(self.u.backup_dir()).st_mode & 0o777, 0o700)

    def test_a_link_at_the_name_of_the_new_backup_is_replaced_not_written_through(self):
        os.mkdir(self.u.backup_dir(), 0o700)
        os.symlink(self.victim, os.path.join(self.u.backup_dir(), "settings-1.0.0-1000.json"))
        dst = self.u.backup_settings("1.0.0")
        self.assertFalse(os.path.islink(dst))
        with open(dst) as f:
            self.assertEqual(json.load(f)["marker"], "mine")
        self.victim_untouched()

    def test_restoring_over_a_link_at_settings_json_leaves_the_target_alone(self):
        dst = self.u.backup_settings("1.0.0")
        settings = os.path.join(self.state, "settings.json")
        os.unlink(settings)
        os.symlink(self.victim, settings)
        os.symlink(self.victim, os.path.join(self.state, ".settings.json.%d.tmp" % os.getpid()))   # and at the temporary name
        self.u.restore_settings(dst)
        self.assertFalse(os.path.islink(settings))
        self.assertEqual(self.env.settings()["marker"], "mine")
        self.assertEqual(os.stat(settings).st_mode & 0o777, 0o600)
        self.victim_untouched()

    def test_restoring_never_reads_the_backup_through_a_link(self):
        dst = self.u.backup_settings("1.0.0")
        os.unlink(dst)
        os.symlink(self.victim, dst)
        with self.assertRaises(UpdateError):
            self.u.restore_settings(dst)
        self.assertEqual(self.env.settings()["marker"], "mine")
        self.victim_untouched()

    def test_restoring_with_a_link_for_the_backups_folder_fails_and_changes_nothing(self):
        target = os.path.join(self.victims, "folder")
        os.mkdir(target)
        with open(os.path.join(target, "settings-1.0.0-1000.json"), "w") as f:
            f.write('{"schema": 1, "marker": "planted"}')
        os.symlink(target, self.u.backup_dir())
        self.assertIsNone(self.u.latest_backup("1.0.0"))
        with self.assertRaises(UpdateError):
            self.u.restore_settings(os.path.join(self.u.backup_dir(), "settings-1.0.0-1000.json"))
        self.assertEqual(self.env.settings()["marker"], "mine")

    def test_a_link_for_the_state_folder_is_not_followed(self):
        real = os.path.join(self.victims, "state")
        os.mkdir(real)
        shutil.move(os.path.join(self.state, "settings.json"), real)
        shutil.rmtree(self.state)
        os.symlink(real, self.state)
        self.assertIsNone(self.u.backup_settings("1.0.0"))
        self.assertEqual(os.listdir(real), ["settings.json"])

    # --- the review of PR 312 ---------------------------------------------------------------------------------------
    def test_a_failed_restore_does_not_skip_the_restart_or_the_previous_note(self):
        b = self.u.backup_settings("1.0.0")
        os.makedirs(os.path.join(self.env.root, "opt/nxlx/releases/1.0.0"), exist_ok=True)
        os.rename(self.u.backup_dir(), os.path.join(self.state, "hidden"))
        os.symlink("/nonexistent", self.u.backup_dir())
        before = self.env.restarts
        with mock.patch("builtins.print"):
            self.u._switch_to("1.0.0", b, current="1.1.0")
        self.assertEqual(self.env.restarts, before + 1)
        with open(os.path.join(self.env.root, "opt/nxlx/previous")) as f:
            self.assertIn("1.1.0", f.read())
        self.assertEqual(self.env.settings()["marker"], "mine")

    def test_a_planted_rejected_name_does_not_block_the_update(self):
        os.mkdir(os.path.join(self.state, "backups.rejected-1000-%d" % os.getpid()))
        os.symlink("/nonexistent", self.u.backup_dir())
        dst = self.u.backup_settings("1.0.0")
        self.assertTrue(os.path.isfile(dst) and not os.path.islink(dst))

    @unittest.skipIf(os.geteuid() == 0, "root opens a folder of mode 0")
    def test_an_open_error_that_is_not_a_link_never_moves_the_folder(self):
        self.u.backup_settings("1.0.0")
        os.chmod(self.u.backup_dir(), 0)                  # EACCES stands in for EMFILE or EIO
        self.addCleanup(os.chmod, self.u.backup_dir(), 0o700)
        self.assertIsNone(self.u.latest_backup("1.0.0"))   # a read path
        with self.assertRaises(UpdateError):                # a write path: the folder is not usable, and is still there
            self.u.backup_settings("1.0.1")
        self.assertEqual(sorted(os.listdir(self.state)), ["backups", "settings.json"])

    def test_a_read_never_sets_a_folder_aside(self):
        os.mkdir(self.u.backup_dir(), 0o755)
        os.chmod(self.u.backup_dir(), 0o777)
        self.assertIsNone(self.u.latest_backup("1.0.0"))
        self.assertEqual(sorted(os.listdir(self.state)), ["backups", "settings.json"])

    def test_the_schema_read_survives_odd_json(self):
        for body in ("[" * 200000, '{"schema": 1e999}', '{"schema": [1]}', "[1]"):
            with open(os.path.join(self.state, "settings.json"), "w") as f:
                f.write(body)
            self.assertIsNone(self.u._settings_schema(), body[:12])

    def test_a_folder_at_the_temporary_name_leaves_settings_json_in_place(self):
        b = self.u.backup_settings("1.0.0")
        os.makedirs(os.path.join(self.state, ".settings.json.%d.tmp" % os.getpid(), "x"))
        self.env.write_settings(marker="current")
        self.u.restore_settings(b)                          # the temporary name is random: a planted one is never in the way
        self.assertEqual(self.env.settings()["marker"], "mine")

    def test_a_failed_restore_leaves_the_old_settings_json_and_keeps_a_keepsake(self):
        b = self.u.backup_settings("1.0.0")
        self.env.write_settings(marker="current")
        real = os.rename

        def failing(src, dst, **kw):
            if dst == "settings.json":
                raise OSError(5, "I/O error")
            return real(src, dst, **kw)
        with mock.patch("os.rename", failing):
            with self.assertRaises(OSError):
                self.u.restore_settings(b)
        self.assertEqual(self.env.settings()["marker"], "current")
        self.assertFalse([n for n in os.listdir(self.state) if n.endswith(".tmp")])
        self.u.restore_settings(b)
        kept = [n for n in os.listdir(self.state) if n.startswith("settings.json.rolled-back-")]
        self.assertEqual(len(kept), 1)
        with open(os.path.join(self.state, kept[0])) as f:
            self.assertEqual(json.load(f)["marker"], "current")

    def test_latest_backup_matches_the_version_exactly(self):
        self.u.backup_settings("1.0.0-rc1")
        self.assertIsNone(self.u.latest_backup("1.0.0"))
        self.assertIsNone(self.u.latest_backup("1.0"))
        self.assertTrue(self.u.latest_backup("1.0.0-rc1"))

    def test_the_backups_folder_made_here_is_0700_whatever_the_parent_gives(self):
        os.chmod(self.state, 0o2750)
        self.u.backup_settings("1.0.0")
        self.assertEqual(os.stat(self.u.backup_dir()).st_mode & 0o7777, 0o700)
        self.u.now = lambda: 1001
        self.u.backup_settings("1.0.0")                     # and the second call accepts it


if __name__ == "__main__":
    unittest.main()
