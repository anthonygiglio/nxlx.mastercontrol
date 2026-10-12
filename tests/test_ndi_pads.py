# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A pad that holds an NDI source (D62, the owner's words of 2026-10-11: "i want pads to hold NDI sources"): what it
stores, who may put a source on a pad, what a press does for every identity, and what an older release makes of it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from pvj import boxcare, boxstate, midi, ndi, policy
from pvj.api import Api
from pvj.settings import Settings
from tests.test_ndi import FakeClient, FakeLib, frame, wait
from tests.test_roles import CONTROLLER_DEVICES, LAN, TUNNEL, RolesBase, SCENE

NAME, HOST = "RESOLUME (Output)", "192.168.0.20"
RID = ndi.source_id(NAME, HOST)
ROW = {"id": RID, "name": NAME, "from": HOST + ":5961"}
OTHER = (b"MAD (Out)", b"192.168.0.21:5961")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PadBase(RolesBase):
    """The roles' rig (an owner, an operator, a guest, the Room with its scenes) with the real NDI Service behind a
    client that calls it directly, and the fake library behind that."""

    def setUp(self):
        super().setUp()
        self.lib = FakeLib([(NAME.encode(), (HOST + ":5961").encode()), OTHER])
        self.ndidir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.ndidir, True)
        self.service = ndi.Service(self.ndidir, "x", loader=lambda path: self.lib, log=lambda *_: None, first_frame=0.3,
                                   problem=lambda path: None)
        self.addCleanup(self.service.close)
        self.client = FakeClient(self.service)
        reg, st = self.api.registry, self.settings
        self.api.ndi = ndi.Input(self.client, self.service.fifo, ndi.Wanted(lambda: reg.enabled("inputs-ndi"), lambda: st.data,
                                                                            log=lambda *_: None), log=lambda *_: None)
        self.enable()

    def enable(self, on=True):
        self.assertEqual(self.h("POST", "/api/modules/inputs-ndi", {"enabled": on}, self.owner)[0], 200)

    def give(self, index=0, sid=RID, by=None, **more):
        return self.h("POST", "/api/pads", dict({"bank": 0, "index": index, "ndi": sid}, **more), by or self.operator)

    def pad(self, index=0):
        return self.settings.data["pads"]["banks"][0]["pads"][index]

    def press(self, device, index=0, client=LAN):
        self.later()
        return self.h("POST", "/api/play", {"pad": [0, index]}, device, client)

    def pipes(self):
        return [c for c in self.player.calls if c[0] == "play_pipe"]

    def feed(self):
        """Frames for as long as the test runs: every receiver that is opened gets a first picture."""
        stop = threading.Event()
        self.addCleanup(stop.set)

        def run():
            while not stop.wait(0.01):
                if self.lib.frames.qsize() < 4:
                    self.lib.frames.put(frame(64, 16))
        threading.Thread(target=run, daemon=True).start()
        return stop

    def announce(self, *pairs):
        self.lib.raw = [(n.encode(), w.encode()) for n, w in pairs]
        self.service._sources_at = None                 # the helper's one-second memory of the list


