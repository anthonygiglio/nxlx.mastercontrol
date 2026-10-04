/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Fine flowing lines, like folds of silk in a slow wind.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 7 sines a pixel",
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
            "NAME": "lines",
            "LABEL": "Lines",
            "TYPE": "float",
            "MIN": 4.0,
            "MAX": 30.0,
            "DEFAULT": 12.0
        },
        {
            "NAME": "sheen",
            "LABEL": "Sheen",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord;
    float x = uv.x * RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.1;
    float bend = 0.20 * sin(x * 1.7 + t) + 0.10 * sin(x * 3.9 - t * 1.3) + 0.05 * sin(x * 7.3 + t * 0.7);
    float y = uv.y + bend;
    float fine = 0.5 + 0.5 * sin(y * lines * 6.28318 + 1.5 * sin(x * 2.0 + t));
    float fold = 0.5 + 0.5 * sin(y * 3.0 + x * 1.2 - t * 0.8);
    vec3 base = mix(vec3(0.10, 0.02, 0.18), vec3(0.75, 0.25, 0.45), fold);
    vec3 col = base * (0.35 + 0.65 * fine) + vec3(1.0, 0.9, 0.95) * sheen * fine * fine * fine * fine * fold * 0.5;
    gl_FragColor = vec4(col, 1.0);
}
