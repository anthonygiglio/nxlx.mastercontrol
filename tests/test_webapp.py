# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel saved as an app (D76, docs/WEBAPP.md): what the box serves for it.

The manifest (content, type, how long it is kept, public, made from the look in use), the icons (each a sound PNG of
the size the manifest says, the committed files the same pictures tools/make-icons.py makes, the maskable one inside
its safe zone), the page's head, the policy that lets a browser fetch the manifest and nothing more, and that no
service worker is anywhere. The words of the card are checked in tests/ui/webapp.js, which this file runs where
there is a `node`; the browser test runs it too."""
import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer

from pvj import icon, server, themes, webapp
from tests.test_server import ServerBase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = server.WEB_DIR
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
          "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def make_icons():
    spec = importlib.util.spec_from_file_location("make_icons", os.path.join(ROOT, "tools", "make-icons.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Head(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.meta, self.links, self.all = {}, {}, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.all.append((tag, a))
        if tag == "meta" and "name" in a:
            self.meta.setdefault(a["name"], []).append(a.get("content"))
        if tag == "link":
            self.links.setdefault(a.get("rel"), []).append(a)


class RealPanel(ServerBase):
    """The real files of pvj/web behind the test server (ServerBase serves a two-file stand-in)."""

    def setUp(self):
        super().setUp()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, WEB))
        httpd.daemon_threads = True
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

    def fetch(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path)                    # no token, no cookie: as a browser nobody has paired on
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def look(self, name):
        self.settings.data["theme"] = {"name": name}


class ManifestTest(RealPanel):
    def manifest(self):
        r, body = self.fetch(webapp.PATH)
        self.assertEqual(r.status, 200)
        return r, json.loads(body)

    def test_it_is_public_typed_and_kept_for_a_while_but_not_for_ever(self):
        r, _ = self.manifest()
        self.assertEqual(r.getheader("Content-Type"), "application/manifest+json")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Cache-Control"), "max-age=600")
        self.assertIsNone(r.getheader("Set-Cookie"))
        head, body = self.fetch(webapp.PATH, "HEAD")
        self.assertEqual((head.status, body), (200, b""))
        self.assertEqual(self.fetch(webapp.PATH + "?v=1")[0].status, 200)
        self.assertEqual(self.call("POST", webapp.PATH, {})[0], 404)

    def test_what_it_says(self):
        _, m = self.manifest()
        self.assertEqual(m["name"], "nxlx.mastercontrol")
        self.assertTrue(0 < len(m["short_name"]) <= 12, m["short_name"])       # launchers cut a longer one off
        self.assertTrue(m["description"])
        # every address relative to the box: right at its name, at its address and on any port
        self.assertEqual((m["id"], m["start_url"], m["scope"]), ("/", "/", "/"))
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["display_override"], ["standalone", "minimal-ui"])
        self.assertEqual(m["orientation"], "any")
        self.assertEqual(m["categories"], ["entertainment", "utilities"])
        self.assertNotIn("prefer_related_applications", m)
        self.assertNotIn("serviceworker", m)
        self.assertNotIn("—", json.dumps(m, ensure_ascii=False))

    def test_its_colours_are_the_page_colour_of_the_look_in_use(self):
        looks = themes.load_themes()
        self.assertGreaterEqual(len(looks), 6)
        for name, theme in sorted(looks.items()):
            self.look(name)
            _, m = self.manifest()
            self.assertEqual((m["theme_color"], m["background_color"]), (theme["tokens"]["bg"].lower(),) * 2, name)
        # a settings file that was damaged gives the look the box comes with, never an error (it is asked for before pairing)
        for broken in ("nonsense", {"name": 7}, {"name": "no-such-look"}, None):
            self.settings.data["theme"] = broken
            _, m = self.manifest()
            self.assertEqual(m["theme_color"], looks[themes.FALLBACK]["tokens"]["bg"])

    def test_only_a_colour_is_ever_written(self):
        self.assertEqual(webapp.ground({"tokens": {"bg": "#A1B2C3"}}), "#a1b2c3")
        for bad in ('#fff"><script>', "red", "#12345", "#1234567", 5, None, "#12345g", "#123456\n"):
            self.assertEqual(webapp.ground({"tokens": {"bg": bad}}), "#121214", bad)
        self.assertEqual(webapp.ground(None), "#121214")
        self.assertEqual((webapp.ground({"tokens": "x"}), webapp.ground({})), ("#121214", "#121214"))

    def test_every_icon_it_names_is_served_as_what_it_says(self):
        _, m = self.manifest()
        purposes = sorted((i["purpose"], i["sizes"]) for i in m["icons"])
        self.assertEqual(purposes, [("any", "192x192"), ("any", "512x512"), ("maskable", "512x512"), ("monochrome", "any")])
        for entry in m["icons"]:
            r, body = self.fetch(entry["src"])
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, entry["type"]), entry["src"])
            self.assertEqual(r.getheader("Cache-Control"), "max-age=86400", entry["src"])
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            if entry["type"] == "image/png":
                size, _raw = icon.read(body)             # a sound PNG: every checksum right, RGB, it unpacks
                self.assertEqual("%dx%d" % (size, size), entry["sizes"])
            else:
                self.assertIn(b"<svg", body)
                self.assertNotIn(b"<script", body)

    def test_the_apple_icon_is_180_px_and_kept_for_a_day(self):
        r, body = self.fetch("/apple-touch-icon.png")
        self.assertEqual((r.status, r.getheader("Content-Type"), r.getheader("Cache-Control")), (200, "image/png", "max-age=86400"))
        self.assertEqual(icon.read(body)[0], 180)


class IconFilesTest(unittest.TestCase):
    def test_the_committed_files_are_what_the_script_makes(self):
        tool = make_icons()
        self.assertEqual(tool.differing(), [])
        made = icon.files()
        self.assertEqual(sorted(made), ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "icon-mono.svg"])
        for name, data in made.items():
            with open(os.path.join(WEB, name), "rb") as f:
                have = f.read()
            if name.endswith(".png"):
                self.assertEqual(icon.read(have), icon.read(data), name)
            else:
                self.assertEqual(have, data, name)

    def test_the_check_names_a_file_that_is_not_the_drawing(self):
        tool = make_icons()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(tool.differing(tmp), sorted(icon.files()))            # nothing there yet
        tool.write(tmp)
        self.assertEqual(tool.differing(tmp), [])
        with open(os.path.join(tmp, "icon-192.png"), "wb") as f:
            f.write(icon.png(192, 0.5))                                         # a sound PNG of the right size, another picture
        with open(os.path.join(tmp, "icon-mono.svg"), "ab") as f:
            f.write(b" ")
        self.assertEqual(tool.differing(tmp), ["icon-192.png", "icon-mono.svg"])
        with open(os.path.join(tmp, "icon-512.png"), "ab") as f:
            f.write(b"x")                                                       # something after the picture's end
        self.assertEqual(tool.differing(tmp), ["icon-192.png", "icon-512.png", "icon-mono.svg"])
        # the same picture packed another way is the same picture (zlib differs from one version to another)
        import struct
        import zlib
        size, raw = icon.read(icon.png(512))
        other = (b"\x89PNG\r\n\x1a\n" + icon._chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                 + icon._chunk(b"IDAT", zlib.compress(raw, 1)) + icon._chunk(b"IEND", b""))
        self.assertNotEqual(other, icon.png(512))
        with open(os.path.join(tmp, "icon-512.png"), "wb") as f:
            f.write(other)
        self.assertEqual(tool.differing(tmp), ["icon-192.png", "icon-mono.svg"])

    def pixels(self, size, scale):
        got, raw = icon.read(icon.png(size, scale))
        self.assertEqual(got, size)
        stride = 1 + 3 * size
        return [[raw[y * stride + 1 + 3 * x:y * stride + 4 + 3 * x] for x in range(size)] for y in range(size)]

    def test_a_mark_on_a_ground_with_smooth_edges_and_no_other_colour(self):
        back, mark = bytes(icon.BACK), bytes(icon.MARK)
        for size in (48, 180, 192, 512):
            rows = self.pixels(size, 1.0)
            flat = [p for row in rows for p in row]
            share = flat.count(mark) / float(len(flat))
            self.assertTrue(0.05 < share < 0.09, (size, share))               # the triangle is 7.5% of the picture
            self.assertGreater(flat.count(back), len(flat) * 0.85)
            between = set(flat) - {back, mark}
            self.assertTrue(between, size)                                      # the edges are smoothed
            for p in between:                                                   # and every other colour is a mix of the two
                for i in range(3):
                    self.assertTrue(min(back[i], mark[i]) <= p[i] <= max(back[i], mark[i]), (size, p))
            self.assertEqual({rows[0][0], rows[0][-1], rows[-1][0], rows[-1][-1]}, {back})     # opaque to the corners

    def test_the_maskable_icon_keeps_the_mark_inside_the_safe_zone(self):
        """A launcher may cut a maskable icon down to a circle of 40% of its width about the middle."""
        size, scale = icon.FILES["icon-maskable-512.png"]
        back = bytes(icon.BACK)
        furthest = 0.0
        for y, row in enumerate(self.pixels(size, scale)):
            for x, p in enumerate(row):
                if p != back:
                    furthest = max(furthest, ((x + 0.5 - size / 2.0) ** 2 + (y + 0.5 - size / 2.0) ** 2) ** 0.5)
        self.assertGreater(furthest, 0.15 * size)                               # there is a mark
        self.assertLess(furthest, 0.30 * size)                                  # well inside 0.40
        for x, y in icon.corners(scale):
            self.assertLess(((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5, icon.SAFE)

    def test_bad_sizes_are_refused(self):
        for size, scale in ((8, 1.0), (1024, 1.0), (True, 1.0), ("192", 1.0), (192, 0.1), (192, 2), (192, True)):
            with self.assertRaises(ValueError):
                icon.png(size, scale)
        for data in (b"", b"GIF89a", icon.png(32)[:-5], icon.png(32).replace(b"IDAT", b"IDAX")):
            with self.assertRaises(ValueError):
                icon.read(data)

    def test_the_outline_is_one_shape_and_says_whose_it_is(self):
        text = icon.outline()
        self.assertIn("SPDX-License-Identifier: Apache-2.0", text)
        self.assertEqual(len(re.findall(r"<polygon points=\"[0-9., ]+\"/>", text)), 1)
        self.assertNotIn("fill", text)                                          # no colour of its own: the system tints it


class HeadTest(RealPanel):
    def head(self):
        r, body = self.fetch("/")
        self.assertEqual(r.status, 200)
        return Head(body.decode("utf-8"))

    def test_the_tags_of_an_app(self):
        h = self.head()
        self.assertEqual(h.meta["application-name"], ["nxlx.mastercontrol"])
        self.assertEqual(h.meta["mobile-web-app-capable"], ["yes"])
        self.assertEqual(h.meta["apple-mobile-web-app-capable"], ["yes"])
        self.assertEqual(h.meta["apple-mobile-web-app-title"], [webapp.SHORT_NAME])
        self.assertEqual([a["href"] for a in h.links["manifest"]], [webapp.PATH])
        self.assertEqual([a["href"] for a in h.links["apple-touch-icon"]], ["/apple-touch-icon.png"])
        # room to the edges of the screen (the style sheet keeps clear of the notch), and pinch zoom left on
        self.assertEqual(h.meta["viewport"], ["width=device-width, initial-scale=1, viewport-fit=cover"])
        # no tag for a thing no current browser needs (a pinned-tab mask icon), and nothing from another address
        self.assertNotIn("mask-icon", h.links)
        for _tag, a in h.all:
            for key in ("href", "src", "content"):
                self.assertNotRegex(a.get(key) or "", r"^(https?:)?//")

    def test_the_bar_colour_and_the_iphone_status_bar_follow_the_look(self):
        for name, theme in sorted(themes.load_themes().items()):
            self.look(name)
            h = self.head()
            bg = theme["tokens"]["bg"].lower()
            self.assertEqual(h.meta["theme-color"], [bg], name)
            dark = themes.contrast(bg, "#ffffff") > themes.contrast(bg, "#000000")
            # white status bar text over the page's own colour only where white can be read on it
            self.assertEqual(h.meta["apple-mobile-web-app-status-bar-style"], ["black-translucent" if dark else "default"], name)
        self.assertEqual({webapp.is_dark(t["tokens"]["bg"]) for t in themes.load_themes().values()}, {True, False})

    def test_the_file_has_the_two_lines_the_box_rewrites(self):
        with open(os.path.join(WEB, "index.html"), "rb") as f:
            page = f.read()
        self.assertEqual((page.count(webapp.THEME_META), page.count(webapp.BAR_META)), (1, 1))
        self.assertEqual(webapp.head(b"<html>hi</html>", themes.load_themes()["light"]), b"<html>hi</html>")
        light = webapp.head(page, themes.load_themes()["light"])
        self.assertIn(b'<meta name="theme-color" content="#f4f3ef">', light)
        self.assertIn(b'<meta name="apple-mobile-web-app-status-bar-style" content="default">', light)
        self.assertEqual(len(light) - len(page), len(b"default") - len(b"black-translucent"))
        self.assertEqual(webapp.head(page, themes.load_themes()[themes.FALLBACK]), page)       # the look the box comes with: the file as it is


class PolicyTest(RealPanel):
    def test_the_policy_lets_the_manifest_through_and_nothing_more(self):
        self.assertEqual(server.CSP, POLICY)
        for path in ("/", webapp.PATH, "/icon-192.png", "/api/hello", "/nothing-here"):
            self.assertEqual(self.fetch(path)[0].getheader("Content-Security-Policy"), POLICY, path)
        self.assertNotIn("worker-src", server.CSP)             # default-src 'none' refuses a worker, and so it stays

    def test_no_service_worker_anywhere(self):
        """Over plain HTTP a browser refuses one; and one that kept an answer of the API would keep a token."""
        for name in sorted(os.listdir(WEB)):
            if name.endswith((".js", ".html")):
                with open(os.path.join(WEB, name), encoding="utf-8") as f:
                    text = re.sub(r"^\s*//.*$", "", f.read(), flags=re.M)
                self.assertNotRegex(text, r"serviceWorker|caches\.open|importScripts", name)
        self.assertEqual(self.fetch("/sw.js")[0].status, 404)
        self.assertEqual(self.fetch("/service-worker.js")[0].status, 404)

    def test_pairing_sets_the_cookie_an_app_keeps(self):
        """An app on a home screen keeps its own cookies; the one that signs it in lasts a year, cannot be read by
        script, and is not sent from another site. It cannot be Secure: the panel is plain HTTP."""
        _token, r = self.pair()
        cookie = r.getheader("Set-Cookie")
        self.assertRegex(cookie, r"^pvj_token=[A-Za-z0-9_-]+; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000$")


class CardWordsTest(unittest.TestCase):
    def test_the_words_of_the_card_for_every_device(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node here; the browser test runs tests/ui/webapp.js too")
        done = subprocess.run([node, os.path.join(ROOT, "tests", "ui", "webapp.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
