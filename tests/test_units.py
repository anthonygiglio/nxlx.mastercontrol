# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Static checks on the systemd unit files. Both of these bugs were found only on a real Pi 4, which is why
they are guarded here: nothing in a container starts these units."""
import glob
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def parse(path):
    keys = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"^([A-Za-z]+)=(.*)$", line.strip())
            if m:
                keys.setdefault(m.group(1), []).append(m.group(2))
    return keys


def load_units(directory=None):
    directory = directory or os.path.join(REPO, "install")
    return {os.path.basename(p): parse(p) for p in glob.glob(os.path.join(directory, "*.service"))}


def words(keys, *names):
    return [w for n in names for v in keys.get(n, []) for w in v.split()]


def start_order(units, implicit=False):
    """The start-order graph: edges[a] holds b when a must start before b. With `implicit`, also what systemd adds
    by itself to a service that keeps its default dependencies (after sysinit.target and basic.target), and the
    fact that sysinit.target waits for systemd-tmpfiles-setup.service, which is what creates /run/pvj at boot."""
    edges = {}
    if implicit:
        edges.setdefault("systemd-tmpfiles-setup.service", set()).add("sysinit.target")
        edges.setdefault("sysinit.target", set()).add("basic.target")
    for name, keys in units.items():
        if implicit and keys.get("DefaultDependencies", ["yes"])[-1] != "no":
            edges.setdefault("sysinit.target", set()).add(name)
            edges.setdefault("basic.target", set()).add(name)
        for other in words(keys, "After"):
            edges.setdefault(other, set()).add(name)
        for other in words(keys, "Before"):
            edges.setdefault(name, set()).add(other)
        for target in words(keys, "WantedBy", "RequiredBy"):
            edges.setdefault(name, set()).add(target)     # a target starts after what it wants
    return edges


def starts_before(edges, first, later):
    seen, todo = set(), [first]
    while todo:
        node = todo.pop()
        if node == later:
            return True
        if node not in seen:
            seen.add(node)
            todo.extend(edges.get(node, ()))
    return False


def ordering_cycle(units, implicit=False):
    """A cycle in the start-order graph, as a list of names, or None."""
    edges = start_order(units, implicit)

    def visit(node, path):
        if node in path:
            return path[path.index(node):] + [node]
        for nxt in sorted(edges.get(node, ())):
            found = visit(nxt, path + [node])
            if found:
                return found
        return None
    for node in sorted(edges):
        found = visit(node, [])
        if found:
            return found
    return None


class UnitOrderingTest(unittest.TestCase):
    def test_no_unit_is_ordered_after_a_target_that_wants_it(self):
        # A target waits for the units it wants, so "After=multi-user.target" plus "WantedBy=multi-user.target"
        # is a cycle, and systemd then deletes one of the start jobs without any error.
        for name, keys in load_units().items():
            clash = set(words(keys, "After")) & set(words(keys, "WantedBy", "RequiredBy"))
            self.assertEqual(clash, set(), "%s is ordered after a target that pulls it in: %s" % (name, sorted(clash)))

    def test_no_ordering_cycle_between_our_units(self):
        self.assertIsNone(ordering_cycle(load_units()))

    def test_no_cycle_with_what_systemd_adds_by_itself_and_the_run_folder_is_made_first(self):
        units = load_units()
        self.assertIsNone(ordering_cycle(units, implicit=True))
        edges = start_order(units, implicit=True)
        for name, keys in units.items():
            if any(d.startswith("pvj/") for d in words(keys, "RuntimeDirectory")):
                self.assertNotIn("DefaultDependencies", keys, name)
                self.assertTrue(starts_before(edges, "systemd-tmpfiles-setup.service", name), name)
                self.assertFalse(starts_before(edges, name, "sysinit.target"), name)

    def test_the_services_start_in_any_order(self):
        # No service needs another to be there: a missing peer is "not running" to the code (tests/test_paths.py,
        # tests/test_player.py), never a failed start. The one ordering between our units is the panel after the
        # player, and that is a wish (Wants), not a requirement.
        units = load_units()
        ours = set(units)
        for name, keys in units.items():
            self.assertEqual(set(words(keys, "Requires", "BindsTo", "PartOf", "Requisite")) & ours, set(), name)
            self.assertEqual(set(words(keys, "Before")) & ours, set(), name)
            after = set(words(keys, "After")) & ours
            self.assertEqual(after, {"pvj-player.service"} if name == "pvj-web.service" else set(), name)

    def test_the_check_would_have_caught_the_old_player_unit(self):
        old = {"pvj-player.service": {"After": ["multi-user.target systemd-udev-settle.service"], "WantedBy": ["multi-user.target"]},
               "pvj-web.service": {"After": ["network.target pvj-player.service"], "WantedBy": ["multi-user.target"]}}
        self.assertIsNotNone(ordering_cycle(old))


class PlayerUnitTest(unittest.TestCase):
    def test_player_unit_has_no_shell_hook_for_the_socket(self):
        # The socket is opened up by `pvj-player serve` (tests/test_player.py). A unit hook ran before mpv had made
        # its new socket and changed the stale one, so it did nothing (found on a real Pi 4).
        self.assertNotIn("ExecStartPost", load_units()["pvj-player.service"])

    def test_player_runs_in_the_pvj_group_so_the_panel_can_reach_the_socket(self):
        self.assertEqual(load_units()["pvj-player.service"]["Group"], ["pvj"])
        self.assertEqual(load_units()["pvj-web.service"]["Group"], ["pvj"])


def runtime_folders(units):
    """{folder name under /run: {(unit, user, mode, preserve)}} for every RuntimeDirectory= in the units."""
    out = {}
    for name, keys in units.items():
        for d in words(keys, "RuntimeDirectory"):
            out.setdefault(d.split(":")[0].strip("/"), set()).add(
                (name, keys.get("User", ["root"])[-1], keys.get("RuntimeDirectoryMode", ["0755"])[-1],
                 keys.get("RuntimeDirectoryPreserve", ["no"])[-1]))
    return out


def runtime_folder_faults(units):
    """What is wrong with the units' runtime folders, as a list of sentences (empty when all is well).

    systemd gives a RuntimeDirectory, and everything in it, to the User= and Group= of the unit that is starting
    whenever the folder's owner or group differs (systemd.exec(5), src/core/execute.c: path_chown_recursive), so a
    folder named by units of different users belongs to whichever started last. The parent of a nested folder is
    only created when missing (root:root 0755) and is otherwise left alone, so it stays root's exactly as long as
    no unit names it as its own RuntimeDirectory."""
    faults = []
    folders = runtime_folders(units)
    for d, users in sorted(folders.items()):
        owners = {(u, keys_user) for u, keys_user, _mode, _keep in users}
        if len({user for _unit, user in owners}) > 1:
            faults.append("%s is the RuntimeDirectory of units with different users: %s" % (d, sorted(owners)))
        groups = {tuple(units[u].get("Group", [""])) for u, _user, _mode, _keep in users}
        if len(groups) > 1:
            faults.append("%s is the RuntimeDirectory of units with different groups" % d)
        if len({(mode, keep) for _unit, _user, mode, keep in users}) > 1:
            faults.append("%s has different modes or lifetimes in different units" % d)
        for _unit, _user, mode, _keep in users:
            if int(mode, 8) & 0o022:
                faults.append("%s is writable by its group or by everybody (%s)" % (d, mode))
        for other in folders:
            if other != d and other.startswith(d + "/"):
                faults.append("%s is a unit's own folder and the parent of %s" % (d, other))
    return faults


class RuntimeFolderTest(unittest.TestCase):
    """Each service has a runtime folder nobody else can take (D44; found on a real Pi 4, 2026-10-04)."""

    def setUp(self):
        from pvj import paths
        self.paths, self.units = paths, load_units()

    def env(self, unit):
        return dict(w.split("=", 1) for w in words(self.units[unit], "Environment"))

    def test_no_folder_is_shared_between_users_or_writable_by_a_group_or_is_another_folders_parent(self):
        self.assertEqual(runtime_folder_faults(self.units), [])

    def test_the_check_would_have_caught_the_old_units(self):
        shared = {"RuntimeDirectory": ["pvj"], "RuntimeDirectoryMode": ["0770"], "RuntimeDirectoryPreserve": ["yes"]}
        old = {"pvj-player.service": dict(shared, User=["gigbox"], Group=["pvj"]),
               "pvj-web.service": dict(shared, User=["pvj-web"], Group=["pvj"]),
               "pvj-netd.service": dict(shared, User=["root"], Group=["pvj"])}
        faults = "\n".join(runtime_folder_faults(old))
        self.assertIn("different users", faults)
        self.assertIn("writable by its group", faults)
        nested = {"a.service": {"RuntimeDirectory": ["pvj"], "User": ["pvj-web"]},
                  "b.service": {"RuntimeDirectory": ["pvj/netd"], "User": ["root"]}}
        self.assertIn("the parent of pvj/netd", "\n".join(runtime_folder_faults(nested)))

    def test_the_parent_is_no_units_own_folder_so_it_stays_roots(self):
        folders = runtime_folders(self.units)
        parent = self.paths.RUN[len("/run/"):]
        self.assertEqual(parent, "pvj")
        self.assertNotIn(parent, folders)
        self.assertEqual(sorted(d for d in folders if d.startswith(parent + "/")), ["pvj/netd", "pvj/player", "pvj/web"])
        # and nothing gives a service write access to the parent through the sandbox either
        for name, keys in self.units.items():
            self.assertNotIn(self.paths.RUN, [p.lstrip("-+") for p in words(keys, "ReadWritePaths", "BindPaths")], name)

    def test_each_folder_has_one_owner_and_the_mode_the_code_expects(self):
        p = self.paths
        want = {"pvj/player": ("pvj-player.service", "@PVJ_USER@", "0750", "no"),
                "pvj/web": ("pvj-web.service", "pvj-web", "0750", "yes"),
                "pvj/netd": ("pvj-netd.service", "root", "0750", "no"),
                "pvj-sysd": ("pvj-sysd.service", "root", "0750", "no"),
                "pvj-supportd": ("pvj-supportd.service", "root", "0750", "no")}
        folders = runtime_folders(self.units)
        for d, owner in want.items():
            self.assertEqual(folders[d], {owner}, d)
            self.assertEqual(self.units[owner[0]]["Group"], ["pvj"], d)      # the panel reaches each through group pvj
        self.assertEqual({user for _u, user, _m, _k in folders["pvj-update"]}, {"root"})
        self.assertEqual(set(folders), set(want) | {"pvj-update"})
        self.assertEqual(["/run/" + d for d in want], [p.PLAYER_DIR, p.WEB_DIR, p.NETD_DIR, p.SYSD_DIR, p.SUPPORTD_DIR])
        self.assertEqual("/run/pvj-update", p.UPDATE_DIR)

    def test_every_unit_is_told_its_own_folder_and_the_panel_where_its_peers_are(self):
        p = self.paths
        self.assertEqual(self.env("pvj-player.service")["PVJ_RUNTIME_DIR"], p.PLAYER_DIR)
        self.assertEqual(self.env("pvj-netd.service")["PVJ_RUNTIME_DIR"], p.NETD_DIR)
        web = self.env("pvj-web.service")
        self.assertEqual((web["PVJ_RUNTIME_DIR"], web["PVJ_PLAYER_DIR"], web["PVJ_NETD_DIR"]), (p.WEB_DIR, p.PLAYER_DIR, p.NETD_DIR))
        self.assertEqual(p.player_socket(web), p.player_socket(self.env("pvj-player.service")))
        self.assertEqual(p.netd_socket(web), p.netd_socket(self.env("pvj-netd.service")))
        self.assertEqual(self.env("pvj-sysd.service")["PVJ_SYSD_DIR"], p.SYSD_DIR)
        self.assertEqual(self.env("pvj-supportd.service")["PVJ_SUPPORTD_DIR"], p.SUPPORTD_DIR)
        for unit in ("pvj-update-usb@.service", "pvj-update-inbox@.service"):
            self.assertIn("--result " + p.UPDATE_RESULT, " ".join(self.units[unit]["ExecStart"]))
        # a version from before D44 that an update rolled back to keeps working in its own folder: for those the
        # only name is PVJ_RUNTIME_DIR, and it must never point a service at a folder it cannot write
        for unit, own in (("pvj-player.service", "pvj/player"), ("pvj-web.service", "pvj/web"), ("pvj-netd.service", "pvj/netd")):
            self.assertEqual(self.env(unit)["PVJ_RUNTIME_DIR"], "/run/" + self.units[unit]["RuntimeDirectory"][0])
            self.assertEqual(self.units[unit]["RuntimeDirectory"], [own])

    def test_a_sandboxed_service_may_write_its_own_folder_and_no_other_runtime_folder(self):
        for unit, own in (("pvj-web.service", self.paths.WEB_DIR), ("pvj-netd.service", self.paths.NETD_DIR),
                          ("pvj-sysd.service", self.paths.SYSD_DIR), ("pvj-supportd.service", self.paths.SUPPORTD_DIR)):
            self.assertEqual(self.units[unit]["ProtectSystem"], ["strict"], unit)
            run = [w.lstrip("-+") for w in words(self.units[unit], "ReadWritePaths") if w.lstrip("-+").startswith("/run")]
            self.assertEqual(run, [own], unit)

    def test_tmpfiles_keeps_the_parent_with_root_and_the_links_for_an_older_panel_in_the_panels_folder(self):
        p = self.paths
        with open(os.path.join(REPO, "install", "pvj-tmpfiles.conf")) as f:
            lines = [ln.split() for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertEqual(lines[0], ["d", p.RUN, "0755", "root", "root", "-"])
        self.assertEqual(lines[1], ["d", p.WEB_DIR, "0750", "pvj-web", "pvj", "-"])
        links = {ln[1]: ln[-1] for ln in lines if ln[0] == "L+"}
        self.assertEqual(links, {p.WEB_DIR + "/player.sock": p.PLAYER_DIR + "/" + p.PLAYER_SOCKET,
                                 p.WEB_DIR + "/netd.sock": p.NETD_DIR + "/" + p.NETD_SOCKET})
        self.assertEqual(len(lines), 4)
        for ln in lines:                                   # nothing outside the parent and the panel's own folder
            self.assertTrue(ln[1] == p.RUN or ln[1].startswith(p.WEB_DIR), ln)

    def test_the_installer_takes_the_folder_back_after_the_reload_and_before_any_restart(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            sh = f.read()
        real = sh[sh.index('[ -d /run/systemd/system ]; then\n\tsystemctl daemon-reload'):]
        reload_, fix, restart = real.index("systemctl daemon-reload"), real.index("fix_run_folder"), real.index("systemctl restart pvj-player.service")
        self.assertLess(reload_, fix)      # before the reload a player that restarts by itself takes the folder back
        self.assertLess(fix, restart)
        body = sh[sh.index("fix_run_folder() {"):sh.index("\n}\n", sh.index("fix_run_folder() {"))]
        self.assertLess(body.index("chown root:root"), body.index("-mindepth 1 -delete"))     # first take it, then empty it
        self.assertLess(body.index("chmod 0755"), body.index("-mindepth 1 -delete"))
        self.assertLess(body.index("-mindepth 1 -delete"), body.index("systemd-tmpfiles --create"))
        # an update from the panel installs with --no-start and restarts only the player and the panel
        self.assertIn("systemctl try-restart pvj-netd.service", sh)
        self.assertIn('"$TMPFILES"', sh[sh.index("uninstall() {"):sh.index("if [ \"$UNINSTALL\" = 1 ]")])

    def test_the_pin_command_reads_where_the_panel_writes(self):
        with open(os.path.join(REPO, "bin", "pvj-pin")) as f:
            self.assertIn('f="${PVJ_RUNTIME_DIR:-%s}/%s"' % (self.paths.WEB_DIR, self.paths.PIN), f.read())

    def test_no_code_names_a_runtime_path_outside_the_paths_module(self):
        for path in glob.glob(os.path.join(REPO, "pvj", "*.py")):
            if os.path.basename(path) == "paths.py":
                continue
            with open(path) as f:
                code = [ln.split("#")[0] for ln in f if not ln.lstrip().startswith("#")]
            for ln in code:
                if '"""' in ln:
                    continue
                self.assertNotRegex(ln, r"""["']/run/pvj""", "%s: %s" % (os.path.basename(path), ln.strip()))


