<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, A crossfade asked as the last one ended could lose its clip

- CI failed `ManyFadesTest` once on a docs-only PR: fade 26 of 180 stayed in `waiting` for 10 s. The code had not changed, so it was read as a bug, not a flake.
- The fade's end queues a `stop` on the outgoing layer, and the next crossfade loads into that layer. The comment there said the load lands after the stop. In mpv 0.40's `misc/dispatch.c` a blocking command takes the core's lock ahead of what is queued, so it can land first, and the stop then ends the new clip. No failed load and no first frame follow, so the fade waits out `FADE_FIRST_FRAME` and goes to an empty picture.
- Fixed: the stop is counted and sent with a reply, and `pvj-crossfade` waits for that reply, at most 1 s, before it loads. The render thread still never waits on libmpv.
- `PVJ_RENDER_TEST_SLOW_STOP` holds the stop back until the load is in. Without the wait the new test fails 3 of 3 at 10.05 s, the same wait CI saw; with it, passes 5 of 5. A stress loop of the real timing never hit it in four 90 s runs, so whether the CI failure was this path is inferred from the matching state, not caught in the act.
- Not run on the node yet at the time of writing. The CI run also failed `ShotPaceTest`, a timing test, 26.01 against 26.35 on a loaded runner, unrelated.
