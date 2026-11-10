<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, A question in a list is never wiped by its redraw

- **Why.** The coordinator: panel-ui failed three times today (#241, #246, #249) at panel.test.js 2406, the click on Not now under "Switch Main wall off?" on Now, with "element is not stable", then "element was detached". Reproduced in the sandbox: with the question open, after Now's next look at the node (every 5 s) the question and its Not now were gone. Now's walls list is emptied and built again on every look. A person would see the same: Not now slips away under the finger, or the question disappears before it is answered.
- **Cause.** The redraws that keep the cursor (`keepCursor()`, 13 lists, Now's among them) emptied the list whatever was in it. Needs you was guarded by `asking()`, the walls, sources, scenes, node and Today lists were not.
- **What.** `keepCursor()` leaves a list as it is while a question (`#confirmrow`) is open in it. The next redraw after the question is answered, or goes by itself after 8 s, brings it up to date.
- **Checked.**
  - Sandbox, before: the same Not now was gone after one refresh of Now, and so was the question. After: the same button, the question still open.
  - tests/ui/panel.test.js keeps the question open through Now's next GET /api/room and checks that #confirmno is the same element, before pressing it.
