/*{
    "ISFVSN": "2",
    "DESCRIPTION": "One ring that travels out from a point and bends the picture like a lens as it passes, growing weaker on its way. Set where it is by hand, or let it travel.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Geometry"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "travel",
            "LABEL": "Where the ring is",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.35
        },
        {
            "NAME": "rate",
            "LABEL": "Rings a minute",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 20.0,
            "DEFAULT": 6.0
        },
        {
            "NAME": "width",
            "LABEL": "Width",
            "TYPE": "float",
            "MIN": 0.02,
            "MAX": 0.4,
            "DEFAULT": 0.12
        },
        {
            "NAME": "bend",
            "LABEL": "Bend",
            "TYPE": "float",
            "MIN": -1.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "glow",
            "LABEL": "Glow",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.15
        },
        {
            "NAME": "centre",
            "LABEL": "Centre",
            "TYPE": "point2D",
            "MIN": [
                0.0,
                0.0
            ],
            "MAX": [
                1.0,
                1.0
            ],
            "DEFAULT": [
                0.5,
                0.5
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

vec2 fold(vec2 p) {
    return vec2(1.0) - abs(mod(p, 2.0) - vec2(1.0));
}

const float TURN = 6.2831853;

void main() {
    vec2 shape = vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    vec2 d = (isf_FragNormCoord - centre) * shape + vec2(0.00003, 0.00002);
    float r = length(d);
    // how far the ring has got, 0..1 of its way: by hand, plus its own travelling (at most 20 a minute)
    float way = fract(travel + fract(TIME * min(rate, 20.0) / 60.0));
    float w = max(width, 0.02);
    // across the ring: -1 at its inner rim, 0 on its middle line, 1 at its outer rim; a window that is 1 in the middle
    // (the ring starts a little way out and ends before the corners, at half its strength: it is never out of sight)
    float x = clamp((r - mix(0.15, 0.85, way)) / w, -1.0, 1.0);
    float window = (1.0 - x * x) * (1.0 - x * x) * (1.0 - 0.5 * way);
    // a lens: what is inside the ring is read from nearer its middle line (or from further away, with a bend below 0)
    vec2 p = isf_FragNormCoord - (d / r) * x * window * bend * w / shape;
    vec4 c = IMG_NORM_PIXEL(inputImage, fold(p));
    gl_FragColor = vec4(c.rgb + vec3(glow * window * (1.0 - abs(x))), c.a);
}
