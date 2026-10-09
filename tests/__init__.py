# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Every run of the tests gets a temp folder of its own, removed when the run ends (D69, tests/tmpguard.py).
# `python3 -m unittest discover -s tests` does not import this package by itself; tests/test_000_tmp.py, the first
# module it loads, does.
from . import tmpguard

tmpguard.activate()
