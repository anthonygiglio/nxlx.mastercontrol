/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The picture washed in one colour of your choice: as a tint, as light of that colour, or with the picture's own light turned into it.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Color"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "wash",
            "LABEL": "Colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.55,
                0.2,
                1.0
            ]
        },
        {
            "NAME": "strength",
            "LABEL": "Strength",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.75
        },
        {
            "NAME": "way",
            "LABEL": "How",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Tint",
                "Light of that colour",
                "Only that colour"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "keep",
            "LABEL": "Keep the whites white",
            "TYPE": "bool",
            "DEFAULT": false
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

void main() {
    vec3 src = IMG_THIS_PIXEL(inputImage).rgb;
    float light = dot(src, vec3(0.2126, 0.7152, 0.0722));
    vec3 c = src * wash.rgb;
    if (way == 1) {
        c = vec3(1.0) - (vec3(1.0) - src) * (vec3(1.0) - wash.rgb * 0.6);
    } else if (way == 2) {
        c = wash.rgb * light;
    }
    float k = clamp(strength, 0.0, 1.0);
    if (keep) {
        k *= 1.0 - smoothstep(0.7, 1.0, light);
    }
    gl_FragColor = vec4(mix(src, c, k), 1.0);
}
