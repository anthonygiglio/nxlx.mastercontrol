# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The critical review of #94 at 8cfde96 (2026-10-11), one test for each finding."""
import os
import re
import shutil
import tempfile
import time
import unittest

from pvj import ndi, ndiscovery as D, ndisetup
from tests.test_ndi import frame, wait
from tests.test_ndi_discovery import FAKE, elf
from tests.test_ndi_pads import HOST, NAME, REPO, RID, PadBase
from tests.test_roles import CONTROLLER_DEVICES


def read(*parts):
    with open(os.path.join(REPO, *parts)) as f:
        return f.read()


class UpdateOverAnOlderSetUp(unittest.TestCase):
    """Medium 1: an update of a box that had NDI set up by an earlier release wrote the tmpfiles lines and never made
    the folders, then started a helper whose unit named a folder that was not there: no NDI until a reboot."""

    def setUp(self):
        self.sh = read("install", "install.sh")

    def test_the_installer_makes_the_folders_right_after_it_wrote_their_lines_and_before_any_restart(self):
        refresh = self.sh.index('"$RELEASE/bin/pvj-ndi-runtime" refresh --root "$ROOT" --prefix "$PREFIX"')
        make = 'systemd-tmpfiles --create "$NDI_TMPFILES"'
        self.assertEqual(self.sh.count(make), 1)
        at = self.sh.index(make)
        self.assertGreater(at, refresh, "the folders are made after their lines were written")
        self.assertLess(at, self.sh.index("systemctl restart pvj-ndi.service"), "and before the helper is started")
        self.assertLess(at, self.sh.index("systemctl restart pvj-player.service pvj-web.service"), "and before the panel is, whose drop-in names one of them")
        line = [l for l in self.sh.splitlines() if make in l][0]
        block = self.sh[self.sh.rindex("\nif ndi_mark; then", 0, at):at]
        self.assertNotIn("\nfi\n", block, "only behind the mark")
        self.assertIn('if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -f "$NDI_TMPFILES" ]; then', line, "only on a real install")
        self.assertIn("|| log", line, "a failure is said and does not end the install")

    def test_a_folder_that_tmpfiles_makes_is_never_a_reason_for_a_unit_not_to_start(self):
        made = [l.split()[1] for l in ndisetup.TMPFILES_TEXT.splitlines() if l.startswith("d ")]
        self.assertEqual(made, ["/run/pvj-ndi-pipe", "/run/pvj-ndi-config"])
        for name, text in (("pvj-ndi.service", read("pvj", "systemd", "pvj-ndi.service")), ("the panel's drop-in", ndisetup.WEB_TEXT),
                           ("the player's drop-in", ndisetup.PLAYER_TEXT), ("pvj-ndi-discovery.service", read("pvj", "systemd", "pvj-ndi-discovery.service"))):
            for line in text.splitlines():
                if line.startswith(("ReadWritePaths=", "ReadOnlyPaths=", "BindPaths=", "BindReadOnlyPaths=")):
                    for word in line.split("=", 1)[1].split():
                        if word.lstrip("-+") in made:
                            self.assertTrue(word.startswith("-"), "%s: %s names a folder that may not be there yet, without the dash" % (name, word))
        self.assertIn("ReadWritePaths=/run/pvj-ndi -/run/pvj-ndi-pipe\n", read("pvj", "systemd", "pvj-ndi.service"))

    def test_the_set_up_commands_make_the_folders_themselves(self):
        ran = []
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        ndisetup.enable(run=lambda c: ran.append(c) or 0, say=lambda m: None, systemd=lambda: True, account=lambda **k: None, which=lambda n: None,
                        root=root, check=lambda r: None, sleep=lambda s: None, overlay=lambda: False)
        self.assertIn(["systemd-tmpfiles", "--create", ndisetup.TMPFILES_PATH], ran)
        self.assertLess(ran.index(["systemd-tmpfiles", "--create", ndisetup.TMPFILES_PATH]), ran.index(["systemctl", "restart", ndisetup.UNIT]))
        self.assertNotIn("tmpfiles", D.WEB_TEXT + read("pvj", "systemd", "pvj-ndi-discovery.service").split("[Unit]")[1])      # the discovery server needs none: its only folder is its RuntimeDirectory

    def test_the_real_install_job_updates_over_a_box_whose_folders_were_never_made(self):
        sh = read("tests", "real_install_test.sh")
        step = sh[sh.index('step "an update over a box that an earlier release set up'):]
        step = step[:step.index("\nstep ", 10)]
        for want in ("rm -rf /run/pvj-ndi-pipe /run/pvj-ndi-config", "rm -f /etc/tmpfiles.d/pvj-ndi.conf", "\ninstaller\n",
                     'is /run/pvj-ndi-pipe "root:pvj-ndi 750 directory"', 'is /run/pvj-ndi-pipe/ndi.fifo "root:pvj-ndi 660 fifo"',
                     'is /run/pvj-ndi-config "pvj-web:pvj-ndi 750 directory"', "systemctl is-active --quiet pvj-ndi.service"):
            self.assertIn(want, step, want)


