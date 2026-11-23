<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel as an app

The owner, 2026-10-10: "i need to make sure that the control panel can be saved as a webapp on all major devices." On 2026-11-22 he decided how: staff at a venue add the panel to the home screen of their phone or tablet and open it like an app (iPhone Safari, Android Chrome, the Amazon Fire tablet's Silk), now that a node can run on https:// with his own root certificate ([HTTPS.md](HTTPS.md)). The decision is D151.

**Nothing on this page was tried on a real phone, tablet or laptop.** What the browsers do is what their makers' pages say (read 2026-10-10, listed in the table), or is marked as not confirmed. The checks that did run are the tests in `tests/test_webapp.py`, which run the node's side and the two scripts against stand-ins for a browser. The rows to try on real devices are in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md), "The panel saved as an app (D151)".

## What a person does

Setup, **Save as an app**, on any paired device (Owner, Operator and Guest). The card names the device and browser it is open in, gives that browser's own steps, and shows an **Install** button when the browser offers its own install question (Chrome and Edge, over https:// only). When the page is already running as an app the card says so and has one button, "Load the panel again", because an app window has no reload button.

## What the node serves

| Address | What | Type | Kept |
| --- | --- | --- | --- |
| `/manifest.webmanifest` | the web app manifest, made for each request from the look in use (`mctl/webapp.py`) | `application/manifest+json` | ten minutes |
| `/sw.js` | the service worker: `mctl/web/sw.js` with the name of the release written in | `text/javascript` | never (`no-store`) |
| `/icon-192.png`, `/icon-512.png`, `/icon-maskable-512.png`, `/icon-mono.svg`, `/icon.svg` | the icons the manifest names (`icon.svg` is the source drawing) | `image/png`, `image/svg+xml` | a day |
| `/apple-touch-icon.png` and two other names | the 180 px icon an iPhone asks for | `image/png` | a day |
| `/away.html`, `/away.css` | the page the service worker shows when the node cannot be reached | | never |

All of them are public, like the page itself: they are asked for before anyone has paired, so the connect screen can be saved too. The page's policy has one more word, `manifest-src 'self'`; without it `default-src 'none'` refuses the manifest. The worker needs no new word: it is a script of the node's own (`script-src 'self'`). `/sw.js` is at the root, so its scope is the whole node and no `Service-Worker-Allowed` header is sent.

