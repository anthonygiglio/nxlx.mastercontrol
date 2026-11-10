<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, A soft strip shadow on a light page

- **Why.** In Signal light the shadow above the strip (`0 -10px 24px` at 55 % black, made for the dark page, where it hardly shows) was a grey band across the cards that scroll under the strip. It was noted as a possible follow-up when the strip was restyled; the sandbox picture of Now at 1440 showed it plainly.
- **What.**
  - `markLook()` marks the root `data-tone="light"` or `"dark"` from the chosen look's page colour (its `bg` token, already in the list of looks). It needs no stylesheet to load first.
  - Signal on a light page has a short, soft shadow (`0 -4px 12px`, 12 %). Signal on a dark page is as it was.
- **Checked.**
  - The sandbox, Now at 1440 in Signal light, before and after: the band is gone and the strip still stands off the page.
  - tests/ui/panel.test.js checks that the root says dark in Dark stage.
