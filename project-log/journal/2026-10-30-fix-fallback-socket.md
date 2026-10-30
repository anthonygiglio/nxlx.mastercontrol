<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-30, the overnight soak, and the panel kept reaching classic mpv after a fallback

- **The soak:** 10 hours on New on the test node, 4 sources at 720p in software, the mapping on, 1920 x 1080 at 60 Hz, a crossfade every 30 s (1199, none refused, every one ended by itself), watched at the real HDMI by the capture dongle. No freeze or black outside the set-up and the fallback check, no capture gap, no restart of the player, 58 to 61 C.
- **Rate:** 40.7 flips a second and 19 missed vblanks a second all night, pvj-render at about 318% of one core, the box at 82%. Steady, not smooth: 58.8 flips a second on the same load at 1024 x 768 (SK1).
- **Longest gaps between new pictures:** 83 ms is typical, 228 ms at worst away from a crossfade, 317 ms at worst in one.
- **Memory:** RSS 354 MB at the start, 840 MB after an hour, then about 19 MB an hour to 1028 MB at the end. fds, threads and GL objects stayed level. Memory available fell from 3.1 to 2.4 GB. A slow growth to look at; not a danger on this night.
- **A false alarm:** the soak's `fades_done` of 10 is a count for each 5 minutes, not a total: 10 crossfades in 5 minutes is exactly one every 30 s.
- **The bug:** the check at the end killed pvj-render twice. Classic took over and said why, as it should, but the panel was shut out of it: `ipc error: [Errno 13] Permission denied` on every call, and New chosen again answered 200 and did nothing. On the node `player.sock` was mode 600.
- **Why:** `Player.serve` removes the old socket at its start, before pvj-render runs. A killed pvj-render leaves its own there, and the opener, which waits for a socket at that name, opened that old file at once. mpv then made a new one, owner-only.
- **The fix:** the fallback removes the old socket before the opener starts. A test in `tests/test_renderer.py`, and row SK3. To be run again on the node once deployed.
- **Put back:** `pvj-player` restarted by hand on the node, New drawing again.
