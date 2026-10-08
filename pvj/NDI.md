<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# NDI® input (beta, never run against a real sender)

Show the output of Resolume, MadMapper, OBS or any other NDI sender on the box's screen, like a clip. Switch it on under System > NDI input. Information about NDI and its tools is at [ndi.video](https://ndi.video/); the free tools (Test Patterns, Screen Capture, Studio Monitor) are at [ndi.video/tools](https://ndi.video/tools/).

NDI® is a registered trademark of Vizrt NDI AB.

**State on 2026-10-07.** The receiver, its service, the routes and the page are written and tested against a fake library and fake frames. The library layer has met the real NDI library once, on a Mac (see "Measured against the real library"); **it has never run on Linux, on a Pi, or with mpv**, and no picture from it has been on a screen. Whether it is smooth on a Pi 4 is unknown until the steps in `tools/DEVICE-TESTING.md` ("NDI input") are run with a real sender. Everything under "Not verified" below is exactly that. **Whether NDI's licence allows the library on a box like this at all is an open question for the owner** (see "The SDK License Agreement itself").

## Measured against the real library (2026-10-07, macOS only)

Run by the coordinator on the dev Mac against the owner's NDI Tools. **This is not the Pi, not Linux and not mpv**: it says that the structure layouts and constants in `pvj/ndi.py` are right for 64-bit macOS on library 6.3.2, and nothing about aarch64, about Linux, or about a picture on a screen.

- `CtypesLibrary` loaded the `libndi.dylib` inside NDI Scan Converter; its version string was "NDI SDK APPLE 12:49:17 Apr 13 2026 6.3.2.0". Discovery found two senders on the same machine, "FIRESPRAY-31.LOCAL (Scan Converter)" and "FIRESPRAY-31.LOCAL (Test Patterns)", and `clean_sources` kept both.
- **Scan Converter:** a first frame at once, 3360 x 1890, UYVY, line stride 6720, rate 30000/1000, frame format 1 (progressive); `check_frame` accepted it. Straight from the library: 155 frames in 6 seconds, the middle gap 39.1 ms, the worst 55.0 ms, none dropped by the library. Through `Receiver` into a pipe read by a Python reader: 23.6 whole frames a second over 10 seconds (300 MB a second), state "playing", about 56 percent of one core for the whole process, and a clean close.
- **Test Patterns:** 1920 x 1080, UYVY, stride 3840, 30000/1001, format 1; **one video frame and then none in 6 seconds**. The owner confirms it is a still pattern with a test tone, so after the first picture only sound arrives. A still source is therefore normal, and the code now says "still" for it, not "waiting" (see "How it works"). The tone is also a ready reference for sound later.

## What the box needs that this repository does not contain

The NDI runtime library, `libndi.so.6`. It is proprietary, it is not in this repository and it is not in the image. Somebody with the right to accept NDI's licence fetches the NDI SDK for Linux from [ndi.video](https://ndi.video/) (the download asks you to agree to its licence), and copies one file from it to the box:

    sudo pvj-ndi-runtime install "/path/to/NDI SDK for Linux"      # the unpacked SDK folder, or the libndi.so.6 file itself
    sudo pvj-ndi-runtime status
    sudo pvj-ndi-runtime remove

**Before installing it: NDI's licence may not cover a box like this one: read it before you install the runtime. This is not legal advice.** The agreement names kinds of product its free licence does not cover, and a small box built for one job may be one of them; see "The SDK License Agreement itself" below. The owner of the box decides, and asks NDI if in doubt.

The command picks the file for this board from the SDK's `lib/` folder, refuses a file built for another kind of processor and says which ("this file is for x86_64, this box is aarch64"), and copies it to `/opt/pvj-ndi/libndi.so.6` (root's, read-only for every service). It needs root on the box (a keyboard or SSH), on purpose: see "Why there is no upload button". It does not follow a link given as the file, does not wait on a pipe, reads no more than the size it allows, and keeps its copy private until every check has passed. Uninstalling the project leaves `/opt/pvj-ndi` and the `pvj-ndi` account and group in place; `install.sh --uninstall --purge` removes the folder, and `deluser pvj-ndi` the account.

