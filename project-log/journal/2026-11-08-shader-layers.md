<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, shader transitions: a design and a measurement on node-01

- Found on node-01 (Pi 4, New renderer, Mix crossfade 2.0 s): clip pads crossfade, shader pads cut, a shader pad also cuts a running fade short, and a clip after a shader blends from a frozen still. Cause read in the code: Source 1's generator is slot 0 in `fx.c`, applied after the layers are mixed, so a fade has nowhere to keep the old shader (D73, D98).
- Wrote `design/shader-transitions_2026-11-08.md` and proposed D130: a generator slot for each video layer of Source 1, `pvj-fade-to-program`, the still retired on the New renderer, four slices, nine device rows (LT1 to LT9), four questions for the owner.
- Measured on node-01 without new code, by loading program files into Sources 1 to 3 through the renderer's socket and reading its own counters. Light pairs and medium with light hold 60 Hz at 30 runs a second; two medium shaders fall to 40 to 45 flips a second. Table in section 6 of the design.
- What went wrong and how it was handled: my first run named a program file wrongly (ridgelines), so the heavy pair did not run; run again with the right name. During the first run another session deployed to the node (`systemctl daemon-reload` and a restart of pvj-web, pvj-sysd, pvj-supportd and pvj-player at 03:57:26); the run's last crossfade repetition and its cleanup lost the socket. The new renderer came up idle, which is how the node began, and my temporary files were already removed. No setting, pairing or login was touched.
- Not measured: the real shader to shader transition (needs slice 1), a Pi 5, a desktop, a feedback-buffer pair. Nothing was run on the projector.
