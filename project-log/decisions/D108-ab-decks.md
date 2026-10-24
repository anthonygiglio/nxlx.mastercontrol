<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D108. A and B decks for a Performance mode: two sources mixed by a crossfader and a blend, in the mixing stage pvj-render already has (2026-10-24)

**A design only. Nothing here is built yet.** It is a draft for the owner and the coordinator to read before any code. Revised the same day after the coordinator's review (previews, failures mid-show, hardware faders, rates, live rules, the curve, roles, sound, the order of the steps). Three choices are left open on purpose; they are under "Decide before step 1".

**The owner's words.** His wishlist named "an A and B deck for a Performance mode", "a small assortment of blending modes" and "robust, reliable core engines so that we can expand". On 2026-10-20 he asked for the core engines first (D101 built its mixing stage for that reason). On 2026-10-24 he chose the decks as the next thing, after phase 3 of the renderer (D106 steps a to e).

**What is there to build on.**
- **Sources (D101).** pvj-render runs up to 8 sources. Each source is a libmpv core for clips, a slot 0 for a generator (D106 step d) and up to three effects (D106 step b; the owner keeps three per source, see the addendum to D74).
- **The mixing stage (D101).** Each source already carries an `opacity` and a `blend`, set with `["pvj-source", N, "set_property", "opacity"|"blend", VALUE]`. Only `normal` is built. The stage's shader (`FS_MIX` in `render/src/main.c`) lays a layer over a base by a share and a blend. D98's crossfade is that same pass, with the share moved by a clock.
- **Source 1 is the program today.** It is the player as it always was: its sound, D98's two layers and their crossfade, the Mix's transitions, Vibes, the pads, the effect under Effect. Sources 2 to N have no sound.
- **The rule, from D101:** the decks, the crossfader and the blends are settings of the stage, not special cases in the code.

**What a deck is.** A deck is a source, seen from the performer's side. It holds:
- what plays on it: a clip, a shader, later NDI or a live input;
- its three effects and their amounts;
- its own transport: play and stop now, loop and pause later.

It is not a new kind of object in the renderer. Deck A and Deck B are two source numbers held in the settings: **A is Source 1, B is Source 2** by default. A setting may name other sources later, because the stage reads two numbers, not two fixed places.

**How A and B reach the screen.** A new pass of the mixing stage makes the **program picture**.
- It reads deck A's picture and deck B's picture after each deck's own effects.
- It lays B over A by the crossfader and the blend.
- It writes the result to **a texture of its own**, at the internal size.

Where surfaces read it from is the first open choice below. With Performance mode off, the pass is not run and nothing changes from today. Deck A keeps everything Source 1 has: a clip played to A still crossfades inside A by the Mix (D98's two layers), and the A and B crossfader is a second, manual fade above that one.

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
- **Fade to B** (or to A) moves the same value by the renderer's clock, as D98's crossfade is moved. It has **its own duration** in the settings (2 s at first), not the Mix's, since the two fades are different gestures.

**Live rules.**
- A cut or a manual move of the fader (from anywhere) cancels a Fade to B or A that is running. The hand wins.
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

**Decide before step 1** (the coordinator's list; my suggestion beside each, for the owner's word).
1. **Where surfaces read the program from.**
   - (a) Overload Source 1: while Performance mode is on, a surface on Source 1 shows the program.
   - (b) **A new choice for a surface, "Program"**, beside Source 1 to N. Source 1 then always means deck A alone.

   (b) is clearer: a surface's choice never changes meaning with a mode, a monitor quad can show deck A alone, and the show table already carries a source index per surface. The cost is the owner choosing "Program" on his surfaces once; a one-press "show the program on every surface that shows Source 1" in the desk makes that quick. **Suggested: (b).**
2. **Where the level and Blackout apply, exactly once.**
   - Today the Mix's level and Blackout are drawn on Source 1's picture after its programs (D106).
   - With decks they should apply **once, to the program**, after the mix. Deck A's own picture is then drawn at full level into the program pass, so a Blackout never darkens A twice or leaves B lit.
   - A surface showing a deck alone also goes black under Blackout, as D101's surfaces on Sources 2 to N do today.

   **Suggested: once, on the program, and Blackout on everything.**
3. **Whether the Mix's transforms apply to the program or per deck.**
   - The transforms are size, position, rotate, flips and opacity. libmpv applies them per core today, so they are Source 1's.
   - **Suggested: per deck for now.** Deck A keeps Source 1's, deck B has none until the full sources panel gives every source its own. The program pass does not move or turn the picture.
   - The other way (one transform on the program) is simpler for a performer but needs a transform stage in the program pass, which is new code.

**The panel's Performance mode, as a sketch.** A desk of its own (an area of the Workspace, D72, beside Live and Mapping).
- **On a laptop (1440 px):** three columns.
  - Deck A on the left: what plays, its preview, its pads bank (a tap plays to A), its three effects with their amounts, its shader row.
  - Deck B on the right, the same.
  - In the middle: the program's preview, the **crossfader** as a long horizontal slider, the four blend buttons above it, the cut buttons A, Centre and B under it, Fade to B or A, Blackout.
- **On a tablet (820 px):** the decks side by side and the middle row across the bottom.
- **On a phone (390 px):** one deck at a time behind two tabs, A and B, with the crossfader pinned at the bottom of the screen.
- **Previews in the panel, not on a quad.** Small, low-rate thumbnails (about 2 a second, a few kilobytes each) of A, B and the program, read back off the render thread as the screenshots are (D106's readback) and sent to the panel. A monitor quad on the projector would be seen by the audience, so the previews live in the panel. Built in step 3.
- **Keys:** the left and right arrows move the fader a tenth; Home and End cut to A and B.
- The desk is built from the Sources card's parts (D106 step e), so a deck's controls and a source's controls are one code.

**The steps, one pull request each.**
0. **Before any code:** the owner's word on the three choices above, and the Pi spike (deck B decoding, deck A crossfading, a program pass at 540 and 720).
1. **The program pass in pvj-render.**
   - The blends `add`, `screen` and `multiply` in `FS_MIX`.
   - The `pvj-mix` commands (decks, crossfader, blend, fade duration, on and off), the curve per blend, the fade by the clock and its cancel by a move.
   - The program's own texture; the skip when nothing changed; the level and Blackout once.
   - Read back in `pvj-render`. Tests by pixels for each blend at 0, 0.25, 0.5 and 1.
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
5. **Later, when he asks:** lighten, darken and difference; a deck's own loop and pause; NDI and a live input as a deck; D106 step (f) so deck A can fade into and out of a shader on its own; a transform stage on the program.

**Rules out.** No second program or compositor beside the mixing stage. No mixing of two sound streams. No hue modes. Nothing on classic.

**Cost.** One more pass a frame while Performance mode is on and something changed, and a second source decoding all the time. Measured on the Pi before step 1 (the spike) and in step 1 (the device row). It is his choice to keep it if it does not hold at 720.
