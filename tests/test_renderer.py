# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Which program draws (pvj/renderer.py, D95): the choice, pvj-render's arguments, the supervisor in the player's
service and its fallback to classic mpv, the report read as untrusted data, and the panel's routes.

pvj-render itself is tested in tests/test_pvj_render.py. Here a stand-in helper (a small Python program that answers
on the socket as pvj-render does, and can be told to fail in each way) stands in for it, so these run everywhere."""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import boxcare, renderer, screenmode
from pvj.player import Ipc, Player, PlayerError

# The stand-in: answers get_property pid with its own pid (and quit) on --socket=PATH, as pvj-render does. What it
# does is read from STANDIN_MODE: "ok" (a quit ends it with 0), "die:SECONDS:CODE" (answer, then end with CODE),
# "silent" (never answer), "exit:CODE" (end at once), "quit-after:SECONDS" (end with 0 as if quit was asked),
# "term" (never takes a quit; SIGTERM ends it), "deaf" (answers the first question, then hears nothing more and
# ignores SIGTERM: only SIGKILL ends it, as a jammed pvj-render on the first Pi run).
STANDIN = textwrap.dedent('''\
    import json, os, signal, socket, sys, time
    path = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--socket=")][0]
    with open(os.environ["STANDIN_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    mode = os.environ.get("STANDIN_MODE", "ok")
    if mode.startswith("exit:"):
        sys.exit(int(mode.split(":")[1]))
    if mode == "deaf":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.listen(4)
    s.settimeout(0.1)
    end, code, answered = None, 0, 0
    if mode.startswith("die:"):
        end = time.monotonic() + float(mode.split(":")[1]); code = int(mode.split(":")[2])
    if mode.startswith("quit-after:"):
        end = time.monotonic() + float(mode.split(":")[1]); code = 0
    if mode == "deaf":
        end = time.monotonic() + 120        # never outlives a broken test run by long
    while end is None or time.monotonic() < end:
        if mode == "deaf" and answered:
            time.sleep(0.1); continue
        try:
            c, _ = s.accept()
        except socket.timeout:
            continue
        if mode == "silent":
            c.close(); continue
        line = c.makefile().readline()
        req = json.loads(line)
        if req.get("command") == ["quit"] and mode == "term":
            c.close(); continue
        c.sendall((json.dumps({"request_id": req["request_id"], "error": "success", "data": os.getpid()}) + "\\n").encode())
        c.close()
        answered += 1
        if req.get("command") == ["quit"] and mode == "ok":
            sys.exit(0)
    sys.exit(code)
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pvj-renderer-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dir]))
        self.standin = os.path.join(self.dir, "pvj-render")
        with open(self.standin, "w") as f:
            f.write("#!%s\n%s" % (sys.executable, STANDIN))
        os.chmod(self.standin, 0o755)
        self.calls = os.path.join(self.dir, "calls.txt")
        self.env = {"PVJ_RENDER_BIN": self.standin}
        self.sock = os.path.join(self.dir, "player.sock")
        self.lines = []

    def runs(self):
        try:
            with open(self.calls) as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def supervisor(self, mode, **kw):
        os.environ["STANDIN_MODE"], os.environ["STANDIN_LOG"] = mode, self.calls
        self.addCleanup(os.environ.pop, "STANDIN_MODE", None)
        self.addCleanup(os.environ.pop, "STANDIN_LOG", None)
        cmd = renderer.command(self.sock, ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"],
                               "auto", "pi4", self.env)
        kw.setdefault("start_seconds", 5.0)
        sup = renderer.Supervisor(cmd, self.sock, self.dir, "auto", log=self.lines.append, **kw)
        self.addCleanup(sup._end)
        return sup

    def report(self):
        return renderer.clean_report(screenmode.read_json(os.path.join(self.dir, renderer.REPORT)))


class FinalRuleTest(unittest.TestCase):
    """Automatic for the final pass (D108): the rule alone, driven with made-up numbers."""
    HZ = 60.0

    def run_rule(self, steps, state=None, start=0.0, step=0.5):
        """steps: (seconds, missed a second, load). Returns (the state, [(when, final)] at each change)."""
        st, now, missed, seen = state or {"final": "full"}, start, 0.0, []
        for seconds, rate, load in steps:
            for _ in range(int(round(seconds / step))):
                before = st["final"]
                st = renderer.final_rule(st, now, int(missed), load, self.HZ)
                if st["final"] != before:
                    seen.append((now, st["final"]))
                now += step
                missed += rate * step
        return st, seen

    def test_a_box_with_room_stays_at_full_size(self):
        st, seen = self.run_rule([(120, 0, 5), (120, 2.9, 5)])       # just under 5 in 100 for two minutes
        self.assertEqual((st["final"], seen), ("full", []))

    def test_more_than_5_in_100_missed_over_2_seconds_goes_to_720(self):
        st, seen = self.run_rule([(10, 0, 5), (10, 12.9, 5)])
        self.assertEqual(seen[0][1], 720)
        self.assertLessEqual(seen[0][0] - 10, renderer.BUSY_SECONDS + 2.0, "within a window or two")
        self.assertIn("the box was busy", st["why"])
        self.assertEqual(st["load"], 5)

    def test_one_stall_under_the_share_is_not_busy(self):
        # 5 vblanks in one instant: 4 in 100 of a 2 s window
        st = {"final": "full"}
        st = renderer.final_rule(st, 0.0, 0, 3, self.HZ)
        st = renderer.final_rule(st, 2.0, 5, 3, self.HZ)
        self.assertEqual(st["final"], "full")

    def test_nothing_drawn_is_never_busy(self):
        st, seen = self.run_rule([(30, 30, 0)])
        self.assertEqual((st["final"], seen), ("full", []))

    def test_the_same_load_holds_720_however_clean_it_runs(self):
        st, seen = self.run_rule([(5, 13, 5), (600, 0, 5)])
        self.assertEqual((st["final"], len(seen)), (720, 1), "no trial at full size, no flap")

    def test_a_heavier_load_holds_720(self):
        st, seen = self.run_rule([(5, 13, 3), (120, 0, 4)])
        self.assertEqual((st["final"], len(seen)), (720, 1))

    def test_a_lighter_load_goes_back_after_5_seconds_and_at_least_30_after_the_switch(self):
        st, seen = self.run_rule([(5, 13, 5), (40, 0, 4)])
        down, up = seen
        self.assertEqual((down[1], up[1]), (720, "full"))
        self.assertGreaterEqual(up[0] - down[0], renderer.HOLD_SECONDS)
        self.assertLess(up[0] - down[0], renderer.HOLD_SECONDS + 1.5, "the hold was the longer of the two")
        # lighter long after the switch: the 5 seconds decide
        st, seen = self.run_rule([(5, 13, 5), (60, 0, 5), (20, 0, 4)])
        self.assertEqual(seen[1][1], "full")
        self.assertAlmostEqual(seen[1][0], 65 + renderer.LIGHTER_SECONDS, delta=0.6)

    def test_a_load_that_comes_back_starts_the_5_seconds_again(self):
        st, seen = self.run_rule([(40, 13, 5), (4, 0, 4), (2, 0, 5), (4, 0, 4)])
        self.assertEqual((st["final"], len(seen)), (720, 1))
        st, seen = self.run_rule([(1, 0, 4)] * 3, state=st, start=100.0)
        self.assertEqual((st["final"], seen[-1][1] if seen else None), ("full", "full"))

    def test_it_cannot_flap_on_a_load_that_stays(self):
        # busy at 5, lighter at 4 and still busy there, lighter at 3: every way back needs a load lighter than the last
        st, seen = self.run_rule([(5, 13, 5), (40, 13, 4), (40, 13, 4), (300, 13, 4)])
        self.assertEqual([f for _, f in seen], [720, "full", 720], "down, back once for the lighter load, and down to stay")
        self.assertEqual((st["final"], st["load"]), (720, 4))

    def test_the_counter_starting_again_is_no_jump(self):
        st = {"final": "full"}
        st = renderer.final_rule(st, 0.0, 900, 5, self.HZ)
        st = renderer.final_rule(st, 3.0, 0, 5, self.HZ)
        self.assertEqual(st["final"], "full")

    def test_the_rule_changes_only_what_it_returns(self):
        st = {"final": "full", "t0": 0.0, "m0": 0}
        renderer.final_rule(st, 3.0, 100, 5, self.HZ)
        self.assertEqual(st, {"final": "full", "t0": 0.0, "m0": 0})


class FakeRender:
    """What the supervisor's watch asks of pvj-render: its counters, and pvj-final."""

    def __init__(self):
        self.state = {"missed-vblanks": 0, "load": 5, "output-fps": 60.0, "final-lines": 0, "final-why": ""}
        self.asked, self.refuse, self.down = [], False, False

    def request(self, *command):
        if self.down:
            raise PlayerError("no reply from mpv")
        if command[0] == "pvj-final":
            if self.refuse:
                raise PlayerError("mpv: invalid parameter")
            self.asked.append(command[1])
            self.state["final-lines"] = 720 if command[1] == 720 else 0
            return None
        return dict(self.state)


