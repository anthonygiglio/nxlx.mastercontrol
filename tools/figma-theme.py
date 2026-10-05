#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Turn design tokens exported from Figma into a theme file for the box (pvj/THEMES.md, "Make your own theme").

    tools/figma-theme.py tokens.json --id my-look --name "My look" -o my-look.json

It reads either of two shapes of JSON:

* what Figma's variables-export plugins and the W3C design-tokens format write: a nested object of names, each
  token an object with "$value" (or "value"); collections and modes may sit above the names;
* a flat object of name to value: {"colour/page": "#0b0b0d", "shape/corner": 8}.

Names are matched without regard to letter case, "colour" or "color", spaces or hyphens, and "/" or "."; the names
of the Figma file "nxlx.mastercontrol Signal UI kit", the names in pvj/THEMES.md and the code names of app.css
(bg, --bg, var(--ar-room)) all work. A name it does not know is listed at the end and changes nothing.

Whatever the tokens do not give comes from a base theme (--base, "signal" unless said otherwise). The result is
checked by the box's own validator (pvj/themes.py), the contrast of every pairing is printed, and the file is
written only if the box would accept it. Standard library only. This tool is never installed on the box.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pvj import themes  # noqa: E402

# normalised name -> (where it goes, key). The first spelling of each is the one THEMES.md documents.
NAMES = {}


def _names(where, key, *spellings):
    for s in spellings:
        NAMES[s] = (where, key)


for _key, _word, _more in (("bg", "page", ()), ("cd", "surface", ()), ("fg", "text", ()), ("mu", "muted", ("muted-text",)),
                           ("ln", "line", ()), ("ac", "accent", ()), ("on", "on-color", ("on-accent", "on"))):
    _names("tokens", _key, "color/" + _word, _key, *("color/" + m for m in _more))
for _key, _word in (("room", "room"), ("shaders", "shaders"), ("clips", "live"), ("mix", "mix"), ("system", "system")):
    _names("areas", _key, "area/" + _word, "ar-" + _key, "area/" + _key)
for _key, _word in (("off", "off"), ("setup", "setup"), ("active", "active"), ("problem", "problem"), ("error", "error-text")):
    _names("states", _key, "state/" + _word, "st-" + _key, "state/" + _key, "state/set-up" if _key == "setup" else "state/" + _key)
_names("design", "radius_control", "radius/control")
_names("design", "radius_panel", "radius/panel")
_names("design", "border_width", "border/width", "shape/rule", "rule")
_names("design", "control_height", "size/control")
_names("design", "control_height_large", "size/control-large", "size/control-big")
_names("design", "title_case", "type/title-case")
_names("design", "title_weight", "type/title-weight")
_names("design", "text_weight", "type/text-weight")
_names("design", "font_title", "font/title")
_names("design", "font_text", "font/text")
_names("design", "font_number", "font/number", "font/numbers")
_names("design", "density", "space/density", "density")
_names("design", "tabs", "style/tabs")
_names("design", "primary", "style/primary")
# one name for two tokens, and a name that only repeats another: used where the exact name was not given
SHARED = {"shape/corner": (("design", "radius_control"), ("design", "radius_panel")),
          "font/words": (("design", "font_title"), ("design", "font_text")),
          "color/danger": (("states", "problem"),)}
READY = "state/ready"                    # Ready is always the text colour on the box: there is nothing to set
# in the Figma file and of no use to a theme: passed over without a word
QUIET = re.compile(r"(space/(?!density$).+|size/(control-room|scene|slider-bar)|type/(?!title-case$|title-weight$|text-weight$).+)")
FONTS = {"archivo": "archivo", "jetbrains-mono": "jetbrains-mono", "jetbrainsmono": "jetbrains-mono", "system": "system",
         "system-ui": "system", "sans-serif": "system", "monospace": "system", "ui-monospace": "system"}
CASES = {"capitals": "capitals", "uppercase": "capitals", "upper": "capitals", "caps": "capitals", "all-caps": "capitals",
         "sentence": "sentence", "sentence-case": "sentence", "none": "sentence", "original": "sentence", "as-typed": "sentence"}


