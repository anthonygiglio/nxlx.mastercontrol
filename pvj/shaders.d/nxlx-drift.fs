/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Soft clouds of colour drifting slowly.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "medium: 2 clouds of 3 octaves, 24 hashes a pixel",
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
            "MIN": 1.0,
            "MAX": 4.0,
            "DEFAULT": 2.0
        },
        {
            "NAME": "warmth",
            "LABEL": "Warmth",
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

float cell(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float soft(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(cell(i), cell(i + vec2(1.0, 0.0)), u.x), mix(cell(i + vec2(0.0, 1.0)), cell(i + vec2(1.0, 1.0)), u.x), u.y);
}

float cloud(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 3; i++) {
        v += a * soft(p);
        p = p * 2.03 + vec2(17.0, 9.0);
        a *= 0.5;
    }
    return v;
}

void main() {
    vec2 uv = isf_FragNormCoord;
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.04;
    float a = cloud(uv * scale + vec2(t, t * 0.6));
    float b = cloud(uv * scale * 0.7 - vec2(t * 0.8, -t * 0.3) + vec2(40.0));
    vec3 cool = mix(vec3(0.02, 0.05, 0.20), vec3(0.10, 0.55, 0.70), a);
    vec3 warm = mix(vec3(0.20, 0.03, 0.10), vec3(0.95, 0.55, 0.25), a);
    vec3 col = mix(cool, warm, warmth) * (0.35 + 0.9 * b);
    gl_FragColor = vec4(col, 1.0);
}
