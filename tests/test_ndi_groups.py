# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Which senders the box looks for (D62, the owner's words of 2026-10-11: "make sure to incorporate the NDI group and
discovery policies. i can lock the NDI network with a group policy"): the groups reach the finder's structure
exactly, the discovery servers reach the library's own configuration file exactly, only the Owner changes them, and
an older release loads the file."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from pvj import ndi, ndisetup, paths
from tests.test_ndi_pads import MASTER, REPO, PadBase
from tests.test_roles import CONTROLLER_DEVICES, TUNNEL


class Names(unittest.TestCase):
    def test_a_group_is_kept_as_typed_and_has_no_comma(self):
        for good in ("Leyline", "leyline", "Stage 1", "room_2.b-c", "A", "x" * ndi.MAX_GROUP, "Public"):
            self.assertEqual(ndi.clean_group(good), good)
        for bad in ("", " ", "a,b", "a, b", " lead", "trail ", "x" * (ndi.MAX_GROUP + 1), "café", "a\nb", "a\x00", "a;b", 'a"b', "a\\b", "a/b",
                    7, None, ["a"], "‮ab"):
            with self.assertRaises(ndi.NdiError, msg=repr(bad)):
                ndi.clean_group(bad)

    def test_a_discovery_server_is_a_private_address_with_or_without_a_port(self):
        for good in ("192.168.1.5", "10.0.0.2:5959", "169.254.3.4:1", "172.16.0.9:65535"):
            self.assertEqual(ndi.clean_discovery(good), good)
        for bad in ("8.8.8.8", "8.8.8.8:5959", "192.168.1.5:0", "192.168.1.5:65536", "192.168.1.5:", "192.168.1.5:59 59", "server.local", "192.168.1.5,10.0.0.1",
                    "192.168.1.5:5959:1", "[::1]:5959", "127.0.0.1", "192.168.1.5:05959", "", 7, None, "192.168.1.5\n"):
            with self.assertRaises(ndi.NdiError, msg=repr(bad)):
                ndi.clean_discovery(bad)

    def test_the_saved_section_is_checked_and_a_bad_entry_is_left_out_when_read(self):
        good = {"addresses": [], "groups": ["Leyline", "Stage 1"], "discovery": ["10.0.0.2:5959", "10.0.0.3"], "mdns": True}
        self.assertEqual(ndi.validate_saved(good), good)
        for bad in ({"groups": "Leyline"}, {"groups": ["a", "a"]}, {"groups": ["a,b"]}, {"groups": ["g%d" % n for n in range(ndi.MAX_GROUPS + 1)]},
                    {"discovery": "10.0.0.2"}, {"discovery": ["10.0.0.2", "10.0.0.2"]}, {"discovery": ["8.8.8.8"]},
                    {"discovery": ["10.0.0.%d" % n for n in range(1, ndi.MAX_DISCOVERY + 2)]}, {"mdns": "no"}, {"other": 1}):
            with self.assertRaises(ndi.NdiError, msg=bad):
                ndi.validate_saved(dict({"addresses": []}, **bad))
        self.assertEqual(ndi.saved_groups({"groups": ["Leyline", "a,b", "Leyline", 7, "ok"]}), ["Leyline", "ok"])
        self.assertEqual(ndi.saved_discovery({"discovery": ["10.0.0.2", "8.8.8.8", "10.0.0.2", None, "10.0.0.3:1"]}), ["10.0.0.2", "10.0.0.3:1"])
        self.assertEqual((ndi.saved_groups("x"), ndi.saved_discovery(None), ndi.saved_groups({"groups": "x"})), ([], [], []))


class FinderStructure(unittest.TestCase):
    """The real CtypesLibrary.find_open, with the library's one call replaced: what reaches NDIlib_find_create_t."""

    def open(self, ips, groups):
        seen = {}

        class Lib:
            @staticmethod
            def NDIlib_find_create_v2(ref):
                s = ref._obj
                seen.update(show_local=s.show_local, groups=s.groups, extra_ips=s.extra_ips)
                return 1
        lib = ndi.CtypesLibrary.__new__(ndi.CtypesLibrary)
        lib.lib = Lib
        self.assertEqual(lib.find_open(ips, groups), 1)
        return seen

    def test_the_groups_and_the_addresses_reach_the_structure_exactly(self):
        self.assertEqual([n for n, _t in ndi._FindCreate._fields_], ["show_local", "groups", "extra_ips"])     # show_local_sources, p_groups, p_extra_ips
        self.assertEqual([t for _n, t in ndi._FindCreate._fields_], [ctypes.c_bool, ctypes.c_char_p, ctypes.c_char_p])
        self.assertEqual(self.open("10.0.0.5,10.0.0.6", "Leyline,Stage 1"), {"show_local": True, "groups": b"Leyline,Stage 1", "extra_ips": b"10.0.0.5,10.0.0.6"})
        self.assertEqual(self.open("", ""), {"show_local": True, "groups": None, "extra_ips": None})      # NULL: the library's own default groups


class ConfigFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_file_is_ndis_own_key_and_nothing_else(self):
        self.assertEqual(json.loads(ndi.config_text(["10.0.0.2:5959", "10.0.0.3"])), {"ndi": {"networks": {"discovery": "10.0.0.2:5959,10.0.0.3"}}})
        self.assertEqual(json.loads(ndi.config_text([])), {"ndi": {}})
        self.assertEqual(ndi.CONFIG_NAME, "ndi-config.v1.json")

    def wanted(self, section):
        w = ndi.Wanted(lambda: True, lambda: {"ndi": section}, log=lambda *_: None)
        w()
        return w

    def test_the_panel_writes_it_whole_and_only_when_it_changed(self):
        i = ndi.Input(None, "/x", self.wanted({"addresses": [], "discovery": ["10.0.0.2:5959"]}), log=lambda *_: None, config_dir=self.dir)
        self.assertEqual(i.write_config(), ["10.0.0.2:5959"])
        path = os.path.join(self.dir, ndi.CONFIG_NAME)
        with open(path) as f:
            self.assertEqual(json.load(f), {"ndi": {"networks": {"discovery": "10.0.0.2:5959"}}})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(os.listdir(self.dir), [ndi.CONFIG_NAME])                  # no temporary file is left
        self.assertEqual(ndi.config_discovery(self.dir), ["10.0.0.2:5959"])
        before = os.stat(path).st_ino
        i.write_config()
        self.assertEqual(os.stat(path).st_ino, before, "the same content was written again")
        i._wanted = self.wanted({"addresses": []})
        self.assertEqual(i.write_config(), [])
        self.assertEqual(ndi.config_discovery(self.dir), [])

    def test_without_the_folder_the_page_is_told_and_nothing_is_written(self):
        i = ndi.Input(None, "/x", self.wanted({"addresses": [], "discovery": ["10.0.0.2"]}), log=lambda *_: None)
        self.assertEqual(i.write_config(), ["10.0.0.2"])
        self.assertIn("pvj-ndi-runtime refresh", i.config_problem)
        i = ndi.Input(None, "/x", self.wanted({"addresses": [], "discovery": ["10.0.0.2"]}), log=lambda *_: None, config_dir=os.path.join(self.dir, "gone"))
        i.write_config()
        self.assertIn("could not be written", i.config_problem)
        i = ndi.Input(None, "/x", self.wanted({"addresses": []}), log=lambda *_: None)
        i.write_config()
        self.assertEqual(i.config_problem, "")

    def test_a_file_this_box_did_not_write_is_not_believed(self):
        for text in ("{", '{"ndi": {"networks": {"discovery": "8.8.8.8"}}}', '{"ndi": {"networks": {"discovery": ["10.0.0.2"]}}}', "x" * 9000,
                     '{"ndi": {"networks": {"discovery": "10.0.0.2,10.0.0.2"}}}'):
            with open(os.path.join(self.dir, ndi.CONFIG_NAME), "w") as f:
                f.write(text)
            self.assertEqual(ndi.config_discovery(self.dir), [], text[:40])
        self.assertEqual(ndi.config_discovery(os.path.join(self.dir, "none")), [])

    def test_the_helper_reads_the_file_where_its_unit_says_and_the_panel_writes_there(self):
        with open(ndisetup.TEMPLATE) as f:
            unit = f.read()
        self.assertIn("Environment=NDI_CONFIG_DIR=%s\n" % paths.NDI_CONFIG_DIR, unit)
        rw = [l for l in unit.splitlines() if l.startswith("ReadWritePaths=")]
        self.assertEqual(rw, ["ReadWritePaths=/run/pvj-ndi -/run/pvj-ndi-pipe"], "the helper must not be able to write its own configuration")
        self.assertIn("d %s 0750 pvj-web pvj-ndi -\n" % paths.NDI_CONFIG_DIR, ndisetup.TMPFILES_TEXT)
        self.assertIn("Environment=PVJ_NDI_CONFIG_DIR=%s\n" % paths.NDI_CONFIG_DIR, ndisetup.WEB_TEXT)
        self.assertIn("ReadWritePaths=-%s\n" % paths.NDI_CONFIG_DIR, ndisetup.WEB_TEXT)
        self.assertEqual(paths.ndi_config_dir({"PVJ_NDI_CONFIG_DIR": "/a"}), "/a")
        self.assertIsNone(paths.ndi_config_dir({}))


