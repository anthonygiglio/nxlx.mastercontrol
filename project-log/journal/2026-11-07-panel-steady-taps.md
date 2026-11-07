<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, a tap is not lost to the page's own redraw (`panel-steady-taps`)

- **Why.** A tap is a press and a release on one control. When a timer redraws the card between the two, a new control is under the finger and the tap does nothing: in a show, a button that does not answer. The coordinator saw it as a test flake twice (Try again on the Projectors page, #205; More in #206's test) and asked for the product fix before Now.
- **Measured first, and what the measure missed.** In the sandbox (scenario playing, 820 px) each screen was left alone for 12 s and every control it removed was counted: Projectors removed its whole list every look and Health "Show the address" every 5 s. An idle sandbox does not show the pages that redraw only in a state (a screen mode waiting to be kept, a support session, nodes in step, a network change going back, an update installing); the review named those, and they are covered too.
- **What.** One press tracker for the page:
  - it counts each pointer down with its main button (two fingers are two), and Enter or Space held on a control (let go by the same key);
  - it lets go of everything when a menu opens, when the window itself loses the focus, or when the page is hidden.
- **The two helpers.** `held(key, again)` is called by each timed look just before it draws: with a press down it draws nothing, and the look runs again, with fresh data, just after the release. `whenFree` does the same for the status poll's full redraws.
- **The looks that use it:** Projectors (5 s), Health (5 s; its next look is now set by the look itself, so it never stops), Screen output (1 s), Support (5 s), Nodes in step (2 s), Network (1 to 2 s), Updates (3 s). The older `drawLate` (a late part of the page) uses the same tracker; its own flag let go on any field's blur.
- **Test.** panel.test.js puts the cursor on a projector's power button, then holds a press on its More for 6.5 s while every answer of `/api/projectors` differs (routed), and lets go once with no second try: the control is the same element and More opens.
- **Break-checks.**
  - With the first version of this PR (a blur of any field ended the press), the test fails on "the control under the finger is not replaced while it is pressed".
  - With the redraws not waiting at all, it fails the same way.
- **Run on the HP's WSL:** `tests/ui/panel.test.js` passes, run alone.
- **Next:** take the retry out of #206's test once both are in, and look at what swallowed More's click there, just after a window resize.
