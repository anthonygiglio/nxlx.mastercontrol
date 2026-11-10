<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The mapping display redraws when a new look has loaded

- **Why.** The coordinator's note on #244: the display reads the look's colours at each paint, so it shows a new look only when something paints it again. Looking closer: choosing a look swaps /theme.css and redraws the page at once, before the new stylesheet has loaded, so whatever the script draws in that redraw takes the old colours, and nothing drew it again once the new ones arrived.
- **What.** The Look page, when it swaps the stylesheet, says so when it has loaded (a `pvj-look` event on the window). One listener for the page draws the mapping display on the page, if any, again; each display leaves its own paint on its canvas.
- **Checked.** tests/ui/panel.test.js sets a new `--bg`, sends `pvj-look`, and reads the display's background in the new colour.
