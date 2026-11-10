<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, Whole lines in the phone title bar, and Danger in the look's colour

- **Why.** A UX audit of every screen as the Owner (scratchpad tool: every screen at 390, 820 and 1440 px in all six looks, checking text contrast, target sizes, a page wider than the window, text cut off, console errors). It found two things, and nothing else:
  - On a phone the title bar shows a screen's one line on one line, cut with an ellipsis, so Library ("Find a clip, a picture or a live input and play it; keep the files."), Effect and Sound lost the end of theirs. A phone has no hover, so the full text in the title could not be read.
  - The Danger heading over Reset on Setup > Backup and reset was a fixed red, #d32f4a, outside Signal: 3.8:1 on Dark stage and 4.3 to 4.4:1 on Light, High contrast and Night red, under the 4.5 that text of its size needs.
- **What.** The three lines are short enough for a phone: Library "Play clips, pictures and live inputs.", Effect "Put an effect over the picture.", Sound "How loud, and where it comes out." Danger takes the look's accent, as every other error line outside Signal does (Signal already gives it its problem colour).
- **Checked.** The audit again on this branch: no line is cut at 390 px and Danger reads in every look. tests/ui/panel.test.js passes.
