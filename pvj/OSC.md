# OSC control

OSC 1.0 over UDP, for TouchOSC, Resolume, QLab, Max, Chataigne, Companion and anything else that can send OSC. Switch it on in **Setup > OSC** with the switch at the top of the page (full-access devices only); there is no other switch, and the port and networks have their own Save button. Default port **9876**, the same as the old receiver, so existing controller layouts keep their target. The box receives; it sends its state back only where the Owner switched that on and named the device ("State sent back", below).

## Safety

- **Off until you switch it on**, and it only listens while on.
- **Only private networks may send**: loopback, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local `169.254.0.0/16` (a direct Ethernet cable between laptop and box) and their IPv6 equivalents. Add your show network under "Also accept from these networks". Ranges wider than a /8 are refused. UDP source addresses can be forged, so this keeps the internet out; it does not stop a hostile device on your own network, so keep the show network private. This is the outer wall, and it always applies.
- **Three more locks** ("Who may send", below): a list of single devices, a rule that the sender is a paired device, and a key in the address. They narrow who may send inside the outer wall. None of them is encryption. On a box where OSC was never on, the paired-device rule comes on with the first switch-on (D83); a box that updates keeps what it has. With OSC on and all three off, the panel says "OSC is open".
- **Nothing dangerous is reachable.** `/shutdown`, `/reboot`, `/rebootall`, `/shutdownall`, factory reset, password changes and updates do not exist over OSC. They need a paired full-access device in the panel.
- **Nothing is ever sent unless the Owner switched "Send state back" on and named the device it goes to** (D82, "State sent back" below). It is off on every box until then, and a box that updates sends nothing. Even with it on, the box never answers a stranger: no packet decides where anything is sent, a refused or malformed packet is answered with nothing, and there are no error replies. So the receiver cannot be used to bounce traffic at someone else.
- Each sender is limited to 200 messages a second (burst 400); packets are parsed strictly and bounded in size, arguments, and bundle depth. Log lines about bad or refused packets appear at most once every 10 seconds.
- Every value goes through the same validation as the web panel. A wrong value is dropped and logged, never applied.

## Who may send: three locks

On **Setup > OSC**, under "Who may send", for a full-access device. **What changes when you update: nothing.** A box where OSC is on, or ever was set up, keeps its locks exactly as they are, and the receiver behaves as before. **On a box where OSC was never on, the first switch-on brings one lock with it** (D83): "A sender must be a paired device". OSC has no login: with it on and every lock off, anyone on the network can play, stop, black out and move the picture, and the panel's roles and the guest lock do not apply to OSC. So a new box starts with that lock, and the page says beside the switch what it asks of you: open the panel on the tablet and pair it once (as an Owner device, or choose "Owner and operator devices"), and open it again if the tablet's address changes. Switch it off if you would rather use another lock. **While OSC is on with all three locks off**, the OSC page, its row in Setup and the health page say "OSC is open: anyone on this network can control the box. Switch on a lock." (an Operator reads it on the health page; the locks themselves are the Owner's).

**The three locks in short, and for a live night.**

| Lock | Stops | Does not stop |
| --- | --- | --- |
| Only these devices may send | Every device whose address is not in the list: a guest's phone, a stray laptop | Someone on the same network who forges a listed address |
| A sender must be a paired device | Every address from which no paired Owner (or Operator) device opened the panel lately | Someone on the same network who forges that address; another device behind the same address |
| A key in the address | Anyone who does not know the key, forged address or not | Anyone who can listen on the same Wi-Fi: the key travels in the clear in every message and can be read and used again |

None of this is encryption. For a live night: **paired devices plus a key**, so a sender must come from the tablet's address and know the secret. On a shared venue network that is still not enough against someone who listens: there, the box and the tablet belong on a network of their own (the box's own hotspot, or a small router you bring).

Each lock is one switch. They can be on together; a packet must then pass every one that is on, and the old names without `/pvj` (`/stopall`, `/startmaster05` and so on) follow the same rules.

**What "secure" means here, and what it does not.** OSC is plain UDP. It cannot be encrypted by this box, anyone who can listen on the network can read every message, and a device on the same network can send a packet that claims to come from another device's address. So these locks do three honest things: far fewer devices may send, a sender must know a secret, and you can see who sent. They keep out devices that are merely on the network (a guest's phone, a laptop with a stray OSC program, a neighbour's lighting desk). They do not keep out someone on the network who captures traffic and forges packets on purpose. What such a person could do is limited to what OSC can do at all: play, stop, mix, scenes. Shutdown, reset, updates and passwords are not reachable over OSC.