The box also needs `avahi-daemon` and `libavahi-client3` (Raspberry Pi OS has both; the installer adds them when the network allows). The NDI library finds senders through them.

Until the file is there, the NDI page says "The NDI runtime is not on this box yet" and shows these steps; its switch, its list and its address form are on the same page.

## How it works

    sender --NDI--> libndi.so.6 (in pvj-ndi, its own account and sandbox)
                      | UYVY frames, whole frames only, newest wins
                      v
                  /run/pvj-ndi/ndi.fifo  --read by-->  the one mpv (rawvideo demuxer, uyvy422)

- `pvj-ndi.service` runs `pvj/ndi.py` as the account `pvj-ndi`. It loads the library with `ctypes` (standard library; nothing is compiled), asks it for the senders on the network, and, when the panel asks for one, receives its frames.
- Frames are asked for in the library's "fastest" format, which is 8-bit UYVY (UYVY plus a separate alpha plane when the sender has alpha; the alpha plane is not read). No colour conversion happens in Python or in the library.
- The helper writes raw frames into a pipe; the player plays the pipe with mpv's raw video demuxer, as it already does for the HDMI capture input (`pvj/capture.py`, measured on the Pi 4 at 1080p30). The player never loads the library.
- The size and frame rate are not known until the first frame. So: the panel asks the helper to open a source; the helper connects and waits up to 6 seconds for a frame; it answers with the width, height and rate; the panel loads the pipe in the player with exactly those; the helper starts writing when the player opens the pipe.
- **A sender that changes size or rate**: the helper stops writing (the raw pipe has one fixed layout) and says "changed"; the panel, which looks at the helper once a second while NDI is on screen, opens the source again and reloads the pipe. The picture is gone for the second or two that takes.
- **A still picture** (a test pattern, a paused output, a slide): the sender sends a frame and then nothing new. That is normal and is left alone: the page says "On the screen, a still picture", and nothing is reloaded however long it lasts.
- **A sender that goes away**: only when the library itself reports that the connection dropped does the page say "The connection to the sender dropped; waiting for it". The helper keeps the connection (the library reconnects by itself), the screen holds the last frame, and when frames return they continue; if they return in another size, see above. If the helper has nothing open (it started again, or the sender was gone at a reload), the panel tries to show the source again after 3 seconds, then 6, 12 and so on up to once a minute, and writes the reason in the log once.
- **A source is a name on a machine.** The id the panel plays by is made from the checked name and the address of the machine that announces it (not the port, which changes when a sender restarts). A second machine announcing the same name is another row and can never take the place of the one that was chosen. The price: a sender whose address changes (DHCP) becomes a new row and has to be chosen again; staying with the machine was preferred to following the name.
- **Too many senders**: at most 64 are listed. Which ones does not depend on the order they are announced in: the one on the screen first, then senders at an address the owner added, then the rest by name. The page says when the list was cut and that adding the sender's address brings it in.
- **A source that ends by itself** (a format this input does not show, or the player moved on) is no longer "what plays"; the page keeps the reason until something is played again.
- **Something else is played while a source is still connecting** (it can take seconds): the later choice stands; the source is let go and the screen is not touched. Everything that loads or clears the player through the panel's own calls cancels a connecting source first, and waits if the NDI pipe is being loaded at that very moment. Two paths load the player without going through that door (a client box following its server in "Boxes in step", and a shader swap); for those the pipe simply loses its reader and the helper lets the source go.
- **Slow screen, fast sender**: the helper holds one frame; a newer one replaces it and the old one is counted as dropped. A frame is never written in part. The counts (received, shown, dropped here, dropped by the library) are on the page and in the status route.
- **Interlaced senders are refused**, with a plain message ("the source sends interlaced video, which this input does not show; set the sender to progressive"). That covers all three interlaced frame types the library has: a frame of two fields woven together (value 0), and single fields (2 and 3). Only a progressive frame (1) is shown. Why not have the library de-interlace: the SDK's NDI-RECEIVE page says of `allow_video_fields`, "If your application does not like receiving fielded video data, you can specify false to this value, and all video received will be de-interlaced before it is passed to you. ... The implied value is true when color_format is NDIlib_recv_color_format_fastest", and of the fastest format, "The `allow_video_fields` option is assumed to be true in this mode". So de-interlacing in the library means giving up the fastest format (the alternative with UYVY hands frames with alpha over as BGRA, converted), for senders that Resolume and MadMapper are not. The helper therefore asks for fastest with fields allowed, said outright, and refuses what is not progressive. **To be confirmed on a device with an interlaced sender** (step N8): which of the three values really arrive, and that the message is what the page shows.
- **Sound: not built.** The helper asks the library for video only. See "Open design questions".

