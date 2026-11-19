<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D144. Root's own state folder is /var/lib/nxlx-update (2026-11-19)

Root's update backups and the clip stick's claim live in /var/lib/nxlx-update, root:root 0700, not in /var/lib/nxlx. Why: the panel account owns /var/lib/nxlx and can rename anything in it, so it could hide root's backups across a factory reset and put them back, or replay the claim; a folder whose parent is /var/lib (root's) cannot be touched by it. The name follows the nxlx-<thing> pattern of /var/lib/nxlx-player and /var/lib/nxlx-netd and is one path in mctl/paths.py (ROOT_STATE_DIR). Cost / rules out: one more folder to make at install (install.sh does, so nxlx-sysd's ReadWritePaths exists), and a one-time move of older files (migrate_legacy_state); a root folder inside the panel's folder, even with checks, is ruled out.
