# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import re
import tempfile
import unittest

from pvj import modules, themes
from pvj.settings import Settings


def registry(board="pi5"):
    d = tempfile.mkdtemp()
    s = Settings(os.path.join(d, "s.json"))
    s.load()
    return modules.Registry(s, board), s


class ManifestTest(unittest.TestCase):
    def test_shipped_manifests_are_valid_and_cover_requested_inputs(self):
        m = modules.load_manifests()
        for needed in ("core", "inputs-ndi", "inputs-srt", "inputs-audio-ip", "mapper"):
            self.assertIn(needed, m)
        # D30: no native ST 2110 input (1 GbE cannot carry it, no hardware PTP on a Pi 4); use a gateway
        self.assertNotIn("inputs-st2110", m)
        self.assertEqual(m["piwall"]["boards"], ["pi3"])

    def test_validation_reports_problems(self):
        good = modules.load_manifests()["mapper"]
        self.assertEqual(modules.validate_manifest(good), [])
        for key, bad in (("id", "Bad Id"), ("version", "1.x"), ("type", "weird"), ("boards", []),
                         ("channel", "gold"), ("requires", "core")):
            self.assertTrue(modules.validate_manifest(dict(good, **{key: bad})), key)
        self.assertTrue(modules.validate_manifest("nope"))

    def test_bad_directory_contents_fail_loudly(self):
        d = tempfile.mkdtemp()
        base = modules.load_manifests()["core"]
        with open(os.path.join(d, "a.json"), "w") as f:
            json.dump(dict(base, requires=["ghost"]), f)
        with self.assertRaises(modules.ModuleError):
            modules.load_manifests(d)
        with open(os.path.join(d, "a.json"), "w") as f:
            f.write("{")
        with self.assertRaises(modules.ModuleError):
            modules.load_manifests(d)


class RegistryTest(unittest.TestCase):
    def test_core_is_locked_on(self):
        reg, _ = registry()
        self.assertTrue(reg.enabled("core"))
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("core", False)
        self.assertTrue(next(m for m in reg.list() if m["id"] == "core")["locked"])

    def test_planned_modules_cannot_be_enabled_yet(self):
        reg, _ = registry()
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("inputs-ndi", True)
        self.assertFalse(reg.enabled("inputs-ndi"))

    def test_board_support_and_dependencies_enforced(self):
        reg, _ = registry("pi3")
        rows = {m["id"]: m for m in reg.list()}
        self.assertFalse(rows["mapper"]["supported"])          # the mapper needs a Pi 4 or better
        self.assertTrue(rows["piwall"]["supported"])
        # a ready, supported module with an unmet dependency: fake manifests
        manifests = modules.load_manifests()
        for k in ("mapper", "layout-import"):
            manifests[k] = dict(manifests[k], status="ready", boards=["pi5"])
        d = tempfile.mkdtemp()
        s = Settings(os.path.join(d, "s.json"))
        s.load()
        reg = modules.Registry(s, "pi5", manifests)
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("layout-import", True)  # needs mapper first
        reg.set_enabled("mapper", True)
        reg.set_enabled("layout-import", True)
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("mapper", False)  # layout-import depends on it
        reg.set_enabled("layout-import", False)
        reg.set_enabled("mapper", False)
        self.assertFalse(reg.enabled("mapper"))

    def test_switches_persist_and_unknown_or_bad_values_rejected(self):
        manifests = modules.load_manifests()
        manifests["presenter"] = dict(manifests["presenter"], status="ready")
        d = tempfile.mkdtemp()
        s = Settings(os.path.join(d, "s.json"))
        s.load()
        reg = modules.Registry(s, "pi4", manifests)
        reg.set_enabled("presenter", False)
        s2 = Settings(s.path)
        s2.load()
        self.assertFalse(modules.Registry(s2, "pi4", manifests).enabled("presenter"))
        for args in (("ghost", True), ("presenter", "yes")):
            with self.assertRaises(modules.ModuleError):
                reg.set_enabled(*args)


