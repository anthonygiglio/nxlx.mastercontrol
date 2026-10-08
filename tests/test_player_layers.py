# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The player's two shader layers (a shader source and the projection mapping) and its epoch, on the real Player
class with a fake mpv behind its IPC. Each test is a finding of the review of the Shaders and Vibes module."""
import os
import tempfile
import unittest

from pvj.player import Player, PlayerError

CARRIER = "av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0"


class FakeMpv:
    def __init__(self):
        self.props = {"pid": 100, "path": None, "glsl-shaders": [], "fbo-format": "auto", "keep-open": "yes", "brightness": 0}
        self.down = False
        self.refuse_load = False
        self.commands = []

    def restart(self):
        self.props.update({"pid": self.props["pid"] + 1, "path": None, "glsl-shaders": [], "fbo-format": "auto"})

    def request(self, *c):
        if self.down:
            raise PlayerError("player is not running")
        self.commands.append(c)
        if c[0] == "get_property":
            if c[1] == "path" and self.props["path"] is None:
                raise PlayerError("mpv: property unavailable")
            return self.props.get(c[1])
        if c[0] == "set_property":
            self.props[c[1]] = c[2]
        elif c[0] == "loadfile" and not self.refuse_load:
            self.props["path"] = c[1]
        elif c[0] == "stop":
            self.props["path"] = None
        return None


class LayersTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        os.chmod(d, 0o700)
        self.p = Player(rundir=d)
        self.mpv = self.p.ipc = FakeMpv()
        self.p.is_running = lambda: not self.mpv.down
        self.p._wait_for_path = lambda path, timeout=3.0: None

    def test_source_and_mapping_share_the_list_and_a_clip_removes_only_the_source(self):
        self.p.set_shaders(["/run/map.glsl"])
        self.p.set_mapping_mode(True)
        epoch = self.p.play_source("/run/s1.glsl", CARRIER)
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/s1.glsl", "/run/map.glsl"])
        self.p.set_shaders(["/run/map2.glsl"])                        # the mapper replaces its file: the source stays
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/s1.glsl", "/run/map2.glsl"])
        self.assertTrue(self.p.swap_source("/run/s2.glsl", epoch))
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/s2.glsl", "/run/map2.glsl"])
        self.p.play(["/media/a.mp4"], spawn=False)
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/map2.glsl"])
        self.assertEqual((self.mpv.props["fbo-format"], self.p.source_shader), ("rgba8", None))     # the mapping still wants 8 bits
        self.assertFalse(self.p.swap_source("/run/s3.glsl", epoch))   # an old epoch has no say any more
        self.assertIsNone(self.p.play_source("/run/s3.glsl", CARRIER, epoch))
        self.assertEqual(self.mpv.props["path"], "/media/a.mp4")

    def test_another_text_of_the_source_leaves_the_buffers_format_alone(self):
        """A generator's text is exchanged at every change of a value, and each `fbo-format` set made mpv set its
        renderer up anew. It is set when a source comes or goes, as an effect's is."""
        sets = lambda: [c[2] for c in self.mpv.commands if c[:2] == ("set_property", "fbo-format")]
        epoch = self.p.play_source("/run/s1.glsl", CARRIER)
        self.assertEqual((self.mpv.props["fbo-format"], sets()), ("rgba8", ["rgba8"]))
        for n in range(2, 6):
            self.assertTrue(self.p.swap_source("/run/s%d.glsl" % n, epoch))
        self.assertEqual((self.mpv.props["glsl-shaders"], sets()), (["/run/s5.glsl"], ["rgba8"]))
        self.assertTrue(self.p.swap_source(None, epoch))             # the bare carrier: no pass is added any more
        self.assertEqual((self.mpv.props["glsl-shaders"], sets()), ([], ["rgba8", "auto"]))
        self.assertTrue(self.p.swap_source("/run/s6.glsl", epoch))   # and a source again
        self.assertEqual(sets(), ["rgba8", "auto", "rgba8"])
        self.mpv.restart()                                           # the new player never had the source
        self.p.swap_source("/run/s7.glsl", epoch)
        self.assertEqual((self.p.source_shader, sets()), (None, ["rgba8", "auto", "rgba8", "auto"]))   # dropped, and the buffers chosen anew

    def test_after_an_mpv_restart_the_stale_source_is_dropped_before_the_buffers_are_chosen(self):
        """fbo-format stayed rgba8: autostart put a saved mapping that is off back on the restarted player, and
        set_mapping_mode(False) chose the buffers while the lost source still counted."""
        epoch = self.p.play_source("/run/s1.glsl", CARRIER)
        self.assertEqual(self.mpv.props["fbo-format"], "rgba8")
        self.mpv.restart()
        self.p.set_mapping_mode(False)                                # what Engine._commit(None) does first
        self.assertEqual(self.mpv.props["fbo-format"], "auto")
        self.p.set_shaders([])
        self.assertEqual((self.mpv.props["glsl-shaders"], self.mpv.props["fbo-format"], self.p.source_shader), ([], "auto", None))
        self.assertNotEqual(self.p.source_epoch, epoch)               # and the screen has changed hands
        self.assertFalse(self.p.clear_source(epoch))

    def test_the_other_order_after_a_restart_also_ends_with_normal_buffers(self):
        self.p.play_source("/run/s1.glsl", CARRIER)
        self.mpv.restart()
        self.p.set_shaders(["/run/map.glsl"])
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/map.glsl"])
        self.p.set_mapping_mode(False)
        self.assertEqual(self.mpv.props["fbo-format"], "auto")

    def test_clear_on_a_player_that_is_down_still_hands_the_screen_over(self):
        epoch = self.p.play_source("/run/s1.glsl", CARRIER)
        self.mpv.down = True
        with self.assertRaises(PlayerError):
            self.p.clear()
        self.assertEqual((self.p.source_shader, self.p.source_epoch > epoch), (None, True))

    def test_a_carrier_that_does_not_start_is_an_error_and_nothing_is_left_behind(self):
        """play_source never checked that the carrier became current; on that path keep-open was already changed
        for the clip that kept playing."""
        self.p.set_shaders(["/run/map.glsl"])
        self.mpv.props["path"] = "/media/a.mp4"
        epoch = self.p.source_epoch
        self.mpv.refuse_load = True
        with self.assertRaises(PlayerError) as c:
            self.p.play_source("/run/s1.glsl", CARRIER)
        self.assertIn("blank picture", str(c.exception))
        self.assertEqual(self.mpv.props["glsl-shaders"], ["/run/map.glsl"])
        self.assertEqual((self.mpv.props["path"], self.mpv.props["keep-open"], self.mpv.props["fbo-format"]), ("/media/a.mp4", "yes", "auto"))
        self.assertEqual((self.p.source_shader, self.p.source_epoch), (None, epoch))
        self.mpv.refuse_load = False
        self.assertIsNotNone(self.p.play_source("/run/s1.glsl", CARRIER))
        self.assertEqual(self.mpv.props["keep-open"], "no")           # set once the carrier is what plays

    def test_opacity_and_stop_for_a_source_happen_only_while_the_epoch_is_current(self):
        epoch = self.p.play_source("/run/s1.glsl", CARRIER)
        self.assertTrue(self.p.source_opacity(128, epoch))
        self.assertEqual((self.mpv.props["brightness"], self.p.opacity_now()), (-50, 50))
        self.p.claim_screen()                                         # a clip was accepted and waits for its dip
        self.assertFalse(self.p.source_opacity(0, epoch))
        self.assertEqual(self.mpv.props["brightness"], -50)
        self.assertFalse(self.p.clear_source(epoch))
        self.assertEqual(self.mpv.props["path"], CARRIER)
        self.assertEqual(self.p.source_shader, "/run/s1.glsl")        # the shader stays up until the clip loads
        again = self.p.play_source("/run/s2.glsl", CARRIER)
        self.p.play(["/media/a.mp4"], spawn=False)
        self.assertFalse(self.p.clear_source(again))                  # never stops a clip
        self.assertEqual(self.mpv.props["path"], "/media/a.mp4")
        last = self.p.play_source("/run/s3.glsl", CARRIER)
        self.assertTrue(self.p.clear_source(last))
        self.assertEqual((self.mpv.props["path"], self.mpv.props["glsl-shaders"]), (None, []))


if __name__ == "__main__":
    unittest.main()
