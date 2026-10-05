# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Themes are token files: seven colours in a small JSON file.

Built-in themes live in pvj/themes.d; user themes are dropped into the add-ons
folder (<addons>/themes), which updates never touch. Colours are strictly
validated as #rrggbb so a theme file can never inject CSS.

A theme may also name a style: one of the few looks that pvj/web/app.css knows
(STYLES). The name only chooses a block of the panel's own CSS; a theme file
never carries CSS of its own. A theme of a style with a colour per area gives
those colours under "areas", and its state colours under "states"; both are
fixed sets of names with #rrggbb values. See pvj/THEMES.md.
"""

import glob
import json
import os
import re

TOKENS = ("bg", "cd", "fg", "ln", "mu", "ac", "on")
STYLES = ("default", "signal")          # the looks app.css has a block for; "default" is the look with no block
AREAS = ("room", "shaders", "clips", "mix", "system")
STATES = ("off", "setup", "problem", "error")   # fills for three chips, and the colour of an error line of text
READABLE = 4.5                          # text against its ground, WCAG AA
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "themes.d")


class ThemeError(Exception):
    pass


def luminance(hex_colour):
    def channel(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(accent):
    """Black or white, whichever reads better on the accent colour."""
    return "#000000" if contrast(accent, "#000000") >= contrast(accent, "#ffffff") else "#ffffff"


def validate(theme):
    problems = []
    if not isinstance(theme, dict):
        return ["theme is not an object"]
    if not isinstance(theme.get("id"), str) or not _ID.fullmatch(theme["id"]):
        problems.append("bad id")
    if not isinstance(theme.get("name"), str) or not theme.get("name") or len(theme["name"]) > 40:
        problems.append("bad name")
    tokens = theme.get("tokens")
    if not isinstance(tokens, dict):
        return problems + ["tokens missing"]
    for t in TOKENS:
        if not isinstance(tokens.get(t), str) or not _HEX.fullmatch(tokens[t]):
            problems.append("token %s must be #rrggbb" % t)
    extra = set(tokens) - set(TOKENS)
    if extra:
        problems.append("unknown tokens: %s" % ", ".join(sorted(extra)))
    if "style" in theme and (not isinstance(theme["style"], str) or not _ID.fullmatch(theme["style"])):
        problems.append("bad style")                 # an unknown but well-formed name is not a problem: see style_of
    for key, names in (("areas", AREAS), ("states", STATES)):
        if key not in theme:
            continue
        group = theme[key]
        if not isinstance(group, dict):
            problems.append("%s must be an object" % key)
            continue
        for n in sorted(group, key=str):
            if n not in names:
                problems.append("unknown name in %s: %s" % (key, str(n)[:40]))
            elif not isinstance(group[n], str) or not _HEX.fullmatch(group[n]):
                problems.append("%s.%s must be #rrggbb" % (key, n))
    return problems


def style_of(theme):
    """The style a theme asks for, or "default" when it names none or one this version does not know (a theme
    written for a later version still gives its colours, in the look this version has)."""
    style = theme.get("style") if isinstance(theme, dict) else None
    return style if isinstance(style, str) and style in STYLES else "default"


def _best(colour, candidates):
    """Of the candidates, the one that reads best on the colour."""
    return max(candidates, key=lambda c: contrast(colour, c))


def _load_dir(directory, source, out, strict):
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path) as f:
                theme = json.load(f)
            problems = validate(theme)
        except (OSError, ValueError) as e:
            problems = [str(e)]
            theme = None
        if problems:
            if strict:
                raise ThemeError("%s: %s" % (os.path.basename(path), "; ".join(problems)))
            continue  # a broken add-on theme is skipped, never fatal
        if theme["id"] in out and source == "addon":
            continue  # add-ons cannot replace built-in themes
        out[theme["id"]] = dict(theme, source=source)


def load_themes(addons_dir=None):
    themes = {}
    _load_dir(BUILTIN_DIR, "builtin", themes, strict=True)
    if addons_dir:
        _load_dir(os.path.join(addons_dir, "themes"), "addon", themes, strict=False)
    return themes


def css(theme, accent=None):
    """CSS custom properties for a theme, with an optional validated accent override."""
    tokens = dict(theme["tokens"])
    if accent is not None:
        if not isinstance(accent, str) or not _HEX.fullmatch(accent):
            raise ThemeError("accent must be #rrggbb")
        if not theme.get("areas"):       # a theme with a colour per area has no one accent to replace
            tokens["ac"] = accent.lower()
            tokens["on"] = text_on(accent)
    out = ["--%s:%s" % (k, tokens[k]) for k in TOKENS]
    bg, cd, fg = (theme["tokens"][k].lower() for k in ("bg", "cd", "fg"))
    # A colour per area: the fill, the text that reads on it, and "ink": the same colour where it is readable as
    # text or as a thin line on the page and on a card, else the text colour (a yellow line on a white page is lost).
    for name in AREAS:
        c = (theme.get("areas") or {}).get(name)
        if c is None:
            continue
        c = c.lower()
        ink = c if min(contrast(c, bg), contrast(c, cd)) >= READABLE else fg
        out += ["--ar-%s:%s" % (name, c), "--ar-%s-on:%s" % (name, _best(c, (bg, fg))), "--ar-%s-ink:%s" % (name, ink)]
    for name in STATES:
        c = (theme.get("states") or {}).get(name)
        if c is None:
            continue
        c = c.lower()
        out.append("--st-%s:%s" % (name, c))
        if name != "error":
            out.append("--st-%s-on:%s" % (name, _best(c, (bg, fg))))
    return ":root{%s}" % ";".join(out)
