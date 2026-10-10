# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel saved as an app on a phone, a tablet or a laptop (D76, docs/WEBAPP.md): what the box tells the browser.

Two things are made here from the look in use, because a look is chosen at run time and a file could only name one:

* the web app manifest (`/manifest.webmanifest`): the name, the icons, "open in a window of your own", and the
  colour of the page for the launch screen and the window's bar
* two lines of the page's head: `theme-color` (the colour a browser gives its own bar and the status bar) and the
  iPhone's status bar style

Both use the look's page colour (`bg`), which is a checked `#rrggbb` (themes.py) and is checked again here before it
is written. A browser reads the manifest when the panel is saved, and most keep what they read: an app saved under
one look keeps that look's launch colour until it is saved again. The page itself follows the look at once
(pvj/web/webapp.js sets `theme-color` again when the look's style sheet arrives).

Nothing here needs HTTPS and nothing here registers a service worker: over plain HTTP a browser refuses one.
"""
import json
import re

from . import icon, themes

PATH = "/manifest.webmanifest"
TYPE = "application/manifest+json"
NAME = "nxlx.mastercontrol"
SHORT_NAME = "NXLX control"            # under a home screen icon: launchers cut a longer one off at about 12 letters
DESCRIPTION = "The control panel of an nxlx.mastercontrol box: play clips and shaders, run the room, set the box up."
# The two lines as index.html has them, which is right for the look the box comes with: that page is served as the file is.
THEME_META = b'<meta name="theme-color" content="#121214">'
BAR_META = b'<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


def ground(theme):
    """The page colour of a look as #rrggbb, or the colour of the look the box comes with for anything else."""
    tokens = theme.get("tokens") if isinstance(theme, dict) else None
    colour = tokens.get("bg") if isinstance(tokens, dict) else None
    return colour.lower() if isinstance(colour, str) and _HEX.match(colour) else "#121214"


def is_dark(colour):
    """Whether white text reads better on it than black (the same measure the looks are checked with)."""
    return themes.contrast(colour, "#ffffff") >= themes.contrast(colour, "#000000")


def manifest(theme):
    """The manifest as JSON bytes. Every address in it is relative to the box ("/"), so it is right at the box's
    name, at its address and through any port."""
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
    body = body.replace(THEME_META, THEME_META.replace(b"#121214", colour.encode("ascii")), 1)
    # iPhone: in a dark look the status bar takes the page's own colour ("black-translucent": the page runs under
    # the bar, which the panel's style sheet keeps clear of, and the bar's text is white). In a light look that
    # white text could not be read, so "default": the system's own light bar with dark text, the page below it.
    if not is_dark(colour):
        body = body.replace(BAR_META, BAR_META.replace(b"black-translucent", b"default"), 1)
    return body
