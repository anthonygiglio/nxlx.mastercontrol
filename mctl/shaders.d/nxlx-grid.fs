/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A wall of tiles that light on the beat in four patterns; at most 3 flashes a second unless Fast is on, which doubles the rate (up to 6 a second: not for photosensitive people).",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: no sines, about 60 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Beats a second",
            "TYPE": "float",
            "MIN": 0.25,
            "MAX": 3.0,
            "DEFAULT": 1.5
        },
        {
            "NAME": "rows",
            "LABEL": "Rows of tiles",
            "TYPE": "float",
            "MIN": 3.0,
            "MAX": 16.0,
            "DEFAULT": 7.0
        },
        {
            "NAME": "decay",
            "LABEL": "Fade after each beat",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.7
        },
        {
            "NAME": "fill",
            "LABEL": "Tile size",
            "TYPE": "float",
            "MIN": 0.4,
            "MAX": 1.0,
            "DEFAULT": 0.86
        },
        {
            "NAME": "pattern",
            "LABEL": "Pattern",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3
            ],
            "LABELS": [
                "Scatter",
                "Rows",
                "Diagonals",
                "Squares from the middle"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "fast",
            "LABEL": "Fast (double rate)",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "lit",
            "LABEL": "Lit colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.75,
                0.25,
                1.0
            ]
        },
        {
            "NAME": "unlit",
            "LABEL": "Unlit colour",
            "TYPE": "color",
            "DEFAULT": [
                0.5,
                0.12,
                0.3,
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
    vec2 uv = isf_FragNormCoord;
    float count = floor(rows + 0.5);
    vec2 g = (uv - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0) * count + vec2(32.0) + vec2(0.5 * mod(count, 2.0));
    vec2 id = floor(g);
    vec2 f = g - id - vec2(0.5);
    // the beat: capped at 3 a second here, whatever the number says, unless Fast is on
    float hz = min(rate, 3.0) * (fast ? 2.0 : 1.0);
    float beats = TIME * hz;
    float beat = mod(floor(beats), 1024.0);
    float since = fract(beats);
    float tile = id.x + id.y * 64.0;
    float on = step(0.66, scramble(tile, beat));
    if (pattern == 1) {
        on = 1.0 - step(0.5, mod(id.y + beat, 3.0));
    }
    if (pattern == 2) {
        on = 1.0 - step(0.5, mod(id.x + id.y + beat, 4.0));
    }
    if (pattern == 3) {
        float ring = max(abs(id.x - 32.0 + 0.5 * (1.0 - mod(count, 2.0))), abs(id.y - 32.0 + 0.5 * (1.0 - mod(count, 2.0))));
        on = 1.0 - step(0.5, mod(floor(ring) - beat + 1024.0, 3.0));
    }
    float fade = 1.0 - since;
    float level = on * mix(1.0, 0.12 + 0.88 * fade * fade, decay);
    // a tile: a square with soft corners, each with its own shade so the wall is never flat
    float d = max(abs(f.x), abs(f.y));
    float body = 1.0 - smoothstep(0.5 * fill - 0.06, 0.5 * fill, d);
    float own = 0.55 + 0.45 * scramble(tile, 7.0);
    float sheen = 1.0 - 0.9 * dot(f, f) + 0.25 * f.y;
    vec3 rest = unlit.rgb * (0.30 + 0.45 * own) * sheen + vec3(0.02, 0.02, 0.03);
    vec3 glowing = lit.rgb * (0.75 + 0.25 * own) * sheen + vec3(0.10) * sheen;
    vec3 col = mix(rest, glowing, level) * body;
    col += unlit.rgb * (0.10 + 0.08 * uv.y) + vec3(0.015);
    gl_FragColor = vec4(col, 1.0);
}
