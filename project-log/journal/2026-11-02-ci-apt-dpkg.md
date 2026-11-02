<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-02, CI: an interrupted install no longer fails all three attempts

- `tools/ci-apt.sh` cuts an attempt off at 90 s. Cut during unpacking, dpkg stays marked as interrupted and the next two attempts stop at once, so the mirror's retry never got a chance. Seen on #196 and #199, each passing on a rerun. Each retry now runs `dpkg --configure -a` first.
- The fix for the fade stop (#199) ran on the node: 60 crossfades, each asked as soon as the last ended, 60 done, none cut, slowest 0.82 s, no restart, none of the new log lines. The window is microseconds, so this shows nothing broke, not that the race was hit.
