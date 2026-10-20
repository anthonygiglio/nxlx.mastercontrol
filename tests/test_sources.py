# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Sources and surfaces, step 1 (D101): more clip layers on the new renderer, and a source per surface.

The mapping's side (a surface's "source", the show table and both shaders carrying it, classic mpv's shader byte for
byte as before, the release before loading a file with the key), the renderer's number of sources, and the API's
play to a source, its refusals, its roles, the pads' target and the warning about load. pvj-render's own side runs
for real in tests/test_pvj_render.py (SourcesTest and the classes after it)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from pvj import mapper, renderer
from tests.test_server import ServerBase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The release before D101, by its commit (D62's lesson: never by a branch): master at 06d1067, #152 merged.
BEFORE = "06d10674578b3178a6783845160279aa31f96311"


def have_before():
    try:
        subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", BEFORE + "^{commit}"], check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def old_mapper():
    """pvj/mapper.py as the release before had it, loaded as a module of its own."""
    text = subprocess.run(["git", "-C", REPO, "show", BEFORE + ":pvj/mapper.py"], check=True, capture_output=True).stdout
    d = tempfile.mkdtemp()
    pkg = os.path.join(d, "oldpvj")
    os.makedirs(pkg)
    shutil.copy(os.path.join(REPO, "pvj", "locks.py"), os.path.join(pkg, "locks.py"))
    open(os.path.join(pkg, "__init__.py"), "w").close()
    with open(os.path.join(pkg, "mapper.py"), "wb") as f:
        f.write(text)
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location("oldpvj.mapper", os.path.join(pkg, "mapper.py"),
                                                      submodule_search_locations=None)
        import oldpvj  # noqa: F401
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(d)


def quad(sid, x0, w, h, source=None, tex=((0, 0), (1, 0), (1, 1), (0, 1))):
    s = {"id": sid, "type": "quad", "name": sid, "on": True, "vertices": [[x0, 0], [x0 + w, 0], [x0 + w, h], [x0, h]],
         "tex": [list(p) for p in tex]}
    if source is not None:
        s["source"] = source
    return s


def grid(sid, source=None):
    s = {"id": sid, "type": "grid", "name": "Grid", "on": True, "cols": 2, "rows": 2,
         "vertices": [[100 + 200 * c, 600 + 150 * r + 10 * c] for r in range(3) for c in range(3)],
         "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    if source is not None:
        s["source"] = source
    return s


def tri(sid, source=None):
    s = {"id": sid, "type": "triangle", "name": "Tri", "on": True, "vertices": [[1300, 900], [1600, 500], [1900, 1000]],
         "tex": [[0, 1], [0.5, 0], [1, 1]]}
    if source is not None:
        s["source"] = source
    return s


class ValidateTest(unittest.TestCase):
    def test_a_surface_names_its_source_and_source_1_is_no_key(self):
        s = mapper.validate_surface(quad("a1a1a1a1", 0, 100, 100))
        self.assertNotIn("source", s, "a surface of before is as before")
        self.assertNotIn("source", mapper.validate_surface(quad("a1a1a1a1", 0, 100, 100, 1)), "Source 1 is stored as no key")
        for k in range(2, 9):
            self.assertEqual(mapper.validate_surface(quad("a1a1a1a1", 0, 100, 100, k))["source"], k)
        for bad in (0, 9, -1, True, False, "2", 2.0, [2]):
            with self.assertRaises(mapper.MapperError, msg=repr(bad)):
                mapper.validate_surface(quad("a1a1a1a1", 0, 100, 100, bad))
        self.assertEqual(mapper.shown_source({"source": 3}, 4), 3)
        self.assertEqual(mapper.shown_source({"source": 3}, 2), 1, "a source the player does not have: Source 1")
        self.assertEqual(mapper.shown_source({}, 8), 1)

    def test_an_import_takes_the_key_and_refuses_a_bad_one(self):
        from pvj import boxcare
        good = {"on": True, "screen": [1920, 1080], "surfaces": [quad("a1a1a1a1", 0, 100, 100, 2)], "sets": {}}
        self.assertEqual(boxcare.check_mapper(good, None)["surfaces"][0]["source"], 2)
        with self.assertRaises(ValueError):
            boxcare.check_mapper(dict(good, surfaces=[quad("a1a1a1a1", 0, 100, 100, 12)]), None)


class TableTest(unittest.TestCase):
    W, H = 400, 200

    def halves(self, left, right):
        return mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, self.W / 2, self.H, left),
                                                     quad("aaaaaaa2", self.W / 2, self.W / 2, self.H, right)]})

    def test_the_table_carries_each_layers_source(self):
        lw, lh, values, src = mapper.warp_table(self.halves(None, 3), self.W, self.H, with_sources=True)
        self.assertEqual(len(src), 2 * lw * lh)
        self.assertEqual(mapper.warp_table(self.halves(None, 3), self.W, self.H), (lw, lh, values), "the same table")
        at = lambda i, j: tuple(src[2 * (j * lw + i):2 * (j * lw + i) + 2])
        self.assertEqual(at(5, lh // 2), (1, 1), "deep in the left half: Source 1 on both layers")
        self.assertEqual(at(lw - 5, lh // 2), (3, 3), "deep in the right half: Source 3")
        # at the edge each layer keeps its own surface's source, so the blend is between the two pictures
        seam = [at(i, lh // 2) for i in range(lw // 2 - 2, lw // 2 + 2)]
        self.assertTrue(all(sorted(p) == [1, 3] for p in seam), seam)
        # outside every surface: 1 (nothing is read from it)
        lw2, lh2, _, src2 = mapper.warp_table(mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, 50, 50, 2)]}), self.W, self.H, with_sources=True)
        self.assertEqual(tuple(src2[-2:]), (1, 1))

    def test_the_shader_binds_only_the_sources_shown_and_reads_the_index_unblended(self):
        table = mapper.warp_table(self.halves(2, 4), self.W, self.H, with_sources=True)
        text = mapper.warp_shader(*table, available=4)
        self.assertIn("//!BIND SOURCE2\n//!BIND SOURCE4\n", text)
        self.assertNotIn("SOURCE3", text)
        self.assertIn("texelFetch(PVJWARPB", text, "the index is read at the nearest cell, never blended")
        self.assertNotIn("SOURCE", text.split("//!HOOK OUTPUT")[0], "no source in the data's directives")
        # the second texture's spare channels hold the sources: (uB, vB, sA, sB)
        import struct
        hexes = [ln for ln in text.splitlines() if ln and all(c in "0123456789abcdef" for c in ln)]
        b = struct.unpack("<%df" % (len(hexes[1]) // 8), bytes.fromhex(hexes[1]))
        sources = {(b[k + 2], b[k + 3]) for k in range(0, len(b), 4)}
        self.assertTrue({(2.0, 2.0), (4.0, 4.0)} <= sources, sources)
        # only the sources the player has: Source 4 on a player with 3 is drawn from Source 1
        three = mapper.warp_shader(*table, available=3)
        self.assertIn("//!BIND SOURCE2\n", three)
        self.assertNotIn("SOURCE4", three)

    def test_the_editing_shader_reads_each_surfaces_source(self):
        surfaces = mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, 100, 100, 2), tri("aaaaaaa2", 5), grid("aaaaaaa3")]})
        text = mapper.shader(surfaces, {"selected": None, "corner": 0}, available=8)
        self.assertIn("//!BIND SOURCE2\n//!BIND SOURCE5\n", text)
        self.assertIn("vec4 pick2(vec2 t) { return vec4(SOURCE2_tex(", text)
        self.assertIn("return pick5(", text)
        self.assertIn("return pick(mix(", text, "the grid on Source 1 reads HOOKED")
        self.assertEqual(mapper.shader(surfaces, available=1), mapper.shader(mapper.validate_mapping(
            {"surfaces": [dict(s, source=1) for s in surfaces]})), "on a player with Source 1 only: as before")


