#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A stand-in for pvj-raster for the browser test (tests/ui/harness.py puts it in PVJ_RASTER_EXEC).

The browser test judges the panel, not the picture helper (tests/test_raster.py runs the real program against real
fonts), and CI's browser job has no compiler for it. So this answers the same requests with plain coloured blocks of
the right shape: a PNG is stored as it is (it reads the size from the header and checks nothing else), a thumbnail is
that PNG, and a text or a logo is a block whose size follows the request (a text: its longest line and the number of
lines; a logo: the width asked for), as raw premultiplied B G R A or as a PNG in a canvas. Run with PVJ_HARNESS_REAL_RASTER=1
to use the real program (make -C render pvj-raster) instead."""
import struct
import sys
import zlib


def out(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    sys.exit(0)


def refuse(words):
    out(b"ERR " + words.encode() + b"\n")


def main():
    head = sys.stdin.buffer.readline().decode("latin-1").split()
    if len(head) < 3 or head[0] != "RASTER1":
        refuse("the request is not understood")
    if head[1] in ("IMG", "THUMB"):
        n = int(head[-1])
        data = sys.stdin.buffer.read(n)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            refuse("this stand-in reads PNG files only")
        w, h = struct.unpack(">II", data[16:24])
        out(b"OK %d %d 1 %d\n" % (w, h, len(data)) + data)
    if head[1] != "GFX":
        refuse("the request is not understood")
    plen, paylen = int(head[2]), int(head[3])
    params = sys.stdin.buffer.read(plen).decode("utf-8", "replace")
    sys.stdin.buffer.read(paylen)
    top, _, text = params.partition("\nTEXT\n")
    kv = {}
    for line in top.split("\n"):
        k, _, v = line.partition(" ")
        kv[k] = v
    limit = [int(x) for x in kv.get("limit", "1920 1080").split()]
    if kv.get("kind") == "logo":
        w = max(1, min(int(float(kv.get("width", "100"))), limit[0]))
        h = max(1, w // 2)
        colour = (255, 90, 54, 255)
    else:
        if not text.strip():
            refuse("there is no text to draw, or it is too long")
        lines = text.split("\n")
        size = float(kv.get("size", "60"))
        w = max(1, min(int(size * 0.6 * max(len(l) for l in lines)) + 8, limit[0]))
        h = max(1, min(int(size * 1.2 * len(lines)), limit[1]))
        colour = (255, 255, 255, 255)
    if kv.get("out") == "png":
        cw, ch, cx, cy = [int(float(x)) for x in kv["canvas"].split()] if "canvas" in kv else (w, h, 0, 0)
        # one row of the canvas with the block in it, repeated; clear elsewhere
        clear, block = bytes(4), bytes(colour)
        raw = b""
        for y in range(ch):
            row = bytearray(clear * cw)
            if cy <= y < cy + h:
                for x in range(max(0, cx), min(cw, cx + w)):
                    row[x * 4:x * 4 + 4] = block
            raw += b"\x00" + bytes(row)

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cw, ch, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
        out(b"OKPNG %d %d 0 %d\n" % (cw, ch, len(data)) + data)
    out(b"OKRAW %d %d 0 %d\n" % (w, h, w * h * 4) + bytes((colour[2], colour[1], colour[0], 255)) * (w * h))


main()
