<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D129 (2026-11-08): what the independent review of slice 2 changed

The review found two Mediums and three Lows. Each is fixed:

- **Overlay order (design 3.1: codes and masks stay on top).** The ids are now graphics 20 to 27, the masks 30 (`overlay.OVERLAY_ID`), the pairing codes 40 to 43 (`pinscreen.QR_IDS`), and the crossfade still stays at 63. mpv draws a higher id over a lower one. Every user of the ids reads the constants; the player takes 0 to 63. While `api.access_on_screen()` is true the graphics are taken off the screen (the PIN text is drawn by the OSD before any overlay, so only taking them off keeps it readable) and the status says "held". The pictures stay built, so they come back without being made again. The panel's tick (`Graphics.watch`) notices the change within a second.
- **OSC text is the Owner's choice.** "Controllers may push text" (`settings["control"]["graphics"]`, off by default, `POST /api/graphics/remote`, full access) is shown on the OSC page and on the Graphics page, as the mapping switch is. A controller's (OSC, MIDI) request that carries `text` is refused with 403 unless it is on. Showing, hiding and Hide all over OSC do not need it, and a push from the panel by an Operator or the Owner never asks. `policy.LEGACY_LIVE`'s comment no longer says "frozen": it says which route was added on purpose and why.
- **NaN.** `clampd` in `render/raster/gfx.inc.c` gives its low limit for NaN, so a "nan" in an opacity, a shadow, a plate, a size or a limit is the smallest value and not garbage. Every alpha and limit goes through it.
- **A failed build is not retried.** A player with no run folder, or a picture that cannot be written, is recorded in `_failed` like any other failed build and tried again only when something changes (or the player restarts).
- **The typed line is not in the address.** The preview is a POST with the line in the body (`POST /api/graphics/preview.png`, an Operator's route); the page shows the answer from a blob. The GET form no longer reads `text`.

Tests: `tests/test_graphics.py` (order, held back and back again, OSC refused by default, allowed when on, the switch is the Owner's, failed builds, the preview body), `tests/test_raster.py` (the real helper with "nan"), `tests/test_roles.py` (both routes have rows), `tests/ui/panel.test.js` (the line is in the body and not the address).
