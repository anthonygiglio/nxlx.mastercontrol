# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The room: groups, scenes and the Room screen's API. Every projector here is the fake from test_projector.py on
loopback (allowed in these tests only), or a loopback port with nothing behind it; nothing touches a network."""
import datetime
import os
import socket
import threading
import time
import unittest

from pvj import midi, osc, projector, room, scheduler, settings as settings_mod
from tests.test_projector import FakeProjector, LOOPBACK_OK, wait_for
from tests.test_server import ServerBase


def sets(fake):
    """The set commands the fake projector got, in order: "POWR 1", "INPT 32", "AVMT 11"."""
    return [line[2:] for line in list(fake.received) if not line.endswith("?")]


def room_threads():
    return [t for t in threading.enumerate() if t.name == "room"]


class ValidateTest(unittest.TestCase):
    GROUP = {"name": "Main wall", "projectors": ["0123abcd"]}

    def scene(self, **kw):
        return dict({"name": "Movie", "groups": [{"group": "all", "power": "on", "input": "31"}], "box": {"action": "stop"}}, **kw)

    def refused(self, value):
        with self.assertRaises(room.RoomError):
            room.validate(value)

    def test_a_clean_section_and_its_defaults(self):
        self.assertEqual(room.validate(None), {"groups": [], "scenes": []})
        self.assertEqual(settings_mod.default_settings()["room"], room.default_room())
        self.assertEqual(settings_mod.SCHEMA, 14)                                 # room itself made no schema change; 14 is the NDI input
        out = room.validate({"groups": [dict(self.GROUP, name="  Main wall ")], "scenes": [self.scene()]})
        g, s = out["groups"][0], out["scenes"][0]
        self.assertEqual((g["name"], g["projectors"]), ("Main wall", ["0123abcd"]))
        self.assertRegex(g["id"], r"\A[0-9a-f]{8}\Z")
        self.assertEqual(s["groups"], [{"group": "all", "power": "on", "input": "31", "picture": "leave", "sound": "leave"}])
        self.assertEqual(s["box"], {"action": "stop"})
        self.assertEqual(room.validate(out), out)                                 # what was stored is accepted again, ids kept

    def test_limits_on_counts_and_names(self):
        many = [{"name": "G%d" % i, "projectors": ["0123abcd"]} for i in range(room.MAX_GROUPS + 1)]
        self.refused({"groups": many})
        self.assertEqual(len(room.validate({"groups": many[:-1]})["groups"]), room.MAX_GROUPS)
        scenes = [self.scene(name="S%d" % i) for i in range(room.MAX_SCENES + 1)]
        self.refused({"scenes": scenes})
        self.assertEqual(len(room.validate({"scenes": scenes[:-1]})["scenes"]), room.MAX_SCENES)
        self.refused({"groups": [dict(self.GROUP, name="x" * (room.NAME_MAX + 1))]})
        self.refused({"groups": [dict(self.GROUP, name="")]})
        self.refused({"groups": [dict(self.GROUP, projectors=[])]})
        self.refused({"groups": [dict(self.GROUP, projectors=["0123abcd"] * 2)]})
        self.refused({"groups": [dict(self.GROUP, projectors=["%08x" % i for i in range(projector.MAX_PROJECTORS + 1)])]})
        self.refused({"scenes": [self.scene(groups=[{"group": "%08x" % i, "power": "on"} for i in range(room.MAX_GROUPS + 2)])]})

    def test_a_trailing_newline_never_passes(self):
        """re.match with $ would accept these; every check here is a fullmatch (LESSONS)."""
        self.refused({"groups": [dict(self.GROUP, name="Main wall\n")]})
        self.refused({"groups": [dict(self.GROUP, id="0123abcd\n")]})
        self.refused({"groups": [dict(self.GROUP, projectors=["0123abcd\n"])]})
        self.refused({"scenes": [self.scene(name="Movie\n")]})
        self.refused({"scenes": [self.scene(groups=[{"group": "all", "input": "31\n"}])]})
        self.refused({"scenes": [self.scene(groups=[{"group": "0123abcd\n", "power": "on"}])]})
        self.refused({"scenes": [self.scene(box={"action": "stream", "stream": "0123abcd\n"})]})
        self.refused({"scenes": [self.scene(box={"action": "file", "file": "a.mp4\n"})]})
        with self.assertRaises(scheduler.ScheduleError):
            scheduler.validate({"entries": [{"time": "10:00", "days": [1], "action": "scene", "scene": "0123abcd\n"}]})
        with self.assertRaises(midi.MidiError):
            midi.validate_entry({"kind": "note", "number": 1, "action": "scene", "scene": "0123abcd\n"})

    def test_names_and_ids_are_not_repeated_and_all_is_taken(self):
        self.refused({"groups": [self.GROUP, dict(self.GROUP, name="main WALL")]})
        self.refused({"groups": [dict(self.GROUP, id="0123abcd"), dict(self.GROUP, name="Other", id="0123abcd")]})
        self.refused({"groups": [dict(self.GROUP, name="All")]})
        self.refused({"scenes": [self.scene(), self.scene()]})
        self.refused({"scenes": [self.scene(groups=[{"group": "all", "power": "on"}, {"group": "all", "power": "off"}])]})
        self.refused({"groups": [dict(self.GROUP, name="Main‮wall")]})        # a right-to-left override

    def test_names_that_look_empty_or_like_another_are_refused(self):
        for name in ("\u2800", "\u0301\u0301", "...", "\u0410ll", "A ll", "\uff21\uff4c\uff4c", "a\u04cf\u04cf", "ALL "):
            self.refused({"groups": [dict(self.GROUP, name=name)]})
        self.refused({"groups": [self.GROUP, dict(self.GROUP, name="Main  Wall")]})
        self.refused({"groups": [self.GROUP, dict(self.GROUP, name="\uff2dain wall")]})        # a full-width M
        self.refused({"scenes": [self.scene(), self.scene(name="MOVIE")]})
        self.refused({"scenes": [self.scene(name="\u2800")]})
        self.assertEqual(room.validate({"scenes": [self.scene(name="All")]})["scenes"][0]["name"], "All")     # only groups keep "All" free
        self.assertEqual(room.validate({"groups": [dict(self.GROUP, name="Wall 2")]})["groups"][0]["name"], "Wall 2")

    def test_what_a_group_gets_in_a_scene(self):
        for row in ({"group": "all", "power": "maybe"}, {"group": "all", "input": "99"}, {"group": "all", "picture": True},
                    {"group": "everything"}, {"group": "all", "power": "off", "input": "31"},
                    {"group": "all", "power": "off", "sound": "mute"}, "all"):
            self.refused({"scenes": [self.scene(groups=[row])]})

    def test_the_box_does_only_what_is_on_the_list(self):
        self.assertEqual(sorted(room.BOX), ["blackout", "file", "leave", "pad", "stop", "stream", "vibes", "vibes_stop"])
        for box in ({"action": "reboot"}, {"action": "file", "file": "../etc/passwd"}, {"action": "file", "file": "notes.txt"},
                    {"action": "file", "file": "a.mp4", "loop": "yes"}, {"action": "pad", "pad": [0]}, {"action": "pad", "pad": [0, 12]},
                    {"action": "pad", "pad": [True, 1]}, {"action": "stream"}, {"action": ["stop"]}, "stop"):
            self.refused({"scenes": [self.scene(box=box)]})
        ok = room.validate({"scenes": [self.scene(box={"action": "file", "file": "a.mp4", "extra": 1})]})["scenes"][0]["box"]
        self.assertEqual(ok, {"action": "file", "file": "a.mp4", "loop": True})
        self.assertEqual(room.validate({"scenes": [self.scene(box=None)]})["scenes"][0]["box"], {"action": "leave"})


class RoomBase(ServerBase):
    def setUp(self):
        super().setUp()
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)
        mon = self.api.projectors
        mon.interval, mon.changing, mon.stagger, mon.retry_for, mon.retry_every = 0.15, 0.05, 0.0, 4.0, 0.1
        self.api.room.tick = 0.02
        self.api._pjlink = lambda e: projector.PJLink(e["host"], e["port"], e["password"], timeout=1.0)
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.assertEqual(self.post("/api/room/scene", {"number": 1})[0], 409)                 # module off
        self.assertEqual(self.post("/api/modules/room", {"enabled": True})[0], 409)           # it needs Projector control
        self.post("/api/modules/projector", {"enabled": True})
        self.assertEqual(self.post("/api/modules/room", {"enabled": True})[0], 200)
        self.addCleanup(self.api.projectors.stop, True)
        self.addCleanup(self.api.room.stop)

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def state(self, token=None):
        st, body, _ = self.call("GET", "/api/room", token=token or self.full)
        self.assertEqual(st, 200, body)
        return body

    def projector(self, name, fake=None, port=None, **kw):
        """Add a fake projector (or a dead port) under System > Projectors; (fake, its id)."""
        if fake is None and port is None:
            fake = FakeProjector(**kw)
        if fake is not None:
            self.addCleanup(fake.close)
        st, body, _ = self.post("/api/projectors", {"add": {"name": name, "host": "127.0.0.1", "port": port or fake.port}})
        self.assertEqual(st, 200, body)
        pid = [p["id"] for p in body["projectors"] if p["name"] == name][0]
        if fake is not None:
            self.assertTrue(wait_for(lambda: (self.entry(pid).get("details") or {}).get("inputs")), "the input list was read")
        return fake, pid

    def entry(self, pid):
        return [p for p in self.settings.data["projectors"] if p["id"] == pid][0]

    def group(self, name, pids):
        st, body, _ = self.post("/api/room", {"group": {"name": name, "projectors": pids}})
        self.assertEqual(st, 200, body)
        return [g["id"] for g in body["groups"] if g["name"] == name][0]

    def scene(self, name, rows, box=None):
        st, body, _ = self.post("/api/room", {"scene": {"name": name, "groups": rows, "box": box or {"action": "leave"}}})
        self.assertEqual(st, 200, body)
        return [s["id"] for s in body["scenes"] if s["name"] == name][0]

    def job(self):
        return self.state()["job"]

    def finished(self):
        return wait_for(lambda: not self.job()["running"], 8.0)

    @staticmethod
    def dead_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port


class GroupTest(RoomBase):
    def test_groups_are_edited_by_full_access_and_all_needs_no_entry(self):
        a, pa = self.projector("Left")
        b, pb = self.projector("Right")
        main = self.group("Main wall", [pa])
        paint = self.group("Painting wall", [pb])
        d = self.state()
        self.assertEqual([(g["name"], g["projectors"]) for g in d["groups"]], [("Main wall", [pa]), ("Painting wall", [pb])])
        self.assertEqual((d["all"]["id"], d["all"]["name"], d["all"]["projectors"]), ("all", "All", [pa, pb]))
        st, body, _ = self.post("/api/room", {"group": {"id": main, "name": "Backdrop", "projectors": [pa, pb]}})      # replaced by id
        self.assertEqual([(g["id"], g["name"], g["projectors"]) for g in body["groups"]][0], (main, "Backdrop", [pa, pb]))
        self.assertEqual(self.post("/api/room", {"group": {"id": "ffffffff", "name": "X", "projectors": [pa]}})[0], 404)
        self.assertEqual(self.post("/api/room", {"group": {"name": "Ghost", "projectors": ["ffffffff"]}})[0], 400)    # not a projector
        self.assertEqual(self.post("/api/room", {"group": {"name": "Backdrop", "projectors": [pa]}})[0], 400)         # the name is taken
        self.assertEqual(self.post("/api/room", {"nonsense": 1})[0], 400)
        sid = self.scene("Evening", [{"group": paint, "power": "on"}, {"group": main, "power": "on"}])
        st, body, _ = self.post("/api/room", {"remove_group": paint})
        self.assertEqual([g["id"] for g in body["groups"]], [main])
        self.assertEqual([r["group"] for r in body["scenes"][0]["groups"]], [main])       # the scene is kept, without that group
        self.assertEqual(self.post("/api/room", {"remove_group": paint})[0], 404)
        self.assertEqual(self.post("/api/room", {"remove_scene": sid})[1]["scenes"], [])
        self.assertEqual(self.post("/api/room", {"remove_scene": sid})[0], 404)
        text = str(self.state())
        self.assertNotIn("127.0.0.1", text)                                               # no address on the Room screen
        self.assertNotIn("password", text)

    def test_count_limits_at_the_api(self):
        _, pa = self.projector("Left")
        for i in range(room.MAX_GROUPS):
            self.group("G%d" % i, [pa])
        self.assertEqual(self.post("/api/room", {"group": {"name": "One more", "projectors": [pa]}})[0], 400)
        for i in range(room.MAX_SCENES):
            self.scene("S%d" % i, [])
        self.assertEqual(self.post("/api/room", {"scene": {"name": "One more", "groups": []}})[0], 400)
        self.assertEqual(len(self.settings.data["room"]["scenes"]), room.MAX_SCENES)

    def test_a_group_button_reaches_its_projectors_only_and_all_reaches_every_one(self):
        a, pa = self.projector("Left")
        b, pb = self.projector("Right")
        main = self.group("Main wall", [pa])
        st, body, _ = self.post("/api/room/group", {"group": main, "action": "on"})
        self.assertEqual((st, body["started"], body["name"]), (200, True, "Main wall"))
        self.assertTrue(wait_for(lambda: a.power == "1"))
        self.assertEqual(b.power, "0")
        self.assertTrue(wait_for(lambda: self.state()["groups"][0]["state"] == "on"))
        self.assertTrue(wait_for(lambda: self.state()["groups"][0]["last"]["text"] == "Main wall: on."))
        self.assertEqual(self.state()["all"]["state"], "mixed")
        self.assertEqual(self.post("/api/room/group", {"group": "all", "action": "on"})[0], 200)
        self.assertTrue(wait_for(lambda: b.power == "1"))
        self.assertTrue(wait_for(lambda: self.state()["all"]["state"] == "on"))
        # the source by its label, the mutes apart, and by number or name as a controller sends them
        self.post("/api/projectors", {"label": {"id": pa, "input": "32", "label": "Console"}})
        self.assertEqual([i["label"] or i["name"] for i in self.state()["groups"][0]["inputs"]], ["RGB 1", "Digital 1", "Console"])
        self.assertEqual(self.post("/api/room/group", {"number": 1, "action": "input", "input": "32"})[0], 200)
        self.assertTrue(wait_for(lambda: a.input == "32"))
        self.assertTrue(wait_for(lambda: self.state()["groups"][0]["text"] == "On · Console"), self.state()["groups"][0])
        self.assertEqual(self.post("/api/room/group", {"name": "main wall", "action": "mute_picture"})[0], 200)
        self.assertTrue(wait_for(lambda: a.video_mute and not a.audio_mute))
        self.assertTrue(wait_for(lambda: self.state()["groups"][0]["mute"] == {"picture": True, "sound": False}))
        self.assertEqual(self.post("/api/room/group", {"number": 0, "action": "off"})[0], 200)
        self.assertTrue(wait_for(lambda: a.power == "0" and b.power == "0"))
        self.assertTrue(wait_for(lambda: self.state()["all"]["state"] == "off"))
        # refusals
        self.assertEqual(self.post("/api/room/group", {"group": main, "action": "explode"})[0], 400)
        self.assertEqual(self.post("/api/room/group", {"group": main, "action": "input", "input": "55"})[0], 400)     # nobody has it
        self.assertEqual(self.post("/api/room/group", {"group": main, "action": "input", "input": "31\n"})[0], 400)
        self.assertEqual(self.post("/api/room/group", {"group": "ffffffff", "action": "on"})[0], 404)
        self.assertEqual(self.post("/api/room/group", {"number": 9, "action": "on"})[0], 404)
        self.assertEqual(self.post("/api/room/group", {"number": False, "action": "off"})[0], 404)
        self.assertEqual(self.post("/api/room/group", {"action": "on"})[0], 400)

    def test_roles(self):
        _, pa = self.projector("Left")
        main = self.group("Main wall", [pa])
        sid = self.scene("Evening", [{"group": main, "power": "on"}])
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        live = self.post("/api/devices/invite", {"name": "p", "role": "live"})[1]["token"]
        self.assertEqual(self.state(view)["scenes"][0]["name"], "Evening")                # a guest sees the state
        for path, body in (("/api/room/scene", {"scene": sid}), ("/api/room/group", {"group": main, "action": "on"})):
            self.assertEqual(self.post(path, body, token=view)[0], 403)
            self.assertEqual(self.post(path, body, token=live)[0], 200)
        for token in (view, live):
            self.assertEqual(self.post("/api/room", {"group": {"name": "Mine", "projectors": [pa]}}, token=token)[0], 403)
            self.assertEqual(self.post("/api/room", {"remove_scene": sid}, token=token)[0], 403)
        self.assertEqual(self.call("GET", "/api/room")[0], 401)

    def test_the_state_of_a_group_at_a_glance(self):
        a, pa = self.projector("Left", slow=True)
        _, pd = self.projector("Gone", port=self.dead_port())
        main, gone = self.group("Main wall", [pa]), self.group("Painting wall", [pd])
        word = lambda i: self.state()["groups"][i]["state"]
        self.assertTrue(wait_for(lambda: word(0) == "off"))
        self.assertTrue(wait_for(lambda: word(1) == "no answer"))
        self.post("/api/room/group", {"group": main, "action": "on"})
        self.assertTrue(wait_for(lambda: word(0) == "warming up"))
        a.finish()
        self.assertTrue(wait_for(lambda: word(0) == "on"))
        self.post("/api/room/group", {"group": main, "action": "off"})
        self.assertTrue(wait_for(lambda: word(0) == "cooling down"))
        self.assertTrue(wait_for(lambda: self.state()["all"]["text"] == "Cooling down (Gone: no answer)"), self.state()["all"])
        a.finish()
        self.assertTrue(wait_for(lambda: word(0) == "off"))

    def test_an_older_settings_file_has_no_room_section(self):
        _, pa = self.projector("Left")
        with self.settings.lock:
            self.settings.data.pop("room", None)
        d = self.state()
        self.assertEqual((d["groups"], d["scenes"], d["all"]["projectors"]), ([], [], [pa]))
        self.group("Main wall", [pa])
        self.assertEqual(self.settings.data["room"]["groups"][0]["name"], "Main wall")
        self.assertEqual(self.settings.data["schema"], 14)


class SceneTest(RoomBase):
    def test_a_scene_goes_power_then_input_then_mutes_and_waits_for_the_warm_up(self):
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        self.post("/api/projectors", {"label": {"id": pa, "input": "32", "label": "Console"}})
        sid = self.scene("Console night", [{"group": main, "power": "on", "input": "32", "picture": "unmute", "sound": "mute"}])
        start = time.monotonic()
        st, body, _ = self.post("/api/room/scene", {"scene": sid})
        self.assertEqual((st, body["started"], body["name"]), (200, True, "Console night"))
        self.assertLess(time.monotonic() - start, 0.5)                                    # nobody waits for a projector
        self.assertTrue(wait_for(lambda: a.power == "3"))
        self.assertTrue(wait_for(lambda: self.job()["text"] == "Main wall: switching on (warming up), waiting to switch to Console."), self.job())
        self.assertTrue(self.job()["running"])
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))     # Phase 1's retry has it
        time.sleep(0.4)
        self.assertEqual([c for c in sets(a) if c.startswith("AVMT")], [])                # no mute before the input is in
        a.finish()
        self.assertTrue(self.finished(), self.job())
        self.assertEqual((a.power, a.input, a.video_mute, a.audio_mute), ("1", "32", False, True))
        job = self.job()
        self.assertEqual((job["ok"], job["text"]), (True, "Main wall: on, input Console, picture shown, sound muted."))
        done = sets(a)
        order = [done.index("POWR 1"), len(done) - 1 - done[::-1].index("INPT 32"), done.index("AVMT 10"), done.index("AVMT 21")]
        self.assertEqual(order, sorted(order), done)
        self.assertTrue(wait_for(lambda: not room_threads()))

    def test_picture_and_sound_the_same_way_are_one_command(self):
        a, pa = self.projector("Left", separate_mute=False)             # a projector that can only mute both together
        main = self.group("Main wall", [pa])
        self.scene("Dark", [{"group": main, "power": "on", "picture": "mute", "sound": "mute"}])
        self.post("/api/room/scene", {"name": "dark"})
        self.assertTrue(self.finished())
        self.assertEqual((sets(a), a.video_mute, a.audio_mute), (["POWR 1", "AVMT 31"], True, True))
        self.assertEqual(self.job()["text"], "Main wall: on, picture muted, sound muted.")
        self.post("/api/room/group", {"group": main, "action": "unmute_sound"})
        self.assertTrue(wait_for(lambda: "cannot mute the picture and the sound separately" in (self.state()["groups"][0]["last"] or {}).get("text", "")))

    def test_a_second_scene_replaces_the_first_cleanly(self):
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        first = self.scene("Console", [{"group": main, "power": "on", "input": "32", "picture": "mute"}])
        second = self.scene("Film", [{"group": main, "power": "on", "input": "31"}])
        self.post("/api/room/scene", {"scene": first})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.post("/api/room/scene", {"scene": second})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "31"), self.job())
        mark = len(a.received)
        a.finish()
        self.assertTrue(self.finished(), self.job())
        self.assertEqual((a.input, a.video_mute), ("31", False))
        after = [line[2:] for line in a.received[mark:]]
        self.assertNotIn("INPT 32", after)                                                # the first scene's input is not tried again
        self.assertEqual([c for c in sets(a) if c.startswith("AVMT")], [])                # and its mute never goes out
        job = self.job()
        self.assertEqual((job["scene"], job["ok"], job["text"]), (second, True, "Main wall: on, input Digital 1."))
        self.assertIsNone(self.api.projectors.status(pa)["pending_input"])

    def test_a_retry_already_on_its_way_never_overrides_the_newer_scene(self):
        """The Phase 1 review finding, for scenes: hold the first scene's retry in the gap between "is it still
        wanted" and the send, apply the second scene, then let go. The newer input must be the last one sent."""
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        first = self.scene("Console", [{"group": main, "power": "on", "input": "32"}])
        second = self.scene("Film", [{"group": main, "input": "31"}])
        armed, entered, gate = threading.Event(), threading.Event(), threading.Event()

        class Held(projector.PJLink):
            def set_input(self, code):
                if code == "32" and armed.is_set() and not entered.is_set():
                    entered.set()
                    gate.wait(5)
                return super().set_input(code)
        self.api._pjlink = lambda e: Held(e["host"], e["port"], e["password"], timeout=1.0)
        self.post("/api/room/scene", {"scene": first})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        armed.set()
        self.assertTrue(entered.wait(5))                                                  # the retry is in the gap
        a.finish()                                                                        # the projector would now take it
        self.post("/api/room/scene", {"scene": second})
        time.sleep(0.3)
        self.assertNotIn("INPT 31", sets(a))                                              # the newer one waits its turn
        gate.set()
        self.assertTrue(wait_for(lambda: a.input == "31"), sets(a))
        self.assertTrue(self.finished(), self.job())
        self.assertEqual([c for c in sets(a) if c.startswith("INPT")][-1], "INPT 31")
        time.sleep(0.5)
        self.assertEqual(a.input, "31")                                                   # and nothing follows it
        self.assertIsNone(self.api.projectors.status(pa)["pending_input"])

    def test_a_button_outside_the_room_is_also_a_newer_choice(self):
        """A room step that is waiting must not land after Off on the Projectors card, /beameroff or the schedule,
        nor a scene's source after a newer pick in the card's input chooser."""
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        on = self.scene("On", [{"group": main, "power": "on", "input": "32", "picture": "mute"}])
        sched = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        offs = (lambda: self.api.handle("POST", *osc.translate("/beameroff", [1.0]), osc.OSC_DEVICE, "192.168.0.9"),      # OSC
                lambda: self.post("/api/projector", {"id": pa, "action": "off"}),                                        # the Projectors card
                lambda: sched._execute({"id": "e1", "action": "projector_off"}, datetime.datetime(2026, 10, 3, 23, 0)))   # the schedule
        for press_off in offs:
            self.post("/api/projector", {"id": pa, "action": "on"})
            a.finish()
            self.post("/api/projector", {"id": pa, "action": "off"})
            self.assertEqual(a.power, "2")                                                # cooling down: it refuses everything
            before = sets(a).count("POWR 1")
            self.post("/api/room/scene", {"scene": on})
            self.assertTrue(wait_for(lambda: sets(a).count("POWR 1") >= before + 3), sets(a))     # the room keeps asking
            press_off()
            self.assertTrue(wait_for(lambda: not self.job()["running"]))
            time.sleep(0.3)                                                               # a command already on the wire is over by now
            mark = len(a.received)
            a.finish()
            time.sleep(0.6)
            self.assertEqual([line[2:] for line in a.received[mark:] if not line.endswith("?")], [])
            self.assertEqual((a.power, self.job()["text"]), ("0", "Main wall: changed by a later choice."))
        # the same for a source: the scene's is still to come when the card's chooser picks another
        self.post("/api/room/scene", {"scene": on})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.assertEqual(self.post("/api/projector", {"id": pa, "action": "input", "input": "31"})[1]["results"][pa]["pending"], True)
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "31"))
        time.sleep(0.3)
        self.assertEqual(self.api.projectors.status(pa)["pending_input"], "31")           # the room did not stop the newer retry
        a.finish()
        self.assertTrue(wait_for(lambda: a.input == "31" and a.video_mute), sets(a))      # the scene's mute still goes: nobody chose otherwise
        self.assertTrue(self.finished())
        self.assertNotIn("INPT 32", sets(a)[sets(a).index("INPT 31"):])
        self.assertEqual(self.job()["text"], "Main wall: on, picture muted, changed by a later choice.")

    def test_a_scene_for_other_projectors_also_ends_the_first_one(self):
        a, pa = self.projector("Left", slow=True)
        b, pb = self.projector("Right")
        main, paint = self.group("Main wall", [pa]), self.group("Painting wall", [pb])
        first = self.scene("Console", [{"group": main, "power": "on", "input": "32"}])
        second = self.scene("Painting only", [{"group": paint, "power": "on"}])
        self.post("/api/room/scene", {"scene": first})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.post("/api/room/scene", {"scene": second})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] is None))
        self.assertTrue(wait_for(lambda: b.power == "1"))
        a.finish()
        time.sleep(0.5)
        self.assertEqual(a.input, "11")                                                   # the dropped input never arrives
        self.assertEqual(self.job()["text"], "Painting wall: on.")
        self.assertTrue(wait_for(lambda: not room_threads()))

    def test_a_dead_projector_says_no_answer_and_holds_nobody_up(self):
        a, pa = self.projector("Left")
        _, pd = self.projector("Gone", port=self.dead_port())
        main, paint = self.group("Main wall", [pa]), self.group("Painting wall", [pd])
        self.post("/api/projectors", {"label": {"id": pa, "input": "31", "label": "Console"}})
        sid = self.scene("Evening", [{"group": main, "power": "on", "input": "31"}, {"group": paint, "power": "on", "input": "31"}])
        start = time.monotonic()
        self.assertEqual(self.post("/api/room/scene", {"scene": sid})[0], 200)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertTrue(self.finished(), self.job())
        job = self.job()
        self.assertEqual((job["ok"], job["text"]), (False, "Main wall: on, input Console. Painting wall: no answer."))
        self.assertEqual((a.power, a.input), ("1", "31"))
        # two projectors in one group that differ are named
        both = self.group("Both", [pa, pd])
        self.post("/api/room/group", {"group": both, "action": "on"})
        self.assertTrue(wait_for(lambda: (self.state()["groups"][2]["last"] or {}).get("text") == "Both: Left: on; Gone: no answer."),
                        self.state()["groups"][2])

    def test_a_group_button_joins_a_scene_in_progress_and_off_ends_it(self):
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        sid = self.scene("Console", [{"group": main, "power": "on", "input": "32"}])
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.post("/api/room/group", {"group": main, "action": "mute_picture"})           # only its own kind of step
        time.sleep(0.3)
        self.assertEqual(self.api.projectors.status(pa)["pending_input"], "32")
        a.finish()
        self.assertTrue(wait_for(lambda: a.input == "32" and a.video_mute), sets(a))
        self.assertTrue(self.finished())
        self.assertEqual(self.job()["text"], "Main wall: on, input Digital 2.")
        # Off replaces whatever is still to come
        self.post("/api/room/group", {"group": main, "action": "off"})
        self.assertTrue(wait_for(lambda: a.power == "2"))
        a.finish()
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.post("/api/room/group", {"group": main, "action": "off"})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] is None))
        mark = len(a.received)
        a.finish()
        self.assertTrue(wait_for(lambda: a.power in ("2", "0")), sets(a))
        time.sleep(0.3)
        self.assertNotIn("INPT 32", [line[2:] for line in a.received[mark:]])
        self.assertTrue(wait_for(lambda: not self.job()["running"]))
        self.assertEqual(self.job()["text"], "Main wall: on, changed by a later choice.")
        last = lambda: self.state()["groups"][0]["last"]
        self.assertEqual((last()["text"], last()["running"]), ("Main wall: switching off (cooling down).", True))
        a.finish()
        self.assertTrue(wait_for(lambda: (last()["text"], last()["running"]) == ("Main wall: off.", False)), last())

    def test_many_taps_never_pile_up_threads(self):
        a, pa = self.projector("Left", slow=True)
        b, pb = self.projector("Right", slow=True)
        main = self.group("Main wall", [pa, pb])
        one = self.scene("One", [{"group": main, "power": "on", "input": "31"}])
        two = self.scene("Two", [{"group": "all", "power": "on", "input": "32", "sound": "mute"}])
        most = 0
        for i in range(20):
            self.assertEqual(self.post("/api/room/scene", {"scene": one if i % 2 else two})[0], 200)
            self.post("/api/room/group", {"group": "all", "action": "mute_picture"})
            most = max(most, len(room_threads()))
        self.assertLessEqual(most, 2)                                                     # one per projector, never more
        self.assertLessEqual(len(self.api.room.threads()), 2)
        pending = lambda pid: self.api.projectors.status(pid)["pending_input"]
        self.assertTrue(wait_for(lambda: (a.power, b.power, pending(pa), pending(pb)) == ("3", "3", "31", "31")), self.job())
        a.finish()
        b.finish()
        self.assertTrue(self.finished(), self.job())
        self.assertTrue(wait_for(lambda: not room_threads(), 8.0))
        self.assertEqual((a.input, b.input), ("31", "31"), (self.state(), sets(a)[-12:], sets(b)[-12:]))      # the last scene tapped was "One"

    def test_switching_the_module_off_ends_a_scene_and_nothing_more_is_sent(self):
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        sid = self.scene("Console", [{"group": main, "power": "on", "input": "32", "picture": "mute"}])
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(wait_for(lambda: self.api.projectors.status(pa)["pending_input"] == "32"))
        self.assertEqual(self.post("/api/modules/room", {"enabled": False})[0], 200)
        self.assertTrue(wait_for(lambda: not room_threads()))
        self.assertIsNone(self.api.projectors.status(pa)["pending_input"])                # the retry was ended too
        a.finish()
        time.sleep(0.5)
        self.assertEqual((a.input, a.video_mute), ("11", False))
        self.assertEqual(self.post("/api/room/scene", {"scene": sid})[0], 409)
        self.assertFalse(self.call("GET", "/api/room", token=self.full)[1]["enabled"])

    def test_a_projector_that_stays_off_is_said_plainly_and_a_missing_input_too(self):
        a, pa = self.projector("Left")
        main = self.group("Main wall", [pa])
        self.post("/api/room/group", {"group": main, "action": "mute_sound"})             # it is off: no minute and a half of waiting
        self.assertTrue(wait_for(lambda: (self.state()["groups"][0]["last"] or {}).get("text") == "Main wall: it is switched off."),
                        self.state()["groups"][0])
        b, pb = self.projector("Right", inputs=("11", "31"))
        both = self.group("Both", [pa, pb])
        self.post("/api/room/group", {"group": both, "action": "on"})
        self.assertTrue(wait_for(lambda: a.power == "1" and b.power == "1"))
        self.post("/api/room/group", {"group": both, "action": "input", "input": "32"})
        self.assertTrue(wait_for(lambda: (self.state()["groups"][1]["last"] or {}).get("text") ==
                                 "Both: Left: input Digital 2; Right: has no input Digital 2."), self.state()["groups"][1])

    def test_what_the_box_does(self):
        a, pa = self.projector("Left")
        main = self.group("Main wall", [pa])
        self.assertEqual(self.post("/api/room", {"scene": {"name": "Gone", "groups": [], "box": {"action": "file", "file": "nope.mp4"}}})[0], 404)
        self.assertEqual(self.post("/api/room", {"scene": {"name": "Gone", "groups": [], "box": {"action": "stream", "stream": "0123abcd"}}})[0], 404)
        self.assertEqual(self.post("/api/room", {"scene": {"name": "Gone", "groups": [], "box": {"action": "poweroff"}}})[0], 400)
        self.assertEqual(self.post("/api/room", {"scene": {"name": "Gone", "groups": [{"group": "ffffffff", "power": "on"}]}})[0], 400)
        film = self.scene("Film", [{"group": main, "power": "on"}], {"action": "file", "file": "a.mp4", "loop": False})
        self.post("/api/blackout", {"on": True})
        st, body, _ = self.post("/api/room/scene", {"scene": film})
        self.assertEqual(body["box"], {"ok": True, "text": "playing a.mp4"})
        self.assertEqual((self.player.plays[-1]["paths"][0].endswith("a.mp4"), self.player.plays[-1]["loop"]), (True, False))
        self.assertFalse(self.api.mix["blackout"])                                        # a scene that plays shows the screen
        self.assertTrue(self.finished())
        self.assertEqual(self.job()["text"], "Main wall: on. Box: playing a.mp4.")
        self.scene("Close", [{"group": "all", "power": "off"}], {"action": "stop"})
        self.post("/api/room/scene", {"name": "Close"})
        self.assertIn(("clear",), self.player.calls)
        self.assertTrue(self.finished())
        self.assertEqual(self.job()["text"], "All: off. Box: stopped.")
        self.scene("Dark", [], {"action": "blackout"})
        self.post("/api/room/scene", {"number": 3})
        self.assertTrue(self.api.mix["blackout"])
        os.unlink(os.path.join(self.media, "a.mp4"))                                      # the clip was deleted since
        body = self.post("/api/room/scene", {"scene": film})[1]
        self.assertEqual(body["box"], {"ok": False, "text": "file not found"})
        self.assertTrue(self.finished())
        self.assertFalse(self.job()["ok"])
        for bad in ({"scene": "ffffffff"}, {"number": 99}, {"number": 0}, {"name": "nope"}, {"scene": ["x"]}):
            self.assertEqual(self.post("/api/room/scene", bad)[0], 404, bad)
        self.assertEqual(self.post("/api/room/scene", {})[0], 400)


