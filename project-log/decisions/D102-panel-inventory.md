<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D102. The panel has a written inventory, `pvj/PANEL.md`, kept with the code; the design frames are 390, 820 and 1440 wide (2026-10-20)

**The owner's words** (the panel design brief, 2026-10-20): a design sandbox, a Figma file that matches the live panel, and ranked proposals, starting from "a complete inventory of the panel in plain words".

**What it is.** One file beside the other notes of the panel (`pvj/MAPPER.md`, `pvj/THEMES.md`, `pvj/ROOM.md`), not in `pvj/web`: the server serves every file at the top of that folder to anyone who asks, and the installer copies it to the node. It holds every screen, card and control, who sees it, the route it calls and the states it can be in; the shared patterns; the newest parts in full (D99, D100, the Mapping card); what D101 adds, marked; the design tokens with their Figma names; accessibility facts; the routes by role. It describes and does not judge. A change to a screen changes its section in the same pull request. The machine check of what each role can reach stays `tests/ui/inventory.js`; this file is for people (and for the Figma file, whose variables take their names from its token table).

**The frame widths**, chosen from the panel's container breakpoints so that no frame sits on a line: 390 (under 600: a phone), 820 (600 to 1199: the rail and tabs, the workspace under 900), 1440 (1200 to 1699: the area desks, and the owner's M1 Air at its default scale, where the strip has every control on one line). 1728 is an optional fourth, the only width where Play shows Pads, Library and Shaders together (from 1700). The brief suggested 1280 for the laptop; 1280 is inside the same range but still folds Fade and the 10 second buttons into More, so it is a check, not the frame.

**Cost.** One more file to keep true by hand; nothing checks it against the code, so it can fall behind the way `tools/panel-playground` did (D34). Written from the code, not from a browser walk: the browser test could not run on the laptop when it was written (Chromium's NSS libraries were missing in WSL).