@unittest.skipUnless(have_before(), "no %s here to read the release before from" % BEFORE)
class ClassicUnchangedTest(unittest.TestCase):
    """On classic mpv (Source 1 only), and wherever every surface shows Source 1, the shaders are byte for byte what
    the release before wrote, whatever sources the surfaces name."""

    def test_byte_for_byte(self):
        old = old_mapper()
        self.assertFalse(hasattr(old, "MAX_SOURCES"), "this is the release before")
        layouts = [[quad("aaaaaaa1", 0, 960, 1080), quad("aaaaaaa2", 900, 960, 1080, None, ((0.5, 0), (1, 0), (1, 1), (0.5, 1)))],
                   [grid("aaaaaaa3"), tri("aaaaaaa4"), quad("aaaaaaa5", 200, 500, 400)]]
        for surfaces in layouts:
            plain = mapper.validate_mapping({"surfaces": surfaces})
            named = mapper.validate_mapping({"surfaces": [dict(s, source=2 + i % 3) for i, s in enumerate(surfaces)]})
            before = old.validate_mapping({"surfaces": surfaces})
            self.assertEqual(plain, before, "a mapping without sources is stored as before")
            want_table = old.warp_table(before, 1920, 1080)
            want_show = old.warp_shader(*want_table)
            want_edit = old.shader(before, {"selected": before[0]["id"], "corner": 1})
            self.assertEqual(mapper.warp_shader(*mapper.warp_table(plain, 1920, 1080)), want_show)
            self.assertEqual(mapper.warp_shader(*mapper.warp_table(plain, 1920, 1080, with_sources=True), available=4), want_show,
                             "every surface on Source 1, on the new renderer: as before")
            self.assertEqual(mapper.warp_shader(*mapper.warp_table(named, 1920, 1080, with_sources=True), available=1), want_show,
                             "surfaces on other sources, on classic: drawn from Source 1, as before")
            self.assertEqual(mapper.shader(named, {"selected": before[0]["id"], "corner": 1}, available=1), want_edit)
            self.assertEqual(mapper.shader(plain, {"selected": before[0]["id"], "corner": 1}, available=4), want_edit)


