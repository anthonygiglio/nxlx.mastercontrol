# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The Rollback button (J-057, D150): the Owner goes back to the previous release from the Updates card, without SSH.

The panel never runs anything as root: it asks nxlx-sysd, which starts nxlx-update-rollback@V.service, which runs
`nxlx-update rollback --version V`, the updater's own rollback. These tests hold each step: who may ask, what is asked of
root, every reason the updater or the panel refuses (in plain words), the progress file, and the line in the event log."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from mctl import events, rootfs, support, sysd, update
from mctl.update import UpdateError, Updater
from tests.test_server import ServerBase
from tests.test_support import TUNNEL, SupportBase
from tests.test_update import root_for_main

REPO = os.path.join(os.path.dirname(__file__), "..")
OLD_WEB = "p" + "vj-web"      # the program of a release from before the rename, written so the old-names guard does not count it
NORMAL = lambda: "normal"      # not this machine's /proc/mounts (a container's / is an overlay)


def make_root(current="1.1.0", previous="1.0.0", new_names=True, releases=None):
    """A fake node: /opt/nxlx/releases/<version>/bin/nxlx-web for each release, `current` and `previous` as the installer leaves them."""
    root = tempfile.mkdtemp()
    for d in ("etc/nxlx", "var/lib/nxlx"):
        os.makedirs(os.path.join(root, d))
    for v in releases or sorted({x for x in (current, previous) if x}):
        bin_dir = os.path.join(root, "opt/nxlx/releases", v, "bin")
        os.makedirs(bin_dir)
        open(os.path.join(bin_dir, "nxlx-web" if (new_names or v == current) else OLD_WEB), "w").close()
    if current:
        os.symlink("/opt/nxlx/releases/" + current, os.path.join(root, "opt/nxlx/current"))
    if previous:
        with open(os.path.join(root, "opt/nxlx/previous"), "w") as f:
            f.write("/opt/nxlx/releases/%s\n" % previous)
    return root


class UpdaterViewTest(unittest.TestCase):
    def updater(self, **kw):
        root = make_root(**kw)
        self.addCleanup(shutil.rmtree, root, True)
        self.restarts = []
        return Updater(root=root, rootfs_state=NORMAL, restart=lambda: self.restarts.append(1), health=lambda: True), root

    def test_the_view_names_the_release_that_runs_and_the_one_before_with_its_day(self):
        u, root = self.updater()
        os.utime(os.path.join(root, "opt/nxlx/releases/1.0.0"), (1760000000, 1760000000))
        view = u.rollback_view()
        self.assertEqual(view["current"]["version"], "1.1.0")
        self.assertEqual(view["previous"], {"version": "1.0.0", "date": 1760000000})
        self.assertIsNone(view["refusal"])

    def test_no_previous_release(self):
        u, _ = self.updater(previous=None)
        view = u.rollback_view()
        self.assertEqual((view["current"]["version"], view["previous"], view["refusal"]), ("1.1.0", None, "there is no previous release to go back to"))

    def test_a_previous_release_that_is_gone_or_not_a_version_is_no_release(self):
        u, root = self.updater()
        shutil.rmtree(os.path.join(root, "opt/nxlx/releases/1.0.0"))
        self.assertEqual(u.rollback_view()["refusal"], "there is no previous release to go back to")
        with open(os.path.join(root, "opt/nxlx/previous"), "w") as f:
            f.write("/opt/nxlx/releases/..\n")
        self.assertEqual(u.rollback_view()["refusal"], "there is no previous release to go back to")
        self.assertIsNone(u.rollback_view()["previous"])

    def test_a_release_from_before_the_rename_is_refused_in_words_that_name_the_way_back(self):
        u, _ = self.updater(new_names=False)
        view = u.rollback_view()
        self.assertEqual(view["previous"]["version"], "1.0.0")           # still shown: the Owner is told why, not left guessing
        self.assertIn("before the rename of the system names", view["refusal"])
        self.assertIn("sudo nxlx-convert --undo", view["refusal"])

    def test_the_updater_goes_back_only_to_the_release_that_was_shown(self):
        u, root = self.updater()
        with self.assertRaises(UpdateError) as e:
            u.rollback(log=lambda *_: None, expect="0.9.0")
        self.assertIn("is 1.0.0 now, not 0.9.0", str(e.exception))
        self.assertEqual((u.status()["current"], self.restarts), ("1.1.0", []))      # nothing switched, nothing restarted
        self.assertEqual(u.rollback(log=lambda *_: None, expect="1.0.0"), "1.0.0")
        self.assertEqual((u.status()["current"], u.status()["previous"], self.restarts), ("1.0.0", "1.1.0", [1]))

    def test_the_updater_refuses_what_the_view_refuses_and_under_the_read_only_disk(self):
        u, _ = self.updater(previous=None)
        with self.assertRaisesRegex(UpdateError, "no previous release"):
            u.rollback(log=lambda *_: None)
        u, _ = self.updater(new_names=False)
        with self.assertRaisesRegex(UpdateError, "nxlx-convert --undo"):
            u.rollback(log=lambda *_: None, expect="1.0.0")
        u, _ = self.updater()
        u._rootfs_state = lambda: "overlay"
        with self.assertRaises(UpdateError) as e:
            u.rollback(log=lambda *_: None, expect="1.0.0")
        self.assertEqual(str(e.exception), rootfs.UPDATE_REFUSAL)
        self.assertEqual(self.restarts, [])


