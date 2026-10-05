# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Effects (ISF filters over the playing picture) on a real mpv that really draws: the rig of tests/test_shaders_gpu.py
(mpv's GPU output in a window on a virtual display, Mesa's software GPU), judged on screenshots.

What is settled here by experiment and then held: where a filter can sit in mpv (the NATIVE stage, with the
conversion to RGB and back done by the effect: only there do Blackout and opacity still darken a filter that adds
light), that the picture is right for YUV and RGB pictures of several kinds, sizes and shapes, letterboxed and
stretched, that the projection mapping shows the filtered picture, and that every bundled filter draws.

Run:  PVJ_GPU_TEST=1 xvfb-run -a python3 -m tests.test_effects_gpu
CI runs it three ways: OpenGL ES, desktop OpenGL, and (with Mesa told to be a 3.1 driver and PVJ_GPU_ONLY=desktop)
desktop OpenGL 3.1 with GLSL 1.40, the context mpv makes on a Raspberry Pi 4. Run directly, a skip is a failure.
A software GPU: the pass times printed here are ratios between shaders, nothing about a real board.
"""
import os
import random
import shutil
import struct
import sys
import time
import unittest
import zlib

from pvj import effects as E, shaders as S, vibes as V
from pvj.api import ApiError
from pvj.player import Player, PlayerError
from tests.test_server import ServerBase
from tests.test_shaders_gpu import GPU, GpuCase, H, W, png_rows

# A still picture that the player makes itself: every frame is the same, so two screenshots can be compared whenever
# they were taken. Red waves from left to right, green steps from top to bottom, blue waves on the diagonal: no two
# quarters are alike, so a mirror, a flip and a turn each change it. No colour is near the ends of its range: a
# video keeps colour at half the picture's detail, and a picture of full colours comes back from that with values
# beyond black and white, which the player cuts off and a test would have to guess.
PIC = "geq=r=127+80*sin(14*X/W):g=50+floor(6*Y/H)*26:b=127+80*cos(19*(X+Y)/W)"


def clip(size="320x180", fmt="yuv420p", rate=25, tags=""):
    """The picture as an endless clip that costs the player next to nothing: it is worked out once (one frame goes
    through geq) and that frame is given again and again by the loop filter. The first version worked the picture out
    for every frame; on the CI machine the player then fell behind, dropped its frames, and a screenshot showed
    black for seconds at a time."""
    return "av://lavfi:color=c=black:size=%s:rate=%d,format=gbrp,trim=end_frame=1,%s,format=%s,loop=loop=-1:size=1,setpts=N/(%d*TB)%s" % (
        size, rate, PIC, fmt, rate, tags)


CLIP = clip(tags=",setparams=colorspace=bt709:range=tv")
KINDS = [("bt.709", CLIP), ("bt.601", clip(tags=",setparams=colorspace=smpte170m:range=tv")), ("full range", clip(fmt="yuvj420p")),
         ("tagged full", clip(tags=",setparams=range=pc")), ("10 bit", clip(fmt="yuv420p10le")), ("nv12", clip(fmt="nv12")), ("rgb", clip(fmt="rgb24")),
         ("planar rgb", clip(fmt="gbrp"))]
SHAPES = [("wide", CLIP), ("tall", clip("90x160")), ("square", clip("200x200")), ("small", clip("160x90")), ("large", clip("640x360"))]

SAME = "/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\nvoid main() { gl_FragColor = IMG_THIS_PIXEL(inputImage); }\n"
INVERT = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
          "void main() { gl_FragColor = vec4(vec3(1.0) - IMG_THIS_PIXEL(inputImage).rgb, 1.0); }\n")
# every way of reading the picture, each of which must give the same pixel: by the pixel itself, by a place between 0
# and 1, by a place in pixels; the last quarter is drawn only if the picture's size is the drawing size
READS = """/*{"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}, {"NAME": "way", "TYPE": "long", "VALUES": [0, 1, 2, 3, 4], "DEFAULT": 0}]}*/
void main() {
    vec4 c = IMG_THIS_NORM_PIXEL(inputImage);
    if (way == 1) { c = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord); }
    if (way == 2) { c = IMG_PIXEL(inputImage, gl_FragCoord.xy); }
    if (way == 3) { c = IMG_PIXEL(inputImage, isf_FragNormCoord * IMG_SIZE(inputImage)); }
    if (way == 4) { c = (distance(IMG_SIZE(inputImage), RENDERSIZE) < 0.5) ? IMG_NORM_PIXEL(inputImage, vec2(1.0 - isf_FragNormCoord.x, isf_FragNormCoord.y)) : vec4(0.0); }
    gl_FragColor = c;
}
"""
# the top half of the picture drawn in the bottom half and the other way round: ISF counts from the bottom left
SWAP_HALVES = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
               "void main() { vec2 p = isf_FragNormCoord; gl_FragColor = IMG_NORM_PIXEL(inputImage, vec2(p.x, 1.0 - p.y)); }\n")
CLOCK = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n"
         "void main() { gl_FragColor = vec4(fract(TIME / 8.0), fract(TIME / 64.0), IMG_THIS_PIXEL(inputImage).b, 1.0); }\n")
BROKEN = ("/*{\"INPUTS\": [{\"NAME\": \"inputImage\", \"TYPE\": \"image\"}]}*/\n\nvoid main() {\n    gl_FragColor = IMG_THIS_PIXEL(inputImage) * nonsense;\n}\n")
ALL_TYPES = """/*{"INPUTS": [{"NAME": "inputImage", "TYPE": "image"},
 {"NAME": "level", "TYPE": "float", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.25},
 {"NAME": "lit", "TYPE": "bool", "DEFAULT": false},
 {"NAME": "mode", "TYPE": "long", "VALUES": [0, 1, 2], "DEFAULT": 0},
 {"NAME": "tint", "TYPE": "color", "DEFAULT": [0.0, 0.0, 0.0, 1.0]},
 {"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.25, 0.25], "MIN": [0.0, 0.0], "MAX": [1.0, 1.0]},
 {"NAME": "bang", "TYPE": "event"}]}*/