@unittest.skipUnless(have_before(), "no %s here to read the release before from" % BEFORE)
class RollbackTest(ServerBase):
    """The release before D101 (its own code, read from git) loads a file whose surfaces name sources, shows them all
    from its one picture, and saves the file: the mapping is kept, the source choices are lost (the key is dropped)."""

    def test_the_older_release_loads_a_mapping_with_sources(self):
        self.settings.data["mapper"] = {"on": True, "screen": [1920, 1080], "sets": {"Two": {"screen": [1920, 1080], "surfaces": mapper.validate_mapping(
            {"surfaces": [quad("aaaaaaa1", 0, 960, 1080, 3)]})}},
            "surfaces": mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, 960, 1080, 2), grid("aaaaaaa2", 4)]})}
        self.settings.save()
        old = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, old, True)
        tar = subprocess.run(["git", "-C", REPO, "archive", BEFORE, "pvj"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=tar, check=True)
        with open(os.path.join(old, "pvj", "mapper.py")) as f:
            self.assertNotIn("MAX_SOURCES", f.read(), "this is the older release")
        script = r'''
import json, sys
from pvj.settings import Settings
from pvj import boxcare, mapper
s = Settings(sys.argv[1])
s.load()
cfg = s.data["mapper"]
clean = mapper.validate_mapping({"surfaces": cfg["surfaces"]})
lw, lh, values = mapper.warp_table(clean, 1920, 1080)
text = mapper.warp_shader(lw, lh, values)
checked = boxcare.check_mapper(cfg, None)
s.data["mapper"] = dict(cfg, surfaces=clean)          # what its handle() saves: the clean surfaces
s.save()
print(json.dumps({"schema": s.data["schema"], "n": len(clean), "sources": [x.get("source") for x in clean],
                  "binds": text.count("SOURCE"), "checked": len(checked["surfaces"])}))
'''
        copy = os.path.join(old, "settings.json")
        shutil.copy(self.settings.path, copy)
        run = subprocess.run([sys.executable, "-c", script, copy], cwd=old, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=old))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(out["schema"], self.settings.data["schema"], "no schema change: the older release loads the file")
        self.assertEqual((out["n"], out["checked"], out["binds"]), (2, 2, 0), "both surfaces drawn, from its one picture")
        self.assertEqual(out["sources"], [None, None], "the older release does not keep the key")
        # forward again: the file the older release saved loads here, every surface on Source 1
        from pvj.settings import Settings
        back = Settings(copy).load()["mapper"]
        self.assertEqual([mapper.source_of(s) for s in mapper.validate_mapping({"surfaces": back["surfaces"]})], [1, 1])


