# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Every run of the tests gets a temp folder of its own, removed when the run ends (D69, tests/tmpguard.py).
# `python3 -m unittest discover -s tests` does not import this package by itself; tests/test_000_tmp.py, the first
# module it loads, does.
import atexit
import os
import re
import sys
import threading
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
# The workers of pvj's that look at something again and again must be ended by the test that started them: one that
# is left goes on looking in the middle of the tests that come after (the effects' worker, "shader-changes", looks
# at the picture once a second for as long as an effect is on, and every look scans a folder: 34 scans in another
# test's count). Only these names are looked for, after every test, cleanups included; other threads are not policed.
WATCHERS = ("shader-changes",)
_blamed = set()                         # a thread is one test's fault: the tests after it are not failed for it too


def pin_stands_in(pin, text):
    """Whether the four-digit PIN is in the text as a word of its own. A guest code, a port, a token, a device id or
    a hash is random and can hold the same four digits by chance (seen in CI: the PIN 6283 inside the guest code
    628324), so a plain `in` fails a sound test now and then. A letter counts as a neighbour as well as a digit, so
    that hex (a token, an id, the PIN's own hash and salt) never matches; a leak of the PIN shows it between quotes,
    spaces or punctuation, which this finds. test_boxcare and test_pin_and_logout look for it the same way."""
    return re.search(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(pin), text) is not None


def left_watchers():
    """The watchers that are alive now and that no test has been blamed for yet."""
    found = [t for t in threading.enumerate() if t.name in WATCHERS and t.is_alive() and t not in _blamed]
    _blamed.update(found)
    return found


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
    stray = left_watchers()
    if stray and result is not None:
        try:
            raise AssertionError("left running after the test: %s (end it in a cleanup, see shaderlive.Changer.stop "
                                 "and tests/test_server.py's stop_workers)" % ", ".join(sorted(t.name for t in stray)))
        except AssertionError:
            result.addFailure(self, sys.exc_info())
    if _left and result is not None:
        seen = list(_left)
        del _left[:]
        try:
            raise AssertionError("%d breach(es) of the order of the locks (pvj/locks.py):\n%s" % (len(seen), "\n".join(seen)))
        except AssertionError:
            result.addFailure(self, sys.exc_info())
    return out


unittest.TestCase.run = _run_and_look
