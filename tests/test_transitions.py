# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The crossfade (pvj/transitions.py, D71) against a player that records what it is told. What a real mpv draws is in
tests/test_transitions_gpu.py, which CI's effects-gpu jobs run."""
import os
import struct
import unittest
import zlib

from pvj import transitions as T
from pvj.player import PlayerError
from tests.test_server import FakePlayer, ServerBase

GONE = object()         # a property the player cannot give: "mpv: property unavailable"


def png(w, h, rgb, filtered=False, depth=8):
    raw = b"".join((b"\x01" if filtered else b"\x00") + bytes(rgb) * w for _ in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))


class Screen(FakePlayer):
    """A player with a screen of 8 x 4 in one colour: it writes the still it is asked for and notes every overlay."""

    def __init__(self, rundir):
        super().__init__(rundir)
        self.size = (8, 4)
        self.colour = (200, 100, 50)
        self.props = {"pid": 4242, "pause": False, "time-pos": 0.5, "seeking": False, "idle-active": False,
                      "frame-drop-count": 0, "decoder-frame-drop-count": 0}
        self.ipc = self
        self.levels = []                # the alpha of every overlay drawn, in order; None for a removal
        self.still_bytes = None         # what the next still holds instead of the screen
        self.fail = set()               # names of calls that raise PlayerError
        self.on_overlay = None

    def request(self, *command):
        if command[0] == "get_property":
            if "ipc" in self.fail:
                raise PlayerError("player is not running")
            v = self.props.get(command[1])
            if v is GONE:
                raise PlayerError("mpv: property unavailable")
            return v
        return None

    def osd_size(self):
        return self.size

    def status(self):
        return {"running": self.running, "path": None}

    def pause(self, value=None):
        self.calls.append(("pause", value))
        self.props["pause"] = bool(value)
        return bool(value)

    def play(self, *a, **kw):
        if "play" in self.fail:
            raise PlayerError("player is not running")
        super().play(*a, **kw)
        self.props["pause"] = False

    def still(self, path):
        self.calls.append(("still", os.path.basename(path)))
        if "still" in self.fail:
            raise PlayerError("mpv: error running command")
        assert os.path.exists(path), "the panel makes the file; the player may not make one in the panel's folder"
        with open(path, "wb") as f:
            f.write(self.still_bytes if self.still_bytes is not None else png(self.size[0], self.size[1], self.colour))

    def overlay(self, oid, x, y, w, h, pixels):
        if "overlay" in self.fail:
            raise PlayerError("no reply from mpv")
        assert len(pixels) == w * h * 4
        self.calls.append(("overlay", oid, x, y, w, h))
        self.levels.append(pixels[3])
        self.pixels = pixels
        with open(os.path.join(self.rundir, "overlay-%d.bgra" % oid), "wb") as f:
            f.write(pixels)
        if self.on_overlay:
            self.on_overlay()

    def overlay_remove(self, oid):
        self.calls.append(("overlay_remove", oid))
        self.levels.append(None)
        try:
            os.unlink(os.path.join(self.rundir, "overlay-%d.bgra" % oid))
        except OSError:
            pass


class Names(unittest.TestCase):
    def test_a_newer_transition_is_kept_beside_one_an_older_release_knows(self):
        self.assertEqual(T.stored("crossfade", 2), {"transition": "dip", "style": "crossfade", "duration": 2.0})
        self.assertEqual(T.stored("cut", 1), {"transition": "cut", "duration": 1.0})
        self.assertEqual(T.stored("dip", 0.5), {"transition": "dip", "duration": 0.5})
        for name in T.NAMES:
            self.assertEqual(T.named(T.stored(name, 1)), name)

    def test_what_nobody_knows_is_a_cut_and_an_unknown_style_falls_back_to_what_stands_beside_it(self):
        self.assertEqual(T.named({"transition": "wipe", "duration": 1}), "cut")
        self.assertEqual(T.named({"transition": "dip", "style": "ripple", "duration": 1}), "dip")
        self.assertEqual(T.named({}), "cut")
        self.assertEqual(T.named(None), "cut")

    def test_the_stored_form_is_one_the_release_before_reads_as_a_dip(self):
        # what pvj/api.py and pvj/boxcare.py did before this: `mode not in ("cut", "dip")` refused, and a play
        # looked only at `transition == "dip"`
        kept = T.stored("crossfade", 1.0)
        self.assertIn(kept["transition"], ("cut", "dip"))
        self.assertEqual(kept["transition"] == "dip", True)


