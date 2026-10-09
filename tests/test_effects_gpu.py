# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""SPIKE, throwaway branch transitions-spike only. Not a test: measurements for transitions between clips (D71).

It stands where the effects GPU tests stand so that the three effects-gpu jobs run it (OpenGL ES, desktop OpenGL,
desktop OpenGL 3.1 with GLSL 1.40) without a change to the workflow. Every line it prints starts with SPIKE. It
never fails the job: an experiment that breaks prints its traceback and the next one runs.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zlib

from pvj.player import Player, PlayerError

ES = "yes" if os.environ.get("PVJ_GPU_ONLY") == "gles" else "no"
WW, WH = 1280, 720
PIC = "geq=r=127+80*sin(14*X/W):g=50+floor(6*Y/H)*26:b=127+80*cos(19*(X+Y)/W)"
FLAT_B = "av://lavfi:color=c=0x2060C0:size=320x180:rate=25"
T0 = time.monotonic()


def say(*a):
    print("SPIKE [%7.2f]" % (time.monotonic() - T0), *a, flush=True)


def clip(size="320x180", fmt="yuv420p", rate=25, tags=",setparams=colorspace=bt709:range=tv"):
    return "av://lavfi:color=c=black:size=%s:rate=%d,format=gbrp,trim=end_frame=1,%s,format=%s,loop=loop=-1:size=1,setpts=N/(%d*TB)%s" % (
        size, rate, PIC, fmt, rate, tags)


