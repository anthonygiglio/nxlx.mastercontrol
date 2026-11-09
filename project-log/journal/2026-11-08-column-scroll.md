<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, Columns scroll on their own

- **Why.** The owner, 2026-11-08: "the scrolling on a page with multiple columns is klunky, the columns all scroll together except for the really short ones. it's not a good UX". The page scrolled every column at once, while a few parts (the shader library, the side of a split page, the Setup index) were pinned below the title bar, so some things moved and some did not.
- **What.** From 1200 px wide and 600 px tall, each column of a desk (Play, Shape, the walls of Room), the page beside the Setup index, and each of the four columns of Now is a scroll of its own between the title bar (with its tabs) and the strip: sticky, as tall as that space, `overscroll-behavior: contain`. The page itself hardly scrolls, so a short column stays where it is and a long one scrolls under the pointer. On Now, Needs you scrolls away first, then the four columns stay.
  - The name of each desk column stays at the top of its column while it scrolls, on a band of the page colour.
  - What was pinned below the title bar inside a column (the shader library, the side of a split page, the mapping tools) is pinned to the top of its column instead.
  - Room keeps Scenes and Guests in the page flow (they stack in one column of its grid); Walls scrolls by itself.
  - A window under 600 px tall keeps the one page scroll, and phones and tablets below 1200 px are unchanged.
- **Checked** in the sandbox at 1440 x 900 as the Owner: Play, Shape and a Setup page no longer scroll as a page; the Effect column scrolls with its name kept while Picture and Sound stay; Now scrolls Needs you away, then its columns stay.
