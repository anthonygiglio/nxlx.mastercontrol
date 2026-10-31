# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The switch from one NDI source to another (2026-10-31, D113).

Measured on the node: after a press the screen showed nothing new for 0.83 s at the median (0.68 to 1.10, 21 of
21 switches). Two parts of that are ours and are held here:

1. The old picture was dropped before the new source was ready. Now the helper opens the new source while the old
   picture's pipe is kept (the picture stands), the panel has the player let go, and the helper hands over.
   A press that fails still ends as it always did: the old picture goes, and the page says why.
2. About half a second was the wait for sound from a source that has none (AUDIO_WAIT). A source that sent no sound
   on its last open is waited for only a moment the next time; a source that has sound is never made to wait, and
   sound that turns up under a picture opened without it still ends the stream and opens it again with sound.

Nothing here is skipped (a skipped test fails the pvj-render job)."""

import os
import shutil
import tempfile
import threading
import time
import unittest

from pvj import ndi
from tests import test_ndi as T
from tests.test_ndi import FakeLib, OldPlayer, frame, wait
from tests.test_ndi_sound import SoundLib, sound


def service(test, lib, clock=None, **kw):
    folder = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, folder, True)
    extra = {"clock": clock} if clock is not None else {}
    s = ndi.Service(folder, "x", loader=lambda path: lib, log=lambda *_: None, first_frame=0.4, problem=lambda path: None,
                    pipe_clear=1.0, **extra, **kw)
    test.addCleanup(s.close)
    return s


class SwitchTest(unittest.TestCase):
    """The helper's two calls, with a reader that behaves as the player does: it holds the pipe until it is told to let go."""

    def setUp(self):
        self.lib = FakeLib([(b"A (Out)", b"192.168.0.20:5961"), (b"B (Out)", b"192.168.0.21:5961")])
        self.now = [1000.0]
        self.s = service(self, self.lib, clock=lambda: self.now[0])
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": False})
        self.a, self.b = ndi.source_id("A (Out)", "192.168.0.20"), ndi.source_id("B (Out)", "192.168.0.21")
        self.readers = []
        self.addCleanup(lambda: [r.leave() for r in self.readers])

    def reader(self):
        r = OldPlayer(self.s.fifo)
        self.readers.append(r)
        self.assertTrue(r.opened.wait(3))
        return r

    def show_a(self):
        """A is open, handed over and read by a player, as after a press."""
        self.lib.frames.put(frame(64, 16, fill=0xA1))
        self.assertTrue(self.s.handle({"cmd": "open", "id": self.a, "split": True})["ok"])
        self.assertTrue(self.s.handle({"cmd": "handover", "id": self.a})["ok"])
        old = self.reader()                                          # the player loads the pipe only after the hand-over
        self.assertTrue(wait(lambda: bytes(old.got) == b"\xa1" * 2048))
        return old

    def open_b_when_a_is_given_back(self, fill=0xB2):
        """B's first picture, sent once A's connection is back with the library (the library has one queue of
        frames for every source, and a frame must not be taken by the connection that is going)."""
        threading.Thread(target=lambda: wait(lambda: self.lib.closed >= 1) and self.lib.frames.put(frame(128, 16, fill=fill)), daemon=True).start()
        return self.s.handle({"cmd": "open", "id": self.b, "split": True})

    def test_the_old_picture_stands_while_the_new_source_connects_and_goes_only_at_the_hand_over(self):
        old = self.show_a()
        reply = self.open_b_when_a_is_given_back()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.lib.closed, 1)                         # A's connection is back with the library: one connection at a time
        self.assertFalse(old.ended.is_set(), "the old stream ended before the new source was handed over: the screen would have gone")
        self.assertEqual(bytes(old.got), b"\xa1" * 2048)             # and nothing of B was written into A's stream
        self.assertTrue(os.path.exists(self.s.fifo))
        old.leave()                                                  # the panel has the player let go
        done = self.s.handle({"cmd": "handover", "id": self.b})
        self.assertTrue(done["ok"], done)
        new = self.reader()                                          # and the player loads the pipe again
        self.assertTrue(wait(lambda: bytes(new.got) == b"\xb2" * 4096))
        self.assertEqual(bytes(new.late), b"")
        self.assertEqual(self.s.status()["playing"]["id"], self.b)
        self.assertEqual((self.s.held, self.s.waiting), (None, None))

    def test_a_press_that_fails_does_not_leave_the_old_picture_standing(self):
        old = self.show_a()
        reply = self.s.handle({"cmd": "open", "id": self.b, "split": True})        # B sends no picture
        self.assertFalse(reply["ok"])
        self.assertIn("no picture", reply["error"])
        self.assertTrue(wait(old.ended.is_set), "the old stream was kept after the press failed")
        self.assertEqual((self.s.held, self.s.waiting, self.s.receiver), (None, None, None))
        self.assertIsNone(self.s.status()["playing"])
        self.assertEqual(self.lib.closed, 2)                         # both connections are back

    def test_an_old_picture_kept_for_a_panel_that_never_hands_over_is_not_kept_for_ever(self):
        old = self.show_a()
        reply = self.open_b_when_a_is_given_back()
        self.assertTrue(reply["ok"])
        self.assertFalse(old.ended.is_set())
        self.now[0] += ndi.HANDOVER_SECONDS + 1
        self.assertIsNone(self.s.status()["playing"])                # the status gives up the new source and what was kept
        self.assertTrue(wait(old.ended.is_set))
        self.assertEqual((self.s.held, self.s.waiting, self.s.receiver), (None, None, None))
        self.assertFalse(self.s.handle({"cmd": "handover", "id": self.b})["ok"])    # and a late hand-over is refused in words

    def test_a_hand_over_with_nothing_opened_is_refused(self):
        reply = self.s.handle({"cmd": "handover", "id": self.a})
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["error"])

    def test_an_open_that_is_not_split_hands_over_by_itself_as_it_always_did(self):
        self.lib.frames.put(frame(64, 16, fill=0xA1))
        reply = self.s.handle({"cmd": "open", "id": self.a})
        self.assertTrue(reply["ok"])
        self.assertEqual((self.s.held, self.s.waiting), (None, None))
        self.assertEqual(self.s.status()["playing"]["id"], self.a)

    def test_a_player_that_does_not_let_go_is_not_written_over(self):
        old = self.show_a()
        reply = self.open_b_when_a_is_given_back()
        self.assertTrue(reply["ok"])
        done = self.s.handle({"cmd": "handover", "id": self.b})      # the player was not told to let go (old still reads)
        self.assertEqual((done["ok"], done["error"]), (False, ndi.PIPE_HELD))
        self.assertEqual(bytes(old.late), b"")
        self.assertIsNone(self.s.receiver)


