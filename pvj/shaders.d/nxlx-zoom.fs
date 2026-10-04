/*{
    "ISFVSN": "2",
    "DESCRIPTION": "An endless zoom through rings, squares or diamonds that get smaller towards one point, with a twist.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: 2 logarithms, one square root and 3 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Zoom rate (shapes a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "density",
            "LABEL": "Shapes",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 5.0,
            "DEFAULT": 2.5
        },
        {
            "NAME": "twist",
            "LABEL": "Twist",
            "TYPE": "float",
            "MIN": -1.0,
            "MAX": 1.0,
            "DEFAULT": 0.3
        },
        {
            "NAME": "shape",
            "LABEL": "Shape",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Rings",
                "Squares",
                "Diamonds"
            ],
            "DEFAULT": 1
        },
        {
            "NAME": "outward",
            "LABEL": "Zoom out",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.95,
                0.95,
                0.85,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.28,
                0.6,
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
    float r = max(length(p), 0.0005);
    // the depth: equal steps of it are shapes that each are a fixed part smaller than the last, without end
    float deep = log(r);
    // turn the picture a little more the deeper it goes
    float spun = twist * deep * 1.2;
    float c = cos(spun);
    float s = sin(spun);
    vec2 q = vec2(c * p.x - s * p.y, s * p.x + c * p.y);
    float size = r;
    if (shape == 1) {
        size = max(abs(q.x), abs(q.y));
    }
    if (shape == 2) {
        size = (abs(q.x) + abs(q.y)) * 0.7071;
    }
    // the zoom in whole shapes, folded to 0..1 so it stays exact for hours
    float travel = fract(TIME * min(rate, 3.0) * 0.5);
    if (outward) {
        travel = 1.0 - travel;
    }
    float v = sin(6.2831853 * (log(max(size, 0.0005)) * density * 0.5 + travel));
    float m = smoothstep(-0.15, 0.15, v);
    // at the very centre the shapes are smaller than the pixels: let them melt into one tone there
    float near = smoothstep(0.0, 0.05, r);
    vec3 col = mix(second.rgb, first.rgb, mix(0.5, m, near)) * (0.70 + 0.30 * abs(v) * near);
    col *= 0.55 + 0.45 * smoothstep(0.0, 0.35, r);
    col += vec3(0.02, 0.02, 0.03);
    gl_FragColor = vec4(col, 1.0);
}
