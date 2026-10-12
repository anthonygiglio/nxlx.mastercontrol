# nxlx.mastercontrol

**nxlx.mastercontrol** is a project of NXLX.Systems. It began as a fork of PocketVJ CP v3 (the repository was formerly named `PocketVJ-CP-v3`, after the last version maintained upstream). It is a modernization of that control panel, a control panel for playing, mixing and mapping video on small computers. It is built for artists who run visuals at gigs: raves, concerts and installations.

> **Status: work in progress.** The new core (Python 3, in `pvj/`) is in place and has been tested in containers and CI, and the CI-built image has been booted and exercised on **one Raspberry Pi 4** (see [HANDOFF.md](HANDOFF.md) for exactly what was and was not verified there); other boards are untested. The `master` branch also still holds the legacy v3 code, which targets Raspberry Pi 3B+ on Raspbian Jessie. The state before the fork work began is tagged `legacy-v3`. Do not expose the legacy code to an untrusted network; see [SECURITY.md](SECURITY.md).

## What it looks like

The panel is made for a phone in one hand at a gig: big pads, one screen for what is playing, and everything else a tap away. These pictures are taken by the test suite from the real panel (`tests/ui/screenshots.js`), with test clips and a fake network, so the clips, addresses and hardware shown are not real.

| Live | Mix | Media |
| --- | --- | --- |
| ![Live: pads, now playing, fade, freeze and blackout](docs/images/ui/live.png) | ![Mix: opacity, size, position, speed, transitions](docs/images/ui/mix.png) | ![Media: upload, play, rename, delete](docs/images/ui/media.png) |

More screens, with the beta modules (autostart, schedule, streams, DMX, MIDI, network), are in [docs/UI.md](docs/UI.md). The user manual is [docs/MANUAL.md](docs/MANUAL.md).

## Goals

- Run on Raspberry Pi 3B, 4 and 5, small x86 mini PCs, and old recycled computers.
- Run on current systems: Raspberry Pi OS Bookworm and Trixie, Debian and Ubuntu on x86.
- Be rugged at gigs. The preferred setup is a direct Ethernet link on a private network; Wi-Fi is optional, for the phone only.
- Take video from the network: NDI® ([ndi.video](https://ndi.video/); beta, needs NDI's own runtime, which the owner supplies, see [pvj/NDI.md](pvj/NDI.md); NDI® is a registered trademark of Vizrt NDI AB.), SRT, RTSP and RTMP, plus AES67/Dante audio. SMPTE ST 2110 sources come in through a gateway (2110 to HDMI into the capture input, or 2110 to NDI). Each input is an optional, updatable module. See [ROADMAP.md](ROADMAP.md).

## Layout

| Path | What it is |
| --- | --- |
| `backend.php`, `index.html`, `sync/`, ... | Legacy v3 code, kept until the new core replaces it |
| `docs/` | Legacy manual and notes |
| `ROADMAP.md` | Phased plan for the modernization |
| `NOTICE.md`, `THIRD_PARTY_LICENSES.md` | Credits and licensing of bundled parts |

## Licence and credits

New code in this fork is licensed under the Apache License 2.0; the legacy code keeps its original licence. See [NOTICE.md](NOTICE.md) for exactly which is which.

This is a fork of [PocketVJ CP v3](https://github.com/magdesign/PocketVJ-CP-v3) by Marc-André Gasser (magdesign) and contributors. The original [LICENSE.md](LICENSE.md) and [AUTHORS.md](AUTHORS.md) are kept unchanged. See [NOTICE.md](NOTICE.md).
