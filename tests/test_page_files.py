# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The files the panel's page names, and what pvj/web/load.js needs of the page to watch over them (D68).

load.js can only tell "arrived and did not start" for a script whose tag says what the script leaves behind
(data-gives), and it can only see a file fail if it was listening before that file was asked for. A script added to
index.html without the attribute, or put above load.js, would be outside that care and nothing else would notice;
app.js itself was, until the review of #105. These tests read the page and the files as they are shipped."""
import os
import re
import unittest
from html.parser import HTMLParser

from pvj import server
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)

WEB = server.WEB_DIR
# Named by the page and not a file in pvj/web: the server makes it from the theme in use (server.py, do_GET).
MADE_BY_THE_SERVER = {"/theme.css"}


class Page(HTMLParser):
    """The tags of a page in order: (tag, attributes, whether it is inside <head>)."""

    def __init__(self):
        super().__init__()
        self.tags, self.in_head = [], False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        self.tags.append((tag, dict(attrs), self.in_head))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


def files_of(text):
    """What load.js watches, in the page's order: ("script", src, attrs, in_head) and ("sheet", href, ...)."""
    page = Page()
    page.feed(text)
    out = []
    for tag, attrs, in_head in page.tags:
        if tag == "script":
            out.append(("script", attrs.get("src"), attrs, in_head))
        elif tag == "link" and "stylesheet" in (attrs.get("rel") or "").lower().split():
            out.append(("sheet", attrs.get("href"), attrs, in_head))
    return out


def problems(text, exists, source):
    """Every way the page falls short of what load.js needs; an empty list when it does not.
    exists(path) says whether the box has that file, source(path) gives a script's text."""
    out, files = [], files_of(text)
    for kind, url, _attrs, _in_head in files:
        if not url:
            out.append("a %s with no address: scripts written into the page are refused by its policy" % kind)
        elif not re.fullmatch(r"/[A-Za-z0-9_.-]+", url):
            out.append("%s is not a plain file of the box" % url)
        elif not exists(url):
            out.append("%s is named by the page and the box has no such file" % url)
    if not files or files[0][:2] != ("script", "/load.js") or not files[0][3]:
        out.append("/load.js is not the first script or style sheet of the page, in the head")
    for kind, url, attrs, _in_head in files:
        if kind != "script" or not url or url == "/load.js":
            continue
        gives = attrs.get("data-gives")
        if not gives:
            out.append("%s has no data-gives: if it arrived and did not start, the page would not say so" % url)
        elif not re.fullmatch(r"pvj[A-Z][A-Za-z]*", gives):
            out.append("%s: data-gives=%r is not a name of ours" % (url, gives))
        elif exists(url) and not re.search(r"^\s*window\.%s\s*=[^=]" % re.escape(gives), source(url), re.M):
            out.append("%s never sets window.%s, so load.js would call it broken every time" % (url, gives))
    if any(a.get("data-gives") for _k, u, a, _h in files if u == "/load.js"):
        out.append("/load.js watches the others; nothing watches it, and its tag must not say otherwise")
    return out


