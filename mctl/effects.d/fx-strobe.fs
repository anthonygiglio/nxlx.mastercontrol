/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A strobe: the picture flashes to a colour of your choice, to black, or with that colour added, at a rate you set. At most 3 flashes a second, counted in the filter's TIME (the node's Speed can go above 1 only with its switch \"Allow faster than the flash limit\"). Made to be held down with the Strobe control, which stops the moment you let go.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Strobe"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "rate",
            "LABEL": "Flashes a second",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 3.0,
            "DEFAULT": 2.0
        },
        {
            "NAME": "tint",
            "LABEL": "Colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                1.0,
                1.0,
                1.0
            ]
        },
        {
            "NAME": "flash",
            "LABEL": "How long each flash lasts (milliseconds)",
            "TYPE": "float",
            "MIN": 10.0,
            "MAX": 250.0,
            "DEFAULT": 60.0
        },
        {
            "NAME": "way",
            "LABEL": "Flash to",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "The colour",
                "Black",
                "The colour added to the picture"
            ],
            "DEFAULT": 0
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original filter written for nxlx.mastercontrol: it changes the picture that is playing.

void main() {
    vec3 src = IMG_THIS_PIXEL(inputImage).rgb;
    // The rate is capped here, in the filter's own timing, whatever the slider, the node or a controller says: at most 3
    // flashes a second at the pace of TIME (the node keeps the speed at 1 or below unless its opt-in is on).
    float hz = clamp(rate, 0.0, 3.0);
    // a flash lasts the time asked for, and never more than half of the gap between two flashes
    float on = min(clamp(flash, 10.0, 250.0) * 0.001 * hz, 0.5);
    // the place in the cycle: TIME is folded into it first and its rate is capped here too; a flash starts at the start of
    // each cycle, and no rate (0) is no flash at all
    float cycle = fract(TIME * min(rate, 3.0));
    float lit = (hz > 0.01 && cycle < on) ? 1.0 : 0.0;
    vec3 c = src;
    if (way == 1) {
        c = vec3(0.0);
    } else if (way == 2) {
        c = min(src + tint.rgb, vec3(1.0));
    } else {
        c = tint.rgb;
    }
    gl_FragColor = vec4(mix(src, c, lit), 1.0);
}
