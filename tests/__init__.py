# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Every run of the tests gets a temp folder of its own, removed when the run ends (D69, tests/tmpguard.py).
# `python3 -m unittest discover -s tests` does not import this package by itself; tests/test_000_tmp.py, the first
# module it loads, does.
import atexit
import os
import sys
import unittest

from . import lockrank, tmpguard

# The locks of playing, stopping and the picture's level know their place in every test (tests/lockrank.py, D71): a
# taking that breaks the order raises where it happens and is written down, and the test that was running fails,
# also when the code under test swallowed the exception. A breach is never thrown away: one that a thread wrote
# down between two tests fails the next test, with the name of the test that was running when it happened, and one
# that is left when the run ends makes the run fail (the seventh review: it used to be dropped at the next test's
# start). Every tests/test_*.py imports this package, so this holds however a module is started, also by
# `discover -s tests -p test_x.py` (tests/test_lockrank.py looks at both).
_left = []


def _at_end():
    """Registered before the temp folder's own tidying, so it runs after it: nothing else is cut short."""
    _left.extend(lockrank.take())
    if _left:
        sys.stdout.flush()
        sys.stderr.write("\nFAILED: %d breach(es) of the order of the locks (pvj/locks.py) that no test showed:\n%s\n"
                         % (len(_left), "\n".join(_left)))
        sys.stderr.flush()
        os._exit(1)


atexit.register(_at_end)
tmpguard.activate()
lockrank.install()
_run = unittest.TestCase.run


def _run_and_look(self, result=None):
    _left.extend(lockrank.take())           # written down between two tests, by a thread an earlier test left
    lockrank.during = self.id()
    try:
        out = _run(self, result)
    finally:
        lockrank.during = "no test (the last one was %s)" % self.id()
    _left.extend(lockrank.take())
    if _left and result is not None:
        seen = list(_left)
        del _left[:]
        try:
            raise AssertionError("%d breach(es) of the order of the locks (pvj/locks.py):\n%s" % (len(seen), "\n".join(seen)))
        except AssertionError:
            result.addFailure(self, sys.exc_info())
    return out


unittest.TestCase.run = _run_and_look
