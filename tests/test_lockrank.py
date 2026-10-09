# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The locks that know their place (tests/lockrank.py) and the order they keep (pvj/locks.py, D71)."""
import threading
import unittest

from pvj import locks
from tests import lockrank


class Order(unittest.TestCase):
    def tearDown(self):
        lockrank.take()                     # what these tests break on purpose is not the next test's

    def test_the_tests_run_with_locks_that_know_their_place(self):
        self.assertIs(locks.factory, lockrank.Ranked, "tests/__init__.py did not put the checker in: the order is not checked")
        self.assertIsInstance(locks.make("player", reentrant=True), lockrank.Ranked)

    def test_in_production_a_lock_is_a_plain_lock(self):
        self.assertIsInstance(locks.plain("player"), type(threading.Lock()))
        self.assertIsInstance(locks.plain("player", reentrant=True), type(threading.RLock()))

    def test_a_lock_without_a_place_cannot_be_made(self):
        with self.assertRaises(ValueError):
            locks.make("somebody's new lock")

    def test_in_order_is_fine(self):
        player, stepping, token = locks.make("player", reentrant=True), locks.make("fader.stepping"), locks.make("fader.lock")
        with player:
            with stepping:
                with token:
                    pass
        self.assertEqual(lockrank.take(), [])

    def test_out_of_order_raises_at_once_in_one_thread_and_is_written_down(self):
        # the sixth review's deadlock needed two threads to meet; the order is broken as soon as one thread does this
        player, stepping = locks.make("player", reentrant=True), locks.make("fader.stepping")
        with stepping:
            with self.assertRaises(AssertionError) as caught:
                player.acquire()
        said = str(caught.exception)
        self.assertIn("`player`", said)
        self.assertIn("`fader.stepping`", said)
        self.assertIn("test_lockrank.py", said, "the place where the held lock was taken is not shown")
        self.assertEqual(len(lockrank.take()), 1)
        self.assertFalse(player.locked())

    def test_it_is_written_down_also_when_the_code_swallows_the_exception(self):
        player, stepping = locks.make("player", reentrant=True), locks.make("fader.stepping")
        with stepping:
            try:
                with player:
                    pass
            except Exception:
                pass                        # as the code under test does in many places
        self.assertEqual(len(lockrank.take()), 1)

    def test_taking_again_a_lock_that_is_held_is_not_a_second_lock(self):
        player, stepping = locks.make("player", reentrant=True), locks.make("fader.stepping")
        with player:
            with stepping:
                pass
            with player:                    # again, with nothing later held
                with stepping:
                    pass
        with player:
            with stepping:
                with player:                # again, while a later one is held: still the same lock
                    pass
        self.assertEqual(lockrank.take(), [])

    def test_trying_without_waiting_breaks_no_order(self):
        engine, player = locks.make("shaders.engine", reentrant=True), locks.make("player", reentrant=True)
        with player:
            self.assertTrue(engine.acquire(blocking=False))
            engine.release()
        self.assertEqual(lockrank.take(), [])

    def test_two_locks_of_one_place_are_not_held_together(self):
        one, other = locks.make("player", reentrant=True), locks.make("player", reentrant=True)
        with one:
            with self.assertRaises(AssertionError):
                other.acquire()
        a, b = locks.make("vibes.state"), locks.make("mapper.state")       # the leaves share the last place
        with a:
            with self.assertRaises(AssertionError):
                b.acquire()

    def test_what_one_thread_holds_is_not_another_threads(self):
        player, stepping = locks.make("player", reentrant=True), locks.make("fader.stepping")
        seen = []

        def other():
            try:
                with player:
                    seen.append("taken")
            except AssertionError as e:
                seen.append(e)
        with stepping:
            t = threading.Thread(target=other)
            t.start()
            t.join(5)
        self.assertEqual(seen, ["taken"])
        self.assertEqual(lockrank.take(), [])

    def test_the_real_objects_are_made_of_these_locks(self):
        from pvj.api import Fader
        from pvj.player import Player
        from pvj.transitions import Transitions
        fader = Fader(lambda level: None)
        self.assertEqual((fader.stepping.name, fader._lock.name), ("fader.stepping", "fader.lock"))
        self.assertEqual(Player._lock.name, "player")
        tr = Transitions(api=None)
        self.assertEqual((tr._holding.name, tr._io.name, tr._mark.name), ("transitions.holding", "transitions.io", "transitions.mark"))

    def test_the_second_rule_the_players_lock_stands_before_the_levels(self):
        order = locks.ORDER
        self.assertLess(order.index("player"), order.index("fader.stepping"))
        self.assertLess(order.index("fader.stepping"), order.index("fader.lock"))
        self.assertLess(order.index("capture"), order.index("player"))
        self.assertLess(order.index("shaders.engine"), order.index("player"))


if __name__ == "__main__":
    unittest.main()
