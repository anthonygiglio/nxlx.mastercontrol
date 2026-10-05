# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Themes are token files: colours and a bounded set of design tokens in a small JSON file.

Built-in themes live in pvj/themes.d; the owner's themes live in the add-ons folder (<addons>/themes), which updates
never touch. They get there through the panel (System > Look, "Add a theme"), from a settings file, or by hand.

A theme file never carries CSS. Every value is checked by its type and against a fixed set or range: a colour is
#rrggbb by a whole match, a name comes from a fixed list, a size is a whole number within its range. What reaches
/theme.css is built here from those values and from fixed tables in this file, never from the file's own text.

A theme may name a style: one of the few looks that pvj/web/app.css knows (STYLES). The name only chooses a block of
the panel's own CSS. A theme of a style with a colour per area gives those colours under "areas", its state colours
under "states", and, under "design", any of the design tokens (DESIGN): corner radius, border width, density, control
heights, the case and weight of titles, the fonts (from the ones shipped on the box) and whether the tab bar and the
primary action are filled or outlined. The style's block reads them through CSS variables whose defaults are the
style's own values, so a theme that sets none of them looks as the style always did.

A theme whose text would not be readable is refused: every pairing of text and ground the look draws is computed
(pairings) and must reach READABLE. See pvj/THEMES.md and D59.
"""

import json
import os
import re
import stat as stat_mod
import threading

TOKENS = ("bg", "cd", "fg", "ln", "mu", "ac", "on")
STYLES = ("default", "signal")          # the looks app.css has a block for; "default" is the look with no block
AREAS = ("room", "shaders", "clips", "mix", "system")
STATES = ("off", "setup", "active", "problem", "error")   # fills for four chips, and the colour of an error line of text
KEYS = ("id", "name", "style", "tokens", "areas", "states", "design")       # everything a theme file may hold
READABLE = 4.5                          # text against its ground, WCAG AA
CLOSE = 60                              # a state colour nearer than this to an area colour (in RGB) reads as that area
MAX_FILE = 16 * 1024                    # bytes: a full theme is under 1 KB
MAX_ADDED = 16                          # themes the owner may add
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_ID = re.compile(r"[a-z][a-z0-9-]{1,40}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}\.json")
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "themes.d")
FALLBACK = "dark-stage"                 # the look the box comes with

# ---- design tokens ---------------------------------------------------------------------------------------------
# The fonts a theme may name: only what is shipped in pvj/web/fonts, and the device's own. The stacks are written
# into /theme.css from here, so no font name ever comes from a theme file.
_SYSTEM = 'system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif'
_SYSTEM_MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,monospace'
TEXT_FONTS = {"archivo": '"Archivo",' + _SYSTEM, "system": _SYSTEM}
NUMBER_FONTS = {"jetbrains-mono": '"JetBrains Mono",' + _SYSTEM_MONO, "archivo": '"Archivo",' + _SYSTEM, "system": _SYSTEM_MONO}
DENSITIES = {"compact": "0.75", "regular": "1", "roomy": "1.3"}             # one scale for gaps and padding
TITLE_WEIGHTS = (400, 500, 600, 700, 800, 900)                              # what the shipped Archivo carries
TEXT_WEIGHTS = (400, 500, 600)

# name: ("int", low, high) or ("one", allowed values). The defaults are what the style "signal" draws without them.
DESIGN = {
    "radius_control": ("int", 0, 24),
    "radius_panel": ("int", 0, 24),
    "border_width": ("int", 0, 4),
    "density": ("one", tuple(DENSITIES)),
    "control_height": ("int", 44, 72),             # never under 44: a finger needs that; never over the large one
    "control_height_large": ("int", 56, 72),       # what staff press on Room and Live: never under 56 (D57)
    "title_case": ("one", ("capitals", "sentence")),
    "title_weight": ("one", TITLE_WEIGHTS),
    "text_weight": ("one", TEXT_WEIGHTS),
    "font_title": ("one", tuple(TEXT_FONTS)),
    "font_text": ("one", tuple(TEXT_FONTS)),
    "font_number": ("one", tuple(NUMBER_FONTS)),
    "tabs": ("one", ("filled", "outlined")),
    "primary": ("one", ("filled", "outlined")),
}
DESIGN_DEFAULTS = {
    "radius_control": 0, "radius_panel": 0, "border_width": 3, "density": "regular", "control_height": 44,
    "control_height_large": 56, "title_case": "capitals", "title_weight": 900, "text_weight": 400,
    "font_title": "archivo", "font_text": "archivo", "font_number": "jetbrains-mono", "tabs": "filled", "primary": "filled",
}
DESIGN_WORDS = {
    "radius_control": "the corner radius of controls", "radius_panel": "the corner radius of panels",
    "border_width": "the border width", "density": "the density", "control_height": "the height of a control",
    "control_height_large": "the height of a large control", "title_case": "the case of titles",
    "title_weight": "the weight of titles", "text_weight": "the weight of text", "font_title": "the font of titles",
    "font_text": "the font of text", "font_number": "the font of numbers", "tabs": "the tab bar",
    "primary": "the primary action",
}
AREA_WORDS = {"room": "Room", "shaders": "Shaders", "clips": "Live", "mix": "Mix", "system": "System"}
STATE_WORDS = {"off": "Off", "setup": "Set up", "active": "Active", "problem": "Problem", "error": "error"}


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


def distance(a, b):
    """How far apart two colours are in RGB (0 to 441)."""
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (1, 3, 5)) ** 0.5


def text_on(accent):
    """Black or white, whichever reads better on the accent colour."""
    return "#000000" if contrast(accent, "#000000") >= contrast(accent, "#ffffff") else "#ffffff"


def _is_hex(v):
    return isinstance(v, str) and _HEX.fullmatch(v) is not None


def _show(v, most=40):
    """A value from a theme file, made safe to put in a message."""
    return re.sub(r"[^A-Za-z0-9 ._#:,-]", "?", str(v))[:most]


def _design_problems(design):
    if not isinstance(design, dict):
        return ["design must be an object"]
    problems = []
    for key in sorted(design, key=str):
        if key not in DESIGN:
            problems.append("unknown name in design: %s" % _show(key))
            continue
        kind, v = DESIGN[key], design[key]
        if kind[0] == "int":
            if type(v) is not int or not kind[1] <= v <= kind[2]:          # type(): True is not a number here
                problems.append("design.%s (%s) must be a whole number from %d to %d" % (key, DESIGN_WORDS[key], kind[1], kind[2]))
        elif type(v) not in (int, str) or v not in kind[1]:
            problems.append("design.%s (%s) must be one of: %s" % (key, DESIGN_WORDS[key], ", ".join(str(x) for x in kind[1])))
    if problems:
        return problems
    full = dict(DESIGN_DEFAULTS, **design)
    if full["control_height"] > full["control_height_large"]:
        problems.append("design.control_height must not be more than design.control_height_large")
    if full["border_width"] == 0:
        for key in ("tabs", "primary"):
            if full[key] == "outlined":
                problems.append("design.%s cannot be outlined with a border width of 0: there would be no outline" % key)
    return problems


def form_problems(theme):
    """What is wrong with the shape of a theme: keys, types, names, ranges. Contrast is not looked at here."""
    problems = []
    if not isinstance(theme, dict):
        return ["theme is not an object"]
    extra = sorted(_show(k) for k in theme if k not in KEYS)
    if extra:
        problems.append("unknown keys: %s" % ", ".join(extra))
    if not isinstance(theme.get("id"), str) or not _ID.fullmatch(theme["id"]):
        problems.append("bad id")
    if not isinstance(theme.get("name"), str) or not _NAME.fullmatch(theme["name"]):
        problems.append("bad name")
    tokens = theme.get("tokens")
    if not isinstance(tokens, dict):
        return problems + ["tokens missing"]
    for t in TOKENS:
        if not _is_hex(tokens.get(t)):
            problems.append("token %s must be #rrggbb" % t)
    extra = sorted(_show(k) for k in tokens if k not in TOKENS)
    if extra:
        problems.append("unknown tokens: %s" % ", ".join(extra))
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
                problems.append("unknown name in %s: %s" % (key, _show(n)))
            elif not _is_hex(group[n]):
                problems.append("%s.%s must be #rrggbb" % (key, n))
    if "design" in theme:
        problems += _design_problems(theme["design"])
    return problems


def pairings(theme, accent=None):
    """Every pairing of text and ground the look draws, as [(what, ratio)], for a theme whose form is good. With
    areas and states the text on each is the page colour or the text colour, whichever reads better (as css gives)."""
    tk = {k: v.lower() for k, v in theme["tokens"].items()}
    if accent is not None and not theme.get("areas"):
        tk["ac"], tk["on"] = accent.lower(), text_on(accent)
    bg, cd, fg = tk["bg"], tk["cd"], tk["fg"]
    out = [("Text on the page", contrast(fg, bg)), ("Text on a surface", contrast(fg, cd)),
           ("Muted text on the page", contrast(tk["mu"], bg)), ("Muted text on a surface", contrast(tk["mu"], cd)),
           ("Text on the accent", contrast(tk["on"], tk["ac"]))]
    if style_of(theme) == "default" and not theme.get("areas"):
        # the default look writes an error line, and what is chosen in a list, in the accent
        out += [("The accent as text on the page", contrast(tk["ac"], bg)), ("The accent as text on a surface", contrast(tk["ac"], cd))]
    for name in AREAS:
        c = (theme.get("areas") or {}).get(name)
        if c is not None:
            out.append(("Text on the %s colour" % AREA_WORDS[name], max(contrast(c, bg), contrast(c, fg))))
    for name in STATES:
        c = (theme.get("states") or {}).get(name)
        if c is None:
            continue
        if name == "error":
            out += [("An error line on the page", contrast(c, bg)), ("An error line on a surface", contrast(c, cd))]
        else:
            out.append(("Text on the %s colour" % STATE_WORDS[name], max(contrast(c, bg), contrast(c, fg))))
    return out


def _ratio(r):
    return "%.1f" % (int(r * 10) / 10.0)        # cut, not rounded: 4.46 must not be shown as 4.5


def contrast_problems(theme):
    return ["%s is %s to 1; it needs %s" % (what, _ratio(r), READABLE) for what, r in pairings(theme) if r < READABLE]


def warnings(theme):
    """What the owner should know about a good theme, without refusing it."""
    out = []
    areas, states = theme.get("areas") or {}, theme.get("states") or {}
    for s in STATES:
        if s == "error" or s not in states:
            continue
        for a in AREAS:
            if a in areas and distance(states[s], areas[a]) < CLOSE:
                out.append("The %s colour is close to the %s colour (%d apart in RGB; under %d a state reads as that part of the panel)"
                           % (STATE_WORDS[s], AREA_WORDS[a], round(distance(states[s], areas[a])), CLOSE))
    if theme.get("design") and style_of(theme) == "default":
        out.append("The design tokens (radius, border, sizes, type) act in the style signal only; this theme does not name it, so it changes colours only")
    return out


def validate(theme):
    """Every reason this theme is refused, in plain words; empty when it is good. Form first; a theme whose form is
    good is then held to the contrast of every pairing it draws."""
    problems = form_problems(theme)
    return problems or contrast_problems(theme)


def style_of(theme):
    """The style a theme asks for, or "default" when it names none or one this version does not know (a theme
    written for a later version still gives its colours, in the look this version has)."""
    style = theme.get("style") if isinstance(theme, dict) else None
    return style if isinstance(style, str) and style in STYLES else "default"


def design_of(theme):
    """The design tokens in force for a theme: the style's own values with the theme's over them."""
    design = theme.get("design") if isinstance(theme, dict) else None
    return dict(DESIGN_DEFAULTS, **(design if isinstance(design, dict) else {}))


def _best(colour, candidates):
    """Of the candidates, the one that reads best on the colour."""
    return max(candidates, key=lambda c: contrast(colour, c))


# ---- reading a theme file ----------------------------------------------------------------------------------------
def parse(raw):
    """A theme from untrusted text or bytes: small, UTF-8, one JSON object, no key twice at any level, whole numbers
    only (no fraction, no NaN, nothing long). Raises ThemeError. The result still has to pass validate."""
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8")
        except UnicodeEncodeError:
            raise ThemeError("the file is not UTF-8 text")
    if not isinstance(raw, (bytes, bytearray)):
        raise ThemeError("a theme file is text")
    if len(raw) > MAX_FILE:
        raise ThemeError("the file is too large for a theme (at most %d KB)" % (MAX_FILE // 1024))
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise ThemeError("the file is not UTF-8 text")
    if text.startswith("﻿"):
        text = text[1:]

    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("the key %s appears twice" % _show(k))
            out[k] = v
        return out

    def constant(name):
        raise ValueError("%s is not a number a theme may hold" % name)

    def real(text):
        raise ValueError("a theme holds whole numbers only")

    def whole(text):
        if len(text) > 4:
            raise ValueError("a number is too long")
        return int(text)
    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=real, parse_int=whole)
    except RecursionError:
        raise ThemeError("the file is nested too deeply")
    except ValueError as e:
        raise ThemeError("the file is not a theme: it is not valid JSON (%s)" % _show(e, 120))
    if not isinstance(data, dict):
        raise ThemeError("the file is not a theme: a JSON object is expected")
    return data


PLAIN = {"bad id": "the id must be small letters, digits and hyphens, 2 to 41 characters, a letter first",
         "bad name": "the name must be letters, digits, spaces, dots and hyphens, at most 40 characters",
         "bad style": "the style must be a name such as signal",
         "tokens missing": "the file has no tokens (the seven colours)"}


def checked(raw, taken=()):
    """The theme in an untrusted file, or ThemeError saying in plain words why not. `taken`: ids it may not have."""
    theme = parse(raw)
    problems = validate(theme)
    if problems:
        raise ThemeError("; ".join(PLAIN.get(p, p) for p in problems))
    if theme["id"] in taken:
        raise ThemeError("the id %s belongs to a look that comes with the box; give the theme another id" % theme["id"])
    return theme


def clean(theme):
    """A theme as a file holds it: the known keys only, in a fixed order."""
    return {k: theme[k] for k in KEYS if k in theme}


# ---- where the owner's themes are kept -----------------------------------------------------------------------------
class Store:
    """The owner's themes: one small file each in <addons>/themes. The folder is fixed, a file is named by the
    theme's own id (which is a whole match of a safe pattern), nothing is read or written through a link, and a file
    is written whole beside its place and then renamed there. A file dropped in by hand is read with the same care
    and held to the same checks; one that fails them is skipped."""

    def __init__(self, addons_dir=None):
        self.addons = addons_dir
        self.dir = os.path.join(addons_dir, "themes") if addons_dir else None
        self.lock = threading.Lock()
        self.files = {}                 # id -> file name, for what load() or add() put in

    def _folder_ok(self):
        return (self.dir is not None and not os.path.islink(self.addons) and not os.path.islink(self.dir)
                and os.path.isdir(self.dir))

    def _names(self):
        if not self._folder_ok():
            return []
        try:
            return sorted(n for n in os.listdir(self.dir) if _FILE.fullmatch(n))
        except OSError:
            return []

    def _read(self, name):
        """The bytes of one file in the folder, or None: never a link, never anything but a plain small file."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(os.path.join(self.dir, name), flags)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                return None
            return os.read(fd, MAX_FILE + 1)
        except OSError:
            return None
        finally:
            os.close(fd)

    def load(self, out):
        """Add the owner's themes to `out` ({id: theme}). A file that is broken, unreadable or refused is skipped, a
        theme with the id of one already there (a built-in one) too, and no more than MAX_ADDED are taken."""
        with self.lock:
            self.files = {}
            for name in self._names():
                if len(self.files) >= MAX_ADDED:
                    break
                raw = self._read(name)
                if raw is None:
                    continue
                try:
                    theme = checked(raw)
                except ThemeError:
                    continue
                if theme["id"] in out:
                    if out[theme["id"]].get("source") == "addon":       # read before, by load_themes: remember its file
                        self.files.setdefault(theme["id"], name)
                    continue
                out[theme["id"]] = dict(theme, source="addon")
                self.files[theme["id"]] = name
        return out

    def add(self, theme, themes):
        """Store a checked theme and put it in `themes`. Returns True when it replaced one of the owner's own with
        the same id. Raises ThemeError (with .status) when it cannot be kept."""
        if self.dir is None:
            raise _status(ThemeError("this box has no folder for added themes"), 503)
        tid = theme["id"]
        with self.lock:
            had = themes.get(tid)
            if had is not None and had.get("source") != "addon":
                raise _status(ThemeError("the id %s belongs to a look that comes with the box; give the theme another id" % tid), 409)
            if had is None and sum(1 for t in themes.values() if t.get("source") == "addon") >= MAX_ADDED:
                raise _status(ThemeError("at most %d added themes; remove one first" % MAX_ADDED), 409)
            try:
                if os.path.islink(self.addons):
                    raise ThemeError("the add-ons folder is a link; refusing to write there")
                os.makedirs(self.dir, mode=0o750, exist_ok=True)
                if os.path.islink(self.dir):
                    raise ThemeError("the themes folder is a link; refusing to write there")
                final = os.path.join(self.dir, tid + ".json")
                if os.path.islink(final) or (os.path.lexists(final) and not os.path.isfile(final)):
                    raise ThemeError("refusing to replace something that is not a plain file")
                data = (json.dumps(clean(theme), indent=2) + "\n").encode("utf-8")
                tmp = os.path.join(self.dir, ".adding-%d-%d" % (os.getpid(), threading.get_ident()))
                try:
                    try:
                        os.unlink(tmp)
                    except FileNotFoundError:
                        pass
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, final)
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            except ThemeError as e:
                raise _status(e, 500)
            except OSError as e:
                raise _status(ThemeError("could not store the theme: %s" % (e.strerror or e)), 500)
            old = self.files.get(tid)
            if old and old != tid + ".json":             # the same theme under a name somebody gave it by hand
                self._unlink(old)
            self.files[tid] = tid + ".json"
            themes[tid] = dict(clean(theme), source="addon")
            return had is not None

    def _unlink(self, name):
        try:
            os.unlink(os.path.join(self.dir, name))      # a link is removed itself; what it points at is not touched
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def remove(self, tid, themes):
        """Take one of the owner's themes off the box."""
        with self.lock:
            had = themes.get(tid) if isinstance(tid, str) else None
            if had is None:
                raise _status(ThemeError("there is no such theme"), 404)
            if had.get("source") != "addon":
                raise _status(ThemeError("%s comes with the box and cannot be removed" % had.get("name", tid)), 409)
            name = self.files.get(tid, tid + ".json")
            if self._folder_ok() and not self._unlink(name):
                raise _status(ThemeError("could not remove the theme's file"), 500)
            self.files.pop(tid, None)
            del themes[tid]

    def clear(self, themes):
        """Factory reset: every added theme goes, and every other .json file in the folder with them (a broken one
        would otherwise stay for ever). Returns what could not be removed."""
        problems = []
        with self.lock:
            for name in self._names():
                if not self._unlink(name):
                    problems.append("could not remove the theme file %s" % name)
            self.files = {}
            for tid in [k for k, t in themes.items() if t.get("source") == "addon"]:
                del themes[tid]
        return problems

    def added(self, themes):
        """The owner's themes as files hold them, by id."""
        return [clean(t) for _k, t in sorted(themes.items()) if t.get("source") == "addon"]


