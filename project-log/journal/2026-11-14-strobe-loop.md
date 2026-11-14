<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-14, strobe press at a loop point

- Fixed the Strobe (and every effect or shader change that asks for a picture) being refused at a loop restart: Effects.picture keeps the picture across a gap that mpv's own state shows (D140). After review: the gap is judged by mpv (vid, idle, seeking, time-pos), not by when the picture was last read; the stand-in generator is checked first; the held picture is tied to path, epoch and clears; an unreadable ipc no longer raises. Tests in tests/test_effects.py (LoopGapTest). Not checked on hardware.