class InstallerOwnershipTest(unittest.TestCase):
    """The player account must not be able to replace settings.json (found by the join-code review)."""

    def setUp(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.sh = f.read()

    def test_the_player_gets_its_own_home_and_old_installs_are_moved_without_moving_files(self):
        self.assertIn("--home-dir /var/lib/pvj-player", self.sh)
        self.assertNotIn("--home-dir /var/lib/pvj ", self.sh)
        self.assertRegex(self.sh, r"usermod -d /var/lib/pvj-player")
        self.assertNotRegex(self.sh, r"usermod[^\n]*\s-m\b")                     # -m would move settings and media

    def test_the_player_is_stopped_before_its_home_is_changed(self):
        # usermod fails while the account has a running process, and the installer stops at the first error
        stop = self.sh.index("systemctl stop pvj-player.service")
        change = self.sh.index("usermod -d /var/lib/pvj-player")
        self.assertLess(stop, change)

    def test_the_state_folder_belongs_to_the_web_user_and_is_not_group_writable(self):
        self.assertIn("chown pvj-web:pvj /var/lib/pvj;", self.sh)
        self.assertIn("chmod 2750 /var/lib/pvj", self.sh)
        self.assertNotIn("chmod 2775 /var/lib/pvj;", self.sh)

class SysdUnitTest(unittest.TestCase):
    def test_the_system_helper_has_no_capabilities_and_only_unix_sockets(self):
        u = load_units()["pvj-sysd.service"]
        self.assertEqual(u["CapabilityBoundingSet"], [""])
        self.assertEqual(u["NoNewPrivileges"], ["yes"])
        self.assertEqual(words(u, "RestrictAddressFamilies"), ["AF_UNIX"])
        self.assertEqual(u["ProtectSystem"], ["strict"])
        for key, value in (("PrivateNetwork", "yes"), ("MemoryDenyWriteExecute", "yes"), ("RestrictSUIDSGID", "yes"),
                           ("RuntimeDirectory", "pvj-sysd"), ("RuntimeDirectoryMode", "0750"), ("SystemCallFilter", "@system-service")):
            self.assertEqual(u[key], [value], key)
        self.assertNotIn("PrivateUsers", u)            # it would hide the caller's uid from the peer check

    def test_installer_and_image_enable_it(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.assertIn("pvj-sysd.service", f.read())
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            self.assertIn("pvj-sysd.service", f.read())


class SupportdUnitTest(unittest.TestCase):
    def test_the_support_helper_has_only_net_admin_and_its_own_folders(self):
        u = load_units()["pvj-supportd.service"]
        self.assertEqual(u["CapabilityBoundingSet"], ["CAP_NET_ADMIN"])
        self.assertEqual(u["NoNewPrivileges"], ["yes"])
        self.assertEqual(sorted(words(u, "RestrictAddressFamilies")), ["AF_INET", "AF_INET6", "AF_NETLINK", "AF_UNIX"])
        for key, value in (("ProtectSystem", "strict"), ("RuntimeDirectory", "pvj-supportd"), ("RuntimeDirectoryMode", "0750"),
                           ("StateDirectory", "pvj-support"), ("StateDirectoryMode", "0700"), ("MemoryDenyWriteExecute", "yes"),
                           ("SystemCallFilter", "@system-service"), ("ProtectKernelModules", "yes")):
            self.assertEqual(u[key], [value], key)
        self.assertNotIn("PrivateUsers", u)            # it would hide the caller's uid from the peer check
        self.assertNotIn("PrivateNetwork", u)          # it works on the box's own network

    def test_installer_and_image_enable_it_and_load_wireguard(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            sh = f.read()
        self.assertIn("pvj-supportd.service", sh)
        self.assertIn("echo wireguard >", sh)
        self.assertIn("wireguard-tools", sh)
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            self.assertIn("pvj-supportd.service", f.read())
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "00-packages-nr")) as f:
            self.assertEqual({"wireguard-tools", "nftables"} - set(f.read().split()), set())


class WebUnitCaptureTest(unittest.TestCase):
    def test_the_panel_may_read_capture_devices_and_only_those_besides_alsa(self):
        web = load_units()["pvj-web.service"]
        self.assertIn("video", words(web, "SupplementaryGroups"))
        self.assertIn("char-video4linux rw", web["DeviceAllow"])
        self.assertEqual(web["DevicePolicy"], ["closed"])


class WebUnitTest(unittest.TestCase):
    def test_the_panel_may_read_the_boxs_addresses(self):
        # `ip -j addr` needs a netlink socket; the sandbox refused it and the Network card showed no addresses on a Pi 4
        families = words(load_units()["pvj-web.service"], "RestrictAddressFamilies")
        self.assertIn("AF_NETLINK", families)
        self.assertNotIn("AF_PACKET", families)


class NetdUnitTest(unittest.TestCase):
    def test_the_network_helper_may_write_where_it_puts_wi_fi_profiles(self):
        # found by review: ProtectSystem=strict made /etc read-only, so every Wi-Fi join would have failed
        from pvj import netcfg
        keys = load_units()["pvj-netd.service"]
        self.assertEqual(keys["ProtectSystem"], ["strict"])
        paths = [p.lstrip("-") for p in words(keys, "ReadWritePaths")]
        self.assertIn(netcfg.KEYFILE_DIR, paths)
        self.assertEqual(sorted(paths), sorted(["/run/pvj/netd", "/var/lib/pvj-netd", netcfg.KEYFILE_DIR]))

    def test_the_network_helper_may_read_the_boxs_addresses(self):
        families = words(load_units()["pvj-netd.service"], "RestrictAddressFamilies")
        self.assertEqual(sorted(families), ["AF_NETLINK", "AF_UNIX"])


if __name__ == "__main__":
    unittest.main()
