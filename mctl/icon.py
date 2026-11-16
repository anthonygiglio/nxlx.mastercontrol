# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel's icon, drawn here so that no picture file has to be shipped.

A phone asks the box for /favicon.ico, /apple-touch-icon.png and /apple-touch-icon-precomposed.png when someone
bookmarks the panel or puts it on the home screen; the box answered 404 (seen in the journal of the real box), and
the bookmark got a blank tile. This draws one plain picture with the standard library: a dark square with a play
triangle in the accent colour. It is a PNG at all three addresses (browsers take a PNG as a favicon), about 1 KB.

It is a stand-in until the project has a mark of its own: replace `png()` or the three routes in server.py then.
"""
import struct
import zlib

SIZE = 180                      # what an iPhone asks for; smaller uses scale it down
BACK = (17, 17, 20)
MARK = (255, 122, 48)
PATHS = ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
TYPE = "image/png"

_cache = {}


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(size=SIZE):
    """The icon as PNG bytes (RGB, no transparency: iOS paints a transparent home-screen icon black)."""
    if size in _cache:
        return _cache[size]
    if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 512:
        raise ValueError("size must be 16 to 512")
    left, right = 0.36 * size, 0.70 * size          # the triangle: its flat side at `left`, its point at `right`
    top, bottom = 0.28 * size, 0.72 * size
    middle = (top + bottom) / 2.0
    back, mark = bytes(BACK), bytes(MARK)
    rows = []
    for y in range(size):
        cy = y + 0.5
        row = bytearray(b"\x00")                    # filter type 0 for this line
        # how far to the right the triangle reaches on this line (0: not at all)
        reach = 0.0
        if top <= cy <= bottom:
            reach = left + (right - left) * (1.0 - abs(cy - middle) / (middle - top))
        for x in range(size):
            row += mark if left <= x + 0.5 <= reach else back
        rows.append(bytes(row))
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b""))
    _cache[size] = data
    return data
