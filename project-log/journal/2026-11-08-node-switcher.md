<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, The node switcher in the title bar (fleet step 2b)

- **Why.** Step 2b of the fleet plan the owner approved on 2026-11-07: after discovery (#224, D127), the panel lists the other nodes and opens one.
- **What.** A Nodes button beside the title bar pill, for an Owner and an Operator, not over remote support (the node refuses `GET /api/nodes` there). It opens "Nodes on this network" as a full-width line of the title bar:
  - Each node is a row keyed by its id, with its name (as text, never markup), its address beside it, its kind and version.
  - Open is a plain `https://address[:port]/` link in a new tab, with `rel="noopener noreferrer"` and no referrer: nothing of this node (token, PIN, the page address) goes with it, and the other node asks for its own pairing.
  - No Open for a node without a secure connection, an address that is not an IP address, or a conflict (one id, or one name, at two addresses), each with a line that says why.
  - This device remembers by id where it opened a node (`localStorage`, nothing if storage is blocked) and says when that node's address changed.
  - The list is asked only when Nodes or Look again is pressed: never before, never on a timer, nothing after Close.
  - The Owner has Look for nodes, and Stop looking for nodes (asked first, since it also stops the announcement), on `POST /api/nodes/discovery`.
- **Its own Setup page.** The owner's word, 2026-11-08: "put the discovery switch on its own Setup page too". This node > Nodes on this network says what looking does now (on; off; on but avahi missing or stopped), has Look for nodes or Stop looking for nodes (asked first), the same switch as in the list, and Show the nodes. The row has no url, so the Setup index and Now never ask for the list (each look sends multicast DNS questions); the page asks once when it opens. Not shown over remote support.
- **What the panel cannot do.** It cannot see or pin the other node's certificate: the browser checks it against the root installed on the device. The list says to stop at a certificate warning.
- **Test.** panel.test.js fakes five nodes: no request before Nodes, one on pressing, the https link and its port, no opener or referrer, a name with markup shown as text, no Open for a plain node or the two rows of a conflict, the address-changed line, Look again, and nothing asked after Close. On the Setup page: the state, Stop asked first, `{on: false}` then `{on: true}` sent, the state following (the requests are answered by the test, so the node is not changed).
