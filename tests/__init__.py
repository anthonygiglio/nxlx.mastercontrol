# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Every run of the tests gets a temp folder of its own, removed when the run ends (D69, tests/tmpguard.py).
# `python3 -m unittest discover -s tests` does not import this package by itself; tests/test_000_tmp.py, the first
# module it loads, does.
import unittest

from . import lockrank, tmpguard

tmpguard.activate()

# The locks of playing, stopping and the picture's level know their place in every test (tests/lockrank.py, D71): a
# taking that breaks the order raises where it happens and is written down, and the test that was running fails,
# also when the code under test swallowed the exception.
lockrank.install()
_run = unittest.TestCase.run


def _run_and_look(self, result=None):
    lockrank.take()
    out = _run(self, result)
    seen = lockrank.take()
    if seen and result is not None:
        try:
            raise AssertionError("%d breach(es) of the order of the locks (pvj/locks.py):\n%s" % (len(seen), seen[0]))
        except AssertionError:
            import sys
            result.addFailure(self, sys.exc_info())
    return out


unittest.TestCase.run = _run_and_look
