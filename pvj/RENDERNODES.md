# Render nodes (D94)

The box (Control and Playback) cues a render node (a laptop or a GPU box: Max/Jitter, VDMX, Resolume, MadMapper,
TouchDesigner) over OSC, and shows the node's picture back over NDI. A render node adds to the room; nothing the room
depends on lives only on one. If its picture goes, the box goes back to what played before.

Nothing here has met a real render node. The tests run on loopback only.

## What there is

- **Setup > Render nodes.** The Owner adds a node: a name, its host (an IPv4 address on one of the box's own
  networks, or the machine's `.local` name), its OSC port (1024 to 65535, UDP), its tool (any tool, or the Max patch),
  and its NDI picture, chosen from the NDI senders the box sees (it becomes allowed, as a pad's source does, D62). At
  most 8 nodes. An Operator sees the nodes with a state chip each and sends a cue by hand ("Send a cue").
- **A cue** is one OSC message: an address and up to 8 typed arguments (int, float, string, bool), and optionally
  "then show the node's NDI picture". A cue that shows a picture is checked for the picture first: if the picture is
  not on the network, nothing is sent.
- **A render cue on a pad** (the pad editor's fourth kind, beside a clip, a shader and an NDI source). Everything that
  presses a pad can then fire it: the panel, a Room scene, the schedule (through a scene), MIDI, OSC and TouchOSC,
  DMX. A Guest is refused such a pad and such a scene, as he is refused an NDI pad.
- **Health**, per node: whether its NDI source is on the network now, when a cue was last sent and to which address,
  and why the last one was not. OSC over UDP has no reply: "sent" means the cue left the box, never that the node took
  it.

## Where the box sends

The rule of an OSC state target (D82), checked when a node is saved and again at every send: a private or link-local
IPv4 address on a subnet of one of the box's own real ports (wired, Wi-Fi), never the subnet's network or broadcast
address, never the box itself, loopback or multicast, never the support tunnel's network; a port from 1024. A `.local`
name is checked for its form when saved and looked up at every send (at most 1 second from the panel, 0.2 seconds from
a controller, one lookup at a time per node, the answer kept 30 seconds); the address it gives is held to the same
rule. The socket's time to live is 1. The socket is opened for one datagram and closed: the box listens on no new port.

Caps: one cue is one datagram of at most 1200 bytes; at most 20 cues in any second to one node and 60 to all nodes
together. Over the cap a cue is refused (429), never queued.

## The way back

When a node's picture that a cue put on the screen goes, the box puts back what played before the cue: a clip or a
list from the media folder (from the clip that was playing, looping if it looped), a shader, Vibes, another NDI source
or a saved stream. If nothing played, or it cannot be put back, the box stops as Stop does. It is said once in the
journal and on the page ("Went back from Max's picture: ...").

What counts as gone, looked at once a second: the helper says the connection dropped; the source is no longer
announced; the picture stood still longer than the node's still time (10 seconds by default; 0 means never, for a tool
that sends only when something changes); the NDI helper does not answer; or the picture ended by itself. Once seen
gone for 3 seconds (`GONE_SECONDS`) the box goes back, so within about 4 seconds of the box seeing it. How soon NDI's
library or the network says that a sender has gone is theirs and was not measured.

Anything chosen after the cue (a tap, a scene, Stop) is newer and stands: there is no way back over it. A second cue to
the same node while its picture is up keeps the first "before".

## Who

| Route | What | Guest | Operator | Owner | Controllers, scene, schedule | Support, Owner's role | Support, below |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `GET /api/render` | the nodes and their health; host and port for the Owner only | no | yes | yes | no | yes | no |
| `POST /api/render` | add, change, remove a node | no | no | yes | no | yes, held to the box's networks | no |
| `POST /api/render/cue` | a cue by hand | no | yes | yes | no | yes | no |
| `POST /api/pads` with `render` | a cue on a pad | no | yes | yes | no | yes | no |
| `POST /api/play {"pad"}` on a render pad | fire it | no | yes | yes | yes, as before | yes | yes, as any pad press before |

Remote support follows the OSC state targets (D84), the nearest setting: the box sending to a device in the room. A
settings file brought by a helper keeps the box's own nodes when it names an IPv4 host that is not on one of the box's
networks now.

## Settings

`render` (`{"nodes": [{"id", "name", "host", "port", "ndi", "profile", "still"}]}`) and a pad's `render`
(`{"node", "address", "args", "show"}`), read with defaults; no schema change. An older release keeps both keys as
they are and sees an empty pad (tested with master's code at 1ed58bc). A settings file carries the section; an older
release refuses a file that has it ("sections this version does not know"), as it did for `ndi`. The diagnostics
file holds no host.

## Profiles

A profile is a tool's cues as address templates; a filled template is an ordinary cue and goes through the same
checks. The shape is documented beside `PROFILES` in `pvj/render.py`.

### max: the nxlx patch (the first, the owner's choice of 2026-10-16)

Max has no OSC address space of its own, so this scheme is this project's. Add, never rename: every patch made from it
depends on it. Default port 7400. The companion patch is `tools/max/nxlx-render-node.maxpat` (see its README).

| Address | Arguments | Means |
| --- | --- | --- |
| `/nxlx/scene` | int 0 to 999 | Go to scene N (the patch: one of four shapes) |
| `/nxlx/param` | string name, float 0 to 1 | Set a parameter (the patch: `scale`, `spin`) |
| `/nxlx/color` | string name, float r, g, b (0 to 1) | Set a colour (the patch: `bg`, `shape`) |
| `/nxlx/go` | none | The next look |
| `/nxlx/hello` | none | Is the patch there? The patch prints it; the box reads no answer yet (no new listening port) |

### Another tool: what is needed first

For VDMX, Resolume, MadMapper or TouchDesigner a profile needs, from the owner or the tool's own documentation:

1. The tool and its version, and where its OSC input is switched on, with its default port.
2. The exact addresses for "go to scene or clip N", "set a parameter" and "next" in that version, and whether they
   are fixed by the tool (Resolume's are) or made by the person (VDMX's and TouchDesigner's are).
3. The argument types each address takes (int, float, string; whether a trigger needs 1 then 0).
4. Whether the tool answers over OSC (to which port), if a reply is wanted.
5. The NDI sender's name the tool announces.

No closed product is taken apart for this: only its documentation and what it is seen to do.
