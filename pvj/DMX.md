<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# DMX over the network (beta)

Control the box from a lighting console or software (QLC+, Resolume, MadMapper, grandMA and others) over **Art-Net** (UDP 6454) or **sACN / E1.31** (UDP 5568). Switch it on under System > DMX lighting desk with the switch at the top of the page: it is the only switch, and the box is listening as soon as it says On. Off until you switch it on. Full-access devices only. If the receiver cannot start (the port is in use, say), the reason is shown on the page and the switch stays Off. The switch never saves the fields on the page; **Save** does, because protocol, universe, start channel and networks change together. In the API these are still two things: the `control-dmx` module and `enabled` in `POST /api/dmx`; a box with the module on and `enabled` off shows Off, and switching on sends only `enabled`.

Tell the console the box's address and universe; the box does not announce itself (no ArtPollReply, no discovery, nothing is ever sent back).

## Channels

Eight channels starting at the start address you choose (1 to 505), and an optional ninth for Vibes:

| Ch | Does | Values |
| --- | --- | --- |
| 1 | Opacity | 0 to 255 is 0 to 100 percent |
| 2 | Size | 0 to 255 is 1 to 200 percent |
| 3 | Position X | 0 to 255 is -100 to 100 |
| 4 | Speed | 0 to 255 is 0.25x to 2x |
| 5 | Volume | 0 to 255 is 0 to 100 |
| 6 | Blackout | 128 and up is on |
| 7 | Pad | 0 to 5 idle; each pad owns six values: 6 to 11 is pad 1, 12 to 17 is pad 2, up to pad 36 (216 to 221). Bank A is pads 1 to 12, B 13 to 24, C 25 to 36. A pad plays when the channel moves onto a different pad |
| 8 | Function | 50 to 99 stop, 100 to 149 pause, 150 to 199 resume, 200 to 255 fade out (2 s). 0 to 49 does nothing. Each action fires once when the channel moves into its range, not on every value inside it |
| 9 (optional) | Vibes | 50 to 99 stop Vibes, 100 to 149 start Vibes, 150 to 199 next shader. 0 to 49 and 200 to 255 do nothing. Each fires once when the channel moves into its range. Needs the Shaders and Vibes module (see [SHADERS.md](SHADERS.md)); while that is off the channel does nothing and the log says so once |

The first eight channels are where they always were. The fixed map had no free place (channel 8's ranges are all taken), so Vibes got a ninth channel instead of moving anything. It is read only if the console sends it: a universe that ends at the eighth channel works as before, and with start address 505 there is no ninth channel. To go to the next shader twice, move the channel out of the 150 to 199 range and back.

## Safety and behaviour

- Only private networks may send (loopback, 10/8, 172.16/12, 192.168/16, link-local and their IPv6 equivalents); add your show network under Advanced, "Also accept from these networks". Ranges wider than a /8 are refused. UDP sources can be forged, so this keeps the internet out, not a hostile device on your own network.
- **The first frame after turning it on only sets a baseline.** Nothing fires from it, so a console sitting at zero cannot black out the screen, and one sitting at 120 on the ninth channel cannot start Vibes. The same happens after changing the settings, and for the ninth channel when it first appears.
- If the signal stops, the box holds its last state. If no valid frame arrives for 3 seconds, the next frame (from the same console or another) is a new baseline, so a restarted console cannot fire anything by coming back at zero.
- Each level channel is applied at most 20 times a second; the next frame carries the change on, and a change that could not be applied is tried again.
- Limits that do not depend on the sender's address (which can be forged): 500 packets a second in total, and 50 commands a second to the player. The port is not shared: if another program holds UDP 6454 or 5568, the box reports it.
- Switching it off in System switches the module off, which stops the receiver. Your settings are kept.
- Only the actions in the table are reachable. Nothing that shuts down, reboots or reconfigures the box.
- Art-Net universe 0 to 32767; sACN universe 1 to 63999 (the box joins the multicast group `239.255.x.y` for its universe; unicast also works).

## Not verified

Tested with hand-built packets and a loopback UDP socket, never with a real console or on a real network. The Vibes channel was tested the same way, with a fake player and a fake clock. The sACN multicast join has not been tried on a real network. Merged or multiple sources (sACN priorities, merging two consoles) are not handled: the newest frame wins. sACN sequence numbers are not checked, so a duplicated or reordered packet is treated as a new frame.

## The page (2026-10-04)

The state line says what the box listens for and what has come in ("Listening for Art-Net on universe 0. 1,204 frames received." or "... Nothing received yet."). Protocol, Universe and Start channel each have a label; **Save changes** can be pressed once one of them differs from what is saved. A second card is the channel table: the channel number (from the start channel), what it does (the names of the table above) and the level the desk is sending now, read from `channels` in `GET /api/dmx` every two seconds.
