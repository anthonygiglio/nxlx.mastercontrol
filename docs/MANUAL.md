<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# nxlx.mastercontrol: user manual

For the person running visuals at a gig. It covers the new Python panel (the `pvj/` folder). The old PHP panel for the legacy Pi 3 line has its own manual in `docs/html`.

**Status, read this first.** The new core has been built and tested in containers and CI, and the image has been booted and used on **one Raspberry Pi 4** (what was and was not verified there is listed in [HANDOFF.md](../HANDOFF.md)). Other boards, a projector, and several features (MIDI Learn, streams, the read-only root, the Network module and its Wi-Fi control) have not been tried on hardware. Where this manual describes them, that is what the code is written to do. Treat anything not listed as verified as untested on hardware, and do not use it at a paid show without a rehearsal and a backup plan. The checklist is in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md).

The pictures come from the test suite (test clips and a fake network), see [UI.md](UI.md).

## 1. Get it running

**With the image.** Download the `nxlx-mastercontrol-image` artifact from the "Build image" workflow (Actions tab), check it against `SHA256SUMS`, and flash the `.img.xz` with Raspberry Pi Imager ("Use custom"). The image has **no password and SSH is off**: the first boot asks for a user name and password on the Pi's own screen and keyboard. (For a headless first boot see "Option C" in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md).)

**On an existing system** (Raspberry Pi OS Bookworm or Trixie, Debian, Ubuntu): `sudo install/install.sh`. See [install/README.md](../install/README.md).

The web panel listens on port 80. Connect the box and your phone to the same network. A direct Ethernet cable between a laptop and the box also works (the network module has a "Direct cable" mode for that).

## 2. Pair your phone

Open `http://<address of the box>/` in a browser. The box makes a new four digit PIN every time it starts. Until the first device has paired, the box draws the PIN and its address on its own screen whenever nothing is playing; after that it stays off, so it cannot show at a gig. If every paired device is later removed it comes back, which is the way in for a box nobody can reach; a clip that starts clears it at once. You can always read it with `sudo pvj-pin`, and any full-access device can make a new PIN or a link for someone in System > People and codes.

![Connect screen](images/ui/connect.png)

A paired phone is remembered until you remove it or it logs out (a presenter or a guest under System > About and power, **Log out**; an owner on its own row under Paired devices). A wrong PIN is throttled. If someone locks new pairing by guessing, a paired full-access device clears it with **New PIN** in System.

There are three kinds of access, and the panel uses these names for them everywhere: **Guest (can watch)**, **Presenter (can play and mix)** and **Owner (everything)**: pads, modules, files, settings and who may come in. (In the API and the settings file they are `view`, `live` and `full`.)

### Letting other people in: codes and QR codes on the display

For a studio or a gig, open System > People and codes. The first card is **Let someone in**:

- Choose how long a new code works: **15 minutes, 1 hour or 2 hours**. It is 1 hour unless you choose otherwise.
- **Guest code** (Guest, can watch) or **Presenter code** (Presenter, can play and mix) makes a 6 digit code, shown large with its QR code, the time it still works and the uses it has left (20). There is one code of each kind at a time: if one is active, the panel asks before it makes a new one, because the old one stops working. Codes are never written to disk, so a restart clears them.
- **End this code** ends a code now, after asking. People who already joined with it stay; remove them under Paired devices.
- **Show on the room screen** puts the chosen codes on the box's screen, each with a big **QR code** in the top right corner (guest on the left, presenter on the right), together with the panel address, for 1 minute to 1 hour, even over a playing clip. **Take it off the room screen** takes them off at once. A phone camera that scans a code opens the panel with the code filled in; one tap on **Join with code** and it is in, with exactly that access. The code travels in the part of the address after `#`, which a browser does not send to the box or anyone else, and the panel removes it from the address bar straight away (a phone's camera or scanner app may still remember what it scanned).
- The Owner PIN can be shown too (as text, never as a QR code), but the panel asks first: everyone who can see the screen can then take over the box. While anything is on the display, a snapshot taken by a guest or presenter shows only the video, never the text or the QR codes, so nobody can read the PIN or a presenter code remotely.
- If someone blocks joining by guessing wrong codes, **Unblock joining** opens it again without changing the PIN.
- The panel answers only to its IP addresses, `localhost`, its own name and `<name>.local`. To use another name (a studio DNS name), add it to `PVJ_ALLOWED_HOSTS` in `/etc/pvj/pvj.env` (comma separated). This stops a web page elsewhere from driving the panel through a visitor's browser (DNS rebinding).
- **Print access sheet** prints a page to pin up: a QR code for the panel address (no access in it; people still need a code), and the current guest and presenter codes.
- Under **Paired devices**, **Create link** makes a link for a guest or a presenter that does not expire (until you remove its device from the list), with its QR code; hand it to a resident operator.
- Under **Paired devices** your own device is marked "this device" and has **Log out** instead of Remove; it asks first. If it is the last device with everything allowed, the question says that nobody can then read the PIN from a panel and shows the PIN you will need to pair again (the box also draws it on its display while nothing is paired, and `sudo pvj-pin` prints it).
- **The Owner (everything) PIN** is on the same card, hidden until you press **Show**: it is on the screen for 12 seconds and then goes by itself, and it goes when you leave the page, so mind a projector or a stream. **Copy** copies it (on plain http the panel may ask you to select it and copy it yourself). **New PIN** makes a new one and shows it the same way. The box counts how often the PIN is shown (ten times in five minutes per device, then it asks you to wait) and writes a line in its journal for each, without the PIN. A panel that was started without making a new PIN knows only a check value of it: it says so and offers New PIN. Only a full-access device sees any of this; a presenter, a guest and remote support are refused by the box, not only by the page.

**How many, and for how long.** A phone that joined with a guest code is forgotten once it has not opened the panel for 7 days; it joins again with a new code. Phones paired with the PIN, with a link or with a presenter code stay until you remove them. The box keeps at most 200 paired devices, and 20 of those places are only for devices paired with the PIN, so however many guests have joined, the owner can always pair another phone. When the list is full for guests, joining says so; remove some under Paired devices.

**Staff who are presenters can let a guest in.** A presenter's People and codes (and "Let someone in" at the bottom of the Room screen) has the guest part only: the time, **Guest code**, the code with its QR code, **Show on the room screen** and **End this code**. So whoever is running the room can let a visitor watch without the owner being there. A presenter cannot make a presenter code, see the one the owner made, show or change the PIN, make a link, or see or remove devices; the box refuses those, it is not only that the buttons are missing. A presenter sees a guest code the owner made and can show it; ending it or replacing it asks first and says the owner made it. A presenter's guest code works for 15 minutes, 1 hour or 2 hours and at most 20 times, and presenters together can make 6 guest codes in an hour; after that the panel says how long to wait (the owner is not limited). While the owner has the PIN or the presenter code on the room screen, a presenter cannot put the guest code up (it would replace them); taking the guest code off leaves the owner's items there. None of this can be done through remote support.

**At the box with a MIDI controller and no paired phone.** The box can draw a one-time pairing code on its own display when you hold a pad or button on a controller plugged into it. It is **off until you switch it on**: System > People and codes > **A code from a controller**. "Presenter codes from a controller" lets the hold show a code that pairs one device as a presenter; "Full access codes too" (a second switch, off unless you want it) allows a code that pairs one device with everything allowed, like the PIN. Each asks before it goes on, because anyone who can reach the controller can then pair a device. To use it: hold the control for 3 seconds (up to 10) and let go, read the code off the display, open the address shown there on the new phone and type the code in the "6 digit code" field. The code works once, for 2 minutes; pressing the control again hides it; the box makes at most 6 an hour. The same card tells a paired owner that a code is on the display, since when, and has **End this code**; afterwards it says what became of it. On a Launchpad Mini the eighth pad of the top row shows a presenter code; on any controller you give the action to a pad or button on its card under System > MIDI controller. The rules in full are in [pvj/MIDI.md](../pvj/MIDI.md) ("A pairing code on the display"). Not yet tried on a real controller or on the Pi.

![Access card with a guest and a presenter code and their QR codes](images/ui/access.png)

Tested on a Raspberry Pi 4: the codes and QR codes were drawn on the display, decoded off a snapshot of the screen by a real QR scanner (zbar), and a join with each code gave exactly guest or presenter access.

## 3. Put clips on it

