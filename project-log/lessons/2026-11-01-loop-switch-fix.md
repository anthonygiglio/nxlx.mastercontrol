<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, A way back is a feature, so run it before it is needed

The kill switch of the loop lists had unit tests for its reading and its keeping, and none for the one process that reads the same file for another reason. It would have failed at the moment it was wanted, by taking the picture away. Adding a key to a shared file means checking every reader of that file, and a switch counts as untested until it has been thrown on a real box.