class ReadStill(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "s.png")

    def put(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def test_the_pixels_come_out_as_opaque_bgra(self):
        self.put(png(3, 2, (10, 20, 30)))
        self.assertEqual(T.read_still(self.path, (3, 2)), (3, 2, bytes([30, 20, 10, 255]) * 6))

    def test_anything_but_the_players_own_kind_of_file_is_refused(self):
        for what, data in (("another size", png(4, 2, (1, 2, 3))), ("rows with a filter", png(3, 2, (1, 2, 3), filtered=True)),
                           ("16 bit", png(3, 2, (1, 2, 3), depth=16)), ("not a PNG", b"\xff\xd8\xff\xe0 a jpeg"), ("nothing", b""),
                           ("cut short", png(3, 2, (1, 2, 3))[:40]), ("cut inside its header", png(3, 2, (1, 2, 3))[:20]),
                           ("cut inside a chunk's name", png(3, 2, (1, 2, 3))[:14]), ("the header only", png(3, 2, (1, 2, 3))[:33])):
            self.put(data)
            with self.assertRaises(T.StillError, msg=what):
                T.read_still(self.path, (3, 2))
        with self.assertRaises(T.StillError):
            T.read_still(os.path.join(self.dir, "missing.png"), (3, 2))

    def test_a_file_that_unpacks_to_more_than_a_screen_is_not_unpacked(self):
        # a small file that says 3 x 2 and holds a hundred megabytes of zeros
        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
        self.put(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 3, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(100 << 20), 1)))
        with self.assertRaises(T.StillError):
            T.read_still(self.path, (3, 2))

    def test_reading_holds_about_the_file_and_its_rows_and_no_more(self):
        # finding 6: about 74 MB were alive at the end for a 2560 x 1440 screen, five times the picture
        import tracemalloc
        w, h = 640, 360
        raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
        self.put(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b""))
        tracemalloc.start()
        try:
            got = T.read_still(self.path, (w, h))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        picture = w * h * 4
        self.assertEqual((got[0], got[1], len(got[2])), (w, h, picture))
        self.assertEqual(bytes(got[2][:4]), bytes([raw[3], raw[2], raw[1], 255]))
        self.assertEqual(bytes(got[2][-4:]), bytes([raw[-1], raw[-2], raw[-3], 255]))
        self.assertLess(peak, 2.5 * picture, "reading a still held %.1f times the picture" % (peak / float(picture)))

    def test_a_level_scales_every_byte_the_alpha_too(self):
        self.assertEqual(T.faded(bytes([30, 20, 10, 255]), 255), bytes([30, 20, 10, 255]))
        self.assertEqual(T.faded(bytes([30, 20, 10, 255]), 128), bytes([15, 10, 5, 128]))
        self.assertEqual(T.faded(bytes([30, 20, 10, 255]), 0), bytes(4))


class Base(ServerBase):
    def setUp(self):
        super().setUp()
        self.player = self.api.player = Screen(self.rundir)
        self.player.running = True                      # something plays
        self.now = [50.0]
        self.cost = {}                                  # seconds a call of the player takes on the test's clock
        self.hook = None                                # called once, in the middle of a transition's wait
        self.lines = []
        self.tr = self.api.transitions = T.Transitions(self.api, clock=lambda: self.now[0], sleep=self.sleep, thread=False, log=self.lines.append)
        self.settings.data["mix"] = T.stored("crossfade", 1.0)
        self.api.fader = type(self.api.fader)(self.api._apply_opacity, clock=lambda: self.now[0], sleep=lambda s: None)

    def sleep(self, seconds):
        self.now[0] += seconds
        if self.hook:
            hook, self.hook = self.hook, None
            hook()

    def play(self, name="a.mp4"):
        return self.api.play({"file": name}, None, "t")

    def names(self):
        return [c[0] for c in self.player.calls]

    def left(self):
        return sorted(n for n in os.listdir(self.rundir) if n != "pin" and not n.startswith("shader-"))


