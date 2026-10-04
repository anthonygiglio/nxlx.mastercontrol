<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Room, Live, Setup: a fresh panel

A proposal, not a decision. The prototype is `index.html` in this folder (open it in a browser; it holds a fake box and sends nothing). Screenshots of the key screens at 390x844 and 1280x800 are in `shots/`. Nothing here was tried with people, on a box, or on a real projector.

## 1. The idea in one paragraph

Today's panel is organised by what the software has (Live, Mix, Media, System). This one is organised by **why someone is holding the phone**. There are three jobs, and each gets one screen with one shape:

- **Room**: "make the room look right". The walls of the room are drawn as a small plan; each wall says what it shows ("On · Box: Vibes: nxlx-drift", "Warming up · 00:29, then Box", "No answer"). Under it, one-tap **scenes**. This is the whole panel for staff and guests.
- **Live**: "play". Now playing, banks, pads, one fader that moves whatever you pick, and four quiet show keys. Built for a dark room and one thumb.
- **Setup**: "prepare the box". Calm pages, one task at a time, grouped like the owner's wireframes, with a desk layout (sidebar and card grids) on a tablet or laptop.

What is new compared with today and with the earlier proposal: the room plan with honest per-wall state; scenes as the staff's main control; one fader with selectable targets instead of a page of sliders; latched show keys that never rename themselves; a hold for Stop and a Lock for the pads; every tap showing "on its way" until the box confirms; add-ons that do not exist on everyday screens until switched on; Wi-Fi changes with a visible countdown and Keep.

## 2. What I took from the owner's wireframes, and where I departed

The 17 boards in `reference-wireframes/project/` steered this design. Their direction is kept; the owner's later feedback and the brief's constraints win where they disagree.

Taken:

- **Two contexts made explicit.** The boards split "performer view (phone, at the gig)" from "setup and admin (desktop or tablet, before the gig)". Here that is Room and Live on the phone, and Setup with a desk layout from 700 px up.
- **The desk sidebar and its groups**: Play (Room, Live, Library), then Room (Walls and scenes, Projectors), Sources and output (Inputs, Display and sound, Mapper, Wall and sync), Automate (Schedule and startup, Control), Box (Network, People and codes, System, Add-ons). The version, board and status sit at the bottom of the sidebar, as on the boards.
- **The phone Live screen**: a status pill (board, output mode, temperature, OK), a Now playing card labelled "Now playing · Output 1" with a progress bar, "00:19 / 00:32" on the left and "Next: ..." on the right, bank buttons including a live input bank ("Live in"), and a 3x4 pad grid with mono numbers.
- **The paper-and-ink look**: light ground `#f4f3ef`, ink `#1c1c1c` with 1.5 px borders, white cards, one orange accent `#c2410c` for what is active, small mono uppercase labels, bold sans headings. This is the Light theme, exactly the `light.json` tokens, so nothing new is invented; Dark stage, Night red and High contrast keep the same shapes with their own tokens.
- **Dense, orderly card grids on desk pages** (System: vitals with bars, updates, backup; Control: OSC, MIDI, DMX side by side; Schedule: "when the box powers on" next to the timetable). Setup pages use the same grid.
- **Network with the wired show network first**, then own hotspot, then join a venue Wi-Fi.
- **Schedule and startup on one page** ("When the box powers on" list plus the timetable).
- **Modules as a table with a type tag** (Core, Beta, Not built) and a switch.
- **Connect**: a dashed box with the QR code, Wi-Fi name and address, then a large code field.
- **The Coverage board's promise** that every old feature has a home: section 10 below does the same for today's panel.

Departed, and why:

- **Fade out, Freeze and Blackout at the bottom of Live** were big and loud on the boards; the owner now finds them too prominent. Here they are four equal, 44 px, outline keys (Fade, Freeze, Blackout, Stop) under the mixer. They only fill with the accent while latched. Blackout is no longer a black slab.
- **Mix as its own phone tab** became one fader on Live with targets above it (Bright, Speed, Vol, and for the owner Size, Pos X, Pos Y), and a "More" sheet for transitions, the Fade time, the test pattern and, for the owner only, rotate, flip and overlay. A performer changes one value at a time, and should not leave the pads to do it.
- **Features that are not built are not shown as working**: NDI and Resolume or MadMapper inputs, Presenter slides and PDF, screen sharing and VNC, camera filters, custom scripts, crossfade, update channels (Stable, Beta, Nightly), "roll back" from the panel, an event log, the clock display and countdown, Mapper circles and soft edges, layout import. The Presenter board has no screen here. Inputs says "NDI is not built yet"; the More sheet says "Crossfade is not built: it needs a second player"; Add-ons lists NDI and Dante as "Not built".
- **IBM Plex** cannot load on the box (no internet, strict CSP). System fonts carry the same feel: `system-ui` for text, `ui-monospace` (SF Mono, Menlo, Consolas, Liberation Mono) for labels and numbers.
- **The boards have no staff.** They predate D36. The Room screen is added for staff and guests, and becomes their whole panel.
- **The status pill** shows the board and temperature only to the owner; staff and guests see "Box OK", "Slow link" or "No link". On a narrow phone the output mode is dropped from the pill so the screen title still fits.
- **"Services" with ports (VNC, file manager)** on the Network board are left out: those services do not exist, and OSC, MIDI and DMX live on Control.

