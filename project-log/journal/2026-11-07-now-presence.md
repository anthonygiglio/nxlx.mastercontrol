<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, Now says who is connected and what they last changed (`now-presence`)

- **Why.** The owner asked whether people see the other active controllers. The node now knows who is connected and what each last changed (D126, #218). The panel shows it.
- **What.** At the top of Today on Now:
  - "Connected now: 3 here: Anthony's phone (you), FireTab, 1 guest", as the node tells this device. An Operator is told the Owner's device by its role, a support login gets no names, and guests are counted where they are not listed.
  - Up to three of the last changes: the area and "Changed by FireTab (Operator), 12 s ago", or "by you".
- **No new request.** It is drawn from the status the panel already asks for every second, and drawn again only when its words change.
- **Test.** panel.test.js on Now: this device is "here (you)", and the tap just made on the clock is the change "Tempo, Changed by you".
- **Found by the full run.** The first version named its helper for "N s ago" `agoWords`. The OSC page has a function of that name, and the later declaration replaced it, so OSC said "1 min ago" where it says "1 minute ago". Renamed `presenceAgo`.
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone, on the whole stack over master.
- **Review of the PR.**
  - The guests come from the node's own count (`guests`), less those it lists by name (the Owner's list), not from the count less the list. A Guest is told "3 here", never "3 guests" of itself.
  - The lists are taken only when they are lists, a time that is not a number says nothing, and an answer without presence clears the lines.
  - The test routes the status's presence: as a Guest is told ("3 here", "Changed by someone"), as an Operator is told ("4 here: FireTab (you), Owner, 2 guests"), and none.
  - A Guest has no Now itself (tests/ui/shell.js, roles), so the Guest case is the node's answer shown on this page.
