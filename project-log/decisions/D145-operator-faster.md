<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D145. Owner and Operator may allow faster than the flash limit (2026-11-20)

The switch "Allow faster than the flash limit" (`config.faster` on /api/shaders) may be switched on by an Owner or an Operator, not by a Guest (owner's answer on J-176, 2026-11-20). Why: the Operator runs the room day to day and already has every other shader and effect setting (D80); the switch stays off by default and carries the warning about photosensitive epilepsy. Cost / rules out: an Operator can let flashing filters and Performance shaders run faster than 3 times a second without the Owner; making it Owner only is ruled out for now. No code change: the route is at the Operator's level since D80; tests/test_shader_engine.py now checks that an Operator is allowed as well as that a Guest is refused.
