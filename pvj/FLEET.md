<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Nodes that find each other

Several nodes on one network can see each other. Each one says it is there, and each one can list the others. This is only discovery: every node still has its own pairing, its own PIN and its own roles (D7, D80), and a node in the list is opened and paired like any other. The decision and its reasons are D127.

## What a node announces

By mDNS (the same multicast DNS that makes `node-01.local` work), a service called `_nxlx._tcp` on the node's HTTPS port (the plain panel port, 80, until a certificate is loaded), with these text records:

| Record | Meaning |
|---|---|
| `id` | A random identifier, 32 hex digits, made the first time the node starts and kept in `/var/lib/pvj/node-id`. It stays through restarts, renames and updates. It is public and identifies the node; it is not a password. |
| `name` | The node's host name, as the pin screen and Health show it. |
| `tier` | `lean` (Pi 3 class), `standard` (Pi 4), `full` (Pi 5, PC) or `unknown`. |
| `version` | The installed release. |
| `https` | `1` when the port is the secure connection. |

Nothing else goes out: no address list, no settings, no device names, no media names. The announcement is one file, `/etc/avahi/services/nxlx-mastercontrol.service`, written by the system helper (`pvj-sysd`) and served by `avahi-daemon`, which Raspberry Pi OS already runs. It is rewritten when the name, version, port, secure connection or the switch changes, and otherwise only once a minute, to notice that avahi stopped or that the file was deleted.

Without `avahi-daemon` the node works as before, announces nothing, and says why: `GET /api/nodes` reports `discovery.state` as `no-avahi` (not installed) or `avahi-stopped` (installed, not running), the node's log has a line, and the event log has one. The node still lists other nodes. It looks again every minute, so installing avahi later is enough. This feature never installs a package.

## Listing the other nodes

`GET /api/nodes` (Operator and Owner; a Guest has no access, and neither has a remote support session: network things stay in the room) answers:

```
{"self": {"id", "name", "tier", "version"},
 "discovery": {"on", "state", "detail"},
 "nodes": [{"id", "name", "address", "port", "tier", "version", "https", "seen_seconds_ago"}, ...],
 "scanning": false, "error": null}
```

The node does this by sending one multicast DNS question for `_nxlx._tcp.local` from an ordinary UDP port, on each of its networks, and reading the answers for about a second. It does not need `avahi-browse` or any package, and it does not take port 5353 from avahi. The answer is kept for 10 seconds; a request never waits for a scan except the very first one (at most 2.5 seconds). A node that one scan missed stays in the list for 35 seconds. The node's own entry is left out. When nobody asks, nothing is sent.

What comes back is what strangers on the network say. Names, versions and tiers are checked and cut, an answer from a public address is ignored, and the list is never a credential. Two rows with the same `id` at different addresses mean a card was cloned after its first start; delete `/var/lib/pvj/node-id` on one and restart it.

## Switching it off

The Owner can switch discovery off: `POST /api/nodes/discovery {"on": false}` (a switch in Setup comes with the node switcher). Off removes the service file at once and stops the questions: the node then neither announces nor looks, and `GET /api/nodes` returns an empty list with `discovery.state` `off`. The choice is kept across restarts and is this node's own: a settings import does not carry it. It is one switch for both directions on purpose: a venue whose network team objects to the announcement objects to the questions too.

By hand, without the panel: `sudo rm /etc/avahi/services/nxlx-mastercontrol.service` stops the announcement until the panel next rewrites it (within a minute at most while discovery is on); to stop avahi altogether, `sudo systemctl disable --now avahi-daemon` (this also stops `node-01.local` working).

For a venue's network team: the node sends and receives multicast DNS on UDP port 5353 (group 224.0.0.251) like any Mac or printer, and receives unicast UDP answers to the port it sent the question from. A network that blocks multicast between devices, or Wi-Fi with client isolation, shows an empty list; wired nodes on one switch work.

## What changes on the node, and what is the owner's to apply

- `pvj-web` needs no change: IPv4 UDP is already allowed and its unit has no address filter.
- `pvj-sysd.service` gains `-/etc/avahi/services` in `ReadWritePaths`, so the helper can write the one file. That is a change to what a root helper may write; the installer applies it at the next install or update, and no code path applies it by itself. Until it is applied the announcement fails with `error` and the node otherwise works.
- No firewall rule is added or needed on the node.

## Tested, and not

Tested with hand-built packets: several nodes in one answer, a node whose records come in a second answer, a malformed or hostile packet, a pointer loop, our own node, a goodbye, a public sender, nothing heard, a failing network, avahi missing or stopped, the helper missing, the file the helper writes (contents, mode, no change when nothing changed, nothing written for a bad field), the node id, and who may ask. Not tested without hardware: that avahi serves the file and answers the question as expected, and two real nodes seeing each other (rows in `tools/DEVICE-TESTING.md`).
