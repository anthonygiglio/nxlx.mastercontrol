# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Show stats on output (D133, pvj/statsoverlay.py): the ASS text and its escaping, the sampler, the show-safety rules
and the routes. The renderer's half (osd-overlay on pvj-render) is in tests/test_pvj_render.py, the panel's in
tests/ui/panel.test.js."""
import glob
import json
import os
import re
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from pvj import policy, statsoverlay as so, support, sysd
from pvj.player import Ipc, PlayerError
from tests.test_boxcare import Base as CareBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ERR = object()


class FakeIpc:
    """Stands in for pvj.player.Ipc: answers `request` and `batch` from a dict of properties."""

    def __init__(self, pid=100, render=None):
        self.pid, self.render = pid, render        # render None: classic mpv (no pvj-render property)
        self.props = {"path": "/media/Lumia 1.mp4", "hwdec-current": "drm-copy",
                      "video-params": {"w": 1920, "h": 1080, "pixelformat": "yuv420p"}, "estimated-vf-fps": 29.97,
                      "frame-drop-count": 0, "decoder-frame-drop-count": 0}
        self.sent, self.batches, self.overlay, self.fail, self.refuse = [], 0, None, None, False
        self.translate = None
        self.links = []

    def hold(self):
        link = FakeLink(self)
        self.links.append(link)
        return link

    def request(self, *cmd, wait=None, link=None):
        """Like mpv: an osd-overlay belongs to the connection that sent it. A plain request is a connection of its own that
        closes at once (pvj.player.Ipc), so on classic mpv (no pvj-render property) its overlay is gone as soon as it is made.
        pvj-render keeps its overlays itself."""
        self.sent.append(cmd)
        if self.fail:
            raise PlayerError(self.fail)
        if cmd[:2] == ("get_property", "pid"):
            return self.pid
        if cmd[:2] == ("get_property", "pvj-render"):
            if self.render is None:
                raise PlayerError("mpv: property not found")
            return self.render
        if cmd[0] == "osd-overlay":
            if self.refuse:
                raise PlayerError("mpv: invalid parameter")
            self.overlay = None if cmd[2] == "none" else cmd
            if link is None and self.render is None:
                self.overlay = None                        # classic mpv: the one-request connection closed
            return None
        raise PlayerError("mpv: invalid parameter")

    def batch(self, commands, wait=None):
        self.batches += 1
        if self.fail:
            raise PlayerError(self.fail)
        out = []
        for c in commands:
            name = c[1]
            v = self.pid if name == "pid" else (self.render if name == "pvj-render" else self.props.get(name, ERR))
            out.append((False, "property unavailable") if v is ERR or v is None else (True, v))
        return out


class FakeLink:
    """pvj.player.HeldIpc: one connection kept open. Closing it takes its overlay off on classic mpv."""

    def __init__(self, ipc):
        self.ipc, self.closed = ipc, False

    def request(self, *cmd):
        if self.closed:
            raise PlayerError("ipc error: the connection is closed")
        return self.ipc.request(*cmd, link=self)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.ipc.render is None:
                self.ipc.overlay = None


class FakeApi:
    def __init__(self, ipc):
        self.player = SimpleNamespace(ipc=ipc)
        self.mix = {"blackout": False}
        self.access = False
        self.events = []
        self.sysd = None

    def access_on_screen(self):
        return self.access

    def event(self, kind, text, who=None, urgent=None):
        self.events.append((kind, text, who))

    def _ip_json(self):
        return [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
                {"ifname": "eth0", "addr_info": [{"family": "inet6", "local": "fe80::1"}, {"family": "inet", "local": "192.168.0.169"}]}]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


RENDER = {"version": "0.12.0", "osd-overlay": True, "flips": 1000, "missed-vblanks": 2, "output-fps": 60.0}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pvj-stats-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("proc", "sys/class/thermal/thermal_zone0", "sys/devices/system/cpu/cpu0/cpufreq"):
            os.makedirs(os.path.join(self.tmp, d))
        self.cpu(100, 900)
        self.write("proc/meminfo", "MemTotal:        4000000 kB\nMemAvailable:    3000000 kB\n")
        self.write("sys/class/thermal/thermal_zone0/temp", "48250\n")
        self.ipc = FakeIpc()
        self.api = FakeApi(self.ipc)
        self.clock = Clock()
        self.s = so.StatsOverlay(self.api, clock=self.clock, proc=os.path.join(self.tmp, "proc"), sysfs=os.path.join(self.tmp, "sys"),
                                 log=lambda *_: None, threaded=False)
        self.bits = 0
        self.s.throttle_reader = lambda: self.bits
        self.s._name = lambda: "node-01"
        self.addCleanup(self.s._stop.set)

    def write(self, rel, text):
        with open(os.path.join(self.tmp, rel), "w") as f:
            f.write(text)

    def cpu(self, busy, idle):
        self.write("proc/stat", "cpu  %d 0 0 %d 0 0 0 0 0 0\ncpu0 1 1 1 1 0 0 0 0 0 0\n" % (busy, idle))

    def text(self):
        self.assertIsNotNone(self.ipc.overlay, "nothing is drawn")
        return self.ipc.overlay[3]

    def step(self, seconds=1.0):
        self.clock.t += seconds
        self.s.tick()


class EscapeTest(unittest.TestCase):
    def test_braces_and_backslashes_cannot_open_a_tag_or_a_block(self):
        evil = r"{\fs500}\N\an9 {\p1}"
        out = so.ass_escape(evil)
        self.assertNotRegex(out, r"(?<!\\)\{")             # every { has a backslash before it
        self.assertNotRegex(out, r"(?<!\\)\}")
        self.assertNotIn("\\N", out.replace("\\" + so.WORD_JOINER, ""))      # a backslash is never followed by N, a, f or p
        for tag in ("\\f", "\\a", "\\p", "\\N"):
            self.assertNotIn(tag, out)

    def test_control_characters_and_new_lines_become_a_space(self):
        self.assertEqual(so.ass_escape("a\nb\x00c\x1bd\x7f"), "a b c d ")
        self.assertEqual(so.ass_escape(None), "")
        self.assertEqual(so.ass_escape(12), "12")

    def test_the_whole_text_is_escaped_but_for_its_own_tags(self):
        text = so.ass_text(["node {\\b1}x", "second"], "tl")
        head, body = text.split("}", 1)
        self.assertTrue(head.startswith("{\\an7\\pos("))
        self.assertEqual(body.count("\\N"), 1)               # the one line break is ours
        self.assertIn("\\{", body)
        self.assertLessEqual(len(text), so.TEXT_MAX)

    def test_each_corner_has_its_own_alignment_and_anchor_inside_the_script(self):
        want = {"tl": (7, 12), "tr": (9, 1268), "bl": (1, 12), "br": (3, 1268)}
        for corner, (an, x) in want.items():
            m = re.match(r"\{\\an(\d)\\pos\((\d+),(\d+)\)", so.ass_text(["x"], corner))
            self.assertEqual((int(m.group(1)), int(m.group(2))), (an, x), corner)
            self.assertTrue(0 < int(m.group(3)) < so.PLAY_RES[1])
        self.assertIn("\\an7", so.ass_text(["x"], "nonsense"))      # an unknown corner is the default one

    def test_the_command_is_osd_overlay_in_our_id_at_720_lines(self):
        cmd = so.overlay_command(["x"], "br")
        self.assertEqual(cmd[:3], ("osd-overlay", so.OVERLAY_ID, "ass-events"))
        self.assertEqual(cmd[4:], (1280, 720))
        self.assertEqual(so.remove_command(), ("osd-overlay", so.OVERLAY_ID, "none", ""))
        self.assertIn("\\fnmonospace\\fs20", cmd[3])
        self.assertNotEqual(so.OVERLAY_ID, 0)

    def test_a_long_file_name_is_cut_in_the_middle(self):
        name = "a" * 30 + "-the-end.mp4"
        short = so.shorten(name, 20)
        self.assertEqual(len(short), 20)
        self.assertTrue(short.endswith("end.mp4") and "..." in short)
        self.assertEqual(so.shorten("short.mp4", 40), "short.mp4")


class WordsTest(unittest.TestCase):
    def test_throttling_in_plain_words(self):
        self.assertEqual(so.throttle_words(0), ([], []))
        now, since = so.throttle_words(0x50005)
        self.assertEqual(now, ["under-voltage now", "throttled now"])
        self.assertTrue(since[0].startswith("has throttled since boot"))
        self.assertEqual(so.throttle_words(0x2)[0], ["capped now"])
        self.assertEqual(so.throttle_words(0x8)[0], ["capped now"])               # the soft temperature limit is a cap too
        self.assertEqual(so.throttle_words(0x80000)[0], [])                      # since boot only: nothing now
        self.assertTrue(so.throttle_words(0x80000)[1])

    def test_a_cpu_under_its_top_speed_is_capped_only_when_busy_or_held(self):
        self.assertIsNone(so.cpufreq_capped(None, 1500000, 1500000, 90))
        self.assertFalse(so.cpufreq_capped(600000, 1500000, 1500000, 5))           # idle at the lowest speed: the governor, normal
        self.assertTrue(so.cpufreq_capped(600000, 1500000, 1500000, 95))           # busy and slow: capped
        self.assertTrue(so.cpufreq_capped(600000, 1500000, 1000000, 5))            # the kernel's own ceiling is lower: capped
        self.assertFalse(so.cpufreq_capped(1500000, 1500000, 1500000, 99))

    def test_decoder_words(self):
        self.assertEqual(so.decoder_words("drm-copy", {"w": 1920, "h": 1080, "pixelformat": "yuv420p"}), "drm-copy 1920x1080 yuv420p")
        self.assertEqual(so.decoder_words("no", {"w": 1280, "h": 720, "pixelformat": "yuv420p"}), "software 1280x720 yuv420p")
        self.assertEqual(so.decoder_words(None, None), "decoder: none yet")

    def test_the_lines_for_classic_and_for_the_new_renderer(self):
        base = {"name": "node-02", "ip": "192.168.0.174", "mode": "classic", "video_fps": 29.97, "drop_rate": 0.0, "decoder": "software 1280x720 yuv420p",
                "file": "clip.mp4", "cpu": 23, "mem": 41, "temp": 48.2, "throttle": "throttling: none now", "left": 840}
        lines = so.format_lines(base)
        self.assertEqual(len(lines), 6)
        self.assertIn("node-02", lines[0]); self.assertIn("192.168.0.174", lines[0]); self.assertIn("off in 14m", lines[0])
        self.assertIn("video 30.0 fps", lines[1]); self.assertIn("dropped 0.0/s", lines[1])
        self.assertEqual(lines[4], "cpu 23%  mem 41%  48.2 C")
        render = dict(base, mode="render", out_fps=59.9, out_hz=60.0, missed_rate=0.0, late=3, name="node-01")
        lines = so.format_lines(render)
        self.assertEqual(len(lines), 7)
        self.assertIn("out 59.9 of 60 fps", lines[1]); self.assertIn("late 3", lines[2])
        empty = so.format_lines({})
        self.assertIn("throttling: unknown", empty[-1])
        self.assertIn("cpu --", empty[-2])


class SamplerTest(Base):
    def test_first_frame_shows_the_node_the_clip_and_the_system(self):
        self.s.turn_on("tl", None, who="Studio laptop")
        t = self.text()
        for word in ("192.168.0.169", "classic mpv", "drm-copy 1920x1080 yuv420p", "Lumia 1.mp4", "mem 25%", "48.2 C", "throttling: none now"):
            self.assertIn(word, t.replace("\\" + so.WORD_JOINER, ""))
        self.assertNotIn("127.0.0.1", t)
        self.assertEqual(self.ipc.batches, 1)                       # one connection for all the questions

    def test_drops_and_the_cpu_are_rates_between_two_looks(self):
        self.s.turn_on()
        self.assertIn("cpu --", self.text())                         # no earlier look: no number
        self.ipc.props["frame-drop-count"], self.ipc.props["decoder-frame-drop-count"] = 3, 1
        self.cpu(150, 1000)                                          # +50 busy of +150 in all
        self.step(2.0)
        t = self.text()
        self.assertIn("dropped 2.0/s", t)                            # 4 frames in 2 seconds
        self.assertIn("cpu 33%", t)
        self.ipc.props["frame-drop-count"] = 0                       # a new clip resets the counters: no negative rate
        self.step()
        self.assertIn("dropped --/s", self.text())

    def test_nothing_playing_says_so(self):
        for k in ("path", "hwdec-current", "video-params", "estimated-vf-fps"):
            self.ipc.props.pop(k)
        self.s.turn_on()
        t = self.text()
        self.assertIn("no clip", t); self.assertIn("decoder: none yet", t); self.assertIn("video --", t)

    def test_the_new_renderer_is_read_from_its_property_and_the_shader_drops(self):
        self.ipc.render = dict(RENDER)
        self.ipc.props["pvj-shaders"] = [{"drops": 3}, {"drops": 4}, "junk"]
        self.s.turn_on()
        self.assertEqual(self.s.state()["renderer"], "new")
        self.ipc.render = dict(RENDER, flips=1060, **{"missed-vblanks": 4})
        self.step()
        t = self.text()
        self.assertIn("new renderer", t); self.assertIn("out 60.0 of 60 fps", t); self.assertIn("missed 2.0/s", t); self.assertIn("late 7", t)

    def test_the_helper_is_asked_every_five_seconds_not_every_second(self):
        calls = []
        self.s.throttle_reader = lambda: calls.append(1) or 0
        self.s.turn_on()
        for _ in range(11):
            self.step()
        self.assertEqual(len(calls), 3)                              # at 0, 5 (or 6) and 10 (or 12) seconds

    def test_a_throttled_node_says_so_in_words(self):
        self.bits = 0x50005
        self.s.turn_on()
        t = self.text()
        self.assertIn("under-voltage now", t); self.assertIn("throttled now", t); self.assertIn("has throttled since boot", t)

    def test_no_helper_is_unknown_not_a_crash(self):
        self.s.throttle_reader = lambda: None
        self.s.turn_on()
        self.assertIn("throttling: unknown", self.text())
        self.s.throttle_reader = lambda: (_ for _ in ()).throw(OSError("nope"))      # a reader that raises is caught by the helper call, not here
        # the real reader never raises: a missing or old helper is None
        api = FakeApi(self.ipc)
        api.sysd = SimpleNamespace(path="/nonexistent/sysd.sock", request=lambda m: {"ok": False, "error": "unknown command"})
        s = so.StatsOverlay(api, threaded=False)
        self.assertIsNone(s._ask_helper())
        api.sysd = None
        self.assertIsNone(s._ask_helper())

    def test_cpufreq_adds_capped_now_when_the_helper_is_missing(self):
        base = "sys/devices/system/cpu/cpu0/cpufreq/"
        self.write(base + "scaling_cur_freq", "600000\n"); self.write(base + "cpuinfo_max_freq", "1500000\n"); self.write(base + "scaling_max_freq", "1000000\n")
        self.s.throttle_reader = lambda: None
        self.s.turn_on()
        self.assertIn("throttling: unknown, capped now", self.text())

    def test_a_node_name_with_braces_cannot_break_the_overlay(self):
        self.s._name = lambda: r"{\fs500}x"
        self.s.turn_on()
        t = self.text()
        self.assertNotRegex(t.split("}", 1)[1], r"(?<!\\)\{")


class ShowSafetyTest(Base):
    def test_it_is_off_until_the_route_handler_turns_it_on(self):
        self.assertFalse(self.s.on)
        st = self.s.state()
        self.assertEqual((st["on"], st["lines"]), (False, []))
        self.s.tick()
        self.assertEqual(self.ipc.sent, [])                          # an overlay that is off asks the player nothing

    def test_it_is_not_turned_on_by_anything_but_the_one_route_handler(self):
        calls = []
        for path in glob.glob(os.path.join(REPO, "pvj", "*.py")):
            with open(path) as f:
                src = f.read()
            for m in re.finditer(r"\.turn_on\(", src):
                calls.append((os.path.basename(path), src[:m.start()].rsplit("\n", 1)[-1].strip()))
        self.assertEqual([c[0] for c in calls], ["api.py"], calls)          # one call, and it is in the route handler (below)
        with open(os.path.join(REPO, "pvj", "api.py")) as f:
            src = f.read()
        handler = src[src.index("def set_stats_overlay"):src.index("def get_renderer")]
        self.assertIn("self.stats.turn_on(", handler)
        for path in glob.glob(os.path.join(REPO, "pvj", "*.py")):       # and the flag itself is assigned only inside the module
            name = os.path.basename(path)
            with open(path) as f:
                text = f.read()
            if name != "statsoverlay.py":
                self.assertNotRegex(text, r"stats\.on\s*=[^=]", name)
                self.assertNotIn("stats._on", text, name)

    def test_nothing_is_written_to_settings_or_anywhere_else(self):
        with open(os.path.join(REPO, "pvj", "statsoverlay.py")) as f:
            src = f.read()
        self.assertNotRegex(src, r"settings\.|\.save\(|open\([^)]*,\s*[\"'][wa]|os\.(rename|replace)", "the state is memory only")

    def test_no_controller_or_guest_route_reaches_it(self):
        for table in (policy.LEGACY_LIVE, policy.GUEST):
            self.assertFalse([r for r in table if "stats" in r[1]])
        for name in ("osc.py", "midi.py", "dmx.py", "room.py", "scheduler.py", "presets.py", "autostart.py"):
            path = os.path.join(REPO, "pvj", name)
            if os.path.exists(path):
                with open(path) as f:
                    self.assertNotIn("stats_overlay", f.read(), name)
                with open(path) as f:
                    self.assertNotIn("stats-overlay", f.read(), name)

    def test_the_player_is_cleared_at_every_start_of_the_panel(self):
        self.ipc.overlay = ("osd-overlay", so.OVERLAY_ID, "ass-events", "left over", 1280, 720)
        self.assertTrue(self.s.reset_player(sleep=lambda _: None))
        self.assertIsNone(self.ipc.overlay)
        self.assertEqual(self.ipc.sent[-1], so.remove_command())

    def test_the_clearing_waits_for_a_player_that_is_not_up_yet(self):
        sleeps = []
        self.ipc.fail = "player is not running"
        self.assertFalse(self.s.reset_player(tries=3, delay=2.0, sleep=sleeps.append))
        self.assertEqual(sleeps, [2.0, 4.0, 8.0])           # a back-off, not a fixed wait
        self.ipc.fail = "mpv: invalid parameter"                      # a renderer from before: nothing to take off
        self.assertTrue(self.s.reset_player(sleep=lambda _: None))

    def test_the_clearing_keeps_trying_past_five_tries_and_stops_when_it_works(self):
        sleeps = []
        self.ipc.fail = "player is not running"
        self.ipc.overlay = ("osd-overlay", so.OVERLAY_ID, "ass-events", "left over", 1280, 720)

        def sleep(t):
            sleeps.append(t)
            if len(sleeps) == 9:
                self.ipc.fail = None                         # the player comes up after nine tries
        self.assertTrue(self.s.reset_player(sleep=sleep))
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0])      # capped at 30
        self.assertIsNone(self.ipc.overlay)

    def test_a_failed_removal_while_hidden_is_tried_again(self):
        self.ipc.render = dict(RENDER)                       # pvj-render keeps the text until it is told; classic loses it with the connection
        self.s.turn_on()
        self.api.access = True
        self.ipc.fail = "no reply from mpv"                  # the player does not take the removal
        self.step()
        self.assertIsNotNone(self.ipc.overlay)
        self.assertTrue(self.s._drawn)
        self.ipc.fail = None
        self.step()
        self.assertIsNone(self.ipc.overlay)
        self.assertFalse(self.s._drawn)

    def test_on_classic_mpv_the_overlay_is_drawn_through_one_connection_kept_open(self):
        # mpv removes an osd-overlay when the connection that made it closes; every plain Ipc request is a connection of its own
        # (node-02, 2026-11-11: the overlay was on according to the panel and never on the screen)
        self.s.turn_on()
        self.assertIsNotNone(self.ipc.overlay, "drawn, and still there after the request returned")
        self.assertEqual(len(self.ipc.links), 1)
        for _ in range(3):
            self.step()
        self.assertEqual(len(self.ipc.links), 1, "the same connection every second")
        self.assertFalse(self.ipc.links[0].closed)
        self.s.turn_off()
        self.assertTrue(self.ipc.links[0].closed, "ending the connection takes the text off on classic mpv")
        self.assertIsNone(self.ipc.overlay)

    def test_a_connection_that_breaks_is_replaced_and_the_text_comes_back(self):
        self.s.turn_on()
        first = self.ipc.links[0]
        first.close()                                         # mpv went away and came back, or the socket broke
        self.step()
        self.assertEqual(len(self.ipc.links), 2)
        self.assertIsNotNone(self.ipc.overlay)
        self.assertEqual(self.s._fails, 0)

    def test_hiding_for_a_pin_keeps_the_connection_and_the_text_returns(self):
        self.s.turn_on()
        self.api.access = True
        self.step()
        self.assertIsNone(self.ipc.overlay)
        self.api.access = False
        self.step()
        self.assertIsNotNone(self.ipc.overlay)
        self.assertEqual(len(self.ipc.links), 1)

    def test_on_the_new_renderer_off_sends_none_even_with_no_connection_open(self):
        self.ipc.render = dict(RENDER)
        self.s.turn_on()
        self.s._drop_link()
        self.ipc.overlay = ("osd-overlay", so.OVERLAY_ID, "ass-events", "x", 1280, 720)
        self.s.turn_off()
        self.assertIsNone(self.ipc.overlay)

    def test_off_then_on_at_once_always_has_a_thread(self):
        api = FakeApi(FakeIpc())
        s = so.StatsOverlay(api, proc=os.path.join(self.tmp, "proc"), sysfs=os.path.join(self.tmp, "sys"), log=lambda *_: None)
        s.throttle_reader = lambda: 0
        s.turn_on()
        for _ in range(30):
            s.turn_off()
            s.turn_on()
            self.assertTrue(s._thread.is_alive() and not s._stop.is_set(), "on with no thread")
        s.turn_off()

    def test_an_old_renderer_is_refused_in_plain_words(self):
        self.ipc.render = {"version": "0.11.0", "layers": 2}
        with self.assertRaises(so.StatsError) as e:
            self.s.turn_on()
        self.assertEqual(e.exception.status, 409)
        self.assertIn("too old", e.exception.message)
        self.assertIn("0.11.0", e.exception.message)
        self.assertFalse(self.s.on)
        self.assertEqual(self.api.events, [])

    def test_a_player_that_is_not_running_is_refused(self):
        self.ipc.fail = "player is not running"
        with self.assertRaises(so.StatsError) as e:
            self.s.turn_on()
        self.assertEqual(e.exception.status, 409)
        self.assertFalse(self.s.on)

    def test_a_player_that_refuses_the_first_frame_leaves_it_off(self):
        self.ipc.refuse = True
        with self.assertRaises(so.StatsError):
            self.s.turn_on()
        self.assertFalse(self.s.on)
        self.assertEqual(self.api.events, [])

    def test_corner_and_minutes_are_checked(self):
        for kw in ({"corner": "middle"}, {"corner": 1}, {"minutes": 0}, {"minutes": 241}, {"minutes": True}, {"minutes": "5"}, {"minutes": 1.5}):
            with self.assertRaises(so.StatsError, msg=str(kw)) as e:
                self.s.turn_on(**kw)
            self.assertEqual(e.exception.status, 400)
        self.assertFalse(self.s.on)
        self.s.turn_on("br", 240)
        self.assertEqual((self.s.corner, self.s.minutes, self.s.state()["seconds_left"]), ("br", 240, 240 * 60))
        self.s.turn_off()
        self.s.turn_on(minutes=1)
        self.assertEqual(self.s.state()["seconds_left"], 60)

    def test_the_timer_defaults_to_fifteen_minutes_and_ends_it(self):
        self.s.turn_on()
        self.assertEqual(self.s.state()["seconds_left"], 15 * 60)
        self.step(14 * 60)
        self.assertTrue(self.s.on)
        self.step(61)
        self.assertFalse(self.s.on)
        self.assertIsNone(self.ipc.overlay)                          # taken off the screen
        self.assertTrue(any("timer ran out" in e[1] for e in self.api.events), self.api.events)

    def test_a_new_call_re_arms_the_timer_and_logs_one_on(self):
        self.s.turn_on(minutes=2, who="A")
        self.step(100)
        self.s.turn_on(minutes=2, who="A")
        self.step(100)
        self.assertTrue(self.s.on)
        self.assertEqual(len([e for e in self.api.events if "turned on" in e[1]]), 1)

    def test_a_pin_or_a_code_on_the_screen_takes_it_off_and_it_comes_back(self):
        self.s.turn_on()
        self.assertIsNotNone(self.ipc.overlay)
        self.api.access = True
        self.step()
        self.assertIsNone(self.ipc.overlay)
        self.assertTrue(self.s.on)
        self.assertEqual(self.s.state()["hidden"], "a PIN or code is on the screen")
        batches = self.ipc.batches
        self.step()
        self.assertEqual(self.ipc.batches, batches, "while hidden nothing is sampled")
        self.api.access = False
        self.step()
        self.assertIsNotNone(self.ipc.overlay)
        self.assertIsNone(self.s.state()["hidden"])

    def test_a_question_about_the_pin_screen_that_fails_hides_it_too(self):
        self.s.turn_on()
        self.api.access_on_screen = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        self.step()
        self.assertIsNone(self.ipc.overlay)

    def test_a_blackout_takes_it_off_and_the_end_of_it_brings_it_back(self):
        self.s.turn_on()
        self.api.mix["blackout"] = True
        self.step()
        self.assertIsNone(self.ipc.overlay)
        self.assertEqual(self.s.state()["hidden"], "blackout")
        self.api.mix["blackout"] = False
        self.step()
        self.assertIsNotNone(self.ipc.overlay)

    def test_another_player_process_ends_it(self):
        self.s.turn_on(who="A")
        self.ipc.pid = 200                                           # a restart, a new screen mode, a renderer change, a fall back
        self.step()
        self.assertFalse(self.s.on)
        self.assertIsNone(self.ipc.overlay)
        self.assertTrue(any("restarted" in e[1] for e in self.api.events), self.api.events)
        self.step()                                                  # and it is not drawn again on the new player
        self.assertIsNone(self.ipc.overlay)

    def test_the_hook_for_a_change_ends_it_with_its_reason(self):
        self.s.turn_on()
        self.s.stop_if_on("the renderer was changed and the player restarted")
        self.assertFalse(self.s.on)
        self.assertIn("the renderer was changed", self.api.events[-1][1])
        n = len(self.api.events)
        self.s.stop_if_on("again")
        self.assertEqual(len(self.api.events), n)                    # off already: nothing to say

    def test_a_player_that_stops_answering_ends_it(self):
        self.s.turn_on()
        self.ipc.fail = "no reply from mpv"
        for _ in range(so.FAILS_TO_STOP + 1):
            self.step()
        self.assertFalse(self.s.on)
        self.assertTrue(any("did not answer" in e[1] for e in self.api.events), self.api.events)

    def test_the_event_lines_say_who_and_why(self):
        self.s.turn_on("tr", 30, who="Studio laptop")
        self.s.turn_off(who="Studio laptop")
        on, off = self.api.events
        self.assertEqual((on[0], on[2]), ("player", "Studio laptop"))
        self.assertIn("turned on", on[1]); self.assertIn("top right", on[1]); self.assertIn("30 minutes", on[1])
        self.assertIn("turned off", off[1]); self.assertEqual(off[2], "Studio laptop")
        self.s.turn_off()                                            # off when off: no line
        self.assertEqual(len(self.api.events), 2)

    def test_off_removes_the_text_and_a_drawing_in_flight_cannot_outlive_it(self):
        self.s.turn_on()
        self.s.turn_off()
        self.assertIsNone(self.ipc.overlay)
        self.s.tick()
        self.assertIsNone(self.ipc.overlay)

    def test_the_thread_exists_only_while_it_is_on(self):
        api = FakeApi(FakeIpc())
        s = so.StatsOverlay(api, proc=os.path.join(self.tmp, "proc"), sysfs=os.path.join(self.tmp, "sys"), log=lambda *_: None)
        s.throttle_reader = lambda: 0
        self.assertIsNone(s._thread)
        s.turn_on()
        self.assertTrue(s._thread.is_alive())
        thread = s._thread
        s.turn_off()
        thread.join(3)
        self.assertFalse(thread.is_alive())


class IpcBatchTest(unittest.TestCase):
    """The real Ipc.batch against a small mpv-like server on a socket."""

    def test_many_commands_one_connection_in_order_with_refusals(self):
        import socket
        import threading
        d = tempfile.mkdtemp(prefix="pvj-batch-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "s")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(5)
        self.addCleanup(srv.close)
        conns = []

        def serve():
            c, _ = srv.accept()
            conns.append(1)
            buf = b""
            while buf.count(b"\n") < 3:
                buf += c.recv(4096)
            reqs = [json.loads(l) for l in buf.splitlines()]
            for r in reversed(reqs):                                  # answered out of order: matched by request_id
                name = r["command"][1]
                reply = {"request_id": r["request_id"], "error": "success", "data": name.upper()} if name != "gone" else \
                    {"request_id": r["request_id"], "error": "property unavailable"}
                c.sendall(json.dumps(reply).encode() + b"\n")
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        out = Ipc(path, timeout=3).batch([("get_property", "a"), ("get_property", "gone"), ("get_property", "c")])
        self.assertEqual(out, [(True, "A"), (False, "property unavailable"), (True, "C")])
        self.assertEqual(len(conns), 1)

    def test_no_player_is_an_error_and_a_silent_one_times_out(self):
        d = tempfile.mkdtemp(prefix="pvj-batch-")
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(PlayerError) as e:
            Ipc(os.path.join(d, "none"), timeout=1).batch([("get_property", "a")])
        self.assertEqual(str(e.exception), "player is not running")
        self.assertEqual(Ipc(os.path.join(d, "none")).batch([]), [])


class RealMpvTest(unittest.TestCase):
    """Classic mpv itself (no screen): the player's own arguments are accepted with --load-stats-overlay=no, Ipc.batch works
    against it, and it takes the overlay and its removal. Skipped where there is no mpv (CI has one)."""

    def test_the_overlay_command_batch_and_arguments_work_on_mpv(self):
        import subprocess
        import time
        from pvj.player import Player
        mpv = shutil.which("mpv")
        if not mpv:
            self.skipTest("no mpv here")
        d = tempfile.mkdtemp(prefix="pvj-statsmpv-")
        self.addCleanup(shutil.rmtree, d, True)
        player = Player(mpv_bin=mpv, extra_args=["--vo=null", "--ao=null", "--no-config"], rundir=d)
        player.socket_path = os.path.join(d, "mpv.sock")
        args = player.mpv_command(windowed=True)
        self.assertIn("--load-stats-overlay=no", args)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        ipc = Ipc(os.path.join(d, "mpv.sock"), timeout=3)
        for _ in range(100):
            try:
                ipc.request("get_property", "pid")
                break
            except PlayerError:
                time.sleep(0.1)
        got = ipc.batch([("get_property", "pid"), ("get_property", "path"), ("get_property", "hwdec-current"), ("get_property", "pvj-render"),
                         ("get_property", "frame-drop-count")])
        self.assertEqual(got[0], (True, proc.pid))
        self.assertFalse(got[1][0], "nothing is playing: path is not there, said as a refusal and not an error")
        self.assertEqual(got[3], (False, "property not found"), "classic mpv has no pvj-render: how the overlay knows it is classic")
        ipc.request(*so.overlay_command(["node-02  192.168.0.174", "{\fs500}evil"], "br"))
        ipc.request(*so.remove_command())
        self.assertEqual(ipc.request("get_property", "pid"), proc.pid, "mpv is still there")


class HeldIpcTest(unittest.TestCase):
    def test_a_connection_that_stays_open_matches_answers_by_id_and_skips_events(self):
        import socket
        import threading
        from pvj.player import Ipc
        d = tempfile.mkdtemp(prefix="pvj-held-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "s")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(5)
        self.addCleanup(srv.close)
        seen = []

        def serve():
            c, _ = srv.accept()
            buf = b""
            while True:
                chunk = c.recv(4096)
                if not chunk:
                    seen.append("closed")
                    c.close()
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    r = json.loads(line)
                    seen.append(r["command"][0])
                    c.sendall(b'{"event":"idle"}\n')
                    err = "success" if r["command"][0] != "bad" else "invalid parameter"
                    c.sendall(json.dumps({"request_id": r["request_id"], "error": err, "data": r["command"][0].upper()}).encode() + b"\n")
        threading.Thread(target=serve, daemon=True).start()
        held = Ipc(path, timeout=3).hold()
        self.assertEqual(held.request("one"), "ONE")
        self.assertEqual(held.request("two"), "TWO")
        with self.assertRaises(PlayerError) as e:
            held.request("bad")
        self.assertEqual(str(e.exception), "mpv: invalid parameter")
        self.assertEqual(held.request("three"), "THREE")           # a refusal does not end the connection
        held.close()
        for _ in range(50):
            if seen[-1] == "closed":
                break
            __import__("time").sleep(0.05)
        self.assertEqual(seen, ["one", "two", "bad", "three", "closed"], "one connection for all of them, closed by close()")
        with self.assertRaises(PlayerError):
            held.request("four")

    def test_no_player_is_the_usual_error(self):
        from pvj.player import Ipc
        d = tempfile.mkdtemp(prefix="pvj-held-")
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(PlayerError) as e:
            Ipc(os.path.join(d, "none")).hold()
        self.assertEqual(str(e.exception), "player is not running")


class RouteTest(CareBase):
    """Through the real Api: who may call it, what it answers, what it writes."""

    def setUp(self):
        super().setUp()
        self.ipc = FakeIpc()
        self.api.player.ipc = self.ipc
        self.api.stats._threaded = False
        self.api.stats.throttle_reader = lambda: 0
        self.api.stats.addresses_reader = lambda: ["192.168.0.169"]
        self.addCleanup(self.api.stats.turn_off, quiet=True)

    def test_the_owner_turns_it_on_and_off_and_reads_it(self):
        st, body = self.h("GET", "/api/stats-overlay", None, self.full_dev)
        self.assertEqual((st, body["on"]), (200, False))
        st, body = self.h("POST", "/api/stats-overlay", {"on": True, "corner": "br", "minutes": 5}, self.full_dev)
        self.assertEqual(st, 200, body)
        self.assertEqual((body["on"], body["corner"], body["minutes"], body["renderer"]), (True, "br", 5, "classic"))
        self.assertIsNotNone(self.ipc.overlay)
        self.assertIn("\\an3", self.ipc.overlay[3])
        self.assertTrue(self.h("GET", "/api/stats-overlay", None, self.full_dev)[1]["on"])
        st, body = self.h("POST", "/api/stats-overlay", {"on": False}, self.full_dev)
        self.assertEqual((st, body["on"]), (200, False))
        self.assertIsNone(self.ipc.overlay)
        self.assertEqual(self.api.stats.corner, so.DEFAULT_CORNER)
        self.assertEqual(self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)[1]["corner"], "tl")

    def test_the_events_name_the_device(self):
        events = []
        self.api.event = lambda kind, text, who=None, urgent=None: events.append((kind, text, who))
        self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)
        self.h("POST", "/api/stats-overlay", {"on": False}, self.full_dev)
        self.assertEqual([e[2] for e in events], ["Studio laptop", "Studio laptop"])

    def test_bad_bodies_are_400_and_leave_it_off(self):
        for body in ({}, {"on": "yes"}, {"on": 1}, {"on": True, "corner": "x"}, {"on": True, "minutes": 0}, {"on": True, "minutes": 241},
                     {"on": True, "extra": 1}, {"corner": "tl"}):
            st, out = self.h("POST", "/api/stats-overlay", body, self.full_dev)
            self.assertEqual(st, 400, (body, out))
        self.assertFalse(self.api.stats.on)

    def test_a_player_that_is_not_there_is_a_409_with_words(self):
        self.ipc.fail = "player is not running"
        st, out = self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)
        self.assertEqual(st, 409)
        self.assertIn("not running", out["error"])

    def test_only_the_owner_reaches_it(self):
        for dev in (self.live_dev, self.view_dev):
            for method in ("GET", "POST"):
                st, _ = self.h(method, "/api/stats-overlay", {"on": True}, dev)
                self.assertEqual(st, 403, (method, dev.get("role")))
        self.assertFalse(self.api.stats.on)

    def test_a_support_session_may_read_and_not_turn_it_on(self):
        # tests.test_support_reach holds every refused route to a real remote session; here, that this one is on its list
        self.assertIn(("POST", "/api/stats-overlay"), support.REMOTE_DENY)
        self.assertNotIn(("GET", "/api/stats-overlay"), support.REMOTE_DENY)

    def test_a_change_of_renderer_or_screen_mode_ends_it(self):
        self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)
        self.api._renderer_call(lambda: None)
        self.assertFalse(self.api.stats.on)
        self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)
        self.api._screen_call(lambda: None)
        self.assertFalse(self.api.stats.on)

    def test_it_is_in_no_file_no_export_and_no_import_can_turn_it_on(self):
        before = self.on_disk()
        self.h("POST", "/api/stats-overlay", {"on": True}, self.full_dev)
        self.assertEqual(self.on_disk(), before, "turning it on writes nothing")
        blob = json.dumps(self.settings.data)
        self.assertNotIn("stats", blob.lower())
        file = self.export()
        self.assertNotIn("stats", json.dumps(file).lower())                  # not exported, so not carried to another node
        self.h("POST", "/api/stats-overlay", {"on": False}, self.full_dev)

        def inject(node, depth=0):
            if isinstance(node, dict) and depth < 4:
                node["stats_overlay"] = {"on": True, "corner": "tl", "minutes": 240}
                node["stats"] = {"on": True}
                node["show_stats"] = True
                for v in list(node.values()):
                    inject(v, depth + 1)
        inject(file)
        st, out = self.send(file)
        self.assertEqual(st, 400, out)                                       # the whole file is refused, with the keys named
        self.assertIn("stats", out["error"])
        self.assertFalse(self.api.stats.on)
        self.assertIsNone(self.ipc.overlay)
        self.assertNotIn("stats", json.dumps(self.settings.data).lower(), "an unknown key is not kept either")
        self.assertEqual(self.send(self.export())[0], 200)                     # and the box's own export comes back as it is
        self.assertFalse(self.api.stats.on)

    def test_a_scene_or_a_preset_or_the_schedule_is_not_a_way_in(self):
        for path, body in (("/api/room/scene", {"stats_overlay": True}), ("/api/presets", {"stats": True}), ("/api/schedule", {"stats_overlay": {"on": True}}),
                           ("/api/mix", {"stats_overlay": True}), ("/api/control", {"stats": True})):
            self.h("POST", path, body, self.full_dev)
        self.assertFalse(self.api.stats.on)


class SysdThrottledTest(unittest.TestCase):
    def make(self, out="throttled=0x50005\n", code=0):
        self.calls = []

        def run(argv, **kw):
            self.calls.append(argv)
            import subprocess
            return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")
        return sysd.SysService(runner=run, log=lambda *_: None, schedule=lambda d, fn: None)

    def test_the_word_comes_back_as_a_number_from_a_fixed_command(self):
        s = self.make()
        self.assertEqual(s.handle({"cmd": "throttled", "argv": ["rm", "-rf", "/"]}), {"ok": True, "bits": 0x50005})
        self.assertEqual(self.calls, [["vcgencmd", "get_throttled"]])           # nothing from the caller is in the command

    def test_a_node_without_the_firmware_tool_or_with_odd_output_is_not_ok(self):
        for out, code in (("", 1), ("throttled=0xZZ\n", 0), ("Command not registered\n", 0), ("throttled=0x1; rm -rf /\n", 0)):
            self.assertFalse(self.make(out, code).handle({"cmd": "throttled"})["ok"], out)

    def test_an_old_helper_says_unknown_command(self):
        # the sampler turns this into "throttling: unknown"
        self.assertEqual(sysd.SysService(log=lambda *_: None).handle({"cmd": "nonsense"}), {"ok": False, "error": "unknown command"})


if __name__ == "__main__":
    unittest.main()
