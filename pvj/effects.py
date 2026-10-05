# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Effects: ISF filter shaders applied over whatever is playing (a clip, a stream, a live input).

This first part is the colour arithmetic. A filter has to work on RGB, and it has to sit before the brightness that
the panel's opacity, fades and Blackout use. In mpv that brightness is part of the conversion from the clip's own
colours (YUV for almost every video) to RGB, so the one stage that is before it, NATIVE, is not RGB for a video. The
effect therefore converts each pixel it reads to RGB itself, lets the filter work, and converts the result back, so
that the player's own conversion (with the brightness) still comes after it.
"""

# Kr and Kb of the colour matrices mpv names in video-params/colormatrix. Anything else is treated as BT.709.
MATRICES = {"bt.601": (0.299, 0.114), "bt.709": (0.2126, 0.0722), "bt.2020-ncl": (0.2627, 0.0593),
            "bt.2020-cl": (0.2627, 0.0593), "smpte-240m": (0.212, 0.087)}
LEVELS = ("limited", "full")


def _inverse(m):
    """The inverse of a 3 x 3 matrix given as three rows."""
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def native_maps(colormatrix, levels):
    """(to_rgb, offset, to_native) for a picture whose colours are `colormatrix` ("rgb", "bt.709", ...) at `levels`
    ("limited" or "full"): rgb = to_rgb x (native - offset) and native = to_native x rgb + offset, each matrix as
    three rows. For RGB all three are the identity. The numbers follow mpv's own conversion for 8 bits; for deeper
    pictures they are off by less than half a percent, and since the way back is the exact inverse of the way in,
    a filter that changes nothing gives back exactly what it read."""
    if colormatrix == "rgb":
        one = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        return one, [0.0, 0.0, 0.0], one
    kr, kb = MATRICES.get(colormatrix, MATRICES["bt.709"])
    kg = 1.0 - kr - kb
    if levels == "full":
        ymin, ys, cs = 0.0, 1.0, 255.0 / 254.0
    else:
        ymin, ys, cs = 16.0 / 255.0, 255.0 / 219.0, 255.0 / 224.0
    cmid = 128.0 / 255.0
    to_rgb = [[ys, 0.0, 2.0 * (1.0 - kr) * cs],
              [ys, -2.0 * kb * (1.0 - kb) / kg * cs, -2.0 * kr * (1.0 - kr) / kg * cs],
              [ys, 2.0 * (1.0 - kb) * cs, 0.0]]
    return to_rgb, [ymin, cmid, cmid], _inverse(to_rgb)


def _g(x):
    s = "%.9g" % x
    return s if ("." in s or "e" in s) else s + ".0"


def _mat3(rows):
    """A GLSL mat3 (its constructor takes columns) from three rows."""
    return "mat3(%s)" % ", ".join(_g(rows[r][c]) for c in range(3) for r in range(3))


def native_glsl(colormatrix, levels, prefix="pvj"):
    """The GLSL for the two conversions: `vec3 <prefix>_rgb(vec3 native)` (kept inside 0 to 1) and
    `vec3 <prefix>_native(vec3 rgb)`."""
    to_rgb, off, to_native = native_maps(colormatrix, levels)
    o = "vec3(%s)" % ", ".join(_g(v) for v in off)
    return ["vec3 %s_rgb(vec3 n) { return clamp(%s * (n - %s), 0.0, 1.0); }" % (prefix, _mat3(to_rgb), o),
            "vec3 %s_native(vec3 c) { return %s * c + %s; }" % (prefix, _mat3(to_native), o)]