class SourcesPlayer:
    """The parts of pvj.player.Player that D101 adds, recorded; `n` sources."""

    def __init__(self, base, n=4, rows=None):
        self.base, self.n, self.rows = base, n, rows
        self.to, self.stopped = [], []

    def install(self):
        b = self.base
        b.sources = lambda ask=True: self.n
        b.play_to = lambda k, paths, ending="loop", image_seconds=None: self.to.append((k, paths, ending))
        b.stop_source = lambda k: self.stopped.append(k)
        b.sources_state = lambda: list(self.rows if self.rows is not None else
                                       [{"source": k, "idle": True, "path": None, "height": None, "pause": False, "drawn": 0}
                                        for k in range(1, self.n + 1)])
        return self


class ApiBase(ServerBase):
    def setUp(self):
        super().setUp()
        self.lines = []
        self.api.log = self.lines.append
        self.owner, _ = self.pair("owner")
        self.operator = self.call("POST", "/api/devices/invite", {"name": "Operator phone", "role": "live"}, token=self.owner)[1]["token"]
        self.guest = self.call("POST", "/api/devices/invite", {"name": "Guest phone", "role": "view"}, token=self.owner)[1]["token"]
        with self.settings.lock:
            self.settings.data["guest_controls"] = {"locked": False}
            banks = self.settings.data["pads"]["banks"]
            banks[0]["pads"][0] = {"label": "Clip", "file": "a.mp4", "ending": "stop"}
            banks[0]["pads"][1] = {"label": "", "file": "", "shader": "nxlx-silk.fs"}
            banks[0]["pads"][2] = {"label": "", "file": "", "ndi": "0123456789ab"}
            banks[0]["pads"][3] = {"label": "", "file": "", "render": {"node": "0a0b0c0d", "address": "/nxlx/go"}}
            self.settings.save()
        self.src = SourcesPlayer(self.player).install()

    def post(self, path, body, token=None):
        st, out, _ = self.call("POST", path, body, token=token or self.owner)
        return st, out


