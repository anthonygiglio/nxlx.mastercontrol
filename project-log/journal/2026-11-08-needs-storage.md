<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, Low storage reaches Needs you

- The storage lines of Health (#221, `Health.storage()`) now show in the panel, in three places:
  - Needs you on Now: a disk that is running low or almost full is a line of its own, named Storage, with what is free. On the media disk an owner gets Open the library, where clips are deleted; any other disk, or an operator (who cannot delete clips), gets Open to Health. A disk with room is not listed.
  - The Health row of Setup: a storage line counts toward its worst line, as the projectors do.
  - The Health page: one Storage line for each disk, with its state.
- `storageLine()` drops the leading "Storage: " from the Health words, since each place names the line Storage already.
- Stacked on #220 (Needs you, each problem by itself), which stops listing the Health row in Needs you: without this, low storage would show nowhere on Now once #220 merges.
- Test: panel.test.js gives Now a low media disk and a roomy system disk; only the low one is in Needs you, worded once, with Open the library.
