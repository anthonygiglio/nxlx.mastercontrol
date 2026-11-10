<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# A secure connection to the box (HTTPS with your own root certificate)

Decision D79. **Nothing on this page has run on a real box or met a real phone yet**: it was built and tested on the development Mac and in CI. The rows in `tools/DEVICE-TESTING.md` ("Secure connection") are the first real try.

## What it is

The box can answer on `https://` as well as on `http://`. A browser believes an `https://` address only when the box shows a certificate signed by something the device already trusts. Public authorities do not sign certificates for `.local` names or private addresses, so you sign them yourself:

- **One root certificate of your own**, made once on your computer with `tools/boxcert.py`. Its private key is a file plus a passphrase. It never goes on a box.
- **One certificate per box**, signed by that root for the names and addresses the box is reached by. The box makes its own private key and a *request*; you sign the request with the tool and upload the certificate. Renew it about once a year.
- **Each of your devices installs the root once** and then trusts every box you ever add.
- **Guests stay on `http://`.** They never install anything. A device without the root that opens `https://` sees a full-page warning, so the panel never redirects anybody by itself.
- **The switch "Owner access only over the secure connection"** keeps the PIN and the owner's sessions off the clear once you have the root on your devices. Guests and presenters by code keep working over `http://`.

## Facts this was built on

Verified on 2026-10-10 unless marked. "Unconfirmed" means no vendor page was found that says it; the design does not depend on it.

**Apple's requirements for a TLS server certificate** (support.apple.com/103769, published 2023-11-06): RSA keys of 2048 bits or more, a SHA-2 hash in the signature, the server's name in the Subject Alternative Name, an `ExtendedKeyUsage` holding `serverAuth`, and a validity of 825 days or fewer. The page says "all TLS server certificates" and does not limit itself to Apple's own roots. ECDSA is accepted too (the page's key rule is about RSA sizes; Apple's platform security guide lists ECDSA, not re-read today, so: unconfirmed in those words). The tool makes ECDSA P-256 keys for boxes and the root is ECDSA P-384.

**Apple's 398-day limit does not apply here** (support.apple.com/102028, published 2023-08-21): certificates issued from 2020-09-01 "must not have a validity period greater than 398 days", and "this change will not affect certificates issued from user-added or administrator-added Root CAs". So a box certificate from your root may last up to 825 days on Apple devices. The tool's default is 397 days anyway (a yearly renewal with a month of slack); `--days` allows up to 825.

**iPhone and iPad** (support.apple.com/102390, published 2025-03-26): after a certificate profile is installed, trust is a second step: Settings > General > About > Certificate Trust Settings, then turn on the certificate under "Enable full trust for root certificates". The option appears only after a certificate was installed. Installing the profile itself (Safari downloads it, then Settings shows "Profile Downloaded" near the top, or under General > VPN & Device Management) is from Apple's device-management documentation and common experience, not re-read today: unconfirmed in those words. Without the trust step Safari shows "This Connection Is Not Private".

**Android**: Chrome on Android trusts a certificate authority the user installed (Chromium's network security configuration lists `<certificates src="user"/>`, read 2026-10-10 on `webapp-install`); most other apps do not since Android 7 (Android Developers Blog, 2016-07-07). The menu is Settings > Security (or Security & privacy) > More security settings (or Advanced) > Encryption & credentials > Install a certificate > CA certificate; Android then warns that "your data won't be private" and asks for the screen lock; the words differ by maker and version (vendor guides, no Google page found: unconfirmed in those words). A notice "Network may be monitored" can stay in the notification shade on some versions.

**macOS**: Keychain Access, File > Import Items (or double-click the file), then open the certificate, unfold Trust and set "When using this certificate" to Always Trust; the system asks for the account password. Safari and Chrome read the keychain; Chrome reads it when it starts, so restart Chrome. Firefox keeps its own list and, on macOS and Windows, imports the system's added roots only when `security.enterprise_roots.enabled` is on (it is on by default in current Firefox: unconfirmed today). (Vendor guides and Apple's Keychain Access guide, not re-read today.)

**Windows**: double-click the `.crt` file > Install Certificate > Current User > "Place all certificates in the following store" > Browse > Trusted Root Certification Authorities; Windows asks "Do you want to install this certificate?" with the root's thumbprint. Edge and Chrome use the Windows store. Firefox as above. (Vendor guides; Microsoft Learn describes the store, not the dialog: unconfirmed in those words.)

