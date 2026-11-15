<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-15, sliders reach their exact ends (J-160)

- **The fault.** A shader's number slider moves in 200 steps of (top - bottom) / 200. A range input allows only bottom + k x step up to its top, and that step can come out a hair too big in floating point, so the browser stopped one step short: Tide's speed topped out at 1.991 of 2. The review of PR 293 found it once that test's wait really waited.
- **The fix** (pvj/web/shaders.js, `grid(lo, hi, n)`): the browser gets a step a billionth smaller, so all n steps fit, and the value is worked out from the position k: lo + k x (hi - lo) / n, rounded to 12 digits, with the ends exactly lo and hi. The same helper now serves the shader inputs (200 steps), the shader's Speed and Brightness trim (200; Colour turn stays in whole degrees) and the effects' common controls (100, pvj/web/effects.js). Whole-number inputs are unchanged.
- **Review finding, fixed before merge.** The first version only shrank the step and rounded values near the ends, so every middle position drifted (1.5 became 1.4999999985, 0 on a -1 to 1 slider became -1e-9) and two exact panel tests would have failed. Taking the value from the position removes the drift.
- **Checked in real Chromium** (Playwright's, headless): every position of 13 ranges at 200 and 100 steps, 3,926 in all, gives lo + k x (hi - lo) / n and exact ends; the old step missed the top on 0.2 to 2 (1.991 at 200 steps, 1.982 at 100). The panel test for Tide's speed is strict again (within 1e-6 of the top).
- **Lesson.** A model of the browser's snapping said 0.1 to 0.3 also missed; real Chromium reached it. Check browser rounding in the browser.
- **Not checked:** by hand on a phone or a node; the panel-ui job is the proof for the rest.
