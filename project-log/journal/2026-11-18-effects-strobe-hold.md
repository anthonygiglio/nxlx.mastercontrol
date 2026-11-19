<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-18, J-128 (B4): the Effects list can no longer start a steady strobe

- **The bug.** D134 rule 7 left the Effects page able to put the Strobe on, steady, at the cap. The owner's card (J-128) closes it: a flashing filter starts from the Effects list only through the hold, or behind "Allow faster than the flash limit" with a warning.
- **The fix, on the node.** `Effects.flash_gate(sid)` (mctl/effects.py): a filter in `HOLD_ONLY` or whose file says it flashes is refused with 409 and a plain sentence when the opt-in is off; with it on it starts and the answer carries `warning`. Called by `POST /api/effects` with an id, `POST /api/effects/preset` for an effect other than the one on, and `POST /api/effects/chain` with an id. The hold (mctl/strobe.py) calls `put` and the chains directly, so it is untouched. Next, Previous, the one button and OSC on/off take their ids from `order()`, which now skips flashing filters through the same gate while the opt-in is off.
- **The panel.** The Put on button of a flashing row is disabled with a plain line while the opt-in is off; with it on the row and the answer carry the seizure warning.
- **Tests.** `EffectsListFlashTest` in tests/test_strobe.py: refused without the opt-in (list, preset, chain, uploaded flashing file), quiet filter untouched, the hold still works, the opt-in starts it with a warning. No browser test was run for the panel button.
- **Follow-up (coordinator).** Stepping and controllers closed as above, tests for stepping past an uploaded flashing file, the one button and OSC on; a panel-ui assertion for the disabled Put on; addendum to D134.
