<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D110 (2026-10-31): the numbers on the deployed build

D110 gave the figures with the key set by a runtime drop-in. Here are the same switches on the deployed build (master 0581c5b, no drop-in), the same morning, through the real panel and the HDMI dongle (`measurements/2026-10-31-ndi-switch`, `results/deployed-*`). 21 switches, 16 s apart, between the same A (1920x1080@60) and B (1280x720@30).

| | Failed | Gap median | Gap range | Answer median |
|---|---|---|---|---|
| Deployed, to A (10) | 0 | 1.00 s | 0.82 to 1.10 | 0.92 s |
| Deployed, to B (11) | 0 | 0.80 s | 0.68 to 0.92 | 0.80 s |
| Deployed, all (21) | 0 | 0.83 s | 0.68 to 1.10 | 0.83 s |

- **21 of 21 worked**, the gap with no new frame at the dongle a median 0.83 s, from 0.68 to 1.10. Before the change the same switches had 2 failures in 21 and a median of about 2.9 s (D110).
- **TCP is confirmed.** `ss -uanp | grep -c :5960` was 0 while a source played, and the one connection was TCP to the sender's port. The file says `rudp.recv.enable` false, was written at 07:03:24, and the library was loaded at 07:03:34 from `NDI_CONFIG_DIR=/run/pvj-ndi-config`, so the library read this file. No restart for a changed file was needed on this deploy, since nothing of NDI was playing.
- **Sound blocks were counted, not heard.** NDI Tools' Test Patterns on the HP, opened with sound through the panel as Matroska over TCP, 60 s and a little more: 644 blocks received and 644 written, none dropped, refused or filled; 48 kHz, 4 channels, 2 played; mpv's `audio-pts` ran with the clock. The page said silent (no level) throughout, so this pattern sent silence, and nobody listened. This shows that blocks travel over TCP, not what they sound like. `avsync` read 0.2 to 0.3 s on a source that sends about one picture a second; not judged, and no comparison was made without the key.
- **Still not run:** Wi-Fi, a 6.3.2 sender on another machine, the box's one sound source, a real programme with sound, a long run. The risk D110 names for TCP on a lossy link stands.
