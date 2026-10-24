<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## A plan made outside the lock is checked again inside it (2026-10-24, D106)

The rebuild of the sources' shaders listed what to build under the lock and built it after letting go: anything that came to a source in between (a Stop, a clip, another shader) was undone by a build made for a world that had gone. When work is planned under a lock and done outside it, carry what the plan saw (here the program file's path) and look again under the lock just before acting; act only if it still holds. A test needs a hook between the two to show it: no timing will.

## A count that already sums the branches is not multiplied by them (2026-10-24, D106)

The effects' count sums every branch of the text, the PASSINDEX ones included; multiplying it by the passes counted each pass twice and refused a plain two-pass blur. Before scaling a measure by a factor, check what the measure already folded in.
