# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Safe image upload (D129, slice 1 of design/text-and-logo-sources_2026-11-08.md): pvj/raster.py and pvj-raster.

Three layers:

* the checks that need no decoder, and the store, against a stand-in for the helper (runs everywhere);
* the routes and who may use them, over HTTP (runs everywhere);
* the real program render/pvj-raster, fed good files, damaged files and hostile ones (runs only with PVJ_RASTER_TEST=1
  and the program built: make -C render pvj-raster; CI's job pvj-raster does that and treats a skip as a failure).

Nothing here has run on a Pi, and none of it has run the program under its systemd unit (only a static look at the unit).
Every wait is bounded.
"""
import base64
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib

import tests  # noqa: F401  (the package: ranked locks and a temp folder of its own, as every test module)
from pvj import api as api_mod, policy, raster
from tests.test_roles import RolesBase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(HERE, "render", "pvj-raster")
REAL = os.environ.get("PVJ_RASTER_TEST") == "1"

# --- files made here -------------------------------------------------------------------------------------------------


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(width, height, pixel=lambda x, y: (255, 0, 0), color=2, depth=8, extra=(), trailer=b"", palette=None, trns=None):
    """A PNG: color 2 is RGB, 6 RGBA, 3 palette, 0 grey; pixel(x, y) gives the channel values (16-bit when depth is 16)."""
    channels = {0: 1, 2: 3, 3: 1, 6: 4}[color]
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            for v in (pixel(x, y)[:channels] if color != 3 else (pixel(x, y)[0],)):
                raw += struct.pack(">H", v) if depth == 16 else bytes([v])
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
    for kind, body in extra:
        out += chunk(kind, body)
    if palette:
        out += chunk(b"PLTE", palette)
    if trns:
        out += chunk(b"tRNS", trns)
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"") + trailer
    return out


def big_png(width, height):
    """One colour, many pixels, a few KB: a decompression bomb when the size is allowed, built a row at a time."""
    z = zlib.compressobj(9)
    row = b"\x00" + b"\x10\x20\x30" * width
    body = b"".join(z.compress(row) for _ in range(height)) + z.flush()
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", body) + chunk(b"IEND", b""))


def read_png(data):
    """(width, height, channels, rows of bytes, [chunk names]) for the PNG that pvj-raster writes (8 bit, no interlace)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, names, idat, head = 8, [], b"", None
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        kind, body = data[i + 4:i + 8], data[i + 8:i + 8 + n]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == struct.unpack(">I", data[i + 8 + n:i + 12 + n])[0]
        names.append(kind.decode())
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        i += 12 + n
    width, height, depth, color, _c, _f, interlace = head
    assert depth == 8 and interlace == 0 and color in (2, 6), head
    ch = 3 if color == 2 else 4
    raw, stride, rows, prev = zlib.decompress(idat), width * ch, [], bytearray(width * ch)
    for y in range(height):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            c = prev[x - ch] if x >= ch else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return width, height, ch, rows, names


def pixel_at(img, x, y):
    width, _h, ch, rows, _n = img
    p = rows[y][x * ch:(x + 1) * ch]
    return tuple(p) if ch == 4 else tuple(p) + (255,)


# 32x16 JPEG, left half red and right half blue, 4:4:4 (made with libjpeg for these tests); the same progressive; a grey
# 16x8; a CMYK 16x16. They are tiny and are here as text so that no tool is needed to make them.
JPEG_RGB = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAAQACADAREAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD50r8MP9UwoA8Cr/cY/wAdAoA99r/Dk/2LCgDwKv8AcY/x0CgD/9k=")
JPEG_PROGRESSIVE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wgARCAAQACADAREAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAYH/8QAFgEBAQEAAAAAAAAAAAAAAAAAAAgG/9oADAMBAAIQAxAAAAHOcLVIgbjjoX0OWKIG446H/8QAFBABAAAAAAAAAAAAAAAAAAAAMP/aAAgBAQABBQIP/8QAFBEBAAAAAAAAAAAAAAAAAAAAMP/aAAgBAwEBPwEP/8QAFBEBAAAAAAAAAAAAAAAAAAAAMP/aAAgBAgEBPwEP/8QAFBABAAAAAAAAAAAAAAAAAAAAMP/aAAgBAQAGPwIP/8QAFBABAAAAAAAAAAAAAAAAAAAAMP/aAAgBAQABPyEP/9oADAMBAAIAAwAAABD9L9L/xAAUEQEAAAAAAAAAAAAAAAAAAAAw/9oACAEDAQE/EA//xAAUEQEAAAAAAAAAAAAAAAAAAAAw/9oACAECAQE/EA//xAAUEAEAAAAAAAAAAAAAAAAAAAAw/9oACAEBAAE/EA//2Q==")
JPEG_GREY = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/wAALCAAIABABAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APzfr94K/9k=")
JPEG_CMYK = base64.b64decode(
    "/9j/7gAOQWRvYmUAZAAAAAAA/9sAQwADAgICAgIDAgICAwMDAwQGBAQEBAQIBgYFBgkICgoJCAkJCgwPDAoLDgsJCQ0RDQ4PEBAREAoMEhMSEBMPEBAQ/8AAFAgAEAAQBEMRAE0RAFkRAEsRAP/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/aAA4EQwBNAFkASwAAPwD836/N+vzfr836/eCv3gr94K/eCvwfr8H6/B+vwfr94K/eCv3gr94K/9k=")


def jpeg_with_xmp_then_orientation(jpeg, value):
    """Like jpeg_with_orientation, but an XMP block (also an APP1) comes first, as many phones and editors write it."""
    xmp = b"http://ns.adobe.com/xap/1.0/\x00" + b"<x:xmpmeta/>"
    exif = jpeg_with_orientation(jpeg, value)
    return exif[:2] + b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp + exif[2:]


def jpeg_with_orientation(jpeg, value):
    """The JPEG with an Exif block (APP1) that says the picture should be turned as `value` (1 to 8)."""
    tiff = b"II*\x00\x08\x00\x00\x00" + struct.pack("<H", 1) + struct.pack("<HHIHH", 0x0112, 3, 1, value, 0) + b"\x00\x00\x00\x00"
    body = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + jpeg[2:]


XML = b'<?xml version="1.0"?>'
SVG_OK = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="50" viewBox="0 0 100 50">'
          b'<rect width="50" height="50" fill="#ff0000"/><rect x="50" width="50" height="50" fill="#0000ff"/></svg>')
PIXEL_PNG = png(1, 1, lambda x, y: (1, 2, 3))


def svg(inner, head=b'width="100" height="50" viewBox="0 0 100 50"', pre=b""):
    return pre + b'<svg xmlns="http://www.w3.org/2000/svg" ' + head + b">" + inner + b"</svg>"


# --- the stand-in for the helper -------------------------------------------------------------------------------------
class FakeClient:
    """Stands where raster.Client does and counts what reached it: a refusal that never gets here decoded nothing."""

    def __init__(self, answer=None):
        self.calls, self.thumbs, self.answer = [], 0, answer or PIXEL_PNG

    def available(self):
        return True

    def normalise(self, data):
        self.calls.append(data)
        w, h = raster.png_size(self.answer)
        return {"width": w, "height": h, "alpha": False}, self.answer

    def thumbnail(self, png_bytes, side=raster.THUMB_SIDE):
        self.thumbs += 1
        return self.answer


