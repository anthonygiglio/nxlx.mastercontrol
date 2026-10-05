# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Themes the owner makes (D59): the design tokens and their limits, the contrast a theme is held to, reading an
untrusted file, and where added themes are kept (pvj/themes.py)."""
import json
import os
import re
import stat
import tempfile
import unittest

from pvj import themes

SIGNAL = themes.load_themes()["signal"]
SAMPLE = os.path.join(os.path.dirname(__file__), "..", "tools", "theme-samples", "soft-room.json")


def mine(**over):
    """A good theme of the owner's, in the style signal, with anything replaced."""
    t = {"id": "mine", "name": "Mine", "style": "signal", "tokens": dict(SIGNAL["tokens"]),
         "areas": dict(SIGNAL["areas"]), "states": dict(SIGNAL["states"])}
    t.update(over)
    return t


def read(path):
    with open(path) as f:
        return f.read()


def text(theme):
    return json.dumps(theme).encode()


class DesignTokens(unittest.TestCase):
    def test_every_token_has_a_default_and_the_defaults_are_what_signal_draws(self):
        self.assertEqual(set(themes.DESIGN), set(themes.DESIGN_DEFAULTS))
        self.assertEqual(set(themes.DESIGN), set(themes.DESIGN_WORDS))
        self.assertEqual(themes.validate(mine(design=dict(themes.DESIGN_DEFAULTS))), [])
        self.assertEqual(themes.design_of(SIGNAL), themes.DESIGN_DEFAULTS)
        self.assertEqual(themes.design_of(mine(design={"radius_control": 8}))["radius_control"], 8)
        self.assertEqual(themes.design_of(mine(design={"radius_control": 8}))["border_width"], 3)

    def test_the_built_in_themes_set_no_design_token_and_their_css_is_as_it_was(self):
        """Signal as shipped must not change: its file sets no design token, so /theme.css holds none and the
        stylesheet's own values are in force."""
        for tid, t in themes.load_themes().items():
            self.assertNotIn("design", t, tid)
            self.assertNotIn("--tk-", themes.css(t), tid)
            self.assertEqual(themes.validate(themes.clean(t)), [], tid)
            self.assertRegex(themes.css(t), r"^:root\{[-a-z0-9:#;]+\}$", tid)

    def test_each_token_is_held_to_its_range_and_its_type(self):
        for key, low, high in (("radius_control", 0, 24), ("radius_panel", 0, 24), ("border_width", 0, 4),
                               ("control_height", 44, 64), ("control_height_large", 56, 72)):
            for good in (low, high):
                extra = {"control_height_large": 72} if key == "control_height" else {}
                self.assertEqual(themes.validate(mine(design=dict({key: good}, **extra))), [], (key, good))
            for bad in (low - 1, high + 1, -1, 9999, float(low), str(low), "%dpx" % low, True, None, [low], {"v": low}):
                got = themes.validate(mine(design={key: bad}))
                self.assertEqual(len(got), 1, (key, bad))
                self.assertIn("design.%s" % key, got[0])
                self.assertIn("whole number from %d to %d" % (low, high), got[0])
        # never under 44: a finger needs that. And what staff press is never under 56 (D57).
        self.assertNotEqual(themes.validate(mine(design={"control_height": 43})), [])
        self.assertNotEqual(themes.validate(mine(design={"control_height_large": 55})), [])
        self.assertNotEqual(themes.validate(mine(design={"control_height": 60, "control_height_large": 56})), [])
        self.assertNotEqual(themes.validate(mine(design={"control_height": 60})), [])          # over Signal's own 56
        self.assertEqual(themes.validate(mine(design={"control_height": 60, "control_height_large": 60})), [])
        choices = {"density": ("compact", "regular", "roomy"), "title_case": ("capitals", "sentence"),
                   "title_weight": (400, 500, 600, 700, 800, 900), "text_weight": (400, 500, 600),
                   "font_title": ("archivo", "system"), "font_text": ("archivo", "system"),
                   "font_number": ("jetbrains-mono", "archivo", "system"), "tabs": ("filled", "outlined"),
                   "primary": ("filled", "outlined")}
        self.assertEqual(set(choices) | {"radius_control", "radius_panel", "border_width", "control_height", "control_height_large"}, set(themes.DESIGN))
        for key, good in choices.items():
            for v in good:
                self.assertEqual(themes.validate(mine(design={key: v})), [], (key, v))
            for bad in ("", "x", "Archivo", "uppercase", "filled;}", "filled\n", " filled", 450, 1000, 900.0, "900", True, None, [], {},
                        "Comic Sans MS", 'archivo",serif;}body{display:none}', "url(//evil/x.woff2)", "var(--bg)", "inherit"):
                if bad in good:
                    continue
                got = themes.validate(mine(design={key: bad}))
                self.assertEqual(len(got), 1, (key, bad))
                self.assertIn("design.%s" % key, got[0], (key, bad))
        for bad in ([], "x", 3, None, True):
            self.assertEqual(themes.validate(mine(design=bad)), ["design must be an object"], bad)
        for bad in ("radius", "css", "font-family", "--tk-r", "radius_control ", "radius_control;", 5):
            got = themes.validate(mine(design={bad: 3}))
            self.assertTrue(got and got[0].startswith("unknown name in design"), bad)
        # an outline needs a border
        for key in ("tabs", "primary"):
            got = themes.validate(mine(design={"border_width": 0, key: "outlined"}))
            self.assertEqual(len(got), 1)
            self.assertIn("cannot be outlined with a border width of 0", got[0])
        self.assertEqual(themes.validate(mine(design={"border_width": 0})), [])

    def test_what_reaches_the_page_is_numbers_and_strings_from_fixed_tables(self):
        every = {"radius_control": 10, "radius_panel": 16, "border_width": 2, "density": "roomy", "control_height": 48,
                 "control_height_large": 60, "title_case": "sentence", "title_weight": 800, "text_weight": 500,
                 "font_title": "system", "font_text": "archivo", "font_number": "system", "tabs": "outlined", "primary": "outlined"}
        self.assertEqual(themes.validate(mine(design=every)), [])
        css = themes.css(mine(design=every))
        for want in ("--tk-r:10px", "--tk-rp:16px", "--tk-rule:2px", "--tk-plain:transparent", "--tk-density:1.3", "--tk-ch:48px",
                     "--tk-chl:60px", "--tk-case:none", "--tk-track:0", "--tk-tw:800", "--tk-xw:500",
                     "--tk-tab-bg:transparent", "--tk-tab-on:var(--fg)", "--tk-tab-shadow:inset 0 6px 0 var(--ink)",
                     "--tk-pri-bg:transparent", "--tk-pri-line:var(--ink)", "--tk-pri-shadow:inset 0 -6px 0 var(--ink)"):
            self.assertIn(want + ";", css[:-1] + ";", want)
        for want in ('--tk-f-text:"Archivo",system-ui,', ";--tk-f-title:system-ui,", ";--tk-f-number:ui-monospace,"):
            self.assertIn(want, css)
        self.assertIn("--tk-plain:var(--cd)", themes.css(mine(design={"border_width": 0})))
        filled = themes.css(mine(design={"tabs": "filled", "primary": "filled", "title_case": "capitals"}))
        for want in ("--tk-tab-bg:var(--ac)", "--tk-tab-on:var(--on)", "--tk-tab-shadow:none", "--tk-pri-bg:var(--ac)", "--tk-pri-shadow:none", "--tk-case:uppercase", "--tk-track:.04em"):
            self.assertIn(want, filled)
        # only what the theme sets: one token gives its own properties and no other
        one = themes.css(mine(design={"radius_control": 6}))
        self.assertEqual(re.findall(r"--tk-[a-z-]+", one), ["--tk-r"])
        self.assertNotIn("--tk-", themes.css(mine(design={})))
        self.assertEqual(re.findall(r"--tk-[a-z-]+", themes.css(mine(design={"border_width": 1}))), ["--tk-rule", "--tk-plain"])
        # every value in the design part: a number with px, a plain number, a keyword, a var() of the panel's own, or a font stack from the table
        value = r'(?:inset 0 -?6px 0 var\(--ink\)|\d+px|\d+(?:\.\d+)?|\.04em|none|uppercase|transparent|var\(--[a-z]+\)|(?:"[A-Za-z ]+",)?(?:[a-z-]+|"[A-Za-z ]+")(?:,(?:[A-Za-z-]+|"[A-Za-z ]+"))*)'
        for part in css[len(":root{"):-1].split(";"):
            if part.startswith("--tk-"):
                self.assertRegex(part, r"^--tk-[a-z-]+:%s$" % value)
        self.assertNotRegex(css[6:-1], r"[{}<>\\]|/\*|url|@|expression|\n")
        self.assertEqual(css.count("{"), 1)
        # a design that is not good never reaches the page, even if something put it in a loaded theme
        for bad in ({"radius_control": "0;}body{display:none"}, {"font_text": 'x";}*{color:red'}, {"zz": 1}, "x", {"border_width": True}):
            self.assertNotIn("--tk-", themes.css(mine(design=bad)), bad)
            self.assertNotIn("display", themes.css(mine(design=bad)))

    def test_only_the_known_keys_and_a_name_and_id_by_whole_match(self):
        self.assertEqual(themes.validate(mine()), [])
        for extra in ("css", "source", "script", "fonts", "Design", "font-face"):
            got = themes.validate(mine(**{extra: "x"}))
            self.assertEqual(got, ["unknown keys: %s" % extra])
        for bad in ("", " x", "x" * 41, "a\nb", "My theme\n", "<b>x</b>", "a;b", 'a"b', "a/b", "naïve", 5, None, ["x"]):
            self.assertIn("bad name", themes.validate(mine(name=bad)), repr(bad))
        for good in ("M", "My theme 2", "Soft room v1.2", "x" * 40, "a-b_c"):
            self.assertEqual(themes.validate(mine(name=good)), [], good)
        for bad in ("", "a", "A1", "1a", "my theme", "my_theme", "a" * 42, "mine\n", "../x", "mine.json", 5, None):
            self.assertIn("bad id", themes.validate(mine(id=bad)), repr(bad))