class Stored(PadBase):
    def test_the_pad_stores_an_id_and_nothing_else(self):
        before = self.settings.data["schema"]
        st, out = self.give(label="Stage cam", ending="hold")
        self.assertEqual(st, 200, out)
        self.assertEqual(self.pad(), {"label": "Stage cam", "file": "", "ndi": RID})
        self.assertEqual(out["banks"][0]["pads"][0], {"label": "Stage cam", "file": "", "ndi": RID})
        self.assertEqual(out["ndi"], {RID: {"name": NAME, "from": HOST + ":5961"}})
        self.assertEqual(self.settings.data["schema"], before, "an NDI pad must not move the schema")
        self.assertEqual(self.settings.data["ndi"]["sources"], [ROW])
        again = Settings(self.settings.path)
        again.load()
        self.assertEqual(again.data["pads"]["banks"][0]["pads"][0], {"label": "Stage cam", "file": "", "ndi": RID})
        self.assertEqual(Api.pad_ndi(self.pad()), RID)

    def test_a_clip_or_a_shader_beside_it_wins_in_a_hand_edited_file(self):
        self.assertIsNone(Api.pad_ndi({"label": "", "file": "a.mp4", "ndi": RID}))
        self.assertIsNone(Api.pad_ndi({"label": "", "file": "", "shader": "x.fs", "ndi": RID}))
        for bad in (NAME, RID.upper(), RID + "0", 7, None, ["x"], "", {"id": RID}):
            self.assertIsNone(Api.pad_ndi({"label": "", "file": "", "ndi": bad}), bad)

    def test_what_is_refused_when_a_pad_is_given_a_source(self):
        gone = ndi.source_id("GONE (Out)", "192.168.0.99")
        for body, status in (({"ndi": NAME}, 400), ({"ndi": HOST}, 400), ({"ndi": RID, "file": "a.mp4"}, 400),
                             ({"ndi": RID, "shader": "x.fs"}, 400), ({"ndi": gone}, 404), ({"ndi": 7}, 400), ({"ndi": [RID]}, 400),
                             ({"ndi": {"id": RID, "name": NAME, "from": "10.0.0.1:1"}}, 400)):
            st, out = self.h("POST", "/api/pads", dict({"bank": 0, "index": 0}, **body), self.operator)
            self.assertEqual(st, status, (body, out))
        self.assertEqual(self.pad(), {"label": "", "file": ""}, "a refused request changed the pad")
        self.assertNotIn("sources", self.settings.data.get("ndi", {}))

    def test_who_may_allow_a_source_and_who_removes_one(self):
        """An Operator puts a source that is on the list NOW on a pad, which makes it allowed. Only the Owner takes
        one off the allowed list. A Guest and every controller do neither."""
        for name, dev in [("guest", self.guest)] + sorted(CONTROLLER_DEVICES.items()):
            self.later()
            self.assertEqual(self.give(by=dev)[0], 403, name)
        self.assertEqual(self.give()[0], 200)
        self.assertIn("is allowed for pads", " ".join(self.lines))
        # once allowed it can be put on another pad while the sender is away
        self.announce()
        self.assertEqual(self.give(index=1)[0], 200)
        self.assertEqual(self.settings.data["ndi"]["sources"], [ROW])
        for dev in (self.guest, self.operator, CONTROLLER_DEVICES["osc"]):
            self.later()
            self.assertEqual(self.h("POST", "/api/ndi", {"action": "forget", "id": RID}, dev)[0], 403)
        self.assertEqual(self.h("POST", "/api/ndi", {"action": "forget", "id": RID}, self.owner)[0], 200)
        self.assertNotIn("sources", self.settings.data["ndi"])
        self.assertEqual(self.h("POST", "/api/ndi", {"action": "forget", "id": RID}, self.owner)[0], 404)
        st, out = self.press(self.operator)
        self.assertEqual((st, "not allowed any more" in out["error"]), (409, True), out)
        self.assertEqual(self.give(index=2)[0], 404)           # and a source that is away cannot be allowed

    def test_the_allowed_list_is_bounded_and_survives_the_other_ndi_settings(self):
        self.give()
        for body in ({"action": "sound", "on": True}, {"action": "add_address", "address": "192.168.0.20"},
                     {"action": "remove_address", "address": "192.168.0.20"}, {"action": "sound", "on": False}):
            self.assertEqual(self.h("POST", "/api/ndi", body, self.owner)[0], 200, body)
            self.assertEqual(self.settings.data["ndi"]["sources"], [ROW], body)
        self.assertEqual(self.h("GET", "/api/ndi", {}, self.operator)[1]["allowed"], [ROW])
        self.assertEqual(ndi.validate_saved(self.settings.data["ndi"])["sources"], [ROW])
        with self.settings.lock:
            self.settings.data["ndi"]["sources"] = [dict(id=ndi.source_id("S%d" % n, "10.0.0.1"), name="S%d" % n, **{"from": "10.0.0.1:1"})
                                                    for n in range(ndi.MAX_ALLOWED)]
        st, out = self.give(index=3)
        self.assertEqual((st, "at most" in out["error"]), (400, True))

    def test_a_stored_row_cannot_say_one_thing_and_mean_another(self):
        for bad in (dict(ROW, name="OTHER (Out)"), dict(ROW, **{"from": "192.168.0.66:5961"}), dict(ROW, extra=1), dict(ROW, id="x"),
                    {"id": RID, "name": NAME}, "x", dict(ROW, name=NAME + "‮")):
            with self.assertRaises(ndi.NdiError, msg=bad):
                ndi.validate_saved({"addresses": [], "sources": [bad]})
            self.assertEqual(ndi.saved_sources({"sources": [bad, ROW, ROW]}), [ROW])
        with self.assertRaises(ndi.NdiError):
            ndi.validate_saved({"addresses": [], "sources": [ROW, ROW]})
        self.assertEqual(ndi.saved_sources({"sources": "x"}), [])
        self.assertEqual(ndi.saved_sources(None), [])

    def test_an_export_and_an_import_keep_the_pad_and_the_allowed_source(self):
        self.give(label="Cam")
        self.assertEqual(boxcare.check_pads(self.settings.data["pads"], None)["banks"][0]["pads"][0], {"label": "Cam", "file": "", "ndi": RID})
        self.assertEqual(boxcare.check_ndi(self.settings.data["ndi"], None)["sources"], [ROW])
        pads = json.loads(json.dumps(self.settings.data["pads"]))
        for bad in (NAME, 7, RID + "x"):
            pads["banks"][0]["pads"][0]["ndi"] = bad
            with self.assertRaises(ValueError, msg=bad):
                boxcare.check_pads(pads, None)

    def test_a_guest_gets_no_names_and_addresses_with_the_pads(self):
        self.give()
        self.assertNotIn("ndi", self.h("GET", "/api/pads", {}, self.guest)[1])
        self.assertEqual(self.h("GET", "/api/pads", {}, self.operator)[1]["ndi"][RID]["name"], NAME)

    def test_autostart_does_not_take_an_ndi_pad(self):
        self.give()
        self.api.autostart = type("A", (), {"status": lambda s: {}})()
        st, out = self.h("POST", "/api/autostart", {"mode": "pad", "pad": [0, 0]}, self.owner)
        self.assertEqual((st, out.get("error")), (400, "choose a pad that has a clip or a shader"))


