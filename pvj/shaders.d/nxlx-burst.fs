/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Streaks of light that shoot out from one point, with a ring that leaves the middle on every beat (at most 3 a second).",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one atan, one square root and about 60 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Beats a second",
            "TYPE": "float",
            "MIN": 0.25,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "rays",
            "LABEL": "Streaks",
            "TYPE": "float",
            "MIN": 8.0,
            "MAX": 64.0,
            "DEFAULT": 28.0
        },
        {
            "NAME": "reach",
            "LABEL": "Streak length",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "ring",
            "LABEL": "Ring on the beat",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "inward",
            "LABEL": "Fly inward",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "streak",
            "LABEL": "Streak colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.85,
                0.5,
                1.0
            ]
        },
        {
            "NAME": "ground",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.35,
                0.06,
                0.3,
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

// A scrambled number from 0 to 1 for two small whole numbers. Every step is a whole number below 16 million, so a
// 32-bit float holds it exactly: multiply, fold back under the prime 4093, square, fold, and once more.
float scramble(float a, float b) {
    float n = mod(a * 157.0 + b * 311.0 + 71.0, 4093.0);
    n = mod(n * n + b * 1009.0 + 37.0, 4093.0);
    n = mod(n * 229.0 + a * 53.0 + 11.0, 4093.0);
    return n / 4093.0;
}

void main() {
    vec2 p = isf_FragNormCoord - clamp(centre, 0.0, 1.0);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    float r = length(p);
    float around = atan(p.y, p.x + 0.00001) / 6.2831853 + 0.5;
    // cut the circle into wedges, one streak in each, every one with its own length, pace and start
    float n = floor(rays + 0.5);
    float wedge = min(floor(around * n), n - 1.0);
    float inside = around * n - wedge;
    float h = scramble(wedge, 5.0);
    float h2 = fract(h * 61.0);
    // the beat is capped at 3 a second, whatever the number says
    float hz = min(rate, 3.0);
    // a streak's place along its wedge: a cycle folded to 0..1, so it stays exact for hours
    float along = fract(TIME * hz * (0.35 + 0.4 * h2) + h);
    if (inward) {
        along = 1.0 - along;
    }
    float saw = fract(r / (reach * (0.6 + 0.8 * h2)) - along);
    float head = saw * saw * saw;
    float taper = smoothstep(0.0, 0.3, inside) * smoothstep(0.0, 0.3, 1.0 - inside);
    // near the centre the wedges are thinner than the pixels: fade the streaks out there
    float open = smoothstep(0.03, 0.22, r);
    vec3 col = ground.rgb * (0.30 + 0.55 / (1.0 + 4.0 * r * r));
    col += streak.rgb * head * taper * open * (0.45 + 0.55 * h);
    // the ring: it leaves the centre on the beat and fades as it grows
    float beats = TIME * hz;
    float since = fract(beats);
    if (ring) {
        float gone = abs(r - since * 1.1);
        col += mix(streak.rgb, vec3(1.0), 0.3) * (1.0 - smoothstep(0.0, 0.03 + 0.05 * since, gone)) * (1.0 - since) * 0.7;
    }
    col += streak.rgb * 0.35 / (1.0 + 90.0 * r * r);
    gl_FragColor = vec4(col / (1.0 + 0.2 * col), 1.0);
}
