<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D108. A and B decks for a Performance mode: two sources mixed by a crossfader and a blend, in the mixing stage pvj-render already has (2026-10-24)

**A design only. Nothing here is built yet.** It is a draft for the owner and the coordinator to read before any code. Revised the same day after the coordinator's review (previews, failures mid-show, hardware faders, rates, live rules, the curve, roles, sound, the order of the steps). **Revised again on 2026-10-30 with the owner's answers** to the three choices (under "Decided by the owner"), and with a constraint from the Pi measurements of 30 October (under "Constraint: the order of the work"). Nothing is left open except the Pi spike and the owner's order for the renderer's work.

**The owner's words.** His wishlist named "an A and B deck for a Performance mode", "a small assortment of blending modes" and "robust, reliable core engines so that we can expand". On 2026-10-20 he asked for the core engines first (D101 built its mixing stage for that reason). On 2026-10-24 he chose the decks as the next thing, after phase 3 of the renderer (D106 steps a to e).

**What is there to build on.**
- **Sources (D101).** pvj-render runs up to 8 sources. Each source is a libmpv core for clips, a slot 0 for a generator (D106 step d) and up to three effects (D106 step b; the owner keeps three per source, see the addendum to D74).
- **The mixing stage (D101).** Each source already carries an `opacity` and a `blend`, set with `["pvj-source", N, "set_property", "opacity"|"blend", VALUE]`. Only `normal` is built. The stage's shader (`FS_MIX` in `render/src/main.c`) lays a layer over a base by a share and a blend. D98's crossfade is that same pass, with the share moved by a clock.
- **Source 1 is the program today.** It is the player as it always was: its sound, D98's two layers and their crossfade, the Mix's transitions, Vibes, the pads, the effect under Effect. Sources 2 to N have no sound.
- **The rule, from D101:** the decks, the crossfader and the blends are settings of the stage, not special cases in the code.

