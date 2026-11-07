<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-06, brought up to master and the log moved to files of its own

- **What.** The branch was 182 commits behind master (8d473ea). Master merged in; the only conflicts were the three frozen log files, so this branch's entries moved into its own files under D107, word for word, and its decision keeps its number.
- **PANEL.md brought up to master too.** Section 12 is now the sources as merged (D101) and each source's chain (D106 step e); the Renderer row has Sources and Final picture (D108); Sound reactivity has Follow Ableton Link (D117); the routes list the sources, chains and box clock. Found: the box clock (D116) has no control on the panel, only a MIDI action and OSC.
- **The review** (the coordinator's, 2026-11-06): two Lows, fixed. Section 12 now says Source 1 has no Effect row and gives its fold's real title; D102 no longer says D101 is marked as coming.

## 2026-10-20, the panel inventory (`panel-design`, D102)

- **The brief:** the owner's panel design brief (a sandbox, a Figma file that matches the panel, ranked proposals). Step 1 is the inventory.
- **Found first:** `tests/ui/harness.py` already serves the real panel with mpv, four fake PJLink projectors, fake Wi-Fi and a fake screen report, so the sandbox builds on it. The sources-surfaces work (D101) is a local branch of seven commits, not pushed, no pull request; its panel part is 98 lines of `app.js`. The Figma file "Signal UI kit" has one page now (Start here, with the token sheet and 14 text styles); the components and screens pages that the memory notes of 2026-10-05 describe are not in it.
- **Written:** `pvj/PANEL.md` (not `pvj/web`, as the brief said: every file there is served by the panel and copied to the node), from the code: roles, widths and frames, the frame around every screen, the shared patterns, Play, Shape, Room and Setup card by card, Projectors, the mask list and the Mapping card in full, what D101 adds, the tokens of Darkstage, Signal and Signal light, accessibility facts, the routes by role.
- **Noted for the proposals:** a mapping surface's Remove and a saved mapping's Delete do not ask first; a Room wall's Off does not ask while All off and the Projectors page do; the mapping canvas's colours are fixed in the script.
- **Not run:** the browser test on this laptop (Chromium needs libnspr4 and libnss3 in WSL; Playwright's own installer does not know Ubuntu 26.04).