class FinalWatchTest(Base):
    def watched(self, cmd_final="--final=auto"):
        now = [0.0]
        sup = renderer.Supervisor(["pvj-render", cmd_final], self.sock, self.dir, "auto", log=self.lines.append, clock=lambda: now[0])
        sup._final_begin()
        fake = sup.final_ipc = FakeRender()
        return sup, fake, now

    def tick(self, sup, fake, now, seconds, rate=0.0):
        for _ in range(int(seconds * 2)):
            now[0] += 0.5
            fake.state["missed-vblanks"] += int(rate / 2)
            sup._final_tick()

    def test_busy_then_lighter_tells_the_renderer_and_the_report(self):
        sup, fake, now = self.watched()
        self.tick(sup, fake, now, 6, 14)
        self.assertEqual(fake.asked, [720])
        self.tick(sup, fake, now, 1)
        rep = self.report()
        self.assertEqual(rep["final"], 720)
        self.assertIn("the box was busy", rep["final_why"])
        fake.state["load"] = 4
        self.tick(sup, fake, now, 40)
        self.assertEqual(fake.asked, [720, "full"])
        self.assertEqual((self.report()["final"], self.report()["final_why"]), ("full", ""))

    def test_a_refusal_at_the_start_is_said_and_the_watch_ends(self):
        sup, fake, now = self.watched()
        fake.state["final-why"] = "the display's primary plane was not found"
        self.tick(sup, fake, now, 10, 30)
        self.assertEqual(fake.asked, [])
        self.assertFalse(sup.final_watch)
        rep = self.report()
        self.assertEqual((rep["final"], rep["final_why"]), ("full", "the display's primary plane was not found"))

    def test_a_refused_switch_stays_full_and_says_so(self):
        sup, fake, now = self.watched()
        fake.refuse = True
        self.tick(sup, fake, now, 10, 30)
        self.assertFalse(sup.final_watch)
        self.assertEqual(self.report()["final_why"], "pvj-render did not take the change")

    def test_a_silent_renderer_is_not_this_watchs_business(self):
        sup, fake, now = self.watched()
        fake.down = True
        self.tick(sup, fake, now, 10)
        self.assertTrue(sup.final_watch)

    def test_720_chosen_only_reports(self):
        sup, fake, now = self.watched("--final=720")
        fake.state["final-lines"] = 720
        self.tick(sup, fake, now, 40, 30)
        self.assertEqual(fake.asked, [])
        self.assertEqual((self.report()["final"], self.report()["final_why"]), (720, ""))

    def test_full_chosen_has_no_watch(self):
        cmd = renderer.command(self.sock, [], "auto", "pi4", self.env, None, "full")
        self.assertFalse(any(a.startswith("--final") for a in cmd))
        sup = renderer.Supervisor(cmd, self.sock, self.dir, "auto", final="full")
        sup._final_begin()
        self.assertFalse(sup.final_watch)
        sup._final_tick()               # asks nothing: the socket does not exist here


