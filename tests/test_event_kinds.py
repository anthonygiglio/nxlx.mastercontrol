# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Every kind of event the node keeps (pvj/events.py KINDS) has a label in the panel's What happened card (EVENT_KINDS in app.js)."""
import os
import re
import unittest

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import events

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pvj", "web", "app.js")


class EventKindLabels(unittest.TestCase):
    def test_every_kind_has_a_label(self):
        with open(APP, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"var EVENT_KINDS = \{([^}]*)\};", src)
        self.assertIsNotNone(m, "EVENT_KINDS not found in app.js")
        labels = dict(re.findall(r"(\w+): '([^']+)'", m.group(1)))
        self.assertEqual(sorted(labels), sorted(events.KINDS))
        self.assertEqual(labels["handover"], "Other nodes")


if __name__ == "__main__":
    unittest.main()
