<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## A growing process is not always a leak: ask malloc what is in use (2026-11-15)

The New renderer grew by 12 to 33 MB a crossfade on node-03 (J-157), and the first guesses were the things a crossfade makes: a decoder, GL textures, EGL images. None of them grew: libmpv's GL objects stayed at 51 to 54, the fences at 0, the threads at 63. The growth was all in malloc's heap, and mallinfo2 showed what was in use going up and down with the clip on the screen (113 MB for 1080p, 360 MB for 4K) while the free memory malloc held grew with the fades (244 to 452 MB). malloc_trim gave back 218 MB at once.

The cause is glibc's dynamic threshold for mapped blocks: it rises to the size of every mapped block freed, up to 32 MiB, so after the first big frame the decoders' frames and packets came from the arenas, and two decoders at once in a crossfade cut them up. A fixed threshold (mallopt M_MMAP_THRESHOLD) turns that off.

What we do now: when a process grows, read RssAnon by mapping (smaps: the heap, the arenas, the rest) and the heap's used and free (pvj-render answers `pvj-heap`) before reading code for a leak. If free grows and used does not, it is the allocator, not a lost pointer.
