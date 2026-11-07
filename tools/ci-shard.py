#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The test modules one part of CI's unit tests runs (D104).

    python3 tools/ci-shard.py K N      prints the modules of part K of N, one per line, as `tests.test_x`

The whole suite took 1,731 of its 1,800 seconds on 2026-10-20, so CI runs it as N parts side by side. Every module
under tests/ named test_*.py is in exactly one part, so a new module is run without anyone listing it
(tests/test_ci_shard.py holds this). The parts are made even by the seconds each module took in CI: the heaviest
module goes to the lightest part, and so on down. A module that is not in the list below counts as a small one.

The seconds are from the Python 3.9 job of master's run 39384981398 (2026-10-20). Only modules of ten seconds or more
are listed: they are about nine tenths of the time. When a part comes close to its limit again, take the times of a
new run (the unit step prints a line per test, each stamped by GitHub) and write them here.
"""
import os
import sys

TESTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")
SMALL = 3                                       # seconds; the modules under ten took about three each
SECONDS = {
    "test_transitions": 152,
    "test_roles": 141,
    "test_ndi_sound": 133,
    "test_projector": 112,
    "test_ndi": 79,
    "test_shader_engine": 78,
    "test_ndi_standin": 77,
    "test_shader_pads": 69,
    "test_effects": 59,
    "test_lights": 52,
    "test_boxcare": 49,
    "test_server": 46,
    "test_renderer": 44,
    "test_shaders": 39,
    "test_room": 36,
    "test_osc_defaults": 31,
    "test_midi_layouts": 28,
    "test_recovery": 26,
    "test_support_reach": 23,
    "test_https": 22,
    "test_update": 21,
    "test_000_tmp": 20,
    "test_usb_media": 20,
    "test_join": 18,
    "test_sync": 18,
    "test_mapping_mode": 17,
    "test_osc_feedback": 17,
    "test_preview": 17,
    "test_osc_lockdown": 16,
    "test_controller_code_review": 14,
    "test_theme_api": 14,
    "test_controller_code": 13,
    "test_ndi_pads": 13,
    "test_mapper": 12,
    "test_install": 10,
    "test_render": 10,
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
