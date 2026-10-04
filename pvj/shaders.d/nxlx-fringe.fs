/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Soft fringes where slow ripples from two or three wandering sources meet.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 3 square roots and 9 sines a pixel",
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
            "NAME": "waves",
            "LABEL": "Ripples",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 10.0,
            "DEFAULT": 4.5
        },
        {
            "NAME": "apart",
            "LABEL": "Sources apart",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 0.6,
            "DEFAULT": 0.35
        },
        {
            "NAME": "crisp",
            "LABEL": "Crispness",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.15
        },
        {
            "NAME": "three",
            "LABEL": "Third source",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "crest",
            "LABEL": "Crest colour",
            "TYPE": "color",
            "DEFAULT": [
                0.55,
                0.85,
                0.8,
                1.0
            ]
        },
        {
            "NAME": "trough",
            "LABEL": "Trough colour",
            "TYPE": "color",
            "DEFAULT": [
                0.04,
                0.1,
                0.3,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

// Where a slow cycle stands, as an angle. The cycle count is folded to 0..1 first, so the angle stays exact for hours.
float swing(float rate, float start) {
    return 6.2831853 * fract(TIME * speed * rate + start);
}

void main() {
    vec2 p = isf_FragNormCoord - vec2(0.5);
    float wide = RENDERSIZE.x / RENDERSIZE.y;
    p.x *= wide;
    float k = waves * 6.2831853;
    float out1 = swing(0.050, 0.0);
    // three sources on slow, different paths around the middle; the picture's width sets how far they wander
    vec2 reach = vec2(apart * wide * 0.56, apart * 0.5);
    vec2 a = reach * vec2(sin(swing(0.0070, 0.10)), sin(swing(0.0113, 0.40)));
    vec2 b = reach * vec2(sin(swing(0.0091, 0.55)), sin(swing(0.0061, 0.80)));
    vec2 c = reach * vec2(sin(swing(0.0053, 0.30)), sin(swing(0.0083, 0.95)));
    float sum = sin(k * length(p - a) - out1) + sin(k * length(p - b) - out1);
    float parts = 2.0;
    if (three) {
        sum += sin(k * length(p - c) * 0.8 - out1);
        parts = 3.0;
    }
    // where the ripples agree it is bright, where they cancel it is dark
    float v = sum / parts;
    float light = v * v;
    light = mix(light, smoothstep(0.08, 0.55, light), crisp);
    vec3 col = mix(trough.rgb, crest.rgb, 0.16 + 0.66 * light);
    col *= 1.0 - 0.35 * dot(p, p);
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
