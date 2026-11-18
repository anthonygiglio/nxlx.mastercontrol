<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-18, J-156: the strobe says before the press that nothing plays

- GET /api/strobe gave no sign that nothing plays, so on the classic renderer (the strobe is the effect) the button was lit and the hold was refused after the press (409, "Nothing with a picture is playing"). A first try made `available` follow the effects' picture check; review showed that wrong: the New renderer's chain takes a hold with nothing playing, a clip start or loop point reads as no picture for a moment, state() is also returned by set() on the controllers' threads (no player IPC there), and the panel read it only once.
- Now `available` means only what `_need()` says, and state() asks the player nothing. `Strobe.api_get` (the panel's GET only) adds `warn` with the effects' own reason when the route would be the effect (not the chain), nothing is lit and `effects.available()` is false.
- The button stays enabled whenever `available` is true. The strobe card shows "Nothing to flash over: ..." in the status line instead of "Ready", and reads GET /api/strobe again every 3 seconds (one timer, ended when the card is gone, idle while held) to update only that line.
- Tests in tests/test_strobe.py: effect path with no picture is available with a warn; chain path with no picture has no warn; lit has no warn; state() and set() do not ask the player for the picture. The hold refusal test stays. tests.test_strobe passes in WSL; tests/ui/panel.test.js was not changed or run.
- Not proven: the 3 second re-read and the status text in a browser, and anything on a real node.
