# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Themes the owner adds (D60), over the API: who may add, save and remove one, what is refused and how it is said,
the remote-support tunnel, what reaches the page, and added themes in a settings file and a factory reset."""
import json
import os
import re

from pvj import boxcare, server, support as sp, themes
from pvj.api import ApiError
from tests.test_support import LAN, TUNNEL, SupportBase
from tests.test_themes import SIGNAL, mine

ADD, REMOVE, EXPORT = "/api/theme/add", "/api/theme/remove", "/api/theme/export"


class Base(SupportBase):
    def setUp(self):
        super().setUp()
        self.addons = os.path.join(self.tmp, "addons")
        self.api.theme_store = themes.Store(self.addons)
        self.folder = os.path.join(self.addons, "themes")
        self.view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]
        self.live = self.call("POST", "/api/devices/invite", {"name": "p", "role": "live"}, token=self.full)[1]["token"]
        self.api.projectors.apply = lambda: None

    def add(self, theme, token=None, **kw):
        body = {"file": theme if isinstance(theme, str) else json.dumps(theme)}
        st, out, _ = self.call("POST", ADD, body, token=token or self.full, **kw)
        return st, out

    def files(self):
        return sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []

    def css(self):
        return self.call("GET", "/theme.css")[1].decode()

    def use(self, name):
        self.assertEqual(self.call("POST", "/api/theme", {"name": name, "accent": None}, token=self.full)[0], 200)


class AddAndRemove(Base):
    def test_a_theme_is_added_shown_applied_and_removed(self):
        soft = mine(id="soft", name="Soft", design={"radius_control": 12, "title_case": "sentence"},
                    areas=dict(SIGNAL["areas"], room="#ff8a65"))
        st, out = self.add(soft)
        self.assertEqual(st, 200, out)
        self.assertEqual((out["added"], out["name"], out["replaced"], out["warnings"]), ("soft", "Soft", False, []))
        self.assertEqual(self.files(), ["soft.json"])
        # a guest and a presenter get what the panel needs to draw itself (names, styles), not every look's tokens
        for token in (self.view, self.live):
            st, less, _ = self.call("GET", "/api/theme", token=token)
            self.assertEqual(set(less), {"theme", "available"})
            self.assertEqual({t["id"]: (t["style"], t["areas"], t["source"]) for t in less["available"]}["soft"], ("signal", True, "addon"))
            self.assertTrue(all(set(t) == {"id", "name", "source", "style", "areas"} for t in less["available"]))
        st, got, _ = self.call("GET", "/api/theme", token=self.full)
        look = {t["id"]: t for t in got["available"]}
        self.assertEqual((got["max_added"], got["skipped"], got["accent_dropped"]), (16, [], ""))
        self.assertEqual((look["soft"]["source"], look["soft"]["style"], look["soft"]["areas"]), ("addon", "signal", True))
        self.assertEqual(look["signal"]["source"], "builtin")
        # what a preview is drawn from: the theme's own colours, and every design token in force
        self.assertEqual(look["soft"]["look"]["areas"]["room"], "#ff8a65")
        self.assertEqual(look["soft"]["look"]["design"], dict(themes.DESIGN_DEFAULTS, radius_control=12, title_case="sentence"))
        self.assertEqual(look["signal"]["look"]["design"], themes.DESIGN_DEFAULTS)
        self.assertIsNone(look["dark-stage"]["look"]["design"])
        self.assertEqual(set(look["dark-stage"]["look"]["tokens"]), set(themes.TOKENS))
        # applied like any look: its colours and its tokens reach the page, and the style is written into it
        self.use("soft")
        css = self.css()
        for want in ("--ar-room:#ff8a65", "--tk-r:12px", "--tk-case:none", "--tk-track:0"):
            self.assertIn(want, css)
        self.assertEqual(self.api.theme_style(), "signal")
        self.assertNotIn("--tk-rp", css)
        # removing the look in use: the box goes back to the look it came with, then the file goes
        st, out, _ = self.call("POST", REMOVE, {"id": "soft"}, token=self.full)
        self.assertEqual((st, out["removed"], out["theme"]), (200, "soft", {"name": "dark-stage", "accent": None}))
        self.assertEqual(self.files(), [])
        self.assertNotIn("soft", [t["id"] for t in out["available"]])
        self.assertEqual(self.settings.data["theme"], {"name": "dark-stage", "accent": None})
        with open(self.settings.path) as f:
            self.assertEqual(json.load(f)["theme"], {"name": "dark-stage", "accent": None})
        self.assertIn("--bg:#121214", self.css())
        self.assertEqual(self.api.theme_style(), "default")
        self.assertEqual(self.call("POST", "/api/theme", {"name": "soft", "accent": None}, token=self.full)[0], 400)
        # removing one that is not in use leaves the look alone
        self.add(soft)
        self.use("signal")
        self.assertEqual(self.call("POST", REMOVE, {"id": "soft"}, token=self.full)[0], 200)
        self.assertEqual(self.settings.data["theme"]["name"], "signal")

    def test_every_refusal_is_said_plainly_and_nothing_is_kept(self):
        good = json.dumps(mine())
        cases = (
            (mine(tokens=dict(SIGNAL["tokens"], fg="#555555")), 422, "This theme cannot be used: Text on the page is 2.6 to 1; it needs 4.5"),
            (mine(areas={"room": "#808080"}, tokens=dict(SIGNAL["tokens"], bg="#3c3c3c", cd="#444444", fg="#c8c8c8", mu="#c0c0c0"), states={}), 422,
             "Text on the Room colour is 2.7 to 1; it needs 4.5"),
            (mine(design={"radius_control": 25}), 422, "design.radius_control (the corner radius of controls) must be a whole number from 0 to 24"),
            (mine(design={"control_height": 40}), 422, "must be a whole number from 44 to 72"),
            (mine(design={"font_text": "Comic Sans MS"}), 422, "design.font_text (the font of text) must be one of: archivo, system"),
            (mine(css="body{display:none}"), 422, "unknown keys: css"),
            (mine(tokens=dict(SIGNAL["tokens"], bg="#000;}body{display:none")), 422, "token bg must be #rrggbb"),
            (mine(name="<img src=x onerror=alert(1)>"), 422, "the name must be"),
            (mine(id="../../x"), 422, "the id must be"),
            (good.replace('"id": "mine"', '"id": "mine", "id": "mine2"'), 422, "appears twice"),
            (good[:-1] + ', "design": {"radius_control": 8.5}}', 422, "whole numbers only"),
            ("{", 422, "not valid JSON"), ("[]", 422, "a JSON object is expected"), ("", 422, "not valid JSON"),
            (good + " " * themes.MAX_FILE, 413, "too large for a theme"),
            (mine(id="signal"), 409, "the id signal belongs to a look that comes with the box"),
            (mine(id="dark-stage"), 409, "belongs to a look that comes with the box"),
        )
        for theme, status, want in cases:
            st, out = self.add(theme)
            self.assertEqual(st, status, (want, out))
            self.assertIn(want, out["error"])
            self.assertNotRegex(out["error"], r"[<>{}]")
        for body in ({}, {"file": 5}, {"file": None}, {"file": mine()}, {"file": [json.dumps(mine())]}, {"theme": json.dumps(mine())}):
            st, out, _ = self.call("POST", ADD, body, token=self.full)
            self.assertEqual(st, 400, body)
        self.assertEqual(self.files(), [])
        self.assertEqual(set(self.api.themes), set(themes.load_themes()))
        self.assertEqual(self.api.themes["signal"]["name"], "Signal")
        # a warning is not a refusal, and comes back with the answer
        st, out = self.add(mine(states=dict(SIGNAL["states"], setup="#ffb020")))
        self.assertEqual(st, 200)
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("The Set up colour is close to the Room colour", out["warnings"][0])
        # the same id again replaces the owner's own; the seventeenth is refused
        st, out = self.add(mine(name="Mine again"))
        self.assertEqual((st, out["replaced"]), (200, True))
        for i in range(15):
            self.assertEqual(self.add(mine(id="t-%d" % i))[0], 200)
        st, out = self.add(mine(id="one-more"))
        self.assertEqual((st, out["error"]), (409, "at most 16 added themes; remove one first"))
        self.assertEqual(len(self.files()), 16)
        # removal: only the owner's own, only what is there
        for body, status in (({"id": "signal"}, 409), ({"id": "dark-stage"}, 409), ({"id": "nope"}, 404), ({"id": 5}, 404), ({}, 404),
                             ({"id": "../t-1"}, 404), ({"id": ["t-1"]}, 404)):
            self.assertEqual(self.call("POST", REMOVE, body, token=self.full)[0], status, body)
        self.assertEqual(len(self.files()), 16)

    def test_only_a_full_device_and_only_with_the_request_header(self):
        body = {"file": json.dumps(mine())}
        for path, send in ((ADD, body), (REMOVE, {"id": "mine"}), (EXPORT, {})):
            self.assertEqual(self.call("POST", path, send)[0], 401, path)
            for token in (self.view, self.live):
                self.assertEqual(self.call("POST", path, send, token=token)[0], 403, path)
            self.assertEqual(self.call("POST", path, send, token=self.full, csrf=False)[0], 403, path)
            self.assertEqual(self.call("POST", path, send, token=self.full, headers={"Origin": "http://evil.example"})[0], 403, path)
            self.assertEqual(self.call("GET", path, token=self.full)[0], 405, path)
            st, _, r = self.call("POST", path, raw=json.dumps(send).encode(), token=self.full, headers={"Content-Type": "text/plain"})
            self.assertEqual(st, 415, path)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.call("POST", ADD, body, token=self.full)[0], 200)
        # the body of a request is small whatever it holds
        big = json.dumps({"file": "x" * (server.MAX_BODY + 10)}).encode()
        self.assertEqual(self.call("POST", ADD, raw=big, token=self.full)[0], 413)
        # a box with no folder for them says so
        self.api.theme_store = themes.Store(None)
        st, out = self.add(mine(id="other"))
        self.assertEqual((st, out["error"]), (503, "this box has no folder for added themes"))

    def test_not_through_the_remote_support_tunnel(self):
        self.ready()
        code = self.start(role="full")[1]["code"]
        dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
        self.assertEqual((dev["role"], dev.get("remote")), ("full", True))
        self.assertEqual(self.add(mine())[0], 200)
        for path, body in ((ADD, {"file": json.dumps(mine(id="remote"))}), (REMOVE, {"id": "mine"})):
            st, out = self.h("POST", path, body, dev, TUNNEL)
            self.assertEqual(st, 403, path)
            self.assertIn("remote support", out["error"])
            self.assertIn(("POST", path), sp.REMOTE_DENY)
        self.assertEqual(self.files(), ["mine.json"])
        # the handlers refuse by themselves too, should the table ever change
        for call, body in ((self.api.add_theme, {"file": json.dumps(mine(id="remote"))}), (self.api.remove_theme, {"id": "mine"})):
            with self.assertRaises(ApiError) as e:
                call(body, dev, TUNNEL)
            self.assertEqual(e.exception.status, 403)
        self.assertEqual(self.files(), ["mine.json"])
        # support may still choose a look and save one as a file: neither writes a file on the box
        self.assertEqual(self.h("POST", "/api/theme", {"name": "mine", "accent": None}, dev, TUNNEL)[0], 200)
        self.assertEqual(self.h("POST", EXPORT, {}, dev, TUNNEL)[0], 200)
        # and at the studio a full device may
        self.assertEqual(self.h("POST", REMOVE, {"id": "mine"}, self.full_dev, LAN)[0], 200)

    def test_a_look_saved_as_a_file_can_be_added_back_and_signal_is_a_starting_point(self):
        self.use("signal")
        st, out, _ = self.call("POST", EXPORT, {}, token=self.full)
        self.assertEqual((st, out["name"]), (200, "nxlx-theme-my-signal.json"))
        file = out["file"]
        self.assertEqual((file["id"], file["name"], file["style"]), ("my-signal", "My Signal", "signal"))
        self.assertEqual(file["design"], themes.DESIGN_DEFAULTS)              # every token is there to change
        self.assertEqual((file["tokens"], file["areas"], file["states"]), (SIGNAL["tokens"], SIGNAL["areas"], SIGNAL["states"]))
        self.assertEqual(set(file), set(themes.KEYS))
        # as the panel saves it (pretty-printed), it is a file the box takes back, and it draws what Signal draws
        st, added = self.add(json.dumps(file, indent=2) + "\n")
        self.assertEqual(st, 200, added)
        signal_css = self.css()
        self.use("my-signal")
        mine_css = self.css()
        self.assertTrue(mine_css.startswith(signal_css[:-1] + ";--tk-"))
        # an added theme is saved under its own id, and by id without being in use
        st, out, _ = self.call("POST", EXPORT, {"id": "my-signal"}, token=self.full)
        self.assertEqual((out["name"], out["file"]), ("nxlx-theme-my-signal.json", file))
        st, out, _ = self.call("POST", EXPORT, {"id": "light"}, token=self.full)
        self.assertEqual((out["file"]["id"], out["file"]["name"]), ("my-light", "My Light"))
        self.assertNotIn("design", out["file"])
        self.assertNotIn("source", out["file"])
        self.assertEqual(self.add(out["file"])[0], 200)
        for bad in ("nope", 5, ["light"], "../light"):
            self.assertEqual(self.call("POST", EXPORT, {"id": bad}, token=self.full)[0], 404, bad)
        # an accent chosen under Look is in the file when it can be read; a faint one is left out, and the file says so
        self.assertEqual(self.call("POST", "/api/theme", {"name": "dark-stage", "accent": "#22D3EE"}, token=self.full)[0], 200)
        st, out, _ = self.call("POST", EXPORT, {}, token=self.full)
        self.assertEqual((out["file"]["tokens"]["ac"], out["file"]["tokens"]["on"], out["note"]), ("#22d3ee", "#000000", ""))
        self.assertEqual(themes.validate(out["file"]), [])
        self.settings.data["theme"] = {"name": "dark-stage", "accent": "#222222"}       # kept from before accents were checked
        st, out, _ = self.call("POST", EXPORT, {}, token=self.full)
        self.assertEqual(out["file"]["tokens"], self.api.themes["dark-stage"]["tokens"])
        self.assertIn("cannot be read on this look", out["note"])
        self.assertEqual(themes.validate(out["file"]), [])
        # a long id still gives an id the box accepts
        self.add(mine(id="a" * 41))
        self.assertEqual(self.call("POST", EXPORT, {"id": "a" * 41}, token=self.full)[1]["file"]["id"], "a" * 41)

    def test_a_theme_that_was_damaged_on_the_box_gives_the_default_look(self):
        self.add(mine(design={"radius_control": 8}))
        self.use("mine")
        self.assertEqual(self.api.theme_style(), "signal")
        for damage in (b"{", json.dumps(mine(tokens=dict(SIGNAL["tokens"], fg="#555555"))).encode(),
                       json.dumps(dict(mine(), css="x")).encode(), b""):
            with open(os.path.join(self.folder, "mine.json"), "wb") as f:
                f.write(damage)
            # the box starts again: the theme is skipped, and the page and its colours are the look it came with
            all_ = themes.load_themes()
            self.api.theme_store = themes.Store(self.addons)
            self.api.theme_store.load(all_)
            self.api.themes.clear()
            self.api.themes.update(all_)
            self.assertNotIn("mine", self.api.themes)
            self.assertEqual(self.api.theme_style(), "default")
            self.assertRegex(self.css(), r"^:root\{--bg:#121214;[-a-z0-9:#;]+\}$")
            self.assertEqual(self.call("GET", "/")[0], 200)
        # and when the file is good again it is the look again, with no setting touched
        os.unlink(os.path.join(self.folder, "mine.json"))
        self.assertEqual(self.add(mine(design={"radius_control": 8}))[0], 200)
        self.assertEqual(self.api.theme_style(), "signal")
        self.assertIn("--tk-r:8px", self.css())


class Accent(Base):
    """An accent chosen under Look replaces a theme's own, so it is held to what a theme is held to (review of #88)."""

    def test_an_accent_that_cannot_be_read_is_refused_and_the_pair_is_named(self):
        for name, accent, want in (("dark-stage", "#121214", "The accent as text on the page is 1.0 to 1; it needs 4.5"),
                                   ("light", "#ffffff", "The accent as text on the page is 1.1 to 1; it needs 4.5"),
                                   ("light", "#f59e0b", "The accent as text on the page"), ("night-red", "#c2410c", "The accent as text"),
                                   ("high-contrast", "#c2410c", "The accent as text"), ("dark-stage", "#c2410c", "The accent as text")):
            before = dict(self.settings.data["theme"])
            st, out, _ = self.call("POST", "/api/theme", {"name": name, "accent": accent}, token=self.full)
            self.assertEqual(st, 400, (name, accent))
            self.assertIn("This accent cannot be used with", out["error"])
            self.assertIn(want, out["error"])
            self.assertEqual(self.settings.data["theme"], before)
        # every swatch the panel has is accepted on at least one look, and the ones that pass are accepted
        swatches = ("#f59e0b", "#c2410c", "#22d3ee", "#e879f9", "#a3e635", "#ffffff")
        passing = {}
        for name in ("dark-stage", "light", "night-red", "high-contrast"):
            passing[name] = [c for c in swatches if not themes.accent_problems(self.api.themes[name], c)]
            for c in swatches:
                st = self.call("POST", "/api/theme", {"name": name, "accent": c}, token=self.full)[0]
                self.assertEqual(st, 200 if c in passing[name] else 400, (name, c))
        self.assertEqual(passing["light"], ["#c2410c"])
        self.assertEqual(passing["dark-stage"], ["#f59e0b", "#22d3ee", "#e879f9", "#a3e635", "#ffffff"])
        self.assertTrue(all(passing.values()))
        # a theme with a colour per area has no accent to replace: whatever is sent changes nothing it draws
        self.assertEqual(self.call("POST", "/api/theme", {"name": "signal", "accent": "#0b0b0d"}, token=self.full)[0], 200)
        self.assertEqual(themes.accent_problems(self.api.themes["signal"], "#0b0b0d"), [])
        self.assertEqual(themes.accent_problems(self.api.themes["light"], None), [])
        self.assertEqual(themes.accent_problems(self.api.themes["light"], "red"), ["accent must be #rrggbb"])

    def test_a_faint_accent_in_a_settings_file_refuses_the_import(self):
        st, out = self.h("POST", "/api/system/settings/export", {}, self.full_dev)
        file = out["file"]
        before = json.dumps(self.settings.data, sort_keys=True)
        for theme in ({"name": "light", "accent": "#ffffff"}, {"name": "dark-stage", "accent": "#121214"}):
            file["settings"]["theme"] = theme
            st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(file).encode(), token=self.full)
            self.assertEqual(st, 400, theme)
            self.assertIn("theme: this accent cannot be used with the theme", body["error"])
            self.assertIn("The accent as text on the page is", body["error"])
            self.assertEqual(json.dumps(self.settings.data, sort_keys=True), before)
        file["settings"]["theme"] = {"name": "light", "accent": "#c2410c"}
        self.assertEqual(self.call("POST", "/api/system/settings/import?confirm=import", raw=json.dumps(file).encode(), token=self.full)[0], 200)
        self.assertEqual(self.settings.data["theme"], {"name": "light", "accent": "#c2410c"})

    def test_a_faint_accent_already_in_the_settings_is_dropped_not_fatal_and_the_look_page_is_told(self):
        light = themes.css(self.api.themes["light"])
        for stored in ("#ffffff", "#f4f3ef", "red", 5, ["#c2410c"], "#fff;}body{display:none"):
            self.settings.data["theme"] = {"name": "light", "accent": stored}
            self.assertEqual(self.css(), light, stored)                         # the theme's own accent, as if none was chosen
            self.assertEqual(self.call("GET", "/")[0], 200)
            st, got, _ = self.call("GET", "/api/theme", token=self.full)
            self.assertEqual(st, 200)
            self.assertIn("The accent kept in the settings is not used, and Light has its own", got["accent_dropped"], stored)
            self.assertNotRegex(got["accent_dropped"], r"[<>{}]")
            self.assertNotIn("accent_dropped", self.call("GET", "/api/theme", token=self.view)[1])
        self.settings.data["theme"] = {"name": "light", "accent": "#c2410c"}
        self.assertEqual(self.call("GET", "/api/theme", token=self.full)[1]["accent_dropped"], "")
        self.assertIn("--ac:#c2410c", self.css())