# --- 1. what is checked without a decoder, and the store ---------------------------------------------------------------
class InspectTest(unittest.TestCase):
    def kind(self, data, name):
        return raster.inspect(data, name)

    def refused(self, data, name, status, words=""):
        with self.assertRaises(raster.RasterError) as cm:
            raster.inspect(data, name)
        self.assertEqual(cm.exception.status, status, cm.exception.message)
        self.assertIn(words, cm.exception.message)
        return cm.exception.message

    def test_the_three_types_pass_by_their_first_bytes(self):
        self.assertEqual(self.kind(png(2, 2), "a.png"), "png")
        self.assertEqual(self.kind(JPEG_RGB, "a.jpg"), "jpeg")
        self.assertEqual(self.kind(JPEG_PROGRESSIVE, "a.JPEG"), "jpeg")
        self.assertEqual(self.kind(JPEG_GREY, "a.jpg"), "jpeg")
        self.assertEqual(self.kind(SVG_OK, "a.svg"), "svg")
        self.assertEqual(self.kind(b"\xef\xbb\xbf  \n" + SVG_OK, "a.svg"), "svg")      # a byte-order mark and white space first
        self.assertEqual(self.kind(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg"), "svg")

    def test_other_types_are_refused_in_plain_words_whatever_their_name(self):
        samples = {"gif": b"GIF89a" + b"\0" * 40, "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\0" * 30, "bmp": b"BM" + b"\0" * 60,
                   "tiff": b"II*\x00" + b"\0" * 60, "pdf": b"%PDF-1.7\n" + b"x" * 60, "heic": b"\0\0\0\x18ftypheic" + b"\0" * 40,
                   "text": b"hello, this is not a picture at all", "html": b"<html><script>alert(1)</script></html>",
                   "elf": b"\x7fELF" + b"\0" * 60}
        for what, data in samples.items():
            for name in ("logo.png", "logo.jpg", "logo.svg", "logo.gif", "logo.webp"):
                message = self.refused(data, name, 415)
                self.assertIn("Only PNG, JPEG and SVG", message, what)
        self.refused(b"", "a.png", 400, "empty")

    def test_the_name_must_agree_with_what_is_inside(self):
        self.refused(png(2, 2), "a.jpg", 415, "PNG")
        self.refused(png(2, 2), "a.svg", 415, "PNG")
        self.refused(JPEG_RGB, "a.png", 415, "JPEG")
        self.refused(SVG_OK, "a.png", 415, "SVG")
        self.refused(png(2, 2), "a", 415)
        self.refused(png(2, 2), "a.png.exe", 415)

    def test_size_on_disk_comes_before_anything_else(self):
        self.refused(b"\x89PNG\r\n\x1a\n" + b"\0" * (11 * 1024 * 1024), "a.png", 413, "10 MB")
        self.refused(JPEG_RGB + b"\0" * (11 * 1024 * 1024), "a.jpg", 413, "10 MB")
        self.refused(b"<svg>" + b" " * (1024 * 1024 + 1), "a.svg", 413, "1 MB")
        self.assertEqual(self.kind(b"<svg>" + b" " * (1024 * 1024 - 100), "a.svg"), "svg")

    def test_dimensions_are_read_from_the_header_and_limited(self):
        def header(w, h):
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + b"\0" * 20
        self.assertEqual(self.kind(header(8192, 4880), "a.png"), "png")                  # 39.98 million pixels
        self.refused(header(8193, 10), "a.png", 413, "8192")
        self.refused(header(10, 8193), "a.png", 413, "8192")
        self.refused(header(8192, 4883), "a.png", 413, "40 million")
        self.refused(header(0, 10), "a.png", 400, "no size")
        self.refused(header(40000, 40000), "a.png", 413)
        self.refused(b"\x89PNG\r\n\x1a\n" + b"\0" * 30, "a.png", 400, "damaged")

    def test_jpeg_dimensions_and_kinds(self):
        def with_size(w, h):
            data = bytearray(JPEG_RGB)
            at = data.index(b"\xff\xc0") + 5
            data[at:at + 4] = struct.pack(">HH", h, w)
            return bytes(data)
        self.assertEqual(raster.jpeg_info(JPEG_RGB)[:3], (32, 16, 3))
        self.assertEqual(raster.jpeg_info(JPEG_GREY)[:3], (16, 8, 1))
        self.assertEqual(raster.jpeg_info(JPEG_PROGRESSIVE)[3], 0xC2)
        self.refused(with_size(9000, 100), "a.jpg", 413, "8192")
        self.refused(with_size(8000, 8000), "a.jpg", 413, "40 million")
        self.refused(JPEG_CMYK, "a.jpg", 415, "CMYK")
        self.refused(JPEG_RGB.replace(b"\xff\xc0", b"\xff\xc3", 1), "a.jpg", 415, "not supported")        # lossless
        self.refused(JPEG_RGB.replace(b"\xff\xc0", b"\xff\xc9", 1), "a.jpg", 415, "not supported")        # arithmetic
        self.refused(b"\xff\xd8\xff\xd9", "a.jpg", 400, "damaged")
        self.refused(b"\xff\xd8\xff\xe0" + b"\x00\x02" * 3, "a.jpg", 400, "damaged")


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = os.path.join(self.tmp, "graphics")
        self.client = FakeClient()
        self.now = [1000.0]
        self.store = raster.Store(self.folder, api_mod.valid_name, self.client, clock=lambda: self.now[0])

    def listing(self):
        return sorted(n for n in os.listdir(self.folder) if not n.startswith(".")) if os.path.isdir(self.folder) else []


class StoreTest(StoreBase):
    def test_a_good_file_is_stored_as_the_helpers_png_under_its_chosen_name(self):
        for name, data in (("Mara logo.png", png(4, 4)), ("dj.jpg", JPEG_RGB), ("tag.svg", SVG_OK)):
            out = self.store.store(name, data)
            self.assertEqual(out["name"], os.path.splitext(name)[0] + ".png")
        self.assertEqual(self.listing(), ["Mara logo.png", "dj.png", "tag.png"])
        with open(os.path.join(self.folder, "dj.png"), "rb") as f:
            self.assertEqual(f.read(), PIXEL_PNG)                       # what the helper wrote, never the upload
        self.assertEqual(len(self.client.calls), 3)
        self.assertEqual([f["name"] for f in self.store.files()], ["Mara logo.png", "dj.png", "tag.png"])
        self.assertEqual(self.store.files()[0]["width"], 1)

    def test_a_refusal_decodes_nothing(self):
        bad = [(b"GIF89a" + b"\0" * 40, "a.png"), (png(2, 2), "a.jpg"), (JPEG_CMYK, "a.jpg"), (b"", "a.png"),
               (b"\x89PNG\r\n\x1a\n" + b"\0" * (11 * 1024 * 1024), "a.png"), (SVG_OK, "a.png"), (png(2, 2), "../a.png"),
               (JPEG_RGB.replace(b"\xff\xc0", b"\xff\xc3", 1), "a.jpg")]
        for data, name in bad:
            with self.assertRaises(raster.RasterError):
                self.store.store(name, data)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.listing(), [])

    def test_names_that_try_to_leave_the_folder_or_hide_are_refused(self):
        tricks = ["../evil.png", "a/b.png", "..\\evil.png", "/etc/passwd.png", ".hidden.png", "x\x00y.png", "a" * 300 + ".png",
                  "‮txt.png", "new\nline.png", "..", ".", "", "sub/../../x.png", "a\\b.png", "�.png", "\x07.png"]
        for name in tricks:
            with self.assertRaises(raster.RasterError, msg=repr(name)):
                self.store.store(name, png(2, 2))
        self.assertEqual(self.client.calls, [])
        for root, _dirs, files in os.walk(self.tmp):
            self.assertEqual([f for f in files if f.endswith(".png")], [], root)
        self.assertEqual(self.listing(), [])

    def test_the_other_calls_refuse_the_same_names(self):
        self.store.store("a.png", png(2, 2))
        for name in ("../a.png", "a/../a.png", "/a.png", ".a.png", "a.jpg", "a", "a\x00.png"):
            with self.assertRaises(raster.RasterError, msg=repr(name)):
                self.store.delete(name)
            with self.assertRaises(raster.RasterError, msg=repr(name)):
                self.store.thumbnail(name)
            with self.assertRaises(raster.RasterError, msg=repr(name)):
                self.store.rename(name, "b.png")
        for new in ("../b.png", "b/c.png", ".b.png", "b.jpg", "b", None, 5, "b\x00.png"):
            with self.assertRaises(raster.RasterError, msg=repr(new)):
                self.store.rename("a.png", new)
        self.assertEqual(self.listing(), ["a.png"])

    def test_no_overwriting_without_asking(self):
        self.store.store("a.png", png(2, 2))
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("a.jpg", JPEG_RGB)                  # the same stored name
        self.assertEqual(cm.exception.status, 409)
        self.client.answer = png(3, 3)
        self.assertEqual(self.store.store("a.png", png(2, 2), replace=True)["width"], 3)
        self.assertEqual(self.store.files()[0]["width"], 3)

    def test_a_link_is_never_followed_or_replaced(self):
        self.store.store("a.png", png(2, 2))
        outside = os.path.join(self.tmp, "secret.txt")
        with open(outside, "w") as f:
            f.write("secret")
        os.symlink(outside, os.path.join(self.folder, "l.png"))
        self.assertEqual([f["name"] for f in self.store.files()], ["a.png"])                 # not listed
        for call in (lambda: self.store.thumbnail("l.png"), lambda: self.store.delete("l.png"),
                     lambda: self.store.rename("l.png", "m.png"), lambda: self.store.store("l.png", png(2, 2), replace=True)):
            with self.assertRaises(raster.RasterError):
                call()
        with open(outside) as f:
            self.assertEqual(f.read(), "secret")

    def test_rename_delete_and_the_thumbnail(self):
        self.store.store("a.png", png(2, 2))
        self.assertEqual(self.store.thumbnail("a.png"), PIXEL_PNG)
        self.assertEqual(self.store.thumbnail("a.png"), PIXEL_PNG)
        self.assertEqual(self.client.thumbs, 1)                       # made once, kept
        self.assertEqual(self.store.rename("a.png", "b.png"), {"name": "b.png"})
        self.assertEqual(self.listing(), ["b.png"])
        self.store.store("c.png", png(2, 2))
        with self.assertRaises(raster.RasterError) as cm:
            self.store.rename("b.png", "c.png")                       # never over another
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.store.delete("b.png"), {"deleted": "b.png"})
        self.assertEqual(self.listing(), ["c.png"])
        self.assertEqual(os.listdir(os.path.join(self.folder, ".thumbs")), [])
        with self.assertRaises(raster.RasterError) as cm:
            self.store.delete("b.png")
        self.assertEqual(cm.exception.status, 404)

    def test_two_first_time_thumbnails_at_once_each_get_their_own_file(self):
        self.store.store("seed.png", png(2, 2))                       # makes the folder
        red, blue = png(2, 2, lambda x, y: (255, 0, 0)), png(2, 2, lambda x, y: (0, 0, 255))
        thumbs = {red: png(3, 3, lambda x, y: (250, 0, 0)), blue: png(3, 3, lambda x, y: (0, 0, 250))}
        for name, data in (("red.png", red), ("blue.png", blue)):
            with open(os.path.join(self.folder, name), "wb") as f:
                f.write(data)
        both = threading.Barrier(2, timeout=10)

        class Meeting(FakeClient):
            def thumbnail(me, png_bytes, side=raster.THUMB_SIDE):
                both.wait()                                           # both are inside at once, then both write
                return thumbs[png_bytes]
        self.store.client = Meeting()
        got, errors = {}, []

        def make(name):
            try:
                got[name] = self.store.thumbnail(name)
            except Exception as e:                                    # reported below, not lost in the thread
                errors.append(e)
        threads = [threading.Thread(target=make, args=(n,)) for n in ("red.png", "blue.png")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(got, {"red.png": thumbs[red], "blue.png": thumbs[blue]})
        kept = os.path.join(self.folder, ".thumbs")
        for name, data in (("red.png", thumbs[red]), ("blue.png", thumbs[blue])):
            with open(os.path.join(kept, name), "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertEqual(sorted(n for n in os.listdir(kept) if n.startswith(".")), [])   # no temp file left

    def test_nothing_is_stored_that_cannot_be_thumbnailed(self):
        big = PIXEL_PNG + b"\0" * raster.MAX_PICTURE_BYTES
        self.store.client = FakeClient(answer=big)
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("a.png", png(2, 2))
        self.assertEqual(cm.exception.status, 413)
        self.assertEqual(self.listing(), [])
        self.store.client = FakeClient(answer=PIXEL_PNG + b"\0" * (raster.MAX_PICTURE_BYTES - len(PIXEL_PNG)))
        self.store.store("b.png", png(2, 2))                          # exactly the limit is kept, and can be thumbnailed
        self.assertEqual(self.listing(), ["b.png"])

    def test_a_helper_that_ends_with_no_answer_is_a_plain_too_big_not_a_502(self):
        with self.assertRaises(raster.RasterError) as cm:
            raster.Client._parse(b"")
        self.assertEqual(cm.exception.status, 413)
        self.assertEqual(cm.exception.message, "This picture is too big to process; try a smaller one.")
        killed = os.path.join(self.tmp, "killed")                     # a program the system killed: no output at all
        with open(killed, "w") as f:
            f.write("#!/bin/sh\nkill -9 $$\n")
        os.chmod(killed, 0o755)
        client = raster.Client(exec_bin=killed, env={})
        with self.assertRaises(raster.RasterError) as cm:
            client.normalise(png(2, 2))
        self.assertEqual(cm.exception.status, 413)
        self.assertIn("try a smaller one", cm.exception.message)

    def test_limits_on_the_number_of_files_the_space_and_the_rate(self):
        for i in range(raster.UPLOADS_PER_MINUTE):
            self.store.store("n%d.png" % i, png(2, 2))
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("more.png", png(2, 2))
        self.assertEqual(cm.exception.status, 429)
        self.now[0] += 61
        self.store.store("more.png", png(2, 2))
        self.store.store("more.png", png(2, 2), replace=True)       # a replacement is not a new file
        # the count
        for i in range(raster.MAX_FILES):
            with open(os.path.join(self.folder, "x%02d.png" % i), "wb") as f:
                f.write(PIXEL_PNG)
        self.now[0] += 61
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("one-too-many.png", png(2, 2))
        self.assertEqual(cm.exception.status, 409)
        # the space: 100 MB in all
        shutil.rmtree(self.folder)
        os.makedirs(self.folder)
        self.client.answer = PIXEL_PNG
        self.store.free_space = lambda: 10 * 1024 * 1024
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("disk.png", png(2, 2))
        self.assertEqual(cm.exception.status, 507)
        self.store.free_space = lambda: 1 << 40
        with open(os.path.join(self.folder, "huge.png"), "wb") as f:
            f.write(PIXEL_PNG)
            f.truncate(raster.MAX_TOTAL_BYTES)
        with self.assertRaises(raster.RasterError) as cm:
            self.store.store("full.png", png(2, 2))
        self.assertEqual(cm.exception.status, 507)

    def test_the_helper_is_not_trusted_either(self):
        for bad in (b"not a png", b"OK 1 1 0 3\nabc"):
            with self.assertRaises(raster.RasterError) as cm:
                raster.Client._parse(bad)
            self.assertEqual(cm.exception.status, 502)
        good = raster.Client._parse(b"OK 1 1 0 %d\n" % len(PIXEL_PNG) + PIXEL_PNG)
        self.assertEqual(good[0], {"width": 1, "height": 1, "alpha": False})
        with self.assertRaises(raster.RasterError):
            raster.Client._parse(b"OK 5000 5000 0 %d\n" % len(PIXEL_PNG) + PIXEL_PNG)            # larger than it keeps
        with self.assertRaises(raster.RasterError):
            raster.Client._parse(b"OK 2 2 0 %d\n" % len(PIXEL_PNG) + PIXEL_PNG)                  # not the PNG's own size
        with self.assertRaises(raster.RasterError) as cm:
            raster.Client._parse(b"ERR this SVG contains a script, which is not allowed\n")
        self.assertEqual((cm.exception.status, cm.exception.message), (422, "this SVG contains a script, which is not allowed"))

    def test_a_node_without_the_helper_says_so(self):
        client = raster.Client(socket_path=os.path.join(self.tmp, "nothing.sock"), env={})
        self.assertFalse(client.available())
        with self.assertRaises(raster.RasterError) as cm:
            client.normalise(png(2, 2))
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("not available", cm.exception.message)


# --- 2. the routes, and who may use them -----------------------------------------------------------------------------
OCTET = {"Content-Type": "application/octet-stream"}


class RoutesTest(RolesBase):
    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.api.graphics.client = self.client
        self.op = self.call("POST", "/api/devices/invite", {"name": "op", "role": "live"}, token=self.full)[1]["token"]

    def upload(self, name, data, token, replace=False, headers=OCTET):
        path = "/api/graphics/upload?name=" + name + ("&replace=1" if replace else "")
        return self.call("POST", path, raw=data, headers=headers, token=token)

    def test_the_owner_and_the_operator_upload_list_and_see_thumbnails(self):
        for who, name in ((self.full, "owner.png"), (self.op, "operator.jpg")):
            st, body, _ = self.upload(name, png(3, 3) if name.endswith("png") else JPEG_RGB, who)
            self.assertEqual(st, 200, body)
            self.assertEqual(body["name"], os.path.splitext(name)[0] + ".png")
        for who in (self.full, self.op):
            st, body, _ = self.call("GET", "/api/graphics/file", token=who)
            self.assertEqual(st, 200)
            self.assertEqual([f["name"] for f in body["files"]], ["operator.png", "owner.png"])
            self.assertEqual(body["limits"]["total_bytes"], 100 * 1024 * 1024)
            st, body, resp = self.call("GET", "/api/graphics/thumb?name=owner.png", token=who)
            self.assertEqual((st, resp.getheader("Content-Type"), body), (200, "image/png", PIXEL_PNG))
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        st, body, _ = self.call("POST", "/api/graphics/file", {"rename": "owner.png", "new": "mara.png"}, token=self.op)
        self.assertEqual((st, body), (200, {"name": "mara.png"}))
        st, body, _ = self.call("POST", "/api/graphics/file", {"delete": "mara.png"}, token=self.full)
        self.assertEqual((st, body), (200, {"deleted": "mara.png"}))
        self.assertEqual(sorted(os.listdir(self.api.graphics.folder))[-1], "operator.png")

    def test_a_guest_and_a_stranger_reach_none_of_it(self):
        self.upload("o.png", png(2, 2), self.full)
        calls = len(self.client.calls)
        for token, want in ((self.guest_token, 403), (None, 401)):
            self.assertEqual(self.upload("g.png", png(2, 2), token)[0], want)
            self.assertEqual(self.call("GET", "/api/graphics/file", token=token)[0], want)
            self.assertEqual(self.call("GET", "/api/graphics/thumb?name=o.png", token=token)[0], want)
            self.assertEqual(self.call("POST", "/api/graphics/file", {"delete": "o.png"}, token=token)[0], want)
            self.assertEqual(self.call("POST", "/api/graphics/file", {"rename": "o.png", "new": "p.png"}, token=token)[0], want)
        self.assertEqual(len(self.client.calls), calls)
        self.assertEqual(os.listdir(self.api.graphics.folder).count("o.png"), 1)
        self.assertNotIn("g.png", os.listdir(self.api.graphics.folder))
        # open guest controls do not change that: the upload is not among the forms a Guest may send
        self.lock(False)
        self.assertEqual(self.upload("g.png", png(2, 2), self.guest_token)[0], 403)
        self.assertEqual(self.call("GET", "/api/graphics/file", token=self.guest_token)[0], 403)

    def test_upload_needs_a_length_the_right_type_and_the_request_header(self):
        self.assertEqual(self.upload("a.png", png(2, 2), self.op, headers={"Content-Type": "image/png"})[0], 415)
        self.assertEqual(self.call("POST", "/api/graphics/upload?name=a.png", raw=png(2, 2), headers=OCTET, token=self.op, csrf=False)[0], 403)
        st, body, _ = self.call("POST", "/api/graphics/upload?name=a.png", raw=b"", headers=OCTET, token=self.op)
        self.assertEqual(st, 411)
        self.assertEqual(self.client.calls, [])

    def test_plain_reasons_come_back_and_nothing_is_decoded_for_a_refusal(self):
        cases = [("a.png", b"GIF89a" + b"\0" * 40, 415, "Only PNG, JPEG and SVG"), ("a.jpg", png(2, 2), 415, "PNG"),
                 ("a.png", b"", 411, "Content-Length"), ("a.jpg", JPEG_CMYK, 415, "CMYK"), ("a.png", b"hello", 415, "Only PNG"),
                 ("a.png.exe", png(2, 2), 415, "Rename it"), ("a.png", SVG_OK, 415, "SVG")]
        for name, data, status, words in cases:
            st, body, _ = self.upload(name, data, self.op)
            self.assertEqual(st, status, (name, body))
            self.assertIn(words, body["error"])
        for name in ("..%2Fa.png", "a%2Fb.png", ".hidden.png", "a%00.png", "%E2%80%AEtxt.png", "x" * 300 + ".png"):
            st, body, _ = self.upload(name, png(2, 2), self.op)
            self.assertEqual(st, 400, (name, body))
            self.assertIn("file name", body["error"])
        self.assertEqual(self.client.calls, [])
        folder = self.api.graphics.folder
        self.assertEqual(sorted(n for n in os.listdir(folder) if not n.startswith(".")) if os.path.isdir(folder) else [], [])

    def test_a_too_large_file_is_refused_before_any_of_it_is_read(self):
        def never(n):
            raise AssertionError("read %d bytes of a file that was refused by its length" % n)
        with self.assertRaises(api_mod.ApiError) as cm:
            self.api.graphics_upload("a.png", 11 * 1024 * 1024, never)
        self.assertEqual((cm.exception.status, cm.exception.message), (413, "A picture may be at most 10 MB."))
        # an SVG is told its own limit after the first bytes, before the rest comes
        head = b"<svg " + b" " * 600
        sent = []

        def read(n):
            sent.append(n)
            if len(sent) > 1:
                raise AssertionError("read on after an SVG over 1 MB")
            return head[:n]
        with self.assertRaises(api_mod.ApiError) as cm:
            self.api.graphics_upload("a.svg", 2 * 1024 * 1024, read)
        self.assertEqual((cm.exception.status, cm.exception.message), (413, "An SVG file may be at most 1 MB."))
        self.assertEqual(self.client.calls, [])

    def test_the_helper_missing_is_a_plain_503_not_a_crash(self):
        self.api.graphics.client = raster.Client(socket_path=os.path.join(self.tmp, "no.sock"), env={})
        st, body, _ = self.upload("a.png", png(2, 2), self.op)
        self.assertEqual(st, 503)
        self.assertIn("not available", body["error"])
        st, body, _ = self.call("GET", "/api/graphics/file", token=self.op)
        self.assertEqual((st, body["available"]), (200, False))

    def test_a_cut_short_slow_or_revoked_upload_stores_nothing(self):
        data = png(2, 2)
        parts = iter([data, b""])
        with self.assertRaises(api_mod.ApiError) as cm:
            self.api.graphics_upload("a.png", len(data) + 10, lambda n: next(parts), False)
        self.assertEqual(cm.exception.status, 400)
        clock = iter([0.0, 0.0] + [100.0] * 50)
        chunks = iter([b"\x89PNG"] * 10)
        with self.assertRaises(api_mod.ApiError) as cm:
            self.api.graphics_upload("a.png", 1000, lambda n: next(chunks), False, clock=lambda: next(clock))
        self.assertEqual(cm.exception.status, 408)

        def revoked():
            raise api_mod.ApiError(403, "this device was removed")
        with self.assertRaises(api_mod.ApiError) as cm:
            self.api.graphics_upload("a.png", len(data), lambda n: data, False, check=revoked)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.api._upload_lock.locked())

    def test_only_one_upload_at_a_time_and_not_beside_a_clip_upload(self):
        self.assertTrue(self.api._upload_lock.acquire(blocking=False))
        try:
            st, body, _ = self.upload("a.png", png(2, 2), self.op)
        finally:
            self.api._upload_lock.release()
        self.assertEqual((st, body["error"]), (409, "another upload is in progress"))


# --- 3. the unit files and the installer, read as text -------------------------------------------------------------
def unit_keys(path):
    keys = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"^([A-Za-z]+)=(.*)$", line.strip())
            if m:
                keys.setdefault(m.group(1), []).append(m.group(2))
    return keys


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.service = unit_keys(os.path.join(HERE, "install", "pvj-raster@.service"))
        self.socket = unit_keys(os.path.join(HERE, "install", "pvj-raster.socket"))
        with open(os.path.join(HERE, "install", "install.sh")) as f:
            self.install = f.read()

    def test_the_helper_has_nothing_to_reach_and_nothing_to_raise(self):
        s = self.service
        self.assertEqual((s["User"], s["Group"]), (["pvj-raster"], ["pvj-raster"]))
        for key in ("NoNewPrivileges", "PrivateNetwork", "PrivateTmp", "PrivateDevices", "ProtectHome", "ProtectKernelTunables",
                    "ProtectKernelModules", "ProtectControlGroups", "RestrictNamespaces", "RestrictRealtime", "RestrictSUIDSGID",
                    "LockPersonality", "MemoryDenyWriteExecute"):
            self.assertEqual(s[key], ["yes"], key)
        self.assertEqual(s["ProtectSystem"], ["strict"])
        self.assertEqual(s["CapabilityBoundingSet"], [""])
        self.assertEqual(s["RestrictAddressFamilies"], ["AF_UNIX"])
        self.assertNotIn("ReadWritePaths", s)
        self.assertNotIn("EnvironmentFile", s)          # it loads no configuration
        self.assertNotIn("AmbientCapabilities", s)
        self.assertEqual(s["StandardInput"], ["socket"])
        inaccessible = " ".join(s["InaccessiblePaths"])
        for folder in ("/var/lib/pvj", "/etc/pvj", "/run/pvj", "/run/pvj-sysd", "/run/pvj-supportd", "/run/pvj-link", "/run/pvj-sound"):
            self.assertIn(folder, [w.lstrip("-") for w in inaccessible.split()])
        filt = " ".join(s["SystemCallFilter"])
        self.assertIn("@system-service", filt)
        self.assertRegex(filt, r"~@privileged")
        for group in ("@resources", "@network-io", "@ipc", "@setuid"):
            self.assertIn(group, filt)

    def test_the_ceilings_are_the_designs(self):
        s = self.service
        self.assertEqual((s["LimitAS"], s["LimitCPU"], s["MemoryMax"], s["RuntimeMaxSec"]), (["512M"], ["10"], ["256M"], ["15"]))
        self.assertEqual(s["LimitCORE"], ["0"])
        self.assertIn("TasksMax", s)

    def test_one_helper_per_connection_and_only_the_panels_group_may_connect(self):
        k = self.socket
        self.assertEqual((k["Accept"], k["ListenStream"], k["SocketMode"], k["SocketGroup"]), (["yes"], ["/run/pvj-raster.sock"], ["0660"], ["pvj-raster"]))
        self.assertEqual(k["WantedBy"], ["sockets.target"])

    def test_the_installer_builds_it_makes_the_account_and_starts_the_socket_on_every_board(self):
        sh = self.install
        for want in ("pvj-raster@.service", "pvj-raster.socket", "useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid pvj-raster pvj-raster",
                     "usermod -aG pvj-raster pvj-web", "make -C \"$RELEASE.new/render\" pvj-raster", "systemctl enable pvj-raster.socket",
                     "libpng-dev", "libjpeg-dev"):
            self.assertIn(want, sh)
        self.assertNotIn("usermod -aG pvj-raster \"$PVJ_USER\"", sh)         # the player is not in its group
        self.assertNotIn("usermod -aG pvj-raster pvj-sound", sh)
        # no condition on the board: nothing between the build and the enable asks which board this is
        build = sh[sh.index("# The picture helper (D129), built the same way"):sh.index("previous=\"\"")]
        self.assertNotIn("tier", build)
        self.assertNotIn("board", build)
        with open(os.path.join(HERE, "image", "stage-pvj", "00-install-pvj", "00-packages-nr")) as f:
            packages = f.read().split()
        self.assertIn("libpng-dev", packages)
        self.assertIn("libjpeg-dev", packages)

    def test_the_makefile_builds_it_with_warnings_as_errors_available(self):
        with open(os.path.join(HERE, "render", "Makefile")) as f:
            text = f.read()
        self.assertIn("pvj-raster: raster/pvj-raster.c", text)
        self.assertIn("$(WARN)", text[text.index("pvj-raster: raster"):])
        self.assertIn("-isystem raster/vendor", text)


