/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Large soft discs of light that cross slowly, like street lamps far out of focus.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "medium: 4 discs, 8 sines a pixel",
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
            "LABEL": "Disc size",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 1.6,
            "DEFAULT": 1.0
        },
        {
            "NAME": "blur",
            "LABEL": "Out of focus",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 1.0,
            "DEFAULT": 0.35
        },
        {
            "NAME": "rim",
            "LABEL": "Bright rim",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.4
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.62,
                0.3,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.4,
                0.55,
                1.0,
                1.0
            ]
        },
        {
            "NAME": "ground",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.06,
                0.04,
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
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    p.x *= wide;
    vec3 col = ground.rgb * (1.15 - 0.5 * dot(p, p)) + 0.015;
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        // each disc wanders on its own slow path; the cycles are folded to 0..1 first, so the angles stay exact for
        // hours. The picture's width sets how far across a disc may go.
        vec2 phase = 6.2831853 * fract(TIME * speed * vec2(0.011 + 0.003 * fi, 0.008 + 0.002 * fi) + vec2(0.23, 0.61) * fi);
        vec2 c = vec2(0.42 * wide, 0.30) * sin(phase);
        float radius = size * (0.20 + 0.035 * fi);
        vec2 d = (p - c) / radius;
        float q = dot(d, d);
        float disc = 1.0 - smoothstep(1.0 - blur, 1.0, q);
        float ring = rim * smoothstep(0.45, 1.0 - 0.5 * blur, q) * disc;
        vec3 tone = mix(first.rgb, second.rgb, fract(fi * 0.5) * 2.0 * 0.85 + 0.05 * fi);
        // light adds up where discs overlap, but never to more than the light itself
        col += tone * (0.42 + 0.10 * q + 0.40 * ring) * disc * (1.0 - 0.55 * col);
    }
    gl_FragColor = vec4(col, 1.0);
}
