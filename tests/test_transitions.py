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
        self.screen = (8, 4)
        self.colour = (200, 100, 50)
        self.props = {"pid": 4242, "pause": False, "time-pos": 0.5, "seeking": False, "idle-active": False,
                      "frame-drop-count": 0, "decoder-frame-drop-count": 0}
        self.ipc = self
        self.levels = []                # the alpha of every overlay drawn, in order; None for a removal
        self.parts = []                 # (x, y, offset, width, height) of every part of a still drawn
        self.still_bytes = None         # what the next still holds instead of the screen
        self.fail = set()               # names of calls that raise PlayerError
        self.on_overlay = None
        # as pvj.player.Player: one lock for every change of what plays, and an epoch that a generator must hold
        import threading
        self._lock = threading.RLock()
        self.source_epoch, self.source_shader, self.path, self.carrier = 0, None, None, None
        self.socket_path = os.path.join(rundir, "player.sock")
        self.overlay_up = False
        self.unlocked = []              # changes of what plays that were made without the lock (there must be none)
        self.before_play = None         # called by play(), inside it, before the clip is noted

    def _held(self, what):
        if not self._lock._is_owned():
            self.unlocked.append(what)

    def claim_screen(self):
        self._held("claim_screen")
        with self._lock:
            self.source_epoch += 1

    def play_pipe(self, path, width, height, fps):
        with self._lock:
            self.source_epoch += 1
            self.source_shader, self.path, self.carrier = None, path, None
            self.calls.append(("play_pipe", path))
            self.props["pause"] = False

    def play_source(self, shader, carrier, epoch=None, spawn=False):
        with self._lock:
            if epoch is not None and epoch != self.source_epoch:
                return None
            self.source_shader, self.path, self.carrier = shader, carrier, carrier
            self.source_epoch += 1
            self.calls.append(("play_source", os.path.basename(shader)))
            return self.source_epoch

    def clear(self):
        with self._lock:
            self.source_epoch += 1
            self.source_shader, self.path, self.carrier = None, None, None
            self.calls.append(("clear",))

    def request(self, *command):
        if command[0] == "get_property":
            if "ipc" in self.fail:
                raise PlayerError("player is not running")
            if command[1] == "path":
                return self.path
            v = self.props.get(command[1])
            if v is GONE:
                raise PlayerError("mpv: property unavailable")
            return v
        return None

    def osd_size(self):
        return self.screen

    def status(self):
        return {"running": self.running, "path": None}

    def pause(self, value=None):
        self.calls.append(("pause", value))
        self.props["pause"] = bool(value)
        return bool(value)

    def play(self, *a, **kw):
        with self._lock:
            if self.before_play:
                self.before_play()
            if "play" in self.fail:
                raise PlayerError("player is not running")
            super().play(*a, **kw)
            self.source_epoch += 1
            self.source_shader, self.path, self.carrier = None, a[0][0], None
            self.props["pause"] = False

    def still(self, path):
        self.calls.append(("still", os.path.basename(path)))
        if "still" in self.fail:
            raise PlayerError("mpv: error running command")
        assert os.path.exists(path), "the panel makes the file; the player may not make one in the panel's folder"
        with open(path, "wb") as f:
            f.write(self.still_bytes if self.still_bytes is not None else png(self.screen[0], self.screen[1], self.colour))

    def overlay(self, oid, x, y, w, h, pixels):
        if "overlay" in self.fail:
            raise PlayerError("no reply from mpv")
        assert len(pixels) == w * h * 4
        self.calls.append(("overlay", oid, x, y, w, h))
        self.overlay_up = True
        self.levels.append(pixels[3])
        self.pixels = pixels
        with open(os.path.join(self.rundir, "overlay-%d.bgra" % oid), "wb") as f:
            f.write(pixels)
        if self.on_overlay:
            self.on_overlay()

    def overlay_part(self, oid, path, x, y, offset, w, h, stride):
        if "overlay" in self.fail:
            raise PlayerError("no reply from mpv")
        size = os.path.getsize(path)
        assert offset + h * stride <= size, "the player maps offset + height x stride bytes: %d of %d" % (offset + h * stride, size)
        self.calls.append(("overlay_part", oid, x, y, offset, w, h, stride))
        self.overlay_up = True
        self.parts.append((x, y, offset, w, h))
        self.levels.append(255)
        if self.on_overlay:
            self.on_overlay()

    def overlay_remove(self, oid):
        self.calls.append(("overlay_remove", oid))
        self.overlay_up = False
        self.levels.append(None)
        try:
            os.unlink(os.path.join(self.rundir, "overlay-%d.bgra" % oid))
        except OSError:
            pass


class Helper:
    """A live input's helper as pvj.capture.Capture is to the panel: prepare stops the one before, start runs one."""

    def __init__(self, rundir):
        import threading
        self.lock = threading.RLock()
        self.fifo = os.path.join(rundir, "capture.fifo")
        self.running, self.most, self.stops = 0, 0, 0
        self.on_prepare = None

    def prepare(self, device, mode):
        if self.on_prepare:
            self.on_prepare()
        self.stop()
        return 1280, 720, 30

    def start(self, device, mode):
        self.running += 1
        self.most = max(self.most, self.running)

    def stop(self):
        self.stops += 1
        self.running = 0


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
        self.assertLess(self.names().index("overlay_remove"), i, "the still goes before the screen is cleared")

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
        self.player.screen = (3840, 2160)
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
        self.player.screen = (3840, 2160)
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