class Stylesheet(unittest.TestCase):
    """The Signal block of app.css reads the tokens; with none set it must draw what it always drew."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(themes.__file__), "web", "app.css")) as f:
            css = f.read()
        self.before, block = css.split("/* ==== STYLE: signal")
        self.block = re.sub(r"/\*.*?\*/", "", "/*" + block, flags=re.S)

    def test_every_token_the_stylesheet_reads_has_the_styles_own_value_behind_it(self):
        self.assertNotIn("--tk-", self.before)                       # the default look knows nothing of them
        uses = re.findall(r"var\(--tk-[a-z-]+[^a-z-]", self.block)
        self.assertGreater(len(uses), 80)
        self.assertEqual([u for u in uses if not u.endswith(",")], [])          # never var(--tk-x) with nothing behind it
        self.assertNotRegex(self.block, r"[;{]\s*--tk-[a-z-]+\s*:")          # the stylesheet never sets one: only /theme.css does
        defaults = {}
        for name, value in re.findall(r"var\((--tk-[a-z-]+), ((?:[^()]|\([^()]*\))*?)\)", self.block):
            defaults.setdefault(name, set()).add(value)
        fonts = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
        self.assertEqual(defaults, {
            "--tk-r": {"0"}, "--tk-rp": {"0"}, "--tk-rule": {"3px"}, "--tk-plain": {"transparent"}, "--tk-density": {"1"},
            "--tk-ch": {"44px"}, "--tk-chl": {"56px"}, "--tk-case": {"uppercase"}, "--tk-track": {".04em"}, "--tk-tw": {"900"},
            "--tk-xw": {"400"}, "--tk-f-text": {'"Archivo", ' + fonts}, "--tk-f-title": {'"Archivo", ' + fonts},
            "--tk-f-number": {'"JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace'},
            "--tk-tab-bg": {"var(--ac)"}, "--tk-tab-on": {"var(--on)"}, "--tk-tab-shadow": {"none"},
            "--tk-pri-bg": {"var(--ac)"}, "--tk-pri-on": {"var(--on)"}, "--tk-pri-line": {"var(--ac)"}, "--tk-pri-shadow": {"none"}})
        # ...and those are the values the box writes for a theme that names every default: so such a theme is Signal
        every = themes.css(mine(design=dict(themes.DESIGN_DEFAULTS)))
        wrote = dict(p.split(":", 1) for p in every[len(":root{"):-1].split(";") if p.startswith("--tk-"))
        self.assertEqual(set(wrote), set(defaults))                  # what the box can write is what the stylesheet reads
        for name, value in wrote.items():
            self.assertEqual(value.replace("0px", "0").replace(" ", ""), next(iter(defaults[name])).replace(" ", ""), name)

    def test_a_staff_control_and_a_touch_target_keep_their_floor(self):
        """The heights come from the theme now; the validator, not the stylesheet, holds 44 and 56."""
        self.assertEqual(themes.DESIGN["control_height"][1], 44)
        self.assertEqual(themes.DESIGN["control_height_large"][1], 56)
        for sel in ("#roomscreen .btn", ".livecols .btn", ".tabs .btn"):
            self.assertRegex(self.block, re.escape(sel) + r"[^{}]*\{[^}]*min-height: var\(--tk-chl, 56px\)", sel)


class Contrast(unittest.TestCase):
    def test_every_built_in_theme_passes_every_pairing_it_draws(self):
        for tid, t in themes.load_themes().items():
            for what, ratio in themes.pairings(t):
                self.assertGreaterEqual(ratio, 4.5, (tid, what))
            self.assertEqual(themes.contrast_problems(t), [], tid)
            self.assertEqual(themes.warnings(t), [], tid)
        self.assertEqual(len(themes.pairings(SIGNAL)), 5 + 5 + 4 + 2)
        self.assertEqual(len(themes.pairings(themes.load_themes()["light"])), 7)

    def test_a_theme_whose_text_cannot_be_read_is_refused_with_the_pair_named(self):
        cases = (
            ({"tokens": dict(SIGNAL["tokens"], fg="#555555")}, "Text on the page is 2.6 to 1; it needs 4.5"),
            ({"tokens": dict(SIGNAL["tokens"], mu="#4a4a52")}, "Muted text on the page is 2.2 to 1; it needs 4.5"),
            ({"tokens": dict(SIGNAL["tokens"], on="#ffffff")}, "Text on the accent is 1.4 to 1; it needs 4.5"),
            ({"states": dict(SIGNAL["states"], error="#7a0010")}, "An error line on the page is 1.7 to 1; it needs 4.5"),
        )
        for over, want in cases:
            self.assertIn(want, themes.validate(mine(**over)), want)
        # a colour in the middle, on which neither the page colour nor the text colour reads: a grey page with grey text
        grey = dict(SIGNAL["tokens"], bg="#3c3c3c", cd="#444444", fg="#c8c8c8", mu="#c0c0c0", ln="#c8c8c8")
        got = themes.validate(mine(tokens=grey, areas={"room": "#808080"}, states={"off": "#7f7f7f"}))
        self.assertEqual(got, ["Text on the Room colour is 2.7 to 1; it needs 4.5", "Text on the Off colour is 2.7 to 1; it needs 4.5"])
        # the names are the panel's own words
        words = [w for w, _ in themes.pairings(mine())]
        for want in ("Text on the Room colour", "Text on the Shaders colour", "Text on the Live colour", "Text on the Mix colour",
                     "Text on the System colour", "Text on the Set up colour", "Text on the Active colour", "Text on the Problem colour"):
            self.assertIn(want, words)
        # the default look writes in the accent, so there the accent must read on the page; Signal never does
        light = themes.load_themes()["light"]
        pale = dict(themes.clean(light), id="pale", tokens=dict(light["tokens"], ac="#ffd60a", on="#000000"))
        self.assertEqual(themes.validate(pale), ["The accent as text on the page is 1.2 to 1; it needs 4.5", "The accent as text on a surface is 1.4 to 1; it needs 4.5"])
        self.assertEqual(themes.validate(dict(pale, style="signal")), [])
        # a ratio is cut, never rounded up to the limit
        self.assertEqual(themes._ratio(4.4999), "4.4")
        self.assertEqual(themes._ratio(4.46), "4.4")
        # form comes first: a file that is not a theme is not given a contrast report
        self.assertEqual(themes.validate(mine(tokens=dict(SIGNAL["tokens"], fg="red"))), ["token fg must be #rrggbb"])

    def test_a_state_colour_near_an_area_colour_is_a_warning_not_a_refusal(self):
        amber = mine(states=dict(SIGNAL["states"], setup="#ffb020"))          # Figma's first Set up: 44 from Room yellow (D57)
        self.assertEqual(themes.validate(amber), [])
        self.assertEqual(themes.warnings(amber), ["The Set up colour is close to the Room colour (44 apart in RGB; under 60 a state reads as that part of the panel)"])
        self.assertEqual(themes.warnings(mine()), [])
        self.assertEqual(len(themes.warnings(mine(states=dict(SIGNAL["states"], active="#3ddc97")))), 1)          # the Live green itself
        # the design tokens do nothing in the default look, and the owner is told
        plain = {"id": "plain", "name": "Plain", "tokens": dict(SIGNAL["tokens"]), "design": {"radius_control": 8}}
        self.assertEqual(themes.validate(plain), [])
        self.assertIn("act in the style signal only", themes.warnings(plain)[0])


class Reading(unittest.TestCase):
    def test_a_good_file_is_read_and_a_hostile_or_broken_one_is_refused_in_plain_words(self):
        self.assertEqual(themes.checked(text(mine())), mine())
        self.assertEqual(themes.checked(json.dumps(mine())), mine())
        self.assertEqual(themes.checked(b"\xef\xbb\xbf" + text(mine())), mine())            # a file saved by a Windows editor
        good = json.dumps(mine())
        cases = (
            (b"", "not valid JSON"), (b"{", "not valid JSON"), (b"[]", "a JSON object is expected"), (b'"x"', "a JSON object is expected"),
            (b"null", "a JSON object is expected"), (b"\xff\xfe{}", "not UTF-8"), (b"{}", "the id must be"),
            (good.replace('"id": "mine"', '"id": "mine", "id": "signal"').encode(), "appears twice"),
            (good.replace('"bg": "#0b0b0d"', '"bg": "#0b0b0d", "bg": "#ffffff"').encode(), "appears twice"),
            (good[:-1].encode() + b', "design": {"radius_control": 8.5}}', "whole numbers only"),
            (good[:-1].encode() + b', "design": {"radius_control": 8.0}}', "whole numbers only"),
            (good[:-1].encode() + b', "design": {"radius_control": 1e1}}', "whole numbers only"),
            (good[:-1].encode() + b', "design": {"radius_control": NaN}}', "not a number a theme may hold"),
            (good[:-1].encode() + b', "design": {"radius_control": Infinity}}', "not a number a theme may hold"),
            (good[:-1].encode() + b', "design": {"radius_control": 99999999999999999999}}', "a number is too long"),
            (good[:-1].encode() + b', "design": {"radius_control": 00008}}', "not valid JSON"),
            (b"[" * 100000, "too large"), (b"[" * 4000 + b"]" * 4000, "the file is n"),           # "nested too deeply", or (a Python that reads it) "not a theme"
            (good.encode() + b" " * themes.MAX_FILE, "too large"),
            (good[:-1].encode() + b', "css": "body{display:none}"}', "unknown keys: css"),
            (text(mine(tokens=dict(SIGNAL["tokens"], bg="#000;}body{display:none"))), "token bg must be #rrggbb"),
            (text(mine(tokens=dict(SIGNAL["tokens"], bg="#0b0b0d\n"))), "token bg must be #rrggbb"),
            (text(mine(name="<img src=x onerror=alert(1)>")), "the name must be"),
            (text(mine(id="../../etc/passwd")), "the id must be"),
            (text(mine(style="signal\n")), "the style must be"),
            (text(mine(tokens=dict(SIGNAL["tokens"], fg="#555555"))), "Text on the page is 2.6 to 1; it needs 4.5"),
        )
        for raw, want in cases:
            with self.assertRaises(themes.ThemeError) as e:
                themes.checked(raw)
            self.assertIn(want, str(e.exception), raw[:60])
            self.assertNotRegex(str(e.exception), r"[<>{}\n]", raw[:60])           # nothing of the file's own text that could be markup
        for raw in (5, None, ["x"], {"id": "mine"}):
            with self.assertRaises(themes.ThemeError):
                themes.checked(raw)
        with self.assertRaises(themes.ThemeError) as e:
            themes.checked(text(mine(id="signal")), taken=("signal",))
        self.assertIn("belongs to a look that comes with the box", str(e.exception))

    def test_the_sample_theme_in_tools_is_good_and_uses_the_range(self):
        with open(SAMPLE, "rb") as f:
            t = themes.checked(f.read())
        self.assertEqual(themes.style_of(t), "signal")
        self.assertEqual(themes.warnings(t), [])
        d = t["design"]
        self.assertGreater(d["radius_control"], 0)
        self.assertEqual((d["title_case"], d["density"]), ("sentence", "roomy"))
        self.assertNotEqual(t["areas"], SIGNAL["areas"])
        self.assertEqual(set(t["areas"]), set(themes.AREAS))
        self.assertEqual(set(t["states"]), set(themes.STATES))


class Keeping(unittest.TestCase):
    def setUp(self):
        self.addons = os.path.join(tempfile.mkdtemp(), "addons")
        self.store = themes.Store(self.addons)
        self.themes = themes.load_themes()
        self.folder = os.path.join(self.addons, "themes")

    def files(self):
        return sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []

    def test_a_theme_is_written_whole_under_its_own_id_and_read_back_the_same(self):
        self.assertFalse(self.store.add(mine(design={"radius_control": 8}), self.themes))
        self.assertEqual(self.files(), ["mine.json"])
        path = os.path.join(self.folder, "mine.json")
        self.assertTrue(stat.S_ISREG(os.lstat(path).st_mode))
        self.assertEqual(os.lstat(path).st_mode & 0o007, 0)                 # not for everyone on the box
        with open(path, "rb") as f:
            self.assertEqual(themes.checked(f.read()), mine(design={"radius_control": 8}))
        self.assertEqual(self.themes["mine"]["source"], "addon")
        again = themes.load_themes(self.addons)
        self.assertEqual(themes.clean(again["mine"]), mine(design={"radius_control": 8}))
        # the same id again replaces it (the owner changed a colour and added the file again)
        self.assertTrue(self.store.add(mine(design={"radius_control": 12}), self.themes))
        self.assertEqual(self.files(), ["mine.json"])
        self.assertEqual(themes.load_themes(self.addons)["mine"]["design"], {"radius_control": 12})
        self.store.remove("mine", self.themes)
        self.assertEqual(self.files(), [])
        self.assertNotIn("mine", self.themes)

    def test_a_built_in_look_is_never_replaced_or_removed_and_sixteen_is_the_most(self):
        for tid in ("signal", "dark-stage"):
            with self.assertRaises(themes.ThemeError) as e:
                self.store.add(mine(id=tid), self.themes)
            self.assertEqual(e.exception.status, 409)
            with self.assertRaises(themes.ThemeError) as e:
                self.store.remove(tid, self.themes)
            self.assertEqual(e.exception.status, 409)
            self.assertEqual(self.themes[tid]["source"], "builtin")
        self.assertEqual(self.files(), [])
        for bad in ("nope", None, 5, ["mine"], "../mine"):
            with self.assertRaises(themes.ThemeError) as e:
                self.store.remove(bad, self.themes)
            self.assertEqual(e.exception.status, 404)
        for i in range(themes.MAX_ADDED):
            self.store.add(mine(id="mine-%d" % i), self.themes)
        with self.assertRaises(themes.ThemeError) as e:
            self.store.add(mine(id="one-more"), self.themes)
        self.assertEqual(e.exception.status, 409)
        self.assertIn("at most 16 added themes; remove one first", str(e.exception))
        self.assertEqual(len(self.files()), 16)
        self.assertTrue(self.store.add(mine(id="mine-3", name="Changed"), self.themes))       # replacing one is not one more
        self.store.remove("mine-0", self.themes)
        self.store.add(mine(id="one-more"), self.themes)
        self.assertEqual(len(self.files()), 16)
        self.assertEqual(len(self.store.added(self.themes)), 16)
        self.assertEqual(self.store.clear(self.themes), [])
        self.assertEqual(self.files(), [])
        self.assertEqual(set(self.themes), set(themes.load_themes()))

    def test_nothing_is_written_or_read_through_a_link(self):
        outside = tempfile.mkdtemp()
        secret = os.path.join(outside, "secret.json")
        with open(secret, "w") as f:
            json.dump(mine(id="leaked"), f)
        # the themes folder itself is a link to somewhere else
        os.makedirs(self.addons)
        os.symlink(outside, self.folder)
        with self.assertRaises(themes.ThemeError) as e:
            self.store.add(mine(), self.themes)
        self.assertEqual(e.exception.status, 500)
        self.assertEqual(sorted(os.listdir(outside)), ["secret.json"])
        self.assertNotIn("leaked", themes.load_themes(self.addons))
        os.unlink(self.folder)
        # a file in the folder is a link: it is not read, and adding a theme with that id does not write through it
        os.makedirs(self.folder)
        os.symlink(secret, os.path.join(self.folder, "leaked.json"))
        self.assertNotIn("leaked", themes.load_themes(self.addons))
        before = read(secret)
        with self.assertRaises(themes.ThemeError):
            self.store.add(mine(id="leaked"), self.themes)
        self.assertEqual(read(secret), before)
        # a factory reset removes the link, never what it points at
        self.assertEqual(self.store.clear(self.themes), [])
        self.assertEqual(self.files(), [])
        self.assertEqual(read(secret), before)
        # the add-ons folder is a link
        os.rmdir(self.folder)
        os.rmdir(self.addons)
        os.symlink(outside, self.addons)
        with self.assertRaises(themes.ThemeError):
            self.store.add(mine(), self.themes)
        self.assertEqual(sorted(os.listdir(outside)), ["secret.json"])
        self.assertNotIn("leaked", themes.load_themes(self.addons))
        # a leftover of an interrupted write is in nobody's way and is never a theme
        os.unlink(self.addons)
        self.store.add(mine(), self.themes)
        with open(os.path.join(self.folder, ".adding-1-1"), "w") as f:
            f.write("{")
        self.assertEqual(set(themes.load_themes(self.addons)) - set(themes.load_themes()), {"mine"})

    def test_a_file_put_there_by_hand_is_held_to_the_same_checks(self):
        os.makedirs(self.folder)

        def put(name, obj):
            with open(os.path.join(self.folder, name), "wb") as f:
                f.write(obj if isinstance(obj, bytes) else json.dumps(obj).encode())
        put("hand.json", mine(id="by-hand"))
        put("dim.json", mine(id="dim", tokens=dict(SIGNAL["tokens"], fg="#555555")))                   # cannot be read: skipped
        put("twice.json", json.dumps(mine(id="twice")).replace('"id": "twice"', '"id": "twice", "id": "other"').encode())
        put("big.json", text(mine(id="big")) + b" " * themes.MAX_FILE)
        put("css.json", dict(mine(id="css"), css="x"))
        put("float.json", json.dumps(mine(id="float", design={"radius_control": 8})).replace("8}", "8.5}").encode())
        put("signal.json", mine(id="signal", name="Not Signal"))
        put("notes.txt", mine(id="notes"))
        os.mkfifo(os.path.join(self.folder, "pipe.json"))                                              # reading it must not hang
        os.mkdir(os.path.join(self.folder, "dir.json"))
        loaded = themes.load_themes(self.addons)
        self.assertEqual(set(loaded) - set(themes.load_themes()), {"by-hand"})
        self.assertEqual(loaded["signal"]["name"], "Signal")
        # a theme under a file name of somebody's own is removed by its id, and replaced without leaving the old file
        store, all_ = themes.Store(self.addons), themes.load_themes()
        store.load(all_)
        store.add(mine(id="by-hand", name="Again"), all_)
        self.assertNotIn("hand.json", self.files())
        self.assertIn("by-hand.json", self.files())
        store.remove("by-hand", all_)
        self.assertNotIn("by-hand.json", self.files())
        # no more than sixteen are taken, however many files there are
        for i in range(30):
            put("n%02d.json" % i, mine(id="n-%02d" % i))
        self.assertEqual(len(set(themes.load_themes(self.addons)) - set(themes.load_themes())), themes.MAX_ADDED)

    def test_a_box_with_no_add_on_folder_says_so(self):
        with self.assertRaises(themes.ThemeError) as e:
            themes.Store(None).add(mine(), self.themes)
        self.assertEqual(e.exception.status, 503)
        self.assertEqual(themes.Store(None).load({}), {})
        self.assertEqual(themes.Store(None).clear({}), [])


if __name__ == "__main__":
    unittest.main()
