/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Still water at night with a few slow drops: each makes rings that widen and fade under a soft light.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "medium: 3 drops, 3 square roots, 3 sines and 4 divisions a pixel",
    "INPUTS": [
        {
            "NAME": "speed",
            "LABEL": "How often it drips",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "rings",
            "LABEL": "Rings in a drop",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 8.0,
            "DEFAULT": 4.0
        },
        {
            "NAME": "strength",
            "LABEL": "Ring strength",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "moon",
            "LABEL": "Light on the water",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "water",
            "LABEL": "Water colour",
            "TYPE": "color",
            "DEFAULT": [
                0.04,
                0.15,
                0.28,
                1.0
            ]
        },
        {
            "NAME": "light",
            "LABEL": "Light colour",
            "TYPE": "color",
            "DEFAULT": [
                0.75,
                0.82,
                0.95,
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

// One drop. `age` runs from 0 (it lands) to 1 (its rings are gone); `seed` says where it lands.
float drop(vec2 p, float wide, float age, float seed) {
    float h = scramble(seed, 3.0);
    vec2 at = (vec2(h, fract(h * 61.0)) - vec2(0.5)) * vec2(wide * 0.86, 0.80);
    float w = length(p - at) - age * 0.55;
    float span = 0.02 + 0.16 * age;
    float fade = (1.0 - age) * (1.0 - age);
    return fade * (1.0 - smoothstep(0.0, span, abs(w))) * sin(w * rings * 6.2831853 / (span * 4.0 + 0.04));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    vec2 p = (uv - vec2(0.5)) * vec2(wide, 1.0);
    float ripple = 0.0;
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        // each drop repeats at its own pace: the count of its drops says where the next one lands (folded to stay
        // small), and the part after the point is the drop's age
        float beats = TIME * speed * (0.050 + 0.013 * fi) + 0.37 * fi;
        ripple += drop(p, wide, fract(beats), mod(floor(beats), 512.0) + 600.0 * fi);
    }
    vec3 col = water.rgb * (0.85 + 0.75 * uv.y) + light.rgb * 0.05 * uv.y + vec3(0.01, 0.015, 0.03);
    float glow = 0.0;
    if (moon) {
        vec2 d = vec2(p.x * 0.55 + 0.12, uv.y - 0.72);
        glow = 0.38 / (1.0 + 9.0 * dot(d, d));
        col += light.rgb * glow * 0.55;
    }
    // a ring lifts the water: its near side catches the light, its far side is in shade
    col += light.rgb * strength * ripple * (0.30 + 0.5 * glow);
    gl_FragColor = vec4(max(col, vec3(0.0)), 1.0);
}
