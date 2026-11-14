# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A PC node's Automatic sound goes to the HDMI or DisplayPort sound of the screen the picture is on, when that can be
told safely (J-155, D137): from ALSA's jack and ELD controls (amixer, faked here) and fake /proc and /sys trees."""
import shutil
import subprocess
import types
import unittest
from unittest import mock

from pvj import hardware
from tests.test_audio import FakeIpc
from tests.test_hardware import make_tree
from tests.test_server import ServerBase


def edid(name, maker=0x2157, product=0x2131):
    """A 128 byte EDID: maker and product at bytes 8 to 11, the monitor name in the second descriptor (0xFC)."""
    data = bytearray(128)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    data[8:10] = maker.to_bytes(2, "little")
    data[10:12] = product.to_bytes(2, "little")
    text = name.encode()[:13]
    text = text + (b"\n" + b" " * (12 - len(text)) if len(text) < 13 else b"")
    data[72:90] = b"\0\0\0\xfc\0" + text
    return bytes(data)


def eld(name, maker=0x2157, product=0x2131):
    """CEA-861 ELD bytes as ALSA's ELD control gives them: name length in byte 4, maker at 16, product at 18, the name
    from 20, then one short audio descriptor, padded to whole dwords."""
    body = bytearray(20)
    body[0] = 0x10
    body[4] = (2 << 5) | len(name)
    body[5] = 1 << 4
    body[16:18] = maker.to_bytes(2, "little")
    body[18:20] = product.to_bytes(2, "little")
    body += name.encode() + b"\x09\x07\x07"
    body += b"\0" * (-len(body) % 4)
    body[2] = (len(body) - 4) // 4
    return bytes(body)


def amixer_text(pcms):
    """`amixer -c 0 contents` for HDMI PCMs: {device: None (no screen) or ELD bytes}, with an analog control first."""
    lines = ["numid=1,iface=MIXER,name='Master Playback Switch'", "  ; type=BOOLEAN,access=rw------,values=1",
             "  : values=on"]
    for n, (device, data) in enumerate(sorted(pcms.items())):
        lines += ["numid=%d,iface=CARD,name='HDMI/DP,pcm=%d Jack'" % (17 + n, device),
                  "  ; type=BOOLEAN,access=r-------,values=1", "  : values=%s" % ("on" if data else "off"),
                  "numid=%d,iface=PCM,name='ELD',device=%d" % (23 + 6 * n, device),
                  "  ; type=BYTES,access=r--v----,values=%d" % len(data or b""),
                  "  : values=" + ",".join("0x%02x" % b for b in (data or b""))]
    return "\n".join(lines) + "\n"


def runner(text, returncode=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=text if returncode == 0 else "", returncode=returncode)
    run.calls = calls
    return run


def card(info=True):
    """/proc/asound/card0 of an Intel PC: id PCH, HDMI PCMs 3, 7, 8 named HDMI 0, 1, 2, and the 9 ELD files of the
    Kaby Lake (three a port for DP MST; never read, they are not what decides)."""
    files = {"proc/asound/card0/id": "PCH\n"}
    for n in range(9):
        files["proc/asound/card0/eld#0.%d" % n] = "monitor_present\t\t%d\n" % (n in (0, 3, 6))
    if info:
        for k, dev in enumerate((3, 7, 8)):
            files["proc/asound/card0/pcm%dp/info" % dev] = "card: 0\ndevice: %d\nid: HDMI %d\nname: HDMI %d\n" % (dev, k, k)
    return files


def drm(**screens):
    """sysfs connectors: name (with _ for -) to an EDID, or None for nothing connected."""
    files = {}
    for key, data in screens.items():
        base = "sys/class/drm/card1-%s/" % key.replace("_", "-")
        files[base + "status"] = "connected\n" if data else "disconnected\n"
        files[base + "edid"] = data or b""
    return files


def conns(**screens):
    return [{"connector": k.replace("_", "-"), "status": "connected" if v else "disconnected", "modes": [], "card": 1}
            for k, v in screens.items()]


# What the M710q gave on 2026-11-14 with three capture cards: PCMs 3, 7, 8 all with a screen, ELD maker 0x2157 for
# all, product 0x2131 for PCM 3 and 7 and 0x2130 for PCM 8; the EDIDs: 0x2131 for HDMI-A-1 and -2, 0x2130 for -3. Yet
# a tone on PCM 3 reached HDMI-A-1 and one on PCM 8 reached HDMI-A-2: the ELDs and EDIDs cannot be paired safely.
KABY_PCMS = {3: eld("HDMI TO USB", 0x2157, 0x2131), 7: eld("HDMI TO USB", 0x2157, 0x2131), 8: eld("HD TO USB", 0x2157, 0x2130)}
KABY_DRM = dict(HDMI_A_1=edid("HDMI TO USB", 0x2157, 0x2131), HDMI_A_2=edid("HDMI TO USB", 0x2157, 0x2131),
                HDMI_A_3=edid("HD TO USB", 0x2157, 0x2130))
