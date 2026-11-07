<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, Deblocking off on a Pi

- The owner chose it after the stills: `--skip-deblock` on Pi 3 and 4, on all video cores (D120). `renderer.deblock` true is the way back, and the supervisor counts it as part of a run, so it restarts once and not in a loop, the fault #195 fixed for the other switch.
- Tests: the arguments, the clean, the wish, and the real binary taking the option. Device rows DB1 to DB4 in `tools/DEVICE-TESTING.md`, not run yet on this build.