class ChoiceTest(Base):
    def test_the_setting_is_cleaned(self):
        self.assertEqual(renderer.clean(None), {"use": "classic", "detail": "auto"})
        self.assertEqual(renderer.clean({"use": "new", "detail": 720}), {"use": "new", "detail": 720})
        for bad in ({"use": "NEW"}, {"use": True}, {"detail": True}, {"detail": 1080}, {"detail": "540"}, [], "new"):
            self.assertEqual(renderer.clean(bad), renderer.DEFAULT, bad)

    def test_the_final_setting_is_cleaned(self):
        self.assertEqual(renderer.clean({"final": "full"}), dict(renderer.DEFAULT, final="full"))
        self.assertEqual(renderer.clean({"final": 720}), dict(renderer.DEFAULT, final=720))
        for bad in ("auto", 720.0, "720", True, 1, 540, None, ["full"]):
            self.assertEqual(renderer.clean({"final": bad}), renderer.DEFAULT, bad)

    def test_the_final_argument(self):
        screen = ["--drm-mode=1920x1080@60.00"]
        for board, final, want in (("pi4", "auto", "--final=auto"), ("pi3", "auto", "--final=auto"), ("pi5", "auto", None), ("x86", "auto", None),
                                   ("pi4", 720, "--final=720"), ("pi5", 720, "--final=720"), ("x86", 720, "--final=720"),
                                   ("pi4", "full", None), ("x86", "full", None), ("pi4", None, None)):
            self.assertEqual(renderer.final_arg(final, board), want, (board, final))
            cmd = renderer.command("/run/s", screen, "auto", board, self.env, None, final)
            self.assertEqual([a for a in cmd if a.startswith("--final")], [want] if want else [], (board, final))

    def test_the_arguments_are_the_screens_and_the_detail(self):
        screen = ["--drm-device=/dev/dri/card1", "--drm-connector=HDMI-A-1", "--drm-mode=1920x1080@60.00"]
        self.assertEqual(renderer.command("/run/s", screen, "auto", "pi4", self.env),
                         [self.standin, "--socket=/run/s"] + screen + ["--detail=540", "--fast", "--sources=4"])
        self.assertEqual(renderer.command("/run/s", screen[2:], "auto", "x86", self.env),
                         [self.standin, "--socket=/run/s", "--drm-mode=1920x1080@60.00", "--detail=output", "--sources=4"])
        self.assertEqual(renderer.command("/run/s", [], 720, "pi5", self.env)[-2], "--detail=720")
        self.assertEqual(renderer.command("/run/s", ["--vo=gpu", "--drm-mode=3"], "output", "pi3", self.env),
                         [self.standin, "--socket=/run/s", "--drm-mode=3", "--detail=output", "--fast", "--sources=2"])

    def chosen(self, wish, extra=("--vo=gpu", "--gpu-context=drm"), windowed=False, env=None):
        state = os.path.join(self.dir, "state")
        os.makedirs(state, exist_ok=True)
        if wish is not None:
            screenmode.write_json(os.path.join(state, renderer.WANTED), wish)
        env = dict(self.env if env is None else env, PVJ_STATE_DIR=state)
        with mock.patch.object(renderer.paths, "state_dir", lambda e=None: state):
            return renderer.chosen(self.dir, list(extra), windowed, env)

    def test_classic_unless_new_is_wished_and_possible(self):
        self.assertEqual(self.chosen(None), ("classic", "auto", ""))
        self.assertEqual(self.chosen({"use": "classic"}), ("classic", "auto", ""))
        self.assertEqual(self.chosen({"use": "new", "detail": 720}), ("new", 720, ""))
        self.assertEqual(self.chosen({"use": "new"}, extra=("--vo=gpu",))[0], "classic", "a desktop: no DRM")
        self.assertIn("desktop", self.chosen({"use": "new"}, windowed=True)[2])
        self.assertIn("not built", self.chosen({"use": "new"}, env={"PVJ_RENDER_BIN": os.path.join(self.dir, "none")})[2])
        os.chmod(self.standin, 0o644)
        self.assertIn("not built", self.chosen({"use": "new"})[2], "not executable")


