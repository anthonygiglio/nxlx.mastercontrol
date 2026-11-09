# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Logos and other pictures people upload (D129, design/text-and-logo-sources_2026-11-08.md sections 5 and 7).

The panel never decodes an upload. It does the cheap checks that need no decoder (size, the type from the first
bytes, the sizes written in a PNG or JPEG header, the kind of JPEG), and only then sends the bytes to pvj-raster
(render/raster/pvj-raster.c): a separate program, run by systemd for each job as its own account with no network,
no files of the box's, a syscall filter and hard limits (install/pvj-raster@.service). What comes back is a fresh PNG
written from pixels, so nothing else the file carried survives. That PNG is stored in the graphics store; the upload is
never kept, and the panel never serves anything but the stored PNG or a thumbnail of it.

`Store` is the folder of stored pictures; `Client` talks to the helper. Both are plain Python with no imaging library.
"""
import collections
import os
import re
import socket
import struct
import subprocess
import threading
import time

from . import paths

# The limits of the design's section 7.2. pvj-raster holds the same numbers and checks them again.
MAX_PICTURE_BYTES = 10 * 1024 * 1024        # a PNG or a JPEG
MAX_SVG_BYTES = 1024 * 1024
MAX_SIDE = 8192                             # the header's width or height
MAX_PIXELS = 40_000_000
OUT_SIDE = 2048                             # the long side of what is stored
MAX_FILES = 64
MAX_TOTAL_BYTES = 100 * 1024 * 1024
UPLOADS_PER_MINUTE = 5
THUMB_SIDE = 256
JOB_SECONDS = 20.0                          # the helper kills itself at 15

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EXTENSIONS = {"png": (".png",), "jpeg": (".jpg", ".jpeg"), "svg": (".svg",)}
KIND_WORDS = {"png": "a PNG", "jpeg": "a JPEG", "svg": "an SVG"}
_STEM = re.compile(r"[^\x00-\x1f/\\]{1,110}")


class RasterError(Exception):
    """A refusal or a failure, with the words for the person (status is the HTTP status)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def sniff(head):
    """png, jpeg, svg or None from the first bytes of a file. Never from the name."""
    if head.startswith(PNG_MAGIC):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    text = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    text = text.lstrip(b" \t\r\n")
    if text.startswith((b"<?xml", b"<svg")):
        return "svg"
    return None


def png_size(data):
    """(width, height) from a PNG's header, else a RasterError. Nothing is decoded."""
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise RasterError(400, "This PNG file is damaged.")
    return struct.unpack(">II", data[16:24])


def jpeg_info(data):
    """(width, height, components, kind) from a JPEG's first frame header, else a RasterError.

    kind is the frame marker: 0xC0 baseline, 0xC1 extended, 0xC2 progressive are accepted; 0xC3 and the others are
    lossless, hierarchical or arithmetic and are refused by the caller. Nothing is decoded."""
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xD9, 0xDA):
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 4 + 6 > n:
                break
            precision, height, width, comps = struct.unpack(">BHHB", data[i + 4:i + 10])
            if precision != 8:
                raise RasterError(400, "Only 8-bit JPEG files are supported.")
            return width, height, comps, marker
        i += 2 + length
    raise RasterError(400, "This JPEG file is damaged.")


def check_limits(width, height):
    if width < 1 or height < 1:
        raise RasterError(400, "This picture has no size.")
    if width > MAX_SIDE or height > MAX_SIDE:
        raise RasterError(413, "This picture is larger than %d pixels on a side." % MAX_SIDE)
    if width * height > MAX_PIXELS:
        raise RasterError(413, "This picture has more than 40 million pixels.")


