#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The test modules one part of CI's unit tests runs (D104).

    python3 tools/ci-shard.py K N      prints the modules of part K of N, one per line, as `tests.test_x`

The whole suite took 1,731 of its 1,800 seconds on 2026-10-20, so CI runs it as N parts (4 now) side by side. Every module
under tests/ named test_*.py is in exactly one part, so a new module is run without anyone listing it
(tests/test_ci_shard.py holds this). The parts are made even by the seconds each module took in CI: the heaviest
module goes to the lightest part, and so on down. A module that is not in the list below counts as a small one.

The seconds are from the Python 3.9 jobs of master's run 44183092942 (2026-11-17; each test's time is the gap between
its line and the one before it in the log), written in again on 2026-11-18 (J-019): part 1 of 3 had run 1,100 seconds
of tests against 850 and 950 for the others, with the 3 parts' sum at 2,900, so CI runs 4 parts of about 730 seconds.
It was first from the Python 3.9 job of master's run 39384981398 (2026-10-20). Only modules of ten seconds or more
are listed: they are about nine tenths of the time. When a part comes close to its limit again, take the times of a
new run (the unit step prints a line per test, each stamped by GitHub) and write them here. Added 2026-11-11, from the
Python 3.9 job of pull request 281: test_ticket, test_peer, test_cluster_remove, test_ticket_http, test_lease, test_sandbox and
test_carried_roles, which were counted as small, so adding one small module moved a hundred seconds from one part to another
and part 1 of 3.9 ran out of its twenty minutes; and test_strobe.
"""
import os
import sys

TESTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")
SMALL = 3                                       # seconds; the modules under ten took about three each
SECONDS = {
    "test_transitions": 170,
    "test_ndi_sound": 139,
    "test_nxlx_convert": 136,
    "test_roles": 129,
    "test_peer": 123,
    "test_projector": 116,
    "test_cluster_remove": 108,
    "test_handover_api": 89,
    "test_ticket": 88,
    "test_ticket_http": 85,
    "test_ndi": 82,
    "test_shader_engine": 79,
    "test_ndi_standin": 76,
    "test_lease": 72,
    "test_effects": 66,
    "test_shader_pads": 66,
    "test_boxcare": 52,
    "test_lights": 52,
    "test_renderer": 52,
    "test_server": 45,
    "test_sandbox": 44,
    "test_update": 39,
    "test_shaders": 38,
    "test_install_convert": 37,
    "test_project_log": 34,
    "test_room": 34,
    "test_osc_defaults": 32,
    "test_graphics": 28,
    "test_midi_layouts": 28,
    "test_carried_roles": 24,
    "test_graphics_motion": 24,
    "test_https": 24,
    "test_recovery": 23,
    "test_support_reach": 22,
    "test_install": 20,
    "test_join": 19,
    "test_sync": 19,
    "test_usb_media": 19,
    "test_000_tmp": 18,
    "test_mapping_mode": 17,
    "test_osc_lockdown": 17,
    "test_preview": 17,
    "test_strobe": 17,
    "test_timezone": 15,
    "test_theme_api": 14,
    "test_autocert": 13,
    "test_controller_code_review": 13,
    "test_handover": 13,
    "test_mapper": 13,
    "test_masks": 13,
    "test_ndi_pads": 13,
    "test_osc_feedback": 13,
    "test_controller_code": 12,
    "test_sound": 12,
    "test_looplist": 11,
    "test_sources": 11,
    "test_controllers": 10,
    "test_release": 10,
    "test_render": 10,
    "test_support": 10,
}


def modules():
    """Every test module, as unittest's discover finds them: tests/test_*.py."""
    return sorted(n[:-3] for n in os.listdir(TESTS) if n.startswith("test_") and n.endswith(".py"))


def parts(n):
    """The modules split into n lists of about the same seconds. The same files always give the same parts."""
    out = [[] for _ in range(n)]
    load = [0] * n
    for name in sorted(modules(), key=lambda m: (-SECONDS.get(m, SMALL), m)):
        i = load.index(min(load))
        out[i].append(name)
        load[i] += SECONDS.get(name, SMALL)
    return [sorted(p) for p in out]


def main(argv):
    if len(argv) != 3 or not argv[1].isdigit() or not argv[2].isdigit() or not 1 <= int(argv[1]) <= int(argv[2]):
        sys.stderr.write("usage: %s K N   (the part K of N, 1 <= K <= N)\n" % argv[0])
        return 2
    for name in parts(int(argv[2]))[int(argv[1]) - 1]:
        print("tests." + name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
