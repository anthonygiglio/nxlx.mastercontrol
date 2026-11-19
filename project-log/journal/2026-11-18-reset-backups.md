<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-18, J-055: a factory reset now removes the update backups

- A software update keeps copies of the settings in /var/lib/nxlx/backups, a root-only folder, and a rollback puts the newest one back. A factory reset could not reach that folder, so a rollback after a reset would have brought the old devices, passwords and pairings back.
- nxlx-sysd (the root helper) has one new action, clear_update_backups. It takes no name or path from the panel: it empties that one fixed folder, deleting only plain files named settings-<version>-<time>.json, never a link or a folder. A missing folder is fine. The unit file may now write /var/lib/nxlx/backups and nothing more of /var/lib/nxlx.
- The factory reset calls it after the other clean-up. If the helper cannot be reached or a file stays, the reset still finishes and its problems list says in words that a rollback could bring old devices back. A node with no helper (a laptop) has no such folder, so nothing is asked.
- A rollback with no saved settings copy now says so: the program goes back, the settings stay as they are, and nothing old returns. Not tried on a real node.

- Review fixes: install.sh now makes /var/lib/nxlx/backups (root, 0700) so the path exists when nxlx-sysd starts and its unit can write it; the update's own makedirs is fine with that. The update now ends with a non-blocking try-restart of nxlx-sysd (the update runs in its own unit, so it does not stop itself), so the new action exists without a reboot. If an older helper still answers unknown command, the reset says to restart the node and reset again. The helper opens the folder without following a link, checks it is root's, and works by name inside the open folder; a matching name that is not a plain file counts as one that stayed.
