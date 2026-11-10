<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Nodes opens the This node sheet; no swipe

- **Why.** The owner, on his iPhone after #256 reached node-01: "When I click on the node selector at the top, the dropdown makes the top banner scroll with the page, it shouldn't do that. Also, I do not like the swipe up panel. Make the new swipe up panel what appears when a user clicks on the node selector at the top." And: "The panel that opens when swiping up on the firetab is something different."
- **Cause of the scrolling title bar.** The Nodes list opened inside the title bar. On a phone, a title bar taller than a quarter of the window is let go and scrolls with the screen (dockHeight), so opening the list made the whole bar scroll away.
- **What.**
  - Nodes in the title bar opens the sheet (`nodesToggle()` opens or closes it). The sheet is now called Nodes, and the list (`nodesPanel()`) is its first part, under "Nodes on this network", with Look again and the switch for looking. Its own heading, Close, Escape and scroll holding are the sheet's. Then come This node (what was Status), This device and Tools. For a role without the list, the sheet is still called This node.
  - The pill opens the same sheet.
  - The sheet comes down from the top, under the notch (safe-area padding).
  - Nodes has `aria-haspopup="dialog"`, and `aria-expanded` follows the sheet.
  - The swipe up on the strip and the swipe down on the sheet's bar are gone. The launcher's padding at the foot stays.
  - Setup's "Show the nodes" opens the sheet.
  - The open sheet is kept across a full redraw of the page, with its scroll and cursor. Built again, it went back to its top and asked for the nodes again. The first test run caught this: a redraw came in the middle of the wheel test, and the page moved instead of the sheet. Log out forgets the open sheet.
  - From #258 (sheet-trap, folded in here): Tab stays in the sheet.
- **Checked.**
  - tests/ui/panel.test.js: Nodes opens the sheet with the list first, and the title bar keeps its height with the list out of it. aria-expanded follows the sheet. A wheel over the list scrolls the sheet to its end, not the page. Close gives the cursor back to Nodes. The pill opens it with the four sections in that order. Tab stays in it. A finger swipe up from Blackout or Stop opens nothing and presses neither.
