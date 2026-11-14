<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-14, strobe press at a loop point

- Fixed the Strobe (and every effect or shader change that asks for a picture) being refused at a loop restart: Effects.picture keeps the last picture for PICTURE_GRACE seconds while the same item plays (D140). Tests in tests/test_effects.py (LoopGapTest); two older tests that drop the picture now also drop the held one. Not checked on hardware.
