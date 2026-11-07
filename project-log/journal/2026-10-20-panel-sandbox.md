<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-06, brought up to master and the log moved to files of its own

- **What.** The branch was 182 commits behind master (8d473ea). Master merged in; the only conflicts were the three frozen log files, so this branch's entries moved into its own files under D107, word for word, and its decision keeps its number.

## 2026-10-20, the design sandbox (`panel-sandbox`, D103)

- **The brief:** step 3 of the owner's panel design brief: the real panel on the laptop, every role and state, no node.
- **Built:** `tools/sandbox/serve.py` (the switcher on 127.0.0.1:8700 and one box process per scenario), `box.py` (the real panel with the harness's fakes, one port per role: Owner 8701, Operator 8702, Guest 8703, not paired 8704), ten scenarios (idle, playing, projectors warming and cooling, a projector in trouble, masks combining, a mapping with surfaces, the connection drops, a new box, guests locked, ambience playing), a README.
- **Tests:** `tests/test_sandbox.py` (the scenario files and what they refuse; the switcher's Host, header, origin and body checks, its policy and its files; every scenario started on a real box with each port asked who it is; a stopped box and a SIGTERM leave no folder and no process); `tests/ui/sandbox.test.js` (the switcher, and each role at 390 and 1440 with no script error and no refusal of the policy), a step of the panel-ui job. All pass on the HP's WSL; the panel's own browser test passes there too now (16 minutes).
- **Found:** on Windows a 1440 window gives the panel 1425 px (a classic scrollbar), so the strip keeps More there; on a Mac (overlay scrollbars) 1440 is the whole width.
- **Not done:** fakes for MIDI, DMX, OSC, NDI, render nodes and sound input; a run on the owner's Mac.
- **The review of #155:** two Mediums (Ctrl+C left mpv running; the box listened on 0.0.0.0 once OSC, DMX or sync was on) and four Lows, all fixed with tests (D103's amendment). Break-checks, each test failing without its fix: no loopback guard, the shell's PVJ_ variables kept, no stdin watch, no cross-site check, the port bound after the log, and the old stop order with every layer of the new one taken out.
- **An accident while fixing it:** a debug script of mine with an empty variable SIGKILLed most processes of the WSL user (LESSONS). The owner's sandbox and a CI watch died; the four other sessions were told to check their work in WSL. Two orphan mpv processes of earlier runs were found by the review and stopped.