Media > **Upload clips** (full access). Video and image files only. Uploads go to a hidden temporary file and appear only when complete, so a dropped connection never leaves a half file. Or plug in a USB drive: it mounts read-only under `/media/pvj/<label>` (and `/media/usb` for the newest one), and its video and image files at the top level of the drive appear on the Media screen under "USB drive" with a **Play** button, so you can play straight from the stick without copying (a 3 GB film played fine this way on a Pi 4). Files in folders on the drive are not listed yet; put the clips at the top. To keep a clip after the stick goes, press **Copy to the box** next to it (full access): it is copied in the background with a progress line and a Cancel button, with the same checks as an upload (free space, a hidden temporary file, never replacing a clip unless you confirm). Cancel acts between chunks, within a moment.

![Media screen](images/ui/media.png)

Clips live in `/var/lib/pvj/video`. **If you turn on the read-only root (`sudo pvj-rootfs enable`), move media to a second disk or USB drive first**: everything on the system disk is lost at reboot afterwards, and the command refuses if media is on the system disk.

### Prepare your clips

What plays smoothly depends on the board. What this project has measured, and what it has not:

| Board | Plays well | Notes |
| --- | --- | --- |
| Raspberry Pi 4 | H.264 up to 1080p (a 1080p 24 fps film: about one core, no dropped frames; 720p 30 fps: a fifth of a core) | H.264 is decoded in software on this build. 1080p at 50 or 60 fps, HEVC and 4K are **not tested yet**. |
| Raspberry Pi 5 | HEVC (in hardware), H.264 up to 1080p (in software) | **Not tested on a Pi 5 yet.** |
| Raspberry Pi 3 | 720p H.264 | Larger is **not tested** and may stutter. |

Media > **Info** on a clip shows its format and, when it may not play smoothly on this box, says why.

A safe recipe (HandBrake, free, for Mac, Windows and Linux):
- Format **MP4**; video **H.264** (or **H.265/HEVC** for 4K on a Pi 4 or 5), profile Main or High, **8-bit**;
- **Constant frame rate**, "Same as source" (phone footage is often variable: switch to constant); 25, 30, 50 or 60 fps;
- Resolution: the screen's (1920x1080 for most projectors); **no** automatic cropping;
- Quality: constant quality around 20 to 22 (RF), or 8 to 12 Mbit/s for 1080p;
- Audio: AAC, 48 kHz, stereo.

Avoid editing formats (ProRes, DNxHD and similar): they are many times too heavy. Very high bit rates from a slow USB stick can stutter too; copy those to the box.

Names: anything readable works (spaces too). Start a name with two digits (`01_intro.mp4`, `02_loop.mp4`) to play it by number from Quick play, the old start scripts and OSC. On a USB drive, put clips at the top level.

For a synced show or a video wall, use the same files with the same names on every box, and start each clip with a second of black or a still: the clients jump into step during the first seconds.

Power matters as much as the clip: a weak power supply makes a Pi stutter, drop the network and damage its SD card. Use the official supply (Pi 4: 5 V 3 A; Pi 5: 5 V 5 A); System > Health warns when the voltage drops.

## 4. Play

**Live** has three banks of twelve pads. **Edit pads** (full access) assigns a clip or a shader to a pad. Tap a pad to play it. **Fade out** (one button: a tap fades the picture out over 2 seconds, and the button then reads **Fade in** and flashes while the picture is down; the next tap fades it in, also out of a Blackout. If your device asks for less motion it does not flash: it stands inverted with a dashed edge), **Freeze** (pause), **Stop** (end the clip and leave the screen black and the player ready) and **Blackout** (black without stopping) are always at the bottom.

![Live screen](images/ui/live.png)

**Screen** (Live, under Now playing): tap **Take snapshot** to see what the box is putting on the display. It is a screenshot of the player's own output, so a blackout, brightness or size change shows up. With nothing playing the card says "Nothing is on the screen right now." It is a single picture on request, not a live view, and it is deliberately not automatic: on a Raspberry Pi 4 each snapshot stalls playback for about a quarter of a second (measured: a continuous preview made video visibly choppy). Any paired device may take one, including view-only guests. For a real-time picture use an HDMI capture device on the display's output.

**Sound output** (System): on a Raspberry Pi "Automatic" sends the sound to the HDMI port that has the screen on it (the player's own default is the 3.5 mm headphone jack, which is silent on a monitor). Pick another output, such as the headphones or a USB sound device, in System > Sound. The choice is remembered and re-applied if the player restarts.

**Now playing** (Live) has the controls of a normal player: a **position slider** (drag and release to jump), **Prev** and **Next** (when several clips are playing as a list, for example Play all), **- 10 s** and **+ 10 s**, and **Test pattern** (SMPTE colour bars from the player itself, for lining up a projector; tap again to stop).

![Now playing with the transport, and Take snapshot](images/ui/live-transport.png)

**Quick play** (Media) plays the whole folder, looping or once, or in a random order (**Shuffle all**), or the clips whose names start with a number (`01_intro.mp4` is clip 01), like the old Video tab. Play all leaves audio files out.

![Quick play](images/ui/media-quickplay.png)

**When a clip ends.** Each pad has its own ending (Edit pads, then the pad): **Loop**, **Play once, then black**, or **Play once, hold the last frame** (for a sting that should stay on screen).

**A shader on a pad.** With the Shaders and Vibes module on, the pad editor has two buttons, **Clip** and **Shader**. Shader lists the box's shaders; a shader that has presets then asks which one to start with ("Its own start" is the preset called default if there is one, else the shader's own values). The pad then carries the word Shader and the shader's name, and is lit while that shader is on the screen as the pad starts it (with the pad's preset, and not while Vibes is the one showing it). Renaming a preset on the Shaders page carries the pads that use it; removing a shader there says how many pads play it. Tapping it is the same as choosing the shader on the Shaders page: Vibes stops, the shader stays until something else is played, and Blackout, the Opacity slider and the fades apply to it. A shader has no end, so there is no ending to choose.

