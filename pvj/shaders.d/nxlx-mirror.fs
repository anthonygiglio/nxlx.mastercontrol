/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Travelling bands and rings folded by two, four or eight mirrors, so everything moves towards or away from the seams.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one square root and 2 sines a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Travel rate (bands a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "bands",
            "LABEL": "Bands",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 10.0,
            "DEFAULT": 4.0
        },
        {
            "NAME": "angle",
            "LABEL": "Angle (degrees)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 90.0,
            "DEFAULT": 35.0
        },
        {
            "NAME": "splits",
            "LABEL": "Mirrors",
            "TYPE": "long",
            "VALUES": [
                2,
                4,
                8
            ],
            "LABELS": [
                "Two halves",
                "Four quarters",
                "Eight wedges"
            ],
            "DEFAULT": 4
        },
        {
            "NAME": "rings",
            "LABEL": "Rings over the bands",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "inward",
            "LABEL": "Travel inward",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.2,
                0.9,
                0.7,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.3,
                0.08,
                0.5,
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
    // the mirrors: fold the picture onto one half, one quarter or one eighth of itself
    vec2 q = vec2(abs(p.x), p.y);
    if (splits >= 4) {
        q.y = abs(q.y);
    }
    if (splits == 8 && q.y > q.x) {
        q = q.yx;
    }
    float turn = angle * 0.0174533;
    // the travel in whole bands, folded to 0..1 so it stays exact for hours
    float travel = fract(TIME * min(rate, 3.0));
    if (inward) {
        travel = 1.0 - travel;
    }
    float s = sin(6.2831853 * (dot(q, vec2(cos(turn), sin(turn))) * bands - travel));
    float m = smoothstep(-0.12, 0.12, s);
    vec3 col = mix(second.rgb, first.rgb, m) * (0.72 + 0.28 * abs(s));
    if (rings) {
        // rings around a point on the fold line, travelling the other way: where they cross the bands the colours swap
        float ring = sin(6.2831853 * (length(q - vec2(0.45, 0.0)) * bands * 0.8 + travel));
        float over = smoothstep(0.55, 0.75, ring);
        col = mix(col, mix(first.rgb, second.rgb, m) * 0.9 + 0.08, over * 0.85);
    }
    // a thin shade along the seams, so the mirrors can be seen
    float seam = min(q.x, splits >= 4 ? q.y : 1.0);
    col *= 0.78 + 0.22 * smoothstep(0.0, 0.03, seam);
    col *= 1.0 - 0.25 * min(1.0, dot(p, p) * 1.4);
    col += vec3(0.02, 0.02, 0.03);
    gl_FragColor = vec4(col, 1.0);
}
