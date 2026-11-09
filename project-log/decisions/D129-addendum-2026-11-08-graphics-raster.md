<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D129 (2026-11-08): what slice 1 changed from the design while it was built

Built as the design says, with these differences, each for a reason found in the code:

- **The thumbnail route is `GET /api/graphics/thumb?name=NAME.png`**, not `/api/graphics/file/<name>.png`. The role table (`policy.OUTSIDE`) is exact paths, and a path with a name in it would have no row. The name is checked as every other name and only a stored PNG is ever served.
- **The graphics store is `graphics/` beside `settings.json`** (`/var/lib/pvj/graphics`, or `PVJ_GRAPHICS_DIR`), not beside the media folder: the media folder is `/media/internal/video` by default and is often another disk or a USB drive that the panel may not write to.
- **A stored name always ends in `.png`**: an upload called `mara.jpg` is stored as `mara.png`, because what is stored is the helper's PNG. The upload's extension must still agree with what is inside; if they differ the answer says so.
- **The upload is held in memory (at most 10 MB) while it is received**, not written to a hidden temp file first. It shares the clip upload's lock, so only one upload of either kind runs at a time, and its slow-sender and revoked-device checks. The size of an SVG is refused after the first bytes, before the rest is read.
- **Colour is not managed.** The design said "convert to sRGB"; there is no colour library on the node, so a profile in a PNG or JPEG is dropped and the picture is taken as sRGB. 16-bit becomes 8-bit. An opaque picture is stored as RGB, one with any transparency as RGBA.
- **No in-process seccomp filter**: the syscall list is systemd's (`SystemCallFilter`), with `PrivateNetwork`, `AF_UNIX` only and `NoNewPrivileges` besides, which is what the design's 7.1 names and what the other helpers use. The program also sets `PR_SET_NO_NEW_PRIVS` and an alarm itself, and sets resource limits itself only when run with `--self-limit` (tests, a desk); under the unit systemd sets them.
- **Not built in this slice: the panel's Library > Graphics tab** (upload button, thumbnails, rename, delete) that section 10 lists for slice 1. The routes behind it are done and tested, so the tab is a panel-only change; it is left for the pull request that adds Play > Graphics, or a small one before it, as Anthony prefers. Slice 1 is usable from the API and `curl` only.
- The independent review of 7.5 is still owed before slice 2, and the unit has not yet run as the real socket-activated system service on a node (rows RA1 to RA14 in `tools/DEVICE-TESTING.md`).
