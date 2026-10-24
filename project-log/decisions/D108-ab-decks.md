<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D108. A and B decks for a Performance mode: two sources mixed by a crossfader and a blend, in the mixing stage pvj-render already has (2026-10-24)

**A design only. Nothing here is built yet.** It is a draft for the owner and the coordinator to read before any code.

**The owner's words.** His wishlist named "an A and B deck for a Performance mode", "a small assortment of blending modes" and "robust, reliable core engines so that we can expand". On 2026-10-20 he asked for the core engines first (D101 built its mixing stage for that reason). On 2026-10-24 he chose the decks as the next thing, after phase 3 of the renderer (D106 steps a to e).

**What is there to build on.**
- **Sources (D101).** pvj-render runs up to 8 sources. Each source is a libmpv core for clips, a slot 0 for a generator (D106 step d) and up to three effects (D106 step b; the owner keeps three per source, see the addendum to D74).
- **The mixing stage (D101).** Each source already carries an `opacity` and a `blend`, set with `["pvj-source", N, "set_property", "opacity"|"blend", VALUE]`. Only `normal` is built. The stage's shader (`FS_MIX` in `render/src/main.c`) lays a layer over a base by a share and a blend. D98's crossfade is that same pass, with the share moved by a clock.
- **Source 1 is the program.** It is the player as it always was: its sound, D98's two layers and their crossfade, the Mix's transitions, Vibes, the pads, the effect under Effect. Sources 2 to N have no sound.
- **The rule, from D101:** the decks, the crossfader and the blends are settings of the stage, not special cases in the code.

**What a deck is.** A deck is a source, seen from the performer's side: what plays on it (a clip, a shader, later NDI or a live input), its three effects and their amounts, and its own transport (play, stop, later loop and pause). It is not a new kind of object in the renderer. Deck A and Deck B are two source numbers held in the settings: **A is Source 1, B is Source 2** by default. A setting may name other sources later, because the stage reads two numbers, not two fixed places.