| Lock | What it does | What it does not protect against |
| --- | --- | --- |
| **Only these devices may send** | A list of up to 16 single addresses. While it is on, being on a private network is no longer enough: only a listed address is let in. | A device that forges a listed address. A listed address that the router later gives to another device (give the tablet a fixed address). |
| **A sender must be a paired device** | A packet is let in only from an address from which a paired panel device (the owner's devices, or the owner's and operators'; a guest never) made a panel request in the last hours (12 unless changed, 1 to 72). Open the panel on the tablet once, then TouchOSC on the same tablet may send. No secret sits in the layout. | A device that forges that address. Any other device behind the same address (see below). |
| **A key in the address** | Every message must start with `/k/<key>`: `/k/<key>/pvj/stop`. The box makes the key (80 random bits) and can make a new one; the old one stops at once. | Anyone who can listen on the network: the key is readable in every single packet, and a captured packet can be sent again as it is (a replay). |

**The list of devices.** You do not type an address. Send one message from the tablet; it appears under "Senders in the last ten minutes" with **Allow this one** beside it. Check that the address is the tablet's own (TouchOSC shows it in its connection settings) before you press it; a row from which no paired panel device has asked says "No panel device here, check before allowing": with the list on and empty, nobody may send, and the page says so. A listed address must be inside the outer wall (a private range, or a network you added); an address outside it is refused when you save, and the private ranges and your extra networks stay the outer wall even for a listed address. The receiver listens on IPv4 only, as before, so an IPv6 address in the list matches nothing today.

**A paired device.** The box remembers, in memory only, the address each paired device last asked the panel from. It is never written to the settings file or to an export, and it is gone after a restart of the panel (open the panel on the tablet again). Removing the device under Setup > People and codes stops its address at once, and so does a factory reset. Logging out on the device itself (About and power > Log out) does the same. Some plain consequences:
- **Several devices behind one address count together.** If the tablet reaches the box through a router that shares one address (NAT), or the box is on a phone's hotspot and sees the hotspot's own address, every device behind that address may send while one paired device there counts.
- **A changed address.** When the router gives the tablet a new address, open the panel on it again. The old address still counts until the hours are over, for whichever device holds it then; set fewer hours, or remove and pair the device again, if that matters.
- The panel request is HTTP over TCP, which is much harder to forge than a UDP packet, but the OSC packet that follows is still UDP: this lock ties OSC to an address, not to the device itself.
- Requests through the remote-support tunnel never count.
- **Open the panel by the box's IPv4 address.** The receiver listens on IPv4 only. A device that reached the panel over IPv6 is paired but can never be the sender of a packet; the page names it apart ("reached the panel over IPv6") instead of showing it as counting.

**The key.** Shown only to a full-access device at the studio, or to a remote support session with the Owner's role (D84: the session's log then says support saw the key, and the Remote support page tells you to make a new one afterwards), behind **Show the key**; it hides itself after 30 seconds and is never part of what the page asks for by itself. It is compared in constant time. What stops guessing is the length of the key (80 random bits): at the 200 messages a second one sender is allowed, trying them all takes longer than anyone has. There is on purpose no lock-out for wrong keys: a sender's address can be forged, so a limit per address would be a way to keep the real tablet out. Wrong keys are counted instead, and the senders list says "N wrong keys from this address". A key is never shown in either list or written to the journal, right or wrong (an address that holds it is shown with `<key>` in its place), and an address with the `/k/` prefix twice is refused. Showing the key or making a new one is limited like the owner PIN (10 times in 5 minutes per device) and leaves a line in the journal that names the device and never the key. If any message in a packet (a bundle) lacks the key or has a wrong one, the whole packet is refused. With the lock off, a `/k/<anything>` prefix is simply dropped, so a layout that has the key goes on working. An exported settings file never holds the key, and an import never takes one from a file: the box keeps its own, and makes one if the file switches the lock on and it has none.

**What you can see.** "Senders in the last ten minutes" (the address, how many messages, the last OSC address, let in or refused and why) and "Last messages" (the last 50). Both are kept in memory only, are bounded (64 senders, 50 messages), and only a full-access device gets them; an operator or a guest gets neither the lists nor the key. Of a refused packet only the sender's address, a count and the reason are kept: never its OSC address or its arguments, and never the key it tried. A flood of refused packets from many forged addresses can push the older lines out of "Last messages"; the tablet that works stays in the senders list.

**The existing limits stay**: the outer wall, 200 messages a second per sender, the packet size and parsing limits, no answer to a refused packet. Switching a lock, allowing a device or making a new key does not close the port, so nothing sent in that moment is lost; changing the port does reopen it.