**The manifest** says: name and short name `NXLX`, `id`, `start_url` and `scope` all `/` (right at the node's name, at its address and on any port, over http and https), `display: standalone` with `display_override: ["standalone", "minimal-ui"]`, and four icons (192 and 512 px "any", 512 px "maskable", and a monochrome outline). `start_url` is the panel's own home: a saved app opens on Live like any fresh visit; the Room screen is one tap on the tab bar.

**Colours.** A look is chosen at run time (Setup, Look), so the colours cannot be in a file. The node writes the page colour of the look in use (its `bg` token, a checked `#rrggbb`) into the manifest's `theme_color` and `background_color` and into the page's `<meta name="theme-color">`. When the look changes while a page is open, the page sets `theme-color` again once the look's style sheet arrives. What this cannot do: a browser reads the manifest when the panel is saved and most keep what they read, so an app saved under one look keeps that look's launch colour until it is saved again. On an iPhone the status bar style is `black-translucent` for a dark look (the page runs under the bar and the panel keeps clear of it) and `default` for a light one.

**The icon.** One drawing in `mctl/icon.py`: a plain N in the house off-white on the house near-black, three flat shapes. `tools/make-icons.py` draws the PNGs (smoothed edges, standard library only, nothing outside the repository) and the two SVGs from it; the files are committed and a test fails when a committed file is not what the script makes. The maskable icon draws the mark at 85%, inside the middle circle a launcher may cut the picture down to; the test measures it.

## The service worker, and why it is this small

Chrome used to need a service worker with a fetch handler before it would offer to install a site; its own install criteria no longer list one, and the iPhone, iPad, Safari and Edge never needed one. So the worker is not there to make the app installable. It is there for one thing a person notices: opened from the home screen **away from the node's network** (the phone is on mobile data, or the node is off), a saved app would otherwise show the browser's error page, which looks like a broken app. With the worker it shows a plain page that says the node cannot be reached, that the show keeps playing, a line for a node that has moved back to http, and a button to try again.

What it will not do, and the tests hold it to:

- **It never keeps anything the panel shows.** Its cache holds exactly two files, `/away.html` and `/away.css`, put there when it is installed. It never stores a response from the node. The panel's scripts, style sheets, pictures and every `/api/` call do not pass through it at all (it answers only a page being opened, from a GET, to this node, and not under `/api/`), so nothing per person can be kept and no old panel can be shown after an update.
- **It cannot serve a stale panel.** A page being opened goes to the node first; the away page is shown only when the browser reports that the request failed. There is no timer: a slow node (TLS on a loaded Pi, a phone that has just rejoined Wi-Fi) is waited for and its page is shown when it arrives. The away page's own style sheet is also answered from the cache when the node is away, so the page is styled.
- **An update replaces it.** `/sw.js` is the file with the release written into it, `nxlx-shell-<version>-<checksum of sw.js and the away files>`, and is never kept by the browser's cache. A new release is a different file: the browser installs it, it takes over at once (`skipWaiting` and `clients.claim`) and deletes every cache that is not its own. A change to the away files with the version unchanged is a new name too.
- **It removes itself when the node stops serving it.** When a page is opened and when the worker activates, it asks the node for `/sw.js`; a 404 or 410 (a release without a worker) makes it delete its caches and unregister. Any other answer, or no answer, leaves it alone. `/sw.js` also tells anyone, before pairing, the node's exact version (in the cache name); this was accepted (D151).
- **Over plain http nothing changes.** A browser refuses a service worker on http, and `webapp.js` does not ask for one there (`location.protocol` must be `https:`). The manifest and the page's tags are all that http gets.

Rejected: a worker that keeps the panel's own files for a "works offline" shell. Every control in the panel needs the node, so an offline panel could only show a screen that looks signed out (the status call would fail and the page would fall back to the connect screen) and would risk showing last month's panel after an update. Also rejected: no worker at all. That is the simplest, and the app would show the browser's error page away from the node, with no way to tell a person what is wrong.

## What each browser says (read 2026-10-10)

"App window" means: its own icon and name, its own window, no address bar. Rows for Safari and desktop browsers are as they were on 2026-10-10 and were not read again on 2026-11-22. **No row was tried on a device.**

| Where | How a person saves a site as an app | Over plain http | Over https with a certificate the device trusts | Source |
|---|---|---|---|---|
| iPhone and iPad, Safari, iOS 26 and later | Share, then **Add to Home Screen**; the switch **Open as Web App** is on by default | An app window is expected: Apple states no https condition. Untried | The same | [WebKit features in Safari 26.0](https://webkit.org/blog/17333/webkit-features-in-safari-26-0/), 2025-09-15 |
| iPhone and iPad, Safari, iOS 16.4 to 18 | Share, then **Add to Home Screen** | A manifest with `display` `standalone`, or the tag `apple-mobile-web-app-capable` (the node sends both), makes it open as an app; otherwise a bookmark | The same | [Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/), 2023-02-16 |
| iPhone and iPad, other browsers | Their Share menu, then **Add to Home Screen** (iOS 16.4 and later) | As Safari: every browser on iOS shows pages with Apple's WebKit. Menu words not confirmed | The same | WebKit, 2023-02-16 |
| Android, Chrome | Three dots, then **Install app** or **Add to Home screen** | Only a home screen shortcut; Chrome refuses an installed app ("Page is not served from a secure origin") | An installed app (a WebAPK) with a window of its own | [web.dev, install criteria](https://web.dev/articles/install-criteria), 2024-09-19; Chromium `installable_logging.cc` |
| Android, Samsung Internet | Menu, then **Add page to**, then **Home screen** (words from secondary guides) | A shortcut | Installable; how it opens is not confirmed | [Samsung Internet developer guide](https://developer.samsung.com/browser/android/web-developer-guide.html) |
| Android, Firefox | Three dots, then **Add to Home screen** | A shortcut that opens in Firefox | The same: Firefox on Android does not install apps | MDN, Making PWAs installable, 2026-10-06 |
| Amazon Fire tablet, Silk | Silk's menu, look for **Add to Home screen** or **Install app** (the words are not confirmed; the card says they differ by version) | Not read: no Amazon page was read for this | Not read. Silk is built on Chromium, but whether it makes an installed app is **not confirmed** | none |
| Mac, Safari (macOS 14 and later) | **File, Add to Dock** | An app window is expected. The app "shares no browsing history, cookies, website data, or settings with Safari" | The same | [Apple, Use Safari web apps on Mac](https://support.apple.com/en-us/104996) |
| Windows, Mac, Linux, Chrome | Three dots, **Cast, save, and share**, **Install page as app...** | The menu item is there; the address bar's Install icon is not | The Install icon, and `beforeinstallprompt` (the card's Install button) | Chrome Help, Use web apps; Chromium `docs/webapps` |
| Windows, Mac, Linux, Edge | Three dots, **More tools**, **Apps**, **Install this site as an app** | The menu item is said to work for any site | The App available icon | Microsoft Support and Microsoft Learn, 2025 |
| Windows, Firefox 143 and later | A web apps button in the address bar (words from secondary reports) | Not confirmed | Not confirmed | Firefox source docs, read 2026-10-10 |
| Mac and Linux, Firefox | Not offered | | | Firefox source docs |

Over https the device must trust the node first, or the browser shows a warning page and offers nothing. The card says so, and where: Setup, Secure connection installs the root certificate for each device.

## Signing in from the home screen

The panel keeps a device's sign-in in one cookie that the node sets when a device pairs: `mctl_token` over http, `__Host-mctl_token` (Secure) over https. It is `HttpOnly`, `SameSite=Strict`, `Path=/`, and lasts a year (`Max-Age`). The panel keeps nothing about the sign-in in `localStorage`; the cookie is the whole of it. So the question is whether the app can see the cookie the browser holds.

- **iPhone and iPad.** An app saved from Safari on a current iOS has its own storage, apart from Safari's: cookies set in Safari are not there, and the other way round. This is expected from how iOS treats saved web apps; the one primary source read for it is Apple's page for the Mac's Safari (in the table), which says a saved app shares no cookies or website data with Safari, and no Apple page for the iPhone was read for this. **The first time an app is opened it asks for pairing**; after that the cookie lives in the app's own storage and keeps the sign-in for its year. Pairing in the app is by typing the code or the PIN: **a QR code scanned with the camera, and a guest link, open Safari, not the app**, so a person who scans a code ends up signed in in Safari and not in the app. The panel says so on the connect screen when it runs as an app, and on the Save as an app card. Open the app, type the code, name the device; every saved app is its own entry under Setup, Paired devices.
- **Android, Chrome.** An installed app (over https) is Chrome's own and is expected to share Chrome's cookies, so a device already paired in Chrome on the same https:// address is already signed in. Not confirmed; if it does not, the app asks for pairing like an iPhone.
- **Silk and Samsung Internet.** A shortcut opens in the browser, which has the cookie. Not confirmed for an installed app.
- **http and https are different addresses to a browser, and so are the node's name and its IP address.** A device paired at `http://nxlx-mastercontrol.local` is not paired at `https://nxlx-mastercontrol.local`. Save the app from the address that will be used at the venue (the https:// name, once the node has a certificate) and pair there.
- **The cookie's year does not end by itself on an iPhone.** WebKit's seven day limit is for storage a script writes; it is not expected to apply to a cookie the node sets, and saved apps are said to be outside it. Neither was confirmed here or on a device: the first time an iPhone app is still signed in after a week is the proof.
- **Safe on the move.** `SameSite=Strict` does not stop the cookie being sent when an app is opened from its icon: nothing else started that navigation. A link in a message or a QR code that opens the panel from another app is the case where a Strict cookie may not be sent; then the page loads and the panel asks again, which is what the connect screen is for.

## What it does inside an app window

The page already asked for the whole screen (`viewport-fit=cover`). Now the page also keeps clear of the notch, the status bar and the rounded corners on every side, upright and sideways (the shell and the "connection lost" banner take the device's safe-area insets; the tab bar had its bottom inset already). In an app window only, the page does not stretch away from its bars when pulled past its end; in a browser tab pulling down still loads the page again. Pinch zoom is left on, because some people need it. No screen relies on the browser's back button: every page of Setup has its own back button and the tab bar is always there. The clip picker is not a history entry, so a phone's back gesture does not close it (Cancel does).

## Not done, and why

- No pinned-tab `mask-icon`, no splash screens for every iPhone size (Safari builds one from the manifest's colour and icon), no push notifications, no "update available" banner (a new release replaces the worker by itself, and the page loads fresh from the node each time it is opened).
