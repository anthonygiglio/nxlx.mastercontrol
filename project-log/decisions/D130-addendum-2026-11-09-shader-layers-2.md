<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## Addendum to D130 (2026-11-09): slice 2, how a fade into a program starts and what the renderer says

Slice 2 (pvj-render 0.10.0) adds `pvj-fade-to-program` as the design says. Three points the design left open, settled in the build:

1. The answer comes before the fade, not after. The command is answered when the program is built (or refused), and the fade is made only then, with the idle layer as the front and the clock started by the program's first drawn frame. While the program builds the front layer is unchanged and its picture goes on moving. A refusal therefore changes nothing on the screen, except that a fade that was running has ended on its incoming side (D98), because the idle layer is that fade's outgoing one.
2. The renderer says it has the command by a count, `program-fades`, in the `pvj-render` property, not by `layer-generators` alone. A 0.9.0 renderer says `layer-generators` and has no such command; the panel (`Player.layer_generators()`) asks for both.
3. A crossfade out of a layer whose picture is a generator is a fade even when the layer's clip is idle, which is the case once the panel stops carrying a shader on a blank clip (slice 3). A fade into a program with nothing on the screen is a cut from black, as a clip's is.

The sound of an outgoing clip is taken off at the command (a program has none), and its last frame is kept if it ends during the fade, as for a crossfade.
