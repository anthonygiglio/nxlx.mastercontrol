/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A wall of soft tiles, each brightening and dimming at its own slow pace.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: one sine and about 70 sums and products a pixel",
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
            "NAME": "rows",
            "LABEL": "Rows of tiles",
            "TYPE": "float",
            "MIN": 3.0,
            "MAX": 14.0,
            "DEFAULT": 6.0
        },
        {
            "NAME": "seam",
            "LABEL": "Seams",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "depth",
            "LABEL": "Contrast",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "pattern",
            "LABEL": "Tiling",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Squares",
                "Diamonds",
                "Triangles"
            ],
            "DEFAULT": 1
        },
        {
            "NAME": "first",
            "LABEL": "Dim colour",
            "TYPE": "color",
            "DEFAULT": [
                0.07,
                0.17,
                0.33,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Bright colour",
            "TYPE": "color",
            "DEFAULT": [
                0.94,
                0.68,
                0.44,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

// A scrambled number from 0 to 1 for two small whole numbers. Every step is a whole number below 16 million, so a
// 32-bit float holds it exactly: multiply, fold back under the prime 4093, square, fold, and once more.
float scramble(float a, float b) {
    float n = mod(a * 157.0 + b * 311.0 + 71.0, 4093.0);
    n = mod(n * n + b * 1009.0 + 37.0, 4093.0);
    n = mod(n * 229.0 + a * 53.0 + 11.0, 4093.0);
    return n / 4093.0;
}

void main() {
    vec2 p = (isf_FragNormCoord - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    vec2 g = p * floor(rows + 0.5);
    if (pattern == 1) {
        g = vec2(g.x + g.y, g.x - g.y) * 0.7071;
    }
    g += vec2(40.0);
    vec2 id = floor(g);
    vec2 f = g - id;
    // how far this pixel is from the nearest edge of its tile
    float d = min(min(f.x, 1.0 - f.x), min(f.y, 1.0 - f.y));
    float side = 0.0;
    if (pattern == 2) {
        side = step(f.x, f.y);
        d = min(d, abs(f.x - f.y) * 0.7071);
    }
    float h = scramble(id.x + side * 97.0, id.y);
    float own = fract(h * 61.0);
    // the tile's own slow cycle, folded to 0..1 before it becomes an angle so it stays exact for hours
    float breath = 0.5 + 0.5 * sin(6.2831853 * fract(TIME * speed * (0.010 + 0.022 * own) + h));
    float t = mix(0.5, breath, depth) * (0.75 + 0.25 * own);
    vec3 col = mix(first.rgb, second.rgb, t);
    // a soft seam, and a slight slope of light across each tile
    float edge = smoothstep(0.0, 0.03 + 0.10 * seam, d);
    col *= mix(1.0, 0.45 + 0.55 * edge, seam);
    col *= 0.92 + 0.16 * (f.y - 0.5) + 0.10 * d;
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
