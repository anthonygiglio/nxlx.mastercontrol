# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The crossfade (pvj/transitions.py, D71) against a player that records what it is told. What a real mpv draws is in
tests/test_transitions_gpu.py, which CI's effects-gpu jobs run."""
import os
import struct
import time
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
        from pvj import locks
        self._lock = locks.make("player", reentrant=True)       # at the player's place in the order of the locks
        self.source_epoch, self.source_shader, self.path, self.carrier = 0, None, None, None
        self.socket_path = os.path.join(rundir, "player.sock")
        self.overlay_up = False
        self.pipe_playing = False
        self.unlocked = []              # changes of what plays that were made without the lock (there must be none)
        self.before_play = None         # called by play(), inside it, before the clip is noted
        self.before_source = None       # the same for a generator
        self.lag = None                 # lag(kind) -> seconds this call takes ("opacity", "play", "still", "path"): the stress test
        self.log = []                   # every change of what plays, in the order the lock gave them (the stress test adds the wishes)
        self.steps_playlist = False     # Next and Previous change what plays (the stress test)
        self._path_before, self._path_at = None, 0.0

    def _wait(self, kind):
        if self.lag:
            import time
            time.sleep(self.lag(kind))

    def _now_plays(self, path):
        """Under the lock: what plays changes. The player's own `path` says so a moment later, as mpv's does."""
        import time
        self._path_before, self._path_at = self.path, time.monotonic() + (self.lag("path") if self.lag else 0.0)
        self.path = path

    def opacity(self, value):
        self._wait("opacity")
        self.calls.append(("opacity", value))

    def playlist_step(self, forward):
        if not self.steps_playlist:
            self.calls.append(("playlist_step", forward))
            return True
        with self._lock:
            self._held("playlist_step")
            if self.path is None or self.source_shader is not None:
                self.log.append(("load", "no step"))
                return False
            self._now_plays(self.path + "+1")
            self.source_epoch += 1
            self.log.append(("load", "step", os.path.basename(self.path)))
            self.calls.append(("playlist_step", forward))
            return True

    def _held(self, what):
        if not self._lock._is_owned():
            self.unlocked.append(what)

    def claim_screen(self):
        self._held("claim_screen")
        with self._lock:
            self.source_epoch += 1

    def play_pipe(self, path, width, height, fps):
        self._held("play_pipe")
        with self._lock:
            self._wait("play")
            self.source_epoch += 1
            self.source_shader, self.carrier = None, None
            self._now_plays(path)
            self.pipe_playing = True
            self.log.append(("load", "pipe"))
            self.calls.append(("play_pipe", path))
            self.props["pause"] = False

    def play_source(self, shader, carrier, epoch=None, spawn=False):
        self._held("play_source")
        with self._lock:
            if epoch is not None and epoch != self.source_epoch:
                return None
            if self.before_source:
                self.before_source()
            self._wait("play")
            self.source_shader, self.carrier = shader, carrier
            self._now_plays(carrier)
            self.log.append(("load", "generator"))
            self.pipe_playing = False
            self.source_epoch += 1
            self.calls.append(("play_source", os.path.basename(shader)))
            return self.source_epoch

    def clear(self):
        self._held("clear")
        with self._lock:
            self.source_epoch += 1
            self.source_shader, self.carrier = None, None
            self._now_plays(None)
            self.pipe_playing = False
            self.log.append(("load", "clear"))
            self.calls.append(("clear",))

    def request(self, *command):
        if command[0] == "get_property":
            if "ipc" in self.fail:
                raise PlayerError("player is not running")
            if command[1] == "path":
                import time
                return self._path_before if time.monotonic() < self._path_at else self.path
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
        self._held("play")
        with self._lock:
            if self.before_play:
                self.before_play()
            if "play" in self.fail:
                raise PlayerError("player is not running")
            self._wait("play")
            super().play(*a, **kw)
            self.source_epoch += 1
            self.source_shader, self.carrier = None, None
            self._now_plays(a[0][0])
            self.log.append(("load", "clip" if len(a[0]) == 1 else "list", os.path.basename(a[0][0])))
            self.pipe_playing = False
            self.props["pause"] = False

    def still(self, path):
        self.calls.append(("still", os.path.basename(path)))
        self._wait("still")
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
        from pvj import locks
        self.lock = locks.make("capture", reentrant=True)       # at the live input's place in the order of the locks
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

    def test_a_still_written_in_many_small_pieces_reads_the_same(self):
        # the Pi 4's mpv 0.40 writes a 2560 x 1440 still as some 2700 pieces of 4 KB (measured 2026-10-09)
        w, h = 64, 48
        raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))
        packed = zlib.compress(raw, 0)

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
        head = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        self.put(head + chunk(b"IDAT", packed) + chunk(b"IEND", b""))
        whole = T.read_still(self.path, (w, h))
        self.put(head + b"".join(chunk(b"IDAT", packed[i:i + 97]) for i in range(0, len(packed), 97)) + chunk(b"IEND", b""))
        self.assertEqual(T.read_still(self.path, (w, h)), whole)
        # and one row too many, spread over the pieces, is still refused
        more = zlib.compress(raw + b"\x00" + bytes(w * 3), 0)
        self.put(head + b"".join(chunk(b"IDAT", more[i:i + 97]) for i in range(0, len(more), 97)) + chunk(b"IEND", b""))
        with self.assertRaises(T.StillError):
            T.read_still(self.path, (w, h))

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

    def test_the_masks_stay_on_during_and_after_a_crossfade(self):
        # D100: the masks are overlay 10, the still is 63; a crossfade never takes the masks off nor draws over their slot
        from pvj import overlay as overlay_mod
        self.assertNotEqual(T.OVERLAY_ID, overlay_mod.OVERLAY_ID)
        from tests.test_masks import png as rgba_png
        rgba_png(os.path.join(self.media, "frame.png"), 2, 2, lambda x, y: (0, 0, 0, 255))

        def combine(paths, w, h, out, mpv_bin="mpv", timeout=None, cancelled=None):
            with open(out, "wb") as f:
                f.write(b"\0" * 16)
            return out
        self.api.masks.combine = combine
        self.api.set_overlay({"add": "frame.png", "on": True}, None, "t")
        end = time.monotonic() + 10
        while self.api.masks.busy() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(self.api.masks.status["state"], "on")
        self.assertEqual(self.play(), {"playing": "a.mp4"})
        self.assertEqual(self.play("b.mov"), {"playing": "b.mov"})
        ids = [c[1] for c in self.player.calls if c[0] in ("overlay", "overlay_part", "overlay_remove")]
        self.assertTrue(ids and set(ids) == {T.OVERLAY_ID}, ids)
        self.assertEqual([c[1] for c in self.player.calls if c[0] == "overlay_file"], [overlay_mod.OVERLAY_ID], "drawn once, never again")
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertEqual(self.api.masks.status["state"], "on")
        self.assertFalse(self.api.masks.watch(), "nothing to build again after the crossfade")

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
            import time
            time.sleep(0.05)                    # the dip of the round before (its own thread, no sleeps) has run out
            self.api.fader.cancel()
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

    # -- when the box gives up on stills (the ninth read: it was one strike, measured with the questions before the
    #    still, and nothing in the panel said so) --
    def slow_stills(self, *seconds):
        """The player's next stills take these seconds each on the test's clock (then no time)."""
        real, left = self.player.still, list(seconds)

        def still(path):
            self.now[0] += left.pop(0) if left else 0.0
            real(path)
        self.player.still = still

    def test_one_slow_still_is_used_and_the_box_does_not_give_up(self):
        self.slow_stills(T.SLOW + 0.2)
        self.play()
        self.assertEqual((self.tr.last["ended"], self.tr.given_up), ("done", ""))
        self.assertEqual(self.tr.last["still_ms"], 1200)
        self.assertNotIn("fallback", self.api.status({}, None, "t")["mix"])
        self.assertTrue(any("the still took 1.20 seconds" in line for line in self.lines), self.lines)
        del self.player.calls[:]
        self.play("b.mov")                                                      # the next one blends as ever
        self.assertEqual(self.names()[:4], ["pause", "still", "overlay", "play"])
        self.assertEqual(self.tr.given_up, "")

    def test_two_slow_stills_in_a_row_and_the_box_dips(self):
        self.slow_stills(T.SLOW + 0.2, T.SLOW + 0.4)
        self.play()
        self.assertEqual(self.tr.given_up, "")
        self.play("b.mov")
        self.assertEqual(self.tr.last["ended"], "done")                          # this one was already paid for
        self.assertIn("2 stills in a row took over a second (1.2, 1.4)", self.tr.given_up)
        said = self.api.status({}, None, "t")["mix"]["fallback"]
        self.assertIn("2 stills in a row", said)
        self.assertIn("tries again in 5 minutes, or at once when the transition is chosen again", said)
        del self.player.calls[:]
        self.play()
        self.assertNotIn("pause", self.names())
        self.assertNotIn("still", self.names())
        self.assertTrue(any("given up" in line for line in self.lines))
        # choosing the transition again lets the box try again
        self.assertNotIn("fallback", self.api.set_mix({"transition": "crossfade", "duration": 1}, None, "t"))
        self.assertEqual(self.tr.given_up, "")

    def test_a_quick_still_between_two_slow_ones_starts_the_count_again(self):
        self.slow_stills(T.SLOW + 0.2, 0.1, T.SLOW + 0.3, 0.1, T.SLOW + 0.2)
        for name in ("a.mp4", "b.mov", "a.mp4", "b.mov", "a.mp4"):
            self.play(name)
            self.assertEqual(self.tr.given_up, "", name)

    def test_one_slow_still_is_used_at_every_duration_the_panel_offers(self):
        # the tenth read: with "longer than the transition" in the rule, one still of 1.2 s was the last one at the
        # box's default of one second and at half a second, and the helper above had hidden it by asking for two
        for seconds in (0.5, 1.0, 2.0, 5.0):
            self.tr.chosen_again()
            self.settings.data["mix"] = T.stored("crossfade", seconds)
            self.slow_stills(T.SLOW + 0.2)
            if seconds in (1.0, 5.0):
                self.play("b.mov")
            else:
                self.play()
            self.assertEqual(self.tr.given_up, "", seconds)

    def test_a_still_that_took_hopelessly_long_gives_up_at_once(self):
        self.slow_stills(T.HOPELESS + 0.5)
        self.play()
        self.assertIn("the still took 3.5 seconds", self.tr.given_up)

    def test_the_stills_time_is_the_screenshot_and_its_reading_alone(self):
        # the questions before it, the freeze and the file were counted too: on the Pi 4 a play answered after
        # 0.93 to 1.09 s at 2560 x 1440 against the limit of one second
        real_pause, real_size, real_still = self.player.pause, self.player.osd_size, self.player.still

        def pause(value=None):
            self.now[0] += 0.6
            return real_pause(value)

        def osd_size():
            self.now[0] += 0.5
            return real_size()

        def still(path):
            self.now[0] += 0.7
            real_still(path)
        self.player.pause, self.player.osd_size, self.player.still = pause, osd_size, still
        for name in ("a.mp4", "b.mov", "a.mp4"):
            self.play(name)
            self.assertEqual(self.tr.given_up, "", "the questions before the still were counted as the still's time")
        self.assertEqual(self.tr.last["still_ms"], 700)
        self.assertGreaterEqual(self.tr.last["hold_ms"], 1700)

    def test_a_box_that_gave_up_tries_again_by_itself_after_a_while(self):
        self.slow_stills(T.SLOW + 0.2, T.SLOW + 0.2)
        self.play()
        self.play("b.mov")
        self.assertTrue(self.tr.given_up)
        self.now[0] += T.RETRY - 20
        self.assertIn("fallback", self.api.status({}, None, "t")["mix"])
        self.assertTrue(self.tr.look())
        self.now[0] += 30
        self.assertNotIn("fallback", self.api.status({}, None, "t")["mix"])
        self.assertTrue(any("trying again" in line for line in self.lines))
        del self.player.calls[:]
        self.play("b.mov")
        self.assertEqual(self.names()[:4], ["pause", "still", "overlay", "play"])
        self.assertEqual(self.tr.given_up, "")
        # and it is given no credit for before: one slow still after the pause is one, not the second
        self.slow_stills(T.SLOW + 0.2)
        self.play()
        self.assertEqual(self.tr.given_up, "")

    def test_every_reason_for_giving_up_is_tried_again_after_a_while(self):
        self.tr._give_up("12 steps in 3.0 seconds")
        self.assertIn("12 steps", self.tr.fallback())
        self.now[0] += T.RETRY + 1
        self.assertEqual(self.tr.fallback(), "")

    def test_after_a_fade_out_a_crossfade_a_wipe_and_a_slide_come_up_from_black_as_the_dip_does(self):
        # low, the ninth read: with one of them chosen the new clip snapped to full brightness at once
        ramps = {}
        for style in ("dip", "crossfade", "wipe-from-left", "slide-up"):
            self.settings.data["mix"] = {"transition": "cut", "duration": 2.0}
            self.api.mix.update(blackout=False, opacity=100)
            self.api.fader.cancel()
            self.play("a.mp4")                                          # something plays, put there with a cut
            self.settings.data["mix"] = T.stored(style, 2.0)
            self.api.fadeout({"seconds": 0.1}, None, "t")
            self.assertEqual(self.api.fader.label, "out")
            self.now[0] += 1.0                                          # the Fade out has run to its end, by the fader's clock
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.player.calls[-1:] != [("opacity", 0)]:
                time.sleep(0.005)
            time.sleep(0.05)                                            # and its thread has written its last step
            self.assertEqual(self.player.calls[-1:], [("opacity", 0)], style)
            del self.player.calls[:]
            self.play("b.mov")
            names = self.names()
            self.assertNotIn("still", names, style)
            after = [c for c in self.player.calls[names.index("play"):] if c[0] == "opacity"]
            self.assertEqual(after[0], ("opacity", 0), "%s: the clip did not start from black after a Fade out" % style)
            self.assertNotEqual(after[:2], [("opacity", 0), ("opacity", 255)], style)
            ramps[style] = after
            self.assertIsNone(self.api.fader.label, style)
        for style in ("crossfade", "wipe-from-left", "slide-up"):
            self.assertEqual(ramps[style][:1], ramps["dip"][:1], style)

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

    def test_with_an_effect_on_the_give_up_names_the_effect_and_not_the_clip(self):
        """An effect stays on over the incoming clip (also out of a shader, D74), and its cost is in the frames the
        blend drops. The count is as before: a blend that stutters falls back. The words name what can be done."""
        def drop():
            self.player.props["frame-drop-count"] += 1
        self.player.on_overlay = drop
        fx = self.api.effects
        fx.on = {"id": "fx-edge-glow.fs", "epoch": 4}               # the effects' own memory of what is on
        self.player.effect_shader, self.player.effect_serial = "/run/effect-1-1.glsl", 4
        self.play()
        self.assertEqual(self.tr.last["dropped"], 20)
        self.assertTrue(self.tr.given_up.startswith("frames were dropping during the change with the effect fx-edge-glow on (20 in 1.0 seconds); "
                                                    "taking the effect off may help"), self.tr.given_up)
        self.assertNotIn("the clip dropped", self.tr.given_up)
        self.assertEqual(self.api.status({}, None, "t")["mix"]["fallback"], self.tr.given_up)      # what the line under the picker shows
        # the effect off: the words are the ones before
        fx.on, self.player.effect_shader = None, None
        self.tr.given_up = ""
        self.play()
        self.assertIn("the clip dropped 20 frames", self.tr.given_up)

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


class Died(BaseException):
    """The panel process ending in the middle of a hold: nothing in the panel catches it, as nothing catches a kill."""


