/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Soft clouds of colour drifting slowly.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "high: 2 clouds of 3 octaves, 24 lattice values a pixel",
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
            "DEFAULT": 1.6
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
    vec2 a = fract(p * vec2(0.3127, 0.2713) + vec2(0.137, 0.731));
    float m = a.x * 53.21 + a.y * 87.43 + a.x * a.y * 29.3;
    return fract(m * (1.0 + fract(m * 0.6173)));
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
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 3; i++) {
        v += a * soft(p);
        p = vec2(1.62 * p.x + 1.22 * p.y, 1.62 * p.y - 1.22 * p.x) + vec2(17.0, 9.0);    // turned as well as doubled: no grid shows
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
    // Two palettes side by side, never blended half and half (that was grey): the second cloud says where each one
    // lies, and Warmth moves the border. The first cloud is stretched to the full range for contrast.
    a = smoothstep(0.22, 0.78, a);
    vec3 cool = mix(vec3(0.01, 0.03, 0.22), vec3(0.05, 0.75, 0.90), a);
    vec3 warm = mix(vec3(0.25, 0.01, 0.12), vec3(1.00, 0.62, 0.15), a);
    vec3 col = mix(cool, warm, smoothstep(0.38, 0.62, b + (warmth - 0.5) * 0.6)) * (0.30 + 1.1 * a * a);
    gl_FragColor = vec4(col, 1.0);
}