- **The Mix transition** does around a shader pad what it does around any shader: a shader arrives with a cut whatever the transition, and the clip you play after it blends or dips from the shader's picture.
- **From a controller** (MIDI, OSC, a lighting desk, a Room scene) the pad answers at once and the shader follows as soon as the box's graphics chip has taken it, also when Vibes was in the middle of changing shaders at that moment (Vibes ends, and the picture it had stays until the pad's shader is on). If the chip refuses the shader, the Live screen says so under the pads and the Shaders page too. From the panel the tap itself says so. The controller's own lights have no way to show a refusal: the pad's light stays as it was.
- **If the pad says its shader is gone**, it was deleted or renamed after the pad was set: Edit pads, then choose again. A pad whose **preset** was deleted still shows its shader, starting as the shader does by itself (the panel's answer and the journal say so); deleting a preset on the Shaders page says how many pads start with it.
- **If the box's graphics chip refuses the shader**, a line under the pads says so, with the chip's words, and the pad is marked, whoever tapped it. The shader before it comes back if there was one; if nothing or a clip was on, the screen is black, and an effect that was on goes off with it.
- **Tapping the same pad again while its shader is on** does not start the shader over: it goes on from where it is, with the pad's preset applied again, as Play does on the Shaders page.
- **After Fade out** a shader (from a pad or from the Shaders page) comes up from black over half the Mix duration, as a clip does. If it says to turn on the Shaders and Vibes module, do that in System.
- **At power-up** a shader pad can be the pad that starts (System > At power-up). The schedule plays files and Vibes, not pads.
- **A box that syncs others** sends them nothing for a shader, as for any shader: the other boxes go black while it is on.
- **Going back to an older version of the box's software:** it shows a shader pad as an empty pad with its label and leaves it alone, so the pad works again after the next update; but if you edit that pad, or export and import the settings, on the older version, the shader is gone from the pad. If **At power-up** is set to a shader pad, the older version starts with a black screen (its card says "pad is empty"), and it refuses to import a settings file in which the power-up pad is a shader pad: set At power-up to something else before going back.

**Slideshow** (Media, like the old Presenter tab): the pictures of the media folder or of a USB drive, each for 0.1 second to a minute, then start again, keep the last picture, or go black; optionally in a random order. Prev and Next on Live step through them.

![Slideshow](images/ui/slideshow.png)

**Live input** (Media): an HDMI capture stick or a webcam on USB, shown like a clip, at 720p or 1080p at 30 frames a second. Playing anything else, Stop, the test pattern switched off or Restart player ends it (switching away from it cuts straight to the next clip, without the dip to black). On a Raspberry Pi 4 with a USB3 HDMI capture stick both sizes played without dropping frames (720p60 dropped frames and is not offered); at 1080p the picture kept slightly behind real time in one test, so prefer 720p until that is checked with a real source. The device is read by a separate helper, so a crash in the capture code cannot stop the player (a fault in the kernel's USB or camera driver still could). Only webcams that can send YUYV are known to work; others are converted, untested. The input's sound is not played yet, and the delay from the source to the screen has not been measured.

**Audio files** (mp3, wav, flac, ogg, m4a, aac, opus) can be uploaded and played like clips; the screen stays black while they play.

**Overlay picture** (Mix): put a PNG from the media folder over the video, like the old panel's overlay.png: a logo, a watermark, or a mask that blacks out the parts of the picture that miss the screen. Make it transparent where the video should show. It is fitted to the screen once (about a second on a Pi 4), stays on top of the video, and comes back by itself if the player restarts. It cost no measurable playback on a Pi 4 with a 1080p film.

![Overlay picture](images/ui/mix-overlay.png)

**Mirror and position** (Mix): Flip left-right or upside down for rear projection or a mirror rig (live, no reboot; on a Pi 4 it costs about half a processor core at 1080p, and dropped no frames in the test), and Position Y next to Position X.

![Mirror](images/ui/mix-mirror.png)

**Clip details** (Media > Info): codec, picture size, frame rate, length and sound of a clip, read by the player without showing it (the old Movie Codec and Movie Resolution buttons).

**This box** (System > About and power): versions, free space for clips, what is connected to each screen output (the modes each offers are under "Advanced: what each screen can show"), the picture the player is using now, and the box clock. Below it, for full-access devices, **Restart and power**: **Restart player** (the picture stops for a few seconds), **Restart the box** and **Power off** (each asks first, in place; neither is reachable over OSC, MIDI or DMX), and, while the clock has not been set from the network (a Pi has no clock battery, so a box with no internet starts at the time it was last shut down), **Set the box clock to this phone's time**. These go through a small helper, `pvj-sysd`, that runs as root with no capabilities of its own (systemd and timedated do the work), in its own folder that only root can change, and answers only root and the panel's account. A clock is only accepted up to 2036: a phone set to a far future year would otherwise stick, because the box saves its clock. If setting the clock goes wrong, the panel says so, including when network time could not be switched back on (the helper switches it back on at its next start). **Sound** (System > Sound): choosing where the sound comes out applies at once and says "Saved". **Test sound**: 5 seconds of 440 Hz on the left, right or both speakers; while something is playing it asks first, because it stops what is playing.

![Box](images/ui/box.png)
![Sound output](images/ui/sound-output.png)

**Mix** has opacity, volume, size, position, speed, rotate, loop and mute, and how one clip changes to the next: **Cut**, **Dip to black**, **Crossfade**, a **Wipe** from one of the four sides or a **Slide** off to one of them, and how long it takes.

**Crossfade** blends the picture that is on the screen into the clip you play. The player shows one picture at a time, so it is done with a still: at the tap the outgoing picture freezes (its sound stops), a still of the whole screen is laid over the new clip, and from the new clip's first frame the still fades away over the duration you chose. **The outgoing picture stands still for the length of the transition; only the incoming one moves.** What you should know:

- It works from a clip, a picture, a stream, a live input or a shader, to a clip, a picture or a pad you play from the panel, a controller, the schedule or autostart. Playlists, slideshows, streams, live inputs, the test pattern and a shader start with a cut as before, and the clips inside a playlist follow each other with a cut. Vibes keeps its own dip between shaders.
- The still is the screen as it is: an effect, a projection mapping, the opacity and a logo are in it. An effect you change during the transition changes the incoming clip only.
- **Blackout, Fade out, Fade in, the Opacity slider, Stop, playing something else and changing the mapping end the transition at once**: the still goes and the new clip is there. (The still is drawn over the picture, where Blackout does not reach, so the box takes it away instead.) None of them waits for the still to be taken. **The newest wish wins**: if you press Stop, Next or Previous or play something else while a clip's still is being taken or its dip is on the way down, that clip is not loaded afterwards; of several pads pressed in a row only the last one is played, with one transition. Stop, Next or Previous during a dip put the picture's level back, so what you start next is not dark. **A Fade out, a Blackout or a move of the Opacity slider that you make while something is still loading stands**: a clip, a stream or a live input that takes a second to arrive does not bring the picture back up when it does. Something you play after a Fade out does bring the picture back, as before. Blackout, a fade, Reset and the Opacity slider are not newer wishes: the clip you tapped still loads, as a cut, at the level they set (with Dip to black this used to drop the clip silently). The Vibes rotation and the schedule: a rotation never takes the screen from a clip you tapped; a schedule entry is a wish like any other, and whichever of the two comes later wins.
- **A play from a controller (MIDI, OSC, DMX) or from a Room scene answers before the clip has loaded** when one of these transitions is chosen, so that the same controller's Blackout or Stop is never held up behind the still. If the clip then cannot be loaded, the controller and the Room screen have already been told it went well; the failure is a line in the journal (`journalctl -u pvj-web`: "a clip played from a controller did not start"), nowhere else. A play from the panel still waits and says so itself. **Any opacity message ends it, also one that changes nothing**: a MIDI or DMX opacity fader that sends by itself (a worn fader, a desk that repeats its values) will end every transition as it begins. Take such a fader off the opacity while you use these transitions.
- With the screen dark (Blackout, a picture faded out, opacity 0) or nothing playing, the clip simply starts.
- A stream that takes time to arrive: the old picture stays until its first frame, for ten seconds at most.
- **When the box cannot keep up it uses Dip to black instead**, and says so under the transition's buttons on the Mix screen, with the reason and a **Try again** button (which does what choosing the transition again does). It also tries again by itself after five minutes. That happens with a screen larger than 2560 x 1440 (no Try again helps there), and after transitions that showed the box is too slow: **two stills in a row** that each took the player more than a second, or one still that took more than three seconds (a single slow still is simply used, at every duration: that one transition starts late and nothing else changes); fewer than five steps a second; the clip under it dropped more than five frames a second; or the still or a step failed outright (that play is a cut). `GET /api/status` has the reason in `mix.fallback`, and `journalctl -u pvj-web` a line "transitions given up" and, for every slow still, how long it took.
- **After Fade out** a clip played with Crossfade, a wipe or a slide comes up from black over half the duration, as with Dip to black: there is no picture to blend from. (It used to jump to full brightness.)
- **Wipe** and **Slide** are the same still, used another way. In a wipe the still stays where it is and is cut away from one side, so the new clip appears from the left, the right, the top or the bottom. In a slide the still moves off the screen to the left, the right, up or down and uncovers the new clip, which does not move with it (this is not a push). Everything above holds for them too. They ask less of the box than a crossfade: the still is not rewritten at each step, only a smaller part of it is drawn, thirty times a second.
- **Run on a Raspberry Pi 4, not yet watched on one.** On 2026-10-09 a script played clips on the test Pi 4 at 2560 x 1440 with a crossfade, a wipe and a slide: they ran, and the old picture stood still for about a second before each (a play answered after 0.93 to 1.09 seconds). Nobody was at the monitor, so how smooth a fade looks and how that second of standing still looks are not known. On a 1920 x 1080 screen the standing still should be shorter; that was not measured.

![Mix screen](images/ui/mix.png)

## 5. Optional modules (System)

The System screen is a short list in three groups, with **Health** above them:

- **Everyday**: Projectors, Schedule, Shaders and Vibes, People and codes, Sound.
- **Show tools**: At power-up, Streams, Projection mapping, Boxes in step, MIDI controller, DMX lighting desk, OSC.
- **This box**: Network, Updates, Remote support, Backup and reset, Look, About and power.

Tap a row to open its page. **‹ System** at the top of a page, or the phone's back gesture, returns to the list.

### The look of the panel

**System > Look** (owner only) changes how the panel is drawn on every phone and laptop that opens this box. Tap a look and it is applied at once; the box remembers it, and it travels in a settings file.

- **Dark stage** is the look the box comes with. **Light**, **Night red** (keeps a dark room dark) and **High contrast** are the same panel in other colours. With these four you can also tap an **Accent** colour, or *Default* to go back to the look's own. Only the accents that can be read on that look are offered (on Light, one), and an accent that the next look you tap cannot carry is left behind.
- **Signal** and **Signal light** are a different style, to try: very large capitals, square blocks and thick rules, and one strong colour for each part of the panel, so you can tell where you are from across the room. Room is yellow, Shaders and Vibes pink, Live and Media green, Mix violet, System blue. Signal is for a dark room, Signal light for a bright one. There is no accent to choose with these, because the colour says which part you are in.
- A state is always written out as well as coloured: Off, Set up, Ready, Active, Problem. In Signal a state never has the colour of a part of the panel: Off is grey, Set up amber, Ready white (black in Signal light), Active light blue, Problem red, on every screen.

Each look is shown as a small picture in its own colours and type, so you can judge it before you tap it; the one in use says "in use".

**Your own themes.** A theme is a small file of colours, shapes and type that you can make yourself and carry to another box.

- **Save this look as a file** downloads the look in use as a theme file (for a look that comes with the box, under a name of its own such as "My Signal"). Start from Signal: its file lists everything that can be changed.
- Change the file: in a text editor, or from your Figma design with the converter (see [pvj/THEMES.md](../pvj/THEMES.md), "Make your own theme", which lists every value and its limits).
- **Add a theme** takes the file. It appears among the looks marked "yours"; tap it to use it. Adding the same file again after a change replaces it. The box can hold 16 added themes.
- **Remove** beside its name takes it off the box, after asking. If it is the look in use, the panel goes back to Dark stage.
- The box refuses a theme whose text could not be read, and says which pair is too faint ("Text on the Room colour is 2.1 to 1; it needs 4.5"): make that colour lighter or darker and add it again. It also tells you, without refusing, when a state colour is close to the colour of a part of the panel.
- If a theme file on the box cannot be used (damaged, or put there by hand with a mistake in it), the Look page says which file and why.
- Added themes travel in a settings file (Backup and reset), so a box set up from another's settings has the same look. A factory reset removes them. Adding and removing cannot be done through remote support.

A theme changes colours, corners, line widths, spacing, the size of controls, the case and weight of titles and the choice between the fonts on the box. It cannot change what is on a screen or where: layouts are built into the panel.

To go back, open System > Look and tap **Dark stage**. Signal brings its own two typefaces, which are on the box; nothing is fetched from the internet. A device that has not received them yet shows its own font for a moment. Technical notes, and how to add a theme of your own: [pvj/THEMES.md](../pvj/THEMES.md).

Each row says how it is doing, with one word and a sentence:

| Word | Meaning |
| --- | --- |
| **Off** | switched off (your settings are kept) |
| **Set up** | on, but it cannot work yet: nothing added, or nothing chosen |
| **Ready** | on and set up, doing nothing at the moment |
| **Active** | doing something now |
| **Problem** | something is wrong; the sentence says what |

**Switching a module on.** Beta modules are **off** until you switch them on. Open the module's row: while it is off the page shows what it does and one button, **Switch on**. After that the switch is at the top right of the page, with the word On or Off beside it. You stay on the page, and a line under the title says what happened.

**Switching off** is immediate, except where it changes what the room sees. Then the page asks first, in place, and goes back by itself after 8 seconds if you do not answer: Vibes while it is playing, Projection mapping while it is on the screen, Boxes in step while a role is set, and Projectors.

**One switch per feature.** The switch at the top of a page is the only one: there is no second on and off button inside. Switching DMX, MIDI, the schedule or OSC on makes the box listen (or the schedule run) at once, and Remote support's switch is what allows it. If something stops it from starting (a port that is in use, say), the reason is shown on the page and the switch stays Off. Switching the schedule on when it already has entries asks first ("Switch the schedule on? 5 entries will start running at their times."), and switching it off keeps the entries. A switch never saves other fields on the page: those have their own Save button.

**When a change is saved.** A control that does one safe thing (a switch, a choice from a list) is applied when you tap it, and says "Saved". **Save changes** appears only where several fields must change together (DMX, OSC, At power-up, the Remote support server): it cannot be pressed until something changed, and says "Not saved yet" while it has.

**The same on every page.** Every field has its name above it and, where it helps, a line of help below. A list (projectors, schedule entries, streams) has one row per thing: its name, one line that says how it is, a red line when something is wrong, one main button, and **More** for everything else, with Remove last. **+ Add a ...** opens the form in place; it is already open while the list is empty. **Every Remove, and everything that changes what the room sees or locks someone out, asks first, in place**, naming the thing and what happens; the question goes away by itself after 8 seconds. The panel never opens a browser dialog. On a laptop (from about 900 px) the pages with a lot on them use two columns: the list on the left, scrolling by itself, and its form beside it.

A presenter sees only Health, Projectors, Shaders and Vibes, People and codes (to let a guest in), Sound, Streams, Boxes in step and About and power (and only the modules that are on); a guest sees Health, About and power, and Shaders and Vibes while that is on (to see what is playing). Both log out on About and power (**Log out**, under This phone): the box forgets the device and the pairing screen comes back. Modules that are not built yet are listed at the bottom, folded, with no switches.

| What | Where to read |
| --- | --- |
| **Autostart**: what plays at power-up and after a crash | [pvj/AUTOSTART.md](../pvj/AUTOSTART.md) |
| **Weekly schedule**: play, start scripts, stop, blackout, show and projector power at set times | [pvj/SCHEDULE.md](../pvj/SCHEDULE.md) |
| **Video wall and sync**: boxes play in step (server and client), each can show a tile of the picture | [pvj/SYNC.md](../pvj/SYNC.md) |
| **Projection mapper**: quads, triangles and grids, lined up from the phone (card on Mix) | [pvj/MAPPER.md](../pvj/MAPPER.md) |
| **Shaders and Vibes**: moving pictures made by the GPU, and one button that plays them endlessly | below, and [pvj/SHADERS.md](../pvj/SHADERS.md) |
| **Projector control** over PJLink: on, off, input, picture and sound mute, live status, lamp hours and warnings. **No real projector has been tested yet** | [pvj/PROJECTORS.md](../pvj/PROJECTORS.md) |
| **Streams**: SRT, RTSP, RTMP | [pvj/STREAMS.md](../pvj/STREAMS.md) |
| **DMX over the network**: Art-Net and sACN | [pvj/DMX.md](../pvj/DMX.md) |
| **MIDI controller** (USB) | [pvj/MIDI.md](../pvj/MIDI.md) |
| **OSC**: TouchOSC, Resolume, QLab and others | [pvj/OSC.md](../pvj/OSC.md) |
| **Network settings** (wired and Wi-Fi: join a network, the box's own hotspot, Wi-Fi off) | [pvj/NETWORK.md](../pvj/NETWORK.md) |

**A MIDI controller that just works.** Switch MIDI on (System > MIDI controller) and plug in a Korg nanoKONTROL2, an Akai MIDI Mix or a Novation Launchpad Mini (the original one): the box recognises it within a couple of seconds and it works at once, with nothing to teach. The page shows a card for it with the controller drawn on it; every control says what it does, and lights up when you move it. In short, the same on each so your hands find it again:

- **The shader's controls** are the top row of knobs on the MIDI Mix (the first eight controls of whichever shader is playing) and knobs 4 to 7 on the nanoKONTROL2 (the first four).
- **The faders**, left to right: opacity, volume, clip speed, shader speed, shader colour turn, shader brightness, effect amount; the last is spare.
- **Zoom, position X and position Y sit side by side on three knobs** (knobs 1 to 3 of the nanoKONTROL2, the first three of the MIDI Mix's middle row). The middle of each knob is exactly 100 percent and exactly centred, and a knob that is not where the picture is does nothing until it gets there, so nothing jumps.
- **One fade button** on each controller fades out, and at the next press in; its light flashes while the picture is down (not on the MIDI Mix, whose fade button has no light).
- **The Launchpad Mini's grid is the pads**, each bank as the panel draws it, three rows of four: bank A top left (amber), bank B top right (yellow-green), bank C under A (orange); the pad that plays is bright green. Its round buttons on the right are the Room scenes and, below them, Fade, Freeze, Stop and Blackout.
- Each controller's card tints the controls that belong together and names the zones under the drawing. **These layouts were drawn afresh on 2026-10-10 and have not been on a controller yet**; [pvj/MIDI.md](../pvj/MIDI.md) has each one as a drawing with the reasons, and what changed for a hand that knew the first ones.
- **A row of buttons plays pads 1 to 8** (S on the nanoKONTROL2, Mute on the MIDI Mix) of the bank the controllers are on; two buttons step through banks A, B and C (Marker < and > on the nanoKONTROL2, Bank Left and Right on the MIDI Mix). **The next row is shader presets 1 to 8** (M, Rec Arm). The Launchpad Mini shows all three banks at once: two rows of six pads each, then a row of presets and a row that presses the shader's controls.
- **Transport**: Play is pause and resume, Stop is stop, Rewind and Forward are the previous and next clip, Track < and > the previous and next shader, Cycle is Vibes on and off, Marker Set starts Vibes with the set Ambient and Rec with the set Show (E and F on the Launchpad). On the Launchpad these are the round buttons along the top.
- **Blackout and the Room scenes need the same button twice within a second**, so a stray finger does not darken the room. They are marked 2x on the card. If you put one of them on another control, it is guarded the same way unless you switch "Press twice" off there.
- **A fader that is not where the box is waits.** Opacity, volume, the speeds and the shader brightness do nothing until the fader reaches the value the box has; then it is followed. So a fader left at the bottom does not black the screen out when you touch it.
- **Change anything**: tap a control on the card and choose what it should do, or Nothing. Your choice wins over the standard; "Back to the standard" undoes it. The switch "Standard layout" turns the whole layout off for that controller.

A nanoKONTROL2 must be in its CC mode: hold SET MARKER and CYCLE while plugging it in, once (it starts in the mode it was last used in; until then its card says "Nothing received yet"). **The controller's own lights show the box's state** (new, not yet seen on a real controller): on a Launchpad Mini a pad with a clip or a shader is amber and the playing one green, Vibes and the preset in use are green, the blackout button is dim red and turns full red while the screen is black. The card has a switch **Lights**, a **Brightness** choice (low at first, for a dark room) and **Test lights**, which lights every pad in turn. On a nanoKONTROL2 and a MIDI Mix the lights are off until you switch them on: the nanoKONTROL2 needs **LED Mode set to External in Korg's editor first**, and the MIDI Mix's light messages are from no Akai document. If the card says "Lights need the box's installer to run once", the box has an older service file: run the installer once. Details and what each light means: [pvj/MIDI.md](../pvj/MIDI.md), "Lights". **These three layouts are written from documents and one recording and have not been tried on the real controllers yet**: move each control once and watch it light up on the card; one that does not light has a wrong number. **What changes when you update a box that already used one of these controllers:** what you taught it still works and still wins; every control you never taught now does something; taught faders for opacity, volume and the speeds now wait until they reach the box's value; and the old built-in numbers no longer apply to these three (on a Launchpad Mini the pads that used to play pads 1 to 36, stop and blackout now do what the card shows). Switch a controller's Standard layout off to get the old behaviour back. The full tables, and how to add a controller by writing one file, are in [pvj/MIDI.md](../pvj/MIDI.md#controller-profiles). Any other controller is taught with Learn on the same page.

The Projection mapping card on Mix, with a grid and a quad:

![Projection mapping](images/ui/mapper.png)

Projectors and the schedule (here with projector power, a clip, a start script and a blackout):

![Projectors](images/ui/projectors.png)

Each projector is one row: its name, what it says it is (maker, model) and its address, and one line with its state, input, mutes, lamp hours and warnings (also on System > Health). It has **one power button** that follows the state: **Turn on** while it is off, **Warming up...** and **Cooling down...** (which cannot be pressed) in between, **Turn off** while it is on, which asks first ("Turn off Main wall? It needs about a minute to cool before it can come on again."). With two or more there are **All on** and **All off**, which ask first and name how many. **Input** is a list that switches as soon as you choose; an input nobody named yet says what kind of socket it is ("Digital 2 (HDMI or DVI)"). If no inputs are known the row says so and has **Read inputs**. A projector that does not answer says what to check and has **Try again**. Under **More**: Blank the picture / Show the picture, Mute the sound / Unmute the sound, **Name the inputs** (one field per input, **Show** switches to that input so you can see which socket it is, one **Save names**; naming does not switch the projector), Check now, Read details again, **Edit** (name, address, port or password, in place, keeping the input names) and **Remove** (asks first). The picture above may be older than this. This is built from the published PJLink standard and tested against a fake projector only: **no real projector has been tested**. Details: [pvj/PROJECTORS.md](../pvj/PROJECTORS.md).
![Schedule](images/ui/schedule.png)

The Schedule page starts with the box's own time ("Box time now: Sat 3 Oct, 17:30 (PDT)") and what happens next ("Next: today 18:00, Start Vibes"). Entries are listed in time order, with their days in words ("Every day", "Mon to Fri", "Sat, Sun"). **+ Add an entry** asks for the time, the days (seven buttons, with **Every day**, **Weekdays** and **Weekend** above them), what happens (Play a clip, Start Vibes with a choice of set when there are several, Apply a Room scene, Projectors on, Projectors off, Screen to black, Screen back on, Stop playing; Old start script is under Advanced in that list) and a note for yourself. **Edit** and **Remove** are under **More** on each entry. A choice whose feature is switched off is marked ("Start Vibes (Vibes is off)"); choosing it says "Vibes is switched off, so this will do nothing." with a **Switch Vibes on** button that does it there. An entry runs only if the box is on at that minute; a missed entry is not caught up.

Check the box time before relying on the schedule: a Pi has no battery clock, and until the network sets the time the clock is wrong. When the clock was not set from the network the page says so in red and, where the box can, offers **Set the box clock to this phone's time**.

### Running the room (beta)

This is for the people who run the room. You do not need to know how the box works.

Open the panel on your phone and tap **Room** at the bottom. (If you joined with a presenter code, you start there.)

- **Start ambience** is the big button at the very top (it is there when the owner has switched Shaders and Vibes on). Tap it and the box starts drawing slow moving pictures, one after another, for as long as you like. The button then reads **Ambience is playing: Aurora. Tap to stop**; tap it to stop. **Next one** beside it moves on to the next picture. If there is a small list beside the button ("Set: Ambient", "Set: Show"), it chooses which collection of pictures plays; leave it alone unless you were told otherwise. Ambience is the same thing the owner's pages call **Vibes**: one feature, two names.
- **Scenes** are the big buttons under it, with names such as "Console night" or "Film". Tap one. The projectors it needs switch on, choose the right source and the box starts what belongs to it. A projector takes about a minute to warm up; the line under the buttons says how it is going ("Main wall: switching on (warming up)") and then how it went, for example "Main wall: on, input Console. Painting wall: no answer."
- Tapped the wrong scene? Just tap the right one. The newer one takes over.
- Each **wall** has its own box: a word that says how it is (On, Off, Warming up, Cooling down, No answer), **On** and **Off**, a button for each **source** (the console, the box, and so on), **Mute picture** and **Mute sound**.
- **All off** at the bottom switches every projector off. It first asks "Turn off all projectors?"; tap **Turn off** to do it or **Keep them on** to leave them, so it cannot happen by accident. Projectors cool down for a minute or two before they are really off; leave them plugged in.
- "No answer" means the box cannot reach that projector. Check that it has power at the wall and that its network cable is in. Then tap the scene again.
- If you joined with a guest code you can look, and the buttons are not there. You still read whether ambience is playing, and which picture.

Setting it up is for a full-access device: switch **Projectors** and then **Room** on (System, each on its own page), add the projectors under System > Projectors and give their inputs labels, then on the Room screen, under "Set up the room", make the groups (the walls) and the scenes. A scene can also run from the schedule ("Apply a Room scene"), from OSC and from a MIDI control. Details: [pvj/ROOM.md](../pvj/ROOM.md).

**Not tried in a real room yet.** No real projector has been controlled by any of this; it has been tested against a stand-in projector in software only.

### Shaders and Vibes (beta)

A shader is a small program that the box's graphics chip runs to draw a moving picture: there is no clip, so it never ends and never repeats exactly. The box takes shaders in the **ISF** format (Interactive Shader Format, `.fs` files) and comes with 40 of its own: 25 slow, quiet ones made for ambience (the first ten and the **Ambient** family), and 15 stronger, rhythmic ones made to be played by hand (the **Performance** family: tunnels, bars and tiles on a beat, beams, checker and stripe bends, bursts, an oscilloscope, glitch blocks, dots, mirrors). The Performance ones are in the list to play, but **not in the Vibes rotation until you switch one in**, so one tap on Vibes never starts something that flashes. None of them flashes more than 3 times a second; a few have a switch **Fast** that doubles that, off unless you turn it on (do not, where someone may be sensitive to flashing light). The list with what each one shows and what can be set is in [pvj/SHADERS.md](../pvj/SHADERS.md).

Switch it on under System > Shaders and Vibes (beta, off by default; not offered on a Raspberry Pi 3). Everything about shaders is on that one page. It also opens from the **Shaders** link next to the Vibes button on Live, and Back then returns to Live.

- **Vibes** is the big button that then appears on the **Live** screen (presenters and full-access devices), and on the **Room** screen under the name **ambience** (see "Running the room"; it is the same thing). It reads **Start Vibes**, and while it runs **Vibes is playing: Aurora** (tap to stop). One tap and the box plays shaders endlessly: a shuffled order, three minutes each, a dip to black between them, and slightly different speeds, sizes and colours every round. It ends when you press **Stop**, press the button again, or play anything else; it never takes the screen back by itself. **For staff this is all there is to know: one tap starts the ambience, one tap stops it.**
- While a shader is on, Live also has **Previous** and **Next** beside the button, and a strip with **Speed** and the shader's first four controls (on a laptop the strip is a column of its own on the right, with the pads and the transport on the left). If the box has more than one set of shaders, a list beside the button chooses the set to play.
- The **Shaders and Vibes** page. On a phone, top to bottom:
  1. **On screen now**: the shader's name, how long until the next one, and how the box is coping, as three lights and a sentence (**Running smoothly**, **Close to the limit**, **Dropping frames: try a lower picture detail**). Full access has **Picture detail** right there (the choices are this board's own; the usual one is marked). Then **Previous**, **Start Vibes** or **Stop Vibes**, and **Next**. If Vibes ended because something else was played, it says so here.
  2. **Controls** for the shader that is on. See "Performing with shaders" below.
  3. **Presets**: your saved versions of this shader.
  4. **Shaders**: the library. A box to find one by name, and choices by how much work it is, by where it came from, and by family (Ambient or Performance). Each row says how much work it is, with the numbers measured on a real board where there are any ("Light work. 7.5 ms on a Pi 4 at 720 lines": the time the graphics chip needs for one picture; all 47 that come with the box were measured on a Raspberry Pi 4). **Play** shows just that one until something else plays. Full access has the switch **In Vibes** per shader, applied when tapped, and **Remove** for its own files (it asks first). A shader the box found too heavy says **Too heavy on this box. Left out of Vibes.**, with **Put it back**; one the graphics chip refused shows the reason in red.
  5. **Vibes settings** (full access): **Each one stays for** (30 seconds to 1 hour), **They play** (shuffled, or in the order they were put in) and **Change speed and colours a little each round**, all applied when tapped. **More than one set of shaders** adds a second list (see below). Under **Advanced**: the switch that leaves out shaders that are too heavy for this box, **Allow faster than the flash limit** (off unless you switch it on: it lets Performance shaders flash faster than 3 times a second, which can trigger seizures in people with photosensitive epilepsy; leave it off in a room open to the public), and **+ Add a shader file (.fs)** (generator shaders only; a file the box cannot show is refused, with the reason under the button).
  6. **Controllers** (full access): the MIDI switch and the pads or knobs for Vibes (start and stop, next one, how long each stays). For a lighting desk: the DMX switch, the channel Vibes is on (the ninth from the start channel), its ranges (50 to 99 stop, 100 to 149 start, 150 to 199 next) and the level the box last received.
- A presenter can start and stop Vibes, choose the set it plays, step to the shader before and the next, play one shader, move its controls and put on a preset. Only full access saves presets, edits sets, changes picture detail, adds and removes files. A guest sees what is playing and the list. Nobody is shown a control they cannot use.
- The list holds the project's own 40 shaders, then seven from Vidvox's public **ISF-Files** collection (names starting with `isf-`; somebody else's work under the MIT licence, each with its author's credit), then your uploads. The `isf-` shaders are there to play by hand and are **not in Vibes** until a full-access device switches one to In Vibes: most are still pictures, and the two that move (`isf-ridgelines`, `isf-simplex-noise`) are heavy work for a Raspberry Pi 4. More ISF generators (from ISF-Files, the ISF editor's web site or VDMX) are added one file at a time with Upload; filters, transitions and shaders that react to sound are not supported yet ([pvj/SHADERS.md](../pvj/SHADERS.md) says which and why).
- Opacity, the fades, Blackout, the overlay picture and the projection mapping all work on a shader as they do on a clip.
- Vibes can also start by itself: choose **Vibes** under System > At power-up, add a **Start Vibes** entry to the schedule, send the OSC address `/pvj/vibes`, assign **Vibes on / off**, **next shader** and the time each shader stays to a MIDI controller with Learn, or use the ninth DMX channel (see [pvj/MIDI.md](../pvj/MIDI.md) and [pvj/DMX.md](../pvj/DMX.md)).

On a laptop (from about 900 px wide) the page is a workspace: the list of shaders is a column of its own on the left that scrolls by itself, what is playing, its controls and its presets stay in view beside it, and the Vibes settings and controllers are a third column on a wide window (from about 1200 px).

No page sends you to another one for its controls: System > Projection mapping holds the mapping controls (they are also on Mix), and System > Room holds the Room screen's scenes, walls and set-up under its switch.

#### Performing with shaders

Play one shader (its **Play** button, or **Next** and **Previous**) and the page is an instrument. Everything below changes the picture while it runs: nothing restarts, and Vibes keeps going if it is running.

- **Controls every shader has**, at the top: **Speed** (0 to 4 times; the mark shows the shader's own pace, and **Freeze** stops it and gives the speed back when switched off; for a Performance shader, which flashes, Speed stops at 1), **Colour turn** (shifts the whole palette around) and **Brightness trim**. Each has **Reset**.
- **The shader's own controls**, drawn by what they are: a slider for a number, a switch, a row of buttons for a choice of up to five (a list for more), a colour (tap the swatch for the colour, the slider beside it is its alpha), a square to drag in for a point (the arrow keys nudge it), and a button for a flash. The picture follows while you drag. A double tap on a slider, or **Reset**, puts it back; **Reset all** puts everything back. If the box does not take a value, the reason appears beside that control.
- **Presets** keep what you have set, per shader. Full access: type a name and press **Save as preset**; under **More** a preset is renamed or deleted (it asks first). Anyone who can play taps a preset to put it on. The one in use is filled in; after you move a control it is outlined and the card says **Changed**. A preset called **default** is what a plain Play and Vibes use for that shader.
- **Sets** are lists of shaders for Vibes, each with its own time, order and variation: quiet ones for opening hours, strong ones for a show. A box starts with two: **Ambient** (the usual one, the quiet shaders) and **Show** (the Performance shaders), and a list beside **Start Vibes** chooses which one plays. The card lists the sets, and **+ Add a set** makes another. If you delete down to one set, nothing on the page mentions sets any more. Tap a set to edit it, and the switches in the list of shaders put shaders in or out of **that** set (the list says which). **Start Vibes on this set** plays it now; **Make it the usual set** makes it the one the Vibes button, the schedule and controllers start. A presenter chooses the set to play from the list beside the Vibes button.
- **A MIDI controller** is taught beside the thing it controls: the small **MIDI** button next to a control, next to Speed, under Previous and Next, and under Presets > More for preset 1 to 8. It opens a box that says what is on it now, with **Remove** and **Teach a control** (then move the knob or press the pad). A knob is not tied to one shader: knob 1 follows the first control of whichever shader is playing, knob 2 the second, up to eight. Colours and points have no knob.
- **Keys on a laptop**, while no field has the cursor: **Space** starts and stops Vibes, **left** and **right** go to the shader before and the next, **1** to **8** put on that preset (the number is shown on each preset).
- **Previous** and **Next** go through the shaders of the usual set, in its order, whether Vibes is running or not.

**Not tried on a real box.** The page has run in a browser against the software only; nobody has performed with it on a Pi yet. How quickly the picture follows a dragged control there, and whether it hitches while it does, is not known.

**Measured on one Raspberry Pi 4** (2560 x 1440 at 75 Hz, all 47 shaders that come with the box): at 540 lines 43 of them run without a dropped frame; at 720 lines 33 keep up ("Light work" in the list) and 11 do not ("Medium work": Aurora, Tide, Lantern, Bloom and others); Drift, Nebula and `isf-ridgelines` drop frames even at 540 ("Heavy work"). So a Pi 4 draws 540 lines unless you choose otherwise and is not offered 1080, and Drift and Nebula are not in the rotation until you put them in. If Picture detail on your box says 720 and the usual choice marked there is 540, choose 540: Vibes then plays every shader of its sets smoothly. While Vibes runs the box watches for dropped frames: a shader that keeps dropping them is passed over and left out until you put it back. A Pi 5 and a PC have not been measured. Watch the screen the first time; if the picture stutters, choose fewer lines under Picture detail. Details: [pvj/SHADERS.md](../pvj/SHADERS.md).

### Effects (beta)

An effect changes the picture that is playing: it mirrors it, turns its colours, breaks it into big pixels, draws its outlines in light. It is a small program for the box's graphics chip, like a shader, but where a shader draws a picture from nothing, an effect needs a picture to work on: a clip, a stream, a live input, or a shader's own picture. Effects come with the **Shaders and Vibes** module (System > Shaders and Vibes), and they are in the same **ISF** format; the kind of file is called a filter.

- **One effect at a time**, over whatever plays. It never takes the screen: the clip or the shader goes on playing under it.
- **It stays on when the clip changes**, so you can play pads through one look. It comes off when you press **Off** or **Stop**, when the module is switched off, and when the player restarts. It does not come back by itself.
- **Over a shader and over Vibes.** An effect goes on over a shader as over a clip, and it stays on when you choose a shader, start Vibes, or Vibes moves to its next shader: the effect changes the shader's picture. While Vibes dips to black between two shaders the effect stays on and dips with the picture. Stopping Vibes clears the screen and takes the effect off with it. If the graphics chip refuses an effect over a shader, the shader stays on the screen, the effect is off, and the card says why; the box does not try that same effect over a shader again until it is restarted, so the screen does not flash each time. While an effect is on, Vibes does not leave a shader out for dropping frames (it cannot tell whether the shader or the effect is the cause); the Effects card says when the two together are too heavy. If the two together go on dropping frames for about twenty seconds, the box takes the effect off by itself, leaves the shader playing, and says so on the Effects card; it does not put the effect back. (How heavy is too heavy, and how long, are first guesses until this has run on a real box.) A shader with an effect on it is more work than either alone, and **this has not been tried on a real box yet**: watch the three lights on the Effects card, and use a lighter effect or a lower Picture detail if they turn red. One more thing to know: ambience on the Room screen, a scheduled start and autostart are Vibes too, so an effect that was left on stays on over them. The same rule holds there with nobody watching: an effect that makes the pictures stutter is taken off after about twenty seconds. Still, take the effect off (or press Stop) before leaving the box to run by itself, until this has been tried on a real box.
- **Amount** is the one control every effect has: from 0 percent (the picture as it is) to 100 percent (the effect in full). Fade an effect in and out with it. One thing to know on a small box: while the effect works at a lower size than the clip (see Effect detail below, which a Raspberry Pi 4 does for a 1080p clip), the whole picture is the slightly softer one at every Amount above 0, also the part the effect leaves alone. So the picture steps from sharp to slightly softer the moment Amount leaves 0 percent, and back when it returns to 0.
- **On Live** there is a small strip under the Vibes button: the effect's name, **Previous**, **On** or **Off**, **Next**, and **Amount** while one is on. **Effects** opens the card on Mix.
- **On Mix** the **Effects** card has everything: the list of effects (find one by name, or show only the light ones), **Put on**, then the controls of the one that is on: Amount first, and the effect's own controls, drawn by what they are, as on the Shaders page. Under them are its **presets** (full access saves and deletes them; a preset called **default** is what Put on uses). On a laptop the list is a column of its own beside the controls.
- **While a shader has the screen, or nothing with a picture is playing, the strip and the card say so** and offer nothing to press: play a clip first.
- Opacity, the fades, Blackout, the overlay picture and the projection mapping work on a picture with an effect as on any other. An invert under a Blackout is black.
- **Effect detail** (on the Effects card; full access changes it, and it applies as you tap) is the size an effect works at. A small box cannot run an effect over every pixel of a 1080p clip thirty times a second, so the effect may work on a smaller copy of the picture, which the box then scales back up to the screen. The choices are **Automatic**, **540 lines**, **720 lines** and **Full**. Leave it on **Automatic (recommended on this box)** on a Raspberry Pi 4: the box then works at 720 lines, the most this board keeps up with over a 1080p clip, and at 540 for the one heaviest effect (Edge blowout) and for effects you add. A clip that is already that small is not touched. One line under the setting says what is happening now: "Automatic: working at 720 lines for this 1080p clip." **What it costs in look:** the picture is a little softer, and fine patterns (the dots of a halftone, thin outlines, small tiles) come out larger, one and a half times at 720 lines over a 1080p clip and twice at 540. **Full** is for a strong box, or for when you want the finest pattern and accept a stutter. On a Raspberry Pi 5 and on a PC the setting starts at Full, because no effect has been measured on them yet; if effects stutter there, choose 720 or 540 lines. Amount at 0 percent always shows the clip itself, at its own size; anything above 0 shows the softer picture (see Amount above).
- **How heavy an effect is.** A shader is drawn at 540 lines; an effect works on every pixel it is given, so a 1080p clip at full size is four times the work of a 540 line shader for the same kind of sum. The list says how much work each one is. For the effects that come with the box that was measured on a Raspberry Pi 4: all of them keep up with a 720p clip at full size, and with a 1080p clip at 720 lines (Edge blowout at 540), which is what Automatic does. For an effect you add it is counted from its text. While an effect is on, the card shows how the box is coping; **Too heavy with this clip** means frames are being dropped: lower Effect detail (the card names the next step down), choose a lighter effect, or play a smaller clip.
- **A MIDI controller** is taught beside the thing it controls, as on the Shaders page: the small **MIDI** button next to Amount, next to each of the first eight controls a knob can drive, and under Previous, On and Next. On the three controllers the box knows, a few spare controls already do this (see [pvj/MIDI.md](../pvj/MIDI.md)).
- **Flashing.** Some effects move by themselves, and an effect over a fast clip can flicker. The box keeps an effect that moves by itself, or that says it flashes, at its own pace or slower, unless **Allow faster than the flash limit** is switched on (Shaders page, Advanced; leave it off in a room open to the public). With that switch off, a controller's buttons can also switch effects on at most about three times a second, however fast they are pressed; Off always works at once. **It cannot see what an effect does to the picture**: an invert tapped on and off quickly, or a posterise over a strobing clip, is a flash that nothing here limits. Mind people who are sensitive to flashing light.
- The list holds the project's own twelve effects, then the ones from Vidvox's public **ISF-Files** collection (names starting with `isf-`; somebody else's work under the MIT licence, each with its author's credit), then your uploads (**+ Add an effect file**, full access; one `.fs` file that reads the playing picture in one pass). A file is taken only if the box can count from its text how much work it does for one picture point: its loops must say plainly how often they run. If not, the box says which line of thinking stopped it.
- A presenter can put an effect on, change it, step through them and take it off. Only full access saves presets and adds or removes files. A guest sees what is on.

**What has and has not been tried.** An effect over a shader has run only in the project's tests on a software graphics chip, never on a Raspberry Pi. Every effect that comes with the box has been run on a Raspberry Pi 4 over a 720p and a 1080p clip and timed, and the pages have run in a browser in the project's tests. All of that was judged from snapshots and the player's own counters: nobody has watched an effect on a display or a projector yet. A Raspberry Pi 5, a PC and a projector at 60 Hz have not been measured. One thing known not to work well yet: an effect put on, changed or taken off while the clip is **paused** does not show until the clip moves on by a frame.

## 6. Keep it safe and recoverable

- **Keep the show network private.** The panel is protected by a PIN and per-device tokens, but it is not built to face the internet. OSC, DMX and MIDI are off until you switch them on; OSC and DMX only accept senders on private networks (plus ranges you add).
- **A code from a controller is a way in for whoever can touch the controller.** It is off unless you switched it on (System > People and codes), the full access kind is a second switch, and neither can be switched through remote support or by a settings file. Leave both off in a room where strangers can reach the box's controllers.
- **Power cuts.** The read-only root protects the system disk from a pulled plug (`sudo pvj-rootfs enable`, then reboot). Not tested on a real board.
- **Remote support** is off until you switch it on (System > Remote support), and even then only open while you run a session you started (time-limited, panel only, visible on every device, stoppable). See [REMOTE-SUPPORT.md](REMOTE-SUPPORT.md).
- **Updates** are signed bundles (`pvj-N.N.N.tar.gz` with its `.sig`; a `.sha256` is optional). System > **Updates** (full access) installs one from a `pvj-update` folder on a USB stick, or one you upload there; it is checked against your signing key, an older version is refused, and if the panel does not come back the box goes back to the version before by itself. The panel and the player restart during an update. From a terminal: `sudo pvj-update`. See [pvj/README.md](../pvj/README.md#updates-and-rollback).
- **Player stuck?** System > About and power > Restart player (it asks first: the picture stops for a few seconds) asks it to quit and systemd brings it back. If mpv ignores that, run `sudo systemctl restart pvj-player` on the box.
- **Network changes** always revert by themselves unless you confirm them. Test them with a keyboard and monitor on the box, never over SSH on the only link.

**Hostile drives and files.** A USB stick or an upload can hold a file that is named `.mp4` but is really a playlist or a script for the player. The player is started so that it plays media only: it does not follow references inside files (playlists, EDL), load sidecar subtitle or audio files, load scripts or run youtube-dl. This was tested on a Raspberry Pi 4 with fake playlists. A drive is also mounted read-only with `nosuid,nodev,noexec`, and only the top level of a drive is listed, up to a limit.

## Health

System > Health says in plain words whether the box is well: the power supply (a Pi warns when the voltage drops; any drop is remembered until the next reboot, because it is the most common cause of odd stutters, network drops and damaged SD cards: use the official supply, 5 V 3 A for a Pi 4), the temperature (above 80 C a Pi slows down), the player (decoded in hardware or software, and dropped frames a second while playing), the load, and whether the helpers are running. It also lists the addresses to open the panel from another device, and a full-access device can put the address on the display for 2 minutes.

## Settings file, diagnostics and factory reset

Three cards under System > Backup and reset, for full-access devices only. Factory reset is last, under the heading Danger.

**Settings file.** *Export settings* saves the box's settings as one file (`nxlx-settings-<box>-<date>.json`): pads, modules, theme (and any themes you added under Look, whole), mix, OSC, schedule, streams, DMX and MIDI, autostart, sound output, the picture over the video, projectors, the mapping and sync. *Import settings* loads such a file: it is checked first (a file from a newer version is refused, one from an older version is brought up to date, and every value goes through the same checks as the panel's own forms), a copy of the present settings is kept on the box (`settings.json.before-import-<time>` beside the settings file, the last three), and only then are the settings replaced. One wrong value stops the whole import and nothing changes. The clips themselves are not in the file: pads and the schedule refer to them by name.

What happens to secrets:

| | Export | Import |
| --- | --- | --- |
| The PIN, paired devices and their tokens | never in the file | the box keeps its own; a file that holds any is refused |
| Remote support (server, keys, address, history) | never in the file | the box keeps its own |
| Guest, presenter and support codes | never in the file (they are not saved anywhere) | not affected |
| Projector passwords and the secrets in a stream address (a name and password, an RTMP stream key, an SRT passphrase or stream id) | left out, unless you switch on *Passwords in the file* | a file without them keeps the password the box already has for the same projector at the same address, and the full address the box already has for the same stream; otherwise type it in again |

Without the tick a stream address is shortened to what is not secret: the name and password go; an RTMP address loses the last part of its path (the stream key) and everything after a `?`; an SRT address keeps only plain connection options such as `mode` and `latency`, so the passphrase and the stream id go; an RTSP address loses query values whose name sounds like a secret (password, token, key, auth, sign). A shortened address does not play on a box that never had the stream: add the stream again there, or export with the tick. On the box the file came from, an import keeps the full address it already has. A file with passwords holds them, and the full addresses, in plain text. The streams list and the player status in the panel hide the same parts, for every device.

**Diagnostics.** *Download diagnostics file* saves one file to send to whoever is helping you: the version, the board, the screens, module states, the Health card's data, the last update, and the settings with every secret removed (no PIN, token, code, key, password or stream login; a stream is shown only as where it comes from). The panel runs without system rights and is not allowed to read the system log, so the file says so instead of holding log lines; on the box, `sudo journalctl -b -u 'pvj-*'` prints them. If a box is set up so that the panel can read the log, the lines are included with PINs, codes and logins removed.

**Factory reset.** Choose whether the clips stay or are deleted, then *Reset to factory settings* and answer the question that appears in its place (*Reset this box* or *Keep everything*). Playback stops, every setting goes back to how a new box starts, every phone, tablet and guest is unpaired (this one too), codes and a running support session end, the themes you added under Look are removed, and the box makes a new PIN, so the PIN screen is back on the display (`sudo pvj-pin` also prints it). Deleting the clips removes the media files in the box's own media folder and nothing else; a USB drive is never emptied, and if the box keeps its clips on a USB drive (`PVJ_MEDIA_DIR`) the reset refuses to delete them. What a reset does not touch: the network settings, wired and Wi-Fi (System > Network changes the system's own configuration), anything in `/etc/pvj` (the signing key, fleet support details), and the settings backups that updates keep for a rollback (`/var/lib/pvj/backups`, readable by root only). Those backups still hold the old devices: after a reset, do not run `sudo pvj-update rollback` unless you mean to bring the old settings back, or remove them first with `sudo rm -r /var/lib/pvj/backups`.

Through a remote support session, the settings can be exported without passwords and the diagnostics file downloaded; importing, exporting with passwords and factory reset are refused.

Not tested on a real box yet (2026-10-03): the three cards were tested with the automatic tests and a browser test only.

## 7. Troubleshooting

| Symptom | Try |
| --- | --- |
| The browser says the site is not secure or cannot be reached | Type `http://` in front of the address: the panel uses plain HTTP on your own network, and browsers that try HTTPS first fail |
| A clip stutters | Media > Info on the clip says if it is too heavy for this box; see Prepare your clips |
| Stutters, network drops, odd restarts | System > Health: a "Power" warning means the power supply is too weak. After an unexpected restart, `journalctl -b -1 -e` on the box shows the end of the log before it (the log is kept across restarts, up to 64 MB) |
| The page does not load | Same network as the box? `systemctl status pvj-web` on the box; the address may have changed (check the router's client list) |
| Holding the pad shows no code | The switch under System > People and codes > A code from a controller must be on, MIDI must be on, and the hold must last 3 to 10 seconds and end. `journalctl -u pvj-web \| grep "controller code"` says why it was refused |
| "Wrong PIN" | The PIN changes at every start. `sudo pvj-pin`, or System > People and codes > New PIN from a paired device |
| A pad is grey and says Empty | Edit pads (full access) and assign a clip or a shader |
| A shader pad answers that its shader is not on the box any more | The shader was deleted, or a newer upload has another name. Edit pads and choose a shader again |
| Clip will not play | Check the file plays in `mpv` on the box; on a Pi 5 use HEVC (no hardware H.264 decode) |
| Nothing shows on the projector | `pvj-selftest --play` on the box; check `journalctl -u pvj-player` |
| DMX or MIDI does nothing | Open its page under System: the switch at the top must say On (if it could not start, the reason is on the page); then read the card's status line and `journalctl -u pvj-web` |
| A projector does not answer | PJLink must be switched on in the projector's network menu; check its address and password; a projector that is warming up or cooling down refuses commands for a minute or so (an input change is tried again for 90 seconds by itself) |
| A projector shows no inputs to choose | Many projectors list their inputs only while switched on: switch it on, wait for "On", then tap **Read inputs** on its row (System > Projectors) |
| The mapped picture stutters | Map at 1920x1080 or less on a Pi 4, and leave Edit on the display when you are done (editing costs more) |
| Schedule fires at the wrong time | Check "Box time now" at the top of the Schedule page, and its time zone |
| Someone is helping you from far away | System > Backup and reset > Download diagnostics file, and send it; it holds no PIN or password |
| The box should go to someone else, or start over | System > Backup and reset > Factory reset |

## 8. Not built yet

A crossfade in which both pictures move, a push (both pictures moving), a luma dissolve, ISF transition files, updates from the network, a panel update button, NDI, AES67/Dante, the presenter, importing old mapper files and custom DMX layouts. See [ROADMAP.md](../ROADMAP.md).

**SMPTE ST 2110** is not supported directly and not planned: use a converter from 2110 to HDMI into the live input (USB capture), or from 2110 to NDI once NDI is built.