class PlayToSourceTest(ApiBase):
    def test_a_clip_plays_on_source_2_and_source_1_is_untouched(self):
        st, out = self.post("/api/play", {"file": "a.mp4", "source": 2})
        self.assertEqual((st, out), (200, {"playing": "a.mp4", "source": 2}))
        self.assertEqual(self.src.to, [(2, [os.path.realpath(os.path.join(self.media, "a.mp4"))], "loop")])
        self.assertEqual(self.player.plays, [], "source 1 plays nothing new")
        self.assertTrue(any("source 2 plays \"a.mp4\"" in ln for ln in self.lines), self.lines)

    def test_a_clip_pad_plays_on_its_target_with_its_own_ending(self):
        st, out = self.post("/api/play", {"pad": [0, 0], "source": 3})
        self.assertEqual(st, 200, out)
        self.assertEqual(self.src.to[0][0::2], (3, "stop"))
        st, out = self.post("/api/play", {"pad": [0, 0], "source": 3, "ending": "hold"})
        self.assertEqual(self.src.to[1][2], "hold")

    def test_source_1_is_the_play_it_always_was(self):
        st, out = self.post("/api/play", {"file": "a.mp4", "source": 1})
        self.assertEqual(st, 200, out)
        self.assertEqual(self.src.to, [])
        self.assertEqual(len(self.player.plays), 1)

    def test_what_is_not_a_clip_is_refused_in_plain_words(self):
        from pvj.api import Api
        banks = self.settings.data["pads"]["banks"]
        self.assertIsNotNone(Api.pad_shader(banks[0]["pads"][1]))
        self.assertIsNotNone(Api.pad_ndi(banks[0]["pads"][2]))
        self.assertIsNotNone(Api.pad_render(banks[0]["pads"][3]), "the render pad is a cue")
        for body, words in (({"pad": [0, 1]}, "this pad's shader plays on Source 1"), ({"pad": [0, 2]}, "this pad's NDI source"),
                            ({"pad": [0, 3]}, "this pad's render cue"), ({"stream": "x"}, "a stream"), ({"ndi": "x"}, "an NDI source"),
                            ({"capture": {"device": "/dev/video0"}}, "the capture input"), ({"slideshow": {}}, "a slideshow"),
                            ({"preset": "startlessonce05"}, "a preset"), ({"usb": "STICK/a.mp4"}, "a clip from a USB drive")):
            st, out = self.post("/api/play", dict(body, source=2))
            self.assertEqual(st, 409, body)
            self.assertIn(words, out["error"])
            self.assertIn("only clips play to Source 2 and up for now", out["error"])
        self.assertEqual((self.src.to, self.player.plays), ([], []), "nothing was played anywhere")
        self.assertEqual(self.post("/api/play", {"pad": [0, 5], "source": 2}), (400, {"error": "pad is empty"}))

    def test_a_sound_file_is_not_a_clip_for_a_source(self):
        # review of #153: it played with no sound track and no picture, black and silent, while the answer said it played
        open(os.path.join(self.media, "song.mp3"), "w").close()
        st, out = self.post("/api/play", {"file": "song.mp3", "source": 2})
        self.assertEqual(st, 409)
        self.assertEqual(out["error"], "only clips play to Source 2 and up for now; a sound file plays on Source 1: choose Source 1 under Play to, or a clip")
        with self.settings.lock:
            self.settings.data["pads"]["banks"][0]["pads"][6] = {"label": "Song", "file": "song.mp3", "ending": "loop"}
        self.assertEqual(self.post("/api/play", {"pad": [0, 6], "source": 3})[0], 409)
        self.assertEqual(self.src.to, [])
        self.assertEqual(self.post("/api/play", {"file": "b.mov", "source": 2})[0], 200, "a video still goes")
        self.assertEqual(self.post("/api/play", {"file": "song.mp3"})[0], 200, "Source 1 plays it as before")

    def test_bad_sources_and_sources_the_player_does_not_have(self):
        for k in (0, 9, -1, True, "2", 2.5, None):
            st, out = self.post("/api/play", {"file": "a.mp4", "source": k})
            self.assertEqual(st, 400, k)
        self.src.n = 3
        st, out = self.post("/api/play", {"file": "a.mp4", "source": 4})
        self.assertEqual((st, out["error"]), (409, "this box runs 3 sources; Source 4 is not one of them"))
        self.src.n = 1
        st, out = self.post("/api/play", {"file": "a.mp4", "source": 2})
        self.assertEqual(st, 409)
        self.assertIn("needs the New renderer", out["error"])
        self.assertEqual(self.src.to, [])
        st, out = self.post("/api/play", {"file": "../secret.mp4", "source": 2})
        self.assertIn(st, (400, 404))

    def test_roles_the_owner_and_an_operator_play_to_a_source_a_guest_does_not(self):
        self.assertEqual(self.post("/api/play", {"file": "a.mp4", "source": 2}, self.operator)[0], 200)
        self.assertEqual(self.post("/api/play", {"pad": [0, 0], "source": 2}, self.owner)[0], 200)
        for body in ({"file": "a.mp4", "source": 2}, {"pad": [0, 0], "source": 2}, {"file": "a.mp4", "source": 1}):
            st, out = self.post("/api/play", body, self.guest)
            self.assertEqual(st, 403, body)
        self.assertEqual(self.post("/api/play", {"file": "a.mp4"}, self.guest)[0], 200, "a guest's own play is as before")
        self.assertEqual(len(self.src.to), 2)
        for path in ("/api/sources",):
            self.assertEqual(self.call("GET", path, token=self.operator)[0], 200)
            self.assertEqual(self.call("GET", path, token=self.guest)[0], 403)
            self.assertEqual(self.post(path, {"source": 2, "action": "stop"}, self.guest)[0], 403)


