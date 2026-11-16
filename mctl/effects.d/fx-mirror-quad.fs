/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The picture folded left to right and top to bottom around a point, so four mirrored copies of one quarter meet there.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Geometry"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "centre",
            "LABEL": "Where the mirrors meet",
            "TYPE": "point2D",
            "MIN": [
                0.0,
                0.0
            ],
            "MAX": [
                1.0,
                1.0
            ],
            "DEFAULT": [
                0.5,
                0.5
            ]
        },
        {
            "NAME": "quarter",
            "LABEL": "Quarter that is kept",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2,
                3
            ],
            "LABELS": [
                "Top left",
                "Top right",
                "Bottom left",
                "Bottom right"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "across",
            "LABEL": "Mirror left to right",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "down",
            "LABEL": "Mirror top to bottom",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "zoom",
            "LABEL": "Zoom",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 4.0,
            "DEFAULT": 1.0
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

vec2 fold(vec2 p) {
    return vec2(1.0) - abs(mod(p, 2.0) - vec2(1.0));
}

void main() {
    vec2 d = isf_FragNormCoord - centre;
    // which side of each mirror is the real one: the kept quarter's
    float sx = (quarter == 1 || quarter == 3) ? 1.0 : -1.0;
    float sy = (quarter >= 2) ? -1.0 : 1.0;
    if (across) {
        d.x = sx * abs(d.x);
    }
    if (down) {
        d.y = sy * abs(d.y);
    }
    gl_FragColor = IMG_NORM_PIXEL(inputImage, fold(centre + d / max(zoom, 1.0)));
}