**An older release and the first switch-on (D83).** Whether OSC "was ever on" is one more setting under `osc`, `ever_on`, written once when this version first starts on a box (true when OSC is on, or when anything about it was ever set: the port, an extra network, a lock, a key; false only for an untouched box) and true from the first switch-on; `paired_default` marks a lock the box put on. No schema change. Tried with the code of master before this change: it ignores both, keeps them through a save of its own OSC page, and holds the paired lock (that lock is its own), so going back does not open the box. One box cannot be told from a new one: OSC was on before this version, was switched off, and nothing else was ever changed. Its next switch-on brings the lock, with the note beside the switch. A settings file never carries the mark, an import never lowers it, and a file that has OSC on comes with its own locks as they are. A factory reset makes the box new again.

**An older release and these settings.** No settings schema change: the locks are extra settings under `osc` (`only_on`, `only`, `paired_on`, `paired_roles`, `paired_hours`, `key_on`), and the key is kept apart, in a section of its own (`osc_secret`) that no release exports or imports. Tried with the code of master before this change: it loads a settings file that has all of this, keeps it when its own OSC page saves and when a new PIN is made, and its export does not hold the key. But it does not enforce the locks: after going back to an older release every lock is silently off, although the file still says on, and its import drops the lock settings. Switch OSC off before going back if the locks matter. A key that a test build of this change kept under `osc` is moved to its own section the first time the receiver is set up.

## Setting up TouchOSC

Not tried with a real TouchOSC or on a box yet; the receiver was tested with packets built by the tests.

