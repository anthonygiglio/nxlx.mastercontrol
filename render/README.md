<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: GPL-3.0-or-later -->
# pvj-render

The box's own renderer (project-log/DECISIONS.md, D95). Phase 1 takes the place of the one mpv behind the same JSON
socket: one video layer drawn by libmpv at an internal size, the projection mapping and the scale to the screen as one
final pass, and overlays and on-screen text over it. Chosen on Setup > Screen output (Renderer); classic mpv stays the
default, and the player's service goes back to it by itself when this program fails.

Phase 2 (D98) adds a second video layer and draws the crossfade itself. `["pvj-crossfade", URL, SECONDS,
"keep-open=no,image-display-duration=inf"]` loads the clip into the idle layer, which the socket speaks to from then on
(path, pause, the loops, a seek: the new clip's), and mixes the two pictures by real opacity once a vblank, from the
clip's first frame, in a straight line over SECONDS (0 to 60); then the outgoing layer is stopped. The idle layer
decodes and draws nothing. How the picture looks (brightness, zoom, pan, rotation, crop, flips, effects) is set on
both layers; a load, a stop, a step in the list or another crossfade ends a fade at once. The panel uses it for the
Mix's Crossfade from one clip to another (`Player.play(..., fade=seconds)`); everything else is a cut, as before.
Classic's crossfade steps a still 20 times a second; this one moves at the screen's rate, with both pictures moving.

**Licence:** GPL-3.0-or-later (this folder only; `LICENSE`). It links libmpv, which is GPLv2 or later as Debian
builds it. The rest of nxlx.mastercontrol is Apache-2.0 and only starts this program and talks to it over a socket.

**Build** (on the box, by the installer; by hand the same):

    sudo apt install gcc make pkg-config libmpv-dev libegl-dev libgles-dev libgbm-dev libdrm-dev
    make -C render              # render/pvj-render
    make -C render WERROR=1     # as CI builds it

**Run** (the player's service does this; by hand only with the player stopped, as the display's account):

    render/pvj-render --socket=/run/pvj/player/mpv.sock --drm-device=/dev/dri/card1 --drm-connector=HDMI-A-1 \
        --drm-mode=1920x1080@60.00 --detail=540 --fast

`--headless=WxH` draws without a screen (EGL surfaceless), for the tests: `tests/test_pvj_render.py`.

A picture goes out at every vblank, a new clip frame or not (the clip's last frame is used again), and
`get_property pvj-render` counts flips, clip frames drawn and missed vblanks. `--every-vblank` is still taken and
changes nothing. The socket gives each client a thread of its own (at most 32 at once); a client that takes no
answer for 2 seconds, or sends nothing for 30, is dropped. libmpv 0.40 and 0.41 leave a GL fence behind at every
frame with the render API (on the Pi's V3D a file descriptor each); this program deletes it, and
`gl-fences` / `gl-fences-freed` in the same property show it. The same property says `layers`, `front`, `fade`
(`off`, `waiting`, `running`), `fade-progress`, `fades`, `fades-done`, `fades-cut`, `back-drawn`, `back-idle` and
`gl-objects` (libmpv's textures, framebuffers, renderbuffers and buffers held now).
