# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""No browser test waits with an async function in page.waitForFunction.

Playwright polls the function until it returns something truthy. An async function returns a Promise, which is
truthy at once, so the wait is over on its first look whatever the check says: nine such waits in
tests/ui/panel.test.js passed that way for months. A check that fetches from the node goes through
`until` in tests/ui/poll.js, which polls one page.evaluate at a time.
"""
import pathlib
import re
import unittest

UI = pathlib.Path(__file__).resolve().parent / 'ui'
BAD = re.compile(r'waitForFunction\s*\(\s*(?:async\b|\(?[\w\s,]*\)?\s*=>\s*await\b)')


class UiWaitsTest(unittest.TestCase):
    def test_no_async_waitForFunction(self):
        files = sorted(UI.glob('*.js'))
        self.assertTrue(files, 'no tests/ui/*.js found: the check looks in the wrong place')
        found = []
        for f in files:
            for n, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
                if BAD.search(line):
                    found.append('%s:%d' % (f.name, n))
        self.assertEqual(found, [], 'waitForFunction with an async function never polls: use until() from tests/ui/poll.js')

    def test_check_sees_the_bad_spellings(self):
        self.assertTrue(BAD.search("await page.waitForFunction(async () => x, null, {})"))
        self.assertTrue(BAD.search("page.waitForFunction( async () => 1)"))
        self.assertFalse(BAD.search("page.waitForFunction(() => fetch('/a').then((r) => r.json()))"))


if __name__ == '__main__':
    unittest.main()
