# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sound sources (D97): one input of a multi-input card, the box's own sound measured inside the player, the NDI
sound, levels sent in over OSC (Max/RNBO), who may switch between them, remote support, and the release before
(D96) reading the setting. Every signal is made here; a real mpv is used where the machine has one."""
import array
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave

from pvj import osc, oscfeedback as fb, render, sound, soundout
from tests.test_roles import LAN, TUNNEL, RolesBase
from tests.test_sound import DEVICE, FakeApi, FakeChild, ServiceBase, Stub, proc_dir, sine, wait_for

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STREAM = ("C-Media USB Audio Device at usb-0000:01:00.0-1.3, full speed : USB Audio\n\nPlayback:\n  Status: Stop\n"
          "  Interface 1\n    Altset 1\n    Format: S16_LE\n    Channels: 2\n\nCapture:\n  Status: Stop\n  Interface 2\n"
          "    Altset 1\n    Format: S16_LE\n    Channels: 4\n    Altset 2\n    Channels: 2\n")


def with_stream(test, text=STREAM):
    d = proc_dir(test)
    os.makedirs(os.path.join(d, "card1"))
    with open(os.path.join(d, "card1", "stream0"), "w") as f:
        f.write(text)
    return d


def interleave(*chans):
    out = array.array("h")
    for frame in zip(*chans):
        out.extend(frame)
    return out


# --- 1. one input of a multi-input card ---------------------------------------------------------------------------------
class ChannelTest(unittest.TestCase):
    def test_the_count_comes_from_proc_and_is_bounded(self):
        self.assertEqual(sound.capture_devices(with_stream(self))[0]["channels"], 4, "the largest under Capture, never Playback's")
        self.assertEqual(sound.capture_devices(proc_dir(self))[0]["channels"], sound.DEFAULT_CHANNELS, "a card that does not say")
        self.assertEqual(sound.capture_devices(with_stream(self, "Capture:\n    Channels: 999\n"))[0]["channels"], sound.MAX_CHANNELS)
        self.assertEqual(sound.capture_devices(with_stream(self, "Playback:\n    Channels: 8\n"))[0]["channels"], sound.DEFAULT_CHANNELS)

    def test_arecord_stays_argv_and_plughw_with_a_checked_count(self):
        self.assertEqual(sound.arecord_args(DEVICE, "arecord", 4)[-2:], ["-c", "4"])
        self.assertEqual(sound.arecord_args(DEVICE, "arecord", 4)[3], "plughw:CARD=Device,DEV=0")
        for bad in (0, 33, "4", True, None, 2.0):
            with self.assertRaises(ValueError, msg=bad):
                sound.arecord_args(DEVICE, "arecord", bad)
        self.assertEqual(sound.channel_form("mix", 2), "mix")
        for bad in (0, 3, "1", True, -1):
            with self.assertRaises(ValueError, msg=bad):
                sound.channel_form(bad, 2)


class ChannelServiceTest(ServiceBase):
    def test_the_chosen_input_is_the_one_heard(self):
        left, right = sine(1000, 0.25, 1.0), array.array("h", bytes(2 * sound.RATE))
        self.make = lambda: FakeChild(interleave(left, right))
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0, "source": "device", "channel": 1, "channels": 2})
        self.assertTrue(wait_for(lambda: (sound.read_levels(self.levels) or {}).get("blocks", 0) > 10))
        self.assertEqual(self.spawned[0][-2:], ["-c", "2"])
        self.assertAlmostEqual(sound.read_levels(self.levels)["rms_db"], 20 * math.log10(0.25) - 3.01, delta=0.5, msg="input 1 is the sine")
        self.svc.configure({"on": True, "device": DEVICE, "gain_db": 0.0, "source": "device", "channel": 2, "channels": 2})
        self.assertTrue(wait_for(lambda: len(self.spawned) == 2 and sound.read_levels(self.levels)["blocks"] > 10 and sound.read_levels(self.levels)["rms_db"] <= sound.FLOOR_DB))
        for bad in ({"channel": 3, "channels": 2}, {"channel": 1, "channels": 0}, {"channel": "left", "channels": 2}, {"source": "x"}):
            self.assertFalse(self.svc.configure(dict({"on": True, "device": DEVICE, "gain_db": 0.0}, **bad))["ok"], bad)


# --- levels fed in: the service's side -----------------------------------------------------------------------------------
class FeedServiceTest(ServiceBase):
    def test_a_feed_becomes_a_record_only_while_a_fed_source_is_chosen(self):
        self.assertFalse(self.svc.take_feed({"rms_db": -10.0})["ok"], "not while the device is the source")
        self.svc.configure({"on": True, "device": "", "gain_db": 0.0, "source": "feed"})
        self.assertTrue(wait_for(lambda: self.svc.status()["state"] == "waiting"))
        self.assertEqual(self.spawned, [], "no arecord for a fed source")
        self.assertTrue(self.svc.take_feed({"rms_db": -12.0, "peak_db": -6.0, "bands": [0.5] * 6})["ok"])
        self.assertTrue(wait_for(lambda: (sound.read_levels(self.levels) or {}).get("capturing")))
        v = sound.read_levels(self.levels)
        self.assertAlmostEqual(v["rms_db"], -12.0, places=3)
        self.assertEqual(self.svc.status()["state"], "capturing")
        self.assertTrue(wait_for(lambda: self.svc.status()["state"] == "waiting", 3), "no feed for half a second: waiting")
        self.assertFalse(sound.read_levels(self.levels)["capturing"])

    def test_what_a_feed_may_hold_and_how_many(self):
        self.svc.configure({"on": True, "device": "", "gain_db": 0.0, "source": "feed"})
        for bad in ({"db": 1}, {"rms_db": "x"}, {"rms_db": float("nan")}, {"rms_db": 1, "beat": 1}, {"rms_db": 1, "bands": [0] * 9},
                    {"rms_db": 1, "run": "x"}, [], {}):
            self.assertFalse(self.svc.take_feed(bad)["ok"], bad)
        f = sound.feed_form({"rms_db": 500, "bands": [9, -1], "bpm": 9000})
        self.assertEqual((f["rms_db"], f["bands"], f["bpm"]), (sound.GAIN_MAX, [1.0, 0.0], 400.0))
        took = sum(1 for _ in range(300) if self.svc.take_feed({"rms_db": -20.0})["ok"])
        self.assertLessEqual(took, int(sound.FEED_BURST) + 5, "a flood is held to the rate")
        self.assertGreater(self.svc.status()["counts"]["feeds_dropped"], 0)


class FeedAnalyzerTest(unittest.TestCase):
    def test_beats_and_a_tempo_from_the_level_alone(self):
        a = sound.FeedAnalyzer()
        t, beats = 0.0, 0
        while t < 8.0:
            loud = (t % 0.5) < 0.05                     # a hit every half second: 120 BPM
            v = a.feed({"rms_db": -6.0 if loud else -40.0, "peak_db": -3.0}, t)
            beats += v["beat"]
            t += 0.04
        self.assertGreaterEqual(beats, 15)
        self.assertLessEqual(beats, 17)
        self.assertAlmostEqual(a.a.tempo()[0], 120.0, delta=4.0)

    def test_the_senders_beat_and_bands_and_the_slow_bands(self):
        a = sound.FeedAnalyzer()
        v = a.feed({"rms_db": -20.0, "peak_db": -20.0, "beat": True, "bands": [1.0] * 6, "bpm": 128.0}, 1.0)
        self.assertTrue(v["beat"])
        self.assertEqual((v["bpm"], v["bands"]), (128.0, [1.0] * 6))
        self.assertEqual(v["slow_bands"], [0.0] * 6, "the slow bands change only after 200 ms")
        v = a.feed({"rms_db": -20.0, "peak_db": -20.0, "beat": True, "bands": [0.0] * 6}, 1.05)
        self.assertFalse(v["beat"], "never two beats closer than BEAT_GAP")
        v = a.feed({"rms_db": -20.0, "peak_db": -20.0, "bands": [0.0] * 6}, 1.25)
        self.assertEqual([round(x, 3) for x in v["slow_bands"]], [0.333] * 6, "the mean of what came in the 200 ms")


# --- 2 and 3. the box's own sound and the NDI sound, measured in the player ----------------------------------------------
class FakeIpc:
    def __init__(self, level="-18.0", take=True):
        self.calls, self.filters, self.level, self.take = [], [], level, take

    def request(self, *cmd):
        """As pvj.player.Ipc: the answer's data, or PlayerError."""
        from pvj.player import PlayerError
        self.calls.append(cmd)
        if cmd[:2] == ("get_property", "af"):
            return [{"name": "lavfi", "label": f} for f in self.filters]
        if cmd[0] == "af":
            if not self.take:
                raise PlayerError("mpv: invalid parameter")
            if cmd[1] == "add":
                self.filters.append(cmd[2].split(":")[0][1:])
            else:
                self.filters = [f for f in self.filters if "@" + f != cmd[2]]
            return None
        if cmd[:2] == ("get_property", "af-metadata/pvjsound"):
            if self.level is None:
                raise PlayerError("mpv: property unavailable")
            return {"lavfi.astats.Overall.RMS_level": self.level, "lavfi.astats.Overall.Peak_level": "-6.0"}
        return None


