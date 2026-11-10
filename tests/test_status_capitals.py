# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A status line a module sets is shown as it is, as a sentence: on Now under Needs you, on Health and on the
module's own page. So each begins with a capital (the mapping's began "the player did not take the mapping...",
found 2026-11-09). This reads the code: every message given to a _set_status(state, message, ...) as a string
literal starts with a capital letter."""
import os
import re
import unittest

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALL = re.compile(r'_set_status\(\s*"[a-z_]+"\s*,\s*"(.)')


class StatusLinesStartWithACapital(unittest.TestCase):
    def test_every_status_message_starts_with_a_capital(self):
        found, bad = 0, []
        root = os.path.join(HERE, "pvj")
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for m in CALL.finditer(line):
                        found += 1
                        if m.group(1).islower():
                            bad.append("pvj/%s:%d: %s" % (name, n, line.strip()[:100]))
        self.assertGreater(found, 5, "the status messages were found in the code")
        self.assertEqual(bad, [], "status messages that start with a small letter")


if __name__ == "__main__":
    unittest.main()