# --- 4. the real program ---------------------------------------------------------------------------------------------
@unittest.skipUnless(REAL, "PVJ_RASTER_TEST=1 runs the real pvj-raster")
class RealRasterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.access(HELPER, os.X_OK):
            raise unittest.SkipTest("make -C render pvj-raster first")

    def setUp(self):
        self.client = raster.Client(exec_bin=HELPER, env={})
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_ok(self, data):
        meta, out = self.client.normalise(data)
        img = read_png(out)
        self.assertEqual((img[0], img[1]), (meta["width"], meta["height"]))
        return meta, img, out

    def refused(self, data, words, status=422):
        started = time.monotonic()
        with self.assertRaises(raster.RasterError) as cm:
            self.client.normalise(data)
        self.assertEqual(cm.exception.status, status, cm.exception.message)
        self.assertIn(words, cm.exception.message)
        self.assertLess(time.monotonic() - started, 16)
        return cm.exception.message

    # good files
    def test_png_in_every_common_shape(self):
        meta, img, out = self.run_ok(png(40, 20, lambda x, y: (255, 0, 0) if x < 20 else (0, 0, 255)))
        self.assertEqual((img[0], img[1], img[2], meta["alpha"]), (40, 20, 3, False))                # no alpha: stored as RGB
        self.assertEqual(pixel_at(img, 5, 5), (255, 0, 0, 255))
        self.assertEqual(pixel_at(img, 30, 5), (0, 0, 255, 255))
        meta, img, _ = self.run_ok(png(8, 8, lambda x, y: (0, 255, 0, 255 if x < 4 else 0), color=6))
        self.assertEqual((meta["alpha"], img[2]), (True, 4))
        self.assertEqual(pixel_at(img, 1, 1), (0, 255, 0, 255))
        self.assertEqual(pixel_at(img, 6, 1)[3], 0)
        meta, img, _ = self.run_ok(png(8, 8, lambda x, y: (65535, 0, 0), depth=16))                    # 16 bit becomes 8
        self.assertEqual(pixel_at(img, 2, 2), (255, 0, 0, 255))
        meta, img, _ = self.run_ok(png(4, 4, lambda x, y: (x % 2,), color=3, depth=8, palette=bytes([0, 0, 255, 255, 255, 0]), trns=bytes([0])))
        self.assertTrue(meta["alpha"])                                                                  # the palette's transparent entry
        self.assertEqual(pixel_at(img, 0, 0)[3], 0)
        self.assertEqual(pixel_at(img, 1, 0), (255, 255, 0, 255))
        meta, img, _ = self.run_ok(png(6, 6, lambda x, y: (128,), color=0))                             # grey
        self.assertEqual(pixel_at(img, 3, 3), (128, 128, 128, 255))

    def test_a_large_png_is_scaled_to_2048_on_its_long_side(self):
        meta, img, _ = self.run_ok(png(3000, 1500, lambda x, y: (255, 0, 0) if x < 1500 else (0, 0, 255)))
        self.assertEqual((meta["width"], meta["height"]), (2048, 1024))
        self.assertEqual(pixel_at(img, 100, 100), (255, 0, 0, 255))
        self.assertEqual(pixel_at(img, 1900, 900), (0, 0, 255, 255))
        meta, _, _ = self.run_ok(png(1500, 3000))
        self.assertEqual((meta["width"], meta["height"]), (1024, 2048))
        meta, _, _ = self.run_ok(png(2048, 100))                                                        # never scaled up, never down at the limit
        self.assertEqual((meta["width"], meta["height"]), (2048, 100))

    def test_the_output_is_clean_whatever_the_file_carried(self):
        secret = b"Comment\x00the secret in a text chunk"
        dirty = png(10, 10, extra=[(b"tEXt", secret), (b"iCCP", b"icc\x00\x00" + zlib.compress(b"not a profile")), (b"eXIf", b"MM\x00*" * 5),
                                   (b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta/>"), (b"tIME", b"\x07\xea\x0b\x08\x01\x02\x03"),
                                   (b"acTL", struct.pack(">II", 2, 0))],
                    trailer=b"<?php system($_GET['c']); ?>" + b"PK\x03\x04" * 20)
        _, img, out = self.run_ok(dirty)
        self.assertEqual(sorted(set(img[4])), ["IDAT", "IEND", "IHDR"])
        for leaked in (b"secret", b"xmpmeta", b"icc", b"php", b"PK\x03\x04", b"eXIf", b"tEXt"):
            self.assertNotIn(leaked, out)

    def test_jpeg_in_its_kinds_and_with_its_orientation(self):
        _, img, _ = self.run_ok(JPEG_RGB)
        self.assertEqual((img[0], img[1]), (32, 16))
        self.assertGreater(pixel_at(img, 3, 8)[0], 200)
        self.assertGreater(pixel_at(img, 28, 8)[2], 200)
        _, img, _ = self.run_ok(JPEG_PROGRESSIVE)
        self.assertEqual((img[0], img[1]), (32, 16))
        _, img, _ = self.run_ok(JPEG_GREY)
        self.assertEqual(pixel_at(img, 2, 2)[:3], pixel_at(img, 2, 2)[:1] * 3)
        # the picture is turned the way its Exif block says, and no metadata survives
        _, img, out = self.run_ok(jpeg_with_orientation(JPEG_RGB, 6))                                   # to be turned 90 degrees clockwise
        self.assertEqual((img[0], img[1]), (16, 32))
        self.assertGreater(pixel_at(img, 8, 2)[0], 200)                                                 # the left (red) half is now the top
        self.assertGreater(pixel_at(img, 8, 29)[2], 200)
        self.assertNotIn(b"Exif", out)
        _, img, _ = self.run_ok(jpeg_with_orientation(JPEG_RGB, 8))                                     # counter-clockwise: the left half goes to the bottom
        self.assertGreater(pixel_at(img, 8, 29)[0], 200)
        _, img, _ = self.run_ok(jpeg_with_orientation(JPEG_RGB, 2))                                     # mirrored
        self.assertGreater(pixel_at(img, 3, 8)[2], 200)
        _, img, _ = self.run_ok(jpeg_with_orientation(JPEG_RGB, 3))
        self.assertGreater(pixel_at(img, 3, 8)[2], 200)
        _, img, _ = self.run_ok(jpeg_with_orientation(JPEG_RGB, 99))                                    # nonsense is ignored
        self.assertGreater(pixel_at(img, 3, 8)[0], 200)
        _, img, _ = self.run_ok(jpeg_with_xmp_then_orientation(JPEG_RGB, 6))                            # an XMP APP1 first: the Exif one is still used
        self.assertEqual((img[0], img[1]), (16, 32))

    def test_svg_is_drawn_to_2048_and_not_kept(self):
        meta, img, _ = self.run_ok(SVG_OK)
        self.assertEqual((meta["width"], meta["height"]), (2048, 1024))
        self.assertEqual(pixel_at(img, 300, 500)[:3], (255, 0, 0))
        self.assertEqual(pixel_at(img, 1700, 500)[:3], (0, 0, 255))
        grad = svg(b'<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient></defs>'
                   b'<circle cx="50" cy="25" r="20" fill="url(#g)" stroke="#f0f" stroke-width="2"/>')
        meta, img, _ = self.run_ok(grad)
        self.assertTrue(meta["alpha"])                                                                  # the corners are empty
        self.assertEqual(pixel_at(img, 2, 2)[3], 0)
        meta, _, _ = self.run_ok(svg(b'<rect width="10" height="10"/>', head=b'width="10" height="40"'))
        self.assertEqual((meta["width"], meta["height"]), (512, 2048))
        self.run_ok(svg(b'<rect width="10" height="10"/>', pre=XML + b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'))
        self.run_ok(svg(b'<use href="#a"/><g id="a"><rect width="5" height="5"/></g>'))                 # a reference inside the file is fine

    def test_the_thumbnail_is_made_from_the_stored_png_only(self):
        _, _, stored = self.run_ok(png(1200, 600))
        thumb = read_png(self.client.thumbnail(stored))
        self.assertEqual((thumb[0], thumb[1]), (256, 128))
        small = read_png(self.client.thumbnail(png(10, 10)))
        self.assertEqual((small[0], small[1]), (10, 10))
        with self.assertRaises(raster.RasterError):
            self.client.thumbnail(JPEG_RGB)
        with self.assertRaises(raster.RasterError):
            self.client.thumbnail(SVG_OK)

    # wrong types and sizes (the program checks again what the panel already checked)
    def test_the_program_refuses_what_it_is_given_even_if_the_panel_did_not(self):
        self.refused(b"GIF89a" + b"\0" * 40, "only PNG, JPEG and SVG")
        self.refused(b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\0" * 30, "only PNG, JPEG and SVG")
        self.refused(b"%PDF-1.7\n" + b"x" * 60, "only PNG, JPEG and SVG")
        self.refused(b"\x89PNG\r\n\x1a\n" + b"\0" * (11 * 1024 * 1024), "larger than 10 MB")
        self.refused(b"<svg>" + b" " * (1024 * 1024 + 1), "at most 1 MB")
        self.refused(b"abc", "too short")
        self.refused(big_png(8193, 8), "8192")
        self.refused(big_png(8192, 4883), "40 million")
        self.refused(JPEG_CMYK, "CMYK")

    # damaged files
    def test_malformed_and_truncated_png(self):
        good = png(50, 50, lambda x, y: (x * 5 % 256, y * 5 % 256, 99))
        self.run_ok(good)
        self.refused(good[:len(good) // 2], "damaged")
        self.refused(good[:40], "damaged")
        self.refused(good[:-12], "damaged")                                                              # IEND cut off
        flipped = bytearray(good)
        flipped[-20] ^= 0xFF                                                                             # inside the image data
        self.refused(bytes(flipped), "damaged")
        self.refused(b"\x89PNG\r\n\x1a\n" + b"\xff" * 200, "damaged")
        self.refused(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 2, 0, 0, 0)), "damaged")      # no image data
        self.refused(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 7, 2, 0, 0, 0)) + chunk(b"IEND", b""), "damaged")
        bad_filter = bytearray(png(4, 4))
        bad_ihdr = bytes(bad_filter[:8]) + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 9)) + bytes(bad_filter[33:])
        self.refused(bad_ihdr, "damaged")                                                                # interlace method 9
        # a small file that claims a big picture and holds a few pixels' worth of data
        liar = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8000, 4000, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0" * 1000)) + chunk(b"IEND", b"")
        self.refused(liar, "damaged")

    def test_malformed_and_truncated_jpeg(self):
        self.refused(JPEG_RGB[:len(JPEG_RGB) // 2], "JPEG")
        self.refused(JPEG_RGB[:300], "JPEG")
        self.refused(JPEG_PROGRESSIVE[:len(JPEG_PROGRESSIVE) - 60], "JPEG")
        self.refused(b"\xff\xd8\xff" + b"\x00" * 100, "JPEG")
        self.refused(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(200), "JPEG")
        garbled = bytearray(JPEG_RGB)
        for i in range(len(garbled) - 120, len(garbled) - 4):
            garbled[i] = 0xFF if i % 7 == 0 else garbled[i] ^ 0x5A
        try:
            self.client.normalise(bytes(garbled))
        except raster.RasterError as e:
            self.assertEqual(e.status, 422)
        self.refused(JPEG_RGB.replace(b"\xff\xc0", b"\xff\xc3", 1), "JPEG")                               # the program refuses lossless too

    # decompression bombs
    def test_a_large_allowed_picture_of_one_colour_is_decoded_in_bounds(self):
        started = time.monotonic()
        meta, out = self.client.normalise(big_png(8192, 4880))                                           # 39.98 million pixels in a few KB
        self.assertEqual((meta["width"], meta["height"]), (2048, 1220))
        self.assertLess(time.monotonic() - started, 15)
        self.assertLess(len(out), 200 * 1024)

    # hostile SVG
    def test_svg_with_scripts_references_entities_and_text_is_refused_in_words(self):
        cases = [
            (svg(b'<script>alert(1)</script><rect width="9" height="9"/>'), "script"),
            (svg(b'<SCRIPT type="text/javascript">x</SCRIPT>'), "script"),
            (svg(b'<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'), "foreignObject"),
            (svg(b'<image href="http://example.com/a.png" width="10" height="10"/>'), "picture"),
            (svg(b'<image xlink:href="data:image/png;base64,AAAA" width="10" height="10"/>'), "picture"),
            (svg(b'<use href="http://example.com/a.svg#x"/>'), "outside the file"),
            (svg(b'<use xlink:href="file:///etc/passwd"/>'), "outside the file"),
            (svg(b'<rect width="9" height="9" fill="url(http://example.com/x)"/>'), "outside the file"),
            (svg(b'<rect style="fill:url( \'https://e.com/x\' )" width="9" height="9"/>'), "outside the file"),
            (svg(b'<style>@import url(http://example.com/a.css);</style>'), "style sheet"),
            (svg(b'<rect width="9" height="9"/>', pre=XML + b'<?xml-stylesheet href="http://example.com/a.css" type="text/css"?>'), "style sheet"),
            (svg(b'<text x="5" y="5">Hi</text>'), "Convert the text to outlines"),
            (svg(b'<TEXT>Hi</TEXT>'), "Convert the text to outlines"),
            (svg(b'<g/>', pre=XML + b'<!DOCTYPE svg [<!ENTITY a "aaaa">]>'), "entities"),
            (svg(b'<g/>', pre=XML + b'<!DOCTYPE svg [ ]>'), "DTD"),
            (b'<?xml version="1.0"?>\n<!DOCTYPE lolz [\n<!ENTITY lol "lol">\n<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
             b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
             b'<!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">\n]>\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><desc>&lol5;</desc></svg>', "entities"),
            (b'<?xml version="1.0"?><!DOCTYPE svg SYSTEM "file:///etc/passwd" [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><desc>&xxe;</desc></svg>', "entities"),
        ]
        for data, words in cases:
            message = self.refused(data, words)
            self.assertNotIn("\n", message)

    def test_svg_that_is_odd_but_not_forbidden_gives_an_answer_in_time(self):
        def done(data):
            started = time.monotonic()
            try:
                self.client.normalise(data)
            except raster.RasterError as e:
                self.assertEqual(e.status, 422, e.message)
            self.assertLess(time.monotonic() - started, 16)
        done(b"<svg>" + b"<g>" * 100000 + b"</svg>")                                                       # about 300 KB of nothing but <g>
        done(b"<svg width='10' height='10'>" + b"<g>" * 340000 + b"</svg>")                                 # a megabyte of it, never closed
        done(svg(b"<g>" * 3000 + b'<rect width="9" height="9"/>' + b"</g>" * 3000))                         # nested very deep
        done(svg(b'<rect width="1e30" height="1e30" x="-1e30"/><circle r="1e38" cx="1e38" cy="1e38"/>'))     # huge coordinates
        done(b'<svg width="1e30" height="1e30"><rect width="9" height="9"/></svg>')
        done(b'<svg width="0" height="0"><rect width="9" height="9"/></svg>')
        done(b"<svg>\x00\x00<rect/></svg>")
        done(b"<?xml version='1.0'?>" + b"<a>" * 1000)                                                       # not an SVG at all

    def test_an_svg_with_too_many_points_is_refused(self):
        path = "M0 0 " + "L1 1 L2 0 " * 4000
        self.refused(svg(('<path d="%s"/>' % path).encode()), "too complicated")
        self.run_ok(svg(('<path d="%s"/>' % ("M0 0 " + "L1 1 L2 0 " * 500)).encode()))

    # the program on its own
    def test_the_program_reads_one_request_and_nothing_else(self):
        def talk(request):
            done = subprocess.run([HELPER, "--self-limit"], input=request, capture_output=True, timeout=20, env={})
            return done.returncode, done.stdout
        for request in (b"", b"hello\n", b"RASTER1 IMG\n", b"RASTER1 IMG -5\n", b"RASTER1 IMG 99999999999\n", b"RASTER1 THUMB 5 100\n" + b"x" * 100,
                        b"RASTER1 THUMB 9999 100\n", b"RASTER1 ZIP 10\n" + b"x" * 10, b"RASTER1 IMG 100\n" + b"x" * 10, b"\xff" * 500, b"A" * 5000):
            code, out = talk(request)
            self.assertEqual(code, 0, request[:30])
            self.assertTrue(out.startswith(b"ERR "), (request[:30], out[:60]))
        code, out = talk(b"RASTER1 IMG %d\n" % len(PIXEL_PNG) + PIXEL_PNG + b"RASTER1 IMG 3\nabc")        # a second request behind the first is not read
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"OK 1 1 0 "))
        self.assertEqual(out.count(b"OK "), 1)

    def test_the_programs_own_code_opens_no_file_and_makes_no_connection(self):
        # a look at the source (the vendored SVG reader has a file loader nobody calls; it is not our code and is not read here)
        with open(os.path.join(HERE, "render", "raster", "pvj-raster.c")) as f:
            code = re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", f.read()), flags=re.S)
        code = code.replace("png_create", "").replace("jpeg_create", "").replace("must(", "")
        for banned in (r"\bfopen\(", r"\bopen\(", r"\bsocket\(", r"\bconnect\(", r"\bsystem\(", r"\bpopen\(", r"\bexec[a-z]*\(",
                       r"\bgetenv\(", r"\bdlopen\(", r"\bfork\(", r"\bchdir\(", r"\bunlink\("):
            self.assertIsNone(re.search(banned, code), banned)

    def test_it_honours_the_ceiling_on_memory(self):
        # run with a small address space: the 40 MP picture cannot be held, and the answer is a refusal, not a crash
        script = "ulimit -v 100000; exec %s --self-limit" % HELPER
        request = b"RASTER1 IMG %d\n" % len(big_png(8192, 4880))
        data = big_png(8192, 4880)
        done = subprocess.run(["bash", "-c", script], input=request + data, capture_output=True, timeout=20, env={})
        self.assertTrue(done.stdout.startswith(b"ERR "), done.stdout[:80] + done.stderr[:80])
        self.assertIn(b"memory", done.stdout)



