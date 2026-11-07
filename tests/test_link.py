# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Ableton Link (D117), everything but the real helper (tests/test_pvj_link.py has that): the clock's new word and its
hand-over, the record, the panel's side against a fake helper and a fake clock, stale records, no peers, the helper
missing, the Owner's setting and who may touch it, and the rules the helper's source and its unit must keep. Nothing
sleeps. Nothing here has met Ableton Live, a phone or a Pi."""
import json
import math
import os
import re
import shutil
import tempfile
import unittest

from pvj import boxcare, clock as clk, link, paths, soundout
from pvj.clock import Clock
from pvj.settings import default_settings
from tests.test_roles import TUNNEL, RolesBase
from tests.test_sound import FakeApi

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Fake:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def make(t=100.0):
    f = Fake(t)
    return Clock(now=f), f


def phase_of(c, at=None):
    return c.bar_position(at)


class ClockWordTest(unittest.TestCase):
    def test_its_first_word_starts_the_clock_at_that_place_in_the_bar(self):
        c, f = make()
        self.assertTrue(c.link(128.0, 2.5, 100.0))
        self.assertEqual((c.source(), c.bpm(), c.state()), ("link", 128.0, "running"))
        self.assertAlmostEqual(phase_of(c), 2.5, places=9)
        self.assertGreaterEqual(c.beats(), 0.0)

    def test_the_place_is_for_the_time_it_names_not_the_time_it_was_read(self):
        c, f = make(100.0)
        c.link(120.0, 1.0, 99.8)                    # read 0.2 s late: at 120 that is 0.4 beat further on
        self.assertAlmostEqual(phase_of(c), 1.4, places=9)
        f.t = 100.5
        self.assertAlmostEqual(phase_of(c), 2.4, places=9, msg="and the flywheel runs on from there")

    def test_every_word_puts_the_phase_right_and_the_beat_count_is_its_own(self):
        c, f = make(100.0)
        c.set_bpm(120)
        f.t = 110.0
        self.assertEqual(c.beat_count(), 20)
        c.link(120.0, 1.5, 110.0)                   # bar position 0 and the session says 1.5: the short way round
        self.assertAlmostEqual(phase_of(c), 1.5, places=9)
        self.assertLessEqual(abs(c.beats() - 20.0), 2.0, "the position moves at most half a bar")
        self.assertEqual(c.beat_count(), 21, "Link's own beat count is not taken")
        c.link(120.0, 3.5, 110.0)                   # the other way round
        self.assertAlmostEqual(phase_of(c), 3.5, places=9)
        self.assertLess(c.beats(), 20.0)

    def test_a_steady_session_gives_a_steady_beat_with_no_drift(self):
        c, f = make(0.0)
        bpm = 133.0
        for i in range(0, 2000):                    # 100 s at 20 records a second
            f.t = i * 0.05
            c.link(bpm, (f.t * bpm / 60.0) % 4.0, f.t)
        self.assertAlmostEqual(c.beats(), 1999 * 0.05 * bpm / 60.0, delta=1e-6)

    def test_it_outranks_the_hand_and_a_tap_while_it_steers_changes_nothing(self):
        c, f = make(0.0)
        c.set_bpm(100)
        c.link(140.0, 0.0, 0.0)
        self.assertEqual((c.source(), c.bpm()), ("link", 140.0))
        f.t = 0.5
        out = c.tap()
        self.assertFalse(out["steers"])
        self.assertEqual((c.source(), c.bpm()), ("link", 140.0))
        self.assertAlmostEqual(c.beats(), 140.0 / 120.0, places=6, msg="the phase did not move")

    def test_when_it_goes_quiet_the_hand_takes_over_with_no_jump(self):
        c, f = make(0.0)
        c.set_bpm(100)
        c.link(140.0, 0.0, 0.0)
        f.t = 5.0
        at_end = c.beats()
        self.assertEqual(c.source(), "link")
        f.t = 5.0 + 1e-6
        self.assertEqual((c.source(), c.bpm()), ("tap", 100.0), "5 s after its last word, by its age and not by when it was read")
        self.assertAlmostEqual(c.beats(), at_end, places=3)

    def test_when_all_are_quiet_its_last_tempo_runs_on_and_says_so(self):
        c, f = make(0.0)
        c.link(140.0, 0.0, 0.0)
        f.t = 60.0
        st = c.status()
        self.assertEqual((st["state"], st["source"], st["steering"], st["held"], st["bpm"]), ("running", "link", None, True, 140.0))
        self.assertEqual(st["age"], 60.0)

    def test_sound_never_overrules_it_and_osc_neither(self):
        c, f = make(0.0)
        c.link(140.0, 0.0, 0.0)
        c.osc_bpm(90)
        c.sound(90.0, 0.9, 5, at=0.0)
        self.assertEqual((c.source(), c.bpm()), ("link", 140.0))

    def test_a_clear_forgets_it_until_it_speaks_again(self):
        c, f = make(0.0)
        c.link(140.0, 0.0, 0.0)
        c.clear()
        self.assertEqual(c.state(), "none")
        c.link(141.0, 1.0, 0.0)
        self.assertEqual((c.state(), c.bpm()), ("running", 141.0))

    def test_odd_input_is_refused_and_changes_nothing(self):
        c, f = make(100.0)
        for args in ((29.9, 0.0, 100.0), (300.1, 0.0, 100.0), (float("nan"), 0.0, 100.0), ("120", 0.0, 100.0), (True, 0.0, 100.0),
                     (120.0, float("inf"), 100.0), (120.0, None, 100.0), (120.0, 0.0, None), (120.0, 0.0, 100.5),
                     (120.0, 0.0, 100.0 - clk.HOLD["link"] - 1.0)):
            self.assertFalse(c.link(*args), repr(args))
        self.assertEqual(c.state(), "none")

    def test_a_clock_that_stepped_back_does_not_stick(self):
        c, f = make(100.0)
        c.link(120.0, 0.0, 100.0)
        f.t = 90.0                                  # the word is now ahead of the clock
        self.assertAlmostEqual(c.bpm(), 120.0)
        self.assertTrue(c.link(120.0, 1.0, 90.0))
        self.assertAlmostEqual(phase_of(c), 1.0, places=9)

    def test_the_consumers_follow_it(self):
        self.assertIn("link", clk.FOLLOWED)
        f = Fake(50.0)
        out = soundout.Sound(FakeApi({}), socket_path="/nonexistent", levels_path="/nonexistent", clock=f, log=lambda *_: None,
                             link_socket="/nonexistent", link_state="/nonexistent")
        self.assertIsNone(out.follow_clock(None, False))
        out.clock.link(120.0, 0.0, 50.0)
        v = out.follow_clock(None, False)
        self.assertEqual((v["bpm"], v["pulse"], v["confidence"], v["clock"], v["level"]), (120.0, 1.0, 1.0, True, 0.0))


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "state")

    def test_a_record_round_trips_and_its_size_is_fixed(self):
        data = link.pack(link.F_ON | link.F_ENABLED, 12.5, 128.25, 3.75, 2, seq=7)
        self.assertEqual(len(data), link.SIZE)
        self.assertEqual(link.unpack(data), {"seq": 7, "on": True, "enabled": True, "time": 12.5, "bpm": 128.25, "phase": 3.75, "peers": 2})

    def test_a_damaged_or_foreign_record_is_nothing(self):
        good = link.pack(1, 1.0, 120.0, 0.0, 1)
        self.assertIsNone(link.unpack(good[:-1]))
        self.assertIsNone(link.unpack(b"\0" * link.SIZE))
        self.assertIsNone(link.unpack(None))
        for i in (0, 4, 8, 16, 24, 40, 48, 52):
            bad = bytearray(good)
            bad[i] ^= 1
            self.assertIsNone(link.unpack(bytes(bad)), i)

    def test_only_a_regular_file_of_the_right_size_is_read(self):
        self.assertIsNone(link.read_state(self.path), "missing")
        with open(self.path, "wb") as f:
            f.write(b"short")
        self.assertIsNone(link.read_state(self.path))
        with open(self.path, "wb") as f:
            f.write(link.pack(1, 1.0, 120.0, 0.0, 1))
        self.assertEqual(link.read_state(self.path)["bpm"], 120.0)
        other = os.path.join(self.dir, "other")
        os.symlink(self.path, other)
        self.assertIsNone(link.read_state(other), "a link is not followed")
        self.assertIsNone(link.read_state(self.dir), "a folder")

    def test_the_helpers_layout_says_the_same(self):
        with open(os.path.join(REPO, "render", "link", "pvj-link.cpp")) as f:
            src = f.read()
        self.assertIn('"NXLK"', src)
        self.assertIn("RECORD_SIZE = %d" % link.SIZE, src)
        self.assertIn("F_ON = %d, F_ENABLED = %d" % (link.F_ON, link.F_ENABLED), src)
        for offset, field in ((12, "flags"), (16, "at"), (24, "bpm"), (32, "phase"), (40, "peers"), (52, "mSeq")):
            self.assertIn("b + %d, &%s," % (offset, field), src)


class FakeHelper:
    """Stands for the socket: answers as pvj-link does, and can be down."""

    def __init__(self):
        self.on, self.up, self.asked = False, True, []

    def request(self, message):
        self.asked.append(message["cmd"])
        if not self.up:
            return None
        if message["cmd"] in ("on", "off"):
            self.on = message["cmd"] == "on"
        return {"ok": True, "on": self.on, "stage": "listening" if self.on else "off", "peers": 0, "bpm": 0.0, "cpu_seconds": 0.5, "uptime": 100.0}


class PanelBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "state")
        self.f = Fake(100.0)
        self.helper = FakeHelper()
        self.api = FakeApi({})
        self.clock = Clock(now=self.f)
        self.link = link.Link(self.api, self.clock, self.helper, self.path, now=self.f)

    def write(self, peers=1, bpm=128.0, phase=1.0, at=None, flags=3):
        with open(self.path + ".new", "wb") as f:
            f.write(link.pack(flags, self.f.t if at is None else at, bpm, phase, peers))
        os.replace(self.path + ".new", self.path)

    def switch_on(self):
        self.api.settings.data["link"] = {"on": True}
        self.link.tick()
        self.assertTrue(self.helper.on)


class PanelTest(PanelBase):
    def test_off_nothing_is_read_and_nothing_is_heard(self):
        self.write()
        self.link.tick()
        self.assertFalse(self.link.hear())
        self.assertEqual(self.clock.state(), "none")
        self.assertNotIn("on", self.helper.asked, "off by default: the helper is not told to start")

    def test_a_word_with_a_peer_reaches_the_clock_with_its_phase(self):
        self.switch_on()
        self.write(peers=1, bpm=128.0, phase=1.0)
        self.assertTrue(self.link.hear())
        self.assertEqual((self.clock.source(), self.clock.bpm()), ("link", 128.0))
        self.assertAlmostEqual(self.clock.bar_position(), 1.0, places=6)

    def test_no_peers_says_nothing_so_links_own_tempo_never_reaches_the_clock(self):
        self.switch_on()
        self.write(peers=0, bpm=120.0)
        self.assertFalse(self.link.hear())
        self.assertEqual(self.clock.state(), "none")
        self.assertEqual(self.link.view()["bpm"], None)

    def test_peers_leaving_hands_over_by_the_clocks_own_rules(self):
        self.switch_on()
        self.clock.set_bpm(100)
        self.write(peers=1, bpm=128.0)
        self.assertTrue(self.link.hear())
        self.assertEqual(self.clock.source(), "link")
        self.f.t += 1.0
        self.write(peers=0, bpm=0.0)
        self.assertFalse(self.link.hear())
        self.assertEqual(self.clock.source(), "link", "young enough still")
        self.f.t += 4.5
        self.assertFalse(self.link.hear())
        self.assertEqual((self.clock.source(), self.clock.bpm()), ("tap", 100.0))

    def test_a_stale_record_is_not_heard_and_the_clock_runs_on(self):
        self.switch_on()
        self.write(peers=1, at=self.f.t - link.STALE_SECONDS - 0.01)
        self.assertFalse(self.link.hear())
        self.assertEqual(self.clock.state(), "none")
        self.f.t += 0.1
        self.write(peers=1)
        self.assertTrue(self.link.hear())
        self.f.t += 1.0                              # the helper stopped writing; the same record is read on
        self.assertFalse(self.link.hear())
        self.assertEqual(self.clock.state(), "running")

    def test_a_record_from_a_helper_that_is_off_is_not_heard(self):
        self.switch_on()
        self.write(peers=1, flags=0)
        self.assertFalse(self.link.hear())

    def test_it_looks_at_the_file_at_most_twenty_times_a_second(self):
        self.switch_on()
        self.write()
        self.assertTrue(self.link.hear())
        self.f.t += 0.01
        self.write()
        self.assertFalse(self.link.hear())
        self.f.t += link.LOOK_EVERY
        self.write()
        self.assertTrue(self.link.hear())

    def test_a_missing_or_damaged_file_is_quiet(self):
        self.switch_on()
        self.assertFalse(self.link.hear())
        with open(self.path, "wb") as f:
            f.write(b"\0" * link.SIZE)
        self.f.t += 1.0
        self.assertFalse(self.link.hear())

    def test_the_helper_missing_is_an_ordinary_state(self):
        self.helper.up = False
        self.api.settings.data["link"] = {"on": True}
        self.link.tick()
        v = self.link.view()
        self.assertEqual((v["on"], v["available"], v["state"], v["peers"], v["bpm"]), (True, False, "unavailable", 0, None))
        self.assertFalse(self.link.hear())
        self.helper.up = True                        # it came back: it is told at the next tick
        self.link.tick()
        self.assertTrue(self.helper.on)
        self.assertTrue(self.link.view()["available"])

    def test_a_restarted_helper_is_told_again_and_the_switch_off_reaches_it(self):
        self.switch_on()
        self.helper.on = False
        self.link.tick()
        self.assertTrue(self.helper.on)
        self.api.settings.data["link"] = {"on": False}
        self.link.tick()
        self.assertFalse(self.helper.on)
        self.assertFalse(self.link.hear())

    def test_an_odd_answer_from_the_helper_means_it_is_not_there(self):
        for reply in (None, {}, {"ok": False}, {"ok": "yes"}, []):
            self.assertEqual(link.helper_view(reply), {"running": False})
        self.assertEqual(link.helper_view({"ok": True, "stage": "<b>", "on": 1})["stage"], "off")
        self.assertEqual(len(link.helper_view({"ok": True, "why": "x" * 500})["why"]), 80)
        self.assertIs(link.helper_view({"ok": True, "on": 1})["on"], False)

    def test_the_view_shows_peers_and_tempo_only_while_fresh(self):
        self.switch_on()
        self.write(peers=2, bpm=128.123)
        v = self.link.view()
        self.assertEqual((v["on"], v["available"], v["state"], v["peers"], v["bpm"]), (True, True, "listening", 2, 128.12))
        self.assertEqual(v["cpu_percent"], 0.5)
        self.f.t += 1.0
        self.assertEqual(self.link.view()["peers"], 0)

    def test_a_setting_that_cannot_be_saved_keeps_the_old_one(self):
        def fail():
            raise OSError("disk full")
        self.api.settings.save = fail
        with self.assertRaises(OSError):
            self.link.save({"on": True})
        self.assertNotIn("link", self.api.settings.data)


class SettingTest(unittest.TestCase):
    def test_off_by_default_and_read_with_defaults(self):
        self.assertEqual(default_settings()["link"], {"on": False})
        self.assertEqual(link.stored(None), {"on": False})

    def test_the_form_is_strict_and_a_damaged_setting_is_off(self):
        self.assertEqual(link.form({"on": True}), {"on": True})
        for bad in ({"on": 1}, {"on": "yes"}, {"on": None}, {"on": True, "more": 1}, [], "on"):
            with self.assertRaises(link.LinkError):
                link.form(bad)
            self.assertEqual(link.stored(bad), {"on": False})

    def test_it_belongs_to_this_box_and_is_not_in_the_sound_section(self):
        self.assertIn("link", boxcare.BOX_OWN)
        # A key of its own: a release from before D117 reads a sound section with an unknown key as damaged, and off.
        self.assertNotIn("link", soundout.DEFAULT)

    def test_the_runtime_paths(self):
        self.assertEqual(paths.link_state(), "/run/pvj-link/state")
        self.assertEqual(paths.link_socket({"PVJ_LINK_DIR": "/x"}), "/x/link.sock")


class RouteTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.helper = FakeHelper()
        self.api.sound.link.client = self.helper

    def test_the_route_is_the_owners_and_replaces_nothing(self):
        routes = self.api.routes()
        self.assertEqual(routes[("POST", "/api/tempo/link")][0], "full")
        self.assertEqual(routes[("POST", "/api/tempo/link")][1].__name__, "set_link")
        with open(os.path.join(REPO, "pvj", "api.py")) as f:
            src = f.read()
        self.assertEqual(len(re.findall(r"def set_link\(", src)), 1)
        self.assertEqual(len(re.findall(r'\("POST", "/api/tempo/link"\)', src)), 1)
        self.assertEqual(routes[("GET", "/api/tempo")][1].__name__, "get_tempo")
        self.assertEqual(routes[("POST", "/api/tempo")][1].__name__, "set_tempo")

    def test_an_owner_switches_it_and_the_tempo_route_shows_it(self):
        out = self.api.set_link({"on": True}, {"id": "o", "role": "full"}, "127.0.0.1")
        self.assertTrue(out["link"]["on"])
        self.assertTrue(self.api.settings.data["link"]["on"])
        self.assertTrue(self.helper.on)
        self.assertIn("clock", out)
        self.assertEqual(self.api.get_tempo({}, {"role": "live"}, "127.0.0.1")["link"]["state"], "listening")
        self.assertIn("link", self.api.get_sound({}, {"role": "live"}, "127.0.0.1"))
        out = self.api.set_link({"on": False}, {"id": "o", "role": "full"}, "127.0.0.1")
        self.assertFalse(self.helper.on)
        self.assertEqual(out["link"]["state"], "off")

    def test_a_bad_body_is_refused(self):
        from pvj.api import ApiError
        for body in ({}, {"on": 1}, {"on": True, "more": 2}, [], {"bpm": 120}):
            with self.assertRaises(ApiError) as e:
                self.api.set_link(body, {"id": "o", "role": "full"}, "127.0.0.1")
            self.assertEqual(e.exception.status, 400, repr(body))

    def test_through_remote_support_off_yes_on_no(self):
        from pvj.api import ApiError
        self.api.support.is_remote = lambda client: client == TUNNEL
        helper_login = {"id": "r", "role": "full", "remote": True}
        with self.assertRaises(ApiError) as e:
            self.api.set_link({"on": True}, helper_login, TUNNEL)
        self.assertEqual(e.exception.status, 403)
        self.assertNotIn("on", self.helper.asked)
        self.api.settings.data["link"] = {"on": True}
        self.api.set_link({"on": False}, helper_login, TUNNEL)
        self.assertFalse(self.api.settings.data["link"]["on"])
        with self.assertRaises(ApiError) as e:
            self.api.set_link({"on": False}, {"id": "r", "role": "live", "remote": True}, TUNNEL)
        self.assertEqual(e.exception.status, 403)


class UnitAndInstallTest(unittest.TestCase):
    def setUp(self):
        from tests.test_units import load_units
        self.u = load_units()["pvj-link.service"]
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.sh = f.read()

    def test_its_sandbox_is_exactly_this(self):
        u = self.u
        self.assertEqual(u["User"], ["pvj-link"])
        self.assertEqual(u["CapabilityBoundingSet"], [""])
        self.assertNotIn("AmbientCapabilities", u)
        self.assertEqual(sorted(u["RestrictAddressFamilies"][0].split()), ["AF_INET", "AF_INET6", "AF_NETLINK", "AF_UNIX"])
        self.assertEqual(u["DeviceAllow"], ["/dev/null rw", "/dev/urandom r"])
        for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("ProtectHome", "yes"), ("PrivateTmp", "yes"),
                           ("DevicePolicy", "closed"), ("Restart", "always"), ("RuntimeDirectoryMode", "0750"), ("UMask", "0027"),
                           ("RestrictRealtime", "yes"), ("MemoryDenyWriteExecute", "yes"), ("LimitCORE", "0")):
            self.assertEqual(u[key], [value], key)
        self.assertNotIn("SupplementaryGroups", u)
        self.assertNotIn("PrivateUsers", u)            # it would hide the caller's uid from the peer check
        self.assertNotIn("PrivateNetwork", u)
        self.assertEqual(u["ExecStart"], ["@PVJ_DIR@/render/pvj-link"])
        self.assertEqual(u["ConditionPathExists"], ["@PVJ_DIR@/render/pvj-link"], "where it could not be built the unit is skipped, no failure")

    def test_it_is_kept_from_what_the_rest_of_the_box_has(self):
        hidden = " ".join(self.u["InaccessiblePaths"])
        for path in ("/var/lib/pvj", "/etc/pvj", "/run/pvj", "/run/pvj-sound", "/run/pvj-sysd", "/run/pvj-supportd"):
            self.assertRegex(hidden, r"(^| )-?%s( |$)" % re.escape(path))

    def test_the_installer_makes_the_account_before_the_unit_and_only_the_panel_joins_its_group(self):
        sh = self.sh
        self.assertIn("--gid pvj-link pvj-link", sh)
        self.assertLess(sh.index("--gid pvj-link pvj-link"), sh.index('install/pvj-link.service" > "$LINK_UNIT"'))
        self.assertEqual(re.findall(r"usermod -aG pvj-link (\S+)", sh), ["pvj-web"])
        self.assertIn("systemctl enable pvj-link.service", sh)
        self.assertIn('"$LINK_UNIT"', sh[sh.index("uninstall() {"):sh.index("\n}\n", sh.index("uninstall() {"))])

    def test_a_missing_package_never_fails_an_install(self):
        sh = self.sh
        block = sh[sh.index("link_need=()"):sh.index("# Optional: sound reactivity (D96)")]
        self.assertIn("g++", block)
        self.assertIn("ableton-link-dev", block)
        self.assertIn('[ "$OFFLINE" = 1 ]', block, "--offline only says so")
        self.assertIn("|| log", block, "a failed apt-get is logged and the install goes on")
        self.assertNotIn(" die ", block)
        build = sh[sh.index('rm -f "$RELEASE.new/render/pvj-link"'):sh.index('previous=""')]
        self.assertNotIn(" die ", build)
        self.assertIn("nice -n 10 timeout 1200 make", build)

    def test_the_image_has_the_packages_and_enables_the_unit(self):
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "00-packages-nr")) as f:
            pkgs = f.read().split()
        self.assertEqual({"g++", "ableton-link-dev", "libasio-dev"} - set(pkgs), set())
        with open(os.path.join(REPO, "image", "stage-pvj", "00-install-pvj", "01-run.sh")) as f:
            self.assertIn("pvj-link.service", f.read())


class SourceRulesTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "render", "link", "pvj-link.cpp")) as f:
            self.src = f.read()
        self.code = re.sub(r"//.*", "", self.src)

    def test_the_helper_never_sets_anything_on_the_session(self):
        for word in ("setTempo", "commitAppSessionState", "commitAudioSessionState", "requestBeatAtTime", "forceBeatAtTime", "setIsPlaying"):
            self.assertNotIn(word, self.code, word)

    def test_it_is_gpl_and_uses_the_quantum_of_the_bar(self):
        self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", self.src)
        self.assertIn("QUANTUM = %d.0" % clk.BAR_BEATS, self.src)

    def test_a_time_on_the_boxs_clock_is_never_links_own(self):
        # Link's clock on Linux is CLOCK_MONOTONIC_RAW; every record is stamped with CLOCK_MONOTONIC, read beside it.
        self.assertIn("CLOCK_MONOTONIC,", self.code)
        self.assertNotIn("CLOCK_MONOTONIC_RAW", self.code)
        self.assertIn("mLink.clock().micros()", self.code)

    def test_the_wait_before_joining_is_longer_than_links_margin(self):
        wait = float(re.search(r"JOIN_WAIT = ([0-9.]+);", self.code).group(1))
        self.assertGreater(wait, 0.5)

    def test_the_helper_stays_out_of_the_plain_build_of_the_renderer(self):
        with open(os.path.join(REPO, "render", "Makefile")) as f:
            mk = f.read()
        first = re.search(r"(?m)^([a-z][a-z-]*):", mk).group(1)
        self.assertEqual(first, "pvj-render", "plain make, and with it CI's jobs without the package, builds the renderer only")
        self.assertNotIn("pvj-link", mk.split("# pvj-link (D117)")[0])


if __name__ == "__main__":
    unittest.main()