class FrozenByAPanelThatDied(Base):
    """D112: a panel that dies between freezing the outgoing clip and loading the new one leaves a record, and the
    next panel to start thaws that clip and only that clip."""

    def record(self):
        return os.path.join(self.rundir, T.FROZEN_FILE)

    def die_at_the_still(self):
        """The panel's play, killed while the player takes the still: the clip is frozen, the new one not loaded."""
        real, real_pause = self.player.still, self.player.pause
        self.seen_record = None

        def pause(value=None):
            if value and self.seen_record is None:
                self.seen_record = os.path.exists(self.record())    # looked at as the freeze is asked for
            return real_pause(value)

        def still(path, kind="window"):
            raise Died()
        self.player.still, self.player.pause = still, pause
        with self.assertRaises(Died):
            self.play()
        self.player.still, self.player.pause = real, real_pause

    def next_panel(self):
        self.tr = self.api.transitions = T.Transitions(self.api, clock=lambda: self.now[0], sleep=self.sleep, thread=False, log=self.lines.append)
        del self.player.calls[:]
        self.tr.tidy()

    def test_a_panel_that_dies_with_the_outgoing_clip_frozen_leaves_a_record_and_the_next_panel_thaws_it(self):
        self.die_at_the_still()
        self.assertTrue(self.seen_record, "the record is written before the freeze")
        self.assertTrue(self.player.props["pause"], "the clip is frozen and nobody is left to thaw it")
        self.assertTrue(os.path.exists(self.record()))
        self.next_panel()
        self.assertFalse(self.player.props["pause"], "a frozen picture on the screen after a crash")
        self.assertIn(("pause", False), self.player.calls)
        self.assertEqual(self.left(), [])

    def test_a_hold_that_ends_well_leaves_no_record_and_a_freeze_after_it_is_the_operators(self):
        self.play()
        self.assertFalse(os.path.exists(self.record()))
        self.player.props["pause"] = True               # Freeze, after the new clip loaded
        self.next_panel()
        self.assertTrue(self.player.props["pause"], "the operator's freeze is not the fix's to undo")

    def test_a_hold_that_fails_or_is_abandoned_removes_its_record(self):
        from pvj.api import ApiError
        self.player.fail = {"play"}
        with self.assertRaises(ApiError):
            self.play()
        self.assertEqual(self.left(), [])
        self.assertFalse(self.player.props["pause"])

    def test_a_clip_the_operator_froze_before_the_play_gets_no_record_and_stays_frozen_for_the_next_panel(self):
        self.player.props["pause"] = True
        self.die_at_the_still()
        self.assertFalse(self.seen_record, "this hold froze nothing")
        self.next_panel()
        self.assertTrue(self.player.props["pause"])

    def test_an_operator_freeze_made_after_the_new_clip_loaded_is_left_alone_even_if_the_record_stayed(self):
        self.player.path = "a.mp4"
        self.die_at_the_still()
        self.player.path = "b.mp4"                      # a load unfreezes and changes the clip; the record was not yet removed
        self.player.props["pause"] = True               # the operator froze the new clip
        self.next_panel()
        self.assertTrue(self.player.props["pause"])
        self.assertEqual(self.left(), [])

    def test_a_restarted_player_has_nothing_to_thaw(self):
        self.die_at_the_still()
        self.player.props["pid"] = 9999                 # the player process was started anew
        self.player.props["pause"] = True               # an operator's freeze on the new process
        self.next_panel()
        self.assertTrue(self.player.props["pause"])
        self.assertNotIn(("pause", False), self.player.calls)
        self.assertEqual(self.left(), [])

    def test_a_record_for_a_clip_that_is_not_frozen_is_only_removed(self):
        self.die_at_the_still()                         # died after the record but, say, before the freeze took
        self.player.props["pause"] = False
        self.next_panel()
        self.assertNotIn(("pause", False), self.player.calls)
        self.assertEqual(self.left(), [])

    def test_a_record_of_an_older_run_with_a_different_clip_is_removed_and_the_clip_left_alone(self):
        self.player.path = "old.mp4"
        self.die_at_the_still()
        self.player.path = "new.mp4"
        self.player.props["pause"] = True
        self.next_panel()
        self.assertTrue(self.player.props["pause"])
        self.assertEqual(self.left(), [])

    def test_a_record_that_is_not_ours_to_read_is_removed_and_nothing_is_thawed(self):
        self.player.props["pause"] = True
        for body in (b"", b"not json", b"[1]", b'{"player": "4242"}', b"\xff\xfe"):
            with open(self.record(), "wb") as f:
                f.write(body)
            self.next_panel()
            self.assertTrue(self.player.props["pause"], body)
            self.assertEqual(self.left(), [], body)

    def test_a_link_in_place_of_the_record_is_not_followed(self):
        target = os.path.join(self.rundir, "elsewhere")
        with open(target, "w") as f:
            f.write('{"player": 4242, "path": null}')
        os.symlink(target, self.record())
        self.player.props["pause"] = True
        self.tr.tidy()
        self.assertTrue(self.player.props["pause"])
        self.assertTrue(os.path.exists(target), "only the panel's own files are removed")

    def test_a_player_that_cannot_be_asked_leaves_the_record_removed_and_does_not_stop_the_start(self):
        self.die_at_the_still()
        self.player.fail = {"ipc"}
        self.next_panel()
        self.assertEqual(self.left(), [])

    def test_a_record_left_by_a_crash_before_the_freeze_took_is_swept_by_the_pattern(self):
        for n in (T.FROZEN_FILE + ".tmp",):
            open(os.path.join(self.rundir, n), "w").close()
        self.next_panel()
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
        workers, work = [], self.tr._work
        self.tr._thread = False                     # the worker itself, on this thread: who steps is noted as it begins
        self.tr._work = lambda token, seconds, name: (workers.append(token), work(token, seconds, name))
        self.tr.run(first, 0.1)                     # the first play gets to its run late: it must do nothing
        self.tr.run(second, 0.1)
        self.assertEqual(workers, [second], "the first play's run started a worker on the second play's still")
        self.assertIsNone(self.tr.running)

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
        self.assertLess(took, 5.0, "Stop waited for the still")       # the still is held for 10 s: a call that waits takes that
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
            self.assertLess(self.timed(act), 5.0, "it waited for the still")
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
        self.assertLess(took, 5.0, "the controller's thread waited for the still")
        self.assertTrue(self.inside.wait(5))
        self.assertLess(self.timed(lambda: self.api.blackout({"on": True}, MIDI_DEVICE, "midi")), 5.0)
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
            self.tr._thread = False                 # the last play's transition runs whole, on the thread that plays it
            self.settings.data["mix"] = T.stored("crossfade", 0.1)
            self.release(order)
            self.assertEqual(self.names().count("still"), 1, "a still for each of the plays that were overtaken")
            self.assertEqual(self.names().count("pause"), 1)
            self.assertEqual(self.loaded(), ["b.mov"], "the clip on the screen is not the last one asked for")
            self.assertIsNone(self.tr.running)

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

        def hold(name, ticket=None, wanted=None, seconds=None):
            token = real_hold(name, ticket, wanted, seconds)
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
        # Asked of the Stop itself, not read off the player's calls: the fade's own first step is 99.83 percent,
        # which is 255 to the player, and on a slow runner it landed after the calls had been emptied and was
        # taken for the Stop's (one failure in CI on a branch stacked on this one).
        self.assertFalse(self.api._level_back(), "a Stop undid the operator's Fade out")
        self.assertEqual(self.api.fader.label, "out")
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
                self.assertTrue(done.wait(5), "%s waited for the clip's load: something is done under the fader's lock" % what)
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
        fader, over = Fader(apply), self.threading.Event()
        fader.ramp(0, 100, 1.0, cancelled=over.set)
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
        self.assertTrue(over.wait(5), "the ramp never saw that the fader was taken")
        # steps may have gone on until the setter got its turn (the lock is not a queue); none comes after it
        self.assertEqual(written[-1], ("set", 0), "a step landed after the level was set: %s" % written)
        self.assertEqual([w for w in written if w[0] == "set"], [("set", 0)])

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

    def owner_watch(self):
        """Stand-ins that note, for every taking of the fader, whether the caller held the level's lock, and for
        every newer wish, whether the caller held the player's lock."""
        fader, tr, seen = self.api.fader, self.tr, []
        real, holder = fader.stepping, [None]

        class Watched:
            def __enter__(self_):
                real.acquire()
                holder[0] = self.threading.get_ident()

            def __exit__(self_, *exc):
                holder[0] = None
                real.release()
        fader.stepping = Watched()
        cancel, end = fader.cancel, tr.end
        fader.cancel = lambda: seen.append(("fader", holder[0] == self.threading.get_ident())) or cancel()

        def ended(why="ended", newer=False):
            if newer:
                seen.append(("newest", self.player._lock._is_owned()))
            return end(why, newer)
        tr.end = ended
        return seen

    def test_a_newer_wish_and_its_own_change_of_what_plays_are_one_step(self):
        # the fifth review's order oracle found by reading what the stress test now finds by running: a Stop that
        # moved the generation and cleared the screen in two steps could clear a clip that was asked for after it
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.registry.set_enabled("inputs-srt", True)
        self.api.capture = Helper(self.rundir)
        seen = self.owner_watch()
        loads = []
        self.player.before_play = lambda: loads.append(len([x for x in seen if x[0] == "newest"]))
        # ONE step means one taking of the player's lock for the wish and for its change of what plays, not two
        # takings one after the other (the ninth read: with the wish under one and the clear under a second, every
        # test by hand passed, and a clip asked for between the two was cleared by a Stop that came before it).
        # The lock's takings from free are counted, and the wish and every change are noted with the count.
        lock, takings, wished, changed = self.player._lock, [0], [], []
        take = lock.acquire

        def counted(*a, **k):
            got = take(*a, **k)
            if got and lock._count == 1:
                takings[0] += 1
            return got
        lock.acquire = counted
        end = self.tr.end
        self.tr.end = lambda why="ended", newer=False: (wished.append(takings[0]) if newer else None, end(why, newer))[1]

        class Noted(list):
            def append(self_, entry):
                changed.append((takings[0], lock._is_owned(), entry))
                list.append(self_, entry)
        self.player.log = Noted()
        for what, act in (("Stop", lambda: self.api.control({"action": "stop"}, None, "t")),
                          ("Next", lambda: self.api.control({"action": "next"}, None, "t")),
                          ("a preset", lambda: self.api.play({"preset": "startless"}, None, "t")),
                          ("a stream", lambda: self.api.play({"stream": "bbbb0001"}, None, "t")),
                          ("the test pattern", lambda: self.api.test_pattern({"on": True}, None, "t")),
                          ("the test pattern off", lambda: self.api.test_pattern({"on": False}, None, "t")),
                          ("a tone", lambda: self.api.test_tone({"channel": "left"}, None, "t")),
                          ("a live input", lambda: self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")),
                          ("a generator", lambda: self.api.shaders.show(self.shader()))):
            del seen[:], self.player.unlocked[:], wished[:], changed[:]
            act()
            wishes = [x for x in seen if x[0] == "newest"]
            self.assertEqual(wishes, [("newest", True)], "%s became the newest wish outside the player's lock, apart from its own change" % what)
            self.assertEqual(self.player.unlocked, [], "%s changed what plays outside the lock it became the newest wish under" % what)
            self.assertEqual(len(wished), 1, what)
            apart = [c for c in changed if c[0] != wished[0] or not c[1]]
            self.assertEqual(apart, [], "%s: the wish was made in taking %d of the player's lock and its change of what plays in another"
                             % (what, wished[0]))
            if what in ("Stop", "a stream", "the test pattern", "a tone", "a live input", "a generator"):
                self.assertTrue(changed, "%s changed nothing that the test saw: nothing is tested" % what)

    def test_the_blackout_switch_is_set_inside_the_levels_lock(self):
        # the fifth review's mutation a: set outside, a Fade in that had the lock could go up under a Blackout the
        # mix already called on. Parked: the test has the lock, Blackout waits for it, and the mix must not say yet.
        real, waiting = self.api.fader.stepping, self.threading.Event()

        class Watched:
            def __enter__(self_):
                waiting.set()
                real.acquire()

            def __exit__(self_, *exc):
                real.release()
        self.assertTrue(real.acquire(timeout=1))
        self.api.fader.stepping = Watched()
        t, _ = self.background(lambda: self.api.blackout({"on": True}, None, "t"))
        self.assertTrue(waiting.wait(5))
        said = self.api.mix["blackout"]
        real.release()
        t.join(5)
        self.assertFalse(said, "the mix said Blackout before the level's lock was Blackout's")
        self.assertTrue(self.api.mix["blackout"])
        self.assertEqual(self.player.calls[-1], ("opacity", 0))

    def test_the_opacitys_value_is_set_inside_the_levels_lock(self):
        # the sixth review's mutation: the value set outside, two sliders could leave the mix at one value and the
        # picture at the other. Parked as the Blackout switch's test: the mix must not say yet while the lock is waited for.
        real, waiting = self.api.fader.stepping, self.threading.Event()

        class Watched:
            def __enter__(self_):
                waiting.set()
                real.acquire()

            def __exit__(self_, *exc):
                real.release()
        for what, act, value in (("the Opacity slider", lambda: self.api.control({"action": "opacity", "value": 40}, None, "t"), 40),
                                 ("Reset", lambda: self.api.control({"action": "reset"}, None, "t"), 100)):
            before = self.api.mix["opacity"]
            waiting.clear()
            self.assertTrue(real.acquire(timeout=1))
            self.api.fader.stepping = Watched()
            t, _ = self.background(act)
            self.assertTrue(waiting.wait(5))
            said = self.api.mix["opacity"]
            real.release()
            t.join(5)
            self.api.fader.stepping = real
            self.assertEqual(said, before, "%s: the mix had the new opacity before the level's lock was its own" % what)
            self.assertEqual(self.api.mix["opacity"], value)

    def test_every_write_of_the_level_is_made_under_the_levels_lock(self):
        # the fifth review's mutation i: a play's own level written beside the lock passed every test by hand
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.registry.set_enabled("inputs-srt", True)
        self.api.capture = Helper(self.rundir)
        sid = self.shader()
        owner, bare = [None], []
        real = self.api.fader.stepping

        class Watched:
            def __enter__(self_):
                real.acquire()
                owner[0] = self.threading.get_ident()

            def __exit__(self_, *exc):
                owner[0] = None
                real.release()
        self.api.fader.stepping = Watched()
        write = self.player.opacity
        self.player.opacity = lambda value: (bare.append(value) if owner[0] != self.threading.get_ident() else None, write(value))

        def clip(kind):
            def go():
                self.settings.data["mix"] = T.stored(kind, 0.1)
                self.played.clear()
                self.api.play({"file": "a.mp4"}, None, "t")
                self.assertTrue(self.played.wait(5))
            return go
        for what, act in (("a clip by a cut", clip("cut")), ("a clip by a dip", clip("dip")), ("a clip by a crossfade", clip("crossfade")),
                          ("a preset", lambda: self.api.play({"preset": "startless"}, None, "t")),
                          ("a stream", lambda: self.api.play({"stream": "bbbb0001"}, None, "t")),
                          ("the test pattern", lambda: self.api.test_pattern({"on": True}, None, "t")),
                          ("a live input", lambda: self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")),
                          ("a generator", lambda: self.api.shaders.show(sid)),
                          ("Stop", lambda: self.api.control({"action": "stop"}, None, "t")),
                          ("Blackout", lambda: self.api.blackout({"on": True}, None, "t")),
                          ("Blackout off", lambda: self.api.blackout({"on": False}, None, "t")),
                          ("the Opacity slider", lambda: self.api.control({"action": "opacity", "value": 100}, None, "t")),
                          ("Fade out", lambda: self.api.fadeout({"seconds": 0.1}, None, "t")),
                          ("Fade in", lambda: self.api.fadein({"seconds": 0.1}, None, "t"))):
            del bare[:]
            act()
            import time
            time.sleep(0.25)                                            # its ramp, if it has one, has run
            self.settle()
            self.assertEqual(bare, [], "%s wrote the picture's level outside the lock a level is written under" % what)

    def test_the_fader_is_first_taken_before_the_levels_lock_is_waited_for(self):
        # low, fifth review: with a player that does not answer, every step of a ramp holds the lock for a whole
        # answer time; a Blackout that took the fader only inside the lock let the ramp go on until it got its turn
        seen = self.owner_watch()
        for what, act in (("Blackout", lambda: self.api.blackout({"on": True}, None, "t")),
                          ("the Opacity slider", lambda: self.api.control({"action": "opacity", "value": 40}, None, "t")),
                          ("Reset", lambda: self.api.control({"action": "reset"}, None, "t")),
                          ("Fade in", lambda: self.api.fadein({"seconds": 0.1}, None, "t")),
                          ("Fade out", lambda: self.api.fadeout({"seconds": 0.1}, None, "t"))):
            del seen[:]
            act()
            takes = [x for x in seen if x[0] == "fader"]
            self.assertTrue(takes and takes[0] == ("fader", False), "%s: %s" % (what, takes))
            self.api.fader.cancel()
            self.api.mix.update(blackout=False)

    def test_a_generator_does_not_take_the_fader_before_it_is_on_the_screen(self):
        # low, fifth review: taken early, a generator the GPU then refused had stopped a fade for nothing
        order = []
        cancel = self.api.fader.cancel
        self.api.fader.cancel = lambda: order.append("fader") or cancel()
        self.player.before_source = lambda: order.append("generator")
        self.assertTrue(self.api.shaders.show(self.shader())["ok"])
        self.assertEqual(order[0], "generator", order)

    # -- the fifth review: one rule for the level, for everything that loads --
    def fade_out_and_wait(self):
        """The operator's Fade out, to its end: called in the middle of a load."""
        before = len(self.player.calls)             # the dark must be this fade's own, not one that was there already
        self.api.fadeout({"seconds": 0.1}, None, "t")
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and ("opacity", 0) not in self.player.calls[before:]:
            time.sleep(0.005)

    def level_now(self):
        return [c[1] for c in self.player.calls if c[0] == "opacity"][-1]

    def test_a_fade_out_made_while_something_loads_is_not_undone_by_the_load(self):
        # medium, fifth review: every load path wrote the mix's level when its load was done, whatever had been
        # asked for meanwhile: the label said "faded out" and the picture was lit
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.registry.set_enabled("inputs-srt", True)
        helper = self.api.capture = Helper(self.rundir)
        sid = self.shader()

        def during_play(act):
            def go():
                self.player.before_play = lambda: (setattr(self.player, "before_play", None), self.fade_out_and_wait())
                act()
            return go

        def during_helper():
            real = helper.start
            helper.start = lambda device, mode: (setattr(helper, "start", real), self.fade_out_and_wait(), real(device, mode))
            self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")

        def during_generator():
            self.player.before_source = lambda: (setattr(self.player, "before_source", None), self.fade_out_and_wait())
            self.api.shaders.show(sid)

        def dip_after_its_way_down():
            self.settings.data["mix"] = {"transition": "dip", "duration": 0.2}
            self.played.clear()
            self.player.before_play = lambda: (setattr(self.player, "before_play", None), self.fade_out_and_wait(), self.played.set())
            self.api.play({"file": "b.mov"}, None, "t")
            self.assertTrue(self.played.wait(5))

        def cut():
            self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
            self.api.play({"file": "b.mov"}, None, "t")
        for what, act in (("a clip by a cut", during_play(cut)), ("a clip after its dip's way down", dip_after_its_way_down),
                          ("a preset", during_play(lambda: self.api.play({"preset": "startless"}, None, "t"))),
                          ("a stream", during_play(lambda: self.api.play({"stream": "bbbb0001"}, None, "t"))),
                          ("the test pattern", during_play(lambda: self.api.test_pattern({"on": True}, None, "t"))),
                          ("a live input", during_helper), ("a generator", during_generator)):
            self.api.fader.cancel()
            self.api.mix.update(blackout=False, opacity=100)
            self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
            self.api.play({"file": "a.mp4"}, None, "t")                 # something plays, lit
            self.assertEqual(self.level_now(), 255, what)
            act()
            import time
            time.sleep(0.05)
            self.settle()
            self.assertEqual((self.api.fader.label, self.level_now()), ("out", 0),
                             "%s: a Fade out made while it loaded was undone by its load" % what)

    def test_a_fade_out_between_a_dips_tap_and_its_way_down_is_not_replaced_by_the_way_down(self):
        # low, sixth review: the way down took the fader whatever had come since the tap's mark
        mark = self.api._level_mark
        once = []

        def tap():
            token = mark()
            if not once:
                once.append(1)
                self.fade_out_and_wait()            # the operator's Fade out, right after the tap
            return token
        self.api._level_mark = tap
        self.settings.data["mix"] = {"transition": "dip", "duration": 0.2}
        self.played.clear()
        self.api.play({"file": "b.mov"}, None, "t")
        self.assertTrue(self.played.wait(5), "the clip was not loaded")
        import time
        time.sleep(0.05)
        self.settle()
        self.assertEqual((self.api.fader.label, self.level_now()), ("out", 0), "the dip's way down took the place of a Fade out that was newer than its tap")

    def test_a_fade_that_ran_before_the_tap_is_ended_by_the_load(self):
        # the other half of the rule: the tap is newer than a fade that was already running
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.api.fadeout({"seconds": 30}, None, "t")
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertEqual((self.api.fader.label, self.level_now()), (None, 255))

    def test_a_stop_that_meets_a_fade_out_does_not_undo_it(self):
        # low, fifth review: Stop looked at the label, a Fade out took the fader, and Stop put the level back
        real = self.player.clear
        self.player.clear = lambda: (self.fade_out_and_wait(), real())
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual((self.api.fader.label, self.level_now()), ("out", 0))

    def test_a_stop_that_meets_a_fade_in_does_not_cut_it_short(self):
        # the seventh review: the mark in Stop's putting-back was called an equivalent mutation (row n of the stress
        # test's table). It is not: a Fade in that comes between Stop's clear and its putting-back was cancelled,
        # and the level snapped to full
        real = self.player.clear
        self.player.clear = lambda: (self.api.fadein({"seconds": 30}, None, "t"), real())
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual(self.api.fader.label, "in", "Stop took the fader from a Fade in that was asked for after it")
        self.assertLess(self.level_now(), 128, "the Fade in snapped to full")
        self.api.fader.cancel()

    def test_a_tap_that_is_overtaken_before_it_loads_leaves_a_running_fade_alone(self):
        # found by the first stress test, and caught by nothing afterwards (the fifth review's mutation h): the
        # fader taken at the tap, the tap then overtaken, and a Fade in left half way with nobody to finish it
        self.api.fadein({"seconds": 30}, None, "t")                     # a long Fade in runs
        self.player.still = self.slow_still
        t, out = self.background(lambda: self.api.play({"file": "a.mp4"}, None, "t"))
        self.assertTrue(self.inside.wait(5))
        self.assertEqual(self.api.fader.label, "in", "the tap took the fader before its clip had loaded")
        self.api.control({"action": "next"}, None, "t")
        self.gate.set()
        t.join(5)
        self.assertEqual(out, [{"playing": None, "superseded": "a.mp4"}])
        self.assertEqual(self.api.fader.label, "in", "the Fade in was stopped by a clip that never loaded")
        self.api.fader.cancel()

    def test_the_helper_goes_by_what_was_loaded_not_by_what_the_player_says_its_path_is(self):
        # low, fifth review: the player's path changes a moment after a load, and the wait for it gives up silently
        helper = self.api.capture = Helper(self.rundir)
        self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")
        self.assertEqual(helper.running, 1)
        self.player.lag = lambda kind: 30.0 if kind == "path" else 0.0   # the path goes on saying "the pipe" for a long time
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertEqual(self.player.ipc.request("get_property", "path"), helper.fifo)
        self.assertEqual(helper.running, 0, "the helper was left under a clip because the player's path was late")

    def test_a_dip_that_a_stop_overtakes_does_not_show_the_old_picture_before_the_screen_is_cleared(self):
        # low, fifth review: the dip's own putting-back of the level could come before the Stop's clear
        self.dip(30.0)                                                  # a long way down
        del self.player.calls[:]
        self.api.control({"action": "stop"}, None, "t")
        self.settle()
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and ("clear" not in self.names() or self.player.calls[-1] != ("opacity", 255)):
            time.sleep(0.005)
        names = self.names()
        before = [c for c in self.player.calls[:names.index("clear")] if c == ("opacity", 255)]
        self.assertEqual(before, [], "the old picture was put back to full before the screen was cleared")
        self.assertEqual(self.player.calls[-1], ("opacity", 255))

    def test_a_stop_clears_the_screen_before_the_level_goes_back(self):
        self.dip(0.2)
        self.api.control({"action": "stop"}, None, "t")
        names = self.names()
        last_level = max(i for i, c in enumerate(self.player.calls) if c == ("opacity", 255))
        self.assertLess(names.index("clear"), last_level, "the old picture was shown at full before it was cleared")


class RealPlayer(ServerBase):
    """The real pvj.player.Player over a stand-in for mpv on its socket (tests/fakempv.py): what player.py itself
    does with its lock and with what it remembers, which the fakes of the other tests rewrote and so never tested.
    The locks know their place here as everywhere (tests/lockrank.py): a taking out of order fails the test."""

    def setUp(self):
        super().setUp()
        import threading
        import time
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.threading, self.time, self.FakeMpv = threading, time, FakeMpv
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"))
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.helper = self.api.capture = Helper(self.rundir)
        self.api.log = lambda line: None
        self.api.transitions.log = lambda line: None
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        self.clip = os.path.join(self.media, "a.mp4")
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}

    def background(self, fn):
        t = self.threading.Thread(target=fn, daemon=True)
        t.start()
        self.addCleanup(t.join, 10)
        return t

    def live_input(self):
        self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")
        self.assertEqual((self.player.pipe_playing, self.helper.running), (True, 1))

    # -- what the player remembers of the live input's pipe (the sixth review's M3: nothing tested the flag) --
    def test_the_pipe_is_what_plays_from_its_load_until_anything_else_is_loaded(self):
        sid = self.api.shaders.library()[0]["id"]
        for what, act in (("a clip", lambda: self.player.play([self.clip], spawn=False)), ("the screen cleared", self.player.clear),
                          ("a generator", lambda: self.api.shaders.show(sid)),
                          ("the player's restart from the panel", lambda: self.api.stop_player({}, None, "t"))):
            self.live_input()
            act()
            self.assertFalse(self.player.pipe_playing, "after %s the player still says the live input's pipe plays" % what)
            self.api._stop_capture()
            self.assertEqual(self.helper.running, 0, "after %s the live input's helper was left running" % what)

    def test_a_generator_just_after_a_live_input_takes_the_screen_from_the_pipe(self):
        # the seventh review's M1: the pipe is loaded without waiting for mpv's `path` to follow, so `path` still
        # named the carrier of the generator before; the next generator saw "the carrier plays", loaded nothing, and
        # the pipe stayed on the screen with its helper stopped
        first, second = [g["id"] for g in self.api.shaders.library()[:2]]
        self.assertTrue(self.api.shaders.show(first)["ok"])
        self.assertEqual(self.mpv.log[-1][1], "carrier")
        self.mpv.lag = lambda kind: 5.0 if kind == "path" else 0.0      # mpv goes on saying the old path
        self.live_input()
        self.assertEqual(self.mpv.log[-1][1], "pipe")
        self.mpv.lag = None
        self.assertEqual(self.FakeMpv.kind(self.mpv._get("path")), "carrier", "the stand-in does not lag: nothing is tested")
        self.assertTrue(self.api.shaders.show(second)["ok"])
        self.assertEqual(self.mpv.log[-1][1], "carrier", "the generator was put on over the live input's pipe, which goes on playing")
        self.assertFalse(self.player.pipe_playing)
        self.api._stop_capture()
        self.assertEqual(self.helper.running, 0)

    def test_a_generator_after_a_generator_does_not_load_the_carrier_again(self):
        # the other half of the same rule: the carrier this side loaded last, and that mpv says plays, is left alone
        first, second = [g["id"] for g in self.api.shaders.library()[:2]]
        self.assertTrue(self.api.shaders.show(first)["ok"])
        loads = len(self.mpv.log)
        self.assertTrue(self.api.shaders.show(second)["ok"])
        self.assertEqual(len(self.mpv.log), loads, "the picture restarted between two generators")

    def test_a_carrier_the_player_has_dropped_is_loaded_again(self):
        first, second = [g["id"] for g in self.api.shaders.library()[:2]]
        self.assertTrue(self.api.shaders.show(first)["ok"])
        self.mpv.path = None                                            # mpv went idle by itself
        loads = len(self.mpv.log)
        self.assertTrue(self.api.shaders.show(second)["ok"])
        self.assertEqual(len(self.mpv.log), loads + 1)
        self.assertEqual(self.mpv.log[-1][1], "carrier")

    def test_a_player_that_restarted_by_itself_never_had_the_pipe(self):
        self.live_input()
        self.mpv.pid += 1                           # the service's unit started a new process
        self.mpv._reset()
        self.assertFalse(self.player.pipe_playing)
        self.api._stop_capture()
        self.assertEqual(self.helper.running, 0)

    def test_a_pipe_that_was_not_loaded_is_not_what_plays(self):
        from pvj.api import ApiError
        self.mpv.fail.add("loadfile")
        with self.assertRaises(ApiError):
            self.api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "t")
        self.assertFalse(self.player.pipe_playing)
        self.assertEqual(self.helper.running, 0)

    def test_the_panels_restart_of_the_player_stops_the_live_inputs_helper(self):
        # M3: the restart went past the player's own bookkeeping, and the helper kept the device open
        self.live_input()
        self.assertEqual(self.api.stop_player({}, None, "t"), {"ok": True})
        self.assertEqual(self.helper.running, 0)
        self.assertEqual(self.mpv.log[-1][:2], ("load", "quit"))
        self.assertIsNone(self.player.source_shader)

    def test_a_restart_the_player_does_not_answer_stops_the_helper_all_the_same(self):
        # the seventh review's M2: Player.quit raised, and the stopping of the helper after it was skipped
        from pvj.api import ApiError
        self.live_input()
        self.mpv.dies = True                        # the process goes at once: no answer, the connection closes
        with self.assertRaises(ApiError):
            self.api.stop_player({}, None, "t")
        self.assertEqual(self.mpv.log[-1][:2], ("load", "quit"))
        self.assertEqual(self.helper.running, 0, "the helper goes on writing into a pipe that nobody reads")
        self.assertFalse(self.player.pipe_playing)

    def test_a_restart_that_is_refused_stops_the_helper_too(self):
        from pvj.api import ApiError
        self.live_input()
        self.mpv.fail.add("quit")
        with self.assertRaises(ApiError):
            self.api.stop_player({}, None, "t")
        self.assertEqual(self.helper.running, 0)

    def test_a_question_the_player_does_not_answer_does_not_end_the_live_input(self):
        # the seventh review: one lost answer made the pipe "not playing" for good, and the next _stop_capture
        # stopped the helper of a live input that was on the screen
        self.live_input()
        self.mpv.mute.add("pid")
        self.assertTrue(self.player.pipe_playing)
        self.api._stop_capture()
        self.assertEqual(self.helper.running, 1)
        self.mpv.mute.clear()
        self.assertTrue(self.player.pipe_playing)
        self.mpv.pid += 1                           # but a player that answers as another process never had it
        self.mpv._reset()
        self.assertFalse(self.player.pipe_playing)

    # -- the level on a player that has just started (the watcher's call, pvj/autostart.py) --
    def test_the_level_goes_back_on_a_restarted_player_as_the_mix_has_it(self):
        # on the real Player, so the locks' checker sees this path too (the seventh review's M3)
        self.player.play([self.clip], spawn=False)
        for wish, level in ((lambda: self.api.blackout({"on": True}, None, "t"), -100),
                            (lambda: self.api.blackout({"on": False}, None, "t"), 0),
                            (lambda: self.api.control({"action": "opacity", "value": 50}, None, "t"), -50)):
            wish()
            self.mpv.pid += 1
            self.mpv._reset()                       # a new process: its own full brightness
            self.assertEqual(self.mpv.props["brightness"], 0)
            self.assertTrue(self.api.restore_level())
            self.assertEqual(self.mpv.props["brightness"], level)

    def test_a_level_the_restarted_player_did_not_take_is_said_so(self):
        self.api.blackout({"on": True}, None, "t")
        self.mpv.fail.add("set_property")
        self.assertIs(self.api.restore_level(), False)
        self.mpv.fail.clear()
        self.assertTrue(self.api.restore_level())
        self.assertEqual(self.mpv.props["brightness"], -100)

    def test_the_watcher_puts_the_level_back_after_the_panels_restart(self):
        from pvj import autostart
        watcher = autostart.Autostart(self.api, self.settings, log=lambda *_: None, sleep=lambda s: None)
        watcher.tick()                              # the player it knows
        self.api.blackout({"on": True}, None, "t")
        self.api.stop_player({}, None, "t")
        self.assertEqual(self.mpv.props["brightness"], 0)
        self.mpv.fail.add("set_property")           # not ready for it at the first sight
        watcher.tick()
        self.mpv.fail.clear()
        self.assertEqual(self.mpv.props["brightness"], 0)
        watcher.tick()
        self.assertEqual(self.mpv.props["brightness"], -100, "the restarted player stays lit under a Blackout")

    def test_a_load_thaws_the_freeze_a_hold_made(self):
        # the ninth read: without the player's own "pause off" at a load the clip came in frozen under the still,
        # and only the stress test said so
        self.player.play([self.clip], spawn=False)
        self.settings.data["mix"] = T.stored("crossfade", 0.2)
        frozen = []
        ask = self.player.ipc.request

        def asked(*command):
            if command and command[0] == "loadfile":
                frozen.append(self.mpv.props["pause"])
            return ask(*command)
        self.player.ipc.request = asked
        self.api.play({"file": "b.mov"}, None, "t")
        self.assertEqual(frozen, [True], "the hold did not freeze the clip before the load: nothing is tested")
        self.assertIs(self.mpv.props["pause"], False, "the new clip was loaded frozen under the still")
        deadline = self.time.monotonic() + 10
        while self.time.monotonic() < deadline and (self.api.transitions.running is not None or self.mpv.overlays):
            self.time.sleep(0.01)
        self.assertIs(self.mpv.props["pause"], False)
        self.mpv.props["pause"] = True                                  # and by the player alone: frozen by anybody, a load plays
        self.player.play([self.clip], spawn=False)
        self.assertIs(self.mpv.props["pause"], False)

    # -- Vibes' own dip, through the real player's own source_opacity (which takes the player's lock) --
    def vibes(self):
        vibes = self.api.vibes
        sid = self.api.shaders.library()[0]["id"]
        self.assertTrue(self.api.shaders.show(sid)["ok"])              # a generator is on the screen, as in a rotation
        vibes.running, vibes.epoch = True, self.player.source_epoch
        vibes._clock, vibes._sleep = (lambda: 0.0), (lambda s: None)
        self.addCleanup(setattr, vibes, "running", False)
        return vibes

    def test_vibes_own_dip_writes_its_levels_and_leaves_a_dark_screen_dark(self):
        vibes = self.vibes()
        del self.mpv.levels[:]
        self.assertTrue(vibes._fade(100, False, 0.2))
        self.assertEqual(self.mpv.levels[-1], -100)
        self.assertTrue(vibes._fade(0, True, 0.2))
        self.assertEqual(self.mpv.levels[-1], 0)
        self.api.blackout({"on": True}, None, "t")
        del self.mpv.levels[:]
        self.assertTrue(vibes._fade(0, True, 0.2))
        self.assertEqual(self.mpv.levels, [], "Vibes wrote a level under Blackout")
        self.api.blackout({"on": False}, None, "t")
        self.api.fadeout({"seconds": 0.1}, None, "t")
        deadline = self.time.monotonic() + 5
        while self.time.monotonic() < deadline and (not self.mpv.levels or self.mpv.levels[-1] != -100):
            self.time.sleep(0.005)
        del self.mpv.levels[:]
        self.assertTrue(vibes._fade(0, True, 0.2))
        self.assertEqual(self.mpv.levels, [], "Vibes wrote a level over the operator's Fade out")

    def test_vibes_does_not_bring_the_level_back_while_a_tapped_clip_takes_its_still(self):
        # low, the ninth read: a rotation's dip overtaken by a tap put the level back to full while the still was
        # taken, so the picture went bright, then the half-dark still came over it, then the blend ran
        vibes = self.vibes()
        self.assertTrue(vibes._fade(100, False, 0.2))                  # its own dip, down: the screen is dark
        self.assertEqual(self.mpv.levels[-1], -100)
        tr = self.api.transitions
        self.assertFalse(tr.busy())
        tr._holding.acquire()                                           # a play is taking its still
        try:
            self.assertTrue(tr.busy())
            del self.mpv.levels[:]
            vibes._dipped = True
            vibes._undip()
            self.assertEqual(self.mpv.levels, [], "Vibes wrote the level while a play's still was being taken")
        finally:
            tr._holding.release()
        vibes._dipped = True                                            # with no play on its way it puts the level back, as before
        vibes._undip()
        self.assertEqual(self.mpv.levels[-1:], [0])

    def test_a_stop_and_vibes_own_dip_do_not_wait_for_each_other(self):
        # the sixth review's high finding: Stop held the player's lock and waited for the level's; Vibes' step held
        # the level's and waited for the player's. Here the two are made to meet: Vibes' step is in the player
        # while Stop comes. (The order itself is checked by the locks in every test; this is the hang, by a bound.)
        vibes = self.vibes()
        in_step, go = self.threading.Event(), self.threading.Event()
        ask = self.player.ipc.request

        def slow(*command):
            if command[:2] == ("set_property", "brightness"):
                in_step.set()
                go.wait(5)
            return ask(*command)
        self.player.ipc.request = slow
        dip = self.background(lambda: vibes._fade(100, False, 0.2))
        self.assertTrue(in_step.wait(5))
        stop = self.background(lambda: self.api.control({"action": "stop"}, None, "t"))
        self.time.sleep(0.1)                                            # Stop has come as far as it gets
        go.set()
        dip.join(5), stop.join(5)
        self.assertFalse(dip.is_alive() or stop.is_alive(), "Stop and Vibes' own dip wait for each other for ever")
        black = self.background(lambda: self.api.blackout({"on": True}, None, "t"))
        black.join(5)
        self.assertFalse(black.is_alive(), "a Blackout after them hangs too")

    # -- a Blackout waits for nothing but one write of the level --
    # What a Blackout may wait for: a level that is on its way to the player, and its own. So the bound is two
    # writes of a level as the stand-in answers them (LEVEL: at once in these tests, which make only the path late)
    # plus MARGIN, the time a busy machine may take to run a thread at all. The margin is the one number that is
    # not derived from anything; what it must be is far below the load the Blackout would otherwise wait behind,
    # so the load is made four bounds long.
    LEVEL, MARGIN = 0.0, 0.5
    BOUND = 2 * LEVEL + MARGIN
    LOAD = 4 * BOUND

    def slow_load(self, seconds=LOAD):
        """A clip whose load holds the player's lock for `seconds` (mpv says the old path for that long, and the
        player waits for the new one). Returns the thread, once the load has the lock."""
        self.mpv.lag = lambda kind: seconds if kind == "path" else 0.0
        loading = self.threading.Event()
        ask = self.player.ipc.request

        def asked(*command):
            if command and command[0] == "loadfile":
                loading.set()
            return ask(*command)
        self.player.ipc.request = asked
        t = self.background(lambda: self.player.play([os.path.join(self.media, "b.mov")], spawn=False))
        self.assertTrue(loading.wait(5))
        self.time.sleep(0.05)
        self.mpv.lag = None
        self.assertTrue(t.is_alive(), "the load was over before anything could meet it")
        return t

    def timed(self, fn):
        began = self.time.monotonic()
        fn()
        return self.time.monotonic() - began

    def test_a_blackout_does_not_wait_behind_a_load(self):
        self.player.play([self.clip], spawn=False)
        load = self.slow_load()
        took = self.timed(lambda: self.api.blackout({"on": True}, None, "t"))
        self.assertTrue(load.is_alive(), "the load ended first: nothing was measured")
        self.assertLess(took, self.BOUND, "a Blackout waited %.2f s behind a load that holds the player" % took)
        self.assertEqual(self.mpv.props["brightness"], -100)

    def test_a_blackout_does_not_wait_behind_a_load_while_vibes_dips(self):
        # the sixth review's M2: Vibes' step held the level's lock while it waited for the player's, behind the load
        vibes = self.vibes()
        vibes._clock, vibes._sleep = self.time.monotonic, self.time.sleep
        load = self.slow_load()
        dip = self.background(lambda: vibes._fade(100, False, 1.0))
        self.time.sleep(0.1)                                            # the dip's step waits for the player's lock
        took = self.timed(lambda: self.api.blackout({"on": True}, None, "t"))
        self.assertTrue(load.is_alive(), "the load ended first: nothing was measured")
        self.assertLess(took, self.BOUND, "a Blackout waited %.2f s behind a load, with Vibes' dip in between" % took)
        vibes.running = False
        dip.join(5), load.join(5)

    def test_a_blackout_does_not_wait_behind_a_load_during_a_ramp_or_a_crossfade(self):
        self.player.play([self.clip], spawn=False)
        self.api.fadein({"seconds": 5}, None, "t")                     # a ramp runs
        self.settings.data["mix"] = T.stored("crossfade", 5.0)
        self.api.play({"file": "a.mp4"}, None, "t")                    # and a crossfade's still fades over it
        load = self.slow_load()
        took = self.timed(lambda: self.api.blackout({"on": True}, None, "t"))
        self.assertTrue(load.is_alive(), "the load ended first: nothing was measured")
        self.assertLess(took, self.BOUND, "a Blackout waited %.2f s behind a load, during a ramp and a crossfade" % took)
        self.assertEqual(self.mpv.props["brightness"], -100)
        self.assertEqual(self.mpv.overlays, {}, "the still stayed over the Blackout")
        load.join(5)

    def test_a_stop_waits_for_the_load_it_clears_and_no_longer(self):
        # Stop is not a Blackout: it clears what the load puts up, so it has to come after it. What it must not do
        # is wait for anything else.
        self.player.play([self.clip], spawn=False)
        load = self.slow_load(1.0)
        took = self.timed(lambda: self.api.control({"action": "stop"}, None, "t"))
        self.assertFalse(load.is_alive())
        self.assertLess(took, 1.0 + self.BOUND, "Stop took %.2f s behind a load of one second" % took)
        self.assertIsNone(self.mpv.path)


