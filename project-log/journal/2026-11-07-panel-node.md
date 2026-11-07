<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, the panel says node (`panel-node`, D121)

- **What.** 360 strings of `pvj/web` (app.js, room.js, shaders.js, effects.js, load.js, index.html) say node where they said box. Only the text a person reads: comments, CSS, ids and keys (`'box'`) are unchanged. Names stay: "fader box", and the certificate's own name "NXLX boxes root" in the Mac install steps (tools/boxcert.py writes it, and owners' devices carry it).
- **Tests.** The browser test's quotes of the panel's words follow (panel.test.js, delivery.js, screenshots.js, signal-pages.js), and test_theme_api's one quote of app.js. The quotes of the server's words stay (the OSC warning, the Health line, a room job's "Box: playing"): those change with the server's own wording in the next pull request.
- **Run on the HP's WSL:** `tests/ui/panel.test.js` passes; the 13 Python modules that read the panel's files pass.
- **Found while testing:** a failed run of the browser test leaves its mpv behind (four were stopped by PID afterwards, each checked to be a test's own). The local test needs pngjs beside Playwright, as CI installs it.
