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
    """Each service has a runtime folder nobody else can take (D45; found on a real Pi 4, 2026-10-04)."""

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
        # the NDI helper's folder is the one that is NOT reached through group pvj (D61)
        self.assertEqual(folders["pvj-ndi"], {("pvj-ndi.service", "pvj-ndi", "0750", "no")})
        self.assertEqual("/run/pvj-ndi", p.NDI_DIR)
        self.assertEqual(set(folders), set(want) | {"pvj-update", "pvj-ndi"})
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
        # a version from before D45 that an update rolled back to keeps working in its own folder: for those the
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
        self.assertEqual(lines[1], ["f", p.RUN + "/.d45", "0644", "root", "root", "-"])     # the installer's "done"
        self.assertEqual(lines[2], ["d", p.WEB_DIR, "0750", "pvj-web", "pvj", "-"])
        # inside the panel's folder root makes links only, and sets no owner or mode there
        for ln in lines[3:]:
            self.assertEqual((ln[0], ln[2:5]), ("L+", ["-", "-", "-"]), ln)
        links = {ln[1]: ln[-1] for ln in lines if ln[0] == "L+"}
        self.assertEqual(links, {p.WEB_DIR + "/player.sock": p.PLAYER_DIR + "/" + p.PLAYER_SOCKET,
                                 p.WEB_DIR + "/netd.sock": p.NETD_DIR + "/" + p.NETD_SOCKET})
        self.assertEqual(len(lines), 5)
        for ln in lines:                                   # nothing outside the parent and the panel's own folder
            self.assertTrue(ln[1] in (p.RUN, p.RUN + "/.d45") or ln[1].startswith(p.WEB_DIR), ln)

    def installer(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            return f.read()

    @staticmethod
    def function(sh, name):
        start = sh.index(name + "() {")
        return sh[start:sh.index("\n}\n", start)]

    def test_the_installer_stops_the_services_then_reloads_then_takes_the_folder_back_then_restarts(self):
        sh = self.installer()
        real = sh[sh.index('[ -d /run/systemd/system ]; then\n\tprepare_run_folder'):]
        order = [real.index(x) for x in ("prepare_run_folder", "systemctl daemon-reload", "fix_run_folder",
                                         "systemctl restart pvj-player.service")]
        # before the reload a player that restarts by itself runs its old unit and takes the folder back; between
        # the reload and the emptying a restart would let systemd chown a name planted as /run/pvj/player
        self.assertEqual(order, sorted(order))
        prepare = self.function(sh, "prepare_run_folder")
        self.assertIn('systemctl stop "${RUN_UNITS[@]}"', prepare)
        self.assertLess(prepare.index("is-active"), prepare.index("systemctl stop"))
        self.assertIn("RUN_UNITS=(pvj-player.service pvj-web.service pvj-netd.service)", sh)
        self.assertNotIn("systemctl start", sh[:sh.index("fix_run_folder\n\tif systemctl restart systemd-journald")])
        body = self.function(sh, "fix_run_folder")
        steps = [body.index(x) for x in ("chown root:root", "chmod 0755", 'rm -f "$RUN_MARK"', "-mindepth 1 -delete || die",
                                         "systemd-tmpfiles --create")]
        self.assertEqual(steps, sorted(steps))     # take it, forget "done", empty it or stop, and only then "done"
        # an update from the panel installs with --no-start and restarts only the player and the panel
        self.assertIn("systemctl try-restart pvj-netd.service", sh)
        self.assertIn('systemctl start "${RUN_WAS_ACTIVE[@]}"', sh)
        self.assertIn('"$TMPFILES"', sh[sh.index("uninstall() {"):sh.index("if [ \"$UNINSTALL\" = 1 ]")])

    def test_root_never_writes_chowns_or_chmods_a_name_inside_a_folder_an_account_owns(self):
        # Found by the review: the installer wrote a note into /run/pvj/web as root, and a link planted there by
        # the panel's account sent the write, the chown and the chmod anywhere. In these functions root may only
        # chown and chmod /run/pvj itself, remove names, and let tmpfiles write the marker in /run/pvj.
        sh = self.installer()
        for name in ("run_folder_is_sound", "prepare_run_folder", "fix_run_folder"):
            body = "\n".join(ln.split("  #")[0] for ln in self.function(sh, name).splitlines() if not ln.lstrip().startswith("#"))
            for ln in body.splitlines():
                if re.search(r"\b(chown|chmod|install|cp|mv|ln|mkdir|touch|tee|printf|echo|cat)\b", ln) or re.search(r"(?<![<0-9])>", ln):
                    if ln.strip().startswith("log ") or "|| die" in ln or "|| log" in ln:
                        continue
                    targets = re.findall(r'"(\$[A-Z_]+[^"]*)"', ln)
                    self.assertTrue(targets, ln)
                    for t in targets:
                        self.assertIn(t, ("$RUN_DIR", "$RUN_MARK", "${RUN_UNITS[@]}", "$REAL", "$RUN_EMPTY"), ln)
            for word in ("/web", "/player", "/netd", "undervoltage", "pvj-web:pvj"):
                self.assertNotIn(word, body.replace('web) want="pvj-web:pvj 750"', ""), "%s in %s" % (word, name))
        self.assertNotIn("undervoltage", sh)
        # the marker is in the folder that is root's, never in a service's
        self.assertIn('RUN_MARK="$RUN_DIR/.d45"', sh)
        self.assertIn('RUN_DIR="$ROOT/run/pvj"', sh)

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

    def test_every_runtime_path_in_bin_and_install_is_one_the_paths_module_knows(self):
        # Shell and unit files cannot import pvj/paths.py, so every /run/pvj... they name must be one of its paths.
        p = self.paths
        known = {p.RUN, p.RUN + "/.d45", p.PLAYER_DIR, p.WEB_DIR, p.NETD_DIR, p.SYSD_DIR, p.SUPPORTD_DIR, p.UPDATE_DIR,
                 p.UPDATE_RESULT, p.WEB_DIR + "/" + p.PIN, p.WEB_DIR + "/player.sock", p.WEB_DIR + "/netd.sock",
                 p.PLAYER_DIR + "/" + p.PLAYER_SOCKET, p.NETD_DIR + "/" + p.NETD_SOCKET, p.NETD_DIR + "/<name>",
                 p.RUN + "/<name>", p.NDI_DIR}
        files = glob.glob(os.path.join(REPO, "bin", "*")) + glob.glob(os.path.join(REPO, "install", "*"))
        seen = set()
        for path in files:
            if path.endswith(".md"):
                continue
            with open(path) as f:
                for found in re.findall(r"/run/pvj[A-Za-z0-9_./<>-]*", f.read()):
                    found = found.rstrip(".")
                    seen.add(found)
                    self.assertIn(found, known, "%s names %s" % (os.path.basename(path), found))
        self.assertTrue({p.PLAYER_DIR, p.WEB_DIR, p.NETD_DIR, p.SYSD_DIR, p.SUPPORTD_DIR, p.UPDATE_RESULT} <= seen)


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


class WebUnitLightsTest(unittest.TestCase):
    """D53: the panel may write to ALSA devices, for a known controller's lights. That one word changed; this pins
    the whole device policy and the rest of the sandbox, so nothing else widens with it or after it."""

    def test_the_device_policy_is_exactly_this(self):
        web = load_units()["pvj-web.service"]
        self.assertEqual(web["DevicePolicy"], ["closed"])
        self.assertEqual(web["DeviceAllow"], ["/dev/null rw", "/dev/zero rw", "/dev/full rw", "/dev/random r", "/dev/urandom r",
                                              "char-alsa rw", "char-video4linux rw"])
        self.assertNotIn("PrivateDevices", web)             # it would hide /dev/snd; the policy above stands in for it

    def test_nothing_else_in_the_sandbox_widened(self):
        web = load_units()["pvj-web.service"]
        self.assertEqual((web["User"], web["Group"], words(web, "SupplementaryGroups")), (["pvj-web"], ["pvj"], ["audio", "video", "pvj-ndi"]))
        self.assertEqual(web["CapabilityBoundingSet"], ["CAP_NET_BIND_SERVICE"])
        self.assertEqual(web["AmbientCapabilities"], ["CAP_NET_BIND_SERVICE"])
        self.assertEqual(sorted(words(web, "RestrictAddressFamilies")), ["AF_INET", "AF_INET6", "AF_NETLINK", "AF_UNIX"])
        self.assertEqual(words(web, "ReadWritePaths"), ["/var/lib/pvj", "/run/pvj/web"])
        for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("ProtectHome", "yes"), ("PrivateTmp", "yes"),
                           ("ProtectKernelTunables", "yes"), ("ProtectKernelModules", "yes"), ("ProtectControlGroups", "yes"),
                           ("RestrictNamespaces", "yes"), ("LockPersonality", "yes"), ("SystemCallArchitectures", "native"), ("UMask", "0007")):
            self.assertEqual(web[key], [value], key)

    def test_no_other_unit_was_given_alsa_devices_for_writing(self):
        for name, keys in load_units().items():
            if name not in ("pvj-web.service", "pvj-player.service"):       # the player plays sound; it has no device policy of this kind
                self.assertFalse([v for v in keys.get("DeviceAllow", []) if "alsa" in v], name)

    def test_the_check_after_the_open_refuses_what_is_not_an_alsa_device_and_leaks_no_handle(self):
        # L5 of the review. The path's shape is checked first, so to reach the check that comes after the open the
        # shape is loosened here: a regular file, a FIFO with a reader, /dev/null (a character device, of another
        # major number) and a link are each refused, and none leaves a handle open
        import tempfile
        from unittest import mock
        from pvj import midi
        tmp = tempfile.mkdtemp()
        plain, fifo, link = os.path.join(tmp, "file"), os.path.join(tmp, "fifo"), os.path.join(tmp, "link")
        open(plain, "w").close()
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)                 # so the open for writing succeeds and the check decides
        os.symlink("/dev/null", link)

        def handles():
            return len(os.listdir("/dev/fd"))
        try:
            with mock.patch.object(midi, "DEVICE_PATH", re.compile(r".+")):
                before = handles()
                for path in (plain, fifo, "/dev/null", link, os.path.join(tmp, "missing")):
                    with self.assertRaises(OSError, msg=path):
                        midi.LightWriter._open_device(path)
                    self.assertEqual(handles(), before, path)
                with open(plain, "rb") as f:
                    self.assertEqual(f.read(), b"")                         # and nothing was written to anything
        finally:
            os.close(reader)
        self.assertNotEqual(os.major(os.stat("/dev/null").st_rdev), midi.ALSA_MAJOR)

    def test_the_program_opens_only_a_midi_node_by_the_shape_of_its_path(self):
        # what the unit cannot say, the code does: the one place that opens a device for writing checks the path's
        # shape, refuses links, and wants a character device with ALSA's major number
        from pvj import midi
        self.assertEqual(midi.ALSA_MAJOR, 116)
        for path in ("/dev/snd/pcmC0D0p", "/dev/snd/controlC0", "/dev/snd/seq", "/dev/snd/timer", "/dev/snd/midiC0D0/../pcmC0D0p",
                     "/dev/snd/midiC0D0\n", "/dev/null", "midiC0D0", 7, None):
            with self.assertRaises(OSError, msg=path):
                midi.LightWriter._open_device(path)
        with open(midi.__file__) as f:
            source = f.read()
        self.assertEqual(source.count("os.O_WRONLY"), 1)     # one place opens for writing
        self.assertEqual(source.count("LightWriter("), 1)    # and one place makes a writer: MidiHub._lights_tick, for a controller whose
        tick = source[source.index("    def _lights_tick"):source.index("    def _lights_loop")]      # profile has lights and is certainly that controller
        self.assertIn('profile["lights"]', tick)
        self.assertIn("self._sure_of(path, src, profile, now)", tick)
        self.assertLess(tick.index("self._sure_of("), tick.index("LightWriter("))        # (tests/test_lights.py runs both conditions)
        self.assertNotIn("O_RDWR", source)


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


