<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, On the screens says which surfaces each source is on (`now-surfaces`)

- **Why.** Follow-up (c) after Now (#214), from the owner's direction: "each source, what it plays and on which surfaces".
- **What.** While the mapping is on, each source's line on Now ends with the surfaces that show it ("tunnel.mkv · on Left, Centre"), or "on no surface". A surface shows Source 1 unless it names another (D101), and a hidden surface is left out. Without the mapping, or with no surfaces, nothing is added: the source is the whole screen, as before. Now reads the mapping every 5 s, with Health, while the Projection mapping module is on.
- **Test.** panel.test.js, once the mapping is on, with the sources' answer routed to three: the source the box says the Quad is on ends with "on Quad" (on the harness's classic player that is Source 1), and the others end with "on no surface".
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone.
