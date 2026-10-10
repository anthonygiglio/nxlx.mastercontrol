# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel's icon, drawn here: one drawing for every size and every use.

A phone asks the box for /favicon.ico, /apple-touch-icon.png and /apple-touch-icon-precomposed.png when someone
bookmarks the panel or puts it on the home screen; the box answered 404 (seen in the journal of the real box), and
the bookmark got a blank tile. This draws one plain picture with the standard library: a dark square with a play
triangle in the accent colour. Those three addresses are drawn when asked for (a PNG at each: browsers take a PNG as
a favicon), about 1 KB.

The panel saved as an app (D76, docs/WEBAPP.md) needs larger ones, named by the manifest: 192 and 512 px, a 512 px
"maskable" one (a launcher cuts it to its own shape, so the mark is drawn smaller, well inside the middle), and an
outline with no colours of its own. Those are files in pvj/web, written from this drawing by tools/make-icons.py and
committed, so that what a launcher is given is a file anybody can open and look at; tests/test_webapp.py holds the
files to the drawing.

It is a stand-in until the project has a mark of its own: change `SHAPE` and the colours, run tools/make-icons.py.
"""
import struct
import zlib

SIZE = 180                      # what an iPhone asks for; smaller uses scale it down
BACK = (17, 17, 20)
MARK = (255, 122, 48)
PATHS = ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
TYPE = "image/png"
# The triangle, as parts of the picture's width and height: its flat side at LEFT, its point at RIGHT.
LEFT, RIGHT, TOP, BOTTOM = 0.36, 0.70, 0.28, 0.72
MASKABLE = 0.85                 # the mark's size in the maskable icon, about the middle of the picture
SAFE = 0.40                     # a launcher may cut away everything outside a circle of this radius (W3C manifest)
# The files tools/make-icons.py writes into pvj/web: name -> (size, the mark's scale). The manifest names the same.
FILES = {"icon-192.png": (192, 1.0), "icon-512.png": (512, 1.0), "icon-maskable-512.png": (512, MASKABLE)}
OUTLINE = "icon-mono.svg"

_cache = {}


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def corners(scale=1.0):
    """The triangle's three corners as parts of the picture, made smaller or larger about the picture's middle."""
    def at(v):
        return 0.5 + (v - 0.5) * scale
    return (at(LEFT), at(TOP)), (at(RIGHT), (at(TOP) + at(BOTTOM)) / 2.0), (at(LEFT), at(BOTTOM))


def png(size=SIZE, scale=1.0):
    """The icon as PNG bytes (RGB, no transparency: iOS paints a transparent home-screen icon black). The edges of
    the mark are smoothed: a pixel the edge crosses gets the two colours mixed by how much of it the mark covers."""
    if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 512:
        raise ValueError("size must be 16 to 512")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.25 <= scale <= 1.0:
        raise ValueError("scale must be 0.25 to 1")
    key = (size, float(scale))
    if key in _cache:
        return _cache[key]
    (l, t), (r, m), (_, b) = corners(scale)
    left, right, top, bottom, middle = l * size, r * size, t * size, b * size, m * size
    back, mark = bytes(BACK), bytes(MARK)
    mixes = {}

    def mixed(cover):               # cover: sixteenths of the pixel that the mark covers
        if cover not in mixes:
            mixes[cover] = bytes((BACK[i] * (16 - cover) + MARK[i] * cover + 8) // 16 for i in range(3))
        return mixes[cover]
    rows = []
    for y in range(size):
        # how far to the right the triangle reaches on four lines through this row of pixels (0: not at all)
        reaches = []
        for k in range(4):
            cy = y + (k + 0.5) / 4.0
            reaches.append(left + (right - left) * (1.0 - abs(cy - middle) / (middle - top)) if top <= cy <= bottom else 0.0)
        far = max(reaches)
        row = bytearray(b"\x00")                    # filter type 0 for this line
        if far <= left:
            row += back * size
        else:
            first, last = int(left), min(size - 1, int(far))
            row += back * first
            for x in range(first, last + 1):
                cover = sum(max(0.0, min(x + 1.0, reach) - max(float(x), left)) for reach in reaches)   # 0 to 4
                row += mixed(int(cover * 4 + 0.5))
            row += back * (size - 1 - last)
        rows.append(bytes(row))
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b""))
    _cache[key] = data
    return data


def read(data):
    """(size, the picture's lines as zlib packed them, unpacked) of a PNG this file wrote; ValueError for anything
    else. Two files are the same picture when this gives the same for both: the packed bytes themselves may differ
    from one zlib to another, so tools/make-icons.py and the tests compare this and not the files' bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    at, packed, size, ended = 8, b"", None, False
    while at + 12 <= len(data) and not ended:
        n, kind = struct.unpack(">I", data[at:at + 4])[0], data[at + 4:at + 8]
        body = data[at + 8:at + 8 + n]
        if struct.unpack(">I", data[at + 8 + n:at + 12 + n])[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ValueError("a damaged PNG")
        if kind == b"IHDR":
            width, height, depth, colour, _, _, _ = struct.unpack(">IIBBBBB", body)
            if width != height or (depth, colour) != (8, 2):
                raise ValueError("not a square RGB picture of 8 bits")
            size = width
        elif kind == b"IDAT":
            packed += body
        ended = kind == b"IEND"
        at += 12 + n
    if not ended or at != len(data):                 # cut short, or something after the end
        raise ValueError("a damaged PNG")
    try:
        raw = zlib.decompress(packed)
    except zlib.error:
        raise ValueError("a damaged PNG")
    if size is None or len(raw) != size * (1 + 3 * size):
        raise ValueError("a damaged PNG")
    return size, raw


def outline():
    """The mark alone as an SVG text, with no ground and no colour of its own, for a system that tints its icons
    (the manifest's purpose "monochrome"). Drawn at the maskable size, since such an icon is cut to a shape too."""
    points = " ".join("%g,%g" % (round(x * 100, 2), round(y * 100, 2)) for x, y in corners(MASKABLE))
    return ("<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors -->\n"
            "<!-- SPDX-License-Identifier: Apache-2.0 -->\n"
            "<!-- Written by tools/make-icons.py from pvj/icon.py; do not edit by hand. -->\n"
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><polygon points="%s"/></svg>\n' % points)


def files():
    """{file name in pvj/web: its bytes}: what tools/make-icons.py writes and tests/test_webapp.py compares."""
    out = {name: png(size, scale) for name, (size, scale) in FILES.items()}
    out[OUTLINE] = outline().encode("ascii")
    return out