class ThemeTest(unittest.TestCase):
    def test_builtin_themes_valid_and_readable(self):
        t = themes.load_themes()
        self.assertEqual(set(t), {"dark-stage", "light", "night-red", "high-contrast", "signal", "signal-light"})
        for theme in t.values():
            tk = theme["tokens"]
            self.assertGreaterEqual(themes.contrast(tk["fg"], tk["bg"]), 4.5, theme["id"])
            self.assertGreaterEqual(themes.contrast(tk["on"], tk["ac"]), 4.5, theme["id"])

    def test_css_and_accent_override_with_automatic_text_colour(self):
        t = themes.load_themes()["dark-stage"]
        self.assertIn("--bg:#121214", themes.css(t))
        out = themes.css(t, "#FFFFFF")
        self.assertIn("--ac:#ffffff", out)
        self.assertIn("--on:#000000", out)
        self.assertIn("--on:#ffffff", themes.css(t, "#101010"))
        for bad in ("red", "#12345", "#gggggg", "#ffffff;}body{display:none", 5):
            with self.assertRaises(themes.ThemeError):
                themes.css(t, bad)

    def test_addon_themes_load_but_cannot_replace_builtin_or_inject(self):
        addons = tempfile.mkdtemp()
        os.makedirs(os.path.join(addons, "themes"))
        tokens = dict(themes.load_themes()["light"]["tokens"])

        def put(name, obj):
            with open(os.path.join(addons, "themes", name), "w") as f:
                f.write(obj if isinstance(obj, str) else json.dumps(obj))
        put("mine.json", {"id": "my-theme", "name": "Mine", "tokens": tokens})
        put("hijack.json", {"id": "dark-stage", "name": "Evil", "tokens": tokens})
        put("inject.json", {"id": "inject", "name": "X", "tokens": dict(tokens, bg="#000;}body{display:none")})
        put("extra.json", {"id": "extra", "name": "X", "tokens": dict(tokens, zz="#000000")})
        put("broken.json", "{")
        loaded = themes.load_themes(addons)
        self.assertEqual(loaded["my-theme"]["source"], "addon")
        self.assertEqual(loaded["dark-stage"]["name"], "Dark stage")
        for missing in ("inject", "extra"):
            self.assertNotIn(missing, loaded)

    def test_a_theme_names_a_style_from_a_fixed_set_and_never_carries_css(self):
        t = themes.load_themes()
        self.assertEqual({k: themes.style_of(v) for k, v in t.items()},
                         {"dark-stage": "default", "light": "default", "night-red": "default", "high-contrast": "default",
                          "signal": "signal", "signal-light": "signal"})
        for theme in t.values():                                  # a built-in theme never names a style this version lacks
            self.assertIn(theme.get("style", "default"), themes.STYLES, theme["id"])
        base = {"id": "mine", "name": "Mine", "tokens": dict(t["light"]["tokens"])}
        self.assertEqual(themes.validate(dict(base, style="signal")), [])
        # a name this version does not know is not an error: the theme keeps its colours and gets the default look
        self.assertEqual(themes.validate(dict(base, style="from-the-future")), [])
        self.assertEqual(themes.style_of(dict(base, style="from-the-future")), "default")
        self.assertEqual(themes.style_of(base), "default")
        for bad in (5, None, ["signal"], "", "Signal", "signal;}body{display:none", "a b", "x" * 60, "signal\n", "\nsignal"):
            self.assertIn("bad style", themes.validate(dict(base, style=bad)), repr(bad))
            self.assertEqual(themes.style_of(dict(base, style=bad)), "default", repr(bad))
        self.assertEqual(themes.style_of(None), "default")

    def test_a_trailing_newline_is_not_part_of_a_colour_or_a_name(self):
        """`$` in a pattern also matches before a newline at the end, so `match` let "#ffffff\\n" through and the
        newline reached /theme.css (found by the review). Every check is a whole match now."""
        t = themes.load_themes()
        good = {"id": "mine", "name": "Mine", "tokens": dict(t["signal"]["tokens"])}
        self.assertEqual(themes.validate(good), [])
        for bad in ("#ffffff\n", "\n#ffffff", "#ffffff\r", "#ffffff\n;}"):
            self.assertNotEqual(themes.validate(dict(good, tokens=dict(good["tokens"], bg=bad))), [], repr(bad))
            self.assertNotEqual(themes.validate(dict(good, areas={"room": bad})), [], repr(bad))
            self.assertNotEqual(themes.validate(dict(good, states={"off": bad})), [], repr(bad))
            for theme in (t["dark-stage"], t["signal"]):
                with self.assertRaises(themes.ThemeError):
                    themes.css(theme, bad)
        for bad in ("mine\n", "\nmine", "mine\r\n"):
            self.assertIn("bad id", themes.validate(dict(good, id=bad)), repr(bad))
            self.assertIn("bad style", themes.validate(dict(good, style=bad)), repr(bad))
        for tid in t:
            self.assertNotIn("\n", themes.css(t[tid]))

    def test_area_and_state_colours_are_fixed_names_with_hex_values(self):
        t = themes.load_themes()
        base = {"id": "mine", "name": "Mine", "tokens": dict(t["signal"]["tokens"])}
        self.assertEqual(themes.validate(dict(base, areas={"room": "#ffd60a"}, states={"off": "#333333"})), [])
        for key, good in (("areas", "room"), ("states", "off")):
            for bad in ({good: "red"}, {good: "#fff"}, {good: "#ffffff;}*{display:none"}, {good: 5}, {good: None},
                        {"lobby": "#ffffff"}, {"room;}": "#ffffff"}, {5: "#ffffff"}, [], "x", 3):
                self.assertNotEqual(themes.validate(dict(base, **{key: bad})), [], (key, bad))
        css = themes.css(dict(base, areas={"room": "#FFD60A"}, states={"off": "#3A3A42", "error": "#ff3b30"}))
        self.assertIn("--ar-room:#ffd60a;--ar-room-on:#0b0b0d;--ar-room-ink:#ffd60a", css)
        self.assertIn("--st-off:#3a3a42;--st-off-on:#f5f5f0", css)
        self.assertIn("--st-error:#ff3b30", css)
        self.assertNotIn("--st-error-on", css)
        self.assertNotIn("--ar-mix", css)                          # only what the theme gives
        self.assertRegex(css, r"^:root\{[-a-z0-9:#;]+\}$")         # nothing but names and colours ever reaches the page
        self.assertRegex(themes.css(t["dark-stage"]), r"^:root\{(--[a-z]{2}:#[0-9a-f]{6};?){7}\}$")   # the old themes: as before

    def test_signal_is_readable_in_the_dark_and_in_the_light(self):
        """Every pairing the Signal block of app.css draws, computed: text on the page and on a card, the text on each
        area colour and on each state fill, an error line, and the colour used for thin lines and focus rings."""
        t = themes.load_themes()
        for tid in ("signal", "signal-light"):
            theme = t[tid]
            tk, css = theme["tokens"], themes.css(theme)
            var = dict(pair.split(":") for pair in css[len(":root{"):-1].split(";"))
            self.assertEqual(set(theme["areas"]), set(themes.AREAS), tid)
            self.assertEqual(set(theme["states"]), set(themes.STATES), tid)
            for ground in (tk["bg"], tk["cd"]):
                for text in (tk["fg"], tk["mu"], var["--st-error"]):
                    self.assertGreaterEqual(themes.contrast(text, ground), 4.5, (tid, text, ground))
            for name in themes.AREAS:
                self.assertGreaterEqual(themes.contrast(var["--ar-%s-on" % name], var["--ar-" + name]), 4.5, (tid, name))
                for ground in (tk["bg"], tk["cd"]):
                    self.assertGreaterEqual(themes.contrast(var["--ar-%s-ink" % name], ground), 4.5, (tid, name, ground))
            for name in ("off", "setup", "problem"):
                self.assertGreaterEqual(themes.contrast(var["--st-%s-on" % name], var["--st-" + name]), 4.5, (tid, name))
            # the switch: the chosen half is the text colour with the page colour on it, the other half is muted text
            self.assertGreaterEqual(themes.contrast(tk["bg"], tk["fg"]), 4.5, tid)
            # an accent chosen under Look never replaces the area colours, and never breaks the file
            self.assertEqual(themes.css(theme, "#ff0000"), css)
            with self.assertRaises(themes.ThemeError):
                themes.css(theme, "red")
        # the dark one keeps large light areas out: the page and the cards are near black
        self.assertLess(themes.luminance(t["signal"]["tokens"]["bg"]), 0.01)
        self.assertLess(themes.luminance(t["signal"]["tokens"]["cd"]), 0.02)
        # a state colour is never an area colour, except Active, which is the area's colour by design (D54)
        for tid in ("signal", "signal-light"):
            fills = {t[tid]["states"][k] for k in ("off", "setup", "problem")}
            self.assertEqual(fills & set(t[tid]["areas"].values()), set(), tid)

    def test_addon_theme_with_a_style_or_bad_extras(self):
        addons = tempfile.mkdtemp()
        os.makedirs(os.path.join(addons, "themes"))
        tokens = dict(themes.load_themes()["signal"]["tokens"])
        for name, extra in (("ok", {"style": "signal", "areas": {"room": "#00ff00"}}), ("later", {"style": "neon"}),
                            ("badstyle", {"style": "x;}"}), ("badarea", {"areas": {"room": "url(x)"}})):
            with open(os.path.join(addons, "themes", name + ".json"), "w") as f:
                json.dump(dict({"id": "t-" + name, "name": name, "tokens": tokens}, **extra), f)
        loaded = themes.load_themes(addons)
        self.assertEqual(themes.style_of(loaded["t-ok"]), "signal")
        self.assertEqual(themes.style_of(loaded["t-later"]), "default")
        self.assertNotIn("t-badstyle", loaded)
        self.assertNotIn("t-badarea", loaded)

    def test_the_signal_block_of_the_stylesheet_only_ever_applies_under_its_style(self):
        """The default look must not change: every rule after the marker in app.css is scoped to the style attribute
        (the font files are only declared there, and a browser fetches a font only when a rule that applies uses it)."""
        with open(os.path.join(os.path.dirname(themes.__file__), "web", "app.css")) as f:
            css = f.read()
        marker = "/* ==== STYLE: signal"
        self.assertEqual(css.count(marker), 1)
        before, block = css.split(marker)
        self.assertNotIn("data-style", before)
        self.assertNotIn("Archivo", before)
        self.assertNotIn("fonts/", before)
        block = re.sub(r"/\*.*?\*/", "", "/*" + block, flags=re.S)
        scope = 'html[data-style="signal"]'
        depth, selector, unscoped, faces, at_media = 0, "", [], 0, False
        for ch in block:
            if ch == "{":
                sel = selector.strip()
                if depth == 0 or (depth == 1 and at_media):
                    if sel.startswith("@font-face"):
                        faces += 1
                    elif not sel.startswith("@media"):
                        unscoped += [part.strip() for part in sel.split(",") if not part.strip().startswith(scope)]
                if depth == 0:
                    at_media = sel.startswith("@media")
                depth += 1
                selector = ""
            elif ch == "}":
                depth -= 1
                selector = ""
            elif ch == ";" and depth:
                selector = ""
            else:
                selector += ch
        self.assertEqual(depth, 0)
        self.assertEqual(unscoped, [])
        self.assertEqual(faces, 2)
        fonts = os.path.join(os.path.dirname(themes.__file__), "web", "fonts")
        self.assertEqual(sorted(os.listdir(fonts)), ["OFL-Archivo.txt", "OFL-JetBrainsMono.txt", "archivo-latin.06fa7831.woff2", "jetbrains-mono-500-latin.6c95bc2f.woff2"])
        self.assertEqual(sorted(re.findall(r"url\(/fonts/([a-z0-9.-]+)\)", block)), ["archivo-latin.06fa7831.woff2", "jetbrains-mono-500-latin.6c95bc2f.woff2"])
        self.assertLess(sum(os.path.getsize(os.path.join(fonts, n)) for n in os.listdir(fonts)), 300 * 1024)
        self.assertNotRegex(block, r"https?:|//[a-z]")           # nothing from the network


if __name__ == "__main__":
    unittest.main()
