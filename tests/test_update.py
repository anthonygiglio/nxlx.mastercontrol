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

from pvj import update
from pvj.update import UpdateError, Updater

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
    for sub in ("pvj", "bin", "install"):
        shutil.copytree(os.path.join(REPO, sub), os.path.join(d, sub), ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(d, "pvj", "__init__.py"), "w") as f:
        f.write('__version__ = "%s"\n' % version)
    if schema is not None:
        p = os.path.join(d, "pvj", "settings.py")
        with open(p) as f:
            text = f.read()
        with open(p, "w") as f:
            f.write(re.sub(r"^SCHEMA = \d+", "SCHEMA = %d" % schema, text, flags=re.M))
    return d


def make_bundle(version, schema=None, top=True):
    tree = make_tree(version, schema)
    out = os.path.join(tempfile.mkdtemp(), "pvj-%s.tar.gz" % version)
    with tarfile.open(out, "w:gz") as tar:
        for sub in ("pvj", "bin", "install"):
            tar.add(os.path.join(tree, sub), arcname=("pvj-%s/%s" % (version, sub)) if top else sub)
    with open(out + ".sha256", "w") as f:
        f.write(update.sha256_file(out) + "  " + os.path.basename(out) + "\n")
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
        os.makedirs(os.path.join(self.root, "etc/pvj"))
        os.makedirs(os.path.join(self.root, "var/lib/pvj"))
        self.updater = Updater(root=self.root, install=self.install, restart=self.restart, health=lambda: self.healthy)
        test.addCleanup(shutil.rmtree, self.root, True)

    def install(self, work, info):
        r = subprocess.run([os.path.join(work, "install", "install.sh"), "--stage", self.root, "--user", "gigbox"],
                           capture_output=True, text=True)
        if r.returncode:
            raise UpdateError(r.stderr)

    def restart(self):
        self.restarts += 1

    def write_settings(self, schema=1, marker="x"):
        with open(os.path.join(self.root, "var/lib/pvj/settings.json"), "w") as f:
            json.dump({"schema": schema, "marker": marker}, f)

    def settings(self):
        with open(os.path.join(self.root, "var/lib/pvj/settings.json")) as f:
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
        info = tarfile.TarInfo("pvj-1.0.0/bin/tool")
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
        info = update.inspect_bundle(tree)
        self.assertEqual(info["version"], "2.3.4")
        self.assertGreaterEqual(info["schema"], 1)
        os.unlink(os.path.join(tree, "bin", "pvj-web"))
        with self.assertRaises(UpdateError):
            update.inspect_bundle(tree)


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
            f.write('%s namespaces="pvj-release" %s %s\n' % (update.PRINCIPAL, fields[0], fields[1]))
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
        self.assertTrue(os.path.isfile(os.path.join(self.env.root, "opt/pvj/releases/1.1.0/bin/pvj-web")))

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
        self.assertFalse(os.path.isdir(os.path.join(self.env.root, "opt/pvj/releases")) and os.listdir(os.path.join(self.env.root, "opt/pvj/releases")))

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
        aside = [n for n in os.listdir(os.path.join(self.env.root, "var/lib/pvj")) if "rolled-back" in n]
        self.assertEqual(len(aside), 1)  # newer settings set aside, not deleted

    def test_rollback_without_previous_is_an_error(self):
        self.apply("1.0.0")
        with self.assertRaises(UpdateError):
            self.u.rollback(log=lambda *_: None)

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
        with open(os.path.join(self.env.root, "etc/pvj/pvj.env"), "w") as f:
            f.write("# panel port\nPVJ_PORT=%d\n" % port)
        real = Updater(root=self.env.root)
        self.assertEqual(real.configured_port(), str(port))
        self.assertTrue(real._http_health(timeout=3))
        with open(os.path.join(self.env.root, "etc/pvj/pvj.env"), "w") as f:
            f.write("PVJ_PORT=1\n")
        self.assertFalse(real._http_health(timeout=1.2))

    @unittest.skipUnless(os.geteuid() == 0, "needs root to set file owners")
    def test_restored_settings_keep_the_panels_owner_and_stay_private(self):
        self.apply("1.0.0")
        self.env.write_settings(marker="v1")
        self.apply("1.1.0")
        path = os.path.join(self.env.root, "var/lib/pvj/settings.json")
        os.chown(path, 23456, 34567)  # as if pvj-web owns it
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
        for label, names in (("A", ["pvj-1.2.0.tar.gz", "notes.txt"]), ("B", ["pvj-1.10.0.tar.gz", "pvj-1.9.0.tar.gz"])):
            os.makedirs(os.path.join(base, label, "pvj-update"))
            for n in names:
                open(os.path.join(base, label, "pvj-update", n), "w").close()
        found = [os.path.basename(p) for p in self.u.usb_bundles(base)]
        self.assertEqual(found, ["pvj-1.10.0.tar.gz", "pvj-1.9.0.tar.gz", "pvj-1.2.0.tar.gz"])

    @unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
    def test_signed_bundle_end_to_end(self):
        d = tempfile.mkdtemp()
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        with open(self.u.allowed_signers(), "w") as f:
            f.write('%s namespaces="pvj-release" %s %s\n' % (update.PRINCIPAL, fields[0], fields[1]))
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


if __name__ == "__main__":
    unittest.main()
