<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Remote support

Help a studio from anywhere, without opening anything on their router and without a standing way in.

## For the studio

Remote support is **off** until someone with full access, at the studio, allows it (System > Remote support). Even then nothing can reach the box until you start a session.

1. Support asks for a session. In System > Remote support, choose how long (1 hour unless you choose otherwise, 4 hours at most) and what support may do:
   - **Full**: support can check and change settings (most useful when something is wrong). That includes everything a full-access device can do except the list below: for example the network settings (a wrong change reverts by itself unless confirmed), projectors and streams (the box then connects to addresses on your network), uploads, the schedule, restarting;
   - **Play and mix only**;
   - **Watch only**.
2. Press **Start support session**. The box connects out to the support server. A support code appears, like `3AW8-C65R`. Read it to support on the phone.
3. While the session is open, every phone and laptop connected to the box shows a banner with the time left; presenter and full-access devices also get a **Stop** button. Stop ends it at once; so does the end of the time, a restart of the box or of its panel service, or a reboot. You can give it more time while it runs, but a session never lasts more than 8 hours in all. Support's sign-in ends with it.
4. The last sessions are listed on the card: who started it, how long, what support could do, how it ended and how many times support signed in.

What support can never do through the connection, whatever you chose: change these settings, start, extend or restart a session, pair or invite devices, make guest or presenter codes, change the PIN or lift its lockout, show access codes, import settings, export settings with their passwords, reset the box to factory settings, or power the box off. They can download the diagnostics file and an export without passwords, and restart the box (which ends the session). Only the panel is reachable: OSC, DMX, SSH and everything else on the box are blocked on the support connection.

Your IT only needs to allow the box to send UDP to the support server's address and port (51820 unless your support provider says otherwise). No incoming rules, no port forwarding.

## For support

Once per support laptop, get a WireGuard configuration from whoever runs the support server and import it in the WireGuard app. When the studio has started a session and read you the code:

1. Switch the WireGuard connection on.
2. Open the address the studio's panel shows, `http://<the box's support address>/` (for example `http://10.77.0.40/`; a box whose panel is not on port 80 shows the port too). The panel asks for the support code.
3. Type the code. You are signed in for the rest of the session, with what the studio chose. A code works for 3 sign-ins. Wrong codes are limited: 5 from one address in 10 minutes, or 15 from all addresses together, lock sign-in for 5 minutes; if someone else on the support network is guessing, ask the studio to stop and start a new session (that resets it).

The screen snapshot on the Live screen shows you what the display shows.

## Running the support server (the owner)

A small Linux server with a fixed public address (any cheap VPS; Debian or Ubuntu).

1. Copy `tools/support-hub/` to it and run, as root, `./setup-hub.sh support.example.com 51820`. It installs WireGuard, makes the server's key, sets up the network `10.77.0.0/24` with a firewall (support laptops `10.77.0.2` to `.31` may reach boxes' panels on `.32` to `.254`; boxes cannot reach each other or the laptops) and prints the values every box needs. Open UDP 51820 in the provider's firewall.
2. For each support laptop: `./add-peer.sh support anthony-laptop` prints a WireGuard configuration to import on that laptop.
3. For each box: in its panel (System > Remote support), copy **This box's key**. On the server: `./add-peer.sh box studio-a <key>`; it tells you the box's address. In the box's panel, fill in the support server, the server key, the support network and that address, and save. Nothing connects until the studio starts a session.
4. A lost or stolen box: `./add-peer.sh remove studio-a`.

For a fleet, the server, its key and the network can be put in `/etc/pvj/support.json` on each box (`{"endpoint": "...", "server_key": "...", "network": "10.77.0.0/24"}`); the box's own address is still set per box. The file can never allow remote support by itself.

## What is trusted, and the limits

- The support network (10.77.0.0/24 by default) must not be a network the box is already on; the panel refuses it, because the studio's own devices would then look like remote support. Remote rules apply only while a session is open.

- The support server sees panel traffic during a session (the panel is plain HTTP inside the encrypted tunnel) and can reach any box that has a session open. Keep it small, updated, and used only for this.
- A box's private key never leaves it (`/var/lib/pvj-support`, root only). Removing the box on the server revokes it.
- A Pi has no clock battery. If it has not got the time from the network yet, the support server may refuse its first contact; the card then says "Waiting for the support server". Wait for the clock, or start the session again.
- Networks that block outgoing UDP, or allow only a web proxy, cannot use this. A fallback over TCP is not built.

## How it is built

`pvj/support.py` (the panel's side: settings, the session, the code, the rules above) and `pvj/supportd.py` (`pvj-supportd`, a small root helper with only the network-admin capability: it creates the WireGuard interface `wg-pvj` and its firewall table, keeps the deadline itself, and removes both when it starts, stops or the time is up). See D29 in `project-log/DECISIONS.md`.

## Tested

On the test Pi 4 (Debian 13), against a stand-in support server in a network namespace on the same Pi: the tunnel came up in seconds; the panel answered through it and asked for the code; a wrong code was refused and the right one signed in with the chosen role; every refused action above answered 403; SSH was blocked; restarting the helper during a session closed the tunnel and the firewall table at once and the panel ended the session; Stop removed everything. **Not tested: a real support server on the internet, a studio's network, NAT, or the support-hub scripts on a real VPS.**
