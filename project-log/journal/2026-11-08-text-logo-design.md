<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, Text and logo sources: a design, no code

- Anthony decided on 8 November to build a Text Generator source and uploaded logos (as a source or overlay, with keying and effects) into Mastercontrol, instead of streaming HTML over NDI from a laptop (that test: about 24 fps, 0.8 s behind, heavy laptop load).
- Wrote design/text-and-logo-sources_2026-11-08.md: a "graphic" (text or logo) with its own list of up to 8, drawn by pvj-render on the GPU on Pi 4, Pi 5 and desktop, and by mpv overlay-add on the Pi 3 and the classic renderer. Motion (fade, typewriter, scroll, crawl) is per frame on the GPU, stepped on classic.
- Uploads are decoded only in a new sandboxed program, pvj-raster (PNG, JPEG, SVG in; one clean PNG out); SVG is rasterised and not kept; fonts are bundled open-licence only, font upload deferred. The upload path needs an independent review before the drawing slices build on it.
- The mask list (D100) is not turned into a general layer; the two sit side by side. Seven slices, open questions and unsettled points are in the document.
- Found: design/ did not exist on master, and D127 and D128 are already claimed by open pull requests (#224, #230), so the proposed decision is D129.
