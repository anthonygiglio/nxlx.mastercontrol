/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Rows of arrowheads that march in lanes, with one in every few lit as it passes on the beat.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one sine and about 50 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "March rate (arrows a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.5
        },
        {
            "NAME": "count",
            "LABEL": "Arrows",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 16.0,
            "DEFAULT": 6.0
        },
        {
            "NAME": "point",
            "LABEL": "How pointed",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "lanes",
            "LABEL": "Lanes",
            "TYPE": "long",
            "VALUES": [
                1,
                2,
                4,
                8
            ],
            "LABELS": [
                "1",
                "2",
                "4",
                "8"
            ],
            "DEFAULT": 2
        },
        {
            "NAME": "alternate",
            "LABEL": "Every other lane the other way",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "sideways",
            "LABEL": "March sideways",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "Arrow colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.55,
                0.1,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Background colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.12,
                0.2,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord;
    vec2 p = (uv - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    // along the march and across it; the lanes divide the picture across
    float along = sideways ? p.x : p.y;
    float across = sideways ? uv.y : uv.x;
    float span = sideways ? 1.0 : wide;
    float n = float(lanes);
    float lane = min(floor(across * n), n - 1.0);
    float inlane = across * n - lane;
    float way = alternate ? 1.0 - 2.0 * mod(lane, 2.0) : 1.0;
    // an arrowhead: the row is pushed back the further a pixel is from the middle of its lane
    float back = abs(inlane - 0.5) * point * span / n * 2.0;
    // the march in whole arrows, folded so it stays exact for hours; 4 arrows are one round of the lit one
    float travel = fract(TIME * min(rate, 3.0) * 0.25) * 4.0;
    float v = (way * along + back) * count * 0.5 - travel + 64.0;
    float row = floor(v);
    float inrow = v - row;
    float arrow = smoothstep(0.0, 0.08, inrow) * (1.0 - smoothstep(0.46, 0.54, inrow));
    // one arrow in four is the bright one, the others glow a little
    float lit = 1.0 - step(0.5, mod(row, 4.0));
    float edge = smoothstep(0.0, 0.04, inlane) * smoothstep(0.0, 0.04, 1.0 - inlane);
    vec3 dim = mix(second.rgb, first.rgb, 0.45) * (0.55 + 0.45 * inrow * 2.0);
    vec3 bright = first.rgb * (0.80 + 0.20 * sin(3.14159 * inlane)) + vec3(0.10);
    vec3 col = second.rgb * (0.50 + 0.35 * uv.y) + vec3(0.02, 0.02, 0.03);
    col = mix(col, mix(dim, bright, lit), arrow * edge);
    // a slope of light over the whole wall and across each lane, so thin arrows are still a shaded picture
    col *= 0.78 + 0.22 * uv.y + 0.14 * inlane * (1.0 - inlane) * 4.0;
    gl_FragColor = vec4(col, 1.0);
}
