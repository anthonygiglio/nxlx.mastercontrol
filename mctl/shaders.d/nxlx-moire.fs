/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Two sets of fine rings, one circling the other slowly, and the large soft shapes that appear between them.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 2 square roots and 4 sines a pixel",
    "INPUTS": [
        {
            "NAME": "speed",
            "LABEL": "Speed",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "rings",
            "LABEL": "Rings",
            "TYPE": "float",
            "MIN": 8.0,
            "MAX": 40.0,
            "DEFAULT": 22.0
        },
        {
            "NAME": "orbit",
            "LABEL": "Distance between the two",
            "TYPE": "float",
            "MIN": 0.02,
            "MAX": 0.25,
            "DEFAULT": 0.09
        },
        {
            "NAME": "soft",
            "LABEL": "Softness",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "invert",
            "LABEL": "Invert",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "ink",
            "LABEL": "Line colour",
            "TYPE": "color",
            "DEFAULT": [
                0.95,
                0.78,
                0.5,
                1.0
            ]
        },
        {
            "NAME": "ground",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.05,
                0.16,
                1.0
            ]
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
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 p = isf_FragNormCoord - clamp(centre, 0.0, 1.0);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    // the second set circles the first: one slow cycle, folded to 0..1 so the angle stays exact for hours
    float turn = 6.2831853 * fract(TIME * speed * 0.012);
    vec2 other = orbit * vec2(cos(turn), sin(turn));
    float k = rings * 6.2831853;
    float one = sin(k * length(p));
    float two = sin(k * 1.03 * length(p - other));
    // fine lines multiplied: the eye sees their slow product, the large shapes
    float fine = one * two;
    float m = smoothstep(-0.9 + 0.7 * (1.0 - soft), 0.9 - 0.7 * (1.0 - soft), fine);
    if (invert) {
        m = 1.0 - m;
    }
    vec3 col = mix(ground.rgb, ink.rgb, 0.14 + 0.58 * m);
    col *= 1.0 - 0.45 * min(1.0, dot(p, p));
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
