/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A night sky that barely moves: two layers of stars that twinkle a little over a faint band of light.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 2 layers of stars, 3 sines and about 90 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "speed",
            "LABEL": "Drift",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "density",
            "LABEL": "Stars",
            "TYPE": "float",
            "MIN": 6.0,
            "MAX": 20.0,
            "DEFAULT": 11.0
        },
        {
            "NAME": "twinkle",
            "LABEL": "Twinkle",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "size",
            "LABEL": "Star size",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "band",
            "LABEL": "Band of light",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "sky",
            "LABEL": "Sky colour",
            "TYPE": "color",
            "DEFAULT": [
                0.03,
                0.05,
                0.16,
                1.0
            ]
        },
        {
            "NAME": "haze",
            "LABEL": "Band colour",
            "TYPE": "color",
            "DEFAULT": [
                0.42,
                0.3,
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

// One layer: the sky cut into squares, one star somewhere in the middle part of each. The squares repeat every 64
// across, so the layer can drift for ever: `shift` goes from 0 to 64 and starts again without a jump.
vec3 layer(vec2 g, float shift, float seed) {
    g.x += shift;
    vec2 id = floor(g);
    vec2 f = g - id;
    float h = scramble(mod(id.x, 64.0) + seed, id.y + 20.0);
    float h2 = fract(h * 61.0);
    float h3 = fract(h * 173.0);
    vec2 at = vec2(0.28) + 0.44 * vec2(h2, h3);
    float d = length(f - at) / (0.05 + 0.20 * h * h) / size;
    float star = 1.0 - smoothstep(0.0, 1.0, d);
    float blink = 1.0 - twinkle * 0.6 * (0.5 + 0.5 * sin(6.2831853 * fract(TIME * (0.05 + 0.25 * h3) + h2)));
    vec3 tone = mix(vec3(0.75, 0.85, 1.0), vec3(1.0, 0.85, 0.65), h2);
    return tone * star * star * blink * (0.35 + 0.65 * h);
}

void main() {
    vec2 uv = isf_FragNormCoord;
    vec2 p = (uv - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    // one very slow cycle for the drift, folded to 0..1: a whole turn of 64 squares takes more than an hour
    float drift = fract(TIME * speed * 0.00022);
    vec3 col = sky.rgb * (0.55 + 0.75 * uv.y) + vec3(0.015, 0.015, 0.03);
    if (band) {
        float across = p.y - 0.25 * p.x + 0.06 * sin(p.x * 2.2 + 6.2831853 * drift);
        col += haze.rgb * 0.42 / (1.0 + 26.0 * across * across);
    }
    col += layer(p * density + vec2(30.0, 10.0), drift * 64.0, 0.0);
    col += 0.7 * layer(p * density * 1.9 + vec2(11.0, 14.0), drift * 128.0, 300.0);
    gl_FragColor = vec4(col, 1.0);
}
