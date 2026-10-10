// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The panel saved as an app (D76, docs/WEBAPP.md): the part of it that runs in the page.
//
//   where(env)     which device and browser this is and whether the page runs as an app, from what the browser
//                  reports. A plain function of its argument, so a test can ask about any device.
//   here()         where(what this browser reports)
//   guide(w)       the steps to save the panel as an app THERE, in that browser's own words for its menus, and one
//                  line about what does not work there and why. Plain data; the words are from the vendors' pages
//                  listed in docs/WEBAPP.md, and none of it was tried on a real phone or tablet.
//   card(ctx)      the card on System > Save as an app
//   pairNote(h)    a line for the connect screen when the page runs as an app that is not paired yet
//
// It also keeps the page's theme-color (the colour of the browser's own bar and of the status bar) on the look in
// use: the box writes it into the page, and when the look is changed the style sheet of the look arrives again and
// the colour is read from it here.
//
// No service worker is registered here or anywhere: the panel is plain HTTP on a private network, where a browser
// refuses one. So nothing is kept on the device, and away from the box the app shows the browser's error page.
(function () {
  'use strict';

  // ---- where am I -------------------------------------------------------
  // env: { ua: the User-Agent text, touch: navigator.maxTouchPoints, standalone: navigator.standalone (Apple only),
  //        mode: true when a display-mode of standalone, fullscreen or minimal-ui matches }
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

    if (device === 'iphone' || device === 'ipad') {
      // every browser on these is Apple's engine under another name
      browser = /CriOS/.test(ua) ? 'chrome' : /FxiOS/.test(ua) ? 'firefox' : /EdgiOS/.test(ua) ? 'edge' : /Safari/.test(ua) ? 'safari' : 'other';
    } else if (/SamsungBrowser/.test(ua)) browser = 'samsung';
    else if (/Firefox\//.test(ua)) browser = 'firefox';
    else if (/Edg(A|e)?\//.test(ua)) browser = 'edge';
    else if (/OPR\/|Opera/.test(ua)) browser = 'other';
    else if (/Chrome\//.test(ua)) browser = 'chrome';
    else if (/Safari\//.test(ua) && /Version\//.test(ua)) browser = 'safari';
    return { device: device, browser: browser, app: !!(env && (env.standalone === true || env.mode)) };
  }
  function here() {
    var mode = false;
    try {
      mode = ['standalone', 'fullscreen', 'minimal-ui'].some(function (m) { return window.matchMedia('(display-mode: ' + m + ')').matches; });
    } catch (e) { mode = false; }
    return where({ ua: navigator.userAgent, touch: navigator.maxTouchPoints, standalone: navigator.standalone, mode: mode });
  }

  // ---- the steps, per device --------------------------------------------
  var NAMES = { iphone: 'an iPhone', ipad: 'an iPad', android: 'an Android device', chromebook: 'a Chromebook', windows: 'a Windows computer', mac: 'a Mac', linux: 'a Linux computer', other: 'this device' };
  var BROWSERS = { safari: 'Safari', chrome: 'Chrome', edge: 'Edge', firefox: 'Firefox', samsung: 'Samsung Internet', other: 'this browser' };
  var OWN_SIGN_IN = 'Open the panel from its new icon and pair it once there. The app keeps its own sign-in, apart from ';
  var NO_HTTPS = 'the box is plain HTTP on your own network, not HTTPS';
  var AWAY = 'Away from the box\'s network the app cannot open: it shows an error page until this device is back on that network.';

  function guide(w) {
    var d = w.device, b = w.browser, name = BROWSERS[b] || BROWSERS.other;
    var g = { where: 'This panel is open in ' + name.replace(/^this/, 'a') + ' on ' + (NAMES[d] || NAMES.other) + '.', steps: [], cannot: AWAY, window: true };
    if (d === 'iphone' || d === 'ipad') {
      if (b === 'safari') {
        g.steps = ['Tap the Share button: a square with an arrow pointing up.', 'Scroll down and tap Add to Home Screen.',
          'If there is a switch Open as Web App, leave it on. Tap Add.', OWN_SIGN_IN + 'Safari.'];
      } else {
        g.steps = ['Open the Share menu of ' + name + '.', 'Tap Add to Home Screen. If there is no such entry, open the panel in Safari and do it there.',
          'Tap Add.', OWN_SIGN_IN + name + '.'];
      }
    } else if (d === 'android') {
      g.window = false;
      if (b === 'samsung') g.steps = ['Tap the menu: three lines at the bottom right.', 'Tap Add page to, then Home screen.', 'Tap Add.'];
      else if (b === 'chrome') g.steps = ['Tap the three dots at the top right.', 'Tap Add to Home screen.', 'Tap Add.'];
      else g.steps = ['Open the menu of ' + name + ': three dots.', 'Tap Add to Home screen.', 'Tap Add.'];
      g.cannot = 'On Android the panel does not get a window of its own: the icon opens it in ' + name + ', with the bar of ' + name + '. ' +
        (b === 'firefox' ? 'Firefox on Android does not make apps from sites.' : 'An Android browser only makes a real app from a page served over HTTPS, and ' + NO_HTTPS + '.');
    } else if (b === 'safari' && d === 'mac') {
      g.steps = ['In the menu bar choose File, then Add to Dock. Or click the Share button, then Add to Dock.', 'Type a name and click Add.', OWN_SIGN_IN + 'Safari.'];
    } else if (b === 'chrome') {
      g.steps = ['Click the three dots at the top right.', 'Choose Cast, save, and share, then Install page as app...', 'Click Install.'];
      g.cannot = 'Chrome\'s Install button in the address bar does not appear for this box: it needs HTTPS, and ' + NO_HTTPS + '. The menu does the same. ' + AWAY;
    } else if (b === 'edge') {
      g.steps = ['Click the three dots at the top right: Settings and more.', 'Choose More tools, then Apps, then Install this site as an app.', 'Click Install.'];
      g.cannot = 'Edge\'s App available button in the address bar does not appear for this box: it needs HTTPS, and ' + NO_HTTPS + '. The menu does the same. ' + AWAY;
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
  // Chrome and Edge raise this only for a page served over HTTPS, so on a box as shipped it never comes; it is kept
  // for the day the box has HTTPS. Nothing is shown by the browser itself: the card has the button.
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
    var el = h('div', { class: 'card', id: 'appcard', 'data-device': w.device, 'data-browser': w.browser, 'data-app': w.app ? '1' : '0' });
    if (w.app) {
      el.appendChild(h('h2', { text: 'This is the app' }));
      el.appendChild(h('div', { id: 'appwhere', text: 'This panel is open as an app on ' + (NAMES[w.device] || NAMES.other) + '. Nothing more to do here.' }));
      el.appendChild(h('div', { class: 'hint', id: 'appcannot', text: AWAY + ' That needs HTTPS, and ' + NO_HTTPS + '.' }));
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
    return el;
  }

  // On the connect screen, in an app window nobody has paired in yet. A code scanned with the camera opens the
  // browser, not the app, so the way in from here is to type.
  function pairNote(h) {
    if (!here().app) return null;
    return h('div', { class: 'msg', id: 'appnote', role: 'note',
      text: 'This app keeps its own sign-in, apart from the browser it was saved from, so it asks once more. Type the code or the PIN shown on the box\'s screen. (A QR code scanned with the camera opens the browser, not this app.)' });
  }

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

  window.pvjWebapp = { where: where, here: here, guide: guide, card: card, pairNote: pairNote, colour: colour };
})();