class SilentSourceTest(unittest.TestCase):
    """With sound on, a source that sends none made every open wait AUDIO_WAIT for it."""

    def setUp(self):
        self.lib = SoundLib([(b"A (Out)", b"192.168.0.20:5961"), (b"B (Out)", b"192.168.0.21:5961")])
        self.now = [1000.0]
        self.s = service(self, self.lib, clock=lambda: self.now[0])
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": True})
        self.a, self.b = ndi.source_id("A (Out)", "192.168.0.20"), ndi.source_id("B (Out)", "192.168.0.21")

    def timed_open(self, sid):
        began = time.monotonic()
        reply = self.s.handle({"cmd": "open", "id": sid})
        return reply, time.monotonic() - began

    def test_the_second_open_of_a_source_that_sent_no_sound_does_not_wait_for_it_again(self):
        self.lib.frames.put(frame(64, 16))
        reply, first = self.timed_open(self.a)
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.assertGreaterEqual(first, ndi.AUDIO_WAIT - 0.1)         # the first time nobody knows: the whole wait
        self.s.handle({"cmd": "close"})
        self.lib.frames.put(frame(64, 16))
        reply, second = self.timed_open(self.a)
        self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "raw"))
        self.assertLess(second, ndi.AUDIO_WAIT - 0.2)
        self.assertLess(ndi.AUDIO_WAIT_SILENT, ndi.AUDIO_WAIT)

    def test_what_one_source_did_is_not_said_of_another(self):
        self.lib.frames.put(frame(64, 16))
        self.timed_open(self.a)
        self.s.handle({"cmd": "close"})
        self.lib.frames.put(frame(64, 16))
        reply, took = self.timed_open(self.b)
        self.assertTrue(reply["ok"])
        self.assertGreaterEqual(took, ndi.AUDIO_WAIT - 0.1)

    def test_a_source_that_has_sound_is_opened_with_it_every_time_and_is_never_remembered_as_silent(self):
        for _ in range(2):
            self.lib.sounds.put(sound())
            self.lib.frames.put(frame(64, 16))
            reply, took = self.timed_open(self.a)
            self.assertEqual((reply["ok"], reply["playing"]["container"]), (True, "matroska"), reply)
            self.assertLess(took, ndi.AUDIO_WAIT + 0.3)
            self.assertNotIn(self.a, self.s.no_sound)
            self.s.handle({"cmd": "close"})

    def test_sound_that_turns_up_under_a_source_believed_silent_ends_the_stream_and_is_forgotten(self):
        self.lib.frames.put(frame(64, 16))
        self.timed_open(self.a)
        self.assertIn(self.a, self.s.no_sound)
        self.s.handle({"cmd": "close"})
        self.lib.frames.put(frame(64, 16))
        reply, _ = self.timed_open(self.a)
        self.assertEqual(reply["playing"]["container"], "raw")
        self.lib.sounds.put(sound())                                 # the source starts to send sound after all
        self.assertTrue(wait(lambda: self.s.receiver.state == "changed"))
        self.assertNotIn(self.a, self.s.no_sound)
        self.s.handle({"cmd": "close"})
        self.lib.sounds.put(sound())
        self.lib.frames.put(frame(64, 16))
        reply, _ = self.timed_open(self.a)
        self.assertEqual(reply["playing"]["container"], "matroska")  # and the next open gives it its sound, in step, from the start

    def test_what_was_believed_of_a_source_is_not_believed_for_ever(self):
        self.lib.frames.put(frame(64, 16))
        self.timed_open(self.a)
        self.s.handle({"cmd": "close"})
        self.now[0] += ndi.NO_SOUND_SECONDS + 1
        self.lib.frames.put(frame(64, 16))
        reply, took = self.timed_open(self.a)
        self.assertTrue(reply["ok"])
        self.assertGreaterEqual(took, ndi.AUDIO_WAIT - 0.1)

    def test_with_sound_off_there_is_no_wait_at_all(self):
        self.s.handle({"cmd": "configure", "on": True, "addresses": [], "sound": False})
        self.lib.frames.put(frame(64, 16))
        reply, took = self.timed_open(self.a)
        self.assertTrue(reply["ok"])
        self.assertLess(took, ndi.AUDIO_WAIT - 0.2)
        self.assertEqual(self.s.no_sound, {})


