/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A slow net of light, like sun on the floor of a shallow pool.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 3 rounds of bending, 9 sines and one division a pixel",
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
            "NAME": "scale",
            "LABEL": "Scale",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 9.0,
            "DEFAULT": 5.5
        },
        {
            "NAME": "bend",
            "LABEL": "Ripple",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 0.9,
            "DEFAULT": 0.6
        },
        {
            "NAME": "focus",
            "LABEL": "Sharpness of the light",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.45
        },
        {
            "NAME": "water",
            "LABEL": "Water colour",
            "TYPE": "color",
            "DEFAULT": [
                0.02,
                0.2,
                0.36,
                1.0
            ]
        },
        {
            "NAME": "light",
            "LABEL": "Light colour",
            "TYPE": "color",
            "DEFAULT": [
                0.7,
                0.95,
                0.9,
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
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    vec2 q = p * scale;
    // bend the plane three times, each time across the last bend; each round has its own slow cycle, folded to
    // 0..1 before it becomes an angle so that it stays exact for hours
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        vec2 phase = 6.2831853 * fract(TIME * speed * vec2(0.021 + 0.006 * fi, 0.017 - 0.004 * fi) + vec2(0.31, 0.67) * fi);
        q += bend * sin(q.yx * (1.25 + 0.35 * fi) + phase);
    }
    // the light gathers along the lines where two waves over the bent plane cancel
    float web = abs(sin(q.x) + sin(q.y)) * 0.5;
    float gather = 1.0 / (1.0 + (4.0 + 26.0 * focus) * web);
    float deep = 0.5 + 0.5 * sin(q.x * 0.5 + q.y * 0.3);
    vec3 col = water.rgb * (0.55 + 0.45 * deep);
    col += light.rgb * gather * gather * (0.55 + 0.25 * deep);
    col = col / (1.0 + 0.25 * col);
    gl_FragColor = vec4(col, 1.0);
}
