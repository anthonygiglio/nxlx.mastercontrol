<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel, as it is

An inventory of the control panel (`pvj/web`): every screen, card and control, who sees it, what it asks the box,
and what states it can be in. It is the reference for the design work of 2026-10-20 (the design sandbox in
`tools/sandbox/`, the Figma file "nxlx.mastercontrol Signal UI kit", and the proposals that follow). It describes; it
does not judge. What could be better goes in the proposals.

Taken from master at 06d1067 (the merge of #152, the mask list) by reading `app.js`, `room.js`, `shaders.js`,
`effects.js`, `app.css`, `pvj/policy.py`, the route table in `pvj/api.py`, `pvj/THEMES.md` and the decisions. Parts
marked **coming in D101** are on the branch `sources-surfaces` and not on master yet. Not checked in a browser while
writing: the control lists of `tests/ui/inventory.js` are the machine check of what each role can reach.

Keep it true: a change to a screen changes its section here in the same pull request.

## Contents

1. Who: the three roles and the other ways in
2. Widths: the real breakpoints, and the frames for Figma
3. The frame around every screen
4. The shared patterns
5. Play: Pads, Library, Shaders
6. Shape: Effect, Picture, Sound, Mapping
7. Room: Scenes, Walls, Guests
8. Setup: the index and its pages
9. Projectors (D99) in full
10. The mask list (D100) in full
11. The Mapping card in full
12. Coming in D101
13. Design tokens
14. Accessibility facts
15. Routes and the role each needs

## 1. Who

The box says each device's reach in one word (`GET /api/status`, field `reach`); the panel shows or hides controls
from it, and the box checks every request again (`pvj/policy.py`). Hiding is a courtesy, never the protection.

| Role (id) | Reach word | Who | In one line |
| --- | --- | --- | --- |
| Owner (`full`) | `owner` | paired with the PIN, a recovery code, an owner link or an owner code from a controller | everything |
| Operator (`live`) | `operator` | an operator code, an operator link, or a controller's join code set to Operator | runs the room day to day: plays, mixes, maps, sets up the room and the schedule, uploads and deletes clips, lets guests in, locks guest controls, removes guests and other operators. Not: network, screen output, updates, power, the PIN, OSC, DMX, NDI settings, HTTPS, backup, looks |
| Guest (`view`) | `guest` while guest controls are open, `watch` while they are locked | a guest code, a guest link, a controller's code | watches; while guest controls are open (the default, D80) also plays pads and clips, applies scenes, starts and stops Vibes, chooses a shader or a preset, steps, Stop, Blackout, Previous and Next, projectors on, and projectors off with a confirm |
| Remote support | `presenter` below full | a support code typed through the support tunnel (D29, D84) | what the session's level allows, minus a list the box keeps (no pairing, no codes, no PIN, no power off of a projector, no factory reset) |
| Controllers | none | MIDI, DMX, OSC, Room scenes, the schedule | held to the fixed list `LEGACY_LIVE` (what a presenter had before D80) |

What a Guest may send is a fixed list of request forms (`policy.GUEST`): play, control, blackout, vibes, shaders
play, step and preset, effects on, step and preset, projector, room group, room scene. Each is rate limited (10 in 10
seconds per device, 30 for all guests), and switching projectors off takes two requests (the box answers 409 with a
token the second one must carry, within 30 seconds).

Who lands where: an Operator or a Guest lands on Room > Scenes when the Room module is on (the first load or right
after pairing); the Owner lands on Play > Pads. Nobody already on a screen is moved.

## 2. Widths

The panel uses container queries, not the window's width: `app` is the whole panel and `ws` is the workspace beside
the rail. The breakpoints that change the layout:

| Width of the panel (`app`) | Navigation | What the areas look like |
| --- | --- | --- |
| under 380 | as under 600, tighter: the strip's buttons 13 px, smaller gaps | one screen at a time |
| under 600 (phone) | four area tabs at the foot (Play, Shape, Room, Setup), the open area's screens as a row of tabs under the title | one screen at a time; the strip shows Freeze, Stop, Blackout and More; More opens the rest in place |
| 600 to 1199 (tablet) | a rail at the left with one button per area; the open area's screens as tabs across the top | one screen at a time; the strip folds less as the width grows (below) |
| 1200 to 1699 (laptop) | rail; Play's tab row is "Pads · Library" plus Shaders; Shape's is "Effect · Picture · Sound" plus Mapping | **desks**: an area's screens stand side by side as columns (Play: Pads and Library; Shape: Effect, Picture and Sound; Room: Scenes, Walls and Guests); Setup shows its index as a column beside the open page; Mapping and Shaders take the whole width as a tab |
| 1700 and up (wide) | rail; Play has no tabs | Play's desk is Pads, Library and Shaders together |

Inside the workspace (`ws`), from 900 px: the pads go from 3 to 4 a row, a page with a lot on it uses two columns
(`grid2`), the Shaders page puts its library in a column of its own, and the Mapping card puts its tools beside the
display. From 1100 px some pages gain a third column (the Shaders page's sets and controllers).

The transport strip folds its controls into More by width, in this order of leaving: back and forward 10 s first, then
Fade, Speed, Loop, Previous and Next. Shown on one line from: Previous and Next 800, Loop 940, Speed 1080, Fade 1280,
back and forward 10 s 1440. More is gone from 1440. Freeze, Stop and Blackout are never folded.

Heights: under 600 and under 480 px of height the strip and the open More are held to part of the window and scroll
inside; a short window keeps the strip.

**Frames for Figma**, chosen from these breakpoints so each frame shows one layout and no frame sits on a line:

| Frame | Size | Why this size |
| --- | --- | --- |
| Phone | 390 x 844 | an iPhone 14 to 16; inside "under 600"; the strip at its most folded that staff see |
| Tablet | 820 x 1180 | an iPad Air in portrait; inside 600 to 1199, so the rail and tabs; the workspace stays under 900, one column of cards |
| Laptop | 1440 x 900 | the owner's M1 MacBook Air at its default scale; inside 1200 to 1699, so desks; the whole strip on one line with no More |
| Wide (optional) | 1728 x 1117 | an external monitor or a 16-inch MacBook Pro; the only layout where Play shows Pads, Library and Shaders together |

1280 x 800 is also inside the laptop range; it shows the strip with More still there (Fade folds at 1280, the 10 s
buttons at 1440). It is a useful check, not a frame of its own.

The widths are the panel's, not the window's. Measured in the design sandbox (D103) on Windows: Chrome draws a classic
15 px scrollbar inside a 1440 window, so the panel is 1425 wide and the strip still shows More. On a Mac the scrollbars
float over the page, so a 1440 window is a 1440 panel. A Figma frame has no scrollbar: 1440 there is the full strip.

## 3. The frame around every screen

Built once by `render()` and `chrome()` in `app.js`:

- **Pairing screen** (no device yet): "Connect to your box". A 6-digit field for a guest or operator code (`#joincode`)
  with "Join with code"; a name for the device (`#devname`); four PIN boxes with "Pair with PIN"; "I have a recovery
  code" (folded, D81; a 12-character field and "Pair with recovery code", or a link to https:// when the box takes owner
  codes over https only); "Locked out?" (a fold that names every way back in). Calls `POST /api/pair`; a wrong code or
  PIN is said in the message line, with "Try again in N seconds" when the box is holding off. A QR code puts the code or
  the PIN into the fields (`#code=` or `#pin=` in the address, removed at once). Through the support tunnel this screen is
  "Remote support" with one 9-character code field and Sign in (`POST /api/support/login`).
- **Title bar** (`#wshead`): the area's name as the title, one line of what the open screen is for (`#wsjob`), the brand
  "nxlx.mastercontrol", and the pill (`#pill`): the board, the temperature and "OK" or "No player".
- **Rail** (`#wsside`, from 600 px): Play, Shape, Room (when its module is on), Setup. An area's button returns to the
  screen it was left on (Setup: always its index).
- **Screen tabs** (`#wssub`): the open area's screens; on a desk, the desk's columns are one tab.
- **Area tabs** (`#wstabs`, under 600 px): the same four areas at the foot.
- **Message line** (`#msg`, role status): one per build; what a press did, or why it was refused (an error in the error
  colour).
- **Offline banner** (`#offline`): "Connection lost? The box keeps playing. Reconnecting..." after two failed requests in
  a row; gone with the next answer.
- **Support banner** (`#supportbanner`): while a remote support session is open: who is connected and the minutes left,
  with End session or Stop (Operator and up).
- **Transport strip** (`#wstp`, at the foot of every screen at every width). From left: what plays (`#np`, the clip,
  stream, shader, NDI source, test pattern or test tone, and the effect), "Clip N of M", the time; the place in the clip
  (`#seek`, a slider 0 to 1000, sends `seek_to` on release; never moved by the poll while a finger is on it); Speed
  (`#mv`, 0.25x to 2.00x, sent while dragging, at most every 80 ms; "1.00x · live" and held while a live source plays);
  Loop (on or off); Previous and Next (enabled only with a playlist of two or more); back 10 s and forward 10 s; Fade (the
  one fade button of D75: "Fade out", then "Fade in"; flashes while the picture goes down and while it is black from a
  fade, or stands inverted with a dashed edge for reduced motion); Freeze (Resume while paused); Stop; Blackout (Show
  while on, flashing). All go to `POST /api/control` except Fade (`/api/fade`) and Blackout (`/api/blackout`). Enabled
  for Operator and up; a Guest with open controls gets Previous, Next, Stop and Blackout only; a watching device gets
  none. The strip is built once and kept across redraws, so a finger on a slider is never cut off.

## 4. The shared patterns

Every page is built from these (D42, D43, D50, D52); the helpers are in `app.js` and handed to the other files.

| Pattern | Helper | What it is | States |
| --- | --- | --- | --- |
| Page shell | `sysPage` | Back (to the Setup index or where the page was opened from), the title, the page's one switch, a sentence of what it is for (`#sysblurb`), a state line (chip and words, `#sysstate`), the cards | Checking (the flag not read yet), On (the cards), Off ("Off. Your settings are kept while it is off." and, for the Owner, "Switch on ...") |
| The one switch | `toggle`, page switch `#sysswitch` | a real switch (`role="switch"`, `aria-checked`) with a noun for its label; a page switch is a list of steps (the module, then the feature's own flag) and shows On only when every step is on | off, on, busy (the request is out), refused (back to Off with the reason in the message line) |
| State chip | `showState`, `.chip` | a word in a box: Off, Set up, Ready, Active, Problem, Check; with one sentence beside it | the six words; none (nothing to say) |
| Question in place | `confirmRow` | the tapped control's row is replaced by the question, a danger button and a plain one; "no", or 8 seconds, puts the row back | asking, yes (the action runs), no or timed out (back as it was) |
| List row | `listRow` | a name, a sub line (address, maker), one state line, a red problem line, at most one main button, and More, which opens the rest in place, Remove last | closed, More open (one row at a time for the whole panel) |
| Add form | `addBlock` | "+ Add a ..." that opens the form in place; open by itself while the list is empty | closed, open, refused (the reason under its button) |
| Save bar | `saveBar` | "Save changes", disabled until something differs, "Not saved yet" beside it; the result under it and in the message line | clean (disabled), dirty, saving, saved, refused |
| Apply on tap | (each control) | a switch, a tick or a choice that does one safe thing is applied when tapped, with a brief "Saved" | idle, saved (2 s), refused |
| Feature off | `offNotice` | a choice that needs a feature that is off says so in place, with the switch itself | shown while that feature is off |
| Said in place | `sayAt` | a result beside the button as well as in the message line | none, said, error |
| Cursor kept | `keepCursor` | a card that is rebuilt gives the cursor back to the control that had it, and a field its text | |
| No redraw under a hand | `asking`, `inUse` | a card that polls waits while a question is open in it, a field has the cursor or a slider is held | |

Buttons: plain (outline), `on` (chosen: a block of the area's colour), `pri` (the primary action: filled like chosen),
`danger` (the problem colour), `small`, `big` (56 px), `back`, `linkish` (a text button), `segbtn` (a segment of a row of
choices). A pad is its own button (`.pad`). The pad editor is a sheet from the bottom (`.picker .sheet`, `role="dialog"`).

## 5. Play

Colour: `clips` (green in Signal). Screens: Pads, Library, and Shaders (when the Shaders and Vibes module is on).

### Pads (`#padsscreen`)

| Part | What it does | Server | States | Roles |
| --- | --- | --- | --- | --- |
| Guest card (`#guestcard`) or "locked" line | only on a box without the Room module: see Room > Scenes | | | Guest |
| Vibes row (`#vibesrow`) | the big Vibes button ("Start Vibes" / "Vibes is playing: name", "Tap to stop"); Previous and Next shader while a shader is on; the set to play when there are two or more; "Shaders ›" | `POST /api/vibes`, `POST /api/shaders/step`, `GET /api/shaders` | off, on, a shader without Vibes (Previous and Next shown) | Operator and up; a Guest with open controls gets the big button |
| Banks | one button per bank; the chosen one is `on` | none (local) | chosen, not | everyone |
| Pads (`#pads`) | 3 a row, 4 from 900 px; number, kind ("Shader", "NDI", "Render", or "Operator" for a guest) and label. A tap plays it | `POST /api/play {pad}` | empty (dashed), holds something, playing (`on`, `aria-pressed`), not available to this role (`na`, dotted, half seen), busy (an NDI source connecting), refused (dashed red, with `#padrefused` under the pads saying why) | Operator and up; a Guest with open controls (not NDI or render pads); watching: none |
| Edit pads / Done editing | in edit mode a tap opens the pad editor | | off, on | Operator and up |
| Pad editor (sheet) | "Pad N"; Clip, Shader, NDI source, Render cue; for a clip "When the clip ends" (Loop, Play once then black, Play once and hold) and the list of clips; for a shader the list and then its presets ("Its own start" or a preset); for NDI the sources allowed and the ones on the network, each with where it comes from (two senders with one name: not offered); for a render cue the node, a cue from its profile, the OSC address, the arguments and "Then show its NDI picture", Save the cue; Clear pad; Cancel | `POST /api/pads`, `GET /api/shaders`, `GET /api/ndi`, `GET /api/render` | per kind: loading, empty ("No clips in the media folder yet", "Shaders are off", "NDI is off", "No render nodes yet"), list, refused | Operator and up |
| Transition between clips (`#transitioncard`) | Cut, Dip to black, Crossfade, four wipes and four slides (two to a row), and Duration 0.5, 1, 2 or 5 s; applied on tap | `POST /api/mix` | chosen; fallback (the box does a dip instead, with its reason and "Try again", `#mixfallback`) | Operator and up (choices disabled for others) |
| Screen snapshot (`#previewcard`) | "Take snapshot": one picture of what the box shows, never repeated by itself (each one stalls playback for about a quarter of a second) | `GET /api/preview.jpg` | idle, taking, picture, nothing on the screen (409), no picture (error) | everyone |

### Library (`#libraryscreen`)

| Part | What it does | Server | States | Roles |
| --- | --- | --- | --- | --- |
| Refresh | reads the list again | `GET /api/media` | | everyone |
| Play the whole folder (`#quickplay`) | Play all, loop; Play all once; Shuffle all; Play by number (files named 01_..., Loop or Once) | `POST /api/play {preset}` | shown only with clips | Operator and up |
| Live input (`#inputcard`) | a USB capture stick or camera: the device, the picture size, "Show live input" | `GET /api/inputs`, `POST /api/play {capture}` | hidden with no device | Operator and up |
| Slideshow (`#slideshow`) | from the media folder or a USB drive; each picture for 0.1 s to 1 minute; then start again, keep the last or black; random order; "Start slideshow" | `POST /api/play {slideshow}` | shown only with pictures | Operator and up |
| Upload (`#uploadbtn`) | free space and the largest file; choose files; a progress bar per file; the result of each kept under it | `POST /api/media/upload` (the server's own route) | idle, uploading (bars), done, failed per file | Operator and up |
| The clips | a row per file: name and size; Play; Info (codec, size, rate, sound, length, advice); Rename (in place, no browser dialog); Delete (with the question in place) | `POST /api/play {file}`, `POST /api/media/info`, `/rename`, `/delete` | empty ("No clips yet..."), list | Play: Operator, and a Guest with open controls; Info: everyone; Rename and Delete: Operator and up |
| A USB drive (`.usb-drive`) | per drive: its files; Play from the drive; Copy to the box (or Copy again, with the question in place); a line while it copies, with Cancel | `POST /api/play {usb}`, `POST /api/media/import`, `GET /api/media/import`, `/import/cancel` | copying (percent), done, stopped with the reason | Play: Operator and up; Copy: Operator and up |

### Shaders (`#shaderpage`, the page "Shaders and Vibes")

A page of its own (it has the module switch for the Owner) shown as a column of Play. On a phone, top to bottom; from
900 px the library is a column of its own; from 1200 px the sets and controllers are a third column. Each card is drawn
by itself and never under a hand.

| Card | What it holds | Server | States |
| --- | --- | --- | --- |
| On screen now (`#shadernow`) | the shader's name, a chip (Vibes or Playing; Changing), the state line with the countdown to the next one, a progress bar while Vibes runs, the load (light, medium, heavy, with dropped frames a second and ms a frame), Picture detail (Automatic or a number of lines, the Owner's), Previous, Next, Stop | `GET /api/shaders` (every 3 s while a shader plays, 5 s otherwise; nothing while the column is hidden), `POST /api/vibes`, `/api/shaders/step`, `/api/shaders {action: config}` | nothing on screen, one shader, Vibes; load light, medium, heavy, unknown; changing |
| Controls for the shader (`#shadercontrols`) | Speed, then the shader's own inputs by type (sliders, switches, colour, a point as an x and y pad, choices), a MIDI button beside each for the Owner | `POST /api/shaders/values` (a value as it moves) | shown while a shader is on; a control held is never moved by the box |
| Presets (`#shaderpresets`) | the shader's presets to apply; save the values as a preset, rename, remove | `POST /api/shaders/preset`, `/api/shaders/presets` | none yet, list, applied |
| The library (`#shadercard`, "Shaders (N)") | filters by family and work; one compact row per shader with its class (light, medium, heavy), Play, and whether it is in the rotation | `POST /api/shaders/play`, `POST /api/shaders` | filtered, empty filter, refused shader |
| Vibes and its sets (`#vibessettings`) | how long each shader stays, the guard, the sets (add, rename, choose shaders, remove) | `POST /api/shaders` | one set (sets stay out of sight until there are two, D47), several |
| Controllers (`#shaderremote`) | the MIDI switch, the Vibes rows to teach, the lighting desk's channel | `GET /api/midi`, `POST /api/midi/learn`, `/api/midi/map` | MIDI off, on, learning (a countdown), learned |

Who: watching devices see the state only; a Guest with open controls may play a shader, step and apply a preset;
Operators get everything here except the module switch, MIDI teach and DMX (the Owner's).

## 6. Shape

Colour: `mix` (violet). Screens: Effect (when the Shaders module is on), Picture, Sound (when this device has the Sound
page), Mapping (when the mapping module is on).

### Effect (`#effectscreen`, the Effects card `#fxcard`, `effects.js`)

One effect over the picture (D55, D74): on and off, previous and next, the effect's load, Effect detail (Automatic or a
size, D58), its controls (Amount and its own inputs), its presets, the list with filters, and Upload an effect. Server:
`GET /api/effects`, `POST /api/effects`, `/effects/step`, `/effects/values`, `/effects/preset`, `/effects/presets`,
`/effects/config`, `/effects/library`. States: off, on, heavy (a warning), refused. A Guest with open controls may switch
an effect, step and apply a preset; Operators everything else; MIDI teach is the Owner's.

### Picture (`#picturescreen`)

| Part | What it does | Server | Roles |
| --- | --- | --- | --- |
| Opacity, Size, Position X, Position Y | four sliders (`#mo` 0 to 100 %, `#ms` 1 to 200 %, `#mp` and `#mpy` -100 to 100), sent while dragging at most every 80 ms and on release | `POST /api/control` | Operator and up |
| Mirror | Flip left-right (`#fliph`), Flip upside down (`#flipv`), pressed while on | `POST /api/control` | Operator and up |
| Overlay pictures (`#overlaycard`) | the mask list, section 10 | `GET`/`POST /api/overlay` | Operator and up |
| Rotate | 0, 90, 180, 270 degrees | `POST /api/control` | Operator and up |
| Test pattern | only here when the box has no Mapping screen | `POST /api/testpattern` | Operator and up |
| Reset mix | puts every picture setting back | `POST /api/control {reset}` | Operator and up |

### Sound (the page "Sound", a column of Shape)

Volume (`#mvol`, 0 to 130 %), Audio on or mute, and the Sound output card: where the sound comes out (Automatic or a
device), and a test tone left, right, both. Server: `POST /api/control` (volume, mute), `GET`/`POST /api/audio`,
`POST /api/testtone`. A Guest sees the volume and mute (disabled) without the page.

### Mapping (Shape > Mapping, the page "Projection mapping")

A tab of its own with the whole width. Section 11.

## 7. Room

Colour: `room` (yellow). There while the Room module is on. Screens: Scenes, Walls, and Guests (Operator and up, not
through remote support). Cards are `room.js`'s, put into the columns by `roomDesk()`. Staff press these, so in Signal
every button here is at least 56 px.

| Screen | Card | What it does | Server | States | Roles |
| --- | --- | --- | --- | --- | --- |
| Scenes | Guest card / locked line | a Guest with open controls: "You are a guest. Tap a scene, a pad or a clip...", Start and Stop ambience, a shader to Show, Next, Projectors on and off; locked: "The room is locked for a show: you can watch" | as below | open, locked | Guest |
| Scenes | Ambience (`#roomambience`) | the big button: "Start ambience" / "Ambience is playing: name", "Tap to stop"; "Next one"; the set; for the Owner a line when the saved picture detail is above the board's usual one, with its button | `POST /api/vibes`, `GET /api/shaders` | off, playing, starting (held for 5 s until the box agrees); module off: for the Owner "Ambience (Vibes) is switched off" and "Open Shaders and Vibes" | Operator and up; a watching device gets one line of state |
| Scenes | Scenes (`#roomscenes`) | one big button per scene; the one whose job runs is `on`; the job's result under them ("working", done, or the error) | `POST /api/room/scene`, `GET /api/room` (polled) | none yet, list, working, done, failed | Operator and up; a Guest with open controls (scenes not marked "Not for guests", and none that plays a stream) |
| Walls | a card per group (`.room-group`) | the group's state badge and a line; On and Off; Source (one button per named input, the chosen one `on`); Mute picture or Show picture; Mute sound or Sound on; the last result | `POST /api/room/group` | the badge words from the box (on, off, mixed, warming up, cooling down, no answer...) | Operator and up |
| Walls | Everything | All on; All off asks in place ("Turn off all N projectors? They need about a minute to cool..."), and a second tap in the first 600 ms is not taken | `POST /api/room/group {group: all}` | asking, working | Operator and up |
| Guests | Guest controls (`#guestlockcard`) | "Lock guest controls for a show" / "Open guest controls", with the line that says which | `POST /api/guests` | open (default), locked | Operator and up |
| Guests | Let someone in (`#roomletin`, a fold) | the guest code part of People and codes, opened on request | see Setup > People and codes | | Operator and up |

Setup > Room holds the set-up card ("Set up the room", Operator and up): groups (a name and its projectors) and scenes
(a name, per group the power, input, picture mute and sound mute, what the box plays, and "Not for guests"). Server
`POST /api/room`.

## 8. Setup

Colour: `system` (blue). The index (`#sysindex`) is rows in groups, each with a chip and a sentence worked out from the
row's GET answer (about fifteen requests when the index opens; Health asks again every 5 s). From 1200 px the index
stays as a column beside the open page. A row a role cannot use is not shown; a module that is not on this board, or
not built yet, is in a fold at the foot (Owner only).

| Row | Group | Shown to | Switch | Cards | State words (examples) |
| --- | --- | --- | --- | --- | --- |
| Health | top | everyone | | Health: power, temperature, the player, the helpers, each projector's warnings | "This box · all well"; Problem or Check with the worst line |
| Projectors | Everyday | Operator and up (module `projector`) | module, with a question when it goes off | Projectors (section 9) | No projectors added yet; X does not answer; 2 of 3 on |
| Room | Everyday | Operator and up | module | Set up the room | No groups or scenes yet; 2 groups, 4 scenes; Working: name |
| Schedule | Everyday | Operator and up | module and its flag (asks before switching on with entries) | Schedule: entries (time, days, what: a clip, stop, black, show, Vibes, a scene, projectors on or off), the box's clock | Next: Sat 18:00 Play x; Last run failed |
| Shaders and Vibes | Everyday | everyone | module (asks when a shader is on screen) | a column of Play (section 5) | Vibes is playing; N shaders in the rotation |
| People and codes | Everyday | Operator and up, never through support | | Let someone in; for the Owner also A code from a controller, Paired devices (with the link that does not expire and the PIN: Show, Copy, New PIN, Unblock joining), Guest controls, If every owner device is lost (recovery codes, the USB stick); for an Operator Guests and operators, Guest controls | N devices, a guest code |
| Sound | Everyday | Operator and up | | a column of Shape | Automatic: device |
| At power-up | Show tools | Operator and up | | what plays at power-up (off, a file, all, slideshow, a pad, USB, Vibes), after how long, loop, Test | The box waits for you; Plays x |
| Streams | Show tools | Operator and up (module `inputs-srt`) | module | saved SRT, RTSP and RTMP addresses, Play; adding is the Owner's | No streams saved yet; Playing: x |
| NDI input | Show tools | Operator and up (module `inputs-ndi`) | module (asks when an NDI picture is on) | Sources on the network, Where sound comes from, Addresses to ask, Which senders this box looks for, Sources that pads may hold; the settings are the Owner's | Showing: x; Waiting for x |
| Render nodes | Show tools | Operator and up | | Render nodes (add, the Owner's), Send a cue | Showing x; No render nodes yet |
| Sound reactivity | Show tools | Operator and up | | Sound reactivity (device, gain: the Owner's), A shader input that follows the sound | Listening: -18 dB |
| NDI discovery server | Show tools | Owner, never through support | module | The discovery server this box runs, Where it listens, What this is | Listening on address:port |
| Projection mapping | Show tools | Operator and up (module `mapper`) | module (asks when the mapping is on screen) | Shape > Mapping (section 11) | On the screen; Being placed |
| Boxes in step | Show tools | Operator and up (module `wall`) | module (asks when it leads or follows) | Boxes in step: lead or follow, the group, a tile of a video wall; changing it is the Owner's | Leads group x; Following x |
| MIDI controller | Show tools | Operator and up (module `control-midi`) | module and its flag | the controllers plugged in (layouts, lights, levels), Mappings (learn, map, remove) | 2 controllers: names; No controller plugged in |
| DMX lighting desk | Show tools | Owner (module `control-dmx`) | module and its flag | Lighting desk (protocol, universe, start channel, networks, Save changes), Channels | Frames arriving; Listening, nothing received yet |
| OSC | Show tools | Owner | its flag (the paired-device lock comes on with the first switch-on, D83) | OSC (port, who may send, the key), Send state back | Open: anyone on this network can control the box |
| Network | This box | Owner (module `network`) | module | Network: wired and Wi-Fi, automatic or fixed, a direct cable, a hotspot; every change waits for a confirm and goes back by itself | A change is waiting for your confirmation |
| Screen output | This box | Owner | | Screen output (Automatic or a mode, tried and kept or put back, D90), Renderer (classic or New, D95) | 1920 x 1080 at 60 Hz (Automatic) |
| Updates | This box | Owner | | Updates: from a USB stick or an upload, signed only | Version x |
| Remote support | This box | Owner (and the support session itself) | its flag; the card shows while off | Remote support: state, start a session (how long, what support may do), the code to read, extend, stop; Advanced: the server, this box's key, recent sessions and what support did | Session open, 40 min left |
| Secure connection | This box | Owner | | State, Certificate, this device | |
| Backup and reset | This box | Owner | | Settings file (export with or without passwords, import), Diagnostics, then "Danger": Factory reset | |
| Look | This box | Owner | | Appearance: the looks as small pictures, an accent where the look allows one; Your own themes (add, remove) | the look's name |
| About and power | This box | everyone | | This box (versions, storage, screens, the clock); for the Owner Restart and power (Restart player asks first); for everyone else This phone with Log out | board · 52°C · 12.1 GB free |

## 9. Projectors (D99)

Setup > Projectors, card `#projcard`. The list is read every 5 seconds while the page is open (the box asks each
projector every 10 seconds while someone looks, 45 otherwise), and never drawn over a field being typed in or an open
question.

- **The line** (`#projline`): "N projectors, M on. The box asks each one how it is every few seconds." With none:
  "No projectors yet. On the projector, open its network menu and switch PJLink on. Then add it here."
- **All on, All off** (`#projallon`, `#projalloff`, with two or more): each asks in place first. All off is not shown
  through remote support.
- **A row per projector** (`listRow`, class `proj-entry`, `data-power` for a style that draws a chip): the name; a sub
  line (maker and model, the address and port, "password set", the control when not PJLink); the state line (On, Off,
  Warming up, Cooling down; the input; what is muted; frozen; lamp hours; then "Error: ..." or "Warning: ..." from its
  own report); a red problem line when it does not answer ("Not answering at address. Is it plugged in at the wall, and
  is PJLink switched on in its network menu?"); a note (the input it will switch to when ready, up to 90 seconds; the
  result of the last action).
- **The one power button** (primary): "Checking..." (disabled), "Try again" (no answer), "Warming up..." and "Cooling
  down..." (disabled), "Turn off" (asks first: "It needs about a minute to cool before it can come on again."), "Turn
  on". Through remote support an On projector shows "On (switched off in the room)", disabled.
- **While it is on**: Mute (black) / Unmute; with Epson control also Freeze / Unfreeze; then the picture block: Brightness
  (a slider 0 to 255, sent on release; "the projector keeps its nearest step"), Eco light (a switch), Colour mode (Epson's
  numbers, "(in use)", "(not tried yet)"; a refused one is not offered again). Through remote support the picture block
  is one line saying it is changed in the room. If the picture settings could not be read: "Picture settings not read: ...".
- **Input**: a list of the inputs by their names ("Choose..." until one is in use), applied on choosing; with no inputs
  yet "Inputs appear once the projector is on..." and Read inputs; with Epson control only, a line saying inputs need
  PJLink.
- **More**: Blank the picture / Show the picture, Mute the sound / Unmute the sound, Name the inputs (Owner), Check now,
  Read details again, Test (Owner: asks PJLink and Epson control at once and says what answered and which control is
  best), Edit (Owner), Remove (Owner, asks first).
- **Edit** (in place of the row): Name, Address, Port, PJLink password (empty keeps it; "Remove the password" as a switch
  when one is set), Control (PJLink; PJLink and Epson control; Epson control only); Save changes (disabled until
  something differs), Cancel. A new address says that the stored password and the input names are kept.
- **Name the inputs** (a panel under the row): a field per input (up to 24 characters) with Show beside it, Save names,
  Close. Naming never switches the projector.
- **Add a projector** (the add form, Owner; beside the list from 900 px): Name, Address, Port (4352), PJLink password,
  Control, Test (picks the best control), Add, Cancel.

Server: `GET /api/projectors` (view); `POST /api/projector {id, action, value?, input?}` (live; a Guest's form for on,
off with the confirm, and the input); `POST /api/projectors {add | edit | remove | label | test}` (full). Who: see the
table in D99. Not pressed against a real projector yet (rows PX1 to PX8 in `tools/DEVICE-TESTING.md`).

## 10. The mask list (D100)

Shape > Picture, card `#overlaycard`, "Overlay pictures (logos or masks over the video)".

- **Status line** (`#overlaystatus`, role status): "Loading...", "Combining...", "1 mask on", "3 masks on", "The overlay is
  off", "No masks yet", "Nothing is shown", or the box's error ("m2.png is not in the media folder any more (left out)").
  While combining, the card asks again every 0.7 s.
- **The whole switch** (`#overlaytoggle`, a button with `aria-pressed`): "Show overlay" / "Overlay is on. Turn off"; while
  the request is out "Combining..." or "Turning off...". With no mask yet it adds the picture chosen below and shows it.
- **A row per mask** (`.maskrow`): "1. name.png" ("(not in the Library)" when missing); On or Off and a real switch
  (`.maskswitch`); up and down (disabled at the ends); Remove (asks in place: "Take name.png out of the masks? The
  picture stays in the Library.").
- **Add**: a list of the Library's PNGs not yet in the list, and Add ("Add and show" for the first). With 8 masks: "Up to 8
  masks. Remove one to add another." With no PNG at all: "Upload a PNG (transparent where the video should show) in the
  Library (under Play) to use it here."
- With two or more: "Later masks are drawn over earlier ones. They stay in place on the screen, over a mapping too."

Server: `GET /api/overlay`; `POST /api/overlay` with `{add}`, `{remove}`, `{mask, mask_on}`, `{mask, move}`, `{on}`; the
answer is the whole state with `status.state` combining, on, off or error. Operator and up; a Guest sees nothing of it.
No thumbnails (there is no thumbnail route for media).

## 11. The Mapping card

Shape > Mapping, `#mapcard` (a desk of its own: `.mapdesk`). With the module off: "Projection mapping (beta). Off.
Switch it on under Setup, Projection mapping (beta)."

- **The stage** (`#mapstage`): the status (`#mapstatus`): "Mapping is off", "Preparing the mapped picture...", "Mapping is
  on", "Editing on the display" or "Problem: ...", then the screen's size and the number of surfaces. While the picture is
  built the card asks again every 1.2 s.
- **The display** (`#mapcanvas`, a canvas at the screen's aspect, focusable): the surfaces as outlines (the chosen one
  yellow with its corners, others cyan, hidden ones grey, names written in); in Picture corners only the chosen surface's
  part of the picture. Drag a corner (sent at most every 120 ms while editing on the display, else only on release; one
  request at a time, the newest next); tap inside a surface to choose it; the arrow keys nudge.
- **Under it**: Mapping on / off (`#mapon`), Edit on the display (`#mapedit`, outlines on the real screen), Screen corners
  or Picture corners (`#maptarget-*`), Test pattern with its line; a warning when the mapping was made on a screen of
  another shape (D90); the masks' line; for the Owner "Controllers may adjust the mapping" (a switch, D75), for an
  Operator a line saying how it stands. When a controller is in mapping mode, a line says so with its step and when it
  ends.
- **The tools** (`#maptools`, beside the display from 900 px): Surfaces (+ Quad, + Triangle, + Grid, disabled at 16);
  the list, the first on top, each with the name to choose it, forward and back, Hide or Show, Remove; with none "No
  surface yet. Add one, then drag its corners on the display above."
- **The chosen surface** (`#mapsurface`): which corner of how many (or the whole surface); left, up, down, right with a
  step of 1, 10 or 50 px; Next corner; Move the whole surface; the name and Rename; for a grid its columns and rows (1 to
  8) and Set grid.
- **Saved mappings** (N of the limit): the list with Load and Delete; a name and Save.

Server: `GET /api/mapper` and `POST /api/mapper {action: on | edit | place | move | add | remove | order | show |
rename | grid | load | delete | save | source (D101)}`, each answering the whole state; `POST /api/mapper/remote`
(Owner). Answers are numbered so an old one is never drawn over a newer one. Operator and up use it; anyone else gets
the stage with the status and Test pattern only.

Facts noted for the proposals: a surface's Remove and a saved mapping's Delete act at once, without the question in
place that D50 asks for every Remove; the display's colours are written in the script (`#ffd800`, `#00ccff`,
`#ff2878`, `#808080`, `#111`), not taken from the look.

## 12. Coming in D101

On the branch `sources-surfaces` (seven commits on 2026-10-20, not pushed when this was written). Its panel part is 98
lines of `app.js`; nothing of the stylesheet changes.

- **Play to** (`#playtorow`, beside the pads): a list "Source 1 (the player)", "Source 2" ... and a hint. With another source
  chosen, a pad's clip plays there (`POST /api/play {pad, source}`), "Source N plays x."; a shader, NDI or render pad is
  refused in plain words. Shown to Operator and up, only when the box has two sources or more (the New renderer).
- **Shows** (`#mapsource`, in the chosen surface's panel of the Mapping card): which source the surface shows; sent at once
  (`POST /api/mapper {action: source, id, source}`). A source the box does not run now stays chosen and is said
  ("Source 3 needs the New renderer ...: until then this surface shows Source 1."). The surface list says ", Source N".
- **Sources** (`#mapsources`, a card under the Mapping card): a row per source, "Source N: what plays" (Source 1 is the
  player); for Sources 2 and up a clip list, Play and Stop. Read every 4 s (`GET /api/sources`), `POST /api/sources
  {source, action: stop}`. On the classic player: "Sources 2 and up need the New renderer (Setup > Renderer)." A warning
  when a source over 720 lines plays while two others do. Operator and up.

This file is updated to match once that pull request merges.

## 13. Design tokens

The colours come from the theme (`theme.css`, written by the server from a theme file in `pvj/themes.d/`); the rest are
in `app.css`. A fresh box uses the look **Darkstage** (`dark-stage`, the default style); the redesign's direction is
**Signal** (D54, D57) in two themes, Signal (dark room) and Signal light.

### Colour

| CSS name | Figma name | Darkstage | Signal | Signal light |
| --- | --- | --- | --- | --- |
| `--bg` | colour/page | #121214 | #0b0b0d | #f2f0ea |
| `--cd` | colour/surface | #1c1c20 | #1a1a1f | #ffffff |
| `--fg` | colour/text | #f2f1ec | #f5f5f0 | #0b0b0d |
| `--ln` | colour/line | #4a4a54 | #f5f5f0 | #0b0b0d |
| `--mu` | colour/muted | #a9a8a3 | #b8b8c0 | #4a4a52 |
| `--ac` | colour/accent | #f59e0b | #ffd60a (or the area's) | #ffd60a (or the area's) |
| `--on` | colour/on colour | #1a1300 | #0b0b0d | #0b0b0d |
| `--ar-room` | area/room | | #ffd60a | #ffd60a |
| `--ar-shaders` | area/shaders | | #ff4fa3 | #ff4fa3 |
| `--ar-clips` | area/live | | #3ddc97 | #3ddc97 |
| `--ar-mix` | area/mix | | #b78cff | #b78cff |
| `--ar-system` | area/system | | #7aa2ff | #7aa2ff |
| `--st-off` | state/off | | #3a3a42 | #d6d6dc |
| `--st-setup` | state/setup | | #ff9500 | #ff9500 |
| `--st-active` | state/active | | #00e0ff | #00e0ff |
| `--st-problem` | state/problem | | #ff3b30 | #ff3b30 |
| `--st-error` | state/error text | | #ff3b30 | #b00020 |

Which area colour a screen wears (`data-area` on the root): Play `clips`, Shape `mix`, Room `room`, Setup `system`, and
the Shaders screen `shaders` while it is the one screen on the page. In Signal `--ac` and `--on` follow the area, and
`--ink` is the area colour where it reads as a thin line, else the text colour. The default look's Problem chip is a
fixed #b00020 with white text. A theme may also set an accent the owner chooses (Darkstage's six: #f59e0b, #c2410c,
#22d3ee, #e879f9, #a3e635, #ffffff).

### Type

The default look: the system face, 16 px body, labels (`.k`) 11 px capitals in a monospace face, card titles 15 px.
Signal (from `pvj/THEMES.md` and the style block of `app.css`):

| Use | Size | Weight and case | Face |
| --- | --- | --- | --- |
| Screen title (phone) | 44 | Black, capitals, line 1.05 | Archivo |
| Page title (a block of the area's colour) | 30 | Black, capitals | Archivo |
| On screen now (laptop) | 48 | Black, capitals | Archivo |
| Row name | 18 to 20 | Black or Bold, capitals | Archivo |
| Primary action | 18, 0.04 em | Black, capitals | Archivo |
| Button, slider name | 16, 0.04 em (small 14) | Black, capitals | Archivo |
| Section label, tab | 14, 0.04 em | Black, capitals | Archivo |
| Chip, the switch's words | 13, 0.04 em | Black, capitals | Archivo |
| Body | 16, line 1.35 | Regular | Archivo |
| Label, hint, state line | 14 | SemiBold / Regular | Archivo |
| Number beside a slider | 20 | Medium | JetBrains Mono |
| Numbers, times, addresses, codes | 13 to 16 | Medium | JetBrains Mono |

### Shape, size, space, motion

| Token | Signal | Default look | Theme key (range) |
| --- | --- | --- | --- |
| Corner of a control, of a panel | 0 | 6 px (pads 6, chips 10, the sheet 12 at the top) | `radius_control`, `radius_panel` (0 to 24) |
| Rule (every outline) | 3 px | 1.5 px | `border_width` (0 to 4) |
| Control height | 44 px | 44 (small 34) | `control_height` (44 to 72) |
| Large control (staff, tabs) | 56 px; Room's On, Off and sources 64; scenes 72 | 56 | `control_height_large` (56 to 72) |
| Pad | at least 80 px tall | 80 | |
| Switch | 52 x 32 drawn in a 52 x 44 target | the same | |
| Density | 1 (0.75 compact, 1.3 roomy) | | `density` |
| Gaps used | 2, 4, 6, 8, 10, 12, 16 px (8 most); a screen's padding 16 (phone) and 24 by 28 (from 900 px) | | |
| Shadows | none for depth; inset rules mark a chosen row (3 px at the left) and a chosen pad (2 px); a theme may give the primary button and the open tab a shadow | the same | `primary`, `tabs` |
| Motion | the fade and Blackout flash: 0.5 s steps, twice a second; a progress bar 0.2 s; a slider's knob 0.12 s; Signal's message toast 8 s. With reduced motion asked: no flash (inverted with a dashed edge instead) | the same | |

## 14. Accessibility facts

Facts only; judgements are for the proposals.

- **Everywhere**: the language is set (`lang="en"`); the message line, the status lines of the cards and the confirm row
  are announced (`role="status"`, `role="alert"`); the workspace says it is busy while the box is asked (`aria-busy`);
  focus is a 3 px ring in the accent (Signal: in `--ink`) with a 2 px offset, on buttons, fields, links, lists, folds,
  the x and y pad and the mapping canvas; the cursor is kept when a card is rebuilt and when the layout changes with the
  width; reduced motion is honoured for the two flashing buttons. Text is put in with `textContent` only; no browser
  dialog is used.
- **Tap targets**: buttons 44 px tall (Signal; the default look's small buttons are 34); staff buttons on Room 56 to 72;
  pads 80 tall; the switch 52 x 44; sliders 32 tall. Some list buttons are narrow: the mask list's up and down arrows, the
  surface list's arrows, the mapping nudges (one character wide, padding 10).
- **Contrast** (`pvj/THEMES.md`, computed): Signal's text on the page 17.98:1, muted 9.98:1, black on each area colour
  6.46:1 (pink) to 13.93:1 (yellow), the error line 5.54:1 on the page and 4.89:1 on a surface. The mapping canvas draws
  its own colours on #111.
- **Labels**: fields have a visible label (`labelled`) or an `aria-label`; buttons that repeat in a list carry the item's
  name in their `aria-label` ("Remove name", "Turn off name", "Move name up"); the position slider says its value as a time
  (`aria-valuetext`); state is said in words beside every colour (chips, badges, "On"/"Off" beside a switch).
- **Pages**: Pads: pads say playing with `aria-pressed`, unavailable with `aria-disabled`. Mapping: the canvas has a label
  and arrow keys, but a surface can only be chosen on it by pointer or from the list. Projectors: the brightness slider
  has a label with its value. The pad editor is a `role="dialog"` sheet without a focus trap.

## 15. Routes and the role each needs

From the route table in `pvj/api.py` and `policy.OUTSIDE` (a Guest's extra forms are in section 1):

- **Nobody** (before pairing): `GET /api/hello`, `POST /api/pair`, `/api/session`, `/api/logout`, `/api/support/login`.
- **Guest (view)**: the reads the panel draws from: `GET /api/status`, `/media`, `/pads`, `/modules`, `/theme`, `/health`,
  `/projectors`, `/room`, `/schedule`, `/shaders`, `/effects`, `/overlay`, `/mapper`, `/audio`, `/autostart`, `/streams`,
  `/sync`, `/support`, `/system`, `/inputs`, `/osc`, `/media/import`, `/preview.jpg`; `POST /api/media/info`.
- **Operator (live)**: playing and mixing (`/api/play`, `/control`, `/blackout`, `/fade`, `/fadein`, `/fadeout`, `/mix`,
  `/testpattern`, `/testtone`, `/player/restart`), shaders and effects (every POST), Vibes, pads, the overlay, the
  mapping and mapping mode's nudge, projectors' actions (`/api/projector`), the room (`/room`, `/room/group`, `/room/scene`),
  the schedule, power-up, sound output and sound reactivity's mapping and source, media (upload, delete, rename, import),
  access (`/access`, `/access/code`, `/access/cancel`, `/access/screen`, `/devices`, `/devices/invite`, `/devices/revoke`,
  `/guests`), MIDI (`/midi`, `/midi/learn`, `/midi/map`, `/midi/lights`), render cues, the look (`POST /api/theme`, and
  its export), `/api/support/stop`, and the reads of NDI, render nodes and sound.
- **Owner (full)**: network, screen output and the renderer, updates, clock, reboot and power off, the PIN, recovery,
  controller codes, DMX, OSC (and its key, its messages and state sent back), NDI settings and the discovery server,
  streams' changes, render nodes' changes, sound reactivity's device, boxes in step, adding and changing projectors
  (`/api/projectors`), mapping mode's switch, remote support's settings, start and extend, HTTPS (every route), settings
  export and import, diagnostics, factory reset, adding and removing looks.
