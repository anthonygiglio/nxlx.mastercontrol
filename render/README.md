<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: GPL-3.0-or-later -->
# pvj-render

The box's own renderer (project-log/DECISIONS.md, D95). Phase 1 takes the place of the one mpv behind the same JSON
socket: one video layer drawn by libmpv at an internal size, the projection mapping and the scale to the screen as one
final pass, and overlays and on-screen text over it. Chosen on Setup > Screen output (Renderer); classic mpv stays the
default, and the player's service goes back to it by itself when this program fails.

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
`--every-vblank` draws and flips at every vblank and counts missed vblanks (`get_property pvj-render`), for
measuring on a board.