class ReviewTest(RoomBase):
    """One test per finding of the independent review of PR #64."""

    def test_off_is_not_done_because_a_remembered_status_says_cooling_down(self):
        """Off, On (retried while it cools), cooled, On taken, it warms up; Off again before the next status check
        lands. The remembered status still says "cooling down": that is not proof. The step keeps asking."""
        a, pa = self.projector("Left", slow=True)
        mon = self.api.projectors
        self.post("/api/projector", {"id": pa, "action": "on"})
        a.finish()
        self.post("/api/room/group", {"group": "all", "action": "off"})
        self.assertTrue(wait_for(lambda: mon.status(pa).get("power") == "cooling down"))
        self.post("/api/room/group", {"group": "all", "action": "on"})
        self.assertTrue(wait_for(lambda: sets(a).count("POWR 1") >= 3))
        gate, real = threading.Event(), mon._poll

        def held(w, entry):                     # a status check that does not come back for a while
            gate.wait(10)
            return real(w, entry)
        mon._poll = held
        self.addCleanup(gate.set)
        time.sleep(0.3)
        a.finish()                              # cooled: the next retry of On is taken and it warms up
        self.assertTrue(wait_for(lambda: a.power == "3"))
        self.assertEqual(mon.status(pa).get("power"), "cooling down")                     # what the box remembers is now wrong
        offs = sets(a).count("POWR 0")
        self.post("/api/room/group", {"group": "all", "action": "off"})
        self.assertTrue(wait_for(lambda: sets(a).count("POWR 0") >= offs + 2))            # refused, and asked again
        last = lambda: self.state()["all"]["last"]
        self.assertEqual((last()["running"], last()["text"]), (True, "All: switching off."))
        a.finish()                              # warm: the next Off is taken
        self.assertTrue(wait_for(lambda: a.power == "2"), sets(a))
        mon._poll = real
        gate.set()
        self.assertTrue(wait_for(lambda: last()["text"] == "All: switching off (cooling down)."), last())
        a.finish()
        self.assertTrue(wait_for(lambda: (last()["running"], last()["text"]) == (False, "All: off.")), last())

    def hold_the_rooms_input(self):
        """Monitor.set_input, held for the room's thread just before it runs; (entered, gate)."""
        mon, real = self.api.projectors, self.api.projectors.set_input
        entered, gate = threading.Event(), threading.Event()

        def held(entry, code, **kw):
            if threading.current_thread().name == "room" and not entered.is_set():
                entered.set()
                gate.wait(5)
            return real(entry, code, **kw)
        mon.set_input = held
        self.addCleanup(gate.set)
        return entered, gate

    def test_a_scene_s_source_never_follows_a_newer_pick_on_the_projectors_card(self):
        a, pa = self.projector("Left")
        self.post("/api/projector", {"id": pa, "action": "on"})
        main = self.group("Main wall", [pa])
        sid = self.scene("Console", [{"group": main, "input": "32"}])
        entered, gate = self.hold_the_rooms_input()
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(entered.wait(5))                                                  # the room is about to send 32
        self.assertEqual(self.post("/api/projector", {"id": pa, "action": "input", "input": "31"})[0], 200)
        gate.set()
        self.assertTrue(self.finished())
        time.sleep(0.4)
        self.assertEqual(([c for c in sets(a) if c.startswith("INPT")], a.input), (["INPT 31"], "31"))
        self.assertEqual(self.job()["text"], "Main wall: changed by a later choice.")

    def test_the_room_does_not_stop_the_card_s_retry_of_the_same_source(self):
        a, pa = self.projector("Left", slow=True)
        self.post("/api/projector", {"id": pa, "action": "on"})                           # warming up: every input change is retried
        main = self.group("Main wall", [pa])
        sid = self.scene("Console", [{"group": main, "input": "32"}])
        entered, gate = self.hold_the_rooms_input()
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(entered.wait(5))
        self.assertTrue(self.post("/api/projector", {"id": pa, "action": "input", "input": "32"})[1]["results"][pa]["pending"])
        gate.set()
        self.assertTrue(self.finished())
        self.assertTrue(wait_for(lambda: not room_threads()))
        self.assertEqual(self.api.projectors.status(pa)["pending_input"], "32")           # the card's retry is still there
        a.finish()
        self.assertTrue(wait_for(lambda: a.input == "32"))

    def test_the_same_scene_over_and_over_still_gets_its_commands_through(self):
        """A controller that repeats itself, or an impatient thumb: twenty a second for two seconds."""
        a, pa = self.projector("Left")
        main = self.group("Main wall", [pa])
        sid = self.scene("Film", [{"group": main, "power": "on", "input": "32", "sound": "mute"}], {"action": "file", "file": "a.mp4"})
        connections, end, during = a.connections, time.monotonic() + 2.0, None
        while time.monotonic() < end:
            self.assertEqual(self.api.handle("POST", "/api/room/scene", {"scene": sid}, osc.OSC_DEVICE, "192.168.0.9")[0], 200)
            if "POWR 1" in sets(a) and during is None:
                during = time.monotonic()
            time.sleep(0.05)
        self.assertIsNotNone(during, "no power command got through during the flood")
        self.assertTrue(self.finished(), self.job())
        self.assertEqual((a.power, a.input, a.audio_mute), ("1", "32", True))
        self.assertGreaterEqual(min(sets(a).count("POWR 1"), sets(a).count("INPT 32"), sets(a).count("AVMT 21")), 1)
        self.assertLessEqual(a.connections - connections, len(a.received) + 3)            # no connections opened and abandoned
        self.assertEqual(len(self.player.plays), 1)                                       # and the clip started once
        self.api.room._last_box = None                                                    # (as if) a while later: it plays again
        self.post("/api/room/scene", {"scene": sid})
        self.assertEqual(len(self.player.plays), 2)

    def test_removing_a_scene_or_a_group_ends_what_it_still_had_to_send(self):
        a, pa = self.projector("Left", slow=True)
        main = self.group("Main wall", [pa])
        sid = self.scene("Console", [{"group": main, "power": "on", "input": "32", "picture": "mute"}])
        pending = lambda: self.api.projectors.status(pa)["pending_input"]
        self.post("/api/room/scene", {"scene": sid})
        self.assertTrue(wait_for(lambda: pending() == "32"))
        self.assertEqual(self.post("/api/room", {"remove_scene": sid})[0], 200)
        self.assertTrue(wait_for(lambda: pending() is None and not room_threads()))
        self.post("/api/room/group", {"group": main, "action": "input", "input": "31"})
        self.post("/api/room/group", {"group": main, "action": "mute_sound"})
        self.assertTrue(wait_for(lambda: pending() == "31"))
        self.assertEqual(self.post("/api/room", {"remove_group": main})[0], 200)
        self.assertTrue(wait_for(lambda: pending() is None and not room_threads()))
        mark = len(a.received)
        a.finish()
        time.sleep(0.5)
        self.assertEqual([line[2:] for line in a.received[mark:] if not line.endswith("?")], [])
        self.assertEqual((a.input, a.video_mute, a.audio_mute), ("11", False, False))

    def test_more_than_one_thing_in_one_edit_is_refused(self):
        _, pa = self.projector("Left")
        group, scene = {"name": "Main wall", "projectors": [pa]}, {"name": "Film", "groups": []}
        for body in ({"group": group, "scene": scene}, {"group": group, "remove_scene": "0123abcd"},
                     {"scene": scene, "remove_group": "0123abcd"}, {"remove_group": "0123abcd", "remove_scene": "0123abcd"}, {}):
            st, out, _ = self.post("/api/room", body)
            self.assertEqual((st, out["error"]), (400, "send one of group, remove_group, scene or remove_scene"), body)
        self.assertEqual(self.settings.data["room"], {"groups": [], "scenes": []})

    def test_a_command_that_never_comes_back_does_not_hold_off_back(self):
        """A name lookup that hangs is the one step nobody can bound. The thread stuck in it is left behind (its
        command is never sent) and Off goes out from a fresh one; still never more threads than projectors."""
        a, pa = self.projector("Left")
        main = self.group("Main wall", [pa])
        self.api.room.stuck = 0.3
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        class Hung(projector.PJLink):
            def power(self, on):
                if on and not entered.is_set():
                    entered.set()
                    gate.wait(10)
                return super().power(on)
        self.api._pjlink = lambda e: Hung(e["host"], e["port"], e["password"], timeout=1.0)
        self.post("/api/room/group", {"group": main, "action": "on"})
        self.assertTrue(entered.wait(5))
        time.sleep(0.5)
        self.post("/api/room/group", {"group": main, "action": "off"})
        self.assertTrue(wait_for(lambda: "POWR 0" in sets(a)), sets(a))                   # Off did not wait for the stuck thread
        self.assertLessEqual(len(room_threads()), 2)
        self.assertLessEqual(len(self.api.room.threads()), projector.MAX_PROJECTORS)
        mark = len(a.received)
        gate.set()
        self.assertTrue(wait_for(lambda: not room_threads()))
        self.assertNotIn("POWR 1", [line[2:] for line in a.received[mark:]])              # what it was stuck on is never sent

    def test_a_scene_through_the_support_tunnel_runs_whole(self):
        a, pa = self.projector("Left")
        sid = self.scene("Film", [{"group": "all", "power": "on"}], {"action": "file", "file": "a.mp4"})
        tunnel, support = "10.77.0.9", {"id": "support", "name": "Support", "role": "live", "remote": True}
        self.api.support.is_remote = lambda client: client == tunnel
        st, out = self.api.handle("POST", "/api/room/scene", {"scene": sid}, support, tunnel)
        self.assertEqual((st, out["box"]), (200, {"ok": True, "text": "playing a.mp4"}))
        self.assertTrue(wait_for(lambda: a.power == "1"))
        self.assertEqual(len(self.player.plays), 1)
        # what a support login may not do stays refused, and a paired device's token does not work through the tunnel
        self.assertEqual(self.api.handle("POST", "/api/room", {"remove_scene": sid}, support, tunnel)[0], 403)
        self.assertEqual(self.api.handle("POST", "/api/room/scene", {"scene": sid}, {"id": "x", "role": "live"}, tunnel)[0], 403)


