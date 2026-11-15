/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Bars that flash on the beat in four patterns; at most 3 flashes a second unless Fast is on, which doubles the rate (up to 6 a second: not for photosensitive people).",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one sine and about 40 sums and products a pixel",
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
            "NAME": "bars",
            "LABEL": "Bars",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 32.0,
            "DEFAULT": 12.0
        },
        {
            "NAME": "decay",
            "LABEL": "Fade after each beat",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
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
                "Chase",
                "Alternate",
                "Scatter",
                "From the middle"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "sideways",
            "LABEL": "Lying bars",
            "TYPE": "bool",
            "DEFAULT": false
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
                0.35,
                0.9,
                1.0,
                1.0
            ]
        },
        {
            "NAME": "unlit",
            "LABEL": "Unlit colour",
            "TYPE": "color",
            "DEFAULT": [
                0.12,
                0.14,
                0.55,
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
    float along = sideways ? uv.y : uv.x;
    float up = sideways ? uv.x : uv.y;
    float count = floor(bars + 0.5);
    float k = min(floor(along * count), count - 1.0);
    float inside = along * count - k;
    // the beat: capped at 3 a second here, whatever the number says, unless Fast is on
    float hz = min(rate, 3.0) * (fast ? 2.0 : 1.0);
    float beats = TIME * hz;
    float beat = mod(floor(beats), 1024.0);
    float since = fract(beats);
    float on = 0.0;
    if (pattern == 0) {
        on = 1.0 - step(0.5, mod(k - beat + 1024.0, min(4.0, count)));
    }
    if (pattern == 1) {
        on = 1.0 - step(0.5, mod(k + beat, 2.0));
    }
    if (pattern == 2) {
        on = step(0.62, scramble(k, beat));
    }
    if (pattern == 3) {
        float ring = floor(abs(k - (count - 1.0) * 0.5));
        on = 1.0 - step(0.5, mod(ring - beat + 1024.0, clamp(floor(count * 0.5 + 0.5), 2.0, 3.0)));
    }
    float fade = 1.0 - since;
    float level = on * mix(1.0, 0.12 + 0.88 * fade * fade, decay);
    // each bar is a soft column: rounded across, a little brighter in the middle of its length
    float column = smoothstep(0.0, 0.12, inside) * smoothstep(0.0, 0.12, 1.0 - inside);
    float body = 0.72 + 0.28 * sin(3.14159 * up);
    vec3 rest = unlit.rgb * (0.45 + 0.40 * up) + vec3(0.02, 0.02, 0.03);
    vec3 col = mix(rest, lit.rgb * body + vec3(0.12) * body * body, level) * (0.25 + 0.75 * column);
    gl_FragColor = vec4(col, 1.0);
}
