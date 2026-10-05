# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A spike, not a test: where in mpv's GPU pipeline can a filter over the playing picture sit?

It prints what a real mpv does (Mesa's software GPU under xvfb, as tests/test_shaders_gpu.py) and always exits 0.
The answers decide the design of pvj/effects.py; the real tests replace this file.

Run:  xvfb-run -a python3 -m tests.spike_effects_stage [yes|no]      (OpenGL ES yes or no)
"""
import os
import shutil
import sys
import tempfile
import time
import traceback

from pvj import effects as E, shaders as S
from pvj.player import Player
from tests.test_shaders_gpu import png_rows

W, H = 320, 180
PIC = "format=gbrp,geq=r=127+127*sin(14*X/W):g=floor(6*Y/H)*42:b=127+127*cos(19*(X+Y)/W)"
CLIPS = [
    ("yuv709", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=yuv420p,setparams=colorspace=bt709:range=tv" % PIC),
    ("yuv601", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=yuv420p,setparams=colorspace=smpte170m:range=tv" % PIC),
    ("full", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=yuv420p,setparams=range=pc" % PIC),
    ("p10", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=yuv420p10le" % PIC),
    ("rgb24", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=rgb24" % PIC),
    ("gbrp", "av://lavfi:nullsrc=size=320x180:rate=25,%s" % PIC),
    ("nv12", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=nv12" % PIC),
    ("yuyv", "av://lavfi:nullsrc=size=320x180:rate=25,%s,format=yuyv422" % PIC),
    ("tall", "av://lavfi:nullsrc=size=90x160:rate=25,%s,format=yuv420p" % PIC),
    ("square", "av://lavfi:nullsrc=size=200x200:rate=25,%s,format=yuv420p" % PIC),
    ("small", "av://lavfi:nullsrc=size=160x90:rate=25,%s,format=yuv420p" % PIC),
    ("hd", "av://lavfi:testsrc2=size=1280x720:rate=25,format=yuv420p"),
    ("fps60", "av://lavfi:nullsrc=size=320x180:rate=60,%s,format=yuv420p" % PIC),
    ("testsrc", "av://lavfi:testsrc=size=160x90:rate=25"),
]


class Rig:
    def __init__(self, es):
        self.es = es
        self.tmp = tempfile.mkdtemp(prefix="pvj-spike-")
        self.log = os.path.join(self.tmp, "mpv.log")
        self.p = Player(extra_args=["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + es, "--ao=null", "--geometry=%dx%d" % (W, H),
                                    "--no-border", "--log-file=" + self.log], rundir=self.tmp)
        self.n = 0
        self.params = {}

    def say(self, what, *value):
        print("SPIKE es=%s %s: %s" % (self.es, what, " ".join(str(v) for v in value)), flush=True)

    def get(self, name):
        try:
            return self.p.ipc.request("get_property", name)
        except Exception as e:
            return "?(%s)" % e

    def play(self, url, pause=True):
        if self.p.is_running():
            self.p.ipc.request("set_property", "glsl-shaders", [])
            self.p.ipc.request("set_property", "pause", False)
        self.p.play([url], windowed=True)
        end = time.monotonic() + 15
        while time.monotonic() < end:
            t = self.get("time-pos")
            if isinstance(t, (int, float)) and t > 0.2 and self.p.osd_size() == (W, H):
                break
            time.sleep(0.05)
        self.p.ipc.request("set_property", "screenshot-format", "png")
        self.p.ipc.request("set_property", "screenshot-high-bit-depth", False)
        if pause:
            self.p.ipc.request("set_property", "pause", True)
            time.sleep(0.4)
        vp = self.get("video-params")
        self.params = vp if isinstance(vp, dict) else {}

    def shot(self):
        path = os.path.join(self.tmp, "shot.png")
        if os.path.exists(path):
            os.unlink(path)
        self.p.ipc.request("screenshot-to-file", path, "window")
        w, h, rows = png_rows(path)
        return rows

    def base(self):
        for _ in range(20):
            rows = self.shot()
            if len({rows[y][x] for y in range(2, H, 7) for x in range(2, W, 7)}) > 20:
                return rows
            time.sleep(0.25)
        self.say("WARNING", "the unfiltered picture stayed flat")
        return rows

    def shaders(self, *texts):
        paths = []
        for t in texts:
            self.n += 1
            path = os.path.join(self.tmp, "spike-%d.glsl" % self.n)
            with open(path, "w") as f:
                f.write(t.replace("@N@", str(self.n)))
            paths.append(path)
        tap = S.LogTap(self.p.socket_path)
        try:
            self.p.ipc.request("set_property", "glsl-shaders", paths)
            lines = tap.drain(0.6)
        finally:
            tap.close()
        err = S.shader_errors(lines)
        return err or ""

    def passes(self):
        v = self.get("vo-passes")
        out = []
        for key in ("fresh", "redraw"):
            for x in ((v or {}).get(key) or []) if isinstance(v, dict) else []:
                out.append("%s:%s=%s" % (key, x.get("desc"), x.get("avg")))
        return out

    def conv(self):
        return E.native_glsl(self.params.get("colormatrix"), self.params.get("colorlevels"))


def diff(a, b, flip=None):
    """(largest, mean) difference over a grid of points; `flip` "h" or "v" mirrors b first."""
    worst, total, n = 0, 0, 0
    for y in range(3, H - 3, 4):
        for x in range(3, W - 3, 4):
            q = b[y][W - 1 - x] if flip == "h" else (b[H - 1 - y][x] if flip == "v" else b[y][x])
            d = max(abs(i - j) for i, j in zip(a[y][x], q))
            worst, total, n = max(worst, d), total + d, n + 1
    return "max %d mean %.2f" % (worst, total / float(n))


def brightest(rows):
    return max(max(rows[y][x]) for y in range(0, H, 5) for x in range(0, W, 5))


def hook(stage, body, binds=("HOOKED",), extra=(), head=()):
    return "\n".join(["//!HOOK %s" % stage] + ["//!BIND %s" % b for b in binds] + list(extra) + ["//!DESC spike @N@", "", "// spike @N@"]
                     + list(head) + ["vec4 hook() {"] + ["    " + line for line in body] + ["}", ""])


RAW = ["return HOOKED_tex(HOOKED_pos);"]
TRIP = ["vec4 t = HOOKED_tex(HOOKED_pos);", "vec3 c = pvj_rgb(t.rgb);", "return vec4(pvj_native(c), t.a);"]
INVERT = ["vec4 t = HOOKED_tex(HOOKED_pos);", "vec3 c = 1.0 - pvj_rgb(t.rgb);", "return vec4(pvj_native(c), t.a);"]
FLIP = ["vec4 t = HOOKED_tex(vec2(1.0 - HOOKED_pos.x, HOOKED_pos.y));", "vec3 c = pvj_rgb(t.rgb);", "return vec4(pvj_native(c), t.a);"]
SWAP = "//!HOOK OUTPUT\n//!BIND HOOKED\n//!DESC swap @N@\nvec4 hook() { return vec4(HOOKED_tex(HOOKED_pos).bgr, 1.0); }\n"


def one_clip(r, name, url, deep):
    r.play(url)
    vp = r.params
    r.say(name, "video-params", {k: vp.get(k) for k in ("pixelformat", "hw-pixelformat", "colormatrix", "colorlevels", "w", "h", "rotate", "alpha")},
          "container-fps", r.get("container-fps"), "estimated-vf-fps", r.get("estimated-vf-fps"), "hwdec", r.get("hwdec-current"))
    base = r.base()
    conv = r.conv()
    for fbo in (("rgba8", "auto") if deep else ("rgba8",)):
        r.p.ipc.request("set_property", "fbo-format", fbo)
        time.sleep(0.2)
        plain = r.shot()
        r.say(name, "fbo %s: no shader, against fbo auto:" % fbo, diff(plain, base))
        err = r.shaders(hook("NATIVE", RAW))
        r.say(name, "fbo %s: NATIVE pass-through:" % fbo, diff(r.shot(), plain), err, r.passes() if deep and fbo == "rgba8" else "")
        err = r.shaders(hook("NATIVE", TRIP, head=conv))
        r.say(name, "fbo %s: NATIVE to RGB and back:" % fbo, diff(r.shot(), plain), err)
        err = r.shaders(hook("NATIVE", INVERT, head=conv))
        inv = [[tuple(255 - c for c in px) for px in row] for row in plain]
        r.say(name, "fbo %s: NATIVE invert, against 255 minus the picture:" % fbo, diff(r.shot(), inv), err)
        # the two-block text: one block for a picture with a LUMA plane, one for a picture with an RGB plane
        dual = hook("NATIVE", INVERT, binds=("HOOKED", "LUMA"), head=conv if vp.get("colormatrix") != "rgb" else E.native_glsl("bt.709", "limited")) \
            + hook("NATIVE", INVERT, binds=("HOOKED", "RGB"), head=E.native_glsl("rgb", "full"))
        err = r.shaders(dual)
        r.say(name, "fbo %s: two blocks (BIND LUMA, BIND RGB) invert, against 255 minus the picture:" % fbo, diff(r.shot(), inv), err, r.passes())
    r.p.ipc.request("set_property", "fbo-format", "rgba8")
    r.shaders()
    time.sleep(0.2)
    plain = r.shot()
    # a mirror, with the picture as it lies in the window
    err = r.shaders(hook("NATIVE", FLIP, head=conv))
    r.say(name, "NATIVE mirror, against the screenshot mirrored:", diff(r.shot(), plain, "h"), err)
    if not deep:
        return
    # Blackout and opacity with a filter that adds light, at three stages
    for stage, body, head in (("NATIVE", INVERT, conv), ("MAINPRESUB", ["return vec4(1.0 - HOOKED_tex(HOOKED_pos).rgb, 1.0);"], []),
                              ("MAIN", ["return vec4(1.0 - HOOKED_tex(HOOKED_pos).rgb, 1.0);"], []),
                              ("NATIVE", ["return vec4(pvj_native(vec3(1.0)), 1.0);"], conv), ("MAIN", ["return vec4(1.0);"], [])):
        r.p.opacity(255)
        err = r.shaders(hook(stage, body, head=head))
        full = r.shot()
        r.p.opacity(0)
        time.sleep(0.4)
        dark = brightest(r.shot())
        r.p.opacity(128)
        time.sleep(0.4)
        half = r.shot()
        r.say(name, "%s %s: brightest under Blackout %d; at opacity 128 the centre is %s, at 255 it was %s" % (
            stage, "invert" if "1.0 -" in " ".join(body) else "white", dark, half[H // 2][W // 2], full[H // 2][W // 2]), err)
    r.shaders()
    r.p.opacity(128)
    time.sleep(0.4)
    dim = r.shot()
    r.shaders(hook("NATIVE", TRIP, head=conv))
    r.say(name, "NATIVE to RGB and back at opacity 128, against no shader at opacity 128:", diff(r.shot(), dim))
    r.p.opacity(255)
    # the mapper's stage sees the filtered picture
    err = r.shaders(hook("NATIVE", INVERT, head=conv), SWAP)
    want = [[(255 - px[2], 255 - px[1], 255 - px[0]) for px in row] for row in plain]
    r.say(name, "NATIVE invert then an OUTPUT hook that swaps red and blue:", diff(r.shot(), want), err, r.get("glsl-shaders") and len(r.get("glsl-shaders")))
    # stretched (as with a mapping on) and rotated
    r.shaders()
    for prop, value, back in (("keepaspect", False, True), ("video-rotate", 90, 0), ("video-rotate", 180, 0)):
        r.p.ipc.request("set_property", prop, value)
        time.sleep(0.4)
        plain2 = r.shot()
        err = r.shaders(hook("NATIVE", FLIP, head=conv))
        got = r.shot()
        r.say(name, "%s=%s: NATIVE mirror against the screenshot mirrored left-right: %s; top-bottom: %s" % (prop, value, diff(got, plain2, "h"), diff(got, plain2, "v")), err)
        r.shaders()
        r.p.ipc.request("set_property", prop, back)
    time.sleep(0.3)
    # half the size
    err = r.shaders(hook("NATIVE", TRIP, head=conv, extra=("//!WIDTH HOOKED.w 2 /", "//!HEIGHT HOOKED.h 2 /")))
    r.say(name, "NATIVE at half size, against the picture:", diff(r.shot(), plain), err, r.passes())
    err = r.shaders(hook("NATIVE", FLIP, head=conv, extra=("//!WIDTH HOOKED.w 2 /", "//!HEIGHT HOOKED.h 2 /")))
    r.say(name, "NATIVE mirror at half size, against the screenshot mirrored:", diff(r.shot(), plain, "h"), err)
    # sizes the hook is told
    probe = ["vec2 s = HOOKED_size / vec2(1280.0, 720.0);", "return vec4(pvj_native(vec3(s.x, s.y, HOOKED_pt.x * 100.0)), 1.0);"]
    err = r.shaders(hook("NATIVE", probe, head=conv))
    px = r.shot()[H // 2][W // 2]
    r.say(name, "HOOKED_size read back as about %d x %d, 1/HOOKED_pt.x about %s" % (round(px[0] / 255.0 * 1280), round(px[1] / 255.0 * 720),
                                                                                    round(100.0 / (px[2] / 255.0)) if px[2] else "?"), px, err)
    # a file that redefines a built-in function when it is not on OpenGL ES (four of the candidate filters do)
    redef = ["#ifndef GL_ES", "float distance(vec2 a, vec2 b) { float t = pow(a.x - b.x, 2.0) + pow(a.y - b.y, 2.0); return pow(t, 0.5); }", "#endif"]
    err = r.shaders(hook("NATIVE", ["float d = distance(HOOKED_pos, vec2(0.5));", "return vec4(pvj_native(vec3(d)), 1.0);"], head=conv + redef))
    r.say(name, "a redefined distance():", "refused: " + err if err else "taken", "centre", r.shot()[H // 2][W // 2])


def frame_rate(r, name, url):
    """How fast mpv's `frame` number goes for a clip of a known rate."""
    r.play(url, pause=False)
    conv = r.conv()
    body = ["int f = frame - (frame / 256) * 256;", "return vec4(pvj_native(vec3(float(f) / 255.0, 0.5, 0.5)), 1.0);"]
    err = r.shaders(hook("NATIVE", body, head=conv))
    time.sleep(0.5)
    a, t0 = r.shot()[H // 2][W // 2][0], time.monotonic()
    time.sleep(2.0)
    b, t1 = r.shot()[H // 2][W // 2][0], time.monotonic()
    r.say(name, "`frame` went on by %.1f a second (container-fps %s, estimated-vf-fps %s, speed %s)" % (
        ((b - a) % 256) / (t1 - t0), r.get("container-fps"), r.get("estimated-vf-fps"), r.get("speed")), err)


def change_of_source(r):
    """A text made for one picture, left on while another kind of picture starts (a playlist that mixes them)."""
    clips = dict(CLIPS)
    r.play(clips["yuv709"])
    conv = r.conv()
    one = hook("NATIVE", INVERT, head=conv)
    dual = hook("NATIVE", INVERT, binds=("HOOKED", "LUMA"), head=conv) + hook("NATIVE", INVERT, binds=("HOOKED", "RGB"), head=E.native_glsl("rgb", "full"))
    for label, text in (("one block made for yuv709", one), ("two blocks", dual)):
        for nxt in ("rgb24", "gbrp", "full", "yuv601", "yuv709"):
            r.play(clips[nxt])
            plain = r.shot()
            inv = [[tuple(255 - c for c in px) for px in row] for row in plain]
            err = r.shaders(text)
            first = diff(r.shot(), inv)
            # and with the text already on while the next picture starts
            r.p.ipc.request("set_property", "pause", False)
            r.p.ipc.request("loadfile", clips["yuv709"], "replace")
            time.sleep(0.8)
            r.p.ipc.request("loadfile", clips[nxt], "replace")
            time.sleep(0.8)
            r.p.ipc.request("set_property", "pause", True)
            time.sleep(0.4)
            r.say("change of source", "%s, over %s: invert against 255 minus the picture: put on over it %s; left on from yuv709 %s" % (
                label, nxt, first, diff(r.shot(), inv)), err, r.passes())


def main():
    es = sys.argv[1] if len(sys.argv) > 1 else "yes"
    r = Rig(es)
    try:
        r.play(CLIPS[0][1])
        r.say("mpv", r.get("mpv-version"), "vo", r.get("current-vo"), "gpu context", r.get("current-gpu-context"))
        for n, (name, url) in enumerate(CLIPS):
            try:
                one_clip(r, name, url, deep=name in ("yuv709", "tall", "rgb24"))
            except Exception:
                r.say(name, "FAILED", traceback.format_exc().replace("\n", " | "))
            r.p.opacity(255)
        for name in ("yuv709", "fps60", "hd"):
            try:
                frame_rate(r, name, dict(CLIPS)[name])
            except Exception:
                r.say(name, "FAILED", traceback.format_exc().replace("\n", " | "))
        try:
            change_of_source(r)
        except Exception:
            r.say("change of source", "FAILED", traceback.format_exc().replace("\n", " | "))
    finally:
        r.p.stop()
        try:
            with open(r.log, errors="replace") as f:
                for line in f:
                    if any(k in line for k in ("GL_VERSION", "GL_VENDOR", "GL_RENDERER", "GLSL", "Detected", "fbo format", "Using FBO")):
                        r.say("log", line.strip()[:200])
        except OSError:
            pass
        shutil.rmtree(r.tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.exit(0)