## Routes and roles

| Route | Role | What |
| --- | --- | --- |
| `GET /api/ndi` | view | The runtime (present, version, a problem in words), the helper, the sources found, the saved addresses, what plays and its counts |
| `POST /api/ndi` `{"action": "add_address", "address": "192.168.1.20"}` | full | Also look for senders at this address |
| `POST /api/ndi` `{"action": "remove_address", "address": "..."}` | full | Forget it |
| `POST /api/play` `{"ndi": "<id>"}` | live | Show a source. The id comes from `GET /api/ndi`; a name is never taken |

Settings: `"ndi": {"addresses": []}` (schema 14). The switch is the module `inputs-ndi` (off by default, D43).

## Safety

Everything that arrives from the network is treated as hostile, and the code that reads it is a closed-source library, so the design assumes the helper can be taken over by a bad packet and limits what that would give.

- **Its own account and group** (`pvj-ndi`). It is not in group `pvj`, so it cannot reach the player's control socket (mpv's control channel can run programs), the PIN, the settings or the media. The player and the panel are each given the extra group `pvj-ndi`, which lets them read the helper's folder and nothing else.
- **Its sandbox** (`install/pvj-ndi.service`): no capabilities, no devices, a read-only system, no home folders, a `/tmp` of its own held to 8 MB, one writable folder (`/run/pvj-ndi`), at most 512 MB of memory (what it writes to `/run` counts), 64 tasks and three of a Pi 4's four cores (so the player always has one; figures chosen by reasoning, none measured), and **network addresses limited to private ranges, link-local and multicast** (`IPAddressDeny=any` with an allow list), which is meant to keep the library from talking to the internet at all. `tests/test_units.py` pins every line; that the lines do on a device what they say is step N9 of the device steps, not yet done.
- **The helper answers only the panel's account** on its socket (`SO_PEERCRED`), with the same small server the other helpers use.
- **The panel does not trust the helper's answers.** One deadline bounds a whole question and answer, however slowly the answer trickles in; an answer nested deeper than a real one is refused before it is read; every name, number and sentence in it is checked again (and scrubbed of control and direction-changing characters) before it reaches the page; and no answer, or fault, of the helper can stop the panel from starting.
- **Off means no NDI code running.** When the module is switched off after having been on, the helper ends and systemd starts a fresh one that loads nothing until the module is on again.
- **The system bus stays reachable** from the helper, and has to: NDI's library finds senders through `libavahi-client`, which talks to `avahi-daemon` over D-Bus and no other way.
- **The pipe.** Only the helper's service makes and removes it. Before the player is told to read it, the panel looks that it is a pipe and belongs to the helper's account (a look that can be raced, so a tripwire, not a guarantee; the guarantee is that the player reads whatever is there as raw video only). mpv's own source (`stream/stream_file.c`, read on 2026-10-07 from its master branch) opens a file for reading without blocking and waits for data in a way a later command interrupts, so a helper that never writes does not hold the player; whether the older mpv on Raspberry Pi OS Bookworm (0.35) does the same was not determined and is step N11.
- **Names**: a source name from the network is shown and used only if it is valid UTF-8, 1 to 128 characters, with no control, format (bidirectional overrides, zero width) or line-separator characters. It is written one way (accented letters composed, every kind of space as a plain one), so two rows cannot look alike while being different, and a pile of accents on one letter is refused. At most 64 sources are kept. The panel plays by an id, a hash of the name and the announcing machine made by the helper, never by a string from the request.
- **Frames**: width, height, line stride and format are checked against fixed bounds (16 to 3840 by 16 to 2160, an even width, a stride of at least two bytes per pixel and at most a bounded excess) before a single byte is read from the library's buffer; the byte count read is computed from the checked values only.
- **Addresses** added by hand are IPv4 literals in a private range, at most 16, and are stored exactly as checked. The library is never given a host name or a string with a comma.
- **The library file** can only be put in place by root on the box.

