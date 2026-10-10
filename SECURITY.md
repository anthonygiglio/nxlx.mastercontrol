# Security

The legacy v3 code has known critical issues: unauthenticated shell command injection, no authentication on the control panel, and state-changing actions over GET. Until the security hotfix ships, run it only on a private, isolated network, never on the internet or a shared Wi-Fi.

Please report security problems by opening a private security advisory on this repository.

## OSC (the new player)

OSC is receive-only UDP, off until switched on, and only private networks may send. It is not encrypted and cannot be: anyone who can listen on the network reads every message, and a device on the same network can forge another device's address. Three optional locks on System > OSC (a list of devices, a paired device, a key in the address; D78) reduce who may send and show who sent. They stop devices that are merely on the network, not someone who captures traffic or forges packets: the key is readable in a single packet and a captured packet can be replayed. Shutdown, reset, updates and passwords are not reachable over OSC at all. The details and the limits of each lock are in [pvj/OSC.md](pvj/OSC.md).

## Owner, Operator, Guest (the new player, D80)

Three levels: Owner (`full`), Operator (`live`), Guest (`view`). The whole policy is D80 in `project-log/DECISIONS.md`, with one row for every route; the code is `pvj/policy.py` and the one gate every request passes, `Api.gate`. What to know when judging the risk:

- **Guest controls are open by default**, by the owner's choice. A guest link or a guest code is therefore no longer "watch only": while the lock is open its holder can change what the room shows and switch projectors on and, with a second request, off. One box-wide lock (an Operator or the Owner) makes every guest read-only on the next request. Guests are limited (10 requests in 10 seconds per device, 30 for the box, refused ones too; play, blackout and a scene once in 2 seconds for all guests together; one switch-off per device and two for the box in five minutes), every guest action is logged with the device's name, and no guest request saves settings. A guest may apply every room scene but one that plays a stream, so **what a scene does is what a guest can do**: a scene an Operator made that mutes a wall can be applied by a guest who could not mute directly.
- **An Operator is trusted with the room, not with the box.** He can delete every clip, change the schedule and what plays at power-up, and remove guests. He cannot reach the network, updates, the PIN, the pairing of Operators and Owners, remote support, OSC, DMX and MIDI settings, the settings file, streams (the box would connect outside), projector addresses and passwords, or the module switches. An Operator link or code is made by the Owner only.
- **Callers with no login do not rise with the Operator.** MIDI, OSC, DMX, room scenes and the schedule, a remote support session below `full`, and a device paired as a presenter from a controller before D80 are held to a list written out by hand of what a presenter could reach before (`policy.LEGACY_LIVE`); a test refuses each of them every other route. A code drawn from a MIDI controller pairs a Guest, not an Operator.
- The lock is never changed through the remote support tunnel. **Not covered:** the lock is not a secret and not a per-guest setting; a guest request already past the gate when the lock is set still runs (one request); the confirm for a switch-off is bound to the device and the request, not to what a scene holds at that moment (an Operator editing the scene between a guest's two taps changes what the second tap does). **Nothing of this has run on a real box.**

