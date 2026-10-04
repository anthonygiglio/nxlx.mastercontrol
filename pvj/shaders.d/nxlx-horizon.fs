/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A wide sky at dusk with a slow sun and drifting haze.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 4 sines, one length and 4 exponentials a pixel",
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
            "NAME": "sun",
            "LABEL": "Sun height",
            "TYPE": "float",
            "MIN": 0.35,
            "MAX": 0.7,
            "DEFAULT": 0.45
        },
        {
            "NAME": "haze",
            "LABEL": "Haze",
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
    float aspect = RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.05;
    float edge = 0.30;
    vec3 sky = mix(vec3(0.95, 0.45, 0.20), vec3(0.05, 0.06, 0.25), smoothstep(edge, 1.0, uv.y));
    vec2 centre = vec2(0.5 + 0.25 * sin(t), sun + 0.03 * sin(t * 1.7));
    vec2 d = vec2((uv.x - centre.x) * aspect, uv.y - centre.y);
    float r = length(d);
    sky += vec3(1.0, 0.75, 0.45) * exp(-r * 6.0) * 0.9;
    sky += vec3(1.0, 0.95, 0.80) * (1.0 - smoothstep(0.070, 0.078, r));
    float band = sin(uv.y * 40.0 + 2.0 * sin(uv.x * aspect * 2.0 + t * 3.0)) * 0.5 + 0.5;
    sky = mix(sky, vec3(0.90, 0.60, 0.50), haze * 0.25 * band * exp(-abs(uv.y - edge - 0.15) * 8.0));
    float shimmer = 0.6 + 0.4 * sin(uv.x * aspect * 30.0 + uv.y * 90.0 - t * 20.0);
    vec3 ground = vec3(0.03, 0.02, 0.06) + vec3(0.50, 0.22, 0.10) * exp(-(edge - uv.y) * 9.0) * shimmer;
    vec3 col = mix(ground, sky, smoothstep(edge - 0.003, edge + 0.003, uv.y));
    gl_FragColor = vec4(col, 1.0);
}
