<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-31, NDI switches: the box receives over TCP (D110)

- **The fault:** the library's reliable UDP (QUIC) failed to start from the HP's senders, so a first picture took up to 7 s and past 6 s the press was refused. Found with a bare library bench and a packet capture on the node; `measurements/2026-10-31-ndi-switch`.
- **The change:** `CONFIG_TRANSPORT` in `config_text`, and the helper starts again by itself when the file says another transport than the library was started with. The updater starts the helper before the panel, so the old panel could load the library with the old file, and a new file with the same discovery servers would have restarted nothing.
- **Tests:** the file's key spelled out, read back, the update's road with no second restart, an unreadable file restarting nothing. Break-checks in the PR. `CONFIG_TRANSPORT` empty fails the first; the look taken out fails the road.
- **Not done:** the old picture staying up until the new one has a frame, and `AUDIO_WAIT`: left out on the coordinator's word. Not run on a box from this branch; the key alone was measured by a drop-in. Wi-Fi not run.
