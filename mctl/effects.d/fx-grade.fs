/*{
    "ISFVSN": "2",
    "DESCRIPTION": "Colour controls in one place: turn the colours around the wheel, more or less colour, exposure, contrast and warmth. It starts a little richer and warmer than the picture; set everything to the middle for no change.",
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
            "NAME": "hue",
            "LABEL": "Turn the colours (degrees)",
            "TYPE": "float",
            "MIN": -180.0,
            "MAX": 180.0,
            "DEFAULT": 0.0
        },
        {
            "NAME": "saturation",
            "LABEL": "Colour",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 2.0,
            "DEFAULT": 1.35
        },
        {
            "NAME": "exposure",
            "LABEL": "Exposure (stops)",
            "TYPE": "float",
            "MIN": -2.0,
            "MAX": 2.0,
            "DEFAULT": 0.25
        },
        {
            "NAME": "contrast",
            "LABEL": "Contrast",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 2.0,
            "DEFAULT": 1.15
        },
        {
            "NAME": "warmth",
            "LABEL": "Warmth",
            "TYPE": "float",
            "MIN": -1.0,
            "MAX": 1.0,
            "DEFAULT": 0.25
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

void main() {
    vec3 c = IMG_THIS_PIXEL(inputImage).rgb;
    // turn around the grey axis: black, white and greys stay as they are
    float a = hue * 0.0174533;
    vec3 axis = vec3(0.57735027);
    c = c * cos(a) + cross(axis, c) * sin(a) + axis * dot(axis, c) * (1.0 - cos(a));
    float light = dot(c, vec3(0.2126, 0.7152, 0.0722));
    c = vec3(light) + (c - vec3(light)) * saturation;
    c *= pow(2.0, exposure);
    c = (c - vec3(0.5)) * contrast + vec3(0.5);
    c += vec3(0.08, 0.0, -0.08) * warmth;
    gl_FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}
