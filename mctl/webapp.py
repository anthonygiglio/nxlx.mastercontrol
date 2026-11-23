# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel saved as an app on a phone, a tablet or a laptop (D151, docs/WEBAPP.md): what the node tells the browser.

Three things are made here, because each depends on something only the running node knows:

* the web app manifest (`/manifest.webmanifest`): the name, the icons, "open in a window of your own", and the colour
  of the page, which is the colour of the look in use (a look is chosen at run time, so a file could name only one)
* the service worker (`/sw.js`): the file in mctl/web with the name of the release written into it, so that a new
  release is a new worker and a new cache, and the old cache is deleted
* two lines of the page's head: `theme-color` (the colour a browser gives its own bar and the status bar) and the
  iPhone's status bar style

The colour is the look's page colour (`bg`), a checked `#rrggbb` (themes.py), checked again here before it is written.
A browser reads the manifest when the panel is saved and most keep what they read: an app saved under one look keeps
that look's launch colour until it is saved again. The page itself follows the look at once (mctl/web/webapp.js).

Over plain http a browser refuses a service worker, and the page does not ask for one (webapp.js): the manifest and
the page's tags are all that http gets, and they are what an iPhone, an iPad, a Mac's Safari and desktop Chrome and
Edge need to save the panel as an app.
"""
import hashlib
import json
import os
import re

from . import __version__, icon, themes

PATH = "/manifest.webmanifest"
TYPE = "application/manifest+json"
SW_PATH = "/sw.js"
SW_FILE = "sw.js"
SW_TYPE = "text/javascript; charset=utf-8"
SW_MARK = b"__RELEASE__"              # in mctl/web/sw.js, where the release's name goes
SHELL = ("away.html", "away.css")     # what the worker keeps for the day the node is out of reach (mctl/web/sw.js)
NAME = "NXLX"
SHORT_NAME = "NXLX"                   # under a home screen icon: launchers cut a longer one off at about 12 letters
DESCRIPTION = "The control panel of an NXLX node: play clips and shaders, run the room, set the node up."
FALLBACK = "#121214"                  # the page colour of the look the node comes with
# The two lines as index.html has them, which is right for the look the node comes with: that page is served as the file is.
THEME_META = b'<meta name="theme-color" content="#121214">'
BAR_META = b'<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


def ground(theme):
    """The page colour of a look as #rrggbb, or the colour of the look the node comes with for anything else."""
    tokens = theme.get("tokens") if isinstance(theme, dict) else None
    colour = tokens.get("bg") if isinstance(tokens, dict) else None
    return colour.lower() if isinstance(colour, str) and _HEX.match(colour) else FALLBACK


def is_dark(colour):
    """Whether white text reads better on it than black (the same measure the looks are checked with)."""
    return themes.contrast(colour, "#ffffff") >= themes.contrast(colour, "#000000")


def manifest(theme):
    """The manifest as JSON bytes. Every address in it is relative to the node ("/"), so it is right at the node's
    name, at its address and through any port, over http and over https."""
    colour = ground(theme)
    icons = [{"src": "/" + name, "sizes": "%dx%d" % (size, size), "type": "image/png",
              "purpose": "maskable" if scale != 1.0 else "any"} for name, (size, scale) in sorted(icon.FILES.items())]
    icons.append({"src": "/" + icon.OUTLINE, "sizes": "any", "type": "image/svg+xml", "purpose": "monochrome"})
    return json.dumps({
        "name": NAME, "short_name": SHORT_NAME, "description": DESCRIPTION, "lang": "en",
        "id": "/", "start_url": "/", "scope": "/",
        # a window of its own with no browser bars; a browser that cannot do that tries the next, then a tab
        "display": "standalone", "display_override": ["standalone", "minimal-ui"],
        "orientation": "any",
        "theme_color": colour, "background_color": colour,
        "categories": ["entertainment", "utilities"],
        "icons": icons,
    }, indent=1).encode("ascii")


def head(body, theme):
    """index.html with the look's page colour in its two lines (see the top of this file). A page that does not
    have the lines exactly as shipped is returned as it is."""
    colour = ground(theme)
    body = body.replace(THEME_META, THEME_META.replace(FALLBACK.encode("ascii"), colour.encode("ascii")), 1)
    # iPhone: in a dark look the status bar takes the page's own colour ("black-translucent": the page runs under
    # the bar, which the panel's style sheet keeps clear of, and the bar's text is white). In a light look that
    # white text could not be read, so "default": the system's own light bar with dark text, the page below it.
    if not is_dark(colour):
        body = body.replace(BAR_META, BAR_META.replace(b"black-translucent", b"default"), 1)
    return body


def release(web_dir):
    """The name of this release for the worker's cache: the version, and a short checksum of the files the worker
    keeps and of the worker itself, so a change to them is a new cache even when the version number was not raised."""
    h = hashlib.sha256()
    for name in (SW_FILE,) + SHELL:
        try:
            with open(os.path.join(web_dir, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"-")
    return "%s-%s" % (__version__, h.hexdigest()[:8])


def worker(web_dir):
    """mctl/web/sw.js with the release written into it (bytes), or None when the file is not there."""
    try:
        with open(os.path.join(web_dir, SW_FILE), "rb") as f:
            body = f.read()
    except OSError:
        return None
    return body.replace(SW_MARK, release(web_dir).encode("ascii"))
