<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-07, On the screens says which surfaces each source is on (`now-surfaces`)

- **Why.** Follow-up (c) after Now (#214), from the owner's direction: "each source, what it plays and on which surfaces".
- **What.** While the mapping is on, each source's line on Now ends with the surfaces that show it ("tunnel.mkv · on Left, Centre"), or "on no surface". Each surface is counted under the source the node draws it from (D101, mapper.py shown_source): its own source when the player has that many (the mapper's `sources`), Source 1 otherwise. A hidden surface is left out, and a failed answer of the mapping says nothing. Without the mapping, or with no surfaces, nothing is added: the source is the whole screen, as before. Now reads the mapping every 5 s, with Health, while the Projection mapping module is on.
- **Test.** panel.test.js, once the mapping is on, with the node's answers changed on the way (three sources, Source 3 playing, four surfaces):
  - with three sources, Source 2 is on Quad and Source 3 on Left and Centre (the hidden one left out), and Source 1 on no surface;
  - with one source, every surface falls back to Source 1;
  - with the mapping off, nothing is said.
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone.
- **Review of the PR.** Each surface is counted by the node's own rule (D101, shown_source), as described above; at first it was counted by the source it names. A failed answer of the mapping clears the words. The mapping is asked for only by a device that may see it.
