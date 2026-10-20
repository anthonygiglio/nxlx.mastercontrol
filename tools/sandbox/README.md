<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The design sandbox

The real panel on your own computer, with no node: every role, every width, and a set of named states (scenarios),
for looking at the panel, clicking through it, and capturing it for Figma. A dev tool: it never ships on a box.

## Run it

```bash
python3 tools/sandbox/serve.py
```

It prints an address, `http://127.0.0.1:8700/`. Open it in a browser: choose a scenario and Load it, choose who is
looking (Owner, Operator, Guest, Not paired) and a width (Phone 390, Tablet 820, Laptop 1440, Wide 1728), then "Open in
a window". Stop it with Ctrl+C.

- `--scenario playing` starts with another scenario (the default is `idle`); `--port 8800` uses other ports.
- Needs Python 3 (standard library only) and the repository. With **mpv** installed, clips really play (headless: no
  picture and no sound anywhere); without it the panel says the player is not running.
- The panel is on the next four ports: Owner 8701, Operator 8702, Guest 8703, not paired 8704. You can open those
  directly too, side by side: each port is its own device, so no cookie or pairing is needed.
- Everything listens on 127.0.0.1 only. Nothing reaches a real node, projector or network: the projectors are fakes on
  this computer, the network is a fake, and the node's address is never used.

**For a capture** (Figma, a screenshot), use the address under "Open the panel":
`http://127.0.0.1:8700/open?scenario=playing&role=guest`. It loads that scenario if another one is running, then goes
to that role's port, so the same address always gives the same page.

## What each part is

- `serve.py`: the switcher, its page (`page/`), and the one box process it starts per scenario.
- `box.py`: one box: the real panel (`pvj/server.py`'s own build and handler) in a temporary folder, with the fakes of
  the browser test's harness (`tests/ui/harness.py`), one paired device per role, and the scenario put in place through
  the box's own routes. A new scenario is a new process, so nothing is left from the one before.
- `scenarios/`: one JSON file per scenario. `_base.json` is shared and not listed.

## Add a scenario

Copy one in `scenarios/` and change it. The name of the file (lower case, digits and dashes) is its name in the
address. The keys:

| Key | What it is |
| --- | --- |
| `title` | its name in the list (needed) |
| `about` | one or two sentences of what it shows |
| `extends` | another file of the folder (`"_base"`), whose keys this one replaces; its `steps` run first |
| `clips`, `pictures` | file names put in the Library: clips are a 2 second test clip (or a name only, without mpv), pictures are PNGs of one colour with alpha (for masks and logos) |
| `modules` | `{"projector": true, "room": true, ...}`: modules switched on or off, as on Setup |
| `projectors` | `[{"name": "Main wall", "state": "on"}]`: fake PJLink projectors; `state` is on, off, warming, cooling or silent (nothing answers); `errors` is PJLink's six digits (fan, lamp, temperature, cover, filter, other; 1 a warning, 2 an error) |
| `steps` | requests to the box's own routes, in order: `{"as": "owner", "method": "POST", "path": "/api/...", "body": {...}}` (`as` and `method` may be left out: owner, POST). A value `"@projector:Main wall"` or `"@group:Stage"` is the id the box gave it |
| `guests_locked` | true: guest controls are locked |
| `offline_after` | seconds after which the box stops answering, as on a lost connection |
| `slow_masks` | seconds each combining of masks waits first, so "Combining..." stays to be looked at |

A step the box refuses is listed on the switcher's page and the rest go on: a scenario that no longer fits the panel
says so. Check a new one with `python3 -m unittest tests.test_sandbox` (every scenario is started on a real box there).

## Not covered

MIDI controllers, DMX, OSC senders, NDI sources, render nodes and sound input have no fakes here, so their pages show
their empty or not-set-up states. The remote support banner and HTTPS are not shown.
