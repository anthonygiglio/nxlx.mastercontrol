<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Panel design playground

A working copy of the panel (Live, Mix, Media, System and the Updates card) with live controls for its look and layout. It is for trying ideas before changing `mctl/web/app.css` and `app.js`. It is a dev tool: it is not installed on the box and the panel does not depend on it (D34). For pictures of the panel as it is, with every screen exported as SVG, PSD and PNG, see [docs/mockups](../../docs/mockups/README.md); this tool is for changing it.

## What it does

- Shows the panel at phone, tablet, laptop or desktop size, or a phone next to a bigger screen. The wide layout follows the preview's width, not the browser window's (the 900px media query of `app.css` is a container query here).
- Changes the seven theme colours (`bg cd fg ln mu ac on`, starting from the four themes in `mctl/themes.d`), the typefaces, and about twenty sizes: text, border width, corner radius, padding, gaps, button, tab and pad heights, pad columns.
- Tries layout ideas that need `app.js` work: tabs at the top or in a side rail, a two-column Live screen, pads first, the Fade out, Freeze, Stop and Blackout row docked above the tabs, pad name placement, clip thumbnails.
- Shows each state of the Updates card (D33): nothing waiting, on a USB drive, uploaded, uploading, confirm, installing, done and failed. The progress bar and the confirm step drawn on the card are ideas; the panel shows text and uses the browser's confirm dialog today.
- **Export** gives only what was changed: CSS to append at the end of `mctl/web/app.css`, a theme file for `mctl/themes.d`, and the settings as JSON. Settings are kept in the browser's local storage.

The panel part is `src/Panel.tsx` and `src/panel.css`, which use the panel's own class names and default values. When `app.css` changes, update `panel.css` and the defaults in `src/settings.ts` to match, or the playground drifts from the real panel.

## Run and build

Needs Node 20.19 or newer, or 22.12 or newer (Vite's minimum).

```
cd tools/panel-playground
npm install
npm run dev        # http://localhost:5173, reloads on save
npm run build      # type check, then one self-contained dist/index.html
```

`dist/index.html` has everything inlined (the Google Fonts it offers load from the network when online). Open it from disk or publish it as a page.
