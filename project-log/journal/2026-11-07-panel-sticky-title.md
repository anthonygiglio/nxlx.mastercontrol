<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, the title bar stays at the top while a screen scrolls (`panel-sticky-title`, proposal 2 of pvj/PROPOSALS.md)

- **Why.** The owner, 2026-11-06, trying the Figma prototype: after scrolling down to the tabs and opening another screen, the title bar was out of view. In the panel it scrolled away with every long screen.
- **What.** The title bar (`#wshead`) sticks to the top of the window. What already stuck to the top now starts under it: the message line, the rail, the screen tabs, the Setup index, the mapping tools, the Library column, a split's side. The page measures the title bar's height into `--head` as it measures the strip's into `--dock` (the same ResizeObserver).
- **Where it does not.** The sweep's rule (`tests/ui/sweep.js`: the bars that stay put leave at least half of the window) failed in 16 cases with the title bar pinned everywhere: at 320 x 568 a Signal title bar, the strip and the tabs took 304 to 375 px, and with the strip opened with More even 390 x 844 went over half. So the title bar scrolls away on a phone under 800 px high, at any size while the strip is open, and under 480 px high as before. Tablets and laptops always keep it.
- **Run on the HP's WSL:** `tests/ui/panel.test.js` with its width sweep passes; sweep.js and signal-pages.js pass. In the sandbox, scrolled down: the title at 0 and the tabs and rail under it at 390 x 844, 820 x 1180 and 1440 x 900; at 844 x 390 the title gone and the tabs at the top.
- **A lesson of the night:** two browser tests at once on this laptop failed each other on timing, and a sweep of leftover test players stopped the other run's. One at a time.
