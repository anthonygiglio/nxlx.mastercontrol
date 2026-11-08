<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, The output: pick the connector, and switch the AV output on (D127)

- `screen.output` (Automatic by default) rides in WANTED beside the mode; `screenmode.decide` takes the named connector when it is connected, else Automatic with a plain reason in the report (`output_why`), and a named output without a known card still passes its name. The Pi 4's Automatic arguments are unchanged (a test holds them). A composite connector lists interlaced modes only; its first mode (set by the TV standard) is used.
- `GET /api/output`, `POST /api/output`, `POST /api/output/av` (Owner only; the writes are in `support.REMOTE_DENY`). `Screen.set_output` saves, writes WANTED and restarts the player once; `Screen.choose/confirm/revert/start/sync` keep the output.
- `pvj/bootcfg.py` edits config.txt and cmdline.txt (composite on the KMS overlay line, `enable_tvout=1` in a marked block, `vc4.tv_norm`) with backups; `pvj-sysd` has the `av_output` command and its unit may write `/boot/firmware` and `/boot`. Official documentation says AV on a Pi 4 and Pi 5 disables HDMI; the API says so in `av.costs` and `av.hdmi_off`.
- Tests: fake sysfs for a Pi 4 (two HDMI and composite, writeback), a Pi 3B, a Pi 5 (two cards) and a desktop; the boot files edited and put back exactly; backups kept and rotated; a second file failing; the helper command; the late-screen watcher, the decision and pvj-render's own pick agree on composite. Nothing ran on a node.
