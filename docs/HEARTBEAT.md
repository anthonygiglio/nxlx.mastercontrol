<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Heartbeat: a push to your phone when the node goes quiet

A node that has lost power or its network cannot tell you so. Something outside it has to notice that it stopped saying "I am here". That is what a *dead man's switch* service does: the node pings a web address every few minutes, and the service sends a push to your phone when the pings stop.

The node's half is the **Heartbeat** card on Setup > Health (the Owner's only). It works with any service that gives you a ping address and sends a push when pings stop. [healthchecks.io](https://healthchecks.io) is one example, and so are the push monitors of self-hosted tools such as Uptime Kuma; this is not an endorsement of any of them. **The heartbeat is off until you switch it on, and the node sends nothing anywhere until then.**

## Set it up (about ten minutes, once)

These steps are on the service's side, so the screens differ a little by service; healthchecks.io is used as the example.

1. **Make a free account and one check** at the service. Name it after the node, for example "Leyline node".
2. **Set its period and its grace time.** The *period* is how often the node pings; the *grace time* is how long the service waits after a missed ping before it raises the alarm. Suggested: **period 15 minutes, grace 60 minutes**. A reboot, a short network drop or a restart of the panel then never wakes you, and a node that is really gone is reported within about an hour and a quarter.
3. **Connect it to your phone.** In the service's integrations, add the one for your phone (for healthchecks.io, its own phone app, a Telegram or Signal bot, email or a notification service you already use). Send its test notification and make sure it arrives, with the phone's sound on.
4. **Copy the check's ping address.** It starts with `https://` and ends with a long code (healthchecks.io: `https://hc-ping.com/` and a code). Treat it like a password: anyone who has it can tell the service the node is fine.
5. **In the panel**, as the Owner, open Setup > Health and find the Heartbeat card. Paste the address and press **Save address**. The panel shows only the end of it from then on.
6. **Press Send a test ping now.** It says in plain words whether the service took the ping. On the service's page the check should turn green at once.
7. **Switch Send a heartbeat on**, and choose how often (the same as the period you set at the service; 15 minutes is a good default). The node sends its first ping about 20 seconds later and then on that interval, for as long as it is running.
8. **Prove the alarm once.** Pull the node's network cable (or its power) at a time that suits you and wait for the grace time to pass: the phone should ring. Plug it back in and the service says the node is up again.

## What the node sends

One plain HTTPS `GET` to your address, with a short time limit, and nothing else: no body, nothing added to the address, no node name, no network address, no device names, no PIN. The only header it adds is a fixed program name. It does not follow redirects, so a ping never leaves HTTPS.

**Tell the service when the player is down** is an extra switch for healthchecks.io: while the player is not running, the ping goes to your address with `/fail` added, which the service counts as a failure and tells you at once without waiting for the grace time. Leave it off for other services; with it off every ping is the same plain ping.

## What it does when something goes wrong

- A service that is slow, a missing network, a wrong address or a refused ping is never an error for the panel or the player. The card says in words what happened the last time (for example "The service does not know that address (404)"), and the Health page and the player carry on.
- The node writes one line to its log when pings start failing and one when they work again; the address is never in the log, in an export of settings, or in a diagnostics file (only its masked end).
- The node does not know that the *service* has stopped working. If you want to be sure, press **Send a test ping now** once in a while, and check the service's page after a power cut.

## Who may change it

Only the Owner, at the studio. A Guest or an Operator does not see the card. **Remote support never changes the address, switches it on or off, or sends the test ping**: a helper cannot point the node's pings at a service of his own, or silence your phone while he works. A helper in a session with the Owner's role may read the card (with the address masked).

The setting (`heartbeat` in `settings.json`) belongs to the node: it is not part of a settings export, a file that holds it does not set it (the import notes that it was left), and a factory reset forgets it. This is the only part of the box that reaches out by itself to a service on the internet, and only after the Owner switched it on; everything else works on a network with no internet.

## Not proven yet

The node's side has been tested against a local test server only. It has not been run against a real service, and the push to a phone, the 15 minute period and the 60 minute grace were not tried on hardware. Do step 8 once before trusting it.