DELL, LG = ("DELL U2415", 0xac10, 0xa0a0), ("LG HDR 4K", 0x6d1e, 0x095b)


class HdmiSoundTest(unittest.TestCase):
    def tree(self, *parts):
        files = {}
        for p in parts:
            files.update(p)
        root = make_tree(files)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def test_amixer_contents_give_the_jacks_and_the_elds(self):
        got = hardware.parse_amixer(amixer_text({3: KABY_PCMS[3], 7: None, 8: KABY_PCMS[8]}))
        self.assertEqual(sorted(got), [3, 7, 8])
        self.assertEqual([got[d]["jack"] for d in (3, 7, 8)], [True, False, True])
        self.assertEqual(hardware.eld_identity(got[3]["eld"]), ("HDMI TO USB", "0x2157", "0x2131"))
        self.assertEqual(hardware.eld_identity(got[8]["eld"]), ("HD TO USB", "0x2157", "0x2130"))
        self.assertIsNone(hardware.eld_identity(got[7]["eld"] or b""))
        self.assertIsNone(hardware.eld_identity(bytes(40)))                     # an ELD of zeros: no screen
        self.assertIsNone(hardware.parse_amixer("numid=2,iface=PCM,name='ELD',device=3\n  : values=0xzz\n")[3]["eld"])

    def test_one_screen_takes_its_pcm_whatever_its_names_say(self):
        root = self.tree(card(), drm(HDMI_A_1=None, HDMI_A_2=edid("ANYTHING", 1, 2), HDMI_A_3=None))
        run = runner(amixer_text({3: None, 7: None, 8: KABY_PCMS[8]}))
        found = hardware.hdmi_sound_for("HDMI-A-2", conns(HDMI_A_2="y"), root, run)
        self.assertEqual((found["card_id"], found["device"], found["hdmi_index"], found["how"]), ("PCH", 8, 2, "the only screen"))
        self.assertEqual(run.calls[0], ["amixer", "-c", "0", "contents"])
        self.assertIsNone(hardware.hdmi_sound_for("VGA-1", conns(VGA_1="y"), root, run))    # a screen without HDMI sound
        # the fixed HDA numbers stand in when the driver does not name its PCMs
        root = self.tree(card(info=False))
        self.assertEqual(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="x"), root, run)["hdmi_index"], 2)

    def test_the_measured_kaby_lake_contradiction_falls_back(self):
        root = self.tree(card(), drm(**KABY_DRM))
        run = runner(amixer_text(KABY_PCMS))
        self.assertEqual([s["device"] for s in hardware.hdmi_sound_screens(root, run)], [3, 7, 8])
        c = conns(HDMI_A_1="x", HDMI_A_2="y", HDMI_A_3="z")
        for name in ("HDMI-A-1", "HDMI-A-2", "HDMI-A-3"):
            # pairing HDMI-A-3 with PCM 8 by product would be wrong: PCM 8's tone came out on HDMI-A-2
            self.assertIsNone(hardware.hdmi_sound_for(name, c, root, run), name)

    def test_several_screens_match_only_when_every_one_is_told_apart(self):
        root = self.tree(card(), drm(HDMI_A_1=edid(*LG), HDMI_A_2=edid(*DELL)))
        run = runner(amixer_text({3: eld(*DELL), 7: None, 8: eld(*LG)}))
        c = conns(HDMI_A_1="x", HDMI_A_2="y")
        one, two = (hardware.hdmi_sound_for(n, c, root, run) for n in ("HDMI-A-1", "HDMI-A-2"))
        self.assertEqual((one["device"], one["how"], two["device"]), (8, "monitor name and model", 3))
        # same name, other model: still told apart
        root2 = self.tree(card(), drm(HDMI_A_1=edid("SAME", 1, 1), HDMI_A_2=edid("SAME", 1, 2)))
        run2 = runner(amixer_text({3: eld("SAME", 1, 2), 8: eld("SAME", 1, 1)}))
        self.assertEqual(hardware.hdmi_sound_for("HDMI-A-1", c, root2, run2)["device"], 8)
        # a third connected screen that looks like another: no match for anyone
        root3 = self.tree(card(), drm(HDMI_A_1=edid(*LG), HDMI_A_2=edid(*DELL), HDMI_A_3=edid(*DELL)))
        c3 = conns(HDMI_A_1="x", HDMI_A_2="y", HDMI_A_3="z")
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c3, root3, run))
        # an ELD that says nothing, or a screen without an EDID: no match
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c, root, runner(amixer_text({3: eld(*DELL), 8: bytes(24)}))))
        root4 = self.tree(card(), drm(HDMI_A_1=edid(*LG)), {"sys/class/drm/card1-HDMI-A-2/status": "connected\n"})
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c, root4, run))
        # the picture on a screen the kernel does not show as connected
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-3", c, root, run))

    def test_nothing_to_read_gives_nothing(self):
        root = self.tree(card(), drm(HDMI_A_1=edid("A")))
        c = conns(HDMI_A_1="x")
        self.assertIsNone(hardware.hdmi_sound_screens(root, runner("", returncode=1)))         # amixer failed: not "no screens"

        def timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 3)
        self.assertIsNone(hardware.hdmi_sound_screens(root, timeout))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c, root, timeout))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c, root, runner(amixer_text({3: None, 7: None}))))
        self.assertEqual(hardware.hdmi_sound_screens(self.tree(drm(HDMI_A_1=edid("A"))), runner(amixer_text(KABY_PCMS))), [])
        bad = self.tree({"proc/asound/card1/id": "bad id;\n"})
        self.assertEqual(hardware.hdmi_sound_screens(bad, runner(amixer_text(KABY_PCMS))), [])
        with mock.patch("pvj.hardware.shutil.which", return_value=None):
            self.assertIsNone(hardware.hdmi_sound_screens(root))                                 # no amixer at all

    def test_edid_reading(self):
        root = self.tree(drm(HDMI_A_1=edid(*DELL)), {"sys/class/drm/card1-HDMI-A-2/edid": b"not an edid"})
        self.assertEqual(hardware.edid_monitor_name("HDMI-A-1", root), "DELL U2415")
        self.assertEqual(hardware.edid_identity("HDMI-A-1", root), ("DELL U2415", "0xac10", "0xa0a0"))
        self.assertIsNone(hardware.edid_identity("HDMI-A-2", root))
        self.assertIsNone(hardware.edid_monitor_name("HDMI-A", root))


