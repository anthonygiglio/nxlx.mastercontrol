/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A flower of three rings of petals that open and close a little and turn slowly against each other.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "medium: 3 rings, one atan, one square root, 6 sines a pixel",
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
            "NAME": "petals",
            "LABEL": "Petals",
            "TYPE": "long",
            "VALUES": [
                5,
                6,
                8,
                10,
                12
            ],
            "LABELS": [
                "5",
                "6",
                "8",
                "10",
                "12"
            ],
            "DEFAULT": 8
        },
        {
            "NAME": "size",
            "LABEL": "Size",
            "TYPE": "float",
            "MIN": 0.6,
            "MAX": 1.4,
            "DEFAULT": 1.0
        },
        {
            "NAME": "soft",
            "LABEL": "Softness",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "outer",
            "LABEL": "Outer colour",
            "TYPE": "color",
            "DEFAULT": [
                0.55,
                0.16,
                0.38,
                1.0
            ]
        },
        {
            "NAME": "inner",
            "LABEL": "Inner colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.78,
                0.5,
                1.0
            ]
        },
        {
            "NAME": "ground",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.07,
                0.05,
                0.16,
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

void main() {
    vec2 p = isf_FragNormCoord - clamp(centre, 0.0, 1.0);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    float r = length(p);
    float a = atan(p.y, p.x + 0.00001);
    float n = float(petals);
    vec3 col = ground.rgb * (1.0 - 0.4 * min(1.0, r)) + 0.02;
    // a faint glow behind the flower
    col += outer.rgb * 0.30 / (1.0 + 5.0 * r * r);
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        // each ring turns its own way and breathes at its own pace; the cycles are folded to 0..1 first, so the
        // angles stay exact for hours
        float way = 1.0 - 2.0 * mod(fi, 2.0);
        float turn = way * 6.2831853 * fract(TIME * speed * (0.004 + 0.002 * fi));
        float breath = sin(6.2831853 * fract(TIME * speed * (0.023 - 0.004 * fi) + 0.3 * fi));
        float reach = size * (0.44 - 0.135 * fi) * (1.0 + 0.05 * breath);
        // the ring's outline: further out at the tip of each petal, closer in between two petals
        float tip = 0.5 + 0.5 * cos(n * a + turn + fi * 3.14159);
        float edge = reach * (0.62 + 0.38 * tip);
        float blur = 0.012 + 0.07 * soft * reach;
        float inside = 1.0 - smoothstep(edge - blur, edge + blur, r);
        vec3 tone = mix(outer.rgb, inner.rgb, fi * 0.5);
        // lighter along the middle of a petal, darker at its root
        tone *= 0.62 + 0.38 * smoothstep(0.0, edge, r) * (0.7 + 0.3 * tip);
        col = mix(col, tone, inside * (0.92 - 0.06 * fi));
    }
    gl_FragColor = vec4(col, 1.0);
}
