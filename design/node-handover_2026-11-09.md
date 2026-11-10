<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Opening another node without pairing again: a design (2026-11-09)

Status: design, proposed, for Anthony's review. Nothing here is built. Decision: D132 (`project-log/decisions/D132-node-handover.md`), marked proposed.
Written for Anthony (owner). Plain words first; the builders' detail follows.

## 0. In plain words

Today, when you tap Open on another node in the Nodes list, that node asks for its own PIN, because every node pairs its own devices. You asked that a device already paired on one node, and allowed to see the others, should just open them. This design does that without ever putting your root key on a node. When you tap Open, the node you are on (call it A) checks that you are still paired and allowed, makes a one-time ticket that lives for a minute, and your browser hands it to the other node (B). B phones A once, over the secure connection, to ask "is this ticket good, and who is it for?", and A answers once. B then signs you in as a normal device of its own, with the same role or lower, never higher. If anything is off (ticket old, A switched off, a key that does not match, a node not on your list) you land on the PIN screen as you do today, with one plain sentence saying why. Only Owners and Operators paired over https carry across; guests, presenter codes, controller codes and remote support never do. An Owner stays an Owner on B only if you have put both nodes on a fleet list that you sign once on your computer with your root; without that list the most that carries is Operator, and only on nodes where you switched this on. Removing a device on A also signs it out on B within minutes. Four questions are yours, in section 12.

## 1. Where things stand (facts read from master 63b16a4, 2026-11-09)

- Pairing is per node (D7, D80). A token is 192 random bits, only its SHA-256 is stored (`pvj/auth.py`). It travels as the cookie `__Host-pvj_token` over https (Secure, HttpOnly, SameSite=Strict, no Domain) or as a Bearer header for scripts (`pvj/server.py`). A cookie belongs to one host name, so a token on node-01 is never sent to node-02: that is why each node asks again.
- Roles are stored as `view` (Guest), `live` (Operator), `full` (Owner). A device record has an optional `via` (`code`, `controller`, `recovery`), and `secure: true` when its token was made over https (D79).
- D79: your root CA is made by `tools/boxcert.py` on your computer, its key never goes on a node. Each node has its own key and a certificate signed by the root, and pins the root (`root.pem`). `HttpsBox._issued_by_stored_root` already checks a certificate against the pinned root with `openssl verify`, with the dates left out of it. The node's key is `key.pem` in `<state>/tls`, which only the panel's account reads.
- D127 (discovery, step 2a, PR 224): each node announces `id`, `name`, `tier`, `version`, `https` over mDNS, and `pvj/fleet.py` lists the others. The node id is public. It is a hint for where to knock, never a credential, and a cloned card shares its twin's id.
- Step 2b (the node switcher, PR 238, journal 2026-11-08-node-switcher): Open is a plain `https://address[:port]/` link in a new tab with `noopener noreferrer`. The rule written there, "nothing of this node (token, PIN, the page address) goes with it", is the rule this design changes in one narrow way: a ticket goes, never the token or the PIN.
- Step 2c (signed enrollment through your root) is not built. The fleet proposal of 2026-11-07 calls it "the owner's own root certificate signs a device's enrollment; every node that trusts that root accepts the device with its role". This design reads 2c as signing nodes, not devices (section 3.3) and says why.
- The page's content security policy has `connect-src 'self'` and `form-action 'self'`, so the panel cannot call another node with `fetch`, and cannot post a form to one, today. Both are used below on purpose (sections 5 and 6).
- The Pi has no battery clock. `HttpsBox.clock_trusted()` says when a node's clock was set from the network. Nothing in this design needs two nodes to agree on the time.
- The event log (D125) has fixed kinds (`boot`, `player`, `renderer`, `update`, `projector`, `network`, `signin`, `scene`), 500 lines, Owner-only.
- Step 3 (configurations pushed between nodes, profiles) is not approved and nothing here prepares it beyond the fleet list.

## 2. What is being asked, and what it is not

Asked: "Once one node ... has been paired with a user's device, if they are allowed to see and access other nodes, they should be able to just open the other nodes without having to pair with each one."

In scope: a device that is paired on A and may see the Nodes list (Owner or Operator, over https, not remote support) opens B signed in.
Not in scope: pairing a new device, copying settings or looks between nodes (step 3), a shared device list, guests, joining by code, controllers, anything over plain http.

The cookie is per host, so every scheme has to put a credential in front of B's origin once. That is the heart of the problem, and it is why a shared device list does not remove the hand-over step (option 2 below).

