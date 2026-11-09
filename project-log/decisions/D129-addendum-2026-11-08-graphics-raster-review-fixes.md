<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D129 (2026-11-08): after the review of PR 234

- **What is stored is at most 10 MB, so it can always be thumbnailed.** The helper reads at most 10 MB for a thumbnail, while the panel would have stored up to 64 MB of its output. The safer side was to cap what is stored (a 2048 pixel PNG of noise that cleans to more than 10 MB is refused with a plain 413), not to raise the helper's input limit, which is also its memory bound.
- **A helper that ends with no answer is a 413, not a 502.** The unit's memory ceiling (256 MB) is below the address-space limit (512 MB), so a decode that runs out of memory is killed by the system and says nothing. The panel now tells the person the picture is too big to process and to try a smaller one. MemoryMax stays at 256 MB because a Pi 3 has under 1 GB. Row RA15 in the device testing list (progressive 40 megapixel JPEG) is NOT YET RUN.
- **The thumbnail route in the design is `GET /api/graphics/thumb?name=`**, as built. The Exif block is the first APP1 that starts with `Exif`, looking at no more than 16 APP1 segments.

