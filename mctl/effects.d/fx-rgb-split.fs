/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Red and blue pulled apart along a line, steady or breathing at a rate you set (at most 3 times a second). The split never closes completely, so the picture keeps its look through the whole breath.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Stylize"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "spread",
            "LABEL": "Spread",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 0.1,
            "DEFAULT": 0.02
        },
        {
            "NAME": "angle",
            "LABEL": "Angle (degrees)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 180.0,
            "DEFAULT": 0.0
        },
        {
            "NAME": "rate",
            "LABEL": "Breaths a second",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "depth",
            "LABEL": "Depth of the breath",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "wave",
            "LABEL": "Breath",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Smooth",
                "Sawtooth",
                "Steps"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "green",
            "LABEL": "Move green too",
            "TYPE": "bool",
            "DEFAULT": false
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
    // a place in the breath between 0 and 1; the rate is capped here, not only by the slider
    float t = fract(TIME * min(rate, 3.0));
    float w = 0.5 - 0.5 * cos(TURN * t);
    if (wave == 1) {
        w = t;
    } else if (wave == 2) {
        w = floor(t * 4.0) / 3.0;
    }
    // between 1 - depth/2 and 1 of the spread: never nothing
    float open = spread * (1.0 - 0.5 * clamp(depth, 0.0, 1.0) * (1.0 - w));
    float turn = angle * 0.0174533;
    vec2 along = vec2(cos(turn), sin(turn) * RENDERSIZE.x / RENDERSIZE.y) * open;
    vec2 uv = isf_FragNormCoord;
    vec4 mid = IMG_NORM_PIXEL(inputImage, green ? fold(uv + along * 0.5) : uv);
    float r = IMG_NORM_PIXEL(inputImage, fold(uv + along)).r;
    float bl = IMG_NORM_PIXEL(inputImage, fold(uv - along)).b;
    gl_FragColor = vec4(r, mid.g, bl, 1.0);
}
