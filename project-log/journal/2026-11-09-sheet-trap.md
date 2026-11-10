<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Tab stays in the node sheet; a swipe from Blackout presses nothing

- **Why.** The coordinator's review of #256 asked for two follow-ups. First, the sheet is a modal dialog, but Tab could leave it. Second, a test should confirm that a swipe up that starts on Blackout or Stop never presses them.
- **What.**
  - Tab and Shift+Tab go round the sheet's own controls. Before: at 390 and 1440, Tab from Log out went on to the page behind, and Shift+Tab from Close went to the strip or the tabs. After: Close, then the sheet's controls in order, then Close again; Shift+Tab from Close goes to Log out.
  - The swipe needed no change. Real touch events (CDP) at 390 and 820: a swipe up from Blackout and from Stop opens the sheet, sends no POST /api/control and fires no click on the strip or the backdrop. Chrome sends no click after a touch that moved this far.
- **Checked.** tests/ui/panel.test.js does both: it presses Tab through the sheet and checks each step stays in it, checks Shift+Tab from Close goes round to the last control, and swipes up from Blackout and from Stop with CDP touch events, checking the sheet opens and nothing is sent to /api/control.
