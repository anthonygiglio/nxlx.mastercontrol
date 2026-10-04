/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Travelling stripes at any angle that can be bent into waves, a zigzag or bricks, and mirrored.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 3 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Travel rate (stripes a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "count",
            "LABEL": "Stripes",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 24.0,
            "DEFAULT": 8.0
        },
        {
            "NAME": "angle",
            "LABEL": "Angle (degrees)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 180.0,
            "DEFAULT": 30.0
        },
        {
            "NAME": "warp",
            "LABEL": "Bend",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "shape",
            "LABEL": "Kind of bend",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Waves",
                "Zigzag",
                "Bricks"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "mirror",
            "LABEL": "Mirror left and right",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.42,
                0.2,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.12,
                0.04,
                0.3,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 p = (isf_FragNormCoord - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    if (mirror) {
        p.x = abs(p.x);
    }
    float turn = angle * 0.0174533;
    vec2 across = vec2(cos(turn), sin(turn));
    float u = dot(p, across);
    float w = dot(p, vec2(-across.y, across.x));
    // the travel in whole stripes, and a slow cycle for the bend; both folded to 0..1 so they stay exact for hours
    float travel = fract(TIME * rate * 0.5) * 2.0;
    float slow = 6.2831853 * fract(TIME * (0.04 + 0.05 * rate));
    float bend = 0.0;
    if (shape == 0) {
        bend = 0.9 * sin(w * 5.0 + slow) + 0.4 * sin(w * 11.0 - slow * 2.0);
    }
    if (shape == 1) {
        bend = 2.4 * abs(fract(w * 2.0 + 0.25) - 0.5) - 0.6;
    }
    if (shape == 2) {
        bend = 1.0 - 2.0 * step(1.0, mod(w * 4.0 + 64.0, 2.0));
    }
    float v = u * count + warp * bend - travel;
    float s = sin(3.14159 * v);
    float m = smoothstep(-0.10, 0.10, s);
    // each stripe is rounded by light across its width, and the whole field is shaded towards the corners
    vec3 col = mix(second.rgb, first.rgb, m) * (0.74 + 0.26 * abs(s));
    col *= 1.0 - 0.30 * min(1.0, dot(p, p) * 1.5);
    col += vec3(0.02, 0.02, 0.03);
    gl_FragColor = vec4(col, 1.0);
}