**What a deck is.** A deck is a source, seen from the performer's side. It holds:
- what plays on it: a clip, a shader, later NDI or a live input;
- its three effects and their amounts;
- its own level (the Mix's level, kept separately for each deck);
- its own transport: play and stop now, loop and pause later.

It is not a new kind of object in the renderer. Deck A and Deck B are two source numbers held in the settings: **A is Source 1, B is Source 2** by default. A setting may name other sources later, because the stage reads two numbers, not two fixed places.

**How A and B reach the screen.** A new pass of the mixing stage makes the **program picture**.
- It reads deck A's picture and deck B's picture after each deck's own effects.
- It lays B over A by the crossfader and the blend.
- It writes the result to **a texture of its own**, at the internal size.

Where surfaces read it from is decided below: a surface may choose Program. With Performance mode off, the pass is not run and nothing changes from today. Deck A keeps everything Source 1 has: a clip played to A still crossfades inside A by the Mix (D98's two layers), and the A and B crossfader is a second, manual fade above that one.

**D101's per-source opacity and blend.** The crossfader does not drive them and does not replace them. They keep meaning what D101 made them: how a source lies over a surface's black where a surface shows that source alone. The program pass has its own crossfader and its own blend. While a source is a deck, its own opacity and blend are left at 1 and `normal`, and the panel does not offer them for it, so there is one control per job.

**The crossfader.** One value from 0 (A only) to 1 (B only).
- **Setting it.** A new socket command, `["pvj-mix", "set", "crossfader", X]`. Like D106's values, it is copied once a frame under `app.lock`, so a frame never mixes two settings.
- **The rate, for every transport** (the panel, MIDI, OSC, DMX):
  - the latest value wins, with at most one call in flight per sender, so a fast drag never queues up stale positions behind it;
  - the panel sends at most 30 a second while dragging;
  - the settings file is saved on a debounce (once the fader rests for a second, and at the end of a show), never once per move.
  - **Target:** under 50 ms from a drag to the picture on the Pi, measured in step 1.
- **Hardware faders.** MIDI, OSC (`/pvj/crossfader`) and DMX set it through one new action, `crossfader`, beside `fade` and `blackout` in `pvj/actions.py`.
  - **Soft takeover (pickup):** a hardware fader whose position differs from the value moves nothing until it passes through the value. So a fader left at B never jumps the picture when someone moved the panel's fader to A.
  - **14-bit MIDI** (a pair of CCs) is taken where the controller sends it. 7-bit faders get a short slew (about 50 ms), so 128 steps do not show as steps.
- **The curve.** For `normal` the mix is straight: A times (1 - x) plus B times x. For the other blends, each deck has a presence:
  - A's presence is a = min(1, 2 - 2x), and B's is b = min(1, 2x), so both are full at the centre;
  - the picture is **a(1 - b)·A + b(1 - a)·B + ab·f(A, B)**, where f is the blend;
  - so x = 0 gives A alone, x = 1 gives B alone (also for multiply, which would otherwise darken B by A), and the centre gives the blend in full;
  - that is how hardware mixers treat additive modes, and it keeps the centre from looking dim;
  - the curve belongs to the blend, so the panel needs no second control for it.
- **Cut buttons:** "A", "B" and "Centre" set the value at once.
- **Fade** moves the same value by the renderer's clock, as D98's crossfade is moved, and it goes **in either direction**, not only to B. It has **its own duration** in the settings (2 s at first), not the Mix's, since the two fades are different gestures.
  - **The rule (the owner's word, 2026-10-31: part way, the transition goes "towards whichever deck it is closest to").** With the crossfader part way, Fade finishes the move to the nearer deck: below 0.5 it goes to A, above 0.5 to B.
  - **At an end, Fade crosses over.** At A (exactly 0) it goes to B, and at B (exactly 1) it goes to A, so the button always does something (his word of 30 October: "fade between A and B, not just to B").
  - **From the Centre (exactly 0.5): Fade goes to B.** That is the simplest rule: a tie goes to B.
  - The button's label says where it will go ("Fade to B" or "Fade to A") and changes as the fader moves, so a performer never has to guess.
  - **The duration is the whole fade, wherever it starts.** A fade from the Centre takes the set time, not half of it. This is the simplest to say and to hear; a fade at constant speed would be the other way and is not planned.
  - **Pressing Fade while a fade runs stops it where it is.** The hand wins, as under the live rules. Pressing again then starts a fade by the same rule from the new position.

**Live rules.**
- A cut or a manual move of the fader (from anywhere) cancels a Fade that is running. The hand wins.
- Loading onto the deck that is on air gets a warning, not a refusal:
  - when the crossfader is past 0.9 towards that deck, the panel's Play on that deck asks once "Deck B is on air: play there anyway?";
  - a controller's tap plays without asking, and its light flashes;
  - the rule is the same as a DJ mixer's: you load onto the deck the audience does not see.
- **A failure or restart mid-show.** If pvj-render ends and comes back, or one deck stops drawing (its clip fails, its shader is refused), the program goes to the deck that can still draw:
  - the crossfader is set to that side;
  - it is not put back to the other side until that deck plays again. That needs D101 step 3's put-back of what each source played, so it waits for that step, and until then the fader stays on the deck that draws;
  - the panel shows the deck that failed in its warning colour with the reason;
  - a controller's light for that deck flashes;
  - the journal says what happened.

  Classic's fallback (D105) is as today: classic has no decks, so it draws Source 1.

**The blends, and which come first.** Each blend is one line in `FS_MIX` and costs the same on the GPU: two texture reads a pixel, which the pass does anyway, and a few operations.
- **First:** `normal`, `add`, `screen` and `multiply`. Normal mixes; add and screen are the two that VJs use most to lay a light picture over another; multiply darkens one picture by the other. These four answer "a small assortment".
- **Next, when he asks:** `lighten`, `darken` and `difference`, which D101 already lists as names.
- **Not planned:** the hue, saturation and luminosity modes. They need a colour space change per pixel, and nothing he has said asks for them.
- A blend the build does not know stays refused with "invalid parameter", as D101 has it.

**How it runs on the Pi 4.** The program pass is one more draw a frame at the internal size, the same work as D98's crossfade pass. That pass held at 540 and 720 lines in the spike and did not hold at 1080 output. So:
- **A spike comes first, before step 1's C work.** On the Pi, measure deck B decoding a 720p clip while deck A runs a D98 crossfade and a stand-in program pass mixes the two, at 540 and at 720 lines: flips, missed vblanks, the GPU's share, the CPU. If it does not hold at 720, the owner hears that before anything is built.
- Performance mode wants the **renderer's detail at 720 or below**.
- It wants **720p clips on both decks**, since D101 measured several 720p sources decoding in software side by side on the Pi.
- The program has its own texture, so a surface reads it like any source.
- **The pass is skipped when nothing changed:** no new frame on either deck, the same fader and the same blend give the same picture as the frame before. It is also skipped when the crossfader sits at an end with `normal`: the program is then that deck's picture.
- Deck B decodes even when the crossfader is at A, so its picture is ready when the fader moves. That is the cost of a deck, and the panel says so in plain words: "Deck B plays even when you cannot see it".
- Each deck's effects and shader count against the box-wide 192 MB and the 30 runs a second of D106.
- A device row in step 1 measures the real pass: flips, missed vblanks and the GPU's share at 540 and 720, the four blends against each other (they should cost the same; the row proves it), and the drag-to-picture latency.

**What classic does.** Nothing changes. Classic mpv has one source, so it has no decks.
- The Performance desk says "Performance mode needs the New renderer (System > Screen output)" and offers nothing else.
- The API refuses `crossfader` and the blends there with the same words, as D101 refused Source 2.
- The crossfader action from a controller is answered by a light that stays off and a line in the journal, never an error that stops a show.

**Sound.** Deck A has the sound (Source 1's), wherever the crossfader is. Sources 2 to N have no sound (D101: one sound device). Sound that follows the crossfader would need a mix of two cores' sound, which the box does not do. One thing it can do is offered as an **option**, off by default: **duck deck A's sound by (1 - x)**, so A's sound fades as the fader moves to B, through Source 1's own volume. The panel says plainly that deck B has no sound.

**Roles.** The desk is the Owner's and an Operator's. A guest sees nothing of it, as with D101's routes.
- **While Performance mode is on, it holds back what would take Source 1 (deck A) from under the performer:**
  - the guest card's play;
  - the schedule;
  - Room scenes that play;
  - the autostart;
  - Vibes playing to Source 1.

  Each of these is refused or held with words that say Performance mode is on ("the performer has the screen"), and the journal says so. Blackout from a Room scene still works, because it is a safety control.
- **Remote support:** a support session may watch the desk but may not move the fader or cut. Moving the picture is done by someone in the room, as the box already has it for switching a projector off through remote support. This is said in the support banner.

**Decided by the owner (2026-10-30).** These replace the three open choices. The first answer differs from the earlier suggestion in point 1 (it adds Source 1 and 2 beside Program) and the second differs from the earlier recommendation, so each is written out.

1. **Surfaces gain a Program choice.**
   - A surface's source choice gains **Program**: it shows the mix.
   - **Source 1** shows deck A alone, and **Source 2** shows deck B alone. (With the default settings, deck A is Source 1 and deck B is Source 2, as above; a setting that names other decks makes Source 1 and Source 2 mean those decks.) Source 1 therefore never changes meaning with a mode.
   - **A surface without a choice shows Program while Performance mode is on.** That covers a surface that has no per-surface source setting; with Performance mode off it shows what it always did.
   - A one-press "show the program on every surface that shows Source 1" stays in the desk, so the owner does not set his surfaces one by one.
2. **Level acts on each deck separately, and Blackout covers everything at once.**
   - **Each deck keeps its own level.** The level is applied to a deck's picture **after that deck's own effects and before the program pass**, so a deck's picture is already at its level when it enters the mix. A surface showing a deck alone shows that picture, at that deck's level.
   - **Blackout is applied once, at the end,** to the program and to every surface showing a deck alone, so it covers everything at once and nothing is darkened twice by it. Blackout is not a level: it is a final cover over what the mix and the decks made.
   - **This differs from the earlier recommendation** (level once, on the mix). That recommendation was made so a multiply blend would not darken twice. The owner has chosen the other way, so the table below says what each blend does with a level on a deck. L(A) and L(B) are the two levels (1 is full, 0 is black); A' = L(A) times A and B' = L(B) times B are the pictures after their levels; the curve is the one above, a(1 - b)A' + b(1 - a)B' + ab f(A', B').

   | Blend | f(A', B') | What a level on one deck does |
   |---|---|---|
   | normal | the straight mix | Dims only that deck, towards black. The other deck is unchanged. At the Centre with L(B) at 0, the picture is A at half, not A at full, because the mix still gives B its half and B is black. |
   | add | A' + B' | Dims only that deck's part of the sum. At L(B) = 0, the sum is A' alone: the other deck shows, unchanged by the blend. |
   | screen | 1 - (1 - A')(1 - B') | Black is neutral in screen, so L(B) = 0 gives A' alone, as in add. |
   | multiply | A' times B' | **Compounds.** The product carries both levels: at the Centre the product term is L(A) times L(B) times A times B. Two decks at half level give a quarter at the Centre. |

   - **The case the owner should see for himself: multiply.**
     - A level on **either** deck darkens the whole product, because the product term has both levels in it. At the Centre, **L(B) = 0 makes the whole picture black, not deck A alone**, since anything times black is black. Under normal, add or screen the same level change leaves the other deck showing.
     - Away from the Centre the effect is milder: x = 0 or 1 shows one deck alone, at its own level only, and a level on the other deck changes nothing there.
     - So the level on a deck under multiply behaves as a second darkener, in the way a multiply does by nature, and no code can make it otherwise without changing what multiply means. Nothing in the pass stops it; it is a thing to try on the real screen, with a clip on each deck, before the owner relies on it in a show. It is only worked out here, not looked at.
   - **A level change is never skipped.** The pass-skipping rule (nothing changed) counts a level change as a change, and the skip at a crossfader end with `normal` shows that deck's picture at its own level.
3. **Size, position and rotation apply to each deck on its own for now, and the mixed output gets transform controls too.**
   - **The owner's words: "There should be some form of transform controls for the mixed output as well."**
   - The per-deck transforms are as before: libmpv applies them per core, so they are Source 1's today (deck A's), and deck B's wait for the full sources panel (D106 step e's parts), which gives every source its own.
   - **The program transform** is new: **size, position, rotation and flips on the program picture as a whole.** It is a setting of the program pass (`["pvj-mix", "set", "transform", ...]`), not a second compositor.
   - **Where it sits relative to mapping surfaces: upstream of them.** The order is: each deck's own effects, level and transform; the mix; the program transform; then the surfaces, each with its own mapping (corners, warp, blend edges, mask). A surface that shows Program reads the already transformed picture and then places it by its mapping, so the program transform moves the whole picture on every surface that shows it, and the mapping still decides where the picture lands on each surface. A surface that shows Source 1 or Source 2 alone does not get the program transform.
   - How the program transform is drawn (in the program pass, or folded into each surface's sampling) is left to step 1 and its device row. Nothing is claimed about its cost.

**Constraint: the order of the work (2026-10-30).**
- **Measured on 30 October:** on the Pi 4 at 1920 x 1080 with 4 sources in software decoding, the output held **40.7 frames a second**.
- **The owner has ordered the work:** make **plain playback smooth first, then transitions, then shaders and effects.**
- **So decks building waits** for that order, and for the short Pi measurement already in this design (the spike below: deck B decoding while deck A crossfades and a program pass mixes them). The design may be refined meanwhile; no step below starts before then.
- **Nothing is claimed here about decks' performance.** The measurement above is of 4 plain sources, not of decks, the program pass, per-deck levels or the program transform. Those are measured by the spike and by step 1's device row.

**The panel's Performance mode, as a sketch.** A desk of its own (an area of the Workspace, D72, beside Live and Mapping).
- **On a laptop (1440 px):** three columns.
  - Deck A on the left: what plays, its preview, its pads bank (a tap plays to A), its three effects with their amounts, its shader row, its level.
  - Deck B on the right, the same.
  - In the middle: the program's preview, the **crossfader** as a long horizontal slider, the four blend buttons above it, the cut buttons A, Centre and B under it, the Fade button (it reads "Fade to B" or "Fade to A" by the rule above) with its time, the program's transform controls (size, position, rotation, flips), Blackout.
- **On a tablet (820 px):** the decks side by side and the middle row across the bottom.
- **On a phone (390 px):** one deck at a time behind two tabs, A and B, with the crossfader pinned at the bottom of the screen.
- **Previews in the panel, not on a quad.** Small, low-rate thumbnails (about 2 a second, a few kilobytes each) of A, B and the program, read back off the render thread as the screenshots are (D106's readback) and sent to the panel. A monitor quad on the projector would be seen by the audience, so the previews live in the panel. Built in step 3.
- **Keys:** the left and right arrows move the fader a tenth; Home and End cut to A and B.
- The desk is built from the Sources card's parts (D106 step e), so a deck's controls and a source's controls are one code.

**The steps, one pull request each.**
0. **Before any code:** the owner's order for the renderer's work (plain playback, then transitions, then shaders and effects) has to reach the point where decks come next, and the Pi spike has to be run (deck B decoding, deck A crossfading, a program pass at 540 and 720). The owner's three choices are decided above.
1. **The program pass in pvj-render.**
   - The blends `add`, `screen` and `multiply` in `FS_MIX`.
   - The `pvj-mix` commands (decks, crossfader, blend, fade duration, on and off, program transform), the curve per blend, the fade by the clock in either direction and its stop by a press or a move.
   - The program's own texture; the skip when nothing changed; each deck's level before the pass, and Blackout once at the end.
   - The program transform (size, position, rotation, flips), upstream of the surfaces' mapping.
   - A surface's Program choice, and Program as the default for a surface without a choice while Performance mode is on.
   - Read back in `pvj-render`. Tests by pixels for each blend at 0, 0.25, 0.5 and 1, and for each blend with a level on one deck (multiply at the Centre with L(B) = 0 included).
   - A device row for the cost and the latency.
2. **The panel's side of the engine.**
   - The settings (`performance`: on, deck A, deck B, blend, crossfader, fade duration), saved on a debounce; `GET` and `POST /api/performance`.
   - The roles, the holds of the guest card, schedule, Room scenes, autostart and Vibes, and remote support watching only.
   - The `crossfader` action for MIDI, OSC and DMX, with latest-value-wins, soft takeover, 14-bit MIDI and the slew.
   - The on-air warning; classic's refusal; the optional duck of A's sound.
3. **The Performance desk** in the panel, at 390, 820 and 1440 px, with the browser test.
   - The thumbnails of A, B and the program.
   - A failed deck's warning, and the fader kept on the deck that draws.
   - Putting B back after a restart, which needs D101 step 3's put-back; if that step is not built yet, this part waits for it and the rest of the desk goes ahead.
4. **Controllers:** the built-in maps for the owner's three MIDI controllers get the crossfader on a fader, and the Launchpad gets cut buttons and the deck lights. This waits for his word on the mapping section (the open question in his controller notes).
5. **Later, when he asks:** lighten, darken and difference; a deck's own loop and pause; NDI and a live input as a deck; D106 step (f) so deck A can fade into and out of a shader on its own.

**Rules out.** No second program or compositor beside the mixing stage. No mixing of two sound streams. No hue modes. Nothing on classic.

**Cost.** One more pass a frame while Performance mode is on and something changed, and a second source decoding all the time. Measured on the Pi before step 1 (the spike) and in step 1 (the device row). It is his choice to keep it if it does not hold at 720. None of this is measured yet for decks; the 30 October figure (40.7 frames a second, 4 sources, 1080p, software decoding) is not a measurement of them.