## 3. Trust between nodes

### 3.1 Three options

**Option 1: a ticket that A signs with its own key, B checks the signature.** The ticket says who, which role, for which node, until when. B checks the signature with A's public key, taken from A's certificate, which chains to B's pinned root.
- Good: B does not need to reach A at the time. Easy to explain.
- Against: Python's standard library cannot check a signature, so B would run `openssl` on data from the network (the same road `_issued_by_stored_root` already takes, but on every sign-in). Freshness needs both clocks, and a Pi with no battery clock is wrong by minutes or years until it learns the time; a ticket that is "fresh" by one clock is old by another. One-time use lives only in B's memory and is forgotten at a restart, so a ticket kept in a log could be replayed after B restarts while its date still holds. Nothing tells B that A removed the device a minute later.

**Option 2: a shared device list, synced between enrolled nodes.** Every node holds every device (token hash, name, role). A device paired anywhere is known everywhere.
- Good: no calls at sign-in time.
- Against: it does not remove the problem it is meant to solve, because the browser still has to hand the token itself to B's origin once to get B's cookie, so the long-lived secret travels in a form anyway. Every node then holds every device's token hash, so one stolen card exposes the whole fleet's device list. It needs conflict rules (two nodes change the same device while apart), delete tombstones, a sync channel with its own authentication, and a migration of D7. It is step 3's "hub or signed copy" question in disguise, which you have not decided.

**Option 3 (recommended): a ticket A keeps, and B calls A back to redeem it.** The ticket is 128 random bits that mean nothing by themselves. A remembers it (in memory only) with the device it was made for, the node it is for, and a lifetime counted on A's own monotonic clock. B takes the ticket from the browser and asks A over a verified https connection: "is this one good, for me?" A answers once and forgets the ticket.
- Good: no signature code. Freshness and single use are decided in one place, A, by one clock that only counts seconds. Revocation is live: a device removed a second ago fails. A can also later say "this device is gone" to B, which gives section 8 its form.
- Against: B must reach A while the browser waits (a second or two). That is not a new demand: the Nodes list already needs the nodes to reach each other by multicast DNS and by unicast, and a Wi-Fi with client isolation stops both. If A is unreachable, the tap falls back to the PIN screen with a sentence.

Recommendation: option 3. Option 1 is the same idea with a harder way of checking it and a dependence on clocks that a Pi cannot meet. Option 2 is step 3 and should wait for it.

### 3.2 How B knows it is really talking to A

B opens a TLS connection to A's address (found in B's own discovery list by A's id, never from anything the browser sent), and takes A's certificate from the handshake. It does not use the browser-style checks (name, dates), because the Pi's clock can be wrong. It does this instead, in order:
1. The certificate chains to B's pinned root: the existing `_issued_by_stored_root` (openssl verify with fixed arguments, `-purpose sslserver`, the root's name constraints, dates left out).
2. The handshake itself proves A holds the certificate's private key.
3. If B holds a fleet list (3.3), the hash of A's public key must be the one the list gives for A's id. A key that changed is refused, not trusted on first sight (section 10).

A rogue host on the LAN has no certificate from your root, so it fails at step 1 however it announces itself. A is checked in the same way when it makes a ticket for B, so A never sends a ticket toward an impostor (section 5, step 2).

### 3.3 What an enrolled node is (step 2c)

An enrolled node is a node named in the **fleet list**: a small file that you sign on your computer with the root, and that each node stores and checks against its pinned root. The root key never leaves your computer.

```
fleet.json   {"v":1, "fleet":"<16 hex>", "name":"Anthony's rig", "serial":3, "issued":"2026-11-09",
              "nodes":[{"id":"<node id>", "name":"node-01", "key":"<sha-256 of the node's public key>", "carry":"full"},
                       {"id":"...", "name":"leyline", "key":"...", "carry":"live"}]}
fleet.sig    your root's signature over the bytes "nxlx-fleet-v1\n" + fleet.json
```

