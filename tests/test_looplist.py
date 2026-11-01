# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The loop lists of plain clips (D118): the real Player and the real Lists against the stand-in for mpv on the socket.
tests/test_pvj_render.py runs the same against the real renderer; what only a node shows is in tools/DEVICE-TESTING.md."""
import os
import re
import stat
import tempfile
import threading
import time
import unittest

from pvj import looplist, renderer, settings as settings_mod
from pvj.player import Ipc, Player, PlayerError, expand_media, names_other_files
from pvj.sync import SyncManager
from tests.fakempv import FakeMpv
import tests        # the run's own temp folder and the locks' checker (tests/__init__.py)

MP4 = looplist.FORMATS[0]
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class ListMpv(FakeMpv):
    """The stand-in, with what lists need: a position that is set, a player that refuses lists, seeks written down."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.layers = True
        self.moves = True
        self.position = 0.5
        self.seeks = []
        self.refuse = False
        self.loads = []             # every loadfile, pvj-crossfade and pvj-source loadfile, whole
        self.sources_path = {}

    def _do(self, name, args):
        if name in ("loadfile", "pvj-crossfade"):
            self.loads.append((name,) + tuple(args))
            if self.refuse and args[0].endswith(".ffconcat"):
                raise ValueError("refused")
        if name == "seek":
            self.seeks.append(tuple(args))
        if name == "pvj-source":
            n, what = args[0], args[1]
            if what == "loadfile":
                self.loads.append((name,) + tuple(args))
                if self.refuse and args[2].endswith(".ffconcat"):
                    raise ValueError("refused")
                self.sources_path[n] = args[2]
                return None
            if what == "get_property":
                if args[2] == "path":
                    return self.sources_path.get(n)
                if args[2] == "time-pos":
                    return self.position
                if args[2] == "duration":
                    return 999.0
            return None
        return super()._do(name, args)

    def _get(self, prop):
        if prop == "time-pos" and self.path is not None:
            return self.position
        if prop == "duration" and self.path is not None:
            return 2000.0
        if prop == "percent-pos" and self.path is not None:
            return 7.0
        if prop == "playlist":
            return [{"filename": f} for f in self.playlist]
        if prop == "pvj-sources":
            rows = [{"source": 1, "idle": self.path is None, "path": self.path}]
            return rows + [{"source": n, "idle": False, "path": p} for n, p in sorted(self.sources_path.items())]
        if prop == "pvj-render":
            return dict(super()._get(prop), sources=4)
        return super()._get(prop)