void main() {
    vec2 uv = isf_FragNormCoord;
    vec3 c = vec3(level, lit ? 1.0 : 0.0, float(mode) * 0.5);
    if (uv.x > 0.5) { c = tint.rgb; }
    if (distance(uv, spot) < 0.08) { c = vec3(1.0); }
    if (bang) { c = vec3(1.0, 0.0, 1.0); }
    gl_FragColor = vec4(c, IMG_THIS_PIXEL(inputImage).a);
}
"""
MAIN_INVERT = "//!HOOK %s\n//!BIND HOOKED\n//!DESC stage probe %d\nvec4 hook() { return vec4(vec3(1.0) - HOOKED_tex(HOOKED_pos).rgb, 1.0); }\n"
# Filters of the pack that change nothing with their own defaults (a level at 1, a zoom of 1, a shift of 0): each is
# judged with these values, written here on purpose, and its defaults are checked to give back the picture.
NEUTRAL = {
    "isf-rgb-eq.fs": {"red": 1.6, "green": 0.6, "blue": 1.3},
    "isf-gamma-correction.fs": {"gamma": 0.85},
    "isf-lgg.fs": {"saturation": 0.1, "lift": [0.75, 0.5, 0.35, 0.5]},
    "isf-white-point-adjust.fs": {"newWhite": [1.0, 0.55, 0.3, 1.0]},
    "isf-zoom.fs": {"level": 2.0, "center": [0.5, 0.5]},
    "isf-triple-rotate.fs": {"angle1": 0.25, "angle2": 0.5, "angle3": 0.125, "angle4": 0.375},
    "isf-double-vision.fs": {"hShift": 0.04, "vShift": 0.03},
}


def quick_rows(path):
    """The rows of a PNG the player wrote with no filtering of its rows (screenshot-png-filter 0): then a row is its
    own bytes and reading it takes a few milliseconds, where the general reader in test_shaders_gpu takes a third of
    a second. Any other PNG goes to that reader."""
    with open(path, "rb") as f:
        data = f.read()
    pos, idat, w, h, bpp = 8, [], 0, 0, 3
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[pos + 8:pos + 18])
            if depth != 8 or ctype not in (2, 6):
                return png_rows(path)
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(data[pos + 8:pos + 8 + n])
        pos += 12 + n
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp + 1
    if any(raw[y * stride] for y in range(h)):
        return png_rows(path)
    rows = []
    for y in range(h):
        line = raw[y * stride + 1:(y + 1) * stride]
        rows.append(list(zip(line[0::bpp], line[1::bpp], line[2::bpp])))
    return w, h, rows


# Values that would switch a filter off altogether when every switch is turned the other way: these stay as they are
# for the "varied" draw (all four switches of Edge Blowout off is the picture untouched, by design).
OTHER = {
    "isf-edge-blowout.fs": {"doHorizontal": True, "doVertical": True, "insideBleed": True, "outsideBleed": False},
    # the project's own three that bend the picture: a strength of nearly nothing would be no change to judge
    "fx-twirl.fs": {"turns": 1.2, "size": 0.9},
    "fx-ripple.fs": {"depth": 0.035, "reach": 1.6},
    "fx-ring.fs": {"bend": 0.9, "width": 0.3, "travel": 0.3, "rate": 0.0},
}


# "The picture again": what a filter that changes nothing, and any filter at amount 0, must give. Not to the last
# bit: with an effect the picture takes one more pass through the GPU, and for a video with colour at half the detail
# (4:2:0, 8 bit) the rounding came out up to 3 of 255 away, 1.1 on average, in every way of drawing; for 10 bit and for
# two-plane video half of that, for RGB nothing. A wrong matrix, a wrong range or a shifted picture is far outside it.
ALIKE_MAX, ALIKE_MEAN = 4, 1.5


def alike(d):
    return d[0] <= ALIKE_MAX and d[1] <= ALIKE_MEAN


def grid(rows, step=5):
    """A grid of points of a screenshot. The step is odd, so the points lie on even and on odd lines alike (a filter
    that treats every other line differently is not missed)."""
    return [rows[y][x] for y in range(3, H - 3, step) for x in range(3, W - 3, step)]


def differ(a, b):
    """(largest, mean, share of points that differ clearly) between two screenshots, on a grid of points."""
    pairs = list(zip(grid(a), grid(b)))
    d = [max(abs(i - j) for i, j in zip(p, q)) for p, q in pairs]
    return max(d), sum(d) / float(len(d)), sum(1 for x in d if x > 12) / float(len(d))


def mirrored(rows, left=0, right=0):
    """Left and right exchanged within the picture: `left` and `right` are the bars beside it. They are not always
    alike (a 90 x 160 clip on a 320 x 180 screen is drawn 101 wide with 109 and 110 beside it), and a filter mirrors
    the picture, not the screen."""
    end = len(rows[0]) - right
    return [list(row[:left]) + list(reversed(row[left:end])) + list(row[end:]) for row in rows]


def upside_down(rows, top=0, bottom=0):
    end = len(rows) - bottom
    return list(rows[:top]) + list(reversed(rows[top:end])) + list(rows[end:])


class FxCase(GpuCase):
    maxDiff = None

    def setUp(self):
        """The generators' rig with two differences, both found the hard way (a diagnosis job printed what the player
        said at every look). It starts with the test picture itself, not with the generators' small RGB clip: on
        this mpv (0.37) with Mesa's software GPU and mpv's default scalers, a 320 x 180 video played after a
        160 x 90 one was drawn black until a shader was put on or taken off, effect or no effect (that is the
        "flat colour" the generators' own clip test could not explain). And the player runs with --profile=fast,
        as it does on a Raspberry Pi 4 (pvj/hardware.py): with that cheap scaling the black picture did not come."""
        super(GpuCase, self).setUp()
        self.api.board = dict(self.api.board, kind="pi4")       # and the panel takes the box for one: 8-bit buffers under an effect
        self.real = Player(extra_args=["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + self.ES, "--ao=null", "--geometry=%dx%d" % (W, H),
                                       "--no-border", "--profile=fast"], rundir=self.rundir)
        self.addCleanup(self.real.stop)
        self.api.player = self.real
        self.play(CLIP)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.real.osd_size() != (W, H):
            time.sleep(0.05)
        self.assertEqual(self.real.osd_size(), (W, H), "the player has no window: is there a display (xvfb-run)?")
        self.assertEqual(self.real.ipc.request("get_property", "current-vo"), "gpu")
        self.api.registry.set_enabled("shaders", True)
        self.engine = self.api.shaders
        self.engine.log = lambda *_: None
        self.fx = self.api.effects
        self.fx.log = lambda *_: None
        self.fx.changer._use_thread = False

    def play(self, url):
        self.real.play([url], windowed=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                t = self.real.ipc.request("get_property", "time-pos")
            except PlayerError:
                t = None                    # not yet: the clip has not started
            if isinstance(t, (int, float)) and t > 0.15:
                break
            time.sleep(0.05)
        self.real.ipc.request("set_property", "screenshot-format", "png")
        self.real.ipc.request("set_property", "screenshot-high-bit-depth", False)
        self.real.ipc.request("set_property", "screenshot-png-filter", 0)
        self.real.ipc.request("set_property", "screenshot-png-compression", 1)
        # A clip of another shape than the one before: in this window on a virtual display the player went on drawing
        # it where the old one lay (a tall clip sat in the top left corner) until something made it lay the picture
        # out again. Changing "keepaspect" and back does; then wait until the bars on both sides are alike.
        self.real.ipc.request("set_property", "keepaspect", False)
        self.real.ipc.request("set_property", "keepaspect", True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            m = self.real.ipc.request("get_property", "osd-dimensions")
            if isinstance(m, dict) and abs(m.get("ml", 0) - m.get("mr", 9)) <= 1 and abs(m.get("mt", 0) - m.get("mb", 9)) <= 1 and (m.get("w"), m.get("h")) == (W, H):
                break
            time.sleep(0.05)
        self.settle()

    def settle(self, seconds=0.2):
        """Let the player draw a few pictures. Every change of a shader sets its renderer up again, and a screenshot
        taken before the first picture after that came out black on this rig (seen in the player's own log: the
        screenshot between "Using FBO format" and the next frame)."""
        try:
            start = self.real.ipc.request("get_property", "time-pos")
        except PlayerError:
            start = None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(0.05)
            try:
                now = self.real.ipc.request("get_property", "time-pos")
            except PlayerError:
                continue
            if not isinstance(start, (int, float)):
                start = now
            elif isinstance(now, (int, float)) and now - start >= seconds:
                return

    def shot(self):
        path = os.path.join(self.tmp, "shot.png")
        if os.path.exists(path):
            os.unlink(path)
        self.real.ipc.request("screenshot-to-file", path, "window")
        w, h, rows = quick_rows(path)
        self.assertEqual((w, h), (W, H))
        return rows

    def still(self, wait=6.0, flat=False):
        """A screenshot once the picture has settled: the clip is a still, so two in a row must agree, and unless
        `flat` is expected (a Blackout) it must be a picture. (A single screenshot taken right after a change was at
        times a flat or an old picture on this rig.)"""
        deadline, last = time.monotonic() + wait, None
        self.settle()
        while True:
            time.sleep(0.15)
            rows = self.shot()
            if last is not None and differ(rows, last)[0] <= 2 and (flat or len(set(grid(rows, 9))) > 12):
                return rows
            if time.monotonic() > deadline:
                return rows
            last = rows

    def put(self, sid, drawn=True, **kw):
        r = self.fx.put(sid, **kw)
        self.assertTrue(r and r["ok"], r)
        if drawn:
            self.assertIs(self.fx.on["checked"], True, "the player never drew a frame with %s" % sid)
        self.settle()
        if not drawn:               # amount 0: the text tells the player to leave the hook out, and it does
            self.assertEqual(self.fx._fresh(self.fx.on["desc"]), [], "the player drew a pass of %s at amount 0" % sid)
        return r

    def cap(self, lines):
        """Effect detail for a test: any number of lines, or None for the clip's own size. The box's own choices
        (540 and 720 lines) are far above these small clips, so the engine's choice is replaced and everything
        after it is the real thing: the text, the player's arithmetic, what is drawn."""
        if not hasattr(self, "_cap"):
            real = E.cap_lines
            self.addCleanup(setattr, E, "cap_lines", real)
            E.cap_lines = lambda *a, **k: self._cap
        self._cap = lines

    def pump(self, wait=3.0):
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if self.fx.changer.pump():
                self.settle()
                return
            time.sleep(0.02)
        self.fail("no change was due")

    def loaded(self):
        return [os.path.basename(p) for p in self.real.ipc.request("get_property", "glsl-shaders")]

    def text_on(self):
        with open(self.fx.on["path"]) as f:
            return f.read()

    def raw(self, text):
        """A hand-written hook, given to the player directly (for the stages an effect does not use)."""
        self.n = getattr(self, "n", 0) + 1
        path = os.path.join(self.rundir, "probe-%d.glsl" % self.n)
        with open(path, "w") as f:
            f.write(text)
        self.real.ipc.request("set_property", "glsl-shaders", [path])

    # -- the stage --
    def test_only_at_the_native_stage_do_blackout_and_opacity_still_darken_a_filter_that_adds_light(self):
        """The experiment the stage was chosen by. An invert turns black into white. Hooked after mpv's conversion to
        RGB (MAINPRESUB, MAIN), where the brightness that Blackout and opacity use has already been applied, it
        turns the blacked-out picture white; as an effect at NATIVE the brightness comes after it."""
        self.fx.upload("invert.fs", INVERT)
        plain = self.still()
        for stage in ("MAINPRESUB", "MAIN"):
            self.raw(MAIN_INVERT % (stage, 1 if stage == "MAIN" else 2))
            self.api.blackout({"on": True}, None, "t")
            rows = self.still(flat=True)
            lit = max(max(p) for p in grid(rows))
            print("stage, ES %s: an invert hooked at %s under Blackout: brightest %d" % (self.ES, stage, lit))
            self.assertGreater(lit, 200, "an invert at %s was dark under Blackout: the reason for NATIVE is gone, read effects.py again" % stage)
            self.api.blackout({"on": False}, None, "t")
        self.real.ipc.request("set_property", "glsl-shaders", [])
        self.put("invert.fs")
        inverted = self.still()
        d = differ(inverted, [[tuple(255 - c for c in p) for p in row] for row in plain])
        print("stage, ES %s: the effect inverts: against 255 minus the picture max %d mean %.2f" % (self.ES, d[0], d[1]))
        self.assertLess(d[1], 3.0)
        self.api.blackout({"on": True}, None, "t")
        self.assertLessEqual(max(max(p) for p in grid(self.still(flat=True))), 3, "Blackout does not darken the effect")
        self.api.blackout({"on": False}, None, "t")
        self.api.control({"action": "opacity", "value": 50}, None, "t")
        half = self.still()
        self.fx.off()
        d = differ(half, [[tuple(max(0, c - 128) for c in p) for p in row] for row in inverted])
        print("stage, ES %s: the effect at opacity 50: against the inverted picture less a half max %d mean %.2f" % (self.ES, d[0], d[1]))
        self.assertLess(d[1], 3.0)
        self.api.control({"action": "opacity", "value": 100}, None, "t")

    def test_the_picture_comes_back_unchanged_and_inverts_rightly_for_every_kind_of_picture(self):
        """A filter that changes nothing must give back the picture, and an invert must be 255 minus it, whatever the
        clip's colours are: BT.709 and BT.601, limited and full range, 10 bit, two planes, RGB packed and planar."""
        self.fx.upload("same.fs", SAME)
        self.fx.upload("invert.fs", INVERT)
        failed = []
        for name, url in KINDS:
            self.play(url)
            params = self.real.ipc.request("get_property", "video-params")
            out = self.real.ipc.request("get_property", "video-out-params")
            plain = self.still()
            negative = [[tuple(255 - c for c in p) for p in row] for row in plain]
            self.put("same.fs")
            same = differ(self.still(), plain)
            self.put("invert.fs")
            inv = differ(self.still(), negative)
            picture = dict(self.fx.on["picture"])
            other = []
            if picture["matrix"] != "rgb":           # the same invert with a text made for the other range: is the player's word right?
                real = self.fx.picture
                for levels in ("limited", "full"):
                    self.fx.picture = lambda levels=levels: dict(picture, levels=levels)
                    self.fx.adjust("anchor")
                    other.append("%s %.2f" % (levels, differ(self.still(), negative)[1]))
                self.fx.picture = real
            self.fx.off()
            print("kinds, ES %s: %-11s %s/%s %s (the output: %s %s) as %s: unchanged max %d mean %.2f; inverted max %d mean %.2f; with a text for %s" % (
                self.ES, name, params.get("pixelformat"), params.get("colormatrix"), params.get("colorlevels"), (out or {}).get("colormatrix"),
                (out or {}).get("colorlevels"), picture, same[0], same[1], inv[0], inv[1], ", ".join(other) or "nothing else"))
            if not alike(same):
                failed.append("%s: a filter that changes nothing changed the picture (max %d, mean %.2f)" % (name, same[0], same[1]))
            if inv[1] > 3.0:
                failed.append("%s: the invert is off by %.2f on average" % (name, inv[1]))
        self.assertEqual(failed, [])

    def test_an_effect_stays_right_when_another_kind_of_picture_starts_under_it(self):
        """A playlist that mixes a video and an RGB picture: the text holds a hook for each, so the first frame of the
        other kind is already right. Between two kinds of YUV the worker's regular look writes a new text."""
        self.fx.upload("invert.fs", INVERT)
        kinds = dict(KINDS)
        self.put("invert.fs")
        for name in ("rgb", "bt.601", "planar rgb", "bt.709"):
            self.fx.off()
            self.play(kinds[name])
            plain = self.still()
            self.play(CLIP)
            self.put("invert.fs")
            self.play(kinds[name])                   # the effect is on, made for BT.709, while the other picture starts
            self.assertIsNotNone(self.fx.current(), "an effect stays on when the clip changes")
            before = differ(self.still(), [[tuple(255 - c for c in p) for p in row] for row in plain])
            had = dict(self.fx.on["picture"])
            self.fx.adjust("anchor")                 # the worker's look, once a second
            after = differ(self.still(), [[tuple(255 - c for c in p) for p in row] for row in plain])
            print("change of picture, ES %s: to %-10s before the look (%s) mean %.2f, after it (%s) mean %.2f" % (
                self.ES, name, had, before[1], self.fx.on["picture"], after[1]))
            self.assertLess(after[1], 3.0, name)
            if name in ("rgb", "planar rgb"):
                self.assertLess(before[1], 3.0, "the hook for an RGB picture did not take over by itself")

    # -- where things are --
    def test_every_way_of_reading_the_picture_gives_the_same_pixel_for_every_size_and_shape(self):
        self.fx.upload("reads.fs", READS)
        self.fx.upload("swap.fs", SWAP_HALVES)
        failed = []
        for name, url in SHAPES:
            for stretch in (False, True):
                self.play(url)
                self.real.ipc.request("set_property", "keepaspect", not stretch)       # what the mapper does while a mapping is on
                plain = self.still()
                bars = self.real.ipc.request("get_property", "osd-dimensions")          # where the player put the picture
                sides, ends = (int(bars["ml"]), int(bars["mr"])), (int(bars["mt"]), int(bars["mb"]))
                what = "%s%s" % (name, ", stretched" if stretch else "")
                for way in (0, 1, 2, 3, 4):
                    self.put("reads.fs", values={"way": way})
                    d = differ(self.still(), mirrored(plain, *sides) if way == 4 else plain)
                    if d[0] > 6 or d[1] > ALIKE_MEAN:
                        failed.append("%s, way %d: max %d mean %.2f" % (what, way, d[0], d[1]))
                self.put("swap.fs")
                d = differ(self.still(), upside_down(plain, *ends))
                if d[0] > 6 or d[1] > ALIKE_MEAN:
                    failed.append("%s, top and bottom exchanged: max %d mean %.2f" % (what, d[0], d[1]))
                # the superseded "half": 540 lines at most, which no clip here reaches, so nothing is scaled
                self.put("reads.fs", values={"way": 4}, controls={"half": True})
                d = differ(self.still(), mirrored(plain, *sides))
                print("coordinates, ES %s: %-18s bars %s %s, mirrored with the old half: max %d mean %.2f" % (self.ES, what, sides, ends, d[0], d[1]))
                if d[0] > 6 or d[1] > ALIKE_MEAN:
                    failed.append("%s, mirrored with the old half: max %d mean %.2f" % (what, d[0], d[1]))
                self.fx.off()
                self.real.ipc.request("set_property", "keepaspect", True)
        self.assertEqual(failed, [])

    # -- the working size (Effect detail) --
    def test_a_capped_effect_draws_the_right_picture_for_every_shape_and_amount_0_is_the_clip_itself(self):
        """Under a cap the filter draws a smaller picture and the player scales it up: the same picture, softer.
        For each shape of clip, a cap that bites and (for one) a cap the clip is already under: every way of reading
        the picture, a mirror, top and bottom exchanged, a mix (an invert at amount one half is a flat grey), Blackout,
        and amount 0, which must be the clip as it is. The size the code takes for RENDERSIZE is compared with the
        size the player really draws at, pixel for pixel. Every case is tried before anything fails."""
        for name, text in (("reads.fs", READS), ("swap.fs", SWAP_HALVES), ("invert.fs", INVERT), ("same.fs", SAME)):
            self.fx.upload(name, text)
        cases = [("wide", CLIP, (320, 180), 100), ("tall", clip("90x160"), (90, 160), 60), ("square", clip("200x200"), (200, 200), 77),
                 ("small", clip("160x90"), (160, 90), 90), ("large", clip("640x360"), (640, 360), 135), ("large, low", clip("640x360"), (640, 360), 50)]
        failed = []
        for name, url, size, lines in cases:
            self.cap(None)
            self.play(url)
            plain = self.still()
            bars = self.real.ipc.request("get_property", "osd-dimensions")
            sides, ends = (int(bars["ml"]), int(bars["mr"])), (int(bars["mt"]), int(bars["mb"]))
            want = E.work_size(size[0], size[1], lines)
            scaled = want != size
            what = "%s %dx%d at %d lines (%dx%d)" % (name, size[0], size[1], lines, want[0], want[1])
            self.cap(lines)

            def judge(label, d, soft=6.0):
                # a picture that was not scaled must be the picture, as without a cap; a scaled one is softer
                print("capped, ES %s: %-36s %-22s max %3d mean %5.2f" % (self.ES, what, label, d[0], d[1]))
                if (d[1] > soft) if scaled else (d[0] > 6 or d[1] > ALIKE_MEAN):
                    failed.append("%s, %s: max %d mean %.2f" % (what, label, d[0], d[1]))

            for way in (0, 1, 2, 3, 4):
                self.put("reads.fs", values={"way": way})
                judge("read %d" % way, differ(self.still(), mirrored(plain, *sides) if way == 4 else plain))
            on = self.fx.state()["on"]["working"]
            if (on["lines"], (on["clip"]["width"], on["clip"]["height"]), (on["width"], on["height"]), on["scaled"]) != (lines, size, want, scaled):
                failed.append("%s: the panel is told %s" % (what, on))
            if (" WIDTH " in self.text_on().replace("//!", " ")) is not True or ("x >" in self.text_on()) != (size[1] > size[0]):
                failed.append("%s: the text is not the one for this shape" % what)
            self.put("swap.fs")
            judge("top and bottom", differ(self.still(), upside_down(plain, *ends)))
            self.put("same.fs")
            judge("unchanged", differ(self.still(), plain))
            # the mix is done where the filter draws, with the clip read at the same place: an invert at one half is
            # a flat grey whatever the size
            self.put("invert.fs", controls={"amount": 0.5})
            rows = self.still()
            inside = [rows[y][x] for y in range(ends[0] + 3, H - ends[1] - 3, 5) for x in range(sides[0] + 3, W - sides[1] - 3, 5)]
            off = max(abs(c - 128) for px in inside for c in px)
            print("capped, ES %s: %-36s %-22s furthest from grey %d" % (self.ES, what, "half inverted", off))
            if off > 6:
                failed.append("%s, an invert at amount 0.5: %d away from grey" % (what, off))
            self.put("invert.fs")
            self.api.blackout({"on": True}, None, "t")
            lit = max(max(px) for px in grid(self.still(flat=True)))
            self.api.blackout({"on": False}, None, "t")
            if lit > 3:
                failed.append("%s: Blackout does not darken a capped effect (brightest %d)" % (what, lit))
            # amount 0: the clip itself, not a scaled copy of it
            self.put("invert.fs", drawn=False, controls={"amount": 0.0})
            d = differ(self.still(), plain)
            print("capped, ES %s: %-36s %-22s max %3d mean %5.2f" % (self.ES, what, "amount 0", d[0], d[1]))
            if d[0] > 6 or d[1] > ALIKE_MEAN:           # the bar the shapes have without a cap, in the test above
                failed.append("%s, amount 0: not the clip as it is (max %d, mean %.2f)" % (what, d[0], d[1]))
            self.fx.change({"controls": {"amount": 1.0}})                                # and from 0 the filter comes back, looked at by the GPU
            self.pump()
            if self.fx.on["checked"] is not True or not self.fx._fresh(self.fx.on["desc"]):
                failed.append("%s: the filter did not come back from amount 0" % what)
            self.fx.off()
            # The size the generated code believes it draws at against the size the player draws at: a hook with the
            # same size lines and the same arithmetic paints light where the two agree to a hundredth of a pixel and
            # dark where they do not (the clip is a video: what a hook returns here is brightness, then two colour
            # differences, and one half is no colour).
            tall = size[1] > size[0]
            made = E.translate(S.parse(SAME, S.FILTER), lines=lines, tall=tall)
            code = [l for l in made.split("\n") if "pvj_work = " in l][:2]
            self.raw("//!HOOK NATIVE\n//!BIND HOOKED\n%s\n//!DESC nxlx size probe\n\nvec4 hook() {\n    vec2 pvj_work;\n%s\n"
                     "    vec2 at = HOOKED_pos * pvj_work;\n    float dx = abs(at.x - gl_FragCoord.x);\n"
                     "    float dy = min(abs(at.y - gl_FragCoord.y), abs(pvj_work.y - at.y - gl_FragCoord.y));\n"
                     "    return (max(dx, dy) < 0.01) ? vec4(0.85, 0.5, 0.5, 1.0) : vec4(0.15, 0.5, 0.5, 1.0);\n}\n"
                     % ("\n".join(E.size_lines(lines, tall)), "\n".join(code)))
            self.settle()
            rows = self.still()
            inside = [rows[y][x] for y in range(ends[0] + 2, H - ends[1] - 2, 3) for x in range(sides[0] + 2, W - sides[1] - 2, 3)]
            red = sum(1 for px in inside if not min(px) > 150)
            self.real.ipc.request("set_property", "glsl-shaders", [])
            print("capped, ES %s: %-36s %-22s %d of %d points disagree" % (self.ES, what, "the code's size", red, len(inside)))
            if red:
                failed.append("%s: the code's RENDERSIZE is not the size the player draws at (%d of %d points)" % (what, red, len(inside)))
        self.assertEqual(failed, [])

    def test_a_capped_effect_is_right_over_a_turned_picture(self):
        """A clip shown turned (the panel's Rotate, or a phone's video that says so itself) is still as it is stored
        where the effect hooks: the cap goes by its shorter side, and the picture comes out turned like the clip
        without an effect, not stretched and not shifted."""
        self.fx.upload("same.fs", SAME)
        self.fx.upload("invert.fs", INVERT)
        failed = []
        for name, url, size in (("wide", CLIP, (320, 180)), ("tall", clip("90x160"), (90, 160))):
            self.play(url)
            for turn in (90, 180, 270):
                self.cap(None)
                self.real.rotate(turn)
                self.settle()
                plain = self.still()
                self.put("invert.fs")
                negative = self.still()
                self.cap(60)
                self.put("same.fs")
                same = differ(self.still(), plain)
                self.put("invert.fs")
                inverted = differ(self.still(), negative)
                on = self.fx.state()["on"]["working"]
                self.put("invert.fs", drawn=False, controls={"amount": 0.0})
                zero = differ(self.still(), plain)
                self.fx.off()
                print("turned, ES %s: %-5s by %3d at 60 lines: unchanged mean %.2f, inverted against the uncapped invert mean %.2f, amount 0 max %d mean %.2f" % (
                    self.ES, name, turn, same[1], inverted[1], zero[0], zero[1]))
                if same[1] > 6.0 or inverted[1] > 6.0 or zero[0] > 6 or zero[1] > ALIKE_MEAN:
                    failed.append("%s turned by %d: unchanged %.2f, inverted %.2f, amount 0 max %d mean %.2f" % (name, turn, same[1], inverted[1], zero[0], zero[1]))
                if (on["width"], on["height"]) != E.work_size(size[0], size[1], 60):
                    failed.append("%s turned by %d: the panel is told %s" % (name, turn, on))
                # What the arithmetic rests on: where the effect hooks, a turned picture is still as it is stored
                # (light: wider than high). Were it handed over turned, the text would cap the wrong side.
                self.raw("//!HOOK NATIVE\n//!BIND HOOKED\n//!DESC nxlx turn probe\n\nvec4 hook() {\n"
                         "    return (HOOKED_size.x > HOOKED_size.y) ? vec4(0.85, 0.5, 0.5, 1.0) : vec4(0.15, 0.5, 0.5, 1.0);\n}\n")
                self.settle()
                middle = self.still()[H // 2][W // 2]
                self.real.ipc.request("set_property", "glsl-shaders", [])
                lying = min(middle) > 150
                print("turned, ES %s: %-5s by %3d: at the hook the picture is %s (stored %dx%d)" % (self.ES, name, turn, "wider than high" if lying else "higher than wide", size[0], size[1]))
                if lying != (size[0] > size[1]):
                    failed.append("%s turned by %d: at the hook the picture is not as it is stored" % (name, turn))
            self.real.rotate(0)
        self.assertEqual(failed, [])

    def test_a_mapped_surface_shows_the_filtered_picture(self):
        self.fx.upload("invert.fs", INVERT)
        plain = self.still()
        self.put("invert.fs")
        self.api.registry.set_enabled("mapper", True)
        self.api.mapper.handle({"action": "add", "type": "quad"})
        self.api.mapper.handle({"action": "on", "on": True})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.api.mapper.state()["status"]["state"] != "on":
            time.sleep(0.1)
        self.assertEqual(self.api.mapper.state()["status"]["state"], "on")
        names = self.loaded()
        self.assertTrue(len(names) == 2 and names[0].startswith("effect-") and names[1].startswith("mapper-"), names)
        rows = self.still()
        self.assertEqual(rows[4][4], (0, 0, 0))                                        # outside the surface
        want = tuple(255 - c for c in plain[H // 2][W // 2])
        self.near(rows[H // 2][W // 2], want, 14)                                      # the middle of the inverted picture, in the middle of the quad
        self.cap(100)                                                                  # the same under a cap: the surface shows the filtered picture
        self.put("invert.fs")
        self.assertTrue(self.fx.state()["on"]["working"]["scaled"])
        self.assertTrue(self.loaded()[1].startswith("mapper-"))
        self.near(self.still()[H // 2][W // 2], want, 14)
        self.cap(None)
        self.put("invert.fs", drawn=False, controls={"amount": 0.0})                  # a new text of the effect: the mapping stays behind it
        self.assertTrue(self.loaded()[1].startswith("mapper-"))
        self.near(self.still()[H // 2][W // 2], plain[H // 2][W // 2], 14)
        self.fx.off()
        names = self.loaded()
        self.assertTrue(len(names) == 1 and names[0].startswith("mapper-"), names)
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")
        self.api.mapper.handle({"action": "on", "on": False})
        self.assertEqual(self.loaded(), [])

    # -- the bundled filters --
    def varied(self, row, rng):
        """Other values for every input: numbers by the rotation's own rule, a switch the other way, the next choice,
        another point, another colour for each colour."""
        v = V.vary([dict(i, varies=True) for i in row["inputs"]], rng)
        colours = [[0.9, 0.35, 0.6, 1.0], [0.1, 0.5, 0.3, 1.0], [0.95, 0.85, 0.2, 1.0], [0.2, 0.25, 0.8, 1.0]]
        for i in row["inputs"]:
            if i["type"] == "bool":
                v[i["name"]] = not i["default"]
            elif i["type"] == "long" and "values" in i:
                v[i["name"]] = i["values"][(i["values"].index(i["default"]) + 1) % len(i["values"])]
            elif i["type"] == "point2D":
                v[i["name"]] = [0.35, 0.6]
            elif i["type"] == "color":
                v[i["name"]] = colours.pop(0) if colours else [0.5, 0.5, 0.5, 1.0]
        return v

    def test_every_bundled_filter_draws_changes_the_picture_and_gives_it_back_at_amount_0(self):
        """The project's own filters and the pack from Vidvox's ISF-Files, each over a real picture: with its
        defaults, with other values in every input, and with those values at amount 0. The GPU takes each text and
        draws with it; the picture with the filter is not the picture without it (a filter whose own defaults change
        nothing is named in NEUTRAL: its defaults must give the picture back, and it is judged on the values written
        there); at amount 0 it is the picture again. Every file is tried before anything fails; a line is printed
        per draw."""
        rows = [s for s in self.fx.library() if s["source"] == "bundled"]
        self.assertGreaterEqual(len(rows), 9)
        plain = self.still()
        failed, base_ms = [], None
        for s in rows:
            sid = s["id"]
            self.assertIsNone(s["error"], sid)
            rng = random.Random("fx " + sid)
            other = dict(self.varied(s, rng), **dict(NEUTRAL.get(sid, {}), **OTHER.get(sid, {})))
            for what, kw in (("defaults", {}), ("varied", {"values": other}), ("amount 0", {"values": other, "controls": {"amount": 0.0}})):
                self.fx._checked.clear()
                try:
                    self.fx.put(sid, **kw)
                except ApiError as e:
                    failed.append("%s (%s): %s" % (sid, what, e.message))
                    print("filter, ES %s: %-34s %-9s REFUSED %s" % (self.ES, sid, what, e.message))
                    continue
                shot = self.still()
                d = differ(shot, plain)
                colours = len(set(grid(shot, 5)))
                print("filter, ES %s: %-34s %-9s drawn=%s against the picture max %3d mean %6.2f changed %3d%% colours %d" % (
                    self.ES, sid, what, self.fx.on["checked"], d[0], d[1], round(d[2] * 100), colours))
                if what == "amount 0":           # the player leaves the hook out: nothing is drawn, nothing is paid
                    if self.fx._fresh(self.fx.on["desc"]):
                        failed.append("%s (%s): the player drew a pass at amount 0" % (sid, what))
                elif self.fx.on["checked"] is not True:
                    failed.append("%s (%s): the player never drew a frame with it" % (sid, what))
                if self.fx.error is not None and self.fx.error["id"] == sid:
                    failed.append("%s (%s): %s" % (sid, what, self.fx.error))
                same = alike(d)
                if what == "amount 0" or (what == "defaults" and sid in NEUTRAL):
                    if not same:
                        failed.append("%s (%s): not the picture as it was (max %d, mean %.2f)" % (sid, what, d[0], d[1]))
                else:
                    if d[2] < 0.02 and d[1] < 1.5:
                        failed.append("%s (%s): the picture is unchanged" % (sid, what))
            self.fx.off()
        self.assertEqual(failed, [])

    def test_what_a_filter_costs_next_to_a_generator(self):
        """Ratios only: the pass time of every bundled filter over a clip of the generators' drawing size, beside
        three generators, on this software GPU. The numbers for a real board come from the board."""
        times = {}
        for sid in ("nxlx-silk.fs", "nxlx-ember.fs", "nxlx-aurora.fs"):
            self.engine.show(sid)
            time.sleep(1.2)
            times[sid] = self.engine.state()["playing"].get("pass_ms")
        self.play(CLIP)
        for s in [s for s in self.fx.library() if s["source"] == "bundled"]:
            for lines in (None, 90):                    # the clip's own 180 lines, and a cap of half that
                self.cap(lines)
                try:
                    self.fx.put(s["id"])
                except ApiError:
                    continue
                time.sleep(0.6)
                times[s["id"] + (" (half)" if lines else "")] = (self.fx.state()["on"] or {}).get("pass_ms")
            self.fx.off()
        base = times.get("nxlx-silk.fs")
        self.assertTrue(base, "the player timed no pass for a generator")
        for name, ms in times.items():
            row = next((s for s in self.fx.library() if s["id"] == name.replace(" (half)", "")), None)
            print("cost, ES %s: %-42s %-7s pass %s ms, %s times nxlx-silk" % (
                self.ES, name, (row or {}).get("weight", "") or "", ms, "%.2f" % (ms / base) if ms else "?"))
        timed = [n for n, ms in times.items() if ms and n.startswith(("fx-", "isf-"))]
        self.assertGreaterEqual(len(timed), 9, "the player timed too few filter passes")

    # -- its life --
    def test_it_stays_over_the_next_clip_and_comes_off_with_stop_a_generator_and_a_restart(self):
        self.fx.upload("invert.fs", INVERT)
        self.put("invert.fs")
        self.assertEqual(self.real.ipc.request("get_property", "fbo-format"), "rgba8")      # as on a Pi 4 (see Player._apply_fbo)
        self.assertEqual(self.api.status({}, None, "t")["player"]["effect"], "invert")
        self.play(dict(SHAPES)["tall"])                                               # another clip: the effect stays
        self.assertEqual(len(self.loaded()), 1)
        self.assertEqual(self.fx.state()["on"]["id"], "invert.fs")
        over = self.still()
        self.fx.off()
        self.assertEqual((self.loaded(), self.real.ipc.request("get_property", "fbo-format")), ([], "auto"))
        plain_tall = self.still()
        # the bars beside a tall picture are not part of it: they stay black, only the picture is inverted
        self.assertEqual(over[H // 2][4], (0, 0, 0))
        self.near(over[H // 2][W // 2], tuple(255 - c for c in plain_tall[H // 2][W // 2]), 10)
        # Stop takes it off
        self.put("invert.fs")
        self.api.control({"action": "stop"}, None, "t")
        self.assertEqual(self.loaded(), [])
        self.assertIsNone(self.fx.state()["on"])
        self.assertEqual(self.fx.state()["last"], "Stop was pressed")
        self.assertEqual([n for n in os.listdir(self.rundir) if n.startswith("effect-")], [])
        # nothing plays: there is nothing to put it on
        with self.assertRaises(ApiError) as c:
            self.fx.put("invert.fs")
        self.assertEqual(c.exception.status, 409)
        # a generator takes the screen: the effect comes off with it, and none goes on over it
        self.play(CLIP)
        self.put("invert.fs")
        self.engine.show("nxlx-silk.fs")
        names = self.loaded()
        self.assertTrue(len(names) == 1 and names[0].startswith("shader-"), names)
        self.assertIsNone(self.fx.state()["on"])
        self.assertIn("generator", self.fx.state()["last"])
        with self.assertRaises(ApiError) as c:
            self.fx.put("invert.fs")
        self.assertEqual(c.exception.status, 409)
        self.assertIn("generator", c.exception.message)
        self.assertFalse(self.fx.state()["available"])
        # the player is restarted: the new one never had the effect, and the record says so
        self.play(CLIP)
        self.put("invert.fs")
        self.real.stop()
        self.play(CLIP)
        self.assertIsNone(self.fx.state()["on"])
        self.assertEqual(self.fx.state()["last"], "the player was restarted")
        self.assertEqual(self.loaded(), [])
        self.put("invert.fs")                                                        # and it goes on again like the first time
        self.assertEqual(len(self.loaded()), 1)

    def test_a_filter_the_gpu_refuses_is_reported_with_its_line_and_the_one_before_stays(self):
        self.fx.upload("invert.fs", INVERT)
        self.fx.upload("broken.fs", BROKEN)
        plain = self.still()
        with self.assertRaises(ApiError) as c:
            self.fx.put("broken.fs")
        self.assertEqual(c.exception.status, 422)
        self.assertIn("nonsense", c.exception.message)
        self.assertIn("line 4:", c.exception.message)                                # the line of the file, on every way of drawing
        self.assertIn("No effect is on", c.exception.message)
        self.assertEqual(self.loaded(), [])
        self.assertTrue(alike(differ(self.still(), plain)))                            # the picture as it was, not black
        self.put("invert.fs")
        inverted = self.still()
        for _ in range(2):                                                             # and every time again (mpv keeps quiet about a text it knows)
            with self.assertRaises(ApiError) as c:
                self.fx.put("broken.fs")
            self.assertIn("The effect before it is back on", c.exception.message)
        self.assertEqual(self.fx.state()["on"]["id"], "invert.fs")
        self.assertEqual(self.fx.state()["error"]["id"], "broken.fs")
        self.assertEqual(len(self.loaded()), 1)
        self.assertTrue(alike(differ(self.still(), inverted)))
        row = next(s for s in self.fx.state()["effects"] if s["id"] == "broken.fs")
        self.assertIn("nonsense", row["refused"])

    def test_values_of_every_type_the_amount_and_an_event_change_while_it_is_on(self):
        self.fx.upload("types.fs", ALL_TYPES)
        plain = self.still()
        self.put("types.fs")
        left = lambda: self.still()[H // 8][W // 8]
        self.near(left(), (64, 0, 0))
        for values, want in (({"level": 0.75}, (191, 0, 0)), ({"lit": True}, (191, 255, 0)), ({"mode": 2}, (191, 255, 255))):
            self.fx.change({"values": values})
            self.pump()
            self.near(left(), want)
        self.assertIs(self.fx.on["checked"], True)
        self.fx.change({"values": {"tint": [0.2, 0.4, 0.6], "spot": [0.25, 0.75]}})
        self.pump()
        rows = self.still()
        self.near(rows[H // 8][W - W // 8], (51, 102, 153))
        self.near(rows[H // 4][W // 4], (255, 255, 255))
        # the amount: half way between the picture and the filtered one, then the picture itself
        self.fx.change({"controls": {"amount": 0.5}})
        self.pump()
        got, was = self.still()[H // 8][W // 8], plain[H // 8][W // 8]
        self.near(got, tuple((a + b) // 2 for a, b in zip((191, 255, 255), was)), 8)
        self.fx.change({"controls": {"amount": 0.0}})
        self.pump()
        d = differ(self.still(), plain)
        self.assertTrue(alike(d), d)
        self.fx.change({"controls": {"amount": 1.0}})
        self.pump()
        self.fx.change({"values": {"bang": True}})
        self.pump()                                                                    # the press: held until the worker lets go
        self.near(left(), (255, 0, 255))
        self.pump()                                                                    # the release, a quarter of a second later
        self.near(left(), (191, 255, 255))
        self.assertIsNone(self.fx.error)

    def test_the_picture_is_never_dark_while_an_effect_goes_on_changes_and_comes_off(self):
        """No screenshot in the middle of it may be dark, with the clip playing and with it frozen, and that with the
        switch to 8-bit buffers that a Pi 4 makes for an effect. (With mpv's default scalers, which this rig does
        not use, a screenshot right after that switch was black in an earlier run; see Player._apply_fbo.)"""
        self.fx.upload("same.fs", SAME)
        self.fx.upload("invert.fs", INVERT)
        plain = self.still()
        negative = [[tuple(255 - c for c in p) for p in row] for row in plain]
        # frozen: nothing new arrives, so what is drawn now is what stays
        self.real.ipc.request("set_property", "pause", True)
        time.sleep(0.3)
        self.fx.put("invert.fs")
        time.sleep(0.4)
        d = differ(self.shot(), negative)
        self.assertLess(d[1], 3.0, "an effect put on over a frozen clip: not the inverted picture (max %d, mean %.2f)" % (d[0], d[1]))
        self.fx.change({"controls": {"amount": 0.0}})
        while not self.fx.changer.pump():
            time.sleep(0.02)
        time.sleep(0.4)
        d = differ(self.shot(), plain)
        self.assertTrue(alike(d), "amount 0 over a frozen clip: not the picture (max %d, mean %.2f)" % (d[0], d[1]))
        self.fx.off()
        time.sleep(0.4)
        d = differ(self.shot(), plain)
        self.assertTrue(alike(d), "an effect taken off a frozen clip: not the picture (max %d, mean %.2f)" % (d[0], d[1]))
        # Why an effect does not set the buffers' format: what a screenshot shows after that setting, over the frozen
        # clip, with and without a filter in the list. Printed, not asserted: it is the player's behaviour, not ours.
        seen = []
        for label, filter_on in (("no filter", False), ("a filter on", True)):
            if filter_on:
                self.fx.put("same.fs")
                time.sleep(0.4)
            for value in ("rgba8", "rgba8", "auto"):
                self.real.ipc.request("set_property", "fbo-format", value)
                time.sleep(0.4)
                d = differ(self.shot(), plain)
                seen.append("%s, fbo-format=%s: max %d mean %.1f" % (label, value, d[0], d[1]))
            self.fx.off()
        print("buffers, ES %s: over a frozen clip, against the picture: %s" % (self.ES, "; ".join(seen)))
        self.real.ipc.request("set_property", "pause", False)
        self.settle()
        shots = 0
        for n in range(6):
            self.fx.put("same.fs", controls={"amount": 0.5 + 0.08 * n})
            for _ in range(2):
                rows = self.shot()
                shots += 1
                self.assertGreater(len(set(grid(rows, 9))), 12, "a flat picture while the effect went on")
            self.fx.off()
            rows = self.shot()
            shots += 1
            self.assertGreater(len(set(grid(rows, 9))), 12, "a flat picture while the effect came off")
        print("no dark picture, ES %s: %d screenshots while an effect went on and off" % (self.ES, shots))

    def test_time_runs_at_the_clips_own_frame_rate_and_the_speed_scales_it(self):
        self.fx.upload("clock.fs", CLOCK)

        def now():
            r, g, _ = self.shot()[H // 2][W // 2]
            fine, coarse = r / 255.0 * 8.0, g / 255.0 * 64.0
            return (round((coarse - fine) / 8.0) * 8.0 + fine) % 64.0, time.monotonic()
        for name, url, fps in (("25 a second", CLIP, 25.0), ("60 a second", clip(rate=60), 60.0)):
            self.play(url)
            self.put("clock.fs")
            self.settle(0.4)
            self.assertEqual(self.fx.on["picture"]["fps"], fps)
            row = next(s for s in self.fx.library() if s["id"] == "clock.fs")
            self.assertEqual((row["moves"], row["speed_max"]), (True, 1.0))            # it reads the clock: the flash limit applies
            time.sleep(0.5)
            a, wa = now()
            time.sleep(2.0)
            b, wb = now()
            rate = ((b - a) % 64.0) / (wb - wa)
            print("time, ES %s: a clip of %s: TIME runs at %.2f times the clock" % (self.ES, name, rate))
            # 0.99 to 1.03 in the runs so far; the reading itself is good to about 3 in 100 over two seconds
            self.assertTrue(0.9 <= rate <= 1.1, "TIME runs at %.2f times the clock over a clip of %s" % (rate, name))
            self.fx.change({"controls": {"speed": 0.0}})
            self.pump()
            self.settle(0.4)
            c, _ = now()
            time.sleep(1.0)
            d, _ = now()
            self.assertLess(abs(d - c), 0.05, "speed 0 does not hold TIME")
            self.assertEqual(self.fx.change({"controls": {"speed": 3.0}})["controls"]["speed"], 1.0)     # never above its own pace
            self.fx.off()


for _name in dir(GpuCase):                    # the generators' tests run in their own job, not here again
    if _name.startswith("test_"):
        setattr(FxCase, _name, None)

ONLY = os.environ.get("PVJ_GPU_ONLY")          # "gles" or "desktop": CI runs each way of drawing in a job of its own

if ONLY != "desktop":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class GlesEffectTest(FxCase, ServerBase):
        ES = "yes"


if ONLY != "gles":
    @unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 and run under a display (xvfb-run) with mpv")
    class DesktopGlEffectTest(FxCase, ServerBase):
        ES = "no"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    ran = result.testsRun - len(result.skipped)
    print("effect GPU tests: %d run, %d skipped" % (ran, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() and ran > 0 and not result.skipped else 1)
