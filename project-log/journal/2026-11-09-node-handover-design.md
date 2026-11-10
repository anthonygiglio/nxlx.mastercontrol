<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Design: opening another node without pairing again

- **Why.** The owner asked that a device paired on one node can open the others without pairing each.
- **What.** design/node-handover_2026-11-09.md and proposed decision D132. Recommends a one-time ticket kept by the first node and redeemed by the second with a call back, roles never raised, and a root-signed fleet list for Owner carry. No code.
- **Note.** The fleet proposal of 2026-11-07 is not in the repo; D127 quotes it.
