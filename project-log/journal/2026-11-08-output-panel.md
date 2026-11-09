<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-08, the output in the panel: the connector, and the AV output of a Pi (`output-panel`)

- **Why.** The owner (2026-11-07): "include a way for the user to pick the output, including the AV output on Pi's". The node side is D128 (#225). This is the panel's part.
- **Setup, Screen output: the Output card** (the Owner's):
  - which connector is in use, and whether it was chosen automatically or by hand;
  - a choice of Automatic or each output the node lists (an output with nothing connected says so);
  - Use, which says the player takes the choice at its next start.
- **The AV output (composite)**, under the same card:
  - its state: on, off, or on or off after the next restart;
  - "A restart of the node is needed" in plain words when it is;
  - where it is not supported, the node's reason and no switch;
  - the TV standard;
  - Turn on or off, which first asks with the node's own costs. On a Pi 4 and 5, that there is no HDMI picture after the restart and the panel still works over the network.
- Through remote support the card shows all this and changes nothing (the node refuses both writes there).
- **Now, The node** (the Owner's): a line Output ("HDMI 1, automatic"; "the AV output needs a restart" when it does) with Open, to Screen output. It is read with the rows of Setup every 30 s.
- **Test.** panel.test.js, with the node's answers routed (the harness has no screens):
  - Use sends the connector chosen and says "next start";
  - the AV output's question reads the costs, HDMI off and "over the network", and sends enable, confirm and the standard;
  - the restart is said, and The node on Now says the output and the restart.
- **Run on the HP's WSL:** tests/ui/panel.test.js passes, run alone; tests.test_modules_themes passes.
- **Review of the PR.**
  - A refusal (400, 409, 503) is said in the card, which is read again (a refusal carries no state).
  - The button that sent a write, with its choice, waits for the answer, so a second press opens no second question and sends nothing more.
  - The question of the AV output waits 30 s (its costs are long; confirmRow takes a time now), asks plainly where the node gives no costs, and says HDMI comes back when turning it off.
  - The node's note is not said twice, and no sentence ends in two full stops.
  - The test also answers Not now (nothing sent), turns the AV output off, shows a refusal in the card, and shows a node without AV (its reason, no switch) and without the system helper (no writes).

- panel-ui flake on the Screen output page: Rotate, Flip and Mute redraw their column 200 ms after the press, and when the person had already left that page redrawPart drew everything, wiping the Output card (its Saved line went with it). Those timed redraws now draw only if the column is still on the page.
