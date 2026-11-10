<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-10, A label for the handover events in What happened

- **Why.** Node handover slice 2 (PR 267) added a kind of event, "handover", for the fleet list and accepting devices from other nodes. The panel's What happened card showed the raw word "handover" for it.
- **What.** EVENT_KINDS in pvj/web/app.js now labels it "Other nodes". A new unit test, tests/test_event_kinds.py, checks that every kind in pvj/events.py KINDS has a label in EVENT_KINDS, so the next new kind cannot arrive without one.
- **Checked.** python3 -m unittest tests.test_event_kinds tests.test_lockrank passes; node --check on app.js passes.
