<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D93 (2026-10-30): the old nxlx-drift is removed

The owner: "Simply delete the old drift shader. This new Drift is part of the Signal Synth external module/app. It can be referred to as Signal.Drift". `pvj/shaders.d/nxlx-drift.fs` is deleted, with its rows in `PI4` and `PI4_1440`. D93's figures that name it (the 47 shaders, 41 at 720 lines, six drawn at 540, drift and nebula the two heavy ones at 1440p) are now 46, 41, five (aurora, lantern, nebula, isf-ridgelines, isf-simplex-noise) and nebula alone. The run of 2026-10-16 is history and stays as measured, with the shader's rows in `pvj/SHADERS.md`. Signal.Drift is not added here; it comes in its own pull request.

A box set up before still has the name in a set, a list, a mark, a preset or a pad. That is taken as any other shader that is not there: the page, the saves and the rotation go on without it, and a tap on it is a 404 (`test_a_setting_that_names_a_shader_the_box_no_longer_has_is_passed_over`). Nothing needed fixing.
