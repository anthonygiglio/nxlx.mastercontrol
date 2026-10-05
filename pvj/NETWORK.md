# Network settings (wired and Wi-Fi)

Change the box's wired and Wi-Fi network from the panel (System, Network). It is the **Network** module: beta, **off by default**, needs NetworkManager (Raspberry Pi OS Bookworm and Trixie, and most desktop Linux, have it). Switch it on on that page.

A gig usually runs on a **direct Ethernet cable** or a small private switch, so wired comes first. Wi-Fi is for a room where staff use a phone or a wall tablet, or where a cable cannot reach.

## Wired modes

| Mode | What it does | When to use it |
| --- | --- | --- |
| Automatic (DHCP) | Takes an address from a router | Venue or studio network with a router |
| Fixed address | An address, prefix length, optional gateway and DNS that you choose | Gear that expects the box at a known address |
| Direct cable | Link-local (169.254.x.x), no router needed | Laptop plugged straight into the box |
| Serve addresses | The box hands out addresses to whatever is plugged in (NetworkManager "shared", default 10.42.0.1/24) | A laptop or another box on a cable, with no router; only private ranges are accepted |

## Wi-Fi modes

Pick the Wi-Fi port (`wlan0` on a Pi) in the port list; it has its own modes.

| Mode | What it does | When to use it |
| --- | --- | --- |
| Join a network | Joins a Wi-Fi network and takes an address from its router | A venue or studio Wi-Fi |
| Join, fixed address | Joins a Wi-Fi network with an address, prefix, optional gateway and DNS that you choose | Gear that expects the box at a known address |
| Own hotspot | The box makes its own Wi-Fi network (WPA2, 2.4 or 5 GHz) and hands out addresses to whoever joins it (default 10.43.0.1/24) | A private NXLX room network with no router, or setting a box up from a phone |
| Wi-Fi off | Disconnects; when you confirm, the Wi-Fi radio is switched off and stays off after a restart (on a box with two Wi-Fi ports, both) | A show on a cable only, or a crowded room |

**Find networks** lists the networks in range, strongest first; tap one to fill in its name and security. Hidden networks are typed in by hand (tick "Hidden network"). Security: a WPA2 password (this also joins WPA2/WPA3 mixed networks), WPA3 only, or open. Company networks that need a user name (802.1X, "Enterprise") and old WEP networks are not supported and are shown greyed out. A password is 8 to 63 plain characters (letters, digits, punctuation, spaces) or a 64-digit hex key.

Only one Wi-Fi network is kept as the box's own choice (`pvj-wlan0`); joining another replaces it. A network set up before (for example by Raspberry Pi Imager) stays saved, at a lower priority. Changing only the address on the same Wi-Fi asks for the password again, because the panel never reads it back.

**A Pi needs its Wi-Fi country set** before Wi-Fi works at all (Raspberry Pi Imager asks for it, or `sudo raspi-config`, Localisation, WLAN Country). Until then the panel says "blocked". 5 GHz hotspots depend on the country too.

If the panel reaches the box over Wi-Fi, joining another network or switching Wi-Fi off cuts that connection. For a hotspot, join the box's network with the same phone and open `http://10.43.0.1` to confirm. For a new network, join it yourself and find the box there. If you cannot, the box goes back to its previous Wi-Fi by itself when the timer runs out (120 seconds is offered first for Wi-Fi).

## Why a change is never final straight away

Changing the network of the box you are controlling over that network can lock you out. So every change is **pending** until you press **Confirm**:

- The new network is built as a separate **candidate** profile (`pvj-<port>-try`) and brought up, and a countdown starts once it is up (30, 60, 120 or 300 seconds; default 60). Your confirmed setup is **never edited in place**.
- If you press Confirm in time, the candidate replaces the old profile.
- If the countdown ends, if a command fails, if the helper restarts, or if the box loses power or reboots before you confirm, **the previous network comes back by itself**: the candidate is not set to connect at boot, and the helper undoes any leftover on start-up.
- An undo that fails is retried every few seconds until it works (the panel says "Restoring the previous network") and is never reported as done before it is; after 20 tries it goes on once a minute and is never dropped, no new change is accepted until it has worked, and the saved state lets a restart take it over. A slow or failing `nmcli` during an undo counts as a failed try.
- Confirm gives the old profile a temporary name, gives the new one its name, and only then deletes the old one. If Confirm fails part way, the old profile gets its name back and the change can still be undone; if only the last delete fails, the new network is kept and the old profile is deleted at the next change.
- If the page stops responding after Apply (the address changed), open the box at its new address and press Confirm there. For a fixed or served address the panel tells you the address.