class Files(Base):
    """What the review of #88 found in how theme files on the box are handled."""

    def put(self, name, theme):
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(theme if isinstance(theme, bytes) else json.dumps(theme).encode())

    def restart(self):
        """The box starts again: the looks it comes with, and what its store reads from the folder."""
        self.api.theme_store = themes.Store(self.addons)
        self.api.themes.clear()
        self.api.themes.update(themes.load_themes())
        self.api.theme_store.load(self.api.themes)

    def test_two_files_with_one_id_are_one_theme_and_removing_it_removes_both(self):
        self.put("a-old.json", mine(name="Old copy"))
        self.put("z-new.json", mine(name="New copy"))
        self.restart()
        self.assertEqual(self.api.themes["mine"]["name"], "Old copy")
        self.assertEqual([k["file"] for k in self.api.theme_store.skipped], ["z-new.json"])
        self.assertIn("it has the same id, mine, as a-old.json", self.api.theme_store.skipped[0]["why"])
        self.assertEqual(self.call("POST", REMOVE, {"id": "mine"}, token=self.full)[0], 200)
        self.assertEqual(self.files(), [])
        self.restart()
        self.assertNotIn("mine", self.api.themes)                               # it is not back from the other file
        self.assertEqual(self.api.theme_store.skipped, [])
        # the file the panel wrote wins over a hand-named copy, whatever the names sort to; adding again leaves one file
        self.put("a-old.json", mine(name="Old copy"))
        self.put("mine.json", mine(name="From the panel"))
        self.put("b-older.json", mine(name="Older copy"))
        self.restart()
        self.assertEqual(self.api.themes["mine"]["name"], "From the panel")
        self.assertEqual(sorted(k["file"] for k in self.api.theme_store.skipped), ["a-old.json", "b-older.json"])
        self.assertEqual(self.add(mine(name="Newest"))[0], 200)
        self.assertEqual(self.files(), ["mine.json"])
        self.assertEqual(self.call("GET", "/api/theme", token=self.full)[1]["skipped"], [])
        self.restart()
        self.assertEqual(self.api.themes["mine"]["name"], "Newest")
        # a factory-style clear takes every copy
        self.put("copy.json", mine())
        self.restart()
        self.assertEqual(self.api.theme_store.clear(self.api.themes), [])
        self.assertEqual(self.files(), [])

    def test_a_theme_file_that_is_not_used_is_logged_and_shown_to_the_owner(self):
        import contextlib
        import io
        self.put("good.json", mine(id="good", name="Good"))
        self.put("dim.json", mine(id="dim", tokens=dict(SIGNAL["tokens"], fg="#555555")))
        self.put("broken.json", b"{")
        self.put("evil.json", mine(id="evil", name="<img src=x onerror=alert(1)>"))
        self.put("taken.json", mine(id="signal"))
        self.put("named.json", mine(id="named", name="dark STAGE"))
        self.put("big.json", json.dumps(mine(id="big")).encode() + b" " * themes.MAX_FILE)
        os.symlink(os.path.join(self.folder, "good.json"), os.path.join(self.folder, "link.json"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.restart()
        self.assertEqual(sorted(set(self.api.themes) - set(themes.load_themes())), ["good"])
        log = err.getvalue()
        skipped = {k["file"]: k["why"] for k in self.api.theme_store.skipped}
        self.assertEqual(sorted(skipped), ["big.json", "broken.json", "dim.json", "evil.json", "link.json", "named.json", "taken.json"])
        for file, want in (("dim.json", "Text on the page is 2.6 to 1; it needs 4.5"), ("broken.json", "not valid JSON"), ("evil.json", "the name must be"),
                           ("taken.json", "its id, signal, belongs to a look that comes with the box"), ("named.json", "is that of a look that comes with the box"),
                           ("big.json", "not a plain file of at most 16 KB"), ("link.json", "not a plain file")):
            self.assertIn(want, skipped[file], file)
            self.assertIn("pvj-web: theme file %s was not used: " % file, log)
            self.assertNotRegex(skipped[file], r"[<>{}\n\x1b]")
        self.assertEqual(log.count("\n"), 7)                                    # one line each
        self.assertNotIn("<img", log)
        # the owner sees it on the Look page; a guest and a presenter are not told what is in the box's folders
        st, got, _ = self.call("GET", "/api/theme", token=self.full)
        self.assertEqual({k["file"] for k in got["skipped"]}, set(skipped))
        for token in (self.view, self.live):
            self.assertNotIn("skipped", self.call("GET", "/api/theme", token=token)[1])
        with open(os.path.join(server.WEB_DIR, "app.js")) as f:
            js = f.read()
        self.assertIn("One theme file on this box could not be used: ", js)
        self.assertIn("k.file + ': ' + k.why", js)

    def test_a_name_of_a_look_that_comes_with_the_box_is_refused(self):
        for name in ("Signal", "signal", "SIGNAL LIGHT", "Dark stage", "dark Stage", "High contrast"):
            st, out = self.add(mine(name=name))
            self.assertEqual(st, 409, name)
            self.assertIn("is that of a look that comes with the box; give the theme another name", out["error"])
        self.assertEqual(self.files(), [])
        self.assertEqual(self.add(mine(name="My Signal"))[0], 200)
        self.assertEqual(self.add(mine(id="other", name="My Signal"))[0], 200)          # two of the owner's own may share a name: they are marked "yours"

    def test_removing_a_theme_whose_file_cannot_be_removed_changes_nothing(self):
        self.assertEqual(self.add(mine())[0], 200)
        self.use("mine")
        self.api.theme_store._unlink = lambda name: False
        st, out, _ = self.call("POST", REMOVE, {"id": "mine"}, token=self.full)
        self.assertEqual((st, out["error"]), (500, "could not remove the theme's file"))
        self.assertEqual(self.settings.data["theme"]["name"], "mine")               # the look is still the theme, and the theme is still there
        self.assertIn("mine", self.api.themes)
        self.assertEqual(self.files(), ["mine.json"])
        self.assertEqual(self.api.theme_style(), "signal")
        # and when the settings cannot be saved after the file went: the theme is gone and the box draws its own look
        del self.api.theme_store._unlink
        real = self.settings.save

        def full_disk():
            raise OSError(28, "No space left on device")
        self.settings.save = full_disk
        st, out, _ = self.call("POST", REMOVE, {"id": "mine"}, token=self.full)
        self.settings.save = real
        self.assertEqual(st, 200, out)
        self.assertNotIn("mine", self.api.themes)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.api.theme_style(), "default")
        self.assertIn("--bg:#121214", self.css())

    def test_no_theme_is_added_or_removed_while_a_reset_or_an_import_runs(self):
        self.assertEqual(self.add(mine())[0], 200)
        for busy in ("a factory reset", "an import"):
            self.api._care_busy = busy
            st, out = self.add(mine(id="late"))
            self.assertEqual((st, out["error"]), (409, "%s is running; try again when it has finished" % busy))
            self.assertEqual(self.call("POST", REMOVE, {"id": "mine"}, token=self.full)[0], 409)
        self.api._care_busy = None
        self.assertEqual(self.files(), ["mine.json"])
        # an add that is under way holds the lock a reset raises its flag under: the reset waits, then removes it
        import threading
        inside, go, done = threading.Event(), threading.Event(), []
        real = self.api.theme_store.add

        def slow(theme, all_):
            inside.set()
            go.wait(10)
            return real(theme, all_)
        self.api.theme_store.add = slow
        adder = threading.Thread(target=lambda: done.append(self.add(mine(id="racing"))[0]))
        adder.start()
        self.assertTrue(inside.wait(10))
        reset = threading.Thread(target=lambda: done.append(self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)[0]))
        reset.start()
        reset.join(0.5)
        self.assertTrue(reset.is_alive())                                         # it has not got past the add
        go.set()
        adder.join(20)
        reset.join(20)
        self.assertEqual(sorted(done), [200, 200])
        self.assertEqual(self.files(), [])
        self.assertEqual(set(self.api.themes), set(themes.load_themes()))

    def test_many_adds_at_once_at_the_limit_keep_sixteen(self):
        import threading
        for i in range(14):
            self.assertEqual(self.add(mine(id="have-%d" % i))[0], 200)
        got = []
        start = threading.Barrier(8)

        def one(i):
            start.wait(10)
            got.append(self.h("POST", ADD, {"file": json.dumps(mine(id="new-%d" % i))}, self.full_dev)[0])      # the handler itself: eight at once
        threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(got), [200, 200, 409, 409, 409, 409, 409, 409])
        self.assertEqual(len(self.files()), 16)
        self.assertEqual(sum(1 for t in self.api.themes.values() if t["source"] == "addon"), 16)
        self.assertFalse([n for n in os.listdir(self.folder) if n.startswith(".")])         # no half-written file left
        self.restart()
        self.assertEqual(sum(1 for t in self.api.themes.values() if t["source"] == "addon"), 16)