**How A and B reach the screen.** A new pass of the mixing stage makes the **program picture**. It reads deck A's picture and deck B's picture after each deck's own effects. It lays B over A by the crossfader and the blend, and writes the result to one texture at the internal size.
- While Performance mode is on, the final pass reads that texture wherever a surface shows Source 1. So every mapping the owner already has shows the mix with no remapping.
- A surface set to Source 2 (or 3, 4) still shows that source alone. A side screen or a monitor quad can therefore show deck B by itself as a cue preview.
- With Performance mode off, the pass is not run and the program is Source 1 exactly as today.
- Deck A keeps everything Source 1 has. A clip played to A still crossfades inside A by the Mix (D98's two layers). The A and B crossfader is a second, manual fade above that one.

**The crossfader.** One value from 0 (A only) to 1 (B only).
- It is set with a new socket command, `["pvj-mix", "set", "crossfader", X]`. Like D106's values, it is copied once a frame under `app.lock`, so a frame never mixes two settings.
- The panel sends it as it is dragged, at most 30 times a second, as the shader values are sent. A MIDI fader, OSC (`/pvj/crossfader`) and DMX set it through one new action, `crossfader`, beside `fade` and `blackout` in `pvj/actions.py`.
- **The curve.** For `normal` the mix is straight: A times (1 - x) plus B times x. For the other blends, B's share rises to full by the middle and A's falls from full after the middle: B's share is min(1, 2x) and A's is min(1, 2 - 2x), so both are full at the centre. That is how hardware mixers treat additive modes, and it keeps the centre from looking dim. The curve belongs to the blend, so the panel needs no second control for it.
- **Cut buttons:** "A", "B" and "Centre" set the value at once.
- **An automatic fade:** "Fade to B" over the Mix's seconds is the same value moved by the renderer's clock, as D98's crossfade is.
- **Blackout, Fade out and the level** act on the program after the mix, as they act on Source 1 today, so the screen still goes black at once.

**The blends, and which come first.** Each blend is one line in `FS_MIX` and costs the same on the GPU: two texture reads a pixel, which the pass does anyway, and a few operations.
- **First:** `normal`, `add`, `screen` and `multiply`. Normal mixes; add and screen are the two that VJs use most to lay a light picture over another; multiply darkens one picture by the other. These four answer "a small assortment".
- **Next, when he asks:** `lighten`, `darken` and `difference`, which D101 already lists as names.
- **Not planned:** the hue, saturation and luminosity modes. They need a colour space change per pixel, and nothing he has said asks for them.
- A blend the build does not know stays refused with "invalid parameter", as D101 has it.

**How it runs on the Pi 4.** The program pass is one more draw a frame at the internal size, the same work as D98's crossfade pass (which held at 540 and 720 lines in the spike and did not hold at 1080 output). So:
- Performance mode wants the **renderer's detail at 720 or below**.
- It wants **720p clips on both decks**, since D101 measured several 720p sources decoding in software side by side on the Pi.
- The pass runs only while Performance mode is on, and not at all when the crossfader is at 0 with `normal` (deck A's picture is then the program, as today).
- Deck B decodes even when the crossfader is at A, so its picture is ready when the fader moves. That is the cost of a deck, and the panel says so in plain words: "Deck B plays even when you cannot see it".
- Each deck's effects and shader count against the box-wide 192 MB and the 30 runs a second of D106.
- A device test measures the pass on the Pi before anything is called "held": flips, missed vblanks and the GPU's share at 540 and 720, and the four blends against each other. The four should cost the same; the test proves it.

**What classic does.** Nothing changes. Classic mpv has one source, so it has no decks. The Performance desk says "Performance mode needs the New renderer (System > Screen output)" and offers nothing else. The API refuses `crossfader` and the blends there with the same words, as D101 refused Source 2. The crossfader action from a controller is answered by a light that stays off and a line in the journal, never an error that stops a show.

**Sound.** Deck A has the sound (Source 1's), wherever the crossfader is. Sources 2 to N have no sound (D101: one sound device). Sound that follows the crossfader would need a mix of two cores' sound, which the box does not do. It is not part of this design, and it is said in the panel.

**The panel's Performance mode, as a sketch.** A desk of its own (an area of the Workspace, D72, beside Live and Mapping), for the Owner and an Operator.
- **On a laptop (1440 px):** three columns.
  - Deck A on the left: what plays, its pads bank (a tap plays to A), its three effects with their amounts, its shader row.
  - Deck B on the right, the same.
  - In the middle: the **crossfader** as a long horizontal slider, the four blend buttons above it, the cut buttons A, Centre and B under it, Fade to B or A, Blackout.
- **On a tablet (820 px):** the decks side by side and the middle row across the bottom.
- **On a phone (390 px):** one deck at a time behind two tabs, A and B, with the crossfader pinned at the bottom of the screen.
- **Keys:** the left and right arrows move the fader a tenth; Home and End cut to A and B.
- What each deck plays is said by name, as the Sources card does; a thumbnail comes with the full sources panel (D101 step 3).
- The desk is built from the Sources card's parts (D106 step e), so a deck's controls and a source's controls are one code.
- A guest sees nothing of it. The desk follows the roles, as D101's routes do.

**The steps, one pull request each.**
1. **The program pass in pvj-render.** The blends `add`, `screen` and `multiply` in `FS_MIX`; the `pvj-mix` commands (decks, crossfader, blend, on and off); the curve per blend; the automatic fade by the clock. Read back in `pvj-render`. Tests by pixels for each blend at 0, 0.5 and 1. A device row for the cost.
2. **The panel's side of the engine.** The settings (`performance`: on, deck A, deck B, blend, crossfader), `GET` and `POST /api/performance`, the roles, the `crossfader` action for MIDI, OSC and DMX, and classic's refusal. Restored after a restart of the player, as the renderer's choice is.
3. **The Performance desk** in the panel, at 390, 820 and 1440 px, with the browser test.
4. **Controllers:** the built-in maps for the owner's three MIDI controllers get the crossfader on a fader, and the Launchpad gets cut buttons. This waits for his word on the mapping section (the open question in his controller notes).
5. **Later, when he asks:** lighten, darken and difference; a deck's own loop and pause; NDI and a live input as a deck; D106 step (f) so deck A can fade into and out of a shader on its own.

**Rules out.** No second program or compositor beside the mixing stage. No sound mixing. No hue modes. Nothing on classic.

**Cost.** One more pass a frame while Performance mode is on, and a second source decoding all the time. Measured on the Pi in step 1, and his choice to keep if it does not hold at 720.
