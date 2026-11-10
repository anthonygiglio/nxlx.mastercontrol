<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The transition picker shows the choice that was made

- **Why.** panel-ui failed on the msg-sentences branch at panel.test.js 413: "Wipe from left" was chosen, and the node's status said wipe-from-left, but the picker never marked it as chosen. That branch does not touch the picker; the cause is on master.
- **Cause.** A choice in the transition card sent POST /api/mix, then asked for the status and drew the card again after a fixed 150 ms. When the status took longer, the card was drawn from the old status, and the old transition stayed marked as chosen. Nothing drew it again until the page was opened again: the poll only redraws the card when the box's fallback words change. In the sandbox with the status held 600 ms, "Slide off up" was pressed and "Wipe from top" was still marked 4 s later.
- **What.** `poll()` gives back the promise of its answer (a resolved one when it does not ask). The transition card is drawn again when that answer is in, whether it came or failed.
- **Checked.**
  - The same sandbox probe after: the new choice is marked once the status is in (700 ms with the 600 ms hold), and stays.
  - tests/ui/panel.test.js holds /api/status 600 ms, chooses Wipe from right, and checks that it is marked as chosen.
