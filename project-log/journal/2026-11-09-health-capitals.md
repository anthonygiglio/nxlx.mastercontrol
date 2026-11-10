<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Status lines begin with a capital

- **Why.** On Now, Needs you showed Projection mapping as "the player did not take the mapping (player is not running); it is tried again when the player restarts", lower case among lines that are sentences. The coordinator asked for the fix where the lines are made, and a check so it does not come back.
- **What.** The messages the mapping and the masks give `_set_status()` begin with a capital: four in pvj/mapper.py, three in pvj/overlay.py. The words are otherwise the same. The panel shows them as given ("Problem: " before them on the Mapping card, which reads as well with a capital).
- **Checked.**
  - tests/test_status_capitals.py reads pvj/*.py and fails on any string given to `_set_status(state, message)` that starts with a small letter. It failed on the old code and passes now.
  - tests/ui/panel.test.js checks that every line of Needs you, its name and its words, begins with a capital.
