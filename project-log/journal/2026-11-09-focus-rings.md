<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The keyboard's ring can be seen everywhere

- **Why.** A focus audit (scratchpad tool): Tab through Now, Pads, Picture, Scenes and Projectors as the Owner at 1440 in all six looks, and measure the focused control's outline against what is around it. Two findings:
  - **Signal:** the ring on Nodes, the one control in the title bar, was `--ink`, which on the bar is the area's own colour: 1.0:1 against the bar, so a keyboard user could not see they were on it.
  - **Outside Signal:** dropdowns, folds (Let someone in, Not built yet) and text boxes had no ring of the panel's own. They had the browser's thin one, while every button and field had a 3 px ring in the look's accent.
- **What.**
  - In Signal, a ring on the title bar is the bar's ink (`--on`).
  - Outside Signal, the panel's ring rule takes select, summary and textarea as well.
- **Checked.**
  - The audit again: no control without a ring of its own, and none under 3:1 against what is around it, in all six looks.
  - tests/ui/panel.test.js switches to Signal, tabs to Nodes and checks that its ring is solid and not the bar's colour.
