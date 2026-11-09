<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, A scroll over a box on the page stays in it: the Nodes list, the pad dialog, the open strip

- **Why.** The owner, 2026-11-09, on node-01: "the node picker looks good. check what happens when a user scrolls in that section. i notice the page underneath moving", then "check the strip and other popups for the same scroll issue".
- **Measured** in the sandbox, with the wheel and with a finger (touch events through the browser's own input, at 390, 820 and 1440 px), how far the page behind moved:
  - **Nodes list:** 521 px with the wheel at 1440, 876 px with a finger on a phone. A long list scrolled to its end and the page went on from there; a short one gave the page the whole scroll.
  - **Pad dialog (Play > Edit pads > a pad):** 756 px with the wheel and 516 px with a finger over the sheet, and 756 px over the dark around it.
  - **The strip with More open, on a phone:** 607 px and 513 px.
  - **Left as they are:** the closed strip, the title bar and the rail scroll the page, as any bar fixed over a page does. Setup's index column scrolls itself and nothing else.
- **What.** `keepScroll()` in app.js holds a wheel or a finger that would carry past either end of the box. `overscroll-behavior: contain` does that only for a box that can scroll; a short one hands the scroll to the page. It is used in three places:
  - the Nodes list;
  - the pad dialog: its sheet is the box, and over the dark around it nothing scrolls at all;
  - the strip, only while More is open.

  Inside each box nothing else changes: it scrolls as before. A slider keeps its own drag, and so does a text box with more in it than it shows; a pinch, or Ctrl with the wheel, is left alone. The Nodes list also has `overscroll-behavior: contain`.
- **Checked.**
  - **Sandbox, after:** the page stays where it was in every case above, with the wheel and with a finger, and a long Nodes list scrolls to its end.
  - **Strip slider:** a finger dragged along the strip's slider with More open still moves it.
  - **tests/ui/panel.test.js:** turns the wheel over the open Nodes list (the list reaches its end, the page does not move), over the pad dialog's sheet and the dark around it, and over the open strip at 820 px; in each, the page stays where it is.