class Moves(Base):
    """The wipes and the slides: the same still, of which a part is drawn."""

    def go(self, name, seconds=1.0):
        self.settings.data["mix"] = T.stored(name, seconds)
        self.player.screen = (40, 20)
        self.play()

    def test_what_each_draws_of_the_still_at_the_start_half_way_and_at_the_end(self):
        w, h, row = 40, 20, 160
        want = {"wipe-from-left": (20, 0, 80, 20, 20), "wipe-from-right": (0, 0, 0, 20, 20),
                "wipe-from-top": (0, 10, 10 * row, 40, 10), "wipe-from-bottom": (0, 0, 0, 40, 10),
                "slide-left": (0, 0, 80, 20, 20), "slide-right": (20, 0, 0, 20, 20),
                "slide-up": (0, 0, 10 * row, 40, 10), "slide-down": (0, 10, 0, 40, 10)}
        self.assertEqual(sorted(want), sorted(T.WIPES + T.SLIDES))
        for name, half in want.items():
            blend = T.BLENDS[name]()
            self.assertEqual(blend.step(w, h, None, 0.0), (0, 0, 0, w, h), name)
            self.assertEqual(blend.step(w, h, None, 0.5), half, name)
            self.assertIsNone(blend.step(w, h, None, 1.0), name)
            x, y, offset, pw, ph = half
            self.assertTrue(0 <= x and x + pw <= w and 0 <= y and y + ph <= h, "%s draws outside the screen" % name)

    def test_a_wipe_writes_the_still_once_and_then_only_names_parts_of_it(self):
        self.go("wipe-from-left")
        names = self.names()
        self.assertEqual(names[:4], ["pause", "still", "overlay_part", "play"])
        self.assertNotIn("overlay", names, "a wipe rewrote the still's bytes")
        parts = self.player.parts
        self.assertEqual(parts[0], (0, 0, 0, 40, 20))
        self.assertEqual(len(parts) - 1, 30, "thirty steps a second for a wipe")
        xs = [p[0] for p in parts[1:]]
        self.assertEqual(xs, sorted(xs))
        self.assertTrue(all(p[0] + p[3] == 40 and p[2] == p[0] * 4 for p in parts), "the right edge stays where it is")
        self.assertEqual(self.player.levels[-1], None)
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertEqual(self.left(), [], "the still's file is gone")

    def test_the_stills_file_is_the_picture_and_one_row_more(self):
        seen = []
        self.player.on_overlay = lambda: seen.append(os.path.getsize(os.path.join(self.rundir, "transition-%d.bgra" % os.getpid())))
        self.go("slide-left")
        self.assertEqual(set(seen), {40 * 20 * 4 + 40 * 4})

    def test_every_one_runs_whole_and_leaves_nothing(self):
        for name in T.WIPES + T.SLIDES:
            del self.player.calls[:], self.player.parts[:], self.player.levels[:]
            self.now[0] = 50.0
            self.go(name, 0.5)
            self.assertEqual((self.tr.last["name"], self.tr.last["ended"], self.tr.last["steps"]), (name, "done", 15), name)
            self.assertEqual(self.left(), [], name)
            self.assertEqual(self.tr.given_up, "", name)

    def test_blackout_and_stop_end_a_wipe_and_its_file_goes(self):
        for act in (lambda: self.api.blackout({"on": True}, None, "t"), lambda: self.api.control({"action": "stop"}, None, "t")):
            self.api.mix.update(blackout=False)
            del self.player.levels[:]
            self.hook = act
            self.go("wipe-from-top")
            self.assertEqual(self.player.levels[-1], None)
            self.assertLess(len(self.player.levels), 6)
            self.assertEqual(self.left(), [])

    def test_a_second_play_during_a_wipe_starts_from_the_screen(self):
        self.hook = lambda: self.play("b.mov")
        self.go("slide-up")
        self.assertEqual(self.names().count("still"), 2)
        self.assertEqual(self.player.levels[-1], None)
        self.assertEqual(self.left(), [])

    def test_the_limits_are_the_crossfades(self):
        self.player.on_overlay = lambda: self.now.__setitem__(0, self.now[0] + 0.3)
        self.go("wipe-from-right")
        self.assertIn("steps in", self.tr.given_up)
        del self.player.calls[:]
        self.play()
        self.assertNotIn("still", self.names(), "the box dips from then on")

    def test_each_is_kept_in_the_form_an_older_release_reads(self):
        for name in T.WIPES + T.SLIDES:
            self.assertEqual(self.api.set_mix({"transition": name, "duration": 1}, None, "t"), {"transition": name, "duration": 1.0})
            self.assertEqual(self.settings.data["mix"], {"transition": "dip", "style": name, "duration": 1.0})
            self.assertEqual(self.api.status({}, None, "t")["mix"]["transition"], name)

    def test_the_panel_starting_removes_a_stills_file(self):
        for n in ("transition-77.bgra", "transition-77.bgra.tmp", "transition-77.png"):
            open(os.path.join(self.rundir, n), "w").close()
        self.tr.tidy()
        self.assertEqual(self.left(), [])


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
        self.assertIn("cut, dip, crossfade, wipe-from-left", out["error"])
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
        self.played = threading.Event()
        self.player.before_play = self.played.set
        self.looked = threading.Event()             # set when a play has looked whether it is still the newest wish
        newest = self.tr.newest
        self.looks = []
        self.tr.newest = lambda ticket: self.looks.append(1) or self.looked.set() or newest(ticket)
        real = self.player.still

        self.stuck = []

        def still(path):
            self.inside.set()
            if not self.gate.wait(10):          # noted, not asserted: the code under test catches what a still raises
                self.stuck.append(path)
            real(path)
        self.slow_still = still
        self.addCleanup(lambda: self.assertEqual(self.stuck, [], "a test never let the still go"))
        self.addCleanup(self.gate.set)
        self.held = []                          # threads the panel asked for and the test has not started yet

    def hold_threads_back(self):
        """From now on a thread that pvj/api.py starts is only noted; `release()` runs them, in the order given."""
        import pvj.api as api_module
        test = self

        class Held:
            def __init__(self, target=None, name=None, daemon=None, args=()):
                self.target, self.args, self.name = target, args, name

            def start(self):
                test.held.append(self)
        real = api_module.threading.Thread
        api_module.threading.Thread = Held
        self.addCleanup(setattr, api_module.threading, "Thread", real)

    def turn_taken(self):
        """Take the stills' turn, as another play would while its still is in the player, and return an event that
        is set when somebody else comes to wait for it (so a test never guesses with a sleep that somebody waits)."""
        real, waiting = self.tr._holding, self.threading.Event()

        class Watched:
            def acquire(self, *a, **k):
                waiting.set()
                return real.acquire(*a, **k)

            def __enter__(self):
                self.acquire()

            def __exit__(self, *exc):
                real.release()

            def release(self):
                real.release()

            def locked(self):
                return real.locked()
        self.assertTrue(real.acquire(timeout=1))
        self.tr._holding = Watched()
        return waiting

    def release(self, order=None):
        held, self.held = self.held, []
        for i in (order if order is not None else range(len(held))):
            held[i].target(*held[i].args)

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
        workers = []
        self.tr._thread = False                     # no clock in this test: who would step is noted, nobody steps
        self.tr._work = lambda token, seconds, name: workers.append(token)
        self.tr.run(first, 0.3)                     # the first play gets to its run late: it must do nothing
        self.tr.run(second, 0.3)
        self.assertEqual(workers, [second], "the first play's run started a worker on the second play's still")
        self.tr.end()

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
        self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}])
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
        waiting = self.turn_taken()                                 # another play is taking its still
        t, out = self.background(lambda: self.tr.hold("crossfade"))
        self.assertTrue(waiting.wait(5), "the hold never came to wait its turn")
        self.assertEqual(out, [], "the hold did not wait its turn")
        now[0] += T.SLOW + 5
        self.tr._holding.release()
        t.join(5)
        self.assertTrue(out and out[0])
        self.assertEqual(self.tr.given_up, "")
        self.assertLess(self.tr.last["still_ms"], 1000)
        self.tr.end()

    # -- the second review: which wish is the newest --
    def loaded(self):
        return [os.path.basename(p["paths"][0]) for p in self.player.plays]

    def test_a_stop_that_is_handled_before_the_controllers_thread_starts_is_seen_by_it(self):
        # M2: the snapshot was taken on the new thread, so a Stop that came before the thread ran was not newer
        from pvj.midi import MIDI_DEVICE
        self.hold_threads_back()
        self.assertEqual(self.api.play({"file": "a.mp4"}, MIDI_DEVICE, "midi"), {"playing": "a.mp4", "pending": True})
        self.assertEqual(len(self.held), 1)
        self.api.control({"action": "stop"}, MIDI_DEVICE, "midi")
        del self.player.calls[:]
        self.release()
        self.assertEqual(self.player.calls, [], "the clip's thread touched the player after the Stop")

    def test_every_other_way_of_playing_during_a_still_is_the_newer_wish(self):
        # M1: only Stop was counted; a preset, a stream, the test pattern, a tone or a sync client's clip started
        # while a clip's still was taken was loaded over by that clip
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.registry.set_enabled("inputs-srt", True)
        for what, act in (("a preset", lambda: self.api.play({"preset": "startless"}, None, "t")),
                          ("a stream", lambda: self.api.play({"stream": "bbbb0001"}, None, "t")),
                          ("the test pattern", lambda: self.api.test_pattern({"on": True}, None, "t")),
                          ("a tone", lambda: self.api.test_tone({"channel": "left"}, None, "t")),
                          ("a sync client's clip", lambda: self.api.sync._local_player().load(os.path.join(self.media, "b.mov"), True)),
                          ("another clip", lambda: self.api.play({"file": "b.mov"}, None, "other"))):
            self.gate.clear(), self.inside.clear()
            del self.player.calls[:], self.player.plays[:]
            self.tr.given_up = ""
            real, self.player.still = self.player.still, self.slow_still
            t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
            self.assertTrue(self.inside.wait(5), what)
            self.player.still = real                    # the newer play's own still, if it takes one, is not held
            other = self.threading.Thread(target=act, daemon=True)
            other.start()                               # "another clip" waits its turn behind the still
            if what != "another clip":
                other.join(5)
                self.assertEqual(len(self.player.plays), 1, what)
            theirs = list(self.player.plays)
            self.gate.set()
            t.join(5), other.join(5)
            self.settle()
            self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}], "%s: a clip that did not load answered that it plays" % what)
            if what == "another clip":
                self.assertEqual(self.loaded(), ["b.mov"], "the older clip loaded as well, or instead")
            else:
                self.assertEqual(self.player.plays, theirs, "%s was started while the clip's still was taken, and the clip loaded over it" % what)
            self.assertFalse(self.player.props["pause"], what)

    def shader(self):
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        return self.api.shaders.library()[0]["id"]

    def test_a_generator_the_operator_chose_during_a_still_keeps_the_screen(self):
        sid = self.shader()
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        self.assertTrue(self.api.shaders.show(sid)["ok"])          # the real engine: no epoch, the operator's own choice
        self.gate.set()
        t.join(5)
        self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}])
        self.assertEqual(self.loaded(), [])
        self.assertIsNotNone(self.player.source_shader, "the clip took the generator off the screen")
        self.assertNotIn("overlay", self.names())

    def test_a_rotation_tick_never_beats_a_tap_that_waits_for_its_still(self):
        # H1 of the third review: the ticket was taken before the screen was claimed, so a rotation that got the
        # player in between became the newer wish and the operator's clip was never loaded
        sid = self.shader()
        epoch = self.player.source_epoch                            # what a rotation holds from before the tap
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        self.assertIsNone(self.api.shaders.show(sid, epoch=epoch, cut=False), "the rotation's change was not refused")
        self.gate.set()
        t.join(5)
        self.assertEqual(out, [{"playing": "a.mp4"}])
        self.assertEqual(self.loaded(), ["a.mp4"])
        self.assertIsNone(self.player.source_shader)
        self.settle()

    def test_a_rotation_that_comes_between_the_tap_and_its_claim_of_the_screen_is_refused(self):
        # H1 itself: the rotation gets its turn at the very moment the tap has taken its ticket. With the ticket
        # and the claim of the screen as one step under the player's lock it has to wait, and is then refused.
        sid = self.shader()
        epoch = self.player.source_epoch
        results, real = [], self.tr.claim

        def claim():
            ticket = real()
            rotation = self.threading.Thread(target=lambda: results.append(self.api.shaders.show(sid, epoch=epoch, cut=False)), daemon=True)
            rotation.start()
            rotation.join(1.0)                      # it must not get through while the tap holds the lock
            self.rotation = rotation
            return ticket
        self.tr.claim = claim
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.assertEqual(self.api.play({"file": "a.mp4"}, None, "t"), {"playing": "a.mp4"}, "a rotation beat the operator's tap")
        self.rotation.join(5)
        self.assertEqual(results, [None], "the rotation's change was not refused")
        self.assertEqual(self.loaded(), ["a.mp4"])
        self.assertIsNone(self.player.source_shader)

    def test_a_rotation_tick_never_beats_a_tap_that_dips(self):
        sid = self.shader()
        self.settings.data["mix"] = {"transition": "dip", "duration": 0.4}
        epoch = self.player.source_epoch
        self.assertEqual(self.api.play({"file": "a.mp4"}, None, "t"), {"playing": "a.mp4", "pending": True})    # on its way down
        self.assertIsNone(self.api.shaders.show(sid, epoch=epoch, cut=False))
        self.assertTrue(self.played.wait(5), "the tapped clip was never loaded: the screen stays black")
        self.assertEqual(self.loaded(), ["a.mp4"])

    def test_the_screen_is_claimed_and_the_ticket_taken_as_one_step_under_the_players_lock(self):
        seen = []
        real = self.tr.claim
        self.tr.claim = lambda: seen.append(self.player._lock._is_owned()) or real()
        self.api.play({"file": "a.mp4"}, None, "t")
        self.settle()
        self.assertEqual(seen, [True], "the ticket was taken outside the lock the screen is claimed under")
        self.assertEqual(self.player.unlocked, [], "the screen was claimed outside the player's lock")

    def test_the_last_look_and_the_load_are_one_step_under_the_players_lock(self):
        looks = []
        real = self.tr.newest
        self.tr.newest = lambda ticket: looks.append(self.player._lock._is_owned()) or real(ticket)
        loads = []
        self.player.before_play = lambda: loads.append(self.player._lock._is_owned())
        self.api.play({"file": "a.mp4"}, None, "t")
        self.settle()
        self.assertEqual(loads, [True], "the clip was loaded outside the player's lock")
        self.assertIs(looks[-1], True, "the last look at the newest wish was made outside the lock the load is made under")

    def test_of_many_plays_from_a_controller_the_last_wins_and_one_still_is_taken(self):
        # M3: one thread and one still each, in turn, and the last asked for need not be the last loaded
        from pvj.midi import MIDI_DEVICE
        for order in (None, [4, 3, 2, 1, 0], [2, 4, 0, 3, 1]):
            self.hold_threads_back()
            del self.player.calls[:], self.player.plays[:]
            for name in ("a.mp4", "b.mov", "a.mp4", "a.mp4", "b.mov"):
                self.api.play({"file": name}, MIDI_DEVICE, "midi")
            self.assertEqual(len(self.held), 5)
            self.tr._thread = False
            self.tr._work = lambda token, seconds, name: None
            self.release(order)
            self.assertEqual(self.names().count("still"), 1, "a still for each of the plays that were overtaken")
            self.assertEqual(self.names().count("pause"), 1)
            self.assertEqual(self.loaded(), ["b.mov"], "the clip on the screen is not the last one asked for")
            self.tr.end()

    def test_a_flood_of_plays_on_real_threads_ends_with_the_last_one(self):
        from pvj.midi import MIDI_DEVICE
        names = ["flood%02d.mp4" % i for i in range(13)]
        for name in names:
            open(os.path.join(self.media, name), "w").close()
        self.player.still = self.slow_still             # the first play's still is in the player while the others come
        for name in names:
            self.api.play({"file": name}, MIDI_DEVICE, "midi")
        self.assertTrue(self.inside.wait(5))
        self.gate.set()
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(t.name == "transition-play" for t in self.threading.enumerate()):
            time.sleep(0.01)
        self.settle()
        self.assertEqual(self.loaded()[-1], "flood12.mp4", "the clip on the screen is not the last one asked for")
        self.assertLessEqual(len(self.loaded()), 2)
        self.assertLessEqual(self.names().count("still"), 2, "more stills than the one in flight and the last")
        self.assertIsNone(self.tr.running)

    def test_an_end_that_comes_while_a_play_waits_its_turn_takes_its_blend_and_keeps_its_brightness(self):
        # M1: pad A, pad B, Fade out could end with a lit still over the fade
        waiting = self.turn_taken()                                     # another play's still is being taken
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(waiting.wait(5), "the play never came to wait its turn")
        self.assertEqual(out, [])
        self.api.blackout({"on": True}, None, "t")
        del self.player.calls[:]
        self.tr._holding.release()
        t.join(5)
        self.assertEqual(out, [{"playing": "a.mp4"}])
        self.assertEqual(self.names(), ["play"], "no still, no freeze, the clip under the dark, and its brightness left alone")

    def test_the_screen_is_looked_at_again_when_the_still_would_be_laid(self):
        asked = []
        self.assertEqual(self.tr.hold("crossfade", self.tr.claim(), lambda: asked.append(1) or False), 0)
        self.assertEqual(asked, [1])
        self.assertIn("still", self.names())
        self.assertNotIn("overlay", self.names())
        self.assertFalse(self.player.props["pause"])

    def test_a_play_whose_turn_comes_after_the_box_gave_up_takes_no_still(self):
        waiting = self.turn_taken()
        t, out = self.background(lambda: self.tr.hold("crossfade", self.tr.claim()))
        self.assertTrue(waiting.wait(5), "the hold never came to wait its turn")
        self.tr.given_up = "the still took 3.0 seconds"                 # the still before this one said so
        self.tr._holding.release()
        t.join(5)
        self.assertEqual((out, self.player.calls), ([0], []))

    def test_abandoning_an_older_hold_leaves_a_newer_ones_still(self):
        first = self.tr.hold("crossfade")
        second = self.tr.hold("crossfade")
        self.tr.abandon(first)
        self.assertEqual(self.tr.running, "crossfade", "the newer play's still was taken off by the older one")
        self.assertNotEqual(self.player.levels[-1], None)
        self.assertTrue(self.player.props["pause"], "the older play thawed a clip the newer play's still stands for")
        self.tr.abandon(second)
        self.assertIsNone(self.tr.running)
        self.assertEqual(self.player.levels[-1], None)
        self.assertFalse(self.player.props["pause"], "a pause was left with no owner")

    def test_abandon_is_not_an_end_a_play_that_waits_its_turn_keeps_its_blend(self):
        # low, third review: abandon() went through end(), which takes the blend off every play that waits
        older = self.tr.hold("crossfade")
        ticket = self.tr.claim()                                        # a newer play, about to wait its turn
        self.tr.abandon(older)                                          # the older one finds it is overtaken
        self.assertIsNone(self.tr.running)
        self.assertFalse(self.tr.ended_since(ticket))
        token = self.tr.hold("crossfade", ticket)
        self.assertTrue(token, "the newer play lost its blend to the older play's clean-up")
        self.tr.abandon(token)

    def test_a_freeze_is_handed_on_and_never_left_without_an_owner(self):
        first = self.tr.hold("crossfade")                               # froze a clip that played
        self.assertTrue(self.player.props["pause"])
        self.player.props["idle-active"] = True                         # the next hold comes to nothing
        self.assertEqual(self.tr.hold("crossfade"), 0)
        self.assertFalse(self.player.props["pause"], "the hold that came to nothing left the first one's freeze behind")
        self.tr.abandon(first)

    def test_the_thaw_belongs_to_the_hold_that_froze(self):
        token = self.tr.hold("crossfade")
        self.assertTrue(self.player.props["pause"])
        self.tr.abandon(token + 100)                                    # somebody else's
        self.assertTrue(self.player.props["pause"])
        self.tr.abandon(token)
        self.assertFalse(self.player.props["pause"])

    def test_an_older_play_that_finds_a_newer_one_waiting_leaves_it_its_blend(self):
        # the older play has its still and is about to load; the newer one waits for its turn at the still
        holding = self.threading.Event()
        go = self.threading.Event()
        real_play = self.player.before_play

        def parked():
            holding.set()
        first_token = []
        real_hold = self.tr.hold

        def hold(name, ticket=None, wanted=None):
            token = real_hold(name, ticket, wanted)
            if not first_token:
                first_token.append(token)
                holding.set()
                go.wait(5)                                              # parked between its still and its load
            return token
        self.tr.hold = hold
        a, out_a = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(holding.wait(5))
        b, out_b = self.background(lambda: self.api.play({"file": "b.mov"}, None, "t"))
        b.join(5)
        go.set()
        a.join(5)
        self.settle()
        self.assertEqual(out_a, [{"playing": None, "superseded": "a.mp4"}])
        self.assertEqual(out_b, [{"playing": "b.mov"}])
        self.assertEqual(self.loaded(), ["b.mov"])
        self.assertEqual(self.names().count("still"), 2, "the newer play blended from a still of its own")
        self.assertEqual(self.tr.last["ended"], "done")

    def test_next_and_previous_are_newer_wishes_and_end_a_blend(self):
        for action in ("next", "prev"):
            self.gate.clear(), self.inside.clear()
            del self.player.calls[:], self.player.plays[:]
            real, self.player.still = self.player.still, self.slow_still
            t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
            self.assertTrue(self.inside.wait(5))
            self.player.still = real
            self.api.control({"action": action}, None, "t")
            self.gate.set()
            t.join(5)
            self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}], action)
            self.assertEqual(self.loaded(), [], action)
            self.assertNotIn("overlay", self.names(), action)

    def test_a_play_that_fails_is_still_the_newest_wish(self):
        # stated in D71: the operator's last tap was the one that failed; what it overtook is not brought back
        from pvj.api import ApiError
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.player.fail = {"play"}
        with self.assertRaises(ApiError):
            self.api.play({"file": "b.mov"}, None, "t")
        self.player.fail = set()
        self.gate.set()
        t.join(5)
        self.assertEqual(self.loaded(), [])
        self.assertFalse(self.player.props["pause"], "the clip that was there plays on")

    # -- the dip: the clip hangs on its end and on its being cut short alike --
    def dip(self, seconds=2.0):
        self.settings.data["mix"] = {"transition": "dip", "duration": seconds}
        return self.api.play({"file": "a.mp4"}, None, "t")

    def test_a_blackout_a_fade_a_reset_or_an_opacity_change_during_a_dip_still_loads_the_clip(self):
        # older than this branch: the load hung on the fader's token, so any of these (a MIDI opacity fader that
        # moves) meant the tapped clip was silently never loaded
        for what, act, level in (("Blackout", lambda: self.api.blackout({"on": True}, None, "t"), 0),
                                 ("Fade out", lambda: self.api.fadeout({"seconds": 30}, None, "t"), None),
                                 ("Fade in", lambda: self.api.fadein({"seconds": 30}, None, "t"), None),
                                 ("Reset", lambda: self.api.control({"action": "reset"}, None, "t"), 255),
                                 ("an opacity change", lambda: self.api.control({"action": "opacity", "value": 40}, None, "t"), 102)):
            self.api.mix.update(blackout=False, opacity=100)
            self.api.fader.cancel()
            self.played.clear()
            del self.player.calls[:], self.player.plays[:]
            self.assertEqual(self.dip(), {"playing": "a.mp4", "pending": True}, "a dip answers before its clip has loaded")
            act()
            self.assertTrue(self.played.wait(5), "%s during the dip: the tapped clip was never loaded" % what)
            self.assertEqual(self.loaded(), ["a.mp4"], what)
            after = [c for c in self.player.calls[self.names().index("play"):] if c[0] == "opacity"]
            if level is not None:
                self.assertTrue(all(c[1] == level for c in after), "%s: the clip's load changed the level that was set: %s" % (what, after))
                self.assertEqual([c for c in self.player.calls if c[0] == "opacity"][-1], ("opacity", level), what)
            self.api.fader.cancel()

    def test_a_stop_during_a_dip_drops_the_clip_and_does_not_leave_the_picture_dark(self):
        # M1 of the third review: the fader had gone towards black and nobody put the picture back
        self.dip()
        self.looked.clear()
        self.api.control({"action": "stop"}, None, "t")
        self.assertTrue(self.looked.wait(5), "the dip's clip never came to look whether it is still wanted")
        self.assertEqual(self.loaded(), [])
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"][-1], ("opacity", 255), "the next shader would come up dark")

    def test_a_stop_during_a_dip_leaves_blackout_and_a_fade_out_as_they_are(self):
        self.api.blackout({"on": True}, None, "t")
        self.api._level_back()
        self.assertEqual(self.player.calls[-1], ("opacity", 0))
        self.api.blackout({"on": False}, None, "t")
        self.api.fadeout({"seconds": 30}, None, "t")
        del self.player.calls[:]
        self.api._level_back()
        self.assertNotIn(("opacity", 255), self.player.calls, "a Stop undid the operator's Fade out")
        self.api.fader.cancel()

    def test_a_dip_that_is_overtaken_after_its_way_down_loads_nothing_and_the_picture_comes_back(self):
        # the test has the player's lock, as a Stop's clear would: the clip's look and load wait for it
        at_black = self.threading.Event()
        real = self.player.opacity
        self.player.opacity = lambda value: (real(value), at_black.set() if value == 0 else None)
        with self.player._lock:
            self.dip(0.2)
            self.assertTrue(at_black.wait(5), "the dip never reached black")
            self.looked.clear()
            self.api.control({"action": "stop"}, None, "t")
        self.assertTrue(self.looked.wait(5))
        self.assertEqual(self.loaded(), [], "the clip loaded after the Stop")
        self.assertEqual([c for c in self.player.calls if c[0] == "opacity"][-1], ("opacity", 255))

    def test_a_newer_play_during_a_dip_drops_the_older_clip(self):
        self.dip()
        del self.looks[:]
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.assertEqual(self.api.play({"file": "b.mov"}, None, "t"), {"playing": "b.mov"})
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(self.looks) < 2:      # the cut's own look, and the dip's
            time.sleep(0.005)
        self.assertGreaterEqual(len(self.looks), 2, "the older clip never came to look whether it is still wanted")
        self.assertEqual(self.loaded(), ["b.mov"])

    def test_a_live_input_started_during_a_still_is_not_stopped_by_the_clip_it_overtook(self):
        helper = self.api.capture = Helper(self.rundir)
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")
        before = helper.stops
        self.gate.set()
        t.join(5)
        self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}])
        self.assertEqual((helper.stops, helper.running), (before, 1), "the clip that was overtaken stopped the live input that overtook it")

    def clip_parked_before_it_stops_the_helper(self):
        """A clip that has loaded and has come to the look at the live input's helper, where it waits for the lock a
        live input is started under: the test holds it. Returns the clip's thread."""
        helper = self.api.capture = Helper(self.rundir)
        helper.start("video0", "720p30")                                # a live input is what was on the screen
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.assertTrue(helper.lock.acquire(timeout=1))
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.played.wait(5))
        return helper, t

    def test_a_live_input_started_after_a_clip_loaded_and_before_its_look_is_left_alone(self):
        # the case the first version of this test did not reach: the clip HAS loaded, and looks afterwards
        helper, t = self.clip_parked_before_it_stops_the_helper()
        self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")      # this thread has the lock (it is re-entrant)
        before = helper.stops
        helper.lock.release()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual((helper.stops, helper.running), (before, 1), "the clip stopped a live input that was started after it had loaded")

    def test_a_clip_that_loaded_stops_the_old_helper_even_when_a_newer_wish_came_since(self):
        # low, fourth review: decided by "is this still the newest wish", a Next a moment after the load left the
        # live input's helper running under the clip, with the device open
        helper, t = self.clip_parked_before_it_stops_the_helper()
        self.api.control({"action": "next"}, None, "t")
        helper.lock.release()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(helper.running, 0, "a live input's helper was left running under a clip")

    def test_a_clip_that_loads_stops_the_live_input_it_replaces(self):
        helper = self.api.capture = Helper(self.rundir)
        helper.start("video0", "720p30")
        self.api.play({"file": "a.mp4"}, None, "t")
        self.settle()
        self.assertEqual(helper.running, 0)

    # -- the fourth review: nothing is done under the fader's lock --
    def test_a_dips_clip_that_is_loading_holds_nobody_up_at_the_fader(self):
        # H1: the callback ran under Fader._lock, so every cancel() and ramp(), which is the first line of
        # Blackout, opacity, Stop and every play, waited for the clip's load
        loading, let_go = self.threading.Event(), self.threading.Event()

        def slow_load():
            loading.set()
            let_go.wait(10)
        self.dip()
        self.player.before_play = slow_load
        self.api.control({"action": "opacity", "value": 60}, None, "t")        # cuts the dip short: its clip loads now
        self.assertTrue(loading.wait(5), "the dip's clip never came to load")
        try:
            for what, act in (("fader.cancel()", self.api.fader.cancel), ("fader.ramp()", lambda: self.api.fader.ramp(0, 10, 0.1)),
                              ("Blackout", lambda: self.api.blackout({"on": True}, None, "t")),
                              ("an opacity change", lambda: self.api.control({"action": "opacity", "value": 50}, None, "t")),
                              ("Fade out", lambda: self.api.fadeout({"seconds": 0.1}, None, "t"))):
                done = self.threading.Event()
                thread = self.threading.Thread(target=lambda: (act(), done.set()), daemon=True)
                thread.start()
                self.assertTrue(done.wait(2), "%s waited for the clip's load: something is done under the fader's lock" % what)
        finally:
            let_go.set()
        self.api.fader.cancel()

    def test_a_callback_is_never_called_under_the_faders_lock(self):
        from pvj.api import Fader
        seen = []
        fader = Fader(lambda level: None)
        done = self.threading.Event()
        fader.ramp(0, 1, 0.05, then=lambda: (seen.append(fader._lock.locked()), done.set()))
        self.assertTrue(done.wait(5))
        done.clear()
        applied = self.threading.Event()
        fader = Fader(lambda level: applied.set())
        fader.ramp(0, 1, 5.0, cancelled=lambda: (seen.append(fader._lock.locked()), done.set()))
        self.assertTrue(applied.wait(5))
        fader.cancel()
        self.assertTrue(done.wait(5))
        self.assertEqual(seen, [False, False], "a callback of the fader was called while its lock was held")

    def test_a_dip_cut_short_while_a_live_input_is_being_started_does_not_hang(self):
        # H1, the deadlock: the start of a live input held the capture lock and wanted the fader's; the dip's
        # callback held the fader's and wanted the capture lock
        helper = self.api.capture = Helper(self.rundir)
        in_prepare, go = self.threading.Event(), self.threading.Event()
        helper.on_prepare = lambda: (in_prepare.set(), go.wait(10))
        self.dip()
        live, _ = self.background(lambda: self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t"))
        self.assertTrue(in_prepare.wait(5))                                     # it has the capture lock
        self.played.clear()
        self.api.control({"action": "opacity", "value": 60}, None, "t")        # the dip is cut short: its clip loads,
        self.assertTrue(self.played.wait(5))                                    # and comes to wait for the capture lock
        go.set()                                                                # the live input goes on to the fader
        live.join(5)
        self.assertFalse(live.is_alive(), "the start of a live input and a dip's clip wait for each other for ever")
        self.settle()
        self.assertEqual(helper.running, 1)

    def test_a_step_that_was_on_its_way_does_not_land_after_a_level_that_was_set(self):
        from pvj.api import Fader
        written, in_step, let_go = [], self.threading.Event(), self.threading.Event()

        def apply(level):
            in_step.set()
            let_go.wait(10)
            written.append(("step", level))
        fader = Fader(apply)
        fader.ramp(0, 100, 1.0)
        self.assertTrue(in_step.wait(5))                                # a step is in the player

        def set_level():
            with fader.stepping:                                        # as Blackout and the Opacity slider do
                fader.cancel()
                written.append(("set", 0))
        setter = self.threading.Thread(target=set_level, daemon=True)
        setter.start()
        setter.join(0.3)
        self.assertTrue(setter.is_alive(), "a level was set while a step was still on its way: the step lands after it")
        let_go.set()
        setter.join(5)
        self.assertEqual(written[-1], ("set", 0))
        self.assertEqual(len(written), 2, "a step went on after the level was set")

    def test_next_and_previous_during_a_dip_do_not_leave_the_picture_dark(self):
        # M1 of the fourth review: they count as newer wishes and do not touch the fader, so the way down went on
        # to black, the clip was dropped and nobody brought the picture back
        for action in ("next", "prev"):
            del self.player.calls[:], self.player.plays[:]
            self.dip(0.2)
            del self.looks[:]
            self.api.control({"action": action}, None, "t")
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.looks:
                time.sleep(0.005)
            self.assertTrue(self.looks, "the dip's clip never came to look")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.player.calls[-1] != ("opacity", 255):
                time.sleep(0.005)
            self.assertEqual(self.loaded(), [], action)
            self.assertEqual(self.player.calls[-1], ("opacity", 255), "%s during a dip left the picture dark" % action)

    def test_next_after_the_way_down_does_not_leave_the_picture_dark(self):
        at_black = self.threading.Event()
        real = self.player.opacity
        self.player.opacity = lambda value: (real(value), at_black.set() if value == 0 else None)
        with self.player._lock:                                         # the clip's look and load wait here
            self.dip(0.2)
            self.assertTrue(at_black.wait(5))
            self.api.control({"action": "next"}, None, "t")
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.player.calls[-1] != ("opacity", 255):
            time.sleep(0.005)
        self.assertEqual(self.loaded(), [])
        self.assertEqual(self.player.calls[-1], ("opacity", 255))

    def test_a_clip_that_fails_to_load_after_its_dip_is_logged_and_the_picture_comes_back(self):
        lines = []
        self.api.log = lines.append
        self.player.fail = {"play"}
        self.dip(0.2)
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not lines:
            time.sleep(0.005)
        self.assertTrue(lines and "did not start after its dip" in lines[0], lines)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.player.calls[-1] != ("opacity", 255):
            time.sleep(0.005)
        self.assertEqual(self.player.calls[-1], ("opacity", 255), "the old clip was left dark")

    def test_a_dip_after_a_fade_out_does_not_flash_the_old_picture(self):
        done = self.threading.Event()
        self.api.fader.ramp(100, 0, 0.05, label="out", then=done.set)   # the operator's Fade out, to its end
        self.assertTrue(done.wait(5))
        del self.player.calls[:]
        self.dip(0.2)
        self.assertTrue(self.played.wait(5))
        before = [c[1] for c in self.player.calls[:self.names().index("play")] if c[0] == "opacity"]
        self.assertEqual(before, [], "the way down began again from the mix's level over a dark screen: %s" % before)
        self.api.fader.cancel()

    def test_the_generation_moves_before_the_fader_is_taken(self):
        # low: the other way round, a clip whose dip was cut short came to load before it was the older wish
        order = []
        end, cancel = self.tr.end, self.api.fader.cancel
        self.tr.end = lambda *a, **k: order.append("generation") or end(*a, **k)
        self.api.fader.cancel = lambda: order.append("fader") or cancel()
        self.api._settle(newer=True)
        self.assertEqual(order, ["generation", "fader"])
        del order[:]
        self.assertTrue(self.api.shaders.show(self.shader())["ok"])
        self.assertEqual(order[:2], ["generation", "fader"])

    def test_a_stop_clears_the_screen_before_the_level_goes_back(self):
        self.dip(0.2)
        self.api.control({"action": "stop"}, None, "t")
        names = self.names()
        last_level = max(i for i, c in enumerate(self.player.calls) if c == ("opacity", 255))
        self.assertLess(names.index("clear"), last_level, "the old picture was shown at full before it was cleared")


class Stress(ServerBase):
    """A test of another kind (after four reviews that each found an order of events the hand-written tests did not
    have): random actions from several threads against the fake player, which has the real one's lock and epoch,
    and after each round what must hold whatever the order was. The seed and the actions are in the failure's
    message, so a failure can be run again: Stress().replay(seed, round)."""
    SEEDS = (20261009, 1, 7, 4242, 99)
    ROUNDS = 60
    FAST = 10.0                             # the fader's and the transitions' clocks run this much faster

    def setUp(self):
        super().setUp()
        import threading
        import time
        self.threading, self.time = threading, time
        self.player = self.api.player = Screen(self.rundir)
        self.player.running = True
        self.helper = self.api.capture = Helper(self.rundir)
        self.tr = self.api.transitions
        self.tr.log = lambda line: None
        self.api.log = lambda line: None
        fast = self.FAST
        self.tr._clock, self.tr._sleep = (lambda: time.monotonic() * fast), (lambda s: time.sleep(s / fast))
        self.api.fader = type(self.api.fader)(self.api._apply_opacity, clock=lambda: time.monotonic() * fast, sleep=lambda s: time.sleep(s / fast))
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        self.sid = self.api.shaders.library()[0]["id"]
        for name in ("c1.mp4", "c2.mp4", "c3.mp4", "c4.mp4"):
            open(os.path.join(self.media, name), "w").close()
        self.epoch = 0

    def actions(self):
        from pvj.midi import MIDI_DEVICE
        api = self.api

        def play(name, kind, device=None):
            def act():
                self.settings.data["mix"] = T.stored(kind, 0.3)
                api.play({"file": name}, device, "stress")
            return act
        return {
            "play c1 cut": play("c1.mp4", "cut"), "play c2 dip": play("c2.mp4", "dip"),
            "play c3 crossfade": play("c3.mp4", "crossfade"), "play c4 wipe": play("c4.mp4", "wipe-from-left"),
            "controller plays c1 crossfade": play("c1.mp4", "crossfade", MIDI_DEVICE),
            "controller plays c2 slide": play("c2.mp4", "slide-up", MIDI_DEVICE),
            "stop": lambda: api.control({"action": "stop"}, None, "stress"),
            "blackout on": lambda: api.blackout({"on": True}, None, "stress"),
            "blackout off": lambda: api.blackout({"on": False}, None, "stress"),
            "fade out": lambda: api.fadeout({"seconds": 0.3}, None, "stress"),
            "fade in": lambda: api.fadein({"seconds": 0.3}, None, "stress"),
            "opacity 40": lambda: api.control({"action": "opacity", "value": 40}, None, "stress"),
            "opacity 100": lambda: api.control({"action": "opacity", "value": 100}, None, "stress"),
            "next": lambda: api.control({"action": "next"}, None, "stress"),
            "generator by hand": lambda: api.shaders.show(self.sid),
            "rotation tick": lambda: api.shaders.show(self.sid, epoch=self.epoch, cut=False),
            "live input": lambda: api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "stress"),
        }

    def quiet(self, baseline, what):
        """Every thread a round started has ended, within a time no round needs: or something waits for ever."""
        deadline = self.time.monotonic() + 10
        while self.time.monotonic() < deadline:
            left = [t for t in self.threading.enumerate() if t not in baseline and t.is_alive()
                    and not t.name.startswith(("shader", "effect"))]        # the engines' own workers live on by design
            if not left and self.tr.running is None:
                return
            self.time.sleep(0.002)
        self.fail("%s: threads that never ended: %s" % (what, [t.name for t in left]))

    def one_round(self, rng, what):
        api, player, time = self.api, self.player, self.time
        acts = self.actions()
        names = sorted(acts)
        baseline = set(self.threading.enumerate())
        # a clean start: something plays, lit, nothing on its way
        api.control({"action": "stop"}, None, "stress")
        api.blackout({"on": False}, None, "stress")
        api.control({"action": "opacity", "value": 100}, None, "stress")
        self.settings.data["mix"] = T.stored("cut", 0.3)
        api.play({"file": "c1.mp4"}, None, "stress")
        self.quiet(baseline, what + " (before the round)")
        self.epoch = player.source_epoch
        chosen = [rng.choice(names) for _ in range(rng.randint(3, 6))]
        delays = [rng.random() * 0.004 for _ in chosen]
        what = "%s, actions %s" % (what, chosen)
        errors = []

        def run(name, delay):
            time.sleep(delay)
            try:
                acts[name]()
            except Exception as e:
                errors.append("%s raised %r" % (name, e))
        threads = [self.threading.Thread(target=run, args=(n, d), daemon=True) for n, d in zip(chosen[:-1], delays)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
            self.assertFalse(t.is_alive(), "%s: a call never returned (a deadlock)" % what)
        last = chosen[-1]
        final = self.threading.Thread(target=run, args=(last, delays[-1]), daemon=True)
        final.start()
        final.join(10)
        self.assertFalse(final.is_alive(), "%s: the last call never returned (a deadlock)" % what)
        self.quiet(baseline, what)
        self.assertEqual(errors, [], what)
        # -- what must hold, whatever the order was --
        self.assertEqual(player.unlocked, [], what)
        self.assertFalse(player.overlay_up, "%s: a still was left on the screen" % what)
        self.assertFalse(player.props["pause"], "%s: the clip was left frozen" % what)
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("transition-") or n.startswith("overlay-")], [], what)
        path = os.path.basename(player.path or "")
        if last == "stop":
            self.assertEqual((player.path, player.source_shader), (None, None), "%s: something was loaded after the last Stop" % what)
        elif "play" in last:
            clip = [w for w in last.split() if len(w) == 2 and w[0] == "c" and w[1].isdigit()][0] + ".mp4"
            self.assertEqual(path, clip, "%s: the last wish was %s and what plays is %s" % (what, clip, path or "nothing"))
        elif last == "generator by hand":
            self.assertIsNotNone(player.source_shader, "%s: the generator chosen last is not on the screen" % what)
        elif last == "live input":
            self.assertEqual(player.path, self.helper.fifo, "%s: the live input started last is not what plays" % what)
        dark = api.mix["blackout"] or api.fader.label == "out"
        want = 0 if dark else int(round(api.mix["opacity"] * 2.55))
        level = [c[1] for c in player.calls if c[0] == "opacity"][-1]
        self.assertEqual(level, want, "%s: the picture's level is %d and the mix says %d (blackout %s, fader %s, opacity %s)"
                         % (what, level, want, api.mix["blackout"], api.fader.label, api.mix["opacity"]))
        self.assertLessEqual(self.helper.most, 1, "%s: two helpers of a live input at once" % what)
        if player.path == self.helper.fifo:
            self.assertEqual(self.helper.running, 1, "%s: a live input plays and its helper does not run" % what)
        else:
            self.assertEqual(self.helper.running, 0, "%s: a live input's helper runs under something else" % what)
        del player.calls[:]
        player.calls.append(("opacity", level))

    def replay(self, seed, number):
        import random
        rng = random.Random(seed)                   # the rounds before it are run too: they draw from the same numbers
        for i in range(number + 1):
            self.one_round(rng, "seed %d round %d" % (seed, i))

    def test_random_orders_of_everything_an_operator_can_do(self):
        import random
        began = self.time.monotonic()
        for seed in self.SEEDS:
            rng = random.Random(seed)
            for i in range(self.ROUNDS):
                self.one_round(rng, "seed %d round %d" % (seed, i))
        print("stress: %d rounds in %.1f s" % (len(self.SEEDS) * self.ROUNDS, self.time.monotonic() - began))


