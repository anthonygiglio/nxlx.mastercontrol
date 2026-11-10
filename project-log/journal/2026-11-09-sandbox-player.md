<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The sandbox starts the player in every scenario, and its masks combine

- **Why.** PROPOSALS 10: the design sandbox runs the box in dev mode, which starts mpv only on the first play. In the idle scenarios nothing played, so the pill said NO PLAYER and Health showed a problem that a real node, whose player runs from the start, would not have. Pictures of the panel taken from the sandbox showed it too.
- **What.** tools/sandbox/box.py starts the headless mpv as soon as the box is built, where this machine has mpv. If it does not start, the sandbox says so on stderr and the scenario goes on without it, as before. Nothing of the node's own code changes.
- **Masks (PROPOSALS 11).** A headless mpv draws on no screen and knows no size, so in the masks scenario the masks never got as far as "Combining". The sandbox's player now says it draws at the mode the fake screen report says is used (1920 x 1080), as a node's would.
- **Checked.** The idle scenario's /api/status says the player is running before anything is played. The masks scenario says combining, then on with 2 masks after its 30 s wait. tests/test_sandbox.py passes when no other browser test holds the Art-Net port (6454; the panel test's harness listens on it).