class SupervisorTest(Base):
    def test_a_good_start_reports_new_and_a_quit_ends_the_service(self):
        sup = self.supervisor("quit-after:1.5")
        with self.assertRaises(SystemExit) as e:
            sup.run()
        self.assertEqual(e.exception.code, 0)
        self.assertEqual(len(self.runs()), 1)
        rep = self.report()
        self.assertEqual((rep["chosen"], rep["drawing"], rep["why"]), ("new", "new", ""))
        self.assertEqual(rep["pid"], os.getpid())
        self.assertIn("--detail=540", self.runs()[0])

    def test_a_program_that_ends_at_once_falls_back_at_once(self):
        why = self.supervisor("exit:2").run()
        self.assertEqual(why, "pvj-render did not start (exit 2)")
        self.assertEqual(len(self.runs()), 1)

    def test_a_program_that_never_answers_falls_back(self):
        sup = self.supervisor("silent", start_seconds=1.5)
        why = sup.run()
        self.assertEqual(why, "pvj-render did not answer within 1 seconds")
        self.assertIsNotNone(sup.proc.poll(), "the silent one was ended")

    def test_one_end_is_started_again_and_two_in_a_row_fall_back(self):
        why = self.supervisor("die:0.8:3").run()
        self.assertEqual(why, "pvj-render ended twice in a row (exit 3)")
        self.assertEqual(len(self.runs()), 2)
        self.assertTrue(any("starting it once more" in line for line in self.lines), self.lines)

    def test_an_end_after_a_long_run_is_not_in_a_row(self):
        clock = [0.0]
        sup = self.supervisor("die:0.5:3", healthy_seconds=100, clock=lambda: clock[0])
        real_wait = subprocess.Popen.wait

        def wait(proc, timeout=None):
            code = real_wait(proc, timeout)
            clock[0] += 101             # every run "lasted" longer than the healthy time
            return code
        with mock.patch.object(subprocess.Popen, "wait", wait):
            starts = []
            sup.spawn = lambda argv: starts.append(1) or (subprocess.Popen(argv) if len(starts) < 4 else (_ for _ in ()).throw(OSError(2, "enough")))
            why = sup.run()
        self.assertEqual(len(starts), 4, "started again after each long run, never fallen back for them")
        self.assertIn("could not be started", why)

    def test_systemds_stop_is_passed_on_and_ends_the_service(self):
        sup = self.supervisor("ok")
        import threading
        t = threading.Timer(1.5, sup._stop)
        t.start()
        self.addCleanup(t.cancel)
        with self.assertRaises(SystemExit) as e:
            sup.run()
        self.assertEqual(e.exception.code, 0)
        self.assertIsNotNone(sup.proc.poll())

    def test_a_missing_program_falls_back(self):
        sup = self.supervisor("ok")
        sup.cmd[0] = os.path.join(self.dir, "nothing-here")
        self.assertIn("could not be started", sup.run())

    def test_systemds_stop_kills_a_helper_that_ignores_sigterm(self):
        sup = self.supervisor("deaf")
        import threading
        t = threading.Timer(1.5, sup._stop)
        t.start()
        self.addCleanup(t.cancel)
        done = {}

        def run():
            try:
                done["why"] = sup.run()
            except SystemExit as e:
                done["exit"] = e.code
        r = threading.Thread(target=run, daemon=True)
        start = time.monotonic()
        r.start()
        r.join(1.5 + renderer.STOP_SECONDS + 3)
        if r.is_alive():
            sup.proc.kill()
            r.join(10)
            self.fail("a helper that ignores SIGTERM was left running after systemd's stop")
        self.assertEqual(done, {"exit": 0})
        self.assertEqual(sup.proc.poll(), -signal.SIGKILL)
        self.assertLess(time.monotonic() - start, 1.5 + renderer.STOP_SECONDS + 3, "never left to systemd's 90 seconds")


