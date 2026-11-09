<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, nodes announce themselves and list each other (`node-discovery`, D127)

- **Why.** Step 2a of the fleet plan: the owner approved discovery first, then the switcher in the panel (2b), then signed enrollment (2c). This pull request is the node side only; there is no panel page yet.
- **What.** `pvj/fleet.py` (the node id, reading mDNS answers, the cached list, the announcement), two commands in `pvj-sysd` that write or remove one avahi service file, `GET /api/nodes` (Operator and up), `POST /api/nodes/discovery` (Owner), the `fleet.discovery` setting, `pvj/FLEET.md`, device rows in `tools/DEVICE-TESTING.md`, `tests/test_fleet.py`.
- **Checked first.** `project-log` and the code for mDNS, avahi, D7, D80, D126 and render nodes: nothing contradicted. NDI's opt-in installs avahi and a plain install must not; this adds no package and works (as a lister) without avahi. D126 is the presence branch's, so this decision is D127.
- **Found on the way.** `pvj-web` cannot write `/etc` and `avahi-utils` may be absent, so the file is written by the root helper and the list is read with a one-shot question of our own. The unit's umask (0027) would have made the file unreadable for avahi's account; the helper sets 0644 explicitly and a test checks it.
- **The one thing for the owner.** `pvj-sysd.service` gains `-/etc/avahi/services` in `ReadWritePaths` (see D127 and `pvj/FLEET.md`). Nothing else in a unit or a firewall changes.
- **Not run on hardware.** avahi serving the file, avahi answering our question, two real nodes seeing each other, a node with avahi stopped. Rows are in `tools/DEVICE-TESTING.md`.