class WithTheSettings(Base):
    def setUp(self):
        super().setUp()
        self.care = self.api.boxcare

    def export(self):
        st, out = self.h("POST", "/api/system/settings/export", {}, self.full_dev)
        self.assertEqual(st, 200, out)
        return out["file"]

    def send(self, file, raw=None):
        data = raw if raw is not None else json.dumps(file).encode()
        st, body, _ = self.call("POST", "/api/system/settings/import?confirm=import", raw=data, token=self.full)
        return st, body

    def test_a_box_without_added_themes_writes_the_file_it_always_wrote(self):
        self.assertNotIn("themes", self.export())
        self.assertEqual(set(self.export()), set(boxcare.ENVELOPE) - {"themes"})

    def test_added_themes_and_the_look_in_use_go_round_to_another_box(self):
        soft = mine(id="soft", name="Soft", design={"radius_control": 12, "density": "roomy"})
        self.assertEqual(self.add(soft)[0], 200)
        self.assertEqual(self.add(mine(id="other", name="Other"))[0], 200)
        self.use("soft")
        file = self.export()
        self.assertEqual(file["themes"], [mine(id="other", name="Other"), soft])
        self.assertEqual(file["settings"]["theme"], {"name": "soft", "accent": None})
        # "another box": this one after its themes are gone and its look is another
        for tid in ("soft", "other"):
            self.assertEqual(self.call("POST", REMOVE, {"id": tid}, token=self.full)[0], 200)
        self.use("light")
        self.assertEqual(self.files(), [])
        st, out = self.send(file)
        self.assertEqual(st, 200, out)
        self.assertEqual((out["themes"], out["problems"]), (["other", "soft"], []))
        self.assertEqual(self.files(), ["other.json", "soft.json"])
        self.assertEqual(self.settings.data["theme"], {"name": "soft", "accent": None})
        self.assertEqual(themes.clean(self.api.themes["soft"]), soft)
        self.assertIn("--tk-r:12px", self.css())
        self.assertEqual(self.api.theme_style(), "signal")
        # and what that box exports is the same again
        again = self.export()
        self.assertEqual((again["themes"], again["settings"]["theme"]), (file["themes"], file["settings"]["theme"]))
        # importing over a theme of the same id replaces it; the box's other themes stay
        self.assertEqual(self.add(mine(id="kept"))[0], 200)
        changed = json.loads(json.dumps(file))
        changed["themes"][1]["design"]["radius_control"] = 4
        self.assertEqual(self.send(changed)[0], 200)
        self.assertEqual(self.api.themes["soft"]["design"]["radius_control"], 4)
        self.assertEqual(self.files(), ["kept.json", "other.json", "soft.json"])
        # a file with no themes leaves the box's own alone
        del changed["themes"]
        changed["settings"]["theme"] = {"name": "kept", "accent": None}
        self.assertEqual(self.send(changed)[0], 200)
        self.assertEqual(self.files(), ["kept.json", "other.json", "soft.json"])
        self.assertEqual(self.settings.data["theme"]["name"], "kept")

    def test_a_theme_in_a_settings_file_is_held_to_the_same_checks_and_one_bad_one_refuses_the_file(self):
        self.use("light")
        base = self.export()
        before = json.dumps(self.settings.data, sort_keys=True)
        cases = (
            ([mine(tokens=dict(SIGNAL["tokens"], fg="#555555"))], 400, "themes: Mine: Text on the page is 2.6 to 1; it needs 4.5"),
            ([mine(design={"radius_control": 99})], 400, "must be a whole number from 0 to 24"),
            ([mine(design={"radius_control": 8.0})], 400, "themes: Mine: the file is not a theme: it is not valid JSON (a theme holds whole numbers only)"),
            ([dict(mine(), name="x" * 30, tokens=dict(SIGNAL["tokens"]), areas=dict(SIGNAL["areas"], **{}), states=dict(SIGNAL["states"]),
                   design=dict(themes.DESIGN_DEFAULTS), id="a" * 41)] * 1 + [], 200, ""),
            ([mine(name="signal LIGHT")], 400, "the name signal LIGHT is that of a look that comes with the box"),
            ([mine(design={"radius_control": True})], 400, "must be a whole number"),
            ([dict(mine(), css="body{}")], 400, "unknown keys: css"),
            ([mine(tokens=dict(SIGNAL["tokens"], bg="#000;}*{x:y"))], 400, "token bg must be #rrggbb"),
            ([mine(name="<b>x</b>")], 400, "themes: theme 1: the name must be"),
            ([mine(id="signal")], 400, "the id signal belongs to a look that comes with the box"),
            ([mine(), mine()], 400, "the id mine is in the file twice"),
            ([mine(), "x"], 400, "themes: theme 2: the file is not a theme: a JSON object is expected"),
            ("x", 400, "a list of at most 16 themes is expected"), ({"mine": mine()}, 400, "a list of at most 16"),
            ([mine(id="t-%d" % i) for i in range(17)], 400, "a list of at most 16"),
        )
        cases = tuple(c for c in cases if c[1] != 200)
        for found, status, want in cases:
            st, out = self.send(dict(base, themes=found, settings=dict(base["settings"], theme={"name": "mine", "accent": None})))
            self.assertEqual(st, status, (want, out))
            self.assertIn(want, out["error"])
            self.assertNotRegex(out["error"], r"[<>{}]")
            self.assertEqual(self.files(), [])
            self.assertEqual(json.dumps(self.settings.data, sort_keys=True), before)          # nothing changed: not the look, not anything
        # a key twice inside a theme is refused by the strict reading of the whole file
        raw = json.dumps(dict(base, themes=[mine()])).replace('"id": "mine"', '"id": "mine", "id": "signal"').encode()
        st, out = self.send(None, raw=raw)
        self.assertEqual(st, 400)
        self.assertIn("appears twice", out["error"])
        # the file's themes and the box's own may be sixteen together, not more
        for i in range(10):
            self.assertEqual(self.add(mine(id="own-%d" % i))[0], 200)
        st, out = self.send(dict(base, themes=[mine(id="new-%d" % i) for i in range(7)]))
        self.assertEqual(st, 409)
        self.assertIn("would be 17 together, and 16 is the most", out["error"])
        self.assertEqual(len(self.files()), 10)
        self.assertEqual(self.send(dict(base, themes=[mine(id="new-%d" % i) for i in range(5)] + [mine(id="own-0")]))[0], 200)
        self.assertEqual(len(self.files()), 15)
        # a look the file names and does not bring, and this box does not have: the look is left as it is, and said
        st, out = self.send(dict(base, settings=dict(base["settings"], theme={"name": "elsewhere", "accent": None})))
        self.assertEqual(st, 200)
        self.assertEqual(self.settings.data["theme"]["name"], "light")
        self.assertTrue(any("elsewhere is not on this box" in n for n in out["notes"]))

    def test_a_theme_that_cannot_be_stored_is_reported_and_the_box_draws_its_own_look(self):
        base = self.export()
        os.makedirs(self.addons)
        os.symlink(self.tmp, self.folder)                       # the themes folder is a link: nothing is written through it
        st, out = self.send(dict(base, themes=[mine()], settings=dict(base["settings"], theme={"name": "mine", "accent": None})))
        self.assertEqual(st, 200, out)
        self.assertEqual(out["themes"], [])
        self.assertTrue(any("the theme Mine could not be kept" in p for p in out["problems"]), out["problems"])
        self.assertNotIn("mine.json", os.listdir(self.tmp))
        self.assertEqual(self.api.theme_style(), "default")
        self.assertIn("--bg:#121214", self.css())
        self.assertEqual(self.call("GET", "/")[0], 200)

    def test_an_import_is_still_refused_through_the_tunnel_with_themes_in_it(self):
        self.ready()
        code = self.start(role="full")[1]["code"]
        dev = self.api.support.authenticate(self.h("POST", "/api/support/login", {"code": code}, client=TUNNEL)[1]["token"])
        raw = json.dumps(dict(self.export(), themes=[mine()])).encode()
        with self.assertRaises(ApiError) as e:
            self.care.import_settings(raw, "import", dev, TUNNEL)
        self.assertEqual(e.exception.status, 403)
        self.assertEqual(self.files(), [])

    def test_a_factory_reset_removes_added_themes(self):
        self.assertEqual(self.add(mine())[0], 200)
        self.assertEqual(self.add(mine(id="other"))[0], 200)
        with open(os.path.join(self.folder, "broken.json"), "w") as f:
            f.write("{")
        self.use("mine")
        st, out = self.h("POST", "/api/system/factory-reset", {"confirm": "factory-reset", "media": "keep"}, self.full_dev)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["problems"], [])
        self.assertEqual(self.files(), [])
        self.assertEqual(set(self.api.themes), set(themes.load_themes()))
        self.assertEqual(self.settings.data["theme"], {"name": "dark-stage", "accent": None})
        self.assertEqual(self.api.theme_style(), "default")
        self.assertTrue(re.match(r"^:root\{--bg:#121214;", self.css()))


if __name__ == "__main__":
    import unittest
    unittest.main()
