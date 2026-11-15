/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A dusk or dawn sky: a slow glow low on the wall under thin drifting streaks of cloud.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 5 sines and one division a pixel",
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
            "NAME": "glow",
            "LABEL": "Glow height",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 0.6,
            "DEFAULT": 0.3
        },
        {
            "NAME": "streaks",
            "LABEL": "Cloud streaks",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "mood",
            "LABEL": "Hour",
            "TYPE": "long",
            "VALUES": [
                0,
                1,
                2
            ],
            "LABELS": [
                "Dusk",
                "Dawn",
                "Blue hour"
            ],
            "DEFAULT": 0
        },
        {
            "NAME": "zenith",
            "LABEL": "Sky colour",
            "TYPE": "color",
            "DEFAULT": [
                0.04,
                0.06,
                0.24,
                1.0
            ]
        },
        {
            "NAME": "horizon",
            "LABEL": "Horizon colour",
            "TYPE": "color",
            "DEFAULT": [
                1.0,
                0.52,
                0.2,
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
    vec2 uv = isf_FragNormCoord;
    float x = (uv.x - 0.5) * RENDERSIZE.x / RENDERSIZE.y;
    float a = swing(0.004);
    float b = swing(0.011);
    // the colour between the horizon and the top of the sky, and how strong the low glow is
    vec3 mid = vec3(0.62, 0.20, 0.40);
    float power = 1.0;
    if (mood == 1) {
        mid = vec3(0.90, 0.60, 0.56);
        power = 0.8;
    }
    if (mood == 2) {
        mid = vec3(0.14, 0.22, 0.52);
        power = 0.45;
    }
    float h = glow + 0.03 * sin(b);
    vec3 col = mix(horizon.rgb * (0.55 + 0.45 * power), mid, smoothstep(0.0, h, uv.y));
    col = mix(col, zenith.rgb + 0.06 * mid, smoothstep(h * 0.5, 1.0, uv.y));
    // the glow of a sun just under the bottom edge, leaning slowly from side to side
    vec2 d = vec2(x - 0.3 * sin(a), (uv.y + 0.10) * 1.7);
    col += (horizon.rgb * 0.8 + vec3(0.2, 0.15, 0.1)) * power * 0.55 / (1.0 + 7.0 * dot(d, d));
    // thin streaks of cloud around the glow's upper edge, lit from below
    float line = sin(uv.y * 34.0 + 1.6 * sin(x * 1.3 + b) + 0.7 * sin(x * 2.9 - a * 3.0));
    float near = 1.0 - smoothstep(0.0, 0.32, abs(uv.y - h - 0.08));
    float cloud = streaks * near * smoothstep(0.15, 0.95, line);
    col = mix(col, mix(mid, horizon.rgb, 0.45) * 0.8 + 0.08, cloud * 0.45);
    gl_FragColor = vec4(col, 1.0);
}
