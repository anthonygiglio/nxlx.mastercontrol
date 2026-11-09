<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, The Nodes list reads on every title bar

- **Why.** The owner, 2026-11-09: "the node switcher texxt is too hard to read against teh different colours". The list opened inside the title bar with no background of its own. In Signal the bar takes the area's colour (yellow on Room, lavender on Shape), so the white and grey text of the list sat on it.
- **What.** The list is a card of its own in the page colours (`--bg` and `--fg`), with a border, for every look. In Signal it has the bar's dark edge (`--on`, the rule's width) and its own padding; the Signal rule sets the background too, since Signal's card style would otherwise leave it clear.
- **Also.** tests/ui/panel.test.js: the check that a badly formed remembered entry is ignored waited for 12 rows, which the list already had before Look again redrew it, so it could read the old list (failed once, on the mapping-look run). It now waits for the answer, then for the redraw.
- **Checked** in the sandbox at 1440 as the Owner on Now, Play, Shape, Room and Setup in Signal, Signal light, Dark stage and Light.