class LayeredCrossfade(ServerBase):
    """D98: on pvj-render with two video layers the Mix's crossfade is the renderer's (`pvj-crossfade`), with no still
    and no freeze; on classic mpv it is D71's still exactly as before; where the renderer refuses, a cut. The stand-in
    answers as pvj-render does when `layers` is on (tests/fakempv.py). The set-up is RealPlayer's."""

    live_input = RealPlayer.live_input

    def setUp(self):
        super().setUp()
        import threading
        import time
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.threading, self.time = threading, time
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"))
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.helper = self.api.capture = Helper(self.rundir)
        self.api.transitions.log = lambda line: None
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        self.clip = os.path.join(self.media, "a.mp4")
        self.player.play([self.clip], spawn=False)
        self.settings.data["mix"] = T.stored("crossfade", 1.5)
        self.lines = []
        self.api.log = self.lines.append

    def wait_still_gone(self):
        deadline = self.time.monotonic() + 10
        while self.time.monotonic() < deadline and (self.api.transitions.running is not None or self.mpv.overlays):
            self.time.sleep(0.01)

    def test_on_the_new_renderer_a_crossfade_is_the_renderers_with_no_still(self):
        self.mpv.layers = True
        asked = len(self.mpv.commands)
        self.assertEqual(self.api.play({"file": "b.mov"}, None, "t"), {"playing": "b.mov"})
        self.assertEqual(self.mpv.log[-1], ("fade", "file", "b.mov"))
        self.assertEqual(self.mpv.fades, [1.5], "the Mix's own duration")
        sent = self.mpv.commands[asked:]
        for word in ("screenshot-to-file", "overlay-add", "loadfile", "set_property pause"):
            if word == "set_property pause":
                # the one pause is the player's own "play" after the load; nothing froze the old clip
                self.assertEqual(sent.count(word), 1, sent)
            else:
                self.assertNotIn(word, sent, "the still's way was taken: %s" % sent)
        self.assertIs(self.mpv.props["pause"], False)
        self.assertEqual(self.mpv.props["loop-file"], "inf", "a pad's clip loops as after any play")
        self.assertIsNone(self.api.transitions.running)
        self.assertEqual(self.lines, [])
        # asked once per player process: the next crossfade does not ask again
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 1)
        self.assertEqual(self.mpv.log[-1], ("fade", "file", "a.mp4"))

    def test_on_classic_mpv_the_crossfade_is_the_still_as_before(self):
        self.api.play({"file": "b.mov"}, None, "t")
        self.wait_still_gone()
        self.assertIn("screenshot-to-file", self.mpv.commands, "D71's still")
        self.assertNotIn("pvj-crossfade", self.mpv.commands)
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"))
        self.assertEqual(self.mpv.fades, [])
        # classic mpv's "property not found" is remembered for that process: asked once
        self.api.play({"file": "a.mp4"}, None, "t")
        self.wait_still_gone()
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 1)

    def test_a_cut_or_a_dip_never_asks_about_layers(self):
        for style in ("cut", "dip"):
            self.settings.data["mix"] = T.stored(style, 0.2)
            self.api.play({"file": "b.mov"}, None, "t")
        self.assertNotIn("get_property pvj-render", self.mpv.commands, "classic plays are unchanged")

    def test_when_the_renderer_refuses_the_fade_it_is_a_cut_never_black(self):
        self.mpv.layers = True
        self.mpv.fail.add("pvj-crossfade")
        self.assertEqual(self.api.play({"file": "b.mov"}, None, "t"), {"playing": "b.mov"})
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"), "cut to the clip")
        self.assertEqual(self.mpv.path, self.mpv.playlist[0])
        self.assertNotIn("screenshot-to-file", self.mpv.commands)
        self.assertTrue(any("a cut instead" in line and "error running command" in line for line in self.lines), self.lines)

    def test_on_the_new_renderer_every_wipe_and_slide_is_the_renderers_with_no_still(self):
        self.mpv.layers = self.mpv.moves = True
        names = T.WIPES + T.SLIDES
        self.assertEqual(len(names), 8)
        for n, name in enumerate(names):
            self.settings.data["mix"] = T.stored(name, 1.5)
            asked = len(self.mpv.commands)
            clip = ("b.mov", "a.mp4")[n % 2]
            self.assertEqual(self.api.play({"file": clip}, None, "t"), {"playing": clip})
            self.assertEqual(self.mpv.log[-1], ("fade", "file", clip))
            self.assertEqual(self.mpv.fade_styles[-1], name, "the panel's name goes to the renderer unchanged")
            self.assertEqual(self.mpv.fades[-1], 1.5, "the Mix's own duration")
            sent = self.mpv.commands[asked:]
            for word in ("screenshot-to-file", "overlay-add", "loadfile"):
                self.assertNotIn(word, sent, "the still's way was taken: %s" % sent)
            self.assertEqual(sent.count("set_property pause"), 1, "nothing froze the old clip: %s" % sent)
        self.assertIsNone(self.api.transitions.running)
        self.assertEqual(self.lines, [])
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 1, "asked once per player process")

    def test_a_crossfade_sends_what_it_always_sent(self):
        self.mpv.layers = self.mpv.moves = True
        self.api.play({"file": "b.mov"}, None, "t")
        self.assertEqual(self.mpv.fade_styles, [None], "no fifth word")

    def test_a_renderer_from_before_keeps_the_still_for_a_wipe_and_its_own_crossfade(self):
        self.mpv.layers = True          # two layers, and no `moves`
        self.settings.data["mix"] = T.stored("slide-left", 0.2)
        self.still_crossfade()
        self.settings.data["mix"] = T.stored("crossfade", 0.2)
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertEqual(self.mpv.log[-1], ("fade", "file", "a.mp4"))

    def test_on_classic_mpv_a_wipe_is_the_still_as_before(self):
        self.settings.data["mix"] = T.stored("wipe-from-top", 0.2)
        self.still_crossfade()
        self.assertNotIn("pvj-crossfade", self.mpv.commands)
        self.api.play({"file": "a.mp4"}, None, "t")
        self.wait_still_gone()
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 2, "classic's answer is remembered: "
                         "the question about layers, as Player.still asks it, and the one about moves, once each")

    def test_out_of_a_generator_on_the_new_renderer_a_slide_is_d71s_still(self):
        self.mpv.layers = self.mpv.moves = True
        sid = self.api.shaders.library()[0]["id"]
        self.assertTrue(self.api.shaders.show(sid)["ok"])
        self.settings.data["mix"] = T.stored("slide-right", 0.2)
        self.still_crossfade()
        self.assertIsNone(self.api.player.source_shader)

    def test_out_of_a_live_input_on_the_new_renderer_a_wipe_is_d71s_still(self):
        self.mpv.layers = self.mpv.moves = True
        self.live_input()
        self.settings.data["mix"] = T.stored("wipe-from-bottom", 0.2)
        self.still_crossfade()
        self.assertFalse(self.player.pipe_playing)

    def test_when_the_renderer_refuses_a_wipe_it_is_a_cut_never_black(self):
        self.mpv.layers = self.mpv.moves = True
        self.settings.data["mix"] = T.stored("wipe-from-left", 1.0)
        self.mpv.fail.add("pvj-crossfade")
        self.assertEqual(self.api.play({"file": "b.mov"}, None, "t"), {"playing": "b.mov"})
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"), "cut to the clip")
        self.assertNotIn("screenshot-to-file", self.mpv.commands)
        self.assertTrue(any("no wipe-from-left on the new renderer, a cut instead" in line for line in self.lines), self.lines)

    def test_a_wipe_from_a_dark_screen_is_a_cut_as_a_crossfade_is(self):
        self.mpv.layers = self.mpv.moves = True
        self.settings.data["mix"] = T.stored("slide-up", 1.0)
        self.api.blackout({"on": True}, None, "t")
        self.api.play({"file": "b.mov"}, None, "t")
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"))
        self.assertEqual(self.mpv.fades, [])

    def test_a_box_that_gave_up_on_stills_still_draws_the_renderers_wipe(self):
        self.mpv.layers = self.mpv.moves = True
        self.settings.data["mix"] = T.stored("wipe-from-left", 1.0)
        self.api.play({"file": "b.mov"}, None, "t")
        self.api.transitions._give_up("the clip dropped 30 frames in 2.0 seconds")
        self.assertNotIn("fallback", self.api._mix_settings(), "nothing of a still gives this up")
        self.api.play({"file": "a.mp4"}, None, "t")
        self.assertEqual(self.mpv.log[-1], ("fade", "file", "a.mp4"), "no dip")
        self.player._source = "/x/generator.glsl"
        self.assertIn("fallback", self.api._mix_settings(), "out of a generator the still's reason applies")

    def test_one_question_of_the_renderer_answers_for_the_crossfade_and_the_wipe(self):
        self.mpv.layers = self.mpv.moves = True
        self.settings.data["mix"] = T.stored("wipe-from-left", 1.0)
        self.assertIs(self.player.crossfades(), True)
        self.assertIs(self.player.moves(ask=False), True, "noted with the layers, with no question of its own")
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 1)
        self.api.transitions._give_up("the clip dropped 30 frames in 2.0 seconds")
        self.assertNotIn("fallback", self.api._mix_settings(), "the status asks the player nothing, and shows no still's reason")
        self.assertEqual(self.mpv.commands.count("get_property pvj-render"), 1)

    def test_a_renderer_without_wipes_noted_with_its_layers_keeps_the_stills_reason(self):
        self.mpv.layers = True          # two layers, and no `moves`
        self.settings.data["mix"] = T.stored("wipe-from-left", 1.0)
        self.assertIs(self.player.crossfades(), True)
        self.assertIs(self.player.moves(ask=False), False)
        self.api.transitions._give_up("the clip dropped 30 frames in 2.0 seconds")
        self.assertIn("fallback", self.api._mix_settings())

    def still_crossfade(self):
        """The play went D71's way: a still of the window, the clip loaded under it, the still faded away."""
        asked = len(self.mpv.commands)
        self.api.play({"file": "b.mov"}, None, "t")
        self.wait_still_gone()
        sent = self.mpv.commands[asked:]
        self.assertIn("screenshot-to-file", sent, "no still: %s" % sent)
        self.assertIn("overlay-add", sent, "the still was not laid over the clip")
        self.assertNotIn("pvj-crossfade", sent)
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"))
        self.assertEqual(self.mpv.fades, [])
        self.assertEqual(self.api.transitions.last.get("ended"), "done", self.api.transitions.last)
        self.assertFalse(any("a cut instead" in line for line in self.lines), self.lines)

    def test_out_of_a_generator_on_the_new_renderer_the_crossfade_is_d71s_still(self):
        # D89: the still of the window is what a crossfade out of a generator fades from, on every player
        self.mpv.layers = True
        sid = self.api.shaders.library()[0]["id"]
        self.assertTrue(self.api.shaders.show(sid)["ok"])
        self.still_crossfade()
        self.assertIsNone(self.api.player.source_shader, "the generator came off when the clip loaded")

    def test_out_of_a_live_input_on_the_new_renderer_the_crossfade_is_d71s_still(self):
        # and D62's hand-over of the pipe stays as it was: the one layer lets go of it at the load
        self.mpv.layers = True
        self.live_input()
        self.still_crossfade()
        self.assertFalse(self.player.pipe_playing)

    def test_a_crossfade_from_a_dark_screen_rises_as_before(self):
        # Blackout on: no picture to blend from; the clip loads as a cut, as on classic
        self.mpv.layers = True
        self.api.blackout({"on": True}, None, "t")
        self.api.play({"file": "b.mov"}, None, "t")
        self.assertEqual(self.mpv.log[-1], ("load", "file", "b.mov"))
        self.assertEqual(self.mpv.fades, [])

    def test_the_status_says_no_fallback_for_the_renderers_crossfade(self):
        self.mpv.layers = True
        self.api.play({"file": "b.mov"}, None, "t")
        self.api.transitions._give_up("a still took long")          # from a time before the new renderer
        self.assertNotIn("fallback", self.api._mix_settings())
        self.settings.data["mix"] = T.stored("wipe-from-left", 1.0)
        self.assertIn("fallback", self.api._mix_settings(), "a wipe still takes the still")
        # a crossfade out of a generator takes the still on this player too: then the still's reason is shown
        self.settings.data["mix"] = T.stored("crossfade", 1.0)
        self.assertNotIn("fallback", self.api._mix_settings())
        self.player._source = "/x/generator.glsl"
        self.assertIn("fallback", self.api._mix_settings(), "out of a generator the still's reason applies")


