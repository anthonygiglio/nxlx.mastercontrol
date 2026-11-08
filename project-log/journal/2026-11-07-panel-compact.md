<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, a compact title bar on a phone, and the node's name in it (`panel-compact`)

- **Why.** The owner, testing on a phone: "Banner is too tall." and "X86 should actually show the node's name (ie. node 01)".
- **The bar.** In Signal on a phone:
  - the area's name is 28 to 34 px (it was 34 to 44) and the padding is smaller;
  - the line under it is one line, cut with dots where it is longer, with its whole text as its title (#213 had kept room for two lines);
  - in the sandbox at 390 x 844 the bar is 83 px high, where it was 128;
  - it is still the same on every screen.
- **The name.** `GET /api/status` has `system.name`: the host name's first label, in lower case, without the domain ("nxlx-node-01"). The pill shows it without the leading "nxlx-" that every node's name has ("node-01 · OK", in capitals by the look), and the board kind only where a node has no name. Test: tests/test_status_name.py.
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone; tests.test_status_name, tests.test_lockrank and tests.test_modules_themes pass.
