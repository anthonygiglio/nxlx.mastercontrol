<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Room's column names no longer cover the Walls column

- **Why.** Found in a sweep of the sandbox's reference screens: on Room at a desk (1440 x 900, the Operator), with the page scrolled a little, the Guests name and its band of page colour sat over the Walls column beside it and cut off the foot of the Stage card (its Mute row). Since #235 each column's name sticks at the top of its column on a band as wide as the column, but Room keeps Scenes and Guests in the page (they share one column of its grid), so their names stuck to the window and their bands crossed into Walls.
- **What.** Only a column that scrolls by itself keeps its name stuck: the rule takes the same exceptions as the column scroll (not Scenes, not Guests). Walls, and the columns of Play and Shape, are as before.
- **Checked.** In the sandbox at 1440 x 900 as the Operator, scrolled: the Stage card is whole. tests/ui/panel.test.js checks that on Room at 1440 the Scenes and Guests names are static and the Walls name is sticky.