1. In TouchOSC's connections, OSC: host is the box's address (Setup > Network or the box's display shows it), send port **9876** (or the port on Setup > OSC), UDP. A receive port is needed only for "State sent back" (below); without that the box sends nothing.
2. On the box: Setup > OSC, switch it on.
3. Press a control in the layout. The tablet appears under "Senders in the last ten minutes". For the list lock, press **Allow this one**; for the paired-device lock, open the panel on the same tablet once.
4. For the key: press **Show the key**, and put `/k/<key>` at the front of every address the layout sends, so `/pvj/stop` becomes `/k/<key>/pvj/stop`. In TouchOSC an OSC message's address is built from parts: add a constant part with `/k/<key>` before the others in each control's message, or add it in one place with a script. After **Make a new key**, change it in the layout.

## State sent back

Off until the Owner switches it on (D82, 2026-10-11). With it on, the box tells the devices the Owner named what is really playing, so a layout's lights are right whoever acted: the panel, a MIDI controller, a second tablet. A fader you are moving is sent its own value back within a fifth of a second; if it jumps under the finger, a small script in the layout ignores received values while the control is touched (the box does not hold anything back). Not tried with a real TouchOSC, a real tablet or on a box yet; the sender was tested with packets read back by the tests.

**Why it is off by default.** UDP has no connection, and a device on the network can send a packet that claims to come from another address. A box that answers "whoever sent the last message" can be made to send packets at someone else. So this box never does that:

- **It sends only to the devices in the list on Setup > OSC, "Send state back"**: at most 4, each a name, an IPv4 address and a port, added by the Owner (at the studio, or since D84 by a remote support session with the Owner's role, which is written into that session's log). Not an Operator, not a controller, not a support session below the Owner's role, and never by a packet. "Use the device that is sending now" only fills the address into the form from the last sender that passed every lock; the Owner still gives the port and a name and presses Add.
- **A target must be another device on the box's own network**: a private or link-local IPv4 address on a subnet the box is directly connected to, not the box itself, not a broadcast or multicast address, not loopback, and a port from 1024 up. "The box's own network" means a subnet of one of its real ports: wired (`eth0`, `end0`, `enx...`) or Wi-Fi (`wlan0`, which is also its hotspot). The support tunnel, any other VPN port (`wg*`, `tun*`, `tap*`, `tailscale*`, `zt*`), and the ports a container or a bridge makes (`docker*`, `veth*`, `br-*`, `virbr*`) are never counted, and neither is loopback. This is checked when a device is added and again whenever something is sent, so a box that moved to another network sends nothing to the old address: its row then says "Not on this network now, nothing is sent to it", it stays in the list until you press Remove, and it does not stand in the way of adding a new one. Packets are sent with a time to live of 1: they cannot cross a router.
- **The locks of "Who may send" hold for a target too.** With "Only these devices may send" on, a target must be in that list; with "A sender must be a paired device" on, a paired device must have opened the panel from that address (removing the device stops its state at once). With neither on, the list of targets alone decides.
- **A hard cap, whatever comes in.** To one device at most 20 packets and 12,000 bytes in any second; to all of them together at most 60 packets and 36,000 bytes in any second; one packet is at most 1200 bytes. What does not fit waits for the next look and leaves as the newest value only; nothing queues up. A flood of requests is answered at most once in 2 seconds per device.
- **Nothing secret is in it**: no PIN, no code, no key, no device name or address, no network detail, no folder. A clip's file name and a shader's name are in it, and since OSC is not encrypted anyone on the network can read them.
- **The key (lock 3) is never in a packet the box sends**, on or off. See "Setting up TouchOSC to listen".
- **State is not a command.** Every address the box sends starts with `/pvj/state/`, which does nothing when it is sent to a box. So a target that is another box, or two boxes that name each other, drive nothing.
- **It only sends while OSC itself is on**, from a thread of its own with a socket that never waits: a tablet that left the network holds up nothing.

**When.** The box looks at its state 5 times a second and sends what changed since the last look, each address once with its newest value. `/pvj/state/alive` goes out every 2 seconds, changed or not. Everything goes out again every 30 seconds, and at once when a listed device asks: send `/pvj/state` (any argument or none; with the key lock on, `/k/<key>/pvj/state`) from the device itself. The request must pass every lock, and its sender's address must be in the list; from any other address it does nothing and is answered with nothing.

**Types.** Every number is a float32 (`f`) and every name a string (`s`), one argument per message: what a TouchOSC control binds directly. Several messages travel together as one OSC bundle (a full state is 132 messages in 5 packets, about 5,000 bytes, and is out within half a second; as single messages it would be 132 packets and break the cap). **Whether TouchOSC takes bundles is to be confirmed on the tablet**; it has not been tried. On and off are `1.0` and `0.0`. Each address has the tail and the range of its control address, so `/pvj/opacity` (0 to 100) is `/pvj/state/opacity` (0 to 100).

| Address | Argument | What it says |
| --- | --- | --- |
| `/pvj/state/alive` | f, always 1 | The heartbeat, every 2 seconds. No heartbeat for 6 seconds or more: the box is not answering (or this device is no longer a target) |
| `/pvj/state/playing` | f 0/1 | Something plays |
| `/pvj/state/pause` | f 0/1 | Paused. Freeze on the panel is pause, as for `/pvj/pause` |
| `/pvj/state/clip/name` | s | The playing clip's file name, without its folder; empty for a shader, a stream, a live input and when nothing plays |
| `/pvj/state/clip/number` | f | Its place in the playlist that plays, from 1; 0 when nothing plays |
| `/pvj/state/clip/count` | f | How many clips that playlist has |
| `/pvj/state/time` | f | Seconds into the clip, whole seconds |
| `/pvj/state/length` | f | The clip's length in seconds; 0 when it has none |
| `/pvj/state/progress` | f 0 to 1 | Time over length, in steps of 0.01: for a fader or a bar |
| `/pvj/state/fade` | s | `idle`, `out` (the picture is going down), `black` (it is down, from a fade out), `in` (it is coming up) |
| `/pvj/state/fade/on` | f 0/1 | 1 while the fade is anything but `idle`: the light of the one fade button (`/pvj/fade`) |
| `/pvj/state/blackout` | f 0/1 | Blackout is on |
| `/pvj/state/transition` | s | The transition's name, as `/pvj/transition` takes it |
| `/pvj/state/transition/duration` | f 0.1 to 10 | Its seconds |
| `/pvj/state/opacity` | f 0 to 100 | Percent |
| `/pvj/state/size` | f 1 to 200 | Percent |
| `/pvj/state/position/x`, `/pvj/state/position/y` | f -100 to 100 | The shift |
| `/pvj/state/rotate` | f 0, 90, 180, 270 | Degrees |
| `/pvj/state/flip/h`, `/pvj/state/flip/v` | f 0/1 | Mirrored |
| `/pvj/state/speed` | f 0.1 to 4 | Playback speed |
| `/pvj/state/volume` | f 0 to 130 | Percent |
| `/pvj/state/loop`, `/pvj/state/mute` | f 0/1 | Loop and mute of what plays |
| `/pvj/state/overlay` | f 0/1 | The overlay picture is on |
| `/pvj/state/shader` | s | The shader on the screen, as `/pvj/shader` takes it (`silk`); empty when none |
| `/pvj/state/shader/speed` | f 0 to 4 | As `/pvj/shader/speed` |
| `/pvj/state/shader/hue` | f -180 to 180 | Degrees |
| `/pvj/state/shader/brightness` | f 0 to 2 | 1 leaves it alone |
| `/pvj/state/shader/control/<n>` | f 0 to 1 | The n-th input (1 to 8) of the shader on screen, spread as `/pvj/shader/control/<n>` takes it; 0 for one that is not there |
| `/pvj/state/shader/control/<n>/name` | s | What the panel calls that control: the shader file's LABEL for the input, or its NAME when it has none; at most 40 characters; empty for one that is not there. For the label beside a knob |
| `/pvj/state/shader/control/<n>/kind` | s | What that control is: `float` (a number over a range: a fader or a knob), `bool` (a switch), `choice` (a list of named values, by position), `int` (a whole number over a range), `event` (a button that fires once; its value is always 0); empty for one that is not there, so a layout can dim it |
| `/pvj/state/shader/preset/<n>` | f 0/1 | The n-th preset (1 to 8) is the one on now |
| `/pvj/state/effect` | s | The effect that is on; empty when none |
| `/pvj/state/effect/on` | f 0/1 | An effect is on |
| `/pvj/state/effect/amount` | f 0 to 100 | Percent, as `/pvj/effect/amount` |
| `/pvj/state/effect/control/<n>` | f 0 to 1 | The n-th input (1 to 8) of the effect that is on, spread as `/pvj/effect/control/<n>` takes it; 0 for one that is not there |
| `/pvj/state/effect/control/<n>/name` | s | What the panel calls that control, as for a shader. An effect file's own input called "amount" is sent as "Amount (the filter's own)", as the panel says it, because `/pvj/effect/amount` is another thing |
| `/pvj/state/effect/control/<n>/kind` | s | As for a shader |
| `/pvj/state/vibes` | f 0/1 | Vibes runs |
| `/pvj/state/vibes/set` | s | The name of its set |
| `/pvj/state/vibes/dwell` | f 15 to 3600 | Seconds each shader stays |
| `/pvj/state/mapping/mode` | f 0/1 | Mapping mode is on |
| `/pvj/state/guests/open` | f 0/1 | Guest controls are open (1) or locked for a show (0) |
| `/pvj/state/pad/bank` | f | The bank (1 to 3) of the pad that plays; 0 when no pad does |
| `/pvj/state/pad/number` | f | That pad's number (1 to 12); 0 when no pad does |
| `/pvj/state/pad/<bank>/<pad>` | f 0/1 | That pad is the one playing: the light of the button that sends `/pvj/pad/<bank>/<pad>` |

