<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-31, Ableton Link: the box follows a session (D117)

- `pvj-link`, a small C++ helper in `render/link/`, with its own unit, account and group, built at install from Debian's `ableton-link-dev`. A missing package means Link is not available and nothing else changes, also with `--offline`.
- The box clock has a new word, `Clock.link(bpm, phase, at)`, so the place in the bar reaches it as well as the tempo. `pvj/link.py` is the panel's side: the Owner's setting, telling the helper, reading its file where the levels are read. `link` is in `GET /api/tempo` and `GET /api/sound`; `POST /api/tempo/link` is the Owner's. One switch and one line on the Sound page.
- Found while building: Link's clock on Linux is CLOCK_MONOTONIC_RAW, so records are stamped with the box's clock, read beside it. And in Link the older session wins, so a box left on alone would have pushed 120 BPM onto the next program to join (seen on a desk: 133 went to 120). So the helper keeps Link disabled until it hears someone, and it says nothing on the network while alone.
- Tests: `tests/test_link.py` (fake helper, fake clock) and `tests/test_pvj_link.py` (the real helper beside a stand-in peer, only with `PVJ_LINK_TEST=1`). A new CI job, `pvj-link`, builds and runs them on Debian 13, where a skip is a failure.
- Run on a desk against the package's headers, Ubuntu's 3.1.2 unpacked to a temporary folder: the helper followed a peer's tempo and phase, did not move it, and let go. Not run: a Pi, Ableton Live, a phone, a real network, the unit's sandbox, the installer for real. `tools/DEVICE-TESTING.md` LK1 to LK7, all not run.
