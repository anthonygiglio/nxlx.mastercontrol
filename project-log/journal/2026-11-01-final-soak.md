<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, The final soak, and what falls during a crossfade

- 2 hours on the node on master 617d8ae, mapping on, 4 sources on lists, 22 crossfades: 59.98 flips, 0.025 missed a second, same pid, 0 restarts, all transitions to the end, throttled 0x0. Recorded in `tools/DEVICE-TESTING.md`. Nobody watched the screen.
- D119 stands: gaps of 50 ms or more are 0.17 to 0.30 a second per source, uneven frames 1.06 to 1.54, the low end of what a lock would win.
- New detail on a known limit: during a crossfade the other three sources fall too, not only the incoming clip. Same cause as in D111. Open as its own bug.
- RSS climbed 80 MB at transitions in the first 70 minutes, then held at 411 to 432 MB.
- The switch fix of #195 is proven on the node, so the note in the device tests that said otherwise is corrected.

