/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The picture as a wall of big square lights with dark gaps between them, like an LED screen seen from close by.",
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
            "NAME": "cells",
            "LABEL": "Lights across",
            "TYPE": "float",
            "MIN": 8.0,
            "MAX": 160.0,
            "DEFAULT": 48.0
        },
        {
            "NAME": "gap",
            "LABEL": "Gap between the lights",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 0.5,
            "DEFAULT": 0.18
        },
        {
            "NAME": "dots",
            "LABEL": "Round lights",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "lift",
            "LABEL": "Brightness of the lights",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 2.0,
            "DEFAULT": 1.25
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

void main() {
    float across = max(floor(cells), 8.0);
    // square cells whatever the picture's shape
    vec2 count = vec2(across, max(floor(across * RENDERSIZE.y / RENDERSIZE.x + 0.5), 1.0));
    vec2 cell = floor(isf_FragNormCoord * count);
    vec2 inside = fract(isf_FragNormCoord * count) - vec2(0.5);
    vec3 c = IMG_NORM_PIXEL(inputImage, (cell + vec2(0.5)) / count).rgb;
    float edge = 0.5 - clamp(gap, 0.0, 0.5) * 0.5;
    float lit = dots ? 1.0 - smoothstep(edge - 0.06, edge, length(inside))
                     : 1.0 - smoothstep(edge - 0.04, edge, max(abs(inside.x), abs(inside.y)));
    gl_FragColor = vec4(clamp(c * lift, 0.0, 1.0) * lit, 1.0);
}
