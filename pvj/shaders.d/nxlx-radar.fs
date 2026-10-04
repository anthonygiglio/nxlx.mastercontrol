/*{
    "ISFVSN": "2",
    "DESCRIPTION": "A sweeping arm of light with a fading trail, range rings, and blips that light as the arm passes over them.",
    "CREDIT": "NXLX.Systems and contributors",
    "CATEGORIES": [
        "Generator",
        "Performance"
    ],
    "COST": "low: one atan, one square root, one sine and about 70 sums and products a pixel",
    "INPUTS": [
        {
            "NAME": "rate",
            "LABEL": "Turns a second",
            "TYPE": "float",
            "MIN": 0.05,
            "MAX": 1.5,
            "DEFAULT": 0.4
        },
        {
            "NAME": "rings",
            "LABEL": "Range rings",
            "TYPE": "float",
            "MIN": 2.0,
            "MAX": 10.0,
            "DEFAULT": 5.0
        },
        {
            "NAME": "trail",
            "LABEL": "Trail length",
            "TYPE": "float",
            "MIN": 0.1,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "blips",
            "LABEL": "Blips",
            "TYPE": "float",
            "MIN": 0.0,
            "MAX": 1.0,
            "DEFAULT": 0.5
        },
        {
            "NAME": "reverse",
            "LABEL": "Turn the other way",
            "TYPE": "bool",
            "DEFAULT": false
        },
        {
            "NAME": "arm",
            "LABEL": "Arm colour",
            "TYPE": "color",
            "DEFAULT": [
                0.3,
                1.0,
                0.5,
                1.0
            ]
        },
        {
            "NAME": "screen",
            "LABEL": "Screen colour",
            "TYPE": "color",
            "DEFAULT": [
                0.02,
                0.16,
                0.12,
                1.0
            ]
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
        }
    ]
}*/
// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// An original shader written for nxlx.mastercontrol.

// A scrambled number from 0 to 1 for two small whole numbers. Every step is a whole number below 16 million, so a
// 32-bit float holds it exactly: multiply, fold back under the prime 4093, square, fold, and once more.
float scramble(float a, float b) {
    float n = mod(a * 157.0 + b * 311.0 + 71.0, 4093.0);
    n = mod(n * n + b * 1009.0 + 37.0, 4093.0);
    n = mod(n * 229.0 + a * 53.0 + 11.0, 4093.0);
    return n / 4093.0;
}

void main() {
    vec2 p = isf_FragNormCoord - clamp(centre, 0.0, 1.0);
    p.x *= RENDERSIZE.x / RENDERSIZE.y;
    float r = length(p);
    float around = atan(p.y, p.x + 0.00001) / 6.2831853 + 0.5;
    if (reverse) {
        around = 1.0 - around;
    }
    // where the arm stands: whole turns, folded to 0..1 so it stays exact for hours
    float turn = fract(TIME * rate);
    // how long ago the arm was here, in turns: 0 just now, close to 1 a moment before it returns
    float ago = fract(turn - around);
    float decay = 1.0 - smoothstep(0.0, trail, ago);
    float sweep = decay * decay;
    float head = 1.0 - smoothstep(0.0, 0.006 + 0.002 / max(r, 0.02), min(ago, 1.0 - ago));
    // range rings and four cross lines, drawn dim on the screen
    float count = floor(rings + 0.5);
    float ringline = abs(fract(r * count * 1.4) - 0.5);
    float rule = 1.0 - smoothstep(0.0, 0.06, 0.5 - ringline);
    float cross1 = 1.0 - smoothstep(0.0, 0.004, min(abs(p.x), abs(p.y)));
    // blips: the picture cut into ring pieces, some of them hold one; it lights when the arm passes and fades
    float band = floor(r * count * 1.4);
    float pieces = 6.0 + 4.0 * band;
    float piece = min(floor(around * pieces), pieces - 1.0);
    float h = scramble(band * 64.0 + piece, 9.0);
    vec2 f = vec2(fract(around * pieces) - 0.5, fract(r * count * 1.4) - 0.5);
    float spot = (1.0 - smoothstep(0.0, 0.30, length(f))) * step(1.0 - 0.45 * blips, h);
    float when = fract(turn - (piece + 0.5) / pieces);
    float echo = 1.0 - smoothstep(0.0, 0.25 + 0.6 * trail, when);
    vec3 col = screen.rgb * (0.85 + 0.35 / (1.0 + 6.0 * r * r)) + arm.rgb * 0.05;
    col += arm.rgb * (0.16 * rule + 0.10 * cross1);
    col += arm.rgb * sweep * 0.42 * (1.0 - 0.3 * min(1.0, r));
    col += mix(arm.rgb, vec3(1.0), 0.4) * head * 0.8;
    col += mix(arm.rgb, vec3(1.0), 0.25) * spot * echo * echo * 0.9;
    gl_FragColor = vec4(col / (1.0 + 0.15 * col), 1.0);
}
