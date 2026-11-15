/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Wide bands of colour lying over each other and swaying slowly, like layers of cloth.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 4 sines a pixel",
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
            "NAME": "bands",
            "LABEL": "Bands",
            "TYPE": "float",
            "MIN": 3.0,
            "MAX": 12.0,
            "DEFAULT": 6.0
        },
        {
            "NAME": "sway",
            "LABEL": "Sway",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.6
        },
        {
            "NAME": "blend",
            "LABEL": "Soft edges",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.0,
            "DEFAULT": 0.45
        },
        {
            "NAME": "palette",
            "LABEL": "Colours",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3
            ],
            "LABELS": [
                "Own two colours",
                "Warm",
                "Sea",
                "Forest"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "first",
            "LABEL": "Own colour, bottom",
            "TYPE": "color",
            "DEFAULT": [
                0.16,
                0.05,
                0.3,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Own colour, top",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.62,
                0.42,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

// Where a slow cycle stands, as an angle. The cycle count is folded to 0..1 first, so the angle stays exact for hours.
float swing(float rate) {
    return 6.2831853 * fract(TIME * speed * rate);
}

// A colour scale of three steps: low, middle, high.
vec3 scale3(vec3 low, vec3 middle, vec3 high, float t) {
    return t < 0.5 ? mix(low, middle, t * 2.0) : mix(middle, high, t * 2.0 - 1.0);
}

void main() {
    vec2 uv = isf_FragNormCoord;
    float x = (uv.x - 0.5) * RENDERSIZE.x / RENDERSIZE.y;
    float a = swing(0.017);
    float b = swing(0.011);
    float count = floor(bands + 0.5);
    // each band's edge sways by itself: the sway depends a little on the height too
    float lift = sway * (0.055 * sin(x * 1.9 + a + uv.y * 2.0) + 0.030 * sin(x * 4.3 - b + uv.y * 5.0));
    float v = clamp(uv.y + lift, 0.0, 0.9999) * count;
    float band = floor(v);
    float inside = v - band;
    float t = (band + smoothstep(0.5 - 0.5 * blend, 0.5 + 0.5 * blend, inside)) / count;
    vec3 col = scale3(first.rgb, mix(first.rgb, second.rgb, 0.5) * 1.1 + 0.03, second.rgb, t);
    if (palette == 1) {
        col = scale3(vec3(0.24, 0.05, 0.13), vec3(0.86, 0.34, 0.20), vec3(1.00, 0.83, 0.56), t);
    }
    if (palette == 2) {
        col = scale3(vec3(0.02, 0.07, 0.24), vec3(0.05, 0.46, 0.56), vec3(0.76, 0.93, 0.86), t);
    }
    if (palette == 3) {
        col = scale3(vec3(0.04, 0.12, 0.08), vec3(0.22, 0.46, 0.20), vec3(0.86, 0.80, 0.46), t);
    }
    // a slow sheen that travels along the cloth, and a fold of shade under each edge
    float sheen = 0.5 + 0.5 * sin(x * 1.1 - uv.y * 3.0 + b * 2.0);
    float fold = sin(3.14159 * inside);
    col *= 0.80 + 0.12 * fold + 0.10 * sheen;
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