- `key` is the hash of the node's own public key (the one in its certificate). A yearly renewal that reuses the key (D79 says to) keeps it; a new key means signing the fleet list again, on purpose.
- `carry` is the highest role this node will give to a device that arrives from another node of the list: `full`, `live` (Operator), or `view`/none (this node takes nobody in). It is the venue's safety valve: your rig says `full`, a client's node says `live`.
- `serial` only counts up. A node refuses a list with a lower serial than the one it has, so an old list cannot bring back a node you removed. Dates are shown, never judged, because the Pi's clock is unreliable (the rule D79 applies to certificates only on a clock set from the network).
- Making it: `python3 tools/boxcert.py fleet add node-01.local` asks that node for its fleet card (`GET /api/fleet/card`: id, name, key hash, public information the node already announces, plus the key hash), checks the card against the certificate chain on your computer, and adds a row. `fleet remove`, `fleet sign` (asks the passphrase, bumps the serial) and `fleet show` complete it. The file goes to each node through Setup > This node > Nodes on this network (Owner, over https only), which checks the signature against the pinned root and that the node's own id is in the list.
- A node is **enrolled** when it holds a current fleet list that names it. Two nodes are **in the same fleet** when each holds a list with the same `fleet` id, and each names the other, so a stale list on one side cannot be used to promote itself.
- This is "signed enrollment through his own root" for nodes. Devices are not enrolled: a device keeps being paired by a node, and a node vouches for it by the hand-over. Signing every device would put your root passphrase on the road for each new phone, and the fleet proposal's own list of risks says the hub or signed copy question belongs to step 3.

Without a fleet list the nodes still share the pinned root (they have certificates from your root, so they are yours by construction), and that is enough for the lower tier in section 4: Operator at most, and only on a node where an Owner switched "Accept devices from other nodes" on there. That switch is the node's own consent; the list is yours.

## 4. Roles

The rule, in one line: **the role on B is the lowest of what the device has on A, what A and B may give, and Operator unless both nodes are in the same fleet.** Never higher than on A.

