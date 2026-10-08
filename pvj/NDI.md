<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# NDI® input (beta, never run against a real sender)

Show the output of Resolume, MadMapper, OBS or any other NDI sender on the box's screen, like a clip. Switch it on under System > NDI input. Information about NDI and its tools is at [ndi.video](https://ndi.video/); the free tools (Test Patterns, Screen Capture, Studio Monitor) are at [ndi.video/tools](https://ndi.video/tools/).

NDI® is a registered trademark of Vizrt NDI AB.

**State on 2026-10-07.** The receiver, its service, the routes and the page are written and tested against a fake library and fake frames on a Mac. **Not one NDI frame has been received by this code**, on any machine: the dev machine has no NDI runtime, no mpv and no sender. Whether it is smooth on a Pi 4 is unknown until the steps in `tools/DEVICE-TESTING.md` ("NDI input") are run with a real sender. Everything under "Not verified" below is exactly that.

## What the box needs that this repository does not contain

The NDI runtime library, `libndi.so.6`. It is proprietary, it is not in this repository and it is not in the image. Somebody with the right to accept NDI's licence fetches the NDI SDK for Linux from [ndi.video](https://ndi.video/) (the download asks you to agree to its licence), and copies one file from it to the box:

    sudo pvj-ndi-runtime install "/path/to/NDI SDK for Linux"      # the unpacked SDK folder, or the libndi.so.6 file itself
    sudo pvj-ndi-runtime status
    sudo pvj-ndi-runtime remove

The command picks the file for this board from the SDK's `lib/` folder, refuses a file built for another kind of processor and says which ("this file is for x86_64, this box is aarch64"), and copies it to `/opt/pvj-ndi/libndi.so.6` (root's, read-only for every service). It needs root on the box (a keyboard or SSH), on purpose: see "Why there is no upload button".

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
- **A sender that goes away**: the helper keeps the connection (the library reconnects by name by itself), the screen holds the last frame, and the page says "Waiting for the source". When frames return they continue; if they return in another size, see above.
- **Slow screen, fast sender**: the helper holds one frame; a newer one replaces it and the old one is counted as dropped. A frame is never written in part. The counts (received, shown, dropped here, dropped by the library) are on the page and in the status route.
- **Fields**: a sender of interlaced video delivers single fields in this format. They are refused with a plain message ("the source sends interlaced fields"); set the sender to progressive.
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
- **Its sandbox** (`install/pvj-ndi.service`): no capabilities, no devices, a read-only system, no home folders, a private `/tmp`, one writable folder (`/run/pvj-ndi`), and **network addresses limited to private ranges, link-local and multicast** (`IPAddressDeny=any` with an allow list), so the library cannot talk to the internet at all. `tests/test_units.py` pins every line.
- **The helper answers only the panel's account** on its socket (`SO_PEERCRED`), with the same small server the other helpers use.
- **Names**: a source name from the network is shown and used only if it is valid UTF-8, 1 to 128 characters, with no control, format (bidirectional overrides, zero width) or line-separator characters; at most 64 sources are kept. The panel plays by an id, a hash of the name made by the helper, never by a string from the request.
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
4. **The name.** The product is nxlx.mastercontrol; "NDI input" is the label of one page. Whether that counts as using NDI "within the name of your product" is for the owner to judge or ask NDI about.

**Not confirmed, and for the owner before NDI is used at a venue or the box is sold or lent:**

- The License Agreement itself and its "updated list of exclusions" are only inside the SDK download, which was not fetched for this work (it needs agreeing to the licence). Whether a box like this one (hardware that receives NDI and shows it) is excluded from the royalty-free SDK, or needs the NDI Advanced SDK or a commercial arrangement, is **unknown**. The Advanced SDK page says "To use the Advanced SDK in a commercial product or environment you should contact licensing@ndi.video"; nothing equally plain was found for the standard SDK. Read the agreement in the SDK's root folder.
- Whether the terms allow putting the library into an image or an installer for other people. Until someone reads them, nobody but each box's owner should put the library on a box.
- Which processor builds the current SDK ships. The nixpkgs package for NDI 6 (a secondary source) takes `x86_64-linux-gnu`, `aarch64-rpi4-linux-gnueabi` and `arm-rpi2-linux-gnueabihf` from the SDK's `lib/` folder and lists i686 as well. `pvj-ndi-runtime` looks for the folder that matches the box and checks the file's own header, so a wrong guess here is caught on the box.

