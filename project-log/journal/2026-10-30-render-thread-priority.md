<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-30, fix 1 of the playback measurements: the render thread at real-time priority, and a clear before the final pass

- **Built:** pvj-render's render thread asks for SCHED_RR at priority 1 once, before its loop (render/src/main.c), and the final pass clears its target first. The player unit gets `LimitRTPRIO=1` (install/pvj-player.service); nothing else in the unit changed, as it has no capability or sandbox keys.
- **Measured before (a scratch build on the test node, 4 sources at 720p, mapping on, 1080p output, 20 s windows):** 40.7 flips a second as shipped; 44.9 with the clear; 45.3 with the clear and the real-time thread, and no flip longer than 2 vblanks. Not measured with this build on the node (it was not touched).
- **Ran here (WSL, Ubuntu, kernel 6.18):** pvj-render built with WERROR=1 and with TEST_HOOKS=1; `tests.test_pvj_render` PriorityTest, PictureTest, MappingTest, PaceTest and `tests.test_units` pass. The test that needs the right ran as uid 1000 with RLIMIT_RTPRIO 1:1, no capabilities and no-new-privs (`prlimit` and `setpriv` as root): it passed. As a plain account with a limit of 0 it skips, and the no-right test passes.
- **Runaway thread, measured here, not on the Pi:** `sched_rt_runtime_us` 950000 per `sched_rt_period_us` 1000000. One RR-1 spinner on 4 cores: a normal task lost nothing (worst 12 ms loop). Four spinners on 4 cores: the normal task still ran, with 301 loops against 518 and a worst loop of 961 ms. The Pi's kernel setting was not read.
- **Not done:** the display scaler, the 30 Hz lock and the loop seam (fixes 2 to 4); the node check (DEVICE-TESTING row RT1).
