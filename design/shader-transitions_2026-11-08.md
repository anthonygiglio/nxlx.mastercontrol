<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Shader transitions: generators as layers, a design (2026-11-08)

Status: design, accepted by the owner on 2026-11-08 (all four recommendations, section 9). Nothing here is built yet. Decision: D130 (`project-log/decisions/D130-shader-layers-transitions.md`).
Written for Anthony (owner). Plain words first; the builders' detail follows.

## 0. What you will see, in plain words

Today, a clip pad crossfades, wipes or slides as you choose. A shader pad (Tide, Bloom, Kaleido and the rest) does not: it cuts in, and it also cuts a running fade short. A clip after a shader blends from a frozen photo of the screen, not from the moving shader.

After this work, on a Pi 4, a Pi 5 and a desktop running the New renderer, all three of these use the Mix transition you chose, with both sides moving the whole time and no frozen picture:

- shader to shader
- clip to shader
- shader to clip

Two limits you should know now, both from the node-01 numbers in section 6:

- Two shaders at once cost two shaders of drawing. On a Pi 4 at 540 lines, two light shaders, or one medium and one light, hold 60 Hz through a transition. Two medium ones (Nebula with Aurora, or Nebula with Ridgelines) do not: the screen falls to 40 to 45 flips a second. For that pairing the plan is that the shader on its way out slows down (still moving) so the fade stays smooth.
- A Pi 3 runs classic mpv and keeps today's behaviour: a cut into a shader. Nothing is promised there.

## 1. How it works today, and why shaders cut

Facts read from the code on master (cddfbf5, 2026-11-08):

1. pvj-render has two video layers for Source 1 (D98). A crossfade (`pvj-crossfade`) loads the incoming clip into the idle layer, and the render thread mixes the two layers (`mix_pass`, or `move_pass` for wipes and slides, D111) once a vblank.
2. A shader is not in a layer. It is slot 0 of Source 1 in `render/src/fx.c`: `fx_source(0, picture, ...)` runs after the layers have been mixed and replaces the whole picture. Under it the player keeps a blank "carrier" clip playing in the front layer.
3. So there is one shader for the whole of Source 1, and a transition has nowhere to hold the old shader while the new one comes in. `Player.fades_from_clip()` answers false whenever a shader or a live pipe is loaded, so `Api._layered_fade` says no and the panel falls back to D71: a photograph of the screen laid over the new picture. That photograph is the frozen still. D73 wrote the rule ("INTO a shader is a cut whatever the transition", `docs/MANUAL.md` line 153), and D98 wrote the exception for "OUT of" a shader, both saying that a shader list per layer, "generators as layers", is the cure. D106 step (d) built the other half (generators as whole pictures of Sources 2 to N). This design is the half left over for Source 1's layers.

## 2. The design: a generator belongs to a layer

### 2.1 In pvj-render