| Case | On A | On B |
|---|---|---|
| Both in the same fleet list, B's `carry` is `full` | Owner | Owner (with the four exceptions below) |
| Same, B's `carry` is `live` | Owner | Operator |
| Same fleet, Operator on A | Operator | Operator (or Guest if B's `carry` is `view`) |
| No list, or the nodes are in different fleets, B's switch on | Owner | Operator |
| No list, B's switch on | Operator | Operator |
| B's switch off, or B has no https | anything | nothing: the PIN screen |
| Guest, or a device paired by a join code or a link | Guest or presenter | nothing, never |
| A device paired from a controller's code (MIDI) | any | nothing, never |
| A remote support login | Owner by role | nothing, never |

Which devices carry: role Owner or Operator, `secure: true` (its token was made over https, D79), and a record with no `via`, or `via` equal to `recovery` (a genuine Owner who had lost every device). Excluded: Guests, `via` `code` (a presenter by code), `via` `controller` (someone who stood at one node), `via` `node` (a hand-over does not chain: B's device cannot hand on to C; C asks A), and every remote support login (`support.authenticate`), whose routes are added to `support.REMOTE_DENY` with the reason "support stays at the node it was given to".

**The four exceptions for a carried Owner** (question 2): on B it may not replace B's root, upload B's certificate or key request (`/api/https/*`), load or remove B's fleet list or switch B's accept setting, or make recovery codes or wipe the node (factory reset). These need a device **paired at B itself** (a PIN, a recovery code), because they are the actions that decide who can sign in to B at all, and B should not rest them on A alone. Everything else an Owner does, a carried Owner does. The mechanism is one flag on B's device record, `via: node`, and one check in the gate for those few routes.

B's record is an ordinary device of B (name "device name, via node-01", role as above, `via: node`, `secure: true`, `from: {node, device}`), so People and codes lists it, the Owner at B can remove it, and the existing gate, rate limits and guest rules apply with no new branch. A device that taps Open again on the same B gets the same record (matched by A's node id and device id) with a new token; no pile of records. It counts in the shared pool of places beside guests, so it can never fill the places kept for the PIN (`_room_for`).

## 5. The flow in the panel

Preconditions for the Open row: B is in A's discovery list with https on; A has https loaded; this device is Owner or Operator, `secure`, not remote; and B passes the A-side check below. If not, Open does what it does today and a line says "You will be asked for this node's PIN" (the line says which of these is missing, in words).

1. **Tap.** The panel opens an empty tab at once, named for the node (`window.open('', 'nxlx-' + id)`, in the tap, so no popup blocker objects) and sets its `opener` to null. The tab shows "Signing in to node-02..." until the page is replaced.
2. **POST to A for the ticket.** `POST /api/handover/ticket` on A, JSON, with the usual request header, same-origin cookie, and a body `{"to": "<B's id>", "proof": "<hash of a value the page just made>"}` (see "proof" below). The panel sends an id, never an address: A looks B up in its own discovery list. A checks, in this order: the device may carry (section 4), B is in A's list or the lower tier applies, and **B is who it says it is**: A connects to B and runs the checks of 3.2 on B's certificate, so a ticket is never made toward a host that only announced B's id. A throttles tickets (6 a minute per device, 30 per minute for the node) and makes a ticket: 128 random bits, kept in memory with the device id, B's id and key hash, the proof hash, and an end time counted on A's monotonic clock, **60 seconds**. A answers `{"ticket": "...", "action": "https://B:port/handover"}`; the address is the one A just verified, not the one the browser may have seen.
3. **Form POST to B.** The page builds a form in its own memory (`method=POST`, `enctype=application/x-www-form-urlencoded`, `action` = the answer, `target` = the named tab, two hidden fields: `ticket` and `secret`, the value whose hash was the proof) and submits it. The ticket is therefore in a JSON answer over TLS, in the page's memory, and in a POST body over TLS: **never in an address, a query string, a fragment, the history, a Referer (the page sends `Referrer-Policy: no-referrer`), an access log or the journal**. The panel's content security policy gains `form-action 'self' https://B:port` for each node the page may open, computed on the server from the same list the Nodes view uses and checked against the one shape `CSP_ORIGIN` already allows, as `page_csp` does for `connect-src`.
4. **B redeems with A.** B's `POST /handover` exists on the TLS listener only (plain http gets 404 with the https address, never a redirect). It takes the one form it knows, 2 KB at most, and does the following. It checks that it accepts hand-overs at all, then calls A's `POST /api/handover/redeem` over a connection verified as in 3.2, with `{"ticket", "secret", "to": B's id, "key": B's key hash}`. A looks the ticket up, checks it has not ended and not been used, that `to` and `key` are the ones it made it for, that hash of `secret` is the proof, and that the device is still paired, still Owner or Operator, and still `secure`. It removes the ticket and answers `{"name", "role", "lease"}`, where `role` is already the lowest of the device's role and the caps A knows. B then lowers the role again by its own caps (section 4), never raises, and makes or refreshes the device record.
5. **B answers a page, not a redirect.** B answers 200 with a tiny page and `Set-Cookie: __Host-pvj_token=...; Secure; HttpOnly; SameSite=Strict; Path=/`. The page's one script (a file of B's own, `'self'`) runs `location.replace('/')`. A redirect would not do: the move after a cross-site POST counts as cross-site, so a SameSite=Strict cookie would not be sent and the panel would show the PIN screen. The move by B's own page is same-site and carries the cookie. (Needs a try in Safari, Chrome and Firefox, hardware row HO6.)
6. **B's panel opens signed in.** If anything failed in steps 2 to 5, the tab ends at B's PIN screen with the node's sentence ("node-02 did not accept the hand-over: the ticket had run out. Use the PIN.") and the failure is logged (section 9). A browser's back button never re-sends the ticket: the history entry was replaced, and the ticket is gone from A anyway.

**What the "proof" is for.** It binds a ticket to the browser tab that asked for it: A keeps only the hash, the page keeps the value, and B needs both. A ticket that leaks without the value (a proxy log, a screenshot) cannot be redeemed. It is a second guard, not the first: single use and the 60 seconds are the first.

**The Fire tab launcher and installed apps.** The panel saved as an app on an iPhone or Android may open B in the system browser, whose cookie jar is not the app's. That is how Open behaves today, so it signs in to B in the browser, and not in the app. Whether the launcher's locked tablet (D131 on branch firetab-log) can use it is for that work; this design adds a route that a launcher-locked device would have to be allowed, and does not decide it.

## 6. Where the checks live

- New module `pvj/handover.py` (~300 lines, standard library): the peer client (TLS connect, checks of 3.2, one JSON POST over the connection), the ticket table (in memory, 64 at most, monotonic end times), the lease table (section 8), the fleet list reader. It never runs a long-lived thread; a short worker thread does the lease checks (section 8) and sleeps otherwise.
- Routes on the TLS listener only: `POST /api/handover/ticket` (Operator and up, `secure`, not remote), `POST /handover` (no device; a form; throttled), `POST /api/handover/redeem` and `POST /api/handover/lease` (peer calls; no cookie; must carry a ticket or lease that only A and the right B hold; rate limited), `POST /api/handover/poke` (no authority, see section 8), `GET /api/fleet/card` (public, same facts as the mDNS announcement and the key hash), Owner routes for the list and the accept switch. Each gets a row in `policy` and, where the route could outlive a support session or reach through the tunnel, one in `REMOTE_DENY`.
- Peer calls are limited to the private, link-local and loopback addresses that `fleet.py` already accepts, one in flight at a time, 3 seconds to connect, 6 in all. The redeem and lease paths are the only ways B makes an outbound connection to an address the network gave it, so they are the ones tests aim at (section 10).