class Scope(PadBase):
    """The settings through the API, with the real Service and the fake library."""

    def setUp(self):
        super().setUp()
        self.confdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.confdir, True)
        self.api.ndi.config_dir = self.service.config_dir = self.confdir
        self.restarts = []
        self.service.on_unload = lambda: self.restarts.append(1)

    def set(self, body, by=None):
        return self.h("POST", "/api/ndi", body, by or self.owner)

    def test_only_the_owner_changes_them(self):
        bodies = ({"action": "add_group", "group": "Leyline"}, {"action": "remove_group", "group": "Leyline"},
                  {"action": "add_discovery", "server": "10.0.0.2"}, {"action": "remove_discovery", "server": "10.0.0.2"})
        self.ready()
        others = [("guest", self.guest), ("operator", self.operator)] + sorted(CONTROLLER_DEVICES.items())
        for body in bodies:
            for name, dev in others:
                self.later()
                self.assertEqual(self.set(body, dev)[0], 403, (name, body))
        for role in ("view", "live"):                       # a remote support session below Owner: as the other NDI settings
            if self.api.support.session is not None:
                self.h("POST", "/api/support/stop", {}, self.owner)
            code = self.h("POST", "/api/support/start", {"confirm": "start", "role": role}, self.owner)[1]["code"]
            dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, None, TUNNEL)[1]["token"])
            for body in bodies:
                self.assertEqual(self.h("POST", "/api/ndi", body, dev, TUNNEL)[0], 403, (role, body))
        self.assertNotIn("groups", self.settings.data.get("ndi", {}))
        self.assertNotIn("discovery", self.settings.data.get("ndi", {}))
        self.assertEqual(self.lib.groups, [""])

    def test_the_groups_reach_the_finder_and_the_page_says_which_are_in_force(self):
        self.assertEqual(self.lib.groups, [""])                                  # none: the library's own default
        st, out = self.set({"action": "add_group", "group": "Leyline"})
        self.assertEqual((st, out["groups"], out["groups_now"]), (200, ["Leyline"], ["Leyline"]))
        self.assertEqual(self.set({"action": "add_group", "group": "Stage 1"})[0], 200)
        self.assertEqual(self.lib.groups, ["", "Leyline", "Leyline,Stage 1"])      # the finder is opened again, with exactly these
        self.assertEqual(self.lib.closed_finders, 2)
        self.assertEqual(self.settings.data["ndi"]["groups"], ["Leyline", "Stage 1"])
        self.assertIn("NDI groups to look in are now Leyline, Stage 1", " ".join(self.lines))
        for body, status in (({"action": "add_group", "group": "Leyline"}, 400), ({"action": "add_group", "group": "a,b"}, 400),
                             ({"action": "remove_group", "group": "nope"}, 404), ({"action": "add_group"}, 400)):
            self.assertEqual(self.set(body)[0], status, body)
        self.assertEqual(self.set({"action": "remove_group", "group": "Leyline"})[1]["groups_now"], ["Stage 1"])
        self.assertEqual(self.set({"action": "remove_group", "group": "Stage 1"})[1]["groups_now"], [])
        self.assertEqual(self.lib.groups[-1], "")
        self.assertNotIn("groups", self.settings.data["ndi"])
        for n in range(ndi.MAX_GROUPS):
            self.assertEqual(self.set({"action": "add_group", "group": "g%d" % n})[0], 200)
        self.assertEqual(self.set({"action": "add_group", "group": "one more"})[0], 400)

    def test_a_source_that_is_playing_plays_on_when_the_groups_change(self):
        from tests.test_ndi import frame
        self.give()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.press(self.operator)[0], 200)
        self.assertEqual(self.set({"action": "add_group", "group": "Elsewhere"})[0], 200)
        self.assertEqual((self.lib.closed, self.api.ndi.current["name"]), (0, "RESOLUME (Output)"))
        self.assertIsNotNone(self.service.receiver)

    def test_the_discovery_servers_reach_the_file_and_the_helper_starts_again_to_read_them(self):
        st, out = self.set({"action": "add_discovery", "server": "10.0.0.2:5959"})
        self.assertEqual((st, out["discovery"]), (200, ["10.0.0.2:5959"]))
        with open(os.path.join(self.confdir, ndi.CONFIG_NAME)) as f:
            self.assertEqual(json.load(f), {"ndi": {"networks": {"discovery": "10.0.0.2:5959"}}})
        self.assertEqual(self.restarts, [1], "the library reads its file once: the helper must start again")
        self.assertEqual(out["discovery_now"], [], "and until it has, the page is told the old servers are in force")
        # the new process: the library is loaded with the file as it is now
        self.service.lib, self.service.discovery, self.service.configured = None, None, False
        out = self.h("GET", "/api/ndi", {}, self.owner)[1]
        self.assertEqual((out["discovery"], out["discovery_now"]), (["10.0.0.2:5959"], ["10.0.0.2:5959"]))
        self.assertEqual(self.restarts, [1], "no second restart once the library has what was asked")
        for body, status in (({"action": "add_discovery", "server": "10.0.0.2:5959"}, 400), ({"action": "add_discovery", "server": "8.8.8.8"}, 400),
                             ({"action": "remove_discovery", "server": "10.0.0.9"}, 404), ({"action": "add_discovery", "server": "x.local"}, 400)):
            self.assertEqual(self.set(body)[0], status, body)
        self.assertEqual(self.set({"action": "remove_discovery", "server": "10.0.0.2:5959"})[0], 200)
        self.assertEqual(self.restarts, [1, 1])
        self.assertNotIn("discovery", self.settings.data["ndi"])
        self.assertIn("NDI discovery servers are now none", " ".join(self.lines))

    def test_a_box_whose_set_up_is_older_says_so_and_nothing_restarts(self):
        self.api.ndi.config_dir = None
        self.service.config_dir = "/opt/pvj-ndi"                 # what the older unit told the library
        st, out = self.set({"action": "add_discovery", "server": "10.0.0.2"})
        self.assertEqual((st, out["discovery"], out["discovery_now"], self.restarts), (200, ["10.0.0.2"], [], []))
        self.assertIn("pvj-ndi-runtime refresh", out["notice"])

    def test_every_other_ndi_setting_survives_each_of_them(self):
        self.give()
        steps = ({"action": "add_group", "group": "Leyline"}, {"action": "add_discovery", "server": "10.0.0.2"}, {"action": "sound", "on": True},
                 {"action": "add_address", "address": "192.168.0.20"}, {"action": "remove_address", "address": "192.168.0.20"})
        for body in steps:
            self.assertEqual(self.set(body)[0], 200, body)
        got = self.settings.data["ndi"]
        self.assertEqual((got["groups"], got["discovery"], got["sound"], got["addresses"], [r["id"] for r in got["sources"]]),
                         (["Leyline"], ["10.0.0.2"], True, [], [self.pad()["ndi"]]))
        self.assertEqual(ndi.validate_saved(got), got)
        from pvj import boxcare
        self.assertEqual(boxcare.check_ndi(got, None), got)

    def test_a_pads_source_is_looked_for_only_where_the_box_looks(self):
        """The pad's press asks the same list as the page: a sender the finder does not report (another group) is not
        there for the pad either."""
        self.give()
        self.assertEqual(self.set({"action": "add_group", "group": "Leyline"})[0], 200)
        self.announce()                                              # the finder, in that group, reports nobody
        st, out = self.press(self.operator)
        self.assertEqual((st, "is not on the network now" in out["error"], self.lib.opened), (409, True, []))

    def test_masters_code_loads_a_file_with_groups_and_discovery(self):
        self.set({"action": "add_group", "group": "Leyline"})
        self.set({"action": "add_discovery", "server": "10.0.0.2:5959"})
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", MASTER], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % MASTER)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", MASTER, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        script = ("import sys, json\nfrom pvj.settings import Settings\ns = Settings(sys.argv[1])\ns.load()\n"
                  "s.data['mix'] = {'transition': 'dip', 'duration': 2.0}\ns.save()\nprint(json.dumps(s.data['schema']))\n")
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual((run.returncode, json.loads(run.stdout.strip().splitlines()[-1])), (0, self.settings.data["schema"]), run.stderr)
        with open(copy) as f:
            kept = json.load(f)["ndi"]
        self.assertEqual((kept["groups"], kept["discovery"]), (["Leyline"], ["10.0.0.2:5959"]))


if __name__ == "__main__":
    unittest.main()
