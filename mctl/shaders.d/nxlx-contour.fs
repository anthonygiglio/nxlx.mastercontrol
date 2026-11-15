/*{
    "ISFVSN": "2",
    "DESCRIPTION": "The contour lines of a map whose hills rise and sink very slowly.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Ambient"
    ],
    "COST": "low: 4 sines a pixel",
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
            "NAME": "levels",
            "LABEL": "Contour lines",
            "TYPE": "float",
            "MIN": 3.0,
            "MAX": 16.0,
            "DEFAULT": 8.0
        },
        {
            "NAME": "scale",
            "LABEL": "Scale",
            "TYPE": "float",
            "MIN": 0.6,
            "MAX": 2.5,
            "DEFAULT": 1.0
        },
        {
            "NAME": "ink",
            "LABEL": "Line strength",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.55
        },
        {
            "NAME": "steps",
            "LABEL": "Shade each level flat",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "valley",
            "LABEL": "Valley colour",
            "TYPE": "color",
            "DEFAULT": [
                0.04,
                0.2,
                0.3,
                1.0
            ]
        },
        {
            "NAME": "peak",
            "LABEL": "Peak colour",
            "TYPE": "color",
            "DEFAULT": [
                0.96,
                0.8,
                0.55,
                1.0
            ]
        },
        {
            "NAME": "line",
            "LABEL": "Line colour",
            "TYPE": "color",
            "DEFAULT": [
                0.98,
                0.95,
                0.85,
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
    vec2 p = (isf_FragNormCoord - vec2(0.5)) * vec2(RENDERSIZE.x / RENDERSIZE.y, 1.0) * scale;
    float a = swing(0.0090);
    float b = swing(0.0061);
    float c = swing(0.0047);
    // the land: a few broad waves that slide through each other, brought to 0..1
    float land = sin(p.x * 2.3 + a) * sin(p.y * 2.9 - b)
        + 0.6 * sin(p.x * 1.4 - p.y * 2.1 + c)
        + 0.35 * sin(p.x * 4.6 + p.y * 3.7 - a);
    float h = clamp(0.5 + land * 0.27, 0.0, 1.0);
    float count = floor(levels + 0.5);
    float v = h * count;
    float level = floor(v);
    float inside = v - level;
    float shade = steps ? (level + 0.5) / count : h;
    vec3 col = mix(valley.rgb, peak.rgb, shade);
    // a line where one level ends and the next begins
    float mark = 1.0 - smoothstep(0.0, 0.10, min(inside, 1.0 - inside));
    col = mix(col, line.rgb, mark * ink * 0.75);
    col *= 0.90 + 0.10 * inside;
    col += 0.02;
    gl_FragColor = vec4(col, 1.0);
}
