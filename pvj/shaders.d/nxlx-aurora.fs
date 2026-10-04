/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Slow curtains of light over a dark sky.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 4 bands, about 12 sines a pixel",
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
            "NAME": "height",
            "LABEL": "Height",
            "TYPE": "float",
            "MIN": 0.6,
            "MAX": 1.3,
            "DEFAULT": 1.0
        },
        {
            "NAME": "tint",
            "LABEL": "Tint",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.0
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

vec3 tone(float t) {
    return vec3(0.5) + vec3(0.5) * cos(6.28318 * (vec3(t) + vec3(0.55, 0.25, 0.05) + vec3(tint)));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float x = uv.x * RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.15;
    vec3 col = vec3(0.0, 0.01, 0.04) * (1.0 - uv.y);
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        float wave = 0.45 + 0.10 * fi + 0.10 * sin(x * (1.3 + 0.4 * fi) + t * (1.0 + 0.3 * fi) + fi * 1.7)
            + 0.05 * sin(x * 3.1 - t * 1.7 + fi);
        float d = abs(uv.y - wave * height);
        float band = 1.0 - smoothstep(0.0, 0.30, d);
        float ripple = 0.65 + 0.35 * sin(x * 9.0 + t * 3.0 + fi * 2.0);
        col += tone(0.12 * fi + 0.05 * sin(t + x)) * band * band * ripple * (0.45 - 0.07 * fi);
    }
    gl_FragColor = vec4(col, 1.0);
}