class Problem(Exception):
    pass


def norm(name):
    """A name as it is matched: var(--bg) is bg, "Colour / On colour" is color/on-color."""
    n = str(name).strip().lower()
    m = re.fullmatch(r"var\(\s*(--[a-z0-9-]+)\s*\)", n)
    if m:
        n = m.group(1)
    n = n.lstrip("-")
    n = re.sub(r"\s*[/.]\s*", "/", n)
    n = re.sub(r"[\s_]+", "-", n)
    return re.sub(r"(^|/)colour", r"\1color", n).replace("on-colour", "on-color")


def leaves(data, path=()):
    """Every token in the file as (path, value): a nested object of names with "$value" or "value" at the end, or a
    flat object of name to value. Keys that start with "$" describe a token or a group and are not names."""
    if isinstance(data, dict):
        if path and _is_colour_object(data):             # a flat name whose value is Figma's {r, g, b, a}
            yield path, data
            return
        for marker in ("$value", "value"):
            if marker in data:
                v = data[marker]
                if not isinstance(v, dict) or _is_colour_object(v) or "value" in v:
                    yield path, v
                    return
        for k, v in data.items():
            if str(k).startswith("$"):
                continue
            yield from leaves(v, path + (str(k),))
    elif path:
        yield path, data


def _is_colour_object(v):
    return isinstance(v, dict) and all(k in v for k in ("r", "g", "b"))


def colour(v):
    """#rrggbb from the ways Figma writes a colour, or Problem."""
    if _is_colour_object(v):
        if v.get("a", 1) not in (1, 1.0):
            raise Problem("a colour with transparency cannot be used (alpha %s)" % v.get("a"))
        parts = [v[k] for k in ("r", "g", "b")]
        if not all(isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1 for p in parts):
            raise Problem("not a colour: %r" % (v,))
        return "#%02x%02x%02x" % tuple(int(round(p * 255)) for p in parts)
    if not isinstance(v, str):
        raise Problem("not a colour: %r" % (v,))
    s = v.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", s):
        return s
    if re.fullmatch(r"#[0-9a-f]{3}", s):
        return "#" + "".join(c * 2 for c in s[1:])
    if re.fullmatch(r"#[0-9a-f]{8}", s):
        if s[7:] != "ff":
            raise Problem("a colour with transparency cannot be used (%s)" % s)
        return s[:7]
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,/]+(1|1\.0+|100%))?\s*\)", s)
    if m and all(int(g) <= 255 for g in m.groups()[:3]):
        return "#%02x%02x%02x" % tuple(int(g) for g in m.groups()[:3])
    raise Problem("not a colour the box can use (give #rrggbb): %r" % (v,))


def number(v):
    if isinstance(v, dict) and "value" in v:             # {"value": 8, "unit": "px"}
        v = v["value"]
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(px)?\s*", v)
        if not m:
            raise Problem("not a number: %r" % (v,))
        v = float(m.group(1))
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise Problem("not a number: %r" % (v,))
    return int(round(v))


def word(table, what):
    def read(v):
        key = norm(v) if isinstance(v, str) else None
        if key not in table:
            raise Problem("%r is not %s the box knows (one of: %s)" % (v, what, ", ".join(sorted(set(table.values())))))
        return table[key]
    return read


def choice(key):
    allowed = themes.DESIGN[key][1]

    def read(v):
        if isinstance(v, str) and v.strip().lower() in allowed:
            return v.strip().lower()
        raise Problem("%r is not one of: %s" % (v, ", ".join(allowed)))
    return read


READERS = {"radius_control": number, "radius_panel": number, "border_width": number, "control_height": number,
           "control_height_large": number, "title_weight": number, "text_weight": number,
           "title_case": word(CASES, "a case"), "font_title": word(FONTS, "a font"), "font_text": word(FONTS, "a font"),
           "font_number": word(FONTS, "a font"), "density": choice("density"), "tabs": choice("tabs"), "primary": choice("primary")}


