<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-30, the sound feed test no longer races the stale limit

- **The flake:** `test_a_feed_becomes_a_record_only_while_a_fed_source_is_chosen` failed now and then in CI, `'waiting' != 'capturing'`. A feed counts for STALE_SECONDS (0.5 s), and on a slow runner that much could pass between the file's read and the look at the status. The service was right, the test was late.
- **The fix:** the test feeds again and waits for the state. Five runs in a row pass locally.