**ChromeOS**: Chrome settings > Privacy and security > Security > Manage certificates > Authorities > Import, tick "Trust this certificate for identifying websites". (Institutional guides; no Google page found for the user-level path: unconfirmed.)

**Linux**: Chrome and Chromium use the NSS database of the user (`certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n nxlx -i root.pem`) and Firefox its own profile store (Settings > Privacy & Security > Certificates > View Certificates > Authorities > Import); the system store (`/usr/local/share/ca-certificates/` and `update-ca-certificates` on Debian) is read by curl and Python, and by Chrome on some distributions. Standard tooling, not re-verified today.

**Name constraints.** RFC 5280 says a validator must process the extension when it is marked critical, and must reject a certificate with a critical extension it does not understand. Apple added support in iOS 9 and macOS 10.12 (Apple's own statement in a CA/Browser Forum thread). Chrome enforces constraints on user-added roots on every platform since Chrome 112 (a 2023 report; before that Linux did not). Firefox (NSS) and Android (BoringSSL and Conscrypt) enforce them (long-standing, not re-verified today). No source found says a current platform rejects a constrained root as such. So the root carries constraints (see D79); `make-root --no-constraints` exists in case a device of yours does not accept it.

**`.local` names and bare addresses in the certificate.** A browser matches the name in the address bar against the DNS names in the Subject Alternative Name, and an address typed in the bar against the IP entries there (RFC 6125 and RFC 5280 behaviour; every major browser does this). `.local` is a name like any other to the browser; it is public authorities that refuse to issue for it, which is the reason for an own root. An address in the certificate stops matching when the box gets another address: put the names you really type into the request, and the `.local` name first.

**HSTS** is not sent. A browser that once saw `Strict-Transport-Security` for the name refuses `http://` for it until the time runs out; guests have only `http://`, so one owner visit would lock every guest out on that browser. The page over `http://` instead checks whether the device trusts the box (one small fetch of `https://<the same host>/api/https/probe`, which answers `{"https": true}` with no CORS header, so the page learns only whether the connection was made) and offers the link; it never redirects by itself. The page's content security policy is `connect-src 'self'`, which does not cover that fetch on its own: `'self'` matching the `https://` form of the same host is a CSP level 3 rule for the default ports only, not verified for Safari on iOS, and in CI the ports are not the default ones. So, while a certificate is in use, the box names its own `https://` origin in the policy of the HTML page it serves over `http://`: exactly one origin, built from the matched name in the Host header (a clean DNS name or address that the certificate carries, lower case, nothing copied from the header) and the TLS listener's port, never from the header's port. A Host the certificate does not carry gets the plain policy, and the page then says so without asking: no device trusts the box by that name. Over `https://` nothing is added, `'self'` is that origin already.

**The box's clock.** A Raspberry Pi has no battery clock. The visiting device, not the box, judges whether a certificate is in its dates, so a wrong box clock does not break the connection. The box judges "has run out" only when its clock is set from the network; otherwise the page says the date is as the box counts it and the upload is not refused for it.

## One-time set-up, in order

You need: a computer with Python 3 and `openssl` (a Mac has both; Linux has both; on Windows install OpenSSL or use Git for Windows' `openssl`), and the box reachable in a browser with full access.

1. **Make the root** (once, ever):
   `python3 tools/boxcert.py make-root`
   It asks for a passphrase twice, writes `~/nxlx-root-ca/root.key` (encrypted) and `~/nxlx-root-ca/root.pem` (public), and refuses to run again on a folder that has them. Back the folder up now (a password manager's file attachment, an encrypted USB stick), and remember the passphrase: without it the key is useless and you start over. Never copy `root.key` to a box.
2. **Ask the box for a request.** Open the panel, System > Secure connection. Check the names shown (the `.local` name, the host name and the box's current addresses), add any other name you type into the browser for this box, and press "Make the request". Press "Download the request": a file `nxlx-mastercontrol.csr`.
3. **Sign it:**
   `python3 tools/boxcert.py sign ~/Downloads/nxlx-mastercontrol.csr`
   It shows the names from the request, asks for the passphrase, and writes `~/nxlx-root-ca/nxlx-mastercontrol-cert.pem` (the box's certificate followed by the root). `--name` adds a name, `--address` an address, `--days` sets the length (397 by default, 825 at most).
4. **Upload it.** On the same page, "Upload the certificate", choose that file. The page says what the certificate names and when it ends. If the box refuses it, the message says why (wrong key, wrong names, run out, the root uploaded by mistake).
   The first certificate file also gives the box your root, which it then **pins**: from then on it accepts only certificates issued by that root, and the root on the box is replaced only over `https://`, by a device paired over `https://`, through "Replace the root..." on the same page, with the old and the new fingerprint shown. Compare the fingerprint once: the page shows "Root this box trusts, SHA-256: ..." and `python3 tools/boxcert.py root` prints the same on your computer. They must be the same.
5. **Install the root on this device.** Still over `http://`, press "Download the root certificate" and follow the steps the page shows for the device in your hand (they are the ones above). Then press "Does this device trust the box?": it should say yes.
6. **Move to https://** with the link on the page, and **pair again** (the `https://` address is a new origin to the browser, so the old session is not there; the PIN is under People and codes on the `http://` panel, or on the box's display).
7. **Switch on "Owner access only over the secure connection"** on the same page, now over `https://`. From then on the PIN is refused over `http://`, owner devices paired over `http://` are told to pair again over `https://`, and guests notice nothing.
8. Repeat step 5 and 6 on each of your other devices. Save the `https://` panel as an app where you had the `http://` one.

## Adding another device of yours later

With the switch "Owner access only over the secure connection" on, a new phone of yours cannot pair over `http://` and cannot download the root from the box (the download needs an owner session, which it has not got yet). Give it the root from your computer instead: `~/nxlx-root-ca/root.pem` (AirDrop it, or mail it to yourself and open the attachment), install it as the steps above say, then open `https://nxlx-mastercontrol.local/` and pair with the PIN there. Or switch the owner-only setting off for a minute from a device that already has https://, and do it the ordinary way.

## Adding another box

Steps 2 to 4 for the new box with the same root; its certificate is `~/nxlx-root-ca/<its host name>-cert.pem`. Nothing to do on your devices: they trust the root already. `python3 tools/boxcert.py list` shows every box signed, with its end date.

## Letting your nodes open each other (the fleet list)

Decision D132, design in `design/node-handover_2026-11-09.md`. **Built so far: the list and its tool, and a node that can store the list, ask who it trusts and give its card. Nothing opens without a PIN yet.** A node does not call another node, make a ticket or sign anyone in. Later parts do that, and let an Owner paired on one node open another without pairing again.

What a node does now (the Owner's, over `https://` only; there is no panel screen for it yet):

- `GET /api/fleet/card` (anyone, `https://` only): the node's id, its name and the hash of its public key. Nothing secret. `boxcert.py fleet add node-01.local` asks for it. **It is given only while "Discovery of other nodes" is on** (a node that announces nothing gives its id and name to nobody): with discovery off the answer is "not found", and you add the node with `--cert` and `--id` instead, or switch discovery on for a minute.
- `POST /api/fleet/list` takes `fleet.json` and `fleet.sig`. From your computer: `python3 tools/boxcert.py fleet push node-01.local`. It checks the node's certificate against your root first, then asks for the node's PIN on the prompt (never on the command line), pairs as an Owner, sends the list and logs that device out again. `--token-file FILE` uses the token of an Owner device instead. Nothing is kept on your computer. The node checks them against the root it pins and keeps them beside `tls/` (`fleet.json`, `fleet.sig`, mode 0640). It refuses a list that is not signed by its root, is of another fleet, is older than the one it holds, or does not name this node with this node's key. A newer list replaces the old one. If the files on disk do not check out when the node starts (the root is briefly missing, or the node's key was renewed), the node still remembers the fleet and the highest serial they show, and refuses a list of another fleet or one below that serial. A newer list on disk that does not check out is never replaced by the older pair: the node approves nobody through the list until a good list is loaded. To start a different fleet on a node, factory reset it.
- `POST /api/fleet/accepted` accepts one other node by the fingerprint of its key, for two nodes with no list. Send the node's id, the carry and the fingerprint typed back from THAT node's own panel. The node fetches the other node's card over a connection it checks itself (a certificate from your root, covering the address, matching the card) and takes the name and the key from there; nobody else supplies them, so a lying card cannot confirm itself. Nothing is stored unless the typed fingerprint matches that key. `POST /api/fleet/accepted/look` shows the name and fingerprint to compare. `POST /api/fleet/accepted/remove` takes an acceptance away.
- **Nodes talk to each other** (`POST /api/fleet/peer`, `https://` only, no token). A node reaches another by trying every address its Nodes list holds for it and uses the first whose certificate chains to the root it pins, covers that address, has the key the list or acceptance gives for that node and, when the node's clock was set from the network, has not run out. The root alone is never enough. Each call carries the caller's list serial: a node with a lower serial is refused, a higher one sends its list, which is checked like any list before it is taken. One call at a time per node, 3 seconds to connect and 6 in all, and failed calls (not good ones) count toward 10 a minute for the node and 5 for each address. Removals spread by asking: a node takes the removals an approved node reports, never an allow. A call from a node that is not approved is answered "no" and counted, nothing more. `POST /api/fleet/sync` makes a node say hello to every node it approves.
- A list on disk that could not be checked when the node started (the root or the certificate was not there yet) is read again by itself once the root or the certificate appears.
- `POST /api/fleet/accept` is the switch "Accept devices from other nodes". It is off by default, only an Owner changes it, it is this node's own (never in a settings export, and a file cannot set it) and a factory reset clears it.
- `GET /api/fleet` shows the Owner the list's names and serial, the nodes accepted, this node's own fingerprint and the switch. It never shows another node's key or the signature.
- The list, the acceptances and the deny file (`deny.json`, which later parts fill) are not in any other answer, in the diagnostics file or in a settings export, and a factory reset removes them. A node counts as approved only when it is on the held list with its id **and** its key, or accepted by its fingerprint, and its key is not on the deny list.

The idea in plain words: you decide, once, on your own computer, which nodes may take each other's devices. You write that down in a **fleet list** and sign it with your root. A node will believe the list only if your root's signature on it holds. Having a certificate from your root is not enough for a node to be trusted this way, because a stolen or lent node still has one. The root key stays on your computer; it is used only to sign the list.

**Make and sign a list** (the working copy and the signed files sit in the root's folder, `~/nxlx-root-ca`; back them up with it):

1. `python3 tools/boxcert.py fleet new --name "My rig"` starts a list with a fleet id of its own. It is made once.
2. For each node: `python3 tools/boxcert.py fleet add node-01.local --carry full`. The tool asks the node for its card (its id, its name and a hash of its public key) over https and trusts only your root while doing it, so a node showing someone else's certificate is refused and nothing is added. Nodes do not give the card yet; until a later release does, use the certificate you signed for that node: `fleet add --cert ~/nxlx-root-ca/node-01-cert.pem --id <the node's id> --name node-01 --carry full` (the id is on the node's panel and in the Nodes list), or `--card FILE` with a card you saved.
3. `--carry` is required, and it is the highest role this node gives to a device that arrives from another node on the list: `full` (an Owner stays an Owner there), `live` (at most an Operator) or `none` (this node takes nobody in). Your own rig can say `full`; a venue's node should say `live` or `none`. Run `fleet add` again for the same node to change its carry.
4. `python3 tools/boxcert.py fleet sign` shows what it will sign, asks for the root's passphrase, raises the serial by one and writes `fleet.json` and `fleet.sig`. A wrong passphrase writes nothing. `--serial N` sets a higher serial than the next one; a serial never goes down.
5. `python3 tools/boxcert.py fleet show` shows the signed list and what the working copy would change. `fleet verify` checks the signature again; `fleet verify --list FILE --sig FILE` checks a copy taken from somewhere else.
6. To take a node off: `fleet remove node-01`, then `fleet sign`. Until you sign, nodes keep trusting the list they hold.

**Things that matter**

- Do not edit `fleet.json` by hand and copy it as it is (as a binary file, not as text that an editor or a mail program may change). The signature covers every byte; one changed byte and a node refuses it.
- A node's key is part of the list. A renewed certificate that keeps the node's key keeps working. If the node made a new key, `fleet add ... --replace-key` and sign again; the tool refuses to swap a key by accident.
- A list for one fleet is never taken by a node of another fleet, and a node never goes back to a lower serial. Dates in the list are for you; nothing judges them, because a Pi has no battery clock.
- **If a node is stolen or lost**, taking it off a list is not the cure by itself. A later release adds "Remove a node from this cluster" on every node's panel. `fleet clear NODE` (or a key hash) is the other half: it puts the node's key into the next signed list as allowed again, for the case where a node was wrongly removed. It only has an effect once nodes read lists.
- The tool needs `openssl` and, for the fleet commands only, the file `pvj/fleetlist.py` of the same checkout: the tool and the nodes read a list with the same code. The first test on macOS (LibreSSL) and Windows has not been done.

**For builders.** `fleet.json` is JSON (version 1: `fleet`, `name`, `serial`, `issued`, `nodes` with `id`, `name`, `key`, `carry`, and `clear` when it is not empty). The signature is the root key's SHA-256 signature over `"nxlx-fleet-v1" + newline + fleet id + newline + serial in decimal + newline + the exact bytes of fleet.json`, base64 in `fleet.sig`. `key` is the SHA-256 of the DER SubjectPublicKeyInfo of the node's certificate. A list holds up to 64 nodes and 256 clears and is at most 64 KiB. `pvj/fleetlist.py` parses it strictly (unknown fields, repeated fields, repeated ids, keys or names, bad carry values and oversized files are refused even when signed), verifies the signature with `openssl dgst -verify`, and compares the fleet id and the serial.

## Moving the signing key to another computer

Copy the folder `~/nxlx-root-ca` (all of it: `root.key`, `root.pem`, `signed.json`) to the other computer, where Python 3 and `openssl` exist, and use `--dir` if you put it elsewhere. The key is the file plus the passphrase; nothing ties it to a machine. Keep one copy that is not on a computer you carry around.

## When a certificate has run out

The page warns from 30 days before the end and says plainly after it. Devices then show the full-page warning on `https://`. Open the box over `http://` on a device that is **already paired as owner** (one you paired over `http://` before the switch, or a guest device will not do): once the certificate has run out by the box's clock (when that clock is set from the network), System > Secure connection lets that device download the request and upload a new certificate over `http://`, and nothing else. The PIN is still refused there, pairing is still refused, and every other owner route too: the box's clock can be moved from the network by whoever answers its time requests, so a certificate that "ran out" is not proof of anything, and the switch keeps its promise. Press "Download the request" (the same key, the same request), sign it again (`renew` is `sign`), upload the new certificate. Nothing changes on your devices.

If every device of yours was paired over `https://` and the certificate has run out, there is no paired owner device on `http://`. Two ways back: open `https://nxlx-mastercontrol.local/` anyway and click through the browser's warning (there is no HSTS, so the browser allows it; the connection is still encrypted, only the date is wrong), and renew from there; or at the box, `sudo rm -r /var/lib/pvj/tls && sudo systemctl restart pvj-web` takes HTTPS away and with it the switch's effect, `sudo pvj-pin` prints the PIN, and you start again at step 2. The same console road serves when the box's clock is not set from the network and cannot tell that the certificate has run out.

Two neighbours of that case: a certificate **removed** while the switch is on stops it biting altogether (nothing is left to reach the owner by, and removing it needs an owner over `https://`); a box whose **address changed** does not (the `.local` name is always in the certificate and keeps working; an address you type that the certificate does not carry is refused by the device, so use the `.local` name, or make a new request with the new address in it from `https://`).

## If the root key is lost

Nothing can be signed any more, and the boxes' certificates run out one by one. Make a new root (`make-root --dir ~/nxlx-root-ca-2`); on each box, over `https://`, "Replace the root..." with the new `root.pem` (the box pins its root, so a certificate from the new one is refused until then); sign every box again and upload; install the new root on every device, and remove the old root from each device (the same place you installed it). The old root cannot hurt anybody if nobody has its key.

## If the root key may have been stolen

Whoever has the file and the passphrase can sign a certificate that your devices will trust, for any `.local` name or private address (the root's constraints), so on a network they control they could stand in for a box. Do the three things above today: a new root, every box signed again, and **the old root removed from every device** (that is the step that ends the risk; there is no revocation). Then change the passphrase habit that let it happen.

## If a box or its SD card is stolen

The thief has that box's key and certificate and can pretend to be that box, by that name, to a device with your root, until the certificate runs out. Make a new key on the remaining boxes' pages ("new key" at the request), sign and upload, and if the stolen box's name is one you still use, give the new box a different host name. The root is safe: it was never on the box.

## What is not built

A box without `openssl` cannot make a key (the page says so). No automatic renewal: the root is not on the box, by design, so a person signs once a year. No revocation list. No certificate for a public address (the root's constraints refuse it; `--no-constraints` at `make-root` is the way out, at the cost above). A root helper that would keep the key from the panel process was rejected (D79). Nothing of this has run on a box or on a real phone.
