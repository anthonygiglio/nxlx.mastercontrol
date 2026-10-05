/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The outlines of the picture drawn in light of one colour, over the picture dimmed as far as you like.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Stylize"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "glow",
            "LABEL": "Colour of the lines",
            "TYPE": "color",
            "DEFAULT": [
                0.3,
                0.9,
                1.0,
                1.0
            ]
        },
        {
            "NAME": "width",
            "LABEL": "Line width (pixels)",
            "TYPE": "float",
            "MIN": 1.0,
            "MAX": 4.0,
            "DEFAULT": 1.5
        },
        {
            "NAME": "gain",
            "LABEL": "Strength",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 8.0,
            "DEFAULT": 3.0
        },
        {
            "NAME": "keep",
            "LABEL": "How much of the picture stays",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.35
        },
        {
            "NAME": "own",
            "LABEL": "Lines in the picture's own colours",
            "TYPE": "bool",
            "DEFAULT": false
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

float light(vec2 p) {
    return dot(IMG_NORM_PIXEL(inputImage, p).rgb, vec3(0.2126, 0.7152, 0.0722));
}

void main() {
    vec2 uv = isf_FragNormCoord;
    vec2 px = max(width, 1.0) / RENDERSIZE;
    vec3 src = IMG_NORM_PIXEL(inputImage, uv).rgb;
    // how fast the light changes here, from the four neighbours
    float dx = light(uv + vec2(px.x, 0.0)) - light(uv - vec2(px.x, 0.0));
    float dy = light(uv + vec2(0.0, px.y)) - light(uv - vec2(0.0, px.y));
    float edge = clamp(length(vec2(dx, dy)) * gain, 0.0, 1.0);
    vec3 line = own ? clamp(src * 1.6 + 0.1, 0.0, 1.0) : glow.rgb;
    gl_FragColor = vec4(src * clamp(keep, 0.0, 1.0) * (1.0 - edge) + line * edge, 1.0);
}
