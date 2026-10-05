# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A diagnosis, not a test (temporary): screenshots of a playing clip are black for seconds at a time on the CI rig,
with and without an effect. This watches a clip, an effect going on and coming off, and prints what the player says
at every look, and its own log around the first black picture. Always exits 0."""
import os
import shutil
import sys
import tempfile
import time

from pvj.player import Player
from tests.test_effects_gpu import CLIP, KINDS, SHAPES, quick_rows

W, H = 320, 180
HOOK = ("//!HOOK NATIVE\n//!BIND HOOKED\n//!BIND LUMA\n//!DESC diag %d\nvec4 hook() { vec4 t = HOOKED_tex(HOOKED_pos); return vec4(t.r, 1.0 - t.g, t.b, t.a); }\n"
        "//!HOOK NATIVE\n//!BIND HOOKED\n//!BIND RGB\n//!DESC diag %d\nvec4 hook() { vec4 t = HOOKED_tex(HOOKED_pos); return vec4(t.r, 1.0 - t.g, t.b, t.a); }\n")


class Rig:
    def __init__(self, es, extra=()):
        self.tmp = tempfile.mkdtemp(prefix="pvj-diag-")
        self.log = os.path.join(self.tmp, "mpv.log")
        self.p = Player(extra_args=["--vo=gpu", "--gpu-context=x11egl", "--opengl-es=" + es, "--ao=null", "--geometry=%dx%d" % (W, H), "--no-border",
                                    "--log-file=" + self.log] + list(extra), rundir=self.tmp)
        self.n, self.seen, self.t0, self.black = 0, 0, time.monotonic(), 0

    def get(self, name):
        try:
            return self.p.ipc.request("get_property", name)
        except Exception as e:
            return "?(%s)" % str(e)[:30]

    def look(self, what):
        path = os.path.join(self.tmp, "shot.png")
        try:
            self.p.ipc.request("screenshot-to-file", path, "window")
            w, h, rows = quick_rows(path)
        except Exception as e:
            print("DIAG %6.2f %-34s NO SCREENSHOT %s" % (time.monotonic() - self.t0, what, e), flush=True)
            return None
        pts = [rows[y][x] for y in range(4, H, 9) for x in range(4, W, 9)]
        lit, colours = max(max(p) for p in pts), len(set(pts))
        passes = self.get("vo-passes")
        fresh = [str(x.get("desc"))[:28] for x in ((passes or {}).get("fresh") or [])] if isinstance(passes, dict) else passes
        print("DIAG %6.2f %-34s lit %3d colours %4d centre %-15s pos %s drops %s/%s idle %s pause %s shaders %d fbo %s passes %s" % (
            time.monotonic() - self.t0, what, lit, colours, rows[H // 2][W // 2], self.get("time-pos"), self.get("frame-drop-count"),
            self.get("decoder-frame-drop-count"), self.get("core-idle"), self.get("pause"), len(self.get("glsl-shaders") or []), self.get("fbo-format"), fresh), flush=True)
        if colours <= 3:
            self.black += 1
            if self.black <= 3:
                self.tail(what)
        return colours

    def tail(self, what):
        try:
            with open(self.log, errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            return
        for line in lines[max(self.seen, len(lines) - 60):]:
            print("DIAG   log | %s" % line[:220])
        self.seen = len(lines)

    def mark(self):
        try:
            with open(self.log, errors="replace") as f:
                self.seen = f.read().count("\n")
        except OSError:
            pass

    def watch(self, what, seconds, every=0.35):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.look(what)
            time.sleep(every)

    def hook(self):
        self.n += 1
        path = os.path.join(self.tmp, "diag-%d.glsl" % self.n)
        with open(path, "w") as f:
            f.write(HOOK % (self.n, self.n))
        return path


def run(es, extra, label):
    r = Rig(es, extra)
    print("DIAG ==== %s, ES %s, extra %s" % (label, es, list(extra)), flush=True)
    try:
        clips = [("first", "av://lavfi:testsrc=size=160x90:rate=25"), ("bt709", CLIP), ("tall", dict(SHAPES)["tall"]), ("bt601", dict(KINDS)["bt.601"]),
                 ("bt709 again", CLIP), ("rgb", dict(KINDS)["rgb"]), ("square", dict(SHAPES)["square"]), ("bt709 third", CLIP)]
        for name, url in clips:
            r.mark()
            r.p.play([url], windowed=True)
            r.p.ipc.request("set_property", "screenshot-format", "png")
            r.p.ipc.request("set_property", "screenshot-high-bit-depth", False)
            r.p.ipc.request("set_property", "screenshot-png-filter", 0)
            r.p.ipc.request("set_property", "screenshot-png-compression", 1)
            r.watch("%s: just played" % name, 2.5)
            r.mark()
            r.p.put_effect(r.hook())
            r.watch("%s: effect on" % name, 2.5)
            r.mark()
            r.p.put_effect(r.hook())
            r.watch("%s: another effect" % name, 1.5)
            r.mark()
            r.p.ipc.request("set_property", "keepaspect", False)
            r.watch("%s: stretched" % name, 1.2)
            r.p.ipc.request("set_property", "keepaspect", True)
            r.mark()
            r.p.clear_effect()
            r.watch("%s: effect off" % name, 1.5)
        print("DIAG ==== %s: %d black looks" % (label, r.black), flush=True)
    finally:
        r.p.stop()
        shutil.rmtree(r.tmp, ignore_errors=True)


if __name__ == "__main__":
    es = sys.argv[1] if len(sys.argv) > 1 else "yes"
    for label, extra in (("as the tests run it", ()), ("frames never dropped", ("--framedrop=no",)), ("untimed", ("--untimed",)),
                         ("display-resample", ("--video-sync=display-resample",))):
        try:
            run(es, extra, label)
        except Exception as e:
            print("DIAG %s FAILED %r" % (label, e), flush=True)
    sys.exit(0)