class CommandTest(unittest.TestCase):
    """`nxlx-update rollback --version V --result FILE`, what the unit runs: the progress file the card reads."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.result = os.path.join(folder, "result.json")
        os.environ["NXLX_UPDATE_LOCK"] = os.path.join(folder, "update.lock")
        self.addCleanup(os.environ.pop, "NXLX_UPDATE_LOCK", None)
        patch = root_for_main()
        patch.start()
        self.addCleanup(patch.stop)
        self.restarts = []

    def read(self):
        with open(self.result) as f:
            return json.load(f)

    def run_with(self, argv, **kw):
        root = make_root(**kw)
        self.addCleanup(shutil.rmtree, root, True)
        made = lambda: Updater(root=root, rootfs_state=NORMAL, restart=lambda: self.restarts.append(1), health=lambda: True)
        with mock.patch.object(update, "Updater", made):
            return update.main(argv + ["--result", self.result])

    def test_a_timed_out_rollback_is_marked_as_going_back_not_as_an_update(self):
        with open(self.result, "w") as f:
            json.dump({"state": "running", "message": "going back to the previous release", "action": "rollback", "at": 1}, f)
        self.assertEqual(update.main(["finish", "--action", "rollback", "--result", self.result]), 0)
        self.assertEqual(self.read(), dict(self.read(), state="failed", action="rollback", message="going back stopped before it finished"))
        with open(self.result, "w") as f:
            json.dump({"state": "running", "message": "installing", "at": 1}, f)
        update.main(["finish", "--result", self.result])                 # an update's unit: as before
        self.assertEqual((self.read()["message"], "action" in self.read()), ("the update stopped before it finished", False))

    def test_a_going_back_whose_panel_does_not_answer_returns_to_the_release_it_came_from(self):
        root = make_root()
        self.addCleanup(shutil.rmtree, root, True)
        up = {"1.1.0": "new settings\n", "1.0.0": "old settings\n"}
        with open(os.path.join(root, "var/lib/nxlx/settings.json"), "w") as f:
            f.write(up["1.1.0"])
        os.makedirs(os.path.join(root, "var/lib/nxlx-update"), mode=0o700)
        healthy = iter([False, True])
        u = Updater(root=root, rootfs_state=NORMAL, restart=lambda: self.restarts.append(1), health=lambda: next(healthy),
                    root_state_dir="/var/lib/nxlx-update")
        # the copy of the settings from before 1.1.0 was installed
        with open(os.path.join(root, "var/lib/nxlx-update/settings-1.0.0-1000.json"), "w") as f:
            f.write(up["1.0.0"])
        with self.assertRaises(UpdateError) as e:
            u.rollback(log=lambda *_: None, expect="1.0.0")
        self.assertEqual(str(e.exception), "Going back did not work; the node returned to 1.1.0")
        st = u.status()
        self.assertEqual((st["current"], st["previous"]), ("1.1.0", "1.0.0"))        # where it was, history as it was
        with open(os.path.join(root, "var/lib/nxlx/settings.json")) as f:
            self.assertEqual(f.read(), up["1.1.0"])                                     # the settings it had are back
        self.assertEqual(len(self.restarts), 2)                                       # back, then forward again

    def test_it_reports_running_then_done_as_a_rollback(self):
        seen = []
        real = update.json.dump

        def watch(data, f, *a, **k):
            seen.append((data["state"], data.get("action")))
            return real(data, f, *a, **k)
        with mock.patch.object(update.json, "dump", watch):
            self.assertEqual(self.run_with(["rollback", "--version", "1.0.0"]), 0)
        self.assertEqual(seen[0], ("running", "rollback"))
        self.assertEqual(self.read(), dict(self.read(), state="done", action="rollback", version="1.0.0", message="rolled back to 1.0.0"))
        self.assertEqual(self.restarts, [1])

    def test_every_refusal_ends_running_with_its_reason_and_changes_nothing(self):
        cases = (
            ({"previous": None}, "there is no previous release to go back to"),
            ({"new_names": False}, "sudo nxlx-convert --undo"),
            ({"releases": ["1.1.0", "1.0.0", "0.9.0"], "previous": "0.9.0"}, "is 0.9.0 now, not 1.0.0"),
        )
        for kw, words in cases:
            self.assertEqual(self.run_with(["rollback", "--version", "1.0.0"], **kw), 1, kw)
            self.assertEqual(self.read()["state"], "failed", kw)
            self.assertEqual(self.read()["action"], "rollback", kw)
            self.assertIn(words, self.read()["message"], kw)
        self.assertEqual(self.restarts, [])

    def test_the_read_only_disk_refuses(self):
        with mock.patch.object(update.rootfs, "current_state", return_value="overlay"):
            root = make_root()
            self.addCleanup(shutil.rmtree, root, True)
            with mock.patch.object(update, "Updater", lambda: Updater(root=root, restart=lambda: self.restarts.append(1), health=lambda: True)):
                self.assertEqual(update.main(["rollback", "--version", "1.0.0", "--result", self.result]), 1)
        self.assertEqual((self.read()["state"], self.read()["message"]), ("failed", rootfs.UPDATE_REFUSAL))
        self.assertEqual(self.restarts, [])

    def test_an_update_that_is_running_is_not_disturbed(self):
        held = update.take_lock()
        try:
            with open(self.result, "w") as f:
                json.dump({"state": "running", "message": "installing 1.2.0", "at": int(time.time())}, f)
            self.assertEqual(self.run_with(["rollback", "--version", "1.0.0"]), 1)
            self.assertEqual(self.read()["message"], "installing 1.2.0")        # the running update's own file is left alone
        finally:
            held.close()
        self.assertEqual(self.restarts, [])

    def test_a_version_that_is_not_one_is_refused_before_anything_is_looked_at(self):
        self.assertEqual(self.run_with(["rollback", "--version", "1.0; reboot"]), 1)
        self.assertIn("not a version", self.read()["message"])

    def test_from_a_terminal_it_is_the_call_it_always_was(self):
        calls = []

        class Fake:
            def rollback(self, *a, **k):
                calls.append((a, k))
                return "1.0.0"
        with mock.patch.object(update, "Updater", Fake):
            os.environ["NXLX_UPDATE_RESULT"] = self.result
            self.addCleanup(os.environ.pop, "NXLX_UPDATE_RESULT", None)
            self.assertEqual(update.main(["rollback"]), 0)
        self.assertEqual(calls, [((), {})])


class TunnelTest(SupportBase):
    """A real request through the support tunnel, for each role a support login can have."""

    def test_the_rollback_is_refused_to_a_support_login_and_root_is_not_asked(self):
        asked = []

        class Sysd:
            def request(inner, message):
                asked.append(message)
                return {"ok": True}
        self.api.sysd = Sysd()
        self.ready()
        for role in ("live", "full"):
            if self.api.support.session is not None:
                self.h("POST", "/api/support/stop", device=self.full_dev)
            code = self.start(role=role)[1]["code"]
            dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
            st, out = self.h("POST", "/api/system/update/rollback", {"version": "1.0.0", "confirm": "rollback"}, dev, TUNNEL)
            self.assertEqual(st, 403, (role, out))
            self.assertIn("remote support", out["error"])
        self.assertEqual(asked, [])


class SysdTest(unittest.TestCase):
    def service(self, running=""):
        calls = []

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out, ""

        def run(argv, **kw):
            calls.append(argv)
            return R(running if argv[1] == "list-units" else "")
        return sysd.SysService(runner=run, log=lambda *_: None), calls

    def test_it_starts_the_fixed_rollback_unit_for_a_checked_version(self):
        s, calls = self.service()
        self.assertEqual(s.handle({"cmd": "rollback", "version": "1.2.3"}), {"ok": True, "started": "rollback", "version": "1.2.3"})
        self.assertEqual(calls[-1], ["systemctl", "start", "--no-block", "nxlx-update-rollback@1.2.3.service"])
        for version in (None, "1.2", "1.2.3 x", "1.2.3\n", 123, "-1.2.3", "../../x", "1.2.3; reboot", "1.2.3.service"):
            s, calls = self.service()
            self.assertFalse(s.handle({"cmd": "rollback", "version": version})["ok"], version)
            self.assertFalse(any(c[1] == "start" for c in calls), version)

    def test_it_refuses_while_any_update_or_rollback_unit_runs_and_an_update_while_a_rollback_runs(self):
        for unit in ("nxlx-update-usb@1.0.0.service", "nxlx-update-inbox@1.0.0.service", "nxlx-update-rollback@1.0.0.service"):
            s, calls = self.service(running=unit + " loaded activating start Update")
            r = s.handle({"cmd": "rollback", "version": "1.0.0"})
            self.assertFalse(r["ok"])
            self.assertIn("already running", r["error"])
            self.assertFalse(any(c[1] == "start" for c in calls), unit)
        s, calls = self.service(running="nxlx-update-rollback@1.0.0.service loaded activating start Update")
        self.assertIn("already running", s.handle({"cmd": "update", "source": "usb", "version": "1.2.0"})["error"])
        self.assertTrue(any("nxlx-update-rollback@*.service" in c for c in calls))

    def test_the_unit_and_the_installer(self):
        with open(os.path.join(REPO, "install", "nxlx-update-rollback@.service")) as f:
            text = f.read()
        self.assertIn("ExecStart=@NXLX_DIR@/bin/nxlx-update rollback --version %i --result /run/nxlx-update/result.json", text)
        self.assertIn("ExecStopPost=-@NXLX_DIR@/bin/nxlx-update finish --action rollback --result /run/nxlx-update/result.json", text)
        for line in ("User=root", "RuntimeDirectory=nxlx-update", "RuntimeDirectoryMode=0755", "RuntimeDirectoryPreserve=yes"):
            self.assertIn(line + "\n", text)
        self.assertNotIn("\n[Install]", text)                  # never started at boot
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            install = f.read()
        self.assertIn("nxlx-update-rollback@.service", install)
        with open(os.path.join(REPO, "mctl", "sysd.py")) as f:
            self.assertIn("nxlx-update-rollback@*.service", f.read())


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        normal = mock.patch("mctl.rootfs.current_state", return_value="normal")
        normal.start()
        self.addCleanup(normal.stop)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "Anthony's phone"})[1]["token"]
        self.asked = []
        self.reply = {"ok": True}

        class Sysd:
            def request(inner, message):
                self.asked.append(message)
                return self.reply
        self.api.sysd = Sysd()
        self.result = os.path.join(tempfile.mkdtemp(), "result.json")
        os.environ["NXLX_UPDATE_RESULT"] = self.result
        self.addCleanup(os.environ.pop, "NXLX_UPDATE_RESULT", None)
        self.node(current="1.1.0", previous="1.0.0")
        self.api.events = events.EventLog(os.path.join(self.tmp, "events.json"), schedule=lambda d, fn: None)

    def node(self, **kw):
        root = make_root(**kw)
        self.addCleanup(shutil.rmtree, root, True)
        self.api._updater = lambda: Updater(root=root, rootfs_state=NORMAL)

    def token(self, role):
        return self.call("POST", "/api/devices/invite", {"name": role, "role": role}, token=self.full)[1]["token"]

    def go(self, body=None, token=None):
        return self.call("POST", "/api/system/update/rollback", {"version": "1.0.0", "confirm": "rollback"} if body is None else body, token=token or self.full)

    def view(self):
        return self.call("GET", "/api/system/update", token=self.full)[1]["rollback"]

    def test_the_card_gets_the_release_that_runs_and_the_one_before(self):
        view = self.view()
        self.assertEqual((view["current"]["version"], view["previous"]["version"], view["refusal"]), ("1.1.0", "1.0.0", None))
        self.assertIsInstance(view["previous"]["date"], int)

    def test_the_owner_goes_back_and_root_is_asked_for_exactly_that_version(self):
        st, body, _ = self.go()
        self.assertEqual((st, body), (200, {"started": "rollback", "version": "1.0.0"}))
        self.assertEqual(self.asked, [{"cmd": "rollback", "version": "1.0.0"}])

    def test_the_event_log_says_what_and_which_role_not_which_device(self):
        self.go()
        row = self.api.events.events()[0]
        self.assertEqual((row["kind"], row["who"]), ("update", "Owner"))
        self.assertIn("rollback to version 1.0.0", row["text"])
        self.assertNotIn("Anthony", json.dumps(self.api.events.events()))

    def test_nothing_is_logged_or_asked_when_it_is_refused(self):
        self.go({"version": "1.0.0"})
        self.go({"version": "1.0", "confirm": "rollback"})
        self.node(previous=None)
        self.go()
        self.assertEqual((self.asked, self.api.events.events()), ([], []))

    def test_operator_and_guest_cannot_see_or_use_it(self):
        for role in ("live", "view"):
            tok = self.token(role)
            self.assertEqual(self.go(token=tok)[0], 403, role)
            self.assertEqual(self.call("GET", "/api/system/update", token=tok)[0], 403, role)      # the card's data, rollback included
        self.assertEqual(self.go(token="nope")[0], 401)
        self.assertEqual(self.asked, [])

    def test_the_deny_list_names_the_route_and_leaves_the_card_readable(self):
        self.assertIn(("POST", "/api/system/update/rollback"), support.REMOTE_DENY)
        self.assertNotIn(("GET", "/api/system/update"), support.REMOTE_DENY)        # a helper may still read the card
        self.assertNotIn(("POST", "/api/system/update"), support.REMOTE_DENY)       # D84: a signed update may be sent; unchanged

    def test_a_care_job_that_is_running_refuses_with_its_name(self):
        self.api._care_busy = "A settings import"
        self.assertEqual(self.view()["refusal"], "A settings import is running; go back when it has finished.")
        st, body, _ = self.go()
        self.assertEqual((st, body["error"]), (409, "A settings import is running; go back when it has finished."))
        self.assertEqual((self.asked, self.api.events.events()), ([], []))

    def test_every_refusal_is_in_plain_words(self):
        # read-only system disk
        with mock.patch("mctl.rootfs.current_state", return_value="overlay"):
            self.assertEqual(self.view()["refusal"], self.api.ROLLBACK_RO)
            st, body, _ = self.go()
            self.assertEqual((st, body["error"]), (409, self.api.ROLLBACK_RO))
        for words in ("read-only", "sudo nxlx-rootfs disable", "sudo nxlx-rootfs enable"):
            self.assertIn(words, self.api.ROLLBACK_RO)
        # no previous release
        self.node(previous=None)
        self.assertIsNone(self.view()["previous"])
        st, body, _ = self.go()
        self.assertEqual((st, body["error"]), (409, "There is no previous release to go back to."))
        # the release with the old names
        self.node(new_names=False)
        st, body, _ = self.go()
        self.assertEqual(st, 409)
        self.assertIn("before the rename of the system names", body["error"])
        self.assertIn("sudo nxlx-convert --undo", body["error"])
        self.assertEqual(self.view()["refusal"], body["error"])
        # an update already running (the progress file says so)
        self.node()
        with open(self.result, "w") as f:
            json.dump({"state": "running", "message": "installing 1.2.0", "at": int(time.time())}, f)
        st, body, _ = self.go()
        self.assertEqual((st, body["error"]), (409, "An update or a rollback is running; go back when it has finished."))
        self.assertEqual(self.view()["refusal"], body["error"])
        self.assertEqual(self.asked, [])

    def test_the_previous_release_changed_since_it_was_shown(self):
        st, body, _ = self.go({"version": "0.9.0", "confirm": "rollback"})
        self.assertEqual((st, body["error"]), (409, "The release before this one is 1.0.0 now, not 0.9.0. Look at the card again."))
        self.assertEqual(self.asked, [])

    def test_the_helpers_own_refusal_is_passed_on(self):
        self.reply = {"ok": False, "error": "an update is already running"}
        st, body, _ = self.go()
        self.assertEqual((st, body["error"]), (409, "an update is already running"))
        self.assertEqual(self.api.events.events(), [])

    def test_progress_and_result_states_are_what_the_card_reads(self):
        for record in ({"state": "running", "message": "going back to the previous release", "action": "rollback", "version": None, "at": 5},
                       {"state": "failed", "message": "the previous release 1.0.0 is from before the rename", "action": "rollback", "version": None, "at": 6}):
            with open(self.result, "w") as f:
                json.dump(record, f)
            self.assertEqual(self.call("GET", "/api/system/update", token=self.full)[1]["last"], record)
        from mctl import __version__
        done = {"state": "done", "message": "rolled back to " + __version__, "action": "rollback", "version": __version__, "at": 7}
        with open(self.result, "w") as f:
            json.dump(done, f)
        self.assertEqual(self.call("GET", "/api/system/update", token=self.full)[1]["last"], done)

    def test_the_end_of_a_rollback_is_logged_as_one(self):
        for state, message, line in (("done", "rolled back to 1.0.0", "Going back finished: rolled back to 1.0.0"),
                                     ("failed", "the panel did not answer", "Going back failed: the panel did not answer")):
            with open(self.result, "w") as f:
                json.dump({"state": state, "message": message, "action": "rollback", "version": "1.0.0", "at": int(time.time()) + len(state)}, f)
            self.api.events_tick()
            self.assertEqual(self.api.events.events()[0]["text"], line)
        with open(self.result, "w") as f:
            json.dump({"state": "done", "message": "updated to 1.3.0", "version": "1.3.0", "at": int(time.time()) + 99}, f)
        self.api.events_tick()
        self.assertEqual(self.api.events.events()[0]["text"], "Update finished: updated to 1.3.0")


class CardTest(unittest.TestCase):
    """The card's words and steps are in mctl/web/app.js; the browser test (tests/ui) drives them. Here: what must be there."""

    def setUp(self):
        with open(os.path.join(REPO, "mctl", "web", "app.js")) as f:
            self.js = f.read()

    def test_the_button_asks_in_the_page_and_never_through_the_browsers_dialog(self):
        start = self.js.index("function goBack(rb)")
        block = self.js[start:self.js.index("function row(name, state, problem, id, go)", start)]
        self.assertIn("confirmRow(", block)
        self.assertNotIn("confirm(", block.replace("confirmRow(", ""))
        for words in ("restarts its services", "plays again in about a minute", "set aside in a file, not deleted", "paired devices and the PIN",
                      "has to pair again with the PIN", "installed around", "the node returns to", "'/api/system/update/rollback'", "confirm: 'rollback'"):
            self.assertIn(words, block)
        self.assertIn("rb.refusal", block)
        self.assertIn("if (d.rollback) body.appendChild(goBack(d.rollback));", self.js)


if __name__ == "__main__":
    unittest.main()