## 7. What the switches do

- **Accept devices from other nodes** (Owner, at that node, Setup > Nodes on this network). Off by default, kept in `settings.json` as `fleet.accept`, in `BOX_OWN` so an import never brings it, gone at a factory reset. Off: `POST /handover` answers the PIN screen with "this node does not take devices from other nodes". Turning it off also removes every `via: node` device at once.
- **Owner access only over the secure connection** (D79). A hand-over never needs it and never loosens it. Tickets, redemption, the cookie and the move all exist over https only. The new device is marked `secure: true` because its token was made over https, so with the switch on B does not refuse it for being "paired over plain http", and with the switch off it still cannot be used over http either (the cookie is Secure and `__Host-`; an `Authorization` header with the token would work over http, as for any token, and is no new road). On A, a device paired over plain http is not eligible at all, whatever the switch says, because its token may have been listened to. So the switch protects the start of the chain: the first node pairs you over https or not at all (once it is on).
- **Locking pairing.** I found no switch of that name. The nearest are the guessing lockout (20 wrong PINs in 10 minutes shuts all pairing for a minute, `Auth.pairing_locked`) and the guest-controls lock (D80). Neither affects a hand-over: no PIN is typed, so nothing counts as a guess and a lockout does not stop one; the guest lock covers Guests, who never carry. A bad ticket has its own throttle at B (10 a minute for the node, 5 a minute per source address) so guessing tickets cannot hide behind or feed the PIN's throttle. The lock for this feature is "Accept devices from other nodes" above. If you meant a new button that stops all pairing on a node including hand-overs, say so (question 4).

## 8. Revocation