## 3. Information architecture

| Screen | Who sees it | What lives there | Why there |
| --- | --- | --- | --- |
| Join | anyone not paired | QR box (Wi-Fi, address), 6 digit code, "I am the owner, use the PIN" | Most arrivals are staff and guests with a code; the PIN is the exception |
| Room | everyone | room plan with each wall's state; what the box plays; Now playing; scenes; Sound (quieter, louder) for staff | The room is what staff are responsible for; one tap per intention |
| Room > wall sheet | everyone (buttons for staff) | On, Off (second tap), the wall's labelled sources (Box, Console), picture mute, sound mute, model and lamp hours | Rare per-wall fixes stay one level down, so scenes stay the main path |
| Live | staff/presenter, owner | Now playing, lock, banks (A, B, C, Live in, Vibes), pads, mixer, show keys | Everything a performer needs without scrolling on a 390x844 phone |
| Now sheet (tap Now playing anywhere) | everyone | position slider, from start, back 10 s, on 10 s, snapshot on request | Transport is occasional; it should not push pads below the fold |
| More sheet (Live) | staff/presenter, owner | Cut or Dip to black, Fade time, test pattern; owner: flip, rotate, overlay | Settings you set before a set, not during a phrase |
| Setup | owner | home (status plus every area with a one-line state), then one page per area | Rare, calm, guided work |
| This device sheet (role pill) | everyone | what this role can do, theme for this device, Forget this device | A theme is a per-device choice (a phone in a dark room, a tablet at the bar) |
| The box sheet (status pill) | everyone | link quality in words, board, output, temperature, address | Answers "is it me or the box?" |

Setup areas (phone list and desk sidebar use the same groups): Library; Walls and scenes; Projectors; Inputs; Display and sound; Mapper; Wall and sync; Schedule and startup; Control; Network; People and codes; System; Add-ons. An area whose add-on is off is not listed (Mapper, Wall and sync, Network, Walls and scenes, Projectors), except inside Add-ons.

## 4. Navigation model

- **Phone (under 700 px of panel width)**: a bottom bar with only the screens this role has: Room, Live, Setup (owner); Room, Live (staff and presenter); none for a guest, who has only Room. Each is a 48 px button with a word, no icon puzzles. Setup pages have a "‹ Setup" back button; sheets slide up from the bottom and close with Close or a tap on the shade.
- **Tablet and laptop (700 px and wider)**: the bottom bar becomes the wireframes' sidebar. Staff see Play: Room, Live. The owner also sees every Setup area by group, so a desk session never needs the Setup home. Pills move to the sidebar foot.
- **Live** becomes two columns from 900 px: pads left, mixer and keys right.
- **Landing**: guests and staff land on Room; the owner lands where they last were (the prototype starts on Room).
- A tab change renders at once from what the panel already knows, then refreshes; it never waits for the box.

## 5. Interaction rules

**What applies on tap.** Pads, banks, scenes without "asks first", wall On, sources, mutes, Blackout, Fade, Freeze, mixer targets, the Vibes pad, Next shader, Quieter and Louder. Sliders send while dragging (throttled) and once more on release.

**What asks first, in place (never a browser dialog).**

| Action | How it asks |
| --- | --- |
| A scene marked "asks first" (any scene that switches a wall off or plays over the room, such as Close room, Film night, Painting only) | The card turns red and reads "Tap again: Close room / Or wait, and nothing happens"; 6 seconds |
| Wall Off | The Off button reads "Tap again: Off"; 6 seconds |
| Stop on Live | Hold for 0.6 s (a fill runs across the key); a keyboard press asks for a second press instead |
| Delete a clip, remove a schedule entry | The button reads "Delete?" or "Remove?"; 6 seconds; schedule removal also offers Undo |
| Switch off Projector control, Room scenes, Shaders and Vibes | The switch turns red, the line reads "Tap again to switch off. Staff lose these buttons." |
| Factory reset | "Tap again to reset the box" |
| Network and Wi-Fi changes | Try it, then a countdown card with **Keep** and **Go back now**; nobody keeping it means the box goes back by itself (D10, D41) |