def _status(error, status):
    error.status = status
    return error


def _load_builtin(out):
    for name in sorted(os.listdir(BUILTIN_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(BUILTIN_DIR, name), "rb") as f:
                theme = checked(f.read())
        except (OSError, ThemeError) as e:
            raise ThemeError("%s: %s" % (name, e))           # a broken built-in theme is a broken build
        out[theme["id"]] = dict(theme, source="builtin")


def load_themes(addons_dir=None):
    themes = {}
    _load_builtin(themes)
    if addons_dir:
        Store(addons_dir).load(themes)
    return themes


# ---- what reaches the page ---------------------------------------------------------------------------------------
def _design_css(design):
    """The custom properties for the design tokens a theme sets. Each value is a whole number formatted here, or a
    string from a table in this file."""
    out = []
    px = (("radius_control", "--tk-r"), ("radius_panel", "--tk-rp"), ("border_width", "--tk-rule"),
          ("control_height", "--tk-ch"), ("control_height_large", "--tk-chl"))
    for key, prop in px:
        if key in design:
            out.append("%s:%dpx" % (prop, design[key]))
    if "border_width" in design:
        # with no border a plain button would be words alone: it gets the surface colour to stand on
        out.append("--tk-plain:%s" % ("var(--cd)" if design["border_width"] == 0 else "transparent"))
    if "density" in design:
        out.append("--tk-density:%s" % DENSITIES[design["density"]])
    if "title_case" in design:
        caps = design["title_case"] == "capitals"
        out += ["--tk-case:%s" % ("uppercase" if caps else "none"), "--tk-track:%s" % (".04em" if caps else "0")]
    for key, prop in (("title_weight", "--tk-tw"), ("text_weight", "--tk-xw")):
        if key in design:
            out.append("%s:%d" % (prop, design[key]))
    for key, prop, table in (("font_title", "--tk-f-title", TEXT_FONTS), ("font_text", "--tk-f-text", TEXT_FONTS),
                             ("font_number", "--tk-f-number", NUMBER_FONTS)):
        if key in design:
            out.append("%s:%s" % (prop, table[design[key]]))
    # The open tab and a primary action: a block of the area's colour, or an outline. An outline is drawn in --ink (the
    # area colour where it reads as a line on the page, else the text colour) with a bar of it along one edge.
    if "tabs" in design:
        if design["tabs"] == "outlined":
            out += ["--tk-tab-bg:transparent", "--tk-tab-on:var(--fg)", "--tk-tab-shadow:inset 0 6px 0 var(--ink)"]
        else:
            out += ["--tk-tab-bg:var(--ac)", "--tk-tab-on:var(--on)", "--tk-tab-shadow:none"]
    if "primary" in design:
        if design["primary"] == "outlined":
            out += ["--tk-pri-bg:transparent", "--tk-pri-on:var(--fg)", "--tk-pri-line:var(--ink)", "--tk-pri-shadow:inset 0 -6px 0 var(--ink)"]
        else:
            out += ["--tk-pri-bg:var(--ac)", "--tk-pri-on:var(--on)", "--tk-pri-line:var(--ac)", "--tk-pri-shadow:none"]
    return out


def css(theme, accent=None):
    """CSS custom properties for a theme, with an optional validated accent override."""
    tokens = dict(theme["tokens"])
    if accent is not None:
        if not _is_hex(accent):
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
    design = theme.get("design")
    if isinstance(design, dict) and design and not _design_problems(design):
        out += _design_css(design)
    return ":root{%s}" % ";".join(out)