- Source 1 gets two generator slots, one for each of its two video layers (`gen[0]`, `gen[1]`), kept in their own array beside the `gl[source][slot]` table, so no existing slot number moves and D129's graphics can claim slots without meeting these.
- A layer's picture is its generator's texture when that layer has a generator, else libmpv's texture, as now. `mix_pass` and `move_pass` are handed those two textures. Nothing in `FS_MIX` or `FS_MOVE` changes: they already take any two textures at the inside size.
- Two programs are alive for the length of a transition (the incoming one and the outgoing one), each drawn into its own texture. At the end of the fade the outgoing layer is stopped, and its generator is cleared with it, by the same render-thread code that stops the outgoing clip today (`stop_outgoing`).
- Size: each generator keeps the size the panel gave it (D93's height, 540 on a Pi 4, from `//!SIZE`), so a single shader looks as it does now. The mix reads both textures with linear filtering into the inside size (960 x 540 on node-01). Cost of the mix is one pass at the inside size, which is the crossfade's existing cost (section 6: a clip crossfade alone missed no vblanks).
- Pacing: each generator runs at most `--fx-fps` times a second (30 on Pi 3 and 4, the screen's rate up to 60 on the full tier, D123). The mix runs every vblank at 60 Hz. The outgoing generator keeps its own TIME, so it keeps moving while the incoming one starts from its offset. A generator frame that runs late is counted as a drop, as in D106.
- The effects chain (slots 1 to 3), the level, a Blackout, the mapping and the final pass come after the mix and are not touched: an effect stays on through the transition, as D74 wants.
- Waiting for the incoming shader: a new program is built by the render thread (the load answers when it is built, at most 3 s). The fade clock starts at the incoming generator's first drawn frame, as it starts at a clip's first frame today. The outgoing picture keeps playing while the program builds. This is better than today, where a still is taken and the screen freezes for 80 to 470 ms.
- With nothing on the screen there is nothing to fade from: the shader appears as a cut from black, as a clip does (D98).
- Memory: a program with feedback buffers (D106 step c) doubles its buffers while two are alive. The existing checks (64 MiB per program, the node's whole budget) already run at load and refuse a second program that would not fit; that refusal falls to "cut" (section 7).

### 2.2 Commands (IPC)

Only the pvj-render property gains a word and the socket gains one command. A renderer from before this and a panel from before this stay compatible, as the other transition steps did (D98, D111).

- Property `pvj-render` gains `layer-generators` (true) and, from slice 4, `fades-degraded`. Version 0.9.0. A panel asks once per process, as it asks `crossfades` and `moves`.
- `["pvj-fade-to-program", PATH, SECONDS, MOVE?]`: load the program file at PATH (read by `fx.c`'s reader, same rules as `pvj-shader load`) into the idle layer's generator, make that layer the front, and start the fade (a mix, or a wipe or slide if MOVE names one). Answered when the program is built. If the GPU refuses it, the answer carries the compiler's words and nothing changes on the screen. It is a separate command and not a flavour of `pvj-crossfade` because a clip goes through the hardened libmpv load and a program does not.
- `["pvj-crossfade", URL, SECONDS, OPTIONS, MOVE?]` (clip in) works as now, and when the outgoing layer holds a generator it keeps that generator running until the end of the fade, then clears it. This is shader to clip.
- `["pvj-shader", "set"|"load"|"clear", 1, 0, ...]`: slot 0 of Source 1 now means "the front layer's generator". A value that moves goes to the incoming program (the front layer is the incoming one from the moment a fade starts). The panel's value worker therefore needs no change.
- The rule of D98 is kept: a load, a stop or a new transition while a fade runs ends that fade at once on its incoming side, then the new one starts from that. See question 2 in section 9 (decided).

### 2.3 In the panel (Python)

- `Player.fades_from_clip()` is replaced by `Player.fades_native()`. It is true when the renderer says `layer-generators` and neither side is a live input's pipe (NDI and capture keep D62's hand-over, and with them D71's still). A shader no longer makes it false.
- New `Player.fade_to_program(path, seconds, style, epoch)`: under the player's lock like `play_source`, same epoch rules (`source_epoch`), same refusals. On the New renderer a shader is no longer carried by a blank clip, so `_carrier` stays empty there and the carrier's decode and frame conversion go away. Classic and the Pi 3 are never sent any of this: they keep `play_source`, `_carrier` and the still.
- `LiveEngine.show` (the Shaders page, shader pads, controllers' queued taps) calls `fade_to_program` when something is on screen and a Mix transition other than "cut" is chosen; otherwise it behaves as today. `Api.play` for a clip, with a shader on screen, takes the layered path through `_layered_fade`, which now only refuses for a pipe. The ticket, level mark and epoch chain of D73 stay as they are, because this changes what the player is asked, not who may ask.
- `Api._play_shader_pad` is unchanged: it already goes through `LiveEngine.play`. Its docstring and D73's "cut" note get an addendum.
- `docs/MANUAL.md` line 153 and the Mix card's note are rewritten in slice 3.

### 2.4 How the fade from a still is retired

The still (D71, `pvj/transitions.py`) is not deleted. It stays for four cases only:

1. classic mpv, which includes every Pi 3
2. a renderer without `layer-generators`
3. a live input (NDI, capture) on either side
4. a renderer that has refused this fade just now (cut, never black)

For a shader or a clip on the New renderer it is never taken. The visible signs: `transitions.last` no longer reports `still_ms` for these fades, `mix.fallback` stays empty, the screen does not freeze for a moment before the fade, and the old picture goes on moving under the new one. Tests in `tests/test_transitions.py` pin these cases with the real `Player` over the stand-in renderer.

## 3. Interaction with other designs

**A/B decks (D108, draft PR 174).** Deck A is Source 1 and deck B is Source 2. A transition inside deck A is this work: A's two layers hold clip or shader, and the crossfader then mixes deck A with deck B in one more pass (`FS_MIX`). Deck B's own shader is slot 0 of Source 2, which already exists as a whole picture. What decks add is cost: a shader transition on A while B shows a shader is three generators drawn at once. Section 6 says three light ones hold, three that include a medium one do not, so the budget rule (section 5) is written for the whole node and not for a pair. A transition inside deck B (shader to shader on B) needs only a second generator slot for Source 2 and a mix, no second libmpv core, because a generator needs no clip layer; it is left to the slice after decks exist. If PR 174 is changed so deck B is not Source 2, nothing here moves: the generator slots follow the layer, not the deck.

**Graphics (D129, text and logos).** A graphic is drawn over the screen or over a source, or is a source of its own. Graphics sit on the text layer or on Sources 2 and up, after the program picture and its effects, so they stay over both sides of a transition exactly as a logo or a mask stays over a wipe (D111). They use none of Source 1's layer generators. They do spend GPU: each graphic as a source is a small pass, counted in the same budget (a graphic is weight 1 or less; its real cost is D129's slice to measure). One thing to settle at build time: the renderer arrays must not reuse the new `gen[]` names; D129's slot constants are separate.

## 4. Tiers

- **Pi 4**: the target of the measurements below. Lean values (540 lines inside, `--fast`, final pass Automatic). The budget rule applies.
- **Pi 5 and desktop (full tier)**: same code. D123 lets each generator run at the screen's rate (up to 60), which doubles each generator's cost against a Pi 4. None of this is measured on a Pi 5 or a desktop. Until it is, the budget rule uses the Pi 4's numbers and the rate guard (section 7) is on. Row LT8 measures it.
- **Unknown boards** (D122): lean values, so the Pi 4 rule applies.
- **Pi 3** (classic mpv, D105 frozen): no pvj-render, no layer generators. A shader pad still cuts in (D73), and out of a shader a clip still blends from D71's still, as now. The panel says so in the Mix card in words. A Pi 3 gets no worse and no better.

## 5. The budget rule (what holds 60 Hz)

A frame is 16.7 ms at 60 Hz. Generators run at 30 a second, so each one is drawn on every second vblank, and two meeting in a transition can land on the same vblank. The only costs in the panel today are classic's pass times (`shaderlive.PI4`), which are not the renderer's costs (OpenGL ES 3.0 has no timer query on V3D). The measured behaviour is simpler. Give each bundled shader a weight from its `PI4` class at 540 lines: light 1, medium 2, heavy 3. On a Pi 4 at 540:

- total weight 3 or less held 60 Hz at 30 runs a second in every run (light, light and light; medium and light; medium alone)
- total weight 4 (medium and medium) did not: 45 and 40 flips a second, generators at 22.5 and 20 runs a second

So the rule for the transition is: if the two generators together weigh more than 3 (and, with decks or graphics, if all generators live at once weigh more than 3), use the ladder in section 7. A shader with no class (an upload) is weighted heavy until measured. These numbers are for node-01 at its settings (540 inside, final pass Automatic), not for every Pi 4.

## 6. Measured on node-01 (2026-11-08, Pi 4 Model B Rev 1.5, V3D 4.2, renderer 0.8.0, 960 x 540 inside, 1920 x 1080 at 60 Hz on HDMI-A-1, 4 sources, `--fast --skip-deblock --final=auto`)

What was possible without new code. The node's renderer already draws a generator for each of Sources 2 to 4 (D106 step d), with no mapping so no mixing of them. I loaded program files (made from the bundled shaders by the panel's own `translate_native`, at 960 x 540) straight into the renderer's socket and read its own counters (`pvj-render`: flips, missed-vblanks, load; `pvj-shaders`: runs, drops) over 12 to 15 s windows. For the transition cost I did clip to clip crossfades (720p software decode on both sides, which is heavier on the CPU than shader to shader will be) with generators running on Sources 2 and 3. The cost of the mix pass alone is in the first clip row. What is not in these runs: Source 1 doing the fade between two generators (that needs the new code) and a Blackout or an effect on top.

| Case (all at 540, 30 a second asked) | flips a second | vblanks missed | each generator: runs a second | drops |
| --- | --- | --- | --- | --- |
| nothing playing | 60.0 | 0 in 6 to 8 s | | |
| one light (Tide) | 60.0 | 0 in 12 s | 30.0 | 0 |
| one medium (Nebula), 2 runs | 60.0 to 60.1 | 0 | 30.0 to 30.1 | 0 |
| two light (Tide, Bloom) | 60.0 | 0 | 30.0, 30.0 | 0 |
| three light (Tide, Bloom, Kaleido) | 59.9 | 2 in 12 s | 30.0 each | 0 (the final-pass governor stepped to 720 once) |
| medium and light (Nebula, Tide) | 59.6 | 5 in 12 s | 29.8, 29.8 | 0 |
| medium and light (Aurora, Bloom) | 60.1 | 0 | 30.0, 30.1 | 0 |
| medium and medium (Nebula, Aurora) | 45.1 | 225 in 15 s | 22.5, 22.5 | 48, 48 |
| medium and medium (Nebula, Ridgelines) | 40.0 | 300 in 15 s | 20.0, 20.0 | 100, 100 |
| clip to clip crossfade 2 s, no shader, 3 runs (4 s windows) | 60.1, 60.1, 60.1 | 0, 0, 0 | | |
| the same with Tide on Source 2, 3 runs | 60.2, 59.9, 60.2 | 0, 0, 0 | 30.0, 29.7, 30.2 | 0, 0, 0 |
| the same with Tide and Bloom on Sources 2 and 3, first two runs | 53.5, 57.5 | 25, 10 | 26.6, 28.4 | 10, 6 or 7 |

Reading it: one shader of any class and any light pair cost nothing visible. The mix pass is free at this load. The hard limit is two medium shaders at once, which saturates the GPU at about 40 to 45 passes a second between them. The last row is a harsher test than a real shader transition (two software video decoders on four cores run at the same time), so it is an upper bound on the damage; a third run of it was lost because another session deployed to this node at 03:57:26 (pvj-player was restarted by that deploy, not by these tests). The node's own governor stepped the final pass down to 720 lines under load (three journal lines of "the final pass goes to 720") and back, which is D108 working as designed and also a reminder that measuring on Automatic hides some of the cost.

Not measured, and why: a real shader to shader transition (the code does not exist), missed vblanks seen at the HDMI end, a Pi 5, a desktop, a feedback-buffer shader pair, 720-line generators in pairs. A real measure needs slice 1 (about one to two days of C and tests) and then row LT1 below.

State left behind: nothing playing, no program in any slot (`pvj-shaders` all off), temp files removed. A restart of the player by another session's deploy happened mid-test; the node came back idle, as it began. No pairing, no login, no setting changed.

## 7. Fallbacks when two shaders cannot both hold

Never black, never a frozen photograph of the whole screen. In order:

1. **Weight 4 or more at the start of a fade** (known from the table): the outgoing generator runs at half rate (every fourth vblank, 15 a second) for the fade only. It still moves, a little steppier, and the cost is 1.5 shaders, not 2. The incoming one runs at full rate.
2. **Overload during the fade** (more than 8 vblanks missed in the first 30 of the fade, or late generator frames): the outgoing generator freezes on its last frame for the rest of the fade. This is a still of one layer, only for the tail of the fade, and the counter `fades-degraded` says it happened. The D108 governor (final pass 720) is also free to step in.
3. **The incoming program is refused or does not fit memory**: no fade; the screen keeps what it had and the panel shows the GPU's words, as D73 says for a shader chosen by hand.
4. **Something else breaks mid-fade** (a Stop, a new play): the fade ends at once on its incoming side (D98).

Both degradations are logged in the node's event log (D125) with the shaders' names, so a repeat can be seen after a show.

## 8. Slices (one pull request each)

1. **Renderer: layer generators.** `gen[2]` for Source 1, layer textures into the mix and the move, fade end clears the outgoing generator, `pvj-shader` slot 0 to the front layer, property words. Headless tests on llvmpipe: two programs at half mix equal the average of each drawn alone (pixel check), a wipe edge with shaders on both sides, 2,000 fades with no object or thread left over (as D106's churn test), ASan and UBSan on the program reader paths. Device rows LT1, LT2.
2. **Renderer: `pvj-fade-to-program` and clip to and from shader.** Waiting for the build, the first-frame start, the outgoing generator kept running through a clip crossfade. Tests: all eight wipes and slides with a shader side, a refused program leaves the screen, a Stop during a fade. Device rows LT3, LT4.
3. **Panel: use it.** `fades_native`, `fade_to_program`, no carrier on the New renderer, `LiveEngine.show` and `Api.play` choose the layered path, status and Mix card words, `MANUAL.md` line 153, addenda to D73 and D98. Tests over the stand-in renderer and the real `Player`; the existing D73 tests that pin "into a shader is a cut" are changed for the New renderer and kept for classic. Device rows LT5, LT6.
4. **Budget and the ladder.** The weight table from `shaderlive`, the half rate, the freeze, `fades-degraded`, the event-log lines. Device rows LT7, LT8.
5. **Later, after decks and graphics exist:** a generator pair for Source 2 and up (decks' B side), Vibes using the chosen Mix transition between shaders, a balance of the node-wide budget when graphics are sources.

Slices 1 and 2 are the risky ones (render thread, GL objects). A fresh independent review of the C diff is recommended before slice 3, as for D106.

### Device rows (to add to `tools/DEVICE-TESTING.md`, filled with the numbers)

| Row | What | Passes when |
| --- | --- | --- |
| LT1 | Shader to shader, every Mix transition (crossfade, four wipes, four slides), light pair | 60 flips a second, 0 to 3 missed, both shaders visibly moving by eye at the output, no frozen frame, `fade-steps` near 120 for 2 s |
| LT2 | The same with a medium and a light | 59 or more flips, at most 8 missed |
| LT3 | Clip to shader and shader to clip, each style | no `still_ms` in `transitions.last`, the clip keeps playing under the shader, no freeze before it |
| LT4 | A shader the GPU refuses, as the target | screen unchanged, GPU's words in the panel, no black |
| LT5 | Tap another shader pad during a fade; Stop; Blackout; a preset change in the middle | no crash, no black, the level is right, the new picture is what the last tap chose |
| LT6 | Shader pads from MIDI and OSC at 10 taps a second for 20 s | no stuck fade, GL objects and threads flat |
| LT7 | Medium and medium: the ladder | half-rate outgoing seen, `fades-degraded` counted, flips at least 55 |
| LT8 | The same rows on a Pi 5 and on a desktop | the numbers recorded; the budget limit set from them |
| LT9 | A Pi 3 on classic | a cut into a shader and the still out of one, as before, and the Mix card says so |

## 9. Questions for Anthony (four, all decided on 2026-11-08: he accepted every recommendation)

1. **When two medium shaders meet, what should the fade do?** DECIDED: the outgoing one keeps drawing at half rate. Options: the outgoing one slows to half speed of drawing but keeps moving; it freezes; or it cuts. Recommendation: slow it (section 7, step 1), because a fade that is a little steppier on the way out is much less noticeable than a screen dropping to 40 frames a second.
2. **Should a tap on another pad in the middle of a fade jump to the new picture first?** DECIDED: yes, the running fade finishes at once, then fades to the new pad. Two layers can only hold two pictures, so a third has to wait. Recommendation: yes, finish the first fade at once, then fade to the new one (the same as clips do today). A true "blend from wherever it is" needs a third layer and costs another pass and another generator; revisit if you find the jump annoying.
3. **Should Vibes (the automatic rotation) use your chosen Mix transition between shaders?** DECIDED: yes, but only after slice 4 and a soak. Today it dips by level. Recommendation: yes, but only after slice 4 and one soak, since a rotation runs for hours and each fade is two shaders at once.
4. **The Pi 3: keep the cut, or give it a dip to black?** DECIDED: keep the cut. Classic can already dip. Recommendation: keep the cut; classic is frozen (D105) and a dip would be a new look to test on a board you are retiring.

## 10. Risks

- The render thread: a second program alive at the same time touches GL objects, texture sizes and the build wait. This is the area that needed fixes after review in D106. A leak or a stall here freezes the screen.
- Medium and medium is a real case: Nebula and Aurora are in the bundled set and a performer will pair them. The ladder makes it survivable and not invisible.
- The fade clock waits for a program to build (up to 3 s on a first showing). The outgoing picture keeps moving, but the time between tap and fade is longer for a shader that has never been shown. Mitigation: build on pad load for pads that hold a shader (a later improvement, not in these slices).
- Pi 5 and desktop are unmeasured; D123's higher effect rate doubles each generator's cost there.
- Measurement limits: node-01 is a single node on Automatic final pass. Sources 2 and 3 were used as stand-ins for the layer generators, with no mapping and no mix of them; the real fade has one more pass. Another session deployed to the node during the run.
- Tests of D73 that pin "a cut into a shader" will change; each change is a deliberate change of behaviour and is listed in slice 3, not a regression.
