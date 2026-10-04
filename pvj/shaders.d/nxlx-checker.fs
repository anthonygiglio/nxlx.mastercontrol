/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A checkerboard that travels and is bent three ways: into waves, into a bulge or into a twirl.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 6 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Travel rate (squares a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "squares",
            "LABEL": "Squares",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 16.0,
            "DEFAULT": 7.0
        },
        {
            "NAME": "warp",
            "LABEL": "Bend",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.55
        },
        {
            "NAME": "shape",
            "LABEL": "Kind of bend",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Waves",
                "Bulge",
                "Twirl"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "upward",
            "LABEL": "Travel upward",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "invert",
            "LABEL": "Invert",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.9,
                0.3,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.08,
                0.1,
                0.3,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 p = (isf_FragNormCoord - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    // a slow cycle for the bend, and the travel in squares; both are folded so they stay exact for hours
    float slow = 6.2831853 * fract(TIME * (0.05 + 0.1 * rate));
    float travel = 2.0 * fract(TIME * rate * 0.5);
    float r2 = dot(p, p);
    vec2 q = p;
    if (shape == 0) {
        q += warp * 0.16 * sin(p.yx * 4.5 + vec2(slow, -slow));
    }
    if (shape == 1) {
        q *= 1.0 - warp * 0.55 * (0.6 + 0.4 * sin(slow)) / (1.0 + 4.0 * r2);
    }
    if (shape == 2) {
        float angle = warp * 2.2 * sin(slow) / (1.0 + 5.0 * r2);
        float c = cos(angle);
        float s = sin(angle);
        q = vec2(c * q.x - s * q.y, s * q.x + c * q.y);
    }
    vec2 g = q * squares;
    if (upward) {
        g.y -= travel;
    } else {
        g.x += travel;
    }
    float v = sin(3.14159 * g.x) * sin(3.14159 * g.y);
    float m = smoothstep(-0.06, 0.06, v);
    if (invert) {
        m = 1.0 - m;
    }
    // each square is a little rounded by light, so the board is not two flat colours
    float lift = 0.78 + 0.22 * abs(v);
    vec3 col = mix(second.rgb, first.rgb, m) * lift;
    col *= 1.0 - 0.30 * min(1.0, r2 * 1.6);
    col += vec3(0.02, 0.02, 0.03);
    gl_FragColor = vec4(col, 1.0);
}
