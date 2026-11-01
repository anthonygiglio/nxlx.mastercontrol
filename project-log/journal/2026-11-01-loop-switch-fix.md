<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, The loop lists on the node, and the fault in their switch

- The device rows of D118 ran on the node on master 4ab1168. Joins went from 117 to 179 ms to about 34 ms under 4 sources, and an hour held. Not run: USB (LL7, the pull in LL8, LL8b) and the ear half of LL4.
- The first use of `renderer.loop_lists` false put the player in a restart loop, no picture, until the key left `renderer.json`. `_wish_changed` compared a file that carries the key with a run that never has it. It now drops the key, as it is the panel's alone. Not rerun on the node yet.
- Recorded in `tools/DEVICE-TESTING.md`: those rows, the Link rows (one machine only, a stand-in peer) and the BC2 rerun with the sound-on cost.
