/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A slow kaleidoscope: soft shapes of three colours, mirrored around the centre and turning gently.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: one atan, one square root and 6 sines a pixel",
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
            "NAME": "mirrors",
            "LABEL": "Mirrors",
            "TYPE": "long",
            "VALUES": [
                3,
                4,
                5,
                6,
                8,
                12
            ],
            "LABELS": [
                "3",
                "4",
                "5",
                "6",
                "8",
                "12"
            ],
            "DEFAULT": 6
        },
        {
            "NAME": "zoom",
            "LABEL": "Size of the shapes",
            "TYPE": "float",
            "MIN": 0.5,
            "MAX": 3.0,
            "DEFAULT": 1.2
        },
        {
            "NAME": "spin",
            "LABEL": "Turn slowly",
            "TYPE": "bool",
            "DEFAULT": true
        },
        {
            "NAME": "first",
            "LABEL": "First colour",
            "TYPE": "color",
            "DEFAULT": [
                0.08,
                0.1,
                0.34,
                1.0
            ]
        },
        {
            "NAME": "second",
            "LABEL": "Second colour",
            "TYPE": "color",
            "DEFAULT": [
                0.1,
                0.62,
                0.66,
                1.0
            ]
        },
        {
            "NAME": "third",
            "LABEL": "Third colour",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.8,
                0.55,
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
    vec2 p = (isf_FragNormCoord - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0);
    float r = length(p);
    float a = atan(p.y, p.x + 0.00001);
    if (spin) {
        a += swing(0.006);
    }
    // fold the angle into one wedge and mirror it, as two mirrors do
    float wedge = 6.2831853 / float(mirrors);
    a = abs(mod(a, wedge) - 0.5 * wedge);
    vec2 q = r * vec2(cos(a), sin(a)) * 6.0 / zoom;
    float one = swing(0.021);
    float two = swing(0.013);
    // what lies between the mirrors: three slow waves that slide through each other
    float u = sin(q.x * 1.3 + one) * sin(q.y * 1.7 - two);
    float v = sin(q.x * 0.9 - q.y * 1.1 + two + 1.5 * u);
    vec3 col = mix(first.rgb, second.rgb, smoothstep(-0.7, 0.7, u));
    col = mix(col, third.rgb, 0.85 * smoothstep(0.25, 0.95, v));
    col *= 0.86 + 0.14 * v;
    col *= 1.0 - 0.45 * min(1.0, r * r * 1.5);
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
