<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D119. No lock to 30, plain clips stay at 60 (2026-11-01)

The lock was planned on 2026-10-30, when the 4-source load gave 40.7 flips a second and 23.9 off runs a second per source, and locking to every second vblank gave 0.2. Since then the display scaler (D108) and the render thread's priority brought the same load to a steady 60. The hour of LL10 on the node, on master 4ab1168: 59.99 flips a second, 0.009 missed a second, and 1184 to 2146 gaps of 50 ms or more per source in the hour with lists on, which is 0.3 to 0.6 a second. That counts a little less than an off run does, as a frame held one vblank is not in it, so read it as about 1 a second at worst. That hour had mapping off; the numbers of 2026-10-30 had it on, and with the scaler at 60 and mapping on they gave 3.0 off runs a second, against 0.2 locked. The final soak runs with mapping on and is the check on this.

What is left to win is 1 to 3 a second per source, against a switch that would have to decide when a load is plain, change the pace at every crossfade, shader or effect, and hold anything moving at 60 down to 30. That is new timing code in the part that now runs smoothest, near the end of the work. So it is not built. Rows S4 and the 29.97 stutter pairs stay open as their own bug.

Revisit if a screen or the dongle shows judder on plain clips that this reading misses: the trace has undercounted before (35 off runs seen by the dongle in 60 s against 15 in the trace). The lock as an overload fallback is the same work and waits with it.
