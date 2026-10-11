# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Projection mapping, drawn by the player's GPU.

The old box used ofxPiMapper (an openFrameworks program steered by a fake keyboard). Here the mapping is a GLSL
user shader that mpv runs on its final picture (the OUTPUT hook): the picture that would be on screen without
mapping is the "texture", and each surface takes a piece of it (its texture corners, 0 to 1) and places it on the
screen (its screen corners, in pixels). Everything outside every surface is black. Masks are the existing PNG
overlay, drawn after the shader, so they stay in screen space.

Surfaces, like ofxPiMapper's:
* quad: four corners with perspective (a homography), the usual keystone and building-face surface;
* triangle: three corners, straight (affine) mapping;
* grid: a quad split into columns x rows cells whose inner points move too (a coarse mesh warp for curved screens).

A surface list is validated here (never trusted), turned into matrices here, and written into the shader as
constants, so the shader holds no loops over data and no user text. The first surface in the list is drawn on top.
"""

import math
import os
import re
import struct
import threading
import uuid

from . import locks

MAX_SURFACES = 16
MAX_CELLS = 64            # all cells of all surfaces (a quad or triangle is one cell); bounds the work per pixel
MAX_GRID = 8              # columns and rows of a grid surface
MAX_COORD = 16384.0
MAX_SETS = 8              # saved mappings
NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,40}")
TYPES = {"quad": 4, "triangle": 3, "grid": None}
UNIT_QUAD = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
UNIT_TRI = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


class MapperError(ValueError):
    pass


# ---- geometry -----------------------------------------------------------------------------------------------------
def _solve(a, b):
    """Solve a x = b (n x n, small) by Gaussian elimination with partial pivoting. Raises MapperError if singular."""
    n = len(b)
    m = [list(map(float, row)) + [float(b[i])] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise MapperError("the corners are in a line or on top of each other")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                if f:
                    for c in range(col, n + 1):
                        m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def homography(src, dst):
    """The 3x3 matrix (row-major, 9 floats, h[8] = 1) that maps the four points `src` to the four points `dst`."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return _solve(a, b) + [1.0]


def affine(src, dst):
    """The 3x3 matrix (row-major) of the affine map taking three points `src` to three points `dst`."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0]); b.append(u)
        a.append([0, 0, 0, x, y, 1]); b.append(v)
    return _solve(a, b) + [0.0, 0.0, 1.0]


def apply(h, p):
    x, y = p
    w = h[6] * x + h[7] * y + h[8]
    return ((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w)


def _convex(points):
    """True when the polygon is strictly convex (a perspective quad must be, or it folds over itself)."""
    sign = 0
    n = len(points)
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = points[i], points[(i + 1) % n], points[(i + 2) % n]
        cross = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(cross) < 1e-9:
            return False
        s = 1 if cross > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


# ---- validation ---------------------------------------------------------------------------------------------------
def _point(p, limit, what):
    if (not isinstance(p, (list, tuple)) or len(p) != 2
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in p)):
        raise MapperError("%s must be [x, y] numbers" % what)
    x, y = float(p[0]), float(p[1])
    if not (-limit <= x <= limit and -limit <= y <= limit):
        raise MapperError("%s is out of range" % what)
    return [round(x, 4), round(y, 4)]


def default_surface(kind, width, height, index=0):
    """A new surface in the middle of a width x height screen, showing the whole picture."""
    w, h = width / 2.0, height / 2.0
    x0, y0 = width / 4.0 + 30 * index, height / 4.0 + 30 * index
    if kind == "triangle":
        verts = [[x0, y0 + h], [x0 + w / 2, y0], [x0 + w, y0 + h]]
        tex = [[0.0, 1.0], [0.5, 0.0], [1.0, 1.0]]
        return {"id": uuid.uuid4().hex[:8], "type": "triangle", "name": "Triangle", "on": True, "vertices": verts, "tex": tex}
    cols = rows = 1
    if kind == "grid":
        cols = rows = 2
    verts = [[x0 + w * c / cols, y0 + h * r / rows] for r in range(rows + 1) for c in range(cols + 1)]
    if kind == "quad":
        verts = [verts[0], verts[1], verts[3], verts[2]]          # clockwise, like ofxPiMapper
    s = {"id": uuid.uuid4().hex[:8], "type": kind, "name": "Grid" if kind == "grid" else "Quad", "on": True,
         "vertices": [[round(a, 4), round(b, 4)] for a, b in verts], "tex": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
    if kind == "grid":
        s["cols"], s["rows"] = cols, rows
    return s


def validate_surface(s):
    """A clean surface from untrusted input. Raises MapperError."""
    if not isinstance(s, dict):
        raise MapperError("a surface must be an object")
    kind = s.get("type")
    if kind not in TYPES:
        raise MapperError("surface type must be quad, triangle or grid")
    sid = s.get("id") or uuid.uuid4().hex[:8]
    if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-f]{8}", sid):
        raise MapperError("bad surface id")
    name = s.get("name", kind.title())
    if not isinstance(name, str) or not NAME.fullmatch(name.strip() or "x"):
        raise MapperError("a surface name is 1 to 40 letters, digits, spaces or . _ -")
    on = s.get("on", True)
    if not isinstance(on, bool):
        raise MapperError("on must be true or false")
    out = {"id": sid, "type": kind, "name": name.strip() or kind.title(), "on": on}
    if kind == "grid":
        cols, rows = s.get("cols"), s.get("rows")
        for v, what in ((cols, "cols"), (rows, "rows")):
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_GRID:
                raise MapperError("%s must be 1 to %d" % (what, MAX_GRID))
        out["cols"], out["rows"] = cols, rows
        need = (cols + 1) * (rows + 1)
    else:
        need = TYPES[kind]
    verts = s.get("vertices")
    if not isinstance(verts, list) or len(verts) != need:
        raise MapperError("a %s needs %d screen corners" % (kind, need))
    out["vertices"] = [_point(p, MAX_COORD, "a screen corner") for p in verts]
    tex = s.get("tex")
    tneed = 3 if kind == "triangle" else 4
    if not isinstance(tex, list) or len(tex) != tneed:
        raise MapperError("a %s needs %d picture corners" % (kind, tneed))
    out["tex"] = [_point(p, 1.0, "a picture corner (0 to 1)") for p in tex]
    turn = None
    for cell in cells(out):            # fails now, not at draw time, on a folded or flat surface
        if len(cell[0]) == 4 and not _convex(cell[0]):
            raise MapperError("surface %s: its corners cross or bend inwards; move a corner" % out["name"])
        if len(cell[0]) == 3 and not _convex(cell[0]):
            raise MapperError("surface %s: its corners are in a line" % out["name"])
        (x0, y0), (x1, y1), (x2, y2) = cell[0][:3]
        t = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1) > 0
        if turn is not None and t != turn:     # a cell turned over: the grid folds over itself
            raise MapperError("surface %s: the grid folds over itself; move a point back" % out["name"])
        turn = t
    return out


def validate_mapping(m):
    """{"surfaces": [...], "edit": bool, "selected": id|None, "corner": int} from untrusted input."""
    if not isinstance(m, dict):
        raise MapperError("send a mapping object")
    surfaces = m.get("surfaces", [])
    if not isinstance(surfaces, list) or len(surfaces) > MAX_SURFACES:
        raise MapperError("at most %d surfaces" % MAX_SURFACES)
    clean = [validate_surface(s) for s in surfaces]
    if len({s["id"] for s in clean}) != len(clean):
        raise MapperError("two surfaces have the same id")
    if sum(len(cells(s)) for s in clean) > MAX_CELLS:
        raise MapperError("too many grid cells in all (at most %d)" % MAX_CELLS)
    return clean


# ---- cells and shader ---------------------------------------------------------------------------------------------
def _bilerp(q, u, v):
    """Point at (u, v) inside the texture quad q = [tl, tr, br, bl]."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q
    top = (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)
    bot = (x3 + (x2 - x3) * u, y3 + (y2 - y3) * u)
    return (top[0] + (bot[0] - top[0]) * v, top[1] + (bot[1] - top[1]) * v)


