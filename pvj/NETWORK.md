# Wired network settings

Change the box's wired network from the panel (System, Network). It is the **Network** module: beta, **off by default**, needs NetworkManager (Raspberry Pi OS Bookworm and Trixie, and most desktop Linux, have it). Switch it on on that page.

A gig usually runs on a **direct Ethernet cable** or a small private switch, so wired comes first; Wi-Fi is not configurable here yet.

## Modes

| Mode | What it does | When to use it |
| --- | --- | --- |
| Automatic (DHCP) | Takes an address from a router | Venue or studio network with a router |
| Fixed address | An address, prefix length, optional gateway and DNS that you choose | Gear that expects the box at a known address |
| Direct cable | Link-local (169.254.x.x), no router needed | Laptop plugged straight into the box |
| Serve addresses | The box hands out addresses to whatever is plugged in (NetworkManager "shared", default 10.42.0.1/24) | A laptop or another box on a cable, with no router; only private ranges are accepted |

## Why a change is never final straight away

Changing the network of the box you are controlling over that network can lock you out. So every change is **pending** until you press **Confirm**:

- The new network is built as a separate **candidate** profile (`pvj-<port>-try`) and brought up, and a countdown starts once it is up (30, 60, 120 or 300 seconds; default 60). Your confirmed setup is **never edited in place**.
- If you press Confirm in time, the candidate replaces the old profile.
- If the countdown ends, if a command fails, if the helper restarts, or if the box loses power or reboots before you confirm, **the previous network comes back by itself**: the candidate is not set to connect at boot, and the helper undoes any leftover on start-up.
- An undo that fails is retried every few seconds until it works (the panel says "Restoring the previous network") and is never reported as done before it is; if it still fails after many tries the saved state is kept, so the next start tries again.
- If the page stops responding after Apply (the address changed), open the box at its new address and press Confirm there. For a fixed or served address the panel tells you the address.

Only one change can be waiting at a time. "Preview commands" shows exactly what would run, without changing anything.

## How it works, and what it will not do

The panel is unprivileged and cannot change the network itself. It asks `pvj-netd`, a small root service, over a socket in `/run/pvj` that only group `pvj` can use. The helper checks every request again, runs only fixed `nmcli` commands built from validated values (argument lists, never a shell), and refuses everything else. What it saves about a pending change (an interface name and a connection UUID, nothing else) lives in a root-only directory (`/var/lib/pvj-netd`), never in `/run/pvj`, which every member of group `pvj` can write; it is checked strictly before use.

Refused on purpose: wireless interfaces, virtual ones (Docker, bridges, tunnels), addresses that cannot be used on a network (0.x.x.x, loopback, multicast, broadcast, link-local as a fixed address), a gateway outside the address's network, prefixes outside /8 to /30, DNS servers that are duplicated, link-local or the box's own address, a range that overlaps another port's network, and serving addresses outside 10.x, 172.16-31.x and 192.168.x or with a pool bigger than a /16.

## Not built yet

Wi-Fi setup and the box's own hotspot, several addresses per port, VLANs, bonding, static routes and choosing the DHCP range when serving addresses. (The box does answer to `<name>.local`, for example `nxlx-mastercontrol.local`: that comes from the system image, not this module; checked from a Mac on the test network.)

## Tested, and not

Tested with a fake NetworkManager that keeps real state (and after an independent security review of this feature, whose findings each have a test): the exact commands for each mode, apply, confirm, timeout revert, revert after a failed command, revert after a daemon restart, a power cut and a restart while a change is pending, an undo that fails and is retried, a slow activation, planted or damaged saved state, refusal of hostile or malformed requests, the socket protocol, and the panel end to end in a browser. **Never run against a real NetworkManager, a real cable, or a real Pi.** The exact `nmcli` property names and how NetworkManager reacts to switching a live connection (especially `shared` and `link-local`) need a test on a real device; try it first on a box you can reach another way, and expect to adjust.