def convert(data, base, theme_id, name, style="signal", mode=None, flags=None):
    """(theme, report) from an export. report: {"used": [(name, where.key, value)], "ignored": [...], "notes": [...]}.
    Raises Problem when the export cannot be read at all, or two tokens claim one place."""
    if not isinstance(data, dict):
        raise Problem("the export is not a JSON object")
    found = [("/".join(norm(p) for p in path), path, value) for path, value in leaves(data)]
    if not found:
        raise Problem("the export holds no tokens")
    by_path = {full: value for full, _p, value in found}
    if mode:
        want = norm(mode)
        found = [f for f in found if want in f[0].split("/")]
        if not found:
            raise Problem("no token is under the mode %r" % mode)

    def resolve(value, depth=0):
        """{colour.text} or {colour/text}: the value of that token (an alias, as the tokens format writes it)."""
        if isinstance(value, str) and re.fullmatch(r"\{[^{}]+\}", value.strip()):
            target = norm(value.strip()[1:-1])
            hits = [v for full, v in by_path.items() if full == target or full.endswith("/" + target)]
            if not hits or depth > 8:
                raise Problem("the alias %s points at nothing in the file" % value)
            return resolve(hits[0], depth + 1)
        return value

    def known(full):
        parts = full.split("/")
        for tail in ("/".join(parts[-2:]), parts[-1]):
            if tail in NAMES or tail in SHARED or tail == READY or QUIET.fullmatch(tail):
                return tail
        return None

    report = {"used": [], "ignored": [], "notes": []}
    exact, shared, claimed = {}, {}, {}
    for full, path, value in found:
        shown = "/".join(path)
        tail = known(full)
        if tail is None:
            report["ignored"].append(shown)
            continue
        if QUIET.fullmatch(tail) and tail not in NAMES:
            continue
        if tail == READY:
            report["notes"].append("%s: Ready is always the text colour on the box, so there is nothing to set" % shown)
            continue
        targets = (NAMES[tail],) if tail in NAMES else SHARED[tail]
        for where, key in targets:
            try:
                got = resolve(value)
                got = colour(got) if where != "design" else READERS[key](got)
            except Problem as e:
                raise Problem("%s: %s" % (shown, e))
            bucket = exact if tail in NAMES else shared
            if (where, key) in bucket and bucket[(where, key)][0] != got:
                raise Problem("%s and %s both give %s.%s, with different values (%s, %s); if the file holds several modes, choose one with --mode"
                              % (bucket[(where, key)][1], shown, where, key, bucket[(where, key)][0], got))
            bucket[(where, key)] = (got, shown)
    for place, (got, shown) in list(shared.items()) + list(exact.items()):      # an exact name wins over a shared one
        claimed[place] = (got, shown)
    theme = {"id": theme_id, "name": name, "style": style, "tokens": dict(base["tokens"])}
    for part in ("areas", "states"):
        if base.get(part):
            theme[part] = dict(base[part])
    design = dict(base.get("design") or {})
    for (where, key), (got, shown) in sorted(claimed.items()):
        (design if where == "design" else theme.setdefault(where, {}) if where != "tokens" else theme["tokens"])[key] = got
        report["used"].append((shown, "%s.%s" % (where, key), got))
    for key, value in (flags or {}).items():
        if value is not None:
            design[key] = value
            report["used"].append(("(option)", "design.%s" % key, value))
    # what the tokens leave open follows what they give
    tk, given = theme["tokens"], {place for place in claimed}
    if ("tokens", "ln") not in given and ("tokens", "fg") in given:
        tk["ln"] = tk["fg"]
        report["notes"].append("no line colour given: lines take the text colour")
    if ("tokens", "ac") not in given and ("areas", "room") in given:
        tk["ac"] = theme["areas"]["room"]
        report["notes"].append("no accent given: the accent (shown before a screen is open) takes the Room colour")
    if ("tokens", "on") not in given and (("tokens", "ac") in given or ("areas", "room") in given or ("tokens", "bg") in given or ("tokens", "fg") in given):
        tk["on"] = themes._best(tk["ac"], (tk["bg"], tk["fg"]))
        report["notes"].append("no colour for text on the accent given: it is the page or the text colour, whichever reads better")
    states = theme.get("states")
    if states and ("states", "error") not in given and ("states", "problem") in given:
        if min(themes.contrast(states["problem"], tk["bg"]), themes.contrast(states["problem"], tk["cd"])) >= themes.READABLE:
            states["error"] = states["problem"]
            report["notes"].append("no colour for an error line given: it takes the Problem colour")
    if design:
        theme["design"] = design
    return theme, report