class PageFilesTest(unittest.TestCase):
    def shipped(self, text=None):
        return problems(read("index.html") if text is None else text,
                        lambda url: url in MADE_BY_THE_SERVER or os.path.isfile(os.path.join(WEB, url.lstrip("/"))),
                        lambda url: read(url.lstrip("/")))

    def test_the_page_as_shipped_is_wholly_under_load_js(self):
        self.assertEqual(self.shipped(), [])
        # and the page is what the checks think it is: the loader, four scripts that each leave a name, two sheets
        got = [(kind, url, attrs.get("data-gives")) for kind, url, attrs, _ in files_of(read("index.html"))]
        self.assertEqual(got, [("script", "/load.js", None), ("sheet", "/theme.css", None), ("sheet", "/app.css", None),
                               ("script", "/shaders.js", "pvjShaders"), ("script", "/effects.js", "pvjEffects"),
                               ("script", "/app.js", "pvjApp"), ("script", "/room.js", "pvjRoom")])

    def test_what_the_server_makes_itself_is_only_the_theme_and_it_does_make_it(self):
        self.assertFalse(os.path.exists(os.path.join(WEB, "theme.css")))
        with open(server.__file__, encoding="utf-8") as f:
            self.assertIn('if path == "/theme.css":', f.read())

    def test_each_fault_of_a_page_is_found(self):
        page = read("index.html")

        def changed(old, new):
            self.assertEqual(page.count(old), 1, old)
            return self.shipped(page.replace(old, new))
        # app.js without its attribute: the state of the page before the review, an empty page that says nothing
        self.assertEqual(changed(' data-gives="pvjApp"', ""),
                         ["/app.js has no data-gives: if it arrived and did not start, the page would not say so"])
        self.assertEqual(changed('data-gives="pvjRoom"', 'data-gives="pvjRooms"'),
                         ["/room.js never sets window.pvjRooms, so load.js would call it broken every time"])
        self.assertEqual(changed('data-gives="pvjRoom"', 'data-gives="onload"'),
                         ["/room.js: data-gives='onload' is not a name of ours"])
        self.assertEqual(changed('<script src="/room.js"', '<script src="/rooms.js"'),
                         ["/rooms.js is named by the page and the box has no such file"])
        self.assertEqual(changed('href="/app.css"', 'href="/panel.css"'),
                         ["/panel.css is named by the page and the box has no such file"])
        self.assertEqual(changed('src="/shaders.js"', 'src="https://example.org/shaders.js"')[0],
                         "https://example.org/shaders.js is not a plain file of the box")
        first = "/load.js is not the first script or style sheet of the page, in the head"
        # a style sheet above the loader: it would be asked for before anything listens
        self.assertEqual(changed('<script src="/load.js"></script>\n', '<link rel="stylesheet" href="/app.css">\n<script src="/load.js"></script>\n'), [first])
        # the loader at the end of the body, after everything it should have watched
        moved = page.replace('<script src="/load.js"></script>\n', "").replace("</body>", '<script src="/load.js"></script>\n</body>')
        self.assertEqual(self.shipped(moved), [first])
        self.assertEqual(changed('<script src="/load.js"></script>', '<script src="/load.js" data-gives="pvjLoad"></script>'),
                         ["/load.js watches the others; nothing watches it, and its tag must not say otherwise"])
        self.assertEqual(changed("</body>", "<script>var x = 1;</script>\n</body>"),
                         ["a script with no address: scripts written into the page are refused by its policy"])

    def test_an_icon_above_the_style_sheets_does_not_count(self):
        # load.js watches scripts and style sheets only; the icons may stay where they are
        page = read("index.html")
        self.assertEqual(self.shipped(page.replace('<script src="/load.js"></script>\n<link rel="icon"', '<link rel="icon" href="/x.png">\n<script src="/load.js"></script>\n<link rel="icon"')), [])

    def test_app_js_says_it_has_started_as_its_very_last_statement(self):
        """pvjApp is set after boot() and nowhere else, so a file that cannot be read as a program, or that stops
        before the panel was started, leaves it unset (the browser check "/app.js broken" in tests/ui/delivery.js
        is the same thing seen from the page)."""
        text = read("app.js")
        self.assertEqual(len(re.findall(r"\bpvjApp\b", re.sub(r"^\s*//.*$", "", text, flags=re.M))), 1)
        statements = [line.strip() for line in text.rstrip().split("\n") if line.strip() and not line.strip().startswith("//")]
        self.assertEqual(statements[-3:], ["boot();", "window.pvjApp = true;", "})();"])

    def test_the_other_scripts_set_their_name_once_they_are_complete(self):
        # each sets its name in one place, at the outer level of its file, not inside something that may not run
        for name, gives in (("shaders.js", "pvjShaders"), ("effects.js", "pvjEffects"), ("room.js", "pvjRoom")):
            found = re.findall(r"^( *)window\.%s\s*=[^=]" % gives, read(name), re.M)
            self.assertEqual(found, ["  "], name)

    def test_the_style_sheet_closes_every_block_it_opens_and_no_more(self):
        """A closing brace too many is not an error to a browser: it drops the rule after it and says nothing.
        (2026-10-09: one left behind by an edit took the rule for a wide Play desk with it; seen in a picture.)"""
        with open(os.path.join(WEB, "app.css"), encoding="utf-8") as f:
            css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
        depth = 0
        for number, line in enumerate(css.split("\n"), 1):
            for char in line:
                depth += (char == "{") - (char == "}")
                self.assertGreaterEqual(depth, 0, "app.css closes a block that is not open (counted without comments, near line %d of that)" % number)
        self.assertEqual(depth, 0, "app.css leaves %d block(s) open" % depth)

    def test_load_js_reads_the_attribute_the_page_writes(self):
        text = read("load.js")
        self.assertIn("getAttribute('data-gives')", text)
        self.assertNotIn("data-gives", read("app.js").replace("(data-gives in index.html)", ""))


if __name__ == "__main__":
    unittest.main()
