# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The locks that know their place (tests/lockrank.py) and the order they keep (pvj/locks.py, D71)."""
import ast
import glob
import os
import subprocess
import sys
import threading
import unittest

from pvj import locks
from tests import lockrank


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python(code_or_args, **env):
    args = code_or_args if isinstance(code_or_args, list) else ["-c", code_or_args]
    return subprocess.run([sys.executable] + args, cwd=REPO, env=dict(os.environ, **env), capture_output=True, text=True,
                          timeout=300)


class NeverLost(unittest.TestCase):
    """A breach that was written down is shown by a failure, whenever it happened (the seventh review's M3)."""

    def tearDown(self):
        import tests
        lockrank.take()
        del tests._left[:]

    def inner(self):
        class Quiet(unittest.TestCase):
            def test_nothing(self):
                pass
        result = unittest.TestResult()
        Quiet("test_nothing").run(result)
        return result

    def break_the_order(self):
        player, stepping = locks.make("player", reentrant=True), locks.make("fader.stepping")
        with stepping:
            try:
                with player:
                    pass
            except AssertionError:
                pass                                # as the code under test does in many places

    def test_a_breach_between_two_tests_fails_the_next_one_and_names_the_one_before(self):
        self.assertEqual(self.inner().failures, [])
        self.break_the_order()                      # after a test's end, as a thread it left behind would
        result = self.inner()
        self.assertEqual(len(result.failures), 1)
        said = result.failures[0][1]
        self.assertIn("the order of the locks", said)
        self.assertIn("no test (the last one was", said)
        self.assertIn("Quiet.test_nothing", said)
        self.assertEqual(self.inner().failures, [], "the same breach failed a second test")

    def test_a_breach_names_the_test_that_was_running(self):
        outer = self

        class Breaks(unittest.TestCase):
            def test_it(self):
                outer.break_the_order()
        result = unittest.TestResult()
        Breaks("test_it").run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("Breaks.test_it ran, the order of the locks was broken", result.failures[0][1])

    def test_a_breach_seen_without_a_result_is_kept_for_the_next_test(self):
        import tests
        self.break_the_order()
        unittest.TestCase.run(type("Q", (unittest.TestCase,), {"test_x": lambda self: None})("test_x"), None)
        self.assertEqual(len(tests._left), 1)

    def test_a_breach_that_no_test_showed_fails_the_run_at_its_end(self):
        code = ("import tests\nfrom pvj import locks\na, b = locks.make('player', True), locks.make('fader.stepping')\n"
                "with b:\n    try:\n        a.acquire()\n    except AssertionError:\n        pass\n")
        done = python(code)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("that no test showed", done.stderr)
        self.assertIn("`player` (place", done.stderr)
        done = python("import tests\nfrom pvj import locks\nwith locks.make('player', True):\n    pass\n")
        self.assertEqual((done.returncode, done.stderr), (0, ""))


class AlwaysOn(unittest.TestCase):
    """The checker is on however a test module is started (the seventh review's M3: `discover -s tests -p
    test_player.py` loads the module without the package, and the locks were plain ones)."""

    def test_every_test_module_imports_the_package(self):
        without = []
        for path in sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py"))):
            with open(path) as f:
                tree = ast.parse(f.read())
            names = [a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names]
            names += [n.module or "" for n in tree.body if isinstance(n, ast.ImportFrom) and not n.level]
            if not any(n == "tests" or n.startswith("tests.") for n in names):
                without.append(os.path.basename(path))
        self.assertEqual(without, [], "these modules do not import the `tests` package at their top (add `import tests`): "
                         "started by themselves they would run with plain locks and without a temp folder of their own")

    def test_a_module_started_by_discover_alone_runs_with_the_checker(self):
        code = ("import sys, unittest\nfrom pvj import locks\n"
                "p = unittest.main(module=None, argv=['x', 'discover', '-s', 'tests', '-p', 'test_player_layers.py'], exit=False)\n"
                "on = locks.factory.__name__ == 'Ranked' and locks.factory.__module__.endswith('lockrank')\n"
                "sys.exit(0 if on and p.result.testsRun and p.result.wasSuccessful() else 3)\n")
        done = python(code)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_breach_in_a_module_started_by_discover_alone_fails_it(self):
        import tempfile
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        with open(os.path.join(folder, "test_breaks.py"), "w") as f:
            f.write("import unittest\nimport tests\nfrom pvj import locks\n\n\nclass T(unittest.TestCase):\n"
                    "    def test_it(self):\n        a, b = locks.make('player', True), locks.make('fader.stepping')\n"
                    "        with b:\n            try:\n                a.acquire()\n            except AssertionError:\n"
                    "                pass\n")
        done = python(["-m", "unittest", "discover", "-s", folder, "-p", "test_breaks.py"])
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("the order of the locks", done.stderr)


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
