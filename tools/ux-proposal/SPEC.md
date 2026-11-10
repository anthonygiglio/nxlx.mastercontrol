<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Everyday screens: audit and redesign spec

A proposal, not a decision. It covers pairing, the tab bar and navigation, Live, the Shaders page, Media, Mix (with the overlay and the mapping page), role views, and status and feedback. System and its module pages have their own audit and are built on the branch `system-index`; the Room screen is built on `room-scenes`. This document uses the shared patterns adopted for System (page shell, `.k` / `.hint` / `.state`, field, inline confirm, list row, Add, save rule, one switch, state chips, Advanced) and does not add parallel ones.

Line numbers are `pvj/web/app.js` and `pvj/web/shaders.js` at master `0793d2a` (2026-10-03). Widths are a 390 px phone. Everything was read from the code and the screenshots in `docs/images/ui`; nothing here was measured with people, and nothing was tried on a box.

The prototype (`index.html`, published at https://claude.ai/artifact/NwPErwtYzJb6WTaAj9UqKj) shows every screen below with a Current / Proposed switch.

## 1. Audit

### Step counts for the common jobs (today)

| Job | What it takes today | Proposed |
| --- | --- | --- |
| Play this clip now (it is on a pad) | Live, tap the pad: 1 tap. Rows 3 and 4 of the pads are below the first screenful, because Now playing, Vibes and Screen come first | 1 tap, pads in the first screenful |
| Play this clip now (not on a pad) | Media, scroll past Quick play, Slideshow and Upload (about one and a half screens), Play: 2 taps and a scroll. "Playing x" appears at the very bottom of the page, off screen | Media, Play: 2 taps, clips first, answer under the header |
| Start ambience | Live, Vibes: 1 tap if the owner switched the module on. If not, staff cannot | The same button, "Start Vibes". Also a Room scene |
| Black out, then bring it back | Scroll to the bottom of Live, Blackout; later scroll again and tap the same button, now called "Show": 2 taps and 2 scrolls. From Media or Mix: 3 taps, and nothing there says the screen is black | 2 taps, no scroll: docked button, "Blackout" then "Bring picture back". From any other screen the top bar says the screen is black and has "Bring back" |
| Upload a clip and put it on a pad | Media, scroll, Upload clips, choose, wait, Live, scroll, Edit pads, the pad, (set the ending first), find the file name among all files, Done editing: 7 taps in the panel, 2 tabs, 2 scrolls, and the name must be remembered | Media, Upload clips, choose, Put on a pad, the pad: 4 taps, one screen |
| What is playing, how long is left | Only on Live. File name, "00:01 / 03:20": subtract in your head. Nothing says whether it loops | Every screen: pad name, "2:06 left, then again" |
| Switch everything off at closing | Live, scroll, Stop, System, scroll about seven cards, All off: 3 taps and 2 long scrolls. Presenters can do it but must know where | Room, Close the room, confirm: 3 taps, no scroll |

### Top problems, with evidence

Navigation, roles, status and feedback

1. **The message line moves, and is often off screen.** `#msg` is under the pads on Live (332), the last element on Mix (500), the last element on Media after the clip list and USB cards (966), and at the top on System. On Media, "Playing x", an Info result and every error land below the fold. `say()` (72) only fills whichever `#msg` exists.
2. **State lives only on Live.** Blackout, pause, Vibes and the clip name are drawn by `patchLive` (384 to 416), which returns at once on other tabs (387). From Media you can play into a blacked-out screen and get "Playing x".
3. **Fade out leaves no trace in the panel.** `#fade` calls `/api/fadeout` and nothing else (335): no poll, no label change. The server ramps the picture to black and knows the level it reached (Vibes reads it: `vibes.py` 311, `level <= 0.5`), but `/api/status` sends only `mix.opacity` and `mix.blackout`, which stay as they were (`api.py` 277, 921 to 926). So the Opacity slider still reads 100% and nothing in the panel says the screen is dark. The way back, Fade in, is a small button in another card (319).
4. **Buttons whose words are their state.** "Show" for Blackout (405), "Resume" (404), "Test pattern off" (397), "Vibes is on. Stop" (`shaders.js` 21), "Loop: on", "Audio: mute" (494, 496), "Overlay is on. Turn off" (514), "Mapping on / Mapping off" (mapper). Loop and Audio do not even use the pressed style the others use.
5. **Three vocabularies for roles.** `view`, `live`, `full` in code and printed raw in Vitals (977: "My phone (live)"); "guest" and "presenter" on the pairing screen (168); "watch only", "play and mix" in remote support (232). A view-only device sees every control greyed (`disabled: !canLive`, 314 to 341) with no reason until it taps a pad (294).
6. **Every tab tap waits for six requests before anything changes** (`loadAll().then(render)`, 2162). On a weak connection the tab bar feels dead; there is no pressed state in between.
7. **Jargon in the best corner.** The pill reads "PI4 · 52°C · OK" (403).
8. **Small targets.** `.btn.small` is 34 px high (app.css) and is used for every Media row button, the transport's neighbours, Edit pads, Take snapshot, Refresh, and the shader rows.

Pairing

9. **The highlighted button is the owner's.** With no scanned code, "Pair with PIN" gets the accent (151) although most arrivals are staff with a code. Two forms share one screen, "Name for this device" sits between the code and its button (169 to 171), and both forms report errors in one line under everything (175).

Live

10. **The show buttons are not docked.** Fade out, Freeze, Stop, Blackout are in the normal flow after the pads and Edit pads (334 to 341). `docs/UI.md` says they are "always at the bottom"; on a phone that is the bottom of a two-screen scroll.
11. **Four ways to darken, three ways back.** Blackout (toggle, "Show"), Fade out (undone by Fade in elsewhere, or by Blackout twice), Stop (play something), Freeze (not dark at all, but sits between them).
12. **Editing pads has hidden state and a hidden order.** In edit mode the pads look the same (286 to 297); only the button says "Done editing" (331). In the sheet the ending select comes before the clip list, and tapping a clip saves at once with whatever ending is showing (425 to 434). The pad name is always the file name without its ending (426); it cannot be typed.
13. **Transport before pads.** Seek, Prev, Next, the 10 s buttons, Fade in, Test pattern and the Screen card take the first screenful (308 to 325).

Media

14. **The clip list is the fourth card** (937 to 945), under Quick play, Slideshow and Upload.
15. **Four equal buttons per clip** (Play, Info, Rename, Delete; 880 to 891), 34 px high, Delete beside Play. At 390 px the name gets what is left (the 10 rem rule in app.css moves the buttons to their own line; `docs/images/ui/media.png` predates that and shows names three letters wide).
16. **Browser dialogs.** Rename is `window.prompt` (886), Delete is `window.confirm` (890), Copy again is `window.confirm` (959). Delete does not say the clip is on a pad or on screen.
17. **Info is one sentence in the far message line** (`describeClip`, 794 to 804, shown through `say`, 883).
18. **A failed upload is a grey line.** "name: connection lost" or "name: failed (500)" in `.k` text (844, 849). No retry.
19. **No path from a clip to a pad.** Media has no "put on a pad"; the pad sheet lists bare file names (433).

Mix

20. **Performance and room setup are mixed.** Opacity, Speed and Volume sit with Size, Position X, Position Y (466 to 472), then Mirror (479), then three other cards, then Rotate (489). A presenter can rotate or shrink the room's picture.
21. **One Reset for everything** (498), no per-control reset, no undo. "Position X" has no unit or direction.
22. **Cards for switched-off modules** tell people to go elsewhere: "Off. Switch on "Projection mapper" under System > Modules (beta)." (546), the same for shaders (`shaders.js` 52). Presenters cannot.
23. **Mapping card:** a surface's Remove and a saved mapping's Delete act at once with no question; each surface row has four 34 px buttons (↑, ↓, Hide, Remove); Load replaces the current mapping without asking.

Shaders (today a card on Mix, `shaders.js` 46 to 152)

24. **One card does everything:** status line, a second Vibes button (95), up to three small buttons per shader (105 to 112), sliders in the middle of the list (113), the dwell time as a number of seconds with Save (116 to 143), drawing size and upload.
25. **Sliders have no range, default or reset** (79 to 87), and nothing says their values are not kept.
26. **A refused shader** says "Cannot be shown: ..." in the small grey line (101); a refused upload goes to the page's message line at the bottom of Mix (92, 133).

## 2. Navigation and shell

- **Tabs by role.** Owner: Room, Live, Mix, Media, System. Presenter: Room, Live, Media. Guest: Room, Live. Until the Room screen lands, the tabs are the same without Room. Tab height 48 px; five tabs at 390 px are 68 px each.
- **A tab changes at once** and loads its data after (render first, then `loadAll`).
- **Top bar on every screen** (not on the pairing screen): what is on screen (the pad name, or "Vibes: nxlx-aurora", or "Nothing playing"), under it the state and the time left ("Playing · 2:06 left, then again"; "Paused"; "Vibes · next in 1:40"). Tapping it opens Live. On the right a chip with the role (Guest, Presenter, Owner) that opens the role sheet. While the screen is black the bar turns light and reads "The screen is black", with a "Bring back" button on every screen but Live. Data: `GET /api/status` (the poll that exists). Needs one small addition: the status must also carry whether the picture is faded out; the server already knows the level (Vibes uses it), it only does not send it. See slice G.
- **Pages.** Shaders, and each System module, are pages, not tabs: back button ("‹ Back to Live"), h1, optional switch at the top right, one or two sentences, then `#msg`.
- **Message line.** One `#msg` directly under each screen's header; it stays in view while the screen scrolls (`position: sticky`). An action that can be undone carries an "Undo" button for 8 s. Errors use the same line, in the error style.
- **Role sheet** (tap the chip). Title "You are a presenter". Text: Guest "You can watch what is playing. You cannot change anything." Presenter "You can play clips, start Vibes, black out and switch projectors. Settings stay with the owner." Owner "You can do everything, including settings." Then for presenters and the owner the **Guest code** card (the same card is also in plain sight at the bottom of the Room screen, so nobody has to know that the chip can be tapped): hint "Lets someone watch what is playing on their own phone. They cannot change anything.", button "Make a guest code"; after it the code in large type, "Watch only. Works for 60 minutes.", "Cancel this code" with the inline confirm. Then, not for the owner, "Forget this phone" with the inline confirm "Forget this phone? You need a new code to come back." API: `POST /api/access/code {role: "view", minutes: 60}` and `POST /api/access/cancel {code}`; both are full-only today (`api.py` 2048), so the owner's decision that presenters may make guest codes needs the server to allow `role: "view"` at live level.
- **Watching only.** A guest sees no disabled controls. Each screen starts with one line: "You are watching only. To control the room, ask for a presenter code."

## 3. Pairing

Top to bottom: h1 "Join the room"; text "Scan the QR code on the big screen with your camera, or type the 6 digit code shown next to it."; label "Code"; one large field (example `123456`); button "Join" (accent, 56 px); the error line under the button; hint "No internet needed. This phone is remembered until someone removes it."; at the bottom the button "I am the owner (use the PIN)", which opens: label "Owner PIN", hint "Four digits. A new box shows it on its own screen; on a running box, run sudo pvj-pin.", four boxes, "Pair as owner", its own error line.

- Scanned code: the text becomes "Code read from the QR code. Tap Join." and the field is filled.
- No device name is asked. The name defaults to "Phone" and can be changed later by the owner under People and codes.
- Errors: fewer than 6 digits "The code has 6 digits. It is on the big screen, next to the QR code."; refused (403) "That code is wrong or has expired. Ask the person in charge for a new one."; throttled, add "Try again in N seconds."; wrong PIN "Wrong PIN. It changes every time the box starts."; no connection "Cannot reach the box. Are you on the same Wi-Fi?"
- After joining, the role sheet opens once.
- API: `POST /api/pair {pin, name}` as today (a code or the PIN), `GET /api/hello`, the `#code=` and `#pin=` fragments as today. Ids kept: `#joincode`, `#joinbtn`, `#pairbtn`, `#scannednote`.

## 4. Live

Top to bottom:

1. h1 "Live", then `#msg`.
2. **On screen now** card: label "On screen now" and a chip (Active while a picture is showing, otherwise Off; only the five shared chips are used); the title in 20 px (the pad's name); a state line ("Playing", "Paused", "Idle", "Blackout: the screen is black", "Faded out: the screen is black"); "Clip loop-a.mkv" when the pad's name differs from the file; a progress bar; the time left in large digits ("2:06 left, then again", "0:12 left", "picture", "next in 1:40"), "of 3:20" on the right. For presenters and the owner a row: "Pause" (becomes "Resume"), "Next one" (while Vibes runs), "Stop". Empty: "Nothing is playing and the screen is black. Tap a pad, or start Vibes."
3. **Vibes**: one 56 px button, "Start Vibes" (becomes "Stop Vibes", accent). Under it the link "Shaders and controls ›" (also shown to guests). Module off: nothing for staff; for the owner the line "Vibes is switched off." and a button "Set up Vibes", which opens the Shaders page.
4. In edit mode a banner: "Editing pads. Tap a pad to change it." with "Done".
5. Banks (three buttons), then the pads, three across, 80 px high. Each pad: its number and ending in small type ("03 · loops", "01 · once", "05 · once, holds"), then its name. The playing pad is lit. Presenters and guests see only pads that hold a clip; the owner also sees empty pads as "+ Add a clip", which open the pad sheet directly. No clips on a bank: "No clips on this bank yet. The owner puts clips on pads."
6. Owner: "Edit pads".
7. **More controls** (a closed `<details>`): hint "Jump in the clip, volume, test pattern and a snapshot of the screen."; "Back 10 s", "Forward 10 s", "From the start"; Volume slider; the switch "Test pattern" with the state "Colour bars, for lining up a projector"; "Take a snapshot" with the hint "One picture of what the box is showing. It pauses the picture for a moment." Prev and Next appear here only while a list plays.
8. **Docked above the tabs** (presenters and the owner): "Fade out" (becomes "Fade in") and "Blackout" (becomes "Bring picture back", inverted colours). Both 56 px. While Blackout is on, Fade is disabled.

Pad sheet: title "Pad 7 on Bank A"; label "Clip" and the list of clips (the chosen one ticked); label "Name" with the hint "What the pad says. Staff see this, not the file name."; label "At the end" with "Loop until something else plays", "Play once, then black", "Play once, hold the last picture"; "Save pad" (disabled until a clip is chosen); "Empty this pad" (with Undo in the message line) and "Cancel".

Messages: "Playing Intro." / "Playing Intro, but the screen is still black." with "Bring picture back" / "Stopped. The screen is black." with "Undo" / "The picture is back." / "Pad 7 now plays Friday loop." Errors come from the server's `error` text; no connection shows the existing banner "Connection lost? The box keeps playing. Reconnecting...".

API (all exist): `GET /api/status`, `GET /api/pads`, `POST /api/play {pad: [bank, index]}`, `POST /api/control {action: pause | stop | seek | seek_to | prev | next | volume}`, `POST /api/fadeout {seconds: 2}`, `POST /api/fadein {seconds: 2}`, `POST /api/blackout {on}`, `POST /api/testpattern {on}`, `GET /api/preview.jpg`, `POST /api/vibes {on}` and `{next: true}`, `POST /api/pads {bank, index, label, file, ending}`. Ids kept: `#black`, `#fade`, `#stop`, `#freeze`, `#vibes`, `#seek`, `#testpattern`, `#previewbtn`, `#padending`.

## 5. Shaders (a page of its own)

**Where it sits.** A page, not a tab. It opens from "Shaders and controls ›" under the Vibes button on Live, from the "Shaders and Vibes" row in System, and from the same row on Mix; all three land on this page, and Back returns to where you came from. Why not a tab: five tabs already fill a phone; staff need only the Vibes button; and the page does not exist when the module is off or on a Pi 3, so a tab would come and go. It replaces the shaders card on Mix and the planned System > Vibes settings page.

Shell: "‹ Back to Live"; h1 "Shaders"; for the owner the module's switch at the top right; "Moving pictures the box draws by itself. Vibes plays them one after another, for as long as you like."; `#msg`.

1. **On screen now** card: chip (Active, Hidden while the screen is black, Off); the shader's name, or "No shader on screen"; the state "Vibes · next one in 2:10", or "One shader, until you play something else", or "Intro is playing. Starting Vibes takes its place.", or "Nothing is playing."; while the screen is black the hint "The screen is black (Blackout or Fade out). The shaders go on changing unseen until you bring the picture back." Presenters and the owner: "Start Vibes" / "Stop Vibes" (56 px) and, while it runs, "Next one".
2. **Controls** card (only while a shader is on screen): the shader's name; one slider per number input with its label, the value as a small reset button ("1.4 · reset", drawn as a chip with a 44 px touch area), and the hint "0.2 to 2, normally 1"; after letting go the label shows "On screen" for 2 s, not "Saved", on purpose, because nothing is saved; a line for inputs without a slider ("Glow colour keeps the shader's own value. Only number inputs have a slider."); "Reset all"; the hint "The picture changes when you let go of a slider. These values are not saved: next time the shader starts from its own values, and Vibes picks new ones each round." For the owner a marked block: "Proposed, not built: keep these values as your own version of the shader. Needs an addition to the box software." with a disabled "Save as my version". Guests see the sliders disabled.
3. **Shaders** list, one row each (the ten bundled, then uploads): name; state "Light work", "Medium work" or "Heavy work", plus "uploaded"; a chip "Active" on the one on screen; a problem line "The GPU refused it: line 42: ..." when the player refused it; one main button "Play"; for the owner a tick "In Vibes" under the name (saves on tap, shows "Saved"); "More" opens the description, the names of its sliders, the note "Cost is a rough count of work per pixel, not a measurement.", and for uploads "Remove" with the inline confirm "Remove sunrise? The file is deleted from the box." Bundled shaders say "Comes with the box, so it cannot be removed." Presenters and guests read "in Vibes" or "not in Vibes" in the state line.
4. **Vibes settings**, owner: select "Each one stays" (30 seconds, 1, 3, 5, 10, 30 minutes; saves on tap; when a MIDI knob or the API has set another value, 10 to 3600 s, the select gains that value as an extra first option, for example "47 seconds", so it never shows a wrong one); switch "Change a little each round" with "Speed, sizes and colours shift slightly"; **Advanced** (closed): select "Picture detail" (360, 540, 720, 1080 lines) with "Fewer lines is lighter work for the box. Choose fewer if the picture stutters. Speed on this board has not been measured yet."; "+ Add a shader" with the hint "An ISF file (.fs) that draws from nothing (a generator). Shaders that need a picture or sound are refused, with the reason."; a refusal appears under that button, in the server's words ("blur-filter.fs was not added: input inputImage is a picture: only generator shaders are supported, not filters that need an image."). Presenters see the two values as plain lines and "Only the owner changes these."
5. **Other ways to start**, owner: rows that open MIDI controller ("A knob or pad: Vibes on and off, next one, how long each stays"), DMX ("A lighting desk: channel 9 stops, starts and skips"), At power-up, Weekly schedule; each with its state chip.

States: module off, owner: chip Off, "While this is off nothing is drawn, and the Vibes button is not on Live. Your shaders and settings are kept.", one big "Switch on". Module off, others: "Shaders are switched off. Only the owner can switch them on." Nothing in the rotation: Start Vibes is disabled and the line under it reads "Nothing is in the Vibes rotation. Tick "In Vibes" on at least one shader below." Every shader refused: the server's "Vibes ended: the player refused every shader" in `#msg`. Box busy or player not running: the server's error in `#msg`, the buttons stay as they were. Not offered on this board (Pi 3): the System row says so and the page is not reachable.

API (all exist): `GET /api/shaders` (list with `inputs`, `cost`, `source`, `vibes`, `error`; `playing` with `values`; `vibes.running`, `vibes.next_in`; `config.dwell`, `vary`, `height`; `limits`; `render.heights`), `POST /api/vibes {on}` and `{next: true}`, `POST /api/shaders/play {id, values}`, `POST /api/shaders {action: "vibes", id, on}`, `{action: "config", dwell, vary, height}`, `{action: "upload", name, source}`, `{action: "delete", id}`, `POST /api/modules/shaders {enabled}`. Backend additions: none for the page itself. Two optional ones, each its own later slice: (a) "Save as my version" needs somewhere to keep values per shader (for example `{action: "values", id, values}` stored in settings, read back as the shader's start values); (b) Light, Medium, Heavy is today a free-text `cost` written by the shader's author, so the page maps "low", "medium", "high" at the start of that text and shows "Not stated" otherwise. Ids kept: `#shadercard` moves to the page's root, `#vibesdwell` (now a select), `#shadersave` goes away (the browser test uses it, see slices).

## 6. Media

Top to bottom:

1. h1 "Media" and, for the owner, "Upload clips" (accent). `#msg`.
2. Upload cards, one per file while there is something to say. Uploading: "Uploading friday_loop.mp4: 48%", the bar, "182 MB. Keep this page open.", "Cancel". Done: "friday_loop.mp4 is on the box." with "Put on a pad" (accent), "Play", "Done". Failed: "big_show.mp4 did not upload: the connection dropped at 36%. Nothing was saved, so it is safe to try again." with "Try again" and "Dismiss". Refused by the server: its reason, for example "big_show.mp4 did not upload: not enough free space (1.2 GB free)."
3. Label "Find a clip", a search field (example "loop").
4. The clips, one row each: name; state "3:20 · 164 MB · Bank A, pad 3"; chip "Active" on the one on screen; problem line "May stutter on this box" when the box's advice says so; one main button "Play". "More" opens under the row: Format, Sound, On a pad; the advice in a sentence, or "Should play smoothly on this box."; and for the owner "Put on a pad", "Rename" (a form in place: label "New name", "Save changes" disabled until changed, errors under it: "Another clip is already called x."), "Remove" with the inline confirm "Remove loop-a.mkv? The file is deleted from the box. Bank A, pad 3 will be empty. It is on screen right now."
5. Owner: "84.4 GB free on the box. One file can be up to 8 GB."
6. "USB drive NXLX-USB": hint "Plays straight from the drive. Copy a clip to keep it after the drive is gone."; rows with "Play", and under More "Copy to the box"; while copying the state reads "Copying trailer.mp4: 40%". The autostart warning stays ("Plugging in a USB drive starts it, even during a show.").
7. "More ways to play" (closed `<details>` each): "Play all clips" (hint "Every video in the folder, one after another."; "Again and again", "Once", "Shuffled"; hint about 01_, 02_ names), "Slideshow (3 pictures)" (labels "Each picture", "After the last"; "Start slideshow"), and "Live input" when the box has one.

"Put on a pad" sheet: title "Put friday_loop.mp4 on a pad"; hint "Tap a pad. A pad that is taken is replaced, and you can undo it."; the three banks; the twelve pads (name, or "Free"). One tap saves and the message line says "Friday loop is on pad 7 of Bank A." with "Undo".

Empty: "No clips yet." and "Clips are the videos and pictures the box plays. Tap Upload clips, or plug in a USB drive." (owner) or "The owner adds clips." Search with no hit: "No clip has "x" in its name."

API (all exist): `GET /api/media`, `POST /api/play {file}`, `{usb}`, `{preset, shuffle}`, `{slideshow}`, `{capture}`, `POST /api/media/upload?name=` (the cancel is the request's abort), `POST /api/media/info {name}` (asked when More opens), `POST /api/media/rename {name, new}`, `POST /api/media/delete {name}`, `POST /api/media/import {usb, replace}` and `/cancel`, `GET /api/media/import`, `GET /api/inputs`, `POST /api/pads`. Ids kept: `#uploadbtn`, `#filepick`, `#playall` and friends inside the fold, `#importline`.

## 7. Mix (owner only)

1. h1 "Mix", `#msg`.
2. "While playing" card: sliders "Brightness" (today Opacity), "Speed", "Volume", each with its value as a small reset button (44 px touch area), and "Saved" for 2 s after letting go; the switch "Sound" (state "On" or "Muted").
3. "Clip change" card: "Cut" or "Dip to black"; with Dip the choice of "0.5 s", "1 s", "2 s", "5 s" under "How long the dip to black takes"; with Cut the hint "The next clip appears at once." (Crossfade is not shown until it exists.)
4. "Shaders": one row, "Shaders and Vibes", with its chip and state, opening the Shaders page.
5. "Layers": the row "Overlay picture" with its switch, chip and "logo.png, on top of the video" (choosing the picture: a select above the switch when more than one PNG exists; none: "Upload a PNG on Media to use it here. Make it transparent where the video should show."); the row "Projection mapping" with the module's switch, its chip and state, opening the mapping page.
6. "Picture setup" (a closed `<details>` whose summary is the current state: "100% size · centred · not turned · not mirrored"): hint "Set once for the room. It stays for every clip."; sliders "Size", "Left or right", "Up or down"; "Turn" with 0°, 90°, 180°, 270°; switches "Mirror left to right" ("For rear projection") and "Mirror upside down" ("For a ceiling mount or a mirror rig"); "Reset picture setup" with Undo.

Loop leaves Mix: each pad has its own ending, and a clip played from Media loops.

Mapping page (the same page from Mix and from System): shell with the module's switch; switch "Show the mapping" (state "Mapping is on", "Preparing the mapped picture...", "Problem: ..."); switch "Edit on the display" (hint "Shows the outlines on the projector while you line things up. Switch it off when you are done; editing is heavier work."); the canvas and the nudge buttons as they are, at 44 px; the surfaces as list rows (name, "Grid 3x2, on top", one main button "Choose", More: Rename, Bring forward, Send back, Hide, Remove with the inline confirm "Remove Back wall? Its corners are lost."); "+ Add a surface" opening a form (Kind: Quad, Triangle, Grid; Name; "Add"); "Saved mappings" as list rows (main button "Load", with the confirm "Load Main stage? It replaces the mapping on screen."; More: Remove) and "+ Add a saved mapping" (label "Name", "Add"). Empty: "No surfaces yet." and "A surface is a part of the picture that you pull onto a wall, a screen or an object. Add one, then drag its corners." with the Add form open. The prototype shows only this empty state.

API (all exist): `POST /api/control {action: opacity | speed | volume | mute | size | position | position_y | rotate | flip_h | flip_v | reset}`, `POST /api/mix {transition, duration}`, `GET` and `POST /api/overlay {file, on}`, `GET` and `POST /api/mapper {action: ...}`. Picture setup for the owner only needs the server to ask for full access on size, position, rotate and flip (today live).

## 8. Implementation slices

Each is one pull request against the real panel. **Safe** changes no behaviour and no navigation. **Sign-off** should be agreed in the prototype first.

Shared helpers: `origin/system-index` already has `sysPage(id, title, blurb, opts)` (the page shell), `flipSwitch`, `confirmRow(question, yesText, noText, onYes, control)`, `openSys` and `sysBack`. The slices below reuse those. The prototype's own `listRow` and `form` are stand-ins; if `system-index` has no shared list row and add form when it merges, slice D adds them for both.

| # | Slice | Kind | Size | Code | Browser test and manual |
| --- | --- | --- | --- | --- | --- |
| 0 | **System index and module pages** (the owner's first priority): built on `system-index`, specified in the System audit, not repeated here. A, D, F and J build on its helpers | Sign-off (given for the direction) | L | `system`, `sysIndex`, `sysPage`, every card | its own |
| A | Message line under each header, sticky; Undo support in `say()` | Safe | S | `say`, `live`, `mix`, `media` (move `#msg`), `.msg` | `#msg` keeps its id; add a check that it is in view after a Media action |
| B | Live words: time left, pad name in Now playing, "Bring picture back", "Pause", role names Guest, Presenter, Owner, the "watching only" line in place of greyed controls | Safe | S | `patchLive`, `live`, `padButton`, Vitals (977) | line 92 waits for `#black` to read "Show": change the word there; manual section 4 |
| C | Pairing: code first with the accent, errors under each button, no name field, PIN behind "I am the owner" | Safe, except the fold (sign-off) | S | `connect` | ids kept; screenshots `connect.png` |
| D | Media rows: one Play and More, details as lines, Rename and Remove in place (no `window.prompt`, no `window.confirm`), clips first, find box, folds | Sign-off (order); the row is safe | M | `media`, `describeClip`, shared `listRow`, `confirmRow`, `form` from `system-index` | tests that click Rename or Delete answer the in-page question; `media.png`, manual section 3 |
| E | Upload cards with Put on a pad, Play, Try again; the Put on a pad sheet | Sign-off | S | `media`, `uploadFile` | new checks; no server change |
| F | **The Shaders page** | Sign-off | M | `shaders.js`: `card` becomes `page`, `liveRow` gains the link; `app.js`: a page state in `render`, the Mix row, the System row | lines 520 and 524 wait for "Vibes is on" in `#vibes` and `#shaderline`: change to "Stop Vibes" and the new state line; `#shadersave`, `#vibesdwell` steps change to the select; `pvj/SHADERS.md` "Using it", manual section 5; **no backend change** (API list in section 5) |
| G | Docked Blackout and Fade; the top bar on every screen; the status says when the picture is faded out | Sign-off | M | `render`, `live`, `patchLive`; `pvj/api.py`: add the fader's level (or a `faded` boolean from it) to the `mix` part of `/api/status`, with a unit test. The server already has the level | `#black`, `#fade` keep ids; `live.png` |
| H | Live order: pads before More controls, test pattern as a switch, empty pads hidden from staff, pad sheet with Name and Save, edit banner | Sign-off | M | `live`, `sheet`, `padButton` | "Edit pads" steps (74, 106) then use Save pad; `#padending` kept |
| I | Mix in groups; switches for Sound and Mirror; per-control reset; Loop removed; picture setup for the owner only | Sign-off | M | `mix`, `slider`, `overlayCard`; `pvj/api.py` role for size, position, rotate, flip | `#mo`, `#fliph`, `#flipv` keep ids; `mix.png`, `mix-mirror.png` |
| J | Mapping page in the shared patterns (rows, More, confirms, Add forms) | Sign-off | M | `mapperCard` | `#mapon`, `#mapadd-*`, `#mapsave` and friends: keep ids where the control survives |
| K | Tabs by role, role sheet, presenters make guest codes. **Open question for the owner:** under these tabs a presenter loses Mix, so can no longer change brightness (opacity), speed or the clip change, which a presenter can do today; volume moves to Live. Keep that, or give presenters the "While playing" card | Sign-off (the codes are decided) | M | `render`; `pvj/api.py`: `/api/access/code` allows `role: "view"` at live level, with a test | guest and presenter contexts in the test check their tabs |
| L | "Save as my version" for shader values | Sign-off, later | S | `pvj/shaders.py` (store values per shader), the Controls card | new unit test; `pvj/SHADERS.md` |

Order: 0 first. A, B, C are safe and independent of it. F can go as soon as the page shell from `system-index` is merged. D, E, G, H, I, J in any order after A. K last, with Room.