Only one change can be waiting at a time. Under Advanced, "Show the commands" shows exactly what would run, without changing anything, and "Go back by itself after" sets how long you have to confirm. The button that makes the change is "Try this setting"; the choice of port is shown only when the box has more than one.

## How it works, and what it will not do

The panel is unprivileged and cannot change the network itself. It asks `pvj-netd`, a small root service, over a socket in the helper's own folder `/run/pvj/netd` (root, group `pvj`, mode 0750: the group can reach the socket, only root can replace it), and the helper answers only root and the panel's account. The helper checks every request again, runs only fixed `nmcli` commands built from validated values (argument lists, never a shell), and refuses everything else. What it saves about a pending change (an interface name and a connection UUID, nothing else) lives in a root-only directory (`/var/lib/pvj-netd`), never under `/run`, which is empty after a restart; it is checked strictly before use.

**The Wi-Fi password never appears on a command line**, because every account on a Linux box can read other programs' command lines. `pvj-netd` writes the new Wi-Fi profile as a NetworkManager keyfile that only root can read (in `/etc/NetworkManager/system-connections`, a new file whose name starts with `pvj-wlan0-try-`, created new, never through a link; NetworkManager may keep that file name after Confirm renames the profile), has NetworkManager load it, and uses `nmcli` only for the addressing and to switch it on. The password is not kept in memory after that, and is never in a reply, the log, the saved state or the preview. The network name is written as a list of bytes, so no name can add lines or sections to the file. If a change had to switch the Wi-Fi radio on, undoing it switches it off again, also after a restart. Names of networks in range come from strangers; they are cleaned (no control or invisible characters, at most 32 bytes) and only ever shown as text. Which network is in use is taken from what the box is joined to, never from the scan list. Anyone nearby can make a network with the same name as yours, so check you are joining the right one. The hotspot switches off protected management frames (PMF), because with them some phones cannot join a Pi's hotspot. The helper's sandbox may write only to `/run/pvj`, its own state folder and NetworkManager's profile folder.

Refused on purpose: virtual interfaces (Docker, bridges, tunnels), addresses that cannot be used on a network (0.x.x.x, loopback, multicast, broadcast, link-local as a fixed address), a gateway outside the address's network, prefixes outside /8 to /30, DNS servers that are duplicated, link-local or the box's own address, a range that overlaps another port's network, and serving addresses outside 10.x, 172.16-31.x and 192.168.x or with a pool bigger than a /16.

## Not built yet

Company Wi-Fi (802.1X), WEP, several saved Wi-Fi networks to choose between, setting the Wi-Fi country from the panel, a hotspot without a password, several addresses per port, VLANs, bonding, static routes and choosing the DHCP range when serving addresses. (The box does answer to `<name>.local`, for example `nxlx-mastercontrol.local`: that comes from the system image, not this module; checked from a Mac on the test network.)

## Tested, and not

Tested with a fake NetworkManager that keeps real state (and after an independent security review of this feature, whose findings each have a test): the exact commands for each mode, apply, confirm, timeout revert, revert after a failed command, revert after a daemon restart, a power cut and a restart while a change is pending, an undo that fails and is retried, a slow activation, planted or damaged saved state, refusal of hostile or malformed requests, the socket protocol, and the panel end to end in a browser. The Wi-Fi part is tested the same way (keyfile contents and permissions, planted links, an unsafe folder, a keyfile written but never loaded, a failed connection, the radio switched back off after a revert and after a restart, the hotspot, Wi-Fi off, scanning, hostile network names, and the password absent from every command, reply, log and state file). **Never run against a real NetworkManager, a real cable, a real Wi-Fi network, or a real Pi.** For Wi-Fi in particular: that NetworkManager loads the keyfile as written (byte-list SSID, escaped password, `key-mgmt=sae` for WPA3), how long joining takes, whether the Pi's Wi-Fi chip can run a 5 GHz hotspot and WPA3, and what happens to the keyfile's name when the profile is renamed on confirm. The exact `nmcli` property names and how NetworkManager reacts to switching a live connection (especially `shared` and `link-local`) need a test on a real device; try it first on a box you can reach another way, and expect to adjust.