class Crossfade(Base):
    def test_a_still_of_the_screen_is_laid_over_the_new_clip_and_taken_away_step_by_step(self):
        self.assertEqual(self.play(), {"playing": "a.mp4"})
        names = self.names()
        # the outgoing clip is frozen, the still is taken and laid over everything, and only then the clip is loaded
        self.assertEqual(names[:4], ["pause", "still", "overlay", "play"])
        self.assertEqual(self.player.calls[0], ("pause", True))
        self.assertEqual(self.player.calls[2], ("overlay", T.OVERLAY_ID, 0, 0, 8, 4))
        levels = self.player.levels
        self.assertEqual((levels[0], levels[-1]), (255, None))
        steps = levels[1:-1]
        self.assertEqual(len(steps), 20)                                    # twenty a second for the one second asked for
        self.assertEqual(steps, sorted(steps, reverse=True))
        self.assertTrue(steps[0] == 255 and 0 < steps[-1] < 20, steps)
        self.assertEqual(names.count("overlay_remove"), 1)
        self.assertAlmostEqual(self.now[0], 51.0, places=6)                 # the seconds asked for
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertEqual((self.tr.running, self.tr.given_up), (None, ""))
        self.assertEqual(self.left(), [], "the still's files are gone")

    def test_the_still_is_the_screen_itself_blue_first(self):
        self.play()
        self.assertEqual(self.player.calls[2][3:], (0, 8, 4))
        # the last step drawn: every byte of the screen's colour scaled by its alpha
        a = self.player.pixels[3]
        self.assertEqual(self.player.pixels[:4], bytes([(50 * a + 127) // 255, (100 * a + 127) // 255, (200 * a + 127) // 255, a]))

    def test_the_duration_is_the_mix_setting(self):
        self.settings.data["mix"] = T.stored("crossfade", 2.0)
        self.play()
        self.assertAlmostEqual(self.now[0], 52.0, places=6)
        self.assertEqual(len(self.player.levels) - 2, 40)

    def test_with_nothing_playing_the_clip_just_starts(self):
        self.player.running = False
        self.play()
        self.assertEqual(self.names(), ["play", "opacity"])

    def test_a_dark_screen_is_not_blended_from(self):
        for label, make in (("Blackout", lambda: self.api.blackout({"on": True}, None, "t")),
                            ("opacity 0", lambda: self.api.control({"action": "opacity", "value": 0}, None, "t")),
                            ("a picture faded out", lambda: self.api.fadeout({"seconds": 0.1}, None, "t"))):
            self.api.mix.update(blackout=False, opacity=100)
            self.api.fader.cancel()
            make()
            del self.player.calls[:]
            self.play()
            self.assertNotIn("still", self.names(), label)
            self.assertNotIn("overlay", self.names(), label)
            self.assertIn("play", self.names(), label)

    def test_a_frozen_clip_is_blended_from_and_the_new_clip_plays(self):
        self.player.props["pause"] = True
        self.play()
        self.assertEqual(self.names()[:4], ["pause", "still", "overlay", "play"])
        self.assertFalse(self.player.props["pause"])

    def test_the_steps_wait_for_the_new_clips_first_frame(self):
        # a stream that takes 2 seconds to come: the player says it is still seeking, and the still stays whole
        self.player.props.update({"time-pos": 0.0, "seeking": True})
        seen = []

        def arrive():
            seen.append(list(self.player.levels))
            self.now[0] += 2.0
            self.player.props.update({"seeking": False})
        self.hook = arrive
        self.play()
        self.assertEqual(seen, [[255]])
        self.assertEqual(len(self.player.levels) - 2, 20)
        self.assertGreater(self.now[0], 53.0)
        self.assertEqual(self.tr.last["ended"], "done")

    def test_a_clip_that_has_no_position_yet_is_not_there_yet(self):
        self.player.props.update({"time-pos": GONE, "seeking": GONE})
        self.hook = lambda: self.player.props.update({"time-pos": 0.04, "seeking": False})
        self.play()
        self.assertEqual(self.tr.last["ended"], "done")

    def test_a_clip_that_never_comes_does_not_keep_the_still_for_ever(self):
        self.player.props.update({"time-pos": 0.0, "seeking": True})
        self.play()
        self.assertEqual(self.player.levels[-1], None)
        self.assertLess(self.now[0], 50.0 + T.FIRST_FRAME + 1.5)
        self.assertEqual(self.left(), [])

    def test_a_player_that_went_idle_is_not_waited_for(self):
        # the new clip could not be opened: the player has nothing loaded any more
        self.player.props.update({"time-pos": GONE, "seeking": GONE})
        self.player.play = lambda *a, **kw: self.player.props.update({"idle-active": True})
        self.play()
        self.assertAlmostEqual(self.now[0], 51.0, places=6)
        self.assertEqual(self.tr.last["ended"], "done")

    # -- what ends it --
    def ended_by(self, act, why=None):
        self.hook = act
        self.play()
        levels = self.player.levels
        self.assertEqual(levels[-1], None, "the still was not taken off")
        self.assertLess(len(levels), 6, "steps went on after it ended: %s" % levels)
        self.assertEqual(self.left(), [])
        self.assertIsNone(self.tr.running)

    def test_blackout_ends_it_at_once(self):
        self.ended_by(lambda: self.api.blackout({"on": True}, None, "t"))
        self.assertEqual(self.player.calls[-2:], [("opacity", 0), ("overlay_remove", T.OVERLAY_ID)], "dark first, then the still goes")

    def test_a_blackout_that_comes_while_the_still_is_taken_keeps_it_off_the_screen(self):
        real = self.player.still

        def during(path):
            real(path)
            self.tr._ended += 1             # what end() does first, before it waits for the lock this still holds
        self.player.still = during
        self.play()
        self.assertNotIn("overlay", self.names(), "a still was laid over a screen that had just gone dark")
        self.assertIn("play", self.names())
        self.assertEqual(self.left(), [])

    def test_a_player_that_runs_with_nothing_loaded_has_nothing_to_blend_from(self):
        # on a box the player always runs: after Stop and at power-up it is idle, and `running` is still true
        self.player.props["idle-active"] = True
        self.play()
        self.assertEqual(self.names(), ["play", "opacity"])
        self.assertEqual(self.lines, [], "the ordinary first play is not worth a line in the journal")

    def test_stop_ends_it_at_once(self):
        self.ended_by(lambda: self.api.control({"action": "stop"}, None, "t"))
        i = self.names().index("clear")
        self.assertEqual(self.names()[i - 1], "overlay_remove", "the still goes before the screen is cleared")

    def test_a_fade_out_a_fade_in_a_change_of_opacity_and_a_reset_end_it(self):
        for act in (lambda: self.api.fadeout({"seconds": 1}, None, "t"), lambda: self.api.fadein({"seconds": 1}, None, "t"),
                    lambda: self.api.control({"action": "opacity", "value": 50}, None, "t"), lambda: self.api.control({"action": "reset"}, None, "t")):
            self.api.mix.update(blackout=False, opacity=100)
            self.api.fader.cancel()
            del self.player.levels[:]
            self.ended_by(act)

    def test_every_other_way_of_playing_ends_it(self):
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.registry.set_enabled("inputs-srt", True)
        for act in (lambda: self.api.play({"preset": "startless"}, None, "t"), lambda: self.api.play({"stream": "bbbb0001"}, None, "t"),
                    lambda: self.api.test_pattern({"on": True}, None, "t"), lambda: self.api.test_pattern({"on": False}, None, "t"),
                    lambda: self.api.test_tone({"channel": "left"}, None, "t"),
                    lambda: self.api.sync._local_player().load(os.path.join(self.media, "b.mov"), True)):       # a sync client
            del self.player.levels[:]
            self.ended_by(act)

    def test_a_sync_client_cuts_as_it_does_with_a_dip(self):
        del self.player.calls[:]
        self.api.sync._local_player().load(os.path.join(self.media, "b.mov"), True)
        self.assertEqual(self.names(), ["play", "opacity"])

    def test_a_change_of_the_mapping_ends_it(self):
        self.ended_by(self.api.mapper.apply)

    def test_a_generator_shader_taking_the_screen_ends_it(self):
        self.api.registry.set_enabled("shaders", True)
        sid = self.api.shaders.library()[0]["id"]
        self.ended_by(lambda: self.api.shaders.show(sid))

    def test_a_second_play_starts_from_what_is_on_the_screen_and_the_first_stops(self):
        second = []

        def again():
            before = len(self.player.levels)
            second.append(self.play("b.mov"))
            second.append(self.player.levels[before:])
        self.hook = again
        self.play()
        self.assertEqual(second[0], {"playing": "b.mov"})
        mine = second[1]
        self.assertEqual((mine[0], mine[-1], len(mine)), (255, None, 22), "the second transition ran whole")
        self.assertEqual(self.names().count("still"), 2)
        self.assertEqual(self.player.levels[len(self.player.levels) - 1], None)
        self.assertEqual(self.player.levels[-len(mine):], mine, "the first transition drew nothing after the second ended")
        self.assertEqual(self.left(), [])

    def test_an_effect_changed_during_it_does_not_touch_it(self):
        # an effect is a shader under the still: nothing in the effects engine ends a transition
        import inspect
        from pvj import effects
        self.assertNotIn("transitions", inspect.getsource(effects))

    # -- a player that goes away --
    def test_a_player_restarted_during_it_gets_no_still(self):
        def restart():
            self.player.props["pid"] = 4343
            del self.player.levels[:]
        self.hook = restart
        self.play()
        self.assertEqual(self.player.levels, [None], "nothing is drawn on the new player; the still is only taken off")
        self.assertIn("restarted", self.tr.last["ended"])
        self.assertEqual(self.left(), [])

    def test_a_player_that_crashes_during_it_leaves_nothing_behind(self):
        self.hook = lambda: self.player.fail.update(("overlay", "ipc"))
        self.play()
        self.assertIn("the player", self.tr.last["ended"])
        self.assertIsNone(self.tr.running)
        self.assertEqual(self.left(), [])

    def test_a_player_that_crashes_while_the_clip_is_waited_for_leaves_nothing_behind(self):
        self.player.props.update({"time-pos": 0.0, "seeking": True})
        self.hook = lambda: self.player.fail.add("ipc")
        self.play()
        self.assertIn("the player", self.tr.last["ended"])
        self.assertEqual(self.left(), [])

    def test_the_panel_starting_takes_an_old_still_off_and_removes_what_was_left(self):
        for name in ("transition-123.png", "overlay-%d.bgra" % T.OVERLAY_ID, "overlay-10.bgra", "transition-123.png.txt"):
            open(os.path.join(self.rundir, name), "w").close()
        self.tr.tidy()
        self.assertEqual(self.player.calls, [("overlay_remove", T.OVERLAY_ID)])
        self.assertEqual(self.left(), ["overlay-10.bgra", "transition-123.png.txt"], "only a transition's own files go")

    # -- when it cannot be done --
    def test_a_still_that_fails_is_a_cut_with_the_clip_playing_and_nothing_left(self):
        for what in ("still", "overlay"):
            self.player.fail = {what}
            self.tr.given_up = ""
            del self.player.calls[:]
            self.assertEqual(self.play(), {"playing": "a.mp4"})
            self.assertEqual(self.names()[-2:], ["play", "opacity"], what)
            self.assertFalse(self.player.props["pause"])
            self.assertEqual(self.left(), [], what)
            self.assertIsNone(self.tr.running)
            # and the box gives up: a still that fails would freeze and cut at every play (finding 3 of the review)
            self.assertIn("the still failed", self.api.status({}, None, "t")["mix"]["fallback"], what)
            del self.player.calls[:]
            self.player.fail = set()
            self.play()
            self.assertNotIn("pause", self.names(), "the next play dips: no freeze, no still")
            self.assertNotIn("still", self.names())

    def test_any_kind_of_failure_in_the_still_is_cleaned_up_after(self):
        # finding 1: only three kinds of exception were caught; any other left the clip frozen and answered 500
        for boom in (RuntimeError("anything"), MemoryError(), ValueError("x")):
            self.tr.given_up = ""
            del self.player.calls[:]

            def still(path, boom=boom):
                raise boom
            self.player.still = still
            self.assertEqual(self.play(), {"playing": "a.mp4"})
            self.assertFalse(self.player.props["pause"], repr(boom))
            self.assertIn("play", self.names())
            self.assertEqual(self.left(), [])
            self.assertIsNone(self.tr.running)

    def test_a_failure_between_the_still_and_its_steps_takes_the_still_off(self):
        # finding 1, in Api.play: nothing would ever have removed overlay 63
        def boom(percent):
            raise RuntimeError("anything")
        self.api._apply_opacity = boom
        with self.assertRaises(RuntimeError):
            self.play()
        self.assertEqual(self.player.levels, [255, None])
        self.assertIsNone(self.tr.running)
        self.assertEqual(self.left(), [])

    def test_a_still_that_ended_meanwhile_is_no_reason_to_give_up(self):
        real = self.player.still

        def during(path):
            real(path)
            self.tr.end("Blackout")
        self.player.still = during
        self.play()
        self.assertNotIn("overlay", self.names())
        self.assertEqual(self.tr.given_up, "")

    def test_a_step_the_player_does_not_answer_makes_the_box_give_up(self):
        def stall():
            self.player.fail.add("overlay")         # "no reply from mpv" from the next step on
        self.hook = stall
        self.play()
        self.assertIn("a step failed", self.tr.given_up)
        self.assertIn("a step failed", self.api.status({}, None, "t")["mix"]["fallback"])
        self.assertEqual(self.left(), [])

    def test_an_access_code_that_comes_up_while_the_still_is_taken_keeps_it_off_the_screen(self):
        seen = []

        def shown():
            seen.append(1)
            return len(seen) > 2                    # not at the play's look, not at the hold's first, then yes
        self.api.access_on_screen = shown
        self.play()
        self.assertIn("still", self.names())
        self.assertNotIn("overlay", self.names())
        self.assertEqual((self.left(), self.tr.given_up), ([], ""))
        self.assertFalse(self.player.props["pause"])

    def test_a_status_request_asks_the_player_nothing(self):
        asked = []
        real = self.player.osd_size
        self.player.osd_size = lambda: asked.append(1) or real()
        for _ in range(5):
            self.api.status({}, None, "t")
        self.assertEqual(asked, [])
        self.player.size = (3840, 2160)
        self.play()                                 # a play looks
        self.assertIn("larger than", self.api.status({}, None, "t")["mix"]["fallback"])

    def test_a_clip_the_operator_froze_after_a_good_crossfade_stays_frozen_when_the_next_play_fails(self):
        from pvj.api import ApiError
        self.play()                                 # a good one: it froze a clip that was playing
        self.player.props["pause"] = True           # Freeze
        self.player.fail = {"play"}
        with self.assertRaises(ApiError):
            self.play("b.mov")
        self.assertTrue(self.player.props["pause"], "the failed play unfroze what the operator froze")

    def test_a_still_that_is_not_the_screen_is_a_cut(self):
        self.player.still_bytes = png(9, 9, (1, 2, 3))
        self.play()
        self.assertNotIn("overlay", self.names())
        self.assertIn("play", self.names())
        self.assertEqual(self.left(), [])
        self.assertTrue(any("a cut instead" in line for line in self.lines), self.lines)

    def test_a_new_clip_that_does_not_start_leaves_the_old_one_playing_with_no_still_over_it(self):
        from pvj.api import ApiError
        self.player.fail = {"play"}
        with self.assertRaises(ApiError) as e:
            self.play()
        self.assertEqual(e.exception.status, 503)
        self.assertEqual(self.player.levels, [255, None])
        self.assertFalse(self.player.props["pause"], "the old clip plays on")
        self.assertEqual(self.left(), [])

    def test_a_clip_somebody_froze_stays_frozen_when_the_new_one_does_not_start(self):
        from pvj.api import ApiError
        self.player.props["pause"] = True
        self.player.fail = {"play"}
        with self.assertRaises(ApiError):
            self.play()
        self.assertTrue(self.player.props["pause"])

    def test_a_screen_too_large_dips_and_the_status_says_why(self):
        self.player.size = (3840, 2160)
        self.play()
        self.assertNotIn("still", self.names())
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "play" not in self.names():       # the dip's own thread plays the clip
            time.sleep(0.01)
        self.assertEqual(self.names()[0], "opacity", "the dip goes down first")
        self.assertIn("play", self.names())
        self.assertIn("larger than", self.api.status({}, None, "t")["mix"]["fallback"])
        self.assertEqual(self.api.status({}, None, "t")["mix"]["transition"], "crossfade")

    def test_a_still_that_takes_too_long_is_used_once_and_then_the_box_dips(self):
        real = self.player.still

        def slow(path):
            self.now[0] += T.SLOW + 0.2
            real(path)
        self.player.still = slow
        self.play()
        self.assertEqual(self.tr.last["ended"], "done")                          # this one was already paid for
        self.assertIn("the still took", self.tr.given_up)
        self.assertIn("the still took", self.api.status({}, None, "t")["mix"]["fallback"])
        del self.player.calls[:]
        self.play()
        self.assertNotIn("pause", self.names())
        self.assertTrue(any("given up" in line for line in self.lines))
        # choosing the transition again lets the box try again
        self.assertNotIn("fallback", self.api.set_mix({"transition": "crossfade", "duration": 1}, None, "t"))
        self.assertEqual(self.tr.given_up, "")

    def test_too_few_steps_a_second_and_the_box_dips_from_then_on(self):
        self.player.on_overlay = lambda: self.now.__setitem__(0, self.now[0] + 0.3)     # every step costs 0.3 seconds
        self.play()
        self.assertLess(len(self.player.levels) - 2, T.MIN_RATE)
        self.assertLessEqual(self.now[0], 51.0 + 0.3 + 0.051, "a slow box makes fewer steps, not a longer transition (one step over at most)")
        self.assertIn("steps in", self.tr.given_up)

    def test_a_clip_that_drops_frames_under_the_steps_and_the_box_dips_from_then_on(self):
        def drop():
            self.player.props["frame-drop-count"] += 1
        self.player.on_overlay = drop
        self.play()
        self.assertEqual(self.tr.last["dropped"], 20)                # one at each step; the still itself came before the count
        self.assertIn("dropped 20 frames", self.tr.given_up)

    def test_a_few_dropped_frames_are_no_reason(self):
        self.hook = lambda: self.player.props.update({"decoder-frame-drop-count": 3})
        self.play()
        self.assertEqual((self.tr.last["dropped"], self.tr.given_up), (3, ""))

    def test_while_an_access_code_is_on_the_display_the_box_dips(self):
        self.api.access_on_screen = lambda: True
        self.play()
        self.assertNotIn("still", self.names())
        self.assertEqual(self.tr.given_up, "", "only this time")


class Settings(Base):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def test_crossfade_can_be_chosen_and_is_kept_in_a_form_the_release_before_reads(self):
        st, out = self.call("POST", "/api/mix", {"transition": "crossfade", "duration": 2}, token=self.full)[:2]
        self.assertEqual((st, out), (200, {"transition": "crossfade", "duration": 2.0}))
        self.assertEqual(self.settings.data["mix"], {"transition": "dip", "style": "crossfade", "duration": 2.0})
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[1]["mix"]["transition"], "crossfade")
        self.call("POST", "/api/mix", {"transition": "cut", "duration": 2}, token=self.full)
        self.assertEqual(self.settings.data["mix"], {"transition": "cut", "duration": 2.0})
        self.assertEqual(self.call("GET", "/api/status", token=self.full)[1]["mix"]["transition"], "cut")
        self.assertNotIn("style", self.call("GET", "/api/status", token=self.full)[1]["mix"])

    def test_another_name_is_refused_with_the_three_there_are(self):
        st, out = self.call("POST", "/api/mix", {"transition": "wipe", "duration": 1}, token=self.full)[:2]
        self.assertEqual(st, 400)
        self.assertIn("cut, dip or crossfade", out["error"])
        self.assertNotIn("not built", out["error"])

    def test_a_value_nobody_knows_in_the_file_plays_as_a_cut(self):
        self.settings.data["mix"] = {"transition": "ripple", "duration": 1.0}
        self.play()
        self.assertEqual(self.names(), ["play", "opacity"])
        self.assertEqual(self.api.status({}, None, "t")["mix"]["transition"], "cut")

    def test_the_schema_is_not_touched(self):
        from pvj import settings as S
        self.assertNotIn("style", S.default_settings()["mix"])
        self.assertEqual(S.default_settings()["mix"]["transition"], "dip")


if __name__ == "__main__":
    unittest.main()


class Threads(ServerBase):
    """The findings of the review that are about threads, with real threads and the real clock."""

    def setUp(self):
        super().setUp()
        import threading
        self.threading = threading
        self.player = self.api.player = Screen(self.rundir)
        self.player.running = True
        self.tr = self.api.transitions
        self.tr.log = lambda line: None
        self.settings.data["mix"] = T.stored("crossfade", 0.3)
        self.gate, self.inside = threading.Event(), threading.Event()
        real = self.player.still

        def still(path):
            self.inside.set()
            self.assertTrue(self.gate.wait(10), "the test never let the still go")
            real(path)
        self.slow_still = still
        self.addCleanup(self.gate.set)

    def background(self, fn):
        out = []
        t = self.threading.Thread(target=lambda: out.append(fn()), daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        return t, out

    def timed(self, fn):
        import time
        began = time.monotonic()
        fn()
        return time.monotonic() - began

    def names(self):
        return [c[0] for c in self.player.calls]

    def settle(self):
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (self.tr.running is not None or any(t.name.startswith("transition") for t in self.threading.enumerate())):
            time.sleep(0.01)

    def test_two_plays_between_a_still_and_its_steps_make_one_set_of_steps(self):
        # finding 2: run() took the current token, so the first play's run started a second worker on the
        # second play's still, and the two sent different alpha in turn
        first = self.tr.hold("crossfade")
        second = self.tr.hold("crossfade")
        self.assertTrue(first and second and first != second)
        del self.player.levels[:]
        self.tr.run(first, 0.3)                     # the first play gets to its run late: it must do nothing
        self.tr.run(second, 0.3)
        self.assertLessEqual(sum(1 for t in self.threading.enumerate() if t.name == "transition"), 1)
        self.settle()
        steps = [v for v in self.player.levels if v is not None]
        self.assertGreaterEqual(len(steps), 3)
        self.assertEqual(steps, sorted(steps, reverse=True), "two workers stepped in turn: %s" % steps)
        self.assertEqual(self.player.levels[-1], None)

    def test_two_plays_at_once_end_with_one_clean_transition(self):
        barrier = self.threading.Barrier(2)

        def play(name):
            barrier.wait(5)
            return self.api.play({"file": name}, None, "t")
        a, _ = self.background(lambda: play("a.mp4"))
        b, _ = self.background(lambda: play("b.mov"))
        a.join(5), b.join(5)
        self.settle()
        self.assertIsNone(self.tr.running)
        self.assertEqual(self.player.levels[-1], None, "a still was left on the screen")
        last = len(self.player.levels) - 1 - self.player.levels[::-1].index(255)
        steps = [v for v in self.player.levels[last:] if v is not None]
        self.assertEqual(steps, sorted(steps, reverse=True), "after the last still the steps went up and down: %s" % steps)
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("transition") or n.startswith("overlay")], [])

    def test_stop_does_not_wait_for_a_still_and_nothing_loads_after_it(self):
        # finding 5: Stop waited behind the still, and the clip could load after it
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        took = self.timed(lambda: self.api.control({"action": "stop"}, None, "t"))
        self.assertLess(took, 1.0, "Stop waited for the still")
        self.assertIn("clear", self.names())
        self.gate.set()
        t.join(5)
        self.assertEqual(out, [{"playing": "a.mp4"}])
        self.assertNotIn("play", self.names(), "the clip was loaded after the Stop")
        self.assertNotIn("overlay", self.names(), "the still was laid over the cleared screen")
        self.assertFalse(self.player.props["pause"])
        self.assertEqual(self.tr.given_up, "")

    def test_blackout_fade_out_and_opacity_do_not_wait_for_a_still(self):
        for act in (lambda: self.api.blackout({"on": True}, None, "t"), lambda: self.api.fadeout({"seconds": 0.1}, None, "t"),
                    lambda: self.api.control({"action": "opacity", "value": 40}, None, "t")):
            self.api.mix.update(blackout=False, opacity=100)
            self.api.fader.cancel()
            self.gate.clear(), self.inside.clear()
            del self.player.calls[:]
            self.player.still = self.slow_still
            t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
            self.assertTrue(self.inside.wait(5))
            self.assertLess(self.timed(act), 1.0, "it waited for the still")
            self.gate.set()
            t.join(5)
            self.assertNotIn("overlay", self.names(), "the still was laid down after the picture changed")
            self.assertIn("play", self.names(), "the clip still plays: only the blend is given up")
            self.assertEqual(self.tr.given_up, "")
            self.api.fader.cancel()

    def test_a_controllers_play_answers_at_once_and_its_blackout_is_not_held_behind_the_still(self):
        from pvj.midi import MIDI_DEVICE
        self.player.still = self.slow_still
        took = self.timed(lambda: self.api.play({"file": "a.mp4"}, MIDI_DEVICE, "midi"))
        self.assertLess(took, 1.0, "the controller's thread waited for the still")
        self.assertTrue(self.inside.wait(5))
        self.assertLess(self.timed(lambda: self.api.blackout({"on": True}, MIDI_DEVICE, "midi")), 1.0)
        self.assertIn(("opacity", 0), self.player.calls)
        self.gate.set()
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "play" not in self.names():
            time.sleep(0.01)
        self.assertIn("play", self.names())
        self.assertNotIn("overlay", self.names())

    def test_a_panels_play_still_answers_after_the_clip_was_asked_for(self):
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertIn("play", self.names(), "a play from the panel hears of a clip that does not start")
        self.settle()

    def test_the_time_a_play_waited_behind_another_still_is_not_its_stills_time(self):
        # finding 4: the clock started before the lock, so a double tap could make the box give up for the show
        now = [100.0]
        self.tr._clock = lambda: now[0]
        self.assertTrue(self.tr._holding.acquire(timeout=1))        # another play is taking its still
        t, out = self.background(lambda: self.tr.hold("crossfade"))
        import time
        time.sleep(0.2)
        self.assertEqual(out, [], "the hold did not wait its turn")
        now[0] += T.SLOW + 5
        self.tr._holding.release()
        t.join(5)
        self.assertTrue(out and out[0])
        self.assertEqual(self.tr.given_up, "")
        self.assertLess(self.tr.last["still_ms"], 1000)
        self.tr.end()
