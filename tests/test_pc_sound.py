# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A PC node's Automatic sound goes to the HDMI or DisplayPort sound of the screen the picture is on (J-155, D137),
read from fake /proc/asound and /sys/class/drm trees."""
import shutil
import unittest
from unittest import mock

from pvj import hardware
from tests.test_audio import FakeIpc
from tests.test_hardware import make_tree
from tests.test_server import ServerBase


def edid(name):
    """A 128 byte EDID whose second descriptor is the monitor name (0xFC)."""
    data = bytearray(128)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    text = name.encode()[:13]
    text = text + (b"\n" + b" " * (12 - len(text)) if len(text) < 13 else b"")
    data[72:90] = b"\0\0\0\xfc\0" + text
    return bytes(data)


def eld(present, name="", kind="HDMI"):
    if not present:
        return "monitor_present\t\t0\neld_valid\t\t0\n"
    return "monitor_present\t\t1\neld_valid\t\t1\nmonitor_name\t\t%s\nconnection_type\t\t%s\n" % (name, kind)


def intel(pins, info=True, card="card0", card_id="PCH"):
    """An Intel HDA card: ELD files for codec 2, pins in order, and its HDMI PCMs 3, 7, 8 named HDMI 0, 1, 2."""
    files = {"proc/asound/%s/id" % card: card_id + "\n",
             "proc/asound/%s/pcm0p/info" % card: "card: 0\ndevice: 0\nid: ALC233 Analog\nname: ALC233 Analog\n"}
    for i, text in enumerate(pins):
        files["proc/asound/%s/eld#2.%d" % (card, i)] = text
        if info:
            dev = (3, 7, 8)[i]
            files["proc/asound/%s/pcm%dp/info" % (card, dev)] = "card: 0\ndevice: %d\nid: HDMI %d\nname: HDMI %d\n" % (dev, i, i)
    return files


def drm(**screens):
    """sysfs connectors: name (with _ for -) to a monitor name, or None for nothing connected."""
    files = {}
    for key, name in screens.items():
        conn = key.replace("_", "-")
        base = "sys/class/drm/card1-%s/" % conn
        files[base + "status"] = "connected\n" if name else "disconnected\n"
        files[base + "edid"] = edid(name) if name else b""
    return files


def conns(**screens):
    return [{"connector": k.replace("_", "-"), "status": "connected" if v else "disconnected", "modes": [], "card": 1}
            for k, v in screens.items()]


class HdmiSoundTreeTest(unittest.TestCase):
    def tree(self, *parts):
        files = {}
        for p in parts:
            files.update(p)
        root = make_tree(files)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def test_the_monitor_name_matches_the_pin_and_its_pcm(self):
        root = self.tree(intel([eld(False), eld(True, "LG HDR 4K"), eld(True, "DELL U2415")]),
                         drm(HDMI_A_1="DELL U2415", HDMI_A_2="LG HDR 4K"))
        self.assertEqual(hardware.edid_monitor_name("HDMI-A-1", root), "DELL U2415")
        c = conns(HDMI_A_1="x", HDMI_A_2="y")
        one = hardware.hdmi_sound_for("HDMI-A-1", c, root)
        two = hardware.hdmi_sound_for("HDMI-A-2", c, root)
        self.assertEqual((one["card_id"], one["device"], one["hdmi_index"], one["how"]), ("PCH", 8, 2, "monitor name"))
        self.assertEqual((two["device"], two["hdmi_index"]), (7, 1))

    def test_the_only_screen_takes_the_only_pin_without_a_name(self):
        root = self.tree(intel([eld(True, ""), eld(False), eld(False)]), drm(HDMI_A_1="", DP_1=None))
        found = hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="x", DP_1=None), root)
        self.assertEqual((found["device"], found["how"]), (3, "the only screen"))

    def test_pcm_numbers_come_from_the_driver_and_the_fixed_list_without_it(self):
        root = self.tree(intel([eld(False), eld(True, "A")], info=False), drm(HDMI_A_1="A"))
        self.assertEqual(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="A"), root)["device"], 7)

    def test_no_clue_gives_none(self):
        c = conns(HDMI_A_1="x", HDMI_A_2="y")
        # two screens of one model: no guess
        root = self.tree(intel([eld(True, "SAME"), eld(True, "SAME")]), drm(HDMI_A_1="SAME", HDMI_A_2="SAME"))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", c, root))
        # no monitor present on any pin, no sound card at all, a screen kind without sound
        root = self.tree(intel([eld(False), eld(False)]), drm(HDMI_A_1="A"))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="A"), root))
        root = self.tree(drm(HDMI_A_1="A"))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="A"), root))
        self.assertIsNone(hardware.hdmi_sound_for("VGA-1", conns(VGA_1="A"), root))
        # one screen and one pin that names another monitor: two screens, not one
        root = self.tree(intel([eld(True, "OTHER")]), drm(HDMI_A_1="MINE"))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="MINE"), root))
        # a DisplayPort pin is not an HDMI screen's
        root = self.tree(intel([eld(True, "A", "DisplayPort")]), drm(HDMI_A_1="A"))
        self.assertIsNone(hardware.hdmi_sound_for("HDMI-A-1", conns(HDMI_A_1="A"), root))
        self.assertEqual(hardware.hdmi_sound_for("DP-1", conns(DP_1="A"), self.tree(intel([eld(True, "A", "DisplayPort")]),
                                                                                   drm(DP_1="A")))["device"], 3)

    def test_odd_files_are_left_out(self):
        root = self.tree(intel([eld(True, "A")]), {"proc/asound/card0/eld#2.0.1": eld(True, "A"),      # an MST stream
                                                   "proc/asound/card1/id": "bad id;\n", "proc/asound/card1/eld#0.0": eld(True, "A"),
                                                   "sys/class/drm/card1-HDMI-A-1/edid": b"not an edid"})
        pins = hardware.hdmi_sound_pins(root)
        self.assertEqual([(p["card_id"], p["pin"]) for p in pins], [("PCH", 0)])
        self.assertIsNone(hardware.edid_monitor_name("HDMI-A-1", root))
        self.assertIsNone(hardware.edid_monitor_name("HDMI-A", root))


PC_DEVICES = [{"name": "auto", "description": "Autoselect device"},
              {"name": "alsa/default:CARD=PCH", "description": "HDA Intel PCH, ALC233 Analog"},
              {"name": "alsa/hdmi:CARD=PCH,DEV=0", "description": "HDA Intel PCH, HDMI 0"},
              {"name": "alsa/hdmi:CARD=PCH,DEV=1", "description": "HDA Intel PCH, HDMI 1"},
              {"name": "alsa/plughw:CARD=PCH,DEV=3", "description": "HDA Intel PCH, HDMI 0"},
              {"name": "alsa/plughw:CARD=PCH,DEV=7", "description": "HDA Intel PCH, HDMI 1"}]


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
        root = make_tree(dict(intel([eld(True, "DELL"), eld(True, "LG")]), **drm(HDMI_A_1="DELL", HDMI_A_2="LG")))
        self.addCleanup(shutil.rmtree, root, True)
        self.api.SOUND_ROOT = root
        self.using = [None]
        p = mock.patch("pvj.api.hardware.drm_connectors", return_value=conns(HDMI_A_1="DELL", HDMI_A_2="LG"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.api.screen, "report", side_effect=lambda: {"connector": self.using[0]} if self.using[0] else None)
        p.start()
        self.addCleanup(p.stop)

    def test_automatic_follows_the_screen_the_picture_is_on(self):
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")
        self.using[0] = "HDMI-A-2"                                       # the output changed: the player restarted on it
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=7")
        self.assertEqual(self.api.get_audio({}, None, None)["automatic_is"], "alsa/plughw:CARD=PCH,DEV=7")

    def test_without_a_report_the_chosen_output_then_the_first_hdmi(self):
        self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=3")    # HDMI-A-1 first, as the player chooses
        with mock.patch.object(self.api.screen, "saved_output", return_value="HDMI-A-2"):
            self.assertEqual(self.api.apply_audio(), "alsa/plughw:CARD=PCH,DEV=7")

    def test_the_hdmi_plugin_name_when_mpv_lists_no_plughw(self):
        self.ipc.devices = [d for d in PC_DEVICES if "plughw" not in d["name"]]
        self.using[0] = "HDMI-A-2"
        self.assertEqual(self.api.apply_audio(), "alsa/hdmi:CARD=PCH,DEV=1")

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
        self.api.SOUND_ROOT = "/nonexistent-pvj-test-root"
        self.assertEqual(self.api.apply_audio(), "auto")
        with mock.patch("pvj.api.hardware.hdmi_sound_for", side_effect=OSError("boom")):
            self.assertEqual(self.api.apply_audio(), "auto")          # a read that fails never stops the sound

    def test_a_pi_is_unchanged(self):
        self.api.board = dict(self.api.board, kind="pi4")
        self.using[0] = "HDMI-A-1"
        self.assertEqual(self.api.apply_audio(), "auto")                # no vc4hdmi device listed: mpv's own, as before


if __name__ == "__main__":
    unittest.main()
