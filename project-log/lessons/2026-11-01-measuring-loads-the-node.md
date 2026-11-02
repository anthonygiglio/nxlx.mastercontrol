<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## A test that logs in to the node during the moment it measures is part of what it measures (2026-11-01)

The final soak and the one-hour deblocking soak both reported clips stalling in a crossfade, down to 6 frames a second. Both scripts waited for the crossfade to finish by asking the node every 0.4 s, and each ask was a new ssh login, sudo and a fresh python. The stall was theirs: the same crossfades with the node left alone had 0 s under 25 on every source, in 32 of 32. An open issue was written from the first soak, and a build decision quoted it.

What gave it away was two tests of one setting disagreeing. A result that disagrees is a reason to compare the scripts line by line before explaining the renderer.

What we do now: during anything timed, nothing touches the node. Read the trace, which the renderer writes itself, and read counters before and after. A wait is a fixed sleep. When a test does need the panel's view, use one keep-alive connection, as a browser does, and say so in the record.