class SwitchApiTest(T.NdiApiTest):
    """The panel's order for a press: open, then the player lets go, then the hand-over."""

    def playing_a(self):
        """A is shown, and a reader (the player's stand-in) holds the pipe and has read its picture."""
        self.enable()
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.call("POST", "/api/play", {"ndi": self.rid}, token=self.full)[0], 200)
        reader = OldPlayer(self.service.fifo)
        self.addCleanup(reader.leave)
        self.assertTrue(wait(lambda: len(reader.got) == 2048))
        return reader

    def test_the_player_is_told_to_let_go_after_the_new_source_has_its_first_picture_and_before_it_is_written(self):
        reader = self.playing_a()
        seen = []
        self.player.release_pipe = lambda path: seen.append((self.service.waiting is not None, self.service.held is not None)) or reader.leave() or True
        other = ndi.source_id("MAD (Out)", "192.168.0.21")
        n = len(self.lib.opened)
        threading.Thread(target=lambda: wait(lambda: self.lib.closed >= 1) and self.lib.frames.put(frame(64, 16)), daemon=True).start()
        self.assertEqual(self.call("POST", "/api/play", {"ndi": other}, token=self.full)[0], 200)
        self.assertEqual(len(self.lib.opened), n + 1)
        self.assertEqual(seen, [(True, True)])                       # the new source was waiting, and the old stream was kept for the player
        self.assertEqual(self.api.ndi.current["id"], other)

    def test_a_press_that_fails_to_open_has_the_player_let_go_and_the_page_says_why(self):
        reader = self.playing_a()
        released = []
        self.player.release_pipe = lambda path: released.append(path) or reader.leave() or True
        other = ndi.source_id("MAD (Out)", "192.168.0.21")
        st, body, _ = self.call("POST", "/api/play", {"ndi": other}, token=self.full)         # no picture comes
        self.assertEqual(st, 409)
        self.assertIn("no picture", body["error"])
        self.assertEqual(released, [self.service.fifo])              # the old picture goes, as a failed press always ended
        self.assertEqual((self.service.held, self.service.waiting, self.service.receiver), (None, None, None))


# The inherited tests of NdiApiTest are run by its own class; here only the ones above.
for _name in dir(T.NdiApiTest):
    if _name.startswith("test_"):
        setattr(SwitchApiTest, _name, None)


if __name__ == "__main__":
    unittest.main()
