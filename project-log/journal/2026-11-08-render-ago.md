<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, the Render nodes page says its times in its own words again (`render-ago`)

- **Why.** Found in the review of #228. app.js declared `agoWords` twice: once for the Render nodes page ("125 s" is "2 min ago") and once, later, for the controllers' card ("2 minutes ago", "just now"). In one script the later declaration wins, so the Render nodes page said "sent just now" and "2 minutes ago", and "sent just now" where no cue time was known.
- **What.** The Render nodes page's helper is `renderAgo`. Where a node has no time for its last cue, the line names the cue and says nothing about when.
- **Test.** panel.test.js, with the node's answer for the render nodes changed on the way: a cue sent 125 s ago reads "sent 2 min ago", and a picture gone 30 s ago reads "picture 30 s ago".
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone. Break-check: with master's app.js the new case fails on "a cue sent 125 s ago, in the page's words".
