/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A soft darkening towards the edges, so a picture on a wall ends without a hard border. Made for the painting wall.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Color",
        "Masking"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "reach",
            "LABEL": "Size of the clear middle",
            "TYPE": "float",
            "MIN": 0.2,
            "MAX": 1.5,
            "DEFAULT": 0.75
        },
        {
            "NAME": "soft",
            "LABEL": "Softness of the edge",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.0,
            "DEFAULT": 0.55
        },
        {
            "NAME": "strength",
            "LABEL": "Strength",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.9
        },
        {
            "NAME": "shape",
            "LABEL": "Shape",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Oval",
                "Round",
                "Box"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "centre",
            "LABEL": "Centre",
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
            "NAME": "edge",
            "LABEL": "Colour of the edge",
            "TYPE": "color",
            "DEFAULT": [
                0.0,
                0.0,
                0.0,
                1.0
            ]
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

void main() {
    vec3 src = IMG_THIS_PIXEL(inputImage).rgb;
    vec2 d = (isf_FragNormCoord - centre) * 2.0;
    float far = length(d);
    if (shape == 1) {
        far = length(d * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0));
    } else if (shape == 2) {
        // a box with rounded corners: the larger of the two distances, bent a little towards the corner
        vec2 a = abs(d);
        far = max(max(a.x, a.y), length(a) * 0.82);
    }
    float shade = smoothstep(reach, reach + max(soft, 0.05), far) * clamp(strength, 0.0, 1.0);
    gl_FragColor = vec4(mix(src, edge.rgb, shade), 1.0);
}