def load_base(name):
    if os.path.isfile(name):
        with open(name, "rb") as f:
            return themes.checked(f.read())
    built = themes.load_themes()
    if name not in built:
        raise Problem("--base %s: not a file, and not a look that comes with the box (%s)" % (name, ", ".join(sorted(built))))
    return themes.clean(built[name])


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Turn design tokens exported from Figma into a theme file for the box.")
    ap.add_argument("export", help="the JSON exported from Figma (nested design tokens, or a flat object of name to value)")
    ap.add_argument("-o", "--output", help="where to write the theme file (default: <id>.json here)")
    ap.add_argument("--id", default="my-theme", help="the theme's id: small letters, digits and hyphens (default my-theme)")
    ap.add_argument("--name", default=None, help='the name shown on the Look page (default: made from the id)')
    ap.add_argument("--base", default="signal", help="where what the tokens do not give comes from: a look of the box, or a theme file (default signal)")
    ap.add_argument("--style", default="signal", help="the style the theme is made for (default signal)")
    ap.add_argument("--mode", help="if the export holds several modes, the one to take")
    ap.add_argument("--title-case", choices=("capitals", "sentence"), help="Figma keeps capitals on text styles, not in a variable: say it here")
    ap.add_argument("--density", choices=tuple(themes.DENSITIES))
    ap.add_argument("--tabs", choices=("filled", "outlined"))
    ap.add_argument("--primary", choices=("filled", "outlined"))
    ap.add_argument("--title-weight", type=int, choices=themes.TITLE_WEIGHTS)
    ap.add_argument("--text-weight", type=int, choices=themes.TEXT_WEIGHTS)
    a = ap.parse_args(argv)

    def say(text=""):
        print(text, file=out)
    try:
        with open(a.export, "rb") as f:
            data = json.loads(f.read().decode("utf-8-sig"))
        name = a.name or a.id.replace("-", " ").capitalize()
        flags = {"title_case": a.title_case, "density": a.density, "tabs": a.tabs, "primary": a.primary,
                 "title_weight": a.title_weight, "text_weight": a.text_weight}
        theme, report = convert(data, load_base(a.base), a.id, name, a.style, a.mode, flags)
    except (OSError, ValueError, Problem, themes.ThemeError) as e:
        say("Cannot make a theme: %s" % e)
        return 2
    say("Taken from the export:")
    for shown, place, value in report["used"]:
        say("  %-28s -> %-28s %s" % (shown, place, value))
    for note in report["notes"]:
        say("  note: %s" % note)
    form = themes.form_problems(theme)
    if form:
        say("\nThe box would refuse this theme:")
        for p in form:
            say("  %s" % themes.PLAIN.get(p, p))
    else:
        say("\nContrast (text needs %s to 1):" % themes.READABLE)
        for what, ratio in themes.pairings(theme):
            say("  %-34s %5s to 1  %s" % (what, themes._ratio(ratio), "ok" if ratio >= themes.READABLE else "TOO LOW"))
        for p in themes.contrast_problems(theme):
            say("  refused: %s" % p)
        for w in themes.warnings(theme):
            say("  warning: %s" % w)
    if report["ignored"]:
        say("\nNames in the export that mean nothing to a theme (ignored): %s" % ", ".join(sorted(report["ignored"])))
    if themes.validate(theme):
        say("\nNo file was written.")
        return 1
    path = a.output or (a.id + ".json")
    with open(path, "w") as f:
        f.write(json.dumps(theme, indent=2) + "\n")
    say("\nWrote %s. Add it on the box under System > Look, \"Add a theme\"." % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
