<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## In libmpv a blocking command can overtake a queued one (2026-11-01)

`mpv_command_async` goes on the core's queue. `mpv_command` takes the core's lock, and `mp_dispatch_queue_process` serves a lock request ahead of the queue. So async then blocking, even from threads ordered by our own lock, does not mean they run in that order. The renderer's comment assumed they did, and a crossfade asked as the last one ended could lose its clip.

What we do now: when two commands to one core must run in order, wait for the first one's reply, or send both the same way. When a CI test fails on code the PR did not touch, read what state it stopped in before rerunning: a timing test that missed by a hair is noise, a fade stuck for its whole time-out is a bug.
