# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A diagnosis, not a test (temporary): in the effect tests the first screenshot of a clip is black until a shader is
put on. This plays clips in the tests' own order and prints what the player says at every look, what brings the
picture back, and the player's own log around the first black look. Always exits 0."""
import os
import shutil
import sys
import tempfile
import time

from pvj.player import Player
from tests.test_effects_gpu import CLIP, KINDS, quick_rows

W, H = 320, 180
HOOK = ("//!HOOK NATIVE\n//!BIND HOOKED\n//!BIND LUMA\n//!DESC diag %d\nvec4 hook() { vec4 t = HOOKED_tex(HOOKED_pos); return vec4(t.r, 1.0 - t.g, t.b, t.a); }\n"
        "//!HOOK NATIVE\n//!BIND HOOKED\n//!BIND RGB\n//!DESC diag %d\nvec4 hook() { vec4 t = HOOKED_tex(HOOKED_pos); return vec4(t.r, 1.0 - t.g, t.b, t.a); }\n")
FIRST = "av://lavfi:testsrc=size=160x90:rate=25"


class Rig:
    def __init__(self, es, extra=()):
        self.tmp = tempfile.mkdtemp(prefix="pvj-diag-")
        self.log = os.path.join(self.tmp, "mpv.log")
        self.p = Player(extra_args=["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + es, "--ao=null", "--geometry=%dx%d" % (W, H), "--no-border",
                                    "--log-file=" + self.log] + list(extra), rundir=self.tmp)
        self.n, self.seen, self.t0, self.tails = 0, 0, time.monotonic(), 0

    def get(self, name):
        try:
            return self.p.ipc.request("get_property", name)
        except Exception as e:
            return "?(%s)" % str(e)[:30]

    def look(self, what, mode="window"):
        path = os.path.join(self.tmp, "shot.png")
        try:
            self.p.ipc.request("screenshot-to-file", path, mode)
            w, h, rows = quick_rows(path)
        except Exception as e:
            print("DIAG %6.2f %-40s NO SCREENSHOT %s" % (time.monotonic() - self.t0, what, e), flush=True)
            return None
        pts = [rows[y][x] for y in range(2, h, 7) for x in range(2, w, 7)]
        lit, colours = max(max(p) for p in pts), len(set(pts))
        passes = self.get("vo-passes")
        fresh = [str(x.get("desc"))[:22] for x in ((passes or {}).get("fresh") or [])] if isinstance(passes, dict) else passes
        dims = self.get("osd-dimensions")
        dims = {k: dims.get(k) for k in ("w", "h", "ml", "mr", "mt", "mb")} if isinstance(dims, dict) else dims
        print("DIAG %6.2f %-40s %dx%d lit %3d colours %4d centre %-15s pos %s drops %s/%s shaders %d fbo %s dims %s passes %s" % (
            time.monotonic() - self.t0, what, w, h, lit, colours, rows[h // 2][w // 2], self.get("time-pos"), self.get("frame-drop-count"),
            self.get("decoder-frame-drop-count"), len(self.get("glsl-shaders") or []), self.get("fbo-format"), dims, fresh), flush=True)
        return colours

    def tail(self, lines=70):
        try:
            with open(self.log, errors="replace") as f:
                all_lines = f.read().split("\n")
        except OSError:
            return
        for line in all_lines[max(self.seen, len(all_lines) - lines):]:
            if "ipc_" not in line and "Client" not in line:
                print("DIAG   log | %s" % line[:230])
        self.seen = len(all_lines)

    def mark(self):
        try:
            with open(self.log, errors="replace") as f:
                self.seen = f.read().count("\n")
        except OSError:
            pass

    def play(self, url):
        self.mark()
        self.p.play([url], windowed=True)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            t = self.get("time-pos")
            if isinstance(t, (int, float)) and t > 0.15:
                break
            time.sleep(0.05)
        for k, v in (("screenshot-format", "png"), ("screenshot-high-bit-depth", False), ("screenshot-png-filter", 0), ("screenshot-png-compression", 1)):
            self.p.ipc.request("set_property", k, v)

    def watch(self, what, looks=8, every=0.2):
        """Look a few times; if the last look is black, say what the player's log holds and try what brings it back."""
        last = None
        for _ in range(looks):
            last = self.look(what)
            time.sleep(every)
        if last is not None and last <= 3:
            if self.tails < 4:
                self.tails += 1
                self.tail()
            self.look(what + " [the video only]", "video")
            for name, value in (("video-zoom", 0), ("sub-visibility", True)):
                self.p.ipc.request("set_property", name, value)
                time.sleep(0.3)
                self.look(what + " [after %s=%s]" % (name, value))
            self.p.ipc.request("set_property", "keepaspect", False)
            self.p.ipc.request("set_property", "keepaspect", True)
            time.sleep(0.3)
            self.look(what + " [after keepaspect]")
            self.p.ipc.request("set_property", "glsl-shaders", self.get("glsl-shaders") or [])
            time.sleep(0.3)
            self.look(what + " [the same shader list again]")
            self.p.ipc.request("seek", 0, "relative")
            time.sleep(0.4)
            self.look(what + " [after a seek of 0]")
            self.p.put_effect(self.hook())
            time.sleep(0.4)
            self.look(what + " [after a shader was put on]")
            self.p.clear_effect()
            time.sleep(0.4)
            self.look(what + " [and taken off again]")
        return last

    def hook(self):
        self.n += 1
        path = os.path.join(self.tmp, "diag-%d.glsl" % self.n)
        with open(path, "w") as f:
            f.write(HOOK % (self.n, self.n))
        return path


def run(es, label, steps, extra=()):
    r = Rig(es, extra)
    print("DIAG ==== %s, ES %s %s" % (label, es, list(extra)), flush=True)
    try:
        for name, url in steps:
            if url == "effect":
                r.p.put_effect(r.hook())
            elif url == "off":
                r.p.clear_effect()
            else:
                r.play(url)
            r.watch("%s: %s" % (label, name))
    except Exception as e:
        print("DIAG %s FAILED %r" % (label, e), flush=True)
    finally:
        r.p.stop()
        shutil.rmtree(r.tmp, ignore_errors=True)


if __name__ == "__main__":
    es = sys.argv[1] if len(sys.argv) > 1 else "yes"
    kinds = dict(KINDS)
    run(es, "as a test starts", [("the first clip", FIRST), ("then the picture", CLIP), ("the picture again", CLIP), ("10 bit", kinds["10 bit"]),
                                 ("bt.601", kinds["bt.601"]), ("effect on", "effect"), ("effect off", "off"), ("10 bit after an effect", kinds["10 bit"])])
    run(es, "the picture first", [("the picture", CLIP), ("the picture again", CLIP), ("10 bit", kinds["10 bit"])])
    run(es, "no cache of shaders", [("the first clip", FIRST), ("then the picture", CLIP), ("10 bit", kinds["10 bit"])], ("--gpu-shader-cache=no",))
    run(es, "plain scaling", [("the first clip", FIRST), ("then the picture", CLIP), ("10 bit", kinds["10 bit"])], ("--scale=bilinear", "--cscale=bilinear", "--dither=no"))
    sys.exit(0)