class FakeClient:
    def __init__(self):
        self.sent = []

    def request(self, m):
        self.sent.append(m)
        return {"ok": True}


def rig(source="box", ipc=None, **cfg):
    api = FakeApi({"sound": dict(soundout.DEFAULT, on=True, source=source, allowed=list(soundout.INPUTS), **cfg)})
    api.player = Stub(ipc=ipc or FakeIpc())
    out = soundout.Sound(api, socket_path="/nonexistent", levels_path="/nonexistent", log=lambda *_: None)
    out.client = FakeClient()
    return api, out


class BoxSoundTest(unittest.TestCase):
    def test_the_meter_goes_in_once_reads_two_numbers_and_comes_out(self):
        api, out = rig()
        ipc = api.player.ipc
        self.assertIsNone(out.meter(True))
        self.assertIsNone(out.meter(True))
        self.assertEqual([c for c in ipc.calls if c[0] == "af"], [("af", "add", soundout.METER_FILTER)], "added once")
        self.assertIn("astats=metadata=1", soundout.METER_FILTER)
        self.assertEqual(out.read_meter(), (-18.0, -6.0))
        f = out.feed_once(out.config())
        self.assertEqual(f, {"rms_db": -18.0, "peak_db": -6.0})
        self.assertEqual(out.client.sent[-1], {"cmd": "feed", "rms_db": -18.0, "peak_db": -6.0}, "only two numbers leave the player")
        out.meter(False)
        self.assertEqual(ipc.filters, [])

    def test_silence_odd_answers_and_a_renderer_that_will_not(self):
        api, out = rig(ipc=FakeIpc(level="-inf"))
        self.assertEqual(out.read_meter()[0], sound.FLOOR_DB)
        for bad in ("nan", "x" * 40, None, "1e999"):
            api.player.ipc.level = bad
            got = out.read_meter()
            self.assertTrue(got is None or sound.FLOOR_DB <= got[0] <= sound.GAIN_MAX, bad)
        api.player.ipc = FakeIpc(take=False)
        self.assertIn("new renderer", out.meter(True))

    def test_the_ndi_sound_counts_only_while_ndi_plays(self):
        api, out = rig(source="ndi")
        api.ndi = Stub(current=None)
        self.assertIsNone(out.feed_once(out.config()))
        self.assertIn("No NDI picture is playing", out.feed_why)
        api.ndi.current = {"id": "0123456789ab"}
        self.assertEqual(out.feed_once(out.config())["rms_db"], -18.0)

    def test_the_feeder_thread_runs_only_for_a_fed_source(self):
        api, out = rig()
        out.ensure_feeder()
        self.addCleanup(out.stop)
        self.assertTrue(wait_for(lambda: len(out.client.sent) >= 3, 3), "it feeds the service")
        api.settings.data["sound"]["source"] = "device"
        t = out._feeder
        out.ensure_feeder()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(api.player.ipc.filters, [], "the meter is taken out of the player again")


