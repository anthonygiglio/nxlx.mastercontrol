# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Feedback from the room (J-179, D147): the store (mctl/feedback.py) and its two routes, POST and GET /api/feedback."""
import json
import os
import tempfile
import unittest
from unittest import mock

import tests        # the run's own temp folder (tests/__init__.py)
from mctl import feedback
from tests.test_boxcare import Base as CareBase, PIN


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class StoreTest(unittest.TestCase):
    def make(self, **kw):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "feedback.json")
        self.clock = Clock()
        self.lines = []
        return feedback.FeedbackStore(self.path, now=lambda: 1790000000, clock=self.clock, log=self.lines.append, **kw)

    def on_disk(self):
        with open(self.path) as f:
            return json.load(f)

    def add(self, store, face="good", text="", session="s1", **kw):
        return store.add(session, face, text, kw.pop("role", "guest"), kw.pop("screen", "room/scenes"),
                         kw.pop("node", {"name": "nxlx-node-01", "id": "ab" * 16}),
                         kw.pop("playing", [{"output": "Source 1", "playing": "intro.mp4"}]))

    def test_a_note_is_saved_with_its_context(self):
        s = self.make()
        note = self.add(s, "bad", "The left wall is too bright.", role="operator")
        self.assertEqual(note, {"t": 1790000000, "face": "bad", "role": "operator", "text": "The left wall is too bright.",
                                "screen": "room/scenes", "node": {"name": "nxlx-node-01", "id": "ab" * 16},
                                "playing": [{"output": "Source 1", "playing": "intro.mp4"}]})
        self.assertEqual(self.on_disk()["notes"], [note])
        self.assertEqual(s.notes(), [note])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), oct(0o640))

    def test_a_face_alone_is_a_whole_note(self):
        s = self.make()
        for face in feedback.FACES:
            self.clock.t += 10
            note = self.add(s, face)
            self.assertNotIn("text", note)
            self.assertEqual(note["face"], face)
        self.assertEqual([n["face"] for n in s.notes()], ["bad", "so-so", "good"], "newest first")

    def test_only_the_three_faces_and_the_three_roles(self):
        s = self.make()
        for face in ("great", "", None, 5, "GOOD"):
            with self.assertRaises(ValueError):
                self.add(s, face)
        with self.assertRaises(ValueError):
            self.add(s, "good", role="root")
        self.assertEqual(s.notes(), [])
        self.assertFalse(os.path.exists(self.path))

    def test_the_text_is_cut_to_a_thousand_characters_and_made_plain(self):
        s = self.make()
        note = self.add(s, "so-so", "x" * 5000)
        self.assertEqual(len(note["text"]), 1000)
        self.clock.t += 10
        note = self.add(s, "so-so", "two\nlines\x00 and\x1b[31m a bell\x07")
        self.assertNotIn("\x00", note["text"])
        self.assertNotIn("\x1b", note["text"])
        self.assertNotIn("\x07", note["text"])
        self.assertIn("two\nlines", note["text"])
        self.clock.t += 10
        self.assertNotIn("text", self.add(s, "good", "   \n  "), "a blank text is no text")

    def test_the_screen_and_the_playing_lines_are_checked(self):
        s = self.make()
        note = self.add(s, "good", screen="../../etc/passwd", playing=[{"output": "Source 1", "playing": "a\nb\x00c" + "z" * 500},
                                                                      {"output": "", "playing": "x"}, "junk"] + [{"output": "S%d" % i, "playing": "p"} for i in range(40)])
        self.assertNotIn("screen", note)
        self.assertEqual(len(note["playing"]), feedback.OUTPUTS_MAX - 2, "the first 16 are read, the two odd ones are left out")
        self.assertNotIn("\n", note["playing"][0]["playing"])
        self.assertLessEqual(len(note["playing"][0]["playing"]), feedback.LINE_MAX)

    def test_one_send_in_five_seconds_for_a_session(self):
        s = self.make()
        self.add(s, "good")
        self.clock.t += 2
        with self.assertRaises(feedback.TooFast) as c:
            self.add(s, "bad")
        self.assertTrue(1 <= c.exception.wait <= 5)
        self.assertEqual(len(s.notes()), 1, "a refused send stores nothing")
        self.add(s, "bad", session="s2")                       # another session is not held
        self.clock.t += 5
        self.add(s, "so-so")                                   # the first one's gap is over
        self.assertEqual(len(s.notes()), 3)

    def test_a_failed_write_keeps_nothing_and_does_not_hold_the_session(self):
        s = self.make()
        with mock.patch("mctl.feedback.os.replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                self.add(s, "good")
        self.assertEqual(s.notes(), [])
        self.add(s, "good")                                    # at once, no wait
        self.assertEqual(len(s.notes()), 1)
        self.assertTrue(any("could not be written" in l for l in self.lines))

    def test_the_store_is_bounded_and_the_newest_stay(self):
        s = self.make(cap=5)
        for i in range(8):
            self.clock.t += 10
            self.add(s, "good", "note %d" % i)
        self.assertEqual([n["text"] for n in s.notes()], ["note %d" % i for i in (7, 6, 5, 4, 3)])
        self.assertEqual(len(self.on_disk()["notes"]), 5)
        again = feedback.FeedbackStore(self.path, cap=3)
        self.assertEqual([n["text"] for n in again.notes()], ["note 7", "note 6", "note 5"], "a smaller cap on the next start")
        self.assertEqual(feedback.CAP, 500)

    def test_the_sessions_that_went_quiet_leave_no_entry(self):
        s = self.make()
        for i in range(20):
            self.add(s, "good", session="s%d" % i)
        self.clock.t += 60
        self.add(s, "good", session="late")
        self.assertEqual(list(s._last), ["late"])

    def test_a_link_at_the_state_file_is_not_followed_when_read(self):
        secret = os.path.join(tempfile.mkdtemp(), "elsewhere.json")
        with open(secret, "w") as f:
            json.dump({"version": 1, "notes": [{"t": 1, "face": "good", "role": "owner", "text": "planted"}]}, f)
        d = tempfile.mkdtemp()
        os.symlink(secret, os.path.join(d, "feedback.json"))
        s = feedback.FeedbackStore(os.path.join(d, "feedback.json"))
        self.assertEqual(s.notes(), [], "a link is no file of ours")

    def test_a_link_at_the_state_file_is_replaced_not_written_through(self):
        target = os.path.join(tempfile.mkdtemp(), "victim.json")
        with open(target, "w") as f:
            f.write("keep me")
        d = tempfile.mkdtemp()
        path = os.path.join(d, "feedback.json")
        os.symlink(target, path)
        s = feedback.FeedbackStore(path)
        self.add(s, "good", "hello")
        with open(target) as f:
            self.assertEqual(f.read(), "keep me")
        self.assertFalse(os.path.islink(path))
        with open(path) as f:
            self.assertEqual(json.load(f)["notes"][0]["text"], "hello")

    def test_a_link_at_the_temporary_name_is_refused(self):
        target = os.path.join(tempfile.mkdtemp(), "victim.json")
        with open(target, "w") as f:
            f.write("keep me")
        d = tempfile.mkdtemp()
        path = os.path.join(d, "feedback.json")
        os.symlink(target, path + ".tmp")
        s = feedback.FeedbackStore(path)
        with self.assertRaises(OSError):
            self.add(s, "good")
        with open(target) as f:
            self.assertEqual(f.read(), "keep me")
        self.assertEqual(s.notes(), [])

    def test_a_damaged_file_never_stops_the_panel(self):
        for raw in ("not json", "[]", '{"notes": 5}', '{"notes": [1, "x", {"face": "great"}, {"t": 1, "face": "good", "role": "guest", "text": 7}]}',
                    "[" * 100000):
            d = tempfile.mkdtemp()
            with open(os.path.join(d, "feedback.json"), "w") as f:
                f.write(raw)
            s = feedback.FeedbackStore(os.path.join(d, "feedback.json"))
            self.assertLessEqual(len(s.notes()), 1, raw[:20])
        self.assertEqual(feedback.FeedbackStore(os.path.join(tempfile.mkdtemp(), "none.json")).notes(), [])

    def test_the_state_says_where_the_notes_are_kept(self):
        s = self.make()
        self.assertIn("survive a restart", s.state()["note"])
        self.assertIn("lost at a restart", s.state(read_only_root=True)["note"])
        self.assertFalse(s.state(read_only_root=True)["kept"])


class ApiTest(CareBase):
    def setUp(self):
        super().setUp()
        self.auth.set_pin(PIN)
        self.path = os.path.join(tempfile.mkdtemp(), "feedback.json")
        self.clock = Clock()
        self.api.feedback = feedback.FeedbackStore(self.path, now=lambda: 1790000000, clock=self.clock)
        self.api.node_id = "cd" * 16

    def send(self, body, dev=False, **kw):
        return self.h("POST", "/api/feedback", body, self.view_dev if dev is False else dev, **kw)

    def test_a_guest_sends_a_face_alone_and_the_node_adds_the_context(self):
        st, out = self.send({"face": "good"})
        self.assertEqual((st, out), (200, {"sent": True}))
        [note] = self.api.feedback.notes()
        self.assertEqual((note["face"], note["role"], note["t"]), ("good", "guest", 1790000000))
        self.assertNotIn("text", note)
        self.assertEqual(note["node"]["id"], "cd" * 16)
        self.assertTrue(note["node"]["name"])
        self.assertIsInstance(note["playing"], list)
        self.assertTrue(all(set(p) == {"output", "playing"} for p in note["playing"]))

    def test_the_role_is_the_senders_and_the_screen_is_kept(self):
        for i, (dev, role) in enumerate(((self.view_dev, "guest"), (self.live_dev, "operator"), (self.full_dev, "owner"))):
            self.clock.t += 10
            st, _ = self.send({"face": "so-so", "text": "ok", "screen": "room/scenes"}, dev)
            self.assertEqual(st, 200)
            self.assertEqual(self.api.feedback.notes()[0]["role"], role)
        self.assertEqual(self.api.feedback.notes()[0]["screen"], "room/scenes")

    def test_nothing_that_names_a_device_or_a_secret_is_kept(self):
        self.send({"face": "bad", "text": "slow"}, self.view_dev, client="192.168.0.77")
        with open(self.path) as f:
            raw = f.read()
        for word in (self.view_dev["id"], "192.168.0.77", PIN, "token_hash"):
            self.assertNotIn(word, raw)

    def test_a_bad_face_and_odd_bodies_are_refused(self):
        for body in ({}, {"face": "great"}, {"face": "good", "text": 5}, {"face": "good", "extra": 1}, {"face": ["good"]},
                     {"face": "good", "screen": 7}):
            self.assertEqual(self.send(body)[0], 400, body)
        self.assertEqual(self.api.feedback.notes(), [])

    def test_a_long_text_is_cut_not_refused(self):
        self.assertEqual(self.send({"face": "good", "text": "y" * 9000})[0], 200)
        self.assertEqual(len(self.api.feedback.notes()[0]["text"]), 1000)

    def test_a_second_send_within_five_seconds_waits(self):
        self.assertEqual(self.send({"face": "good"})[0], 200)
        self.clock.t += 1
        st, out = self.send({"face": "bad"})
        self.assertEqual(st, 429, out)
        self.assertIn("retry_after", out)
        self.assertEqual(self.send({"face": "bad"}, self.live_dev)[0], 200, "another session is not held")
        self.clock.t += 6
        self.assertEqual(self.send({"face": "bad"})[0], 200)
        self.assertEqual(len(self.api.feedback.notes()), 3)

    def test_a_guest_may_send_whether_guest_controls_are_locked_or_open(self):
        for locked in (False, True):
            self.clock.t += 10
            self.assertEqual(self.h("POST", "/api/guests", {"locked": locked}, self.full_dev)[0], 200)
            self.assertEqual(self.send({"face": "good"})[0], 200, "locked" if locked else "open")

    def test_a_guest_cannot_read_and_does_not_gain_anything_else(self):
        self.send({"face": "good", "text": "hello"})
        self.assertEqual(self.h("GET", "/api/feedback", {}, self.view_dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/feedback", {}, self.live_dev)[0], 403)
        self.assertEqual(self.h("GET", "/api/feedback", {}, None)[0], 401)
        self.assertEqual(self.send({"face": "good"}, None)[0], 401)
        self.assertEqual(self.h("POST", "/api/feedback", {"face": "good"}, self.view_dev)[0], 429)
        self.assertEqual(self.h("DELETE", "/api/feedback", {}, self.full_dev)[0], 405)
        self.assertEqual(self.h("POST", "/api/pads", {"bank": 0, "index": 0, "label": "x", "file": ""}, self.view_dev)[0], 403)

    def test_the_owner_reads_the_list_newest_first(self):
        self.send({"face": "good", "text": "first"})
        self.clock.t += 10
        self.send({"face": "bad", "text": "second"}, self.live_dev)
        st, out = self.h("GET", "/api/feedback", {}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual([n["text"] for n in out["notes"]], ["second", "first"])
        self.assertEqual(out["cap"], 500)
        self.assertIn("survive a restart", out["note"])
        self.assertEqual(self.call("GET", "/api/feedback", token=self.full)[0], 200)
        self.assertEqual(self.call("GET", "/api/feedback", token=self.full)[1]["notes"][0]["role"], "operator")

    def test_a_secret_in_the_words_is_taken_out_when_the_owner_reads(self):
        self.send({"face": "bad", "text": "my pin is %s" % PIN})
        out = self.h("GET", "/api/feedback", {}, self.full_dev)[1]
        self.assertNotIn(PIN, json.dumps(out))

    def test_not_running_is_said_plainly(self):
        self.api.feedback = None
        self.assertEqual(self.send({"face": "good"})[0], 503)
        self.assertEqual(self.h("GET", "/api/feedback", {}, self.full_dev)[0], 503)

    def test_a_support_session_reads_as_the_owner_does_and_sends_nothing(self):
        self.send({"face": "good", "text": "hello"})
        remote, _ = self.remote_login()
        with mock.patch.object(self.api.support, "is_remote", return_value=True):
            out = self.h("GET", "/api/feedback", {}, remote)
            self.assertEqual(out[0], 200)
            self.assertEqual(out[1]["notes"][0]["text"], "hello")
            self.assertNotIn(self.full_dev["id"], json.dumps(out[1]))
            self.clock.t += 10
            self.assertEqual(self.h("POST", "/api/feedback", {"face": "good"}, remote)[0], 403)
        self.assertEqual(len(self.api.feedback.notes()), 1)

    def test_a_controller_sends_nothing(self):
        for who in ("midi", "osc", "dmx", "room"):
            dev = {"id": who, "name": who, "role": "live"}
            self.clock.t += 10
            self.assertEqual(self.h("POST", "/api/feedback", {"face": "good"}, dev)[0], 403, who)


if __name__ == "__main__":
    unittest.main()
