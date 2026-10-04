<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The room: groups, scenes and the Room screen (beta)

For a room that staff run from a phone: a few projectors on the walls, consoles on the projectors' own inputs, and the box playing films and ambience. This is Phase 3 of the projector plan in [PROJECTORS.md](PROJECTORS.md); everything it sends to a projector goes through the PJLink client described there.

Switch **Room (groups and scenes)** on under System > Modules (beta, off by default). It needs **Projector control** to be on, and the projectors added under System > Projectors.

**No real projector has run any of this.** It is tested against the fake projector in `tests/test_projector.py`, on loopback only. See "Not verified" at the end.

## Groups

A group is a named set of projectors: "Main wall", "Painting wall". **All** is every projector and is always there; it needs no entry. A full-access device adds, changes and removes groups on the Room screen under "Set up the room". Up to 8 groups, names up to 32 characters, each name once ("All" is taken), 1 to 8 projectors in a group. A projector may be in more than one group. A projector that is later removed under System > Projectors is simply skipped; the group stays.

## Scenes

A scene is a named preset a person taps once. Up to 24, names up to 32 characters. For each group (and for All) it says:

- **power**: switch on, switch off, or leave;
- **source**: one of the projector's inputs, stored by the projector's own input code (`31`) and shown by its label ("Console"), or leave;
- **picture** and **sound**: muted, not muted, or leave.

A group that is switched off cannot also get a source or a mute. If one projector is in two groups of a scene, the later line stands.

And one thing **the box** does: leave alone, play a clip (loop or once), play a pad, play a saved stream, start Vibes, stop Vibes, Stop, or Blackout. This is a short list of existing API calls (`BOX` in `pvj/room.py`), each made as a presenter through the same checks as a tap in the panel; a later feature is one more line in that list. A scene that plays something (a clip, a pad, a stream, Vibes) also takes a blackout off, so the screen is not left dark.

### How a scene is applied

- The tap answers at once; nobody waits for a projector. The Room screen shows progress and then a plain result per group: "Main wall: on, input Console. Painting wall: no answer." While a projector that took the command is still warming up or cooling down, its line says "switching on (warming up)" or "switching off (cooling down)"; it says "on" or "off" once a status check agrees. A projector is named only when the projectors of one group differ ("Both: Left: on; Right: no answer").
- Per projector the order is power, then the source, then the mutes. A projector that answers "unavailable" (it does while warming up or cooling down) is asked again every 5 seconds for up to 90 seconds. A refused power command counts as done only if the projector itself, asked at that moment, says it is on its way there; the status the box remembers is never taken as proof. The source change is the Phase 1 input retry itself (`Monitor.set_input`), so it follows the same rules as the input chooser on the Projectors card. Picture and sound muted the same way go out as one command, since some projectors cannot mute them apart.
- A projector that does not answer costs one attempt and is reported as "no answer"; its later steps are skipped. The other projectors are not held up.
- A mute for a projector that is switched off (and that the scene did not switch on) is reported at once as "it is switched off", not after 90 seconds.
- **A second scene replaces the first.** What the first had not sent yet is dropped, also for projectors the second scene does not mention; an input change still being retried for it is stopped; and each projector's commands go out from one thread, in the order they were chosen. A source is sent only after asking "is this still wanted", inside the same lock the Projectors card's input chooser uses, so an older command does not follow a newer one. (The one gap left is a command already on the wire when the newer choice is made: the newer one is then sent after it.)
- **The same scene again changes nothing that is still to come.** A controller that repeats itself, or an impatient thumb, does not cancel the command under way, and the box does not start the scene's clip again within two seconds. Once a scene's steps are done, tapping it again sends them again.
- **A group button joins in.** On, a source or a mute pressed while a scene is still under way replaces only its own kind of step for that group's projectors (a mute pressed during warm-up does not cancel the scene's source). Off replaces everything still to come for them.
- **A button outside the room counts too.** On, Off, a mute or an input pressed on the Projectors card, the old OSC `/beameron` and `/beameroff`, or a `projector_on` or `projector_off` schedule entry drops what the room still had to send of that kind for those projectors (Off drops everything), before it sends. So a scene that is still waiting for a projector to cool down does not switch it on after someone has pressed Off.
- Switching the Room module off, removing a projector, removing the scene or the group, a factory reset, or the panel shutting down ends what is still to come; nothing more is sent.

