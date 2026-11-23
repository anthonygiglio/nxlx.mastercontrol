// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The panel saved as an app (D151, docs/WEBAPP.md): the part of it that runs in the page.
//
//   where(env)     which device and browser this is, whether the page runs as an app and whether it is on https,
//                  from what the browser reports. A plain function of its argument, so a test can ask about any device.
//   here()         where(what this browser reports)
//   guide(w)       the steps to save the panel as an app THERE, in that browser's own words for its menus, and one
//                  line about what does not work there and why. Plain data; the words are from the vendors' pages
//                  listed in docs/WEBAPP.md, and none of it was tried on a real phone or tablet.
//   card(ctx)      the card on Setup > Save as an app, with the browser's own install button where it offers one
//   pairNote(h)    a line for the connect screen when the page runs as an app that is not paired yet
//   register()     asks for the service worker (mctl/web/sw.js), over https only
//
// It also keeps the page's theme-color (the colour of the browser's own bar and of the status bar) on the look in
// use: the node writes it into the page, and when the look is changed the style sheet of the look arrives again and
// the colour is read from it here.
(function () {
  'use strict';

  // ---- where am I -------------------------------------------------------
  // env: { ua: the User-Agent text, touch: navigator.maxTouchPoints, standalone: navigator.standalone (Apple only),
  //        mode: true when a display-mode of standalone, fullscreen or minimal-ui matches, secure: the page is https }
  function where(env) {
    var ua = String((env && env.ua) || ''), touch = Number((env && env.touch) || 0);
    var device = 'other', browser = 'other';
    if (/iPhone|iPod/.test(ua)) device = 'iphone';
    // an iPad says it is a Mac; a Mac has no touch screen
    else if (/iPad/.test(ua) || (/Macintosh/.test(ua) && touch > 1)) device = 'ipad';
    else if (/Android/.test(ua)) device = 'android';
    else if (/CrOS/.test(ua)) device = 'chromebook';
    else if (/Windows/.test(ua)) device = 'windows';
    else if (/Macintosh/.test(ua)) device = 'mac';
    else if (/Linux|X11/.test(ua)) device = 'linux';
    // an Amazon Fire tablet is an Android tablet whose browser is Silk
    if (/\bSilk\//.test(ua) && device !== 'iphone' && device !== 'ipad') device = /Android|Linux|X11/.test(ua) ? 'fire' : device;

    if (device === 'iphone' || device === 'ipad') {
      // every browser on these is Apple's engine under another name
      browser = /CriOS/.test(ua) ? 'chrome' : /FxiOS/.test(ua) ? 'firefox' : /EdgiOS/.test(ua) ? 'edge' : /Safari/.test(ua) ? 'safari' : 'other';
    } else if (/\bSilk\//.test(ua)) browser = 'silk';
    else if (/SamsungBrowser/.test(ua)) browser = 'samsung';
    else if (/Firefox\//.test(ua)) browser = 'firefox';
    else if (/Edg(A|e)?\//.test(ua)) browser = 'edge';
    else if (/OPR\/|Opera/.test(ua)) browser = 'other';
    else if (/Chrome\//.test(ua)) browser = 'chrome';
    else if (/Safari\//.test(ua) && /Version\//.test(ua)) browser = 'safari';
    return { device: device, browser: browser, app: !!(env && (env.standalone === true || env.mode)), secure: !!(env && env.secure) };
  }
  function here() {
    var mode = false;
    try {
      mode = ['standalone', 'fullscreen', 'minimal-ui'].some(function (m) { return window.matchMedia('(display-mode: ' + m + ')').matches; });
    } catch (e) { mode = false; }
    return where({ ua: navigator.userAgent, touch: navigator.maxTouchPoints, standalone: navigator.standalone, mode: mode, secure: location.protocol === 'https:' });
  }

  // ---- the steps, per device --------------------------------------------
  var NAMES = { iphone: 'an iPhone', ipad: 'an iPad', android: 'an Android device', fire: 'an Amazon Fire tablet', chromebook: 'a Chromebook', windows: 'a Windows computer', mac: 'a Mac', linux: 'a Linux computer', other: 'this device' };
  var BROWSERS = { safari: 'Safari', chrome: 'Chrome', edge: 'Edge', firefox: 'Firefox', samsung: 'Samsung Internet', silk: 'Silk', other: 'this browser' };
  var OWN_SIGN_IN = 'Open the panel from its new icon and pair it once there: the app keeps its own sign-in, apart from ';
  var NO_HTTPS = 'this node is open over plain http://, not https://';
  var NEED_ROOT = 'Over https:// the device must trust the node first: install the node\'s root certificate (Setup, Secure connection).';
  var AWAY = 'Away from the node\'s network the app opens a page that says the node cannot be reached, and a button to try again.';
  var AWAY_HTTP = 'Away from the node\'s network the app shows the browser\'s error page until this device is back on that network.';

  function guide(w) {
    var d = w.device, b = w.browser, name = BROWSERS[b] || BROWSERS.other, sec = !!w.secure;
    var g = { where: 'This panel is open in ' + name.replace(/^this/, 'a') + ' on ' + (NAMES[d] || NAMES.other) + '.', steps: [], cannot: sec ? AWAY : AWAY_HTTP, window: true };
    if (d === 'iphone' || d === 'ipad') {
      if (b === 'safari') {
        g.steps = ['Tap the Share button: a square with an arrow pointing up.', 'Scroll down and tap Add to Home Screen.',
          'If there is a switch Open as Web App, leave it on. Tap Add.', OWN_SIGN_IN + 'Safari. A QR code scanned with the camera opens Safari, not the app, so type the code or PIN in the app.'];
      } else {
        g.steps = ['Open the Share menu of ' + name + '.', 'Tap Add to Home Screen. If there is no such entry, open the panel in Safari and do it there.',
          'Tap Add.', OWN_SIGN_IN + name + '.'];
      }
    } else if (d === 'android' || d === 'fire') {
      if (b === 'samsung') g.steps = ['Tap the menu: three lines at the bottom right.', 'Tap Add page to, then Home screen.', 'Tap Add.'];
      else if (b === 'chrome') g.steps = sec ? ['Tap the three dots at the top right.', 'Tap Install app. If you see only Add to Home screen, tap that.', 'Tap Install or Add.']
        : ['Tap the three dots at the top right.', 'Tap Add to Home screen.', 'Tap Add.'];
      else if (b === 'silk') g.steps = ['Open Silk\'s menu: three dots or three lines.', 'Look for Add to Home screen, or Install app. The words differ between versions of Silk.', 'Tap Add or Install.'];
      else g.steps = ['Open the menu of ' + name + ': three dots.', 'Tap Add to Home screen.', 'Tap Add.'];
      if (sec) {
        g.window = b !== 'firefox';
        g.cannot = NEED_ROOT + (b === 'firefox' ? ' Firefox on Android does not make apps from sites: it adds a shortcut that opens in Firefox.' : '') + ' ' + AWAY;
      } else {
        g.window = false;
        g.cannot = 'The panel does not get a window of its own here: the icon opens it in ' + name + ', with its bar. ' +
          (b === 'firefox' ? 'Firefox on Android does not make apps from sites. ' : 'An Android browser makes a real app only from a page served over https, and ' + NO_HTTPS + '. Open the node\'s https:// address (Setup, Secure connection) and do this again. ') + AWAY_HTTP;
      }
    } else if (b === 'safari' && d === 'mac') {
      g.steps = ['In the menu bar choose File, then Add to Dock. Or click the Share button, then Add to Dock.', 'Type a name and click Add.', OWN_SIGN_IN + 'Safari.'];
    } else if (b === 'chrome') {
      g.steps = sec ? ['Click the Install icon at the right of the address bar. Or click the three dots, then Cast, save, and share, then Install page as app...', 'Click Install.']
        : ['Click the three dots at the top right.', 'Choose Cast, save, and share, then Install page as app...', 'Click Install.'];
      g.cannot = sec ? NEED_ROOT + ' ' + AWAY : 'Chrome\'s Install button in the address bar does not appear: it needs https://, and ' + NO_HTTPS + '. The menu does the same. ' + AWAY_HTTP;
    } else if (b === 'edge') {
      g.steps = ['Click the three dots at the top right: Settings and more.', 'Choose More tools, then Apps, then Install this site as an app.', 'Click Install.'];
      g.cannot = sec ? NEED_ROOT + ' ' + AWAY : 'Edge\'s App available button in the address bar does not appear: it needs https://, and ' + NO_HTTPS + '. The menu does the same. ' + AWAY_HTTP;
    } else if (b === 'firefox' && d === 'windows') {
      g.steps = ['If Firefox shows a web apps button at the right of the address bar, click it: the panel goes to the taskbar.',
        'If there is no such button, open the panel in Edge or Chrome and save it there, or bookmark this page.'];
    } else if (b === 'firefox') {
      g.window = false;
      g.steps = ['Bookmark this page.', 'Or open the panel in ' + (d === 'mac' ? 'Safari, Chrome or Edge' : 'Chrome or Edge') + ' and save it there.'];
      g.cannot = 'Firefox on this computer cannot save a site as an app.';
    } else {
      g.steps = ['Look in this browser\'s menu for Add to Home screen, Install, or Add to Dock.', 'If there is none, bookmark this page.'];
    }
    return g;
  }

  // ---- the browser's own install question, where it offers one ------------
  // Chrome and Edge raise this only for a page served over https, from a device that trusts the node. Nothing is shown
  // by the browser itself: the card has the button.
  var offer = null, installed = false;
  window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); offer = e; redraw(); });
  window.addEventListener('appinstalled', function () { offer = null; installed = true; redraw(); });
  function redraw() {
    var old = document.getElementById('appcard');
    if (old && old.parentNode && lastCtx) old.parentNode.replaceChild(card(lastCtx), old);
  }

  // ---- the card -----------------------------------------------------------
  var lastCtx = null;
  function card(ctx) {
    lastCtx = ctx;
    var h = ctx.h, w = here(), g = guide(w);
    var el = h('div', { class: 'card', id: 'appcard', 'data-device': w.device, 'data-browser': w.browser, 'data-app': w.app ? '1' : '0', 'data-secure': w.secure ? '1' : '0' });
    if (w.app) {
      el.appendChild(h('h2', { text: 'This is the app' }));
      el.appendChild(h('div', { id: 'appwhere', text: 'This panel is open as an app on ' + (NAMES[w.device] || NAMES.other) + '. Nothing more to do here.' }));
      el.appendChild(h('div', { class: 'hint', id: 'appcannot', text: g.cannot }));
      // an app window has no reload button of its own
      el.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'appreload', text: 'Load the panel again', onclick: function () { location.reload(); } })));
      return el;
    }
    el.appendChild(h('h2', { text: 'Save this panel as an app' }));
    el.appendChild(h('div', { id: 'appwhere', text: g.where + (g.window ? ' Saved as an app it gets its own icon and its own window.' : ' It can have its own icon on the home screen.') }));
    if (installed) el.appendChild(h('div', { class: 'msg', id: 'appdone', role: 'status', text: 'Saved. Open the panel from its new icon.' }));
    if (offer) {
      el.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn on pri big grow', id: 'appinstall', text: 'Install', onclick: function () {
        var o = offer;
        offer = null;
        if (o && o.prompt) o.prompt();
        redraw();
      } })));
      el.appendChild(h('div', { class: 'k', text: 'Or by hand' }));
    }
    el.appendChild(h('ol', { class: 'steps', id: 'appsteps' }, g.steps.map(function (s) { return h('li', { text: s }); })));
    el.appendChild(h('div', { class: 'hint', id: 'appcannot', text: g.cannot }));
    el.appendChild(h('div', { class: 'hint', id: 'appsignin', text: 'Signing in: a saved app can keep its own sign-in, apart from the browser it was saved from. If the app asks for a code or the PIN once, that is why; pair it there. A code scanned with the camera opens the browser, not the app.' }));
    return el;
  }

  // On the connect screen, in an app window nobody has paired in yet.
  function pairNote(h) {
    if (!here().app) return null;
    return h('div', { class: 'msg', id: 'appnote', role: 'note',
      text: 'This app keeps its own sign-in, apart from the browser it was saved from, so it asks once more. Type the code or the PIN shown on the node\'s screen. (A QR code scanned with the camera opens the browser, not this app.)' });
  }

  // ---- the service worker: over https only --------------------------------
  // A browser refuses one over plain http, so it is not asked for there and the panel is exactly what it was.
  function register() {
    if (location.protocol !== 'https:' || !navigator.serviceWorker) return;
    try {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () { /* the panel works without it */ });
    } catch (e) { /* the same */ }
  }
  window.addEventListener('load', register);

  // ---- the colour of the browser's bar follows the look --------------------
  function colour() {
    var meta = document.querySelector('meta[name="theme-color"]');
    var bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    if (meta && /^#[0-9a-fA-F]{6}$/.test(bg) && meta.getAttribute('content') !== bg) meta.setAttribute('content', bg);
  }
  // ("load" on an element does not bubble, but it passes through the document on its way down)
  document.addEventListener('load', function (e) {
    var el = e.target;
    if (el && el.tagName === 'LINK' && /^\/theme\.css/.test(el.getAttribute('href') || '')) colour();
  }, true);

  window.mctlWebapp = { where: where, here: here, guide: guide, card: card, pairNote: pairNote, register: register, colour: colour };
})();
