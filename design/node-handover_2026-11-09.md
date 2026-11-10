<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Opening another node without pairing again: a design (2026-11-09)

Status: design, proposed, for Anthony's review. Revised after an independent review on 2026-11-09. Nothing here is built. Decision: D132 (`project-log/decisions/D132-node-handover.md`), marked proposed.
Written for Anthony (owner). Plain words first; the builders' detail follows.

## 0. In plain words

Today, when you tap Open on another node in the Nodes list, that node asks for its own PIN, because every node pairs its own devices. You asked that a device already paired on one node, and allowed to see the others, should just open them. This design does that without ever putting your root key on a node. When you tap Open, the node you are on (call it A) checks that you are still paired and allowed, makes a one-time ticket that lives for a minute, and your browser hands it to the other node (B). B phones A once, over the secure connection, to ask "is this ticket good, and who is it for?", and A answers once. B then signs you in as a device of its own, with the same role or lower, never higher. If anything is off (ticket old, A switched off, a key that does not match, a node you did not approve) you land on the PIN screen as you do today, with one plain sentence saying why. Only Owners and Operators paired over https carry across; guests, presenter codes, controller codes and remote support never do. Two nodes trust each other only when you have said so: either both are on a fleet list that you sign once on your computer with your root, or the Owner of a node accepts the other node once by its key fingerprint, shown on both panels. Having a certificate from your root is not enough, because a stolen, lent or retired node still has one. A signed-in device from another node can use B's panel but can never create, show or remove a way into B (no PIN, no codes, no guest links, no removing the local Owner), so a hand-over can never become permanent access. Six questions are yours, in section 13.

## 1. Where things stand (facts read from master 63b16a4, 2026-11-09)

- Pairing is per node (D7, D80). A token is 192 random bits, only its SHA-256 is stored (`pvj/auth.py`). It travels as the cookie `__Host-pvj_token` over https (Secure, HttpOnly, SameSite=Strict, no Domain) or as a Bearer header for scripts (`pvj/server.py`). A cookie belongs to one host name, so a token on node-01 is never sent to node-02: that is why each node asks again.
- Roles are stored as `view` (Guest), `live` (Operator), `full` (Owner). A device record has an optional `via` (`code`, `controller`, `recovery`), and `secure: true` when its token was made over https (D79).
- D79: your root CA is made by `tools/boxcert.py` on your computer, its key never goes on a node. Each node has its own key and a certificate signed by the root, and pins the root (`root.pem`). `HttpsBox._issued_by_stored_root` already checks a certificate against the pinned root with `openssl verify`, dates left out. `httpsbox.name_matches` already says whether a certificate's names cover an address. The node's key is `key.pem` in `<state>/tls`, which only the panel's account reads. D79 also says a stolen card can pretend to be that node until its certificate ends: "the root alone" is therefore not proof that a node is still yours.
- D127 (discovery, step 2a, PR 224): each node announces `id`, `name`, `tier`, `version`, `https` over mDNS, and `pvj/fleet.py` lists the others. The node id is public. It is a hint for where to knock, never a credential, and a cloned card shares its twin's id.
- Step 2b (the node switcher, PR 238): Open is a plain `https://address[:port]/` link in a new tab with `noopener noreferrer`. Its rule "nothing of this node (token, PIN, the page address) goes with it" changes here in one narrow way: a ticket goes, never the token or the PIN.
- Step 2c (signed enrollment through your root) is not built. The fleet proposal of 2026-11-07 (outside the repo; D127 quotes it) says "the owner's own root certificate signs a device's enrollment". This design reads 2c as signing nodes, not devices (section 3.3).
- The page's content security policy has `connect-src 'self'` and `form-action 'self'`, so the panel cannot call another node with `fetch`, nor post a form to one, today. The form post is allowed on purpose in section 5.
- The Pi has no battery clock. `HttpsBox.clock_trusted()` says when a node's clock was set from the network. Nothing here needs two nodes to agree on the time.
- The event log (D125) has fixed kinds (`boot`, `player`, `renderer`, `update`, `projector`, `network`, `signin`, `scene`), 500 lines, Owner-only.
- Step 3 (configurations pushed between nodes, profiles) is not approved and nothing here prepares it beyond the fleet list.

## 2. What is being asked, and what it is not

Asked: "Once one node ... has been paired with a user's device, if they are allowed to see and access other nodes, they should be able to just open the other nodes without having to pair with each one."

In scope: a device paired on A that may see the Nodes list (Owner or Operator, over https, not remote support) opens B signed in.
Not in scope: pairing a new device, copying settings or looks between nodes (step 3), a shared device list, guests, joining by code, controllers, anything over plain http.

The cookie is per host, so every scheme has to put a credential in front of B's origin once. That is why a shared device list does not remove the hand-over step (option 2 below).

## 3. Trust between nodes

### 3.1 Three options

**Option 1: a ticket that A signs with its own key, B checks the signature.**
- Good: B does not need to reach A at the time.
- Against: Python's standard library cannot check a signature, so B would run `openssl` on network data at every sign-in. Freshness needs both clocks, and a Pi with no battery clock is wrong by minutes or years until it learns the time. One-time use lives only in B's memory and is forgotten at a restart. Nothing tells B that A removed the device a minute later. The node's TLS key would also sign a message format, which is key reuse across uses.