## The Room screen

While the module is on, the panel has a **Room** tab, and a presenter (play and mix) or guest (view) device starts on it when it joins or opens the panel; someone who is already on another screen when the module is switched on is not moved. The module is switched on System > Room, which also says how many groups and scenes there are. Full-access devices keep the whole panel and get Room as one more tab.

- **Scenes**: one big button each, and under them how the last one went.
- **Each group**: its state in a word (On, Off, Warming up, Cooling down, No answer, Mixed, Checking), its source and mutes, and buttons for On, Off, each source by its label, Mute picture and Mute sound.
- **Everything**: All on, and **All off**, which first asks "Turn off all 2 projectors? They need about a minute to cool before they can come on again." with **Turn off** and **Keep them on**. A double tap does nothing: "Turn off" is not taken in the first moment after the question appears. After 8 seconds without an answer the buttons come back.
- **Let someone in** (folded, for a presenter or the owner): the guest code part of System > People and codes, here so that staff need not leave the room's controls to let a visitor watch: how long the code works, **Guest code**, the code with its QR code, **Show on the room screen**, **End this code**. Nothing is asked of the box until it is opened. A guest does not get it, and neither does a remote support login (the box refuses codes through the support connection). See "Letting other people in" in the [manual](../docs/MANUAL.md) and D47.
- The screen asks the box every 2 seconds, less often while the box does not answer, and not at all while the tab is hidden or once the device is no longer paired.
- A **guest** sees the scenes' names and every state, and has no button that does anything.
- A **full-access** device also has "Set up the room": groups, scenes, and (with the MIDI module on) a MIDI button per scene that learns a control for it.

The state comes from the background checks of the Projectors module (about every 45 seconds, every 10 while warming up or cooling down, and at once after any button), so it can be a little behind.

## Schedule, OSC, MIDI and DMX

All of these make the same API call as a tap on the Room screen, as a presenter, so the module switch, the role, the limits and every check apply. A scene that plays something takes a blackout off from here too.

- **Schedule**: the action `scene` with a scene id (`"scene": "1a2b3c4d"`); in the panel, "Apply a Room scene" under System > Schedule. A scene that was removed since shows "no such scene" under the entry's Last run. Group buttons are not schedulable by themselves: make a scene for it.
- **OSC** (see [OSC.md](OSC.md)): `/pvj/scene/<n>` applies the n-th scene of the list on a press; `/pvj/scene` takes a name (a string) or a number (2 or 2.0). `/pvj/group/<n>/on` and `/off` (press), `/pvj/group/<n>/mute`, `/mute_picture`, `/mute_sound` (1 mutes, 0 unmutes) and `/pvj/group/<n>/input` (the input code, `31`); `all` in place of the number is every projector.
- **MIDI** (see [MIDI.md](MIDI.md)): the trigger action `scene` with a scene id. It is assigned from the Room screen's set-up (the MIDI button next to a scene), which uses the same Learn; the mapping is then listed under System > MIDI controllers.
- **DMX**: not built. The DMX layout is a fixed row of channels with no place for a scene id; it would need a layout of its own.

A schedule entry or MIDI mapping keeps the id of a scene that is later removed; it then does nothing and says so.

## Safety

