/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Layers of slow waves, deep water colours.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 5 layers, 10 sines a pixel",
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
            "NAME": "swell",
            "LABEL": "Swell",
            "TYPE": "float",
            "MIN": 0.3,
            "MAX": 1.5,
            "DEFAULT": 1.0
        },
        {
            "NAME": "night",
            "LABEL": "Night colours",
            "TYPE": "bool",
            "DEFAULT": false
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord;
    float x = uv.x * RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.2;
    vec3 top = night ? vec3(0.02, 0.02, 0.10) : vec3(0.05, 0.30, 0.45);
    vec3 deep = night ? vec3(0.0, 0.0, 0.02) : vec3(0.0, 0.05, 0.15);
    vec3 col = mix(deep, top, uv.y);
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        float level = 0.85 - 0.17 * fi;
        float wave = level + swell * (0.035 * sin(x * (2.0 + fi) + t * (1.0 + 0.25 * fi) + fi * 1.9)
            + 0.02 * sin(x * (4.5 + 1.5 * fi) - t * 1.3 + fi * 0.7));
        float under = 1.0 - smoothstep(wave - 0.004, wave + 0.004, uv.y);
        vec3 water = mix(top, deep, 0.25 + 0.17 * fi) + vec3(0.03, 0.06, 0.08) * (1.0 - smoothstep(0.0, 0.05, wave - uv.y));
        col = mix(col, water, under * 0.85);
    }
    gl_FragColor = vec4(col, 1.0);
}
