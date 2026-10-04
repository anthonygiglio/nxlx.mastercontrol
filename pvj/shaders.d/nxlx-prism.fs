/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Soft rings of colour turning around the centre.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: one atan, one length, 5 cosines a pixel",
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
            "NAME": "rings",
            "LABEL": "Rings",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 6.0,
            "DEFAULT": 3.0
        },
        {
            "NAME": "petals",
            "LABEL": "Petals",
            "TYPE": "long",
            "VALUES": [
                3,
                4,
                5,
                6,
                8
            ],
            "LABELS": [
                "3",
                "4",
                "5",
                "6",
                "8"
            ],
            "DEFAULT": 5
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

vec3 tone(float t) {
    return vec3(0.5) + vec3(0.5) * cos(6.28318 * (vec3(t) + vec3(0.0, 0.33, 0.67)));
}

void main() {
    vec2 uv = isf_FragNormCoord - vec2(0.5);
    uv.x *= RENDERSIZE.x / RENDERSIZE.y;
    float t = TIME * speed * 0.08;
    float r = length(uv);
    float a = atan(uv.y, uv.x);
    float bend = cos(a * float(petals) + t * 2.0) * 0.08;
    float band = 0.5 + 0.5 * cos((r + bend) * rings * 6.28318 - t * 4.0);
    vec3 col = tone(r * 0.8 - t + bend) * (0.25 + 0.75 * band * band);
    col *= 1.0 - smoothstep(0.55, 1.1, r);
    gl_FragColor = vec4(col, 1.0);
}
