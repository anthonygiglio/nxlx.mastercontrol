<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-24, pvj-render phase 3 on the test Pi with the X27: device rows FX11 to FX21 and SK1

- **Run:** one projector session, 62.0 minutes on, by `nxlx_shared/scripts/phase3_session.py` (written by me, reviewed with a dry-run harness and run by the coordinator). The results are in `measurements/2026-10-25-phase3/run-20261024-160437/`, and the table is under the phase 3 rows in `tools/DEVICE-TESTING.md`.
- **Setup:** New renderer, detail auto, a 1024 x 768 screen, 2 x 2 quads on Sources 1 to 4, 720p clips decoded in software. Effects worked at 720 x 540, generators at 960 x 720. Everything was put back as found and the projector is in standby.
- **Passed:**
  - FX13: Blackout over an effect is black (mean 0.0), and Fade in comes back.
  - FX14: Stop clears the chain.
  - FX17: classic refuses uploads with passes in plain words.
  - FX20: the Sources card's calls, through the API only.
  - FX21: rebuilt on a change of detail and on a replaced upload, values kept.
  - SK1, 44 minutes: RSS flat at 586 to 589 MB against about 875 MB and climbing before #169; 58.8 flips/s; fds 24 to 26, threads 83 to 86, GL objects 26; 58 to 60 C.
- **Frame rates, flips a second:**
  - 4 clips plain: 58.3.
  - fx-grade on all 4 (FX11): 42.7.
  - A chain of 3 on Source 2 plus fx-grade on the others (FX12): 32.3.
  - A feedback trail (FX15): 55.2.
  - A two-pass blur generator (FX16): 59.4 at 540, 59.2 at 720.
  - One shader as Source 2 (FX18): 59.4.
  - Shaders on Sources 2 to 4 (FX19): 50.5, with about one run in five dropped.
- **Not proven:** the 192 MB budget's refusal. All 11 slots took the large buffer at 720 x 540.
- **Not read:** the GPU's share (no counter); the rows at 390 px on a phone.
- **What it means:**
  - Effects or shaders on all four sources at once cost a quarter to almost half the frames on a Pi 4. One program alone costs little.
  - Most likely GPU-bound, not proven: the CPU stayed near 79 to 87% of the box with or without effects, and was at 15% while three shaders dropped runs.
  - For D108: the spike must include an effect per deck, and the program pass's skip when nothing changed matters.
  - For the effects' cost estimate: a box-wide count (passes a second by area) and a warning like D101's source warning, with a threshold set from more than one run.
