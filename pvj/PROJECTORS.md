<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Projector control (PJLink)

The old panel had Beamer On and Beamer Off buttons. This does the same over the network with **PJLink**, the standard most network projectors speak (Epson, Panasonic, NEC, Sony, Hitachi, Christie, Optoma, Canon, BenQ and others). It is usually switched off in the projector's own network menu; switch it on there and note the password, if any.

## In the panel

Switch the **Projector control** module on under System > Modules (beta, off by default), then use System > Projectors. A full-access device adds a projector (a name, its IP address or name, the port, 4352 unless changed, and the PJLink password if one is set) and removes it. A guest sees the list and the status only.

**What the projector says it is.** When a projector is added the box asks it, in the background, for its name, maker, model, other information, PJLink class and its list of inputs, and shows them under the projector. Adding never waits for the projector: one that is switched off at the wall is added all the same and shows "Details not read yet". Many projectors will not list their inputs while in standby; the box asks again by itself the first time it sees the projector switched on. **Refresh details** asks again at any time (after a firmware update or a swapped projector, for example). What the projector will not say this time, or says in a form that is not the standard's, keeps its older value: one odd answer never empties the input list.

**Status without asking.** While the module is on, the box asks each projector about every 45 seconds (every 10 seconds while it warms up or cools down, and at once after any button) for its power state, input, mutes, lamp hours and warnings, and shows them: "On · input Matrix (Digital 1) · picture muted · lamp 1234 h". The projectors are asked a couple of seconds apart, not all at the same moment. The status is kept in memory only. Only the power state decides whether a projector "answers": lamp hours, warnings, an input or mutes that it will not give, or gives in a form that is not the standard's, are shown as not known, and the rest stays. The checks stop when the module is switched off or the projector is removed: the command being sent at that moment is the last one, and nothing is saved afterwards. **Check** still asks for the power state on the spot. After any button, also one that failed, the status is read again at once.

**A presenter (play and mix) or full-access device can:**

- switch each projector **on** or **off** (with more than one projector there are also All on and All off);
- **mute** the picture and the sound separately (Mute picture, Mute sound) or together (Mute both, Unmute both). Some projectors can only mute both together; the panel then says "this projector cannot mute the picture and the sound separately";
- choose the **input** from the projector's own list. PJLink class 1 numbers its inputs by kind: RGB 1 to 9, Video, Digital (HDMI, DVI, SDI and the like), Storage and Network; which socket is "Digital 1" is in the projector's manual. A full-access device can give each input a **label** of its own ("Matrix", "Box"), which is then shown instead. Labels have their own chooser, "Label an input...", under the input chooser: pick the input there, type the label, Set label (an empty label takes it away). Labelling sends nothing to the projector and does not switch its input. A label is kept if the projector later stops listing that input (it is just not shown), so a refresh never deletes what was typed; it goes with the projector when that is removed.

Switching on takes the projector a minute or so to warm up, and switching off starts a cool-down; during those the projector refuses other commands as "unavailable". An **input change** that is refused that way (the projector is still warming up, or is off) is tried again in the background every 5 seconds for up to 90 seconds; the panel says so, and then says either "Input switched to Matrix." or "Could not switch to Matrix: it was still not ready after 90 seconds (is it switched on?)". Choosing another input in the meantime replaces the first choice: input changes to one projector go out one at a time, and a retry is never sent after a newer choice, so the last choice made is the one that stands. Mutes are not retried.

**Lamp hours and warnings** are also on System > Health, one line per projector: the lamp hours and the projector's own warnings and errors (fan, lamp, temperature, cover, filter, other). A projector that does not answer shows as "?" there, not as a fault, since it may simply be switched off at the wall. A projector that reports an error makes the box's overall health "bad", a warning "warn".

## Schedule and OSC

- The weekly schedule can switch every projector on or off (`projector_on`, `projector_off`), and can run a legacy start script such as `startlessonce05` (`preset`).
- The old OSC addresses `/beameron` and `/beameroff` switch every projector on or off (on press).
- Input selection and the separate mutes are not in the schedule or OSC yet: an input belongs to one projector, so an entry would need a projector and an input of that projector. That comes with groups and scenes (Phase 3 below).
- From OSC and the schedule the commands are sent in the background, so a projector that is off the network never delays the next cue or entry. A failure is written to the log (OSC) or shown under the entry's "Last run" (schedule).

