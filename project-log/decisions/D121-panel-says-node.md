<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D121. The panel calls the device the node, not the box (2026-11-07)

**The owner's words** (2026-11-06, while reviewing the panel design): "Change the reference to the Box to the Node", then "make the panel PR". He has said node for the Pi for some time; the panel still said box everywhere a person reads it.

**What changes.** Every word the panel shows: "This node", "Nodes in step", "Connect to your node", "the node keeps playing", the hints, the questions and the errors written in `pvj/web`. Changed only inside the strings the page shows. Comments, CSS, element ids, routes and keys stay as they are: `'box'` is still the key of Setup's group and of the sound source the node itself listens to, and the API, the settings and the files are unchanged, so nothing saved on a node moves.

**Other hardware keeps its word.** A "fader box" is a controller and stays one; "a laptop or GPU box running Max/Jitter" becomes "a laptop or GPU computer", since that machine is a render node, not this node.

**Not in this change.** The words the server sends (errors, states, set-up texts in `pvj/*.py`) are a second pull request, so a page can show "This node" beside a server message that still says box until that one merges. The documentation and the manual are not changed here.

**Cost.** None at run time. Tests that quote the panel's words quote the new ones; tests that quote the server's words wait for the second pull request.

This is one of the owner's decisions of 2026-11-06 on the panel (the others: a control centre that VJs can use, not an instrument; a Now area first; the title bar stays at the top). They are kept with the design files in `nxlx.systems/nxlx.mastercontrol/design/panel-direction_2026-11-06.md` until the panel work that carries them comes with its own decision.
