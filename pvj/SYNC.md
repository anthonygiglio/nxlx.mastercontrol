<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Multi-box sync and video wall (beta)

Several boxes play the same clip in step: one **server** leads, the **clients** follow. With the video wall, each box shows its own tile of the picture. This replaces the old PocketVJ "master and slaves" (omxplayer-sync) and PiWall.

Switch it on under System > Boxes in step on every box. Switching it off while a role is set asks first, because the other boxes stop following.

## Setting up

1. Connect the boxes to the same wired network (Wi-Fi works for testing, but is not steady enough for a show).
2. Put the same clips on every box, **with the same file names** (the media folder, or the top of a USB drive).
3. On one box choose **Server**, on the others **Client**. Use the same group name on all of them (`main` unless you change it); a second group on the same network then never mixes with the first.
4. Play on the server as usual (pads, the media list, the schedule, OSC, MIDI). The clients play the same clip, follow seeks, pause and blackout, and stop when the server stops.

The client's card shows what it does: the server it follows, "in step" or "catching up", and how many milliseconds it is off.

## How it keeps in step

The server sends a small message about ten times a second: the clip's name, the position, pause, speed, looping and blackout. A client that is off by more than half a second (a new clip, a seek, a late start) jumps, aimed a little ahead to allow for the time a jump takes; it learns that allowance from each jump. Smaller differences are corrected by playing up to 4 percent faster or slower for a moment, which is not visible and keeps the sound's pitch. Within 15 ms it plays at the server's speed.

Measured on a Raspberry Pi 4:
- two headless players following each other over the network on the same box: after the first jump, a median difference of 0 ms and at most 40 ms (one frame at 25 fps, which is also how finely the position is read);
- the box's real player as a client of a stand-in server on a Mac on Wi-Fi, following a 1080p film from a USB stick into the middle (15 minutes in): two jumps in the first 4 seconds (the first jump into a long file takes longer than expected), then it caught up the remaining third of a second by playing 4 percent faster (not a jump), and from 14 seconds on it stayed within 5 ms.

So allow a few seconds at the start of a synced show (a still or black at the start of the clip hides it). **Not yet measured between two boxes on two screens.**

## Video wall

Set, on each box, the wall's columns and rows, which tile this screen is, and the frame between screens (bezel) as a percentage of a screen: that much picture is hidden behind each frame, so lines run straight across the gap. Every box plays the whole file; the player crops it. 1 column and 1 row shows the whole picture.

## Safety and limits

- Messages are only accepted from private network addresses, with the same group name, and are small and strictly checked; more than 40 a second are ignored. A client follows one server at a time (the first it hears; another only after 3 seconds of silence). UDP senders can be forged on the same network, so keep the show network private, as for OSC and DMX.
- The server sends to the broadcast address of each local network (UDP port 5577 unless changed); routers do not pass broadcasts, so all boxes must be on the same network.
- A client follows the server's clip; a clip started on a client's own panel is replaced by the server's with the next message, and a client whose player restarted starts the clip again. Only files from the media folder or the top of a USB drive are synced: when the server shows a live input, a stream or the test pattern, the clients stop.
- A box with sync off can still show its wall tile.
- Not built: syncing a playlist position (a client plays the same file the server is playing, so "play all" works clip by clip), and sound-only boxes.