PC_DEVICES = [{"name": "auto", "description": "Autoselect device"},
              {"name": "alsa/default:CARD=PCH", "description": "HDA Intel PCH, ALC233 Analog"},
              {"name": "alsa/hdmi:CARD=PCH,DEV=0", "description": "HDA Intel PCH, HDMI 0"},
              {"name": "alsa/hdmi:CARD=PCH,DEV=2", "description": "HDA Intel PCH, HDMI 2"},
              {"name": "alsa/plughw:CARD=PCH,DEV=3", "description": "HDA Intel PCH, HDMI 0"},
              {"name": "alsa/plughw:CARD=PCH,DEV=8", "description": "HDA Intel PCH, HDMI 2"}]


class PcIpc(FakeIpc):
    devices = PC_DEVICES

    def request(self, *cmd):
        if cmd[:2] == ("get_property", "audio-device-list") and not self.down:
            self.calls.append(cmd)
            return list(self.devices)
        return FakeIpc.request(self, *cmd)


class PcAutomaticSoundTest(ServerBase):
    """The Api of a PC node (ServerBase's board is x86)."""

    def setUp(self):
        super().setUp()
        self.ipc = PcIpc()
        self.player.ipc = self.ipc
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        root = make_tree(dict(card(), **drm(HDMI_A_1=edid(*DELL), HDMI_A_2=edid(*LG))))
        self.addCleanup(shutil.rmtree, root, True)
        self.api.SOUND_ROOT = root
        self.api.SOUND_RUN = runner(amixer_text({3: eld(*DELL), 7: None, 8: eld(*LG)}))
        self.using = [None]
        p = mock.patch("pvj.api.hardware.drm_connectors", return_value=conns(HDMI_A_1="x", HDMI_A_2="y"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.api.screen, "report", side_effect=lambda: {"connector": self.using[0]} if self.using[0] else None)
        p.start()
        self.addCleanup(p.stop)

    def test_automatic_follows_the_screen_the_picture_is_on(self):
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")
        self.using[0] = "HDMI-A-2"                                       # the output changed: the player restarted on it
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=8")
        st, body, _ = self.call("GET", "/api/audio", token=self.full)
        self.assertEqual((st, body["automatic_is"], body["automatic_note"]), (200, "alsa/plughw:CARD=PCH,DEV=8", ""))

    def test_without_a_report_the_chosen_output_then_the_first_hdmi(self):
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")    # HDMI-A-1 first, as the player chooses
        with mock.patch.object(self.api.screen, "saved_output", return_value="HDMI-A-2"):
            self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=8")

    def test_one_projector_gets_its_pcm(self):
        self.api.SOUND_RUN = runner(amixer_text({3: KABY_PCMS[3], 7: None, 8: None}))
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")    # measured: a tone at -24 dB on HDMI-A-1

    def test_the_hdmi_plugin_name_when_mpv_lists_no_plughw(self):
        self.ipc.devices = [d for d in PC_DEVICES if "plughw" not in d["name"]]
        self.using[0] = "HDMI-A-2"
        self.assertEqual(self.api.apply_audio(), "alsa/hdmi:CARD=PCH,DEV=2")

    def test_several_screens_that_cannot_be_told_apart_keep_automatic_and_say_so(self):
        self.api.SOUND_RUN = runner(amixer_text(KABY_PCMS))
        self.using[0] = "HDMI-A-2"
        self.assertEqual(self.api.apply_audio(), "auto")
        st, body, _ = self.call("GET", "/api/audio", token=self.full)
        self.assertEqual((body["automatic_is"], body["automatic_note"]), ("auto", "Several HDMI screens: choose the sound output by hand"))

    def sets(self):
        return [c[2] for c in self.ipc.calls if c[:2] == ("set_property", "audio-device")]

    def test_a_failed_or_empty_read_keeps_the_last_pick(self):
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")

        def timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 3)
        for run in (runner("", returncode=1), timeout,                                 # amixer failed, timed out
                    runner(amixer_text({3: None, 7: None, 8: None}))):                # a jack dropped for a moment
            self.api.SOUND_RUN = run
            for _ in range(3):                                                           # ensure_audio every few seconds
                self.assertEqual(self.api.ensure_audio(), "alsa/plughw:CARD=PCH,DEV=3")
        self.assertNotIn("auto", self.sets())                                           # never to the analog jack
        st, body, _ = self.call("GET", "/api/audio", token=self.full)
        self.assertEqual((body["automatic_is"], body["automatic_note"]), ("alsa/plughw:CARD=PCH,DEV=3", ""))

    def test_a_new_pick_for_the_same_screen_needs_two_reads_in_a_row(self):
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")
        moved = runner(amixer_text({3: None, 8: eld(*DELL)}))         # the driver gave HDMI-A-1's screen PCM 8
        steady = self.api.SOUND_RUN
        self.api.SOUND_RUN = moved
        self.assertEqual(self.api.ensure_audio(), "alsa/plughw:CARD=PCH,DEV=3")       # one read: not yet
        self.api.SOUND_RUN = steady
        self.assertEqual(self.api.ensure_audio(), "alsa/plughw:CARD=PCH,DEV=3")       # back again: the odd one forgotten
        self.api.SOUND_RUN = moved
        self.assertEqual(self.api.ensure_audio(), "alsa/plughw:CARD=PCH,DEV=3")
        self.assertEqual(self.api.ensure_audio(), "alsa/plughw:CARD=PCH,DEV=8")       # two in a row: taken
        self.assertEqual(self.sets()[-1], "alsa/plughw:CARD=PCH,DEV=8")

    def test_get_audio_reads_amixer_once(self):
        self.using[0] = "HDMI-A-1"
        self.call("GET", "/api/audio", token=self.full)
        self.assertEqual(len(self.api.SOUND_RUN.calls), 1)
        self.call("POST", "/api/audio", {"device": "auto"}, token=self.full)
        self.assertEqual(len(self.api.SOUND_RUN.calls), 2)

    def test_a_choice_made_by_hand_is_kept(self):
        self.settings.data["audio"]["device"] = "alsa/default:CARD=PCH"
        self.using[0] = "HDMI-A-2"
        self.assertEqual(self.api.apply_audio(), "alsa/default:CARD=PCH")
        self.assertEqual(self.api.ensure_audio(), "alsa/default:CARD=PCH")

    def test_no_mapping_keeps_mpvs_own_choice(self):
        self.using[0] = "VGA-1"                                          # a screen without HDMI sound
        self.assertEqual(self.api.apply_audio(), "auto")
        self.using[0] = "HDMI-A-1"
        self.ipc.devices = [d for d in PC_DEVICES if "PCH," not in d["name"]]    # mpv does not list the device
        self.assertEqual(self.api.apply_audio(), "auto")
        self.ipc.devices = PC_DEVICES
        self.api.SOUND_RUN = runner("", returncode=1)                    # amixer failed
        self.assertEqual(self.api.apply_audio(), "auto")
        with mock.patch("pvj.api.hardware.hdmi_sound_for", side_effect=OSError("boom")):
            self.assertEqual(self.api.apply_audio(), "auto")          # a read that fails never stops the sound

    def test_a_pi_is_unchanged(self):
        self.api.board = dict(self.api.board, kind="pi4")
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "auto")                # no vc4hdmi device listed: mpv's own, as before
        self.assertEqual(self.api.SOUND_RUN.calls, [])                  # and amixer is never asked
        self.assertEqual(self.call("GET", "/api/audio", token=self.full)[1]["automatic_note"], "")


if __name__ == "__main__":
    unittest.main()
