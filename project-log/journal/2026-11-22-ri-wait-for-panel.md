<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-22, real-install waits for the panel after the conversion

- The real-install check failed twice on PR 317 at "the device paired before the conversion was signed out", then passed on a rerun. The log shows the converter starting the panel again and curl meeting port 80 closed about one second later.
- The test asked once; it now waits up to 40 seconds for the panel to take the old token, as it already does after the undo. Test-only change.
- The same wait now covers the check after the second conversion, which had the same single-shot race (found in review).
