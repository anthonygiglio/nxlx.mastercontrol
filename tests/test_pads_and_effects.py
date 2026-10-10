# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A shader on a pad (D73) and an effect over a shader (D74), together.

Each was built and tested on a branch of its own; both reshaped a controller's queue and both touched Engine.show,
LiveEngine.watch, Engine.off, the worker and the status. Here the two are used at once, on the real player over the
stand-in for mpv of tests/test_effects.py, with both engines real: a pad tapped (from the panel and from a
controller) with an effect on, the two queues beside each other, a pad tapped while Vibes changes with an effect
on, the floor over a pad's shader, the guard's window, the rise from black, and both workers running on threads.
"""
import os
import random
import threading
import time
import unittest

from pvj import midi as M, vibes as V
from pvj.api import ApiError
from tests import test_effects as TE
from tests.test_effects import CARRIER_VIDEO, DUMP, FxTap, GenTap

ONE, TWO = "nxlx-aurora.fs", "nxlx-tide.fs"
MIDI = {"id": "midi"}                   # the device the box's own MIDI thread acts as: its calls never wait for the GPU


class Together(TE.OverShaderTest):
    def setUp(self):
        super().setUp()
        self.api.log = lambda *_: None
        self.said = []
        self.gen.log = lambda *a: self.said.append(" ".join(str(x) for x in a))
        self.api.set_pad({"bank": 0, "index": 0, "shader": ONE}, None, "t")
        self.api.set_pad({"bank": 0, "index": 1, "shader": TWO}, None, "t")

    def tap(self, index=0, device=None):
        """A pad's tap. From the panel it has shown its shader when it answers; from a controller it is noted for
        the generators' worker. Either way the stand-in speaks of a carrier once a generator plays."""
        out = self.api.play({"pad": [0, index]}, device, "t")
        self.carrier()
        return out

    def carrier(self):
        if self.gen.is_carrier(self.mpv.props.get("path")):
            self.mpv.video, self.mpv.fps = dict(CARRIER_VIDEO), 30.0

    def work(self):
        """The generators' worker and the effects' worker, until nothing waits for either."""
        for _ in range(40):
            self.fx._switched = self.fx.changer._last = self.gen.changer._last = -1e9
            did = self.gen.changer.pump()
            self.carrier()
            did = self.fx.changer.pump() or did
            if not did and self.gen.changer.queued() is None and self.fx.changer.queued() is None:
                return
        self.fail("the two queues never emptied")

    def shader(self):
        on = self.gen.on_screen()
        return on["id"] if on else None

    def effect(self):
        return (self.state()["on"] or {}).get("id")

    def player_status(self):
        return self.api.status({}, None, "t")["player"]

    # -- a pad tapped with an effect on --
    def test_a_pad_tapped_with_an_effect_on_keeps_the_effect_from_the_panel_and_from_a_controller(self):
        for name, device in (("the panel", None), ("a controller", MIDI)):
            self.clip()
            self.fx.put("fx-wash.fs", {"strength": 0.4})
            serial, clears = self.player.effect_serial, self.player.clears
            out = self.tap(0, device)
            self.assertEqual((out["shader"], out.get("pending", False)), (ONE, device is not None), name)
            self.work()
            self.assertEqual((self.shader(), self.effect(), self.kinds()), (ONE, "fx-wash.fs", ["shader", "effect"]), name)
            self.assertEqual((self.player.effect_serial, self.player.clears), (serial, clears), name)   # never off and on, and nothing cleared
            # the pair is looked at: the worker's look hands the GPU a text for the shader's picture
            self.fx.adjust("anchor")
            on = self.state()["on"]
            self.assertEqual((on["checked"], on["working"]["under"], on["values"]["strength"]), (True, "shader", 0.4), name)
            st = self.player_status()
            self.assertEqual((st["shader"], st["shader_preset"], st["effect"], st.get("shader_refused")), (ONE[:-3], "", "fx-wash", None), name)
            # another pad under the same effect
            self.tap(1, device)
            self.work()
            self.assertEqual((self.shader(), self.effect(), self.kinds()), (TWO, "fx-wash.fs", ["shader", "effect"]), name)
            self.api.control({"action": "stop"}, None, "t")
            self.assertEqual((self.shader(), self.effect()), (None, None), name)

    def test_a_pair_refused_at_a_pads_tap_leaves_the_pads_shader_on_and_the_two_places_agree(self):
        """The effect was on over a clip; a pad's shader comes under it; the GPU refuses the effect over that shader.
        The pad's shader stays and is "playing" for the pads; the refusal is the effect's and is said on its card;
        nothing is said under the pads, where a refused SHADER would be said."""
        for name, device in (("the panel", None), ("a controller", MIDI)):
            self.clip()
            self.fx._bad.clear()
            self.fx.put("fx-wash.fs")
            self.tap(0, device)
            self.refuse()                               # the GPU will refuse the effect's next text, the one for the shader's picture
            self.work()                                 # the pad's shader shows (from a controller: now), and its arrival asks for the look
            FxTap.lines = []
            s, st = self.state(), self.player_status()
            self.assertEqual((s["on"], s["last"], s["error"]["id"]), (None, "the GPU refused it over the shader that came on", "fx-wash.fs"), name)
            self.assertEqual((self.shader(), self.kinds()), (ONE, ["shader"]), name)
            self.assertEqual((st["shader"], st.get("shader_refused"), st.get("effect")), (ONE[:-3], None, None), name)
            self.assertIsNone(self.gen.error, name)
            row = next(r for r in s["effects"] if r["id"] == "fx-wash.fs")
            self.assertEqual((row["refused"], "undeclared" in row["refused_pair"]), (None, True), name)
            # the other pad's shader is another pair: the same effect is tried over it
            self.tap(1, device)
            self.work()
            self.fx.put("fx-wash.fs")
            self.assertEqual((self.shader(), self.effect()), (TWO, "fx-wash.fs"), name)
            self.api.control({"action": "stop"}, None, "t")

    def test_a_pads_shader_the_gpu_refuses_with_an_effect_on_is_said_under_the_pads_and_the_effect_says_nothing_false(self):
        """The other way round: the SHADER is refused. Over a clip the screen is then black and stopped (the engine has
        no clip to go back to, D73), so the effect comes off with it; with a shader on before, that shader is back
        and the effect stays over it."""
        named = lambda: [(p, level, t % ("nxlx shader %d %d" % (os.getpid(), self.gen._serial + 1)) if "%s" in t else t) for p, level, t in DUMP]
        self.clip()
        self.fx.put("fx-wash.fs")
        GenTap.lines = named()
        with self.assertRaises(ApiError) as c:
            self.tap(0)
        self.carrier()
        GenTap.lines = []
        self.assertEqual(c.exception.status, 422)
        st, s = self.player_status(), self.state()
        self.assertEqual((st["shader_refused"]["id"], st.get("shader"), st.get("path"), st.get("effect")), (ONE, None, None, None))
        # the engine stopped the screen (it has no clip to go back to), and the effect went with the picture it
        # was over: its card says that, and says no refusal of its own
        self.assertEqual((s["on"], s["error"], s["last"]), (None, None, "the shader under it was taken off the screen"))
        self.assertEqual(self.mpv.loaded, [])
        # from a controller, with a shader on before: the one before is back under the effect, and it is said
        self.tap(1)
        self.fx.put("fx-wash.fs")
        self.assertIsNone(self.player_status().get("shader_refused"))                  # something else took the screen: no longer news
        self.gen._refusals.clear()
        GenTap.lines = named()
        self.tap(0, MIDI)
        self.work()
        GenTap.lines = []
        st = self.player_status()
        self.assertEqual((st["shader_refused"]["id"], st["shader"], st["effect"]), (ONE, TWO[:-3], "fx-wash"))
        self.assertEqual((self.state()["error"], self.kinds()), (None, ["shader", "effect"]))

    # -- the two queues beside each other --
    def test_a_controllers_pad_and_a_controllers_effect_wish_made_together_both_land_in_either_order(self):
        ids = self.fx.order()
        for name, wishes in (("the effect first", [lambda: self.fx.step(1), lambda: self.tap(0, MIDI)]),
                             ("the pad first", [lambda: self.tap(0, MIDI), lambda: self.fx.step(1)]),
                             ("pad, effect, pad, effect", [lambda: self.tap(0, MIDI), lambda: self.fx.step(1), lambda: self.tap(1, MIDI), lambda: self.fx.step(1)])):
            for workers in ("the generators' worker first", "the effects' worker first"):
                self.clip()
                self.fx.put(ids[2])
                for wish in wishes:
                    wish()
                if workers.startswith("the effects"):
                    self.fx._switched = -1e9
                    self.fx.changer.pump()
                self.work()
                steps = len(wishes) // 2              # half of them are steps of the effect
                want = TWO if len(wishes) == 4 else ONE
                self.assertEqual((self.shader(), self.effect(), self.kinds()), (want, ids[2 + steps], ["shader", "effect"]), "%s, %s" % (name, workers))
                self.assertEqual((self.fx.error, self.gen.error), (None, None), name)
                self.api.control({"action": "stop"}, None, "t")
        # a Stop after both wishes: neither lands, and each says so in its own place
        self.clip()
        self.fx.put(ids[2])
        del self.said[:]
        self.fx.step(1)
        self.tap(0, MIDI)
        self.api.control({"action": "stop"}, None, "t")
        self.clip()
        self.work()
        self.assertEqual((self.shader(), self.effect(), self.mpv.loaded), (None, None, []))
        self.assertIn("Stop was pressed after it was asked for", self.state()["error"]["message"])
        self.assertTrue(any("dropped" in line or "was not shown" in line for line in self.said), self.said)

    # -- Vibes --
    def test_a_controllers_pad_while_vibes_changes_with_an_effect_on_never_clears_the_screen(self):
        """D73's black-screen case with an effect on: Vibes' own thread is held while the GPU looks at its next
        shader, the pad's tap ends the rotation, and the rotation finishes its way out. The screen is never cleared
        (a clearing would take the effect with it), and the effect is on over the pad's shader afterwards."""
        self.settings.data["mix"]["duration"] = 0.1
        vibes = self.api.vibes = V.Vibes(self.api, self.gen, rng=random.Random(4), log=lambda *_: None)
        self.addCleanup(vibes.stop)
        reached, go = threading.Event(), threading.Event()
        self.addCleanup(go.set)
        watch = self.gen._watch
        arm = []

        def held(tap, desc):
            if arm and threading.current_thread().name == "vibes" and not reached.is_set():
                reached.set()
                go.wait(10)
            return watch(tap, desc)
        self.gen._watch = held
        self.fx.put("fx-wash.fs")
        vibes.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not vibes.started:
            time.sleep(0.01)
        self.assertTrue(vibes.started)
        self.carrier()
        self.fx.adjust("anchor")
        self.assertEqual(self.kinds(), ["shader", "effect"])
        clears, serial = self.player.clears, self.player.effect_serial
        self.gen._checked.clear()                       # the GPU looks at the rotation's next shader (and the thread is held there)
        arm.append(1)
        vibes.skip()
        self.assertTrue(reached.wait(10), "the rotation never came to the GPU's look")
        self.tap(0, MIDI)                               # the pad, from a controller, in the middle of the change
        self.assertFalse(vibes.running)
        go.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and vibes._thread is not None:
            time.sleep(0.01)
        self.assertIsNone(vibes._thread, "the rotation's thread never ended")
        self.assertEqual(self.player.clears, clears, "the rotation cleared the screen on its way out")
        self.assertEqual(self.kinds(), ["shader", "effect"])                           # its own shader stays until the pad's shows
        self.work()
        self.assertEqual((self.shader(), self.effect(), self.kinds()), (ONE, "fx-wash.fs", ["shader", "effect"]))
        self.assertEqual((self.player.clears, self.player.effect_serial), (clears, serial))
        self.assertIsNone(self.fx.error)

    # -- the floor and the guard over a pad's shader --
    def test_the_floor_over_a_pads_shader_leaves_the_pad_playing_and_its_light_on(self):
        now = self.floor_clock()
        self.api.registry.set_enabled("control-midi", True)
        self.settings.data["control"]["midi"]["enabled"] = True
        hub = M.MidiHub(self.api, self.settings, log=lambda *_: None, lister=lambda: [], describer=lambda p: None)
        self.addCleanup(hub.stop)
        light = lambda: M.light_state({"action": "pad", "bank": 0, "index": 0}, hub._snapshot(now[0], fresh=True))
        self.tap(0)
        self.fx.put("fx-wash.fs")
        self.assertEqual(light(), "active")
        self.assertIs(self.heavy_seconds(now, 22), False)
        self.assertIs(self.heavy_seconds(now, 3), True)                                # the floor takes the effect off
        st = self.player_status()
        self.assertEqual((st["shader"], st["shader_preset"], st.get("effect"), st.get("shader_refused")), (ONE[:-3], "", None, None))
        self.assertEqual((light(), self.shader(), self.kinds()), ("active", ONE, ["shader"]))
        self.assertEqual(self.settings.data.get("shaders", {}).get("heavy", {}), {})
        self.assertIn("was dropping frames with it on over the shader", self.state()["last"])

    def test_a_pad_that_changes_the_shader_under_an_effect_starts_both_guards_windows_as_they_should(self):
        """The generators' guard judges the new shader from a window of its own; the effect's guard starts over for
        the new shader; the floor's count goes on (it is the pair's, across the shaders under the effect)."""
        now = self.floor_clock()
        self.tap(0)
        self.fx.put("fx-wash.fs")
        self.assertIs(self.heavy_seconds(now, 12), False)
        self.assertEqual((self.gen.guard.verdict["state"], self.fx.guard.verdict["state"]), ("heavy", "heavy"))
        self.tap(1, MIDI)                                                              # another pad under the effect
        self.work()
        self.assertIs(self.heavy_seconds(now, 1), False)
        self.assertEqual((self.gen.guard.verdict["state"], self.fx.guard.verdict["state"]), (None, None))
        self.assertIn(" under effect ", self.gen.guard._desc)
        self.assertIs(self.heavy_seconds(now, 9), False)                               # 12 + 1 + 9: not yet the floor's 3 + 20
        self.assertIs(self.heavy_seconds(now, 4), True)                                # and then it is, across the change of pad
        self.assertEqual((self.shader(), self.kinds()), (TWO, ["shader"]))

    # -- the level --
    def test_a_pads_shader_comes_up_from_black_after_a_fade_out_with_the_effect_still_on(self):
        self.settings.data["mix"] = {"transition": "cut", "duration": 0.4}
        level = lambda: self.mpv.props["brightness"]
        for name, device in (("the panel", None), ("a controller", MIDI)):
            self.clip()
            self.fx.put("fx-wash.fs")
            self.api.fadeout({"seconds": 0.1}, None, "t")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (self.api.fader.label == "out" and level() == -100):
                time.sleep(0.01)
            self.assertEqual((self.api.fader.label, level()), ("out", -100), name)
            before = len(self.mpv.commands)
            self.tap(0, device)
            self.work()
            levels = [c[2] for c in self.mpv.commands[before:] if c[:2] == ("set_property", "brightness")]
            self.assertEqual(levels[0], -100, "%s: the shader did not start from black" % name)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and level() != 0:
                time.sleep(0.01)
            self.assertEqual((level(), self.api.fader.label), (0, None), name)
            self.assertTrue(any(-100 < v < 0 for v in [c[2] for c in self.mpv.commands[before:] if c[:2] == ("set_property", "brightness")]),
                            "%s: it did not rise, it snapped" % name)
            self.assertEqual((self.shader(), self.effect(), self.kinds()), (ONE, "fx-wash.fs", ["shader", "effect"]), name)
            self.api.control({"action": "stop"}, None, "t")
            self.api.fader.cancel()

    # -- both workers on their own threads --
    def test_both_queues_workers_run_at_once_and_nothing_is_lost_or_out_of_order(self):
        """The generators' worker and the effects' worker on real threads, a controller's pads and a controller's
        effect steps coming together: nothing hangs, nothing raises, the generator is always first in the list, and
        the last wish of each kind is what is on. (The order of the locks is checked at every taking.)"""
        cfg = self.gen.config()
        cfg["faster"] = True
        self.gen._save(cfg)
        ids = self.fx.order()
        self.show()
        self.fx.put(ids[0])
        before = len(self.mpv.commands)
        self.gen.changer._use_thread = self.fx.changer._use_thread = True
        errors, rounds = [], 10

        def pads():
            for n in range(rounds):
                try:
                    self.api.play({"pad": [0, n % 2]}, MIDI, "t")
                    time.sleep(0.002)
                except Exception as e:
                    errors.append("pad: %r" % (e,))

        def steps():
            for n in range(rounds):
                try:
                    self.fx.step(1)
                    time.sleep(0.003)
                except Exception as e:
                    errors.append("step: %r" % (e,))
        threads = [threading.Thread(target=pads, daemon=True), threading.Thread(target=steps, daemon=True)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual([t.is_alive() for t in threads], [False, False])
        idle = lambda: all(c.queued() is None and c.working is None and getattr(c, "_doing", None) is None for c in (self.gen.changer, self.fx.changer))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (idle() and not self.fx._intent):
            time.sleep(0.01)
        self.assertTrue(idle(), "a worker never finished")
        self.gen.changer._use_thread = self.fx.changer._use_thread = False
        self.assertEqual(errors, [])
        self.assertEqual((self.shader(), self.effect()), (TWO if rounds % 2 == 0 else ONE, ids[rounds % len(ids)]))
        self.assertEqual((self.fx.error, self.gen.error), (None, None))
        lists = self.lists(before)
        self.assertEqual(sorted(set(tuple(x) for x in lists) - {("shader",), ("shader", "effect")}), [], "a list with the effect first, or no generator")


for _name in dir(TE.OverShaderTest):               # the pair's own tests run from their module, not here again
    if _name.startswith("test_") and _name not in Together.__dict__:
        setattr(Together, _name, None)


if __name__ == "__main__":
    unittest.main()
