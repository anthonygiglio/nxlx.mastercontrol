/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Two fine grids turning against each other: a slow moire.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 4 sines a pixel",
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
            "NAME": "density",
            "LABEL": "Density",
            "TYPE": "float",
            "MIN": 8.0,
            "MAX": 40.0,
            "DEFAULT": 18.0
        },
        {
            "NAME": "ink",
            "LABEL": "Colour",
            "TYPE": "color",
            "DEFAULT": [
                0.35,
                0.75,
                1.0,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

vec2 turn(vec2 p, float a) {
    float c = cos(a);
    float s = sin(a);
    return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
}

float dots(vec2 p) {
    return 0.5 + 0.25 * (sin(p.x) + sin(p.y));
}

void main() {
    vec2 uv = isf_FragNormCoord - vec2(0.5);
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.03;
    float g1 = dots(turn(uv, t) * density * 6.28318);
    float g2 = dots(turn(uv, -t * 0.7 + 0.3) * density * 6.28318 * 1.04);
    float m = smoothstep(0.15, 0.75, g1 * g2);
    vec3 col = ink.rgb * m + vec3(0.02, 0.02, 0.05);
    col *= 1.0 - 0.6 * dot(uv, uv);
    gl_FragColor = vec4(col, 1.0);
}
