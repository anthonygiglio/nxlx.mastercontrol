/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Four paper lanterns that sway a little and glow, each breathing at its own pace.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "medium: 4 lanterns, 9 sines and 5 divisions a pixel",
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
            "NAME": "size",
            "LABEL": "Lantern size",
            "TYPE": "float",
            "MIN": 0.6,
            "MAX": 1.4,
            "DEFAULT": 1.0
        },
        {
            "NAME": "sway",
            "LABEL": "Sway",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "ribs",
            "LABEL": "Paper ribs",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "form",
            "LABEL": "Shape",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Round",
                "Tall",
                "Wide"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "glow",
            "LABEL": "Light colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.72,
                0.34,
                1.0
            ]
        },
        {
            "NAME": "paper",
            "LABEL": "Paper colour",
            "TYPE": "color",
            "DEFAULT": [
                0.86,
                0.2,
                0.12,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 p = isf_FragNormCoord - vec2(0.5);
    float wide = 0.5 * RENDERSIZE.x / RENDERSIZE.y;
    p.x *= 2.0 * wide;
    vec2 radius = vec2(0.150, 0.175);
    if (form == 1) {
        radius = vec2(0.110, 0.230);
    }
    if (form == 2) {
        radius = vec2(0.195, 0.135);
    }
    radius *= size * min(1.0, wide / 0.8);
    float rib = 1.0 - 0.30 * ribs * smoothstep(0.55, 1.0, sin(p.y * 22.0 / radius.y));
    vec2 scale = vec2(1.0) / radius;
    vec3 col = vec3(0.06, 0.025, 0.05) + vec3(0.05, 0.025, 0.02) * (0.5 - p.y);
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        // each lantern has its own slow cycles; the counts are folded to 0..1 so they stay exact for hours
        float swing = sin(6.2831853 * fract(TIME * speed * (0.021 + 0.004 * fi) + 0.37 * fi));
        float breath = 0.88 + 0.12 * sin(6.2831853 * fract(TIME * speed * (0.047 - 0.006 * fi) + 0.61 * fi));
        vec2 c = vec2((fi - 1.5) * wide * 0.46 + 0.035 * sway * swing, 0.02 + 0.11 * (fract(fi * 0.618 + 0.25) - 0.5));
        vec2 d = (p - c) * scale;
        float q = dot(d, d);
        float body = 1.0 - smoothstep(0.85, 1.0, q);
        float halo = 0.50 / (1.0 + 1.1 * q);
        vec3 lit = mix(glow.rgb, paper.rgb, min(q * q, 1.0)) * rib;
        col = mix(col + mix(paper.rgb, glow.rgb, 0.5) * halo * breath * 0.6, lit * breath, body);
        // the cord it hangs from
        float cord = clamp(1.75 - 500.0 * abs(p.x - c.x), 0.0, 1.0) * step(c.y + radius.y, p.y);
        col = mix(col, vec3(0.16, 0.10, 0.08), cord * 0.8);
    }
    gl_FragColor = vec4(col, 1.0);
}
