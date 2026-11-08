<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, Now says who is connected and what they last changed (`now-presence`)

- **Why.** The owner asked whether people see the other active controllers. The node now knows who is connected and what each last changed (D126, #218). The panel shows it.
- **What.** At the top of Today on Now:
  - "Connected now: 3 here: Anthony's phone (you), FireTab, 1 guest", as the node tells this device. An Operator is told the Owner's device by its role, a support login gets no names, and guests are counted where they are not listed.
  - Up to three of the last changes: the area and "Changed by FireTab (Operator), 12 s ago", or "by you".
- **No new request.** It is drawn from the status the panel already asks for every second, and drawn again only when its words change.
- **Test.** panel.test.js on Now: this device is "here (you)", and the tap just made on the clock is the change "Tempo, Changed by you".