class Stress(ServerBase):
    """A test of another kind (after reviews that each found an order of events the hand-written tests did not
    have): random actions that overlap, from several threads, against the fake player, which has the real one's
    lock and epoch and answers late by random amounts, as a player does; then what must hold whatever the order was.

    What plays in the end is not guessed from the end: the order in which the wishes were accepted is written down
    as they are made (every newer wish is made under the player's lock, where the fake also notes every change of
    what plays, so the two are one list in one order), and the newest wish decides what must be on the screen.

    A round's actions and their delays come from its seed and its number alone, so a round can be run again by
    itself (`replay`). HOW THE THREADS INTERLEAVE IS NOT FIXED BY THE SEED, and neither is which call draws which
    lag: the same round can pass and fail. A failure therefore prints the seed, the round, the actions and the
    order that was seen, and the fixed seeds are run in every suite run; a fault that needs a rare order shows up
    some of the time, and the mutation table below says how often for the faults that are known.

    What it catches by itself, tried on scratch copies of the code with one thing broken at a time, the five seeds
    run three times each (caught in how many of the three):

    | what was broken                                                                          | Stress alone | the tests by hand |
    | ---------------------------------------------------------------------------------------- | ------------ | ----------------- |
    | a  the Blackout switch set outside the level's lock                                       | 3 of 3       | yes               |
    | b  the live input's helper stopped whatever plays                                         | 3 of 3       | yes               |
    | c  no level put back after a dip that was overtaken                                       | 3 of 3       | yes               |
    | d  a ramp's step written outside the level's lock                                         | 3 of 3       | yes               |
    | e  a newer wish and its own change of what plays not one step (no player's lock)          | 3 of 3       | yes               |
    | f  a callback of the fader called under its lock                                          | 3 of 3       | the run hangs     |
    | g  a clip's last look at the newest wish outside the player's lock                        | 3 of 3       | yes               |
    | h  the fader taken at the tap, before the clip has loaded                                 | 0 of 3       | yes               |
    | i  a play's own level written outside the level's lock                                    | 3 of 3       | yes               |
    | j  a load setting the level whatever was asked for since its tap                          | 3 of 3       | yes               |
    | k  Stop's wish and its clearing of the screen in two steps                                | 3 of 3       | yes               |
    | l  the Opacity slider's value set outside the level's lock                                | 3 of 3       | yes               |
    | m  a generator setting the level whatever was asked for since it was chosen               | 3 of 3       | yes               |
    | n  the level put back after a Stop whoever has taken the fader since                      | 0 of 3       | yes               |
    | o  Vibes' step taking the level's lock before the player's (the deadlock of round six)    | 3 of 3       | yes               |
    | p  the player forgetting nothing at its restart (the pipe "plays" on)                     | 3 of 3       | yes               |
    | q  a clip that is loaded leaving the pipe "playing" (a fault inside pvj/player.py)        | 3 of 3       | yes               |
    | r  a shader pad's shader shown while the play holds the player's lock (D73)               | 3 of 3       | yes               |
    | s  a shader pad taking the play's ticket before its shader: two newest wishes for one tap | 0 of 3       | yes               |
    | t  a queued shader refused by the queue's own job before it (the older tap stays on)      | not in it    | yes               |
    | u  the effect put before the generator in the player's shader list (D74)                 | 3 of 3       | yes               |
    | v  the question's pixel left on the screen (D89, `Player.overlay_scales`)                | 3 of 3       | yes               |
    | w  the player asked with a still of ours on the screen (D89)                             | 0 of 3       | yes               |
    | x  the answer kept for the next player process (D89)                                     | 0 of 3       | yes               |
    | y  a scaled crossfade step sent without its display size (D89)                           | 0 of 3       | yes               |
    | z  a part of a halved still placed by the still's own pixels (D89, `on_screen`)          | 0 of 3       | yes               |
    | aa a still of the clip that misses making the box give up (D89, the fault it found)      | 2 of 3       | yes               |
    | ab a still of the clip taken with the logo on (D89)                                      | 0 of 3       | yes               |
    | ac misses of the still of the clip never turning to the window (D89)                     | 0 of 3       | yes               |

    What the rows say that is not "caught":
    * h needs a level wish that runs (a Fade in, a Fade out), a clip tapped with a blend, and then something newer
      before that clip loads. The spelling tried here was the line `self.fader.cancel()` put in before
      `tapped = self._level_mark()` in Api.play: none of three, twice. The seventh review spelled it
      `tapped = self.fader.cancel()` and saw this test fail three times of three, and so it does here. That is not
      the fault being caught: this test learns of a tap by wrapping `Api._level_mark` (setUp), the second spelling
      no longer calls it, and with no tap noted every Fade out counts as "asked for after every tap". A control
      shows it: `tapped = self.fader.mark()`, which behaves exactly as the right code, fails the same way, with the
      same message, two times of two. So: 0 of 3 for the fault, and A CHANGE THAT READS THE MARK WITHOUT
      `_level_mark` MAKES THIS TEST FAIL WITH A MESSAGE ABOUT A FADE OUT; look at the hook first.
      The test by hand makes the fault (`test_a_tap_that_is_overtaken_before_it_loads_leaves_a_running_fade_alone`).
    * n was called an equivalent mutation here until the seventh review, wrongly: a Fade in that comes between a
      Stop's clear and its putting-back of the level was cancelled by it, and the level snapped to full. The rounds
      do not draw that meeting (none of three, twice). The test by hand that kills it was added then:
      `Threads.test_a_stop_that_meets_a_fade_in_does_not_cut_it_short`.
    * p was caught none of three times when the table was first made and is caught three of three since the whole
      table was run again for the shader pad (the seventh review's changes to the restart and to `pipe_playing`
      came between the two runs; which of them did it was not looked for). By hand: three tests of the real player.
    * r and s came with the pad that holds a shader (D73, tests/test_shader_pads.py), which is one more kind of
      action here ("shader pad", from the panel: a controller's tap is queued for the engine's worker, which lives
      on after a round, so it is tested by hand only). r is caught by the locks themselves, here and in eighteen
      tests by hand. s changes nothing that the end of a round can see (the second wish is the same action's); the
      test by hand counts the wishes of one tap (`Tapped.test_a_tap_is_one_newest_wish_as_a_shader_chosen_by_hand_is`).
    * t (the review of #114) is a fault of the engine's queue for the controllers' taps, which this test does not
      draw, for the reason given under r and s: "not in it" is not "0 of 3". By hand, with the worker held between
      taking its job and taking the screen: `tests.test_shader_pads.Queued`, for a pad, a preset and a step.
    * The whole table, a to s, was run again when "shader pad" was added: the rows above are from that run. One of
      n's three runs failed, not for n's fault but with "the box gave up on transitions: the still took 1.3
      seconds", while a browser was taking pictures on the same machine: A MACHINE THAT STALLS FOR A SECOND MAKES
      THIS TEST FAIL THAT WAY, since the code under test measures its still by the real clock.
    * o is caught by the locks themselves (tests/lockrank.py), in this test and in single-threaded tests by hand:
      no interleaving is needed for it.
    * e is not the fifth review's e, "the fader taken before the generation moves": no way of playing takes the
      fader before its load any more. f by hand: a test with no bound of its own waits for ever, so the run does
      not end; a watchdog (`python3 -X faulthandler`, `faulthandler.dump_traceback_later`) shows where, and CI's
      job has its time limit.
    * u is caught by the second test below, not by the first: an effect's actions (on, off, the next one; D74) are
      in rounds of their own, which are the first test's rounds with one or two of those actions added from numbers
      of their own. The first test's rounds are as they were, so the rows above stand as measured. The actions were
      first put into the first test's rounds, and for a while the second test did not hold "the box never gives
      up on transitions": a still had a tenth of a second of real time on this test's fast clock, and a stalled
      runner failed a round. That limit is out of the way now (setUp), and both tests hold it for every other reason.
    * v to ac (D89, 2026-10-15; most rounds run on a stand-in that scales an overlay, about half with a clip
      smaller than the screen). This test holds the order of things, not what is drawn where, so faults of place
      and size (y, z), of a setting (ab) and of what one player process is told (w, x, ac: they need two processes,
      two misses in a row, or a still on the screen at the moment of the question) are for the tests by hand
      (`Scaled`, `ScaledRealPlayer`, `ReadHalf`); each of v to ac fails at least one of those. aa is the fault this
      test found in its first run on the new stand-in (a generator chosen during the screenshot).
    The table is run again whenever this test changes: adding ten kinds of action to it took a from three of
    three to none, and moving the fake's lateness from the caller to the stand-in for mpv took d and i to none,
    before weights, a look inside each wish's own step and lateness on the way to the player brought them back.
    """
    SEEDS = (20261009, 1, 7, 4242, 99)
    ROUNDS = 50
    FAST = 10.0                             # the fader's, the transitions' and Vibes' clocks run this much faster
    LAGS = {"opacity": 0.003, "play": 0.006, "still": 0.008, "path": 0.004, "look": 0.002, "helper": 0.005}
    # Seconds a Blackout may take here, whatever else goes on: two writes of a level at the stand-in's slowest (one
    # on its way, and its own; see RealPlayer.BOUND) and a margin for a machine that runs a dozen threads of the
    # round at once, twice the hand test's. What it would wait for if the rule were broken is a load's wait for
    # its path (three seconds in pvj/player.py) or for ever.
    BLACKOUT = 2 * LAGS["opacity"] + 2 * 0.5

    def setUp(self):
        super().setUp()
        import re as re_module
        import threading
        import time
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.threading, self.time, self.re = threading, time, re_module
        # the REAL player (pvj/player.py), talking to a stand-in for mpv on its socket: its lock, what it remembers
        # of what it loaded and the order of its questions are what is tested, and a fault put into player.py shows
        # The limit after which the box gives up on transitions is a matter of time on a real clock, and this test
        # runs its clock ten times fast: a still had a tenth of a second, and a runner that stalled for 80 ms failed
        # the round with "the still took 1.0 seconds" (seen in CI on a branch stacked on this one, and on a loaded
        # desk). The limit itself is held by the test with a clock of its own above; here it is out of the way.
        slow = T.SLOW
        T.SLOW = 600.0
        self.addCleanup(setattr, T, "SLOW", slow)
        half = T.HALF_WINDOW
        T.HALF_WINDOW = 0                   # the stand-in's screen is 8 x 4: halved as a large one is (D89)
        self.addCleanup(setattr, T, "HALF_WINDOW", half)
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"), lag=self.lag)
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.helper = self.api.capture = Helper(self.rundir)
        self.tr = self.api.transitions
        self.tr.log = lambda line: None
        self.api.log = lambda line: None
        fast = self.FAST
        self.tr._clock, self.tr._sleep = (lambda: time.monotonic() * fast), (lambda s: time.sleep(s / fast))
        self.api.fader = type(self.api.fader)(self.api._apply_opacity, clock=lambda: time.monotonic() * fast, sleep=lambda s: time.sleep(s / fast))
        self.api.vibes._clock, self.api.vibes._sleep = (lambda: time.monotonic() * fast), (lambda s: time.sleep(s / fast))
        self.api.registry.set_enabled("shaders", True)
        self.api.registry.set_enabled("inputs-srt", True)
        self.settings.data["streams"] = [{"id": "bbbb0001", "name": "Camera", "url": "rtsp://192.168.1.60/live"}]
        self.api.shaders.log = lambda *_: None
        self.api.effects.log = lambda *_: None
        self.sid = self.api.shaders.library()[0]["id"]
        self.fid = self.api.effects.order()[0]
        self.effects_on = 0                 # how often an effect really went on, over all rounds (the actions prove nothing otherwise)
        self.pairs = 0                      # and how often the player was given a generator and an effect together
        self.settings.data["pads"]["banks"][self.SHADER_PAD[0]]["pads"][self.SHADER_PAD[1]] = {"label": "", "file": "", "shader": self.sid}
        for i in range(6):
            open(os.path.join(self.media, "r%d.mp4" % i), "w").close()
        self.epoch = 0
        self.me = threading.local()         # the action a thread is carrying out, or nothing (the test's own calls)
        self.bad = []                       # what was seen to go wrong while a round ran
        self.switches, self.sliders = [], []
        self.wishes_for_level, self.taps = [], []   # (the fader's token, kind) of each wish for a level; (token, action) at each tap
        marked = self.api._level_mark

        def tap():
            token = marked()
            self.taps.append((token, getattr(self.me, "index", None)))
            return token
        self.api._level_mark = tap
        self.lags, self.lag_lock = None, threading.Lock()
        # -- every command that changes what plays is sent with the player's lock held --
        ask = self.player.ipc.request

        def asked(*command):
            if command and command[0] in ("loadfile", "stop", "playlist-next", "playlist-prev", "quit") and not self.player._lock._is_owned():
                self.bad.append("%s was sent to the player without the player's lock" % command[0])
            if command[:2] == ("set_property", "brightness"):
                self.wait("opacity")        # on its way to the player: a level that was decided too early lands late
            if command[:2] == ("set_property", "glsl-shaders"):
                # a generator and an effect together (D74): the generator is first, or the effect filters nothing
                kinds = [os.path.basename(p).split("-")[0] for p in command[2]]
                if "shader" in kinds and "effect" in kinds:
                    self.pairs += 1
                    if kinds.index("effect") < kinds.index("shader"):
                        self.bad.append("the player was given the effect before the generator: %s" % (kinds,))
            return ask(*command)
        self.player.ipc.request = asked
        # -- the wishes, written into the stand-in's own list as they are made --
        claim, end, newest = self.tr.claim, self.tr.end, self.tr.newest

        def wish(gen):
            if not self.player._lock._is_owned():
                self.bad.append("%s became the newest wish outside the player's lock" % getattr(self.me, "action", None))
            self.mpv.log.append(("wish", gen, getattr(self.me, "index", None)))

        def claimed():
            ticket = claim()
            wish(ticket[0])
            return ticket

        def ended(why="ended", newer=False):
            gen = end(why, newer)
            if newer:
                wish(gen)
            return gen

        def looked(ticket):
            answer = newest(ticket)
            self.wait("look")               # a look that is not one step with the load shows here
            return answer
        self.tr.claim, self.tr.end, self.tr.newest = claimed, ended, looked
        # -- the level's wishes, in the order the level's lock gave them (a wrapper around the real lock) --
        real = self.api.fader.stepping

        class Noted:
            def __enter__(self_):
                real.acquire()

            def __exit__(self_, *exc):
                kind, done = getattr(self.me, "kind", None), getattr(self.me, "noted", True)
                if not done:
                    self.me.noted = True
                    if kind in ("blackout on", "blackout off", "fade in"):
                        self.switches.append(kind == "blackout on")
                        if self.api.mix["blackout"] != (kind == "blackout on"):
                            self.bad.append("%s found another Blackout switch in the mix inside its own step" % self.me.action)
                    elif kind in ("opacity 40", "opacity 100", "reset"):
                        value = 100.0 if kind == "reset" else float(kind.split()[1])
                        self.sliders.append(value)
                        if self.api.mix["opacity"] != value:
                            self.bad.append("%s found another opacity in the mix inside its own step" % self.me.action)
                    if kind in ("blackout on", "blackout off", "fade in", "opacity 40", "opacity 100", "reset") or (kind == "fade out" and not self.api.mix["blackout"]):
                        self.wishes_for_level.append((self.api.fader.mark(), kind))     # the fader's own count orders them
                real.release()
        self.api.fader.stepping = Noted()
        start = self.helper.start
        self.helper.start = lambda device, mode: (self.wait("helper"), start(device, mode))

    def wait(self, kind):
        self.time.sleep(self.lag(kind))

    def lag(self, kind):
        if self.lags is None:
            return 0.0
        with self.lag_lock:
            if kind == "path" and self.lags.random() < 0.8:     # the player's wait for a late path costs 50 ms a time
                return 0.0
            return self.lags.random() * self.LAGS[kind]

    PLAYS = ("play cut", "play dip", "play crossfade", "play wipe", "controller plays crossfade", "controller plays slide")
    CORE = PLAYS + ("stop", "blackout on", "blackout off", "fade out", "fade in", "opacity 40", "opacity 100", "next",
                    "generator by hand", "rotation tick", "live input")
    # what an operator does most, twice as often as the rest; the rare ones that matter (a restart, Vibes' own dip) too
    KINDS = CORE + CORE + ("reset", "vibes dip", "vibes dip", "list", "stream", "test pattern", "test pattern off", "tone", "restart", "restart",
                           "shader pad", "shader pad")
    GENERATORS = ("generator by hand", "shader pad")       # a pad that holds a shader is the same choosing by hand (D73)
    TAPS = GENERATORS + ("rotation tick", "live input", "list", "stream", "test pattern")     # besides the clips: who notes the level's mark
    SHADER_PAD = [2, 11]
    # An effect over whatever plays, a generator included (D74). It neither plays, stops nor sets the level, so it is
    # no wish and no tap; what it adds is the effects' engine and its lock beside everything above. These are drawn
    # from numbers of their own and added to a round's actions in the second test only (see row u in the docstring).
    EFFECTS = ("effect on", "effect on", "effect off", "effect next")

    def vibes_dip(self):
        """Vibes' own dip between two shaders, as its rotation makes it: only over a generator, down and up again,
        and the screen put right if it loses the screen on the way."""
        vibes = self.api.vibes
        if self.player.source_shader is None:
            return
        vibes.running, vibes.epoch = True, self.player.source_epoch
        if not (vibes._fade(vibes._level(), False, 0.3) and vibes._fade(0, True, 0.3)):
            vibes._undip()

    def act(self, kind, index):
        from pvj.midi import MIDI_DEVICE
        api = self.api
        if kind in self.PLAYS:
            style = {"cut": "cut", "dip": "dip", "crossfade": "crossfade", "wipe": "wipe-from-left", "slide": "slide-up"}[kind.split()[-1]]
            self.settings.data["mix"] = T.stored(style, 0.3)
            return api.play({"file": "r%d.mp4" % index}, MIDI_DEVICE if kind.startswith("controller") else None, "stress")
        return {
            "stop": lambda: api.control({"action": "stop"}, None, "stress"),
            "blackout on": lambda: api.blackout({"on": True}, None, "stress"),
            "blackout off": lambda: api.blackout({"on": False}, None, "stress"),
            "fade out": lambda: api.fadeout({"seconds": 0.3}, None, "stress"),
            "fade in": lambda: api.fadein({"seconds": 0.3}, None, "stress"),
            "opacity 40": lambda: api.control({"action": "opacity", "value": 40}, None, "stress"),
            "opacity 100": lambda: api.control({"action": "opacity", "value": 100}, None, "stress"),
            "reset": lambda: api.control({"action": "reset"}, None, "stress"),
            "next": lambda: api.control({"action": "next"}, None, "stress"),
            "generator by hand": lambda: api.shaders.show(self.sid),
            "shader pad": lambda: api.play({"pad": list(self.SHADER_PAD)}, None, "stress"),     # from the panel: it waits for its answer
            "rotation tick": lambda: api.shaders.show(self.sid, epoch=self.epoch, cut=False),
            "vibes dip": self.vibes_dip,
            "live input": lambda: api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "stress"),
            "list": lambda: api.play({"preset": "startless"}, None, "stress"),
            "stream": lambda: api.play({"stream": "bbbb0001"}, None, "stress"),
            "test pattern": lambda: api.test_pattern({"on": True}, None, "stress"),
            "test pattern off": lambda: api.test_pattern({"on": False}, None, "stress"),
            "tone": lambda: api.test_tone({"channel": "left"}, None, "stress"),
            "restart": lambda: (api.stop_player({}, None, "stress"), api.restore_level()),      # and the watcher sees the new one
            "effect on": self.effect_on,
            "effect off": lambda: api.effects.off(),
            "effect next": lambda: api.effects.step(1),
        }[kind]()

    def effect_on(self):
        self.api.effects.put(self.fid)
        self.effects_on += 1

    def quiet(self, baseline, what):
        """Every thread a round started has ended, within a time no round needs: or something waits for ever."""
        deadline = self.time.monotonic() + 15
        while self.time.monotonic() < deadline:
            left = [t for t in self.threading.enumerate() if t not in baseline and t.is_alive()
                    and not t.name.startswith(("shader", "effect"))]        # the engines' own workers live on by design
            if not left and self.tr.running is None:
                return
            self.time.sleep(0.002)
        self.fail("%s: threads that never ended (a deadlock): %s" % (what, [t.name for t in left]))

    def brightness(self):
        return self.mpv.props["brightness"]

    def one_round(self, seed, number, effects=False):
        import random
        from pvj.api import ApiError
        api, player, mpv, time = self.api, self.player, self.mpv, self.time
        rng = random.Random("%d/%d" % (seed, number))           # this round's own numbers, whatever came before it
        chosen = [rng.choice(self.KINDS) for _ in range(rng.randint(3, 6))]
        delays = [rng.random() * 0.006 for _ in chosen]
        if effects:
            more = random.Random("%d/%d/effects" % (seed, number))  # the effect's actions, from numbers of their own
            for _ in range(more.choice((1, 1, 2))):
                chosen.append(more.choice(self.EFFECTS))
                delays.append(more.random() * 0.006)
        what = "seed %d round %d" % (seed, number)
        baseline = set(self.threading.enumerate())
        # a clean start: a clip plays, lit, nothing on its way
        self.lags = None
        self.me.kind = self.me.index = None
        api.vibes.running = False
        api.control({"action": "stop"}, None, "stress")
        api.blackout({"on": False}, None, "stress")
        api.control({"action": "opacity", "value": 100}, None, "stress")
        self.tr.given_up = ""
        # D89: an mpv that scales an overlay (0.38 on) in 80 of 100 rounds, 55 of them with a clip smaller than the
        # screen (a still of the clip) and 25 with a clip larger (a still of the window, halved); an mpv that cannot
        # (0.37) in the rest. From numbers of their own, so the rounds' actions are as they were, and asked anew in
        # every round (the stand-in's answer changes between rounds, its process does not).
        scaling = random.Random("%d/%d/scaling" % (seed, number)).random()
        mpv.scales, mpv.video = scaling < 0.8, ((4, 2) if scaling < 0.55 else (1920, 1080))
        self.tr._scaling, self.tr._no_clip, self.tr._misses = None, None, 0
        self.settings.data["mix"] = T.stored("cut", 0.3)
        api.play({"file": "a.mp4"}, None, "stress")
        before = rng.random()
        if before < 0.35:                                       # or a generator, as while Vibes rotates
            api.shaders.show(self.sid)
        elif before < 0.5:                                      # or a live input, with its helper
            api.play({"capture": {"device": "video0", "mode": "720p30"}}, None, "stress")
        self.quiet(baseline, what + " (before the round)")
        self.epoch = player.source_epoch
        del mpv.log[:], self.bad[:], self.switches[:], self.sliders[:], self.wishes_for_level[:], self.taps[:]
        self.helper.most = self.helper.running
        self.lags = random.Random("%d/%d/lags" % (seed, number))
        errors = []

        def run(index):
            self.me.kind, self.me.index, self.me.noted = chosen[index], index, False
            self.me.action = "%d %s" % (index, chosen[index])
            time.sleep(delays[index])
            began = time.monotonic()
            try:
                self.act(chosen[index], index)
            except ApiError as e:
                # nothing to step to, and no picture to put an effect on (a Stop came first): honest answers
                if not ((chosen[index] == "next" or chosen[index] in self.EFFECTS) and e.status == 409):
                    errors.append("%s raised %r" % (self.me.action, e))
            except Exception as e:
                errors.append("%s raised %r" % (self.me.action, e))
            if chosen[index].startswith("blackout") and time.monotonic() - began > self.BLACKOUT:
                errors.append("%s took %.2f seconds: a Blackout waits for nothing but one write of the level" % (self.me.action, time.monotonic() - began))
        threads = [self.threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(chosen))]
        for t in threads:
            t.start()
        told = lambda: "%s\n  actions (with the delay each began after, ms): %s\n  the order seen: %s" % (
            what, ["%d %s +%.1f" % (i, k, d * 1000) for i, (k, d) in enumerate(zip(chosen, delays))], list(mpv.log))
        for t in threads:
            t.join(15)
            self.assertFalse(t.is_alive(), "a call never returned (a deadlock)\n" + told())
        self.quiet(baseline, what)
        self.lags = None
        api.vibes.running = False
        log, said = list(mpv.log), told()
        # -- what must hold, whatever the order was --
        self.assertEqual(errors, [], said)
        self.assertEqual(self.bad, [], said)
        self.assertEqual(self.tr.given_up, "", "the box gave up on transitions\n" + said)
        self.assertEqual(mpv.overlays, {}, "a still was left on the screen\n" + said)
        self.assertFalse(mpv.props["pause"], "the clip was left frozen\n" + said)
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("transition-") or n.startswith("overlay-")], [], said)
        # newest wins, by the order in which the wishes were accepted
        clip = self.re.compile(r"r(\d)\.mp4")
        newest = 0
        claims, loaded = {}, set()
        for entry in log:
            if entry[0] == "wish":
                self.assertGreater(entry[1], newest, "the wishes are not in the order of their generation\n" + said)
                newest = entry[1]
                claims[entry[2]] = entry[1]
            elif entry[1] == "file" and clip.fullmatch(entry[2]):
                index = int(clip.fullmatch(entry[2]).group(1))
                loaded.add(index)
                self.assertEqual(claims.get(index), newest, "the clip of action %d was loaded after a newer wish had been accepted\n%s" % (index, said))
        wishes = [i for i, entry in enumerate(log) if entry[0] == "wish"]
        loads = [i for i, entry in enumerate(log) if entry[0] == "load"]
        path = os.path.basename(mpv.path or "")
        if wishes:
            at = wishes[-1]
            kind = chosen[log[at][2]]
            if kind in self.PLAYS:
                self.assertTrue(loads and loads[-1] > at and log[loads[-1]][1:] == ("file", "r%d.mp4" % log[at][2]),
                                "the newest wish was the clip of action %d, and it is not what was loaded last\n%s" % (log[at][2], said))
                self.assertEqual(path, "r%d.mp4" % log[at][2], said)
            else:
                # its own change of what plays is one step with the wish: just before it (a generator) or just after
                later = [i for i in loads if i > at and not (log[i][1] == "file" and not clip.fullmatch(log[i][2]) and i <= at + 1)]
                self.assertTrue(not later or later[-1] <= at + 1, "something was loaded after the newest wish (%s) and its own change\n%s" % (kind, said))
                if kind in ("stop", "test pattern off", "restart"):
                    self.assertEqual((mpv.path, player.source_shader), (None, None), "something plays after the %s that came last\n%s" % (kind, said))
                elif kind in self.GENERATORS:
                    self.assertIsNotNone(player.source_shader, "the generator chosen last is not on the screen\n" + said)
                elif kind == "live input":
                    self.assertTrue(player.pipe_playing, "the live input started last is not what plays\n" + said)
                elif kind in ("list", "stream", "test pattern", "tone"):
                    self.assertIsNotNone(mpv.path, "nothing plays after the %s that came last\n%s" % (kind, said))
        # the switches, by the order the level's lock gave them
        if self.switches:
            self.assertEqual(api.mix["blackout"], self.switches[-1], "Blackout is not as the last of its wishes said\n" + said)
        if self.sliders:
            self.assertEqual(api.mix["opacity"], self.sliders[-1], "the opacity is not as the last of its wishes said\n" + said)
        # a wish for the level that came after every tap stands: the last of them was a Fade out, so the picture is
        # faded out, unless something was tapped after it AND LOADED (the order is the fader's own count). A tap that
        # was overtaken, or a rotation's tick that the player refused, loads nothing and brings nothing back.
        real_taps = [token for token, index in self.taps if index is not None and (
            index in loaded if chosen[index] in self.PLAYS else (chosen[index] in self.TAPS and index in claims))]
        if self.wishes_for_level:
            token, kind = max(self.wishes_for_level)
            if kind == "fade out" and not any(tap >= token for tap in real_taps):
                self.assertEqual(api.fader.label, "out", "a Fade out that was asked for after every tap was undone by something that loaded\n" + said)
        # the level: what the mix says, and never lit under a label that says dark, also on a player that restarted
        dark = api.mix["blackout"] or api.fader.label == "out"
        want = 0 if dark else int(round(api.mix["opacity"] * 2.55))
        self.assertEqual(self.brightness(), int(round(-100 * (1 - want / 255.0))),
                         "the picture's brightness is %s and the mix asks for level %d of 255 (blackout %s, the fader's label %s, opacity %s)\n%s"
                         % (self.brightness(), want, api.mix["blackout"], api.fader.label, api.mix["opacity"], said))
        # the live input's helper runs exactly when the pipe is what mpv itself has loaded, and never two; and the
        # player's own word for it (which the panel goes by) is the truth
        piped = (mpv.path or "").endswith(".fifo")
        self.assertLessEqual(self.helper.most, 1, "two helpers of a live input at once\n" + said)
        self.assertEqual(self.helper.running, 1 if piped else 0, "the live input's helper and what mpv plays disagree\n" + said)
        self.assertEqual(player.pipe_playing, piped, "the player's word for whether the pipe plays is not what mpv plays\n" + said)

    def replay(self, seed, number, times=20):
        """Run one round again, `times` times: its actions and delays are the same, the interleaving is not."""
        for _ in range(times):
            self.one_round(seed, number)

    def test_random_orders_of_everything_an_operator_can_do(self):
        began = self.time.monotonic()
        for seed in self.SEEDS:
            for i in range(self.ROUNDS):
                self.one_round(seed, i)
        print("stress: %d rounds in %.1f s; %d overlays drawn at a size of their own, %d stills of the clip alone (D89)"
              % (len(self.SEEDS) * self.ROUNDS, self.time.monotonic() - began, self.mpv.counts["scaled"], self.mpv.counts["video stills"]))
        self.assertGreater(self.mpv.counts["video stills"], 8, "the rounds hardly ever took a still of the clip: they prove nothing of D89")
        self.assertGreater(self.mpv.counts["scaled"], 100, "the rounds hardly ever drew a scaled still: they prove nothing of D89")

    def test_with_an_effect_beside_everything_an_operator_can_do(self):
        """The same rounds with one or two actions of an effect added to each (on, off, the next one), and one more
        thing that must hold in them: the player is never given the effect before the generator (setUp's look at
        every shader list). An effect is no wish and sets no level, so everything else is held as in the first test,
        a give-up on transitions for any reason included. It leaves no worker behind for whatever runs next."""
        self.addCleanup(self.api.effects.changer.clear)
        self.addCleanup(self.api.effects.off)
        began = self.time.monotonic()
        for seed in self.SEEDS:
            for i in range(self.ROUNDS):
                self.one_round(seed, i, effects=True)
        print("stress with an effect: %d rounds in %.1f s; an effect went on %d times, the player was given a generator and an effect "
              "together %d times" % (len(self.SEEDS) * self.ROUNDS, self.time.monotonic() - began, self.effects_on, self.pairs))
        self.assertGreater(self.effects_on, 40, "the effect's actions put next to nothing on: they prove nothing")
        self.assertGreater(self.pairs, 8, "a generator and an effect were hardly ever on together")


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


