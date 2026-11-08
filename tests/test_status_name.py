# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The status names the node by its host name, which the panel's title bar shows (2026-11-07): the first label only,
in lower case, without the domain."""
import unittest
from unittest import mock

import tests  # noqa: F401  (every test module imports the package: tests/test_lockrank.py)
from tests.test_boxcare import Base


class StatusNameTest(Base):
    def name(self, host):
        with mock.patch("pvj.api.socket.gethostname", return_value=host):
            return self.api.status({}, None, "t")["system"]["name"]

    def test_the_status_names_the_node_by_its_host_name(self):
        self.assertEqual(self.name("nxlx-node-01"), "nxlx-node-01")

    def test_the_first_label_only_in_lower_case(self):
        self.assertEqual(self.name("NXLX-Node-02.local"), "nxlx-node-02")
        self.assertEqual(self.name("nxlx-node-01.lan.example"), "nxlx-node-01")


if __name__ == "__main__":
    unittest.main()
