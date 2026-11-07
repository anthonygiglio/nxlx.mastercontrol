<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel: proposals, ranked

Step 6 of the panel design brief (2026-10-20). What could be better in the panel, ranked: the owner's decisions first, then by what each gives an operator for what it costs. `pvj/PANEL.md` says what the panel is; this file says what to change. Each item names the evidence, the work and the risk, so it can become one pull request. Written 2026-11-07, after the owner's decisions of 2026-11-06:

- Mastercontrol is a control centre that VJs can use, **not an instrument**. "Performing on Play" went too far.
- The wide layouts are fine, but they must use the whole screen.
- The device is the node (D121, #202 and #203).

The Figma file "nxlx.mastercontrol Signal UI kit" shows proposal 1 at 390, 820 and 1440 (page 2, the "00" frames; prototype flows "0.").

## Decided by the owner (build next)

### 1. Now, the control centre, comes first

**What.** A new area, Now, ahead of Play, Shape, Room and Setup, and the screen the panel opens on. It shows what is on and what needs you:
- **Needs you:** each problem with the button that fixes it or the page that does (a projector that does not answer, low storage, a helper not running), taken from Health.
- **On the screens:** each source, what it plays and which surfaces show it; Fade out, Next clip, Stop, Test pattern.
- **The room:** scenes; a one-line state for each projector; All on and All off, which asks first; the walls.
- **The node:** the player and renderer, load, the clock and Link, sound in, network, temperature, remote support.
- **Today:** who is connected, the next schedule entries, what happened lately.

On the laptop these are four columns across the whole width; on the tablet two; on the phone one, in that order. Play keeps the pads, one tap away.

**Why.** The owner's words: a control centre, not an instrument. Today the panel opens on Play > Pads, and the state of the room and the node is spread over Health, Room, Projectors and Setup.

**Work.** One area built from routes that already exist (`/api/status`, `/api/health`, `/api/projectors`, `/api/room`, `/api/sources`, `/api/schedule`, `/api/devices`, `/api/tempo`). Nothing new on the server. The area colour, the rail item and the phone tab are new. A Guest sees Now read-only.

**Risk.** Low. It adds a screen and changes which screen opens first. The inventory test (`tests/ui/inventory.js`) gains a row.

### 2. The title bar stays at the top while a screen scrolls

**What.** The title bar of every screen stays in view while its content scrolls, and switching screens opens the next one at its top.

**Why.** In the prototype the title went out of view after a tab change (the owner, 2026-11-06). The panel itself should be checked at 390 and 820 with a long screen (Setup's index, the Mapping card).

**Work.** CSS (`position: sticky` on the title bar, the same top offset for the area's tabs), plus a check in the browser test. **Risk.** Low.

### 3. Spread the wide layouts out

**What.** From 1200 px, use the whole width: Now in four columns, and the desks with no empty column. Today Play's desk leaves Shaders off below 1700 px.

**Why.** The owner: the multi-column laptop design "is okay, but make better use of all that screen real estate. Spread it out."

**Work.** CSS grid on the desks and the new area. **Risk.** Low to medium: the container queries are shared by every page, so check the 1200, 1440 and 1700 lines in the sandbox.

## Found in the inventory and the sandbox (ranked)

### 4. Ask before the destructive actions that do not ask yet

- **Where:** a mapping surface's Remove and a saved mapping's Delete act at once (PANEL.md section 11). A single wall's Off does not ask, while All off and the Projectors page do.
- **Why:** D50 asks for the question in place for every Remove. A projector switched off must cool for about a minute before it can come back.
- **Work:** `confirmRow` at three call sites. **Risk:** low.

### 5. The box clock gets a place on the panel

- **Where:** the clock (D116) has routes but no control. A tap comes only from a MIDI action or OSC.
- **What:** show the tempo, its source (tap, Link, sound) and Tap on Now's node column. The beat matters for running a room as much as for playing.
- **Work:** one row and one button (`POST /api/tempo/tap`, Operator). **Risk:** low.

### 6. The mapping display follows the look

- **Where:** the display's colours are written in the script: `#ffd800`, `#00ccff`, `#ff2878`, `#808080` and `#111`. A light look or the Signal look still draws them.
- **Work:** read them from the CSS tokens. **Risk:** low.

### 7. No missing glyphs

- **Where:** ⏮ and ⏭ are not in Archivo, so the strip shows empty boxes at tablet and laptop widths.
- **What:** draw them as inline SVG, as the other icons are, or use the words Previous and Next.
- **Risk:** low.

### 8. 1440 on Windows is 1425

- **Where:** a classic scrollbar on Windows takes 15 px, so a 1440 window gives the panel 1425 px. The strip then keeps More, where a Mac at 1440 shows every control.
- **What:** set the strip's line at 1400, or reserve the scrollbar's room with `scrollbar-gutter: stable` and measure.
- **Risk:** low.

### 9. Performing stays one tap away, not first

- **What:** Play keeps everything it has. Only its place changes, and the Vibes button and the transitions card stay on Play rather than on Now.
- **Why:** this is the part of the owner's "too far" that is not proposal 1: make sure nothing of Play's performing tools moves onto the control screens.
- **Risk:** none. It is a rule for the work above, not a change of its own.

## For the tools

### 10. The sandbox starts the player in every scenario

- **Where:** in the idle scenarios the player is never started (dev mode starts mpv on the first play), so the pill says NO PLAYER and Health shows a problem that a real node would not have.
- **Work:** start mpv when the box starts, in `tools/sandbox/box.py`. **Risk:** low.

### 11. Masks reach "Combining" in the sandbox

- **Where:** with no screen size when headless, the masks scenario never shows Combining.
- **Work:** the fake screen report gives a size. **Risk:** low.

## Not proposed

- A/B decks (#174) and other mixing on the control panel: closer to an instrument. Held until the owner decides.
- New shader variety on the panel: on hold since 2026-10-16, pending the renderer question.