What is NOT covered: anyone on the show network can announce a sender, with any name, including the name of a real one. Keep the show network private, as for streams and OSC.

### Why there is no upload button

A library is a program. A route that took the file from a browser would let any full-access phone run its own code on the box, which nothing in the panel can do today (updates are signed). A root command keeps that line where it is. The page says so and shows the command.

## Licence position (read from NDI's pages on 2026-10-07)

Sources: the SDK documentation's [Licensing](https://docs.ndi.video/all/developing-with-ndi/sdk/licensing), [Software Distribution](https://docs.ndi.video/all/developing-with-ndi/sdk/software-distribution), [Dynamic Loading](https://docs.ndi.video/all/developing-with-ndi/sdk/dynamic-loading-of-ndi-libraries), [Platform Considerations](https://docs.ndi.video/all/developing-with-ndi/sdk/platform-considerations) and [Port Numbers](https://docs.ndi.video/all/developing-with-ndi/sdk/port-numbers) pages, and the SDK's public header files as mirrored in the DistroAV project (`lib/ndi/Processing.NDI.*.h`).

What those pages say, in their words:

- "The NDI SDK remains royalty-free, subject to the SDK terms and conditions. For more details including a full explanation and updated list of exclusions please consult the terms and conditions by downloading the NDI 6.3.2 SDK." and "You may use this SDK in accordance with its License Agreement, which is available for review in the root level of the SDK folder."
- "Your application must provide a link to ndi.video in a location close to all locations where NDI is used/selected within the product, on your website, and in its documentation."
- "You may not distribute the NDI tools; if you wish to make these accessible to your users, you may provide a link to ndi.video/tools."
- "NDI is a registered trademark of Vizrt NDI AB and should be used only with the ® as follows: NDI®, along with the statement "NDI® is a registered trademark of Vizrt NDI AB" located on the same page near the mark where it is first used, or at the bottom of the page in footnotes. You are required to use the registered trademark designation only on the first use of the word NDI within a single document."
- "Your application's About Box and any other locations where trademark attribution is provided should also specifically indicate that "NDI® is a registered trademark of Vizrt NDI AB"."
- "Note that if you wish to use "NDI" within the name of your product please reach out to the NDI team."
- "You should include the NDI DLLs as part of your own application and keep them in your application folders ... Please do not install your NDI DLLs into the system path for this reason. If you are distributing the NDI DLLs, you need to ensure that your application complies with the License Agreement, this section, and the license terms outlined in "3rd party rights" towards the end of this manual."
- Binary files: "You may distribute these files within your application if your EULA terms cover the specific requirements of the NDI SDK EULA, and your application covers the terms of the License section above." Redistributables: "you must make all reasonable efforts to keep the versions you distribute up to date."
- Header files "may be distributed with open-source projects under the terms of the MIT license ... (see "Dynamic Loading" section for preferred mechanism)."
- "Because AAC, H.264, and H.265 are formats that potentially are not license-free, it is your responsibility to ensure that these are correctly licensed for your product if you are using these with this SDK." (This is NDI HX.)
- Linux: "The NDI library on Linux depends on two 3rd party libraries: `libavahi-common.so.3`, `libavahi-client.so.3`. The usage of these libraries depends on the `avahi-daemon` service to be installed and running."
- In the header for Linux the library name is `libndi.so.6` and the download address for a redistributable is an empty string: **NDI publishes no separate runtime download for Linux.** The library comes only inside the SDK, whose installer shows the licence and asks for a yes.

What follows for this project:

