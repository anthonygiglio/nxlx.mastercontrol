# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from pvj import qr
import tests        # the run's own temp folder and the locks' checker, however this module is started (tests/__init__.py)


class KnownValuesTest(unittest.TestCase):
    def test_reed_solomon_matches_the_published_example(self):
        # the widely used "HELLO WORLD" example (version 1, 13 error correction codewords)
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236]
        self.assertEqual(qr.rs_remainder(data, 13), [168, 72, 22, 82, 217, 54, 156, 0, 46, 15, 180, 122, 16])

    def test_format_information_for_level_m_matches_the_standard_table(self):
        expected = ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
                    "100010111111001", "100000011001110", "100111110010111", "100101010100000"]
        self.assertEqual([format(qr.format_bits(m), "015b") for m in range(8)], expected)

    def test_every_format_value_is_a_valid_bch_codeword(self):
        # independent of the table: undo the standard XOR mask, divide by the generator polynomial, remainder must be 0
        for mask in range(8):
            v = qr.format_bits(mask) ^ 0x5412
            for i in range(14, 9, -1):
                if v >> i & 1:
                    v ^= 0x537 << (i - 10)
            self.assertEqual(v, 0, mask)

    def test_capacities_at_level_m(self):
        self.assertEqual([qr.capacity(v) for v in range(1, 11)], [14, 26, 42, 62, 84, 106, 122, 152, 180, 213])


class StructureTest(unittest.TestCase):
    def test_size_grows_with_the_data_and_the_fixed_patterns_are_there(self):
        for text, version in (("A", 1), ("x" * 15, 2), ("x" * 43, 4), ("x" * 213, 10)):
            m = qr.encode(text)
            self.assertEqual(len(m), 17 + 4 * version, text[:5])
            n = len(m)
            for cx, cy in ((0, 0), (n - 7, 0), (0, n - 7)):                      # the three finder patterns
                for dy in range(7):
                    for dx in range(7):
                        want = max(abs(dx - 3), abs(dy - 3)) != 2 and max(abs(dx - 3), abs(dy - 3)) != 4 if max(abs(dx - 3), abs(dy - 3)) < 4 else False
                        self.assertEqual(m[cy + dy][cx + dx], want)
            self.assertTrue(all(m[6][x] == (x % 2 == 0) for x in range(8, n - 8)))   # timing pattern
            self.assertTrue(m[n - 8][8])                                              # the always-dark module

    def test_too_long_is_refused_and_bytes_and_str_agree(self):
        with self.assertRaises(qr.QrError):
            qr.encode("x" * 214)
        self.assertEqual(qr.encode("http://a/"), qr.encode(b"http://a/"))

    def test_the_same_text_always_gives_the_same_code(self):
        self.assertEqual(qr.encode("http://192.168.0.5/#code=123456"), qr.encode("http://192.168.0.5/#code=123456"))


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.m = qr.encode("http://192.168.0.5/#code=123456")

    def test_png_is_a_valid_one_bit_image_of_the_right_size(self):
        data = qr.png(self.m, scale=5, border=4)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height, depth, ctype = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((width, height, depth, ctype), ((len(self.m) + 8) * 5,) * 2 + (1, 0))
        pos, idat = 8, b""
        while pos < len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            self.assertEqual(struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0], zlib.crc32(kind + body) & 0xFFFFFFFF)
            if kind == b"IDAT":
                idat += body
            pos += 12 + length
        self.assertEqual(len(zlib.decompress(idat)), height * (1 + (width + 7) // 8))

    def test_svg_has_one_path_and_no_script(self):
        text = qr.svg(self.m)
        self.assertTrue(text.startswith("<svg") and text.count("<path") == 1)
        self.assertNotIn("<script", text)
        self.assertIn("viewBox=\"0 0 %d %d\"" % (len(self.m) + 8, len(self.m) + 8), text)

    def test_bgra_is_opaque_black_on_white_with_a_quiet_zone(self):
        w, h, raw = qr.bgra(self.m, 3, border=4)
        self.assertEqual((w, h, len(raw)), ((len(self.m) + 8) * 3,) * 2 + ((len(self.m) + 8) ** 2 * 9 * 4,))
        self.assertEqual(raw[:4], b"\xff\xff\xff\xff")                                      # the corner is quiet-zone white
        first_dark = ((4 * 3) * w + 4 * 3) * 4                                               # the top-left module is part of a finder: dark
        self.assertEqual(raw[first_dark:first_dark + 4], b"\x00\x00\x00\xff")
        self.assertTrue(all(raw[i + 3] == 0xFF for i in range(0, len(raw), 4)))               # every pixel opaque


@unittest.skipUnless(shutil.which("zbarimg"), "needs the zbar decoder (zbar-tools)")
class DecodesWithARealDecoderTest(unittest.TestCase):
    def decode(self, text, scale=6):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "q.png")
        with open(path, "wb") as f:
            f.write(qr.png(qr.encode(text), scale=scale))
        r = subprocess.run(["zbarimg", "--quiet", "--raw", "-Sdisable", "-Sqrcode.enable", path], capture_output=True, text=True)
        return r.stdout.rstrip("\n")

    def test_every_version_and_the_capacity_edges(self):
        for n in (1, 14, 15, 26, 27, 42, 43, 62, 63, 84, 85, 106, 107, 122, 123, 152, 153, 180, 181, 213):
            text = ("nxlx" * 60)[:n]
            self.assertEqual(self.decode(text), text, n)

    def test_join_links_unicode_and_random_text(self):
        for text in ("http://192.168.0.169/#code=123456", "http://nxlx-mastercontrol.local/#code=000000", "café ✓ ünï"):
            self.assertEqual(self.decode(text), text)
        rng = random.Random(3)
        for _ in range(15):
            text = "".join(chr(rng.randrange(33, 127)) for _ in range(rng.randrange(1, 200)))
            self.assertEqual(self.decode(text), text)


if __name__ == "__main__":
    unittest.main()