class Press(PadBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.give()[0], 200)

    def test_present_it_plays_through_the_one_road_and_the_pad_is_the_one_playing(self):
        self.lib.frames.put(frame(64, 16))
        st, out = self.press(self.operator)
        self.assertEqual((st, out.get("playing"), out.get("name")), (200, "ndi", NAME), out)
        self.assertEqual(len(self.pipes()), 1)
        self.assertEqual(self.api.ndi.current, {"id": RID, "name": NAME})
        self.player.status = lambda: {"running": True, "path": self.service.fifo}
        pl = self.h("GET", "/api/status", {}, self.operator)[1]["player"]
        self.assertEqual((pl["ndi"], pl["ndi_id"], pl.get("path")), (NAME, RID, None))
        self.assertNotIn("ndi_refused", pl)

        class Holder:
            _player_seen = None
        snap = boxstate.snapshot(self.api, 0.0, fresh=True, holder=Holder)
        self.assertEqual((snap["pad_ndi"][0][0], snap["ndi"]), (RID, RID))
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 0}, snap, 0), "active")
        snap["ndi"] = None
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 0}, snap, 0), "on")
        self.assertEqual(midi.light_state({"action": "pad", "bank": 0, "index": 5}, snap, 0), "off")

    def test_absent_it_answers_at_once_and_opens_nothing(self):
        self.announce(("MAD (Out)", "192.168.0.21:5961"))
        sent = len(self.client.sent)
        began = time.monotonic()
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, "NDI source '%s' is not on the network now" % NAME))
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertEqual([m["cmd"] for m in self.client.sent[sent:]], ["status"])       # the list was asked; nothing was opened
        self.assertEqual((self.lib.opened, self.pipes()), ([], []))
        pl = self.h("GET", "/api/status", {}, self.operator)[1]["player"]
        self.assertEqual(pl["ndi_refused"], {"id": RID, "message": out["error"]})

    def test_duplicated_it_is_refused_and_no_sender_is_picked(self):
        self.announce((NAME, HOST + ":5961"), (NAME, HOST + ":5962"))
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, "two senders announce '%s' from %s now: the box does not pick one" % (NAME, HOST)))
        self.assertEqual((self.lib.opened, self.pipes()), ([], []))
        # and a helper that lists one id twice is not believed either
        self.announce((NAME, HOST + ":5961"))
        self.client.answer = dict(self.service.status(), sources=[dict(ROW), dict(ROW)])
        st, out = self.press(self.operator)
        self.assertEqual((st, "does not pick one" in out["error"]), (409, True))
        self.client.answer = None
        # such a source cannot be put on a pad in the first place
        self.announce(("TWO (Out)", "192.168.0.30:1"), ("TWO (Out)", "192.168.0.30:2"))
        self.assertEqual(self.give(index=1, sid=ndi.source_id("TWO (Out)", "192.168.0.30"))[0], 409)

    def test_the_same_name_from_another_machine_is_refused_by_name(self):
        """The pad follows a name ON A MACHINE. A sender that carries the name from elsewhere is another source."""
        self.announce((NAME, "192.168.0.66:5961"))
        st, out = self.press(self.operator)
        self.assertEqual(st, 409)
        self.assertIn("is announced from 192.168.0.66 now, not from %s where this pad's source is" % HOST, out["error"])
        self.assertNotIn("an address the owner gave", out["error"])
        self.assertEqual((self.lib.opened, self.pipes()), ([], []))
        # bound to an address the Owner gave: the same refusal, and it says so
        self.assertEqual(self.h("POST", "/api/ndi", {"action": "add_address", "address": HOST}, self.owner)[0], 200)
        self.announce((NAME, "192.168.0.66:5961"))
        st, out = self.press(self.operator)
        self.assertEqual(st, 409)
        self.assertIn("(an address the owner gave)", out["error"])
        self.assertEqual((self.lib.opened, self.pipes()), ([], []))
        # both there: the pad's own machine plays, never the other
        self.announce((NAME, "192.168.0.66:5961"), (NAME, HOST + ":5961"))
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.press(self.operator)[0], 200)
        self.assertEqual(self.lib.opened, [(NAME.encode(), (HOST + ":5961").encode())])

    def test_a_live_row_that_does_not_match_the_allowed_one_is_not_played(self):
        """The id is 48 bits of a hash: the name and the machine are compared as well."""
        real = self.api.ndi.find
        self.api.ndi.find = lambda: ([{"id": RID, "name": "LOOKALIKE (Out)", "from": "192.168.0.77:1"}], "")
        st, out = self.press(self.operator)
        self.assertEqual((st, self.lib.opened), (409, []))
        self.api.ndi.find = real

    def test_the_module_off_the_helper_away_and_the_runtime_missing_each_say_why(self):
        self.client.down = True
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, "the NDI helper (pvj-ndi) is not running"))
        self.client.down = False
        self.api.ndi.setup = False
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, ndi.NOT_SET_UP))
        self.api.ndi.setup = True
        self.client.answer = dict(self.service.status(), runtime={"present": False, "loaded": False, "problem": "the NDI runtime is not on this box yet"})
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, "the NDI runtime is not on this box yet"))
        self.client.answer = None
        self.enable(False)
        st, out = self.press(self.operator)
        self.assertEqual((st, out["error"]), (409, "turn on the NDI input in System first"))
        self.assertEqual(self.pipes(), [])
        self.assertNotIn("ndi_refused", self.h("GET", "/api/status", {}, self.operator)[1]["player"])
        # and nothing else broke: the pads are read, a clip pad plays, the NDI pad can be made a clip pad
        self.assertEqual(self.h("GET", "/api/pads", {}, self.operator)[0], 200)
        self.assertEqual(self.h("POST", "/api/pads", {"bank": 0, "index": 1, "file": "a.mp4"}, self.operator)[0], 200)
        self.assertEqual(self.press(self.operator, index=1)[0], 200)
        self.assertEqual(self.h("POST", "/api/pads", {"bank": 0, "index": 0, "file": "a.mp4"}, self.operator)[0], 200)
        self.assertEqual(self.give(index=2)[0], 409)               # and none is given while the module is off

    def test_a_clip_pad_after_it_plays_and_the_source_is_let_go(self):
        self.h("POST", "/api/pads", {"bank": 0, "index": 1, "file": "a.mp4"}, self.operator)
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.press(self.operator)[0], 200)
        self.assertEqual(self.press(self.operator, index=1)[0], 200)
        self.assertTrue(wait(lambda: self.api.ndi.current is None and self.service.receiver is None))


