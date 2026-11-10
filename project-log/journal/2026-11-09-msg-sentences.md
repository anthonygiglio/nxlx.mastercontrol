<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-09, What the message line says when a press goes wrong or is slow

- **Why.** A resilience probe in the sandbox (scratchpad tool) answered the panel's requests badly on purpose:
  - **The node not answering:** handled well. The "Connection lost?" banner came in 1.5 s and went 0.9 s after the node answered again, on the same screen.
  - **The node forgetting the device:** handled well, back to pairing.
  - **A refused press** showed the node's reason as it is sent, "the player is restarting". The node's errors are phrases: 72 of the 73 in pvj/ begin in lower case, made to sit inside the panel's sentences.
  - **A lost request** said "no connection".
  - **A press the node took 12 s to answer** said nothing at all. The line kept the previous error the whole time, so it looked as if that press had failed too.
- **What.**
  - Where the panel shows an error as a line of its own (`say` and `sayAt` with an error), it begins with a capital (`sentence()`). The node's words are kept, and so are the panel's own sentences around them.
  - A lost request says "No connection to the node".
  - A press not answered in 1.5 s says "Waiting for the node..." in the message line, in place of what was there. The answer then takes the line as before; an answer that says nothing clears the waiting words.
- **Checked.**
  - The probe again: "The player is restarting", "Waiting for the node..." after 1.5 s, and the line cleared after the answer.
  - tests/ui/panel.test.js gives Freeze a 500 with a lower-case reason and checks the line says it with a capital, then holds a press back and checks the waiting words come and go.