# -- D89: a smaller still and scaled steps, where the player draws an overlay at a size of its own --

def png_of(rows, bpp=3):
    """A PNG of these rows of pixels (each a tuple of three or four bytes), with plain rows."""
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    head = struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2 if bpp == 3 else 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


class ReadHalf(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "s.png")

    def put(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def test_a_still_above_the_bound_comes_out_at_half_its_size_every_other_pixel_of_every_other_row(self):
        for bpp in (3, 4):
            for w, h in ((5, 3), (6, 4), (1, 1), (7, 2)):
                rows = [[(x, y, 100 + x + 10 * y) + ((9,) if bpp == 4 else ()) for x in range(w)] for y in range(h)]
                self.put(png_of(rows, bpp))
                ow, oh, out = T.read_still(self.path, (w, h), half=0)
                self.assertEqual((ow, oh), ((w + 1) // 2, (h + 1) // 2), (w, h))
                want = b"".join(bytes([100 + x + 10 * y, y, x, 255]) for y in range(0, h, 2) for x in range(0, w, 2))
                self.assertEqual(bytes(out), want, "%d x %d, %d bytes a pixel" % (w, h, bpp))

    def test_at_or_under_the_bound_it_is_read_whole(self):
        rows = [[(x, y, 7) for x in range(4)] for y in range(2)]
        self.put(png_of(rows))
        self.assertEqual(T.read_still(self.path, (4, 2), half=8)[:2], (4, 2))
        self.assertEqual(T.read_still(self.path, (4, 2), half=7)[:2], (2, 1))
        self.assertEqual(T.read_still(self.path, (4, 2), half=None)[:2], (4, 2))

    def test_a_still_of_the_clip_may_be_any_size_that_fits_the_screen_and_no_larger(self):
        self.put(png(3, 2, (1, 2, 3)))
        self.assertEqual(T.read_still(self.path, (8, 4), within=True)[:2], (3, 2))
        self.assertEqual(T.read_still(self.path, (3, 2), within=True)[:2], (3, 2))
        for bound in ((2, 4), (8, 1)):
            with self.assertRaises(T.StillError):
                T.read_still(self.path, bound, within=True)
        with self.assertRaises(T.StillError, msg="without `within` the size is exact, as before"):
            T.read_still(self.path, (8, 4))

    def test_halving_holds_less_than_reading_whole(self):
        import tracemalloc
        w, h = 640, 360
        raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
        self.put(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b""))
        tracemalloc.start()
        try:
            got = T.read_still(self.path, (w, h), half=0)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(len(got[2]), w * h)                     # a quarter of the picture's four bytes a pixel
        self.assertLess(peak, 2.5 * w * h * 4)


class ScaledScreen(Screen):
    """The fake screen of a player that draws an overlay at a size of its own (mpv 0.38 on, D89), or that refuses
    to (`can_scale` False, as mpv 0.37). It notes where every overlay lands on the screen and at what size."""

    def __init__(self, rundir):
        super().__init__(rundir)
        self.can_scale = True
        self.probe_fails = False        # the question is not answered
        self.clip_place = None          # what video_place answers: ((x, y, w, h) on the screen, (w, h) of the clip)
        self.clip_still = None          # the size of a still of the clip alone
        self.kinds = []                 # the kind of every still taken
        self.shown = []                 # (x, y, width, height) on the screen of every overlay, and the size of its pixels
        self.video_refused = False      # a still of the clip is refused ("mpv: error running command")
        self.probe_oserror = False      # the question's pixel cannot be written

    def overlay_scales(self, oid):
        self.calls.append(("overlay_scales", oid))
        if self.probe_oserror:
            raise OSError(28, "No space left on device")
        if self.probe_fails:
            raise PlayerError("no reply from mpv")
        assert not self.overlay_up, "asked with a still of ours on the screen: the pixel would take its place"
        return self.can_scale

    def video_place(self, screen):
        self.calls.append(("video_place",))
        return self.clip_place

    def still(self, path, kind="window"):
        self.kinds.append(kind)
        if kind == "video":
            self.calls.append(("still", os.path.basename(path)))
            if self.video_refused:
                raise PlayerError("mpv: error running command")
            with open(path, "wb") as f:
                f.write(png(self.clip_still[0], self.clip_still[1], self.colour))
            return
        super().still(path)

    def overlay(self, oid, x, y, w, h, pixels, dw=None, dh=None):
        if dw is not None and not self.can_scale:
            raise PlayerError("mpv: invalid parameter")
        super().overlay(oid, x, y, w, h, pixels)
        self.calls[-1] = self.calls[-1] + (dw, dh)
        self.shown.append(((x, y, dw if dw is not None else w, dh if dh is not None else h), (w, h)))

    def overlay_part(self, oid, path, x, y, offset, w, h, stride, dw=None, dh=None):
        if dw is not None and not self.can_scale:
            raise PlayerError("mpv: invalid parameter")
        super().overlay_part(oid, path, x, y, offset, w, h, stride)
        self.calls[-1] = self.calls[-1] + (dw, dh)
        self.shown.append(((x, y, dw if dw is not None else w, dh if dh is not None else h), (w, h)))


class Scaled(Base):
    def setUp(self):
        super().setUp()
        self.player = self.api.player = ScaledScreen(self.rundir)
        self.player.running = True
        half = T.HALF_WINDOW
        T.HALF_WINDOW = 0                       # the screen of these tests is small: halve it as a large one is
        self.addCleanup(setattr, T, "HALF_WINDOW", half)

    def overlays(self):
        return [c for c in self.player.calls if c[0] == "overlay"]

    def test_a_still_of_the_window_is_read_at_half_its_size_and_every_step_drawn_at_the_full_size(self):
        self.assertEqual(self.play(), {"playing": "a.mp4"})
        names = self.names()
        self.assertLess(names.index("overlay_scales"), names.index("pause"), "the player was asked while the clip stood still")
        drawn = self.overlays()
        self.assertEqual(len(drawn), 21)
        self.assertEqual(set(c[1:] for c in drawn), {(T.OVERLAY_ID, 0, 0, 4, 2, 8, 4)}, "a quarter of the pixels, drawn over the whole screen")
        self.assertEqual(len(self.player.pixels), 4 * 2 * 4)
        self.assertEqual((self.tr.last["still"], self.tr.last["still_size"], self.tr.last["drawn"]), ("window", [4, 2], [0, 0, 8, 4]))
        self.assertEqual(self.tr.last["ended"], "done")
        self.assertEqual(self.left(), [])

    def test_the_status_says_what_kind_of_still_was_taken_and_at_what_size(self):
        self.assertNotIn("last", self.api.status({}, None, "t")["mix"], "nothing to say before the first transition")
        self.play()
        last = self.api.status({}, None, "t")["mix"]["last"]
        self.assertEqual((last["still"], last["still_size"], last["drawn"], last["ended"]), ("window", [4, 2], [0, 0, 8, 4], "done"))
        self.assertIn("still_ms", last)

    def test_a_small_screen_is_not_halved_and_gets_the_commands_of_before(self):
        T.HALF_WINDOW = 8 * 4
        self.play()
        self.assertEqual(set(c[1:] for c in self.overlays()), {(T.OVERLAY_ID, 0, 0, 8, 4, None, None)})
        self.assertEqual(self.tr.last["still_size"], [8, 4])

    def test_a_player_that_cannot_scale_gets_the_commands_of_before_and_is_asked_once(self):
        self.player.can_scale = False
        self.play()
        self.play("b.mov")
        self.assertEqual(set(c[1:] for c in self.overlays()), {(T.OVERLAY_ID, 0, 0, 8, 4, None, None)}, "a still was scaled on a player that cannot")
        self.assertEqual(self.names().count("overlay_scales"), 1, "asked again within one player process")
        said = [line for line in self.lines if "cannot draw an overlay at another size" in line]
        self.assertEqual(len(said), 1, self.lines)
        self.assertEqual((self.tr.given_up, self.tr.last["ended"], self.tr.last["still"]), ("", "done", "window"))
        # another player process is asked again, and said so of again
        self.player.props["pid"] = 4243
        self.play()
        self.assertEqual(self.names().count("overlay_scales"), 2)
        self.assertEqual(len([line for line in self.lines if "cannot draw" in line]), 2)

    def test_a_player_that_can_is_asked_once_and_says_nothing(self):
        self.play()
        self.play("b.mov")
        self.assertEqual(self.names().count("overlay_scales"), 1)
        self.assertFalse([line for line in self.lines if "cannot draw" in line], self.lines)

    def test_a_question_that_is_not_answered_is_the_full_size_this_once_and_asked_again(self):
        self.player.probe_fails = True
        self.play()
        self.assertEqual(set(c[1:] for c in self.overlays()), {(T.OVERLAY_ID, 0, 0, 8, 4, None, None)})
        self.assertEqual(self.tr.given_up, "", "a question that is not answered is no reason to give up")
        self.player.probe_fails = False
        del self.player.calls[:]
        self.play("b.mov")
        self.assertEqual(self.names().count("overlay_scales"), 1)
        self.assertEqual(set(c[1:] for c in self.overlays()), {(T.OVERLAY_ID, 0, 0, 4, 2, 8, 4)})

    def test_with_a_still_on_the_screen_the_player_is_not_asked(self):
        # a second play during a transition, on a player not asked yet (here: its answer forgotten in between):
        # asking would put the pixel in place of the still that is on the screen
        def again():
            self.tr._scaling = None
            self.play("b.mov")
        self.hook = again
        self.play()
        self.assertEqual(self.names().count("overlay_scales"), 1, "asked with the first still on the screen")
        self.assertEqual(self.names().count("still"), 2)
        self.assertEqual(self.tr.last["still_size"], [8, 4], "the second still is the full-size way, this once")
        self.assertEqual(self.player.levels[-1], None)
        self.assertEqual(self.left(), [])

    def test_a_still_of_the_clip_is_laid_over_the_clips_place(self):
        # a clip of 4 x 1 shown at 8 x 2 in the middle of a screen of 8 x 4
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (4, 1)
        self.play()
        self.assertEqual(self.player.kinds, ["video"])
        self.assertEqual(set(c[1:] for c in self.overlays()), {(T.OVERLAY_ID, 0, 1, 4, 1, 8, 2)})
        self.assertEqual((self.tr.last["still"], self.tr.last["still_size"], self.tr.last["drawn"]), ("video", [4, 1], [0, 1, 8, 2]))
        self.assertEqual((self.tr.last["ended"], self.tr.given_up), ("done", ""))
        self.assertEqual(self.left(), [])

    def test_with_the_picture_over_the_video_on_the_still_is_of_the_window(self):
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (4, 1)
        self.settings.data["overlay"] = dict(self.settings.data["overlay"], on=True, file="logo.png")
        self.play()
        self.assertEqual(self.player.kinds, ["window"], "a still of the clip would leave the logo out")
        self.assertNotIn("video_place", self.names())
        self.assertEqual(self.tr.last["drawn"], [0, 0, 8, 4])

    def test_a_still_of_the_clip_of_another_shape_than_its_place_is_a_cut_and_twice_makes_it_the_window(self):
        # the clip may have changed hands between the look and the screenshot (a generator's carrier is loaded
        # before the player counts the change, which the stress test found): once is a cut and no give-up
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (4, 4)
        self.assertEqual(self.play(), {"playing": "a.mp4"})
        self.assertNotIn("overlay", self.names())
        self.assertIn("play", self.names())
        self.assertFalse(self.player.props["pause"])
        self.assertEqual(self.tr.given_up, "")
        self.assertTrue(any("the still of the clip is 4 x 4" in line for line in self.lines), self.lines)
        self.assertEqual(self.left(), [])
        # twice in a row, and this player process gets stills of the window, which work everywhere
        self.play("b.mov")
        self.assertEqual(self.tr.given_up, "")
        self.assertTrue(any("stills of the window from now on" in line for line in self.lines), self.lines)
        del self.player.calls[:]
        self.play()
        self.assertEqual(self.player.kinds, ["video", "video", "window"])
        self.assertEqual((self.tr.last["still"], self.tr.last["ended"]), ("window", "done"))
        # a new player process tries the clip's own picture again
        self.player.props["pid"] = 4243
        self.player.clip_still = (4, 1)
        self.play("b.mov")
        self.assertEqual((self.tr.last["still"], self.tr.last["ended"]), ("video", "done"))

    def test_a_still_of_the_clip_of_the_right_shape_and_the_wrong_size_is_a_miss(self):
        # the review of #138, L3: the size is held, not only the shape
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (8, 2)
        self.play()
        self.assertNotIn("overlay", self.names())
        self.assertTrue(any("not the 4 x 1 the player said" in line for line in self.lines), self.lines)
        self.assertEqual((self.tr.given_up, self.tr._misses), ("", 1))
        # and the shape: the size the player said, in a place of another shape
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 4)), (4, 4)
        self.play("b.mov")
        self.assertTrue(any("not the shape of its place" in line for line in self.lines), self.lines)
        self.assertEqual(self.tr.given_up, "")

    def test_a_still_of_the_clip_the_player_refuses_is_a_cut_and_never_a_give_up(self):
        # the review of #138, M2: "mpv: error running command" on the clip's kind of screenshot
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (4, 1)
        self.player.video_refused = True
        self.assertEqual(self.play(), {"playing": "a.mp4"})
        self.assertEqual(self.tr.given_up, "")
        self.assertFalse(self.player.props["pause"])
        self.assertIn("play", self.names())
        self.play("b.mov")
        self.assertEqual(self.tr.given_up, "")
        self.play()
        self.assertEqual(self.player.kinds, ["video", "video", "window"], "two refusals in a row and the player gets the window's still")
        self.assertEqual((self.tr.last["still"], self.tr.last["ended"], self.tr.given_up), ("window", "done", ""))
        self.assertEqual(self.left(), [])

    def test_a_question_whose_pixel_cannot_be_written_is_the_full_size_this_once(self):
        # the review of #138, L5: an OSError writing overlay-63.bgra is "cannot tell", not a failed still
        self.player.probe_oserror = True
        self.play()
        self.assertEqual(self.tr.given_up, "")
        self.assertEqual((self.tr.last["still_size"], self.tr.last["ended"]), ([8, 4], "done"))
        self.player.probe_oserror = False
        self.play("b.mov")
        self.assertEqual(self.names().count("overlay_scales"), 2, "not asked again")
        self.assertEqual(self.tr.last["still_size"], [4, 2])

    def test_a_wipe_or_a_slide_over_a_clip_with_bars_takes_the_window(self):
        # the review of #138, M1: a still of the clip would be moved inside the clip's place and stop at the bar's
        # edge, with the new clip already beside it; over the whole screen, the window's still moves as it should
        self.player.clip_place, self.player.clip_still = ((2, 0, 4, 4), (2, 2)), (2, 2)       # pillarboxed
        for name in T.WIPES + T.SLIDES:
            self.settings.data["mix"] = T.stored(name, 1.0)
            del self.player.kinds[:], self.player.shown[:]
            self.now[0] = 50.0
            self.play()
            self.assertEqual(self.player.kinds, ["window"], name)
            self.assertEqual(self.player.shown[0][0], (0, 0, 8, 4), "%s: the still does not cover the screen" % name)
            self.assertEqual(self.tr.last["ended"], "done", name)
        self.player.clip_place = ((0, 1, 8, 2), (4, 1))                                          # letterboxed
        self.player.clip_still = (4, 1)
        for name in ("wipe-from-top", "slide-down"):
            self.settings.data["mix"] = T.stored(name, 1.0)
            del self.player.kinds[:]
            self.play()
            self.assertEqual(self.player.kinds, ["window"], name)
        # a crossfade over the same place fades where the clip was, which is right: the clip's own still
        self.settings.data["mix"] = T.stored("crossfade", 1.0)
        del self.player.kinds[:]
        self.play()
        self.assertEqual(self.player.kinds, ["video"])
        # and a move over a clip that fills the screen keeps the clip's own still
        self.player.clip_place, self.player.clip_still = ((0, 0, 8, 4), (4, 2)), (4, 2)
        self.settings.data["mix"] = T.stored("slide-left", 1.0)
        del self.player.kinds[:], self.player.shown[:]
        self.play()
        self.assertEqual(self.player.kinds, ["video"])
        self.assertEqual(self.player.shown[0][0], (0, 0, 8, 4))

    def test_one_still_of_the_clip_that_misses_between_good_ones_does_not_count_up(self):
        self.player.clip_place, self.player.clip_still = ((0, 1, 8, 2), (4, 1)), (4, 4)
        self.play()
        self.player.clip_still = (4, 1)
        self.play("b.mov")
        self.player.clip_still = (4, 4)
        self.play()
        self.player.clip_still = (4, 1)
        self.play("b.mov")
        self.assertEqual(self.player.kinds, ["video"] * 4)
        self.assertEqual(self.tr.last["still"], "video")
        self.assertEqual(self.tr.given_up, "")

    def test_a_wipe_and_a_slide_on_a_halved_still_keep_their_edges_where_they_belong(self):
        self.player.screen = (40, 20)
        for name in T.WIPES + T.SLIDES:
            self.settings.data["mix"] = T.stored(name, 1.0)
            del self.player.shown[:], self.player.calls[:]
            self.now[0] = 50.0
            sizes = []
            self.player.on_overlay = lambda: sizes.append(os.path.getsize(os.path.join(self.rundir, "transition-%d.bgra" % os.getpid())))
            self.play()
            self.assertEqual(self.tr.last["ended"], "done", name)
            self.assertEqual(set(sizes), {20 * 10 * 4 + 20 * 4}, "%s: the file is the halved still and one row more" % name)
            parts = [c for c in self.player.calls if c[0] == "overlay_part"]
            self.assertEqual(parts[0][1:], (T.OVERLAY_ID, 0, 0, 0, 20, 10, 80, 40, 20), name)
            blend = T.BLENDS[name]()
            across = blend.cut in ("left", "right")
            for (sx, sy, sw, sh), (pw, ph) in self.player.shown:
                self.assertTrue(0 <= sx and sx + sw <= 40 and 0 <= sy and sy + sh <= 20, "%s draws outside the screen: %s" % (name, (sx, sy, sw, sh)))
                self.assertEqual((sw, sh), (2 * pw, 2 * ph), "%s: a part is not drawn at twice its size" % name)
                near, far = (sx, sx + sw) if across else (sy, sy + sh)
                full = 40 if across else 20
                kept = 0 if blend.shift else (full if blend.cut in ("left", "top") else 0)
                if blend.shift:
                    self.assertEqual(near if blend.cut in ("left", "top") else far, 0 if blend.cut in ("left", "top") else full, "%s: the slide does not leave from its side" % name)
                else:
                    self.assertEqual(far if blend.cut in ("left", "top") else near, kept, "%s: the edge that stays moved" % name)
            self.assertEqual(self.left(), [], name)


