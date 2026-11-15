/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Rings of water over the picture: waves run out from a point and push the picture to and fro along their way, weaker the further they get.",
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
            "NAME": "rings",
            "LABEL": "Rings",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 30.0,
            "DEFAULT": 8.0
        },
        {
            "NAME": "depth",
            "LABEL": "Depth",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 0.05,
            "DEFAULT": 0.02
        },
        {
            "NAME": "reach",
            "LABEL": "Reach",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 2.0,
            "DEFAULT": 1.2
        },
        {
            "NAME": "rate",
            "LABEL": "Waves a second",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.25
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
    // where this distance is within a wave, 0..1; the waves run outwards, at most one a second
    float place = fract(r * rings - fract(TIME * min(rate, 1.0)));
    float push = sin(TURN * place) * depth * (1.0 - smoothstep(0.0, max(reach, 0.1), r));
    vec2 p = isf_FragNormCoord + (d / r) * push / shape;
    gl_FragColor = IMG_NORM_PIXEL(inputImage, fold(p));
}