def cells(s):
    """[(screen polygon, texture polygon)] for a surface: one for a quad or triangle, cols x rows for a grid.
    Screen quads are [tl, tr, br, bl]."""
    v = [tuple(p) for p in s["vertices"]]
    t = [tuple(p) for p in s["tex"]]
    if s["type"] in ("quad", "triangle"):
        return [(v, t)]
    cols, rows = s["cols"], s["rows"]
    out = []
    for r in range(rows):
        for c in range(cols):
            i = r * (cols + 1) + c
            scr = [v[i], v[i + 1], v[i + cols + 2], v[i + cols + 1]]
            tex = [_bilerp(t, c / cols, r / rows), _bilerp(t, (c + 1) / cols, r / rows),
                   _bilerp(t, (c + 1) / cols, (r + 1) / rows), _bilerp(t, c / cols, (r + 1) / rows)]
            out.append((scr, tex))
    return out


def _f(x):
    """A GLSL float literal. Only finite numbers: an inf or nan would not even be valid GLSL."""
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
        raise MapperError("not a finite number")
    s = "%.9g" % x
    return s if ("." in s or "e" in s) else s + ".0"


def _mat3(h):
    """GLSL mat3(...) for a row-major 3x3 (GLSL takes columns)."""
    return "mat3(%s)" % ", ".join(_f(h[r * 3 + c]) for c in range(3) for r in range(3))


def inv_bilinear(p, q):
    """(u, v) with bilerp(q, u, v) == p for a convex quad q = [tl, tr, br, bl], or None. The root inside the cell is
    preferred; outside it the nearer root carries the mapping on."""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = q
    ex, ey = bx - ax, by - ay
    fx, fy = dx - ax, dy - ay
    gx, gy = ax - bx + cx - dx, ay - by + cy - dy
    hx, hy = p[0] - ax, p[1] - ay
    k2 = gx * fy - gy * fx
    k1 = (ex * fy - ey * fx) + (hx * gy - hy * gx)
    k0 = hx * ey - hy * ex
    scale = max(abs(ex * fy - ey * fx), 1e-12)
    if abs(k2) < 1e-9 * scale:
        if abs(k1) < 1e-12:
            return None
        vs = [-k0 / k1]
    else:
        disc = k1 * k1 - 4 * k0 * k2
        if disc < 0:
            return None
        r = math.sqrt(disc)
        vs = [(-k1 - r) / (2 * k2), (-k1 + r) / (2 * k2)]
    best = None
    for v in vs:
        denx, deny = ex + gx * v, ey + gy * v
        if abs(denx) >= abs(deny):
            if abs(denx) < 1e-12:
                continue
            u = (hx - fx * v) / denx
        else:
            u = (hy - fy * v) / deny
        off = max(0.0, -u, u - 1, -v, v - 1)
        if best is None or off < best[0]:
            best = (off, u, v)
    return None if best is None else (best[1], best[2])


