<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D136 (2026-11-15): install.sh runs the converter, and the way back is only its undo

With rename PR 7 the release itself has the new system names, so the converter stops being a tool for a cutover by hand and becomes part of the install:

- install.sh finds the old layout (/opt/pvj, /etc/pvj or /var/lib/pvj), puts the new release into /opt/pvj/releases/<version> and runs `nxlx-convert --run --release <version>`. The switch of "current" to the new release is one of the converter's own journaled changes, so `--undo` (by hand, or the 15 minute timer) brings back the release the node ran before together with the old names. A refusal stops install.sh with nothing changed.
- The panel cannot do the first conversion: the updater of a release with the old names refuses this bundle (it looks for bin/pvj-player). The PR 6 stubs that let it read the bundle are gone. The first install of 0.1.2 on a node is install.sh over ssh, from an admin login that is not the deploy account (it is renamed).
- After a conversion the updater refuses a bundle or a rollback with the old program names; `sudo nxlx-convert --undo` is the only way back, because a release with the old names runs only under the old units, accounts and folders.
- Variable names (PVJ_*) are held by the converter until rename PR 8 (ENV_NAMES_LIVE), as release names already were, because the code of PR 7 still reads them. PR 8 turns both on together with the code.
- Cost: a node on PR 7 converts at its first install, before PR 8; PR 8 then needs a second, smaller pass of the converter for the variable and release names (its rows only).
