# OSC control

Receive-only OSC 1.0 over UDP, for TouchOSC, Resolume, QLab, Max, Chataigne, Companion and anything else that can send OSC. Switch it on in **System > OSC** with the switch at the top of the page (full-access devices only); there is no other switch, and the port and networks have their own Save button. Default port **9876**, the same as the old receiver, so existing controller layouts keep their target.

## Safety

- **Off until you switch it on**, and it only listens while on.
- **Only private networks may send**: loopback, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local `169.254.0.0/16` (a direct Ethernet cable between laptop and box) and their IPv6 equivalents. Add your show network under "Extra networks". Ranges wider than a /8 are refused. UDP source addresses can be forged, so this keeps the internet out; it does not stop a hostile device on your own network, so keep the show network private.
- **Nothing dangerous is reachable.** `/shutdown`, `/reboot`, `/rebootall`, `/shutdownall`, factory reset, password changes and updates do not exist over OSC. They need a paired full-access device in the panel.
- **No replies are ever sent**, so the receiver cannot be used to bounce traffic at someone else.
- Each sender is limited to 200 messages a second (burst 400); packets are parsed strictly and bounded in size, arguments, and bundle depth. Log lines about bad or refused packets appear at most once every 10 seconds.
- Every value goes through the same validation as the web panel. A wrong value is dropped and logged, never applied.

## Stable addresses

Numbers use natural units, so set your controller's fader range to match (for example 0 to 100 for opacity). Pads and banks are **1-based** like TouchOSC's multipush grids.

