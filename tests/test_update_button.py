# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The update button: the updater's inbox and progress file, pvj-sysd's update command, the API and the units."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from pvj import sysd, update
from pvj.update import UpdateError
from tests.test_server import ServerBase
from tests.test_update import make_bundle

REPO = os.path.join(os.path.dirname(__file__), "..")
HAVE_SSH = shutil.which("ssh-keygen") is not None


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.inbox = os.path.join(self.root, "var/lib/pvj/update-inbox")
        os.makedirs(self.inbox)
        self.u = update.Updater(root=self.root)

    def test_newest_first_exact_names_only_and_cleared_inside_the_folder(self):
        for n in ("pvj-0.2.0.tar.gz", "pvj-0.10.0.tar.gz", "pvj-0.10.0.tar.gz.sig", "notes.txt", "pvj-x.tar.gz", "xpvj-9.9.9.tar.gz"):
            open(os.path.join(self.inbox, n), "w").close()
        fd = self.u.open_inbox()
        try:
            self.assertEqual(self.u.inbox_bundles(fd), ["pvj-0.10.0.tar.gz", "pvj-0.2.0.tar.gz"])
            self.u.clear_inbox(fd)
        finally:
            os.close(fd)
        self.assertEqual(sorted(os.listdir(self.inbox)), ["notes.txt", "xpvj-9.9.9.tar.gz"])

    def test_a_linked_inbox_is_not_followed(self):
        # pvj-web owns /var/lib/pvj: it could swap the folder for a link to a system folder full of pvj-* files.
        victim = tempfile.mkdtemp()
        open(os.path.join(victim, "pvj-web.service"), "w").close()
        os.rmdir(self.inbox)
        os.symlink(victim, self.inbox)
        with self.assertRaises(UpdateError):
            self.u.open_inbox()
        self.assertEqual(os.listdir(victim), ["pvj-web.service"])

    def test_links_devices_and_fifos_are_refused(self):
        os.symlink("/dev/zero", os.path.join(self.inbox, "pvj-1.0.0.tar.gz"))
        os.mkfifo(os.path.join(self.inbox, "pvj-2.0.0.tar.gz"))
        fd = self.u.open_inbox()
        try:
            for name in ("pvj-1.0.0.tar.gz", "pvj-2.0.0.tar.gz"):
                with self.assertRaises(UpdateError):
                    self.u.check(name, allow_unsigned=True, dir_fd=fd)
            with self.assertRaises(UpdateError):
                self.u.check("pvj-3.0.0.tar.gz", dir_fd=fd)      # missing
        finally:
            os.close(fd)

    def test_a_fifo_checksum_does_not_hang(self):
        bundle = make_bundle("1.0.0")
        os.unlink(bundle + ".sha256")
        os.mkfifo(bundle + ".sha256")
        done = []
        t = threading.Thread(target=lambda: done.append(self._try(bundle)), daemon=True)
        t.start()
        t.join(10)
        self.assertTrue(done, "check() blocked on a FIFO")
        self.assertIsInstance(done[0], UpdateError)

    def _try(self, bundle):
        try:
            self.u.check(bundle)
        except UpdateError as e:
            return e

    def test_too_large_or_growing_files_are_refused(self):
        bundle = make_bundle("1.0.0")
        with mock.patch.object(update, "MAX_BUNDLE_BYTES", 10):
            with self.assertRaises(UpdateError):
                self.u.check(bundle, allow_unsigned=True)
        src = open(bundle, "rb")
        try:
            with self.assertRaises(UpdateError):
                update.copy_bounded(src, os.path.join(tempfile.mkdtemp(), "x"), 10)
        finally:
            src.close()

    def test_unsigned_needs_a_checksum(self):
        bundle = make_bundle("1.0.0")
        os.unlink(bundle + ".sha256")
        with self.assertRaises(UpdateError) as e:
            self.u.check(bundle, allow_unsigned=True)
        self.assertIn("SHA-256", str(e.exception))

    @unittest.skipUnless(HAVE_SSH, "ssh-keygen not installed")
    def test_a_signed_bundle_needs_no_checksum_file(self):
        d = tempfile.mkdtemp()
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        os.makedirs(os.path.dirname(self.u.allowed_signers()), exist_ok=True)
        with open(self.u.allowed_signers(), "w") as f:
            f.write('%s namespaces="pvj-release" %s %s\n' % (update.PRINCIPAL, fields[0], fields[1]))
        bundle = make_bundle("1.0.0")
        os.unlink(bundle + ".sha256")
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", update.NAMESPACE, bundle], check=True, capture_output=True)
        info, _tree, scratch = self.u.check(bundle)
        shutil.rmtree(scratch)
        self.assertEqual(info["version"], "1.0.0")
        os.unlink(bundle + ".sig")
        with self.assertRaises(UpdateError):                       # no signature and no checksum: refused
            self.u.check(bundle)


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.result = os.path.join(tempfile.mkdtemp(), "result.json")
        os.environ["PVJ_UPDATE_LOCK"] = os.path.join(tempfile.mkdtemp(), "lock")
        self.addCleanup(os.environ.pop, "PVJ_UPDATE_LOCK", None)
        self.root = mock.patch("os.geteuid", return_value=0)
        self.root.start()
        self.addCleanup(self.root.stop)

    def read(self):
        with open(self.result) as f:
            return json.load(f)

    def fake(self, bundles, apply):
        cleared = []

        class Fake:
            def open_inbox(self):
                return os.open(tempfile.mkdtemp(), os.O_RDONLY)

            def inbox_bundles(self, fd):
                return bundles

            def clear_inbox(self, fd):
                cleared.append(True)

            def apply(self, bundle, log=print, dir_fd=None):
                log("installing")
                return apply(bundle)
        return mock.patch.object(update, "Updater", Fake), cleared

    def test_empty_inbox_reports_failed(self):
        p, cleared = self.fake([], lambda b: "x")
        with p:
            self.assertEqual(update.main(["inbox", "--result", self.result]), 1)
        self.assertEqual(self.read()["state"], "failed")
        self.assertIn("upload it again", self.read()["message"])

    def test_only_the_confirmed_version_is_installed(self):
        got = []
        p, cleared = self.fake(["pvj-2.0.0.tar.gz", "pvj-1.5.0.tar.gz"], lambda b: got.append(b) or "1.5.0")
        with p:
            self.assertEqual(update.main(["inbox", "--version", "1.5.0", "--result", self.result]), 0)
            self.assertEqual(got, ["pvj-1.5.0.tar.gz"])
            self.assertEqual(self.read(), dict(self.read(), state="done", version="1.5.0"))
            self.assertEqual(update.main(["inbox", "--version", "3.0.0", "--result", self.result]), 1)
            self.assertIn("no longer there", self.read()["message"])
            self.assertEqual(update.main(["inbox", "--version", "1.0; rm", "--result", self.result]), 1)
        self.assertEqual(len(cleared), 2)                      # tried once, then emptied (not for a bad version)

    def test_an_unexpected_error_still_ends_running(self):
        def boom(b):
            raise OSError(28, "No space left on device")
        p, _ = self.fake(["pvj-1.0.0.tar.gz"], boom)
        with p:
            self.assertEqual(update.main(["inbox", "--result", self.result]), 1)
        self.assertEqual(self.read()["state"], "failed")
        self.assertIn("No space left", self.read()["message"])

    def test_one_update_at_a_time(self):
        held = update.take_lock()
        try:
            p, _ = self.fake(["pvj-1.0.0.tar.gz"], lambda b: "1.0.0")
            with open(self.result, "w") as f:
                json.dump({"state": "running", "message": "other", "at": int(time.time())}, f)
            with p:
                self.assertEqual(update.main(["inbox", "--result", self.result]), 1)
            self.assertEqual(self.read()["message"], "other")      # the running update's file is left alone
        finally:
            held.close()

    def test_a_rollback_from_a_terminal_replaces_the_last_update_line(self):
        # The Updates card went on saying "Last update: updated to 2.0.0" after `sudo pvj-update rollback`.
        with open(self.result, "w") as f:
            json.dump({"state": "done", "message": "updated to 2.0.0", "version": "2.0.0", "at": 1}, f)

        class Fake:
            def rollback(self):
                return "1.0.0"

            def apply(self, bundle, sha256=None, allow_unsigned=False, force=False):
                return "3.0.0"
        os.environ["PVJ_UPDATE_RESULT"] = self.result
        self.addCleanup(os.environ.pop, "PVJ_UPDATE_RESULT", None)
        with mock.patch.object(update, "Updater", Fake):
            self.assertEqual(update.main(["rollback"]), 0)
            self.assertEqual(self.read(), dict(self.read(), state="done", version="1.0.0", message="rolled back to 1.0.0"))
            self.assertEqual(update.main(["apply", "pvj-3.0.0.tar.gz"]), 0)
            self.assertEqual(self.read(), dict(self.read(), state="done", version="3.0.0", message="updated to 3.0.0"))
            # where the update units never ran there is no folder, and none is made
            os.environ["PVJ_UPDATE_RESULT"] = os.path.join(os.path.dirname(self.result), "missing", "result.json")
            self.assertEqual(update.main(["rollback"]), 0)
            self.assertFalse(os.path.exists(os.path.dirname(os.environ["PVJ_UPDATE_RESULT"])))

    def test_a_failed_rollback_from_a_terminal_is_reported(self):
        class Fake:
            def rollback(self):
                raise UpdateError("there is no previous release to go back to")
        os.environ["PVJ_UPDATE_RESULT"] = self.result
        self.addCleanup(os.environ.pop, "PVJ_UPDATE_RESULT", None)
        with mock.patch.object(update, "Updater", Fake):
            self.assertEqual(update.main(["rollback"]), 1)
        self.assertEqual(self.read()["state"], "failed")

    def test_finish_marks_an_interrupted_update(self):
        with open(self.result, "w") as f:
            json.dump({"state": "running", "message": "installing", "at": 1}, f)
        self.assertEqual(update.main(["finish", "--result", self.result]), 0)
        self.assertEqual(self.read()["state"], "failed")
        with open(self.result, "w") as f:
            json.dump({"state": "done", "message": "updated", "at": 1}, f)
        update.main(["finish", "--result", self.result])
        self.assertEqual(self.read()["state"], "done")


