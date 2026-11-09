<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D129 (2026-11-09): the place of a graphic after a motion

- The engine's worker no longer loses a request made while it finishes a pass for the same graphic (the cause of a picture left at its old place after quick changes).
- A change of place on a graphic that is on with a scroll or a crawl plays it across again. For a crawl only the row (and the nudge down) is the place; for a scroll only the column (and the nudge across). The panel says so under the grid and greys out the nudge that does nothing. Nothing in the renderer changed.