The pad lights, the fade and Blackout come from the same picture of the box the MIDI controllers' lights are worked out from (`pvj/boxstate.py`), so a pad lit on a controller is lit in the layout. Not sent: the Room's scenes and projectors, the overlay's file.

**The eight controls and their names.** The count is the one `/pvj/shader/control/<n>` and a MIDI controller's knobs use: the file's numbers, switches, choices and events, in the file's order, the first eight. A colour and a point (x and y) have no knob, so they have no place among the eight and no name is sent for them; a number whose MIN is its MAX cannot move and is left out too. The names and kinds are sent when the shader or the effect changes and with every full state, not at every look, so a knob that moves sends its value alone. Like a clip's name, a control's name is read by anyone on the network.

## Sound values sent out (D96)

With sound reactivity on and the Owner's switch for it, the box sends `/nxlx/sound/...` (the level, dB, the envelope, the beat pulse, the tempo, six bands, and a message at each beat) to the state targets above and to the render nodes below, at most 20 times a second, under caps of their own. The table and the rule are in [SOUND.md](SOUND.md#osc-out-nxlxsound). The values are levels, never sound.

## Cues sent to a render node (D94)

The box also sends OSC to render nodes the Owner lists (a laptop running Max/Jitter or another tool), under the same rule
of where it may send as the state above, from a socket it opens for one datagram and never reads. Those cues are not
this receiver's addresses: they are what the person types, or the `/nxlx/` scheme of the Max profile. See
[RENDERNODES.md](RENDERNODES.md).

## Setting up TouchOSC to listen

Not tried with a real TouchOSC yet; this is written from how OSC and TouchOSC's connection settings work, and the first try on a tablet may correct it.

