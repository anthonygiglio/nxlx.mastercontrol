<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-14, strobe press at a loop point

- Fixed the Strobe (and every effect or shader change that asks for a picture) being refused at a loop restart: Effects.picture keeps the picture across a gap that mpv's own state shows (D140). After review: the gap is judged by mpv (idle, seeking, time-pos); the picture is a real read taken when the item starts (Player.play and fade_to_program tell Effects.note_source), tied to path, epoch and clears, with no plain stand-in; the stand-in generator is checked first; an unreadable ipc no longer raises. Tests in tests/test_effects.py (LoopGapTest). Not checked on hardware.