1. **Nothing of NDI's is in this repository or the image**: no library, no header, no logo. The structures in `pvj/ndi.py` were written here from the layout the public headers describe; no header text is copied. So the distribution terms (a matching EULA, keeping distributed versions current) do not come into play.
2. **The owner fetches and accepts.** The installer does not download the SDK. An automatic fetch would have to say yes to NDI's licence on somebody's behalf, and there is no vendor address for the bare Linux library to fetch.
3. **In the product**: the NDI page writes NDI® on first use, carries the trademark sentence and a link to ndi.video beside the list where a source is chosen, and points to ndi.video/tools for the tools. About and power carries the trademark sentence. This document and the manual do the same. The library lives in the application's own folder (`/opt/pvj-ndi`), not on the system path.
4. **The name.** The product is nxlx.mastercontrol; "NDI input" is the label of one page. Whether that counts as using NDI "within the name of your product" is for the owner to judge or ask NDI about. Also the owner's to judge: the System index shows the row "NDI® input" and Live's "Now playing" line says "NDI: <the source>" with the trademark sentence one tap away (on the NDI page and on About and power), not on those screens themselves.

### The SDK License Agreement itself (read on 2026-10-07; an open legal question for the owner)

The owner downloaded the Linux SDK and the agreement its installer shows was read. It was **not accepted and the installer was not run**; nothing of NDI's has been put on the Pi or into this repository. The agreement treats the SDK and its documents as confidential, so what follows is a summary in this project's words, by section number. **The text itself is in the SDK's installer; read it there. Nothing here is legal advice.**

- **Which products the free licence covers (section 1b).** Products that run on a general purpose operating system on a general purpose computer (a server, a desktop, a laptop) where the person using it can freely change the system and install other software. It names three kinds it does not cover: a fixed-purpose physical appliance with a locked or restricted operating system; a fixed-purpose virtual or cloud environment; and a product built on hardware that may be classed as an embedded device, running an operating system typical for such devices (Linux and its derivatives are named), with a specific function that is not easily changed. For those, a commercial licence agreement with NDI is needed.
- **What that means here.** Whether a Raspberry Pi running this project's image may use the library at all is an **open legal question that only the owner can settle**, with NDI if need be: a Pi that is set up to be a video box may fall under the third kind. An ordinary PC running Debian with this project installed on it may be a different case. Until it is settled, the runtime should not be installed on a box. The page and the manual say so where the install steps are.
- **Staying current (2b).** A product must use, and work with, the latest SDK at the time it is used or distributed. So a copy of the library that is put on a box has to be kept up to date by whoever put it there; `pvj-ndi-runtime install` over an older copy does that.
- **No SDK file is passed on (2d)** unless the SDK's documentation says it may be. This project passes none on.
- **Terms a product's own licence must carry when it is distributed with the SDK (3d):** no modifying or reverse engineering of the SDK, disclaimers of warranty and liability on NDI's behalf, and a copyright notice. This project distributes nothing of the SDK, so its Apache-2.0 licence is not changed; if the library were ever shipped with a box, this would have to be met first.
- **The marks (3f)** may be used only to say that a product is compatible, with a clear notice that they are NDI's. The page, the manual and About carry that notice.
- **Governing law:** Sweden.
- **Which processor builds the SDK ships** was not looked at (the installer was not run). The nixpkgs package for NDI 6 (a secondary source) takes `x86_64-linux-gnu`, `aarch64-rpi4-linux-gnueabi` and `arm-rpi2-linux-gnueabihf` from the SDK's `lib/` folder and lists i686 as well. `pvj-ndi-runtime` looks for the folder that matches the box and checks the file's own header, so a wrong guess here is caught on the box.

## Discovery and the network

- The library finds senders by mDNS through `avahi-daemon` (it talks to the daemon over the system D-Bus socket; the sandbox allows Unix sockets for that). mDNS is UDP port 5353, multicast, and does not cross routers or many Wi-Fi access points.
- Where mDNS does not pass, add the sender's IPv4 address on the page. The library then asks that machine directly (TCP 5960) for its sources.
- Video arrives on TCP or UDP ports from 5960 upward (5961 and up per stream, 6960 and 7960 and up for the multi-connection modes). The box runs no firewall of its own, so nothing needs opening on it. A managed switch or a firewall between sender and box must let these through.
- The helper only receives. It never announces a source.
- A cost that is there even with the NDI input switched off: `pvj-ndi.service` is enabled on every box (idle, no library loaded, nothing sent) and wants `avahi-daemon`, which the installer adds where it is missing (Raspberry Pi OS has it already). A box with avahi answers to its name by mDNS on the network it is on.

