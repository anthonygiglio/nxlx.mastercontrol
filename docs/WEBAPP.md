<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel as an app

The owner, 2026-10-10: "i need to make sure that the control panel can be saved as a webapp on all major devices."

This page has three parts: what each browser says it needs before it saves a site as an app (read on 2026-10-10 from the vendors' own pages), what the box does about it (D76), and what is left for the owner to decide (HTTPS).

**Nothing on this page was tried on a real phone, tablet or laptop.** Every "the browser does X" below is what the vendor's page says, or is marked as not confirmed. The rows to try on real devices are in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md), "The panel saved as an app (D76)".

## The hard fact

The panel is served over plain HTTP on a private network (`http://nxlx-mastercontrol.local` or the box's address). Browsers tie parts of "install" to a secure context: HTTPS, or `localhost` on the same machine. A phone that opens the box is neither. So:

- No browser runs a **service worker** for the panel ("Service workers are restricted to running across HTTPS", MDN). Nothing can be kept for the moment the box is out of reach.
- Chrome's own **install prompt** (the Install icon in the address bar, the `beforeinstallprompt` event, and on Android the installed app that Chrome builds, a WebAPK) is refused: "Be served over HTTPS" is on Chrome's list, and Chromium's source has the message "Page is not served from a secure origin".
- What still works is each browser's **menu item that saves any site**. On Apple devices, in desktop Chrome and in desktop Edge the vendors say that gives an app window for any site. On Android it gives a home screen shortcut.

## What each browser says (read 2026-10-10)

"App window" means: its own icon and name, its own window, no address bar.

| Where | How a person saves a site as an app | What the browser wants for an app window | Over plain HTTP on a `.local` name or a private address | Source, and its date |
|---|---|---|---|---|
| iPhone, Safari, iOS 26 and later | Share, then **Add to Home Screen**; the switch **Open as Web App** is on by default | Nothing: "By default, every website added to the Home Screen opens as a web app", "no longer requires a manifest file". If the manifest names icons "they're used" | Apple states no HTTPS condition for this. So an app window is expected. **Untried** | [WebKit features in Safari 26.0](https://webkit.org/blog/17333/webkit-features-in-safari-26-0/), 2025-09-15 |
| iPhone, Safari, iOS 16.4 to 18 | Share, then **Add to Home Screen** | A manifest with `display` `standalone` or `fullscreen` (or, older, the meta tag `apple-mobile-web-app-capable`); otherwise it is saved as a bookmark that opens in the browser | No HTTPS condition is stated; the box sends both the manifest and the meta tag. **Untried** | [Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/), 2023-02-16 |
| iPhone, Chrome, Edge, Firefox and other browsers | Their Share menu, then **Add to Home Screen** (since iOS 16.4) | The same as Safari on that iOS: every browser on iOS shows pages with Apple's WebKit. Apple's condition for the Share menu entry names "an HTTP or HTTPS URL" | As Safari on the same iOS. The exact menu words in each browser were **not confirmed** | WebKit, 2023-02-16 (above); [MDN, Making PWAs installable](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable), 2026-10-06 |
| iPad | As the iPhone with the same system version | As the iPhone | As the iPhone. **Untried** | The two WebKit pages above |
| Android, Chrome | The three dots, then **Add to Home screen** (or **Install app** where Chrome offers it) | For an installed app (a WebAPK): a manifest with a name, a `start_url`, icons of 192 and 512 px and a `display` that is not `browser`, **and HTTPS**. A service worker is not on the list any more | Not an installed app: Chromium refuses with "Page is not served from a secure origin". The menu still adds a **home screen shortcut**. Whether that shortcut opens with or without Chrome's address bar on a current Chrome is **not confirmed** by any primary source found; MDN says of browsers that only add a shortcut that it "opens the site in the browser" | [web.dev, install criteria](https://web.dev/articles/install-criteria), 2024-09-19; [Chromium, installable_logging.cc](https://chromium.googlesource.com/chromium/src/+/main/components/webapps/browser/installable/installable_logging.cc) and [docs/webapps/README.md](https://chromium.googlesource.com/chromium/src/+/main/docs/webapps/README.md), main branch on 2026-10-10 |
| Android, Samsung Internet | The menu (three lines), then **Add page to**, then **Home screen** (menu words from secondary guides, **not confirmed** from Samsung) | Samsung's developer guide lists HTTPS, a service worker and a manifest for its install button; an installed app (WebAPK) only on Samsung devices | A home screen shortcut. How it opens is **not confirmed** | [Samsung Internet web developer guide](https://developer.samsung.com/browser/android/web-developer-guide.html), undated and old (it names version 6.2); MDN, 2026-10-06 |
| Android, Firefox | The three dots, then **Add to Home screen** (menu words **not confirmed**: Mozilla's help pages refused to load here) | Firefox on Android does not install: it adds "a browser-badged home-screen shortcut that opens the site in the browser" | A shortcut that opens in Firefox, with its bars | MDN, Making PWAs installable, 2026-10-06 |
| Mac, Safari (macOS 14 Sonoma and later) | **File > Add to Dock**, or the Share button, then **Add to Dock**; type a name, **Add** | Nothing: "for any web app with or without a manifest file" | Apple states no HTTPS condition. An app window is expected. **Untried**. The app "shares no browsing history, cookies, website data, or settings with Safari" | [Apple, Use Safari web apps on Mac](https://support.apple.com/en-us/104996), 2026-05-27; WebKit, 2025-09-15 |
| Windows, Mac, Linux: Chrome | The three dots, then **Cast, save, and share**, then **Install page as app...** | For this menu item: nothing but an `http` or `https` address ("Any site can be installed through the browser menu"). For the Install icon in the address bar and for `beforeinstallprompt`: the manifest and HTTPS | The menu item is there and Chromium's rule for it allows `http`. An app window is expected; what the window's title bar shows for an address that is "Not secure" is **not confirmed**. **Untried**. The address bar's Install icon does not appear | [Chrome Help, Use web apps](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DDesktop), undated; [Chromium, docs/webapps/concepts.md](https://chromium.googlesource.com/chromium/src/+/main/docs/webapps/concepts.md), main branch on 2026-10-10 |
| Windows, Mac, Linux: Edge | The three dots (**Settings and more**), then **More tools**, then **Apps**, then **Install this site as an app** | Nothing for this menu item: "you can install any website". For the **App available** icon in the address bar: a manifest, and HTTPS for the parts that need it. "A PWA doesn't need to have a service worker for Microsoft Edge to be able to install the app" | The menu item is said to work for any website. **Untried** over HTTP | [Microsoft Support, Install, manage, or uninstall apps in Microsoft Edge](https://support.microsoft.com/topic/install-manage-or-uninstall-apps-in-microsoft-edge-0c156575-a94a-45e4-a54f-3a84846f6113), undated; [Microsoft Learn, Get started developing a PWA](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/), 2025-10-09, updated 2026-09-02 |
| Windows: Firefox (143 and later) | A web apps button in the address bar adds the site to the taskbar (words from secondary reports; Mozilla's help page refused to load here, so **not confirmed**) | Firefox does not use the manifest to install ("Firefox does not support installing PWAs using a manifest file", MDN). Its own feature pins "a simplified and site stylized browser window to the taskbar" | **Not confirmed** | [Firefox source docs, Web Apps in Firefox](https://firefox-source-docs.mozilla.org/browser/components/taskbartabs/docs/index.html), read 2026-10-10; MDN, 2026-10-06 |
| Mac and Linux: Firefox | Not offered: "macOS support is not currently available"; on Linux it is off by default | | A bookmark is all there is | Firefox source docs, as above |
| Chromebook (ChromeOS) | Chrome's menu, as desktop Chrome | As desktop Chrome | As desktop Chrome. Nothing specific to ChromeOS was read, so **not confirmed** | Chrome Help and Chromium, as above |

### What follows from the table

- **iPhone, iPad, Mac with Safari, and desktop Chrome and Edge:** the vendors' pages say an app window is given for any site, and state no HTTPS condition for it. Untried.
- **Android (Chrome, Samsung Internet, Firefox):** no installed app over plain HTTP. A home screen icon, which opens the panel in the browser. That is the gap HTTPS would close; see "HTTPS" below.
- **Firefox on a Mac or on Linux:** a bookmark.
- **Nowhere** is there a service worker, so nowhere does the app open when the box is out of reach; see "Away from the box".

## What the box does (D76)

This part is written with the code; see below in this pull request.

## HTTPS: the options, and a question for the owner

Nothing in this part is built. Serving TLS from the box changes what the box exposes and has to be decided by the owner and read by an independent reviewer first.

What HTTPS would buy: on Android an installed app with its own window (Chrome, and Samsung Internet on Samsung devices); the Install button in the address bar of desktop Chrome and Edge and the panel's own "Install" button; a service worker, so the app could open to a page that says "the box is not on this network" where it now opens to the browser's error page; a `Secure` cookie. What it would not change: iPhone, iPad and Mac, which the vendors say give an app window already.

Facts used below, with their sources: Chrome on Android trusts a certificate authority the user has installed (its [network security configuration](https://chromium.googlesource.com/chromium/src/+/main/chrome/android/java/res_base/xml/network_security_config.xml) lists `<certificates src="user"/>`, read 2026-10-10; other Android apps do not by default since Android 7, [Android Developers Blog](https://android-developers.googleblog.com/2016/07/changes-to-trusted-certificate.html), 2016-07-07). On an iPhone or iPad a certificate installed by hand must also be switched on under Settings > General > About > Certificate Trust Settings ([Apple](https://support.apple.com/en-us/102390), 2025-03-26). Chrome has a policy that lifts the restrictions on named insecure origins, on Windows, Mac, Linux, ChromeOS and Android ([OverrideSecurityRestrictionsOnInsecureOrigin](https://chromium.googlesource.com/chromium/src/+/main/components/policy/resources/templates/policy_definitions/Miscellaneous/OverrideSecurityRestrictionsOnInsecureOrigin.yaml), read 2026-10-10), and the flag `--unsafely-treat-insecure-origin-as-secure` does the same for one browser.

### 1. The box is its own certificate authority

The box makes a root certificate once, and with it a certificate for its name and its address. The panel offers the root for download with steps for each device.

- **Each person, each device, once:** download the root; on an iPhone or iPad install the profile in Settings and then switch on full trust in a second place; on Android install it under Settings > Security > "Install a certificate" > "CA certificate" (the words differ by maker) and accept a warning that the network may be monitored, which stays in the notification area on some phones; on a Mac add it to the keychain and set it to Always Trust; on Windows import it into Trusted Root Certification Authorities. Firefox keeps its own list on the desktop. These are four to eight taps or clicks with frightening words in them, per device. A venue's staff will not do this, and a guest with a code never should.
- **What breaks:** a certificate names the box's name and address. When the address changes (another network, another DHCP lease) the certificate for the address is wrong until the box makes a new one, which it can do by itself because it holds the root; the `.local` name keeps working. A **factory reset** or a **reinstall** makes a new root unless the old one is kept, and then every device has to remove the old root and install the new one. Leaf certificates must be short (Apple accepts at most 398 days for a certificate, and applies its rules to roots a user installed as well, which is **not confirmed** here), so the box must renew by itself, and its clock must be right, which a Pi without a battery clock and without internet cannot promise.
- **Risk:** a root that a phone trusts can vouch for any site, not just the box. Whoever copies the root's key from the box (the SD card is not encrypted) can read that phone's traffic to any site on a network they control. A name constraint in the root (only the box's name and private addresses) limits this where the platform enforces it; whether every platform above does was **not confirmed**. The key would have to live where only a root-owned helper reads it.
- **Cost:** large. Making and renewing certificates with the standard library alone is not possible (Python's `ssl` cannot create a certificate), so it needs `openssl` on the box or a dependency; a TLS listener in the panel; the download and the per-device steps; a redirect or both ports; tests; a security review. The same origin change signs everybody out once (`http://` and `https://` are different origins, and an installed app saved from the `http://` address stays on it).

### 2. A real certificate for a real name that points at a private address

The owner has a domain. A name under it, such as `box.example.org`, has a public DNS record that points at the box's private address, and the box gets a certificate from a public authority by proving it controls the DNS (the DNS-01 challenge), which needs no way in from the internet.

- **Each person:** nothing. Every device already trusts the certificate. This is the only option with that property.
- **Owner:** a domain (a yearly fee), a DNS provider with an API, and a token for it stored on the box.
- **What breaks:** the certificate lasts 90 days or less, so the box needs **internet at least every few weeks** to renew; a box that sits on a private wire at a venue for a season lapses, and then every device shows a full-page warning, worse than plain HTTP. The devices need **DNS from the internet** to find the name, so the direct-cable set-up with no internet does not work at all with that name. Some routers refuse public names that answer with private addresses (protection against DNS rebinding). When the box's address changes, the DNS record has to change with it. The name and the private address are public knowledge (certificates are logged publicly).
- **Risk:** a DNS token on a box that travels. It must be limited to one record.
- **Cost:** medium to large: an ACME client (not in the standard library), the DNS provider's API, renewal, the TLS listener, and a fallback for the day the certificate has lapsed.

### 3. A browser flag or a policy on each device

Chrome and Edge can be told to treat `http://nxlx-mastercontrol.local` as secure: the flag "Insecure origins treated as secure" (`chrome://flags`), or the policy above on managed devices.

- **Each person, each device:** type an address into a flags page, paste the box's address exactly, restart the browser. Chrome shows a warning bar about an unsupported flag on some versions. Only Chromium browsers; nothing like it on an iPhone, an iPad or in Safari (which do not need it).
- **What breaks:** the address is in the flag, so a new address means doing it again. A flag can disappear in any Chrome release.
- **Risk:** small and local to that browser; no key anywhere.
- **Cost to build:** none in the box; a paragraph in the manual. It is the only way to get a service worker and an installed Android app without touching the box, so it is also the way to **try** what HTTPS would give before deciding to build it.

### 4. A small wrapper app per platform

An Android app (and perhaps others) that is nothing but a window on the panel's address.

- **Each person:** install an app from outside the store (Android: allow unknown sources) or from a store (a developer account, a yearly fee and a review, and Apple does not accept a bare wrapper).
- **Cost:** large and for ever: a second code base, signing keys, releases per platform. It also has to find the box.
- **Risk:** signing keys; an app that asks people to switch off a protection of their phone.

### 5. Stay on HTTP

What this pull request builds. iPhone, iPad, Mac, and Chrome and Edge on a laptop save the panel as an app by their own menu (as the vendors say; untried). Android gets a home screen icon that opens the panel in the browser, with the browser's bar. Nothing opens away from the box.

- **Cost:** none further. **Risk:** none added.

### Recommendation

- **A venue's staff room:** option 5. Staff and guests will not install certificates, and should not be taught to. On Android the panel in a browser tab from a home screen icon is the same panel.
- **The owner's own devices:** option 5 as well if they are Apple devices, which is where the vendors say it already works. For an Android phone or tablet of his own, option 3 on that one device, which costs ten minutes and also shows what option 1 or 2 would buy.
- **Build 1 or 2 only if** the real-device rows show something that matters is missing. Of the two, option 2 is right if he already owns a domain and the boxes see the internet now and then; option 1 is right for a box that never sees the internet, for his own devices only.

### The question

On Android the panel cannot become a real app over plain HTTP (it gets a home screen icon that opens in Chrome). iPhone, iPad, Mac and laptops are expected to be fine. What should happen about Android?

1. **Leave it** (recommended): Android opens the panel in Chrome from a home screen icon. Nothing to build.
2. **My own Android only:** I set one Chrome flag on my own device, following the manual. Nothing to build.
3. **The box makes its own certificate:** every device installs it once, by hand. A large build with a security review.
4. **A real domain and a public certificate:** I have (or will buy) a domain, and the box sees the internet at least monthly. A large build with a security review.
