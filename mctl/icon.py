# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The panel's icon, drawn here: one drawing for every size and every use.

The mark is a plain "N" in the house off-white on the house near-black ("NXLX House": quiet ground, one mark, no
gradient, no glow). It is three flat shapes, so it is the same picture at 180 px on an iPhone and at 512 px in a
launcher, and it is written as an SVG as well as drawn as PNGs with the standard library, so nothing outside the
repository is needed to make it.

A phone asks the box for /favicon.ico, /apple-touch-icon.png and /apple-touch-icon-precomposed.png when someone
bookmarks the panel or puts it on the home screen; those three addresses are drawn when asked for (a PNG at each:
browsers take a PNG as a favicon), about 1 KB.

The panel saved as an app (D151, docs/WEBAPP.md) needs larger ones, named by the manifest: 192 and 512 px, a 512 px
"maskable" one (a launcher cuts it to its own shape, so the mark is drawn smaller, well inside the middle), a
monochrome outline for a system that tints its icons, and the colour drawing as an SVG. Those are files in mctl/web,
written from this drawing by tools/make-icons.py and committed, so that what a launcher is given is a file anybody can
open and look at; tests/test_webapp.py holds the files to the drawing.

To change the mark: change `SHAPES` and the colours, run tools/make-icons.py, look at the pictures, commit them.
"""
import struct
import zlib

SIZE = 180                      # what an iPhone asks for; smaller uses scale it down
BACK = (11, 11, 13)             # #0b0b0d, the ground of the house look
MARK = (242, 239, 232)          # #f2efe8, its text colour
PATHS = ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
TYPE = "image/png"
# The N: two stems and the stroke between them, each a convex shape as parts of the picture (x, y), y down.
LEFT, RIGHT, TOP, BOTTOM, STEM = 0.27, 0.73, 0.27, 0.73, 0.10
SHAPES = (
    ((LEFT, TOP), (LEFT + STEM, TOP), (LEFT + STEM, BOTTOM), (LEFT, BOTTOM)),
    ((RIGHT - STEM, TOP), (RIGHT, TOP), (RIGHT, BOTTOM), (RIGHT - STEM, BOTTOM)),
    ((LEFT, TOP), (LEFT + STEM, TOP), (RIGHT, BOTTOM), (RIGHT - STEM, BOTTOM)),
)
MASKABLE = 0.85                 # the mark's size in the maskable icon, about the middle of the picture
SAFE = 0.40                     # a launcher may cut away everything outside a circle of this radius (W3C manifest)
# The files tools/make-icons.py writes into mctl/web: name -> (size, the mark's scale). The manifest names the same.
FILES = {"icon-192.png": (192, 1.0), "icon-512.png": (512, 1.0), "icon-maskable-512.png": (512, MASKABLE)}
OUTLINE = "icon-mono.svg"
DRAWING = "icon.svg"
_GRID = 4                       # samples across a pixel, each way, for the smoothed edges

_cache = {}


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def shapes(scale=1.0):
    """The shapes as corner lists, made smaller or larger about the picture's middle."""
    return tuple(tuple((0.5 + (x - 0.5) * scale, 0.5 + (y - 0.5) * scale) for x, y in s) for s in SHAPES)


def _span(shape, y):
    """Where a convex shape lies along the horizontal line at y: (left, right), or None."""
    xs = []
    for i in range(len(shape)):
        (ax, ay), (bx, by) = shape[i], shape[(i + 1) % len(shape)]
        if ay == by:
            continue
        if min(ay, by) <= y <= max(ay, by):
            xs.append(ax + (bx - ax) * (y - ay) / (by - ay))
    return (min(xs), max(xs)) if len(xs) >= 2 else None


def _union(spans):
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def png(size=SIZE, scale=1.0):
    """The icon as PNG bytes (RGB, no transparency: iOS paints a transparent home-screen icon black). The edges of
    the mark are smoothed: a pixel an edge crosses gets the two colours mixed by how much of it the mark covers."""
    if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 512:
        raise ValueError("size must be 16 to 512")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.25 <= scale <= 1.0:
        raise ValueError("scale must be 0.25 to 1")
    key = (size, float(scale))
    if key in _cache:
        return _cache[key]
    placed = shapes(scale)
    # only the rows a shape's box touches are sampled; the rest is ground
    lo_y = int(min(y for s in placed for _, y in s) * size)
    hi_y = min(size - 1, int(max(y for s in placed for _, y in s) * size))
    back = bytes(BACK)
    mixes = {}
    full = _GRID * _GRID

    def mixed(cover):
        if cover not in mixes:
            mixes[cover] = bytes((BACK[i] * (full - cover) + MARK[i] * cover + full // 2) // full for i in range(3))
        return mixes[cover]
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")                    # filter type 0 for this line
        if y < lo_y or y > hi_y:
            row += back * size
        else:
            cover = [0.0] * size                    # per pixel: how much of the sample lines it holds
            for j in range(_GRID):
                line = (y + (j + 0.5) / _GRID) / size
                spans = [sp for sp in (_span(sh, line) for sh in placed) if sp]
                for lo, hi in _union([(p * size, q * size) for p, q in spans]):
                    for x in range(max(0, int(lo)), min(size - 1, int(hi)) + 1):
                        cover[x] += max(0.0, min(x + 1.0, hi) - max(float(x), lo))
            for x in range(size):
                row += mixed(int(cover[x] / _GRID * full + 0.5)) if cover[x] else back
        rows.append(bytes(row))
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b""))
    _cache[key] = data
    return data


def read(data):
    """(size, the picture's lines, unpacked) of a PNG this file wrote; ValueError for anything else. Two files are
    the same picture when this gives the same for both: the packed bytes themselves may differ from one zlib to
    another, so tools/make-icons.py and the tests compare this and not the files' bytes."""
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


def _points(shape):
    return " ".join("%g,%g" % (round(x * 100, 2), round(y * 100, 2)) for x, y in shape)


def _svg(body):
    return ("<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors -->\n"
            "<!-- SPDX-License-Identifier: Apache-2.0 -->\n"
            "<!-- Written by tools/make-icons.py from mctl/icon.py; do not edit by hand. -->\n"
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">%s</svg>\n' % body)


def outline():
    """The mark alone as an SVG text, with no ground and no colour of its own, for a system that tints its icons
    (the manifest's purpose "monochrome"). Drawn at the maskable size, since such an icon is cut to a shape too."""
    return _svg("".join('<polygon points="%s"/>' % _points(s) for s in shapes(MASKABLE)))


def drawing():
    """The whole icon as an SVG text: the ground and the mark in their colours (the source the PNGs are drawn from)."""
    return _svg('<rect width="100" height="100" fill="#%02x%02x%02x"/><g fill="#%02x%02x%02x">%s</g>'
                % (BACK + MARK + ("".join('<polygon points="%s"/>' % _points(s) for s in shapes(1.0)),)))


def files():
    """{file name in mctl/web: its bytes}: what tools/make-icons.py writes and tests/test_webapp.py compares."""
    out = {name: png(size, scale) for name, (size, scale) in FILES.items()}
    out[OUTLINE] = outline().encode("ascii")
    out[DRAWING] = drawing().encode("ascii")
    return out
