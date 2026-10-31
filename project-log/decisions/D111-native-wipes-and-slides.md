<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D111. The wipes and the slides are drawn by pvj-render itself, as the crossfade's stage with the picture cut along an edge (2026-10-31)

**Why.** His order is plain playback, then transitions, then shaders and effects (2026-10-09 and since). D108 and the HDMI check of 2026-10-31 (`measurements/2026-10-31-hdmi`, section 4) showed the fault: with 4 sources of 720p clips and the mapping on, a wipe or a slide makes 26 to 33 of 60 steps, the clip drops 24 to 38 frames in 2 s, MAX_DROPS trips and the box dips for 5 minutes. With one clip they are fine, and the crossfade, drawn by the renderer since D98 and D101, ran 1199 times in the soak at this load. D98 said wipes and slides "are the next thing to draw in the compositor". So this reverses nothing of his. D71's still is D71's way for a player that shows one picture, and it stays for classic.

**What.** One more word on the crossfade's command: `["pvj-crossfade", URL, SECONDS, OPTIONS, MOVE]`, MOVE one of the panel's eight names. Everything of D98 holds as it is: the incoming clip goes into the idle layer, the socket speaks to it from then on, the outgoing clip plays on silent, the clock starts at the incoming clip's first frame, the share runs in a straight line over SECONDS, a load, a stop, a step in the list or a new transition ends it at once, and the outgoing layer stops at the end. The only difference is the picture of a vblank: `move_pass` in place of `mix_pass`.

**The same geometry as the still.** `MOVE_LIST` is class Move in numbers: the edge lies at round(share x size) columns or rows, no softness, as `Move.gone`. A test asks the panel's own `Move.step` where the old picture's part is, in all eight, at two moments, and reads every seventh pixel on both sides of the edge, so the two cannot drift apart.

**A slide stays what it is: it is not a push.** The panel's button says "Slide off left" and the manual says the new clip "does not move with it (this is not a push)". The brief for this change said a slide must move both pictures as it does now; the code, the manual and D71 say it moves one. "Unchanged" won: the outgoing picture moves off, the incoming one stands and plays. A push is now cheap (one more shift in `move_pass`, and a name), and it would be a new choice for the Owner, not this fix.

**Cost: less than the crossfade's.** Two scissored draws, one from each layer, so a pixel reads one texture where the mix reads two, and no mixing. The mix texture's last picture is invalidated first, the same saving as the final pass's (D106's addendum, D108). At the internal size, before the effects and the final pass, which are not touched.

**What the Owner sees differently from the still** (the same as D98 did to the crossfade):
- The outgoing clip plays on; no freeze before it, no still of 80 to 470 ms.
- The edge moves at every vblank, not 30 times a second.
- Blackout, a fade and an opacity change darken both pictures and it goes on (with the still they ended it).
- **Under a mapping** the cut is in the inside picture, so a surface shows it warped with the picture: a wipe crosses each surface that shows source 1, not the screen, and on a turned surface it runs along the surface. Surfaces of other sources play on. This follows from running before the mapping's final pass, which the brief required, and it is the one change of look: row MW3 asks him.
- A logo or a mask stays over both pictures; in a still it moved off with the old picture.
- The box does not give up: there is no still, so MAX_DROPS and the rest do not apply, as for the crossfade since D98. The status says no fallback for them.

**The panel asks.** `moves` in the `pvj-render` property, once per process (`Player.moves`, as `crossfades`). `Api._layered_fade` takes the transition's name. A plain clip on the screen and a renderer that says `moves`: the renderer's. Classic mpv, a pvj-render from before this one, a generator or a live input on the screen: D71's still, as the crossfade does (`LayeredCrossfade` in `tests/test_transitions.py`). The renderer refusing at the moment: a cut, never black, with the transition's name in the journal. No setting changes, no schema change, nothing to roll back: an older panel never sends the fifth word, and an older renderer refuses it and is never sent it.

**Measured:** see `measurements/2026-10-31-native-wipes` and below.

Not run on the node at this commit.

**Tests.** `tests/test_pvj_render.py` (MoveTest, MovePaceTest, ManyMovesTest, PlayerFadeTest, FinalPassTest), `tests/test_native_shaders.py` (MoveEffectTest), `tests/test_transitions.py` (LayeredCrossfade), headless on llvmpipe and over the stand-in mpv. Break-checks in the journal entry. Rows MW1 to MW6 in `tools/DEVICE-TESTING.md`.