## Not verified

- **Everything that touches the real library on Linux**: that `libndi.so.6` loads under this sandbox, that the structure layouts match on aarch64 (they did on 64-bit macOS with library 6.3.2, which shares the 64-bit layout rules but is not the same build), that discovery works through `avahi-daemon` from inside the unit, that "fastest" gives UYVY on a Pi.
- **A still source on the screen**: whether mpv draws the one frame a still sender gives through the raw pipe (step N12).
- **An interlaced sender**: which frame types really arrive and that they are refused as written (step N13).
- **The bounds in the unit** (memory, tasks, processor, the small `/tmp`), and discovery without localhost (step N14).
- **Everything that touches mpv**: the raw pipe in `uyvy422` (the capture input's `yuyv422` path is proven on the Pi 4; this byte order is not), a fractional frame rate (59.94), and the reload on a size change.
- **Every sandbox line** in `pvj-ndi.service`, above all `IPAddressDeny`/`IPAddressAllow` (it needs the kernel's cgroup network filter), `PrivateDevices` and `RestrictAddressFamilies`. LESSONS has two entries where a sandbox line emptied a feature silently on the Pi. Also unverified: that the player and the panel start with the extra group `pvj-ndi` after an update over a running older install.
- **Speed.** Full NDI is decoded in software by the library; 1080p30 UYVY is 124 MB a second through the pipe (the capture input managed that on a Pi 4), 1080p60 is twice that and has never been tried. NDI HX is H.264 or HEVC decoded by the library in software and may be heavier still. Latency is unmeasured.
- A Pi 3 is not offered (the module lists Pi 4, Pi 5 and x86).
- The browser test step for the page was written and syntax-checked, not run (the dev Mac has no Playwright). Neither was the installer: its tests cannot run on macOS, so the new account, group and unit have only static checks (`tests/test_units.py`).
- Health does not list the NDI helper; the NDI page and its row on System say when it is not running.

## Open design questions

- **Sound.** Options: (a) a second pipe of raw samples played as mpv's external audio (`audio-files` with the raw audio demuxer): simple, but keeping it in step with the raw video pipe is unsolved; (b) the helper writes one stream that holds both with time stamps (NUT or Matroska with raw video and PCM), which lets mpv keep them in step and also carries the true frame times, at the price of a muxer written here and tested against mpv on a device; (c) leave sound to the room's own audio path. Recommended: (b), after the video path is proven on the Pi.
- **Low bandwidth.** The library can ask a sender for its small proxy stream; a "small picture" choice per source would make a Pi 3 or a weak Wi-Fi link usable. Not built.
- **A source as a pad, in a scene or in the schedule.** The id is a hash of the name, so it stays the same across restarts and could be saved. Not built.
- **Getting the library onto a box without a keyboard.** A USB stick route (root's USB unit copies `ndi/libndi.so.6` from a stick) would suit a venue; physical access is already the box. Not built; needs the licence read first.

## Alternatives that were rejected

- **mpv or ffmpeg with NDI built in.** Debian's and Raspberry Pi OS's ffmpeg have no NDI (as far as is remembered, ffmpeg removed its NDI device in 2019 after a licence dispute; this was not looked up for this work), so it would mean shipping our own ffmpeg and mpv linked against a proprietary library.
- **The library inside the player or the panel.** A crash would take the screen down (the reason the capture input has a helper), and a takeover would have the PIN, the settings and the stream passwords (panel) or the screen and the control socket (player).
- **The helper as a child of the panel**, like the capture helper. It would inherit the panel's account and its access to the settings.
- **A compiled helper** (C, with or without GStreamer's NDI plugin). Faster to a known-good result, but it breaks "standard library only" and adds a build per board. If the Pi 4 measurement shows Python's copy is the bottleneck, this is the next thing to weigh, and it is the owner's call.
- **YUV4MPEG instead of raw frames.** It carries the size in a header, but only planar formats, so every frame would be rearranged in Python.
- **An upload route for the library.** See above.
