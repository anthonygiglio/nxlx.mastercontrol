# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""CI's unit tests in parts side by side (D104, tools/ci-shard.py): every module runs once, the parts are even, and
the workflow runs each part with every rollback commit fetched, behind the two checks the ruleset requires."""
import importlib.util
import io
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout

import tests                                    # noqa: F401  the temp folder and the lock checker (tests/__init__.py)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("ci_shard", os.path.join(REPO, "tools", "ci-shard.py"))
shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shard)
PARTS = 3


def workflow():
    with open(os.path.join(REPO, ".github", "workflows", "pvj.yml")) as f:
        return f.read()


def job(text, name):
    """The lines of one job of the workflow, from its key to the next job's."""
    at = text.index("\n  %s:\n" % name)
    nxt = re.search(r"\n  [\w-]+:\n", text[at + 1:])
    return text[at:at + 1 + nxt.start()] if nxt else text[at:]


def fetched(text):
    return set(re.findall(r"\b[0-9a-f]{40}\b", text))


class Parts(unittest.TestCase):
    def test_every_module_is_in_exactly_one_part(self):
        found = sorted(n[:-3] for n in os.listdir(os.path.join(REPO, "tests"))
                       if n.startswith("test_") and n.endswith(".py"))
        self.assertIn("test_ci_shard", found)
        for n in (1, 2, PARTS, 5):
            parts = shard.parts(n)
            self.assertEqual(len(parts), n)
            every = [m for p in parts for m in p]
            self.assertEqual(sorted(every), found, "%d parts: each module once, none left out" % n)
            self.assertTrue(all(parts), "%d parts: none is empty" % n)

    def test_the_same_files_give_the_same_parts(self):
        self.assertEqual(shard.parts(PARTS), shard.parts(PARTS))

    def test_the_parts_are_even(self):
        weight = lambda p: sum(shard.SECONDS.get(m, shard.SMALL) for m in p)
        loads = [weight(p) for p in shard.parts(PARTS)]
        self.assertLess(max(loads), 1.1 * sum(loads) / PARTS, loads)

    def test_every_module_with_measured_seconds_is_still_there(self):
        # A module that was renamed or removed would leave its seconds here and its new name counted as small.
        modules = set(shard.modules())
        self.assertEqual([m for m in shard.SECONDS if m not in modules], [])

    def test_what_it_prints_is_one_part_as_unittest_names(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(shard.main(["ci-shard.py", "2", str(PARTS)]), 0)
        self.assertEqual(out.getvalue().split(), ["tests." + m for m in shard.parts(PARTS)[1]])

    def test_a_wrong_part_is_refused(self):
        for args in (["0", "3"], ["4", "3"], ["x", "3"], ["1"], ["-1", "3"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(shard.main(["ci-shard.py"] + args), 2, args)
            self.assertEqual(out.getvalue(), "", args)
            self.assertIn("usage", err.getvalue())


class Workflow(unittest.TestCase):
    def setUp(self):
        self.y = workflow()
        self.unit = job(self.y, "unit")

    def test_the_unit_job_runs_each_of_the_parts_once(self):
        self.assertIn("part: [%s]" % ", ".join(str(k) for k in range(1, PARTS + 1)), self.unit)
        self.assertIn("python: ['3.9', '3.12']", self.unit)
        self.assertIn("part ${{ matrix.part }} of %d)" % PARTS, self.unit)
        self.assertIn("python tools/ci-shard.py ${{ matrix.part }} %d)" % PARTS, self.unit)
        self.assertIn("-m unittest -v", self.unit)
        self.assertNotIn("discover", self.unit, "a part runs its own modules, not the whole suite")
        self.assertIn("fail-fast: false", self.unit)

    def test_the_unit_job_fetches_the_rollback_commits_and_master_before_its_part(self):
        # a rollback test that finds its commit missing fails (master after #152)
        self.assertGreater(len(fetched(self.unit)), 10)
        self.assertRegex(self.unit, r"git fetch --depth=1 origin \+master:refs/remotes/origin/master")
        self.assertLess(self.unit.index("origin/master"), self.unit.index("python tools/ci-shard.py"))

    def test_the_pis_mpv_job_runs_no_whole_suite(self):
        # Its whole suite "for information" never finished (12 of 12 runs stopped at 2,100 s a third of the way) and
        # made a required check 35 minutes longer (2026-10-23). The unit parts run every module; that job runs named
        # steps, so it needs no list of rollback commits either.
        ndi = job(self.y, "ndi-mpv")
        self.assertNotIn("discover", ndi)
        self.assertNotIn("The releases the rollback tests read", ndi)
        self.assertNotIn("continue-on-error", ndi)

    def test_the_required_checks_wait_for_every_part_and_fail_when_one_did_not_pass(self):
        gate = job(self.y, "test")
        self.assertIn("name: test (${{ matrix.python }})", gate)
        self.assertIn("python: ['3.9', '3.12']", gate)
        self.assertIn("needs: [unit, shaders-gpu]", gate)
        self.assertIn("if: always()", gate, "a skipped check counts as passed")
        self.assertIn('test "${{ needs.unit.result }}" = success', gate)
        self.assertIn("test \"${{ needs['shaders-gpu'].result }}\" = success", gate)

    def test_the_shader_steps_still_say_a_skip_is_a_failure(self):
        gpu = job(self.y, "shaders-gpu")
        for module in ("tests.test_shaders_gpu", "tests.test_shaderlive_gpu"):
            self.assertIn("PVJ_GPU_TEST=1 xvfb-run", gpu)
            self.assertIn(module, gpu)
        self.assertIn("PVJ_GPU_ONLY=desktop", gpu)
        self.assertIn("a skip here is a failure", gpu)


if __name__ == "__main__":
    unittest.main()