class SwitchTest(Base):
    """A change of the wish while pvj-render draws (the Owner chose classic again): the supervisor ends pvj-render
    itself, asked first, then SIGTERM, then SIGKILL, and ends the service, whether or not pvj-render answers."""

    def run_switch(self, mode, wish={"use": "classic", "detail": "auto"}):
        import threading
        state = os.path.join(self.dir, "state")
        os.makedirs(state, exist_ok=True)
        wanted = os.path.join(state, renderer.WANTED)
        screenmode.write_json(wanted, {"use": "new", "detail": "auto"})
        sup = self.supervisor(mode, wanted=wanted)
        done = {}

        def run():
            try:
                done["why"] = sup.run()
            except SystemExit as e:
                done["exit"] = e.code
        t = threading.Thread(target=run, daemon=True)
        t.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any("is drawing" in line for line in self.lines):
            time.sleep(0.05)
        self.assertTrue(any("is drawing" in line for line in self.lines), self.lines)
        time.sleep(0.5)
        screenmode.write_json(wanted, wish)
        start = time.monotonic()
        t.join(renderer.WATCH_SECONDS + renderer.QUIT_SECONDS + renderer.TERM_SECONDS + 5)
        took = time.monotonic() - start
        if t.is_alive():
            sup.stopping = True
            sup._end()
            t.join(10)
            self.fail("the change of the wish did not end pvj-render (%s)" % self.lines)
        return done, sup, took

    def test_a_helper_that_never_answers_is_killed_and_the_service_ends(self):
        done, sup, took = self.run_switch("deaf")
        self.assertEqual(done, {"exit": 0}, "the service ends; systemd starts it again with the new wish")
        self.assertEqual(sup.proc.poll(), -signal.SIGKILL)
        self.assertGreaterEqual(took, renderer.QUIT_SECONDS + renderer.TERM_SECONDS - 0.5, "asked, then SIGTERM, first")
        self.assertLess(took, renderer.WATCH_SECONDS + renderer.QUIT_SECONDS + renderer.TERM_SECONDS + 2)
        rep = self.report()
        self.assertEqual((rep["chosen"], rep["drawing"]), ("classic", "classic"))
        self.assertIn("SIGKILL", rep["switched"])
        self.assertIn("its socket did not answer", rep["switched"])
        self.assertTrue(any("switched from pvj-render" in line for line in self.lines), self.lines)

    def test_a_helper_that_takes_no_quit_gets_sigterm(self):
        done, sup, took = self.run_switch("term")
        self.assertEqual(done, {"exit": 0})
        self.assertEqual(sup.proc.poll(), -signal.SIGTERM)
        self.assertIn("SIGTERM", self.report()["switched"])
        self.assertNotIn("SIGKILL", self.report()["switched"])

    def test_a_helper_that_answers_is_asked_to_quit_and_nothing_more(self):
        done, sup, took = self.run_switch("ok")
        self.assertEqual(done, {"exit": 0})
        self.assertEqual(sup.proc.poll(), 0)
        self.assertLess(took, renderer.WATCH_SECONDS + renderer.QUIT_SECONDS)
        self.assertEqual(self.report()["switched"], "it quit when asked")

    def test_another_detail_is_a_change_too(self):
        done, sup, took = self.run_switch("deaf", {"use": "new", "detail": 720})
        self.assertEqual(done, {"exit": 0})
        rep = self.report()
        self.assertEqual((rep["chosen"], rep["detail"]), ("new", 720))

    def test_the_final_pass_this_run_has_is_no_change(self):
        wanted = os.path.join(self.dir, "wanted.json")
        sup = renderer.Supervisor(["x"], self.sock, self.dir, "auto", wanted=wanted, final=720)
        screenmode.write_json(wanted, {"use": "new", "detail": "auto", "final": 720})
        self.assertIsNone(sup._wish_changed())
        screenmode.write_json(wanted, {"use": "new", "detail": "auto"})
        self.assertEqual(sup._wish_changed(), {"use": "new", "detail": "auto"})

    def test_another_final_pass_is_a_change_too(self):
        done, sup, took = self.run_switch("ok", {"use": "new", "detail": "auto", "final": 720})
        self.assertEqual(done, {"exit": 0})

    def test_the_same_wish_or_none_changes_nothing(self):
        for wanted in (None, os.path.join(self.dir, "missing.json")):
            sup = self.supervisor("quit-after:1.5", wanted=wanted)
            with self.assertRaises(SystemExit):
                sup.run()
            self.assertEqual(sup.proc.poll(), 0, "it ran until it quit by itself")
            self.assertEqual(self.report()["switched"], "")


