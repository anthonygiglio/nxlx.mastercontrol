<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D135. A loop restart or a seek does not take the picture away (2026-11-14)

The picture under an effect (Effects.picture) is the last one read for up to PICTURE_GRACE (1 second) while mpv has no video-params and `path` is still the same item. Why: at the start of each pass of a short loop mpv has no video-params for a moment, and a Strobe press there was refused with 409 and released. Cost / rules out: a source that loses its picture while the same item plays is believed for one second; a stop, another file, and a source that never had a picture are refused as before.
