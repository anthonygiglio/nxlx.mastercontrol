/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The picture cut into bands that slide against each other, each a little further than the one before, drifting slowly if you let them.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Filter",
        "Geometry",
        "Stylize"
    ],
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "bands",
            "LABEL": "Bands",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 64.0,
            "DEFAULT": 12.0
        },
        {
            "NAME": "shift",
            "LABEL": "Shift",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 0.5,
            "DEFAULT": 0.12
        },
        {
            "NAME": "drift",
            "LABEL": "Drift (picture widths a minute)",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 6.0,
            "DEFAULT": 1.0
        },
        {
            "NAME": "upright",
            "LABEL": "Upright bands",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "alternate",
            "LABEL": "Every other band the other way",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "gap",
            "LABEL": "Dark seam",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 0.3,
            "DEFAULT": 0.06
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
    vec2 uv = isf_FragNormCoord;
    float count = max(floor(bands), 2.0);
    float across = upright ? uv.x : uv.y;
    float band = floor(across * count);
    float inside = fract(across * count);
    // each band's own shift: it grows from band to band and comes back, so no two neighbours agree
    float own = (mod(band * 3.0, 7.0) / 6.0 - 0.5) * 2.0 * shift;
    float way = (alternate && mod(band, 2.0) > 0.5) ? -1.0 : 1.0;
    float move = own + way * fract(TIME * min(drift, 6.0) / 60.0);
    vec2 p = upright ? vec2(uv.x, uv.y + move) : vec2(uv.x + move, uv.y);
    vec3 c = IMG_NORM_PIXEL(inputImage, fold(p)).rgb;
    // a soft dark line where two bands meet
    float seam = smoothstep(0.0, gap * 0.5 + 0.0001, min(inside, 1.0 - inside));
    gl_FragColor = vec4(c * mix(1.0, seam, step(0.0001, gap)), 1.0);
}