class ServeTest(Base):
    """Player.serve with the wish for new: the supervisor, then, on a failure, the exec of classic mpv as before."""

    def serve(self, mode, wish, opener=lambda path: None):
        os.environ["STANDIN_MODE"], os.environ["STANDIN_LOG"] = mode, self.calls
        self.addCleanup(os.environ.pop, "STANDIN_MODE", None)
        self.addCleanup(os.environ.pop, "STANDIN_LOG", None)
        os.environ["PVJ_RENDER_BIN"] = self.standin
        self.addCleanup(os.environ.pop, "PVJ_RENDER_BIN", None)
        state = os.path.join(self.dir, "state")
        os.makedirs(state)
        screenmode.write_json(os.path.join(state, renderer.WANTED), wish)
        p = Player(extra_args=["--vo=gpu", "--gpu-context=drm"], rundir=self.dir)

        class Exec(Exception):
            pass
        seen = {}

        def execvp(path, args):
            seen["args"] = args
            raise Exec()
        with mock.patch.object(screenmode, "prepare", lambda *a, **k: ["--drm-mode=1920x1080@60.00"]), \
                mock.patch.object(renderer.paths, "state_dir", lambda e=None: state), \
                mock.patch("pvj.player.detach_socket_opener", opener), \
                mock.patch("os.execvp", execvp), \
                mock.patch.object(renderer, "START_SECONDS", 3.0), \
                mock.patch.object(renderer.hardware, "detect_board", lambda: {"kind": "pi4"}):
            try:
                p.serve()
            except Exec:
                pass
        return seen.get("args"), self.report()

    def test_a_failing_renderer_gives_classic_mpv_with_the_screens_mode(self):
        args, rep = self.serve("exit:2", {"use": "new"})
        self.assertEqual(args[0], "mpv")
        self.assertIn("--drm-mode=1920x1080@60.00", args)
        self.assertEqual((rep["chosen"], rep["drawing"]), ("new", "classic"))
        self.assertEqual(rep["why"], "pvj-render did not start (exit 2)")
        self.assertEqual(self.runs()[0][-4:], ["--detail=540", "--fast", "--sources=4", "--final=auto"])

    def test_a_killed_renderers_socket_is_gone_before_classic_mpv_starts(self):
        # the opener takes whatever is at the name for mpv's socket: an old file left there would be opened instead of
        # mpv's, and the panel would be shut out of classic mpv (the soak of 2026-10-30)
        real, seen = renderer.serve, []

        def leaves_its_socket(player, *a, **k):
            why = real(player, *a, **k)
            with open(player.socket_path, "w"):
                pass
            return why
        with mock.patch.object(renderer, "serve", leaves_its_socket):
            args, rep = self.serve("exit:2", {"use": "new"}, lambda path: seen.append(os.path.exists(path)))
        self.assertEqual(args[0], "mpv")
        self.assertEqual(rep["drawing"], "classic")
        self.assertEqual(seen, [False], "the old socket is removed before the opener starts")

    def test_classic_chosen_starts_no_renderer(self):
        args, rep = self.serve("ok", {"use": "classic"})
        self.assertEqual(args[0], "mpv")
        self.assertEqual(self.runs(), [])
        self.assertIsNone(rep)

    def test_a_quit_of_the_renderer_ends_serve_without_mpv(self):
        with self.assertRaises(SystemExit):
            self.serve("quit-after:1", {"use": "new"})
        self.assertEqual(len(self.runs()), 1)


class ReportTest(Base):
    def test_the_report_is_untrusted_data(self):
        self.assertIsNone(renderer.clean_report({}))
        r = renderer.clean_report({"chosen": "rm -rf", "drawing": ["x"], "why": "<script>a b" + "x" * 500, "detail": {"a": 1},
                                   "pid": True, "at": "now", "boot": 5})
        self.assertEqual((r["chosen"], r["drawing"], r["detail"], r["pid"], r["at"], r["boot"]), (None, None, "auto", None, None, ""))
        self.assertNotIn("<", r["why"])
        self.assertLessEqual(len(r["why"]), renderer.REPORT_WORDS)

    def test_the_final_pass_in_the_report_is_untrusted_data_too(self):
        r = renderer.clean_report({"chosen": "new", "drawing": "new", "final": "huge", "final_why": "<b>x" + "y" * 500})
        self.assertEqual(r["final"], None)
        self.assertEqual((r["final_why"][:1], len(r["final_why"])), ("b", renderer.REPORT_WORDS))
        self.assertEqual(renderer.clean_report({"final": 720, "final_why": 3}).get("final_why"), "")
        self.assertEqual(renderer.clean_report({"final": 720})["final"], 720)

    def test_a_link_or_a_pipe_at_the_reports_name_is_no_report(self):
        api = mock.Mock()
        rend = renderer.Renderer(api, mock.Mock(data={}), self.dir, self.dir, log=self.lines.append, env=self.env)
        target = os.path.join(self.dir, "elsewhere.json")
        screenmode.write_json(target, {"chosen": "new", "drawing": "classic", "why": "x", "pid": 1})
        os.symlink(target, rend.report_file)
        self.assertIsNone(rend.report())
        os.unlink(rend.report_file)
        os.mkfifo(rend.report_file)
        start = time.monotonic()
        self.assertIsNone(rend.report())
        self.assertLess(time.monotonic() - start, 2, "never waits on a pipe")


from tests.test_boxcare import Base as CareBase      # noqa: E402  (the real Api, settings and devices)


