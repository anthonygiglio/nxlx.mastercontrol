<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The node sheet: swipe up for this node, this device, the nodes and the tools

- **Why.** The owner, 2026-11-09, on the FireTab launcher (NXLX Panel 2.0, build 2): "being able to swipe up from the bottom of the screen and seeing info like status, device details, nodes, tools, etc.", then "yes build the sheet". The launcher's drawer (status, nodes, tools, brightness, version) was read for reference on the tablet, without changing anything.
- **What.** A sheet from the foot of the screen, over the page, in the page colours (Signal: the area's bar on top, as the title bar has).
  - **Status:** this node's name, its address, temperature and whether the player runs; what is on the screen; Health with Open (to Now for the Owner and an Operator).
  - **This device:** its name and role (and remote support), and whether the connection is secure.
  - **Nodes:** "Nodes on this network" opens the list in the title bar (the Owner and an Operator, not over remote support).
  - **Tools:** Reload the panel; Full screen and Keep the screen on where the browser can; Look (the Owner); Log out (asked first).
  - **The version line**, from GET /api/system, asked when the sheet opens.
- **How it opens.**
  - A swipe up on the strip: 48 px, more up than sideways, not on one of its sliders. A press that does not move is still the button's.
  - A press on the pill in the title bar, at any width. The pill is a button now: Enter and Space open the sheet too.
- **How it closes.** Close, Escape, a press on the dark around it, or a swipe down on its bar. The cursor goes to Close when it opens and back to the pill when it closes.
- **Scrolling.** A scroll over the sheet stays in it (keepScroll, from #248).
- **In the launcher.** The launcher adds " NXLXLauncher/<version>" to its user agent (agreed with the coordinator); the root then has `data-launcher`.
  - The swipe on the strip is off, so a swipe never opens two drawers.
  - The strip keeps 28 px clear at its foot, where the launcher's swipe strip lies over the page. Its buttons ran into that band and their lowest pixels never reached the page.
  - The pill still opens the sheet.
- **Checked.**
  - **Sandbox:** phone (Owner), tablet (Operator), laptop, a launcher user agent at 1024 x 600, and a Guest phone, by finger (CDP touch) and by mouse:
    - the swipe opens it except in the launcher, and the pill opens it everywhere;
    - Escape closes it and the cursor comes back;
    - the Operator has no Look and the Guest no Nodes;
    - the launcher's strip has its 28 px;
    - no console messages.
  - **tests/ui/panel.test.js:** the pill is a button; the sheet opens with the cursor on Close, with its four parts and the version; Escape and a press on the dark close it, the cursor back on the pill; Nodes on this network opens the list.
- **Looks like the launcher.** After the owner showed photos of the launcher drawer he likes (2026-11-09: "This is what I current see and kinda like"), the sheet in Signal has Setup's blue bar in every area, as the drawer has, and is as wide as a tablet (up to 1100 px), so the two read as one kind of sheet.