class VibesBoxTest(RoomBase):
    def test_a_scene_starts_and_stops_vibes_through_its_own_api(self):
        self.scene("Ambience", [], {"action": "vibes"})
        self.scene("Quiet", [], {"action": "vibes_stop"})
        body = self.post("/api/room/scene", {"name": "Ambience"})[1]                      # the Shaders and Vibes module is off
        self.assertEqual(body["box"], {"ok": False, "text": "turn on the Shaders and Vibes module in System first"})
        asked = []
        self.api.vibes.api_vibes = lambda b, device, client: asked.append((b, device["role"])) or {"running": b["on"]}
        self.post("/api/blackout", {"on": True})
        self.assertEqual(self.post("/api/room/scene", {"name": "Ambience"})[1]["box"], {"ok": True, "text": "Vibes"})
        self.assertFalse(self.api.mix["blackout"])
        self.assertEqual(self.post("/api/room/scene", {"name": "Quiet"})[1]["box"], {"ok": True, "text": "Vibes stopped"})
        self.assertEqual(asked, [({"on": True}, "live"), ({"on": False}, "live")])
        self.assertEqual(self.job()["text"], "Box: Vibes stopped.")


class ScheduleOscMidiTest(RoomBase):
    def test_the_schedule_applies_a_scene_and_says_when_it_is_gone(self):
        a, pa = self.projector("Left")
        sid = self.scene("Evening", [{"group": "all", "power": "on"}])
        clean = scheduler.validate({"enabled": True, "entries": [{"time": "18:00", "days": [0], "action": "scene", "scene": sid}]})
        self.assertEqual(clean["entries"][0]["scene"], sid)
        for bad in ({}, {"scene": "Evening"}, {"scene": 5}, {"scene": sid + "0"}):
            with self.assertRaises(scheduler.ScheduleError):
                scheduler.validate({"entries": [dict({"time": "10:00", "days": [1], "action": "scene"}, **bad)]})
        s = scheduler.Scheduler(self.api, self.settings, self.api.registry, log=lambda *_: None)
        minute = datetime.datetime(2026, 10, 3, 18, 0)
        start = time.monotonic()
        s._run({"id": "e1", "action": "scene", "scene": sid}, minute)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual((s.last["e1"]["ok"], s.last["e1"]["message"]), (True, "done"))
        self.assertTrue(wait_for(lambda: a.power == "1"))
        s._run({"id": "e2", "action": "scene", "scene": "ffffffff"}, minute)
        self.assertEqual((s.last["e2"]["ok"], s.last["e2"]["message"]), (False, "no such scene"))
        self.post("/api/modules/room", {"enabled": False})
        s._run({"id": "e3", "action": "scene", "scene": sid}, minute)
        self.assertEqual((s.last["e3"]["ok"], s.last["e3"]["message"]), (False, "turn on the Room module in System first"))

    def test_osc_addresses(self):
        t = osc.translate
        self.assertEqual(t("/pvj/scene/2", [1.0]), ("/api/room/scene", {"number": 2}))
        self.assertIsNone(t("/pvj/scene/2", [0.0]))                                       # the release does nothing
        self.assertEqual(t("/pvj/scene", ["Evening"]), ("/api/room/scene", {"name": "Evening"}))
        self.assertEqual(t("/pvj/scene", [3]), ("/api/room/scene", {"number": 3}))
        self.assertEqual(t("/pvj/scene", [2.0]), ("/api/room/scene", {"number": 2}))        # a whole number sent as a float
        self.assertIsNone(t("/pvj/scene", [2.5]))
        self.assertIsNone(t("/pvj/scene", [float("nan")]))
        self.assertEqual(t("/pvj/group/all/off", [1.0]), ("/api/room/group", {"group": "all", "action": "off"}))
        self.assertEqual(t("/pvj/group/1/on", []), ("/api/room/group", {"number": 1, "action": "on"}))
        self.assertEqual(t("/pvj/group/2/mute", [1]), ("/api/room/group", {"number": 2, "action": "mute"}))
        self.assertEqual(t("/pvj/group/2/mute_sound", [0.0]), ("/api/room/group", {"number": 2, "action": "unmute_sound"}))
        self.assertEqual(t("/pvj/group/1/input", [31.0]), ("/api/room/group", {"number": 1, "action": "input", "input": "31"}))
        self.assertEqual(t("/pvj/group/1/input", ["32"]), ("/api/room/group", {"number": 1, "action": "input", "input": "32"}))
        for address, args in (("/pvj/scene/0", [1.0]), ("/pvj/scene/2\n", [1.0]), ("/pvj/scene", []), ("/pvj/scene", [True]),
                              ("/pvj/group/1/explode", [1.0]), ("/pvj/group/1/mute", []), ("/pvj/group/1/input", [float("inf")]),
                              ("/pvj/group/1/input", [31.5]), ("/pvj/group/x/on", [1.0]), ("/pvj/group/1/on/", [0.0]), ("/pvj/scenery", [1.0])):
            self.assertIsNone(t(address, args), address)
        # through the same API path, as the OSC device (a presenter): the scene runs, a full-access call is refused
        a, pa = self.projector("Left")
        self.group("Main wall", [pa])
        self.scene("Evening", [{"group": "all", "power": "on"}])
        server = osc.OscServer(self.api, log=lambda *_: None)
        handle = lambda address, args: self.api.handle("POST", *t(address, args), osc.OSC_DEVICE, "192.168.0.9")
        self.assertEqual(handle("/pvj/scene/1", [1.0])[0], 200)
        self.assertTrue(wait_for(lambda: a.power == "1"))
        self.assertEqual(handle("/pvj/group/1/input", [32.0])[0], 200)
        self.assertTrue(wait_for(lambda: a.input == "32"))
        self.assertEqual(handle("/pvj/scene/7", [1.0])[0], 404)
        self.assertEqual(self.api.handle("POST", "/api/room", {"remove_scene": "x"}, osc.OSC_DEVICE, "192.168.0.9")[0], 403)
        self.assertEqual(server.stats["handled"], 0)

    def test_a_midi_control_can_be_given_a_scene(self):
        a, pa = self.projector("Left")
        sid = self.scene("Evening", [{"group": "all", "power": "on"}])
        e = midi.validate_entry({"kind": "note", "number": 40, "action": "scene", "scene": sid})
        self.assertEqual(e["scene"], sid)
        for bad in ({}, {"scene": "Evening"}, {"scene": 4}):
            with self.assertRaises(midi.MidiError):
                midi.validate_entry(dict({"kind": "note", "number": 40, "action": "scene"}, **bad))
        calls = []
        mapper = midi.MidiMapper(lambda path, body: calls.append((path, body)) or True, [dict(e, channel=0)])
        mapper.message("pads", ("on", 0, 40, 100))
        self.assertEqual(calls, [("/api/room/scene", {"scene": sid})])
        self.assertEqual(self.api.handle("POST", calls[0][0], calls[0][1], midi.MIDI_DEVICE, "midi")[0], 200)
        self.assertTrue(wait_for(lambda: a.power == "1"))


class CancelInputTest(RoomBase):
    def test_only_the_named_retry_is_stopped(self):
        a, pa = self.projector("Left", slow=True)
        self.post("/api/projector", {"id": pa, "action": "on"})
        self.assertTrue(wait_for(lambda: a.power == "3"))
        mon = self.api.projectors
        self.assertEqual(mon.set_input(self.entry(pa), "32"), {"pending": True})
        self.assertFalse(mon.cancel_input(pa, "31"))                                      # another input: left alone
        self.assertEqual(mon.status(pa)["pending_input"], "32")
        self.assertTrue(mon.cancel_input(pa, "32"))
        self.assertIsNone(mon.status(pa)["pending_input"])
        self.assertFalse(mon.cancel_input("ffffffff", "32"))
        a.finish()
        time.sleep(0.4)
        self.assertEqual(a.input, "11")


if __name__ == "__main__":
    unittest.main()