def inspect(data, filename):
    """Everything that is checked without decoding, in this order: size on disk, the type from the first bytes, the
    name's extension against it, then the sizes in the header. Returns the kind. Raises RasterError."""
    if not data:
        raise RasterError(400, "This file is empty.")
    kind = sniff(data[:512])
    if kind is None:
        raise RasterError(415, "Only PNG, JPEG and SVG pictures are accepted (not WebP, GIF, BMP, TIFF, HEIC or PDF).")
    limit = MAX_SVG_BYTES if kind == "svg" else MAX_PICTURE_BYTES
    if len(data) > limit:
        raise RasterError(413, "%s may be at most %d MB." % ("An SVG file" if kind == "svg" else "A picture", limit // (1024 * 1024)))
    if not filename.lower().endswith(EXTENSIONS[kind]):
        raise RasterError(415, "The name says one kind of file but it holds %s. Rename it to match (%s)." % (KIND_WORDS[kind], EXTENSIONS[kind][0]))
    if kind == "png":
        check_limits(*png_size(data))
    elif kind == "jpeg":
        width, height, comps, marker = jpeg_info(data)
        check_limits(width, height)
        if comps == 4:
            raise RasterError(415, "CMYK JPEG files are not supported; save it as an RGB picture.")
        if comps not in (1, 3):
            raise RasterError(415, "This kind of JPEG is not supported.")
        if marker not in (0xC0, 0xC1, 0xC2):
            raise RasterError(415, "This kind of JPEG (lossless, hierarchical or arithmetic) is not supported.")
    return kind


class Client:
    """Asks pvj-raster to do a job. On a node that is its socket, which systemd starts a helper behind for each job.

    `exec_bin` (or PVJ_RASTER_EXEC) runs the program directly with its own ceilings instead; that is for tests and
    for a developer's desk. No unit of the node sets it."""

    def __init__(self, socket_path=None, exec_bin=None, env=None):
        env = os.environ if env is None else env
        self.socket_path = socket_path or paths.raster_socket(env)
        self.exec_bin = exec_bin or env.get("PVJ_RASTER_EXEC") or None

    def available(self):
        if self.exec_bin:
            return os.access(self.exec_bin, os.X_OK)
        return os.path.exists(self.socket_path)

    def _run(self, request, payload):
        if self.exec_bin:
            try:
                done = subprocess.run([self.exec_bin, "--self-limit"], input=request + payload, capture_output=True,
                                      timeout=JOB_SECONDS, env={"PATH": "/usr/bin:/bin"})
            except subprocess.TimeoutExpired:
                raise RasterError(422, "This picture took too long to read; use a simpler or smaller one.")
            except OSError:
                raise RasterError(503, "Picture upload is not available on this node.")
            return done.stdout
        deadline = time.monotonic() + JOB_SECONDS
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(JOB_SECONDS)
                s.connect(self.socket_path)
                s.sendall(request + payload)
                s.shutdown(socket.SHUT_WR)
                out = bytearray()
                while True:
                    s.settimeout(max(0.1, deadline - time.monotonic()))
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    out += chunk
                    if len(out) > 64 * 1024 * 1024:
                        raise RasterError(502, "The picture helper sent too much.")
            return bytes(out)
        except socket.timeout:
            raise RasterError(422, "This picture took too long to read; use a simpler or smaller one.")
        except (FileNotFoundError, ConnectionRefusedError, PermissionError):
            raise RasterError(503, "Picture upload is not available on this node (the picture helper is not running).")
        except OSError:
            raise RasterError(503, "The picture helper could not be reached.")

    @staticmethod
    def _parse(out):
        head, _, body = out.partition(b"\n")
        text = head.decode("utf-8", "replace")
        if text.startswith("ERR "):
            raise RasterError(422, text[4:][:300].strip() or "This picture could not be read.")
        if not out:
            # the helper ended with nothing to say: the unit's memory ceiling (256 MB) is below the address-space one
            # (512 MB), so a decode that runs out of memory is killed by the system with no answer. To the person it
            # is the same plain refusal as for a picture over the size limits.
            raise RasterError(413, "This picture is too big to process; try a smaller one.")
        m = re.fullmatch(r"OK (\d+) (\d+) ([01]) (\d+)", text)
        if not m:
            raise RasterError(502, "The picture helper gave no answer.")
        width, height, alpha, length = int(m.group(1)), int(m.group(2)), m.group(3) == "1", int(m.group(4))
        # the helper is a lower account but it is still checked: a PNG of the size it says, within the limits we keep
        if len(body) != length or not body.startswith(PNG_MAGIC) or not (1 <= width <= OUT_SIDE and 1 <= height <= OUT_SIDE):
            raise RasterError(502, "The picture helper gave a wrong answer.")
        if png_size(body) != (width, height):
            raise RasterError(502, "The picture helper gave a wrong answer.")
        return {"width": width, "height": height, "alpha": alpha}, body

    def normalise(self, data):
        """({width, height, alpha}, png bytes) for an uploaded PNG, JPEG or SVG."""
        return self._parse(self._run(b"RASTER1 IMG %d\n" % len(data), data))

    def thumbnail(self, png, side=THUMB_SIDE):
        return self._parse(self._run(b"RASTER1 THUMB %d %d\n" % (side, len(png)), png))[1]


class Store:
    """The graphics store: a folder of stored PNGs, each with a thumbnail made the first time it is asked for.

    Names are the media folder's (`valid_name`) and always end in .png: what is stored is the helper's PNG, whatever the
    upload was. Nothing in the folder is ever replaced without the caller saying so, and a link is never followed."""

    def __init__(self, folder, valid_name, client, free_space=None, clock=time.monotonic):
        self.folder = folder
        self.valid_name = valid_name
        self.client = client
        self.free_space = free_space or (lambda: 1 << 62)
        self.clock = clock
        self._lock = threading.Lock()          # rename, delete and publishing never interleave
        self._recent = collections.deque()     # times of the last new files, for the rate

    # --- names ---------------------------------------------------------------------------------------------------
    def stored_name(self, upload_name):
        """The name a file is stored under: the upload's name with .png in place of its extension (.png, .jpg, .jpeg or .svg)."""
        if not self.valid_name(upload_name):
            raise RasterError(400, "That is not a usable file name.")
        stem = os.path.splitext(upload_name)[0]
        if not _STEM.fullmatch(stem):
            raise RasterError(400, "That is not a usable file name.")
        name = stem + ".png"
        if not self.valid_name(name):
            raise RasterError(400, "That is not a usable file name.")
        return name

    def _path(self, name):
        """The path of a stored picture, for a name that was checked; a name that is not valid is not found."""
        if not isinstance(name, str) or not self.valid_name(name) or not name.endswith(".png"):
            raise RasterError(404, "No such graphic.")
        return os.path.join(self.folder, name)

    def _thumbs(self):
        return os.path.join(self.folder, ".thumbs")

    def _ensure(self):
        try:
            os.makedirs(self.folder, mode=0o750, exist_ok=True)
        except OSError as e:
            raise RasterError(500, "The graphics folder is not writable: %s" % (e.strerror or e))

    # --- reading ---------------------------------------------------------------------------------------------------
    def files(self):
        """[{name, size, width, height, modified}], by name. A damaged file is listed with no size rather than hidden."""
        out = []
        try:
            names = sorted(os.listdir(self.folder))
        except OSError:
            return out
        for name in names:
            if name.startswith(".") or not name.endswith(".png"):
                continue
            path = os.path.join(self.folder, name)
            try:
                st = os.lstat(path)
                if not os.path.isfile(path) or os.path.islink(path):
                    continue
                with open(path, "rb") as f:
                    head = f.read(33)
                width, height = png_size(head)
            except (OSError, RasterError):
                continue
            out.append({"name": name, "size": st.st_size, "width": width, "height": height, "modified": int(st.st_mtime)})
        return out

    def total_bytes(self):
        return sum(f["size"] for f in self.files())

    def state(self):
        return {"files": self.files(), "available": self.client.available(),
                "limits": {"picture_bytes": MAX_PICTURE_BYTES, "svg_bytes": MAX_SVG_BYTES, "side": MAX_SIDE, "pixels": MAX_PIXELS,
                          "stored_side": OUT_SIDE, "files": MAX_FILES, "total_bytes": MAX_TOTAL_BYTES}}

    def thumbnail(self, name):
        """The thumbnail PNG of a stored picture, made by the helper once and kept."""
        path = self._path(name)
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                raise FileNotFoundError(name)
            mtime = os.stat(path).st_mtime
            cached = os.path.join(self._thumbs(), name)
            if os.path.isfile(cached) and not os.path.islink(cached) and os.stat(cached).st_mtime >= mtime:
                with open(cached, "rb") as f:
                    return f.read()
            with open(path, "rb") as f:
                png = f.read(MAX_PICTURE_BYTES + 1)
        except OSError:
            raise RasterError(404, "No such graphic.")
        thumb = self.client.thumbnail(png)
        try:
            os.makedirs(self._thumbs(), mode=0o750, exist_ok=True)
            # a name of its own for each call (as store() does): the server is threaded, and two first-time
            # thumbnails made at once must not write to one file. The replace is atomic, so a reader sees all or none.
            tmp = os.path.join(self._thumbs(), ".new-%d-%d-%s" % (os.getpid(), threading.get_ident(), os.urandom(4).hex()))
            try:
                with open(tmp, "wb") as f:
                    f.write(thumb)
                os.replace(tmp, cached)
                tmp = None
            finally:
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        except OSError:
            pass                  # a thumbnail that cannot be kept is still shown
        return thumb

    # --- changing ------------------------------------------------------------------------------------------------
    def _drop_thumb(self, name):
        try:
            os.unlink(os.path.join(self._thumbs(), name))
        except OSError:
            pass

    def store(self, upload_name, data, replace=False):
        """The whole pipeline for bytes that were received: checks without decoding, the helper, the store.
        Returns {name, size, width, height, alpha}."""
        kind = inspect(data, upload_name)            # nothing is decoded before this has passed
        name = self.stored_name(upload_name)
        self._ensure()
        final = self._path(name)
        with self._lock:
            exists = os.path.lexists(final)
            if exists and not replace:
                raise RasterError(409, "A graphic with that name already exists.")
            if exists and (os.path.islink(final) or not os.path.isfile(final)):
                raise RasterError(409, "Refusing to replace something that is not a plain picture.")
            files = self.files()
            if not exists and len(files) >= MAX_FILES:
                raise RasterError(409, "The graphics store holds %d files already; delete one first." % MAX_FILES)
            now = self.clock()
            while self._recent and now - self._recent[0] > 60:
                self._recent.popleft()
            if not exists and len(self._recent) >= UPLOADS_PER_MINUTE:
                raise RasterError(429, "Too many new graphics in a minute; wait a little and try again.")
        meta, png = self.client.normalise(data)        # outside the lock: it can take seconds
        if len(png) > MAX_PICTURE_BYTES:
            # what is stored can always be thumbnailed: the helper reads at most MAX_PICTURE_BYTES for a thumbnail, so
            # nothing bigger than that is kept (a 2048 pixel PNG of noise can be; that one is refused in words)
            raise RasterError(413, "This picture is too detailed to keep (over %d MB once cleaned); use a simpler or smaller one." % (MAX_PICTURE_BYTES // (1024 * 1024)))
        with self._lock:
            old = os.path.getsize(final) if os.path.isfile(final) else 0
            if self.total_bytes() - old + len(png) > MAX_TOTAL_BYTES:
                raise RasterError(507, "The graphics store would be over %d MB; delete a graphic first." % (MAX_TOTAL_BYTES // (1024 * 1024)))
            if len(png) + 50 * 1024 * 1024 > self.free_space():
                raise RasterError(507, "Not enough free space on the node.")
            tmp = os.path.join(self.folder, ".new-%d-%d" % (os.getpid(), threading.get_ident()))
            try:
                with open(tmp, "wb") as f:
                    f.write(png)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o640)
                if replace:
                    os.replace(tmp, final)
                else:
                    try:
                        os.link(tmp, final)            # fails if the name appeared meanwhile: never overwrites silently
                    except FileExistsError:
                        raise RasterError(409, "A graphic with that name appeared during the upload.")
                    os.unlink(tmp)
                tmp = None
            except OSError as e:
                raise RasterError(500, "Could not store the graphic: %s" % (e.strerror or e))
            finally:
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            self._drop_thumb(name)
            if not exists:
                self._recent.append(self.clock())
        return {"name": name, "size": len(png), "width": meta["width"], "height": meta["height"], "alpha": meta["alpha"], "from": kind}

    def delete(self, name):
        path = self._path(name)
        with self._lock:
            if not os.path.lexists(path):
                raise RasterError(404, "No such graphic.")
            if os.path.islink(path) or not os.path.isfile(path):
                raise RasterError(409, "%s is not a plain picture; change it on the node, not from here." % name)
            try:
                os.unlink(path)
            except OSError as e:
                raise RasterError(500, "Could not delete: %s" % (e.strerror or e))
            self._drop_thumb(name)
        return {"deleted": name}

    def rename(self, name, new):
        src = self._path(name)
        if not isinstance(new, str) or not new.lower().endswith(".png"):
            raise RasterError(400, "The new name must end in .png.")
        new = self.stored_name(new)
        dst = self._path(new)
        with self._lock:
            if not os.path.lexists(src):
                raise RasterError(404, "No such graphic.")
            if os.path.islink(src) or not os.path.isfile(src):
                raise RasterError(409, "%s is not a plain picture; change it on the node, not from here." % name)
            try:
                os.link(src, dst)            # fails if the new name exists: a rename never overwrites
            except FileExistsError:
                raise RasterError(409, "A graphic with that name already exists.")
            except OSError as e:
                raise RasterError(500, "Could not rename: %s" % (e.strerror or e))
            os.unlink(src)
            self._drop_thumb(name)
        return {"name": new}
