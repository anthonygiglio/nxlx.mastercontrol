<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D113 (2026-10-31): measured on the test node

mpv 0.40 keeps the last picture while its pipe stands still, so the design holds. Then the screen is black for 0.10 to 0.20 s before the new picture; the cause is not found. The longest time with no new frame fell from 0.83 s to 0.25 s, but the node's sound setting was on for the 0.83 s, so the like-for-like figure is about 0.8 s to 0.45 s with sound on and silent sources. A press of a source with sound and no picture holds the old picture for the 6 s wait, then black for about 0.6 s, and the panel brings the old source back. Not run: real programme sound with someone listening, Wi-Fi, a sender on another machine.
