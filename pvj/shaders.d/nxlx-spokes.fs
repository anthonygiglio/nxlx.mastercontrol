/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A wheel of spokes cut into bands that turn against each other, with the turning rate, the curl and the centre to play.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one atan, one square root and one sine a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Turning rate (spokes a second)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 3.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "spokes",
            "LABEL": "Spokes",
            "TYPE": "long",
            "VALUES": [
                2,
                3,
                4,
                6,
                8,
                12,
                16
            ],
            "LABELS": [
                "2",
                "3",
                "4",
                "6",
                "8",
                "12",
                "16"
            ],
            "DEFAULT": 8
        },
        {
            "NAME": "bands",
            "LABEL": "Bands",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 8.0,
            "DEFAULT": 4.0
        },
        {
            "NAME": "curl",
            "LABEL": "Curl",
            "TYPE": "float",
            "MIN": -2.0,
            "MAX": 2.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "mirror",
            "LABEL": "Mirror left and right",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.3,
                0.45,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.12,
                0.08,
                0.35,
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
    if (mirror) {
        p.x = abs(p.x);
    }
    float r = max(length(p), 0.0005);
    float around = atan(p.y, p.x + 0.00001) / 6.2831853;
    float n = float(spokes);
    // the bands: every other one turns the other way
    float count = floor(bands + 0.5);
    float band = floor(r * count * 1.6);
    float way = 1.0 - 2.0 * mod(band, 2.0);
    // the turn: whole spokes a second, folded to 0..1 so it stays exact for hours
    float turn = fract(TIME * min(rate, 3.0));
    float s = sin(6.2831853 * (around * n + way * turn + curl * r * 2.0 + band * 0.25));
    // close to the centre the spokes are finer than the pixels: let them melt together there
    float edge = 0.08 + 0.012 * n / r;
    float m = smoothstep(-edge, edge, s);
    // a soft seam between bands, and a shade along each band so it reads as a ring
    float inband = fract(r * count * 1.6);
    float seam = smoothstep(0.0, 0.06, inband) * smoothstep(0.0, 0.06, 1.0 - inband);
    float shade = 0.70 + 0.30 * inband;
    vec3 col = mix(second.rgb, first.rgb, m) * shade * (0.35 + 0.65 * seam);
    col *= 1.0 - 0.25 * min(1.0, r);
    col += vec3(0.02, 0.02, 0.03);
    gl_FragColor = vec4(col, 1.0);
}
