## nxlx.mastercontrol roadmap

Legacy v3 items are kept below the line for reference.

| Phase | Scope |
| --- | --- |
| 0 | Fork housekeeping: README, legacy tag, Pages fix, CI |
| 1 | Security hotfix on the old line (3.9.x): input casts and quoting, narrow sudoers, auth, POST plus tokens, pairing PIN |
| 2 | Platform layer: `pvj-player` on mpv, hardware detection, display layer, Python 3, gpiozero, no hardcoded `/home/pi` |
| 3 | Installer, systemd services, watchdog, USB automount, images built in CI, binaries out of git |
| 4 | New core in Python 3: module manifests, versioned settings and migrations, themes, update and rollback |
| 5 | Feature parity with the 12 legacy tabs |
| 6 | Mapper spike and network inputs (see below) |
| 7 | Themes, manual, migration tool, hardware test matrix, 4.0 |

### Network input modules (all optional, each updatable on its own)

| Module | Notes |
| --- | --- |
| NDI® | Receive is built (beta, off by default, D61): video only, through its own sandboxed helper; **never yet run against a real sender**. The runtime is proprietary: the owner fetches it from [ndi.video](https://ndi.video/) and puts it on the box with `pvj-ndi-runtime`; it is not fetched or updated by the box (NDI publishes no separate Linux runtime, and its licence must be accepted by a person). Still to do: the measurements on a Pi, sound, the low-bandwidth stream, a source as a pad or in a scene. See [pvj/NDI.md](pvj/NDI.md). NDI® is a registered trademark of Vizrt NDI AB. |
| SRT, RTSP, RTMP | Fully open. Via ffmpeg or mpv. |
| AES67 / Dante audio | Dante devices interoperate through AES67 mode. Native Dante on Linux needs the community Inferno project or a Dante hardware card; to be evaluated. |
| SMPTE ST 2110 | Not planned as a native input (D30). HD 2110-20 video needs 1.2 to 2.6 Gbps, more than the Pi's 1 GbE; a Pi 4 has no hardware PTP; broadcast plants expect NMOS IS-04/IS-05. Use a gateway: 2110 to HDMI into the capture input, or 2110 to NDI. Revisit only for x86 with a 10 GbE card if a user with a 2110 plant needs direct ingest. |

### Legacy v3 list (upstream, 2022)

 <br />

- add rescue script similar to exhibition <br />
- fix the overlay edge case, needed? <br />
- play all video files random, integrated in custom01  <br />
- mapping without mouse, adopt it from exhibition so it will work too <br />
- remove the underline from play video 01_* so its more userfriendly  <br />
<br />
- bring the audio quality fixes from exhibition to the rtc as well!<br />
- change build pipeline to build docs into cp and to website <br />
- feedback in cp when moving speed or opacity slider <br />
- rewrite update script to force remove content in ofxPiMapper/example/bin/data/sources/videos, recheck to mapper update process! <br />
- port the new pjlink commands from Exhibition <br />
- moving big steps for mapper witout mouse or create faders <br />
- prevent bootloops when pusing reboot and browser reconnects, solve with closing the browser tab <br />
- add omxplayer error output when setting to alsa/usb audio but no soundcard is recognized <br />
- Update the osc_control.js (startlesser scripts) <br />
- playing videos once does not give feedback in CP, add feedbacks inbackend or in scripts <br />

### Tutorials: <br />

- Show how to use mapper without mouse  <br />
- All autostart functions  <br />



=======================<br />

Opensource rocks! <br />
©2022 marc-andré gasser

