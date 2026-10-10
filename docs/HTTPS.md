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

**HSTS** is not sent. A browser that once saw `Strict-Transport-Security` for the name refuses `http://` for it until the time runs out; guests have only `http://`, so one owner visit would lock every guest out on that browser. The page over `http://` instead checks whether the device trusts the box (one small fetch of `https://<the same host>/api/https/probe`) and offers the link; it never redirects by itself. The page's content security policy allows that fetch: `connect-src 'self'` matches the `https://` form of the same host on the default ports (CSP level 3, "Does url match expression in origin with redirect count").

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