**Option 2: a shared device list, synced between enrolled nodes.**
- Good: no calls at sign-in time.
- Against: it does not remove the hand-over step, because the browser still has to give B's origin a credential once, so the long-lived token itself would travel in a form. Every node would hold every device's token hash, so one stolen card exposes the fleet's device list. It needs conflict rules, delete tombstones, an authenticated sync channel and a change to D7. It is step 3's "hub or signed copy" question in disguise.

**Option 3 (recommended): a ticket A keeps, and B calls A back to redeem it.** The ticket is 128 random bits that mean nothing alone. A remembers it (memory only) with the device, the node it is for, and a lifetime counted on A's monotonic clock. B takes the ticket from the browser and asks A over a verified https connection: "is this one good, for me?" A answers once and forgets the ticket.
- Good: no signature code. Freshness and single use are decided in one place, by a clock that only counts seconds. Revocation is live. A can later tell B "this device is gone" (section 8).
- Against: B must reach A while the browser waits (a second or two). The Nodes list already needs the nodes to reach each other, and a Wi-Fi with client isolation stops both. If A is unreachable, the tap falls back to the PIN screen with a sentence.

Recommendation: option 3.

### 3.2 How a node knows it is talking to its peer

Every peer check, in either direction, does all of the following, in order. A failure at any step ends the call and writes an event (section 10).
1. **Find the peer.** The caller has the peer's id, never an address from the browser. It tries every address its own discovery list holds for that id (a node may have an Ethernet and a Wi-Fi address), one after the other, and accepts the first whose certificate passes steps 2 to 6. If the id is not in the list it runs one scan (the cached rate limit of `fleet.py` applies) and tries again. Only private, link-local and loopback addresses are tried, as `fleet.py` already does.
2. **Chain to the root.** The certificate from the handshake chains to the pinned root, by the existing `_issued_by_stored_root` (openssl verify, fixed arguments, `-purpose sslserver`, the root's name constraints, dates left out).
3. **Key possession.** The handshake itself proves the peer holds the certificate's private key.
4. **The address is covered.** The certificate's names cover the address that was connected to (`name_matches`). A certificate for another node's name does not pass for this address.
5. **The id is bound to the key.** The hash of the peer's public key must be the one that the trust record gives for that id: the signed fleet list (3.3) or a node pairing (3.4). A key that changed is refused, not trusted on first sight. This closes the gap that the id is public: an id only counts together with its key.
6. **Not run out.** When `clock_trusted()` is true on the caller, a peer certificate whose end date has passed is refused and logged ("node-02's certificate ended on <date>"). When the clock is not trusted, dates are not judged, as D79 does.
7. **The list is current.** Every peer call carries the caller's fleet-list serial (or "pairing"). A peer with a lower serial is refused. A node that hears a higher serial fetches the list from that peer, verifies it against the pinned root and adopts it (3.3).

There is no lower tier: the shared root alone is never enough (3.3, 3.4).

### 3.3 The fleet list, and what an enrolled node is (step 2c)

An enrolled node is a node named in the **fleet list**: a small file that you sign on your computer with the root. The root key never leaves your computer.

```
fleet.json   {"v":1, "fleet":"<16 hex>", "name":"Anthony's rig", "serial":3, "issued":"2026-11-09",
              "nodes":[{"id":"<node id>", "name":"node-01", "key":"<sha-256 of the node's public key>", "carry":"full"},
                       {"id":"...", "name":"leyline", "key":"...", "carry":"live"}]}
fleet.sig    your root's signature over the bytes  "nxlx-fleet-v1\n" + fleet id + "\n" + serial + "\n" + the bytes of fleet.json
```

- The fleet id and the serial are inside the signed bytes under a domain prefix, so a signature made for something else cannot pass as a list, and a list cannot be moved to another fleet.
- `key` is the hash of the node's public key. A yearly renewal that reuses the key (D79) keeps it; a new key means signing the list again, on purpose.
- `carry` is the highest role this node gives to a device that arrives from another node on the list: `full`, `live` (Operator) or `none` (this node takes nobody in). Your rig says `full`, a client's node says `live` or `none`. **Carry needs both nodes on the same signed list.**
- `serial` only counts up. A node refuses a lower serial than the one it holds. Dates are shown, never judged.
- Making it: `python3 tools/boxcert.py fleet add node-01.local` asks that node for its fleet card (`GET /api/fleet/card`: id, name, key hash; the first two are already announced), checks it against the certificate chain on your computer, and adds a row. `fleet remove`, `fleet sign` (asks the passphrase, raises the serial) and `fleet show` complete it.
- **Where a node keeps it.** In a file `fleet.json` and `fleet.sig` beside `tls/` in the state folder (mode 0640, written by rename), never in `settings.json`. It is left out of settings export and import, of the diagnostics file and of every API answer except the Owner's own status of it (id, name, serial, node names, no keys). A test like D79's walks every GET, the diagnostics file and an export, and finds the file's contents nowhere.
- **Passing a newer list on.** A node that hears a higher serial in a peer call (step 7 of 3.2) fetches the list from that peer over the checked connection, verifies the signature against its own pinned root and that its own id and key are in it, stores it, and logs it. Nothing is trusted from the peer except the signed bytes. This is question 5; with it off, you load the list on each node by hand.
- A node is **enrolled** when it holds a current list that names it. Two nodes are **in the same fleet** when both lists have the same fleet id and name the other.
- This is "signed enrollment through his own root" for nodes. Devices are not enrolled: a node vouches for a device by the hand-over. Signing every device would put your root passphrase on the road for each new phone.

### 3.4 The alternative for one pair: node pairing by fingerprint

For two nodes with no fleet list (a rig of two, or a friend's node), the Owner of B can **accept A once**, by the fingerprint of A's key: B shows "node-01 asks to be trusted: key 3F:9A:... (check that node-01 shows the same)". The same number is shown on node-01's panel. The Owner at B types or confirms it, over https, on a device paired at B. B then keeps a pairing record {id, name, key hash, carry} (carry chosen then, `full`, `live` or `none`), beside `tls/`, with the same exclusions as the list. It is symmetric only if A's Owner accepts B too; a one-sided pairing lets A's devices into B but not B's into A. The record lists on both panels; either Owner removes it with a tap, and removing it signs out every `via: node` device from that peer within the next lease check.

It carries the same roles as the list (section 4) and the same checks (3.2). It is the substitute for a list, not for the root: nothing carries on the root alone. Whether it exists at all is question 1.

## 4. Roles

The rule, in one line: **the role on B is the lowest of what the device has on A, what A may vouch for, and B's carry for A. It is never higher than on A, and there is no hand-over unless both nodes are on the same fleet list or each has accepted the other by fingerprint.**

| Case | On A | On B |
|---|---|---|
| Same fleet list, B's carry is `full` | Owner | Owner, with the credential rule below |
| Same list, B's carry is `live` | Owner | Operator |
| Same list, B's carry is `full` or `live` | Operator | Operator |
| Same list, B's carry is `none` | any | nothing: the PIN screen |
| Node pairing (3.4) | role as above, capped at the carry chosen | as above |
| Not on the same list, not paired | any | nothing: the PIN screen |
| B's accept switch off, or B has no https | any | nothing |
| Guest, or a device paired by a join code or a link | Guest or presenter | nothing, never |
| A device paired from a controller's code (MIDI) | any | nothing, never |
| A remote support login | Owner by role | nothing, never |

Which devices carry: role Owner or Operator, `secure: true`, and a record with no `via`, or `via` equal to `recovery`. Excluded: Guests, `via` `code`, `via` `controller`, `via` `node` (no chaining: C asks A), and every remote support login, whose routes go in `support.REMOTE_DENY` with the reason "support stays at the node it was given to".

**The credential rule (one rule, in the gate).** A device with `via: node` may not create, reveal, rotate or remove any credential at B, nor change who can sign in to B. Concretely it is refused: showing or rotating the PIN, lifting the PIN lockout, join codes, controller codes, invites and guest links, recovery codes and the USB stick settings, the root, the certificate, the key request, the fleet list, node pairings and the accept switch, factory reset, sign out of every device, and **removing or demoting any device that is not itself `via: node`**. Everything else an Owner can do on B (playback, mixing, setup of the show, Rooms, schedules, network and update pages), a carried Owner can do. The rule is one check in `Api.gate`, by the device's `via`, with one list of routes written out by hand beside `policy`, not a list of four exceptions. A test (slice 4c) walks the whole route table, finds every route that reads or writes `Auth` or the settings keys of access, https, fleet, recovery and devices, and asserts each refuses `via: node`. A new route that touches `Auth` and is not on the list fails the test. This is what makes a hand-over end with the lease: a carried Owner cannot turn it into a permanent way in.

B's record is an ordinary device of B (name "device name, via node-01", role as above, `via: node`, `secure: true`, `from: {node, device}`), so People and codes lists it and the Owner at B can remove it. A device that taps Open again on the same B gets the same record (matched by A's node id and device id) with a new token. **At most 8 `via: node` records per source node**: the ninth tap from a different device of the same source replaces nothing and is refused with a sentence, so one node cannot fill B's list. They count in the shared pool beside guests (`_room_for`).

## 5. The flow in the panel

Preconditions for the Open row: B is in A's discovery list with https on; A has https loaded; this device is Owner or Operator, `secure`, not remote; and A has a trust record for B (list or pairing) that passes the check below. If not, Open does what it does today and a line says why in words ("You will be asked for this node's PIN").

1. **Tap.** The panel opens an empty tab at once, named for the node (`window.open('', 'nxlx-' + id)`, inside the tap so no popup blocker objects), sets its `opener` to null, and shows "Signing in to node-02..." in it.
2. **POST to A for the ticket.** `POST /api/handover/ticket` on A: JSON, the request header, same-origin cookie, body `{"to": "<B's id>", "proof": "<hash of a value the page just made>"}`. The panel sends an id, never an address. A checks, in order: the device may carry (section 4); A holds a trust record for B and B's carry is not `none`; **B passes every step of 3.2** (A connects to B, so a ticket is never made toward a host that only announced B's id); and **B's certificate covers the address that A will hand to the browser**, so the browser's own check will pass there too. A throttles tickets (6 a minute per device) and makes a ticket: 128 random bits, kept in memory with the device id, B's id and key hash, the proof hash, and an end time on A's monotonic clock, **60 seconds**. A answers `{"ticket", "action": "https://B:port/handover", "origin": "https://A-origin"}`. The address is the one A just verified.
3. **Form POST to B.** The page builds a form in memory (`method=POST`, `enctype=application/x-www-form-urlencoded`, `action` the answer, `target` the named tab, hidden fields `ticket`, `secret` (the value whose hash was the proof) and `from` (A's node id)) and submits it. The ticket is in a JSON answer over TLS, in the page's memory, and in a POST body over TLS: **never in an address, query string, fragment, history, Referer (`Referrer-Policy: no-referrer`), access log or journal.** The page's content security policy gains `form-action 'self' https://B:port` for each node the Nodes view may open, built on the server from the same list and checked against the shape `CSP_ORIGIN` allows. **A node that appeared after the page loaded** is not in the policy, so the browser would block the form: the panel notices (the list is asked only at a tap, as the switcher already does) and reloads the page once, which carries the new policy, before it shows Open for that node; until then the row says "Reload the panel to open this node".
4. **B redeems with A.** `POST /handover` exists on the TLS listener only (plain http gets 404 with the https address, no redirect). It takes one form, 2 KB at most. It checks that it accepts hand-overs, and that the **`Origin` header of the form POST equals A's https origin** as A returned it (a request with another Origin, or none from a browser that sends one, is refused: this is what stops another site from posting a ticket to B in your browser). It finds A by id (3.2, every address, first that passes), and calls A's `POST /api/handover/redeem` with `{"ticket", "secret", "to": B's id, "key": B's key hash, "serial": ...}`. A does the lookup and the delete in one step **under A's lock**: the ticket is removed whether or not the rest passes, so two calls can never both win. A then checks that the node and key are those it made it for, that the hash of `secret` is the proof, and that the device is still paired, still Owner or Operator and still `secure`. It answers `{"name", "role", "lease", "origin"}`. **B makes the call once.** If it times out, B does not retry: the ticket is probably already spent; the tab shows the PIN screen with the sentence and the user taps Open again (a new ticket). B lowers the role by its own carry, never raises, and makes or refreshes the record.
5. **B answers a page, not a redirect.** B answers 200 with a tiny page and `Set-Cookie: __Host-pvj_token=...; Secure; HttpOnly; SameSite=Strict; Path=/`. The page's one script (a file of B's own) runs `location.replace('/')`. A redirect would not do: the move after a cross-site POST counts as cross-site, so a SameSite=Strict cookie would not be sent. **B never silently replaces a cookie that is already valid at B with one of a lower or other identity**: if the form arrives with a valid B cookie of a higher role (for example an Owner paired at B, tapping Open from a node where he is only an Operator), B does not set the new cookie; it answers a one-line page, "You are already signed in here as Owner. Switch to the Operator from node-01? [Switch] [Keep]", whose button is a same-origin POST. If the cookie is of the same or a lower role, B replaces it quietly.
6. **B's panel opens signed in.** On any failure in steps 2 to 5 the tab ends at B's PIN screen with the node's sentence ("node-02 did not accept the hand-over: the ticket had run out. Use the PIN.") and the failure is logged (section 10). The back button never re-sends the ticket: the history entry was replaced, and the ticket is gone from A anyway.

**The proof.** A binds a ticket to the browser tab that asked for it: A keeps only the hash, the page keeps the value, and B needs both. A ticket that leaks without the value cannot be redeemed. Single use and the 60 seconds are the first guard; the proof is the second.

**The Fire tab launcher and installed apps.** An installed web app on iOS (standalone) may send `window.open` to Safari, whose cookie jar is not the app's: the hand-over then signs in to B in Safari, not in the app. That is how Open behaves today. The locked launcher tablet (D131, branch firetab-log) runs in its own WebView; whether `window.open` works there is a row to try (HO6), and a launcher-locked device would need the ticket route allowed explicitly. This design does not decide that.

## 6. Where the checks live

- New module `pvj/handover.py` (about 300 lines, standard library): the peer client (3.2), the ticket table (memory, at most 64, monotonic end times), the lease table (section 8), the fleet and pairing readers. A short worker thread does the lease checks and sleeps otherwise.
- Routes on the TLS listener only: `POST /api/handover/ticket` (Operator and up, `secure`, not remote), `POST /handover` (no device; a form), `POST /api/handover/redeem` and `POST /api/handover/lease` (peer calls; no cookie; need a ticket or lease that only A and the right B hold), `POST /api/handover/poke` (no authority), `GET /api/fleet/card` (public, the facts mDNS announces plus the key hash), and the Owner routes for the list, pairings and the accept switch. Each gets a row in `policy`, and in `REMOTE_DENY` where a support session could reach it.
- Peer calls: private, link-local and loopback addresses only, 3 seconds to connect, 6 in all. **One outbound call at a time per peer id**, not one for the node, so a slow or dead peer cannot starve a hand-over with another one. Calls to peers are counted as failures or successes for the throttles of section 7.

## 7. What the switches do

- **Accept devices from other nodes** (Owner, at that node, Setup > Nodes on this network). Off by default, kept in `settings.json` as `fleet.accept` (a plain on or off, in `BOX_OWN` so an import never brings it, gone at a factory reset). Off: `POST /handover` answers the PIN screen, "this node does not take devices from other nodes". Turning it off removes every `via: node` device at once. It does not hold the list or the pairings, so those stay.
- **Owner access only over the secure connection** (D79). A hand-over never needs it and never loosens it. Tickets, redemption, the cookie and the move exist over https only. The new device is marked `secure: true` (its token was made over https), so with the switch on B does not refuse it as "paired over plain http". On A, a device paired over plain http does not carry at all, switch or no switch, because its token may have been listened to. The switch protects the start of the chain: the first node pairs you over https.
- **Locking pairing.** I found no switch of that name. The nearest are the PIN guessing lockout (20 wrong PINs in 10 minutes shuts all pairing for a minute, `Auth.pairing_locked`) and the guest-controls lock (D80). Neither affects a hand-over: no PIN is typed, so nothing counts as a guess; the guest lock concerns Guests, who never carry. The lock for this feature is "Accept devices from other nodes". Question 4 asks whether you want a real lock button.
- **Throttles.** A bad ticket, bad secret or bad handle has its own throttle at B and at A: 10 **failures** a minute for the node and 5 a minute per source address. Failures count, attempts do not, so a flood of good hand-overs cannot lock the room out and a flood of bad ones cannot hide among them. Good ticket requests are limited per device (6 a minute) as in section 5. A poke with a handle the node does not know costs a table lookup and nothing else: no call, no log line beyond a counter shown on the status.

## 8. Revocation

A device removed on A: A's record goes, and with it every ticket made for it (a ticket is checked against the device list at redemption). The records already made on other nodes go too, in a time you can say out loud.
- **Lease.** At redemption A gives B a lease handle (128 random bits); B stores its hash in the record. While the device is in use, B asks A `POST /api/handover/lease {handle, serial}` at most every 10 minutes (and at once on a poke): "is this device still paired, and with what role?" Gone: B removes its record (the existing `revoke` path: presence, OSC address included). Role lower: B lowers it. Role higher: ignored (a higher role needs a new hand-over).
- **Poke.** When A removes a device it knows it handed over, it sends each such node `POST /api/handover/poke {handle}`. The poke has no authority: B reacts only by asking A, so a forged poke can cause one extra question. A keeps which nodes each device was handed to **in memory** and writes it to the device record **at most once a day** (like `seen`), so a restart can lose a day of that set; the lease check covers the gap within 10 minutes of use.
- **A switched off or unreachable.** B keeps the device until **7 days** after the last good answer from A, or until B's Owner removes it. The answer is the only thing that renews it. A hand-over refreshes the record. The real limit on a stolen A is not these 7 days but the fleet list: section 9.
- **Everything removed** (sign out every device, factory reset): the next lease check on each node removes the hand-over devices.
- **A node removed from the list or pairing**: its devices fail their next check and go within 10 minutes of use (the peer no longer passes 3.2 step 5).
- **Role change on A**: the lease answer carries it; B follows it downward at the next check.
- **Log out** in B's panel removes B's record only; one more tap on Open signs in again.

## 9. A node leaves the fleet (a runbook)

A node that is stolen, retired, lent back or re-imaged still has a valid key and a certificate from your root. Dates do not save you (the Pi's clock is not trusted, and certificates last a year), so the way out is the list. Do these in order, on your computer and then at each remaining node:
1. `python3 tools/boxcert.py fleet remove <name>` then `fleet sign`. The serial goes up. If you use node pairings instead of a list, remove the pairing at each remaining node (one tap on its Nodes page).
2. Load the new list on one remaining node (Setup > Nodes on this network); the others take it when they hear its serial (if you allowed that, question 5) or you load it on each.
3. From that moment every remaining node refuses the removed one as a peer (key not on the list) and refuses to hand over to it. Its `via: node` devices on the remaining nodes lapse at their next lease check, 10 minutes of use at most. Devices of the removed node's own that were already signed in elsewhere go the same way.
4. The removed node keeps its own devices and its own PIN; it simply cannot sign anyone in on your other nodes, and your other nodes' devices cannot be signed into it. If it was **stolen**, also follow D79 ("If a box or its SD card is stolen"): a new key on the remaining nodes if the name is still in use, and a different host name for any replacement. If it was **re-imaged**, it gets a new node id and a new key: add it as a new node with `fleet add` and sign again.
5. A node that is **offline** when the new list is signed learns it when it next hears a higher serial from a peer, and until then it still trusts the old list. The stale node's only power is over itself and over nodes that have not heard the new serial; load the list there by hand when it is back.
6. When `clock_trusted()` is true, a peer certificate that has run out is refused and logged too (3.2 step 6), so a forgotten retired node falls out of the fleet when its certificate ends even if you forgot the list.

## 10. The event log and the journal

A new kind `handover` (the `KINDS` tuple is the only change to the events module), plain sentences, `who` the device's name:
- On A, when a ticket is redeemed: "Anthony iPad (Owner) was signed in to node-02." On a refusal at A: "A ticket for node-02 was not made: node-02's key is not the one on the fleet list."
- On B, on success: "Anthony iPad (Owner) signed in from node-01." On refusal: "A hand-over from node-01 was refused: the ticket had ended. / ... node-01's key is not on the fleet list. / ... this node does not take devices from other nodes. / ... node-01 could not be reached. / ... node-01's certificate ended on <date>. / ... node-01 has an older fleet list."
- On removal at B by lease: "Anthony iPad was signed out: node-01 no longer has it paired." On the 7 days: "... node-01 has not answered for 7 days."
- A fleet list or pairing loaded, adopted from a peer, or removed: "The fleet list (serial 4, 3 nodes) was loaded / taken from node-01."

The journal gets the same sentences with the device id, never a ticket, handle, secret, token, key or whole certificate. Repeats within 5 minutes collapse as the log does. The two ends write separately; the pair of lines, one on each node, is the trail. It is not a security audit trail (D125).

## 11. Threats

| Threat | What stops it |
|---|---|
| A stolen ticket (proxy, history, screenshot) | 60 seconds, used once, useless without the `secret` only the page holds, never in an address. Dead at A the moment it is used. |
| Replay (second POST, back button, double tap) | A looks the ticket up and deletes it in one step under its lock, whether or not the rest passes. B never retries after a timeout. The back button goes to a replaced history entry. |
| A rogue LAN host posing as B or A | It has no certificate from your root, so it fails step 2 of 3.2; if it somehow had one, its key hash is not the one on the list for that id (step 5), and its certificate must cover the address (step 4). A ticket is made only toward a verified B, and the form goes only to the address A verified. |
| A stolen, retired or lent node (valid key and certificate) | There is no lower tier: the root alone carries nothing. It is out the moment its key leaves the list (section 9) and, when the clock is trusted, when its certificate ends. Until you sign a new list, it can pose as one of your nodes to nodes that have not heard the new serial. Question 6 asks whether you will follow the procedure. |
| A node posing as B to teach users to type B's PIN into it | A hand-over to it needs a key hash on the list or pairing; and the PIN screen is B's own page: the user reaches it by the address the browser verified. A rogue host with no certificate of yours shows a full-page browser warning, as today. |
| A node whose certificate changed | Same key under a renewed certificate: nothing changes. A new key: the list does not match; hand-overs to and from it are refused with a sentence until you sign again. No trust on first use, no "accept this new key" prompt. |
| A compromised node | It has its key and certificate (D79's stolen card). It can make tickets and lease answers only for its own devices, up to the `carry` of each node that lists it. A compromised A can claim "Owner" and B gives at most its carry, and the credential rule (section 4) stops it making a permanent way in. Venue nodes should say `live` or `none`. A compromised B learns the name and role of a device that taps Open on it, and gets no token for any other node. |
| A carried Owner turning the lease into permanent access | The credential rule in the gate and its route-walking test (slice 4c). It cannot show or rotate the PIN, make a code, link or invite, remove or demote the local Owner, or change the list. |
| Downgrade to http | The routes exist on the TLS listener only. The cookie is `__Host-` and Secure. A device paired over http does not carry. A node without https offers no hand-over. No redirect from http to https. |
| CSRF on the ticket request | JSON only, the `X-PVJ-Request` header, an `Origin` equal to `Host`, SameSite=Strict cookie. |
| Login CSRF on the form (a site makes your browser post an attacker's ticket and secret) | The form's `Origin` must equal A's https origin as A returned it in the redeem answer. The attacker also needs your browser to hold his own valid ticket and secret. B never overwrites an existing valid cookie of a higher role without a confirm page (section 5, step 5). B's People list names the device. |
| Clock skew (no battery clock) | No decision uses a wall clock. Ticket life and lease checks use each node's monotonic clock; the list uses a serial; dates are judged only when `clock_trusted()` and only to refuse a run-out certificate. |
| Guessing a ticket or a handle | 128 bits each; failures throttled (section 7). |
| A flood of redeem calls used against A | Peer calls need a ticket first, run one at a time per peer id, 6 in all a minute, and go only to addresses in B's own discovery list. |
| OSC and MIDI | A carried Owner device at B has an address, so it passes OSC's "sender must be a paired device" rule (D78) while it is signed in, like any Owner device, and `forget_address` ends it with the lease. MIDI controllers and their codes are not devices and never carry. OSC feedback targets (D82) are per node and not carried. |
| A support helper | Tickets, redemption, the list and the pairings are denied to remote support; a support login does not carry; B refuses `/handover` through the tunnel. |
| What "Owner only over https" does | Section 7: not loosened, not needed, and it makes the chain start on https. |
| Key reuse | The node's TLS key is used for TLS only. Nothing is signed with it. |
| Hostile Wi-Fi | The bytes are TLS. Client isolation stops the call between nodes and the tap falls back to the PIN. |

## 12. Slices, tests and the nodes

Small pull requests, in order; each ends green and changes nothing visible until the last two.

**Slice 1: the fleet list and its tool.** `tools/boxcert.py fleet add/remove/sign/show`, the file format, the signed bytes (domain prefix, fleet id, serial), `GET /api/fleet/card`. Tests: a list signs and verifies; one changed byte, a changed fleet id or a signature from another prefix fails; a lower serial is refused; the card matches the certificate's key hash; a wrong passphrase writes nothing; the card route answers no secret.
**Slice 2: a node stores and loads the list (step 2c), and node pairing.** `pvj/handover.py` readers; the list stored as `fleet.json` and `fleet.sig` beside `tls/` (0640, rename), never in `settings.json`; `POST /api/fleet/list` (Owner, https, device paired at this node); the pairing record (3.4) with its fingerprint screen; status in Setup > Nodes on this network; the `handover` event kind; the `fleet.accept` switch (off). Tests: the signature is checked against the pinned root; a list not naming this node is refused; serial order; the switch is kept, not exported, cleared at factory reset; **the list and the pairings appear in no GET, no diagnostics file and no settings export or import (a walk like D79's)**; a pairing needs the fingerprint typed back.
**Slice 3: the peer channel.** The verified TLS connect, every address of an id, the seven checks of 3.2, one call in flight per peer id, the serial in each call and the adoption of a higher list. Tests against a local TLS server with certificates from the test's own root: good peer; another root; right root and wrong key hash; SAN that does not cover the address (`name_matches`); an address that is not private; slow peer (timeout); a peer with two addresses where the first is dead; a lower serial refused; a higher serial adopted after verification; a run-out certificate refused only when the clock is trusted; the system time moved a day with the dates ignored otherwise.
**Slice 4a: ticket, redeem, record.** The ticket table, `ticket` and `redeem` routes, `POST /handover` with the Origin check and the higher-cookie confirm page, the `via: node` record, caps, the 8 per source node, throttles on failures. Tests: every row of the role table; the ticket ends at 60 s (a fake monotonic clock); used once under concurrency (two threads, one winner); wrong node, wrong key, wrong secret; device removed or demoted between ticket and redeem; a second tap refreshes the record; the ninth device of one source is refused; no retry after a timeout; Origin wrong or absent refused; the confirm page; no secret in any log line, API answer or the diagnostics file; remote support gets 403.
**Slice 4b: lease, poke, lapse and the switch-off sweep.** The lease table, the 10 minute check, the poke, the 7 day lapse, `fleet.accept` off removing the records, a node leaving the list. Tests: revoke on A removes at B after a poke and after a lease; lower role followed, higher ignored; A down for 7 days (fake clock); the handed-to set kept in memory and written at most once a day; an unknown handle costs a lookup (no outbound call, no event); the sweep.
**Slice 4c: the gate rule and its test.** The credential rule in `Api.gate` and the route list beside `policy`. A policy-walking test: for every route in the table and every path answered outside it, if the handler or what it calls touches `Auth` (devices, tokens, PIN, codes, recovery, join) or the settings keys for access, https, fleet or devices, a `via: node` Owner gets 403; a new route that does so and is not on the list fails the test.
**Slice 5: the panel.** The Open flow, the named tab, the form, `form-action` in `page_csp` for the nodes the list shows, the reload for a node that is new, the sentences. `panel.test.js` with a fake pair of nodes: one request to A and one form to B; no ticket in any address; opener null; fallbacks and their sentences; nothing asked before the tap; the old line for a node that cannot carry.
**Slice 6: review, hardware rows, a short soak.** An independent review of slices 3 to 5, then 200 hand-overs between node-01 and node-02 with a lease check every minute for an hour.

**Hardware rows** for `tools/DEVICE-TESTING.md`, section "Hand-over between nodes". node-01 is the Pi 4, node-02 the Pi 3B. Both have a certificate from your root.

| ID | Do | Pass | Fail |
|---|---|---|---|
| HO1 | Sign and load a fleet list naming node-01 and node-02 with carry `full`; open Setup on each | Both show "Fleet list loaded", the same fleet id and serial 1 | Either refuses, or shows a node not named |
| HO2 | On node-01 paired as Owner over https, Nodes, Open node-02 | node-02 opens signed in as Owner; People lists "device, via node-01"; both event logs have a line | The PIN screen, or a second record on a second tap |
| HO3 | The same, node-02 to node-01 | The same; the time from tap to panel is written down | Over 8 seconds, or a leftover socket on node-01 |
| HO4 | Remove the device on node-01; within 2 minutes use node-02 | node-02 signs it out; the event says why | Still works after 10 minutes of use |
| HO5 | Power off node-01; keep using node-02 | The device keeps working; with the lapse set to 2 minutes in a test build it signs out after that | Signed out at once, or never |
| HO6 | The Open flow in: Safari on iPhone, Safari and Chrome on the Mac, Chrome on Android, **Firefox on a laptop**, **the Fire launcher's WebView**, and **the panel saved as an app on iOS (standalone)** | Lands on node-02 signed in, no PIN screen, back does not re-ask. For the iOS standalone app: write down where it lands. **The known trap: `window.open` goes to Safari, so the cookie is set in Safari's jar and the app stays on node-01**; the row passes if the page says so in one sentence | Any browser shows the PIN screen after the move, or the iOS app claims success it does not have |
| HO7 | Switch "Accept devices from other nodes" off on node-02; tap Open | PIN screen with the sentence; no new device | A signed-in panel |
| HO8 | Remove node-02 from the list (serial 2) on node-01 only | Open on node-01 offers the PIN note and makes no ticket | A ticket is made |
| HO9 | Stop node-02 from taking network time and set its clock a day wrong | Hand-over still works both ways | Refused for the date |
| HO10 | Take node-01 and node-02 off the list but leave both with the root; tap Open | The PIN note, no ticket: the root alone carries nothing | A ticket, or a sign-in |
| HO11 | A guest link device and a presenter code device tap Open | The PIN note; no ticket route called | A ticket is made |
| HO12 | A laptop with no root announcing node-02's id (`avahi-publish`) | node-01 will not make a ticket; the row says it cannot confirm that node | A ticket or a posted form |
| HO13 | Renew node-01's certificate with the same key, then with a new key | Same key: nothing changes. New key: refused until the list is signed again, with the sentence | The new key accepted |
| HO14 | A carried Owner on node-02 tries: show the PIN, make a guest link, remove the local Owner | All three refused with the sentence | Any works |
| HO15 | A node in two addresses (Ethernet and Wi-Fi) with the Ethernet unplugged | Hand-over still works through the other address | Refused as unreachable |
| HO16 | Node pairing by fingerprint between two nodes with no list | Fingerprint shown on both panels; after node-02's Owner accepts, node-01's devices carry; removing the pairing signs them out within 10 minutes of use | Carry before accepting, or after removing |
| HO17 | The one-hour soak of slice 6 | No growth in sockets, threads or memory; no frame drops on node-01 during a hand-over while a clip plays | A visible stutter |

## 13. Questions for Anthony

1. **Who may trust whom?** A node's certificate from your root is not proof that the node is still yours: a stolen, lent or retired node still has one, so if the root alone were enough, such a node could sign people in everywhere. Choose: (a) carry only between nodes on a list you sign on your computer; (b) as (a), and also let a node's Owner accept one other node once, by its key fingerprint shown on both panels, for a rig with no list; (c) list only, no pairing by fingerprint. I recommend (b): the list is the way for your rig, the fingerprint is the way for a friend's node.
2. **What may a carried Owner do on the other node?** (a) Anything except create, show or remove a way into that node: PIN, codes, guest links, other devices, root, certificate, list, switches, factory reset. (b) Act as an Operator plus read-only Setup. (c) Everything. I recommend (a). With (c), a hand-over could be turned into permanent Owner access.
3. **How long may a carried device stay signed in when the node it came from is off?** (a) 7 days from the last answer; (b) 24 hours; (c) until someone removes it. I recommend (a). The real limit on a stolen node is not this time but the list: you sign a new one without it.
4. **A lock button?** I found no switch called lock pairing. Today the lock for this feature is "Accept devices from other nodes", off until you switch it on at that node. (a) That is enough. (b) Add a Room button that stops PIN pairing, join codes and hand-overs together. I recommend (a) for now.
5. **May nodes pass a newer signed list to each other, so you load it once?** (a) Yes: a node that hears a higher serial takes the list from its peer after checking your signature; a stolen node cannot forge one, and an old list never replaces a newer one. (b) No: you load the list on each node. I recommend (a).
6. **When a node is stolen or retired, will you really follow "sign a new list and load it" (section 9)?** It is the only thing that removes it; certificate dates do not, because the Pi's clock cannot be trusted. (a) Yes. (b) I want a button on the node itself that removes a node from the fleet, even if it is only a stop-gap until I sign. I recommend (a), with the runbook in Setup so it is one page away.

## 14. What this does not change

PIN pairing stays the way into a node with nothing else; roles, the guest lock and the join codes are as before; discovery (D127) and the switcher keep working, and the switcher's rule "never carry this node's token or PIN" becomes "never carry a token or PIN, only a one-time ticket that this node made for that node". No package is installed, no root key goes on a node, and no setting is carried from one node to another: that is step 3, not approved.

## 15. Not checked yet

- Whether Safari (iOS and macOS) accepts a Set-Cookie on the response of a cross-site form POST to a `.local` name and then sends it on the same-site move by the page (HO6). Chrome and Firefox: expected, not tried.
- Whether `form-action` blocks the post in any browser that applies it to a tab named by `target`. The form is built on the same page, so the policy applies as for a normal link.
- Whether the form's `Origin` header is sent on a cross-site POST by every browser in HO6 (the spec says yes; Safari with a strict referrer policy is the one to watch). If a browser sends `Origin: null`, B falls back to the PIN screen for that browser and the sentence says so.
- The time a Pi 3B needs for the TLS connection to the Pi 4 plus an `openssl verify` (the figure for HO3 is the first measurement).
- That `window.open('', name)` followed by `opener = null` works in the Fire launcher's WebView.