## Discovery and the network

- The library finds senders by mDNS through `avahi-daemon` (it talks to the daemon over the system D-Bus socket; the sandbox allows Unix sockets for that). mDNS is UDP port 5353, multicast, and does not cross routers or many Wi-Fi access points.
- Where mDNS does not pass, add the sender's IPv4 address on the page. The library then asks that machine directly (TCP 5960) for its sources.
- Video arrives on TCP or UDP ports from 5960 upward (5961 and up per stream, 6960 and 7960 and up for the multi-connection modes). The box runs no firewall of its own, so nothing needs opening on it. A managed switch or a firewall between sender and box must let these through.
- The helper only receives. It never announces a source.

## Not verified

- **Everything that touches the real library**: that `libndi.so.6` loads under this sandbox, that the structure layouts match (a test pins sizes and offsets for 64-bit, read from the headers, but only a real call proves them), that discovery works through `avahi-daemon` from inside the unit, that "fastest" gives UYVY on a Pi.
- **Everything that touches mpv**: the raw pipe in `uyvy422` (the capture input's `yuyv422` path is proven on the Pi 4; this byte order is not), a fractional frame rate (59.94), and the reload on a size change.
- **Every sandbox line** in `pvj-ndi.service`, above all `IPAddressDeny`/`IPAddressAllow`, `PrivateDevices`, and the absence of `AF_NETLINK`'s friends the library may want. LESSONS has two entries where a sandbox line emptied a feature silently on the Pi.
- **Speed.** Full NDI is decoded in software by the library; 1080p30 UYVY is 124 MB a second through the pipe (the capture input managed that on a Pi 4), 1080p60 is twice that and has never been tried. NDI HX is H.264 or HEVC decoded by the library in software and may be heavier still. Latency is unmeasured.
- A Pi 3 is not offered (the module lists Pi 4, Pi 5 and x86).
- The browser test step for the page was written and syntax-checked, not run (the dev Mac has no Playwright).

## Open design questions

- **Sound.** Options: (a) a second pipe of raw samples played as mpv's external audio (`audio-files` with the raw audio demuxer): simple, but keeping it in step with the raw video pipe is unsolved; (b) the helper writes one stream that holds both with time stamps (NUT or Matroska with raw video and PCM), which lets mpv keep them in step and also carries the true frame times, at the price of a muxer written here and tested against mpv on a device; (c) leave sound to the room's own audio path. Recommended: (b), after the video path is proven on the Pi.
- **Low bandwidth.** The library can ask a sender for its small proxy stream; a "small picture" choice per source would make a Pi 3 or a weak Wi-Fi link usable. Not built.
- **A source as a pad, in a scene or in the schedule.** The id is a hash of the name, so it stays the same across restarts and could be saved. Not built.
- **Getting the library onto a box without a keyboard.** A USB stick route (root's USB unit copies `ndi/libndi.so.6` from a stick) would suit a venue; physical access is already the box. Not built; needs the licence read first.

## Alternatives that were rejected

- **mpv or ffmpeg with NDI built in.** Debian's and Raspberry Pi OS's ffmpeg have no NDI (ffmpeg removed its `libndi_newtek` device in 2019 over a licence violation by a distributor), so it would mean shipping our own ffmpeg and mpv linked against a proprietary library.
- **The library inside the player or the panel.** A crash would take the screen down (the reason the capture input has a helper), and a takeover would have the PIN, the settings and the stream passwords (panel) or the screen and the control socket (player).
- **The helper as a child of the panel**, like the capture helper. It would inherit the panel's account and its access to the settings.
- **A compiled helper** (C, with or without GStreamer's NDI plugin). Faster to a known-good result, but it breaks "standard library only" and adds a build per board. If the Pi 4 measurement shows Python's copy is the bottleneck, this is the next thing to weigh, and it is the owner's call.
- **YUV4MPEG instead of raw frames.** It carries the size in a header, but only planar formats, so every frame would be rearranged in Python.
- **An upload route for the library.** See above.
