# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""No browser test gives page.waitForFunction a check that returns a Promise.

Playwright polls the function until it returns something truthy. A Promise is truthy at once, so a wait whose check
is async, or is fetch(...).then(...), or awaits anything, is over on its first look whatever the check says: 46 such
waits in tests/ui passed that way, one of them (never true) after 36 ms of a 1.5 s timeout. A check that fetches
or awaits goes through `pollFor` in tests/ui/poll.js, which polls one page.evaluate at a time.

The files are read whole (a call may run over several lines, and a comment may come before it): the comments are
blanked, then each waitForFunction( has its first argument cut out by matching brackets. That argument is refused
when it holds async, await, fetch(, .then( or Promise, or when it is a name whose definition in the same file does.
A name with no definition in the file (a parameter) cannot be judged and is let through.
"""
import tests  # noqa: F401 (started alone, it still gets the checked locks and its own temp folder)
import pathlib
import re
import unittest

UI = pathlib.Path(__file__).resolve().parent / 'ui'
BS = chr(92)
QUOTES = '\'"`'
PROMISE = re.compile(r'\basync\b|\bawait\b|\bfetch\s*\(|\.then\s*\(|\bPromise\b')
NAME = re.compile(r'\s*([A-Za-z_$][\w$]*)\s*(?:\|\|\s*([A-Za-z_$][\w$]*)\s*)?')


def blank_comments(s):
    """s with every // and /* */ comment turned into spaces (newlines kept), so offsets and lines are unchanged."""
    out = list(s)
    i, q = 0, None
    while i < len(s):
        c = s[i]
        if q:
            if c == BS:
                i += 1
            elif c == q:
                q = None
        elif c in QUOTES:
            q = c
        elif s.startswith('//', i):
            j = s.find('\n', i)
            j = len(s) if j < 0 else j
            for k in range(i, j):
                out[k] = ' '
            i = j
            continue
        elif s.startswith('/*', i):
            j = s.find('*/', i + 2)
            j = len(s) if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != '\n':
                    out[k] = ' '
            i = j
            continue
        i += 1
    return ''.join(out)


def first_argument(s, i):
    """The text of the first argument of the call whose opening bracket ends just before index i."""
    d, q, k = 0, None, i
    while k < len(s):
        c = s[k]
        if q:
            if c == BS:
                k += 1
            elif c == q:
                q = None
        elif c in QUOTES:
            q = c
        elif c in '([{':
            d += 1
        elif c in ')]}':
            if d == 0:
                break
            d -= 1
        elif c == ',' and d == 0:
            break
        k += 1
    return s[i:k]


def definition(s, name):
    """The text of what `name` is set to in s (const/let/var name = ..., or function name(...) {...}), or None."""
    m = re.search(r'(?:\b(?:const|let|var)\s+%s\s*=|\bfunction\s+%s\s*\()' % (re.escape(name), re.escape(name)), s)
    if not m:
        return None
    d, q, k = 0, None, m.end()
    start = k
    while k < len(s):
        c = s[k]
        if q:
            if c == BS:
                k += 1
            elif c == q:
                q = None
        elif c in QUOTES:
            q = c
        elif c in '([{':
            d += 1
        elif c in ')]}':
            d -= 1
        elif c == ';' and d <= 0:
            break
        k += 1
    return s[start:k]


def promises(code):
    """True when code (a check, or what a name is set to) holds a Promise-making word outside its quoted strings."""
    plain = re.sub(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", "''", code)
    return bool(PROMISE.search(plain))


def promising_waits(text):
    """(line number, first argument) of every waitForFunction in text whose check can return a Promise."""
    s = blank_comments(text)
    found = []
    for m in re.finditer(r'\bwaitForFunction\s*\(', s):
        first = first_argument(s, m.end())
        line = s.count('\n', 0, m.start()) + 1
        names = NAME.fullmatch(first)
        if names:
            bad = any(promises(definition(s, n) or '') for n in names.groups() if n)
        else:
            bad = promises(first)
        if bad:
            found.append((line, ' '.join(first.split())[:100]))
    return found


class UiWaitsTest(unittest.TestCase):
    def test_no_waitForFunction_with_a_promise(self):
        files = sorted(UI.glob('*.js'))
        self.assertTrue(files, 'no tests/ui/*.js found: the check looks in the wrong place')
        found = []
        for f in files:
            found += ['%s:%d  %s' % (f.name, n, a) for n, a in promising_waits(f.read_text(encoding='utf-8'))]
        self.assertEqual(found, [], 'waitForFunction with a check that returns a Promise never polls: use pollFor() from tests/ui/poll.js')

    def test_the_check_sees_every_spelling(self):
        bad = [
            "await page.waitForFunction(async () => x, null, {});",
            "page.waitForFunction( async () => 1)",
            "page.waitForFunction(() => fetch('/a').then((r) => r.json()))",
            "page.waitForFunction((v) => fetch('/a')\n  .then((r) => r.json())\n  .then((d) => d.x === v), 3, { timeout: 5 });",
            "page.waitForFunction(() => { return Promise.resolve(1); })",
            "const ok = async () => 1;\nawait page.waitForFunction(ok, null, {});",
            "const ok = () => fetch('/a');\nawait page.waitForFunction(ok || other, null, {});",
            "// a comment first\n/* and another */\nawait pg\n  .waitForFunction(\n    async () => 1,\n    null);",
        ]
        for text in bad:
            self.assertTrue(promising_waits(text), text)

    def test_the_check_lets_the_plain_ones_through(self):
        fine = [
            "page.waitForFunction(() => !!document.querySelector('#a'), null, { timeout: 5 });",
            "const ok = () => !!document.body;\nawait page.waitForFunction(ok, null, {});",
            "page.waitForFunction(fn, arg, { timeout: 5 });",
            "// page.waitForFunction(async () => 1) is how it went wrong\nawait until(page, async () => 1, 5);",
            "/* waitForFunction(() => fetch('/a')) */ x();",
            "page.waitForFunction(() => document.title === 'fetch(me)', null, {});",
        ]
        for text in fine:
            self.assertEqual(promising_waits(text), [], text)


if __name__ == '__main__':
    unittest.main()
