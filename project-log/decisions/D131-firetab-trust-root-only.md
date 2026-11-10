<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D131. The FireTab launcher trusts only the owner's own root and shows only nodes (2026-11-09, decided)

Decided in the launcher's 2.0 build and its independent review, 2026-11-09. The FireTab launcher (NXLX Panel, a separate local repo, nxlx-fire-launcher) is a kiosk: it shows a node's panel and nothing else. It trusts exactly one certificate root, the owner's own (the public `root.pem`, bundled in the app; the private key is never in that repo). Android's network security config makes it the only trust anchor, so there are no system roots and a public site cannot load at all.

The rules:
- A node is an `https` address whose host is a `.local` name or a private LAN address (10/8, 172.16/12, 192.168/16). Plain `http` for such a host on the default port is upgraded to `https`. Everything else is refused: public names and addresses, nxlx.systems, other ports on plain http, user-name tricks such as `https://192.168.0.1@evil.com`, and any scheme other than https.
- A certificate error is never passed over; there is no proceed button. An extra check (`CertCheck`) can only refuse: not signed directly by the bundled root, weak signature, a CA certificate, a host missing from the SAN, and so on.
- Every web request that is not to a node, or to the launcher's own error page, gets an empty 403, so a node page cannot pull in a public site through a POST or an iframe.
- Pop-ups: at most one, and only from a user tap. The launcher answers alert and leave-page questions itself (accepted) and cancels confirm and prompt, so a node switch is never held up.

Why: the tablet is a controller that sits on a wall or a desk and may be handled by staff, so it should not be a way onto the open web, and a lost or tampered link should not be able to move it off the nodes. Using the owner's root, which the nodes already use (HTTPS with his own CA), means no new trust to manage.

Alternatives not taken: trusting the system roots as well as the owner's root (a public site could load, which a kiosk does not need); continuing past certificate errors (a controller would then accept a fake node).

Cost and what it rules out: the tablet's clock matters. After a power cut a wrong date makes every certificate look out of date, so the launcher says so in plain words and retries every 30 seconds and when Wi-Fi returns. Other refusals are not retried. Any page the panel needs from the internet would not load; the panel was checked on the device and needs none. The panel must keep using no confirm(), prompt() or alert(), or the launcher is changed first. Source: the launcher repo's README ("How trust works") and commits 2af8a6a and 74cd4f6.
