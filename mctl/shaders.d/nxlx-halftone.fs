/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A field of dots that swell and shrink as a wave runs through them: a sweep, rings from a point, or a beat.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 2 square roots and 2 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Waves a second",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 3.0,
            "DEFAULT": 0.8
        },
        {
            "NAME": "dots",
            "LABEL": "Rows of dots",
            "TYPE": "float",
            "MIN": 6.0,
            "MAX": 40.0,
            "DEFAULT": 18.0
        },
        {
            "NAME": "wave",
            "LABEL": "Wave",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Sweep",
                "Rings",
                "Beat"
            ],
            "DEFAULT": 1
        },
        {
            "NAME": "diagonal",
            "LABEL": "Dots on the diagonal",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "invert",
            "LABEL": "Invert",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "ink",
            "LABEL": "Dot colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.9,
                0.2,
                1.0
            ]
        },
        {
            "NAME": "paper",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.75,
                0.1,
                0.3,
                1.0
            ]
        },
        {
            "NAME": "centre",
            "LABEL": "Centre of the rings",
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
    vec2 uv = isf_FragNormCoord;
    vec2 scale = vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    vec2 p = (uv - vec2(0.5)) * scale;
    vec2 g = p * floor(dots + 0.5);
    if (diagonal) {
        g = vec2(g.x + g.y, g.x - g.y) * 0.7071;
    }
    vec2 id = floor(g);
    vec2 f = g - id - vec2(0.5);
    // the middle of this dot, back in the picture's own measure: the wave is read there, so a dot is one size
    vec2 mid = id + vec2(0.5);
    if (diagonal) {
        mid = vec2(mid.x + mid.y, mid.x - mid.y) * 0.7071;
    }
    mid /= floor(dots + 0.5);
    // the wave: capped at 3 a second, and folded to 0..1 so it stays exact for hours
    float hz = min(rate, 3.0);
    float run = fract(TIME * hz);
    float v = 0.5 + 0.5 * sin(6.2831853 * (mid.x * 1.2 + mid.y * 0.5 - run));
    if (wave == 1) {
        v = 0.5 + 0.5 * sin(6.2831853 * (length(mid - (clamp(centre, 0.0, 1.0) - vec2(0.5)) * scale) * 2.2 - run));
    }
    if (wave == 2) {
        v = (1.0 - run) * (1.0 - run) * (1.0 - 0.6 * min(1.0, length(mid)));
    }
    // a dot: its radius follows the wave, between a small dot and one that almost touches its neighbours
    float radius = 0.12 + 0.36 * v;
    float edge = 0.04 + 0.012 * floor(dots + 0.5) / 10.0;
    float dot1 = 1.0 - smoothstep(radius - edge, radius + edge, length(f));
    if (invert) {
        dot1 = 1.0 - dot1;
    }
    vec3 col = mix(paper.rgb * (0.50 + 0.30 * uv.y), ink.rgb * (0.80 + 0.20 * v), dot1);
    col *= 1.0 - 0.25 * min(1.0, dot(p, p));
    col += vec3(0.02);
    gl_FragColor = vec4(col, 1.0);
}