class SysdUpdateTest(unittest.TestCase):
    def service(self, running=""):
        calls = []

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out, ""

        def run(argv, **kw):
            calls.append(argv)
            return R(running if argv[1] == "list-units" else "")
        return sysd.SysService(runner=run, log=lambda *_: None), calls

    def test_starts_a_fixed_template_unit_for_a_checked_version(self):
        s, calls = self.service()
        self.assertTrue(s.handle({"cmd": "update", "source": "inbox", "version": "1.2.3"})["ok"])
        self.assertEqual(calls[-1], ["systemctl", "start", "--no-block", "pvj-update-inbox@1.2.3.service"])
        for source, version in (("../../x", "1.2.3"), ("usb; reboot", "1.2.3"), (None, "1.2.3"), ("usb", None),
                                ("usb", "1.2"), ("usb", "1.2.3 x"), ("usb", "1.2.3\n"), ("usb", 123), ("usb", "-1.2.3")):
            self.assertFalse(s.handle({"cmd": "update", "source": source, "version": version})["ok"], (source, version))

    def test_refuses_a_second_update_while_one_runs(self):
        s, calls = self.service(running="pvj-update-usb@1.0.0.service loaded activating start Update")
        r = s.handle({"cmd": "update", "source": "inbox", "version": "1.0.0"})
        self.assertFalse(r["ok"])
        self.assertIn("already running", r["error"])
        self.assertFalse(any(c[1] == "start" for c in calls))


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.asked = []

        class Sysd:
            def request(inner, message):
                self.asked.append(message)
                return {"ok": True}
        self.api.sysd = Sysd()
        self.result = os.path.join(tempfile.mkdtemp(), "result.json")
        os.environ["PVJ_UPDATE_RESULT"] = self.result
        self.addCleanup(os.environ.pop, "PVJ_UPDATE_RESULT", None)

    def upload(self, name, data, token=None, headers=None):
        return self.call("POST", "/api/system/update/upload?name=" + name, raw=data, token=token or self.full,
                         headers=headers or {"Content-Type": "application/octet-stream"})

    def start(self, body, token=None):
        return self.call("POST", "/api/system/update", body, token=token or self.full)

    def test_upload_status_and_start(self):
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz", b"x" * 1000)[0], 200)
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz.sig", b"sig")[0], 200)
        st, body, _ = self.call("GET", "/api/system/update", token=self.full)
        self.assertEqual(body["inbox"], [{"version": "0.2.0", "signed": True}])
        self.upload("pvj-0.3.0.tar.gz", b"y" * 10)                       # a newer one replaces what was waiting
        self.assertEqual(self.call("GET", "/api/system/update", token=self.full)[1]["inbox"], [{"version": "0.3.0", "signed": False}])
        self.assertEqual(self.start({"source": "inbox", "version": "0.3.0"})[0], 400)
        self.assertEqual(self.start({"source": "web", "version": "0.3.0", "confirm": "update"})[0], 400)
        self.assertEqual(self.start({"source": "inbox", "version": "0.3", "confirm": "update"})[0], 400)
        self.assertEqual(self.start({"source": "inbox", "version": "0.3.0", "confirm": "update"})[0], 200)
        self.assertEqual(self.asked, [{"cmd": "update", "source": "inbox", "version": "0.3.0"}])

    def test_a_done_line_for_another_version_is_not_shown(self):
        # A box rolled back by an older pvj-update (which wrote nothing): "updated to 9.9.9" is no longer true.
        from pvj import __version__
        with open(self.result, "w") as f:
            json.dump({"state": "done", "message": "updated to 9.9.9", "version": "9.9.9", "at": 5}, f)
        self.assertIsNone(self.call("GET", "/api/system/update", token=self.full)[1]["last"])
        for record in ({"state": "done", "message": "rolled back to " + __version__, "version": __version__, "at": 5},
                       {"state": "failed", "message": "signature check failed", "version": None, "at": 5},
                       {"state": "running", "message": "installing 9.9.9", "version": None, "at": 5}):
            with open(self.result, "w") as f:
                json.dump(record, f)
            self.assertEqual(self.call("GET", "/api/system/update", token=self.full)[1]["last"], record)

    def test_usb_bundles_are_listed(self):
        usb = tempfile.mkdtemp()
        os.makedirs(os.path.join(usb, "STICK", "pvj-update"))
        for n in ("pvj-1.1.0.tar.gz", "pvj-1.1.0.tar.gz.sig", "xpvj-9.9.9.tar.gz"):
            open(os.path.join(usb, "STICK", "pvj-update", n), "w").close()
        self.api.usb_root = usb
        body = self.call("GET", "/api/system/update", token=self.full)[1]
        self.assertEqual(body["usb"], [{"drive": "STICK", "version": "1.1.0", "signed": True}])

    def test_nothing_while_an_update_runs(self):
        with open(self.result, "w") as f:
            json.dump({"state": "running", "message": "installing", "at": int(time.time())}, f)
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz", b"x")[0], 409)
        self.assertEqual(self.start({"source": "usb", "version": "0.2.0", "confirm": "update"})[0], 409)
        with open(self.result, "w") as f:                                # stale: the box was not restarted
            json.dump({"state": "running", "message": "installing", "at": int(time.time()) - 3 * 3600}, f)
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz", b"x")[0], 200)

    def test_bad_uploads_and_roles(self):
        for name in ("evil.sh", "pvj-1.0.tar.gz", "../pvj-1.0.0.tar.gz", "pvj-1.0.0.tar.gz.exe", "pvj-1.0.0.tar.gz/x"):
            self.assertEqual(self.upload(name, b"x")[0], 400, name)
        self.assertEqual(self.upload("pvj-1.0.0.tar.gz.sig", b"x" * (70 * 1024))[0], 413)
        self.assertEqual(self.upload("pvj-1.0.0.tar.gz.sha256", b"x" * (70 * 1024))[0], 413)
        for role in ("live", "view"):
            tok = self.call("POST", "/api/devices/invite", {"name": role, "role": role}, token=self.full)[1]["token"]
            self.assertEqual(self.upload("pvj-1.0.0.tar.gz", b"x", token=tok)[0], 403, role)
            self.assertEqual(self.call("GET", "/api/system/update", token=tok)[0], 403, role)
            self.assertEqual(self.start({"source": "usb", "version": "1.0.0", "confirm": "update"}, token=tok)[0], 403, role)
        self.assertEqual(self.asked, [])

    def test_upload_needs_the_request_header(self):
        st, _, _ = self.call("POST", "/api/system/update/upload?name=pvj-1.0.0.tar.gz", raw=b"x", token=self.full,
                             headers={"Content-Type": "application/octet-stream"}, csrf=False)
        self.assertEqual(st, 403)

    def test_not_enough_space(self):
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1)):
            self.assertEqual(self.upload("pvj-1.0.0.tar.gz", b"x" * 100)[0], 507)


class UnitsTest(unittest.TestCase):
    def test_update_units_run_the_updater_on_request_only(self):
        for src in ("usb", "inbox"):
            with open(os.path.join(REPO, "install", "pvj-update-%s@.service" % src)) as f:
                text = f.read()
            self.assertIn("pvj-update %s --version %%i --result /run/pvj-update/result.json" % src, text)
            self.assertIn("ExecStopPost=-@PVJ_DIR@/bin/pvj-update finish --result /run/pvj-update/result.json", text)
            self.assertNotIn("\n[Install]", text)                      # never started at boot
            self.assertIn("RuntimeDirectoryPreserve=yes", text)        # the panel reads the outcome after the restart
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.assertIn("pvj-update-inbox@.service", f.read())


if __name__ == "__main__":
    unittest.main()