@unittest.skipUnless(shutil.which("mpv"), "no mpv here")
class RealMpvMeterTest(unittest.TestCase):
    def test_a_real_player_measures_a_tone_it_plays(self):
        from pvj.player import Ipc
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        wav = os.path.join(d, "t.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<hh", int(0.25 * 32767 * math.sin(2 * math.pi * 440 * i / 48000)), 0) for i in range(48000 * 4)))
        sock = os.path.join(d, "s")
        p = subprocess.Popen(["mpv", "--no-config", "--ao=null", "--vo=null", "--idle=yes", "--input-ipc-server=" + sock],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.terminate(), p.wait(5)))
        self.assertTrue(wait_for(lambda: os.path.exists(sock), 10))
        api, out = rig(ipc=Ipc(sock))
        self.assertIsNone(out.meter(True))
        api.player.ipc.request("loadfile", wav)
        self.assertTrue(wait_for(lambda: out.read_meter() is not None, 10))
        rms, peak = out.read_meter()
        self.assertAlmostEqual(rms, 20 * math.log10(0.25 / math.sqrt(2)) - 3.01, delta=0.5, msg="a 0.25 sine in one of two channels")
        self.assertAlmostEqual(peak, 20 * math.log10(0.25), delta=0.3)
        self.assertIsNone(out.meter(False))
        self.assertFalse(any(f.get("label") == "pvjsound" for f in api.player.ipc.request("get_property", "af")))


