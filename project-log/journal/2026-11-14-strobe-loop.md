<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-14, strobe press at a loop point

- Fixed the Strobe (and every effect put) being refused at a loop restart or a seek: Effects.put waits up to half a second, outside the lock, for video-params to come back when mpv says it is a loop point or a seek (D140). Replaces the remembered-picture design after review (no hook, no thread, no stored read). Tests: tests/test_effects.py LoopGapTest, tests/test_strobe.py RouteTest. Not checked on hardware.