- Every command to a projector is one PJLink command through the Projector module's client: the private-address check before every command, one command at a time per projector, the 10 second deadline ([PROJECTORS.md](PROJECTORS.md#safety)).
- At most one Room thread per configured projector at work, and never more than 8 Room threads in all, whatever is tapped and however fast; they end when their work is done. These are in addition to the Projector module's own 8 background threads.
- **Give projectors an IP address, not a name.** Looking a name up is the one step the box cannot put a time limit on. If a lookup hangs, that projector's thread is stuck in it; after 25 seconds a new choice for that projector (Off, say) leaves the stuck thread behind and goes out from a fresh one, as long as that stays within the 8 threads. What the stuck thread was about to send is never sent.
- **Through the remote support tunnel** a support login with presenter rights can apply a scene like any presenter, and the scene's box action runs with it (it only plays, stops or blacks out, which a presenter may do anyway). Editing groups and scenes needs full access as everywhere.
- A group or scene name needs a letter or a digit, and names are compared without regard to case, spaces or look-alike forms (full-width letters, the Cyrillic and Greek look-alikes of "a" and "l"), so nothing that reads as "All" or as a name already there is accepted.
- No address and no password is in anything the Room screen is given.
- **A projector edited to a new address while a scene is under way** (System > Projectors > Edit): every step reads the stored address when it is its turn, so the steps not yet sent go to the new address. An input step that was already handed over for the old address is not sent anywhere, and one the old address refused is not retried at the new one; the result for that group then says "stopped". Tap the scene again.
- Names may not contain control or format characters. Every id, input code and name is checked as a whole (a trailing line break is refused).
- Editing needs full access; applying a scene or pressing a group button needs a presenter; looking needs any paired device.

## Settings

A top-level section `room` in the settings file, read with defaults, so the schema stays at 13 and an older file needs no migration:

```json
"room": {
  "groups": [{"id": "1a2b3c4d", "name": "Main wall", "projectors": ["<projector id>"]}],
  "scenes": [{"id": "5e6f7a8b", "name": "Console night",
              "groups": [{"group": "1a2b3c4d", "power": "on", "input": "32", "picture": "leave", "sound": "mute"},
                         {"group": "all", "power": "leave", "input": "", "picture": "unmute", "sound": "leave"}],
              "box": {"action": "file", "file": "intro.mp4", "loop": true}}]
}
```

`box.action` is one of `leave`, `file` (`file`, `loop`), `pad` (`pad`: `[bank, index]`), `stream` (`stream`: the saved stream's id), `stop`, `blackout`, `vibes`, `vibes_stop`. The section is checked by `room.validate`, which the settings export and import use too (`check_room` in `pvj/boxcare.py`). Only the form is checked there: a projector, group, clip or stream that is not on the box is skipped or reported when the scene runs.

## API

- `GET /api/room` (view): `{"enabled", "scenes": [as stored], "groups": [{"id", "name", "projectors": [ids], "state": "on|off|warming up|cooling down|no answer|mixed|checking|empty", "text", "inputs": [{"input", "name", "label"}], "input": the source in use or null, "mute": {"picture", "sound"}, "last": how its last button went or null}], "all": the same for every projector, "job": {"scene", "name", "started", "running", "ok", "text"} or null, "projectors": [{"id", "name"}], "limits", "box": the box actions}`. Nothing is asked of a projector here.
- `POST /api/room` (full): exactly one of `{"group": {"id"?, "name", "projectors": [ids]}}`, `{"remove_group": id}`, `{"scene": {"id"?, "name", "groups": [...], "box": {...}}}` or `{"remove_scene": id}` (more than one is refused). With an id the group or scene is replaced; without, it is added. Removing a group takes it out of the scenes, which are kept.
- `POST /api/room/scene` (live): `{"scene": id}`, `{"number": n}` (its place in the list, from 1) or `{"name": "..."}`. Answers `{"started": true, "scene", "name", "box": {"ok", "text"} or null}` at once.
- `POST /api/room/group` (live): `{"group": id or "all"}` (or `"number"`, 0 for all, or `"name"`), `"action": "on|off|mute|unmute|mute_picture|unmute_picture|mute_sound|unmute_sound|input"`, and `"input": "31"` for `input` (a projector of the group that has no such input says so; the others switch). Answers `{"started": true}` at once.

## Not verified

- **No real projector, and no real room.** How long an Epson stays "unavailable" after power-on decides whether 90 seconds is enough for a scene's source and mutes; that is unknown until one is tried ([PROJECTORS.md](PROJECTORS.md#not-verified-on-real-hardware)).
- The Room screen has run only in the automated browser test (a phone-sized window against two fake projectors). Nobody has used it on a phone in a room.
- OSC was tested by its translation and through the API, not with a real controller; MIDI by the mapper with made-up messages, not with a controller; the schedule with a made-up minute.
