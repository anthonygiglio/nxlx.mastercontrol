# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Start the real panel with a headless mpv behind it, for browser tests.

Prints one JSON line {"port": ..., "pin": ..., "projector_ports": [...]} once it is listening. The projector ports
are two fake PJLink projectors on loopback (the one from tests/test_projector.py, written from the standard):
the first is switched on, has the password "secret1" and a filter warning; the second is in standby. Loopback is
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
_net_service = NetService(runner=_fake_nm, sysfs=make_sysfs())


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

httpd = server.PvjServer(("127.0.0.1", 0), server.make_handler(api, auth))
print(json.dumps({"port": httpd.server_address[1], "pin": auth.current_pin, "projector_ports": [f.port for f in _fakes]}), flush=True)
try:
    httpd.serve_forever()
finally:
    player.stop()
