<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, What happened: the node's event log on Health and on Now (`now-events`)

- **Why.** The owner approved a simple log for diagnosing issues (D125, #215). The panel shows it where he looks.
- **Health** (the Owner's): a "What happened" card. It lists the node's events newest first:
  - each one's kind (Node, Player, Renderer, Update, Projector, Network, Sign-in, Scene), when (the time today, the day and time before), the node's own sentence, how many times, and the device or address it was about (never over a support login, which the node leaves out);
  - 20 at first, Show more for 50 more, Refresh;
  - the node's note on whether the log survives a restart;
  - it looks again every 30 s, never under a finger, and stops when the page is left.
- **Now**, under Today (the Owner's): the last five events, read with the rows of Setup every 30 s, and "The whole log", which opens Health.
- **Test.** panel.test.js on Now: the last events, or "Nothing yet" on a fresh node, with an event's kind and time when there is one; The whole log opens Health with its list and its note.
- **Review of the PR.**
  - The times are in the device's own time (the browser's clock and zone), not the node's.
  - One timer, however often Refresh is pressed. A hidden tab asks nothing and looks again in 30 s.
  - Now keeps the last events when a look fails.
  - On Health a failed look leaves nothing of the older answer: no rows, no Show more, no note.
  - The test also routes 25 events: 20 are shown at first, Show more shows the rest and then goes, and a repeated event says "(3 times)".