class NdiUnitTest(unittest.TestCase):
    """D61: the one process that loads a closed-source library reading the network. Every line of its sandbox is
    pinned, and so is what it must never be given. None of it has run on a device with the real library."""

    def setUp(self):
        self.units = load_units()
        self.ndi = self.units["pvj-ndi.service"]
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.sh = f.read()

    def test_its_own_account_and_group_and_never_the_groups_that_reach_the_player_the_screen_or_sound(self):
        self.assertEqual((self.ndi["User"], self.ndi["Group"]), (["pvj-ndi"], ["pvj-ndi"]))
        self.assertNotIn("SupplementaryGroups", self.ndi)
        self.assertIn("useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj-ndi pvj-ndi", self.sh)
        for line in self.sh.splitlines():                  # the installer never puts the account into another group
            if "usermod" in line:
                self.assertNotIn("pvj-ndi", line, line)
        # no other unit runs as this account or in this group as its main group
        for name, keys in self.units.items():
            if name != "pvj-ndi.service":
                self.assertNotEqual(keys.get("User"), ["pvj-ndi"], name)
                self.assertNotEqual(keys.get("Group"), ["pvj-ndi"], name)

    def test_only_the_panel_and_the_player_are_let_into_its_folder(self):
        got = sorted(n for n, k in self.units.items() if "pvj-ndi" in words(k, "SupplementaryGroups"))
        self.assertEqual(got, ["pvj-player.service", "pvj-web.service"])
        self.assertEqual((self.ndi["RuntimeDirectory"], self.ndi["RuntimeDirectoryMode"], self.ndi["UMask"]), (["pvj-ndi"], ["0750"], ["0027"]))
        from pvj import paths
        env = dict(w.split("=", 1) for w in words(self.ndi, "Environment"))
        web = dict(w.split("=", 1) for w in words(self.units["pvj-web.service"], "Environment"))
        self.assertEqual(env["PVJ_NDI_DIR"], paths.NDI_DIR)
        self.assertEqual(paths.ndi_socket(web), paths.ndi_socket(env))
        self.assertEqual(paths.ndi_fifo(web), paths.NDI_DIR + "/" + paths.NDI_FIFO)

    def test_the_group_exists_before_any_unit_that_names_it_is_written_or_started(self):
        # systemd refuses to start a unit whose extra group does not exist: the player would stay down
        make = self.sh.index("groupadd --system pvj-ndi")
        self.assertLess(make, self.sh.index('"$SRC/install/pvj-player.service" > "$UNIT"'))
        self.assertLess(make, self.sh.index("systemctl restart pvj-player.service"))

    def test_the_sandbox_is_exactly_this(self):
        n = self.ndi
        self.assertEqual(n["CapabilityBoundingSet"], [""])
        self.assertNotIn("AmbientCapabilities", n)
        for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("ProtectHome", "yes"),
                           ("PrivateDevices", "yes"), ("ProtectKernelTunables", "yes"), ("ProtectKernelModules", "yes"),
                           ("ProtectKernelLogs", "yes"), ("ProtectControlGroups", "yes"), ("ProtectClock", "yes"), ("ProtectHostname", "yes"),
                           ("RestrictNamespaces", "yes"), ("RestrictRealtime", "yes"), ("RestrictSUIDSGID", "yes"),
                           ("LockPersonality", "yes"), ("SystemCallArchitectures", "native")):
            self.assertEqual(n[key], [value], key)
        self.assertEqual(words(n, "ReadWritePaths"), ["/run/pvj-ndi"])
        self.assertEqual(sorted(words(n, "RestrictAddressFamilies")), ["AF_INET", "AF_INET6", "AF_NETLINK", "AF_UNIX"])
        self.assertEqual(words(n, "InaccessiblePaths"), ["-/var/lib/pvj", "-/etc/pvj", "-/run/pvj"])
        for key in ("DeviceAllow", "BindPaths", "StateDirectory", "ExecStartPre", "ExecStartPost", "EnvironmentFile"):
            self.assertNotIn(key, n, key)

    def test_the_library_cannot_reach_the_internet_and_the_panel_takes_only_addresses_the_unit_allows(self):
        import ipaddress
        from pvj import ndi
        self.assertEqual(self.ndi["IPAddressDeny"], ["any"])
        allow = words(self.ndi, "IPAddressAllow")
        self.assertEqual(allow, ["link-local", "multicast", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])      # not localhost
        nets = {ipaddress.ip_network(a) for a in allow if "/" in a} | {ipaddress.ip_network("169.254.0.0/16")}      # link-local
        self.assertEqual(set(ndi.PRIVATE_NETS), nets)

    def test_the_library_is_given_nowhere_to_write_that_lasts_and_comes_from_a_folder_that_is_roots(self):
        from pvj import ndi
        env = dict(w.split("=", 1) for w in words(self.ndi, "Environment"))
        self.assertEqual((env["HOME"], env["NDI_CONFIG_DIR"]), ("/tmp", ndi.LIB_DIR))
        self.assertFalse(ndi.LIB_DIR.startswith(("/usr/lib", "/lib", "/usr/local/lib", "/var", "/run", "/tmp")))   # not the system path, not writable state
        self.assertNotIn("libndi", self.sh)                # the installer never fetches or copies the runtime
        self.assertNotRegex(self.sh, r"ndi\.(video|tv)|downloads\.ndi")

    def test_installer_and_image_enable_it_and_uninstall_removes_it(self):
        self.assertIn("systemctl enable pvj-ndi.service", self.sh)
        self.assertIn("systemctl try-restart pvj-ndi.service", self.sh)
        self.assertIn('"$SRC/install/pvj-ndi.service" > "$NDI_UNIT"', self.sh)
        self.assertIn('run rm -f "$NDI_UNIT" "$BIN_LINKS/pvj-ndi-runtime"', self.sh)
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            self.assertIn("pvj-ndi.service", f.read())
        for name in ("pvj-ndi", "pvj-ndi-runtime"):
            self.assertTrue(os.access(os.path.join(REPO, "bin", name), os.X_OK), name)

    def test_no_ndi_file_is_in_the_repository(self):
        for root, _dirs, files in os.walk(REPO):
            if os.sep + "." in root[len(REPO):]:
                continue
            for n in files:
                self.assertNotRegex(n, r"(?i)libndi|Processing\.NDI|\.so(\.[0-9]+)*$", os.path.join(root, n))

    def test_what_it_can_write_and_use_is_bounded(self):
        # Review finding 10. None of these figures has been tried on a device.
        n = self.ndi
        self.assertEqual((n["MemoryMax"], n["TasksMax"], n["CPUQuota"]), (["512M"], ["64"], ["300%"]))
        self.assertNotIn("PrivateTmp", n)                  # its folders are on the card and have no bound
        self.assertEqual(words(n, "TemporaryFileSystem"), ["/tmp:rw,nosuid,nodev,size=8M,mode=1777", "/var/tmp:rw,nosuid,nodev,size=8M,mode=1777"])
        env = dict(w.split("=", 1) for w in words(n, "Environment"))
        self.assertEqual(env["HOME"], "/tmp")              # a home in the bounded private /tmp, not in /run
        self.assertEqual(words(n, "ReadWritePaths"), ["/run/pvj-ndi"])
        self.assertEqual(n["Restart"], ["always"])         # the helper ends itself when the module goes off, and comes back clean

    def test_the_system_bus_stays_reachable_and_the_unit_says_why(self):
        with open(os.path.join(REPO, "install", "pvj-ndi.service")) as f:
            text = f.read()
        self.assertIn("The system bus is NOT denied, and cannot be", text)
        self.assertIn("libavahi-client", text)
        for key in ("InaccessiblePaths", "TemporaryFileSystem", "BindReadOnlyPaths"):
            self.assertFalse([w for w in words(self.ndi, key) if "dbus" in w], key)
        self.assertIn("AF_UNIX", words(self.ndi, "RestrictAddressFamilies"))

    def test_uninstall_says_what_stays_and_purge_takes_the_runtime(self):
        body = self.sh[self.sh.index("uninstall() {"):self.sh.index('if [ "$UNINSTALL" = 1 ]')]
        self.assertIn('if [ "$PURGE" = 1 ]; then run rm -rf "${ROOT}/opt/pvj-ndi"; fi', body)
        self.assertIn("/opt/pvj-ndi (your copy of the NDI runtime) unless --purge", body)
        self.assertIn("pvj-ndi, the player's) and the groups pvj and pvj-ndi are left in place", body)
        self.assertNotIn("userdel", body)
