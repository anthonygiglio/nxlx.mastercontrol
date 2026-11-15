/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Folded clouds of gas: noise bent by more noise.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "high: 3 clouds of 2 octaves, 24 lattice values a pixel",
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
            "NAME": "fold",
            "LABEL": "Fold",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 3.0,
            "DEFAULT": 1.5
        },
        {
            "NAME": "scale",
            "LABEL": "Scale",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 3.0,
            "DEFAULT": 1.6
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

float cell(vec2 p) {
    vec2 a = fract(p * vec2(0.2861, 0.3359) + vec2(0.419, 0.263));
    float m = a.x * 61.37 + a.y * 79.11 + a.x * a.y * 33.7;
    return fract(m * (1.0 + fract(m * 0.5381)));
}

// Smooth noise: the four corner values of the lattice square around p, blended with an eased weight.
float soft(vec2 p) {
    vec2 corner = floor(p);
    vec2 w = p - corner;
    w = w * w * w * (w * (w * 6.0 - 15.0) + 10.0);
    float low = mix(cell(corner), cell(corner + vec2(1.0, 0.0)), w.x);
    float high = mix(cell(corner + vec2(0.0, 1.0)), cell(corner + vec2(1.0, 1.0)), w.x);
    return mix(low, high, w.y);
}

float cloud(vec2 p) {
    return 0.65 * soft(p) + 0.35 * soft(p * 2.1 + vec2(31.0, 7.0));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    vec2 p = uv * scale;
    float t = TIME * speed * 0.03;
    vec2 bend = vec2(cloud(p + vec2(t, 0.0)), cloud(p + vec2(5.2, 1.3 - t)));
    float v = cloud(p + fold * bend + vec2(-t * 0.5, t * 0.3));
    vec3 col = mix(vec3(0.02, 0.0, 0.08), vec3(0.45, 0.10, 0.55), smoothstep(0.2, 0.7, v));
    col = mix(col, vec3(0.10, 0.70, 0.85), smoothstep(0.55, 0.95, v) * bend.x);
    col += vec3(1.0, 0.8, 0.6) * smoothstep(0.80, 1.0, v) * 0.5;
    gl_FragColor = vec4(col, 1.0);
}
