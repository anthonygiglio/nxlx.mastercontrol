# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""tools/figma-theme.py: design tokens exported from Figma become a theme file the box accepts (D60)."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from pvj import themes

ROOT = os.path.join(os.path.dirname(__file__), "..")
TOOL = os.path.join(ROOT, "tools", "figma-theme.py")
SAMPLES = os.path.join(ROOT, "tools", "theme-samples")
spec = importlib.util.spec_from_file_location("figma_theme", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
SIGNAL = themes.clean(themes.load_themes()["signal"])


def sample(name):
    with open(os.path.join(SAMPLES, name)) as f:
        return json.load(f)


def run(data, *args):
    """(exit code, what it printed, the theme it wrote or None)."""
    tmp = tempfile.mkdtemp()
    src, dst = os.path.join(tmp, "export.json"), os.path.join(tmp, "theme.json")
    with open(src, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    out = io.StringIO()
    code = tool.main([src, "-o", dst] + list(args), out=out)
    theme = None
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            theme = themes.checked(f.read())           # what it writes is a file the box takes
    return code, out.getvalue(), theme


class Converter(unittest.TestCase):
    def test_the_export_of_the_figma_ui_kit_becomes_signal(self):
        """The variables of the Figma file "nxlx.mastercontrol Signal UI kit", as a variables-export plugin writes
        them (collection, mode, then the names; aliases; "colour" and names with spaces): the theme is Signal."""
        code, said, theme = run(sample("figma-ui-kit-export.json"), "--id", "from-figma", "--name", "From Figma")
        self.assertEqual(code, 0, said)
        self.assertEqual((theme["id"], theme["name"], theme["style"]), ("from-figma", "From Figma", "signal"))
        for part in ("tokens", "areas", "states"):
            self.assertEqual(theme[part], SIGNAL[part], part)
        self.assertEqual(theme["design"], {"radius_control": 0, "radius_panel": 0, "border_width": 3, "control_height": 44,
                                           "control_height_large": 56, "font_title": "archivo", "font_text": "archivo", "font_number": "jetbrains-mono"})
        self.assertEqual(themes.design_of(theme), themes.DESIGN_DEFAULTS)
        self.assertIn("state/ready: Ready is always the text colour", said)
        self.assertIn("Text on the Room colour", said)
        self.assertNotIn("TOO LOW", said)
        self.assertNotIn("ignored", said)              # spacing and type sizes are passed over without a word
        # Figma keeps capitals on text styles, so the case is an option here
        code, said, theme = run(sample("figma-ui-kit-export.json"), "--title-case", "sentence", "--density", "compact", "--tabs", "outlined",
                                "--primary", "outlined", "--title-weight", "700", "--text-weight", "500")
        self.assertEqual(code, 0, said)
        self.assertEqual([theme["design"][k] for k in ("title_case", "density", "tabs", "primary", "title_weight", "text_weight")],
                         ["sentence", "compact", "outlined", "outlined", 700, 500])

    def test_a_flat_file_of_names_gives_the_sample_theme(self):
        code, said, theme = run(sample("starter-flat.json"), "--id", "soft-room", "--name", "Soft room", "--density", "roomy", "--tabs", "outlined",
                                "--title-weight", "800")
        self.assertEqual(code, 0, said)
        with open(os.path.join(SAMPLES, "soft-room.json"), "rb") as f:
            shipped = themes.checked(f.read())
        self.assertEqual(theme["areas"], shipped["areas"])
        self.assertEqual({k: theme["tokens"][k] for k in ("bg", "cd", "fg", "mu", "ac", "on")}, {k: shipped["tokens"][k] for k in ("bg", "cd", "fg", "mu", "ac", "on")})
        self.assertEqual(theme["tokens"]["ln"], theme["tokens"]["fg"])               # no line colour given
        self.assertEqual(theme["states"]["error"], theme["states"]["problem"])       # no error colour given, and the Problem colour reads
        for key in ("radius_control", "radius_panel", "border_width", "density", "control_height", "control_height_large", "title_case",
                    "title_weight", "font_title", "font_text", "font_number", "tabs"):
            self.assertEqual(theme["design"][key], shipped["design"][key], key)

    def test_every_documented_name_and_its_other_spellings_land_in_the_same_place(self):
        want = {"colour/page": "tokens.bg", "colour/surface": "tokens.cd", "colour/text": "tokens.fg", "colour/muted": "tokens.mu",
                "colour/line": "tokens.ln", "colour/accent": "tokens.ac", "colour/on colour": "tokens.on",
                "area/room": "areas.room", "area/shaders": "areas.shaders", "area/live": "areas.clips", "area/mix": "areas.mix", "area/system": "areas.system",
                "state/off": "states.off", "state/setup": "states.setup", "state/active": "states.active", "state/problem": "states.problem",
                "state/error text": "states.error", "shape/rule": "design.border_width", "size/control": "design.control_height",
                "size/control big": "design.control_height_large", "font/numbers": "design.font_number",
                # the list the brief gave, which THEMES.md also documents
                "color/page": "tokens.bg", "color/on-accent": "tokens.on", "radius/control": "design.radius_control", "radius/panel": "design.radius_panel",
                "border/width": "design.border_width", "size/control-large": "design.control_height_large", "type/title-case": "design.title_case",
                "font/title": "design.font_title", "font/text": "design.font_text", "font/number": "design.font_number",
                # the code names of app.css, bare, as a custom property and as Figma's web code syntax
                "bg": "tokens.bg", "--cd": "tokens.cd", "var(--fg)": "tokens.fg", "mu": "tokens.mu", "ln": "tokens.ln", "ac": "tokens.ac", "on": "tokens.on",
                "var(--ar-room)": "areas.room", "--ar-clips": "areas.clips", "var(--st-setup)": "states.setup", "st-error": "states.error",
                # forgiving: case, spaces, hyphens, dots
                "Colour / Page": "tokens.bg", "COLOR.SURFACE": "tokens.cd", "State/Error-Text": "states.error", "size/Control_Big": "design.control_height_large",
                "Area/Live": "areas.clips", "area/clips": "areas.clips", "state/set up": "states.setup"}
        for name, place in want.items():
            where, key = place.split(".")
            self.assertEqual(tool.NAMES.get(tool.norm(name)), (where, key), name)
        self.assertEqual(tool.SHARED["shape/corner"], (("design", "radius_control"), ("design", "radius_panel")))
        self.assertEqual(tool.SHARED[tool.norm("font/words")], (("design", "font_title"), ("design", "font_text")))
        self.assertEqual(tool.SHARED[tool.norm("colour/danger")], (("states", "problem"),))
        # an exact name wins over one that stands for two; colour/danger only fills in for state/problem
        theme, _ = tool.convert({"shape/corner": 8, "radius/panel": 16, "colour/danger": "#ff5a4f"}, SIGNAL, "x", "X")
        self.assertEqual((theme["design"]["radius_control"], theme["design"]["radius_panel"], theme["states"]["problem"]), (8, 16, "#ff5a4f"))
        theme, _ = tool.convert({"colour/danger": "#ff5a4f", "state/problem": "#ff3b30"}, SIGNAL, "x", "X")
        self.assertEqual(theme["states"]["problem"], "#ff3b30")

    def test_the_ways_figma_writes_a_value(self):
        for given, want in (("#FFD60A", "#ffd60a"), ("#fd0", "#ffdd00"), ("#ffd60aff", "#ffd60a"), ("rgb(255, 214, 10)", "#ffd60a"),
                            ("rgba(255,214,10,1)", "#ffd60a"), ({"r": 1, "g": 0.5, "b": 0, "a": 1}, "#ff8000"), ({"r": 0.2, "g": 0.2, "b": 0.2}, "#333333")):
            self.assertEqual(tool.colour(given), want, given)
        for bad in ("#ffd60a80", "rgba(255,214,10,0.5)", {"r": 1, "g": 1, "b": 1, "a": 0.5}, "red", "", 5, None, "#ffd60", "url(x)", "rgb(300,0,0)",
                    {"r": 255, "g": 0, "b": 0}):
            with self.assertRaises(tool.Problem, msg=repr(bad)):
                tool.colour(bad)
        for given, want in ((8, 8), (8.0, 8), (7.6, 8), ("8", 8), ("8px", 8), (" 12 px ", 12), ({"value": 8, "unit": "px"}, 8)):
            self.assertEqual(tool.number(given), want, given)
        for bad in ("eight", "8em", True, None, [8], float("nan")):
            with self.assertRaises(tool.Problem, msg=repr(bad)):
                tool.number(bad)
        # the shapes of a file: "$value", "value", a bare value, Figma's colour object, and an alias
        data = {"colour": {"page": {"value": "#000000", "type": "color"}, "text": {"$value": {"r": 1, "g": 1, "b": 1, "a": 1}},
                           "surface": "#111111", "muted": {"$value": "{colour.text}"}, "$description": "a group"},
                "colour/line": {"r": 1, "g": 1, "b": 1}, "font/words": {"$value": "archivo"}}
        theme, report = tool.convert(data, SIGNAL, "x", "X")
        self.assertEqual([theme["tokens"][k] for k in ("bg", "fg", "cd", "mu", "ln")], ["#000000", "#ffffff", "#111111", "#ffffff", "#ffffff"])
        self.assertEqual(report["ignored"], [])
        # a list of named values, as some plugins write it
        theme, report = tool.convert([{"name": "Colour/Page", "value": "#000000"}, {"name": "shape/corner", "resolvedValue": 6},
                                      {"collection": "x", "variables": [{"name": "area/live", "$value": "#00ff88"}]}], SIGNAL, "x", "X")
        self.assertEqual((theme["tokens"]["bg"], theme["design"]["radius_control"], theme["areas"]["clips"]), ("#000000", 6, "#00ff88"))

    def test_unknown_names_are_listed_and_the_theme_is_still_made(self):
        data = dict(sample("starter-flat.json"), **{"elevation/card": 4, "motion/fast": "120ms", "colour/brand": "#123456", "space/8": 8, "type/body": 16})
        code, said, theme = run(data)
        self.assertEqual(code, 0, said)
        self.assertIn("Names in the export that mean nothing to a theme (ignored): colour/brand, elevation/card, motion/fast", said)
        self.assertNotIn("space/8", said)
        self.assertEqual(theme["id"], "my-theme")

    def test_what_the_box_would_refuse_is_said_and_no_file_is_written(self):
        code, said, theme = run(dict(sample("starter-flat.json"), **{"color/text": "#555555"}))
        self.assertEqual(code, 1)
        self.assertIsNone(theme)
        self.assertIn("refused: Text on the page is 2.4 to 1; it needs 4.5", said)
        self.assertIn("TOO LOW", said)
        self.assertIn("No file was written.", said)
        code, said, theme = run(dict(sample("starter-flat.json"), **{"radius/control": 40, "font/title": "JetBrains Mono"}))
        self.assertEqual((code, theme), (1, None))
        self.assertIn("design.radius_control (the corner radius of controls) must be a whole number from 0 to 24", said)
        self.assertIn("design.font_title (the font of titles) must be one of: archivo, system", said)
        code, said, theme = run(sample("starter-flat.json"), "--id", "Bad Id")
        self.assertEqual((code, theme), (1, None))
        self.assertIn("the id must be", said)
        # a warning does not stop it
        code, said, theme = run(dict(sample("figma-ui-kit-export.json"), **{"state/setup": "#ffb020"}))
        self.assertEqual(code, 2)                      # two tokens, two values, one place: it does not guess
        self.assertIn("both give states.setup", said)
        kit = sample("figma-ui-kit-export.json")
        kit["Signal theme"]["Signal dark"]["state"]["setup"]["$value"] = "#ffb020"
        code, said, theme = run(kit)
        self.assertEqual(code, 0)
        self.assertIn("warning: The Set up colour is close to the Room colour (44 apart", said)
        # what cannot be read at all
        for data, want in (("{", "Cannot make a theme"), ("[]", "not a JSON object"), ("{}", "holds no tokens"), ('"x"', "not a JSON object"),
                           ({"elevation/card": 4, "brand": "#123456"}, "no name in the export is one a theme uses"),
                           ([{"name": "elevation/card", "value": 4}], "no name in the export is one a theme uses"),
                           ({"colour/page": "#12"}, "colour/page: not a colour the box can use"), ({"shape/corner": "big"}, "shape/corner: not a number"),
                           ({"font/words": "Comic Sans MS"}, "is not a font the box knows"), ({"colour/page": "{colour.nowhere}"}, "points at nothing"),
                           ({"colour/page": "#ffffff80"}, "transparency")):
            code, said, theme = run(data)
            self.assertEqual((code, theme), (2, None), data)
            self.assertIn(want, said)
        code, said, theme = run(sample("starter-flat.json"), "--base", "nowhere")
        self.assertEqual(code, 2)
        self.assertIn("not a look that comes with the box", said)

    def test_a_hostile_export_ends_plainly_with_no_traceback_and_no_file(self):
        """From the review of #88: nesting 5000 deep, a number too large to hold, and terminal escapes in a name."""
        deep = '{"a":' * 5000 + '1' + '}' * 5000
        for data, want in ((deep, "Cannot make a theme"), ({"shape/corner": 1e999}, "Cannot make a theme"), ('{"shape/corner": 1e999}', "not a number a theme can use"),
                           ({"shape/corner": "9" * 400}, "not a number a theme can use"), ({"shape/corner": "1" + "0" * 400 + "px"}, "not a number a theme can use"),
                           ({"size/control": float("inf")}, "Cannot make a theme"), ({"size/control": 10 ** 400}, "not a number a theme can use")):
            code, said, theme = run(data)
            self.assertEqual((code, theme), (2, None), str(data)[:60])
            self.assertIn(want, said)
            self.assertNotIn("Traceback", said)
        # as a program: return code 2, nothing on stderr, no file
        tmp = tempfile.mkdtemp()
        for i, text in enumerate((deep, '{"shape/corner": 1e999}', '{"shape/corner": "%s"}' % ("9" * 400))):
            src, dst = os.path.join(tmp, "in%d.json" % i), os.path.join(tmp, "out%d.json" % i)
            with open(src, "w") as f:
                f.write(text)
            got = subprocess.run([sys.executable, TOOL, src, "-o", dst], capture_output=True, text=True, timeout=60)
            self.assertEqual((got.returncode, got.stderr), (2, ""), got.stdout[-300:] + got.stderr[-300:])
            self.assertFalse(os.path.exists(dst))
        # a name from the export is never printed raw: an escape sequence could redraw the terminal
        esc = "\x1b[2J\x1b]0;owned\x07evil"
        data = dict(sample("starter-flat.json"), **{esc + "/name": 4, "colour/" + esc: "#123456"})
        code, said, theme = run(data)
        self.assertEqual(code, 0, said)
        self.assertNotRegex(said, "[\x00-\x09\x0b-\x1f\x7f]")
        self.assertIn("ignored", said)
        for data in ({"colour/page": esc}, {"font/words": esc}, {"shape/corner": esc}, {"colour/page": "{" + esc + "}"}, {"colour/page": {"r": 1, "g": 1, "b": 1, "a": esc}}):
            code, said, theme = run(data)
            self.assertEqual((code, theme), (2, None))
            self.assertNotRegex(said, "[\x00-\x09\x0b-\x1f\x7f]")
        code, said, theme = run(sample("starter-flat.json"), "--mode", esc)
        self.assertEqual(code, 2)
        self.assertNotRegex(said, "[\x00-\x09\x0b-\x1f\x7f]")

    def test_two_modes_in_one_file_need_a_choice(self):
        dark = sample("figma-ui-kit-export.json")["Signal theme"]["Signal dark"]
        light = json.loads(json.dumps(dark))
        light["colour"].update({"page": {"$value": "#f2f0ea"}, "surface": {"$value": "#ffffff"}, "text": {"$value": "#0b0b0d"}, "muted": {"$value": "#4a4a52"}})
        light["state"].update({"off": {"$value": "#d6d6dc"}, "error text": {"$value": "#b00020"}})
        both = {"Signal theme": {"Signal dark": dark, "Signal light": light}}
        code, said, theme = run(both)
        self.assertEqual(code, 2)
        self.assertIn("choose one with --mode", said)
        code, said, theme = run(both, "--mode", "Signal light", "--base", "signal-light")
        self.assertEqual(code, 0, said)
        self.assertEqual(theme["tokens"]["bg"], "#f2f0ea")
        self.assertEqual(theme["states"]["error"], "#b00020")
        self.assertEqual(run(both, "--mode", "Signal dusk")[0], 2)

    def test_it_runs_as_a_program_and_is_not_shipped_to_the_box(self):
        tmp = tempfile.mkdtemp()
        got = subprocess.run([sys.executable, TOOL, os.path.join(SAMPLES, "starter-flat.json"), "--id", "cli-test", "-o", os.path.join(tmp, "t.json")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn("Wrote", got.stdout)
        with open(os.path.join(tmp, "t.json"), "rb") as f:
            self.assertEqual(themes.checked(f.read())["id"], "cli-test")
        with open(os.path.join(ROOT, "tools", "make-release.sh")) as f:
            self.assertIn('git ls-files pvj bin install', f.read())          # a release holds pvj, bin and install: never tools/
        with open(TOOL) as f:
            head = f.read(300)
        self.assertIn("SPDX-License-Identifier: Apache-2.0", head)


if __name__ == "__main__":
    unittest.main()
