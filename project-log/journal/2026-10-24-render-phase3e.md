<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-24, the review of #172 (D106 step e), fixed on its branch

- **Medium:** a rebuild leaves a source that took a Stop, a clip or another shader meanwhile (`expect`, looked at under the chains' lock); the advanced section keeps its hand-set state over the redraws. **Low-Medium:** the rows wrap at 390 px (swept at 390, 820, 1440); the count is the upload's limit and only the weight takes the passes. **Low:** gone inputs dropped in a rebuild and what could not be rebuilt said; one request on classic; libraries asked again; choices and a dragged amount kept; the same detail builds nothing; an effect's put asks once; real waits in the panel test.
- **Tests:** the three races, a gone input and a refused rebuild, the same detail, the effect's one question, the blur's count and weight; the panel's open section, choice and amount over two redraws, and the width sweep. **Break-checks (12, each caught):** no look before the rebuild's build; no path handed in; a gone input refusing the rebuild; what was not rebuilt unsaid; the same detail building; an effect's put asking twice; the weight of one pass; the limit times the passes; the section forgotten; the choice drawn over; the amount drawn over; the rows not wrapped.
- **Logged:** buffer reads in the effects' count; a replaced effect not rebuilt in slots 1 to 3.
- **CI caught:** the card's new rules were first put at the end of app.css, inside the "signal" style's block, where every rule must be scoped to that style (`test_modules_themes`); they now stand before its marker.

## 2026-10-24, pvj-render phase 3, step e (D106): the panel's controls for a source's chain

- **Built:** in the Sources card on the New renderer, a Shader row on Sources 2 and up (slot 0, read from the chain), an Effect row, and the advanced slots 2 and 3 closed unless one is on. The logged items: the effects' cost counts every pass; a play asks once whether the player runs programs; the shaders of Sources 2 to N built again on a replaced upload and on a change of Picture detail.
- **Tests:** the panel's chain controls; the cost of two passes; one question a play; the rebuilds. **Break-checks (6, each caught):** the cost counting one pass; a play asking twice; a replaced upload ignored; Picture detail ignored; every source rebuilt on any upload; the Shader row not read from slot 0.
- **The panel test's ws.roles failure** ("a guest sees Test pattern on Mapping, and cannot press it") is this machine's: clean master failed it in 1 of 3 runs here, with nothing of this step in it, and this branch in 2 of 3 (the same block of steps, the run that passed went through the chain controls and every other step); CI's panel-ui passes.