class Crowd(PadBase):
    """Medium 2: the helper's list is cut to 64 rows. Allowed sources were not kept in it, so a few hundred announced
    names pushed every pad's source out and every press said "not on the network now"."""

    def test_five_hundred_announced_names_do_not_push_a_pads_source_out_of_the_list(self):
        self.assertEqual(self.give()[0], 200)
        crowd = [("AAA %03d (x)" % n, "192.168.9.%d:5961" % (n % 250 + 1)) for n in range(500)]
        self.announce(*(crowd + [(NAME, HOST + ":5961")]))
        out = self.h("GET", "/api/ndi", {}, self.operator)[1]
        self.assertEqual((len(out["sources"]), out["cut"]), (ndi.MAX_SOURCES, True))
        self.assertIn(RID, [s["id"] for s in out["sources"]], "the pad's source was pushed out of the list")
        self.lib.frames.put(frame(64, 16))
        st, body = self.press(self.operator)
        self.assertEqual((st, body.get("name")), (200, NAME), body)

    def test_the_helper_is_told_the_allowed_ids_and_nothing_else_of_them(self):
        self.give()
        sent = [m for m in self.client.sent if m.get("cmd") == "configure"][-1]
        self.assertEqual(sent["allowed"], [RID])
        self.assertEqual(self.service.status()["allowed"], [RID])
        self.h("POST", "/api/ndi", {"action": "forget", "id": RID}, self.owner)
        self.assertEqual([m for m in self.client.sent if m.get("cmd") == "configure"][-1]["allowed"], [])
        for bad in (["x"], [RID] * 2, [RID.upper()], "x", [7], [RID[:-1] + "g"], ["%012x" % n for n in range(ndi.MAX_ALLOWED + 1)]):
            self.assertEqual(self.service.handle({"cmd": "configure", "on": True, "addresses": [], "allowed": bad})["ok"], False, bad)

    def test_the_source_on_the_screen_and_the_owners_addresses_still_come_first(self):
        raw = [(("Z %03d" % n).encode(), ("10.1.%d.%d:1" % (n // 250, n % 250 + 1)).encode()) for n in range(300)]
        keep, allowed, owner = ndi.source_id("Z 299", "10.1.1.50"), ndi.source_id("Z 298", "10.1.1.49"), "10.1.1.48"
        rows, cut = ndi.clean_sources(raw, [owner], {keep, allowed})
        ids = [r["id"] for r in rows]
        self.assertEqual((len(rows), cut, keep in ids, allowed in ids, ndi.source_id("Z 297", owner) in ids), (ndi.MAX_SOURCES, True, True, True, True))
        self.assertIn(keep, [r["id"] for r in ndi.clean_sources(raw, [], keep)[0]], "one id as text is still taken")


class GuestSeesNoAddress(PadBase):
    """Low a: the last refusal of a pad's source rides on /api/status, which a Guest reads, and can name addresses."""

    def test_a_guest_is_not_told_why_a_pads_source_was_refused(self):
        self.give()
        self.announce((NAME, "192.168.0.66:5961"))
        st, out = self.press(self.operator)
        self.assertIn("192.168.0.66", out["error"])
        for who, dev, told in (("operator", self.operator, True), ("owner", self.owner, True), ("guest", self.guest, False), ("midi", CONTROLLER_DEVICES["midi"], True)):
            pl = self.h("GET", "/api/status", {}, dev)[1]["player"]
            self.assertEqual("ndi_refused" in pl, told, who)
        self.assertNotIn("192.168", str(self.h("GET", "/api/status", {}, self.guest)[1]))
        self.assertNotIn(HOST, str(self.h("GET", "/api/pads", {}, self.guest)[1]))


class RollbackRunsInCi(unittest.TestCase):
    """Low b: the rollback tests read master's code from git and skipped where the checkout had no origin/master."""

    def test_the_python_job_fetches_master(self):
        y = read(".github", "workflows", "pvj.yml")
        job = y[y.index("python: ['3.9', '3.12']"):]
        job = job[:job.index("\n  ", job.index("run: python"))] if "run: python" in job else job
        self.assertRegex(job, r"git fetch --depth=1 origin \+?master:refs/remotes/origin/master")


class WholeGroup(unittest.TestCase):
    """Low c: stopping the server ended the program and left whatever the program had started."""

    def test_stopping_ends_what_the_program_started_too(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile, program = os.path.join(tmp, "child.pid"), os.path.join(tmp, D.SERVER_NAME)
        with open(program, "w") as f:
            f.write(FAKE % (os.path.join(tmp, "args"), "sleep 300 &\necho $! > '%s'\nwait" % pidfile))
        os.chmod(program, 0o755)
        s = D.Service(program, log=lambda m: None, problem=lambda p: None)
        self.addCleanup(s.close)
        s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})
        self.assertTrue(wait(lambda: os.path.exists(pidfile) and open(pidfile).read().strip()))
        pid = int(open(pidfile).read())
        os.kill(pid, 0)                                      # it runs
        self.assertNotEqual(os.getpgid(pid), os.getpgid(0), "the program runs in the supervisor's own process group")
        s.handle({"cmd": "configure", "on": False})

        def gone():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            try:
                return os.waitpid(pid, os.WNOHANG) != (0, 0)
            except ChildProcessError:
                try:                                         # not our child: gone, or a zombie waiting for its parent
                    return open("/proc/%d/stat" % pid).read().split()[2] == "Z"
                except (FileNotFoundError, ProcessLookupError):
                    return True                              # it went between the two looks
        self.assertTrue(wait(gone, 5), "what the program started is still running")


class ConfigurationBesideTheProgram(unittest.TestCase):
    """Low d: NDI's program also reads ndi-discovery-service.v1.json lying beside it. The box never writes one, so a
    file there is somebody else's: the program is not started with it, and not installed over it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dest = os.path.join(self.tmp, "opt")
        self.src = os.path.join(self.tmp, "prog")
        with open(self.src, "wb") as f:
            f.write(elf())

    def test_a_configuration_file_beside_the_program_refuses_the_start_and_the_install(self):
        placed = D.install_server(self.src, dest_dir=self.dest, machine=183, chown=False)
        mine = (os.getuid(),)
        self.assertIsNone(D.server_problem(placed, mine, 183))
        for name in (D.CONFIG_BESIDE, "anything-else"):
            path = os.path.join(self.dest, name)
            with open(path, "w") as f:
                f.write("{}")
            problem = D.server_problem(placed, mine, 183)
            self.assertIn("beside NDI's program", problem or "", name)
            self.assertIn(name, problem)
            with self.assertRaises(ndi.NdiError, msg=name) as c:
                D.install_server(self.src, dest_dir=self.dest, machine=183, chown=False)
            self.assertIn(name, str(c.exception))
            os.unlink(path)
        self.assertEqual(D.CONFIG_BESIDE, "ndi-discovery-service.v1.json")
        self.assertEqual(D.install_server(self.src, dest_dir=self.dest, machine=183, chown=False), placed)
        s = D.Service(placed, log=lambda m: None, problem=lambda p: "a file lies beside NDI's program")
        self.assertEqual(s.handle({"cmd": "configure", "on": True, "bind": "192.168.1.9", "port": 5959})["state"], "missing")
        self.assertIsNone(s.child)

    def test_the_docs_say_what_a_pad_follows_and_where_the_program_reads_from(self):
        for doc in (read("pvj", "NDI.md"), read("SECURITY.md")):
            self.assertIn("ndi-discovery-service.v1.json", doc)
        text = read("pvj", "NDI.md")
        self.assertRegex(text, r"a name at an IP address")
        self.assertIn("another port", text)
        self.assertFalse(re.search("—", text))


if __name__ == "__main__":
    unittest.main()