# --- 4. levels sent in over OSC ------------------------------------------------------------------------------------------
NODE = {"id": "0a0b0c0d", "name": "Max", "host": "192.168.0.30", "port": 7400, "ndi": "", "profile": "generic", "still": 10}


def msg(address, *args):
    typed = [{"type": "float", "value": a} if isinstance(a, float) else {"type": "int", "value": a} for a in args]
    return render.encode(address, typed)


class OscInTest(unittest.TestCase):
    def setUp(self):
        self.api, self.out = rig(source="osc", in_hosts=["192.168.0.44"])
        self.api.settings.data["render"] = {"nodes": [NODE]}
        self.api.render = Stub(why_not=lambda a, nets=None: None if str(a).startswith("192.168.0.") else "not on this network now")
        self.api.sound = self.out
        self.handled = []
        self.api.handle = lambda *a: self.handled.append(a) or (200, {})
        self.api.mix = {}
        self.server = osc.OscServer(self.api, log=lambda *_: None, rules={"paired_on": True, "only_on": True, "only": ["192.168.0.99"]})

    def test_from_a_render_node_or_a_listed_address_and_past_the_locks(self):
        for host in ("192.168.0.30", "192.168.0.44"):
            self.assertEqual(self.server.handle_packet(msg("/nxlx/sound/in/db", -12.0), host), 0)
            self.assertAlmostEqual(self.out._in_latest["rms_db"], -12.0)
            self.out._in_latest = None
        data = fb._BUNDLE + b"".join(struct.pack(">i", len(m)) + m for m in (msg("/nxlx/sound/in/level", 0.5), msg("/nxlx/sound/in/beat"),
                                                                              msg("/nxlx/sound/in/bands", 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)))
        self.server.handle_packet(data, "192.168.0.30")
        got = self.out._in_latest
        self.assertAlmostEqual(got["rms_db"], -30.0)
        self.assertTrue(got["beat"])
        self.assertEqual([round(b, 3) for b in got["bands"]], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        self.assertEqual(self.handled, [], "nothing of it is a command")

    def test_from_anyone_else_or_with_a_command_beside_it_it_is_the_normal_way(self):
        self.server.handle_packet(msg("/nxlx/sound/in/db", -12.0), "192.168.0.45")
        self.assertIsNone(self.out._in_latest, "an address nobody listed")
        self.server.handle_packet(msg("/nxlx/sound/in/db", -12.0), "192.168.7.30")
        self.assertIsNone(self.out._in_latest)
        mixed = fb._BUNDLE + b"".join(struct.pack(">i", len(m)) + m for m in (msg("/nxlx/sound/in/db", -12.0), msg("/pvj/stop")))
        self.server.handle_packet(mixed, "192.168.0.30")
        self.assertIsNone(self.out._in_latest, "a packet with a command in it meets the locks, which refuse it here")
        self.assertEqual(self.handled, [])
        self.api.settings.data["sound"]["source"] = "device"
        self.out._in_hosts = None
        self.server.handle_packet(msg("/nxlx/sound/in/db", -12.0), "192.168.0.30")
        self.assertIsNone(self.out._in_latest, "only while OSC in is the source")

    def test_bounded_clamped_and_never_answered(self):
        for _ in range(300):
            self.out.osc_in([("/nxlx/sound/in/db", [99999.0]), ("/nxlx/sound/in/envelope", [-5.0])], "192.168.0.30")
        self.assertEqual((self.out._in_latest["rms_db"], self.out._in_latest["envelope"]), (sound.GAIN_MAX, 0.0))
        self.assertLessEqual(self.out.in_stats["taken"], soundout.IN_BURST + 2, "a burst, then held to the rate")
        self.assertGreater(self.out.in_stats["held"], 0)
        self.assertEqual(self.out.osc_in([("/nxlx/sound/in/run", ["x"])], "192.168.0.44"), 0)
        with open(os.path.join(REPO, "pvj", "soundout.py")) as f:
            part = f.read().split("def osc_in(")[1].split("\n    def ")[0]
        self.assertNotIn("sendto", part)

    def test_what_came_in_is_fed_then_goes_stale(self):
        self.out.osc_in([("/nxlx/sound/in/db", [-10.0]), ("/nxlx/sound/in/beat", [])], "192.168.0.30")
        f = self.out.feed_once(self.out.config())
        self.assertEqual((f["rms_db"], f["beat"]), (-10.0, True))
        self.assertNotIn("beat", self.out.feed_once(self.out.config()), "a beat is fed once")
        self.out._in_at -= 1.0
        self.assertIsNone(self.out.feed_once(self.out.config()))


# --- 5. who may switch, remote support, the setting -----------------------------------------------------------------------
class SourceRoutesTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.sdir = tempfile.mkdtemp(prefix="pvj-sound-src-")
        self.addCleanup(shutil.rmtree, self.sdir, True)
        self.api.sound = soundout.Sound(self.api, socket_path=os.path.join(self.sdir, "none.sock"),
                                        levels_path=os.path.join(self.sdir, "levels"), proc=with_stream(self), log=lambda *_: None)
        self.addCleanup(self.api.sound.stop)

    def session(self):
        from tests.test_support import CFG
        self.assertEqual(self.h("POST", "/api/support/config", dict(CFG, allowed=True), self.owner)[0], 200)
        st, body = self.h("POST", "/api/support/start", {"confirm": "start"}, self.owner)
        login = self.h("POST", "/api/support/login", {"code": body["code"]}, client=TUNNEL)[1]
        return self.api.support.authenticate(login["token"])

    def test_the_owner_allows_and_the_operator_switches(self):
        self.assertEqual(self.h("POST", "/api/sound/source", {"source": "box"}, self.operator)[0], 403, "not allowed yet")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": ["device", "box", "osc"]}, self.operator)[0], 403, "the list is the Owner's")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": ["device", "box", "osc"]}, self.owner)[0], 200)
        st, d = self.h("POST", "/api/sound/source", {"source": "box"}, self.operator)
        self.assertEqual((st, d["config"]["source"]), (200, "box"))
        self.assertEqual(self.h("POST", "/api/sound/source", {"source": "ndi"}, self.operator)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound/source", {"source": "box"}, self.guest)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound/source", {"source": "x"}, self.operator)[0], 400)
        self.assertFalse(self.settings.data["sound"]["on"], "switching the source never switches listening on")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": ["device"]}, self.owner)[1]["config"]["source"], "device",
                         "the source in use taken off the list: back to the first allowed")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": []}, self.owner)[0], 400)

    def test_the_owner_chooses_an_input_and_the_addresses(self):
        self.assertEqual(self.h("POST", "/api/sound", {"device": DEVICE, "channel": 3}, self.owner)[0], 200)
        self.assertEqual(self.h("POST", "/api/sound", {"channel": 5}, self.owner)[0], 400, "the card has four")
        self.assertEqual(self.h("POST", "/api/sound", {"in_hosts": ["8.8.8.8"]}, self.owner)[0], 400)
        self.assertEqual(self.h("POST", "/api/sound", {"in_hosts": ["192.168.0.44"]}, self.owner)[0], 200)
        d = self.h("GET", "/api/sound", {}, self.operator)[1]
        self.assertEqual((d["channels"], d["config"]["in_hosts"]), (4, []), "an Operator does not see the addresses")
        self.assertEqual(self.h("GET", "/api/sound", {}, self.owner)[1]["config"]["in_hosts"], ["192.168.0.44"])
        self.assertEqual(self.api.sound.wanted(), {"source": "device", "channel": 3, "channels": 4})

    def test_remote_support_never_starts_listening_to_the_room(self):
        self.h("POST", "/api/sound", {"device": DEVICE, "allowed": ["device", "box"], "on": True}, self.owner)
        self.h("POST", "/api/sound/source", {"source": "box"}, self.owner)
        dev = self.session()
        st, body = self.h("POST", "/api/sound/source", {"source": "device"}, dev, TUNNEL)
        self.assertEqual(st, 403)
        self.assertIn("a helper does not start listening to the room", body["error"])
        self.assertEqual(self.h("POST", "/api/sound/source", {"source": "box"}, dev, TUNNEL)[0], 200, "not a microphone")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": ["device", "box", "osc"]}, dev, TUNNEL)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound", {"in_hosts": ["192.168.0.44"]}, dev, TUNNEL)[0], 403)
        self.assertEqual(self.h("POST", "/api/sound", {"channel": 2}, dev, TUNNEL)[0], 403, "not another input while listening")
        st, body = self.h("POST", "/api/sound", {"allowed": ["device"]}, dev, TUNNEL)
        self.assertEqual(st, 403, "taking the source in use off the list never falls back to the microphone (review of D97)")
        self.assertIn("a helper does not start listening to the room", body["error"])
        self.assertEqual(self.api.sound.config()["source"], "box")
        self.assertEqual(self.h("POST", "/api/sound", {"allowed": ["box"]}, dev, TUNNEL)[0], 200, "taking one off is allowed")
        self.assertEqual(self.h("GET", "/api/sound/levels", {}, dev, TUNNEL)[0], 200)


class RollbackTest(unittest.TestCase):
    def test_the_release_before_reads_the_new_section_as_off_and_keeps_it(self):
        master = "5a3a1c6c593cb387706afa488c5eeb79923860e3"     # D96, the release before D97 (D62's lesson: by its commit)
        try:
            subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", master], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no %s here to read the older release from" % master)
        from pvj.settings import Settings
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "settings.json")
        s = Settings(path)
        s.load()
        s.data["sound"] = dict(soundout.DEFAULT, on=True, device=DEVICE, source="osc", allowed=["device", "osc"], channel=2, in_hosts=["192.168.0.44"])
        s.save()
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", master, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        script = ("import json, sys\nfrom pvj.settings import Settings\nfrom pvj import soundout\ns = Settings(sys.argv[1])\ns.load()\n"
                  "print(json.dumps(soundout.stored(s.data['sound'])['on']))\ns.save()\n")
        r = subprocess.run([sys.executable, "-c", script, path], cwd=old, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), False, "going back switches listening off, never on")
        with open(path) as f:
            self.assertEqual(json.load(f)["sound"], s.data["sound"], "and keeps the section for this release")


if __name__ == "__main__":
    unittest.main()