class OddSizes(unittest.TestCase):
    """A wipe and a slide on a screen of 41 x 23: every column and every row, to the last."""

    def test_every_step_is_inside_the_screen_and_the_file_and_the_last_one_is_one_column_or_row(self):
        w, h = 41, 23
        size = w * h * 4 + w * 4                                        # the file: the picture and one row more
        for name in T.WIPES + T.SLIDES:
            blend = T.BLENDS[name]()
            across = w if blend.cut in ("left", "right") else h
            seen = []
            for n in range(across + 1):
                part = blend.step(w, h, None, n / float(across))
                if n == across:
                    self.assertIsNone(part, name)
                    break
                x, y, offset, pw, ph = part
                self.assertTrue(pw >= 1 and ph >= 1 and 0 <= x and x + pw <= w and 0 <= y and y + ph <= h, "%s at %d: %s" % (name, n, part))
                self.assertLessEqual(offset + (ph - 1) * w * 4 + pw * 4, w * h * 4, "%s at %d reads past the picture" % (name, n))
                self.assertLessEqual(offset + ph * w * 4, size, "%s at %d: the player maps past the file" % (name, n))
                seen.append(pw if blend.cut in ("left", "right") else ph)
            self.assertEqual(seen, list(range(across, 0, -1)), "%s does not go column by column (or row by row) to the last" % name)


class OddRun(Base):
    def test_a_wipe_and_a_slide_run_whole_on_a_screen_of_41_by_23(self):
        for name in ("wipe-from-left", "wipe-from-bottom", "slide-right", "slide-up"):
            self.settings.data["mix"] = T.stored(name, 2.0)
            self.player.screen = (41, 23)
            del self.player.parts[:]
            self.now[0] = 50.0
            self.play()
            self.assertEqual(self.tr.last["ended"], "done", name)
            smallest = min(p[3] * p[4] for p in self.player.parts)
            self.assertLessEqual(smallest, 2 * max(41, 23), "%s never came near its last column or row: %s" % (name, self.player.parts[-1]))
            self.assertEqual(self.left(), [], name)


if __name__ == "__main__":
    unittest.main()