class Identities(PadBase):
    """Every identity of tests/test_roles.py's table, on the real handlers, with an NDI pad."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.give()[0], 200)
        self.h("POST", "/api/pads", {"bank": 0, "index": 1, "file": "a.mp4"}, self.operator)

    def shown(self):
        return len(self.pipes())

    def test_who_plays_an_ndi_pad(self):
        self.ready()
        feeding = self.feed()
        # nobody
        self.assertEqual(self.press(None)[0], 401)
        # a Guest, locked and open: refused as he is refused a stream, and the clip pad beside it is his
        self.lock(True)
        st, out = self.press(self.guest)
        self.assertEqual((st, out["error"]), (403, policy.LOCKED_TEXT))
        self.lock(False)
        st, out = self.press(self.guest)
        self.assertEqual((st, out["error"]), (403, policy.NDI_PAD_TEXT))
        self.assertEqual(self.press(self.guest, index=1)[0], 200)
        self.assertEqual((self.shown(), self.lib.opened), (0, []))
        # past the gate (as if the form let it through): the handler refuses a Guest itself
        with self.assertRaises(Exception) as c:
            self.api.play({"pad": [0, 0]}, self.guest, LAN)
        self.assertEqual((c.exception.status, c.exception.message), (403, Api.NDI_PAD_GUEST))
        self.assertEqual(policy.NDI_PAD_TEXT, Api.NDI_PAD_GUEST)
        # an Operator and an Owner
        for n, dev in enumerate((self.operator, self.owner), 1):
            st, out = self.press(dev)
            self.assertEqual((st, out.get("playing")), (200, "ndi"), out)
            self.assertEqual(self.shown(), n)
        # MIDI, OSC and DMX: the press is answered at once and the source connects on its own thread
        n = self.shown()
        for name in ("midi", "osc", "dmx"):
            st, out = self.press(CONTROLLER_DEVICES[name], client=name)
            self.assertEqual((st, out), (200, {"playing": "ndi", "name": NAME, "pending": True}), name)
            n += 1
            self.assertTrue(wait(lambda: self.shown() == n and not self.api._ndi_job.is_alive()), name)
        # a Room scene (and so the schedule) waits for it, and its answer is the play's own
        st, out = self.press(CONTROLLER_DEVICES["room"], client="room")
        self.assertEqual((st, out.get("playing"), "pending" in out), (200, "ndi", False))
        self.assertEqual(self.shown(), n + 1)
        # support sessions: view is refused as a guest's view always was, live plays as a controller's reach, full as the Owner
        n = self.shown()
        for role, want in (("view", 403), ("live", 200), ("full", 200)):
            if self.api.support.session is not None:
                self.h("POST", "/api/support/stop", {}, self.owner)
            body = self.h("POST", "/api/support/start", {"confirm": "start", "role": role}, self.owner)[1]
            login = self.h("POST", "/api/support/login", {"code": body["code"]}, None, TUNNEL)[1]
            dev = self.api.support.authenticate(login["token"])
            st, out = self.press(dev, client=TUNNEL)
            self.assertEqual(st, want, (role, out))
            n += 1 if want == 200 else 0
            self.assertEqual(self.shown(), n, role)
        feeding.set()

    def test_a_controllers_refusal_is_kept_for_the_pads(self):
        self.announce()
        st, out = self.press(CONTROLLER_DEVICES["midi"], client="midi")
        self.assertEqual(st, 409)                           # what can be said at once is said at once
        pl = self.h("GET", "/api/status", {}, self.operator)[1]["player"]
        self.assertEqual(pl["ndi_refused"]["id"], RID)
        self.assertIn("is not on the network now", pl["ndi_refused"]["message"])
        # a source that is listed and sends nothing: the refusal comes from the thread
        self.announce((NAME, HOST + ":5961"))
        st, out = self.press(CONTROLLER_DEVICES["osc"], client="osc")
        self.assertEqual((st, out.get("pending")), (200, True))
        st2, out2 = self.press(CONTROLLER_DEVICES["osc"], client="osc")      # a second press while it connects
        self.assertIn(st2, (200, 409))
        self.assertTrue(wait(lambda: not self.api._ndi_job.is_alive(), 10))
        self.assertTrue(wait(lambda: "a pad's NDI source" in " ".join(self.lines)))
        self.assertEqual(self.shown(), 0)
        # and it goes when the source shows
        self.lib.frames.put(frame(64, 16))
        self.assertEqual(self.press(self.operator)[0], 200)
        self.player.status = lambda: {"running": True, "path": self.service.fifo}
        self.assertNotIn("ndi_refused", self.h("GET", "/api/status", {}, self.operator)[1]["player"])

    def test_a_scene_and_a_schedule_entry_that_name_an_ndi_pad(self):
        """SCENE's box is pad [0, 0]. A Guest is refused the scene, by any way of naming it; an Operator starts it;
        the schedule runs it as the Room does."""
        for body in ({"scene": SCENE}, {"number": 1}, {"name": "Show"}):
            self.later()
            st, out = self.h("POST", "/api/room/scene", body, self.guest)
            self.assertEqual((st, out["error"]), (403, "this scene plays an NDI source; ask an operator to start it"), body)
        self.assertEqual(self.shown(), 0)
        self.feed()
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, self.operator)[0], 200)
        self.assertTrue(wait(lambda: self.shown() == 1))
        from pvj import scheduler
        self.assertTrue(wait(lambda: self.api.room._scene_job is None or not self.api.room._report(self.api.room._scene_job)["running"]))
        sched = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        self.api.room._last_box = None                      # the same scene again within a few seconds is not started twice (BOX_GAP)
        sched._execute({"id": "e", "label": "Show", "action": "scene", "scene": SCENE}, __import__("datetime").datetime(2026, 10, 11, 12, 0))
        self.assertTrue(wait(lambda: self.shown() == 2))
        # with the pad a clip pad again the scene is a guest's
        self.h("POST", "/api/pads", {"bank": 0, "index": 0, "file": "a.mp4"}, self.operator)
        self.later()
        self.assertTrue(wait(lambda: self.api.room._scene_job is None or not self.api.room._report(self.api.room._scene_job)["running"]))
        self.assertEqual(self.h("POST", "/api/room/scene", {"scene": SCENE}, self.guest)[0], 200)


class Order(PadBase):
    """The locks know their place in every test (tests/lockrank.py fails a taking out of order): these run the two
    orders of events that matter for a pad, on real threads."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.give()[0], 200)
        self.h("POST", "/api/pads", {"bank": 0, "index": 1, "file": "a.mp4"}, self.operator)
        self.h("POST", "/api/pads", {"bank": 0, "index": 2, "file": "b.mov"}, self.operator)

    def test_a_press_during_a_crossfade(self):
        self.settings.data["mix"] = {"transition": "crossfade", "duration": 1.0}
        self.assertEqual(self.press(self.operator, index=1)[0], 200)
        out = []
        clip = threading.Thread(target=lambda: out.append(self.h("POST", "/api/play", {"pad": [0, 2]}, self.operator)))
        clip.start()                                         # a clip on its way in over a still
        self.lib.frames.put(frame(64, 16))
        st, body = self.h("POST", "/api/play", {"pad": [0, 0]}, self.operator)
        clip.join(10)
        self.assertFalse(clip.is_alive())
        self.assertIn(st, (200, 409), body)                  # the source shows, or the clip was the later wish
        self.assertEqual(out[0][0], 200)
        if st == 200 and self.pipes():
            self.assertFalse(self.api.transitions.look(), "a live source is cut to: no transition is left running")

    def test_stop_during_a_load(self):
        loading, go, out = threading.Event(), threading.Event(), []
        real = self.player.play_pipe

        def play_pipe(*args):
            loading.set()
            go.wait(5)
            return real(*args)
        self.player.play_pipe = play_pipe
        self.lib.frames.put(frame(64, 16))
        t = threading.Thread(target=lambda: out.append(self.press(CONTROLLER_DEVICES["midi"], client="midi")))
        t.start()
        self.assertTrue(loading.wait(5))
        stop = threading.Thread(target=lambda: out.append(self.h("POST", "/api/control", {"action": "stop"}, self.operator)))
        stop.start()
        time.sleep(0.2)
        go.set()
        t.join(5)
        stop.join(5)
        self.assertFalse(t.is_alive() or stop.is_alive())
        self.assertEqual(sorted(o[0] for o in out), [200, 200])
        self.assertTrue(wait(lambda: not self.api._ndi_job.is_alive()))
        self.assertTrue(wait(lambda: self.api.ndi.current is None), "the Stop came later: it stands")


