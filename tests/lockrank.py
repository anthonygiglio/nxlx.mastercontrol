# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Locks that know their place (pvj/locks.py, D71), for the tests. Tests only.

Six reviews each found an order of events that made two threads wait for each other or act in the wrong order, and
the last one found a real deadlock that readers and a stress test had missed. A lock order kept by readers is not
kept. With this module's factory in pvj.locks (tests/__init__.py puts it there for every test), each lock of the
order remembers, per thread, what that thread holds. A thread that waits for a lock while it holds one that stands
LATER in the order breaks the rule: the taking raises AssertionError, with both locks' names and the place in the
code where the held one was taken, and the breach is also written down, because the code under test catches
exceptions in many places; tests/__init__.py fails the test that was running when a breach was written down.

So an inversion shows as soon as each of the two paths has merely run in some test, in one thread, without the
interleaving that would hang.

What is not a breach: trying a lock without waiting (it cannot make anybody wait for ever), and taking again an
RLock that the thread already holds.
"""
import threading
import traceback

from pvj import locks

_held = threading.local()           # .stack: [(place, name, lock, where it was taken)]
breaches = []                       # what was seen, for the test that is running
_breaches_lock = threading.Lock()


def _stack():
    try:
        return _held.stack
    except AttributeError:
        _held.stack = []
        return _held.stack


def place(name):
    return len(locks.ORDER) - len(locks.LEAVES) if name in locks.LEAVES else locks.ORDER.index(name)


class Ranked:
    def __init__(self, name, reentrant=False):
        self.name, self.place, self.reentrant = name, place(name), reentrant
        self._lock = threading.RLock() if reentrant else threading.Lock()
        self._owner, self._count = None, 0

    def _mine(self):
        return self._owner == threading.get_ident()

    def acquire(self, blocking=True, timeout=-1):
        again = self.reentrant and self._mine()
        if blocking and not again:
            for held_place, held_name, held_lock, where in _stack():
                if held_place >= self.place and held_lock is not self:
                    said = ("the order of the locks is broken: `%s` (place %d) is waited for while `%s` (place %d) is held.\n"
                            "`%s` was taken here:\n%s`%s` is waited for here:\n%s"
                            % (self.name, self.place + 1, held_name, held_place + 1, held_name, where, self.name,
                               "".join(traceback.format_stack(limit=12)[:-1])))
                    with _breaches_lock:
                        breaches.append(said)
                    raise AssertionError(said)
        got = self._lock.acquire(blocking, timeout) if blocking else self._lock.acquire(False)
        if got:
            self._owner = threading.get_ident()
            self._count += 1
            if not again:
                _stack().append((self.place, self.name, self, "".join(traceback.format_stack(limit=10)[:-1])))
        return got

    def release(self):
        self._count -= 1
        if self._count == 0:
            self._owner = None
            stack = _stack()
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][2] is self:
                    del stack[i]
                    break
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def locked(self):
        return self._count > 0

    def _is_owned(self):
        return self._mine()


def take():
    """What was written down since the last call, and forget it."""
    with _breaches_lock:
        out = list(breaches)
        del breaches[:]
    return out


def install():
    locks.factory = Ranked