class PanelTest(CareBase):
    """The panel's half through the real Api: the routes, the restart, the status, a reset and an import."""

    def setUp(self):
        super().setUp()
        self.bdir = tempfile.mkdtemp(prefix="pvj-renderer-bin-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.bdir]))
        self.bin = os.path.join(self.bdir, "pvj-render")
        with open(self.bin, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.bin, 0o755)
        self.api.renderer.env = {"PVJ_RENDER_BIN": self.bin}
        self.quits = []
        self.api.player.quit = lambda: self.quits.append(1)
        for f in (self.api.renderer.report_file, self.api.renderer.wanted_file):       # nothing of another test's
            if os.path.exists(f):
                os.unlink(f)
            self.addCleanup(lambda f=f: os.path.exists(f) and os.unlink(f))

    def r(self, method, path, body=None):
        return self.h(method, path, body, self.full_dev)

    def test_choose_new_saves_writes_the_wish_and_restarts_the_player_once(self):
        st, body = self.r("GET", "/api/renderer")
        self.assertEqual(st, 200, body)
        self.assertEqual((body["use"], body["detail"], body["drawing"], body["available"]), ("classic", "auto", "classic", True))
        st, body = self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.settings.data["renderer"], {"use": "new", "detail": 720})
        self.assertEqual(screenmode.read_json(self.api.renderer.wanted_file), {"use": "new", "detail": 720})
        self.assertEqual(len(self.quits), 1)
        self.assertEqual(body["why"], "the player has not started pvj-render yet")
        self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
        self.assertEqual(len(self.quits), 1, "the same again restarts nothing")
        self.assertEqual(stat.S_IMODE(os.stat(self.api.renderer.wanted_file).st_mode), 0o640)
        st, body = self.r("POST", "/api/renderer", {"use": "classic"})
        self.assertEqual((st, body["use"], body["detail"], len(self.quits)), (200, "classic", 720, 2))

    def test_the_final_pass_is_saved_beside_the_detail_and_a_change_restarts_the_player(self):
        st, body = self.r("GET", "/api/renderer")
        self.assertEqual((body["final"], body["finals"]), ("auto", ["auto", "full", 720]))
        self.assertNotIn("final_now", body)
        self.r("POST", "/api/renderer", {"use": "new", "detail": 540})
        self.assertEqual(self.settings.data["renderer"], {"use": "new", "detail": 540}, "Automatic is no key: a file as before")
        quits = len(self.quits)
        st, body = self.r("POST", "/api/renderer", {"use": "new", "final": 720})
        self.assertEqual((st, body["final"], body["detail"]), (200, 720, 540), body)
        self.assertEqual(self.settings.data["renderer"], {"use": "new", "detail": 540, "final": 720})
        self.assertEqual(screenmode.read_json(self.api.renderer.wanted_file), {"use": "new", "detail": 540, "final": 720})
        self.assertEqual(len(self.quits), quits + 1)
        st, body = self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
        self.assertEqual(body["final"], 720, "leaving it out keeps it")
        st, body = self.r("POST", "/api/renderer", {"use": "new", "final": "full"})
        self.assertEqual(self.settings.data["renderer"]["final"], "full")
        st, body = self.r("POST", "/api/renderer", {"use": "new", "final": "auto"})
        self.assertNotIn("final", self.settings.data["renderer"])

    def test_a_bad_final_pass_is_refused_and_nothing_changes(self):
        before = dict(self.settings.data.get("renderer") or {})
        for bad in (540, "720", True, 720.0, "yes", [720]):
            st, body = self.r("POST", "/api/renderer", {"use": "new", "final": bad})
            self.assertEqual(st, 400, bad)
        self.assertEqual(dict(self.settings.data.get("renderer") or {}), before)

    def test_the_page_says_what_the_final_pass_draws_at_and_why(self):
        self.r("POST", "/api/renderer", {"use": "new", "detail": "auto"})
        os.makedirs(os.path.dirname(self.api.renderer.report_file), exist_ok=True)
        pid = 4321
        self.api.player.ipc = mock.Mock(request=mock.Mock(return_value=pid))
        screenmode.write_json(self.api.renderer.report_file, {"chosen": "new", "drawing": "new", "why": "", "detail": "auto", "pid": os.getpid(),
                                                              "final": 720, "final_why": "the box was busy: 21 in 100 frames came late with a load of 5"})
        with mock.patch.object(self.api.renderer, "_same_run", return_value=True):
            st, body = self.r("GET", "/api/renderer")
        self.assertEqual(body["final_now"], {"final": 720, "why": "the box was busy: 21 in 100 frames came late with a load of 5"})

    def test_choosing_new_again_after_a_fall_back_tries_pvj_render_again(self):
        self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
        self.assertEqual(len(self.quits), 1)
        os.makedirs(os.path.dirname(self.api.renderer.report_file), exist_ok=True)
        renderer.write_report(os.path.dirname(self.api.renderer.report_file),
                              {"chosen": "new", "drawing": "classic", "why": "pvj-render ended twice in a row (exit 3)", "detail": 720})
        pid = screenmode.read_json(self.api.renderer.report_file)["pid"]
        real = self.api.player.ipc.request
        with mock.patch.object(self.api.player.ipc, "request", lambda *a: pid if a == ("get_property", "pid") else real(*a)):
            st, body = self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
            self.assertEqual((st, body["drawing"]), (200, "classic"))
            self.assertEqual(len(self.quits), 2, "the same choice again re-arms New: the player restarts and tries pvj-render")
            # drawing New already (a report of New): the same again restarts nothing
            renderer.write_report(os.path.dirname(self.api.renderer.report_file),
                                  {"chosen": "new", "drawing": "new", "why": "", "detail": 720})
            pid2 = screenmode.read_json(self.api.renderer.report_file)["pid"]
        with mock.patch.object(self.api.player.ipc, "request", lambda *a: pid2 if a == ("get_property", "pid") else real(*a)):
            self.r("POST", "/api/renderer", {"use": "new", "detail": 720})
            self.assertEqual(len(self.quits), 2)
            self.r("POST", "/api/renderer", {"use": "classic", "detail": 720})
            self.assertEqual(len(self.quits), 3)
            self.r("POST", "/api/renderer", {"use": "classic", "detail": 720})
            self.assertEqual(len(self.quits), 3, "classic again restarts nothing")

    def test_bad_bodies_are_refused_and_nothing_changes(self):
        for body in ({}, {"use": "fast"}, {"use": "new", "detail": 1080}, {"use": "new", "detail": True},
                     {"use": "new", "detail": "540"}, {"use": "new", "x": 1}):
            st, out = self.r("POST", "/api/renderer", body)
            self.assertEqual(st, 400, (body, out))
        self.assertEqual(self.api.renderer.saved(), renderer.DEFAULT)
        self.assertEqual(self.quits, [])

    def test_new_is_refused_where_it_is_not_built(self):
        self.api.renderer.env = {"PVJ_RENDER_BIN": os.path.join(self.bdir, "missing")}
        st, out = self.r("POST", "/api/renderer", {"use": "new"})
        self.assertEqual(st, 409, out)
        self.assertEqual(self.quits, [])
        self.assertFalse(self.r("GET", "/api/renderer")[1]["available"])

    def test_the_status_says_what_draws_and_why(self):
        self.r("POST", "/api/renderer", {"use": "new"})
        os.makedirs(os.path.dirname(self.api.renderer.report_file), exist_ok=True)
        renderer.write_report(os.path.dirname(self.api.renderer.report_file),
                              {"chosen": "new", "drawing": "classic", "why": "pvj-render ended twice in a row (exit 3)", "detail": "auto"})
        pid = screenmode.read_json(self.api.renderer.report_file)["pid"]
        real = self.api.player.ipc.request
        with mock.patch.object(self.api.player.ipc, "request", lambda *a: pid if a == ("get_property", "pid") else real(*a)):
            self.assertEqual(self.api.renderer.brief(), {"use": "new", "drawing": "classic", "why": "pvj-render ended twice in a row (exit 3)"})
        st, out = self.h("GET", "/api/status", None, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(set(out["renderer"]), {"use", "drawing", "why"})

    def test_the_page_says_how_the_last_switch_ended(self):
        self.r("POST", "/api/renderer", {"use": "classic"})
        os.makedirs(os.path.dirname(self.api.renderer.report_file), exist_ok=True)
        renderer.write_report(os.path.dirname(self.api.renderer.report_file),
                              {"chosen": "classic", "drawing": "classic", "why": "", "detail": "auto",
                               "switched": "it did not quit within 2 s when asked <b>, so it was killed (SIGKILL)"})
        st, body = self.r("GET", "/api/renderer")
        self.assertEqual((st, body["drawing"]), (200, "classic"))
        self.assertEqual(body["switched"], "it did not quit within 2 s when asked b, so it was killed (SIGKILL)")
        self.assertIsInstance(body["switched_at"], int)
        self.assertNotIn("why", body)

    def test_a_report_of_another_run_does_not_count(self):
        self.r("POST", "/api/renderer", {"use": "new"})
        os.makedirs(os.path.dirname(self.api.renderer.report_file), exist_ok=True)
        renderer.write_report(os.path.dirname(self.api.renderer.report_file),
                              {"chosen": "new", "drawing": "classic", "why": "old", "detail": "auto"})
        with mock.patch.object(self.api.player.ipc, "request", lambda *a: 999999):
            self.assertEqual(self.api.renderer.drawing(), ("classic", ""))

    def test_not_exported_and_an_import_leaves_the_final_pass(self):
        self.settings.data["renderer"] = {"use": "new", "detail": 540, "final": 720}
        file = self.export()
        self.assertNotIn("renderer", file["settings"])
        file["settings"]["renderer"] = {"use": "new", "detail": 540, "final": "full"}
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["renderer"]["final"], 720)

    def test_not_exported_and_an_import_leaves_it(self):
        self.settings.data["renderer"] = {"use": "new", "detail": 540}
        file = self.export()
        self.assertNotIn("renderer", file["settings"])
        file["settings"]["renderer"] = {"use": "classic", "detail": 720}
        st, out = self.send(file)
        self.assertEqual((st, out["problems"]), (200, []), out)
        self.assertEqual(self.settings.data["renderer"], {"use": "new", "detail": 540})
        self.assertIn("renderer is not imported by this version; left as it is", out["notes"])

    def test_the_panels_start_writes_the_wish_and_restarts_nothing(self):
        self.settings.data["renderer"] = {"use": "new", "detail": "output"}
        self.assertTrue(self.api.renderer.start())
        self.assertEqual(screenmode.read_json(self.api.renderer.wanted_file), {"use": "new", "detail": "output"})
        self.assertFalse(self.api.renderer.start())
        self.assertEqual(self.quits, [])

    def test_a_reset_puts_classic_back_and_restarts_the_player(self):
        self.r("POST", "/api/renderer", {"use": "new"})
        self.settings.data["renderer"] = dict(renderer.DEFAULT)
        self.api.renderer.sync()
        self.assertEqual(screenmode.read_json(self.api.renderer.wanted_file), renderer.DEFAULT)
        self.assertEqual(len(self.quits), 2)


if __name__ == "__main__":
    unittest.main()
