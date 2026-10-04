/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Three colours folding slowly into one another, like light through a thin curtain.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 6 sines a pixel",
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
            "MIN": 0.5,
            "MAX": 2.5,
            "DEFAULT": 1.0
        },
        {
            "NAME": "folds",
            "LABEL": "Folds",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 2.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.05,
                0.16,
                0.42,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.72,
                0.22,
                0.46,
                1.0
            ]
        },
        {
            "NAME": "third",
            "LABEL": "Third colour",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.7,
                0.36,
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

void main() {
    vec2 p = isf_FragNormCoord - vec2(0.5);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    p *= scale;
    float a = swing(0.0130);
    float b = swing(0.0091);
    float c = swing(0.0057);
    // two slow waves, each bent by another one that runs across it
    float one = sin(p.x * 2.1 + folds * sin(p.y * 2.7 + a) + b);
    float two = sin(p.y * 1.7 - p.x * 0.8 + folds * sin(p.x * 2.3 - b) + c);
    float three = sin((p.x + p.y) * 1.3 + one * 0.8 - a);
    vec3 col = mix(first.rgb, second.rgb, 0.5 + 0.5 * one);
    col = mix(col, third.rgb, 0.62 * smoothstep(-0.6, 1.0, two) * (0.6 + 0.4 * three));
    // a little depth: the folds are darker where the waves agree
    col *= 0.80 + 0.20 * three * two;
    col += 0.03;
    gl_FragColor = vec4(col, 1.0);
}
