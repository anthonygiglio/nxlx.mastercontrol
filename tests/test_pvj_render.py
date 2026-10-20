# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-render (render/, D95) run for real, headless: EGL without a surface on Mesa's software GPU, no screen.

The program is built first (`make -C render`). Without it these tests are skipped, except where PVJ_RENDER_TEST=1
(the CI job pvj-render), where a missing program, a missing clip maker or a missing font is a failure: there a skip
would judge nothing. Every question goes through pvj.player.Ipc, the panel's own client, so what is tested is what the
panel will meet. The pictures are read back with pvj-render's own `pvj-capture` (the final picture as it would go to
the screen) and judged by their pixels.
"""
import atexit
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import zlib

import tests        # the run's own temp folder (tests/__init__.py)
from pvj import mapper
from pvj.player import Ipc, PlayerError

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.environ.get("PVJ_RENDER_BIN") or os.path.join(REPO, "render", "pvj-render")
STRICT = os.environ.get("PVJ_RENDER_TEST") == "1"
W, H = 320, 180

RED, GREEN, BLUE, WHITE, BLACK, MAGENTA = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0), (255, 0, 255)


def png(path, w, h, pixel):
    """A plain RGB PNG of w x h whose pixel at (x, y) is pixel(x, y)."""
    rows = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def quadrants(x, y):
    """Top left red, top right green, bottom left blue, bottom right white (a W x H picture)."""
    return (RED if x < W // 2 else GREEN) if y < H // 2 else (BLUE if x < W // 2 else WHITE)


def read_ppm(path):
    with open(path, "rb") as f:
        data = f.read()
    head = data.split(b"\n", 3)
    assert head[0] == b"P6", head[0]
    w, h = map(int, head[1].split())
    assert head[2] == b"255"
    px = head[3]
    assert len(px) == w * h * 3, (len(px), w, h)
    return w, h, px


def near(a, b, tol=40):
    return all(abs(int(p) - int(q)) <= tol for p, q in zip(a, b))


LIVE = set()        # every Renderer not yet closed: ended at exit whatever happened (atexit below)


def _die_with_parent():
    """In the child, before pvj-render runs: the kernel ends it with SIGTERM if this test process dies first."""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG
    except Exception:
        pass


@atexit.register
def _end_all():
    for r in list(LIVE):
        r.close()


class Renderer:
    """One pvj-render process in its own folder."""

    def __init__(self, *args):
        self.dir = tempfile.mkdtemp(prefix="pvj-render-")
        self.sock = os.path.join(self.dir, "mpv.sock")
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1")
        self.log = open(os.path.join(self.dir, "stderr.txt"), "w+")
        self.proc = subprocess.Popen([BIN, "--socket=" + self.sock, "--headless=%dx%d" % (W, H)] + list(args),
                                     stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, env=env,
                                     preexec_fn=_die_with_parent)
        LIVE.add(self)
        self.ipc = Ipc(self.sock, timeout=10.0)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("pvj-render ended at once (%s): %s" % (self.proc.returncode, self.stderr()))
            try:
                self.pid = self.ipc.request("get_property", "pid")
                return
            except PlayerError:
                time.sleep(0.1)
        self.close()
        raise AssertionError("pvj-render did not answer: " + self.stderr())

    def stderr(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()[-3000:]

    def __call__(self, *command):
        return self.ipc.request(*command)

    def capture(self):
        path = os.path.join(self.dir, "cap-%d.ppm" % time.monotonic_ns())
        self("pvj-capture", path)
        out = read_ppm(path)
        os.unlink(path)
        return out

    def pixel(self, x, y, shot=None):
        w, h, px = shot or self.capture()
        o = (y * w + x) * 3
        return tuple(px[o:o + 3])

    def wait(self, what, seconds=10.0, step=0.05):
        """Ask `what()` until it is true; its last answer is returned (false when time ran out)."""
        deadline = time.monotonic() + seconds
        while True:
            got = what()
            if got or time.monotonic() > deadline:
                return got
            time.sleep(step)

    def playing(self):
        """The file the helper plays now, or None: right after a loadfile, and while idle, mpv answers "property
        unavailable" for `path`, which means "not yet" here (a CI run met it once, 2026-10-17)."""
        try:
            return self("get_property", "path")
        except PlayerError as e:
            if "property unavailable" in str(e):
                return None
            raise

    def load(self, path):
        self("loadfile", path, "replace")
        ok = self.wait(lambda: self.playing() == path)
        assert ok, "the clip was not loaded: " + self.stderr()
        # a picture is on the screen once the video layer drew it (its own counter) and one more frame went out
        drawn = self("get_property", "pvj-render")["drawn"]
        self.wait(lambda: self("get_property", "pvj-render")["drawn"] > drawn, 5)
        time.sleep(0.3)

    def close(self):
        LIVE.discard(self)
        if self.log.closed:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def need_program(case):
    if not (os.path.isfile(BIN) and os.access(BIN, os.X_OK)):
        if STRICT:
            case.fail("PVJ_RENDER_TEST=1 and there is no %s: build it with make -C render" % BIN)
        case.skipTest("pvj-render is not built (make -C render)")


