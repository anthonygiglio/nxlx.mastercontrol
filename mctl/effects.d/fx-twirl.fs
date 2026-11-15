/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The picture wound around a point: the middle is turned furthest, and the turning eases out to nothing at the rim of a circle.",
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
            "NAME": "turns",
            "LABEL": "Turns",
            "TYPE": "float",
            "MIN": -2.0,
            "MAX": 2.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "size",
            "LABEL": "Size",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.5,
            "DEFAULT": 0.6
        },
        {
            "NAME": "soft",
            "LABEL": "Soft rim",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.0,
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
    float r = length(d);
    // the share of the turning this distance gets: all of it in the middle, none from the rim on
    // (with a hard rim the middle turns as one piece and the easing is all near the rim)
    float rim = max(size, 0.05);
    float share = 1.0 - smoothstep(rim * (1.0 - clamp(soft, 0.05, 1.0)), rim, r);
    float a = atan(d.y, d.x) - TURN * turns * share * share;
    vec2 p = centre + r * vec2(cos(a), sin(a)) / shape;
    gl_FragColor = IMG_NORM_PIXEL(inputImage, fold(p));
}
