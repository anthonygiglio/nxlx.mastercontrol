<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, small follow-ups after the title bar and steady taps (`panel-followups`)

- **Why.** The coordinator's review of #206 and #207 left three small things: a retry in #206's test, the timed cards finding themselves by id, and a click on More that was lost once just after a window resize.
- **Timed cards know their own element.** Health, Network, Remote support, Screen output and Nodes in step checked `document.getElementById('...card')` before they drew. That is true for a newer card with the same id too. So a late answer to a card that was drawn again meanwhile went on as if it were current. Held behind a press, its look again replaced the new card's. On the release the old card took the shared timer, and the new card never refreshed. Each now checks its own `card.isConnected`.
- **Test.** On Health, the first card's answer is held back 4 s; the page is drawn again; a press is held while both answers land, the old one last; on the release the new card must show the node's latest answer.
- **The resize case.** A probe in the sandbox (owner, playing, OSC page) went from 320 x 844 to 820 x 1180, scrolled and clicked More once, twelve times: it opened each time, on master with #207. The lost click is not seen any more, most likely the redraw that #207 now holds back. The retry in #206's test is gone: one click, and More must open.
- **Run on the HP's WSL:** `tests/ui/panel.test.js` passes, run alone. Break-check: with master's app.js the new case fails on "the new Health card stopped refreshing".