Put on a pad, emptying a pad, removing a schedule entry: done at once, with **Undo** in the message for 8 seconds.

**Latched states.** Blackout, Fade, Freeze and the test pattern are latching keys. The label never changes ("Blackout" stays "Blackout"); while it is on, the key fills with the accent (`aria-pressed="true"`), and the Now playing card shows a small lit tag ("Blackout", "Faded out", "Frozen", "Test pattern") so the state is visible from Room and from the Now sheet too. Tap again to release. Fade shows its level as a thin bar along the key's bottom edge while it ramps. The same rule covers every two-state control: Picture mute, Sound mute, Flip H, mapping and so on are pressed or not, never "Unmute".

**Accidental taps.** Stop needs a hold. A padlock next to Now playing locks the pads, mixer and keys (for a phone in a pocket, or a tablet on a stand in a crowd); "Hold to unlock" takes 0.6 s. Empty pads are disabled for staff. The owner edits a pad with a long press (0.65 s), never a mode switch that changes what a tap does.

**Time.** Always `mm:ss / mm:ss` (`00:19 / 03:20`; `1:34:00` style hours for a film), in tabular mono figures, with a progress bar. Right of the time: "Loops", "Then black", "Then holds last frame", or for Vibes "Next: nxlx-ember". Countdowns are the same format: "Warming up · 00:29, then Box", the Wi-Fi Keep countdown `01:59`. No phrases like "then again".

**Feedback.** One message area, floating above the bottom bar (or at the top while a sheet is open), so it is always in view. Plain sentences: "Playing Tunnel.", "Playing Intro. Blackout is still on.", "Open room: started. Projectors take about 30 seconds." Errors use the error colour and the server's own words. A scene's progress stays under the scenes as a short report per wall: "Open room: on its way · Main wall: warming up, 00:29, then Box · Painting wall: no answer".

**Slow and unreachable.** A tap marks its control with a dashed outline until the box answers; nothing is drawn as done before the box says so. A second tap on a control that is still on its way is ignored, not queued. After about 2 s per answer the status pill reads "Slow link". After 14 s without an answer: a banner "The box has not answered for 14 s. You see its last known state. Taps are not sent.", the content dims, the pill reads "No link" (dashed), and taps are refused with a message. Nothing is queued to fire later. Projector state is always the last check's ("checked every 45 s" is printed beside the plan).

## 6. Roles

| | Guest (view) | Staff and presenter (live) | Owner (full) |
| --- | --- | --- | --- |
| Screens | Room | Room, Live | Room, Live, Setup |
| Room | plan, Now playing, scene names as state; a line "You are watching. Nothing here changes the room." No disabled buttons to wonder about | scenes, wall sheets, Sound | the same |
| Live | not shown | pads (no editing), Live in, Vibes, Bright, Speed, Vol, show keys, More (transitions, Fade time, test pattern) | plus Size, Pos X, Pos Y, flip, rotate, overlay, long press to edit pads, "+ Add" on empty pads |
| Pills | "Box OK", "Guest" | "Box OK", "Staff" | "Pi 4 · 1080p60 · 52°C · OK", "Owner" |

"Staff" is the word on screen for the live role. A presenter code and a staff code are the same role today; see the open questions.

## 7. How add-ons appear

- An add-on that is off is **on no everyday screen**: no greyed card, no "switch this on in System" line for staff. The Vibes bank appears only with Shaders and Vibes on; Room without Projector control and Room scenes shows only Now playing and, for the owner, "Open Add-ons".
- Setup lists only areas that exist. Setup > Add-ons lists everything: name, one line on what it does, a type tag (Core, Beta, Not built), a switch, and once on, "In: Room, and Setup > Projectors" so the owner knows where it went. Switching on says the same in the message.
- Inside a page, a section that depends on an off add-on is a dashed card with one button, "Switch on in Add-ons" (Streams on Inputs, MIDI and DMX on Control, the timetable on Schedule and startup).
- Room scenes needs Projector control: switching Room on switches Projector control on; switching Projector control off takes Room off. Both ask first, because staff lose buttons.

## 8. Visual language

