<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-15, shader sliders reach their exact ends (J-160)

- **The fault.** A shader's number slider moves in 200 steps of (top - bottom) / 200. A range input allows only bottom + n x step up to its top, and that step can come out a hair too big in floating point, so the browser stopped one step short: Tide's speed topped out at 1.991 of 2. The review of PR 293 found it once that test's wait really waited.
- **The fix** (pvj/web/shaders.js): the float step is made a billionth smaller, so the last position falls just under the top, and a value within half a step of either end is sent as that end exactly. Whole-number sliders (step 1) were never affected and are unchanged.
- **Checked:** a model of the browser's snapping over eight ranges: the old step missed the top on three (1.991 of 2, 0.299 of 0.3, 0.68 of 0.7), the new one reaches both ends on all eight, and middle positions are the same. The panel test for Tide's speed is strict again (within 1e-6 of the top, as it was before PR 293 allowed one step).
- **Not checked:** on a phone or a node by hand; the browser test in CI is the proof.
