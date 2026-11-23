# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel saved as an app (D151, docs/WEBAPP.md): what the node serves for it, and what its two scripts do.

The manifest (content, type, how long it is kept, public, made from the look in use), the icons (each a sound PNG of
the size the manifest says, the committed files the same pictures tools/make-icons.py makes, the maskable one inside
its safe zone), the page's head, the policy that lets a browser fetch the manifest and nothing more, the service worker
(served without a login, never kept, the release in its cache name, never touching an API address or keeping a page),
and the page asking for the worker over https only. The scripts are run against stand-ins for the browser by
tests/webapp_unit.js, where there is a `node`."""
import http.client
import importlib.util
import json
import os
import re
import shutil
import shutil as _shutil
import subprocess
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from unittest import mock

from mctl import icon, server, themes, webapp
from tests.test_server import ServerBase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = server.WEB_DIR
NODE = _shutil.which("node")


def make_icons():
    spec = importlib.util.spec_from_file_location("make_icons", os.path.join(ROOT, "tools", "make-icons.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Head(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.meta, self.links = {}, {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and "name" in a:
            self.meta.setdefault(a["name"], []).append(a.get("content"))
        if tag == "link":
            self.links.setdefault(a.get("rel"), []).append(a)


class RealPanel(ServerBase):
    """The real files of mctl/web behind the test server (ServerBase serves a two-file stand-in)."""

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

    def test_the_fields_a_browser_needs(self):
        _, m = self.manifest()
        self.assertEqual(m["name"], "NXLX")
        self.assertTrue(0 < len(m["short_name"]) <= 12, m["short_name"])       # launchers cut a longer one off
        self.assertTrue(m["description"])
        # every address relative to the node: right at its name, at its address and on any port, http and https
        self.assertEqual((m["id"], m["start_url"], m["scope"]), ("/", "/", "/"))
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["display_override"], ["standalone", "minimal-ui"])
        self.assertNotIn("prefer_related_applications", m)
        self.assertNotIn("—", json.dumps(m, ensure_ascii=False))
        self.assertRegex(m["theme_color"], r"^#[0-9a-f]{6}$")

    def test_its_colours_are_the_page_colour_of_the_look_in_use(self):
        looks = themes.load_themes()
        self.assertGreaterEqual(len(looks), 6)
        for name, theme in sorted(looks.items()):
            self.look(name)
            _, m = self.manifest()
            self.assertEqual((m["theme_color"], m["background_color"]), (theme["tokens"]["bg"].lower(),) * 2, name)
        # a settings file that was damaged gives the look the node comes with, never an error (it is asked for before pairing)
        self.settings.data["theme"] = {"name": "signal"}
        _, comes_with = self.manifest()
        for broken in ("nonsense", {"name": 7}, {"name": "no-such-look"}, None):
            self.settings.data["theme"] = broken
            _, m = self.manifest()
            self.assertRegex(m["theme_color"], r"^#[0-9a-f]{6}$")
        self.assertEqual(comes_with["theme_color"], looks["signal"]["tokens"]["bg"].lower())

    def test_only_a_colour_is_ever_written(self):
        self.assertEqual(webapp.ground({"tokens": {"bg": "#A1B2C3"}}), "#a1b2c3")
        for bad in ('#fff"><script>', "red", "#12345", "#1234567", 5, None, "#12345g", "#123456\n"):
            self.assertEqual(webapp.ground({"tokens": {"bg": bad}}), webapp.FALLBACK, bad)
        self.assertEqual(webapp.ground(None), webapp.FALLBACK)
        self.assertEqual((webapp.ground({"tokens": "x"}), webapp.ground({})), (webapp.FALLBACK, webapp.FALLBACK))

    def test_every_icon_it_names_is_served_at_the_size_it_says(self):
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

    def test_the_policy_lets_the_manifest_in_and_nothing_else(self):
        r, _ = self.fetch("/")
        policy = r.getheader("Content-Security-Policy")
        self.assertIn("manifest-src 'self'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertEqual(policy, server.CSP)
        # the worker is a script of the node's own: script-src 'self' covers it, and nothing wider was added
        self.assertNotIn("worker-src", policy)
        self.assertNotIn("unsafe", policy)


class PageHeadTest(RealPanel):
    def head(self):
        r, body = self.fetch("/")
        self.assertEqual(r.status, 200)
        return Head(body.decode("utf-8")), body

    def test_the_page_has_the_tags_a_home_screen_reads(self):
        page, _ = self.head()
        self.assertEqual(page.meta["apple-mobile-web-app-capable"], ["yes"])
        self.assertEqual(page.meta["mobile-web-app-capable"], ["yes"])
        self.assertEqual(page.meta["apple-mobile-web-app-title"], ["NXLX"])
        self.assertEqual(page.meta["application-name"], ["NXLX"])
        self.assertIn(page.meta["apple-mobile-web-app-status-bar-style"][0], ("black-translucent", "default"))
        self.assertEqual([l["href"] for l in page.links["manifest"]], [webapp.PATH])
        self.assertEqual([l["href"] for l in page.links["apple-touch-icon"]], ["/apple-touch-icon.png"])
        self.assertIn("viewport-fit=cover", page.meta["viewport"][0])

    def test_the_bar_colour_follows_the_look_and_the_status_bar_the_brightness(self):
        looks = themes.load_themes()
        seen = set()
        for name, theme in sorted(looks.items()):
            self.look(name)
            page, _ = self.head()
            bg = theme["tokens"]["bg"].lower()
            self.assertEqual(page.meta["theme-color"], [bg], name)
            want = "black-translucent" if webapp.is_dark(bg) else "default"
            self.assertEqual(page.meta["apple-mobile-web-app-status-bar-style"], [want], name)
            seen.add(want)
        self.assertEqual(seen, {"black-translucent", "default"})           # the looks include a light one


class IconFilesTest(unittest.TestCase):
    def test_the_committed_files_are_what_the_script_makes(self):
        tool = make_icons()
        self.assertEqual(tool.differing(), [])
        made = icon.files()
        self.assertEqual(sorted(made), ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "icon-mono.svg", "icon.svg"])
        for name in made:
            self.assertTrue(os.path.isfile(os.path.join(WEB, name)), name)

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

    def pixels(self, size, scale):
        got, raw = icon.read(icon.png(size, scale))
        self.assertEqual(got, size)
        stride = 1 + 3 * size
        return [[raw[y * stride + 1 + 3 * x:y * stride + 4 + 3 * x] for x in range(size)] for y in range(size)]

    def test_a_mark_on_a_ground_with_smooth_edges_and_no_other_colour(self):
        back, mark = bytes(icon.BACK), bytes(icon.MARK)
        for size in (48, 180, 192, 512):
            rows = self.pixels(size, 1.0)
            colours = {p for row in rows for p in row}
            self.assertIn(back, colours)
            self.assertIn(mark, colours)
            for p in colours:                                                   # every colour lies between the two
                for i in range(3):
                    self.assertTrue(min(back[i], mark[i]) <= p[i] <= max(back[i], mark[i]), (size, p))
            self.assertGreater(len(colours), 2, "the edges are smoothed")
            self.assertEqual(rows[0][0], back)                                  # corners are ground: a launcher cuts the corners

    def test_the_maskable_icon_keeps_its_mark_inside_the_safe_circle(self):
        size = 512
        rows = self.pixels(size, icon.MASKABLE)
        back = bytes(icon.BACK)
        far = 0.0
        for y, row in enumerate(rows):
            for x, p in enumerate(row):
                if p != back:
                    far = max(far, (((x + 0.5) / size - 0.5) ** 2 + ((y + 0.5) / size - 0.5) ** 2) ** 0.5)
        self.assertLessEqual(far, icon.SAFE, far)
        self.assertGreater(far, 0.2)                                            # and it is not a speck
        full = self.pixels(size, 1.0)
        self.assertGreater(sum(p != back for row in full for p in row), sum(p != back for row in rows for p in row))

    def test_the_outline_has_no_colours_and_no_script(self):
        text = icon.outline()
        self.assertNotIn("fill=", text)
        self.assertNotIn("<script", text)
        self.assertEqual(text.count("<polygon"), len(icon.SHAPES))


class WorkerServedTest(RealPanel):
    def test_it_is_served_without_a_login_as_a_script_that_is_never_kept(self):
        r, body = self.fetch(webapp.SW_PATH)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIsNone(r.getheader("Service-Worker-Allowed"))                # /sw.js is at the root, so its scope is the node
        self.assertIsNone(r.getheader("Set-Cookie"))
        head, nothing = self.fetch(webapp.SW_PATH, "HEAD")
        self.assertEqual((head.status, nothing), (200, b""))

    def test_the_release_is_written_into_it(self):
        _, body = self.fetch(webapp.SW_PATH)
        text = body.decode("utf-8")
        self.assertNotIn("__RELEASE__", text)
        name = webapp.release(WEB)
        self.assertIn("var RELEASE = '%s';" % name, text)
        from mctl import __version__
        self.assertTrue(name.startswith(__version__ + "-"), name)
        self.assertIn("'nxlx-shell-' + RELEASE", text)                          # the cache's name is made from it

    def test_a_new_release_is_a_new_worker_and_a_new_cache(self):
        _, one = self.fetch(webapp.SW_PATH)
        with mock.patch.object(webapp, "__version__", "9.9.9"):
            _, two = self.fetch(webapp.SW_PATH)
        self.assertNotEqual(one, two)
        self.assertIn(b"9.9.9-", two)

    def test_a_change_to_the_files_it_keeps_is_a_new_release_even_with_the_same_version(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in (webapp.SW_FILE,) + webapp.SHELL:
            shutil.copy(os.path.join(WEB, name), os.path.join(tmp, name))
        before = webapp.release(tmp)
        with open(os.path.join(tmp, "away.html"), "a") as f:
            f.write("<!-- changed -->")
        self.assertNotEqual(webapp.release(tmp), before)
        self.assertIsNone(webapp.worker(os.path.join(tmp, "nowhere")))

    def test_the_files_it_keeps_are_there_and_public(self):
        for name in webapp.SHELL:
            r, body = self.fetch("/" + name)
            self.assertEqual(r.status, 200, name)
            self.assertEqual(r.getheader("Cache-Control"), "no-store")
        r, body = self.fetch("/away.html")
        self.assertIn(b"Try again", body)
        self.assertNotIn(b"<script", body)                                      # the policy would refuse one
        self.assertNotIn(b"<style", body)
        self.assertNotIn("—".encode("utf-8"), body)

    def test_the_source_never_keeps_a_page_or_touches_an_api_address(self):
        """Read as text: the only way into the cache is the two files at install, and a request is let through
        untouched unless it is a GET page navigation to this node that is not under /api/."""
        with open(os.path.join(WEB, webapp.SW_FILE), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(re.findall(r"\.(put|add|addAll)\(", text), ["addAll"])
        self.assertEqual(re.findall(r"fetch\('([^']*)'", text), ["/sw.js"])      # the one address it asks for by itself
        self.assertIn("var SHELL = ['/away.html', '/away.css'];", text)
        self.assertIn("req.mode === 'navigate'", text)
        self.assertIn("SHELL.indexOf(url.pathname) >= 0", text)
        self.assertNotIn("setTimeout", text)                                    # slowness is never taken for being away
        self.assertIn("indexOf('/api/') === 0", text)
        self.assertIn("req.method !== 'GET'", text)
        self.assertIn("url.origin !== self.location.origin", text)
        self.assertNotIn("importScripts", text)
        self.assertNotIn("Service-Worker-Allowed", text)
        self.assertEqual(text.count("__RELEASE__"), 1)
        self.assertNotIn("—", text)


@unittest.skipUnless(NODE, "needs node")
class ScriptsRunTest(unittest.TestCase):
    """sw.js and webapp.js run against stand-ins for the browser (tests/webapp_unit.js)."""

    @classmethod
    def setUpClass(cls):
        cls.release = "9.9.9-abcd1234"
        r = subprocess.run([NODE, os.path.join(ROOT, "tests", "webapp_unit.js"), WEB, cls.release], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.out = json.loads(r.stdout)

    def test_install_keeps_two_files_under_a_name_with_the_release_and_takes_over(self):
        self.assertEqual(self.out["installed"], {"nxlx-shell-" + self.release: ["/away.html", "/away.css"]})
        self.assertTrue(self.out["skipped"])
        self.assertTrue(self.out["claimed"])

    def test_activate_deletes_every_cache_that_is_not_its_own(self):
        self.assertEqual(self.out["kept"], ["nxlx-shell-" + self.release])
        self.assertEqual(self.out["deleted"], ["nxlx-shell-0.0.1-old", "something-else"])

    def test_a_node_that_answers_is_passed_through_and_nothing_is_stored(self):
        self.assertTrue(self.out["liveSame"])
        self.assertEqual(self.out["puts"], 0)

    def test_a_node_out_of_reach_gets_the_away_page(self):
        self.assertEqual(self.out["awayBody"], "AWAY PAGE /away.html")
        self.assertEqual(self.out["noCache"], [503, "The node cannot be reached."])      # never installed: a plain note

    def test_a_slow_node_is_waited_for_and_its_page_wins(self):
        self.assertTrue(self.out["slowSame"])

    def test_the_away_page_has_its_style_sheet_from_the_cache_when_the_node_is_away(self):
        self.assertTrue(self.out["shellOnline"])
        self.assertEqual(self.out["shellAway"], "AWAY PAGE /away.css")
        self.assertEqual(self.out["shellAwayHtml"], "AWAY PAGE /away.html")

    def test_a_node_that_no_longer_has_the_worker_makes_it_remove_itself(self):
        r = self.out["selfRemoval"]
        for k in ("gone404", "gone410"):
            self.assertEqual(r[k], {"unregistered": True, "caches": []}, k)   # its caches go, and it unregisters
        for k in ("ok", "error500"):
            self.assertEqual(r[k], {"unregistered": False, "caches": ["nxlx-shell-" + self.release]}, k)
        self.assertTrue(self.out["removedOnNavigation"])
        self.assertTrue(self.out["networkErrorLeavesIt"])

    def test_an_api_address_a_post_a_script_or_another_site_is_never_touched(self):
        self.assertEqual(self.out["untouched"], {k: True for k in ("api", "apiNested", "post", "script", "fetchApi", "other", "head")})

    def test_the_page_asks_for_the_worker_over_https_only(self):
        self.assertEqual(self.out["httpCalls"], 0)                              # plain http: exactly as before, nothing asked for
        self.assertEqual(self.out["httpsCalls"], [["/sw.js", {"scope": "/"}]])
        self.assertEqual(self.out["noWorkerSupport"], 0)                        # a browser with none: nothing to ask

    def test_the_devices_are_told_apart(self):
        w = self.out["where"]
        self.assertEqual((w["iphone"]["device"], w["iphone"]["browser"]), ("iphone", "safari"))
        self.assertEqual((w["ipad"]["device"], w["ipad"]["browser"]), ("ipad", "safari"))
        self.assertEqual((w["androidChrome"]["device"], w["androidChrome"]["browser"]), ("android", "chrome"))
        self.assertEqual((w["silk"]["device"], w["silk"]["browser"]), ("fire", "silk"))
        self.assertEqual((w["silkDesktop"]["device"], w["silkDesktop"]["browser"]), ("fire", "silk"))
        self.assertEqual((w["winChrome"]["device"], w["winChrome"]["browser"]), ("windows", "chrome"))
        self.assertTrue(self.out["appMode"])

    def test_the_steps_say_what_http_and_https_change(self):
        g = self.out["guide"]
        for k in ("iphone", "androidChrome", "silk", "winChrome"):
            for which in ("http", "https"):
                self.assertTrue(g[k][which]["steps"], (k, which))
        # Android over http: a shortcut that opens in the browser; over https a real app, if the device trusts the node
        self.assertFalse(g["androidChrome"]["http"]["window"])
        self.assertIn("https", g["androidChrome"]["http"]["cannot"])
        self.assertTrue(g["androidChrome"]["https"]["window"])
        self.assertIn("Install app", " ".join(g["androidChrome"]["https"]["steps"]))
        self.assertIn("root certificate", g["androidChrome"]["https"]["cannot"])
        self.assertIn("Silk", g["silk"]["https"]["where"])
        self.assertIn("Add to Home Screen", " ".join(g["iphone"]["https"]["steps"]))
        self.assertIn("own sign-in", " ".join(g["iphone"]["https"]["steps"]))   # iOS keeps a separate sign-in for the app
        for k in g:
            for which in g[k]:
                text = json.dumps(g[k][which], ensure_ascii=False)
                self.assertNotIn("—", text)
                self.assertNotRegex(text, r"\b[Bb]ox\b")                      # "node", not "box"


if __name__ == "__main__":
    unittest.main()
