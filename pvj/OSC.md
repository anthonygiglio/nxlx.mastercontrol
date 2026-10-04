# OSC control

Receive-only OSC 1.0 over UDP, for TouchOSC, Resolume, QLab, Max, Chataigne, Companion and anything else that can send OSC. Switch it on in **System > Control (OSC)** (full-access devices only). Default port **9876**, the same as the old receiver, so existing controller layouts keep their target.

## Safety

- **Off until you turn it on**, and it only listens while on.
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
| `/pvj/stop` | value (press) | Stop the clip (the player stays running) |
| `/pvj/pause` | none, or 0/1 | No argument toggles; 1 pauses, 0 resumes |
| `/pvj/blackout` | none, or 0/1 | No argument toggles |
| `/pvj/fadeout` | seconds (default 2) | Fade to black |
| `/pvj/opacity` | 0 to 100 | Percent |
| `/pvj/size` | 1 to 200 | Percent |
| `/pvj/position` | -100 to 100 | Horizontal shift |
| `/pvj/speed` | 0.1 to 4 | Playback speed |
| `/pvj/volume` | 0 to 130 | Percent |
| `/pvj/seek` | -3600 to 3600 | Seconds, relative |
| `/pvj/rotate` | 0, 90, 180, 270 | Degrees |
| `/pvj/loop`, `/pvj/mute` | 0/1 | Set (an argument is required) |
| `/pvj/mix/reset` | value (press) | Reset opacity, size, position, speed, rotation |

**Buttons fire on press only.** TouchOSC sends `1.0` when a button is pressed and `0.0` when it is released; a command marked "press" acts on a non-zero value (or no argument) and ignores the release, so a pad does not fire twice.

## Names kept from the old receiver

`/stopall`, `/stopvideo` (both stop the clip), `/pause`, `/fastforward` (seek forward 10 s), `/volumeup`, `/volumedown` (10 percent steps), `/rotate0`, `/rotate90`, `/rotate180`, `/rotate270`, `/beameron` and `/beameroff` (every projector added under System > Projectors on or off, see [PROJECTORS.md](PROJECTORS.md)), and every start preset: `/startmaster`, `/startmaster05`, `/startlessonce03`, `/startlesseronce07`, `/startseamless02`, ... (the same names and files as the old scripts; see the preset table in `pvj/presets.py`).

Also from the old receiver: `/startmasteronce01` to `/startmasteronce99` (play the clip numbered so, once; these failed silently before), `/testscreen` and `/testscreenoff` (the test pattern), `/testtone`, `/testtoneleft`, `/testtoneright`, `/overlay` and `/stopoverlay` (the overlay picture chosen on the Mix screen), `/image` (the slideshow of the media folder) and `/stopimage`, `/fliph` and `/flipv` (each press switches the mirror over, like the old buttons).

Not carried over:
- on purpose, because they change settings or the system, which needs a full-access device: `/audiohdmiout`, `/audiojack`, `/audiousb`, `/audioboth` (System > Sound output), `/startslave` (System > Sync and video wall), `/reboot`, `/shutdown`, `/rebootall`, `/shutdownall`, `/customfunction1` and `/customfunction2` (they ran shell scripts);
- not built yet: `/screenon` and `/screenoff` (display sleep), the clock (`/clockdisplay` and colours), `/imageusb` and `/imagemanual`, the audio player (`/startaudio...`, `/stopaudio`), the PDF presenter (`/startpdf...`), the camera and its effects, soft edge, `/getcontent`;
- replaced: PiWall (`/piwallmaster`, `/piwallloop`) by Sync and video wall; Syphon (`/tcpsserver`) and `/ndisend`, `/ndireceiver` wait for NDI.

## Tested

The parser is tested on every type and padding length, on malformed packets, bundle nesting bombs and 4000 random or corrupted packets; the receiver on the allow-list, rate limit, a real UDP socket on loopback (and that it never replies), and through the settings API. Not tested with a real TouchOSC layout or hardware controller.
