# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The one order of the locks that playing something, stopping it and setting the picture's level can hold (D71).

THE RULE: a thread never waits for a lock that stands earlier in ORDER than one it holds. Two threads that keep to
it cannot wait for each other. A lock may be taken with others above it held or with none; it may not be taken with
one below it held. (Trying a lock without waiting for it can break no order, and taking an RLock again that the
thread already holds is not a second lock.)

A SECOND RULE, for `fader.stepping`: while it is held, nothing waits for the player except the one write of the
level that the holder came for, and never for `player` (the player's own lock), which stands before it. A load can
hold `player` for seconds (mpv is asked, and the wait for its new path goes on up to three); Blackout needs
`fader.stepping` and nothing else, so it never waits behind a load. That is why `player` stands before
`fader.stepping` and not the other way round: whoever needs both waits for the load first, holding nothing a
Blackout needs.

The order is not kept by readers. Every lock below is made by `make`, which in production returns a plain lock at
no cost. The tests put another factory here (tests/lockrank.py) whose locks know their place, keep for each thread
what it holds, and fail the test at any taking that breaks the order: both paths only have to RUN in any test, in
one thread, for an inversion to show; the interleaving that would hang is not needed.

| place | name                 | what it guards, and what its holder may wait for                                      |
| ----- | -------------------- | ------------------------------------------------------------------------------------- |
| 1     | room.order           | one Room scene at a time; held over the scene's own calls of the API                    |
| 2     | vibes.work           | one change of the rotation at a time (only ever tried, nobody waits for it)            |
| 3     | shaders.engine       | one shader shown at a time; held while the GPU looks at it (seconds)                   |
| 4     | effects.engine       | the same for the effect over the picture                                               |
| 5     | mapper.apply         | the newest mapping and its switch into the player, together                            |
| 6     | capture              | a live input from its pipe to its helper's start (a second or more)                    |
| 7     | transitions.holding  | one still is taken at a time, from the freeze to the overlay (the screenshot's time)   |
| 8     | player               | Player._lock: one change of what plays at a time; held over a load (seconds)           |
| 9     | transitions.io       | one overlay command at a time                                                          |
| 10    | transitions.mark     | the transitions' counters; never held over a call                                      |
| 11    | fader.stepping       | one write of the picture's level at a time (the second rule above)                     |
| 12    | fader.lock           | the fader's token and label; nothing at all is done while it is held                   |
| 13    | shaderlive.cfg       | the shaders' settings                                                                  |
| 14    | pinscreen            | what is drawn of the pairing codes                                                     |
| 15    | leaves               | vibes.state, shaderlive.queue, mapper.state: a few fields each, never held over a call |

Not in the order, because their holders never call into these paths while holding them (read, not checked by the
machine): the settings' lock, the MIDI, DMX, OSC and sync managers' own locks, the Room's `lock` ("never held while
talking to a projector or the player"), the projectors', the health checks' and the uploads' locks.
"""
import threading

ORDER = ("room.order", "vibes.work", "shaders.engine", "effects.engine", "mapper.apply", "capture", "transitions.holding",
         "player", "transitions.io", "transitions.mark", "fader.stepping", "fader.lock", "shaderlive.cfg", "pinscreen",
         "vibes.state", "shaderlive.queue", "mapper.state")
LEAVES = ("vibes.state", "shaderlive.queue", "mapper.state")       # share the last place: none is held with another


def plain(name, reentrant=False):
    return threading.RLock() if reentrant else threading.Lock()


factory = plain         # the tests put tests/lockrank.py's factory here


def make(name, reentrant=False):
    """A lock that has the place `name` in ORDER."""
    if name not in ORDER:
        raise ValueError("no place in the order of the locks for %r" % (name,))
    return factory(name, reentrant)
