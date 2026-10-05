# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Start the real panel with a headless mpv behind it, for browser tests.

Prints one JSON line {"port": ..., "pin": ..., "projector_ports": [...]} once it is listening. The projector ports
are two fake PJLink projectors on loopback (the one from tests/test_projector.py, written from the standard):
the first is switched on, has the password "secret1" and a filter warning; the second is in standby; a third (on)
and a fourth (in standby) stay in cooling down and warming up once switched. Loopback is
allowed as a projector address in this harness only, so the browser test never contacts a device on the network.
"""
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from http.server import ThreadingHTTPServer  # noqa: E402

from pvj import server  # noqa: E402
from pvj.player import Player  # noqa: E402
from tests.test_server import make_test_video  # noqa: E402

tmp = tempfile.mkdtemp()
media = os.path.join(tmp, "video")
os.makedirs(media)
clip = make_test_video(media)
for name in ("intro.mkv", "tunnel.mkv"):
    if clip:
        os.link(clip, os.path.join(media, name))
rundir = os.path.join(tmp, "run")
os.makedirs(rundir, mode=0o700)
os.environ.update(PVJ_STATE_DIR=tmp, PVJ_MEDIA_DIR=media, PVJ_DEV_SPAWN="1")
player = Player(extra_args=["--vo=null", "--ao=null"], rundir=rundir)
api, auth, _ = server.build(os.environ, player=player)
from tests.test_netd import FakeNm, make_sysfs  # noqa: E402
from pvj.netd import NetService  # noqa: E402

_fake_nm = FakeNm()
_fake_nm.scan_text = ("Leyline Staff:72:WPA2:36\nVenue Guest:40::1\nCorp:60:WPA2 802.1X:6\n"
                      "<img src=x onerror=pwned=1>:30:WPA2:11\n")   # names come from strangers
_keyfiles = os.path.join(tmp, "nm-connections")
os.makedirs(_keyfiles, mode=0o700)
_net_service = NetService(runner=_fake_nm, sysfs=make_sysfs(), keyfile_dir=_keyfiles)


class _DirectNet:
    def request(self, message):
        return _net_service.handle(message)


api.net = _DirectNet()
api._sysfs = _net_service.sysfs
api._ip_json = lambda: [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]
import ipaddress  # noqa: E402
from pvj import projector  # noqa: E402
from tests.test_projector import FakeProjector  # noqa: E402

projector.PRIVATE = projector.PRIVATE + [ipaddress.ip_network("127.0.0.0/8")]
_fakes = [FakeProjector("secret1"), FakeProjector(slow=True, lamps=(310, 295))]
_fakes[0].power, _fakes[0].errors = "1", "000010"
# Two more, for the pictures and checks of every power state (tests/ui/signal-pages.js): both slow, so the third,
# switched off, stays "cooling down" and the fourth, switched on, stays "warming up". Nothing uses them otherwise.
_fakes += [FakeProjector(slow=True, lamps=(2210,)), FakeProjector(slow=True, lamps=(48,)), FakeProjector(lamps=(7,))]
_fakes[2].power = "1"      # (the fifth is one nobody switches: in standby, so "off" is on the page whatever earlier steps did)

# Two fake MIDI controllers, pipes in place of device files: a Korg nanoKONTROL2 (a controller with a shipped profile)
# and "keys" (one without). They are "plugged in" while the file <midi_dir>/plug exists, and every line of hex bytes
# appended to <midi_dir>/in ("B0 00 40", or "keys: 90 3C 7F" for the second one) is what the controller sends. The
# harness keeps the writing end of each pipe, so a reader never sees the end of the stream.
import time  # noqa: E402
from pvj import midi as midi_mod  # noqa: E402

_midi_dir = os.path.join(tmp, "midi")
os.makedirs(_midi_dir)
_midi_names = {"/dev/snd/midiC7D0": "nanoKONTROL2", "/dev/snd/midiC8D0": "keys"}
# A third, plugged in only while <midi_dir>/plug-launchpad exists: a Novation Launchpad Mini, whose profile has lights
# with a brightness. For the pictures and checks of a style (tests/ui/signal-pages.js); the steps of the browser test
# that count controllers never make that file.
_midi_pad = "/dev/snd/midiC9D0"
_midi_names[_midi_pad] = "Mini"
_midi_pipes = {}


def _midi_open(path):
    r, w = os.pipe()
    _midi_pipes[_midi_names[path]] = w
    return r


def _midi_feed():
    done = 0
    while True:
        time.sleep(0.1)
        try:
            with open(os.path.join(_midi_dir, "in")) as f:
                lines = f.read().split("\n")[:-1]
        except OSError:
            continue
        for line in lines[done:]:
            name, _, data = line.rpartition(":")
            w = _midi_pipes.get(name.strip() or "nanoKONTROL2")
            if w is not None:
                os.write(w, bytes.fromhex(data.replace(" ", "")))
        done = len(lines)


def _midi_light_open(path):
    """The lights: what the box writes to a fake controller ends up in <midi_dir>/out, one message a line in hex
    ("b0 20 7f"). Another pipe; no device."""
    r, w = os.pipe()
    os.set_blocking(w, False)

    def drain():
        rest = b""
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                return
            rest += chunk
            whole = len(rest) - len(rest) % 3
            with open(os.path.join(_midi_dir, "out"), "a") as f:
                for i in range(0, whole, 3):
                    f.write(rest[i:i + 3].hex(" ") + "\n")
            rest = rest[whole:]
    threading.Thread(target=drain, daemon=True).start()
    return w


api.midi.stop()
api.midi = midi_mod.MidiHub(api, api.settings, open_fn=_midi_open, namer=lambda p: _midi_names[p], describer=lambda p: "Launchpad Mini" if p == _midi_pad else _midi_names[p],
                            lister=lambda: [p for p in sorted(_midi_names) if os.path.exists(os.path.join(_midi_dir, "plug-launchpad" if p == _midi_pad else "plug"))],
                            scan_interval=0.3,
                            light_open_fn=_midi_light_open)
api.midi.apply()
threading.Thread(target=_midi_feed, daemon=True).start()

httpd = server.PvjServer(("127.0.0.1", 0), server.make_handler(api, auth))
print(json.dumps({"port": httpd.server_address[1], "pin": auth.current_pin, "projector_ports": [f.port for f in _fakes], "midi_dir": _midi_dir}), flush=True)
try:
    httpd.serve_forever()
finally:
    player.stop()
