/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Three ranges of hills in morning mist under a pale sun, each drifting at its own slow pace.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 3 ranges, 6 sines and one division a pixel",
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
            "LABEL": "Hill height",
            "TYPE": "float",
            "MIN": 0.4,
            "MAX": 1.5,
            "DEFAULT": 1.0
        },
        {
            "NAME": "mist",
            "LABEL": "Mist",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "sun",
            "LABEL": "Sun",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "sky",
            "LABEL": "Sky colour",
            "TYPE": "color",
            "DEFAULT": [
                0.95,
                0.72,
                0.6,
                1.0
            ]
        },
        {
            "NAME": "far",
            "LABEL": "Mist colour",
            "TYPE": "color",
            "DEFAULT": [
                0.74,
                0.7,
                0.8,
                1.0
            ]
        },
        {
            "NAME": "hill",
            "LABEL": "Hill colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.14,
                0.26,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

void main() {
    vec2 uv = isf_FragNormCoord;
    float x = (uv.x - 0.5) * RENDERSIZE.x / RENDERSIZE.y;
    vec3 col = mix(far.rgb, sky.rgb, smoothstep(0.25, 1.0, uv.y)) * (0.80 + 0.20 * uv.y);
    if (sun) {
        vec2 d = vec2(x - 0.28, uv.y - 0.74);
        float r2 = dot(d, d);
        col += vec3(1.0, 0.92, 0.8) * (0.30 / (1.0 + 60.0 * r2) + 0.5 * (1.0 - smoothstep(0.0045, 0.0060, r2)));
    }
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        // the range's own slow drift, folded to 0..1 before it becomes an angle so it stays exact for hours
        float drift = 6.2831853 * fract(TIME * speed * (0.004 + 0.003 * fi) + 0.4 * fi);
        float top = 0.56 - 0.17 * fi + height * (0.075 * sin(x * (1.6 + 0.9 * fi) + drift + fi * 2.1)
            + 0.035 * sin(x * (4.1 + 1.7 * fi) - drift * 2.0 + fi * 0.8));
        float under = 1.0 - smoothstep(top - 0.006, top + 0.006, uv.y);
        // nearer ranges are darker; mist lies in the valleys, thicker the further down the slope
        vec3 body = mix(far.rgb, hill.rgb, 0.30 + 0.33 * fi);
        float pool = mist * smoothstep(0.0, 0.30, top - uv.y) * (0.75 - 0.2 * fi);
        col = mix(col, mix(body, far.rgb, pool), under);
    }
    gl_FragColor = vec4(col, 1.0);
}