def _cell_maps(scr, tex, bilinear=False):
    """How one cell maps screen to picture: ("persp", to_unit, to_tex), ("affine", to_unit, to_tex), or
    ("bilinear", screen quad, texture quad). Grid cells are bilinear, so that neighbouring cells agree along the edge
    they share (per-cell perspective broke the picture at every inner grid line, found on the Pi)."""
    if bilinear:
        return ("bilinear", [tuple(p) for p in scr], [tuple(p) for p in tex])
    if len(scr) == 4:
        return ("persp", homography(scr, UNIT_QUAD), homography(UNIT_QUAD, tex))
    return ("affine", affine(scr, UNIT_TRI), affine(UNIT_TRI, tex))


def cell_uv(cm, p):
    """(picture point, inside?) for screen point p through cell map cm; (None, False) when it has no answer."""
    kind = cm[0]
    if kind == "bilinear":
        uv = inv_bilinear(p, cm[1])
        if uv is None:
            return None, False
        u, v = uv
        return _bilerp(cm[2], u, v), (-1e-6 <= u <= 1 + 1e-6 and -1e-6 <= v <= 1 + 1e-6)
    a, b = cm[1], cm[2]
    w = a[6] * p[0] + a[7] * p[1] + a[8]
    if w <= 0:
        return None, False
    u = ((a[0] * p[0] + a[1] * p[1] + a[2]) / w, (a[3] * p[0] + a[4] * p[1] + a[5]) / w)
    if kind == "affine":
        inside = u[0] >= -1e-6 and u[1] >= -1e-6 and u[0] + u[1] <= 1 + 1e-6
    else:
        inside = -1e-6 <= u[0] <= 1 + 1e-6 and -1e-6 <= u[1] <= 1 + 1e-6
    return apply(b, u), inside


def surface_maps(s):
    """[(screen polygon, cell map)] for a surface."""
    return [(scr, _cell_maps(scr, tex, s["type"] == "grid")) for scr, tex in cells(s)]


def shader(surfaces, edit=None):
    """A direct shader: every pixel tests the cells in turn (the first surface on top). Exact, changes show at once,
    but it costs per cell: on a Pi 4 at 1080p one quad is free and a 4x4 grid drops frames. Used while editing, with
    `edit` = {"selected": id, "corner": n} drawing the outlines."""
    body = _outline_code(surfaces, edit.get("selected"), edit.get("corner")) if edit else []
    for s in surfaces:
        if not s["on"]:
            continue
        for scr, cm in surface_maps(s):
            if cm[0] == "bilinear":
                # in units of 1024 pixels, so the quadratic stays well inside 32-bit float precision
                pts = ", ".join("vec2(%s, %s)" % (_f(x / 1024.0), _f(y / 1024.0)) for x, y in cm[1])
                tq = cm[2]
                body.append("    u = ibl(p / 1024.0, %s);\n"
                            "    if (u.x >= 0.0 && u.y >= 0.0 && u.x <= 1.0 && u.y <= 1.0) return pick(mix(mix(vec2(%s, %s), vec2(%s, %s), u.x), mix(vec2(%s, %s), vec2(%s, %s), u.x), u.y));"
                            % (pts, _f(tq[0][0]), _f(tq[0][1]), _f(tq[1][0]), _f(tq[1][1]), _f(tq[3][0]), _f(tq[3][1]), _f(tq[2][0]), _f(tq[2][1])))
                continue
            inside = ("u.x >= 0.0 && u.y >= 0.0 && u.x + u.y <= 1.0" if cm[0] == "affine" else
                      "q.z > 0.0 && u.x >= 0.0 && u.y >= 0.0 && u.x <= 1.0 && u.y <= 1.0")
            body.append("    q = %s * vec3(p, 1.0); u = q.xy / q.z;\n"
                        "    if (%s) { t = %s * vec3(u, 1.0); return pick(t.xy / t.z); }" % (_mat3(cm[1]), inside, _mat3(cm[2])))
    return "\n".join([
        "// nxlx.mastercontrol projection mapping, direct (generated; do not edit)",
        "//!HOOK OUTPUT",
        "//!BIND HOOKED",
        "//!DESC nxlx mapper (editing)",
        "",
        "vec4 pick(vec2 t) { return vec4(HOOKED_tex(clamp(t, vec2(0.0), vec2(1.0))).rgb, 1.0); }",
        "float cr(vec2 a, vec2 b) { return a.x * b.y - a.y * b.x; }",
        "vec2 ibl(vec2 p, vec2 a, vec2 b, vec2 c, vec2 d) {",
        "    vec2 e = b - a; vec2 f = d - a; vec2 g = a - b + c - d; vec2 h = p - a;",
        "    float k2 = cr(g, f); float k1 = cr(e, f) + cr(h, g); float k0 = cr(h, e); float v;",
        "    if (abs(k2) < 1e-6) { v = -k0 / k1; } else {",
        "        float w = k1 * k1 - 4.0 * k0 * k2; if (w < 0.0) return vec2(-1.0); w = sqrt(w);",
        "        v = (-k1 - w) / (2.0 * k2); if (v < 0.0 || v > 1.0) v = (-k1 + w) / (2.0 * k2); }",
        "    vec2 den = e + g * v;",
        "    float u = abs(den.x) > abs(den.y) ? (h.x - f.x * v) / den.x : (h.y - f.y * v) / den.y;",
        "    return vec2(u, v); }",
        "float seg(vec2 p, vec2 a, vec2 b) { vec2 ab = b - a; float h = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-6), 0.0, 1.0); return length(p - a - ab * h); }",
        "",
        "vec4 hook() {",
        "    vec2 p = HOOKED_pos * HOOKED_size;",
        "    vec3 q; vec2 u; vec3 t;",
    ] + body + [
        "    return vec4(0.0, 0.0, 0.0, 1.0);",
        "}",
        "",
    ])


