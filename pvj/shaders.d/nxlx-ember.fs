/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Warm glowing blobs that merge and part, like embers.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 5 blobs, 10 sines a pixel",
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
            "NAME": "size",
            "LABEL": "Blob size",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 1.5,
            "DEFAULT": 1.0
        },
        {
            "NAME": "glow",
            "LABEL": "Glow colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.45,
                0.12,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord - vec2(0.5);
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.12;
    float field = 0.0;
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        vec2 c = vec2(0.70 * sin(t * (0.7 + 0.13 * fi) + fi * 2.4), 0.38 * cos(t * (0.9 - 0.11 * fi) + fi * 1.3));
        vec2 d = uv - c;
        field += (0.035 + 0.008 * fi) * size / (dot(d, d) + 0.02);
    }
    float core = smoothstep(0.6, 1.6, field);
    float halo = smoothstep(0.1, 1.0, field);
    vec3 col = glow.rgb * halo * 0.55 + vec3(1.0, 0.9, 0.7) * core * 0.6;
    col += vec3(0.05, 0.0, 0.02) * (1.0 - length(uv));
    gl_FragColor = vec4(max(col, vec3(0.0)), 1.0);
}