class SourcesRouteTest(ApiBase):
    def test_what_each_source_plays_by_name_only(self):
        self.src.rows = [{"source": 1, "idle": False, "path": "/srv/media/a.mp4", "height": 1080, "pause": False, "drawn": 4},
                         {"source": 2, "idle": True, "path": None, "height": None, "pause": False, "drawn": 0}]
        self.src.n = 2
        st, out = self.call("GET", "/api/sources", token=self.operator)[:2]
        self.assertEqual(st, 200)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["sources"][0], {"source": 1, "idle": False, "name": "a.mp4", "height": 1080, "pause": False})
        self.assertNotIn("/srv", json.dumps(out), "never a path")
        self.assertEqual(out["cap"], 8, "an x86 box: up to 8")

    def test_stop_a_source(self):
        st, out = self.post("/api/sources", {"source": 3, "action": "stop"}, self.operator)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.src.stopped, [3])
        self.assertTrue(any("source 3 stopped" in ln for ln in self.lines))
        for body in ({"source": 1, "action": "stop"}, {"source": 3}, {"source": 3, "action": "play"}, {"source": 9, "action": "stop"},
                     {"source": 3, "action": "stop", "x": 1}, {}):
            self.assertEqual(self.post("/api/sources", body)[0], 400, body)
        self.src.n = 1
        self.assertEqual(self.post("/api/sources", {"source": 2, "action": "stop"})[0], 409)
        self.assertEqual(self.src.stopped, [3])

    def test_on_a_player_without_sources_there_is_source_1(self):
        for name in ("sources", "play_to", "stop_source", "sources_state"):
            delattr(self.player, name)
        st, out = self.call("GET", "/api/sources", token=self.owner)[:2]
        self.assertEqual((st, out["count"], [s["source"] for s in out["sources"]]), (200, 1, [1]))
        self.assertEqual(self.post("/api/play", {"file": "a.mp4", "source": 2})[0], 409)


class WarningTest(ApiBase):
    WARN = "this may drop frames on this box; 720p clips are lighter"

    def rows(self, heights):
        return [{"source": k, "idle": h is None, "path": None if h is None else "/m/c%d.mp4" % k, "height": h, "pause": False, "drawn": 0}
                for k, h in enumerate(heights, 1)]

    def test_a_1080p_clip_beside_two_others_is_warned_about_on_a_pi_4(self):
        self.api.board = dict(self.api.board, kind="pi4")
        self.src.rows = self.rows([720, 1080, 540, None])
        st, out = self.post("/api/play", {"file": "a.mp4", "source": 2})
        self.assertEqual((st, out.get("warning")), (200, self.WARN))
        self.assertEqual(self.call("GET", "/api/sources", token=self.owner)[1]["warning"], self.WARN)
        for heights in ([720, 720, 540, None], [None, 1080, 540, None], [720, 1080, None, None]):
            self.src.rows = self.rows(heights)
            st, out = self.post("/api/play", {"file": "a.mp4", "source": 2})
            self.assertNotIn("warning", out, heights)
        self.src.rows = self.rows([720, 1080, 540, None])
        self.api.board = dict(self.api.board, kind="x86")
        self.assertNotIn("warning", self.post("/api/play", {"file": "a.mp4", "source": 2})[1], "not a Pi: its numbers are not known")

    def test_the_rule(self):
        rows = self.rows([1080, 1080, 1080, 1080])
        self.assertEqual(renderer.source_warning(rows, 2, "pi4"), self.WARN)
        self.assertEqual(renderer.source_warning(rows, 2, "pi3"), self.WARN)
        self.assertIsNone(renderer.source_warning(rows, 2, "pi5"))
        self.assertIsNone(renderer.source_warning(self.rows([None, 1080, 1080, None]), 2, "pi4"), "one other plays")


class CountTest(unittest.TestCase):
    def test_the_cap_and_the_default_per_board(self):
        self.assertEqual([renderer.sources_for(b) for b in ("pi4", "pi3", "pi5", "x86", "")], [4, 2, 4, 4, 4])
        self.assertEqual([renderer.source_cap(b) for b in ("pi4", "pi3", "pi5", "x86")], [4, 2, 8, 8])
        self.assertEqual(renderer.sources_for("pi4", 8), 4, "never over the board's cap")
        self.assertEqual(renderer.sources_for("x86", 8), 8)
        self.assertEqual(renderer.sources_for("x86", 1), 1)
        self.assertEqual(renderer.sources_for("pi4", 0), 4, "a number out of range is the default")
        self.assertEqual(renderer.clean({"use": "new", "detail": 720, "sources": 3}), {"use": "new", "detail": 720, "sources": 3})
        for bad in (0, 9, True, "3", 3.0):
            self.assertNotIn("sources", renderer.clean({"use": "new", "sources": bad}), bad)
        self.assertEqual(renderer.command("/s", [], "auto", "pi4")[-1], "--sources=4")
        self.assertEqual(renderer.command("/s", [], "auto", "x86", sources=6)[-1], "--sources=6")


class RendererChoiceTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.owner, _ = self.pair("owner")
        self.api.renderer.env = dict(os.environ, PVJ_RENDER_BIN=sys.executable)       # "built"
        self.api.player.quit = lambda: None

    def test_the_owner_chooses_the_number_of_sources(self):
        st, out, _ = self.call("POST", "/api/renderer", {"use": "new", "sources": 3}, token=self.owner)
        self.assertEqual(st, 200, out)
        self.assertEqual(self.settings.data["renderer"], {"use": "new", "detail": "auto", "sources": 3})
        self.assertEqual(out["sources"], {"chosen": 3, "default": 4, "cap": 8, "starts": 3})
        st, out, _ = self.call("POST", "/api/renderer", {"use": "new", "detail": 720}, token=self.owner)
        self.assertEqual(self.settings.data["renderer"]["sources"], 3, "kept when not sent")
        st, out, _ = self.call("POST", "/api/renderer", {"use": "new", "sources": "auto"}, token=self.owner)
        self.assertNotIn("sources", self.settings.data["renderer"], "auto is no key")
        self.api.board = dict(self.api.board, kind="pi4")
        for bad in (5, 0, "4", True):
            st, out, _ = self.call("POST", "/api/renderer", {"use": "new", "sources": bad}, token=self.owner)
            self.assertEqual(st, 400, bad)
        self.assertEqual(self.call("POST", "/api/renderer", {"use": "new", "sources": 4}, token=self.owner)[0], 200)


class EngineTest(ServerBase):
    """The mapping engine writes the shader for the sources the player has."""

    def setUp(self):
        super().setUp()
        with self.settings.lock:
            self.settings.data["modules"]["enabled"]["mapper"] = True
            self.settings.data["mapper"] = {"on": True, "screen": [1920, 1080], "sets": {},
                                            "surfaces": mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, 960, 1080),
                                                                                              quad("aaaaaaa2", 960, 960, 1080, 2)]})}
            self.settings.save()

    def shown(self):
        """The text of the last shader the engine gave the player."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            sets = [c for c in self.player.calls if c[0] == "set_shaders" and c[1]]
            if sets and os.path.exists(sets[-1][1][0]):
                with open(sets[-1][1][0]) as f:
                    text = f.read()
                if "//!HOOK OUTPUT" in text:
                    return text
            time.sleep(0.05)
        self.fail("no shader was given to the player: %s" % self.player.calls[-5:])

    def test_the_state_asks_the_player_once_per_process(self):
        # review of #153: with only what was known (ask=False) a new player process said 1 until something else asked
        asked = []

        def sources(ask=True):
            asked.append(ask)
            return 4 if ask else 1
        self.player.sources = sources
        self.assertEqual(self.api.mapper.state()["sources"], 4)
        self.assertIn(True, asked)

    def test_classic_gets_the_shader_of_before_and_the_new_renderer_the_sources(self):
        self.api.mapper.apply()
        classic = self.shown()
        self.assertNotIn("SOURCE", classic)
        self.player.calls.clear()
        self.player.sources = lambda ask=True: 4
        self.api.mapper.apply()
        text = self.shown()
        self.assertIn("//!BIND SOURCE2\n", text)
        self.assertEqual(self.api.mapper.state()["sources"], 4)
        self.player.calls.clear()
        self.api.mapper.handle({"action": "edit", "on": True})
        self.assertIn("vec4 pick2(", self.shown())
        st = self.api.mapper.handle({"action": "source", "id": "aaaaaaa2", "source": 1})
        self.assertNotIn("source", st["surfaces"][1])
        self.assertNotIn("source", self.settings.data["mapper"]["surfaces"][1])
        with self.assertRaises(mapper.MapperError):
            self.api.mapper.handle({"action": "source", "id": "aaaaaaa2", "source": 9})
        with self.assertRaises(mapper.MapperError):
            self.api.mapper.handle({"action": "source", "id": "nope0000", "source": 2})


if __name__ == "__main__":
    unittest.main()