1. In TouchOSC's connections, OSC, the same connection that sends to the box: set a **receive port** (9000 is a usual choice; any port from 1024 up). The tablet needs a fixed address: give it one in the router, or the state stops when its address changes.
2. On the box: Setup > OSC, "Send state back". Send one message from the tablet, press **Use the device that is sending now**, check the address is the tablet's own (TouchOSC shows it in its connection settings), type the receive port and a name, press **Add**. Then switch "Send state back" on.
3. In the layout, give a control a message that **receives**: its address is the state address (`/pvj/state/opacity` for the opacity fader, `/pvj/state/pad/1/3` for the third pad of bank A, `/pvj/state/clip/name` for a label's text), with the message's Receive ticked and Send not. The message that sends stays as it is (`/pvj/opacity`). A fader's range is the same in both directions.
4. With the key lock on, only the sending message carries `/k/<key>`; the receiving one never does.
5. For "box not answering": a label or light bound to `/pvj/state/alive`, with a small script that dims it when nothing came for 6 seconds.
6. A layout opened late shows everything within 30 seconds by itself, or at once when it sends `/pvj/state` (a button, or a script when the layout opens).

## Stable addresses

Numbers use natural units, so set your controller's fader range to match (for example 0 to 100 for opacity). Pads and banks are **1-based** like TouchOSC's multipush grids.

Every address in this table, the ones added with D75 and `/pvj/mapping/...` among them, sits behind the three locks of "Who may send" whenever those are on (see "Behind the three locks" under the table).

| Address | Arguments | Effect |
| --- | --- | --- |
| `/pvj/pad/<bank>/<pad>` | value (press) | Play that pad. Bank 1 to 3, pad 1 to 12 |
| `/pvj/play/pad` | bank, pad (ints) | Same, with arguments |
| `/pvj/play/file` | name (string) | Play a file from the media folder |
| `/pvj/play/preset` | name (string) | Play a legacy preset such as `startlessonce05` |
| `/pvj/vibes` | value (press) | Start Vibes, the endless shader rotation (needs the Shaders and Vibes module, see [SHADERS.md](SHADERS.md)); `/pvj/stop` ends it |
| `/pvj/vibes/next` | value (press) | Go to the next shader now |
| `/pvj/vibes/previous` | value (press) | Go back to the shader before |
| `/pvj/vibes/set` | string | Start Vibes through the rotation set of that name (or id); `/pvj/vibes` uses the active set |
| `/pvj/stop` | value (press) | Stop the clip (the player stays running) |
| `/pvj/pause` | none, or 0/1 | No argument toggles; 1 pauses, 0 resumes |
| `/pvj/blackout` | none, or 0/1 | No argument toggles |
| `/pvj/fadeout` | none, or a button's 1 and 0 | Fade to black in 2 seconds. A 0 is a button's release and does nothing; exactly 1 is its press. Any other number is still read as the seconds, as before |
| `/pvj/fadeout/seconds`, `/pvj/fadein/seconds` | 0.1 to 30 | The same two fades over that many seconds (also exactly 1) |
| `/pvj/opacity` | 0 to 100 | Percent |
| `/pvj/size` | 1 to 200 | Percent |
| `/pvj/position`, `/pvj/position/x` | -100 to 100 | Horizontal shift (two names for the same thing) |
| `/pvj/position/y` | -100 to 100 | Vertical shift |
| `/pvj/flip/h`, `/pvj/flip/v` | 0/1 | Mirror left to right, top to bottom: set (an argument is required; the old `/fliph` and `/flipv` still switch over at each press) |
| `/pvj/fade` | value (press) | The one fade button: fades out in 2 seconds, and at the next press in. The box decides which from what the screen is doing, so it is right whoever started the fade; it also brings the picture up out of a Blackout. A button's release (0) does nothing |
| `/pvj/fadein` | none, or a button's 1 and 0 | Fade in from black in 2 seconds; also ends a blackout. Press and release as for `/pvj/fadeout` |
| `/pvj/clip/next`, `/pvj/clip/prev` | value (press) | The next or the previous clip of the playlist that is playing |
| `/pvj/transition` | name (string) | How one clip changes to the next: `cut`, `dip`, `crossfade`, `wipe-from-left`, `wipe-from-right`, `wipe-from-top`, `wipe-from-bottom`, `slide-left`, `slide-right`, `slide-up`, `slide-down` (the Mix screen's choice; it is saved) |
| `/pvj/transition/duration` | 0.1 to 10 | Seconds a transition takes (saved) |
| `/pvj/overlay` | none, or 0/1 | The overlay picture: no argument switches it over; refused while no picture is chosen |
| `/pvj/overlay/file` | name (string) | Choose the overlay picture: a PNG in the media folder, checked as the panel checks it |
| `/pvj/effect` | none, or 0/1 | The effect over the picture: 1 puts the one that was on last back (the first of the list if there was none), 0 takes it off, no argument is the one button (needs the Shaders and Vibes module, as every effect and shader address does) |
| `/pvj/effect/next`, `/pvj/effect/prev` | value (press) | The neighbour in the list of effects |
| `/pvj/effect/amount` | 0 to 100 | Percent of the effect in the picture |
| `/pvj/effect/control/<n>` | 0 to 1 | The n-th input (1 to 8) of the effect that is on, spread over its own range as a controller's knob does: a number over its MIN to MAX, a switch on from 0.5 up, a choice by position |
| `/pvj/shader` | name (string) | Show that shader, as the Shaders screen's Play does (`silk` or `silk.fs`). The box answers at once and the shader follows when the graphics chip has taken it; a refusal is shown on the Shaders screen |
| `/pvj/shader/next`, `/pvj/shader/prev` | value (press) | The neighbour in the active set (steps Vibes while it runs) |
| `/pvj/shader/speed` | 0 to 4 | Times the shader's own pace; 0 freezes it |
| `/pvj/shader/hue` | -180 to 180 | Degrees the colours are turned |
| `/pvj/shader/brightness` | 0 to 2 | 1 leaves it alone |
| `/pvj/shader/control/<n>` | 0 to 1 | The n-th input (1 to 8) of the shader on screen, as for an effect |
| `/pvj/shader/preset/<n>` | value (press) | The n-th preset (1 to 8) of the shader on screen |
| `/pvj/vibes/dwell` | 15 to 3600 | Seconds each shader of the rotation stays (saved) |
| `/pvj/mapping/mode` | none, or a button's 1 and 0 | Mapping mode ([MIDI.md](MIDI.md), "Layers"): exactly as the fade addresses read a button, 1 (the press) enters it and **a 0 is the button's release and does nothing**; no argument switches over. Until 2026-10-10 a 0 left the mode, so a momentary button entered it and left it again as the finger came up. **Every `/pvj/mapping/` address does nothing unless a full-access device switched "Controllers may adjust the mapping" on (the Mapping card), and all but the `mode` addresses only in the mode. Like every address in this table they are behind the three locks of "Who may send" when those are on** |
| `/pvj/mapping/mode/off`, `/pvj/mapping/mode/on` | value (press) | Leave the mode, enter it, said outright (off twice is off). **This is how the mode is switched off over OSC**: a toggle button in a layout sends 1 to `/pvj/mapping/mode` and, for its other state, 1 to `/pvj/mapping/mode/off` |
| `/pvj/mapping/mode/toggle` | value (press) | One momentary button for both: each press switches over, each release does nothing |
| `/pvj/mapping/surface/next`, `/prev`; `/pvj/mapping/corner/next`, `/prev` | value (press) | Choose the surface and the corner, round and round; the box's display marks them |
| `/pvj/mapping/left`, `/right`, `/up`, `/down` | value (press) | Nudge the chosen corner by the step |
| `/pvj/mapping/nudge` | two whole numbers | So many steps to the right and so many down (negative: left, up), -127 to 127 each; one message moves a corner 200 pixels at most. Every step is taken at once; a flood of them is drawn at most 25 times a second and saved at most twice a second, with a last picture and a last save when it stops ([MAPPER.md](MAPPER.md)) |
| `/pvj/mapping/step` | 1, 10 or 50 | Pixels a step |
| `/pvj/mapping/undo` | value (press) | Take the last run of nudges back: the nudges of one corner that followed each other within a second (a fader's sweep, an arrow tapped quickly) are one step. One step back, no further |
| `/pvj/speed` | 0.1 to 4 | Playback speed |
| `/pvj/volume` | 0 to 130 | Percent |
| `/pvj/seek` | -3600 to 3600 | Seconds, relative |
| `/pvj/rotate` | 0, 90, 180, 270 | Degrees |
| `/pvj/loop`, `/pvj/mute` | 0/1 | Set (an argument is required) |
| `/pvj/mix/reset` | value (press) | Reset opacity, size, position, speed, rotation |
| `/pvj/scene/<n>` | value (press) | Apply the n-th scene of the Room module (1 is the first; see [ROOM.md](ROOM.md)) |
| `/pvj/scene` | name (string) or number (2 or 2.0) | The same, by the scene's name or its place in the list. A scene that plays something also takes a blackout off |
| `/pvj/group/<n>/on`, `/pvj/group/<n>/off` | value (press) | Switch the projectors of the n-th group on or off; `all` in place of the number is every projector |
| `/pvj/group/<n>/mute`, `/mute_picture`, `/mute_sound` | 0/1 | Mute (1) or unmute (0) picture and sound, the picture, or the sound of that group (an argument is required) |
| `/pvj/group/<n>/input` | input code (`31`, as a string or a number) | Switch that group's projectors to that input; the codes are listed in Setup > Projectors |

**Real units, and no centre that sits.** A MIDI knob sends 0 to 127, so the box gives its zoom and position a middle that holds and a curve ([MIDI.md](MIDI.md), "How a level follows a knob"). OSC sends the real value, so `/pvj/size 100` is 100 percent and `/pvj/position/y 0.5` is half a step off centre: nothing is rounded to a centre and nothing waits for pickup. Set the fader's range in the sender.

**Freeze** on the panel is pause: `/pvj/pause`. There is no `/pvj/freeze`.

**Behind the three locks.** The locks of "Who may send" (the list of devices, the paired device, the key in the address) are about who sends, never about which address: a packet passes every lock that is on before anything in it is read, so the addresses added with D75 (the fade button, position, the shader's and the effect's steps and controls, the overlay, the transition) and all of `/pvj/mapping/` are behind them exactly as `/pvj/stop` is. With the key on they are sent as `/k/<key>/pvj/fade` and `/k/<key>/pvj/mapping/nudge`: the prefix is taken off before the address is looked at. No lock lets a sender through for some addresses only; the list of single addresses is a list of devices (their network addresses), not of OSC addresses. Mapping mode still needs the owner's own switch as well.

**One table with MIDI.** The addresses above that are a press or a level (the fade, the clip's and the shader's and the effect's steps, the presets, position, the shader's speed, hue and brightness, the effect's amount) make their calls from the same table as a MIDI controller's actions (`pvj/actions.py`), so what a controller can do and what OSC can do do not drift apart.

**Buttons fire on press only.** TouchOSC sends `1.0` when a button is pressed and `0.0` when it is released; a command marked "press" acts on a non-zero value (or no argument) and ignores the release, so a pad does not fire twice.

## Names kept from the old receiver

`/stopall`, `/stopvideo` (both stop the clip), `/pause`, `/fastforward` (seek forward 10 s), `/volumeup`, `/volumedown` (10 percent steps), `/rotate0`, `/rotate90`, `/rotate180`, `/rotate270`, `/beameron` and `/beameroff` (every projector added under Setup > Projectors on or off, see [PROJECTORS.md](PROJECTORS.md)), and every start preset: `/startmaster`, `/startmaster05`, `/startlessonce03`, `/startlesseronce07`, `/startseamless02`, ... (the same names and files as the old scripts; see the preset table in `pvj/presets.py`).

Also from the old receiver: `/startmasteronce01` to `/startmasteronce99` (play the clip numbered so, once; these failed silently before), `/testscreen` and `/testscreenoff` (the test pattern), `/testtone`, `/testtoneleft`, `/testtoneright`, `/overlay` and `/stopoverlay` (the overlay picture chosen on the Mix screen), `/image` (the slideshow of the media folder) and `/stopimage`, `/fliph` and `/flipv` (each press switches the mirror over, like the old buttons).

Not carried over:
- on purpose, because they change settings or the system, which needs a full-access device: `/audiohdmiout`, `/audiojack`, `/audiousb`, `/audioboth` (Setup > Sound), `/startslave` (Setup > Boxes in step), `/reboot`, `/shutdown`, `/rebootall`, `/shutdownall`, `/customfunction1` and `/customfunction2` (they ran shell scripts);
- not built yet: `/screenon` and `/screenoff` (display sleep), the clock (`/clockdisplay` and colours), `/imageusb` and `/imagemanual`, the audio player (`/startaudio...`, `/stopaudio`), the PDF presenter (`/startpdf...`), the camera and its effects, soft edge, `/getcontent`;
- replaced: PiWall (`/piwallmaster`, `/piwallloop`) by Sync and video wall; Syphon (`/tcpsserver`) and `/ndisend`, `/ndireceiver` wait for NDI.

## Tested

The parser is tested on every type and padding length, on malformed packets, bundle nesting bombs and 4000 random or corrupted packets; the receiver on the allow-list, rate limit, a real UDP socket on loopback (and that it never replies), and through the settings API. The three locks: each alone and all together, the list replacing the private ranges, the paired-device rule against removing a device, forgetting, expiry and two devices behind one address, the key (missing, wrong, right, an old key after a new one, the constant-time comparison, a flood of wrong keys that does not hold the right one up), that nothing of a refused packet is applied or kept, what an operator and a guest are sent, a real UDP socket on loopback with all three on, and in a browser the page at 320 px wide with IPv6 addresses. The addresses added with D75 (2026-10-10) are tested through the parser, through the API with a fake player and a real shader engine with a fake graphics chip, and over a real UDP socket on loopback (`tests/test_osc_reach.py`); each of the three locks, and all three together, against one of those addresses and against mapping mode's, with the key's prefix in front (`BehindTheLocksTest` there). Not tested with a real TouchOSC layout or hardware controller, and never on a box.

State sent back (D82, `tests/test_osc_feedback.py`): off by default and nothing sent under any input while off; what may be a target (public, broadcast, multicast, loopback, the box's own, IPv6, a name, the ports) when it is added and again at every send; only the Owner at the studio changes it, and every other identity in the roles table is refused; every packet is a documented address with a float or a string, and the table above is compared with what the code sends; the caps under a flood of changes and requests on a clock the test owns; a forged sender that is no target gets nothing and causes nothing; what the box sends does nothing when it is sent back to it; a dead target, a socket that fails and a look at the box that hangs stall neither the receiver nor a tick; no PIN, key, code, token, device name, address or folder in any packet of a full scenario; the code of master before this change keeps the settings and sends nothing. Not tried with a real TouchOSC, a real tablet or on a box.
