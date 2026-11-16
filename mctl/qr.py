# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A small QR code encoder: standard library only.

Byte mode, error correction level M, versions 1 to 10 (up to 213 bytes), which is plenty for a panel address with a join
code. `encode()` returns the module matrix; `png()`, `svg()` and `bgra()` draw it. It is checked in the tests by
decoding what it makes with a real decoder (zbar) when one is installed, and against published Reed-Solomon and
format-information values otherwise.

The algorithm follows the ISO/IEC 18004 layout as described by the public-domain reference implementations.
"""

import struct
import zlib

# (data codewords in the first group blocks, ...) per version for level M:
# version -> (ec codewords per block, [(block count, data codewords per block), ...])
_M_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
ECC_M_FORMAT_BITS = 0          # level M is 00 in the format information


class QrError(Exception):
    pass


def capacity(version):
    """How many bytes fit in `version` at level M."""
    ec, groups = _M_BLOCKS[version]
    data = sum(n * d for n, d in groups)
    return data - 2 - (1 if version >= 10 else 0)       # mode (4 bits) + count (8 or 16 bits) + terminator slack


# --- Reed-Solomon over GF(256) --------------------------------------------------
def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def rs_remainder(data, degree):
    """The Reed-Solomon error correction codewords for `data`."""
    divisor = _rs_divisor(degree)
    result = [0] * degree
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# --- building the bit stream -----------------------------------------------------
def _data_codewords(data, version):
    ec, groups = _M_BLOCKS[version]
    total = sum(n * d for n, d in groups)
    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)
    put(0b0100, 4)
    put(len(data), 8 if version < 10 else 16)
    for b in data:
        put(b, 8)
    limit = total * 8
    put(0, min(4, limit - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < total:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    return words


def _interleave(words, version):
    ec, groups = _M_BLOCKS[version]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    eccs = [rs_remainder(b, ec) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec):
        for e in eccs:
            out.append(e[i])
    return out


# --- drawing -----------------------------------------------------------------------
class _Grid:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def setf(self, x, y, dark):
        self.m[y][x] = bool(dark)
        self.fn[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self.setf(6, i, i % 2 == 0)
            self.setf(i, 6, i % 2 == 0)
        for x, y in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < n and 0 <= yy < n:
                        self.setf(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        for i, cx in enumerate(pos):
            for j, cy in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)                       # reserves the area; drawn again with the real mask
        self.draw_version()

    def draw_format(self, mask):
        bits = format_bits(mask)
        n = self.size
        for i in range(6):
            self.setf(8, i, (bits >> i) & 1)
        self.setf(8, 7, (bits >> 6) & 1)
        self.setf(8, 8, (bits >> 7) & 1)
        self.setf(7, 8, (bits >> 8) & 1)
        for i in range(9, 15):
            self.setf(14 - i, 8, (bits >> i) & 1)
        for i in range(8):
            self.setf(n - 1 - i, 8, (bits >> i) & 1)
        for i in range(8, 15):
            self.setf(8, n - 15 + i, (bits >> i) & 1)
        self.setf(8, n - 8, True)                 # the one always-dark module

    def draw_version(self):
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        n = self.size
        for i in range(18):
            bit = (bits >> i) & 1
            a, b = n - 11 + i % 3, i // 3
            self.setf(a, b, bit)
            self.setf(b, a, bit)

    def draw_codewords(self, data):
        n, i = self.size, 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = bool((data[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                if self.fn[y][x]:
                    continue
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                self.m[y][x] ^= inv

    def penalty(self):
        n, m, score = self.size, self.m, 0
        for lines in (m, [list(c) for c in zip(*m)]):          # rows, then columns
            for row in lines:
                run, prev = 1, row[0]
                for v in row[1:]:
                    if v == prev:
                        run += 1
                    else:
                        if run >= 5:
                            score += run - 2
                        run, prev = 1, v
                if run >= 5:
                    score += run - 2
                s = "".join("1" if v else "0" for v in row)
                score += 40 * (s.count("10111010000") + s.count("00001011101"))
        for y in range(n - 1):
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(r) for r in m)
        score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
        return score


def format_bits(mask, ecc_bits=ECC_M_FORMAT_BITS):
    """The 15 format information bits (with the standard XOR mask) for level M and `mask`."""
    data = ecc_bits << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def encode(text):
    """The QR module matrix for `text` (str or bytes) as a list of rows of booleans (True is a dark module)."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    for version in range(1, 11):
        if len(data) <= capacity(version):
            break
    else:
        raise QrError("too long for a QR code here (%d bytes; the limit is %d)" % (len(data), capacity(10)))
    words = _interleave(_data_codewords(data, version), version)
    words = words + [0] * 0      # the remainder bits stay zero
    best = None
    for mask in range(8):
        g = _Grid(version)
        g.draw_function_patterns()
        g.draw_codewords(words)
        g.apply_mask(mask)
        g.draw_format(mask)
        p = g.penalty()
        if best is None or p < best[0]:
            best = (p, g)
    return best[1].m


# --- output -------------------------------------------------------------------------
def png(matrix, scale=8, border=4):
    """A black and white PNG of the code, `scale` pixels per module and `border` modules of white around it."""
    n = len(matrix)
    side = (n + 2 * border) * scale
    rows = []
    for y in range(side):
        my = y // scale - border
        line = bytearray([0])
        bits = []
        for x in range(side):
            mx = x // scale - border
            dark = 0 <= mx < n and 0 <= my < n and matrix[my][mx]
            bits.append(0 if dark else 1)
        for i in range(0, side, 8):
            chunk = bits[i:i + 8] + [1] * (8 - len(bits[i:i + 8]))
            line.append(int("".join(map(str, chunk)), 2))
        rows.append(bytes(line))
    raw = b"".join(rows)

    def chunk(kind, body):
        c = struct.pack(">I", len(body)) + kind + body
        return c + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def svg(matrix, border=4):
    """An SVG of the code (one path, crisp at any size)."""
    n = len(matrix)
    cells = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if row[x]:
                start = x
                while x < n and row[x]:
                    x += 1
                cells.append("M%d,%dh%dv1h-%dz" % (start + border, y + border, x - start, x - start))
            else:
                x += 1
    size = n + 2 * border
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="100%%" height="100%%" fill="#fff"/><path d="%s" fill="#000"/></svg>' % (size, size, "".join(cells)))


def bgra(matrix, scale, border=4):
    """(width, height, bytes) of the code as raw BGRA pixels (opaque black on white), for mpv's overlay-add."""
    n = len(matrix)
    side = (n + 2 * border) * scale
    white, black = b"\xff\xff\xff\xff", b"\x00\x00\x00\xff"
    out = bytearray()
    for y in range(side):
        my = y // scale - border
        row = bytearray()
        for mx in range(-border, n + border):
            dark = 0 <= mx < n and 0 <= my < n and matrix[my][mx]
            row += (black if dark else white) * scale
        out += row
    return side, side, bytes(out)