# The release before NDI, by its commit (master at f36f8f0, just before #94 was merged). It was "origin/master" while
# the NDI branch was open; once that branch was master, the rollback tests of D62 and D85 read their own code as the
# "older release" and failed on master itself (2026-10-11). An older release is named by a commit, never by a branch.
MASTER = "f36f8f0d040a35a6ecc72fb8a1001273b7f7ba0d"


class Rollback(PadBase):
    """An older release (master's own code, read from git) loads a settings file that has an NDI pad, an allowed
    source and nothing else new, and treats the pad as an empty one."""

    def test_masters_code_loads_the_file_and_the_pad_is_empty_there(self):
        self.give(label="Cam")
        self.h("POST", "/api/pads", {"bank": 0, "index": 1, "file": "a.mp4"}, self.operator)
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", MASTER], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % MASTER)
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", MASTER, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        self.assertFalse(os.path.exists(os.path.join(old, "pvj", "ndi.py")), "master has no NDI: this is the older release")
        script = r'''
import json, sys
from pvj.settings import Settings
from pvj import boxcare
s = Settings(sys.argv[1])
s.load()
pad = s.data["pads"]["banks"][0]["pads"][0]
out = {"schema": s.data["schema"], "pad": pad, "file": pad["file"], "ndi": s.data.get("ndi")}
out["checked"] = boxcare.check_pads(s.data["pads"], None)["banks"][0]["pads"][:2]
import inspect
from pvj.api import Api
out["play_reads"] = "pad is empty" in inspect.getsource(Api.play)
out["pad_shader"] = Api.pad_shader(pad)
s.data["mix"] = {"transition": "dip", "duration": 2.0}
s.save()
print(json.dumps(out))
'''
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(out["schema"], self.settings.data["schema"])
        self.assertEqual(out["file"], "")                                       # master's play: "pad is empty"
        self.assertTrue(out["play_reads"])
        self.assertIsNone(out["pad_shader"])
        self.assertEqual(out["checked"], [{"label": "Cam", "file": ""}, {"label": "", "file": "a.mp4", "ending": "loop"}])
        with open(copy) as f:
            kept = json.load(f)                                                 # and master's save kept what it does not know
        self.assertEqual(kept["pads"]["banks"][0]["pads"][0], {"label": "Cam", "file": "", "ndi": RID})
        self.assertEqual(kept["ndi"]["sources"], [ROW])


if __name__ == "__main__":
    unittest.main()