def write_png(path, w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


class Img:
    """An 8-bit PNG written with no row filter: the pixel bytes as they are."""

    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read()
        self.bytes = len(data)
        pos, idat = 8, []
        self.w = self.h = 0
        self.bpp = 3
        while pos < len(data):
            n, kind = struct.unpack(">I4s", data[pos:pos + 8])
            if kind == b"IHDR":
                self.w, self.h, depth, ctype = struct.unpack(">IIBB", data[pos + 8:pos + 18])
                self.bpp = {2: 3, 6: 4}.get(ctype, 0)
                self.depth = depth
            elif kind == b"IDAT":
                idat.append(data[pos + 8:pos + 8 + n])
            pos += 12 + n
        self.raw = zlib.decompress(b"".join(idat))
        self.stride = self.w * self.bpp + 1
        self.filtered = any(self.raw[y * self.stride] for y in range(self.h))

    def px(self, x, y):
        o = y * self.stride + 1 + x * self.bpp
        return tuple(self.raw[o:o + 3])

    def rgb(self):
        """The pixels as one run of RGB bytes."""
        s, bpp = self.stride, self.bpp
        if bpp == 3:
            return b"".join(self.raw[y * s + 1:(y + 1) * s] for y in range(self.h))
        out = bytearray(self.w * self.h * 3)
        body = b"".join(self.raw[y * s + 1:(y + 1) * s] for y in range(self.h))
        for c in range(3):
            out[c::3] = body[c::4]
        return bytes(out)


def rgb_to(rgb, w, h, k, order=(0, 1, 2), alpha=True):
    """Every k-th pixel of every k-th row of RGB bytes, as RGBA (or BGRA with order (2, 1, 0)), or RGB without alpha."""
    nw, nh = w // k, h // k
    rows = [rgb[y * k * w * 3:(y * k * w + nw * k) * 3] for y in range(nh)]
    body = b"".join(rows)
    n = nw * nh
    step = 3 * k
    out = bytearray(n * (4 if alpha else 3))
    width = 4 if alpha else 3
    for i, c in enumerate(order):
        out[i::width] = body[c::step]
    if alpha:
        out[3::4] = b"\xff" * n
    return nw, nh, bytes(out)


class Rig:
    def __init__(self, name, fast=True):
        self.name = name
        self.tmp = tempfile.mkdtemp(prefix="spike-")
        self.log = os.path.join(self.tmp, "mpv.log")
        args = ["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + ES, "--ao=null", "--geometry=%dx%d" % (WW, WH), "--no-border",
                "--log-file=" + self.log]
        if fast:
            args.append("--profile=fast")
        self.p = Player(extra_args=args, rundir=self.tmp)
        self.p.ipc.timeout = 30.0
        self.req = self.p.ipc.request
        self.n = 0

    def start(self, url):
        self.p.play([url], windowed=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.p.osd_size() != (WW, WH):
            time.sleep(0.05)
        say(self.name, "window", self.p.osd_size(), "vo", self.get("current-vo"))
        self.playing()
        for k, v in (("screenshot-format", "png"), ("screenshot-high-bit-depth", False), ("screenshot-png-filter", 0), ("screenshot-png-compression", 0)):
            self.req("set_property", k, v)

    def get(self, prop):
        try:
            return self.req("get_property", prop)
        except PlayerError as e:
            return "ERR(%s)" % str(e)[:40]

    def playing(self, least=0.15, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            t = self.get("time-pos")
            if isinstance(t, (int, float)) and t > least:
                return True
            time.sleep(0.03)
        return False

    def load(self, url, least=0.15):
        self.req("loadfile", url, "replace")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.get("path") != url:
            time.sleep(0.02)
        return self.playing(least)

    def shot(self, mode="window", ext="png"):
        path = os.path.join(self.tmp, "shot-%s.%s" % (mode, ext))
        try:
            os.unlink(path)
        except OSError:
            pass
        self.req("screenshot-to-file", path, mode)
        return Img(path) if ext == "png" else path

    def timed_shot(self, mode, ext="png", comp=0, runs=3):
        self.req("set_property", "screenshot-format", "jpg" if ext == "jpg" else "png")
        self.req("set_property", "screenshot-png-compression", comp)
        self.req("set_property", "screenshot-jpeg-quality", 85)
        path = os.path.join(self.tmp, "timed.%s" % ext)
        times, size = [], 0
        for _ in range(runs):
            try:
                os.unlink(path)
            except OSError:
                pass
            a = time.monotonic()
            self.req("screenshot-to-file", path, mode)
            times.append((time.monotonic() - a) * 1000)
            size = os.path.getsize(path)
        self.req("set_property", "screenshot-format", "png")
        self.req("set_property", "screenshot-png-compression", 0)
        return "min %.0f ms, max %.0f ms, %d bytes" % (min(times), max(times), size)

    def write(self, text, binary=False):
        self.n += 1
        path = os.path.join(self.tmp, "x-%d.%s" % (self.n, "bin" if binary else "glsl"))
        with open(path, "wb" if binary else "w") as f:
            f.write(text)
        return path

    def shaders(self, paths):
        self.req("set_property", "glsl-shaders", list(paths))

    def drawn(self, desc, timeout=20.0):
        """Seconds until the player lists a pass called `desc` that took time, or None."""
        a = time.monotonic()
        while time.monotonic() - a < timeout:
            p = self.get("vo-passes")
            if isinstance(p, dict):
                for x in (p.get("fresh") or []):
                    if isinstance(x, dict) and desc in str(x.get("desc", "")) and any(isinstance(x.get(k), (int, float)) and x[k] > 0 for k in ("avg", "last")):
                        return time.monotonic() - a
            time.sleep(0.005)
        return None

    def counters(self):
        return tuple(self.get(k) for k in ("frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count", "decoder-frame-drop-count"))

    def settle(self, seconds=0.25):
        time.sleep(seconds)

    def loglines(self, words, limit=25):
        try:
            with open(self.log, errors="replace") as f:
                lines = [ln.rstrip() for ln in f if any(w in ln for w in words)]
        except OSError:
            lines = []
        for ln in lines[:limit]:
            say(self.name, "log:", ln[:300])
        if len(lines) > limit:
            say(self.name, "log: ... %d more" % (len(lines) - limit))

    def close(self):
        try:
            self.p.stop()
        except Exception:
            pass
        self.loglines(("rror", "ailed", "nsupported", "nvalid"), 12)
        shutil.rmtree(self.tmp, ignore_errors=True)


STAMP = """//!HOOK OUTPUT
//!BIND HOOKED
//!DESC spike stamp %d
vec4 hook() {
    int f = frame;
    int hi = f / 65536;
    int rest = f - hi * 65536;
    int mid = rest / 256;
    int lo = rest - mid * 256;
    return vec4(float(lo) / 255.0, float(mid) / 255.0, float(hi) / 255.0, 1.0);
}
"""
FX_MAIN = "//!HOOK MAIN\n//!BIND HOOKED\n//!DESC spike fx\nvec4 hook() { return vec4(vec3(1.0) - HOOKED_tex(HOOKED_pos).rgb, 1.0); }\n"
FX_NATIVE = "//!HOOK NATIVE\n//!BIND HOOKED\n//!DESC spike fxn\nvec4 hook() { return vec4(vec3(1.0) - HOOKED_tex(HOOKED_pos).rgb, 1.0); }\n"
MAP = "//!HOOK OUTPUT\n//!BIND HOOKED\n//!DESC spike map\nvec4 hook() { return HOOKED_tex(vec2(1.0 - HOOKED_pos.x, HOOKED_pos.y)); }\n"
HOLD = "//!HOOK MAIN\n//!BIND HOOKED\n//!DESC spike hold\nvec4 hook() { return vec4(1.0, 0.0, 1.0, 1.0); }\n"
GEN = "//!HOOK NATIVE\n//!BIND HOOKED\n//!DESC spike gen\nvec4 hook() { return vec4(HOOKED_pos, 0.25, 1.0); }\n"


def blend(w, h, fmt, hexdata, p, tag, stage="MAIN"):
    return ("//!TEXTURE PVJSTILL\n//!SIZE %d %d\n//!FORMAT %s\n//!FILTER LINEAR\n//!BORDER CLAMP\n%s\n\n"
            "//!HOOK %s\n//!BIND HOOKED\n//!BIND PVJSTILL\n//!DESC spike blend %s\n"
            "vec4 hook() {\n    vec4 s = texture(PVJSTILL, HOOKED_pos);\n    return vec4(mix(s.rgb, HOOKED_tex(HOOKED_pos).rgb, %.4f), 1.0);\n}\n"
            % (w, h, fmt, hexdata, stage, tag, p))


def versions():
    try:
        out = subprocess.run(["mpv", "--version"], capture_output=True, text=True, timeout=20).stdout.splitlines()
        for ln in out[:4]:
            say("version:", ln)
    except Exception as e:
        say("version: failed", e)
    r = Rig("versions")
    try:
        r.start(clip())
        say("ES", ES, "env", {k: v for k, v in os.environ.items() if k.startswith("MESA") or k.startswith("PVJ_GPU")})
        for prop in ("mpv-version", "ffmpeg-version", "current-vo", "current-gpu-context", "video-sync", "display-fps", "hwdec-current",
                     "video-params/pixelformat", "dither", "fbo-format", "scale"):
            say("prop", prop, "=", r.get(prop))
    finally:
        r.p.stop()
        r.loglines(("GL_VERSION", "GL_RENDERER", "GL_SHADING", "GLSL", "Loaded extension", "FBO format", "texture formats", "rgb8", "rgba8 "), 30)
        r.close()


def q3_frame():
    """What is mpv's `frame` uniform: when does it move, stand still, start again?"""
    r = Rig("q3")
    try:
        r.start(clip())
        r.shaders([r.write(STAMP % 1)])
        r.settle(0.5)
        began = time.monotonic()

        def f(label, mode="window"):
            try:
                img = r.shot(mode)
                c = img.px(img.w // 2, img.h // 2)
                v = c[0] + c[1] * 256 + c[2] * 65536
            except Exception as e:
                v = "ERR(%s)" % str(e)[:60]
            say("q3 %-34s frame=%s at %.2f s time-pos=%s" % (label, v, time.monotonic() - began, r.get("time-pos")))
            return v

        f("playing a 25 fps clip")
        f("at once again")
        f("at once again")
        time.sleep(1.0)
        f("after 1.00 s more of playing")
        time.sleep(2.0)
        f("after 2.00 s more of playing")
        r.req("set_property", "pause", True)
        time.sleep(0.3)
        f("paused")
        f("paused, at once again")
        f("paused, at once again")
        time.sleep(1.0)
        f("paused, 1 s later")
        try:
            img = r.shot("video")
            say("q3 a video-mode screenshot while paused: size %dx%d centre %s" % (img.w, img.h, img.px(img.w // 2, img.h // 2)))
        except Exception as e:
            say("q3 video-mode screenshot failed", e)
        f("paused, after a video-mode screenshot")
        r.req("set_property", "pause", False)
        time.sleep(0.4)
        f("playing again (0.4 s)")
        r.req("seek", 0, "absolute")
        time.sleep(0.4)
        f("0.4 s after a seek to 0")
        r.load(FLAT_B)
        f("after loadfile of another clip, same size")
        time.sleep(1.0)
        f("1 s later")
        r.load(clip("640x360", "rgb24", 30, ""))
        f("after loadfile of a clip of another size, rgb, 30 fps")
        time.sleep(1.0)
        f("1 s later (30 fps)")
        r.shaders([r.write(STAMP % 2)])
        time.sleep(0.4)
        f("0.4 s after the shader file was exchanged")
        r.req("set_property", "fbo-format", "rgba8")
        time.sleep(0.4)
        f("0.4 s after fbo-format rgba8")
        r.req("set_property", "fbo-format", "auto")
        time.sleep(0.4)
        f("0.4 s after fbo-format auto")
        still = os.path.join(r.tmp, "still.png")
        write_png(still, 320, 180, (90, 120, 60))
        r.p.play([still], windowed=True)
        time.sleep(0.6)
        f("a still picture (png), 0.6 s after play")
        f("still picture, at once again")
        time.sleep(1.0)
        f("still picture, 1 s later")
        r.shaders([r.write(STAMP % 3)])
        time.sleep(0.3)
        f("still picture, after a shader exchange")
        r.req("stop")
        time.sleep(0.4)
        f("idle (after stop)")
    finally:
        r.close()


def timeline(r, marks, seconds, until=None, every=0.0):
    """Look at the window again and again: what the player says and what the middle of the picture shows."""
    began = time.monotonic()
    seen = 0
    rows = []
    while time.monotonic() - began < seconds:
        t = time.monotonic() - began
        props = [r.get(k) for k in ("path", "time-pos", "idle-active", "core-idle", "video-params/w", "estimated-frame-number", "seeking", "video-out-params/w")]
        try:
            img = r.shot("window")
            c = img.px(img.w // 2, img.h // 2)
            what = next((name for name, want in marks.items() if all(abs(a - b) <= 14 for a, b in zip(c, want))), "black" if max(c) < 16 else str(c))
        except Exception as e:
            what = "no-shot(%s)" % str(e)[:40]
        took = time.monotonic() - began - t
        props[0] = os.path.basename(str(props[0]))[-18:]
        rows.append("%.3f(+%.0fms) %-10s path=%s t=%s idle=%s core-idle=%s w=%s n=%s seeking=%s out-w=%s" % ((t, took * 1000, what) + tuple(props)))
        if until and what == until:
            seen += 1
            if seen >= 3:
                break
        if every:
            time.sleep(every)
    return rows


def q5_gap():
    """What is on the screen between loadfile and the first frame of the new clip?"""
    r = Rig("q5")
    try:
        r.start(clip())
        r.p.ipc.timeout = 3.0
        img = r.shot("window")
        a_px = img.px(img.w // 2, img.h // 2)
        marks = {"A": a_px, "B": (0x20, 0x60, 0xC0), "HOLD": (255, 0, 255)}
        say("q5 clip A's middle pixel", a_px)
        r.req("loadfile", FLAT_B, "replace")
        for row in timeline(r, marks, 3.0, until="B"):
            say("q5 fast-switch", row)
        # a source that opens slowly: a pipe nobody writes to for 1.5 seconds
        for hold in (False, True):
            tag = "q5 slow-pipe%s" % (" with a shader that draws magenta" if hold else "")
            r.load(clip())
            r.shaders([r.write(HOLD)] if hold else [])
            time.sleep(0.4)
            fifo = os.path.join(r.tmp, "in-%d.fifo" % hold)
            os.mkfifo(fifo)
            frame = bytes([150, 60, 150, 190]) * (320 * 180 // 2)
            stop = threading.Event()

            def writer():
                time.sleep(1.5)
                deadline = time.monotonic() + 8
                fd = None
                while fd is None and time.monotonic() < deadline:
                    try:
                        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        time.sleep(0.05)
                if fd is None:
                    say(tag, "writer: nobody opened the pipe")
                    return
                os.set_blocking(fd, True)
                say(tag, "writer: first frame goes in now")
                try:
                    for _ in range(100):
                        if stop.is_set():
                            break
                        os.write(fd, frame)
                        time.sleep(0.04)
                except OSError as e:
                    say(tag, "writer stopped:", e)
                finally:
                    os.close(fd)

            th = threading.Thread(target=writer, daemon=True)
            th.start()
            a = time.monotonic()
            try:
                r.p.play_pipe(fifo, 320, 180, 25)
                say(tag, "play_pipe returned after %.0f ms" % ((time.monotonic() - a) * 1000))
            except Exception as e:
                say(tag, "play_pipe failed after %.0f ms: %s" % ((time.monotonic() - a) * 1000, e))
            marks["P"] = None
            rows = timeline(r, {k: v for k, v in marks.items() if v}, 5.0, every=0.05)
            last = None
            for row in rows:              # print a row when what is seen changes, and its neighbours
                what = row.split()[1]
                if what != last:
                    say(tag, row)
                last = what
            say(tag, "last row:", rows[-1] if rows else None)
            stop.set()
            th.join(3)
            r.shaders([])
    finally:
        r.close()


def q1_stills():
    """Which screenshot mode holds what: user shaders before OUTPUT, a shader at OUTPUT (the mapper), brightness, overlays."""
    r = Rig("q1")
    try:
        r.start(clip())
        fx, fxn, mp = r.write(FX_MAIN), r.write(FX_NATIVE), r.write(MAP)
        say("q1 the plain picture has about (99,76,160) at a quarter from the left and (57,76,98) at three quarters, a quarter down")

        def look(label):
            for mode in ("video", "subtitles", "window"):
                try:
                    img = r.shot(mode)
                    say("q1 %-30s %-9s %4dx%-4d bpp %d left %s right %s" % (label, mode, img.w, img.h, img.bpp, img.px(img.w // 4, img.h // 4),
                                                                              img.px(3 * img.w // 4, img.h // 4)))
                except Exception as e:
                    say("q1 %-30s %-9s failed: %s" % (label, mode, e))

        look("plain")
        r.shaders([fx]); r.settle(); look("invert at MAIN")
        r.shaders([mp]); r.settle(); look("mirror at OUTPUT")
        r.shaders([fx, mp]); r.settle(); look("invert at MAIN + mirror at OUTPUT")
        r.shaders([]); r.settle()
        r.req("set_property", "brightness", -30); r.settle(); look("brightness -30")
        r.req("set_property", "brightness", 0)
        box = r.write(bytes([0, 0, 255, 255]) * (400 * 200), binary=True)       # opaque red, BGRA
        r.req("overlay-add", 5, 220, 130, box, 0, "bgra", 400, 200, 1600); r.settle(); look("a red overlay over the left point")
        r.req("overlay-remove", 5)
        r.load(clip("320x180", "rgb24", 25, ""))
        r.shaders([fxn]); r.settle(); look("rgb clip, invert at NATIVE")
        r.shaders([]); r.settle()
        r.load(clip("90x160"))
        r.req("set_property", "keepaspect", False); r.req("set_property", "keepaspect", True); r.settle(0.5)
        look("a tall clip (90x160)")
        say("q1 osd-dimensions for the tall clip", r.get("osd-dimensions"))
        r.load(clip())
        r.settle(0.5)
        before = r.counters()
        for mode, ext, comp in (("window", "png", 0), ("window", "png", 1), ("window", "jpg", 0), ("video", "png", 0)):
            say("q1 time of a %s screenshot at %dx%d, %s compression %d: %s" % (mode, WW, WH, ext, comp, r.timed_shot(mode, ext, comp, 5)))
        say("q1 counters (dropped, delayed, mistimed, decoder-dropped) before and after those 20 screenshots", before, r.counters())
    finally:
        r.close()


def q2_texture():
    """A still as a texture in the shader file: what it costs at three sizes."""
    r = Rig("q2")
    try:
        r.start(clip("1920x1080"))
        r.settle(1.0)
        for ext, comp in (("png", 0), ("png", 1), ("jpg", 0)):
            say("q2 time of a video screenshot of a 1920x1080 clip, %s compression %d: %s" % (ext, comp, r.timed_shot("video", ext, comp, 3)))
        a = time.monotonic()
        img = r.shot("video")
        t_shot = time.monotonic() - a
        say("q2 the still: %dx%d bpp %d filtered=%s file %d bytes, screenshot and read back in %.0f ms" % (img.w, img.h, img.bpp, img.filtered, img.bytes, t_shot * 1000))
        a = time.monotonic()
        rgb = img.rgb()
        say("q2 rows joined to RGB bytes in %.1f ms (%d bytes)" % ((time.monotonic() - a) * 1000, len(rgb)))
        live = r.shot("window")
        ref = [live.px(x, y) for x in (WW // 5, WW // 2, 4 * WW // 5) for y in (WH // 5, WH // 2, 4 * WH // 5)]
        for k, fmt in ((1, "rgba8"), (2, "rgba8"), (3, "rgba8"), (3, "rgb8"), (2, "rgb8")):
            try:
                a = time.monotonic()
                w, h, body = rgb_to(rgb, img.w, img.h, k, alpha=(fmt == "rgba8"))
                t_scale = time.monotonic() - a
                a = time.monotonic()
                hexdata = body.hex()
                t_hex = time.monotonic() - a
                a = time.monotonic()
                path = r.write(blend(w, h, fmt, hexdata, 0.0, "s%d%s" % (k, fmt)))
                t_write = time.monotonic() - a
                c0 = r.counters()
                a = time.monotonic()
                r.shaders([path])
                t_set = time.monotonic() - a
                t_drawn = r.drawn("spike blend s%d%s" % (k, fmt), 20.0)
                got = r.shot("window")
                diff = max(max(abs(i - j) for i, j in zip(got.px(x, y), want))
                           for (x, y), want in zip([(x, y) for x in (WW // 5, WW // 2, 4 * WW // 5) for y in (WH // 5, WH // 2, 4 * WH // 5)], ref))
                a = time.monotonic()
                r.shaders([])
                t_off = time.monotonic() - a
                say("q2 %dx%d %s: pick pixels %.1f ms, hex %.1f ms, write %.1f ms (%d bytes of text), set %.1f ms, drawn after %s, "
                    "the still alone differs from the live picture by at most %d of 255, taking it off %.1f ms, counters %s -> %s"
                    % (w, h, fmt, t_scale * 1000, t_hex * 1000, t_write * 1000, os.path.getsize(path), t_set * 1000,
                       "%.0f ms" % (t_drawn * 1000) if t_drawn is not None else "NEVER", diff, t_off * 1000, c0, r.counters()))
                r.settle(0.3)
            except Exception:
                say("q2 size /%d %s failed:\n%s" % (k, fmt, traceback.format_exc()))
        # progress by a new text at every step: what a step costs
        for k in (3, 2):
            try:
                w, h, body = rgb_to(rgb, img.w, img.h, k)
                hexdata = body.hex()
                c0 = r.counters()
                took = []
                began = time.monotonic()
                for i in range(1, 11):
                    a = time.monotonic()
                    r.shaders([r.write(blend(w, h, "rgba8", hexdata, i / 10.0, "t%d-%d-" % (k, i)))])
                    d = r.drawn("spike blend t%d-%d-" % (k, i), 10.0)
                    took.append((time.monotonic() - a) * 1000 if d is not None else -1)
                say("q2 ten texts in a row with a %dx%d still, each waited for: per step %s ms, all in %.2f s, counters %s -> %s"
                    % (w, h, " ".join("%.0f" % t for t in took), time.monotonic() - began, c0, r.counters()))
                r.shaders([])
                r.settle(0.3)
            except Exception:
                say("q2 steps /%d failed:\n%s" % (k, traceback.format_exc()))
        r.loglines(("spike blend", "PVJSTILL", "exture"), 12)
    finally:
        r.close()


def q4_stages():
    """Where a blend can hook: against brightness (Blackout), against a shader at OUTPUT (the mapper), over a generator."""
    r = Rig("q4")
    try:
        r.start(clip())
        red = (bytes([255, 0, 0, 255]) * 64).hex()
        mp = r.write(MAP)

        def px(label):
            img = r.shot("window")
            say("q4 %-58s left %s right %s" % (label, img.px(img.w // 4, img.h // 4), img.px(3 * img.w // 4, img.h // 4)))

        px("plain (about (99,76,160) left, (57,76,98) right)")
        for stage in ("MAINPRESUB", "MAIN", "LINEAR", "SCALED", "OUTPUT"):
            try:
                path = r.write(blend(8, 8, "rgba8", red, 0.5, stage, stage))
                r.shaders([path]); r.settle()
                d = r.drawn("spike blend %s" % stage, 3.0)
                px("half red at %s (drawn: %s)" % (stage, d is not None))
                r.req("set_property", "brightness", -100); r.settle()
                px("half red at %s, brightness -100" % stage)
                r.req("set_property", "brightness", 0)
                r.shaders([path, mp]); r.settle()
                px("half red at %s, then mirror at OUTPUT" % stage)
                r.shaders([mp, path]); r.settle()
                px("mirror at OUTPUT listed first, then half red at %s" % stage)
            except Exception:
                say("q4 %s failed:\n%s" % (stage, traceback.format_exc()))
        r.shaders([]); r.settle()
        r.load(clip("320x180", "rgb24", 25, ""))
        for label, files in (("rgb clip, half red at NATIVE", [blend(8, 8, "rgba8", red, 0.5, "N", "NATIVE")]),
                             ("rgb clip, a generator at NATIVE then half red at NATIVE", [GEN, blend(8, 8, "rgba8", red, 0.5, "N2", "NATIVE")]),
                             ("rgb clip, a generator at NATIVE then half red at MAIN", [GEN, blend(8, 8, "rgba8", red, 0.5, "N3", "MAIN")])):
            try:
                r.shaders([r.write(t) for t in files]); r.settle()
                px(label)
                r.req("set_property", "brightness", -100); r.settle()
                px(label + ", brightness -100")
                r.req("set_property", "brightness", 0)
            except Exception:
                say("q4 %s failed:\n%s" % (label, traceback.format_exc()))
        # a still of another shape than the clip under it
        r.shaders([]); r.load(clip("90x160"))
        r.req("set_property", "keepaspect", False); r.req("set_property", "keepaspect", True); r.settle(0.5)
        r.shaders([r.write(blend(8, 8, "rgba8", red, 0.0, "tall", "MAIN"))]); r.settle()
        img = r.shot("window")
        say("q4 a red still over a tall clip (90x160), still alone: far left %s, middle %s, far right %s" % (img.px(20, WH // 2), img.px(WW // 2, WH // 2), img.px(WW - 20, WH // 2)))
    finally:
        r.close()


def rm(r, oid):
    try:
        r.req("overlay-remove", oid)
    except Exception as e:
        say("ov overlay-remove %d failed: %s" % (oid, e))


def overlay():
    """The other candidate: the still as the player's overlay, its alpha changed step by step."""
    r = Rig("ov")
    try:
        r.start(clip())
        for comp in (0, 1):
            say("ov a window screenshot %dx%d, png compression %d: %s" % (WW, WH, comp, r.timed_shot("window", "png", comp, 5)))
        a = time.monotonic()
        img = r.shot("window")
        t_shot = time.monotonic() - a
        a = time.monotonic()
        rgb = img.rgb()
        t_rgb = time.monotonic() - a
        a = time.monotonic()
        w, h, full = rgb_to(rgb, img.w, img.h, 1, order=(2, 1, 0))
        t_full = time.monotonic() - a
        a = time.monotonic()
        hw, hh, half = rgb_to(rgb, img.w, img.h, 2, order=(2, 1, 0))
        t_half = time.monotonic() - a
        say("ov the still: screenshot and decode %.0f ms, rows %.1f ms, to BGRA at %dx%d %.1f ms, at %dx%d %.1f ms" % (t_shot * 1000, t_rgb * 1000, w, h, t_full * 1000, hw, hh, t_half * 1000))
        s_px = img.px(WW // 2, WH // 2)
        r.load(FLAT_B)
        b_px = r.shot("window").px(WW // 2, WH // 2)
        say("ov the still's middle", s_px, "the new clip's middle", b_px)
        path = r.write(half, binary=True)
        scaled = True
        try:
            r.req("overlay-add", 63, 0, 0, path, 0, "bgra", hw, hh, hw * 4, WW, WH)
        except Exception as e:
            scaled = False
            say("ov overlay-add with a display size (dw, dh) refused:", e)
        r.settle(0.1)
        got = r.shot("window")
        say("ov a %dx%d still drawn at %dx%d (scaled=%s): middle %s, a quarter %s (the still had %s), size of the drawn still judged at the far corner %s"
            % (hw, hh, WW, WH, scaled, got.px(WW // 2, WH // 2), got.px(WW // 4, WH // 4), img.px(WW // 4, WH // 4), got.px(WW - 8, WH - 8)))
        tables = [bytes((v * k + 127) // 255 for v in range(256)) for k in range(256)]
        for alpha in (0.5, 0.25):
            p2 = r.write(half.translate(tables[int(round(alpha * 255))]), binary=True)
            r.req(*(["overlay-add", 63, 0, 0, p2, 0, "bgra", hw, hh, hw * 4] + ([WW, WH] if scaled else [])))
            r.settle(0.1)
            got = r.shot("window").px(WW // 2, WH // 2)
            want = tuple(int(round(s * alpha + b * (1 - alpha))) for s, b in zip(s_px, b_px))
            say("ov alpha %.2f: middle %s, a plain mix would be %s" % (alpha, got, want))
        # which overlay is on top
        box = r.write(bytes([0, 0, 255, 255]) * (200 * 200), binary=True)
        for low, high in ((5, 63), (63, 5), (0, 10), (10, 0)):
            for i in (0, 5, 10, 63):
                rm(r, i)
            r.req(*(["overlay-add", low, 0, 0, path, 0, "bgra", hw, hh, hw * 4] + ([WW, WH] if scaled else [])))
            r.req("overlay-add", high, WW // 2 - 100, WH // 2 - 100, box, 0, "bgra", 200, 200, 800)
            r.settle(0.1)
            say("ov the still as overlay %d and a red box as overlay %d, added in that order: middle %s (red means the box is on top)" % (low, high, r.shot("window").px(WW // 2, WH // 2)))
        for i in (0, 5, 10, 63):
            rm(r, i)
        # the cost of a step at four sizes of the still
        r.load(clip("1280x720"))
        r.settle(0.5)
        for sw, sh in ((640, 360), (960, 540), (1280, 720), (1920, 1080)):
            try:
                buf = (full * 3)[:sw * sh * 4]
                target = os.path.join(r.tmp, "step.bgra")
                c0 = r.counters()
                parts = [[], [], []]
                began = time.monotonic()
                steps = 25
                for i in range(steps):
                    a = time.monotonic()
                    data = buf.translate(tables[255 - (255 * (i + 1)) // steps])
                    b = time.monotonic()
                    with open(target, "wb") as f:
                        f.write(data)
                    c = time.monotonic()
                    r.req(*(["overlay-add", 63, 0, 0, target, 0, "bgra", sw, sh, sw * 4] + ([WW, WH] if scaled else [])))
                    d = time.monotonic()
                    for lst, v in zip(parts, (b - a, c - b, d - c)):
                        lst.append(v * 1000)
                total = time.monotonic() - began
                say("ov 25 steps of a %dx%d still (%.1f MB) over a playing 1280x720 clip: all in %.2f s; per step the bytes %.1f ms (max %.1f), the file %.1f ms (max %.1f), "
                    "overlay-add %.1f ms (max %.1f); counters %s -> %s"
                    % (sw, sh, sw * sh * 4 / 1e6, total, sum(parts[0]) / steps, max(parts[0]), sum(parts[1]) / steps, max(parts[1]), sum(parts[2]) / steps, max(parts[2]),
                       c0, r.counters()))
                a = time.monotonic()
                r.req("overlay-remove", 63)
                say("ov overlay-remove %.1f ms" % ((time.monotonic() - a) * 1000))
            except Exception:
                say("ov steps at %dx%d failed:\n%s" % (sw, sh, traceback.format_exc()))
        # does a step show while the clip is frozen, over a still picture, and with nothing playing
        r.load(FLAT_B)
        r.req("set_property", "pause", True); r.settle(0.3)
        r.req(*(["overlay-add", 63, 0, 0, path, 0, "bgra", hw, hh, hw * 4] + ([WW, WH] if scaled else [])))
        r.settle(0.15)
        say("ov frozen clip, the still added: middle", r.shot("window").px(WW // 2, WH // 2))
        r.req("overlay-remove", 63); r.settle(0.15)
        say("ov frozen clip, the still removed: middle", r.shot("window").px(WW // 2, WH // 2))
        r.req("set_property", "pause", False)
        still = os.path.join(r.tmp, "still.png")
        write_png(still, 320, 180, (90, 120, 60))
        r.p.play([still], windowed=True); r.settle(0.6)
        say("ov a still picture plays: middle", r.shot("window").px(WW // 2, WH // 2))
        r.req(*(["overlay-add", 63, 0, 0, path, 0, "bgra", hw, hh, hw * 4] + ([WW, WH] if scaled else [])))
        r.settle(0.15)
        say("ov still picture, the overlay added: middle", r.shot("window").px(WW // 2, WH // 2))
        try:
            v = r.shot("video")
            say("ov a video-mode screenshot with the overlay on: %dx%d middle %s" % (v.w, v.h, v.px(v.w // 2, v.h // 2)))
        except Exception as e:
            say("ov video-mode screenshot failed:", e)
        r.req("stop"); r.settle(0.4)
        try:
            say("ov nothing playing, the overlay still added: middle", r.shot("window").px(WW // 2, WH // 2))
        except Exception as e:
            say("ov a window screenshot with nothing playing failed:", e)
        r.req("overlay-remove", 63); r.settle(0.2)
        try:
            say("ov nothing playing, the overlay removed: middle", r.shot("window").px(WW // 2, WH // 2))
        except Exception as e:
            say("ov a window screenshot with nothing playing failed:", e)
        # a shader at OUTPUT (the mapper) and brightness against the overlay
        r.load(clip())
        mp = r.write(MAP)
        r.shaders([mp]); r.settle(0.3)
        m = r.shot("window")
        say("ov mirror at OUTPUT: window left %s right %s (plain is about (99,76,160) left, (57,76,98) right)" % (m.px(WW // 4, WH // 4), m.px(3 * WW // 4, WH // 4)))
        _, _, mb = rgb_to(m.rgb(), m.w, m.h, 2, order=(2, 1, 0))
        mpath = r.write(mb, binary=True)
        r.load(FLAT_B)
        r.req(*(["overlay-add", 63, 0, 0, mpath, 0, "bgra", hw, hh, hw * 4] + ([WW, WH] if scaled else [])))
        r.settle(0.15)
        m2 = r.shot("window")
        say("ov that screenshot as the overlay, mirror still on: left %s right %s (the same as the line before means it is not warped twice)" % (m2.px(WW // 4, WH // 4), m2.px(3 * WW // 4, WH // 4)))
        r.req("set_property", "brightness", -100); r.settle(0.2)
        say("ov brightness -100 with the overlay on: middle %s (not black means Blackout does not cover an overlay)" % (r.shot("window").px(WW // 2, WH // 2),))
        r.req("set_property", "brightness", 0)
    finally:
        r.close()


def main():
    if not shutil.which("mpv"):
        say("no mpv here")
        return
    for step in (versions, q3_frame, q5_gap, q1_stills, q4_stages, q2_texture, overlay):
        say("==== %s (ES %s) ====" % (step.__name__, ES))
        try:
            step()
        except Exception:
            say("%s broke:\n%s" % (step.__name__, traceback.format_exc()))
    say("==== done ====")


if __name__ == "__main__":
    main()
    sys.exit(0)
