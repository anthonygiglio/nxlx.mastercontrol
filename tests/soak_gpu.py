# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""TEMPORARY (the hunt for "no reply from mpv"; removed again before this branch is finished): run the GPU tests
that failed that way, and the ones that ask for most screenshots, again and again for some minutes, each batch in a
process of its own, on a quiet machine, a busy one (every processor kept spinning) or a cold one (the disk cache
emptied before every start of the player). Every failure is printed whole; at the end, what every request took.

Run under a display:  PVJ_GPU_ONLY=desktop xvfb-run -a python -m tests.soak_gpu --minutes 19
In CI it runs in place of tests.test_effects_gpu, on a push only, while the file tests/SOAK is there.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

LIVE = ["test_time_goes_on_across_a_change_of_a_value_and_of_the_speed", "test_the_picture_is_never_black_during_changes_and_they_are_coalesced",
        "test_this_mpv_has_no_live_parameters_for_a_user_shader", "test_every_input_type_changes_the_picture", "test_a_preset_brings_its_values_back"]
FX = ["test_a_capped_effect_draws_the_right_picture_for_every_shape_and_amount_0_is_the_clip_itself",
      "test_the_picture_is_never_dark_while_an_effect_goes_on_changes_and_comes_off"]
FX_SHORT = ["test_a_filter_the_gpu_refuses_is_reported_with_its_line_and_the_one_before_stays",
            "test_every_way_of_reading_the_picture_gives_the_same_pixel_for_every_size_and_shape"]


def phase(load, until, env, es, counts, spin=0):
    live = ["tests.test_shaderlive_gpu.%sLiveTest.%s" % (es, t) for t in LIVE]
    fx = ["tests.test_effects_gpu.%sEffectTest.%s" % (es, t) for t in FX]
    short = ["tests.test_effects_gpu.%sEffectTest.%s" % (es, t) for t in FX_SHORT]
    if load == "cold":                      # one start of the player per process, each from an empty disk cache
        batches = [[t] for t in live[:3] + short + live[3:]]
    elif load.startswith("old"):            # round 2: the generators' tests only, on the code path of 2026-10-05
        batches = [live]
    else:
        batches = [live, fx, live]
    spinners = []
    if load in ("busy", "old-heavy"):
        for _ in range(spin or (os.cpu_count() or 2) * (2 if load == "old-heavy" else 1)):
            spinners.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
    print("soak: %s, load %s, %d processors, load average %s, %d spinners" % (es, load, os.cpu_count(), os.getloadavg(), len(spinners)), flush=True)
    n = 0
    try:
        while time.monotonic() < until:
            names = batches[n % len(batches)]
            if load == "cold":
                subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null", shell=True)
                shutil.rmtree(os.path.expanduser("~/.cache/mesa_shader_cache"), True)
                shutil.rmtree(os.path.expanduser("~/.cache/mesa_shader_cache_db"), True)
            e = dict(env, PVJ_MPV_LOG="1" if n % 2 else "0")
            t = time.monotonic()
            try:
                if load.startswith("old"):
                    e["PVJ_SOAK_OLD_FBO"] = "1"
                p = subprocess.run([sys.executable, "-m", "tests.soak_gpu", "--child"] + names, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=420)
                text, code = p.stdout.decode("utf-8", "replace"), p.returncode
            except subprocess.TimeoutExpired as x:
                text, code = (x.stdout or b"").decode("utf-8", "replace") + "\nSOAK: the batch did not end in 7 minutes", 1
            took = time.monotonic() - t
            bad = code != 0 or "skipped=" in text or ("Ran %d test" % len(names)) not in text or "MPV-WATCH  ===" in text
            n += 1
            counts["batches"] += 1
            counts["tests"] += len(names)
            counts["failed"] += 1 if bad else 0
            print("soak: %s batch %d (%d tests, mpv log %s) %s in %.1f s, load average %.2f" % (load, n, len(names), e["PVJ_MPV_LOG"], "FAILED" if bad else "ok", took, os.getloadavg()[0]), flush=True)
            if bad:
                print(text, flush=True)
    finally:
        for s in spinners:
            s.kill()
        for s in spinners:
            s.wait()


def child(names):
    """One batch. With PVJ_SOAK_OLD_FBO=1 a generator's value change sets fbo-format again every time, as the
    player did until #92: the first lost screenshot (2026-10-05) was taken right after such a change."""
    import unittest
    if os.environ.get("PVJ_SOAK_OLD_FBO") == "1":
        from pvj.player import Player
        real = Player.swap_source

        def swap_source(self, shader, epoch):
            with self._lock:
                done = real(self, shader, epoch)
                if done:
                    self._apply_fbo()
                return done
        Player.swap_source = swap_source
    unittest.main(module=None, argv=["soak", "-v"] + names)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child(sys.argv[2:])
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=19.0, help="in all: a third quiet, a third busy, the rest cold")
    ap.add_argument("--spinners", type=int, default=0, help="busy: how many (default: one per processor)")
    args = ap.parse_args()
    es = "Gles" if os.environ.get("PVJ_GPU_ONLY") == "gles" else "DesktopGl"
    folder = tempfile.mkdtemp()
    os.environ.pop("PVJ_MPV_WATCH_OUT", None)
    counts = {"batches": 0, "tests": 0, "failed": 0}
    start = time.monotonic()
    subprocess.run("nproc; free -m; mpv --version | head -1; dpkg -s libgl1-mesa-dri | grep Version", shell=True)
    for load, share in (("none", 0.08), ("old", 0.45), ("old-heavy", 1.0)):
        out = os.path.join(folder, load + ".jsonl")
        phase(load, start + args.minutes * 60 * share, dict(os.environ, PVJ_GPU_TEST="1", PVJ_MPV_WATCH_OUT=out), es, counts, args.spinners)
        totals(out, "%s, load %s" % (es, load))
    print("soak: %s: %d batches (%d tests), %d failed" % (es, counts["batches"], counts["tests"], counts["failed"]), flush=True)
    return 1 if counts["failed"] else 0


def totals(out, what):
    """What every request took in one phase, over all its processes."""
    from tests import mpv_watch as M
    reports = 0
    try:
        with open(out) as f:
            for line in f:
                row = json.loads(line)
                reports += row["reports"]
                for kind, (count, bad, longest, buckets) in row["stats"].items():
                    have = M.STATS.setdefault(kind, [0, 0, 0.0, [0] * len(buckets)])
                    have[0] += count
                    have[1] += bad
                    have[2] = max(have[2], longest)
                    have[3] = [a + b for a, b in zip(have[3], buckets)]
                M.SLOWEST.extend(tuple(r) for r in row["slowest"])
    except OSError:
        pass
    M.SLOWEST.sort(key=lambda r: -r[0])
    del M.SLOWEST[12:]
    print("\n".join("SOAK-TOTAL  " + line for line in M.summary()), flush=True)
    M.STATS.clear()
    del M.SLOWEST[:]
    print("soak: %s: %d reports of a missing reply" % (what, reports), flush=True)


if __name__ == "__main__":
    sys.exit(main())