class ScaledRealPlayer(ServerBase):
    """The real Player over the stand-in for mpv, which scales an overlay or refuses to (D89)."""

    def setUp(self):
        super().setUp()
        from pvj.player import Player
        from tests.fakempv import FakeMpv
        self.mpv = FakeMpv(os.path.join(self.rundir, "player.sock"))
        self.addCleanup(self.mpv.stop)
        self.player = self.api.player = Player(rundir=self.rundir)
        self.api.log = lambda line: None
        self.lines = []
        self.api.transitions.log = self.lines.append
        self.api.transitions._thread = False
        self.clip = os.path.join(self.media, "a.mp4")
        half = T.HALF_WINDOW
        T.HALF_WINDOW = 0
        self.addCleanup(setattr, T, "HALF_WINDOW", half)

    def left(self):
        return [n for n in os.listdir(self.rundir) if n.startswith("transition-") or n.startswith("overlay-")]

    def test_the_player_asks_with_one_pixel_and_takes_it_off_either_way(self):
        for scales in (True, False):
            self.mpv.scales = scales
            self.assertIs(self.player.overlay_scales(T.OVERLAY_ID), scales)
            self.assertEqual(self.mpv.overlays, {})
            self.assertEqual(self.left(), [])

    def test_where_the_clips_own_picture_is_the_screen(self):
        self.mpv.size = (8, 4)
        self.player.play([self.clip], spawn=False)
        for video, want in (((4, 2), ((0, 0, 8, 4), (4, 2))), ((2, 2), ((2, 0, 4, 4), (2, 2))), ((4, 1), ((0, 1, 8, 2), (4, 1))),
                            ((8, 4), None), ((16, 8), None)):
            self.mpv.video = video
            self.assertEqual(self.player.video_place((8, 4)), want, video)
        self.mpv.video = (4, 2)
        for prop, value in (("video-zoom", 0.5), ("video-pan-x", 0.1), ("video-pan-y", -0.1), ("video-rotate", 90)):
            was = self.mpv.props[prop]
            self.mpv.props[prop] = value
            self.assertIsNone(self.player.video_place((8, 4)), "%s %s" % (prop, value))
            self.mpv.props[prop] = was
        self.mpv.props["video-zoom"] = -0.5             # smaller than fits: still all of it on the screen
        self.assertIsNotNone(self.player.video_place((8, 4)))
        self.mpv.props["video-zoom"] = 0.0
        self.assertIsNone(self.player.video_place((16, 8)), "the player's screen is another one")
        self.player.set_mapping_mode(True)
        self.assertIsNone(self.player.video_place((8, 4)), "the mapping is not in a still of the clip")
        self.player.set_mapping_mode(False)
        self.assertIsNotNone(self.player.video_place((8, 4)))

    def test_no_still_of_the_clip_over_a_generator(self):
        self.mpv.size, self.mpv.video = (8, 4), (4, 2)
        self.api.registry.set_enabled("shaders", True)
        self.api.shaders.log = lambda *_: None
        self.assertTrue(self.api.shaders.show(self.api.shaders.library()[0]["id"])["ok"])
        self.assertIsNone(self.player.video_place((8, 4)))

    def go(self):
        self.player.play([self.clip], spawn=False)
        self.settings.data["mix"] = T.stored("crossfade", 0.3)
        open(os.path.join(self.media, "b.mp4"), "w").close()
        self.assertEqual(self.api.play({"file": "b.mp4"}, None, "t"), {"playing": "b.mp4"})
        shots = [c for c in self.mpv.commands if c == "screenshot-to-file"]
        self.assertEqual(len(shots), 1)
        self.assertEqual(self.mpv.overlays, {})
        self.assertEqual(self.left(), [])
        self.assertEqual((self.api.transitions.last["ended"], self.api.transitions.given_up), ("done", ""))
        return self.api.transitions.last

    def test_a_crossfade_on_an_mpv_that_scales_takes_the_clips_own_picture(self):
        self.mpv.scales, self.mpv.size, self.mpv.video = True, (8, 4), (4, 2)
        last = self.go()
        self.assertEqual((last["still"], last["still_size"], last["drawn"]), ("video", [4, 2], [0, 0, 8, 4]))

    def test_a_crossfade_on_an_mpv_that_scales_with_a_clip_as_large_as_the_screen_halves_the_window(self):
        self.mpv.scales, self.mpv.size, self.mpv.video = True, (8, 4), (8, 4)
        last = self.go()
        self.assertEqual((last["still"], last["still_size"], last["drawn"]), ("window", [4, 2], [0, 0, 8, 4]))

    def test_a_crossfade_on_an_mpv_that_cannot_scale_is_as_before(self):
        self.mpv.scales, self.mpv.size, self.mpv.video = False, (8, 4), (4, 2)
        last = self.go()
        self.assertEqual((last["still"], last["still_size"], last["drawn"]), ("window", [8, 4], [0, 0, 8, 4]))
        self.assertEqual(len([line for line in self.lines if "cannot draw an overlay" in line]), 1)