- **Type**: `system-ui` for text, `ui-monospace` for labels, numbers and times. Scale 11 / 14 / 16 / 20 / 28 / 40 px. Labels are 11 px mono uppercase with 0.08 em tracking (the wireframes' `.k`). Headings bold. Times and values use tabular figures.
- **Spacing**: 4, 8, 12, 16, 24 px. Phone gutter 16 px, desk 24 px.
- **Shape**: 1.5 px borders in the ink token (`--ln`), 6 px radius, white cards (`--cd`) on the ground (`--bg`). Dashed borders mean "a place, not a control" (the room plan, the QR box, an add-on that is off) or "on its way" (the pending outline).
- **Colour**: the seven theme tokens (`bg`, `cd`, `fg`, `ln`, `mu`, `ac`, `on`) exactly as in `pvj/themes.d`, plus four derived tokens per theme: `ok`, `wa` (warning), `bd` (bad and asks-first) and `hi` (a quiet fill). The accent means only "this is active or chosen". Red (`bd`) means only "this is about to do something you cannot take back" or "a fault". Night red keeps every colour in reds and relies on shape and words (filled, outlined, dashed, "No answer") rather than hue.
- **Icons**: a few inline SVG line icons drawn in the page (scenes: sun, waves, film, gamepad, frame, moon; projector; padlock; chevrons). Every icon has a word beside it. No icon fonts, no external assets.
- **Targets**: at least 44 x 44 px everywhere (pads 64 px tall on a phone, 88 px on a desk; scenes 92 px; bottom bar 48 px). The prototype's test checks this on every key screen.
- **Motion**: only for state (a pending outline pulse, a warming stripe, the Stop hold fill), off under `prefers-reduced-motion`.

## 9. The pieces of each journey (as in the prototype)

1. **Staff opens the room**: Room, Open room. Both walls go to "Warming up · 00:29, then Box", the box starts Vibes, the report under the scenes says "on its way", then "done".
2. **Staff closes the room**: Close room, "Tap again: Close room", tap. Walls go to "Cooling down", the box stops.
3. **VJ performs**: Live in Night red. Pads, banks, the Vibes bank (Vibes pad, ten shaders, Next shader), the fader with its targets, More, Blackout latched, Fade with its level, Stop by hold, Lock.
4. **Guest watches**: Room with no controls and a one-line explanation.
5. **Join**: QR box, 6 digit code, the owner PIN as a second path.
6. **Add media**: Library, Upload (progress, "keep this page open"), Put on a pad, pick the pad, Undo offered.
7. **Add a projector**: four steps: address and password, "Ask the projector" (model, class, inputs), name and wall, done; the new wall appears on Room.
8. **Wi-Fi**: Network, Join a Wi-Fi, Find, choose, password, Try it, countdown with Keep or Go back now; or Own hotspot with name and password and the instruction to keep it from the hotspot's own address.
9. **Schedule entry**: Schedule and startup, Add, time, days, what happens (a scene, Start Vibes, Stop, Blackout, Projectors off), Save; the week strip shows it.
10. **Switch an add-on on**: Add-ons, switch Network on, the message names where it is; Network appears in Setup.

## 10. What changes compared with today's panel (for planning)

Today: tabs Live, Mix, Media, System (`pvj/web/app.js`, `app.css`, `shaders.js`), plus the Room tab on the unmerged `room-scenes` branch.

| Today | In this design | Work |
| --- | --- | --- |
| Live tab: Now playing card with transport and Screen card above the pads; Fade out, Freeze, Stop, Blackout as big buttons after the pads | Live: compact Now playing (tap for transport and snapshot), banks, pads, mixer, four 44 px latching keys; Stop by hold; Lock | Rewrite `live()`, `patchLive()`; new Now sheet; hold and lock are client only |
| Blackout button text changes to "Show" | "Blackout" always, pressed state | Small; `#black` keeps its id for the browser test |
| Mix tab: six sliders, transition, mirror, rotate, loop, mute, reset, overlay, mapping and shaders cards | Fader with targets on Live; More sheet; geometry and overlay for the owner only; mapping moves to Setup > Mapper; shaders become the Vibes bank | `mix()` splits into the mixer and a sheet; shaders card becomes pads plus Setup settings |
| Vibes: a button on Live and a card on Mix | Vibes bank: an endless Vibes pad with `mm:ss / mm:ss`, one pad per shader, Next shader | Uses `/api/vibes`, `/api/shaders/play` as today |
| Live input card on Media | "Live in" bank pads (capture 720p and 1080p; saved streams when Streams is on) | Uses `/api/play {capture}` and stream ids as today |
| Media tab: quick play, slideshow, upload, clip list, USB | Setup > Library: clip list first (desk: codec, size, frame rate columns, as on the boards), Upload with Put on a pad, USB card, More ways to play | `window.prompt` and `window.confirm` for rename, delete and copy go; in-place confirms |
| Pad editing: Edit pads mode and a sheet | Long press a pad (owner); the sheet takes a name, the ending, then the clip; empty pads say "+ Add" | Pad name becomes typed, not only the file name |
| System tab: one long column of cards | Setup home plus a page per area, grouped as in the wireframes; desk sidebar | Mostly moving existing cards into pages; the `system-index` branch already started this |
| Projectors card in System | Room (walls) for daily use; Setup > Projectors with an add wizard | Needs `room-scenes` merged; the wizard is a front end to `POST /api/projectors {add}` and `identify` |
| Room tab (branch): scenes, a card per group, All on and All off with a second tap | Room: a plan of the walls with state, scenes with in-place confirm per scene, a wall sheet | Groups are shown as walls; needs a per-scene "asks first" flag and, optionally, each group's place on the plan |
| Autostart card and Schedule card | One page: When the box powers on, and the timetable with a week strip | Same APIs |
| Network card | How the box connects (wired first), then Join a Wi-Fi or Own hotspot, Try it, countdown, Keep, Go back now | Same APIs (`plan`, `apply`, `confirm`, `revert`, `scan`) |
| Modules card | Add-ons page with type tags and "In: where" | Same API |
| Access card | People and codes: staff code, guest code, show on the big screen, paired devices | Same APIs |
| Messages in a `#msg` line that moves per tab | One floating message with Undo where it applies | New small module |
| No visible "on its way" or offline state | Dashed pending outline per control, Slow link and No link pills, a banner and dimmed content when lost | Client side: track requests per control; count poll failures |
| Themes: four, chosen in System for everyone | Same four token files, chosen per device from the role pill; the Light theme matches the wireframes | Store the theme per device (local), keep the box default |

Server additions this design needs (small, each its own slice):

1. `/api/status` should say whether the picture is faded out (the server knows the level; the earlier proposal found it is not sent). The Fade key and the "Faded out" tag need it.
2. A per-scene flag "asks first" in the `room` settings section (read with a default, no schema change).
3. Optional: a place on the plan per group (top wall, right wall, left wall, a list) so the plan matches the real room; without it the first two groups are drawn top and right and the rest are a list, as in the prototype.
4. Optional: tell the panel which kind of live code a device joined with (staff or presenter), if the owner wants presenters to see Live and staff only Room (see question 1).

Nothing in this design needs a framework, a build step, external fonts or scripts, inline handlers or eval. The prototype follows the same rules: one file, `data-act` attributes with one delegated listener, `textContent` only.

## 11. Open questions for the owner

1. Should a staff code and a presenter code differ? Today both are the live role. One option: staff codes open Room only, presenter codes open Room and Live, owner decides when making the code.
2. Which scenes should ask first? The prototype asks for anything that switches a wall off or plays over the room (Film night, Painting only, Close room). Should Open room ask too?
3. Is one fader with targets right for live mixing, or do you want Bright always visible as its own fader?
4. Stop by hold (0.6 s): too slow, too fast, or should Stop leave the show keys and live in the Now sheet?
5. Should the Lock also lock Room on a wall tablet, with a staff PIN to unlock?
6. Should the owner land on Live or on Room?
7. The plan of the room: draw it from a fixed set (top, right, left walls), or let you place walls freely in Setup?
8. Do staff need volume on Room, or is volume part of each scene?
9. Do you want the board and temperature in the status pill on the phone, or only on the desk sidebar?
10. Light is the default here to match your wireframes; should the box default stay Dark stage for gigs and Light for setup on a laptop?
11. Which not-built items from the boards matter most next: NDI input, an event log on System, a clock or countdown display, or crossfade?

## 12. Notes on the prototype

- The right-hand panel (or "Roles, notes" on a phone) switches role, device size (phone, tablet, laptop), theme and the link (good, slow, lost), unplugs the painting wall projector, and runs ten journeys that set up the fake box and say what to tap.
- Notes per screen are kept in this browser's localStorage (key prefix `nxlx-fresh-ui-notes:`), read and written inside try and catch; a private window says it cannot keep them. "Show all notes" lists them for copying.
- Not drawn: the mapping editor, the wall and sync page, wired address modes, the pad sheet's file preview, editing scenes. Those pages say so.
- Checked with Playwright in Chromium at 390x844 and 1280x800 (phone, tablet and laptop frames, all four themes): no console errors, no horizontal overflow inside the panel, every enabled control at least 44 x 44 px.
