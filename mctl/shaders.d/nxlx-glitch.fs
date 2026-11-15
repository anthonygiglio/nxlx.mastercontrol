/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A striped picture torn into blocks that jump sideways and split their colours on every step; at most 3 steps a second unless Fast is on (up to 6 a second: not for photosensitive people).",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 3 sines and about 80 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Steps a second",
            "TYPE": "float",
            "MIN": 0.25,
            "MAX": 3.0,
            "DEFAULT": 2.0
        },
        {
            "NAME": "amount",
            "LABEL": "How much is torn",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.45
        },
        {
            "NAME": "rows",
            "LABEL": "Rows of blocks",
            "TYPE": "float",
            "MIN": 4.0,
            "MAX": 24.0,
            "DEFAULT": 10.0
        },
        {
            "NAME": "shift",
            "LABEL": "Jump",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "split",
            "LABEL": "Colour split",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "fast",
            "LABEL": "Fast (double rate)",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.95,
                0.85,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.9,
                0.1,
                0.5,
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

// The picture that gets torn: slanted soft stripes, 0 to 1.
float stripes(vec2 p, float slide) {
    return 0.5 + 0.5 * sin(6.2831853 * (p.x * 3.0 + p.y * 2.0 + slide));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    // the step: capped at 3 a second here, whatever the number says, unless Fast is on
    float hz = min(rate, 3.0) * (fast ? 2.0 : 1.0);
    float beats = TIME * hz;
    float step1 = mod(floor(beats), 1024.0);
    // blocks: rows of the picture, each row cut into a few pieces of its own width
    float count = floor(rows + 0.5);
    float row = min(floor(uv.y * count), count - 1.0);
    float pieces = 2.0 + floor(4.0 * scramble(row, 3.0 + step1));
    float piece = min(floor(uv.x * pieces), pieces - 1.0);
    float h = scramble(row * 8.0 + piece, step1);
    float h2 = fract(h * 61.0);
    float torn = step(1.0 - amount, h);
    // a torn block jumps sideways and its colours come apart
    float jump = torn * shift * (h2 - 0.5) * 0.6;
    float apart = torn * split * 0.04 * (0.5 + h2);
    float slide = fract(TIME * hz * 0.125);
    vec2 p = vec2((uv.x + jump) * wide, uv.y);
    float left = stripes(p - vec2(apart, 0.0), slide);
    float mid = stripes(p, slide);
    float right = stripes(p + vec2(apart, 0.0), slide);
    vec3 col = mix(second.rgb, first.rgb, vec3(left, mid, right));
    // torn blocks are a little brighter or darker than the rest, and every row has a fine shade of its own
    col *= 0.72 + 0.20 * fract(h * 173.0) * torn + 0.16 * fract(uv.y * count);
    col += torn * 0.10 * h2;
    col += vec3(0.03);
    gl_FragColor = vec4(col, 1.0);
}