## Safety

- **Private networks only.** A projector must be on a private or link-local address (10.x, 172.16 to 31.x, 192.168.x, 169.254.x, or the IPv6 equivalents); a name must resolve to one. It is checked when the projector is added and again before every command, so the box cannot be used to reach the internet. Loopback and the cloud metadata address 169.254.169.254 are refused.
- **Passwords** are stored in the box's settings file (readable by the panel service only) and never sent back to the panel or the API; the list shows only "password set". PJLink sends an MD5 digest of the projector's random number and the password, never the password itself. PJLink security is weak by design, so keep the show network private.
- Each command (connect, greeting and answer) must finish within 10 seconds. Looking up a name is not bounded by the box, so use an IP address for a projector if you can. All projectors are asked at the same time, and the panel waits at most 30 seconds in all, so an unplugged projector does not hold up the others.
- One command at a time per projector, each on a connection of its own: many projectors accept only one connection, so a second button press, or the background check, waits for the command before it. That wait is inside the same 10 seconds: a command that cannot start and finish in time ends with "the projector is busy with another command" and is not sent. "The same projector" means the same address and port after a name is looked up, so two spellings of one device share one connection. The private-address check is made again before every single command, the background ones too.
- The same projector (the same address and port, also under another name) cannot be added twice.
- At most 8 projectors, and at most 8 background threads in all, counting those of removed projectors that are still finishing a command. A projector beyond that waits ("Waiting for an earlier check to end") until a thread is free; a name that cannot be looked up can hold a thread for as long as the system's lookup takes. Each projector's checks have their own stop signal, and switching the module on and off quickly never leaves two threads for one projector.
- Text from a projector (its name, maker, model) is stripped of control and format characters (escape codes, right-to-left overrides, line separators, zero-width marks) and cut to 64 characters (the name) or 32 (maker, model, other information) before it is stored or shown; names and labels typed in the panel may not contain such characters either. An input can only be chosen from the list the projector itself gave.
- A set command (power, mute, input) counts as done only when the projector answers OK. An answer that never ends, or is cut off before its end, is refused.
- Every line sent ends with a carriage return alone, as the standard says.

## API

- `GET /api/projectors` (view): `{"enabled", "projectors": [{"id", "name", "host", "port", "has_password", "details", "inputs", "status"}]}`. Nothing is asked of a projector here.
  - `details`: `null` until read, then `{"name", "maker", "model", "info", "class", "inputs": ["11", "31"], "read": time}`; a value the projector has never given is `null`.
  - `inputs`: `[{"code": "31", "name": "Digital 1", "label": "Matrix"}]`.
  - `status` (`null` while the module is off). Before the first check it is only `{"pending_input": null, "notice": null, "waiting": false}`, with no `ok`; `waiting` is true while no thread is free for this projector. After a check: `{"ok", "checked": time, "power": "off|on|warming up|cooling down", "input", "mute": {"picture", "sound"}, "lamps": [{"hours", "on"}], "warnings": {"fan": "ok|warning|error", "lamp", "temperature", "cover", "filter", "other"}, "pending_input", "notice": {"ok", "text"}, "waiting"}`, or `{"ok": false, "checked", "error", "pending_input", "notice", "waiting"}`. `input` and `mute` are `null` unless the projector is on; `lamps` or `warnings` are `null` if the projector would not say. `pending_input` is an input change being retried, `notice` how the last retried one ended.