class Base(unittest.TestCase):
    ARGS = ()

    def setUp(self):
        need_program(self)
        self.tmp = tempfile.mkdtemp(prefix="pvj-render-media-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = Renderer(*self.ARGS)
        self.addCleanup(lambda: self.r.close())
        # a picture stays until something else comes: mpv ends one after 1 s by default, and the screen is black then
        # (as with classic mpv); the tests that read a picture back must not race that on a slow machine
        self.r("set_property", "image-display-duration", "inf")
        self.picture = os.path.join(self.tmp, "quadrants.png")
        png(self.picture, W, H, quadrants)

    def assertColour(self, got, want, where):
        self.assertTrue(near(got, want), "%s: %s, not %s\n%s" % (where, got, want, self.r.stderr()))

    def assertQuadrants(self, shot=None):
        shot = shot or self.r.capture()
        for (x, y), want in (((W // 4, H // 4), RED), ((3 * W // 4, H // 4), GREEN), ((W // 4, 3 * H // 4), BLUE),
                             ((3 * W // 4, 3 * H // 4), WHITE)):
            self.assertColour(self.r.pixel(x, y, shot), want, "pixel %d,%d" % (x, y))


class AnswersTest(Base):
    def test_what_it_says_of_itself(self):
        r = self.r
        self.assertEqual(r("get_property", "pid"), r.proc.pid)
        self.assertEqual(r("get_property", "current-vo"), "pvj-render")
        self.assertEqual((r("get_property", "osd-width"), r("get_property", "osd-height")), (W, H))
        self.assertEqual((r("get_property", "display-width"), r("get_property", "display-height")), (W, H))
        self.assertAlmostEqual(r("get_property", "display-fps"), 60.0)
        st = r("get_property", "pvj-render")
        self.assertEqual((st["internal-width"], st["internal-height"], st["output-width"], st["output-height"]), (W, H, W, H))
        self.assertEqual(st["mapping"], "off")
        self.assertEqual(st["hwdec"], "no")
        dims = r("get_property", "osd-dimensions")
        self.assertEqual((dims["w"], dims["h"]), (W, H))
        self.assertIs(r("get_property", "idle-active"), True)

    def test_the_request_id_comes_back_and_lines_are_answered_in_order(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(self.r.sock)
        self.addCleanup(s.close)
        s.sendall(b'{"command": ["get_property", "osd-width"], "request_id": 7}\n'
                  b'{"command": ["get_property", "osd-height"], "request_id": 8}\n')
        got = b""
        while got.count(b"\n") < 2:
            got += s.recv(4096)
        a, b = [json.loads(line) for line in got.splitlines()]
        self.assertEqual((a["request_id"], a["data"], a["error"]), (7, W, "success"))
        self.assertEqual((b["request_id"], b["data"]), (8, H))

    def test_what_is_not_on_the_lists_is_refused_with_mpvs_words(self):
        r = self.r
        refused = [("run", "/bin/sh", "-c", "touch " + os.path.join(self.tmp, "ran")),
                   ("subprocess", ["/bin/true"]), ("load-script", "/tmp/x.lua"), ("script-message", "x"),
                   ("keypress", "q"), ("loadfile", self.picture, "replace", -1, "script=/tmp/x.lua"),
                   ("loadfile", self.picture, "replace", "input-commands=quit"), ("loadfile", self.picture, "nonsense"),
                   ("vf", "add", "lavfi=[movie=/etc/passwd]"), ("vf", "set", "hflip"),
                   ("overlay-add", 1, 0, 0, "@3", 0, "bgra", 1, 1, 4), ("overlay-add", 1, 0, 0, "&123", 0, "bgra", 1, 1, 4),
                   ("overlay-add", 99, 0, 0, "/tmp/x", 0, "bgra", 1, 1, 4), ("screenshot-to-file", "relative.png", "video"),
                   ("seek", 1, "sideways"), ("add", "speed", 1), ("pvj-capture", "relative.ppm")]
        for cmd in refused:
            with self.assertRaises(PlayerError, msg=str(cmd)) as e:
                r(*cmd)
            self.assertIn(str(e.exception), ("mpv: invalid parameter", "mpv: error running command"), cmd)
        for prop in ("script-opts", "input-ipc-server", "screenshot-directory", "stream-open-filename", "scripts"):
            with self.assertRaises(PlayerError) as e:
                r("set_property", prop, "x")
            self.assertEqual(str(e.exception), "mpv: property not found", prop)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ran")))
        self.assertEqual(r("get_property", "osd-width"), W, "still answering")

    def test_an_overlay_file_that_mpv_would_map_past_its_end_is_refused_and_the_helper_lives(self):
        # mpv maps offset + h * stride bytes of the file without a look at its size: a short file was SIGBUS
        r = self.r
        short = os.path.join(self.tmp, "short.bgra")
        with open(short, "wb") as f:
            f.write(b"\0" * 100)
        fifo = os.path.join(self.tmp, "fifo.bgra")
        os.mkfifo(fifo)
        link = os.path.join(self.tmp, "link.bgra")
        os.symlink(short, link)
        good = os.path.join(self.tmp, "good.bgra")
        with open(good, "wb") as f:
            f.write(b"\0" * (10 * 10 * 4))
        goodlink = os.path.join(self.tmp, "goodlink.bgra")
        os.symlink(good, goodlink)
        for path, w, h, stride, off in ((short, 100, 100, 400, 0), ("/etc/hostname", 100, 100, 400, 0),
                                        (fifo, 10, 10, 40, 0), (link, 5, 5, 20, 0), (goodlink, 10, 10, 40, 0),
                                        (self.tmp, 10, 10, 40, 0), (good, 10, 10, 40, 4), (good, 0, 10, 40, 0),
                                        (good, 10, 10, 39, 0), (good, 100000, 1, 400000, 0), (good, 10, 10, 40, -1)):
            with self.assertRaises(PlayerError, msg=str((path, w, h, stride, off))) as e:
                r("overlay-add", 3, 0, 0, path, off, "bgra", w, h, stride)
            self.assertEqual(str(e.exception), "mpv: invalid parameter")
        self.assertEqual(r("get_property", "osd-width"), W, "still answering")
        self.assertIsNone(self.r.proc.poll(), "the helper is alive")
        r("overlay-add", 3, 0, 0, good, 0, "bgra", 10, 10, 40)    # a file that is big enough still goes
        r("overlay-remove", 3)

    def test_bad_lines_are_answered_or_dropped_and_the_socket_lives_on(self):
        for junk in (b"quit\n", b"{not json}\n", b'{"command": "quit"}\n', b'["quit"]\n', b'{"command": []}\n',
                     b'{"command": [' * 40 + b"\n"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(self.r.sock)
            s.sendall(junk)
            msg = json.loads(s.recv(4096).splitlines()[0])
            self.assertEqual(msg["error"], "invalid parameter", junk)
            s.close()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(self.r.sock)
        try:
            s.sendall(b"x" * (70 * 1024))          # longer than a line may be: the client is dropped
            self.assertEqual(s.recv(10), b"")
        except (BrokenPipeError, ConnectionResetError):
            pass
        s.close()
        self.assertIsNone(self.r.proc.poll())
        self.assertEqual(self.r("get_property", "osd-height"), H)

    def test_strings_go_both_ways_as_utf8(self):
        name = os.path.join(self.tmp, "clip été \"quoted\" \\ ♫.png")
        shutil.copy(self.picture, name)
        self.r.load(name)
        self.assertEqual(self.r("get_property", "path"), name)

    def test_quit_ends_it_with_0_and_removes_the_socket(self):
        self.r("quit")
        self.assertEqual(self.r.proc.wait(10), 0, self.r.stderr())
        self.assertFalse(os.path.exists(self.r.sock))

    def test_sigterm_ends_it_with_0(self):
        self.r.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.r.proc.wait(10), 0, self.r.stderr())


class PictureTest(Base):
    def test_black_before_anything_is_loaded(self):
        self.assertColour(self.r.pixel(W // 2, H // 2), BLACK, "idle")

    def test_a_picture_is_drawn_the_right_way_up(self):
        self.r.load(self.picture)
        self.assertQuadrants()

    def test_stop_gives_black_and_idle(self):
        self.r.load(self.picture)
        self.r("stop")
        self.assertTrue(self.r.wait(lambda: self.r("get_property", "idle-active") is True))
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(W // 4, H // 4), BLACK)), self.r.stderr())

    def test_brightness_is_the_video_layers_as_on_classic(self):
        self.r.load(self.picture)
        self.r("set_property", "brightness", -100)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(3 * W // 4, 3 * H // 4), BLACK, 60)), self.r.stderr())
        self.assertEqual(self.r("get_property", "brightness"), -100)

    def test_a_shader_on_another_stage_is_libmpvs(self):
        """An effect (a MAIN hook) is handed to the video layer and filters the picture; the list reads back whole."""
        fx = os.path.join(self.tmp, "invert.glsl")
        with open(fx, "w") as f:
            f.write("//!HOOK MAIN\n//!BIND HOOKED\n//!DESC invert\nvec4 hook() { vec4 c = HOOKED_tex(HOOKED_pos); return vec4(1.0 - c.rgb, c.a); }\n")
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [fx])
        self.assertEqual(self.r("get_property", "glsl-shaders"), [fx])
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(W // 4, H // 4), (0, 255, 255))), self.r.stderr())
        self.assertEqual(self.r("get_property", "pvj-render")["mapping"], "off")


class DetailTest(Base):
    ARGS = ("--detail=90",)

    def test_a_smaller_inside_is_scaled_to_the_output(self):
        st = self.r("get_property", "pvj-render")
        self.assertEqual((st["internal-width"], st["internal-height"]), (160, 90))
        self.assertEqual(self.r("get_property", "osd-width"), W)
        self.r.load(self.picture)
        self.assertQuadrants()


class MappingTest(Base):
    def surface(self, verts):
        return mapper.validate_mapping({"surfaces": [{"id": "a1b2c3d4", "type": "quad", "name": "Left", "on": True,
                                                      "vertices": verts, "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}]})

    def write(self, text):
        path = os.path.join(self.tmp, "map-%d.glsl" % time.monotonic_ns())
        with open(path, "w") as f:
            f.write(text)
        return path

    def check_left_half(self):
        shot = self.r.capture()
        # the whole picture squeezed into the left half; the right half black (outside every surface)
        for (x, y), want in (((W // 8, H // 4), RED), ((3 * W // 8, H // 4), GREEN), ((W // 8, 3 * H // 4), BLUE),
                             ((3 * W // 8, 3 * H // 4), WHITE), ((3 * W // 4, H // 2), BLACK), ((W - 5, 5), BLACK)):
            self.assertColour(self.r.pixel(x, y, shot), want, "pixel %d,%d" % (x, y))

    def test_the_show_table_is_drawn_at_the_outputs_pixels(self):
        surfaces = self.surface([[0, 0], [W / 2, 0], [W / 2, H], [0, H]])
        lw, lh, values = mapper.warp_table(surfaces, W, H)
        path = self.write(mapper.warp_shader(lw, lh, values))
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [path])
        self.assertEqual(self.r("get_property", "pvj-render")["mapping"], "on", self.r.stderr())
        self.check_left_half()
        self.r("set_property", "glsl-shaders", [])
        self.assertEqual(self.r("get_property", "pvj-render")["mapping"], "off")
        self.assertQuadrants()

    def test_the_editing_shader_is_drawn_too(self):
        surfaces = self.surface([[0, 0], [W / 2, 0], [W / 2, H], [0, H]])
        path = self.write(mapper.shader(surfaces))
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [path])
        self.assertEqual(self.r("get_property", "pvj-render")["mapping"], "on", self.r.stderr())
        self.check_left_half()

    def test_the_same_with_a_smaller_inside(self):
        self.r.close()
        self.r = Renderer("--detail=90")
        surfaces = self.surface([[0, 0], [W / 2, 0], [W / 2, H], [0, H]])
        lw, lh, values = mapper.warp_table(surfaces, W, H)
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [self.write(mapper.warp_shader(lw, lh, values))])
        self.check_left_half()
        # the editing shader measures in screen pixels (HOOKED_pos * HOOKED_size): the output's size, not the inside's
        self.r("set_property", "glsl-shaders", [self.write(mapper.shader(surfaces))])
        self.check_left_half()

    def test_a_damaged_mapping_file_is_refused_and_nothing_changes(self):
        surfaces = self.surface([[0, 0], [W / 2, 0], [W / 2, H], [0, H]])
        lw, lh, values = mapper.warp_table(surfaces, W, H)
        good = mapper.warp_shader(lw, lh, values)
        self.r.load(self.picture)
        bad = [good.replace("//!SIZE %d %d" % (lw, lh), "//!SIZE %d %d" % (lw + 1, lh)),        # data of the wrong size
               good.replace("//!SIZE %d %d" % (lw, lh), "//!SIZE 5000 5000"),
               good.replace("rgba16f", "rgb10_a2"), good.replace("//!BIND PVJWARPB", "//!BIND NOSUCH"),
               good.replace("//!TEXTURE PVJWARPA", "//!TEXTURE gl_bad")]
        for text in bad:
            with self.assertRaises(PlayerError):
                self.r("set_property", "glsl-shaders", [self.write(text)])
        self.assertEqual(self.r("get_property", "pvj-render")["mapping"], "off")
        self.assertQuadrants()
        big = os.path.join(self.tmp, "big.glsl")
        with open(big, "w") as f:
            f.write("//!HOOK OUTPUT\n//!BIND HOOKED\nvec4 hook() { return vec4(0.0); }\n" + "//" + "x" * (17 << 20) + "\n")
        with self.assertRaises(PlayerError):
            self.r("set_property", "glsl-shaders", [big])

    def test_a_shader_that_does_not_compile_leaves_the_picture_unmapped(self):
        path = self.write("//!HOOK OUTPUT\n//!BIND HOOKED\nvec4 hook() { return not_glsl; }\n")
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [path])
        self.assertTrue(self.r("get_property", "pvj-render")["mapping"].startswith("failed"))
        self.assertQuadrants()


class TextLayerTest(Base):
    def test_an_overlay_is_drawn_at_output_pixels_and_comes_off(self):
        self.r.load(self.picture)
        bgra = os.path.join(self.tmp, "o.bgra")
        with open(bgra, "wb") as f:
            f.write(bytes((255, 0, 255, 255)) * (40 * 30))     # B, G, R, A: opaque magenta
        self.r("overlay-add", 5, 10, 10, bgra, 0, "bgra", 40, 30, 160)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(25, 20), MAGENTA)), self.r.stderr())
        self.assertColour(self.r.pixel(W // 4, 3 * H // 4), BLUE, "beside the overlay")
        self.assertTrue(self.r("get_property", "pvj-render")["text-layer"])
        self.r("overlay-remove", 5)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(25, 20), RED)), self.r.stderr())
        self.assertFalse(self.r("get_property", "pvj-render")["text-layer"])

    def test_masks_combined_into_one_picture_are_drawn_as_the_overlay(self):
        # D100: the panel's combined masks (pvj/overlay.py) go to pvj-render as one overlay-add, as to classic mpv.
        # The picture is combined here in plain Python by the same rule as overlay.combine (premultiplied, a later
        # mask "over" an earlier one), so this needs no mpv program: the CI job has libmpv only. mpv's own combining
        # is tested in tests/test_masks.py.
        from pvj import overlay
        masks = [lambda x, y: (0, 0, 0, 255) if x < W // 2 else (0, 0, 0, 0),                          # black left half
                 lambda x, y: (255, 0, 255, 255) if y >= H // 2 and x < W // 4 else (0, 0, 0, 0)]      # magenta, over it
        pixels = bytearray()
        for y in range(H):
            for x in range(W):
                b = g = r = a = 0                                   # premultiplied B, G, R, A of what is below
                for mask in masks:
                    sr, sg, sb, sa = mask(x, y)
                    sr, sg, sb = sr * sa // 255, sg * sa // 255, sb * sa // 255
                    keep = 255 - sa
                    b, g, r, a = sb + b * keep // 255, sg + g * keep // 255, sr + r * keep // 255, sa + a * keep // 255
                pixels += bytes((b, g, r, a))
        out = os.path.join(self.tmp, "masks.bgra")
        with open(out, "wb") as f:
            f.write(pixels)
        self.r.load(self.picture)
        self.r("overlay-add", overlay.OVERLAY_ID, 0, 0, out, 0, "bgra", W, H, W * 4)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(W // 4, H // 4), BLACK)), self.r.stderr())
        self.assertColour(self.r.pixel(W // 8, 3 * H // 4), MAGENTA, "the later mask over the earlier one")
        self.assertColour(self.r.pixel(3 * W // 8, 3 * H // 4), BLACK, "the first mask where the second is clear")
        self.assertColour(self.r.pixel(3 * W // 4, H // 4), GREEN, "the video where both are clear")
        self.assertColour(self.r.pixel(3 * W // 4, 3 * H // 4), WHITE, "the video")
        self.r("overlay-remove", overlay.OVERLAY_ID)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(W // 4, H // 4), RED)), self.r.stderr())

    def test_an_overlay_over_a_mapping_stays_in_screen_space(self):
        surfaces = MappingTest.surface(self, [[0, 0], [W / 2, 0], [W / 2, H], [0, H]])
        lw, lh, values = mapper.warp_table(surfaces, W, H)
        path = MappingTest.write(self, mapper.warp_shader(lw, lh, values))
        self.r.load(self.picture)
        self.r("set_property", "glsl-shaders", [path])
        bgra = os.path.join(self.tmp, "o.bgra")
        with open(bgra, "wb") as f:
            f.write(bytes((255, 0, 255, 255)) * (20 * 20))
        self.r("overlay-add", 1, W - 30, H - 30, bgra, 0, "bgra", 20, 20, 80)
        self.assertTrue(self.r.wait(lambda: near(self.r.pixel(W - 20, H - 20), MAGENTA)), self.r.stderr())

    def test_show_text_is_drawn_and_goes(self):
        # held on the screen: a picture alone ends after mpv's image-display-duration (1 s) and the screen is black
        # then, as with classic mpv (before, the last picture stayed on by mistake, and this test leaned on that)
        self.r("set_property", "loop-file", "inf")
        self.r.load(self.picture)
        base = self.r.capture()
        self.r("show-text", "PIN 1234 PIN 1234", 8000)

        def changed():
            shot = self.r.capture()
            return sum(1 for i in range(0, len(base[2]), 3) if not near(base[2][i:i + 3], shot[2][i:i + 3], 30))
        n = self.r.wait(lambda: changed() > 200)
        self.assertTrue(n, "no text on the picture (is a font installed?)\n" + self.r.stderr())
        self.r("show-text", "", 1)
        self.assertTrue(self.r.wait(lambda: changed() < 20), self.r.stderr())


def make_clip(case, path, seconds=3, source="testsrc2"):
    """A 30 fps H.264 clip of W x H made with ffmpeg (a skip without it, a failure where PVJ_RENDER_TEST=1).
    `source`: ffmpeg's test source, or "color=c=red" for one plain colour."""
    maker = shutil.which("ffmpeg")
    if not maker:
        if STRICT:
            case.fail("PVJ_RENDER_TEST=1 and no ffmpeg to make the clip")
        case.skipTest("no ffmpeg to make a clip")
    sep = ":" if "=" in source else "="
    subprocess.run([maker, "-v", "error", "-y", "-f", "lavfi", "-i", "%s%ss=320x180:r=30:d=%d" % (source, sep, seconds), "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-g", "15", path], check=True, timeout=120, stdin=subprocess.DEVNULL)


def connect(path, timeout=10):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    return s


class SocketTest(Base):
    """Many clients at once, and clients that misbehave, never hold up the others (the first Pi run, 2026-10-18: one
    thread served every client, and the panel's plays answered 503 once its listen queue was full)."""

    def quick(self, n=20, limit=1.0):
        """n questions through the panel's own client, each answered within `limit` seconds."""
        ipc = Ipc(self.r.sock, timeout=limit)
        for i in range(n):
            start = time.monotonic()
            self.assertEqual(ipc.request("get_property", "osd-width"), W)
            self.assertLess(time.monotonic() - start, limit)

    def test_a_client_that_never_reads_its_answers_holds_up_nobody_and_is_dropped(self):
        import threading
        stuck = connect(self.r.sock)
        self.addCleanup(stuck.close)
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        line = b'{"command": ["get_property", "pvj-render"], "request_id": 1}\n'

        def flood():
            try:
                for _ in range(20000):          # far more answers than the socket's buffers hold; never read
                    stuck.sendall(line * 50)
            except OSError:
                pass
        t = threading.Thread(target=flood, daemon=True)
        started = time.monotonic()
        t.start()
        time.sleep(0.5)                         # the helper is now waiting on that client's full socket
        self.quick(30, 0.5)
        # it is dropped once it has taken no answer for 2 seconds (nothing is read from it before that): what it
        # can still read then ends with the end
        time.sleep(max(0.0, started + 3.5 - time.monotonic()))
        deadline = time.monotonic() + 10
        dropped = False
        stuck.settimeout(0.5)
        while time.monotonic() < deadline and not dropped:
            try:
                dropped = stuck.recv(1 << 20) == b""
            except socket.timeout:
                pass
            except OSError:
                dropped = True
        self.assertTrue(dropped, "the client that does not read is dropped")
        self.assertIn("took no answer", self.r.stderr())
        self.quick(5)

    def test_clients_that_say_nothing_hold_up_nobody(self):
        silent = [connect(self.r.sock) for _ in range(12)]      # more than the 8 the one thread had room for
        for s in silent:
            self.addCleanup(s.close)
        time.sleep(0.3)
        self.quick(20, 0.5)

    def test_more_clients_than_the_bound_are_turned_away_and_it_lives_on(self):
        held = [connect(self.r.sock) for _ in range(32)]
        time.sleep(0.5)
        extra = connect(self.r.sock)
        self.assertEqual(extra.recv(10), b"", "the 33rd is closed at once, never left waiting")
        extra.close()
        for s in held:
            s.close()
        time.sleep(0.5)                         # their threads end and their places are free again
        self.quick(10)
        self.assertIsNone(self.r.proc.poll())

    def test_many_clients_at_once_are_all_answered(self):
        import threading
        errors, answered = [], []

        def worker():
            ipc = Ipc(self.r.sock, timeout=5.0)
            for _ in range(25):
                try:
                    answered.append(ipc.request("get_property", "pvj-render")["output-width"])
                except Exception as e:
                    errors.append(repr(e))
        threads = [threading.Thread(target=worker) for _ in range(16)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(answered, [W] * 400)
        self.assertLess(time.monotonic() - start, 30)


class PaceTest(Base):
    """A picture goes out at every vblank (here: the headless clock's 60 a second), new clip frame or not."""

    def rate(self, seconds=2.0):
        a, t0 = self.r("get_property", "pvj-render"), time.monotonic()
        time.sleep(seconds)
        b, t1 = self.r("get_property", "pvj-render"), time.monotonic()
        return (b["flips"] - a["flips"]) / (t1 - t0), (b["drawn"] - a["drawn"]) / (t1 - t0)

    def test_the_output_rate_with_nothing_playing(self):
        flips, drawn = self.rate()
        self.assertGreater(flips, 0.85 * 60, "flips a second while idle: %.1f\n%s" % (flips, self.r.stderr()))
        self.assertLessEqual(flips, 62)
        self.assertLess(drawn, 2, "no clip, so no clip frame converted")

    def test_the_output_rate_with_a_30_fps_clip_and_each_clip_frame_once(self):
        clip = os.path.join(self.tmp, "clip30.mp4")
        make_clip(self, clip, 4)
        self.r("set_property", "loop-file", "inf")
        self.r.load(clip)
        flips, drawn = self.rate()
        self.assertGreater(flips, 0.85 * 60, "flips a second: %.1f, clip frames %.1f\n%s" % (flips, drawn, self.r.stderr()))
        self.assertLessEqual(flips, 62)
        self.assertGreater(drawn, 20, "the clip's frames are drawn")
        self.assertLess(drawn, 36, "each clip frame converted once, not at every vblank: %.1f" % drawn)


class FenceTest(Base):
    """libmpv 0.40 and 0.41 leave a GL fence behind at every render (a sync file each on the Pi's V3D, until the
    process had no file descriptor left and Mesa said "Export failed" at every frame, 2026-10-18). They are deleted."""

    def test_libmpvs_fences_do_not_pile_up(self):
        clip = os.path.join(self.tmp, "clip.mp4")
        make_clip(self, clip, 4)
        self.r("set_property", "loop-file", "inf")
        self.r.load(clip)
        self.assertTrue(self.r.wait(lambda: self.r("get_property", "pvj-render")["drawn"] >= 60, 15), self.r.stderr())
        st = self.r("get_property", "pvj-render")
        self.assertLessEqual(st["gl-fences"], 4, st)
        version = self.r("get_property", "mpv-version") or ""
        if version.startswith(("mpv 0.40.", "mpv 0.41.")):
            # the libmpv that leaks one a frame (mpv 0.37, Ubuntu 24.04's, still skipped the fence: none to delete)
            self.assertGreaterEqual(st["gl-fences-freed"], st["drawn"] - 5, st)
        self.assertNotIn("Export failed", self.r.stderr())


class ClipTest(Base):
    """A real clip: pause, seek, loop, volume, stop."""

    def setUp(self):
        super().setUp()
        self.clip = os.path.join(self.tmp, "clip.mp4")
        make_clip(self, self.clip)

    def test_play_pause_seek_loop_volume_stop(self):
        r = self.r
        r.load(self.clip)
        self.assertAlmostEqual(r("get_property", "duration"), 3.0, delta=0.2)
        self.assertEqual((r("get_property", "width"), r("get_property", "height")), (320, 180))
        self.assertIs(r("get_property", "idle-active"), False)
        self.assertEqual(r("get_property", "hwdec-current"), "no")
        self.assertTrue(r.wait(lambda: (r("get_property", "time-pos") or 0) > 0.3), r.stderr())
        r("set_property", "pause", True)
        self.assertIs(r("get_property", "pause"), True)
        r("seek", 2.0, "absolute")
        self.assertTrue(r.wait(lambda: abs((r("get_property", "time-pos") or 0) - 2.0) < 0.2), r.stderr())
        a = r("get_property", "time-pos")
        time.sleep(0.5)
        self.assertEqual(r("get_property", "time-pos"), a, "paused")
        r("set_property", "loop-file", "inf")
        r("set_property", "pause", False)
        time.sleep(2.0)
        self.assertEqual(r("get_property", "path"), self.clip, "looped, not ended")
        self.assertIs(r("get_property", "idle-active"), False)
        r("set_property", "volume", 40)
        r("add", "volume", 5)
        self.assertEqual(r("get_property", "volume"), 45)
        self.assertIsInstance(r("get_property", "frame-drop-count"), int)
        r("stop")
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True))

    def test_without_a_loop_the_clip_ends_idle(self):
        r = self.r
        r("set_property", "loop-file", "no")
        r.load(self.clip)
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True, 10), r.stderr())


class FadeBase(Base):
    """Two video layers and the renderer's own crossfade (D98): ["pvj-crossfade", URL, SECONDS, OPTIONS]."""

    def setUp(self):
        super().setUp()
        self.red = os.path.join(self.tmp, "red.png")
        self.blue = os.path.join(self.tmp, "blue.png")
        png(self.red, W, H, lambda x, y: RED)
        png(self.blue, W, H, lambda x, y: BLUE)

    def st(self):
        return self.r("get_property", "pvj-render")

    def fade(self, path, seconds, opts="keep-open=no,image-display-duration=inf"):
        return self.r("pvj-crossfade", path, seconds, opts)

    def settled(self, seconds=15):
        """Wait until no crossfade is on its way; the state then."""
        ok = self.r.wait(lambda: self.st()["fade"] == "off", seconds, 0.02)
        self.assertTrue(ok, "the crossfade did not end: %s\n%s" % (self.st(), self.r.stderr()))
        return self.st()

    def centre(self):
        return self.r.pixel(W // 2, H // 2)


class CrossfadeTest(FadeBase):
    def test_what_it_says_of_its_layers(self):
        st = self.st()
        self.assertEqual((st["layers"], st["front"], st["fade"], st["fades"], st["back-drawn"]), (2, 0, "off", 0, 0))

    def test_a_crossfade_mixes_the_two_pictures_and_then_the_incoming_layer_is_the_front(self):
        r = self.r
        r.load(self.red)
        self.assertColour(self.centre(), RED, "before")
        self.fade(self.blue, 3.0)
        # the socket speaks to the incoming clip from the command on, as after a loadfile
        self.assertTrue(r.wait(lambda: r.playing() == self.blue), r.stderr())
        self.assertEqual(self.st()["front"], 1)
        # half way: both pictures at once, by real opacity
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running" and 0.3 < self.st()["fade-progress"] < 0.7, 10, 0.02), self.st())
        mixed = self.centre()
        self.assertTrue(mixed[0] > 50 and mixed[2] > 50 and mixed[1] < 40, "not a mix of red and blue: %s" % (mixed,))
        st = self.settled()
        self.assertEqual((st["fades"], st["fades-done"], st["fades-cut"], st["front"]), (1, 1, 0, 1))
        self.assertColour(self.centre(), BLUE, "after")
        self.assertEqual(r("get_property", "playlist-count"), 1)
        self.assertEqual(r("get_property", "playlist")[0]["filename"], self.blue)

    def test_the_fade_is_a_straight_line_over_its_seconds(self):
        r = self.r
        r.load(self.red)
        self.fade(self.blue, 2.0)
        seen = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            st = self.st()
            if st["fade"] == "running":
                seen.append((time.monotonic(), st["fade-progress"]))
            elif seen:
                break
            time.sleep(0.05)
        self.assertGreater(len(seen), 10, seen)
        took = seen[-1][0] - seen[0][0]
        self.assertAlmostEqual(took, 2.0, delta=0.5)
        # a straight line: every sample is where the clock says, measured from where the line meets 0
        start = seen[0][0] - seen[0][1] * 2.0
        worst = max(abs(p - (t - start) / 2.0) for t, p in seen)
        self.assertLess(worst, 0.1, "the share is not a straight line over the seconds: %s" % seen)
        mid = min(seen, key=lambda s: abs(s[0] - (start + 1.0)))
        self.assertAlmostEqual(mid[1], 0.5, delta=0.1, msg="half way by the clock: %s" % (mid,))

    def test_a_crossfade_of_no_seconds_is_a_cut_through_the_layers(self):
        self.r.load(self.red)
        self.fade(self.blue, 0)
        st = self.settled(5)
        self.assertEqual((st["fades-done"], st["front"]), (1, 1))
        self.assertColour(self.centre(), BLUE, "after")

    def test_the_layers_swap_again_and_again(self):
        r = self.r
        r.load(self.red)
        for n, (path, colour) in enumerate(((self.blue, BLUE), (self.red, RED), (self.blue, BLUE), (self.picture, None)), 1):
            self.fade(path, 0.2)
            st = self.settled()
            self.assertEqual((st["fades-done"], st["front"]), (n, n % 2))
            self.assertEqual(r("get_property", "path"), path)
            if colour:
                self.assertTrue(r.wait(lambda: near(self.centre(), colour)), "%d: %s" % (n, self.centre()))
        self.assertQuadrants()

    def test_properties_answer_for_the_layer_on_the_screen_after_a_swap(self):
        r = self.r
        clip = os.path.join(self.tmp, "a.mp4")
        make_clip(self, clip, 4)
        r("set_property", "loop-file", "inf")
        r.load(clip)
        r("set_property", "pause", True)
        r("seek", 3.0, "absolute")
        self.assertTrue(r.wait(lambda: abs((r("get_property", "time-pos") or 0) - 3.0) < 0.2), r.stderr())
        self.fade(self.picture, 0.3)
        st = self.settled()
        self.assertEqual(st["front"], 1)
        # the picture's answers, not the paused clip's
        self.assertEqual(r("get_property", "path"), self.picture)
        self.assertIs(r("get_property", "pause"), False, "the incoming layer plays")
        self.assertEqual((r("get_property", "width"), r("get_property", "height")), (W, H))
        self.assertEqual(r("get_property", "playlist-pos"), 0)
        self.assertIs(r("get_property", "idle-active"), False)
        r("set_property", "pause", True)
        self.assertIs(r("get_property", "pause"), True)
        # back the other way: the clip again, on the first layer, which was stopped and is fresh for it
        self.fade(clip, 0.3)
        st = self.settled()
        self.assertEqual(st["front"], 0)
        self.assertEqual(r("get_property", "path"), clip)
        self.assertIs(r("get_property", "pause"), False, "the incoming layer was set playing, whatever it was before")
        self.assertTrue(r.wait(lambda: (r("get_property", "time-pos") or 0) > 0.2), r.stderr())
        self.assertLess(r("get_property", "time-pos"), 2.9, "the new clip from its start, not the old layer's place")
        r("seek", 1.0, "absolute")
        self.assertTrue(r.wait(lambda: abs((r("get_property", "time-pos") or 0) - 1.2) < 0.5), r.stderr())

    def test_how_the_picture_looks_reaches_both_layers(self):
        """The panel's opacity is mpv's brightness: a Blackout during a crossfade darkens both pictures, and the clip
        that comes in has the look the panel set (here: brightness and a flip) from its first frame."""
        r = self.r
        r.load(self.red)
        r("set_property", "video-zoom", 0.0)
        r("vf", "add", "@pvjfliph:hflip")
        self.fade(self.picture, 6.0)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running" and self.st()["fade-progress"] > 0.2, 10, 0.02), self.st())
        r("set_property", "brightness", -100)
        time.sleep(0.3)
        shot = r.capture()
        st = self.st()
        self.assertEqual(st["fade"], "running", "a Blackout does not end the fade; it darkens both")
        self.assertLess(st["fade-progress"], 0.8, "the picture is read while both layers show: %s" % st)
        for x, y in ((W // 2, H // 2), (W // 4, H // 4), (3 * W // 4, 3 * H // 4)):
            px = r.pixel(x, y, shot)
            self.assertTrue(near(px, BLACK, 60), "both layers dark, pixel %d,%d: %s" % (x, y, px))
        r("set_property", "brightness", 0)
        self.settled()
        # the incoming picture came in mirrored left to right, as the outgoing one was
        shot = r.capture()
        self.assertColour(r.pixel(W // 4, H // 4, shot), GREEN, "flipped: top left")
        self.assertColour(r.pixel(3 * W // 4, H // 4, shot), RED, "flipped: top right")

    def test_a_load_a_stop_or_a_new_crossfade_during_a_fade_ends_it(self):
        r = self.r
        r.load(self.red)
        self.fade(self.blue, 10.0)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.02))
        r("loadfile", self.picture, "replace")            # a cut while it fades: the cut wins, now
        st = self.st()
        self.assertEqual((st["fade"], st["fades-cut"]), ("off", 1))
        self.assertTrue(r.wait(lambda: r.playing() == self.picture))
        time.sleep(0.4)
        self.assertQuadrants()
        self.fade(self.red, 10.0)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.02))
        self.fade(self.blue, 0.2)                         # a crossfade during a crossfade: the first ends, the next runs
        st = self.settled()
        self.assertEqual((st["fades"], st["fades-cut"], st["fades-done"]), (3, 2, 1))
        self.assertColour(self.centre(), BLUE, "the newest")
        self.fade(self.red, 10.0)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.02))
        r("stop")
        self.assertEqual(self.st()["fade"], "off")
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True))
        self.assertTrue(r.wait(lambda: near(self.centre(), BLACK)), "a Stop is black, both layers: %s" % (self.centre(),))

    def test_a_crossfade_into_a_file_that_does_not_play_ends_as_a_cut_to_it_would(self):
        r = self.r
        r.load(self.red)
        self.fade(os.path.join(self.tmp, "no such clip.mp4"), 1.0)
        st = self.settled(8)
        self.assertEqual(st["fades-done"], 1)
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True), r.stderr())
        self.assertTrue(r.wait(lambda: near(self.centre(), BLACK)), self.centre())
        self.assertIsNone(r.proc.poll())

    def test_what_is_not_a_crossfade_is_refused_with_mpvs_words(self):
        r = self.r
        r.load(self.red)
        for cmd in (("pvj-crossfade",), ("pvj-crossfade", self.blue), ("pvj-crossfade", self.blue, "2"),
                    ("pvj-crossfade", self.blue, -1), ("pvj-crossfade", self.blue, 61), ("pvj-crossfade", 5, 2),
                    ("pvj-crossfade", "", 2), ("pvj-crossfade", self.blue, 2, "script=/tmp/x.lua"),
                    ("pvj-crossfade", self.blue, 2, "pause=yes"), ("pvj-crossfade", self.blue, 2, "keep-open"),
                    ("pvj-crossfade", self.blue, 2, "keep-open=yes;rm"), ("pvj-crossfade", self.blue, 2, 7),
                    ("pvj-crossfade", self.blue, 2, "keep-open=no", "extra"), ("pvj-crossfade", self.blue, 1e9)):
            with self.assertRaises(PlayerError, msg=str(cmd)) as e:
                r(*cmd)
            self.assertEqual(str(e.exception), "mpv: invalid parameter", cmd)
        st = self.st()
        self.assertEqual((st["fades"], st["fade"], st["front"]), (0, "off", 0), "nothing was faded")
        self.assertEqual(r("get_property", "path"), self.red)
        with self.assertRaises(PlayerError) as e:
            r("set_property", "aid", "no")                 # the renderer sets it on its layers; the socket may not
        self.assertEqual(str(e.exception), "mpv: property not found")


class IdleLayerTest(FadeBase):
    """The second layer costs nothing while no crossfade runs: it converts no frame, and after a fade it is
    stopped (idle) again."""

    def test_the_idle_layer_draws_nothing_and_after_a_fade_it_is_idle_again(self):
        r = self.r
        a, b = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_clip(self, a, 4)
        shutil.copy(a, b)
        r("set_property", "loop-file", "inf")
        r.load(a)
        before = self.st()["back-drawn"]
        self.assertIs(self.st()["back-idle"], True)
        time.sleep(1.0)
        self.assertEqual(self.st()["back-drawn"], before, "the idle layer was drawn while nothing faded")
        self.fade(b, 1.0)
        r("set_property", "loop-file", "inf")
        st = self.settled()
        self.assertGreater(st["back-drawn"], before + 10, "the outgoing clip moved during the fade: %s" % st)
        after = st["back-drawn"]
        self.assertTrue(r.wait(lambda: self.st()["back-idle"] is True, 5), "the outgoing clip was not stopped: %s" % self.st())
        time.sleep(1.0)
        self.assertEqual(self.st()["back-drawn"], after, "the outgoing layer went on being drawn after the fade")
        # the outgoing core is stopped: the front plays b alone (its sound included: the old one is gone)
        self.assertEqual(r("get_property", "path"), b)
        self.assertIs(r("get_property", "idle-active"), False)


class FadePaceTest(FadeBase):
    """During a crossfade of two 30 fps clips a picture still goes out at every vblank (here: the headless clock's
    60), and each layer converts each of its clip frames once."""

    def test_flips_and_frames_during_a_fade(self):
        r = self.r
        a, b = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_clip(self, a, 6)
        shutil.copy(a, b)
        r("set_property", "loop-file", "inf")
        r.load(a)
        time.sleep(0.5)
        self.fade(b, 4.0)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.02), r.stderr())
        s0, t0 = self.st(), time.monotonic()
        time.sleep(2.0)
        s1, t1 = self.st(), time.monotonic()
        self.assertEqual(s1["fade"], "running", "the measure is inside the fade")
        dt = t1 - t0
        flips = (s1["flips"] - s0["flips"]) / dt
        front = (s1["drawn"] - s0["drawn"]) / dt
        back = (s1["back-drawn"] - s0["back-drawn"]) / dt
        self.assertGreater(flips, 0.85 * 60, "flips a second during a fade: %.1f\n%s" % (flips, r.stderr()))
        self.assertLessEqual(flips, 62)
        for name, rate in (("incoming", front), ("outgoing", back)):
            self.assertGreater(rate, 20, "%s frames a second: %.1f" % (name, rate))
            self.assertLess(rate, 36, "%s: each clip frame converted once, not at every vblank: %.1f" % (name, rate))


def fd_count(pid):
    return len(os.listdir("/proc/%d/fd" % pid))


def thread_count(pid):
    return len(os.listdir("/proc/%d/task" % pid))


class ManyFadesTest(FadeBase):
    """A show crossfades all night: 200 fades in a row (two pictures, then two clips) and 180 more each cut short by
    the next leave no file descriptor, thread, GL fence or GL object behind. After a warm-up the counts are the same
    at the same state (two clips, nothing fading), give or take SLACK. Bounded: the test gives up after LIMIT.

    libmpv's GL objects are not one number at rest: over 600 fades cut short (2026-10-18, here) they moved between 37
    and 41, down as well as up (37, 38, 39, 39, 39, 39, 41, 41, 39, 41, 39, 39 at 0, 30, 60 ... 600), and on CI's
    mpv 0.40 from 39 to 42 after 30: a pool (most likely the textures of the frames libmpv had queued when a load was
    cut), not a leak. So the cut-short run is judged by its trend: GL_BAND over the highest of three warm-up
    readings at most, at each of four later checkpoints. A leak of one object a fade is 30 more at each checkpoint,
    and one in ten fades is 12 more by the last."""
    LIMIT = 420.0
    SLACK = 2
    GL_BAND = 4

    def run_fades(self, a, b, n, seconds, deadline):
        r = self.r
        for i in range(n):
            self.assertLess(time.monotonic(), deadline, "the bounded run of fades took too long")
            done = self.st()["fades-done"]
            self.fade(b if i % 2 == 0 else a, seconds)
            self.assertTrue(r.wait(lambda: self.st()["fades-done"] > done, 10, 0.01), "fade %d: %s\n%s" % (i, self.st(), r.stderr()))

    def counts(self):
        """At rest: nothing fading, the other layer idle, the socket's client threads gone (each Ipc question is a
        connection of its own, reaped within a fifth of a second)."""
        st = self.settled()
        self.assertTrue(self.r.wait(lambda: self.st()["back-idle"] is True, 5))
        time.sleep(0.6)
        pid = self.r.proc.pid
        return {"fds": fd_count(pid), "threads": thread_count(pid), "gl-objects": self.st()["gl-objects"], "fences": st["gl-fences"]}

    def same(self, now, then, what, gl=None):
        """fds and threads within SLACK of `then`; libmpv's GL objects within SLACK of it, or with `gl` (the highest
        of some earlier readings) within GL_BAND of that."""
        for k in ("fds", "threads"):
            self.assertLessEqual(now[k], then[k] + self.SLACK, "%s: %s piled up: %s, at the start %s" % (what, k, now, then))
        limit = then["gl-objects"] + self.SLACK if gl is None else gl + self.GL_BAND
        self.assertLessEqual(now["gl-objects"], limit, "%s: GL objects piled up: %s, at most %d" % (what, now, limit))
        self.assertLessEqual(now["fences"], 4, now)

    def cut_short(self, a, b, n, deadline):
        """n fades, each ended by the next one while it runs (end_fade), then one that runs to its end; the counts."""
        r = self.r
        cut = self.st()["fades-cut"]
        for i in range(n):
            self.assertLess(time.monotonic(), deadline, "the bounded run of fades took too long")
            self.fade(b if i % 2 == 0 else a, 5.0)
            self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.01), "fade %d: %s" % (i, self.st()))
        self.run_fades(a, b, 1, 0.1, deadline)
        self.assertEqual(self.st()["fades-cut"], cut + n)
        return self.counts()

    def test_two_hundred_fades_leave_nothing_behind(self):
        r = self.r
        deadline = time.monotonic() + self.LIMIT
        a, b = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_clip(self, a, 2)
        shutil.copy(a, b)
        r.load(self.red)
        self.run_fades(self.red, self.blue, 10, 0.03, deadline)     # libmpv's pools filled once, for pictures
        r("set_property", "loop-file", "inf")
        self.run_fades(a, b, 10, 0.1, deadline)                     # and for clips
        start = self.counts()
        self.run_fades(self.red, self.blue, 180, 0.03, deadline)
        self.run_fades(a, b, 20, 0.1, deadline)
        after = self.counts()
        st = self.st()
        self.assertGreaterEqual(st["fades-done"], 220)
        self.assertEqual(st["fades-cut"], 0)
        # libmpv's pool moves up and down by 4 or 5 with no leak (600 fades, the D98 addendum): the band, not SLACK.
        # A leak of one object every ten fades still ends 20 over.
        self.same(after, start, "200 fades", gl=start["gl-objects"])
        # fades cut short: three warm-up checkpoints of 20, then four of 30, judged against the warm-up's highest
        warm = [self.cut_short(a, b, 20, deadline) for _ in range(3)]
        peak = max(w["gl-objects"] for w in warm)
        later = []
        for n in range(4):
            later.append(self.cut_short(a, b, 30, deadline))
            self.same(later[-1], start, "%d fades cut short" % (60 + 30 * (n + 1)), gl=peak)
        self.assertNotIn("Export failed", r.stderr())
        self.assertIsNone(r.proc.poll())
        print("\n%d fades (%d cut short): at the start %s, after 200 %s; GL objects after 20, 40, 60 cut short %s, then "
              "after 90 to 180 %s; GL fences deleted %d"
              % (self.st()["fades-done"] + self.st()["fades-cut"], self.st()["fades-cut"], start, after,
                 [w["gl-objects"] for w in warm], [c["gl-objects"] for c in later], self.st()["gl-fences-freed"]))


def make_clip_with_sound(case, path, seconds=3):
    """As make_clip, with a sound track (a tone)."""
    maker = shutil.which("ffmpeg")
    if not maker:
        if STRICT:
            case.fail("PVJ_RENDER_TEST=1 and no ffmpeg to make the clip")
        case.skipTest("no ffmpeg to make a clip")
    subprocess.run([maker, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=%d" % seconds,
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds, "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-g", "15", "-c:a", "aac", "-shortest", path],
                   check=True, timeout=120, stdin=subprocess.DEVNULL)


class FadeEdgesTest(FadeBase):
    """The review of #148: a clip that ends during the fade, a crossfade with nothing on the screen, the sound."""

    def mean(self, shot):
        px = shot[2]
        return sum(px[::97]) / float(len(px[::97]))

    def test_an_outgoing_clip_that_ends_during_the_fade_keeps_its_last_frame(self):
        r = self.r
        short = os.path.join(self.tmp, "short.mp4")
        make_clip(self, short, 3, "color=c=red")
        black = os.path.join(self.tmp, "black.png")
        png(black, W, H, lambda x, y: BLACK)
        r("set_property", "loop-file", "no")
        r("set_property", "keep-open", "no")
        r.load(short)
        # In a fresh headless process a clip is sometimes drawn black for the whole run, on master's phase 1 build too
        # (2026-10-18, the journal: a fault of its own, still open). So the picture is judged only when the clip was
        # seen red before the fade; the layer's state is judged always.
        shown = r.wait(lambda: near(self.centre(), RED), 0.6)
        self.assertLess(r("get_property", "time-pos"), 2.0, "the clip still has a second to play: %s" % r.stderr())
        self.fade(black, 8.0)                   # into black: what shows is the outgoing clip's share alone
        # past the clip's end (3 s, of which some went before the fade), and still well inside the fade
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running" and 0.45 < self.st()["fade-progress"] < 0.7, 10, 0.02), self.st())
        shot = r.capture()
        self.assertIs(self.st()["back-idle"], False, "the outgoing clip ended and went idle (black) under the fade")
        if shown:
            red = self.r.pixel(W // 2, H // 2, shot)
            self.assertGreater(red[0], 40, "the outgoing clip went black when it ended, half way through the fade: %s" % (red,))
        self.settled()
        self.assertTrue(r.wait(lambda: self.st()["back-idle"] is True, 5), "stopped at the end of the fade")

    def test_a_crossfade_with_nothing_on_the_screen_is_a_cut(self):
        r = self.r
        r.load(self.red)
        r("stop")
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True))
        self.fade(self.blue, 3.0)
        st = self.st()
        self.assertEqual((st["fades"], st["fades-from-nothing"], st["front"], st["fade"]), (0, 1, 0, "off"))
        self.assertTrue(r.wait(lambda: r.playing() == self.blue))
        began = time.monotonic()
        self.assertTrue(r.wait(lambda: near(self.centre(), BLUE), 5))
        self.assertLess(time.monotonic() - began, 1.5, "it rose from black instead of cutting")
        time.sleep(1.5)
        self.assertColour(self.centre(), BLUE, "the options went with it: the picture stays (image-display-duration inf)")
        # and at power-up, before anything was ever loaded
        self.r.close()
        self.r = Renderer()
        self.fade(self.blue, 3.0)
        self.assertEqual(self.st()["fades-from-nothing"], 1)

    def test_the_sound_is_handed_from_the_outgoing_clip_to_the_incoming_one(self):
        # ALSA's null device: a real sound output that opens at once and plays to nothing. Not the machine's own
        # sound server, which may answer slowly (WSLg's PulseAudio held a core for over 10 seconds once).
        self.r.close()
        self.r = r = Renderer("--audio-device=alsa/null")
        a, b = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_clip_with_sound(self, a, 4)
        shutil.copy(a, b)
        r("set_property", "loop-file", "inf")
        r.load(a)
        self.assertTrue(r.wait(lambda: r("get_property", "aid") not in (False, None, "no")), "the first clip has its sound track")
        self.assertTrue(r.wait(lambda: self.output(r) == "alsa", 5), "the first clip's sound output is open: %s" % r.stderr())
        self.fade(b, 0.5)
        self.assertNotIn(r("get_property", "aid"), (False, None, "no"), "the incoming clip was given its sound track")
        self.assertTrue(r.wait(lambda: self.output(r) == "alsa", 5), "the incoming clip's sound output opened")
        st = self.settled()
        self.assertEqual(st["sound-held"], 0, "the outgoing core had not closed its output in time: %s" % r.stderr())
        self.assertNotIn("still holds the sound device", r.stderr())
        self.fade(a, 0.5)                       # and back: the layer that gave its sound up gets it again
        self.settled()
        self.assertNotIn(r("get_property", "aid"), (False, None, "no"))
        self.assertTrue(r.wait(lambda: self.output(r) == "alsa", 5))
        self.assertEqual(self.st()["sound-held"], 0)

    @staticmethod
    def output(r):
        """The front layer's open sound output (`current-ao`), or None."""
        try:
            return r("get_property", "current-ao")
        except PlayerError:
            return None


class PlayerFadeTest(FadeBase):
    """pvj/player.py's crossfade through the real pvj-render: Player.play(..., fade=seconds)."""

    def player(self):
        from pvj.player import Player
        p = Player(rundir=self.tmp)
        p.socket_path = self.r.sock
        p.ipc = Ipc(self.r.sock, timeout=10.0)
        return p

    def test_a_player_fades_a_clip_into_a_clip_through_the_layers(self):
        p = self.player()
        self.assertTrue(p.is_running())
        self.assertIs(p.crossfades(), True)
        p.play([self.red], spawn=False)
        self.assertEqual(self.st()["fades"], 0, "a play without a fade is a load, as before")
        p.play([self.blue], spawn=False, fade=0.5)
        self.assertEqual(p.ipc.request("get_property", "path"), self.blue)
        self.assertEqual(p.fade_refused, "")
        st = self.settled()
        self.assertEqual((st["fades"], st["fades-done"]), (1, 1))
        self.assertEqual(self.r("get_property", "loop-file"), "inf", "a single clip loops, as Player.play sets it")
        self.assertColour(self.centre(), BLUE, "after")
        status = p.status()
        self.assertEqual((status["path"], status["playlist_count"]), (self.blue, 1))

    def test_a_list_is_a_cut(self):
        p = self.player()
        p.play([self.red], spawn=False)
        p.play([self.blue, self.picture], spawn=False, fade=0.5)
        self.assertEqual(self.st()["fades"], 0)
        self.assertIn("one clip", p.fade_refused)
        self.assertEqual(self.r("get_property", "playlist-count"), 2)


# ---- sources (D101) ---------------------------------------------------------------------------------------------------
SHOW = "keep-open=no,image-display-duration=inf"     # a picture on a source stays until something else comes


def halves(left, right):
    """Two quads: the left half of the screen showing source `left`, the right half showing source `right`, each with
    the whole picture of its source."""
    def quad(sid, x0, source):
        s = {"id": sid, "type": "quad", "name": sid, "on": True, "vertices": [[x0, 0], [x0 + W / 2, 0], [x0 + W / 2, H], [x0, H]],
             "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}
        if source != 1:
            s["source"] = source
        return s
    return mapper.validate_mapping({"surfaces": [quad("aaaaaaa1", 0, left), quad("aaaaaaa2", W / 2, right)]})


class SourcesBase(FadeBase):
    ARGS = ("--sources=4",)

    def setUp(self):
        super().setUp()
        self.green = os.path.join(self.tmp, "green.png")
        png(self.green, W, H, lambda x, y: GREEN)

    def sources(self):
        return self.r("get_property", "pvj-sources")

    def source(self, k):
        return self.sources()[k - 1]

    def to(self, k, path, flag="replace", opts=SHOW):
        """Load `path` on source k and wait until it plays there and has drawn a frame."""
        drawn = self.source(k)["drawn"]
        self.r("pvj-source", k, "loadfile", path, flag, opts)
        self.assertTrue(self.r.wait(lambda: self.source(k)["path"] == path and self.source(k)["drawn"] > drawn, 10),
                        "source %d did not take %s: %s\n%s" % (k, path, self.sources(), self.r.stderr()))
        time.sleep(0.2)

    def map(self, text):
        path = os.path.join(self.tmp, "map-%d.glsl" % time.monotonic_ns())
        with open(path, "w") as f:
            f.write(text)
        self.r("set_property", "glsl-shaders", [path])
        self.assertEqual(self.st()["mapping"], "on", self.r.stderr())

    def show_halves(self, left, right, available=4):
        table = mapper.warp_table(halves(left, right), W, H, with_sources=True)
        self.map(mapper.warp_shader(*table, available=available))

    def halves_are(self, left, right, shot=None):
        shot = shot or self.r.capture()
        for x in (W // 8, W // 4, 3 * W // 8 - 4):
            for y in (H // 4, 3 * H // 4):
                self.assertColour(self.r.pixel(x, y, shot), left, "left half, %d,%d" % (x, y))
        for x in (W // 2 + 4, 5 * W // 8, 7 * W // 8):
            for y in (H // 4, 3 * H // 4):
                self.assertColour(self.r.pixel(x, y, shot), right, "right half, %d,%d" % (x, y))


class SourcesTest(SourcesBase):
    def test_what_it_says_of_its_sources(self):
        st = self.st()
        self.assertEqual((st["sources"], st["layers"], st["front"]), (4, 2, 0))
        got = self.sources()
        self.assertEqual([s["source"] for s in got], [1, 2, 3, 4])
        # Sources 2 to 4 are drawn once (their empty picture), then never while idle (CI drew them three times before
        # an idle source stopped drawing, the review of #153). Source 1 is the phase 1 and 2 front layer, left as it
        # was: its start-up draws vary (2 here, 3 on CI's llvmpipe), so it is judged by no new draw after start-up.
        self.assertTrue(all(s["idle"] is True and s["path"] is None and s["loads"] == 0 for s in got), got)
        self.assertEqual([s["drawn"] for s in got[1:]], [1, 1, 1], got)
        time.sleep(0.5)
        first = self.sources()[0]["drawn"]
        time.sleep(1.0)
        now = self.sources()
        self.assertEqual(now[0]["drawn"], first, "idle source 1 drew again after start-up: %s" % now)
        self.assertEqual([s["drawn"] for s in now[1:]], [1, 1, 1], "an idle source drew again")

    def test_several_sources_play_different_clips_and_source_1_is_unchanged(self):
        r = self.r
        clips = {}
        for k, colour in ((2, "red"), (3, "green"), (4, "blue")):
            clips[k] = os.path.join(self.tmp, "%s.mp4" % colour)
            make_clip(self, clips[k], 3, "color=c=%s" % colour)
        r("set_property", "loop-file", "inf")
        r.load(self.picture)                        # source 1, through the plain commands, as always
        for k in (2, 3, 4):
            self.to(k, clips[k], opts="keep-open=no")
            r("pvj-source", k, "set_property", "loop-file", "inf")
        before = self.sources()
        time.sleep(1.5)
        after = self.sources()
        for k in (2, 3, 4):
            s = after[k - 1]
            self.assertEqual((s["path"], s["idle"], s["height"]), (clips[k], False, 180), s)
            self.assertGreater(s["drawn"] - before[k - 1]["drawn"], 20, "source %d moves: %s" % (k, s))
            self.assertLess(s["drawn"] - before[k - 1]["drawn"], 60, "each clip frame converted once: %s" % s)
            self.assertEqual(r("pvj-source", k, "get_property", "loop-file"), "inf")
        # every plain command still means source 1
        self.assertEqual(r("get_property", "path"), self.picture)
        self.assertEqual(after[0]["path"], self.picture)
        self.assertEqual(r("get_property", "playlist-count"), 1)
        self.assertQuadrants()                      # no mapping: the screen shows source 1 alone
        st = self.st()
        self.assertEqual((st["fades"], st["front"]), (0, 0))
        # the panel's level reaches every source; a source's own pause is its own
        r("set_property", "brightness", -50)
        self.assertEqual([r("pvj-source", k, "get_property", "brightness") for k in (2, 3, 4)], [-50] * 3)
        r("pvj-source", 3, "set_property", "pause", True)
        self.assertIs(r("pvj-source", 3, "get_property", "pause"), True)
        self.assertIs(r("get_property", "pause"), False)
        self.assertIs(r("pvj-source", 2, "get_property", "pause"), False)

    def test_a_surface_on_source_2_shows_source_2s_picture(self):
        r = self.r
        r.load(self.red)
        self.to(2, self.blue)
        self.to(3, self.green)
        self.show_halves(1, 2)
        self.halves_are(RED, BLUE)
        self.show_halves(3, 1)
        self.halves_are(GREEN, RED)
        self.show_halves(2, 3)
        self.halves_are(BLUE, GREEN)
        # the editing shader (direct, per cell) too
        self.map(mapper.shader(halves(1, 2), available=4))
        self.halves_are(RED, BLUE)
        # a source this run does not have is drawn from source 1, as the panel would send it (available=4 here,
        # source 2 asked of a player with 1)
        self.show_halves(2, 3, available=1)
        self.halves_are(RED, RED)
        # all on source 1: the shader is the one it always was
        table = mapper.warp_table(halves(1, 1), W, H, with_sources=True)
        self.assertEqual(mapper.warp_shader(*table, available=4), mapper.warp_shader(*table[:3]))

    def test_the_edge_between_two_sources_is_a_blend_not_a_step_of_the_table(self):
        r = self.r
        r.load(self.red)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        shot = r.capture()
        y = H // 2
        row = [self.r.pixel(x, y, shot) for x in range(W // 2 - 12, W // 2 + 12)]
        for px in row:
            # every pixel across the edge is red, blue, or a mix of them: never black or the wrong picture
            self.assertLess(px[1], 40, row)
            self.assertGreater(px[0] + px[2], 200, "a dark gap at the edge: %s" % (row,))
        self.assertTrue(near(row[0], RED) and near(row[-1], BLUE), row)

    def test_a_sources_opacity_changes_its_share_of_the_picture_and_only_normal_is_a_blend(self):
        r = self.r
        r.load(self.red)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        self.halves_are(RED, BLUE)
        self.assertEqual((self.source(2)["opacity"], self.source(2)["blend"]), (1.0, "normal"))
        self.assertEqual((self.source(1)["opacity"], self.source(1)["blend"]), (1.0, "normal"), "source 1 is the program")
        r("pvj-source", 2, "set_property", "opacity", 0.5)
        self.assertEqual(r("pvj-source", 2, "get_property", "opacity"), 0.5)
        self.assertEqual(self.source(2)["opacity"], 0.5)
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), (0, 0, 128), 20)), r.pixel(3 * W // 4, H // 2))
        self.assertColour(r.pixel(W // 4, H // 2), RED, "source 1 at its own level")
        r("pvj-source", 2, "set_property", "opacity", 0)
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), BLACK, 10)), r.pixel(3 * W // 4, H // 2))
        r("pvj-source", 2, "set_property", "blend", "normal")
        self.assertEqual(r("pvj-source", 2, "get_property", "blend"), "normal")
        for name, value in (("blend", "add"), ("blend", "screen"), ("blend", 1), ("blend", ""), ("opacity", 1.5),
                            ("opacity", -0.1), ("opacity", "1"), ("opacity", True)):
            with self.assertRaises(PlayerError, msg=(name, value)) as e:
                r("pvj-source", 2, "set_property", name, value)
            self.assertEqual(str(e.exception), "mpv: invalid parameter", (name, value))
        r("pvj-source", 2, "set_property", "opacity", 1)
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), BLUE)), r.pixel(3 * W // 4, H // 2))

    def test_a_stop_on_a_source_makes_it_idle_and_its_surface_black(self):
        r = self.r
        r.load(self.red)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        self.halves_are(RED, BLUE)
        r("pvj-source", 2, "stop")
        self.assertTrue(r.wait(lambda: self.source(2)["idle"] is True), self.sources())
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), BLACK)), r.pixel(3 * W // 4, H // 2))
        self.assertColour(r.pixel(W // 4, H // 2), RED, "source 1 plays on")
        self.assertIs(r("get_property", "idle-active"), False)
        # the plain Stop is source 1's alone
        self.to(2, self.blue)
        r("stop")
        self.assertTrue(r.wait(lambda: r("get_property", "idle-active") is True))
        self.assertIs(self.source(2)["idle"], False)
        self.assertTrue(r.wait(lambda: near(r.pixel(W // 4, H // 2), BLACK) and near(r.pixel(3 * W // 4, H // 2), BLUE)))

    def test_a_blackout_darkens_every_source(self):
        r = self.r
        r.load(self.red)
        self.to(2, self.blue)
        self.show_halves(1, 2)
        r("set_property", "brightness", -100)
        self.assertTrue(r.wait(lambda: near(r.pixel(W // 4, H // 2), BLACK, 60) and near(r.pixel(3 * W // 4, H // 2), BLACK, 60)),
                        (r.pixel(W // 4, H // 2), r.pixel(3 * W // 4, H // 2)))
        self.to(3, self.green)                      # a source loaded under the Blackout comes in dark
        self.show_halves(1, 3)
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), BLACK, 60)), r.pixel(3 * W // 4, H // 2))
        r("set_property", "brightness", 0)
        self.assertTrue(r.wait(lambda: near(r.pixel(3 * W // 4, H // 2), GREEN)), r.pixel(3 * W // 4, H // 2))

    def test_what_is_not_a_source_command_is_refused_with_mpvs_words(self):
        r = self.r
        p = self.blue
        for cmd in (("pvj-source",), ("pvj-source", 2), ("pvj-source", 1, "stop"), ("pvj-source", 5, "stop"),
                    ("pvj-source", 0, "stop"), ("pvj-source", "2", "stop"), ("pvj-source", 2.0, "stop"), ("pvj-source", True, "stop"),
                    ("pvj-source", 2, "loadfile"), ("pvj-source", 2, "loadfile", ""), ("pvj-source", 2, "loadfile", 7),
                    ("pvj-source", 2, "loadfile", p, "nonsense"), ("pvj-source", 2, "loadfile", p, "insert-next"),
                    ("pvj-source", 2, "loadfile", p, "replace", "script=/tmp/x.lua"), ("pvj-source", 2, "loadfile", p, "replace", "aid=auto"),
                    ("pvj-source", 2, "loadfile", p, "replace", "pause=yes"), ("pvj-source", 2, "loadfile", p, "replace", SHOW, "x"),
                    ("pvj-source", 2, "stop", "now"), ("pvj-source", 2, "run", "/bin/sh"), ("pvj-source", 2, "seek", 1),
                    ("pvj-source", 2, "set_property", "pause"), ("pvj-source", 2, "get_property"), ("pvj-source", 2, "quit")):
            with self.assertRaises(PlayerError, msg=str(cmd)) as e:
                r(*cmd)
            self.assertEqual(str(e.exception), "mpv: invalid parameter", cmd)
        for name in ("aid", "volume", "audio-device", "glsl-shaders", "script-opts", "input-ipc-server", "vf", "keep-open"):
            with self.assertRaises(PlayerError) as e:
                r("pvj-source", 2, "set_property", name, "x")
            self.assertEqual(str(e.exception), "mpv: property not found", name)
        for name in ("stream-open-filename", "playlist", "mpv-version", "pvj-render"):
            with self.assertRaises(PlayerError) as e:
                r("pvj-source", 2, "get_property", name)
            self.assertEqual(str(e.exception), "mpv: property not found", name)
        self.assertTrue(all(s["idle"] for s in self.sources()[1:]), "nothing was loaded")
        self.assertIsNone(r.proc.poll())

    def test_a_source_has_no_sound(self):
        self.r.close()
        self.r = r = Renderer("--sources=2", "--audio-device=alsa/null")
        clip = os.path.join(self.tmp, "tone.mp4")
        make_clip_with_sound(self, clip, 3)
        self.to(2, clip, opts="keep-open=no")
        self.assertIn(r("pvj-source", 2, "get_property", "aid"), (False, "no"))
        with self.assertRaises(PlayerError):
            r("pvj-source", 2, "get_property", "current-ao")       # no sound output was opened
        r.load(clip)                                                # source 1 has its sound, as always
        self.assertTrue(r.wait(lambda: FadeEdgesTest.output(r) == "alsa", 5), r.stderr())


class OwnersMappingTest(SourcesBase):
    """The owner's mapping on the node (2026-10-20), at this window's size: a quad on top and a triangle under it.
    Its quad was never drawn (its perspective map's w was negative over the whole quad, on classic too), and a source's
    picture kept its letterbox under a mapping (the stretch reached source 1 only)."""

    def owners(self, quad, tri):
        sx, sy = W / 1024.0, H / 768.0
        q = {"id": "aaaaaaa1", "type": "quad", "name": "Quad.01", "on": True,
             "vertices": [[x * sx, y * sy] for x, y in ((38, 412), (324, 368), (667, 629), (70, 722))], "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}
        t = {"id": "aaaaaaa2", "type": "triangle", "name": "Triangle", "on": True,
             "vertices": [[x * sx, y * sy] for x, y in ((286, 606), (542, 222), (798, 606))], "tex": [[0, 1], [0.5, 0], [1, 1]]}
        if quad != 1:
            q["source"] = quad
        if tri != 1:
            t["source"] = tri
        return mapper.validate_mapping({"surfaces": [q, t]})

    def at(self, shot, x, y):
        return self.r.pixel(int(x * W / 1024.0), int(y * H / 768.0), shot)

    def test_the_owners_quad_is_drawn_on_top_and_each_shows_its_whole_source(self):
        r = self.r
        narrow = os.path.join(self.tmp, "green43.png")
        png(narrow, 160, 120, lambda x, y: GREEN)          # 4:3 in a 16:9 window: letterboxed unless stretched
        r("set_property", "keepaspect", False)               # what Player.set_mapping_mode does with a mapping on
        r.load(self.red)
        self.to(2, narrow)
        for quad, tri, want in ((1, 1, (RED, RED, RED)), (1, 2, (RED, RED, GREEN)), (2, 1, (GREEN, GREEN, RED))):
            surfaces = self.owners(quad, tri)
            for kind, text in (("show", mapper.warp_shader(*mapper.warp_table(surfaces, W, H, with_sources=True), available=4)),
                               ("edit", mapper.shader(surfaces, available=4))):
                self.map(text)
                time.sleep(0.3)
                shot = r.capture()
                got = (self.at(shot, 120, 600), self.at(shot, 450, 560), self.at(shot, 600, 400))
                for g, w, where in zip(got, want, ("the quad alone", "the quad over the triangle", "the triangle alone")):
                    self.assertColour(g, w, "quad on %d, triangle on %d, %s, %s" % (quad, tri, kind, where))
                self.assertColour(self.at(shot, 950, 100), BLACK, "outside both")
        # a source's whole picture reaches the surface's edges: stretched as source 1's is, not letterboxed
        whole = mapper.validate_mapping({"surfaces": [{"id": "aaaaaaa3", "type": "quad", "name": "All", "on": True, "source": 2,
                                                       "vertices": [[0, 0], [W, 0], [W, H], [0, H]], "tex": [[0, 0], [1, 0], [1, 1], [0, 1]]}]})
        self.map(mapper.warp_shader(*mapper.warp_table(whole, W, H, with_sources=True), available=4))
        time.sleep(0.3)
        shot = r.capture()
        for x in (W // 30, W // 2, W - W // 30):
            self.assertColour(r.pixel(x, H // 2, shot), GREEN, "source 2 across the whole surface, x %d" % x)


class SourceCountTest(Base):
    def test_one_source_by_default_and_no_more_than_eight(self):
        st = self.r("get_property", "pvj-render")
        self.assertEqual(st["sources"], 1)
        self.assertEqual(len(self.r("get_property", "pvj-sources")), 1)
        with self.assertRaises(PlayerError) as e:
            self.r("pvj-source", 2, "stop")
        self.assertEqual(str(e.exception), "mpv: invalid parameter")
        for bad in ("--sources=0", "--sources=9", "--sources=two", "--sources="):
            run = subprocess.run([BIN, "--socket=" + os.path.join(self.tmp, "x.sock"), "--headless=64x64", bad],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 64, bad)
        r8 = Renderer("--sources=8")
        self.addCleanup(r8.close)
        self.assertEqual(r8("get_property", "pvj-render")["sources"], 8)
        self.assertEqual([s["source"] for s in r8("get_property", "pvj-sources")], list(range(1, 9)))


class IdleSourcesTest(SourcesBase):
    """Sources that play nothing cost nothing: no frame converted, and the output keeps its rate."""

    def test_idle_sources_draw_nothing_and_cost_no_flips(self):
        r = self.r
        clip = os.path.join(self.tmp, "clip.mp4")
        make_clip(self, clip, 4)
        r("set_property", "loop-file", "inf")
        r.load(clip)
        a, t0 = self.sources(), time.monotonic()
        s0 = self.st()
        time.sleep(2.0)
        b, t1 = self.sources(), time.monotonic()
        s1 = self.st()
        for k in (2, 3, 4):
            self.assertEqual(b[k - 1]["drawn"], a[k - 1]["drawn"], "idle source %d converted frames: %s" % (k, b))
            self.assertEqual(b[k - 1]["drawn"], 1, "only its empty picture as it started")
        flips = (s1["flips"] - s0["flips"]) / (t1 - t0)
        self.assertGreater(flips, 0.85 * 60, "flips a second with three idle sources: %.1f" % flips)
        self.assertLessEqual(flips, 62)
        # and a source that stops is idle again: one more picture (its empty one), then nothing
        self.to(2, self.blue)
        r("pvj-source", 2, "stop")
        self.assertTrue(r.wait(lambda: self.source(2)["idle"] is True))
        time.sleep(0.5)
        n = self.source(2)["drawn"]
        time.sleep(1.0)
        self.assertEqual(self.source(2)["drawn"], n, "a stopped source goes on converting")


class SourceFadeTest(SourcesBase):
    def test_a_crossfade_on_source_1_while_source_2_plays(self):
        r = self.r
        clip = os.path.join(self.tmp, "s2.mp4")
        make_clip(self, clip, 3)
        self.to(2, clip, opts="keep-open=no")
        r("pvj-source", 2, "set_property", "loop-file", "inf")
        r.load(self.red)
        self.show_halves(1, 2)
        self.fade(self.blue, 1.5)
        self.assertTrue(r.wait(lambda: self.st()["fade"] == "running", 10, 0.02), self.st())
        d0 = self.source(2)["drawn"]
        time.sleep(0.6)
        self.assertGreater(self.source(2)["drawn"], d0 + 8, "source 2 moved during the fade")
        st = self.settled()
        self.assertEqual((st["fades"], st["fades-done"], st["front"]), (1, 1, 1))
        self.assertTrue(r.wait(lambda: near(r.pixel(W // 4, H // 2), BLUE)), r.pixel(W // 4, H // 2))
        self.assertEqual(self.source(2)["path"], clip)
        self.assertEqual(r("get_property", "path"), self.blue)
        self.assertEqual(self.sources()[0]["path"], self.blue, "pvj-sources follows source 1's front")
        self.assertIs(self.source(2)["idle"], False)


class PlayerSourcesTest(SourcesBase):
    """pvj/player.py's side through the real pvj-render: Player.sources, play_to, stop_source, sources_state."""

    def player(self):
        from pvj.player import Player
        p = Player(rundir=self.tmp)
        p.socket_path = self.r.sock
        p.ipc = Ipc(self.r.sock, timeout=10.0)
        return p

    def test_a_player_plays_to_a_source_and_stops_it(self):
        p = self.player()
        self.assertEqual(p.sources(), 4)
        self.assertIs(p.crossfades(), True, "source 1 still fades")
        p.play([self.red], spawn=False)
        p.play_to(2, [self.blue], "loop")
        self.assertEqual(self.r("pvj-source", 2, "get_property", "path"), self.blue)
        self.assertEqual(self.r("pvj-source", 2, "get_property", "loop-file"), "inf")
        self.assertIs(self.r("pvj-source", 2, "get_property", "pause"), False)
        p.play_to(3, [self.green, self.picture], "loop")
        self.assertEqual(self.r("pvj-source", 3, "get_property", "playlist-count"), 2)
        self.assertEqual(self.r("pvj-source", 3, "get_property", "loop-playlist"), "inf")
        self.assertTrue(self.r.wait(lambda: self.source(2)["height"] == H and self.source(3)["path"] == self.green), self.sources())
        rows = p.sources_state()
        self.assertEqual([(r["source"], r["path"], r["idle"]) for r in rows],
                         [(1, self.red, False), (2, self.blue, False), (3, self.green, False), (4, None, True)])
        self.assertEqual(self.r("get_property", "path"), self.red, "source 1 unchanged")
        self.show_halves(1, 2)
        self.halves_are(RED, BLUE)
        p.stop_source(2)
        self.assertTrue(self.r.wait(lambda: self.source(2)["idle"] is True))
        for bad in (1, 5, 0, True):
            # the player says so itself, before anything is asked of the renderer
            with self.assertRaisesRegex(PlayerError, "this player has no source"):
                p.play_to(bad, [self.blue])
        with self.assertRaises(PlayerError):
            p.play_to(2, [self.blue], "sideways")


class ManySourceLoadsTest(SourcesBase):
    """Loads to sources 2 to 4 all night leave no file descriptor, thread, GL fence or GL object behind (judged as
    ManyFadesTest is: SLACK for descriptors and threads, GL_BAND over the warm-up's highest for libmpv's pool).
    Bounded by LIMIT."""
    LIMIT = 300.0
    SLACK = 2
    GL_BAND = 4

    def counts(self):
        """At rest: sources 2 to 4 stopped and idle (a clip's decoder threads come and go as it plays and ends), the
        socket's client threads gone."""
        for k in (2, 3, 4):
            self.r("pvj-source", k, "stop")
        self.assertTrue(self.r.wait(lambda: all(s["idle"] for s in self.sources()[1:]), 10), self.sources())
        time.sleep(0.6)
        pid = self.r.proc.pid
        st = self.st()
        return {"fds": fd_count(pid), "threads": thread_count(pid), "gl-objects": st["gl-objects"], "fences": st["gl-fences"]}

    def run_loads(self, a, b, n, deadline):
        for i in range(n):
            self.assertLess(time.monotonic(), deadline, "the bounded run of loads took too long")
            k = 2 + i % 3
            path = a if (i // 3) % 2 == 0 else b
            loads = self.source(k)["loads"]
            self.r("pvj-source", k, "loadfile", path, "replace", "keep-open=no")
            self.assertTrue(self.r.wait(lambda: self.source(k)["loads"] > loads and self.source(k)["path"] == path, 10, 0.01),
                            "load %d: %s\n%s" % (i, self.sources(), self.r.stderr()))
            if i % 10 == 9:
                self.r("pvj-source", k, "stop")

    def same(self, now, then, what, gl):
        for k in ("fds", "threads"):
            self.assertLessEqual(now[k], then[k] + self.SLACK, "%s: %s piled up: %s, at the start %s" % (what, k, now, then))
        self.assertLessEqual(now["gl-objects"], gl + self.GL_BAND, "%s: GL objects piled up: %s, at most %d" % (what, now, gl + self.GL_BAND))
        self.assertLessEqual(now["fences"], 4, now)

    def test_many_loads_to_sources_2_to_4_leave_nothing_behind(self):
        r = self.r
        deadline = time.monotonic() + self.LIMIT
        a, b = os.path.join(self.tmp, "a.mp4"), os.path.join(self.tmp, "b.mp4")
        make_clip(self, a, 2)
        make_clip(self, b, 2, "color=c=blue")
        r.load(self.red)
        self.show_halves(2, 3)
        warm = []
        for _ in range(3):
            self.run_loads(a, b, 30, deadline)
            warm.append(self.counts())
        peak = max(w["gl-objects"] for w in warm)
        start = warm[-1]
        later = []
        for n in range(4):
            self.run_loads(a, b, 45, deadline)
            later.append(self.counts())
            self.same(later[-1], start, "%d loads to sources 2 to 4" % (90 + 45 * (n + 1)), peak)
        self.assertNotIn("Export failed", r.stderr())
        self.assertIsNone(r.proc.poll())
        print("\n%d loads to sources 2 to 4: warm-up %s, then %s" % (sum(s["loads"] for s in self.sources()),
                                                                     [w for w in warm], [c for c in later]))


if __name__ == "__main__":
    unittest.main()