if __name__ == "__main__":
    unittest.main()


class ReadStillSizeTest(unittest.TestCase):
    """A still of the wrong size is a cut that once (D108's review of the fault as shipped: the window still fell to
    libmpv's 960 x 540 between two 1080p pictures and gave transitions up for five minutes)."""

    def test_the_wrong_size_is_its_own_refusal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.png")
            with open(path, "wb") as f:
                f.write(png(4, 2, b"\x10\x20\x30"))
            with self.assertRaises(T.WrongSize):
                T.read_still(path, (8, 4))
            with open(path, "wb") as f:
                f.write(b"nothing of the kind")
            with self.assertRaises(T.StillError) as e:
                T.read_still(path, (8, 4))
            self.assertNotIsInstance(e.exception, T.WrongSize)


class WrongSizeStillTest(Base):
    def test_a_still_of_the_wrong_size_is_a_cut_and_gives_nothing_up(self):
        real = self.player.still

        def small(path):
            real(path)
            with open(path, "wb") as f:
                f.write(png(1, 1, b"\x10\x20\x30"))
        self.player.still = small
        self.assertEqual(self.tr.hold("wipe-from-left"), 0, "a cut this once")
        self.assertEqual(self.tr.given_up, "", "nothing about the box in one still's size")
        self.assertEqual(self.tr.fallback(), "")
        self.player.still = real
        self.assertTrue(self.tr.hold("wipe-from-left"), "the next one is a wipe")

    def wrong_still(self):
        real = self.player.still

        def small(path):
            real(path)
            with open(path, "wb") as f:
                f.write(png(1, 1, b"\x10\x20\x30"))
        self.player.still = small
        self.addCleanup(setattr, self.player, "still", real)
        return real

    def test_stills_of_the_wrong_size_in_a_row_give_up_with_a_reason(self):
        self.wrong_still()
        for n in range(T.SLOW_IN_A_ROW):
            self.assertEqual(self.tr.given_up, "", n)
            self.assertEqual(self.tr.hold("wipe-from-left"), 0)
        self.assertIn("%d stills in a row were not of the screen's size" % T.SLOW_IN_A_ROW, self.tr.fallback())
        self.assertIn("were not of the screen's size", self.api.status({}, None, "t")["mix"]["fallback"])

    def test_a_good_still_clears_the_count(self):
        real = self.wrong_still()
        small = self.player.still
        for _ in range(3):
            self.player.still = small
            self.assertEqual(self.tr.hold("wipe-from-left"), 0)
            self.assertEqual(self.tr.given_up, "")
            self.player.still = real
            self.assertTrue(self.tr.hold("wipe-from-left"))
            self.tr.end()
        self.assertEqual(self.tr.given_up, "")