# --- 5. the real program's text and logo jobs (D129, slice 2) ----------------------------------------------------------------
def text_params(text, font="archivo", size=100, extra="", limit="1920 1080", out="raw", weight=None):
    lines = ["kind text", "out " + out, "font " + font, "size %s" % size, "colour ffffff", "limit " + limit]
    if weight:
        lines.append("weight %d" % weight)
    return "\n".join(lines + ([extra] if extra else []) + ["TEXT", text])


def logo_params(extra="", out="raw", limit="1920 1080"):
    return "\n".join(["kind logo", "out " + out, "limit " + limit] + ([extra] if extra else []))


def bgra_pixels(done):
    d = done["data"]
    return [tuple(d[i:i + 4]) for i in range(0, len(d), 4)]


@unittest.skipUnless(REAL, "PVJ_RASTER_TEST=1 runs the real pvj-raster")
class RealGraphicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.access(HELPER, os.X_OK):
            raise unittest.SkipTest("make -C render pvj-raster first")

    def setUp(self):
        self.client = raster.Client(exec_bin=HELPER, env={})

    def draw(self, params, payload=b""):
        started = time.monotonic()
        done = self.client.graphic(params, payload)
        self.assertLess(time.monotonic() - started, 16)
        return done

    def refused(self, params, words, payload=b""):
        with self.assertRaises(raster.RasterError) as cm:
            self.client.graphic(params, payload)
        self.assertEqual(cm.exception.status, 422, cm.exception.message)
        self.assertIn(words, cm.exception.message)

    # text
    def test_a_text_is_premultiplied_blue_green_red_alpha_and_cut_to_the_ink(self):
        done = self.draw(text_params("HELLO", size=120, extra="colour ff8000"))
        self.assertFalse(done["png"])
        w, h, px = done["w"], done["h"], bgra_pixels(done)
        self.assertEqual(len(px), w * h)
        self.assertTrue(40 < h < 200 and 150 < w < 700, (w, h))
        solid = [p for p in px if p[3] == 255]
        self.assertTrue(solid)
        self.assertTrue(all(p[:3] == (0, 128, 255) for p in solid), "orange, blue first: B G R A")
        self.assertTrue(all(p[0] <= p[3] and p[1] <= p[3] and p[2] <= p[3] for p in px), "premultiplied: no colour above its alpha")
        edge = [p for p in px if 0 < p[3] < 255]
        self.assertTrue(edge, "antialiased edges")
        self.assertTrue(any(px[i * w + j][3] for i in range(h) for j in (0, w - 1)) and any(px[j * w + i][3] for i in range(w) for j in (0, h - 1)),
                        "no empty margin: the picture is cut to the ink on every side")

    def test_every_bundled_font_draws_and_a_weight_changes_a_variable_one(self):
        from pvj import graphics
        for f in graphics.FONTS:
            done = self.draw(text_params("AaBbCc 0123", font=f["id"], size=80))
            self.assertEqual(done["missing"], 0, f["id"])
            self.assertGreater(done["w"], 100, f["id"])
        light = self.draw(text_params("HHHH", font="oswald", weight=200))
        bold = self.draw(text_params("HHHH", font="oswald", weight=700))
        self.assertLess(sum(p[3] for p in bgra_pixels(light)), sum(p[3] for p in bgra_pixels(bold)), "a heavier weight has more ink")
        again = self.draw(text_params("HHHH", font="oswald", weight=700))
        self.assertEqual(again["data"], bold["data"], "the same request draws the same picture")
        clamped = self.draw(text_params("HHHH", font="oswald", weight=999))
        self.assertEqual(clamped["data"], bold["data"], "a weight outside the font's own is the nearest it has")

    def test_latin_with_accents_cyrillic_and_greek_draw_and_what_no_font_has_is_counted(self):
        for text in ("École à la plage", "Жара Мара", "Ωμέγα Αθήνα"):
            for font in ("bebas-neue", "archivo", "press-start-2p"):
                self.assertEqual(self.draw(text_params(text, font=font))["missing"], 0, (text, font))     # a missing letter comes from Noto Sans
        self.assertEqual(self.draw(text_params("ok \U0001F600 \U0001F680"))["missing"], 2, "emoji are not in the first release")
        self.assertEqual(self.draw(text_params("a​b﻿c"))["missing"], 0, "invisible marks are skipped, not boxes")

    def test_a_long_text_is_made_smaller_to_fit_the_screen_and_never_cut(self):
        done = self.draw(text_params("W" * 120, size=200, limit="1728 972"))
        self.assertLessEqual(done["w"], 1728)
        self.assertGreater(done["w"], 1500, "it was made smaller, not cut: it still fills the width")
        tall = self.draw(text_params("\n".join(["MMM"] * 6), size=400, limit="1728 972"))
        self.assertLessEqual(tall["h"], 972)
        self.refused(text_params("\n".join(["x"] * 7)), "6 lines")
        self.refused(text_params("W" * 140, size=200, limit="16 16"), "too much text")

    def test_there_is_no_markup_a_tag_or_a_brace_is_a_letter(self):
        plain = self.draw(text_params("hi"))
        tagged = self.draw(text_params("{\\an8}{\\fs200}<b>hi</b>"))
        self.assertGreater(tagged["w"], plain["w"] * 3, "every character of it is drawn")
        # a typed line cannot become a setting: it is only ever after the line that ends the settings
        sneaky = self.draw(text_params("a\nfont nonexistent\nsize 4000\ncolour 00ff00", size=50))
        self.assertLess(sneaky["h"], 400)
        self.assertEqual(sneaky["missing"], 0)

    def test_outline_shadow_and_plate_add_to_the_picture_in_their_colours(self):
        base = self.draw(text_params("OO", size=100))
        outlined = self.draw(text_params("OO", size=100, extra="outline 0.06 ff0000"))
        self.assertGreater(outlined["w"], base["w"] + 6)
        self.assertTrue(any(p[:3] == (0, 0, 255) for p in bgra_pixels(outlined)), "red ink in the ring (blue first)")
        shadow = self.draw(text_params("OO", size=100, extra="shadow 0.05 0.05 0.04 000000 80"))
        self.assertGreater(shadow["w"], base["w"])
        self.assertTrue(any(0 < p[3] < 255 and p[:3] == (0, 0, 0) for p in bgra_pixels(shadow)), "a soft black shadow")
        plate = self.draw(text_params("OO", size=100, extra="plate 0.3 0000ff 50"))
        self.assertGreater(plate["w"], base["w"] + 30)
        px = bgra_pixels(plate)
        self.assertEqual(px[0], (128, 0, 0, 128), "a half-clear blue plate, premultiplied, blue first: the corner is the plate")

    def test_alignment_places_a_short_line_under_a_long_one(self):
        def ink_left(align):
            done = self.draw(text_params("WWWWWWWW\nW", size=60, extra="align " + align))
            w, h, px = done["w"], done["h"], bgra_pixels(done)
            cols = [j for j in range(w) if any(px[i * w + j][3] > 200 for i in range(h // 2 + 2, h))]       # the second line fills the lower half
            return cols[0], w
        left, w = ink_left("left")
        centre, _ = ink_left("center")
        right, _ = ink_left("right")
        self.assertLess(left, 6)
        self.assertTrue(left + 20 < centre < right - 20, (left, centre, right))

    def test_opacity_scales_every_channel_and_a_preview_is_a_png_in_a_canvas_with_straight_alpha(self):
        full = self.draw(text_params("HH", size=80))
        half = self.draw(text_params("HH", size=80, extra="opacity 50"))
        self.assertEqual(max(p[3] for p in bgra_pixels(half)), 128)
        self.assertTrue(all(p[0] <= p[3] for p in bgra_pixels(half)))
        self.assertEqual((full["w"], full["h"]), (half["w"], half["h"]))
        prev = self.draw(text_params("HH", size=80, extra="canvas 320 180 100 60", out="png"))
        self.assertTrue(prev["png"])
        img = read_png(prev["data"])
        self.assertEqual((img[0], img[1]), (320, 180))
        self.assertEqual(pixel_at(img, 0, 0), (0, 0, 0, 0), "a clear canvas")
        solid = [(x, y) for y in range(180) for x in range(320) if pixel_at(img, x, y)[3] == 255]
        self.assertTrue(solid)
        self.assertTrue(all(pixel_at(img, x, y)[:3] == (255, 255, 255) for x, y in solid), "straight, not premultiplied")
        self.assertGreaterEqual(min(x for x, _ in solid), 100)
        self.assertGreaterEqual(min(y for _, y in solid), 60)

    def test_what_is_not_understood_is_refused_in_words(self):
        for params, words in ((text_params("x", font="nonexistent"), "not on this node"), (text_params("x", font="../etc/passwd"), "not on this node"),
                              (text_params("x", font="ARCHIVO"), "not on this node"), (text_params("x", font="a b"), "not on this node"),
                              (text_params("x", extra="colour red"), "colour"), (text_params("x", extra="outline 0.1 zzzzzz"), "outline"),
                              (text_params("x", extra="shadow 1 2"), "shadow"), (text_params("x", extra="plate 0.1"), "back plate"),
                              (text_params(""), "no text"), ("kind text\nfont archivo\n", "no text")):
            self.refused(params, words)
        self.refused(text_params("x" * 2000), "too long")
        self.refused("kind logo\nout raw\n", "no logo")
        self.refused(logo_params(), "stored PNG", payload=JPEG_RGB)
        self.refused(logo_params(), "stored PNG", payload=b"not a picture at all")

    def test_a_text_job_without_fonts_says_so_and_a_font_file_is_read_only_by_its_checked_name(self):
        request = text_params("hi").encode()
        done = subprocess.run([HELPER, "--self-limit"], input=b"RASTER1 GFX %d 0\n" % len(request) + request, capture_output=True, timeout=20, env={})
        self.assertIn(b"no fonts", done.stdout)
        with tempfile.TemporaryDirectory() as folder:
            os.symlink("/etc/hostname", os.path.join(folder, "x.ttf"))
            done = subprocess.run([HELPER, "--self-limit", "--fonts", folder], input=b"RASTER1 GFX %d 0\n" % len(request) + request.replace(b"font archivo", b"font x"),
                                  capture_output=True, timeout=20, env={})
            self.assertTrue(done.stdout.startswith(b"ERR "), done.stdout[:80])
            with open(os.path.join(folder, "bad.ttf"), "wb") as f:
                f.write(b"\x00\x01\x00\x00" + b"\xff" * 4000)
            done = subprocess.run([HELPER, "--self-limit", "--fonts", folder], input=b"RASTER1 GFX %d 0\n" % len(request) + request.replace(b"font archivo", b"font bad"),
                                  capture_output=True, timeout=20, env={})
            self.assertTrue(done.stdout.startswith(b"ERR "), done.stdout[:80])

    def test_a_picture_is_never_bigger_than_the_cap_and_a_huge_request_ends_in_words_in_time(self):
        started = time.monotonic()
        done = self.draw(text_params("\n".join(["MMMMMMMMMMMMMM"] * 6), size=900, limit="4096 4096"))
        self.assertLessEqual(done["w"] * done["h"], 9_000_000)
        self.assertLess(time.monotonic() - started, 16)

    # logos
    def logo(self, w=8, h=4):
        return png(w, h, lambda x, y: (255, 0, 0, 255) if x < w // 2 else (0, 0, 255, 128), color=6)

    def test_a_logo_is_scaled_to_the_width_asked_and_keeps_its_shape_and_its_transparency(self):
        done = self.draw(logo_params("width 64"), self.logo())
        self.assertEqual((done["w"], done["h"]), (64, 32))
        px = bgra_pixels(done)
        self.assertEqual(px[0], (0, 0, 255, 255), "red, opaque (blue first)")
        self.assertEqual(px[63][3], 128)
        self.assertTrue(all(p[0] <= p[3] and p[1] <= p[3] and p[2] <= p[3] for p in px), "premultiplied")
        self.assertEqual(px[63][:3], (128, 0, 0), "the half-clear blue, premultiplied")
        small = self.draw(logo_params("width 4"), self.logo(64, 32))
        self.assertEqual((small["w"], small["h"]), (4, 2), "made smaller by averaging, not by dropping pixels")

    def test_a_logo_turns_and_mirrors_and_a_logo_too_big_for_the_screen_is_made_smaller(self):
        quarter = self.draw(logo_params("width 64\nrotate 90"), self.logo())
        self.assertEqual((quarter["w"], quarter["h"]), (32, 64))
        turned = self.draw(logo_params("width 64\nrotate 45"), self.logo())
        self.assertTrue(turned["w"] > 64 and turned["h"] > 32, "the box of a turned picture is bigger")
        px = bgra_pixels(turned)
        self.assertEqual(px[0][3], 0, "a corner of the turned box is clear")
        mirrored = self.draw(logo_params("width 64\nmirror 1"), self.logo())
        mp = bgra_pixels(mirrored)
        self.assertEqual((mp[0][3], mp[63][:3]), (128, (0, 0, 255)), "left and right swapped")
        fit = self.draw(logo_params("width 4000\nrotate 30", limit="800 600"), self.logo())
        self.assertTrue(fit["w"] <= 800 and fit["h"] <= 600, (fit["w"], fit["h"]))

    def test_a_logo_has_opacity_and_a_preview_of_it(self):
        half = self.draw(logo_params("width 16\nopacity 50"), self.logo())
        self.assertEqual(max(p[3] for p in bgra_pixels(half)), 128)
        prev = self.draw(logo_params("width 16\ncanvas 64 32 8 4", out="png"), self.logo())
        img = read_png(prev["data"])
        self.assertEqual((img[0], img[1]), (64, 32))
        self.assertEqual(pixel_at(img, 8, 4), (255, 0, 0, 255))
        self.assertEqual(pixel_at(img, 0, 0)[3], 0)

    def test_a_damaged_logo_file_is_refused_not_drawn(self):
        good = self.logo()
        for bad in (good[:40], good[:-20], good[:-1]):
            with self.assertRaises(raster.RasterError):
                self.client.graphic(logo_params("width 16"), bad)
        self.assertEqual(self.draw(logo_params("width 16"), good)["w"], 16)

    def test_the_programs_graphics_code_opens_only_the_fonts_it_is_given_by_checked_name(self):
        with open(os.path.join(HERE, "render", "raster", "gfx.inc.c")) as f:
            code = re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", f.read()), flags=re.S)
        self.assertEqual(len(re.findall(r"\bfopen\(", code)), 1)
        self.assertIn('"%s/%s.ttf"', code)
        for banned in (r"\bopen\(", r"\bsocket\(", r"\bconnect\(", r"\bsystem\(", r"\bpopen\(", r"\bexec[a-z]*\(", r"\bgetenv\(", r"\bdlopen\(",
                       r"\bfork\(", r"\bchdir\(", r"\bunlink\(", r"\bwritefile\b", r"\bfopen\([^)]*\"w"):
            self.assertIsNone(re.search(banned, code), banned)
        self.assertRegex(code, r"'a' && \*q <= 'z'.*'0' && \*q <= '9'.*== '-'")


if __name__ == "__main__":
    unittest.main()
