/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A flight through a patterned tunnel, with the travel rate, the twist and the vanishing point to play.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one atan, one square root, one division and 2 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Travel rate (rings a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "twist",
            "LABEL": "Twist",
            "TYPE": "float",
            "MIN": -2.0,
            "MAX": 2.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "depth",
            "LABEL": "Depth",
            "TYPE": "float",
            "MIN": 0.15,
            "MAX": 0.6,
            "DEFAULT": 0.3
        },
        {
            "NAME": "pattern",
            "LABEL": "Pattern",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Checker",
                "Rings",
                "Spiral"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "sides",
            "LABEL": "Segments",
            "TYPE": "long",
            "VALUES": [
                4,
                6,
                8,
                12,
                16
            ],
            "LABELS": [
                "4",
                "6",
                "8",
                "12",
                "16"
            ],
            "DEFAULT": 8
        },
        {
            "NAME": "reverse",
            "LABEL": "Fly backwards",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "wall",
            "LABEL": "Wall colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.85,
                0.95,
                1.0
            ]
        },
        {
            "NAME": "centre",
            "LABEL": "Vanishing point",
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

// The tunnel is not marched: a pixel's distance from the vanishing point gives its depth at once (depth over radius),
// and its angle gives the place around the wall.
void main() {
    vec2 p = isf_FragNormCoord - clamp(centre, 0.0, 1.0);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    float r = max(length(p), 0.0005);
    float around = atan(p.y, p.x + 0.00001) / 6.2831853;
    float z = depth / r;
    // the travel: whole rings a second, folded to 0..1 so it stays exact for hours
    float travel = fract(TIME * rate);
    if (reverse) {
        travel = 1.0 - travel;
    }
    float along = sin(6.2831853 * (z + travel));
    float across = sin(6.2831853 * (around * float(sides) + twist * z));
    float v = along * across;
    if (pattern == 1) {
        v = along;
    }
    if (pattern == 2) {
        v = sin(6.2831853 * (around * float(sides) * 0.5 + z + travel));
    }
    // far away the pattern is finer than the pixels: let it melt into the dark there
    float far = smoothstep(0.10, 0.75, r / depth);
    float edge = 0.06 + 0.5 * (1.0 - far);
    float m = smoothstep(-edge, edge, v);
    vec3 dark = wall.rgb * 0.10 + vec3(0.02, 0.01, 0.05);
    vec3 col = mix(dark, wall.rgb, m) * far * (0.55 + 0.45 * min(1.0, r * 1.6));
    // the other half of the pattern gets a second tone made from the wall colour
    col += wall.brg * (1.0 - m) * 0.30 * far;
    col += vec3(0.02, 0.02, 0.04);
    gl_FragColor = vec4(col, 1.0);
}