class Base(unittest.TestCase):
    LENGTH = 2.0

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.media = os.path.join(self.dir, "media")
        os.mkdir(self.media)
        self.clip = self.make("a.mp4")
        self.on = True
        self.asked = []
        self.mpv = ListMpv(os.path.join(self.dir, "player.sock"))
        self.addCleanup(self.mpv.stop)
        self.player = Player(rundir=self.dir)
        self.lists = looplist.Lists(os.path.join(self.dir, "loops"), enabled=lambda: self.on, prober=self.prober)
        self.player.attach_lists(self.lists)

    def make(self, name, body=b"\x00\x00\x00\x18ftypisom" + b"\0" * 64):
        path = os.path.join(self.media, name)
        with open(path, "wb") as f:
            f.write(body)
        return path

    def prober(self, path, mpv_bin):
        self.asked.append((path, threading.current_thread() is threading.main_thread()))
        return self.LENGTH, MP4, "h264"

    def settle(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lists._lock:
                if self.lists._worker is None:
                    return
            time.sleep(0.005)
        self.fail("the worker did not end")

    def known(self, clip=None):
        """The clip's length, found: the plain load that asks, and its worker."""
        self.assertIsNone(self.lists.for_clip(clip or self.clip))
        self.settle()

    def listing(self, clip=None):
        self.known(clip)
        return self.lists.for_clip(clip or self.clip)

    def raw(self):
        return self.player.ipc.ask("get_property", "path")


class WhichLoadsTest(Base):
    def test_a_looping_clip_whose_length_is_known_loads_its_list_with_the_queue(self):
        self.mpv.layers = True
        self.known()
        self.player.play([self.clip], spawn=False)
        name, path, flag, index, opts = self.mpv.loads[-1]
        self.assertEqual((name, flag, index, opts), ("loadfile", "replace", -1, looplist.QUEUE))
        self.assertTrue(path.endswith(".ffconcat"))
        self.assertEqual(self.mpv.props["loop-file"], "inf", "at the end of the list one ordinary seam, and it goes on")
        self.assertEqual(self.mpv.props["keep-open"], "no")

    def test_the_list_is_what_the_design_says(self):
        path = self.listing()
        name = os.path.basename(path)[:-len(".ffconcat")]
        self.assertRegex(name, "^[0-9a-f]{32}$")
        with open(path) as f:
            lines = f.read().split("\n")
        self.assertEqual(lines[0], "ffconcat version 1.0")
        self.assertEqual(lines[1:3], ["file %s.mp4" % name, "duration 2.000000"])
        self.assertEqual(len(lines), 2 + 2 * looplist.PAIRS)
        link = os.path.join(os.path.dirname(path), name + ".mp4")
        self.assertTrue(stat.S_ISLNK(os.lstat(link).st_mode))
        self.assertEqual(os.readlink(link), self.clip)
        self.assertFalse([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")])

    def test_the_length_is_found_off_the_load_and_the_next_load_is_seamless(self):
        release = threading.Event()
        ask = self.prober

        def slow(path, mpv_bin):
            self.assertTrue(release.wait(5))
            return ask(path, mpv_bin)
        self.lists.prober = slow
        self.player.play([self.clip], spawn=False)
        self.assertEqual(self.mpv.loads[-1], ("loadfile", self.clip, "replace"), "plain today, at once, with the probe still running")
        release.set()
        self.settle()
        self.assertEqual(self.asked, [(self.clip, False)], "never on the caller's thread")
        self.player.play([self.clip], spawn=False)
        self.assertTrue(self.mpv.loads[-1][1].endswith(".ffconcat"))

    def test_what_never_takes_a_list(self):
        self.known()
        picture, song, other = self.make("p.png"), self.make("s.mp3"), self.make("b.mp4")
        for clip in (picture, song):
            self.known(clip)
        self.known(other)
        for files, ending, why in (([self.clip], "stop", "stop"), ([self.clip], "next", "next"), ([self.clip], "hold", "hold"),
                                   ([self.clip, other], "loop", "a list of clips"), ([picture], "loop", "a picture"), ([song], "loop", "sound"),
                                   (["srt://1.2.3.4:9000"], "loop", "a stream"), (["av://lavfi:color=c=black"], "loop", "a carrier")):
            self.mpv.loads.clear()
            self.player.play(files, ending=ending, spawn=False)
            self.assertFalse([c for c in self.mpv.loads if c[1].endswith(".ffconcat")], why)

    def test_a_live_input_and_the_shader_carrier_take_no_list(self):
        self.known()
        self.player.play_pipe(os.path.join(self.dir, "capture.fifo"), 640, 480, 30)
        self.player.play_source("/tmp/s.glsl", "av://lavfi:color=c=black:size=64x36:rate=30")
        self.assertFalse([c for c in self.mpv.loads if c[1].endswith(".ffconcat")])

    def test_a_clip_under_the_shortest_length_or_of_another_kind_stays_plain_for_good(self):
        for seconds, kind, video in ((0.19, MP4, "h264"), (5.0, "mpegts", "h264"), (5.0, "concat", "h264"), (5.0, MP4, None), (None, MP4, "h264")):
            lists = looplist.Lists(os.path.join(self.dir, "other"), prober=lambda p, b: (seconds, kind, video))
            self.assertIsNone(lists.for_clip(self.clip))
            deadline = time.monotonic() + 5
            while lists._worker is not None and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertIsNone(lists.for_clip(self.clip), (seconds, kind, video))
            self.assertEqual(list(lists._lengths.values()), [None])

    def test_classic_mpv_plays_as_it_always_did(self):
        self.known()
        self.mpv.layers = False
        self.player._layers_seen = None
        self.player.play([self.clip], spawn=False)
        self.assertEqual(self.mpv.loads[-1], ("loadfile", self.clip, "replace"))

    def test_sources_2_to_n_take_a_list_with_the_queue(self):
        self.known()
        self.player.is_running()
        self.player.play_to(2, [self.clip])
        name, n, what, path, flag, opts = self.mpv.loads[-1]
        self.assertTrue(path.endswith(".ffconcat"))
        self.assertEqual((n, what, flag), (2, "loadfile", "replace"))
        self.assertEqual(opts, "keep-open=no,image-display-duration=inf," + looplist.QUEUE)

    def test_a_crossfade_and_a_wipe_take_a_list_with_the_queue(self):
        self.known()
        other = self.make("b.mp4")
        self.player.play([other], spawn=False)
        self.settle()
        for style in ("crossfade", "wipe-left"):
            self.assertIsNone(self.player.play([self.clip], fade=1.0, style=style, spawn=False))
            load = self.mpv.loads[-1]
            self.assertEqual(load[0], "pvj-crossfade")
            self.assertTrue(load[1].endswith(".ffconcat"))
            self.assertEqual(load[3], "keep-open=no,image-display-duration=inf," + looplist.QUEUE)
            self.assertEqual(load[4:], () if style == "crossfade" else (style,))
            self.assertEqual(self.mpv.props["loop-file"], "inf")

    def test_a_crossfade_into_a_plain_clip_has_no_queue_in_its_options(self):
        self.player.play([self.make("b.mp4")], spawn=False)
        self.player.play([self.clip], fade=1.0, spawn=False)
        self.assertEqual(self.mpv.loads[-1][1:], (self.clip, 1.0, "keep-open=no,image-display-duration=inf"))


class FallbackTest(Base):
    def test_a_renderer_that_refuses_the_list_plays_the_clip(self):
        self.known()
        self.mpv.refuse = True
        self.player.play([self.clip], spawn=False)
        self.assertEqual(self.mpv.loads[-1], ("loadfile", self.clip, "replace"))
        self.assertEqual(self.mpv.path, self.clip)

    def test_a_crossfade_and_a_source_the_same(self):
        self.known()
        self.player.play([self.make("b.mp4")], spawn=False)
        self.mpv.refuse = True
        self.assertIsNone(self.player.play([self.clip], fade=1.0, spawn=False))
        self.assertEqual(self.mpv.loads[-1][:2], ("pvj-crossfade", self.clip))
        self.player.play_to(2, [self.clip])
        self.assertEqual(self.mpv.sources_path[2], self.clip)

    def test_a_folder_that_cannot_be_written_is_a_plain_load(self):
        self.known()
        with open(self.lists.folder, "w"):
            pass
        self.assertIsNone(self.lists.for_clip(self.clip))
        self.player.play([self.clip], spawn=False)
        self.assertEqual(self.mpv.loads[-1], ("loadfile", self.clip, "replace"))

    def test_a_clip_that_is_gone_a_probe_that_fails_and_a_changed_file(self):
        self.assertIsNone(self.lists.for_clip(os.path.join(self.media, "no.mp4")))
        self.assertIsNone(self.lists.for_clip(self.media), "a folder")
        calls = []

        def fails(path, mpv_bin):
            calls.append(path)
            raise OSError("busy")
        self.lists.prober = fails
        self.known()
        self.known()
        self.assertEqual(len(calls), 2, "a probe that failed says nothing: the next load asks again")
        self.lists.prober = self.prober
        self.known()
        self.assertIsNotNone(self.lists.for_clip(self.clip))
        with open(self.clip, "ab") as f:
            f.write(b"more")
        self.assertIsNone(self.lists.for_clip(self.clip), "another file: its length is not known")

    def test_the_kill_switch(self):
        self.known()
        self.on = False
        self.player.play([self.clip], spawn=False)
        self.assertEqual(self.mpv.loads[-1], ("loadfile", self.clip, "replace"))
        self.on = True
        self.player.play([self.clip], spawn=False)
        self.assertTrue(self.mpv.loads[-1][1].endswith(".ffconcat"))

    def test_a_queue_that_is_full_drops_the_ask_and_nothing_else(self):
        release = threading.Event()
        self.lists.prober = lambda p, b: release.wait(5) and (2.0, MP4, "h264")
        for i in range(looplist.WAITING + 5):
            self.assertIsNone(self.lists.for_clip(self.make("q%d.mp4" % i)))
        self.assertLessEqual(len(self.lists._queue), looplist.WAITING)
        release.set()
        self.settle()


class SettingsTest(unittest.TestCase):
    def test_on_by_default_read_with_defaults(self):
        self.assertTrue(renderer.loop_lists(None))
        self.assertTrue(renderer.loop_lists({}))
        self.assertTrue(renderer.loop_lists({"use": "new"}), "a file from before the key")
        self.assertTrue(renderer.loop_lists({"loop_lists": "no"}), "only False is off")
        self.assertFalse(renderer.loop_lists({"use": "new", "loop_lists": False}))

    def test_the_key_stays_out_of_a_file_that_never_had_it(self):
        self.assertNotIn("loop_lists", renderer.clean({"use": "new"}))
        self.assertNotIn("loop_lists", settings_mod.default_settings()["renderer"], "no schema change: nothing for a rollback to meet")
        self.assertEqual(settings_mod.default_settings()["schema"], settings_mod.SCHEMA)

    def test_a_change_of_renderer_keeps_the_switch_off(self):
        with open(os.path.join(REPO, "pvj", "renderer.py")) as f:
            source = f.read()
        self.assertIn('if before.get("loop_lists") is False:', source)
        self.assertEqual(renderer.clean({"use": "new", "loop_lists": False})["loop_lists"], False)


class TranslationTest(Base):
    def playing(self, at=4.5):
        self.known()
        self.player.play([self.clip], spawn=False)
        self.mpv.position = at

    def ask(self, prop):
        return self.player.ipc.request("get_property", prop)

    def test_everything_above_sees_the_clip(self):
        self.playing(4.5)
        self.assertTrue(self.raw().endswith(".ffconcat"))
        self.assertEqual(self.ask("path"), self.clip)
        self.assertEqual(self.ask("duration"), 2.0)
        self.assertAlmostEqual(self.ask("time-pos"), 0.5)
        self.assertAlmostEqual(self.ask("percent-pos"), 25.0)
        self.assertEqual(self.ask("playlist"), [{"filename": self.clip}])
        self.assertEqual(self.player.playlist_files(), [self.clip])
        self.assertEqual(self.ask("pvj-sources")[0]["path"], self.clip)
        self.assertEqual(self.player.sources_state()[0]["path"] if self.player.sources() > 1 else self.clip, self.clip)

    def test_the_status(self):
        self.playing(6.25)
        st = self.player.status()
        self.assertEqual((st["path"], st["duration"], st["position"], st["playlist_count"]), (self.clip, 2.0, 0.25, 1))
        self.assertEqual(st["loop_file"], "inf")

    def test_a_source(self):
        self.known()
        self.player.is_running()
        self.player.play_to(2, [self.clip])
        self.mpv.position = 5.0
        ask = self.player.ipc.request
        self.assertEqual(ask("pvj-source", 2, "get_property", "path"), self.clip)
        self.assertEqual(ask("pvj-source", 2, "get_property", "duration"), 2.0)
        self.assertAlmostEqual(ask("pvj-source", 2, "get_property", "time-pos"), 1.0)
        self.assertEqual(ask("get_property", "pvj-sources")[1]["path"], self.clip)

    def test_a_panel_that_started_again_reads_what_its_player_still_plays(self):
        self.playing(4.5)
        other = Player(rundir=self.dir)
        other.attach_lists(looplist.Lists(self.lists.folder, prober=self.prober))
        self.assertEqual(other.status()["path"], self.clip)
        self.assertEqual(other.status()["duration"], 2.0)
        self.assertAlmostEqual(other.status()["position"], 0.5)

    def test_a_plain_clip_is_left_alone_and_a_box_with_no_lists_is_asked_nothing_more(self):
        self.player.play([self.clip], spawn=False)
        self.mpv.commands.clear()
        self.assertEqual(self.ask("duration"), 2000.0)
        self.assertEqual(self.ask("time-pos"), 0.5)
        self.assertEqual(self.mpv.commands, ["get_property duration", "get_property time-pos"])

    def test_a_plain_clip_beside_a_list_keeps_its_own_numbers(self):
        self.known()
        self.player.play([self.clip], spawn=False)
        self.player.play([self.make("b.mp4")], spawn=False)
        self.assertEqual(self.ask("duration"), 2000.0)
        self.mpv.position = 7.5
        self.assertEqual(self.ask("time-pos"), 7.5)

    def test_nothing_loaded_stays_unavailable(self):
        self.known()
        self.player.play([self.clip], spawn=False)
        self.player.clear()
        self.assertEqual(self.player.status()["path"], None)
        with self.assertRaises(PlayerError):
            self.ask("time-pos")

    def test_a_file_of_the_same_look_elsewhere_is_not_a_list(self):
        name = "0" * 32 + ".ffconcat"
        self.assertIsNone(self.lists.view(os.path.join(self.media, name)))
        self.assertIsNone(self.lists.view(os.path.join(self.lists.folder, "x.ffconcat")))
        self.assertIsNone(self.lists.view(None))
        self.assertIsNone(self.lists.view("av://lavfi:color"))

    def test_a_list_that_lost_its_files_reads_as_what_the_player_says(self):
        self.playing(4.5)
        path = self.raw()
        os.unlink(path[:-len(".ffconcat")] + ".mp4")
        self.lists._views.clear()
        self.assertEqual(self.ask("path"), path, "said as it is; never a crash")

    def test_sync_acts_on_the_clip(self):
        """The server's messages: the file's name, the place inside the clip and the clip's length."""
        self.playing(4.5)

        class Api:
            player, mix, media_dir, usb_root = self.player, {}, self.media, None

        class Saved:
            data = {"sync": {"role": "server", "group": "main", "port": 5577, "wall": {"cols": 1, "rows": 1, "col": 0, "row": 0, "bezel": 0.0}}}
        out = SyncManager(Api, Saved).state()
        self.assertEqual((out["f"], out["p"], out["d"], out["lp"]), ("a.mp4", 0.5, 2.0, True))


class SeekTest(Base):
    def setUp(self):
        super().setUp()
        self.known()
        self.player.play([self.clip], spawn=False)
        self.mpv.position = 7.0          # the fourth pass of a 2 s clip

    def test_an_absolute_seek_lands_in_the_pass_that_plays(self):
        self.player.seek_to(0.5)
        self.assertEqual(self.mpv.seeks[-1], (6.5, "absolute"))

    def test_absolute_exact_is_one_string(self):
        self.player.ipc.request("seek", 1.5, "absolute+exact")
        self.assertEqual(self.mpv.seeks[-1], (7.5, "absolute+exact"))

    def test_past_the_clips_end_it_wraps_as_the_loop_does(self):
        self.player.seek_to(2.5)
        self.assertEqual(self.mpv.seeks[-1], (6.5, "absolute"))

    def test_a_relative_seek_is_a_distance_on_either_timeline(self):
        self.player.seek(-1.5)
        self.assertEqual(self.mpv.seeks[-1], (-1.5, "relative"))
        self.player.ipc.request("seek", 0, "relative+exact")
        self.assertEqual(self.mpv.seeks[-1], (0, "relative+exact"))

    def test_percent_is_of_the_clip(self):
        self.player.ipc.request("seek", 25, "absolute-percent")
        self.assertEqual(self.mpv.seeks[-1], (6.5, "absolute"))
        self.player.ipc.request("seek", 50, "relative-percent+exact")
        self.assertEqual(self.mpv.seeks[-1], (1.0, "relative+exact"))

    def test_a_plain_clip_is_not_touched(self):
        self.player.play([self.make("b.mp4")], spawn=False)
        self.player.seek_to(1.0)
        self.assertEqual(self.mpv.seeks[-1], (1.0, "absolute"))


class LoopTest(Base):
    def test_loop_off_ends_at_the_end_of_this_pass_and_loop_on_clears_it(self):
        self.known()
        self.player.play([self.clip], spawn=False)
        self.mpv.position = 7.0
        self.player.loop(False)
        self.assertEqual(self.mpv.props["end"], "7.999000")
        self.assertEqual(self.mpv.props["loop-file"], "no")
        self.player.loop(True)
        self.assertEqual(self.mpv.props["end"], "none")
        self.assertEqual(self.mpv.props["loop-file"], "inf")

    def test_the_boundary_is_the_demuxers_sum_of_lengths(self):
        self.LENGTH = 20.166667
        self.known()
        self.player.play([self.clip], spawn=False)
        self.mpv.position = 20.166667 * 500 + 1
        self.player.loop(False)
        self.assertEqual(self.mpv.props["end"], "%.6f" % (501 * 20166667 / 1e6 - 0.001))

    def test_a_plain_clip_stays_plain(self):
        self.player.play([self.clip], ending="stop", spawn=False)
        self.mpv.loads.clear()
        self.player.loop(True)
        self.assertEqual(self.mpv.props["loop-file"], "inf")
        self.assertNotIn("end", self.mpv.props)
        self.assertEqual(self.mpv.loads, [], "no reload in the middle of playing")
        self.player.loop(False)
        self.assertEqual(self.mpv.props["loop-file"], "no")
        self.assertNotIn("end", self.mpv.props)

    def test_a_list_of_clips_as_before(self):
        self.player.play([self.clip, self.make("b.mp4")], spawn=False)
        self.player.loop(False)
        self.assertEqual((self.mpv.props["loop-file"], self.mpv.props["loop-playlist"]), ("no", "no"))
        self.player.loop(True)
        self.assertEqual((self.mpv.props["loop-file"], self.mpv.props["loop-playlist"]), ("no", "inf"))

    def test_with_nothing_loaded(self):
        self.known()
        self.player.loop(True)
        self.assertEqual(self.mpv.props["loop-file"], "inf")


class PruneTest(Base):
    def setUp(self):
        super().setUp()
        self.mpv.layers = True
        self.old = time.time() - 3600

    def fill(self, n):
        """n lists on the folder, none of them new."""
        folder = self.lists.folder
        os.makedirs(folder, exist_ok=True)
        names = []
        for i in range(n):
            name = "%032x" % (i + 1)
            names.append(name)
            path = os.path.join(folder, name + ".ffconcat")
            with open(path, "w") as f:
                f.write("ffconcat version 1.0\nfile %s.mp4\nduration 2.000000\n" % name)
            os.symlink(self.clip, os.path.join(folder, name + ".mp4"))
            os.utime(path, (self.old + i, self.old + i))
        return names

    def there(self, name):
        return os.path.exists(os.path.join(self.lists.folder, name + ".ffconcat")) and os.path.lexists(os.path.join(self.lists.folder, name + ".mp4"))

    def test_only_names_no_layer_uses_go_and_the_folder_is_bound(self):
        names = self.fill(looplist.KEEP + 10)
        self.known()
        used = [os.path.join(self.lists.folder, names[0] + ".ffconcat"), os.path.join(self.lists.folder, names[1] + ".ffconcat")]
        path = self.lists.for_clip(self.clip, lambda: used)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(self.there(names[0]) and self.there(names[1]), "the oldest of all, and they play")
        left = {n.split(".")[0] for n in os.listdir(self.lists.folder)}
        self.assertLessEqual(len(left), looplist.KEEP + 1)

    def test_the_load_prunes_through_the_real_player_and_keeps_every_source(self):
        names = self.fill(looplist.KEEP + 10)
        self.known()
        self.mpv.path = os.path.join(self.lists.folder, names[0] + ".ffconcat")
        self.mpv.sources_path[2] = os.path.join(self.lists.folder, names[1] + ".ffconcat")
        self.mpv.sources_path[3] = os.path.join(self.lists.folder, names[2] + ".ffconcat")
        self.player.play([self.clip], spawn=False)
        for n in names[:3]:
            self.assertTrue(self.there(n), n)
        self.assertFalse(self.there(names[3]))

    def test_both_sides_of_a_crossfade(self):
        """The outgoing layer plays on, and the socket has stopped naming it the moment the crossfade began."""
        other = self.make("b.mp4")
        self.known(other)
        self.player.play([other], spawn=False)
        outgoing = self.raw()
        os.utime(outgoing, (self.old, self.old))
        self.known()
        self.player.play([self.clip], fade=2.0, spawn=False)
        incoming = self.raw()
        self.assertNotEqual(incoming, outgoing)
        self.fill(looplist.KEEP + 10)           # the folder is over its bound with a third load during the fade
        third = self.make("c.mp4")
        self.known(third)
        self.lists.for_clip(third, self.player._lists_playing)
        self.assertTrue(os.path.exists(incoming))
        self.assertTrue(os.path.exists(outgoing), "the name no question to the player gives")
        self.assertTrue(os.path.lexists(outgoing[:-len(".ffconcat")] + ".mp4"))

    def test_a_crossfade_from_a_clip_to_itself(self):
        self.known()
        self.player.play([self.clip], spawn=False)
        path = self.raw()
        self.player.play([self.clip], fade=2.0, spawn=False)
        self.assertEqual(self.raw(), path)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.listdir(self.lists.folder).count(os.path.basename(path)), 1)

    def test_a_list_rewritten_for_a_new_length_replaces_the_name_in_one_step(self):
        path = self.listing()
        before = os.stat(path).st_ino
        again = self.lists.for_clip(self.clip)
        self.assertEqual((again, os.stat(path).st_ino), (path, before), "the same list is left in place while it plays")
        with open(self.clip, "ab") as f:
            f.write(b"more")
        self.LENGTH = 3.0
        self.known()
        self.assertEqual(self.lists.for_clip(self.clip), path)
        self.assertNotEqual(os.stat(path).st_ino, before)
        self.assertEqual(self.lists.view(path).length, 3.0)

    def test_a_player_that_cannot_say_has_nothing_pruned(self):
        names = self.fill(looplist.KEEP + 10)
        self.known()

        def silent():
            raise PlayerError("no reply from mpv")
        self.assertIsNotNone(self.lists.for_clip(self.clip, silent))
        self.assertTrue(all(self.there(n) for n in names))

    def test_a_folder_at_its_bound_with_nothing_to_prune_is_a_plain_load(self):
        names = self.fill(looplist.MOST)
        self.known()
        used = [os.path.join(self.lists.folder, n + ".ffconcat") for n in names]
        self.assertIsNone(self.lists.for_clip(self.clip, lambda: used))
        self.assertTrue(all(self.there(n) for n in names))

    def test_leftovers_of_a_power_cut_go_too(self):
        names = self.fill(looplist.KEEP + 3)
        with open(os.path.join(self.lists.folder, names[0] + ".ffconcat.tmp"), "w"):
            pass
        self.known()
        self.lists.for_clip(self.clip, lambda: [])
        self.assertFalse(os.path.exists(os.path.join(self.lists.folder, names[0] + ".ffconcat.tmp")))


class RefusalTest(Base):
    BODY = b"ffconcat version 1.0\nfile n.mp4\n"

    def tag(self, n, flags=0):
        return b"ID3\x03\x00" + bytes([flags]) + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + b"\0" * n

    def test_the_forms_ffmpeg_follows(self):
        """Each of these was followed by mpv 0.40 on FFmpeg 7.1.5 on the node, but for the two tags: refused all the same."""
        for name, body in (("plain", self.BODY), ("crlf", self.BODY.replace(b"\n", b"\r\n")), ("pad", self.BODY + b"#" * 4096),
                           ("id3", self.tag(16) + self.BODY), ("two", self.tag(16) + self.tag(16) + self.BODY),
                           ("big", self.tag(200000) + self.BODY), ("footer", self.tag(16, 0x10) + b"\0" * 10 + self.BODY)):
            self.assertTrue(names_other_files(self.make("x-%s.mp4" % name, body)), name)

    def test_what_is_a_clip_or_not_a_list(self):
        for name, body in (("mp4", b"\0\0\0\x18ftypisom"), ("empty", b""), ("id3", self.tag(16) + b"\0\0\0\x18ftyp"), ("short", b"ID3"),
                           ("bom", b"\xef\xbb\xbf" + self.BODY), ("space", b"  " + self.BODY), ("newline", b"\n" + self.BODY)):
            self.assertFalse(names_other_files(self.make("c-%s.mp4" % name, body)), name)
        self.assertFalse(names_other_files(os.path.join(self.media, "gone.mp4")))

    def test_a_pipe_is_not_opened(self):
        fifo = os.path.join(self.dir, "x.fifo")
        os.mkfifo(fifo)
        self.assertFalse(names_other_files(fifo))

    def test_every_way_of_playing_refuses_it(self):
        bad = self.make("x.mp4", self.BODY)
        self.assertEqual(expand_media([bad]), [])
        self.assertEqual(expand_media([self.clip, bad]), [self.clip], "a list of clips goes on without it")
        self.assertEqual(expand_media([self.media]), [self.clip])
        with self.assertRaises(PlayerError):
            self.player.play([bad], spawn=False)
        self.player.is_running()
        with self.assertRaises(PlayerError):
            self.player.play_to(2, [bad])
        self.player.play([self.clip], ending="stop", spawn=False)
        with self.assertRaises(PlayerError):
            self.player.play([bad], fade=1.0, spawn=False)      # no crossfade, and the cut after it refuses it too
        self.assertFalse([c for c in self.mpv.loads if bad in c])

    def test_classic_refuses_it_too(self):
        self.mpv.layers = False
        self.player._layers_seen = None
        with self.assertRaises(PlayerError):
            self.player.play([self.make("x.mp4", self.BODY)], spawn=False)

    def test_urls_are_not_read(self):
        self.assertEqual(expand_media(["srt://1.2.3.4:9000", "av://lavfi:x"]), ["srt://1.2.3.4:9000", "av://lavfi:x"])


class RendererSourceTest(unittest.TestCase):
    """The socket's lists, read from the source as tests of the player's flags are."""

    def setUp(self):
        with open(os.path.join(REPO, "render", "src", "ipc.c")) as f:
            self.ipc = f.read()

    def listed(self, name):
        return re.search(r"const char \*const %s\[\] = \{(.*?)\};" % name, self.ipc, re.S).group(1)

    def test_the_queue_goes_with_the_load_only(self):
        for key in ("vd-queue-enable", "vd-queue-max-samples", "vd-queue-max-secs"):
            self.assertIn('"%s"' % key, self.listed("LOAD_KEYS"))
            self.assertIn('"%s"' % key, self.listed("FADE_KEYS"))
            self.assertIn('"%s"' % key, self.listed("QUEUE_KEYS"))
            self.assertNotIn('"%s"' % key, self.listed("SETTABLE"))
        for key, _ in (pair.split("=") for pair in looplist.QUEUE.split(",")):
            self.assertIn('"%s"' % key, self.listed("QUEUE_KEYS"))

    def test_the_values_are_plain(self):
        self.assertTrue(all(re.fullmatch(r"[a-z0-9.-]+", p.split("=")[1]) for p in looplist.QUEUE.split(",")))

    def test_end_is_settable_for_the_file_alone(self):
        self.assertIn('"end"', self.listed("SETTABLE"))
        self.assertIn('"file-local-options/end"', self.ipc)
        self.assertNotIn('"end"', self.listed("MIRRORED"))

    def test_d22_is_not_touched(self):
        """Nothing here may name the demuxer's options or the references."""
        for word in ("safe", "protocol_whitelist", "demuxer-lavf-o", "access-references"):
            for name in ("LOAD_KEYS", "FADE_KEYS", "SETTABLE"):
                self.assertNotIn('"%s"' % word, self.listed(name))
        from pvj.player import Player as P
        self.assertIn("--access-references=no", P(rundir=tempfile.mkdtemp()).mpv_command())


class IpcTest(unittest.TestCase):
    def test_a_plain_ipc_translates_nothing(self):
        self.assertIsNone(Ipc("/nonexistent").translate)


if __name__ == "__main__":
    unittest.main()
