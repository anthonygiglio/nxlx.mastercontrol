#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The lean-tier spike of design/text-and-logo-sources_2026-11-08.md section 3 (D129, slice 2).

The question: mpv's `osd-overlay` command takes ASS events, and libass can animate `\\move`, `\\fad` and `\\t`. Does mpv
advance the ASS clock for such an overlay (so scroll, crawl and true fades could go to a Pi 3 at no per-frame cost), or
does it draw the event at one fixed time?

How it asks: it starts mpv through libmpv's software renderer (no display and no GPU needed), plays a black test video,
puts up an ASS event that moves, fades or grows over two seconds, and takes a picture of the overlay now and again after
0.6, 1.2 and 2.4 seconds. If the pictures differ, the clock runs. It needs only Python 3 and libmpv.so.2 (the library the
`mpv` package already depends on):

    python3 tools/osd-ass-spike.py

Answer (8 November 2026, mpv 0.41.0 and its libass, on a PC, software renderer): the pictures are the same every time:
`\\move` stays at its first position, `\\fad` stays at its first strength (a fade in is clear at every time) and `\\t`
stays at its first size. So the OSD draws an overlay's ASS event at time 0 and the clock does not run. The table of the
design stands (scroll and crawl are refused on the lean tier). What this cannot say, and a Pi 3 must: whether the mpv
that Raspberry Pi OS ships (0.35 to 0.37) differs. Run this program on the Pi 3 (row GT14 of tools/DEVICE-TESTING.md)
and write down the mpv version and the last line.
"""
import ctypes
import ctypes.util
import sys
import time

lib = ctypes.CDLL(ctypes.util.find_library("mpv") or "libmpv.so.2")
lib.mpv_create.restype = ctypes.c_void_p
lib.mpv_wait_event.restype = ctypes.c_void_p
lib.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
lib.mpv_error_string.restype = ctypes.c_char_p
lib.mpv_get_property_string.restype = ctypes.c_char_p
W, H = 640, 360


class Param(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


h = ctypes.c_void_p(lib.mpv_create())
for k, v in (("vo", "libmpv"), ("ao", "null"), ("terminal", "no"), ("config", "no"), ("idle", "yes"), ("keep-open", "yes"), ("msg-level", "all=no")):
    lib.mpv_set_option_string(h, k.encode(), v.encode())
if lib.mpv_initialize(h) != 0:
    sys.exit("mpv did not start")
version = lib.mpv_get_property_string(h, b"mpv-version")
print("mpv:", version.decode() if version else "unknown")
ctx = ctypes.c_void_p()
api = ctypes.c_char_p(b"sw")
if lib.mpv_render_context_create(ctypes.byref(ctx), h, (Param * 2)(Param(1, ctypes.cast(api, ctypes.c_void_p)), Param(0, None))) != 0:
    sys.exit("this libmpv has no software renderer")


def cmd(*args):
    arr = (ctypes.c_char_p * (len(args) + 1))(*[a.encode() for a in args], None)
    r = lib.mpv_command(h, arr)
    if r < 0:
        raise RuntimeError("%s: %s" % (args[0], lib.mpv_error_string(r).decode()))


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        lib.mpv_wait_event(h, 0.01)


def frame():
    stride = ctypes.c_size_t(W * 4)
    size = (ctypes.c_int * 2)(W, H)
    buf = ctypes.create_string_buffer(W * H * 4)
    fmt = ctypes.c_char_p(b"rgb0")
    ps = (Param * 5)(Param(17, ctypes.cast(size, ctypes.c_void_p)), Param(18, ctypes.cast(fmt, ctypes.c_void_p)), Param(19, ctypes.cast(ctypes.byref(stride), ctypes.c_void_p)),
                     Param(20, ctypes.cast(buf, ctypes.c_void_p)), Param(0, None))
    if lib.mpv_render_context_render(ctx, ps) != 0:
        sys.exit("the software renderer did not draw")
    return buf.raw


def ink(data):
    return sum(data[0::16]) + sum(data[1::16]) + sum(data[2::16])


def box(data):
    xs = [x for y in range(0, H, 2) for x in range(0, W, 2) if sum(data[(y * W + x) * 4:(y * W + x) * 4 + 3]) > 60]
    return (min(xs), max(xs)) if xs else None


def show(text, label, seconds=(0.0, 0.6, 1.2, 2.4)):
    cmd("osd-overlay", "1", "ass-events", text, str(W), str(H))
    began, rows = time.time(), []
    for s in seconds:
        pump(max(0, s - (time.time() - began)))
        data = frame()
        rows.append((round(time.time() - began, 2), box(data), ink(data)))
    cmd("osd-overlay", "1", "none", "")
    print(label)
    for r in rows:
        print("   t=%5.2f s  left and right edge of the ink %s  amount of ink %d" % r)
    return rows


cmd("loadfile", "av://lavfi:color=c=black:s=640x360:r=25")
pump(1.5)
frame()
move = show("{\\an7\\pos(0,0)\\move(0,100,500,100,0,2000)\\fs40}MOVING", "\\move: from x 0 to x 500 over 2 s")
fade = show("{\\an7\\pos(100,100)\\fs60\\fad(0,2000)}FADING", "\\fad(0,2000): full at first, gone at 2 s if the clock runs")
show("{\\an7\\pos(100,100)\\fs60\\fad(2000,0)}FADING IN", "\\fad(2000,0): clear at first, full at 2 s if the clock runs")
show("{\\an7\\pos(100,100)\\fs20\\t(0,2000,\\fs80)}GROWING", "\\t: the font grows from 20 to 80 over 2 s if the clock runs")
moves = len({r[1] for r in move}) > 1
fades = len({r[2] for r in fade}) > 1
print()
print("ASS time advances in osd-overlay: \\move %s, \\fad %s" % ("YES" if moves else "NO", "YES" if fades else "NO"))
