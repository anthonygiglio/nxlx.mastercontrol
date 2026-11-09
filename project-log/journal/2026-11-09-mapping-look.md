<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The mapping display follows the look

- **Why.** PROPOSALS 6: the projection mapping display on Shape > Mapping drew in colours written into the script (yellow, cyan and pink on near black), so the light looks and Signal still showed a black box with neon lines.
- **What.** The display reads the look's colours from the CSS at each paint: the page colour behind, the text colour for a surface that is on, the quiet colour (and a dashed line) for one that is off, and the area's colour for the chosen surface, drawn as a band under a thin line of `--ink` (the colour that reads as a thin line; Signal light's accent alone is too faint on its page). Its corners are the area's colour ringed in `--ink`; the corner being moved is the text colour, larger. The labels use the look's font.
- **Checked** in the sandbox (mapping-surfaces, 820 wide) in Dark stage, Light, Signal and Signal light, before and after. tests/ui/panel.test.js checks that the display's background is the look's `--bg`.
- **Also.** The node switcher check that a badly formed remembered entry is ignored now waits for Look again to redraw the list (it read the old list once in this branch's run; the same fix is in nodes-contrast).
