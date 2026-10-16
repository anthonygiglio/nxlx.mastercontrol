# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-render (render/, D95) run for real, headless: EGL without a surface on Mesa's software GPU, no screen.

The program is built first (`make -C render`). Without it these tests are skipped, except where PVJ_RENDER_TEST=1
(the CI job pvj-render), where a missing program, a missing clip maker or a missing font is a failure: there a skip
would judge nothing. Every question goes through pvj.player.Ipc, the panel's own client, so what is tested is what the
panel will meet. The pictures are read back with pvj-render's own `pvj-capture` (the final picture as it would go to
the screen) and judged by their pixels.
"""
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


class Renderer:
    """One pvj-render process in its own folder."""

    def __init__(self, *args):
        self.dir = tempfile.mkdtemp(prefix="pvj-render-")
        self.sock = os.path.join(self.dir, "mpv.sock")
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1")
        self.log = open(os.path.join(self.dir, "stderr.txt"), "w+")
        self.proc = subprocess.Popen([BIN, "--socket=" + self.sock, "--headless=%dx%d" % (W, H)] + list(args),
                                     stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, env=env)
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

    def load(self, path):
        self("loadfile", path, "replace")
        ok = self.wait(lambda: self("get_property", "path") == path)
        assert ok, "the clip was not loaded: " + self.stderr()
        # a picture is on the screen once the video layer drew it (its own counter) and one more frame went out
        drawn = self("get_property", "pvj-render")["drawn"]
        self.wait(lambda: self("get_property", "pvj-render")["drawn"] > drawn, 5)
        time.sleep(0.3)

    def close(self):
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


class ClipTest(Base):
    """A real clip: pause, seek, loop, volume, stop."""

    def setUp(self):
        super().setUp()
        self.clip = os.path.join(self.tmp, "clip.mp4")
        maker = shutil.which("ffmpeg")
        if not maker:
            if STRICT:
                self.fail("PVJ_RENDER_TEST=1 and no ffmpeg to make the clip")
            self.skipTest("no ffmpeg to make a clip")
        subprocess.run([maker, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-g", "15", self.clip], check=True, timeout=120, stdin=subprocess.DEVNULL)

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


if __name__ == "__main__":
    unittest.main()
