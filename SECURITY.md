# Security

The legacy v3 code has known critical issues: unauthenticated shell command injection, no authentication on the control panel, and state-changing actions over GET. Until the security hotfix ships, run it only on a private, isolated network, never on the internet or a shared Wi-Fi.

Please report security problems by opening a private security advisory on this repository.

## OSC (the new player)

OSC is receive-only UDP, off until switched on, and only private networks may send. It is not encrypted and cannot be: anyone who can listen on the network reads every message, and a device on the same network can forge another device's address. Three optional locks on System > OSC (a list of devices, a paired device, a key in the address; D78) reduce who may send and show who sent. They stop devices that are merely on the network, not someone who captures traffic or forges packets: the key is readable in a single packet and a captured packet can be replayed. Shutdown, reset, updates and passwords are not reachable over OSC at all. The details and the limits of each lock are in [pvj/OSC.md](pvj/OSC.md).
