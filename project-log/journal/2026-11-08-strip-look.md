<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, the transport strip stands apart from the page (`strip-look`)

- **Why.** The owner, 2026-11-08, testing the prototype on a phone: "it is too hard to distinguish the bottom control banner from the rest of the page."
- **The look.** In Signal, the transport strip at the foot of every screen:
  - has the surface colour (`--cd`) instead of the page colour;
  - has its 3 px line on top in the area's colour (`--ac`) instead of the text colour;
  - casts a shadow up over the screen that scrolls under it.
  - The area tabs under it on a phone keep the page colour.
- **Tint and capitals.** His next word: "make the bottom control banner's background a slight colour tint, matched to the main page the user is on, but i may want just a single colour. also, make all the buttons in the bottom banner all caps, but do not change the clip name".
  - The strip is the surface with the area's colour mixed in at 20 % (`color-mix`; the plain surface where a browser has no `color-mix`).
  - A theme can set `--tk-strip` to give the strip one colour of its own. No theme sets it yet; a design choice for it in pvj/themes.py is offered to the coordinator.
  - The strip's buttons are in capitals; the clip's name and time are not buttons and keep their case.
- **Figma.** The kit's strip component has the same look (all three widths), and the phone and tablet frames scroll under a fixed title bar, strip and tabs.
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone; tests.test_modules_themes passes.