# ---- the warp table (the show path) --------------------------------------------------------------------------------
TABLE_STEP = 4            # screen pixels per table cell each way
EDGE = 8.0                # signed distance kept near edges, in screen pixels


def outline(s):
    """The surface's outer edge as a polygon of screen points."""
    v = [tuple(p) for p in s["vertices"]]
    if s["type"] != "grid":
        return v
    cols, rows = s["cols"], s["rows"]
    at = lambda r, c: v[r * (cols + 1) + c]
    return ([at(0, c) for c in range(cols + 1)] + [at(r, cols) for r in range(1, rows + 1)]
            + [at(rows, c) for c in range(cols - 1, -1, -1)] + [at(r, 0) for r in range(rows - 1, 0, -1)])


def _dist_to_outline(p, poly):
    px, py = p
    best = float("inf")
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        ex, ey = px - ax - t * dx, py - ay - t * dy
        d = ex * ex + ey * ey
        if d < best:
            best = d
    return math.sqrt(best)


def _inside(p, poly):
    """Point in polygon (even-odd rule; the outline of a grid may be concave)."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _uv_at(p, cellmaps):
    """The picture point for screen point p on a surface, from the cell that holds p or, just outside, the nearest
    cell carried on (so that interpolation up to an edge stays smooth). None if it cannot be carried on.
    `cellmaps` is [(screen polygon, cell map, (x0, x1, y0, y1))]: only cells whose box holds p are tried."""
    best, best_d = None, float("inf")
    for scr, cm, (x0, x1, y0, y1) in cellmaps:
        if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
            continue
        uv, inside = cell_uv(cm, p)
        if uv is None:
            continue
        d = 0.0 if inside else _dist_to_outline(p, scr)
        if d < best_d:
            best, best_d = uv, d
            if inside:
                break
    if best is None or not all(math.isfinite(c) and -1 <= c <= 1.999 for c in best):
        return None
    return best


def warp_table(surfaces, width, height, step=TABLE_STEP, stop=None):
    """(table width, table height, [uA, vA, dA, dB, uB, vB] per cell, row by row from the top).

    Each table cell holds two layers: A, the top surface at (or within a few pixels of) that screen point, and B, the
    surface under it. For each: where on the picture the point comes from, and how far the point is inside (+) or
    outside (-) that surface's edge, in pixels. The GPU interpolates the table between cells, so every pixel costs the
    same few texture reads whatever the number of surfaces, and the two distances keep every edge smooth: against black,
    and where one surface lies over another (measured on the Pi: with one layer, such a join followed the 4 pixel
    table grid in visible steps). `stop`, if given, is asked now and then; when it says True, None is returned."""
    lw, lh = max(1, math.ceil(width / step)), max(1, math.ceil(height / step))
    tw, th = width / lw, height / lh
    n = lw * lh
    live = [s for s in surfaces if s["on"]]
    maps = [surface_maps(s) for s in live]           # per surface: [(screen polygon, cell map)]
    owner = [-1] * n                # the top surface over each cell centre (index into live; 0 is the top)
    uu = [0.0] * n
    vv = [0.0] * n
    eps = 1e-6
    for si in range(len(live) - 1, -1, -1):       # the bottom surface first; the top one is written last
        for scr, cm in maps[si]:
            xs, ys = [x for x, _ in scr], [y for _, y in scr]
            i0, i1 = max(0, int(min(xs) / tw - 1)), min(lw - 1, int(max(xs) / tw + 1))
            j0, j1 = max(0, int(min(ys) / th - 1)), min(lh - 1, int(max(ys) / th + 1))
            if cm[0] == "bilinear":
                # inv_bilinear written out for speed (a 64-cell grid took 6 s on a Pi 4 through the function)
                (ax, ay), (bx, by), (cx, cy), (dx, dy) = cm[1]
                (t0x, t0y), (t1x, t1y), (t2x, t2y), (t3x, t3y) = cm[2]
                ex, ey, fx, fy = bx - ax, by - ay, dx - ax, dy - ay
                gx, gy = ax - bx + cx - dx, ay - by + cy - dy
                k2 = gx * fy - gy * fx
                base = ex * fy - ey * fx
                flat = abs(k2) < 1e-9 * max(abs(base), 1e-12)
                for j in range(j0, j1 + 1):
                    if stop is not None and stop():
                        return None
                    hy = (j + 0.5) * th - ay
                    for i in range(i0, i1 + 1):
                        hx = (i + 0.5) * tw - ax
                        k1 = base + hx * gy - hy * gx
                        k0 = hx * ey - hy * ex
                        if flat:
                            if k1 == 0:
                                continue
                            v = -k0 / k1
                        else:
                            disc = k1 * k1 - 4 * k0 * k2
                            if disc < 0:
                                continue
                            r = math.sqrt(disc)
                            v = (-k1 - r) / (2 * k2)
                            if v < -eps or v > 1 + eps:
                                v = (-k1 + r) / (2 * k2)
                        if v < -eps or v > 1 + eps:
                            continue
                        denx, deny = ex + gx * v, ey + gy * v
                        if abs(denx) >= abs(deny):
                            u = (hx - fx * v) / denx
                        else:
                            u = (hy - fy * v) / deny
                        if u < -eps or u > 1 + eps:
                            continue
                        k = j * lw + i
                        owner[k] = si
                        topx, topy = t0x + (t1x - t0x) * u, t0y + (t1y - t0y) * u
                        botx, boty = t3x + (t2x - t3x) * u, t3y + (t2y - t3y) * u
                        uu[k] = topx + (botx - topx) * v
                        vv[k] = topy + (boty - topy) * v
                continue
            tri = cm[0] == "affine"
            a, b = cm[1], cm[2]
            a0, a1, a2, a3, a4, a5, a6, a7, a8 = a
            b0, b1, b2, b3, b4, b5, b6, b7, b8 = b
            for j in range(j0, j1 + 1):
                if stop is not None and stop():
                    return None
                py = (j + 0.5) * th
                row = j * lw
                for i in range(i0, i1 + 1):
                    px = (i + 0.5) * tw
                    w = a6 * px + a7 * py + a8
                    if w <= 0:
                        continue
                    ux = (a0 * px + a1 * py + a2) / w
                    uy = (a3 * px + a4 * py + a5) / w
                    if tri:
                        if ux < -eps or uy < -eps or ux + uy > 1 + eps:
                            continue
                    elif ux < -eps or uy < -eps or ux > 1 + eps or uy > 1 + eps:
                        continue
                    k = row + i
                    owner[k] = si
                    tz = b6 * ux + b7 * uy + b8
                    uu[k] = (b0 * ux + b1 * uy + b2) / tz
                    vv[k] = (b3 * ux + b4 * uy + b5) / tz
    # Layer A everywhere is the top surface (or nothing); layer B only matters near an edge.
    out = [0.0] * (n * 6)
    for k in range(n):
        o = k * 6
        if owner[k] >= 0:
            out[o:o + 6] = (uu[k], vv[k], EDGE, -EDGE, uu[k], vv[k])
        else:
            out[o:o + 6] = (0.0, 0.0, -EDGE, -EDGE, 0.0, 0.0)
    band = set()
    for j in range(lh):
        for i in range(lw):
            k = j * lw + i
            o = owner[k]
            if ((i > 0 and owner[k - 1] != o) or (i < lw - 1 and owner[k + 1] != o)
                    or (j > 0 and owner[k - lw] != o) or (j < lh - 1 and owner[k + lw] != o)):
                for dj in (-2, -1, 0, 1, 2):
                    for di in (-2, -1, 0, 1, 2):
                        x, y = i + di, j + dj
                        if 0 <= x < lw and 0 <= y < lh:
                            band.add(y * lw + x)
    polys = [outline(s) for s in live]
    reach = 3 * max(tw, th) + EDGE             # a band point is at most this far outside a cell that matters
    near = [[(scr, cm, (min(x for x, _ in scr) - reach, max(x for x, _ in scr) + reach,
                        min(y for _, y in scr) - reach, max(y for _, y in scr) + reach))
             for scr, cm in m] for m in maps]
    boxes = [(min(x for x, _ in pl) - EDGE, max(x for x, _ in pl) + EDGE, min(y for _, y in pl) - EDGE, max(y for _, y in pl) + EDGE)
             for pl in polys]
    for count, k in enumerate(band):
        if stop is not None and count % 256 == 0 and stop():
            return None
        i, j = k % lw, k // lw
        p = ((i + 0.5) * tw, (j + 0.5) * th)
        layers = []                                 # (signed distance, uv), top first
        for si, pl in enumerate(polys):
            x0, x1, y0, y1 = boxes[si]
            if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
                continue
            d = min(EDGE, _dist_to_outline(p, pl))
            if not _inside(p, pl):
                d = -d
            if d <= -EDGE:
                continue
            uv = _uv_at(p, near[si])
            if uv is None:
                continue
            layers.append((d, uv))
            if len(layers) == 2:
                break
        o = k * 6
        if not layers:
            continue
        (da, uva) = layers[0]
        (db, uvb) = layers[1] if len(layers) > 1 else (-EDGE, uva)
        out[o:o + 6] = (uva[0], uva[1], da, db, uvb[0], uvb[1])
    return lw, lh, out


def warp_shader(lw, lh, values):
    """The show shader: the two-layer table as two half-float textures filtered by the GPU, then two reads of the
    picture per pixel, blended by the layers' edge distances. mpv takes texture data as 32-bit floats (half floats were
    refused). Measured on a Pi 4 playing 1080p H.264: at 1920x1080 no dropped frames, even with a 64-cell grid; at
    2560x1440 about 3 a second (the GPU is at its limit there; even one table read and one picture read cost 1.4).
    Skipping the second picture read inside a surface with a branch was slower, so there is none."""
    a, b = [], []
    for k in range(0, len(values), 6):
        a += values[k:k + 4]
        b += (values[k + 4], values[k + 5], 0.0, 1.0)
    return "\n".join([
        "// nxlx.mastercontrol projection mapping (generated; do not edit)",
        "//!TEXTURE PVJWARPA",
        "//!SIZE %d %d" % (lw, lh),
        "//!FORMAT rgba16f",
        "//!FILTER LINEAR",
        "//!BORDER CLAMP",
        struct.pack("<%df" % len(a), *a).hex(),
        "",
        "//!TEXTURE PVJWARPB",
        "//!SIZE %d %d" % (lw, lh),
        "//!FORMAT rgba16f",
        "//!FILTER LINEAR",
        "//!BORDER CLAMP",
        struct.pack("<%df" % len(b), *b).hex(),
        "",
        "//!HOOK OUTPUT",
        "//!BIND HOOKED",
        "//!BIND PVJWARPA",
        "//!BIND PVJWARPB",
        "//!DESC nxlx mapper",
        "",
        "vec4 hook() {",
        "    vec4 a = texture(PVJWARPA, HOOKED_pos);",
        "    float ka = clamp(a.z + 0.5, 0.0, 1.0);",
        "    vec3 top = HOOKED_tex(clamp(a.xy, vec2(0.0), vec2(1.0))).rgb;",
        "    vec4 b = texture(PVJWARPB, HOOKED_pos);",
        "    float kb = clamp(a.w + 0.5, 0.0, 1.0);",
        "    vec3 under = HOOKED_tex(clamp(b.xy, vec2(0.0), vec2(1.0))).rgb * kb;",
        "    return vec4(mix(under, top, ka), 1.0);",
        "}",
        "",
    ])


# ---- outlines while editing ------------------------------------------------------------------------------------------
def _outline_code(surfaces, selected, corner):
    """GLSL lines that paint every surface's edges (the chosen one yellow and thicker, hidden ones grey), the chosen
    surface's grid lines, and the chosen corner as a pink dot. Drawn in the shader, not with mpv's on-screen overlay,
    because an osd-overlay did not appear in window screenshots on the Pi, so it could not be checked."""
    out = []
    for s in surfaces:
        sel = s["id"] == selected
        colour = "vec4(1.0, 0.85, 0.0, 1.0)" if sel else ("vec4(0.0, 0.8, 1.0, 1.0)" if s["on"] else "vec4(0.5, 0.5, 0.5, 1.0)")
        polys = [outline(s)]
        if sel and s["type"] == "grid":
            polys += [scr for scr, _ in cells(s)]
        width = "2.5" if sel else "1.5"
        for poly in polys:
            for i in range(len(poly)):
                a, b = poly[i], poly[(i + 1) % len(poly)]
                out.append("    if (seg(p, vec2(%s, %s), vec2(%s, %s)) < %s) return %s;"
                           % (_f(a[0]), _f(a[1]), _f(b[0]), _f(b[1]), width, colour))
        if sel and isinstance(corner, int) and 0 <= corner < len(s["vertices"]):
            cx, cy = s["vertices"][corner]
            out.insert(0, "    if (distance(p, vec2(%s, %s)) < 14.0) return vec4(1.0, 0.16, 0.47, 1.0);" % (_f(cx), _f(cy)))
    return out


# ---- scaling to the screen ---------------------------------------------------------------------------------------
def scaled(surfaces, frm, to):
    """The surfaces moved from a frm = (w, h) screen to a `to` screen, keeping their place in proportion."""
    if not frm or tuple(frm) == tuple(to):
        return surfaces
    fx, fy = to[0] / frm[0], to[1] / frm[1]
    out = []
    for s in surfaces:
        t = dict(s)
        t["vertices"] = [[round(x * fx, 4), round(y * fy, 4)] for x, y in s["vertices"]]
        out.append(t)
    return out


def resample_grid(s, cols, rows):
    """A grid surface with a new number of cells, spread evenly between its four outer corners (inner points reset)."""
    o = s["vertices"]
    c0, r0 = s["cols"], s["rows"]
    tl, tr, br, bl = o[0], o[c0], o[r0 * (c0 + 1) + c0], o[r0 * (c0 + 1)]
    verts = [list(_bilerp([tl, tr, br, bl], c / cols, r / rows)) for r in range(rows + 1) for c in range(cols + 1)]
    return dict(s, cols=cols, rows=rows, vertices=[[round(x, 4), round(y, 4)] for x, y in verts])


# ---- the engine: settings -> the player ---------------------------------------------------------------------------
class Engine:
    """Keeps the player's picture in step with settings["mapper"] and the edit state.

    While editing, the direct shader with outlines is put on at once. Otherwise the warp table is built in a
    background thread (about a second on a Pi 4) and swapped in when ready; a newer change makes an older build moot.
    Everything the player reads is written to its runtime folder under a fresh name, then the old files are removed."""

    def __init__(self, api, log=print):
        self.api = api
        self.log = log
        self.edit = {"on": False, "selected": None, "corner": 0, "target": "screen"}
        self.status = {"state": "off", "message": ""}
        self._gen = 0
        self._serial = 0                       # file names
        self._lock = locks.make("mapper.state")       # the counters and the status
        self._apply_lock = locks.make("mapper.apply")  # the newest-change check and the switch, together
        self._job = None                       # the newest build waiting for the worker
        self._building = False

    @property
    def settings(self):
        return self.api.settings

    def enabled(self):
        return self.api.registry.enabled("mapper")

    def screen(self):
        """(width, height) of the display, from the player; the saved screen or 1920x1080 when it is not known."""
        size = None
        try:
            size = self.api.player.osd_size()
        except Exception:
            size = None
        if size:
            return tuple(size)
        saved = self.settings.data["mapper"].get("screen")
        return tuple(saved) if saved else (1920, 1080)

    def placed(self, size=None):
        """(surfaces, screen they are in): the saved surfaces moved to the current screen in proportion, or, if that
        would put a point out of range (a far larger screen), left where they were, so they can still be edited."""
        cfg = self.settings.data["mapper"]
        size = tuple(size or self.screen())
        moved = scaled(cfg["surfaces"], cfg.get("screen"), size)
        if moved is cfg["surfaces"]:
            return moved, size
        try:
            validate_mapping({"surfaces": moved})
            return moved, size
        except MapperError:
            return cfg["surfaces"], tuple(cfg["screen"])

    def current(self):
        """The surfaces placed for the current screen."""
        return self.placed()[0]

    def state(self):
        cfg = self.settings.data["mapper"]
        with self._lock:
            status = dict(self.status)
        return {"enabled": self.enabled(), "on": cfg["on"], "screen": list(self.screen()), "surfaces": self.current(),
                "sets": sorted(cfg["sets"]), "edit": dict(self.edit), "status": status,
                "limits": {"surfaces": MAX_SURFACES, "cells": MAX_CELLS, "grid": MAX_GRID, "sets": MAX_SETS}}

    # -- writing to the player --
    def _write(self, text):
        """Write a shader file for the player under a new name (never over a file that may be in use)."""
        rundir = self.api.player.rundir
        with self._lock:
            self._serial += 1
            name = os.path.join(rundir, "mapper-%d-%d.glsl" % (os.getpid(), self._serial))
        tmp = name + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(text)
            os.replace(tmp, name)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return name

    def _cleanup(self, keep, limit):
        """Remove the mapping files this process wrote up to serial `limit` (taken when the switch was decided, so a
        file a newer change is writing or about to use is never touched), except `keep`, the file now in use (mpv may
        read it again), and every mapping file left by an earlier run of the service."""
        rundir = self.api.player.rundir
        try:
            names = os.listdir(rundir)
        except OSError:
            return
        for n in names:
            m = re.fullmatch(r"mapper-(\d+)-(\d+)\.glsl(\.tmp)?", n)
            full = os.path.join(rundir, n)
            if not m or full == keep:
                continue
            if int(m.group(1)) == os.getpid() and (m.group(3) or int(m.group(2)) > limit):
                continue
            try:
                os.unlink(full)
            except OSError:
                pass

    def _set_status(self, state, message="", gen=None):
        """Record the state; with `gen`, only if that change is still the newest (an older one never overwrites)."""
        with self._lock:
            if gen is not None and gen != self._gen:
                return
            self.status = {"state": state, "message": message}

    def _commit(self, gen, path, state, message=""):
        """Switch the player to `path` (None: no mapping) and set the status, only if `gen` is still the newest change.
        Check and switch happen under one lock, so an older build can never land after a newer change. While a mapping
        is shown the player stretches the picture to the whole screen (surfaces are in screen pixels, and the shader
        only sees the picture's own area) and uses 8-bit GPU buffers (measured on a Pi 4: with 16-bit ones the extra
        pass dropped 8 frames a second at 2560x1440)."""
        with self._apply_lock:
            if gen != self._gen:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                return False
            with self._lock:
                limit = self._serial
            try:
                self.api.player.set_mapping_mode(bool(path))
                self.api.player.set_shaders([path] if path else [])
            except Exception as e:      # the player is down or restarting: it gets the mapping when it comes back
                self._set_status("error", "the player did not take the mapping (%s); it is tried again when the player restarts" % e)
                return False
            self._cleanup(path, limit)
            self._set_status(state, message)
            return True

    def apply(self):
        """Make the screen match. Never raises for a player that is down (the status says so)."""
        ending = getattr(self.api, "transitions", None)
        if ending is not None:
            ending.end()            # the still of a crossfade shows the surfaces where they were
        with self._lock:
            self._gen += 1
            gen = self._gen
        cfg = self.settings.data["mapper"]
        try:
            surfaces = validate_mapping({"surfaces": self.current()}) if self.enabled() else []
        except MapperError as e:
            self._set_status("error", "the saved mapping does not fit this screen: %s" % e, gen)
            return
        editing = self.edit["on"] and self.enabled()
        try:
            if not surfaces or not (cfg["on"] or editing):
                self._commit(gen, None, "off", "" if self.enabled() else "the module is off")
                return
            if editing:
                self._commit(gen, self._write(shader(surfaces, self.edit)), "editing")
                return
        except Exception as e:
            self._set_status("error", "could not write the mapping: %s" % e, gen)
            return
        self._set_status("building", "", gen)
        with self._lock:
            self._job = (gen, surfaces, self.screen())
            start = not self._building
            self._building = True
        if start:
            threading.Thread(target=self._builder, name="mapper-build", daemon=True).start()

    def _builder(self):
        """The one build worker: always builds the newest job, and gives up on one that a newer change has overtaken.
        A drag that sends many changes costs one build at a time, not one thread each."""
        while True:
            with self._lock:
                job, self._job = self._job, None
                if job is None:
                    self._building = False
                    return
            gen, surfaces, size = job
            try:
                table = warp_table(surfaces, size[0], size[1], stop=lambda: gen != self._gen)
                if table is None:
                    continue                     # overtaken
                self._commit(gen, self._write(warp_shader(*table)), "on")
            except Exception as e:
                self._set_status("error", "could not build the mapping: %s" % e, gen)
                self.log("pvj-web: mapper: %s" % e)

    # -- requests from the panel --
    def handle(self, body):
        """One change from the panel (see MAPPER.md for the actions). Returns the state. Raises MapperError."""
        if not isinstance(body, dict):
            raise MapperError("send an object")
        action = body.get("action")
        with self.settings.lock:
            cfg = self.settings.data["mapper"]
            placed, size = self.placed()
            surfaces = [dict(s) for s in placed]
            sets = dict(cfg["sets"])
            on = cfg["on"]
            edit = dict(self.edit)
            find = lambda sid: next((s for s in surfaces if s["id"] == sid), None)

            def need(sid):
                s = find(sid)
                if s is None:
                    raise MapperError("no such surface")
                return s
            if action == "add":
                kind = body.get("type")
                if kind not in TYPES:
                    raise MapperError("type must be quad, triangle or grid")
                if len(surfaces) >= MAX_SURFACES:
                    raise MapperError("at most %d surfaces" % MAX_SURFACES)
                new = default_surface(kind, size[0], size[1], len(surfaces))
                surfaces.insert(0, new)
                edit.update(selected=new["id"], corner=0)
            elif action == "remove":
                need(body.get("id"))
                surfaces = [s for s in surfaces if s["id"] != body["id"]]
                if edit["selected"] == body["id"]:
                    edit.update(selected=surfaces[0]["id"] if surfaces else None, corner=0)
            elif action == "order":
                s = need(body.get("id"))
                i = surfaces.index(s)
                j = i - 1 if body.get("dir") == "up" else i + 1 if body.get("dir") == "down" else None
                if j is None:
                    raise MapperError("dir must be up or down")
                if 0 <= j < len(surfaces):
                    surfaces[i], surfaces[j] = surfaces[j], surfaces[i]
            elif action == "show":
                s = need(body.get("id"))
                if not isinstance(body.get("on"), bool):
                    raise MapperError("on must be true or false")
                s["on"] = body["on"]
            elif action == "rename":
                need(body.get("id"))["name"] = body.get("name")
            elif action in ("move", "place"):
                s = need(body.get("id"))
                target = body.get("target", "screen")
                if target not in ("screen", "picture"):
                    raise MapperError("target must be screen or picture")
                key = "vertices" if target == "screen" else "tex"
                pts = [list(p) for p in s[key]]
                corner = body.get("corner", -1)
                if isinstance(corner, bool) or not isinstance(corner, int) or not -1 <= corner < len(pts):
                    raise MapperError("bad corner")
                if action == "move":
                    dx, dy = body.get("dx", 0), body.get("dy", 0)
                    for v in (dx, dy):
                        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 4000:
                            raise MapperError("dx and dy must be -4000 to 4000")
                    if target == "picture":
                        dx, dy = dx / size[0], dy / size[1]
                    for i in (range(len(pts)) if corner == -1 else [corner]):
                        pts[i] = [pts[i][0] + dx, pts[i][1] + dy]
                else:
                    if corner == -1:
                        raise MapperError("choose a corner to place")
                    pts[corner] = _point([body.get("x"), body.get("y")], MAX_COORD if target == "screen" else 1.0, "the point")
                if target == "picture":
                    pts = [[min(1.0, max(0.0, x)), min(1.0, max(0.0, y))] for x, y in pts]
                s[key] = pts
                if corner >= 0:
                    edit.update(selected=s["id"], corner=corner, target=target)
            elif action == "grid":
                s = need(body.get("id"))
                if s["type"] != "grid":
                    raise MapperError("only a grid surface has cells")
                cols, rows = body.get("cols"), body.get("rows")
                for v in (cols, rows):
                    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_GRID:
                        raise MapperError("cols and rows must be 1 to %d" % MAX_GRID)
                surfaces[surfaces.index(s)] = resample_grid(s, cols, rows)
                edit["corner"] = 0
            elif action == "set":
                surfaces = body.get("surfaces")
            elif action == "edit":
                if "on" in body:
                    if not isinstance(body["on"], bool):
                        raise MapperError("on must be true or false")
                    edit["on"] = body["on"]
                if "selected" in body:
                    if body["selected"] is not None:
                        need(body["selected"])
                    edit.update(selected=body["selected"], corner=0)
                if "corner" in body:
                    c = body["corner"]
                    s = find(edit["selected"])
                    if s is None or isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < len(s["vertices"]):
                        raise MapperError("bad corner")
                    edit["corner"] = c
                if "target" in body:
                    if body["target"] not in ("screen", "picture"):
                        raise MapperError("target must be screen or picture")
                    edit["target"] = body["target"]
            elif action == "on":
                if not isinstance(body.get("on"), bool):
                    raise MapperError("on must be true or false")
                on = body["on"]
            elif action in ("save", "load", "delete"):
                name = body.get("name")
                if not isinstance(name, str) or not NAME.fullmatch(name) or name != name.strip():
                    raise MapperError("a mapping name is 1 to 40 letters, digits, spaces or . _ -")
                if action == "save":
                    if name not in sets and len(sets) >= MAX_SETS:
                        raise MapperError("at most %d saved mappings; delete one first" % MAX_SETS)
                    sets[name] = {"screen": list(size), "surfaces": validate_mapping({"surfaces": surfaces})}
                elif name not in sets:
                    raise MapperError("no saved mapping called %s" % name)
                elif action == "load":
                    surfaces = scaled(sets[name]["surfaces"], sets[name]["screen"], size)
                    edit.update(selected=surfaces[0]["id"] if surfaces else None, corner=0)
                else:
                    del sets[name]
            else:
                raise MapperError("unknown action")
            clean = validate_mapping({"surfaces": surfaces})
            if edit["selected"] is not None and not any(s["id"] == edit["selected"] for s in clean):
                edit.update(selected=clean[0]["id"] if clean else None, corner=0)
            self.settings.data["mapper"] = {"on": on, "screen": list(size), "surfaces": clean, "sets": sets}
            self.settings.save()
            self.edit = edit
        self.apply()
        return self.state()
