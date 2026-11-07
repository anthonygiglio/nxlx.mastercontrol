<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, no symbol icons, and the title bar the same on every screen (`panel-tidy`)

- **Why.** The owner: "Remove all emoji icons. Make sure all title and menus align from page to page."
- **Symbols.** Prev and Next carried the skip symbols (they showed as empty boxes where the font has none). They are plain words now. So are the arrow buttons: Up and Down on masks and surfaces, and Left, Up, Down and Right on the mapping nudge pad. An arrow between two words is "to"; the shader keys hint names the arrow keys; the pending dot beside a control is "...". The Figma kit and its screens had the same symbols and were changed the same way.
- **Measured first.** A probe opened every screen in the sandbox (owner, playing) at 390, 820 and 1440 and recorded where the title bar, its line, the tabs, the rail and the first heading sit. Three things moved from screen to screen:
  - a page with its own heading (every page of Setup, Mapping) had no line under the area's name, so its bar was lower (79 px on a phone, against 107);
  - a long line wrapped to two (Library, Effect, Sound: 128);
  - from 1200 px the line started where each area's name ended.
- **What.** Such a page now has its name on the line (a page of Setup whose module is off takes it from its row). On a phone the line keeps room for two lines and is cut after the second, with the whole text as its title. From 1200 px the area's name is at least 100 px wide.
- **After.** The same probe: on all 34 screens the bar is 128 px on a phone, 70 at 820 and 46 at 1440. The line, the tabs and the rail start at the same place on every screen.
