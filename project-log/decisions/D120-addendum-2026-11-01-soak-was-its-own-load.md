<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D120 (2026-11-01): the stutter in the soaks was the test's own load

D120 opened with the final soak's figures of 6 to 16 frames a second for up to 4 s. Those figures, and row DB2 of the one-hour soak, came from a script that asked the node for its state every 0.4 s through each crossfade, each time over a new ssh login with sudo and a fresh python. The hour with deblocking off still showed CRT and Oscilloscope under 25 for up to 3 s, which disagreed with the short test behind D120, so the two scripts were compared and the difference tested on the node, master 7d6b151, deblocking off, mapping on, 4 sources of 720p clips, 14 s from play to play, 8 crossfades a block.

| Blocks | CRT seconds under 25 | Oscilloscope | Lumia 2 | Lowest second | Incoming clip to reach 25 |
|---|---|---|---|---|---|
| Nothing asked of the node, 4 blocks over 2 runs | 0 in all 32 | 0 | 0 | 29 | 0 s |
| A panel's polling, 1 client, 2 blocks | 0 in all 16 | 0 | 0 | 29 | 0 s |
| A panel's polling, 2 clients, 2 blocks | 0 in all 16 | 0 | 0 | 28 | 0 s |
| The soak's 0.4 s probe, 2 blocks | 1 to 2 in all 16 | 0 to 3 | 0 to 1 | 6 | 0 to 3 s |

So D120 stands, and it does more than the soak said: with nothing else on the node, or a panel open, a crossfade over 4 sources holds every source at 25 or more. The baseline with the filter on keeps its figure from the short test, which did not probe during the crossfades: 2.67 s under 25 on CRT, 1.83 on Oscilloscope. The final soak's 3.8 and 3.0 are not figures for the renderer alone.

**Not settled:** the panel clients imitated the Play > Pads page from its code (one keep-alive connection, `GET /api/status` each second, `/api/shaders` each 20 s), not a real browser, a phone on Wi-Fi, or the other screens. Which part of the probe costs the frames was not measured. It does show the margin: a little extra work on the node at that moment took clips down to 6 frames a second, while the output stayed at about 60 flips. Missed vblanks did not follow any of this, from 3 to 47 a block, unexplained, and one pass of each. DB4, the look, is still the owner's.