- `POST /api/projectors` (full): `{"add": {"name", "host", "port"?, "password"?}}`, `{"remove": id}` or `{"label": {"id", "input": "31", "label": "Matrix"}}` (up to 24 characters; empty removes it; nothing is sent to the projector). Adding a projector that is already in the list gives 409.
- `POST /api/projector` (live): `{"id": id or "all", "action": "on|off|mute|unmute|mute_picture|unmute_picture|mute_sound|unmute_sound|input|identify|state", "background"?: true}`. `mute` and `unmute` are picture and sound together, as before. `input` needs one projector and `"input": "31"`, a code from that projector's list (409 if the list is not known yet). `identify` is Refresh details. With `background` the answer is `{"started": true}` at once. Answers `{"results": {id: {"ok", "power"?, "pending"?, "error"?}}}`; `pending: true` means the input change is being retried in the background. A single projector that does not answer gives 502 with the reason.
- `GET /api/health` (view) has `"projectors": [{"id", "name", "state": "ok|warn|bad|unknown", "text"}]`.

## Not verified on real hardware

**No real projector has been tested.** Not one command of this has been sent to a real projector, of any make. Everything here was checked against the published standard ("PJLink Specifications", version 1.04, JBMIA, 2013-12-10, the class 1 document) and tested against a fake projector in `tests/test_projector.py` that was written from that document, not from the client. What a real projector does where the standard leaves room is unknown until one is tried, in particular:

- which questions it answers in standby (the standard lets it answer "unavailable"; the box then shows what it has and asks again when the projector is on);
- how long it stays "unavailable" after power-on, and so whether 90 seconds of retrying an input change is enough;
- whether it can mute picture and sound separately;
- whether its lamp hours and warnings are filled in at all (a laser projector may report 0 hours or none);
- how many connections it takes at once, and whether a check every 45 seconds bothers it or keeps it from sleeping.

The tests cover: with and without a password, a wrong password, an unknown command, a display without a lamp, a projector that is off the network, a slow sender, a device that is not a projector, one that goes silent after its greeting, answers that never end or are cut off, bytes that are not UTF-8, six commands at once to one projector, one projector under two names, answers in lower case, a name with accents, an empty name, answers that are not in the standard (they do not wipe the status), a set command answered with something other than OK, warm-up and cool-down, the input retry (works, gives up, is replaced, never overrides a newer choice, is ended by switching the module off), a name that later points at a public address, that nothing is sent or saved once the module is off, that there are never more than 8 threads, and that switching the module on and off 20 times leaves no thread behind. Class 2 features (the projector's own input names, volume, freeze, search, status notices) are not used.


## Plan: a general PJLink control system (D36)

Only the PJLink standard, so it works with any brand. Command details to be checked against the published PJLink specification before building; nothing is claimed about real projectors until tested on one.

**Phase 1, class 1 (every PJLink projector). Built (see above); not tried on a real projector.**
- Identify on add: name (`NAME ?`), maker (`INF1 ?`), model (`INF2 ?`), other info (`INFO ?`), class (`CLSS ?`), inputs (`INST ?`); shown in the panel.
- Input selection (`INPT`) from the projector's own list, with a friendly label per input ("Matrix", "Box"). A change while warming up is answered "unavailable" (ERR3); retry for up to about 90 seconds, then say so plainly.
- Picture and sound mute separately (`AVMT 11/10`, `21/20`) and together (`31/30`).
- Health: lamp hours (`LAMP ?`) and the projector's warnings (`ERST ?`: fan, lamp, temperature, cover, filter, other) in the Health card.
- Background status every 30 to 60 seconds, staggered, so the panel shows on, off, warming up or cooling down without asking each time.

**Phase 2, class 2 (used only when `CLSS ?` says 2).**
- Volume up and down (`SVOL`; class 2 has steps, not a level).
- Freeze (`FREZ`), the projector's input names (`INNM ?`), the incoming resolution (`IRES ?`).
- "Find projectors": the class 2 search on the private network, to add one with a tap.
- Status notices sent by the projector, so the panel updates at once.

**Phase 3, the room.**
- Groups ("Main wall", "Painting wall", "All").
- Scenes: power, input, mute and volume per group, together with what the box plays.
- A Room screen for staff (presenter and guest codes): per group on or off, source, volume, All off.
- Projector actions from the schedule, OSC, MIDI and DMX.

**Safety and tests.** The rules above stay (private networks only, passwords never shown, one command at a time per projector, time limits). The fake projector grows a class 2 mode, with tests for every refusal and timeout. Epson's own protocol (exact volume levels) is a possible later add-on, not part of this plan.
