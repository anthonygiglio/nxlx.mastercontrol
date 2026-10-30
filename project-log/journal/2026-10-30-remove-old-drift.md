<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-30, remove the old nxlx-drift shader

- The owner asked for the old drift shader to be deleted: the new Drift is part of the Signal Synth app and will be called Signal.Drift, added later in its own pull request. Deleted `pvj/shaders.d/nxlx-drift.fs` and its rows in `PI4` and `PI4_1440` (`pvj/shaderlive.py`).
- Tests re-pointed at nxlx-nebula, which has the same classes as drift had (medium at 1080p, heavy at 1440p): `tests/test_shaders.py` lists, the AUTO_540 list, the heavy-mark and set tests in `tests/test_shader_engine.py`, the Automatic hint in `tests/ui/panel.test.js` (Aurora, Nebula). Counts went from 47 to 46 and 25 to 24 calm shaders. The Drift buttons are gone from `tests/ui/fixtures/controls-before.json`.
- Docs: `pvj/SHADERS.md` (library row and the present-state lines; the measured tables stay as history), `docs/MANUAL.md`, and rows A4, A5, FX2 and FX6 of `tools/DEVICE-TESTING.md`. Rows that record past results are unchanged. HANDOFF.md and the other history of D93 are left as written. Clock and sync drift, and the Drift control of nxlx-stars and fx-slit-bands, are other things and stay.
- A saved name of the removed shader needed no code: new test shows a set, the disabled and included lists, a heavy mark and a preset naming it leave the page, saves and rotation working, and a play of it is a 404. Addendum to D93.