A device removed on A: A's device record goes, and with it every ticket made for it (the ticket table is checked against the device list at redemption). The hand-over devices already made on other nodes need to go too, in a time you can say out loud:
- **Lease.** At redemption A gives B a lease handle (128 random bits). B stores a hash of it in the device record. While the device is in use, B asks A `POST /api/handover/lease {handle}` at most every 10 minutes (and at once on a poke): "is this device still paired, and with what role?" A answers `{ok, role}`. Gone: B removes its device, which signs it out (the existing `revoke` path, presence list and the OSC address included). Role lower: B lowers it. Role higher: ignored (a higher role needs a new hand-over: never raised).
- **Poke.** When A removes a device it knows it handed over (A keeps, in the device record, the ids of the nodes it made tickets for), it sends each such node `POST /api/handover/poke {handle}`. The poke carries no authority: B reacts only by asking A, so a forged poke can cause one extra question and nothing else. That makes removal on A reach B in a second or two when both are up.
- **A switched off or unreachable.** B keeps the device until **7 days** after the last good answer from A (the same figure as a guest code's idle limit; question 3), or until B's Owner removes it, whichever is first. You do not want a show to lose its operators because the node they signed in from was switched off for the night. The answer from A is the only thing that renews it; a hand-over refreshes the record.
- **Removing everything** ("sign out every device", factory reset): A's record is gone; the next lease check on each node removes the hand-over devices. A factory reset of B drops them with the rest.
- **Removing a node from the fleet.** You sign a new list without it (a higher serial) and load it on the other nodes. They then refuse the removed node as a peer, and their `via: node` devices that came from it fail their next lease check (the peer is no longer trusted), so they go within 10 minutes of use. A removed node that is still running keeps its own devices; it is simply not in the fleet.
- **Role change on A** (demote an Owner to Operator): the lease answer carries the new role; B follows it downward at the next check.
- **Log out** in the panel on B removes B's record only; A is untouched, and one more tap on Open signs in again.

## 9. The event log and the journal

A new kind `handover` (the `KINDS` tuple and the Owner-only log are the only changes to the events module), plain sentences, `who` the device's name:
- On A, when a ticket is redeemed: "Anthony iPad (Owner) was signed in to node-02." On a refusal at A: "A ticket for node-02 was not made: node-02's key does not match the fleet list."
- On B, on success: "Anthony iPad (Owner) signed in from node-01." On refusal: "A hand-over from node-01 was refused: the ticket had ended. / ... node-01's key is not the one on the fleet list. / ... this node does not take devices from other nodes. / ... node-01 could not be reached."
- On removal at B by lease: "Anthony iPad was signed out: node-01 no longer has it paired." On the 7 days: "... signed out: node-01 has not answered for 7 days."
- Fleet list loaded: "The fleet list (serial 4, 3 nodes) was loaded."

The journal (the node's own log, not the Owner's page) gets the same sentences with the device id, never a ticket, handle, secret, token, key or full certificate. Repeats within 5 minutes collapse as the log does already. The two ends write separately; there is no shared log and none is needed: the pair of lines, one on each node, is the trail. The log is not a security audit trail (D125), and this does not make it one.

## 10. Threats

| Threat | What stops it |
|---|---|
| A stolen ticket (read from a proxy, a history, a screenshot) | It is 60 seconds long, used once, and useless without the `secret` that only the page holds. It never appears in an address. After it is used it is dead at A. |
| Replay (the same POST sent again, back button, double tap) | A deletes the ticket when it answers. The second call finds nothing. The back button goes to a replaced history entry. Two quick taps make two tickets for the same device, each used once. |
| A rogue host on the LAN that announces itself as B | A will not make a ticket for it: A connects to B and requires a certificate from your root, with the key on the fleet list (3.2). Such a host cannot have one, and the root's name constraints keep even a legitimately signed certificate to `.local` names and private addresses. A browser that is sent there would show a full-page warning, and the form posts only to the address A verified. |
| A rogue host that announces itself as A (to B) | B finds A's address in its own discovery list and requires the same checks; the redeem call fails if it is not A. A fake A could only answer "no". It cannot make B sign anyone in unless it holds A's key. |
| A node whose certificate changed | The same key under a renewed certificate: nothing changes (the list holds the key hash). A new key: the list does not match, so hand-overs to and from it are refused with the plain sentence, until you sign the list again. Never trust on first use, never "accept this new key" in the panel. |
| A compromised node (someone took the card, or got in) | It has its own key and certificate, which is what D79 already says (a stolen card can pretend to be that node until the certificate ends). It can make tickets and lease answers only for its own devices, up to the `carry` of each node that accepts it. A compromised A can say "Owner" and B gives up to `carry` and nothing the four exceptions guard, which is why your venue nodes should say `live`. A compromised B learns the device name and role of a device that taps Open on it, and holds the device's B-side record; it gets no token for any other node (the ticket was for B only). The cure is the same as D79: new key, sign the list again without or with the new key. A stolen B-side token works at B only. |
| Downgrade to http | The routes exist on the TLS listener only. The cookie is `__Host-` and Secure. A device paired over http is not eligible to carry. A node without https offers no hand-over, and the panel says so. No redirect from http to https is ever made (as today). |
| CSRF | The ticket request is a normal API call: JSON only, the `X-PVJ-Request` header, an `Origin` that equals `Host`, a SameSite=Strict cookie. The one form POST to B is cross-site on purpose, so it cannot lean on those: it is accepted only with a live ticket and its secret, one field set, 2 KB, no other effect. The remaining case is a login CSRF, an attacker who is himself an Operator making your browser post his ticket and secret to B, which would sign you in as his device; for that he needs your browser to run his page and his own valid ticket, he gains nothing he does not have (B's record is a fresh token he never sees), and the action in B's People list names his device, so it is visible. Accepted. |
| Clock skew (no battery clock) | No decision uses a wall clock. Ticket life and lease checks are counted on each node's monotonic clock; the fleet list uses a serial, not a date; the TLS checks leave the dates out. The only dates anywhere are the ones shown to people. |
| Guessing a ticket or a lease handle | 128 bits each; throttled at 10 a minute per node and 5 per source address; a wrong one is logged without its text. |
| A flood of redeem calls turning B into a fire hose against A | Peer calls need a ticket first (the form), are limited to one in flight and 6 a minute, and go only to addresses that are in B's own discovery list. |
| OSC and MIDI | A hand-over makes an Owner device at B, and an Owner device's last address is what OSC's "sender must be a paired device" rule (D78) checks, so that device's address can send OSC to B while it is signed in; that is the same as any Owner device at B and it ends the moment the lease does (`forget_address` on revoke). MIDI controllers and their pairing codes are not devices: they never carry, and a controller's code at A does nothing at B. OSC feedback targets (D82) are per node and not carried. |
| A support helper | Remote support routes are denied for tickets, redemption and the fleet list; a support login is not a carrying device; B refuses `/handover` arriving through the tunnel. |
| What "Owner only over https" does | See section 7: it is not loosened, not needed by this feature, and makes the chain start on https. It does not stop a carried Owner from being used on B if B has the switch off and the device is paired over https, as expected. |
| Cross-protocol use of the node's key | The node's TLS key is used only for TLS here (it proves itself in the handshake). Nothing is signed with it, so there is no signed-message format to confuse. This is another reason for option 3 over option 1. |
| Hand-over on a hostile Wi-Fi | The bytes between nodes and browsers are TLS. Client isolation stops it (B cannot reach A), and the tap falls back to the PIN. |

## 11. Slices, tests and the nodes

Small pull requests, in order; each ends green on its own and changes nothing visible until the last two.

**Slice 1: the fleet list and its tool.** `tools/boxcert.py fleet add/remove/sign/show`, the file format, the signature with `openssl dgst`, the canonical bytes. `GET /api/fleet/card`. Tests (`tests/test_boxcert.py`, `tests/test_fleet.py`): a list signs and verifies; a changed byte fails; a lower serial is refused; the card matches the certificate's key hash; a tool run with the wrong passphrase writes nothing; the card route answers no secret and is read-only.
**Slice 2: a node loads the list (this is step 2c).** `pvj/handover.py` reader, `POST /api/fleet/list` (Owner, https, paired at this node), status in Setup > Nodes on this network, event `handover`, `fleet.accept` switch (off). Tests: signature checked against the pinned root; a list that does not name this node is refused; serial order; the accept switch kept, not exported, cleared at a factory reset; a carried-Owner exception check on those routes (no device yet, so a unit test with a fake `via: node` record).
**Slice 3: the peer channel.** `handover.py` peer client and the verified TLS connect, with the key hash and root checks. Tests against a local TLS server with certificates made by the test's own root (`tests/test_https.py` shows how): good peer; certificate from another root; right root and wrong key hash; expired clock case (the system time is moved on purpose and the dates are ignored); an address that is not private; a peer that answers slowly (timeout); one in flight.
**Slice 4: ticket, redeem, lease, record.** The four routes, the ticket table, the `via: node` record, caps, the exceptions in the gate, `REMOTE_DENY` rows, throttles, lease and poke. Tests: every row of the role table in section 4; ticket expires at 60 s (a fake monotonic clock); used once; wrong node; wrong secret; device removed between ticket and redeem; device demoted; second tap refreshes the same record; places count against the shared pool; revoke on A removes at B after a poke and after a lease; 7 days without an answer; the accept switch off removes the records; no secret in any log line, any API answer or the diagnostics file (a test walks them, as the PIN test does); remote support gets 403 on all of it.
**Slice 5: the panel.** The Open flow, the named tab, the form, `form-action` in `page_csp`, the sentences. `panel.test.js`: a fake pair of nodes; one request to A and one form to B; no ticket in any address; the opener is null; the fallbacks and their sentences; nothing asked before the tap; Open for a node that cannot carry shows the old line.
**Slice 6: review, hardware rows, a short soak.** An independent review of slices 3 to 5 (the same arrangement D130 used for its slices) before the soak; two nodes handing over 200 times with a lease check every minute for an hour on node-01, node-02 free of leftover threads and sockets.

**Hardware rows** for `tools/DEVICE-TESTING.md`, section "Hand-over between nodes". node-01 is the Pi 4, node-02 the Pi 3B. Both need a certificate from your root and the fleet list loaded; the Pi 3B's clock matters for HO9.

| ID | Do | Pass | Fail |
|---|---|---|---|
| HO1 | node-01 and node-02 each with a certificate from your root; sign and load a fleet list naming both with `carry` `full`; Setup shows both with the list's serial | Both pages show "Fleet list loaded", the same fleet id, serial 1 | Either refuses; or shows a node not named |
| HO2 | On node-01 paired as Owner over https, Nodes, Open node-02 | node-02 opens signed in as Owner; People on node-02 lists "device, via node-01"; both event logs have a line | The PIN screen; or a second record on a second tap |
| HO3 | Same, node-02 to node-01 (the Pi 3B asks the Pi 4) | The same; the time from tap to panel is written down | Over 8 seconds, or a leftover socket on node-01 |
| HO4 | Remove the device on node-01 People list; then within 2 minutes use node-02 | node-02 signs the device out; the event says why | The device still works after 10 minutes of use |
| HO5 | Power off node-01; keep using node-02 | The device keeps working; after 7 days of node-01 off (set the lease limit to 2 minutes for this row in a test build) it is signed out | Signed out at once, or never |
| HO6 | Safari on iPhone, Chrome on Android, Safari or Chrome on the Mac: the Open flow | Lands on node-02's panel signed in, no PIN screen, back button does not re-ask | Any browser shows the PIN screen after the move |
| HO7 | Switch "Accept devices from other nodes" off on node-02, tap Open | PIN screen with the node's sentence; no new device | A signed-in panel |
| HO8 | Remove node-02 from the fleet list (serial 2) on node-01 only | Open on node-01 offers the PIN note for node-02 and makes no ticket | A ticket is made |
| HO9 | node-02 with its clock set wrong by a day (node-02 stopped from the network time) | Hand-over still works both ways | Refused for the date |
| HO10 | An Operator device from node-01 with no fleet list on node-02 and the switch on | Operator on node-02, never Owner | Owner |
| HO11 | A guest link device and a presenter code device tap Open | Open shows the PIN note; no ticket route is called | A ticket is made |
| HO12 | A laptop on the same LAN with no root, announcing node-02's id with `avahi-publish` | node-01 will not hand over to it; the Open row says it cannot confirm that node | A ticket made or a form posted |
| HO13 | node-01's certificate renewed with the same key; then with a new key | Same key: nothing changes. New key: hand-over refused until the list is signed again, with the sentence naming the key | The new key accepted |
| HO14 | The one-hour soak of slice 6 on node-01 and node-02 | No growth in sockets, threads or memory beyond the first minute; no frame drops during a hand-over on node-01 while a clip plays | A visible stutter in the picture during a hand-over |

## 12. Questions for Anthony

1. **Should an Owner carry only with your signed fleet list, and the list set each node's limit?** (a) Yes: Owner carries only if both nodes are on a list you signed; without a list, Operator at most and only where you switched it on; each node can say `full` or `live` (venues say `live`). (b) The root alone is enough: any node with a certificate from your root counts as yours, and an Owner carries to all of them with no extra step. (c) No Owner carries at all: Operator is the top. I recommend (a): one extra step each time you add a node, and a borrowed or lost node can never receive an Owner on its own say.
2. **May a carried Owner do everything on the other node?** (a) Everything except four things that need a device paired at that node: replace the root, change the certificate or the fleet list or the accept switch, make recovery codes or wipe the node. (b) Everything, no exceptions. (c) Nothing Owner-only at all. I recommend (a): the four are the actions that decide who can sign in to that node.
3. **How long may a carried device stay signed in on a node when the node it came from is switched off?** (a) 7 days from the last time that node answered, then it signs in again (when the node is back). (b) 24 hours. (c) Until someone removes it. I recommend (a), the same as a guest code's idle limit, so a show weekend survives a node switched off for the night.
4. **Do you want a "lock pairing" button that also stops hand-overs on that node?** I found no switch of that name. Today the lock for this feature is "Accept devices from other nodes", which is off until you switch it on at that node. (a) That is enough. (b) Add a Lock button on the Room page that stops PIN pairing, join codes and hand-overs together, for a night when the room is in other hands. I recommend (a) for now; (b) is a small add-on later.

## 13. What this does not change

PIN pairing stays the way into a node with nothing else; roles, the guest lock and the join codes are as before; discovery (D127) and the switcher keep working as they do, and the switcher's rule "never carry this node's token or PIN" becomes "never carry a token or PIN, only a one-time ticket that this node made for that node". No package is installed, no root key goes on a node, and no setting is carried from one node to another: that is step 3, not approved.

## 14. Not checked yet

- Whether Safari (iOS and macOS) accepts a Set-Cookie on the response of a cross-site form POST to a `.local` name and then sends it on the same-site move by the page (row HO6). Chrome and Firefox: expected, not tried.
- Whether `form-action` blocks the post in any browser that applies it to the tab named by `target`. The form is built on the same page, so the policy applies as for a normal link.
- The time a Pi 3B needs for the TLS connection to the Pi 4 plus an `openssl verify` (the figure for HO3 is the first measurement).
- That `window.open('', name)` followed by `opener = null` is allowed in an installed web app on iOS.
