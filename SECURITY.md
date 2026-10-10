# Security

The legacy v3 code has known critical issues: unauthenticated shell command injection, no authentication on the control panel, and state-changing actions over GET. Until the security hotfix ships, run it only on a private, isolated network, never on the internet or a shared Wi-Fi.

Please report security problems by opening a private security advisory on this repository.

<<<<<<< HEAD
## OSC (the new player)

OSC is receive-only UDP, off until switched on, and only private networks may send. It is not encrypted and cannot be: anyone who can listen on the network reads every message, and a device on the same network can forge another device's address. Three optional locks on System > OSC (a list of devices, a paired device, a key in the address; D78) reduce who may send and show who sent. They stop devices that are merely on the network, not someone who captures traffic or forges packets: the key is readable in a single packet and a captured packet can be replayed. Shutdown, reset, updates and passwords are not reachable over OSC at all. The details and the limits of each lock are in [pvj/OSC.md](pvj/OSC.md).

=======
>>>>>>> origin/https-own-ca
## The threat model of the current code (D79, 2026-10-10)

The box is a staff room controller and a VJ tool on a private network. It is not built to face the internet.

- **Someone on the same network who listens.** Over plain `http://` the PIN at pairing, the session cookies and everything the panel shows travel in the clear. With the owner's own root installed on his devices and the switch "Owner access only over the secure connection" on (System > Secure connection, `docs/HTTPS.md`), the PIN and the owner's cookie never cross the network in the clear; guests' codes and cookies still do, over `http://`, and give what they gave before (view, or live for a presenter).
- **Someone who answers for the box's name.** Over `https://` a device with the root refuses an impostor, which has no certificate from that root. Over `http://` a fake pairing page can still collect a PIN typed into it; with the switch on the real box refuses that PIN over `http://`, so it is useful to the attacker only over `https://`, where the throttle on guesses applies as before. The PIN is four digits and rotates at every start.
- **Someone who steals a box or its SD card** gets the box's key and certificate and can stand in for that one box, by its name, to devices with the root, until the certificate runs out (there is no revocation; `docs/HTTPS.md` says what to do). The root's key is never on a box, so no other box or name can be signed with what the card holds.
- **Someone who steals the root's key file** needs its passphrase; with it they can sign for `.local` names and private addresses (the root's name constraints, where the device enforces them), not for sites on the internet. The cure is a new root, every box signed again, and the old root removed from every device.
- **A guest with a code** gets view or live access over `http://`, cannot reach the certificate routes, and cannot read the root from the box.
- **Not covered:** a device of the owner's that is itself compromised; anybody who can run commands on the box; a four-digit PIN guessed over `https://` (the same throttle); the clear `http://` side, which stays for guests by design (no HSTS, no redirect).

Nothing of the secure connection has run on a box or met a real phone as of 2026-10-10.
<<<<<<< HEAD
=======

## OSC (the new player)

OSC is receive-only UDP, off until switched on, and only private networks may send. It is not encrypted and cannot be: anyone who can listen on the network reads every message, and a device on the same network can forge another device's address. Three optional locks on System > OSC (a list of devices, a paired device, a key in the address; D78) reduce who may send and show who sent. They stop devices that are merely on the network, not someone who captures traffic or forges packets: the key is readable in a single packet and a captured packet can be replayed. Shutdown, reset, updates and passwords are not reachable over OSC at all. The details and the limits of each lock are in [pvj/OSC.md](pvj/OSC.md).
>>>>>>> origin/https-own-ca
