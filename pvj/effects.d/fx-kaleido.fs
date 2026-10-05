/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A kaleidoscope of the playing picture: one wedge of it mirrored around a point, turning slowly if you let it.",
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
            "NAME": "segments",
            "LABEL": "Mirrors",
            "TYPE": "long",
            "VALUES": [
                3,
                4,
                5,
                6,
                8,
                12
            ],
            "LABELS": [
                "3",
                "4",
                "5",
                "6",
                "8",
                "12"
            ],
            "DEFAULT": 6
        },
        {
            "NAME": "turn",
            "LABEL": "Turn",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.0
        },
        {
            "NAME": "spin",
            "LABEL": "Turns a minute",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 6.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "zoom",
            "LABEL": "Zoom",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 3.0,
            "DEFAULT": 1.0
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
    // a hair off the exact centre, where an angle is not defined
    vec2 d = (isf_FragNormCoord - centre) * shape + vec2(0.00003, 0.00002);
    float wedge = TURN / float(segments);
    // the turning in whole turns, folded to 0..1 so it stays exact for hours
    float a = atan(d.y, d.x) + TURN * (turn + fract(TIME * min(spin, 6.0) / 60.0));
    a = abs(mod(a, wedge) - wedge * 0.5);
    vec2 p = centre + length(d) * vec2(cos(a), sin(a)) / shape / max(zoom, 0.5);
    gl_FragColor = IMG_NORM_PIXEL(inputImage, fold(p));
}