| Address | Arguments | Effect |
| --- | --- | --- |
| `/pvj/pad/<bank>/<pad>` | value (press) | Play that pad. Bank 1 to 3, pad 1 to 12 |
| `/pvj/play/pad` | bank, pad (ints) | Same, with arguments |
| `/pvj/play/file` | name (string) | Play a file from the media folder |
| `/pvj/play/preset` | name (string) | Play a legacy preset such as `startlessonce05` |
| `/pvj/vibes` | value (press) | Start Vibes, the endless shader rotation (needs the Shaders and Vibes module, see [SHADERS.md](SHADERS.md)); `/pvj/stop` ends it |
| `/pvj/vibes/next` | value (press) | Go to the next shader now |
| `/pvj/vibes/previous` | value (press) | Go back to the shader before |
| `/pvj/vibes/set` | string | Start Vibes through the rotation set of that name (or id); `/pvj/vibes` uses the active set |
| `/pvj/stop` | value (press) | Stop the clip (the player stays running) |
| `/pvj/pause` | none, or 0/1 | No argument toggles; 1 pauses, 0 resumes |
| `/pvj/blackout` | none, or 0/1 | No argument toggles |
| `/pvj/fadeout` | none, or a button's 1 and 0 | Fade to black in 2 seconds. A 0 is a button's release and does nothing; exactly 1 is its press. Any other number is still read as the seconds, as before |
| `/pvj/fadeout/seconds`, `/pvj/fadein/seconds` | 0.1 to 30 | The same two fades over that many seconds (also exactly 1) |
| `/pvj/opacity` | 0 to 100 | Percent |
| `/pvj/size` | 1 to 200 | Percent |
| `/pvj/position`, `/pvj/position/x` | -100 to 100 | Horizontal shift (two names for the same thing) |
| `/pvj/position/y` | -100 to 100 | Vertical shift |
| `/pvj/flip/h`, `/pvj/flip/v` | 0/1 | Mirror left to right, top to bottom: set (an argument is required; the old `/fliph` and `/flipv` still switch over at each press) |
| `/pvj/fade` | value (press) | The one fade button: fades out in 2 seconds, and at the next press in. The box decides which from what the screen is doing, so it is right whoever started the fade; it also brings the picture up out of a Blackout. A button's release (0) does nothing |
| `/pvj/fadein` | none, or a button's 1 and 0 | Fade in from black in 2 seconds; also ends a blackout. Press and release as for `/pvj/fadeout` |
| `/pvj/clip/next`, `/pvj/clip/prev` | value (press) | The next or the previous clip of the playlist that is playing |
| `/pvj/transition` | name (string) | How one clip changes to the next: `cut`, `dip`, `crossfade`, `wipe-from-left`, `wipe-from-right`, `wipe-from-top`, `wipe-from-bottom`, `slide-left`, `slide-right`, `slide-up`, `slide-down` (the Mix screen's choice; it is saved) |
| `/pvj/transition/duration` | 0.1 to 10 | Seconds a transition takes (saved) |
| `/pvj/overlay` | none, or 0/1 | The overlay picture: no argument switches it over; refused while no picture is chosen |
| `/pvj/overlay/file` | name (string) | Choose the overlay picture: a PNG in the media folder, checked as the panel checks it |
| `/pvj/effect` | none, or 0/1 | The effect over the picture: 1 puts the one that was on last back (the first of the list if there was none), 0 takes it off, no argument is the one button (needs the Shaders and Vibes module, as every effect and shader address does) |
| `/pvj/effect/next`, `/pvj/effect/prev` | value (press) | The neighbour in the list of effects |
| `/pvj/effect/amount` | 0 to 100 | Percent of the effect in the picture |
| `/pvj/effect/control/<n>` | 0 to 1 | The n-th input (1 to 8) of the effect that is on, spread over its own range as a controller's knob does: a number over its MIN to MAX, a switch on from 0.5 up, a choice by position |
| `/pvj/shader` | name (string) | Show that shader, as the Shaders screen's Play does (`silk` or `silk.fs`). The box answers at once and the shader follows when the graphics chip has taken it; a refusal is shown on the Shaders screen |
| `/pvj/shader/next`, `/pvj/shader/prev` | value (press) | The neighbour in the active set (steps Vibes while it runs) |
| `/pvj/shader/speed` | 0 to 4 | Times the shader's own pace; 0 freezes it |
| `/pvj/shader/hue` | -180 to 180 | Degrees the colours are turned |
| `/pvj/shader/brightness` | 0 to 2 | 1 leaves it alone |
| `/pvj/shader/control/<n>` | 0 to 1 | The n-th input (1 to 8) of the shader on screen, as for an effect |
| `/pvj/shader/preset/<n>` | value (press) | The n-th preset (1 to 8) of the shader on screen |
| `/pvj/vibes/dwell` | 15 to 3600 | Seconds each shader of the rotation stays (saved) |
| `/pvj/mapping/mode` | none, or 0/1 | Mapping mode ([MIDI.md](MIDI.md), "Layers"): 1 enters it, 0 leaves it, no argument switches over. **Every `/pvj/mapping/` address does nothing unless a full-access device switched "Controllers may adjust the mapping" on (the Mapping card), and all but this one only in the mode** |
| `/pvj/mapping/surface/next`, `/prev`; `/pvj/mapping/corner/next`, `/prev` | value (press) | Choose the surface and the corner, round and round; the box's display marks them |
| `/pvj/mapping/left`, `/right`, `/up`, `/down` | value (press) | Nudge the chosen corner by the step |
| `/pvj/mapping/nudge` | two whole numbers | So many steps to the right and so many down (negative: left, up), -127 to 127 each; one message moves a corner 200 pixels at most |
| `/pvj/mapping/step` | 1, 10 or 50 | Pixels a step |
| `/pvj/mapping/undo` | value (press) | Take the last nudge back |
| `/pvj/speed` | 0.1 to 4 | Playback speed |
| `/pvj/volume` | 0 to 130 | Percent |
| `/pvj/seek` | -3600 to 3600 | Seconds, relative |
| `/pvj/rotate` | 0, 90, 180, 270 | Degrees |
| `/pvj/loop`, `/pvj/mute` | 0/1 | Set (an argument is required) |
| `/pvj/mix/reset` | value (press) | Reset opacity, size, position, speed, rotation |
| `/pvj/scene/<n>` | value (press) | Apply the n-th scene of the Room module (1 is the first; see [ROOM.md](ROOM.md)) |
| `/pvj/scene` | name (string) or number (2 or 2.0) | The same, by the scene's name or its place in the list. A scene that plays something also takes a blackout off |
| `/pvj/group/<n>/on`, `/pvj/group/<n>/off` | value (press) | Switch the projectors of the n-th group on or off; `all` in place of the number is every projector |
| `/pvj/group/<n>/mute`, `/mute_picture`, `/mute_sound` | 0/1 | Mute (1) or unmute (0) picture and sound, the picture, or the sound of that group (an argument is required) |
| `/pvj/group/<n>/input` | input code (`31`, as a string or a number) | Switch that group's projectors to that input; the codes are listed in System > Projectors |

**Real units, and no centre that sits.** A MIDI knob sends 0 to 127, so the box gives its zoom and position a middle that holds and a curve ([MIDI.md](MIDI.md), "How a level follows a knob"). OSC sends the real value, so `/pvj/size 100` is 100 percent and `/pvj/position/y 0.5` is half a step off centre: nothing is rounded to a centre and nothing waits for pickup. Set the fader's range in the sender.

**Freeze** on the panel is pause: `/pvj/pause`. There is no `/pvj/freeze`.

**One table with MIDI.** The addresses above that are a press or a level (the fade, the clip's and the shader's and the effect's steps, the presets, position, the shader's speed, hue and brightness, the effect's amount) make their calls from the same table as a MIDI controller's actions (`pvj/actions.py`), so what a controller can do and what OSC can do do not drift apart.

**No replies, still.** The box sends nothing back, so a TouchOSC layout cannot show the box's state (which pad plays, whether the screen is black). That is the safety property above and it was kept. Feedback is a later step and the owner's decision (D75): off unless switched on, sent only to addresses on the allow-list, and limited in rate.

**Buttons fire on press only.** TouchOSC sends `1.0` when a button is pressed and `0.0` when it is released; a command marked "press" acts on a non-zero value (or no argument) and ignores the release, so a pad does not fire twice.

## Names kept from the old receiver

`/stopall`, `/stopvideo` (both stop the clip), `/pause`, `/fastforward` (seek forward 10 s), `/volumeup`, `/volumedown` (10 percent steps), `/rotate0`, `/rotate90`, `/rotate180`, `/rotate270`, `/beameron` and `/beameroff` (every projector added under System > Projectors on or off, see [PROJECTORS.md](PROJECTORS.md)), and every start preset: `/startmaster`, `/startmaster05`, `/startlessonce03`, `/startlesseronce07`, `/startseamless02`, ... (the same names and files as the old scripts; see the preset table in `pvj/presets.py`).

Also from the old receiver: `/startmasteronce01` to `/startmasteronce99` (play the clip numbered so, once; these failed silently before), `/testscreen` and `/testscreenoff` (the test pattern), `/testtone`, `/testtoneleft`, `/testtoneright`, `/overlay` and `/stopoverlay` (the overlay picture chosen on the Mix screen), `/image` (the slideshow of the media folder) and `/stopimage`, `/fliph` and `/flipv` (each press switches the mirror over, like the old buttons).

Not carried over:
- on purpose, because they change settings or the system, which needs a full-access device: `/audiohdmiout`, `/audiojack`, `/audiousb`, `/audioboth` (System > Sound), `/startslave` (System > Boxes in step), `/reboot`, `/shutdown`, `/rebootall`, `/shutdownall`, `/customfunction1` and `/customfunction2` (they ran shell scripts);
- not built yet: `/screenon` and `/screenoff` (display sleep), the clock (`/clockdisplay` and colours), `/imageusb` and `/imagemanual`, the audio player (`/startaudio...`, `/stopaudio`), the PDF presenter (`/startpdf...`), the camera and its effects, soft edge, `/getcontent`;
- replaced: PiWall (`/piwallmaster`, `/piwallloop`) by Sync and video wall; Syphon (`/tcpsserver`) and `/ndisend`, `/ndireceiver` wait for NDI.

## Tested

The parser is tested on every type and padding length, on malformed packets, bundle nesting bombs and 4000 random or corrupted packets; the receiver on the allow-list, rate limit, a real UDP socket on loopback (and that it never replies), and through the settings API. The addresses added with D75 (2026-10-10) are tested through the parser, through the API with a fake player and a real shader engine with a fake graphics chip, and over a real UDP socket on loopback (`tests/test_osc_reach.py`). Not tested with a real TouchOSC layout or hardware controller, and not on the Pi.
