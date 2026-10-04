/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Slow ripples spreading from three wandering points.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 3 lengths and 9 sines a pixel",
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
            "NAME": "ripple",
            "LABEL": "Ripples",
            "TYPE": "float",
            "MIN": 4.0,
            "MAX": 24.0,
            "DEFAULT": 10.0
        },
        {
            "NAME": "calm",
            "LABEL": "Calm",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.4
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord - vec2(0.5);
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.1;
    float sum = 0.0;
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        vec2 c = vec2(0.55 * sin(t * (0.6 + 0.2 * fi) + fi * 2.1), 0.30 * cos(t * (0.5 + 0.15 * fi) + fi * 4.0));
        float r = length(uv - c);
        sum += sin(r * ripple * 6.28318 - t * 6.0 + fi) / (1.0 + r * (4.0 + 8.0 * calm));
    }
    float v = 0.5 + 0.5 * sum / 1.5;
    vec3 col = mix(vec3(0.0, 0.04, 0.12), vec3(0.10, 0.55, 0.60), smoothstep(0.25, 0.75, v));
    col += vec3(0.85, 0.95, 1.0) * smoothstep(0.80, 1.0, v) * 0.35;
    gl_FragColor = vec4(col, 1.0);
}
